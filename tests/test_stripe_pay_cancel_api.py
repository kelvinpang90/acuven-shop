"""现有模拟支付与取消接口接入 Stripe 适配器：POST /api/pay/attempts、POST /api/pay/cancel、
POST /api/member/pay/attempts 与 POST /api/member/pay/cancel（app/api/pay.py、
app/api/member_pay.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 2 条（下称「第 2 条」）：“订单只要有一条不是 expired 的 Stripe 记录（creating、open 或
  complete），不论开关当前取值，都不接受模拟支付”。
- 第 5 条（下称「第 5 条」）：“有 Stripe 记录的订单，取消一律先处理会话”；“会话为 open 时请求
  Stripe 作废：作废成功或会话已过期才按现有规则取消”；“Stripe 答复会话已完成，按会话的付款
  状态处理”；“Stripe 不可用时本人取消提示稍后再试”。
- 第 9 条（下称「第 9 条」）：“CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe”。
以及 docs/HANDOFF.md 0.54 的落实写法：“Stripe 不可用时接口回 503 `stripe_unavailable`，模拟支付
遇到 Stripe 途径回 409 `method_changed`”。每条测试（参数化的测试是每个用例）的文档字符串写明它
守住的那一句；只出自 SHOP-TASK-118 验收标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表，引擎与接口的
会话依赖照 tests/test_demo_payment.py 与 tests/test_member_pay_api.py；订单与 Stripe 会话记录
照 tests/test_stripe_cancel.py 的写法在本文件里直接按模型写入，库存按已为订单预留 2 件写入。
游客的访问 cookie 经 issue_order_access 签发，会员会话经 issue_member_session 签发。Stripe 一律
以 app.state.stripe_gateway 注入 FakeStripeGateway（第 9 条）。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductVariant,
    StripeCheckout,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_MEMBER,
    ACTOR_SYSTEM,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
)
from app.services import member_auth, order_access, payment
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeError,
    StripeOutcome,
    StripeResult,
)

GUEST_PAY_URL = "/api/pay/attempts"
GUEST_CANCEL_URL = "/api/pay/cancel"
MEMBER_PAY_URL = "/api/member/pay/attempts"
MEMBER_CANCEL_URL = "/api/member/pay/cancel"
MEMBER_COOKIE = "__Host-shop_member_session"
SESSIONS_PATH = "/v1/checkout/sessions"

NO_STORE = "no-store"
WHO = ["guest", "member"]

# 订单一行 2 件、单价 3000 仙，运费 800 仙；规格每日初始库存 10，已为订单预留 2 件。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY
FINGERPRINT = "0f" * 32

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)
# HANDOFF 0.53 的核验结论：再作废已过期或已完成的会话都 400。
EXPIRE_REJECTED = StripeResult(
    StripeOutcome.REJECTED, error=StripeError(http_status=400, type="invalid_request_error")
)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env：模拟支付的途径判定取默认配置。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@dataclass(frozen=True)
class _Placed:
    order_number: str
    order_id: int
    variant_id: int
    # 本人操作时的请求头（访问 cookie 或会员会话 cookie，加 CSRF 令牌）。
    headers: dict[str, str]
    pay_url: str
    cancel_url: str
    actor: str


@dataclass(frozen=True)
class _Record:
    id: int
    session_id: str | None
    create_key: str
    order_id: int
    total_sen: int


def _variant(db: Session) -> int:
    n = next(_SEQUENCE)
    category = _add(db, Category(slug=f"goods-{n}", name_en="Goods", is_active=True))
    product = Product(
        category_id=category.id,
        slug=f"tee-{n}",
        name_en="Tee",
        description_en="A tee.",
        is_active=True,
    )
    _add(db, product)
    row = ProductVariant(
        product_id=product.id,
        sku=f"TEE-{n}",
        price_sen=UNIT_PRICE,
        daily_initial_stock=INITIAL_STOCK,
        available_stock=RESERVED_STOCK,
        is_active=True,
    )
    return _add(db, row).id


def _order_row(db: Session, variant_id: int, member_id: int | None) -> Order:
    """1 分钟前下单、未到支付到期时间的待支付订单（应付 6800 仙，没有券与积分）。"""
    n = next(_SEQUENCE)
    placed_at = _now() - timedelta(minutes=1)
    order = Order(
        order_number=f"{n:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=placed_at,
        payment_expires_at=placed_at + timedelta(minutes=15),
        member_id=member_id,
        claim_status=CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE,
    )
    _add(db, order)
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=variant_id,
        sku="TEE",
        product_name_en="Tee",
        product_name_zh="Tee",
        product_name_ms="Tee",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=UNIT_PRICE,
        quantity=QUANTITY,
        line_subtotal_sen=SUBTOTAL,
    )
    _add(db, item)
    return order


def _guest(db: Session) -> _Placed:
    """游客订单与该单的 guest_checkout 授权；写完即提交。"""
    variant_id = _variant(db)
    order = _order_row(db, variant_id, None)
    db.commit()
    issued = order_access.issue_order_access(db, None, order.id, SCOPE_GUEST_CHECKOUT, _now())
    db.commit()
    assert issued.new_token is not None
    headers = {
        "Cookie": f"{order_access.COOKIE_NAME}={issued.new_token}",
        "X-CSRF-Token": issued.csrf_token,
    }
    return _Placed(
        order.order_number,
        order.id,
        variant_id,
        headers,
        GUEST_PAY_URL,
        GUEST_CANCEL_URL,
        ACTOR_GUEST,
    )


def _member(db: Session) -> _Placed:
    """会员下的订单与该会员的登录会话；写完即提交。"""
    phone = f"+60123{next(_SEQUENCE):06d}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=_now()))
    variant_id = _variant(db)
    order = _order_row(db, variant_id, member.id)
    db.commit()
    issued = member_auth.issue_member_session(db, member, _now())
    db.commit()
    csrf = member_auth.csrf_token_for_cookie(issued.token)
    assert csrf is not None
    headers = {"Cookie": f"{MEMBER_COOKIE}={issued.token}", "X-CSRF-Token": csrf}
    return _Placed(
        order.order_number,
        order.id,
        variant_id,
        headers,
        MEMBER_PAY_URL,
        MEMBER_CANCEL_URL,
        ACTOR_MEMBER,
    )


def _place(db: Session, who: str) -> _Placed:
    return _guest(db) if who == "guest" else _member(db)


def _checkout(db: Session, placed: _Placed, status: str = CHECKOUT_OPEN) -> _Record:
    """直接写一条合法的会话记录（模拟之前的发起留下的），提交后返回它的对账字段。"""
    n = next(_SEQUENCE)
    total = db.scalars(select(Order.total_sen).where(Order.id == placed.order_id)).one()
    now = _now()
    fields: dict[str, Any] = {
        "order_id": placed.order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": total,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
        "expires_at": now + timedelta(minutes=31),
        "created_at": now - timedelta(seconds=30),
        "active_order_id": None if status == CHECKOUT_EXPIRED else placed.order_id,
    }
    session_id = None
    if status != CHECKOUT_CREATING:
        session_id = f"cs_test_{n}"
        fields["checkout_session_id"] = session_id
        fields["payment_status"] = PAYMENT_UNPAID
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status == CHECKOUT_COMPLETE:
        fields["completed_at"] = now - timedelta(seconds=10)
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return _Record(record.id, session_id, record.idempotency_key, placed.order_id, total)


def _session(record: _Record, status: str, *, paid: bool = False) -> CheckoutSession:
    """与记录、订单相符的会话对象；已付款时 payment_intent 已展开、方式为 card。"""
    intent = None
    if paid:
        intent = PaymentIntentInfo(
            id=f"pi_test_{record.id}",
            status="succeeded",
            livemode=False,
            payment_method_type="card",
        )
    return CheckoutSession(
        id=record.session_id or f"cs_test_for_{record.id}",
        status=status,
        payment_status=PAYMENT_PAID if paid else PAYMENT_UNPAID,
        livemode=False,
        currency="myr",
        amount_total=record.total_sen,
        client_reference_id=str(record.order_id),
        order_id=str(record.order_id),
        create_key=record.create_key,
        payment_intent=intent,
    )


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _inject(app: FastAPI, *results: StripeResult) -> FakeStripeGateway:
    gateway = FakeStripeGateway(*results)
    app.state.stripe_gateway = gateway
    return gateway


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _cancel(client: TestClient, placed: _Placed) -> Any:
    body = {"order_number": placed.order_number}
    return client.post(placed.cancel_url, json=body, headers=placed.headers)


def _pay(client: TestClient, placed: _Placed, result: str) -> Any:
    body = {"order_number": placed.order_number, "method": "card", "result": result}
    headers = placed.headers | {"Idempotency-Key": f"pay-key-{next(_SEQUENCE):012d}"}
    return client.post(placed.pay_url, json=body, headers=headers)


def _status(db: Session, placed: _Placed) -> str:
    db.expire_all()
    return db.scalars(select(Order.status).where(Order.id == placed.order_id)).one()


def _stock(db: Session, placed: _Placed) -> int:
    db.expire_all()
    stmt = select(ProductVariant.available_stock).where(ProductVariant.id == placed.variant_id)
    return db.scalars(stmt).one()


def _events(db: Session, placed: _Placed) -> list[tuple[str | None, str, str]]:
    db.expire_all()
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == placed.order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _attempt_count(db: Session, placed: _Placed) -> int:
    db.expire_all()
    stmt = select(PaymentAttempt.id).where(PaymentAttempt.order_id == placed.order_id)
    return len(list(db.scalars(stmt)))


def _record_status(db: Session, record: _Record) -> tuple[str, int | None]:
    db.expire_all()
    stmt = select(StripeCheckout.status, StripeCheckout.active_order_id).where(
        StripeCheckout.id == record.id
    )
    return db.execute(stmt).tuples().one()


def _assert_kept(db: Session, placed: _Placed) -> None:
    """订单仍待支付：没有事件与支付尝试，库存仍预留。"""
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _events(db, placed) == []
    assert _attempt_count(db, placed) == 0
    assert _stock(db, placed) == RESERVED_STOCK


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("who", WHO)
def test_open_session_expired_then_cancelled(
    app: FastAPI, db: Session, client: TestClient, who: str
) -> None:
    """第 5 条「会话为 open 时请求 Stripe 作废：作废成功……才按现有规则取消」；验收：取消接口经
    get_stripe_gateway 取得适配器并传给 cancel_order——作废成功后取消 200，响应同现有取消
    （本人取消），记录为 expired，库存释放，事件操作者为本人。"""
    placed = _place(db, who)
    record = _checkout(db, placed)
    gateway = _inject(app, _ok(_session(record, "expired")))

    response = _cancel(client, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert response.headers["cache-control"] == NO_STORE
    assert _paths(gateway) == [("POST", f"{SESSIONS_PATH}/{record.session_id}/expire")]
    assert _record_status(db, record) == (CHECKOUT_EXPIRED, None)
    assert _stock(db, placed) == INITIAL_STOCK
    assert _events(db, placed) == [(STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, placed.actor)]


@pytest.mark.parametrize("who", WHO)
def test_stripe_unavailable_is_503(
    app: FastAPI,
    db: Session,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    who: str,
) -> None:
    """第 5 条「Stripe 不可用时本人取消提示稍后再试」；HANDOFF 0.54「Stripe 不可用时接口回 503
    `stripe_unavailable`」；验收：订单仍待支付、库存仍预留、记录仍为 open，响应带 no-store，
    接口不写日志。"""
    placed = _place(db, who)
    record = _checkout(db, placed)
    gateway = _inject(app, UNAVAILABLE)
    caplog.set_level(logging.DEBUG)

    response = _cancel(client, placed)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "stripe_unavailable"}
    assert response.headers["cache-control"] == NO_STORE
    assert len(gateway.calls) == 1
    _assert_kept(db, placed)
    assert _record_status(db, record) == (CHECKOUT_OPEN, placed.order_id)
    assert [r for r in caplog.records if r.name.startswith("app.api")] == []


@pytest.mark.parametrize("who", WHO)
def test_paid_session_is_order_not_cancellable(
    app: FastAPI, db: Session, client: TestClient, who: str
) -> None:
    """第 5 条「Stripe 答复会话已完成，按会话的付款状态处理」「会话 complete 且已付款，按第 4 条
    确认、不取消」；验收：作废被拒、查询为 complete 且已付款时订单变为 demo_paid，接口按现有
    映射回 409 order_not_cancellable（status 为 demo_paid），库存不加回。"""
    placed = _place(db, who)
    record = _checkout(db, placed)
    _inject(app, EXPIRE_REJECTED, _ok(_session(record, "complete", paid=True)))

    response = _cancel(client, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_cancellable", "status": STATUS_PAID}
    assert response.headers["cache-control"] == NO_STORE
    assert _status(db, placed) == STATUS_PAID
    assert _stock(db, placed) == RESERVED_STOCK
    assert _events(db, placed) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]


@pytest.mark.parametrize("who", WHO)
def test_cancel_without_record_does_not_call_stripe(
    app: FastAPI, db: Session, client: TestClient, who: str
) -> None:
    """第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有模拟支付流程不变」与
    第 5 条「有 Stripe 记录的订单，取消一律先处理会话」（反面：没有记录不处理会话）；验收：没有
    Stripe 记录的订单取消照现有流程成功、库存释放，不调用适配器。"""
    placed = _place(db, who)
    gateway = _inject(app)

    response = _cancel(client, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert gateway.calls == []
    assert _stock(db, placed) == INITIAL_STOCK


# ---------------------------------------------------------------------------
# 模拟支付
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_status", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
@pytest.mark.parametrize("result", ["success", "failure"])
@pytest.mark.parametrize("who", WHO)
def test_active_record_rejects_simulated_payment(
    app: FastAPI,
    db: Session,
    client: TestClient,
    who: str,
    result: str,
    record_status: str,
) -> None:
    """第 2 条「订单只要有一条不是 expired 的 Stripe 记录（creating、open 或 complete），不论开关
    当前取值，都不接受模拟支付」；HANDOFF 0.54「模拟支付遇到 Stripe 途径回 409
    `method_changed`」；验收：不写支付尝试，订单与库存不变，响应带 no-store；模拟支付不传
    适配器（不调用 Stripe）。"""
    placed = _place(db, who)
    record = _checkout(db, placed, record_status)
    gateway = _inject(app)

    response = _pay(client, placed, result)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "method_changed"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    _assert_kept(db, placed)
    assert _record_status(db, record) == (record_status, placed.order_id)

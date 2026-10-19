"""会员支付页的 Stripe 发起与确认接口：POST /api/member/pay/stripe/start、
POST /api/member/pay/stripe/confirm（app/api/member_pay.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 2 条（下称「第 2 条」）：“服务端在访客发起支付时按当时的值判定”；“订单只要有一条不是
  expired 的 Stripe 记录……不另建会话”。
- 第 3 条（下称「第 3 条」）：“访客在支付页按现有授权（游客短期凭据或会员登录会话）与 CSRF 校验
  后发起”；“24 小时内仍无法确定就不再自动处理、告警由人工核对”。
- 第 4 条（下称「第 4 条」）：“访客从 Stripe 返回后前台调用本站接口，服务端按授权找到该单的会话
  向 Stripe 查询一次”；“返回地址本身不作依据”。
- 第 9 条（下称「第 9 条」）：“创建会话或查询时 Stripe 不可用，支付页提示稍后再试……不自动改用
  模拟支付。CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe”。
- docs/DESIGN.md 1.11「权限与资料保护」第 6 条（下称「权限第 6 条」）：“会员仅能访问自己认领或
  下单的订单”；“支付、取消……等写操作另须 CSRF 令牌”；“不缓存敏感响应”。
docs/UX.md 0.11 的 P06（下称「P06」）与 P07（下称「P07」）的会员访问（会员凭登录会话访问支付页与
结果页），以及 docs/HANDOFF.md 0.54 的落实写法（下称「HANDOFF 0.54」）。每条测试（参数化的测试是
每个用例）的文档字符串写明它守住的那一句；只出自 SHOP-TASK-121 验收标准的约定注明「验收：」。

引擎、内存库与接口的会话依赖照 tests/test_member_pay_api.py（SQLite 内存库、StaticPool、每个连接
打开外键检查并断言已打开）；会员经 issue_member_session 签发会话，cookie 与 CSRF 令牌放进请求头；
游客订单的访问 cookie 经 issue_order_access 签发。订单、Stripe 会话记录与退款尝试在本文件里直接按
模型写入，库存按已为订单预留 2 件写入。应用的 Settings 带齐 Stripe 测试配置；Stripe 一律以
app.state.stripe_gateway 注入 FakeStripeGateway（第 9 条）。
"""

from __future__ import annotations

import itertools
import json
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
    OrderItem,
    Product,
    ProductVariant,
    SiteSetting,
    StripeCheckout,
    StripeRefund,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.models.site import PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_PENDING,
)
from app.services import member_auth, order_access, payment
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeOutcome,
    StripeResult,
)

START_URL = "/api/member/pay/stripe/start"
CONFIRM_URL = "/api/member/pay/stripe/confirm"
URLS = [START_URL, CONFIRM_URL]
GUEST_START_URL = "/api/pay/stripe/start"
GUEST_CONFIRM_URL = "/api/pay/stripe/confirm"
SESSIONS_PATH = "/v1/checkout/sessions"
MAX_BODY_BYTES = 8 * 1024

MEMBER_COOKIE = "__Host-shop_member_session"
ACCESS_COOKIE = order_access.COOKIE_NAME

NO_STORE = "no-store"
SESSION_REQUIRED = "member_session_required"
CSRF_FAILED = "csrf_failed"
NOT_FOUND = "order_not_found"

# 不合格式与格式合法但不存在的订单号（下面建的订单号都是数字）。
INVALID_NUMBER = "not-an-order"
UNKNOWN_NUMBER = "Z" * 16

# 订单一行 2 件、单价 3000 仙，运费 800 仙；规格每日初始库存 10，已为订单预留 2 件。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY
FINGERPRINT = "0f" * 32

# 新建会话的付款页地址：不含会话 ID，便于断言响应里没有其他 Stripe ID。
NEW_CHECKOUT_URL = "https://checkout.stripe.example.com/c/pay/new-session-page"
NEW_SESSION_ID = "cs_test_created_by_member_start"
NEW_INTENT_ID = "pi_test_created_by_member_start"

CONFIGURED = Settings(
    _env_file=None,
    stripe_api_base_url="https://api.stripe.example.com",
    stripe_secret_key="sk_test_placeholder",
    stripe_webhook_secret="whsec_placeholder",
    stripe_demo_email="demo@example.com",
    public_site_url="https://shop.example.com",
)

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

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
    app = create_app(CONFIGURED)

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
    # 不读本地 .env：业务规则层自己取配置之处一律用默认配置。
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


@dataclass(frozen=True)
class _Record:
    id: int
    session_id: str | None
    create_key: str
    order_id: int


def _stripe_switch(db: Session) -> None:
    """站点设置的支付方式为「Stripe 测试」。"""
    row = SiteSetting(
        id=1, sms_verification_enabled=False, payment_method=PAYMENT_METHOD_STRIPE_TEST
    )
    row.updated_at = _now()
    db.add(row)
    db.commit()


def _member(db: Session) -> int:
    """一个 active 会员；写完即提交。"""
    phone = f"+60123{next(_SEQUENCE):06d}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=_now()))
    db.commit()
    return member.id


def _login(db: Session, member_id: int) -> dict[str, str]:
    """签发会员会话并提交；返回带会话 cookie 与 CSRF 令牌的请求头。"""
    member = db.get(Member, member_id)
    assert member is not None
    issued = member_auth.issue_member_session(db, member, _now())
    db.commit()
    csrf = member_auth.csrf_token_for_cookie(issued.token)
    assert csrf is not None
    return {"Cookie": f"{MEMBER_COOKIE}={issued.token}", "X-CSRF-Token": csrf}


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


def _order(
    db: Session,
    member_id: int | None,
    status: str = STATUS_AWAITING_PAYMENT,
    *,
    claim_status: str | None = None,
) -> _Placed:
    """1 分钟前下单、未到支付到期时间的订单（应付 6800 仙）；member_id 为 None 时是游客订单。
    写完即提交。"""
    n = next(_SEQUENCE)
    if claim_status is None:
        claim_status = CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE
    variant_id = _variant(db)
    placed_at = _now() - timedelta(minutes=1)
    order = Order(
        order_number=f"{n:016d}",
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=TOTAL,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=placed_at,
        payment_expires_at=placed_at + timedelta(minutes=15),
        member_id=member_id,
        claim_status=claim_status,
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
    db.commit()
    return _Placed(order.order_number, order.id, variant_id)


def _guest_access(db: Session, placed: _Placed) -> dict[str, str]:
    """签发该单的 guest_checkout 授权并提交；返回带访问 cookie 与 CSRF 令牌的请求头。"""
    issued = order_access.issue_order_access(
        db, None, placed.order_id, SCOPE_GUEST_CHECKOUT, _now()
    )
    db.commit()
    assert issued.new_token is not None
    return {"Cookie": f"{ACCESS_COOKIE}={issued.new_token}", "X-CSRF-Token": issued.csrf_token}


def _checkout(
    db: Session,
    placed: _Placed,
    status: str = CHECKOUT_OPEN,
    *,
    alerted_at: datetime | None = None,
) -> _Record:
    """直接写一条合法的会话记录（模拟之前的发起留下的），提交后返回它的对账字段。"""
    n = next(_SEQUENCE)
    now = _now()
    fields: dict[str, Any] = {
        "order_id": placed.order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
        "expires_at": now + timedelta(minutes=31),
        "created_at": now - timedelta(seconds=30),
        "alerted_at": alerted_at,
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
    return _Record(record.id, session_id, record.idempotency_key, placed.order_id)


def _refund(db: Session, record: _Record) -> None:
    """直接写一条来源为该会话的 pending 退款尝试（模拟之前的迟到付款处理留下的）。"""
    db.add(
        StripeRefund(
            payment_intent_id=f"pi_test_{record.id}",
            source_type=SOURCE_CHECKOUT,
            checkout_id=record.id,
            amount_sen=TOTAL,
            status=STRIPE_REFUND_PENDING,
            call_count=0,
            created_at=_now(),
            active_checkout_id=record.id,
        )
    )
    db.commit()


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
        amount_total=TOTAL,
        client_reference_id=str(record.order_id),
        order_id=str(record.order_id),
        create_key=record.create_key,
        payment_intent=intent,
    )


def _created_session() -> CheckoutSession:
    """Stripe 对创建请求返回的 open 会话（带付款页地址）。"""
    return CheckoutSession(
        id=NEW_SESSION_ID,
        status=CHECKOUT_OPEN,
        payment_status=PAYMENT_UNPAID,
        livemode=False,
        currency="myr",
        amount_total=TOTAL,
        url=NEW_CHECKOUT_URL,
        payment_intent=PaymentIntentInfo(id=NEW_INTENT_ID),
    )


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _inject(app: FastAPI, *results: StripeResult) -> FakeStripeGateway:
    gateway = FakeStripeGateway(*results)
    app.state.stripe_gateway = gateway
    return gateway


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _post(
    client: TestClient,
    url: str,
    headers: dict[str, str],
    number: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    payload = {"order_number": number} if body is None else body
    return client.post(url, json=payload, headers=headers, params=params)


def _assert_error(response: Any, status: int, detail: str) -> None:
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []


def _records(db: Session, placed: _Placed) -> list[StripeCheckout]:
    db.rollback()
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.order_id == placed.order_id)
        .order_by(StripeCheckout.id)
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(stmt))


def _status(db: Session, placed: _Placed) -> str:
    db.rollback()
    stmt = select(Order.status).where(Order.id == placed.order_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _stock(db: Session, placed: _Placed) -> int:
    db.rollback()
    stmt = select(ProductVariant.available_stock).where(ProductVariant.id == placed.variant_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _assert_untouched(db: Session, placed: _Placed, gateway: FakeStripeGateway) -> None:
    """没有调用 Stripe、没有写会话记录，订单仍待支付、库存仍预留。"""
    assert gateway.calls == []
    assert _records(db, placed) == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _stock(db, placed) == RESERVED_STOCK


def _api_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.api")]


# ---------------------------------------------------------------------------
# 发起：POST /api/member/pay/stripe/start
# ---------------------------------------------------------------------------


def test_start_creates_session_and_returns_only_the_checkout_url(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 3 条「访客在支付页按现有授权（游客短期凭据或会员登录会话）与 CSRF 校验后发起」；
    P06 会员凭登录会话访问支付页，「点 `[pay.stripe_go]` 由服务端……创建会话并返回付款页地址」；
    验收：
    200 {"result":"redirect","checkout_url":付款页地址}，响应里除付款页地址外不带任何 Stripe ID，
    locale 随 lang，带 no-store。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    gateway = _inject(app, _ok(_created_session()))

    response = _post(client, START_URL, headers, placed.order_number, params={"lang": "zh"})

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "redirect", "checkout_url": NEW_CHECKOUT_URL}
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    assert dict(gateway.calls[0].form)["locale"] == "zh"
    [record] = _records(db, placed)
    assert record.status == CHECKOUT_OPEN
    assert record.checkout_session_id == NEW_SESSION_ID
    for stripe_id in (NEW_SESSION_ID, NEW_INTENT_ID, record.idempotency_key):
        assert stripe_id not in response.text


def test_start_on_claimed_guest_order(app: FastAPI, db: Session, client: TestClient) -> None:
    """权限第 6 条「会员仅能访问自己认领或下单的订单」：会员认领的游客订单也可凭会员会话发起。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id, claim_status=CLAIM_CLAIMED)
    headers = _login(db, member_id)
    _inject(app, _ok(_created_session()))

    response = _post(client, START_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "redirect", "checkout_url": NEW_CHECKOUT_URL}


def test_start_with_open_session_returns_the_same_url(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P06「有进行中的会话……`[pay.stripe_continue]` 打开同一会话，不另建会话」；第 2 条「订单只要有
    一条不是 expired 的 Stripe 记录……不另建会话」：返回原付款页地址，不调用 Stripe、不写新记录。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    _checkout(db, placed, CHECKOUT_OPEN)
    [before] = _records(db, placed)
    url = before.checkout_url
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, START_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "redirect", "checkout_url": url}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert [r.id for r in _records(db, placed)] == [before.id]


def test_start_with_complete_session_is_complete(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """HANDOFF 0.54「P06 打开时已有 complete 会话，由前端调用确认接口完成『服务端先确认』」；验收：
    与游客接口相同，complete 回 200 {"result":"complete","checkout_url":null}，不调用 Stripe。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    _checkout(db, placed, CHECKOUT_COMPLETE)
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, START_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "complete", "checkout_url": None}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []


def test_start_on_simulated_route_is_method_changed(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 2 条「服务端在访客发起支付时按当时的值判定」；P06「点 Stripe 跳转时已改为模拟……页面显示
    `[pay.method_changed]`」：开关为模拟（没有设置行即默认模拟）时 409 method_changed、不带
    status，不写记录、不调用 Stripe。"""
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, START_URL, headers, placed.order_number)

    _assert_error(response, 409, "method_changed")
    _assert_untouched(db, placed, gateway)


def test_start_on_order_not_awaiting_payment_is_order_not_payable(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 4 条「只看订单是否仍待支付」；验收：与游客接口相同，409 order_not_payable 带订单当前
    status，不写记录、不调用 Stripe。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id, STATUS_CANCELLED)
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, START_URL, headers, placed.order_number)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_payable", "status": STATUS_CANCELLED}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _records(db, placed) == []


def test_start_under_review_is_stripe_under_review(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对，订单在此期间不取消、库存继续
    预留」；P06「转人工核对时……改显示 `[pay.stripe_under_review]`」；HANDOFF 0.54「发起时转人工回
    409 `stripe_under_review`」：不调用 Stripe，订单与库存不变。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    _checkout(db, placed, CHECKOUT_CREATING, alerted_at=_now())
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, START_URL, headers, placed.order_number)

    _assert_error(response, 409, "stripe_under_review")
    assert gateway.calls == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _stock(db, placed) == RESERVED_STOCK


def test_start_when_stripe_unavailable_is_503(
    app: FastAPI, db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """第 9 条「创建会话或查询时 Stripe 不可用，支付页提示稍后再试，订单保持待支付……不自动改用
    模拟支付」；HANDOFF 0.54「Stripe 不可用时接口回 503 `stripe_unavailable`」；验收：接口不写
    日志。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    gateway = _inject(app, UNAVAILABLE)
    caplog.set_level(logging.DEBUG)

    response = _post(client, START_URL, headers, placed.order_number)

    _assert_error(response, 503, "stripe_unavailable")
    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    assert [r.status for r in _records(db, placed)] == [CHECKOUT_CREATING]
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _stock(db, placed) == RESERVED_STOCK
    assert _api_logs(caplog) == []


# ---------------------------------------------------------------------------
# 确认：POST /api/member/pay/stripe/confirm
# ---------------------------------------------------------------------------


def test_confirm_paid(app: FastAPI, db: Session, client: TestClient) -> None:
    """第 4 条「访客从 Stripe 返回后前台调用本站接口，服务端按授权找到该单的会话向 Stripe 查询
    一次」「全部相符且订单仍为 `awaiting_demo_payment` 时……进入 `demo_paid`」；P07 会员凭登录
    会话访问结果页：200 {"result":"paid"}，只查询一次。"""
    member_id = _member(db)
    placed = _order(db, member_id)
    record = _checkout(db, placed, CHECKOUT_OPEN)
    headers = _login(db, member_id)
    gateway = _inject(app, _ok(_session(record, CHECKOUT_COMPLETE, paid=True)))

    response = _post(client, CONFIRM_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "paid"}
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    assert _paths(gateway) == [("GET", f"{SESSIONS_PATH}/{record.session_id}")]
    assert _status(db, placed) == STATUS_PAID


def test_confirm_when_stripe_unavailable_is_confirming(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P07 确认中「服务端尚未确认（付款结果未定，或查询时 Stripe 暂不可用）」；HANDOFF 0.54「Stripe
    不可用……按『确认中』显示」：不回 503，回 confirming；订单不变。"""
    member_id = _member(db)
    placed = _order(db, member_id)
    _checkout(db, placed, CHECKOUT_OPEN)
    headers = _login(db, member_id)
    gateway = _inject(app, UNAVAILABLE)

    response = _post(client, CONFIRM_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "confirming"}
    assert response.headers["cache-control"] == NO_STORE
    assert len(gateway.calls) == 1
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


def test_confirm_open_unpaid_is_not_paid(app: FastAPI, db: Session, client: TestClient) -> None:
    """P07 尚未付款「本单的会话仍进行中、未付款（例如直接打开本页）」；第 4 条「返回地址本身不作
    依据」：会话仍 open 未付款时 not_paid，订单仍待支付。"""
    member_id = _member(db)
    placed = _order(db, member_id)
    record = _checkout(db, placed, CHECKOUT_OPEN)
    headers = _login(db, member_id)
    _inject(app, _ok(_session(record, CHECKOUT_OPEN)))

    response = _post(client, CONFIRM_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "not_paid"}
    assert response.headers["cache-control"] == NO_STORE
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


def test_confirm_late_payment_refund_is_refunded(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P07 已取消、已发起退款「服务端告知本单有来源为会话的 Stripe 退款尝试时」：订单已取消且有
    来源为会话的退款尝试时 refunded，不调用 Stripe。"""
    member_id = _member(db)
    placed = _order(db, member_id, STATUS_CANCELLED)
    _refund(db, _checkout(db, placed, CHECKOUT_EXPIRED))
    headers = _login(db, member_id)
    gateway = _inject(app)

    response = _post(client, CONFIRM_URL, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "refunded"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _status(db, placed) == STATUS_CANCELLED


# ---------------------------------------------------------------------------
# 与游客接口相同
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["complete", "method-changed", "not-payable", "unavailable"])
def test_start_matches_guest_interface(
    app: FastAPI, db: Session, client: TestClient, scenario: str
) -> None:
    """验收：两个接口的成功响应、结果映射与错误都与 SHOP-TASK-120 的游客接口相同——同一场景分别在
    会员订单上调用会员发起接口、在游客订单上调用游客发起接口，状态码与响应体相同，
    都带 no-store。"""
    if scenario != "method-changed":
        _stripe_switch(db)
    status = STATUS_CANCELLED if scenario == "not-payable" else STATUS_AWAITING_PAYMENT
    member_id = _member(db)
    mine = _order(db, member_id, status)
    guest = _order(db, None, status)
    if scenario == "complete":
        _checkout(db, mine, CHECKOUT_COMPLETE)
        _checkout(db, guest, CHECKOUT_COMPLETE)
    results = [UNAVAILABLE, UNAVAILABLE] if scenario == "unavailable" else []
    _inject(app, *results)
    guest_client = TestClient(app, base_url="https://testserver")

    member_response = _post(client, START_URL, _login(db, member_id), mine.order_number)
    guest_headers = _guest_access(db, guest)
    guest_response = _post(guest_client, GUEST_START_URL, guest_headers, guest.order_number)

    assert member_response.status_code == guest_response.status_code, member_response.text
    assert member_response.json() == guest_response.json()
    assert member_response.headers["cache-control"] == NO_STORE
    assert guest_response.headers["cache-control"] == NO_STORE


def test_confirm_matches_guest_interface(app: FastAPI, db: Session, client: TestClient) -> None:
    """验收：确认的结果与游客接口相同——会员订单与游客订单各有一条仍 open 未付款的会话时，两个确认
    接口都回 200 {"result":"not_paid"}。"""
    member_id = _member(db)
    mine = _order(db, member_id)
    guest = _order(db, None)
    mine_record = _checkout(db, mine, CHECKOUT_OPEN)
    guest_record = _checkout(db, guest, CHECKOUT_OPEN)
    _inject(
        app,
        _ok(_session(mine_record, CHECKOUT_OPEN)),
        _ok(_session(guest_record, CHECKOUT_OPEN)),
    )
    guest_client = TestClient(app, base_url="https://testserver")

    member_response = _post(client, CONFIRM_URL, _login(db, member_id), mine.order_number)
    guest_headers = _guest_access(db, guest)
    guest_response = _post(guest_client, GUEST_CONFIRM_URL, guest_headers, guest.order_number)

    assert member_response.status_code == guest_response.status_code == 200
    assert member_response.json() == guest_response.json() == {"result": "not_paid"}


# ---------------------------------------------------------------------------
# 授权与 CSRF（两个接口）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("case", ["no-cookie", "malformed", "revoked"])
def test_without_member_session_is_401(
    app: FastAPI, db: Session, client: TestClient, url: str, case: str
) -> None:
    """第 3 条「按现有授权（……会员登录会话）与 CSRF 校验后发起」；P06、P07 会员凭登录会话访问：
    未登录、会话不合法或已撤销 401 member_session_required、带 no-store，
    不调用 Stripe、不写记录。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    gateway = _inject(app)
    headers: dict[str, str] = {}
    if case == "malformed":
        headers = {"Cookie": f"{MEMBER_COOKIE}=not-a-token", "X-CSRF-Token": "0" * 64}
    elif case == "revoked":
        headers = _login(db, member_id)
        assert client.post("/api/member/logout", headers=headers).status_code == 204

    response = _post(client, url, headers, placed.order_number)

    _assert_error(response, 401, SESSION_REQUIRED)
    _assert_untouched(db, placed, gateway)


@pytest.mark.parametrize("url", URLS)
def test_guest_access_cookie_is_not_a_member_session(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收「只凭会员会话，不读订单访问 cookie」：拿游客订单的访问 cookie 与它的 CSRF 令牌
    调用会员接口是 401 member_session_required，不调用 Stripe、不写记录。"""
    _stripe_switch(db)
    guest = _order(db, None)
    headers = _guest_access(db, guest)
    gateway = _inject(app)

    response = _post(client, url, headers, guest.order_number)

    _assert_error(response, 401, SESSION_REQUIRED)
    _assert_untouched(db, guest, gateway)


@pytest.mark.parametrize("url", URLS)
def test_guest_access_cookie_does_not_widen_member_access(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """权限第 6 条「会员仅能访问自己认领或下单的订单」；验收「不读订单访问 cookie」：已登录会员的
    浏览器同时持有某张游客订单的 guest_checkout 授权，借它的订单号访问仍是
    404 order_not_found。"""
    _stripe_switch(db)
    member_id = _member(db)
    guest = _order(db, None)
    member_headers = _login(db, member_id)
    guest_headers = _guest_access(db, guest)
    headers = {
        "Cookie": f"{member_headers['Cookie']}; {guest_headers['Cookie']}",
        "X-CSRF-Token": member_headers["X-CSRF-Token"],
    }
    gateway = _inject(app)

    response = _post(client, url, headers, guest.order_number)

    _assert_error(response, 404, NOT_FOUND)
    _assert_untouched(db, guest, gateway)


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("case", ["missing", "wrong", "two-headers", "guest-token"])
def test_without_csrf_is_403(
    app: FastAPI, db: Session, client: TestClient, url: str, case: str
) -> None:
    """第 3 条「与 CSRF 校验后发起」；P07「页面调用本站的确认接口一次（须 CSRF）」；权限第 6 条
    「支付……等写操作另须 CSRF 令牌」：缺、错、重复或拿游客订单的 CSRF 令牌一律
    403 csrf_failed，不调用 Stripe、不写记录。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    cookie = {"Cookie": headers["Cookie"]}
    gateway = _inject(app)

    if case == "two-headers":
        raw = [("Cookie", headers["Cookie"]), ("Content-Type", "application/json")]
        raw += [("X-CSRF-Token", headers["X-CSRF-Token"])] * 2
        body = json.dumps({"order_number": placed.order_number})
        response = client.post(url, content=body, headers=raw)
    else:
        if case == "missing":
            headers = cookie
        elif case == "wrong":
            headers = cookie | {"X-CSRF-Token": "0" * 64}
        else:
            guest = _guest_access(db, _order(db, None))
            headers = cookie | {"X-CSRF-Token": guest["X-CSRF-Token"]}
        response = _post(client, url, headers, placed.order_number)

    _assert_error(response, 403, CSRF_FAILED)
    _assert_untouched(db, placed, gateway)


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("case", ["other-member", "guest", "unknown", "invalid"])
def test_others_order_is_404(
    app: FastAPI, db: Session, client: TestClient, url: str, case: str
) -> None:
    """权限第 6 条「会员仅能访问自己认领或下单的订单」；验收：本人订单经 _member_order 定位，他人
    订单、游客订单、不存在或不合法的订单号一律 404 order_not_found，不回显订单号，不调用 Stripe、
    不写记录。"""
    _stripe_switch(db)
    member_id = _member(db)
    headers = _login(db, member_id)
    gateway = _inject(app)
    target: _Placed | None = None
    number = UNKNOWN_NUMBER if case == "unknown" else INVALID_NUMBER
    if case == "other-member":
        target = _order(db, _member(db))
    elif case == "guest":
        target = _order(db, None)
    if target is not None:
        number = target.order_number

    response = _post(client, url, headers, number)

    _assert_error(response, 404, NOT_FOUND)
    assert number not in response.text
    if target is not None:
        _assert_untouched(db, target, gateway)
    assert gateway.calls == []


@pytest.mark.parametrize("url", URLS)
def test_csrf_is_checked_before_order_lookup(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收：检查顺序与 POST /api/member/pay/cancel 相同（require_member → require_member_csrf →
    查订单）：没有 CSRF 时其他会员的订单与不存在的订单号都是 403，不因订单号而不同。"""
    member_id = _member(db)
    other = _order(db, _member(db))
    headers = {"Cookie": _login(db, member_id)["Cookie"]}
    _inject(app)

    _assert_error(_post(client, url, headers, other.order_number), 403, CSRF_FAILED)
    _assert_error(_post(client, url, headers, UNKNOWN_NUMBER), 403, CSRF_FAILED)


# ---------------------------------------------------------------------------
# 请求格式与检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("with_session", [False, True], ids=["no-session", "session"])
def test_unknown_field_is_422_before_session(
    app: FastAPI, db: Session, client: TestClient, url: str, with_session: bool
) -> None:
    """验收：请求体与游客接口相同（只有 order_number，extra forbid），检查顺序与 POST
    /api/member/pay/cancel 相同（422 先于会话）：有无会话都是 422，只给位置、类型与固定消息，不回显
    输入，带 no-store，不调用 Stripe。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id) if with_session else {}
    gateway = _inject(app)
    secret = "4242424242424242"
    body = {"order_number": placed.order_number, "card_number": secret}

    response = _post(client, url, headers, placed.order_number, body=body)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "card_number"]
    assert set(error) == {"type", "loc", "msg"}
    assert secret not in response.text
    assert placed.order_number not in response.text
    _assert_untouched(db, placed, gateway)


def test_illegal_lang_is_422_without_echo(app: FastAPI, db: Session, client: TestClient) -> None:
    """验收：发起的查询参数 lang 与游客接口相同，只接受 en、zh、ms，不合法时 422 不回显所给的值、
    带 no-store，不写记录、不调用 Stripe。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    gateway = _inject(app)
    secret = "fr-SECRETLANG"

    response = _post(client, START_URL, headers, placed.order_number, params={"lang": secret})

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    [error] = response.json()["detail"]
    assert error["loc"] == ["query", "lang"]
    assert secret not in response.text
    _assert_untouched(db, placed, gateway)


@pytest.mark.parametrize("url", URLS)
def test_oversized_body_is_413_first(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收：检查顺序与 POST /api/member/pay/cancel 相同（413 → 415 → 422 → 401 → 403）：没有会话
    且请求体超过 8 KB 时先回 413，带 no-store，不回显请求内容。"""
    member_id = _member(db)
    placed = _order(db, member_id)
    gateway = _inject(app)
    padding = "4111111111111111" * (MAX_BODY_BYTES // 16)
    body = {"order_number": placed.order_number, "card_number": padding}

    response = _post(client, url, {}, placed.order_number, body=body)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert "4111111111111111" not in response.text
    _assert_untouched(db, placed, gateway)


@pytest.mark.parametrize("url", URLS)
def test_non_json_is_415_before_session(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收：检查顺序与 POST /api/member/pay/cancel 相同：不是 JSON 415，先于 require_member。"""
    placed = _order(db, _member(db))
    _inject(app)
    body = json.dumps({"order_number": placed.order_number})

    response = client.post(url, content=body, headers={"Content-Type": "text/plain"})

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == NO_STORE


# ---------------------------------------------------------------------------
# no-store 与日志
# ---------------------------------------------------------------------------


def test_every_response_is_no_store(app: FastAPI, db: Session, client: TestClient) -> None:
    """权限第 6 条「不缓存敏感响应」；验收：两个接口的处理函数与依赖产生的响应（含错误）都带
    no-store——逐一走到 200、401、403、404、409、413、415、422、503。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    cancelled = _order(db, member_id, STATUS_CANCELLED)
    headers = _login(db, member_id)
    cookie = {"Cookie": headers["Cookie"]}
    big = json.dumps({"order_number": "x" * (MAX_BODY_BYTES + 1)})
    _inject(app, UNAVAILABLE)

    responses = [
        _post(client, CONFIRM_URL, headers, placed.order_number),
        _post(client, START_URL, {}, placed.order_number),
        _post(client, CONFIRM_URL, cookie, placed.order_number),
        _post(client, START_URL, headers, UNKNOWN_NUMBER),
        _post(client, START_URL, headers, cancelled.order_number),
        client.post(CONFIRM_URL, content=big, headers=headers),
        client.post(START_URL, content="{}", headers=headers | {"Content-Type": "text/plain"}),
        client.post(CONFIRM_URL, json={}, headers=headers),
        _post(client, START_URL, headers, placed.order_number),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 401, 403, 404, 409, 413, 415, 422, 503]
    for response in responses:
        assert response.headers["cache-control"] == NO_STORE


def test_writes_no_log_records(
    app: FastAPI, db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """权限第 6 条「应用日志…不记录…」；验收「接口不写日志」：两个接口的成功与错误都不写接口层
    日志。"""
    _stripe_switch(db)
    member_id = _member(db)
    placed = _order(db, member_id)
    headers = _login(db, member_id)
    _inject(app, _ok(_created_session()))
    caplog.set_level(logging.DEBUG)

    _post(client, START_URL, headers, placed.order_number)
    _post(client, START_URL, headers, UNKNOWN_NUMBER)
    _post(client, CONFIRM_URL, {}, placed.order_number)
    _post(client, CONFIRM_URL, {"Cookie": headers["Cookie"]}, placed.order_number)

    assert _api_logs(caplog) == []

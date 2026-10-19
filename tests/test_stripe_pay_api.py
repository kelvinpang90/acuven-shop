"""游客支付页的 Stripe 发起与确认接口：POST /api/pay/stripe/start、POST /api/pay/stripe/confirm
（app/api/stripe_pay.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 2 条（下称「第 2 条」）：“服务端在访客发起支付时按当时的值判定”；“为『模拟』且没有 Stripe
  记录（或只有 expired 记录）时，现有模拟支付流程不变”。
- 第 3 条（下称「第 3 条」）：“访客在支付页按现有授权（游客短期凭据或会员登录会话）与 CSRF 校验
  后发起”；“24 小时内仍无法确定就不再自动处理、告警由人工核对”。
- 第 4 条（下称「第 4 条」）：“访客从 Stripe 返回后前台调用本站接口，服务端按授权找到该单的会话
  向 Stripe 查询一次”；“返回地址本身不作依据”。
- 第 9 条（下称「第 9 条」）：“创建会话或查询时 Stripe 不可用，支付页提示稍后再试……不自动改用
  模拟支付。CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe”。
docs/UX.md 0.11 的 P06（下称「P06」）与 P07（下称「P07」），以及 docs/HANDOFF.md 0.54 的落实写法
（下称「HANDOFF 0.54」）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；只出自
SHOP-TASK-120 验收标准的约定注明「验收：」。

引擎、内存库与接口的会话依赖照 tests/test_demo_payment.py（SQLite 内存库、StaticPool、每个连接
打开外键检查并断言已打开）；订单、会话记录与退款尝试在本文件里直接按模型写入，库存按已为订单预留
2 件写入，游客的访问 cookie 经 issue_order_access 签发。应用的 Settings 带齐 Stripe 测试配置；
Stripe 一律以 app.state.stripe_gateway 注入 FakeStripeGateway（第 9 条）。
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
    Order,
    OrderItem,
    Product,
    ProductVariant,
    SiteSetting,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import (
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
from app.services import order_access, payment
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeOutcome,
    StripeResult,
)

START_URL = "/api/pay/stripe/start"
CONFIRM_URL = "/api/pay/stripe/confirm"
URLS = [START_URL, CONFIRM_URL]
SESSIONS_PATH = "/v1/checkout/sessions"
MAX_BODY_BYTES = 8 * 1024

NO_STORE = "no-store"

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
NEW_SESSION_ID = "cs_test_created_by_start"

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
    # 访问 cookie 与 CSRF 令牌。
    headers: dict[str, str]


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


def _guest(db: Session, status: str = STATUS_AWAITING_PAYMENT) -> _Placed:
    """1 分钟前下单、未到支付到期时间的游客订单（应付 6800 仙）与该单的 guest_checkout 授权；
    写完即提交。"""
    n = next(_SEQUENCE)
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
        member_id=None,
        claim_status=CLAIM_OPEN,
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
    issued = order_access.issue_order_access(db, None, order.id, SCOPE_GUEST_CHECKOUT, _now())
    db.commit()
    assert issued.new_token is not None
    headers = {
        "Cookie": f"{order_access.COOKIE_NAME}={issued.new_token}",
        "X-CSRF-Token": issued.csrf_token,
    }
    return _Placed(order.order_number, order.id, variant_id, headers)


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
        payment_intent=PaymentIntentInfo(id="pi_test_created_by_start"),
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
    placed: _Placed,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> Any:
    payload = {"order_number": placed.order_number} if body is None else body
    return client.post(
        url, json=payload, headers=placed.headers if headers is None else headers, params=params
    )


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


def _api_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.api")]


# ---------------------------------------------------------------------------
# 发起：POST /api/pay/stripe/start
# ---------------------------------------------------------------------------


def test_start_creates_session_and_returns_only_the_checkout_url(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 3 条「访客在支付页按现有授权……与 CSRF 校验后发起」「成功后锁住该记录写回会话 ID 与付款页
    地址」；P06「点 `[pay.stripe_go]` 由服务端按 Stripe 节第 3 条创建会话并返回付款页地址，页面只
    跳转到服务端返回的地址」；验收：200 {"result":"redirect","checkout_url":付款页地址}，响应里除
    付款页地址外不带任何 Stripe ID，带 no-store。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app, _ok(_created_session()))

    response = _post(client, START_URL, placed, params={"lang": "zh"})

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "redirect", "checkout_url": NEW_CHECKOUT_URL}
    assert response.headers["cache-control"] == NO_STORE
    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    assert dict(gateway.calls[0].form)["locale"] == "zh"
    [record] = _records(db, placed)
    assert record.status == CHECKOUT_OPEN
    assert record.checkout_session_id == NEW_SESSION_ID
    assert record.checkout_url == NEW_CHECKOUT_URL
    for stripe_id in (NEW_SESSION_ID, "pi_test_created_by_start", record.idempotency_key):
        assert stripe_id not in response.text


def test_start_with_open_session_returns_the_same_url(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P06「有进行中的会话……`[pay.stripe_continue]` 打开同一会话，不另建会话」；第 2 条「订单只要有
    一条不是 expired 的 Stripe 记录……不另建会话」：直接返回原付款页地址，不调用 Stripe，不写新
    记录。"""
    _stripe_switch(db)
    placed = _guest(db)
    _checkout(db, placed, CHECKOUT_OPEN)
    [before] = _records(db, placed)
    url = before.checkout_url
    gateway = _inject(app)

    response = _post(client, START_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "redirect", "checkout_url": url}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert [r.id for r in _records(db, placed)] == [before.id]


def test_start_with_complete_session_is_complete(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P06「本单若已有付款完成的会话，服务端先按 Stripe 节第 4 条向 Stripe 确认」与 HANDOFF 0.54
    「P06 打开时已有 complete 会话，由前端调用确认接口完成『服务端先确认』」；验收：complete 回
    200 {"result":"complete","checkout_url":null}，不调用 Stripe。"""
    _stripe_switch(db)
    placed = _guest(db)
    _checkout(db, placed, CHECKOUT_COMPLETE)
    gateway = _inject(app)

    response = _post(client, START_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "complete", "checkout_url": None}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []


def test_start_on_simulated_route_is_method_changed(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 2 条「服务端在访客发起支付时按当时的值判定」；P06「点 Stripe 跳转时已改为模拟……页面显示
    `[pay.method_changed]`」；HANDOFF 0.54 的 409 写法：开关为模拟（没有设置行即默认模拟）时 409
    method_changed、不带 status，不写记录、不调用 Stripe。"""
    placed = _guest(db)
    gateway = _inject(app)

    response = _post(client, START_URL, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "method_changed"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _records(db, placed) == []


def test_start_on_order_not_awaiting_payment_is_order_not_payable(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 4 条「只看订单是否仍待支付」与 SHOP-TASK-112 的 start_checkout「订单不是
    awaiting_demo_payment：not_payable」；验收：409 order_not_payable，照现有 _conflict 带订单当前
    status，不写记录、不调用 Stripe。"""
    _stripe_switch(db)
    placed = _guest(db, STATUS_CANCELLED)
    gateway = _inject(app)

    response = _post(client, START_URL, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_payable", "status": STATUS_CANCELLED}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _records(db, placed) == []


def test_start_under_review_is_stripe_under_review(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对，订单在此期间不取消、库存继续
    预留」；P06「转人工核对时，不显示 `[pay.stripe_go]`，改显示 `[pay.stripe_under_review]`」；
    HANDOFF 0.54「发起时转人工回 409 `stripe_under_review`」：不调用 Stripe，订单与库存不变。"""
    _stripe_switch(db)
    placed = _guest(db)
    _checkout(db, placed, CHECKOUT_CREATING, alerted_at=_now())
    gateway = _inject(app)

    response = _post(client, START_URL, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "stripe_under_review"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _stock(db, placed) == RESERVED_STOCK


def test_start_when_stripe_unavailable_is_503(
    app: FastAPI, db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """第 9 条「创建会话或查询时 Stripe 不可用，支付页提示稍后再试，订单保持待支付……不自动改用
    模拟支付」；HANDOFF 0.54「Stripe 不可用时接口回 503 `stripe_unavailable`」；第 3 条「调用结果
    未知……记录保持 `creating`」；验收：接口不写日志。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app, UNAVAILABLE)
    caplog.set_level(logging.DEBUG)

    response = _post(client, START_URL, placed)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "stripe_unavailable"}
    assert response.headers["cache-control"] == NO_STORE
    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    assert [r.status for r in _records(db, placed)] == [CHECKOUT_CREATING]
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _stock(db, placed) == RESERVED_STOCK
    assert _api_logs(caplog) == []


# ---------------------------------------------------------------------------
# 确认：POST /api/pay/stripe/confirm
# ---------------------------------------------------------------------------


def test_confirm_paid(app: FastAPI, db: Session, client: TestClient) -> None:
    """第 4 条「访客从 Stripe 返回后前台调用本站接口，服务端按授权找到该单的会话向 Stripe 查询
    一次」「全部相符且订单仍为 `awaiting_demo_payment` 时……进入 `demo_paid`」；P07 成功：200
    {"result":"paid"}，只查询一次。"""
    placed = _guest(db)
    record = _checkout(db, placed, CHECKOUT_OPEN)
    gateway = _inject(app, _ok(_session(record, CHECKOUT_COMPLETE, paid=True)))

    response = _post(client, CONFIRM_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "paid"}
    assert response.headers["cache-control"] == NO_STORE
    assert _paths(gateway) == [("GET", f"{SESSIONS_PATH}/{record.session_id}")]
    assert _status(db, placed) == STATUS_PAID


def test_confirm_when_stripe_unavailable_is_confirming(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P07 确认中「服务端尚未确认（付款结果未定，或查询时 Stripe 暂不可用）」；HANDOFF 0.54「Stripe
    不可用……按『确认中』显示」；验收：Stripe 不可用不回 503，由 confirm_order_payment 归为
    confirming；订单不变。"""
    placed = _guest(db)
    _checkout(db, placed, CHECKOUT_OPEN)
    gateway = _inject(app, UNAVAILABLE)

    response = _post(client, CONFIRM_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "confirming"}
    assert response.headers["cache-control"] == NO_STORE
    assert len(gateway.calls) == 1
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


def test_confirm_open_unpaid_is_not_paid(app: FastAPI, db: Session, client: TestClient) -> None:
    """P07 尚未付款「本单的会话仍进行中、未付款（例如直接打开本页）」；第 4 条「返回地址本身不作
    依据」：会话仍 open 未付款时 not_paid，订单仍待支付。"""
    placed = _guest(db)
    record = _checkout(db, placed, CHECKOUT_OPEN)
    _inject(app, _ok(_session(record, CHECKOUT_OPEN)))

    response = _post(client, CONFIRM_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "not_paid"}
    assert response.headers["cache-control"] == NO_STORE
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


def test_confirm_late_payment_refund_is_refunded(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """P07 已取消、已发起退款「服务端告知本单有来源为会话的 Stripe 退款尝试时（不论该尝试当前
    状态……）」；第 6 条迟到的付款「订单状态不变……全额退款」：订单已取消且有来源为会话的退款尝试
    时 refunded，不调用 Stripe。"""
    placed = _guest(db, STATUS_CANCELLED)
    _refund(db, _checkout(db, placed, CHECKOUT_EXPIRED))
    gateway = _inject(app)

    response = _post(client, CONFIRM_URL, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"result": "refunded"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _status(db, placed) == STATUS_CANCELLED


# ---------------------------------------------------------------------------
# 授权、CSRF 与请求校验（两个接口）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", URLS)
def test_without_access_cookie_is_401(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """第 3 条「按现有授权……与 CSRF 校验后发起」与第 4 条「服务端按授权找到该单的会话」；P06
    「游客凭据过期或不属于该单时替换整页」`[pay.session_expired]`：没有订单访问 cookie 时 401
    access_expired、带 no-store，不调用 Stripe、不写记录。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app)
    headers = {"X-CSRF-Token": placed.headers["X-CSRF-Token"]}

    response = _post(client, url, placed, headers=headers)

    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "access_expired"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _records(db, placed) == []


@pytest.mark.parametrize("url", URLS)
def test_without_csrf_is_403(app: FastAPI, db: Session, client: TestClient, url: str) -> None:
    """第 3 条「与 CSRF 校验后发起」；P07「页面调用本站的确认接口一次（须 CSRF）」：缺 CSRF 令牌时
    403 csrf_failed、带 no-store，不调用 Stripe、不写记录。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app)
    headers = {"Cookie": placed.headers["Cookie"]}

    response = _post(client, url, placed, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []
    assert _records(db, placed) == []


@pytest.mark.parametrize("url", URLS)
def test_unknown_field_is_422_without_echo(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收：请求体只有 order_number（extra forbid），未知字段 422，只给位置、类型与固定消息，不回显
    输入（订单号与多出字段的取值都不出现在响应里），带 no-store，不调用 Stripe。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app)
    secret = "4242424242424242"
    body = {"order_number": placed.order_number, "card_number": secret}

    response = _post(client, url, placed, body=body)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    [error] = response.json()["detail"]
    assert error["loc"] == ["body", "card_number"]
    assert secret not in response.text
    assert placed.order_number not in response.text
    assert gateway.calls == []


def test_illegal_lang_is_422_without_echo(app: FastAPI, db: Session, client: TestClient) -> None:
    """验收：发起的查询参数 lang 照 LanguageDep，只接受 en、zh、ms，不合法时 422 不回显所给的值，
    带 no-store，不写记录、不调用 Stripe。"""
    _stripe_switch(db)
    placed = _guest(db)
    gateway = _inject(app)
    secret = "fr-SECRETLANG"

    response = _post(client, START_URL, placed, params={"lang": secret})

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    [error] = response.json()["detail"]
    assert error["loc"] == ["query", "lang"]
    assert secret not in response.text
    assert gateway.calls == []
    assert _records(db, placed) == []


@pytest.mark.parametrize("url", URLS)
def test_oversized_body_is_413_before_authorization(
    app: FastAPI, db: Session, client: TestClient, url: str
) -> None:
    """验收：检查顺序与 POST /api/pay/cancel 相同（413 → 415 → 422 → 401 → 403）：没有 cookie 与
    CSRF 令牌而请求体超过 8 KB 时先回 413，带 no-store。"""
    placed = _guest(db)
    gateway = _inject(app)
    body = {"order_number": "x" * (MAX_BODY_BYTES + 1)}

    response = _post(client, url, placed, headers={}, body=body)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []

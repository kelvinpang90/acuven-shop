"""Stripe webhook 接口：POST /api/stripe/webhook（app/api/stripe_webhook.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 1 条（下称「第 1 条」）：“webhook 事件、会话或付款的 `livemode` 为真即拒绝”。
- 第 2 条（下称「第 2 条」）：签名密钥未配置或密钥不是测试密钥时不启用 Stripe。
- 第 7 条（下称「第 7 条」）：“用原始请求体与 webhook 签名密钥校验 Stripe-Signature（含时间
  容差），不通过回 400、不处理”；“已提交过的事件直接回 200；处理失败整个事务回滚、回 5xx，由
  Stripe 重发”；“其他类型回 200 不处理”；“不需要会话与 CSRF”；“不存、不记录事件原文”。
- 第 8 条（下称「第 8 条」）：“之后的退款事件（按 Stripe 退款 ID 或元数据里的记录 ID 关联）更新
  记录状态”。
- 第 9 条（下称「第 9 条」）：“CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe”。
以及 docs/HANDOFF.md 0.55 的落实写法（3）（下称「HANDOFF 0.55」：请求体超过 256 KB 回 413、凭据
不可用回 503 `stripe_unavailable` 且不验签、签名不通过回 400、事件内容不合格回 400、`livemode`
为真回 400 且不记事件并记 error 日志；处理中 Stripe 不可用回 503）。每条测试（参数化的测试是每个
用例）的文档字符串写明它守住的那一句；只出自 SHOP-TASK-129 验收标准的约定注明「验收：」。

引擎、内存库与接口的会话依赖照 tests/test_stripe_pay_api.py；订单、会话记录与退款尝试在本文件里
直接按模型写入。Stripe 配置照 tests/test_stripe_gateway.py 的 make_settings 写法在本文件里另写；
Stripe 一律以 app.state.stripe_gateway 注入 FakeStripeGateway（第 9 条）。接口的当前时间替换为
NOW，Stripe-Signature 在测试里用测试签名密钥与 NOW 计算。请求一律不带 cookie 与 CSRF 令牌。
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import stripe_webhook as webhook_api
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
    StripeCheckout,
    StripeEvent,
    StripeRefund,
)
from app.models.order import (
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import payment
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    Refund,
    StripeOutcome,
    StripeResult,
)

URL = "/api/stripe/webhook"
SESSIONS_PATH = "/v1/checkout/sessions"
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
NO_STORE = "no-store"

BASE_URL = "https://api.stripe.example.com"
SECRET_KEY = "sk_test_placeholder"
WEBHOOK_SECRET = "whsec_test_placeholder"
DEMO_EMAIL = "demo@example.com"
SITE_URL = "https://shop.example.com"

# 接口的当前时间；签名时间戳默认取它。
NOW = datetime(2026, 10, 21, 3, 0)
TOLERANCE_SECONDS = 300

# 订单一行 2 件、单价 3000 仙，运费 800 仙；规格每日初始库存 10，已为订单预留 2 件。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY
FINGERPRINT = "0f" * 32

COMPLETED = "checkout.session.completed"
REFUND_UPDATED = "refund.updated"
REFUND_ID = "re_test_webhook_api"
REFUND_AMOUNT = 2500

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

_SEQUENCE = itertools.count(1)


def make_settings(**overrides: str) -> Settings:
    values = {
        "stripe_api_base_url": BASE_URL,
        "stripe_secret_key": SECRET_KEY,
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_demo_email": DEMO_EMAIL,
        "public_site_url": SITE_URL,
        **overrides,
    }
    return Settings(_env_file=None, **values)


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


def _make_app(engine: Engine, settings: Settings) -> FastAPI:
    app = create_app(settings)

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    return _make_app(engine, make_settings())


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env：业务规则层自己取配置之处一律用默认配置。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook_api, "_utcnow", lambda: NOW)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@dataclass(frozen=True)
class _Placed:
    order_id: int
    variant_id: int


@dataclass(frozen=True)
class _Record:
    id: int
    session_id: str
    create_key: str
    order_id: int


def _order(db: Session, status: str = STATUS_AWAITING_PAYMENT) -> _Placed:
    """1 分钟前下单、未到支付到期时间的游客订单（应付 6800 仙）；写完即提交。"""
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
    variant = ProductVariant(
        product_id=product.id,
        sku=f"TEE-{n}",
        price_sen=UNIT_PRICE,
        daily_initial_stock=INITIAL_STOCK,
        available_stock=RESERVED_STOCK,
        is_active=True,
    )
    _add(db, variant)
    placed_at = NOW - timedelta(minutes=1)
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
        variant_id=variant.id,
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
    return _Placed(order.id, variant.id)


def _checkout(db: Session, placed: _Placed, status: str = CHECKOUT_OPEN) -> _Record:
    """直接写一条合法的会话记录（模拟之前的发起留下的），提交后返回它的对账字段。expired 的
    记录为已付款（迟到付款，作退款尝试的来源）。"""
    n = next(_SEQUENCE)
    session_id = f"cs_test_{n}"
    fields: dict[str, Any] = {
        "order_id": placed.order_id,
        "idempotency_key": f"create-key-{n}",
        "checkout_session_id": session_id,
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "payment_status": PAYMENT_PAID if status == CHECKOUT_EXPIRED else PAYMENT_UNPAID,
        "create_params": "[]",
        "expires_at": NOW + timedelta(minutes=31),
        "created_at": NOW - timedelta(seconds=30),
        "active_order_id": None if status == CHECKOUT_EXPIRED else placed.order_id,
    }
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/{session_id}"
    if status == CHECKOUT_EXPIRED:
        fields["completed_at"] = NOW - timedelta(seconds=20)
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return _Record(record.id, session_id, record.idempotency_key, placed.order_id)


def _attempt(db: Session) -> int:
    """已取消订单的已付款 expired 会话记录与来源为它的 pending 退款尝试（已记下 Stripe 退款
    ID）；提交后返回尝试 ID。"""
    record = _checkout(db, _order(db, STATUS_CANCELLED), CHECKOUT_EXPIRED)
    attempt = StripeRefund(
        payment_intent_id=_intent_id(record),
        source_type=SOURCE_CHECKOUT,
        checkout_id=record.id,
        stripe_refund_id=REFUND_ID,
        amount_sen=REFUND_AMOUNT,
        status=STRIPE_REFUND_PENDING,
        call_count=0,
        created_at=NOW - timedelta(hours=1),
        active_checkout_id=record.id,
    )
    db.add(attempt)
    db.commit()
    return attempt.id


def _intent_id(record: _Record) -> str:
    return f"pi_test_{record.id}"


def _paid_session(record: _Record) -> CheckoutSession:
    """Stripe 查询到的、与记录和订单相符的已付款会话；payment_intent 已展开、方式为 card。"""
    return CheckoutSession(
        id=record.session_id,
        status=CHECKOUT_COMPLETE,
        payment_status=PAYMENT_PAID,
        livemode=False,
        currency="myr",
        amount_total=TOTAL,
        client_reference_id=str(record.order_id),
        order_id=str(record.order_id),
        create_key=record.create_key,
        payment_intent=PaymentIntentInfo(
            id=_intent_id(record),
            status="succeeded",
            livemode=False,
            payment_method_type="card",
        ),
    )


def _raw_session(record: _Record) -> dict[str, Any]:
    """事件里的会话对象（Stripe 原文形态，含不读取的客户资料）。"""
    return {
        "id": record.session_id,
        "object": "checkout.session",
        "status": "complete",
        "payment_status": "paid",
        "livemode": False,
        "currency": "myr",
        "amount_total": TOTAL,
        "client_reference_id": str(record.order_id),
        "metadata": {"order_id": str(record.order_id), "create_key": record.create_key},
        "customer_details": {"email": "someone@example.com"},
        "payment_intent": _intent_id(record),
    }


def _raw_event(
    event_type: str, obj: dict[str, Any], *, event_id: str = "evt_test_api", **overrides: Any
) -> bytes:
    data: dict[str, Any] = {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "livemode": False,
        "created": int(NOW.replace(tzinfo=UTC).timestamp()),
        "data": {"object": obj},
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


def _sign(payload: bytes, *, secret: str = WEBHOOK_SECRET, at: datetime = NOW) -> str:
    """按 Stripe 的写法计算 Stripe-Signature：t 为签名时间戳，v1 为对「t.请求体」的
    HMAC-SHA256。"""
    timestamp = int(at.replace(tzinfo=UTC).timestamp())
    signed = str(timestamp).encode("ascii") + b"." + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post(client: TestClient, payload: bytes, signature: str | None = "sign") -> Any:
    """只带 Content-Type 与 Stripe-Signature，不带 cookie 与 CSRF 令牌。"""
    headers = {"Content-Type": "application/json"}
    if signature == "sign":
        signature = _sign(payload)
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post(URL, content=payload, headers=headers)


def _inject(app: FastAPI, *results: StripeResult) -> FakeStripeGateway:
    gateway = FakeStripeGateway(*results)
    app.state.stripe_gateway = gateway
    return gateway


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _stripe_events(db: Session) -> list[tuple[str, str, str, datetime]]:
    db.rollback()
    columns = (StripeEvent.event_id, StripeEvent.event_type, StripeEvent.outcome)
    stmt = select(*columns, StripeEvent.processed_at).order_by(StripeEvent.id)
    rows = [tuple(row) for row in db.execute(stmt)]
    db.rollback()
    return rows


def _status(db: Session, placed: _Placed) -> str:
    db.rollback()
    stmt = select(Order.status).where(Order.id == placed.order_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _attempt_status(db: Session, attempt_id: int) -> str:
    db.rollback()
    stmt = select(StripeRefund.status).where(StripeRefund.id == attempt_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _api_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.api")]


def _assert_no_echo(response: Any, *values: str) -> None:
    """响应带 no-store，且不出现请求里的事件 ID、Stripe ID、签名或其他输入。"""
    assert response.headers["cache-control"] == NO_STORE
    for value in values:
        assert value not in response.text


def _record_ids(record: _Record) -> tuple[str, ...]:
    return (record.session_id, record.create_key, _intent_id(record), "someone@example.com")


# ---------------------------------------------------------------------------
# 处理
# ---------------------------------------------------------------------------


def test_completed_paid_event_marks_order_paid(
    app: FastAPI, db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """第 7 条「用原始请求体与 webhook 签名密钥校验 Stripe-Signature」「不需要会话与 CSRF」与第 4 条
    确认（经 handle_webhook_event）：签名正确的 checkout.session.completed 已付款事件不带 cookie 与
    CSRF 也被处理，回 200 {"received":true}，订单进入 demo_paid，事件记为 processed；验收：接口
    不写日志，响应不回显输入、不带任何 Stripe ID，带 no-store。"""
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, StripeResult(StripeOutcome.OK, _paid_session(record)))
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_paid")
    signature = _sign(payload)
    caplog.set_level(logging.DEBUG)

    response = _post(client, payload, signature)

    assert response.status_code == 200, response.text
    assert response.json() == {"received": True}
    _assert_no_echo(response, "evt_test_paid", signature, *_record_ids(record))
    assert not client.cookies
    assert _paths(gateway) == [("GET", f"{SESSIONS_PATH}/{record.session_id}")]
    assert _status(db, placed) == STATUS_PAID
    assert _stripe_events(db) == [("evt_test_paid", COMPLETED, EVENT_PROCESSED, NOW)]
    assert _api_logs(caplog) == []


def test_same_event_again_is_200_without_calling_stripe(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 7 条「已提交过的事件直接回 200」：同一事件第二次送达回 200 {"received":true}，不再调用
    Stripe，事件记录仍只有一条。"""
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, StripeResult(StripeOutcome.OK, _paid_session(record)))
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_twice")
    assert _post(client, payload).status_code == 200
    assert len(gateway.calls) == 1

    response = _post(client, payload)

    assert response.status_code == 200, response.text
    assert response.json() == {"received": True}
    _assert_no_echo(response, "evt_test_twice", *_record_ids(record))
    assert len(gateway.calls) == 1
    assert _status(db, placed) == STATUS_PAID
    assert [row[0] for row in _stripe_events(db)] == ["evt_test_twice"]


def test_other_event_type_is_200_and_recorded_as_ignored(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 7 条「其他类型回 200 不处理」：签名正确的其他类型事件回 200，记为 ignored，不调用
    Stripe。"""
    gateway = _inject(app)
    payload = _raw_event(
        "customer.created", {"id": "cus_test_1", "object": "customer"}, event_id="evt_test_other"
    )

    response = _post(client, payload)

    assert response.status_code == 200, response.text
    assert response.json() == {"received": True}
    _assert_no_echo(response, "evt_test_other", "cus_test_1")
    assert gateway.calls == []
    assert _stripe_events(db) == [("evt_test_other", "customer.created", EVENT_IGNORED, NOW)]


def test_refund_updated_event_updates_the_attempt(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 8 条「之后的退款事件（按 Stripe 退款 ID 或元数据里的记录 ID 关联）更新记录状态」「退款
    事件只作触发，收到后先查询再更新」：refund.updated 回 200，按查询到的 succeeded 更新尝试，事件
    记为 processed。"""
    attempt_id = _attempt(db)
    db.rollback()
    intent = select(StripeRefund.payment_intent_id).where(StripeRefund.id == attempt_id)
    pi = db.scalars(intent).one()
    db.rollback()
    queried = Refund(
        id=REFUND_ID,
        status="succeeded",
        amount=REFUND_AMOUNT,
        payment_intent=pi,
        attempt_id=str(attempt_id),
    )
    gateway = _inject(app, StripeResult(StripeOutcome.OK, queried))
    snapshot = {
        "id": REFUND_ID,
        "object": "refund",
        "status": "pending",
        "amount": REFUND_AMOUNT,
        "payment_intent": pi,
        "metadata": {"attempt_id": str(attempt_id)},
    }
    payload = _raw_event(REFUND_UPDATED, snapshot, event_id="evt_test_refund")

    response = _post(client, payload)

    assert response.status_code == 200, response.text
    assert response.json() == {"received": True}
    _assert_no_echo(response, "evt_test_refund", REFUND_ID, pi)
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    assert _attempt_status(db, attempt_id) == STRIPE_REFUND_SUCCEEDED
    assert _stripe_events(db) == [("evt_test_refund", REFUND_UPDATED, EVENT_PROCESSED, NOW)]


def test_stripe_unavailable_is_503_without_event_record(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """第 7 条「处理失败整个事务回滚、回 5xx，由 Stripe 重发」与 HANDOFF 0.55「处理中 Stripe 不可用
    ……整个事务回滚、不留事件记录、回 503」：查询时 Stripe 不可用回 503 stripe_unavailable，没有
    事件记录，订单仍待支付。"""
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, UNAVAILABLE)
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_unavailable")

    response = _post(client, payload)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "stripe_unavailable"}
    _assert_no_echo(response, "evt_test_unavailable", *_record_ids(record))
    assert len(gateway.calls) == 1
    assert _stripe_events(db) == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# 拒绝：签名、配置、请求体、事件内容与 livemode
# ---------------------------------------------------------------------------


def _missing(_payload: bytes) -> str | None:
    return None


def _wrong_secret(payload: bytes) -> str | None:
    return _sign(payload, secret="whsec_some_other_secret")


def _too_old(payload: bytes) -> str | None:
    return _sign(payload, at=NOW - timedelta(seconds=TOLERANCE_SECONDS + 1))


def _too_new(payload: bytes) -> str | None:
    return _sign(payload, at=NOW + timedelta(seconds=TOLERANCE_SECONDS + 1))


@pytest.mark.parametrize(
    "signer",
    [
        pytest.param(_missing, id="missing-header"),
        pytest.param(_wrong_secret, id="signature-mismatch"),
        pytest.param(_too_old, id="timestamp-too-old"),
        pytest.param(_too_new, id="timestamp-too-new"),
    ],
)
def test_bad_signature_is_400_without_processing(
    app: FastAPI, db: Session, client: TestClient, signer: Callable[[bytes], str | None]
) -> None:
    """第 7 条「用原始请求体与 webhook 签名密钥校验 Stripe-Signature（含时间容差），不通过回 400、
    不处理」：缺签名头、签名不符、时间戳超出 300 秒容差都回 400 signature_invalid，不写数据库、
    不调用 Stripe，订单不变。"""
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, StripeResult(StripeOutcome.OK, _paid_session(record)))
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_bad_signature")
    signature = signer(payload)

    response = _post(client, payload, signature)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "signature_invalid"}
    _assert_no_echo(response, "evt_test_bad_signature", *_record_ids(record))
    if signature is not None:
        assert signature not in response.text
    assert gateway.calls == []
    assert _stripe_events(db) == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize(
    ("overrides", "secret"),
    [
        pytest.param({"stripe_webhook_secret": ""}, "", id="webhook-secret-empty"),
        pytest.param(
            {"stripe_secret_key": "sk_live_placeholder"}, WEBHOOK_SECRET, id="live-secret-key"
        ),
    ],
)
def test_not_configured_is_503_without_verifying(
    engine: Engine, db: Session, overrides: dict[str, str], secret: str
) -> None:
    """第 2 条（签名密钥未配置或密钥不是测试密钥时不启用 Stripe）、第 1 条「服务端只接受 sk_test_ 或
    rk_test_ 开头的密钥」与 HANDOFF 0.55「凭据不可用（stripe_configured 为假，含签名密钥未配置）回
    503 stripe_unavailable 且不验签」：签名密钥为空（签名也按空密钥计算）或密钥为正式环境前缀时
    回 503，不写数据库、不调用 Stripe。"""
    app = _make_app(engine, make_settings(**overrides))
    client = TestClient(app, base_url="https://testserver")
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, StripeResult(StripeOutcome.OK, _paid_session(record)))
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_unconfigured")

    response = _post(client, payload, _sign(payload, secret=secret))

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "stripe_unavailable"}
    _assert_no_echo(response, "evt_test_unconfigured", *_record_ids(record))
    assert gateway.calls == []
    assert _stripe_events(db) == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(make_settings(), id="configured"),
        pytest.param(make_settings(stripe_webhook_secret=""), id="not-configured"),
    ],
)
def test_oversized_body_is_413_first(engine: Engine, db: Session, settings: Settings) -> None:
    """HANDOFF 0.55「webhook 的检查顺序为请求体超过 256 KB 回 413」；验收：超过
    MAX_WEBHOOK_BODY_BYTES 即 413 request body too large，先于配置检查与验签（签名不符、未配置时
    也是 413），不写数据库、不调用 Stripe。"""
    app = _make_app(engine, settings)
    client = TestClient(app, base_url="https://testserver")
    gateway = _inject(app)
    payload = b"x" * (MAX_WEBHOOK_BODY_BYTES + 1)

    response = _post(client, payload, "t=1,v1=00")

    assert webhook_api.MAX_WEBHOOK_BODY_BYTES == MAX_WEBHOOK_BODY_BYTES
    assert response.status_code == 413, response.text
    assert response.json() == {"detail": "request body too large"}
    _assert_no_echo(response, "xxxx")
    assert gateway.calls == []
    assert _stripe_events(db) == []


def test_body_at_limit_is_not_413(app: FastAPI, db: Session, client: TestClient) -> None:
    """HANDOFF 0.55「请求体超过 256 KB 回 413」：正好 256 KB 不算超过，继续验签（这里签名正确、
    内容不是 JSON，回 400 event_invalid）。"""
    gateway = _inject(app)
    payload = b" " * MAX_WEBHOOK_BODY_BYTES

    response = _post(client, payload)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "event_invalid"}
    assert response.headers["cache-control"] == NO_STORE
    assert gateway.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"not json at all", id="not-json"),
        pytest.param(b'["evt_test_list"]', id="not-object"),
        pytest.param(_raw_event(COMPLETED, {}, event_id="evt_test_empty"), id="session-missing"),
        pytest.param(
            _raw_event("customer.created", {}, event_id="evt_test_nolive", livemode=None),
            id="livemode-not-bool",
        ),
    ],
)
def test_signed_but_malformed_event_is_400(
    app: FastAPI, db: Session, client: TestClient, payload: bytes
) -> None:
    """HANDOFF 0.55「事件内容不合格回 400」：签名正确而内容不合格（parse_webhook_event 返回 None）
    时回 400 event_invalid，不写数据库、不调用 Stripe，不回显请求内容。"""
    gateway = _inject(app)

    response = _post(client, payload)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "event_invalid"}
    _assert_no_echo(response, "evt_test_list", "evt_test_empty", "evt_test_nolive", "not json")
    assert gateway.calls == []
    assert _stripe_events(db) == []


def test_livemode_event_is_400_without_event_record(
    app: FastAPI, db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「webhook 事件……的 livemode 为真即拒绝」与 HANDOFF 0.55「livemode 为真回 400 且不记
    事件并记 error 日志」：签名正确的 livemode 事件回 400 livemode_rejected，没有事件记录、不调用
    Stripe、订单不变；接口记一条只含类别的 error 日志 stripe_livemode。"""
    placed = _order(db)
    record = _checkout(db, placed)
    gateway = _inject(app, StripeResult(StripeOutcome.OK, _paid_session(record)))
    payload = _raw_event(COMPLETED, _raw_session(record), event_id="evt_test_live", livemode=True)
    caplog.set_level(logging.DEBUG)

    response = _post(client, payload)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "livemode_rejected"}
    _assert_no_echo(response, "evt_test_live", *_record_ids(record))
    assert gateway.calls == []
    assert _stripe_events(db) == []
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert [(r.levelno, r.getMessage()) for r in _api_logs(caplog)] == [
        (logging.ERROR, "stripe_livemode")
    ]

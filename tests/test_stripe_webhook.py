"""Stripe webhook 事件的解析与 Checkout 事件的同事务处理（app/services/stripe_webhook.py，
app/services/stripe_gateway.py 的 parse_webhook_event）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 1、3、4、5、7 条与
docs/HANDOFF.md 0.55 的落实写法。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的
设计原句；只出自 SHOP-TASK-127 验收标准的约定注明「验收：」。退款事件类型的处理由 SHOP-TASK-128
接入，这里不测。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；订单、订单行与
会话记录直接按模型写入（写法照 tests/test_stripe_overdue.py，在本文件里另写），库存按已为订单
预留 2 件写入。Stripe 一律用 FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数
会提交与回滚，测试数据都先提交。SQLite 不支持行锁，加锁顺序只按代码与 MySQL 行为审阅。
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
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Category,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ProductVariant,
    StripeCheckout,
    StripeEvent,
)
from app.models.order import (
    ACTOR_SYSTEM,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    EVENT_TYPE_MAX_LENGTH,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    STRIPE_ID_MAX_LENGTH,
)
from app.services import stripe_webhook
from app.services.payment import StripeEndResult, cancel_after_stripe_end
from app.services.stripe_confirm import ConfirmResult, confirm_checkout
from app.services.stripe_gateway import (
    CHECKOUT_EVENT_TYPES,
    REFUND_EVENT_TYPES,
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    Refund,
    StripeError,
    StripeOutcome,
    StripeResult,
    WebhookEvent,
    parse_webhook_event,
)
from app.services.stripe_webhook import WebhookResult, handle_webhook_event

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起会话。
CREATED = datetime(2026, 10, 18, 2, 0)
ORDER_EXPIRES = CREATED + timedelta(minutes=15)
STARTED = CREATED + timedelta(minutes=1)
NOW = CREATED + timedelta(minutes=5)
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)

# 订单行 1 行 2 件、单价 3000 仙；运费 800 仙。游客订单应付 6800 仙。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY

FINGERPRINT = "0f" * 32
SESSIONS_PATH = "/v1/checkout/sessions"
SNAPSHOT = '[["mode","payment"],["metadata[order_id]","1"]]'

COMPLETED = "checkout.session.completed"
ASYNC_SUCCEEDED = "checkout.session.async_payment_succeeded"
ASYNC_FAILED = "checkout.session.async_payment_failed"
EXPIRED_EVENT = "checkout.session.expired"

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
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
def variant(db: Session) -> int:
    """一个规格：每日初始库存 10，已为一张订单预留 2 件；写完即提交，返回规格 ID。"""
    category = _add(db, Category(slug="goods", name_en="Goods", is_active=True))
    product = Product(
        category_id=category.id,
        slug="tee",
        name_en="Tee",
        description_en="A tee.",
        is_active=True,
    )
    _add(db, product)
    row = ProductVariant(
        product_id=product.id,
        sku="TEE",
        price_sen=UNIT_PRICE,
        daily_initial_stock=INITIAL_STOCK,
        available_stock=RESERVED_STOCK,
        is_active=True,
    )
    _add(db, row)
    db.commit()
    return row.id


@dataclass(frozen=True)
class _Placed:
    order_id: int
    variant_id: int


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _guest_order(db: Session, variant_id: int) -> _Placed:
    """待支付的游客订单（应付 6800 仙，没有券与积分）；写完即提交。"""
    n = next(_SEQUENCE)
    order = Order(
        order_number=f"{n:016d}",
        status=STATUS_AWAITING_PAYMENT,
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
        created_at=CREATED,
        payment_expires_at=ORDER_EXPIRES,
        paid_at=None,
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
    return _Placed(order.id, variant_id)


def _checkout(
    db: Session,
    order_id: int,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起留下的），提交后返回记录 ID。creating 没有
    会话 ID；complete 的完成时间为 STARTED。"""
    n = next(_SEQUENCE)
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "create_params": SNAPSHOT,
        "expires_at": STRIPE_EXPIRES,
        "created_at": STARTED,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    if status != CHECKOUT_CREATING:
        fields["checkout_session_id"] = f"cs_test_{n}"
        fields["payment_status"] = payment_status
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status == CHECKOUT_COMPLETE:
        fields["completed_at"] = STARTED
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return record.id


def _record(db: Session, checkout_id: int) -> StripeCheckout:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.id == checkout_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _record_state(db: Session, checkout_id: int) -> tuple[Any, ...]:
    """会话记录中会被确认或取消改动的列，用来断言记录没改。"""
    record = _record(db, checkout_id)
    return (
        record.status,
        record.active_order_id,
        record.checkout_session_id,
        record.payment_status,
        record.payment_intent_id,
        record.payment_method_type,
        record.completed_at,
        record.confirmed_at,
    )


def _session(
    db: Session, checkout_id: int, status: str, *, session_id: str | None = None, **overrides: Any
) -> CheckoutSession:
    """与记录、订单相符的会话对象；已付款时 payment_intent 已展开、方式为 card。"""
    record = _record(db, checkout_id)
    paid = overrides.get("payment_status") == PAYMENT_PAID
    fields: dict[str, Any] = {
        "id": session_id or record.checkout_session_id or f"cs_test_for_{checkout_id}",
        "status": status,
        "payment_status": PAYMENT_UNPAID,
        "livemode": False,
        "currency": "myr",
        "amount_total": TOTAL,
        "client_reference_id": str(record.order_id),
        "order_id": str(record.order_id),
        "create_key": record.idempotency_key,
        "url": None,
        "payment_intent": None,
    }
    if paid:
        fields["payment_intent"] = PaymentIntentInfo(
            id=f"pi_test_{checkout_id}",
            status="succeeded",
            livemode=False,
            payment_method_type="card",
        )
    fields.update(overrides)
    db.rollback()
    return CheckoutSession(**fields)


def _paid(db: Session, checkout_id: int, **overrides: Any) -> CheckoutSession:
    return _session(db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID, **overrides)


def _intent(checkout_id: int, status: str) -> PaymentIntentInfo:
    """未付款会话的 PaymentIntent（没有成功的 Charge，付款方式类型为空）。"""
    return PaymentIntentInfo(id=f"pi_test_{checkout_id}", status=status, livemode=False)


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _webhook(
    event_type: str, session: CheckoutSession | None, *, event_id: str | None = None
) -> WebhookEvent:
    """签名校验通过、已解析的测试环境事件；事件对象就是给出的会话（事件只作触发）。"""
    return WebhookEvent(
        id=event_id or f"evt_test_{next(_SEQUENCE)}",
        type=event_type,
        livemode=False,
        session=session,
    )


def _handle(db: Session, gateway: Any, webhook: WebhookEvent) -> WebhookResult:
    return handle_webhook_event(db, gateway, webhook, NOW)


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _retrieve_path(session_id: str) -> tuple[str, str]:
    return ("GET", f"{SESSIONS_PATH}/{session_id}")


def _status(db: Session, order_id: int) -> str:
    db.rollback()
    return db.scalars(select(Order.status).where(Order.id == order_id)).one()


def _stock(db: Session, variant_id: int) -> int:
    db.rollback()
    stmt = select(ProductVariant.available_stock).where(ProductVariant.id == variant_id)
    return db.scalars(stmt).one()


def _order_events(db: Session, order_id: int) -> list[tuple[str | None, str, str]]:
    db.rollback()
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _order_state(db: Session, placed: _Placed) -> dict[str, Any]:
    """订单状态、库存与订单事件，用来断言订单一侧什么都没改。"""
    return {
        "status": _status(db, placed.order_id),
        "stock": _stock(db, placed.variant_id),
        "events": _order_events(db, placed.order_id),
    }


def _stripe_events(db: Session) -> list[tuple[str, str, str, datetime]]:
    db.rollback()
    columns = (StripeEvent.event_id, StripeEvent.event_type, StripeEvent.outcome)
    stmt = select(*columns, StripeEvent.processed_at).order_by(StripeEvent.id)
    rows = [tuple(row) for row in db.execute(stmt)]
    db.rollback()
    return rows


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    """本站服务记下的 error 级日志（告警邮件接入前的告警）。"""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.startswith("app.services.")
    ]


# ---------------------------------------------------------------------------
# 事件解析
# ---------------------------------------------------------------------------


def _raw_session(**overrides: Any) -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": "cs_test_parse",
        "object": "checkout.session",
        "status": "complete",
        "payment_status": "paid",
        "livemode": False,
        "currency": "myr",
        "amount_total": TOTAL,
        "client_reference_id": "7",
        "metadata": {"order_id": "7", "create_key": "create-key-parse"},
        "customer_details": {"email": "someone@example.com"},
        "payment_intent": "pi_test_parse",
    }
    session.update(overrides)
    return session


def _raw_refund(**overrides: Any) -> dict[str, Any]:
    refund: dict[str, Any] = {
        "id": "re_test_parse",
        "object": "refund",
        "status": "succeeded",
        "amount": 6800,
        "payment_intent": "pi_test_parse",
        "metadata": {"attempt_id": "3"},
    }
    refund.update(overrides)
    return refund


def _raw_event(event_type: str = COMPLETED, obj: Any = None, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "evt_test_parse",
        "object": "event",
        "type": event_type,
        "livemode": False,
        "created": 1792288800,
        "data": {"object": _raw_session() if obj is None else obj},
    }
    data.update(overrides)
    return data


def _body(data: Any) -> bytes:
    return json.dumps(data).encode("utf-8")


def test_event_type_constants_follow_handoff() -> None:
    """HANDOFF 0.55（1）：「webhook 处理 checkout.session.completed、
    checkout.session.async_payment_succeeded、checkout.session.async_payment_failed 与
    refund.created、refund.updated、refund.failed」。"""
    assert CHECKOUT_EVENT_TYPES == (COMPLETED, ASYNC_SUCCEEDED, ASYNC_FAILED)
    assert REFUND_EVENT_TYPES == ("refund.created", "refund.updated", "refund.failed")


@pytest.mark.parametrize("event_type", [COMPLETED, ASYNC_SUCCEEDED, ASYNC_FAILED])
def test_checkout_event_parses_the_session_fields(event_type: str) -> None:
    """第 7 条「只处理第 4、5 条的 Checkout 事件」与第 10 条「本站不读取、不保存 Stripe 返回的
    客户资料」：Checkout 事件按白名单取出会话的对账字段与元数据。"""
    parsed = parse_webhook_event(_body(_raw_event(event_type)))

    assert parsed is not None
    assert (parsed.id, parsed.type, parsed.livemode, parsed.refund) == (
        "evt_test_parse",
        event_type,
        False,
        None,
    )
    assert parsed.session == CheckoutSession(
        id="cs_test_parse",
        status="complete",
        payment_status="paid",
        livemode=False,
        currency="myr",
        amount_total=TOTAL,
        client_reference_id="7",
        order_id="7",
        create_key="create-key-parse",
        payment_intent=PaymentIntentInfo("pi_test_parse"),
    )


@pytest.mark.parametrize("event_type", ["refund.created", "refund.updated", "refund.failed"])
def test_refund_event_parses_the_refund_fields(event_type: str) -> None:
    """第 7 条「只处理……Stripe 退款状态事件」与 HANDOFF 0.55「退款事件的 data.object 均为退款
    对象」：退款事件取出退款的对账字段。"""
    parsed = parse_webhook_event(_body(_raw_event(event_type, _raw_refund())))

    assert parsed is not None
    assert parsed.session is None
    assert parsed.refund == Refund(
        id="re_test_parse",
        status="succeeded",
        amount=6800,
        payment_intent="pi_test_parse",
        attempt_id="3",
    )


@pytest.mark.parametrize(
    ("event_type", "obj"),
    [
        (EXPIRED_EVENT, _raw_session(status="expired", payment_status="unpaid")),
        ("payment_intent.succeeded", {"id": "pi_test_parse", "object": "payment_intent"}),
        ("charge.refund.updated", _raw_refund()),
        ("customer.created", {}),
    ],
)
def test_other_event_types_carry_no_object(event_type: str, obj: dict[str, Any]) -> None:
    """第 7 条「其他类型回 200 不处理」与 HANDOFF 0.55「checkout.session.expired、其他类型……
    记为 ignored」：其他类型（含 checkout.session.expired）照常解析，会话与退款都为空。"""
    parsed = parse_webhook_event(_body(_raw_event(event_type, obj)))

    assert parsed == WebhookEvent("evt_test_parse", event_type, False)


def test_livemode_event_still_parses() -> None:
    """第 1 条「webhook 事件……的 `livemode` 为真即拒绝」；验收：解析只要求 livemode
    是布尔值，为真照常返回，由接口拒绝。"""
    parsed = parse_webhook_event(_body(_raw_event(livemode=True)))

    assert parsed is not None
    assert parsed.livemode is True


_INVALID_EVENTS: dict[str, Any] = {
    "not_json": b"not json",
    "not_utf8": b"\xff\xfe",
    "array": b"[]",
    "string": b'"event"',
    "object_not_event": _body(_raw_event(object="list")),
    "object_missing": _body({k: v for k, v in _raw_event().items() if k != "object"}),
    "id_missing": _body({k: v for k, v in _raw_event().items() if k != "id"}),
    "id_empty": _body(_raw_event(id="")),
    "id_not_string": _body(_raw_event(id=123)),
    "id_too_long": _body(_raw_event(id="e" * (STRIPE_ID_MAX_LENGTH + 1))),
    "type_empty": _body(_raw_event(type="")),
    "type_not_string": _body(_raw_event(type=None)),
    "type_too_long": _body(_raw_event(type="t" * (EVENT_TYPE_MAX_LENGTH + 1))),
    "livemode_missing": _body({k: v for k, v in _raw_event().items() if k != "livemode"}),
    "livemode_string": _body(_raw_event(livemode="false")),
    "livemode_int": _body(_raw_event(livemode=0)),
    "data_missing": _body({k: v for k, v in _raw_event().items() if k != "data"}),
    "data_not_object": _body(_raw_event(data=[])),
    "data_object_missing": _body(_raw_event(data={})),
    "data_object_not_object": _body(_raw_event(data={"object": "cs_test_parse"})),
    "checkout_with_refund_object": _body(_raw_event(COMPLETED, _raw_refund())),
    "checkout_without_session_id": _body(_raw_event(ASYNC_FAILED, _raw_session(id=""))),
    "refund_with_session_object": _body(_raw_event("refund.updated", _raw_session())),
    "refund_without_id": _body(_raw_event("refund.failed", _raw_refund(id=None))),
}


@pytest.mark.parametrize("payload", list(_INVALID_EVENTS.values()), ids=list(_INVALID_EVENTS))
def test_invalid_events_return_none_without_logging(
    payload: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """第 7 条「不存、不记录事件原文」；验收：不是 JSON 对象、object 不是 event、id 或
    type 不合格或超长、livemode 不是布尔值、data.object 不是对象，或 Checkout 与退款
    事件取不到对象时返回 None，不抛异常、不写日志。"""
    caplog.set_level(logging.DEBUG)

    assert parse_webhook_event(payload) is None
    assert caplog.records == []


def test_non_bytes_payload_returns_none() -> None:
    """验收：入参是原始请求体 bytes，其他类型返回 None、不抛异常。"""
    text_payload: Any = _body(_raw_event()).decode("utf-8")

    assert parse_webhook_event(text_payload) is None


def test_id_and_type_at_the_column_width_are_accepted() -> None:
    """数据模型「`StripeEvent`：Stripe 事件 ID（唯一）、类型」；验收：长度恰为列宽的
    id 与 type 照常解析（超过才不合格）。"""
    event_id = "e" * STRIPE_ID_MAX_LENGTH
    event_type = "t" * EVENT_TYPE_MAX_LENGTH
    parsed = parse_webhook_event(_body(_raw_event(id=event_id, type=event_type)))

    assert parsed == WebhookEvent(event_id, event_type, False)


# ---------------------------------------------------------------------------
# Checkout 事件：确认
# ---------------------------------------------------------------------------


def test_completed_and_paid_confirms_in_the_same_transaction(db: Session, variant: int) -> None:
    """第 4 条「订单只在服务端确认 Stripe 付款成功后才进入 `demo_paid`」与第 7 条「事件 ID 的
    去重记录与它引起的订单……改动在同一个数据库事务里提交」：completed 且查询到已付款时
    订单进入 demo_paid，事件记录为 processed；HANDOFF 0.55「事件只作触发，按向 Stripe
    查询到的对象处理」。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    gateway = FakeStripeGateway(_ok(paid))
    webhook = _webhook(COMPLETED, paid, event_id="evt_test_paid")

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    assert _paths(gateway) == [_retrieve_path(paid.id)]
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _order_events(db, placed.order_id) == [
        (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)
    ]
    record = _record(db, checkout_id)
    assert (record.status, record.payment_status, record.confirmed_at) == (
        CHECKOUT_COMPLETE,
        PAYMENT_PAID,
        NOW,
    )
    assert record.payment_method_type == "card"
    assert _stripe_events(db) == [("evt_test_paid", COMPLETED, EVENT_PROCESSED, NOW)]


def test_same_event_again_is_duplicate_without_calling_stripe(db: Session, variant: int) -> None:
    """第 7 条「已提交过的事件直接回 200」：同一事件再来为 duplicate，不调用 Stripe、不改任何
    东西。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    webhook = _webhook(COMPLETED, paid)
    assert _handle(db, FakeStripeGateway(_ok(paid)), webhook) is WebhookResult.PROCESSED
    before = (_order_state(db, placed), _record_state(db, checkout_id), _stripe_events(db))
    gateway = FakeStripeGateway(_ok(paid))

    assert _handle(db, gateway, webhook) is WebhookResult.DUPLICATE

    assert gateway.calls == []
    after = (_order_state(db, placed), _record_state(db, checkout_id), _stripe_events(db))
    assert after == before


def test_creating_record_is_found_by_create_key_and_order_id(db: Session, variant: int) -> None:
    """第 3 条末句「万一有本站没记下会话 ID 的会话被付款，其 webhook 按元数据里的创建幂等键
    与订单 ID 对上这条记录」与第 4 条：creating 记录按创建幂等键与订单 ID 找回并确认，
    补上会话 ID。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    paid = _paid(db, checkout_id, session_id="cs_test_unrecorded")
    gateway = FakeStripeGateway(_ok(paid))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.PROCESSED

    assert _paths(gateway) == [_retrieve_path("cs_test_unrecorded")]
    assert _status(db, placed.order_id) == STATUS_PAID
    record = _record(db, checkout_id)
    assert (record.status, record.checkout_session_id) == (CHECKOUT_COMPLETE, "cs_test_unrecorded")
    assert record.confirmed_at == NOW
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


def test_expired_record_is_found_by_create_key_and_revived(db: Session, variant: int) -> None:
    """第 3 条末句「按元数据里的创建幂等键与订单 ID 对上这条记录，照第 4、6 条处理」；验收：
    找回含 expired 的记录（对账记为 expired、没有会话 ID），订单仍待支付时改回 complete
    并确认。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_EXPIRED)
    stmt = (
        update(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .values(checkout_session_id=None, payment_status=None)
    )
    db.execute(stmt)
    db.commit()
    paid = _paid(db, checkout_id, session_id="cs_test_after_reconcile")
    gateway = FakeStripeGateway(_ok(paid))

    assert _handle(db, gateway, _webhook(ASYNC_SUCCEEDED, paid)) is WebhookResult.PROCESSED

    assert _status(db, placed.order_id) == STATUS_PAID
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_COMPLETE, placed.order_id)


# ---------------------------------------------------------------------------
# Checkout 事件：不处理
# ---------------------------------------------------------------------------


def test_event_without_a_site_record_is_ignored(db: Session, variant: int) -> None:
    """HANDOFF 0.55「找不到本站记录的事件记为 ignored、回 200」：会话 ID 与创建幂等键都对不上
    时为 ignored，不调用 Stripe，只写事件记录。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    stranger = _paid(db, checkout_id, session_id="cs_test_stranger", create_key="other-key")
    gateway = FakeStripeGateway(_ok(stranger))
    webhook = _webhook(COMPLETED, stranger, event_id="evt_test_stranger")
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    assert _handle(db, gateway, webhook) is WebhookResult.IGNORED

    assert gateway.calls == []
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert _stripe_events(db) == [("evt_test_stranger", COMPLETED, EVENT_IGNORED, NOW)]


@pytest.mark.parametrize("order_ref", [None, "", "x1", "-1", "1.0", "١", "9" * 19])
def test_metadata_order_id_must_be_decimal_digits(
    db: Session, variant: int, order_ref: str | None
) -> None:
    """第 3 条末句「按元数据里的创建幂等键与订单 ID 对上这条记录」；验收：订单 ID 不是
    十进制数字时不按创建幂等键找回，为 ignored、不调用 Stripe。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    paid = _paid(db, checkout_id, session_id="cs_test_bad_ref", order_id=order_ref)
    gateway = FakeStripeGateway(_ok(paid))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.IGNORED

    assert gateway.calls == []
    assert _record(db, checkout_id).status == CHECKOUT_CREATING


def test_create_key_must_belong_to_the_metadata_order(db: Session, variant: int) -> None:
    """第 3 条末句「按元数据里的创建幂等键与订单 ID 对上这条记录」：创建幂等键对上而订单 ID 是
    另一张订单时不算找到，为 ignored。"""
    placed = _guest_order(db, variant)
    other = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    paid = _paid(db, checkout_id, session_id="cs_test_wrong_order", order_id=str(other.order_id))
    gateway = FakeStripeGateway(_ok(paid))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.IGNORED

    assert gateway.calls == []


@pytest.mark.parametrize(
    "event_type", [EXPIRED_EVENT, "payment_intent.succeeded", "customer.created"]
)
def test_expired_and_other_types_are_ignored(db: Session, variant: int, event_type: str) -> None:
    """第 7 条「其他类型回 200 不处理」与 HANDOFF 0.55「`checkout.session.expired`、其他类型……
    记为 ignored、回 200（open 会话的过期由定时任务查询处理）」：不调用 Stripe、不改记录
    与订单。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    gateway = FakeStripeGateway()
    webhook = _webhook(event_type, None, event_id="evt_test_other")
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    assert _handle(db, gateway, webhook) is WebhookResult.IGNORED

    assert gateway.calls == []
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert _stripe_events(db) == [("evt_test_other", event_type, EVENT_IGNORED, NOW)]


def test_ignored_event_again_is_duplicate(db: Session) -> None:
    """第 7 条「已提交过的事件直接回 200」：记为 ignored 的事件再来也为 duplicate，
    不另写记录。"""
    webhook = _webhook(EXPIRED_EVENT, None)
    assert _handle(db, FakeStripeGateway(), webhook) is WebhookResult.IGNORED

    assert _handle(db, FakeStripeGateway(), webhook) is WebhookResult.DUPLICATE

    assert len(_stripe_events(db)) == 1


# ---------------------------------------------------------------------------
# Checkout 事件：付款失败与付款未定
# ---------------------------------------------------------------------------


def test_async_payment_failed_cancels_by_the_existing_rule(db: Session, variant: int) -> None:
    """第 5 条「async_payment_failed 后按现有规则取消」与「所有取消入口……用同一规则」：查询到
    complete 而 PaymentIntent 为 canceled 时记录记为 expired、订单以 system 取消并释放库存，与
    事件记录同一事务提交。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    failed = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_intent=_intent(checkout_id, "canceled")
    )
    gateway = FakeStripeGateway(_ok(failed))
    webhook = _webhook(ASYNC_FAILED, failed, event_id="evt_test_failed")

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    assert _paths(gateway) == [_retrieve_path(failed.id)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _stock(db, variant) == INITIAL_STOCK
    assert _order_events(db, placed.order_id) == [
        (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)
    ]
    assert _stripe_events(db) == [("evt_test_failed", ASYNC_FAILED, EVENT_PROCESSED, NOW)]


def test_complete_with_undecided_payment_only_records_the_session(
    db: Session, variant: int
) -> None:
    """第 5 条「complete 而付款未定……订单继续待支付、库存继续预留」，等
    async_payment_succeeded 确认：open 记录只改为 complete，订单仍待支付，事件记录为
    processed。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    pending = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_intent=_intent(checkout_id, "processing")
    )
    # confirm_checkout 见付款方式类型为空，再查询一次。
    gateway = FakeStripeGateway(_ok(pending), _ok(pending))

    assert _handle(db, gateway, _webhook(COMPLETED, pending)) is WebhookResult.PROCESSED

    record = _record(db, checkout_id)
    assert (record.status, record.payment_status, record.confirmed_at) == (
        CHECKOUT_COMPLETE,
        PAYMENT_UNPAID,
        None,
    )
    assert record.completed_at == NOW
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT
    assert _stock(db, variant) == RESERVED_STOCK
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


def test_open_session_changes_nothing_and_is_processed(db: Session, variant: int) -> None:
    """HANDOFF 0.55「事件只作触发，按向 Stripe 查询到的对象处理」；验收：查询到的会话
    仍为 open 时不改记录与订单，记为 processed。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    still_open = _session(db, checkout_id, CHECKOUT_OPEN)
    gateway = FakeStripeGateway(_ok(still_open))
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    assert _handle(db, gateway, _webhook(COMPLETED, still_open)) is WebhookResult.PROCESSED

    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


def test_failed_event_but_paid_session_confirms(db: Session, variant: int) -> None:
    """HANDOFF 0.55「事件只作触发，按向 Stripe 查询到的对象处理」：事件为 async_payment_failed
    而查询到已付款时按查询结果确认（事件可能乱序），不取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    stale = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_intent=_intent(checkout_id, "canceled")
    )
    gateway = FakeStripeGateway(_ok(_paid(db, checkout_id)))

    assert _handle(db, gateway, _webhook(ASYNC_FAILED, stale)) is WebhookResult.PROCESSED

    assert _status(db, placed.order_id) == STATUS_PAID
    assert _record(db, checkout_id).status == CHECKOUT_COMPLETE


def test_succeeded_event_but_failed_payment_cancels(db: Session, variant: int) -> None:
    """HANDOFF 0.55「事件只作触发，按向 Stripe 查询到的对象处理」与第 5 条：事件为
    async_payment_succeeded 而查询到 PaymentIntent 为 requires_payment_method 时按查询结果
    取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    failed = _session(
        db,
        checkout_id,
        CHECKOUT_COMPLETE,
        payment_intent=_intent(checkout_id, "requires_payment_method"),
    )
    gateway = FakeStripeGateway(_ok(failed))
    webhook = _webhook(ASYNC_SUCCEEDED, _paid(db, checkout_id))

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED


# ---------------------------------------------------------------------------
# Checkout 事件：无法完成
# ---------------------------------------------------------------------------


def test_stripe_unavailable_leaves_no_event_record(db: Session, variant: int) -> None:
    """第 7 条「处理失败整个事务回滚、回 5xx」与 HANDOFF 0.55「处理中 Stripe 不可用……不留事件
    记录、回 503，由 Stripe 重发」：查询不可用为 unavailable，订单与记录不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    gateway = FakeStripeGateway(UNAVAILABLE)
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.UNAVAILABLE

    assert _paths(gateway) == [_retrieve_path(paid.id)]
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert _stripe_events(db) == []


def test_unavailable_event_is_handled_when_resent(db: Session, variant: int) -> None:
    """第 7 条「处理失败整个事务回滚」后 Stripe 重发：同一事件再来时照常处理并确认。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    webhook = _webhook(COMPLETED, paid)
    assert _handle(db, FakeStripeGateway(UNAVAILABLE), webhook) is WebhookResult.UNAVAILABLE

    assert _handle(db, FakeStripeGateway(_ok(paid)), webhook) is WebhookResult.PROCESSED

    assert _status(db, placed.order_id) == STATUS_PAID


@pytest.mark.parametrize(
    ("result_name", "category"),
    [("live", "stripe_livemode"), ("rejected", "stripe_confirm_rejected")],
)
def test_live_or_rejected_query_is_unavailable_and_alerts(
    db: Session,
    variant: int,
    caplog: pytest.LogCaptureFixture,
    result_name: str,
    category: str,
) -> None:
    """第 1 条「会话或付款的 `livemode` 为真即拒绝」与第 7 条「处理失败整个事务回滚、回 5xx」，
    HANDOFF 0.55「已找到本站记录、但向 Stripe 查询到的会话为正式环境或查询被拒……不留
    事件记录、回 503」：为 unavailable，订单与记录不变，记一条只含记录 ID 的 error 日志。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    if result_name == "live":
        result = StripeResult(StripeOutcome.LIVE, _paid(db, checkout_id, livemode=True))
    else:
        error = StripeError(http_status=404, type="invalid_request_error", code="resource_missing")
        result = StripeResult(StripeOutcome.REJECTED, error=error)
    gateway = FakeStripeGateway(result)
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.UNAVAILABLE

    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert _stripe_events(db) == []
    assert _alerts(caplog) == [f"{category} checkout={checkout_id}"]


def test_confirmation_unavailable_leaves_no_event_record(db: Session, variant: int) -> None:
    """第 7 条「处理失败整个事务回滚」；验收：confirm_checkout 返回 unavailable（核对
    不符）时为 unavailable，不留事件记录，订单不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    # 查询到的会话 ID 与记录不同：确认核对身份不符。
    other = _paid(db, checkout_id, id="cs_test_someone_else")
    paid = _paid(db, checkout_id)
    gateway = FakeStripeGateway(_ok(other))

    assert _handle(db, gateway, _webhook(COMPLETED, paid)) is WebhookResult.UNAVAILABLE

    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT
    assert _stripe_events(db) == []


# ---------------------------------------------------------------------------
# 同一事务：事件记录与改动
# ---------------------------------------------------------------------------


def test_concurrent_same_event_is_duplicate_and_rolls_back_the_change(
    db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 7 条「事件 ID 的去重记录与它引起的订单、积分或退款改动在同一个数据库事务里提交」与
    HANDOFF 0.55「事件 ID 撞唯一约束时以专门的异常返回『已处理过』，不被确认函数的并发
    重试吞掉」：普通读没查到而写入时撞上同一事件 ID，返回 duplicate，订单与会话记录都未改。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    db.add(
        StripeEvent(
            event_id="evt_test_race",
            event_type=COMPLETED,
            outcome=EVENT_PROCESSED,
            processed_at=STARTED,
        )
    )
    db.commit()
    monkeypatch.setattr(stripe_webhook, "_already_recorded", lambda _db, _event_id: False)
    gateway = FakeStripeGateway(_ok(paid))
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    result = _handle(db, gateway, _webhook(COMPLETED, paid, event_id="evt_test_race"))

    assert result is WebhookResult.DUPLICATE
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before
    assert _stripe_events(db) == [("evt_test_race", COMPLETED, EVENT_PROCESSED, STARTED)]


def test_concurrent_same_event_on_cancel_is_duplicate(
    db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 7 条「事件 ID 的去重记录与它引起的订单……改动在同一个数据库事务里提交」：取消
    一路写事件记录撞上同一事件 ID 时返回 duplicate，订单不取消、记录不改。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    failed = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_intent=_intent(checkout_id, "canceled")
    )
    db.add(
        StripeEvent(
            event_id="evt_test_race_cancel",
            event_type=ASYNC_FAILED,
            outcome=EVENT_PROCESSED,
            processed_at=STARTED,
        )
    )
    db.commit()
    monkeypatch.setattr(stripe_webhook, "_already_recorded", lambda _db, _event_id: False)
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    webhook = _webhook(ASYNC_FAILED, failed, event_id="evt_test_race_cancel")
    result = _handle(db, FakeStripeGateway(_ok(failed)), webhook)

    assert result is WebhookResult.DUPLICATE
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before


class _Boom(Exception):
    pass


class _FailingRecorder:
    """在提交之前被调用、随即抛异常的 record_event。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, db: Session) -> None:
        self.calls += 1
        raise _Boom


def test_confirm_calls_record_event_before_its_commit(db: Session, variant: int) -> None:
    """第 7 条「事件 ID 的去重记录与它引起的订单……改动在同一个数据库事务里提交」；验收：
    confirm_checkout 在提交之前调用 record_event，它抛出的异常原样传出，订单与记录都不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    recorder = _FailingRecorder()
    gateway = FakeStripeGateway()
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    with pytest.raises(_Boom):
        confirm_checkout(db, gateway, checkout_id, paid, NOW, record_event=recorder)

    assert recorder.calls == 1
    assert gateway.calls == []
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before


def test_cancel_calls_record_event_before_its_commit(db: Session, variant: int) -> None:
    """第 7 条「事件 ID 的去重记录与它引起的订单……改动在同一个数据库事务里提交」；验收：
    cancel_after_stripe_end 在提交之前调用 record_event，它抛出的异常原样传出，订单与记录都
    不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    recorder = _FailingRecorder()
    before = (_order_state(db, placed), _record_state(db, checkout_id))

    with pytest.raises(_Boom):
        cancel_after_stripe_end(
            db,
            placed.order_id,
            ACTOR_SYSTEM,
            NOW,
            checkout_id=checkout_id,
            seen_status=CHECKOUT_COMPLETE,
            record_event=recorder,
        )

    assert recorder.calls == 1
    db.rollback()
    assert (_order_state(db, placed), _record_state(db, checkout_id)) == before


def test_record_event_is_not_called_without_a_write(db: Session, variant: int) -> None:
    """第 7 条「业务动作另按对象去重」；验收：不写入的路径（已确认过的重复确认、记录在
    查询之后被改过）不调用 record_event。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    gateway = FakeStripeGateway()
    assert confirm_checkout(db, gateway, checkout_id, paid, NOW) is ConfirmResult.PAID
    recorder = _FailingRecorder()

    again = confirm_checkout(db, gateway, checkout_id, paid, NOW, record_event=recorder)
    changed = cancel_after_stripe_end(
        db,
        placed.order_id,
        ACTOR_SYSTEM,
        NOW,
        checkout_id=checkout_id,
        seen_status=CHECKOUT_OPEN,
        record_event=recorder,
    )

    assert again is ConfirmResult.PAID
    assert changed is StripeEndResult.CHANGED
    assert recorder.calls == 0
    assert gateway.calls == []


def test_already_paid_confirmation_writes_its_own_event_record(db: Session, variant: int) -> None:
    """第 4 条「同一会话……的重复确认（订单已由它付款）直接返回原结果」与第 7 条：另一
    事件 ID 再报同一笔已确认的付款时不再迁移，单独写一条 processed 的事件记录。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id)
    paid = _paid(db, checkout_id)
    first = _webhook(COMPLETED, paid)
    assert _handle(db, FakeStripeGateway(_ok(paid)), first) is WebhookResult.PROCESSED
    second = _webhook(ASYNC_SUCCEEDED, paid, event_id="evt_test_second")

    assert _handle(db, FakeStripeGateway(_ok(paid)), second) is WebhookResult.PROCESSED

    assert _order_events(db, placed.order_id) == [
        (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)
    ]
    assert _stripe_events(db)[-1] == ("evt_test_second", ASYNC_SUCCEEDED, EVENT_PROCESSED, NOW)


# ---------------------------------------------------------------------------
# 入参
# ---------------------------------------------------------------------------


def test_livemode_event_raises_before_touching_the_database(db: Session) -> None:
    """第 1 条「webhook 事件……的 `livemode` 为真即拒绝」；验收：livemode 为真抛
    ValueError（由接口先拒绝），不调用 Stripe、不写事件记录。"""
    webhook = WebhookEvent("evt_test_live", COMPLETED, True, CheckoutSession("cs_live_1"))
    gateway = FakeStripeGateway()

    with pytest.raises(ValueError):
        _handle(db, gateway, webhook)

    assert gateway.calls == []
    assert _stripe_events(db) == []


def test_aware_now_raises(db: Session) -> None:
    """库里的时间一律是不带时区的 UTC；验收：带时区的 now 抛 ValueError，先于访问数据库。"""
    webhook = _webhook(EXPIRED_EVENT, None)

    with pytest.raises(ValueError):
        handle_webhook_event(db, FakeStripeGateway(), webhook, NOW.replace(tzinfo=UTC))

    assert _stripe_events(db) == []

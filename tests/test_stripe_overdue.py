"""有 Stripe 记录的订单的定时处理（app/services/stripe_overdue.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 3、4、5 条。每条测试
（参数化的测试是每个用例）的文档字符串写明它守住的设计原句；只出自 SHOP-TASK-116 验收
标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；订单、
订单行、券与积分的预占和会话记录直接按模型写入，库存按已为订单预留 2 件写入。Stripe
一律用 FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe），未配置的情形用未配置的
StripeHttpGateway（它不调用传输函数）。被测函数会提交与回滚，测试数据都先提交。与
webhook 并发的情形，以第二个数据库会话在 Stripe 调用发生时写入并提交来模拟。SQLite
不支持行锁，加锁顺序只按代码与 MySQL 行为审阅，这里不验证。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Category,
    Coupon,
    CouponUse,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    PointEntry,
    Product,
    ProductVariant,
    StripeCheckout,
    StripeRefund,
)
from app.models.coupon import TYPE_FIXED, USE_RELEASED, USE_RESERVED
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_SYSTEM,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.points import KIND_REDEEM, KIND_RELEASE
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
)
from app.services import stripe_overdue
from app.services.coupons import reserve_coupon
from app.services.points import earn_points, reserve_points
from app.services.stripe_checkout import deserialize_form
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    HttpRequest,
    HttpResponse,
    PaymentIntentInfo,
    StripeError,
    StripeHttpGateway,
    StripeOutcome,
    StripeResult,
)

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起会话。
CREATED = datetime(2026, 10, 18, 2, 0)
ORDER_EXPIRES = CREATED + timedelta(minutes=15)
STARTED = CREATED + timedelta(minutes=1)
NOW = CREATED + timedelta(minutes=5)
# 会话的 Stripe 到期时间：发起后 31 分钟。
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)
# 已过本地 15 分钟支付到期时间，仍在 Stripe 到期时间之内。
AFTER_ORDER_EXPIRY = ORDER_EXPIRES + timedelta(minutes=10)
# 已过 Stripe 到期时间。
AFTER_STRIPE_EXPIRY = STRIPE_EXPIRES + timedelta(minutes=1)
DAY = timedelta(hours=24)
SECOND = timedelta(seconds=1)
EARNED_AT = datetime(2026, 9, 1, 4, 0)
SOURCE_POINTS = 1000
LIMIT = 100

# 订单行 1 行 2 件、单价 3000 仙；运费 800 仙；会员订单另有券 1000 仙、积分抵扣 500 与获得积分 45。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
COUPON_OFF = 1000
REDEEMED = 500
EARNED = 45
# 规格的每日初始库存；写入时已为一张订单预留 2 件。
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY

FINGERPRINT = "0f" * 32
SESSIONS_PATH = "/v1/checkout/sessions"
# creating 记录的创建参数快照（重试与对账原样发送）。
SNAPSHOT = '[["mode","payment"],["metadata[order_id]","1"]]'

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)
# 核验结论：换新键带过去的 expires_at 被 400 拒绝（param 为 expires_at），即原请求没有建成。
CREATE_REJECTED_EXPIRES_AT = StripeResult(
    StripeOutcome.REJECTED,
    error=StripeError(http_status=400, type="invalid_request_error", param="expires_at"),
)

CANCELLED_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)
PAID_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：被测函数提交与回滚，两个会话共用同一个内存库连接（模拟并发的 webhook）。
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


def _order_row(
    db: Session,
    variant_id: int,
    *,
    member_id: int | None = None,
    status: str = STATUS_AWAITING_PAYMENT,
    coupon_off: int = 0,
    redeemed: int = 0,
    earned: int = 0,
) -> Order:
    n = next(_SEQUENCE)
    order = Order(
        order_number=f"{n:016d}",
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=coupon_off,
        points_redeemed=redeemed,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL - coupon_off - redeemed + SHIPPING,
        points_earned=earned,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=CREATED,
        payment_expires_at=ORDER_EXPIRES,
        paid_at=STARTED if status == STATUS_PAID else None,
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


def _guest_order(db: Session, variant_id: int, *, status: str = STATUS_AWAITING_PAYMENT) -> _Placed:
    """游客订单（应付 6800 仙）：没有券与积分；写完即提交。"""
    order = _order_row(db, variant_id, status=status)
    db.commit()
    return _Placed(order.id, variant_id)


def _member_order(db: Session, variant_id: int) -> _Placed:
    """会员订单：来源批次 1000 积分（另一张已支付订单所得），本单预占券 1000 仙与积分 500，
    获得积分快照 45；写完即提交。"""
    phone = f"+60123{next(_SEQUENCE):06d}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=EARNED_AT))
    source = Order(
        order_number=f"{next(_SEQUENCE):016d}",
        status=STATUS_PAID,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"source-key-{next(_SEQUENCE)}",
        request_fingerprint=FINGERPRINT,
        created_at=EARNED_AT,
        payment_expires_at=EARNED_AT + timedelta(minutes=15),
        paid_at=EARNED_AT,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    _add(db, source)
    assert earn_points(db, member.id, source.id, SOURCE_POINTS, EARNED_AT) is not None
    order = _order_row(
        db,
        variant_id,
        member_id=member.id,
        coupon_off=COUPON_OFF,
        redeemed=REDEEMED,
        earned=EARNED,
    )
    coupon = Coupon(
        code=f"SAVE{next(_SEQUENCE)}",
        coupon_type=TYPE_FIXED,
        amount_off_sen=COUPON_OFF,
        percent_off=None,
        min_subtotal_sen=0,
        starts_at=datetime(2025, 1, 1),
        ends_at=datetime(2027, 12, 31),
        total_use_limit=None,
        per_member_use_limit=None,
        is_active=True,
    )
    _add(db, coupon)
    check = reserve_coupon(db, coupon.id, order.id, member.id, COUPON_OFF, SUBTOTAL, CREATED)
    assert check.ok
    reserve_points(db, member.id, order.id, REDEEMED, CREATED)
    db.commit()
    return _Placed(order.id, variant_id)


def _checkout(
    db: Session,
    order_id: int,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
    expires_at: datetime = STRIPE_EXPIRES,
    alerted_at: datetime | None = None,
    confirmed: bool = False,
    payment_intent_id: str | None = None,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），提交后
    返回记录 ID。发起于 STARTED，Stripe 到期时间默认为 STRIPE_EXPIRES；complete 的
    完成时间为 STARTED。
    """
    n = next(_SEQUENCE)
    total = db.scalars(select(Order.total_sen).where(Order.id == order_id)).one()
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": total,
        "currency": "myr",
        "status": status,
        "create_params": SNAPSHOT,
        "expires_at": expires_at,
        "created_at": STARTED,
        "alerted_at": alerted_at,
        "payment_intent_id": payment_intent_id,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    if status != CHECKOUT_CREATING:
        fields["checkout_session_id"] = f"cs_test_{n}"
        fields["payment_status"] = payment_status
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status == CHECKOUT_COMPLETE:
        fields["completed_at"] = STARTED
    if confirmed:
        fields["confirmed_at"] = STARTED
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return record.id


def _record(db: Session, checkout_id: int) -> StripeCheckout:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.id == checkout_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _session(db: Session, checkout_id: int, status: str, **overrides: Any) -> CheckoutSession:
    """与记录、订单相符的会话对象；已付款时 payment_intent 已展开、方式为 card。"""
    record = _record(db, checkout_id)
    total = db.scalars(select(Order.total_sen).where(Order.id == record.order_id)).one()
    paid = overrides.get("payment_status") == PAYMENT_PAID
    fields: dict[str, Any] = {
        "id": record.checkout_session_id or f"cs_test_for_{checkout_id}",
        "status": status,
        "payment_status": PAYMENT_UNPAID,
        "livemode": False,
        "currency": "myr",
        "amount_total": total,
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


def _intent(checkout_id: int, status: str) -> PaymentIntentInfo:
    """未付款会话的 PaymentIntent（没有成功的 Charge，付款方式类型为空）。"""
    return PaymentIntentInfo(id=f"pi_test_{checkout_id}", status=status, livemode=False)


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _run(db: Session, gateway: Any, now: datetime, limit: int = LIMIT) -> int:
    return stripe_overdue.process_stripe_checkouts(db, gateway, now, limit)


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


def _events(db: Session, order_id: int) -> list[tuple[str | None, str, str]]:
    db.rollback()
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _coupon_uses(db: Session, order_id: int) -> list[str]:
    db.rollback()
    return list(db.scalars(select(CouponUse.status).where(CouponUse.order_id == order_id)))


def _entry_kinds(db: Session, order_id: int) -> list[str]:
    db.rollback()
    stmt = select(PointEntry.kind).where(PointEntry.order_id == order_id).order_by(PointEntry.id)
    return list(db.scalars(stmt))


def _state(db: Session, placed: _Placed) -> dict[str, Any]:
    """订单状态、库存、事件、用券记录与积分流水，用来断言订单一侧什么都没改。"""
    order_id = placed.order_id
    return {
        "status": _status(db, order_id),
        "stock": _stock(db, placed.variant_id),
        "events": _events(db, order_id),
        "coupon_uses": _coupon_uses(db, order_id),
        "entries": _entry_kinds(db, order_id),
    }


def _polled(db: Session, checkout_ids: list[int]) -> list[datetime | None]:
    return [_record(db, checkout_id).last_polled_at for checkout_id in checkout_ids]


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    """本站服务记下的 error 级日志（告警邮件接入前的告警）。"""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.startswith("app.services.")
    ]


class _HookGateway(FakeStripeGateway):
    """每次调用 Stripe 时先执行钩子（看调用时的数据库状态，或模拟并发的 webhook）。"""

    def __init__(self, hook: Callable[[], None], *results: StripeResult[Any]) -> None:
        super().__init__(*results)
        self.hook = hook

    def _record(self, method: str, path: str, form: Any = (), idempotency_key: Any = None) -> Any:
        self.hook()
        return super()._record(method, path, form, idempotency_key)


# ---------------------------------------------------------------------------
# creating：重试与对账
# ---------------------------------------------------------------------------


def test_creating_is_retried_before_stripe_expiry_with_the_same_key(
    db: Session, variant: int
) -> None:
    """第 3 条「调用结果未知……时，记录保持 `creating`，之后的发起或定时任务
    以同一幂等键与同一快照重试，取得同一个会话」；验收：creating 记录不论是否
    已过 Stripe 到期时间都调用对账函数，本地支付到期时间已过也不取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    key = _record(db, checkout_id).idempotency_key
    url = "https://checkout.stripe.example.com/c/pay/cs_test_retry"
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "open", url=url)))

    assert _run(db, gateway, AFTER_ORDER_EXPIRY) == 1

    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    assert gateway.calls[0].idempotency_key == key
    assert gateway.calls[0].form == deserialize_form(SNAPSHOT)
    record = _record(db, checkout_id)
    assert (record.status, record.checkout_url) == (CHECKOUT_OPEN, url)
    assert record.last_polled_at == AFTER_ORDER_EXPIRY
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT
    assert _stock(db, variant) == RESERVED_STOCK


def test_creating_reconciled_as_never_created_cancels_and_releases(
    db: Session, variant: int
) -> None:
    """第 5 条「会话为 creating 时先按第 3 条取得会话或对账，只有对账确认
    没有建成会话（记为 expired）才取消」与核验结论（过去的 expires_at 被 400
    拒绝即原请求没有建成）；验收：订单已过本地支付到期时间时以 system 按现有
    规则取消，释放库存、券与积分。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(CREATE_REJECTED_EXPIRES_AT)

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]
    assert _stock(db, variant) == INITIAL_STOCK
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]


def test_creating_reconciled_as_never_created_before_local_expiry_keeps_the_order(
    db: Session, variant: int
) -> None:
    """第 3 条「Stripe 以参数不合格……拒绝……记为 expired」；验收：只有对账
    记为 expired 且订单已过本地支付到期时间时才取消——到期前的重试被 400 参数
    错误拒绝时只记 expired，订单仍待支付。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    before = _state(db, placed)
    rejected = StripeResult(
        StripeOutcome.REJECTED,
        error=StripeError(http_status=400, type="invalid_request_error", param="line_items"),
    )

    assert _run(db, FakeStripeGateway(rejected), NOW) == 1

    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert _state(db, placed) == before
    assert _coupon_uses(db, placed.order_id) == [USE_RESERVED]


def test_creating_with_an_unknown_result_keeps_the_order(db: Session, variant: int) -> None:
    """第 3 条「Stripe 不可用或答复冲突则留到下一轮……订单在此期间不取消、
    库存继续预留」；验收：结果不明时订单保留不动，不凭时间取消。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    before = _state(db, placed)
    gateway = FakeStripeGateway(UNAVAILABLE)

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    record = _record(db, checkout_id)
    assert (record.status, record.alerted_at) == (CHECKOUT_CREATING, None)
    assert record.last_polled_at == AFTER_STRIPE_EXPIRY
    assert _state(db, placed) == before


def test_creating_reconciled_to_a_paid_session_continues_to_confirmation(
    db: Session, variant: int
) -> None:
    """第 3 条「Stripe 回放原会话，说明当时已建成，记下会话后按它的实际状态
    处理（已付款按第 4 条确认……）」；验收：对账取得会话时按会话实际状态继续
    处理——complete 且已付款调用确认。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    replayed = _session(db, checkout_id, "open", url="https://checkout.stripe.example.com/c/x")
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(_ok(replayed), _ok(paid), _ok(paid))

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    assert _paths(gateway) == [
        ("POST", SESSIONS_PATH),
        _retrieve_path(paid.id),
        _retrieve_path(paid.id),
    ]
    record = _record(db, checkout_id)
    assert (record.status, record.payment_method_type) == (CHECKOUT_COMPLETE, "card")
    assert record.confirmed_at == AFTER_STRIPE_EXPIRY
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _events(db, placed.order_id) == [PAID_BY_SYSTEM]


def test_creating_alerts_once_24_hours_after_creation(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对，订单在此
    期间不取消、库存继续预留」；验收：creating 记录自创建满 24 小时只告警一次
    （SHOP-TASK-112 的 stripe_checkout_needs_review），不等到 Stripe 到期后
    24 小时，之后不再调用 Stripe。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    before = _state(db, placed)

    almost = FakeStripeGateway(UNAVAILABLE)
    assert _run(db, almost, STARTED + DAY - SECOND) == 1
    assert _paths(almost) == [("POST", SESSIONS_PATH)]
    assert _alerts(caplog) == []

    due = FakeStripeGateway()
    assert _run(db, due, STARTED + DAY) == 1
    later = FakeStripeGateway()
    assert _run(db, later, STRIPE_EXPIRES + DAY) == 1

    assert due.calls == [] and later.calls == []
    assert _alerts(caplog) == [f"stripe_checkout_needs_review checkout={checkout_id}"]
    record = _record(db, checkout_id)
    assert (record.status, record.alerted_at) == (CHECKOUT_CREATING, STARTED + DAY)
    assert _state(db, placed) == before


# ---------------------------------------------------------------------------
# open：Stripe 到期之后查询
# ---------------------------------------------------------------------------


def test_open_is_cancelled_only_after_stripe_confirms_it_expired(db: Session, variant: int) -> None:
    """第 5 条「不以本地时钟过了会话到期时间代替 Stripe 的状态确认」与第 4 条
    「订单在会话结束之前不会被超时取消」；验收：open 且已过 Stripe 到期时间时
    查询会话，expired 才记 expired 并以 system 取消（本地支付到期时间早已过去
    也不提前取消）。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _record(db, checkout_id).checkout_session_id
    before = _state(db, placed)

    early = FakeStripeGateway()
    assert _run(db, early, AFTER_ORDER_EXPIRY) == 0
    assert early.calls == []
    assert _state(db, placed) == before

    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "expired")))
    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    assert _paths(gateway) == [_retrieve_path(session_id)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]
    assert _stock(db, variant) == INITIAL_STOCK
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]


def test_open_still_open_after_stripe_expiry_keeps_waiting(db: Session, variant: int) -> None:
    """第 5 条「不以本地时钟过了会话到期时间代替 Stripe 的状态确认」：Stripe
    到期时间已过而会话仍为 open 时不取消，留到下一轮。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    before = _state(db, placed)
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "open")))

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    assert _record(db, checkout_id).status == CHECKOUT_OPEN
    assert _state(db, placed) == before


def test_open_reported_complete_and_paid_is_confirmed(db: Session, variant: int) -> None:
    """第 5 条「会话 complete 且已付款，按第 4 条确认、不取消」；验收：open
    记录查询到 complete 或已付款时调用确认。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)

    assert _run(db, FakeStripeGateway(_ok(paid)), AFTER_STRIPE_EXPIRY) == 1

    record = _record(db, checkout_id)
    assert (record.status, record.confirmed_at) == (CHECKOUT_COMPLETE, AFTER_STRIPE_EXPIRY)
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _events(db, placed.order_id) == [PAID_BY_SYSTEM]
    assert _stock(db, variant) == RESERVED_STOCK


def test_changed_record_is_left_for_the_next_round(
    db: Session, engine: Engine, variant: int
) -> None:
    """第 5 条「所有取消入口……用同一规则」；验收：取消经
    cancel_after_stripe_end，它返回 changed（查询 Stripe 之后记录被 webhook
    改为 complete 且已付款）时这条留到下一轮，下一轮按 complete 确认。模拟的
    webhook 一并写上付款方式类型，使这条不满足补齐扫描的条件、本轮只算会话处理
    这一条。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    expired = _session(db, checkout_id, "expired")
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)

    def webhook() -> None:
        with Session(engine) as other:
            other.execute(
                update(StripeCheckout)
                .where(StripeCheckout.id == checkout_id)
                .values(
                    status=CHECKOUT_COMPLETE,
                    payment_status=PAYMENT_PAID,
                    payment_method_type="card",
                    completed_at=AFTER_STRIPE_EXPIRY,
                )
            )
            other.commit()

    assert _run(db, _HookGateway(webhook, _ok(expired)), AFTER_STRIPE_EXPIRY) == 1

    assert _record(db, checkout_id).status == CHECKOUT_COMPLETE
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT
    assert _stock(db, variant) == RESERVED_STOCK

    next_round = AFTER_STRIPE_EXPIRY + timedelta(minutes=1)
    assert _run(db, FakeStripeGateway(_ok(paid)), next_round) == 1
    assert _status(db, placed.order_id) == STATUS_PAID


# ---------------------------------------------------------------------------
# complete：付款状态
# ---------------------------------------------------------------------------


def test_complete_and_paid_is_confirmed(db: Session, variant: int) -> None:
    """第 5 条「定时任务也定期查询该会话的付款状态，以 Stripe 给出的成功或
    失败为准」与第 4 条「确认不看订单的 15 分钟支付到期时间」；验收：complete
    而付款未定时查询会话，已付款调用确认。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)

    assert _run(db, FakeStripeGateway(_ok(paid)), AFTER_ORDER_EXPIRY) == 1

    assert _record(db, checkout_id).confirmed_at == AFTER_ORDER_EXPIRY
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _stock(db, variant) == RESERVED_STOCK


@pytest.mark.parametrize("intent_status", ["canceled", "requires_payment_method"])
def test_complete_with_a_failed_payment_is_cancelled(
    db: Session, variant: int, intent_status: str
) -> None:
    """第 5 条「以 Stripe 给出的成功或失败为准」；验收：付款未定时
    PaymentIntent 为 canceled 或 requires_payment_method（Stripe 给出的付款
    失败）时记为 expired 并按现有规则取消。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    failed = _session(
        db, checkout_id, "complete", payment_intent=_intent(checkout_id, intent_status)
    )

    assert _run(db, FakeStripeGateway(_ok(failed)), NOW) == 1

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]
    assert _stock(db, variant) == INITIAL_STOCK
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]


@pytest.mark.parametrize("intent_status", ["processing", "requires_action", None])
def test_complete_with_an_unsettled_payment_keeps_waiting(
    db: Session, variant: int, intent_status: str | None
) -> None:
    """第 5 条「complete 而付款未定……订单继续待支付、库存继续预留」「不因
    时间取消」；验收：processing、requires_action 等其余状态或查询不到
    PaymentIntent 时继续等待。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    intent = None if intent_status is None else _intent(checkout_id, intent_status)
    pending = _session(db, checkout_id, "complete", payment_intent=intent)
    before = _state(db, placed)

    assert _run(db, FakeStripeGateway(_ok(pending)), AFTER_STRIPE_EXPIRY) == 1

    record = _record(db, checkout_id)
    assert (record.status, record.alerted_at) == (CHECKOUT_COMPLETE, None)
    assert _state(db, placed) == before


def test_complete_without_a_result_alerts_once_after_24_hours(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 5 条「会话完成 24 小时后仍无结果就告警，由运营者在 Stripe 后台跟进，
    订单照旧等待」；验收：完成时间满 24 小时写告警时间并记一条
    stripe_payment_needs_review，只告警一次。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    pending = _ok(
        _session(db, checkout_id, "complete", payment_intent=_intent(checkout_id, "processing"))
    )
    before = _state(db, placed)

    assert _run(db, FakeStripeGateway(pending), STARTED + DAY - SECOND) == 1
    assert _alerts(caplog) == []
    assert _run(db, FakeStripeGateway(pending), STARTED + DAY) == 1
    assert _run(db, FakeStripeGateway(pending), STARTED + DAY + timedelta(hours=1)) == 1

    assert _alerts(caplog) == [f"stripe_payment_needs_review checkout={checkout_id}"]
    assert _record(db, checkout_id).alerted_at == STARTED + DAY
    assert _state(db, placed) == before


def test_complete_unreachable_alerts_as_payment_needs_review(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 5 条「会话完成 24 小时后仍无结果就告警」；验收：complete 记录查询
    不可用时不用 stripe_unreachable，按完成时间满 24 小时记
    stripe_payment_needs_review。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)

    assert _run(db, FakeStripeGateway(UNAVAILABLE), STARTED + DAY) == 1

    assert _alerts(caplog) == [f"stripe_payment_needs_review checkout={checkout_id}"]
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT


def test_refunded_mismatched_payment_cancels_an_overdue_order(db: Session, variant: int) -> None:
    """第 6 条「金额、币种不符，订单状态不变，服务端对这笔付款……全额退款」
    与 HANDOFF 0.53 的落实决定（写完退款尝试后把记录标为 expired，订单随后
    可……按现有规则取消）：确认返回 refunded 时，订单已过本地支付到期时间即以
    system 取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    mismatched = _session(
        db, checkout_id, "complete", payment_status=PAYMENT_PAID, amount_total=SUBTOTAL
    )

    assert _run(db, FakeStripeGateway(_ok(mismatched)), AFTER_ORDER_EXPIRY) == 1

    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert db.scalars(select(StripeRefund.checkout_id)).all() == [checkout_id]
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]
    assert _stock(db, variant) == INITIAL_STOCK


# ---------------------------------------------------------------------------
# Stripe 不可用与告警
# ---------------------------------------------------------------------------


def test_open_unreachable_alerts_once_24_hours_after_stripe_expiry(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 5 条「Stripe 不可用时……定时任务跳过该单留到下一轮；订单保持待支付、
    库存继续预留」「24 小时仍无法查询就告警由人工核对，订单不自动取消」；验收：
    open 记录自 Stripe 到期时间满 24 小时写告警时间并记一条
    stripe_unreachable，只告警一次。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    before = _state(db, placed)

    assert _run(db, FakeStripeGateway(UNAVAILABLE), STRIPE_EXPIRES + DAY - SECOND) == 1
    assert _alerts(caplog) == []
    assert _record(db, checkout_id).last_polled_at == STRIPE_EXPIRES + DAY - SECOND
    assert _run(db, FakeStripeGateway(UNAVAILABLE), STRIPE_EXPIRES + DAY) == 1
    assert _run(db, FakeStripeGateway(UNAVAILABLE), STRIPE_EXPIRES + DAY + SECOND) == 1

    assert _alerts(caplog) == [f"stripe_unreachable checkout={checkout_id}"]
    record = _record(db, checkout_id)
    assert (record.status, record.alerted_at) == (CHECKOUT_OPEN, STRIPE_EXPIRES + DAY)
    assert _state(db, placed) == before


def test_open_paid_session_that_confirm_cannot_verify_is_skipped(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 5 条「Stripe 不可用时……定时任务跳过该单留到下一轮」；验收：确认返回
    unavailable（会话为 open 而已付款，取不到可核对的对象）按 Stripe 不可用
    处理：跳过，自 Stripe 到期满 24 小时告警 stripe_unreachable。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    odd = _ok(_session(db, checkout_id, "open", payment_status=PAYMENT_PAID))
    before = _state(db, placed)

    assert _run(db, FakeStripeGateway(odd), AFTER_STRIPE_EXPIRY) == 1
    assert _alerts(caplog) == []
    assert _run(db, FakeStripeGateway(odd), STRIPE_EXPIRES + DAY) == 1

    assert _alerts(caplog) == [f"stripe_unreachable checkout={checkout_id}"]
    record = _record(db, checkout_id)
    assert (record.status, record.confirmed_at) == (CHECKOUT_OPEN, None)
    assert _state(db, placed) == before


def test_unconfigured_stripe_is_handled_as_unavailable(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 5 条「24 小时仍无法查询就告警由人工核对」；验收：Stripe 不可用含
    配置被清空后未配置——未配置的适配器不发请求，记录照样按不可用计时并告警，
    订单不取消。"""

    def transport(request: HttpRequest) -> HttpResponse:
        raise AssertionError("an unconfigured gateway must not send requests")

    gateway = StripeHttpGateway(Settings(_env_file=None), transport)
    assert not gateway.configured
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)

    assert _run(db, gateway, STRIPE_EXPIRES + DAY) == 1

    assert _alerts(caplog) == [f"stripe_unreachable checkout={checkout_id}"]
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize(
    ("status", "now"),
    [(CHECKOUT_OPEN, STRIPE_EXPIRES + DAY), (CHECKOUT_COMPLETE, STARTED + DAY)],
)
def test_records_with_an_alert_time_are_not_alerted_again(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture, status: str, now: datetime
) -> None:
    """第 5 条「24 小时仍无法查询就告警」；验收：每条记录至多告警一次——告警
    时间已非空的记录（如 SHOP-TASK-117 在确认返回 needs_review 时写的）不再记
    任何告警，告警时间也不清空。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, status, alerted_at=STARTED)

    assert _run(db, FakeStripeGateway(UNAVAILABLE), now) == 1

    assert _alerts(caplog) == []
    assert _record(db, checkout_id).alerted_at == STARTED
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# 付款方式类型补齐
# ---------------------------------------------------------------------------


def _paid_record(db: Session, variant: int) -> int:
    """已由 Stripe 付款确认、付款方式类型为空的记录（订单为 demo_paid）。"""
    placed = _guest_order(db, variant, status=STATUS_PAID)
    n = next(_SEQUENCE)
    return _checkout(
        db,
        placed.order_id,
        CHECKOUT_COMPLETE,
        payment_status=PAYMENT_PAID,
        confirmed=True,
        payment_intent_id=f"pi_test_paid_{n}",
    )


def test_payment_method_type_is_filled_by_a_separate_scan(db: Session, variant: int) -> None:
    """第 4 条付款方式类型「读不到时留空……由定时任务之后向 Stripe 重查补齐
    （只补这一字段，不再执行付款成功的状态迁移）」；验收：补齐单独扫描付款状态
    为 paid 而类型为空的记录，不论订单状态（订单已是 demo_paid）。"""
    checkout_id = _paid_record(db, variant)
    record = _record(db, checkout_id)
    order_id, intent_id = record.order_id, record.payment_intent_id
    intent = PaymentIntentInfo(
        id=intent_id, status="succeeded", livemode=False, payment_method_type="fpx"
    )
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID, payment_intent=intent)

    assert _run(db, FakeStripeGateway(_ok(paid)), AFTER_STRIPE_EXPIRY) == 1

    record = _record(db, checkout_id)
    assert (record.payment_method_type, record.last_polled_at) == ("fpx", AFTER_STRIPE_EXPIRY)
    assert _status(db, order_id) == STATUS_PAID
    assert _events(db, order_id) == []


def test_filling_is_silent_when_stripe_is_unavailable(
    db: Session, variant: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 4 条「由定时任务之后向 Stripe 重查补齐」；验收：补齐只是补字段，
    Stripe 不可用时静默留到下一轮，不记任何告警、不写告警时间，只写上次查询
    时间。"""
    checkout_id = _paid_record(db, variant)

    assert _run(db, FakeStripeGateway(UNAVAILABLE), STARTED + 2 * DAY) == 1

    record = _record(db, checkout_id)
    assert (record.payment_method_type, record.alerted_at) == (None, None)
    assert record.last_polled_at == STARTED + 2 * DAY
    assert _alerts(caplog) == []


# ---------------------------------------------------------------------------
# 选取、上限、轮转与处理条数
# ---------------------------------------------------------------------------


def test_only_records_that_need_action_are_selected(db: Session, variant: int) -> None:
    """第 5 条「查看入口只对没有 Stripe 记录（或只有 expired 记录）的订单执行
    超时取消，其余留给定时任务」；验收：会话处理只选待支付订单的不是 expired 的
    记录中的 creating、Stripe 到期时间已过的 open 与 complete——到期前的 open、
    expired 记录与订单已不是待支付的记录都不选、不调用 Stripe。"""
    later = AFTER_STRIPE_EXPIRY + timedelta(minutes=5)
    fresh = _checkout(db, _guest_order(db, variant).order_id, CHECKOUT_OPEN, expires_at=later)
    expired = _checkout(db, _guest_order(db, variant).order_id, CHECKOUT_EXPIRED)
    cancelled = _guest_order(db, variant, status=STATUS_CANCELLED)
    stale = _checkout(db, cancelled.order_id, CHECKOUT_OPEN)
    paid = _guest_order(db, variant, status=STATUS_PAID)
    leftover = _checkout(db, paid.order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway()

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 0
    assert stripe_overdue.has_pending_checkouts(db, AFTER_STRIPE_EXPIRY) is False

    assert gateway.calls == []
    assert _polled(db, [fresh, expired, stale, leftover]) == [None, None, None, None]


def test_each_scan_has_its_own_limit_and_rotates_by_last_poll(db: Session, variant: int) -> None:
    """第 5 条「定时任务跳过该单留到下一轮」；验收：两种扫描各自最多 limit
    条，按上次查询时间从早到晚、从没查过的排最前（同时刻按 ID），长期不能确定
    的记录不挤掉新记录；处理条数为两种扫描处理过的记录数之和（Stripe 不可用而
    跳过的也算）。"""
    opens = [_checkout(db, _guest_order(db, variant).order_id) for _ in range(3)]
    fills = [_paid_record(db, variant) for _ in range(3)]
    first = AFTER_STRIPE_EXPIRY
    second = first + timedelta(minutes=1)
    third = second + timedelta(minutes=1)

    assert _run(db, FakeStripeGateway(), first, limit=2) == 4
    assert _polled(db, opens) == [first, first, None]
    assert _polled(db, fills) == [first, first, None]

    assert _run(db, FakeStripeGateway(), second, limit=2) == 4
    assert _polled(db, opens) == [second, first, second]
    assert _polled(db, fills) == [second, first, second]

    newcomer = _checkout(db, _guest_order(db, variant).order_id)
    assert _run(db, FakeStripeGateway(), third, limit=2) == 4
    assert _polled(db, [*opens, newcomer]) == [second, third, second, third]


def test_stripe_is_called_outside_any_transaction(db: Session, variant: int) -> None:
    """第 5 条「取消一律先处理会话」；验收：调用 Stripe 时不持有任何数据库锁
    ——每次调用时被测会话都不在事务里（之后才按统一顺序加锁并重查）。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    expired = _session(db, checkout_id, "expired")
    in_transaction: list[bool] = []
    gateway = _HookGateway(lambda: in_transaction.append(db.in_transaction()), _ok(expired))

    assert _run(db, gateway, AFTER_STRIPE_EXPIRY) == 1

    assert in_transaction == [False]
    assert _status(db, placed.order_id) == STATUS_CANCELLED


def test_aware_time_is_rejected_before_touching_the_database(db: Session) -> None:
    """验收：库里的时间一律是不带时区的 UTC——带时区的当前时间先于访问数据库
    报错。"""
    with pytest.raises(ValueError):
        _run(db, FakeStripeGateway(), NOW.replace(tzinfo=UTC))

"""有 Stripe 记录的订单的模拟支付、本人取消与超时取消（app/services/payment.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2、5 条与「计价、优惠、积分与
库存」第 6 条，以及 docs/HANDOFF.md 0.53 的核验结论（再作废已过期或已完成的会话都 400，实现时
400 之后查询会话决定）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句或
核验结论；只出自 SHOP-TASK-114 验收标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；订单、订单行、
券与积分的预占和会话记录直接按模型写入，库存按已为订单预留 2 件写入。Stripe 一律用
FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会提交与回滚，测试数据都先
提交。与发起会话并发的情形，以第二个数据库会话在被测函数的交错点写入并提交一条会话记录来模拟
（StaticPool 只有一个连接，pysqlite 在第一条写语句前才开始事务）。SQLite 不支持行锁，加锁
顺序只按代码与 MySQL 行为审阅，这里不验证。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
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
    PaymentAttempt,
    PointEntry,
    Product,
    ProductVariant,
    SiteSetting,
    StripeCheckout,
    StripeRefund,
)
from app.models.coupon import TYPE_FIXED, USE_RELEASED, USE_RESERVED
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
from app.models.points import KIND_REDEEM, KIND_RELEASE
from app.models.site import PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
)
from app.services import payment
from app.services.coupons import reserve_coupon
from app.services.points import earn_points, reserve_points
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeError,
    StripeOutcome,
    StripeResult,
)

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起会话，5 分钟时操作。
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
# 会员来源批次的取得时间：在上面这些时刻都未过期。
EARNED_AT = datetime(2026, 9, 1, 4, 0)
SOURCE_POINTS = 1000

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

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)
# 核验结论：再作废已过期或已完成的会话都 400。
EXPIRE_REJECTED = StripeResult(
    StripeOutcome.REJECTED, error=StripeError(http_status=400, type="invalid_request_error")
)
# 核验结论：换新键带过去的 expires_at 被 400 拒绝（param 为 expires_at），即原请求没有建成。
CREATE_REJECTED_EXPIRES_AT = StripeResult(
    StripeOutcome.REJECTED,
    error=StripeError(http_status=400, type="invalid_request_error", param="expires_at"),
)

CANCELLED_BY_GUEST = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_GUEST)
CANCELLED_BY_MEMBER = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_MEMBER)
CANCELLED_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：被测函数提交与回滚，两个会话共用同一个内存库连接（模拟并发的发起）。
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


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env：最低收款额取默认的 200 仙。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


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
    member_id: int | None
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
    placed_at: datetime = CREATED,
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
        created_at=placed_at,
        payment_expires_at=placed_at + timedelta(minutes=15),
        paid_at=placed_at if status == STATUS_PAID else None,
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


def _guest_order(
    db: Session,
    variant_id: int,
    *,
    status: str = STATUS_AWAITING_PAYMENT,
    placed_at: datetime = CREATED,
) -> _Placed:
    """游客订单（应付 6800 仙）：没有券与积分；写完即提交。"""
    order = _order_row(db, variant_id, status=status, placed_at=placed_at)
    db.commit()
    return _Placed(order.id, None, variant_id)


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
    return _Placed(order.id, member.id, variant_id)


def _checkout(
    db: Session,
    order_id: int,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
    confirmed: bool = False,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），提交后返回记录 ID。"""
    n = next(_SEQUENCE)
    total = db.scalars(select(Order.total_sen).where(Order.id == order_id)).one()
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": total,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
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


def _record_state(db: Session, checkout_id: int) -> dict[str, Any]:
    record = _record(db, checkout_id)
    return {name: getattr(record, name) for name in StripeCheckout.__table__.columns.keys()}


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


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _expire_path(session_id: str) -> tuple[str, str]:
    return ("POST", f"{SESSIONS_PATH}/{session_id}/expire")


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


def _attempt_count(db: Session, order_id: int) -> int:
    db.rollback()
    stmt = select(PaymentAttempt.id).where(PaymentAttempt.order_id == order_id)
    return len(db.scalars(stmt).all())


def _coupon_uses(db: Session, order_id: int) -> list[str]:
    db.rollback()
    return list(db.scalars(select(CouponUse.status).where(CouponUse.order_id == order_id)))


def _entry_kinds(db: Session, order_id: int) -> list[str]:
    db.rollback()
    stmt = select(PointEntry.kind).where(PointEntry.order_id == order_id).order_by(PointEntry.id)
    return list(db.scalars(stmt))


def _state(db: Session, placed: _Placed) -> dict[str, Any]:
    """订单状态、库存、事件、支付尝试、用券记录与积分流水，用来断言订单一侧什么都没改。"""
    order_id = placed.order_id
    return {
        "status": _status(db, order_id),
        "stock": _stock(db, placed.variant_id),
        "events": _events(db, order_id),
        "attempts": _attempt_count(db, order_id),
        "coupon_uses": _coupon_uses(db, order_id),
        "entries": _entry_kinds(db, order_id),
    }


def _setting(db: Session, method: str) -> None:
    row = SiteSetting(id=1, sms_verification_enabled=False, payment_method=method)
    row.updated_at = CREATED
    db.add(row)
    db.commit()


def _key() -> str:
    return f"pay-key-{next(_SEQUENCE):012d}"


def _in_other_session[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """用第二个数据库会话执行一个操作（操作自己提交）。"""
    with Session(engine) as other:
        return action(other)


def _before_first_transition(monkeypatch: pytest.MonkeyPatch, action: Callable[[], Any]) -> None:
    """payment 模块第一次调用迁移判定函数（随后就是带条件的 UPDATE）之前插入一段操作。"""
    real = payment.is_transition_allowed
    done: list[bool] = []

    def wrapper(current: str, target: str, actor: str) -> bool:
        if not done:
            done.append(True)
            action()
        return real(current, target, actor)

    monkeypatch.setattr(payment, "is_transition_allowed", wrapper)


def _start_concurrently(engine: Engine, order_id: int) -> Callable[[], Any]:
    """并发的发起：另一个会话写入并提交一条 creating 记录。"""
    insert = partial(_checkout, order_id=order_id, status=CHECKOUT_CREATING)
    return lambda: _in_other_session(engine, insert)


# ---------------------------------------------------------------------------
# 模拟支付：submit_payment
# ---------------------------------------------------------------------------


def test_replay_comes_before_the_path_check(db: Session, variant: int) -> None:
    """第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有模拟支付流程不变」；
    验收：「同键同请求的幂等重放返回原结果」排在检查顺序的第一位，「重放不改订单，不受途径检查
    影响」：没有记录时提交的失败尝试，在订单有了 open 记录之后以同键同请求重放，仍返回原结果，
    不新写支付尝试。"""
    placed = _guest_order(db, variant)
    key = _key()
    first, created = payment.submit_payment(db, placed.order_id, "card", "failure", key, NOW)
    assert created
    _checkout(db, placed.order_id, CHECKOUT_OPEN)

    again, created_again = payment.submit_payment(db, placed.order_id, "card", "failure", key, NOW)

    assert not created_again
    assert again == first
    assert _attempt_count(db, placed.order_id) == 1
    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize("record_status", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
@pytest.mark.parametrize("result", ["success", "failure"])
@pytest.mark.parametrize("now", [NOW, AFTER_ORDER_EXPIRY], ids=["unexpired", "overdue"])
def test_active_record_rejects_simulated_payment(
    db: Session, variant: int, record_status: str, result: str, now: datetime
) -> None:
    """第 2 条「订单只要有一条不是 expired 的 Stripe 记录（creating、open 或 complete），不论开关
    当前取值，都不接受模拟支付」；验收：有记录抛 PaymentPathMismatch 排在本地到期之前（已过
    15 分钟也不超时取消），先于写支付尝试，订单、库存与事件都不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, record_status)
    before = _state(db, placed)
    record_before = _record_state(db, checkout_id)

    with pytest.raises(payment.PaymentPathMismatch):
        payment.submit_payment(db, placed.order_id, "card", result, _key(), now)

    assert _state(db, placed) == before
    assert _record_state(db, checkout_id) == record_before


def test_expired_record_keeps_the_simulated_flow(db: Session, variant: int) -> None:
    """第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有模拟支付流程不变」：
    只有 expired 记录的订单照常模拟支付成功。"""
    placed = _guest_order(db, variant)
    _checkout(db, placed.order_id, CHECKOUT_EXPIRED)

    outcome, created = payment.submit_payment(db, placed.order_id, "card", "success", _key(), NOW)

    assert created
    assert outcome.status == STATUS_PAID
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _attempt_count(db, placed.order_id) == 1


def test_overdue_without_record_is_order_expired_even_on_stripe_route(
    db: Session, variant: int
) -> None:
    """「计价、优惠、积分与库存」第 6 条「15 分钟未支付自动取消并释放」；验收：没有记录而已过
    本地支付到期时间时按现有做法先超时取消再抛 OrderExpired，排在途径判定之前（开关为
    「Stripe 测试」也一样）；不写支付尝试，库存加回，事件操作者为 system。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    placed = _guest_order(db, variant)

    with pytest.raises(payment.OrderExpired) as raised:
        payment.submit_payment(db, placed.order_id, "card", "success", _key(), AFTER_ORDER_EXPIRY)

    assert raised.value.status == STATUS_CANCELLED
    assert _attempt_count(db, placed.order_id) == 0
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]


@pytest.mark.parametrize("result", ["success", "failure"])
def test_stripe_route_rejects_simulated_payment(db: Session, variant: int, result: str) -> None:
    """第 2 条「服务端在访客发起支付时按当时的值判定」：开关为「Stripe 测试」、应付不低于最低
    收款额（默认 200 仙）而没有记录时，途径为 stripe；验收：抛 PaymentPathMismatch，先于写支付
    尝试，订单不变。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    placed = _guest_order(db, variant)
    before = _state(db, placed)

    with pytest.raises(payment.PaymentPathMismatch):
        payment.submit_payment(db, placed.order_id, "card", result, _key(), NOW)

    assert _state(db, placed) == before


def test_below_minimum_amount_stays_simulated(
    db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「订单应付低于 Stripe 对 MYR 的最低收款额（配置项……）时，即使为『Stripe 测试』
    也走模拟支付」；验收：最低收款额取 app/core/config.py 的 stripe_min_amount_sen。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    placed = _guest_order(db, variant)
    settings = Settings(_env_file=None, stripe_min_amount_sen=SUBTOTAL + SHIPPING + 1)
    monkeypatch.setattr(payment, "get_settings", lambda: settings)

    outcome, created = payment.submit_payment(db, placed.order_id, "card", "success", _key(), NOW)

    assert created
    assert outcome.status == STATUS_PAID


def test_success_rechecks_records_after_the_update(
    engine: Engine, db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「现有的模拟支付提交……要改为在锁住订单的同一事务里先查该单的 Stripe 记录」；
    验收：成功的一路在带条件的 UPDATE 命中之后以加锁读再查一次，堵住与发起会话并发的情形——
    途径检查之后、UPDATE 之前另一会话写入了 creating 记录：回滚并抛 PaymentPathMismatch，订单
    仍待支付，没有支付尝试与事件。"""
    placed = _guest_order(db, variant)
    _before_first_transition(monkeypatch, _start_concurrently(engine, placed.order_id))
    before = _state(db, placed)

    with pytest.raises(payment.PaymentPathMismatch):
        payment.submit_payment(db, placed.order_id, "card", "success", _key(), NOW)

    assert _state(db, placed) == before


def test_failure_rechecks_records_under_the_order_lock(
    engine: Engine, db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「在锁住订单的同一事务里先查该单的 Stripe 记录」；验收：失败的一路在写支付尝试之前
    以 SELECT … FOR UPDATE 锁住订单行并重查——途径检查之后另一会话写入了 creating 记录：回滚并
    抛 PaymentPathMismatch，不写支付尝试。"""
    placed = _guest_order(db, variant)
    real = payment._lock_order
    start = _start_concurrently(engine, placed.order_id)
    done: list[bool] = []

    def lock_after_start(session: Session, order_id: int) -> Order:
        if not done:
            done.append(True)
            start()
        return real(session, order_id)

    monkeypatch.setattr(payment, "_lock_order", lock_after_start)
    before = _state(db, placed)

    with pytest.raises(payment.PaymentPathMismatch):
        payment.submit_payment(db, placed.order_id, "card", "failure", _key(), NOW)

    assert done == [True]
    assert _state(db, placed) == before


# ---------------------------------------------------------------------------
# 本人取消：creating
# ---------------------------------------------------------------------------


def test_creating_reconciled_as_expired_is_cancelled(db: Session, variant: int) -> None:
    """第 5 条「会话为 creating 时先按第 3 条取得会话或对账，只有对账确认没有建成会话（记为
    expired）才取消」；核验结论「换新键带过去的 expires_at 被 400 拒绝（param 为 expires_at），
    即原请求没有建成」：Stripe 到期时间之后对账被拒，记录为 expired，订单由本人取消、库存加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(CREATE_REJECTED_EXPIRES_AT)

    outcome = payment.cancel_order(db, placed.order_id, AFTER_STRIPE_EXPIRY, gateway=gateway)

    assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert _paths(gateway) == [("POST", SESSIONS_PATH)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_GUEST]


def test_creating_reconciled_as_open_is_expired_then_cancelled(db: Session, variant: int) -> None:
    """第 5 条「会话为 creating 时先按第 3 条取得会话」「会话为 open 时请求 Stripe 作废：作废成功
    ……才按现有规则取消」；验收：对账取得 open 会话时接着请求作废：重试取得 open 会话、作废
    成功，记录为 expired，订单取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    created = _session(
        db, checkout_id, "open", id="cs_test_new", url="https://checkout.stripe.example.com/new"
    )
    expired = _session(db, checkout_id, "expired", id="cs_test_new")
    gateway = FakeStripeGateway(_ok(created), _ok(expired))

    outcome = payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert outcome.status == STATUS_CANCELLED
    assert _paths(gateway) == [("POST", SESSIONS_PATH), _expire_path("cs_test_new")]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.checkout_session_id == "cs_test_new"
    assert _stock(db, variant) == INITIAL_STOCK


def test_creating_with_unknown_result_is_not_cancelled(db: Session, variant: int) -> None:
    """第 3 条「Stripe 不可用或答复冲突则留到下一轮……订单在此期间不取消、库存继续预留」；
    验收：对账结果不明抛 StripeUnavailable，记录保持 creating，订单与库存不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    before = _state(db, placed)
    gateway = FakeStripeGateway(UNAVAILABLE)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert _state(db, placed) == before
    assert _record(db, checkout_id).status == CHECKOUT_CREATING


# ---------------------------------------------------------------------------
# 本人取消：open
# ---------------------------------------------------------------------------


def test_open_is_expired_then_cancelled(db: Session, variant: int) -> None:
    """第 5 条「会话为 open 时请求 Stripe 作废：作废成功……才按现有规则取消」：作废成功后记录为
    expired、活动会话位释放，订单为 demo_cancelled、取消方为本人，库存加回，事件操作者为
    guest。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _record(db, checkout_id).checkout_session_id
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "expired")))

    outcome = payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert _paths(gateway) == [_expire_path(session_id)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_GUEST]


def test_rejected_expire_then_expired_session_is_cancelled(db: Session, variant: int) -> None:
    """第 5 条「作废成功或会话已过期才按现有规则取消」；核验结论「再作废已过期或已完成的会话都
    400……实现时 400 之后查询会话决定，不解析消息」：作废被拒后查询为 expired，记录记为
    expired，订单取消。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _record(db, checkout_id).checkout_session_id
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(_session(db, checkout_id, "expired")))

    outcome = payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert outcome.status == STATUS_CANCELLED
    assert _paths(gateway) == [_expire_path(session_id), _retrieve_path(session_id)]
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert _stock(db, variant) == INITIAL_STOCK


def test_rejected_expire_then_paid_session_is_confirmed_not_cancelled(
    db: Session, variant: int
) -> None:
    """第 5 条「Stripe 答复会话已完成，按会话的付款状态处理」「会话 complete 且已付款，按第 4 条
    确认、不取消」；核验结论「400 之后查询会话决定」：作废被拒、查询为 complete 且已付款时抛
    OrderNotCancellable，订单为 demo_paid（由这个会话确认），库存不加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(paid))

    with pytest.raises(payment.OrderNotCancellable) as raised:
        payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert raised.value.status == STATUS_PAID
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _record(db, checkout_id).confirmed_at == NOW
    assert _stock(db, variant) == RESERVED_STOCK
    assert _events(db, placed.order_id) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]


def test_refunded_mismatched_payment_lets_the_order_be_cancelled(db: Session, variant: int) -> None:
    """第 6 条「金额、币种不符，订单状态不变，服务端对这笔付款……全额退款」与 HANDOFF 0.53 的
    落实决定（写完退款尝试后把记录标为 expired、释放活动会话位，订单随后可……按现有规则取消）；
    验收：确认返回 refunded 时不给记录 ID 调用 cancel_after_stripe_end 取消：订单取消，记录为
    expired，有一条退款尝试，库存加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    mismatched = _session(
        db, checkout_id, "complete", payment_status=PAYMENT_PAID, amount_total=SUBTOTAL
    )
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(mismatched))

    outcome = payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    refunds = db.scalars(select(StripeRefund.checkout_id)).all()
    assert refunds == [checkout_id]
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_GUEST]


def test_needs_review_is_not_cancelled(db: Session, variant: int) -> None:
    """第 5 条「24 小时仍无法查询就告警由人工核对，订单不自动取消」；验收：needs_review（没写成
    退款尝试、活动位保留）时不取消、抛 StripeUnavailable，交人工核对：会话缺 amount_total 的
    已付款会话，订单与库存不变，记录仍占活动位。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    unrefundable = _session(
        db, checkout_id, "complete", payment_status=PAYMENT_PAID, amount_total=None
    )
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(unrefundable))
    before = _state(db, placed)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert _state(db, placed) == before
    assert _record(db, checkout_id).active_order_id == placed.order_id


def test_complete_with_undetermined_payment_is_not_cancelled(db: Session, variant: int) -> None:
    """第 5 条「complete 而付款未定……订单继续待支付、库存继续预留……不因时间取消」；验收：付款
    未定抛 StripeUnavailable：作废被拒、查询为 complete 而未付款，订单与库存不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    unpaid = _session(db, checkout_id, "complete")
    # 未付款的会话没有展开的 PaymentIntent，确认会再查询一次。
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(unpaid), _ok(unpaid))
    before = _state(db, placed)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, AFTER_ORDER_EXPIRY, gateway=gateway)

    assert _state(db, placed) == before
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_COMPLETE, placed.order_id)


def test_still_open_after_rejected_expire_is_not_cancelled(db: Session, variant: int) -> None:
    """第 5 条「恢复后按会话的实际状态处理，不以本地时钟过了会话到期时间代替 Stripe 的状态
    确认」；验收：作废后查询仍不能确定（仍为 open）时不取消、抛 StripeUnavailable。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    gateway = FakeStripeGateway(EXPIRE_REJECTED, _ok(_session(db, checkout_id, "open")))
    before = _state(db, placed)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, AFTER_STRIPE_EXPIRY, gateway=gateway)

    assert _state(db, placed) == before
    assert _record(db, checkout_id).status == CHECKOUT_OPEN


@pytest.mark.parametrize("case", ["no-gateway", "expire-unavailable", "retrieve-unavailable"])
def test_stripe_unavailable_keeps_the_order(db: Session, variant: int, case: str) -> None:
    """第 5 条「Stripe 不可用时本人取消提示稍后再试……订单保持待支付、库存继续预留」；验收：
    cancel_order 不给适配器时等同 Stripe 不可用；作废或作废被拒后的查询不可用时都抛
    StripeUnavailable，订单、库存与记录都不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)
    gateway = None
    if case == "expire-unavailable":
        gateway = FakeStripeGateway(UNAVAILABLE)
    elif case == "retrieve-unavailable":
        gateway = FakeStripeGateway(EXPIRE_REJECTED, UNAVAILABLE)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert _state(db, placed) == before
    assert _record_state(db, checkout_id) == record_before


def test_member_cancel_after_local_expiry_releases_stock_coupon_and_points(
    db: Session, variant: int
) -> None:
    """第 4 条「订单在会话结束之前不会被超时取消（第 5 条）」与第 5 条「作废成功……才按现有规则
    取消」；「订单与退款状态」：取消释放库存、券及积分预占。验收：本地 15 分钟已过、Stripe
    会话才结束时本人取消成功，取消方为本人（不是超时），释放库存、券与积分，事件操作者为
    member。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    assert _coupon_uses(db, placed.order_id) == [USE_RESERVED]
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "expired")))

    outcome = payment.cancel_order(
        db, placed.order_id, AFTER_ORDER_EXPIRY, actor="member", gateway=gateway
    )

    assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert _stock(db, variant) == INITIAL_STOCK
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]
    assert _events(db, placed.order_id) == [CANCELLED_BY_MEMBER]


@pytest.mark.parametrize("status", [STATUS_CANCELLED, STATUS_PAID])
def test_order_no_longer_awaiting_does_not_touch_the_session(
    db: Session, variant: int, status: str
) -> None:
    """「所有状态迁移校验当前状态」；验收：订单已不是待支付时照现有规则（已取消返回当前状态、其余
    抛 OrderNotCancellable），不处理会话（不调用 Stripe）。"""
    placed = _guest_order(db, variant, status=status)
    _checkout(db, placed.order_id, CHECKOUT_OPEN)
    gateway = FakeStripeGateway()

    if status == STATUS_CANCELLED:
        outcome = payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)
        assert outcome.status == STATUS_CANCELLED
    else:
        with pytest.raises(payment.OrderNotCancellable):
            payment.cancel_order(db, placed.order_id, NOW, gateway=gateway)

    assert gateway.calls == []


def test_cancel_racing_a_new_session_takes_the_record_path(
    engine: Engine, db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「现有的……本人取消……要改为在锁住订单的同一事务里先查该单的 Stripe 记录」；验收：
    没有记录时只在带条件的 UPDATE 命中后以加锁读重查一次，有则回滚并按有记录的路径重新处理
    一次——UPDATE 之前另一会话写入了 creating 记录：不给适配器时按 Stripe 不可用抛
    StripeUnavailable，订单与库存不变。"""
    placed = _guest_order(db, variant)
    _before_first_transition(monkeypatch, _start_concurrently(engine, placed.order_id))
    before = _state(db, placed)

    with pytest.raises(payment.StripeUnavailable):
        payment.cancel_order(db, placed.order_id, NOW)

    assert _state(db, placed) == before


# ---------------------------------------------------------------------------
# cancel_after_stripe_end
# ---------------------------------------------------------------------------


def test_system_end_commits_expired_and_cancel_together(db: Session, variant: int) -> None:
    """第 5 条「所有取消入口（本人取消、……定时任务）用同一规则」；验收：cancel_after_stripe_end 以
    system 调用时记录的 expired 与订单的取消在同一事务提交（一次提交），不看本地支付到期时间；
    事件操作者为 system，库存加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    commits: list[bool] = []
    event.listen(db, "after_commit", lambda _session: commits.append(True))

    result = payment.cancel_after_stripe_end(
        db,
        placed.order_id,
        ACTOR_SYSTEM,
        AFTER_STRIPE_EXPIRY,
        checkout_id=checkout_id,
        seen_status=CHECKOUT_OPEN,
    )

    assert result is payment.StripeEndResult.CANCELLED
    assert commits == [True]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]
    assert _stock(db, variant) == INITIAL_STOCK


@pytest.mark.parametrize("status", [STATUS_CANCELLED, STATUS_PAID])
def test_end_for_order_no_longer_awaiting_only_expires_the_record(
    db: Session, variant: int, status: str
) -> None:
    """「所有状态迁移校验当前状态」；验收：订单已不是待支付时只记 expired、订单不变，返回 kept。"""
    placed = _guest_order(db, variant, status=status)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    before = _state(db, placed)

    result = payment.cancel_after_stripe_end(
        db,
        placed.order_id,
        ACTOR_SYSTEM,
        NOW,
        checkout_id=checkout_id,
        seen_status=CHECKOUT_OPEN,
    )

    assert result is payment.StripeEndResult.KEPT
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert _state(db, placed) == before


def test_end_with_another_active_record_keeps_the_order(db: Session, variant: int) -> None:
    """第 2 条「订单只要有一条不是 expired 的 Stripe 记录……都不接受模拟支付」，第 5 条「有 Stripe
    记录的订单，取消一律先处理会话」；验收：订单另有不是 expired 的记录时只记 expired（这里给的
    记录早已是 expired）、订单不变，返回 kept；不给记录 ID 时同样 kept。"""
    placed = _guest_order(db, variant)
    old_id = _checkout(db, placed.order_id, CHECKOUT_EXPIRED)
    new_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    before = _state(db, placed)

    with_id = payment.cancel_after_stripe_end(
        db, placed.order_id, ACTOR_SYSTEM, NOW, checkout_id=old_id, seen_status=CHECKOUT_EXPIRED
    )
    without_id = payment.cancel_after_stripe_end(db, placed.order_id, ACTOR_SYSTEM, NOW)

    assert with_id is payment.StripeEndResult.KEPT
    assert without_id is payment.StripeEndResult.KEPT
    assert _record(db, new_id).status == CHECKOUT_OPEN
    assert _state(db, placed) == before


@pytest.mark.parametrize(
    ("record_status", "payment_status", "confirmed", "seen_status"),
    [
        pytest.param(CHECKOUT_COMPLETE, PAYMENT_PAID, False, CHECKOUT_OPEN, id="webhook-complete"),
        pytest.param(CHECKOUT_OPEN, PAYMENT_PAID, False, CHECKOUT_OPEN, id="paid-status"),
        pytest.param(CHECKOUT_COMPLETE, PAYMENT_PAID, True, CHECKOUT_COMPLETE, id="confirmed"),
    ],
)
def test_end_after_record_changed_changes_nothing(
    db: Session,
    variant: int,
    record_status: str,
    payment_status: str,
    confirmed: bool,
    seen_status: str,
) -> None:
    """第 5 条「会话 complete 且已付款，按第 4 条确认、不取消」；验收：查询 Stripe 后记录被改为
    complete 且已付款，或付款状态为 paid、已有确认时间时，不改记录与订单，返回 changed。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(
        db, placed.order_id, record_status, payment_status=payment_status, confirmed=confirmed
    )
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)

    result = payment.cancel_after_stripe_end(
        db, placed.order_id, ACTOR_SYSTEM, NOW, checkout_id=checkout_id, seen_status=seen_status
    )

    assert result is payment.StripeEndResult.CHANGED
    assert _record_state(db, checkout_id) == record_before
    assert _state(db, placed) == before


@pytest.mark.parametrize(
    "payment_status",
    [
        pytest.param(None, id="reconciled-expired"),
        pytest.param(PAYMENT_PAID, id="refunded"),
    ],
)
def test_end_without_record_id_only_rechecks_the_order(
    db: Session, variant: int, payment_status: str | None
) -> None:
    """第 5 条「只有对账确认没有建成会话（记为 expired）才取消」与 HANDOFF 0.53 的落实决定（核对
    不符的已付款会话写完退款尝试后标为 expired，订单随后可按现有规则取消）；验收：不给记录 ID
    时只重查订单仍待支付且已没有不是 expired 的记录，再取消；记录不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_EXPIRED)
    if payment_status is not None:
        record = _record(db, checkout_id)
        record.payment_status = payment_status
        db.commit()
    record_before = _record_state(db, checkout_id)

    result = payment.cancel_after_stripe_end(db, placed.order_id, ACTOR_GUEST, NOW)

    assert result is payment.StripeEndResult.CANCELLED
    assert _record_state(db, checkout_id) == record_before
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [CANCELLED_BY_GUEST]
    assert _stock(db, variant) == INITIAL_STOCK


def test_end_rejects_unknown_actor(db: Session, variant: int) -> None:
    """「所有状态迁移校验当前状态与操作者」；验收：操作者为 guest、member 或 system，其他值抛
    ValueError，订单不变。"""
    placed = _guest_order(db, variant)

    with pytest.raises(ValueError):
        payment.cancel_after_stripe_end(db, placed.order_id, "admin", NOW)

    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# 超时取消：expire_overdue_orders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_status", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_view_timeout_skips_orders_with_records(
    db: Session, variant: int, record_status: str
) -> None:
    """第 5 条「查看入口只对没有 Stripe 记录（或只有 expired 记录）的订单执行超时取消，其余留给
    定时任务」；验收：有记录的订单不做任何改动。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, record_status)
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)

    cancelled = payment.expire_overdue_orders(db, AFTER_ORDER_EXPIRY, order_ids=[placed.order_id])

    assert cancelled == 0
    assert _state(db, placed) == before
    assert _record_state(db, checkout_id) == record_before


def test_view_timeout_still_cancels_orders_with_only_expired_records(
    db: Session, variant: int
) -> None:
    """第 5 条「查看入口只对没有 Stripe 记录（或只有 expired 记录）的订单执行超时取消」：只有
    expired 记录的逾期订单照常超时取消，事件操作者为 system。"""
    placed = _guest_order(db, variant)
    _checkout(db, placed.order_id, CHECKOUT_EXPIRED)

    cancelled = payment.expire_overdue_orders(db, AFTER_ORDER_EXPIRY, order_ids=[placed.order_id])

    assert cancelled == 1
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]


def test_limit_is_applied_after_excluding_orders_with_records(db: Session, variant: int) -> None:
    """「计价、优惠、积分与库存」第 6 条「15 分钟未支付自动取消并释放」；验收：候选查询在应用
    limit 之前就排除有记录的订单——最早到期的 limit 张都有活动会话时，后面的普通逾期订单照常
    取消。"""
    earliest = [
        _guest_order(db, variant, placed_at=CREATED - timedelta(minutes=10 - i)) for i in range(2)
    ]
    for placed in earliest:
        _checkout(db, placed.order_id, CHECKOUT_OPEN)
    plain = _guest_order(db, variant)

    cancelled = payment.expire_overdue_orders(db, AFTER_ORDER_EXPIRY, limit=2)

    assert cancelled == 1
    assert _status(db, plain.order_id) == STATUS_CANCELLED
    assert [_status(db, p.order_id) for p in earliest] == [STATUS_AWAITING_PAYMENT] * 2


def test_timeout_rechecks_records_after_the_update(
    engine: Engine, db: Session, variant: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「现有的……查看触发的超时取消与定时任务都要改为在锁住订单的同一事务里先查该单的
    Stripe 记录」；验收：选单之后才建记录的订单在带条件的 UPDATE 命中后的重查里被跳过——回滚，
    订单、库存与事件都不变。"""
    placed = _guest_order(db, variant)
    _before_first_transition(monkeypatch, _start_concurrently(engine, placed.order_id))
    before = _state(db, placed)

    cancelled = payment.expire_overdue_orders(db, AFTER_ORDER_EXPIRY)

    assert cancelled == 0
    assert _state(db, placed) == before

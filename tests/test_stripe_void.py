"""人工作废已告警的 Stripe 结账记录（app/services/payment.py 的 void_alerted_checkout）。

依据 docs/DESIGN.md 1.22（提交 ffb043e）「Stripe 测试环境支付」第 11 条第（1）项与「订单与退款
状态」第 3 条，以及 docs/HANDOFF.md 0.90 记录的 Kelvin 2026-11-08 决定（操作者 admin 的取消对访客
也归为超时）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的第 11 条哪一句；只出自
SHOP-TASK-223 验收标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；订单、订单行、
券与积分的预占和会话记录直接按模型写入，库存按已为订单预留 2 件写入。Stripe 一律用
FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会提交与回滚，测试数据都先
提交。Stripe 处理期间的并发改动，以第二个数据库会话在调用 Stripe 时写入并提交来模拟（被测函数
此时已结束读事务）。SQLite 不支持行锁，加锁顺序只按代码与 MySQL 行为审阅，这里不验证。
"""

from __future__ import annotations

import itertools
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
    AdminAccount,
    AuditEvent,
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
    ACTOR_ADMIN,
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
    SOURCE_CHECKOUT,
    STRIPE_REFUND_PENDING,
)
from app.services import ops_alert, payment
from app.services.coupons import reserve_coupon
from app.services.points import earn_points, reserve_points
from app.services.stripe_confirm import ConfirmResult, confirm_checkout
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeOutcome,
    StripeResult,
)

# 订单在 CREATED 下单，访客 1 分钟后发起会话；告警在 24 小时之后写入，管理员再过一阵处置。
CREATED = datetime(2026, 11, 1, 2, 0)
STARTED = CREATED + timedelta(minutes=1)
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)
ALERTED = STARTED + timedelta(hours=25)
NOW = ALERTED + timedelta(hours=2)
LATER = NOW + timedelta(hours=1)
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
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY

FINGERPRINT = "0f" * 32
SESSIONS_PATH = "/v1/checkout/sessions"

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

VOIDED_BY_ADMIN = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_ADMIN)
CANCELLED_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)
PAID_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """以 monkeypatch 替换 ops_alert.alert（确认里的告警点），不入真实队列。"""
    calls: list[tuple[str, object]] = []

    def record(category: str, target: object = None, **_: object) -> None:
        calls.append((category, target))

    monkeypatch.setattr(ops_alert, "alert", record)
    return calls


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env：最低收款额取默认的 200 仙（pay_orders 用到）。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：被测函数提交与回滚，两个会话共用同一个内存库连接（模拟并发的改动）。
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


@pytest.fixture
def admin_id(db: Session) -> int:
    account = AdminAccount(
        username="ops@example.com", password_hash="x" * 60, password_updated_at=CREATED
    )
    _add(db, account)
    db.commit()
    return account.id


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
        payment_expires_at=CREATED + timedelta(minutes=15),
        paid_at=CREATED if status == STATUS_PAID else None,
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
) -> _Placed:
    """游客订单（应付 6800 仙）：没有券与积分；写完即提交。"""
    order = _order_row(db, variant_id, status=status)
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
    alerted: bool = True,
) -> int:
    """直接写一条会话记录（默认已写告警时间），提交后返回记录 ID。"""
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
        "alerted_at": ALERTED if alerted else None,
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


def _retrieve_path(session_id: str) -> tuple[str, str]:
    return ("GET", f"{SESSIONS_PATH}/{session_id}")


def _expire_path(session_id: str) -> tuple[str, str]:
    return ("POST", f"{SESSIONS_PATH}/{session_id}/expire")


def _session_id(db: Session, checkout_id: int) -> str:
    session_id = _record(db, checkout_id).checkout_session_id
    assert session_id is not None
    return session_id


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


def _refunds(db: Session) -> list[tuple[int | None, str, int, str]]:
    db.rollback()
    refunds = db.scalars(select(StripeRefund).order_by(StripeRefund.id))
    return [(r.checkout_id, r.source_type, r.amount_sen, r.status) for r in refunds]


def _audits(db: Session) -> list[tuple[str, str | None, int | None, str | None]]:
    db.rollback()
    audits = db.scalars(select(AuditEvent).order_by(AuditEvent.id))
    return [(a.action, a.target_type, a.target_id, a.new_value) for a in audits]


def _state(db: Session, placed: _Placed) -> dict[str, Any]:
    """订单状态、库存、事件、用券记录、积分流水与退款尝试，用来断言订单一侧什么都没改。"""
    order_id = placed.order_id
    return {
        "status": _status(db, order_id),
        "stock": _stock(db, placed.variant_id),
        "events": _events(db, order_id),
        "coupon_uses": _coupon_uses(db, order_id),
        "entries": _entry_kinds(db, order_id),
        "refunds": _refunds(db),
    }


def _assert_voided(db: Session, placed: _Placed, checkout_id: int) -> None:
    """记录为 expired、活动列为空，订单为 demo_cancelled，库存加回，事件只有一条 admin 的取消。"""
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.alerted_at == ALERTED
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _stock(db, placed.variant_id) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [VOIDED_BY_ADMIN]


class _ChangingGateway(FakeStripeGateway):
    """查询会话时先由另一个数据库会话改动并提交（模拟 Stripe 处理期间 webhook 或定时任务的
    改动），再按编排返回。"""

    def __init__(self, change: Callable[[], None], *results: StripeResult[Any]) -> None:
        super().__init__(*results)
        self._change = change

    def retrieve_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]:
        self._change()
        return super().retrieve_checkout_session(session_id)


def _change_in_other_session(engine: Engine, stmt: Any) -> Callable[[], None]:
    def change() -> None:
        with Session(engine) as other:
            other.execute(stmt)
            other.commit()

    return change


def _commit_counter(db: Session) -> list[bool]:
    commits: list[bool] = []
    event.listen(db, "after_commit", lambda _session: commits.append(True))
    return commits


# ---------------------------------------------------------------------------
# 入口与前置条件
# ---------------------------------------------------------------------------


class _NoDatabase:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError("database accessed")


def test_aware_now_is_rejected_before_touching_the_database() -> None:
    """第 11 条（1）的处置以本站时间写库（「库里的时间一律是不带时区的 UTC」）；验收：now 带时区
    抛 ValueError，先于访问数据库、不调用 Stripe。"""
    gateway = FakeStripeGateway()
    no_db: Any = _NoDatabase()

    with pytest.raises(ValueError):
        payment.void_alerted_checkout(no_db, gateway, 1, NOW.replace(tzinfo=UTC))

    assert gateway.calls == []


def test_missing_record_raises_checkout_not_found(db: Session) -> None:
    """第 11 条（1）「服务端先以普通读取重查记录与订单」；验收：记录不存在抛 CheckoutNotFound，
    不调用 Stripe。"""
    gateway = FakeStripeGateway()

    with pytest.raises(payment.CheckoutNotFound):
        payment.void_alerted_checkout(db, gateway, 12345, NOW)

    assert gateway.calls == []


@pytest.mark.parametrize(
    ("record_status", "alerted", "order_status"),
    [
        pytest.param(CHECKOUT_OPEN, False, STATUS_AWAITING_PAYMENT, id="not-alerted"),
        pytest.param(CHECKOUT_EXPIRED, True, STATUS_AWAITING_PAYMENT, id="record-expired"),
        pytest.param(CHECKOUT_OPEN, True, STATUS_CANCELLED, id="order-cancelled"),
        pytest.param(CHECKOUT_OPEN, True, STATUS_PAID, id="order-paid"),
    ],
)
def test_record_no_longer_pending_review_changes_nothing(
    db: Session, variant: int, record_status: str, alerted: bool, order_status: str
) -> None:
    """第 11 条（1）「已写告警时间、状态不是 expired、所属订单仍为 awaiting_demo_payment 的
    StripeCheckout」「服务端先以普通读取重查记录与订单仍满足上述条件（不满足回答已变化……）」：
    返回 changed，不调用 Stripe，记录与订单都不变。"""
    placed = _guest_order(db, variant, status=order_status)
    checkout_id = _checkout(db, placed.order_id, record_status, alerted=alerted)
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)
    gateway = FakeStripeGateway()

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.CHANGED
    assert gateway.calls == []
    assert _record_state(db, checkout_id) == record_before
    assert _state(db, placed) == before


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------


def test_creating_is_voided_without_calling_stripe(db: Session, variant: int) -> None:
    """第 11 条（1）「没有会话 ID（creating）……不调用 Stripe，凭管理员的核实记为 expired 并取消
    订单」与「取消照现有规则加回库存、释放券与积分预占，订单事件的操作者为 admin」：会员订单的
    记录记为 expired、活动列置空，订单取消，库存、券与积分预占都释放，事件操作者为 admin，
    告警时间不清空；作废与取消一次提交。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    assert _coupon_uses(db, placed.order_id) == [USE_RESERVED]
    gateway = FakeStripeGateway()
    commits = _commit_counter(db)

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    assert gateway.calls == []
    assert commits == [True]
    _assert_voided(db, placed, checkout_id)
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]


def test_voided_order_shows_timeout_to_the_visitor(db: Session, variant: int) -> None:
    """「订单与退款状态」第 3 条「或由管理员按『Stripe 测试环境支付』第 11 条作废已告警的结账记录
    后取消（1.22，操作者为 admin……）」与 HANDOFF 0.90（对访客就是超时未付，不改 UX 与文案）：
    人工作废之后，支付页数据（pay_orders）的取消原因为 timeout。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    result = payment.void_alerted_checkout(db, FakeStripeGateway(), checkout_id, NOW)
    assert result is payment.VoidResult.VOIDED

    [view] = payment.pay_orders(db, [placed.order_id], "en", LATER)
    assert view.status == STATUS_CANCELLED
    assert view.cancelled_by == "timeout"


# ---------------------------------------------------------------------------
# 有会话 ID：Stripe 给出确定结果
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_paid_session_is_confirmed_not_cancelled(
    db: Session, variant: int, record_status: str
) -> None:
    """第 11 条（1）「有会话 ID（open、complete）：先按第 5 条向 Stripe 查询一次该会话。
    Stripe 给出确定结果时照第 4、5 条处理，不凭管理员的核实：已付款且相符就确认（订单进入
    demo_paid，不取消）」：只查询一次、不请求作废，返回 confirmed，事件操作者为 system，库存
    不加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, record_status)
    session_id = _session_id(db, checkout_id)
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(_ok(paid))
    calls: list[Session] = []

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW, on_void=calls.append)

    assert result is payment.VoidResult.CONFIRMED
    assert _paths(gateway) == [_retrieve_path(session_id)]
    assert calls == []
    assert _status(db, placed.order_id) == STATUS_PAID
    assert _record(db, checkout_id).confirmed_at == NOW
    assert _stock(db, variant) == RESERVED_STOCK
    assert _events(db, placed.order_id) == [PAID_BY_SYSTEM]


def test_mismatched_payment_refunded_then_order_cancelled(db: Session, variant: int) -> None:
    """第 11 条（1）「已付款而金额或币种不符、照第 6 条写成了全额退款尝试（记录随之记为 expired），
    就接着取消订单，退款照第 8 条继续」：返回 cancelled，有一条来源为该会话的 pending 退款尝试，
    订单以 system 取消，库存加回。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE)
    mismatched = _session(
        db, checkout_id, "complete", payment_status=PAYMENT_PAID, amount_total=SUBTOTAL
    )
    gateway = FakeStripeGateway(_ok(mismatched))
    calls: list[Session] = []

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW, on_void=calls.append)

    assert result is payment.VoidResult.CANCELLED
    assert calls == []
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _refunds(db) == [(checkout_id, SOURCE_CHECKOUT, SUBTOTAL, STRIPE_REFUND_PENDING)]
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]


def test_open_session_is_expired_then_cancelled(db: Session, variant: int) -> None:
    """第 11 条（1）「open 就请求作废，作废成功或会话已过期才取消」：查询为 open 后请求作废一次，
    返回的会话为 expired：记录记为 expired，订单以 system 取消，返回 cancelled。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _session_id(db, checkout_id)
    gateway = FakeStripeGateway(
        _ok(_session(db, checkout_id, "open")), _ok(_session(db, checkout_id, "expired"))
    )

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.CANCELLED
    assert _paths(gateway) == [_retrieve_path(session_id), _expire_path(session_id)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _stock(db, variant) == INITIAL_STOCK
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]


def test_expired_session_is_cancelled_without_expire_request(db: Session, variant: int) -> None:
    """第 11 条（1）「已过期就取消」：查询为 expired 时不请求作废，记录记为 expired，订单以 system
    取消，返回 cancelled。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _session_id(db, checkout_id)
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "expired")))

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.CANCELLED
    assert _paths(gateway) == [_retrieve_path(session_id)]
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert _coupon_uses(db, placed.order_id) == [USE_RELEASED]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_RELEASE]
    assert _events(db, placed.order_id) == [CANCELLED_BY_SYSTEM]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        pytest.param("order-paid", payment.VoidResult.CONFIRMED, id="kept-paid"),
        pytest.param("order-cancelled", payment.VoidResult.CHANGED, id="kept-cancelled"),
        pytest.param("record-paid", payment.VoidResult.CHANGED, id="changed"),
    ],
)
def test_end_result_after_stripe_is_mapped(
    engine: Engine, db: Session, variant: int, change: str, expected: payment.VoidResult
) -> None:
    """第 11 条「记录或订单在查询 Stripe 之后被 webhook、确认或定时任务改过，就不改、回滚并回答
    已变化」；验收：cancel_after_stripe_end 返回 changed 时返回 changed，返回 kept 时按订单当前
    状态（demo_paid 为 confirmed，其他为 changed）——查询为 expired，其间订单已被付款或
    取消，或记录的付款状态已改为 paid。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    expired = _session(db, checkout_id, "expired")
    if change == "record-paid":
        stmt = (
            update(StripeCheckout)
            .where(StripeCheckout.id == checkout_id)
            .values(payment_status=PAYMENT_PAID)
        )
    elif change == "order-paid":
        stmt = (
            update(Order).where(Order.id == placed.order_id).values(status=STATUS_PAID, paid_at=NOW)
        )
    else:
        stmt = update(Order).where(Order.id == placed.order_id).values(status=STATUS_CANCELLED)
    gateway = _ChangingGateway(_change_in_other_session(engine, stmt), _ok(expired))

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is expected
    assert _events(db, placed.order_id) == []
    assert _stock(db, variant) == RESERVED_STOCK


# ---------------------------------------------------------------------------
# 有会话 ID：给不出确定结果时凭核实作废
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", ["unavailable", "other-create-key", "other-session-id", "livemode", "unknown-status"]
)
def test_undetermined_session_is_voided(db: Session, variant: int, case: str) -> None:
    """第 11 条（1）「只有 Stripe 不可用、答复冲突……时，才凭管理员的核实把记录记为 expired 并取消
    订单」；验收：查询不成，或会话 ID、元数据里的创建幂等键与记录不同、不是 livemode 为假，或
    状态不认识时为给不出确定结果：只查询一次，作废记录、以 admin 取消订单，返回 voided。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _session_id(db, checkout_id)
    answers = {
        "unavailable": UNAVAILABLE,
        "other-create-key": _ok(_session(db, checkout_id, "expired", create_key="other")),
        "other-session-id": _ok(_session(db, checkout_id, "expired", id="cs_test_other")),
        "livemode": StripeResult(
            StripeOutcome.LIVE, _session(db, checkout_id, "expired", livemode=True)
        ),
        "unknown-status": _ok(_session(db, checkout_id, "weird")),
    }
    gateway = FakeStripeGateway(answers[case])

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    assert _paths(gateway) == [_retrieve_path(session_id)]
    _assert_voided(db, placed, checkout_id)


@pytest.mark.parametrize("expire_answer", ["unavailable", "still-open"])
def test_open_session_that_cannot_be_expired_is_voided(
    db: Session, variant: int, expire_answer: str
) -> None:
    """第 11 条（1）「open 就请求作废，作废成功或会话已过期才取消」「只有 Stripe 不可用、
    答复冲突……时，才凭管理员的核实……」；验收：查询为 open 而作废请求不成或返回的会话不是
    expired 时为给不出确定结果，不再查询第二次，凭核实作废。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _session_id(db, checkout_id)
    still_open = _ok(_session(db, checkout_id, "open"))
    expire = UNAVAILABLE if expire_answer == "unavailable" else still_open
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, "open")), expire)

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    assert _paths(gateway) == [_retrieve_path(session_id), _expire_path(session_id)]
    _assert_voided(db, placed, checkout_id)


@pytest.mark.parametrize("record_status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_complete_with_undetermined_payment_is_voided(
    db: Session, variant: int, record_status: str
) -> None:
    """第 11 条（1）「只有……会话 complete 而付款仍未定……时，才凭管理员的核实把记录记为 expired 并
    取消订单」；验收：confirm_checkout 返回 pending 时凭核实作废，作废前重新读取记录（open
    的记录已被确认改为 complete）。未付款的会话没有展开的 PaymentIntent，确认会再查询一次；不请求
    作废。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, record_status)
    session_id = _session_id(db, checkout_id)
    unpaid = _session(db, checkout_id, "complete")
    gateway = FakeStripeGateway(_ok(unpaid), _ok(unpaid))

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    assert _paths(gateway) == [_retrieve_path(session_id), _retrieve_path(session_id)]
    _assert_voided(db, placed, checkout_id)


def test_paid_but_unrefundable_is_voided_without_refund(db: Session, variant: int) -> None:
    """第 11 条（1）「或 Stripe 答复已付款而照第 6 条仍写不成退款尝试时，才凭管理员的核实把记录
    记为 expired 并取消订单；最后一种不建退款尝试」：会话已付款而缺 amount_total，confirm_checkout
    返回 needs_review；付款状态已为 paid 仍作废，没有退款尝试，事件操作者为 admin。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    unrefundable = _session(
        db, checkout_id, "complete", payment_status=PAYMENT_PAID, amount_total=None
    )
    gateway = FakeStripeGateway(_ok(unrefundable))

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    _assert_voided(db, placed, checkout_id)
    assert _record(db, checkout_id).payment_status == PAYMENT_PAID
    assert _refunds(db) == []


def test_unverifiable_confirmation_is_voided(db: Session, variant: int) -> None:
    """第 11 条（1）「只有 Stripe 不可用、答复冲突……时，才凭管理员的核实……」；验收：
    confirm_checkout 返回 unavailable（会话仍 open 却答复已付款，取不到可核对的对象）且读取时
    付款状态不是 paid：凭核实作废；查到已付款时不请求作废。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    session_id = _session_id(db, checkout_id)
    conflicting = _session(db, checkout_id, "open", payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(_ok(conflicting))

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW)

    assert result is payment.VoidResult.VOIDED
    assert _paths(gateway) == [_retrieve_path(session_id)]
    _assert_voided(db, placed, checkout_id)


@pytest.mark.parametrize("case", ["unavailable", "conflict"])
def test_locally_paid_record_is_not_voided_when_stripe_has_no_answer(
    db: Session, variant: int, case: str
) -> None:
    """第 11 条（1）「本站记录的付款状态已是 paid 而这次 Stripe 不可用或答复冲突时，不凭核实作废，
    回答 Stripe 暂不可用、请稍后再试」：返回 unavailable，记录与订单都不变。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID)
    if case == "unavailable":
        answer = UNAVAILABLE
    else:
        answer = _ok(_session(db, checkout_id, "complete", create_key="other"))
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)
    gateway = FakeStripeGateway(answer)
    calls: list[Session] = []

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW, on_void=calls.append)

    assert result is payment.VoidResult.UNAVAILABLE
    assert calls == []
    assert _record_state(db, checkout_id) == record_before
    assert _state(db, placed) == before


@pytest.mark.parametrize("change", ["record-complete", "record-paid", "order-cancelled"])
def test_change_during_stripe_call_is_reported(
    engine: Engine, db: Session, variant: int, change: str
) -> None:
    """第 11 条「记为 expired 与取消订单在同一事务里，按统一的加锁顺序『会员 → 订单 → Stripe 会话
    记录』重查：记录或订单在查询 Stripe 之后被 webhook、确认或定时任务改过，就不改、回滚并回答
    已变化」：查询 Stripe 期间记录被改为 complete、付款状态被改为 paid，或订单已被取消，而
    Stripe 不可用：返回 changed，不作废、不写 admin 事件。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    if change == "record-complete":
        stmt = (
            update(StripeCheckout)
            .where(StripeCheckout.id == checkout_id)
            .values(status=CHECKOUT_COMPLETE, completed_at=NOW, checkout_url=None)
        )
    elif change == "record-paid":
        stmt = (
            update(StripeCheckout)
            .where(StripeCheckout.id == checkout_id)
            .values(payment_status=PAYMENT_PAID)
        )
    else:
        stmt = update(Order).where(Order.id == placed.order_id).values(status=STATUS_CANCELLED)
    gateway = _ChangingGateway(_change_in_other_session(engine, stmt), UNAVAILABLE)
    calls: list[Session] = []

    result = payment.void_alerted_checkout(db, gateway, checkout_id, NOW, on_void=calls.append)

    assert result is payment.VoidResult.CHANGED
    assert calls == []
    assert _record(db, checkout_id).active_order_id == placed.order_id
    assert _events(db, placed.order_id) == []
    assert _stock(db, variant) == RESERVED_STOCK


# ---------------------------------------------------------------------------
# on_void：审计与作废同一事务
# ---------------------------------------------------------------------------


def test_on_void_runs_in_the_void_transaction(db: Session, variant: int, admin_id: int) -> None:
    """第 11 条「并写一条审计记录（操作名 stripe_checkout_voided，对象为该结账记录，新值为处理结果
    ……voided……）」；验收：on_void 在 flush 之后、提交之前以同一会话调用，这时已看得到记录为
    expired、订单为 demo_cancelled；它写的审计与作废一次提交。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    seen: list[tuple[str, str]] = []
    commits = _commit_counter(db)

    def write_audit(session: Session) -> None:
        assert session is db
        record_stmt = select(StripeCheckout.status).where(StripeCheckout.id == checkout_id)
        order_stmt = select(Order.status).where(Order.id == placed.order_id)
        seen.append((session.scalars(record_stmt).one(), session.scalars(order_stmt).one()))
        assert commits == []
        session.add(
            AuditEvent(
                occurred_at=NOW,
                admin_account_id=admin_id,
                action="stripe_checkout_voided",
                target_type="stripe_checkout",
                target_id=checkout_id,
                new_value="voided",
            )
        )

    result = payment.void_alerted_checkout(
        db, FakeStripeGateway(UNAVAILABLE), checkout_id, NOW, on_void=write_audit
    )

    assert result is payment.VoidResult.VOIDED
    assert seen == [(CHECKOUT_EXPIRED, STATUS_CANCELLED)]
    assert commits == [True]
    assert _audits(db) == [("stripe_checkout_voided", "stripe_checkout", checkout_id, "voided")]
    _assert_voided(db, placed, checkout_id)


def test_on_void_failure_rolls_back_the_void(db: Session, variant: int) -> None:
    """第 11 条「并写一条审计记录」；验收：on_void 抛出时回滚、异常原样传出，作废随之不生效——记录
    仍占活动位、状态不变，订单仍待支付，库存、券与积分预占都不释放，没有事件。"""
    placed = _member_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_CREATING)
    record_before = _record_state(db, checkout_id)
    before = _state(db, placed)

    class AuditFailed(Exception):
        pass

    def fail(_session: Session) -> None:
        raise AuditFailed

    with pytest.raises(AuditFailed):
        payment.void_alerted_checkout(db, FakeStripeGateway(), checkout_id, NOW, on_void=fail)

    assert _record_state(db, checkout_id) == record_before
    assert _state(db, placed) == before


# ---------------------------------------------------------------------------
# 作废之后的付款与现有入口
# ---------------------------------------------------------------------------


def test_payment_after_void_is_refunded_and_order_stays_cancelled(
    db: Session, variant: int
) -> None:
    """第 11 条「凭核实作废之后，若这个会话仍被付款（Stripe 不可用时会话可能仍可付款）……webhook 与
    确认照第 4、6 条按迟到付款全额退款，订单保持已取消」：作废之后同一会话已付款，
    confirm_checkout 返回 refunded，写一条全额的 pending 退款尝试，订单仍为 demo_cancelled，
    不再写事件。"""
    placed = _guest_order(db, variant)
    checkout_id = _checkout(db, placed.order_id, CHECKOUT_OPEN)
    voided = payment.void_alerted_checkout(db, FakeStripeGateway(UNAVAILABLE), checkout_id, NOW)
    assert voided is payment.VoidResult.VOIDED
    total = SUBTOTAL + SHIPPING
    paid = _session(db, checkout_id, "complete", payment_status=PAYMENT_PAID)

    result = confirm_checkout(db, FakeStripeGateway(), checkout_id, paid, LATER)

    assert result is ConfirmResult.REFUNDED
    assert _refunds(db) == [(checkout_id, SOURCE_CHECKOUT, total, STRIPE_REFUND_PENDING)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _status(db, placed.order_id) == STATUS_CANCELLED
    assert _events(db, placed.order_id) == [VOIDED_BY_ADMIN]
    assert _stock(db, variant) == INITIAL_STOCK


def test_existing_cancel_entries_still_reject_admin(db: Session, variant: int) -> None:
    """第 11 条「凭核实作废是新的取消路径：现有的取消入口只接受访客、会员与 system 作为
    操作者……实现时为本项另写服务层操作（操作者为 admin，只在本项的条件下允许），不放宽现有
    入口」：cancel_after_stripe_end 与 cancel_order 以 admin 调用仍抛 ValueError，订单不变。"""
    placed = _guest_order(db, variant)
    _checkout(db, placed.order_id, CHECKOUT_OPEN)

    with pytest.raises(ValueError):
        payment.cancel_after_stripe_end(db, placed.order_id, ACTOR_ADMIN, NOW)
    admin: Any = ACTOR_ADMIN
    with pytest.raises(ValueError):
        payment.cancel_order(db, placed.order_id, NOW, actor=admin)

    assert _status(db, placed.order_id) == STATUS_AWAITING_PAYMENT

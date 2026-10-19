"""确认 Stripe 测试付款与补齐付款方式类型（app/services/stripe_confirm.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 1、3、4、6 条，以及
docs/HANDOFF.md 0.53 记录的核验结论（付款方式类型取 PaymentIntent 的
latest_charge.payment_method_details.type）。每条测试（参数化的测试是每个用例）的文档
字符串写明它守住的设计原句或核验结论；只出自 SHOP-TASK-113 验收标准的约定注明「验收：」。
第 6 条的迟到与不符的付款本任务只判定、返回 needs_review，退款由 SHOP-TASK-117 实现。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；会员订单直接
按模型写入，券与积分经 reserve_coupon、reserve_points 预占，来源批次经 earn_points。Stripe
一律用 SHOP-TASK-111 的 FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会
提交与回滚，测试数据都先提交。SQLite 不支持行锁，加锁顺序只按代码与 MySQL 行为审阅，这里
不验证。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Coupon,
    CouponUse,
    Member,
    Order,
    OrderEvent,
    PaymentAttempt,
    PointEntry,
    PointLot,
    StripeCheckout,
)
from app.models.coupon import TYPE_FIXED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_SYSTEM,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.points import KIND_EARN, KIND_REDEEM
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
from app.services.stripe_confirm import (
    LOG_CONFIRM_REJECTED,
    LOG_LATE_PAYMENT,
    LOG_LIVEMODE,
    LOG_SESSION_MISMATCH,
    ConfirmResult,
    FillResult,
    confirm_checkout,
    fill_payment_method_type,
)
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeError,
    StripeOutcome,
    StripeResult,
)

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起，5 分钟时确认。
CREATED = datetime(2026, 10, 18, 2, 0)
ORDER_EXPIRES = CREATED + timedelta(minutes=15)
STARTED = CREATED + timedelta(minutes=1)
NOW = CREATED + timedelta(minutes=5)
# 已过 15 分钟支付到期时间（会话仍在 Stripe 到期时间之内）。
AFTER_ORDER_EXPIRY = ORDER_EXPIRES + timedelta(minutes=10)
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)
# 会员来源批次的取得时间：在上面这些时刻都未过期。
EARNED_AT = datetime(2026, 9, 1, 4, 0)
SOURCE_POINTS = 1000

# 商品小计 6000 仙、运费 800 仙；会员订单另有券 1000 仙、积分抵扣 500 与获得积分快照 45。
SUBTOTAL = 6000
SHIPPING = 800
COUPON_OFF = 1000
REDEEMED = 500
EARNED = 45

FINGERPRINT = "0f" * 32
SESSIONS_PATH = "/v1/checkout/sessions"
EXPAND = (("expand[]", "payment_intent.latest_charge"),)
LOGGER = "app.services.stripe_confirm"

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：被测函数提交与回滚，两个会话共用同一个内存库连接（模拟并发的确认）。
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


@dataclass(frozen=True)
class _Placed:
    order_id: int
    member_id: int | None


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _order_row(
    db: Session,
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
    return _add(db, order)


def _guest_order(db: Session, *, status: str = STATUS_AWAITING_PAYMENT) -> _Placed:
    """游客订单：没有券与积分，快照都为 0；写完即提交。"""
    order = _order_row(db, status=status)
    db.commit()
    return _Placed(order.id, None)


def _member_order(db: Session) -> _Placed:
    """会员订单：来源批次 1000 积分（另一张已支付订单所得），本单预占券 1000 仙与积分 500，
    获得积分快照 45；写完即提交。
    """
    phone = f"+60123{next(_SEQUENCE):06d}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=EARNED_AT))
    source = _order_row(db, member_id=member.id, status=STATUS_PAID, placed_at=EARNED_AT)
    assert earn_points(db, member.id, source.id, SOURCE_POINTS, EARNED_AT) is not None
    order = _order_row(
        db, member_id=member.id, coupon_off=COUPON_OFF, redeemed=REDEEMED, earned=EARNED
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
    return _Placed(order.id, member.id)


def _checkout(
    db: Session,
    placed: _Placed,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），提交后返回记录 ID。"""
    n = next(_SEQUENCE)
    total = db.scalars(select(Order.total_sen).where(Order.id == placed.order_id)).one()
    fields: dict[str, Any] = {
        "order_id": placed.order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": total,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
        "expires_at": STRIPE_EXPIRES,
        "created_at": STARTED,
        "active_order_id": None if status == CHECKOUT_EXPIRED else placed.order_id,
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


def _intent(
    intent_id: str, method: str | None = "card", *, livemode: bool | None = False
) -> PaymentIntentInfo:
    """展开的 PaymentIntent；method 为 latest_charge.payment_method_details.type，None 表示
    latest_charge 缺失。"""
    return PaymentIntentInfo(
        id=intent_id, status="succeeded", livemode=livemode, payment_method_type=method
    )


def _object(db: Session, checkout_id: int, **overrides: Any) -> CheckoutSession:
    """与记录、订单全部相符的已付款会话对象（payment_intent 已展开、方式为 card）；
    overrides 改其中的字段。"""
    record = _record(db, checkout_id)
    total = db.scalars(select(Order.total_sen).where(Order.id == record.order_id)).one()
    fields: dict[str, Any] = {
        "id": record.checkout_session_id or f"cs_test_for_{checkout_id}",
        "status": "complete",
        "payment_status": PAYMENT_PAID,
        "livemode": False,
        "currency": "myr",
        "amount_total": total,
        "client_reference_id": str(record.order_id),
        "order_id": str(record.order_id),
        "create_key": record.idempotency_key,
        "url": None,
        "payment_intent": _intent(f"pi_test_{checkout_id}"),
    }
    fields.update(overrides)
    db.rollback()
    return CheckoutSession(**fields)


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _record_state(db: Session, checkout_id: int) -> dict[str, Any]:
    record = _record(db, checkout_id)
    return {name: getattr(record, name) for name in StripeCheckout.__table__.columns.keys()}


def _world(db: Session, placed: _Placed, checkout_id: int) -> dict[str, Any]:
    """订单、记录、事件、支付尝试、用券记录与积分流水的全部状态，用来断言什么都没改。"""
    order_id = placed.order_id
    return {
        "order": _order(db, order_id),
        "record": _record_state(db, checkout_id),
        "events": _events(db, order_id),
        "attempts": _attempt_count(db, order_id),
        "coupon_uses": _coupon_uses(db, order_id),
        "entries": _entry_kinds(db, order_id),
    }


def _order(db: Session, order_id: int) -> tuple[str, datetime | None]:
    """（状态、支付时间）。"""
    db.rollback()
    stmt = select(Order.status, Order.paid_at).where(Order.id == order_id)
    status, paid_at = db.execute(stmt).one()
    return status, paid_at


def _events(db: Session, order_id: int) -> list[tuple[str | None, str, str]]:
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _attempt_count(db: Session, order_id: int) -> int:
    stmt = select(PaymentAttempt.id).where(PaymentAttempt.order_id == order_id)
    return len(db.scalars(stmt).all())


def _coupon_uses(db: Session, order_id: int) -> list[str]:
    return list(db.scalars(select(CouponUse.status).where(CouponUse.order_id == order_id)))


def _entry_kinds(db: Session, order_id: int) -> list[str]:
    stmt = select(PointEntry.kind).where(PointEntry.order_id == order_id).order_by(PointEntry.id)
    return list(db.scalars(stmt))


def _lots(db: Session, order_id: int) -> list[tuple[int, int]]:
    stmt = select(PointLot.member_id, PointLot.points).where(PointLot.order_id == order_id)
    return [tuple(row) for row in db.execute(stmt)]


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def _confirm(
    db: Session,
    gateway: FakeStripeGateway,
    checkout_id: int,
    stripe_session: CheckoutSession | None = None,
    now: datetime = NOW,
) -> ConfirmResult:
    return confirm_checkout(db, gateway, checkout_id, stripe_session, now)


class _HookGateway(FakeStripeGateway):
    """替身之上，在查询调用发生时先执行 hook（看数据库此刻的样子）。"""

    def __init__(self, hook: Callable[[], None], *results: StripeResult) -> None:
        super().__init__(*results)
        self._hook = hook

    def retrieve_checkout_session(self, session_id: str) -> StripeResult:
        self._hook()
        return super().retrieve_checkout_session(session_id)


# ---------------------------------------------------------------------------
# 确认：订单进入 demo_paid
# ---------------------------------------------------------------------------


def test_guest_order_is_confirmed_by_a_verified_object(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 4 条「全部相符且订单仍为 `awaiting_demo_payment` 时，按模拟支付成功的同一事务进入
    `demo_paid`……并记下 PaymentIntent ID，以及……付款方式类型」；验收：「事件操作者为
    system」「不写 PaymentAttempt」「同一事务里写该记录的确认时间」：webhook 给出已展开的
    对象时不调用 Stripe；订单为 demo_paid、支付时间为 now；只有一条 system 事件、没有支付
    尝试；记录为 complete，写完成时间、确认时间、付款状态、PaymentIntent ID 与方式 card。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    stripe_session = _object(db, checkout_id)
    gateway = FakeStripeGateway()

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, gateway, checkout_id, stripe_session)

    assert result is ConfirmResult.PAID
    assert gateway.calls == []
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)
    assert _events(db, placed.order_id) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]
    assert _attempt_count(db, placed.order_id) == 0
    record = _record(db, checkout_id)
    assert record.status == CHECKOUT_COMPLETE
    assert record.completed_at == NOW
    assert record.confirmed_at == NOW
    assert record.payment_status == PAYMENT_PAID
    assert record.payment_intent_id == f"pi_test_{checkout_id}"
    assert record.payment_method_type == "card"
    assert record.active_order_id == placed.order_id
    assert _messages(caplog) == []


def test_member_order_confirmation_uses_the_coupon_and_earns_points(db: Session) -> None:
    """第 4 条「按模拟支付成功的同一事务进入 `demo_paid`（消耗库存预留、核销用券记录、按快照
    发放获得积分）」：会员订单确认后用券记录为 used，按快照 45 积分写批次与 earn 流水，事件
    操作者为 system。"""
    placed = _member_order(db)
    checkout_id = _checkout(db, placed)
    assert _coupon_uses(db, placed.order_id) == [USE_RESERVED]

    result = _confirm(db, FakeStripeGateway(), checkout_id, _object(db, checkout_id))

    assert result is ConfirmResult.PAID
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)
    assert _coupon_uses(db, placed.order_id) == [USE_USED]
    assert _lots(db, placed.order_id) == [(placed.member_id, EARNED)]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_EARN]
    assert _events(db, placed.order_id) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]


def test_confirmation_ignores_the_15_minute_payment_expiry(db: Session) -> None:
    """第 4 条「确认不看订单的 15 分钟支付到期时间，只看订单是否仍待支付……即使确认晚于
    15 分钟也照常确认，不当作迟到款」：已过支付到期时间、仍待支付的订单照常进入 demo_paid，
    支付时间是本站确认的时间。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)

    result = _confirm(
        db, FakeStripeGateway(), checkout_id, _object(db, checkout_id), AFTER_ORDER_EXPIRY
    )

    assert result is ConfirmResult.PAID
    assert _order(db, placed.order_id) == (STATUS_PAID, AFTER_ORDER_EXPIRY)
    assert _record(db, checkout_id).confirmed_at == AFTER_ORDER_EXPIRY


def test_repeated_confirmation_returns_paid_without_settling_again(db: Session) -> None:
    """第 4 条「同一会话或同一 PaymentIntent 的重复确认（订单已由它付款）直接返回原结果，不退款、
    不重复发放」；验收：「该记录已有确认时间……时返回 paid，不再迁移、核销或发放」：同一对象
    再给一次、再按查询确认一次，都返回 paid；事件、积分批次与 earn 流水各只有一条，确认时间
    与支付时间不变。"""
    placed = _member_order(db)
    checkout_id = _checkout(db, placed)
    stripe_session = _object(db, checkout_id)
    gateway = FakeStripeGateway(_ok(stripe_session))

    first = _confirm(db, gateway, checkout_id, stripe_session)
    again = _confirm(db, gateway, checkout_id, stripe_session, NOW + timedelta(minutes=1))
    queried = _confirm(db, gateway, checkout_id, None, NOW + timedelta(minutes=2))

    assert [first, again, queried] == [ConfirmResult.PAID] * 3
    assert len(gateway.calls) == 1
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)
    assert _events(db, placed.order_id) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]
    assert _lots(db, placed.order_id) == [(placed.member_id, EARNED)]
    assert _entry_kinds(db, placed.order_id) == [KIND_REDEEM, KIND_EARN]
    assert _record(db, checkout_id).confirmed_at == NOW


def test_concurrent_confirmations_transition_only_once(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：「并发的两次确认只有一次迁移（带条件的 UPDATE 保证）」；第 4 条「不重复发放」：
    本次确认加锁重查之后、带条件的 UPDATE 之前，另一会话先提交了同一会话的确认；本次 UPDATE
    未命中、回滚重查，见到确认时间后返回 paid。只有一条 system 事件与一个积分批次，用券只
    核销一次。"""
    placed = _member_order(db)
    checkout_id = _checkout(db, placed)
    stripe_session = _object(db, checkout_id)
    real = payment.is_transition_allowed
    started: list[bool] = []
    done: list[ConfirmResult] = []

    def other_confirms_first(current: str, target: str, actor: str) -> bool:
        # 只插入一次；插入的那次确认自己也经过这里，先记下已开始，免得递归。
        if not started:
            started.append(True)
            with Session(engine) as other:
                gateway = FakeStripeGateway()
                done.append(confirm_checkout(other, gateway, checkout_id, stripe_session, NOW))
        return real(current, target, actor)

    monkeypatch.setattr(payment, "is_transition_allowed", other_confirms_first)

    later = NOW + timedelta(seconds=1)
    result = _confirm(db, FakeStripeGateway(), checkout_id, stripe_session, later)

    assert done == [ConfirmResult.PAID]
    assert result is ConfirmResult.PAID
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)
    assert _events(db, placed.order_id) == [(STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_SYSTEM)]
    assert _lots(db, placed.order_id) == [(placed.member_id, EARNED)]
    assert _coupon_uses(db, placed.order_id) == [USE_USED]
    assert _record(db, checkout_id).confirmed_at == NOW


def test_stripe_is_queried_without_a_transaction(db: Session) -> None:
    """验收：「先以普通读取得记录与订单，查询 Stripe 时不持有任何锁」：查询发生时会话不在
    事务里，订单仍待支付；查询之后照常确认。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    seen: list[bool] = []
    queried = _ok(_object(db, checkout_id))
    gateway = _HookGateway(lambda: seen.append(db.in_transaction()), queried)

    result = _confirm(db, gateway, checkout_id)

    assert seen == [False]
    assert result is ConfirmResult.PAID


# ---------------------------------------------------------------------------
# 付款方式类型
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["card", "fpx", "grabpay"])
def test_payment_method_type_comes_from_the_latest_charge(db: Session, method: str) -> None:
    """第 4 条「从该 PaymentIntent 实际成功的那笔 Charge 读取的付款方式类型
    （`payment_method_details.type`，如 card、fpx、grabpay）」；核验结论「付款方式类型取
    PaymentIntent 的 latest_charge.payment_method_details.type」：没有给出对象时按记录的会话
    ID 查询一次（expand 为 payment_intent.latest_charge），记下查询到的类型。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    session_id = _record(db, checkout_id).checkout_session_id
    queried = _object(db, checkout_id, payment_intent=_intent("pi_test_x", method))
    gateway = FakeStripeGateway(_ok(queried))

    result = _confirm(db, gateway, checkout_id)

    assert result is ConfirmResult.PAID
    [call] = gateway.calls
    assert (call.method, call.path, call.form) == ("GET", f"{SESSIONS_PATH}/{session_id}", EXPAND)
    record = _record(db, checkout_id)
    assert record.payment_method_type == method
    assert record.payment_intent_id == "pi_test_x"


def test_missing_latest_charge_confirms_with_empty_type_then_fills_it(db: Session) -> None:
    """第 4 条「读不到时留空，不用会话的可用方式列表代替，由定时任务之后向 Stripe 重查补齐
    （只补这一字段，不再执行付款成功的状态迁移）」；验收：「查询后已付款而 latest_charge 仍
    缺失时照常确认、付款方式类型留空待补齐」：给出的对象缺 latest_charge 先查询一次，仍缺失
    时照常确认、类型为空；补齐函数查到 fpx 后只写这一列，订单、事件与积分都不变；再补一次
    不调用 Stripe。"""
    placed = _member_order(db)
    checkout_id = _checkout(db, placed)
    without_charge = _object(db, checkout_id, payment_intent=_intent("pi_test_1", None))
    gateway = FakeStripeGateway(_ok(without_charge))

    result = _confirm(db, gateway, checkout_id, without_charge)

    assert result is ConfirmResult.PAID
    assert len(gateway.calls) == 1
    assert _record(db, checkout_id).payment_method_type is None
    before = _world(db, placed, checkout_id)

    with_charge = _object(db, checkout_id, payment_intent=_intent("pi_test_1", "fpx"))
    filler = FakeStripeGateway(_ok(with_charge))
    filled = fill_payment_method_type(db, filler, checkout_id)

    assert filled is FillResult.FILLED
    [call] = filler.calls
    assert call.form == EXPAND
    after = _world(db, placed, checkout_id)
    assert after["record"]["payment_method_type"] == "fpx"
    after["record"]["payment_method_type"] = None
    assert after == before

    again = FakeStripeGateway()
    assert fill_payment_method_type(db, again, checkout_id) is FillResult.SKIPPED
    assert again.calls == []


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param(UNAVAILABLE, FillResult.UNAVAILABLE, id="unavailable"),
        pytest.param(
            StripeResult(StripeOutcome.REJECTED, error=StripeError(404, "invalid_request_error")),
            FillResult.UNAVAILABLE,
            id="rejected",
        ),
        pytest.param(None, FillResult.MISSING, id="still-missing"),
    ],
)
def test_fill_that_cannot_read_the_type_changes_nothing(
    db: Session, result: StripeResult | None, expected: FillResult
) -> None:
    """第 4 条「由定时任务之后向 Stripe 重查补齐」；验收：「unavailable 时留待下次」：查询
    不成或 latest_charge 仍缺失时不改记录，类型仍为空，下次再查。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    without_charge = _object(db, checkout_id, payment_intent=_intent("pi_test_1", None))
    assert _confirm(db, FakeStripeGateway(_ok(without_charge)), checkout_id) is ConfirmResult.PAID
    before = _world(db, placed, checkout_id)

    gateway = FakeStripeGateway(_ok(without_charge) if result is None else result)
    filled = fill_payment_method_type(db, gateway, checkout_id)

    assert filled is expected
    assert len(gateway.calls) == 1
    assert _world(db, placed, checkout_id) == before


def test_fill_skips_an_unpaid_record(db: Session) -> None:
    """验收：「对已付款而付款方式类型为空的记录向 Stripe 查询一次补上」：未付款的记录不查询、
    不改。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    gateway = FakeStripeGateway()

    assert fill_payment_method_type(db, gateway, checkout_id) is FillResult.SKIPPED
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# 尚未付款与取不到可核对的对象
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["open", "expired"])
def test_unpaid_session_is_pending_and_changes_nothing(
    db: Session, status: str, caplog: pytest.LogCaptureFixture
) -> None:
    """第 4 条「尚未确认时结果页显示『确认中』并稍后重查」；验收：「payment_status 不是 paid
    时返回 pending，订单不变」「会话报告 expired 而未付款时确认不改记录与订单、返回
    pending」：open 或 expired 而未付款（没有 PaymentIntent）的会话返回 pending，什么都不改、
    不记日志。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    unpaid = _object(
        db, checkout_id, status=status, payment_status=PAYMENT_UNPAID, payment_intent=None
    )
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, FakeStripeGateway(_ok(unpaid)), checkout_id)

    assert result is ConfirmResult.PENDING
    assert _world(db, placed, checkout_id) == before
    assert _messages(caplog) == []


def test_complete_but_unpaid_session_updates_only_the_record(db: Session) -> None:
    """验收：「按会话更新记录的状态与付款状态（只把 open 或 creating 更新为 complete，complete
    时写完成时间）」「payment_status 不是 paid 时返回 pending，订单不变」：complete 而未付款
    的会话把记录改为 complete、付款状态 unpaid，不写确认时间，订单仍待支付。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    unpaid = _object(db, checkout_id, payment_status=PAYMENT_UNPAID)

    result = _confirm(db, FakeStripeGateway(_ok(unpaid)), checkout_id)

    assert result is ConfirmResult.PENDING
    record = _record(db, checkout_id)
    assert record.status == CHECKOUT_COMPLETE
    assert record.completed_at == NOW
    assert record.payment_status == PAYMENT_UNPAID
    assert record.confirmed_at is None
    assert _order(db, placed.order_id) == (STATUS_AWAITING_PAYMENT, None)
    assert _events(db, placed.order_id) == []


@pytest.mark.parametrize("given", [True, False], ids=["given", "queried"])
def test_open_session_reported_paid_is_unavailable(db: Session, given: bool) -> None:
    """验收：「会话状态不是 complete 而 payment_status 为 paid（Stripe 正常不会这样给出）视为
    取不到可核对的对象……不改订单与记录、返回 unavailable」：给出或查询到 open 而已付款的
    对象都不改任何东西；给出的已展开对象不再查询。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    odd = _object(db, checkout_id, status="open")
    before = _world(db, placed, checkout_id)
    gateway = FakeStripeGateway() if given else FakeStripeGateway(_ok(odd))

    result = _confirm(db, gateway, checkout_id, odd if given else None)

    assert result is ConfirmResult.UNAVAILABLE
    assert len(gateway.calls) == (0 if given else 1)
    assert _world(db, placed, checkout_id) == before


def test_paid_session_without_payment_intent_is_unavailable(db: Session) -> None:
    """验收：「已付款却仍缺 PaymentIntent 时无法核对其 livemode，不改订单与记录、返回
    unavailable」：查询之后仍没有 PaymentIntent 的已付款会话不改任何东西。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    without_intent = _object(db, checkout_id, payment_intent=None)
    before = _world(db, placed, checkout_id)

    result = _confirm(db, FakeStripeGateway(_ok(without_intent)), checkout_id)

    assert result is ConfirmResult.UNAVAILABLE
    assert _world(db, placed, checkout_id) == before


def test_record_without_session_id_and_no_object_is_unavailable(db: Session) -> None:
    """验收：「记录没有会话 ID 又没有给出会话对象时无法查询，返回 unavailable」：creating
    记录不调用 Stripe、不改任何东西。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed, CHECKOUT_CREATING)
    before = _world(db, placed, checkout_id)
    gateway = FakeStripeGateway()

    result = _confirm(db, gateway, checkout_id)

    assert result is ConfirmResult.UNAVAILABLE
    assert gateway.calls == []
    assert _world(db, placed, checkout_id) == before


def test_creating_record_is_confirmed_by_the_webhook_object(db: Session) -> None:
    """第 3 条「万一有本站没记下会话 ID 的会话被付款，其 webhook 按元数据里的创建幂等键与订单
    ID 对上这条记录，照第 4、6 条处理」；验收：「creating 记录由 webhook 的会话对象确认时同样
    写入」：creating 记录按对象写入会话 ID、改为 complete 并确认。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed, CHECKOUT_CREATING)
    stripe_session = _object(db, checkout_id, id="cs_test_found_by_key")

    result = _confirm(db, FakeStripeGateway(), checkout_id, stripe_session)

    assert result is ConfirmResult.PAID
    record = _record(db, checkout_id)
    assert record.checkout_session_id == "cs_test_found_by_key"
    assert record.status == CHECKOUT_COMPLETE
    assert record.confirmed_at == NOW
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)


@pytest.mark.parametrize(
    "intent",
    [PaymentIntentInfo("pi_test_unexpanded"), _intent("pi_test_no_livemode", livemode=None)],
    ids=["not-expanded", "no-livemode"],
)
def test_webhook_object_with_unexpanded_intent_is_queried_first(
    db: Session, intent: PaymentIntentInfo
) -> None:
    """验收：「给出的对象里 payment_intent 未展开、缺少其 livemode 或 latest_charge 时，向
    Stripe 查询一次，expand 为 payment_intent.latest_charge」：按对象的会话 ID 查询一次，按
    查询到的对象确认。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    from_webhook = _object(db, checkout_id, payment_intent=intent)
    gateway = FakeStripeGateway(_ok(_object(db, checkout_id)))

    result = _confirm(db, gateway, checkout_id, from_webhook)

    assert result is ConfirmResult.PAID
    [call] = gateway.calls
    assert (call.path, call.form) == (f"{SESSIONS_PATH}/{from_webhook.id}", EXPAND)
    assert _record(db, checkout_id).payment_method_type == "card"


def test_webhook_object_with_unexpanded_intent_and_no_query_changes_nothing(db: Session) -> None:
    """第 9 条「创建会话或查询时 Stripe 不可用……订单保持待支付」；验收：「查询为 unavailable
    时返回 unavailable、不改任何东西」：对象未展开而查询不可用时不按对象确认。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    from_webhook = _object(db, checkout_id, payment_intent=PaymentIntentInfo("pi_test_1"))
    before = _world(db, placed, checkout_id)
    gateway = FakeStripeGateway(UNAVAILABLE)

    result = _confirm(db, gateway, checkout_id, from_webhook)

    assert result is ConfirmResult.UNAVAILABLE
    assert len(gateway.calls) == 1
    assert _world(db, placed, checkout_id) == before


# ---------------------------------------------------------------------------
# 测试环境与查询结果
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "log"),
    [
        pytest.param("live", LOG_LIVEMODE, id="live"),
        pytest.param(
            StripeResult(StripeOutcome.REJECTED, error=StripeError(404, "invalid_request_error")),
            LOG_CONFIRM_REJECTED,
            id="rejected",
        ),
        pytest.param(UNAVAILABLE, None, id="unavailable"),
    ],
)
def test_query_results_other_than_ok_change_nothing(
    db: Session, result: Any, log: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「webhook 事件、会话或付款的 `livemode` 为真即拒绝」；验收：「查询为 unavailable
    时返回 unavailable、不改任何东西；查询结果为 live 或 rejected 时同样不改任何东西、返回
    unavailable，并记一条 error 日志（类别 stripe_livemode 或 stripe_confirm_rejected，含记录
    ID）」。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    if result == "live":
        result = StripeResult(StripeOutcome.LIVE, _object(db, checkout_id, livemode=True))
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        outcome = _confirm(db, FakeStripeGateway(result), checkout_id)

    assert outcome is ConfirmResult.UNAVAILABLE
    assert _world(db, placed, checkout_id) == before
    expected = [] if log is None else [f"{log} checkout={checkout_id}"]
    assert _messages(caplog) == expected
    assert all(r.levelno == logging.ERROR for r in caplog.records if r.name == LOGGER)


@pytest.mark.parametrize("where", ["session", "payment-intent"])
def test_livemode_object_changes_nothing(
    db: Session, where: str, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「会话或付款的 `livemode` 为真即拒绝」；第 4 条「核对会话……处于测试环境」：给出
    的对象里会话或 PaymentIntent 的 livemode 为真时不改任何东西、返回 unavailable，记一条
    stripe_livemode 日志（含记录 ID）。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    if where == "session":
        live = _object(db, checkout_id, livemode=True)
    else:
        live = _object(db, checkout_id, payment_intent=_intent("pi_live", livemode=True))
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, FakeStripeGateway(), checkout_id, live)

    assert result is ConfirmResult.UNAVAILABLE
    assert _world(db, placed, checkout_id) == before
    assert _messages(caplog) == [f"{LOG_LIVEMODE} checkout={checkout_id}"]


@pytest.mark.parametrize("field", ["create_key", "id"])
def test_session_of_another_record_changes_nothing(
    db: Session, field: str, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「webhook 按元数据里的创建幂等键与订单 ID 对上这条记录」；验收：「metadata
    [create_key] 等于这条记录的创建幂等键，记录已有会话 ID 时会话的 id 与之相同……不符时不迁移
    订单、不改写这条记录，记一条 error 日志（类别 stripe_session_mismatch，含记录 ID）并返回
    unavailable」：同一订单另一条会话的对象不能确认这条记录。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    other = _object(db, checkout_id, **{field: "belongs-to-another-session"})
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, FakeStripeGateway(), checkout_id, other)

    assert result is ConfirmResult.UNAVAILABLE
    assert _world(db, placed, checkout_id) == before
    assert _messages(caplog) == [f"{LOG_SESSION_MISMATCH} checkout={checkout_id}"]


# ---------------------------------------------------------------------------
# 留给 SHOP-TASK-117 的迟到与不符的付款：只判定
# ---------------------------------------------------------------------------


def _late_case(db: Session, case: str) -> tuple[_Placed, int, CheckoutSession]:
    if case == "expired-record":
        placed = _guest_order(db)
        checkout_id = _checkout(db, placed, CHECKOUT_EXPIRED)
        return placed, checkout_id, _object(db, checkout_id)
    if case == "order-cancelled":
        placed = _guest_order(db, status=STATUS_CANCELLED)
        checkout_id = _checkout(db, placed)
        return placed, checkout_id, _object(db, checkout_id)
    if case == "order-paid-elsewhere":
        placed = _guest_order(db, status=STATUS_PAID)
        checkout_id = _checkout(db, placed)
        return placed, checkout_id, _object(db, checkout_id)
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    total = db.scalars(select(Order.total_sen).where(Order.id == placed.order_id)).one()
    overrides: dict[str, Any] = {
        "amount": {"amount_total": total - 1},
        "currency": {"currency": "sgd"},
        "metadata-order": {"order_id": str(placed.order_id + 1000)},
        "client-reference": {"client_reference_id": str(placed.order_id + 1000)},
    }[case]
    return placed, checkout_id, _object(db, checkout_id, **overrides)


@pytest.mark.parametrize(
    "case",
    [
        "expired-record",
        "order-cancelled",
        "order-paid-elsewhere",
        "amount",
        "currency",
        "metadata-order",
        "client-reference",
    ],
)
def test_late_or_mismatched_payment_needs_review_and_changes_nothing(
    db: Session, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条「确认时订单已不是待支付且不是由这个会话付款（已取消，或已由另一途径付款），或
    金额、币种不符，订单状态不变」；验收：「记录已为 expired 而会话状态为 complete；
    payment_status 为 paid 而订单已不是待支付且该记录没有确认时间；payment_status 为 paid 而
    金额、币种或所属订单不符……订单与记录都不改……返回 needs_review，记一条 error 日志（类别
    stripe_late_payment，只含记录 ID）」。退款留给 SHOP-TASK-117。

    SHOP-TASK-117 替换了这里的 needs_review 占位断言（原因：第 6 条的退款尝试与 HANDOFF 0.53
    的释放活动位已实现，第 3 条末句的已为 expired 的记录改回 complete 也已实现）：
    expired-record（订单仍待支付、没有别的活动记录、全部相符）改回 complete 并确认，返回
    paid、不记日志；其余情形订单状态与其他各表不变，写一条退款尝试后记录改为 expired，返回
    refunded，只记一条 stripe_late_payment。细节见 tests/test_stripe_late_payment.py。"""
    placed, checkout_id, stripe_session = _late_case(db, case)
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, FakeStripeGateway(), checkout_id, stripe_session)

    after = _world(db, placed, checkout_id)
    if case == "expired-record":
        assert result is ConfirmResult.PAID
        assert after["order"] == (STATUS_PAID, NOW)
        assert after["record"]["status"] == CHECKOUT_COMPLETE
        assert _messages(caplog) == []
    else:
        assert result is ConfirmResult.REFUNDED
        assert after["record"]["status"] == CHECKOUT_EXPIRED
        assert after["record"]["active_order_id"] is None
        del after["record"], before["record"]
        assert after == before
        assert _messages(caplog) == [f"{LOG_LATE_PAYMENT} checkout={checkout_id}"]


@pytest.mark.parametrize("status", ["expired", "open"])
def test_expired_record_with_an_unpaid_session_is_pending_without_log(
    db: Session, status: str, caplog: pytest.LogCaptureFixture
) -> None:
    """验收：「会话为 expired 或 open 且未付款时照确认函数一条返回 pending、不记日志」：本站
    记录已为 expired 时，未付款的会话不是迟到的付款，什么都不改。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed, CHECKOUT_EXPIRED)
    unpaid = _object(
        db, checkout_id, status=status, payment_status=PAYMENT_UNPAID, payment_intent=None
    )
    before = _world(db, placed, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, FakeStripeGateway(_ok(unpaid)), checkout_id)

    assert result is ConfirmResult.PENDING
    assert _world(db, placed, checkout_id) == before
    assert _messages(caplog) == []


def test_order_paid_by_this_session_is_not_a_late_payment(db: Session) -> None:
    """第 4 条「同一会话……的重复确认（订单已由它付款）直接返回原结果，不退款」：订单已由这条
    记录确认付款（不是待支付了）时再确认返回 paid，不归为迟到的付款。"""
    placed = _guest_order(db)
    checkout_id = _checkout(db, placed)
    stripe_session = _object(db, checkout_id)
    assert _confirm(db, FakeStripeGateway(), checkout_id, stripe_session) is ConfirmResult.PAID

    again = _confirm(db, FakeStripeGateway(), checkout_id, stripe_session)

    assert again is ConfirmResult.PAID
    assert _order(db, placed.order_id) == (STATUS_PAID, NOW)


def test_aware_time_is_rejected_before_touching_the_database(db: Session) -> None:
    """模块说明「库里的时间一律是不带时区的 UTC」：带时区的当前时间抛 ValueError，不发任何
    语句。"""
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    with pytest.raises(ValueError):
        confirm_checkout(db, FakeStripeGateway(), 1, None, NOW.replace(tzinfo=UTC))

    assert statements == []

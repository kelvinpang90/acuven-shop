"""模拟支付成功、本人取消与支付超时接入优惠券与积分（app/services/payment.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1、3 条（券与积分在取消或
支付超时时释放；模拟支付成功后获得积分）、「订单与退款状态」（待支付订单可由下单者取消或超时为
demo_cancelled，释放库存、券及积分预占）与「失败、并发与重试」第 1 条，以及 docs/HANDOFF.md
0.46、0.47 记录的 Kelvin 决定（预占与核销都计用券次数；已预占的积分以预占时为准，订单取消时批次
已过期则释放的积分随即记为到期；获得积分按下单时的快照在支付成功时发放）。每条测试（参数化的
测试是每个用例）的文档字符串写明它守住的设计或决定原句；没有直接原句的，写明是 SHOP-TASK-090
验收标准里的约定。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；会员订单直接按
模型写入，券与积分的预占经 SHOP-TASK-085 的 reserve_coupon 与 SHOP-TASK-086 的 reserve_points，
来源批次经 earn_points。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE：锁顺序
只能以 SQL 执行事件钩子断言语句的先后，并发下的排队只在 MySQL 上成立。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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
    PointLot,
    Product,
    ProductVariant,
)
from app.models.coupon import TYPE_FIXED, USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
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
from app.models.points import (
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_RELEASE,
)
from app.services import payment
from app.services.coupons import reserve_coupon
from app.services.member_deletion import delete_member
from app.services.points import available_points, earn_points, lot_expires_at, reserve_points

# 订单下单时间（马来西亚时间 2026-10-12 12:00）与支付到期时间。
PLACED_AT = datetime(2026, 10, 12, 4, 0)
WINDOW = timedelta(minutes=15)
PAY_AT = PLACED_AT + timedelta(minutes=5)
TIMEOUT_AT = PLACED_AT + WINDOW + timedelta(minutes=1)
# 来源批次的取得时间：在上面这些时刻都未过期。
EARNED_AT = datetime(2026, 9, 1, 4, 0)
SOURCE_POINTS = 1000

# 会员订单：一行 2 件、单价 3000 仙；券抵 1000 仙，积分抵 500，运费 800，获得积分快照 45。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
COUPON_OFF = 1000
REDEEMED = 500
SHIPPING = 800
EARNED = 45
# 规格的初始库存与下单扣去之后的当日可用库存。
INITIAL_STOCK = 5
RESERVED_STOCK = INITIAL_STOCK - QUANTITY

FINGERPRINT = "0f" * 32

PLACED_BY_MEMBER = (None, STATUS_AWAITING_PAYMENT, ACTOR_MEMBER)
PLACED_BY_GUEST = (None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：两个会话共用同一个内存库连接，用来模拟另一个请求先提交。
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


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@dataclass(frozen=True)
class _Placed:
    member_id: int | None
    order_id: int
    variant_id: int
    coupon_id: int | None
    lot_id: int | None


def _variant(db: Session) -> int:
    n = next(_SEQUENCE)
    category = _add(db, Category(slug=f"goods-{n}", name_en="Goods", is_active=True))
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=f"tee-{n}",
            name_en="Tee",
            name_zh="T恤",
            description_en="A tee.",
            is_active=True,
        ),
    )
    variant = ProductVariant(
        product_id=product.id,
        sku=f"TEE-{n}",
        price_sen=UNIT_PRICE,
        daily_initial_stock=INITIAL_STOCK,
        available_stock=RESERVED_STOCK,
        is_active=True,
    )
    return _add(db, variant).id


def _member(db: Session) -> Member:
    phone = f"+60123{next(_SEQUENCE):06d}"
    return _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=EARNED_AT))


def _order_row(
    db: Session,
    member_id: int | None,
    placed_at: datetime,
    *,
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
        created_at=placed_at,
        payment_expires_at=placed_at + WINDOW,
        paid_at=placed_at if status == STATUS_PAID else None,
        member_id=member_id,
        claim_status=CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE,
    )
    return _add(db, order)


def _items_and_event(db: Session, order: Order, variant_id: int, actor: str) -> None:
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=variant_id,
        sku="TEE",
        product_name_en="Tee",
        product_name_zh="T恤",
        product_name_ms="Tee",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=UNIT_PRICE,
        quantity=QUANTITY,
        line_subtotal_sen=SUBTOTAL,
    )
    _add(db, item)
    placed = OrderEvent(
        order_id=order.id,
        from_status=None,
        to_status=STATUS_AWAITING_PAYMENT,
        actor_type=actor,
        created_at=order.created_at,
    )
    _add(db, placed)


def _coupon(db: Session) -> Coupon:
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
    return _add(db, coupon)


def _member_order(
    db: Session,
    *,
    earned_at: datetime = EARNED_AT,
    placed_at: datetime = PLACED_AT,
    earned: int = EARNED,
) -> _Placed:
    """会员订单：来源批次 1000 积分（另一张已支付订单所得），本单预占券 1000 仙与积分 500，
    获得积分快照 earned；写完即提交。
    """
    variant_id = _variant(db)
    member = _member(db)
    source = _order_row(db, member.id, earned_at, status=STATUS_PAID)
    lot = earn_points(db, member.id, source.id, SOURCE_POINTS, earned_at)
    assert lot is not None
    order = _order_row(
        db, member.id, placed_at, coupon_off=COUPON_OFF, redeemed=REDEEMED, earned=earned
    )
    _items_and_event(db, order, variant_id, ACTOR_MEMBER)
    coupon = _coupon(db)
    check = reserve_coupon(db, coupon.id, order.id, member.id, COUPON_OFF, SUBTOTAL, placed_at)
    assert check.ok
    reserve_points(db, member.id, order.id, REDEEMED, placed_at)
    db.commit()
    return _Placed(member.id, order.id, variant_id, coupon.id, lot.id)


def _guest_order(db: Session) -> _Placed:
    """游客订单：没有券与积分，快照都为 0；写完即提交。"""
    variant_id = _variant(db)
    order = _order_row(db, None, PLACED_AT)
    _items_and_event(db, order, variant_id, ACTOR_GUEST)
    db.commit()
    return _Placed(None, order.id, variant_id, None, None)


def _key() -> str:
    return f"pay-key-{next(_SEQUENCE)}"


def _pay(db: Session, placed: _Placed, result: str = "success", **kwargs: Any) -> Any:
    key = kwargs.pop("key", None) or _key()
    now = kwargs.pop("now", PAY_AT)
    return payment.submit_payment(db, placed.order_id, "card", result, key, now, **kwargs)


def _status(db: Session, placed: _Placed) -> str:
    db.expire_all()
    return db.scalars(select(Order.status).where(Order.id == placed.order_id)).one()


def _coupon_uses(db: Session, placed: _Placed) -> list[str]:
    db.expire_all()
    stmt = select(CouponUse.status).where(CouponUse.order_id == placed.order_id)
    return list(db.scalars(stmt))


def _lots(db: Session, placed: _Placed) -> list[tuple[int, int]]:
    """本单获得的批次：（会员、积分）。"""
    stmt = select(PointLot.member_id, PointLot.points).where(PointLot.order_id == placed.order_id)
    return [tuple(row) for row in db.execute(stmt)]


def _entries(db: Session, *conditions: Any) -> list[tuple[str, int | None, int]]:
    """（类别、批次、积分），按流水 ID 升序。"""
    stmt = (
        select(PointEntry.kind, PointEntry.lot_id, PointEntry.points)
        .where(*conditions)
        .order_by(PointEntry.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _order_entries(db: Session, placed: _Placed) -> list[tuple[str, int | None, int]]:
    return _entries(db, PointEntry.order_id == placed.order_id)


def _lot_entries(db: Session, placed: _Placed) -> list[tuple[str, int | None, int]]:
    return _entries(db, PointEntry.lot_id == placed.lot_id)


def _stock(db: Session, placed: _Placed) -> int:
    db.expire_all()
    stmt = select(ProductVariant.available_stock).where(ProductVariant.id == placed.variant_id)
    return db.scalars(stmt).one()


def _events(db: Session, placed: _Placed) -> list[tuple[str | None, str, str]]:
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == placed.order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _attempt_count(db: Session, placed: _Placed) -> int:
    stmt = select(PaymentAttempt.id).where(PaymentAttempt.order_id == placed.order_id)
    return len(list(db.scalars(stmt)))


def _cancel(db: Session, placed: _Placed, path: str) -> None:
    """按 path 取消：self 为会员本人在到期前取消，timeout 为到期后的超时取消。"""
    if path == "self":
        outcome = payment.cancel_order(db, placed.order_id, PAY_AT, actor="member")
        assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    else:
        assert payment.expire_overdue_orders(db, TIMEOUT_AT) == 1


def _with_lot(placed: _Placed, *entries: tuple[str, int]) -> list[tuple[str, int | None, int]]:
    return [(kind, placed.lot_id, points) for kind, points in entries]


# ---------------------------------------------------------------------------
# 支付成功与失败
# ---------------------------------------------------------------------------


def test_success_uses_coupon_and_earns_snapshot(db: Session) -> None:
    """第 3 条「模拟支付成功后获得」；Kelvin 0.47「获得积分按下单时的快照在支付成功时发放」；
    Kelvin 0.46「预占与核销都计次数」（核销把 reserved 改为 used）。
    SHOP-TASK-090 验收：支付成功后用券记录为 used，写一个积分批次与一条 earn，数量为订单快照
    points_earned；预占的 redeem 不另写流水；事件操作者为 member。
    """
    placed = _member_order(db)

    outcome, created = _pay(db, placed, actor="member")

    assert (outcome.status, created) == (STATUS_PAID, True)
    assert _status(db, placed) == STATUS_PAID
    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == [(placed.member_id, EARNED)]
    (lot_id,) = db.scalars(select(PointLot.id).where(PointLot.order_id == placed.order_id))
    assert _order_entries(db, placed) == [
        (KIND_REDEEM, placed.lot_id, -REDEEMED),
        (KIND_EARN, lot_id, EARNED),
    ]
    assert _events(db, placed) == [
        PLACED_BY_MEMBER,
        (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_MEMBER),
    ]
    assert _stock(db, placed) == RESERVED_STOCK


def test_failure_neither_uses_nor_earns(db: Session) -> None:
    """「模拟支付失败仍停在此状态并记录失败尝试」；第 3 条「模拟支付成功后获得」（失败不获得）。
    SHOP-TASK-090 验收：支付失败不核销、不发放，用券记录仍为 reserved，积分只有预占的 redeem。
    """
    placed = _member_order(db)

    outcome, created = _pay(db, placed, result="failure", actor="member")

    assert (outcome.status, created) == (STATUS_AWAITING_PAYMENT, True)
    assert _coupon_uses(db, placed) == [USE_RESERVED]
    assert _lots(db, placed) == []
    assert _order_entries(db, placed) == [(KIND_REDEEM, placed.lot_id, -REDEEMED)]
    assert _events(db, placed) == [PLACED_BY_MEMBER]


def test_zero_snapshot_earns_nothing(db: Session) -> None:
    """Kelvin 0.47「获得积分按下单时的快照在支付成功时发放」。SHOP-TASK-090 验收：快照
    points_earned 为 0 时不写批次与 earn；券照常核销。
    """
    placed = _member_order(db, earned=0)

    _pay(db, placed, actor="member")

    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == []
    assert _order_entries(db, placed) == [(KIND_REDEEM, placed.lot_id, -REDEEMED)]


def test_replayed_and_repeated_payment_do_not_repeat(db: Session) -> None:
    """「失败、并发与重试」第 1 条「相同键相同请求返回原结果」；「所有状态迁移校验当前状态」。
    SHOP-TASK-090 验收：重放支付不再核销或发放；换一个键再付即 OrderNotPayable，也不重复。
    """
    placed = _member_order(db)
    key = _key()
    first, created = _pay(db, placed, key=key, actor="member")
    assert created

    replay, replayed_created = _pay(db, placed, key=key, actor="member")
    with pytest.raises(payment.OrderNotPayable):
        _pay(db, placed, actor="member")

    assert replay == first
    assert not replayed_created
    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == [(placed.member_id, EARNED)]
    kinds = [kind for kind, _, _ in _order_entries(db, placed)]
    assert kinds == [KIND_REDEEM, KIND_EARN]
    assert _attempt_count(db, placed) == 1


def test_member_deleted_before_payment_earns_nothing(db: Session) -> None:
    """「权限与资料保护」第 3 条「注销时…把其订单上的会员 ID 清空」「重新注册不能据此恢复…积分」。
    SHOP-TASK-090 验收：会员在支付前被注销（member_id 已清空）时支付成功也不发放积分；券的核销
    只按订单找记录，照常改为 used。
    """
    placed = _member_order(db)
    assert delete_member(db, placed.member_id, PAY_AT)
    db.commit()

    outcome, _ = _pay(db, placed)

    assert outcome.status == STATUS_PAID
    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == []
    assert [kind for kind, _, _ in _order_entries(db, placed)] == [KIND_REDEEM]


def test_member_not_active_earns_nothing(db: Session) -> None:
    """第 3 条「模拟支付成功后获得」只给会员。SHOP-TASK-090 验收：以加锁读重新读取订单的会员 ID
    与会员状态，会员 ID 非空但会员已不是 active 时不发放。
    """
    placed = _member_order(db)
    db.execute(
        update(Member)
        .where(Member.id == placed.member_id)
        .values(status=MEMBER_DELETED, phone=None, password_hash=None, deleted_at=PAY_AT)
    )
    db.commit()

    _pay(db, placed, actor="member")

    assert _status(db, placed) == STATUS_PAID
    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == []


# ---------------------------------------------------------------------------
# 本人取消与超时取消
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["self", "timeout"])
def test_cancel_releases_coupon_points_and_stock(db: Session, path: str) -> None:
    """「订单与退款状态」「待支付订单可由下单者取消或超时为 demo_cancelled，释放库存、券及积分
    预占」；第 1 条「取消或支付超时释放」；第 3 条「失败或取消时释放」。SHOP-TASK-090 验收：
    取消后用券记录为 released、积分写 release 且可用余额恢复、库存加回；事件操作者本人取消为
    member、超时为 system。
    """
    placed = _member_order(db)
    now = PAY_AT if path == "self" else TIMEOUT_AT
    assert available_points(db, placed.member_id, now) == SOURCE_POINTS - REDEEMED

    _cancel(db, placed, path)

    assert _status(db, placed) == STATUS_CANCELLED
    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert _order_entries(db, placed) == _with_lot(
        placed, (KIND_REDEEM, -REDEEMED), (KIND_RELEASE, REDEEMED)
    )
    assert available_points(db, placed.member_id, now) == SOURCE_POINTS
    assert _stock(db, placed) == INITIAL_STOCK
    actor = ACTOR_MEMBER if path == "self" else ACTOR_SYSTEM
    assert _events(db, placed) == [
        PLACED_BY_MEMBER,
        (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, actor),
    ]
    assert _lots(db, placed) == []


def test_cancel_after_lot_expired_marks_expire(db: Session) -> None:
    """Kelvin 0.46「已预占的积分以预占时为准…订单取消时批次已过期则释放的积分随即记为到期」。
    SHOP-TASK-090 验收：取消时批次已过期，release 之后紧接着写等量的 expire，可用余额不因释放
    而增加。
    """
    acquired = datetime(2025, 10, 12, 4, 0)
    lot_end = lot_expires_at(acquired)
    # 预占时批次仍有效；取消时（到期时刻之后、支付到期之前）已过期。
    placed = _member_order(db, earned_at=acquired, placed_at=lot_end - timedelta(minutes=5))
    cancel_at = lot_end + timedelta(minutes=1)

    outcome = payment.cancel_order(db, placed.order_id, cancel_at, actor="member")

    assert outcome.cancelled_by == "self"
    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert _lot_entries(db, placed) == _with_lot(
        placed,
        (KIND_EARN, SOURCE_POINTS),
        (KIND_REDEEM, -REDEEMED),
        (KIND_RELEASE, REDEEMED),
        (KIND_EXPIRE, -REDEEMED),
    )
    assert available_points(db, placed.member_id, cancel_at) == 0
    assert _stock(db, placed) == INITIAL_STOCK


def test_repeated_cancel_and_timeout_do_not_release_twice(db: Session) -> None:
    """「可重复运行…只生效一次」；「所有状态迁移校验当前状态」。SHOP-TASK-090 验收：重复取消、
    重复运行超时取消不重复释放券、积分与库存。
    """
    placed = _member_order(db)

    _cancel(db, placed, "self")
    again = payment.cancel_order(db, placed.order_id, PAY_AT, actor="member")
    timeout_runs = payment.expire_overdue_orders(db, TIMEOUT_AT)

    assert again.status == STATUS_CANCELLED
    assert timeout_runs == 0
    assert _coupon_uses(db, placed) == [USE_RELEASED]
    kinds = [kind for kind, _, _ in _order_entries(db, placed)]
    assert kinds == [KIND_REDEEM, KIND_RELEASE]
    assert _stock(db, placed) == INITIAL_STOCK
    cancel_events = [row for row in _events(db, placed) if row[1] == STATUS_CANCELLED]
    assert len(cancel_events) == 1


def test_repeated_timeout_runs_do_not_release_twice(db: Session) -> None:
    """「可重复运行…只生效一次」。SHOP-TASK-090 验收：重复运行超时取消不重复释放。"""
    placed = _member_order(db)

    assert payment.expire_overdue_orders(db, TIMEOUT_AT) == 1
    assert payment.expire_overdue_orders(db, TIMEOUT_AT) == 0

    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert [kind for kind, _, _ in _order_entries(db, placed)] == [KIND_REDEEM, KIND_RELEASE]
    assert _stock(db, placed) == INITIAL_STOCK


def test_timeout_after_member_deleted_forfeits(db: Session) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」；「订单与退款状态」超时释放券及积分预占。
    SHOP-TASK-090 验收：会员已注销（订单 member_id 已清空）后超时取消，释放的积分随即作废
    （release 后紧接着等量的 forfeit），用券记录为 released，库存加回。注销时先作废未预占的
    余额（SHOP-TASK-094），所以 redeem 与 release 之间另有一条 forfeit。
    """
    placed = _member_order(db)
    assert delete_member(db, placed.member_id, PAY_AT)
    db.commit()

    assert payment.expire_overdue_orders(db, TIMEOUT_AT) == 1

    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert _lot_entries(db, placed) == _with_lot(
        placed,
        (KIND_EARN, SOURCE_POINTS),
        (KIND_REDEEM, -REDEEMED),
        (KIND_FORFEIT, -(SOURCE_POINTS - REDEEMED)),
        (KIND_RELEASE, REDEEMED),
        (KIND_FORFEIT, -REDEEMED),
    )
    assert _stock(db, placed) == INITIAL_STOCK
    assert _events(db, placed)[-1] == (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)


def test_cancel_losing_to_payment_releases_nothing(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「所有状态迁移校验当前状态」；「失败、并发与重试」第 1 条（同一事务，失败全部回滚）。
    SHOP-TASK-090 验收：带条件的 UPDATE 未命中时整个事务回滚，不释放——取消读过订单之后、
    UPDATE 之前另一会话先提交了支付成功：取消抛 OrderNotCancellable，券为 used、积分不写
    release、库存不加回。
    """
    placed = _member_order(db)
    real = payment.is_transition_allowed
    done: list[bool] = []

    def pay_first(current: str, target: str, actor: str) -> bool:
        if not done:
            done.append(True)
            with Session(engine) as other:
                _pay(other, placed, actor="member")
        return real(current, target, actor)

    monkeypatch.setattr(payment, "is_transition_allowed", pay_first)

    with pytest.raises(payment.OrderNotCancellable):
        payment.cancel_order(db, placed.order_id, PAY_AT, actor="member")

    assert _status(db, placed) == STATUS_PAID
    assert _coupon_uses(db, placed) == [USE_USED]
    assert [kind for kind, _, _ in _order_entries(db, placed)] == [KIND_REDEEM, KIND_EARN]
    assert _stock(db, placed) == RESERVED_STOCK


# ---------------------------------------------------------------------------
# 锁顺序
# ---------------------------------------------------------------------------


def _statement_log(engine: Engine) -> tuple[list[str], Any]:
    """记下会员行的读取（member）与订单的带条件 UPDATE（update-order）的先后。"""
    log: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.startswith("SELECT members."):
            log.append("member")
        elif statement.startswith("UPDATE orders"):
            log.append("update-order")

    event.listen(engine, "before_cursor_execute", capture)
    return log, capture


@pytest.mark.parametrize("operation", ["pay", "self", "timeout"])
def test_member_row_is_read_before_order_update(
    engine: Engine, db: Session, operation: str
) -> None:
    """SHOP-TASK-090 验收：锁顺序与会员下单、注销一致（会员 → 订单）——支付成功、本人取消与超时
    取消在带条件的 UPDATE 之前先锁该会员的行。SQLite 不生成 FOR UPDATE，这里只断言读取会员行的
    语句在订单 UPDATE 之前。
    """
    placed = _member_order(db)
    log, capture = _statement_log(engine)
    if operation == "pay":
        _pay(db, placed, actor="member")
    else:
        _cancel(db, placed, operation)
    event.remove(engine, "before_cursor_execute", capture)

    assert "update-order" in log
    assert log[0] == "member"
    assert log.index("member") < log.index("update-order")


@pytest.mark.parametrize("case", ["guest", "member-cleared"])
def test_no_member_lock_before_update_without_member_id(
    engine: Engine, db: Session, case: str
) -> None:
    """SHOP-TASK-090 验收：订单会员 ID 为空（游客订单或会员已注销后被清空）时不预先锁；会员 ID
    已清空的订单在取消时由 release_points 在库存之后才锁会员行。
    """
    if case == "guest":
        placed = _guest_order(db)
    else:
        placed = _member_order(db)
        assert delete_member(db, placed.member_id, PAY_AT)
        db.commit()
    log, capture = _statement_log(engine)
    payment.expire_overdue_orders(db, TIMEOUT_AT)
    event.remove(engine, "before_cursor_execute", capture)

    assert log[0] == "update-order"
    expected = [] if case == "guest" else ["member"]
    assert log[1:] == expected


# ---------------------------------------------------------------------------
# 操作者
# ---------------------------------------------------------------------------


def test_member_actor_writes_member_events(db: Session) -> None:
    """「所有状态迁移校验当前状态与操作者，事务内写订单、流水和事件」。SHOP-TASK-090 验收：以
    member 为操作者支付与取消写 member 事件；取消方显示为本人。
    """
    paid = _member_order(db)
    cancelled = _member_order(db)

    _pay(db, paid, actor="member")
    outcome = payment.cancel_order(db, cancelled.order_id, PAY_AT, actor="member")

    assert _events(db, paid)[-1] == (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_MEMBER)
    assert _events(db, cancelled)[-1] == (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_MEMBER)
    assert outcome.cancelled_by == "self"


@pytest.mark.parametrize("actor", ["system", "admin", "Member", "", None])
@pytest.mark.parametrize("operation", ["pay", "cancel"])
def test_other_actor_values_raise(db: Session, operation: str, actor: Any) -> None:
    """SHOP-TASK-090 验收：actor 只接受 guest 与 member，其他值抛 ValueError；不写支付尝试、
    不改订单、不动券与积分。超时取消的 system 不由调用方给出。
    """
    placed = _member_order(db)

    with pytest.raises(ValueError):
        if operation == "pay":
            _pay(db, placed, actor=actor)
        else:
            payment.cancel_order(db, placed.order_id, PAY_AT, actor=actor)

    db.rollback()
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _attempt_count(db, placed) == 0
    assert _coupon_uses(db, placed) == [USE_RESERVED]
    assert _events(db, placed) == [PLACED_BY_MEMBER]


# ---------------------------------------------------------------------------
# 游客订单
# ---------------------------------------------------------------------------


def test_guest_payment_unchanged(db: Session) -> None:
    """「成功进入 demo_paid」；第 1 条「优惠券仅会员可用」（游客订单没有用券记录与积分）。
    SHOP-TASK-090 验收：游客接口不传 actor，行为不变——事件操作者为 guest，不写用券记录、
    积分批次与流水。
    """
    placed = _guest_order(db)

    outcome, created = _pay(db, placed)

    assert (outcome.status, created) == (STATUS_PAID, True)
    assert _events(db, placed) == [
        PLACED_BY_GUEST,
        (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_GUEST),
    ]
    assert _coupon_uses(db, placed) == []
    assert _lots(db, placed) == []
    assert _order_entries(db, placed) == []
    assert _stock(db, placed) == RESERVED_STOCK


@pytest.mark.parametrize("path", ["self", "timeout"])
def test_guest_cancel_unchanged(db: Session, path: str) -> None:
    """「待支付订单可由下单者在支付页取消（游客凭该单短期凭据），或超时为 demo_cancelled，
    释放库存」。SHOP-TASK-090 验收：游客订单的取消行为不变——库存加回、事件操作者为 guest
    或 system，没有用券记录与积分流水可释放，两者都不写。
    """
    placed = _guest_order(db)

    if path == "self":
        outcome = payment.cancel_order(db, placed.order_id, PAY_AT)
        assert outcome.cancelled_by == "self"
        actor = ACTOR_GUEST
    else:
        assert payment.expire_overdue_orders(db, TIMEOUT_AT) == 1
        actor = ACTOR_SYSTEM

    assert _events(db, placed) == [
        PLACED_BY_GUEST,
        (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, actor),
    ]
    assert _stock(db, placed) == INITIAL_STOCK
    assert _coupon_uses(db, placed) == []
    assert db.scalars(select(PointEntry.id)).all() == []


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------


def test_view_gives_snapshot_fields(db: Session) -> None:
    """「数据模型」Order 的券折扣、积分抵扣与获得积分快照。
    SHOP-TASK-090 验收：pay_orders 的视图另给 coupon_discount_sen、points_redeemed 与
    points_earned（取订单快照，游客订单为 0）。
    """
    member = _member_order(db)
    guest = _guest_order(db)

    views = payment.pay_orders(db, [member.order_id, guest.order_id], "en", PAY_AT)

    snapshot = [(v.coupon_discount_sen, v.points_redeemed, v.points_earned) for v in views]
    assert snapshot == [(COUPON_OFF, REDEEMED, EARNED), (0, 0, 0)]
    assert views[0].total_sen == SUBTOTAL - COUPON_OFF - REDEEMED + SHIPPING

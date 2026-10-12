"""积分的获得、预占、释放、到期与作废规则。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1、3、4 条与「权限与资料
保护」第 3 条（注销时积分余额作废），以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定
（积分按批次记账，先用先到期的批次；批次在取得日（马来西亚时间）加 12 个自然月那一天的 23:59:59
到期，没有那一天取该月最后一天；已预占的积分以预占时为准，到期处理跳过已预占的部分，订单若之后
取消而批次已过期，释放的积分随即记为到期）。每条测试（参数化的测试是每个用例）写明它守住的设计
或决定原句。

用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。SQLite 不支持行锁，
SQLAlchemy 在 SQLite 上不生成 FOR UPDATE：同一会员的并发写在会员行锁上排队只在 MySQL 上成立，
这里不能复现，只验证锁下的判定与写入。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order, PointEntry, PointLot, RefundRequest
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.models.points import (
    KIND_DEBT_ADD,
    KIND_DEBT_REPAY,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_RELEASE,
)
from app.models.refund import REFUND_REQUESTED
from app.services.points import (
    InsufficientPoints,
    available_points,
    debt_points,
    earn_points,
    expire_points,
    forfeit_points,
    is_lot_expired,
    lot_expires_at,
    release_points,
    reserve_points,
)

CREATED = datetime(2026, 10, 1, 2, 0)
# 马来西亚时间 2026-10-12 12:00。
NOW = datetime(2026, 10, 12, 4, 0)
# NOW 取得的批次仍有效的最后一刻：马来西亚时间 2027-10-12 23:59:59。
NOW_LOT_EXPIRES = datetime(2027, 10, 12, 15, 59, 59)
# 这一天取得的批次在 NOW 时已过期（2026-09-07 23:59:59 马来西亚时间到期）。
LONG_AGO = NOW - timedelta(days=400)
FINGERPRINT = "0f" * 32
ONE_SECOND = timedelta(seconds=1)

_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)
_REFUND_SEQUENCE = itertools.count(1)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _member(db: Session) -> Member:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    return _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=CREATED))


def _delete_member(db: Session, member: Member) -> None:
    """按注销后的样子改会员行：清空手机号与密码哈希、状态 deleted、写注销时间。"""
    member.status = MEMBER_DELETED
    member.phone = None
    member.password_hash = None
    member.deleted_at = NOW
    db.flush()


def _order(db: Session, member_id: int | None, **overrides: Any) -> Order:
    """待支付的会员订单，商品 RM60、券抵 RM10、运费 RM8；每次给不同的订单号与幂等键。"""
    n = next(_ORDER_SEQUENCE)
    values: dict[str, Any] = {
        "order_number": f"{n:016d}",
        "status": STATUS_AWAITING_PAYMENT,
        "subtotal_sen": 6000,
        "coupon_discount_sen": 1000,
        "points_redeemed": 0,
        "shipping_fee_sen": 800,
        "total_sen": 5800,
        "points_earned": 0,
        "shipping_zone_code": "MY-10",
        "shipping_rate_version": 1,
        "idempotency_key": f"order-key-{n}",
        "request_fingerprint": FINGERPRINT,
        "created_at": CREATED,
        "payment_expires_at": CREATED + timedelta(minutes=15),
        "paid_at": None,
        "member_id": member_id,
        "claim_status": CLAIM_NOT_CLAIMABLE,
    }
    return _add(db, Order(**(values | overrides)))


def _debt(db: Session, member: Member, points: int) -> None:
    """退款追回不足时记入的待抵扣（debt_add，批次为空，带退款申请）。"""
    order = _order(db, member.id)
    refund = _add(
        db,
        RefundRequest(
            order_id=order.id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_MEMBER,
            idempotency_key=f"refund-key-{next(_REFUND_SEQUENCE)}",
            request_fingerprint=FINGERPRINT,
            amount_sen=1000,
            created_at=CREATED,
        ),
    )
    _add(
        db,
        PointEntry(
            member_id=member.id,
            lot_id=None,
            kind=KIND_DEBT_ADD,
            points=points,
            order_id=order.id,
            refund_request_id=refund.id,
            created_at=CREATED,
        ),
    )


def _earn(db: Session, member: Member, points: int, now: datetime) -> PointLot:
    """为该会员新建一张订单并在 now 获得积分。"""
    order = _order(db, member.id)
    lot = earn_points(db, member.id, order.id, points, now)
    assert lot is not None
    return lot


def _entries(db: Session, *conditions: Any) -> list[tuple[str, int | None, int, int]]:
    """（类别、批次、积分、订单），按流水 ID 升序。"""
    rows = db.execute(
        select(PointEntry.kind, PointEntry.lot_id, PointEntry.points, PointEntry.order_id)
        .where(*conditions)
        .order_by(PointEntry.id)
    ).all()
    return [tuple(row) for row in rows]


def _remaining(db: Session, lot: PointLot) -> int:
    stmt = select(func.coalesce(func.sum(PointEntry.points), 0)).where(PointEntry.lot_id == lot.id)
    return int(db.scalar(stmt))


def _entry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(PointEntry))


# ---------------------------------------------------------------------------
# 到期时刻
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acquired_at", "expected"),
    [
        # Kelvin 决定 2：取得日（马来西亚时间）加 12 个自然月那一天的 23:59:59（马来西亚时间）到期。
        pytest.param(NOW, NOW_LOT_EXPIRES, id="ordinary-day"),
        # Kelvin 决定 2：月末取得，一年后同月同日（1 月 31 日）仍存在，取那一天。
        pytest.param(
            datetime(2026, 1, 30, 16, 0), datetime(2027, 1, 31, 15, 59, 59), id="month-end-jan"
        ),
        # Kelvin 决定 2：8 月 31 日取得，一年后 8 月 31 日到期。
        pytest.param(
            datetime(2026, 8, 31, 5, 0), datetime(2027, 8, 31, 15, 59, 59), id="month-end-aug"
        ),
        # Kelvin 决定 2：没有那一天取该月最后一天——闰年 2 月 29 日取得，次年 2 月 28 日到期。
        pytest.param(datetime(2028, 2, 29, 4, 0), datetime(2029, 2, 28, 15, 59, 59), id="leap-day"),
        # Kelvin 决定 2：平年 2 月 28 日取得，次年（闰年）仍是 2 月 28 日，不顺延到 29 日。
        pytest.param(
            datetime(2027, 2, 28, 4, 0), datetime(2028, 2, 28, 15, 59, 59), id="feb-28-to-leap"
        ),
        # Kelvin 决定 2：取得日按马来西亚时间——UTC 2028-02-28 16:00 是马来西亚 2 月 29 日。
        pytest.param(
            datetime(2028, 2, 28, 16, 0), datetime(2029, 2, 28, 15, 59, 59), id="leap-day-myt"
        ),
        # Kelvin 决定 2：马来西亚时间 12 月 31 日 23:59:59 取得，仍算 12 月 31 日。
        pytest.param(
            datetime(2026, 12, 31, 15, 59, 59),
            datetime(2027, 12, 31, 15, 59, 59),
            id="before-myt-midnight",
        ),
        # Kelvin 决定 2：UTC 12 月 31 日 16:00 已是马来西亚次年 1 月 1 日，按这一天加 12 个月。
        pytest.param(
            datetime(2026, 12, 31, 16, 0),
            datetime(2028, 1, 1, 15, 59, 59),
            id="after-myt-midnight",
        ),
        # 取得时间先截到整秒：23:59:59.7 不进到次日（MySQL 会把它四舍五入到次日 00:00:00）。
        pytest.param(
            datetime(2026, 12, 31, 15, 59, 59, 700_000),
            datetime(2027, 12, 31, 15, 59, 59),
            id="microseconds-truncated",
        ),
    ],
)
def test_lot_expires_at(acquired_at: datetime, expected: datetime) -> None:
    assert lot_expires_at(acquired_at) == expected


def test_expiry_moment_itself_is_still_valid(db: Session) -> None:
    """Kelvin 决定 2：到期时刻是仍有效的最后一刻；当前时间截到整秒后晚于它才算过期。"""
    expires = lot_expires_at(NOW)
    assert not is_lot_expired(expires, expires)
    assert not is_lot_expired(expires, expires + timedelta(microseconds=999_999))
    assert is_lot_expired(expires, expires + ONE_SECOND)

    member = _member(db)
    _earn(db, member, 100, NOW)
    assert available_points(db, member.id, expires) == 100
    assert available_points(db, member.id, expires + timedelta(microseconds=500_000)) == 100
    assert available_points(db, member.id, expires + ONE_SECOND) == 0


def test_earn_truncates_acquired_at_around_myt_midnight(db: Session) -> None:
    """Kelvin 决定 2 与 MySQL DATETIME 的取整：取得时间截到整秒再写入并据以计算到期时刻，
    马来西亚时间午夜前后的取得时刻各得到正确的到期日。"""
    member = _member(db)
    before = _earn(db, member, 10, datetime(2026, 12, 31, 15, 59, 59, 700_000))
    after = _earn(db, member, 10, datetime(2026, 12, 31, 16, 0, 0, 300_000))
    db.expire_all()

    assert before.acquired_at == datetime(2026, 12, 31, 15, 59, 59)
    assert before.expires_at == datetime(2027, 12, 31, 15, 59, 59)
    assert after.acquired_at == datetime(2026, 12, 31, 16, 0)
    assert after.expires_at == datetime(2028, 1, 1, 15, 59, 59)
    created = db.scalars(select(PointEntry.created_at)).all()
    assert all(value.microsecond == 0 for value in created)


# ---------------------------------------------------------------------------
# 获得
# ---------------------------------------------------------------------------


def test_earn_writes_lot_and_earn_entry_once(db: Session) -> None:
    """DESIGN 第 3 条「模拟支付成功后」获得积分；Kelvin 决定 1「每单获得的积分为一个批次」：
    写一个批次与一条 earn，同一订单重复调用返回已有批次、不重复发放。"""
    member = _member(db)
    order = _order(db, member.id)

    lot = earn_points(db, member.id, order.id, 19, NOW)
    assert lot is not None
    assert (lot.member_id, lot.order_id, lot.points) == (member.id, order.id, 19)
    assert (lot.acquired_at, lot.expires_at) == (NOW, NOW_LOT_EXPIRES)
    assert _entries(db) == [(KIND_EARN, lot.id, 19, order.id)]

    again = earn_points(db, member.id, order.id, 19, NOW + ONE_SECOND)
    assert again is not None and again.id == lot.id
    assert db.scalar(select(func.count()).select_from(PointLot)) == 1
    assert _entries(db) == [(KIND_EARN, lot.id, 19, order.id)]
    assert available_points(db, member.id, NOW) == 19


def test_earn_zero_writes_nothing(db: Session) -> None:
    """DESIGN 第 3 条：现金实付不满 RM1 时获得 0 积分，不写批次也不写流水。"""
    member = _member(db)
    order = _order(db, member.id)
    assert earn_points(db, member.id, order.id, 0, NOW) is None
    assert db.scalar(select(func.count()).select_from(PointLot)) == 0
    assert _entry_count(db) == 0


def test_earn_repays_debt_first_in_pairs(db: Session) -> None:
    """模型的 debt_repay：以后获得积分先偿还待抵扣，成对写入（一条从新批次扣出、一条减少
    待抵扣，订单都记新批次的来源订单）；获得不足以还清时余下的仍待抵扣。"""
    member = _member(db)
    _debt(db, member, 50)
    assert debt_points(db, member.id) == 50

    first = _earn(db, member, 19, NOW)
    assert _entries(db, PointEntry.order_id == first.order_id) == [
        (KIND_EARN, first.id, 19, first.order_id),
        (KIND_DEBT_REPAY, first.id, -19, first.order_id),
        (KIND_DEBT_REPAY, None, -19, first.order_id),
    ]
    assert debt_points(db, member.id) == 31
    assert _remaining(db, first) == 0

    second = _earn(db, member, 100, NOW + ONE_SECOND)
    assert _entries(db, PointEntry.order_id == second.order_id) == [
        (KIND_EARN, second.id, 100, second.order_id),
        (KIND_DEBT_REPAY, second.id, -31, second.order_id),
        (KIND_DEBT_REPAY, None, -31, second.order_id),
    ]
    assert debt_points(db, member.id) == 0
    assert available_points(db, member.id, NOW) == 69

    # 没有待抵扣时只写 earn。
    third = _earn(db, member, 5, NOW + 2 * ONE_SECOND)
    assert _entries(db, PointEntry.order_id == third.order_id) == [
        (KIND_EARN, third.id, 5, third.order_id)
    ]


# ---------------------------------------------------------------------------
# 预占
# ---------------------------------------------------------------------------


def test_reserve_uses_earliest_expiring_lot_first_across_lots(db: Session) -> None:
    """DESIGN 第 3 条「先到期先使用」与 Kelvin 决定 1「抵扣先用先到期的批次」：按到期时刻升序
    逐批扣，跨多个批次凑足；后建但先到期的批次先用。"""
    member = _member(db)
    later = _earn(db, member, 100, NOW - timedelta(days=100))
    earlier = _earn(db, member, 100, NOW - timedelta(days=200))
    assert earlier.id > later.id and earlier.expires_at < later.expires_at
    order = _order(db, member.id)

    assert reserve_points(db, member.id, order.id, 150, NOW) is None
    assert _entries(db, PointEntry.order_id == order.id) == [
        (KIND_REDEEM, earlier.id, -100, order.id),
        (KIND_REDEEM, later.id, -50, order.id),
    ]
    assert available_points(db, member.id, NOW) == 50


def test_reserve_same_expiry_uses_lower_lot_id_first(db: Session) -> None:
    """DESIGN 第 3 条「先到期先使用」：同一马来西亚日期取得的批次到期时刻相同，按批次 ID 升序，
    不按取得的时分。"""
    member = _member(db)
    # 马来西亚时间 10:00 与 09:00 取得：先建的批次取得得较晚，但 ID 较小。
    first = _earn(db, member, 100, datetime(2026, 10, 12, 2, 0))
    second = _earn(db, member, 100, datetime(2026, 10, 12, 1, 0))
    assert first.expires_at == second.expires_at and first.id < second.id
    order = _order(db, member.id)

    reserve_points(db, member.id, order.id, 120, NOW)
    assert _entries(db, PointEntry.order_id == order.id) == [
        (KIND_REDEEM, first.id, -100, order.id),
        (KIND_REDEEM, second.id, -20, order.id),
    ]


def test_reserve_insufficient_raises_and_writes_nothing(db: Session) -> None:
    """DESIGN 第 3 条「批次在取得后 12 个月到期」：已过期批次不可用；可用余额不足时抛
    InsufficientPoints 且不写记录（HANDOFF 2026-10-11 决定 2：不可用时整单拒绝）。"""
    member = _member(db)
    expired = _earn(db, member, 500, LONG_AGO)
    valid = _earn(db, member, 100, NOW - timedelta(days=1))
    assert is_lot_expired(expired.expires_at, NOW)
    assert available_points(db, member.id, NOW) == 100
    order = _order(db, member.id)
    before = _entry_count(db)

    with pytest.raises(InsufficientPoints) as excinfo:
        reserve_points(db, member.id, order.id, 101, NOW)
    assert member.phone not in str(excinfo.value)
    assert _entry_count(db) == before

    reserve_points(db, member.id, order.id, 100, NOW)
    assert _entries(db, PointEntry.order_id == order.id) == [
        (KIND_REDEEM, valid.id, -100, order.id)
    ]


def test_reserve_zero_writes_nothing(db: Session) -> None:
    """DESIGN 第 3 条「结账抵扣时预占」：不用积分的订单不写流水。"""
    member = _member(db)
    _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    before = _entry_count(db)
    reserve_points(db, member.id, order.id, 0, NOW)
    assert _entry_count(db) == before


def test_reserve_twice_for_same_order_raises(db: Session) -> None:
    """DESIGN 第 3 条「结账抵扣时预占」：一单只在下单时预占一次，同一订单再次预占抛 ValueError。"""
    member = _member(db)
    _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 30, NOW)
    before = _entry_count(db)
    with pytest.raises(ValueError):
        reserve_points(db, member.id, order.id, 30, NOW)
    assert _entry_count(db) == before


@pytest.mark.parametrize("case", ["deleted-member", "other-member-order", "guest-order"])
@pytest.mark.parametrize("operation", ["earn", "reserve"])
def test_earn_and_reserve_require_active_member_and_own_order(
    db: Session, case: str, operation: str
) -> None:
    """「权限与资料保护」第 3 条（注销后积分作废，不再获得或使用）与 DESIGN 第 3 条「游客不积累
    积分」：会员不是 active、订单属于另一会员或是游客订单时，获得与预占抛 ValueError、不写记录。"""
    member = _member(db)
    other = _member(db)
    _earn(db, member, 100, NOW)
    if case == "deleted-member":
        order = _order(db, member.id)
        _delete_member(db, member)
    elif case == "other-member-order":
        order = _order(db, other.id)
    else:
        order = _order(db, None)
    before = _entry_count(db)

    with pytest.raises(ValueError):
        if operation == "earn":
            earn_points(db, member.id, order.id, 10, NOW)
        else:
            reserve_points(db, member.id, order.id, 10, NOW)
    assert _entry_count(db) == before


# ---------------------------------------------------------------------------
# 释放
# ---------------------------------------------------------------------------


def test_release_restores_balance_and_is_idempotent(db: Session) -> None:
    """DESIGN 第 3 条「失败或取消时释放」：每个批次还回本单的净抵扣，余额恢复；重复释放不再写。"""
    member = _member(db)
    first = _earn(db, member, 100, NOW - timedelta(days=200))
    second = _earn(db, member, 100, NOW - timedelta(days=100))
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 150, NOW)

    assert release_points(db, order.id, NOW + ONE_SECOND) == 150
    assert _entries(db, PointEntry.order_id == order.id, PointEntry.kind == KIND_RELEASE) == [
        (KIND_RELEASE, first.id, 100, order.id),
        (KIND_RELEASE, second.id, 50, order.id),
    ]
    assert available_points(db, member.id, NOW) == 200

    before = _entry_count(db)
    assert release_points(db, order.id, NOW + 2 * ONE_SECOND) == 0
    assert _entry_count(db) == before


def test_release_without_redeem_writes_nothing(db: Session) -> None:
    """DESIGN 第 3 条「失败或取消时释放」：没用积分的订单取消时没有可释放的，不写记录。"""
    member = _member(db)
    order = _order(db, member.id)
    assert release_points(db, order.id, NOW) == 0
    assert _entry_count(db) == 0


def test_release_of_expired_lot_expires_immediately(db: Session) -> None:
    """Kelvin 决定 4「订单若之后取消而批次已过期，释放的积分随即记为到期」：release 后紧接着
    等量的 expire（订单记批次的来源订单），批次剩余与可用余额都不因释放而增加。"""
    member = _member(db)
    lot = _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 60, NOW_LOT_EXPIRES)
    after_expiry = NOW_LOT_EXPIRES + ONE_SECOND

    assert release_points(db, order.id, after_expiry) == 60
    assert _entries(db, PointEntry.id > 2) == [
        (KIND_RELEASE, lot.id, 60, order.id),
        (KIND_EXPIRE, lot.id, -60, lot.order_id),
    ]
    assert _remaining(db, lot) == 40
    assert available_points(db, member.id, after_expiry) == 0


def test_release_for_deleted_member_forfeits_unexpired_lot(db: Session) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」：会员在订单取消前已注销（订单的会员 ID 已
    清空），释放未过期批次的积分随即作废。"""
    member = _member(db)
    lot = _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 60, NOW)
    assert forfeit_points(db, member.id, NOW) == 40
    _delete_member(db, member)
    order.member_id = None
    db.flush()

    assert release_points(db, order.id, NOW + ONE_SECOND) == 60
    assert _entries(db, PointEntry.kind.in_((KIND_RELEASE, KIND_FORFEIT))) == [
        (KIND_FORFEIT, lot.id, -40, lot.order_id),
        (KIND_RELEASE, lot.id, 60, order.id),
        (KIND_FORFEIT, lot.id, -60, lot.order_id),
    ]
    assert _remaining(db, lot) == 0


def test_release_for_deleted_member_of_expired_lot_records_expire(db: Session) -> None:
    """Kelvin 决定 4 与「权限与资料保护」第 3 条：已注销会员释放已过期批次时记为到期而不是作废
    （已过期优先记为到期）。"""
    member = _member(db)
    lot = _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 60, NOW)
    _delete_member(db, member)
    order.member_id = None
    db.flush()

    assert release_points(db, order.id, NOW_LOT_EXPIRES + ONE_SECOND) == 60
    assert _entries(db, PointEntry.kind.in_((KIND_RELEASE, KIND_EXPIRE, KIND_FORFEIT))) == [
        (KIND_RELEASE, lot.id, 60, order.id),
        (KIND_EXPIRE, lot.id, -60, lot.order_id),
    ]


# ---------------------------------------------------------------------------
# 到期
# ---------------------------------------------------------------------------


def test_reserved_points_stay_valid_after_expiry_and_expire_skips_them(db: Session) -> None:
    """Kelvin 决定 4「预占时批次未过期即有效，之后在支付窗口内到期也照常核销；到期处理跳过已
    预占的部分」：支付成功不另写流水，到期只扣剩余，可重复运行。"""
    member = _member(db)
    lot = _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 60, NOW_LOT_EXPIRES)
    after_expiry = NOW_LOT_EXPIRES + ONE_SECOND

    # 支付成功（之后的任务改订单状态）不需要另写积分流水，预占的 60 仍记在本单上。
    assert _entries(db, PointEntry.order_id == order.id) == [(KIND_REDEEM, lot.id, -60, order.id)]
    assert expire_points(db, after_expiry, 10) == 1
    assert _entries(db, PointEntry.kind == KIND_EXPIRE) == [
        (KIND_EXPIRE, lot.id, -40, lot.order_id)
    ]
    assert _remaining(db, lot) == 0
    assert _entries(db, PointEntry.order_id == order.id) == [(KIND_REDEEM, lot.id, -60, order.id)]
    assert expire_points(db, after_expiry, 10) == 0


def test_expire_respects_batch_size_and_selection_order(db: Session) -> None:
    """DESIGN 第 3 条「批次在取得后 12 个月到期」：到期写等于剩余的 expire；每批最多上限个批次，
    按会员 ID 升序、同一会员内按到期时刻与批次 ID 升序选取，可重复运行；未过期与剩余为 0 的批次
    不处理。"""
    first_member = _member(db)
    second_member = _member(db)
    # 第二个会员的批次最先建、最早到期，但会员 ID 较大，排在第一个会员的批次之后。
    other = _earn(db, second_member, 30, LONG_AGO - timedelta(days=40))
    # 第一个会员：最早到期的批次被整批预占（剩余 0）；later 先建、ID 较小但到期较晚。
    used_up = _earn(db, first_member, 5, LONG_AGO - timedelta(days=30))
    later = _earn(db, first_member, 20, LONG_AGO)
    earlier = _earn(db, first_member, 10, LONG_AGO - timedelta(days=10))
    order = _order(db, first_member.id)
    reserve_points(db, first_member.id, order.id, 5, LONG_AGO)
    assert _remaining(db, used_up) == 0
    valid = _earn(db, first_member, 40, NOW)

    assert expire_points(db, NOW, 1) == 1
    assert _entries(db, PointEntry.kind == KIND_EXPIRE) == [
        (KIND_EXPIRE, earlier.id, -10, earlier.order_id)
    ]

    assert expire_points(db, NOW, 2) == 2
    assert _entries(db, PointEntry.kind == KIND_EXPIRE) == [
        (KIND_EXPIRE, earlier.id, -10, earlier.order_id),
        (KIND_EXPIRE, later.id, -20, later.order_id),
        (KIND_EXPIRE, other.id, -30, other.order_id),
    ]

    before = _entry_count(db)
    assert expire_points(db, NOW, 10) == 0
    assert _entry_count(db) == before
    for lot in (other, used_up, later, earlier):
        assert _remaining(db, lot) == 0
    assert _remaining(db, valid) == 40
    assert available_points(db, first_member.id, NOW) == 40


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5, "10", None])
def test_expire_rejects_non_positive_batch_size(db: Session, batch_size: Any) -> None:
    """契约「每批上限须为正整数（否则抛 ValueError）」：上限为 0、负数、布尔、小数、字符串或空都
    拒绝，不写记录。"""
    member = _member(db)
    _earn(db, member, 10, LONG_AGO)
    before = _entry_count(db)
    with pytest.raises(ValueError):
        expire_points(db, NOW, batch_size)
    assert _entry_count(db) == before


# ---------------------------------------------------------------------------
# 作废
# ---------------------------------------------------------------------------


def test_forfeit_clears_balance_and_keeps_debt(db: Session) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」：剩余大于 0 的未过期批次写 forfeit、已过期
    的写 expire，作废后可用余额为 0；待抵扣不作废。"""
    member = _member(db)
    expired = _earn(db, member, 50, LONG_AGO)
    first = _earn(db, member, 100, NOW - timedelta(days=10))
    second = _earn(db, member, 30, NOW)
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, 30, NOW)
    _debt(db, member, 25)

    assert forfeit_points(db, member.id, NOW) == 100
    assert _entries(db, PointEntry.kind.in_((KIND_EXPIRE, KIND_FORFEIT))) == [
        (KIND_EXPIRE, expired.id, -50, expired.order_id),
        (KIND_FORFEIT, first.id, -70, first.order_id),
        (KIND_FORFEIT, second.id, -30, second.order_id),
    ]
    assert available_points(db, member.id, NOW) == 0
    assert debt_points(db, member.id) == 25

    before = _entry_count(db)
    assert forfeit_points(db, member.id, NOW) == 0
    assert _entry_count(db) == before


# ---------------------------------------------------------------------------
# DESIGN 第 4 条算例
# ---------------------------------------------------------------------------


def test_design_example_redeem_300_and_earn_19(db: Session) -> None:
    """DESIGN 第 4 条算例「使用 300 积分抵 RM3 … 整单获得 19 积分」：下单预占写 redeem −300，
    支付成功写 19 积分的批次与 earn +19。"""
    member = _member(db)
    old = _earn(db, member, 500, NOW - timedelta(days=30))
    order = _order(
        db,
        member.id,
        subtotal_sen=2500,
        coupon_discount_sen=250,
        points_redeemed=300,
        shipping_fee_sen=800,
        total_sen=2750,
        points_earned=19,
    )

    reserve_points(db, member.id, order.id, order.points_redeemed, NOW)
    paid_at = NOW + timedelta(minutes=5)
    lot = earn_points(db, member.id, order.id, order.points_earned, paid_at)
    assert lot is not None

    assert _entries(db, PointEntry.order_id == order.id) == [
        (KIND_REDEEM, old.id, -300, order.id),
        (KIND_EARN, lot.id, 19, order.id),
    ]
    assert (lot.points, lot.acquired_at, lot.expires_at) == (19, paid_at, NOW_LOT_EXPIRES)
    assert available_points(db, member.id, paid_at) == 200 + 19


# ---------------------------------------------------------------------------
# 入参
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("points", "error"),
    [(1.5, TypeError), (True, TypeError), ("3", TypeError), (None, TypeError), (-1, ValueError)],
)
@pytest.mark.parametrize("operation", ["earn", "reserve"])
def test_points_must_be_non_negative_int(
    db: Session, operation: str, points: Any, error: type[Exception]
) -> None:
    """DESIGN 第 3 条「100 积分抵 RM1」与模型「积分一律整数，不与仙混用」：积分不是非负整数时抛
    TypeError 或 ValueError，消息不含会员资料，不写记录。"""
    member = _member(db)
    _earn(db, member, 100, NOW)
    order = _order(db, member.id)
    before = _entry_count(db)
    function = earn_points if operation == "earn" else reserve_points
    with pytest.raises(error) as excinfo:
        function(db, member.id, order.id, points, NOW)
    assert member.phone not in str(excinfo.value)
    assert _entry_count(db) == before


def test_now_must_be_naive_utc(db: Session) -> None:
    """模型「时间一律是不带时区的 UTC」：带时区的当前时间抛 ValueError，不是 datetime 的抛
    TypeError；会员 ID 不是正整数抛 ValueError。"""
    member = _member(db)
    order = _order(db, member.id)
    with pytest.raises(ValueError):
        earn_points(db, member.id, order.id, 10, NOW.replace(tzinfo=UTC))
    with pytest.raises(TypeError):
        release_points(db, order.id, "2026-10-12T04:00:00")
    with pytest.raises(ValueError):
        forfeit_points(db, 0, NOW)
    assert _entry_count(db) == 0

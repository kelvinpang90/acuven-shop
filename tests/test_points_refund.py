"""退款批准时积分的返还与追回，以及退款申请页的积分预估。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 4、5 条与「订单与退款状态」
（批准时回退未过期的已抵扣积分并追回获得积分；余额不足时扣至 0，剩余记待抵扣积分，以后获得积分
先偿还；不出现负余额），以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定 1（返还以整单为
单位，按该单在各批次的净抵扣从后到期的批次起返还，已过期的批次不返还）。每条测试（参数化的测试是
每个用例）写明它守住的设计或决定原句。

用 SQLite 内存库按模型建表，经 app/services/points.py 的函数准备批次与流水；SQLite 默认不检查
外键，每个连接都须打开。应返还与应追回的积分由调用方按逐件快照合计，这里直接给出合计数。退款
申请停在 requested（审核接入留给之后的任务）。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成
FOR UPDATE：与注销并发、在会员行锁上排队只在 MySQL 上成立，这里只验证锁下的判定与写入。
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
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
)
from app.models.refund import REFUND_REQUESTED
from app.services.points import (
    available_points,
    debt_points,
    earn_points,
    forfeit_points,
    reserve_points,
)
from app.services.points_refund import (
    RefundPoints,
    apply_refund_points,
    estimate_refund_points,
)

CREATED = datetime(2026, 10, 1, 2, 0)
# 马来西亚时间 2026-10-12 12:00。
NOW = datetime(2026, 10, 12, 4, 0)
# 这一天取得的批次在 NOW 时已过期。
LONG_AGO = NOW - timedelta(days=400)
FINGERPRINT = "0f" * 32
ONE_SECOND = timedelta(seconds=1)
ONE_DAY = timedelta(days=1)

_PHONE_SEQUENCE = itertools.count(200_000_000)
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
    """会员订单，商品 RM60、券抵 RM10、运费 RM8；每次给不同的订单号与幂等键。"""
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


def _refund(db: Session, order: Order) -> RefundRequest:
    return _add(
        db,
        RefundRequest(
            order_id=order.id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_MEMBER,
            idempotency_key=f"refund-key-{next(_REFUND_SEQUENCE)}",
            request_fingerprint=FINGERPRINT,
            amount_sen=780,
            created_at=CREATED,
        ),
    )


def _earn(db: Session, member: Member, points: int, now: datetime) -> PointLot:
    """为该会员新建一张订单并在 now 获得积分。"""
    order = _order(db, member.id)
    lot = earn_points(db, member.id, order.id, points, now)
    assert lot is not None
    return lot


def _spend(db: Session, member: Member, points: int, now: datetime) -> Order:
    """该会员另下一单、在 now 预占积分（先到期先用）。"""
    order = _order(db, member.id)
    reserve_points(db, member.id, order.id, points, now)
    return order


def _design_order(db: Session, member: Member) -> tuple[Order, PointLot, PointLot]:
    """DESIGN 第 4 条算例：会员有 500 积分的旧批次，下单抵扣 300 积分、支付成功获得 19 积分。

    返回（订单、旧批次、本单获得的批次）。
    """
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
    reserve_points(db, member.id, order.id, 300, NOW)
    lot = earn_points(db, member.id, order.id, 19, NOW)
    assert lot is not None
    return order, old, lot


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
# DESIGN 第 4 条算例
# ---------------------------------------------------------------------------


def test_design_example_refund_first_a_returns_120_and_reverses_8(db: Session) -> None:
    """DESIGN 第 4 条算例「仅退第一件 A 且原抵扣积分尚未到期时 … 返还原抵扣的 120 积分，并追回该件
    获得的 8 积分」：返还记在原抵扣的批次上，追回从本单获得的批次扣。"""
    member = _member(db)
    order, old, lot = _design_order(db, member)
    refund = _refund(db, order)
    when = NOW + ONE_DAY

    result = apply_refund_points(db, order.id, refund.id, 120, 8, when)
    assert result == RefundPoints(returned=120, reversed_from_balance=8, debt_added=0)
    assert result.reversed_total == 8
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_RETURN, old.id, 120, order.id),
        (KIND_REFUND_REVERSAL, lot.id, -8, order.id),
    ]
    assert _remaining(db, old) == 200 + 120
    assert _remaining(db, lot) == 11
    assert available_points(db, member.id, when) == 331
    assert debt_points(db, member.id) == 0


# ---------------------------------------------------------------------------
# 返还
# ---------------------------------------------------------------------------


def _two_lot_order(db: Session, member: Member) -> tuple[Order, PointLot, PointLot]:
    """先到期的批次 100、后到期的批次 100，订单抵扣 150：先到期的用 100、后到期的用 50。"""
    early = _earn(db, member, 100, NOW - timedelta(days=200))
    late = _earn(db, member, 100, NOW - timedelta(days=100))
    assert early.expires_at < late.expires_at
    order = _spend(db, member, 150, NOW)
    return order, early, late


def test_return_starts_from_later_expiring_lot(db: Session) -> None:
    """Kelvin 决定 1「按抵扣的相反顺序返还（后到期的先还）… 按该单在各批次的净抵扣从后到期的批次
    起返还」：返还 60 时后到期的批次还满其净抵扣 50，余下 10 还给先到期的批次。"""
    member = _member(db)
    order, early, late = _two_lot_order(db, member)
    refund = _refund(db, order)

    result = apply_refund_points(db, order.id, refund.id, 60, 0, NOW + ONE_SECOND)
    assert result == RefundPoints(returned=60, reversed_from_balance=0, debt_added=0)
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_RETURN, late.id, 50, order.id),
        (KIND_REFUND_RETURN, early.id, 10, order.id),
    ]


def test_return_same_expiry_starts_from_higher_lot_id(db: Session) -> None:
    """Kelvin 决定 1「后到期的先还」（抵扣的相反顺序）：到期时刻相同的批次抵扣时按 ID 升序用，
    返还时按 ID 降序还。"""
    member = _member(db)
    first = _earn(db, member, 100, datetime(2026, 10, 12, 2, 0))
    second = _earn(db, member, 100, datetime(2026, 10, 12, 1, 0))
    assert first.expires_at == second.expires_at and first.id < second.id
    order = _spend(db, member, 120, NOW)
    refund = _refund(db, order)

    apply_refund_points(db, order.id, refund.id, 30, 0, NOW + ONE_SECOND)
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_RETURN, second.id, 20, order.id),
        (KIND_REFUND_RETURN, first.id, 10, order.id),
    ]


@pytest.mark.parametrize(
    ("moment", "expected_lots", "returned"),
    [
        # Kelvin 决定 1：两个批次都未过期时 120 全部返还，后到期的先还。
        pytest.param("none-expired", [("late", 50), ("early", 70)], 120, id="none-expired"),
        # DESIGN 第 5 条「原抵扣积分批次若已经过期，不返还已过期积分」与 Kelvin 决定 1「已过期的
        # 批次不返还」：先到期批次已过期而后到期批次未过期时，只返还后到期批次的 50，分到先到期
        # 批次的 70 不返还也不记流水。
        pytest.param("early-expired", [("late", 50)], 50, id="early-expired"),
        # 同上：两个批次都已过期时什么都不返还、不记流水。
        pytest.param("both-expired", [], 0, id="both-expired"),
    ],
)
def test_expired_lot_share_is_not_returned(
    db: Session, moment: str, expected_lots: list[tuple[str, int]], returned: int
) -> None:
    """DESIGN 第 5 条与 Kelvin 决定 1：各用例写在参数旁。"""
    member = _member(db)
    order, early, late = _two_lot_order(db, member)
    refund = _refund(db, order)
    when = {
        "none-expired": NOW + ONE_SECOND,
        "early-expired": early.expires_at + ONE_SECOND,
        "both-expired": late.expires_at + ONE_SECOND,
    }[moment]
    lots = {"early": early, "late": late}

    result = apply_refund_points(db, order.id, refund.id, 120, 0, when)
    assert result == RefundPoints(returned=returned, reversed_from_balance=0, debt_added=0)
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_RETURN, lots[name].id, points, order.id) for name, points in expected_lots
    ]


# ---------------------------------------------------------------------------
# 累计上限
# ---------------------------------------------------------------------------


def test_repeated_partial_refunds_stay_within_redeemed_and_earned(db: Session) -> None:
    """DESIGN 第 5 条「累计返还抵扣积分 ≤ 该单实际抵扣积分；累计追回获得积分 ≤ 该单实际获得积分」：
    算例三件逐次退，第三次应返还的超出剩余净抵扣时只还到 300 为止；之后再追回 1 积分超出获得的 19，
    抛 ValueError、不写记录。"""
    member = _member(db)
    order, old, lot = _design_order(db, member)
    when = NOW + ONE_DAY

    first = apply_refund_points(db, order.id, _refund(db, order).id, 120, 8, when)
    second = apply_refund_points(db, order.id, _refund(db, order).id, 120, 7, when)
    # 调用方给了超出的返还数（100 > 剩余净抵扣 60），只还 60。
    third = apply_refund_points(db, order.id, _refund(db, order).id, 100, 4, when)
    assert first == RefundPoints(returned=120, reversed_from_balance=8, debt_added=0)
    assert second == RefundPoints(returned=120, reversed_from_balance=7, debt_added=0)
    assert third == RefundPoints(returned=60, reversed_from_balance=4, debt_added=0)
    assert _remaining(db, old) == 500
    assert _remaining(db, lot) == 0

    before = _entry_count(db)
    with pytest.raises(ValueError):
        apply_refund_points(db, order.id, _refund(db, order).id, 0, 1, when)
    assert _entry_count(db) == before
    # 净抵扣已全部返还，再返还也不写记录。
    nothing = apply_refund_points(db, order.id, _refund(db, order).id, 50, 0, when)
    assert nothing == RefundPoints(returned=0, reversed_from_balance=0, debt_added=0)
    assert _entry_count(db) == before


def test_debt_counts_toward_reversal_limit(db: Session) -> None:
    """DESIGN 第 5 条「累计追回获得积分 ≤ 该单实际获得积分」：记入待抵扣的缺口也算已追回；获得 19、
    已记待抵扣 8 时再追回 12 超出、抛 ValueError，追回 11 恰好到上限。"""
    member = _member(db)
    order = _order(db, member.id)
    earn_points(db, member.id, order.id, 19, NOW)
    _spend(db, member, 19, NOW)

    first = apply_refund_points(db, order.id, _refund(db, order).id, 0, 8, NOW + ONE_SECOND)
    assert first == RefundPoints(returned=0, reversed_from_balance=0, debt_added=8)
    before = _entry_count(db)
    with pytest.raises(ValueError):
        apply_refund_points(db, order.id, _refund(db, order).id, 0, 12, NOW + ONE_SECOND)
    assert _entry_count(db) == before
    last = apply_refund_points(db, order.id, _refund(db, order).id, 0, 11, NOW + ONE_SECOND)
    assert last == RefundPoints(returned=0, reversed_from_balance=0, debt_added=11)
    assert debt_points(db, member.id) == 19


# ---------------------------------------------------------------------------
# 追回与待抵扣
# ---------------------------------------------------------------------------


def test_reversal_takes_own_lot_then_other_lots_by_expiry(db: Session) -> None:
    """DESIGN 第 5 条「追回获得积分」与第 3 条「先到期先使用」：先从本单获得的批次扣，再从其他
    未过期批次按到期时刻升序扣（不按批次 ID）；已过期批次的剩余不扣。"""
    member = _member(db)
    expired = _earn(db, member, 50, LONG_AGO)
    order = _order(db, member.id)
    own = earn_points(db, member.id, order.id, 19, NOW)
    assert own is not None
    # 另一单用掉本单批次的 14（已过期批次不可用），本单批次剩 5。
    _spend(db, member, 14, NOW)
    # 先建的批次 ID 较小但到期较晚。
    later = _earn(db, member, 10, NOW + 2 * ONE_DAY)
    sooner = _earn(db, member, 10, NOW + ONE_DAY)
    assert later.id < sooner.id and sooner.expires_at < later.expires_at
    refund = _refund(db, order)

    result = apply_refund_points(db, order.id, refund.id, 0, 19, NOW + 3 * ONE_DAY)
    assert result == RefundPoints(returned=0, reversed_from_balance=19, debt_added=0)
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_REVERSAL, own.id, -5, order.id),
        (KIND_REFUND_REVERSAL, sooner.id, -10, order.id),
        (KIND_REFUND_REVERSAL, later.id, -4, order.id),
    ]
    assert _remaining(db, expired) == 50
    assert _remaining(db, later) == 6


def test_reversal_shortfall_becomes_debt_and_is_repaid_by_next_earn(db: Session) -> None:
    """DESIGN 第 5 条「积分余额不足时扣至 0，缺口记待抵扣」与「订单与退款状态」「剩余记待抵扣积分，
    以后获得积分先偿还；不出现负余额」：追回 8 时余额只有 3，扣至 0 并记待抵扣 5；之后获得 20
    先偿还 5。"""
    member = _member(db)
    order = _order(db, member.id)
    own = earn_points(db, member.id, order.id, 19, NOW)
    assert own is not None
    _spend(db, member, 16, NOW)
    refund = _refund(db, order)

    result = apply_refund_points(db, order.id, refund.id, 0, 8, NOW + ONE_SECOND)
    assert result == RefundPoints(returned=0, reversed_from_balance=3, debt_added=5)
    assert result.reversed_total == 8
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_REFUND_REVERSAL, own.id, -3, order.id),
        (KIND_DEBT_ADD, None, 5, order.id),
    ]
    assert _remaining(db, own) == 0
    assert available_points(db, member.id, NOW + ONE_SECOND) == 0
    assert debt_points(db, member.id) == 5

    later = _earn(db, member, 20, NOW + 2 * ONE_SECOND)
    assert _entries(db, PointEntry.kind == KIND_DEBT_REPAY) == [
        (KIND_DEBT_REPAY, later.id, -5, later.order_id),
        (KIND_DEBT_REPAY, None, -5, later.order_id),
    ]
    assert debt_points(db, member.id) == 0
    assert available_points(db, member.id, NOW + 2 * ONE_SECOND) == 15


def test_reversal_skips_expired_own_lot(db: Session) -> None:
    """DESIGN 第 5 条「追回获得积分 … 余额不足时扣至 0，缺口记待抵扣」：本单获得的批次已过期时不从
    它扣，余额（未过期批次）为 0 时整笔记待抵扣。"""
    member = _member(db)
    order = _order(db, member.id)
    own = earn_points(db, member.id, order.id, 19, LONG_AGO)
    assert own is not None
    refund = _refund(db, order)

    result = apply_refund_points(db, order.id, refund.id, 0, 8, NOW)
    assert result == RefundPoints(returned=0, reversed_from_balance=0, debt_added=8)
    assert _entries(db, PointEntry.refund_request_id == refund.id) == [
        (KIND_DEBT_ADD, None, 8, order.id)
    ]
    assert _remaining(db, own) == 19


# ---------------------------------------------------------------------------
# 订单不属于 active 会员
# ---------------------------------------------------------------------------


def test_guest_order_writes_nothing(db: Session) -> None:
    """DESIGN 第 3 条「游客不积累积分」：订单的 member_id 为空时不写记录、返回全零。"""
    order = _order(db, None)
    refund = _refund(db, order)
    result = apply_refund_points(db, order.id, refund.id, 120, 8, NOW)
    assert result == RefundPoints(returned=0, reversed_from_balance=0, debt_added=0)
    assert _entry_count(db) == 0


def test_order_of_deleted_member_writes_nothing(db: Session) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」：注销后订单的 member_id 已清空，退款不再
    返还或追回积分，不写记录、返回全零。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    forfeit_points(db, member.id, NOW)
    _delete_member(db, member)
    order.member_id = None
    db.flush()
    refund = _refund(db, order)
    before = _entry_count(db)

    result = apply_refund_points(db, order.id, refund.id, 120, 8, NOW + ONE_DAY)
    assert result == RefundPoints(returned=0, reversed_from_balance=0, debt_added=0)
    assert _entry_count(db) == before


def test_member_found_deleted_after_lock_writes_nothing(db: Session) -> None:
    """「权限与资料保护」第 3 条：与注销并发时，锁后读到会员已不是 active（订单的 member_id 尚未
    清空）同样不写记录、返回全零。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    _delete_member(db, member)
    refund = _refund(db, order)
    before = _entry_count(db)

    result = apply_refund_points(db, order.id, refund.id, 120, 8, NOW + ONE_DAY)
    assert result == RefundPoints(returned=0, reversed_from_balance=0, debt_added=0)
    assert _entry_count(db) == before


def test_refund_request_of_another_order_raises(db: Session) -> None:
    """「订单与退款状态」：积分流水关联被批准的退款申请；申请不属于该订单时抛 ValueError、
    不写记录。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    other = _order(db, member.id)
    refund = _refund(db, other)
    before = _entry_count(db)
    with pytest.raises(ValueError):
        apply_refund_points(db, order.id, refund.id, 120, 8, NOW + ONE_DAY)
    assert _entry_count(db) == before


# ---------------------------------------------------------------------------
# 重复调用
# ---------------------------------------------------------------------------


def test_same_refund_request_does_not_apply_twice(db: Session) -> None:
    """「失败、并发与重试」第 1 条（退款审核使用幂等键）与 DESIGN 第 5 条累计上限：同一退款申请
    再次调用不再写，返回已写的结果（含记入待抵扣的部分），不看这次给的积分数。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    refund = _refund(db, order)
    first = apply_refund_points(db, order.id, refund.id, 120, 8, NOW + ONE_DAY)
    before = _entry_count(db)
    again = apply_refund_points(db, order.id, refund.id, 240, 15, NOW + 2 * ONE_DAY)
    assert again == first
    assert _entry_count(db) == before

    debtor = _member(db)
    debt_order = _order(db, debtor.id)
    earn_points(db, debtor.id, debt_order.id, 19, NOW)
    _spend(db, debtor, 19, NOW)
    debt_refund = _refund(db, debt_order)
    first = apply_refund_points(db, debt_order.id, debt_refund.id, 0, 8, NOW + ONE_SECOND)
    again = apply_refund_points(db, debt_order.id, debt_refund.id, 0, 8, NOW + ONE_SECOND)
    assert first == again == RefundPoints(returned=0, reversed_from_balance=0, debt_added=8)
    assert debt_points(db, debtor.id) == 8


# ---------------------------------------------------------------------------
# 预估
# ---------------------------------------------------------------------------


def test_estimate_matches_approval_and_writes_nothing(db: Session) -> None:
    """DESIGN 第 4 条算例与第 5 条：退款申请页在提交前（没有退款申请 ID）按同一规则预估，只读不写；
    没有变化时与批准时的结果相同，之后的预估在已批准退款之后的状态上计算。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    when = NOW + ONE_DAY
    before = _entry_count(db)

    estimate = estimate_refund_points(db, order.id, 120, 8, when)
    assert estimate == RefundPoints(returned=120, reversed_from_balance=8, debt_added=0)
    assert _entry_count(db) == before
    assert not db.new and not db.dirty

    actual = apply_refund_points(db, order.id, _refund(db, order).id, 120, 8, when)
    assert actual == estimate

    # 第二件 A：剩余净抵扣 180、本单批次剩 11。
    estimate = estimate_refund_points(db, order.id, 120, 7, when)
    assert estimate == RefundPoints(returned=120, reversed_from_balance=7, debt_added=0)
    assert apply_refund_points(db, order.id, _refund(db, order).id, 120, 7, when) == estimate


def test_estimate_with_debt_and_expiry_matches_approval(db: Session) -> None:
    """DESIGN 第 5 条「不返还已过期积分 … 余额不足时扣至 0，缺口记待抵扣」：预估同样不计已过期
    批次的份额、同样把缺口算作待抵扣，refund.points_reversed 为从余额追回与记入待抵扣之和。"""
    member = _member(db)
    order, early, late = _two_lot_order(db, member)
    when = early.expires_at + ONE_SECOND

    estimate = estimate_refund_points(db, order.id, 120, 0, when)
    assert estimate == RefundPoints(returned=50, reversed_from_balance=0, debt_added=0)
    assert apply_refund_points(db, order.id, _refund(db, order).id, 120, 0, when) == estimate

    debtor = _member(db)
    debt_order = _order(db, debtor.id)
    earn_points(db, debtor.id, debt_order.id, 19, NOW)
    _spend(db, debtor, 16, NOW)
    estimate = estimate_refund_points(db, debt_order.id, 0, 8, NOW + ONE_SECOND)
    assert estimate == RefundPoints(returned=0, reversed_from_balance=3, debt_added=5)
    assert estimate.reversed_total == 8
    refund = _refund(db, debt_order)
    assert apply_refund_points(db, debt_order.id, refund.id, 0, 8, NOW + ONE_SECOND) == estimate


def test_estimate_guest_order_and_limit(db: Session) -> None:
    """DESIGN 第 3 条「游客不积累积分」与第 5 条累计上限：游客订单预估为全零；应追回的超出该单
    获得积分时预估同样抛 ValueError。"""
    guest_order = _order(db, None)
    assert estimate_refund_points(db, guest_order.id, 120, 8, NOW) == RefundPoints(0, 0, 0)

    member = _member(db)
    order, _, _ = _design_order(db, member)
    with pytest.raises(ValueError):
        estimate_refund_points(db, order.id, 0, 20, NOW)


# ---------------------------------------------------------------------------
# 入参
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("points", "error"),
    [(1.5, TypeError), (True, TypeError), ("3", TypeError), (-1, ValueError)],
)
def test_points_must_be_non_negative_int(db: Session, points: Any, error: type[Exception]) -> None:
    """模型「积分一律是整数，不与仙混用」：应返还或应追回的积分不是非负整数时抛错、不写记录。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    refund = _refund(db, order)
    before = _entry_count(db)
    with pytest.raises(error):
        apply_refund_points(db, order.id, refund.id, points, 0, NOW)
    with pytest.raises(error):
        apply_refund_points(db, order.id, refund.id, 0, points, NOW)
    with pytest.raises(error):
        estimate_refund_points(db, order.id, points, 0, NOW)
    assert _entry_count(db) == before


def test_now_must_be_naive_utc(db: Session) -> None:
    """模型「时间一律是不带时区的 UTC」：带时区的当前时间抛 ValueError，不是 datetime 的抛
    TypeError。"""
    member = _member(db)
    order, _, _ = _design_order(db, member)
    refund = _refund(db, order)
    with pytest.raises(ValueError):
        apply_refund_points(db, order.id, refund.id, 120, 8, NOW.replace(tzinfo=UTC))
    with pytest.raises(TypeError):
        estimate_refund_points(db, order.id, 120, 8, "2026-10-12T04:00:00")

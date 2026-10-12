"""退款批准时积分的返还与追回，以及退款申请页的积分预估。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 4、5 条（退款批准时按原逐件
快照返还未过期的抵扣积分并追回获得积分；积分余额不足时扣至 0，缺口记待抵扣；原抵扣积分批次若
已经过期，不返还已过期积分；累计返还抵扣积分 ≤ 该单实际抵扣积分，累计追回获得积分 ≤ 该单实际
获得积分）与「订单与退款状态」（批准时回退积分，以后获得积分先偿还待抵扣，不出现负余额），以及
docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定 1：返还以整单为单位，按该单在各批次的净抵扣
从后到期的批次起返还，已过期的批次不返还，不追踪每一件用了哪个批次。
本模块只提供规则函数，不接入退款审核与退款申请接口。

- 两个积分数由调用方按被退的件的逐件快照 points_discount 与 points_earned 合计得出，本模块不读
  逐件快照。
- 返还：该单在每个批次的净抵扣为该单在该批次的 redeem、release 与 refund_return 流水之和取负。
  按到期时刻降序、同时刻按批次 ID 降序依次分配应返还的积分，每个批次分到的不超过其净抵扣；
  分到已过期批次的部分不返还、不记流水，分到未过期批次的部分写 refund_return；净抵扣分完仍有
  剩余的部分不返还。
- 追回（在返还之后）：先从该单获得的批次（未过期时）的剩余扣，再从该会员其他未过期批次按到期
  时刻升序、同时刻按批次 ID 升序扣，都写 refund_reversal；仍不足的缺口写一条 debt_add。该单获得
  的积分减去以往各次退款的 refund_reversal 与 debt_add 之和小于应追回的积分时抛 ValueError。
- 订单的 member_id 为空（游客订单或会员已注销）时不写记录、返回全零。被认领的游客订单的
  member_id 不为空，但没有抵扣与获得积分，照常走规则，返还与追回都是 0。
- 锁：先以普通读取订单的 member_id，再以 SELECT … FOR UPDATE 锁会员行，锁后以加锁读重新读取
  订单的 member_id、会员状态、该会员的批次与流水（批次与会员以 populate_existing 重新载入），
  累计上限也以此为准。调用方（退款审核）已持有订单行锁，这里的顺序是订单 → 会员，与
  app/services/points.py 的会员 → 订单相反：与注销并发时 MySQL 可能判定死锁并回滚其中一方，由
  调用方按现有的数据库错误处理回答，这里不重试。SQLite 不生成 FOR UPDATE，并发只在 MySQL 上成立。
- 预估只读、不写库、不锁行，按同一规则在当前（已批准退款之后）的状态上计算；预估与批准时可能因
  到期或余额变化而不同，以批准时为准。

时间一律是不带时区的 UTC；积分一律整数。写库的函数只 flush、不提交。本模块不写日志。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member, Order, PointEntry, PointLot, RefundRequest
from app.models.member import MEMBER_ACTIVE
from app.models.points import (
    KIND_DEBT_ADD,
    KIND_REDEEM,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_RELEASE,
)
from app.services.points import (
    _naive_second,
    _non_negative_points,
    _positive_id,
    is_lot_expired,
)

# 某单在某批次的净抵扣由这三类流水求和取负（与 app/services/points.py 相同）。
_NET_REDEEM_KINDS = (KIND_REDEEM, KIND_RELEASE, KIND_REFUND_RETURN)


@dataclass(frozen=True)
class RefundPoints:
    """一次退款的积分结果：实际返还、从余额追回与记入待抵扣的积分。"""

    returned: int
    reversed_from_balance: int
    debt_added: int

    @property
    def reversed_total(self) -> int:
        """从余额追回与记入待抵扣之和，即退款申请页的 refund.points_reversed。"""
        return self.reversed_from_balance + self.debt_added


_ZERO = RefundPoints(returned=0, reversed_from_balance=0, debt_added=0)


@dataclass(frozen=True)
class _Plan:
    # （批次 ID、积分），积分为正数。
    returns: tuple[tuple[int, int], ...]
    reversals: tuple[tuple[int, int], ...]
    debt: int

    def result(self) -> RefundPoints:
        return RefundPoints(
            returned=sum(points for _, points in self.returns),
            reversed_from_balance=sum(points for _, points in self.reversals),
            debt_added=self.debt,
        )


def _plan(
    order_id: int,
    lots: Sequence[PointLot],
    entries: Iterable[tuple[int | None, str, int, int]],
    points_back: int,
    points_reversed: int,
    now: datetime,
) -> _Plan:
    """按返还与追回规则算出要写的流水。

    lots 为该会员的全部批次，entries 为该会员的全部流水（批次、类别、积分、订单）。
    应追回的积分超过该单获得积分减去以往已追回与已记待抵扣之和时抛 ValueError。
    """
    balances: dict[int, int] = {}
    net_redeemed: dict[int, int] = {}
    prior_reversed = 0
    for lot_id, kind, points, entry_order_id in entries:
        if lot_id is not None:
            balances[lot_id] = balances.get(lot_id, 0) + points
        if entry_order_id != order_id:
            continue
        if lot_id is not None and kind in _NET_REDEEM_KINDS:
            net_redeemed[lot_id] = net_redeemed.get(lot_id, 0) - points
        elif kind == KIND_REFUND_REVERSAL:
            prior_reversed -= points
        elif kind == KIND_DEBT_ADD:
            prior_reversed += points

    own_lot = next((lot for lot in lots if lot.order_id == order_id), None)
    earned = own_lot.points if own_lot is not None else 0
    if points_reversed > earned - prior_reversed:
        raise ValueError("points to reverse exceed the points earned by the order")

    # 返还：后到期的先还；分到已过期批次的份额照样占去应返还的积分，但不返还。
    returns: list[tuple[int, int]] = []
    left = points_back
    for lot in sorted(lots, key=lambda lot: (lot.expires_at, lot.id), reverse=True):
        if left == 0:
            break
        share = min(net_redeemed.get(lot.id, 0), left)
        if share <= 0:
            continue
        left -= share
        if not is_lot_expired(lot.expires_at, now):
            returns.append((lot.id, share))
            balances[lot.id] = balances.get(lot.id, 0) + share

    # 追回：先本单获得的批次，再其他批次按到期升序；已过期的批次都不扣。
    candidates = [lot for lot in lots if not is_lot_expired(lot.expires_at, now)]
    candidates.sort(key=lambda lot: (lot is not own_lot, lot.expires_at, lot.id))
    reversals: list[tuple[int, int]] = []
    left = points_reversed
    for lot in candidates:
        if left == 0:
            break
        take = min(balances.get(lot.id, 0), left)
        if take <= 0:
            continue
        reversals.append((lot.id, take))
        left -= take
    return _Plan(returns=tuple(returns), reversals=tuple(reversals), debt=left)


def _validate(
    order_id: object, points_back: object, points_reversed: object, now: object
) -> tuple[int, int, int, datetime]:
    return (
        _positive_id(order_id, "order_id"),
        _non_negative_points(points_back, "points_back"),
        _non_negative_points(points_reversed, "points_reversed"),
        _naive_second(now),
    )


def _order_member_id(db: Session, order_id: int, *, lock: bool) -> int | None:
    stmt = select(Order.member_id).where(Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update()
    member_ids = db.execute(stmt).scalars().all()
    if not member_ids:
        raise ValueError("order does not exist")
    return member_ids[0]


def _member_state(
    db: Session, member_id: int, *, lock: bool
) -> tuple[list[PointLot], list[tuple[int | None, str, int, int]]]:
    lots_stmt = (
        select(PointLot)
        .where(PointLot.member_id == member_id)
        .order_by(PointLot.expires_at, PointLot.id)
    )
    entries_stmt = select(
        PointEntry.lot_id, PointEntry.kind, PointEntry.points, PointEntry.order_id
    ).where(PointEntry.member_id == member_id)
    if lock:
        lots_stmt = lots_stmt.with_for_update().execution_options(populate_existing=True)
        entries_stmt = entries_stmt.with_for_update()
    lots = list(db.scalars(lots_stmt).all())
    entries = [(row.lot_id, row.kind, row.points, row.order_id) for row in db.execute(entries_stmt)]
    return lots, entries


# ---------------------------------------------------------------------------
# 退款批准
# ---------------------------------------------------------------------------


def apply_refund_points(
    db: Session,
    order_id: int,
    refund_request_id: int,
    points_back: int,
    points_reversed: int,
    now: datetime,
) -> RefundPoints:
    """退款批准时返还抵扣积分、追回获得积分，返回实际返还、从余额追回与记入待抵扣的积分。

    退款申请须属于该订单，否则抛 ValueError。订单的 member_id 为空，或锁后发现订单已不属于该
    会员、会员已不是 active 时不写记录、返回全零。同一退款申请已有积分流水时不再写、返回已写的
    结果。应追回的积分超出累计上限时抛 ValueError、不写记录。流水的时间为截到整秒的当前时间。
    只 flush、不提交。
    """
    order_id, points_back, points_reversed, now = _validate(
        order_id, points_back, points_reversed, now
    )
    refund_request_id = _positive_id(refund_request_id, "refund_request_id")
    refund_order_id = db.scalars(
        select(RefundRequest.order_id).where(RefundRequest.id == refund_request_id)
    ).one_or_none()
    if refund_order_id != order_id:
        raise ValueError("refund request does not belong to the order")

    # 锁会员之前以普通读定位会员；锁后的判断都用加锁读重新读取。
    member_id = _order_member_id(db, order_id, lock=False)
    if member_id is None:
        return _ZERO
    member = db.scalars(
        select(Member)
        .where(Member.id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if member is None or member.status != MEMBER_ACTIVE:
        return _ZERO
    if _order_member_id(db, order_id, lock=True) != member_id:
        return _ZERO

    written = db.execute(
        select(PointEntry.kind, PointEntry.points)
        .where(PointEntry.refund_request_id == refund_request_id)
        .with_for_update()
    ).all()
    if written:
        return RefundPoints(
            returned=sum(p for kind, p in written if kind == KIND_REFUND_RETURN),
            reversed_from_balance=-sum(p for kind, p in written if kind == KIND_REFUND_REVERSAL),
            debt_added=sum(p for kind, p in written if kind == KIND_DEBT_ADD),
        )

    lots, entries = _member_state(db, member_id, lock=True)
    plan = _plan(order_id, lots, entries, points_back, points_reversed, now)

    def entry(lot_id: int | None, kind: str, points: int) -> PointEntry:
        return PointEntry(
            member_id=member_id,
            lot_id=lot_id,
            kind=kind,
            points=points,
            order_id=order_id,
            refund_request_id=refund_request_id,
            created_at=now,
        )

    for lot_id, points in plan.returns:
        db.add(entry(lot_id, KIND_REFUND_RETURN, points))
    for lot_id, points in plan.reversals:
        db.add(entry(lot_id, KIND_REFUND_REVERSAL, -points))
    if plan.debt > 0:
        db.add(entry(None, KIND_DEBT_ADD, plan.debt))
    db.flush()
    return plan.result()


# ---------------------------------------------------------------------------
# 退款申请页的预估
# ---------------------------------------------------------------------------


def estimate_refund_points(
    db: Session,
    order_id: int,
    points_back: int,
    points_reversed: int,
    now: datetime,
) -> RefundPoints:
    """按批准时的同一规则预估返还、从余额追回与记入待抵扣的积分。只读、不写库、不锁行。

    在当前状态（已批准退款之后）上计算，不计审核中的申请。订单的 member_id 为空或会员不是
    active 时返回全零；应追回的积分超出累计上限时抛 ValueError。批准时可能因到期或余额变化
    而不同，以批准时为准。
    """
    order_id, points_back, points_reversed, now = _validate(
        order_id, points_back, points_reversed, now
    )
    member_id = _order_member_id(db, order_id, lock=False)
    if member_id is None:
        return _ZERO
    status = db.scalars(select(Member.status).where(Member.id == member_id)).one_or_none()
    if status != MEMBER_ACTIVE:
        return _ZERO
    lots, entries = _member_state(db, member_id, lock=False)
    return _plan(order_id, lots, entries, points_back, points_reversed, now).result()

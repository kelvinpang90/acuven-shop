"""积分的获得、预占、释放、到期与作废，以及可用余额与待抵扣的读取。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1、3 条（积分只抵商品额；
模拟支付成功后获得；批次取得后 12 个月到期，先到期先使用；结账抵扣时预占，失败或取消时释放）
与「权限与资料保护」第 3 条（注销时积分余额作废），以及 docs/HANDOFF.md 0.46 记录的 Kelvin
2026-10-10 决定：积分按批次记账；批次在取得日（马来西亚时间）加 12 个自然月那一天的 23:59:59
（马来西亚时间）到期，没有那一天取该月最后一天；已预占的积分以预占时为准，之后批次到期也照常
有效，到期处理跳过已预占的部分，订单取消时批次已过期则释放的积分随即记为到期。
本模块只提供规则函数，不做接口与定时任务，不接入下单、支付与注销；退款的返还与追回留给
SHOP-TASK-087。

- 到期时刻：取得时间先截到整秒（MySQL 的 DATETIME 会把小数秒四舍五入，截掉后写入的值与据以
  计算的值相同），加 8 小时得马来西亚日期，加 12 个自然月（没有那一天取该月最后一天），取那天
  23:59:59 马来西亚时间再减 8 小时。到期时刻本身仍有效，当前时间（同样截到整秒）晚于它才算过期。
- 余额口径：批次剩余为该批次全部流水之和；待抵扣为该会员批次为空的流水之和；可用余额为该会员
  未过期批次剩余之和。
- 会员行锁：每个写函数先以 SELECT … FOR UPDATE 锁住该会员的行，锁后用于判断的订单、批次与流水
  一律加锁读（批次与会员、订单另以 populate_existing 重新载入，不沿用 ORM 缓存），读到最新已
  提交的状态而不是 MySQL 可重复读的事务快照；同一会员的写因此在会员行锁上排队，批次剩余与待
  抵扣都不小于零。锁的顺序为会员 → 订单，与优惠券预占一致；到期函数涉及多个会员时按会员 ID
  升序逐个加锁。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE，并发只在 MySQL
  上成立。
- 释放、到期与作废不要求会员仍为 active；注销后订单的 member_id 已清空，释放从该订单的 redeem
  流水取会员 ID。

时间一律是不带时区的 UTC；积分一律整数，不与仙混用。写库的函数只 flush、不提交，由调用方在同一
事务里提交。本模块不写日志。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Member, Order, PointEntry, PointLot
from app.models.member import MEMBER_ACTIVE
from app.models.points import (
    KIND_DEBT_REPAY,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_REFUND_RETURN,
    KIND_RELEASE,
)

# 马来西亚时间固定为 UTC+8，没有夏令时；写法同 app/services/sms_budget.py。
MALAYSIA_UTC_OFFSET = timedelta(hours=8)
# 批次取得后的有效期：12 个自然月。
LOT_VALID_MONTHS = 12
# 到期当天仍有效的最后一刻（马来西亚时间）。
_LAST_MOMENT = time(23, 59, 59)

# 某单在某批次的净抵扣由这三类流水求和取负。
_NET_REDEEM_KINDS = (KIND_REDEEM, KIND_RELEASE, KIND_REFUND_RETURN)


class InsufficientPoints(Exception):
    """预占时可用余额不足；不写任何记录。"""

    def __init__(self) -> None:
        super().__init__("insufficient points")


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_points(value: object, name: str) -> int:
    # bool 是 int 的子类，但把 True 当 1 积分显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _naive_second(now: object) -> datetime:
    """校验为不带时区的 datetime 并截到整秒。"""
    if not isinstance(now, datetime):
        raise TypeError(f"now must be datetime, got {type(now).__name__}")
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    return now.replace(microsecond=0)


def _add_months(day: date, months: int) -> date:
    """加若干自然月；没有那一天取该月最后一天。"""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


# ---------------------------------------------------------------------------
# 到期时刻与余额
# ---------------------------------------------------------------------------


def lot_expires_at(acquired_at: datetime) -> datetime:
    """批次的到期时刻（仍有效的最后一刻，不带时区的 UTC）。

    取得时间先截到整秒，换算为马来西亚日期，加 12 个自然月（没有那一天取该月最后一天），
    取那天 23:59:59 马来西亚时间再换回 UTC。
    """
    acquired_at = _naive_second(acquired_at)
    acquired_day = (acquired_at + MALAYSIA_UTC_OFFSET).date()
    expiry_day = _add_months(acquired_day, LOT_VALID_MONTHS)
    return datetime.combine(expiry_day, _LAST_MOMENT) - MALAYSIA_UTC_OFFSET


def is_lot_expired(expires_at: datetime, now: datetime) -> bool:
    """当前时间（截到整秒）晚于到期时刻时为已过期；到期时刻本身仍有效。"""
    return _naive_second(now) > expires_at


def available_points(db: Session, member_id: int, now: datetime) -> int:
    """该会员未过期批次剩余之和。只读，不加锁。"""
    member_id = _positive_id(member_id, "member_id")
    now = _naive_second(now)
    total = db.scalar(
        select(func.coalesce(func.sum(PointEntry.points), 0))
        .select_from(PointEntry)
        .join(PointLot, PointLot.id == PointEntry.lot_id)
        .where(PointLot.member_id == member_id, PointLot.expires_at >= now)
    )
    # MySQL 的 SUM 返回 DECIMAL，转回整数。
    return int(total or 0)


def debt_points(db: Session, member_id: int) -> int:
    """该会员的待抵扣：批次为空的流水之和。只读，不加锁。"""
    member_id = _positive_id(member_id, "member_id")
    total = db.scalar(
        select(func.coalesce(func.sum(PointEntry.points), 0)).where(
            PointEntry.member_id == member_id, PointEntry.lot_id.is_(None)
        )
    )
    return int(total or 0)


# ---------------------------------------------------------------------------
# 加锁读
# ---------------------------------------------------------------------------


def _lock_member(db: Session, member_id: int) -> Member | None:
    return db.scalars(
        select(Member)
        .where(Member.id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()


def _lock_active_member_order(db: Session, member_id: int, order_id: int) -> None:
    """锁会员行与订单行；会员不是 active 或订单不属于该会员时抛 ValueError。"""
    member = _lock_member(db, member_id)
    if member is None or member.status != MEMBER_ACTIVE:
        raise ValueError("member is not an active member")
    order = db.scalars(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if order is None or order.member_id != member_id:
        raise ValueError("order does not belong to the member")


def _lock_member_lots(db: Session, member_id: int) -> list[PointLot]:
    """该会员的全部批次，按到期时刻升序、同时刻按批次 ID 升序。"""
    stmt = (
        select(PointLot)
        .where(PointLot.member_id == member_id)
        .order_by(PointLot.expires_at, PointLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(stmt).all())


def _sum_by_lot(rows: Iterable[tuple[int | None, int]]) -> dict[int | None, int]:
    totals: dict[int | None, int] = {}
    for lot_id, points in rows:
        totals[lot_id] = totals.get(lot_id, 0) + points
    return totals


def _lock_member_balances(db: Session, member_id: int) -> dict[int | None, int]:
    """加锁读该会员的全部流水，按批次求和；键 None 为待抵扣。

    取行后在 Python 里求和，不对聚合加锁。
    """
    rows = db.execute(
        select(PointEntry.lot_id, PointEntry.points)
        .where(PointEntry.member_id == member_id)
        .with_for_update()
    ).all()
    return _sum_by_lot(rows)


def _entry(
    member_id: int, lot_id: int | None, kind: str, points: int, order_id: int, now: datetime
) -> PointEntry:
    return PointEntry(
        member_id=member_id,
        lot_id=lot_id,
        kind=kind,
        points=points,
        order_id=order_id,
        refund_request_id=None,
        created_at=now,
    )


# ---------------------------------------------------------------------------
# 获得与预占
# ---------------------------------------------------------------------------


def earn_points(
    db: Session, member_id: int, order_id: int, points: int, now: datetime
) -> PointLot | None:
    """支付成功时获得积分：写一个批次与一条 earn 流水，再以新批次偿还待抵扣。

    锁会员行与订单行后核对会员为 active、订单属于该会员，否则抛 ValueError。同一订单已有
    批次时不再写、返回已有批次；获得积分为 0 时不写记录、返回 None。有待抵扣时以
    min(待抵扣, 获得积分) 成对写两条 debt_repay：一条从新批次扣出、一条减少待抵扣，订单都记
    该订单。取得时间为截到整秒的当前时间。只 flush、不提交。
    """
    member_id = _positive_id(member_id, "member_id")
    order_id = _positive_id(order_id, "order_id")
    points = _non_negative_points(points, "points")
    now = _naive_second(now)

    _lock_active_member_order(db, member_id, order_id)
    existing = db.scalars(
        select(PointLot)
        .where(PointLot.order_id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if existing is not None:
        return existing
    if points == 0:
        return None

    debt = _lock_member_balances(db, member_id).get(None, 0)
    lot = PointLot(
        member_id=member_id,
        order_id=order_id,
        points=points,
        acquired_at=now,
        expires_at=lot_expires_at(now),
        created_at=now,
    )
    db.add(lot)
    db.flush()
    db.add(_entry(member_id, lot.id, KIND_EARN, points, order_id, now))
    repay = min(debt, points)
    if repay > 0:
        db.add(_entry(member_id, lot.id, KIND_DEBT_REPAY, -repay, order_id, now))
        db.add(_entry(member_id, None, KIND_DEBT_REPAY, -repay, order_id, now))
    db.flush()
    return lot


def reserve_points(db: Session, member_id: int, order_id: int, points: int, now: datetime) -> None:
    """下单时预占：按到期时刻升序、同时刻按批次 ID 逐批写 redeem 流水直到凑足。

    锁会员行与订单行后核对会员为 active、订单属于该会员，同一订单已有 redeem 流水时抛
    ValueError；抵扣积分为 0 时不写记录；可用余额（未过期批次剩余之和）不足时抛
    InsufficientPoints、不写记录。预占即抵扣，支付成功时不另写流水。只 flush、不提交。
    """
    member_id = _positive_id(member_id, "member_id")
    order_id = _positive_id(order_id, "order_id")
    points = _non_negative_points(points, "points")
    now = _naive_second(now)

    _lock_active_member_order(db, member_id, order_id)
    existing = db.scalars(
        select(PointEntry.id)
        .where(PointEntry.order_id == order_id, PointEntry.kind == KIND_REDEEM)
        .with_for_update()
    ).first()
    if existing is not None:
        raise ValueError("order already has redeemed points")
    if points == 0:
        return

    lots = [
        lot for lot in _lock_member_lots(db, member_id) if not is_lot_expired(lot.expires_at, now)
    ]
    balances = _lock_member_balances(db, member_id)
    if sum(balances.get(lot.id, 0) for lot in lots) < points:
        raise InsufficientPoints()

    needed = points
    for lot in lots:
        take = min(balances.get(lot.id, 0), needed)
        if take <= 0:
            continue
        db.add(_entry(member_id, lot.id, KIND_REDEEM, -take, order_id, now))
        needed -= take
        if needed == 0:
            break
    db.flush()


# ---------------------------------------------------------------------------
# 释放
# ---------------------------------------------------------------------------


def release_points(db: Session, order_id: int, now: datetime) -> int:
    """取消或支付超时时释放：对该订单在每个批次尚未释放的净抵扣写一条 release 流水。

    该批次此时已过期时紧接着写一条等量的 expire（Kelvin 决定 4）；未过期而会员已注销时
    紧接着写等量的 forfeit。会员 ID 取自该订单的 redeem 流水（注销后订单的 member_id 已清空），
    不要求会员仍为 active。没有可释放的净抵扣时不写记录、返回 0（重复调用不出错）。返回释放的
    积分数。只 flush、不提交。
    """
    order_id = _positive_id(order_id, "order_id")
    now = _naive_second(now)

    # 锁会员之前以普通读定位会员；锁后的判断都用加锁读重新读取。
    stmt = select(PointEntry.member_id).where(
        PointEntry.order_id == order_id, PointEntry.kind == KIND_REDEEM
    )
    member_ids = set(db.scalars(stmt).all())
    if not member_ids:
        return 0
    if len(member_ids) > 1:
        raise ValueError("order has redeemed points of more than one member")
    member_id = member_ids.pop()

    member = _lock_member(db, member_id)
    if member is None:
        raise ValueError("member does not exist")
    rows = db.execute(
        select(PointEntry.lot_id, PointEntry.points)
        .where(
            PointEntry.order_id == order_id,
            PointEntry.member_id == member_id,
            PointEntry.lot_id.is_not(None),
            PointEntry.kind.in_(_NET_REDEEM_KINDS),
        )
        .with_for_update()
    ).all()
    net_by_lot = {lot_id: -total for lot_id, total in _sum_by_lot(rows).items()}
    lot_ids = [lot_id for lot_id, net in net_by_lot.items() if net > 0]
    if not lot_ids:
        return 0
    lots = db.scalars(
        select(PointLot)
        .where(PointLot.id.in_(lot_ids))
        .order_by(PointLot.expires_at, PointLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()

    released = 0
    for lot in lots:
        net = net_by_lot[lot.id]
        db.add(_entry(member_id, lot.id, KIND_RELEASE, net, order_id, now))
        # 已过期优先记为到期，与作废函数一致。
        if is_lot_expired(lot.expires_at, now):
            db.add(_entry(member_id, lot.id, KIND_EXPIRE, -net, lot.order_id, now))
        elif member.status != MEMBER_ACTIVE:
            db.add(_entry(member_id, lot.id, KIND_FORFEIT, -net, lot.order_id, now))
        released += net
    db.flush()
    return released


# ---------------------------------------------------------------------------
# 到期与作废
# ---------------------------------------------------------------------------


def expire_points(db: Session, now: datetime, batch_size: int) -> int:
    """对已过期且剩余大于 0 的批次各写一条等于剩余的 expire 流水，一次最多 batch_size 个批次。

    候选批次以普通读按会员 ID 升序、同一会员内按到期时刻与批次 ID 升序选取；再按会员 ID 升序
    逐个锁会员行，以加锁读重新核对批次已过期且剩余大于 0 后写入。已预占的部分已从剩余里扣掉，
    自然被跳过。返回写了 expire 的批次数，可重复运行。只 flush、不提交。
    """
    now = _naive_second(now)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    remaining = (
        select(PointEntry.lot_id, func.sum(PointEntry.points).label("remaining"))
        .where(PointEntry.lot_id.is_not(None))
        .group_by(PointEntry.lot_id)
        .subquery()
    )
    candidates = db.execute(
        select(PointLot.member_id, PointLot.id)
        .join(remaining, remaining.c.lot_id == PointLot.id)
        .where(PointLot.expires_at < now, remaining.c.remaining > 0)
        .order_by(PointLot.member_id, PointLot.expires_at, PointLot.id)
        .limit(batch_size)
    ).all()
    lot_ids_by_member: dict[int, list[int]] = {}
    for member_id, lot_id in candidates:
        lot_ids_by_member.setdefault(member_id, []).append(lot_id)

    processed = 0
    for member_id in sorted(lot_ids_by_member):
        _lock_member(db, member_id)
        lots = db.scalars(
            select(PointLot)
            .where(PointLot.id.in_(lot_ids_by_member[member_id]))
            .order_by(PointLot.expires_at, PointLot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        rows = db.execute(
            select(PointEntry.lot_id, PointEntry.points)
            .where(PointEntry.lot_id.in_([lot.id for lot in lots]))
            .with_for_update()
        ).all()
        balances = _sum_by_lot(rows)
        for lot in lots:
            left = balances.get(lot.id, 0)
            if left > 0 and is_lot_expired(lot.expires_at, now):
                db.add(_entry(lot.member_id, lot.id, KIND_EXPIRE, -left, lot.order_id, now))
                processed += 1
    db.flush()
    return processed


def forfeit_points(db: Session, member_id: int, now: datetime) -> int:
    """注销时作废：对该会员剩余大于 0 的批次各写一条 forfeit 流水，已过期的写 expire。

    不要求会员仍为 active；待抵扣不作废。返回作废（forfeit）的积分数，即作废前的可用余额。
    只 flush、不提交。
    """
    member_id = _positive_id(member_id, "member_id")
    now = _naive_second(now)

    if _lock_member(db, member_id) is None:
        raise ValueError("member does not exist")
    lots = _lock_member_lots(db, member_id)
    balances = _lock_member_balances(db, member_id)
    forfeited = 0
    for lot in lots:
        left = balances.get(lot.id, 0)
        if left <= 0:
            continue
        if is_lot_expired(lot.expires_at, now):
            db.add(_entry(member_id, lot.id, KIND_EXPIRE, -left, lot.order_id, now))
        else:
            db.add(_entry(member_id, lot.id, KIND_FORFEIT, -left, lot.order_id, now))
            forfeited += left
    db.flush()
    return forfeited

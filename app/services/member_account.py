"""会员中心的积分只读查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, PointEntry, PointLot
from app.models.points import (
    KIND_DEBT_ADD,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_REDEEM,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_RELEASE,
)
from app.services.points import MALAYSIA_UTC_OFFSET, available_points, debt_points


@dataclass(frozen=True)
class NextExpiry:
    points: int
    date: date


@dataclass(frozen=True)
class PointHistory:
    type: str
    points: int
    order_number: str
    created_at: datetime


@dataclass(frozen=True)
class PointsOverview:
    balance: int
    pending: int
    next_expiry: NextExpiry | None
    history: list[PointHistory]


_DISPLAY_KINDS = {
    KIND_EARN: "earned",
    KIND_REDEEM: "redeemed",
    KIND_RELEASE: "redeemed",
    KIND_EXPIRE: "expired",
    KIND_REFUND_RETURN: "returned",
    KIND_REFUND_REVERSAL: "reversed",
    KIND_DEBT_ADD: "reversed",
}
_DISPLAY_ORDER = {"reversed": 0, "returned": 1, "expired": 2, "redeemed": 3, "earned": 4}


def points_overview(db: Session, member_id: int, now: datetime) -> PointsOverview:
    """返回会员可用积分、待抵扣、最近到期日及最多一百条显示明细。"""
    now = now.replace(microsecond=0)
    balance = available_points(db, member_id, now)
    pending = debt_points(db, member_id)

    lots = db.scalars(select(PointLot).where(PointLot.member_id == member_id)).all()
    lot_ids = [lot.id for lot in lots]
    lot_balances = dict.fromkeys(lot_ids, 0)
    if lot_ids:
        for lot_id, points in db.execute(
            select(PointEntry.lot_id, PointEntry.points).where(PointEntry.lot_id.in_(lot_ids))
        ):
            lot_balances[lot_id] += points
    candidates = [lot for lot in lots if lot_balances[lot.id] > 0 and lot.expires_at >= now]
    next_expiry = None
    if candidates:
        earliest = min(lot.expires_at for lot in candidates)
        expiry_day = (earliest + MALAYSIA_UTC_OFFSET).date()
        next_expiry = NextExpiry(
            points=sum(
                lot_balances[lot.id]
                for lot in candidates
                if (lot.expires_at + MALAYSIA_UTC_OFFSET).date() == expiry_day
            ),
            date=expiry_day,
        )

    grouped: dict[tuple[int, str], tuple[int, datetime, str]] = {}
    rows = db.execute(
        select(
            PointEntry.kind,
            PointEntry.points,
            PointEntry.created_at,
            Order.id,
            Order.order_number,
        )
        .join(Order, Order.id == PointEntry.order_id)
        .where(PointEntry.member_id == member_id, PointEntry.kind.in_(_DISPLAY_KINDS))
    )
    for kind, points, created_at, order_id, order_number in rows:
        display_type = _DISPLAY_KINDS[kind]
        key = (order_id, display_type)
        current = grouped.get(key)
        if current is None:
            grouped[key] = (points, created_at, order_number)
        else:
            grouped[key] = (current[0] + points, max(current[1], created_at), current[2])
    sortable = [
        (
            values[1],
            order_id,
            -_DISPLAY_ORDER[display_type],
            PointHistory(display_type, values[0], values[2], values[1]),
        )
        for (order_id, display_type), values in grouped.items()
        if values[0] != 0
    ]
    sortable.sort(key=lambda row: row[:3], reverse=True)
    history = [row[3] for row in sortable]
    return PointsOverview(balance, pending, next_expiry, history[:100])

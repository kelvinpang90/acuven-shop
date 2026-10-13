"""会员中心积分概览与流水的只读查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, PointEntry, PointLot
from app.models.points import (
    KIND_DEBT_ADD,
    KIND_DEBT_REPAY,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_RELEASE,
)
from app.services.points import (
    MALAYSIA_UTC_OFFSET,
    available_points,
    debt_points,
    is_lot_expired,
)

_DISPLAY_ORDER = {"reversed": 0, "returned": 1, "expired": 2, "redeemed": 3, "earned": 4}
_DISPLAY_KINDS = {
    KIND_EARN: "earned",
    KIND_REDEEM: "redeemed",
    KIND_RELEASE: "redeemed",
    KIND_EXPIRE: "expired",
    KIND_REFUND_RETURN: "returned",
    KIND_REFUND_REVERSAL: "reversed",
    KIND_DEBT_ADD: "reversed",
}


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
class PointAccount:
    balance: int
    pending: int
    next_expiry: NextExpiry | None
    history: list[PointHistory]


def member_points(db: Session, member_id: int, now: datetime) -> PointAccount:
    """返回会员余额、待抵扣、最近到期日与最多 100 条显示流水；只读。"""
    balance = available_points(db, member_id, now)
    pending = debt_points(db, member_id)
    lots = db.execute(
        select(PointLot.expires_at, func.coalesce(func.sum(PointEntry.points), 0))
        .outerjoin(PointEntry, PointEntry.lot_id == PointLot.id)
        .where(PointLot.member_id == member_id)
        .group_by(PointLot.id, PointLot.expires_at)
    ).all()
    eligible = [
        (expires_at, int(left))
        for expires_at, left in lots
        if left > 0 and not is_lot_expired(expires_at, now)
    ]
    next_expiry = None
    if eligible:
        expires_at = min(expires_at for expires_at, _ in eligible)
        expiry_date = (expires_at + MALAYSIA_UTC_OFFSET).date()
        next_expiry = NextExpiry(
            points=sum(
                left for at, left in eligible if (at + MALAYSIA_UTC_OFFSET).date() == expiry_date
            ),
            date=expiry_date,
        )

    groups: dict[tuple[int, str], tuple[int, datetime, str]] = {}
    rows = db.execute(
        select(
            PointEntry.kind,
            PointEntry.points,
            PointEntry.created_at,
            PointEntry.order_id,
            Order.order_number,
        )
        .join(Order, Order.id == PointEntry.order_id)
        .where(PointEntry.member_id == member_id)
    ).all()
    for kind, points, created_at, order_id, order_number in rows:
        display_type = _DISPLAY_KINDS.get(kind)
        if display_type is None or kind in (KIND_DEBT_REPAY, KIND_FORFEIT):
            continue
        key = (order_id, display_type)
        current_points, current_created_at, current_order_number = groups.get(
            key, (0, created_at, order_number)
        )
        groups[key] = (
            current_points + (-points if kind == KIND_DEBT_ADD else points),
            max(current_created_at, created_at),
            current_order_number,
        )
    grouped_history = [
        (order_id, display_type, points, created_at, order_number)
        for (order_id, display_type), (points, created_at, order_number) in groups.items()
        if points != 0
    ]
    grouped_history.sort(key=lambda row: (-row[3].timestamp(), -row[0], _DISPLAY_ORDER[row[1]]))
    history = [
        PointHistory(
            type=display_type,
            points=points,
            order_number=order_number,
            created_at=created_at,
        )
        for _, display_type, points, created_at, order_number in grouped_history
    ]
    return PointAccount(
        balance=balance, pending=pending, next_expiry=next_expiry, history=history[:100]
    )

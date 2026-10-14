"""Read-only point summaries and activity for a member account.

The presentation types in this module deliberately derive their values from
the append-only points ledger.  They do not make an account active, lock a
member, flush, or otherwise change the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
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
from app.services.points import (
    _naive_second,
    _positive_id,
    available_points,
    debt_points,
)

MALAYSIA_UTC_OFFSET = timedelta(hours=8)

DISPLAY_EARNED = "earned"
DISPLAY_REDEEMED = "redeemed"
DISPLAY_EXPIRED = "expired"
DISPLAY_RETURNED = "returned"
DISPLAY_REVERSED = "reversed"

_DISPLAY_KINDS = {
    KIND_EARN: DISPLAY_EARNED,
    KIND_REDEEM: DISPLAY_REDEEMED,
    KIND_RELEASE: DISPLAY_REDEEMED,
    KIND_EXPIRE: DISPLAY_EXPIRED,
    KIND_REFUND_RETURN: DISPLAY_RETURNED,
    KIND_REFUND_REVERSAL: DISPLAY_REVERSED,
    KIND_DEBT_ADD: DISPLAY_REVERSED,
}
_DISPLAY_ORDER = {
    DISPLAY_REVERSED: 0,
    DISPLAY_RETURNED: 1,
    DISPLAY_EXPIRED: 2,
    DISPLAY_REDEEMED: 3,
    DISPLAY_EARNED: 4,
}


@dataclass(frozen=True)
class PointsOverview:
    """Balances and the earliest still-useful points expiry for one member."""

    available_points: int
    debt_points: int
    nearest_expiry_date: date | None
    nearest_expiry_points: int | None


@dataclass(frozen=True)
class PointActivity:
    """One displayable, per-order group of point ledger entries."""

    display_type: str
    points: int
    order_number: str
    created_at: datetime


def points_overview(db: Session, member_id: int, now: datetime) -> PointsOverview:
    """Return a member's balances and nearest expiry without writing the session."""
    member_id = _positive_id(member_id, "member_id")
    now = _naive_second(now)

    remaining = func.sum(PointEntry.points).label("remaining")
    # 只读：不让查询把会话里待写的对象自动 flush 出去。
    with db.no_autoflush:
        available = available_points(db, member_id, now)
        debt = debt_points(db, member_id)
        lots = db.execute(
            select(PointLot.expires_at, remaining)
            .join(PointEntry, PointEntry.lot_id == PointLot.id)
            .where(PointLot.member_id == member_id, PointLot.expires_at >= now)
            .group_by(PointLot.id, PointLot.expires_at)
            .having(remaining > 0)
            .order_by(PointLot.expires_at)
        ).all()
    if not lots:
        return PointsOverview(available, debt, None, None)

    expiry = lots[0].expires_at
    expiry_day = (expiry + MALAYSIA_UTC_OFFSET).date()
    expiry_points = sum(
        int(row.remaining)
        for row in lots
        if (row.expires_at + MALAYSIA_UTC_OFFSET).date() == expiry_day
    )
    return PointsOverview(available, debt, expiry_day, expiry_points)


def point_activity(db: Session, member_id: int) -> list[PointActivity]:
    """Return at most 100 displayable point groups for a single member."""
    member_id = _positive_id(member_id, "member_id")
    with db.no_autoflush:
        rows = db.execute(
            select(
                PointEntry.order_id,
                Order.order_number,
                PointEntry.kind,
                PointEntry.points,
                PointEntry.created_at,
            )
            .join(Order, Order.id == PointEntry.order_id)
            .where(PointEntry.member_id == member_id)
        ).all()

    grouped: dict[tuple[int, str], list[object]] = {}
    for row in rows:
        display_type = _DISPLAY_KINDS.get(row.kind)
        if display_type is None:
            continue
        points = -row.points if row.kind == KIND_DEBT_ADD else row.points
        key = (row.order_id, display_type)
        group = grouped.setdefault(key, [0, row.order_number, row.created_at])
        group[0] = int(group[0]) + points
        group[2] = max(group[2], row.created_at)

    ordered = sorted(
        (
            (order_id, display_type, group)
            for (order_id, display_type), group in grouped.items()
            if group[0] != 0
        ),
        key=lambda item: (
            item[2][2],
            item[0],
            -_DISPLAY_ORDER[item[1]],
        ),
        reverse=True,
    )
    return [
        PointActivity(
            display_type=display_type,
            points=int(group[0]),
            order_number=str(group[1]),
            created_at=group[2],
        )
        for _, display_type, group in ordered[:100]
    ]

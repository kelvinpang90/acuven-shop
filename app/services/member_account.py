"""Member-facing, read-only points account queries.

The values here are deliberately derived from the append-only points ledger.
They are the query counterpart to ``app.services.points`` and do not flush or
otherwise change the supplied session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

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
    MALAYSIA_UTC_OFFSET,
    _naive_second,
    _positive_id,
    available_points,
    debt_points,
    is_lot_expired,
)


@dataclass(frozen=True)
class NextPointsExpiry:
    """The remaining points in the member's next Malaysia-calendar expiry day."""

    date: date
    points: int


@dataclass(frozen=True)
class PointsOverview:
    balance: int
    pending: int
    next_expiry: NextPointsExpiry | None


@dataclass(frozen=True)
class PointsHistoryItem:
    type: str
    points: int
    order_number: str
    created_at: datetime


def get_points_overview(db: Session, member_id: int, now: datetime) -> PointsOverview:
    """Return this member's balance, refund debt, and next usable lot expiry.

    Lots sharing the earliest Malaysia date are combined even where their UTC
    expiry instants differ.  An expiry instant itself is still usable, matching
    ``available_points`` and the points-rule boundary.
    """
    member_id = _positive_id(member_id, "member_id")
    now = _naive_second(now)
    remaining = (
        select(
            PointEntry.lot_id.label("lot_id"),
            func.sum(PointEntry.points).label("points"),
        )
        .where(PointEntry.member_id == member_id, PointEntry.lot_id.is_not(None))
        .group_by(PointEntry.lot_id)
        .subquery()
    )
    lots = db.execute(
        select(PointLot.expires_at, remaining.c.points)
        .join(remaining, remaining.c.lot_id == PointLot.id)
        .where(PointLot.member_id == member_id, remaining.c.points > 0)
        .order_by(PointLot.expires_at, PointLot.id)
    ).all()

    next_day: date | None = None
    next_points = 0
    for expires_at, points in lots:
        if is_lot_expired(expires_at, now):
            continue
        malaysia_day = (expires_at + MALAYSIA_UTC_OFFSET).date()
        if next_day is None:
            next_day = malaysia_day
        if malaysia_day != next_day:
            break
        next_points += int(points)

    return PointsOverview(
        balance=available_points(db, member_id, now),
        pending=debt_points(db, member_id),
        next_expiry=(
            None if next_day is None else NextPointsExpiry(date=next_day, points=next_points)
        ),
    )


_HISTORY_KINDS = {
    KIND_EARN: "earned",
    KIND_EXPIRE: "expired",
    KIND_REFUND_RETURN: "returned",
}
_TYPE_ORDER = {"reversed": 0, "returned": 1, "expired": 2, "redeemed": 3, "earned": 4}


def get_points_history(db: Session, member_id: int) -> list[PointsHistoryItem]:
    """Return at most 100 displayed ledger groups for one member.

    The grouping is intentionally done after the member filter: a member can
    never obtain another member's order or ledger information through this
    query.  ``debt_repay`` and ``forfeit`` have no member-centre display type.
    """
    member_id = _positive_id(member_id, "member_id")
    entries = db.execute(
        select(
            PointEntry.kind,
            PointEntry.points,
            PointEntry.order_id,
            PointEntry.created_at,
            Order.order_number,
        )
        .join(Order, Order.id == PointEntry.order_id)
        .where(PointEntry.member_id == member_id)
    ).all()

    grouped: dict[tuple[int, str], tuple[int, datetime, str]] = {}
    for kind, points, order_id, created_at, order_number in entries:
        if kind in _HISTORY_KINDS:
            display_type = _HISTORY_KINDS[kind]
            display_points = points
        elif kind in (KIND_REDEEM, KIND_RELEASE):
            display_type = "redeemed"
            display_points = points
        elif kind in (KIND_REFUND_REVERSAL, KIND_DEBT_ADD):
            display_type = "reversed"
            # debt_add is positive in the debt account but a negative balance
            # movement in the member-visible refund-recovery line.
            display_points = points if kind == KIND_REFUND_REVERSAL else -points
        else:
            continue
        key = (order_id, display_type)
        if key not in grouped:
            grouped[key] = (0, created_at, order_number)
        total, latest, number = grouped[key]
        grouped[key] = (total + display_points, max(latest, created_at), number)

    rows = [
        (order_id, display_type, points, created_at, order_number)
        for (order_id, display_type), (points, created_at, order_number) in grouped.items()
        if points != 0
    ]
    rows.sort(
        key=lambda row: (row[3], row[0], -_TYPE_ORDER[row[1]]),
        reverse=True,
    )
    return [
        PointsHistoryItem(
            type=display_type,
            points=points,
            order_number=order_number,
            created_at=created_at,
        )
        for _, display_type, points, created_at, order_number in rows[:100]
    ]

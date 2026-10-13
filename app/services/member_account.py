"""会员中心的积分概览与积分明细只读查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import case, func, select
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
class PointsOverview:
    """P13 所需的积分余额、待抵扣与最近到期信息。"""

    balance: int
    debt: int
    next_expiry_date: date | None
    next_expiry_points: int | None


@dataclass(frozen=True)
class PointHistoryItem:
    """P13 积分明细的一行已合并显示数据。"""

    display_type: str
    points: int
    order_number: str
    created_at: datetime


_DISPLAY_TYPES = {
    KIND_EARN: "earned",
    KIND_EXPIRE: "expired",
    KIND_REFUND_RETURN: "returned",
}
_DISPLAY_ORDER = {"reversed": 0, "returned": 1, "expired": 2, "redeemed": 3, "earned": 4}


def points_overview(db: Session, member_id: int, now: datetime) -> PointsOverview:
    """读取会员积分概览，不写库也不 flush。"""
    balance = available_points(db, member_id, now)
    now = now.replace(microsecond=0)
    debt = debt_points(db, member_id)

    remaining = func.sum(PointEntry.points)
    rows = db.execute(
        select(PointLot.expires_at, remaining)
        .join(PointEntry, PointEntry.lot_id == PointLot.id)
        .where(PointLot.member_id == member_id, PointLot.expires_at >= now)
        .group_by(PointLot.id, PointLot.expires_at)
        .having(remaining > 0)
        .order_by(PointLot.expires_at, PointLot.id)
    ).all()
    if not rows:
        return PointsOverview(balance, debt, None, None)

    first_day = (rows[0].expires_at + MALAYSIA_UTC_OFFSET).date()
    points = sum(int(row[1]) for row in rows if (row[0] + MALAYSIA_UTC_OFFSET).date() == first_day)
    return PointsOverview(balance, debt, first_day, points)


def points_history(db: Session, member_id: int) -> list[PointHistoryItem]:
    """读取会员最近 100 行积分明细，不写库也不 flush。"""
    display_type = case(
        (PointEntry.kind.in_((KIND_REDEEM, KIND_RELEASE)), "redeemed"),
        (PointEntry.kind.in_((KIND_REFUND_REVERSAL, KIND_DEBT_ADD)), "reversed"),
        *[(PointEntry.kind == kind, value) for kind, value in _DISPLAY_TYPES.items()],
        else_=None,
    ).label("display_type")
    display_points = func.sum(
        case(
            (PointEntry.kind == KIND_DEBT_ADD, -PointEntry.points),
            else_=PointEntry.points,
        )
    ).label("display_points")
    latest = func.max(PointEntry.created_at).label("latest")
    stmt = (
        select(display_type, display_points, Order.order_number, latest, Order.id)
        .join(Order, Order.id == PointEntry.order_id)
        .where(
            PointEntry.member_id == member_id,
            PointEntry.kind.in_(
                (
                    KIND_EARN,
                    KIND_REDEEM,
                    KIND_RELEASE,
                    KIND_EXPIRE,
                    KIND_REFUND_RETURN,
                    KIND_REFUND_REVERSAL,
                    KIND_DEBT_ADD,
                )
            ),
        )
        .group_by(display_type, Order.id, Order.order_number)
        .having(display_points != 0)
    )
    rows = db.execute(stmt).all()
    rows.sort(
        key=lambda row: (
            row.latest,
            row.id,
            -_DISPLAY_ORDER[row.display_type],
        ),
        reverse=True,
    )
    return [
        PointHistoryItem(row.display_type, int(row.display_points), row.order_number, row.latest)
        for row in rows[:100]
    ]

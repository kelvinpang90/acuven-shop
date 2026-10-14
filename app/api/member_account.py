"""Member account points and coupon read APIs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.member_auth import MemberDep
from app.api.order_lookup import _NoStoreRoute
from app.db.session import get_session
from app.services.coupons import list_available_coupons, list_member_used_coupons
from app.services.member_account import point_activity, points_overview

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)

SessionDep = Annotated[Session, Depends(get_session)]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc(value: datetime) -> datetime:
    """Stored datetimes are naive UTC; API datetimes must say so explicitly."""
    return value.replace(tzinfo=UTC)


class NextExpiryOut(BaseModel):
    points: int
    date: date


class PointHistoryOut(BaseModel):
    type: Literal["earned", "redeemed", "expired", "returned", "reversed"]
    points: int
    order_number: str
    created_at: datetime


class PointsOut(BaseModel):
    balance: int
    pending: int
    next_expiry: NextExpiryOut | None
    history: list[PointHistoryOut]


class AvailableCouponOut(BaseModel):
    code: str
    type: Literal["fixed", "percent"]
    amount_sen: int | None
    percent: int | None
    min_spend_sen: int
    valid_until: datetime


class UsedCouponOut(BaseModel):
    code: str
    order_number: str
    discount_sen: int


class CouponsOut(BaseModel):
    available: list[AvailableCouponOut]
    used: list[UsedCouponOut]


@router.get("/points", response_model=PointsOut)
def get_points(member: MemberDep, session: SessionDep) -> PointsOut:
    """Return only the authenticated member's points overview and activity."""
    overview = points_overview(session, member.member.id, _utcnow())
    history = point_activity(session, member.member.id)
    next_expiry = None
    if overview.nearest_expiry_date is not None:
        next_expiry = NextExpiryOut(
            points=overview.nearest_expiry_points or 0,
            date=overview.nearest_expiry_date,
        )
    return PointsOut(
        balance=overview.available_points,
        pending=overview.debt_points,
        next_expiry=next_expiry,
        history=[
            PointHistoryOut(
                type=item.display_type,
                points=item.points,
                order_number=item.order_number,
                created_at=_utc(item.created_at),
            )
            for item in history
        ],
    )


@router.get("/coupons", response_model=CouponsOut)
def get_coupons(member: MemberDep, session: SessionDep) -> CouponsOut:
    """Return shared available coupons and this member's redeemed coupon records."""
    now = _utcnow()
    return CouponsOut(
        available=[
            AvailableCouponOut(
                code=item.code,
                type=item.coupon_type,
                amount_sen=item.amount_off_sen,
                percent=item.percent_off,
                min_spend_sen=item.min_subtotal_sen,
                valid_until=_utc(item.ends_at),
            )
            for item in list_available_coupons(session, now)
        ],
        used=[
            UsedCouponOut(
                code=item.code,
                order_number=item.order_number,
                discount_sen=item.coupon_discount_sen,
            )
            for item in list_member_used_coupons(session, member.member.id)
        ],
    )

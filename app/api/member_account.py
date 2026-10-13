"""会员中心积分与优惠券的只读接口。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.member_auth import MemberDep
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _utcnow
from app.db.session import get_session
from app.services.coupons import list_available_coupons, list_member_used_coupons
from app.services.member_account import points_overview

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)
SessionDep = Annotated[Session, Depends(get_session)]


class NextExpiryOut(BaseModel):
    points: int
    date: str


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
    type: str
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


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


@router.get("/points", response_model=PointsOut)
def get_points(member: MemberDep, session: SessionDep) -> PointsOut:
    overview = points_overview(session, member.member.id, _utcnow())
    expiry = overview.next_expiry
    return PointsOut(
        balance=overview.balance,
        pending=overview.pending,
        next_expiry=(
            None
            if expiry is None
            else NextExpiryOut(points=expiry.points, date=expiry.date.isoformat())
        ),
        history=[
            PointHistoryOut(
                type=row.type,
                points=row.points,
                order_number=row.order_number,
                created_at=_utc(row.created_at),
            )
            for row in overview.history
        ],
    )


@router.get("/coupons", response_model=CouponsOut, response_model_exclude_none=True)
def get_coupons(member: MemberDep, session: SessionDep) -> CouponsOut:
    return CouponsOut(
        available=[
            AvailableCouponOut(
                code=row.code,
                type=row.coupon_type,
                amount_sen=row.amount_off_sen,
                percent=row.percent_off,
                min_spend_sen=row.min_subtotal_sen,
                valid_until=_utc(row.ends_at),
            )
            for row in list_available_coupons(session, _utcnow())
        ],
        used=[
            UsedCouponOut(
                code=row.code,
                order_number=row.order_number,
                discount_sen=row.coupon_discount_sen,
            )
            for row in list_member_used_coupons(session, member.member.id)
        ],
    )

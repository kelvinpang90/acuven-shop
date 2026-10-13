"""会员中心积分与优惠券的只读接口。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.member_auth import MemberDep
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _utcnow
from app.db.session import get_session
from app.services.coupons import list_available_coupons, list_member_used_coupons
from app.services.member_account import member_points

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)
SessionDep = Annotated[Session, Depends(get_session)]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


class NextExpiryOut(BaseModel):
    points: int
    date: date


class PointHistoryOut(BaseModel):
    type: str
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


@router.get("/points", response_model=PointsOut)
def points(member: MemberDep, db: SessionDep) -> PointsOut:
    account = member_points(db, member.member.id, _utcnow())
    expiry = None if account.next_expiry is None else NextExpiryOut(**account.next_expiry.__dict__)
    return PointsOut(
        balance=account.balance,
        pending=account.pending,
        next_expiry=expiry,
        history=[
            PointHistoryOut(
                type=row.type,
                points=row.points,
                order_number=row.order_number,
                created_at=_utc(row.created_at),
            )
            for row in account.history
        ],
    )


@router.get("/coupons", response_model=CouponsOut)
def coupons(member: MemberDep, db: SessionDep) -> CouponsOut:
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
            for row in list_available_coupons(db, _utcnow())
        ],
        used=[
            UsedCouponOut(
                code=row.code,
                order_number=row.order_number,
                discount_sen=row.coupon_discount_sen,
            )
            for row in list_member_used_coupons(db, member.member.id)
        ],
    )

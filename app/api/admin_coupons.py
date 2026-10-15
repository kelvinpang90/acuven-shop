"""后台优惠券 A05 的列表、新建与停用接口。"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_coupons import (
    PAGE_SIZE,
    AdminCouponCreate,
    AdminCouponRow,
    CouponCodeTaken,
    CouponInvalid,
    CouponNotFound,
    create_admin_coupon,
    disable_admin_coupon,
    list_admin_coupons,
)
from app.services.admin_stock_resets import MAX_PAGE

router = APIRouter(prefix="/api/admin/coupons", tags=["admin"], route_class=_NoStoreRoute)

_PAGE = re.compile(r"[1-9][0-9]{0,4}")
_COUPON_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_COUPON_ID = 2**31 - 1


class CouponIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    code: str = Field(min_length=1, max_length=64)
    coupon_type: Literal["fixed", "percent"] = Field(alias="type")
    amount_off_sen: int | None
    percent_off: int | None
    min_subtotal_sen: int
    starts_on: date
    ends_on: date
    total_use_limit: int | None
    per_member_use_limit: int | None


class CouponOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    code: str
    coupon_type: str = Field(alias="type")
    amount_off_sen: int | None
    percent_off: int | None
    min_subtotal_sen: int
    starts_on: date
    ends_on: date
    total_use_limit: int | None
    per_member_use_limit: int | None
    used_count: int
    active: bool


class CouponPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    coupons: list[CouponOut]
    csrf_token: str


def _coupon_out(row: AdminCouponRow) -> CouponOut:
    return CouponOut(
        id=row.id,
        code=row.code,
        type=row.coupon_type,
        amount_off_sen=row.amount_off_sen,
        percent_off=row.percent_off,
        min_subtotal_sen=row.min_subtotal_sen,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        total_use_limit=row.total_use_limit,
        per_member_use_limit=row.per_member_use_limit,
        used_count=row.used_count,
        active=row.is_active,
    )


def _page(page: str = "1") -> int:
    if not _PAGE.fullmatch(page) or int(page) > MAX_PAGE:
        raise RequestValidationError(
            [
                _error(
                    ("query", "page"),
                    "page_invalid",
                    f"Page should be an integer from 1 to {MAX_PAGE}",
                )
            ]
        )
    return int(page)


def _coupon_id(coupon_id: str) -> int:
    if not _COUPON_ID.fullmatch(coupon_id) or int(coupon_id) > MAX_COUPON_ID:
        raise RequestValidationError(
            [
                _error(
                    ("path", "coupon_id"),
                    "coupon_id_invalid",
                    "Coupon ID should be a positive integer",
                )
            ]
        )
    return int(coupon_id)


async def _create_body(request: Request) -> bytes:
    """只在这里判定 413 与 415；结构验证在 CSRF 之后。"""
    return await _json_body(request)


PageDep = Annotated[int, Depends(_page)]
CouponIdDep = Annotated[int, Depends(_coupon_id)]
CreateBodyDep = Annotated[bytes, Depends(_create_body)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=CouponPageOut)
def list_coupons(admin: AdminDep, page: PageDep, session: SessionDep) -> CouponPageOut:
    token = csrf_token_for_cookie(admin.cookie_value)
    if token is None:
        raise HTTPException(status_code=401, detail="admin_session_required")
    found = list_admin_coupons(session, page)
    return CouponPageOut(
        total=found.total,
        page=page,
        page_size=PAGE_SIZE,
        coupons=[_coupon_out(item) for item in found.items],
        csrf_token=token,
    )


@router.post("", status_code=201, response_model=CouponOut)
def create_coupon(
    raw: CreateBodyDep, admin: AdminDep, session: SessionDep, request: Request
) -> CouponOut:
    require_admin_csrf(request, admin)
    try:
        body = CouponIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    try:
        created = create_admin_coupon(
            session,
            admin.account.id,
            AdminCouponCreate(
                code=body.code,
                coupon_type=body.coupon_type,
                amount_off_sen=body.amount_off_sen,
                percent_off=body.percent_off,
                min_subtotal_sen=body.min_subtotal_sen,
                starts_on=body.starts_on,
                ends_on=body.ends_on,
                total_use_limit=body.total_use_limit,
                per_member_use_limit=body.per_member_use_limit,
            ),
            _utcnow(),
        )
        session.commit()
    except CouponInvalid:
        session.rollback()
        raise HTTPException(status_code=422, detail="coupon_invalid") from None
    except CouponCodeTaken:
        session.rollback()
        raise HTTPException(status_code=409, detail="coupon_code_taken") from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    return _coupon_out(created)


@router.post("/{coupon_id}/disable", response_model=CouponOut)
def disable_coupon(
    admin: AdminDep, coupon_id: CouponIdDep, session: SessionDep, request: Request
) -> CouponOut:
    require_admin_csrf(request, admin)
    try:
        disabled = disable_admin_coupon(session, admin.account.id, coupon_id, _utcnow())
        session.commit()
    except CouponNotFound:
        session.rollback()
        raise HTTPException(status_code=404, detail="not_found") from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=503, detail="service_unavailable") from None
    return _coupon_out(disabled)

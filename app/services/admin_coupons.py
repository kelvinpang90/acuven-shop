"""后台优惠券的列表、新建与停用规则。

后台只可新建或停用券；已经预占或核销的用券记录不因停用而改变。时间在库中
一律为不带时区的 UTC，有效期输入和列表输出则使用马来西亚日期（固定 UTC+8）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Coupon, CouponUse
from app.models.coupon import COUNTED_USE_STATUSES, TYPE_FIXED, TYPE_PERCENT
from app.services.admin_auth import record_audit
from app.services.coupons import normalize_coupon_code

MALAYSIA_UTC_OFFSET = timedelta(hours=8)
PAGE_SIZE = 30
MAX_AMOUNT_SEN = 10_000_000
MAX_USE_LIMIT = 1_000_000
ADMIN_COUPON_CREATED = "admin_coupon_created"
ADMIN_COUPON_DISABLED = "admin_coupon_disabled"
AUDIT_TARGET_COUPON = "coupon"


class CouponInvalid(Exception):
    """后台提交的优惠券字段不符合规则；不携带提交值。"""


class CouponCodeTaken(Exception):
    """规范化后的优惠券代码已经存在。"""


class CouponNotFound(Exception):
    """要停用的优惠券不存在。"""


@dataclass(frozen=True)
class AdminCouponCreateRequest:
    code: object
    coupon_type: object
    amount_off_sen: object
    percent_off: object
    min_subtotal_sen: object
    starts_on: object
    ends_on: object
    total_use_limit: object = None
    per_member_use_limit: object = None


@dataclass(frozen=True)
class AdminCouponRow:
    id: int
    code: str
    coupon_type: str
    amount_off_sen: int | None
    percent_off: int | None
    min_subtotal_sen: int
    starts_on: date
    ends_on: date
    total_use_limit: int | None
    per_member_use_limit: int | None
    used_count: int
    is_active: bool


@dataclass(frozen=True)
class AdminCouponPage:
    total: int
    page: int
    page_size: int
    coupons: list[AdminCouponRow]


# 后续接口层使用较短的领域名；保留 Admin 前缀使本规则层的返回值来源清楚。
CouponCreateRequest = AdminCouponCreateRequest
CouponRow = AdminCouponRow
CouponPage = AdminCouponPage


def _malaysia_date(value: datetime) -> date:
    return (value + MALAYSIA_UTC_OFFSET).date()


def _row(coupon: Coupon, used_count: int) -> AdminCouponRow:
    return AdminCouponRow(
        id=coupon.id,
        code=coupon.code,
        coupon_type=coupon.coupon_type,
        amount_off_sen=coupon.amount_off_sen,
        percent_off=coupon.percent_off,
        min_subtotal_sen=coupon.min_subtotal_sen,
        starts_on=_malaysia_date(coupon.starts_at),
        ends_on=_malaysia_date(coupon.ends_at),
        total_use_limit=coupon.total_use_limit,
        per_member_use_limit=coupon.per_member_use_limit,
        used_count=used_count,
        is_active=coupon.is_active,
    )


def _used_count(db: Session, coupon_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(CouponUse)
        .where(
            CouponUse.coupon_id == coupon_id,
            CouponUse.status.in_(COUNTED_USE_STATUSES),
        )
    )
    return int(db.scalar(stmt) or 0)


def list_coupons(db: Session, page: int) -> AdminCouponPage:
    """返回后台优惠券页；只读且不写审计。"""
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive integer")
    counts = (
        select(CouponUse.coupon_id.label("coupon_id"), func.count().label("used_count"))
        .where(CouponUse.status.in_(COUNTED_USE_STATUSES))
        .group_by(CouponUse.coupon_id)
        .subquery()
    )
    with db.no_autoflush:
        total = int(db.scalar(select(func.count()).select_from(Coupon)) or 0)
        rows = list(
            db.execute(
                select(Coupon, func.coalesce(counts.c.used_count, 0))
                .outerjoin(counts, counts.c.coupon_id == Coupon.id)
                .order_by(Coupon.created_at.desc(), Coupon.id.desc())
                .limit(PAGE_SIZE)
                .offset((page - 1) * PAGE_SIZE)
            ).tuples()
        )
    return AdminCouponPage(
        total=total,
        page=page,
        page_size=PAGE_SIZE,
        coupons=[_row(coupon, int(used_count)) for coupon, used_count in rows],
    )


def _valid_int(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _valid_date(value: object) -> bool:
    return type(value) is date


def _require_naive_utc(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")


def _validated_request(
    request: AdminCouponCreateRequest, now: datetime
) -> tuple[str, datetime, datetime]:
    normalized = normalize_coupon_code(request.code)
    if normalized is None or request.coupon_type not in (TYPE_FIXED, TYPE_PERCENT):
        raise CouponInvalid
    if request.coupon_type == TYPE_FIXED:
        amount_ok = _valid_int(request.amount_off_sen, 1, MAX_AMOUNT_SEN)
        type_ok = request.percent_off is None
    else:
        amount_ok = _valid_int(request.percent_off, 1, 100)
        type_ok = request.amount_off_sen is None
    if not amount_ok or not type_ok:
        raise CouponInvalid
    if not _valid_int(request.min_subtotal_sen, 0, MAX_AMOUNT_SEN):
        raise CouponInvalid
    if not _valid_date(request.starts_on) or not _valid_date(request.ends_on):
        raise CouponInvalid
    if request.starts_on > request.ends_on or request.ends_on < _malaysia_date(now):
        raise CouponInvalid
    for limit in (request.total_use_limit, request.per_member_use_limit):
        if limit is not None and not _valid_int(limit, 1, MAX_USE_LIMIT):
            raise CouponInvalid
    starts_at = datetime.combine(request.starts_on, time.min) - MALAYSIA_UTC_OFFSET
    ends_at = datetime.combine(request.ends_on, time(23, 59, 59)) - MALAYSIA_UTC_OFFSET
    return normalized, starts_at, ends_at


def _is_code_unique_violation(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return (
        "coupons.code" in message
        or "unique constraint failed: coupons.code" in message
        or "uq_coupons_code" in message
    )


def create_coupon(
    db: Session,
    admin_account_id: int,
    request: AdminCouponCreateRequest,
    now: datetime,
) -> AdminCouponRow:
    """新建一张启用的优惠券，只 flush、不提交。"""
    _require_naive_utc(now)
    normalized, starts_at, ends_at = _validated_request(request, now)
    coupon = Coupon(
        code=normalized,
        coupon_type=request.coupon_type,
        amount_off_sen=request.amount_off_sen,
        percent_off=request.percent_off,
        min_subtotal_sen=request.min_subtotal_sen,
        starts_at=starts_at,
        ends_at=ends_at,
        total_use_limit=request.total_use_limit,
        per_member_use_limit=request.per_member_use_limit,
        is_active=True,
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(coupon)
            db.flush()
    except IntegrityError as exc:
        if _is_code_unique_violation(exc):
            raise CouponCodeTaken from None
        raise
    record_audit(
        db,
        ADMIN_COUPON_CREATED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_COUPON,
        target_id=coupon.id,
        new_value=normalized,
    )
    return _row(coupon, 0)


def disable_coupon(
    db: Session, admin_account_id: int, coupon_id: int, now: datetime
) -> AdminCouponRow:
    """停用仍启用的券；重复停用不写审计，只 flush、不提交。"""
    _require_naive_utc(now)
    result = db.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, Coupon.is_active.is_(True))
        .values(is_active=False)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        coupon = db.scalars(
            select(Coupon).where(Coupon.id == coupon_id).execution_options(populate_existing=True)
        ).one()
        record_audit(
            db,
            ADMIN_COUPON_DISABLED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_COUPON,
            target_id=coupon_id,
            old_value="active",
            new_value="disabled",
        )
        return _row(coupon, _used_count(db, coupon_id))
    coupon = db.scalars(select(Coupon).where(Coupon.id == coupon_id)).one_or_none()
    if coupon is None:
        raise CouponNotFound
    return _row(coupon, _used_count(db, coupon_id))

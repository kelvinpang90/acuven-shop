"""后台优惠券 A05 的列表、新建与停用业务规则。

本模块不做接口或页面。优惠券有效期以马来西亚日期录入，数据库仍保存不带
时区的 UTC；创建与停用均只 flush、不提交。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Coupon, CouponUse
from app.models.coupon import COUNTED_USE_STATUSES, TYPE_FIXED, TYPE_PERCENT
from app.services.admin_auth import record_audit
from app.services.coupons import normalize_coupon_code
from app.services.sms_budget import MALAYSIA_UTC_OFFSET, malaysia_date

PAGE_SIZE = 30
_MAX_SEN = 10_000_000
_MAX_LIMIT = 1_000_000

ADMIN_COUPON_CREATED = "admin_coupon_created"
ADMIN_COUPON_DISABLED = "admin_coupon_disabled"


class CouponInvalid(Exception):
    """新券字段不符合已批准的后台优惠券规则；消息不含提交值。"""


class CouponCodeTaken(Exception):
    """规范化后的券代码已存在。"""


class CouponNotFound(Exception):
    """要停用的券不存在。"""


class CouponCreate(BaseModel):
    """后台新建券的原始输入；字段校验由 create_coupon 统一完成。"""

    code: object
    coupon_type: object
    amount_off_sen: object = None
    percent_off: object = None
    min_subtotal_sen: object = 0
    start_date: object
    end_date: object
    total_use_limit: object = None
    per_member_use_limit: object = None


class AdminCouponRow(BaseModel):
    """后台列表与写操作返回的一张券，日期均为马来西亚日期。"""

    id: int
    code: str
    coupon_type: str
    amount_off_sen: int | None
    percent_off: int | None
    min_subtotal_sen: int
    start_date: date
    end_date: date
    total_use_limit: int | None
    per_member_use_limit: int | None
    used_count: int
    is_active: bool


class AdminCouponPage(BaseModel):
    total: int
    page: int
    page_size: int
    coupons: list[AdminCouponRow]


def _as_int_in_range(value: object, low: int, high: int) -> int | None:
    if type(value) is not int or not low <= value <= high:
        return None
    return value


def _optional_limit(value: object) -> int | None:
    if value is None:
        return None
    return _as_int_in_range(value, 1, _MAX_LIMIT)


def _as_date(value: object) -> date | None:
    return value if isinstance(value, date) and not isinstance(value, datetime) else None


def _malaysia_start(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time()) - MALAYSIA_UTC_OFFSET


def _malaysia_end(value: date) -> datetime:
    return datetime.combine(value, datetime.max.time()).replace(microsecond=0) - MALAYSIA_UTC_OFFSET


def _row(coupon: Coupon, used_count: int = 0) -> AdminCouponRow:
    return AdminCouponRow(
        id=coupon.id,
        code=coupon.code,
        coupon_type=coupon.coupon_type,
        amount_off_sen=coupon.amount_off_sen,
        percent_off=coupon.percent_off,
        min_subtotal_sen=coupon.min_subtotal_sen,
        start_date=(coupon.starts_at + MALAYSIA_UTC_OFFSET).date(),
        end_date=(coupon.ends_at + MALAYSIA_UTC_OFFSET).date(),
        total_use_limit=coupon.total_use_limit,
        per_member_use_limit=coupon.per_member_use_limit,
        used_count=used_count,
        is_active=coupon.is_active,
    )


def list_coupons(db: Session, page: int) -> AdminCouponPage:
    """按创建时间、ID 倒序列出券；用券次数由一条分组查询取得。"""
    with db.no_autoflush:
        total = int(db.scalar(select(func.count()).select_from(Coupon)) or 0)
        counted = (
            select(CouponUse.coupon_id, func.count(CouponUse.id).label("used_count"))
            .where(CouponUse.status.in_(COUNTED_USE_STATUSES))
            .group_by(CouponUse.coupon_id)
            .subquery()
        )
        rows = list(
            db.execute(
                select(Coupon, func.coalesce(counted.c.used_count, 0))
                .outerjoin(counted, counted.c.coupon_id == Coupon.id)
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


def _duplicate_code(error: IntegrityError) -> bool:
    message = str(error.orig)
    return (
        "UNIQUE constraint failed: coupons.code" in message
        or "uq_coupons_code" in message
        or "Duplicate entry" in message
        and "code" in message
    )


def create_coupon(
    db: Session, admin_account_id: int, request: CouponCreate, now: datetime
) -> AdminCouponRow:
    """创建启用中的新券，写入创建审计；校验失败不写库。"""
    code = normalize_coupon_code(request.code)
    start_date = _as_date(request.start_date)
    end_date = _as_date(request.end_date)
    if now.tzinfo is not None or code is None or start_date is None or end_date is None:
        raise CouponInvalid
    if start_date > end_date or end_date < malaysia_date(now):
        raise CouponInvalid
    min_subtotal_sen = _as_int_in_range(request.min_subtotal_sen, 0, _MAX_SEN)
    total_use_limit = _optional_limit(request.total_use_limit)
    per_member_use_limit = _optional_limit(request.per_member_use_limit)
    if min_subtotal_sen is None or (
        request.total_use_limit is not None and total_use_limit is None
    ):
        raise CouponInvalid
    if request.per_member_use_limit is not None and per_member_use_limit is None:
        raise CouponInvalid
    if request.coupon_type == TYPE_FIXED:
        amount_off_sen = _as_int_in_range(request.amount_off_sen, 1, _MAX_SEN)
        percent_off = None
        if amount_off_sen is None or request.percent_off is not None:
            raise CouponInvalid
    elif request.coupon_type == TYPE_PERCENT:
        amount_off_sen = None
        percent_off = _as_int_in_range(request.percent_off, 1, 100)
        if percent_off is None or request.amount_off_sen is not None:
            raise CouponInvalid
    else:
        raise CouponInvalid

    coupon = Coupon(
        code=code,
        coupon_type=request.coupon_type,
        amount_off_sen=amount_off_sen,
        percent_off=percent_off,
        min_subtotal_sen=min_subtotal_sen,
        starts_at=_malaysia_start(start_date),
        ends_at=_malaysia_end(end_date),
        total_use_limit=total_use_limit,
        per_member_use_limit=per_member_use_limit,
        is_active=True,
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(coupon)
            db.flush()
    except IntegrityError as error:
        if _duplicate_code(error):
            raise CouponCodeTaken from None
        raise
    record_audit(
        db,
        ADMIN_COUPON_CREATED,
        admin_account_id,
        now,
        target_type="coupon",
        target_id=coupon.id,
        new_value=code,
    )
    return _row(coupon)


def disable_coupon(
    db: Session, admin_account_id: int, coupon_id: int, now: datetime
) -> AdminCouponRow:
    """条件停用券；重复停用保持幂等且不再写审计。"""
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    result = db.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, Coupon.is_active.is_(True))
        .values(is_active=False)
        .execution_options(synchronize_session="fetch")
    )
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise CouponNotFound
    if result.rowcount:
        record_audit(
            db,
            ADMIN_COUPON_DISABLED,
            admin_account_id,
            now,
            target_type="coupon",
            target_id=coupon.id,
            old_value="active",
            new_value="disabled",
        )
    else:
        db.flush()
    return _row(coupon)

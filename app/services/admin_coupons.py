"""后台优惠券的列表、新建与停用规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Coupon, CouponUse
from app.models.coupon import COUNTED_USE_STATUSES, TYPE_FIXED, TYPE_PERCENT
from app.services.admin_auth import record_audit
from app.services.coupons import normalize_coupon_code
from app.services.sms_budget import MALAYSIA_UTC_OFFSET

PAGE_SIZE = 30
MAX_SEN = 10_000_000
MAX_USE_LIMIT = 1_000_000
ADMIN_COUPON_CREATED = "admin_coupon_created"
ADMIN_COUPON_DISABLED = "admin_coupon_disabled"


class CouponInvalid(Exception):
    """后台提交的优惠券参数不符合规则。"""


class CouponCodeTaken(Exception):
    """规范化后的优惠券代码已经存在。"""


class CouponNotFound(Exception):
    """要停用的优惠券不存在。"""


@dataclass(frozen=True)
class AdminCouponCreate:
    code: object
    coupon_type: object
    amount_off_sen: object
    percent_off: object
    min_subtotal_sen: object
    starts_on: object
    ends_on: object
    total_use_limit: object
    per_member_use_limit: object


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
    items: list[AdminCouponRow]


def _require_naive_utc(now: datetime) -> None:
    if not isinstance(now, datetime) or now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")


def _integer_in(value: object, minimum: int, maximum: int) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        return None
    return value


def _limit(value: object) -> int | None | object:
    if value is None:
        return None
    valid = _integer_in(value, 1, MAX_USE_LIMIT)
    return valid if valid is not None else _INVALID


_INVALID = object()


def _row(coupon: Coupon, used_count: int) -> AdminCouponRow:
    return AdminCouponRow(
        id=coupon.id,
        code=coupon.code,
        coupon_type=coupon.coupon_type,
        amount_off_sen=coupon.amount_off_sen,
        percent_off=coupon.percent_off,
        min_subtotal_sen=coupon.min_subtotal_sen,
        starts_on=(coupon.starts_at + MALAYSIA_UTC_OFFSET).date(),
        ends_on=(coupon.ends_at + MALAYSIA_UTC_OFFSET).date(),
        total_use_limit=coupon.total_use_limit,
        per_member_use_limit=coupon.per_member_use_limit,
        used_count=used_count,
        is_active=coupon.is_active,
    )


def _used_count(db: Session, coupon_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(CouponUse.id)).where(
                CouponUse.coupon_id == coupon_id,
                CouponUse.status.in_(COUNTED_USE_STATUSES),
            )
        )
        or 0
    )


def list_admin_coupons(db: Session, page: int) -> AdminCouponPage:
    """按创建时间倒序列出优惠券；列表只读。"""
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive integer")
    counts = (
        select(CouponUse.coupon_id.label("coupon_id"), func.count(CouponUse.id).label("used_count"))
        .where(CouponUse.status.in_(COUNTED_USE_STATUSES))
        .group_by(CouponUse.coupon_id)
        .subquery()
    )
    rows = db.execute(
        select(Coupon, func.coalesce(counts.c.used_count, 0))
        .outerjoin(counts, counts.c.coupon_id == Coupon.id)
        .order_by(Coupon.created_at.desc(), Coupon.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return AdminCouponPage(
        total=int(db.scalar(select(func.count(Coupon.id))) or 0),
        items=[_row(coupon, int(used_count)) for coupon, used_count in rows],
    )


def create_admin_coupon(
    db: Session, admin_account_id: int, request: AdminCouponCreate, now: datetime
) -> AdminCouponRow:
    """校验并插入一张启用中的券；只 flush，不提交。"""
    _require_naive_utc(now)
    code = normalize_coupon_code(request.code)
    if code is None or request.coupon_type not in (TYPE_FIXED, TYPE_PERCENT):
        raise CouponInvalid()
    if not isinstance(request.starts_on, date) or isinstance(request.starts_on, datetime):
        raise CouponInvalid()
    if not isinstance(request.ends_on, date) or isinstance(request.ends_on, datetime):
        raise CouponInvalid()
    if request.starts_on > request.ends_on or request.ends_on < (now + MALAYSIA_UTC_OFFSET).date():
        raise CouponInvalid()
    minimum = _integer_in(request.min_subtotal_sen, 0, MAX_SEN)
    total_limit = _limit(request.total_use_limit)
    member_limit = _limit(request.per_member_use_limit)
    if minimum is None or total_limit is _INVALID or member_limit is _INVALID:
        raise CouponInvalid()
    if request.coupon_type == TYPE_FIXED:
        amount = _integer_in(request.amount_off_sen, 1, MAX_SEN)
        if amount is None or request.percent_off is not None:
            raise CouponInvalid()
        percent = None
    else:
        percent = _integer_in(request.percent_off, 1, 100)
        if percent is None or request.amount_off_sen is not None:
            raise CouponInvalid()
        amount = None

    starts_at = datetime.combine(request.starts_on, time.min) - MALAYSIA_UTC_OFFSET
    ends_at = datetime.combine(request.ends_on, time(23, 59, 59)) - MALAYSIA_UTC_OFFSET
    coupon = Coupon(
        code=code,
        coupon_type=request.coupon_type,
        amount_off_sen=amount,
        percent_off=percent,
        min_subtotal_sen=minimum,
        starts_at=starts_at,
        ends_at=ends_at,
        total_use_limit=total_limit,
        per_member_use_limit=member_limit,
        is_active=True,
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(coupon)
            db.flush()
    except IntegrityError as exc:
        # 保存点已经回滚；只有已存在的相同规范化代码才是可预期的冲突，其他
        # 完整性错误应交给调用方，而不是伪装成「代码被占用」。
        if db.scalar(select(Coupon.id).where(Coupon.code == code)) is not None:
            raise CouponCodeTaken() from exc
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
    return _row(coupon, 0)


def disable_admin_coupon(
    db: Session, admin_account_id: int, coupon_id: int, now: datetime
) -> AdminCouponRow:
    """停用仍启用的券；已停用券幂等返回，且不改用券记录。"""
    _require_naive_utc(now)
    result = db.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, Coupon.is_active.is_(True))
        .values(is_active=False)
        .execution_options(synchronize_session="fetch")
    )
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise CouponNotFound()
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
    return _row(coupon, _used_count(db, coupon.id))

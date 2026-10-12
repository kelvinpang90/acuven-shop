"""优惠券的查券、预占、核销、释放与会员中心的两个只读查询。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1、2、7 条：优惠券仅会员
可用，先抵商品额、不抵运费；使用额度在下单时预占，取消或支付超时释放；折扣由
app/services/pricing.py 的 price_order 计算；后台修改只影响新订单（用券记录存本单折扣）。
docs/UX.md 0.10 P05 与 P13：结账输入代码；「我的优惠券」列出所有启用中且在有效期内的券，对全部
会员相同（Q19）。docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定：两个上限可留空表示不限，
预占与核销都计次数，退款不恢复，每会员按会员账号计数。
本模块只提供规则函数，不做接口，不接入下单与支付（留给 SHOP-TASK-088 起的任务）。

- 代码规范化：去掉首尾空白后只由 ASCII 字母与数字组成、长 4 到 20 时转大写返回，否则为 None，
  调用方按查无此券处理。先核对字符再转大写：str.upper 会把个别非 ASCII 字母变成 ASCII（如
  「ß」变「SS」），先转再核对会放行这类输入。
- 有效：启用中且生效时间 ≤ 当前时间 ≤ 截止时间（两端都含，截止时间是仍有效的最后一刻）。
- 查券按 not_found、inactive、not_started、expired、below_min_spend、total_exhausted、
  member_exhausted 的顺序判定，首个不成立的即为原因；都成立时可用，并给出 price_order 的券参数
  （fixed 为 fixed_off 的仙数，percent 为 percent_off 的 Decimal 百分比），不另算折扣。
- 计数口径：状态为 reserved 或 used 的用券记录计次，released 不计；总次数按该券全部记录，每会员
  次数按用券记录上的会员 ID（不按订单上的会员 ID，后者在注销时被清空）。上限为空即不限。
- 预占的行锁：依次以 SELECT … FOR UPDATE 锁住会员行、订单行与券行（会员 → 订单 → 券，与会员注销
  及积分规则一致）。券以加锁读重新载入（populate_existing，不沿用 ORM 缓存），用券记录的计数
  也用加锁读，读到最新已提交的状态而不是 MySQL 可重复读的事务快照；同一张券的预占因此在券行锁上
  排队，两个上限都不会超发。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE，并发
  只在 MySQL 上成立。
- 核销与释放：以条件更新（仅当状态为 reserved）改为 used 或 released，写更新时间；used 与
  released 不再改变，退款不改状态、不恢复次数。

时间一律是不带时区的 UTC；金额一律整数仙。写库的函数只 flush、不提交，
由调用方在同一事务里提交。本模块不写日志。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Coupon, CouponUse, Member, Order
from app.models.coupon import (
    CODE_MAX_LENGTH,
    CODE_MIN_LENGTH,
    COUNTED_USE_STATUSES,
    TYPE_FIXED,
    USE_RELEASED,
    USE_RESERVED,
    USE_USED,
)
from app.models.member import MEMBER_ACTIVE

# 查券不可用的原因，按判定顺序。
REASON_NOT_FOUND = "not_found"
REASON_INACTIVE = "inactive"
REASON_NOT_STARTED = "not_started"
REASON_EXPIRED = "expired"
REASON_BELOW_MIN_SPEND = "below_min_spend"
REASON_TOTAL_EXHAUSTED = "total_exhausted"
REASON_MEMBER_EXHAUSTED = "member_exhausted"

REASONS = (
    REASON_NOT_FOUND,
    REASON_INACTIVE,
    REASON_NOT_STARTED,
    REASON_EXPIRED,
    REASON_BELOW_MIN_SPEND,
    REASON_TOTAL_EXHAUSTED,
    REASON_MEMBER_EXHAUSTED,
)

_CODE_PATTERN = re.compile(rf"[A-Za-z0-9]{{{CODE_MIN_LENGTH},{CODE_MAX_LENGTH}}}")


@dataclass(frozen=True)
class CouponCheck:
    """查券或预占的结果：reason 为空即可用，此时给出券 ID 与 price_order 的券参数。

    fixed_off 与 percent_off 至多一个非空；不可用时三者都为空。
    """

    reason: str | None
    coupon_id: int | None = None
    fixed_off: int | None = None
    percent_off: Decimal | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None

    def price_kwargs(self) -> dict[str, int | Decimal]:
        """传给 price_order 的关键字参数；不可用时为空，即不用券。"""
        if self.fixed_off is not None:
            return {"fixed_off": self.fixed_off}
        if self.percent_off is not None:
            return {"percent_off": self.percent_off}
        return {}


@dataclass(frozen=True)
class AvailableCoupon:
    """会员中心「我的优惠券」的可用券一项。fixed 时百分比为空，percent 时固定金额为空。"""

    code: str
    coupon_type: str
    amount_off_sen: int | None
    percent_off: int | None
    min_subtotal_sen: int
    ends_at: datetime


@dataclass(frozen=True)
class UsedCoupon:
    """会员中心「我的优惠券」的使用记录一项。"""

    code: str
    order_number: str
    coupon_discount_sen: int


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_sen(value: object, name: str) -> int:
    # bool 是 int 的子类，但把 True 当 1 仙显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _naive(now: datetime) -> datetime:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    return now


def normalize_coupon_code(code: object) -> str | None:
    """去掉首尾空白后只由字母与数字组成、长 4 到 20 时返回大写代码，否则为 None。"""
    if not isinstance(code, str):
        return None
    stripped = code.strip()
    if not _CODE_PATTERN.fullmatch(stripped):
        return None
    return stripped.upper()


def is_coupon_valid(coupon: Coupon, now: datetime) -> bool:
    """启用中且生效时间 ≤ 当前时间 ≤ 截止时间（两端都含）。"""
    now = _naive(now)
    return coupon.is_active and coupon.starts_at <= now <= coupon.ends_at


def _counted_member_ids(db: Session, coupon_id: int, *, lock: bool) -> list[int]:
    """该券计次（reserved、used）的用券记录的会员 ID，一条记录一项。

    lock 为真时加锁读（SELECT … FOR UPDATE）：MySQL 可重复读下读到的是最新已提交的状态，而不是
    事务快照。
    """
    stmt = select(CouponUse.member_id).where(
        CouponUse.coupon_id == coupon_id,
        CouponUse.status.in_(COUNTED_USE_STATUSES),
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt).all())


def _judge(
    db: Session,
    coupon: Coupon | None,
    member_id: int,
    subtotal_sen: int,
    now: datetime,
    *,
    lock: bool,
) -> CouponCheck:
    if coupon is None:
        return CouponCheck(REASON_NOT_FOUND)
    if not coupon.is_active:
        return CouponCheck(REASON_INACTIVE)
    if now < coupon.starts_at:
        return CouponCheck(REASON_NOT_STARTED)
    if now > coupon.ends_at:
        return CouponCheck(REASON_EXPIRED)
    if subtotal_sen < coupon.min_subtotal_sen:
        return CouponCheck(REASON_BELOW_MIN_SPEND)
    total_limit = coupon.total_use_limit
    member_limit = coupon.per_member_use_limit
    if total_limit is not None or member_limit is not None:
        member_ids = _counted_member_ids(db, coupon.id, lock=lock)
        if total_limit is not None and len(member_ids) >= total_limit:
            return CouponCheck(REASON_TOTAL_EXHAUSTED)
        member_count = sum(1 for used_by in member_ids if used_by == member_id)
        if member_limit is not None and member_count >= member_limit:
            return CouponCheck(REASON_MEMBER_EXHAUSTED)
    if coupon.coupon_type == TYPE_FIXED:
        return CouponCheck(None, coupon_id=coupon.id, fixed_off=coupon.amount_off_sen)
    return CouponCheck(None, coupon_id=coupon.id, percent_off=Decimal(coupon.percent_off))


# ---------------------------------------------------------------------------
# 查券与预占
# ---------------------------------------------------------------------------


def check_coupon(
    db: Session, code: object, member_id: int, subtotal_sen: int, now: datetime
) -> CouponCheck:
    """结账输入代码时查券：返回首个不成立的原因，或可用及 price_order 的券参数。

    代码规范化不通过即 not_found。只读，不写库、不加锁；结果只供计价显示，下单时由
    reserve_coupon 在行锁下重新判定。
    """
    member_id = _positive_id(member_id, "member_id")
    subtotal_sen = _non_negative_sen(subtotal_sen, "subtotal_sen")
    now = _naive(now)
    normalized = normalize_coupon_code(code)
    coupon = None
    if normalized is not None:
        coupon = db.scalars(select(Coupon).where(Coupon.code == normalized)).one_or_none()
    return _judge(db, coupon, member_id, subtotal_sen, now, lock=False)


def reserve_coupon(
    db: Session,
    coupon_id: int,
    order_id: int,
    member_id: int,
    coupon_discount_sen: int,
    subtotal_sen: int,
    now: datetime,
) -> CouponCheck:
    """下单时预占：在会员 → 订单 → 券的行锁下按查券的全部条件重新判定，通过时写一条 reserved 的
    用券记录。

    会员不存在或不是 active、订单不存在或其会员 ID 不等于该会员、同一订单已有用券记录时抛
    ValueError。不通过时返回原因，不写记录。coupon_discount_sen 是 price_order 结果里的券折扣
    合计，照存不重算。只 flush、不提交。
    """
    coupon_id = _positive_id(coupon_id, "coupon_id")
    order_id = _positive_id(order_id, "order_id")
    member_id = _positive_id(member_id, "member_id")
    coupon_discount_sen = _non_negative_sen(coupon_discount_sen, "coupon_discount_sen")
    subtotal_sen = _non_negative_sen(subtotal_sen, "subtotal_sen")
    now = _naive(now)

    member = db.scalars(
        select(Member)
        .where(Member.id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if member is None or member.status != MEMBER_ACTIVE:
        raise ValueError("member is not an active member")
    order = db.scalars(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if order is None or order.member_id != member_id:
        raise ValueError("order does not belong to the member")
    coupon = db.scalars(
        select(Coupon)
        .where(Coupon.id == coupon_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()

    existing = db.scalars(
        select(CouponUse.id).where(CouponUse.order_id == order_id).with_for_update()
    ).first()
    if existing is not None:
        raise ValueError("order already has a coupon use")

    result = _judge(db, coupon, member_id, subtotal_sen, now, lock=True)
    if not result.ok:
        return result
    db.add(
        CouponUse(
            coupon_id=coupon_id,
            order_id=order_id,
            member_id=member_id,
            status=USE_RESERVED,
            coupon_discount_sen=coupon_discount_sen,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    return result


# ---------------------------------------------------------------------------
# 核销与释放
# ---------------------------------------------------------------------------


def _finish_reservation(db: Session, order_id: int, status: str, now: datetime) -> bool:
    order_id = _positive_id(order_id, "order_id")
    now = _naive(now)
    result = db.execute(
        update(CouponUse)
        .where(CouponUse.order_id == order_id, CouponUse.status == USE_RESERVED)
        .values(status=status, updated_at=now)
        .execution_options(synchronize_session="fetch")
    )
    db.flush()
    return bool(result.rowcount)


def use_coupon(db: Session, order_id: int, now: datetime) -> bool:
    """支付成功时核销：把该订单 reserved 的用券记录改为 used。

    没有记录或状态不是 reserved 时不改、为假（重复调用不出错）。只 flush、不提交。
    """
    return _finish_reservation(db, order_id, USE_USED, now)


def release_coupon(db: Session, order_id: int, now: datetime) -> bool:
    """取消或支付超时时释放：把该订单 reserved 的用券记录改为 released。

    没有记录或状态不是 reserved 时不改、为假；已核销的不释放（退款不恢复次数）。
    只 flush、不提交。
    """
    return _finish_reservation(db, order_id, USE_RELEASED, now)


# ---------------------------------------------------------------------------
# 会员中心「我的优惠券」
# ---------------------------------------------------------------------------


def list_available_coupons(db: Session, now: datetime) -> list[AvailableCoupon]:
    """所有在当前时间有效的券，对全部会员相同，不按两个上限与是否用过过滤（Q19）。

    按截止时间升序、同时刻按 ID 升序。
    """
    now = _naive(now)
    coupons = db.scalars(
        select(Coupon)
        .where(Coupon.is_active.is_(True), Coupon.starts_at <= now, Coupon.ends_at >= now)
        .order_by(Coupon.ends_at, Coupon.id)
    ).all()
    return [
        AvailableCoupon(
            code=coupon.code,
            coupon_type=coupon.coupon_type,
            amount_off_sen=coupon.amount_off_sen,
            percent_off=coupon.percent_off,
            min_subtotal_sen=coupon.min_subtotal_sen,
            ends_at=coupon.ends_at,
        )
        for coupon in coupons
    ]


def list_member_used_coupons(db: Session, member_id: int) -> list[UsedCoupon]:
    """该会员已核销（used）的用券记录，按创建时间降序、同时刻按 ID 降序。"""
    member_id = _positive_id(member_id, "member_id")
    rows = db.execute(
        select(Coupon.code, Order.order_number, CouponUse.coupon_discount_sen)
        .select_from(CouponUse)
        .join(Coupon, Coupon.id == CouponUse.coupon_id)
        .join(Order, Order.id == CouponUse.order_id)
        .where(CouponUse.member_id == member_id, CouponUse.status == USE_USED)
        .order_by(CouponUse.created_at.desc(), CouponUse.id.desc())
    ).all()
    return [
        UsedCoupon(code=code, order_number=order_number, coupon_discount_sen=discount)
        for code, order_number, discount in rows
    ]

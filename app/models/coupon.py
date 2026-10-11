"""优惠券与用券记录。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 Coupon / CouponUse 一行与「计价、优惠、
积分与库存」第 1 条（优惠券仅会员可用，使用额度在下单时预占，取消或支付超时释放），以及
docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定：总使用上限与每会员使用上限都可留空表示
不限；待支付订单的预占与已支付订单的核销都计入次数，取消或支付超时释放后不计，退款不恢复次数；
每会员按会员账号计数。

时间一律是不带时区的 UTC；金额一律是整数仙。指向券、订单与会员的外键一律 RESTRICT：
被用券记录引用的券不能物理删除，只能停用。除设计指定可空的列外全部非空：检查约束遇到空值会
放行，非空约束不能省。检查约束只用比较、LENGTH、IN 与 IS NULL，MySQL 与 SQLite 都能执行。
券代码库里存大写，库里只保证长 4 到 20，字符集与大小写由写入方保证（MySQL 默认排序规则不区分
大小写，唯一约束也按不区分大小写判断）。
这里只建表与约束：查券、预占、释放、核销与后台维护都由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 券的类型：固定金额（仙）或百分比（整数，1 到 100）。
TYPE_FIXED = "fixed"
TYPE_PERCENT = "percent"

COUPON_TYPES = (TYPE_FIXED, TYPE_PERCENT)

# 用券记录的状态。reserved：下单时预占，订单待支付；used：订单已支付，核销；
# released：订单取消或支付超时，额度已释放。reserved 与 used 都计入使用次数，released 不计；
# 退款不改状态，不恢复次数。
USE_RESERVED = "reserved"
USE_USED = "used"
USE_RELEASED = "released"

COUPON_USE_STATUSES = (USE_RESERVED, USE_USED, USE_RELEASED)
# 计入总使用次数与每会员使用次数的状态。
COUNTED_USE_STATUSES = (USE_RESERVED, USE_USED)

CODE_MIN_LENGTH = 4
CODE_MAX_LENGTH = 20
PERCENT_MIN = 1
PERCENT_MAX = 100


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class Coupon(Base):
    """一张优惠券：固定金额或百分比、有效期、最低商品消费、两个使用上限与启停状态。

    有效期是闭区间：生效时间起、截止时间（仍有效的最后一刻）止。两个上限为空表示不限。
    券一旦被用券记录引用就不能删除，停用即把启用状态改为假。
    """

    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint(
            f"LENGTH(code) >= {CODE_MIN_LENGTH} AND LENGTH(code) <= {CODE_MAX_LENGTH}",
            name="code_length",
        ),
        CheckConstraint(f"coupon_type IN {_sql_in(COUPON_TYPES)}", name="type_valid"),
        # 分成两条，未知类型只由 type_valid 拒绝。
        CheckConstraint(
            f"coupon_type <> '{TYPE_FIXED}'"
            " OR (amount_off_sen IS NOT NULL AND amount_off_sen > 0 AND percent_off IS NULL)",
            name="fixed_amount",
        ),
        CheckConstraint(
            f"coupon_type <> '{TYPE_PERCENT}'"
            f" OR (percent_off IS NOT NULL AND percent_off >= {PERCENT_MIN}"
            f" AND percent_off <= {PERCENT_MAX} AND amount_off_sen IS NULL)",
            name="percent_value",
        ),
        CheckConstraint("min_subtotal_sen >= 0", name="min_subtotal_sen_non_negative"),
        CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        CheckConstraint(
            "total_use_limit IS NULL OR total_use_limit >= 1",
            name="total_use_limit_positive",
        ),
        CheckConstraint(
            "per_member_use_limit IS NULL OR per_member_use_limit >= 1",
            name="per_member_use_limit_positive",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 大写；结账输入的代码由写入方规范化后再比较。
    code: Mapped[str] = mapped_column(String(CODE_MAX_LENGTH))
    coupon_type: Mapped[str] = mapped_column(String(10))
    # fixed 时为抵扣金额（仙），percent 时为空。
    amount_off_sen: Mapped[int | None] = mapped_column(Integer)
    # percent 时为抵扣百分比（1 到 100），fixed 时为空。
    percent_off: Mapped[int | None] = mapped_column(Integer)
    # 商品小计达到此金额（仙）才可用；0 即不限。
    min_subtotal_sen: Mapped[int] = mapped_column(Integer, server_default="0")
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    # 仍有效的最后一刻。
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    # 为空即不限。
    total_use_limit: Mapped[int | None] = mapped_column(Integer)
    per_member_use_limit: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CouponUse(Base):
    """一张订单用的券：下单时预占，支付后核销，取消或支付超时释放。

    一单至多一张券（订单 ID 全表唯一）。会员 ID 非空：会员行不物理删除，注销只清空会员的手机号
    与密码哈希，这里的记录随之去标识化、照常保留；订单上的会员 ID 注销时被清空，每会员计数只能
    靠这里的会员 ID。本单券折扣是订单快照 coupon_discount_sen 的副本。按（券、状态）与
    （券、会员、状态）的索引供之后计算总使用次数与每会员使用次数。
    """

    __tablename__ = "coupon_uses"
    __table_args__ = (
        UniqueConstraint("order_id"),
        Index("ix_coupon_uses_coupon_id_status", "coupon_id", "status"),
        Index("ix_coupon_uses_coupon_id_member_id_status", "coupon_id", "member_id", "status"),
        CheckConstraint(f"status IN {_sql_in(COUPON_USE_STATUSES)}", name="status_valid"),
        CheckConstraint("coupon_discount_sen >= 0", name="coupon_discount_sen_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coupon_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("coupons.id", ondelete="RESTRICT"),
    )
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    member_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("members.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(String(10))
    coupon_discount_sen: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

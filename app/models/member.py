"""会员、会员会话、短信验证记录、短信每日用量与预算。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Member / VerificationAttempt 一行，
以及「失败、并发与重试」「权限与资料保护」「资料保留」。

时间一律是不带时区的 UTC；短信费用一律是整数微美元（百万分之一美元），不用浮点或 Decimal。
会员行不物理删除（注销只清空手机号与密码哈希、把状态改为 deleted），指向会员的外键一律
RESTRICT。除设计指定可空的列外全部非空：检查约束遇到空值会放行，非空约束不能省。

检查约束只用比较、LENGTH、LIKE、IN / NOT IN 与 IS NULL，MySQL 与 SQLite 都能执行。
手机号库里只保证以加号开头且不超过 16 个字符，是否为规范化的 E.164 由写入方按
app/services/phone.py 校验；会话摘要库里只保证长 64，是否为小写十六进制由写入方保证
（MySQL 默认排序规则不区分大小写，唯一约束也按不区分大小写判断）。
这里只建表与约束：密码哈希、会话签发、短信发送、限流、预算的原子预占与结算、
订单认领与注销都由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 会员账号状态。注销后行保留，状态为 deleted。
MEMBER_ACTIVE = "active"
MEMBER_DELETED = "deleted"

MEMBER_STATUSES = (MEMBER_ACTIVE, MEMBER_DELETED)

# 短信验证的用途：结账验证、注册、短信登录、密码重设、注销确认。
PURPOSE_CHECKOUT = "checkout"
PURPOSE_REGISTER = "register"
PURPOSE_LOGIN = "login"
PURPOSE_RESET_PASSWORD = "reset_password"
PURPOSE_DELETE_ACCOUNT = "delete_account"

VERIFICATION_PURPOSES = (
    PURPOSE_CHECKOUT,
    PURPOSE_REGISTER,
    PURPOSE_LOGIN,
    PURPOSE_RESET_PASSWORD,
    PURPOSE_DELETE_ACCOUNT,
)

# 短信验证记录的状态。sent：提供方已受理、等待提交验证码；approved / rejected：提供方的
# 核验结果；undeliverable：提供方无法送达（可能已有、也可能没有请求 ID）；
# suspended：本系统停发（预算、服务不可用或 Redis / MySQL 不可用），短信未发出、没有请求 ID。
VERIFICATION_SENT = "sent"
VERIFICATION_APPROVED = "approved"
VERIFICATION_REJECTED = "rejected"
VERIFICATION_UNDELIVERABLE = "undeliverable"
VERIFICATION_SUSPENDED = "suspended"

VERIFICATION_STATUSES = (
    VERIFICATION_SENT,
    VERIFICATION_APPROVED,
    VERIFICATION_REJECTED,
    VERIFICATION_UNDELIVERABLE,
    VERIFICATION_SUSPENDED,
)
# 这些状态都经提供方受理，必有提供方请求 ID。
PROVIDER_ACCEPTED_STATUSES = (
    VERIFICATION_SENT,
    VERIFICATION_APPROVED,
    VERIFICATION_REJECTED,
)

PHONE_MAX_LENGTH = 16
TOKEN_HASH_LENGTH = 64
PASSWORD_HASH_MAX_LENGTH = 255
PROVIDER_REQUEST_ID_MAX_LENGTH = 64


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class Member(Base):
    """会员账号：只有手机号、密码哈希、状态与创建、注销时间，不存其他任何个人资料。

    短信验证自动注册的会员起初没有密码哈希。注销时删除手机号与密码哈希、写注销时间，
    行本身保留，订单与会话仍可引用它；手机号全表唯一，多个已注销会员的手机号都为空可以共存。
    """

    __tablename__ = "members"
    __table_args__ = (
        UniqueConstraint("phone"),
        CheckConstraint(f"status IN {_sql_in(MEMBER_STATUSES)}", name="status_valid"),
        # 分成两条，未知状态只由 status_valid 拒绝。
        CheckConstraint(
            f"status <> '{MEMBER_ACTIVE}' OR (phone IS NOT NULL AND deleted_at IS NULL)",
            name="active_has_phone",
        ),
        CheckConstraint(
            f"status <> '{MEMBER_DELETED}'"
            " OR (phone IS NULL AND password_hash IS NULL AND deleted_at IS NOT NULL)",
            name="deleted_cleared",
        ),
        CheckConstraint(
            f"phone IS NULL OR (phone LIKE '+%' AND LENGTH(phone) <= {PHONE_MAX_LENGTH})",
            name="phone_format",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 规范化的 E.164；注销后为空。
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH))
    password_hash: Mapped[str | None] = mapped_column(String(PASSWORD_HASH_MAX_LENGTH))
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)


class MemberSession(Base):
    """会员登录会话。只存会话令牌的 SHA-256 十六进制摘要，令牌原文从不入库。

    退出与注销写撤销时间，行保留。会话时长、令牌生成与 cookie 由之后的接口任务决定。
    """

    __tablename__ = "member_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(
            f"LENGTH(token_hash) = {TOKEN_HASH_LENGTH}",
            name="token_hash_length",
        ),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("members.id", ondelete="RESTRICT"),
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class VerificationAttempt(Base):
    """一次短信验证请求及其结果。验证码由 Twilio Verify 托管，这里不存验证码、IP 或其他资料。

    按手机号与创建时间的索引供之后判定「该号码刚遇到短信无法送达或停发」的降级与防滥用。
    设计要求短期保留，到期删除由之后单独登记的任务在短信正式上线前完成。
    """

    __tablename__ = "verification_attempts"
    __table_args__ = (
        UniqueConstraint("provider_request_id"),
        Index("ix_verification_attempts_phone_created_at", "phone", "created_at"),
        CheckConstraint(
            f"phone LIKE '+%' AND LENGTH(phone) <= {PHONE_MAX_LENGTH}",
            name="phone_format",
        ),
        CheckConstraint(
            f"purpose IN {_sql_in(VERIFICATION_PURPOSES)}",
            name="purpose_valid",
        ),
        CheckConstraint(
            f"status IN {_sql_in(VERIFICATION_STATUSES)}",
            name="status_valid",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(PROVIDER_ACCEPTED_STATUSES)}"
            " OR provider_request_id IS NOT NULL",
            name="accepted_has_request_id",
        ),
        CheckConstraint(
            f"status <> '{VERIFICATION_SUSPENDED}' OR provider_request_id IS NULL",
            name="suspended_no_request_id",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 规范化的 E.164。
    phone: Mapped[str] = mapped_column(String(PHONE_MAX_LENGTH))
    purpose: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(16))
    # 提供方（Twilio Verify）的请求 ID；本系统停发时为空。
    provider_request_id: Mapped[str | None] = mapped_column(String(PROVIDER_REQUEST_ID_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class SmsDailyUsage(Base):
    """短信每日总量与费用预算的 MySQL 兜底记录，每个马来西亚日期一行。

    日期按马来西亚时间（UTC+8）切日。费用是整数微美元：发送前按目的地预占保守的单次最高费用，
    结算后更新已结算费用；原子预占与结算由之后的短信服务任务在事务内实现。
    """

    __tablename__ = "sms_daily_usage"
    __table_args__ = (
        UniqueConstraint("usage_date"),
        CheckConstraint("sent_count >= 0", name="sent_count_non_negative"),
        CheckConstraint("reserved_micro_usd >= 0", name="reserved_micro_usd_non_negative"),
        CheckConstraint("settled_micro_usd >= 0", name="settled_micro_usd_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 马来西亚时间的日期。
    usage_date: Mapped[date] = mapped_column(Date)
    sent_count: Mapped[int] = mapped_column(Integer)
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger)
    settled_micro_usd: Mapped[int] = mapped_column(BigInteger)

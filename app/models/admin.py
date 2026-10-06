"""管理员账号、后台会话与审计记录。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 AdminAccount / AuditEvent 一行
（「单一管理员、密码哈希、会话及后台操作记录」）、SiteSetting 一行（后台修改写入
AuditEvent：操作者、时间、新旧值）、「权限与资料保护」第 4–6 条，以及 docs/HANDOFF.md
记录的 Kelvin 2026-10-04 管理员登录决定（库里至多一个管理员账号，由账号表的单例槽
唯一约束保证；每条审计记录都属于某个账号；审计记录不存来源地址与提交的用户名）。

时间一律是不带时区的 UTC。外键一律 RESTRICT：被会话或审计记录引用的账号不能物理删除。
除后台会话撤销时间与审计记录的对象类别、对象 ID、旧值、新值外全部非空：检查约束遇到空值会放行，
非空约束不能省。

检查约束只用比较、LENGTH、LIKE、IN / NOT IN 与 IS NULL，MySQL 与 SQLite 都能执行。
用户名只含小写字母、数字与下划线、会话摘要为小写十六进制、操作名与对象类别取自写入方的常量，
都由写入方保证；库里只检查长度（这些都是 ASCII，MySQL 按字节计的 LENGTH 与字符数相同）。
旧值与新值可能含非 ASCII 字符，上限只由列长保证，库里不加长度上限检查（与 app/models/order.py
的幂等键相同）。
这里只建表与约束：密码哈希、建账号命令、会话签发与时长、锁定与登录都由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
PASSWORD_HASH_MAX_LENGTH = 255
TOKEN_HASH_LENGTH = 64
ACTION_MAX_LENGTH = 40
TARGET_TYPE_MAX_LENGTH = 20
AUDIT_VALUE_MAX_LENGTH = 255

# 单例槽唯一的合法值：账号表的这一列全表唯一且只能是 1，故至多一行。
SINGLETON_SLOT = 1


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class AdminAccount(Base):
    """唯一的管理员账号：只有用户名、密码哈希、单例槽与创建、密码更新时间，不存其他个人资料。

    两个建账号命令并发时，后插入的一个撞上单例槽的唯一约束被拒。
    """

    __tablename__ = "admin_accounts"
    __table_args__ = (
        UniqueConstraint("username"),
        UniqueConstraint("singleton_slot"),
        CheckConstraint(
            f"LENGTH(username) >= {USERNAME_MIN_LENGTH}"
            f" AND LENGTH(username) <= {USERNAME_MAX_LENGTH}",
            name="username_length",
        ),
        CheckConstraint(f"singleton_slot = {SINGLETON_SLOT}", name="singleton_slot_one"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH))
    password_hash: Mapped[str] = mapped_column(String(PASSWORD_HASH_MAX_LENGTH))
    singleton_slot: Mapped[int] = mapped_column(Integer, default=SINGLETON_SLOT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    password_updated_at: Mapped[datetime] = mapped_column(DateTime)


class AdminSession(Base):
    """后台登录会话。只存会话令牌的 SHA-256 十六进制摘要，令牌原文从不入库。

    退出写撤销时间，行保留。会话时长、令牌生成与 cookie 由之后的任务实现。
    """

    __tablename__ = "admin_sessions"
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
    admin_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("admin_accounts.id", ondelete="RESTRICT"),
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditEvent(Base):
    """一条后台操作记录：发生时间、操作的管理员、操作名，可选的对象（类别与 ID）与旧值、新值。

    操作名取值由写入方的常量决定，库里不按枚举约束，之后的任务增加操作不必改约束。
    不存来源地址、提交的用户名、密码或其他个人资料；旧值与新值不含个人资料由写入方保证。
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            f"LENGTH(action) >= 1 AND LENGTH(action) <= {ACTION_MAX_LENGTH}",
            name="action_length",
        ),
        CheckConstraint(
            "target_type IS NULL OR (LENGTH(target_type) >= 1"
            f" AND LENGTH(target_type) <= {TARGET_TYPE_MAX_LENGTH})",
            name="target_type_length",
        ),
        CheckConstraint(
            "(target_type IS NULL AND target_id IS NULL)"
            " OR (target_type IS NOT NULL AND target_id IS NOT NULL)",
            name="target_both_or_neither",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    admin_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("admin_accounts.id", ondelete="RESTRICT"),
    )
    action: Mapped[str] = mapped_column(String(ACTION_MAX_LENGTH))
    target_type: Mapped[str | None] = mapped_column(String(TARGET_TYPE_MAX_LENGTH))
    target_id: Mapped[int | None] = mapped_column(Integer)
    old_value: Mapped[str | None] = mapped_column(String(AUDIT_VALUE_MAX_LENGTH))
    new_value: Mapped[str | None] = mapped_column(String(AUDIT_VALUE_MAX_LENGTH))

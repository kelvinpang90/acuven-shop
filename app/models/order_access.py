"""订单访问会话与按订单的授权记录。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条：游客短期凭据与查单授权
「都以服务端保存的会话实现，服务端校验所属订单、到期时间并可撤销」，「一个浏览器可同时持有
多张订单的授权，各自独立到期」；以及第 7 条（日志不记录个人资料与查询参数）。

一个浏览器只有一个访问会话（一个 cookie），每张订单、每种范围各一条授权挂在会话下。
会话只存令牌的 SHA-256 十六进制摘要，令牌原文从不入库；不存 IP、浏览器标识或任何个人资料。
时间一律是不带时区的 UTC。订单长期保存，授权指向订单与会话的外键一律 RESTRICT。
除撤销时间外所有列非空：检查约束遇到空值会放行，非空约束不能省。

检查约束只用比较、LENGTH 与 IN，MySQL 与 SQLite 都能执行。会话摘要库里只保证长 64，
是否为小写十六进制由写入方（app/services/order_access.py）保证。
签发、校验、cookie 与 CSRF 见 app/services/order_access.py。
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

# 授权范围：guest_checkout 是游客订单创建后给当前浏览器的短期凭据（模拟支付、失败重试、
# 取消与结果页）；lookup 是查单通过后的授权（查看、确认收货、申请退款）。两者不能互相替代。
SCOPE_GUEST_CHECKOUT = "guest_checkout"
SCOPE_LOOKUP = "lookup"

ACCESS_SCOPES = (SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP)

TOKEN_HASH_LENGTH = 64


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class OrderAccessSession(Base):
    """一个浏览器的订单访问会话：令牌摘要、创建、到期与撤销时间，不存其他任何资料。

    到期时间由签发方维持为它与各授权到期时间中较晚者；撤销写撤销时间，行保留。
    """

    __tablename__ = "order_access_sessions"
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
    # 会话令牌的 SHA-256 十六进制摘要（小写）。
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class OrderAccessGrant(Base):
    """会话对一张订单、一种范围的授权，各自到期、可单独撤销。

    同一会话、订单与范围只有一行：重复签发延长原授权的到期时间并清空撤销时间，不新增行。
    按订单 ID 建索引，供之后按订单撤销授权（如订单取消或被认领）。
    """

    __tablename__ = "order_access_grants"
    __table_args__ = (
        UniqueConstraint("session_id", "order_id", "scope"),
        CheckConstraint(f"scope IN {_sql_in(ACCESS_SCOPES)}", name="scope_valid"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order_access_sessions.id", ondelete="RESTRICT"),
    )
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

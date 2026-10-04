"""订单访问会话与按订单的访问授权。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条：游客订单创建后的短期凭据
与查单通过后的查单授权「都以服务端保存的会话实现，服务端校验所属订单、到期时间并可撤销」，
「一个浏览器可同时持有多张订单的授权，各自独立到期」；以及第 7 条（日志不记录查询参数）。

一个浏览器一个会话（cookie 里是会话令牌原文，库里只存它的 SHA-256 十六进制摘要），
会话下每张订单、每种范围一条授权，各自有到期与撤销时间。会话不存 IP、浏览器标识或任何
个人资料。时间一律是不带时区的 UTC。会话与授权行都不物理删除（撤销只写撤销时间），
外键一律 RESTRICT。除撤销时间外全部非空：检查约束遇到空值会放行，非空约束不能省。

检查约束只用比较、LENGTH 与 IN，MySQL 与 SQLite 都能执行。会话摘要库里只保证长 64，
是否为小写十六进制由写入方（app/services/order_access.py）保证。签发、校验、cookie 与
CSRF 令牌见 app/services/order_access.py。
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

# 授权范围。guest_checkout：游客订单创建后的短期凭据，用于该单的模拟支付、失败重试、取消与
# 结果页；lookup：查单通过后的查单授权，用于查看该单、确认收货与申请退款。两者互不替代。
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
    """一个浏览器的订单访问会话。只存会话令牌的 SHA-256 十六进制摘要，令牌原文从不入库。

    到期时间不早于它下面最晚到期的授权；撤销会话即撤销它下面的全部授权。
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
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class OrderAccessGrant(Base):
    """会话对一张订单、一种范围的授权。

    同一会话、订单与范围只有一条；重复签发延长这一条的到期时间并清空撤销时间，不新增行。
    唯一约束以会话 ID 打头，MySQL 用它作会话外键的索引；订单 ID 另建普通索引，
    供按订单撤销授权与订单外键使用。
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

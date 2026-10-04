"""订单访问授权：签发与校验授权、设置 cookie、计算与校验 CSRF 令牌。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条：游客短期凭据与查单授权
都是 30 分钟、仅限该单，「以服务端保存的会话实现，服务端校验所属订单、到期时间并可撤销；
以 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，不交给页面脚本」，「支付、取消、
确认收货与退款申请等写操作另须 CSRF 令牌」，「一个浏览器可同时持有多张订单的授权，各自
独立到期」；以及第 7 条（日志不记录订单查询参数）。

一个浏览器一个 cookie（__Host-shop_order_access），值是会话令牌原文；库里只存它的 SHA-256
摘要。CSRF 令牌不入库，由令牌原文加固定用途前缀做 SHA-256 得到，持有 cookie 的请求随时可
由服务端重新算出，交给页面后由页面放进请求头。

本模块不写日志；令牌、CSRF 令牌与 cookie 值不出现在任何异常消息里。签发只 flush、不提交，
由调用方在同一事务里提交。时间参数一律是不带时区的 UTC，带时区的拒绝（ValueError）。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order_access import ACCESS_SCOPES, OrderAccessGrant, OrderAccessSession

COOKIE_NAME = "__Host-shop_order_access"

# 游客短期凭据与查单授权都是 30 分钟；cookie 的 Max-Age 同此。
GRANT_LIFETIME = timedelta(minutes=30)
COOKIE_MAX_AGE_SECONDS = int(GRANT_LIFETIME.total_seconds())

# secrets.token_urlsafe(32)：32 字节即 256 位随机，编码为 43 个 URL 安全字符、无填充。
TOKEN_BYTES = 32
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")

# CSRF 令牌的用途前缀，使它与入库的会话摘要（令牌原文直接做 SHA-256）不同。
_CSRF_PREFIX = b"shop-order-access-csrf:"


@dataclass(frozen=True)
class IssuedGrant:
    """签发结果。new_token 只在新建会话时有值，沿用会话时为空。"""

    new_token: str | None
    csrf_token: str
    expires_at: datetime


def _check_now(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")


def _check_scope(scope: str) -> None:
    if scope not in ACCESS_SCOPES:
        raise ValueError("unknown order access scope")


def _well_formed(value: object) -> str | None:
    """格式合法的令牌原样返回，否则为空；只看格式，不查库。"""
    if isinstance(value, str) and _TOKEN_PATTERN.fullmatch(value) is not None:
        return value
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _active_session(db: Session, token: str, now: datetime) -> OrderAccessSession | None:
    """令牌对应的未撤销、未到期的会话；调用方已确认令牌格式合法。"""
    return db.scalars(
        select(OrderAccessSession).where(
            OrderAccessSession.token_hash == _token_hash(token),
            OrderAccessSession.revoked_at.is_(None),
            OrderAccessSession.expires_at > now,
        )
    ).one_or_none()


def csrf_token_for(token: str) -> str:
    """由会话令牌原文算出 CSRF 令牌：SHA-256(用途前缀 + 令牌)，64 位小写十六进制。"""
    return hashlib.sha256(_CSRF_PREFIX + token.encode("ascii")).hexdigest()


def issue_grant(
    db: Session,
    cookie_value: str | None,
    order_id: int,
    scope: str,
    now: datetime,
) -> IssuedGrant:
    """给当前浏览器签发（或延长）对一张订单、一种范围的 30 分钟授权。

    cookie_value 是请求带来的 __Host-shop_order_access 的值（没有时为空）。它格式合法且对应
    的会话未撤销、未到期时沿用该会话，否则新建会话（令牌由 secrets 生成，256 位随机，只含
    URL 安全字符）。同一会话、订单与范围已有授权时把它的到期时间更新为 now + 30 分钟并清空
    撤销时间，否则新建授权。会话到期时间更新为它与新授权到期时间中较晚者。

    只 flush、不提交。并发对同一会话、订单与范围签发时，后提交的一方可能违反唯一约束，
    由调用方回滚重试。调用方应随后用 set_order_access_cookie 设置 cookie：新建会话时传
    new_token，沿用会话时传原 cookie 值，使 cookie 的 Max-Age 与新授权同时到期。
    """
    _check_now(now)
    _check_scope(scope)

    token = _well_formed(cookie_value)
    session = _active_session(db, token, now) if token is not None else None

    expires_at = now + GRANT_LIFETIME
    new_token: str | None = None
    if token is None or session is None:
        token = new_token = secrets.token_urlsafe(TOKEN_BYTES)
        session = OrderAccessSession(
            token_hash=_token_hash(token),
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
        )
        db.add(session)
        db.flush()

    grant = db.scalars(
        select(OrderAccessGrant).where(
            OrderAccessGrant.session_id == session.id,
            OrderAccessGrant.order_id == order_id,
            OrderAccessGrant.scope == scope,
        )
    ).one_or_none()
    if grant is None:
        db.add(
            OrderAccessGrant(
                session_id=session.id,
                order_id=order_id,
                scope=scope,
                created_at=now,
                expires_at=expires_at,
                revoked_at=None,
            )
        )
    else:
        grant.expires_at = expires_at
        grant.revoked_at = None

    session.expires_at = max(session.expires_at, expires_at)
    db.flush()

    return IssuedGrant(
        new_token=new_token,
        csrf_token=csrf_token_for(token),
        expires_at=expires_at,
    )


def verify_grant(
    db: Session,
    cookie_value: str | None,
    order_id: int,
    scope: str,
    now: datetime,
) -> bool:
    """当前浏览器是否持有对该订单、该范围的有效授权。

    只有会话存在、未撤销、未到期，且该会话对该订单、该范围的授权存在、未撤销、未到期时
    才通过。格式不合法的 cookie 值不查库直接不通过。guest_checkout 与 lookup 互不替代，
    一张订单的授权不能用于其他订单。写操作还须同时通过 verify_csrf。
    """
    _check_now(now)
    _check_scope(scope)
    token = _well_formed(cookie_value)
    if token is None:
        return False

    grant_id = db.scalars(
        select(OrderAccessGrant.id)
        .join(OrderAccessSession, OrderAccessGrant.session_id == OrderAccessSession.id)
        .where(
            OrderAccessSession.token_hash == _token_hash(token),
            OrderAccessSession.revoked_at.is_(None),
            OrderAccessSession.expires_at > now,
            OrderAccessGrant.order_id == order_id,
            OrderAccessGrant.scope == scope,
            OrderAccessGrant.revoked_at.is_(None),
            OrderAccessGrant.expires_at > now,
        )
    ).one_or_none()
    return grant_id is not None


def verify_csrf(
    db: Session,
    cookie_value: str | None,
    csrf_header: str | None,
    now: datetime,
) -> bool:
    """请求头里的 CSRF 令牌是否与由 cookie 重新算出的值一致。

    支付、取消、确认收货与退款申请等写操作必须同时通过 verify_grant（该订单、该范围的授权）
    与本函数，缺一不可；只读的查看不需要 CSRF。

    请求头缺失或为空、cookie 格式不合法或对应的会话不存在、已撤销、已到期时都不通过。
    比较用 hmac.compare_digest 做常量时间比较。
    """
    _check_now(now)
    token = _well_formed(cookie_value)
    if token is None or not csrf_header:
        return False
    if _active_session(db, token, now) is None:
        return False

    expected = csrf_token_for(token).encode("ascii")
    return hmac.compare_digest(csrf_header.encode("utf-8"), expected)


def set_order_access_cookie(response: Response, token: str) -> None:
    """把会话令牌放进 __Host-shop_order_access cookie。

    HttpOnly（不交给页面脚本）、Secure、SameSite=Lax、Path=/，不设 Domain：前缀 __Host-
    要求这三项，浏览器因此拒绝同一上级域名下的其他站点写入同名 cookie，防会话固定。
    Max-Age 为 30 分钟。一个浏览器的多张订单授权都挂在这一个会话下、各自到期。
    """
    if _well_formed(token) is None:
        raise ValueError("malformed order access token")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="lax",
    )

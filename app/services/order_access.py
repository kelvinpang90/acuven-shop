"""订单访问授权：签发与校验按订单的授权、设置 cookie、算出与校验 CSRF 令牌。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条：游客订单创建后给当前浏览器
一个不可猜测、30 分钟有效且仅限该单的短期凭据（范围 guest_checkout），查单通过后仅对该订单
在本浏览器保持 30 分钟授权（范围 lookup）；两者「都以服务端保存的会话实现，服务端校验所属订单、
到期时间并可撤销；以 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，不交给页面脚本」；
「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」；「一个浏览器可同时持有多张订单的
授权，各自独立到期」。第 7 条：日志不记录订单查询参数。

一个浏览器只有一个 cookie（会话令牌原文），库里只存令牌的 SHA-256 十六进制摘要；会话下每张
订单、每种范围一条授权。CSRF 令牌不入库，由令牌原文加固定用途前缀做 SHA-256 得到，
与入库的摘要不同，服务端对任何带 cookie 的请求都能重新算出。

本模块不写日志；令牌、CSRF 令牌与 cookie 值不出现在异常消息或 repr 里。
签发只 flush、不提交，由调用方在同一事务里提交。时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypeGuard

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order_access import ACCESS_SCOPES, OrderAccessGrant, OrderAccessSession

COOKIE_NAME = "__Host-shop_order_access"
GRANT_LIFETIME = timedelta(minutes=30)
COOKIE_MAX_AGE_SECONDS = int(GRANT_LIFETIME.total_seconds())

# secrets.token_urlsafe(32)：32 字节（256 位）随机数的无填充 Base64url，恰好 43 个字符。
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")

# CSRF 令牌的用途前缀：同一令牌原文按不同用途得到不同摘要，CSRF 令牌不等于入库的会话摘要。
_CSRF_PREFIX = b"acuven-shop/order-access/csrf\x00"


@dataclass(frozen=True)
class IssuedAccess:
    """签发结果。

    new_token 只在新建会话时有值，调用方须用它调用 set_order_access_cookie；沿用会话时为空，
    调用方仍应以请求带来的 cookie 值调用 set_order_access_cookie，把 cookie 的 Max-Age
    刷新为 30 分钟，否则浏览器可能在新授权到期前丢掉 cookie。
    """

    new_token: str | None = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


def _is_well_formed(cookie_value: str | None) -> TypeGuard[str]:
    return cookie_value is not None and _TOKEN_PATTERN.fullmatch(cookie_value) is not None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf_for(token: str) -> str:
    return hashlib.sha256(_CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _require_scope(scope: str) -> None:
    if scope not in ACCESS_SCOPES:
        raise ValueError(f"unknown order access scope: {scope!r}")


def _require_naive(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")


def _live_session(
    db: Session, cookie_value: str | None, now: datetime
) -> OrderAccessSession | None:
    if not _is_well_formed(cookie_value):
        return None
    token_hash = _token_hash(cookie_value)
    session = db.scalars(
        select(OrderAccessSession).where(OrderAccessSession.token_hash == token_hash)
    ).one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return None
    return session


def issue_order_access(
    db: Session,
    cookie_value: str | None,
    order_id: int,
    scope: str,
    now: datetime,
) -> IssuedAccess:
    """给当前浏览器签发对一张订单、一种范围的 30 分钟授权。

    cookie 值格式合法且对应的会话未撤销、未到期时沿用该会话（返回的 new_token 为空），
    否则新建会话与令牌。同一会话、订单与范围已有授权时把它的到期时间更新为 now 加 30 分钟、
    清空撤销时间，否则新建授权；会话到期时间更新为它与新授权到期时间中较晚者。
    只 flush、不提交：调用方在创建订单或查单通过的同一事务里提交。订单不存在时由外键在
    flush 时拒绝。
    """
    _require_scope(scope)
    _require_naive(now)
    expires_at = now + GRANT_LIFETIME

    session = _live_session(db, cookie_value, now)
    new_token: str | None = None
    if session is None:
        new_token = secrets.token_urlsafe(TOKEN_BYTES)
        session = OrderAccessSession(
            token_hash=_token_hash(new_token),
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
        )
        db.add(session)
        db.flush()
        token = new_token
    else:
        # 沿用的会话必由格式合法的 cookie 值找到。
        token = str(cookie_value)

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
    return IssuedAccess(new_token=new_token, csrf_token=_csrf_for(token), expires_at=expires_at)


def check_order_access(
    db: Session,
    cookie_value: str | None,
    order_id: int,
    scope: str,
    now: datetime,
) -> bool:
    """当前浏览器对这张订单是否有这种范围的有效授权。

    只有会话存在、未撤销、未到期，且该会话对该订单、该范围的授权存在、未撤销、未到期时
    才通过。格式不合法的 cookie 值不查库，直接不通过。guest_checkout 与 lookup 互不替代，
    一张订单的授权不能用于其他订单。

    支付、取消、确认收货与退款申请等写操作必须同时通过本函数与 check_csrf_token；
    只读的页面（结果页、查单详情）只需本函数。
    """
    _require_scope(scope)
    _require_naive(now)
    if not _is_well_formed(cookie_value):
        return False

    grant_id = db.scalars(
        select(OrderAccessGrant.id)
        .join(OrderAccessSession, OrderAccessGrant.session_id == OrderAccessSession.id)
        .where(
            OrderAccessSession.token_hash == _token_hash(cookie_value),
            OrderAccessSession.revoked_at.is_(None),
            OrderAccessSession.expires_at > now,
            OrderAccessGrant.order_id == order_id,
            OrderAccessGrant.scope == scope,
            OrderAccessGrant.revoked_at.is_(None),
            OrderAccessGrant.expires_at > now,
        )
    ).first()
    return grant_id is not None


def list_order_access(
    db: Session,
    cookie_value: str | None,
    scope: str,
    now: datetime,
) -> list[int]:
    """当前浏览器对哪些订单有这种范围的有效授权，返回订单 ID；只读。

    有效的条件与 check_order_access 相同：会话存在、未撤销、未到期，授权未撤销、未到期。
    按授权到期时间从晚到早（即最近签发的在前），同时到期按授权 ID 从大到小。
    格式不合法的 cookie 值不查库，返回空列表。列出不代替逐单校验：
    写操作仍须对请求里的订单调用 check_order_access 与 check_csrf_token。
    """
    _require_scope(scope)
    _require_naive(now)
    if not _is_well_formed(cookie_value):
        return []

    order_ids = db.scalars(
        select(OrderAccessGrant.order_id)
        .join(OrderAccessSession, OrderAccessGrant.session_id == OrderAccessSession.id)
        .where(
            OrderAccessSession.token_hash == _token_hash(cookie_value),
            OrderAccessSession.revoked_at.is_(None),
            OrderAccessSession.expires_at > now,
            OrderAccessGrant.scope == scope,
            OrderAccessGrant.revoked_at.is_(None),
            OrderAccessGrant.expires_at > now,
        )
        .order_by(OrderAccessGrant.expires_at.desc(), OrderAccessGrant.id.desc())
    )
    return list(order_ids)


def set_order_access_cookie(response: Response, token: str) -> None:
    """把会话令牌写进唯一的订单访问 cookie。

    名为 __Host-shop_order_access，带 HttpOnly、Secure、SameSite=Lax、Path=/，不设 Domain：
    __Host- 前缀要求 Secure、Path=/ 且没有 Domain，同一上级域名下的其他站点因而无法写入它，
    防会话固定。Max-Age 为 30 分钟；沿用会话再签发时也应以原值重新设置，刷新 Max-Age。
    一个浏览器的多张订单授权都挂在这一个会话下，各自到期。
    """
    if not _is_well_formed(token):
        raise ValueError("order access token is not well formed")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def csrf_token_for_cookie(cookie_value: str | None) -> str | None:
    """由 cookie 值重新算出 CSRF 令牌；cookie 值缺失或格式不合法时为空。

    不查库：会话是否有效由 check_order_access 判定。
    """
    if not _is_well_formed(cookie_value):
        return None
    return _csrf_for(cookie_value)


def check_csrf_token(cookie_value: str | None, header_value: str | None) -> bool:
    """请求头里的 CSRF 令牌是否与由 cookie 值重新算出的值一致（常量时间比较）。

    令牌缺失、不一致或 cookie 值缺失、格式不合法都不通过。本函数不查库、不判断授权：
    支付、取消、确认收货与退款申请等写操作必须同时通过 check_order_access 与本函数。
    """
    expected = csrf_token_for_cookie(cookie_value)
    if expected is None or not header_value:
        return False
    return hmac.compare_digest(
        header_value.encode("utf-8", "surrogatepass"),
        expected.encode("ascii"),
    )

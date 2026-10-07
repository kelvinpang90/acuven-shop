"""管理员登录、退出与当前会话：POST /api/admin/login、POST /api/admin/logout、
GET /api/admin/session，以及之后所有后台接口共用的会话依赖 require_admin。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条：「管理员登录按来源与账号
组合限流，连续失败后短时锁定并告警；锁定解除不绕过密码校验。单一管理员也须服务端授权与会话
到期」，第 5 条「后台单一管理员也须认证，不把权限检查留给前端」，第 6 条（应用日志不记录
密码），以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员登录决定（含 2026-10-05 补充）
与 0.37 记录的 Kelvin 2026-10-07 决定（登录名为邮箱，字段名仍叫 username；邮箱不写进日志与
错误响应，锁定计数的 Redis 键仍是摘要）。规则函数见 app/services/admin_auth.py。

登录的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有 username（1 到 254 个字符）与 password（1 到 256 个字符）两个字符串，
   否则 422。这些回答不计失败、不碰 Redis、不查账号。
2. 取 Redis 客户端（未配置即 503）与访客来源（SHOP-TASK-026 的 client_source）。
3. login_locked 为真即 429 login_locked：不查账号、不校验密码、不计失败。
4. 按规范化用户名查账号，校验密码；账号不存在时以假哈希照样校验一次。
5. 不通过：record_login_failure 计一次失败；账号存在时写 admin_login_failed，刚锁定时再写
   admin_login_locked，提交后 401 login_failed。用户名不存在与密码错误的状态码、响应体与
   响应头完全相同；账号存在时多一次审计提交的耗时差是 Kelvin 2026-10-05 接受的用户名计时
   侧信道，不另做时序对齐。
6. 通过：同一事务里签发后台会话、写 admin_login_succeeded 并提交，设置 cookie，204。
Redis 不可用时 503 service_unavailable：发生在查锁定时不查账号，发生在计失败时也是同一个
503 而不是 401。

错误体为 {"detail": "<错误码>"}：401 login_failed、admin_session_required，403 csrf_failed，
429 login_locked，503 service_unavailable；413、415、422 与现有接口相同（422 每条错误只给
位置、类型与固定消息，不回显请求内容）。三个接口的处理函数与依赖产生的全部响应（含错误）
都带 Cache-Control: no-store，由 SHOP-TASK-027 的路由类统一加上。
本模块不写日志；错误响应不含用户名、密码、令牌、来源地址或内部细节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.models import AdminAccount, AdminSession
from app.services.admin_auth import (
    ADMIN_LOGIN_FAILED,
    ADMIN_LOGIN_LOCKED,
    ADMIN_LOGIN_SUCCEEDED,
    ADMIN_LOGOUT,
    SESSION_LIFETIME,
    check_admin_session,
    check_csrf_token,
    csrf_token_for_cookie,
    issue_admin_session,
    login_locked,
    normalize_username,
    record_audit,
    record_login_failure,
    revoke_admin_session,
)
from app.services.pw_hash import DUMMY_PASSWORD_HASH, verify_password
from app.services.rate_limit import client_source, get_redis_client

router = APIRouter(prefix="/api/admin", tags=["admin"], route_class=_NoStoreRoute)

# __Host- 前缀要求 Secure、Path=/ 且没有 Domain，同一上级域名下的其他站点无法写入它。
COOKIE_NAME = "__Host-shop_admin_session"
COOKIE_MAX_AGE_SECONDS = int(SESSION_LIFETIME.total_seconds())
CSRF_HEADER = "X-CSRF-Token"
_NO_STORE = {"Cache-Control": "no-store"}

# 邮箱总长上限，与账号表的列长相同（Kelvin 2026-10-07）；这里不校验邮箱格式。
USERNAME_MAX_CHARS = 254
PASSWORD_MAX_CHARS = 256


class LoginIn(BaseModel):
    """只有用户名与密码两个字符串。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=1, max_length=USERNAME_MAX_CHARS)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_CHARS, repr=False)


class AdminSessionOut(BaseModel):
    username: str
    # 带 UTC 时区，格式与支付接口的时间字段相同。
    expires_at: datetime
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交后台写操作。
    csrf_token: str


@dataclass(frozen=True)
class AdminContext:
    """require_admin 取到的当前后台会话：会话记录（含到期时间）与所属管理员。"""

    session: AdminSession = field(repr=False)
    account: AdminAccount = field(repr=False)
    cookie_value: str = field(repr=False)

    @property
    def expires_at(self) -> datetime:
        return self.session.expires_at


async def _login_input(request: Request) -> LoginIn:
    raw = await _json_body(request)
    try:
        return LoginIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


# 请求体依赖写在 Redis 与会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答，
# 请求体不合格时也不碰 Redis、不查账号。
LoginDep = Annotated[LoginIn, Depends(_login_input)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]
SessionDep = Annotated[Session, Depends(get_session)]


def require_admin(request: Request, db: SessionDep) -> AdminContext:
    """FastAPI 依赖：按 cookie 取当前有效的后台会话与所属管理员。

    之后所有后台接口都必须经它授权；写操作还须用 require_admin_csrf 校验 X-CSRF-Token
    请求头。没有有效会话时 401 {"detail": "admin_session_required"}，不区分会话不存在、
    已撤销或已到期。只读，不延长会话。
    """
    cookie_value = request.cookies.get(COOKIE_NAME)
    session = check_admin_session(db, cookie_value, _utcnow())
    account = None if session is None else db.get(AdminAccount, session.admin_account_id)
    if session is None or account is None or cookie_value is None:
        raise HTTPException(status_code=401, detail="admin_session_required", headers=_NO_STORE)
    return AdminContext(session=session, account=account, cookie_value=cookie_value)


AdminDep = Annotated[AdminContext, Depends(require_admin)]


def require_admin_csrf(request: Request, admin: AdminContext) -> None:
    """后台写操作的 CSRF 校验：X-CSRF-Token 恰好一个且与由 cookie 算出的值一致，否则 403。"""
    values = request.headers.getlist(CSRF_HEADER)
    header_value = values[0] if len(values) == 1 else None
    if not check_csrf_token(admin.cookie_value, header_value):
        raise HTTPException(status_code=403, detail="csrf_failed", headers=_NO_STORE)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="strict",
    )


@router.post("/login", status_code=204, response_class=Response)
def login(
    body: LoginDep,
    client: RedisDep,
    session: SessionDep,
    request: Request,
) -> Response:
    """登录通过 204，响应体为空，设置后台会话 cookie。"""
    source = client_source(request)
    # Redis 不可用时抛 RateLimitUnavailable，由路由类回答 503，此时还没有查账号。
    if login_locked(client, source, body.username):
        raise HTTPException(status_code=429, detail="login_locked")

    now = _utcnow()
    account = session.scalars(
        select(AdminAccount).where(AdminAccount.username == normalize_username(body.username))
    ).one_or_none()
    encoded = DUMMY_PASSWORD_HASH if account is None else account.password_hash
    # 账号不存在时也用假哈希照样校验，避免短路跳过哈希计算。
    password_ok = verify_password(body.password, encoded)
    if account is None or not password_ok:
        # 先计失败：Redis 不可用时回答 503，不写审计。
        just_locked = record_login_failure(client, source, body.username)
        if account is not None:
            record_audit(session, ADMIN_LOGIN_FAILED, account.id, now)
            if just_locked:
                record_audit(session, ADMIN_LOGIN_LOCKED, account.id, now)
            # 会话依赖在请求结束时回滚未提交的改动，审计必须在返回前提交。
            session.commit()
        raise HTTPException(status_code=401, detail="login_failed")

    issued = issue_admin_session(session, account.id, now)
    record_audit(session, ADMIN_LOGIN_SUCCEEDED, account.id, now)
    session.commit()
    response = Response(status_code=204)
    _set_session_cookie(response, issued.token)
    return response


@router.get("/session", response_model=AdminSessionOut)
def current_session(admin: AdminDep) -> AdminSessionOut:
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return AdminSessionOut(
        username=admin.account.username,
        expires_at=admin.expires_at.replace(tzinfo=UTC),
        csrf_token=csrf_token,
    )


@router.post("/logout", status_code=204, response_class=Response)
def logout(admin: AdminDep, session: SessionDep, request: Request) -> Response:
    """撤销当前会话、写 admin_logout 并提交，清除 cookie，204。CSRF 不通过时不撤销。"""
    require_admin_csrf(request, admin)
    now = _utcnow()
    revoke_admin_session(session, admin.session, now)
    record_audit(session, ADMIN_LOGOUT, admin.session.admin_account_id, now)
    session.commit()
    response = Response(status_code=204)
    _clear_session_cookie(response)
    return response

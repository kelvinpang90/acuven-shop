"""会员重设密码：POST /api/member/password-reset/verify 与 POST /api/member/password-reset。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（已有密码的重设再次短信
验证；密码至少 8 位；短信验证开关关闭期间密码重设暂停）与「边界与原则」第 4 条（提交验证码
时按当时读取的开关值判定，开关在流程途中被关闭后不重设密码）、docs/UX.md 0.10 P12 忘记密码
（验证通过后号码已注册才显示新密码输入，未注册不创建账号；重设完成后去登录），以及
docs/HANDOFF.md 0.44 记录的 Kelvin 2026-10-10 决定（一次性重设凭据存于 Redis，10 分钟有效、
用一次即删，经 HttpOnly、Secure、SameSite=Lax 的 cookie 只交给本浏览器，提交新密码另须 CSRF
令牌；Redis 不可用时重设暂停）。核验函数见 app/services/sms_verification.py（SHOP-TASK-069），
凭据与重设规则见 app/services/member_pw_reset.py（SHOP-TASK-078）。

验证的处理顺序，每一步不通过即停止：
1. 请求体逐块读，超过 4 KB 即 413，先于一切；不是 JSON 415；请求体只有 phone、phone_region
   与 code（1 到 16 个字符的字符串），都必填、严格类型，多出字段 422；号码规范化与
   POST /api/member/sms-login 相同，不成立 422（类型 phone_invalid）。
2. 取 Redis 客户端（只建客户端、不连 Redis；未配置时由路由类回答 503 service_unavailable）。
3. 当次读取的短信验证开关关闭：403 sms_disabled，不 PING、不核验。
4. PING 一次；出错由路由类回答 503 service_unavailable，不核验、不消耗验证码。
5. 以用途 reset_password 调用 verify_code。不是 approved 时先提交再回答，状态码与错误码同
   sms-login（wrong_code 422 code_wrong，expired 与 no_pending 422 code_expired，unavailable
   503 sms_unavailable，sms_disabled 403 sms_disabled）。
6. approved 时按号码找 active 会员并提交：找不到 200 {"registered": false}，不建会员、不签发
   凭据、不设 cookie；找到则签发凭据、设置重设 cookie，200 {"registered": true,
   "csrf_token": …}。签发时 Redis 出错由路由类回答 503 service_unavailable、不设 cookie
   （验证记录已为 approved，访客须重新发送验证码）。
   核验或本流程出现数据库错误时回滚，503 sms_unavailable。不签发会员会话、不认领订单。

提交新密码的处理顺序，每一步不通过即停止：
1. 413 → 415 → 422（请求体只有 password，1 到 256 个字符的字符串）。
2. 当次读取的短信验证开关关闭：403 sms_disabled（不取用凭据）。
3. 没有重设 cookie 或其格式不对：401 reset_expired。
4. X-CSRF-Token 恰好一个且与由凭据算出的值一致，否则 403 csrf_failed（不取用凭据）。
5. 不满足 password_length_ok：422 password_too_short（不取用凭据，可改后重交）。
6. 取 Redis 客户端（未配置时 503 service_unavailable）；一次性取用凭据，已过期或已被取用时
   401 reset_expired；Redis 出错 503 service_unavailable。
7. 调用 reset_password：返回 False（会员已注销）401 reset_expired；否则提交，清除重设
   cookie，204 且响应体为空。取用之后数据库出错时回滚，503 service_unavailable（凭据已删，
   访客须重新验证）。重设撤销该会员的全部会话；不签发会员会话。
2 到 5 写在依赖 _reset_request 里，Redis 客户端依赖排在它之后：FastAPI 按声明顺序解析依赖。

两个接口都不需要也不读会员会话。处理函数与依赖产生的全部响应（含错误）都带
Cache-Control: no-store，由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由
框架在路由之外回答，不在此列。422 沿用 app/api/pay.py 的 _body_errors，不回显请求内容。
本模块不写日志；响应体与错误不含号码原文、验证码、凭据、密码或会员 ID。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.member_auth import _ERRORS, CODE_MAX_CHARS, CSRF_HEADER, _rollback
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.models.member import MEMBER_ACTIVE, PURPOSE_RESET_PASSWORD, Member
from app.services.member_auth import password_length_ok
from app.services.member_pw_reset import (
    RESET_TOKEN_TTL_SECONDS,
    check_reset_csrf,
    consume_pw_reset,
    csrf_token_for_reset,
    issue_pw_reset,
    reset_password,
)
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.rate_limit import RateLimitUnavailable, get_redis_client
from app.services.site_settings import is_sms_verification_enabled
from app.services.sms_provider import SmsProvider, get_sms_provider
from app.services.sms_verification import SmsVerificationError, VerifyStatus, verify_code

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)

# __Host- 前缀要求 Secure、Path=/ 且没有 Domain，同一上级域名下的其他站点无法写入它。
COOKIE_NAME = "__Host-shop_member_pw_reset"
COOKIE_MAX_AGE_SECONDS = RESET_TOKEN_TTL_SECONDS

# 字段都很短，4 KB 与 POST /api/member/sms-login 相同。
MAX_BODY_BYTES = 4 * 1024
PASSWORD_MAX_CHARS = 256


class ResetVerifyIn(BaseModel):
    """手机号原文与地区代码、验证码；用途固定为 reset_password，不由请求给出。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    phone: str
    phone_region: str
    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS, repr=False)


class ResetPasswordIn(BaseModel):
    """只有新密码。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    password: str = Field(min_length=1, max_length=PASSWORD_MAX_CHARS, repr=False)


@dataclass(frozen=True)
class ResetVerifyInput:
    """规范化后的号码与验证码；repr 不带出二者。"""

    phone_e164: str = field(repr=False)
    code: str = field(repr=False)


@dataclass(frozen=True)
class ResetRequest:
    """通过开关、cookie、CSRF 与长度检查的提交：凭据原文与新密码；repr 不带出二者。"""

    token: str = field(repr=False)
    password: str = field(repr=False)


async def _json_body(request: Request) -> bytes:
    """逐块读请求体，超过上限就停下返回 413，不看 Content-Length 是否如实；
    读完之后才判断 Content-Type。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    if not _is_json(request.headers.get("content-type")):
        raise HTTPException(status_code=415, detail="request body must be JSON")
    return b"".join(chunks)


async def _verify_input(request: Request) -> ResetVerifyInput:
    raw = await _json_body(request)
    try:
        body = ResetVerifyIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    try:
        phone = normalize_phone(body.phone, body.phone_region).e164
    except InvalidPhoneNumber:
        error = _error(("body", "phone"), "phone_invalid", "invalid phone number")
        raise RequestValidationError([error]) from None
    return ResetVerifyInput(phone, body.code)


async def _reset_body(request: Request) -> ResetPasswordIn:
    raw = await _json_body(request)
    try:
        return ResetPasswordIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


# 请求体依赖写在 Redis、会话与其他依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
VerifyInputDep = Annotated[ResetVerifyInput, Depends(_verify_input)]
ResetBodyDep = Annotated[ResetPasswordIn, Depends(_reset_body)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]
SessionDep = Annotated[Session, Depends(get_session)]
ProviderDep = Annotated[SmsProvider, Depends(get_sms_provider)]


def _sms_disabled() -> HTTPException:
    return HTTPException(status_code=403, detail="sms_disabled")


def _reset_expired() -> HTTPException:
    return HTTPException(status_code=401, detail="reset_expired")


def _service_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="service_unavailable")


def _reset_request(body: ResetBodyDep, session: SessionDep, request: Request) -> ResetRequest:
    """提交新密码在取 Redis 客户端之前的检查：开关 → cookie → CSRF → 长度。都不取用凭据。"""
    try:
        enabled = is_sms_verification_enabled(session)
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None
    if not enabled:
        raise _sms_disabled()
    token = request.cookies.get(COOKIE_NAME)
    if token is None or csrf_token_for_reset(token) is None:
        raise _reset_expired()
    values = request.headers.getlist(CSRF_HEADER)
    header_value = values[0] if len(values) == 1 else None
    if not check_reset_csrf(token, header_value):
        raise HTTPException(status_code=403, detail="csrf_failed")
    if not password_length_ok(body.password):
        raise HTTPException(status_code=422, detail="password_too_short")
    return ResetRequest(token=token, password=body.password)


# 排在 RedisDep 之前：检查不通过时不取 Redis 客户端。
ResetRequestDep = Annotated[ResetRequest, Depends(_reset_request)]


def _set_reset_cookie(response: Response, token: str) -> None:
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


def _clear_reset_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _ping(client: redis.Redis) -> None:
    """核验之前确认 Redis 可用：之后签发凭据要用它，不可用时不消耗验证码。"""
    try:
        client.ping()
    except RedisError:
        # 异常链里可能带主机与端口，from None 不带出去。
        raise RateLimitUnavailable() from None


@router.post("/password-reset/verify")
def verify(
    body: VerifyInputDep,
    client: RedisDep,
    session: SessionDep,
    provider: ProviderDep,
) -> Response:
    """号码已注册 200 {"registered": true, "csrf_token": …} 并设置重设 cookie；
    未注册 200 {"registered": false}，不设 cookie。不返回号码与会员 ID。
    """
    try:
        enabled = is_sms_verification_enabled(session)
    except SQLAlchemyError:
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_unavailable") from None
    if not enabled:
        raise _sms_disabled()
    _ping(client)

    try:
        result = verify_code(
            session, provider, body.phone_e164, PURPOSE_RESET_PASSWORD, body.code, _utcnow()
        )
        member_id: int | None = None
        if result.status is VerifyStatus.APPROVED:
            member_id = session.scalar(
                select(Member.id).where(
                    Member.phone == body.phone_e164, Member.status == MEMBER_ACTIVE
                )
            )
        # 不是 approved 时也提交：核验函数可能已把记录改为 rejected。
        session.commit()
    except (SmsVerificationError, SQLAlchemyError):
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_unavailable") from None

    if result.status is not VerifyStatus.APPROVED:
        status_code, code = _ERRORS.get(result.status, (503, "sms_unavailable"))
        raise HTTPException(status_code=status_code, detail=code)
    if member_id is None:
        # UX P12：号码未注册时不创建账号。
        return JSONResponse({"registered": False})
    # Redis 出错时抛 RateLimitUnavailable，由路由类回答 503，不设 cookie。
    issued = issue_pw_reset(client, member_id)
    response = JSONResponse({"registered": True, "csrf_token": issued.csrf_token})
    _set_reset_cookie(response, issued.token)
    return response


@router.post("/password-reset", status_code=204, response_class=Response)
def submit(reset: ResetRequestDep, client: RedisDep, session: SessionDep) -> Response:
    """取用凭据并重设密码，成功 204、响应体为空并清除重设 cookie；不签发会员会话。"""
    # Redis 不可用时抛 RateLimitUnavailable，由路由类回答 503。
    member_id = consume_pw_reset(client, reset.token)
    if member_id is None:
        raise _reset_expired()
    try:
        changed = reset_password(session, member_id, reset.password, _utcnow())
        if not changed:
            _rollback(session)
            raise _reset_expired()
        session.commit()
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None
    response = Response(status_code=204)
    _clear_reset_cookie(response)
    return response

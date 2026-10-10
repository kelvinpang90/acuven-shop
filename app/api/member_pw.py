"""会员密码登录与首次设置密码：POST /api/member/password-login、POST /api/member/password。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：会员可用手机号加密码
登录；未设密码的账号只能短信登录，密码登录对其与对错误密码返回相同的通用失败；
首次设置密码在已登录会话内进行；密码至少 8 位、不强制复杂度；短信验证开关关闭期间
已设密码的会员仍可密码登录，已登录会员首次设置密码不受影响。以及
docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定：同一号码加来源 15 分钟内密码登录
失败 5 次即锁 15 分钟，失败一律通用提示。
规则函数见 app/services/member_auth.py（SHOP-TASK-071）；会话 cookie、
require_member 与 require_member_csrf 取自 app/api/member_auth.py（SHOP-TASK-076）。
不做重设密码与注销。

密码登录的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 4 KB 即 413，先于一切校验；不是 JSON 415。
2. 请求体只有 phone、phone_region 与 password（1 到 256 个字符的字符串），
   都必填、严格类型，多出字段 422；手机号规范化与 POST /api/member/sms-login
   相同，不成立 422（类型 phone_invalid）。
3. 取 Redis 客户端与访客来源（client_source），调用 password_login：
   返回会员时签发会话、提交、设置 cookie，204 且响应体为空；
   返回 None（锁定、号码未注册、未设密码、密码错误与已注销）一律
   401 {"detail": "login_failed"}，五者的状态码、响应体与响应头完全相同，
   不设置 cookie。Redis 未配置或出错时由路由类回答 503 service_unavailable，
   不放行登录；数据库出错时回滚，503 service_unavailable，不设置 cookie。
   不认领游客订单，不读短信验证开关。

首次设置密码的处理顺序，每一步不通过即停止：
1. 413 与 415 同上；
2. require_member（401 member_session_required）；
3. require_member_csrf（403 csrf_failed，此时不改密码）；
4. 请求体只有 password（1 到 256 个字符的字符串），多出字段 422；
5. 不满足 password_length_ok 时 422 {"detail": "password_too_short"}；
6. 会员已设密码时 409 {"detail": "password_already_set"}；否则算哈希，
   以条件更新（仅当该会员仍为 active 且密码哈希仍为空）写入并提交，
   204 且响应体为空。条件不成立时回滚：会员仍为 active（并发的另一次设置
   先完成）409 password_already_set，已不是 active 则 401
   member_session_required；原哈希都不变。
不撤销会话，不读短信验证开关。数据库出错时回滚，503 service_unavailable。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，
由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由
之外回答，不在此列。422 每条错误只给位置、类型与固定消息，不回显请求内容
（同 app/api/pay.py 的 _body_errors）。本模块不写日志；响应体与错误不含
号码原文、密码、令牌、cookie 值或来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.member_auth import (
    MemberDep,
    _rollback,
    _set_session_cookie,
    require_member_csrf,
)
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.models.member import MEMBER_ACTIVE, Member
from app.services.member_auth import issue_member_session, password_length_ok, password_login
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.pw_hash import hash_password
from app.services.rate_limit import client_source, get_redis_client

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)

# 字段都很短，4 KB 与 POST /api/member/sms-login 相同。
MAX_BODY_BYTES = 4 * 1024
PASSWORD_MAX_CHARS = 256


class PasswordLoginIn(BaseModel):
    """手机号原文与地区代码、密码。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    phone: str
    phone_region: str
    # 登录时不按长度规则预先拒绝：长度不足的密码照常校验、照常不通过。
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_CHARS, repr=False)


class SetPasswordIn(BaseModel):
    """只有新密码。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    password: str = Field(min_length=1, max_length=PASSWORD_MAX_CHARS, repr=False)


@dataclass(frozen=True)
class PasswordLoginInput:
    """规范化后的号码与密码；repr 不带出二者。"""

    phone_e164: str = field(repr=False)
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


async def _password_login_input(request: Request) -> PasswordLoginInput:
    raw = await _json_body(request)
    try:
        body = PasswordLoginIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    try:
        phone = normalize_phone(body.phone, body.phone_region).e164
    except InvalidPhoneNumber:
        error = _error(("body", "phone"), "phone_invalid", "invalid phone number")
        raise RequestValidationError([error]) from None
    return PasswordLoginInput(phone, body.password)


async def _set_password_body(request: Request) -> bytes:
    """设置密码只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


# 请求体依赖写在 Redis、会话与会员依赖之前：FastAPI 按声明顺序解析依赖，
# 413 先于其他一切回答，请求体不合格时也不碰 Redis、不查会员。
PasswordLoginDep = Annotated[PasswordLoginInput, Depends(_password_login_input)]
SetPasswordBodyDep = Annotated[bytes, Depends(_set_password_body)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]
SessionDep = Annotated[Session, Depends(get_session)]


def _login_failed() -> HTTPException:
    # 五种失败共用这一个回答，不带任何区分它们的头或内容。
    return HTTPException(status_code=401, detail="login_failed")


def _service_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="service_unavailable")


def _password_already_set() -> HTTPException:
    return HTTPException(status_code=409, detail="password_already_set")


@router.post("/password-login", status_code=204, response_class=Response)
def login(
    body: PasswordLoginDep,
    client: RedisDep,
    session: SessionDep,
    request: Request,
) -> Response:
    """登录通过 204，响应体为空，设置会员会话 cookie；失败一律 401 login_failed。"""
    source = client_source(request)
    try:
        # Redis 不可用时抛 RateLimitUnavailable，由路由类回答 503。
        member = password_login(session, client, source, body.phone_e164, body.password)
        if member is None:
            raise _login_failed()
        issued = issue_member_session(session, member, _utcnow())
        session.commit()
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None
    response = Response(status_code=204)
    _set_session_cookie(response, issued.token)
    return response


@router.post("/password", status_code=204, response_class=Response)
def set_password(
    raw: SetPasswordBodyDep,
    member: MemberDep,
    session: SessionDep,
    request: Request,
) -> Response:
    """CSRF → 请求体 → 长度规则 → 条件写入。成功 204，响应体为空；不撤销会话。"""
    require_member_csrf(request, member)
    try:
        body = SetPasswordIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    if not password_length_ok(body.password):
        raise HTTPException(status_code=422, detail="password_too_short")
    # 回滚后会话里的 ORM 对象随之过期：会员 ID 在此之前取出。
    member_id = member.member.id
    if member.member.password_hash is not None:
        raise _password_already_set()

    encoded = hash_password(body.password)
    try:
        result = session.execute(
            update(Member)
            .where(
                Member.id == member_id,
                Member.status == MEMBER_ACTIVE,
                Member.password_hash.is_(None),
            )
            .values(password_hash=encoded)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            session.commit()
            return Response(status_code=204)
        # 条件不成立：回滚后在新的事务里读，看到的是另一方已提交的结果。
        session.rollback()
        status = session.scalar(select(Member.status).where(Member.id == member_id))
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None
    if status != MEMBER_ACTIVE:
        raise HTTPException(status_code=401, detail="member_session_required")
    raise _password_already_set()

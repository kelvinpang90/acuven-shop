"""会员注销的短信验证：POST /api/member/delete/send-code 与 POST /api/member/delete/verify。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（账号注销须先以短信验证码
确认）、「边界与原则」第 4 条（发送短信与提交验证码时按当时读取的开关值判定，开关在流程途中
被关闭后不能以短信完成注销确认）与「失败、并发与重试」，docs/UX.md 0.10 P13（V1 用途「注销
确认」，号码固定为本账号；验证通过后才出现确认注销），以及 docs/HANDOFF.md 0.45 记录的
Kelvin 2026-10-10 决定（1）：注销验证码核验通过后，服务端记一条绑定当前会员会话的一次性批准，
10 分钟有效、使用一次即删除，凭登录会话与 CSRF 令牌、不另发 cookie。
发送与核验见 app/services/sms_verification.py（SHOP-TASK-069），批准见
app/services/member_deletion.py（SHOP-TASK-080），会话依赖见 app/api/member_auth.py
（SHOP-TASK-076）。确认注销留给 SHOP-TASK-082；本模块不注销、不改会员、不撤销会话。

两个接口的检查顺序同 app/api/admin_store_design.py 的保存，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 4 KB 即 413，先于一切；不是 JSON 415；
2. require_member（401 member_session_required）；
3. require_member_csrf（403 csrf_failed）；
4. 请求体结构（422，严格类型，多出字段 422，含 phone）：发送只有 captcha_token（1 到 2048 个
   字符的字符串），核验只有 code（1 到 16 个字符的字符串）。号码一律取当前会员的手机号。

发送：以用途 delete_account 调用 send_verification，Redis 客户端取自 app/api/sms.py 的
get_sms_redis_client（未配置时为 None，由发送函数按停发处理）。结果与 POST /api/sms/send 相同：
sent 为 200 {"status": "sent"}，其他照 app/api/sms.py 的 _ERRORS；SmsVerificationError 与
SmsBudgetError 时回滚，503 sms_suspended。

核验，接着上面的第 4 步：
5. 当次从数据库读取的短信验证开关关闭（含无设置行）：403 sms_disabled，不取 Redis 客户端、
   不 PING、不核验。
6. 取 Redis 客户端（get_redis_client；未配置时由路由类回答 503 service_unavailable）并 PING
   一次，出错同样 503 service_unavailable；都不核验、不消耗验证码。
7. 以用途 delete_account 调用 verify_code。不是 approved 时先提交再回答，状态码与错误码同
   sms-login（wrong_code 422 code_wrong，expired 与 no_pending 422 code_expired，unavailable
   503 sms_unavailable，sms_disabled 403 sms_disabled）。approved 时提交后为当前会员会话签发
   注销批准，204 且响应体为空；签发时 Redis 出错由路由类回答 503 service_unavailable（验证记录
   已为 approved，须重新发送验证码）。核验或提交时数据库出错回滚，503 sms_unavailable。
第 4 步写在依赖 _verify_input 里，第 5 步写在 _verify_enabled 里，Redis 客户端依赖声明在它们
之后：FastAPI 按声明顺序解析依赖。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 沿用 app/api/pay.py 的 _body_errors，不回显请求内容。本模块不写日志；响应体与错误不含
号码原文、验证码、令牌、会话 ID 或会员 ID。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import sms
from app.api.member_auth import (
    _ERRORS,
    CODE_MAX_CHARS,
    MemberContext,
    MemberDep,
    _rollback,
    require_member_csrf,
)
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.models.member import PURPOSE_DELETE_ACCOUNT
from app.services.captcha import MAX_TOKEN_LENGTH, CaptchaVerifier, get_captcha_verifier
from app.services.member_deletion import issue_delete_approval
from app.services.rate_limit import RateLimitUnavailable, client_source, get_redis_client
from app.services.site_settings import is_sms_verification_enabled
from app.services.sms_budget import SmsBudgetError
from app.services.sms_provider import SmsProvider, get_sms_provider
from app.services.sms_verification import (
    SendOutcome,
    SmsVerificationError,
    VerifyStatus,
    send_verification,
    verify_code,
)

router = APIRouter(prefix="/api/member/delete", tags=["member"], route_class=_NoStoreRoute)

# 字段都很短，4 KB 与 POST /api/sms/send 相同。
MAX_BODY_BYTES = 4 * 1024


class DeleteSendIn(BaseModel):
    """只有人机挑战令牌；号码取当前会员的，用途固定为 delete_account。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    captcha_token: str = Field(min_length=1, max_length=MAX_TOKEN_LENGTH, repr=False)


class DeleteVerifyIn(BaseModel):
    """只有验证码；号码取当前会员的，用途固定为 delete_account。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS, repr=False)


class DeleteSendOut(BaseModel):
    status: Literal["sent"]


@dataclass(frozen=True)
class DeleteRequest:
    """通过会话、CSRF 与结构检查的请求：会员号码、会话 ID、会员 ID 与请求体里的值。

    repr 不带出号码、令牌与验证码。ID 在这里取出：提交后会话里的 ORM 对象随之过期。
    """

    phone_e164: str = field(repr=False)
    session_id: int
    member_id: int
    value: str = field(repr=False)


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


async def _body(request: Request) -> bytes:
    """两个接口只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


# 请求体依赖写在会员依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
BodyDep = Annotated[bytes, Depends(_body)]


def _request(member: MemberContext, value: str) -> DeleteRequest:
    phone = member.member.phone
    if phone is None:
        # require_member 通过时会员必然 active 且有号码；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="member_session_required")
    return DeleteRequest(phone, member.session.id, member.member.id, value)


def _send_input(raw: BodyDep, member: MemberDep, request: Request) -> DeleteRequest:
    """会话已由 require_member 判定；这里 CSRF → 请求体结构。"""
    require_member_csrf(request, member)
    try:
        body = DeleteSendIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    return _request(member, body.captcha_token)


def _verify_input(raw: BodyDep, member: MemberDep, request: Request) -> DeleteRequest:
    """会话已由 require_member 判定；这里 CSRF → 请求体结构。"""
    require_member_csrf(request, member)
    try:
        body = DeleteVerifyIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    return _request(member, body.code)


SendInputDep = Annotated[DeleteRequest, Depends(_send_input)]
VerifyInputDep = Annotated[DeleteRequest, Depends(_verify_input)]
SessionDep = Annotated[Session, Depends(get_session)]
# 不可用时为 None，交给 send_verification 按停发处理（同 POST /api/sms/send）。
SmsRedisDep = Annotated[redis.Redis | None, Depends(sms.get_sms_redis_client)]
ProviderDep = Annotated[SmsProvider, Depends(get_sms_provider)]
CaptchaDep = Annotated[CaptchaVerifier, Depends(get_captcha_verifier)]


def _verify_enabled(_checked: VerifyInputDep, session: SessionDep) -> None:
    """核验在取 Redis 客户端之前的检查：当次读取的短信验证开关关闭时 403，不触及 Redis。

    以请求体依赖为入参，保证开关只在会话、CSRF 与结构都通过之后才读。
    """
    try:
        enabled = is_sms_verification_enabled(session)
    except SQLAlchemyError:
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_unavailable") from None
    if not enabled:
        raise HTTPException(status_code=403, detail="sms_disabled")


# 排在 RedisDep 之前：开关关闭时不取 Redis 客户端，Redis 未配置也回答 403 而不是 503。
VerifyEnabledDep = Annotated[None, Depends(_verify_enabled)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]


def _ping(client: redis.Redis) -> None:
    """核验之前确认 Redis 可用：之后签发批准要用它，不可用时不消耗验证码。"""
    try:
        client.ping()
    except RedisError:
        # 异常链里可能带主机与端口，from None 不带出去。
        raise RateLimitUnavailable() from None


@router.post("/send-code", response_model=DeleteSendOut)
def send_code(
    body: SendInputDep,
    session: SessionDep,
    client: SmsRedisDep,
    provider: ProviderDep,
    captcha: CaptchaDep,
    request: Request,
) -> DeleteSendOut:
    """向当前会员的手机号发送注销验证码；结果的回答同 POST /api/sms/send。"""
    try:
        outcome = send_verification(
            session,
            request.app.state.settings,
            client,
            provider,
            captcha,
            body.phone_e164,
            PURPOSE_DELETE_ACCOUNT,
            body.value,
            client_source(request),
            _utcnow(),
        )
    except (SmsVerificationError, SmsBudgetError):
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_suspended") from None
    if outcome is SendOutcome.SENT:
        return DeleteSendOut(status="sent")
    status_code, code = sms._ERRORS[outcome]
    raise HTTPException(status_code=status_code, detail=code)


@router.post("/verify", status_code=204, response_class=Response)
def verify(
    body: VerifyInputDep,
    _enabled: VerifyEnabledDep,
    client: RedisDep,
    session: SessionDep,
    provider: ProviderDep,
) -> Response:
    """核验当前会员号码的注销验证码；通过时为当前会话签发注销批准，204 且响应体为空。"""
    _ping(client)

    try:
        result = verify_code(
            session, provider, body.phone_e164, PURPOSE_DELETE_ACCOUNT, body.value, _utcnow()
        )
        # 不是 approved 时也提交：核验函数可能已把记录改为 rejected。
        session.commit()
    except (SmsVerificationError, SQLAlchemyError):
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_unavailable") from None

    if result.status is not VerifyStatus.APPROVED:
        status_code, code = _ERRORS.get(result.status, (503, "sms_unavailable"))
        raise HTTPException(status_code=status_code, detail=code)
    # Redis 出错时抛 RateLimitUnavailable，由路由类回答 503。
    issue_delete_approval(client, body.session_id, body.member_id)
    return Response(status_code=204)

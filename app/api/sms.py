"""发送短信验证码的公开接口：只有 POST /api/sms/send。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 1、3 条与「失败、并发与重试」，
以及 docs/HANDOFF.md 0.41（短信验证与个人资料）。规则见 app/services/sms_verification.py 的
send_verification（SHOP-TASK-069），提交也由它负责。核验不单独开接口，由之后的登录、注册与
重设任务在各自接口里调用 verify_code。

不需要会话与 CSRF（公开接口，由人机挑战与限流保护），不读会员会话与订单访问 cookie。
用途只有结账验证、注册、短信登录与密码重设；注销确认由会员注销任务接上。登录与重设对未注册
号码照常发送，本接口不查会员表，响应不暴露号码是否已注册。

处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 4 KB 即 413，先于一切校验；不是 JSON 415。
2. 请求体只有 phone、phone_region、purpose 与 captcha_token，都必填、严格类型，多出字段 422；
   手机号按 phone_region 规范化（与游客下单相同），不成立 422（类型 phone_invalid）。
   422 每条错误只给位置、类型与固定消息，不回显请求内容。
3. 调用 send_verification，按结果回答：

   sent             200 {"status": "sent"}
   sms_disabled     403 {"detail": "sms_disabled"}
   not_whitelisted  422 {"detail": "not_whitelisted"}
   captcha_failed   403 {"detail": "captcha_failed"}
   rate_limited     429 {"detail": "rate_limited"}
   undeliverable    422 {"detail": "sms_undeliverable"}
   suspended        503 {"detail": "sms_suspended"}

   处理中 MySQL 出错（SmsVerificationError）或每日预算记录不成立（SmsBudgetError）时回滚会话，
   同样 503 sms_suspended。数据库未配置时照 get_session 的 503 回答。

服务商与人机挑战适配器经 SHOP-TASK-067 的依赖函数注入。Redis 客户端由本模块的
get_sms_redis_client 取得：未配置或创建失败时为 None，交给 send_verification 在开关、白名单与
人机挑战之后按停发处理（不放行发送）。

处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由 SHOP-TASK-027 的路由类
统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。不写日志；错误响应不含
手机号、令牌、来源或内部细节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.services.captcha import MAX_TOKEN_LENGTH, CaptchaVerifier, get_captcha_verifier
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.rate_limit import RateLimitUnavailable, client_source, get_redis_client
from app.services.sms_budget import SmsBudgetError
from app.services.sms_provider import SmsProvider, get_sms_provider
from app.services.sms_verification import SendOutcome, SmsVerificationError, send_verification

router = APIRouter(prefix="/api/sms", tags=["sms"], route_class=_NoStoreRoute)

# captcha_token 最长 2048 个字符，其余字段很短，4 KB 留足余量。
MAX_BODY_BYTES = 4 * 1024

# 不是 sent 的结果对应的状态码与错误码。
_ERRORS: dict[SendOutcome, tuple[int, str]] = {
    SendOutcome.SMS_DISABLED: (403, "sms_disabled"),
    SendOutcome.NOT_WHITELISTED: (422, "not_whitelisted"),
    SendOutcome.CAPTCHA_FAILED: (403, "captcha_failed"),
    SendOutcome.RATE_LIMITED: (429, "rate_limited"),
    SendOutcome.UNDELIVERABLE: (422, "sms_undeliverable"),
    SendOutcome.SUSPENDED: (503, "sms_suspended"),
}


class SmsSendIn(BaseModel):
    """手机号原文与页面国家码选择对应的地区代码、用途与人机挑战令牌。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 与游客下单相同：以加号开头时以输入为准。
    phone: str
    phone_region: str
    # 注销确认（delete_account）不在这里，由会员注销任务接上。
    purpose: Literal["checkout", "register", "login", "reset_password"]
    captcha_token: str = Field(min_length=1, max_length=MAX_TOKEN_LENGTH)


class SmsSendOut(BaseModel):
    status: Literal["sent"]


@dataclass(frozen=True)
class SmsSendInput:
    """规范化后的号码、用途与令牌；repr 不带出号码与令牌。"""

    phone_e164: str = field(repr=False)
    purpose: str
    captcha_token: str = field(repr=False)


async def _sms_send_input(request: Request) -> SmsSendInput:
    """逐块读请求体，超过上限就停下返回 413，不看 Content-Length 是否如实；
    读完之后才判断 Content-Type，再校验请求体与手机号。
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
    try:
        body = SmsSendIn.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    try:
        phone = normalize_phone(body.phone, body.phone_region).e164
    except InvalidPhoneNumber:
        error = _error(("body", "phone"), "phone_invalid", "invalid phone number")
        raise RequestValidationError([error]) from None
    return SmsSendInput(phone, body.purpose, body.captcha_token)


def get_sms_redis_client(request: Request) -> redis.Redis | None:
    """FastAPI 依赖：get_redis_client 的客户端，它抛 RateLimitUnavailable 时为 None。

    未配置或创建失败时不让异常经路由类变成 503 service_unavailable：None 交给
    send_verification，由它在开关、白名单与人机挑战之后按停发处理，不放行发送。
    不用 get_optional_redis_client（它只供计价与游客下单）。
    测试用 app.dependency_overrides[get_sms_redis_client] 覆盖。
    """
    try:
        return get_redis_client(request)
    except RateLimitUnavailable:
        return None


# 请求体依赖写在会话与其他依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
SmsSendDep = Annotated[SmsSendInput, Depends(_sms_send_input)]
SessionDep = Annotated[Session, Depends(get_session)]
RedisDep = Annotated[redis.Redis | None, Depends(get_sms_redis_client)]
ProviderDep = Annotated[SmsProvider, Depends(get_sms_provider)]
CaptchaDep = Annotated[CaptchaVerifier, Depends(get_captcha_verifier)]


@router.post("/send", response_model=SmsSendOut)
def send(
    body: SmsSendDep,
    session: SessionDep,
    client: RedisDep,
    provider: ProviderDep,
    captcha: CaptchaDep,
    request: Request,
) -> SmsSendOut:
    try:
        outcome = send_verification(
            session,
            request.app.state.settings,
            client,
            provider,
            captcha,
            body.phone_e164,
            body.purpose,
            body.captcha_token,
            client_source(request),
            _utcnow(),
        )
    except (SmsVerificationError, SmsBudgetError):
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_suspended") from None
    if outcome is SendOutcome.SENT:
        return SmsSendOut(status="sent")
    status_code, code = _ERRORS[outcome]
    raise HTTPException(status_code=status_code, detail=code)


def _rollback(session: Session) -> None:
    # 连接已断时回滚也会出错；会话随后由 get_session 关闭，未提交的改动不会留下。
    try:
        session.rollback()
    except SQLAlchemyError:
        pass

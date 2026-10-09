"""会员短信登录或注册、当前会话与退出：POST /api/member/sms-login、GET /api/member/session、
POST /api/member/logout，以及之后所有会员接口共用的会话依赖 require_member 与 CSRF 校验
require_member_csrf。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（注册、短信登录与结账验证是
同一个短信验证流程；会话到期、退出与服务端授权检查）与第 5 条（会话以服务端会话实现，经
HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，写操作另须 CSRF 令牌），以及
docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（会员会话 30 天、不随使用延长）。
规则函数见 app/services/member_sms_login.py（SHOP-TASK-075）与 app/services/member_auth.py
（SHOP-TASK-071）。不做密码登录、设置与重设密码、注销。

短信登录或注册的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 4 KB 即 413，先于一切校验；不是 JSON 415。
2. 请求体只有 phone、phone_region、purpose（checkout、register、login 之一）与 code（1 到
   16 个字符的字符串，是否为 4 到 10 位数字由核验函数判断），都必填、严格类型，多出字段 422；
   手机号规范化与 POST /api/sms/send 相同，不成立 422（类型 phone_invalid）。
3. 调用 sms_login_or_register，按结果回答：

   approved      提交后设置 cookie，200 {"created": <是否新建会员>}
   wrong_code    422 {"detail": "code_wrong"}
   expired       422 {"detail": "code_expired"}
   no_pending    422 {"detail": "code_expired"}
   unavailable   503 {"detail": "sms_unavailable"}
   sms_disabled  403 {"detail": "sms_disabled"}

   核验记录、会员、认领与会话在同一个事务里提交。不是 approved 时也先提交（核验函数可能已把
   记录改为 rejected）再回答，不设置 cookie。核验或本流程出现数据库错误（SmsVerificationError、
   MemberSmsLoginError 与提交失败）时回滚，503 sms_unavailable，不设置 cookie。数据库未配置时
   照 get_session 的 503 回答。请求已带会员会话 cookie 时照常处理，以新会话覆盖 cookie。

会话不通过一律 401 member_session_required，不区分会话不存在、已撤销、已到期或会员已注销；
不受短信验证开关影响。退出另须 CSRF，不通过 403 csrf_failed，此时不撤销会话。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由 SHOP-TASK-027
的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
本模块不写日志；响应体与错误不含号码原文、验证码、会员 ID、令牌或 cookie 值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.models import Member, MemberSession
from app.services.member_auth import (
    SESSION_LIFETIME,
    check_csrf_token,
    check_member_session,
    csrf_token_for_cookie,
    revoke_member_session,
)
from app.services.member_sms_login import MemberSmsLoginError, sms_login_or_register
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.sms_provider import SmsProvider, get_sms_provider
from app.services.sms_verification import SmsVerificationError, VerifyStatus

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)

# __Host- 前缀要求 Secure、Path=/ 且没有 Domain，同一上级域名下的其他站点无法写入它。
COOKIE_NAME = "__Host-shop_member_session"
COOKIE_MAX_AGE_SECONDS = int(SESSION_LIFETIME.total_seconds())
CSRF_HEADER = "X-CSRF-Token"
_NO_STORE = {"Cache-Control": "no-store"}

# 四个字段都很短，4 KB 与 POST /api/sms/send 相同。
MAX_BODY_BYTES = 4 * 1024
CODE_MAX_CHARS = 16

# 遮盖号码时保留的前后字符数。
_MASK_HEAD = 3
_MASK_TAIL = 4

# 不是 approved 的核验结果对应的状态码与错误码；expired 与 no_pending 都须重新发送。
_ERRORS: dict[VerifyStatus, tuple[int, str]] = {
    VerifyStatus.WRONG_CODE: (422, "code_wrong"),
    VerifyStatus.EXPIRED: (422, "code_expired"),
    VerifyStatus.NO_PENDING: (422, "code_expired"),
    VerifyStatus.UNAVAILABLE: (503, "sms_unavailable"),
    VerifyStatus.SMS_DISABLED: (403, "sms_disabled"),
}


class SmsLoginIn(BaseModel):
    """手机号原文与地区代码、用途与验证码。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    phone: str
    phone_region: str
    # 三者是同一个短信验证流程；密码重设与注销确认不在这里。
    purpose: Literal["checkout", "register", "login"]
    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS, repr=False)


class SmsLoginOut(BaseModel):
    created: bool


class MemberSessionOut(BaseModel):
    phone_masked: str
    has_password: bool
    # 带 UTC 时区，写法同 GET /api/admin/session。
    expires_at: datetime
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交会员写操作。
    csrf_token: str


@dataclass(frozen=True)
class SmsLoginInput:
    """规范化后的号码、用途与验证码；repr 不带出号码与验证码。"""

    phone_e164: str = field(repr=False)
    purpose: str
    code: str = field(repr=False)


@dataclass(frozen=True)
class MemberContext:
    """require_member 取到的当前会员会话：会话记录（含到期时间）与所属 active 会员。"""

    session: MemberSession = field(repr=False)
    member: Member = field(repr=False)
    cookie_value: str = field(repr=False)

    @property
    def expires_at(self) -> datetime:
        return self.session.expires_at


async def _sms_login_input(request: Request) -> SmsLoginInput:
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
        body = SmsLoginIn.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    try:
        phone = normalize_phone(body.phone, body.phone_region).e164
    except InvalidPhoneNumber:
        error = _error(("body", "phone"), "phone_invalid", "invalid phone number")
        raise RequestValidationError([error]) from None
    return SmsLoginInput(phone, body.purpose, body.code)


# 请求体依赖写在会话与其他依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
SmsLoginDep = Annotated[SmsLoginInput, Depends(_sms_login_input)]
SessionDep = Annotated[Session, Depends(get_session)]
ProviderDep = Annotated[SmsProvider, Depends(get_sms_provider)]


def require_member(request: Request, db: SessionDep) -> MemberContext:
    """FastAPI 依赖：按 cookie 取当前有效的会员会话与所属会员。

    之后所有会员接口都必须经它授权；写操作还须用 require_member_csrf 校验 X-CSRF-Token
    请求头。没有有效会话时 401 {"detail": "member_session_required"}，不区分会话不存在、
    已撤销、已到期或会员已注销。只读，不延长会话，不读短信验证开关。
    """
    cookie_value = request.cookies.get(COOKIE_NAME)
    checked = check_member_session(db, cookie_value, _utcnow())
    if checked is None or cookie_value is None:
        raise HTTPException(status_code=401, detail="member_session_required", headers=_NO_STORE)
    return MemberContext(session=checked.session, member=checked.member, cookie_value=cookie_value)


MemberDep = Annotated[MemberContext, Depends(require_member)]


def require_member_csrf(request: Request, member: MemberDep) -> None:
    """会员写操作的 CSRF 校验：X-CSRF-Token 恰好一个且与由 cookie 算出的值一致，否则 403。

    入参是 require_member 已取得的会员上下文，因此只在会话通过之后校验：会话不通过时先是
    401。可直接调用，也可作为依赖（同一请求里 require_member 只解析一次）。
    """
    values = request.headers.getlist(CSRF_HEADER)
    header_value = values[0] if len(values) == 1 else None
    if not check_csrf_token(member.cookie_value, header_value):
        raise HTTPException(status_code=403, detail="csrf_failed", headers=_NO_STORE)


def mask_phone(phone: str) -> str:
    """保留号码前 3 个字符与最后 4 个字符，中间每个字符换成星号。

    不足 8 个字符时（规范化的马新号码不会出现）整串换成星号，不露出任何数字。
    """
    if len(phone) <= _MASK_HEAD + _MASK_TAIL:
        return "*" * len(phone)
    hidden = len(phone) - _MASK_HEAD - _MASK_TAIL
    return phone[:_MASK_HEAD] + "*" * hidden + phone[-_MASK_TAIL:]


def _set_session_cookie(response: Response, token: str) -> None:
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


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        domain=None,
        secure=True,
        httponly=True,
        samesite="lax",
    )


@router.post("/sms-login", response_model=SmsLoginOut)
def sms_login(body: SmsLoginDep, session: SessionDep, provider: ProviderDep) -> Response:
    """核验通过 200 {"created": …} 并设置会员会话 cookie；不返回号码、会员 ID 与认领数。"""
    try:
        result = sms_login_or_register(
            session, provider, body.phone_e164, body.purpose, body.code, _utcnow()
        )
        # 不是 approved 时也提交：核验函数可能已把记录改为 rejected。
        session.commit()
    except (SmsVerificationError, MemberSmsLoginError, SQLAlchemyError):
        _rollback(session)
        raise HTTPException(status_code=503, detail="sms_unavailable") from None

    status = result.verification.status
    if status is not VerifyStatus.APPROVED or result.issued is None:
        status_code, code = _ERRORS.get(status, (503, "sms_unavailable"))
        raise HTTPException(status_code=status_code, detail=code)
    response = JSONResponse(SmsLoginOut(created=result.created_member).model_dump())
    _set_session_cookie(response, result.issued.token)
    return response


@router.get("/session", response_model=MemberSessionOut)
def current_session(member: MemberDep) -> MemberSessionOut:
    csrf_token = csrf_token_for_cookie(member.cookie_value)
    phone = member.member.phone
    if csrf_token is None or phone is None:
        # require_member 通过时 cookie 格式必然合法、会员必然 active 且有号码；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="member_session_required")
    return MemberSessionOut(
        phone_masked=mask_phone(phone),
        has_password=member.member.password_hash is not None,
        expires_at=member.expires_at.replace(tzinfo=UTC),
        csrf_token=csrf_token,
    )


@router.post("/logout", status_code=204, response_class=Response)
def logout(member: MemberDep, session: SessionDep, request: Request) -> Response:
    """撤销当前会话并提交，清除 cookie，204。CSRF 不通过时不撤销。"""
    require_member_csrf(request, member)
    revoke_member_session(session, member.session, _utcnow())
    session.commit()
    response = Response(status_code=204)
    _clear_session_cookie(response)
    return response


def _rollback(session: Session) -> None:
    # 连接已断时回滚也会出错；会话随后由 get_session 关闭，未提交的改动不会留下。
    try:
        session.rollback()
    except SQLAlchemyError:
        pass

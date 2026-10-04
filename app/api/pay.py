"""支付页 P06 与结果页 P07 的游客接口：GET /api/pay/orders、POST /api/pay/attempts、
POST /api/pay/cancel。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「订单与退款状态」「失败、并发与重试」与
「权限与资料保护」第 6、7 条，规则见 app/services/payment.py。字段参照 docs/UX.md 0.6 的
P06、P07。订单号不进任何路径或查询参数（docs/UX.md「阅读说明」），写接口在请求体里给。

三个接口都只凭 SHOP-TASK-019 的订单访问 cookie 与 guest_checkout 授权访问，不读会员会话；
lookup 授权不能用。授权不通过一律 401 {"detail": "access_expired"}（页面的
pay.session_expired），不区分订单不存在、授权过期、不属于该会话或范围不符。

两个写接口的处理顺序，每一步不通过即停止，且之前不读写订单数据：
1. 请求体按实际读到的字节逐块判断，超过 8 KB（与计价、下单接口相同）即 413，先于一切校验；
   不是 JSON 415；幂等键（只有支付要，规则同下单接口）与请求体一并校验，多出字段 422。
   422 只给出错字段的位置、类型与固定消息，不回显提交的内容。
2. 按请求里的订单号查到订单 ID，以当前 cookie 与 guest_checkout 范围调用
   check_order_access；订单号不存在或不通过都是同一个 401。
3. X-CSRF-Token 请求头经 check_csrf_token 校验，不通过 403 {"detail": "csrf_failed"}。
4. 之后才查幂等键与订单状态。

409 的 detail：idempotency_conflict、order_expired、order_not_payable、order_not_cancellable；
后三种另带订单当前状态 status。不写日志；错误响应不含订单号、内部细节或令牌。
限流留给之后统一处理公开接口限流的任务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.checkout import MAX_BODY_BYTES
from app.api.orders import IDEMPOTENCY_HEADER, _error, _is_json, _key_errors
from app.db.session import get_session
from app.models import Order
from app.models.order import ORDER_NUMBER_LENGTH
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.services.catalog import Language
from app.services.order_access import (
    COOKIE_NAME,
    check_csrf_token,
    check_order_access,
    csrf_token_for_cookie,
    list_order_access,
)
from app.services.order_rules import is_valid_order_number
from app.services.payment import (
    CancelOutcome,
    IdempotencyConflict,
    OrderExpired,
    OrderNotCancellable,
    OrderNotPayable,
    PaymentMethod,
    PaymentOutcome,
    PaymentResult,
    PayOrder,
    cancel_order,
    expire_overdue_orders,
    pay_orders,
    submit_payment,
)

router = APIRouter(prefix="/api/pay", tags=["pay"])

CSRF_HEADER = "X-CSRF-Token"
_NO_STORE = {"Cache-Control": "no-store"}
_LANGUAGES: tuple[str, ...] = get_args(Language)


class PaymentIn(BaseModel):
    """只有订单号、支付方式与结果；不接受任何卡号或账户资料。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)
    method: PaymentMethod
    result: PaymentResult


class CancelIn(BaseModel):
    """只有订单号。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)


class PayOrdersOut(BaseModel):
    """本浏览器有效 guest_checkout 授权对应的订单，按授权到期时间从晚到早。"""

    orders: list[PayOrder]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交支付与取消。
    csrf_token: str


@dataclass(frozen=True)
class PaymentInput:
    idempotency_key: str = field(repr=False)
    body: PaymentIn = field(repr=False)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _body_errors(exc: ValidationError) -> list[dict[str, Any]]:
    # 不回显输入：只给位置、类型与固定消息。
    return [
        _error(("body", *error["loc"]), error["type"], error["msg"])
        for error in exc.errors(include_url=False)
    ]


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


async def _payment_input(request: Request) -> PaymentInput:
    raw = await _json_body(request)
    errors = _key_errors(request)
    body: PaymentIn | None = None
    try:
        body = PaymentIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)
    return PaymentInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


async def _cancel_input(request: Request) -> CancelIn:
    raw = await _json_body(request)
    try:
        return CancelIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


def _language(lang: str = "en") -> Language:
    """与目录接口相同，只接受 en、zh、ms，默认 en；不合法时 422 不回显所给的值。"""
    if lang not in _LANGUAGES:
        msg = "Input should be 'en', 'zh' or 'ms'"
        raise RequestValidationError([_error(("query", "lang"), "literal_error", msg)])
    return cast(Language, lang)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
PaymentDep = Annotated[PaymentInput, Depends(_payment_input)]
CancelDep = Annotated[CancelIn, Depends(_cancel_input)]
LanguageDep = Annotated[Language, Depends(_language)]
SessionDep = Annotated[Session, Depends(get_session)]


def _access_expired() -> HTTPException:
    # 订单不存在、授权过期、不属于该会话或范围不符，都是同一个响应。
    return HTTPException(status_code=401, detail="access_expired", headers=_NO_STORE)


def _authorized_order_id(
    db: Session,
    cookie_value: str | None,
    order_number: str,
    now: datetime,
) -> int:
    """请求里的订单号对应的订单 ID，且当前 cookie 对它有有效的 guest_checkout 授权。"""
    order_id = None
    if is_valid_order_number(order_number):
        order_id = db.scalar(select(Order.id).where(Order.order_number == order_number))
    if order_id is None:
        raise _access_expired()
    if not check_order_access(db, cookie_value, order_id, SCOPE_GUEST_CHECKOUT, now):
        raise _access_expired()
    return order_id


def _require_csrf(request: Request, cookie_value: str | None) -> None:
    values = request.headers.getlist(CSRF_HEADER)
    header_value = values[0] if len(values) == 1 else None
    if not check_csrf_token(cookie_value, header_value):
        raise HTTPException(status_code=403, detail="csrf_failed", headers=_NO_STORE)


def _conflict(code: str, status: str | None = None) -> JSONResponse:
    content = {"detail": code}
    if status is not None:
        content["status"] = status
    return JSONResponse(status_code=409, content=content, headers=_NO_STORE)


@router.get("/orders", response_model=PayOrdersOut)
def list_orders(
    lang: LanguageDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> PayOrdersOut:
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_ids = list_order_access(session, cookie_value, SCOPE_GUEST_CHECKOUT, now)
    csrf_token = csrf_token_for_cookie(cookie_value)
    if not order_ids or csrf_token is None:
        raise _access_expired()
    # 返回前先对这些订单执行超时取消，页面看到的状态与库存一致。
    expire_overdue_orders(session, now, order_ids=order_ids)
    orders = pay_orders(session, order_ids, lang, now)
    response.headers["Cache-Control"] = "no-store"
    return PayOrdersOut(orders=orders, csrf_token=csrf_token)


@router.post("/attempts", status_code=201, response_model=PaymentOutcome)
def submit_attempt(
    payment: PaymentDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> PaymentOutcome | JSONResponse:
    """新写支付尝试 201，按幂等键重放 200。"""
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    body = payment.body
    order_id = _authorized_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    try:
        outcome, created = submit_payment(
            session, order_id, body.method, body.result, payment.idempotency_key, now
        )
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderExpired as exc:
        return _conflict("order_expired", exc.status)
    except OrderNotPayable as exc:
        return _conflict("order_not_payable", exc.status)
    if not created:
        response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return outcome


@router.post("/cancel", response_model=CancelOutcome)
def cancel(
    body: CancelDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> CancelOutcome | JSONResponse:
    """不要求幂等键：重复取消已取消的订单直接返回当前状态。"""
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_id = _authorized_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    try:
        outcome = cancel_order(session, order_id, now)
    except OrderNotCancellable as exc:
        return _conflict("order_not_cancellable", exc.status)
    response.headers["Cache-Control"] = "no-store"
    return outcome

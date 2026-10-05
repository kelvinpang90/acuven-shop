"""订单查询 P08 与订单详情 P09（查单模式）的游客接口：POST /api/orders/lookup、
GET /api/orders/lookup、POST /api/orders/confirm-receipt。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 2、6、7 条、「失败、并发与重试」
第 1、4 条与「订单与退款状态」第 2 条，规则见 app/services/order_lookup.py。字段参照
docs/UX.md 0.7 的 P08、P09（查单模式）。订单号与电话只在请求体里，不进任何路径或查询参数。

三个接口都不读会员会话；查看与确认收货只凭 SHOP-TASK-019 的订单访问 cookie 与 lookup 授权，
guest_checkout 授权不能用。授权不通过一律 401 {"detail": "access_expired"}（页面的
order.session_expired），不区分订单不存在、授权过期、不属于该会话或范围不符。
三个接口的所有响应（含错误）都带 Cache-Control: no-store（见 _NoStoreRoute）。

查单的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有 order_number 与 phone 两个字符串，多出字段 422。
2. Redis 连接串未配置即 503（取客户端的依赖），之后按 app/services/order_lookup.py 的顺序：
   来源计数（超限 429 rate_limited）、订单号规范化与格式、失败计数（已满 429）、
   查订单与比对电话。
   格式不合法、订单不存在、电话无法规范化与不符一律 404 {"detail": "not_found"}（页面的
   lookup.not_found）；Redis 连不上或超时 503 {"detail": "service_unavailable"}（页面的
   common.service_unavailable）。
3. 通过时设置订单访问 cookie，204 且响应体为空。

确认收货的处理顺序，每一步不通过即停止，且之前不读写订单数据：请求体大小与格式（规则同
app/api/pay.py，幂等键与请求体一并校验）→ 按订单号查订单 ID，以当前 cookie 与 lookup 范围调用
check_order_access（401）→ CSRF（403 csrf_failed）→ 才查幂等键与订单状态。
409 的 detail：idempotency_conflict；order_not_confirmable 另带订单当前状态 status。

422 只给出错字段的位置、类型与固定消息，不回显提交的内容。不写日志；错误响应不含订单号、
电话、内部细节或令牌。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.orders import IDEMPOTENCY_HEADER, _key_errors
from app.api.pay import (
    LanguageDep,
    _access_expired,
    _body_errors,
    _conflict,
    _json_body,
    _require_csrf,
)
from app.db.session import get_session
from app.models import Order
from app.models.order import ORDER_NUMBER_LENGTH
from app.models.order_access import SCOPE_LOOKUP
from app.services.order_access import (
    COOKIE_NAME,
    check_order_access,
    csrf_token_for_cookie,
    list_order_access,
    set_order_access_cookie,
)
from app.services.order_lookup import (
    ConfirmOutcome,
    IdempotencyConflict,
    LookupNotFound,
    LookupOrder,
    LookupRateLimited,
    OrderNotConfirmable,
    confirm_receipt,
    lookup_order,
    lookup_orders,
)
from app.services.order_rules import is_valid_order_number
from app.services.payment import expire_overdue_orders
from app.services.rate_limit import RateLimitUnavailable, client_source, get_redis_client


class _NoStoreRoute(APIRoute):
    """本路由的每个响应（含错误）都带 Cache-Control: no-store。

    请求体、语言参数与会话依赖抛出的 HTTPException、RequestValidationError 在这里按 FastAPI
    默认的处理器转成响应再加上这个头；Redis 不可用（取客户端的依赖或计数时抛出的
    RateLimitUnavailable）转成 503 service_unavailable。
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def no_store_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except RateLimitUnavailable:
                content = {"detail": "service_unavailable"}
                response = JSONResponse(status_code=503, content=content)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            except StarletteHTTPException as exc:
                response = await http_exception_handler(request, exc)
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store_handler


router = APIRouter(prefix="/api/orders", tags=["order-lookup"], route_class=_NoStoreRoute)


class LookupIn(BaseModel):
    """只有订单号与电话两个字符串；格式由查单流程判断，不合法与查不到同样 404。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str
    phone: str


class ConfirmReceiptIn(BaseModel):
    """只有订单号。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)


class LookupOrdersOut(BaseModel):
    """本浏览器有效 lookup 授权对应的订单，按授权到期时间从晚到早。"""

    orders: list[LookupOrder]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交确认收货。
    csrf_token: str


@dataclass(frozen=True)
class ConfirmInput:
    idempotency_key: str = field(repr=False)
    body: ConfirmReceiptIn = field(repr=False)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _lookup_input(request: Request) -> LookupIn:
    raw = await _json_body(request)
    try:
        return LookupIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


async def _confirm_input(request: Request) -> ConfirmInput:
    raw = await _json_body(request)
    errors = _key_errors(request)
    body: ConfirmReceiptIn | None = None
    try:
        body = ConfirmReceiptIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)
    return ConfirmInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


# 请求体依赖写在其他依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答，
# Redis 未配置的 503 在请求体校验之后。
LookupDep = Annotated[LookupIn, Depends(_lookup_input)]
ConfirmDep = Annotated[ConfirmInput, Depends(_confirm_input)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]
SessionDep = Annotated[Session, Depends(get_session)]


def _authorized_order_id(
    db: Session,
    cookie_value: str | None,
    order_number: str,
    now: datetime,
) -> int:
    """请求里的订单号对应的订单 ID，且当前 cookie 对它有有效的 lookup 授权。"""
    order_id = None
    if is_valid_order_number(order_number):
        order_id = db.scalar(select(Order.id).where(Order.order_number == order_number))
    if order_id is None:
        raise _access_expired()
    if not check_order_access(db, cookie_value, order_id, SCOPE_LOOKUP, now):
        raise _access_expired()
    return order_id


@router.post("/lookup", status_code=204, response_class=Response)
def lookup(
    body: LookupDep,
    redis_client: RedisDep,
    session: SessionDep,
    request: Request,
) -> Response:
    # 只读订单访问 cookie（沿用本浏览器的订单访问会话），不读会员会话。
    cookie_value = request.cookies.get(COOKIE_NAME)
    source = client_source(request)
    try:
        issued = lookup_order(
            session,
            redis_client,
            source,
            body.order_number,
            body.phone,
            cookie_value,
            _utcnow(),
        )
    except LookupRateLimited:
        raise HTTPException(status_code=429, detail="rate_limited") from None
    except LookupNotFound:
        raise HTTPException(status_code=404, detail="not_found") from None

    response = Response(status_code=204)
    # 沿用会话时以原 cookie 值重设，把 Max-Age 刷新为 30 分钟。
    token = issued.new_token or cookie_value
    if token is not None:
        set_order_access_cookie(response, token)
    return response


@router.get("/lookup", response_model=LookupOrdersOut)
def list_lookup_orders(
    lang: LanguageDep,
    session: SessionDep,
    request: Request,
) -> LookupOrdersOut:
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_ids = list_order_access(session, cookie_value, SCOPE_LOOKUP, now)
    csrf_token = csrf_token_for_cookie(cookie_value)
    if not order_ids or csrf_token is None:
        raise _access_expired()
    # 返回前先对其中待支付的订单执行超时取消，页面看到的状态与库存一致。
    expire_overdue_orders(session, now, order_ids=order_ids)
    orders = lookup_orders(session, order_ids, lang, now)
    return LookupOrdersOut(orders=orders, csrf_token=csrf_token)


@router.post("/confirm-receipt", response_model=ConfirmOutcome)
def confirm(
    confirm_input: ConfirmDep,
    session: SessionDep,
    request: Request,
) -> ConfirmOutcome | JSONResponse:
    """确认收货与按幂等键重放都是 200 与订单当前状态。"""
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_number = confirm_input.body.order_number
    order_id = _authorized_order_id(session, cookie_value, order_number, now)
    _require_csrf(request, cookie_value)
    try:
        return confirm_receipt(session, order_id, confirm_input.idempotency_key, now)
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderNotConfirmable as exc:
        return _conflict("order_not_confirmable", exc.status)

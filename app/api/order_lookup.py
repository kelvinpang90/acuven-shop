"""订单查询 P08 与订单详情 P09（查单模式）的游客接口：POST /api/orders/lookup、
GET /api/orders/lookup、POST /api/orders/confirm-receipt。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 2、6、7 条、「失败、并发与重试」
第 1、4 条与「订单与退款状态」，规则见 app/services/order_lookup.py。字段参照 docs/UX.md 0.7 的
P08、P09（查单模式）。订单号与电话只在请求体里，不进任何路径或查询参数。

三个接口都不读会员会话。查看与确认收货只凭 SHOP-TASK-019 的订单访问 cookie 与 lookup 授权，
guest_checkout 授权不能用；授权不通过一律 401 {"detail": "access_expired"}（页面的
order.session_expired），不区分订单不存在、授权过期、不属于该会话或范围不符。

查单（POST /api/orders/lookup）的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有订单号与电话两个字符串，多出字段 422。
2. 取 Redis 客户端（未配置即 503），按来源计数、规范化订单号、读失败计数、比对电话，
   见 app/services/order_lookup.py 的 lookup_order。
3. 通过时签发授权并设置 cookie，204 且响应体为空。
订单号格式不合法、订单不存在、电话无法规范化与电话不符都是同一个 404 {"detail": "not_found"}
（页面的 lookup.not_found）；超过限流 429 rate_limited（common.rate_limited）；Redis 不可用
503 service_unavailable（common.service_unavailable）。

确认收货的处理顺序与 app/api/pay.py 的支付相同：请求体与幂等键 → 按订单号对应的订单 ID 与
lookup 范围逐单校验授权（401）→ CSRF（403 csrf_failed）→ 才查幂等键与订单状态。
409 的 detail：idempotency_conflict，以及另带订单当前状态 status 的 order_not_confirmable。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由本模块的
路由类统一加上；422 每条错误只给位置、类型与固定消息，不回显请求内容。不写日志；错误响应
不含订单号、电话、内部细节或令牌。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.orders import IDEMPOTENCY_HEADER, _key_errors
from app.api.pay import (
    _access_expired,
    _body_errors,
    _conflict,
    _json_body,
    _language,
    _require_csrf,
    _utcnow,
)
from app.db.session import get_session
from app.models import Order
from app.models.order import ORDER_NUMBER_LENGTH
from app.models.order_access import SCOPE_LOOKUP
from app.services.catalog import Language
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


def _plain_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    # 只留位置、类型与消息：框架自己产生的错误也不带出 input 或 ctx。
    return [
        {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
        for error in exc.errors()
    ]


class _NoStoreRoute(APIRoute):
    """处理函数与依赖产生的全部响应都带 no-store，包括它们抛出的错误。

    HTTPException（含 get_session 的 503）、请求校验错误（422）与 Redis 不可用
    （RateLimitUnavailable，回答 503 service_unavailable）都在这里转成响应，
    不交给应用级的异常处理器（那里不加 no-store）。路径存在但方法不匹配的 405 由框架
    在路由之外回答，不经过这里。
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def no_store_handler(request: Request) -> Response:
            response: Response
            try:
                response = await handler(request)
            except StarletteHTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                )
            except RequestValidationError as exc:
                response = JSONResponse({"detail": _plain_errors(exc)}, status_code=422)
            except RateLimitUnavailable:
                response = JSONResponse({"detail": "service_unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store_handler


router = APIRouter(prefix="/api/orders", tags=["orders"], route_class=_NoStoreRoute)


class LookupIn(BaseModel):
    """只有订单号与电话两个字符串；长度与格式不在这里校验，不合法时与查不到同一个 404。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str
    phone: str


class ConfirmIn(BaseModel):
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
    body: ConfirmIn = field(repr=False)


async def _lookup_input(request: Request) -> LookupIn:
    raw = await _json_body(request)
    try:
        return LookupIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


async def _confirm_input(request: Request) -> ConfirmInput:
    raw = await _json_body(request)
    errors = _key_errors(request)
    body: ConfirmIn | None = None
    try:
        body = ConfirmIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)
    return ConfirmInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


# 请求体依赖写在 Redis 与会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答，
# 请求体不合格时也不碰 Redis。
LookupDep = Annotated[LookupIn, Depends(_lookup_input)]
ConfirmDep = Annotated[ConfirmInput, Depends(_confirm_input)]
LanguageDep = Annotated[Language, Depends(_language)]
RedisDep = Annotated[redis.Redis, Depends(get_redis_client)]
SessionDep = Annotated[Session, Depends(get_session)]


def _lookup_order_id(
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
    client: RedisDep,
    session: SessionDep,
    request: Request,
) -> Response:
    """查单通过 204，响应体为空；订单内容由 GET /api/orders/lookup 取。"""
    # 只读订单访问 cookie（沿用本浏览器的订单访问会话），不读会员会话。
    cookie_value = request.cookies.get(COOKIE_NAME)
    try:
        issued = lookup_order(
            session,
            client,
            client_source(request),
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
    confirmation: ConfirmDep,
    session: SessionDep,
    request: Request,
) -> ConfirmOutcome | JSONResponse:
    """新写确认与按幂等键重放都是 200 与订单状态。"""
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    body = confirmation.body
    order_id = _lookup_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    try:
        outcome, _ = confirm_receipt(session, order_id, confirmation.idempotency_key, now)
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderNotConfirmable as exc:
        return _conflict("order_not_confirmable", exc.status)
    return outcome

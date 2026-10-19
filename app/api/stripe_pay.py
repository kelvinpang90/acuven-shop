"""支付页 P06 与结果页 P07 的游客 Stripe 接口：POST /api/pay/stripe/start、
POST /api/pay/stripe/confirm。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 3 条（「访客在支付页按现有授权
与 CSRF 校验后发起」，服务端只把 Stripe 返回的付款页地址交给前台跳转）、第 4 条（「访客从 Stripe
返回后前台调用本站接口，服务端按授权找到该单的会话向 Stripe 查询一次」）与第 9 条（「创建会话或
查询时 Stripe 不可用，支付页提示稍后再试……不自动改用模拟支付」），docs/UX.md 0.11 的 P06、P07，
以及 docs/HANDOFF.md 0.54 的落实写法。规则见 app/services/stripe_checkout.py 的 start_checkout 与
app/services/stripe_confirm.py 的 confirm_order_payment。

两个接口都只凭订单访问 cookie 与 guest_checkout 授权访问，不读会员会话（会员接口由之后的任务做）。
授权、CSRF、请求体读取与 422 写法复用 app/api/pay.py，处理顺序与 POST /api/pay/cancel 相同：
请求体 413 → 415 → 422（含 lang）→ 授权 401 access_expired → CSRF 403 csrf_failed，每一步不通过
即停止，且之前不读写订单数据。适配器经 get_stripe_gateway 取得（测试以 app.state.stripe_gateway
注入替身）。

发起的结果：redirect 回 200 与付款页地址；complete 回 200、地址为 null（前端随后调用确认接口）；
not_payable 回 409 order_not_payable（另带订单当前状态 status）；method_changed 回 409
method_changed；under_review 回 409 stripe_under_review；unavailable 回 503 stripe_unavailable。
确认回 200 与 paid、confirming、not_paid、refunded 之一；Stripe 不可用由 confirm_order_payment
归为 confirming，不回 503。响应里除付款页地址外不带任何 Stripe ID。

处理函数与依赖产生的全部响应（含错误）都带 no-store，由 app/api/order_lookup.py 的路由类加上。
不写日志（服务层按设计记的 error 日志除外）；错误响应不含订单号、内部细节或令牌。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.api.order_lookup import _NoStoreRoute
from app.api.pay import (
    GatewayDep,
    LanguageDep,
    SessionDep,
    _authorized_order_id,
    _body_errors,
    _conflict,
    _json_body,
    _require_csrf,
    _stripe_unavailable,
    _utcnow,
)
from app.models import Order
from app.models.order import ORDER_NUMBER_LENGTH
from app.services.order_access import COOKIE_NAME
from app.services.stripe_checkout import StartResult, start_checkout
from app.services.stripe_confirm import OrderPaymentResult, confirm_order_payment

router = APIRouter(prefix="/api/pay/stripe", tags=["pay"], route_class=_NoStoreRoute)


class StripeOrderIn(BaseModel):
    """只有订单号。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)


class StartOut(BaseModel):
    result: Literal["redirect", "complete"]
    # 只在 redirect 时有：Stripe 返回的付款页地址。
    checkout_url: str | None


class ConfirmOut(BaseModel):
    result: OrderPaymentResult


async def _order_input(request: Request) -> StripeOrderIn:
    raw = await _json_body(request)
    try:
        return StripeOrderIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


# 请求体依赖写在其他依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
OrderDep = Annotated[StripeOrderIn, Depends(_order_input)]


@router.post("/start", response_model=StartOut)
def start(
    body: OrderDep,
    lang: LanguageDep,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> StartOut | JSONResponse:
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_id = _authorized_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    settings = request.app.state.settings
    started = start_checkout(session, gateway, settings, order_id, lang, now)
    if started.result is StartResult.REDIRECT:
        return StartOut(result="redirect", checkout_url=started.checkout_url)
    if started.result is StartResult.COMPLETE:
        return StartOut(result="complete", checkout_url=None)
    if started.result is StartResult.NOT_PAYABLE:
        # start_checkout 已结束自己的事务（含超时取消）；这里重读订单当前状态。
        status = session.scalars(select(Order.status).where(Order.id == order_id)).one()
        session.rollback()
        return _conflict("order_not_payable", status)
    if started.result is StartResult.METHOD_CHANGED:
        return _conflict("method_changed")
    if started.result is StartResult.UNDER_REVIEW:
        return _conflict("stripe_under_review")
    return _stripe_unavailable()


@router.post("/confirm", response_model=ConfirmOut)
def confirm(
    body: OrderDep,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> ConfirmOut:
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_id = _authorized_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    # 授权检查只有读取；confirm_order_payment 要求调用方不持锁、没有未提交的改动。
    session.rollback()
    result = confirm_order_payment(session, gateway, order_id, now)
    return ConfirmOut(result=result)

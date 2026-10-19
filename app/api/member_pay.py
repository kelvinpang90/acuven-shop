"""支付页 P06 与结果页 P07 的会员接口：POST /api/member/pay/order、
POST /api/member/pay/attempts、POST /api/member/pay/cancel。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」（待支付订单可由下单者在支付页
取消，会员凭登录会话）与「权限与资料保护」第 6 条（会员仅能访问自己认领或下单的订单；支付、
取消等写操作另须 CSRF 令牌），以及 docs/UX.md 0.10 的 P06、P07（会员凭登录会话访问支付页与
结果页；成功后会员看到本单获得积分；会员可从 P09 会员模式回到 P06 继续支付或取消）。
规则见 app/services/payment.py（SHOP-TASK-090）；请求体、幂等键与 409 的写法沿用
app/api/pay.py 的游客接口，会话依赖取自 app/api/member_auth.py。

三个接口只凭会员会话（require_member，401 member_session_required），不读订单访问 cookie；
订单号只在请求体里，不进任何路径或查询参数。按订单号查订单时，订单不存在、订单号不合法或
订单的 member_id 不等于该会员一律 404 {"detail": "order_not_found"}；认领的游客订单与会员
下单的订单都以 member_id 归属该会员，不限创建时间。

查看（只读，不要求幂等键与 CSRF）的处理顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415。
2. require_member（401 member_session_required）。
3. 语言参数同 GET /api/pay/orders（en、zh、ms，默认 en）；请求体只有订单号，多出字段 422。
4. 查订单（404 order_not_found）；待支付时先对它执行 expire_overdue_orders，再以 pay_orders
   的视图返回（含收货资料与券折扣、积分抵扣、获得积分三个快照字段）。

两个写接口的处理顺序，每一步不通过即停止，且之前不读写订单数据：
1. 413 与 415 同上；幂等键（只有支付要，规则同下单接口）与请求体一并校验，多出字段 422。
2. require_member（401 member_session_required）。
3. require_member_csrf（403 csrf_failed）。
4. 查订单（404 order_not_found）。
5. 以 actor="member" 调用 submit_payment 与 cancel_order；成功与 409 的状态码、响应体与
   detail 和游客接口相同。取消另经 get_stripe_gateway 取得适配器并传给 cancel_order，模拟
   支付不传（docs/DESIGN.md 1.12（提交 acf576e）第 2、5、9 条与 docs/HANDOFF.md 0.54）：
   PaymentPathMismatch 回 409 method_changed，StripeUnavailable 回 503 stripe_unavailable，
   与游客接口相同。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容。本模块不写日志；错误响应不含订单号、
会员 ID、令牌或 cookie 值。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.member_auth import MemberDep, require_member_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import (
    CancelDep,
    CancelIn,
    GatewayDep,
    LanguageDep,
    PaymentDep,
    _body_errors,
    _conflict,
    _json_body,
    _stripe_unavailable,
    _utcnow,
)
from app.db.session import get_session
from app.models import Order
from app.models.order import STATUS_AWAITING_PAYMENT
from app.services.order_rules import is_valid_order_number
from app.services.payment import (
    CancelOutcome,
    IdempotencyConflict,
    OrderExpired,
    OrderNotCancellable,
    OrderNotPayable,
    PaymentOutcome,
    PaymentPathMismatch,
    PayOrder,
    StripeUnavailable,
    cancel_order,
    expire_overdue_orders,
    pay_orders,
    submit_payment,
)

router = APIRouter(prefix="/api/member/pay", tags=["member"], route_class=_NoStoreRoute)


async def _order_body(request: Request) -> bytes:
    """查看只在这里判断 413 与 415；结构在会话之后才校验。"""
    return await _json_body(request)


# 请求体依赖写在会员与会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
OrderBodyDep = Annotated[bytes, Depends(_order_body)]
SessionDep = Annotated[Session, Depends(get_session)]


def _order_not_found() -> HTTPException:
    # 订单不存在、订单号不合法或不属于该会员，都是同一个响应。
    return HTTPException(status_code=404, detail="order_not_found")


def _member_order(db: Session, member_id: int, order_number: str) -> tuple[int, str]:
    """请求里的订单号对应、且 member_id 为该会员的订单的 ID 与状态。"""
    row: tuple[int, str] | None = None
    if is_valid_order_number(order_number):
        stmt = select(Order.id, Order.status).where(
            Order.order_number == order_number, Order.member_id == member_id
        )
        row = db.execute(stmt).tuples().one_or_none()
    if row is None:
        raise _order_not_found()
    return row


@router.post("/order", response_model=PayOrder)
def view_order(
    raw: OrderBodyDep,
    member: MemberDep,
    lang: LanguageDep,
    session: SessionDep,
) -> PayOrder:
    """只读查看本人的一张订单；不要求幂等键与 CSRF。"""
    try:
        body = CancelIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    # 超时取消会提交，会话里的会员对象随之过期：会员 ID 在此之前取出。
    member_id = member.member.id
    now = _utcnow()
    order_id, status = _member_order(session, member_id, body.order_number)
    if status == STATUS_AWAITING_PAYMENT:
        # 返回前先对它执行超时取消，页面看到的状态与库存一致。
        expire_overdue_orders(session, now, order_ids=[order_id])
    (view,) = pay_orders(session, [order_id], lang, now)
    return view


@router.post("/attempts", status_code=201, response_model=PaymentOutcome)
def submit_attempt(
    payment: PaymentDep,
    member: MemberDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> PaymentOutcome | JSONResponse:
    """新写支付尝试 201，按幂等键重放 200。"""
    require_member_csrf(request, member)
    # 支付函数可能回滚会话，会员对象随之过期：会员 ID 在此之前取出。
    member_id = member.member.id
    body = payment.body
    order_id, _ = _member_order(session, member_id, body.order_number)
    try:
        outcome, created = submit_payment(
            session,
            order_id,
            body.method,
            body.result,
            payment.idempotency_key,
            _utcnow(),
            actor="member",
        )
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderExpired as exc:
        return _conflict("order_expired", exc.status)
    except OrderNotPayable as exc:
        return _conflict("order_not_payable", exc.status)
    except PaymentPathMismatch:
        return _conflict("method_changed")
    if not created:
        response.status_code = 200
    return outcome


@router.post("/cancel", response_model=CancelOutcome)
def cancel(
    body: CancelDep,
    member: MemberDep,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> CancelOutcome | JSONResponse:
    """不要求幂等键：重复取消已取消的订单直接返回当前状态。"""
    require_member_csrf(request, member)
    member_id = member.member.id
    order_id, _ = _member_order(session, member_id, body.order_number)
    try:
        return cancel_order(session, order_id, _utcnow(), actor="member", gateway=gateway)
    except OrderNotCancellable as exc:
        return _conflict("order_not_cancellable", exc.status)
    except StripeUnavailable:
        return _stripe_unavailable()

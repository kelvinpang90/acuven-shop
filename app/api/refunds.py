"""退款申请 P10（查单模式）的游客接口：POST /api/orders/refunds。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」「失败、并发与重试」第 1 条与
「权限与资料保护」第 6、7 条，以及 docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 退款决定，
规则见 app/services/refunds.py。字段参照 docs/UX.md 0.7 的 P10（查单模式）：只选商品与数量，
金额由服务端按逐件现金实付快照计算，运费不退。订单号只在请求体里，不进任何路径或查询参数。

不读会员会话。只凭 SHOP-TASK-019 的订单访问 cookie 与 lookup 授权，guest_checkout 授权不能用；
授权不通过一律 401 {"detail": "access_expired"}（页面的 order.session_expired），不区分订单
不存在、授权过期、不属于该会话或范围不符。

处理顺序，每一步不通过即停止，且之前不读写订单数据：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   幂等键（规则同 SHOP-TASK-020）与请求体一并校验：请求体只有订单号与申请行列表（1 到 20 项，
   每项为订单行序与 1 到 1000 件，行序不重复），多出字段 422。
2. 按请求里的订单号查到订单 ID，以当前 cookie 与 lookup 范围调用 check_order_access（401）。
3. X-CSRF-Token 请求头经 check_csrf_token 校验，不通过 403 {"detail": "csrf_failed"}。
4. 之后才查幂等键与订单（app/services/refunds.py 的 submit_refund）。
新写申请 201，按幂等键重放 200，都是申请的状态、金额与各行件数。
409 的 detail：idempotency_conflict、order_not_refundable、refund_window_closed（页面的
refund.window_closed）、refund_nothing_left（refund.nothing_left）、refund_duplicate
（refund.duplicate）；请求里的行序不属于该订单 422。

积分预估 POST /api/orders/refunds/points-estimate（SHOP-TASK-093）：依据 DESIGN 1.11「计价、
优惠、积分与库存」第 5 条与 docs/HANDOFF.md 0.46，供 P10 在提交前显示 refund.points_back 与
refund.points_reversed。只读、不写库，不要求幂等键与 CSRF；授权与请求体同上（413 → 415 → 422
→ 401）。之后与提交申请相同：订单未支付 409 order_not_refundable，退款期已过 409
refund_window_closed，行序不属于该订单 422，件数超过该行可退件 409 refund_nothing_left。
每行按 refund_state 的可退件以 pick_units 取件序最小的那几件（与提交申请时选件相同），应返还
与应追回分别为这些件的 points_discount 与 points_earned 之和，交给 SHOP-TASK-087 的
estimate_refund_points；订单的 member_id 为空时 member_order 为假、两个数为 0，不调用它。
预估与批准时可能因到期或余额变化而不同，以批准时为准。

处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由 app/api/order_lookup.py
的路由类统一加上；422 每条错误只给位置、类型与固定消息，不回显请求内容。不写日志；错误响应
不含订单号、内部细节或令牌。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.order_lookup import _lookup_order_id, _NoStoreRoute
from app.api.orders import IDEMPOTENCY_HEADER, _error, _key_errors
from app.api.pay import _body_errors, _conflict, _json_body, _require_csrf, _utcnow
from app.db.session import get_session
from app.models import Order, OrderItemUnit
from app.models.order import ORDER_NUMBER_LENGTH, PAID_STATUSES
from app.services.checkout import MAX_CART_LINES, MAX_LINE_QUANTITY
from app.services.order_access import COOKIE_NAME
from app.services.points_refund import estimate_refund_points
from app.services.refunds import (
    IdempotencyConflict,
    OrderNotRefundable,
    RefundDuplicate,
    RefundLineRequest,
    RefundNothingLeft,
    RefundOutcome,
    RefundUnit,
    RefundWindowClosed,
    UnknownOrderLine,
    in_refund_window,
    pick_units,
    refund_state,
    submit_refund,
)

router = APIRouter(prefix="/api/orders", tags=["orders"], route_class=_NoStoreRoute)

# 与购物车相同：最多 20 行。
MAX_REFUND_LINES = MAX_CART_LINES


class RefundLineIn(BaseModel):
    # strict：件数 2.0、"2" 与 true 都不是整数件数。
    model_config = ConfigDict(extra="forbid", strict=True)

    # 订单行序，即 order_items 的 line_index。
    line_index: int = Field(ge=0)
    quantity: int = Field(ge=1, le=MAX_LINE_QUANTITY)


class RefundIn(BaseModel):
    """只有订单号与申请行；没有任何金额字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    order_number: str = Field(min_length=1, max_length=ORDER_NUMBER_LENGTH)
    lines: list[RefundLineIn] = Field(min_length=1, max_length=MAX_REFUND_LINES)


@dataclass(frozen=True)
class RefundInput:
    idempotency_key: str = field(repr=False)
    body: RefundIn = field(repr=False)


def _line_errors(body: RefundIn) -> list[dict[str, Any]]:
    indexes = [line.line_index for line in body.lines]
    if len(set(indexes)) != len(indexes):
        msg = "each line index may appear only once"
        return [_error(("body", "lines"), "value_error", msg)]
    return []


async def _refund_input(request: Request) -> RefundInput:
    raw = await _json_body(request)
    errors = _key_errors(request)
    body: RefundIn | None = None
    try:
        body = RefundIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if body is not None:
        errors.extend(_line_errors(body))
    if errors or body is None:
        raise RequestValidationError(errors)
    return RefundInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], body=body)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
RefundDep = Annotated[RefundInput, Depends(_refund_input)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/refunds", status_code=201, response_model=RefundOutcome)
def request_refund(
    refund: RefundDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> RefundOutcome | JSONResponse:
    """新写申请 201，按幂等键重放 200。"""
    # 只读订单访问 cookie，不读会员会话。
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    body = refund.body
    order_id = _lookup_order_id(session, cookie_value, body.order_number, now)
    _require_csrf(request, cookie_value)
    lines = [RefundLineRequest(line.line_index, line.quantity) for line in body.lines]
    try:
        outcome, created = submit_refund(session, order_id, lines, refund.idempotency_key, now)
    except IdempotencyConflict:
        return _conflict("idempotency_conflict")
    except OrderNotRefundable:
        return _conflict("order_not_refundable")
    except RefundWindowClosed:
        return _conflict("refund_window_closed")
    except RefundNothingLeft:
        return _conflict("refund_nothing_left")
    except RefundDuplicate:
        return _conflict("refund_duplicate")
    except UnknownOrderLine:
        msg = "line_index is not a line of this order"
        raise RequestValidationError([_error(("body", "lines"), "value_error", msg)]) from None
    if not created:
        response.status_code = 200
    return outcome


# ---------------------------------------------------------------------------
# 积分预估
# ---------------------------------------------------------------------------


class PointsEstimateOut(BaseModel):
    """会员订单与预估的返还、追回积分；游客订单两个数为 0。"""

    member_order: bool
    # 预估实际返还（已过期批次的部分不返还）。
    points_back: int
    # 从余额追回与记入待抵扣之和。
    points_reversed: int


async def _estimate_input(request: Request) -> RefundIn:
    # 与提交申请相同的请求体，只是没有幂等键。
    raw = await _json_body(request)
    errors: list[dict[str, Any]] = []
    body: RefundIn | None = None
    try:
        body = RefundIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if body is not None:
        errors.extend(_line_errors(body))
    if errors or body is None:
        raise RequestValidationError(errors)
    return body


EstimateDep = Annotated[RefundIn, Depends(_estimate_input)]


@router.post("/refunds/points-estimate", response_model=PointsEstimateOut)
def estimate_refund_points_for_lookup(
    body: EstimateDep,
    session: SessionDep,
    request: Request,
) -> PointsEstimateOut | JSONResponse:
    """只读：按提交申请时会选中的件预估积分的返还与追回。"""
    # 只读订单访问 cookie，不读会员会话。
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = _utcnow()
    order_id = _lookup_order_id(session, cookie_value, body.order_number, now)
    order = session.execute(
        select(Order.status, Order.paid_at, Order.member_id).where(Order.id == order_id)
    ).one()
    if order.status not in PAID_STATUSES or order.paid_at is None:
        return _conflict("order_not_refundable")
    if not in_refund_window(order.paid_at, now):
        return _conflict("refund_window_closed")

    # 先确认所有行序都属于该订单（422），再比件数（409），与提交申请的顺序相同。
    state = refund_state(session, order_id)
    picked: list[tuple[int, tuple[RefundUnit, ...]]] = []
    for line in body.lines:
        line_state = state.line(line.line_index)
        if line_state is None:
            msg = "line_index is not a line of this order"
            raise RequestValidationError([_error(("body", "lines"), "value_error", msg)])
        picked.append((line.quantity, line_state.refundable))
    if any(quantity > len(refundable) for quantity, refundable in picked):
        return _conflict("refund_nothing_left")
    chosen = [pick_units(refundable, quantity) for quantity, refundable in picked]
    unit_ids = [unit.unit_id for units in chosen for unit in units]

    if order.member_id is None:
        return PointsEstimateOut(member_order=False, points_back=0, points_reversed=0)
    rows = session.execute(
        select(OrderItemUnit.points_discount, OrderItemUnit.points_earned).where(
            OrderItemUnit.id.in_(unit_ids)
        )
    ).all()
    points_back = sum(row.points_discount for row in rows)
    points_reversed = sum(row.points_earned for row in rows)
    estimate = estimate_refund_points(session, order_id, points_back, points_reversed, now)
    return PointsEstimateOut(
        member_order=True,
        points_back=estimate.returned,
        points_reversed=estimate.reversed_total,
    )

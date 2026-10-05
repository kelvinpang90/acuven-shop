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
   每项为订单行序与 1 到 99 件，行序不重复），多出字段 422。
2. 按请求里的订单号查到订单 ID，以当前 cookie 与 lookup 范围调用 check_order_access（401）。
3. X-CSRF-Token 请求头经 check_csrf_token 校验，不通过 403 {"detail": "csrf_failed"}。
4. 之后才查幂等键与订单（app/services/refunds.py 的 submit_refund）。
新写申请 201，按幂等键重放 200，都是申请的状态、金额与各行件数。
409 的 detail：idempotency_conflict、order_not_refundable、refund_window_closed（页面的
refund.window_closed）、refund_nothing_left（refund.nothing_left）、refund_duplicate
（refund.duplicate）；请求里的行序不属于该订单 422。

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
from sqlalchemy.orm import Session

from app.api.order_lookup import _lookup_order_id, _NoStoreRoute
from app.api.orders import IDEMPOTENCY_HEADER, _error, _key_errors
from app.api.pay import _body_errors, _conflict, _json_body, _require_csrf, _utcnow
from app.db.session import get_session
from app.models.order import ORDER_NUMBER_LENGTH
from app.services.checkout import MAX_CART_LINES, MAX_LINE_QUANTITY
from app.services.order_access import COOKIE_NAME
from app.services.refunds import (
    IdempotencyConflict,
    OrderNotRefundable,
    RefundDuplicate,
    RefundLineRequest,
    RefundNothingLeft,
    RefundOutcome,
    RefundWindowClosed,
    UnknownOrderLine,
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

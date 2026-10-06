"""后台订单与模拟发货 A02 的接口：POST /api/admin/orders/query、
GET /api/admin/orders/{order_id}、POST /api/admin/orders/{order_id}/status。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」「权限与资料保护」与 docs/HANDOFF.md
记录的 Kelvin 2026-10-05 决定，规则与锁定协议见 app/services/admin_orders.py。字段参照
docs/UX.md 0.7 的 A02。订单号与收货资料不进任何路径或查询参数：路径只用订单的内部整数 ID，
订单号搜索在请求体里给。不提供任何导出。

三个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

列表（POST /api/admin/orders/query）只读，不要求 CSRF，不写审计：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有可选的 order_number、可选的 status（六种订单状态之一）与 page（1 到 10000 的
   整数，默认 1），多出字段 422。
2. require_admin（401）。
详情（GET）：require_admin（401）→ 路径里的订单 ID 与语言参数（422）→
订单不存在 404 not_found → 同一请求里写 admin_order_viewed 审计并提交后返回，
另带由 cookie 算出的 CSRF 令牌。
推进（POST …/status）：请求体（413、415、422，规则同列表，只有目标状态 status）→
require_admin（401）→ 路径里的订单 ID（422）→ X-CSRF-Token（403 csrf_failed，
先于读取订单）→ 订单不存在 404 not_found → 推进。409 的 detail 为
order_not_advanceable 或 fulfilment_frozen，都另带订单当前状态 status。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；422 每条错误只给位置、类型与固定消息，不回显请求内容
（请求体同 app/api/pay.py 的 _body_errors，语言参数同 _language，路径里的订单 ID 在这里
自行校验）。不写日志；错误响应不含订单号、收货资料、用户名、令牌或内部细节。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _body_errors, _conflict, _json_body, _language, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_orders import (
    MAX_PAGE,
    AdminOrderDetail,
    AdminOrderPage,
    AdvanceOutcome,
    AdvanceTarget,
    FulfilmentFrozen,
    OrderNotAdvanceable,
    OrderNotFound,
    OrderStatus,
    advance_order,
    query_orders,
    view_order,
)
from app.services.catalog import Language

router = APIRouter(prefix="/api/admin/orders", tags=["admin"], route_class=_NoStoreRoute)

# 订单内部 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限。
_ORDER_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_ORDER_ID = 2**31 - 1


class OrderQueryIn(BaseModel):
    """可选的订单号、可选的状态与页码。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 长度与格式不在这里校验：规范化后不合法时返回空列表而不是错误。
    order_number: str | None = None
    status: OrderStatus | None = None
    page: int = Field(default=1, ge=1, le=MAX_PAGE)


class StatusIn(BaseModel):
    """只有目标状态。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: AdvanceTarget


class AdminOrderDetailOut(AdminOrderDetail):
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交推进。
    csrf_token: str


async def _query_input(request: Request) -> OrderQueryIn:
    raw = await _json_body(request)
    try:
        return OrderQueryIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


async def _status_input(request: Request) -> StatusIn:
    raw = await _json_body(request)
    try:
        return StatusIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


def _order_id(order_id: str) -> int:
    """路径里的订单内部 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _ORDER_ID.fullmatch(order_id) or int(order_id) > MAX_ORDER_ID:
        msg = "Order ID should be a positive integer"
        raise RequestValidationError([_error(("path", "order_id"), "order_id_invalid", msg)])
    return int(order_id)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
QueryDep = Annotated[OrderQueryIn, Depends(_query_input)]
StatusDep = Annotated[StatusIn, Depends(_status_input)]
OrderIdDep = Annotated[int, Depends(_order_id)]
LanguageDep = Annotated[Language, Depends(_language)]
SessionDep = Annotated[Session, Depends(get_session)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


@router.post("/query", response_model=AdminOrderPage)
def query(body: QueryDep, admin: AdminDep, session: SessionDep) -> AdminOrderPage:
    """订单列表：排序与分页见 query_orders；不含收货资料。"""
    return query_orders(session, body.order_number, body.status, body.page)


@router.get("/{order_id}", response_model=AdminOrderDetailOut)
def detail(
    admin: AdminDep,
    order_id: OrderIdDep,
    lang: LanguageDep,
    session: SessionDep,
) -> AdminOrderDetailOut:
    """订单详情含收货资料原文；每次成功返回前写一条审计并提交。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    viewed = view_order(session, admin.account.id, order_id, lang, _utcnow())
    if viewed is None:
        raise _not_found()
    return AdminOrderDetailOut.model_validate({**viewed.model_dump(), "csrf_token": csrf_token})


@router.post("/{order_id}/status", response_model=AdvanceOutcome)
def change_status(
    body: StatusDep,
    admin: AdminDep,
    order_id: OrderIdDep,
    session: SessionDep,
    request: Request,
) -> AdvanceOutcome | JSONResponse:
    """推进成功与已是目标状态都是 200 与订单状态。CSRF 不通过时不读订单。"""
    require_admin_csrf(request, admin)
    account_id = admin.account.id
    try:
        outcome, _ = advance_order(session, account_id, order_id, body.status, _utcnow())
    except OrderNotFound:
        raise _not_found() from None
    except OrderNotAdvanceable as exc:
        return _conflict("order_not_advanceable", exc.status)
    except FulfilmentFrozen as exc:
        return _conflict("fulfilment_frozen", exc.status)
    return outcome

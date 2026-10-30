"""后台 A04 当日库存调整接口：POST /api/admin/variants/{variant_id}/available-stock。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 12 条、docs/UX.md 0.17 的
A04「当日库存调整」与 docs/HANDOFF.md 0.76 的落实写法（库存已变动回 409 stock_changed 并带当前
值，取值不合格回 422 stock_invalid，SKU 不存在回 404 variant_not_found）。规则见
app/services/admin_stock_adjust.py，本文件只做结构与 JSON 类型的校验，取值范围交给服务层。

每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401 admin_session_required）；
3. X-CSRF-Token（403 csrf_failed；照 app/api/admin_coupons.py 先声明 CsrfDep 依赖、再声明
   路径 ID 依赖，403 先于路径的 422）；
4. 路径里的 SKU 规格 ID（照 app/api/admin_orders.py 的 _order_id，不合法 422，只给固定消息）；
5. 请求体结构与 JSON 类型（expected 与 available_stock 两个必填的严格整数，多出字段 422）；
6. 服务层 StockInvalid：422 {"detail": "stock_invalid"}；VariantNotFound：404
   {"detail": "variant_not_found"}；StockChanged：409 {"detail": "stock_changed",
   "available_stock": 当前值}。三种拒绝都不写入、不写审计。
成功（含新值与当前相同、未写入）回 200 {"variant_id", "sku", "available_stock"}。

处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由 SHOP-TASK-027 的路由类
统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。422 每条错误只给位置、
类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。不写日志。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _NO_STORE, _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.services.admin_stock_adjust import (
    StockChanged,
    StockInvalid,
    VariantNotFound,
    adjust_available_stock,
)

router = APIRouter(prefix="/api/admin/variants", tags=["admin"], route_class=_NoStoreRoute)

# SKU 规格内部 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限。
_VARIANT_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_VARIANT_ID = 2**31 - 1


class StockIn(BaseModel):
    """调整前页面显示的当日可用库存与新值；strict：整数不收布尔值、字符串与浮点数。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    expected: int
    available_stock: int


class StockOut(BaseModel):
    variant_id: int
    sku: str
    available_stock: int


async def _adjust_body(request: Request) -> bytes:
    """只在这里判定 413 与 415；结构验证在 CSRF 与路径 ID 之后。"""
    return await _json_body(request)


def _require_admin_csrf(request: Request, admin: AdminDep) -> None:
    """写操作先完成会话与 CSRF 检查，再验证路径参数。"""
    require_admin_csrf(request, admin)


def _variant_id(variant_id: str) -> int:
    """路径里的 SKU 规格 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _VARIANT_ID.fullmatch(variant_id) or int(variant_id) > MAX_VARIANT_ID:
        msg = "Variant ID should be a positive integer"
        raise RequestValidationError([_error(("path", "variant_id"), "variant_id_invalid", msg)])
    return int(variant_id)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
AdjustBodyDep = Annotated[bytes, Depends(_adjust_body)]
CsrfDep = Annotated[None, Depends(_require_admin_csrf)]
VariantIdDep = Annotated[int, Depends(_variant_id)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/{variant_id}/available-stock", response_model=StockOut)
def adjust(
    raw: AdjustBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    variant_id: VariantIdDep,
    session: SessionDep,
) -> StockOut | JSONResponse:
    """413 → 415 → 401 → 403 → 路径 ID → 请求体 → 服务层。"""
    try:
        body = StockIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        sku, stock = adjust_available_stock(
            session, account_id, variant_id, body.expected, body.available_stock, _utcnow()
        )
    except StockInvalid:
        session.rollback()
        raise HTTPException(status_code=422, detail="stock_invalid") from None
    except VariantNotFound:
        # 服务层已回滚。
        raise HTTPException(status_code=404, detail="variant_not_found") from None
    except StockChanged as exc:
        # 服务层已回滚。
        content = {"detail": "stock_changed", "available_stock": exc.available_stock}
        return JSONResponse(status_code=409, content=content, headers=_NO_STORE)
    return StockOut(variant_id=variant_id, sku=sku, available_stock=stock)

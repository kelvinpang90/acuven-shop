"""后台库存重置结果 A07 的接口：GET /api/admin/stock-resets 与
GET /api/admin/stock-resets/{reset_id}。

依据 docs/UX.md 0.10 的 A07，字段取自 SHOP-TASK-032 的 stock_resets 与 stock_reset_lines，
查询与整形见 app/services/admin_stock_resets.py。

两个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。
都只读，不要求 CSRF，不写审计。
列表：require_admin（401）→ 页码 page（1 到 10000 的整数，默认 1；422）→ 返回。
详情：require_admin（401）→ 路径里的重置 ID（422）→ 重置不存在 404 not_found → 返回。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；422 每条错误只给位置、类型与固定消息，不回显所给的值
（写法同 app/api/pay.py 的 _language）。不写日志；不返回尝试次数与异常类名。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.db.session import get_session
from app.services.admin_stock_resets import (
    MAX_PAGE,
    StockResetDetail,
    StockResetPage,
    query_stock_resets,
    stock_reset_detail,
)

router = APIRouter(prefix="/api/admin/stock-resets", tags=["admin"], route_class=_NoStoreRoute)

# 页码与重置内部 ID：不带符号与前导零的十进制整数。
_PAGE = re.compile(r"[1-9][0-9]{0,4}")
_RESET_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_RESET_ID = 2**31 - 1


def _page(page: str = "1") -> int:
    """页码 1 到 MAX_PAGE，默认 1；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _PAGE.fullmatch(page) or int(page) > MAX_PAGE:
        msg = f"Page should be an integer from 1 to {MAX_PAGE}"
        raise RequestValidationError([_error(("query", "page"), "page_invalid", msg)])
    return int(page)


def _reset_id(reset_id: str) -> int:
    """路径里的重置内部 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _RESET_ID.fullmatch(reset_id) or int(reset_id) > MAX_RESET_ID:
        msg = "Stock reset ID should be a positive integer"
        raise RequestValidationError([_error(("path", "reset_id"), "reset_id_invalid", msg)])
    return int(reset_id)


# 处理函数里 require_admin 写在页码与路径 ID 之前：FastAPI 按声明顺序解析依赖，401 先于 422。
PageDep = Annotated[int, Depends(_page)]
ResetIdDep = Annotated[int, Depends(_reset_id)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=StockResetPage)
def query(admin: AdminDep, page: PageDep, session: SessionDep) -> StockResetPage:
    """重置列表：排序与分页见 query_stock_resets。"""
    return query_stock_resets(session, page)


@router.get("/{reset_id}", response_model=StockResetDetail)
def detail(admin: AdminDep, reset_id: ResetIdDep, session: SessionDep) -> StockResetDetail:
    """一次重置与逐 SKU 明细。"""
    found = stock_reset_detail(session, reset_id)
    if found is None:
        raise HTTPException(status_code=404, detail="not_found")
    return found

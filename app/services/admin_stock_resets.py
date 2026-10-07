"""后台库存重置结果 A07 的只读查询：重置列表与单次重置的逐 SKU 明细。

依据 docs/UX.md 0.10 的 A07（日期、结果、SKU 数，展开后每个 SKU 的
admin.stock_reset_breakdown：初始库存、仍有效预留、当日可用）与 docs/DESIGN.md 1.11
（提交 2d13250）「计价、优惠、积分与库存」第 6 条。字段取自 SHOP-TASK-032 的
stock_resets 与 stock_reset_lines（app/models/stock_reset.py）；本模块不改重置本身
（app/services/stock_reset.py）。

只读：不写审计、不写日志、不提交。不返回尝试次数、首次开始时间与异常类名；明细不返回规格 ID
（规格被删除时为空，明细凭 SKU 快照照常返回）。访问授权与请求格式由调用方
（app/api/admin_stock_resets.py）先行校验。库里的时间是不带时区的 UTC，返回的视图里换成带
UTC 时区的时间。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StockReset, StockResetLine

PAGE_SIZE = 30
MAX_PAGE = 10000


class StockResetRow(BaseModel):
    """一次重置：营业日期（马来西亚）、结果、SKU 数与完成时间（失败时为空）。"""

    id: int
    business_date: date
    result: str
    sku_count: int
    completed_at: datetime | None


class StockResetPage(BaseModel):
    """全部重置的次数与本页各次（按营业日期从新到旧）。"""

    total: int
    page: int
    page_size: int
    resets: list[StockResetRow]


class StockResetLineOut(BaseModel):
    """一个 SKU 的 admin.stock_reset_breakdown。"""

    sku: str
    initial_stock: int
    held_quantity: int
    available_stock: int


class StockResetDetail(StockResetRow):
    # 按 SKU 升序；失败的重置没有明细。
    lines: list[StockResetLineOut]


_ROW_COLUMNS = (
    StockReset.id,
    StockReset.business_date,
    StockReset.result,
    StockReset.sku_count,
    StockReset.completed_at,
)


def _row(
    reset_id: int,
    business_date: date,
    result: str,
    sku_count: int,
    completed_at: datetime | None,
) -> StockResetRow:
    return StockResetRow(
        id=reset_id,
        business_date=business_date,
        result=result,
        sku_count=sku_count,
        completed_at=None if completed_at is None else completed_at.replace(tzinfo=UTC),
    )


def query_stock_resets(db: Session, page: int) -> StockResetPage:
    """重置列表：按营业日期从新到旧（营业日期全表唯一），每页 PAGE_SIZE 条。

    页码（1 到 MAX_PAGE）由调用方校验；超出时本页为空，总数照常。
    """
    total = int(db.scalar(select(func.count()).select_from(StockReset)) or 0)
    stmt = (
        select(*_ROW_COLUMNS)
        .order_by(StockReset.business_date.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    )
    resets = [_row(*row) for row in db.execute(stmt).tuples()]
    return StockResetPage(total=total, page=page, page_size=PAGE_SIZE, resets=resets)


def stock_reset_detail(db: Session, reset_id: int) -> StockResetDetail | None:
    """一次重置与它的逐 SKU 明细（按 SKU 升序）；重置不存在时为空。"""
    stmt = select(*_ROW_COLUMNS).where(StockReset.id == reset_id)
    row = db.execute(stmt).tuples().one_or_none()
    if row is None:
        return None
    line_stmt = (
        select(
            StockResetLine.sku,
            StockResetLine.initial_stock,
            StockResetLine.held_quantity,
            StockResetLine.available_stock,
        )
        .where(StockResetLine.stock_reset_id == reset_id)
        .order_by(StockResetLine.sku)
    )
    lines = [
        StockResetLineOut(
            sku=sku,
            initial_stock=initial_stock,
            held_quantity=held_quantity,
            available_stock=available_stock,
        )
        for sku, initial_stock, held_quantity, available_stock in db.execute(line_stmt).tuples()
    ]
    return StockResetDetail(**_row(*row).model_dump(), lines=lines)

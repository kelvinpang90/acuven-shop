"""后台当日库存调整：把某个 SKU 的当日可用库存直接改为一个值。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 12 条（下称「第 12 条」）：
「管理员可把某个 SKU（含停用的）的当日可用库存直接改为一个值（0–9,999 的整数），不改每日初始
库存」「请求带调整前页面显示的当日可用库存，服务端以一条带条件的更新写入（当前值仍等于该值才
写）：不等即回答『库存已变动』并返回当前的当日可用库存、不写入」「先比调整前的值：相等之后，新值
与当前值相同时照常回答成功，不写入、不记审计」「调整不改已有预留，也不改订单，不改商品的版本号；
每次写入写一条审计（对象为该 SKU，旧值与新值写成『SKU 代码 available_stock 值』）」；docs/UX.md
0.17 的 A04 与 docs/HANDOFF.md 0.76 的落实写法（审计操作名 admin_variant_stock_adjusted）。

只改 product_variants.available_stock 一列：不改 daily_initial_stock，不碰订单、预留与商品的
version。下一次每日重置（app/services/stock_reset.py）照初始库存减去仍有效的预留重算，调整不
延续到那次重置之后。接口见 app/api/admin_stock_adjust.py。本模块不写日志。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Update, select, update
from sqlalchemy.orm import Session

from app.models import ProductVariant
from app.services.admin_auth import record_audit

# 审计操作名与对象类别（对象 ID 为 SKU 规格的 ID）。
ADMIN_VARIANT_STOCK_ADJUSTED = "admin_variant_stock_adjusted"
AUDIT_TARGET_PRODUCT_VARIANT = "product_variant"

# 第 12 条「0–9,999 的整数」。
MIN_STOCK = 0
MAX_STOCK = 9_999


class StockInvalid(Exception):
    """调整前的值或新值不是 0 到 9,999 的整数（布尔值不算）。"""


class VariantNotFound(Exception):
    """SKU 不存在。"""


class StockChanged(Exception):
    """调整前的值与当前的当日可用库存不等；available_stock 为当前值。"""

    def __init__(self, available_stock: int) -> None:
        super().__init__("stock_changed")
        self.available_stock = available_stock


def _valid_stock(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and MIN_STOCK <= value <= MAX_STOCK
    )


def _audit_value(sku: str, stock: int) -> str:
    return f"{sku} available_stock {stock}"


def adjust_stock_statement(variant_id: int, expected: int, new_value: int) -> Update:
    """带条件的更新：当前值仍等于调整前的值才写。"""
    return (
        update(ProductVariant)
        .where(ProductVariant.id == variant_id, ProductVariant.available_stock == expected)
        .values(available_stock=new_value)
        .execution_options(synchronize_session=False)
    )


def _current(db: Session, variant_id: int) -> tuple[str, int]:
    """只查列值读出 SKU 代码与当前的当日可用库存；SKU 不存在抛 VariantNotFound。"""
    stmt = select(ProductVariant.sku, ProductVariant.available_stock)
    row = db.execute(stmt.where(ProductVariant.id == variant_id)).tuples().one_or_none()
    if row is None:
        raise VariantNotFound
    return row


def adjust_available_stock(
    db: Session,
    admin_account_id: int,
    variant_id: int,
    expected: int,
    new_value: int,
    now: datetime,
) -> tuple[str, int]:
    """把 SKU 的当日可用库存从 expected 改为 new_value，返回 SKU 代码与调整后的当日可用库存。

    admin_account_id 须在调用前取出（这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. 不碰数据库检查 expected 与 new_value 都是 0 到 9,999 的整数（布尔值不算），不通过抛
       StockInvalid。
    2. 回滚此前的事务（此前只有读取）。
    3. 两值不同：执行 adjust_stock_statement 这一条带条件的 UPDATE。命中一行即在同一事务里读出
       该行的 SKU 代码，写一条 admin_variant_stock_adjusted 审计（对象为该 SKU，旧值与新值为
       「SKU 代码 available_stock 值」）后提交；未命中则回滚，再读出当前值：SKU 不存在抛
       VariantNotFound，存在抛 StockChanged（带当前值）。
    4. 两值相同：只读出当前值后回滚，等于 expected 即成功（不写入、不记审计），不等抛
       StockChanged，SKU 不存在抛 VariantNotFound。
    停用的 SKU 照样可调。
    """
    if not _valid_stock(expected) or not _valid_stock(new_value):
        raise StockInvalid

    db.rollback()
    if expected == new_value:
        try:
            sku, current = _current(db, variant_id)
        finally:
            db.rollback()
        if current != expected:
            raise StockChanged(current)
        return sku, current

    if db.execute(adjust_stock_statement(variant_id, expected, new_value)).rowcount != 1:
        db.rollback()
        try:
            _, current = _current(db, variant_id)
        finally:
            db.rollback()
        raise StockChanged(current)

    sku, _ = _current(db, variant_id)
    record_audit(
        db,
        ADMIN_VARIANT_STOCK_ADJUSTED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_PRODUCT_VARIANT,
        target_id=variant_id,
        old_value=_audit_value(sku, expected),
        new_value=_audit_value(sku, new_value),
    )
    db.commit()
    return sku, new_value

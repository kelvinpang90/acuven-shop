"""每日库存重置记录与逐 SKU 明细。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 6 条（每天按马来西亚时间
重建当日可用库存为“初始库存减去仍有效的预留”，记录重置事件；历史订单不变）与「失败、并发与
重试」第 6 条（每日库存重置可重复运行，使用操作标识保证只生效一次）。字段供之后的后台 A07
按 docs/UX.md 0.7 显示：日期、结果、SKU 数，以及每个 SKU 的 admin.stock_reset_breakdown
（初始、有效预留、当日可用）。

马来西亚营业日期全表唯一，即操作标识。时间一律是不带时区的 UTC。不存任何个人资料或订单号；
失败只记异常类名，不存异常消息原文。重置记录与明细长期保存，明细到重置记录的外键 RESTRICT；
明细到 SKU 规格的外键在规格被删除时置空，明细靠 SKU 快照照常显示。

检查约束的写法与 app/models/order.py 相同（只用比较、算术、CASE、IN 与 IS NULL），MySQL 与
SQLite 都能执行。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base
from app.models.order import _sql_in

# 重置结果。
RESET_SUCCEEDED = "succeeded"
RESET_FAILED = "failed"

RESET_RESULTS = (RESET_SUCCEEDED, RESET_FAILED)

ERROR_CLASS_LENGTH = 100


class StockReset(Base):
    """一个马来西亚营业日期的库存重置：结果、尝试次数、首次开始与完成时间、SKU 数、最近一次
    失败的异常类名。

    succeeded 时必有完成时间，failed 时必无；失败后重试成功时由 failed 改为 succeeded，
    永远不由 succeeded 改回 failed（由 app/services/stock_reset.py 的带条件 UPDATE 保证）。
    """

    __tablename__ = "stock_resets"
    __table_args__ = (
        UniqueConstraint("business_date"),
        CheckConstraint(f"result IN {_sql_in(RESET_RESULTS)}", name="result_valid"),
        CheckConstraint("attempts >= 1", name="attempts_positive"),
        CheckConstraint("sku_count >= 0", name="sku_count_non_negative"),
        # 分成两条，未知结果只由 result_valid 拒绝。
        CheckConstraint(
            f"result <> '{RESET_SUCCEEDED}' OR completed_at IS NOT NULL",
            name="succeeded_has_completed_at",
        ),
        CheckConstraint(
            f"result <> '{RESET_FAILED}' OR completed_at IS NULL",
            name="failed_has_no_completed_at",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 马来西亚营业日期（UTC+8 切日），即操作标识。
    business_date: Mapped[date] = mapped_column(Date)
    result: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer)
    # 首次开始时间；之后的重试不改。
    started_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sku_count: Mapped[int] = mapped_column(Integer)
    # 最近一次失败的异常类名；成功时清空。
    error_class: Mapped[str | None] = mapped_column(String(ERROR_CLASS_LENGTH))


class StockResetLine(Base):
    """一次重置里的一个 SKU：初始库存、有效预留与当日可用（admin.stock_reset_breakdown）。"""

    __tablename__ = "stock_reset_lines"
    __table_args__ = (
        UniqueConstraint("stock_reset_id", "sku"),
        CheckConstraint("initial_stock >= 0", name="initial_stock_non_negative"),
        CheckConstraint("held_quantity >= 0", name="held_quantity_non_negative"),
        CheckConstraint("available_stock >= 0", name="available_stock_non_negative"),
        CheckConstraint(
            "available_stock = CASE WHEN initial_stock > held_quantity"
            " THEN initial_stock - held_quantity ELSE 0 END",
            name="available_stock_formula",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_reset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stock_resets.id", ondelete="RESTRICT"),
    )
    # 规格被删除时置空，明细凭 SKU 快照照常显示。
    variant_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("product_variants.id", ondelete="SET NULL"),
    )
    sku: Mapped[str] = mapped_column(String(64))
    initial_stock: Mapped[int] = mapped_column(Integer)
    held_quantity: Mapped[int] = mapped_column(Integer)
    available_stock: Mapped[int] = mapped_column(Integer)

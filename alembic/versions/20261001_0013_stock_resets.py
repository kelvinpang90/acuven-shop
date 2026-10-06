"""Daily stock reset records and per-SKU reset lines.

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 6 条与「失败、并发与重试」
第 6 条，与 app/models/stock_reset.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；营业日期是马来西亚日期（UTC+8 切日），全表唯一，即操作标识。

明细到重置记录的外键 RESTRICT，以（重置记录, SKU 快照）唯一约束的第一列为可用索引，不另建
普通索引；明细到 SKU 规格的外键在规格被删除时置空，与 order_items.variant_id 一样由 MySQL
自行建索引。

downgrade 按依赖倒序删表：先删明细，再删重置记录。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "stock_resets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("result", sa.String(length=10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("sku_count", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name=op.f("ck_stock_resets_result_valid"),
        ),
        sa.CheckConstraint(
            "attempts >= 1",
            name=op.f("ck_stock_resets_attempts_positive"),
        ),
        sa.CheckConstraint(
            "sku_count >= 0",
            name=op.f("ck_stock_resets_sku_count_non_negative"),
        ),
        sa.CheckConstraint(
            "result <> 'succeeded' OR completed_at IS NOT NULL",
            name=op.f("ck_stock_resets_succeeded_has_completed_at"),
        ),
        sa.CheckConstraint(
            "result <> 'failed' OR completed_at IS NULL",
            name=op.f("ck_stock_resets_failed_has_no_completed_at"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_resets")),
        sa.UniqueConstraint(
            "business_date",
            name=op.f("uq_stock_resets_business_date"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "stock_reset_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_reset_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("initial_stock", sa.Integer(), nullable=False),
        sa.Column("held_quantity", sa.Integer(), nullable=False),
        sa.Column("available_stock", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "initial_stock >= 0",
            name=op.f("ck_stock_reset_lines_initial_stock_non_negative"),
        ),
        sa.CheckConstraint(
            "held_quantity >= 0",
            name=op.f("ck_stock_reset_lines_held_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "available_stock >= 0",
            name=op.f("ck_stock_reset_lines_available_stock_non_negative"),
        ),
        sa.CheckConstraint(
            "available_stock = CASE WHEN initial_stock > held_quantity"
            " THEN initial_stock - held_quantity ELSE 0 END",
            name=op.f("ck_stock_reset_lines_available_stock_formula"),
        ),
        sa.ForeignKeyConstraint(
            ["stock_reset_id"],
            ["stock_resets.id"],
            name=op.f("fk_stock_reset_lines_stock_reset_id_stock_resets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_stock_reset_lines_variant_id_product_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_reset_lines")),
        sa.UniqueConstraint(
            "stock_reset_id",
            "sku",
            name=op.f("uq_stock_reset_lines_stock_reset_id_sku"),
        ),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("stock_reset_lines")
    op.drop_table("stock_resets")

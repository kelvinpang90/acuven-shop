"""Product purchase limit: max_per_order column and its range check.

依据 docs/DESIGN.md 1.10（提交 e3b3505）「数据模型」的 Product / Variant 一行：
商品级的每单限购件数，非空整数，1–99，默认 10，不按规格分别设置；
与 app/models/catalog.py 一致。
约束名按 app/db/base.py 的命名约定写出。
alembic check 不比较检查约束与服务端默认值，改这里或模型时须人工核对两边一致。

已有商品由服务端默认值补成 10；默认值保留，与模型的 server_default 一致。
downgrade 先删检查约束再删列：MySQL 不许删除仍被检查约束引用的列。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("max_per_order", sa.Integer(), nullable=False, server_default="10"),
    )
    op.create_check_constraint(
        op.f("ck_products_max_per_order_range"),
        "products",
        "max_per_order >= 1 AND max_per_order <= 99",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_products_max_per_order_range"), "products", type_="check")
    op.drop_column("products", "max_per_order")

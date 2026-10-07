"""Store design: theme and accent, home page blocks, featured products.

依据 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01，以及 docs/HANDOFF.md 0.34
记录的 Kelvin 2026-10-06 决定（存储首版不含标志图），与 app/models/store_design.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；精选商品到商品的外键 RESTRICT，由商品列上的唯一约束充当外键索引。

upgrade 建三张表并写入默认值：设置一行（主题 pandan，主色为空即该主题的默认主色，更新时间为
执行迁移时的 UTC）；四个区块按 hero、how、categories、featured 的顺序全部显示；没有精选商品。
主题 id 与默认值是这里的常量副本（迁移不 import app，模型以后改了旧迁移不跟着变），
与模型常量一致由 tests/test_store_design_models.py 守住。
downgrade 按依赖倒序删表。迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# docs/design/tokens/themes.json 的 10 款主题 id，与 app.models.store_design.THEMES 相同。
THEMES = (
    "pandan",
    "pasar",
    "receipt",
    "kopitiam",
    "batik",
    "malam",
    "gula",
    "galeri",
    "songket",
    "litar",
)
HOME_BLOCKS = ("hero", "how", "categories", "featured")

DEFAULT_THEME = "pandan"
DEFAULT_ACCENT = None
# （区块、位置、是否显示）
DEFAULT_HOME_BLOCKS = (
    ("hero", 1, True),
    ("how", 2, True),
    ("categories", 3, True),
    ("featured", 4, True),
)


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    settings = op.create_table(
        "store_design_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("singleton_slot", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=20), nullable=False),
        sa.Column("accent", sa.String(length=20), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "singleton_slot = 1",
            name=op.f("ck_store_design_settings_singleton_slot_one"),
        ),
        sa.CheckConstraint(
            f"theme IN ({_in_list(THEMES)})",
            name=op.f("ck_store_design_settings_theme_known"),
        ),
        sa.CheckConstraint(
            "accent IS NULL OR (LENGTH(accent) >= 1 AND LENGTH(accent) <= 20)",
            name=op.f("ck_store_design_settings_accent_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_design_settings")),
        sa.UniqueConstraint("singleton_slot", name=op.f("uq_store_design_settings_singleton_slot")),
        **TABLE_OPTIONS,
    )
    blocks = op.create_table(
        "store_home_blocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("block", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            f"block IN ({_in_list(HOME_BLOCKS)})",
            name=op.f("ck_store_home_blocks_block_known"),
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 4",
            name=op.f("ck_store_home_blocks_position_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_home_blocks")),
        sa.UniqueConstraint("block", name=op.f("uq_store_home_blocks_block")),
        sa.UniqueConstraint("position", name=op.f("uq_store_home_blocks_position")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "store_featured_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 1 AND position <= 4",
            name=op.f("ck_store_featured_products_position_range"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_store_featured_products_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_featured_products")),
        sa.UniqueConstraint("position", name=op.f("uq_store_featured_products_position")),
        sa.UniqueConstraint("product_id", name=op.f("uq_store_featured_products_product_id")),
        **TABLE_OPTIONS,
    )
    op.bulk_insert(
        settings,
        [
            {
                "singleton_slot": 1,
                "theme": DEFAULT_THEME,
                "accent": DEFAULT_ACCENT,
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
        ],
    )
    op.bulk_insert(
        blocks,
        [
            {"block": block, "position": position, "is_visible": is_visible}
            for block, position, is_visible in DEFAULT_HOME_BLOCKS
        ],
    )


def downgrade() -> None:
    op.drop_table("store_featured_products")
    op.drop_table("store_home_blocks")
    op.drop_table("store_design_settings")

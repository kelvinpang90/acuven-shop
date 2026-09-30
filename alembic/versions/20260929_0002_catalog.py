"""Catalog tables: categories, products, images, options, option values, variants.

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行，
与 app/models/catalog.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column("name_zh", sa.String(length=200), nullable=True),
        sa.Column("name_ms", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("slug", name=op.f("uq_categories_slug")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column("name_zh", sa.String(length=200), nullable=True),
        sa.Column("name_ms", sa.String(length=200), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("description_zh", sa.Text(), nullable=True),
        sa.Column("description_ms", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_products_category_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("slug", name=op.f("uq_products_slug")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("storage_ref", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_images_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_images")),
        sa.UniqueConstraint(
            "product_id",
            "sort_order",
            name=op.f("uq_product_images_product_id_sort_order"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "product_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("name_en", sa.String(length=100), nullable=True),
        sa.Column("name_zh", sa.String(length=100), nullable=True),
        sa.Column("name_ms", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_options_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_options")),
        sa.UniqueConstraint(
            "product_id",
            "code",
            name=op.f("uq_product_options_product_id_code"),
        ),
        sa.UniqueConstraint(
            "id",
            "product_id",
            name=op.f("uq_product_options_id_product_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "product_option_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("name_en", sa.String(length=100), nullable=True),
        sa.Column("name_zh", sa.String(length=100), nullable=True),
        sa.Column("name_ms", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(
            ["option_id", "product_id"],
            ["product_options.id", "product_options.product_id"],
            name=op.f("fk_product_option_values_option_id_product_options"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_option_values")),
        sa.UniqueConstraint(
            "option_id",
            "code",
            name=op.f("uq_product_option_values_option_id_code"),
        ),
        sa.UniqueConstraint(
            "id",
            "option_id",
            "product_id",
            name=op.f("uq_product_option_values_id_option_id_product_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("price_sen", sa.Integer(), nullable=False),
        sa.Column("daily_initial_stock", sa.Integer(), nullable=False),
        sa.Column("available_stock", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "price_sen >= 0",
            name=op.f("ck_product_variants_price_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "daily_initial_stock >= 0",
            name=op.f("ck_product_variants_daily_initial_stock_non_negative"),
        ),
        sa.CheckConstraint(
            "available_stock >= 0",
            name=op.f("ck_product_variants_available_stock_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_variants_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_variants")),
        sa.UniqueConstraint("sku", name=op.f("uq_product_variants_sku")),
        sa.UniqueConstraint(
            "id",
            "product_id",
            name=op.f("uq_product_variants_id_product_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "variant_option_values",
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("option_value_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("option_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["variant_id", "product_id"],
            ["product_variants.id", "product_variants.product_id"],
            name=op.f("fk_variant_option_values_variant_id_product_variants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_value_id", "option_id", "product_id"],
            [
                "product_option_values.id",
                "product_option_values.option_id",
                "product_option_values.product_id",
            ],
            name=op.f("fk_variant_option_values_option_value_id_product_option_values"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "variant_id",
            "option_value_id",
            name=op.f("pk_variant_option_values"),
        ),
        sa.UniqueConstraint(
            "variant_id",
            "option_id",
            name=op.f("uq_variant_option_values_variant_id_option_id"),
        ),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("variant_option_values")
    op.drop_table("product_variants")
    op.drop_table("product_option_values")
    op.drop_table("product_options")
    op.drop_table("product_images")
    op.drop_table("products")
    op.drop_table("categories")

"""Admin catalog schema: category and product versions, image metadata, purchase limit 1–1000,
and the daily stock reset switch.

依据 docs/DESIGN.md 1.18（提交 5ba9796）「数据模型」的 Product / Variant 一行与 SiteSetting 一行、
「计价、优惠、积分与库存」第 6、8、11、13、14 条的数据部分，以及 docs/HANDOFF.md 0.74 记录的
落实写法；与 app/models/catalog.py、app/models/site.py 一致。约束名按 app/db/base.py 的命名约定
逐个写出（照 0023）。alembic check 不比较检查约束与服务端默认值，改这里或模型时须人工核对两边一致。

- categories、products 加 version（非空整数，服务端默认 1，检查约束 version >= 1）；
  已有的行由服务端默认值填成 1，不另写数据。
- products 的 max_per_order_range 由 1–99 改为 1–1000（先删后建）；列的服务端默认 10 不变。
- product_images 加 file_name（唯一）、source_format、width、height、byte_size 五个可空列与
  三个检查约束；已有的行（示例数据的占位图引用）五列全为空。
- site_settings 加 daily_stock_reset_enabled（非空布尔，服务端默认开启）；已有的那一行取默认值。

执行顺序：改约束与删列在 MySQL 上是 DDL，会隐式提交，不随迁移的事务回滚。
- upgrade：先加列（已有的行由服务端默认值填好，新约束建立时即可通过），再建检查约束与
  file_name 的唯一约束，最后删去 1–99 的 max_per_order_range 并建 1–1000 的约束。
- downgrade：先读出 max_per_order 大于 99 的商品数与 file_name 不为空的图片行数，任一大于 0 即
  抛 RuntimeError，此时还没有改动，库保持原样（限购大于 99 的商品放不进 1–99 的约束；删掉文件名
  会让后台上传的图片永远找不到文件）；判断通过后先删新增的检查约束与 file_name 的唯一约束
  （MySQL 8.0 不许删除被检查约束引用的列），再删新增的列，最后删去 1–1000 的
  max_per_order_range 并建回 1–99 的约束。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0023）：模型以后改了，这个迁移不跟着变。
OLD_MAX_PER_ORDER = 99

MAX_PER_ORDER_CONSTRAINT_NAME = "ck_products_max_per_order_range"
# 与 0008 建的约束逐字相同。
OLD_MAX_PER_ORDER_CONSTRAINT = "max_per_order >= 1 AND max_per_order <= 99"
# 以下与 app/models/catalog.py、app/models/site.py 逐字相同。
NEW_MAX_PER_ORDER_CONSTRAINT = "max_per_order >= 1 AND max_per_order <= 1000"
VERSION_CONSTRAINT = "version >= 1"
IMAGE_METADATA_CONSTRAINT = (
    "(file_name IS NULL AND source_format IS NULL AND width IS NULL"
    " AND height IS NULL AND byte_size IS NULL)"
    " OR (file_name IS NOT NULL AND source_format IS NOT NULL AND width IS NOT NULL"
    " AND height IS NOT NULL AND byte_size IS NOT NULL)"
)
IMAGE_SOURCE_FORMAT_CONSTRAINT = "source_format IS NULL OR source_format IN ('jpeg', 'png', 'webp')"
IMAGE_DISPLAY_SIZE_CONSTRAINT = (
    "width IS NULL OR (width >= 1 AND width <= 1600"
    " AND height >= 1 AND height <= 1600 AND byte_size >= 1)"
)

# (约束名, 表, 文本)：upgrade 按这个顺序建，downgrade 倒序删。
CHECK_CONSTRAINTS = (
    ("ck_categories_version_positive", "categories", VERSION_CONSTRAINT),
    ("ck_products_version_positive", "products", VERSION_CONSTRAINT),
    ("ck_product_images_metadata_all_or_none", "product_images", IMAGE_METADATA_CONSTRAINT),
    ("ck_product_images_source_format_valid", "product_images", IMAGE_SOURCE_FORMAT_CONSTRAINT),
    ("ck_product_images_display_size_range", "product_images", IMAGE_DISPLAY_SIZE_CONSTRAINT),
)
FILE_NAME_UNIQUE = "uq_product_images_file_name"
IMAGE_METADATA_COLUMNS = ("file_name", "source_format", "width", "height", "byte_size")

products = sa.table("products", sa.column("max_per_order", sa.Integer))
product_images = sa.table("product_images", sa.column("file_name", sa.String))


def _count(table: sa.TableClause, condition: sa.ColumnElement[bool]) -> int:
    stmt = sa.select(sa.func.count()).select_from(table).where(condition)
    return op.get_bind().execute(stmt).scalar_one()


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("product_images", sa.Column("file_name", sa.String(length=32), nullable=True))
    op.add_column("product_images", sa.Column("source_format", sa.String(length=4), nullable=True))
    op.add_column("product_images", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("product_images", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column("product_images", sa.Column("byte_size", sa.Integer(), nullable=True))
    op.add_column(
        "site_settings",
        sa.Column(
            "daily_stock_reset_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    for name, table, condition in CHECK_CONSTRAINTS:
        op.create_check_constraint(op.f(name), table, condition)
    op.create_unique_constraint(op.f(FILE_NAME_UNIQUE), "product_images", ["file_name"])

    op.drop_constraint(op.f(MAX_PER_ORDER_CONSTRAINT_NAME), "products", type_="check")
    op.create_check_constraint(
        op.f(MAX_PER_ORDER_CONSTRAINT_NAME), "products", NEW_MAX_PER_ORDER_CONSTRAINT
    )


def downgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    over_old_limit = _count(products, products.c.max_per_order > OLD_MAX_PER_ORDER)
    uploaded_images = _count(product_images, product_images.c.file_name.is_not(None))
    if over_old_limit or uploaded_images:
        raise RuntimeError(
            "cannot downgrade 0024: "
            f"{over_old_limit} products with max_per_order > {OLD_MAX_PER_ORDER}, "
            f"{uploaded_images} product images with a file_name"
        )

    for name, table, _condition in reversed(CHECK_CONSTRAINTS):
        op.drop_constraint(op.f(name), table, type_="check")
    op.drop_constraint(op.f(FILE_NAME_UNIQUE), "product_images", type_="unique")

    op.drop_column("site_settings", "daily_stock_reset_enabled")
    for column in reversed(IMAGE_METADATA_COLUMNS):
        op.drop_column("product_images", column)
    op.drop_column("products", "version")
    op.drop_column("categories", "version")

    op.drop_constraint(op.f(MAX_PER_ORDER_CONSTRAINT_NAME), "products", type_="check")
    op.create_check_constraint(
        op.f(MAX_PER_ORDER_CONSTRAINT_NAME), "products", OLD_MAX_PER_ORDER_CONSTRAINT
    )

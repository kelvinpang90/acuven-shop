"""Store logo: five nullable logo columns and three check constraints on store_design_settings.

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条「记录」一段，以及
docs/HANDOFF.md 0.84 记录的落实写法；与 app/models/store_design.py 一致。约束名按
app/db/base.py 的命名约定逐个写出（照 0024）。alembic check 不比较检查约束，改这里或模型时须
人工核对两边一致。

- store_design_settings 加 logo_file_name、logo_source_format、logo_width、logo_height、
  logo_byte_size 五个可空列与三个检查约束（照 product_images 的五列与三个约束加 logo_ 前缀）；
  已有的那一行五列全为空（没有标志）。单例行只有一行，文件名不另加唯一约束。

执行顺序：加删约束与列在 MySQL 上是 DDL，会隐式提交，不随迁移的事务回滚。
- upgrade：先加五列（已有的那一行五列全为空，新约束建立时即可通过），再建三个检查约束。
- downgrade：先读出 logo_file_name 不为空的行数，大于 0 即抛 RuntimeError，此时还没有改动，
  库保持原样（删掉文件名会让存储桶里的标志成为孤立对象、前台失去标志）；判断通过后先删三个
  检查约束（MySQL 8.0 不许删除被检查约束引用的列），再删五列。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0024）：模型以后改了，这个迁移不跟着变。
TABLE = "store_design_settings"

# 以下与 app/models/store_design.py 逐字相同。
LOGO_ALL_OR_NONE_CONSTRAINT = (
    "(logo_file_name IS NULL AND logo_source_format IS NULL AND logo_width IS NULL"
    " AND logo_height IS NULL AND logo_byte_size IS NULL)"
    " OR (logo_file_name IS NOT NULL AND logo_source_format IS NOT NULL"
    " AND logo_width IS NOT NULL AND logo_height IS NOT NULL"
    " AND logo_byte_size IS NOT NULL)"
)
LOGO_SOURCE_FORMAT_CONSTRAINT = (
    "logo_source_format IS NULL OR logo_source_format IN ('jpeg', 'png', 'webp')"
)
LOGO_DISPLAY_SIZE_CONSTRAINT = (
    "logo_width IS NULL OR (logo_width >= 1 AND logo_width <= 1600"
    " AND logo_height >= 1 AND logo_height <= 1600 AND logo_byte_size >= 1)"
)

# (约束名, 文本)：upgrade 按这个顺序建，downgrade 倒序删。
CHECK_CONSTRAINTS = (
    ("ck_store_design_settings_logo_all_or_none", LOGO_ALL_OR_NONE_CONSTRAINT),
    ("ck_store_design_settings_logo_source_format_valid", LOGO_SOURCE_FORMAT_CONSTRAINT),
    ("ck_store_design_settings_logo_display_size_range", LOGO_DISPLAY_SIZE_CONSTRAINT),
)
LOGO_COLUMNS = (
    "logo_file_name",
    "logo_source_format",
    "logo_width",
    "logo_height",
    "logo_byte_size",
)

store_design_settings = sa.table(TABLE, sa.column("logo_file_name", sa.String))


def _count(table: sa.TableClause, condition: sa.ColumnElement[bool]) -> int:
    stmt = sa.select(sa.func.count()).select_from(table).where(condition)
    return op.get_bind().execute(stmt).scalar_one()


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("logo_file_name", sa.String(length=32), nullable=True))
    op.add_column(TABLE, sa.Column("logo_source_format", sa.String(length=4), nullable=True))
    op.add_column(TABLE, sa.Column("logo_width", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("logo_height", sa.Integer(), nullable=True))
    op.add_column(TABLE, sa.Column("logo_byte_size", sa.Integer(), nullable=True))

    for name, condition in CHECK_CONSTRAINTS:
        op.create_check_constraint(op.f(name), TABLE, condition)


def downgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    with_logo = _count(store_design_settings, store_design_settings.c.logo_file_name.is_not(None))
    if with_logo:
        raise RuntimeError(
            f"cannot downgrade 0025: {with_logo} store design settings with a logo_file_name"
        )

    for name, _condition in reversed(CHECK_CONSTRAINTS):
        op.drop_constraint(op.f(name), TABLE, type_="check")

    for column in reversed(LOGO_COLUMNS):
        op.drop_column(TABLE, column)

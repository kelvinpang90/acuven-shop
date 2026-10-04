"""Site settings: the SMS verification switch.

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条与「数据模型」的 SiteSetting 一行，
与 app/models/site.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束、服务端默认值与表选项，改这里或模型时须人工核对两边一致。

upgrade 建表并写入唯一一行（主键 1，开关关闭，更新时间为执行迁移时的 UTC）；downgrade 删表。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    site_settings = op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "sms_verification_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_site_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_settings")),
        **TABLE_OPTIONS,
    )
    op.bulk_insert(
        site_settings,
        [
            {
                "id": 1,
                "sms_verification_enabled": False,
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("site_settings")

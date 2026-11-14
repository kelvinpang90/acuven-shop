"""Promotion AI proposals: daily call count and per-call records.

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条「限制」
一段，以及 SHOP-TASK-246 写定的表名、列名、类型与约束；与
app/models/promotion_ai.py 一致。约束名按 app/db/base.py 的命名约定逐个写出
（照 0027）。alembic check 不比较检查约束与表选项，改这里或模型时须人工核对
两边一致；测试逐字核对本文件的检查约束常量与模型。时间是不带时区的 UTC。

- promotion_ai_daily_usage：每日调用计数，以马来西亚日期为主键（每天至多
  一行），不另设 id 列。
- promotion_ai_calls：每次调用的时间、结果类别与输入、输出 token 数；
  不记描述与回答原文，不建外键。

执行顺序：
- upgrade：建两张表（互不引用）。
- downgrade：直接删两张表。表里只是计数与调用记录，不影响订单与活动。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0027）：模型以后改了，这个迁移不跟着变。
# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# 建表顺序；downgrade 倒序删。
TABLES = (
    "promotion_ai_daily_usage",
    "promotion_ai_calls",
)

# 以下与 app/models/promotion_ai.py 逐字相同。表名 → ((约束名, 文本), ...)。
CHECK_CONSTRAINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "promotion_ai_daily_usage": (
        ("ck_promotion_ai_daily_usage_call_count_non_negative", "call_count >= 0"),
    ),
    "promotion_ai_calls": (
        (
            "ck_promotion_ai_calls_result_valid",
            "result IN ('ok', 'invalid', 'timeout', 'error')",
        ),
        (
            "ck_promotion_ai_calls_input_tokens_non_negative",
            "input_tokens IS NULL OR input_tokens >= 0",
        ),
        (
            "ck_promotion_ai_calls_output_tokens_non_negative",
            "output_tokens IS NULL OR output_tokens >= 0",
        ),
    ),
}


def _checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(condition, name=op.f(name))
        for name, condition in CHECK_CONSTRAINTS[table]
    ]


def upgrade() -> None:
    op.create_table(
        "promotion_ai_daily_usage",
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False),
        *_checks("promotion_ai_daily_usage"),
        sa.PrimaryKeyConstraint("usage_date", name=op.f("pk_promotion_ai_daily_usage")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "promotion_ai_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("called_at", sa.DateTime(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        *_checks("promotion_ai_calls"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_ai_calls")),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)

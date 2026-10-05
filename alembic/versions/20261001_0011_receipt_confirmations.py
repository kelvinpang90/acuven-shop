"""Receipt confirmations.

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 1 条（确认收货使用幂等键和数据库
唯一约束）与「订单与退款状态」，与 app/models/order.py 的 ReceiptConfirmation 一致。
约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；订单外键 RESTRICT。

订单 ID 的唯一约束同时是订单外键在 MySQL 上可用的索引，不另建普通索引。
downgrade 直接删表，连同唯一约束与外键一起删除。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "receipt_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "LENGTH(idempotency_key) >= 1",
            name=op.f("ck_receipt_confirmations_idempotency_key_not_empty"),
        ),
        sa.CheckConstraint(
            "LENGTH(request_fingerprint) = 64",
            name=op.f("ck_receipt_confirmations_request_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('guest', 'member')",
            name=op.f("ck_receipt_confirmations_actor_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_receipt_confirmations_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipt_confirmations")),
        sa.UniqueConstraint("order_id", name=op.f("uq_receipt_confirmations_order_id")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_receipt_confirmations_idempotency_key"),
        ),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("receipt_confirmations")

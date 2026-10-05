"""Refund requests, refund lines and per-unit refund records.

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 RefundRequest / RefundLine 一行、
「订单与退款状态」与「失败、并发与重试」第 1 条（退款申请使用幂等键和数据库唯一约束），
以及 docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 退款决定，与 app/models/refund.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
金额是整数仙，时间是不带时区的 UTC；外键一律 RESTRICT。

逐件记录的（逐件分摊快照 ID, 占用标记）唯一约束允许多个空值（MySQL 与 SQLite 相同），
所以同一件同时最多被一笔有效申请占用，被拒申请把占用标记置空后可再申请。
申请行与逐件记录的外键都以所在唯一约束的第一列为可用索引，不另建普通索引；
申请行的订单行外键与 order_items.variant_id 等已有外键一样由 MySQL 自行建索引。

downgrade 按依赖倒序直接删表、不单独删订单 ID 索引：它是订单外键唯一可用的索引，MySQL 不许
在外键仍存在时删除它；删表时连同索引、唯一约束与外键一起删除。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "refund_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("actor_type", sa.String(length=10), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("amount_sen", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'rejected')",
            name=op.f("ck_refund_requests_status_valid"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('guest', 'member')",
            name=op.f("ck_refund_requests_actor_type_valid"),
        ),
        sa.CheckConstraint(
            "LENGTH(idempotency_key) >= 1",
            name=op.f("ck_refund_requests_idempotency_key_not_empty"),
        ),
        sa.CheckConstraint(
            "LENGTH(request_fingerprint) = 64",
            name=op.f("ck_refund_requests_request_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "amount_sen >= 0",
            name=op.f("ck_refund_requests_amount_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "status <> 'requested' OR reviewed_at IS NULL",
            name=op.f("ck_refund_requests_requested_has_no_reviewed_at"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR reviewed_at IS NOT NULL",
            name=op.f("ck_refund_requests_reviewed_status_has_reviewed_at"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_refund_requests_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_requests")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_refund_requests_idempotency_key"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_refund_requests_order_id"),
        "refund_requests",
        ["order_id"],
        unique=False,
    )
    op.create_table(
        "refund_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("refund_request_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount_sen", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity >= 1",
            name=op.f("ck_refund_lines_quantity_positive"),
        ),
        sa.CheckConstraint(
            "amount_sen >= 0",
            name=op.f("ck_refund_lines_amount_sen_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id"],
            ["refund_requests.id"],
            name=op.f("fk_refund_lines_refund_request_id_refund_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_refund_lines_order_item_id_order_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_lines")),
        sa.UniqueConstraint(
            "refund_request_id",
            "order_item_id",
            name=op.f("uq_refund_lines_refund_request_id_order_item_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "refund_line_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("refund_line_id", sa.Integer(), nullable=False),
        sa.Column("order_item_unit_id", sa.Integer(), nullable=False),
        sa.Column("cash_paid_sen", sa.Integer(), nullable=False),
        sa.Column("occupied", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "cash_paid_sen >= 0",
            name=op.f("ck_refund_line_units_cash_paid_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "occupied IS NULL OR occupied = 1",
            name=op.f("ck_refund_line_units_occupied_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["refund_line_id"],
            ["refund_lines.id"],
            name=op.f("fk_refund_line_units_refund_line_id_refund_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_unit_id"],
            ["order_item_units.id"],
            name=op.f("fk_refund_line_units_order_item_unit_id_order_item_units"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refund_line_units")),
        sa.UniqueConstraint(
            "refund_line_id",
            "order_item_unit_id",
            name=op.f("uq_refund_line_units_refund_line_id_order_item_unit_id"),
        ),
        sa.UniqueConstraint(
            "order_item_unit_id",
            "occupied",
            name=op.f("uq_refund_line_units_order_item_unit_id_occupied"),
        ),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("refund_line_units")
    op.drop_table("refund_lines")
    op.drop_table("refund_requests")

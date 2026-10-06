"""Refund request reviewer, review reason, review idempotency key and review fingerprint.

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 RefundRequest / RefundLine 一行
（「审核人及理由」）、「订单与退款状态」与「失败、并发与重试」第 1 条（退款审核使用幂等键和
数据库唯一约束），以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 退款审核决定，
与 app/models/refund.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。

四列都可空，对已有行直接加列：迁移前不会有 approved 或 rejected 的申请（审核接口尚未存在），
已有的 requested 申请四列都为空，满足新的检查约束。审核人外键 RESTRICT；先建普通索引再建
外键，外键直接用这个索引（与 0007 的 orders.member_id 相同）。审核幂等键的唯一约束允许多个空值。

downgrade 按依赖倒序撤销：先删检查约束（MySQL 不许删除仍被多列检查约束引用的列）与唯一约束，
再删外键与索引（MySQL 不许在外键仍存在时删除它唯一可用的索引），最后删列。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("refund_requests", sa.Column("reviewer_admin_id", sa.Integer(), nullable=True))
    op.add_column(
        "refund_requests",
        sa.Column("review_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "refund_requests",
        sa.Column("review_idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "refund_requests",
        sa.Column("review_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_refund_requests_reviewer_admin_id"),
        "refund_requests",
        ["reviewer_admin_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_refund_requests_reviewer_admin_id_admin_accounts"),
        "refund_requests",
        "admin_accounts",
        ["reviewer_admin_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_refund_requests_review_idempotency_key"),
        "refund_requests",
        ["review_idempotency_key"],
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_requested_has_no_review"),
        "refund_requests",
        "status <> 'requested' OR (reviewer_admin_id IS NULL"
        " AND review_reason IS NULL AND review_idempotency_key IS NULL"
        " AND review_fingerprint IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_reviewed_status_has_review"),
        "refund_requests",
        "status NOT IN ('approved', 'rejected') OR (reviewer_admin_id IS NOT NULL"
        " AND review_idempotency_key IS NOT NULL AND review_fingerprint IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_rejected_has_review_reason"),
        "refund_requests",
        "status <> 'rejected' OR review_reason IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_review_reason_not_empty"),
        "refund_requests",
        "review_reason IS NULL OR LENGTH(review_reason) >= 1",
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_review_idempotency_key_not_empty"),
        "refund_requests",
        "review_idempotency_key IS NULL OR LENGTH(review_idempotency_key) >= 1",
    )
    op.create_check_constraint(
        op.f("ck_refund_requests_review_fingerprint_length"),
        "refund_requests",
        "review_fingerprint IS NULL OR LENGTH(review_fingerprint) = 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_refund_requests_review_fingerprint_length"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_refund_requests_review_idempotency_key_not_empty"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_refund_requests_review_reason_not_empty"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_refund_requests_rejected_has_review_reason"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_refund_requests_reviewed_status_has_review"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_refund_requests_requested_has_no_review"),
        "refund_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_refund_requests_review_idempotency_key"),
        "refund_requests",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_refund_requests_reviewer_admin_id_admin_accounts"),
        "refund_requests",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_refund_requests_reviewer_admin_id"), table_name="refund_requests")
    op.drop_column("refund_requests", "review_fingerprint")
    op.drop_column("refund_requests", "review_idempotency_key")
    op.drop_column("refund_requests", "review_reason")
    op.drop_column("refund_requests", "reviewer_admin_id")

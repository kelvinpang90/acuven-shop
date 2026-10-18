"""Stripe test events and refund attempts.

依据 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的 StripeCheckout /
StripeEvent / StripeRefund 一行，「Stripe 测试环境支付」第 6、7、8 条（事件
ID 去重；每条退款尝试有自己的本站 ID，同一来源至多一条不是 failed 的尝试，
由数据库唯一约束保证），与 app/models/stripe.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。alembic check 不比较检查约束、
服务端默认值与表选项，改这里或模型时须人工核对两边一致。取值直接写出，
不 import app：模型以后改了，本迁移不跟着变。时间是不带时区的 UTC；金额
是整数仙。外键一律 RESTRICT。
upgrade 依次建 stripe_events、stripe_refunds 及其两个索引；downgrade 倒序
删除：先删两个索引（都不以外键列开头，MySQL 不拿它们当外键的索引，可以
单独删），再删 stripe_refunds、stripe_events。迁移只在 MySQL 上执行，不要求
能在 SQLite 上执行。

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('processed', 'ignored')",
            name=op.f("ck_stripe_events_outcome_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_stripe_events_event_id")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "stripe_refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_intent_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("refund_request_id", sa.Integer(), nullable=True),
        sa.Column("checkout_id", sa.Integer(), nullable=True),
        sa.Column("stripe_refund_id", sa.String(length=255), nullable=True),
        sa.Column("amount_sen", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("active_refund_request_id", sa.Integer(), nullable=True),
        sa.Column("active_checkout_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('refund_request', 'checkout')",
            name=op.f("ck_stripe_refunds_source_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name=op.f("ck_stripe_refunds_status_valid"),
        ),
        sa.CheckConstraint(
            "amount_sen > 0",
            name=op.f("ck_stripe_refunds_amount_positive"),
        ),
        sa.CheckConstraint(
            "call_count >= 0",
            name=op.f("ck_stripe_refunds_call_count_non_negative"),
        ),
        sa.CheckConstraint(
            "source_type <> 'refund_request'"
            " OR (refund_request_id IS NOT NULL AND checkout_id IS NULL)",
            name=op.f("ck_stripe_refunds_refund_request_source"),
        ),
        sa.CheckConstraint(
            "source_type <> 'checkout' OR (checkout_id IS NOT NULL AND refund_request_id IS NULL)",
            name=op.f("ck_stripe_refunds_checkout_source"),
        ),
        sa.CheckConstraint(
            "status <> 'failed'"
            " OR (active_refund_request_id IS NULL AND active_checkout_id IS NULL)",
            name=op.f("ck_stripe_refunds_failed_has_no_active_source"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('pending', 'succeeded')"
            " OR source_type <> 'refund_request'"
            " OR (active_refund_request_id IS NOT NULL"
            " AND active_refund_request_id = refund_request_id"
            " AND active_checkout_id IS NULL)",
            name=op.f("ck_stripe_refunds_active_refund_request_is_source"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('pending', 'succeeded')"
            " OR source_type <> 'checkout'"
            " OR (active_checkout_id IS NOT NULL"
            " AND active_checkout_id = checkout_id"
            " AND active_refund_request_id IS NULL)",
            name=op.f("ck_stripe_refunds_active_checkout_is_source"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR succeeded_at IS NOT NULL",
            name=op.f("ck_stripe_refunds_succeeded_has_succeeded_at"),
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id"],
            ["refund_requests.id"],
            name=op.f("fk_stripe_refunds_refund_request_id_refund_requests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checkout_id"],
            ["stripe_checkouts.id"],
            name=op.f("fk_stripe_refunds_checkout_id_stripe_checkouts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_refunds")),
        sa.UniqueConstraint("stripe_refund_id", name=op.f("uq_stripe_refunds_stripe_refund_id")),
        sa.UniqueConstraint(
            "active_refund_request_id", name=op.f("uq_stripe_refunds_active_refund_request_id")
        ),
        sa.UniqueConstraint(
            "active_checkout_id", name=op.f("uq_stripe_refunds_active_checkout_id")
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_stripe_refunds_payment_intent_id"),
        "stripe_refunds",
        ["payment_intent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stripe_refunds_status_created_at"),
        "stripe_refunds",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_stripe_refunds_status_created_at"), table_name="stripe_refunds")
    op.drop_index(op.f("ix_stripe_refunds_payment_intent_id"), table_name="stripe_refunds")
    op.drop_table("stripe_refunds")
    op.drop_table("stripe_events")

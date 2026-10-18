"""Stripe test checkouts and the site payment method.

依据 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的 SiteSetting 一行
（支付方式：模拟 / Stripe 测试，默认模拟）与 StripeCheckout / StripeEvent /
StripeRefund 一行，「Stripe 测试环境支付」第 2、3 条（同一订单至多一条
不是 expired 的会话记录，由数据库唯一约束保证），以及 docs/HANDOFF.md 0.53
记录的 Kelvin 2026-10-15 落实决定（核对不符的已付款会话标为 expired、释放
订单），与 app/models/site.py、app/models/stripe.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。alembic check 不比较检查约束、
服务端默认值与表选项，改这里或模型时须人工核对两边一致。取值直接写出，
不 import app：模型以后改了，本迁移不跟着变。时间是不带时区的 UTC；金额
是整数仙。外键一律 RESTRICT。
upgrade 先给 site_settings 加 payment_method 列（已有的那一行取服务端
默认值 simulated）与其检查约束，再建 stripe_checkouts 及其三个索引；
downgrade 倒序删除：先删 stripe_checkouts（索引随表删除），再删检查约束与
列（MySQL 不许删除仍被检查约束引用的列）。迁移只在 MySQL 上执行，不要求
能在 SQLite 上执行。

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.add_column(
        "site_settings",
        sa.Column(
            "payment_method",
            sa.String(length=16),
            server_default="simulated",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_site_settings_payment_method_valid"),
        "site_settings",
        "payment_method IN ('simulated', 'stripe_test')",
    )
    op.create_table(
        "stripe_checkouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("checkout_session_id", sa.String(length=255), nullable=True),
        sa.Column("payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("amount_sen", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payment_status", sa.String(length=24), nullable=True),
        sa.Column("payment_method_type", sa.String(length=32), nullable=True),
        sa.Column("checkout_url", sa.String(length=2048), nullable=True),
        sa.Column("create_params", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("alerted_at", sa.DateTime(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(), nullable=True),
        sa.Column("active_order_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('creating', 'open', 'complete', 'expired')",
            name=op.f("ck_stripe_checkouts_status_valid"),
        ),
        sa.CheckConstraint(
            "payment_status IS NULL OR payment_status IN ('unpaid', 'paid', 'no_payment_required')",
            name=op.f("ck_stripe_checkouts_payment_status_valid"),
        ),
        sa.CheckConstraint(
            "currency = 'myr'",
            name=op.f("ck_stripe_checkouts_currency_myr"),
        ),
        sa.CheckConstraint(
            "amount_sen > 0",
            name=op.f("ck_stripe_checkouts_amount_positive"),
        ),
        sa.CheckConstraint(
            "status <> 'expired' OR active_order_id IS NULL",
            name=op.f("ck_stripe_checkouts_expired_has_no_active_order"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('creating', 'open', 'complete')"
            " OR (active_order_id IS NOT NULL AND active_order_id = order_id)",
            name=op.f("ck_stripe_checkouts_active_order_is_order"),
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR (status = 'complete'"
            " AND payment_status IS NOT NULL AND payment_status = 'paid')",
            name=op.f("ck_stripe_checkouts_confirmed_is_complete_paid"),
        ),
        sa.CheckConstraint(
            "status <> 'creating' OR (checkout_session_id IS NULL"
            " AND checkout_url IS NULL AND payment_status IS NULL)",
            name=op.f("ck_stripe_checkouts_creating_has_no_session"),
        ),
        sa.CheckConstraint(
            "status <> 'open' OR (checkout_session_id IS NOT NULL AND checkout_url IS NOT NULL)",
            name=op.f("ck_stripe_checkouts_open_has_session_and_url"),
        ),
        sa.CheckConstraint(
            "status <> 'complete' OR (checkout_session_id IS NOT NULL"
            " AND completed_at IS NOT NULL)",
            name=op.f("ck_stripe_checkouts_complete_has_session_and_time"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_stripe_checkouts_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_checkouts")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_stripe_checkouts_idempotency_key")),
        sa.UniqueConstraint(
            "checkout_session_id", name=op.f("uq_stripe_checkouts_checkout_session_id")
        ),
        sa.UniqueConstraint(
            "payment_intent_id", name=op.f("uq_stripe_checkouts_payment_intent_id")
        ),
        sa.UniqueConstraint("active_order_id", name=op.f("uq_stripe_checkouts_active_order_id")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_stripe_checkouts_order_id"),
        "stripe_checkouts",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stripe_checkouts_status_expires_at"),
        "stripe_checkouts",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stripe_checkouts_status_last_polled_at"),
        "stripe_checkouts",
        ["status", "last_polled_at"],
        unique=False,
    )


def downgrade() -> None:
    # 索引随表一起删，不先单独删：MySQL 会让外键改用以同列开头的
    # ix_stripe_checkouts_order_id、去掉自动建的索引，单独删它会报错（与 0018、0019 相同）。
    op.drop_table("stripe_checkouts")
    op.drop_constraint(
        op.f("ck_site_settings_payment_method_valid"), "site_settings", type_="check"
    )
    op.drop_column("site_settings", "payment_method")

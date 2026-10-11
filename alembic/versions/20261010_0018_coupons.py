"""Coupons and coupon uses.

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 Coupon / CouponUse 一行与「计价、优惠、
积分与库存」第 1 条，以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（两个上限可留空
表示不限，预占与核销都计次数，退款不恢复），与 app/models/coupon.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。alembic check 不比较检查约束与表选项，改这里或
模型时须人工核对两边一致。取值直接写出，不 import app：模型以后改了，本迁移不跟着变。
时间是不带时区的 UTC；金额是整数仙。外键一律 RESTRICT。
upgrade 先建 coupons，再建引用它与 orders、members 的 coupon_uses 及其两个索引；
downgrade 按依赖倒序删除（先 coupon_uses 连同其索引，再 coupons）。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("coupon_type", sa.String(length=10), nullable=False),
        sa.Column("amount_off_sen", sa.Integer(), nullable=True),
        sa.Column("percent_off", sa.Integer(), nullable=True),
        sa.Column("min_subtotal_sen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("total_use_limit", sa.Integer(), nullable=True),
        sa.Column("per_member_use_limit", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "LENGTH(code) >= 4 AND LENGTH(code) <= 20",
            name=op.f("ck_coupons_code_length"),
        ),
        sa.CheckConstraint(
            "coupon_type IN ('fixed', 'percent')",
            name=op.f("ck_coupons_type_valid"),
        ),
        sa.CheckConstraint(
            "coupon_type <> 'fixed'"
            " OR (amount_off_sen IS NOT NULL AND amount_off_sen > 0 AND percent_off IS NULL)",
            name=op.f("ck_coupons_fixed_amount"),
        ),
        sa.CheckConstraint(
            "coupon_type <> 'percent'"
            " OR (percent_off IS NOT NULL AND percent_off >= 1"
            " AND percent_off <= 100 AND amount_off_sen IS NULL)",
            name=op.f("ck_coupons_percent_value"),
        ),
        sa.CheckConstraint(
            "min_subtotal_sen >= 0",
            name=op.f("ck_coupons_min_subtotal_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "ends_at > starts_at",
            name=op.f("ck_coupons_ends_after_starts"),
        ),
        sa.CheckConstraint(
            "total_use_limit IS NULL OR total_use_limit >= 1",
            name=op.f("ck_coupons_total_use_limit_positive"),
        ),
        sa.CheckConstraint(
            "per_member_use_limit IS NULL OR per_member_use_limit >= 1",
            name=op.f("ck_coupons_per_member_use_limit_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupons")),
        sa.UniqueConstraint("code", name=op.f("uq_coupons_code")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "coupon_uses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("coupon_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("coupon_discount_sen", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('reserved', 'used', 'released')",
            name=op.f("ck_coupon_uses_status_valid"),
        ),
        sa.CheckConstraint(
            "coupon_discount_sen >= 0",
            name=op.f("ck_coupon_uses_coupon_discount_sen_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["coupon_id"],
            ["coupons.id"],
            name=op.f("fk_coupon_uses_coupon_id_coupons"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_coupon_uses_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_coupon_uses_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coupon_uses")),
        sa.UniqueConstraint("order_id", name=op.f("uq_coupon_uses_order_id")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_coupon_uses_coupon_id_status"),
        "coupon_uses",
        ["coupon_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coupon_uses_coupon_id_member_id_status"),
        "coupon_uses",
        ["coupon_id", "member_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    # 两个索引随表一起删，不先单独删：MySQL 建这两个以 coupon_id 开头的索引时，会把外键
    # fk_coupon_uses_coupon_id_coupons 自动建的索引去掉、改用它们，逐个删到最后一个会因
    # 外键需要而报错。
    op.drop_table("coupon_uses")
    op.drop_table("coupons")

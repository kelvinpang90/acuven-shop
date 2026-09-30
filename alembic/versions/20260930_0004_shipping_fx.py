"""Shipping zones and demo FX rates.

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行，
与 app/models/shipping.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
运费是 MYR 整数仙；汇率是 1 MYR 等于多少该币种，DECIMAL(18, 6)。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "shipping_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_type", sa.String(length=10), nullable=False),
        sa.Column("zone_code", sa.String(length=5), nullable=False),
        sa.Column("fee_sen", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(zone_type = 'my_state' AND LENGTH(zone_code) = 5 AND zone_code LIKE 'MY-%')"
            " OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY')"
            " OR (zone_type = 'other' AND zone_code = 'OTHER')",
            name=op.f("ck_shipping_rates_zone_code_matches_type"),
        ),
        sa.CheckConstraint(
            "fee_sen >= 0",
            name=op.f("ck_shipping_rates_fee_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_shipping_rates_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipping_rates")),
        sa.UniqueConstraint("zone_code", name=op.f("uq_shipping_rates_zone_code")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "demo_fx_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("currency_decimals", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=6, asdecimal=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "LENGTH(country_code) = 2 AND country_code <> 'MY'",
            name=op.f("ck_demo_fx_rates_country_code_valid"),
        ),
        sa.CheckConstraint(
            "LENGTH(currency_code) = 3 AND currency_code <> 'MYR'",
            name=op.f("ck_demo_fx_rates_currency_code_valid"),
        ),
        sa.CheckConstraint(
            "currency_decimals >= 0 AND currency_decimals <= 3",
            name=op.f("ck_demo_fx_rates_currency_decimals_range"),
        ),
        sa.CheckConstraint(
            "rate > 0",
            name=op.f("ck_demo_fx_rates_rate_positive"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_demo_fx_rates_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_fx_rates")),
        sa.UniqueConstraint("country_code", name=op.f("uq_demo_fx_rates_country_code")),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("demo_fx_rates")
    op.drop_table("shipping_rates")

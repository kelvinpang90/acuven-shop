"""Demo shipping fees and demo FX rates.

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行，
写进 0004 建好的表，不改表结构。数据全是迁移内的常量，用轻量表定义写入，
不 import app 的模型：模型以后改了，这个迁移写入的内容不跟着变。
运费是 MYR 整数仙；汇率是虚构的固定演示值（1 MYR 等于多少该币种），不是市场汇率。
每行版本号为 1。代码一律大写，由本迁移保证（数据库不区分大小写）。
BN 刻意不配汇率，演示只显示 MYR；HK 刻意不配国家运费，演示兜底运费。

downgrade 按区域代码与国家代码删除这批行，只保证在写入后未经后台编辑的库上成立
（CI 与开发库）；部署只执行 upgrade，从不降级。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 马来西亚州属与联邦直辖区（ISO 3166-2:MY）→ 运费仙。
# MY-12 沙巴、MY-13 砂拉越、MY-15 纳闽在东马，运费较高。
MY_STATE_FEES = {
    "MY-01": 800,
    "MY-02": 800,
    "MY-03": 800,
    "MY-04": 800,
    "MY-05": 800,
    "MY-06": 800,
    "MY-07": 800,
    "MY-08": 800,
    "MY-09": 800,
    "MY-10": 800,
    "MY-11": 800,
    "MY-12": 1500,
    "MY-13": 1500,
    "MY-14": 800,
    "MY-15": 1500,
    "MY-16": 800,
}

# 国家（ISO 3166-1 两位代码）→ 运费仙。
COUNTRY_FEES = {
    "SG": 2000,
    "BN": 2500,
    "TH": 3000,
    "CN": 3500,
    "JP": 4500,
    "AU": 5000,
    "US": 6000,
    "GB": 6000,
}

# 其他国家的兜底运费。
OTHER_ZONE_CODE = "OTHER"
OTHER_FEE = 8000

# (收货国家, 币种, 币种小数位, 1 MYR 等于多少该币种)。
FX_RATES = (
    ("SG", "SGD", 2, Decimal("0.310000")),
    ("TH", "THB", 2, Decimal("7.600000")),
    ("CN", "CNY", 2, Decimal("1.650000")),
    ("JP", "JPY", 0, Decimal("34.000000")),
    ("AU", "AUD", 2, Decimal("0.350000")),
    ("US", "USD", 2, Decimal("0.230000")),
    ("GB", "GBP", 2, Decimal("0.170000")),
    ("HK", "HKD", 2, Decimal("1.800000")),
)

VERSION = 1

ZONE_CODES = [*MY_STATE_FEES, *COUNTRY_FEES, OTHER_ZONE_CODE]
FX_COUNTRIES = [country for country, *_ in FX_RATES]

shipping_rates = sa.table(
    "shipping_rates",
    sa.column("zone_type", sa.String),
    sa.column("zone_code", sa.String),
    sa.column("fee_sen", sa.Integer),
    sa.column("version", sa.Integer),
)
demo_fx_rates = sa.table(
    "demo_fx_rates",
    sa.column("country_code", sa.String),
    sa.column("currency_code", sa.String),
    sa.column("currency_decimals", sa.Integer),
    sa.column("rate", sa.Numeric(18, 6, asdecimal=True)),
    sa.column("version", sa.Integer),
)


def _zone(zone_type: str, zone_code: str, fee_sen: int) -> dict:
    return {"zone_type": zone_type, "zone_code": zone_code, "fee_sen": fee_sen, "version": VERSION}


def upgrade() -> None:
    zone_rows = [_zone("my_state", code, fee) for code, fee in MY_STATE_FEES.items()]
    zone_rows += [_zone("country", code, fee) for code, fee in COUNTRY_FEES.items()]
    zone_rows.append(_zone("other", OTHER_ZONE_CODE, OTHER_FEE))
    op.bulk_insert(shipping_rates, zone_rows)

    fx_rows = [
        {
            "country_code": country,
            "currency_code": currency,
            "currency_decimals": decimals,
            "rate": rate,
            "version": VERSION,
        }
        for country, currency, decimals, rate in FX_RATES
    ]
    op.bulk_insert(demo_fx_rates, fx_rows)


def downgrade() -> None:
    op.execute(demo_fx_rates.delete().where(demo_fx_rates.c.country_code.in_(FX_COUNTRIES)))
    op.execute(shipping_rates.delete().where(shipping_rates.c.zone_code.in_(ZONE_CODES)))

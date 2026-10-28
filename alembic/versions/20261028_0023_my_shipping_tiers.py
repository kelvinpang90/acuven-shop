"""Malaysia shipping: two tiers (West MY-W, East MY-E) replace the 16 state rows.

依据 docs/DESIGN.md 1.17（提交 80e3775）「数据模型」的 ShippingRate / DemoFxRate 一行与
「计价、优惠、积分与库存」第 10 条，以及 docs/HANDOFF.md 0.69 记录的落实写法；
与 app/models/shipping.py 一致。约束名按 app/db/base.py 的命名约定逐个写出（照 0004）。
alembic check 不比较检查约束，改这里或模型时须人工核对两边一致。

东马（MY-E）为 MY-12 沙巴、MY-13 砂拉越与 MY-15 纳闽，其余 13 个州属与联邦直辖区
为西马（MY-W）。换算写成纯函数 tier_fees 与 state_fees，不碰数据库：
- tier_fees：每档的运费取该档现有州属行运费的最大值（演示运费不因换算变低）；
  出现 MY-01 到 MY-16 以外的州属代码、或某一档一个州属行都没有时抛 RuntimeError，
  不以零运费代替。
- state_fees：按两档的运费重建 16 个州属的运费；缺任一档时抛 RuntimeError。

执行顺序：改约束在 MySQL 上是 DDL，会隐式提交，不随迁移的事务回滚。所以先读出现有的行、
完成判断，判断不通过时在改表之前失败，库保持原样；判断通过后才改约束与数据。
- upgrade：读出全部 my_state 行并换算 → 删去原检查约束 → 删除州属行 → 建新检查约束
  → 写入两档行（版本号 1），新写入的行经新约束校验。
- downgrade：读出两档行并换算 → 删去新检查约束 → 删除两档行 → 恢复原检查约束
  → 写入 16 个州属行（版本号 1），新写入的行经原约束校验。
两档与州属的行都是新行，行 ID 新分配。迁移不写审计（不是管理员操作），不改订单：
已有订单快照里的州属代码照旧（DESIGN 1.17 第 10 条「已有订单」）。

设计第 10 条要求换算结果记入交接单：upgrade 用 print 把两档的代码与运费仙数写一行到
标准输出。alembic/env.py 不配置 logging，INFO 日志不会输出；部署时 alembic upgrade head
的标准输出进入部署日志，由运营者读出后补记。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0004）：模型以后改了，这个迁移不跟着变。
ZONE_MY_STATE = "my_state"
ZONE_MY_REGION = "my_region"
MY_WEST = "MY-W"
MY_EAST = "MY-E"
# ISO 3166-2:MY 的 13 个州与 3 个联邦直辖区。
MY_STATE_CODES = tuple(f"MY-{n:02d}" for n in range(1, 17))
EAST_STATE_CODES = frozenset({"MY-12", "MY-13", "MY-15"})
VERSION = 1

CONSTRAINT_NAME = "ck_shipping_rates_zone_code_matches_type"
# 与 0004 建的约束逐字相同。
STATE_CONSTRAINT = (
    "(zone_type = 'my_state' AND LENGTH(zone_code) = 5 AND zone_code LIKE 'MY-%')"
    " OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY')"
    " OR (zone_type = 'other' AND zone_code = 'OTHER')"
)
# 与 app/models/shipping.py 的 ShippingRate 逐字相同。
REGION_CONSTRAINT = (
    "(zone_type = 'my_region' AND zone_code IN ('MY-W', 'MY-E'))"
    " OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY')"
    " OR (zone_type = 'other' AND zone_code = 'OTHER')"
)

shipping_rates = sa.table(
    "shipping_rates",
    sa.column("zone_type", sa.String),
    sa.column("zone_code", sa.String),
    sa.column("fee_sen", sa.Integer),
    sa.column("version", sa.Integer),
)


def _tier_of(state_code: str) -> str:
    return MY_EAST if state_code in EAST_STATE_CODES else MY_WEST


def tier_fees(fees: Mapping[str, int]) -> dict[str, int]:
    """州属代码 → 运费仙数，换算成两档 {MY-W: 仙数, MY-E: 仙数}。纯函数。

    每档取该档州属运费的最大值；出现 MY-01 到 MY-16 以外的代码、
    或某一档一个州属都没有时抛 RuntimeError。
    """
    unknown = sorted(code for code in fees if code not in MY_STATE_CODES)
    if unknown:
        raise RuntimeError(f"unknown Malaysian state codes in shipping_rates: {unknown}")
    tiers: dict[str, list[int]] = {MY_WEST: [], MY_EAST: []}
    for code, fee_sen in fees.items():
        tiers[_tier_of(code)].append(fee_sen)
    empty = [tier for tier, tier_states in tiers.items() if not tier_states]
    if empty:
        raise RuntimeError(f"no Malaysian state shipping rows for tier {empty}")
    return {tier: max(tier_states) for tier, tier_states in tiers.items()}


def state_fees(fees: Mapping[str, int]) -> dict[str, int]:
    """两档 {MY-W: 仙数, MY-E: 仙数} → MY-01 到 MY-16 各自的运费仙数。纯函数。

    缺任一档（或出现两档以外的代码）时抛 RuntimeError。
    """
    if set(fees) != {MY_WEST, MY_EAST}:
        raise RuntimeError(f"Malaysian shipping tiers must be MY-W and MY-E: {sorted(fees)}")
    return {code: fees[_tier_of(code)] for code in MY_STATE_CODES}


def _read_fees(zone_type: str) -> dict[str, int]:
    stmt = sa.select(shipping_rates.c.zone_code, shipping_rates.c.fee_sen).where(
        shipping_rates.c.zone_type == zone_type
    )
    return {code: fee_sen for code, fee_sen in op.get_bind().execute(stmt)}


def _rows(zone_type: str, fees: Mapping[str, int]) -> list[dict]:
    return [
        {"zone_type": zone_type, "zone_code": code, "fee_sen": fee_sen, "version": VERSION}
        for code, fee_sen in fees.items()
    ]


def upgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    tiers = tier_fees(_read_fees(ZONE_MY_STATE))

    op.drop_constraint(op.f(CONSTRAINT_NAME), "shipping_rates", type_="check")
    op.execute(shipping_rates.delete().where(shipping_rates.c.zone_type == ZONE_MY_STATE))
    op.create_check_constraint(op.f(CONSTRAINT_NAME), "shipping_rates", REGION_CONSTRAINT)
    op.bulk_insert(shipping_rates, _rows(ZONE_MY_REGION, tiers))

    print(
        "0023 Malaysia shipping tiers (fee_sen, version 1): "
        + ", ".join(f"{tier} {fee_sen}" for tier, fee_sen in tiers.items()),
        flush=True,
    )


def downgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    states = state_fees(_read_fees(ZONE_MY_REGION))

    op.drop_constraint(op.f(CONSTRAINT_NAME), "shipping_rates", type_="check")
    op.execute(shipping_rates.delete().where(shipping_rates.c.zone_type == ZONE_MY_REGION))
    op.create_check_constraint(op.f(CONSTRAINT_NAME), "shipping_rates", STATE_CONSTRAINT)
    op.bulk_insert(shipping_rates, _rows(ZONE_MY_STATE, states))

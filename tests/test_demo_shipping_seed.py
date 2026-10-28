"""示例运费与示例汇率种子数据：迁移 0005 实际写入的行；迁移 0023 的换算纯函数。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行与
SHOP-TASK-007 的验收标准。每条测试的文档字符串引用它守住的那一句规则。
在空的 SQLite 内存库上通过 Alembic 的 Operations 先执行迁移 0004 建表、再执行 0005 的
upgrade / downgrade，查询实际写入的行，而不是只看迁移里的常量。不按模型建表：
DESIGN 1.17（提交 80e3775）第 10 条之后模型的检查约束不收 0005 写入的州属行
（docs/HANDOFF.md 0.69 第 3 点）。

迁移 0023 依据 DESIGN 1.17「计价、优惠、积分与库存」第 10 条（下称「第 10 条」）与
HANDOFF 0.69：这里只调用它的纯函数 tier_fees 与 state_fees，不在 SQLite 上执行 0023
（迁移只在 MySQL 上执行；往返由 CI 的 backend 检查在 MySQL 8 上做）。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, Connection, create_engine, select
from sqlalchemy.orm import Session

from app.models import DemoFxRate, ShippingRate
from app.services.shipping import FxReference, ShippingQuote, quote_shipping, reference_amount

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回与写入相等，完整 18 位精度的往返不在 SQLite 上断言。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"

# 验收标准逐字抄下的期望值，不从迁移常量取：迁移写错了，这里能发现。
EAST_MALAYSIA = {"MY-12", "MY-13", "MY-15"}
EXPECTED_STATE_FEES = {
    f"MY-{n:02d}": 1500 if f"MY-{n:02d}" in EAST_MALAYSIA else 800 for n in range(1, 17)
}
EXPECTED_COUNTRY_FEES = {
    "SG": 2000,
    "BN": 2500,
    "TH": 3000,
    "CN": 3500,
    "JP": 4500,
    "AU": 5000,
    "US": 6000,
    "GB": 6000,
}
EXPECTED_FX = {
    "SG": ("SGD", 2, Decimal("0.310000")),
    "TH": ("THB", 2, Decimal("7.600000")),
    "CN": ("CNY", 2, Decimal("1.650000")),
    "JP": ("JPY", 0, Decimal("34.000000")),
    "AU": ("AUD", 2, Decimal("0.350000")),
    "US": ("USD", 2, Decimal("0.230000")),
    "GB": ("GBP", 2, Decimal("0.170000")),
    "HK": ("HKD", 2, Decimal("1.800000")),
}


def _load_migration(name: str, filename: str) -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location(name, VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCHEMA = _load_migration("shipping_fx_schema_migration", "20260930_0004_shipping_fx.py")
MIGRATION = _load_migration("demo_shipping_migration", "20260930_0005_demo_shipping_fx.py")
TIERS = _load_migration("my_shipping_tiers_migration", "20261028_0023_my_shipping_tiers.py")


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        _run(conn, SCHEMA, "upgrade")
        yield conn
    engine.dispose()


def _run(conn: Connection, migration: ModuleType, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(migration, step)()


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    _run(connection, MIGRATION, "upgrade")
    with Session(bind=connection) as db:
        yield db


def _zones(session: Session, zone_type: str) -> dict[str, int]:
    stmt = select(ShippingRate.zone_code, ShippingRate.fee_sen)
    return dict(session.execute(stmt.where(ShippingRate.zone_type == zone_type)).all())


def test_all_sixteen_states_with_demo_fees(session: Session) -> None:
    """「马来西亚州属 MY-12、MY-13、MY-15 为 1500，其余 13 个州属与联邦直辖区为 800」：
    MY-01 到 MY-16 齐全，不多不少。
    """
    assert _zones(session, "my_state") == EXPECTED_STATE_FEES


def test_country_and_fallback_fees(session: Session) -> None:
    """「国家 SG 2000、BN 2500、TH 3000、CN 3500、JP 4500、AU 5000、US 6000、GB 6000；
    兜底 OTHER 8000」：HK 刻意不配国家运费。
    """
    assert _zones(session, "country") == EXPECTED_COUNTRY_FEES
    assert _zones(session, "other") == {"OTHER": 8000}


def test_row_counts_are_exact_and_versions_start_at_one(session: Session) -> None:
    """「写入下面三条的全部示例行、版本号均为 1」：运费 25 行（16 州属、8 国家、1 兜底），
    汇率 8 行，没有多写。
    """
    zones = session.scalars(select(ShippingRate)).all()
    rates = session.scalars(select(DemoFxRate)).all()

    assert len(zones) == 16 + 8 + 1
    assert len(rates) == 8
    assert {row.version for row in [*zones, *rates]} == {1}


def test_demo_fx_rates(session: Session) -> None:
    """「示例汇率（国家、币种、小数位、1 MYR 等于）」逐项相等，汇率读回是 Decimal；
    「BN 刻意不配汇率」，也没有 MY 行。
    """
    rows = session.scalars(select(DemoFxRate)).all()
    actual = {r.country_code: (r.currency_code, r.currency_decimals, r.rate) for r in rows}

    assert actual == EXPECTED_FX
    assert all(type(r.rate) is Decimal for r in rows)
    assert "BN" not in actual
    assert "MY" not in actual


def test_codes_are_uppercase(session: Session) -> None:
    """「大小写与字母是否合法由写入方（本任务的数据迁移……）校验」：数据库不区分大小写，
    所以由这里确认写入的代码全是大写。
    """
    codes = list(session.scalars(select(ShippingRate.zone_code)))
    for row in session.scalars(select(DemoFxRate)):
        codes += [row.country_code, row.currency_code]

    assert all(code == code.upper() for code in codes)


def test_singapore_has_fee_and_reference(session: Session) -> None:
    """SG 有国家运费也有汇率：运费 2000 仙；RM19.50 按 0.310000 得 SGD 6.05（605 分）。"""
    assert quote_shipping(session, "SG", None) == ShippingQuote(2000, "SG", 1)
    assert reference_amount(session, "SG", 1950) == FxReference("SGD", 2, 605, 1)


def test_brunei_has_fee_but_only_myr(session: Session) -> None:
    """「BN 刻意不配汇率，用来演示只显示 MYR」：有国家运费，参考外币为空。"""
    assert quote_shipping(session, "BN", None) == ShippingQuote(2500, "BN", 1)
    assert reference_amount(session, "BN", 1950) is None


def test_hong_kong_uses_fallback_fee_and_has_reference(session: Session) -> None:
    """「HK 刻意不配国家运费，用来演示兜底运费」：运费用 OTHER 行，但有 HKD 参考金额
    （RM10.00 × 1.8 = HKD 18.00）。
    """
    assert quote_shipping(session, "HK", None) == ShippingQuote(8000, "OTHER", 1)
    assert reference_amount(session, "HK", 1000) == FxReference("HKD", 2, 1800, 1)


def test_country_in_neither_table(session: Session) -> None:
    """两表都没有的国家（FR）：兜底运费 8000，参考外币为空，只显示 MYR。"""
    assert quote_shipping(session, "FR", None) == ShippingQuote(8000, "OTHER", 1)
    assert reference_amount(session, "FR", 1000) is None


def test_malaysia_has_no_reference(session: Session) -> None:
    """马来西亚：MYR 本身没有参考外币。种子州属行不再直接计价（第 10 条之后计价按两档的行，
    两档的行由迁移 0023 换算写入，见下面的纯函数测试）。"""
    assert reference_amount(session, "MY", 1000) is None


def test_downgrade_removes_seed_rows_and_keeps_others(
    session: Session, connection: Connection
) -> None:
    """「downgrade 按区域代码与国家代码删除这批行」：示例行全部删除，
    另插入的非示例运费行与汇率行原样保留。
    """
    session.add(ShippingRate(zone_type="country", zone_code="NZ", fee_sen=5500, version=1))
    session.add(
        DemoFxRate(
            country_code="NZ",
            currency_code="NZD",
            currency_decimals=2,
            rate=Decimal("0.380000"),
            version=1,
        )
    )
    session.flush()

    _run(connection, MIGRATION, "downgrade")
    session.expire_all()

    assert list(session.scalars(select(ShippingRate.zone_code))) == ["NZ"]
    assert list(session.scalars(select(DemoFxRate.country_code))) == ["NZ"]


# ---- 迁移 0023 的换算纯函数 ----


def test_seed_state_fees_convert_to_tiers(session: Session) -> None:
    """第 10 条「示例数据西马各州均为 RM8.00、东马均为 RM15.00，换算后不变」：0005 实际写入的
    16 个州属行换算为 MY-W 800、MY-E 1500，只有这两个键，西马在前。"""
    tiers = TIERS.tier_fees(_zones(session, "my_state"))

    assert tiers == {"MY-W": 800, "MY-E": 1500}
    assert list(tiers) == ["MY-W", "MY-E"]


def test_inconsistent_fees_in_a_tier_take_the_highest() -> None:
    """第 10 条「同一档的州属运费不一致时取其中最高的（演示运费不因换算变低）」：
    西马里一个州属为 900、东马里一个为 1400，换算为 MY-W 900、MY-E 1500。"""
    fees = {**EXPECTED_STATE_FEES, "MY-07": 900, "MY-15": 1400}

    assert TIERS.tier_fees(fees) == {"MY-W": 900, "MY-E": 1500}


def test_tiers_are_computed_from_the_states_present() -> None:
    """第 10 条「每档的运费取该档现有州属行的运费」：不是 16 个州属都有行时，按现有的行取，
    缺的州属不当作零运费。"""
    assert TIERS.tier_fees({"MY-14": 700, "MY-13": 1600}) == {"MY-W": 700, "MY-E": 1600}


@pytest.mark.parametrize(
    "fees",
    [
        pytest.param(
            {code: fee for code, fee in EXPECTED_STATE_FEES.items() if code not in EAST_MALAYSIA},
            id="no-east",
        ),
        pytest.param({code: 1500 for code in EAST_MALAYSIA}, id="no-west"),
        pytest.param({}, id="no-states"),
    ],
)
def test_tier_without_any_state_fails(fees: dict[str, int]) -> None:
    """第 10 条「某一档一个州属行都没有……时迁移失败，不以零运费代替」：抛 RuntimeError。"""
    with pytest.raises(RuntimeError):
        TIERS.tier_fees(fees)


@pytest.mark.parametrize("code", ["MY-17", "MY-00", "my-01", "MY-W", "SG"])
def test_unknown_state_code_fails(code: str) -> None:
    """第 10 条「出现 16 个州属代码以外的州属行时迁移失败」：其余 16 个州属齐全也抛
    RuntimeError，不忽略未知的行。"""
    with pytest.raises(RuntimeError):
        TIERS.tier_fees({**EXPECTED_STATE_FEES, code: 800})


def test_state_fees_rebuild_sixteen_states() -> None:
    """第 10 条「回退时先读出两档的运费……然后按读出的运费重建 16 个州属行」：
    MY-W 800、MY-E 1500 重建为 MY-01 到 MY-16 的种子运费；与 tier_fees 互为往返。"""
    states = TIERS.state_fees({"MY-W": 800, "MY-E": 1500})

    assert states == EXPECTED_STATE_FEES
    assert list(states) == [f"MY-{n:02d}" for n in range(1, 17)]
    assert TIERS.tier_fees(TIERS.state_fees({"MY-W": 650, "MY-E": 1250})) == {
        "MY-W": 650,
        "MY-E": 1250,
    }


@pytest.mark.parametrize("tiers", [{"MY-W": 800}, {"MY-E": 1500}, {}])
def test_state_fees_without_both_tiers_fails(tiers: dict[str, int]) -> None:
    """第 10 条「两档缺任一行即回退失败，判断在改表之前」：抛 RuntimeError。"""
    with pytest.raises(RuntimeError):
        TIERS.state_fees(tiers)


def test_tier_migration_revision_and_constraint_match_the_model() -> None:
    """HANDOFF 0.69「迁移 0023」；第 10 条「改运费表的区域代码检查约束为：my_region 且代码为
    MY-W 或 MY-E」：revision 为 0023、接在 0022 之后；新约束与模型的检查约束逐字一致。"""
    (model_check,) = (
        constraint
        for constraint in ShippingRate.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name.endswith("zone_code_matches_type")
    )

    assert (TIERS.revision, TIERS.down_revision) == ("0023", "0022")
    assert str(model_check.sqltext) == TIERS.REGION_CONSTRAINT

"""示例运费与示例汇率种子数据：迁移 0005 实际写入的行。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行与
SHOP-TASK-007 的验收标准。每条测试的文档字符串引用它守住的那一句规则。
用 SQLite 内存库按模型建表，通过 Alembic 的 Operations 执行迁移文件里的
upgrade / downgrade，再查询实际写入的行，而不是只看迁移里的常量。
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
from sqlalchemy import Connection, create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import DemoFxRate, ShippingRate
from app.services.shipping import FxReference, ShippingQuote, quote_shipping, reference_amount

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回与写入相等，完整 18 位精度的往返不在 SQLite 上断言。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20260930_0005_demo_shipping_fx.py"

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


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("demo_shipping_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        Base.metadata.create_all(conn)
        yield conn
    engine.dispose()


def _run(conn: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(MIGRATION, step)()


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    _run(connection, "upgrade")
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


def test_malaysian_states(session: Session) -> None:
    """马来西亚按州属：西马 MY-10 为 800，东马 MY-13 为 1500；MYR 本身没有参考外币。"""
    assert quote_shipping(session, "MY", "MY-10") == ShippingQuote(800, "MY-10", 1)
    assert quote_shipping(session, "MY", "MY-13") == ShippingQuote(1500, "MY-13", 1)
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

    _run(connection, "downgrade")
    session.expire_all()

    assert list(session.scalars(select(ShippingRate.zone_code))) == ["NZ"]
    assert list(session.scalars(select(DemoFxRate.country_code))) == ["NZ"]

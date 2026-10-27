"""后台运费与演示汇率的读取与保存（app/services/admin_shipping_rates.py，SHOP-TASK-170）。

依据 docs/DESIGN.md 1.16（提交 e9a1c93）「计价、优惠、积分与库存」第 9 条
（下称「第 9 条」）与 docs/HANDOFF.md 0.67 记录的落实写法（下称「HANDOFF 0.67」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句；
没有直接原句的写明是 SHOP-TASK-170 验收里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（StaticPool），写入与迁移 0005 相同的演示数据并提交。
结果一律以只查列值的 SELECT 读出，不看会话里的 ORM 对象。
SQLite 不生成 FOR UPDATE：锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.models import AdminAccount, AuditEvent, DemoFxRate, ShippingRate
from app.services.admin_shipping_rates import (
    AddCountryFee,
    AddFxRate,
    AdminRates,
    FxRateRow,
    KeepFee,
    KeepFxRate,
    RatesChanged,
    RatesInvalid,
    RemoveRow,
    ShippingRateRow,
    canonical_rate,
    load_admin_rates,
    lock_fx_rates_statement,
    lock_shipping_rates_statement,
    save_admin_rates,
)
from app.services.shipping import ShippingQuote, quote_shipping, reference_amount

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 演示汇率至多 6 位小数，读回与写入相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

NOW = datetime(2026, 10, 27, 3, 0)

# 与迁移 0005 相同的演示数据，按迁移的写入顺序。
STATE_FEES = {f"MY-{n:02d}": 1500 if n in (12, 13, 15) else 800 for n in range(1, 17)}
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
OTHER_FEE = 8000
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

SHIPPING_CHANGED = "admin_shipping_rate_changed"
SHIPPING_REMOVED = "admin_shipping_rate_removed"
SHIPPING_ADDED = "admin_shipping_rate_added"
FX_CHANGED = "admin_fx_rate_changed"
FX_REMOVED = "admin_fx_rate_removed"
FX_ADDED = "admin_fx_rate_added"
SHIPPING = "shipping_rate"
FX = "demo_fx_rate"


def _seed_zone(zone_type: str, code: str, fee: int) -> ShippingRate:
    return ShippingRate(zone_type=zone_type, zone_code=code, fee_sen=fee, version=1)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        for code, fee in STATE_FEES.items():
            seed.add(_seed_zone("my_state", code, fee))
        for code, fee in COUNTRY_FEES.items():
            seed.add(_seed_zone("country", code, fee))
        seed.add(_seed_zone("other", "OTHER", OTHER_FEE))
        for country, currency, decimals, rate in FX_RATES:
            seed.add(
                DemoFxRate(
                    country_code=country,
                    currency_code=currency,
                    currency_decimals=decimals,
                    rate=rate,
                    version=1,
                )
            )
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin_id(db: Session) -> int:
    account = AdminAccount(
        username="admin@example.test", password_hash="x" * 60, password_updated_at=NOW
    )
    db.add(account)
    db.commit()
    return account.id


# ---- 辅助 ----


def _request(page: AdminRates) -> dict[str, list]:
    """把读到的整页原样列成保存请求（每行保留、值不变）。"""
    assert page.other is not None
    return {
        "states": [KeepFee(row.id, row.version, row.fee_sen) for row in page.states],
        "other": [KeepFee(page.other.id, page.other.version, page.other.fee_sen)],
        "countries": [KeepFee(row.id, row.version, row.fee_sen) for row in page.countries],
        "fx_rates": [KeepFxRate(row.id, row.version, row.rate) for row in page.fx_rates],
    }


def _replace(group: list, row_id: int, item: object) -> None:
    index = next(i for i, old in enumerate(group) if getattr(old, "id", None) == row_id)
    group[index] = item


def _zone(page: AdminRates, code: str) -> ShippingRateRow:
    rows = [*page.states, *page.countries, *([page.other] if page.other else [])]
    return next(row for row in rows if row.zone_code == code)


def _fx(page: AdminRates, code: str) -> FxRateRow:
    return next(row for row in page.fx_rates if row.country_code == code)


def _save(db: Session, admin_id: int, request: dict[str, list]) -> AdminRates:
    return save_admin_rates(db, admin_id, now=NOW, **request)


def _zones(db: Session) -> list[tuple]:
    stmt = select(
        ShippingRate.id,
        ShippingRate.zone_code,
        ShippingRate.fee_sen,
        ShippingRate.version,
    ).order_by(ShippingRate.id)
    return [tuple(row) for row in db.execute(stmt)]


def _rates(db: Session) -> list[tuple]:
    stmt = select(
        DemoFxRate.id,
        DemoFxRate.country_code,
        DemoFxRate.currency_code,
        DemoFxRate.currency_decimals,
        DemoFxRate.rate,
        DemoFxRate.version,
    ).order_by(DemoFxRate.id)
    return [tuple(row) for row in db.execute(stmt)]


def _audits(db: Session) -> list[tuple]:
    stmt = select(
        AuditEvent.action,
        AuditEvent.target_type,
        AuditEvent.target_id,
        AuditEvent.old_value,
        AuditEvent.new_value,
    ).order_by(AuditEvent.id)
    return [tuple(row) for row in db.execute(stmt)]


def _snapshot(db: Session) -> tuple:
    return _zones(db), _rates(db), _audits(db)


def _record_statements(engine: Engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    return statements


Mutation = Callable[[AdminRates, dict], None]


def _set_fee(code: str, fee: object) -> Mutation:
    def mutate(page: AdminRates, request: dict) -> None:
        row = _zone(page, code)
        group = "states" if code.startswith("MY-") else "other" if code == "OTHER" else "countries"
        _replace(request[group], row.id, KeepFee(row.id, row.version, fee))

    return mutate


def _set_rate(code: str, rate: object) -> Mutation:
    def mutate(page: AdminRates, request: dict) -> None:
        row = _fx(page, code)
        _replace(request["fx_rates"], row.id, KeepFxRate(row.id, row.version, rate))

    return mutate


def _append(group: str, *items: object) -> Mutation:
    def mutate(_page: AdminRates, request: dict) -> None:
        request[group].extend(items)

    return mutate


def _add_country(code: object, fee: object) -> Mutation:
    return _append("countries", AddCountryFee(code, fee))


def _add_fx(code: object, currency: object, decimals: object, rate: object) -> Mutation:
    return _append("fx_rates", AddFxRate(code, currency, decimals, rate))


def _repeat_first(group: str) -> Mutation:
    def mutate(_page: AdminRates, request: dict) -> None:
        request[group].append(request[group][0])

    return mutate


def _remove_first(group: str) -> Mutation:
    def mutate(_page: AdminRates, request: dict) -> None:
        item = request[group].pop(0)
        request[group].append(RemoveRow(item.id, item.version))

    return mutate


# ---- 读取 ----


def test_load_orders_rows_and_carries_ids_and_versions(db: Session) -> None:
    """第 9 条「读取时每行带行 ID 与版本号」；验收「16 个州属按 MY-01 到 MY-16 排列、
    兜底一行、国家运费按国家代码排列、汇率按国家代码排列」「运费为整仙」。"""
    page = load_admin_rates(db)

    assert [row.zone_code for row in page.states] == [f"MY-{n:02d}" for n in range(1, 17)]
    assert {row.zone_code: row.fee_sen for row in page.states} == STATE_FEES
    assert page.other is not None
    assert (page.other.zone_code, page.other.fee_sen) == ("OTHER", OTHER_FEE)
    assert [row.zone_code for row in page.countries] == sorted(COUNTRY_FEES)
    assert {row.zone_code: row.fee_sen for row in page.countries} == COUNTRY_FEES
    assert [row.country_code for row in page.fx_rates] == sorted(c for c, *_ in FX_RATES)
    zones = [*page.states, page.other, *page.countries]
    assert all(type(row.fee_sen) is int and row.version == 1 for row in zones)
    assert all(row.version == 1 for row in page.fx_rates)
    ids = [row.id for row in zones] + [row.id for row in page.fx_rates]
    assert all(type(row_id) is int for row_id in ids)


def test_load_gives_canonical_rates_with_currency(db: Session) -> None:
    """第 9 条「读取接口与审计都用规范形式：去掉小数部分尾随的零
    （全为零时连小数点一起去掉）」：34.000000 读作 34，0.310000 读作 0.31；
    汇率行另带币种代码与小数位。"""
    page = load_admin_rates(db)

    rates = {
        row.country_code: (row.currency_code, row.currency_decimals, row.rate)
        for row in page.fx_rates
    }
    assert rates == {
        "SG": ("SGD", 2, "0.31"),
        "TH": ("THB", 2, "7.6"),
        "CN": ("CNY", 2, "1.65"),
        "JP": ("JPY", 0, "34"),
        "AU": ("AUD", 2, "0.35"),
        "US": ("USD", 2, "0.23"),
        "GB": ("GBP", 2, "0.17"),
        "HK": ("HKD", 2, "1.8"),
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("34.000000"), "34"),
        (Decimal("0.310000"), "0.31"),
        (Decimal("100"), "100"),
        (Decimal("100.000000"), "100"),
        (Decimal("007.50"), "7.5"),
        (Decimal("1E+2"), "100"),
        (Decimal("0.000001"), "0.000001"),
        (Decimal("123456.123456"), "123456.123456"),
    ],
)
def test_canonical_rate(value: Decimal, expected: str) -> None:
    """第 9 条「去掉小数部分尾随的零（全为零时连小数点一起去掉），
    整数部分不带多余的前导零、不用指数」。"""
    assert canonical_rate(value) == expected


def test_load_only_selects_and_writes_no_audit(db: Session, engine: Engine) -> None:
    """验收「只发 SELECT，不写库、不写审计」。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    load_admin_rates(db)

    assert statements
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert _snapshot(db) == before


# ---- 第一步：取值不合格 ----

INVALID = [
    # 第 9 条「运费以整仙提交，0 到 999,999 仙」。
    pytest.param(_set_fee("MY-01", -1), id="state-fee-negative"),
    pytest.param(_set_fee("MY-01", 1_000_000), id="state-fee-too-large"),
    pytest.param(_set_fee("MY-01", True), id="state-fee-bool"),
    pytest.param(_set_fee("OTHER", -1), id="other-fee-negative"),
    pytest.param(_set_fee("SG", 1_000_000), id="country-fee-too-large"),
    pytest.param(_set_fee("SG", False), id="country-fee-bool"),
    pytest.param(_set_fee("SG", 2000.0), id="country-fee-float"),
    pytest.param(_set_fee("SG", "2000"), id="country-fee-string"),
    pytest.param(_add_country("NZ", -1), id="added-fee-negative"),
    pytest.param(_add_country("NZ", True), id="added-fee-bool"),
    # 第 9 条「只接受 1–6 位数字，可再跟小数点与 1–6 位数字
    # （不收正负号、指数、空白、以小数点开头或结尾的写法），且须大于 0」。
    pytest.param(_set_rate("SG", "0"), id="rate-zero"),
    pytest.param(_set_rate("SG", "0.000000"), id="rate-zero-with-decimals"),
    pytest.param(_set_rate("SG", "1234567"), id="rate-seven-integer-digits"),
    pytest.param(_set_rate("SG", "0.1234567"), id="rate-seven-decimal-digits"),
    pytest.param(_set_rate("SG", "+0.31"), id="rate-plus-sign"),
    pytest.param(_set_rate("SG", "-0.31"), id="rate-minus-sign"),
    pytest.param(_set_rate("SG", "3.1e-1"), id="rate-exponent"),
    pytest.param(_set_rate("SG", "1E2"), id="rate-exponent-upper"),
    pytest.param(_set_rate("SG", " 0.31"), id="rate-leading-space"),
    pytest.param(_set_rate("SG", "0.31\n"), id="rate-trailing-newline"),
    pytest.param(_set_rate("SG", "0. 31"), id="rate-inner-space"),
    pytest.param(_set_rate("SG", ".31"), id="rate-leading-point"),
    pytest.param(_set_rate("SG", "31."), id="rate-trailing-point"),
    pytest.param(_set_rate("SG", ""), id="rate-empty"),
    pytest.param(_set_rate("SG", "０.31"), id="rate-fullwidth-digit"),
    pytest.param(_set_rate("SG", "٣"), id="rate-arabic-indic-digit"),
    pytest.param(_set_rate("SG", "1,000"), id="rate-comma"),
    pytest.param(_set_rate("SG", "NaN"), id="rate-nan"),
    pytest.param(_set_rate("SG", 0.31), id="rate-float"),
    pytest.param(_set_rate("SG", Decimal("0.31")), id="rate-decimal-object"),
    pytest.param(_add_fx("NZ", "NZD", 2, "0"), id="added-rate-zero"),
    pytest.param(_add_fx("NZ", "NZD", 2, "0.1234567"), id="added-rate-seven-decimals"),
    # 第 9 条「新增运费或汇率的国家须在结账页的收货国家列表里
    # （即 phonenumbers 支持的两位地区代码），且不是马来西亚」。
    pytest.param(_add_country("nz", 5500), id="country-lowercase"),
    pytest.param(_add_country("MY", 0), id="country-malaysia"),
    pytest.param(_add_country("XX", 5500), id="country-not-listed"),
    pytest.param(_add_country("NZL", 5500), id="country-three-letters"),
    pytest.param(_add_country("ＮＺ", 5500), id="country-fullwidth"),
    pytest.param(_add_fx("nz", "NZD", 2, "0.38"), id="fx-country-lowercase"),
    pytest.param(_add_fx("MY", "SGD", 2, "0.31"), id="fx-country-malaysia"),
    pytest.param(_add_fx("XX", "XXX", 2, "1"), id="fx-country-not-listed"),
    # 第 9 条「币种代码（三位大写字母、不是 MYR……）与小数位（0–3 的整数）」。
    pytest.param(_add_fx("NZ", "MYR", 2, "1"), id="currency-myr"),
    pytest.param(_add_fx("NZ", "nzd", 2, "0.38"), id="currency-lowercase"),
    pytest.param(_add_fx("NZ", "NZ", 2, "0.38"), id="currency-two-letters"),
    pytest.param(_add_fx("NZ", "NZD", 4, "0.38"), id="decimals-4"),
    pytest.param(_add_fx("NZ", "NZD", -1, "0.38"), id="decimals-negative"),
    pytest.param(_add_fx("NZ", "NZD", True, "0.38"), id="decimals-bool"),
    # 第 9 条「同一组的新增行之间国家不重复」；验收「同一组里行 ID 不重复」。
    pytest.param(_repeat_first("states"), id="state-id-repeated"),
    pytest.param(_repeat_first("countries"), id="country-id-repeated"),
    pytest.param(_repeat_first("fx_rates"), id="fx-id-repeated"),
    pytest.param(
        _append("countries", AddCountryFee("NZ", 5500), AddCountryFee("NZ", 6000)),
        id="added-country-repeated",
    ),
    pytest.param(
        _append("fx_rates", AddFxRate("NZ", "NZD", 2, "0.38"), AddFxRate("NZ", "NZD", 2, "1")),
        id="added-fx-country-repeated",
    ),
    # 第 9 条「州属与兜底的行只有保留」：结构不合格。
    pytest.param(_remove_first("states"), id="state-removed"),
    pytest.param(_remove_first("other"), id="other-removed"),
    pytest.param(_append("states", AddCountryFee("NZ", 5500)), id="state-added"),
    pytest.param(_append("fx_rates", AddCountryFee("NZ", 5500)), id="fee-item-in-fx-group"),
]


@pytest.mark.parametrize("mutate", INVALID)
def test_invalid_request_is_rejected_before_any_sql(
    db: Session, engine: Engine, admin_id: int, mutate: Mutation
) -> None:
    """第 9 条「（一）先不碰数据库检查请求本身……不通过即回答『取值不合格』」
    「每步不通过都整次保存不生效」：抛 RatesInvalid，不发任何 SQL，
    数据与审计不变；同一请求里合格的修改也不写入。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("JP", 4600)(page, request)
    mutate(page, request)
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(RatesInvalid):
        _save(db, admin_id, request)

    assert statements == []
    assert _snapshot(db) == before


def test_range_limits_are_accepted(db: Session, admin_id: int) -> None:
    """第 9 条「0 到 999,999 仙……0 即该区免运费」「1–6 位数字，可再跟小数点与
    1–6 位数字」「小数位（0–3 的整数）」：取值范围的两端都能保存，是上面反例的对照。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("MY-01", 0)(page, request)
    _set_fee("SG", 999_999)(page, request)
    _set_rate("JP", "999999.999999")(page, request)
    _set_rate("SG", "0.000001")(page, request)
    _add_fx("NZ", "NZD", 3, "1")(page, request)

    saved = _save(db, admin_id, request)

    assert _zone(saved, "MY-01").fee_sen == 0
    assert _zone(saved, "SG").fee_sen == 999_999
    assert _fx(saved, "JP").rate == "999999.999999"
    assert _fx(saved, "SG").rate == "0.000001"
    assert (_fx(saved, "NZ").currency_decimals, _fx(saved, "NZ").rate) == (3, "1")


# ---- 第二步：数据已被改动 ----


def _bump_version(group: str, code: str) -> Mutation:
    def mutate(page: AdminRates, request: dict) -> None:
        if group == "fx_rates":
            row = _fx(page, code)
            item = KeepFxRate(row.id, row.version + 1, row.rate)
        else:
            row = _zone(page, code)
            item = KeepFee(row.id, row.version + 1, row.fee_sen)
        _replace(request[group], row.id, item)

    return mutate


def _drop(group: str, code: str) -> Mutation:
    def mutate(page: AdminRates, request: dict) -> None:
        row = _fx(page, code) if group == "fx_rates" else _zone(page, code)
        request[group] = [item for item in request[group] if item.id != row.id]

    return mutate


def _state_in_country_group(page: AdminRates, request: dict) -> None:
    row = _zone(page, "MY-01")
    request["states"] = [item for item in request["states"] if item.id != row.id]
    request["countries"].append(KeepFee(row.id, row.version, row.fee_sen))


CHANGED = [
    # 第 9 条「版本号与当前相同」。
    pytest.param(_bump_version("states", "MY-12"), id="state-version-differs"),
    pytest.param(_bump_version("other", "OTHER"), id="other-version-differs"),
    pytest.param(_bump_version("countries", "BN"), id="country-version-differs"),
    pytest.param(_bump_version("fx_rates", "HK"), id="fx-version-differs"),
    # 第 9 条「现有的行一个不漏」。
    pytest.param(_drop("states", "MY-16"), id="state-row-missing"),
    pytest.param(_drop("other", "OTHER"), id="other-row-missing"),
    pytest.param(_drop("countries", "BN"), id="country-row-missing"),
    pytest.param(_drop("fx_rates", "HK"), id="fx-row-missing"),
    # 第 9 条「请求里每个行 ID 都须是该组现有的行」。
    pytest.param(_append("countries", KeepFee(999, 1, 100)), id="country-row-not-existing"),
    pytest.param(_append("countries", RemoveRow(999, 1)), id="removed-row-not-existing"),
    pytest.param(_append("fx_rates", KeepFxRate(999, 1, "1")), id="fx-row-not-existing"),
    pytest.param(_state_in_country_group, id="state-id-in-country-group"),
    # 第 9 条「新增的国家在该组不得已有行」。
    pytest.param(_add_country("SG", 2500), id="added-country-exists"),
    pytest.param(_add_fx("SG", "SGD", 2, "0.32"), id="added-fx-country-exists"),
]


@pytest.mark.parametrize("mutate", CHANGED)
def test_stale_request_is_rejected_without_writing(
    db: Session, admin_id: int, mutate: Mutation
) -> None:
    """第 9 条「（二）再在一个事务里锁住两张表现有的全部行，按行 ID 核对……
    不通过即回答『数据已被改动』」「每步不通过都整次保存不生效」：抛 RatesChanged，
    同一请求里其他行的修改也不写入，数据与审计不变。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("JP", 4600)(page, request)
    mutate(page, request)
    before = _snapshot(db)

    with pytest.raises(RatesChanged):
        _save(db, admin_id, request)

    assert _snapshot(db) == before


def test_remove_then_add_the_same_country(db: Session, admin_id: int) -> None:
    """第 9 条「同一请求里把该行列为删除的除外，相当于先删后增」
    「删除后再新增同一国家，版本号从 1 重新开始」；HANDOFF 0.67「同一事务里先删后增时
    先 flush 删除再插入」「删后再增各写一条审计」：新行的 ID 与旧行不同。"""
    page = load_admin_rates(db)
    request = _request(page)
    old_zone = _zone(page, "SG")
    old_fx = _fx(page, "SG")
    _replace(request["countries"], old_zone.id, RemoveRow(old_zone.id, old_zone.version))
    _replace(request["fx_rates"], old_fx.id, RemoveRow(old_fx.id, old_fx.version))
    _add_country("SG", 2600)(page, request)
    _add_fx("SG", "USD", 3, "0.231")(page, request)

    saved = _save(db, admin_id, request)

    new_zone = _zone(saved, "SG")
    new_fx = _fx(saved, "SG")
    assert new_zone.id != old_zone.id
    assert new_fx.id != old_fx.id
    assert new_zone == ShippingRateRow(new_zone.id, "SG", 2600, 1)
    assert new_fx == FxRateRow(new_fx.id, "SG", "USD", 3, "0.231", 1)
    assert saved == load_admin_rates(db)
    assert _audits(db) == [
        (SHIPPING_REMOVED, SHIPPING, old_zone.id, "SG 2000 v1", None),
        (FX_REMOVED, FX, old_fx.id, "SG SGD 2 0.31 v1", None),
        (SHIPPING_ADDED, SHIPPING, new_zone.id, None, "SG 2600 v1"),
        (FX_ADDED, FX, new_fx.id, None, "SG USD 3 0.231 v1"),
    ]


def test_ids_are_not_reused_after_deleting_the_largest(db: Session, admin_id: int) -> None:
    """第 9 条「行 ID 不复用：删除的行的 ID 不再分配给新行（不依赖数据库的默认行为，
    测试库与生产库都须成立）」；HANDOFF 0.67「两张表在模型的表参数加
    sqlite_autoincrement」：删掉 ID 最大的一行并提交，之后新增的行 ID 仍大于它。"""
    page = load_admin_rates(db)
    request = _request(page)
    _add_country("NZ", 5500)(page, request)
    page = _save(db, admin_id, request)
    nz = _zone(page, "NZ")
    hk = _fx(page, "HK")
    assert nz.id == max(row[0] for row in _zones(db))
    assert hk.id == max(row[0] for row in _rates(db))

    request = _request(page)
    _replace(request["countries"], nz.id, RemoveRow(nz.id, nz.version))
    _replace(request["fx_rates"], hk.id, RemoveRow(hk.id, hk.version))
    page = _save(db, admin_id, request)
    assert "NZ" not in [row.zone_code for row in page.countries]
    assert "HK" not in [row.country_code for row in page.fx_rates]

    request = _request(page)
    _add_country("FR", 7000)(page, request)
    _add_fx("HK", "HKD", 2, "1.8")(page, request)
    page = _save(db, admin_id, request)

    assert _zone(page, "FR").id > nz.id
    assert _fx(page, "HK").id > hk.id


def test_old_page_is_rejected_after_remove_and_re_add(db: Session, admin_id: int) -> None:
    """第 9 条「所以删除后再新增的行是新的行 ID，拿着旧页面保存的请求一定落在这一步」：
    删除前读到的行 ID 与版本号再保存时抛 RatesChanged，数据与审计不变。"""
    old_page = load_admin_rates(db)
    request = _request(old_page)
    singapore = _zone(old_page, "SG")
    _replace(request["countries"], singapore.id, RemoveRow(singapore.id, singapore.version))
    _add_country("SG", 2000)(old_page, request)
    _save(db, admin_id, request)
    before = _snapshot(db)

    with pytest.raises(RatesChanged):
        _save(db, admin_id, _request(old_page))

    assert _snapshot(db) == before


def test_old_page_is_rejected_after_remove(db: Session, admin_id: int) -> None:
    """第 9 条「请求里每个行 ID 都须是该组现有的行」：列出已删除的行的旧页面被拒。"""
    old_page = load_admin_rates(db)
    request = _request(old_page)
    hk = _fx(old_page, "HK")
    _replace(request["fx_rates"], hk.id, RemoveRow(hk.id, hk.version))
    _save(db, admin_id, request)
    before = _snapshot(db)

    with pytest.raises(RatesChanged):
        _save(db, admin_id, _request(old_page))

    assert _snapshot(db) == before


# ---- 写入与审计 ----


def test_changed_rows_get_new_version_and_one_audit_each(db: Session, admin_id: int) -> None:
    """第 9 条「改了值的行版本号加一」「值与当前相同的行不改版本、不写审计」
    「每改、增、删一行写一条审计，记该行的代码、旧值与新值……及版本号」：
    只有改了值的三行版本为 2，各写一条审计。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("MY-12", 1600)(page, request)
    _set_fee("SG", 2500)(page, request)
    _set_rate("JP", "35.5")(page, request)

    saved = _save(db, admin_id, request)

    versions = {row[1]: row[3] for row in _zones(db)}
    assert versions.pop("MY-12") == 2
    assert versions.pop("SG") == 2
    assert set(versions.values()) == {1}
    fx_versions = {row[1]: row[5] for row in _rates(db)}
    assert fx_versions.pop("JP") == 2
    assert set(fx_versions.values()) == {1}
    assert _fx(saved, "JP").rate == "35.5"
    sabah, singapore, japan = _zone(page, "MY-12"), _zone(page, "SG"), _fx(page, "JP")
    # 运费表的行按 id 处理，种子数据里州属在国家之前。
    assert _audits(db) == [
        (SHIPPING_CHANGED, SHIPPING, sabah.id, "MY-12 1500 v1", "MY-12 1600 v2"),
        (SHIPPING_CHANGED, SHIPPING, singapore.id, "SG 2000 v1", "SG 2500 v2"),
        (FX_CHANGED, FX, japan.id, "JP JPY 0 34 v1", "JP JPY 0 35.5 v2"),
    ]


def test_audit_carries_admin_and_time(db: Session, admin_id: int) -> None:
    """第 9 条「每改、增、删一行写一条审计」；验收「经 record_audit 写一条审计」：
    操作者为当前管理员，发生时间为传入的 now。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("OTHER", 9000)(page, request)

    _save(db, admin_id, request)

    events = db.execute(select(AuditEvent.admin_account_id, AuditEvent.occurred_at)).all()
    assert [tuple(row) for row in events] == [(admin_id, NOW)]
    assert page.other is not None
    assert _audits(db) == [
        (SHIPPING_CHANGED, SHIPPING, page.other.id, "OTHER 8000 v1", "OTHER 9000 v2")
    ]


def test_second_change_bumps_version_again(db: Session, admin_id: int) -> None:
    """第 9 条「版本号只在一行存续期间递增」；验收「旧值带改前的版本号、新值带改后的
    版本号」：同一行再改一次，版本从 2 到 3。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("SG", 2500)(page, request)
    page = _save(db, admin_id, request)
    request = _request(page)
    _set_fee("SG", 2700)(page, request)

    saved = _save(db, admin_id, request)

    assert (_zone(saved, "SG").fee_sen, _zone(saved, "SG").version) == (2700, 3)
    assert _audits(db)[-1][3:] == ("SG 2500 v2", "SG 2700 v3")


def test_add_and_remove_rows(db: Session, admin_id: int) -> None:
    """第 9 条「国家运费可改、可新增、可删除」「演示汇率可改、可新增、可删除」
    「删除的行从表里删除；新增的行版本号为 1」：新增与删除各写一条审计，
    新增的没有旧值，删除的没有新值。"""
    page = load_admin_rates(db)
    request = _request(page)
    brunei = _zone(page, "BN")
    britain = _fx(page, "GB")
    _replace(request["countries"], brunei.id, RemoveRow(brunei.id, brunei.version))
    _replace(request["fx_rates"], britain.id, RemoveRow(britain.id, britain.version))
    _add_country("NZ", 5500)(page, request)
    _add_fx("NZ", "NZD", 2, "0.380")(page, request)

    saved = _save(db, admin_id, request)

    assert [row.zone_code for row in saved.countries] == [
        "AU",
        "CN",
        "GB",
        "JP",
        "NZ",
        "SG",
        "TH",
        "US",
    ]
    assert "GB" not in [row.country_code for row in saved.fx_rates]
    nz = _zone(saved, "NZ")
    nz_fx = _fx(saved, "NZ")
    assert nz == ShippingRateRow(nz.id, "NZ", 5500, 1)
    assert nz_fx == FxRateRow(nz_fx.id, "NZ", "NZD", 2, "0.38", 1)
    assert _audits(db) == [
        (SHIPPING_REMOVED, SHIPPING, brunei.id, "BN 2500 v1", None),
        (FX_REMOVED, FX, britain.id, "GB GBP 2 0.17 v1", None),
        (SHIPPING_ADDED, SHIPPING, nz.id, None, "NZ 5500 v1"),
        (FX_ADDED, FX, nz_fx.id, None, "NZ NZD 2 0.38 v1"),
    ]


def test_unchanged_page_writes_nothing(db: Session, engine: Engine, admin_id: int) -> None:
    """第 9 条「值与当前相同的行不改版本、不写审计」；验收「整页都未变时回滚、不写审计」
    「两种情况都返回保存后的整页数据」：原样保存只发 SELECT，返回与读取相同的整页数据。"""
    page = load_admin_rates(db)
    before = _snapshot(db)
    statements = _record_statements(engine)

    saved = _save(db, admin_id, _request(page))

    assert saved == page
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert _snapshot(db) == before


@pytest.mark.parametrize("rate", ["0.31", "0.310000", "00.31", "000000.310000"])
def test_numerically_equal_rate_is_unchanged(db: Session, admin_id: int, rate: str) -> None:
    """第 9 条「汇率按数值比较（0.31 与 0.310000 相同）」：现有汇率 0.310000
    提交数值相同的写法视为未变，不改版本、不写审计。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_rate("SG", rate)(page, request)
    before = _snapshot(db)

    saved = _save(db, admin_id, request)

    assert _fx(saved, "SG").rate == "0.31"
    assert _snapshot(db) == before


def test_changed_rate_is_audited_in_canonical_form(db: Session, admin_id: int) -> None:
    """第 9 条「读取接口与审计都用规范形式」：提交 7.700 时读取与审计都写 7.7。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_rate("TH", "7.700")(page, request)

    saved = _save(db, admin_id, request)

    assert _fx(saved, "TH").rate == "7.7"
    assert _audits(db)[-1][3:] == ("TH THB 2 7.6 v1", "TH THB 2 7.7 v2")


# ---- 加锁 ----


@pytest.mark.parametrize(
    ("statement", "table"),
    [
        (lock_shipping_rates_statement, "shipping_rates"),
        (lock_fx_rates_statement, "demo_fx_rates"),
    ],
)
def test_lock_statements_are_select_for_update(statement: Callable, table: str) -> None:
    """第 9 条「再在一个事务里锁住两张表现有的全部行」；HANDOFF 0.67「各按 id 排序」：
    按 MySQL 方言编译带 FOR UPDATE、按 id 排序、不带条件，并带 populate_existing。"""
    stmt = statement()
    sql = str(stmt.compile(dialect=mysql.dialect()))

    assert f"FROM {table}" in sql
    assert "WHERE" not in sql
    assert f"ORDER BY {table}.id" in sql
    assert sql.rstrip().endswith("FOR UPDATE")
    assert stmt.get_execution_options()["populate_existing"] is True


def test_save_locks_shipping_table_before_fx_table(
    db: Session, engine: Engine, admin_id: int
) -> None:
    """HANDOFF 0.67「加锁顺序先运费表、再汇率表」：
    保存的头两条语句依次锁运费表与汇率表。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("SG", 2500)(page, request)
    seen: list[str] = []

    @event.listens_for(engine, "before_execute")
    def _record(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        # 只编译 SELECT：SQLite 上 ORM 的 INSERT 可能带 RETURNING，MySQL 方言编译不了。
        if isinstance(clauseelement, Select):
            seen.append(str(clauseelement.compile(dialect=mysql.dialect())))

    _save(db, admin_id, request)

    locks = [sql for sql in seen if "FOR UPDATE" in sql]
    assert len(locks) == 2
    assert seen[:2] == locks
    assert "FROM shipping_rates" in locks[0]
    assert "FROM demo_fx_rates" in locks[1]


# ---- 行 ID 不复用的落实 ----


@pytest.mark.parametrize("model", [ShippingRate, DemoFxRate])
def test_tables_declare_autoincrement_in_both_dialects(model: type) -> None:
    """第 9 条「不依赖数据库的默认行为，测试库与生产库都须成立」；HANDOFF 0.67
    「两张表在模型的表参数加 sqlite_autoincrement，不改迁移」：SQLite 建表声明
    AUTOINCREMENT；MySQL 建表语句照旧是 AUTO_INCREMENT 的整数主键。"""
    sqlite_ddl = str(CreateTable(model.__table__).compile(dialect=sqlite.dialect()))
    mysql_ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))

    assert "PRIMARY KEY AUTOINCREMENT" in sqlite_ddl
    assert "id INTEGER NOT NULL AUTO_INCREMENT" in mysql_ddl
    assert "AUTOINCREMENT" not in mysql_ddl
    assert "ENGINE=InnoDB" in mysql_ddl


# ---- 生效 ----


def test_removed_country_fee_falls_back_to_other(db: Session, admin_id: int) -> None:
    """第 9 条「删除后该国按兜底运费计价」「保存提交后，之后的每次计价与下单都按新值
    （费率不缓存）」：删除 SG 国家运费后，quote_shipping 对 SG 返回兜底运费。"""
    assert quote_shipping(db, "SG", None) == ShippingQuote(2000, "SG", 1)
    page = load_admin_rates(db)
    request = _request(page)
    singapore = _zone(page, "SG")
    _replace(request["countries"], singapore.id, RemoveRow(singapore.id, singapore.version))

    _save(db, admin_id, request)

    assert quote_shipping(db, "SG", None) == ShippingQuote(OTHER_FEE, "OTHER", 1)


def test_removed_fx_rate_shows_only_myr(db: Session, admin_id: int) -> None:
    """第 9 条「演示汇率……删除后该国只显示 MYR」：删除 SG 汇率后
    reference_amount 返回 None。"""
    assert reference_amount(db, "SG", 1000) is not None
    page = load_admin_rates(db)
    request = _request(page)
    singapore = _fx(page, "SG")
    _replace(request["fx_rates"], singapore.id, RemoveRow(singapore.id, singapore.version))

    _save(db, admin_id, request)

    assert reference_amount(db, "SG", 1000) is None


def test_changed_fee_is_used_by_the_next_quote(db: Session, admin_id: int) -> None:
    """第 9 条「保存提交后，之后的每次计价与下单都按新值」：改了 MY-13 的运费，
    计价取新值与新版本号。"""
    page = load_admin_rates(db)
    request = _request(page)
    _set_fee("MY-13", 1700)(page, request)

    _save(db, admin_id, request)

    assert quote_shipping(db, "MY", "MY-13") == ShippingQuote(1700, "MY-13", 2)

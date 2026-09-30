"""运费区与演示汇率：表约束、运费查询、参考外币换算。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行：
「国家运费、马来西亚州属运费、“其他国家”兜底运费；参考币种固定汇率及版本。
下单存计算规则版本与金额快照」；「边界与原则」：「金额以 MYR 的仙（sen）为整数单位运算」
「其他货币只按管理员配置的固定演示汇率显示参考数，不参与结算」
「该国无演示汇率时只显示 MYR，不猜测或临时请求实时汇率」。
以及 SHOP-TASK-007 验收标准里的具体规则。每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库按模型建表。SQLite 的 LIKE 与 MySQL 默认排序规则都不区分大小写，
大小写由查询函数与写入方校验，这里不对数据库断言大小写。
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import DemoFxRate, ShippingRate
from app.services.shipping import (
    FxReference,
    InvalidDestination,
    ShippingQuote,
    ShippingRateMissing,
    convert_sen,
    quote_shipping,
    reference_amount,
)

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# MySQL 上是 DECIMAL，不经浮点。所以这里只断言示例汇率读回相等，
# 不在 SQLite 上断言完整 18 位精度的往返。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as db:
        yield db


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def _zone(session: Session, zone_type: str, zone_code: str, **fields: int) -> ShippingRate:
    values = {"fee_sen": 800, "version": 1} | fields
    return _add(session, ShippingRate(zone_type=zone_type, zone_code=zone_code, **values))


def _fx(session: Session, country_code: str = "SG", **fields: object) -> DemoFxRate:
    values = {
        "currency_code": "SGD",
        "currency_decimals": 2,
        "rate": Decimal("0.310000"),
        "version": 1,
    } | fields
    return _add(session, DemoFxRate(country_code=country_code, **values))


@pytest.fixture
def rates(session: Session) -> Session:
    """一小组费率：两个州属、SG、兜底；SG 与 JP 有汇率。"""
    _zone(session, "my_state", "MY-01", fee_sen=800)
    _zone(session, "my_state", "MY-12", fee_sen=1500, version=3)
    _zone(session, "country", "SG", fee_sen=2000, version=2)
    _zone(session, "other", "OTHER", fee_sen=8000)
    _fx(session, "SG", version=4)
    _fx(session, "JP", currency_code="JPY", currency_decimals=0, rate=Decimal("34.000000"))
    return session


# ---- 运费表约束 ----


def test_valid_zone_rows_of_each_type_are_accepted(session: Session) -> None:
    """「区域类型（马来西亚州属、国家、其他国家兜底三种）」：三种合法的行都写得进去，
    运费为零不算负数。也是下面各条拒绝测试的对照。
    """
    _zone(session, "my_state", "MY-16", fee_sen=0)
    _zone(session, "country", "SG")
    _zone(session, "other", "OTHER")

    assert len(session.scalars(select(ShippingRate)).all()) == 3


def test_negative_fee_is_rejected(session: Session) -> None:
    """「运费不小于零」：由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_shipping_rates_fee_sen_non_negative"
    ):
        _zone(session, "country", "SG", fee_sen=-1)


def test_zone_version_zero_is_rejected(session: Session) -> None:
    """「版本号不小于 1」：运费行由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_shipping_rates_version_positive"
    ):
        _zone(session, "country", "SG", version=0)


@pytest.mark.parametrize(
    ("zone_type", "zone_code"),
    [
        ("my_state", "SG"),  # 州属行不以 MY- 开头
        ("my_state", "MY-1"),  # 州属行不是 5 位
        ("my_state", "XX-01"),  # 5 位但不以 MY- 开头
        ("my_state", "OTHER"),
        ("country", "MY"),  # 国家行不能是马来西亚
        ("country", "MY-01"),  # 州属代码不能当国家行
        ("country", "SGP"),  # 国家行不是 2 位
        ("country", "OTHER"),
        ("country", ""),
        ("other", "SG"),  # 兜底行只能是 OTHER
        ("other", "MY-01"),
        ("region", "SG"),  # 三种之外的类型
    ],
)
def test_zone_type_and_code_mismatch_is_rejected(
    session: Session, zone_type: str, zone_code: str
) -> None:
    """「类型与代码的搭配（州属行以 MY- 开头且长 5 位、国家行长 2 位且不是 MY、
    兜底行等于 OTHER）……由显式命名的检查约束保证」，国家行为 MY 也在这里被拒。
    """
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_shipping_rates_zone_code_matches_type"
    ):
        _zone(session, zone_type, zone_code)


def test_duplicate_zone_code_is_rejected(session: Session) -> None:
    """「全表唯一且非空的区域代码」：同一区域代码只能有一行，查询结果才确定。"""
    _zone(session, "country", "SG")

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: shipping_rates.zone_code"):
        _zone(session, "country", "SG", fee_sen=100)


# ---- 汇率表约束 ----


def test_valid_fx_rows_accepted_rate_reads_back_as_decimal(session: Session) -> None:
    """「汇率……列类型为 Numeric(18, 6, asdecimal=True)」：示例汇率经 SQLite 读回
    是与写入相等的 Decimal，不是 float。也是下面各条拒绝测试的对照。
    """
    _fx(session, "SG", rate=Decimal("0.310000"))
    _fx(session, "JP", currency_code="JPY", currency_decimals=0, rate=Decimal("34.000000"))
    session.expire_all()

    rates = dict(session.execute(select(DemoFxRate.country_code, DemoFxRate.rate)).all())
    assert rates == {"SG": Decimal("0.310000"), "JP": Decimal("34.000000")}
    assert all(type(rate) is Decimal for rate in rates.values())


@pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-0.310000")])
def test_zero_or_negative_rate_is_rejected(session: Session, rate: Decimal) -> None:
    """「汇率……大于零」：零与负汇率由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_demo_fx_rates_rate_positive"
    ):
        _fx(session, rate=rate)


@pytest.mark.parametrize("decimals", [-1, 4])
def test_currency_decimals_out_of_range_is_rejected(session: Session, decimals: int) -> None:
    """「币种小数位（0 到 3）」：由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_demo_fx_rates_currency_decimals_range"
    ):
        _fx(session, currency_decimals=decimals)


@pytest.mark.parametrize("currency_code", ["MYR", "SG", "SGDX"])
def test_invalid_currency_code_is_rejected(session: Session, currency_code: str) -> None:
    """「币种代码（三位……不能是 MYR）」：MYR 与长度不是 3 由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_demo_fx_rates_currency_code_valid"
    ):
        _fx(session, currency_code=currency_code)


@pytest.mark.parametrize("country_code", ["MY", "SGP", "S"])
def test_invalid_fx_country_code_is_rejected(session: Session, country_code: str) -> None:
    """「收货国家代码（两位……不能是 MY）」：汇率国家为 MY 与长度不是 2
    由显式命名的检查约束拦住。
    """
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_demo_fx_rates_country_code_valid"
    ):
        _fx(session, country_code)


def test_fx_version_zero_is_rejected(session: Session) -> None:
    """「版本号不小于 1」：汇率行由显式命名的检查约束拦住。"""
    with pytest.raises(
        IntegrityError, match="CHECK constraint failed: ck_demo_fx_rates_version_positive"
    ):
        _fx(session, version=0)


def test_duplicate_fx_country_is_rejected(session: Session) -> None:
    """「收货国家代码……全表唯一」：一个国家只能有一个参考币种与汇率。"""
    _fx(session, "SG")

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: demo_fx_rates.country_code"
    ):
        _fx(session, "SG", currency_code="USD")


# ---- 运费查询 ----


def test_malaysia_uses_state_row(rates: Session) -> None:
    """「马来西亚必须给 16 个州属代码之一」：按州属行返回运费、区域代码与版本号。"""
    assert quote_shipping(rates, "MY", "MY-12") == ShippingQuote(1500, "MY-12", 3)
    assert quote_shipping(rates, "MY", "MY-01") == ShippingQuote(800, "MY-01", 1)


@pytest.mark.parametrize("state_code", [None, "MY-17", "MY-00", "my-01", "SG", ""])
def test_malaysia_without_known_state_is_rejected(rates: Session, state_code: str | None) -> None:
    """「马来西亚……缺少或未知即拒绝」：不退回兜底运费，也不转换大小写。"""
    with pytest.raises(InvalidDestination):
        quote_shipping(rates, "MY", state_code)


def test_malaysia_state_without_row_is_an_error(rates: Session) -> None:
    """「不返回零运费」：州属代码合法但表里没有该行时明确报错，不用兜底行代替。"""
    with pytest.raises(ShippingRateMissing):
        quote_shipping(rates, "MY", "MY-05")


@pytest.mark.parametrize("state_code", ["MY-01", ""])
def test_state_code_outside_malaysia_is_rejected(rates: Session, state_code: str) -> None:
    """「其他国家给了州属代码即拒绝」。"""
    with pytest.raises(InvalidDestination):
        quote_shipping(rates, "SG", state_code)


def test_country_row_is_used_when_present(rates: Session) -> None:
    """「有国家行用国家行」，返回该行的区域代码与版本号。"""
    assert quote_shipping(rates, "SG", None) == ShippingQuote(2000, "SG", 2)


def test_other_country_falls_back_to_other(rates: Session) -> None:
    """「否则用兜底行」：返回的区域代码是 OTHER，供订单快照记下用的是哪一行。"""
    assert quote_shipping(rates, "FR", None) == ShippingQuote(8000, "OTHER", 1)


def test_missing_fallback_is_an_error_not_free_shipping(session: Session) -> None:
    """「兜底行不存在时明确报错，不返回零运费」。"""
    _zone(session, "country", "SG", fee_sen=2000)

    with pytest.raises(ShippingRateMissing):
        quote_shipping(session, "FR", None)


@pytest.mark.parametrize("country_code", ["sg", "Sg", "SGP", "S", "", "S1", "ＳＧ", None, 65])
def test_bad_country_code_is_rejected_by_shipping(rates: Session, country_code: object) -> None:
    """「国家代码不是两位大写字母即拒绝，不自动转换大小写」：小写的 sg 不会被当成 SG。"""
    with pytest.raises(InvalidDestination):
        quote_shipping(rates, country_code, None)


def test_shipping_reads_current_row_without_caching(rates: Session) -> None:
    """「不缓存费率」：费率行改了（之后由后台维护把版本号加一），下一次查询就用新值。"""
    assert quote_shipping(rates, "SG", None) == ShippingQuote(2000, "SG", 2)
    row = rates.scalars(select(ShippingRate).where(ShippingRate.zone_code == "SG")).one()
    row.fee_sen, row.version = 2200, 3
    rates.flush()

    assert quote_shipping(rates, "SG", None) == ShippingQuote(2200, "SG", 3)


def test_queries_only_select(rates: Session, engine: Engine) -> None:
    """「查询函数只读、不写数据库」：两个查询发出的每条语句都是 SELECT。"""
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    quote_shipping(rates, "MY", "MY-01")
    quote_shipping(rates, "SG", None)
    quote_shipping(rates, "FR", None)
    reference_amount(rates, "SG", 1950)
    reference_amount(rates, "FR", 1950)

    assert statements
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert not (rates.new or rates.dirty or rates.deleted)


# ---- 参考外币查询 ----


def test_reference_amount_for_country_with_rate(rates: Session) -> None:
    """「该国有汇率时返回币种代码、币种小数位、以该币种最小单位计的整数参考金额与版本号」。"""
    assert reference_amount(rates, "SG", 1950) == FxReference("SGD", 2, 605, 4)
    assert reference_amount(rates, "JP", 25) == FxReference("JPY", 0, 9, 1)


def test_no_rate_returns_none(rates: Session) -> None:
    """「没有汇率……时返回空，不猜测、不请求外部汇率」：只显示 MYR。"""
    assert reference_amount(rates, "FR", 1950) is None


def test_malaysia_returns_none(rates: Session) -> None:
    """「没有汇率（含马来西亚）时返回空」：MYR 本身就是结算货币。"""
    assert reference_amount(rates, "MY", 1950) is None


@pytest.mark.parametrize("country_code", ["sg", "SGP", "", None])
def test_bad_country_code_is_rejected_by_reference(rates: Session, country_code: object) -> None:
    """「国家代码不是两位大写字母即拒绝」：参考外币查询同样不转换大小写。"""
    with pytest.raises(InvalidDestination):
        reference_amount(rates, country_code, 1950)


@pytest.mark.parametrize(
    ("amount", "error"), [(-1, ValueError), (True, TypeError), (19.5, TypeError)]
)
def test_invalid_amount_is_rejected_by_reference(
    rates: Session, amount: object, error: type[Exception]
) -> None:
    """「金额为负、不是 int（含 bool、float）即拒绝」：查询函数在碰数据库前就拒绝，
    马来西亚也一样。
    """
    for country_code in ("SG", "MY"):
        with pytest.raises(error):
            reference_amount(rates, country_code, amount)


# ---- 换算纯函数 ----


def test_convert_rounds_half_up_to_currency_minor_unit() -> None:
    """「换算为仙乘以汇率除以 100，用 decimal 按四舍五入、恰好半个最小单位进一」：
    1950 仙 × 0.310000 = 6.045 SGD，进一为 605 分。
    """
    assert convert_sen(1950, Decimal("0.310000"), 2) == 605


def test_convert_zero_decimal_currency_rounds_half_up() -> None:
    """「取整到该币种小数位」：日元没有小数位，25 仙 × 34 = 8.5 JPY，进一为 9。"""
    assert convert_sen(25, Decimal("34.000000"), 0) == 9


def test_convert_zero_amount_is_zero() -> None:
    """零仙换算为零；零是合法金额，不被拒绝。"""
    assert convert_sen(0, Decimal("0.310000"), 2) == 0


def test_convert_just_below_half_rounds_down() -> None:
    """「四舍五入」：不到半个最小单位舍去——1949 仙 × 0.31 = 6.0419 SGD，得 604。
    与 1950 仙的算例对照，确认进一只发生在恰好半位及以上。
    """
    assert convert_sen(1949, Decimal("0.310000"), 2) == 604


@pytest.mark.parametrize(
    ("amount", "error"), [(-1, ValueError), (True, TypeError), (19.5, TypeError)]
)
def test_convert_rejects_invalid_amount(amount: object, error: type[Exception]) -> None:
    """「金额为负、不是 int（含 bool、float）即拒绝」。"""
    with pytest.raises(error):
        convert_sen(amount, Decimal("0.310000"), 2)


@pytest.mark.parametrize("rate", [0.31, 34, "0.31"])
def test_convert_rejects_non_decimal_rate(rate: object) -> None:
    """「汇率参数只接受 Decimal（float 即拒绝）」：全程不用浮点。"""
    with pytest.raises(TypeError):
        convert_sen(1950, rate, 2)


@pytest.mark.parametrize(
    "rate",
    [Decimal("0"), Decimal("-0.31"), Decimal("0.3100001"), Decimal("NaN"), Decimal("Infinity")],
)
def test_convert_rejects_invalid_rate(rate: Decimal) -> None:
    """「不大于零或超过 6 位小数即拒绝」：与汇率列 DECIMAL(18, 6) 和 rate > 0 的约束一致。"""
    with pytest.raises(ValueError):
        convert_sen(1950, rate, 2)

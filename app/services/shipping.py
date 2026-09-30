"""示例运费与参考外币的只读查询。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行与
「边界与原则」：金额以 MYR 的仙为整数单位；其他货币只按固定演示汇率显示参考数，不参与结算；
「访客国家货币」按收货国家决定，该国无演示汇率时只显示 MYR，不猜测或临时请求实时汇率。
两个查询都返回所用费率行的版本号，供结账写入订单快照。

只发 SELECT，不写库、不缓存费率。国家代码须是两位大写字母，不自动转换大小写：
MySQL 比较不区分大小写，大小写只能在这里与写入方把关。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DemoFxRate, ShippingRate
from app.models.shipping import OTHER_ZONE_CODE, ZONE_COUNTRY, ZONE_MY_STATE, ZONE_OTHER

MALAYSIA = "MY"
# ISO 3166-2:MY 的 13 个州与 3 个联邦直辖区。
MY_STATE_CODES = frozenset(f"MY-{n:02d}" for n in range(1, 17))
# 汇率列是 DECIMAL(18, 6)。
MAX_RATE_DECIMAL_PLACES = 6
MAX_CURRENCY_DECIMALS = 3

_COUNTRY_CODE = re.compile(r"[A-Z]{2}")


class InvalidDestination(ValueError):
    """收货国家或州属代码不合法。"""


class ShippingRateMissing(LookupError):
    """应有的运费行不存在；不以零运费代替。"""


@dataclass(frozen=True)
class ShippingQuote:
    """示例运费（MYR 整数仙）、所用区域代码与该行版本号。"""

    fee_sen: int
    zone_code: str
    version: int


@dataclass(frozen=True)
class FxReference:
    """参考外币金额：以该币种最小单位计的整数，及所用汇率行的版本号。"""

    currency_code: str
    currency_decimals: int
    amount_minor: int
    version: int


def _require_country_code(country_code: object) -> str:
    if not isinstance(country_code, str) or not _COUNTRY_CODE.fullmatch(country_code):
        raise InvalidDestination(f"country code must be two uppercase letters: {country_code!r}")
    return country_code


def _require_non_negative_int(name: str, value: object) -> int:
    # bool 是 int 的子类，但把 True 当 1 仙显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def _decimal_places(value: Decimal) -> int:
    """去掉尾随零后的小数位数：Decimal("0.3100000") 算 2 位。"""
    _, digits, exponent = value.as_tuple()
    trailing_zeros = len(digits) - len("".join(map(str, digits)).rstrip("0"))
    return max(0, -(exponent + trailing_zeros))


def convert_sen(amount_sen: int, rate: Decimal, currency_decimals: int) -> int:
    """把 MYR 仙按汇率换算成该币种最小单位的整数参考金额。纯函数，不碰数据库。

    参考金额 = 仙 × 汇率 ÷ 100，按十进制定点数四舍五入到该币种小数位，恰好半个最小单位进一。
    rate 是 1 MYR 等于多少该币种，只接受 Decimal，须大于零且至多 6 位小数。
    """
    _require_non_negative_int("amount_sen", amount_sen)
    if not isinstance(rate, Decimal):
        raise TypeError(f"rate must be Decimal, got {type(rate).__name__}")
    if not rate.is_finite() or rate <= 0:
        raise ValueError(f"rate must be positive, got {rate}")
    if _decimal_places(rate) > MAX_RATE_DECIMAL_PLACES:
        raise ValueError(f"rate has more than {MAX_RATE_DECIMAL_PLACES} decimal places: {rate}")
    _require_non_negative_int("currency_decimals", currency_decimals)
    if currency_decimals > MAX_CURRENCY_DECIMALS:
        raise ValueError(f"currency_decimals must be at most 3, got {currency_decimals}")

    _, digits, exponent = rate.as_tuple()
    with localcontext() as ctx:
        # 精度留足，保证乘法与移位都精确，唯一的舍入发生在 quantize。
        # 1E+5 这样的写法只有一位数字，指数另算进去。
        ctx.prec = len(str(amount_sen)) + len(digits) + max(0, exponent) + currency_decimals + 10
        exact = (Decimal(amount_sen) * rate).scaleb(currency_decimals - 2)
        return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _zone(session: Session, zone_type: str, zone_code: str) -> ShippingRate | None:
    stmt = select(ShippingRate).where(
        ShippingRate.zone_type == zone_type,
        ShippingRate.zone_code == zone_code,
    )
    return session.scalars(stmt).one_or_none()


def quote_shipping(session: Session, country_code: str, state_code: str | None) -> ShippingQuote:
    """按收货国家与马来西亚州属查示例运费。

    马来西亚必须给 MY-01 到 MY-16 之一，缺少或未知即拒绝；其他国家给了州属代码即拒绝，
    有国家行用国家行，否则用兜底行。应有的行不存在时抛 ShippingRateMissing。
    """
    _require_country_code(country_code)

    if country_code == MALAYSIA:
        if state_code not in MY_STATE_CODES:
            raise InvalidDestination(f"Malaysia needs a state code MY-01..MY-16: {state_code!r}")
        row = _zone(session, ZONE_MY_STATE, state_code)
        if row is None:
            raise ShippingRateMissing(f"no shipping rate for state {state_code}")
    else:
        if state_code is not None:
            raise InvalidDestination(f"state code is only for Malaysia: {state_code!r}")
        row = _zone(session, ZONE_COUNTRY, country_code)
        if row is None:
            row = _zone(session, ZONE_OTHER, OTHER_ZONE_CODE)
        if row is None:
            raise ShippingRateMissing("no fallback shipping rate OTHER")

    return ShippingQuote(fee_sen=row.fee_sen, zone_code=row.zone_code, version=row.version)


def reference_amount(session: Session, country_code: str, amount_sen: int) -> FxReference | None:
    """按收货国家把 MYR 金额换算成参考外币；该国没有演示汇率（含马来西亚）时返回 None。"""
    _require_country_code(country_code)
    _require_non_negative_int("amount_sen", amount_sen)
    if country_code == MALAYSIA:
        return None

    stmt = select(DemoFxRate).where(DemoFxRate.country_code == country_code)
    row = session.scalars(stmt).one_or_none()
    if row is None:
        return None

    return FxReference(
        currency_code=row.currency_code,
        currency_decimals=row.currency_decimals,
        amount_minor=convert_sen(amount_sen, row.rate, row.currency_decimals),
        version=row.version,
    )

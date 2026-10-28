"""后台 A06 运费与演示汇率：读取整页数据、保存整页修改。

依据 docs/DESIGN.md 1.16（提交 e9a1c93）「计价、优惠、积分与库存」第 9 条
（下称「第 9 条」）与 docs/HANDOFF.md 0.67 记录的落实写法。
接口由 SHOP-TASK-171 接上，本模块只提供规则函数，不写日志。
马来西亚一组依据 DESIGN 1.17（提交 80e3775）第 10 条与 HANDOFF 0.69：即西马（MY-W）、
东马（MY-E）两档的行，名为 malaysia，按西马、东马的顺序排列（不按代码排序）。

范围：马来西亚两档与兜底各一行，只改运费、不增删；国家运费与演示汇率可改、
可新增、可删除。新增的国家须在 app/services/regions.py 的 phone_regions()
里且不是 MY；新增汇率另给币种代码（三位大写字母、不是 MYR）与小数位（0–3），
之后只改汇率。运费为 0 到 999,999 的整仙；汇率以十进制字符串提交、
不经浮点数，按数值比较，读取与审计都用规范形式（canonical_rate）。

保存（save_admin_rates）分两步，每步不通过都整次保存不生效：
1. 不碰数据库检查请求本身，不通过抛 RatesInvalid（接口回 rates_invalid）。
2. 在一个事务里先锁运费表的全部行、再锁汇率表的全部行（各按 id 排序；
   汇率表可能为空，并发保存靠先锁运费表排队），按行 ID 与版本号核对，
   不通过即回滚、抛 RatesChanged（接口回 rates_changed）。
核对通过后，值未变的行不改；改了值的行版本号加一；删除的行从表里删除；
先 flush 删除再插入新增的行（版本号 1，否则撞上区域代码与国家代码的
唯一约束）。每改、删、增一行经 record_audit 写一条审计，删后再增同一国家
各写一条。行 ID 不复用由建表时声明的自增保证，见 app/models/shipping.py
的模块说明。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import DemoFxRate, ShippingRate
from app.models.shipping import (
    MY_EAST_ZONE_CODE,
    MY_WEST_ZONE_CODE,
    ZONE_COUNTRY,
    ZONE_MY_REGION,
    ZONE_OTHER,
)
from app.services.admin_auth import record_audit
from app.services.regions import phone_regions
from app.services.shipping import MALAYSIA, MAX_CURRENCY_DECIMALS

# 审计操作名与对象类别（对象 ID 为该行 ID）。
ADMIN_SHIPPING_RATE_CHANGED = "admin_shipping_rate_changed"
ADMIN_SHIPPING_RATE_REMOVED = "admin_shipping_rate_removed"
ADMIN_SHIPPING_RATE_ADDED = "admin_shipping_rate_added"
ADMIN_FX_RATE_CHANGED = "admin_fx_rate_changed"
ADMIN_FX_RATE_REMOVED = "admin_fx_rate_removed"
ADMIN_FX_RATE_ADDED = "admin_fx_rate_added"
AUDIT_TARGET_SHIPPING_RATE = "shipping_rate"
AUDIT_TARGET_DEMO_FX_RATE = "demo_fx_rate"

# 第 9 条「0 到 999,999 仙（RM0.00–RM9,999.99）」。
MAX_FEE_SEN = 999_999
HOME_CURRENCY = "MYR"
NEW_ROW_VERSION = 1
# DESIGN 1.17 第 10 条「两行按西马、东马的顺序排列」。
_MALAYSIA_ORDER = {MY_WEST_ZONE_CODE: 0, MY_EAST_ZONE_CODE: 1}

# 照 app/services/shipping.py：只认 ASCII 大写字母，不转换大小写。
_COUNTRY_CODE = re.compile(r"[A-Z]{2}")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")
# 第 9 条「只接受 1–6 位数字，可再跟小数点与 1–6 位数字」；
# [0-9] 不含其他文字的数字。
_RATE = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6})?")


class RatesInvalid(Exception):
    """请求本身不合格（第一步）：结构、格式、取值范围或新增的国家不合格。"""


class RatesChanged(Exception):
    """请求与锁住的当前数据不符（第二步）：行已被改动或增删，或新增的国家已有行。"""


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShippingRateRow:
    """一行运费：行 ID、区域代码、运费（整仙）与版本号。"""

    id: int
    zone_code: str
    fee_sen: int
    version: int


@dataclass(frozen=True)
class FxRateRow:
    """一行演示汇率：行 ID、国家代码、币种代码、小数位、规范形式的汇率与版本号。"""

    id: int
    country_code: str
    currency_code: str
    currency_decimals: int
    rate: str
    version: int


@dataclass(frozen=True)
class AdminRates:
    """整页数据：马来西亚两档按西马、东马，国家运费与汇率按国家代码排列。

    other 为兜底行；迁移 0005 已写入它，只有库里缺这一行时为 None。
    """

    malaysia: tuple[ShippingRateRow, ...]
    other: ShippingRateRow | None
    countries: tuple[ShippingRateRow, ...]
    fx_rates: tuple[FxRateRow, ...]


def canonical_rate(value: Decimal) -> str:
    """汇率的规范形式：去掉小数部分尾随的零（全为零时连小数点一起去掉），
    整数部分不带多余的前导零、不用指数。34.000000 为 34，0.310000 为 0.31，100 为 100。"""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def load_admin_rates(db: Session) -> AdminRates:
    """读取整页数据。只发 SELECT、不写库、不写审计；只查列值，
    会话里的旧对象不会被当作当前值。"""
    zones = db.execute(
        select(
            ShippingRate.id,
            ShippingRate.zone_type,
            ShippingRate.zone_code,
            ShippingRate.fee_sen,
            ShippingRate.version,
        )
    ).all()
    # 在这里排序而不交给数据库：排序结果不随 MySQL 的排序规则变化。
    zones = sorted(zones, key=lambda zone: zone.zone_code)

    def rows_of(zone_type: str) -> tuple[ShippingRateRow, ...]:
        return tuple(
            ShippingRateRow(zone.id, zone.zone_code, zone.fee_sen, zone.version)
            for zone in zones
            if zone.zone_type == zone_type
        )

    malaysia = sorted(
        rows_of(ZONE_MY_REGION), key=lambda row: _MALAYSIA_ORDER.get(row.zone_code, 2)
    )
    others = rows_of(ZONE_OTHER)
    rates = db.execute(
        select(
            DemoFxRate.id,
            DemoFxRate.country_code,
            DemoFxRate.currency_code,
            DemoFxRate.currency_decimals,
            DemoFxRate.rate,
            DemoFxRate.version,
        )
    ).all()
    return AdminRates(
        malaysia=tuple(malaysia),
        other=others[0] if others else None,
        countries=rows_of(ZONE_COUNTRY),
        fx_rates=tuple(
            FxRateRow(
                rate.id,
                rate.country_code,
                rate.currency_code,
                rate.currency_decimals,
                canonical_rate(rate.rate),
                rate.version,
            )
            for rate in sorted(rates, key=lambda rate: rate.country_code)
        ),
    )


# ---------------------------------------------------------------------------
# 保存的请求
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeepFee:
    """保留一行运费（马来西亚档、兜底或国家）并给出新运费；id 与 version 为读取时的值。"""

    id: int
    version: int
    fee_sen: int


@dataclass(frozen=True)
class KeepFxRate:
    """保留一行汇率并给出新汇率（十进制字符串）；币种与小数位不改。"""

    id: int
    version: int
    rate: str


@dataclass(frozen=True)
class RemoveRow:
    """删除一行国家运费或汇率；id 与 version 为读取时的值。"""

    id: int
    version: int


@dataclass(frozen=True)
class AddCountryFee:
    """新增一个国家的运费。"""

    country_code: str
    fee_sen: int


@dataclass(frozen=True)
class AddFxRate:
    """新增一个国家的演示汇率：币种代码、小数位与汇率（十进制字符串）。"""

    country_code: str
    currency_code: str
    currency_decimals: int
    rate: str


CountryItem = KeepFee | RemoveRow | AddCountryFee
FxItem = KeepFxRate | RemoveRow | AddFxRate
_ADDED = (AddCountryFee, AddFxRate)


# ---------------------------------------------------------------------------
# 第一步：不碰数据库检查请求本身
# ---------------------------------------------------------------------------


def _is_int(value: object) -> bool:
    # bool 是 int 的子类，但 True 不是一个运费或小数位。
    return type(value) is int


def _check_ref(item: KeepFee | KeepFxRate | RemoveRow) -> None:
    if not _is_int(item.id) or not _is_int(item.version):
        raise RatesInvalid("row id and version must be integers")


def _check_fee(fee_sen: object) -> None:
    if not _is_int(fee_sen) or not 0 <= fee_sen <= MAX_FEE_SEN:
        raise RatesInvalid(f"fee must be an integer from 0 to {MAX_FEE_SEN}")


def _parse_rate(rate: object) -> Decimal:
    if not isinstance(rate, str) or _RATE.fullmatch(rate) is None:
        raise RatesInvalid("rate must be 1-6 digits, optionally a point and 1-6 digits")
    value = Decimal(rate)
    if value <= 0:
        raise RatesInvalid("rate must be greater than 0")
    return value


def _check_new_country(country_code: object) -> None:
    if (
        not isinstance(country_code, str)
        or _COUNTRY_CODE.fullmatch(country_code) is None
        or country_code == MALAYSIA
        or country_code not in {region.code for region in phone_regions()}
    ):
        raise RatesInvalid("country must be a checkout region other than MY")


def _check_new_currency(currency_code: object, currency_decimals: object) -> None:
    if (
        not isinstance(currency_code, str)
        or _CURRENCY_CODE.fullmatch(currency_code) is None
        or currency_code == HOME_CURRENCY
    ):
        raise RatesInvalid("currency must be three uppercase letters other than MYR")
    if not _is_int(currency_decimals) or not 0 <= currency_decimals <= MAX_CURRENCY_DECIMALS:
        raise RatesInvalid("currency decimals must be an integer from 0 to 3")


def _check_request(
    malaysia: Sequence[KeepFee],
    other: Sequence[KeepFee],
    countries: Sequence[CountryItem],
    fx_rates: Sequence[FxItem],
) -> None:
    groups = (malaysia, other, countries, fx_rates)
    if not all(isinstance(group, list | tuple) for group in groups):
        raise RatesInvalid("each group must be a list")

    # 第 9 条「马来西亚与兜底的行只有保留」。
    for item in (*malaysia, *other):
        if not isinstance(item, KeepFee):
            raise RatesInvalid("Malaysia and fallback rows can only be kept")
        _check_ref(item)
        _check_fee(item.fee_sen)
    for item in countries:
        if isinstance(item, KeepFee):
            _check_ref(item)
            _check_fee(item.fee_sen)
        elif isinstance(item, RemoveRow):
            _check_ref(item)
        elif isinstance(item, AddCountryFee):
            _check_new_country(item.country_code)
            _check_fee(item.fee_sen)
        else:
            raise RatesInvalid("unknown country fee item")
    for item in fx_rates:
        if isinstance(item, KeepFxRate):
            _check_ref(item)
            _parse_rate(item.rate)
        elif isinstance(item, RemoveRow):
            _check_ref(item)
        elif isinstance(item, AddFxRate):
            _check_new_country(item.country_code)
            _check_new_currency(item.currency_code, item.currency_decimals)
            _parse_rate(item.rate)
        else:
            raise RatesInvalid("unknown fx rate item")

    for group in groups:
        ids = [item.id for item in group if not isinstance(item, _ADDED)]
        if len(set(ids)) != len(ids):
            raise RatesInvalid("a row id is listed twice in one group")
    for group in (countries, fx_rates):
        added = [item.country_code for item in group if isinstance(item, _ADDED)]
        if len(set(added)) != len(added):
            raise RatesInvalid("a country is added twice in one group")


# ---------------------------------------------------------------------------
# 第二步：锁行核对
# ---------------------------------------------------------------------------


def lock_shipping_rates_statement() -> Select[tuple[ShippingRate]]:
    """锁定运费表全部行的查询（SELECT … FOR UPDATE，按 id 排序）；先于汇率表执行。"""
    return (
        select(ShippingRate)
        .order_by(ShippingRate.id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def lock_fx_rates_statement() -> Select[tuple[DemoFxRate]]:
    """锁定汇率表全部行的查询（SELECT … FOR UPDATE，按 id 排序）；在运费表之后执行。"""
    return (
        select(DemoFxRate)
        .order_by(DemoFxRate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _check_group(
    current: dict[int, ShippingRate] | dict[int, DemoFxRate],
    items: Sequence[object],
) -> None:
    """该组列出的行恰是现有的行，一个不多一个不少，版本号都与当前相同。"""
    refs = [item for item in items if not isinstance(item, _ADDED)]
    # 第一步已保证同一组里行 ID 不重复，集合相等即一一对应。
    if {item.id for item in refs} != current.keys():
        raise RatesChanged("rows were added or removed since they were read")
    for item in refs:
        if current[item.id].version != item.version:
            raise RatesChanged("a row was changed since it was read")


def _check_added(codes: dict[int, str], items: Sequence[object]) -> None:
    """新增的国家在该组没有行；同一请求里列为删除的行除外（先删后增）。

    codes 为该组现有的行 ID 与国家代码。
    """
    removed = {item.id for item in items if isinstance(item, RemoveRow)}
    remaining = {code for row_id, code in codes.items() if row_id not in removed}
    for item in items:
        if isinstance(item, _ADDED) and item.country_code in remaining:
            raise RatesChanged("the added country already has a row")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def _zone_value(row: ShippingRate) -> str:
    return f"{row.zone_code} {row.fee_sen} v{row.version}"


def _fx_value(row: DemoFxRate) -> str:
    return (
        f"{row.country_code} {row.currency_code} {row.currency_decimals}"
        f" {canonical_rate(row.rate)} v{row.version}"
    )


def save_admin_rates(
    db: Session,
    admin_account_id: int,
    malaysia: Sequence[KeepFee],
    other: Sequence[KeepFee],
    countries: Sequence[CountryItem],
    fx_rates: Sequence[FxItem],
    now: datetime,
) -> AdminRates:
    """保存整页的运费与演示汇率，返回保存后的整页数据（同 load_admin_rates）。

    四组依次为马来西亚两档、兜底、国家运费与汇率，列出读取时的全部现有行；
    now 为不带时区的 UTC 时间，交给 record_audit；admin_account_id 须在调用前
    取出（这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. 检查请求本身，不发任何 SQL：运费为 0 到 999,999 的整数（布尔值不算）；
       汇率字符串整串为一到六位 ASCII 数字、可再跟小数点与一到六位 ASCII 数字，
       且大于 0；新增的国家为两位 ASCII 大写字母、在 phone_regions() 里且不是 MY；
       币种代码为三位 ASCII 大写字母且不是 MYR；小数位为 0 到 3 的整数；
       同一组里行 ID 不重复，新增行之间国家不重复。不通过抛 RatesInvalid。
    2. 结束此前的事务（此前只有读取，回滚即可），先锁运费表、再锁汇率表，
       各按 id 排序，使锁定成为新事务的头两条语句。按行 ID 核对：各组恰是
       该组现有的全部行，版本号与锁住的当前值相同；新增的国家在该组没有行
       （同一请求里列为删除的除外）。不通过即回滚、抛 RatesChanged。
    3. 运费按整数、汇率按数值与当前值比较；值相同的行不改；改了值的行写新值、
       版本号加一；列为删除的行删除；flush 删除后再插入新增的行（版本号 1）。
       每改、删、增一行写一条审计，旧值与新值为「区域代码 运费仙数 v版本号」
       或「国家代码 币种代码 小数位 规范汇率 v版本号」；新增的行没有旧值、
       删除的行没有新值。
    4. 整页都未变时回滚、不写审计；否则一并提交。
    """
    _check_request(malaysia, other, countries, fx_rates)

    db.rollback()
    try:
        zones = db.scalars(lock_shipping_rates_statement()).all()
        rates = db.scalars(lock_fx_rates_statement()).all()

        def zones_of(zone_type: str) -> dict[int, ShippingRate]:
            return {zone.id: zone for zone in zones if zone.zone_type == zone_type}

        country_zones = zones_of(ZONE_COUNTRY)
        _check_group(zones_of(ZONE_MY_REGION), malaysia)
        _check_group(zones_of(ZONE_OTHER), other)
        _check_group(country_zones, countries)
        _check_group({rate.id: rate for rate in rates}, fx_rates)
        _check_added({zone.id: zone.zone_code for zone in country_zones.values()}, countries)
        _check_added({rate.id: rate.country_code for rate in rates}, fx_rates)

        changed = False

        def audit(
            action: str,
            target_type: str,
            target_id: int,
            old_value: str | None,
            new_value: str | None,
        ) -> None:
            nonlocal changed
            changed = True
            record_audit(
                db,
                action,
                admin_account_id,
                now,
                target_type=target_type,
                target_id=target_id,
                old_value=old_value,
                new_value=new_value,
            )

        # 三组运费的行 ID 互不重叠（核对已保证各组恰是该组现有的行）。
        zone_items = {
            item.id: item
            for item in (*malaysia, *other, *countries)
            if not isinstance(item, _ADDED)
        }
        for zone in zones:
            item = zone_items[zone.id]
            old_value = _zone_value(zone)
            if isinstance(item, RemoveRow):
                zone_id = zone.id
                db.delete(zone)
                audit(
                    ADMIN_SHIPPING_RATE_REMOVED,
                    AUDIT_TARGET_SHIPPING_RATE,
                    zone_id,
                    old_value,
                    None,
                )
            elif zone.fee_sen != item.fee_sen:
                zone.fee_sen = item.fee_sen
                zone.version += 1
                audit(
                    ADMIN_SHIPPING_RATE_CHANGED,
                    AUDIT_TARGET_SHIPPING_RATE,
                    zone.id,
                    old_value,
                    _zone_value(zone),
                )

        rate_items = {item.id: item for item in fx_rates if not isinstance(item, _ADDED)}
        for rate in rates:
            item = rate_items[rate.id]
            old_value = _fx_value(rate)
            if isinstance(item, RemoveRow):
                rate_id = rate.id
                db.delete(rate)
                audit(ADMIN_FX_RATE_REMOVED, AUDIT_TARGET_DEMO_FX_RATE, rate_id, old_value, None)
            else:
                new_rate = Decimal(item.rate)
                if rate.rate != new_rate:
                    rate.rate = new_rate
                    rate.version += 1
                    audit(
                        ADMIN_FX_RATE_CHANGED,
                        AUDIT_TARGET_DEMO_FX_RATE,
                        rate.id,
                        old_value,
                        _fx_value(rate),
                    )

        # 先把删除写进库再插入：同一次 flush 里 ORM 先插后删，
        # 删后再增同一国家会撞上唯一约束。
        db.flush()
        for item in countries:
            if isinstance(item, AddCountryFee):
                zone = ShippingRate(
                    zone_type=ZONE_COUNTRY,
                    zone_code=item.country_code,
                    fee_sen=item.fee_sen,
                    version=NEW_ROW_VERSION,
                )
                db.add(zone)
                db.flush()
                audit(
                    ADMIN_SHIPPING_RATE_ADDED,
                    AUDIT_TARGET_SHIPPING_RATE,
                    zone.id,
                    None,
                    _zone_value(zone),
                )
        for item in fx_rates:
            if isinstance(item, AddFxRate):
                rate = DemoFxRate(
                    country_code=item.country_code,
                    currency_code=item.currency_code,
                    currency_decimals=item.currency_decimals,
                    rate=Decimal(item.rate),
                    version=NEW_ROW_VERSION,
                )
                db.add(rate)
                db.flush()
                audit(
                    ADMIN_FX_RATE_ADDED,
                    AUDIT_TARGET_DEMO_FX_RATE,
                    rate.id,
                    None,
                    _fx_value(rate),
                )

        saved = load_admin_rates(db)
    except BaseException:
        db.rollback()
        raise

    if changed:
        db.commit()
    else:
        db.rollback()
    return saved

"""运费区与演示汇率。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 ShippingRate / DemoFxRate 一行：
「国家运费、马来西亚州属运费、“其他国家”兜底运费；参考币种固定汇率及版本。
下单存计算规则版本与金额快照」，以及「边界与原则」：金额以 MYR 的仙为整数单位，
其他货币只按固定演示汇率显示参考数，不参与结算。

运费是 MYR 整数仙。汇率不是金额，是全项目唯一用定点小数存的数。
每行的版本号从 1 开始，之后的后台维护每改一行费率把该行版本号加一。

检查约束只用 LENGTH、LIKE、比较与 IN，MySQL 与 SQLite 都能执行。
MySQL 默认排序规则不区分大小写，数据库不保证代码是大写字母：
大小写与字母是否合法由写入方（数据迁移、之后的后台维护）与查询函数校验。
代码是否是真实存在的国家不在这里校验。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 运费区类型。
ZONE_MY_STATE = "my_state"
ZONE_COUNTRY = "country"
ZONE_OTHER = "other"

# 兜底行固定的区域代码。
OTHER_ZONE_CODE = "OTHER"


class ShippingRate(Base):
    """一个运费区的示例运费。

    州属行的区域代码是 ISO 3166-2 马来西亚代码 MY-01 到 MY-16；国家行是 ISO 3166-1
    两位代码格式且不是 MY；兜底行是 OTHER。数据库只保证前缀与长度，
    具体是哪 16 个州属代码由写入方与查询函数校验。
    """

    __tablename__ = "shipping_rates"
    __table_args__ = (
        UniqueConstraint("zone_code"),
        CheckConstraint(
            "(zone_type = 'my_state' AND LENGTH(zone_code) = 5 AND zone_code LIKE 'MY-%')"
            " OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY')"
            " OR (zone_type = 'other' AND zone_code = 'OTHER')",
            name="zone_code_matches_type",
        ),
        CheckConstraint("fee_sen >= 0", name="fee_sen_non_negative"),
        CheckConstraint("version >= 1", name="version_positive"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_type: Mapped[str] = mapped_column(String(10))
    zone_code: Mapped[str] = mapped_column(String(5))
    fee_sen: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)


class DemoFxRate(Base):
    """一个收货国家的固定演示汇率：1 MYR 等于 rate 个该币种。

    币种代码是 ISO 4217 三位代码格式；currency_decimals 是该币种的小数位，
    参考金额以该币种最小单位的整数计。没有行的国家只显示 MYR。
    """

    __tablename__ = "demo_fx_rates"
    __table_args__ = (
        UniqueConstraint("country_code"),
        CheckConstraint(
            "LENGTH(country_code) = 2 AND country_code <> 'MY'",
            name="country_code_valid",
        ),
        CheckConstraint(
            "LENGTH(currency_code) = 3 AND currency_code <> 'MYR'",
            name="currency_code_valid",
        ),
        CheckConstraint(
            "currency_decimals >= 0 AND currency_decimals <= 3",
            name="currency_decimals_range",
        ),
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2))
    currency_code: Mapped[str] = mapped_column(String(3))
    currency_decimals: Mapped[int] = mapped_column(Integer)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 6, asdecimal=True))
    version: Mapped[int] = mapped_column(Integer)

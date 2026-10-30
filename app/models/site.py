"""站点设置：全站只有一行的运营开关表。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条（短信验证
开关，默认关闭），与 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的
SiteSetting 一行：「站点级运营开关：短信验证开关（布尔，默认关闭）与支付
方式（模拟 / Stripe 测试，默认模拟）。每次判定都从数据库读取当前值、不
缓存，修改对之后的请求立即生效」，以及「Stripe 测试环境支付」第 2 条：
「`SiteSetting` 的支付方式取『模拟』（默认）或『Stripe 测试』，与短信验证
开关一样每次从数据库读取、修改写审计」。

每日恢复库存开关依据 docs/DESIGN.md 1.18（提交 5ba9796）同一行（「每日恢复
库存开关（1.18，布尔，默认开启）」）与「计价、优惠、积分与库存」第 6 条。

表只有一行，主键固定为 1，由检查约束保证；迁移 0010 写入这一行（开关
关闭），迁移 0020 加支付方式列，已有的那一行取服务端默认值 simulated；
迁移 0024 加每日恢复库存开关列，已有的那一行取服务端默认值（开启）。
读取这个开关的定时任务与修改它的接口是之后的任务。
短信验证开关的读取见 app/services/site_settings.py，支付方式的读取由
SHOP-TASK-112 加到同一文件。时间是不带时区的 UTC。修改设置的接口与审计
记录（AuditEvent）由之后的管理后台业务接口任务实现，这里不加其他设置项。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 唯一一行的主键。
SITE_SETTING_ID = 1

# 支付方式：模拟（默认）与 Stripe 测试。
PAYMENT_METHOD_SIMULATED = "simulated"
PAYMENT_METHOD_STRIPE_TEST = "stripe_test"
PAYMENT_METHODS = (PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST)
PAYMENT_METHOD_MAX_LENGTH = 16


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class SiteSetting(Base):
    """站点级运营开关，全表只有主键为 1 的一行。"""

    __tablename__ = "site_settings"
    __table_args__ = (
        CheckConstraint(f"id = {SITE_SETTING_ID}", name="single_row"),
        CheckConstraint(
            f"payment_method IN {_sql_in(PAYMENT_METHODS)}",
            name="payment_method_valid",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    # autoincrement=False：主键只能是 1，不让 MySQL 给它 AUTO_INCREMENT。
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # 短信验证开关；服务端默认关闭。
    sms_verification_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # 支付方式；服务端默认模拟。
    payment_method: Mapped[str] = mapped_column(
        String(PAYMENT_METHOD_MAX_LENGTH),
        server_default=PAYMENT_METHOD_SIMULATED,
    )
    # 每日恢复库存开关；服务端默认开启。
    daily_stock_reset_enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

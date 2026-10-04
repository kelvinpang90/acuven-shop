"""站点设置：全站只有一行的运营开关表。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条（短信验证开关，默认关闭）与
「数据模型」的 SiteSetting 一行：「首版只有短信验证开关（布尔，默认关闭）。每次判定都从数据库
读取当前值、不缓存，修改对之后的请求立即生效」。

表只有一行，主键固定为 1，由检查约束保证；迁移 0010 写入这一行（开关关闭）。读取见
app/services/site_settings.py。时间是不带时区的 UTC。修改开关的接口与审计记录（AuditEvent）
由之后的管理后台业务接口任务实现，这里不加其他设置项。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 唯一一行的主键。
SITE_SETTING_ID = 1


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class SiteSetting(Base):
    """站点级运营开关，全表只有主键为 1 的一行。"""

    __tablename__ = "site_settings"
    __table_args__ = (
        CheckConstraint(f"id = {SITE_SETTING_ID}", name="single_row"),
        MYSQL_TABLE_OPTIONS,
    )

    # autoincrement=False：主键只能是 1，不让 MySQL 给它 AUTO_INCREMENT。
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # 短信验证开关；服务端默认关闭。
    sms_verification_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

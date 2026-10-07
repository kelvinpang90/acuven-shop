"""店铺装修设置：主题与主色、首页四个区块的顺序与显隐、精选商品。

依据 docs/REQUIREMENTS.md「店铺装修」（10 款预设主题选一款；主色只能从该主题预先校过对比度的
选项中选；首页由主视觉、演示怎么玩、按分类浏览、精选商品四个区块组成，管理员调整顺序与显隐；
精选商品最多 4 件已上架商品并排定顺序）、docs/UX.md 0.8 的 A08 与 P01，以及 docs/HANDOFF.md
0.34 记录的 Kelvin 2026-10-06 决定（存储首版不含标志图）。主题与主色的取值以
docs/design/tokens/themes.json 为准（主题 id 与各自 accentOptions 的 id），由测试守住一致。

时间一律是不带时区的 UTC。外键一律 RESTRICT：被精选的商品不能物理删除。
检查约束只用比较、LENGTH、IN 与 IS NULL，MySQL 与 SQLite 都能执行。主题、区块与主色 id 都是
ASCII，MySQL 按字节计的 LENGTH 与字符数相同。
这里只建表、约束与常量：读取与公开接口、后台修改与审计、前台显示都由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 每款主题的可选主色 id，顺序与 themes.json 相同（主题按列出顺序，主色第一项即该主题的默认主色）。
# 迁移 0016 写入默认值、之后的读取与写入校验都以这里为准；写入方须校验主色属于所选主题。
THEME_ACCENTS: dict[str, tuple[str, ...]] = {
    "pandan": ("pandan", "teal", "clay", "aubergine", "charcoal"),
    "pasar": ("green", "tomato", "blue", "plum", "ink"),
    "receipt": ("ink", "cobalt", "forest", "oxblood"),
    "kopitiam": ("kopi", "teh", "tile", "red"),
    "batik": ("indigo", "sogan", "maroon", "teal"),
    "malam": ("pink", "violet", "orange", "lime"),
    "gula": ("pink", "mint", "grape", "orange"),
    "galeri": ("black", "graphite", "navy", "bottle"),
    "songket": ("maroon", "emerald", "royal", "black"),
    "litar": ("teal", "violet", "red", "graphite"),
}
THEMES: tuple[str, ...] = tuple(THEME_ACCENTS)

THEME_MAX_LENGTH = 20
ACCENT_MAX_LENGTH = 20

# 首页四个区块：主视觉、演示怎么玩、按分类浏览、精选商品。
BLOCK_HERO = "hero"
BLOCK_HOW = "how"
BLOCK_CATEGORIES = "categories"
BLOCK_FEATURED = "featured"
HOME_BLOCKS: tuple[str, ...] = (BLOCK_HERO, BLOCK_HOW, BLOCK_CATEGORIES, BLOCK_FEATURED)
BLOCK_MAX_LENGTH = 20

# 精选商品最多 4 件。
FEATURED_MAX = 4

# 默认值：主题 pandan、主色为空（即该主题的默认主色），四个区块按上面的顺序全部显示，
# 没有精选商品。每个区块是（区块、位置、是否显示）。
DEFAULT_THEME = "pandan"
DEFAULT_ACCENT: str | None = None
DEFAULT_HOME_BLOCKS: tuple[tuple[str, int, bool], ...] = (
    (BLOCK_HERO, 1, True),
    (BLOCK_HOW, 2, True),
    (BLOCK_CATEGORIES, 3, True),
    (BLOCK_FEATURED, 4, True),
)

# 单例槽唯一的合法值：设置表的这一列全表唯一且只能是 1，故至多一行。
SINGLETON_SLOT = 1


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class StoreDesignSetting(Base):
    """店铺的主题与主色，全表只有一行（迁移 0016 写入）。

    主色为空即该主题的默认主色（themes.json 里该主题 accentOptions 的第一项）。主色是否属于
    所选主题的可选主色由写入方按 THEME_ACCENTS 校验，库里只检查长度 1 到 20。
    """

    __tablename__ = "store_design_settings"
    __table_args__ = (
        UniqueConstraint("singleton_slot"),
        CheckConstraint(f"singleton_slot = {SINGLETON_SLOT}", name="singleton_slot_one"),
        CheckConstraint(f"theme IN ({_in_list(THEMES)})", name="theme_known"),
        CheckConstraint(
            f"accent IS NULL OR (LENGTH(accent) >= 1 AND LENGTH(accent) <= {ACCENT_MAX_LENGTH})",
            name="accent_length",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    singleton_slot: Mapped[int] = mapped_column(Integer, default=SINGLETON_SLOT)
    theme: Mapped[str] = mapped_column(String(THEME_MAX_LENGTH))
    accent: Mapped[str | None] = mapped_column(String(ACCENT_MAX_LENGTH))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class StoreHomeBlock(Base):
    """首页的一个区块及其位置与是否显示。

    四个区块各一行、位置 1 到 4 各一行：四行由迁移 0016 写入，之后的写入须在同一事务里保持
    四行齐全（只改位置与是否显示，不增删区块）；库里不另加行数约束。区块与位置各自全表唯一，
    逐行就地交换位置会在中途撞上位置的唯一约束，写入方须在同一事务里另行处理（如先删后写四行）。
    ★ home.demo_hint 不属于任何区块，不在这张表里。
    """

    __tablename__ = "store_home_blocks"
    __table_args__ = (
        UniqueConstraint("block"),
        UniqueConstraint("position"),
        CheckConstraint(f"block IN ({_in_list(HOME_BLOCKS)})", name="block_known"),
        CheckConstraint(
            f"position >= 1 AND position <= {len(HOME_BLOCKS)}",
            name="position_range",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    block: Mapped[str] = mapped_column(String(BLOCK_MAX_LENGTH))
    position: Mapped[int] = mapped_column(Integer)
    is_visible: Mapped[bool] = mapped_column(Boolean)


class StoreFeaturedProduct(Base):
    """管理员挑选的一件精选商品及其位置；位置 1 到 4 且全表唯一，故至多四行。

    同一商品只能精选一次。挑选时须是已上架商品由写入方校验；之后下架的商品仍留在这里，
    前台按目录可见判定不显示它（docs/UX.md A08、P01）。
    """

    __tablename__ = "store_featured_products"
    __table_args__ = (
        UniqueConstraint("position"),
        UniqueConstraint("product_id"),
        CheckConstraint(f"position >= 1 AND position <= {FEATURED_MAX}", name="position_range"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="RESTRICT"),
    )

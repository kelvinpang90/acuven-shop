"""节日促销活动：活动、规则、规则适用的商品与分类、满额档、装饰与发布锁。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「数据模型」的 Promotion /
PromotionRule / PromotionDecoration 一行与「节日促销活动」第 1–3、12 条
（下称「第 N 条」），以及 SHOP-TASK-229 写定的表名、列名、类型与约束。
素材包与装饰位变体的 id 以 docs/design/tokens/promotions.json（视觉稿导出的
清单，HANDOFF 0.94）为准，由测试守住逐项相同；第 12 条「任一 ID 不在清单里
即回答取值不合格」由写入方回答，库里的检查约束是最后一道。

时间一律是不带时区的 UTC（MySQL 上精确到秒）；金额一律是整数仙。
删除活动时连同规则、范围、满额档与装饰一起删除（CASCADE）；规则范围指向
商品与分类的外键 RESTRICT：被规则引用的商品与分类不能物理删除。
检查约束只用比较、LENGTH、IN 与 IS NULL，MySQL 与 SQLite 都能执行；
库里只查单行就能判断的取值。设计里保存时、发布时才检查的条件（时长不超过
92 天、发布条件、已发布活动的时间不重叠、每个活动的规则条数与类别上限、
排序从 1 连续、各档逐档递增、范围的件数上限、清单之外的组合）由之后的
服务层保证，库里不查。
这里只建表、约束与常量：计价、后台、展示与 AI 每日计数都由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 活动只有草稿与已发布两种状态；未开始、进行中、已结束由时间算出（第 2 条）。
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
PROMOTION_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)

# 规则的四类（第 3 条）：商品级打折、买 X 享 Y（商品级），
# 满额减或满额折、满额免运费（整单级）。
KIND_ITEM_DISCOUNT = "item_discount"
KIND_BUY_X_GET_Y = "buy_x_get_y"
KIND_SPEND_DISCOUNT = "spend_discount"
KIND_FREE_SHIPPING = "free_shipping"
RULE_KINDS = (KIND_ITEM_DISCOUNT, KIND_BUY_X_GET_Y, KIND_SPEND_DISCOUNT, KIND_FREE_SHIPPING)

# 商品级打折与满额减或满额折的优惠方式：百分比，或固定金额（每件直降、
# 每档减固定金额）。
MODE_PERCENT = "percent"
MODE_AMOUNT = "amount"
DISCOUNT_MODES = (MODE_PERCENT, MODE_AMOUNT)

# 海报的两种模式（第 12 条）：模板与上传图。
POSTER_MODE_TEMPLATE = "template"
POSTER_MODE_IMAGE = "image"
POSTER_MODES = (POSTER_MODE_TEMPLATE, POSTER_MODE_IMAGE)

# 素材包 id，顺序同 promotions.json 的 packs（第 12 条，首版 7 套）。
PROMOTION_PACKS: tuple[str, ...] = (
    "cny",
    "raya",
    "deepavali",
    "christmas",
    "merdeka",
    "mega_sale",
    "generic",
)
# 装饰位 id → 变体 id，顺序同 promotions.json 的 slots。
PROMOTION_SLOTS: dict[str, tuple[str, ...]] = {
    "ambient": ("garland", "corners", "fall"),
    "home_poster": ("split", "centered"),
    "list_strip": ("band", "ticket"),
    "card_badge": ("tag", "seal"),
    "detail_ribbon": ("ribbon", "panel"),
    "cart_progress": ("bar", "steps"),
}
# 配色保持店铺主题；氛围装饰可以不要。
PALETTE_KEEP_THEME = "theme"
AMBIENT_NONE = "none"

# 装饰表每个 ID 列允许的取值：素材包、配色（保持主题或任一素材包的配色）
# 与各装饰位的变体（氛围另可为 none）。
DECORATION_CHOICES: dict[str, tuple[str, ...]] = {
    "pack": PROMOTION_PACKS,
    "palette": (PALETTE_KEEP_THEME, *PROMOTION_PACKS),
    **PROMOTION_SLOTS,
    "ambient": (*PROMOTION_SLOTS["ambient"], AMBIENT_NONE),
}
DECORATION_ID_MAX_LENGTH = 16

NAME_MAX_LENGTH = 100
PERCENT_MIN = 1
# 商品级打折与满额折的百分比上限；买 X 享 Y 的享受折扣可到 100（即送）。
DISCOUNT_PERCENT_MAX = 90
GET_PERCENT_MAX = 100
# 每件直降的上限（仙）。
ITEM_AMOUNT_MAX_SEN = 50_000_000
# 买 X 享 Y 的 X 与 Y。
QUANTITY_MIN = 1
QUANTITY_MAX = 10
# 满额档与免运费门槛的上限（仙）。
THRESHOLD_MAX_SEN = 99_999_999
SPEND_TIERS_MAX = 5

# 海报图沿用店铺标志的五列与三条约束（store_design.py）：上传格式与
# 展示图的尺寸上限。
POSTER_SOURCE_FORMATS = ("jpeg", "png", "webp")
POSTER_DISPLAY_MAX = 1600
POSTER_PREFIXES = ("poster_desktop_", "poster_phone_")

# 单例槽唯一的合法值：发布锁表的这一列全表唯一且只能是 1，故至多一行。
SINGLETON_SLOT = 1


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


def _poster_checks(prefix: str) -> tuple[CheckConstraint, ...]:
    """照 StoreDesignSetting 标志五列的三条约束，把 logo_ 换成 prefix。"""
    p = prefix
    return (
        CheckConstraint(
            f"({p}file_name IS NULL AND {p}source_format IS NULL AND {p}width IS NULL"
            f" AND {p}height IS NULL AND {p}byte_size IS NULL)"
            f" OR ({p}file_name IS NOT NULL AND {p}source_format IS NOT NULL"
            f" AND {p}width IS NOT NULL AND {p}height IS NOT NULL"
            f" AND {p}byte_size IS NOT NULL)",
            name=f"{p}all_or_none",
        ),
        CheckConstraint(
            f"{p}source_format IS NULL OR {p}source_format IN ({_in_list(POSTER_SOURCE_FORMATS)})",
            name=f"{p}source_format_valid",
        ),
        CheckConstraint(
            f"{p}width IS NULL OR ({p}width >= 1 AND {p}width <= {POSTER_DISPLAY_MAX}"
            f" AND {p}height >= 1 AND {p}height <= {POSTER_DISPLAY_MAX} AND {p}byte_size >= 1)",
            name=f"{p}display_size_range",
        ),
    )


class Promotion(Base):
    """一个节日促销活动（第 1、2 条）。

    草稿可以不填时间，发布时开始与结束都须填（docs/UX.md 0.20 的 A12）；
    复制为新草稿时不带时间、沿用原活动的名称，所以名称非空、时间可空。
    提前结束的时刻只出现在已发布的活动上，落在开始（含）与结束（不含）之间；
    它由之后的服务层截到整秒再写入，免得四舍五入到结束时间。
    版本号新建为 1，有改动时加一，供保存、发布、撤回与提前结束核对
    （第 2 条「并发」）。按（状态、开始时间）的索引供之后查找生效的与
    可能重叠的已发布活动。
    """

    __tablename__ = "promotions"
    __table_args__ = (
        Index("ix_promotions_status_starts_at", "status", "starts_at"),
        CheckConstraint("LENGTH(name) >= 1", name="name_not_empty"),
        CheckConstraint(f"status IN ({_in_list(PROMOTION_STATUSES)})", name="status_valid"),
        CheckConstraint(
            f"status <> '{STATUS_PUBLISHED}' OR (starts_at IS NOT NULL AND ends_at IS NOT NULL)",
            name="published_has_times",
        ),
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="ends_after_starts",
        ),
        CheckConstraint(
            f"ended_early_at IS NULL OR (status = '{STATUS_PUBLISHED}'"
            " AND ended_early_at >= starts_at AND ended_early_at < ends_at)",
            name="ended_early_within",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 内部名称，只在后台显示（1–100 个字符）。
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    status: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_early_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 可与优惠券同用、可与积分同用（第 6 条），默认都关闭。
    coupon_combinable: Mapped[bool] = mapped_column(Boolean, default=False)
    points_combinable: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PromotionRule(Base):
    """活动的一条规则：类别、参数与在活动内的排序（第 3 条）。

    每类只用自己的参数列，其余参数列为空；满额减或满额折的各档在
    PromotionSpendTier，商品级规则的适用范围在 PromotionRuleProduct 与
    PromotionRuleCategory。排序在活动内唯一；从 1 起连续、条数与类别上限
    由写入方保证。
    """

    __tablename__ = "promotion_rules"
    __table_args__ = (
        UniqueConstraint("promotion_id", "position"),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint(f"kind IN ({_in_list(RULE_KINDS)})", name="kind_valid"),
        # 每类一条；未知类别只由 kind_valid 拒绝。discount_mode 先判非空，
        # 免得它为空时整条表达式求得 NULL 而被放行。
        CheckConstraint(
            f"kind <> '{KIND_ITEM_DISCOUNT}' OR (discount_mode IS NOT NULL"
            f" AND ((discount_mode = '{MODE_PERCENT}' AND percent IS NOT NULL"
            f" AND percent >= {PERCENT_MIN} AND percent <= {DISCOUNT_PERCENT_MAX}"
            " AND amount_sen IS NULL)"
            f" OR (discount_mode = '{MODE_AMOUNT}' AND amount_sen IS NOT NULL"
            f" AND amount_sen >= 1 AND amount_sen <= {ITEM_AMOUNT_MAX_SEN} AND percent IS NULL))"
            " AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent IS NULL"
            " AND threshold_sen IS NULL)",
            name="item_discount_params",
        ),
        CheckConstraint(
            f"kind <> '{KIND_BUY_X_GET_Y}' OR (buy_quantity IS NOT NULL"
            f" AND buy_quantity >= {QUANTITY_MIN} AND buy_quantity <= {QUANTITY_MAX}"
            " AND get_quantity IS NOT NULL"
            f" AND get_quantity >= {QUANTITY_MIN} AND get_quantity <= {QUANTITY_MAX}"
            " AND get_percent IS NOT NULL"
            f" AND get_percent >= {PERCENT_MIN} AND get_percent <= {GET_PERCENT_MAX}"
            " AND discount_mode IS NULL AND percent IS NULL AND amount_sen IS NULL"
            " AND threshold_sen IS NULL)",
            name="buy_x_get_y_params",
        ),
        CheckConstraint(
            f"kind <> '{KIND_SPEND_DISCOUNT}' OR (discount_mode IS NOT NULL"
            f" AND discount_mode IN ({_in_list(DISCOUNT_MODES)})"
            " AND percent IS NULL AND amount_sen IS NULL AND buy_quantity IS NULL"
            " AND get_quantity IS NULL AND get_percent IS NULL AND threshold_sen IS NULL)",
            name="spend_discount_params",
        ),
        CheckConstraint(
            f"kind <> '{KIND_FREE_SHIPPING}' OR (threshold_sen IS NOT NULL"
            f" AND threshold_sen >= 0 AND threshold_sen <= {THRESHOLD_MAX_SEN}"
            " AND discount_mode IS NULL AND percent IS NULL AND amount_sen IS NULL"
            " AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent IS NULL)",
            name="free_shipping_params",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promotion_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promotions.id", ondelete="CASCADE"),
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    # 商品级打折与满额减或满额折：percent 或 amount；其余两类为空。
    discount_mode: Mapped[str | None] = mapped_column(String(8))
    # 商品级打折的百分比（1–90）或每件直降（仙，1–50,000,000）。
    percent: Mapped[int | None] = mapped_column(Integer)
    amount_sen: Mapped[int | None] = mapped_column(Integer)
    # 买 X 享 Y：X、Y（1–10）与享受的折扣百分比（1–100，100 即送）。
    buy_quantity: Mapped[int | None] = mapped_column(Integer)
    get_quantity: Mapped[int | None] = mapped_column(Integer)
    get_percent: Mapped[int | None] = mapped_column(Integer)
    # 满额免运费的门槛（仙，0–99,999,999，0 即全部订单免运费）。
    threshold_sen: Mapped[int | None] = mapped_column(Integer)


class PromotionRuleProduct(Base):
    """商品级规则适用的一件商品。

    件数上限与一件商品至多属于一条商品级规则由写入方保证。
    """

    __tablename__ = "promotion_rule_products"
    __table_args__ = (
        UniqueConstraint("rule_id", "product_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promotion_rules.id", ondelete="CASCADE"),
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="RESTRICT"),
    )


class PromotionRuleCategory(Base):
    """商品级规则适用的一个分类；分类按商品当时所属分类判断（第 3 条）。"""

    __tablename__ = "promotion_rule_categories"
    __table_args__ = (
        UniqueConstraint("rule_id", "category_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promotion_rules.id", ondelete="CASCADE"),
    )
    category_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("categories.id", ondelete="RESTRICT"),
    )


class PromotionSpendTier(Base):
    """满额减或满额折的一档：门槛与一个优惠（第 3 条，1–5 档）。

    每档恰有固定金额与百分比之一；全部档同一种方式（与规则的
    discount_mode 一致）、门槛与优惠逐档严格递增由写入方保证。
    """

    __tablename__ = "promotion_spend_tiers"
    __table_args__ = (
        UniqueConstraint("rule_id", "position"),
        CheckConstraint(f"position >= 1 AND position <= {SPEND_TIERS_MAX}", name="position_range"),
        CheckConstraint(
            f"threshold_sen >= 1 AND threshold_sen <= {THRESHOLD_MAX_SEN}",
            name="threshold_range",
        ),
        CheckConstraint(
            "(amount_sen IS NOT NULL AND percent IS NULL)"
            " OR (amount_sen IS NULL AND percent IS NOT NULL)",
            name="amount_or_percent",
        ),
        CheckConstraint(
            "amount_sen IS NULL OR (amount_sen >= 1 AND amount_sen < threshold_sen)",
            name="amount_below_threshold",
        ),
        CheckConstraint(
            f"percent IS NULL OR (percent >= {PERCENT_MIN} AND percent <= {DISCOUNT_PERCENT_MAX})",
            name="percent_range",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promotion_rules.id", ondelete="CASCADE"),
    )
    position: Mapped[int] = mapped_column(Integer)
    threshold_sen: Mapped[int] = mapped_column(Integer)
    # 减固定金额（仙，1 到小于门槛）或打百分比（1–90），恰有一个不为空。
    amount_sen: Mapped[int | None] = mapped_column(Integer)
    percent: Mapped[int | None] = mapped_column(Integer)


class PromotionDecoration(Base):
    """活动的装饰、海报与三语文案（第 12 条），每个活动至多一行。

    素材包、配色与六个装饰位各存一个清单里的 id，取值见 DECORATION_CHOICES。
    海报图两张（桌面、手机）各五列，照店铺标志：服务端生成的文件名、
    上传时的格式与展示图的宽、高与字节数，同为空或同不为空。
    文案各列可空：空串由写入方存为空，字符数上限由列长保证；英文标题必填、
    上传图模式须两张图与英文替代文字齐全，都是发布条件，由写入方检查。
    """

    __tablename__ = "promotion_decorations"
    __table_args__ = (
        UniqueConstraint("promotion_id"),
        *(
            CheckConstraint(f"{column} IN ({_in_list(values)})", name=f"{column}_known")
            for column, values in DECORATION_CHOICES.items()
        ),
        CheckConstraint(f"poster_mode IN ({_in_list(POSTER_MODES)})", name="poster_mode_valid"),
        *_poster_checks("poster_desktop_"),
        *_poster_checks("poster_phone_"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promotion_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promotions.id", ondelete="CASCADE"),
    )
    pack: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    palette: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    ambient: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    home_poster: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    list_strip: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    card_badge: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    detail_ribbon: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    cart_progress: Mapped[str] = mapped_column(String(DECORATION_ID_MAX_LENGTH))
    poster_mode: Mapped[str] = mapped_column(String(8), default=POSTER_MODE_TEMPLATE)
    # 桌面海报图：文件名（32 位小写十六进制）、上传时的格式与
    # 展示图（WebP）的宽、高与字节数。
    poster_desktop_file_name: Mapped[str | None] = mapped_column(String(32))
    poster_desktop_source_format: Mapped[str | None] = mapped_column(String(4))
    poster_desktop_width: Mapped[int | None] = mapped_column(Integer)
    poster_desktop_height: Mapped[int | None] = mapped_column(Integer)
    poster_desktop_byte_size: Mapped[int | None] = mapped_column(Integer)
    # 手机海报图，五列同上。
    poster_phone_file_name: Mapped[str | None] = mapped_column(String(32))
    poster_phone_source_format: Mapped[str | None] = mapped_column(String(4))
    poster_phone_width: Mapped[int | None] = mapped_column(Integer)
    poster_phone_height: Mapped[int | None] = mapped_column(Integer)
    poster_phone_byte_size: Mapped[int | None] = mapped_column(Integer)
    # 上传图的替代文字（1–150）、标题（1–40）、副标题（0–80）、
    # 角标短语（0–12）、横条文字（0–60），各有英文、中文、马来文。
    poster_alt_en: Mapped[str | None] = mapped_column(String(150))
    poster_alt_zh: Mapped[str | None] = mapped_column(String(150))
    poster_alt_ms: Mapped[str | None] = mapped_column(String(150))
    title_en: Mapped[str | None] = mapped_column(String(40))
    title_zh: Mapped[str | None] = mapped_column(String(40))
    title_ms: Mapped[str | None] = mapped_column(String(40))
    subtitle_en: Mapped[str | None] = mapped_column(String(80))
    subtitle_zh: Mapped[str | None] = mapped_column(String(80))
    subtitle_ms: Mapped[str | None] = mapped_column(String(80))
    badge_en: Mapped[str | None] = mapped_column(String(12))
    badge_zh: Mapped[str | None] = mapped_column(String(12))
    badge_ms: Mapped[str | None] = mapped_column(String(12))
    strip_en: Mapped[str | None] = mapped_column(String(60))
    strip_zh: Mapped[str | None] = mapped_column(String(60))
    strip_ms: Mapped[str | None] = mapped_column(String(60))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class PromotionPublishLock(Base):
    """发布与改时间时先锁住的专用单行锁表（第 2 条「并发」）。

    全表只有一行，由迁移 0027 写入。
    """

    __tablename__ = "promotion_publish_locks"
    __table_args__ = (
        UniqueConstraint("singleton_slot"),
        CheckConstraint(f"singleton_slot = {SINGLETON_SLOT}", name="singleton_slot_one"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    singleton_slot: Mapped[int] = mapped_column(Integer, default=SINGLETON_SLOT)

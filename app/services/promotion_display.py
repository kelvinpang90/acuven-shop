"""节日促销活动的展示数据（服务层）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 10、12 条（下称「第 N 条」）
与 SHOP-TASK-243 的落实写法：promotion_display 按活动 ID 组装一个活动的展示数据，
不判断是否生效；公开接口（app/api/promotion.py）先以 SHOP-TASK-233 的 active_promotion
取生效活动的 ID 再调用它，之后的管理员预览按活动 ID 复用同一函数。

只给展示数据：活动 ID、所选语言的文案（缺译回退英文，英文也空即 None）、装饰与海报设置、
海报图的提供路径（SHOP-TASK-240 的 poster_path）与替代文字、实际结束时间、两个同用开关
与每条规则的类别与参数（前台按三语文案模板生成规则说明，第 10 条）。不返回内部名称、
版本与任何个人资料。商品级规则的范围只给分类（启用且有英文名称的，slug 与所选语言的
名称，按分类 ID 升序）与「是否另选了上架的单件商品」，不逐一列出商品（UX 0.20 P15）。

只发 SELECT，读完结束读事务；不写日志。时间带 UTC 时区输出。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Product,
    Promotion,
    PromotionDecoration,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
from app.models.promotion import POSTER_MODE_IMAGE
from app.services.catalog import Language, localized, published
from app.services.promotion_poster import poster_path


class TierDisplay(BaseModel):
    """满额减或满额折的一档：门槛与减的固定金额或打的百分比（恰有一个不为空）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    threshold_sen: int
    amount_sen: int | None
    percent: int | None


class CategoryDisplay(BaseModel):
    """商品级规则适用的一个分类：slug 与所选语言的名称（缺译回退英文）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    name: str


class RuleDisplay(BaseModel):
    """一条规则的类别与参数（第 3 条）；不属于该类别的参数为空。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: int
    position: int
    kind: str
    discount_mode: str | None
    percent: int | None
    amount_sen: int | None
    buy_quantity: int | None
    get_quantity: int | None
    get_percent: int | None
    threshold_sen: int | None
    # 满额减或满额折的各档，按档的排序；其余类别为空列表。
    tiers: list[TierDisplay]
    # 商品级规则：启用且有英文名称的分类，按分类 ID 升序；整单级规则为空列表。
    categories: list[CategoryDisplay]
    # 规则显式选的商品中有满足 published()（上架）的。
    has_products: bool


class PromotionDisplay(BaseModel):
    """一个活动的展示数据（第 10、12 条）。不含内部名称与版本。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    promotion_id: int
    title: str | None
    subtitle: str | None
    badge: str | None
    strip: str | None
    pack: str
    palette: str
    ambient: str
    home_poster: str
    list_strip: str
    card_badge: str
    detail_ribbon: str
    cart_progress: str
    poster_mode: str
    # 上传图模式时两张图的提供路径与替代文字；模板模式时都为空。
    poster_desktop: str | None
    poster_phone: str | None
    poster_alt: str | None
    # 实际结束时间（提前结束时为提前结束的时刻），带 UTC 时区；草稿没有时间时为空。
    ends_at: datetime | None
    coupon_combinable: bool
    points_combinable: bool
    # 按 position 升序。
    rules: list[RuleDisplay]


def _text(decoration: PromotionDecoration, field: str, lang: Language) -> str | None:
    """所选语言的文案，缺译回退英文；英文也空即 None。空串或只有空白算缺。"""
    for value in (getattr(decoration, f"{field}_{lang}"), getattr(decoration, f"{field}_en")):
        if value is not None and value.strip() != "":
            return value
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def promotion_display(db: Session, promotion_id: int, lang: Language) -> PromotionDisplay | None:
    """按活动 ID 组装展示数据，不判断是否生效；活动或它的装饰行不存在返回 None。

    只读，读完结束读事务（调用方会话里未提交的改动随之回滚）。
    """
    try:
        return _read(db, promotion_id, lang)
    finally:
        db.rollback()


def _read(db: Session, promotion_id: int, lang: Language) -> PromotionDisplay | None:
    promotion = db.execute(
        select(Promotion)
        .where(Promotion.id == promotion_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if promotion is None:
        return None
    decoration = db.execute(
        select(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == promotion_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if decoration is None:
        return None

    image_mode = decoration.poster_mode == POSTER_MODE_IMAGE
    desktop = decoration.poster_desktop_file_name
    phone = decoration.poster_phone_file_name
    return PromotionDisplay(
        promotion_id=promotion.id,
        title=_text(decoration, "title", lang),
        subtitle=_text(decoration, "subtitle", lang),
        badge=_text(decoration, "badge", lang),
        strip=_text(decoration, "strip", lang),
        pack=decoration.pack,
        palette=decoration.palette,
        ambient=decoration.ambient,
        home_poster=decoration.home_poster,
        list_strip=decoration.list_strip,
        card_badge=decoration.card_badge,
        detail_ribbon=decoration.detail_ribbon,
        cart_progress=decoration.cart_progress,
        poster_mode=decoration.poster_mode,
        # 模板模式下已上传的图不在前台显示。
        poster_desktop=poster_path(desktop) if image_mode and desktop is not None else None,
        poster_phone=poster_path(phone) if image_mode and phone is not None else None,
        poster_alt=_text(decoration, "poster_alt", lang) if image_mode else None,
        ends_at=_as_utc(promotion.ended_early_at or promotion.ends_at),
        coupon_combinable=promotion.coupon_combinable,
        points_combinable=promotion.points_combinable,
        rules=_rules(db, promotion_id, lang),
    )


def _rules(db: Session, promotion_id: int, lang: Language) -> list[RuleDisplay]:
    rules_query = (
        select(PromotionRule)
        .where(PromotionRule.promotion_id == promotion_id)
        .order_by(PromotionRule.position)
        .execution_options(populate_existing=True)
    )
    rules = db.execute(rules_query).scalars().all()
    rule_ids = [rule.id for rule in rules]
    tiers: dict[int, list[TierDisplay]] = {rule_id: [] for rule_id in rule_ids}
    categories: dict[int, list[CategoryDisplay]] = {rule_id: [] for rule_id in rule_ids}
    with_products: set[int] = set()
    if rule_ids:
        tiers_query = (
            select(
                PromotionSpendTier.rule_id,
                PromotionSpendTier.threshold_sen,
                PromotionSpendTier.amount_sen,
                PromotionSpendTier.percent,
            )
            .where(PromotionSpendTier.rule_id.in_(rule_ids))
            .order_by(PromotionSpendTier.rule_id, PromotionSpendTier.position)
        )
        for rule_id, threshold_sen, amount_sen, percent in db.execute(tiers_query):
            tier = TierDisplay(threshold_sen=threshold_sen, amount_sen=amount_sen, percent=percent)
            tiers[rule_id].append(tier)

        # 只列启用且有英文名称的分类（同前台分类列表），按分类 ID 升序。
        categories_query = (
            select(PromotionRuleCategory.rule_id, Category)
            .join(Category, Category.id == PromotionRuleCategory.category_id)
            .where(
                PromotionRuleCategory.rule_id.in_(rule_ids),
                Category.is_active.is_(True),
                func.coalesce(func.trim(Category.name_en), "") != "",
            )
            .order_by(Category.id)
        )
        for rule_id, category in db.execute(categories_query).tuples():
            name = localized(category, "name", lang).text
            categories[rule_id].append(CategoryDisplay(slug=category.slug, name=name))

        # published() 要求已 join Category；外层不选 ProductVariant。
        products_query = (
            select(PromotionRuleProduct.rule_id)
            .join(Product, Product.id == PromotionRuleProduct.product_id)
            .join(Category, Category.id == Product.category_id)
            .where(PromotionRuleProduct.rule_id.in_(rule_ids), published())
            .distinct()
        )
        with_products = set(db.scalars(products_query))

    return [
        RuleDisplay(
            rule_id=rule.id,
            position=rule.position,
            kind=rule.kind,
            discount_mode=rule.discount_mode,
            percent=rule.percent,
            amount_sen=rule.amount_sen,
            buy_quantity=rule.buy_quantity,
            get_quantity=rule.get_quantity,
            get_percent=rule.get_percent,
            threshold_sen=rule.threshold_sen,
            tiers=tiers[rule.id],
            categories=categories[rule.id],
            has_products=rule.id in with_products,
        )
        for rule in rules
    ]

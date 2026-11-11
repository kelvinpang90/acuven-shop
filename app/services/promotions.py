"""节日促销活动的读取与规则换算（服务层）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1、3、4 条
（下称「第 N 条」）。从数据库读出当前生效的活动或按 ID 读出任一活动，换算成
app.services.promotion_pricing 的规则对象，判定每个商品命中哪条商品级规则，
并对一组正常行算出 PromotionPricing。

生效活动每次都从数据库读、不缓存（第 1 条），不需要定时任务。同一时刻至多
一个活动由之后的发布服务保证；读到多于一个（数据异常）时取开始时间最早、
同时取 ID 最小的一个，并记一条 warning 日志（键 promotion_overlap，只带活动 ID）。
读取只读，除 share_lock 外不加锁；share_lock 以共享锁读活动行，供下单事务与
后台撤回、保存时的排他锁互相等待。规则、范围与满额档各用一次查询取出。
哪些行是正常行、每行商品当时所属的分类由调用方决定后传入。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
from app.models.promotion import (
    KIND_BUY_X_GET_Y,
    KIND_FREE_SHIPPING,
    KIND_ITEM_DISCOUNT,
    KIND_SPEND_DISCOUNT,
    MODE_AMOUNT,
    STATUS_PUBLISHED,
)
from app.services.promotion_pricing import (
    BuyXGetYRule,
    FreeShippingRule,
    ItemDiscountRule,
    PromoLine,
    PromotionPricing,
    SpendRule,
    price_promotion,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedItemRule:
    """一条商品级规则：排序、换算后的规则对象与适用的商品、分类 ID。"""

    rule_id: int
    position: int
    rule: ItemDiscountRule | BuyXGetYRule
    product_ids: frozenset[int]
    category_ids: frozenset[int]


@dataclass(frozen=True)
class LoadedPromotion:
    """读出的一个活动。

    item_rules 按 position 升序；effective_end 为提前结束时间（有时）或结束时间；
    草稿没有时间时 starts_at 与 effective_end 为 None。
    """

    promotion_id: int
    version: int
    status: str
    starts_at: datetime | None
    effective_end: datetime | None
    coupon_combinable: bool
    points_combinable: bool
    item_rules: tuple[LoadedItemRule, ...]
    spend_rule: SpendRule | None
    free_shipping_rule: FreeShippingRule | None


@dataclass(frozen=True)
class PromoInputLine:
    """调用方给出的一条正常行：行序、每件原价（仙）、件数、商品与它当时所属的分类。"""

    line_index: int
    unit_price: int
    quantity: int
    product_id: int
    category_id: int


def active_promotion(
    db: Session, now: datetime, *, share_lock: bool = False
) -> LoadedPromotion | None:
    """第 1 条：已发布、开始时间 ≤ now 且 now < 实际结束时间的活动，没有返回 None。

    now 为不带时区的 UTC，带时区抛 ValueError。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    effective_end = func.coalesce(Promotion.ended_early_at, Promotion.ends_at)
    query = (
        select(Promotion)
        .where(
            Promotion.status == STATUS_PUBLISHED,
            Promotion.starts_at <= now,
            effective_end > now,
        )
        .order_by(Promotion.starts_at, Promotion.id)
        .execution_options(populate_existing=True)
    )
    if share_lock:
        query = query.with_for_update(read=True)
    promotions = db.execute(query).scalars().all()
    if not promotions:
        return None
    if len(promotions) > 1:
        ids = ",".join(str(promotion.id) for promotion in promotions)
        logger.warning("promotion_overlap promotion_ids=%s", ids)
    return _load(db, promotions[0])


def load_promotion(db: Session, promotion_id: int) -> LoadedPromotion | None:
    """按 ID 读出任一状态的活动（供管理员预览），不存在返回 None。"""
    query = (
        select(Promotion)
        .where(Promotion.id == promotion_id)
        .execution_options(populate_existing=True)
    )
    promotion = db.execute(query).scalar_one_or_none()
    if promotion is None:
        return None
    return _load(db, promotion)


def _load(db: Session, promotion: Promotion) -> LoadedPromotion:
    rules_query = (
        select(PromotionRule)
        .where(PromotionRule.promotion_id == promotion.id)
        .order_by(PromotionRule.position)
        .execution_options(populate_existing=True)
    )
    rules = db.execute(rules_query).scalars().all()
    rule_ids = [rule.id for rule in rules]
    product_ids: dict[int, set[int]] = {rule_id: set() for rule_id in rule_ids}
    category_ids: dict[int, set[int]] = {rule_id: set() for rule_id in rule_ids}
    tiers: dict[int, list[PromotionSpendTier]] = {rule_id: [] for rule_id in rule_ids}
    if rule_ids:
        products_query = select(PromotionRuleProduct.rule_id, PromotionRuleProduct.product_id)
        products_query = products_query.where(PromotionRuleProduct.rule_id.in_(rule_ids))
        for rule_id, product_id in db.execute(products_query):
            product_ids[rule_id].add(product_id)
        categories_query = select(PromotionRuleCategory.rule_id, PromotionRuleCategory.category_id)
        categories_query = categories_query.where(PromotionRuleCategory.rule_id.in_(rule_ids))
        for rule_id, category_id in db.execute(categories_query):
            category_ids[rule_id].add(category_id)
        tiers_query = (
            select(PromotionSpendTier)
            .where(PromotionSpendTier.rule_id.in_(rule_ids))
            .order_by(PromotionSpendTier.rule_id, PromotionSpendTier.position)
            .execution_options(populate_existing=True)
        )
        for tier in db.execute(tiers_query).scalars():
            tiers[tier.rule_id].append(tier)

    item_rules: list[LoadedItemRule] = []
    spend_rule: SpendRule | None = None
    free_shipping_rule: FreeShippingRule | None = None
    # 至多一条满额减或满额折、至多一条满额免运费由写入方保证（第 3 条）；
    # 数据异常时取排序最小的一条。
    for rule in rules:
        if rule.kind in (KIND_ITEM_DISCOUNT, KIND_BUY_X_GET_Y):
            if rule.kind == KIND_ITEM_DISCOUNT:
                converted = ItemDiscountRule(rule.id, rule.percent, rule.amount_sen)
            else:
                converted = BuyXGetYRule(
                    rule.id, rule.buy_quantity, rule.get_quantity, rule.get_percent
                )
            item_rules.append(
                LoadedItemRule(
                    rule_id=rule.id,
                    position=rule.position,
                    rule=converted,
                    product_ids=frozenset(product_ids[rule.id]),
                    category_ids=frozenset(category_ids[rule.id]),
                )
            )
        elif rule.kind == KIND_SPEND_DISCOUNT and spend_rule is None:
            amount = rule.discount_mode == MODE_AMOUNT
            spend_rule = SpendRule(
                mode=rule.discount_mode,
                tiers=tuple(
                    (tier.threshold_sen, tier.amount_sen if amount else tier.percent)
                    for tier in tiers[rule.id]
                ),
            )
        elif rule.kind == KIND_FREE_SHIPPING and free_shipping_rule is None:
            free_shipping_rule = FreeShippingRule(rule.threshold_sen)

    return LoadedPromotion(
        promotion_id=promotion.id,
        version=promotion.version,
        status=promotion.status,
        starts_at=promotion.starts_at,
        effective_end=promotion.ended_early_at or promotion.ends_at,
        coupon_combinable=promotion.coupon_combinable,
        points_combinable=promotion.points_combinable,
        item_rules=tuple(item_rules),
        spend_rule=spend_rule,
        free_shipping_rule=free_shipping_rule,
    )


def rule_for_product(promotion: LoadedPromotion, product_id: int, category_id: int) -> int | None:
    """第 3 条：范围包含该商品的商品级规则中排序数最小者的 ID，没有返回 None。

    category_id 为商品当时所属的分类；商品改分类后落在多条规则的范围里时，
    取排序数较小的规则。
    """
    for item_rule in promotion.item_rules:
        if product_id in item_rule.product_ids or category_id in item_rule.category_ids:
            return item_rule.rule_id
    return None


def price_lines(
    promotion: LoadedPromotion,
    lines: Sequence[PromoInputLine],
    *,
    shipping_fee_sen: int | None,
) -> PromotionPricing:
    """第 4 条（一）至（三）：按 rule_for_product 定每行的规则后调用 price_promotion。

    lines 只含正常行，line_index 严格递增（由 price_promotion 校验）；
    shipping_fee_sen 为 None 时照 price_promotion 不判定是否参加活动。
    """
    promo_lines: list[PromoLine] = []
    for i, line in enumerate(lines):
        if not isinstance(line, PromoInputLine):
            raise TypeError(f"lines[{i}] must be PromoInputLine, got {type(line).__name__}")
        promo_lines.append(
            PromoLine(
                line_index=line.line_index,
                unit_price=line.unit_price,
                quantity=line.quantity,
                rule_id=rule_for_product(promotion, line.product_id, line.category_id),
            )
        )
    return price_promotion(
        promo_lines,
        item_rules={item_rule.rule_id: item_rule.rule for item_rule in promotion.item_rules},
        spend_rule=promotion.spend_rule,
        free_shipping_rule=promotion.free_shipping_rule,
        shipping_fee_sen=shipping_fee_sen,
    )

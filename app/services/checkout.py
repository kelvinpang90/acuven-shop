"""购物车与结账计价（游客计价）：只读，按当前价格重算，不建订单、不预留库存。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Cart 一行：「浏览器中的商品规格与数量；
不信任浏览器价格，结账时服务端重算」；ShippingRate / DemoFxRate 一行；「计价、优惠、积分与库存」
第 1、2 条：服务端以规格价格计算商品小计，运费另计；「边界与原则」：金额以 MYR 的仙为整数单位，
其他货币只按固定演示汇率显示参考数，按收货国家决定、不按 IP。

每单限购依据 docs/DESIGN.md 1.10（提交 e3b3505）「计价、优惠、积分与库存」第 8 条
（SHOP-TASK-016）：同一商品各行件数合计超过该商品的每单限购件数时，
这些行标为超出限购，不拒绝整个请求；标注优先级为不可购买、超出限购、库存不足。
下单时的整单校验由游客下单（app/services/ordering.py，SHOP-TASK-020）用本模块重新计价后执行。

整单上限依据 docs/DESIGN.md 1.18（提交 5ba9796）同一条与 docs/HANDOFF.md 0.74（SHOP-TASK-178）：
每行件数 1–1000；所有行（含不可购买的行）的件数合计不超过 2,000；商品小计加运费（没有运费报价时
只看商品小计，优惠券与积分抵扣之前）不超过 99,999,999 仙。超出时不拒绝整个请求，而返回标记与上限、
不可下单，各行的标注不变；超出金额上限时不返回参考外币。游客与会员下单按 can_place_order 拒绝整单。

节日促销活动依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 4、10 条与「计价、
优惠、积分与库存」第 1、8 条（SHOP-TASK-234）：调用方读出活动后以 promotion 传入（计价接口传
生效活动，管理员预览传要预览的活动），不传即没有活动、结果与之前逐字段相同。活动只算正常行；
有运费报价且本单参加活动时，合计按活动后的商品额与实际运费给出。整单金额上限仍按原价小计与
未免的运费判断。游客不能用券与积分，所以没有第 6 条的方案比较。

只发 SELECT，不写库、不缓存价格与费率。
SKU 不存在、停用或所属商品未发布的行只给 SKU、件数与状态。
优惠券与积分不在这里，由之后的任务扩展。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.services.catalog import (
    Language,
    LocalizedText,
    OptionValueDetail,
    localized,
    published,
)
from app.services.promotion_pricing import BuyXGetYRule, PromotionPricing
from app.services.promotions import LoadedPromotion, PromoInputLine, price_lines
from app.services.shipping import FxReference, ShippingQuote, quote_shipping, reference_amount

MAX_CART_LINES = 20
# DESIGN 1.18 第 8 条：每行件数 1–1000；所有行的件数合计不超过 2,000；
# 商品小计加运费（抵扣之前）不超过 99,999,999 仙（RM999,999.99）。
MAX_LINE_QUANTITY = 1000
MAX_ORDER_UNITS = 2000
MAX_ORDER_AMOUNT_SEN = 99_999_999
MAX_SKU_LENGTH = 64


@dataclass(frozen=True)
class CartItem:
    """浏览器购物车的一行：只有 SKU 与件数，没有价格。"""

    sku: str
    quantity: int


@dataclass(frozen=True)
class SelectedOption:
    """SKU 在一个规格名下所选的规格值。"""

    code: str
    name: LocalizedText
    value: OptionValueDetail


@dataclass(frozen=True)
class UnavailableLine:
    """不可购买：不透露该 SKU 或其商品的任何信息。"""

    sku: str
    quantity: int
    status: Literal["unavailable"]


@dataclass(frozen=True)
class PricedLine:
    sku: str
    quantity: int
    status: Literal["ok", "over_limit", "insufficient_stock"]
    # 当日可用库存小于件数时给出（可为 0），否则为 None；
    # 限购与库存各自独立，超出限购的行库存也不足时照给。
    available_stock: int | None
    # 该商品的每单限购件数，按同一商品各行件数合计比较。
    max_per_order: int
    product_slug: str
    name: LocalizedText
    # 按规格名的排列序号。
    options: list[SelectedOption]
    # 排列序号最小的图片的存储引用；没有图片时为 None。
    image: str | None
    unit_price_sen: int
    line_subtotal_sen: int
    # 有活动时正常行命中的商品级规则 ID 与该行各件商品级折扣之和；其他行与没有活动时取默认值。
    promotion_rule_id: int | None = None
    promotion_item_discount_sen: int = 0


@dataclass(frozen=True)
class BuyXGetYQuote:
    """一条买 X 享 Y 规则：参数、范围内的正常件数 n 与再加几件可多享 Y 件（第 10 条）。"""

    rule_id: int
    buy_quantity: int
    get_quantity: int
    get_percent: int
    units_in_scope: int
    units_to_next: int


@dataclass(frozen=True)
class PromotionQuote:
    """活动对正常行的计价结果，各数取自 app.services.promotions 的 price_lines。

    两个门槛都按 S1（after_item_subtotal_sen）判断。spend_next_threshold_sen 与
    spend_next_value 为下一档的门槛与优惠（固定金额为仙、百分比为整数），已到最高档或
    没有满额规则时为 None。没有运费报价时 shipping_waived_sen 与 participates 为 None。
    """

    promotion_id: int
    item_discount_sen: int
    order_discount_sen: int
    after_item_subtotal_sen: int
    # 满额规则的方式（amount 或 percent），没有满额规则为 None。
    spend_mode: str | None
    spend_tier_reached: int | None
    spend_next_threshold_sen: int | None
    spend_next_value: int | None
    spend_next_gap_sen: int | None
    # 免运费门槛，没有免运费规则为 None。
    free_shipping_threshold_sen: int | None
    free_shipping_reached: bool
    free_shipping_gap_sen: int | None
    shipping_waived_sen: int | None
    participates: bool | None
    # 按 rule_id 升序。
    buy_x_get_y: list[BuyXGetYQuote]


@dataclass(frozen=True)
class CheckoutQuote:
    lines: list[UnavailableLine | PricedLine]
    # 正常、超出限购与库存不足各行行小计之和。
    subtotal_sen: int
    # 以下三项只在给了收货国家时有值。shipping 为未免的运费报价；本单参加活动时
    # total_sen 为小计减活动优惠、加运费报价、减免去的运费。
    shipping: ShippingQuote | None
    total_sen: int | None
    # 合计的参考外币；该国无演示汇率（含马来西亚）或超出金额上限时也为 None。
    fx_reference: FxReference | None
    # 所有行均为正常、给了收货国家且未超出两个整单上限；下单时重新计算与校验。
    can_place_order: bool
    # 恒为 MAX_ORDER_UNITS；所有行（含不可购买的行）的件数合计大于它时为真。
    unit_limit: int
    units_over_limit: bool
    # 恒为 MAX_ORDER_AMOUNT_SEN；商品小计加运费（没给收货国家时只看小计）大于它时为真。
    amount_limit_sen: int
    amount_over_limit: bool
    # 传了活动时总是给出（没有正常行时各金额为 0、差额为门槛本身），否则为 None。
    promotion: PromotionQuote | None = None


def purchasable(
    session: Session,
    skus: Sequence[str],
) -> dict[str, tuple[ProductVariant, Product]]:
    """启用且所属商品已发布的 SKU，按 SKU 原文索引。游客下单（app/services/ordering.py）也用它。

    SKU 在 Python 里逐字比对：MySQL 默认排序规则不区分大小写、比较时忽略尾随空格，
    只靠 WHERE 会把 tee-red 当成 TEE-RED。
    """
    # 外层用别名：发布规则里「至少有一个启用的 SKU」的子查询只与商品关联。
    variant = aliased(ProductVariant)
    stmt = (
        select(variant, Product)
        .join(Product, Product.id == variant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(variant.sku.in_(skus), variant.is_active.is_(True), published())
    )
    return {row.sku: (row, product) for row, product in session.execute(stmt)}


def _first_images(session: Session, product_ids: set[int]) -> dict[int, str]:
    stmt = (
        select(ProductImage)
        .where(ProductImage.product_id.in_(product_ids))
        .order_by(ProductImage.product_id, ProductImage.sort_order, ProductImage.id)
    )
    images: dict[int, str] = {}
    for image in session.scalars(stmt):
        images.setdefault(image.product_id, image.storage_ref)
    return images


def selected_options(
    session: Session,
    product_ids: set[int],
    variant_ids: set[int],
    lang: Language,
) -> dict[int, list[SelectedOption]]:
    """每个 SKU 所选的规格，按规格名的排列序号。游客下单也用它取三语规格说明。"""
    stmt = (
        select(ProductOption)
        .where(ProductOption.product_id.in_(product_ids))
        .order_by(ProductOption.sort_order, ProductOption.id)
    )
    options = list(session.scalars(stmt))
    stmt = (
        select(VariantOptionValue.variant_id, ProductOptionValue)
        .join(ProductOptionValue, ProductOptionValue.id == VariantOptionValue.option_value_id)
        .where(VariantOptionValue.variant_id.in_(variant_ids))
    )
    chosen: dict[int, dict[int, ProductOptionValue]] = {}
    for variant_id, value in session.execute(stmt):
        chosen.setdefault(variant_id, {})[value.option_id] = value

    selected: dict[int, list[SelectedOption]] = {}
    for variant_id in variant_ids:
        values = chosen.get(variant_id, {})
        selected[variant_id] = [
            SelectedOption(
                code=option.code,
                name=localized(option, "name", lang),
                value=OptionValueDetail(
                    code=values[option.id].code,
                    name=localized(values[option.id], "name", lang),
                ),
            )
            for option in options
            if option.id in values
        ]
    return selected


def quote_cart(
    session: Session,
    items: Sequence[CartItem],
    *,
    lang: Language,
    country_code: str | None = None,
    state_code: str | None = None,
    promotion: LoadedPromotion | None = None,
) -> CheckoutQuote:
    """按请求顺序逐行计价。

    行数、件数与 SKU 不重复由调用方校验。给了收货国家时先查运费（app/services/shipping.py）：
    目的地不合法抛 InvalidDestination；应有的运费行缺失抛 ShippingRateMissing，
    不以零运费代替。promotion 由调用方读出后传入，None 即没有活动。
    """
    shipping = None
    if country_code is not None:
        shipping = quote_shipping(session, country_code, state_code)

    found = purchasable(session, [item.sku for item in items])
    product_ids = {product.id for _, product in found.values()}
    variant_ids = {variant.id for variant, _ in found.values()}
    images = _first_images(session, product_ids) if found else {}
    options = selected_options(session, product_ids, variant_ids, lang) if found else {}

    # 每单限购按商品合计；不可购买的行不在 found 里，不计入任何商品的合计。
    product_quantities: dict[int, int] = {}
    for item in items:
        if item.sku in found:
            product_id = found[item.sku][1].id
            product_quantities[product_id] = product_quantities.get(product_id, 0) + item.quantity

    lines: list[UnavailableLine | PricedLine] = []
    promo_lines: list[PromoInputLine] = []
    for index, item in enumerate(items):
        if item.sku not in found:
            lines.append(
                UnavailableLine(sku=item.sku, quantity=item.quantity, status="unavailable")
            )
            continue
        variant, product = found[item.sku]
        short = variant.available_stock < item.quantity
        over = product_quantities[product.id] > product.max_per_order
        status: Literal["ok", "over_limit", "insufficient_stock"]
        if over:
            status = "over_limit"
        elif short:
            status = "insufficient_stock"
        else:
            status = "ok"
            promo_lines.append(
                PromoInputLine(
                    line_index=index,
                    unit_price=variant.price_sen,
                    quantity=item.quantity,
                    product_id=product.id,
                    category_id=product.category_id,
                )
            )
        lines.append(
            PricedLine(
                sku=item.sku,
                quantity=item.quantity,
                status=status,
                available_stock=variant.available_stock if short else None,
                max_per_order=product.max_per_order,
                product_slug=product.slug,
                name=localized(product, "name", lang),
                options=options[variant.id],
                image=images.get(product.id),
                unit_price_sen=variant.price_sen,
                line_subtotal_sen=variant.price_sen * item.quantity,
            )
        )

    # 等于 price_order 的小计；不逐件展开，件数超出上限时也不展开。
    subtotal = sum(line.line_subtotal_sen for line in lines if isinstance(line, PricedLine))
    total = None if shipping is None else subtotal + shipping.fee_sen
    units_over = sum(item.quantity for item in items) > MAX_ORDER_UNITS
    # 抵扣之前的金额（也在活动优惠与免运费之前）；还没有运费报价时只看商品小计。
    amount_over = (subtotal if total is None else total) > MAX_ORDER_AMOUNT_SEN

    promotion_quote = None
    if promotion is not None:
        pricing = price_lines(
            promotion,
            promo_lines,
            shipping_fee_sen=None if shipping is None else shipping.fee_sen,
        )
        line_rules: dict[int, int | None] = {}
        line_discounts: dict[int, int] = {}
        for unit in pricing.units:
            line_rules[unit.line_index] = unit.rule_id
            line_discounts[unit.line_index] = (
                line_discounts.get(unit.line_index, 0) + unit.item_discount
            )
        lines = [
            replace(
                line,
                promotion_rule_id=line_rules[index],
                promotion_item_discount_sen=line_discounts[index],
            )
            if isinstance(line, PricedLine) and index in line_rules
            else line
            for index, line in enumerate(lines)
        ]
        promotion_quote = _promotion_quote(promotion, pricing)
        # 有运费报价才判定参加与否（participates 不为 None）；不参加时合计照旧。
        if shipping is not None and pricing.participates:
            total = (
                subtotal
                - pricing.item_discount_total
                - pricing.order_discount_total
                + shipping.fee_sen
                - (pricing.shipping_waived_sen or 0)
            )

    fx = None
    if total is not None and not amount_over:
        fx = reference_amount(session, country_code, total)
    all_ok = all(line.status == "ok" for line in lines)
    return CheckoutQuote(
        lines=lines,
        subtotal_sen=subtotal,
        shipping=shipping,
        total_sen=total,
        fx_reference=fx,
        can_place_order=all_ok and shipping is not None and not units_over and not amount_over,
        unit_limit=MAX_ORDER_UNITS,
        units_over_limit=units_over,
        amount_limit_sen=MAX_ORDER_AMOUNT_SEN,
        amount_over_limit=amount_over,
        promotion=promotion_quote,
    )


def _promotion_quote(promotion: LoadedPromotion, pricing: PromotionPricing) -> PromotionQuote:
    spend = promotion.spend_rule
    next_tier = None
    if spend is not None and (pricing.spend_tier_reached or 0) < len(spend.tiers):
        next_tier = spend.tiers[pricing.spend_tier_reached or 0]
    free_shipping = promotion.free_shipping_rule
    rules = {
        item_rule.rule_id: item_rule.rule
        for item_rule in promotion.item_rules
        if isinstance(item_rule.rule, BuyXGetYRule)
    }
    buy_x_get_y = []
    # price_promotion 已按 rule_id 升序给出每条买 X 享 Y 规则。
    for progress in pricing.buy_x_get_y:
        rule = rules[progress.rule_id]
        buy_x_get_y.append(
            BuyXGetYQuote(
                rule_id=progress.rule_id,
                buy_quantity=rule.buy_quantity,
                get_quantity=rule.get_quantity,
                get_percent=rule.get_percent,
                units_in_scope=progress.units_in_scope,
                units_to_next=progress.units_to_next,
            )
        )
    return PromotionQuote(
        promotion_id=promotion.promotion_id,
        item_discount_sen=pricing.item_discount_total,
        order_discount_sen=pricing.order_discount_total,
        after_item_subtotal_sen=pricing.after_item_subtotal,
        spend_mode=None if spend is None else spend.mode,
        spend_tier_reached=pricing.spend_tier_reached,
        spend_next_threshold_sen=None if next_tier is None else next_tier[0],
        spend_next_value=None if next_tier is None else next_tier[1],
        spend_next_gap_sen=pricing.spend_next_gap_sen,
        free_shipping_threshold_sen=None if free_shipping is None else free_shipping.threshold_sen,
        free_shipping_reached=pricing.free_shipping_reached,
        free_shipping_gap_sen=pricing.free_shipping_gap_sen,
        shipping_waived_sen=pricing.shipping_waived_sen,
        participates=pricing.participates,
        buy_x_get_y=buy_x_get_y,
    )

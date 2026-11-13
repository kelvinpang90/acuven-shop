"""商品目录只读查询：发布规则、文案回退英文、搜索、筛选、排序与分页。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行：
「文案缺少当前语言时回退英文，再缺失则商品不发布」；「边界与原则」：金额以 MYR 的仙为整数单位。
只发 SELECT，不写库。返回值里没有参考外币、每日初始库存、启用状态等后台字段。
文案为 NULL、空串或只有空格都算缺少。
多规格标记、分类图片与规格筛选项依据 docs/REQUIREMENTS.md 1.10「访客与会员流程」第 1 条
与 docs/UX.md 0.5 的 P01、P02（SHOP-TASK-013）。
商品详情的每单限购件数依据 docs/DESIGN.md 1.10（提交 e3b3505）
「数据模型」的 Product / Variant 一行与「计价、优惠、积分与库存」第 8 条（SHOP-TASK-016）；
商品列表与分类不带它。
活动价与角标依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 3、10 条与
docs/UX.md 0.20 的商品卡规则、P03、P15（SHOP-TASK-244）：生效活动由调用方读出后以
promotion 传入；命中的商品级规则取 rule_for_product，一件的活动单价取 price_promotion
对数量 1 的一行的结果，与计价同一算法。sale_only 的专题页列表只列商品级规则范围内的商品。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
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
from app.models.promotion import KIND_BUY_X_GET_Y, KIND_ITEM_DISCOUNT, MODE_AMOUNT, MODE_PERCENT
from app.services.promotion_pricing import (
    BuyXGetYRule,
    ItemDiscountRule,
    PromoLine,
    price_promotion,
)
from app.services.promotions import LoadedPromotion, rule_for_product

Language = Literal["en", "zh", "ms"]
SortOrder = Literal["newest", "price_asc", "price_desc"]

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 48


@dataclass(frozen=True)
class LocalizedText:
    """请求语言的文案；缺少时给英文并把 english_fallback 置真，供页面显示「仅英文」标签。"""

    text: str
    english_fallback: bool


@dataclass(frozen=True)
class CategorySummary:
    slug: str
    name: LocalizedText


@dataclass(frozen=True)
class CategoryListItem:
    """分类列表接口的一项；嵌在商品摘要与详情里的分类仍是 CategorySummary，不带图片。"""

    slug: str
    name: LocalizedText
    # 该分类下按 newest 排序第一的已发布商品的首张图片引用；没有时为 None。
    image: str | None


@dataclass(frozen=True)
class CardPromotion:
    """商品命中的商品级规则：角标所需的类别与参数，不属于该类别的参数为 None。

    kind 为 item_discount 或 buy_x_get_y；discount_mode 只有打折有（percent 或 amount）。
    """

    rule_id: int
    kind: str
    discount_mode: str | None
    percent: int | None
    amount_sen: int | None
    buy_quantity: int | None
    get_quantity: int | None
    get_percent: int | None


@dataclass(frozen=True)
class ProductSummary:
    slug: str
    name: LocalizedText
    category: CategorySummary
    # 排列序号最小的图片的存储引用；没有图片时为 None。
    image: str | None
    # 启用 SKU 中的最低单价（MYR 整数仙），供「RM x 起」与价格排序。
    min_price_sen: int
    # 启用 SKU 多于一个：页面一律显示「起」，即使各 SKU 同价。
    has_multiple_variants: bool
    # 所有启用 SKU 的当日可用库存都是 0。
    sold_out_today: bool
    # 生效活动中命中的商品级规则；没有生效活动或没有命中时为 None。
    promotion: CardPromotion | None
    # 命中打折规则时各启用 SKU 活动单价的最小值，「起」照 has_multiple_variants；否则 None。
    min_sale_price_sen: int | None


@dataclass(frozen=True)
class ProductPage:
    total: int
    page: int
    page_size: int
    items: list[ProductSummary]


@dataclass(frozen=True)
class OptionValueDetail:
    code: str
    name: LocalizedText


@dataclass(frozen=True)
class OptionDetail:
    code: str
    name: LocalizedText
    values: list[OptionValueDetail]


@dataclass(frozen=True)
class VariantDetail:
    sku: str
    # 规格名 code → 所选规格值 code，按规格名的排列序。
    options: dict[str, str]
    price_sen: int
    available_stock: int
    # 命中打折规则时该 SKU 的活动单价；否则 None（买 X 享 Y 不改显示单价）。
    sale_price_sen: int | None


@dataclass(frozen=True)
class ProductDetail:
    slug: str
    name: LocalizedText
    description: LocalizedText
    category: CategorySummary
    images: list[str]
    options: list[OptionDetail]
    variants: list[VariantDetail]
    # 每单限购件数：该商品所有 SKU 在一张购物车或订单中的件数合计上限。
    max_per_order: int
    # 生效活动中命中的商品级规则；没有生效活动或没有命中时为 None。
    promotion: CardPromotion | None


def _present(column: ColumnElement[str | None]) -> ColumnElement[bool]:
    # 包一层 coalesce，结果永不为 NULL：否则 NOT 一个 NULL 仍是 NULL，缺英文的规格名会漏检。
    return func.coalesce(func.trim(column), "") != ""


def _has_text(value: str | None) -> bool:
    # 与 SQL 的 TRIM 一致，只去空格。
    return value is not None and value.strip(" ") != ""


def localized(row: object, field: str, lang: Language) -> LocalizedText:
    """文案回退：请求语言缺少时给英文并标出。结账计价也用它。"""
    value = getattr(row, f"{field}_{lang}")
    if _has_text(value):
        return LocalizedText(text=value, english_fallback=False)
    return LocalizedText(text=getattr(row, f"{field}_en"), english_fallback=True)


def _category_summary(category: Category, lang: Language) -> CategorySummary:
    return CategorySummary(slug=category.slug, name=localized(category, "name", lang))


_in_category = Category.id == Product.category_id
_active_variant = and_(ProductVariant.product_id == Product.id, ProductVariant.is_active.is_(True))

_min_price = (
    select(func.min(ProductVariant.price_sen))
    .where(_active_variant)
    .correlate(Product)
    .scalar_subquery()
)
_max_stock = (
    select(func.max(ProductVariant.available_stock))
    .where(_active_variant)
    .correlate(Product)
    .scalar_subquery()
)
_active_count = (
    select(func.count(ProductVariant.id))
    .where(_active_variant)
    .correlate(Product)
    .scalar_subquery()
)
_first_image = (
    select(ProductImage.storage_ref)
    .where(ProductImage.product_id == Product.id)
    .order_by(ProductImage.sort_order)
    .limit(1)
    .correlate(Product)
    .scalar_subquery()
)
# 商品列表 newest 的排序；分类图片取同一排序下的第一件。
_newest = (Product.created_at.desc(), Product.id)


def published() -> ColumnElement[bool]:
    """发布规则：商品与所属分类都启用；分类英文名称、商品英文名称与英文描述、
    该商品所有规格名与规格值的英文名称齐全；至少有一个启用的 SKU。调用方须已 join Category。
    结账计价（app/services/checkout.py）也用它；外层查询若也选 ProductVariant，须用别名，
    否则「至少有一个启用的 SKU」的子查询会与外层的 SKU 关联。
    """
    return and_(
        Product.is_active.is_(True),
        Category.is_active.is_(True),
        _present(Category.name_en),
        _present(Product.name_en),
        _present(Product.description_en),
        ~exists().where(
            ProductOption.product_id == Product.id,
            ~_present(ProductOption.name_en),
        ),
        ~exists().where(
            ProductOptionValue.product_id == Product.id,
            ~_present(ProductOptionValue.name_en),
        ),
        exists().where(_active_variant),
    )


def _has_matching_variant(filters: Mapping[str, Sequence[str]]) -> ColumnElement[bool]:
    """同一个启用的 SKU 同时满足全部筛选：同一规格名下的多个值为或，不同规格名之间为且。"""
    conditions = [_active_variant]
    for option_code, value_codes in filters.items():
        conditions.append(
            exists().where(
                VariantOptionValue.variant_id == ProductVariant.id,
                ProductOption.id == VariantOptionValue.option_id,
                ProductOptionValue.id == VariantOptionValue.option_value_id,
                ProductOption.code == option_code,
                ProductOptionValue.code.in_(value_codes),
            )
        )
    return exists().where(*conditions)


ItemRule = ItemDiscountRule | BuyXGetYRule


def _hit_rule(
    promotion: LoadedPromotion | None, product: Product
) -> tuple[CardPromotion, ItemRule] | None:
    """第 3 条：商品按当时所属的分类命中的商品级规则（重叠时取排序小者），换成角标参数。"""
    if promotion is None:
        return None
    rule_id = rule_for_product(promotion, product.id, product.category_id)
    if rule_id is None:
        return None
    rule = next(item.rule for item in promotion.item_rules if item.rule_id == rule_id)
    if isinstance(rule, ItemDiscountRule):
        card = CardPromotion(
            rule_id=rule_id,
            kind=KIND_ITEM_DISCOUNT,
            discount_mode=MODE_PERCENT if rule.percent is not None else MODE_AMOUNT,
            percent=rule.percent,
            amount_sen=rule.amount_sen,
            buy_quantity=None,
            get_quantity=None,
            get_percent=None,
        )
    else:
        # 第 10 条：买 X 享 Y 只给角标所需的 X、Y 与百分比，不改显示单价。
        card = CardPromotion(
            rule_id=rule_id,
            kind=KIND_BUY_X_GET_Y,
            discount_mode=None,
            percent=None,
            amount_sen=None,
            buy_quantity=rule.buy_quantity,
            get_quantity=rule.get_quantity,
            get_percent=rule.get_percent,
        )
    return card, rule


def _card(hit: tuple[CardPromotion, ItemRule] | None) -> CardPromotion | None:
    return None if hit is None else hit[0]


def _sale_price(rule: ItemRule, price_sen: int) -> int | None:
    """打折规则下一件的活动单价：price_promotion 对数量 1 的一行的活动价小计；其余为 None。"""
    if not isinstance(rule, ItemDiscountRule):
        return None
    line = PromoLine(line_index=0, unit_price=price_sen, quantity=1, rule_id=rule.rule_id)
    return price_promotion([line], item_rules={rule.rule_id: rule}).after_item_subtotal


def _sale_scope(promotion: LoadedPromotion | None) -> ColumnElement[bool] | None:
    """P15：全部商品级规则范围（显式选的商品加所选分类下的商品）；没有范围时为 None。"""
    if promotion is None:
        return None
    product_ids: set[int] = set()
    category_ids: set[int] = set()
    for item_rule in promotion.item_rules:
        product_ids |= item_rule.product_ids
        category_ids |= item_rule.category_ids
    scopes = []
    if product_ids:
        scopes.append(Product.id.in_(sorted(product_ids)))
    if category_ids:
        scopes.append(Product.category_id.in_(sorted(category_ids)))
    return or_(*scopes) if scopes else None


def list_categories(session: Session, lang: Language) -> list[CategoryListItem]:
    """启用且有英文名称的分类，按 id 排序。

    每项带该分类下按 newest 排序第一的已发布商品的首张图片（取法同商品摘要的 image）；
    该商品没有图片时为 None，不往后找下一件。
    """
    image = (
        select(_first_image)
        .where(Product.category_id == Category.id, published())
        .order_by(*_newest)
        .limit(1)
        .correlate(Category)
        .scalar_subquery()
    )
    stmt = (
        select(Category, image)
        .where(Category.is_active.is_(True), _present(Category.name_en))
        .order_by(Category.id)
    )
    return [
        CategoryListItem(
            slug=category.slug,
            name=localized(category, "name", lang),
            image=category_image,
        )
        for category, category_image in session.execute(stmt)
    ]


def list_filter_options(session: Session, lang: Language) -> list[OptionDetail]:
    """商品列表可筛选的规格名与规格值。

    只取已发布商品的启用 SKU 实际用到的规格值；规格名按 code 聚合，同一规格名下按规格值
    code 去重。同一 code 在不同商品上名称不同时取 id 最小的商品上的名称。规格名按它在
    各商品中最小的排列序号、再按 code 排序，规格值同理。返回的 code 可原样组成
    list_products 的 options 筛选。
    """
    # 外层用别名：published() 里「所有规格名与规格值的英文名称齐全」的子查询若与外层的
    # 规格名、规格值关联，就只检查当前这一行，不再是该商品的全部规格。
    option_row = aliased(ProductOption)
    value_row = aliased(ProductOptionValue)
    variant_row = aliased(ProductVariant)
    used_by_active_variant = exists().where(
        VariantOptionValue.option_value_id == value_row.id,
        variant_row.id == VariantOptionValue.variant_id,
        variant_row.is_active.is_(True),
    )
    stmt = (
        select(option_row, value_row)
        .join_from(Product, Category, _in_category)
        .join(option_row, option_row.product_id == Product.id)
        .join(value_row, value_row.option_id == option_row.id)
        .where(published(), used_by_active_variant)
        .order_by(Product.id, option_row.id, value_row.id)
    )

    # 按商品 id 升序遍历，先见到的名称即 id 最小的商品上的名称。
    option_names: dict[str, LocalizedText] = {}
    option_orders: dict[str, int] = {}
    value_names: dict[str, dict[str, LocalizedText]] = {}
    value_orders: dict[str, dict[str, int]] = {}
    for option, value in session.execute(stmt).tuples():
        option_names.setdefault(option.code, localized(option, "name", lang))
        option_orders[option.code] = min(
            option_orders.get(option.code, option.sort_order), option.sort_order
        )
        names = value_names.setdefault(option.code, {})
        orders = value_orders.setdefault(option.code, {})
        names.setdefault(value.code, localized(value, "name", lang))
        orders[value.code] = min(orders.get(value.code, value.sort_order), value.sort_order)

    result = []
    for option_code in sorted(option_orders, key=lambda code: (option_orders[code], code)):
        orders = value_orders[option_code]
        names = value_names[option_code]
        value_codes = sorted(orders, key=lambda code, orders=orders: (orders[code], code))
        values = [OptionValueDetail(code=code, name=names[code]) for code in value_codes]
        detail = OptionDetail(code=option_code, name=option_names[option_code], values=values)
        result.append(detail)
    return result


def list_products(
    session: Session,
    *,
    lang: Language,
    query: str | None = None,
    categories: Sequence[str] = (),
    options: Mapping[str, Sequence[str]] | None = None,
    sort: SortOrder = "newest",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    slugs: Sequence[str] = (),
    promotion: LoadedPromotion | None = None,
    sale_only: bool = False,
) -> ProductPage:
    """已发布商品的一页。

    query 按当前语言名称或英文名称做不区分大小写的包含匹配；多个分类 slug 之间为或；
    options 是规格名 code → 规格值 code 列表；slugs 非空时只留 slug 在其中的商品，
    不存在或未发布的 slug 自然被略去（SHOP-TASK-053）。各筛选之间为且。价格排序按最低单价
    （有活动时仍按原价）；平手一律按商品 id 升序，翻页不重复、不遗漏。

    promotion 为调用方读出的生效活动，每项据此给出命中的规则与最低活动单价；sale_only 为真时
    只留 promotion 全部商品级规则范围内的商品，没有活动或活动没有商品级规则时为空页。
    """
    conditions = [published()]
    if sale_only:
        scope = _sale_scope(promotion)
        if scope is None:
            return ProductPage(total=0, page=page, page_size=page_size, items=[])
        conditions.append(scope)
    if query:
        conditions.append(
            or_(
                getattr(Product, f"name_{lang}").icontains(query, autoescape=True),
                Product.name_en.icontains(query, autoescape=True),
            )
        )
    if categories:
        conditions.append(Category.slug.in_(categories))
    if options:
        conditions.append(_has_matching_variant(options))
    if slugs:
        conditions.append(Product.slug.in_(slugs))

    count = select(func.count()).join_from(Product, Category, _in_category)
    total = session.scalar(count.where(*conditions))

    min_price = _min_price.label("min_price_sen")
    order_by = {
        "newest": _newest,
        "price_asc": (min_price, Product.id),
        "price_desc": (min_price.desc(), Product.id),
    }[sort]
    stmt = (
        select(Product, Category, min_price, _active_count, _max_stock, _first_image)
        .join_from(Product, Category, _in_category)
        .where(*conditions)
        .order_by(*order_by)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = session.execute(stmt).all()

    hits = {product.id: _hit_rule(promotion, product) for product, *_ in rows}
    discounted = [
        product_id
        for product_id, hit in hits.items()
        if hit is not None and isinstance(hit[1], ItemDiscountRule)
    ]
    sale_prices: dict[int, list[int]] = {}
    if discounted:
        stmt = select(ProductVariant.product_id, ProductVariant.price_sen).where(
            ProductVariant.product_id.in_(discounted), ProductVariant.is_active.is_(True)
        )
        for product_id, price_sen in session.execute(stmt):
            sale_price = _sale_price(hits[product_id][1], price_sen)
            sale_prices.setdefault(product_id, []).append(sale_price)

    items = [
        ProductSummary(
            slug=product.slug,
            name=localized(product, "name", lang),
            category=_category_summary(category, lang),
            image=image,
            min_price_sen=min_price_sen,
            has_multiple_variants=active_count > 1,
            sold_out_today=max_stock == 0,
            promotion=_card(hits[product.id]),
            min_sale_price_sen=min(sale_prices.get(product.id, ()), default=None),
        )
        for product, category, min_price_sen, active_count, max_stock, image in rows
    ]
    return ProductPage(total=total, page=page, page_size=page_size, items=items)


def _in_sort_order[T](session: Session, model: type[T], product_id: int) -> list[T]:
    stmt = select(model).where(model.product_id == product_id).order_by(model.sort_order, model.id)
    return list(session.scalars(stmt))


def get_product(
    session: Session,
    slug: str,
    lang: Language,
    *,
    promotion: LoadedPromotion | None = None,
) -> ProductDetail | None:
    """已发布商品的详情；不存在与未发布同样返回 None，调用方给同一个 404。

    promotion 为调用方读出的生效活动，据此给出命中的规则与打折规则下各 SKU 的活动单价。
    """
    stmt = select(Product, Category).join_from(Product, Category, _in_category)
    row = session.execute(stmt.where(published(), Product.slug == slug)).first()
    if row is None:
        return None
    product, category = row

    images = _in_sort_order(session, ProductImage, product.id)
    options = _in_sort_order(session, ProductOption, product.id)
    values = _in_sort_order(session, ProductOptionValue, product.id)
    stmt = (
        select(ProductVariant)
        .where(ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True))
        .order_by(ProductVariant.id)
    )
    variants = list(session.scalars(stmt))
    stmt = select(VariantOptionValue).where(VariantOptionValue.product_id == product.id)
    links = list(session.scalars(stmt))

    values_by_option: dict[int, list[OptionValueDetail]] = {}
    for value in values:
        detail = OptionValueDetail(code=value.code, name=localized(value, "name", lang))
        values_by_option.setdefault(value.option_id, []).append(detail)

    value_code = {value.id: value.code for value in values}
    chosen: dict[int, dict[int, str]] = {}
    for link in links:
        chosen.setdefault(link.variant_id, {})[link.option_id] = value_code[link.option_value_id]

    hit = _hit_rule(promotion, product)
    variant_details = []
    for variant in variants:
        selected = chosen.get(variant.id, {})
        variant_details.append(
            VariantDetail(
                sku=variant.sku,
                options={o.code: selected[o.id] for o in options if o.id in selected},
                price_sen=variant.price_sen,
                available_stock=variant.available_stock,
                sale_price_sen=None if hit is None else _sale_price(hit[1], variant.price_sen),
            )
        )

    return ProductDetail(
        slug=product.slug,
        name=localized(product, "name", lang),
        description=localized(product, "description", lang),
        category=_category_summary(category, lang),
        images=[image.storage_ref for image in images],
        options=[
            OptionDetail(
                code=option.code,
                name=localized(option, "name", lang),
                values=values_by_option.get(option.id, []),
            )
            for option in options
        ],
        variants=variant_details,
        max_per_order=product.max_per_order,
        promotion=_card(hit),
    )

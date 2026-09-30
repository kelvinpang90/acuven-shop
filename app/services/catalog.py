"""商品目录只读查询：发布规则、文案回退英文、搜索、筛选、排序与分页。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行：
「文案缺少当前语言时回退英文，再缺失则商品不发布」；「边界与原则」：金额以 MYR 的仙为整数单位。
只发 SELECT，不写库。返回值里没有参考外币、每日初始库存、启用状态等后台字段。
文案为 NULL、空串或只有空格都算缺少。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)

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
class ProductSummary:
    slug: str
    name: LocalizedText
    category: CategorySummary
    # 排列序号最小的图片的存储引用；没有图片时为 None。
    image: str | None
    # 启用 SKU 中的最低单价（MYR 整数仙），供「RM x 起」与价格排序。
    min_price_sen: int
    # 所有启用 SKU 的当日可用库存都是 0。
    sold_out_today: bool


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


@dataclass(frozen=True)
class ProductDetail:
    slug: str
    name: LocalizedText
    description: LocalizedText
    category: CategorySummary
    images: list[str]
    options: list[OptionDetail]
    variants: list[VariantDetail]


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
_first_image = (
    select(ProductImage.storage_ref)
    .where(ProductImage.product_id == Product.id)
    .order_by(ProductImage.sort_order)
    .limit(1)
    .correlate(Product)
    .scalar_subquery()
)


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


def list_categories(session: Session, lang: Language) -> list[CategorySummary]:
    """启用且有英文名称的分类，按 id 排序。"""
    stmt = (
        select(Category)
        .where(Category.is_active.is_(True), _present(Category.name_en))
        .order_by(Category.id)
    )
    return [_category_summary(category, lang) for category in session.scalars(stmt)]


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
) -> ProductPage:
    """已发布商品的一页。

    query 按当前语言名称或英文名称做不区分大小写的包含匹配；多个分类 slug 之间为或；
    options 是规格名 code → 规格值 code 列表。价格排序按最低单价；
    平手一律按商品 id 升序，翻页不重复、不遗漏。
    """
    conditions = [published()]
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

    count = select(func.count()).join_from(Product, Category, _in_category)
    total = session.scalar(count.where(*conditions))

    min_price = _min_price.label("min_price_sen")
    order_by = {
        "newest": (Product.created_at.desc(), Product.id),
        "price_asc": (min_price, Product.id),
        "price_desc": (min_price.desc(), Product.id),
    }[sort]
    stmt = (
        select(Product, Category, min_price, _max_stock, _first_image)
        .join_from(Product, Category, _in_category)
        .where(*conditions)
        .order_by(*order_by)
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    items = [
        ProductSummary(
            slug=product.slug,
            name=localized(product, "name", lang),
            category=_category_summary(category, lang),
            image=image,
            min_price_sen=min_price_sen,
            sold_out_today=max_stock == 0,
        )
        for product, category, min_price_sen, max_stock, image in session.execute(stmt)
    ]
    return ProductPage(total=total, page=page, page_size=page_size, items=items)


def _in_sort_order[T](session: Session, model: type[T], product_id: int) -> list[T]:
    stmt = select(model).where(model.product_id == product_id).order_by(model.sort_order, model.id)
    return list(session.scalars(stmt))


def get_product(session: Session, slug: str, lang: Language) -> ProductDetail | None:
    """已发布商品的详情；不存在与未发布同样返回 None，调用方给同一个 404。"""
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

    variant_details = []
    for variant in variants:
        selected = chosen.get(variant.id, {})
        variant_details.append(
            VariantDetail(
                sku=variant.sku,
                options={o.code: selected[o.id] for o in options if o.id in selected},
                price_sen=variant.price_sen,
                available_stock=variant.available_stock,
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
    )

"""购物车与结账计价（游客计价）：只读，按当前价格重算，不建订单、不预留库存。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Cart 一行：「浏览器中的商品规格与数量；
不信任浏览器价格，结账时服务端重算」；ShippingRate / DemoFxRate 一行；「计价、优惠、积分与库存」
第 1、2 条：服务端以规格价格计算商品小计，运费另计；「边界与原则」：金额以 MYR 的仙为整数单位，
其他货币只按固定演示汇率显示参考数，按收货国家决定、不按 IP。

只发 SELECT，不写库、不缓存价格与费率。
SKU 不存在、停用或所属商品未发布的行只给 SKU、件数与状态。
优惠券与积分不在这里，由之后的任务扩展。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
from app.services.pricing import OrderLine, price_order
from app.services.shipping import FxReference, ShippingQuote, quote_shipping, reference_amount

MAX_CART_LINES = 20
MAX_LINE_QUANTITY = 10
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
    status: Literal["ok", "insufficient_stock"]
    # 库存不足时是当日可用库存（可为 0）；正常行为 None。
    available_stock: int | None
    product_slug: str
    name: LocalizedText
    # 按规格名的排列序号。
    options: list[SelectedOption]
    # 排列序号最小的图片的存储引用；没有图片时为 None。
    image: str | None
    unit_price_sen: int
    line_subtotal_sen: int


@dataclass(frozen=True)
class CheckoutQuote:
    lines: list[UnavailableLine | PricedLine]
    # 正常与库存不足各行行小计之和。
    subtotal_sen: int
    # 以下三项只在给了收货国家时有值。
    shipping: ShippingQuote | None
    total_sen: int | None
    # 合计的参考外币；该国无演示汇率（含马来西亚）时也为 None。
    fx_reference: FxReference | None
    # 所有行均为正常且给了收货国家；下单时由之后的任务重新计算与校验。
    can_place_order: bool


def _purchasable(
    session: Session,
    skus: Sequence[str],
) -> dict[str, tuple[ProductVariant, Product]]:
    """启用且所属商品已发布的 SKU，按 SKU 原文索引。

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


def _selected_options(
    session: Session,
    product_ids: set[int],
    variant_ids: set[int],
    lang: Language,
) -> dict[int, list[SelectedOption]]:
    """每个 SKU 所选的规格，按规格名的排列序号。"""
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
) -> CheckoutQuote:
    """按请求顺序逐行计价。

    行数、件数与 SKU 不重复由调用方校验。给了收货国家时先查运费（app/services/shipping.py）：
    目的地不合法抛 InvalidDestination；应有的运费行缺失抛 ShippingRateMissing，
    不以零运费代替。
    """
    shipping = None
    if country_code is not None:
        shipping = quote_shipping(session, country_code, state_code)

    found = _purchasable(session, [item.sku for item in items])
    product_ids = {product.id for _, product in found.values()}
    variant_ids = {variant.id for variant, _ in found.values()}
    images = _first_images(session, product_ids) if found else {}
    options = _selected_options(session, product_ids, variant_ids, lang) if found else {}

    lines: list[UnavailableLine | PricedLine] = []
    priced: list[OrderLine] = []
    for item in items:
        if item.sku not in found:
            lines.append(
                UnavailableLine(sku=item.sku, quantity=item.quantity, status="unavailable")
            )
            continue
        variant, product = found[item.sku]
        short = variant.available_stock < item.quantity
        lines.append(
            PricedLine(
                sku=item.sku,
                quantity=item.quantity,
                status="insufficient_stock" if short else "ok",
                available_stock=variant.available_stock if short else None,
                product_slug=product.slug,
                name=localized(product, "name", lang),
                options=options[variant.id],
                image=images.get(product.id),
                unit_price_sen=variant.price_sen,
                line_subtotal_sen=variant.price_sen * item.quantity,
            )
        )
        priced.append(OrderLine(unit_price=variant.price_sen, quantity=item.quantity))

    subtotal = price_order(priced).subtotal
    total = None if shipping is None else subtotal + shipping.fee_sen
    fx = None if total is None else reference_amount(session, country_code, total)
    all_ok = all(line.status == "ok" for line in lines)
    return CheckoutQuote(
        lines=lines,
        subtotal_sen=subtotal,
        shipping=shipping,
        total_sen=total,
        fx_reference=fx,
        can_place_order=all_ok and shipping is not None,
    )

"""后台维护商品：请求结构、第一步的共用检查、读取全部商品与单件商品、新建商品与代码生成、
保存已有商品。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（下称「第 11 条」）
与 docs/HANDOFF.md 0.77 的落实写法。已有行的保存为 SHOP-TASK-184，保存时的新增由 185 接上，
接口由 186 接上；本模块只提供规则函数，不写日志。

请求结构（ProductInput 等五个类型）新建与保存共用，接口的 JSON 字段与之同名。已有的行带行 ID，
新增的规格值与 SKU 带请求内的临时编号 temp_id；新建商品时请求里不得有任何行 ID。
第一步 check_product_request 只看请求、不碰数据库，不通过抛 ProductInvalid。
新建在第一步通过后回滚此前的事务，在新事务里以共享锁读所选分类（与第 14 条保存分类时锁分类行
互相等待），再生成代码、插入各行、每个新行写一条审计后提交。代码生成的四个函数保存任务照用。
保存已有商品在第一步通过后回滚此前的事务，在新事务里依次锁商品行、该商品的 SKU 行（按 id，
与下单扣减、每日重置一致）后核对版本号与行 ID（第二步），再以共享锁读分类、查新增 SKU 的
组合与已有 SKU 不重复（第三步），逐项写入有改动的值、每项一条审计，插入新增的规格值与 SKU、
各一条审计，有改动或新增时版本号加一后提交。
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
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
from app.services.admin_auth import record_audit
from app.services.admin_codes import code_base, unique_code

# 审计操作名与对象类别（对象 ID 为各自的行 ID）。
ADMIN_PRODUCT_CREATED = "admin_product_created"
ADMIN_PRODUCT_OPTION_CREATED = "admin_product_option_created"
ADMIN_PRODUCT_OPTION_VALUE_CREATED = "admin_product_option_value_created"
ADMIN_PRODUCT_VARIANT_CREATED = "admin_product_variant_created"
ADMIN_PRODUCT_CHANGED = "admin_product_changed"
ADMIN_PRODUCT_OPTION_CHANGED = "admin_product_option_changed"
ADMIN_PRODUCT_OPTION_VALUE_CHANGED = "admin_product_option_value_changed"
ADMIN_PRODUCT_VARIANT_CHANGED = "admin_product_variant_changed"
AUDIT_TARGET_PRODUCT = "product"
AUDIT_TARGET_OPTION = "product_option"
AUDIT_TARGET_OPTION_VALUE = "product_option_value"
AUDIT_TARGET_VARIANT = "product_variant"

# 第 11 条「取值」。
NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 5000
OPTION_NAME_MAX_LENGTH = 100
PRICE_MAX_SEN = 50_000_000
DAILY_STOCK_MAX = 9999
MAX_PER_ORDER_MIN = 1
MAX_PER_ORDER_MAX = 1000
# 第 11 条「结构与上限」。
MAX_OPTIONS = 3
MAX_VALUES_PER_OPTION = 20
MAX_VARIANTS = 50
# 第 11 条「代码」。
SLUG_MAX_LENGTH = 100
CODE_MAX_LENGTH = 50
SKU_MAX_LENGTH = 64
SLUG_FALLBACK = "product"
OPTION_CODE_FALLBACK = "option"
VALUE_CODE_FALLBACK = "value"
# 没有规格名的商品唯一的 SKU 为 slug 接 -std（与示例数据一致）。
NO_OPTION_SKU_SUFFIX = "std"
NEW_PRODUCT_VERSION = 1
# 分类 ID 超出 INT 的范围即不存在；先拦下，免得驱动在比较之前就报溢出。
MAX_ROW_ID = 2**31 - 1
# 第 11 条「文案的字段带语言，如 name_zh，值取前 100 个字符」。
AUDIT_TEXT_LENGTH = 100

_TEMP_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")


class ProductInvalid(Exception):
    """取值不合格：请求的结构、格式、取值范围、上限、英文名称重复、临时编号、上架条件，
    或所选分类不存在、停用、缺英文名称。"""


class ProductChanged(Exception):
    """商品已被改动：新建或保存时新增的行生成的代码撞上唯一约束（只在两个后台会话同时保存时
    发生）；保存时
    商品不存在、版本号与当前不同，或请求列出的行 ID 不恰好是该商品现有的全部行。"""


# ---------------------------------------------------------------------------
# 请求结构（新建与保存共用，SHOP-TASK-184 至 186 照用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueRef:
    """新增 SKU 所选的一个规格值：id 引用已有的，temp_id 引用同一请求里新增的，恰有一个。"""

    id: int | None
    temp_id: str | None


@dataclass(frozen=True)
class ValueInput:
    """规格值：已有的只带 id，新增的只带 temp_id。"""

    id: int | None
    temp_id: str | None
    name_en: str
    name_zh: str
    name_ms: str


@dataclass(frozen=True)
class OptionInput:
    """规格名：id 为 None 表示新建（只在新建商品时出现）。values 为 list 或 tuple。"""

    id: int | None
    name_en: str
    name_zh: str
    name_ms: str
    values: Sequence[ValueInput]


@dataclass(frozen=True)
class VariantInput:
    """SKU：已有的只带 id 且 option_values 为 None；新增的只带 temp_id，option_values 按规格名
    顺序列出 ValueRef（list 或 tuple）。价格为整仙。"""

    id: int | None
    temp_id: str | None
    option_values: Sequence[ValueRef] | None
    price_sen: int
    daily_initial_stock: int
    is_active: bool


@dataclass(frozen=True)
class ProductInput:
    """一件商品的全部可改内容；options 与 variants 为 list 或 tuple。"""

    category_id: int
    name_en: str
    name_zh: str
    name_ms: str
    description_en: str
    description_zh: str
    description_ms: str
    is_active: bool
    max_per_order: int
    options: Sequence[OptionInput]
    variants: Sequence[VariantInput]


# 第一步的结果：文案已去掉首尾空白、空串为 None，列表为 tuple，ValueRef 只留引用的那一项。


@dataclass(frozen=True)
class CheckedValue:
    id: int | None
    temp_id: str | None
    name_en: str | None
    name_zh: str | None
    name_ms: str | None


@dataclass(frozen=True)
class CheckedOption:
    id: int | None
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    values: tuple[CheckedValue, ...]


@dataclass(frozen=True)
class CheckedVariant:
    id: int | None
    temp_id: str | None
    option_values: tuple[ValueRef, ...] | None
    price_sen: int
    daily_initial_stock: int
    is_active: bool


@dataclass(frozen=True)
class CheckedProduct:
    category_id: int
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    description_en: str | None
    description_zh: str | None
    description_ms: str | None
    is_active: bool
    max_per_order: int
    options: tuple[CheckedOption, ...]
    variants: tuple[CheckedVariant, ...]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminProductListItem:
    """商品列表的一项：三语名称为空时为 None；分类名称为所属分类当前的名称。"""

    id: int
    slug: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    is_active: bool
    category_id: int
    category_name_en: str | None
    category_name_zh: str | None
    category_name_ms: str | None


@dataclass(frozen=True)
class AdminProductValue:
    id: int
    code: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None


@dataclass(frozen=True)
class AdminProductOption:
    id: int
    code: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    values: list[AdminProductValue]


@dataclass(frozen=True)
class AdminProductVariant:
    """option_value_ids 按规格名的顺序排列。"""

    id: int
    sku: str
    price_sen: int
    daily_initial_stock: int
    available_stock: int
    is_active: bool
    option_value_ids: list[int]


@dataclass(frozen=True)
class AdminProductImage:
    id: int
    storage_ref: str


@dataclass(frozen=True)
class AdminProduct:
    """单件商品：规格名与规格值按 sort_order（同序按 id），SKU 按 id，图片按 sort_order。"""

    id: int
    slug: str
    version: int
    category_id: int
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    description_en: str | None
    description_zh: str | None
    description_ms: str | None
    is_active: bool
    max_per_order: int
    options: list[AdminProductOption]
    variants: list[AdminProductVariant]
    images: list[AdminProductImage]


def load_admin_products(db: Session) -> list[AdminProductListItem]:
    """全部商品（含未上架的），按 created_at 从新到旧、同时间按 id 从大到小，不分页。
    只查列值、只发 SELECT，不写库、不写审计。"""
    stmt = (
        select(
            Product.id,
            Product.slug,
            Product.name_en,
            Product.name_zh,
            Product.name_ms,
            Product.is_active,
            Product.category_id,
            Category.name_en,
            Category.name_zh,
            Category.name_ms,
        )
        .join_from(Product, Category, Category.id == Product.category_id)
        .order_by(Product.created_at.desc(), Product.id.desc())
    )
    return [
        AdminProductListItem(
            id=row[0],
            slug=row[1],
            name_en=row[2] or None,
            name_zh=row[3] or None,
            name_ms=row[4] or None,
            is_active=bool(row[5]),
            category_id=row[6],
            category_name_en=row[7] or None,
            category_name_zh=row[8] or None,
            category_name_ms=row[9] or None,
        )
        for row in db.execute(stmt)
    ]


def load_admin_product(db: Session, product_id: int) -> AdminProduct | None:
    """一件商品的全部可改内容、版本号、各行 ID 与图片；不存在时返回 None。
    只查列值、只发 SELECT，不写库、不写审计。"""
    stmt = select(
        Product.id,
        Product.slug,
        Product.version,
        Product.category_id,
        Product.name_en,
        Product.name_zh,
        Product.name_ms,
        Product.description_en,
        Product.description_zh,
        Product.description_ms,
        Product.is_active,
        Product.max_per_order,
    ).where(Product.id == product_id)
    product = db.execute(stmt).first()
    if product is None:
        return None

    stmt = (
        select(
            ProductOptionValue.option_id,
            ProductOptionValue.id,
            ProductOptionValue.code,
            ProductOptionValue.name_en,
            ProductOptionValue.name_zh,
            ProductOptionValue.name_ms,
        )
        .where(ProductOptionValue.product_id == product_id)
        .order_by(ProductOptionValue.sort_order, ProductOptionValue.id)
    )
    values_by_option: dict[int, list[AdminProductValue]] = {}
    for row in db.execute(stmt):
        value = AdminProductValue(
            id=row[1],
            code=row[2],
            name_en=row[3] or None,
            name_zh=row[4] or None,
            name_ms=row[5] or None,
        )
        values_by_option.setdefault(row[0], []).append(value)

    stmt = (
        select(
            ProductOption.id,
            ProductOption.code,
            ProductOption.name_en,
            ProductOption.name_zh,
            ProductOption.name_ms,
        )
        .where(ProductOption.product_id == product_id)
        .order_by(ProductOption.sort_order, ProductOption.id)
    )
    options = [
        AdminProductOption(
            id=row[0],
            code=row[1],
            name_en=row[2] or None,
            name_zh=row[3] or None,
            name_ms=row[4] or None,
            values=values_by_option.get(row[0], []),
        )
        for row in db.execute(stmt)
    ]

    # SKU 的规格值按规格名的顺序排列。
    position = {option.id: index for index, option in enumerate(options)}
    stmt = select(
        VariantOptionValue.variant_id,
        VariantOptionValue.option_id,
        VariantOptionValue.option_value_id,
    ).where(VariantOptionValue.product_id == product_id)
    chosen: dict[int, list[tuple[int, int]]] = {}
    for variant_id, option_id, value_id in db.execute(stmt):
        order = position.get(option_id, len(position))
        chosen.setdefault(variant_id, []).append((order, value_id))

    stmt = (
        select(
            ProductVariant.id,
            ProductVariant.sku,
            ProductVariant.price_sen,
            ProductVariant.daily_initial_stock,
            ProductVariant.available_stock,
            ProductVariant.is_active,
        )
        .where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.id)
    )
    variants = [
        AdminProductVariant(
            id=row[0],
            sku=row[1],
            price_sen=row[2],
            daily_initial_stock=row[3],
            available_stock=row[4],
            is_active=bool(row[5]),
            option_value_ids=[value_id for _, value_id in sorted(chosen.get(row[0], []))],
        )
        for row in db.execute(stmt)
    ]

    stmt = (
        select(ProductImage.id, ProductImage.storage_ref)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.sort_order, ProductImage.id)
    )
    images = [AdminProductImage(id=row[0], storage_ref=row[1]) for row in db.execute(stmt)]

    return AdminProduct(
        id=product[0],
        slug=product[1],
        version=product[2],
        category_id=product[3],
        name_en=product[4] or None,
        name_zh=product[5] or None,
        name_ms=product[6] or None,
        description_en=product[7] or None,
        description_zh=product[8] or None,
        description_ms=product[9] or None,
        is_active=bool(product[10]),
        max_per_order=product[11],
        options=options,
        variants=variants,
        images=images,
    )


# ---------------------------------------------------------------------------
# 第一步：不碰数据库检查请求本身（新建与保存共用）
# ---------------------------------------------------------------------------


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        raise ProductInvalid("names and descriptions must be strings")
    text = value.strip()
    if len(text) > limit:
        raise ProductInvalid(f"a name or description is longer than {limit} characters")
    return text or None


def _integer(value: object, low: int, high: int, field: str) -> int:
    # bool 是 int 的子类，但 True 不是一个数。
    if type(value) is not int or not low <= value <= high:
        raise ProductInvalid(f"{field} must be an integer from {low} to {high}")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ProductInvalid(f"{field} must be a boolean")
    return value


def _row_id(value: object) -> int | None:
    if value is not None and type(value) is not int:
        raise ProductInvalid("row ids must be integers")
    return value


def _temp_id(value: object) -> str | None:
    if value is not None and not (isinstance(value, str) and _TEMP_ID.fullmatch(value)):
        raise ProductInvalid(
            "temp_id must be 1 to 32 ASCII letters, digits, hyphens or underscores"
        )
    return value


def _items[T](value: object, kind: type[T], field: str) -> tuple[T, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(item, kind) for item in value):
        raise ProductInvalid(f"{field} must be a list of {kind.__name__}")
    return tuple(value)


def _name_key(name: str | None) -> str | None:
    """英文名称的比较口径：去掉首尾空白并转小写；为空时为 None，不参与比较。"""
    if name is None:
        return None
    return name.strip().lower() or None


def _check_unique_names(names: Sequence[str | None], what: str) -> None:
    keys = [key for key in map(_name_key, names) if key is not None]
    if len(keys) != len(set(keys)):
        raise ProductInvalid(f"two {what} have the same English name")


def _check_value(value: ValueInput, creating: bool) -> CheckedValue:
    value_id = _row_id(value.id)
    temp_id = _temp_id(value.temp_id)
    if (value_id is None) == (temp_id is None):
        raise ProductInvalid("each option value needs exactly one of id and temp_id")
    if creating and value_id is not None:
        raise ProductInvalid("a new product cannot refer to existing rows")
    name_en = _text(value.name_en, OPTION_NAME_MAX_LENGTH)
    if temp_id is not None and name_en is None:
        raise ProductInvalid("a new option value needs an English name")
    return CheckedValue(
        id=value_id,
        temp_id=temp_id,
        name_en=name_en,
        name_zh=_text(value.name_zh, OPTION_NAME_MAX_LENGTH),
        name_ms=_text(value.name_ms, OPTION_NAME_MAX_LENGTH),
    )


def _check_option(option: OptionInput, creating: bool) -> CheckedOption:
    option_id = _row_id(option.id)
    if creating and option_id is not None:
        raise ProductInvalid("a new product cannot refer to existing rows")
    name_en = _text(option.name_en, OPTION_NAME_MAX_LENGTH)
    if option_id is None and name_en is None:
        raise ProductInvalid("a new option needs an English name")
    values = _items(option.values, ValueInput, "values")
    if not 1 <= len(values) <= MAX_VALUES_PER_OPTION:
        raise ProductInvalid(f"each option needs 1 to {MAX_VALUES_PER_OPTION} values")
    checked = tuple(_check_value(value, creating) for value in values)
    _check_unique_names([value.name_en for value in checked], "values of an option")
    return CheckedOption(
        id=option_id,
        name_en=name_en,
        name_zh=_text(option.name_zh, OPTION_NAME_MAX_LENGTH),
        name_ms=_text(option.name_ms, OPTION_NAME_MAX_LENGTH),
        values=checked,
    )


def _check_ref(ref: ValueRef, option: CheckedOption) -> ValueRef:
    """新增 SKU 在一个规格名下所选的值：须是请求里该规格名下列出的已有值或新增的值。"""
    ref_id = _row_id(ref.id)
    ref_temp_id = _temp_id(ref.temp_id)
    if (ref_id is None) == (ref_temp_id is None):
        raise ProductInvalid("each option value reference needs exactly one of id and temp_id")
    if ref_id is not None:
        listed = {value.id for value in option.values if value.id is not None}
        found = ref_id in listed
    else:
        found = ref_temp_id in {value.temp_id for value in option.values}
    if not found:
        raise ProductInvalid("a variant refers to a value that is not listed under its option")
    return ValueRef(id=ref_id, temp_id=ref_temp_id)


def _check_variant(
    variant: VariantInput, options: tuple[CheckedOption, ...], creating: bool
) -> CheckedVariant:
    variant_id = _row_id(variant.id)
    temp_id = _temp_id(variant.temp_id)
    if (variant_id is None) == (temp_id is None):
        raise ProductInvalid("each variant needs exactly one of id and temp_id")
    if creating and variant_id is not None:
        raise ProductInvalid("a new product cannot refer to existing rows")

    refs: tuple[ValueRef, ...] | None = None
    if variant_id is not None:
        # 已有 SKU 的规格值组合不能改，请求里不带组合。
        if variant.option_values is not None:
            raise ProductInvalid("an existing variant cannot change its option values")
    else:
        given = _items(variant.option_values, ValueRef, "option_values")
        if len(given) != len(options):
            raise ProductInvalid("a new variant needs exactly one value for each option")
        refs = tuple(_check_ref(ref, option) for ref, option in zip(given, options, strict=True))

    return CheckedVariant(
        id=variant_id,
        temp_id=temp_id,
        option_values=refs,
        price_sen=_integer(variant.price_sen, 0, PRICE_MAX_SEN, "price_sen"),
        daily_initial_stock=_integer(
            variant.daily_initial_stock, 0, DAILY_STOCK_MAX, "daily_initial_stock"
        ),
        is_active=_boolean(variant.is_active, "is_active"),
    )


def _check_distinct(items: Sequence[object], what: str) -> None:
    present = [item for item in items if item is not None]
    if len(present) != len(set(present)):
        raise ProductInvalid(f"{what} must not repeat")


def check_product_request(product: ProductInput, *, creating: bool) -> CheckedProduct:
    """第 11 条核对的第（一）步：只看请求、不碰数据库（不发任何 SQL），不通过抛 ProductInvalid。

    creating 为真时是新建请求：请求里不得有任何行 ID（规格名、规格值、SKU 与 ValueRef），
    商品、每个规格名与每个规格值的英文名称必填。新增的规格值（带 temp_id）英文名称必填。
    检查：文案为 str，去掉首尾空白后名称 0–200、描述 0–5,000、规格名与规格值的名称 0–100 个
    字符（空串为 None）；分类 ID、价格、每日初始库存、限购为整数（布尔值不算）且在范围内，
    上架与启用状态为 bool；0–3 个规格名、每个 1–20 个值、1–50 个 SKU，没有规格名时恰有一个
    SKU；同一类行 ID 不重复；temp_id 的格式、在规格值之间与 SKU 之间各自不重复；已有 SKU 不带
    组合，新增 SKU 按规格名顺序在每个规格名下恰引用一个请求里列出的值，新增 SKU 之间组合不重复；
    规格名之间、同一规格名下规格值之间英文名称不重复；上架时请求里看得到的发布条件。
    """
    if not isinstance(product, ProductInput):
        raise ProductInvalid("the request must be a ProductInput")
    category_id = _integer(product.category_id, 1, MAX_ROW_ID, "category_id")
    name_en = _text(product.name_en, NAME_MAX_LENGTH)
    if creating and name_en is None:
        raise ProductInvalid("a new product needs an English name")
    is_active = _boolean(product.is_active, "is_active")
    max_per_order = _integer(
        product.max_per_order, MAX_PER_ORDER_MIN, MAX_PER_ORDER_MAX, "max_per_order"
    )

    option_inputs = _items(product.options, OptionInput, "options")
    if len(option_inputs) > MAX_OPTIONS:
        raise ProductInvalid(f"a product has at most {MAX_OPTIONS} options")
    variant_inputs = _items(product.variants, VariantInput, "variants")
    if not 1 <= len(variant_inputs) <= MAX_VARIANTS:
        raise ProductInvalid(f"a product needs 1 to {MAX_VARIANTS} variants")
    if not option_inputs and len(variant_inputs) != 1:
        raise ProductInvalid("a product without options has exactly one variant")

    options = tuple(_check_option(option, creating) for option in option_inputs)
    _check_unique_names([option.name_en for option in options], "options")
    values = [value for option in options for value in option.values]
    _check_distinct([option.id for option in options], "option ids")
    _check_distinct([value.id for value in values], "option value ids")
    _check_distinct([value.temp_id for value in values], "option value temp_ids")

    variants = tuple(_check_variant(variant, options, creating) for variant in variant_inputs)
    _check_distinct([variant.id for variant in variants], "variant ids")
    _check_distinct([variant.temp_id for variant in variants], "variant temp_ids")
    _check_distinct([variant.option_values for variant in variants], "new variant combinations")

    checked = CheckedProduct(
        category_id=category_id,
        name_en=name_en,
        name_zh=_text(product.name_zh, NAME_MAX_LENGTH),
        name_ms=_text(product.name_ms, NAME_MAX_LENGTH),
        description_en=_text(product.description_en, DESCRIPTION_MAX_LENGTH),
        description_zh=_text(product.description_zh, DESCRIPTION_MAX_LENGTH),
        description_ms=_text(product.description_ms, DESCRIPTION_MAX_LENGTH),
        is_active=is_active,
        max_per_order=max_per_order,
        options=options,
        variants=variants,
    )
    if is_active:
        _check_publishable(checked)
    return checked


def _check_publishable(product: CheckedProduct) -> None:
    """上架条件中只看请求就能判断的部分；分类的条件要查库，由调用方在锁分类后检查。"""
    if product.name_en is None or product.description_en is None:
        raise ProductInvalid("an active product needs an English name and description")
    for option in product.options:
        if option.name_en is None or any(value.name_en is None for value in option.values):
            raise ProductInvalid("an active product needs English names for all options")
    if not any(variant.is_active for variant in product.variants):
        raise ProductInvalid("an active product needs an active variant")


# ---------------------------------------------------------------------------
# 代码生成（新建与保存时的新增共用）
# ---------------------------------------------------------------------------


def generate_slug(db: Session, name_en: str) -> str:
    """商品 slug：unique_code(code_base(英文名称, product), 100, 全站已有 slug)。"""
    # 生成的代码全为小写；已有的按小写比较，MySQL 不区分大小写的排序规则下也不会撞上。
    taken = {slug.lower() for slug in db.scalars(select(Product.slug))}
    return unique_code(code_base(name_en, SLUG_FALLBACK), SLUG_MAX_LENGTH, taken)


def generate_option_codes(
    db: Session, names_en: Sequence[str | None], taken: Collection[str]
) -> list[str]:
    """按顺序给规格名生成代码。基本部分先沿用全站英文名称相同的规格名中 id 最小者的代码，
    没有才取 code_base(英文名称, option)；再在该商品内去重（taken 为该商品已有的规格名代码，
    先生成的也算）。"""
    reusable: dict[str, str] = {}
    stmt = select(ProductOption.name_en, ProductOption.code).order_by(ProductOption.id)
    for name, code in db.execute(stmt):
        key = _name_key(name)
        if key is not None:
            reusable.setdefault(key, code)
    return _unique_codes(names_en, reusable, OPTION_CODE_FALLBACK, taken)


def generate_value_codes(
    db: Session, option_code: str, names_en: Sequence[str | None], taken: Collection[str]
) -> list[str]:
    """按顺序给同一规格名下的规格值生成代码。基本部分先沿用全站规格名代码为 option_code 的
    规格名之下英文名称相同的规格值中 id 最小者的代码，没有才取 code_base(英文名称, value)；
    再在该规格名下去重（taken 为该规格名已有的规格值代码，先生成的也算）。"""
    reusable: dict[str, str] = {}
    stmt = (
        select(ProductOption.code, ProductOptionValue.name_en, ProductOptionValue.code)
        .join_from(
            ProductOptionValue, ProductOption, ProductOption.id == ProductOptionValue.option_id
        )
        .where(ProductOption.code == option_code)
        .order_by(ProductOptionValue.id)
    )
    for owner_code, name, code in db.execute(stmt):
        # 数据库的比较可能不分大小写，取回后照字面再比一次。
        key = _name_key(name)
        if owner_code == option_code and key is not None:
            reusable.setdefault(key, code)
    return _unique_codes(names_en, reusable, VALUE_CODE_FALLBACK, taken)


def _unique_codes(
    names_en: Sequence[str | None],
    reusable: dict[str, str],
    fallback: str,
    taken: Collection[str],
) -> list[str]:
    used = {code.lower() for code in taken}
    codes = []
    for name in names_en:
        key = _name_key(name)
        base = reusable.get(key) if key is not None else None
        code = unique_code(base or code_base(name or "", fallback), CODE_MAX_LENGTH, used)
        used.add(code)
        codes.append(code)
    return codes


def generate_skus(db: Session, slug: str, combinations: Sequence[Sequence[str]]) -> list[str]:
    """按顺序给 SKU 生成代码：slug 依规格名的顺序以连字符接上各规格值的代码，没有规格名时为
    slug 接 -std；再在全站已有 SKU 与先生成的之间去重，上限 64 个字符。"""
    used = {sku.lower() for sku in db.scalars(select(ProductVariant.sku))}
    skus = []
    for value_codes in combinations:
        base = "-".join((slug, *(value_codes or (NO_OPTION_SKU_SUFFIX,))))
        sku = unique_code(base, SKU_MAX_LENGTH, used)
        used.add(sku)
        skus.append(sku)
    return skus


# ---------------------------------------------------------------------------
# 新建
# ---------------------------------------------------------------------------


def _has_text(value: str | None) -> bool:
    # 与前台发布规则的 TRIM 一致，只去空格。
    return value is not None and value.strip(" ") != ""


def check_category_for_product(db: Session, category_id: int) -> None:
    """以共享锁（SELECT … FOR SHARE）读分类：须存在、启用且有英文名称，否则抛 ProductInvalid。"""
    stmt = (
        select(Category.is_active, Category.name_en)
        .where(Category.id == category_id)
        .with_for_update(read=True)
    )
    row = db.execute(stmt).first()
    if row is None or not row[0] or not _has_text(row[1]):
        raise ProductInvalid("the category must exist, be active and have an English name")


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError:
        # 只在两个后台会话同时保存、生成同一代码时发生。
        raise ProductChanged("the products were changed by another session") from None


def create_product(
    db: Session, admin_account_id: int, product: ProductInput, now: datetime
) -> AdminProduct:
    """新建一件商品，返回它（同 load_admin_product 的结果）。

    now 为不带时区的 UTC 时间，作为 created_at 并交给 record_audit；admin_account_id 须在调用前
    取出（这里先回滚，调用方会话里的 ORM 对象随之过期）。按以下顺序：
    1. check_product_request(product, creating=True)，不通过抛 ProductInvalid、不发任何 SQL。
    2. 结束此前的事务（回滚），在新事务里以共享锁读所选分类，须存在、启用且有英文名称，否则
       抛 ProductInvalid。
    3. 按请求顺序生成 slug、规格名代码、规格值代码与 SKU 代码；插入商品（版本号 1）、规格名与
       规格值（sort_order 各按请求顺序从 0 起）、SKU（当日可用库存等于每日初始库存）与 SKU 的
       规格值关联。flush 撞上唯一约束抛 ProductChanged。
    4. 按商品、规格名、规格值、SKU 的顺序，每个新行写一条 *_created 审计（新值为 slug
       或代码），提交。
    任何异常都先回滚再抛出。
    """
    checked = check_product_request(product, creating=True)
    assert checked.name_en is not None

    db.rollback()
    try:
        check_category_for_product(db, checked.category_id)

        slug = generate_slug(db, checked.name_en)
        option_names = [option.name_en for option in checked.options]
        option_codes = generate_option_codes(db, option_names, ())
        value_codes = []
        for option, option_code in zip(checked.options, option_codes, strict=True):
            value_names = [value.name_en for value in option.values]
            value_codes.append(generate_value_codes(db, option_code, value_names, ()))
        # 新建时 SKU 只以 temp_id 引用规格值；temp_id 在整个请求的规格值之间唯一。
        code_by_temp_id = {
            value.temp_id: code
            for option, codes in zip(checked.options, value_codes, strict=True)
            for value, code in zip(option.values, codes, strict=True)
        }
        combinations = [
            [code_by_temp_id[ref.temp_id] for ref in variant.option_values or ()]
            for variant in checked.variants
        ]
        skus = generate_skus(db, slug, combinations)

        product_row = Product(
            category_id=checked.category_id,
            slug=slug,
            name_en=checked.name_en,
            name_zh=checked.name_zh,
            name_ms=checked.name_ms,
            description_en=checked.description_en,
            description_zh=checked.description_zh,
            description_ms=checked.description_ms,
            is_active=checked.is_active,
            max_per_order=checked.max_per_order,
            created_at=now,
            version=NEW_PRODUCT_VERSION,
        )
        db.add(product_row)
        _flush(db)
        product_id = product_row.id

        option_rows: list[ProductOption] = []
        value_rows: list[ProductOptionValue] = []
        value_by_temp_id: dict[str | None, ProductOptionValue] = {}
        for option_order, (option, option_code) in enumerate(
            zip(checked.options, option_codes, strict=True)
        ):
            option_row = ProductOption(
                product_id=product_id,
                code=option_code,
                sort_order=option_order,
                name_en=option.name_en,
                name_zh=option.name_zh,
                name_ms=option.name_ms,
            )
            db.add(option_row)
            _flush(db)
            option_rows.append(option_row)
            for value_order, (value, value_code) in enumerate(
                zip(option.values, value_codes[option_order], strict=True)
            ):
                value_row = ProductOptionValue(
                    product_id=product_id,
                    option_id=option_row.id,
                    code=value_code,
                    sort_order=value_order,
                    name_en=value.name_en,
                    name_zh=value.name_zh,
                    name_ms=value.name_ms,
                )
                db.add(value_row)
                value_rows.append(value_row)
                value_by_temp_id[value.temp_id] = value_row
        _flush(db)

        variant_rows: list[ProductVariant] = []
        for variant, sku in zip(checked.variants, skus, strict=True):
            variant_row = ProductVariant(
                product_id=product_id,
                sku=sku,
                price_sen=variant.price_sen,
                daily_initial_stock=variant.daily_initial_stock,
                # 新增 SKU 还没有预留：当日可用库存等于每日初始库存。
                available_stock=variant.daily_initial_stock,
                is_active=variant.is_active,
            )
            db.add(variant_row)
            _flush(db)
            variant_rows.append(variant_row)
            for ref in variant.option_values or ():
                value_row = value_by_temp_id[ref.temp_id]
                db.add(
                    VariantOptionValue(
                        variant_id=variant_row.id,
                        option_value_id=value_row.id,
                        product_id=product_id,
                        option_id=value_row.option_id,
                    )
                )
        _flush(db)

        audits = [(ADMIN_PRODUCT_CREATED, AUDIT_TARGET_PRODUCT, product_id, slug)]
        audits += [
            (ADMIN_PRODUCT_OPTION_CREATED, AUDIT_TARGET_OPTION, row.id, row.code)
            for row in option_rows
        ]
        audits += [
            (ADMIN_PRODUCT_OPTION_VALUE_CREATED, AUDIT_TARGET_OPTION_VALUE, row.id, row.code)
            for row in value_rows
        ]
        audits += [
            (ADMIN_PRODUCT_VARIANT_CREATED, AUDIT_TARGET_VARIANT, row.id, row.sku)
            for row in variant_rows
        ]
        for action, target_type, target_id, new_value in audits:
            record_audit(
                db,
                action,
                admin_account_id,
                now,
                target_type=target_type,
                target_id=target_id,
                new_value=new_value,
            )
        saved = load_admin_product(db, product_id)
        assert saved is not None
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


# ---------------------------------------------------------------------------
# 保存已有商品
# ---------------------------------------------------------------------------

# 逐项比较与写审计的顺序；字段名照模型列名（HANDOFF 0.77）。
PRODUCT_FIELDS = (
    "category_id",
    "is_active",
    "max_per_order",
    "name_en",
    "name_zh",
    "name_ms",
    "description_en",
    "description_zh",
    "description_ms",
)
NAME_FIELDS = ("name_en", "name_zh", "name_ms")
VARIANT_FIELDS = ("price_sen", "daily_initial_stock", "is_active")

type _FieldValue = str | int | bool | None
# (操作名, 对象类别, 对象 ID, 旧值, 新值)；新增行的审计没有旧值。
type _Audit = tuple[str, str, int, str | None, str]
# 新增的 SKU 与它在各规格名下所选的值（规格名 ID → ValueRef）。
type _Choice = tuple[CheckedVariant, dict[int | None, ValueRef]]


def lock_product_statement(product_id: int) -> Select[tuple[Product]]:
    """锁定商品行的查询（SELECT … FOR UPDATE）；保存第二步的第一条语句。"""
    return (
        select(Product)
        .where(Product.id == product_id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def lock_variants_statement(product_id: int) -> Select[tuple[ProductVariant]]:
    """锁定该商品全部 SKU 行的查询（SELECT … FOR UPDATE，按 id 从小到大，与下单扣减、每日重置
    的加锁顺序一致）；在锁商品行之后执行。"""
    return (
        select(ProductVariant)
        .where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _check_save_request(product: ProductInput, version: object) -> CheckedProduct:
    """保存的第一步：共用检查之外，version 为整数、规格名都带 id；不发任何 SQL。
    上限、英文名称不重复与新增 SKU 的引用由共用检查按请求里的全部项（现有加新增）判断。"""
    checked = check_product_request(product, creating=False)
    # bool 是 int 的子类，但 True 不是一个版本号。
    if type(version) is not int:
        raise ProductInvalid("version must be an integer")
    if any(option.id is None for option in checked.options):
        raise ProductInvalid("options cannot be added to an existing product")
    return checked


def _lock_and_match(
    db: Session, product_id: int, version: int, checked: CheckedProduct
) -> tuple[Product, list[ProductOption], list[ProductOptionValue], list[ProductVariant]]:
    """保存的第二步：锁商品行、再锁该商品的 SKU 行，核对版本号与行 ID，不通过抛
    ProductChanged。返回商品行、规格名（按 sort_order 再按 id）、规格值（同）与 SKU（按 id）。"""
    product_row = None
    # 超出 INT 范围的 ID 不会存在；不拿去比较，免得驱动报溢出。
    if type(product_id) is int and 1 <= product_id <= MAX_ROW_ID:
        product_row = db.scalars(lock_product_statement(product_id)).one_or_none()
    if product_row is None:
        raise ProductChanged("the product does not exist")
    variant_rows = list(db.scalars(lock_variants_statement(product_id)))
    if product_row.version != version:
        raise ProductChanged("the product was changed since it was read")

    # 规格名与规格值只经后台保存改动，商品行已锁，不另加锁。
    option_stmt = (
        select(ProductOption)
        .where(ProductOption.product_id == product_id)
        .order_by(ProductOption.sort_order, ProductOption.id)
        .execution_options(populate_existing=True)
    )
    option_rows = list(db.scalars(option_stmt))
    value_stmt = (
        select(ProductOptionValue)
        .where(ProductOptionValue.product_id == product_id)
        .order_by(ProductOptionValue.sort_order, ProductOptionValue.id)
        .execution_options(populate_existing=True)
    )
    value_rows = list(db.scalars(value_stmt))

    # 第一步已查过同一类行 ID 不重复，集合与映射相等即恰好是现有的全部行；新增的项不带行 ID。
    listed_values = {
        value.id: option.id
        for option in checked.options
        for value in option.values
        if value.id is not None
    }
    current_values = {row.id: row.option_id for row in value_rows}
    listed_variants = {variant.id for variant in checked.variants if variant.id is not None}
    if (
        {option.id for option in checked.options} != {row.id for row in option_rows}
        or listed_values != current_values
        or listed_variants != {row.id for row in variant_rows}
    ):
        raise ProductChanged("the product was changed since it was read")
    return product_row, option_rows, value_rows, variant_rows


def _check_category(db: Session, product_row: Product, checked: CheckedProduct) -> None:
    """保存的第三步：分类改了时以共享锁读新分类；分类不变而保存为上架时以共享锁读所属分类。
    分类不变且不上架时不检查（停用的当前分类可以保留）。"""
    if checked.category_id != product_row.category_id:
        check_category_for_product(db, checked.category_id)
    elif checked.is_active:
        check_category_for_product(db, product_row.category_id)


def _new_choices(checked: CheckedProduct) -> list[_Choice]:
    """请求里新增的 SKU（按请求顺序）与它们在各规格名下所选的值；第一步已查过每个规格名下
    恰有一个值。"""
    choices: list[_Choice] = []
    for variant in checked.variants:
        if variant.id is None:
            refs = zip(checked.options, variant.option_values or (), strict=True)
            choices.append((variant, {option.id: ref for option, ref in refs}))
    return choices


def _check_new_combinations(db: Session, product_id: int, choices: Sequence[_Choice]) -> None:
    """保存的第三步之二：新增 SKU 的组合与该商品已有 SKU（含停用的）的组合不重复，否则抛
    ProductInvalid。引用了新增规格值的组合不会与已有的相同；没有新增 SKU 时不查库。"""
    if not choices:
        return
    # SKU 行已锁；关联行只随新增 SKU 写入，后者经后台保存、在商品行上排队。
    columns = (VariantOptionValue.variant_id, VariantOptionValue.option_value_id)
    stmt = select(*columns).where(VariantOptionValue.product_id == product_id)
    existing: dict[int, set[int]] = {}
    for variant_id, value_id in db.execute(stmt):
        existing.setdefault(variant_id, set()).add(value_id)
    taken = {frozenset(value_ids) for value_ids in existing.values()}
    for _, refs in choices:
        value_ids = [ref.id for ref in refs.values()]
        if None not in value_ids and frozenset(value_ids) in taken:
            raise ProductInvalid("a new variant has the same option values as an existing one")


def _insert_additions(
    db: Session,
    product_row: Product,
    option_rows: Sequence[ProductOption],
    value_rows: Sequence[ProductOptionValue],
    checked: CheckedProduct,
    choices: Sequence[_Choice],
) -> list[_Audit]:
    """插入保存请求里新增的规格值与 SKU，返回它们的 *_created 审计（新值为代码）。

    规格值按规格名的顺序、同一规格名下按请求顺序，sort_order 从该规格名已有的最大值加一起；
    代码经 generate_value_codes 在该规格名下去重。SKU 按请求顺序插入，代码经 generate_skus
    按规格名的顺序取各值的代码、全站去重；当日可用库存等于每日初始库存。代码都先生成、再
    插入；flush 撞上唯一约束抛 ProductChanged。没有新增时不发 SQL。"""
    options_by_id = {option.id: option for option in checked.options}
    code_by_id = {row.id: row.code for row in value_rows}
    code_by_temp_id: dict[str | None, str] = {}
    planned: list[tuple[ProductOption, CheckedValue, str, int]] = []
    for option_row in option_rows:
        added = [value for value in options_by_id[option_row.id].values if value.id is None]
        if not added:
            continue
        current = [row for row in value_rows if row.option_id == option_row.id]
        codes = generate_value_codes(
            db, option_row.code, [value.name_en for value in added], [row.code for row in current]
        )
        start = max((row.sort_order for row in current), default=-1) + 1
        for offset, (value, code) in enumerate(zip(added, codes, strict=True)):
            planned.append((option_row, value, code, start + offset))
            code_by_temp_id[value.temp_id] = code

    skus: list[str] = []
    if choices:
        combinations = [
            [
                code_by_id[ref.id] if ref.id is not None else code_by_temp_id[ref.temp_id]
                for ref in (refs[option_row.id] for option_row in option_rows)
            ]
            for _, refs in choices
        ]
        skus = generate_skus(db, product_row.slug, combinations)

    product_id = product_row.id
    new_value_rows: list[ProductOptionValue] = []
    value_by_id = {row.id: row for row in value_rows}
    value_by_temp_id: dict[str | None, ProductOptionValue] = {}
    for option_row, value, code, sort_order in planned:
        value_row = ProductOptionValue(
            product_id=product_id,
            option_id=option_row.id,
            code=code,
            sort_order=sort_order,
            name_en=value.name_en,
            name_zh=value.name_zh,
            name_ms=value.name_ms,
        )
        db.add(value_row)
        new_value_rows.append(value_row)
        value_by_temp_id[value.temp_id] = value_row
    if new_value_rows:
        _flush(db)

    new_variant_rows: list[ProductVariant] = []
    for (variant, refs), sku in zip(choices, skus, strict=True):
        variant_row = ProductVariant(
            product_id=product_id,
            sku=sku,
            price_sen=variant.price_sen,
            daily_initial_stock=variant.daily_initial_stock,
            # 新增 SKU 还没有预留：当日可用库存等于每日初始库存。
            available_stock=variant.daily_initial_stock,
            is_active=variant.is_active,
        )
        db.add(variant_row)
        _flush(db)
        new_variant_rows.append(variant_row)
        for ref in refs.values():
            value_row = value_by_temp_id[ref.temp_id] if ref.id is None else value_by_id[ref.id]
            db.add(
                VariantOptionValue(
                    variant_id=variant_row.id,
                    option_value_id=value_row.id,
                    product_id=product_id,
                    option_id=value_row.option_id,
                )
            )
        _flush(db)

    audits: list[_Audit] = [
        (ADMIN_PRODUCT_OPTION_VALUE_CREATED, AUDIT_TARGET_OPTION_VALUE, row.id, None, row.code)
        for row in new_value_rows
    ]
    audits += [
        (ADMIN_PRODUCT_VARIANT_CREATED, AUDIT_TARGET_VARIANT, row.id, None, row.sku)
        for row in new_variant_rows
    ]
    return audits


def _audit_value(field: str, value: _FieldValue) -> str:
    """审计的「字段 值」：上架与启用状态为 true 或 false，文案取前 100 个字符，
    为空时只写字段名。"""
    if isinstance(value, bool):
        return f"{field} {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{field} {value}"
    if not value:
        return field
    return f"{field} {value[:AUDIT_TEXT_LENGTH]}"


def _update(
    row: object, wanted: object, fields: Sequence[str]
) -> list[tuple[str, _FieldValue, _FieldValue]]:
    """逐项与当前值比较，有改动的写新值；返回 (字段, 旧值, 新值)。
    空串与 NULL 都按空比较。"""
    changes: list[tuple[str, _FieldValue, _FieldValue]] = []
    for field in fields:
        old_value = getattr(row, field)
        if field == "is_active":
            old_value = bool(old_value)
        elif isinstance(old_value, str):
            old_value = old_value or None
        new_value = getattr(wanted, field)
        if old_value != new_value:
            changes.append((field, old_value, new_value))
            setattr(row, field, new_value)
    return changes


def _audit_rows(
    action: str,
    target_type: str,
    target_id: int,
    changes: list[tuple[str, _FieldValue, _FieldValue]],
    prefix: str = "",
) -> list[_Audit]:
    """每项改动一条审计：(操作名, 对象类别, 对象 ID, 旧值, 新值)。
    SKU 的 prefix 为 SKU 代码加一个空格。"""
    return [
        (
            action,
            target_type,
            target_id,
            prefix + _audit_value(field, old_value),
            prefix + _audit_value(field, new_value),
        )
        for field, old_value, new_value in changes
    ]


def save_product(
    db: Session,
    admin_account_id: int,
    product_id: int,
    version: int,
    product: ProductInput,
    now: datetime,
) -> AdminProduct:
    """保存一件已有商品的全部可改内容，返回保存后的它（同 load_admin_product 的结果）。

    now 与 admin_account_id 同 create_product。新增的规格值与 SKU 带 temp_id，新增 SKU 以行 ID
    引用请求里该规格名下列出的已有规格值、以 temp_id 引用同一规格名下新增的规格值。按以下顺序：
    1. check_product_request(product, creating=False)，另查 version 为整数、规格名都带 id；
       上限与英文名称不重复按现有加新增计算；不通过抛 ProductInvalid、不发任何 SQL。
    2. 结束此前的事务（回滚），在新事务里以 lock_product_statement 锁商品行、再以
       lock_variants_statement 按 id 锁该商品的 SKU 行。商品不存在、version 与当前不同、请求里
       规格名、规格值与 SKU 的行 ID 不恰好是该商品现有的全部行、规格值挂在别的规格名下，抛
       ProductChanged。
    3. 分类与当前不同时以共享锁读新分类，须存在、启用且有英文名称；分类不变而保存为上架时以
       共享锁读所属分类，须启用且有英文名称；新增 SKU 的组合与该商品已有 SKU（含停用的）的
       组合不重复；不通过抛 ProductInvalid。
    4. 按商品（PRODUCT_FIELDS）、规格名、规格值（按规格名再按各自的顺序，NAME_FIELDS）、SKU
       （按 id，VARIANT_FIELDS）逐项与当前值比较，有改动的写新值，每项一条 *_changed 审计（旧值
       与新值为「字段 值」，SKU 的两侧前面加 SKU 代码）。改每日初始库存不改当日可用库存；slug
       与各代码不变。
    5. 插入新增的规格值（排在该规格名已有值之后）与 SKU（当日可用库存等于每日初始库存），
       代码由 generate_value_codes 与 generate_skus 生成，撞上唯一约束抛 ProductChanged；
       之后各写一条 *_created 审计（新值为代码）。
    有改动或新增时版本号加一（一次保存只加一）后提交；都没有时回滚、不加版本、不写审计。
    任何异常都先回滚再抛出。
    """
    checked = _check_save_request(product, version)

    db.rollback()
    try:
        product_row, option_rows, value_rows, variant_rows = _lock_and_match(
            db, product_id, version, checked
        )
        _check_category(db, product_row, checked)
        choices = _new_choices(checked)
        _check_new_combinations(db, product_row.id, choices)

        options_by_id = {option.id: option for option in checked.options}
        values_by_id = {value.id: value for option in checked.options for value in option.values}
        variants_by_id = {variant.id: variant for variant in checked.variants}
        position = {row.id: index for index, row in enumerate(option_rows)}
        value_rows.sort(key=lambda row: (position[row.option_id], row.sort_order, row.id))

        audits = _audit_rows(
            ADMIN_PRODUCT_CHANGED,
            AUDIT_TARGET_PRODUCT,
            product_row.id,
            _update(product_row, checked, PRODUCT_FIELDS),
        )
        for option_row in option_rows:
            audits += _audit_rows(
                ADMIN_PRODUCT_OPTION_CHANGED,
                AUDIT_TARGET_OPTION,
                option_row.id,
                _update(option_row, options_by_id[option_row.id], NAME_FIELDS),
            )
        for value_row in value_rows:
            audits += _audit_rows(
                ADMIN_PRODUCT_OPTION_VALUE_CHANGED,
                AUDIT_TARGET_OPTION_VALUE,
                value_row.id,
                _update(value_row, values_by_id[value_row.id], NAME_FIELDS),
            )
        for variant_row in variant_rows:
            # 只改价格、每日初始库存与启用状态；当日可用库存从下一次每日重置起按新的初始库存。
            audits += _audit_rows(
                ADMIN_PRODUCT_VARIANT_CHANGED,
                AUDIT_TARGET_VARIANT,
                variant_row.id,
                _update(variant_row, variants_by_id[variant_row.id], VARIANT_FIELDS),
                prefix=f"{variant_row.sku} ",
            )
        audits += _insert_additions(db, product_row, option_rows, value_rows, checked, choices)

        saved_id = product_row.id
        if not audits:
            unchanged = load_admin_product(db, saved_id)
            assert unchanged is not None
            db.rollback()
            return unchanged

        product_row.version += 1
        db.flush()
        for action, target_type, target_id, old_text, new_text in audits:
            record_audit(
                db,
                action,
                admin_account_id,
                now,
                target_type=target_type,
                target_id=target_id,
                old_value=old_text,
                new_value=new_text,
            )
        saved = load_admin_product(db, saved_id)
        assert saved is not None
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved

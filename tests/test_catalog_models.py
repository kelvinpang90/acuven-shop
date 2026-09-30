"""商品目录表的数据库约束：价格与库存不为负、SKU 与 slug 唯一、规格关联不跨商品。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行：
「商品、分类、三语文案、图片引用、规格属性、MYR 单价、启用状态；每个规格有 SKU、
管理员设定的每日初始库存及当日可用库存。价格与数量不得为负」。
每条测试的文档字符串引用它守住的那一句。约束写在数据库层，之后的后台维护、
下单预留等写入路径即使漏了校验，也写不出负价格、负库存或张冠李戴的规格。
用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Product,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def _category(session: Session, slug: str) -> Category:
    return _add(session, Category(slug=slug, name_en="Kitchen", is_active=True))


def _product(session: Session, slug: str) -> Product:
    category = _category(session, f"{slug}-category")
    product = Product(category_id=category.id, slug=slug, name_en="Mug", is_active=True)
    return _add(session, product)


def _option(session: Session, product: Product, code: str) -> ProductOption:
    option = ProductOption(product_id=product.id, code=code, sort_order=0, name_en=code)
    return _add(session, option)


def _value(session: Session, option: ProductOption, code: str) -> ProductOptionValue:
    value = ProductOptionValue(
        product_id=option.product_id,
        option_id=option.id,
        code=code,
        sort_order=0,
        name_en=code,
    )
    return _add(session, value)


def _variant(session: Session, product: Product, sku: str, **fields: int) -> ProductVariant:
    values = {"price_sen": 1990, "daily_initial_stock": 10, "available_stock": 10} | fields
    variant = ProductVariant(product_id=product.id, sku=sku, is_active=True, **values)
    return _add(session, variant)


def _link(session: Session, variant: ProductVariant, value: ProductOptionValue) -> None:
    link = VariantOptionValue(
        variant_id=variant.id,
        product_id=variant.product_id,
        option_id=value.option_id,
        option_value_id=value.id,
    )
    _add(session, link)


def test_variant_links_one_value_per_option_of_its_own_product(session: Session) -> None:
    """「规格属性」「每个规格有 SKU」：SKU 在自己商品的每个规格名下关联一个值。
    库存为零（今日售罄）不算负数，可以写入。也是下面各条拒绝测试的对照：
    同样的建表与外键检查下，合法的数据写得进去。
    """
    product = _product(session, "tee")
    color = _option(session, product, "color")
    size = _option(session, product, "size")
    variant = _variant(session, product, "TEE-RED-M", daily_initial_stock=0, available_stock=0)

    _link(session, variant, _value(session, color, "red"))
    _link(session, variant, _value(session, size, "m"))

    assert len(session.scalars(select(VariantOptionValue)).all()) == 2


@pytest.mark.parametrize("field", ["price_sen", "daily_initial_stock", "available_stock"])
def test_negative_price_or_stock_is_rejected(session: Session, field: str) -> None:
    """「价格与数量不得为负」：单价、每日初始库存、当日可用库存各由一个
    显式命名的检查约束拦住，不靠应用层校验。
    """
    product = _product(session, "mug")
    constraint = f"ck_product_variants_{field}_non_negative"

    with pytest.raises(IntegrityError, match=f"CHECK constraint failed: {constraint}"):
        _variant(session, product, "MUG-1", **{field: -1})


def test_duplicate_sku_is_rejected(session: Session) -> None:
    """「每个规格有 SKU」：SKU 全局唯一，另一件商品也不能复用，
    否则按 SKU 找到的规格、价格与库存不确定。
    """
    _variant(session, _product(session, "mug"), "SKU-1")
    other = _product(session, "cup")

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: product_variants.sku"):
        _variant(session, other, "SKU-1")


def test_duplicate_product_slug_is_rejected(session: Session) -> None:
    """「商品」：商品 slug 唯一，一个 slug 只能指向一件商品。"""
    first = _product(session, "mug")
    duplicate = Product(category_id=first.category_id, slug="mug", is_active=True)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: products.slug"):
        _add(session, duplicate)


def test_duplicate_category_slug_is_rejected(session: Session) -> None:
    """「分类」：分类 slug 唯一，一个 slug 只能指向一个分类。"""
    _category(session, "kitchen")

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: categories.slug"):
        _category(session, "kitchen")


def test_variant_cannot_have_two_values_under_one_option(session: Session) -> None:
    """「规格属性」：一个 SKU 在同一规格名下只有一个值（不能既是红又是蓝），
    由唯一约束拦住。
    """
    product = _product(session, "tee")
    color = _option(session, product, "color")
    variant = _variant(session, product, "TEE-RED")
    _link(session, variant, _value(session, color, "red"))
    blue = _value(session, color, "blue")
    message = (
        "UNIQUE constraint failed: "
        "variant_option_values.variant_id, variant_option_values.option_id"
    )

    with pytest.raises(IntegrityError, match=message):
        _link(session, variant, blue)


def test_variant_cannot_link_another_products_value(session: Session) -> None:
    """「每个规格有 SKU」「规格属性」：SKU 的规格值只能取自同一件商品，
    复合外键拦住张冠李戴。
    """
    tee = _product(session, "tee")
    variant = _variant(session, tee, "TEE-1")
    cap = _product(session, "cap")
    cap_red = _value(session, _option(session, cap, "color"), "red")
    link = VariantOptionValue(
        variant_id=variant.id,
        product_id=tee.id,
        option_id=cap_red.option_id,
        option_value_id=cap_red.id,
    )

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, link)


def test_value_must_belong_to_the_linked_option(session: Session) -> None:
    """「规格属性」：关联行里的规格值必须属于它所填的规格名，
    否则同一规格名下至多一个值的唯一约束会被绕过。
    """
    product = _product(session, "tee")
    color = _option(session, product, "color")
    size = _option(session, product, "size")
    red = _value(session, color, "red")
    variant = _variant(session, product, "TEE-1")
    link = VariantOptionValue(
        variant_id=variant.id,
        product_id=product.id,
        option_id=size.id,
        option_value_id=red.id,
    )

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, link)

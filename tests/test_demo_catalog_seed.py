"""示例商品目录种子数据：迁移 0003 实际写入的行，以及随仓库发布的占位图。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行与
SHOP-TASK-006 的验收标准。每条测试的文档字符串引用它守住的那一句规则。
用 SQLite 内存库（每个连接打开外键检查）按模型建表，通过 Alembic 的 Operations
执行迁移文件里的 upgrade / downgrade，再查询实际写入的行，而不是只看迁移里的常量。
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.services.catalog import list_products

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20260930_0003_demo_catalog.py"
PUBLIC_DIR = ROOT / "frontend" / "public"
IMAGE_NAMES = ["apparel", "bags", "home", "kitchen", "stationery", "gadgets"]
SLUG_PATTERN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
# 纯几何图形：没有 text、style、script、foreignObject、image、use 这类元素。
SVG_SHAPES = {"svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path"}


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("demo_catalog_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        Base.metadata.create_all(conn)
        yield conn
    engine.dispose()


def _run(conn: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(MIGRATION, step)()


@pytest.fixture
def session(connection: Connection) -> Iterator[Session]:
    _run(connection, "upgrade")
    with Session(bind=connection) as db:
        yield db


def _all[T](session: Session, model: type[T]) -> list[T]:
    return list(session.scalars(select(model)))


def _present(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _texts(row: object, field: str) -> list[str | None]:
    return [getattr(row, f"{field}_{lang}") for lang in ("en", "zh", "ms")]


@pytest.fixture
def catalog(session: Session) -> dict:
    """实际写入的行，按商品归组。"""
    options: dict[int, list[ProductOption]] = {}
    for option in _all(session, ProductOption):
        options.setdefault(option.product_id, []).append(option)
    values: dict[int, list[ProductOptionValue]] = {}
    for value in _all(session, ProductOptionValue):
        values.setdefault(value.option_id, []).append(value)
    variants: dict[int, list[ProductVariant]] = {}
    for variant in _all(session, ProductVariant):
        variants.setdefault(variant.product_id, []).append(variant)
    links: dict[int, list[VariantOptionValue]] = {}
    for link in _all(session, VariantOptionValue):
        links.setdefault(link.variant_id, []).append(link)
    return {
        "categories": {c.id: c for c in _all(session, Category)},
        "products": _all(session, Product),
        "options": options,
        "values": values,
        "variants": variants,
        "links": links,
    }


def test_six_active_categories_in_order(session: Session) -> None:
    """「6 个启用的分类，slug 依次为 apparel、bags、home、kitchen、stationery、gadgets」。"""
    categories = session.scalars(select(Category).order_by(Category.id)).all()

    assert [c.slug for c in categories] == IMAGE_NAMES
    assert all(c.is_active is True for c in categories)


def test_product_count_and_per_category_minimum(catalog: dict) -> None:
    """「共 28 到 32 件启用的商品，每个分类至少 4 件」。"""
    products = catalog["products"]
    per_category = Counter(catalog["categories"][p.category_id].slug for p in products)

    assert 28 <= len(products) <= 32
    assert all(p.is_active is True for p in products)
    assert set(per_category) == set(IMAGE_NAMES)
    assert min(per_category.values()) >= 4


def test_slugs_and_skus_are_unique_deterministic_lowercase(session: Session) -> None:
    """「slug、SKU 全部唯一、确定、小写」：写入的正是迁移常量里的那一批，
    都是小写字母、数字与连字符，不重复。
    """
    category_slugs = list(session.scalars(select(Category.slug)))
    product_slugs = list(session.scalars(select(Product.slug)))
    skus = list(session.scalars(select(ProductVariant.sku)))

    for names, expected in [
        (category_slugs, MIGRATION.CATEGORY_SLUGS),
        (product_slugs, MIGRATION.PRODUCT_SLUGS),
        (skus, MIGRATION.SKUS),
    ]:
        assert len(set(names)) == len(names)
        assert sorted(names) == sorted(expected)
        assert all(SLUG_PATTERN.fullmatch(name) for name in names)


def test_english_text_is_complete(catalog: dict, session: Session) -> None:
    """「每件商品与分类都有英文名称，商品都有英文描述，所有规格名与规格值都有英文名称，
    满足发布规则」。
    """
    assert all(_present(c.name_en) for c in catalog["categories"].values())
    assert all(_present(p.name_en) and _present(p.description_en) for p in catalog["products"])
    assert all(_present(o.name_en) for o in _all(session, ProductOption))
    assert all(_present(v.name_en) for v in _all(session, ProductOptionValue))


def test_exactly_one_product_is_english_only(catalog: dict, session: Session) -> None:
    """「除刻意的一件外，中文与马来文文案齐全；刻意的那一件只有英文」：
    分类、规格名、规格值的中文与马来文全部齐全；恰好一件商品的名称与描述都缺中文和马来文。
    """
    rows = [*catalog["categories"].values(), *_all(session, ProductOption)]
    rows += _all(session, ProductOptionValue)
    assert all(_present(row.name_zh) and _present(row.name_ms) for row in rows)

    def local(product: Product) -> list[str | None]:
        return [*_texts(product, "name")[1:], *_texts(product, "description")[1:]]

    partial = [p for p in catalog["products"] if not all(_present(t) for t in local(p))]
    assert len(partial) == 1
    assert not any(_present(t) for t in local(partial[0]))


def test_text_has_no_web_address_or_contact(catalog: dict, session: Session) -> None:
    """「不出现真实品牌、商标、真实人物、联系方式或任何网址」：机器能查的部分——
    文案里没有网址、邮箱或电话号码样的长串数字。品牌与人物由人工审阅。
    """
    texts = [t for c in catalog["categories"].values() for t in _texts(c, "name")]
    for product in catalog["products"]:
        texts += _texts(product, "name") + _texts(product, "description")
    for row in [*_all(session, ProductOption), *_all(session, ProductOptionValue)]:
        texts += _texts(row, "name")

    for text in filter(None, texts):
        assert not re.search(r"://|www\.|@|\.com\b|\d{7,}", text), text


def test_variant_shapes_cover_none_single_and_double(catalog: dict) -> None:
    """「规格形态三种都有：无规格（一个 SKU）、单规格、双规格，每种至少 3 件商品」：
    每个 SKU 在自己商品的每个规格名下恰好关联一个值，组合不重复，每个规格值都有 SKU 用到。
    """
    shapes = Counter()
    for product in catalog["products"]:
        options = catalog["options"].get(product.id, [])
        variants = catalog["variants"][product.id]
        shapes[len(options)] += 1
        if not options:
            assert len(variants) == 1
        combos = set()
        for variant in variants:
            links = catalog["links"].get(variant.id, [])
            assert sorted(link.option_id for link in links) == sorted(o.id for o in options)
            combos.add(frozenset(link.option_value_id for link in links))
        assert len(combos) == len(variants)
        used = set().union(*combos)
        for option in options:
            assert {v.id for v in catalog["values"][option.id]} <= used

    assert set(shapes) == {0, 1, 2}
    assert min(shapes.values()) >= 3


def test_double_option_products_miss_a_combination(catalog: dict, session: Session) -> None:
    """「双规格商品至少有一个颜色与尺寸组合没有 SKU，用来演示筛选须由同一个 SKU 同时满足」：
    缺的组合里两个值各自都能筛出这件商品，同时筛两个值时它不出现。
    """
    doubles = [p for p in catalog["products"] if len(catalog["options"].get(p.id, [])) == 2]
    assert len(doubles) >= 3

    for product in doubles:
        color, size = sorted(catalog["options"][product.id], key=lambda o: o.sort_order)
        assert (color.code, size.code) == ("color", "size")
        code = {v.id: v.code for o in (color, size) for v in catalog["values"][o.id]}
        combos = {
            tuple(sorted(code[link.option_value_id] for link in catalog["links"][variant.id]))
            for variant in catalog["variants"][product.id]
        }
        missing = [
            (c, s)
            for c in (v.code for v in catalog["values"][color.id])
            for s in (v.code for v in catalog["values"][size.id])
            if tuple(sorted((c, s))) not in combos
        ]
        assert missing, product.slug

        c, s = missing[0]
        for filters, expected in [
            ({"color": [c]}, True),
            ({"size": [s]}, True),
            ({"color": [c], "size": [s]}, False),
        ]:
            page = list_products(session, lang="en", options=filters, page_size=48)
            assert (product.slug in {item.slug for item in page.items}) is expected


def test_prices_are_integer_sen_within_range(session: Session) -> None:
    """「单价是整数仙，范围 RM5 到 RM300，不全相同」。"""
    prices = list(session.scalars(select(ProductVariant.price_sen)))

    assert all(type(price) is int for price in prices)
    assert all(500 <= price <= 30000 for price in prices)
    assert len(set(prices)) > 1


def test_available_stock_equals_daily_initial_stock(session: Session) -> None:
    """「每个 SKU 的当日可用库存等于每日初始库存」，且都启用。"""
    variants = _all(session, ProductVariant)

    assert all(v.available_stock == v.daily_initial_stock for v in variants)
    assert all(v.is_active is True for v in variants)


def test_one_product_is_sold_out_today(catalog: dict) -> None:
    """「至少一件商品所有 SKU 的库存都为 0，用来演示今日售罄」；其余商品不会全部售罄。"""
    sold_out = [
        p
        for p in catalog["products"]
        if all(v.available_stock == 0 for v in catalog["variants"][p.id])
    ]

    assert 1 <= len(sold_out) < len(catalog["products"])


def test_created_at_is_distinct_naive_utc(catalog: dict) -> None:
    """「商品创建时间各不相同、确定，且按 SHOP-TASK-004 的约定一律是 UTC」：
    存不带时区的 UTC，且都早于迁移日期，按最新排序时不会有来自未来的商品。
    """
    created = [p.created_at for p in catalog["products"]]

    assert len(set(created)) == len(created)
    assert all(t.tzinfo is None for t in created)
    assert max(created) < datetime(2026, 9, 30)


def test_each_product_has_one_image_of_its_category(catalog: dict, session: Session) -> None:
    """「每件商品有且只有一张图片，引用写成以斜杠开头的站点根路径，指向所属分类的占位图」，
    且那张占位图文件真的在 frontend/public 下。
    """
    images = Counter()
    for image in _all(session, ProductImage):
        images[image.product_id] += 1
        product = session.get(Product, image.product_id)
        slug = catalog["categories"][product.category_id].slug
        assert image.storage_ref == f"/demo-images/{slug}.svg"
        assert (PUBLIC_DIR / image.storage_ref.lstrip("/")).is_file()

    assert images == Counter({p.id: 1 for p in catalog["products"]})


def test_all_products_are_published_and_one_falls_back_to_english(
    catalog: dict, session: Session
) -> None:
    """「满足发布规则」：目录查询按发布规则能列出每一件；中文界面里恰好一件回退英文，
    演示「仅英文」标签。
    """
    total = len(catalog["products"])

    english = list_products(session, lang="en", page_size=48)
    chinese = list_products(session, lang="zh", page_size=48)

    assert english.total == total
    assert chinese.total == total
    assert sum(item.name.english_fallback for item in chinese.items) == 1


def test_downgrade_removes_seed_rows_and_keeps_others(
    session: Session, connection: Connection
) -> None:
    """「downgrade 按 slug 与 SKU 删除这批数据及其图片、规格与关联行」：
    示例数据的每张表都删干净，别的分类与商品原样保留。
    """
    other = Category(slug="other", name_en="Other", is_active=True)
    session.add(other)
    session.flush()
    product = Product(category_id=other.id, slug="other-item", name_en="Item", is_active=True)
    session.add(product)
    session.flush()
    session.add(ProductImage(product_id=product.id, storage_ref="/other.svg", sort_order=0))
    session.add(
        ProductVariant(
            product_id=product.id,
            sku="other-item-std",
            price_sen=1000,
            daily_initial_stock=1,
            available_stock=1,
            is_active=True,
        )
    )
    session.flush()

    _run(connection, "downgrade")
    session.expire_all()

    assert list(session.scalars(select(Category.slug))) == ["other"]
    assert list(session.scalars(select(Product.slug))) == ["other-item"]
    assert list(session.scalars(select(ProductVariant.sku))) == ["other-item-std"]
    assert list(session.scalars(select(ProductImage.storage_ref))) == ["/other.svg"]
    for model in (ProductOption, ProductOptionValue, VariantOptionValue):
        assert _all(session, model) == []


@pytest.mark.parametrize("name", IMAGE_NAMES)
def test_placeholder_svg_is_small_static_geometry(name: str) -> None:
    """「纯静态几何图形、不含文字，不含 script、foreignObject、事件属性、外链 href、
    CSS 的 url() 或 @import，每张不超过 4 KB」。
    """
    raw = (PUBLIC_DIR / "demo-images" / f"{name}.svg").read_bytes()
    assert len(raw) <= 4096

    lowered = raw.decode("utf-8").lower()
    for forbidden in ("<script", "foreignobject", "href", "url(", "@import", "<style", "<text"):
        assert forbidden not in lowered, forbidden

    root = ElementTree.fromstring(raw)
    assert root.tag == f"{SVG_NAMESPACE}svg"
    for element in root.iter():
        assert element.tag.startswith(SVG_NAMESPACE), element.tag
        assert element.tag.removeprefix(SVG_NAMESPACE) in SVG_SHAPES, element.tag
        assert not (element.text or "").strip()
        assert not (element.tail or "").strip()
        for attribute in element.attrib:
            local = attribute.rsplit("}", 1)[-1].lower()
            assert not local.startswith("on"), attribute
            assert local != "style", attribute

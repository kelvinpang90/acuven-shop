"""商品目录只读接口：发布规则、回退英文、最低单价与售罄、搜索、筛选、排序、分页。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行：
「文案缺少当前语言时回退英文，再缺失则商品不发布」，以及 SHOP-TASK-005 的验收标准。
每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库按模型建表，接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.services.catalog import MAX_PAGE_SIZE

PATHS = ["/api/catalog/categories", "/api/catalog/products", "/api/catalog/products/tee"]


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app(Settings(_env_file=None))
    # 接口用测试自己的会话：flush 过的数据就看得到，不必提交。
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _category(db: Session, slug: str, **fields: object) -> Category:
    values = {"name_en": "Kitchen", "is_active": True} | fields
    return _add(db, Category(slug=slug, **values))


def _product(
    db: Session,
    slug: str,
    *,
    category: Category | None = None,
    **fields: object,
) -> Product:
    category = category or _category(db, f"{slug}-category")
    defaults = {
        "name_en": slug.title(),
        "description_en": f"The {slug}.",
        "is_active": True,
        "created_at": datetime(2026, 9, 1),
    }
    return _add(db, Product(category_id=category.id, slug=slug, **(defaults | fields)))


def _option(db: Session, product: Product, code: str, *codes: str) -> list[ProductOptionValue]:
    """规格名及其规格值；英文名称取 code 的首字母大写，中文与马来文留空。"""
    option = ProductOption(product_id=product.id, code=code, sort_order=0, name_en=code.title())
    _add(db, option)
    values = []
    for sort_order, value_code in enumerate(codes):
        value = ProductOptionValue(
            product_id=product.id,
            option_id=option.id,
            code=value_code,
            sort_order=sort_order,
            name_en=value_code.title(),
        )
        values.append(_add(db, value))
    return values


def _variant(
    db: Session,
    product: Product,
    sku: str,
    *values: ProductOptionValue,
    **fields: object,
) -> ProductVariant:
    defaults = {
        "price_sen": 1990,
        "daily_initial_stock": 10,
        "available_stock": 10,
        "is_active": True,
    }
    variant = _add(db, ProductVariant(product_id=product.id, sku=sku, **(defaults | fields)))
    for value in values:
        link = VariantOptionValue(
            variant_id=variant.id,
            product_id=product.id,
            option_id=value.option_id,
            option_value_id=value.id,
        )
        _add(db, link)
    return variant


def _listed(db: Session, slug: str, **fields: object) -> Product:
    """最小的可发布商品：英文文案齐全，一个启用的 SKU，没有规格名。"""
    product = _product(db, slug, **fields)
    _variant(db, product, f"{slug.upper()}-1")
    return product


@dataclass
class _Tee:
    category: Category
    product: Product
    option: ProductOption
    value: ProductOptionValue
    variant: ProductVariant


def _tee(db: Session) -> _Tee:
    """一件可发布的商品：一个规格名、一个规格值、一个启用的 SKU。"""
    category = _category(db, "apparel", name_en="Apparel")
    product = _product(db, "tee", category=category)
    (value,) = _option(db, product, "color", "red")
    variant = _variant(db, product, "TEE-RED", value)
    return _Tee(category, product, db.get(ProductOption, value.option_id), value, variant)


def _text(text: str, *, fallback: bool = False) -> dict[str, object]:
    return {"text": text, "english_fallback": fallback}


def _slugs(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/catalog/products", params=params)
    assert response.status_code == 200, response.text
    return [item["slug"] for item in response.json()["items"]]


def test_published_product_has_exactly_the_public_fields(db: Session, client: TestClient) -> None:
    """发布规则的对照：启用、英文齐全、有启用 SKU 的商品出现在列表与详情里；
    「列表每项给出 slug、名称、分类、首张图片引用、最低单价、今日是否售罄」；
    「详情给出按序的图片引用、规格名与规格值、每个启用 SKU 的 SKU、所选规格值、
    单价与当日可用库存」；「响应只含 MYR 整数仙，不含参考外币、每日初始库存、
    启用状态等后台字段」——整体相等，多一个字段就失败。
    不带 lang 即英文（「默认 en」），英文字段都不标回退。
    """
    tee = _product(db, "tee")
    red, blue = _option(db, tee, "color", "red", "blue")
    _add(db, ProductImage(product_id=tee.id, storage_ref="img/tee-2.jpg", sort_order=2))
    _add(db, ProductImage(product_id=tee.id, storage_ref="img/tee-1.jpg", sort_order=1))
    _variant(db, tee, "TEE-RED", red, price_sen=2590, available_stock=3)
    _variant(db, tee, "TEE-BLUE", blue, price_sen=2190, available_stock=0)
    category = {"slug": "tee-category", "name": _text("Kitchen")}

    listing = client.get("/api/catalog/products").json()
    detail = client.get("/api/catalog/products/tee").json()

    expected_item = {
        "slug": "tee",
        "name": _text("Tee"),
        "category": category,
        "image": "img/tee-1.jpg",
        "min_price_sen": 2190,
        "has_multiple_variants": True,
        "sold_out_today": False,
    }
    assert listing == {"total": 1, "page": 1, "page_size": 24, "items": [expected_item]}
    assert detail == {
        "slug": "tee",
        "name": _text("Tee"),
        "description": _text("The tee."),
        "category": category,
        "images": ["img/tee-1.jpg", "img/tee-2.jpg"],
        "options": [
            {
                "code": "color",
                "name": _text("Color"),
                "values": [
                    {"code": "red", "name": _text("Red")},
                    {"code": "blue", "name": _text("Blue")},
                ],
            },
        ],
        "variants": [
            {
                "sku": "TEE-RED",
                "options": {"color": "red"},
                "price_sen": 2590,
                "available_stock": 3,
            },
            {
                "sku": "TEE-BLUE",
                "options": {"color": "blue"},
                "price_sen": 2190,
                "available_stock": 0,
            },
        ],
        "max_per_order": 10,
    }


@pytest.mark.parametrize(
    ("owner", "field", "missing"),
    [
        ("product", "is_active", False),
        ("category", "is_active", False),
        ("category", "name_en", None),
        ("product", "name_en", "  "),
        ("product", "description_en", None),
        ("option", "name_en", None),
        ("value", "name_en", ""),
        ("variant", "is_active", False),
    ],
)
def test_each_publish_condition_hides_the_product_like_an_unknown_slug(
    db: Session,
    client: TestClient,
    owner: str,
    field: str,
    missing: object,
) -> None:
    """发布规则的每个条件：「商品与所属分类都启用，分类英文名称、商品英文名称与英文描述、
    该商品所有规格名与规格值的英文名称齐全，且至少有一个启用的 SKU，才出现在列表与详情里；
    否则详情返回 404，与不存在的 slug 同一响应」。空串与只有空格的文案同缺失。
    先确认改动前商品是发布的，免得测试因别的原因碰巧通过。
    """
    tee = _tee(db)
    assert _slugs(client) == ["tee"]

    setattr(getattr(tee, owner), field, missing)
    db.flush()

    listing = client.get("/api/catalog/products").json()
    hidden = client.get("/api/catalog/products/tee")
    unknown = client.get("/api/catalog/products/no-such-product")
    assert (listing["total"], listing["items"]) == (0, [])
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()


def test_category_list_has_only_active_categories_with_an_english_name(
    db: Session,
    client: TestClient,
) -> None:
    """「分类列表只返回启用且有英文名称的分类」；分类名按请求语言返回，缺少时回退英文并标出。"""
    _category(db, "kitchen", name_zh="厨房")
    _category(db, "garden", name_en="Garden")
    _category(db, "hidden", is_active=False)
    _category(db, "unnamed", name_en=None, name_zh="无英文")

    response = client.get("/api/catalog/categories", params={"lang": "zh"})

    assert response.json() == [
        {"slug": "kitchen", "name": _text("厨房"), "image": None},
        {"slug": "garden", "name": _text("Garden", fallback=True), "image": None},
    ]


def test_missing_translation_falls_back_to_english_and_is_flagged(
    db: Session,
    client: TestClient,
) -> None:
    """「文案按请求语言返回，缺少时回退英文，并标出哪些字段回退了英文」：
    有该语言文案的字段给该语言且不标；缺的字段给英文并标 english_fallback，逐字段独立。
    """
    category = _category(db, "kitchen", name_zh="厨房")
    mug = _product(db, "mug", category=category, name_zh="马克杯", description_ms="Cawan.")
    (red,) = _option(db, mug, "color", "red")
    red.name_zh = "红色"
    _variant(db, mug, "MUG-RED", red)

    zh = client.get("/api/catalog/products/mug", params={"lang": "zh"}).json()
    ms = client.get("/api/catalog/products/mug", params={"lang": "ms"}).json()
    item = client.get("/api/catalog/products", params={"lang": "zh"}).json()["items"][0]

    assert zh["name"] == _text("马克杯")
    assert zh["description"] == _text("The mug.", fallback=True)
    assert zh["category"]["name"] == _text("厨房")
    assert zh["options"][0]["name"] == _text("Color", fallback=True)
    assert zh["options"][0]["values"][0]["name"] == _text("红色")
    assert item["name"] == _text("马克杯")
    assert item["category"]["name"] == _text("厨房")
    assert ms["name"] == _text("Mug", fallback=True)
    assert ms["description"] == _text("Cawan.")
    assert ms["category"]["name"] == _text("Kitchen", fallback=True)


def test_lowest_active_price_and_sold_out_only_count_active_skus(
    db: Session,
    client: TestClient,
) -> None:
    """「启用 SKU 中的最低单价（整数仙）」「今日是否售罄（所有启用 SKU 当日可用库存均为 0）」
    「详情给出每个启用 SKU」：停用的 SKU 再便宜、再有库存，也不影响最低单价与售罄，
    也不出现在详情里。
    """
    sold_out = _product(db, "sold-out", created_at=datetime(2026, 9, 2))
    _variant(db, sold_out, "SO-1", price_sen=2500, available_stock=0)
    _variant(db, sold_out, "SO-2", price_sen=1500, available_stock=0)
    _variant(db, sold_out, "SO-OFF", price_sen=500, available_stock=9, is_active=False)
    in_stock = _product(db, "in-stock")
    _variant(db, in_stock, "IS-1", price_sen=3000, available_stock=0)
    _variant(db, in_stock, "IS-2", price_sen=3500, available_stock=1)

    items = client.get("/api/catalog/products").json()["items"]
    detail = client.get("/api/catalog/products/sold-out").json()

    summary = [(i["slug"], i["min_price_sen"], i["sold_out_today"]) for i in items]
    assert summary == [("sold-out", 1500, True), ("in-stock", 3000, False)]
    assert [variant["sku"] for variant in detail["variants"]] == ["SO-1", "SO-2"]


def test_search_matches_current_language_or_english_name_case_insensitively(
    db: Session,
    client: TestClient,
) -> None:
    """「搜索按当前语言名称或英文名称做不区分大小写的包含匹配」：
    中文界面下中文名与英文名都搜得到；英文界面不按中文名匹配；% 按字面匹配，不是通配符。
    """
    _listed(db, "mug", name_en="Travel Mug", name_zh="旅行杯")
    _listed(db, "plate", name_en="Dinner Plate", name_zh="餐盘")
    _listed(db, "promo", name_en="Mug 50% Off")

    assert _slugs(client, lang="zh", q="旅行") == ["mug"]
    assert _slugs(client, lang="zh", q="dinner PLATE") == ["plate"]
    assert _slugs(client, lang="en", q="旅行") == []
    assert _slugs(client, q="mUG") == ["mug", "promo"]
    assert _slugs(client, q="%") == ["promo"]


def test_category_filter_is_or_between_categories(db: Session, client: TestClient) -> None:
    """「按分类筛选」：所选的多个分类之间为或，不在所选分类的商品不出现。"""
    _listed(db, "mug", category=_category(db, "kitchen"))
    _listed(db, "rake", category=_category(db, "garden", name_en="Garden"))
    _listed(db, "tee", category=_category(db, "apparel", name_en="Apparel"))

    assert _slugs(client, category="kitchen") == ["mug"]
    assert _slugs(client, category=["kitchen", "garden"]) == ["mug", "rake"]


def test_values_under_one_option_are_or(db: Session, client: TestClient) -> None:
    """「同一规格名下多个值为或」：选红与蓝时，红的和蓝的商品都出现，只有绿的不出现。"""
    for slug, color in [("red-tee", "red"), ("blue-tee", "blue"), ("green-tee", "green")]:
        product = _product(db, slug)
        _variant(db, product, slug.upper(), *_option(db, product, "color", color))

    assert _slugs(client, option=["color:red", "color:blue"]) == ["red-tee", "blue-tee"]


def test_different_options_are_and_and_must_be_met_by_one_active_sku(
    db: Session,
    client: TestClient,
) -> None:
    """「不同规格名之间为且，且须由同一个启用 SKU 同时满足」。
    反例：split 的颜色只由 SKU-A 命中、尺寸只由 SKU-B 命中，该商品不出现；
    off 唯一同时是红色与 M 的 SKU 已停用，也不出现。
    """
    split = _product(db, "split")
    red, blue = _option(db, split, "color", "red", "blue")
    small, medium = _option(db, split, "size", "s", "m")
    _variant(db, split, "SKU-A", red, small)
    _variant(db, split, "SKU-B", blue, medium)

    off = _product(db, "off")
    off_red, off_blue = _option(db, off, "color", "red", "blue")
    off_small, off_medium = _option(db, off, "size", "s", "m")
    _variant(db, off, "OFF-RED-M", off_red, off_medium, is_active=False)
    _variant(db, off, "OFF-BLUE-S", off_blue, off_small)

    match = _product(db, "match")
    (match_red,) = _option(db, match, "color", "red")
    (match_medium,) = _option(db, match, "size", "m")
    _variant(db, match, "MATCH-RED-M", match_red, match_medium)

    assert _slugs(client, option=["color:red", "size:m"]) == ["match"]
    assert _slugs(client, option=["color:red"]) == ["split", "match"]
    assert _slugs(client, option=["color:red", "color:blue", "size:m"]) == ["split", "match"]


def test_three_sort_orders_use_lowest_price_and_break_ties_by_product_id(
    db: Session,
    client: TestClient,
) -> None:
    """「排序只接受最新、价格从低到高、价格从高到低（价格按最低单价），平手按商品 id 保持稳定」。
    a 的最高单价最贵：若误按最高单价排序，价格从高到低会把 a 排第一。其他排序值被拒。
    """
    rows = [
        ("a", datetime(2026, 9, 1), 1000, 9000),
        ("b", datetime(2026, 9, 3), 2000, 2100),
        ("c", datetime(2026, 9, 3), 1000, 1100),
        ("d", datetime(2026, 9, 1), 2000, 2100),
    ]
    for slug, created_at, cheap, dear in rows:
        product = _product(db, slug, created_at=created_at)
        _variant(db, product, f"{slug}-cheap", price_sen=cheap)
        _variant(db, product, f"{slug}-dear", price_sen=dear)

    assert _slugs(client) == ["b", "c", "a", "d"]
    assert _slugs(client, sort="newest") == ["b", "c", "a", "d"]
    assert _slugs(client, sort="price_asc") == ["a", "c", "b", "d"]
    assert _slugs(client, sort="price_desc") == ["b", "d", "a", "c"]
    assert client.get("/api/catalog/products", params={"sort": "name"}).status_code == 422


def test_pagination_reports_total_and_caps_page_size(db: Session, client: TestClient) -> None:
    """「分页返回总数，每页条数有上限」：总数是全部命中数，不是本页条数；超过上限被拒。"""
    for day in range(1, 6):
        _listed(db, f"p{day}", created_at=datetime(2026, 9, day))

    first = client.get("/api/catalog/products", params={"page_size": 2}).json()
    last = client.get("/api/catalog/products", params={"page_size": 2, "page": 3}).json()
    too_big = client.get("/api/catalog/products", params={"page_size": MAX_PAGE_SIZE + 1})

    assert (first["total"], first["page"], first["page_size"]) == (5, 1, 2)
    assert [item["slug"] for item in first["items"]] == ["p5", "p4"]
    assert (last["total"], [item["slug"] for item in last["items"]]) == (5, ["p1"])
    assert too_big.status_code == 422


@pytest.mark.parametrize("path", PATHS)
def test_language_outside_en_zh_ms_is_rejected(
    db: Session,
    client: TestClient,
    path: str,
) -> None:
    """「语言参数只接受 en、zh、ms」：其他值（含大写的 EN）被拒，而不是悄悄当成英文。"""
    _tee(db)

    assert client.get(path).status_code == 200
    for lang in ["fr", "EN"]:
        assert client.get(path, params={"lang": lang}).status_code == 422


def test_malformed_option_filter_is_rejected(db: Session, client: TestClient) -> None:
    """筛选参数写作 <规格名>:<规格值>；缺一半时拒绝，而不是丢掉条件返回全部商品。"""
    _tee(db)

    for option in ["color", "color:", ":red"]:
        response = client.get("/api/catalog/products", params={"option": option})
        assert response.status_code == 422


def test_catalog_only_reads(db: Session, client: TestClient) -> None:
    """「只有 GET 接口」「接口不写数据库」：三个接口发出的语句全是 SELECT；写方法得到 405。"""
    _tee(db)
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    for path in PATHS:
        assert client.get(path).status_code == 200
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_app_starts_without_a_database_and_catalog_answers_503() -> None:
    """「未配置数据库时应用照常启动，健康检查不受影响」；目录接口明确回答 503，不给空目录。"""
    client = TestClient(create_app(Settings(_env_file=None, database_url="")))

    assert client.get("/api/healthz").status_code == 200
    assert client.get("/api/catalog/products").status_code == 503

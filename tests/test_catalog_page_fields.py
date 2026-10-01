"""商品目录为首页 P01 与商品列表 P02 补充的只读字段：多规格标记、分类图片、规格筛选项。

依据 docs/REQUIREMENTS.md 1.10「访客与会员流程」第 1 条（商品有多于一个启用规格时一律显示
最低单价加「起」，即使各规格同价）、docs/UX.md 0.5 的 P01「按分类浏览」与 P02 规格筛选栏，
以及 SHOP-TASK-013 的验收标准。每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库按模型建表，接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

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

OPTIONS_PATH = "/api/catalog/options"


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
    values = {"name_en": slug.title(), "is_active": True} | fields
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


def _image(db: Session, product: Product, ref: str, sort_order: int = 0) -> None:
    _add(db, ProductImage(product_id=product.id, storage_ref=ref, sort_order=sort_order))


def _option_name(
    db: Session,
    product: Product,
    code: str,
    sort_order: int = 0,
    **names: object,
) -> ProductOption:
    """只建规格名；英文名称默认取 code 的首字母大写。"""
    values = {"name_en": code.title()} | names
    row = ProductOption(product_id=product.id, code=code, sort_order=sort_order, **values)
    return _add(db, row)


def _value(
    db: Session,
    option: ProductOption,
    code: str,
    sort_order: int = 0,
    **names: object,
) -> ProductOptionValue:
    values = {"name_en": code.title()} | names
    row = ProductOptionValue(
        product_id=option.product_id,
        option_id=option.id,
        code=code,
        sort_order=sort_order,
        **values,
    )
    return _add(db, row)


def _option(db: Session, product: Product, code: str, *codes: str) -> list[ProductOptionValue]:
    """规格名及其规格值，排列序号按给出的顺序；英文名称取 code 的首字母大写。"""
    option = _option_name(db, product, code)
    return [_value(db, option, value_code, index) for index, value_code in enumerate(codes)]


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


def _text(text: str, *, fallback: bool = False) -> dict[str, object]:
    return {"text": text, "english_fallback": fallback}


def _filter_options(client: TestClient, **params: object) -> list[dict[str, Any]]:
    response = client.get(OPTIONS_PATH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _codes(options: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    return [(o["code"], [v["code"] for v in o["values"]]) for o in options]


def _slugs(client: TestClient, **params: object) -> list[str]:
    response = client.get("/api/catalog/products", params=params)
    assert response.status_code == 200, response.text
    return [item["slug"] for item in response.json()["items"]]


# ---------- 商品列表：has_multiple_variants ----------


def test_has_multiple_variants_counts_active_skus_even_at_the_same_price(
    db: Session,
    client: TestClient,
) -> None:
    """REQUIREMENTS 1.10 第 1 条「有多于一个启用规格时一律显示最低单价加『起』，即使各规格同价」；
    验收标准「启用的 SKU 多于一个时为真（即使各 SKU 同价），只有一个启用 SKU 时为假，
    停用的 SKU 不计入」。三种情况：一个启用 SKU；两个同价启用 SKU；两个 SKU 其中一个停用。
    """
    single = _product(db, "single")
    _variant(db, single, "SINGLE-1")
    same_price = _product(db, "same-price")
    _variant(db, same_price, "SAME-1", price_sen=2500)
    _variant(db, same_price, "SAME-2", price_sen=2500)
    one_off = _product(db, "one-off")
    _variant(db, one_off, "ONE-ON", price_sen=2500)
    _variant(db, one_off, "ONE-OFF", price_sen=900, is_active=False)

    items = client.get("/api/catalog/products").json()["items"]

    flags = {item["slug"]: item["has_multiple_variants"] for item in items}
    assert flags == {"single": False, "same-price": True, "one-off": False}
    # 同价也为真：最低单价与单个 SKU 的单价相同，「起」只看 SKU 个数。
    prices = {item["slug"]: item["min_price_sen"] for item in items}
    assert prices == {"single": 1990, "same-price": 2500, "one-off": 2500}


# ---------- 分类列表：image ----------


def test_category_image_is_the_first_image_of_the_newest_published_product(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「分类列表接口每项新增 image：该分类下按商品列表 newest 排序排在第一的
    已发布商品的首张图片引用（取法与商品摘要的 image 相同），该分类没有已发布商品或
    该商品没有图片时为空」；「分类列表的过滤条件与顺序不变」。
    kitchen：更新的商品未发布，不参与；取已发布中最新那件排列序号最小的图片。
    tie：创建时间相同按商品 id 升序（同 newest 的平手规则）。
    bare：最新的已发布商品没有图片时为空，不改取较旧商品的图片。
    garden：只有未发布商品（唯一 SKU 停用），为空。empty：没有商品，为空。
    hidden：停用的分类不出现。
    """
    kitchen = _category(db, "kitchen")
    old = _product(db, "old-mug", category=kitchen, created_at=datetime(2026, 9, 1))
    _variant(db, old, "OLD-1")
    _image(db, old, "img/old.jpg")
    new = _product(db, "new-mug", category=kitchen, created_at=datetime(2026, 9, 2))
    _variant(db, new, "NEW-1")
    _image(db, new, "img/new-2.jpg", sort_order=2)
    _image(db, new, "img/new-1.jpg", sort_order=1)
    draft = _product(
        db, "draft-mug", category=kitchen, created_at=datetime(2026, 9, 3), is_active=False
    )
    _variant(db, draft, "DRAFT-1")
    _image(db, draft, "img/draft.jpg")

    tie = _category(db, "tie")
    for slug in ["tie-a", "tie-b"]:
        product = _product(db, slug, category=tie, created_at=datetime(2026, 9, 5))
        _variant(db, product, f"{slug.upper()}-1")
        _image(db, product, f"img/{slug}.jpg")

    bare = _category(db, "bare")
    pictured = _product(db, "pictured", category=bare, created_at=datetime(2026, 9, 1))
    _variant(db, pictured, "PICTURED-1")
    _image(db, pictured, "img/pictured.jpg")
    plain = _product(db, "plain", category=bare, created_at=datetime(2026, 9, 2))
    _variant(db, plain, "PLAIN-1")

    garden = _category(db, "garden")
    rake = _product(db, "rake", category=garden)
    _variant(db, rake, "RAKE-1", is_active=False)
    _image(db, rake, "img/rake.jpg")

    _category(db, "empty")
    _category(db, "hidden", is_active=False)

    response = client.get("/api/catalog/categories")

    assert response.json() == [
        {"slug": "kitchen", "name": _text("Kitchen"), "image": "img/new-1.jpg"},
        {"slug": "tie", "name": _text("Tie"), "image": "img/tie-a.jpg"},
        {"slug": "bare", "name": _text("Bare"), "image": None},
        {"slug": "garden", "name": _text("Garden"), "image": None},
        {"slug": "empty", "name": _text("Empty"), "image": None},
    ]
    # 对照：kitchen 按商品列表 newest 排在第一的正是 new-mug，未发布的 draft-mug 不在列表里。
    assert _slugs(client, category="kitchen") == ["new-mug", "old-mug"]
    assert _slugs(client, category="tie") == ["tie-a", "tie-b"]


def test_category_embedded_in_product_summary_and_detail_has_no_image(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「只加在分类列表接口的响应里，嵌在商品摘要与商品详情里的分类不变」：
    分类列表带 image 时，列表项与详情里的分类仍只有 slug 与 name。
    """
    kitchen = _category(db, "kitchen")
    mug = _product(db, "mug", category=kitchen)
    _variant(db, mug, "MUG-1")
    _image(db, mug, "img/mug.jpg")
    embedded = {"slug": "kitchen", "name": _text("Kitchen")}

    categories = client.get("/api/catalog/categories").json()
    item = client.get("/api/catalog/products").json()["items"][0]
    detail = client.get("/api/catalog/products/mug").json()

    assert categories[0]["image"] == "img/mug.jpg"
    assert item["category"] == embedded
    assert detail["category"] == embedded


# ---------- 规格筛选项：GET /api/catalog/options ----------


def test_filter_options_only_list_values_used_by_active_skus_of_published_products(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「只统计已发布商品（沿用发布规则）的启用 SKU 实际用到的规格值」：
    停用 SKU 的值（blue、m）、没被任何 SKU 用到的值（green）、未发布商品的值（black），
    以及只挂在未用到的值上的规格名（pattern），都不出现。
    响应整体相等，多出排列序号、启用状态或库存等后台字段即失败。
    """
    tee = _product(db, "tee")
    red, blue, _green = _option(db, tee, "color", "red", "blue", "green")
    small, medium = _option(db, tee, "size", "s", "m")
    _variant(db, tee, "TEE-RED-S", red, small)
    _variant(db, tee, "TEE-BLUE-M", blue, medium, is_active=False)

    mug = _product(db, "mug")
    _option(db, mug, "pattern", "stripe")
    _variant(db, mug, "MUG-1")

    draft = _product(db, "draft", is_active=False)
    (black,) = _option(db, draft, "color", "black")
    _variant(db, draft, "DRAFT-BLACK", black)

    assert _filter_options(client) == [
        {
            "code": "color",
            "name": _text("Color"),
            "values": [{"code": "red", "name": _text("Red")}],
        },
        {
            "code": "size",
            "name": _text("Size"),
            "values": [{"code": "s", "name": _text("S")}],
        },
    ]


def test_filter_options_follow_the_publish_rule(db: Session, client: TestClient) -> None:
    """验收标准「只统计已发布商品（沿用发布规则）」：商品因另一个规格名缺英文名称而不发布时，
    它用到的规格值同样不出现；补上英文名称发布后即出现。先确认改动前不出现。
    """
    tee = _product(db, "tee")
    (red,) = _option(db, tee, "color", "red")
    (small,) = _option(db, tee, "size", "s")
    size = db.get(ProductOption, small.option_id)
    size.name_en = None
    _variant(db, tee, "TEE-RED-S", red, small)
    db.flush()

    assert _filter_options(client) == []

    size.name_en = "Size"
    db.flush()

    assert _codes(_filter_options(client)) == [("color", ["red"]), ("size", ["s"])]


def test_filter_options_aggregate_by_code_and_take_names_from_the_lowest_product_id(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「按规格名 code 聚合，同一规格名下按规格值 code 去重」「同一 code 在不同商品上
    名称不同时，取 id 最小的那件商品上的名称」。second 更新（newest 排第一）且名称不同，
    名称仍取 id 更小的 first；red 两件商品都有，只出现一次。
    """
    first = _product(db, "first", created_at=datetime(2026, 9, 1))
    colour = _option_name(db, first, "color", name_en="Colour", name_zh="颜色")
    first_red = _value(db, colour, "red", name_en="Red", name_zh="红色")
    _variant(db, first, "FIRST-RED", first_red)

    second = _product(db, "second", created_at=datetime(2026, 9, 9))
    color = _option_name(db, second, "color", name_en="Color", name_zh="色彩")
    second_red = _value(db, color, "red", 0, name_en="Crimson", name_zh="深红")
    second_blue = _value(db, color, "blue", 1, name_en="Blue", name_zh="蓝色")
    _variant(db, second, "SECOND-RED", second_red)
    _variant(db, second, "SECOND-BLUE", second_blue)
    assert first.id < second.id

    assert _filter_options(client) == [
        {
            "code": "color",
            "name": _text("Colour"),
            "values": [
                {"code": "red", "name": _text("Red")},
                {"code": "blue", "name": _text("Blue")},
            ],
        },
    ]
    zh = _filter_options(client, lang="zh")
    assert zh[0]["name"] == _text("颜色")
    assert [v["name"] for v in zh[0]["values"]] == [_text("红色"), _text("蓝色")]


def test_filter_options_sort_by_lowest_sort_order_then_code(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「规格名按它在各商品中最小的排列序号、再按 code 排序，规格值同理」。
    color 在 one 上排 1、在 two 上排 0，最小为 0，与 size 平手按 code 排在前；
    若只看先遇到的商品，color 会排在 size 之后。规格值同理：blue 在 two 上排 0，
    与 red 平手按 code 排在前；amber 最小为 2 排最后。
    """
    one = _product(db, "one")
    size = _option_name(db, one, "size", 0)
    color = _option_name(db, one, "color", 1)
    fit = _option_name(db, one, "fit", 3)
    for sku, value in [
        ("ONE-S", _value(db, size, "s", 0)),
        ("ONE-RED", _value(db, color, "red", 0)),
        ("ONE-BLUE", _value(db, color, "blue", 1)),
        ("ONE-SLIM", _value(db, fit, "slim", 0)),
    ]:
        _variant(db, one, sku, value)

    two = _product(db, "two")
    two_color = _option_name(db, two, "color", 0)
    _variant(db, two, "TWO-BLUE", _value(db, two_color, "blue", 0))
    _variant(db, two, "TWO-AMBER", _value(db, two_color, "amber", 2))

    assert _codes(_filter_options(client)) == [
        ("color", ["blue", "red", "amber"]),
        ("size", ["s"]),
        ("fit", ["slim"]),
    ]


def test_filter_options_fall_back_to_english_and_flag_it(db: Session, client: TestClient) -> None:
    """验收标准「每个规格名与规格值返回 code 与按请求语言的名称（回退英文的规则与标记同
    SHOP-TASK-005）」：有该语言名称的给该语言且不标；缺少（含空串与只有空格）时给英文并标
    english_fallback；不带 lang 即英文、不标。
    """
    tee = _product(db, "tee")
    color = _option_name(db, tee, "color", name_zh="颜色", name_ms="  ")
    red = _value(db, color, "red", 0, name_zh="红色", name_ms="Merah")
    blue = _value(db, color, "blue", 1, name_zh="")
    _variant(db, tee, "TEE-RED", red)
    _variant(db, tee, "TEE-BLUE", blue)

    def names(lang: str | None) -> list[object]:
        params = {"lang": lang} if lang else {}
        (option,) = _filter_options(client, **params)
        return [option["name"], *(value["name"] for value in option["values"])]

    assert names(None) == [_text("Color"), _text("Red"), _text("Blue")]
    assert names("zh") == [_text("颜色"), _text("红色"), _text("Blue", fallback=True)]
    assert names("ms") == [
        _text("Color", fallback=True),
        _text("Merah"),
        _text("Blue", fallback=True),
    ]


def test_filter_options_reject_a_language_outside_en_zh_ms(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「语言参数与目录接口相同（只接受 en、zh、ms，默认 en）」：
    其他值（含大写的 EN）422，而不是悄悄当成英文。先确认合法语言 200。
    """
    tee = _product(db, "tee")
    _variant(db, tee, "TEE-RED", *_option(db, tee, "color", "red"))

    for lang in ["en", "zh", "ms"]:
        assert client.get(OPTIONS_PATH, params={"lang": lang}).status_code == 200
    for lang in ["fr", "EN"]:
        assert client.get(OPTIONS_PATH, params={"lang": lang}).status_code == 422


def test_filter_option_codes_compose_the_product_list_option_filter(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「返回的规格名与规格值 code 可以原样组成商品列表接口的 option 筛选参数」：
    每个返回的 <规格名>:<规格值> 都能筛出恰好用到该值的已发布商品。
    """
    red_tee = _product(db, "red-tee")
    (red,) = _option(db, red_tee, "color", "red")
    (small,) = _option(db, red_tee, "size", "s")
    _variant(db, red_tee, "RED-TEE-S", red, small)
    blue_tee = _product(db, "blue-tee")
    blue, off_red = _option(db, blue_tee, "color", "blue", "red")
    _variant(db, blue_tee, "BLUE-TEE", blue)
    _variant(db, blue_tee, "BLUE-TEE-RED-OFF", off_red, is_active=False)
    expected = {
        "color:red": ["red-tee"],
        "color:blue": ["blue-tee"],
        "size:s": ["red-tee"],
    }

    matched = {}
    for option in _filter_options(client):
        for value in option["values"]:
            param = ":".join([option["code"], value["code"]])
            matched[param] = _slugs(client, option=param)

    assert matched == expected


def test_filter_options_are_empty_when_nothing_is_filterable(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「没有任何可筛选规格时返回空列表」：空库，以及只有无规格商品时，都是 []。"""
    assert _filter_options(client) == []

    socks = _product(db, "socks")
    _variant(db, socks, "SOCKS-1")
    assert _slugs(client) == ["socks"]

    assert _filter_options(client) == []


# ---------- 三处共同：只读、不需要登录、不读写 cookie ----------


def test_new_fields_and_endpoint_only_read_and_set_no_cookie(
    db: Session,
    client: TestClient,
) -> None:
    """验收标准「三处都只发 SELECT、不写数据库，不需要登录，不读写 cookie」：
    不带任何凭据即 200；发出的语句全是 SELECT；响应不设 cookie；写方法 405。
    """
    category = _category(db, "kitchen")
    mug = _product(db, "mug", category=category)
    _image(db, mug, "img/mug.jpg")
    (red,) = _option(db, mug, "color", "red")
    _variant(db, mug, "MUG-RED", red)
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    for path in ["/api/catalog/categories", "/api/catalog/products", OPTIONS_PATH]:
        response = client.get(path)
        assert response.status_code == 200
        assert "set-cookie" not in response.headers
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

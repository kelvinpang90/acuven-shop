"""购物车与结账计价接口 POST /api/checkout/quote。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Cart 一行：
「不信任浏览器价格，结账时服务端重算」；ShippingRate / DemoFxRate 一行；
「计价、优惠、积分与库存」第 1、2 条；「边界与原则」：
「金额以 MYR 的仙（sen）为整数单位运算」「按收货国家决定，不按 IP；
该国无演示汇率时只显示 MYR」。以及 SHOP-TASK-009 验收标准里的具体规则。
每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库按模型建表，接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

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
    DemoFxRate,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    ShippingRate,
    VariantOptionValue,
)

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

URL = "/api/checkout/quote"
MAX_BODY_BYTES = 8 * 1024


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


def _option(
    db: Session,
    product: Product,
    code: str,
    sort_order: int,
    *codes: str,
) -> tuple[ProductOption, list[ProductOptionValue]]:
    """规格名及其规格值；英文名称取 code 的首字母大写，中文与马来文留空。"""
    option = ProductOption(
        product_id=product.id,
        code=code,
        sort_order=sort_order,
        name_en=code.title(),
    )
    _add(db, option)
    values = []
    for value_order, value_code in enumerate(codes):
        value = ProductOptionValue(
            product_id=product.id,
            option_id=option.id,
            code=value_code,
            sort_order=value_order,
            name_en=value_code.title(),
        )
        values.append(_add(db, value))
    return option, values


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


@dataclass
class _Tee:
    category: Category
    product: Product
    color: ProductOption
    size: ProductOption
    red: ProductOptionValue
    red_m: ProductVariant
    blue_m: ProductVariant


def _tee(db: Session) -> _Tee:
    """一件可发布的商品：两张图片、两个启用的 SKU、两个规格名。
    尺寸先建但排列序号在后，所以按排列序号是颜色在前。
    """
    category = _add(db, Category(slug="apparel", name_en="Apparel", is_active=True))
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug="tee",
            name_en="Tee",
            description_en="A tee.",
            is_active=True,
        ),
    )
    _add(db, ProductImage(product_id=product.id, storage_ref="img/tee-2.jpg", sort_order=2))
    _add(db, ProductImage(product_id=product.id, storage_ref="img/tee-1.jpg", sort_order=1))
    size, (medium,) = _option(db, product, "size", 1, "m")
    color, (red, blue) = _option(db, product, "color", 0, "red", "blue")
    red_m = _variant(db, product, "TEE-RED-M", red, medium, price_sen=2590, available_stock=5)
    blue_m = _variant(db, product, "TEE-BLUE-M", blue, medium, price_sen=2190, available_stock=5)
    return _Tee(category, product, color, size, red, red_m, blue_m)


def _rates(db: Session, *, fallback: bool = True) -> None:
    """两个州属、SG 与 BN 两个国家行、兜底行；SG 与 JP 有汇率，BN 没有，
    JP 没有国家行。版本号各不相同，才看得出响应里的版本号来自哪一行。
    """
    zones = [
        ("my_state", "MY-10", 800, 1),
        ("my_state", "MY-13", 1500, 2),
        ("country", "SG", 2000, 3),
        ("country", "BN", 2500, 1),
    ]
    if fallback:
        zones.append(("other", "OTHER", 8000, 4))
    for zone_type, zone_code, fee_sen, version in zones:
        _add(
            db,
            ShippingRate(
                zone_type=zone_type,
                zone_code=zone_code,
                fee_sen=fee_sen,
                version=version,
            ),
        )
    fx = [
        ("SG", "SGD", 2, Decimal("0.310000"), 5),
        ("JP", "JPY", 0, Decimal("34.000000"), 6),
    ]
    for country_code, currency_code, decimals, rate, version in fx:
        _add(
            db,
            DemoFxRate(
                country_code=country_code,
                currency_code=currency_code,
                currency_decimals=decimals,
                rate=rate,
                version=version,
            ),
        )


def _text(text: str, *, fallback: bool = False) -> dict[str, object]:
    return {"text": text, "english_fallback": fallback}


def _chosen(code: str, name: str, value_code: str, value_name: str) -> dict[str, object]:
    return {
        "code": code,
        "name": _text(name),
        "value": {"code": value_code, "name": _text(value_name)},
    }


def _red_line(quantity: int = 1, **overrides: object) -> dict[str, object]:
    """TEE-RED-M 的正常行，整体相等地比较。"""
    line = {
        "sku": "TEE-RED-M",
        "quantity": quantity,
        "status": "ok",
        "available_stock": None,
        "max_per_order": 10,
        "product_slug": "tee",
        "name": _text("Tee"),
        "options": [_chosen("color", "Color", "red", "Red"), _chosen("size", "Size", "m", "M")],
        "image": "img/tee-1.jpg",
        "unit_price_sen": 2590,
        "line_subtotal_sen": 2590 * quantity,
    }
    return line | overrides


def _body(*lines: tuple[str, int], **fields: object) -> dict[str, object]:
    return {"lines": [{"sku": sku, "quantity": quantity} for sku, quantity in lines]} | fields


def _quote(client: TestClient, body: object, **params: str) -> dict[str, object]:
    response = client.post(URL, json=body, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_ok_line_has_exactly_the_public_fields(db: Session, client: TestClient) -> None:
    """「正常与库存不足的行另返回商品 slug、按请求语言的商品名称与所选规格
    （规格名与规格值按排列序号）、首张图片引用、单价（整数仙）与行小计（单价乘件数）」；
    「响应不含每日初始库存、启用状态等后台字段」
    「不含优惠券、积分与会员字段，也不预留占位字段」——整体相等，多一个字段就失败。
    不带 lang 即英文，不标回退。
    """
    _tee(db)

    quote = _quote(client, _body(("TEE-RED-M", 3)))

    assert quote == {
        "lines": [_red_line(3)],
        "subtotal_sen": 7770,
        "shipping": None,
        "total_sen": None,
        "fx_reference": None,
        "can_place_order": False,
    }


def test_subtotal_is_the_sum_of_priced_lines_and_skips_unavailable_ones(
    db: Session,
    client: TestClient,
) -> None:
    """「商品小计是正常与库存不足各行行小计之和」「不可购买的行不计入任何金额」；
    「响应按请求顺序逐行返回」：行序与请求一致，不按 SKU 或商品排序。
    """
    _tee(db)

    quote = _quote(client, _body(("TEE-BLUE-M", 2), ("NO-SUCH-SKU", 4), ("TEE-RED-M", 1)))

    assert [line["sku"] for line in quote["lines"]] == ["TEE-BLUE-M", "NO-SUCH-SKU", "TEE-RED-M"]
    assert [line.get("line_subtotal_sen") for line in quote["lines"]] == [4380, None, 2590]
    assert quote["subtotal_sen"] == 4380 + 2590


@pytest.mark.parametrize(
    ("owner", "field", "missing"),
    [
        ("red_m", "is_active", False),
        ("product", "is_active", False),
        ("category", "is_active", False),
        ("category", "name_en", None),
        ("product", "name_en", "  "),
        ("product", "description_en", None),
        ("color", "name_en", None),
        ("red", "name_en", ""),
    ],
    ids=[
        "sku-stopped",
        "product-stopped",
        "category-stopped",
        "category-no-english-name",
        "product-no-english-name",
        "product-no-english-description",
        "option-no-english-name",
        "value-no-english-name",
    ],
)
def test_stopped_sku_and_each_publish_condition_make_the_line_unavailable(
    db: Session,
    client: TestClient,
    owner: str,
    field: str,
    missing: object,
) -> None:
    """「SKU 停用或所属商品按 SHOP-TASK-005 的发布规则未发布，都标为不可购买，
    且这一行只返回 SKU、件数与状态，不透露未发布商品的任何信息」；
    「不可购买的行不计入任何金额」。发布规则逐条：商品停用、分类停用、
    分类缺英文名称、商品缺英文名称、商品缺英文描述、规格名缺英文名称、
    规格值缺英文名称。先确认改动前这一行是正常的，免得测试碰巧通过。
    """
    tee = _tee(db)
    body = _body(("TEE-RED-M", 2))
    assert _quote(client, body)["lines"] == [_red_line(2)]

    setattr(getattr(tee, owner), field, missing)
    db.flush()
    quote = _quote(client, body)

    assert quote["lines"] == [{"sku": "TEE-RED-M", "quantity": 2, "status": "unavailable"}]
    assert quote["subtotal_sen"] == 0


def test_unknown_sku_is_unavailable_and_matched_exactly(db: Session, client: TestClient) -> None:
    """「SKU 不存在…标为不可购买，且这一行只返回 SKU、件数与状态」。
    SKU 逐字比对：大小写不同或带尾随空格的不是同一个
    （MySQL 默认排序规则比较时不区分这些）。
    """
    _tee(db)

    quote = _quote(client, _body(("NO-SUCH-SKU", 1), ("tee-red-m", 1), ("TEE-RED-M ", 1)))

    assert quote["lines"] == [
        {"sku": "NO-SUCH-SKU", "quantity": 1, "status": "unavailable"},
        {"sku": "tee-red-m", "quantity": 1, "status": "unavailable"},
        {"sku": "TEE-RED-M ", "quantity": 1, "status": "unavailable"},
    ]
    assert quote["subtotal_sen"] == 0


def test_short_stock_is_flagged_with_available_count_and_still_priced(
    db: Session,
    client: TestClient,
) -> None:
    """「当日可用库存小于件数的标为库存不足，并给出当日可用库存」（含可用数为 0）；
    「商品小计是正常与库存不足各行行小计之和」：库存不足的行照常给商品信息并计入小计。
    件数恰好等于可用库存仍是正常。
    """
    tee = _tee(db)
    tee.red_m.available_stock = 3
    tee.blue_m.available_stock = 0
    db.flush()

    short = _quote(client, _body(("TEE-RED-M", 4), ("TEE-BLUE-M", 1)))
    exact = _quote(client, _body(("TEE-RED-M", 3)))

    red, blue = short["lines"]
    assert red == _red_line(4, status="insufficient_stock", available_stock=3)
    assert blue["status"] == "insufficient_stock"
    assert (blue["available_stock"], blue["line_subtotal_sen"]) == (0, 2190)
    assert short["subtotal_sen"] == 4 * 2590 + 2190
    assert exact["lines"] == [_red_line(3)]


def test_missing_translation_falls_back_to_english_and_is_flagged(
    db: Session,
    client: TestClient,
) -> None:
    """「按请求语言的商品名称与所选规格…回退英文的规则与标记同 SHOP-TASK-005」：
    有该语言文案的字段给该语言且不标；缺的给英文并标 english_fallback，逐字段独立。
    """
    tee = _tee(db)
    tee.product.name_zh = "T恤"
    tee.red.name_zh = "红色"
    tee.size.name_zh = "尺寸"
    db.flush()

    (zh,) = _quote(client, _body(("TEE-RED-M", 1)), lang="zh")["lines"]
    (ms,) = _quote(client, _body(("TEE-RED-M", 1)), lang="ms")["lines"]

    assert zh["name"] == _text("T恤")
    assert zh["options"] == [
        {
            "code": "color",
            "name": _text("Color", fallback=True),
            "value": {"code": "red", "name": _text("红色")},
        },
        {
            "code": "size",
            "name": _text("尺寸"),
            "value": {"code": "m", "name": _text("M", fallback=True)},
        },
    ]
    assert ms["name"] == _text("Tee", fallback=True)
    assert ms["options"][0]["value"]["name"] == _text("Red", fallback=True)


@pytest.mark.parametrize(
    ("destination", "shipping", "total", "fx"),
    [
        (
            {"country_code": "MY", "state_code": "MY-13"},
            {"fee_sen": 1500, "zone_code": "MY-13", "version": 2},
            5180 + 1500,
            None,
        ),
        (
            {"country_code": "SG"},
            {"fee_sen": 2000, "zone_code": "SG", "version": 3},
            5180 + 2000,
            # 7180 仙 × 0.31 = 2225.8 分 → 2226。
            {"currency_code": "SGD", "currency_decimals": 2, "amount_minor": 2226, "version": 5},
        ),
        (
            {"country_code": "BN"},
            {"fee_sen": 2500, "zone_code": "BN", "version": 1},
            5180 + 2500,
            None,
        ),
        (
            {"country_code": "JP"},
            {"fee_sen": 8000, "zone_code": "OTHER", "version": 4},
            5180 + 8000,
            # 13180 仙 × 34 ÷ 100 = 4481.2 円 → 4481。
            {"currency_code": "JPY", "currency_decimals": 0, "amount_minor": 4481, "version": 6},
        ),
    ],
    ids=["malaysia-state", "country-row-with-fx", "country-row-without-fx", "fallback-with-fx"],
)
def test_destination_adds_shipping_total_and_reference_currency(
    db: Session,
    client: TestClient,
    destination: dict[str, str],
    shipping: dict[str, object],
    total: int,
    fx: dict[str, object] | None,
) -> None:
    """「给了收货国家时，响应另含示例运费（整数仙、所用区域代码与版本号）、
    合计（商品小计加运费）与合计的参考外币金额（币种、小数位、最小单位整数金额
    与版本号…该国无汇率时为空）」：马来西亚按州属、有国家行的国家、兜底国家；
    有汇率、无汇率、马来西亚（只显示 MYR）。全部正常且给了国家即可下单。
    """
    _tee(db)
    _rates(db)

    quote = _quote(client, _body(("TEE-RED-M", 2), **destination))

    assert quote["subtotal_sen"] == 5180
    assert quote["shipping"] == shipping
    assert quote["total_sen"] == total
    assert quote["fx_reference"] == fx
    assert quote["can_place_order"] is True


def test_no_destination_means_no_shipping_total_or_fx_and_no_order(
    db: Session,
    client: TestClient,
) -> None:
    """「没给国家时这三项都为空，不猜测国家、不按 IP 判断」：
    即使请求带着像是马来西亚的来源头，运费、合计、参考外币都为空，且不可下单。
    """
    _tee(db)
    _rates(db)

    response = client.post(
        URL,
        json=_body(("TEE-RED-M", 1)),
        headers={"X-Forwarded-For": "175.139.0.1", "Accept-Language": "ms-MY"},
    )

    quote = response.json()
    assert (quote["shipping"], quote["total_sen"], quote["fx_reference"]) == (None, None, None)
    assert quote["can_place_order"] is False


@pytest.mark.parametrize(
    ("lines", "destination", "expected"),
    [
        ([("TEE-RED-M", 1)], {"country_code": "SG"}, True),
        ([("TEE-RED-M", 1)], {}, False),
        ([("TEE-RED-M", 9)], {"country_code": "SG"}, False),
        ([("TEE-RED-M", 9)], {}, False),
        ([("TEE-RED-M", 1), ("NO-SUCH-SKU", 1)], {"country_code": "SG"}, False),
    ],
    ids=[
        "ok-with-country",
        "ok-without-country",
        "short-with-country",
        "short-without-country",
        "unavailable-with-country",
    ],
)
def test_can_place_order_needs_all_lines_ok_and_a_country(
    db: Session,
    client: TestClient,
    lines: list[tuple[str, int]],
    destination: dict[str, str],
    expected: bool,
) -> None:
    """「所有行均为正常且给了收货国家时为真，否则为假」：
    全部正常与否 × 给没给国家的四种组合，另加有不可购买行的一种。
    可用库存是 5，件数 9 即库存不足。
    """
    _tee(db)
    _rates(db)

    quote = _quote(client, _body(*lines, **destination))

    assert quote["can_place_order"] is expected


def _lines(count: int) -> list[dict[str, object]]:
    return [{"sku": f"SKU-{i}", "quantity": 1} for i in range(count)]


def _line(**fields: object) -> dict[str, object]:
    return {"lines": [{"sku": "TEE-RED-M", "quantity": 1} | fields]}


def _one(**fields: object) -> dict[str, object]:
    return _body(("TEE-RED-M", 1), **fields)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"lines": []}, id="zero-lines"),
        pytest.param({"lines": _lines(21)}, id="21-lines"),
        pytest.param({}, id="no-lines"),
        pytest.param([], id="not-an-object"),
        pytest.param(_line(quantity=0), id="quantity-0"),
        pytest.param(_line(quantity=100), id="quantity-100"),
        pytest.param(_line(quantity=1.5), id="quantity-1.5"),
        pytest.param(_line(quantity=2.0), id="quantity-2.0"),
        pytest.param(_line(quantity="2"), id="quantity-string"),
        pytest.param(_line(quantity=True), id="quantity-bool"),
        pytest.param({"lines": [{"sku": "TEE-RED-M"}]}, id="no-quantity"),
        pytest.param(_line(sku=""), id="empty-sku"),
        pytest.param(_line(sku="S" * 65), id="65-char-sku"),
        pytest.param(_line(sku=7), id="numeric-sku"),
        pytest.param(_body(("TEE-RED-M", 1), ("TEE-RED-M", 2)), id="duplicate-sku"),
        pytest.param(_line(unit_price_sen=1), id="price-on-line"),
        pytest.param(_line(line_subtotal_sen=1), id="line-subtotal-on-line"),
        pytest.param(_one(subtotal_sen=1), id="subtotal"),
        pytest.param(_one(total_sen=1), id="total"),
        pytest.param(_one(shipping_fee_sen=0), id="shipping-fee"),
        pytest.param(_one(fx_rate="0.31"), id="fx-rate"),
        pytest.param(_one(discount_sen=100), id="discount"),
        pytest.param(_one(coupon_code="X"), id="coupon"),
        pytest.param(_one(state_code="MY-10"), id="state-without-country"),
        pytest.param(_one(country_code="sg"), id="lowercase-country"),
        pytest.param(_one(country_code="my", state_code="MY-10"), id="lowercase-malaysia"),
        pytest.param(_one(country_code="SGP"), id="three-letter-country"),
        pytest.param(_one(country_code=""), id="empty-country"),
        pytest.param(_one(country_code="MY", state_code="my-10"), id="lowercase-state"),
        pytest.param(_one(country_code="MY", state_code="MY-17"), id="unknown-state"),
        pytest.param(_one(country_code="MY"), id="malaysia-without-state"),
        pytest.param(_one(country_code="SG", state_code="MY-10"), id="state-outside-malaysia"),
    ],
)
def test_invalid_request_body_is_rejected(db: Session, client: TestClient, body: object) -> None:
    """「购物车 1 到 20 行，每行件数是 1 到 99 的整数（DESIGN 1.10 第 8 条），
    SKU 是 1 到 64 个字符的字符串；
    同一 SKU 出现两次、行数或件数越界、给了州属却没给国家均 422」；
    「不接受任何价格、金额、运费、汇率或折扣字段，多出的字段一律 422」；
    「国家与州属代码的合法性沿用 app/services/shipping.py 的规则，不合法 422，
    不转换大小写」。费率行都在，被拒不是因为查不到运费。
    """
    _tee(db)
    _rates(db)

    assert client.post(URL, json=body).status_code == 422


def test_malformed_json_is_rejected(db: Session, client: TestClient) -> None:
    """「未超限的请求体才按请求模型校验」：不是 JSON 的请求体是 422，不是 500。"""
    _tee(db)

    response = client.post(URL, content=b"lines=TEE-RED-M", headers={"Content-Type": "text/plain"})

    assert response.status_code == 422


def test_limits_are_inclusive(db: Session, client: TestClient) -> None:
    """「1 到 20 行」「1 到 99 的整数」「1 到 64 个字符」的对照：
    恰好 20 行、件数恰好 99、SKU 恰好 64 个字符都接受。
    """
    _tee(db)

    assert client.post(URL, json={"lines": _lines(20)}).status_code == 200
    assert client.post(URL, json=_line(quantity=99)).status_code == 200
    assert client.post(URL, json=_line(sku="S" * 64)).status_code == 200


@pytest.mark.parametrize("lang", ["fr", "EN", ""])
def test_language_outside_en_zh_ms_is_rejected(
    db: Session,
    client: TestClient,
    lang: str,
) -> None:
    """「语言参数与目录接口相同（只接受 en、zh、ms，默认 en）」：
    其他值（含大写的 EN）被拒，而不是悄悄当成英文。
    """
    _tee(db)

    assert client.post(URL, json=_line(), params={"lang": "zh"}).status_code == 200
    assert client.post(URL, json=_line(), params={"lang": lang}).status_code == 422


def _padded(body: dict[str, object], size: int) -> bytes:
    """在合法 JSON 后补空格到恰好 size 字节；JSON 允许尾随空白。"""
    raw = json.dumps(body).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


def test_body_over_8_kb_is_413_and_8_kb_exactly_is_accepted(
    db: Session,
    client: TestClient,
) -> None:
    """「请求体上限 8 KB…超过即停止读取并返回 413」：
    恰好 8192 字节照常计价，多 1 字节即 413。
    """
    _tee(db)
    headers = {"Content-Type": "application/json"}

    at_limit = client.post(URL, content=_padded(_line(), MAX_BODY_BYTES), headers=headers)
    over = client.post(URL, content=_padded(_line(), MAX_BODY_BYTES + 1), headers=headers)

    assert at_limit.status_code == 200
    assert over.status_code == 413


def test_oversized_body_is_413_before_any_validation(db: Session, client: TestClient) -> None:
    """「体积上限先于一切校验，未超限的请求体才按请求模型校验」：
    同时带多余字段、非法语言参数的超限请求体是 413，不是 422。
    """
    _tee(db)
    body = _line() | {"padding": "x" * MAX_BODY_BYTES}

    assert client.post(URL, json=body).status_code == 413
    assert client.post(URL, json=body, params={"lang": "fr"}).status_code == 413


def test_oversized_body_without_content_length_is_413(db: Session, client: TestClient) -> None:
    """「按实际读到的字节逐块判断（不只看 Content-Length）」：
    分块发送、不带 Content-Length 的超限请求体同样 413。
    """
    _tee(db)
    head = b'{"lines": [{"sku": "TEE-RED-M", "quantity": 1}], "x": "'
    chunks = [head, *([b"y" * 1024] * 9), b'"}']

    response = client.post(
        URL,
        content=iter(chunks),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_oversized_body_is_413_even_without_a_database() -> None:
    """「体积上限先于一切校验」：未配置数据库时，超限请求体仍是 413，
    未超限的才是会话依赖给的 503。
    """
    client = TestClient(create_app(Settings(_env_file=None, database_url="")))
    body = _line() | {"padding": "x" * MAX_BODY_BYTES}

    assert client.post(URL, json=body).status_code == 413
    assert client.post(URL, json=_line()).status_code == 503


@pytest.mark.parametrize(
    ("destination", "fallback"),
    [
        ({"country_code": "FR"}, False),
        ({"country_code": "MY", "state_code": "MY-05"}, True),
    ],
    ids=["fallback-row-missing", "malaysia-state-row-missing"],
)
def test_missing_shipping_row_is_503_without_details(
    db: Session,
    client: TestClient,
    destination: dict[str, str],
    fallback: bool,
) -> None:
    """「合法目的地因运费行缺失（马来西亚州属行或兜底行）取不到运费时一律返回 503，
    响应不含内部细节，不返回零运费」：州属行缺失时即使有兜底行也不退回兜底。
    """
    _tee(db)
    _rates(db, fallback=fallback)

    response = client.post(URL, json=_body(("TEE-RED-M", 1), **destination))

    assert response.status_code == 503
    assert response.json() == {"detail": "shipping is unavailable"}
    for detail in ["OTHER", "MY-05", "FR", "fee"]:
        assert detail not in response.text


def test_quote_only_reads_and_sets_no_cookie(db: Session, client: TestClient) -> None:
    """「接口只发 SELECT、不写数据库」「不需要登录，不读写 cookie」「不预留或扣减库存」：
    带国家、含各种状态的计价只发 SELECT，响应不设 cookie，库存不变；
    只有 POST，GET 得到 405。
    """
    tee = _tee(db)
    _rates(db)
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    body = _body(("TEE-RED-M", 2), ("TEE-BLUE-M", 9), ("NO-SUCH-SKU", 1), country_code="SG")
    client.cookies.set("session", "anything")
    response = client.post(URL, json=body)

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert client.get(URL).status_code == 405
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    db.refresh(tee.red_m)
    assert tee.red_m.available_stock == 5

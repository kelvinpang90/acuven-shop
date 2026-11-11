"""游客计价接入节日促销活动（SHOP-TASK-234）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 4、8、10 条与「计价、优惠、积分与库存」
第 1、8 条（下称「第 N 条」与「计价第 N 条」），以及 SHOP-TASK-234 写定的验收标准（下称「验收」）。
每条测试的文档字符串引用它守住的那一句。

服务层直接调用 quote_cart 并传入 active_promotion 读出的活动；接口层替换 app.api.checkout._utcnow
固定时钟。用 SQLite 内存库按模型建表，接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import checkout as checkout_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    DemoFxRate,
    Product,
    ProductVariant,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    ShippingRate,
)
from app.services.checkout import (
    BuyXGetYQuote,
    CartItem,
    PricedLine,
    PromotionQuote,
    UnavailableLine,
    quote_cart,
)
from app.services.promotions import LoadedPromotion, active_promotion
from app.services.shipping import ShippingQuote, reference_amount

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

URL = "/api/checkout/quote"
STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)
NOW = STARTS + timedelta(hours=1)
WEST = {"country_code": "MY", "state_code": "MY-10"}


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
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _category(db: Session, slug: str) -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=True))


def _product(
    db: Session,
    category: Category,
    slug: str,
    price_sen: int,
    *,
    stock: int = 10,
    max_per_order: int = 10,
) -> Product:
    """没有规格名的已发布商品，唯一的 SKU 为 slug 的大写。"""
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=slug,
            name_en=slug.upper(),
            description_en=f"{slug}.",
            is_active=True,
            max_per_order=max_per_order,
        ),
    )
    _add(
        db,
        ProductVariant(
            product_id=product.id,
            sku=slug.upper(),
            price_sen=price_sen,
            daily_initial_stock=10,
            available_stock=stock,
            is_active=True,
        ),
    )
    return product


def _rates(db: Session) -> None:
    """西马 RM8.00、东马 RM15.00、新加坡 RM20.00（有 SGD 演示汇率）。"""
    for zone_type, zone_code, fee_sen in [
        ("my_region", "MY-W", 800),
        ("my_region", "MY-E", 1500),
        ("country", "SG", 2000),
    ]:
        _add(db, ShippingRate(zone_type=zone_type, zone_code=zone_code, fee_sen=fee_sen, version=1))
    _add(
        db,
        DemoFxRate(
            country_code="SG",
            currency_code="SGD",
            currency_decimals=2,
            rate=Decimal("0.310000"),
            version=1,
        ),
    )


@dataclass
class _Shop:
    gifts: Category
    home: Category
    a: Product
    b: Product
    c: Product
    item_rule_id: int
    buy_x_get_y_rule_id: int
    promotion_id: int


def _section_8(db: Session) -> _Shop:
    """第 8 条算例的商品与活动：A RM25.00（分类「礼品」）、B RM12.00 与 C RM9.00（分类「家居」）；
    A 打 8 折；「家居」买 2 享 1、享受的件 100%；满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费。
    活动在 STARTS 到 ENDS 之间生效。
    """
    _rates(db)
    gifts = _category(db, "gifts")
    home = _category(db, "home")
    a = _product(db, gifts, "a", 2500)
    b = _product(db, home, "b", 1200)
    c = _product(db, home, "c", 900)
    promotion = _add(
        db,
        Promotion(
            name="Sale",
            status="published",
            starts_at=STARTS,
            ends_at=ENDS,
            coupon_combinable=True,
            points_combinable=True,
        ),
    )
    item_rule = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id,
            position=1,
            kind="item_discount",
            discount_mode="percent",
            percent=20,
        ),
    )
    _add(db, PromotionRuleProduct(rule_id=item_rule.id, product_id=a.id))
    bxgy = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id,
            position=2,
            kind="buy_x_get_y",
            buy_quantity=2,
            get_quantity=1,
            get_percent=100,
        ),
    )
    _add(db, PromotionRuleCategory(rule_id=bxgy.id, category_id=home.id))
    spend = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id, position=3, kind="spend_discount", discount_mode="amount"
        ),
    )
    for position, threshold_sen, amount_sen in [(1, 5000, 500), (2, 10000, 1200)]:
        tier = PromotionSpendTier(
            rule_id=spend.id, position=position, threshold_sen=threshold_sen, amount_sen=amount_sen
        )
        _add(db, tier)
    _add(
        db,
        PromotionRule(
            promotion_id=promotion.id, position=4, kind="free_shipping", threshold_sen=6000
        ),
    )
    return _Shop(gifts, home, a, b, c, item_rule.id, bxgy.id, promotion.id)


SECTION_8_CART = [CartItem("A", 2), CartItem("B", 2), CartItem("C", 1)]


def _active(db: Session) -> LoadedPromotion:
    promotion = active_promotion(db, NOW)
    assert promotion is not None
    return promotion


def _set_variant(db: Session, sku: str, **values: int) -> None:
    variant = db.scalars(select(ProductVariant).where(ProductVariant.sku == sku)).one()
    for name, value in values.items():
        setattr(variant, name, value)
    db.flush()


def _rule_pairs(lines: list[dict[str, object]]) -> list[tuple[object, object]]:
    return [(line["promotion_rule_id"], line["promotion_item_discount_sen"]) for line in lines]


def _priced(line: UnavailableLine | PricedLine) -> PricedLine:
    assert isinstance(line, PricedLine)
    return line


def _section_8_promotion_quote(shop: _Shop, **overrides: object) -> PromotionQuote:
    """第 8 条算例（游客、不用券）在西马运费报价下的活动结果。"""
    fields: dict[str, object] = {
        "promotion_id": shop.promotion_id,
        "item_discount_sen": 1900,
        "order_discount_sen": 500,
        "after_item_subtotal_sen": 6400,
        "spend_mode": "amount",
        "spend_tier_reached": 1,
        "spend_next_threshold_sen": 10000,
        "spend_next_value": 1200,
        "spend_next_gap_sen": 3600,
        "free_shipping_threshold_sen": 6000,
        "free_shipping_reached": True,
        "free_shipping_gap_sen": 0,
        "shipping_waived_sen": 800,
        "participates": True,
        "buy_x_get_y": [
            BuyXGetYQuote(
                rule_id=shop.buy_x_get_y_rule_id,
                buy_quantity=2,
                get_quantity=1,
                get_percent=100,
                units_in_scope=3,
                units_to_next=3,
            )
        ],
    }
    return PromotionQuote(**(fields | overrides))


# ---- 服务层 ----


def test_section_8_lines_carry_rule_and_item_discount(db: Session) -> None:
    """第 4 条（一）「按第 3 条算出每件的商品级折扣」与验收「status 为 ok 的行分别为该行命中的
    商品级规则 ID 与该行各件商品级折扣之和」：第 8 条算例里 A 每件折 RM5.00、两件 1000 仙；
    B 在买 2 享 1 范围内但不是最便宜的件，0 仙；C 是最便宜的件，900 仙。
    """
    shop = _section_8(db)

    quote = quote_cart(db, SECTION_8_CART, lang="en", **WEST, promotion=_active(db))

    lines = [_priced(line) for line in quote.lines]
    assert [(line.promotion_rule_id, line.promotion_item_discount_sen) for line in lines] == [
        (shop.item_rule_id, 1000),
        (shop.buy_x_get_y_rule_id, 0),
        (shop.buy_x_get_y_rule_id, 900),
    ]


def test_section_8_promotion_fields_and_total_with_west_shipping(db: Session) -> None:
    """第 8 条「S1 = RM64.00」「达到 RM50 一档，整单减 RM5.00」「S1 达到 RM60，运费为 0，免去
    RM8.00」；第 10 条「计价接口另返回 S1、满额减的当前档与下一档还差的金额、免运费还差的金额、
    每条买 X 享 Y 规则范围内的件数 n 与再加几件可多享 Y 件」；计价第 1 条「活动免运费时运费为 0」：
    游客不用券，合计 = 8300 − 1900 − 500 + 800 − 800 = 5900；shipping 仍为未免的运费报价。
    """
    shop = _section_8(db)

    quote = quote_cart(db, SECTION_8_CART, lang="en", **WEST, promotion=_active(db))

    assert quote.promotion == _section_8_promotion_quote(shop)
    assert quote.subtotal_sen == 8300
    assert quote.shipping == ShippingQuote(fee_sen=800, zone_code="MY-W", version=1)
    assert quote.total_sen == 5900
    assert quote.can_place_order is True


def test_without_shipping_quote_participation_is_undecided(db: Session) -> None:
    """第 4 条「还没有运费报价时（购物车页）…免运费只给出是否达到门槛，不判定是否参加活动」：
    不给收货国家时 participates 与 shipping_waived_sen 为 None、free_shipping_reached 照给，
    total_sen 照旧为 None；商品级与整单级优惠照（一）（二）给出。
    """
    shop = _section_8(db)

    quote = quote_cart(db, SECTION_8_CART, lang="en", promotion=_active(db))

    assert quote.promotion == _section_8_promotion_quote(
        shop, shipping_waived_sen=None, participates=None
    )
    assert quote.shipping is None
    assert quote.total_sen is None
    assert quote.fx_reference is None


def test_reference_currency_follows_promotion_total(db: Session) -> None:
    """验收「参考外币按这个合计」：收货新加坡（运费 RM20.00，S1 达到免运费门槛）时，合计为
    8300 − 1900 − 500 + 2000 − 2000 = 5900，参考外币按 5900 仙换算。
    """
    _section_8(db)

    quote = quote_cart(db, SECTION_8_CART, lang="en", country_code="SG", promotion=_active(db))

    assert quote.promotion is not None
    assert quote.promotion.shipping_waived_sen == 2000
    assert quote.total_sen == 5900
    assert quote.fx_reference == reference_amount(db, "SG", 5900)


def test_over_limit_and_short_lines_are_not_in_the_promotion(db: Session) -> None:
    """第 4 条「只算计价结果里的正常行（不可购买、超出限购、库存不足的行不计）」：A 库存不足、
    「家居」的 D 超出限购，都不命中规则、商品级折扣为 0，也不计入 S1 与买 2 享 1 的件数；
    正常的 B 两件与 C 一件照算（C 免费），S1 = 2400。不可购买的行照旧只有 SKU、件数与状态。
    """
    shop = _section_8(db)
    _product(db, shop.home, "d", 500, max_per_order=1)
    _set_variant(db, "A", available_stock=1)
    cart = [
        CartItem("A", 2),
        CartItem("B", 2),
        CartItem("NOPE", 1),
        CartItem("C", 1),
        CartItem("D", 2),
    ]

    quote = quote_cart(db, cart, lang="en", **WEST, promotion=_active(db))

    statuses = [line.status for line in quote.lines]
    assert statuses == ["insufficient_stock", "ok", "unavailable", "ok", "over_limit"]
    priced = [line for line in quote.lines if isinstance(line, PricedLine)]
    assert [(line.promotion_rule_id, line.promotion_item_discount_sen) for line in priced] == [
        (None, 0),
        (shop.buy_x_get_y_rule_id, 0),
        (shop.buy_x_get_y_rule_id, 900),
        (None, 0),
    ]
    assert quote.promotion is not None
    assert quote.promotion.item_discount_sen == 900
    assert quote.promotion.after_item_subtotal_sen == 2400
    assert quote.promotion.buy_x_get_y[0].units_in_scope == 3
    assert quote.can_place_order is False


def test_no_benefit_means_not_participating_and_total_unchanged(db: Session) -> None:
    """第 4 条「本单所有规则都没有带来优惠（商品级折扣、整单级优惠与免去的运费都为 0）时，视为不
    参加活动」与验收「否则 total_sen 照旧」：只买不在任何商品级规则里的 E 一件 RM10.00，S1 未达
    任一门槛，participates 为 False，合计仍为 1000 + 800。
    """
    shop = _section_8(db)
    _product(db, shop.gifts, "e", 1000)

    quote = quote_cart(db, [CartItem("E", 1)], lang="en", **WEST, promotion=_active(db))

    assert quote.promotion is not None
    assert quote.promotion.participates is False
    assert quote.promotion.shipping_waived_sen == 0
    assert (quote.promotion.spend_tier_reached, quote.promotion.spend_next_gap_sen) == (None, 4000)
    assert quote.promotion.free_shipping_gap_sen == 5000
    assert quote.total_sen == 1800
    assert _priced(quote.lines[0]).promotion_rule_id is None


def test_no_ok_lines_still_gives_promotion_with_thresholds_as_gaps(db: Session) -> None:
    """验收「promotion 参数为 None 时为 None，否则总是给出（没有正常行时各金额为 0、差额为门槛
    本身）」：只有不可购买的行时，各金额为 0，满额与免运费的差额就是第一档与免运费的门槛，
    买 2 享 1 的件数为 0、再加 3 件可多享。
    """
    shop = _section_8(db)

    quote = quote_cart(db, [CartItem("NOPE", 1)], lang="en", **WEST, promotion=_active(db))

    assert quote.promotion == PromotionQuote(
        promotion_id=shop.promotion_id,
        item_discount_sen=0,
        order_discount_sen=0,
        after_item_subtotal_sen=0,
        spend_mode="amount",
        spend_tier_reached=None,
        spend_next_threshold_sen=5000,
        spend_next_value=500,
        spend_next_gap_sen=5000,
        free_shipping_threshold_sen=6000,
        free_shipping_reached=False,
        free_shipping_gap_sen=6000,
        shipping_waived_sen=0,
        participates=False,
        buy_x_get_y=[
            BuyXGetYQuote(
                rule_id=shop.buy_x_get_y_rule_id,
                buy_quantity=2,
                get_quantity=1,
                get_percent=100,
                units_in_scope=0,
                units_to_next=3,
            )
        ],
    )
    assert quote.total_sen == 800


def test_top_tier_reached_has_no_next_tier(db: Session) -> None:
    """第 10 条「满额减的当前档与下一档还差的金额」与验收「已到最高档…为 None」：A 四件、B 三件、
    C 三件，S1 = 8000 + 3600 + 900 = 12500（家居 6 件 k = 2，两件 C 免费），达到 RM100 一档，
    整单减 RM12，下一档的门槛、优惠与差额都为 None。
    """
    _section_8(db)
    cart = [CartItem("A", 4), CartItem("B", 3), CartItem("C", 3)]

    quote = quote_cart(db, cart, lang="en", **WEST, promotion=_active(db))

    assert quote.promotion is not None
    assert quote.promotion.after_item_subtotal_sen == 12500
    assert quote.promotion.spend_tier_reached == 2
    assert quote.promotion.order_discount_sen == 1200
    next_tier = (
        quote.promotion.spend_next_threshold_sen,
        quote.promotion.spend_next_value,
        quote.promotion.spend_next_gap_sen,
    )
    assert next_tier == (None, None, None)


def test_amount_limit_uses_original_subtotal_and_unwaived_shipping(db: Session) -> None:
    """计价第 8 条「1.23 起也在活动优惠与活动免运费之前，即按原价小计与未免的运费」：A 改价为
    49,999,600 仙、买两件，原价小计加西马运费为 100,000,000 仙，超出上限；活动打折并免运费后的
    合计虽低于上限，amount_over_limit 仍为真、不可下单、不给参考外币。
    """
    _section_8(db)
    _set_variant(db, "A", price_sen=49_999_600)

    quote = quote_cart(db, [CartItem("A", 2)], lang="en", **WEST, promotion=_active(db))

    assert quote.promotion is not None and quote.promotion.participates is True
    assert quote.total_sen is not None and quote.total_sen < 99_999_999
    assert quote.amount_over_limit is True
    assert quote.can_place_order is False
    assert quote.fx_reference is None


def test_without_promotion_argument_fields_are_as_before(db: Session) -> None:
    """验收「现有调用不传，结果与现在逐字段相同（新字段取默认值）」：库里有生效活动，但不传
    promotion 时不计任何活动，各行两个新字段为默认值，promotion 为 None，合计为小计加运费。
    """
    _section_8(db)

    quote = quote_cart(db, SECTION_8_CART, lang="en", **WEST)

    lines = [_priced(line) for line in quote.lines]
    assert [(line.promotion_rule_id, line.promotion_item_discount_sen) for line in lines] == [
        (None, 0),
        (None, 0),
        (None, 0),
    ]
    assert quote.promotion is None
    assert quote.subtotal_sen == 8300
    assert quote.total_sen == 9100
    assert quote.can_place_order is True


# ---- 接口层 ----


def _post(client: TestClient) -> dict[str, object]:
    body = {
        "lines": [{"sku": item.sku, "quantity": item.quantity} for item in SECTION_8_CART],
        **WEST,
    }
    response = client.post(URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_api_returns_active_promotion_fields(
    db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 1 条「每次计价…都从数据库读取当时生效的活动」与验收「响应新增的字段照数据类原样输出
    （JSON 字段名同上）」：固定时钟在活动期间，响应的 promotion 逐字段为第 8 条算例的结果，
    各行带规则 ID 与商品级折扣，合计 5900，运费仍为未免的报价。
    """
    shop = _section_8(db)
    monkeypatch.setattr(checkout_api, "_utcnow", lambda: NOW)

    quote = _post(client)

    assert quote["promotion"] == {
        "promotion_id": shop.promotion_id,
        "item_discount_sen": 1900,
        "order_discount_sen": 500,
        "after_item_subtotal_sen": 6400,
        "spend_mode": "amount",
        "spend_tier_reached": 1,
        "spend_next_threshold_sen": 10000,
        "spend_next_value": 1200,
        "spend_next_gap_sen": 3600,
        "free_shipping_threshold_sen": 6000,
        "free_shipping_reached": True,
        "free_shipping_gap_sen": 0,
        "shipping_waived_sen": 800,
        "participates": True,
        "buy_x_get_y": [
            {
                "rule_id": shop.buy_x_get_y_rule_id,
                "buy_quantity": 2,
                "get_quantity": 1,
                "get_percent": 100,
                "units_in_scope": 3,
                "units_to_next": 3,
            }
        ],
    }
    assert _rule_pairs(quote["lines"]) == [
        (shop.item_rule_id, 1000),
        (shop.buy_x_get_y_rule_id, 0),
        (shop.buy_x_get_y_rule_id, 900),
    ]
    assert quote["total_sen"] == 5900
    assert quote["shipping"] == {"fee_sen": 800, "zone_code": "MY-W", "version": 1}


@pytest.mark.parametrize(
    "now",
    [
        pytest.param(STARTS - timedelta(seconds=1), id="not_started"),
        pytest.param(ENDS, id="ended"),
    ],
)
def test_api_without_active_promotion_returns_null(
    db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    """第 1 条「活动在已发布、开始时间 ≤ 当前时刻、当前时刻 < 实际结束时间…时生效」与第 10 条
    「没有生效活动时这些字段为空」：活动未开始与已结束时 promotion 为 null，各行两个新字段为默认
    值，合计为小计加运费。
    """
    _section_8(db)
    monkeypatch.setattr(checkout_api, "_utcnow", lambda: now)

    quote = _post(client)

    assert quote["promotion"] is None
    assert _rule_pairs(quote["lines"]) == [(None, 0), (None, 0), (None, 0)]
    assert quote["total_sen"] == 9100

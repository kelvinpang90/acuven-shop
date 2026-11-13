"""商品目录的活动价与角标字段、活动专题页的商品列表（SHOP-TASK-244）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 3、10 条（下称「第 N 条」）与
docs/UX.md 0.20「1. 全局框架」的商品卡规则、P03、P15。每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库按模型建表，接口的会话依赖换成测试自己的会话；接口模块的 _utcnow 固定为 NOW。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import catalog as catalog_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Product,
    ProductVariant,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
)
from app.models.promotion import (
    KIND_BUY_X_GET_Y,
    KIND_FREE_SHIPPING,
    KIND_ITEM_DISCOUNT,
    MODE_AMOUNT,
    MODE_PERCENT,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
)
from app.services.catalog import list_products
from app.services.promotions import PromoInputLine, active_promotion, price_lines

NOW = datetime(2026, 11, 13, 4, 0)
PRODUCTS = "/api/catalog/products"


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
def client(db: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(catalog_api, "_utcnow", lambda: NOW)
    app = create_app(Settings(_env_file=None))
    # 接口用测试自己的会话：flush 过的数据就看得到，不必提交。
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _category(db: Session, slug: str) -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=True))


def _variant(
    db: Session, product: Product, sku: str, price_sen: int, *, is_active: bool = True
) -> ProductVariant:
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        price_sen=price_sen,
        daily_initial_stock=10,
        available_stock=10,
        is_active=is_active,
    )
    return _add(db, variant)


def _product(
    db: Session,
    slug: str,
    category: Category,
    *prices: int,
    day: int = 1,
    **fields: object,
) -> Product:
    """可发布的商品：英文文案齐全，每个价格一个启用的 SKU（不给价格时一个 1990 仙的 SKU）。"""
    defaults = {
        "name_en": slug.title(),
        "description_en": f"The {slug}.",
        "is_active": True,
        "created_at": datetime(2026, 9, day),
    }
    product = _add(db, Product(category_id=category.id, slug=slug, **(defaults | fields)))
    for index, price_sen in enumerate(prices or (1990,)):
        _variant(db, product, f"{slug.upper()}-{index}", price_sen)
    return product


def _promotion(
    db: Session,
    *,
    status: str = STATUS_PUBLISHED,
    starts_at: datetime = datetime(2026, 11, 13),
    ends_at: datetime = datetime(2026, 11, 20),
    ended_early_at: datetime | None = None,
) -> Promotion:
    promotion = Promotion(
        name="Sale",
        status=status,
        starts_at=starts_at,
        ends_at=ends_at,
        ended_early_at=ended_early_at,
    )
    return _add(db, promotion)


def _rule(
    db: Session,
    promotion: Promotion,
    position: int,
    *,
    products: tuple[Product, ...] = (),
    categories: tuple[Category, ...] = (),
    **params: object,
) -> PromotionRule:
    rule = _add(db, PromotionRule(promotion_id=promotion.id, position=position, **params))
    for product in products:
        _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))
    for category in categories:
        _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))
    return rule


def _percent_off(
    db: Session, promotion: Promotion, position: int, percent: int, **scope: object
) -> PromotionRule:
    params = {"kind": KIND_ITEM_DISCOUNT, "discount_mode": MODE_PERCENT, "percent": percent}
    return _rule(db, promotion, position, **params, **scope)


def _amount_off(
    db: Session, promotion: Promotion, position: int, amount_sen: int, **scope: object
) -> PromotionRule:
    params = {"kind": KIND_ITEM_DISCOUNT, "discount_mode": MODE_AMOUNT, "amount_sen": amount_sen}
    return _rule(db, promotion, position, **params, **scope)


def _buy_x_get_y(
    db: Session,
    promotion: Promotion,
    position: int,
    buy: int,
    get: int,
    percent: int,
    **scope: object,
) -> PromotionRule:
    params = {
        "kind": KIND_BUY_X_GET_Y,
        "buy_quantity": buy,
        "get_quantity": get,
        "get_percent": percent,
    }
    return _rule(db, promotion, position, **params, **scope)


def _card(rule: PromotionRule) -> dict[str, object]:
    return {
        "rule_id": rule.id,
        "kind": rule.kind,
        "discount_mode": rule.discount_mode,
        "percent": rule.percent,
        "amount_sen": rule.amount_sen,
        "buy_quantity": rule.buy_quantity,
        "get_quantity": rule.get_quantity,
        "get_percent": rule.get_percent,
    }


def _listing(client: TestClient, **params: object) -> dict[str, object]:
    response = client.get(PRODUCTS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _items(client: TestClient, **params: object) -> dict[str, dict[str, object]]:
    return {item["slug"]: item for item in _listing(client, **params)["items"]}


def _slugs(client: TestClient, **params: object) -> list[str]:
    return [item["slug"] for item in _listing(client, **params)["items"]]


def _detail(client: TestClient, slug: str) -> dict[str, object]:
    response = client.get(f"{PRODUCTS}/{slug}")
    assert response.status_code == 200, response.text
    return response.json()


def test_percent_discount_gives_each_sku_sale_price_and_list_minimum(
    db: Session, client: TestClient
) -> None:
    """第 10 条「打折规则下各启用 SKU 的活动单价（列表与首页另返回最低活动单价，
    『起』的规则照旧）」；第 3 条「每件折扣 = 该 SKU 单价 × 百分比 ÷ 100」：
    八折（percent 20）下 2590 → 2072、
    2190 → 1752。停用的 SKU 再便宜也不算进最低活动单价；原价、最低单价与多规格标记不变；
    首页按 slug 取的商品卡同样带这两个字段。
    """
    tee = _product(db, "tee", _category(db, "apparel"), 2590, 2190)
    _variant(db, tee, "TEE-OFF", 500, is_active=False)
    rule = _percent_off(db, _promotion(db), 1, 20, products=(tee,))
    card = {
        "rule_id": rule.id,
        "kind": "item_discount",
        "discount_mode": "percent",
        "percent": 20,
        "amount_sen": None,
        "buy_quantity": None,
        "get_quantity": None,
        "get_percent": None,
    }

    item = _items(client)["tee"]
    featured = _items(client, slug="tee")["tee"]
    detail = _detail(client, "tee")

    assert item["promotion"] == card
    assert (item["min_price_sen"], item["has_multiple_variants"]) == (2190, True)
    assert item["min_sale_price_sen"] == 1752
    assert featured == item
    assert detail["promotion"] == card
    prices = [(v["sku"], v["price_sen"], v["sale_price_sen"]) for v in detail["variants"]]
    assert prices == [("TEE-0", 2590, 2072), ("TEE-1", 2190, 1752)]


def test_half_sen_rounds_up_and_matches_checkout(db: Session, client: TestClient) -> None:
    """第 3 条「按第 2 条的『四舍五入，恰好半仙进一仙』取整仙」与设计闸门「展示的活动价与计价
    同一算法」：九折下 1995 的折扣 199.5 进到 200（活动单价 1795，向下取整会是 1796），
    1985 的 198.5 进到 199（1786）；两者都等于计价（price_lines）对一件的活动价小计。
    """
    kitchen = _category(db, "kitchen")
    mug = _product(db, "mug", kitchen, 1995, 1985)
    _percent_off(db, _promotion(db), 1, 10, categories=(kitchen,))

    detail = _detail(client, "mug")

    assert [v["sale_price_sen"] for v in detail["variants"]] == [1795, 1786]
    assert _items(client)["mug"]["min_sale_price_sen"] == 1786
    promotion = active_promotion(db, NOW)
    for price_sen, sale_price_sen in [(1995, 1795), (1985, 1786)]:
        line = PromoInputLine(0, price_sen, 1, mug.id, kitchen.id)
        priced = price_lines(promotion, [line], shipping_fee_sen=None)
        assert priced.after_item_subtotal == sale_price_sen


def test_amount_off_larger_than_price_gives_zero(db: Session, client: TestClient) -> None:
    """第 3 条「每件直降……每件折扣取直降额与单价较小者」：直降 RM5 下 1990 → 1490，
    300 → 0（不为负）；最低活动单价为 0。角标为直降（discount_mode amount、amount_sen 500）。
    """
    socks = _product(db, "socks", _category(db, "apparel"), 1990, 300)
    rule = _amount_off(db, _promotion(db), 1, 500, products=(socks,))

    item = _items(client)["socks"]
    detail = _detail(client, "socks")

    assert item["promotion"] == _card(rule)
    card = item["promotion"]
    assert (card["discount_mode"], card["amount_sen"], card["percent"]) == ("amount", 500, None)
    assert (item["min_price_sen"], item["min_sale_price_sen"]) == (300, 0)
    assert [v["sale_price_sen"] for v in detail["variants"]] == [1490, 0]


def test_buy_x_get_y_gives_badge_parameters_only(db: Session, client: TestClient) -> None:
    """第 10 条「买 X 享 Y 只返回角标所需的 X、Y 与百分比，不改显示单价」；UX 0.20 商品卡
    「买 X 享 Y 为 [promo.badge_bxgy_free]……价格不变」：promotion 有 X、Y 与百分比，
    最低活动单价与各 SKU 的活动单价都为 null，原价照旧。
    """
    home = _category(db, "home")
    _product(db, "vase", home, 3000, 2500)
    _buy_x_get_y(db, _promotion(db), 1, 2, 1, 100, categories=(home,))

    item = _items(client)["vase"]
    detail = _detail(client, "vase")

    expected = {
        "rule_id": item["promotion"]["rule_id"],
        "kind": "buy_x_get_y",
        "discount_mode": None,
        "percent": None,
        "amount_sen": None,
        "buy_quantity": 2,
        "get_quantity": 1,
        "get_percent": 100,
    }
    assert item["promotion"] == detail["promotion"] == expected
    assert (item["min_price_sen"], item["min_sale_price_sen"]) == (2500, None)
    prices = [(v["price_sen"], v["sale_price_sen"]) for v in detail["variants"]]
    assert prices == [(3000, None), (2500, None)]


def test_rule_hits_by_category_or_by_product(db: Session, client: TestClient) -> None:
    """第 10 条「为每件商品返回命中的商品级规则」与第 3 条「适用范围为若干商品与若干分类……
    分类按商品当时所属分类判断」：kitchen 分类下的 mug 命中分类规则，显式选的 rake 命中
    商品规则，都不在范围里的 tee 为 null；tee 改到 kitchen 后即命中分类规则。
    """
    kitchen, garden, apparel = (_category(db, slug) for slug in ("kitchen", "garden", "apparel"))
    _product(db, "mug", kitchen)
    rake = _product(db, "rake", garden)
    tee = _product(db, "tee", apparel)
    promotion = _promotion(db)
    by_category = _percent_off(db, promotion, 1, 10, categories=(kitchen,))
    by_product = _buy_x_get_y(db, promotion, 2, 1, 1, 50, products=(rake,))

    items = _items(client)
    assert items["mug"]["promotion"] == _card(by_category)
    assert items["rake"]["promotion"] == _card(by_product)
    assert (items["tee"]["promotion"], items["tee"]["min_sale_price_sen"]) == (None, None)
    assert _detail(client, "tee")["promotion"] is None

    tee.category_id = kitchen.id
    db.flush()

    assert _items(client)["tee"]["promotion"] == _card(by_category)
    assert _detail(client, "tee")["promotion"] == _card(by_category)


def test_two_hits_take_the_rule_with_the_smaller_position(db: Session, client: TestClient) -> None:
    """第 3 条「之后商品改了分类而造成重叠的，计价时取排序数较小的规则」：mug 同时在排序 2 的
    分类规则（九折，先建、ID 较小）与排序 1 的商品规则（直降 RM1）里，取排序 1 的直降，
    活动单价也按它算（1990 → 1890）。
    """
    kitchen = _category(db, "kitchen")
    mug = _product(db, "mug", kitchen)
    promotion = _promotion(db)
    later = _percent_off(db, promotion, 2, 10, categories=(kitchen,))
    earlier = _amount_off(db, promotion, 1, 100, products=(mug,))
    assert later.id < earlier.id

    item = _items(client)["mug"]
    detail = _detail(client, "mug")

    assert item["promotion"] == detail["promotion"] == _card(earlier)
    assert item["min_sale_price_sen"] == 1890
    assert detail["variants"][0]["sale_price_sen"] == 1890


def test_sale_list_has_only_published_products_in_item_rule_scope(
    db: Session, client: TestClient
) -> None:
    """UX 0.20 P15「商品区列出生效活动全部商品级规则范围内的上架商品，排序与分页同 P02 的默认
    排序」；第 10 条「专题页列出生效活动全部商品级规则范围内的上架商品」：范围是所选分类下的
    商品加显式选的商品，未上架的不列；按 newest 排序与分页，total 为范围内的全部件数，
    其他筛选照常可用。不带 sale 时照旧列全部商品。
    """
    kitchen, garden, apparel = (_category(db, slug) for slug in ("kitchen", "garden", "apparel"))
    _product(db, "k1", kitchen, day=1)
    _product(db, "k2", kitchen, day=2)
    g1 = _product(db, "g1", garden, day=3)
    _product(db, "g2", garden, day=4)
    _product(db, "a1", apparel, day=5)
    _product(db, "k3", kitchen, day=6, is_active=False)
    promotion = _promotion(db)
    _percent_off(db, promotion, 1, 10, categories=(kitchen,))
    _buy_x_get_y(db, promotion, 2, 2, 1, 100, products=(g1,))
    _rule(db, promotion, 3, kind=KIND_FREE_SHIPPING, threshold_sen=0)

    first = _listing(client, sale="true", page_size=2)
    second = _listing(client, sale="true", page_size=2, page=2)

    assert _slugs(client, sale="true") == ["g1", "k2", "k1"]
    assert (first["total"], [item["slug"] for item in first["items"]]) == (3, ["g1", "k2"])
    assert (second["total"], [item["slug"] for item in second["items"]]) == (3, ["k1"])
    assert _slugs(client, sale="true", category="garden") == ["g1"]
    assert _slugs(client, sale="true", q="k2") == ["k2"]
    assert _slugs(client) == ["a1", "g2", "g1", "k2", "k1"]
    assert _slugs(client, sale="false") == ["a1", "g2", "g1", "k2", "k1"]


def test_sale_list_is_empty_without_an_item_rule_or_an_active_promotion(
    db: Session, client: TestClient
) -> None:
    """第 10 条「只有整单级规则时，专题页列出规则说明并引导到商品列表」「没有生效活动时这些字段
    为空」：没有生效活动、或活动只有整单级规则时，sale 列表都是空页（total 为 0）；
    服务层 sale_only 而不传 promotion 同样为空页。
    """
    kitchen = _category(db, "kitchen")
    _product(db, "mug", kitchen)

    assert _listing(client, sale="true") == {"total": 0, "page": 1, "page_size": 24, "items": []}
    empty = list_products(db, lang="en", sale_only=True)
    assert (empty.total, empty.items) == (0, [])

    _rule(db, _promotion(db), 1, kind=KIND_FREE_SHIPPING, threshold_sen=0)

    assert _listing(client, sale="true")["total"] == 0
    assert _items(client)["mug"]["promotion"] is None


def test_without_an_active_promotion_responses_are_as_before(
    db: Session, client: TestClient
) -> None:
    """第 10 条「没有生效活动时这些字段为空，前台不显示任何活动内容」：草稿、未开始、已结束与
    已提前结束的活动都覆盖 mug 的分类，列表与详情的新字段都为 null，其余字段与不传活动时
    服务层的结果相同。
    """
    kitchen = _category(db, "kitchen")
    _product(db, "mug", kitchen, 2590, 2190)
    promotions = [
        _promotion(db, status=STATUS_DRAFT),
        _promotion(db, starts_at=datetime(2026, 11, 14)),
        _promotion(db, starts_at=datetime(2026, 11, 1), ends_at=NOW),
        _promotion(
            db,
            starts_at=datetime(2026, 11, 10),
            ends_at=datetime(2026, 11, 20),
            ended_early_at=datetime(2026, 11, 12),
        ),
    ]
    for promotion in promotions:
        _percent_off(db, promotion, 1, 20, categories=(kitchen,))

    listing = _listing(client)
    detail = _detail(client, "mug")

    assert listing == jsonable_encoder(list_products(db, lang="en"))
    item = listing["items"][0]
    assert (item["promotion"], item["min_sale_price_sen"]) == (None, None)
    assert detail["promotion"] is None
    assert [v["sale_price_sen"] for v in detail["variants"]] == [None, None]


def test_responses_are_no_store_and_read_the_promotion_every_request(
    db: Session, client: TestClient
) -> None:
    """第 10 条「展示接口的响应带 no-store」与第 1 条「每次……展示都从数据库读取当时生效的活动，
    不缓存」：列表与详情的成功响应带 Cache-Control: no-store；活动加上后下一次请求即看到活动价。
    404 的格式照旧。
    """
    kitchen = _category(db, "kitchen")
    _product(db, "mug", kitchen)

    before = client.get(f"{PRODUCTS}/mug")
    assert before.headers["cache-control"] == "no-store"
    assert client.get(PRODUCTS).headers["cache-control"] == "no-store"
    assert before.json()["variants"][0]["sale_price_sen"] is None

    _percent_off(db, _promotion(db), 1, 20, categories=(kitchen,))

    assert _detail(client, "mug")["variants"][0]["sale_price_sen"] == 1592
    assert _items(client)["mug"]["min_sale_price_sen"] == 1592
    missing = client.get(f"{PRODUCTS}/no-such-product")
    assert (missing.status_code, missing.json()) == (404, {"detail": "product not found"})

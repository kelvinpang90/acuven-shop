"""每单限购：商品的限购件数列、计价接口按商品合计标出超出限购、商品详情返回限购件数。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「数据模型」的 Product / Variant 一行：
「商品级的每单限购件数（Product 上非空的整数，1–99，默认 10，不按规格分别设置）」；
「计价、优惠、积分与库存」第 8 条（下称第 8 条），以及 SHOP-TASK-016 验收标准里的具体规则。
每条测试的文档字符串引用它守住的那一句。
用 SQLite 内存库（打开外键检查）按模型建表，接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Category, Product, ProductVariant, ShippingRate

URL = "/api/checkout/quote"
LIMIT_CONSTRAINT = "CHECK constraint failed: ck_products_max_per_order_range"


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
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


def _product(db: Session, slug: str, **fields: object) -> Product:
    """可发布的商品（英文文案齐全，没有规格名）；SKU 另建。"""
    category = _add(db, Category(slug=f"{slug}-category", name_en="Things", is_active=True))
    values = {"name_en": slug.title(), "description_en": f"The {slug}.", "is_active": True}
    return _add(db, Product(category_id=category.id, slug=slug, **(values | fields)))


def _variant(db: Session, product: Product, sku: str, **fields: object) -> ProductVariant:
    defaults = {
        "price_sen": 1000,
        "daily_initial_stock": 50,
        "available_stock": 50,
        "is_active": True,
    }
    return _add(db, ProductVariant(product_id=product.id, sku=sku, **(defaults | fields)))


@dataclass
class _Shop:
    tee: Product
    red: ProductVariant
    blue: ProductVariant
    mug: Product
    mug_1: ProductVariant


def _shop(db: Session, *, tee_limit: int = 10, mug_limit: int = 10) -> _Shop:
    """两件商品：T 恤有 TEE-RED、TEE-BLUE 两个 SKU（各 1000 仙），马克杯有 MUG-1（500 仙）。
    库存都是 50，限购之内不会碰上库存不足。
    """
    tee = _product(db, "tee", max_per_order=tee_limit)
    red = _variant(db, tee, "TEE-RED")
    blue = _variant(db, tee, "TEE-BLUE")
    mug = _product(db, "mug", max_per_order=mug_limit)
    mug_1 = _variant(db, mug, "MUG-1", price_sen=500)
    return _Shop(tee, red, blue, mug, mug_1)


def _body(*lines: tuple[str, int], **fields: object) -> dict[str, object]:
    return {"lines": [{"sku": sku, "quantity": quantity} for sku, quantity in lines]} | fields


def _quote(client: TestClient, body: object) -> dict[str, object]:
    response = client.post(URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _statuses(quote: dict[str, object]) -> dict[str, str]:
    return {line["sku"]: line["status"] for line in quote["lines"]}


# ---- 数据模型 ----


def test_limit_defaults_to_10(db: Session) -> None:
    """Product / Variant 一行「每单限购件数（…非空的整数…默认 10）」：
    写商品时不给限购件数，库里由服务端默认值补成 10（直接从库里读回，不看 Python 对象）。
    """
    product = _product(db, "plain")

    stored = db.scalar(select(Product.max_per_order).where(Product.id == product.id))

    assert stored == 10


@pytest.mark.parametrize("limit", [0, 100, -1])
def test_limit_outside_1_to_99_is_rejected_by_the_check_constraint(
    db: Session,
    limit: int,
) -> None:
    """Product / Variant 一行「每单限购件数（…1–99…）」：0、100 与负数由显式命名的
    检查约束拦住，不靠应用层校验。
    """
    with pytest.raises(IntegrityError, match=LIMIT_CONSTRAINT):
        _product(db, "edge", max_per_order=limit)


@pytest.mark.parametrize("limit", [1, 99])
def test_limit_1_and_99_are_accepted(db: Session, limit: int) -> None:
    """Product / Variant 一行「1–99」的对照：两端的 1 与 99 都能写入并读回。"""
    product = _product(db, "edge", max_per_order=limit)

    stored = db.scalar(select(Product.max_per_order).where(Product.id == product.id))

    assert stored == limit


# ---- 计价：按商品合计 ----


def test_lines_of_one_product_are_summed_against_its_limit(
    db: Session,
    client: TestClient,
) -> None:
    """第 8 条「同一商品所有 SKU 在一张购物车或订单中的件数合计不得超过该商品的每单限购件数」
    「把该商品的各行标为超出限购」：T 恤限购 5，红 2 + 蓝 3 恰好等于 5 两行都正常；
    红 3 + 蓝 3 超过 5，两行都标为超出限购（各自都没超）。马克杯限购 1、买 1 件，
    不与 T 恤合计，始终正常。
    """
    _shop(db, tee_limit=5, mug_limit=1)

    at_limit = _quote(client, _body(("TEE-RED", 2), ("MUG-1", 1), ("TEE-BLUE", 3)))
    over = _quote(client, _body(("TEE-RED", 3), ("MUG-1", 1), ("TEE-BLUE", 3)))

    assert _statuses(at_limit) == {"TEE-RED": "ok", "MUG-1": "ok", "TEE-BLUE": "ok"}
    assert _statuses(over) == {"TEE-RED": "over_limit", "MUG-1": "ok", "TEE-BLUE": "over_limit"}


def test_single_line_over_the_limit_is_flagged(db: Session, client: TestClient) -> None:
    """第 8 条「件数合计不得超过该商品的每单限购件数」「不因超出限购拒绝整个请求」：
    只有一行时合计就是这一行，件数 4 超过限购 3 即超出限购，接口仍是 200；件数 3 正常。
    """
    _shop(db, tee_limit=3)

    assert _statuses(_quote(client, _body(("TEE-RED", 4)))) == {"TEE-RED": "over_limit"}
    assert _statuses(_quote(client, _body(("TEE-RED", 3)))) == {"TEE-RED": "ok"}


def test_default_limit_of_10_applies_to_the_quote(db: Session, client: TestClient) -> None:
    """Product / Variant 一行「默认 10」与第 8 条：没设限购件数的商品按 10 计，
    同一商品两行 5 + 5 正常，5 + 6 超出限购。
    """
    tee = _product(db, "tee")
    _variant(db, tee, "TEE-RED")
    _variant(db, tee, "TEE-BLUE")

    at_limit = _quote(client, _body(("TEE-RED", 5), ("TEE-BLUE", 5)))
    over = _quote(client, _body(("TEE-RED", 5), ("TEE-BLUE", 6)))

    assert _statuses(at_limit) == {"TEE-RED": "ok", "TEE-BLUE": "ok"}
    assert _statuses(over) == {"TEE-RED": "over_limit", "TEE-BLUE": "over_limit"}


def test_over_limit_takes_priority_over_short_stock(db: Session, client: TestClient) -> None:
    """第 8 条「标注优先级为不可购买、超出限购、库存不足」「限购与库存各自独立生效」：
    红只剩 2 件、限购 3，买 4 件既超限购又缺货，标为超出限购（缺货仍给出可用库存）；
    买 3 件不超限购只缺货，标为库存不足。
    """
    shop = _shop(db, tee_limit=3)
    shop.red.available_stock = 2
    db.flush()

    (both,) = _quote(client, _body(("TEE-RED", 4)))["lines"]
    (short,) = _quote(client, _body(("TEE-RED", 3)))["lines"]

    assert (both["status"], both["available_stock"]) == ("over_limit", 2)
    assert (short["status"], short["available_stock"]) == ("insufficient_stock", 2)


def test_short_stock_line_counts_toward_the_product_total(
    db: Session,
    client: TestClient,
) -> None:
    """SHOP-TASK-016 验收「把同一已发布商品的所有正常或库存不足的行按件数合计」：
    蓝缺货（可用 0）的 2 件也计入合计，红 2 + 蓝 2 超过限购 3，两行都标为超出限购。
    """
    shop = _shop(db, tee_limit=3)
    shop.blue.available_stock = 0
    db.flush()

    quote = _quote(client, _body(("TEE-RED", 2), ("TEE-BLUE", 2)))

    assert _statuses(quote) == {"TEE-RED": "over_limit", "TEE-BLUE": "over_limit"}


def test_unavailable_lines_do_not_count_toward_any_total(
    db: Session,
    client: TestClient,
) -> None:
    """SHOP-TASK-016 验收「不可购买的行不计入任何商品的合计」与第 8 条优先级的第一位：
    T 恤限购 3，红 3 件正常；同一商品的停用 SKU 买 5 件与不存在的 SKU 都是不可购买，
    只回 SKU、件数与状态，不把红挤成超出限购。
    """
    shop = _shop(db, tee_limit=3)
    _variant(db, shop.tee, "TEE-OLD", is_active=False)

    quote = _quote(client, _body(("TEE-OLD", 5), ("TEE-RED", 3), ("NO-SUCH-SKU", 9)))

    assert _statuses(quote) == {
        "TEE-OLD": "unavailable",
        "TEE-RED": "ok",
        "NO-SUCH-SKU": "unavailable",
    }
    assert quote["lines"][0] == {"sku": "TEE-OLD", "quantity": 5, "status": "unavailable"}


def test_over_limit_lines_are_priced_and_block_the_order(
    db: Session,
    client: TestClient,
) -> None:
    """SHOP-TASK-016 验收「超出限购的行与库存不足的行一样返回商品信息、单价与行小计
    并计入商品小计」「有任何超出限购的行时不可下单」与第 8 条「存在任何非正常行时不可下单」；
    「不改运费、合计与参考外币的计算」：合计仍是商品小计加运费。
    对照：同样带国家、减到限购之内即可下单。
    """
    _shop(db, tee_limit=3)
    _add(db, ShippingRate(zone_type="country", zone_code="SG", fee_sen=2000, version=1))

    sg = {"country_code": "SG"}

    over = _quote(client, _body(("TEE-RED", 2), ("MUG-1", 1), ("TEE-BLUE", 2), **sg))
    within = _quote(client, _body(("TEE-RED", 1), ("MUG-1", 1), ("TEE-BLUE", 2), **sg))

    red = over["lines"][0]
    assert red["status"] == "over_limit"
    assert red["product_slug"] == "tee"
    assert red["name"] == {"text": "Tee", "english_fallback": False}
    assert (red["unit_price_sen"], red["line_subtotal_sen"]) == (1000, 2000)
    assert over["subtotal_sen"] == 2000 + 500 + 2000
    assert over["total_sen"] == 4500 + 2000
    assert over["can_place_order"] is False
    assert within["can_place_order"] is True


def test_every_priced_line_carries_max_per_order(db: Session, client: TestClient) -> None:
    """SHOP-TASK-016 验收「所有返回商品信息的行（正常、库存不足、超出限购）另返回该商品的
    max_per_order」「不可购买的行仍只返回 SKU、件数与状态」；第 8 条「返回限购件数」。
    T 恤限购 3（超出限购）、马克杯限购 7（缺货）、另一件限购 4（正常）各给自己商品的值。
    """
    shop = _shop(db, tee_limit=3, mug_limit=7)
    shop.mug_1.available_stock = 0
    db.flush()
    cap = _product(db, "cap", max_per_order=4)
    _variant(db, cap, "CAP-1")

    quote = _quote(
        client,
        _body(("TEE-RED", 4), ("MUG-1", 1), ("CAP-1", 1), ("NO-SUCH-SKU", 1)),
    )

    tee_line, mug_line, cap_line, missing = quote["lines"]
    assert (tee_line["status"], tee_line["max_per_order"]) == ("over_limit", 3)
    assert (mug_line["status"], mug_line["max_per_order"]) == ("insufficient_stock", 7)
    assert (cap_line["status"], cap_line["max_per_order"]) == ("ok", 4)
    assert missing == {"sku": "NO-SUCH-SKU", "quantity": 1, "status": "unavailable"}


# ---- 请求校验 ----


def test_line_quantity_99_is_accepted_and_100_is_rejected(
    db: Session,
    client: TestClient,
) -> None:
    """第 8 条「每行件数为 1–99 的整数」「计价接口不因超出限购拒绝整个请求」：
    99 件照常计价（超出默认限购 10，标为超出限购），100 件是 422。
    """
    _shop(db)

    accepted = client.post(URL, json=_body(("TEE-RED", 99)))
    rejected = client.post(URL, json=_body(("TEE-RED", 100)))

    assert accepted.status_code == 200
    assert _statuses(accepted.json()) == {"TEE-RED": "over_limit"}
    assert rejected.status_code == 422


# ---- 商品详情 ----


def test_product_detail_returns_max_per_order(db: Session, client: TestClient) -> None:
    """SHOP-TASK-016 验收「商品详情接口新增字段 max_per_order（该商品的每单限购件数）；
    商品列表与分类接口不变」：详情给出该商品自己的限购件数，列表项与分类项没有这个字段。
    """
    _shop(db, tee_limit=7)

    detail = client.get("/api/catalog/products/tee").json()
    mug = client.get("/api/catalog/products/mug").json()
    items = client.get("/api/catalog/products").json()["items"]
    categories = client.get("/api/catalog/categories").json()

    assert detail["max_per_order"] == 7
    assert mug["max_per_order"] == 10
    assert items and all("max_per_order" not in item for item in items)
    assert categories and all("max_per_order" not in category for category in categories)

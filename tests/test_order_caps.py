"""整单件数上限与整单金额上限（计价与下单）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 8 条与 docs/HANDOFF.md 0.74
（SHOP-TASK-178）：所有行（含不可购买的行）的件数合计不超过 2,000；商品小计加运费（优惠券与积分
抵扣之前）不超过 99,999,999 仙，还没有运费报价时只按商品小计判断。计价接口不因此拒绝整个请求，
而返回标记与上限、不可下单；下单时服务端重新校验，超出即拒绝整单。每条测试（参数化的测试是每个
用例）的文档字符串写明它守住的第 8 条原句。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。接口的会话依赖换成每个请求
一个绑定同一内存库的会话；测试自己的会话写完数据先提交。会员下单在服务层调用
place_member_order（不经会员接口，免去会话与限流的替身），时间固定传入 NOW。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    DemoFxRate,
    Member,
    Order,
    OrderItemUnit,
    Product,
    ProductVariant,
    ShippingRate,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_PAID
from app.services.checkout import (
    MAX_ORDER_AMOUNT_SEN,
    MAX_ORDER_UNITS,
    CartItem,
    quote_cart,
)
from app.services.member_ordering import (
    MemberOrderRequest,
    OrderNotPlaceable,
    place_member_order,
    quote_member_cart,
)
from app.services.points import earn_points

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

GUEST_URL = "/api/orders/guest"

# 马来西亚时间 2026-10-12 12:00。
NOW = datetime(2026, 10, 12, 4, 0)
EARNED_AT = NOW - timedelta(days=1)

PHONE_RAW = "012-345 6789"
PHONE_E164 = "+60123456789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"

MY_SHIPPING = 800
SG_SHIPPING = 2000
# A、B、C 三件商品各一个 SKU：1 仙、库存 2000、每单限购 1000，件数凑到上限也都是正常行。
UNIT_SKUS = ("A-SKU", "B-SKU", "C-SKU")
UNIT_STOCK = 2000

_PHONE_SEQUENCE = itertools.count(100_000_000)
_KEY_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
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
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session


@pytest.fixture
def client(engine: Engine) -> TestClient:
    app = create_app(Settings(_env_file=None))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    # https：游客下单的 cookie 带 Secure，TestClient 只在 https 下保存并回送它。
    return TestClient(app, base_url="https://testserver")


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _product(db: Session, category: Category, slug: str, max_per_order: int) -> Product:
    product = Product(
        category_id=category.id,
        slug=slug,
        name_en=slug.title(),
        description_en=f"{slug}.",
        is_active=True,
        max_per_order=max_per_order,
    )
    return _add(db, product)


def _variant(db: Session, product: Product, sku: str, price_sen: int, stock: int) -> None:
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        price_sen=price_sen,
        daily_initial_stock=stock,
        available_stock=stock,
        is_active=True,
    )
    _add(db, variant)


@pytest.fixture
def catalog(db: Session) -> None:
    """A、B、C 见 UNIT_SKUS；BIG 商品 BIG-SKU 库存 10、每单限购 10，单价由各测试设定。
    运费：西马 MY-W 行 800（MY-10 按它计价）、SG 国家行 2000；SG 有演示汇率。写完即提交。
    """
    category = _add(db, Category(slug="goods", name_en="Goods", is_active=True))
    for sku in UNIT_SKUS:
        product = _product(db, category, sku.split("-")[0].lower(), 1000)
        _variant(db, product, sku, 1, UNIT_STOCK)
    big = _product(db, category, "big", 10)
    _variant(db, big, "BIG-SKU", 1, 10)
    _add(db, ShippingRate(zone_type="my_region", zone_code="MY-W", fee_sen=MY_SHIPPING, version=1))
    _add(db, ShippingRate(zone_type="country", zone_code="SG", fee_sen=SG_SHIPPING, version=3))
    fx = DemoFxRate(
        country_code="SG",
        currency_code="SGD",
        currency_decimals=2,
        rate=Decimal("0.310000"),
        version=5,
    )
    _add(db, fx)
    db.commit()


def _big_price(db: Session, price_sen: int) -> None:
    variant = db.scalars(select(ProductVariant).where(ProductVariant.sku == "BIG-SKU")).one()
    variant.price_sen = price_sen
    db.commit()


def _items(*lines: tuple[str, int]) -> list[CartItem]:
    return [CartItem(sku=sku, quantity=quantity) for sku, quantity in lines]


# 合计 2001 件、全是正常行：只因件数上限不可下单。
UNITS_OVER = (("A-SKU", 1000), ("B-SKU", 1000), ("C-SKU", 1))


def _stocks(db: Session) -> dict[str, int]:
    db.expire_all()
    stmt = select(ProductVariant.sku, ProductVariant.available_stock)
    return dict(db.execute(stmt).tuples().all())


def _count(db: Session, model: type) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


def _member(db: Session) -> Member:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=EARNED_AT))
    db.commit()
    return member


def _earn(db: Session, member: Member, points: int) -> None:
    """该会员此前已支付的一张订单，经 earn_points 给一个昨天取得的批次，提交。"""
    order = Order(
        order_number="0000000000000001",
        status=STATUS_PAID,
        subtotal_sen=6000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=800,
        total_sen=6800,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key="prior-key-1",
        request_fingerprint="0f" * 32,
        created_at=EARNED_AT,
        payment_expires_at=EARNED_AT + timedelta(minutes=15),
        paid_at=EARNED_AT,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    _add(db, order)
    assert earn_points(db, member.id, order.id, points, EARNED_AT) is not None
    db.commit()


def _member_request(lines: list[CartItem]) -> MemberOrderRequest:
    return MemberOrderRequest(
        lines=tuple(lines),
        phone=PHONE_E164,
        name=NAME,
        address=ADDRESS,
        postal_code=POSTAL,
        country_code="MY",
        state_code="MY-10",
        region=None,
    )


def _guest_payload(lines: list[CartItem]) -> dict[str, object]:
    return {
        "lines": [{"sku": item.sku, "quantity": item.quantity} for item in lines],
        "phone": PHONE_RAW,
        "phone_region": "MY",
        "name": NAME,
        "address": ADDRESS,
        "postal_code": POSTAL,
        "country_code": "MY",
        "state_code": "MY-10",
    }


def _key() -> str:
    return f"caps-key-{next(_KEY_SEQUENCE)}"


# ---------------------------------------------------------------------------
# 件数上限
# ---------------------------------------------------------------------------


def test_constants_match_design() -> None:
    """第 8 条「件数合计不超过 2,000」「不得超过 99,999,999 仙」：两个整单上限常量。"""
    assert MAX_ORDER_UNITS == 2000
    assert MAX_ORDER_AMOUNT_SEN == 99_999_999


def test_2000_units_is_not_over_and_can_place_order(db: Session, catalog: None) -> None:
    """第 8 条「一张购物车或订单所有行……的件数合计不超过 2,000」：恰好 2000 件不超出，
    各行正常且给了收货国家时可下单。
    """
    quote = quote_cart(db, _items(("A-SKU", 1000), ("B-SKU", 1000)), lang="en", country_code="SG")

    assert quote.unit_limit == 2000
    assert quote.units_over_limit is False
    assert quote.can_place_order is True


def test_2001_units_is_over_and_cannot_place_order(db: Session, catalog: None) -> None:
    """第 8 条「合计超过 2,000 件时，计价接口同样不拒绝整个请求，而返回整单超出件数上限的标记
    与上限件数，不可下单」「整单件数标记……与各行的标注互不影响」：2001 件全是正常行时仍照常
    逐行计价，各行标注不变，只是不可下单。
    """
    quote = quote_cart(db, _items(*UNITS_OVER), lang="en", country_code="SG")

    assert quote.unit_limit == 2000
    assert quote.units_over_limit is True
    assert quote.amount_over_limit is False
    assert [line.status for line in quote.lines] == ["ok", "ok", "ok"]
    assert quote.subtotal_sen == 2001
    assert quote.can_place_order is False


@pytest.mark.parametrize(
    ("unavailable_quantity", "over"),
    [pytest.param(1, False, id="2000-with-unavailable"), pytest.param(2, True, id="2001")],
)
def test_unavailable_lines_count_toward_units(
    db: Session, catalog: None, unavailable_quantity: int, over: bool
) -> None:
    """第 8 条「所有行（含不可购买的行）的件数合计不超过 2,000」「按全部行计数」：
    不可购买的行的件数照计，1999 + 1 不超出、1999 + 2 超出；该行仍只标为不可购买。
    """
    items = _items(("A-SKU", 1000), ("B-SKU", 999), ("NO-SUCH-SKU", unavailable_quantity))

    quote = quote_cart(db, items, lang="en")

    assert quote.units_over_limit is over
    assert [line.status for line in quote.lines] == ["ok", "ok", "unavailable"]
    assert quote.can_place_order is False


# ---------------------------------------------------------------------------
# 金额上限
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "over"),
    [pytest.param(0, False, id="exactly-limit"), pytest.param(1, True, id="one-sen-over")],
)
def test_amount_limit_counts_subtotal_plus_shipping(
    db: Session, catalog: None, extra: int, over: bool
) -> None:
    """第 8 条「商品小计加运费（优惠券与积分抵扣之前）不得超过 99,999,999 仙」「有运费报价后
    按小计加运费判断」「超限时不返回参考外币金额」：小计加 SG 运费恰为 99,999,999 时不超出、
    给出参考外币且可下单；多 1 仙即超出，参考外币为空，不可下单。
    """
    _big_price(db, MAX_ORDER_AMOUNT_SEN - SG_SHIPPING + extra)

    quote = quote_cart(db, _items(("BIG-SKU", 1)), lang="en", country_code="SG")

    assert quote.total_sen == MAX_ORDER_AMOUNT_SEN + extra
    assert quote.amount_limit_sen == 99_999_999
    assert quote.amount_over_limit is over
    assert quote.units_over_limit is False
    assert (quote.fx_reference is None) is over
    assert quote.can_place_order is not over
    assert [line.status for line in quote.lines] == ["ok"]


@pytest.mark.parametrize(
    ("price", "over"),
    [
        pytest.param(MAX_ORDER_AMOUNT_SEN, False, id="subtotal-exactly-limit"),
        pytest.param(MAX_ORDER_AMOUNT_SEN + 1, True, id="subtotal-one-sen-over"),
    ],
)
def test_amount_without_shipping_quote_counts_subtotal_only(
    db: Session, catalog: None, price: int, over: bool
) -> None:
    """第 8 条「还没有运费报价时只按商品小计判断」：没给收货国家时小计恰为 99,999,999 不超出，
    多 1 仙超出；两种情况都没有合计与参考外币，也都不可下单（没给收货国家）。
    """
    _big_price(db, price)

    quote = quote_cart(db, _items(("BIG-SKU", 1)), lang="en")

    assert quote.subtotal_sen == price
    assert quote.shipping is None
    assert quote.amount_over_limit is over
    assert quote.fx_reference is None
    assert quote.can_place_order is False


def test_same_subtotal_goes_over_once_shipping_is_quoted(db: Session, catalog: None) -> None:
    """第 8 条「还没有运费报价时只按商品小计判断，有运费报价后按小计加运费判断」：
    小计恰为上限时不给国家不超出，给了国家加上运费即超出。
    """
    _big_price(db, MAX_ORDER_AMOUNT_SEN)

    without = quote_cart(db, _items(("BIG-SKU", 1)), lang="en")
    with_shipping = quote_cart(db, _items(("BIG-SKU", 1)), lang="en", country_code="SG")

    assert without.amount_over_limit is False
    assert with_shipping.amount_over_limit is True
    assert with_shipping.fx_reference is None
    assert with_shipping.can_place_order is False


def test_quote_endpoint_returns_flags_instead_of_rejecting(
    client: TestClient, catalog: None
) -> None:
    """第 8 条「计价接口同样不拒绝整个请求，而返回整单超出件数上限的标记与上限件数」：
    计价接口 200，响应带标记与上限，不可下单。
    """
    body = {
        "lines": [{"sku": sku, "quantity": quantity} for sku, quantity in UNITS_OVER],
        "country_code": "SG",
    }

    response = client.post("/api/checkout/quote", json=body)

    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["unit_limit"] == 2000
    assert quote["units_over_limit"] is True
    assert quote["amount_limit_sen"] == 99_999_999
    assert quote["amount_over_limit"] is False
    assert quote["can_place_order"] is False


# ---------------------------------------------------------------------------
# 游客下单
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", ["units", "amount"])
def test_guest_order_over_a_cap_is_409_and_writes_nothing(
    client: TestClient, db: Session, catalog: None, cap: str
) -> None:
    """第 8 条件数上限「下单时服务端重新校验，超出即拒绝整单」与金额上限「下单时服务端重新校验，
    超出即拒绝整单」：游客下单 409 order_not_placeable，不建订单、不动库存。
    金额用例：小计恰为上限，加 MY-10 运费即超出。
    """
    if cap == "units":
        lines = _items(*UNITS_OVER)
    else:
        _big_price(db, MAX_ORDER_AMOUNT_SEN)
        lines = _items(("BIG-SKU", 1))
    before = _stocks(db)

    response = client.post(
        GUEST_URL, json=_guest_payload(lines), headers={"Idempotency-Key": _key()}
    )

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_placeable"}
    assert _count(db, Order) == 0
    assert _count(db, OrderItemUnit) == 0
    assert _stocks(db) == before


def test_guest_order_at_the_amount_limit_is_placed(
    client: TestClient, db: Session, catalog: None
) -> None:
    """第 8 条「商品小计加运费……不得超过 99,999,999 仙」的对照：小计加运费恰为上限时照常下单，
    说明上一条的 409 来自上限而不是别的校验。
    """
    _big_price(db, MAX_ORDER_AMOUNT_SEN - MY_SHIPPING)
    payload = _guest_payload(_items(("BIG-SKU", 1)))

    response = client.post(GUEST_URL, json=payload, headers={"Idempotency-Key": _key()})

    assert response.status_code == 201, response.text
    assert response.json()["total_sen"] == MAX_ORDER_AMOUNT_SEN


# ---------------------------------------------------------------------------
# 会员下单与会员计价
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", ["units", "amount"])
def test_member_order_over_a_cap_raises_and_writes_nothing(
    db: Session, catalog: None, cap: str
) -> None:
    """第 8 条件数上限与金额上限各自的「下单时服务端重新校验，超出即拒绝整单」：
    place_member_order 抛 OrderNotPlaceable，不建订单、不动库存。
    """
    if cap == "units":
        lines = _items(*UNITS_OVER)
    else:
        _big_price(db, MAX_ORDER_AMOUNT_SEN)
        lines = _items(("BIG-SKU", 1))
    member = _member(db)
    member_id = member.id
    before = _stocks(db)

    with pytest.raises(OrderNotPlaceable):
        place_member_order(db, member_id, _member_request(lines), _key(), NOW)

    assert _count(db, Order) == 0
    assert _count(db, OrderItemUnit) == 0
    assert _stocks(db) == before


@pytest.mark.parametrize("cap", ["units", "amount"])
def test_member_quote_over_a_cap_skips_coupon_and_points(
    db: Session, catalog: None, cap: str
) -> None:
    """第 8 条「返回整单超出件数上限的标记与上限件数，不可下单」「返回整单超出金额上限的标记与
    上限金额……禁止下单」：会员计价照有不正常行的写法不判定券与积分——券为空、抵扣与上限为 0、
    积分不接受，可用余额照给；四个上限字段取自 quote_cart。
    """
    if cap == "units":
        lines = _items(*UNITS_OVER)
    else:
        _big_price(db, MAX_ORDER_AMOUNT_SEN)
        lines = _items(("BIG-SKU", 1))
    member = _member(db)
    _earn(db, member, 300)

    quote = quote_member_cart(db, member.id, lines, "MY", "MY-10", "NOSUCHCODE", 5, NOW)

    assert quote.unit_limit == 2000
    assert quote.units_over_limit is (cap == "units")
    assert quote.amount_limit_sen == 99_999_999
    assert quote.amount_over_limit is (cap == "amount")
    assert quote.can_place_order is False
    assert quote.coupon_applicable is None
    assert quote.coupon_discount_sen == 0
    assert quote.points_cap == 0
    assert quote.points_redeemed == 0
    assert quote.points_earned == 0
    assert quote.points_accepted is False
    assert quote.available_points == 300
    if cap == "amount":
        assert quote.fx_reference is None


def test_member_quote_under_the_caps_still_judges_coupon_and_points(
    db: Session, catalog: None
) -> None:
    """上一条的对照（第 8 条「件数合计不超过 2,000」）：恰好 2000 件时照常判定券与积分——
    不存在的券为不可用（而不是空），5 积分在上限内被接受。
    """
    member = _member(db)
    _earn(db, member, 300)
    lines = _items(("A-SKU", 1000), ("B-SKU", 1000))

    quote = quote_member_cart(db, member.id, lines, "MY", "MY-10", "NOSUCHCODE", 5, NOW)

    assert quote.units_over_limit is False
    assert quote.can_place_order is True
    assert quote.coupon_applicable is False
    assert quote.points_accepted is True
    assert quote.points_redeemed == 5

"""游客下单接入节日促销活动（SHOP-TASK-235）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 4、7、8、14 条（下称「第 N 条」），
以及 SHOP-TASK-235 写定的验收标准（下称「验收」）。每条测试的文档字符串引用它守住的那一句。

服务层直接调用 place_guest_order，以固定的 now 代替时钟。用 SQLite 内存库按模型建表，
每个连接打开外键检查，订单引用活动与规则的外键因此也被检查。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Category,
    Order,
    OrderItem,
    OrderItemUnit,
    Product,
    ProductVariant,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    ShippingRate,
)
from app.services import ordering
from app.services.checkout import CartItem, quote_cart
from app.services.ordering import GuestOrderRequest, GuestOrderResult, place_guest_order
from app.services.promotions import active_promotion

STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)
NOW = STARTS + timedelta(hours=1)
# 第 8 条算例：S0 = 8300，西马运费 800。
SUBTOTAL = 8300
SHIPPING = 800
SECTION_8_CART = (CartItem("A", 2), CartItem("B", 2), CartItem("C", 1))


@pytest.fixture
def engine() -> Iterator[Engine]:
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


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _product(db: Session, category: Category, slug: str, price_sen: int) -> Product:
    """没有规格名的已发布商品，唯一的 SKU 为 slug 的大写，库存 10。"""
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=slug,
            name_en=slug.upper(),
            description_en=f"{slug}.",
            is_active=True,
        ),
    )
    _add(
        db,
        ProductVariant(
            product_id=product.id,
            sku=slug.upper(),
            price_sen=price_sen,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        ),
    )
    return product


@dataclass(frozen=True)
class _Shop:
    item_rule_id: int
    buy_x_get_y_rule_id: int
    promotion_id: int


def _section_8(db: Session, *, with_promotion: bool = True) -> _Shop:
    """第 8 条算例的商品与活动：A RM25.00（分类「礼品」）、B RM12.00 与 C RM9.00（分类「家居」）；
    A 打 8 折；「家居」买 2 享 1、享受的件 100%；满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费。
    活动在 STARTS 到 ENDS 之间生效；西马运费 RM8.00。写完即提交。
    with_promotion 为假时只建商品与运费，没有任何活动。
    """
    _add(db, ShippingRate(zone_type="my_region", zone_code="MY-W", fee_sen=SHIPPING, version=1))
    gifts = _add(db, Category(slug="gifts", name_en="Gifts", is_active=True))
    home = _add(db, Category(slug="home", name_en="Home", is_active=True))
    a = _product(db, gifts, "a", 2500)
    _product(db, home, "b", 1200)
    _product(db, home, "c", 900)
    if not with_promotion:
        db.commit()
        return _Shop(0, 0, 0)

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
    db.commit()
    return _Shop(item_rule.id, bxgy.id, promotion.id)


def _request(lines: tuple[CartItem, ...] = SECTION_8_CART) -> GuestOrderRequest:
    """寄西马 MY-10 的游客请求；短信验证开关没有设置，视为关闭。"""
    return GuestOrderRequest(
        lines=lines,
        phone="+60123456789",
        name="Guest",
        address="1 Road",
        postal_code="50000",
        country_code="MY",
        state_code="MY-10",
        region=None,
    )


def _place(
    db: Session,
    now: datetime = NOW,
    *,
    lines: tuple[CartItem, ...] = SECTION_8_CART,
    key: str | None = None,
) -> GuestOrderResult:
    return place_guest_order(db, _request(lines), key or f"key-{uuid.uuid4().hex}", None, now)


def _order(db: Session) -> Order:
    db.expire_all()
    return db.scalars(select(Order)).one()


def _promotion_columns(order: Order) -> tuple[Any, ...]:
    return (
        order.promotion_id,
        order.promotion_item_discount_sen,
        order.promotion_order_discount_sen,
        order.shipping_fee_sen,
        order.shipping_waived_sen,
        order.total_sen,
    )


def _units(db: Session) -> list[tuple[int, int, int, int | None]]:
    """按订单行序、件序的（原价, 活动优惠, 现金实付, 命中的规则）。"""
    db.expire_all()
    stmt = (
        select(OrderItemUnit)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .order_by(OrderItem.line_index, OrderItemUnit.unit_index)
    )
    return [
        (u.original_price_sen, u.promotion_discount_sen, u.cash_paid_sen, u.promotion_rule_id)
        for u in db.scalars(stmt)
    ]


def _stocks(db: Session) -> list[int]:
    db.expire_all()
    stmt = select(ProductVariant.available_stock).order_by(ProductVariant.id)
    return list(db.scalars(stmt))


def _end_early(db: Session, shop: _Shop, at: datetime) -> None:
    promotion = db.get(Promotion, shop.promotion_id)
    assert promotion is not None
    promotion.ended_early_at = at
    db.commit()


def test_section_8_order_snapshot(db: Session) -> None:
    """第 7 条「订单记活动 ID…、商品级活动优惠合计、整单级活动优惠合计、实际运费与免去的运费」与
    「应付 = 商品小计 − 活动优惠合计 − 券折扣 − 积分抵扣 + 实际运费」；第 8 条「S1 达到 RM60，
    运费为 0，免去 RM8.00」：游客不用券与积分，商品级 1900、整单级 500、实际运费 0、免去 800、
    应付 8300 − 1900 − 500 + 0 = 5900；摘要的运费与应付同订单。
    """
    shop = _section_8(db)

    result = _place(db)

    order = _order(db)
    assert order.subtotal_sen == SUBTOTAL
    assert _promotion_columns(order) == (shop.promotion_id, 1900, 500, 0, 800, 5900)
    assert (order.coupon_discount_sen, order.points_redeemed, order.points_earned) == (0, 0, 0)
    assert result.created is True
    assert (result.order.subtotal_sen, result.order.shipping_fee_sen) == (SUBTOTAL, 0)
    assert result.order.total_sen == 5900


def test_section_8_unit_snapshots(db: Session) -> None:
    """第 7 条「每件的活动优惠 = 该件的商品级折扣 + 分摊到的整单级优惠」「逐件快照记活动优惠与命中的
    商品级规则 ID」「每件现金实付 = 原价 − 活动优惠 − 分摊券额 − 分摊积分额」；第 8 条「每件活动
    优惠依次为 656、656、94、94、900 仙」：现金实付 1844、1844、1106、1106、0；B 两件不享受买 2 享 1
    的折扣，仍记该行命中的买 X 享 Y 规则（验收「不论这一件是否享受折扣」）。
    """
    shop = _section_8(db)

    _place(db)

    item, bxgy = shop.item_rule_id, shop.buy_x_get_y_rule_id
    assert _units(db) == [
        (2500, 656, 1844, item),
        (2500, 656, 1844, item),
        (1200, 94, 1106, bxgy),
        (1200, 94, 1106, bxgy),
        (900, 900, 0, bxgy),
    ]


@pytest.mark.parametrize(
    ("with_promotion", "now"),
    [
        pytest.param(False, NOW, id="no_promotion"),
        pytest.param(True, STARTS - timedelta(seconds=1), id="not_started"),
        pytest.param(True, ENDS, id="ended"),
    ],
)
def test_without_active_promotion_order_is_as_before(
    db: Session, with_promotion: bool, now: datetime
) -> None:
    """第 1 条「活动在已发布、开始时间 ≤ 当前时刻、当前时刻 < 实际结束时间…时生效」与第 7 条
    「订单记活动 ID（不参加活动…时为空）」：没有活动、活动未开始与已结束时，订单不记活动、三项
    活动金额为 0，运费 800、应付 8300 + 800；逐件活动优惠为 0、规则为空、现金实付即原价。
    """
    _section_8(db, with_promotion=with_promotion)

    _place(db, now)

    assert _promotion_columns(_order(db)) == (None, 0, 0, SHIPPING, 0, SUBTOTAL + SHIPPING)
    assert _units(db) == [
        (2500, 0, 2500, None),
        (2500, 0, 2500, None),
        (1200, 0, 1200, None),
        (1200, 0, 1200, None),
        (900, 0, 900, None),
    ]


def test_no_benefit_means_order_without_promotion(db: Session) -> None:
    """第 4 条「本单所有规则都没有带来优惠（商品级折扣、整单级优惠与免去的运费都为 0）时，视为不
    参加活动：不记活动」：只买 B 一件 RM12.00，在买 2 享 1 的范围内但凑不满，S1 未达任一门槛；
    订单不记活动、应付 1200 + 800，这一件也不记命中的规则（验收「不参加活动时两列为 0 与空」）。
    """
    _section_8(db)

    _place(db, lines=(CartItem("B", 1),))

    assert _promotion_columns(_order(db)) == (None, 0, 0, SHIPPING, 0, 1200 + SHIPPING)
    assert _units(db) == [(1200, 0, 1200, None)]


def test_promotion_ended_early_after_quote_is_not_applied(db: Session) -> None:
    """第 14 条「访客在购物车与结账页看到的是当时的报价，下单时服务端按下单事务读到的生效活动
    重新计价」：访客计价时活动生效、合计 5900；之后活动提前结束，下单不记活动，按 8300 + 800。
    """
    shop = _section_8(db)
    seen = quote_cart(
        db,
        list(SECTION_8_CART),
        lang="en",
        country_code="MY",
        state_code="MY-10",
        promotion=active_promotion(db, NOW),
    )
    assert seen.total_sen == 5900
    _end_early(db, shop, NOW)

    _place(db, NOW)

    assert _promotion_columns(_order(db)) == (None, 0, 0, SHIPPING, 0, SUBTOTAL + SHIPPING)


def test_promotion_ending_after_order_keeps_snapshot_and_replay(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 14 条「活动在待支付期间结束或提前结束，不改已下单订单的金额」「下单…的状态与幂等规则
    不变」与验收「按幂等键重放原订单不重新计价、不重读活动」：下单后活动提前结束，订单与逐件快照
    不变；同键再提交返回原订单与原快照，不读活动、不重新计价、不再扣库存。
    """
    shop = _section_8(db)
    key = "key-replay"
    first = _place(db, NOW, key=key)
    units = _units(db)
    stocks = _stocks(db)
    _end_early(db, shop, NOW + timedelta(minutes=1))

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("replay must not read the promotion or price again")

    monkeypatch.setattr(ordering, "active_promotion", _forbidden)
    monkeypatch.setattr(ordering, "quote_cart", _forbidden)
    again = _place(db, NOW + timedelta(minutes=2), key=key)

    assert again.created is False
    assert again.order == first.order
    assert db.scalar(select(func.count()).select_from(Order)) == 1
    assert _promotion_columns(_order(db)) == (shop.promotion_id, 1900, 500, 0, 800, 5900)
    assert _units(db) == units
    assert _stocks(db) == stocks

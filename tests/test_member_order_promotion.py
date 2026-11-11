"""会员下单接入节日促销活动（按取更优的方案写订单，SHOP-TASK-237）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 6、7、8、9、14 条
（下称「第 N 条」）与「计价、优惠、积分与库存」第 2 条（下称「计价第 N 条」），
以及 SHOP-TASK-237 写定的验收标准（下称「验收」）。
每条测试的文档字符串引用它守住的那一句。

服务层直接调用 place_member_order，以固定的 now 代替时钟；支付经 submit_payment，
退款经 submit_refund 与 approve_refund（沿用现有服务，不改它们）。
用 SQLite 内存库按模型建表，每个连接打开外键检查；引擎设置同
tests/test_refund_review_points.py（关掉 pysqlite 的事务处理、由引擎发 BEGIN），
退款审核的保存点才和 MySQL 上一样。下单函数先回滚此前的事务，所以准备的数据都先提交。
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AdminAccount,
    Category,
    Coupon,
    CouponUse,
    Member,
    Order,
    OrderItem,
    OrderItemUnit,
    PointEntry,
    PointLot,
    Product,
    ProductVariant,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    RefundRequest,
    ShippingRate,
)
from app.models.coupon import TYPE_FIXED, TYPE_PERCENT, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_PAID
from app.models.points import KIND_REDEEM, KIND_REFUND_RETURN, KIND_REFUND_REVERSAL
from app.services import member_ordering, payment, refund_review
from app.services.checkout import CartItem
from app.services.coupons import check_coupon
from app.services.member_ordering import (
    PLAN_PROMOTION,
    CouponUnavailable,
    MemberCheckoutQuote,
    MemberOrderRequest,
    MemberOrderResult,
    PointsUnavailable,
    place_member_order,
    quote_member_cart,
)
from app.services.points import available_points, earn_points
from app.services.refunds import RefundLineRequest, submit_refund

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)
NOW = STARTS + timedelta(hours=1)
SHIPPING = 800
SECTION_8_CART = (CartItem("A", 2), CartItem("B", 2), CartItem("C", 1))
# 第 8 条：1,000 积分。
POINTS = 1000

_PHONE_SEQUENCE = itertools.count(500_000_000)
_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # 关掉驱动的事务处理、由引擎发 BEGIN，保存点才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

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


def _category(db: Session, slug: str) -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=True))


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
            max_per_order=10,
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


def _west_rate(db: Session) -> None:
    """西马运费 RM8.00。"""
    _add(db, ShippingRate(zone_type="my_region", zone_code="MY-W", fee_sen=SHIPPING, version=1))


def _promotion(db: Session, *, coupon_combinable: bool, points_combinable: bool) -> Promotion:
    return _add(
        db,
        Promotion(
            name="Sale",
            status="published",
            starts_at=STARTS,
            ends_at=ENDS,
            coupon_combinable=coupon_combinable,
            points_combinable=points_combinable,
        ),
    )


def _spend_rule(
    db: Session, promotion: Promotion, position: int, tiers: list[tuple[int, int]]
) -> None:
    """满额减（每档减固定金额）：tiers 为 (门槛, 减的仙数)。"""
    spend = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id,
            position=position,
            kind="spend_discount",
            discount_mode="amount",
        ),
    )
    for tier_position, (threshold_sen, amount_sen) in enumerate(tiers, start=1):
        tier = PromotionSpendTier(
            rule_id=spend.id,
            position=tier_position,
            threshold_sen=threshold_sen,
            amount_sen=amount_sen,
        )
        _add(db, tier)


@dataclass(frozen=True)
class _Shop:
    gifts_id: int
    promotion_id: int
    item_rule_id: int
    buy_x_get_y_rule_id: int


def _section_8(
    db: Session, *, coupon_combinable: bool = True, points_combinable: bool = True
) -> _Shop:
    """第 8 条算例的商品与活动：A RM25.00（分类「礼品」）、B RM12.00 与 C RM9.00（分类「家居」）；
    A 打 8 折；「家居」买 2 享 1、享受的件 100%；满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费。
    两个同用开关默认都打开；活动在 STARTS 到 ENDS 之间生效。写完即提交。
    """
    _west_rate(db)
    gifts = _category(db, "gifts")
    home = _category(db, "home")
    a = _product(db, gifts, "a", 2500)
    _product(db, home, "b", 1200)
    _product(db, home, "c", 900)
    promotion = _promotion(
        db, coupon_combinable=coupon_combinable, points_combinable=points_combinable
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
    _spend_rule(db, promotion, 3, [(5000, 500), (10000, 1200)])
    _add(
        db,
        PromotionRule(
            promotion_id=promotion.id, position=4, kind="free_shipping", threshold_sen=6000
        ),
    )
    db.commit()
    return _Shop(gifts.id, promotion.id, item_rule.id, bxgy.id)


def _member(db: Session) -> int:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    member = Member(phone=phone, status=MEMBER_ACTIVE, created_at=NOW - timedelta(days=100))
    member_id = _add(db, member).id
    db.commit()
    return member_id


def _prior_order(db: Session, member_id: int) -> Order:
    """该会员昨天已支付的一张订单，作为积分批次的来源。"""
    n = next(_ORDER_SEQUENCE)
    paid_at = NOW - timedelta(days=1)
    order = Order(
        order_number=f"{n:016d}",
        status=STATUS_PAID,
        subtotal_sen=6000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=800,
        total_sen=6800,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"prior-key-{n:08d}",
        request_fingerprint="0f" * 32,
        created_at=paid_at,
        payment_expires_at=paid_at + timedelta(minutes=15),
        paid_at=paid_at,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    return _add(db, order)


def _member_with_points(db: Session) -> int:
    """有一个昨天取得的 1,000 积分批次的会员；提交。"""
    member_id = _member(db)
    order = _prior_order(db, member_id)
    assert earn_points(db, member_id, order.id, POINTS, NOW - timedelta(days=1)) is not None
    db.commit()
    return member_id


def _coupon(db: Session, **overrides: Any) -> Coupon:
    """默认为第 8 条的 10% 券 TENOFF（最低消费 RM50），十天前起、三十天后止，
    两个上限都不限；提交。
    """
    values: dict[str, Any] = {
        "code": "TENOFF",
        "coupon_type": TYPE_PERCENT,
        "amount_off_sen": None,
        "percent_off": 10,
        "min_subtotal_sen": 5000,
        "starts_at": NOW - timedelta(days=10),
        "ends_at": NOW + timedelta(days=30),
        "total_use_limit": None,
        "per_member_use_limit": None,
        "is_active": True,
        "created_at": NOW - timedelta(days=10),
    }
    coupon = _add(db, Coupon(**(values | overrides)))
    db.commit()
    return coupon


def _request(**overrides: Any) -> MemberOrderRequest:
    """第 8 条首例：10% 券与 1,000 积分，寄西马 MY-10。"""
    request = MemberOrderRequest(
        lines=SECTION_8_CART,
        phone="+60123456789",
        name="Member",
        address="1 Road",
        postal_code="50000",
        country_code="MY",
        state_code="MY-10",
        region=None,
        coupon_code="TENOFF",
        points_to_redeem=POINTS,
    )
    return replace(request, **overrides)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _place(
    db: Session, member_id: int, now: datetime = NOW, *, key: str | None = None, **overrides: Any
) -> MemberOrderResult:
    return place_member_order(db, member_id, _request(**overrides), key or _key(), now)


def _quote(db: Session, member_id: int) -> MemberCheckoutQuote:
    """第 8 条首例的会员计价（寄西马 MY-10）。"""
    return quote_member_cart(
        db, member_id, list(SECTION_8_CART), "MY", "MY-10", "TENOFF", POINTS, NOW
    )


def _order(db: Session, order_number: str) -> Order:
    db.expire_all()
    return db.scalars(select(Order).where(Order.order_number == order_number)).one()


def _columns(order: Order) -> tuple[Any, ...]:
    """（活动、商品级、整单级、券、积分、实际运费、免去的运费、应付、获得积分）。"""
    return (
        order.promotion_id,
        order.promotion_item_discount_sen,
        order.promotion_order_discount_sen,
        order.coupon_discount_sen,
        order.points_redeemed,
        order.shipping_fee_sen,
        order.shipping_waived_sen,
        order.total_sen,
        order.points_earned,
    )


def _units(db: Session, order_id: int) -> list[tuple[Any, ...]]:
    """按行序、件序的（原价、活动优惠、券、积分、现金实付、获得积分、命中的规则）。"""
    db.expire_all()
    stmt = (
        select(OrderItemUnit)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.line_index, OrderItemUnit.unit_index)
    )
    return [
        (
            u.original_price_sen,
            u.promotion_discount_sen,
            u.coupon_discount_sen,
            u.points_discount,
            u.cash_paid_sen,
            u.points_earned,
            u.promotion_rule_id,
        )
        for u in db.scalars(stmt)
    ]


def _coupon_uses(db: Session, order_id: int) -> list[tuple[str, int]]:
    db.expire_all()
    stmt = select(CouponUse.status, CouponUse.coupon_discount_sen).where(
        CouponUse.order_id == order_id
    )
    return [tuple(row) for row in db.execute(stmt)]


def _redeemed(db: Session, order_id: int) -> int:
    stmt = select(func.coalesce(func.sum(PointEntry.points), 0)).where(
        PointEntry.order_id == order_id, PointEntry.kind == KIND_REDEEM
    )
    return -int(db.scalar(stmt) or 0)


def _order_count(db: Session) -> int:
    db.expire_all()
    return int(db.scalar(select(func.count()).select_from(Order)) or 0)


def _set_promotion(db: Session, shop: _Shop, **values: Any) -> None:
    promotion = db.get(Promotion, shop.promotion_id)
    assert promotion is not None
    for name, value in values.items():
        setattr(promotion, name, value)
    db.commit()


# ---------------------------------------------------------------------------
# 第 8 条首例：取甲
# ---------------------------------------------------------------------------


def test_section_8_order_takes_promotion_with_coupon_and_points(db: Session) -> None:
    """第 7 条「订单记活动 ID…、商品级活动优惠合计、整单级活动优惠合计、
    实际运费与免去的运费」与第 8 条「券折 RM5.90」「1,000 积分抵 RM10.00」
    「应付 RM43.10 = 83.00 − 24.00 − 5.90 − 10.00 + 0；获得 43 积分」：
    订单商品级 1900、整单级 500、券 590、积分 1000、实际运费 0、免去 800、应付 4310。
    验收「取甲且 use_coupon 为 True 时以活动后商品额调用 reserve_coupon」：
    用券记录预占 590，1,000 积分预占。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)

    result = _place(db, member_id)

    assert result.created is True
    order = _order(db, result.order.order_number)
    assert order.subtotal_sen == 8300
    assert _columns(order) == (shop.promotion_id, 1900, 500, 590, 1000, 0, 800, 4310, 43)
    summary = result.order
    assert (summary.coupon_discount_sen, summary.points_redeemed) == (590, 1000)
    assert (summary.shipping_fee_sen, summary.total_sen, summary.points_earned) == (0, 4310, 43)
    assert _coupon_uses(db, order.id) == [(USE_RESERVED, 590)]
    assert _redeemed(db, order.id) == 1000
    assert available_points(db, member_id, NOW) == 0


def test_section_8_unit_snapshots(db: Session) -> None:
    """第 7 条「每件的活动优惠 = 该件的商品级折扣 + 分摊到的整单级优惠」
    「逐件快照记活动优惠与命中的商品级规则 ID」与第 8 条
    「每件活动优惠依次为 656、656、94、94、900 仙」
    「券折 RM5.90，分摊为 184、184、111、111、0 仙」「分摊为 313、313、187、187、0 仙」
    「五件现金实付依次 RM13.47、RM13.47、RM8.08、RM8.08、RM0.00」
    「获得 43 积分，分给 14、13、8、8、0」：与 SHOP-TASK-232 测试的数字相同。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)

    result = _place(db, member_id)

    item, bxgy = shop.item_rule_id, shop.buy_x_get_y_rule_id
    assert _units(db, _order(db, result.order.order_number).id) == [
        (2500, 656, 184, 313, 1347, 14, item),
        (2500, 656, 184, 313, 1347, 13, item),
        (1200, 94, 111, 187, 808, 8, bxgy),
        (1200, 94, 111, 187, 808, 8, bxgy),
        (900, 900, 0, 0, 0, 0, bxgy),
    ]


def test_section_8_payment_awards_43_points(db: Session) -> None:
    """第 8 条「获得 43 积分，分给 14、13、8、8、0」与
    第 14 条「下单、支付…的状态与幂等规则不变」：模拟支付成功后按订单快照发放
    43 积分（本单的批次 43，可用余额 0 + 43），用券记录核销。
    """
    _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    result = _place(db, member_id)
    order_id = _order(db, result.order.order_number).id

    paid_at = NOW + timedelta(minutes=1)
    payment.submit_payment(db, order_id, "card", "success", _key(), paid_at, actor="member")

    db.expire_all()
    lots = db.scalars(select(PointLot.points).where(PointLot.order_id == order_id)).all()
    assert list(lots) == [43]
    assert available_points(db, member_id, paid_at) == 43
    assert _coupon_uses(db, order_id) == [(USE_USED, 590)]


def test_section_9_refund_first_b(db: Session, engine: Engine) -> None:
    """第 9 条「上例只退第一件 B：模拟退现金 RM8.08，返还 187 积分，追回 8 积分」与
    「照『计价、优惠、积分与库存』第 4、5 条按逐件快照退」：支付后经现有的
    submit_refund 申请第二行 1 件，金额 808；经现有的 approve_refund 批准，
    返还 187、追回 8。
    """
    _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    result = _place(db, member_id)
    order_id = _order(db, result.order.order_number).id
    paid_at = NOW + timedelta(minutes=1)
    payment.submit_payment(db, order_id, "card", "success", _key(), paid_at, actor="member")
    admin = _add(
        db,
        AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            created_at=NOW - timedelta(days=1),
            password_updated_at=NOW - timedelta(days=1),
        ),
    )
    admin_id = admin.id
    db.commit()

    refund_at = NOW + timedelta(hours=1)
    outcome, created = submit_refund(
        db, order_id, [RefundLineRequest(1, 1)], _key(), refund_at, actor=ACTOR_MEMBER
    )
    assert created is True
    assert outcome.amount_sen == 808

    with Session(engine) as review:
        request_id = review.scalars(
            select(RefundRequest.id).where(RefundRequest.order_id == order_id)
        ).one()
        refund_review.approve_refund(review, request_id, admin_id, None, _key(), refund_at)
        review.commit()

    db.expire_all()
    stmt = select(PointEntry.kind, PointEntry.points).where(
        PointEntry.refund_request_id == request_id
    )
    rows = db.execute(stmt).all()
    returned = sum(points for kind, points in rows if kind == KIND_REFUND_RETURN)
    reversed_ = -sum(points for kind, points in rows if kind == KIND_REFUND_REVERSAL)
    assert (returned, reversed_) == (187, 8)


# ---------------------------------------------------------------------------
# 按方案不用的券与积分
# ---------------------------------------------------------------------------


def test_coupon_not_combinable_writes_no_coupon_use(db: Session) -> None:
    """第 6 条「开关关闭的那一项不用、不预占」与第 8 条「同一购物车若活动关闭了券同用，
    甲（活动，不用券）的积分前应付为 RM59.00，乙…为 RM82.70，取甲」；
    验收「按方案不用的券不写用券记录、不占用次数」：订单记活动、券 0、积分 1000、
    应付 4900；没有用券记录，每人限用一次的券仍可再用。
    """
    shop = _section_8(db, coupon_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db, per_member_use_limit=1)

    result = _place(db, member_id)

    order = _order(db, result.order.order_number)
    assert _columns(order) == (shop.promotion_id, 1900, 500, 0, 1000, 0, 800, 4900, 49)
    assert _coupon_uses(db, order.id) == []
    assert _redeemed(db, order.id) == 1000
    assert check_coupon(db, "TENOFF", member_id, 8300, NOW).ok


def test_points_not_combinable_only_does_not_reserve_points(db: Session) -> None:
    """第 6 条「积分按价值计入比较的口径（100 积分 = RM1），所以只关闭积分同用时，
    参加活动的订单不能用积分」与验收「按方案不用的积分不预占」：订单记活动、
    券 590 照常预占、积分 0、应付 5310、获得 53；没有抵扣流水，可用余额仍为 1000。
    """
    shop = _section_8(db, points_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db)

    result = _place(db, member_id)

    order = _order(db, result.order.order_number)
    assert _columns(order) == (shop.promotion_id, 1900, 500, 590, 0, 0, 800, 5310, 53)
    assert _coupon_uses(db, order.id) == [(USE_RESERVED, 590)]
    assert _redeemed(db, order.id) == 0
    assert available_points(db, member_id, NOW) == POINTS
    assert [u[3] for u in _units(db, order.id)] == [0, 0, 0, 0, 0]


def test_points_not_combinable_ignores_points_over_balance(db: Session) -> None:
    """验收「积分按方案不用时照常下单（不抛…PointsUnavailable）」：只关闭积分同用、
    会员所填积分超过可用余额时，甲不用积分，照常下单、不预占。
    """
    _section_8(db, points_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db)

    result = _place(db, member_id, points_to_redeem=POINTS + 1)

    order = _order(db, result.order.order_number)
    assert (order.points_redeemed, order.total_sen) == (0, 5310)
    assert available_points(db, member_id, NOW) == POINTS


# ---------------------------------------------------------------------------
# 取乙
# ---------------------------------------------------------------------------


def test_example_d_takes_no_promotion_and_reserves_coupon(db: Session) -> None:
    """第 8 条「购物车只有不在任何商品级规则里的 D 1 件 RM200.00…券为直减 RM30，
    活动关闭券同用：甲为…RM188.00，乙为…RM178.00，取乙（本单不记活动、不免运费）」
    与第 7 条「订单记活动 ID（不参加活动或取了乙方案时为空）」：订单不记活动、
    活动金额 0、运费 800、券 3000 照常预占、应付 17800；这一件不记活动优惠与规则。
    """
    shop = _section_8(db, coupon_combinable=False)
    gifts = db.get(Category, shop.gifts_id)
    assert gifts is not None
    _product(db, gifts, "d", 20000)
    db.commit()
    member_id = _member(db)
    _coupon(
        db,
        code="RM30OFF",
        coupon_type=TYPE_FIXED,
        amount_off_sen=3000,
        percent_off=None,
        min_subtotal_sen=0,
    )

    result = _place(
        db, member_id, lines=(CartItem("D", 1),), coupon_code="RM30OFF", points_to_redeem=0
    )

    order = _order(db, result.order.order_number)
    assert _columns(order) == (None, 0, 0, 3000, 0, SHIPPING, 0, 17800, 170)
    assert _coupon_uses(db, order.id) == [(USE_RESERVED, 3000)]
    assert _units(db, order.id) == [(20000, 0, 3000, 0, 17000, 170, None)]


def test_third_example_takes_no_promotion_and_reserves_coupon(db: Session) -> None:
    """第 8 条第三例「甲的活动后商品额 RM99.00 够不到券的门槛，
    积分前应付为 99.00 + 8.00 = RM107.00；乙为 100.00 − 50.00 + 8.00 = RM58.00，取乙」：
    订单不记活动、券 5000 照常预占（最低消费按原价 10000 判断）、运费 800、应付 5800。
    """
    _west_rate(db)
    _product(db, _category(db, "misc"), "e", 10000)
    promotion = _promotion(db, coupon_combinable=True, points_combinable=True)
    _spend_rule(db, promotion, 1, [(10000, 100)])
    db.commit()
    member_id = _member(db)
    _coupon(
        db,
        code="RM50OFF",
        coupon_type=TYPE_FIXED,
        amount_off_sen=5000,
        percent_off=None,
        min_subtotal_sen=10000,
    )

    result = _place(
        db, member_id, lines=(CartItem("E", 1),), coupon_code="RM50OFF", points_to_redeem=0
    )

    order = _order(db, result.order.order_number)
    assert _columns(order) == (None, 0, 0, 5000, 0, SHIPPING, 0, 5800, 50)
    assert _coupon_uses(db, order.id) == [(USE_RESERVED, 5000)]
    assert _units(db, order.id) == [(10000, 0, 5000, 0, 5000, 50, None)]


# ---------------------------------------------------------------------------
# 券或积分本身不成立
# ---------------------------------------------------------------------------


def test_unknown_coupon_is_rejected_even_when_plan_would_not_use_it(db: Session) -> None:
    """验收「券或积分本身不成立时照现在抛 CouponUnavailable…、不建订单」：
    活动关闭券同用（甲不用券）时，券代码查不到仍整单拒绝，不建订单、不预占积分。
    """
    _section_8(db, coupon_combinable=False)
    member_id = _member_with_points(db)

    with pytest.raises(CouponUnavailable):
        _place(db, member_id, coupon_code="NOSUCH")

    assert _order_count(db) == 1  # 只有积分来源的那张订单
    assert available_points(db, member_id, NOW) == POINTS


def test_points_over_balance_are_rejected_when_plan_uses_points(db: Session) -> None:
    """验收「券或积分本身不成立时照现在抛…PointsUnavailable、不建订单」：
    取甲且活动与积分同用时，所填积分超过可用余额即整单拒绝，不写用券记录。
    """
    _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)

    with pytest.raises(PointsUnavailable):
        _place(db, member_id, points_to_redeem=POINTS + 1)

    assert _order_count(db) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(CouponUse)) == 0


# ---------------------------------------------------------------------------
# 时点与幂等
# ---------------------------------------------------------------------------


def test_promotion_ended_early_after_quote_is_not_applied(db: Session) -> None:
    """第 14 条「下单时服务端按下单事务读到的生效活动重新计价」与
    验收「计价后活动结束、提前结束…都以下单事务读到的为准」：计价时取甲、合计 4310；
    之后活动提前结束，订单不记活动，券按原价折 830、积分 1000、运费 800，
    应付 8300 − 830 − 1000 + 800 = 7270。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    quote = _quote(db, member_id)
    assert (quote.promotion_plan, quote.total_sen) == (PLAN_PROMOTION, 4310)
    _set_promotion(db, shop, ended_early_at=NOW)

    result = _place(db, member_id)

    order = _order(db, result.order.order_number)
    assert _columns(order) == (None, 0, 0, 830, 1000, SHIPPING, 0, 7270, 64)
    assert _coupon_uses(db, order.id) == [(USE_RESERVED, 830)]


def test_switch_changed_after_quote_follows_order_transaction(db: Session) -> None:
    """第 6 条末句「下单时服务端按同一规则重算，不信任前台的选择」与
    验收「开关被改…以下单事务读到的为准」：计价时两个开关都打开、甲用券；
    下单前券同用被关闭，订单仍取甲而不用券，不写用券记录。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    quote = _quote(db, member_id)
    assert quote.coupon_discount_sen == 590
    _set_promotion(db, shop, coupon_combinable=False)

    result = _place(db, member_id)

    order = _order(db, result.order.order_number)
    assert _columns(order) == (shop.promotion_id, 1900, 500, 0, 1000, 0, 800, 4900, 49)
    assert _coupon_uses(db, order.id) == []


def test_replay_does_not_price_again(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """第 14 条「活动在待支付期间结束或提前结束，不改已下单订单的金额」与
    验收「按幂等键重放原订单不重新计价」：下单后活动提前结束；同键再提交返回原订单，
    不读活动、不重新计价，订单、快照、用券记录与积分预占都不变。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    key = _key()
    first = _place(db, member_id, key=key)
    order_id = _order(db, first.order.order_number).id
    units = _units(db, order_id)
    _set_promotion(db, shop, ended_early_at=NOW + timedelta(minutes=1))

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("replay must not read the promotion or price again")

    monkeypatch.setattr(member_ordering, "active_promotion", _forbidden)
    monkeypatch.setattr(member_ordering, "quote_cart", _forbidden)
    again = _place(db, member_id, NOW + timedelta(minutes=2), key=key)

    assert again.created is False
    assert again.order == first.order
    assert _order_count(db) == 2  # 积分来源的那张与本单
    order = _order(db, first.order.order_number)
    assert _columns(order) == (shop.promotion_id, 1900, 500, 590, 1000, 0, 800, 4310, 43)
    assert _units(db, order_id) == units
    assert _coupon_uses(db, order_id) == [(USE_RESERVED, 590)]
    assert _redeemed(db, order_id) == 1000

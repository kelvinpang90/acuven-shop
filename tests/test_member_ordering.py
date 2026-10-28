"""会员结账的计价与下单规则。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1 至 4、6、8 条、「失败、并发
与重试」第 1 条与「权限与资料保护」第 1、3 条，以及 docs/HANDOFF.md 0.47 记录的 Kelvin 2026-10-11
决定（下单时券或积分已不可用即整单拒绝，不建订单、不预留库存；会员订单的获得积分在下单时写入
整单与逐件快照，模拟支付成功时才发放）。每条测试（参数化的测试是每个用例）写明它守住的设计或
决定原句。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）；券按模型建行，积分经
SHOP-TASK-086 的 earn_points 发放，已用的券额度经 SHOP-TASK-085 的 reserve_coupon 预占。
下单函数先回滚此前的事务，所以测试准备的数据都先提交。内存库经 StaticPool 只有一个连接，
pysqlite 在第一条写语句前才开始事务，所以在下单函数还只做过查询时，用第二个会话写入并提交，
就能模拟同键并发请求先提交。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE：
会员行锁下的排队只在 MySQL 上成立，这里只验证锁下的判定与写入。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Category,
    Coupon,
    CouponUse,
    DemoFxRate,
    Member,
    Order,
    OrderAccessGrant,
    OrderAccessSession,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    PointEntry,
    PointLot,
    Product,
    ProductVariant,
    ShippingRate,
)
from app.models.coupon import TYPE_FIXED, TYPE_PERCENT, USE_RESERVED
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_NOT_CLAIMABLE,
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
)
from app.models.points import KIND_REDEEM
from app.services import member_ordering
from app.services.checkout import CartItem
from app.services.coupons import REASON_TOTAL_EXHAUSTED, CouponCheck, reserve_coupon
from app.services.member_ordering import (
    CouponUnavailable,
    IdempotencyConflict,
    MemberNotActive,
    MemberOrderRequest,
    OrderNotPlaceable,
    PointsUnavailable,
    member_request_fingerprint,
    place_member_order,
    quote_member_cart,
)
from app.services.order_rules import generate_order_number
from app.services.points import InsufficientPoints, available_points, earn_points
from app.services.shipping import convert_sen

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

# 马来西亚时间 2026-10-12 12:00。
NOW = datetime(2026, 10, 12, 4, 0)
EARNED_AT = NOW - timedelta(days=1)
STARTS = datetime(2026, 9, 30, 16, 0)
ENDS = datetime(2026, 10, 31, 15, 59, 59)
FINGERPRINT = "0f" * 32

PHONE = "+60123456789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"

# 第 4 条算例：A 商品 2 件、各 RM10；B 商品 1 件、RM5。寄马来西亚 MY-10，运费 RM8。
EXAMPLE_LINES = (CartItem(sku="A-SKU", quantity=2), CartItem(sku="B-SKU", quantity=1))
SUBTOTAL = 2500
SHIPPING = 800
SG_SHIPPING = 2000
SG_RATE = Decimal("0.310000")
# 固定金额 RM30，大于算例的商品小计 RM25。
BIG_FIXED_COUPON: dict[str, Any] = {
    "code": "BIGOFF",
    "coupon_type": TYPE_FIXED,
    "amount_off_sen": 3000,
    "percent_off": None,
}

_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)
_KEY_SEQUENCE = itertools.count(1)


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


@dataclass(frozen=True)
class _Catalog:
    a: int
    b: int


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _variant(db: Session, product: Product, sku: str, price_sen: int, stock: int) -> int:
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        price_sen=price_sen,
        daily_initial_stock=stock,
        available_stock=stock,
        is_active=True,
    )
    return _add(db, variant).id


@pytest.fixture
def catalog(db: Session) -> _Catalog:
    """A 商品 A-SKU 1000 仙、库存 5、每单限购 10；B 商品 B-SKU 500 仙、库存 3、每单限购 2。
    运费：西马 MY-W 行 800（MY-10 按它计价）、SG 国家行 2000；SG 有演示汇率。写完即提交。
    """
    category = _add(db, Category(slug="goods", name_en="Goods", is_active=True))
    product_a = Product(
        category_id=category.id, slug="a", name_en="A", description_en="A.", is_active=True
    )
    _add(db, product_a)
    product_b = Product(
        category_id=category.id,
        slug="b",
        name_en="B",
        description_en="B.",
        is_active=True,
        max_per_order=2,
    )
    _add(db, product_b)
    a = _variant(db, product_a, "A-SKU", 1000, 5)
    b = _variant(db, product_b, "B-SKU", 500, 3)
    _add(db, ShippingRate(zone_type="my_region", zone_code="MY-W", fee_sen=SHIPPING, version=1))
    _add(db, ShippingRate(zone_type="country", zone_code="SG", fee_sen=SG_SHIPPING, version=3))
    fx = DemoFxRate(
        country_code="SG", currency_code="SGD", currency_decimals=2, rate=SG_RATE, version=5
    )
    _add(db, fx)
    db.commit()
    return _Catalog(a=a, b=b)


def _member(db: Session) -> Member:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=EARNED_AT))
    db.commit()
    return member


@pytest.fixture
def member(db: Session, catalog: _Catalog) -> Member:
    return _member(db)


def _delete_member(db: Session, member: Member) -> None:
    """按注销后的样子改会员行：清空手机号与密码哈希、状态 deleted、写注销时间。"""
    member.status = MEMBER_DELETED
    member.phone = None
    member.password_hash = None
    member.deleted_at = NOW
    db.commit()


def _prior_order(db: Session, member_id: int) -> Order:
    """该会员此前已支付的一张订单，供发放积分或预占券额度。"""
    n = next(_ORDER_SEQUENCE)
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
        idempotency_key=f"prior-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=EARNED_AT,
        payment_expires_at=EARNED_AT + timedelta(minutes=15),
        paid_at=EARNED_AT,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    return _add(db, order)


def _earn(db: Session, member: Member, points: int) -> None:
    """经 earn_points 给该会员一个昨天取得的批次，提交。"""
    order = _prior_order(db, member.id)
    assert earn_points(db, member.id, order.id, points, EARNED_AT) is not None
    db.commit()


def _coupon(db: Session, **overrides: Any) -> Coupon:
    """10% 券 TENOFF，不设最低消费，两个上限都不限；提交。"""
    values: dict[str, Any] = {
        "code": "TENOFF",
        "coupon_type": TYPE_PERCENT,
        "amount_off_sen": None,
        "percent_off": 10,
        "min_subtotal_sen": 0,
        "starts_at": STARTS,
        "ends_at": ENDS,
        "total_use_limit": None,
        "per_member_use_limit": None,
        "is_active": True,
        "created_at": STARTS,
    }
    coupon = _add(db, Coupon(**(values | overrides)))
    db.commit()
    return coupon


def _request(**overrides: Any) -> MemberOrderRequest:
    """第 4 条算例：10% 券与 300 积分，寄马来西亚 MY-10。"""
    request = MemberOrderRequest(
        lines=EXAMPLE_LINES,
        phone=PHONE,
        name=NAME,
        address=ADDRESS,
        postal_code=POSTAL,
        country_code="MY",
        state_code="MY-10",
        region=None,
        coupon_code="TENOFF",
        points_to_redeem=300,
    )
    return replace(request, **overrides)


def _key() -> str:
    return f"member-key-{next(_KEY_SEQUENCE):08d}"


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _stocks(db: Session, catalog: _Catalog) -> tuple[int, int]:
    stock = dict(
        db.execute(select(ProductVariant.id, ProductVariant.available_stock)).tuples().all()
    )
    return stock[catalog.a], stock[catalog.b]


def _state(db: Session, catalog: _Catalog) -> dict[str, Any]:
    """会被下单改动的全部数据的计数与库存。"""
    db.expire_all()
    models = (
        Order,
        OrderItem,
        OrderItemUnit,
        OrderRecipient,
        OrderEvent,
        CouponUse,
        PointEntry,
        PointLot,
        OrderAccessSession,
        OrderAccessGrant,
    )
    state: dict[str, Any] = {model.__name__: _count(db, model) for model in models}
    state["stock"] = _stocks(db, catalog)
    return state


def _units(db: Session, order_id: int) -> list[tuple[int, int, int, int, int]]:
    """（原价、券、积分、现金实付、获得积分），按行序与件序。"""
    rows = db.scalars(
        select(OrderItemUnit)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.line_index, OrderItemUnit.unit_index)
    )
    return [
        (
            unit.original_price_sen,
            unit.coupon_discount_sen,
            unit.points_discount,
            unit.cash_paid_sen,
            unit.points_earned,
        )
        for unit in rows
    ]


def _order(db: Session, order_number: str) -> Order:
    db.expire_all()
    return db.scalars(select(Order).where(Order.order_number == order_number)).one()


# ---------------------------------------------------------------------------
# 下单：第 4 条算例
# ---------------------------------------------------------------------------


def test_example_order_writes_snapshots(db: Session, catalog: _Catalog, member: Member) -> None:
    """第 4 条算例「整单 10% 券减 RM2.50，分摊到 A 每件 RM1、B RM0.50；使用 300 积分抵 RM3，
    分摊到 A 每件 RM1.20、B RM0.60。三件现金实付依次 RM7.80、RM7.80、RM3.90，共 RM19.50，
    整单获得 19 积分，按稳定序分给 8、7、4」；第 1 条「二者均不抵运费」（应付为现金实付加运费）；
    第 6 条「15 分钟未支付自动取消」（支付到期时间为当前时间加 15 分钟）；Kelvin 2026-10-11
    决定 3「会员订单的获得积分在下单时按计价结果写入整单与逐件快照」。
    """
    _coupon(db)
    _earn(db, member, 300)
    key = _key()

    result = place_member_order(db, member.id, _request(), key, NOW)

    assert result.created is True
    summary = result.order
    assert summary.status == STATUS_AWAITING_PAYMENT
    assert (summary.subtotal_sen, summary.coupon_discount_sen) == (SUBTOTAL, 250)
    assert (summary.points_redeemed, summary.shipping_fee_sen) == (300, SHIPPING)
    assert (summary.total_sen, summary.points_earned) == (1950 + SHIPPING, 19)
    assert summary.payment_expires_at == NOW + timedelta(minutes=15)
    order = _order(db, summary.order_number)
    assert order.idempotency_key == key
    assert order.request_fingerprint == member_request_fingerprint(member.id, _request())
    assert order.subtotal_sen == SUBTOTAL
    assert (order.coupon_discount_sen, order.points_redeemed) == (250, 300)
    assert (order.shipping_fee_sen, order.total_sen, order.points_earned) == (SHIPPING, 2750, 19)
    assert (order.shipping_zone_code, order.shipping_rate_version) == ("MY-W", 1)
    assert (order.created_at, order.payment_expires_at) == (NOW, NOW + timedelta(minutes=15))
    assert order.paid_at is None
    assert _units(db, order.id) == [
        (1000, 100, 120, 780, 8),
        (1000, 100, 120, 780, 7),
        (500, 50, 60, 390, 4),
    ]


def test_example_order_reserves_coupon_points_and_stock(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 1 条「优惠券仅会员可用，使用额度在下单时预占」；第 3 条「结账抵扣时预占」；第 6 条
    「下单事务预留当日可用库存」；Kelvin 2026-10-11 决定 3「模拟支付成功时按订单快照写积分
    批次」（下单时不发放，没有本单的批次）。
    """
    coupon = _coupon(db)
    _earn(db, member, 300)

    result = place_member_order(db, member.id, _request(), _key(), NOW)

    order = _order(db, result.order.order_number)
    use = db.scalars(select(CouponUse).where(CouponUse.order_id == order.id)).one()
    assert (use.coupon_id, use.member_id, use.status) == (coupon.id, member.id, USE_RESERVED)
    assert use.coupon_discount_sen == 250
    stmt = select(PointEntry.points).where(PointEntry.order_id == order.id)
    redeemed = db.scalars(stmt.where(PointEntry.kind == KIND_REDEEM)).all()
    assert sum(redeemed) == -300
    assert available_points(db, member.id, NOW) == 0
    assert _count(db, PointLot) == 1
    assert db.scalar(select(func.count()).where(PointLot.order_id == order.id)) == 0
    assert _stocks(db, catalog) == (5 - 2, 3 - 1)


def test_member_order_is_not_claimable_and_issues_no_guest_access(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """「权限与资料保护」第 3 条「会员下单的订单创建时即标记为不可认领」；第 1 条「已登录会员
    结账不再验证」（不签发游客短期凭据，下单事件操作者为 member）；「OrderRecipient：收货姓名、
    规范化 E.164 电话、地址、邮编等原文」。
    """
    _coupon(db)
    _earn(db, member, 300)

    result = place_member_order(db, member.id, _request(), _key(), NOW)

    order = _order(db, result.order.order_number)
    assert (order.member_id, order.claim_status) == (member.id, CLAIM_NOT_CLAIMABLE)
    assert _count(db, OrderAccessSession) == 0
    assert _count(db, OrderAccessGrant) == 0
    placed = db.scalars(select(OrderEvent)).one()
    assert placed.order_id == order.id
    assert (placed.from_status, placed.to_status) == (None, STATUS_AWAITING_PAYMENT)
    assert placed.actor_type == ACTOR_MEMBER
    recipient = db.scalars(select(OrderRecipient)).one()
    assert recipient.order_id == order.id
    assert (recipient.name, recipient.phone, recipient.address) == (NAME, PHONE, ADDRESS)
    assert (recipient.country_code, recipient.region) == ("MY", "MY-10")
    assert recipient.postal_code == POSTAL


@pytest.mark.parametrize(
    ("coupon", "expected"),
    [
        pytest.param(None, (0, SUBTOTAL + SHIPPING, 25), id="no-coupon"),
        pytest.param(BIG_FIXED_COUPON, (SUBTOTAL, SHIPPING, 0), id="coupon-covers-all-goods"),
    ],
)
def test_shipping_is_never_discounted(
    db: Session,
    catalog: _Catalog,
    member: Member,
    coupon: dict[str, Any] | None,
    expected: tuple[int, int, int],
) -> None:
    """第 1 条「优惠券先抵商品额…二者均不抵运费，最终应付不得低于运费」：没有券与积分时应付为
    商品小计加运费；固定金额券大于商品小计时只抵到商品小计（第 2 条「固定金额券取券额与商品
    小计较小者」），应付恰为运费。
    """
    code = None
    if coupon is not None:
        code = _coupon(db, **coupon).code

    result = place_member_order(
        db, member.id, _request(coupon_code=code, points_to_redeem=0), _key(), NOW
    )

    summary = result.order
    discount, total, earned = expected
    assert (summary.coupon_discount_sen, summary.points_redeemed) == (discount, 0)
    assert (summary.total_sen, summary.points_earned) == (total, earned)
    assert summary.shipping_fee_sen == SHIPPING
    assert summary.total_sen >= summary.shipping_fee_sen
    order = _order(db, summary.order_number)
    units = _units(db, order.id)
    assert sum(unit[4] for unit in units) == earned
    assert sum(unit[3] for unit in units) == total - SHIPPING


# ---------------------------------------------------------------------------
# 计价
# ---------------------------------------------------------------------------


def test_quote_matches_order_and_writes_nothing(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 4 条算例（计价给出与下单相同的金额）；第 1 条「使用额度在下单时预占」——计价只读，
    不写库、不预占券与积分、不预留库存。
    """
    _coupon(db)
    _earn(db, member, 300)
    before = _state(db, catalog)

    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, "MY", "MY-10", "TENOFF", 300, NOW)

    assert not db.new and not db.dirty and not db.deleted
    assert _state(db, catalog) == before
    assert quote.subtotal_sen == SUBTOTAL
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 250)
    assert (quote.points_redeemed, quote.points_accepted, quote.points_earned) == (300, True, 19)
    assert (quote.available_points, quote.points_cap) == (300, 300)
    assert quote.shipping is not None and quote.shipping.fee_sen == SHIPPING
    assert quote.total_sen == 1950 + SHIPPING
    assert quote.can_place_order is True
    assert quote.fx_reference is None

    result = place_member_order(db, member.id, _request(), _key(), NOW)
    assert result.order.total_sen == quote.total_sen
    assert result.order.coupon_discount_sen == quote.coupon_discount_sen
    assert result.order.points_earned == quote.points_earned


def test_quote_reference_currency_uses_member_total(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """「边界与原则」其他货币只按固定演示汇率显示参考数，按收货国家决定；SHOP-TASK-088 验收：
    参考外币按会员合计（现金实付合计加运费）换算，而不是 quote_cart 未扣券与积分的合计。
    """
    _coupon(db)
    _earn(db, member, 300)

    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, "SG", None, "TENOFF", 300, NOW)

    assert quote.total_sen == 1950 + SG_SHIPPING
    assert quote.fx_reference is not None
    assert quote.fx_reference.currency_code == "SGD"
    assert quote.fx_reference.amount_minor == convert_sen(1950 + SG_SHIPPING, SG_RATE, 2)


def test_quote_without_country_still_judges_coupon_and_points(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 1 条「优惠券先抵商品额，积分再抵剩余商品额」——所有行都正常时不论是否给了收货国家都
    判定券与积分；没给收货国家时没有运费与合计，不可下单。
    """
    _coupon(db)
    _earn(db, member, 300)

    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, None, None, "TENOFF", 300, NOW)

    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 250)
    assert (quote.points_redeemed, quote.points_earned) == (300, 19)
    assert (quote.shipping, quote.total_sen, quote.fx_reference) == (None, None, None)
    assert quote.can_place_order is False


def test_quote_unavailable_coupon_is_not_applied(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 1 条「优惠券先抵商品额」——券不可用（已过期）时只标明不可用、不计入折扣，积分上限按
    未扣券的商品额计算。
    """
    _coupon(db, ends_at=NOW - timedelta(seconds=1))
    _earn(db, member, 300)

    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, "MY", "MY-10", "TENOFF", 300, NOW)

    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (False, 0)
    assert (quote.points_cap, quote.points_redeemed, quote.points_accepted) == (300, 300, True)
    # 现金实付 2200，每满 100 仙 1 积分。
    assert (quote.total_sen, quote.points_earned) == (2200 + SHIPPING, 22)


@pytest.mark.parametrize(
    ("balance", "points", "cap"),
    [
        pytest.param(300, 301, 300, id="over-balance"),
        pytest.param(5000, 2251, 2250, id="over-goods-after-coupon"),
    ],
)
def test_quote_points_over_cap_are_not_accepted(
    db: Session, catalog: _Catalog, member: Member, balance: int, points: int, cap: int
) -> None:
    """第 1 条「积分再抵剩余商品额」——积分上限为可用余额与券后商品额的较小值；超过上限的抵扣
    积分不计入、标明不接受，券照常计入。
    """
    _coupon(db)
    _earn(db, member, balance)

    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, "MY", "MY-10", "TENOFF", points, NOW)

    assert (quote.available_points, quote.points_cap) == (balance, cap)
    assert (quote.points_accepted, quote.points_redeemed) == (False, 0)
    assert quote.coupon_discount_sen == 250
    assert (quote.total_sen, quote.points_earned) == (2250 + SHIPPING, 22)


def test_quote_with_abnormal_line_skips_coupon_and_points(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 8 条「存在任何非正常行时不可下单」——有超出限购的行时不判定券与积分：券是否可用为空，
    券折扣、积分抵扣与获得积分为 0，积分上限为 0、不接受抵扣积分，可用余额照常给出。
    """
    _coupon(db)
    _earn(db, member, 300)
    lines = (CartItem(sku="A-SKU", quantity=2), CartItem(sku="B-SKU", quantity=3))

    quote = quote_member_cart(db, member.id, lines, "MY", "MY-10", "TENOFF", 300, NOW)

    assert [line.status for line in quote.lines] == ["ok", "over_limit"]
    assert quote.can_place_order is False
    assert quote.coupon_applicable is None
    assert (quote.coupon_discount_sen, quote.points_redeemed, quote.points_earned) == (0, 0, 0)
    assert (quote.available_points, quote.points_cap, quote.points_accepted) == (300, 0, False)
    assert quote.total_sen == 2000 + 1500 + SHIPPING


def test_quote_without_coupon_code_leaves_coupon_unjudged(
    db: Session, catalog: _Catalog, member: Member
) -> None:
    """第 1 条「优惠券先抵商品额」——没给券代码时不判定券，券是否可用为空。"""
    quote = quote_member_cart(db, member.id, EXAMPLE_LINES, "MY", "MY-10", None, 0, NOW)

    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (None, 0)
    assert (quote.available_points, quote.points_cap, quote.points_accepted) == (0, 0, True)
    assert (quote.total_sen, quote.points_earned) == (SUBTOTAL + SHIPPING, 25)


def test_quote_rejects_deleted_member(db: Session, catalog: _Catalog, member: Member) -> None:
    """第 1 条「优惠券仅会员可用」与「权限与资料保护」第 3 条（注销后积分余额与未用券作废）——
    已注销的会员不能按会员计价。
    """
    _delete_member(db, member)

    with pytest.raises(MemberNotActive):
        quote_member_cart(db, member.id, EXAMPLE_LINES, "MY", "MY-10", None, 0, NOW)


# ---------------------------------------------------------------------------
# 下单时整单拒绝
# ---------------------------------------------------------------------------


def _expired(db: Session, member: Member) -> str:
    return _coupon(db, ends_at=NOW - timedelta(seconds=1)).code


def _member_exhausted(db: Session, member: Member) -> str:
    coupon = _coupon(db, per_member_use_limit=1)
    order = _prior_order(db, member.id)
    assert reserve_coupon(db, coupon.id, order.id, member.id, 100, 6000, EARNED_AT).ok
    db.commit()
    return coupon.code


def _malformed(db: Session, member: Member) -> str:
    _coupon(db)
    return "TEN-OFF"


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(_expired, id="expired"),
        pytest.param(_member_exhausted, id="member-exhausted"),
        pytest.param(_malformed, id="malformed-code"),
    ],
)
def test_unavailable_coupon_rejects_whole_order(
    db: Session, catalog: _Catalog, member: Member, prepare: Callable[[Session, Member], str]
) -> None:
    """Kelvin 2026-10-11 决定 2「下单时券或积分已不可用…整单拒绝，不建订单、不预留库存」；
    「失败、并发与重试」第 1 条「库存预占、券/积分预占及订单创建在同一事务，失败全部回滚」。
    异常消息不含券代码。
    """
    code = prepare(db, member)
    _earn(db, member, 300)
    before = _state(db, catalog)

    with pytest.raises(CouponUnavailable) as raised:
        place_member_order(db, member.id, _request(coupon_code=code), _key(), NOW)

    assert str(raised.value) == ""
    assert _state(db, catalog) == before
    assert available_points(db, member.id, NOW) == 300


@pytest.mark.parametrize(
    ("balance", "points"),
    [
        pytest.param(300, 301, id="over-balance"),
        pytest.param(5000, 2251, id="over-goods-after-coupon"),
    ],
)
def test_unavailable_points_reject_whole_order(
    db: Session, catalog: _Catalog, member: Member, balance: int, points: int
) -> None:
    """第 1 条「积分再抵剩余商品额」——抵扣积分超过可用余额或券后商品额（2250）时，按 Kelvin
    2026-10-11 决定 2 整单拒绝；「失败全部回滚」：库存、券与积分都不变。
    """
    _coupon(db)
    _earn(db, member, balance)
    before = _state(db, catalog)

    with pytest.raises(PointsUnavailable):
        place_member_order(db, member.id, _request(points_to_redeem=points), _key(), NOW)

    assert _state(db, catalog) == before
    assert available_points(db, member.id, NOW) == balance


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param((CartItem("A-SKU", 1), CartItem("B-SKU", 3)), id="over-limit"),
        pytest.param((CartItem("A-SKU", 6), CartItem("B-SKU", 1)), id="insufficient-stock"),
        pytest.param((CartItem("NOPE", 1),), id="unavailable"),
    ],
)
def test_not_placeable_rejects_whole_order(
    db: Session, catalog: _Catalog, member: Member, lines: tuple[CartItem, ...]
) -> None:
    """第 8 条「下单时服务端重新校验，超出限购即拒绝整单，不部分下单。限购与库存各自独立生效」；
    「失败全部回滚」：库存、券与积分都不变。
    """
    _coupon(db)
    _earn(db, member, 300)
    before = _state(db, catalog)

    with pytest.raises(OrderNotPlaceable):
        place_member_order(db, member.id, _request(lines=lines), _key(), NOW)

    assert _state(db, catalog) == before


@pytest.mark.parametrize("which", ["coupon", "points"])
def test_reservation_refused_after_order_insert_rolls_back(
    db: Session,
    catalog: _Catalog,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
) -> None:
    """「库存预占、券/积分预占及订单创建在同一事务，失败全部回滚」；Kelvin 2026-10-11 决定 2
    「计价之后券被用满、积分到期或余额变化等」也整单拒绝。订单与子表已写入后，券的预占在行锁下
    重新判定为用满、或积分预占时余额不足：订单、订单行、逐件分摊、收货资料、事件、库存扣减与
    已写的预占都回滚。
    """
    _coupon(db)
    _earn(db, member, 300)
    before = _state(db, catalog)

    def exhausted(*args: Any, **kwargs: Any) -> CouponCheck:
        return CouponCheck(REASON_TOTAL_EXHAUSTED)

    def insufficient(*args: Any, **kwargs: Any) -> None:
        raise InsufficientPoints()

    if which == "coupon":
        monkeypatch.setattr(member_ordering, "reserve_coupon", exhausted)
        expected: type[Exception] = CouponUnavailable
    else:
        monkeypatch.setattr(member_ordering, "reserve_points", insufficient)
        expected = PointsUnavailable

    with pytest.raises(expected):
        place_member_order(db, member.id, _request(), _key(), NOW)

    assert _state(db, catalog) == before


def test_deleted_member_cannot_place_order(db: Session, catalog: _Catalog, member: Member) -> None:
    """「权限与资料保护」第 3 条：注销后「积分余额与未用券作废」，重新注册不能恢复——已注销的
    会员不能下会员订单，不建订单、不动库存。
    """
    _delete_member(db, member)
    before = _state(db, catalog)

    with pytest.raises(MemberNotActive):
        place_member_order(
            db, member.id, _request(coupon_code=None, points_to_redeem=0), _key(), NOW
        )

    assert _state(db, catalog) == before


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


def test_same_key_same_request_replays(db: Session, catalog: _Catalog, member: Member) -> None:
    """「失败、并发与重试」第 1 条「相同键相同请求返回原结果」——重放返回原订单，不重复扣库存、
    不重复预占券与积分。券代码按规范化后的代码进指纹，大小写与首尾空白不同仍是同一请求。
    """
    _coupon(db)
    _earn(db, member, 600)
    key = _key()
    first = place_member_order(db, member.id, _request(), key, NOW)
    after_first = _state(db, catalog)

    again = place_member_order(db, member.id, _request(coupon_code=" tenoff "), key, NOW)

    assert again.created is False
    assert again.order == first.order
    assert _state(db, catalog) == after_first
    assert after_first["stock"] == (3, 2)
    assert after_first["CouponUse"] == 1
    assert available_points(db, member.id, NOW) == 300


@pytest.mark.parametrize("who", ["same-member-other-request", "other-member"])
def test_same_key_conflicts(db: Session, catalog: _Catalog, member: Member, who: str) -> None:
    """「同键不同内容报冲突」：同一会员同键而抵扣积分不同，或另一会员用同一键（订单的会员 ID
    不是他），都抛 IdempotencyConflict，不重新计价、不动库存、券与积分。
    """
    _coupon(db)
    _earn(db, member, 600)
    key = _key()
    place_member_order(db, member.id, _request(), key, NOW)
    if who == "other-member":
        other = _member(db)
        _earn(db, other, 300)
        member_id, request = other.id, _request()
    else:
        member_id, request = member.id, _request(points_to_redeem=200)
    before = _state(db, catalog)

    with pytest.raises(IdempotencyConflict):
        place_member_order(db, member_id, request, key, NOW)

    assert _state(db, catalog) == before


def test_unique_key_violation_on_insert_replays_committed_order(
    db: Session,
    engine: Engine,
    catalog: _Catalog,
    member: Member,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「下单…均使用幂等键和数据库唯一约束。相同键相同请求返回原结果」；「失败全部回滚」。
    查过幂等键之后、写订单之前，同键同请求的并发请求先提交：插入撞上唯一约束，回滚本次的库存
    扣减，不预占券与积分，按键重放那张订单。
    """
    _coupon(db)
    _earn(db, member, 300)
    key = _key()
    fingerprint = member_request_fingerprint(member.id, _request())
    committed: list[str] = []
    real = member_ordering.quote_cart

    def other_request_commits_first(*args: Any, **kwargs: Any) -> Any:
        if not committed:
            order = Order(
                order_number=generate_order_number(),
                status=STATUS_AWAITING_PAYMENT,
                subtotal_sen=SUBTOTAL,
                coupon_discount_sen=250,
                points_redeemed=300,
                shipping_fee_sen=SHIPPING,
                total_sen=1950 + SHIPPING,
                points_earned=19,
                shipping_zone_code="MY-10",
                shipping_rate_version=1,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                created_at=NOW,
                payment_expires_at=NOW + timedelta(minutes=15),
                paid_at=None,
                member_id=member.id,
                claim_status=CLAIM_NOT_CLAIMABLE,
            )
            committed.append(order.order_number)
            with Session(engine) as other:
                other.add(order)
                other.commit()
        return real(*args, **kwargs)

    monkeypatch.setattr(member_ordering, "quote_cart", other_request_commits_first)
    before = _state(db, catalog)

    result = place_member_order(db, member.id, _request(), key, NOW)

    assert result.created is False
    assert result.order.order_number == committed[0]
    after = _state(db, catalog)
    assert after.pop("Order") == before.pop("Order") + 1
    assert after == before

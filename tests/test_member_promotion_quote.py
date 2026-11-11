"""会员计价接入节日促销活动（参加与不参加活动取更优，SHOP-TASK-236）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 4、6、8、10 条
与「计价、优惠、积分与库存」第 1、2 条（下称「第 N 条」与「计价第 N 条」），
以及 SHOP-TASK-236 写定的验收标准（下称「验收」）。
每条测试的文档字符串引用它守住的那一句。

服务层直接调用 quote_member_cart（它以 active_promotion 读生效活动）；接口层替换
app.api.member_orders._utcnow 固定计价的时钟（会员会话的核对仍用真实时钟）。用 SQLite 内存库
按模型建表，接口的会话依赖换成测试自己的会话。积分经 earn_points 发放。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import member_orders as member_orders_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Coupon,
    Member,
    Order,
    Product,
    ProductVariant,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    ShippingRate,
)
from app.models.coupon import TYPE_FIXED, TYPE_PERCENT
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_PAID
from app.services.checkout import CartItem
from app.services.coupons import REASON_BELOW_MIN_SPEND, check_coupon, reserve_coupon
from app.services.member_auth import issue_member_session
from app.services.member_ordering import (
    COUPON_NOT_COMBINABLE,
    PLAN_NO_PROMOTION,
    PLAN_PROMOTION,
    MemberCheckoutQuote,
    quote_member_cart,
)
from app.services.points import earn_points

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

URL = "/api/member/checkout/quote"
COOKIE_NAME = "__Host-shop_member_session"
STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)
NOW = STARTS + timedelta(hours=1)
WEST = ("MY", "MY-10")

SECTION_8_CART = [CartItem("A", 2), CartItem("B", 2), CartItem("C", 1)]
# 第 8 条：10% 券（最低消费 RM50）与 1,000 积分。
TEN_PERCENT_MIN_50: dict[str, Any] = {
    "code": "TENOFF",
    "coupon_type": TYPE_PERCENT,
    "amount_off_sen": None,
    "percent_off": 10,
    "min_subtotal_sen": 5000,
}
POINTS = 1000

_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)


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
    # 连接串显式为空：Redis 未配置，计价的限流放行。
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))
    app.dependency_overrides[get_session] = lambda: db
    # https：会员会话 cookie 带 Secure。
    return TestClient(app, base_url="https://testserver")


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
    _add(db, ShippingRate(zone_type="my_region", zone_code="MY-W", fee_sen=800, version=1))


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


@dataclass
class _Shop:
    gifts: Category
    promotion_id: int


def _section_8(
    db: Session, *, coupon_combinable: bool = True, points_combinable: bool = True
) -> _Shop:
    """第 8 条算例的商品与活动：A RM25.00（分类「礼品」）、B RM12.00 与 C RM9.00（分类「家居」）；
    A 打 8 折；「家居」买 2 享 1、享受的件 100%；满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费。
    两个同用开关默认都打开；活动在 STARTS 到 ENDS 之间生效。
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
    return _Shop(gifts, promotion.id)


def _member(db: Session) -> int:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    member = Member(phone=phone, status=MEMBER_ACTIVE, created_at=NOW - timedelta(days=100))
    return _add(db, member).id


def _prior_order(db: Session, member_id: int) -> Order:
    """该会员昨天已支付的一张订单，供发放积分或预占券额度。"""
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


def _earn(db: Session, member_id: int, points: int) -> None:
    order = _prior_order(db, member_id)
    assert earn_points(db, member_id, order.id, points, NOW - timedelta(days=1)) is not None


def _coupon(db: Session, **overrides: Any) -> Coupon:
    """默认为第 8 条的 10% 券（最低消费 RM50），十天前起、三十天后止，两个上限都不限。"""
    values: dict[str, Any] = {
        **TEN_PERCENT_MIN_50,
        "starts_at": NOW - timedelta(days=10),
        "ends_at": NOW + timedelta(days=30),
        "total_use_limit": None,
        "per_member_use_limit": None,
        "is_active": True,
        "created_at": NOW - timedelta(days=10),
    }
    return _add(db, Coupon(**(values | overrides)))


def _member_with_points(db: Session) -> int:
    member_id = _member(db)
    _earn(db, member_id, POINTS)
    return member_id


def _quote(
    db: Session,
    member_id: int,
    items: list[CartItem],
    coupon_code: str | None,
    points: int,
    destination: tuple[str | None, str | None] = WEST,
) -> MemberCheckoutQuote:
    return quote_member_cart(db, member_id, items, *destination, coupon_code, points, NOW)


# ---- 服务层：比较 ----


def test_section_8_both_switches_on_takes_promotion(db: Session) -> None:
    """第 8 条「两个同用开关都打开…用 10% 券（最低消费 RM50）与 1,000 积分」
    「活动后商品额 RM59.00，达到券的最低消费；券折 RM5.90」「1,000 积分抵 RM10.00」
    「应付 RM43.10」与第 6 条「比较两者『积分抵扣之前的应付』…取较低的一种」：
    甲 5900 − 590 + 0 = 5310，乙 8300 − 830 + 800 = 8270，取甲；
    券 590、积分 1000、合计 4310、获得 43 积分。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)

    quote = _quote(db, member_id, SECTION_8_CART, "TENOFF", POINTS)

    assert quote.promotion_plan == PLAN_PROMOTION
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (5310, 8270)
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 590)
    assert (quote.points_cap, quote.points_accepted, quote.points_redeemed) == (1000, True, 1000)
    assert quote.total_sen == 4310
    assert quote.points_earned == 43
    assert (quote.coupon_note, quote.points_not_combinable) == (None, False)
    assert quote.promotion is not None
    assert quote.promotion.promotion_id == shop.promotion_id
    assert quote.promotion.participates is True
    assert quote.promotion.shipping_waived_sen == 800


def test_coupon_not_combinable_takes_promotion_without_coupon(db: Session) -> None:
    """第 8 条「同一购物车若活动关闭了券同用，甲（活动，不用券）的积分前应付为
    RM59.00，乙（不参加活动，用券）为 83.00 − 8.30 + 8.00 = RM82.70，取甲」
    与验收「取甲且券可用而未用时，coupon_note 在活动不与券同用时为 not_combinable」：
    券不计入但 coupon_applicable 仍为真，积分 1000 照收，合计 5900 − 1000 = 4900。
    """
    _section_8(db, coupon_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db)

    quote = _quote(db, member_id, SECTION_8_CART, "TENOFF", POINTS)

    assert quote.promotion_plan == PLAN_PROMOTION
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (5900, 8270)
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 0)
    assert quote.coupon_note == COUPON_NOT_COMBINABLE
    assert (quote.points_cap, quote.points_redeemed) == (1000, 1000)
    assert quote.total_sen == 4900


def test_example_d_takes_no_promotion(db: Session) -> None:
    """第 8 条「购物车只有不在任何商品级规则里的 D 1 件 RM200.00、运费 RM8.00，
    券为直减 RM30，活动关闭券同用：甲为 200.00 − 12.00 + 0 = RM188.00，
    乙为 200.00 − 30.00 + 8.00 = RM178.00，取乙（本单不记活动、不免运费）」：
    取乙时券照用、运费照收，合计 17800，没有 coupon_note。
    """
    shop = _section_8(db, coupon_combinable=False)
    _product(db, shop.gifts, "d", 20000)
    member_id = _member(db)
    _coupon(
        db,
        code="RM30OFF",
        coupon_type=TYPE_FIXED,
        amount_off_sen=3000,
        percent_off=None,
        min_subtotal_sen=0,
    )

    quote = _quote(db, member_id, [CartItem("D", 1)], "RM30OFF", 0)

    assert quote.promotion_plan == PLAN_NO_PROMOTION
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (
        18800,
        17800,
    )
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 3000)
    assert quote.total_sen == 17800
    assert quote.coupon_note is None
    # promotion 照转 quote_cart 的结果：活动本身仍判定为参加，只是本单取了乙。
    assert quote.promotion is not None and quote.promotion.participates is True


def test_third_example_coupon_below_min_after_sale_takes_no_promotion(db: Session) -> None:
    """第 8 条「购物车只有 E 1 件 RM100.00，活动只有满 RM100 减 RM1，
    券为直减 RM50、最低消费 RM100，两个开关都打开，运费 RM8.00：
    甲的活动后商品额 RM99.00 够不到券的门槛，积分前应付为 99.00 + 8.00 = RM107.00；
    乙为 100.00 − 50.00 + 8.00 = RM58.00，取乙」
    与计价第 2 条「券的最低商品消费也按整单活动后商品额判断」：合计 5800。
    """
    _west_rate(db)
    _product(db, _category(db, "misc"), "e", 10000)
    promotion = _promotion(db, coupon_combinable=True, points_combinable=True)
    _spend_rule(db, promotion, 1, [(10000, 100)])
    member_id = _member(db)
    _coupon(
        db,
        code="RM50OFF",
        coupon_type=TYPE_FIXED,
        amount_off_sen=5000,
        percent_off=None,
        min_subtotal_sen=10000,
    )

    quote = _quote(db, member_id, [CartItem("E", 1)], "RM50OFF", 0)

    assert quote.promotion_plan == PLAN_NO_PROMOTION
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (
        10700,
        5800,
    )
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (True, 5000)
    assert quote.total_sen == 5800


def test_points_not_combinable_only(db: Session) -> None:
    """第 6 条「积分按价值计入比较的口径（100 积分 = RM1），所以只关闭积分同用时，
    参加活动的订单不能用积分」与验收「取甲、会员填了积分而 points_allowed 为 False 时
    points_not_combinable 为 True、points_cap 为 0、points_redeemed 为 0、
    points_accepted 为 True」：券照用 590，合计 5310，获得 53 积分。
    """
    _section_8(db, points_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db)

    quote = _quote(db, member_id, SECTION_8_CART, "TENOFF", POINTS)

    assert quote.promotion_plan == PLAN_PROMOTION
    assert quote.points_not_combinable is True
    assert (quote.points_cap, quote.points_redeemed, quote.points_accepted) == (0, 0, True)
    assert quote.coupon_discount_sen == 590
    assert quote.coupon_note is None
    assert quote.total_sen == 5310
    assert quote.points_earned == 53


# ---- 服务层：不比较 ----


def test_without_shipping_quote_plan_is_none_and_promotion_is_provisional(db: Session) -> None:
    """第 4 条「还没有运费报价时（购物车页），计价结果只是暂定…不判定是否参加活动、
    不做第 6 条的比较」与验收「没有运费报价…时，券与积分照现在按原价计算，
    promotion_plan 为 None」：promotion 给出暂算的商品级 1900 与整单级 500、
    participates 为 None；券按原价 8300 折 830，积分上限 min(1000, 7470)。
    """
    _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)

    quote = _quote(db, member_id, SECTION_8_CART, "TENOFF", POINTS, destination=(None, None))

    assert quote.promotion_plan is None
    assert quote.promotion is not None
    assert (quote.promotion.item_discount_sen, quote.promotion.order_discount_sen) == (1900, 500)
    assert quote.promotion.participates is None
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (None, None)
    assert quote.coupon_discount_sen == 830
    assert (quote.points_cap, quote.points_redeemed) == (1000, 1000)
    assert quote.total_sen is None
    assert (quote.coupon_note, quote.points_not_combinable) == (None, False)


@pytest.mark.parametrize(
    ("points_combinable", "cap"),
    [pytest.param(True, 1000, id="points_combinable"), pytest.param(False, 0, id="not")],
)
def test_participating_without_coupon_or_points_takes_promotion_without_compare(
    db: Session, points_combinable: bool, cap: int
) -> None:
    """第 6 条「会员在结账时选了券或填了积分、且本单参加活动…时…都算两种方案」的反面
    与验收「参加活动而会员没选券也没填积分时，promotion_plan 为 promotion，
    按活动后的金额与实际运费给出合计，两个积分前应付为 None，
    points_cap 在活动与积分同用时为可用余额与活动后商品额较小者、不同用时为 0」：
    合计 5900，上限 min(1000, 5900) 或 0。
    """
    _section_8(db, points_combinable=points_combinable)
    member_id = _member_with_points(db)

    quote = _quote(db, member_id, SECTION_8_CART, None, 0)

    assert quote.promotion_plan == PLAN_PROMOTION
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (None, None)
    assert quote.total_sen == 5900
    assert (quote.points_cap, quote.points_accepted, quote.points_redeemed) == (cap, True, 0)
    assert (quote.coupon_applicable, quote.coupon_discount_sen) == (None, 0)
    assert quote.points_not_combinable is False


def test_abnormal_line_returns_early_with_promotion_forwarded(db: Session) -> None:
    """验收「有不正常的行或超出整单上限时照现在提前返回，promotion 照转，其余新字段为 None 与
    False」：A 库存不足时不判定券、积分与方案。
    """
    _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    variant = db.scalars(select(ProductVariant).where(ProductVariant.sku == "A")).one()
    variant.available_stock = 1
    db.flush()

    quote = _quote(db, member_id, SECTION_8_CART, "TENOFF", POINTS)

    assert quote.lines[0].status == "insufficient_stock"
    assert quote.promotion is not None
    assert quote.promotion_plan is None
    assert (quote.promotion_pre_points_total_sen, quote.other_pre_points_total_sen) == (None, None)
    assert (quote.coupon_note, quote.points_not_combinable) == (None, False)
    assert (quote.coupon_applicable, quote.points_cap, quote.points_accepted) == (None, 0, False)


# ---- 查券给出最低消费 ----


def test_check_and_reserve_coupon_give_min_subtotal_when_usable(db: Session) -> None:
    """第 6 条「开关打开的券仍须在活动后的金额上达到最低消费」与验收「check_coupon 与
    reserve_coupon 在券可用时填上该券的最低消费」：可用时为 5000；原价就不够最低消费时不可用、
    为 None。
    """
    _section_8(db)
    member_id = _member(db)
    coupon = _coupon(db)
    order = _prior_order(db, member_id)

    assert check_coupon(db, "TENOFF", member_id, 8300, NOW).min_subtotal_sen == 5000
    below = check_coupon(db, "TENOFF", member_id, 4999, NOW)
    assert (below.reason, below.min_subtotal_sen) == (REASON_BELOW_MIN_SPEND, None)
    reserved = reserve_coupon(db, coupon.id, order.id, member_id, 590, 8300, NOW)
    assert reserved.ok
    assert reserved.min_subtotal_sen == 5000


# ---- 接口层 ----


def _token(db: Session, member_id: int) -> str:
    member = db.get(Member, member_id)
    assert member is not None
    # 会员会话按真实时钟签发与核对；只有计价的时钟被固定。
    token = issue_member_session(db, member, datetime.now(UTC).replace(tzinfo=None)).token
    db.commit()
    return token


def _post(client: TestClient, token: str, coupon_code: str, points: int) -> dict[str, Any]:
    body = {
        "lines": [{"sku": item.sku, "quantity": item.quantity} for item in SECTION_8_CART],
        "country_code": WEST[0],
        "state_code": WEST[1],
        "coupon_code": coupon_code,
        "points": points,
    }
    response = client.post(URL, json=body, headers={"Cookie": f"{COOKIE_NAME}={token}"})
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_api_returns_plan_fields_for_section_8(
    db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 6 条「计价接口返回所取的方案与另一方案的积分前应付，前台据此说明」
    与验收「MemberQuoteOut 加同名字段…照服务层结果输出」：固定时钟在活动期间，
    第 8 条首例的响应带 promotion（结构同 PromotionQuote）、promotion_plan、
    两个积分前应付、coupon_note 与 points_not_combinable。
    """
    shop = _section_8(db)
    member_id = _member_with_points(db)
    _coupon(db)
    token = _token(db, member_id)
    monkeypatch.setattr(member_orders_api, "_utcnow", lambda: NOW)

    quote = _post(client, token, "TENOFF", POINTS)

    assert quote["promotion_plan"] == "promotion"
    assert quote["promotion_pre_points_total_sen"] == 5310
    assert quote["other_pre_points_total_sen"] == 8270
    assert quote["coupon_note"] is None
    assert quote["points_not_combinable"] is False
    assert (quote["coupon_applied"], quote["coupon_discount_sen"]) == (True, 590)
    assert (quote["points_max"], quote["points_redeemed"]) == (1000, 1000)
    assert quote["total_sen"] == 4310
    promotion = quote["promotion"]
    assert promotion["promotion_id"] == shop.promotion_id
    assert (promotion["item_discount_sen"], promotion["order_discount_sen"]) == (1900, 500)
    assert (promotion["shipping_waived_sen"], promotion["participates"]) == (800, True)


def test_api_returns_notes_when_both_switches_off(
    db: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 10 条「会员到结账页选了券或填了积分后，可能按第 6 条改为不参加活动，
    结账页据此说明」与验收「coupon_note…为 not_combinable」
    「points_not_combinable 为 True」：两个开关都关闭时甲为 5900、乙为 8270，取甲，
    券与积分都不计入，合计 5900。
    """
    _section_8(db, coupon_combinable=False, points_combinable=False)
    member_id = _member_with_points(db)
    _coupon(db)
    token = _token(db, member_id)
    monkeypatch.setattr(member_orders_api, "_utcnow", lambda: NOW)

    quote = _post(client, token, "TENOFF", POINTS)

    assert quote["promotion_plan"] == "promotion"
    assert (quote["promotion_pre_points_total_sen"], quote["other_pre_points_total_sen"]) == (
        5900,
        8270,
    )
    assert quote["coupon_note"] == "not_combinable"
    assert quote["points_not_combinable"] is True
    assert (quote["coupon_applied"], quote["coupon_discount_sen"]) == (True, 0)
    assert (quote["points_max"], quote["points_accepted"], quote["points_redeemed"]) == (0, True, 0)
    assert quote["total_sen"] == 5900

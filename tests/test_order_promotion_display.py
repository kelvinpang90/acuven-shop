"""订单详情与后台订单详情的活动优惠字段（SHOP-TASK-238）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1 条（内部名称只在后台显示）、
第 7 条（订单记活动 ID、商品级与整单级活动优惠合计、实际运费与免去的运费）、第 8 条首例与
第 14 条（订单显示订单快照里的金额），以及 docs/UX.md 0.20 的 P09 与 A02「金额明细」
（活动优惠合计大于 0 时加 [checkout.summary_promo] 一行，活动免运费时运费行显示
[checkout.shipping_free]；A02 另显示 [admin.order_promotion] 即活动的内部名称）。
每条测试的文档字符串写明它守住的设计或 UX 句子。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。活动、订单
（订单行、逐件分摊快照、收货资料与事件）、会员、会员会话、查单授权与后台会话都直接写库
建立，不经下单与计价；有活动的订单照第 8 条首例写入快照（商品级 1900、整单级 500、
实际运费 0、免去 800），由此断言详情只读快照、不重算。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    Promotion,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_GUEST,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
)
from app.models.order_access import SCOPE_LOOKUP
from app.services import admin_orders as admin_orders_service
from app.services.admin_auth import issue_admin_session
from app.services.member_auth import csrf_token_for_cookie as member_csrf_for_cookie
from app.services.member_auth import issue_member_session
from app.services.order_access import COOKIE_NAME as ORDER_ACCESS_COOKIE
from app.services.order_access import issue_order_access
from app.services.order_lookup import lookup_orders
from app.services.order_rules import generate_order_number

LOOKUP_URL = "/api/orders/lookup"
MEMBER_VIEW_URL = "/api/member/orders/view"
ADMIN_ORDERS_URL = "/api/admin/orders"
MEMBER_COOKIE = "__Host-shop_member_session"
ADMIN_COOKIE = "__Host-shop_admin_session"

PROMOTION_NAME = "Internal 11.11 sale"
BASE = datetime(2026, 11, 11, 2, 0, 0)

# 第 8 条首例的购物车：A 2 件各 2500、B 2 件各 1200、C 1 件 900；西马运费 800。
# 每件：原价、活动优惠、券额、积分、获得积分（券与积分只用于会员订单）。
EXAMPLE_LINES = [
    ("A", [(2500, 656, 184, 313, 14), (2500, 656, 184, 313, 13)]),
    ("B", [(1200, 94, 111, 187, 8), (1200, 94, 111, 187, 8)]),
    ("C", [(900, 900, 0, 0, 0)]),
]
SUBTOTAL = 8300
ITEM_DISCOUNT = 1900
ORDER_DISCOUNT = 500
PROMOTION_DISCOUNT = ITEM_DISCOUNT + ORDER_DISCOUNT
SHIPPING_QUOTE = 800
COUPON = 590
POINTS = 1000

# 两处详情给出的活动字段：有活动与没有活动。
PROMOTED = {"promotion_discount_sen": 2400, "shipping_fee_sen": 0, "shipping_waived_sen": 800}
PLAIN = {"promotion_discount_sen": 0, "shipping_fee_sen": SHIPPING_QUOTE, "shipping_waived_sen": 0}


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
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _promotion(db: Session) -> int:
    """一个已发布、已开始的活动；返回内部 ID。"""
    promotion = Promotion(
        name=PROMOTION_NAME,
        status="published",
        starts_at=BASE - timedelta(days=1),
        ends_at=BASE + timedelta(days=1),
    )
    promotion_id = _add(db, promotion).id
    db.commit()
    return promotion_id


def _member(db: Session) -> int:
    member = Member(phone="+60129990001", status=MEMBER_ACTIVE, created_at=_now())
    member_id = _add(db, member).id
    db.commit()
    return member_id


def _make_order(
    db: Session, *, promotion_id: int | None = None, member_id: int | None = None
) -> _Placed:
    """直接写一张已支付订单（订单行、逐件分摊快照、收货资料与事件）并提交。

    promotion_id 不为空时照第 8 条首例写活动快照：商品级 1900、整单级 500、实际运费 0、
    免去 800；会员订单另带首例的券 590 与积分 1000（应付 4310）。没有活动时运费 800，
    不带券与积分，活动三项取服务端默认值。
    """
    promoted = promotion_id is not None
    with_coupon = promoted and member_id is not None
    coupon = COUPON if with_coupon else 0
    points = POINTS if with_coupon else 0
    promotion_discount = PROMOTION_DISCOUNT if promoted else 0
    shipping = 0 if promoted else SHIPPING_QUOTE
    values: dict[str, Any] = {}
    if promoted:
        values = {
            "promotion_id": promotion_id,
            "promotion_item_discount_sen": ITEM_DISCOUNT,
            "promotion_order_discount_sen": ORDER_DISCOUNT,
            "shipping_waived_sen": SHIPPING_QUOTE,
        }
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_PAID,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=coupon,
        points_redeemed=points,
        shipping_fee_sen=shipping,
        total_sen=SUBTOTAL - promotion_discount - coupon - points + shipping,
        points_earned=43 if with_coupon else 0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"key-{uuid.uuid4().hex}",
        request_fingerprint="0" * 64,
        created_at=BASE,
        payment_expires_at=BASE + timedelta(minutes=15),
        paid_at=BASE + timedelta(minutes=2),
        member_id=member_id,
        claim_status=CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE,
        **values,
    )
    _add(db, order)
    for line_index, (name, units) in enumerate(EXAMPLE_LINES):
        price = units[0][0]
        item = OrderItem(
            order_id=order.id,
            line_index=line_index,
            variant_id=None,
            sku=f"SKU-{name}",
            product_name_en=name,
            product_name_zh=name,
            product_name_ms=name,
            variant_label_en="",
            variant_label_zh="",
            variant_label_ms="",
            unit_price_sen=price,
            quantity=len(units),
            line_subtotal_sen=price * len(units),
        )
        _add(db, item)
        for unit_index, (original, *example) in enumerate(units):
            promo = example[0] if promoted else 0
            unit_coupon, unit_points, earned = example[1:] if with_coupon else (0, 0, 0)
            _add(
                db,
                OrderItemUnit(
                    order_item_id=item.id,
                    unit_index=unit_index,
                    original_price_sen=original,
                    promotion_discount_sen=promo,
                    coupon_discount_sen=unit_coupon,
                    points_discount=unit_points,
                    cash_paid_sen=original - promo - unit_coupon - unit_points,
                    points_earned=earned,
                ),
            )
    _add(
        db,
        OrderRecipient(
            order_id=order.id,
            name="Promo recipient",
            phone="+60123456789",
            country_code="MY",
            region="MY-10",
            address="1 Example Road",
            postal_code="50000",
        ),
    )
    _add(
        db,
        OrderEvent(
            order_id=order.id,
            from_status=None,
            to_status=STATUS_AWAITING_PAYMENT,
            actor_type=ACTOR_GUEST,
            created_at=BASE,
        ),
    )
    placed = _Placed(id=order.id, number=order.order_number)
    db.commit()
    return placed


def _picked(view: dict[str, Any]) -> dict[str, Any]:
    return {key: view[key] for key in PROMOTED}


def _lookup_cookie(db: Session, placed: _Placed) -> dict[str, str]:
    """给一个新浏览器签发该单的查单授权（不经查单接口，不需要 Redis）。"""
    issued = issue_order_access(db, None, placed.id, SCOPE_LOOKUP, _now())
    db.commit()
    assert issued.new_token is not None
    return {"Cookie": f"{ORDER_ACCESS_COOKIE}={issued.new_token}"}


def _member_headers(db: Session, member_id: int) -> dict[str, str]:
    member = db.get(Member, member_id)
    assert member is not None
    issued = issue_member_session(db, member, _now())
    db.commit()
    csrf = member_csrf_for_cookie(issued.token)
    assert csrf is not None
    return {"Cookie": f"{MEMBER_COOKIE}={issued.token}", "X-CSRF-Token": csrf}


def _admin_headers(db: Session) -> dict[str, str]:
    account = AdminAccount(
        username="shop_admin",
        password_hash="not-a-real-hash",
        created_at=_now() - timedelta(days=1),
        password_updated_at=_now() - timedelta(days=1),
    )
    account_id = _add(db, account).id
    issued = issue_admin_session(db, account_id, _now() - timedelta(minutes=1))
    db.commit()
    return {"Cookie": f"{ADMIN_COOKIE}={issued.token}"}


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


# ---------------------------------------------------------------------------
# 服务层：LookupOrder 与 AdminOrderDetail
# ---------------------------------------------------------------------------


def test_lookup_view_reads_promotion_snapshot(db: Session) -> None:
    """DESIGN 1.23 第 7 条「订单记…商品级活动优惠合计、整单级活动优惠合计、实际运费与免去的
    运费」与第 14 条「订单与支付页显示订单快照里的金额」；UX P09「金额明细」：活动优惠合计
    大于 0 时加 [checkout.summary_promo] 一行，活动免运费时运费行显示 [checkout.shipping_free]。
    第 8 条首例的快照：查单视图给出活动优惠 1900 + 500 = 2400、实际运费 0、免去 800，
    应付照快照 4310，不重算。
    """
    promotion_id = _promotion(db)
    member_id = _member(db)
    placed = _make_order(db, promotion_id=promotion_id, member_id=member_id)

    (view,) = lookup_orders(db, [placed.id], "en", _now())

    assert view.promotion_discount_sen == 2400
    assert (view.shipping_fee_sen, view.shipping_waived_sen) == (0, 800)
    assert (view.coupon_discount_sen, view.points_redeemed) == (COUPON, POINTS)
    assert view.total_sen == 4310


def test_lookup_view_promotion_fields_zero_without_promotion(db: Session) -> None:
    """DESIGN 1.23 第 4 条末句「视为不参加活动：不记活动」与第 7 条「已有订单…的新列取 0
    或空」；UX P09：活动优惠合计为 0 时不加活动优惠一行，运费行照常。没有活动的订单两项为 0。
    """
    placed = _make_order(db)

    (view,) = lookup_orders(db, [placed.id], "en", _now())

    assert (view.promotion_discount_sen, view.shipping_waived_sen) == (0, 0)
    assert view.shipping_fee_sen == SHIPPING_QUOTE


def test_admin_detail_reads_promotion_snapshot_and_name(db: Session) -> None:
    """DESIGN 1.23 第 7 条「订单记活动 ID…」与第 1 条「内部名称（只在后台显示）」；UX A02 M1
    「订单记有活动时显示 [admin.order_promotion]（活动的内部名称），活动优惠合计大于 0 时金额
    明细在商品小计之后加 [checkout.summary_promo] 一行，活动免运费时运费行显示
    [checkout.shipping_free]」。第 8 条首例：后台详情给出 2400、免去 800 与活动的内部名称。
    """
    promotion_id = _promotion(db)
    member_id = _member(db)
    placed = _make_order(db, promotion_id=promotion_id, member_id=member_id)

    detail = admin_orders_service.order_detail(db, placed.id, "en", _now())

    assert detail is not None
    assert detail.promotion_discount_sen == 2400
    assert (detail.shipping_fee_sen, detail.shipping_waived_sen) == (0, 800)
    assert detail.promotion_name == PROMOTION_NAME
    assert (
        detail.subtotal_sen
        - detail.promotion_discount_sen
        - detail.coupon_discount_sen
        - detail.points_discount_sen
        + detail.shipping_fee_sen
        == detail.total_sen
    )
    assert detail.total_sen == 4310


def test_admin_detail_promotion_fields_empty_without_promotion(db: Session) -> None:
    """UX A02 M1：「订单记有活动时」才显示 [admin.order_promotion]，「活动优惠合计大于 0 时」
    才加活动优惠一行。没有活动的订单为 0、0 与 None。
    """
    _promotion(db)
    placed = _make_order(db)

    detail = admin_orders_service.order_detail(db, placed.id, "en", _now())

    assert detail is not None
    assert (detail.promotion_discount_sen, detail.shipping_waived_sen) == (0, 0)
    assert detail.promotion_name is None
    assert detail.shipping_fee_sen == SHIPPING_QUOTE


# ---------------------------------------------------------------------------
# 接口层：游客订单查询、会员订单详情与后台订单详情
# ---------------------------------------------------------------------------


def test_guest_lookup_response_carries_promotion_fields(db: Session, client: TestClient) -> None:
    """UX 0.20「P09、A02：金额明细新增活动优惠一行与免运费的显示」（P05 同样「游客与会员都
    显示」）；DESIGN 1.23 第 1 条「内部名称（只在后台显示）」。游客订单查询的响应带
    promotion_discount_sen 与 shipping_waived_sen；没有活动的订单为 0；响应里没有活动的
    内部名称。
    """
    promotion_id = _promotion(db)
    promoted = _make_order(db, promotion_id=promotion_id)
    plain = _make_order(db)

    promoted_body = _ok(client.get(LOOKUP_URL, headers=_lookup_cookie(db, promoted)))
    plain_body = _ok(client.get(LOOKUP_URL, headers=_lookup_cookie(db, plain)))

    (promoted_view,) = promoted_body["orders"]
    (plain_view,) = plain_body["orders"]
    assert _picked(promoted_view) == PROMOTED
    assert promoted_view["total_sen"] == SUBTOTAL - PROMOTION_DISCOUNT
    assert _picked(plain_view) == PLAIN
    assert "promotion_name" not in promoted_view
    assert PROMOTION_NAME not in str(promoted_body)


def test_member_order_detail_response_carries_promotion_fields(
    db: Session, client: TestClient
) -> None:
    """UX P09 会员模式与查单模式共用订单详情；UX 0.20「P09…金额明细新增活动优惠一行与
    免运费的显示」；DESIGN 1.23 第 1 条「内部名称（只在后台显示）」。会员订单详情的响应带
    第 8 条首例的 2400 与 800；没有活动的订单为 0；响应里没有活动的内部名称。
    """
    promotion_id = _promotion(db)
    member_id = _member(db)
    promoted = _make_order(db, promotion_id=promotion_id, member_id=member_id)
    plain = _make_order(db, member_id=member_id)
    headers = _member_headers(db, member_id)

    promoted_view = _ok(
        client.post(MEMBER_VIEW_URL, json={"order_number": promoted.number}, headers=headers)
    )
    plain_view = _ok(
        client.post(MEMBER_VIEW_URL, json={"order_number": plain.number}, headers=headers)
    )

    assert _picked(promoted_view) == PROMOTED
    assert promoted_view["coupon_discount_sen"] == COUPON
    assert promoted_view["points_redeemed"] == POINTS
    assert promoted_view["total_sen"] == 4310
    assert _picked(plain_view) == PLAIN
    assert "promotion_name" not in promoted_view
    assert PROMOTION_NAME not in str(promoted_view)


def test_admin_order_detail_response_carries_promotion_fields(
    db: Session, client: TestClient
) -> None:
    """UX A02 M1「订单记有活动时显示 [admin.order_promotion]（活动的内部名称）…活动免运费时
    运费行显示 [checkout.shipping_free]」；DESIGN 1.23 第 7 条。后台订单详情的响应带
    promotion_discount_sen、shipping_waived_sen 与 promotion_name；没有活动为 0、0 与 null。
    后台订单列表的行不变（不带活动字段）。
    """
    promotion_id = _promotion(db)
    member_id = _member(db)
    promoted = _make_order(db, promotion_id=promotion_id, member_id=member_id)
    plain = _make_order(db)
    headers = _admin_headers(db)

    promoted_body = _ok(client.get(f"{ADMIN_ORDERS_URL}/{promoted.id}", headers=headers))
    plain_body = _ok(client.get(f"{ADMIN_ORDERS_URL}/{plain.id}", headers=headers))
    listing = _ok(client.post(f"{ADMIN_ORDERS_URL}/query", json={}, headers=headers))

    assert _picked(promoted_body) == PROMOTED
    assert promoted_body["promotion_name"] == PROMOTION_NAME
    assert _picked(plain_body) == PLAIN
    assert plain_body["promotion_name"] is None
    for row in listing["orders"]:
        assert set(row) == {
            "id",
            "order_number",
            "created_at",
            "status",
            "total_sen",
            "refunds_pending",
        }

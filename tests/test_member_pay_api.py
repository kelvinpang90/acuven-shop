"""支付页与结果页的会员接口：POST /api/member/pay/order、POST /api/member/pay/attempts 与
POST /api/member/pay/cancel（app/api/member_pay.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」（下称「状态」）：“待支付订单可由下单者
  在支付页取消（游客凭该单短期凭据，会员凭登录会话），或超时为 demo_cancelled，释放库存、
  券及积分预占”。
- 「权限与资料保护」第 6 条（下称「第 6 条」）：“会员仅能访问自己认领或下单的订单”；
  “支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌”；凭据不写入“网址…日志…错误回显”。
- 「计价、优惠、积分与库存」第 3 条（下称「第 3 条」）：模拟支付成功后获得积分。
- 「失败、并发与重试」第 1 条（下称「重试第 1 条」）：“相同键相同请求返回原结果，同键不同
  内容报冲突”。
- docs/UX.md 0.10 P06、P07（下称「P06」「P07」）：会员凭登录会话访问支付页与结果页；
  成功后会员看到 [result.points_earned]；会员可从 P09 会员模式的 [account.order_pay]
  回到 P06 继续支付或取消。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-091 验收标准里的约定（下称「验收」）。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表，引擎与夹具照
tests/test_demo_payment.py；接口的会话依赖换成每个请求一个绑定同一内存库的会话。会员订单照
tests/test_payment_coupons_points.py 按模型写入，券与积分经 reserve_coupon、reserve_points
预占，来源批次经 earn_points；会员会话经 issue_member_session 签发并提交，cookie 与 CSRF
令牌放进请求头（写法同 tests/test_member_pw_api.py）；游客订单的访问 cookie 经
issue_order_access 签发。
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Coupon,
    CouponUse,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    OrderRecipient,
    PaymentAttempt,
    PointEntry,
    PointLot,
    Product,
    ProductVariant,
)
from app.models.coupon import TYPE_FIXED, USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_MEMBER,
    ACTOR_SYSTEM,
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.models.points import KIND_EARN, KIND_REDEEM, KIND_RELEASE
from app.services import member_auth, order_access
from app.services.coupons import reserve_coupon
from app.services.points import available_points, earn_points, reserve_points

ORDER_URL = "/api/member/pay/order"
PAY_URL = "/api/member/pay/attempts"
CANCEL_URL = "/api/member/pay/cancel"
GUEST_PAY_URL = "/api/pay/attempts"
GUEST_CANCEL_URL = "/api/pay/cancel"
URLS = [ORDER_URL, PAY_URL, CANCEL_URL]
MAX_BODY_BYTES = 8 * 1024

MEMBER_COOKIE = "__Host-shop_member_session"
ACCESS_COOKIE = order_access.COOKIE_NAME

NO_STORE = "no-store"
SESSION_REQUIRED = "member_session_required"
CSRF_FAILED = "csrf_failed"
NOT_FOUND = "order_not_found"

# 不合格式与格式合法但不存在的订单号（下面建的订单号都是数字）。
INVALID_NUMBER = "not-an-order"
UNKNOWN_NUMBER = "Z" * 16

# 会员订单：一行 2 件、单价 3000 仙；券抵 1000 仙，积分抵 500，运费 800，获得积分快照 45。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
COUPON_OFF = 1000
REDEEMED = 500
SHIPPING = 800
EARNED = 45
INITIAL_STOCK = 5
RESERVED_STOCK = INITIAL_STOCK - QUANTITY
SOURCE_POINTS = 1000
WINDOW = timedelta(minutes=15)

RECIPIENT = {
    "name": "Zubaidah Secretname",
    "phone": "+60123456789",
    "country_code": "MY",
    "region": "MY-10",
    "address": "77 Hiddenlane Road",
    "postal_code": "50000",
}
FINGERPRINT = "0f" * 32

ORDER_FIELDS = {
    "order_number",
    "status",
    "created_at",
    "payment_expires_at",
    "server_time",
    "lines",
    "subtotal_sen",
    "coupon_discount_sen",
    "points_redeemed",
    "shipping_fee_sen",
    "total_sen",
    "points_earned",
    "recipient",
    "last_payment",
    "cancelled_by",
    # SHOP-TASK-119 新增的 Stripe 字段（DESIGN 1.12 第 2 条、UX 0.11 P06：服务端给出支付途径与
    # Stripe 状态）；取值见 tests/test_stripe_pay_view.py。
    "payment_route",
    "stripe_state",
    "stripe_refunded",
}

PLACED_BY_MEMBER = (None, STATUS_AWAITING_PAYMENT, ACTOR_MEMBER)
PLACED_BY_GUEST = (None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST)
PAID_BY_MEMBER = (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_MEMBER)
CANCELLED_BY_MEMBER = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_MEMBER)
CANCELLED_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)

_SEQUENCE = itertools.count(1)


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
    app = create_app(Settings(_env_file=None))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


def _browser(app: FastAPI) -> TestClient:
    # 每个客户端是一个浏览器。https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return _browser(app)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


@dataclass(frozen=True)
class _Placed:
    order_number: str
    order_id: int
    member_id: int | None
    variant_id: int


def _member(db: Session) -> int:
    """一个 active 会员，另有一个 1000 积分的来源批次（另一张已支付订单所得）；写完即提交。"""
    phone = f"+60123{next(_SEQUENCE):06d}"
    earned_at = _now() - timedelta(days=30)
    member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=earned_at))
    source = _order_row(db, member.id, earned_at, status=STATUS_PAID)
    assert earn_points(db, member.id, source.id, SOURCE_POINTS, earned_at) is not None
    db.commit()
    return member.id


def _variant(db: Session) -> int:
    n = next(_SEQUENCE)
    category = _add(db, Category(slug=f"goods-{n}", name_en="Goods", is_active=True))
    product = Product(
        category_id=category.id,
        slug=f"tee-{n}",
        name_en="Tee",
        name_zh="T恤",
        description_en="A tee.",
        is_active=True,
    )
    _add(db, product)
    variant = ProductVariant(
        product_id=product.id,
        sku=f"TEE-{n}",
        price_sen=UNIT_PRICE,
        daily_initial_stock=INITIAL_STOCK,
        available_stock=RESERVED_STOCK,
        is_active=True,
    )
    return _add(db, variant).id


def _order_row(
    db: Session,
    member_id: int | None,
    placed_at: datetime,
    *,
    status: str = STATUS_AWAITING_PAYMENT,
    coupon_off: int = 0,
    redeemed: int = 0,
    earned: int = 0,
    claim_status: str | None = None,
) -> Order:
    n = next(_SEQUENCE)
    if claim_status is None:
        claim_status = CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE
    order = Order(
        order_number=f"{n:016d}",
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=coupon_off,
        points_redeemed=redeemed,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL - coupon_off - redeemed + SHIPPING,
        points_earned=earned,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=placed_at,
        payment_expires_at=placed_at + WINDOW,
        paid_at=placed_at + timedelta(minutes=1) if status == STATUS_PAID else None,
        member_id=member_id,
        claim_status=claim_status,
    )
    return _add(db, order)


def _items_recipient_event(db: Session, order: Order, variant_id: int, actor: str) -> None:
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=variant_id,
        sku="TEE",
        product_name_en="Tee",
        product_name_zh="T恤",
        product_name_ms="Tee",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=UNIT_PRICE,
        quantity=QUANTITY,
        line_subtotal_sen=SUBTOTAL,
    )
    _add(db, item)
    _add(db, OrderRecipient(order_id=order.id, **RECIPIENT))
    placed = OrderEvent(
        order_id=order.id,
        from_status=None,
        to_status=STATUS_AWAITING_PAYMENT,
        actor_type=actor,
        created_at=order.created_at,
    )
    _add(db, placed)


def _coupon(db: Session) -> Coupon:
    now = _now()
    coupon = Coupon(
        code=f"SAVE{next(_SEQUENCE)}",
        coupon_type=TYPE_FIXED,
        amount_off_sen=COUPON_OFF,
        percent_off=None,
        min_subtotal_sen=0,
        starts_at=now - timedelta(days=365),
        ends_at=now + timedelta(days=365),
        total_use_limit=None,
        per_member_use_limit=None,
        is_active=True,
    )
    return _add(db, coupon)


def _member_order(db: Session, member_id: int, *, placed_at: datetime | None = None) -> _Placed:
    """会员下的待支付订单：预占券 1000 仙与积分 500，获得积分快照 45；写完即提交。

    placed_at 默认 1 分钟前（未到支付到期时间）。
    """
    placed_at = placed_at or _now() - timedelta(minutes=1)
    variant_id = _variant(db)
    order = _order_row(
        db, member_id, placed_at, coupon_off=COUPON_OFF, redeemed=REDEEMED, earned=EARNED
    )
    _items_recipient_event(db, order, variant_id, ACTOR_MEMBER)
    coupon = _coupon(db)
    check = reserve_coupon(db, coupon.id, order.id, member_id, COUPON_OFF, SUBTOTAL, placed_at)
    assert check.ok
    reserve_points(db, member_id, order.id, REDEEMED, placed_at)
    db.commit()
    return _Placed(order.order_number, order.id, member_id, variant_id)


def _paid_member_order(db: Session, member_id: int, placed_at: datetime) -> _Placed:
    """会员下的、已模拟支付的订单（不带券与积分预占），获得积分快照 45；写完即提交。"""
    variant_id = _variant(db)
    order = _order_row(db, member_id, placed_at, status=STATUS_PAID, earned=EARNED)
    _items_recipient_event(db, order, variant_id, ACTOR_MEMBER)
    db.commit()
    return _Placed(order.order_number, order.id, member_id, variant_id)


def _guest_order(
    db: Session, *, claimed_by: int | None = None, placed_at: datetime | None = None
) -> _Placed:
    """游客下的待支付订单（没有券与积分）；claimed_by 给出时为该会员认领。写完即提交。"""
    placed_at = placed_at or _now() - timedelta(minutes=1)
    variant_id = _variant(db)
    claim_status = CLAIM_OPEN if claimed_by is None else CLAIM_CLAIMED
    order = _order_row(db, claimed_by, placed_at, claim_status=claim_status)
    _items_recipient_event(db, order, variant_id, ACTOR_GUEST)
    db.commit()
    return _Placed(order.order_number, order.id, claimed_by, variant_id)


def _login(db: Session, member_id: int) -> dict[str, str]:
    """签发会员会话并提交；返回带会话 cookie 与 CSRF 令牌的请求头。"""
    member = db.get(Member, member_id)
    assert member is not None
    issued = member_auth.issue_member_session(db, member, _now())
    db.commit()
    csrf = member_auth.csrf_token_for_cookie(issued.token)
    assert csrf is not None
    return {"Cookie": f"{MEMBER_COOKIE}={issued.token}", "X-CSRF-Token": csrf}


def _guest_access(db: Session, placed: _Placed) -> dict[str, str]:
    """签发该单的 guest_checkout 授权并提交；返回带访问 cookie 与 CSRF 令牌的请求头。"""
    issued = order_access.issue_order_access(
        db, None, placed.order_id, SCOPE_GUEST_CHECKOUT, _now()
    )
    db.commit()
    assert issued.new_token is not None
    return {"Cookie": f"{ACCESS_COOKIE}={issued.new_token}", "X-CSRF-Token": issued.csrf_token}


def _key() -> str:
    return f"pay-key-{next(_SEQUENCE):012d}"


def _view(client: TestClient, headers: dict[str, str], number: str, **params: str) -> Any:
    return client.post(ORDER_URL, json={"order_number": number}, headers=headers, params=params)


def _pay(
    client: TestClient,
    headers: dict[str, str],
    number: str,
    *,
    method: str = "card",
    result: str = "success",
    key: str | None = None,
    url: str = PAY_URL,
) -> Any:
    body = {"order_number": number, "method": method, "result": result}
    return client.post(url, json=body, headers=headers | {"Idempotency-Key": key or _key()})


def _cancel(
    client: TestClient, headers: dict[str, str], number: str, *, url: str = CANCEL_URL
) -> Any:
    return client.post(url, json={"order_number": number}, headers=headers)


def _post(client: TestClient, url: str, headers: dict[str, str], number: str) -> Any:
    """按 url 调用三个接口之一（支付为成功、新幂等键）。"""
    if url == ORDER_URL:
        return _view(client, headers, number)
    if url == PAY_URL:
        return _pay(client, headers, number)
    return _cancel(client, headers, number)


def _assert_error(response: Any, status: int, detail: str) -> None:
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []


def _status(db: Session, placed: _Placed) -> str:
    db.expire_all()
    return db.scalars(select(Order.status).where(Order.id == placed.order_id)).one()


def _events(db: Session, placed: _Placed) -> list[tuple[str | None, str, str]]:
    db.expire_all()
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == placed.order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _attempt_count(db: Session, placed: _Placed) -> int:
    stmt = select(PaymentAttempt.id).where(PaymentAttempt.order_id == placed.order_id)
    return len(list(db.scalars(stmt)))


def _coupon_uses(db: Session, placed: _Placed) -> list[str]:
    db.expire_all()
    stmt = select(CouponUse.status).where(CouponUse.order_id == placed.order_id)
    return list(db.scalars(stmt))


def _order_entry_kinds(db: Session, placed: _Placed) -> list[str]:
    stmt = (
        select(PointEntry.kind)
        .where(PointEntry.order_id == placed.order_id)
        .order_by(PointEntry.id)
    )
    return list(db.scalars(stmt))


def _lots(db: Session, placed: _Placed) -> list[tuple[int, int]]:
    stmt = select(PointLot.member_id, PointLot.points).where(PointLot.order_id == placed.order_id)
    return [tuple(row) for row in db.execute(stmt)]


def _stock(db: Session, placed: _Placed) -> int:
    db.expire_all()
    stmt = select(ProductVariant.available_stock).where(ProductVariant.id == placed.variant_id)
    return db.scalars(stmt).one()


def _assert_untouched(db: Session, placed: _Placed, placed_event: tuple[Any, ...]) -> None:
    """订单仍待支付：没有支付尝试与新事件，库存仍预留，用券记录（若有）仍为 reserved。"""
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT
    assert _events(db, placed) == [placed_event]
    assert _attempt_count(db, placed) == 0
    assert _stock(db, placed) == RESERVED_STOCK
    assert set(_coupon_uses(db, placed)) <= {USE_RESERVED}


# ---------------------------------------------------------------------------
# 查看：POST /api/member/pay/order
# ---------------------------------------------------------------------------


def test_view_own_pending_order(db: Session, client: TestClient) -> None:
    """P06「会员凭登录会话访问」；第 6 条「会员仅能访问自己…下单的订单」；凭据页面
    「可显示该单收货资料原文」。验收：以 pay_orders 的视图返回（含收货资料与券折扣、积分抵扣、
    获得积分三个字段），响应带 no-store、不设 cookie，只读、不写库。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)

    response = _view(client, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    view = response.json()
    assert set(view) == ORDER_FIELDS
    assert (view["order_number"], view["status"]) == (placed.order_number, STATUS_AWAITING_PAYMENT)
    assert view["recipient"] == RECIPIENT
    snapshot = (view["coupon_discount_sen"], view["points_redeemed"], view["points_earned"])
    assert snapshot == (COUPON_OFF, REDEEMED, EARNED)
    assert view["total_sen"] == SUBTOTAL - COUPON_OFF - REDEEMED + SHIPPING
    assert (view["last_payment"], view["cancelled_by"]) == (None, None)
    _assert_untouched(db, placed, PLACED_BY_MEMBER)


def test_view_follows_language(db: Session, client: TestClient) -> None:
    """验收：语言参数同 GET /api/pay/orders（订单行快照按请求语言取）；不合法时 422 不回显。"""
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)

    zh = _view(client, headers, placed.order_number, lang="zh")
    bad = _view(client, headers, placed.order_number, lang="klingon")

    assert zh.status_code == 200, zh.text
    assert zh.json()["lines"][0]["name"] == "T恤"
    assert bad.status_code == 422, bad.text
    assert bad.headers["cache-control"] == NO_STORE
    assert "klingon" not in bad.text


def test_view_old_paid_order(db: Session, client: TestClient) -> None:
    """P07「成功后会员看到本单获得积分」；UX P09 会员模式按订单号回到支付页与结果页。
    验收：查看不限创建时间——200 天前下单、已支付的订单照常返回，带 points_earned。
    """
    member_id = _member(db)
    placed = _paid_member_order(db, member_id, _now() - timedelta(days=200))
    headers = _login(db, member_id)

    response = _view(client, headers, placed.order_number)

    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["status"], view["points_earned"]) == (STATUS_PAID, EARNED)


def test_view_claimed_guest_order(db: Session, client: TestClient) -> None:
    """第 6 条「会员仅能访问自己认领或下单的订单」：会员认领的游客订单可以查看。"""
    member_id = _member(db)
    placed = _guest_order(db, claimed_by=member_id)
    headers = _login(db, member_id)

    response = _view(client, headers, placed.order_number)

    assert response.status_code == 200, response.text
    assert response.json()["points_earned"] == 0


def test_view_cancels_overdue_order_first(db: Session, client: TestClient) -> None:
    """「状态」「…或超时为 demo_cancelled，释放库存、券及积分预占」。验收：订单待支付时先对它
    执行 expire_overdue_orders，查看时超时的订单已为 demo_cancelled、取消方为超时，库存加回，
    券与积分已释放，事件操作者为 system。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id, placed_at=_now() - timedelta(minutes=20))
    headers = _login(db, member_id)

    response = _view(client, headers, placed.order_number)

    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["status"], view["cancelled_by"]) == (STATUS_CANCELLED, "timeout")
    assert _events(db, placed) == [PLACED_BY_MEMBER, CANCELLED_BY_SYSTEM]
    assert _stock(db, placed) == INITIAL_STOCK
    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert _order_entry_kinds(db, placed) == [KIND_REDEEM, KIND_RELEASE]


@pytest.mark.parametrize("case", ["other-member", "guest", "unknown", "invalid"])
def test_view_of_others_is_404(db: Session, client: TestClient, case: str) -> None:
    """第 6 条「会员仅能访问自己认领或下单的订单」。验收：其他会员与游客的订单、不存在或
    不合法的订单号一律 404 order_not_found，响应相同、不回显订单号，不改订单。
    """
    member_id = _member(db)
    headers = _login(db, member_id)
    target: _Placed | None = None
    number = UNKNOWN_NUMBER if case == "unknown" else INVALID_NUMBER
    if case == "other-member":
        target = _member_order(db, _member(db))
    elif case == "guest":
        target = _guest_order(db)
    if target is not None:
        number = target.order_number

    response = _view(client, headers, number)

    _assert_error(response, 404, NOT_FOUND)
    assert number not in response.text
    if target is not None:
        placed_event = PLACED_BY_GUEST if case == "guest" else PLACED_BY_MEMBER
        _assert_untouched(db, target, placed_event)


# ---------------------------------------------------------------------------
# 模拟支付：POST /api/member/pay/attempts
# ---------------------------------------------------------------------------


def test_payment_success_uses_coupon_and_earns(db: Session, client: TestClient) -> None:
    """第 3 条「模拟支付成功后获得」；P07「成功后会员看到本单获得积分」；「状态」所有状态
    迁移校验操作者、事务内写事件。验收：支付成功写 member 事件、用券记录为 used 且发放积分，
    视图给出 points_earned；成功 201 的响应体同游客接口，带 no-store。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)

    response = _pay(client, headers, placed.order_number)

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == NO_STORE
    body = response.json()
    assert set(body) == {"method", "result", "status", "paid_at"}
    assert (body["method"], body["result"], body["status"]) == ("card", "success", STATUS_PAID)
    assert _events(db, placed) == [PLACED_BY_MEMBER, PAID_BY_MEMBER]
    assert _coupon_uses(db, placed) == [USE_USED]
    assert _lots(db, placed) == [(member_id, EARNED)]
    assert _order_entry_kinds(db, placed) == [KIND_REDEEM, KIND_EARN]
    assert _stock(db, placed) == RESERVED_STOCK

    view = _view(client, headers, placed.order_number).json()
    assert (view["status"], view["points_earned"]) == (STATUS_PAID, EARNED)
    assert (view["last_payment"]["method"], view["last_payment"]["result"]) == ("card", "success")


def test_payment_failure_keeps_order(db: Session, client: TestClient) -> None:
    """「模拟支付失败仍停在此状态并记录失败尝试」；第 3 条（失败不获得）。验收：失败只写支付
    尝试，不核销、不发放，可再支付。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)

    response = _pay(client, headers, placed.order_number, result="failure")

    assert response.status_code == 201, response.text
    assert response.json()["status"] == STATUS_AWAITING_PAYMENT
    assert _attempt_count(db, placed) == 1
    assert _events(db, placed) == [PLACED_BY_MEMBER]
    assert _coupon_uses(db, placed) == [USE_RESERVED]
    assert _lots(db, placed) == []


def test_claimed_guest_order_can_be_paid(db: Session, client: TestClient) -> None:
    """第 6 条「会员仅能访问自己认领或下单的订单」；P09 会员模式回到 P06 继续支付。
    验收：认领的游客订单以 member 为操作者支付。
    """
    member_id = _member(db)
    placed = _guest_order(db, claimed_by=member_id)
    headers = _login(db, member_id)

    response = _pay(client, headers, placed.order_number)

    assert response.status_code == 201, response.text
    assert _events(db, placed) == [PLACED_BY_GUEST, PAID_BY_MEMBER]


# ---------------------------------------------------------------------------
# 取消：POST /api/member/pay/cancel
# ---------------------------------------------------------------------------


def test_cancel_releases_coupon_points_and_stock(db: Session, client: TestClient) -> None:
    """「状态」「待支付订单可由下单者在支付页取消（…会员凭登录会话）…释放库存、券及积分预占」。
    验收：取消写 member 事件、释放券与积分（可用余额恢复）、加回库存；重复取消返回当前状态，
    不再写事件或释放；响应同游客接口，带 no-store。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)
    now = _now()
    assert available_points(db, member_id, now) == SOURCE_POINTS - REDEEMED

    first = _cancel(client, headers, placed.order_number)
    again = _cancel(client, headers, placed.order_number)

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == NO_STORE
    assert first.json() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert _events(db, placed) == [PLACED_BY_MEMBER, CANCELLED_BY_MEMBER]
    assert _coupon_uses(db, placed) == [USE_RELEASED]
    assert _order_entry_kinds(db, placed) == [KIND_REDEEM, KIND_RELEASE]
    db.expire_all()
    assert available_points(db, member_id, now) == SOURCE_POINTS
    assert _stock(db, placed) == INITIAL_STOCK


# ---------------------------------------------------------------------------
# 授权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
@pytest.mark.parametrize("case", ["other-member", "guest", "unknown", "invalid"])
def test_write_on_others_order_is_404(db: Session, client: TestClient, url: str, case: str) -> None:
    """第 6 条「会员仅能访问自己认领或下单的订单」。验收：他人或游客的订单号、不存在或不合法的
    订单号一律 404 order_not_found 且不改订单（不写支付尝试与事件，不动库存、券与积分）。
    """
    member_id = _member(db)
    headers = _login(db, member_id)
    target: _Placed | None = None
    number = UNKNOWN_NUMBER if case == "unknown" else INVALID_NUMBER
    if case == "other-member":
        target = _member_order(db, _member(db))
    elif case == "guest":
        target = _guest_order(db)
    if target is not None:
        number = target.order_number

    response = _post(client, url, headers, number)

    _assert_error(response, 404, NOT_FOUND)
    assert number not in response.text
    if target is not None:
        placed_event = PLACED_BY_GUEST if case == "guest" else PLACED_BY_MEMBER
        _assert_untouched(db, target, placed_event)


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
@pytest.mark.parametrize("case", ["missing", "wrong", "two-headers", "guest-token"])
def test_write_without_csrf_is_403(db: Session, client: TestClient, url: str, case: str) -> None:
    """第 6 条「支付、取消…等写操作另须 CSRF 令牌」。验收：缺、错、重复或拿游客订单的 CSRF
    令牌一律 403 csrf_failed，不改订单。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)
    cookie = {"Cookie": headers["Cookie"]}
    if case == "missing":
        headers = cookie
    elif case == "wrong":
        headers = cookie | {"X-CSRF-Token": "0" * 64}
    elif case == "guest-token":
        guest = _guest_access(db, _guest_order(db))
        headers = cookie | {"X-CSRF-Token": guest["X-CSRF-Token"]}

    if case == "two-headers":
        body = {"order_number": placed.order_number}
        raw = [("Cookie", headers["Cookie"]), ("Content-Type", "application/json")]
        raw += [("X-CSRF-Token", headers["X-CSRF-Token"])] * 2
        if url == PAY_URL:
            body |= {"method": "card", "result": "success"}
            raw.append(("Idempotency-Key", _key()))
        response = client.post(url, content=json.dumps(body), headers=raw)
    else:
        response = _post(client, url, headers, placed.order_number)

    _assert_error(response, 403, CSRF_FAILED)
    _assert_untouched(db, placed, PLACED_BY_MEMBER)


def test_csrf_is_checked_before_order_lookup(db: Session, client: TestClient) -> None:
    """验收：授权顺序为 require_member → require_member_csrf → 查订单；没有 CSRF 时其他会员的
    订单也是 403，不因订单号而不同。
    """
    member_id = _member(db)
    other = _member_order(db, _member(db))
    headers = {"Cookie": _login(db, member_id)["Cookie"]}

    for url in [PAY_URL, CANCEL_URL]:
        _assert_error(_post(client, url, headers, other.order_number), 403, CSRF_FAILED)
        _assert_error(_post(client, url, headers, UNKNOWN_NUMBER), 403, CSRF_FAILED)


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("case", ["no-cookie", "malformed", "revoked", "guest-access-cookie"])
def test_without_member_session_is_401(
    db: Session, client: TestClient, url: str, case: str
) -> None:
    """P06、P07「会员凭登录会话」；第 6 条（会员访问须有会话）。验收：只凭会员会话——未登录、
    会话不合法或已撤销 401 member_session_required；游客的订单访问 cookie（连同它的 CSRF
    令牌）不能用于这些接口，也是 401，不改订单。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    target = placed
    headers: dict[str, str] = {}
    if case == "malformed":
        headers = {"Cookie": f"{MEMBER_COOKIE}=not-a-token", "X-CSRF-Token": "0" * 64}
    elif case == "revoked":
        headers = _login(db, member_id)
        assert client.post("/api/member/logout", headers=headers).status_code == 204
    elif case == "guest-access-cookie":
        target = _guest_order(db)
        headers = _guest_access(db, target)

    response = _post(client, url, headers, target.order_number)

    _assert_error(response, 401, SESSION_REQUIRED)
    placed_event = PLACED_BY_GUEST if target is not placed else PLACED_BY_MEMBER
    _assert_untouched(db, target, placed_event)


@pytest.mark.parametrize("url", URLS)
def test_guest_access_cookie_does_not_widen_member_access(
    db: Session, client: TestClient, url: str
) -> None:
    """第 6 条「会员仅能访问自己认领或下单的订单」；验收「不读订单访问 cookie」：已登录会员的
    浏览器同时持有某张游客订单的 guest_checkout 授权，借它的订单号访问仍是 404，订单不变。
    """
    member_id = _member(db)
    guest = _guest_order(db)
    member_headers = _login(db, member_id)
    guest_headers = _guest_access(db, guest)
    headers = {
        "Cookie": f"{member_headers['Cookie']}; {guest_headers['Cookie']}",
        "X-CSRF-Token": member_headers["X-CSRF-Token"],
    }

    response = _post(client, url, headers, guest.order_number)

    _assert_error(response, 404, NOT_FOUND)
    _assert_untouched(db, guest, PLACED_BY_GUEST)


# ---------------------------------------------------------------------------
# 重放与 409：与游客接口一致
# ---------------------------------------------------------------------------


SCENARIOS = {
    "replay": 200,
    "key-conflict": 409,
    "paid-then-pay": 409,
    "paid-then-cancel": 409,
    "cancelled-then-pay": 409,
    "repeat-cancel": 200,
    "overdue-pay": 409,
    "overdue-cancel": 200,
}


def _scenario(name: str, pay: Callable[..., Any], cancel: Callable[[], Any]) -> Any:
    """按场景先做准备的请求，再返回最后一个请求的响应。"""
    key = _key()
    if name == "replay":
        assert pay(key=key, result="failure").status_code == 201
        return pay(key=key, result="failure")
    if name == "key-conflict":
        assert pay(key=key, result="failure").status_code == 201
        return pay(key=key, result="success")
    if name in ("paid-then-pay", "paid-then-cancel"):
        assert pay().status_code == 201
        return pay(method="bank") if name == "paid-then-pay" else cancel()
    if name in ("cancelled-then-pay", "repeat-cancel"):
        assert cancel().status_code == 200
        return pay() if name == "cancelled-then-pay" else cancel()
    return pay() if name == "overdue-pay" else cancel()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_replay_and_conflicts_match_guest_interface(
    app: FastAPI, db: Session, client: TestClient, name: str
) -> None:
    """重试第 1 条「相同键相同请求返回原结果，同键不同内容报冲突」；「状态」「已模拟支付订单
    不走取消」与超时取消。验收：成功与 409 的状态码、响应体与 detail 和游客接口相同——同一
    场景分别在会员订单上调用会员接口、在游客订单上调用游客接口，响应相同且都带 no-store。
    """
    placed_at = _now() - timedelta(minutes=20) if name.startswith("overdue") else None
    member_id = _member(db)
    mine = _member_order(db, member_id, placed_at=placed_at)
    member_headers = _login(db, member_id)
    guest = _guest_order(db, placed_at=placed_at)
    guest_headers = _guest_access(db, guest)
    guest_client = _browser(app)

    def member_pay(**kwargs: Any) -> Any:
        return _pay(client, member_headers, mine.order_number, **kwargs)

    def member_cancel() -> Any:
        return _cancel(client, member_headers, mine.order_number)

    def guest_pay(**kwargs: Any) -> Any:
        return _pay(guest_client, guest_headers, guest.order_number, url=GUEST_PAY_URL, **kwargs)

    def guest_cancel() -> Any:
        return _cancel(guest_client, guest_headers, guest.order_number, url=GUEST_CANCEL_URL)

    member_response = _scenario(name, member_pay, member_cancel)
    guest_response = _scenario(name, guest_pay, guest_cancel)

    assert member_response.status_code == SCENARIOS[name], member_response.text
    assert guest_response.status_code == member_response.status_code
    assert member_response.json() == guest_response.json()
    assert member_response.headers["cache-control"] == NO_STORE
    if name == "replay":
        assert _attempt_count(db, mine) == 1


# ---------------------------------------------------------------------------
# 请求格式与检查顺序
# ---------------------------------------------------------------------------


def _body_for(url: str, number: str) -> dict[str, Any]:
    if url == PAY_URL:
        return {"order_number": number, "method": "card", "result": "success"}
    return {"order_number": number}


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("with_session", [False, True], ids=["no-session", "session"])
def test_oversized_body_is_413_first(
    db: Session, client: TestClient, url: str, with_session: bool
) -> None:
    """验收：请求体上限 8 KB，按实际读到的字节逐块判断，超过先于一切 413——没有会话、
    不是 JSON 的超限请求体也是 413；不回显请求内容。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id) if with_session else {}
    padding = "4111111111111111" * (MAX_BODY_BYTES // 16)
    body = _body_for(url, placed.order_number) | {"card_number": padding}
    headers = headers | {"Content-Type": "text/plain"}

    response = client.post(url, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert "4111111111111111" not in response.text
    _assert_untouched(db, placed, PLACED_BY_MEMBER)


@pytest.mark.parametrize("url", URLS)
def test_non_json_is_415_before_session(db: Session, client: TestClient, url: str) -> None:
    """验收：不是 JSON 415，先于 require_member（没有会话也是 415）。"""
    placed = _member_order(db, _member(db))
    body = json.dumps(_body_for(url, placed.order_number))

    response = client.post(url, content=body, headers={"Content-Type": "text/plain"})

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == NO_STORE


def test_view_session_is_checked_before_body(db: Session, client: TestClient) -> None:
    """验收：查看的检查顺序为 413 → 415 → require_member → 422——请求体多出字段但没有会话时
    401；有会话时 422，只给位置、类型与消息，不回显。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    body = {"order_number": placed.order_number, "phone": "+60111111111"}

    anonymous = client.post(ORDER_URL, json=body)
    logged_in = client.post(ORDER_URL, json=body, headers=_login(db, member_id))

    _assert_error(anonymous, 401, SESSION_REQUIRED)
    assert logged_in.status_code == 422, logged_in.text
    assert logged_in.headers["cache-control"] == NO_STORE
    errors = logged_in.json()["detail"]
    assert ["body", "phone"] in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert "+60111111111" not in logged_in.text
    assert placed.order_number not in logged_in.text


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"extra": "4111111111111111"}, ["body", "extra"], id="extra-field"),
        pytest.param({"order_number": 12345}, ["body", "order_number"], id="number"),
        pytest.param(None, ["body", "order_number"], id="no-order-number"),
    ],
)
def test_invalid_body_is_422_without_echo(
    db: Session, client: TestClient, url: str, change: dict[str, Any] | None, loc: list[str]
) -> None:
    """验收：请求体只有订单号（支付另有方式与结果），多出字段 422；422 不回显请求内容。"""
    member_id = _member(db)
    placed = _member_order(db, member_id)
    body = _body_for(url, placed.order_number)
    if change is None:
        del body["order_number"]
    else:
        body |= change
    headers = _login(db, member_id) | {"Idempotency-Key": _key()}

    response = client.post(url, json=body, headers=headers)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert loc in [error["loc"] for error in response.json()["detail"]]
    for value in ["4111111111111111", "12345", placed.order_number]:
        assert value not in response.text
    _assert_untouched(db, placed, PLACED_BY_MEMBER)


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
def test_write_body_is_checked_before_session(db: Session, client: TestClient, url: str) -> None:
    """验收：两个写接口的检查顺序沿用 app/api/pay.py（413 → 415 → 422 → 授权）——没有会话时
    多出字段仍是 422。
    """
    placed = _member_order(db, _member(db))
    body = _body_for(url, placed.order_number) | {"reason": "changed my mind"}

    response = client.post(url, json=body, headers={"Idempotency-Key": _key()})

    assert response.status_code == 422, response.text
    assert "changed my mind" not in response.text


@pytest.mark.parametrize("key", [None, "a" * 15, "a" * 20 + "!"], ids=["missing", "short", "bad"])
def test_payment_needs_idempotency_key(db: Session, client: TestClient, key: str | None) -> None:
    """重试第 1 条「模拟支付…使用幂等键」。验收：幂等键只有支付要，规则同下单接口，缺或不合法
    422 且不写支付尝试；查看与取消不要求幂等键（见其他用例）。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post(PAY_URL, json=_body_for(PAY_URL, placed.order_number), headers=headers)

    assert response.status_code == 422, response.text
    assert ["header", "Idempotency-Key"] in [error["loc"] for error in response.json()["detail"]]
    assert _attempt_count(db, placed) == 0


def test_order_number_never_in_path_or_query(app: FastAPI) -> None:
    """第 6 条凭据不写入「网址」；验收：订单号只在请求体里，不进任何路径或查询参数；查看与
    Stripe 发起只多一个查询参数 lang，其余接口不带查询参数。

    SHOP-TASK-121 修改：同一路由器新增 POST /api/member/pay/stripe/start 与
    POST /api/member/pay/stripe/confirm（docs/DESIGN.md 1.12 第 3、4 条，会员凭登录会话发起与
    确认），穷举的路由加入这两个；发起与游客的发起接口一样带 lang，确认不带任何查询参数。
    """
    stripe_start_url = "/api/member/pay/stripe/start"
    stripe_confirm_url = "/api/member/pay/stripe/confirm"
    paths = {
        path: operations
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/member/pay")
    }

    assert {(path, method) for path, ops in paths.items() for method in ops} == {
        (ORDER_URL, "post"),
        (PAY_URL, "post"),
        (CANCEL_URL, "post"),
        (stripe_start_url, "post"),
        (stripe_confirm_url, "post"),
    }
    for path, operations in paths.items():
        names = {param["name"] for param in operations["post"].get("parameters", [])}
        assert names == ({"lang"} if path in (ORDER_URL, stripe_start_url) else set())
        assert "{" not in path


# ---------------------------------------------------------------------------
# no-store 与日志
# ---------------------------------------------------------------------------


def test_every_response_is_no_store(db: Session, client: TestClient) -> None:
    """第 6 条「不缓存敏感响应」。验收：三个接口的处理函数与依赖产生的响应（含错误）都带
    no-store——逐一走到 200、201、401、403、404、409、413、415、422。
    """
    member_id = _member(db)
    placed = _member_order(db, member_id)
    headers = _login(db, member_id)
    cookie = {"Cookie": headers["Cookie"]}
    big = json.dumps({"order_number": "x" * (MAX_BODY_BYTES + 1)})

    responses = [
        _view(client, headers, placed.order_number),
        _pay(client, headers, placed.order_number),
        _pay(client, headers, placed.order_number),
        _view(client, {}, placed.order_number),
        _cancel(client, cookie, placed.order_number),
        _cancel(client, headers, UNKNOWN_NUMBER),
        client.post(ORDER_URL, content=big, headers=headers),
        client.post(CANCEL_URL, content="{}", headers=headers | {"Content-Type": "text/plain"}),
        client.post(CANCEL_URL, json={}, headers=headers),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 201, 409, 401, 403, 404, 413, 415, 422]
    for response in responses:
        assert response.headers["cache-control"] == NO_STORE


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条「应用日志…不记录…」；验收「不写日志」：三个接口的成功与错误都不写 app 日志。"""
    member_id = _member(db)
    paid = _member_order(db, member_id)
    cancelled = _member_order(db, member_id)
    headers = _login(db, member_id)
    caplog.set_level(logging.DEBUG)

    _view(client, headers, paid.order_number)
    _pay(client, headers, paid.order_number, result="failure")
    _pay(client, headers, paid.order_number)
    _cancel(client, headers, cancelled.order_number)
    _cancel(client, headers, UNKNOWN_NUMBER)
    _view(client, {}, paid.order_number)

    assert [record for record in caplog.records if record.name.startswith("app")] == []

"""模拟支付、取消与支付超时：GET /api/pay/orders、POST /api/pay/attempts、
POST /api/pay/cancel 与 app/services/payment.py 的超时取消函数。

依据 docs/DESIGN.md 1.10（提交 e3b3505）：「订单与退款状态」第 1、3、5 条；
「计价、优惠、积分与库存」第 6 条；「失败、并发与重试」第 1、2 条；
「数据模型」的 PaymentAttempt / OrderEvent 一行；「权限与资料保护」第 6、7 条。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句；
没有直接原句的，写明是 SHOP-TASK-021 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。
接口的会话依赖换成每个请求一个绑定同一内存库的会话；订单都经 SHOP-TASK-020
的下单接口建立，cookie 与 CSRF 令牌取自下单响应。内存库经 StaticPool
只有一个连接，pysqlite 在第一条写语句前才开始事务，所以在一个会话还只做过
查询时，用第二个会话执行并提交另一个操作，就能模拟并发请求先提交。
交错点有两个：payment 模块在带条件的 UPDATE 之前调用迁移判定函数时，
以及它第一次按幂等键查支付尝试之后。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Order,
    OrderAccessGrant,
    OrderEvent,
    OrderItem,
    PaymentAttempt,
    Product,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    ShippingRate,
    VariantOptionValue,
)
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_SYSTEM,
    PAYMENT_FAILED,
    PAYMENT_METHOD_BANK,
    PAYMENT_METHOD_CARD,
    PAYMENT_METHOD_EWALLET,
    PAYMENT_SUCCEEDED,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.order_access import SCOPE_LOOKUP
from app.services import payment
from app.services.order_access import COOKIE_NAME, csrf_token_for_cookie, issue_order_access

ORDER_URL = "/api/orders/guest"
ORDERS_URL = "/api/pay/orders"
PAY_URL = "/api/pay/attempts"
CANCEL_URL = "/api/pay/cancel"
MAX_BODY_BYTES = 8 * 1024

PHONE_E164 = "+60123456789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"
SHIPPING = 800

# 默认订单：TEE-RED-M 2 件（2590 仙）、MUG 1 件（1500 仙），寄马来西亚 MY-10。
DEFAULT_LINES = [{"sku": "TEE-RED-M", "quantity": 2}, {"sku": "MUG", "quantity": 1}]
SUBTOTAL = 2 * 2590 + 1500
# TEE-RED-M、TEE-BLUE-M、MUG 的当日可用库存：下单前、默认订单下单后。
INITIAL_STOCK = (5, 5, 3)
RESERVED_STOCK = (3, 5, 2)
# 颜色规格值：代码、英文、中文。
TEE_COLOURS = [("red", "Red", "红色"), ("blue", "Blue", "蓝色")]

PLACED = (None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST)
PAID_BY_GUEST = (STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_GUEST)
CANCELLED_BY_GUEST = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_GUEST)
CANCELLED_BY_SYSTEM = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)

ORDER_FIELDS = {
    "order_number",
    "status",
    "created_at",
    "payment_expires_at",
    "server_time",
    "lines",
    "subtotal_sen",
    "shipping_fee_sen",
    "total_sen",
    "recipient",
    "last_payment",
    "cancelled_by",
}


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


@dataclass(frozen=True)
class _Catalog:
    red_m: int
    blue_m: int
    mug: int


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _variant(
    db: Session,
    product: Product,
    sku: str,
    price_sen: int,
    stock: int,
    value: ProductOptionValue | None = None,
) -> int:
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        price_sen=price_sen,
        daily_initial_stock=stock,
        available_stock=stock,
        is_active=True,
    )
    _add(db, variant)
    if value is not None:
        link = VariantOptionValue(
            variant_id=variant.id,
            product_id=product.id,
            option_id=value.option_id,
            option_value_id=value.id,
        )
        _add(db, link)
    return variant.id


def _catalog(db: Session) -> _Catalog:
    """两件已发布商品与 MY-10 运费，写完即提交。

    Tee：规格名颜色，英文与中文齐全，马来文全缺；TEE-RED-M 2590 仙、库存 5，
    TEE-BLUE-M 2190 仙、库存 5（规格 ID 比红色大）。
    Mug：无规格，三语名称齐全，每单限购 2；MUG 1500 仙、库存 3（规格 ID 最大）。
    """
    category = _add(db, Category(slug="goods", name_en="Goods", is_active=True))
    tee = Product(
        category_id=category.id,
        slug="tee",
        name_en="Tee",
        name_zh="T恤",
        description_en="A tee.",
        is_active=True,
    )
    _add(db, tee)
    color = ProductOption(
        product_id=tee.id,
        code="color",
        sort_order=0,
        name_en="Colour",
        name_zh="颜色",
    )
    _add(db, color)
    values = {}
    for sort_order, (code, name_en, name_zh) in enumerate(TEE_COLOURS):
        value = ProductOptionValue(
            product_id=tee.id,
            option_id=color.id,
            code=code,
            sort_order=sort_order,
            name_en=name_en,
            name_zh=name_zh,
        )
        values[code] = _add(db, value)
    red_m = _variant(db, tee, "TEE-RED-M", 2590, 5, values["red"])
    blue_m = _variant(db, tee, "TEE-BLUE-M", 2190, 5, values["blue"])

    mug = Product(
        category_id=category.id,
        slug="mug",
        name_en="Mug",
        name_zh="马克杯",
        name_ms="Mug Kopi",
        description_en="A mug.",
        is_active=True,
        max_per_order=2,
    )
    _add(db, mug)
    mug_id = _variant(db, mug, "MUG", 1500, 3)

    rate = ShippingRate(zone_type="my_state", zone_code="MY-10", fee_sen=SHIPPING, version=1)
    _add(db, rate)
    db.commit()
    return _Catalog(red_m=red_m, blue_m=blue_m, mug=mug_id)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _place(client: TestClient, lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """经下单接口建一张游客订单；返回下单响应（含订单号与 CSRF 令牌）。"""
    body = {
        "lines": lines or DEFAULT_LINES,
        "phone": "012-345 6789",
        "phone_region": "MY",
        "name": NAME,
        "address": ADDRESS,
        "postal_code": POSTAL,
        "country_code": "MY",
        "state_code": "MY-10",
    }
    response = client.post(ORDER_URL, json=body, headers={"Idempotency-Key": _key()})
    assert response.status_code == 201, response.text
    return response.json()


def _pay(
    client: TestClient,
    placed: dict[str, Any],
    *,
    method: str = "card",
    result: str = "success",
    key: str | None = None,
    csrf: str | None = None,
) -> Any:
    body = {"order_number": placed["order_number"], "method": method, "result": result}
    headers = {"Idempotency-Key": key or _key(), "X-CSRF-Token": csrf or placed["csrf_token"]}
    return client.post(PAY_URL, json=body, headers=headers)


def _cancel(client: TestClient, placed: dict[str, Any], csrf: str | None = None) -> Any:
    body = {"order_number": placed["order_number"]}
    headers = {"X-CSRF-Token": csrf or placed["csrf_token"]}
    return client.post(CANCEL_URL, json=body, headers=headers)


def _order(db: Session, placed: dict[str, Any]) -> Order:
    db.expire_all()
    stmt = select(Order).where(Order.order_number == placed["order_number"])
    return db.scalars(stmt).one()


def _stocks(db: Session, catalog: _Catalog) -> tuple[int, int, int]:
    """TEE-RED-M、TEE-BLUE-M、MUG 的当日可用库存。"""
    db.expire_all()
    stmt = select(ProductVariant.id, ProductVariant.available_stock)
    stock = dict(db.execute(stmt).tuples().all())
    return stock[catalog.red_m], stock[catalog.blue_m], stock[catalog.mug]


def _events(db: Session, order_id: int) -> list[tuple[str | None, str, str]]:
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return list(db.execute(stmt).tuples())


def _attempts(db: Session, order_id: int) -> list[tuple[str, str]]:
    stmt = (
        select(PaymentAttempt.method, PaymentAttempt.result)
        .where(PaymentAttempt.order_id == order_id)
        .order_by(PaymentAttempt.id)
    )
    return list(db.execute(stmt).tuples())


def _expire_order(db: Session, placed: dict[str, Any]) -> None:
    """把订单的支付到期时间改到 1 分钟前并提交。"""
    stmt = (
        update(Order)
        .where(Order.order_number == placed["order_number"])
        .values(payment_expires_at=_now() - timedelta(minutes=1))
    )
    db.execute(stmt)
    db.commit()


def _expire_grants(db: Session) -> None:
    """把所有授权改为 10 分钟前到期并提交（创建时间同时提前，满足到期晚于创建）。"""
    now = _now()
    created = now - timedelta(minutes=40)
    expired = now - timedelta(minutes=10)
    db.execute(update(OrderAccessGrant).values(created_at=created, expires_at=expired))
    db.commit()


def _lookup_cookie(db: Session, order_id: int) -> tuple[str, str]:
    """给一个新浏览器签发该单的查单授权（lookup）并提交；返回 cookie 值与 CSRF 令牌。"""
    issued = issue_order_access(db, None, order_id, SCOPE_LOOKUP, _now())
    db.commit()
    assert issued.new_token is not None
    return issued.new_token, issued.csrf_token


def _with_cookie(token: str | None) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def _assert_access_expired(response: Any) -> None:
    """授权不通过的唯一响应：401、固定错误码、no-store、不设 cookie。"""
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "access_expired"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get_list("set-cookie") == []


def _in_other_session[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """用第二个数据库会话执行一个操作（操作自己提交）。"""
    with Session(engine) as other:
        return action(other)


def _before_first_transition(
    monkeypatch: pytest.MonkeyPatch,
    action: Callable[[], Any],
) -> None:
    """payment 模块第一次调用迁移判定函数（随后就是带条件的 UPDATE）之前插入一段操作。"""
    real = payment.is_transition_allowed
    done: list[bool] = []

    def wrapper(current: str, target: str, actor: str) -> bool:
        if not done:
            done.append(True)
            action()
        return real(current, target, actor)

    monkeypatch.setattr(payment, "is_transition_allowed", wrapper)


def _after_first_key_lookup(
    monkeypatch: pytest.MonkeyPatch,
    action: Callable[[], Any],
) -> None:
    """payment 模块第一次按幂等键查支付尝试之后插入一段操作。"""
    real = payment._attempt_by_key
    done: list[bool] = []

    def wrapper(session: Session, idempotency_key: str) -> PaymentAttempt | None:
        found = real(session, idempotency_key)
        if not done:
            done.append(True)
            action()
        return found

    monkeypatch.setattr(payment, "_attempt_by_key", wrapper)


# ---------------------------------------------------------------------------
# 查看：GET /api/pay/orders
# ---------------------------------------------------------------------------


def test_view_returns_fields_and_recipient(db: Session, client: TestClient) -> None:
    """凭据「用于该单的模拟支付、失败重试、取消与结果页，
    这些页面可显示该单收货资料原文」；「不缓存敏感响应」。
    SHOP-TASK-021 验收：每张订单含订单号、状态、创建时间、支付到期时间、
    服务器当前时间、订单行快照、商品小计、运费、应付、收货资料原文、
    最近一次模拟支付（没有为空）与取消方；另返回由 cookie 重新算出的
    CSRF 令牌；响应带 no-store。
    """
    _catalog(db)
    placed = _place(client)
    before = datetime.now(UTC)

    response = client.get(ORDERS_URL)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"orders", "csrf_token"}
    token = client.cookies.get(COOKIE_NAME)
    assert body["csrf_token"] == csrf_token_for_cookie(token) == placed["csrf_token"]
    (view,) = body["orders"]
    assert set(view) == ORDER_FIELDS
    order = _order(db, placed)
    assert view["order_number"] == placed["order_number"]
    assert view["status"] == STATUS_AWAITING_PAYMENT
    assert datetime.fromisoformat(view["created_at"]) == order.created_at.replace(tzinfo=UTC)
    expires = datetime.fromisoformat(view["payment_expires_at"])
    assert expires == order.payment_expires_at.replace(tzinfo=UTC)
    assert before <= datetime.fromisoformat(view["server_time"]) <= datetime.now(UTC)
    assert view["lines"] == [
        {
            "name": "Tee",
            "variant_label": "Colour: Red",
            "quantity": 2,
            "unit_price_sen": 2590,
            "line_subtotal_sen": 5180,
        },
        {
            "name": "Mug",
            "variant_label": "",
            "quantity": 1,
            "unit_price_sen": 1500,
            "line_subtotal_sen": 1500,
        },
    ]
    assert (view["subtotal_sen"], view["shipping_fee_sen"]) == (SUBTOTAL, SHIPPING)
    assert view["total_sen"] == SUBTOTAL + SHIPPING
    assert view["recipient"] == {
        "name": NAME,
        "phone": PHONE_E164,
        "country_code": "MY",
        "region": "MY-10",
        "address": ADDRESS,
        "postal_code": POSTAL,
    }
    assert view["last_payment"] is None
    assert view["cancelled_by"] is None


@pytest.mark.parametrize(
    ("lang", "tee", "mug"),
    [
        pytest.param("zh", ("T恤", "颜色：红色"), "马克杯", id="zh"),
        pytest.param("ms", ("Tee", "Colour: Red"), "Mug Kopi", id="ms-falls-back"),
    ],
)
def test_view_lines_follow_language(
    db: Session, client: TestClient, lang: str, tee: tuple[str, str], mug: str
) -> None:
    """「文案缺少当前语言时回退英文」（快照在下单时已回退）。
    SHOP-TASK-021 验收：语言参数与目录接口相同，订单行快照按请求语言取。
    """
    _catalog(db)
    _place(client)

    response = client.get(ORDERS_URL, params={"lang": lang})

    assert response.status_code == 200, response.text
    lines = response.json()["orders"][0]["lines"]
    assert (lines[0]["name"], lines[0]["variant_label"]) == tee
    assert lines[1]["name"] == mug


def test_view_rejects_unknown_language_without_echo(db: Session, client: TestClient) -> None:
    """SHOP-TASK-021 验收：语言参数与目录接口相同（只接受 en、zh、ms）；
    422 响应不回显请求内容。
    """
    _catalog(db)
    _place(client)

    response = client.get(ORDERS_URL, params={"lang": "klingon"})

    assert response.status_code == 422, response.text
    assert "klingon" not in response.text


def test_view_orders_by_grant_expiry_latest_first(db: Session, client: TestClient) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」。
    SHOP-TASK-021 验收：按授权到期时间从晚到早（即最近签发的在前）；
    顺序看授权到期时间，不看下单先后。
    """
    _catalog(db)
    first = _place(client, [{"sku": "TEE-RED-M", "quantity": 1}])
    second = _place(client, [{"sku": "TEE-BLUE-M", "quantity": 1}])

    response = client.get(ORDERS_URL)

    numbers = [view["order_number"] for view in response.json()["orders"]]
    assert numbers == [second["order_number"], first["order_number"]]

    later = _now() + timedelta(minutes=40)
    stmt = (
        update(OrderAccessGrant)
        .where(OrderAccessGrant.order_id == _order(db, first).id)
        .values(expires_at=later)
    )
    db.execute(stmt)
    db.commit()

    response = client.get(ORDERS_URL)

    numbers = [view["order_number"] for view in response.json()["orders"]]
    assert numbers == [first["order_number"], second["order_number"]]


def test_view_shows_only_this_browsers_orders(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """凭据「仅限该单」，「不能用于…其他订单」：另一个浏览器只看到它自己的订单。"""
    _catalog(db)
    mine = _place(client)
    other = _browser(app)
    theirs = _place(other, [{"sku": "TEE-BLUE-M", "quantity": 1}])

    response = other.get(ORDERS_URL)

    numbers = [view["order_number"] for view in response.json()["orders"]]
    assert numbers == [theirs["order_number"]]
    assert mine["order_number"] not in response.text


def test_view_shows_last_payment_and_cancelled_by(db: Session, client: TestClient) -> None:
    """「模拟支付失败仍停在此状态并记录失败尝试」；P07 区分「超时或本人取消」。
    SHOP-TASK-021 验收：最近一次模拟支付的结果；已取消时的取消方（本人）。
    """
    _catalog(db)
    placed = _place(client)
    assert _pay(client, placed, method="bank", result="failure").status_code == 201

    failed = client.get(ORDERS_URL).json()["orders"][0]
    assert _cancel(client, placed).status_code == 200
    cancelled = client.get(ORDERS_URL).json()["orders"][0]

    last = failed["last_payment"]
    assert failed["status"] == STATUS_AWAITING_PAYMENT
    assert (last["method"], last["result"]) == ("bank", "failure")
    assert failed["cancelled_by"] is None
    assert cancelled["status"] == STATUS_CANCELLED
    assert cancelled["cancelled_by"] == "self"


def test_view_cancels_overdue_orders_first(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消并释放」。
    SHOP-TASK-021 验收：返回前先对这些订单执行超时取消——状态为已取消、
    取消方为超时、库存加回、事件操作者为 system。
    """
    catalog = _catalog(db)
    placed = _place(client)
    _expire_order(db, placed)

    response = client.get(ORDERS_URL)

    view = response.json()["orders"][0]
    assert (view["status"], view["cancelled_by"]) == (STATUS_CANCELLED, "timeout")
    assert _stocks(db, catalog) == INITIAL_STOCK
    assert _events(db, _order(db, placed).id) == [PLACED, CANCELLED_BY_SYSTEM]


@pytest.mark.parametrize("case", ["no-cookie", "malformed", "expired-grant", "lookup-only"])
def test_view_without_valid_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """凭据「30 分钟有效」；查单授权「不能支付或取消」，「过期须重新查单」。
    SHOP-TASK-021 验收：没有有效 guest_checkout 授权时 401，lookup 授权
    不能用，各种情形的响应相同。
    """
    _catalog(db)
    placed = _place(client)
    headers: dict[str, str] = {}
    if case == "malformed":
        headers = _with_cookie("not-a-token")
    elif case == "expired-grant":
        _expire_grants(db)
        headers = _with_cookie(client.cookies.get(COOKIE_NAME))
    elif case == "lookup-only":
        token, _ = _lookup_cookie(db, _order(db, placed).id)
        headers = _with_cookie(token)

    response = _browser(app).get(ORDERS_URL, headers=headers)

    _assert_access_expired(response)


# ---------------------------------------------------------------------------
# 模拟支付：POST /api/pay/attempts
# ---------------------------------------------------------------------------


def test_success_marks_paid_and_consumes_reservation(db: Session, client: TestClient) -> None:
    """「成功进入 demo_paid」；第 6 条「支付成功消耗预留」（库存不再变动）；
    「事务内写订单、流水和事件」。SHOP-TASK-021 验收：成功后状态、支付时间、
    事件（操作者 guest）与支付尝试各一条，库存不变。
    """
    catalog = _catalog(db)
    placed = _place(client)

    response = _pay(client, placed, method="card", result="success")

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"method", "result", "status", "paid_at"}
    assert (body["method"], body["result"], body["status"]) == ("card", "success", STATUS_PAID)
    order = _order(db, placed)
    assert order.status == STATUS_PAID
    assert order.paid_at is not None
    assert datetime.fromisoformat(body["paid_at"]) == order.paid_at.replace(tzinfo=UTC)
    assert _events(db, order.id) == [PLACED, PAID_BY_GUEST]
    assert _attempts(db, order.id) == [(PAYMENT_METHOD_CARD, PAYMENT_SUCCEEDED)]
    assert _stocks(db, catalog) == RESERVED_STOCK


@pytest.mark.parametrize(
    ("method", "stored"),
    [
        ("card", PAYMENT_METHOD_CARD),
        ("bank", PAYMENT_METHOD_BANK),
        ("ewallet", PAYMENT_METHOD_EWALLET),
    ],
)
def test_payment_methods(db: Session, client: TestClient, method: str, stored: str) -> None:
    """「PaymentAttempt…模拟支付选择与结果」。SHOP-TASK-021 验收：支付方式
    card、bank、ewallet 三种（对应 pay.method_card、pay.method_bank、
    pay.method_ewallet）。
    """
    _catalog(db)
    placed = _place(client)

    response = _pay(client, placed, method=method, result="failure")

    assert response.status_code == 201, response.text
    assert _attempts(db, _order(db, placed).id) == [(stored, PAYMENT_FAILED)]


def test_failure_keeps_order_and_can_retry(db: Session, client: TestClient) -> None:
    """「模拟支付失败仍停在此状态并记录失败尝试」；第 6 条「失败重试期间保持
    预留」。SHOP-TASK-021 验收：失败只多一条支付尝试，状态、库存与事件都不变，
    可再试后成功。
    """
    catalog = _catalog(db)
    placed = _place(client)

    failed = _pay(client, placed, result="failure")

    assert failed.status_code == 201, failed.text
    assert failed.json() == {
        "method": "card",
        "result": "failure",
        "status": STATUS_AWAITING_PAYMENT,
        "paid_at": None,
    }
    order = _order(db, placed)
    assert (order.status, order.paid_at) == (STATUS_AWAITING_PAYMENT, None)
    assert _events(db, order.id) == [PLACED]
    assert _attempts(db, order.id) == [(PAYMENT_METHOD_CARD, PAYMENT_FAILED)]
    assert _stocks(db, catalog) == RESERVED_STOCK

    retried = _pay(client, placed, method="ewallet", result="success")

    assert retried.status_code == 201, retried.text
    assert _order(db, placed).status == STATUS_PAID
    assert _attempts(db, order.id) == [
        (PAYMENT_METHOD_CARD, PAYMENT_FAILED),
        (PAYMENT_METHOD_EWALLET, PAYMENT_SUCCEEDED),
    ]
    assert _events(db, order.id) == [PLACED, PAID_BY_GUEST]
    assert _stocks(db, catalog) == RESERVED_STOCK


@pytest.mark.parametrize("result", ["success", "failure"])
def test_same_key_same_request_replays(db: Session, client: TestClient, result: str) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-021 验收：同键同请求重放 200，
    不重复写支付尝试与事件，不再改订单。
    """
    catalog = _catalog(db)
    placed = _place(client)
    key = _key()
    first = _pay(client, placed, result=result, key=key)
    assert first.status_code == 201, first.text

    again = _pay(client, placed, result=result, key=key)

    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    order = _order(db, placed)
    assert len(_attempts(db, order.id)) == 1
    assert len(_events(db, order.id)) == (2 if result == "success" else 1)
    assert _stocks(db, catalog) == RESERVED_STOCK


@pytest.mark.parametrize(
    "change",
    [{"result": "success"}, {"method": "bank"}],
    ids=["other-result", "other-method"],
)
def test_same_key_different_request_conflicts(
    db: Session, client: TestClient, change: dict[str, str]
) -> None:
    """「同键不同内容报冲突」。SHOP-TASK-021 验收：409 idempotency_conflict，
    不再改订单。
    """
    _catalog(db)
    placed = _place(client)
    key = _key()
    assert _pay(client, placed, method="card", result="failure", key=key).status_code == 201
    request = {"method": "card", "result": "failure"} | change

    response = _pay(client, placed, **request, key=key)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "idempotency_conflict"}
    order = _order(db, placed)
    assert order.status == STATUS_AWAITING_PAYMENT
    assert _attempts(db, order.id) == [(PAYMENT_METHOD_CARD, PAYMENT_FAILED)]


def test_same_key_on_another_order_conflicts(db: Session, client: TestClient) -> None:
    """「同键不同内容报冲突」：指纹含订单 ID，同一浏览器的另一张订单
    用同一个键也是冲突。
    """
    _catalog(db)
    first = _place(client, [{"sku": "TEE-RED-M", "quantity": 1}])
    second = _place(client, [{"sku": "TEE-BLUE-M", "quantity": 1}])
    key = _key()
    assert _pay(client, first, result="failure", key=key).status_code == 201

    response = _pay(client, second, result="failure", key=key)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "idempotency_conflict"}
    assert _attempts(db, _order(db, second).id) == []


@pytest.mark.parametrize("case", ["missing", "wrong", "other-browser"])
def test_payment_without_valid_csrf_is_403(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """「支付、取消…等写操作另须 CSRF 令牌」。SHOP-TASK-021 验收：缺或错
    CSRF 403 csrf_failed，不写支付尝试、不改订单。
    """
    _catalog(db)
    placed = _place(client)
    headers = {"Idempotency-Key": _key()}
    if case == "wrong":
        headers["X-CSRF-Token"] = "0" * 64
    elif case == "other-browser":
        headers["X-CSRF-Token"] = _place(_browser(app))["csrf_token"]
    body = {"order_number": placed["order_number"], "method": "card", "result": "success"}

    response = client.post(PAY_URL, json=body, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    order = _order(db, placed)
    assert order.status == STATUS_AWAITING_PAYMENT
    assert _attempts(db, order.id) == []


def test_paid_order_cannot_be_paid_or_cancelled(db: Session, client: TestClient) -> None:
    """「已模拟支付订单不走取消，走退款流程」；不出现一单两次成功。
    SHOP-TASK-021 验收：已支付后再付 409 order_not_payable、取消 409
    order_not_cancellable，都不写入。
    """
    catalog = _catalog(db)
    placed = _place(client)
    assert _pay(client, placed).status_code == 201

    again = _pay(client, placed, method="bank")
    cancel = _cancel(client, placed)

    assert again.status_code == 409, again.text
    assert again.json() == {"detail": "order_not_payable", "status": STATUS_PAID}
    assert cancel.status_code == 409, cancel.text
    assert cancel.json() == {"detail": "order_not_cancellable", "status": STATUS_PAID}
    order = _order(db, placed)
    assert len(_attempts(db, order.id)) == 1
    assert _events(db, order.id) == [PLACED, PAID_BY_GUEST]
    assert _stocks(db, catalog) == RESERVED_STOCK


def test_cancelled_order_cannot_be_paid(db: Session, client: TestClient) -> None:
    """「所有状态迁移校验当前状态」：已取消的订单不能再支付。
    SHOP-TASK-021 验收：已取消时返回 409 order_not_payable 且不写支付尝试。
    """
    _catalog(db)
    placed = _place(client)
    assert _cancel(client, placed).status_code == 200

    response = _pay(client, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_payable", "status": STATUS_CANCELLED}
    assert _attempts(db, _order(db, placed).id) == []


def test_overdue_payment_returns_order_expired(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消并释放」。SHOP-TASK-021 验收：已过支付
    到期时间时先按超时取消处理，再返回 409 order_expired，不写支付尝试；
    订单变为已取消、库存加回、事件操作者为 system。
    """
    catalog = _catalog(db)
    placed = _place(client)
    _expire_order(db, placed)

    response = _pay(client, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_expired", "status": STATUS_CANCELLED}
    order = _order(db, placed)
    assert (order.status, order.paid_at) == (STATUS_CANCELLED, None)
    assert _attempts(db, order.id) == []
    assert _events(db, order.id) == [PLACED, CANCELLED_BY_SYSTEM]
    assert _stocks(db, catalog) == INITIAL_STOCK


def test_payment_at_expiry_moment_is_expired(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消」。SHOP-TASK-021 验收：当前时间早于
    支付到期时间才可支付；恰在到期时刻提交即按超时处理（order_expired）。
    """
    _catalog(db)
    placed = _place(client)
    order = _order(db, placed)
    due = order.payment_expires_at

    with pytest.raises(payment.OrderExpired) as raised:
        payment.submit_payment(db, order.id, "card", "success", _key(), due)

    assert raised.value.status == STATUS_CANCELLED
    assert _events(db, order.id) == [PLACED, CANCELLED_BY_SYSTEM]
    assert _attempts(db, order.id) == []


# ---------------------------------------------------------------------------
# 取消：POST /api/pay/cancel
# ---------------------------------------------------------------------------


def test_cancel_releases_stock_once(db: Session, client: TestClient) -> None:
    """「待支付订单可由下单者在支付页取消（游客凭该单短期凭据）…释放库存」。
    SHOP-TASK-021 验收：取消后库存加回、事件操作者为 guest；重复取消直接
    返回当前状态，不再写事件或加回库存。
    """
    catalog = _catalog(db)
    placed = _place(client)

    first = _cancel(client, placed)
    again = _cancel(client, placed)

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {"status": STATUS_CANCELLED, "cancelled_by": "self"}
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    order = _order(db, placed)
    assert order.status == STATUS_CANCELLED
    assert _events(db, order.id) == [PLACED, CANCELLED_BY_GUEST]
    assert _stocks(db, catalog) == INITIAL_STOCK


def test_cancel_of_overdue_order_is_timeout(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消并释放」。SHOP-TASK-021 实现约定：
    已过支付到期时间的订单先按超时取消，取消接口返回已取消、取消方为超时，
    库存只加回一次。
    """
    catalog = _catalog(db)
    placed = _place(client)
    _expire_order(db, placed)

    response = _cancel(client, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_CANCELLED, "cancelled_by": "timeout"}
    assert _events(db, _order(db, placed).id) == [PLACED, CANCELLED_BY_SYSTEM]
    assert _stocks(db, catalog) == INITIAL_STOCK


@pytest.mark.parametrize("case", ["missing", "wrong"])
def test_cancel_without_valid_csrf_is_403(db: Session, client: TestClient, case: str) -> None:
    """「支付、取消…等写操作另须 CSRF 令牌」。SHOP-TASK-021 验收：缺或错
    CSRF 403 csrf_failed，不改订单、不动库存。
    """
    catalog = _catalog(db)
    placed = _place(client)
    headers = {} if case == "missing" else {"X-CSRF-Token": "f" * 64}
    body = {"order_number": placed["order_number"]}

    response = client.post(CANCEL_URL, json=body, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT
    assert _stocks(db, catalog) == RESERVED_STOCK


# ---------------------------------------------------------------------------
# 访问授权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["pay", "cancel"])
@pytest.mark.parametrize(
    "case", ["no-cookie", "expired-grant", "lookup-grant", "other-browser", "unknown-order"]
)
def test_write_without_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, operation: str, case: str
) -> None:
    """凭据「仅限该单」「30 分钟有效」；查单授权「不能支付或取消」；
    「服务端校验所属订单、到期时间」。SHOP-TASK-021 验收：无 cookie、授权
    过期、lookup 授权、其他浏览器的 cookie、订单号不存在都是同一个 401
    access_expired，不改订单；401 先于 CSRF 校验。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order = _order(db, placed)
    browser = _browser(app)
    target = dict(placed)
    csrf = placed["csrf_token"]
    if case == "expired-grant":
        _expire_grants(db)
        browser = client
    elif case == "lookup-grant":
        token, csrf = _lookup_cookie(db, order.id)
        browser.headers.update(_with_cookie(token))
    elif case == "other-browser":
        csrf = _place(browser, [{"sku": "TEE-BLUE-M", "quantity": 1}])["csrf_token"]
    elif case == "unknown-order":
        browser = client
        target["order_number"] = "0" * 16

    if operation == "pay":
        response = _pay(browser, target, csrf=csrf)
    else:
        response = _cancel(browser, target, csrf=csrf)

    _assert_access_expired(response)
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT
    assert _events(db, order.id) == [PLACED]
    assert _attempts(db, order.id) == []
    assert _stocks(db, catalog)[0] == RESERVED_STOCK[0]


@pytest.mark.parametrize("operation", ["pay", "cancel"])
def test_grant_for_order_a_cannot_touch_order_b(
    app: FastAPI, db: Session, client: TestClient, operation: str
) -> None:
    """凭据「不能用于确认收货、退款或其他订单」。SHOP-TASK-021 验收：同一会话
    已授权订单 A、未授权订单 B 时，借请求体里 B 的订单号支付或取消，都返回
    与无 cookie 相同的 401，B 的订单、库存、事件与支付尝试都不变。
    """
    catalog = _catalog(db)
    order_a = _place(client, [{"sku": "TEE-RED-M", "quantity": 1}])
    order_b = _place(_browser(app), [{"sku": "TEE-BLUE-M", "quantity": 2}])
    stocks = _stocks(db, catalog)
    target = {"order_number": order_b["order_number"], "csrf_token": order_a["csrf_token"]}

    if operation == "pay":
        response = _pay(client, target)
        baseline = _pay(_browser(app), target)
    else:
        response = _cancel(client, target)
        baseline = _cancel(_browser(app), target)

    _assert_access_expired(response)
    _assert_access_expired(baseline)
    assert response.content == baseline.content
    b = _order(db, order_b)
    assert (b.status, b.paid_at) == (STATUS_AWAITING_PAYMENT, None)
    assert _events(db, b.id) == [PLACED]
    assert _attempts(db, b.id) == []
    assert _stocks(db, catalog) == stocks


# ---------------------------------------------------------------------------
# 加回库存的顺序
# ---------------------------------------------------------------------------


def _extra_item(
    db: Session, order_id: int, line_index: int, variant_id: int | None, quantity: int
) -> None:
    """直接写一行订单行并提交（下单接口不会产生同一规格的两行或规格为空的行）。"""
    item = OrderItem(
        order_id=order_id,
        line_index=line_index,
        variant_id=variant_id,
        sku=f"EXTRA-{line_index}",
        product_name_en="Extra",
        product_name_zh="Extra",
        product_name_ms="Extra",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=100,
        quantity=quantity,
        line_subtotal_sen=100 * quantity,
    )
    db.add(item)
    db.commit()


@pytest.mark.parametrize("path", ["cancel", "timeout"])
def test_release_updates_follow_variant_id_order(
    engine: Engine, db: Session, client: TestClient, path: str
) -> None:
    """第 6 条释放预留。SHOP-TASK-021 验收：按订单行加回当日可用库存，
    规格已删除的行跳过，同一规格的多行先合并件数，再按 SKU 规格 ID 从小到大
    逐个更新（与下单扣库存的顺序一致）；订单行的规格 ID 顺序与行序相反时，
    用 SQL 执行事件钩子断言取消与超时取消发出 UPDATE 的顺序。
    """
    catalog = _catalog(db)
    lines = [
        {"sku": "MUG", "quantity": 1},
        {"sku": "TEE-BLUE-M", "quantity": 1},
        {"sku": "TEE-RED-M", "quantity": 2},
    ]
    placed = _place(client, lines)
    order_id = _order(db, placed).id
    # 第 3 行与第 2 行同一规格（红色）；第 4 行的规格已删除（规格 ID 为空）。
    _extra_item(db, order_id, 3, catalog.red_m, 1)
    _extra_item(db, order_id, 4, None, 7)
    assert _stocks(db, catalog) == (3, 4, 2)
    updates: list[tuple[Any, ...]] = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany) -> None:
        if statement.startswith("UPDATE product_variants"):
            updates.append(tuple(parameters))

    event.listen(engine, "before_cursor_execute", capture)
    if path == "cancel":
        assert _cancel(client, placed).status_code == 200
    else:
        _expire_order(db, placed)
        assert payment.expire_overdue_orders(db, _now()) == 1
    event.remove(engine, "before_cursor_execute", capture)

    assert [params[-1] for params in updates] == [catalog.red_m, catalog.blue_m, catalog.mug]
    assert [params[0] for params in updates] == [3, 1, 1]
    assert _stocks(db, catalog) == (6, 5, 3)


# ---------------------------------------------------------------------------
# 超时取消函数
# ---------------------------------------------------------------------------


def test_expire_runs_once_and_spares_unexpired(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消并释放」；「可重复运行…只生效一次」。
    SHOP-TASK-021 验收：重复运行只生效一次，返回取消的张数；未到期的订单
    不受影响；事件操作者为 system。
    """
    catalog = _catalog(db)
    overdue = _place(client, [{"sku": "TEE-RED-M", "quantity": 2}])
    fresh = _place(client, [{"sku": "TEE-BLUE-M", "quantity": 1}])
    _expire_order(db, overdue)

    first = payment.expire_overdue_orders(db, _now())
    second = payment.expire_overdue_orders(db, _now())

    assert (first, second) == (1, 0)
    assert _order(db, overdue).status == STATUS_CANCELLED
    assert _order(db, fresh).status == STATUS_AWAITING_PAYMENT
    assert _events(db, _order(db, overdue).id) == [PLACED, CANCELLED_BY_SYSTEM]
    assert _events(db, _order(db, fresh).id) == [PLACED]
    assert _stocks(db, catalog) == (5, 4, 3)


def test_expire_boundary_is_not_later_than_now(db: Session, client: TestClient) -> None:
    """第 6 条「15 分钟未支付自动取消」。SHOP-TASK-021 验收：支付到期时间
    不晚于当前时间的订单才取消——到期前一刻不取消，恰在到期时刻取消。
    """
    _catalog(db)
    placed = _place(client)
    due = _order(db, placed).payment_expires_at

    assert payment.expire_overdue_orders(db, due - timedelta(microseconds=1)) == 0
    assert payment.expire_overdue_orders(db, due) == 1
    assert _order(db, placed).status == STATUS_CANCELLED


def test_expire_respects_order_ids_and_limit(db: Session, client: TestClient) -> None:
    """SHOP-TASK-021 验收：超时取消函数可选限定订单 ID 集合与单次上限。"""
    _catalog(db)
    placed = [_place(client, [{"sku": "TEE-RED-M", "quantity": 1}]) for _ in range(3)]
    for order in placed:
        _expire_order(db, order)
    ids = [_order(db, order).id for order in placed]

    assert payment.expire_overdue_orders(db, _now(), order_ids=[]) == 0
    assert payment.expire_overdue_orders(db, _now(), order_ids=[ids[2]]) == 1
    assert payment.expire_overdue_orders(db, _now(), limit=1) == 1
    assert payment.expire_overdue_orders(db, _now(), limit=1) == 1
    assert payment.expire_overdue_orders(db, _now()) == 0
    assert [_order(db, order).status for order in placed] == [STATUS_CANCELLED] * 3


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------


def test_cancel_committed_before_success_update(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「所有状态迁移校验当前状态」。SHOP-TASK-021 验收：成功请求读过订单之后、
    带条件的 UPDATE 之前，另一会话先提交了取消：UPDATE 未命中，回滚并返回
    409 与当前状态；库存恰好加回一次，没有支付尝试，事件只有一条取消。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id

    def cancel(other: Session) -> None:
        payment.cancel_order(other, order_id, _now())

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, cancel))

    response = _pay(client, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_payable", "status": STATUS_CANCELLED}
    assert _order(db, placed).status == STATUS_CANCELLED
    assert _stocks(db, catalog) == INITIAL_STOCK
    assert _attempts(db, order_id) == []
    assert _events(db, order_id) == [PLACED, CANCELLED_BY_GUEST]


def test_two_successes_with_different_keys(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「所有状态迁移校验当前状态」。SHOP-TASK-021 验收：两笔不同幂等键的成功
    并发，后到的条件 UPDATE 未命中，回滚并返回 409 与当前状态，不出现一单
    两次成功；支付尝试与成功事件各一条，库存不变。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    other_key = _key()

    def succeed(other: Session) -> None:
        payment.submit_payment(other, order_id, "bank", "success", other_key, _now())

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, succeed))

    response = _pay(client, placed, method="card")

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_payable", "status": STATUS_PAID}
    assert _attempts(db, order_id) == [(PAYMENT_METHOD_BANK, PAYMENT_SUCCEEDED)]
    assert _events(db, order_id) == [PLACED, PAID_BY_GUEST]
    assert _stocks(db, catalog) == RESERVED_STOCK


@pytest.mark.parametrize("first", ["self-cancel", "timeout"])
def test_timeout_and_self_cancel(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    """「待支付订单可由下单者在支付页取消…或超时为 demo_cancelled」。
    SHOP-TASK-021 验收：超时取消与本人取消并发时只有先提交的生效，库存恰好
    加回一次，只有一条取消事件。first 是先读订单、在条件 UPDATE 前被另一
    会话抢先提交的一方。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    due = _order(db, placed).payment_expires_at
    before_due = due - timedelta(seconds=1)

    def self_cancel(session: Session) -> payment.CancelOutcome:
        return payment.cancel_order(session, order_id, before_due)

    def timeout(session: Session) -> int:
        return payment.expire_overdue_orders(session, due)

    if first == "self-cancel":
        _before_first_transition(monkeypatch, lambda: _in_other_session(engine, timeout))
        outcome = _in_other_session(engine, self_cancel)
        assert outcome.model_dump() == {"status": STATUS_CANCELLED, "cancelled_by": "timeout"}
        expected = CANCELLED_BY_SYSTEM
    else:
        _before_first_transition(monkeypatch, lambda: _in_other_session(engine, self_cancel))
        assert _in_other_session(engine, timeout) == 0
        expected = CANCELLED_BY_GUEST

    assert _order(db, placed).status == STATUS_CANCELLED
    assert _stocks(db, catalog) == INITIAL_STOCK
    assert _events(db, order_id) == [PLACED, expected]
    assert _attempts(db, order_id) == []


@pytest.mark.parametrize("first", ["success", "timeout"])
def test_timeout_and_success_at_expiry(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    """第 6 条「支付成功消耗预留…15 分钟未支付自动取消并释放」。
    SHOP-TASK-021 验收：超时取消与成功在到期时刻并发时只有先提交的生效：
    取消先提交则库存加回一次、没有支付尝试；成功先提交则库存不加回、
    支付尝试与成功事件各一条。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    due = _order(db, placed).payment_expires_at
    just_before = due - timedelta(microseconds=1)
    key = _key()

    def succeed(session: Session) -> Any:
        return payment.submit_payment(session, order_id, "card", "success", key, just_before)

    def timeout(session: Session) -> int:
        return payment.expire_overdue_orders(session, due)

    if first == "success":
        _before_first_transition(monkeypatch, lambda: _in_other_session(engine, timeout))
        with pytest.raises(payment.OrderNotPayable) as raised:
            _in_other_session(engine, succeed)
        assert raised.value.status == STATUS_CANCELLED
        assert _order(db, placed).status == STATUS_CANCELLED
        assert _stocks(db, catalog) == INITIAL_STOCK
        assert _attempts(db, order_id) == []
        assert _events(db, order_id) == [PLACED, CANCELLED_BY_SYSTEM]
    else:
        _before_first_transition(monkeypatch, lambda: _in_other_session(engine, succeed))
        assert _in_other_session(engine, timeout) == 0
        assert _order(db, placed).status == STATUS_PAID
        assert _stocks(db, catalog) == RESERVED_STOCK
        assert _attempts(db, order_id) == [(PAYMENT_METHOD_CARD, PAYMENT_SUCCEEDED)]
        assert _events(db, order_id) == [PLACED, PAID_BY_GUEST]


def test_two_timeout_runs_at_once(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「可重复运行，使用操作标识保证只生效一次」。SHOP-TASK-021 验收：两次
    超时取消同时运行时同一订单只生效一次，不重复加回库存，只有一条 system 事件。
    """
    catalog = _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    due = _order(db, placed).payment_expires_at
    counts: list[int] = []

    def timeout(session: Session) -> None:
        counts.append(payment.expire_overdue_orders(session, due))

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, timeout))
    _in_other_session(engine, timeout)

    # 先完成的是插进来的那一次。
    assert counts == [1, 0]
    assert _order(db, placed).status == STATUS_CANCELLED
    assert _stocks(db, catalog) == INITIAL_STOCK
    assert _events(db, order_id) == [PLACED, CANCELLED_BY_SYSTEM]


@pytest.mark.parametrize("same_request", [True, False], ids=["replay", "conflict"])
def test_same_key_insert_race_on_failure(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    same_request: bool,
) -> None:
    """「模拟支付…均使用幂等键和数据库唯一约束。相同键相同请求返回原结果；
    同键不同内容报冲突」。SHOP-TASK-021 验收：查过幂等键之后，另一会话先提交
    了同键的支付尝试：写入撞上唯一约束，回滚后按指纹重放（200）或返回
    idempotency_conflict，只有一条支付尝试。
    """
    _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    key = _key()
    method = "card" if same_request else "bank"

    def fail(other: Session) -> None:
        payment.submit_payment(other, order_id, method, "failure", key, _now())

    _after_first_key_lookup(monkeypatch, lambda: _in_other_session(engine, fail))

    response = _pay(client, placed, method="card", result="failure", key=key)

    if same_request:
        assert response.status_code == 200, response.text
        assert response.json()["result"] == "failure"
    else:
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "idempotency_conflict"}
    stored = PAYMENT_METHOD_CARD if same_request else PAYMENT_METHOD_BANK
    assert _attempts(db, order_id) == [(stored, PAYMENT_FAILED)]
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT
    assert _events(db, order_id) == [PLACED]


def test_same_key_success_race_replays(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-021 验收：同键的成功请求先提交，
    后到的条件 UPDATE 未命中，回滚后按幂等键查到原结果并重放，
    不出现一单两次成功。
    """
    _catalog(db)
    placed = _place(client)
    order_id = _order(db, placed).id
    key = _key()

    def succeed(other: Session) -> None:
        payment.submit_payment(other, order_id, "card", "success", key, _now())

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, succeed))

    response = _pay(client, placed, method="card", result="success", key=key)

    assert response.status_code == 200, response.text
    assert (response.json()["result"], response.json()["status"]) == ("success", STATUS_PAID)
    assert _attempts(db, order_id) == [(PAYMENT_METHOD_CARD, PAYMENT_SUCCEEDED)]
    assert _events(db, order_id) == [PLACED, PAID_BY_GUEST]


# ---------------------------------------------------------------------------
# 请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_non_json_is_415(
    db: Session, client: TestClient, url: str, content_type: str | None
) -> None:
    """SHOP-TASK-021 验收：只接受 JSON，规则同 SHOP-TASK-020（其他 Content-Type 415）。"""
    _catalog(db)
    placed = _place(client)
    headers = {"Idempotency-Key": _key(), "X-CSRF-Token": placed["csrf_token"]}
    if content_type is not None:
        headers["Content-Type"] = content_type
    body = {"order_number": placed["order_number"], "method": "card", "result": "success"}
    if url == CANCEL_URL:
        body = {"order_number": placed["order_number"]}

    response = client.post(url, content=json.dumps(body), headers=headers)

    assert response.status_code == 415, response.text
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json", "Idempotency-Key": "k" * 20}, id="json"),
        pytest.param({"Content-Type": "application/json"}, id="no-key"),
        pytest.param({"Content-Type": "text/plain"}, id="not-json"),
    ],
)
def test_oversized_body_is_413_before_any_check(
    app: FastAPI, db: Session, client: TestClient, url: str, headers: dict[str, str]
) -> None:
    """SHOP-TASK-021 验收：请求体上限 8 KB，超过先于其他校验 413——带多余
    字段、缺幂等键、不是 JSON 或没有 cookie 的超限请求体都是 413，
    不是 422、415 或 401；响应不回显请求内容。
    """
    _catalog(db)
    placed = _place(client)
    padding = "4111111111111111" * (MAX_BODY_BYTES // 16)
    body = {"order_number": placed["order_number"], "card_number": padding}

    response = _browser(app).post(url, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert "4111111111111111" not in response.text
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT


KEY_LOC = ["header", "Idempotency-Key"]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"key": None}, KEY_LOC, id="missing-key"),
        pytest.param({"key": "a" * 15}, KEY_LOC, id="short-key"),
        pytest.param({"key": "a" * 65}, KEY_LOC, id="long-key"),
        pytest.param({"key": "a" * 20 + "!"}, KEY_LOC, id="bad-char-key"),
        pytest.param({"body": {"method": "visa"}}, ["body", "method"], id="bad-method"),
        pytest.param({"body": {"result": "pending"}}, ["body", "result"], id="bad-result"),
        pytest.param({"drop": "method"}, ["body", "method"], id="no-method"),
        pytest.param({"drop": "order_number"}, ["body", "order_number"], id="no-order-number"),
        pytest.param(
            {"body": {"card_number": "4111111111111111"}},
            ["body", "card_number"],
            id="card-number",
        ),
        pytest.param({"body": {"cvv": "987"}}, ["body", "cvv"], id="cvv"),
        pytest.param(
            {"body": {"account_number": "5550001234"}},
            ["body", "account_number"],
            id="account-number",
        ),
        pytest.param({"body": {"order_number": 12345}}, ["body", "order_number"], id="number"),
    ],
)
def test_invalid_payment_request_is_422_without_echo(
    app: FastAPI, db: Session, client: TestClient, change: dict[str, Any], loc: list[Any]
) -> None:
    """「无真实收款…或银行卡输入」；凭据不写入「错误回显」。
    SHOP-TASK-021 验收：缺或非法 Idempotency-Key、非法支付方式或结果、
    请求体带卡号等多余字段都 422，响应只指出出错的字段，不回显请求内容；
    请求体校验先于授权（没有 cookie 也是 422）。
    """
    _catalog(db)
    placed = _place(client)
    body: dict[str, Any] = {
        "order_number": placed["order_number"],
        "method": "card",
        "result": "success",
    }
    body |= change.get("body", {})
    if "drop" in change:
        del body[change["drop"]]
    headers = {"Content-Type": "application/json"}
    key = change.get("key", _key())
    if key is not None:
        headers["Idempotency-Key"] = key

    response = _browser(app).post(PAY_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in ["4111111111111111", "987", "5550001234", "visa", "pending", "12345"]:
        assert value not in response.text
    assert placed["order_number"] not in response.text
    assert _attempts(db, _order(db, placed).id) == []


@pytest.mark.parametrize(
    ("extra", "loc"),
    [
        pytest.param(None, ["body", "order_number"], id="no-order-number"),
        pytest.param({"reason": "changed my mind"}, ["body", "reason"], id="extra-field"),
    ],
)
def test_invalid_cancel_request_is_422_without_echo(
    db: Session, client: TestClient, extra: dict[str, str] | None, loc: list[Any]
) -> None:
    """SHOP-TASK-021 验收：取消的请求体只有订单号，缺订单号或多出字段都 422，
    不回显请求内容。
    """
    _catalog(db)
    placed = _place(client)
    body: dict[str, Any] = {}
    if extra is not None:
        body = {"order_number": placed["order_number"]} | extra
    headers = {"X-CSRF-Token": placed["csrf_token"]}

    response = client.post(CANCEL_URL, json=body, headers=headers)

    assert response.status_code == 422, response.text
    assert loc in [error["loc"] for error in response.json()["detail"]]
    assert "changed my mind" not in response.text
    assert placed["order_number"] not in response.text
    assert _order(db, placed).status == STATUS_AWAITING_PAYMENT


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」；凭据不写入
    「日志」。SHOP-TASK-021 验收：接口与超时取消函数不写日志。
    """
    _catalog(db)
    placed = _place(client)
    caplog.set_level(logging.DEBUG)

    client.get(ORDERS_URL)
    _pay(client, placed, result="failure")
    _pay(client, placed, result="success")
    _cancel(client, placed)
    payment.expire_overdue_orders(db, _now())

    assert [record for record in caplog.records if record.name.startswith("app")] == []

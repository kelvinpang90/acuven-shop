"""后台「Stripe 待处理」接口（SHOP-TASK-225，app/api/admin_stripe_pending.py）：
GET /api/admin/stripe-pending、POST …/checkouts/{checkout_id}/void、
POST …/checkouts/{checkout_id}/refund-retry。

依据 docs/DESIGN.md 1.22（提交 ffb043e）「Stripe 测试环境支付」第 11 条（下称「第 11 条」）。每条
测试（参数化的测试是每个用例）的文档字符串写明它守住的第 11 条哪一句；只出自 SHOP-TASK-225 验收
标准的约定另注「验收：」。

夹具与后台登录照 tests/test_admin_refund_stripe_api.py（SQLite 内存库、StaticPool、由引擎发
BEGIN，接口的会话依赖换成每个请求一个绑定同一内存库的会话）；订单、会话记录与退款尝试照
tests/test_stripe_void.py 直接写库。Stripe 一律以 app.state.stripe_gateway 注入
FakeStripeGateway（第 9 条），ops_alert.alert 以 monkeypatch 替身记录，接口的当前时间以
monkeypatch 固定 app.api.admin_stripe_pending 的 _utcnow。检查结果一律在另一个数据库会话里读。
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_stripe_pending, pay
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Order,
    OrderEvent,
    OrderItem,
    OrderRecipient,
    Product,
    ProductVariant,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import (
    ACTOR_ADMIN,
    ACTOR_SYSTEM,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import ops_alert, payment
from app.services.admin_auth import issue_admin_session
from app.services.order_rules import generate_order_number
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    Form,
    PaymentIntentInfo,
    Refund,
    StripeOutcome,
    StripeResult,
)

PENDING_URL = "/api/admin/stripe-pending"
STRIPE_REFUNDS_PATH = "/v1/refunds"
SESSIONS_PATH = "/v1/checkout/sessions"

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

API_LOGGER = "app.api.admin_stripe_pending"
SUBMIT_FAILED = "stripe_refund_submit_failed"
VOIDED_ACTION = "stripe_checkout_voided"
RETRIED_ACTION = "stripe_late_refund_retried"
CHECKOUT_TARGET = "stripe_checkout"
ATTEMPT_TARGET = "stripe_refund"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "not_found"}
CHANGED = {"detail": "stripe_pending_changed"}
UNAVAILABLE_BODY = {"detail": "stripe_unavailable"}
NOT_RETRYABLE = {"detail": "stripe_refund_not_retryable"}
CAP = {"detail": "stripe_refund_cap"}
RETRIED = {"result": "retried"}
VERIFIED = {"verified": True}
CHECKOUT_ID_ERROR = {
    "type": "checkout_id_invalid",
    "loc": ["path", "checkout_id"],
    "msg": "Checkout ID should be a positive integer",
}

# 订单行 1 行 2 件、单价 3000 仙，运费 800 仙；库存已为订单预留 2 件。
UNIT_PRICE = 3000
QUANTITY = 2
SUBTOTAL = UNIT_PRICE * QUANTITY
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING
INITIAL_STOCK = 10
RESERVED_STOCK = INITIAL_STOCK - QUANTITY

# 收货资料：断言不出现在任何响应与日志里。
RECIPIENT_NAME = "Siti Recipientname"
RECIPIENT_PHONE = "+60123456789"
RECIPIENT_ADDRESS = "12 Jalan Rahsia Alamat"
RECIPIENT_POSTAL = "50450"

UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


@pytest.fixture(autouse=True)
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """以 monkeypatch 替换 ops_alert.alert（服务层的告警点），不入真实队列。"""
    calls: list[tuple[str, object]] = []

    def record(category: str, target: object = None, **_: object) -> None:
        calls.append((category, target))

    monkeypatch.setattr(ops_alert, "alert", record)
    return calls


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env（照 tests/test_stripe_void.py）。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
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
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture
def admin_id(engine: Engine) -> int:
    with Session(engine) as db:
        row = AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        db.add(row)
        db.flush()
        account_id = row.id
        db.commit()
    return account_id


@pytest.fixture
def token(engine: Engine, admin_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    with Session(engine) as db:
        issued = issue_admin_session(db, admin_id, _now() - timedelta(minutes=1))
        db.commit()
    return issued.token


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """接口的当前时间（作废与审计的时间、新尝试的创建时间）。"""
    fixed = _now()
    monkeypatch.setattr(admin_stripe_pending, "_utcnow", lambda: fixed)
    return fixed


@pytest.fixture
def app(engine: Engine, now: datetime) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.state.stripe_gateway = FakeStripeGateway()
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def variant(engine: Engine) -> int:
    """一个规格：每日初始库存 10，已为一张订单预留 2 件；返回规格 ID。"""
    with Session(engine) as db:
        category = Category(slug="goods", name_en="Goods", is_active=True)
        db.add(category)
        db.flush()
        product = Product(
            category_id=category.id,
            slug="tee",
            name_en="Tee",
            description_en="A tee.",
            is_active=True,
        )
        db.add(product)
        db.flush()
        row = ProductVariant(
            product_id=product.id,
            sku="TEE",
            price_sen=UNIT_PRICE,
            daily_initial_stock=INITIAL_STOCK,
            available_stock=RESERVED_STOCK,
            is_active=True,
        )
        db.add(row)
        db.flush()
        variant_id = row.id
        db.commit()
    return variant_id


def _make_order(engine: Engine, variant_id: int, status: str = STATUS_AWAITING_PAYMENT) -> int:
    """直接写一张游客订单（一行两件、带收货资料）并提交，返回订单 ID。"""
    created = _now() - timedelta(days=2)
    n = next(_SEQUENCE)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=TOTAL,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"order-key-{n}",
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=created if status == STATUS_PAID else None,
            claim_status=CLAIM_OPEN,
        )
        db.add(order)
        db.flush()
        db.add(
            OrderItem(
                order_id=order.id,
                line_index=0,
                variant_id=variant_id,
                sku="TEE",
                product_name_en="Tee",
                product_name_zh="Tee",
                product_name_ms="Tee",
                variant_label_en="",
                variant_label_zh="",
                variant_label_ms="",
                unit_price_sen=UNIT_PRICE,
                quantity=QUANTITY,
                line_subtotal_sen=SUBTOTAL,
            )
        )
        db.add(
            OrderRecipient(
                order_id=order.id,
                name=RECIPIENT_NAME,
                phone=RECIPIENT_PHONE,
                country_code="MY",
                region="MY-10",
                address=RECIPIENT_ADDRESS,
                postal_code=RECIPIENT_POSTAL,
            )
        )
        db.flush()
        order_id = order.id
        db.commit()
    return order_id


def _checkout(
    engine: Engine,
    order_id: int,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
    alerted: bool = True,
    intent_id: str | None = None,
) -> int:
    """直接写一条会话记录（默认已写告警时间），提交后返回记录 ID。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=2)
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
        "expires_at": started + timedelta(minutes=31),
        "created_at": started,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
        "alerted_at": started + timedelta(hours=25) if alerted else None,
        "payment_intent_id": intent_id,
    }
    if status != CHECKOUT_CREATING:
        fields["checkout_session_id"] = f"cs_test_{n}"
        fields["payment_status"] = payment_status
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status in (CHECKOUT_COMPLETE, CHECKOUT_EXPIRED):
        fields["completed_at"] = started + timedelta(minutes=4)
    with Session(engine) as db:
        record = StripeCheckout(**fields)
        db.add(record)
        db.commit()
        return record.id


def _session(engine: Engine, checkout_id: int, status: str, **overrides: Any) -> CheckoutSession:
    """与记录、订单相符的会话对象；已付款时 payment_intent 已展开、方式为 card。"""
    with Session(engine) as db:
        record = db.get(StripeCheckout, checkout_id)
        assert record is not None
        fields: dict[str, Any] = {
            "id": record.checkout_session_id,
            "status": status,
            "payment_status": PAYMENT_UNPAID,
            "livemode": False,
            "currency": "myr",
            "amount_total": TOTAL,
            "client_reference_id": str(record.order_id),
            "order_id": str(record.order_id),
            "create_key": record.idempotency_key,
            "url": None,
            "payment_intent": None,
        }
    if overrides.get("payment_status") == PAYMENT_PAID:
        fields["payment_intent"] = PaymentIntentInfo(
            id=f"pi_test_paid_{checkout_id}",
            status="succeeded",
            livemode=False,
            payment_method_type="card",
        )
    fields.update(overrides)
    return CheckoutSession(**fields)


def _ok(value: Any) -> StripeResult[Any]:
    return StripeResult(StripeOutcome.OK, value)


def _add_attempt(
    engine: Engine,
    checkout_id: int,
    intent_id: str,
    status: str,
    *,
    error_type: str | None = None,
) -> int:
    """直接写一条来源为该会话记录的退款尝试（全额），返回尝试 ID。"""
    created = _now() - timedelta(hours=3)
    active = None if status == STRIPE_REFUND_FAILED else checkout_id
    with Session(engine) as db:
        row = StripeRefund(
            payment_intent_id=intent_id,
            source_type=SOURCE_CHECKOUT,
            checkout_id=checkout_id,
            amount_sen=TOTAL,
            status=status,
            call_count=1,
            last_error_type=error_type,
            created_at=created,
            succeeded_at=created if status == STRIPE_REFUND_SUCCEEDED else None,
            active_checkout_id=active,
            stripe_refund_id=None if status == STRIPE_REFUND_FAILED else f"re_test_{intent_id}",
        )
        db.add(row)
        db.commit()
        return row.id


def _failed_late_refund(engine: Engine, variant_id: int) -> tuple[int, str, int]:
    """已取消订单上迟到付款的结账记录（expired、paid）与一条 failed 的全额退款尝试；
    返回记录 ID、PaymentIntent ID 与尝试 ID。"""
    order_id = _make_order(engine, variant_id, STATUS_CANCELLED)
    intent_id = f"pi_test_late_{next(_SEQUENCE)}"
    checkout_id = _checkout(
        engine, order_id, CHECKOUT_EXPIRED, payment_status=PAYMENT_PAID, intent_id=intent_id
    )
    attempt_id = _add_attempt(
        engine, checkout_id, intent_id, STRIPE_REFUND_FAILED, error_type="invalid_request_error"
    )
    return checkout_id, intent_id, attempt_id


def _inject(app: FastAPI, gateway: FakeStripeGateway) -> FakeStripeGateway:
    app.state.stripe_gateway = gateway
    return gateway


def _next_attempt_id(engine: Engine) -> int:
    """下一条尝试的 ID（SQLite 的整数主键取当前最大值加一）。"""
    with Session(engine) as db:
        return (db.scalar(select(func.max(StripeRefund.id))) or 0) + 1


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _void_url(checkout_id: int | str) -> str:
    return f"{PENDING_URL}/checkouts/{checkout_id}/void"


def _retry_url(checkout_id: int | str) -> str:
    return f"{PENDING_URL}/checkouts/{checkout_id}/refund-retry"


def _void(client: TestClient, token: str, checkout_id: int | str, body: Any = None) -> Any:
    payload = VERIFIED if body is None else body
    return client.post(
        _void_url(checkout_id), json=payload, headers=_headers(token, _csrf_for(token))
    )


def _retry(client: TestClient, token: str, checkout_id: int | str) -> Any:
    return client.post(_retry_url(checkout_id), headers=_headers(token, _csrf_for(token)))


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """审计记录：管理员、操作名、对象类别、对象 ID、旧值、新值，按写入顺序。"""
    with Session(engine) as db:
        rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id))
        return [
            (
                row.admin_account_id,
                row.action,
                row.target_type,
                row.target_id,
                row.old_value,
                row.new_value,
            )
            for row in rows
        ]


def _attempts(engine: Engine) -> list[tuple[Any, ...]]:
    """全部退款尝试，按 ID：（ID、来源记录、金额、状态、活动来源列）。"""
    with Session(engine) as db:
        rows = db.scalars(select(StripeRefund).order_by(StripeRefund.id))
        return [
            (row.id, row.checkout_id, row.amount_sen, row.status, row.active_checkout_id)
            for row in rows
        ]


def _order_state(engine: Engine, checkout_id: int) -> tuple[Any, ...]:
    """记录状态、订单状态、库存与订单事件的操作者。"""
    with Session(engine) as db:
        record = db.get(StripeCheckout, checkout_id)
        assert record is not None
        order = db.get(Order, record.order_id)
        assert order is not None
        stock = db.scalars(select(ProductVariant.available_stock)).one()
        actors = list(
            db.scalars(
                select(OrderEvent.actor_type)
                .where(OrderEvent.order_id == order.id)
                .order_by(OrderEvent.id)
            )
        )
        return record.status, order.status, stock, actors


def _order_number(engine: Engine, checkout_id: int) -> str:
    with Session(engine) as db:
        stmt = (
            select(Order.order_number)
            .join(StripeCheckout, StripeCheckout.order_id == Order.id)
            .where(StripeCheckout.id == checkout_id)
        )
        return db.scalars(stmt).one()


def _snapshot(engine: Engine, checkout_id: int) -> tuple[Any, ...]:
    """记录、订单、尝试与审计：用来断言「不写库」。"""
    return _order_state(engine, checkout_id), _attempts(engine), _audits(engine)


def _api_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == API_LOGGER]


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok_json(response: Any) -> Any:
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _assert_no_private(text_: str) -> None:
    """不含收货资料、Stripe 的任何 ID（会话、PaymentIntent、退款）、付款页地址与创建幂等键。"""
    for secret in (
        RECIPIENT_NAME,
        RECIPIENT_PHONE,
        RECIPIENT_ADDRESS,
        RECIPIENT_POSTAL,
        "cs_test_",
        "pi_test_",
        "re_test_",
        "checkout.stripe",
        "create-key-",
    ):
        assert secret not in text_


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class _Spy:
    """替换服务层函数，记下是否被调用。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("service called")


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Spy]:
    """把接口模块里的三个服务层入口换成替身：校验不通过时不应调用。"""
    found = {name: _Spy() for name in ("stripe_pending", "void", "retry")}
    monkeypatch.setattr(admin_stripe_pending, "stripe_pending", found["stripe_pending"])
    monkeypatch.setattr(admin_stripe_pending, "void_alerted_checkout", found["void"])
    monkeypatch.setattr(admin_stripe_pending, "retry_checkout_attempt", found["retry"])
    return found


def _no_service_calls(spies: dict[str, _Spy]) -> None:
    assert {name: spy.calls for name, spy in spies.items()} == {
        "stripe_pending": 0,
        "void": 0,
        "retry": 0,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def test_routes_are_registered_with_internal_ids_only(app: FastAPI) -> None:
    """第 11 条「由管理员在后台『Stripe 待处理』页处置。只有下面两类」；验收：前缀为
    /api/admin/stripe-pending，只有列表、作废与重试三个接口，路径只用结账记录的内部 ID。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(PENDING_URL)
        for method in operations
    }

    assert routes == {
        ("GET", PENDING_URL),
        ("POST", f"{PENDING_URL}/checkouts/{{checkout_id}}/void"),
        ("POST", f"{PENDING_URL}/checkouts/{{checkout_id}}/refund-retry"),
    }


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_returns_both_lists_and_csrf_token(
    engine: Engine, client: TestClient, token: str, variant: int
) -> None:
    """第 11 条（1）「每行显示结账记录 ID、记录状态、订单号、记录创建时间与告警时间」与（2）
    「每行显示结账记录 ID、最新尝试 ID、订单号、金额、失败时间与错误类别」；验收：200 只有
    checkouts、refunds 与 csrf_token，前两个原样取自 stripe_pending，时间带 Z，令牌由 cookie
    算出。"""
    order_id = _make_order(engine, variant)
    checkout_id = _checkout(engine, order_id, CHECKOUT_CREATING)
    late_id, _, attempt_id = _failed_late_refund(engine, variant)
    order_number = _order_number(engine, checkout_id)
    late_number = _order_number(engine, late_id)
    with Session(engine) as db:
        record = db.get(StripeCheckout, checkout_id)
        attempt = db.get(StripeRefund, attempt_id)
        assert record is not None and attempt is not None
        created_at, alerted_at = record.created_at, record.alerted_at
        attempted_at = attempt.created_at
    assert alerted_at is not None

    body = _ok_json(client.get(PENDING_URL, headers=_headers(token)))

    assert body == {
        "checkouts": [
            {
                "checkout_id": checkout_id,
                "status": CHECKOUT_CREATING,
                "order_number": order_number,
                "created_at": _iso(created_at),
                "alerted_at": _iso(alerted_at),
            }
        ],
        "refunds": [
            {
                "checkout_id": late_id,
                "attempt_id": attempt_id,
                "order_number": late_number,
                "amount_sen": TOTAL,
                "attempted_at": _iso(attempted_at),
                "error_type": "invalid_request_error",
            }
        ],
        "csrf_token": _csrf_for(token),
    }


def test_list_has_no_private_data_and_writes_nothing(
    engine: Engine,
    client: TestClient,
    token: str,
    variant: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 11 条「只显示对账字段……与订单号，不显示收货资料、Stripe 的任何 ID 与 Stripe 返回的
    客户资料」；验收：不要求 CSRF、不写审计、不写日志。"""
    order_id = _make_order(engine, variant)
    _checkout(engine, order_id, CHECKOUT_OPEN, intent_id="pi_test_open")
    _failed_late_refund(engine, variant)
    caplog.set_level(logging.DEBUG)

    response = client.get(PENDING_URL, headers=_headers(token))

    body = _ok_json(response)
    assert len(body["checkouts"]) == 1 and len(body["refunds"]) == 1
    _assert_no_private(response.text)
    assert _audits(engine) == []
    assert _api_logs(caplog) == []


@pytest.mark.parametrize("raw", ["missing", "wrong"])
def test_list_requires_admin_session(client: TestClient, spies: dict[str, _Spy], raw: str) -> None:
    """第 11 条「页面与接口只认管理员会话……所有响应带 no-store」：没有有效后台会话时 401
    admin_session_required，不查询。"""
    headers = {} if raw == "missing" else {"Cookie": f"{COOKIE_NAME}=not-a-session"}

    _assert_error(client.get(PENDING_URL, headers=headers), 401, SESSION_REQUIRED)

    _no_service_calls(spies)


def test_list_without_csrf_token_cookie_is_401(
    client: TestClient,
    token: str,
    spies: dict[str, _Spy],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 11 条「页面与接口只认管理员会话」；验收：csrf_token_for_cookie 算不出令牌时 401
    admin_session_required，不查询。"""
    monkeypatch.setattr(admin_stripe_pending, "csrf_token_for_cookie", lambda _value: None)

    _assert_error(client.get(PENDING_URL, headers=_headers(token)), 401, SESSION_REQUIRED)

    _no_service_calls(spies)


# ---------------------------------------------------------------------------
# 作废：结果与审计
# ---------------------------------------------------------------------------


def test_void_creating_is_voided_with_audit(
    engine: Engine, app: FastAPI, client: TestClient, admin_id: int, token: str, variant: int
) -> None:
    """第 11 条（1）「没有会话 ID（creating）……不调用 Stripe，凭管理员的核实记为 expired 并取消
    订单」与「并写一条审计记录（操作名 stripe_checkout_voided，对象为该结账记录，新值为处理结果
    ……）」：200 {"result": "voided"}，订单以 admin 取消、库存加回，审计一条。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_CREATING)
    gateway = _inject(app, FakeStripeGateway())

    response = _void(client, token, checkout_id)

    assert _ok_json(response) == {"result": "voided"}
    assert gateway.calls == []
    assert _order_state(engine, checkout_id) == (
        CHECKOUT_EXPIRED,
        STATUS_CANCELLED,
        INITIAL_STOCK,
        [ACTOR_ADMIN],
    )
    assert _audits(engine) == [
        (admin_id, VOIDED_ACTION, CHECKOUT_TARGET, checkout_id, None, "voided")
    ]
    _assert_no_private(response.text)


def test_void_audit_is_in_the_void_transaction(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    variant: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 11 条「记为 expired 与取消订单在同一事务里……并写一条审计记录」；验收：voided 的审计经
    on_void 在作废的同一事务里写——写审计抛出时作废随之回滚，记录、订单与库存都不变。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_CREATING)
    before = _snapshot(engine, checkout_id)

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(admin_stripe_pending, "record_audit", broken)

    with pytest.raises(RuntimeError):
        _void(client, token, checkout_id)

    assert _snapshot(engine, checkout_id) == before


def test_void_paid_session_is_confirmed_with_audit(
    engine: Engine, app: FastAPI, client: TestClient, admin_id: int, token: str, variant: int
) -> None:
    """第 11 条（1）「Stripe 给出确定结果时照第 4、5 条处理，不凭管理员的核实：已付款且相符就确认
    ……处理结果（已确认付款、已取消订单）回给管理员」：200 {"result": "confirmed"}，订单为
    demo_paid（操作者 system），审计新值为 confirmed；响应不含 PaymentIntent ID。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_OPEN)
    paid = _session(engine, checkout_id, "complete", payment_status=PAYMENT_PAID)
    _inject(app, FakeStripeGateway(_ok(paid)))

    response = _void(client, token, checkout_id)

    assert _ok_json(response) == {"result": "confirmed"}
    assert _order_state(engine, checkout_id)[1:] == (STATUS_PAID, RESERVED_STOCK, [ACTOR_SYSTEM])
    assert _audits(engine) == [
        (admin_id, VOIDED_ACTION, CHECKOUT_TARGET, checkout_id, None, "confirmed")
    ]
    _assert_no_private(response.text)


def test_void_expired_session_is_cancelled_with_audit(
    engine: Engine, app: FastAPI, client: TestClient, admin_id: int, token: str, variant: int
) -> None:
    """第 11 条（1）「open 就请求作废，作废成功或会话已过期才取消」：查询为 open、作废返回 expired，
    200 {"result": "cancelled"}，订单以 system 取消，审计新值为 cancelled。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_OPEN)
    gateway = _inject(
        app,
        FakeStripeGateway(
            _ok(_session(engine, checkout_id, "open")),
            _ok(_session(engine, checkout_id, "expired")),
        ),
    )

    assert _ok_json(_void(client, token, checkout_id)) == {"result": "cancelled"}

    assert [call.method for call in gateway.calls] == ["GET", "POST"]
    assert all(call.path.startswith(SESSIONS_PATH) for call in gateway.calls)
    assert _order_state(engine, checkout_id) == (
        CHECKOUT_EXPIRED,
        STATUS_CANCELLED,
        INITIAL_STOCK,
        [ACTOR_SYSTEM],
    )
    assert _audits(engine) == [
        (admin_id, VOIDED_ACTION, CHECKOUT_TARGET, checkout_id, None, "cancelled")
    ]


def test_void_cancelled_audit_is_a_separate_transaction(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    variant: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 11 条（1）「Stripe 给出确定结果时照第 4、5 条处理」（取消由现有函数提交）；验收：
    confirmed 与 cancelled 的审计在服务返回之后另开事务写——写审计抛出时取消已提交、不回滚。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_OPEN)
    _inject(app, FakeStripeGateway(_ok(_session(engine, checkout_id, "expired"))))

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(admin_stripe_pending, "record_audit", broken)

    with pytest.raises(RuntimeError):
        _void(client, token, checkout_id)

    assert _order_state(engine, checkout_id)[:2] == (CHECKOUT_EXPIRED, STATUS_CANCELLED)
    assert _audits(engine) == []


@pytest.mark.parametrize(
    ("record_status", "alerted", "order_status"),
    [
        pytest.param(CHECKOUT_OPEN, False, STATUS_AWAITING_PAYMENT, id="not-alerted"),
        pytest.param(CHECKOUT_EXPIRED, True, STATUS_AWAITING_PAYMENT, id="record-expired"),
        pytest.param(CHECKOUT_OPEN, True, STATUS_CANCELLED, id="order-cancelled"),
    ],
)
def test_void_changed_is_409_without_audit(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    variant: int,
    record_status: str,
    alerted: bool,
    order_status: str,
) -> None:
    """第 11 条（1）「服务端先以普通读取重查记录与订单仍满足上述条件（不满足回答已变化，页面
    重新读取）」：409 stripe_pending_changed，不调用 Stripe，不写库、不写审计。"""
    order_id = _make_order(engine, variant, order_status)
    checkout_id = _checkout(engine, order_id, record_status, alerted=alerted)
    before = _snapshot(engine, checkout_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_void(client, token, checkout_id), 409, CHANGED)

    assert gateway.calls == []
    assert _snapshot(engine, checkout_id) == before


def test_void_paid_record_with_stripe_unavailable_is_503(
    engine: Engine, app: FastAPI, client: TestClient, token: str, variant: int
) -> None:
    """第 11 条（1）「本站记录的付款状态已是 paid 而这次 Stripe 不可用或答复冲突时，不凭核实作废，
    回答 Stripe 暂不可用、请稍后再试」：503 stripe_unavailable，不写库、不写审计。"""
    order_id = _make_order(engine, variant)
    checkout_id = _checkout(engine, order_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID)
    before = _snapshot(engine, checkout_id)
    gateway = _inject(app, FakeStripeGateway(UNAVAILABLE))

    _assert_error(_void(client, token, checkout_id), 503, UNAVAILABLE_BODY)

    assert len(gateway.calls) == 1
    assert _snapshot(engine, checkout_id) == before


def test_void_unknown_checkout_is_404(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 11 条（1）「服务端先以普通读取重查记录」；验收：记录不存在 404 not_found（最大的合法 ID
    也是 404 而不是 422），不调用 Stripe、不写审计。"""
    gateway = _inject(app, FakeStripeGateway())

    for checkout_id in (12345, 2**31 - 1):
        _assert_error(_void(client, token, checkout_id), 404, NOT_FOUND)

    assert gateway.calls == []
    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 作废：授权与检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing"),
        pytest.param({"verified": False}, id="false"),
        pytest.param({"verified": "true"}, id="string"),
        pytest.param({"verified": 1}, id="one"),
        pytest.param({"verified": 1.0}, id="float"),
        pytest.param({"verified": None}, id="null"),
        pytest.param({"verified": True, "note": "SECRETVALUE"}, id="extra"),
        pytest.param(["SECRETVALUE"], id="not-object"),
    ],
)
def test_void_requires_exactly_verified_true(
    client: TestClient, token: str, spies: dict[str, _Spy], body: Any
) -> None:
    """第 11 条（1）「须同时勾选『已在 Stripe 测试后台核对过这笔订单的会话与付款』，未勾选服务端
    拒绝」；验收：请求体恰好是 {"verified": true}，缺少、不为 true 或有多余字段都是 422，错误只给
    位置、类型与固定消息、不回显请求内容，不调用服务层。"""
    response = _void(client, token, 1, body)

    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert errors and all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert all(error["loc"][0] == "body" for error in errors)
    assert "SECRETVALUE" not in response.text
    _no_service_calls(spies)


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "01", "1.5", "2147483648", "SECRETVALUE"])
def test_void_invalid_checkout_id_is_plain_422(
    client: TestClient, token: str, spies: dict[str, _Spy], raw: str
) -> None:
    """第 11 条「页面与接口只认管理员会话，写操作校验 CSRF」（校验不通过即停，不碰记录）与「只显示
    对账字段（本站记录 ID……）」；验收：路径里的记录 ID 须是不带符号与前导零的十进制整数，否则
    422，只含位置、类型与固定消息，不回显所给的值，不调用服务层。"""
    response = _void(client, token, raw)

    _assert_error(response, 422, {"detail": [CHECKOUT_ID_ERROR]})
    assert "SECRETVALUE" not in response.text
    _no_service_calls(spies)


def test_void_path_and_body_errors_are_reported_together(
    client: TestClient, token: str, spies: dict[str, _Spy]
) -> None:
    """第 11 条（1）「须同时勾选『已在 Stripe 测试后台核对过这笔订单的会话与付款』，未勾选服务端
    拒绝」；验收：路径与请求体的结构一并校验（照 admin_refunds.py 的审核接口），两者都不合法时
    422 同时列出，不调用服务层。"""
    response = _void(client, token, "abc", {})

    assert response.status_code == 422
    locs = [error["loc"] for error in response.json()["detail"]]
    assert locs == [["path", "checkout_id"], ["body", "verified"]]
    _no_service_calls(spies)


@pytest.mark.parametrize("raw", ["1", "abc"])
def test_void_requires_admin_session(client: TestClient, spies: dict[str, _Spy], raw: str) -> None:
    """第 11 条「页面与接口只认管理员会话」：没有后台会话时 401 admin_session_required，先于
    CSRF、路径与请求体校验，不调用服务层。"""
    response = client.post(_void_url(raw), json={})

    _assert_error(response, 401, SESSION_REQUIRED)
    _no_service_calls(spies)


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicate"])
@pytest.mark.parametrize("raw", ["1", "abc"])
def test_void_requires_csrf(
    client: TestClient, token: str, spies: dict[str, _Spy], csrf: str, raw: str
) -> None:
    """第 11 条「写操作校验 CSRF」：缺、错或重复的 X-CSRF-Token 403 csrf_failed，先于路径与请求体
    校验，不调用服务层。"""
    headers: list[tuple[str, str]] = [("Cookie", f"{COOKIE_NAME}={token}")]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", "0" * 64))
    elif csrf == "duplicate":
        headers.extend([("X-CSRF-Token", _csrf_for(token))] * 2)

    response = client.post(_void_url(raw), json={}, headers=headers)

    _assert_error(response, 403, CSRF_FAILED)
    _no_service_calls(spies)


def test_void_body_too_large_is_413_before_session(
    client: TestClient, spies: dict[str, _Spy]
) -> None:
    """第 11 条「所有响应带 no-store」；验收：原始请求体经 _json_body 读取，超过上限 413，作为依赖
    先于 require_admin（这里不带会话），不调用服务层。"""
    raw = b'{"verified": true, "pad": "' + b"x" * pay.MAX_BODY_BYTES + b'"}'

    response = client.post(_void_url(1), content=raw, headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    _assert_no_store(response)
    _no_service_calls(spies)


def test_void_non_json_is_415_before_session(client: TestClient, spies: dict[str, _Spy]) -> None:
    """第 11 条「所有响应带 no-store」；验收：请求体不是 JSON 415，先于 require_admin，不调用
    服务层。"""
    response = client.post(
        _void_url(1), content=b"verified=true", headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 415
    _assert_no_store(response)
    _no_service_calls(spies)


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


class _CommittedCheckGateway(FakeStripeGateway):
    """调用退款接口时在另一个数据库会话里读新尝试与审计，验证它们已经提交。"""

    def __init__(self, engine: Engine, *results: StripeResult[Any]) -> None:
        super().__init__(*results)
        self._engine = engine
        self.seen: tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]] | None = None

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        self.seen = (_attempts(self._engine), _audits(self._engine))
        return super().create_refund(form, idempotency_key)


class _RaisingGateway(FakeStripeGateway):
    """记下调用后抛出异常的替身（模拟适配器之外的意外错误）。"""

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        super().create_refund(form, idempotency_key)
        raise RuntimeError("unexpected gateway failure pi_test_secret")


def test_retry_creates_attempt_audits_commits_then_calls_stripe(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    admin_id: int,
    token: str,
    variant: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 11 条（2）「新建一条尝试，金额等于原尝试金额……提交后以新尝试的 ID 为幂等键调用
    Stripe」与「重试写一条审计记录（操作名 stripe_late_refund_retried，对象为新尝试）」：200
    {"result": "retried"}；调用 Stripe 时新尝试与审计已提交；幂等键为新尝试 ID。验收：不读请求体、
    不要幂等键（这里带着不是 JSON 的请求体）；接口不写日志。"""
    checkout_id, intent_id, first_id = _failed_late_refund(engine, variant)
    new_id = _next_attempt_id(engine)
    gateway = _CommittedCheckGateway(engine, UNAVAILABLE)
    _inject(app, gateway)
    caplog.set_level(logging.DEBUG)
    headers = _headers(token, _csrf_for(token)) | {"Content-Type": "text/plain"}

    response = client.post(_retry_url(checkout_id), content=b"not json", headers=headers)

    assert _ok_json(response) == RETRIED
    expected_attempts = [
        (first_id, checkout_id, TOTAL, STRIPE_REFUND_FAILED, None),
        (new_id, checkout_id, TOTAL, STRIPE_REFUND_PENDING, checkout_id),
    ]
    expected_audits = [(admin_id, RETRIED_ACTION, ATTEMPT_TARGET, new_id, None, str(checkout_id))]
    assert gateway.seen == (expected_attempts, expected_audits)
    assert _attempts(engine) == expected_attempts
    assert _audits(engine) == expected_audits
    assert [(call.path, call.idempotency_key) for call in gateway.calls] == [
        (STRIPE_REFUNDS_PATH, str(new_id))
    ]
    assert intent_id not in response.text
    _assert_no_private(response.text)
    assert _api_logs(caplog) == []


def test_retry_gateway_exception_still_200(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    variant: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 11 条（2）「之后的状态更新与告警照第 8 条」（第 8 条：本站结果不因 Stripe 退款失败而
    回滚）；验收：submit_refund_attempt 抛出异常时仍 200 {"result": "retried"}，新尝试与审计已
    提交、尝试保持 pending，恰有一条 stripe_refund_submit_failed attempt=<ID>，不含异常内容。"""
    checkout_id, _, _ = _failed_late_refund(engine, variant)
    new_id = _next_attempt_id(engine)
    _inject(app, _RaisingGateway())
    caplog.set_level(logging.DEBUG)

    assert _ok_json(_retry(client, token, checkout_id)) == RETRIED

    assert _attempts(engine)[-1] == (
        new_id,
        checkout_id,
        TOTAL,
        STRIPE_REFUND_PENDING,
        checkout_id,
    )
    assert _audits(engine)[-1][1:] == (
        RETRIED_ACTION,
        ATTEMPT_TARGET,
        new_id,
        None,
        str(checkout_id),
    )
    assert _api_logs(caplog) == [f"{SUBMIT_FAILED} attempt={new_id}"]
    assert "unexpected gateway failure" not in caplog.text
    assert "pi_test_secret" not in caplog.text


@pytest.mark.parametrize("latest", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_retry_refused_unless_latest_failed(
    engine: Engine, app: FastAPI, client: TestClient, token: str, variant: int, latest: str
) -> None:
    """第 11 条（2）「重查该来源最新一次尝试仍为 failed（不是则回滚并回答已变化……）」：最新为
    pending 或 succeeded 时 409 stripe_refund_not_retryable，不写库、不调用 Stripe。"""
    checkout_id, intent_id, _ = _failed_late_refund(engine, variant)
    _add_attempt(engine, checkout_id, intent_id, latest)
    before = _snapshot(engine, checkout_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, checkout_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, checkout_id) == before
    assert gateway.calls == []


def test_retry_twice_only_first_succeeds(
    engine: Engine, app: FastAPI, client: TestClient, token: str, variant: int
) -> None:
    """第 11 条（2）「受同一来源至多一条不是 failed 的唯一约束，重复点击与并发只有一个成功」：
    第二次 409 stripe_refund_not_retryable，只多一条尝试、一条审计。"""
    checkout_id, _, _ = _failed_late_refund(engine, variant)
    gateway = _inject(app, FakeStripeGateway(UNAVAILABLE))
    assert _ok_json(_retry(client, token, checkout_id)) == RETRIED
    after_first = _snapshot(engine, checkout_id)

    _assert_error(_retry(client, token, checkout_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, checkout_id) == after_first
    assert len(gateway.calls) == 1


def test_retry_without_attempt_is_not_retryable(
    engine: Engine, app: FastAPI, client: TestClient, token: str, variant: int
) -> None:
    """第 11 条（2）「来源为会话（第 6 条）、该来源最新一次尝试为 failed」才可重试：没有来源为该
    记录的尝试时 409 stripe_refund_not_retryable，不写库、不调用 Stripe。"""
    checkout_id = _checkout(engine, _make_order(engine, variant), CHECKOUT_OPEN)
    before = _snapshot(engine, checkout_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, checkout_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, checkout_id) == before
    assert gateway.calls == []


def test_retry_over_cap_is_409(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    variant: int,
    alerts: list[tuple[str, object]],
) -> None:
    """第 11 条（2）「核对该 PaymentIntent 的 pending 与 succeeded 合计加上新尝试金额不超过付款
    金额，超出即不建、回滚，之后照常记日志并告警（stripe_refund_cap，对象为结账记录……）」：
    409 stripe_refund_cap，不写库、不调用 Stripe；告警由服务层发出一次。"""
    checkout_id, intent_id, _ = _failed_late_refund(engine, variant)
    # 同一 PaymentIntent 上另一条记录的 pending 尝试占用额度。
    other_id = _checkout(engine, _make_order(engine, variant, STATUS_CANCELLED), CHECKOUT_EXPIRED)
    _add_attempt(engine, other_id, intent_id, STRIPE_REFUND_PENDING)
    before = _snapshot(engine, checkout_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, checkout_id), 409, CAP)

    assert _snapshot(engine, checkout_id) == before
    assert gateway.calls == []
    assert alerts == [("stripe_refund_cap", (CHECKOUT_TARGET, checkout_id))]


def test_retry_unknown_checkout_is_404(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 11 条（2）的重试对象是结账记录；验收：记录不存在 404 not_found（最大的合法 ID 也是 404
    而不是 422），不写审计、不调用 Stripe。"""
    gateway = _inject(app, FakeStripeGateway())

    for checkout_id in (12345, 2**31 - 1):
        _assert_error(_retry(client, token, checkout_id), 404, NOT_FOUND)

    assert gateway.calls == []
    assert _audits(engine) == []


@pytest.mark.parametrize("raw", ["1", "abc"])
def test_retry_requires_admin_session(client: TestClient, spies: dict[str, _Spy], raw: str) -> None:
    """第 11 条「页面与接口只认管理员会话」：没有后台会话时 401 admin_session_required，先于 CSRF
    与路径校验，不调用服务层。"""
    _assert_error(client.post(_retry_url(raw)), 401, SESSION_REQUIRED)

    _no_service_calls(spies)


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicate"])
@pytest.mark.parametrize("raw", ["1", "abc"])
def test_retry_requires_csrf(
    client: TestClient, token: str, spies: dict[str, _Spy], csrf: str, raw: str
) -> None:
    """第 11 条「写操作校验 CSRF」：缺、错或重复的 X-CSRF-Token 403 csrf_failed，先于路径校验，
    不调用服务层。"""
    headers: list[tuple[str, str]] = [("Cookie", f"{COOKIE_NAME}={token}")]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", "0" * 64))
    elif csrf == "duplicate":
        headers.extend([("X-CSRF-Token", _csrf_for(token))] * 2)

    _assert_error(client.post(_retry_url(raw), headers=headers), 403, CSRF_FAILED)

    _no_service_calls(spies)


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "01", "1.5", "2147483648", "SECRETVALUE"])
def test_retry_invalid_checkout_id_is_plain_422(
    client: TestClient, token: str, spies: dict[str, _Spy], raw: str
) -> None:
    """第 11 条「页面与接口只认管理员会话，写操作校验 CSRF」（校验不通过即停，不碰记录）与「只显示
    对账字段（本站记录 ID……）」；验收：路径里的记录 ID 不合法时 422，只含位置、类型与固定消息，
    不回显所给的值，不调用服务层。"""
    response = _retry(client, token, raw)

    _assert_error(response, 422, {"detail": [CHECKOUT_ID_ERROR]})
    assert "SECRETVALUE" not in response.text
    _no_service_calls(spies)


# ---------------------------------------------------------------------------
# no-store
# ---------------------------------------------------------------------------


_SENDERS: dict[str, Callable[[TestClient], Any]] = {
    "list": lambda c: c.get(PENDING_URL),
    "void": lambda c: c.post(_void_url(1), json=VERIFIED),
    "retry": lambda c: c.post(_retry_url(1)),
}


@pytest.mark.parametrize("name", sorted(_SENDERS))
def test_error_responses_are_no_store(client: TestClient, name: str) -> None:
    """第 11 条「所有响应带 no-store」：三个接口的错误响应（这里是依赖抛出的 401）也带
    Cache-Control: no-store。"""
    response = _SENDERS[name](client)

    assert response.status_code == 401, response.text
    _assert_no_store(response)

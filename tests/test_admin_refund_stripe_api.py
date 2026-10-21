"""批准退款后调用 Stripe 与手动重试接口（SHOP-TASK-131）：
POST /api/admin/refunds/{refund_id}/approve 接入的调用与新增的
POST /api/admin/refunds/{refund_id}/stripe-retry（app/api/admin_refunds.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 8 条（下称「第 8 条」：
提交后调用 Stripe 退款接口，以该记录的本站 ID 为幂等键；最新一次尝试为 failed 时管理员可手动
重试，重试是新建一条尝试，受唯一约束，重复点击与并发只有一个成功，再以它的 ID 调用 Stripe；
本站的批准结果不因 Stripe 退款失败而回滚）、第 9 条（CI 与本地测试用模拟的 Stripe 适配器）、
docs/HANDOFF.md 0.54 第（1）项（在批准请求里提交之后立即调用；在那之前及调用结果未知时由定时
任务在下一轮发起）与 docs/UX.md 0.11 的 A03（重试成功后重新读取；被合计上限拒绝时提示）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的是哪一句；只出自 SHOP-TASK-131
验收标准的约定注明「验收：」。

夹具与后台登录照 tests/test_admin_refunds.py（SQLite 内存库、StaticPool、由引擎发 BEGIN，
接口的会话依赖换成每个请求一个绑定同一内存库的会话），Stripe 付款的会话记录照
tests/test_stripe_refund_approval.py 的 _stripe_paid 直接写库。Stripe 一律以
app.state.stripe_gateway 注入 FakeStripeGateway，接口的当前时间以 monkeypatch 固定
app.api.admin_refunds 的 _utcnow。检查结果一律在另一个数据库会话里读。
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_refunds
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Order,
    OrderItem,
    OrderItemUnit,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import ACTOR_GUEST, STATUS_SHIPPED
from app.models.refund import REFUND_APPROVED, REFUND_REQUESTED
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    PAYMENT_PAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services.admin_auth import issue_admin_session
from app.services.order_rules import generate_order_number
from app.services.stripe_gateway import (
    FakeStripeGateway,
    Form,
    Refund,
    StripeError,
    StripeOutcome,
    StripeResult,
)

REFUNDS_URL = "/api/admin/refunds"
STRIPE_REFUNDS_PATH = "/v1/refunds"

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

API_LOGGER = "app.api.admin_refunds"
SUBMIT_FAILED = "stripe_refund_submit_failed"
RETRIED_ACTION = "admin_stripe_refund_retried"
TARGET = "refund_request"

# 订单行：单价与逐件券额；第 0 行第 1 件现金实付 2701 仙。
LINES = [(3000, [299, 301, 300]), (1500, [0])]
SUBTOTAL = 3 * 3000 + 1500
COUPON = 900
SHIPPING = 800
# 应付 10400 仙：会话记录的 amount_sen，即付款金额。
TOTAL = SUBTOTAL - COUPON + SHIPPING
# 申请第 0 行一件的现金退款额（不含运费）。
UNIT_CASH = 2701

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "not_found"}
NOT_APPROVED = {"detail": "refund_not_approved"}
NOT_RETRYABLE = {"detail": "stripe_refund_not_retryable"}
CAP = {"detail": "stripe_refund_cap"}
RETRIED = {"result": "retried"}
REFUND_ID_ERROR = {
    "type": "refund_id_invalid",
    "loc": ["path", "refund_id"],
    "msg": "Refund request ID should be a positive integer",
}

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


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
    """接口的当前时间（审核时间与尝试的创建时间）。"""
    fixed = _now()
    monkeypatch.setattr(admin_refunds, "_utcnow", lambda: fixed)
    return fixed


@pytest.fixture
def app(engine: Engine, now: datetime) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _make_order(engine: Engine) -> int:
    """直接写一张已发货的游客订单（订单行与逐件分摊快照）并提交，返回订单 ID。"""
    created = _now() - timedelta(days=2)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=STATUS_SHIPPED,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=COUPON,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=TOTAL,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=created + timedelta(minutes=2),
        )
        db.add(order)
        db.flush()
        for line_index, (price, coupons) in enumerate(LINES):
            item = OrderItem(
                order_id=order.id,
                line_index=line_index,
                variant_id=None,
                sku=f"SKU-{line_index}",
                product_name_en="Item",
                product_name_zh="商品",
                product_name_ms="Barang",
                variant_label_en="",
                variant_label_zh="",
                variant_label_ms="",
                unit_price_sen=price,
                quantity=len(coupons),
                line_subtotal_sen=price * len(coupons),
            )
            db.add(item)
            db.flush()
            for unit_index, coupon in enumerate(coupons):
                db.add(
                    OrderItemUnit(
                        order_item_id=item.id,
                        unit_index=unit_index,
                        original_price_sen=price,
                        coupon_discount_sen=coupon,
                        points_discount=0,
                        cash_paid_sen=price - coupon,
                        points_earned=0,
                    )
                )
        db.flush()
        order_id = order.id
        db.commit()
    return order_id


def _stripe_paid(engine: Engine, order_id: int) -> tuple[int, str]:
    """直接写该单由 Stripe 付款的会话记录（complete、paid、确认时间非空），
    返回记录 ID 与 PaymentIntent ID。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=2)
    intent_id = f"pi_test_{n}"
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_{n}",
            payment_intent_id=intent_id,
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_COMPLETE,
            payment_status=PAYMENT_PAID,
            payment_method_type="card",
            create_params="[]",
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            completed_at=started + timedelta(minutes=4),
            confirmed_at=started + timedelta(minutes=4),
            active_order_id=order_id,
        )
        db.add(record)
        db.commit()
        return record.id, intent_id


def _add_request(engine: Engine, order_id: int) -> int:
    """直接写一笔 requested 的退款申请（第 0 行第 1 件，占用）并提交，返回申请 ID。"""
    with Session(engine) as db:
        item = db.scalars(
            select(OrderItem).where(OrderItem.order_id == order_id, OrderItem.line_index == 0)
        ).one()
        unit = db.scalars(
            select(OrderItemUnit).where(
                OrderItemUnit.order_item_id == item.id, OrderItemUnit.unit_index == 0
            )
        ).one()
        request = RefundRequest(
            order_id=order_id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_GUEST,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            amount_sen=unit.cash_paid_sen,
            created_at=_now() - timedelta(hours=1),
        )
        db.add(request)
        db.flush()
        line = RefundLine(
            refund_request_id=request.id,
            order_item_id=item.id,
            quantity=1,
            amount_sen=unit.cash_paid_sen,
        )
        db.add(line)
        db.flush()
        db.add(
            RefundLineUnit(
                refund_line_id=line.id,
                order_item_unit_id=unit.id,
                cash_paid_sen=unit.cash_paid_sen,
                occupied=1,
            )
        )
        assert unit.cash_paid_sen == UNIT_CASH
        request_id = request.id
        db.commit()
    return request_id


def _insert_checkout_attempt(engine: Engine, checkout_id: int, intent_id: str, amount: int) -> None:
    """直接插入一条复用该付款 PaymentIntent ID 的 pending 尝试（来源为该会话记录）。"""
    with Session(engine) as db:
        db.add(
            StripeRefund(
                payment_intent_id=intent_id,
                source_type=SOURCE_CHECKOUT,
                checkout_id=checkout_id,
                amount_sen=amount,
                status=STRIPE_REFUND_PENDING,
                call_count=1,
                created_at=_now() - timedelta(hours=1),
                active_checkout_id=checkout_id,
            )
        )
        db.commit()


class _RaisingGateway(FakeStripeGateway):
    """记下调用后抛出异常的替身（模拟适配器之外的意外错误）。"""

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        super().create_refund(form, idempotency_key)
        raise RuntimeError("unexpected gateway failure")


def _inject(app: FastAPI, gateway: FakeStripeGateway) -> FakeStripeGateway:
    app.state.stripe_gateway = gateway
    return gateway


def _next_attempt_id(engine: Engine) -> int:
    """下一条尝试的 ID（SQLite 的整数主键取当前最大值加一）。"""
    with Session(engine) as db:
        return (db.scalar(select(func.max(StripeRefund.id))) or 0) + 1


def _refund(intent_id: str, attempt_id: int, status: str) -> StripeResult[Refund]:
    """与尝试相符的退款接口响应。"""
    refund = Refund(
        id=f"re_test_{attempt_id}",
        status=status,
        amount=UNIT_CASH,
        payment_intent=intent_id,
        attempt_id=str(attempt_id),
    )
    return StripeResult(StripeOutcome.OK, refund)


def _rejected() -> StripeResult[Refund]:
    return StripeResult(
        StripeOutcome.REJECTED, error=StripeError(400, type="invalid_request_error")
    )


def _unavailable() -> StripeResult[Refund]:
    return StripeResult(StripeOutcome.UNAVAILABLE)


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None, key: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _approve(client: TestClient, token: str, refund_id: int, key: str | None = None) -> Any:
    headers = _headers(token, _csrf_for(token), key or _key())
    return client.post(f"{REFUNDS_URL}/{refund_id}/approve", json={}, headers=headers)


def _retry_url(refund_id: int | str) -> str:
    return f"{REFUNDS_URL}/{refund_id}/stripe-retry"


def _retry(client: TestClient, token: str, refund_id: int | str) -> Any:
    return client.post(_retry_url(refund_id), headers=_headers(token, _csrf_for(token)))


def _attempts(engine: Engine) -> list[tuple[Any, ...]]:
    """全部退款尝试，按 ID：（ID、申请、金额、状态、Stripe 退款 ID、调用次数、
    最后的错误类别、活动来源申请列）。"""
    with Session(engine) as db:
        rows = db.scalars(select(StripeRefund).order_by(StripeRefund.id))
        return [
            (
                row.id,
                row.refund_request_id,
                row.amount_sen,
                row.status,
                row.stripe_refund_id,
                row.call_count,
                row.last_error_type,
                row.active_refund_request_id,
            )
            for row in rows
        ]


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


def _request_status(engine: Engine, request_id: int) -> str:
    with Session(engine) as db:
        stmt = select(RefundRequest.status).where(RefundRequest.id == request_id)
        return db.scalars(stmt).one()


def _snapshot(engine: Engine, request_id: int) -> tuple[Any, ...]:
    """尝试、审计与申请状态：用来断言「不写库」。"""
    return _attempts(engine), _audits(engine), _request_status(engine, request_id)


def _api_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == API_LOGGER]


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _expected_outcome(request_id: int, now: datetime) -> dict[str, Any]:
    return {
        "refund_request_id": request_id,
        "status": REFUND_APPROVED,
        "reviewed_at": _iso(now),
        "review_reason": None,
    }


def _stripe_request(engine: Engine) -> tuple[int, int, str]:
    """Stripe 付款订单上一笔 requested、现金退款额 2701 仙的申请；
    返回申请 ID、会话记录 ID 与 PaymentIntent ID。"""
    order_id = _make_order(engine)
    checkout_id, intent_id = _stripe_paid(engine, order_id)
    return _add_request(engine, order_id), checkout_id, intent_id


def _approved_with(
    app: FastAPI, client: TestClient, engine: Engine, token: str, result: str
) -> tuple[int, int, str, int]:
    """经接口批准一笔 Stripe 付款订单的申请，批准时适配器回 result
    （failed 为明确拒绝、pending 为不可用、succeeded 为退款成功）；
    返回申请 ID、会话记录 ID、PaymentIntent ID 与批准建的尝试 ID。"""
    request_id, checkout_id, intent_id = _stripe_request(engine)
    attempt_id = _next_attempt_id(engine)
    outcomes = {
        STRIPE_REFUND_FAILED: _rejected(),
        STRIPE_REFUND_PENDING: _unavailable(),
        STRIPE_REFUND_SUCCEEDED: _refund(intent_id, attempt_id, "succeeded"),
    }
    _inject(app, FakeStripeGateway(outcomes[result]))
    _ok(_approve(client, token, request_id))
    assert [row[3] for row in _attempts(engine) if row[1] == request_id] == [result]
    return request_id, checkout_id, intent_id, attempt_id


# ---------------------------------------------------------------------------
# 批准后调用 Stripe
# ---------------------------------------------------------------------------


def test_approve_stripe_order_calls_refund_once_with_attempt_key(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 8 条「提交后调用 Stripe 退款接口，以该记录的本站 ID 为幂等键」与 HANDOFF 0.54
    第（1）项「在批准请求里提交之后立即调用」：批准 Stripe 付款订单后适配器恰好收到一次
    create_refund，幂等键为新尝试 ID，金额为申请的现金退款额，元数据为尝试 ID；尝试按
    退款对象写入（succeeded、Stripe 退款 ID、调用次数 1）；接口不写日志。"""
    caplog.set_level(logging.DEBUG)
    request_id, _, intent_id = _stripe_request(engine)
    attempt_id = _next_attempt_id(engine)
    gateway = _inject(app, FakeStripeGateway(_refund(intent_id, attempt_id, "succeeded")))

    body = _ok(_approve(client, token, request_id))

    assert body == _expected_outcome(request_id, now)
    assert _request_status(engine, request_id) == REFUND_APPROVED
    assert len(gateway.calls) == 1
    (call,) = gateway.calls
    assert (call.method, call.path, call.idempotency_key) == (
        "POST",
        STRIPE_REFUNDS_PATH,
        str(attempt_id),
    )
    assert call.form == (
        ("payment_intent", intent_id),
        ("amount", str(UNIT_CASH)),
        ("metadata[attempt_id]", str(attempt_id)),
    )
    assert _attempts(engine) == [
        (
            attempt_id,
            request_id,
            UNIT_CASH,
            STRIPE_REFUND_SUCCEEDED,
            f"re_test_{attempt_id}",
            1,
            None,
            request_id,
        )
    ]
    assert _api_logs(caplog) == []


@pytest.mark.parametrize(
    ("result", "status", "error_type", "active"),
    [
        ("unavailable", STRIPE_REFUND_PENDING, None, True),
        ("rejected", STRIPE_REFUND_FAILED, "invalid_request_error", False),
    ],
)
def test_stripe_failure_does_not_change_approval(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    now: datetime,
    result: str,
    status: str,
    error_type: str | None,
    active: bool,
) -> None:
    """第 8 条「本站的批准结果不因 Stripe 退款失败而回滚」、「只有 … Stripe 以请求错误明确
    拒绝创建 … 才记为 failed … 其余一律保持 pending」：适配器回 unavailable 或 rejected 时
    批准仍为 200 且内容与成功时相同、申请已批准；尝试分别保持 pending（留给定时任务）
    与记为 failed（记下错误类别、释放来源）。"""
    request_id, _, _ = _stripe_request(engine)
    attempt_id = _next_attempt_id(engine)
    outcome = _unavailable() if result == "unavailable" else _rejected()
    gateway = _inject(app, FakeStripeGateway(outcome))

    body = _ok(_approve(client, token, request_id))

    assert body == _expected_outcome(request_id, now)
    assert _request_status(engine, request_id) == REFUND_APPROVED
    assert [call.idempotency_key for call in gateway.calls] == [str(attempt_id)]
    assert _attempts(engine) == [
        (
            attempt_id,
            request_id,
            UNIT_CASH,
            status,
            None,
            1,
            error_type,
            request_id if active else None,
        )
    ]


def test_replay_by_review_key_does_not_call_stripe(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """HANDOFF 0.54 第（1）项「在那之前及调用结果未知时由定时任务在下一轮发起」；验收：按审核
    幂等键重放时不调用 Stripe，重放时的尝试（首次调用 unavailable 后仍为 pending）留给定时
    任务；重放返回原结果、尝试不变。"""
    request_id, _, _ = _stripe_request(engine)
    gateway = _inject(app, FakeStripeGateway(_unavailable()))
    key = _key()
    first = _ok(_approve(client, token, request_id, key))
    after_first = _attempts(engine)
    assert len(gateway.calls) == 1

    second = _ok(_approve(client, token, request_id, key))

    assert second == first
    assert len(gateway.calls) == 1
    assert _attempts(engine) == after_first
    assert after_first[0][3] == STRIPE_REFUND_PENDING


def test_mock_paid_order_does_not_call_stripe(
    engine: Engine, app: FastAPI, client: TestClient, token: str, now: datetime
) -> None:
    """第 8 条「订单经 Stripe 付款」才建尝试；验收：没有尝试（模拟支付）时不调用 Stripe，
    批准照常 200。"""
    request_id = _add_request(engine, _make_order(engine))
    gateway = _inject(app, FakeStripeGateway())

    body = _ok(_approve(client, token, request_id))

    assert body == _expected_outcome(request_id, now)
    assert gateway.calls == []
    assert _attempts(engine) == []


def test_approve_over_cap_does_not_call_stripe(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 8 条「合计不得超过付款金额 … 超出即不建并告警」；验收：因合计上限没建成尝试时
    不调用 Stripe，批准照常 200。"""
    request_id, checkout_id, intent_id = _stripe_request(engine)
    _insert_checkout_attempt(engine, checkout_id, intent_id, TOTAL - UNIT_CASH + 1)
    gateway = _inject(app, FakeStripeGateway())

    _ok(_approve(client, token, request_id))

    assert _request_status(engine, request_id) == REFUND_APPROVED
    assert gateway.calls == []
    assert [row for row in _attempts(engine) if row[1] == request_id] == []


def test_gateway_exception_still_200_and_logs_once(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    now: datetime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 8 条「本站的批准结果不因 Stripe 退款失败而回滚」与 HANDOFF 0.54 第（1）项「调用结果
    未知时由定时任务在下一轮发起」；验收：调用抛出异常时仍 200 与审核结果、批准已提交、
    尝试保持 pending，恰有一条 stripe_refund_submit_failed 日志（只含类别与尝试 ID）。"""
    caplog.set_level(logging.DEBUG)
    request_id, _, _ = _stripe_request(engine)
    attempt_id = _next_attempt_id(engine)
    gateway = _inject(app, _RaisingGateway())

    body = _ok(_approve(client, token, request_id))

    assert body == _expected_outcome(request_id, now)
    assert _request_status(engine, request_id) == REFUND_APPROVED
    assert len(gateway.calls) == 1
    (row,) = _attempts(engine)
    assert (row[0], row[3], row[4]) == (attempt_id, STRIPE_REFUND_PENDING, None)
    assert _api_logs(caplog) == [f"{SUBMIT_FAILED} attempt={attempt_id}"]
    assert "unexpected gateway failure" not in caplog.text


# ---------------------------------------------------------------------------
# 手动重试
# ---------------------------------------------------------------------------


def test_retry_after_failed_creates_attempt_audits_and_calls(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    admin_id: int,
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 8 条「最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试 … 再以它的 ID
    调用 Stripe」与 UX 0.11 A03「重试成功后重新读取」：200 {"result": "retried"}（不带
    Stripe 内容）；多一条 pending 尝试；审计一条，新值为新尝试 ID；适配器以新 ID 为幂等键
    调用。验收：不读请求体、不要 Idempotency-Key（这里带着不是 JSON 的请求体、不带幂等键）；
    接口不写日志。"""
    request_id, _, intent_id, first_id = _approved_with(
        app, client, engine, token, STRIPE_REFUND_FAILED
    )
    audits_before = _audits(engine)
    new_id = _next_attempt_id(engine)
    gateway = _inject(app, FakeStripeGateway(_unavailable()))
    caplog.set_level(logging.DEBUG)
    headers = _headers(token, _csrf_for(token)) | {"Content-Type": "text/plain"}

    response = client.post(_retry_url(request_id), content=b"not json", headers=headers)

    assert _ok(response) == RETRIED
    attempts = _attempts(engine)
    assert [row[0] for row in attempts] == [first_id, new_id]
    assert attempts[0][3] == STRIPE_REFUND_FAILED
    assert attempts[1] == (
        new_id,
        request_id,
        UNIT_CASH,
        STRIPE_REFUND_PENDING,
        None,
        1,
        None,
        request_id,
    )
    assert _audits(engine) == [
        *audits_before,
        (admin_id, RETRIED_ACTION, TARGET, request_id, None, str(new_id)),
    ]
    assert [(call.path, call.idempotency_key) for call in gateway.calls] == [
        (STRIPE_REFUNDS_PATH, str(new_id))
    ]
    assert ("metadata[attempt_id]", str(new_id)) in gateway.calls[0].form
    assert intent_id not in response.text
    assert _api_logs(caplog) == []


def test_retry_gateway_exception_still_200(
    engine: Engine,
    app: FastAPI,
    client: TestClient,
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 8 条「本站的批准结果不因 Stripe 退款失败而回滚」（重试同样）；验收：重试后的调用
    抛出异常时仍 200 {"result": "retried"}，新尝试与审计已提交、尝试保持 pending，恰有一条
    stripe_refund_submit_failed 日志。"""
    request_id, _, _, _ = _approved_with(app, client, engine, token, STRIPE_REFUND_FAILED)
    new_id = _next_attempt_id(engine)
    _inject(app, _RaisingGateway())
    caplog.set_level(logging.DEBUG)

    assert _ok(_retry(client, token, request_id)) == RETRIED

    assert _attempts(engine)[-1][:4] == (new_id, request_id, UNIT_CASH, STRIPE_REFUND_PENDING)
    assert _audits(engine)[-1][1:] == (RETRIED_ACTION, TARGET, request_id, None, str(new_id))
    assert _api_logs(caplog) == [f"{SUBMIT_FAILED} attempt={new_id}"]


@pytest.mark.parametrize("latest", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_retry_refused_unless_latest_failed(
    engine: Engine, app: FastAPI, client: TestClient, token: str, latest: str
) -> None:
    """第 8 条「最新一次尝试为 failed 时」才可手动重试：最新为 pending 或 succeeded 时
    409 stripe_refund_not_retryable，不写库、不调用适配器。"""
    request_id, _, _, _ = _approved_with(app, client, engine, token, latest)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, request_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


def test_retry_twice_only_first_succeeds(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 8 条「重复点击与并发只有一个成功」：第一次重试后新尝试为 pending，紧接着的第二次
    409 stripe_refund_not_retryable，只多一条尝试、一条审计。"""
    request_id, _, _, _ = _approved_with(app, client, engine, token, STRIPE_REFUND_FAILED)
    gateway = _inject(app, FakeStripeGateway(_unavailable()))
    assert _ok(_retry(client, token, request_id)) == RETRIED
    after_first = _snapshot(engine, request_id)

    _assert_error(_retry(client, token, request_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, request_id) == after_first
    assert len(gateway.calls) == 1


def test_retry_without_attempt_is_not_retryable(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 8 条「最新一次尝试为 failed 时」：没有任何尝试（模拟支付订单的已批准申请）时
    409 stripe_refund_not_retryable，不写库、不调用适配器。"""
    request_id = _add_request(engine, _make_order(engine))
    _inject(app, FakeStripeGateway())
    _ok(_approve(client, token, request_id))
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, request_id), 409, NOT_RETRYABLE)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


def test_retry_requires_approved_request(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """「订单与退款状态」requested → 管理员 approved：退款只对 approved；验收：未批准时
    409 refund_not_approved，不写库、不调用适配器。"""
    request_id, _, _ = _stripe_request(engine)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, request_id), 409, NOT_APPROVED)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


def test_retry_over_cap_is_409(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额 … 超出即
    不建并告警」与 UX 0.11 A03「被合计上限拒绝时提示」：409 stripe_refund_cap（不带
    status），不写库、不调用适配器。"""
    request_id, checkout_id, intent_id, _ = _approved_with(
        app, client, engine, token, STRIPE_REFUND_FAILED
    )
    _insert_checkout_attempt(engine, checkout_id, intent_id, TOTAL - UNIT_CASH + 1)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    _assert_error(_retry(client, token, request_id), 409, CAP)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


def test_retry_unknown_request_is_404(
    engine: Engine, app: FastAPI, client: TestClient, token: str
) -> None:
    """验收：申请不存在 404 not_found（最大的合法 ID 也是 404 而不是 422），不写库、
    不调用适配器。"""
    request_id, _, _ = _stripe_request(engine)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    for refund_id in (request_id + 1, 2**31 - 1):
        _assert_error(_retry(client, token, refund_id), 404, NOT_FOUND)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# 重试接口的授权与检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["valid", "abc"])
def test_retry_requires_admin_session(
    engine: Engine, app: FastAPI, client: TestClient, raw: str
) -> None:
    """DESIGN「权限与资料保护」「后台单一管理员也须认证，不把权限检查留给前端」；验收：没有
    后台会话时 401 admin_session_required，先于 CSRF 与路径校验；不写库、不调用适配器。"""
    request_id, _, _ = _stripe_request(engine)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())
    refund_id = str(request_id) if raw == "valid" else raw

    _assert_error(client.post(_retry_url(refund_id)), 401, SESSION_REQUIRED)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicate"])
@pytest.mark.parametrize("raw", ["valid", "abc"])
def test_retry_requires_csrf(
    engine: Engine, app: FastAPI, client: TestClient, token: str, csrf: str, raw: str
) -> None:
    """DESIGN「权限与资料保护」「写操作另须 CSRF 令牌」；验收：缺或错 CSRF 403 csrf_failed，
    先于路径校验；不写库、不调用适配器。"""
    request_id, _, _, _ = _approved_with(app, client, engine, token, STRIPE_REFUND_FAILED)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())
    headers: list[tuple[str, str]] = [("Cookie", f"{COOKIE_NAME}={token}")]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", "0" * 64))
    elif csrf == "duplicate":
        headers.extend([("X-CSRF-Token", _csrf_for(token))] * 2)
    refund_id = str(request_id) if raw == "valid" else raw

    _assert_error(client.post(_retry_url(refund_id), headers=headers), 403, CSRF_FAILED)

    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "01", "1.5", "2147483648", "SECRETVALUE"])
def test_retry_invalid_refund_id_is_plain_422(
    engine: Engine, app: FastAPI, client: TestClient, token: str, raw: str
) -> None:
    """验收：路径里的申请 ID 照 _refund_id_errors 校验，不合法时 422，只含位置、类型与固定
    消息，不回显所给的值；不写库、不调用适配器。"""
    request_id, _, _ = _stripe_request(engine)
    before = _snapshot(engine, request_id)
    gateway = _inject(app, FakeStripeGateway())

    response = _retry(client, token, raw)

    _assert_error(response, 422, {"detail": [REFUND_ID_ERROR]})
    assert "SECRETVALUE" not in response.text
    assert _snapshot(engine, request_id) == before
    assert gateway.calls == []

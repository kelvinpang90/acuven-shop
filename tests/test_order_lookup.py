"""订单查询与确认收货：POST /api/orders/lookup、GET /api/orders/lookup、
POST /api/orders/confirm-receipt 与 ReceiptConfirmation 模型。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「权限与资料保护」第 2、6、7 条；「失败、并发与重试」
第 1、4 条；「订单与退款状态」第 2 条；「数据模型」的 PaymentAttempt / OrderEvent 一行。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句；
没有直接原句的，写明是 SHOP-TASK-027 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。订单在测试里直接写库建立，
不经下单接口。接口的会话依赖换成每个请求一个绑定同一内存库的会话；SHOP-TASK-026 的取客户端
依赖换成本文件的内存替身 FakeRedis（只实现被用到的 GET 与事务管道的 INCR、EXPIRE，
可按桶名注入连接错误）。内存库经 StaticPool 只有一个连接，pysqlite 在第一条写语句前才开始
事务，所以在一个会话还只做过查询时，用第二个会话执行并提交另一个操作，就能模拟并发请求先提交；
交错点是 order_lookup 模块调用迁移判定函数时（随后就是写确认收货记录与带条件的 UPDATE）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Order,
    OrderAccessGrant,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    ReceiptConfirmation,
)
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_SYSTEM,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP
from app.services import order_lookup
from app.services.order_access import COOKIE_NAME, csrf_token_for_cookie, issue_order_access
from app.services.order_rules import generate_order_number
from app.services.rate_limit import get_redis_client

LOOKUP_URL = "/api/orders/lookup"
CONFIRM_URL = "/api/orders/confirm-receipt"
PAY_URL = "/api/pay/attempts"
CANCEL_URL = "/api/pay/cancel"
MAX_BODY_BYTES = 8 * 1024

PHONE_E164 = "+60123456789"
PHONE_LOCAL = "012-345 6789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"
SHIPPING = 800

# 订单行：三语名称、三语规格说明、单价、件数。没有券与积分，逐件实付即单价。
LINES = [
    (("Tee", "T恤", "Tee"), ("Colour: Red", "颜色：红色", "Colour: Red"), 2590, 2),
    (("Mug", "马克杯", "Mug Kopi"), ("", "", ""), 1500, 1),
]
SUBTOTAL = 2 * 2590 + 1500

ORDER_FIELDS = {
    "order_number",
    "status",
    "created_at",
    "paid_at",
    "server_time",
    "lines",
    "subtotal_sen",
    "shipping_fee_sen",
    "total_sen",
    "recipient",
    # SHOP-TASK-029 新增的退款部分（取值见 tests/test_refunds.py）。
    "refund_deadline",
    "refund_window_open",
    "refunded_total_sen",
    "refundable_left_sen",
    "fully_refunded",
    "refund_requests",
}

SOURCE_BUCKET = "order_lookup_source"
FAILURE_BUCKET = "order_lookup_failures"

NOT_FOUND = {"detail": "not_found"}
RATE_LIMITED = {"detail": "rate_limited"}
UNAVAILABLE = {"detail": "service_unavailable"}

COMPLETED_BY_GUEST = (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_GUEST)


# ---------------------------------------------------------------------------
# Redis 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：计数值，以及按桶名注入的读、自增连接错误。不模拟过期。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.fail_get: set[str] = set()
        self.fail_incr: set[str] = set()

    @staticmethod
    def bucket_of(key: str) -> str:
        # 键为 acuven_shop:rate_limit:<桶名>:<摘要>。
        return key.split(":")[2]

    def get(self, key: str) -> bytes | None:
        if self.bucket_of(key) in self.fail_get:
            raise redis.exceptions.ConnectionError("connection refused")
        value = self.values.get(key)
        return None if value is None else str(value).encode()

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    def count(self, bucket: str, identifier: str) -> int:
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        return self.values.get(f"acuven_shop:rate_limit:{bucket}:{digest}", 0)

    def buckets(self) -> list[str]:
        return sorted(self.bucket_of(key) for key in self.values)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple[str, str]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key))
        return self

    def execute(self) -> list[Any]:
        for _, key in self.queued:
            if self.store.bucket_of(key) in self.store.fail_incr:
                raise redis.exceptions.ConnectionError("connection refused")
        results: list[Any] = []
        for command, key in self.queued:
            if command == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            else:
                results.append(True)
        return results


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
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(engine: Engine, fake: FakeRedis) -> FastAPI:
    # 连接串显式为空：不覆盖取客户端依赖时即「未配置」。
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    return app


def _browser(app: FastAPI) -> TestClient:
    # 每个客户端是一个浏览器。https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return _browser(app)


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _make_order(
    db: Session,
    status: str = STATUS_SHIPPED,
    *,
    number: str | None = None,
    phone: str = PHONE_E164,
    overdue: bool = False,
) -> _Placed:
    """直接写一张游客订单（订单行、逐件分摊、收货资料与下单事件）并提交。

    待支付订单默认 1 分钟前创建、尚未到期；overdue 为真或其他状态时 1 小时前创建。
    已支付及之后的状态在创建 5 分钟后支付。
    """
    now = _now()
    fresh = status == STATUS_AWAITING_PAYMENT and not overdue
    created = now - (timedelta(minutes=1) if fresh else timedelta(hours=1))
    paid_at = created + timedelta(minutes=5) if status in PAID_STATUSES else None
    order = Order(
        order_number=number or generate_order_number(),
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=_key(),
        request_fingerprint="0" * 64,
        created_at=created,
        payment_expires_at=created + timedelta(minutes=15),
        paid_at=paid_at,
    )
    _add(db, order)
    for line_index, (names, labels, price, quantity) in enumerate(LINES):
        item = OrderItem(
            order_id=order.id,
            line_index=line_index,
            variant_id=None,
            sku=f"SKU-{line_index}",
            product_name_en=names[0],
            product_name_zh=names[1],
            product_name_ms=names[2],
            variant_label_en=labels[0],
            variant_label_zh=labels[1],
            variant_label_ms=labels[2],
            unit_price_sen=price,
            quantity=quantity,
            line_subtotal_sen=price * quantity,
        )
        _add(db, item)
        for unit_index in range(quantity):
            unit = OrderItemUnit(
                order_item_id=item.id,
                unit_index=unit_index,
                original_price_sen=price,
                coupon_discount_sen=0,
                points_discount=0,
                cash_paid_sen=price,
                points_earned=0,
            )
            _add(db, unit)
    recipient = OrderRecipient(
        order_id=order.id,
        name=NAME,
        phone=phone,
        country_code="MY",
        region="MY-10",
        address=ADDRESS,
        postal_code=POSTAL,
    )
    _add(db, recipient)
    placement = OrderEvent(
        order_id=order.id,
        from_status=None,
        to_status=STATUS_AWAITING_PAYMENT,
        actor_type=ACTOR_GUEST,
        created_at=created,
    )
    _add(db, placement)
    placed = _Placed(id=order.id, number=order.order_number)
    db.commit()
    return placed


def _lookup(
    client: TestClient,
    number: str,
    phone: str = PHONE_LOCAL,
    *,
    source: str | None = None,
) -> Any:
    headers = {} if source is None else {"X-Real-IP": source}
    body = {"order_number": number, "phone": phone}
    return client.post(LOOKUP_URL, json=body, headers=headers)


def _looked_up(client: TestClient, placed: _Placed) -> str:
    """查单通过，返回本浏览器的 CSRF 令牌。"""
    response = _lookup(client, placed.number)
    assert response.status_code == 204, response.text
    csrf = csrf_token_for_cookie(client.cookies.get(COOKIE_NAME))
    assert csrf is not None
    return csrf


def _confirm(
    client: TestClient,
    number: str,
    csrf: str | None,
    *,
    key: str | None = None,
) -> Any:
    headers = {"Idempotency-Key": key or _key()}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return client.post(CONFIRM_URL, json={"order_number": number}, headers=headers)


def _status(db: Session, placed: _Placed) -> str:
    db.expire_all()
    return db.scalars(select(Order.status).where(Order.id == placed.id)).one()


def _events(db: Session, placed: _Placed) -> list[tuple[str | None, str, str]]:
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == placed.id)
        .order_by(OrderEvent.id)
    )
    return list(db.execute(stmt).tuples())


def _records(db: Session) -> list[tuple[int, str, str]]:
    stmt = select(
        ReceiptConfirmation.order_id,
        ReceiptConfirmation.idempotency_key,
        ReceiptConfirmation.actor_type,
    ).order_by(ReceiptConfirmation.id)
    return list(db.execute(stmt).tuples())


def _grants(db: Session, placed: _Placed) -> list[str]:
    stmt = select(OrderAccessGrant.scope).where(OrderAccessGrant.order_id == placed.id)
    return list(db.scalars(stmt))


def _expire_grants(db: Session) -> None:
    """把所有授权改为 10 分钟前到期并提交（创建时间同时提前，满足到期晚于创建）。"""
    now = _now()
    created = now - timedelta(minutes=40)
    expired = now - timedelta(minutes=10)
    db.execute(update(OrderAccessGrant).values(created_at=created, expires_at=expired))
    db.commit()


def _issue(db: Session, placed: _Placed, scope: str) -> tuple[str, str]:
    """给一个新浏览器签发该单的授权并提交；返回 cookie 值与 CSRF 令牌。"""
    issued = issue_order_access(db, None, placed.id, scope, _now())
    db.commit()
    assert issued.new_token is not None
    return issued.new_token, issued.csrf_token


def _with_cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def _assert_access_expired(response: Any) -> None:
    """授权不通过的唯一响应：401、固定错误码、no-store、不设 cookie。"""
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "access_expired"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get_list("set-cookie") == []


@contextmanager
def _sql(engine: Engine) -> Iterator[list[str]]:
    """收集期间执行的 SQL 语句。"""
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def _touches_orders(statements: list[str]) -> bool:
    return any(re.search(r"\b(orders|order_recipients)\b", sql) for sql in statements)


def _in_other_session[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """用第二个数据库会话执行一个操作（操作自己提交）。"""
    with Session(engine) as other:
        return action(other)


def _before_first_transition(
    monkeypatch: pytest.MonkeyPatch,
    action: Callable[[], Any],
) -> None:
    """order_lookup 第一次调用迁移判定函数（随后写记录与条件 UPDATE）之前插入一段操作。"""
    real = order_lookup.is_transition_allowed
    done: list[bool] = []

    def wrapper(current: str, target: str, actor: str) -> bool:
        if not done:
            done.append(True)
            action()
        return real(current, target, actor)

    monkeypatch.setattr(order_lookup, "is_transition_allowed", wrapper)


# ---------------------------------------------------------------------------
# 查单：POST /api/orders/lookup
# ---------------------------------------------------------------------------


def test_lookup_issues_lookup_grant(db: Session, client: TestClient) -> None:
    """「查单通过后，仅对该订单在本浏览器保持 30 分钟授权」；授权「以 HttpOnly、Secure、
    SameSite=Lax 的 cookie 交给浏览器」；「不缓存敏感响应」。SHOP-TASK-027 验收：电话正确时
    204 且响应体为空、设置 cookie、库里多一条 lookup 授权；查单不改订单状态与事件。
    """
    placed = _make_order(db)

    response = _lookup(client, placed.number)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    (cookie,) = response.headers.get_list("set-cookie")
    assert cookie.startswith(f"{COOKIE_NAME}=")
    for attribute in ["HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=1800"]:
        assert attribute in cookie
    assert _grants(db, placed) == [SCOPE_LOOKUP]
    assert _status(db, placed) == STATUS_SHIPPED
    assert _events(db, placed) == [(None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST)]


@pytest.mark.parametrize(
    "typed",
    [
        pytest.param("AB10-CD10-EF10-GH10", id="hyphens"),
        pytest.param(" AB10 CD10 EF10 GH10 ", id="spaces"),
        pytest.param("ab10cd10ef10gh10", id="lowercase"),
        pytest.param("ABIOCDL0EFi0GHlo", id="i-l-o"),
    ],
)
def test_lookup_normalizes_order_number(db: Session, client: TestClient, typed: str) -> None:
    """「查单先按高熵订单号定位」。SHOP-TASK-027 验收：订单号去掉空格与连字符、转大写，
    再把 I、L 换成 1、O 换成 0 后照常查到。
    """
    placed = _make_order(db, number="AB10CD10EF10GH10")

    response = _lookup(client, typed)

    assert response.status_code == 204, response.text
    assert _grants(db, placed) == [SCOPE_LOOKUP]


@pytest.mark.parametrize(
    "phone",
    [
        pytest.param("012-345 6789", id="local-with-separators"),
        pytest.param("0123456789", id="local"),
        pytest.param("+60 12-345 6789", id="plus-with-separators"),
        pytest.param("+60123456789", id="e164"),
    ],
)
def test_lookup_accepts_local_and_plus_phone(db: Session, client: TestClient, phone: str) -> None:
    """「注册、查单和订单认领都用相同的 E.164 规范化结果」；「再与该单 OrderRecipient 的规范化
    号码比对」。SHOP-TASK-027 验收：默认地区 MY，以加号开头时以输入为准，本地写法与带加号写法
    都能查到马来西亚号码的订单。
    """
    placed = _make_order(db)

    response = _lookup(client, placed.number, phone)

    assert response.status_code == 204, response.text


@pytest.mark.parametrize(
    "status",
    [
        STATUS_AWAITING_PAYMENT,
        STATUS_PAID,
        STATUS_PACKED,
        STATUS_SHIPPED,
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    ],
)
def test_lookup_any_status(db: Session, client: TestClient, status: str) -> None:
    """「游客凭订单号与电话可随时查单」。SHOP-TASK-027 验收：查单不改订单状态，任何状态的
    订单（含待支付与已取消）都可查。
    """
    placed = _make_order(db, status)

    response = _lookup(client, placed.number)

    assert response.status_code == 204, response.text
    assert _status(db, placed) == status


def test_lookup_failures_are_indistinguishable(
    db: Session, client: TestClient, fake: FakeRedis
) -> None:
    """「查询接口严格限流、防批量枚举」；P08「不存在」「电话不符」统一显示 lookup.not_found。
    SHOP-TASK-027 验收：订单号不存在、格式不合法、电话不符、电话无法规范化四种情形的状态码、
    响应体与响应头完全相同；格式合法的三种给该订单号的失败计数加一，格式不合法的不计。
    """
    placed = _make_order(db)
    unknown = generate_order_number()

    responses = [
        _lookup(client, unknown),
        _lookup(client, "NOT-AN-ORDER!"),
        _lookup(client, placed.number, "+60198765432"),
        _lookup(client, placed.number, "not a phone"),
    ]

    first = responses[0]
    assert first.status_code == 404, first.text
    assert first.json() == NOT_FOUND
    assert first.headers["cache-control"] == "no-store"
    for response in responses:
        assert response.status_code == first.status_code
        assert response.content == first.content
        assert list(response.headers.items()) == list(first.headers.items())
        assert response.headers.get_list("set-cookie") == []
    assert fake.count(FAILURE_BUCKET, unknown) == 1
    assert fake.count(FAILURE_BUCKET, placed.number) == 2
    assert fake.buckets().count(FAILURE_BUCKET) == 2
    assert _grants(db, placed) == []


def test_source_limit_31st_request_is_429(db: Session, client: TestClient) -> None:
    """「查询接口严格限流」。SHOP-TASK-027 验收：按访客来源计数，桶 order_lookup_source，
    10 分钟窗口上限 30 次，成功失败都计；同一来源第 31 次 429 rate_limited，另一来源不受影响。
    """
    placed = _make_order(db)
    for _ in range(15):
        assert _lookup(client, placed.number, source="203.0.113.7").status_code == 204
    for _ in range(15):
        assert _lookup(client, "BAD", source="203.0.113.7").status_code == 404

    blocked = _lookup(client, placed.number, source="203.0.113.7")
    other = _lookup(client, placed.number, source="198.51.100.9")

    assert blocked.status_code == 429, blocked.text
    assert blocked.json() == RATE_LIMITED
    assert blocked.headers["cache-control"] == "no-store"
    assert blocked.headers.get_list("set-cookie") == []
    assert other.status_code == 204, other.text


def test_failure_limit_blocks_order_number(
    db: Session, client: TestClient, fake: FakeRedis
) -> None:
    """「防批量枚举」。SHOP-TASK-027 验收：按规范化后的订单号读失败计数（桶
    order_lookup_failures，1 小时窗口），已达 10 次即 429，电话正确也拒绝；另一订单号不受影响。
    """
    placed = _make_order(db)
    other = _make_order(db)
    for attempt in range(10):
        wrong = _lookup(client, placed.number, "+60198765432", source=f"192.0.2.{attempt}")
        assert wrong.status_code == 404, wrong.text

    blocked = _lookup(client, placed.number, source="192.0.2.200")
    blocked_variant = _lookup(client, placed.number.lower(), source="192.0.2.201")
    unaffected = _lookup(client, other.number, source="192.0.2.202")

    assert fake.count(FAILURE_BUCKET, placed.number) == 10
    assert blocked.status_code == 429, blocked.text
    assert blocked.json() == RATE_LIMITED
    assert blocked_variant.status_code == 429, blocked_variant.text
    assert _grants(db, placed) == []
    assert unaffected.status_code == 204, unaffected.text


@pytest.mark.parametrize("case", ["unconfigured", "source-count-error", "failure-read-error"])
def test_redis_unavailable_is_503_before_order_query(
    app: FastAPI,
    engine: Engine,
    db: Session,
    client: TestClient,
    fake: FakeRedis,
    case: str,
) -> None:
    """「查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝请求」。SHOP-TASK-027 验收：
    Redis 连接串未配置、或替身在来源计数或读取失败计数时抛连接错误，都 503
    service_unavailable，且没有查订单、不签发授权。
    """
    placed = _make_order(db)
    if case == "unconfigured":
        app.dependency_overrides.pop(get_redis_client)
    elif case == "source-count-error":
        fake.fail_incr.add(SOURCE_BUCKET)
    else:
        fake.fail_get.add(FAILURE_BUCKET)

    with _sql(engine) as statements:
        response = _lookup(client, placed.number)

    assert response.status_code == 503, response.text
    assert response.json() == UNAVAILABLE
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get_list("set-cookie") == []
    assert not _touches_orders(statements)
    assert _grants(db, placed) == []


def test_failure_increment_error_is_503_without_grant(
    db: Session, client: TestClient, fake: FakeRedis
) -> None:
    """「查单…依赖 Redis 限流的敏感接口也拒绝请求」。SHOP-TASK-027 验收：替身只在给失败计数
    加一时抛连接错误时，电话不符返回同一个 503 而不是 404，不签发授权；
    电话正确时不需加一，照常 204。
    """
    placed = _make_order(db)
    fake.fail_incr.add(FAILURE_BUCKET)

    wrong = _lookup(client, placed.number, "+60198765432")

    assert wrong.status_code == 503, wrong.text
    assert wrong.json() == UNAVAILABLE
    assert wrong.headers["cache-control"] == "no-store"
    assert wrong.headers.get_list("set-cookie") == []
    assert _grants(db, placed) == []

    right = _lookup(client, placed.number)

    assert right.status_code == 204, right.text


def test_order_number_and_phone_only_in_body(db: Session, client: TestClient) -> None:
    """订单号「不出现在公共索引或分析事件」；P08「订单号与电话只以请求体提交，不进路径或
    查询参数」。SHOP-TASK-027 验收：放在查询参数里的订单号与电话不被读取（请求体缺字段 422）。
    """
    placed = _make_order(db)
    params = {"order_number": placed.number, "phone": PHONE_E164}

    response = client.post(LOOKUP_URL, params=params, json={})

    assert response.status_code == 422, response.text
    assert _grants(db, placed) == []


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_lookup_non_json_is_415(
    db: Session, client: TestClient, fake: FakeRedis, content_type: str | None
) -> None:
    """SHOP-TASK-027 验收：只接受 JSON（否则 415），带 no-store，不回显请求内容，不计数。"""
    placed = _make_order(db)
    headers = {} if content_type is None else {"Content-Type": content_type}
    body = json.dumps({"order_number": placed.number, "phone": PHONE_LOCAL})

    response = client.post(LOOKUP_URL, content=body, headers=headers)

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == "no-store"
    assert placed.number not in response.text
    assert fake.values == {}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json"}, id="json"),
        pytest.param({"Content-Type": "text/plain"}, id="not-json"),
    ],
)
def test_lookup_oversized_body_is_413_first(
    app: FastAPI, db: Session, client: TestClient, headers: dict[str, str]
) -> None:
    """SHOP-TASK-027 验收：请求体上限 8 KB 且先于其他校验（超出 413）——带多余字段、不是 JSON、
    Redis 未配置时都是 413；不回显请求内容，带 no-store。
    """
    placed = _make_order(db)
    app.dependency_overrides.pop(get_redis_client)
    padding = "0123456789" * (MAX_BODY_BYTES // 10)
    body = {"order_number": placed.number, "phone": PHONE_LOCAL, "note": padding}

    response = client.post(LOOKUP_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "0123456789" not in response.text
    assert placed.number not in response.text


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        pytest.param({"email": "secret@example.com"}, ["body", "email"], id="extra-field"),
        pytest.param({"phone": 60123456789}, ["body", "phone"], id="phone-not-string"),
        pytest.param({"drop": "phone"}, ["body", "phone"], id="no-phone"),
    ],
)
def test_lookup_invalid_body_is_422_without_echo(
    db: Session, client: TestClient, fake: FakeRedis, body: dict[str, Any], loc: list[str]
) -> None:
    """「应用日志与监控不记录…订单查询参数」，凭据不写入「错误回显」。SHOP-TASK-027 验收：
    请求体只有订单号与电话两个字符串字段，多出字段 422；每条错误只含位置、类型与固定消息，
    不回显请求内容；带 no-store；请求体不合格时不计数。
    """
    placed = _make_order(db)
    request: dict[str, Any] = {"order_number": placed.number, "phone": PHONE_LOCAL}
    request |= {key: value for key, value in body.items() if key != "drop"}
    if "drop" in body:
        del request[body["drop"]]

    response = client.post(LOOKUP_URL, json=request)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in [placed.number, "secret@example.com", "60123456789", "012-345"]:
        assert value not in response.text
    assert fake.values == {}


# ---------------------------------------------------------------------------
# 查看：GET /api/orders/lookup
# ---------------------------------------------------------------------------


def test_view_returns_fields_and_recipient(db: Session, client: TestClient) -> None:
    """「查询成功后…可见完整姓名和地址」；「不缓存敏感响应」；P09 游客订单不显示
    checkout.summary_coupon、checkout.summary_points 两行。SHOP-TASK-027 验收：订单号、状态、
    创建时间、支付时间、服务器当前时间、订单行快照（含逐件实付金额列表）、商品小计、运费、应付、
    收货资料原文；不含优惠券与积分；另返回由 cookie 重新算出的 CSRF 令牌；no-store。
    """
    placed = _make_order(db, STATUS_SHIPPED)
    _looked_up(client, placed)
    before = datetime.now(UTC)

    response = client.get(LOOKUP_URL)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"orders", "csrf_token"}
    assert body["csrf_token"] == csrf_token_for_cookie(client.cookies.get(COOKIE_NAME))
    (view,) = body["orders"]
    assert set(view) == ORDER_FIELDS
    for absent in ["coupon_discount_sen", "points_redeemed", "points_earned"]:
        assert absent not in view
    db.expire_all()
    order = db.get(Order, placed.id)
    assert order is not None and order.paid_at is not None
    assert view["order_number"] == placed.number
    assert view["status"] == STATUS_SHIPPED
    assert datetime.fromisoformat(view["created_at"]) == order.created_at.replace(tzinfo=UTC)
    assert datetime.fromisoformat(view["paid_at"]) == order.paid_at.replace(tzinfo=UTC)
    assert before <= datetime.fromisoformat(view["server_time"]) <= datetime.now(UTC)
    assert view["lines"] == [
        {
            "line_index": 0,
            "name": "Tee",
            "variant_label": "Colour: Red",
            "quantity": 2,
            "unit_price_sen": 2590,
            "line_subtotal_sen": 5180,
            "unit_cash_paid_sen": [2590, 2590],
            "refundable_quantity": 2,
            "refund_estimates_sen": [2590, 5180],
        },
        {
            "line_index": 1,
            "name": "Mug",
            "variant_label": "",
            "quantity": 1,
            "unit_price_sen": 1500,
            "line_subtotal_sen": 1500,
            "unit_cash_paid_sen": [1500],
            "refundable_quantity": 1,
            "refund_estimates_sen": [1500],
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


def test_view_unpaid_order_has_no_paid_at(db: Session, client: TestClient) -> None:
    """「Order…创建与支付时间」。SHOP-TASK-027 验收：支付时间未支付为空。"""
    placed = _make_order(db, STATUS_AWAITING_PAYMENT)
    _looked_up(client, placed)

    view = client.get(LOOKUP_URL).json()["orders"][0]

    assert (view["status"], view["paid_at"]) == (STATUS_AWAITING_PAYMENT, None)


def test_view_lines_follow_language(db: Session, client: TestClient) -> None:
    """「文案缺少当前语言时回退英文」（快照在下单时已回退）。SHOP-TASK-027 验收：语言参数与
    目录接口相同，订单行快照按请求语言取。
    """
    placed = _make_order(db)
    _looked_up(client, placed)

    zh = client.get(LOOKUP_URL, params={"lang": "zh"}).json()["orders"][0]["lines"]
    ms = client.get(LOOKUP_URL, params={"lang": "ms"}).json()["orders"][0]["lines"]

    assert [(line["name"], line["variant_label"]) for line in zh] == [
        ("T恤", "颜色：红色"),
        ("马克杯", ""),
    ]
    assert [line["name"] for line in ms] == ["Tee", "Mug Kopi"]


def test_view_rejects_unknown_language_without_echo(db: Session, client: TestClient) -> None:
    """SHOP-TASK-027 验收：语言参数不合法时 422，每条错误只含位置、类型与固定消息，不含所给的
    值，且带 no-store。
    """
    placed = _make_order(db)
    _looked_up(client, placed)

    response = client.get(LOOKUP_URL, params={"lang": "klingon"})

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["query", "lang"]]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert "klingon" not in response.text


def test_view_orders_by_grant_expiry_latest_first(db: Session, client: TestClient) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」。SHOP-TASK-027 验收：按授权到期
    时间从晚到早（刚查询的在前）；顺序看授权到期时间。
    """
    first = _make_order(db)
    second = _make_order(db)
    _looked_up(client, first)
    _looked_up(client, second)

    numbers = [view["order_number"] for view in client.get(LOOKUP_URL).json()["orders"]]

    assert numbers == [second.number, first.number]

    later = _now() + timedelta(minutes=40)
    stmt = (
        update(OrderAccessGrant)
        .where(OrderAccessGrant.order_id == first.id)
        .values(expires_at=later)
    )
    db.execute(stmt)
    db.commit()

    numbers = [view["order_number"] for view in client.get(LOOKUP_URL).json()["orders"]]

    assert numbers == [first.number, second.number]


def test_view_cancels_overdue_awaiting_order(db: Session, client: TestClient) -> None:
    """「待支付订单…超时为 demo_cancelled」。SHOP-TASK-027 验收：返回前先对其中待支付的订单
    执行 SHOP-TASK-021 的超时取消函数，过期的待支付订单显示已取消，事件操作者为 system。
    """
    placed = _make_order(db, STATUS_AWAITING_PAYMENT, overdue=True)
    _looked_up(client, placed)

    view = client.get(LOOKUP_URL).json()["orders"][0]

    assert view["status"] == STATUS_CANCELLED
    assert _status(db, placed) == STATUS_CANCELLED
    assert _events(db, placed)[-1] == (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)


@pytest.mark.parametrize(
    "case", ["no-cookie", "malformed", "expired-grant", "guest-checkout-only", "other-browser"]
)
def test_view_without_valid_lookup_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """查单授权「过期须重新查单」；短期凭据「不能用于确认收货、退款或其他订单」。
    SHOP-TASK-027 验收：无 cookie、授权过期、只有 guest_checkout 授权、其他浏览器的 cookie
    都 401 access_expired 且响应相同，不区分订单不存在、授权过期或不属于该浏览器。
    """
    placed = _make_order(db)
    headers: dict[str, str] = {}
    if case == "malformed":
        headers = _with_cookie("not-a-token")
    elif case == "expired-grant":
        _looked_up(client, placed)
        _expire_grants(db)
        headers = _with_cookie(str(client.cookies.get(COOKIE_NAME)))
    elif case == "guest-checkout-only":
        token, _ = _issue(db, placed, SCOPE_GUEST_CHECKOUT)
        headers = _with_cookie(token)
    elif case == "other-browser":
        _looked_up(client, placed)
        headers = _with_cookie(secrets.token_urlsafe(32))

    response = _browser(app).get(LOOKUP_URL, headers=headers)
    baseline = _browser(app).get(LOOKUP_URL)

    _assert_access_expired(response)
    assert response.content == baseline.content


# ---------------------------------------------------------------------------
# 确认收货：POST /api/orders/confirm-receipt
# ---------------------------------------------------------------------------


def test_confirm_completes_shipped_order(db: Session, client: TestClient) -> None:
    """「访客在查单页…确认收货后进入 demo_completed」；「事务内写订单、流水和事件」；
    「确认收货…使用幂等键和数据库唯一约束」。SHOP-TASK-027 验收：成功后 200 与新状态，
    状态、事件（操作者 guest）与确认收货记录各一条；指纹是订单 ID 的 SHA-256 十六进制；no-store。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()

    response = _confirm(client, placed.number, csrf, key=key)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_COMPLETED}
    assert response.headers["cache-control"] == "no-store"
    assert _status(db, placed) == STATUS_COMPLETED
    assert _events(db, placed)[1:] == [COMPLETED_BY_GUEST]
    assert _records(db) == [(placed.id, key, ACTOR_GUEST)]
    record = db.scalars(select(ReceiptConfirmation)).one()
    expected = hashlib.sha256(f'{{"order_id":{placed.id}}}'.encode()).hexdigest()
    assert record.request_fingerprint == expected


def test_confirm_same_key_replays(db: Session, client: TestClient) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-027 验收：同键同请求重放 200 与订单当前状态，
    不重复写记录与事件。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()
    first = _confirm(client, placed.number, csrf, key=key)

    again = _confirm(client, placed.number, csrf, key=key)

    assert again.status_code == 200, again.text
    assert again.json() == first.json() == {"status": STATUS_COMPLETED}
    assert again.headers["cache-control"] == "no-store"
    assert len(_records(db)) == 1
    assert _events(db, placed)[1:] == [COMPLETED_BY_GUEST]


def test_confirm_same_key_other_order_conflicts(db: Session, client: TestClient) -> None:
    """「同键不同内容报冲突」：指纹含订单 ID。SHOP-TASK-027 验收：同键不同订单 409
    idempotency_conflict，另一张订单不变、不写记录。
    """
    first = _make_order(db)
    second = _make_order(db)
    _looked_up(client, first)
    csrf = _looked_up(client, second)
    key = _key()
    assert _confirm(client, first.number, csrf, key=key).status_code == 200

    response = _confirm(client, second.number, csrf, key=key)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "idempotency_conflict"}
    assert response.headers["cache-control"] == "no-store"
    assert _status(db, second) == STATUS_SHIPPED
    assert _records(db) == [(first.id, key, ACTOR_GUEST)]


@pytest.mark.parametrize("case", ["missing", "wrong", "other-browser"])
def test_confirm_without_valid_csrf_is_403(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」。SHOP-TASK-027 验收：缺或错
    CSRF 403 csrf_failed，不改订单、不写记录。
    """
    placed = _make_order(db)
    _looked_up(client, placed)
    csrf: str | None = None
    if case == "wrong":
        csrf = "0" * 64
    elif case == "other-browser":
        csrf = _looked_up(_browser(app), _make_order(db))

    response = _confirm(client, placed.number, csrf)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    assert response.headers["cache-control"] == "no-store"
    assert _status(db, placed) == STATUS_SHIPPED
    assert _records(db) == []


@pytest.mark.parametrize(
    "status",
    [STATUS_AWAITING_PAYMENT, STATUS_PAID, STATUS_PACKED, STATUS_COMPLETED, STATUS_CANCELLED],
)
def test_confirm_other_status_is_409(db: Session, client: TestClient, status: str) -> None:
    """「所有状态迁移校验当前状态」；「已完成的履约状态不可倒退」；P09「确认收货：仅
    demo_shipped 可点」。SHOP-TASK-027 验收：待支付、已支付、已打包、已完成、已取消都 409
    order_not_confirmable 与当前状态，不写记录与事件。
    """
    placed = _make_order(db, status)
    csrf = _looked_up(client, placed)
    events = _events(db, placed)

    response = _confirm(client, placed.number, csrf)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": status}
    assert response.headers["cache-control"] == "no-store"
    assert _status(db, placed) == status
    assert _records(db) == []
    assert _events(db, placed) == events


@pytest.mark.parametrize(
    "case",
    ["no-cookie", "expired-grant", "guest-checkout-only", "unknown-order", "malformed-number"],
)
def test_confirm_without_lookup_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """查单授权「过期须重新查单」；短期凭据「不能用于确认收货」；「服务端校验所属订单、到期
    时间」。SHOP-TASK-027 验收：订单号不存在、授权过期、范围不符都是同一个 401
    access_expired，先于 CSRF 校验，不改订单、不写记录。
    """
    placed = _make_order(db)
    browser = _browser(app)
    number = placed.number
    csrf = "0" * 64
    if case == "expired-grant":
        csrf = _looked_up(client, placed)
        _expire_grants(db)
        browser = client
    elif case == "guest-checkout-only":
        token, csrf = _issue(db, placed, SCOPE_GUEST_CHECKOUT)
        browser.headers.update(_with_cookie(token))
    elif case == "unknown-order":
        csrf = _looked_up(client, placed)
        browser = client
        number = generate_order_number()
    elif case == "malformed-number":
        csrf = _looked_up(client, placed)
        browser = client
        number = placed.number.lower()

    response = _confirm(browser, number, csrf)

    _assert_access_expired(response)
    assert _status(db, placed) == STATUS_SHIPPED
    assert _records(db) == []


def test_grant_for_order_a_cannot_confirm_order_b(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """查单授权「不能替代其他订单的订单号加电话验证」。SHOP-TASK-027 验收：同一会话已授权
    订单 A、未授权订单 B 时，借请求体里 B 的订单号确认收货，返回与无 cookie 相同的 401，
    B 不变。
    """
    order_a = _make_order(db)
    order_b = _make_order(db)
    csrf = _looked_up(client, order_a)

    response = _confirm(client, order_b.number, csrf)
    baseline = _confirm(_browser(app), order_b.number, csrf)

    _assert_access_expired(response)
    _assert_access_expired(baseline)
    assert response.content == baseline.content
    assert _status(db, order_b) == STATUS_SHIPPED
    assert _events(db, order_b) == [(None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST)]
    assert _records(db) == []


@pytest.mark.parametrize("url", [PAY_URL, CANCEL_URL])
def test_lookup_grant_cannot_pay_or_cancel(db: Session, client: TestClient, url: str) -> None:
    """查单授权「只能查看该单、确认收货和申请退款，不能支付或取消」。SHOP-TASK-027 验收：
    lookup 授权调用 SHOP-TASK-021 的支付与取消接口都 401，订单不变。
    """
    placed = _make_order(db, STATUS_AWAITING_PAYMENT)
    csrf = _looked_up(client, placed)
    body: dict[str, str] = {"order_number": placed.number}
    if url == PAY_URL:
        body |= {"method": "card", "result": "success"}
    headers = {"Idempotency-Key": _key(), "X-CSRF-Token": csrf}

    response = client.post(url, json=body, headers=headers)

    _assert_access_expired(response)
    assert _status(db, placed) == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# 确认收货的并发
# ---------------------------------------------------------------------------


def test_two_confirmations_with_different_keys(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「确认收货…使用幂等键和数据库唯一约束」；「所有状态迁移校验当前状态」。
    SHOP-TASK-027 验收：两笔不同幂等键并发确认同一订单，另一会话在本请求写记录前先提交：
    本请求写记录遇唯一约束冲突后回滚，返回 409 order_not_confirmable 与当前状态；
    只有一笔成功，事件与记录各一条。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    other_key = _key()

    def confirm(other: Session) -> None:
        order_lookup.confirm_receipt(other, placed.id, other_key, _now())

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, confirm))

    response = _confirm(client, placed.number, csrf)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": STATUS_COMPLETED}
    assert _status(db, placed) == STATUS_COMPLETED
    assert _records(db) == [(placed.id, other_key, ACTOR_GUEST)]
    assert _events(db, placed)[1:] == [COMPLETED_BY_GUEST]


@pytest.mark.parametrize("same_request", [True, False], ids=["replay", "conflict"])
def test_same_key_insert_race(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    same_request: bool,
) -> None:
    """「相同键相同请求返回原结果；同键不同内容报冲突」。SHOP-TASK-027 验收：同一幂等键并发
    插入，另一会话先提交了同键的确认（同一订单或另一张订单）：本请求写记录遇唯一约束冲突后回滚，
    按指纹重放 200 或返回 409 idempotency_conflict；不出现一单两次完成。
    """
    placed = _make_order(db)
    other_order = placed if same_request else _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()

    def confirm(other: Session) -> None:
        order_lookup.confirm_receipt(other, other_order.id, key, _now())

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, confirm))

    response = _confirm(client, placed.number, csrf, key=key)

    if same_request:
        assert response.status_code == 200, response.text
        assert response.json() == {"status": STATUS_COMPLETED}
        assert _status(db, placed) == STATUS_COMPLETED
    else:
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "idempotency_conflict"}
        assert _status(db, placed) == STATUS_SHIPPED
        assert _events(db, placed)[1:] == []
    assert _records(db) == [(other_order.id, key, ACTOR_GUEST)]
    assert _events(db, other_order)[1:] == [COMPLETED_BY_GUEST]


def test_conditional_update_miss_rolls_back(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「所有状态迁移校验当前状态」。SHOP-TASK-027 验收：条件 UPDATE 未命中时回滚（不留确认
    收货记录），按幂等键查不到即返回 409 order_not_confirmable 与当前状态。另一会话以不写确认
    收货记录的方式（如之后的自动完成）先把订单改为已完成。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)

    def auto_complete(other: Session) -> None:
        other.execute(update(Order).where(Order.id == placed.id).values(status=STATUS_COMPLETED))
        other.add(
            OrderEvent(
                order_id=placed.id,
                from_status=STATUS_SHIPPED,
                to_status=STATUS_COMPLETED,
                actor_type=ACTOR_SYSTEM,
                created_at=_now(),
            )
        )
        other.commit()

    _before_first_transition(monkeypatch, lambda: _in_other_session(engine, auto_complete))

    response = _confirm(client, placed.number, csrf)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": STATUS_COMPLETED}
    assert _records(db) == []
    assert _events(db, placed)[1:] == [(STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)]


# ---------------------------------------------------------------------------
# 确认收货的请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_confirm_non_json_request_is_415(
    db: Session, client: TestClient, content_type: str | None
) -> None:
    """SHOP-TASK-027 验收：只接受 JSON，规则同 SHOP-TASK-020（其他 Content-Type 415），
    带 no-store，不改订单。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    headers = {"Idempotency-Key": _key(), "X-CSRF-Token": csrf}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post(
        CONFIRM_URL, content=json.dumps({"order_number": placed.number}), headers=headers
    )

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == "no-store"
    assert placed.number not in response.text
    assert _status(db, placed) == STATUS_SHIPPED


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json", "Idempotency-Key": "k" * 20}, id="json"),
        pytest.param({"Content-Type": "application/json"}, id="no-key"),
        pytest.param({"Content-Type": "text/plain"}, id="not-json"),
    ],
)
def test_confirm_oversized_body_is_413_first(
    app: FastAPI, db: Session, headers: dict[str, str]
) -> None:
    """SHOP-TASK-027 验收：请求体上限 8 KB，超过先于其他校验 413——带多余字段、缺幂等键、
    不是 JSON 或没有 cookie 的超限请求体都是 413；不回显请求内容，带 no-store。
    """
    placed = _make_order(db)
    padding = "0123456789" * (MAX_BODY_BYTES // 10)
    body = {"order_number": placed.number, "note": padding}

    response = _browser(app).post(CONFIRM_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "0123456789" not in response.text
    assert _status(db, placed) == STATUS_SHIPPED


KEY_LOC = ["header", "Idempotency-Key"]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"key": None}, KEY_LOC, id="missing-key"),
        pytest.param({"key": "a" * 15}, KEY_LOC, id="short-key"),
        pytest.param({"body": {"reason": "never arrived"}}, ["body", "reason"], id="extra"),
        pytest.param({"body": {"order_number": 12345}}, ["body", "order_number"], id="number"),
        pytest.param({"drop": True}, ["body", "order_number"], id="no-order-number"),
    ],
)
def test_confirm_invalid_request_is_422_without_echo(
    db: Session, client: TestClient, change: dict[str, Any], loc: list[str]
) -> None:
    """凭据不写入「错误回显」。SHOP-TASK-027 验收：请求体只有订单号，多出字段 422；须带
    Idempotency-Key（规则同 SHOP-TASK-020）；422 每条错误只含位置、类型与固定消息，不回显
    请求内容，带 no-store；不改订单。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    body: dict[str, Any] = {"order_number": placed.number} | change.get("body", {})
    if change.get("drop"):
        del body["order_number"]
    headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    key = change.get("key", _key())
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post(CONFIRM_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in [placed.number, "never arrived", "12345", "a" * 15]:
        assert value not in response.text
    assert _status(db, placed) == STATUS_SHIPPED
    assert _records(db) == []


# ---------------------------------------------------------------------------
# 确认收货记录模型
# ---------------------------------------------------------------------------


def _confirmation(order_id: int, **values: Any) -> ReceiptConfirmation:
    row: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": _key(),
        "request_fingerprint": "a" * 64,
        "actor_type": ACTOR_GUEST,
        "created_at": _now(),
    }
    return ReceiptConfirmation(**(row | values))


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        pytest.param({"actor_type": ACTOR_SYSTEM}, "actor_type_valid", id="system-actor"),
        pytest.param({"actor_type": "admin"}, "actor_type_valid", id="admin-actor"),
        pytest.param(
            {"request_fingerprint": "a" * 63},
            "request_fingerprint_length",
            id="short-fingerprint",
        ),
        pytest.param({"idempotency_key": ""}, "idempotency_key_not_empty", id="empty-key"),
    ],
)
def test_model_check_constraints(db: Session, values: dict[str, Any], constraint: str) -> None:
    """「数据模型」PaymentAttempt / OrderEvent 一行：「操作者类别」。SHOP-TASK-027 验收：
    操作者类别只允许 guest 与 member（自动完成的 system 不写这里），请求指纹长 64，幂等键非空。
    """
    placed = _make_order(db)
    db.add(_confirmation(placed.id, **values))

    with pytest.raises(IntegrityError, match=rf"CHECK constraint failed: \w*{constraint}"):
        db.flush()


def test_model_accepts_member_actor(db: Session) -> None:
    """「会员…在「我的订单」对这些订单确认收货」。SHOP-TASK-027 验收：
    操作者类别允许 member。
    """
    placed = _make_order(db)
    db.add(_confirmation(placed.id, actor_type="member"))
    db.flush()


@pytest.mark.parametrize("column", ["order_id", "idempotency_key"])
def test_model_unique_constraints(db: Session, column: str) -> None:
    """「确认收货…使用幂等键和数据库唯一约束」。SHOP-TASK-027 验收：所属订单全表唯一（一张
    订单只记一次手动确认），幂等键全表唯一。
    """
    first = _make_order(db)
    second = _make_order(db)
    existing = _confirmation(first.id)
    db.add(existing)
    db.flush()
    if column == "order_id":
        duplicate = _confirmation(first.id)
    else:
        duplicate = _confirmation(second.id, idempotency_key=existing.idempotency_key)
    db.add(duplicate)

    with pytest.raises(IntegrityError, match=f"UNIQUE constraint failed: .*{column}"):
        db.flush()


def test_model_foreign_key(db: Session) -> None:
    """设计没有直接原句。SHOP-TASK-027 验收：确认收货记录的所属订单是外键（RESTRICT），
    拒绝不存在的订单。
    """
    db.add(_confirmation(999_999))

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        db.flush()


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」。SHOP-TASK-027 验收：
    三个接口不写日志。
    """
    placed = _make_order(db)
    caplog.set_level(logging.DEBUG)

    _lookup(client, placed.number, "+60198765432")
    csrf = _looked_up(client, placed)
    client.get(LOOKUP_URL)
    _confirm(client, placed.number, csrf)

    assert [record for record in caplog.records if record.name.startswith("app")] == []

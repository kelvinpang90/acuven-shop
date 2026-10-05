"""订单查询与确认收货：POST /api/orders/lookup、GET /api/orders/lookup、
POST /api/orders/confirm-receipt，以及确认收货记录模型 ReceiptConfirmation。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「权限与资料保护」第 2、6、7 条；
「失败、并发与重试」第 1、4 条；「订单与退款状态」第 2 条；「数据模型」的
PaymentAttempt / OrderEvent 一行。每条测试（参数化的测试是每个用例）的文档字符串
写明它守住的设计原句；没有直接原句的，写明是 SHOP-TASK-027 验收里的哪一条。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）；订单按模型直接写入，
不经下单接口。接口的会话依赖换成每个请求一个绑定同一内存库的会话，SHOP-TASK-026 的
取客户端依赖换成本文件的内存替身 FakeRedis（只实现 GET 与事务管道的 INCR、EXPIRE，
可按键前缀让读或自增抛错）。内存库经 StaticPool 只有一个连接，pysqlite 在第一条写语句前
才开始事务，所以在一个会话还只做过查询时，用第二个会话执行并提交另一个操作，就能模拟
并发请求先提交；交错点是 order_lookup 模块在带条件的 UPDATE 之前调用迁移判定函数时。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text, update
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

NUMBER = "K7Q29MXA1B0C3D4E"
OTHER_NUMBER = "Z9Y8X7W6V5T4S3R2"
UNKNOWN_NUMBER = "ZZZZZZZZZZZZZZZZ"

PHONE_E164 = "+60123456789"
PHONE_LOCAL = "012-345 6789"
WRONG_PHONE = "+60123456780"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"
SOURCE = "203.0.113.5"

SOURCE_PREFIX = "acuven_shop:rate_limit:order_lookup_source:"
FAILURE_PREFIX = "acuven_shop:rate_limit:order_lookup_failures:"

# 默认订单：Tee 2 件各 2590 仙、Mug 1 件 1500 仙；逐件券额 100、100、50（共 250），
# 运费 800。下单时的券与逐件实付快照只为说明视图取的是快照，接口不返回券额。
SUBTOTAL = 2 * 2590 + 1500
COUPON = 250
SHIPPING = 800
TOTAL = SUBTOTAL - COUPON + SHIPPING
ITEMS = [
    {
        "names": ("Tee", "T恤", "Tee"),
        "labels": ("Colour: Red", "颜色：红色", "Colour: Red"),
        "price": 2590,
        "coupons": [100, 100],
    },
    {
        "names": ("Mug", "马克杯", "Mug Kopi"),
        "labels": ("", "", ""),
        "price": 1500,
        "coupons": [50],
    },
]
EXPECTED_LINES_EN = [
    {
        "name": "Tee",
        "variant_label": "Colour: Red",
        "quantity": 2,
        "unit_price_sen": 2590,
        "line_subtotal_sen": 5180,
        "unit_cash_paid_sen": [2490, 2490],
    },
    {
        "name": "Mug",
        "variant_label": "",
        "quantity": 1,
        "unit_price_sen": 1500,
        "line_subtotal_sen": 1500,
        "unit_cash_paid_sen": [1450],
    },
]
VIEW_FIELDS = {
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
}
COMPLETED_BY_GUEST = (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_GUEST)
ALL_STATUSES = [
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
    STATUS_PACKED,
    STATUS_SHIPPED,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
]


# ---------------------------------------------------------------------------
# Redis 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：计数、首次设置的窗口秒数，以及按键前缀注入的故障。不模拟过期。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.windows: dict[str, int] = {}
        self.fail_get_prefix: str | None = None
        self.fail_incr_prefix: str | None = None
        self.error: Exception = redis.exceptions.ConnectionError("Error connecting to x:6379")

    def get(self, key: str) -> bytes | None:
        if self.fail_get_prefix is not None and key.startswith(self.fail_get_prefix):
            raise self.error
        value = self.values.get(key)
        return None if value is None else str(value).encode()

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key, seconds, nx))
        return self

    def execute(self) -> list[Any]:
        batch, self.queued = self.queued, []
        prefix = self.store.fail_incr_prefix
        if prefix is not None and any(command[1].startswith(prefix) for command in batch):
            raise self.store.error
        results: list[Any] = []
        for command in batch:
            key = command[1]
            if command[0] == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            else:
                if not (command[3] and key in self.store.windows):
                    self.store.windows[key] = command[2]
                results.append(True)
        return results


def _digest(identifier: str) -> str:
    return hashlib.sha256(identifier.encode()).hexdigest()


def _source_key(source: str) -> str:
    return SOURCE_PREFIX + _digest(source)


def _failure_key(number: str) -> str:
    return FAILURE_PREFIX + _digest(number)


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
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(engine: Engine, fake_redis: FakeRedis) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    return app


@pytest.fixture
def statements(engine: Engine) -> Iterator[list[str]]:
    """引擎执行过的 SQL 语句；测试在要观察的请求之前清空。"""
    captured: list[str] = []

    def capture(_conn, _cursor, statement, *_rest) -> None:
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    yield captured
    event.remove(engine, "before_cursor_execute", capture)


def _browser(app: FastAPI) -> TestClient:
    # 每个客户端是一个浏览器。https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return _browser(app)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _make_order(
    db: Session,
    status: str = STATUS_SHIPPED,
    *,
    number: str | None = None,
    overdue: bool = False,
) -> Order:
    """按模型写一张游客订单（订单、两行订单行、逐件快照、收货资料）并提交。

    待支付订单默认未到期；overdue 为真时支付到期时间在 5 分钟前。
    已模拟支付的状态有支付时间，其余没有。
    """
    now = _now()
    if status == STATUS_AWAITING_PAYMENT:
        created = now - timedelta(minutes=20 if overdue else 1)
    else:
        created = now - timedelta(days=2)
    order = Order(
        order_number=number or generate_order_number(),
        status=status,
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
        paid_at=created + timedelta(minutes=1) if status in PAID_STATUSES else None,
    )
    db.add(order)
    db.flush()
    for line_index, spec in enumerate(ITEMS):
        quantity = len(spec["coupons"])
        item = OrderItem(
            order_id=order.id,
            line_index=line_index,
            variant_id=None,
            sku=f"SKU-{line_index}",
            product_name_en=spec["names"][0],
            product_name_zh=spec["names"][1],
            product_name_ms=spec["names"][2],
            variant_label_en=spec["labels"][0],
            variant_label_zh=spec["labels"][1],
            variant_label_ms=spec["labels"][2],
            unit_price_sen=spec["price"],
            quantity=quantity,
            line_subtotal_sen=spec["price"] * quantity,
        )
        db.add(item)
        db.flush()
        for unit_index, coupon in enumerate(spec["coupons"]):
            unit = OrderItemUnit(
                order_item_id=item.id,
                unit_index=unit_index,
                original_price_sen=spec["price"],
                coupon_discount_sen=coupon,
                points_discount=0,
                cash_paid_sen=spec["price"] - coupon,
                points_earned=0,
            )
            db.add(unit)
    recipient = OrderRecipient(
        order_id=order.id,
        name=NAME,
        phone=PHONE_E164,
        country_code="MY",
        region="MY-10",
        address=ADDRESS,
        postal_code=POSTAL,
    )
    db.add(recipient)
    db.commit()
    return order


def _order(db: Session, number: str) -> Order:
    db.expire_all()
    return db.scalars(select(Order).where(Order.order_number == number)).one()


def _events(db: Session, order_id: int) -> list[tuple[str | None, str, str]]:
    stmt = (
        select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return list(db.execute(stmt).tuples())


def _confirmations(db: Session, order_id: int | None = None) -> list[tuple[int, str, str, str]]:
    columns = (
        ReceiptConfirmation.order_id,
        ReceiptConfirmation.idempotency_key,
        ReceiptConfirmation.request_fingerprint,
        ReceiptConfirmation.actor_type,
    )
    stmt = select(*columns).order_by(ReceiptConfirmation.id)
    if order_id is not None:
        stmt = stmt.where(ReceiptConfirmation.order_id == order_id)
    return list(db.execute(stmt).tuples())


def _grant_count(db: Session, scope: str = SCOPE_LOOKUP) -> int:
    scoped = OrderAccessGrant.scope == scope
    return db.scalar(select(func.count()).select_from(OrderAccessGrant).where(scoped))


def _fingerprint(order_id: int) -> str:
    # 另算，不取实现里的函数：{"order_id": N} 键排序、无空白的 JSON 的 SHA-256。
    return hashlib.sha256(f'{{"order_id":{order_id}}}'.encode()).hexdigest()


def _lookup(
    client: TestClient,
    number: str,
    phone: str = PHONE_LOCAL,
    *,
    source: str = SOURCE,
) -> Any:
    body = {"order_number": number, "phone": phone}
    return client.post(LOOKUP_URL, json=body, headers={"X-Real-IP": source})


def _csrf(client: TestClient) -> str:
    token = csrf_token_for_cookie(client.cookies.get(COOKIE_NAME))
    return token or ""


def _confirm(
    client: TestClient,
    number: str,
    *,
    key: str | None = None,
    csrf: str | None = None,
) -> Any:
    headers = {
        "Idempotency-Key": key or _key(),
        "X-CSRF-Token": _csrf(client) if csrf is None else csrf,
    }
    return client.post(CONFIRM_URL, json={"order_number": number}, headers=headers)


def _expire_grants(db: Session) -> None:
    """把所有授权改为 10 分钟前到期并提交（创建时间同时提前，满足到期晚于创建）。"""
    now = _now()
    stmt = update(OrderAccessGrant).values(
        created_at=now - timedelta(minutes=40),
        expires_at=now - timedelta(minutes=10),
    )
    db.execute(stmt)
    db.commit()


def _issued_cookie(db: Session, order_id: int, scope: str) -> tuple[str, str]:
    """给一个新浏览器签发该单该范围的授权并提交；返回 cookie 值与 CSRF 令牌。"""
    issued = issue_order_access(db, None, order_id, scope, _now())
    db.commit()
    assert issued.new_token is not None
    return issued.new_token, issued.csrf_token


def _with_cookie(app: FastAPI, token: str) -> TestClient:
    browser = _browser(app)
    browser.headers.update({"Cookie": f"{COOKIE_NAME}={token}"})
    return browser


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _assert_not_found(response: Any) -> None:
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "not_found"}
    _assert_no_store(response)
    assert response.headers.get_list("set-cookie") == []


def _assert_access_expired(response: Any) -> None:
    """授权不通过的唯一响应：401、固定错误码、no-store、不设 cookie。"""
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "access_expired"}
    _assert_no_store(response)
    assert response.headers.get_list("set-cookie") == []


def _same_response(response: Any, baseline: Any) -> None:
    assert response.status_code == baseline.status_code
    assert response.content == baseline.content
    assert dict(response.headers) == dict(baseline.headers)


def _before_transition(monkeypatch: pytest.MonkeyPatch, action: Callable[[], Any]) -> None:
    """order_lookup 模块第一次调用迁移判定函数（随后就是带条件的 UPDATE）之前插入一段操作。"""
    real = order_lookup.is_transition_allowed
    done: list[bool] = []

    def wrapper(current: str, target: str, actor: str) -> bool:
        if not done:
            done.append(True)
            action()
        return real(current, target, actor)

    monkeypatch.setattr(order_lookup, "is_transition_allowed", wrapper)


def _in_other_session[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """用第二个数据库会话执行一个操作（操作自己提交）。"""
    with Session(engine) as other:
        return action(other)


# ---------------------------------------------------------------------------
# 查单：POST /api/orders/lookup
# ---------------------------------------------------------------------------


def test_lookup_success_issues_lookup_grant(
    db: Session, client: TestClient, fake_redis: FakeRedis
) -> None:
    """「查单通过后，仅对该订单在本浏览器保持 30 分钟授权」；授权「以 HttpOnly、
    Secure、SameSite=Lax 的 cookie 交给浏览器」；「不缓存敏感响应」。
    SHOP-TASK-027 验收：电话正确时 204 且响应体为空、设置 cookie、库里多一条
    lookup 授权；来源计数窗口 10 分钟；查单不改订单状态。
    """
    order = _make_order(db)
    assert _grant_count(db) == 0

    response = _lookup(client, order.order_number)

    assert response.status_code == 204, response.text
    assert response.content == b""
    _assert_no_store(response)
    (cookie,) = response.headers.get_list("set-cookie")
    assert cookie.startswith(f"{COOKIE_NAME}=")
    attributes = cookie.lower()
    for attribute in ["httponly", "secure", "samesite=lax", "max-age=1800", "path=/"]:
        assert attribute in attributes
    assert _grant_count(db) == 1
    assert _grant_count(db, SCOPE_GUEST_CHECKOUT) == 0
    grant = db.scalars(select(OrderAccessGrant)).one()
    assert grant.order_id == order.id
    assert _order(db, order.order_number).status == STATUS_SHIPPED
    assert _events(db, order.id) == []
    assert fake_redis.values == {_source_key(SOURCE): 1}
    assert fake_redis.windows == {_source_key(SOURCE): 600}


@pytest.mark.parametrize(
    "typed",
    [
        "k7q29mxa1b0c3d4e",
        "K7Q2-9MXA-1B0C-3D4E",
        "K7Q2 9MXA 1B0C 3D4E",
        " k7q2-9mxa ib0c-3d4e ",
        "K7Q2-9MXA-LBOC-3D4E",
        "k7q2-9mxa-lboc-3d4e",
    ],
    ids=["lower", "hyphens", "spaces", "lower-i", "upper-l-o", "lower-l-o"],
)
def test_lookup_normalizes_order_number(db: Session, client: TestClient, typed: str) -> None:
    """「查单先按高熵订单号定位」。SHOP-TASK-027 验收：订单号去掉空格与连字符、
    转大写，再把 I、L 换成 1、O 换成 0 后照常查到。
    """
    _make_order(db, number=NUMBER)

    response = _lookup(client, typed)

    assert response.status_code == 204, response.text
    assert _grant_count(db) == 1


@pytest.mark.parametrize(
    "phone",
    ["012-345 6789", "0123456789", "+60 12-345 6789", "+60123456789"],
    ids=["local-dashes", "local", "plus-spaced", "plus"],
)
def test_lookup_accepts_local_and_plus_phone(db: Session, client: TestClient, phone: str) -> None:
    """「注册、查单和订单认领都用相同的 E.164 规范化结果」；「再与该单
    OrderRecipient 的规范化号码比对」。SHOP-TASK-027 验收：默认地区 MY，以加号开头
    时以输入为准；本地写法与带加号写法都能查到马来西亚号码的订单。
    """
    order = _make_order(db)

    response = _lookup(client, order.order_number, phone)

    assert response.status_code == 204, response.text


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_lookup_any_status_without_changing_it(
    db: Session, client: TestClient, status: str
) -> None:
    """「资料保留」：「游客凭订单号与电话可随时查单」。SHOP-TASK-027 验收：查单不改
    订单状态，任何状态的订单（含待支付与已取消）都可查。
    """
    order = _make_order(db, status)

    response = _lookup(client, order.order_number)

    assert response.status_code == 204, response.text
    assert _order(db, order.order_number).status == status
    assert _events(db, order.id) == []


def test_not_found_cases_are_identical(
    db: Session, client: TestClient, fake_redis: FakeRedis
) -> None:
    """「查询接口严格限流、防批量枚举」；UX P08「不泄露订单是否存在」。
    SHOP-TASK-027 验收：订单号格式不合法、订单不存在、电话无法规范化与电话不符
    一律同一个 404 not_found，响应体与响应头相同；后三种把该订单号的失败计数
    加一，窗口 1 小时；格式不合法的不计失败；都不签发授权。
    """
    _make_order(db, number=NUMBER)

    responses = [
        _lookup(client, "K7Q2"),
        _lookup(client, UNKNOWN_NUMBER),
        _lookup(client, NUMBER, "not-a-phone"),
        _lookup(client, NUMBER, WRONG_PHONE),
    ]

    for response in responses:
        _assert_not_found(response)
        _same_response(response, responses[0])
    assert _grant_count(db) == 0
    failures = {
        key: value for key, value in fake_redis.values.items() if key.startswith(FAILURE_PREFIX)
    }
    assert failures == {_failure_key(UNKNOWN_NUMBER): 1, _failure_key(NUMBER): 2}
    assert fake_redis.windows[_failure_key(NUMBER)] == 3600
    for response in responses:
        assert NUMBER not in response.text
        assert PHONE_E164 not in response.text


def test_source_limit_rejects_the_31st(
    db: Session, client: TestClient, fake_redis: FakeRedis
) -> None:
    """「查询接口严格限流」；Kelvin 2026-10-01 的决定（docs/HANDOFF.md）：同一访客
    来源 10 分钟内最多查询 30 次（成功失败都计）。SHOP-TASK-027 验收：同一来源
    第 31 次 429 rate_limited（电话正确也拒绝），另一来源不受影响。
    """
    order = _make_order(db)
    assert _lookup(client, order.order_number).status_code == 204
    for _ in range(29):
        _assert_not_found(_lookup(client, "BAD"))

    response = _lookup(client, order.order_number)

    assert response.status_code == 429, response.text
    assert response.json() == {"detail": "rate_limited"}
    _assert_no_store(response)
    assert response.headers.get_list("set-cookie") == []
    assert fake_redis.values[_source_key(SOURCE)] == 31
    assert fake_redis.windows[_source_key(SOURCE)] == 600
    assert _lookup(client, order.order_number, source="198.51.100.7").status_code == 204


def test_failures_lock_the_order_number(
    db: Session, client: TestClient, fake_redis: FakeRedis
) -> None:
    """「防批量枚举」；Kelvin 2026-10-01 的决定：同一订单号 1 小时内失败满 10 次后
    拒绝查询（电话正确也拒绝）。SHOP-TASK-027 验收：同一订单号失败 10 次后用正确
    电话也 429，而另一订单号不受影响；失败按规范化后的订单号计。
    """
    _make_order(db, number=NUMBER)
    _make_order(db, number=OTHER_NUMBER)
    spellings = [NUMBER, "k7q2-9mxa-1b0c-3d4e"]
    for attempt in range(10):
        number = spellings[attempt % 2]
        _assert_not_found(_lookup(client, number, WRONG_PHONE, source=f"192.0.2.{attempt}"))

    locked = _lookup(client, NUMBER, source="192.0.2.100")
    other = _lookup(client, OTHER_NUMBER, source="192.0.2.101")

    assert locked.status_code == 429, locked.text
    assert locked.json() == {"detail": "rate_limited"}
    _assert_no_store(locked)
    assert other.status_code == 204, other.text
    assert fake_redis.values[_failure_key(NUMBER)] == 10
    assert _failure_key(OTHER_NUMBER) not in fake_redis.values
    assert _grant_count(db) == 1


@pytest.mark.parametrize("case", ["unconfigured", "source-count-error", "failure-read-error"])
def test_redis_unavailable_before_order_query(
    app: FastAPI,
    db: Session,
    client: TestClient,
    fake_redis: FakeRedis,
    statements: list[str],
    case: str,
) -> None:
    """「查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝请求」。SHOP-TASK-027 验收：
    Redis 连接串未配置、或来源计数与读取失败计数时连不上，返回 503
    service_unavailable，不查订单、不签发授权。
    """
    order = _make_order(db)
    if case == "unconfigured":
        del app.dependency_overrides[get_redis_client]
    elif case == "source-count-error":
        fake_redis.fail_incr_prefix = SOURCE_PREFIX
    else:
        fake_redis.fail_get_prefix = FAILURE_PREFIX
    statements.clear()

    response = _lookup(client, order.order_number)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "service_unavailable"}
    _assert_no_store(response)
    assert response.headers.get_list("set-cookie") == []
    assert not any("FROM orders" in statement for statement in statements)
    assert _grant_count(db) == 0


@pytest.mark.parametrize(
    ("number", "phone"),
    [(NUMBER, WRONG_PHONE), (NUMBER, "not-a-phone"), (UNKNOWN_NUMBER, PHONE_LOCAL)],
    ids=["wrong-phone", "bad-phone", "unknown-order"],
)
def test_failure_increment_error_is_503(
    db: Session, client: TestClient, fake_redis: FakeRedis, number: str, phone: str
) -> None:
    """「Redis 或 MySQL 不可用时…查单…拒绝请求」。SHOP-TASK-027 验收：替身只在
    失败计数加一时抛连接错误，比对失败返回 503 而不是 404，不签发授权；电话正确的
    查询不需要加一，照常 204。
    """
    _make_order(db, number=NUMBER)
    fake_redis.fail_incr_prefix = FAILURE_PREFIX

    response = _lookup(client, number, phone)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "service_unavailable"}
    _assert_no_store(response)
    assert response.headers.get_list("set-cookie") == []
    assert _grant_count(db) == 0
    assert _lookup(client, NUMBER).status_code == 204


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_lookup_non_json_is_415(
    db: Session, client: TestClient, fake_redis: FakeRedis, content_type: str | None
) -> None:
    """SHOP-TASK-027 验收：只接受 JSON（否则 415）；所有响应带 no-store；
    格式错误先于来源计数。
    """
    order = _make_order(db)
    headers = {} if content_type is None else {"Content-Type": content_type}
    body = json.dumps({"order_number": order.order_number, "phone": PHONE_LOCAL})

    response = client.post(LOOKUP_URL, content=body, headers=headers)

    assert response.status_code == 415, response.text
    _assert_no_store(response)
    assert fake_redis.values == {}
    assert _grant_count(db) == 0


@pytest.mark.parametrize(
    "headers",
    [{"Content-Type": "application/json"}, {"Content-Type": "text/plain"}],
    ids=["json", "not-json"],
)
def test_lookup_oversized_body_is_413_first(
    app: FastAPI, db: Session, fake_redis: FakeRedis, headers: dict[str, str]
) -> None:
    """SHOP-TASK-027 验收：请求体上限 8 KB 且先于其他校验（超出 413）——带多余
    字段、不是 JSON 或 Redis 未配置都先 413；响应不回显请求内容，带 no-store。
    """
    order = _make_order(db)
    del app.dependency_overrides[get_redis_client]
    padding = "Secretname" * (MAX_BODY_BYTES // 10)
    body = {"order_number": order.order_number, "phone": PHONE_LOCAL, "name": padding}

    response = _browser(app).post(LOOKUP_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert "Secretname" not in response.text
    assert order.order_number not in response.text
    _assert_no_store(response)
    assert fake_redis.values == {}


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        pytest.param({"email": "zubaidah@example.com"}, ["body", "email"], id="extra-field"),
        pytest.param({"name": NAME}, ["body", "name"], id="extra-name"),
        pytest.param({"drop": "phone"}, ["body", "phone"], id="no-phone"),
        pytest.param({"drop": "order_number"}, ["body", "order_number"], id="no-order-number"),
        pytest.param({"phone": 60123456789}, ["body", "phone"], id="phone-number-type"),
        pytest.param({"order_number": ["K7Q2"]}, ["body", "order_number"], id="order-list"),
    ],
)
def test_lookup_invalid_body_is_422_without_echo(
    db: Session,
    client: TestClient,
    fake_redis: FakeRedis,
    body: dict[str, Any],
    loc: list[str],
) -> None:
    """「订单号不出现在公共索引或分析事件」；第 7 条日志与监控不记录「订单查询参数」。
    SHOP-TASK-027 验收：请求体只有订单号与电话两个字符串字段，多出字段 422；
    422 响应不回显请求内容；校验先于来源计数。
    """
    order = _make_order(db)
    payload: dict[str, Any] = {"order_number": order.order_number, "phone": PHONE_LOCAL}
    payload |= {key: value for key, value in body.items() if key != "drop"}
    if "drop" in body:
        del payload[body["drop"]]

    response = client.post(LOOKUP_URL, json=payload)

    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for secret in [order.order_number, "zubaidah@example.com", NAME, "60123456789", "012-345"]:
        assert secret not in response.text
    assert fake_redis.values == {}
    assert _grant_count(db) == 0


# ---------------------------------------------------------------------------
# 查看：GET /api/orders/lookup
# ---------------------------------------------------------------------------


def test_view_returns_fields_and_recipient(db: Session, client: TestClient) -> None:
    """「查询成功后…可见完整姓名和地址」；查单授权「只能查看该单…」；「不缓存敏感
    响应」；UX P09 游客订单不显示优惠与积分两行。SHOP-TASK-027 验收：每张订单含
    订单号、状态、创建时间、支付时间、服务器当前时间、按语言的订单行快照（含逐件
    实付列表）、商品小计、运费、应付、收货资料原文；不含优惠券与积分；另返回由
    cookie 重新算出的 CSRF 令牌；no-store。
    """
    order = _make_order(db, STATUS_SHIPPED)
    assert _lookup(client, order.order_number).status_code == 204
    before = datetime.now(UTC)

    response = client.get(LOOKUP_URL)

    assert response.status_code == 200, response.text
    _assert_no_store(response)
    body = response.json()
    assert set(body) == {"orders", "csrf_token"}
    assert body["csrf_token"] == csrf_token_for_cookie(client.cookies.get(COOKIE_NAME))
    (view,) = body["orders"]
    assert set(view) == VIEW_FIELDS
    stored = _order(db, order.order_number)
    assert view["order_number"] == stored.order_number
    assert view["status"] == STATUS_SHIPPED
    assert datetime.fromisoformat(view["created_at"]) == stored.created_at.replace(tzinfo=UTC)
    assert stored.paid_at is not None
    assert datetime.fromisoformat(view["paid_at"]) == stored.paid_at.replace(tzinfo=UTC)
    assert before <= datetime.fromisoformat(view["server_time"]) <= datetime.now(UTC)
    assert view["lines"] == EXPECTED_LINES_EN
    assert (view["subtotal_sen"], view["shipping_fee_sen"]) == (SUBTOTAL, SHIPPING)
    assert view["total_sen"] == TOTAL
    assert view["recipient"] == {
        "name": NAME,
        "phone": PHONE_E164,
        "country_code": "MY",
        "region": "MY-10",
        "address": ADDRESS,
        "postal_code": POSTAL,
    }
    for word in ["coupon", "points"]:
        assert word not in response.text


@pytest.mark.parametrize(
    ("lang", "tee", "mug"),
    [
        pytest.param("zh", ("T恤", "颜色：红色"), "马克杯", id="zh"),
        pytest.param("ms", ("Tee", "Colour: Red"), "Mug Kopi", id="ms"),
    ],
)
def test_view_lines_follow_language(
    db: Session, client: TestClient, lang: str, tee: tuple[str, str], mug: str
) -> None:
    """「文案缺少当前语言时回退英文」（快照在下单时已回退）。SHOP-TASK-027 验收：
    语言参数与目录接口相同，订单行快照按请求语言取。
    """
    order = _make_order(db)
    assert _lookup(client, order.order_number).status_code == 204

    response = client.get(LOOKUP_URL, params={"lang": lang})

    assert response.status_code == 200, response.text
    lines = response.json()["orders"][0]["lines"]
    assert (lines[0]["name"], lines[0]["variant_label"]) == tee
    assert lines[1]["name"] == mug


def test_view_rejects_unknown_language_without_echo(db: Session, client: TestClient) -> None:
    """SHOP-TASK-027 验收：语言参数与目录接口相同（只接受 en、zh、ms）；422 响应
    不回显请求内容；所有响应带 no-store。
    """
    order = _make_order(db)
    assert _lookup(client, order.order_number).status_code == 204

    response = client.get(LOOKUP_URL, params={"lang": "klingon"})

    assert response.status_code == 422, response.text
    assert "klingon" not in response.text
    _assert_no_store(response)


def test_view_unpaid_order_has_no_paid_at(db: Session, client: TestClient) -> None:
    """「数据模型」Order「创建与支付时间」。SHOP-TASK-027 验收：支付时间未支付为空；
    待支付订单也可查看。
    """
    order = _make_order(db, STATUS_AWAITING_PAYMENT)
    assert _lookup(client, order.order_number).status_code == 204

    view = client.get(LOOKUP_URL).json()["orders"][0]

    assert view["status"] == STATUS_AWAITING_PAYMENT
    assert view["paid_at"] is None


def test_view_orders_by_grant_expiry_latest_first(db: Session, client: TestClient) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」。SHOP-TASK-027 验收：
    按授权到期时间从晚到早（刚查询的在前）；顺序看授权到期时间。
    """
    first = _make_order(db, number=NUMBER)
    _make_order(db, number=OTHER_NUMBER)
    assert _lookup(client, NUMBER).status_code == 204
    assert _lookup(client, OTHER_NUMBER).status_code == 204

    numbers = [view["order_number"] for view in client.get(LOOKUP_URL).json()["orders"]]
    assert numbers == [OTHER_NUMBER, NUMBER]

    stmt = (
        update(OrderAccessGrant)
        .where(OrderAccessGrant.order_id == first.id)
        .values(expires_at=_now() + timedelta(minutes=40))
    )
    db.execute(stmt)
    db.commit()

    numbers = [view["order_number"] for view in client.get(LOOKUP_URL).json()["orders"]]
    assert numbers == [NUMBER, OTHER_NUMBER]


def test_view_cancels_overdue_pending_order(db: Session, client: TestClient) -> None:
    """「计价、优惠、积分与库存」第 6 条「15 分钟未支付自动取消」。SHOP-TASK-027
    验收：返回前先对其中待支付的订单执行超时取消函数，过期的待支付订单显示已取消，
    事件操作者为 system。
    """
    order = _make_order(db, STATUS_AWAITING_PAYMENT, overdue=True)
    assert _lookup(client, order.order_number).status_code == 204

    response = client.get(LOOKUP_URL)

    assert response.status_code == 200, response.text
    assert response.json()["orders"][0]["status"] == STATUS_CANCELLED
    assert _order(db, order.order_number).status == STATUS_CANCELLED
    assert _events(db, order.id) == [(STATUS_AWAITING_PAYMENT, STATUS_CANCELLED, ACTOR_SYSTEM)]


@pytest.mark.parametrize(
    "case",
    [
        "malformed",
        "unknown-token",
        "expired-grant",
        "guest-checkout-only",
        "other-browser",
    ],
)
def test_view_without_lookup_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """查单授权「过期须重新查单」；短期凭据「不能用于确认收货、退款或其他订单」；
    「服务端校验所属订单、到期时间」。SHOP-TASK-027 验收：无 cookie、授权过期、只有
    guest_checkout 授权、其他浏览器的 cookie 都 401 access_expired 且响应相同。
    other-browser 是另一个浏览器的 cookie，它只有自己那张订单的 guest_checkout 授权。
    """
    order = _make_order(db, number=NUMBER)
    assert _lookup(client, NUMBER).status_code == 204
    baseline = _browser(app).get(LOOKUP_URL)
    if case == "malformed":
        browser = _with_cookie(app, "not-a-token")
    elif case == "unknown-token":
        browser = _with_cookie(app, "A" * 43)
    elif case == "expired-grant":
        _expire_grants(db)
        browser = client
    elif case == "guest-checkout-only":
        token, _ = _issued_cookie(db, order.id, SCOPE_GUEST_CHECKOUT)
        browser = _with_cookie(app, token)
    else:
        theirs = _make_order(db, STATUS_AWAITING_PAYMENT)
        token, _ = _issued_cookie(db, theirs.id, SCOPE_GUEST_CHECKOUT)
        browser = _with_cookie(app, token)

    response = browser.get(LOOKUP_URL)

    _assert_access_expired(baseline)
    _assert_access_expired(response)
    _same_response(response, baseline)
    assert NUMBER not in response.text


# ---------------------------------------------------------------------------
# 确认收货：POST /api/orders/confirm-receipt
# ---------------------------------------------------------------------------


def test_confirm_completes_shipped_order(db: Session, client: TestClient) -> None:
    """「访客在查单页…确认收货后进入 demo_completed」；「事务内写订单、流水和事件」；
    「事件不写收货资料原文」。SHOP-TASK-027 验收：成功后 200 与新状态，状态、事件
    （操作者 guest）与确认收货记录各一条；指纹由订单 ID 算出；no-store。
    """
    order = _make_order(db, STATUS_SHIPPED)
    assert _lookup(client, order.order_number).status_code == 204
    key = _key()

    response = _confirm(client, order.order_number, key=key)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_COMPLETED}
    _assert_no_store(response)
    assert _order(db, order.order_number).status == STATUS_COMPLETED
    assert _events(db, order.id) == [COMPLETED_BY_GUEST]
    assert _confirmations(db) == [(order.id, key, _fingerprint(order.id), ACTOR_GUEST)]
    assert client.get(LOOKUP_URL).json()["orders"][0]["status"] == STATUS_COMPLETED


def test_confirm_same_key_replays(db: Session, client: TestClient) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-027 验收：同键同请求重放 200 与订单
    当前状态，不重复写入记录与事件。
    """
    order = _make_order(db, STATUS_SHIPPED)
    assert _lookup(client, order.order_number).status_code == 204
    key = _key()
    first = _confirm(client, order.order_number, key=key)
    assert first.status_code == 200, first.text

    again = _confirm(client, order.order_number, key=key)

    assert again.status_code == 200, again.text
    assert again.json() == first.json() == {"status": STATUS_COMPLETED}
    _assert_no_store(again)
    assert _events(db, order.id) == [COMPLETED_BY_GUEST]
    assert len(_confirmations(db)) == 1


def test_confirm_same_key_other_order_conflicts(db: Session, client: TestClient) -> None:
    """「同键不同内容报冲突」。SHOP-TASK-027 验收：同键不同订单 409
    idempotency_conflict，另一张订单不变。
    """
    first = _make_order(db, STATUS_SHIPPED, number=NUMBER)
    second = _make_order(db, STATUS_SHIPPED, number=OTHER_NUMBER)
    assert _lookup(client, NUMBER).status_code == 204
    assert _lookup(client, OTHER_NUMBER).status_code == 204
    key = _key()
    assert _confirm(client, NUMBER, key=key).status_code == 200

    response = _confirm(client, OTHER_NUMBER, key=key)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "idempotency_conflict"}
    _assert_no_store(response)
    assert _order(db, OTHER_NUMBER).status == STATUS_SHIPPED
    assert _events(db, second.id) == []
    assert _confirmations(db, second.id) == []
    assert len(_confirmations(db, first.id)) == 1


@pytest.mark.parametrize("case", ["missing", "wrong", "other-browser"])
def test_confirm_without_valid_csrf_is_403(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」。SHOP-TASK-027 验收：
    缺或错 CSRF 403 csrf_failed，不写记录、不改订单。
    """
    order = _make_order(db, STATUS_SHIPPED)
    assert _lookup(client, order.order_number).status_code == 204
    headers = {"Idempotency-Key": _key()}
    if case == "wrong":
        headers["X-CSRF-Token"] = "0" * 64
    elif case == "other-browser":
        other = _browser(app)
        assert _lookup(other, order.order_number, source="198.51.100.9").status_code == 204
        headers["X-CSRF-Token"] = _csrf(other)

    response = client.post(CONFIRM_URL, json={"order_number": order.order_number}, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    _assert_no_store(response)
    assert _order(db, order.order_number).status == STATUS_SHIPPED
    assert _events(db, order.id) == []
    assert _confirmations(db) == []


@pytest.mark.parametrize(
    "status",
    [STATUS_AWAITING_PAYMENT, STATUS_PAID, STATUS_PACKED, STATUS_COMPLETED, STATUS_CANCELLED],
)
def test_confirm_not_shipped_is_409(db: Session, client: TestClient, status: str) -> None:
    """「管理员依次推进…→ demo_shipped；访客在查单页…确认收货后进入 demo_completed」；
    「所有状态迁移校验当前状态」；「已完成的履约状态不可倒退」。SHOP-TASK-027 验收：
    待支付、已支付、已打包、已完成、已取消都 409 order_not_confirmable 与当前状态，
    不写记录与事件。
    """
    order = _make_order(db, status)
    assert _lookup(client, order.order_number).status_code == 204

    response = _confirm(client, order.order_number)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": status}
    _assert_no_store(response)
    assert _order(db, order.order_number).status == status
    assert _events(db, order.id) == []
    assert _confirmations(db) == []


@pytest.mark.parametrize(
    "case",
    [
        "expired-grant",
        "guest-checkout-grant",
        "other-browser",
        "unknown-order",
        "malformed-order",
    ],
)
def test_confirm_without_lookup_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """查单授权「过期须重新查单」；短期凭据「不能用于确认收货」；「服务端校验所属订单、
    到期时间」。SHOP-TASK-027 验收：订单号不存在、授权过期、不属于该会话或范围不符
    一律与无 cookie 相同的 401 access_expired，先于 CSRF，不改订单。
    """
    order = _make_order(db, STATUS_SHIPPED, number=NUMBER)
    baseline = _confirm(_browser(app), NUMBER, csrf="0" * 64)
    browser, number, csrf = client, NUMBER, None
    if case == "expired-grant":
        assert _lookup(client, NUMBER).status_code == 204
        _expire_grants(db)
    elif case == "guest-checkout-grant":
        token, csrf = _issued_cookie(db, order.id, SCOPE_GUEST_CHECKOUT)
        browser = _with_cookie(app, token)
    elif case == "other-browser":
        assert _lookup(client, NUMBER).status_code == 204
        _make_order(db, STATUS_SHIPPED, number=OTHER_NUMBER)
        browser = _browser(app)
        assert _lookup(browser, OTHER_NUMBER, source="198.51.100.9").status_code == 204
    else:
        assert _lookup(client, NUMBER).status_code == 204
        number = UNKNOWN_NUMBER if case == "unknown-order" else "not-an-order"

    response = _confirm(browser, number, csrf=csrf)

    _assert_access_expired(baseline)
    _assert_access_expired(response)
    _same_response(response, baseline)
    assert _order(db, NUMBER).status == STATUS_SHIPPED
    assert _events(db, order.id) == []
    assert _confirmations(db) == []


def test_grant_for_order_a_cannot_confirm_order_b(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """查单授权「仅对该订单」，「不能替代其他订单的订单号加电话验证」。SHOP-TASK-027
    验收：同一会话已授权订单 A、未授权订单 B 时对 B 确认收货返回与无 cookie 相同的
    401，且 B 不变。
    """
    _make_order(db, STATUS_SHIPPED, number=NUMBER)
    order_b = _make_order(db, STATUS_SHIPPED, number=OTHER_NUMBER)
    assert _lookup(client, NUMBER).status_code == 204

    response = _confirm(client, OTHER_NUMBER)
    baseline = _confirm(_browser(app), OTHER_NUMBER, csrf=_csrf(client))

    _assert_access_expired(response)
    _same_response(response, baseline)
    assert _order(db, OTHER_NUMBER).status == STATUS_SHIPPED
    assert _events(db, order_b.id) == []
    assert _confirmations(db) == []


@pytest.mark.parametrize("operation", ["pay", "cancel"])
def test_lookup_grant_cannot_pay_or_cancel(db: Session, client: TestClient, operation: str) -> None:
    """查单授权「只能查看该单、确认收货和申请退款，不能支付或取消」。SHOP-TASK-027
    验收：lookup 授权调用 SHOP-TASK-021 的支付与取消接口都 401。
    """
    order = _make_order(db, STATUS_AWAITING_PAYMENT)
    assert _lookup(client, order.order_number).status_code == 204
    headers = {"X-CSRF-Token": _csrf(client)}
    if operation == "pay":
        headers["Idempotency-Key"] = _key()
        body = {"order_number": order.order_number, "method": "card", "result": "success"}
        response = client.post(PAY_URL, json=body, headers=headers)
    else:
        response = client.post(
            CANCEL_URL, json={"order_number": order.order_number}, headers=headers
        )

    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "access_expired"}
    stored = _order(db, order.order_number)
    assert (stored.status, stored.paid_at) == (STATUS_AWAITING_PAYMENT, None)
    assert _events(db, order.id) == []


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------


def test_two_keys_confirm_at_once(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「所有状态迁移校验当前状态」；「确认收货…使用幂等键和数据库唯一约束」。
    SHOP-TASK-027 验收：两笔不同幂等键并发确认同一订单时只有一笔成功——另一会话在
    条件 UPDATE 之前提交，后到的 UPDATE 未命中，回滚后 409 order_not_confirmable 与
    当前状态；事件与记录各一条。
    """
    order = _make_order(db, STATUS_SHIPPED)
    order_id = order.id
    assert _lookup(client, order.order_number).status_code == 204
    other_key = _key()

    def confirm(other: Session) -> None:
        order_lookup.confirm_receipt(other, order_id, other_key, _now())

    _before_transition(monkeypatch, lambda: _in_other_session(engine, confirm))

    response = _confirm(client, order.order_number)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": STATUS_COMPLETED}
    assert _order(db, order.order_number).status == STATUS_COMPLETED
    assert _events(db, order_id) == [COMPLETED_BY_GUEST]
    assert _confirmations(db) == [(order_id, other_key, _fingerprint(order_id), ACTOR_GUEST)]


def test_same_key_committed_first_replays(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-027 验收：同键的请求先提交，后到的
    条件 UPDATE 未命中，回滚后按幂等键查到同指纹的记录，返回原结果（200 与当前
    状态）；不出现一单两次完成。
    """
    order = _make_order(db, STATUS_SHIPPED)
    order_id = order.id
    assert _lookup(client, order.order_number).status_code == 204
    key = _key()

    def confirm(other: Session) -> None:
        order_lookup.confirm_receipt(other, order_id, key, _now())

    _before_transition(monkeypatch, lambda: _in_other_session(engine, confirm))

    response = _confirm(client, order.order_number, key=key)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_COMPLETED}
    assert _events(db, order_id) == [COMPLETED_BY_GUEST]
    assert len(_confirmations(db)) == 1


@pytest.mark.parametrize("case", ["same-key-other-order", "same-key-same-order", "other-key"])
def test_unique_conflict_on_insert_rolls_back(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """「确认收货…使用幂等键和数据库唯一约束。相同键相同请求返回原结果；同键不同内容
    报冲突」。SHOP-TASK-027 验收：插入确认收货记录遇唯一约束冲突时回滚，再按幂等键
    重新查询：同键同指纹返回原结果，同键不同指纹 409 idempotency_conflict，否则 409
    order_not_confirmable 与当前状态；回滚后订单不变、不写事件。

    另一会话在条件 UPDATE 之前提交，但不改本单状态，所以本单的 UPDATE 命中、插入撞上
    唯一约束：same-key-other-order 是同键已确认另一张订单（幂等键冲突）；
    same-key-same-order 与 other-key 直接写入本单的记录（同指纹的幂等键冲突、订单 ID
    冲突），只为走到插入冲突这条路径——真实并发里同一订单的后到者在 UPDATE 就未命中。
    """
    order = _make_order(db, STATUS_SHIPPED, number=NUMBER)
    order_id = order.id
    other_order = _make_order(db, STATUS_SHIPPED, number=OTHER_NUMBER)
    other_id = other_order.id
    assert _lookup(client, NUMBER).status_code == 204
    key = _key()
    recorded_key = _key() if case == "other-key" else key

    def interfere(other: Session) -> None:
        if case == "same-key-other-order":
            order_lookup.confirm_receipt(other, other_id, key, _now())
            return
        row = ReceiptConfirmation(
            order_id=order_id,
            idempotency_key=recorded_key,
            request_fingerprint=_fingerprint(order_id),
            actor_type=ACTOR_GUEST,
            created_at=_now(),
        )
        other.add(row)
        other.commit()

    _before_transition(monkeypatch, lambda: _in_other_session(engine, interfere))

    response = _confirm(client, NUMBER, key=key)

    if case == "same-key-other-order":
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "idempotency_conflict"}
        assert _order(db, OTHER_NUMBER).status == STATUS_COMPLETED
    elif case == "same-key-same-order":
        assert response.status_code == 200, response.text
        assert response.json() == {"status": STATUS_SHIPPED}
    else:
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "order_not_confirmable", "status": STATUS_SHIPPED}
    assert _order(db, NUMBER).status == STATUS_SHIPPED
    assert _events(db, order_id) == []
    assert len(_confirmations(db)) == 1


# ---------------------------------------------------------------------------
# 请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_confirm_non_json_is_415(db: Session, client: TestClient, content_type: str | None) -> None:
    """SHOP-TASK-027 验收：确认收货只接受 JSON（规则同 SHOP-TASK-020，否则 415），
    带 no-store，不改订单。
    """
    order = _make_order(db, STATUS_SHIPPED)
    assert _lookup(client, order.order_number).status_code == 204
    headers = {"Idempotency-Key": _key(), "X-CSRF-Token": _csrf(client)}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post(
        CONFIRM_URL, content=json.dumps({"order_number": order.order_number}), headers=headers
    )

    assert response.status_code == 415, response.text
    _assert_no_store(response)
    assert _order(db, order.order_number).status == STATUS_SHIPPED


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
    """SHOP-TASK-027 验收：请求体上限 8 KB，规则同 SHOP-TASK-020，超过先于其他校验
    413——带多余字段、缺幂等键、不是 JSON 或没有 cookie 都先 413；不回显请求内容。
    """
    order = _make_order(db, STATUS_SHIPPED)
    padding = "Secretname" * (MAX_BODY_BYTES // 10)
    body = {"order_number": order.order_number, "note": padding}

    response = _browser(app).post(CONFIRM_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert "Secretname" not in response.text
    _assert_no_store(response)
    assert _order(db, order.order_number).status == STATUS_SHIPPED


KEY_LOC = ["header", "Idempotency-Key"]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"key": None}, KEY_LOC, id="missing-key"),
        pytest.param({"key": "a" * 15}, KEY_LOC, id="short-key"),
        pytest.param({"key": "a" * 65}, KEY_LOC, id="long-key"),
        pytest.param({"drop": True}, ["body", "order_number"], id="no-order-number"),
        pytest.param({"extra": {"phone": PHONE_E164}}, ["body", "phone"], id="extra-phone"),
        pytest.param({"extra": {"note": "Hiddenlane"}}, ["body", "note"], id="extra-note"),
        pytest.param({"number": 12345}, ["body", "order_number"], id="number-type"),
    ],
)
def test_confirm_invalid_request_is_422_without_echo(
    app: FastAPI, db: Session, change: dict[str, Any], loc: list[str]
) -> None:
    """SHOP-TASK-027 验收：请求体只有订单号，多出字段 422；须带 Idempotency-Key
    （规则同 SHOP-TASK-020）；422 响应不回显请求内容；请求体校验先于授权（没有
    cookie 也是 422）；不改订单。
    """
    order = _make_order(db, STATUS_SHIPPED)
    body: dict[str, Any] = {"order_number": change.get("number", order.order_number)}
    body |= change.get("extra", {})
    if change.get("drop"):
        del body["order_number"]
    headers = {"Content-Type": "application/json"}
    key = change.get("key", _key())
    if key is not None:
        headers["Idempotency-Key"] = key

    response = _browser(app).post(CONFIRM_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for secret in [order.order_number, PHONE_E164, "Hiddenlane", "12345", "a" * 15]:
        assert secret not in response.text
    assert _order(db, order.order_number).status == STATUS_SHIPPED


# ---------------------------------------------------------------------------
# 确认收货记录模型
# ---------------------------------------------------------------------------


def _record(order_id: int, **overrides: Any) -> ReceiptConfirmation:
    values: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": _key(),
        "request_fingerprint": _fingerprint(order_id),
        "actor_type": ACTOR_GUEST,
        "created_at": _now(),
    }
    values |= overrides
    return ReceiptConfirmation(**values)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"actor_type": "admin"}, "actor_type_valid"),
        ({"actor_type": "system"}, "actor_type_valid"),
        ({"idempotency_key": ""}, "idempotency_key_not_empty"),
        ({"request_fingerprint": "a" * 63}, "request_fingerprint_length"),
    ],
    ids=["admin-actor", "system-actor", "empty-key", "short-fingerprint"],
)
def test_receipt_confirmation_check_constraints_reject(
    db: Session, overrides: dict[str, Any], constraint: str
) -> None:
    """「模拟发货满 7 天自动确认收货」由系统完成、不写确认收货记录。SHOP-TASK-027
    验收：操作者类别只允许 guest 与 member；幂等键非空；请求指纹 64 位。
    """
    order = _make_order(db)
    name = f"ck_receipt_confirmations_{constraint}"

    db.add(_record(order.id, **overrides))
    with pytest.raises(IntegrityError, match=f"CHECK constraint failed: {name}"):
        db.flush()
    db.rollback()


@pytest.mark.parametrize("column", ["order_id", "idempotency_key"])
def test_receipt_confirmation_unique_constraints_reject(db: Session, column: str) -> None:
    """「确认收货…使用幂等键和数据库唯一约束」。SHOP-TASK-027 验收：所属订单全表唯一
    （一张订单只记一次手动确认）；幂等键全表唯一。
    """
    order = _make_order(db, number=NUMBER)
    other = _make_order(db, number=OTHER_NUMBER)
    first = _record(order.id)
    db.add(first)
    db.commit()
    if column == "order_id":
        duplicate = _record(order.id)
    else:
        duplicate = _record(other.id, idempotency_key=first.idempotency_key)

    db.add(duplicate)
    with pytest.raises(IntegrityError, match=f"UNIQUE constraint failed: .*{column}"):
        db.flush()
    db.rollback()


def test_receipt_confirmation_foreign_key_rejects(db: Session) -> None:
    """「数据模型」订单与其记录长期保存。SHOP-TASK-027 验收：所属订单外键
    （RESTRICT），不存在的订单被拒。
    """
    db.add(_record(999_999))
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        db.flush()
    db.rollback()


def test_receipt_confirmation_stores_no_personal_data() -> None:
    """「事件不写收货资料原文」；SHOP-TASK-027 验收：确认收货记录不存任何个人资料，
    只有所属订单、幂等键、请求指纹、操作者类别与创建时间。
    """
    columns = set(ReceiptConfirmation.__table__.c.keys())
    expected = {"id", "order_id", "idempotency_key", "request_fingerprint", "actor_type"}
    assert columns == expected | {"created_at"}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」；「不得把…完整
    手机号写进日志」。SHOP-TASK-027 验收：不写日志。
    """
    order = _make_order(db, STATUS_SHIPPED)
    caplog.set_level(logging.DEBUG)

    _lookup(client, order.order_number, WRONG_PHONE)
    _lookup(client, order.order_number)
    client.get(LOOKUP_URL)
    _confirm(client, order.order_number)

    assert [record for record in caplog.records if record.name.startswith("app")] == []

"""计价与游客下单两个公开接口的按来源限流（SHOP-TASK-043）。

依据 docs/HANDOFF.md 记录的 Kelvin 2026-10-06 决定：「计价接口与游客下单接口按访客来源限流，
计价每个来源 10 分钟 300 次、下单每个来源 1 小时 60 次，超过返回 429；Redis 未配置或不可用时
这两个接口放行（未配置时不记日志，连不上、超时或出错时记一条警告；不同于查单的拒绝，演示站
没有真实交易，不让结账因 Redis 停摆）」。每条测试（参数化的是每个用例）的文档字符串写明它守住
的是哪一项决定；决定里没有的细节（计数位置、响应头等），写明是 SHOP-TASK-043 验收的约定。

用 SQLite 内存库按模型建表，接口的会话依赖换成每个请求一个绑定同一内存库的会话。新增的可选
取客户端依赖 get_optional_redis_client 换成本文件的内存替身 FakeRedis（只实现事务管道的 INCR
与 EXPIRE NX，带可拨动的时钟，可注入 redis 库的连接或超时错误），不连真 Redis。访客来源用
X-Real-IP 请求头区分。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
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
    OrderAccessSession,
    Product,
    ProductVariant,
    ShippingRate,
)
from app.services.rate_limit import (
    RateLimitUnavailable,
    get_optional_redis_client,
    get_redis_client,
)

QUOTE_URL = "/api/checkout/quote"
ORDER_URL = "/api/orders/guest"
MAX_BODY_BYTES = 8 * 1024

QUOTE_BUCKET = "checkout_quote_source"
ORDER_BUCKET = "guest_order_source"
QUOTE_LIMIT = 300
QUOTE_WINDOW = 10 * 60
ORDER_LIMIT = 60

SOURCE = "203.0.113.5"
OTHER_SOURCE = "203.0.113.6"

SECRET_SKU = "SECRET-SKU-7731"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
SECRET_URL = "redis://:hunter2@secret-host.internal:6379/3"

RATE_LIMITED = {"detail": "rate_limited"}
STOCK = 100


# ---------------------------------------------------------------------------
# Redis 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：计数值与过期时刻（按 self.now 秒计），可注入 execute 时抛出的错误。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, int] = {}
        self.expires: dict[str, float] = {}
        self.error: Exception | None = None

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    def purge(self, key: str) -> None:
        if key in self.expires and self.expires[key] <= self.now:
            del self.values[key]
            del self.expires[key]

    def count(self, bucket: str, identifier: str) -> int:
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        key = f"acuven_shop:rate_limit:{bucket}:{digest}"
        self.purge(key)
        return self.values.get(key, 0)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple[str, str, int]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key, 0))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        assert nx
        self.queued.append(("expire", key, seconds))
        return self

    def execute(self) -> list[Any]:
        if self.store.error is not None:
            raise self.store.error
        results: list[Any] = []
        for command, key, seconds in self.queued:
            self.store.purge(key)
            if command == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            elif key not in self.store.expires:
                self.store.expires[key] = self.store.now + seconds
                results.append(True)
            else:
                results.append(False)
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
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(engine: Engine, fake: FakeRedis) -> FastAPI:
    # 连接串显式为空：不覆盖可选依赖时即「未配置」。
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_optional_redis_client] = lambda: fake
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：下单接口的 cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


def _unconfigured(app: FastAPI) -> None:
    """可选依赖返回空，与连接串未配置时相同。"""
    app.dependency_overrides[get_optional_redis_client] = lambda: None


def _catalog(db: Session) -> None:
    """一件已发布、无规格的商品 MUG（1500 仙，库存 100）与 MY-10 的运费行，写完即提交。"""
    category = Category(slug="goods", name_en="Goods", is_active=True)
    db.add(category)
    db.flush()
    mug = Product(
        category_id=category.id,
        slug="mug",
        name_en="Mug",
        description_en="A mug.",
        is_active=True,
    )
    db.add(mug)
    db.flush()
    db.add(
        ProductVariant(
            product_id=mug.id,
            sku="MUG",
            price_sen=1500,
            daily_initial_stock=STOCK,
            available_stock=STOCK,
            is_active=True,
        )
    )
    db.add(ShippingRate(zone_type="my_state", zone_code="MY-10", fee_sen=800, version=1))
    db.commit()


def _headers(source: str = SOURCE, **extra: str) -> dict[str, str]:
    return {"X-Real-IP": source, **extra}


def _quote(client: TestClient, source: str = SOURCE) -> Any:
    body = {"lines": [{"sku": SECRET_SKU, "quantity": 1}]}
    return client.post(QUOTE_URL, json=body, headers=_headers(source))


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _order_payload() -> dict[str, Any]:
    return {
        "lines": [{"sku": "MUG", "quantity": 1}],
        "phone": "012-345 6789",
        "phone_region": "MY",
        "name": NAME,
        "address": ADDRESS,
        "postal_code": "50000",
        "country_code": "MY",
        "state_code": "MY-10",
    }


def _order(client: TestClient, key: str, source: str = SOURCE) -> Any:
    headers = _headers(source, **{"Idempotency-Key": key})
    return client.post(ORDER_URL, json=_order_payload(), headers=headers)


def _count(db: Session, model: type) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


def _stock(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(ProductVariant.available_stock).where(ProductVariant.sku == "MUG"))


def _app_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")]


# ---------------------------------------------------------------------------
# 计价接口
# ---------------------------------------------------------------------------


def test_quote_allows_300th_and_rejects_301st(client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「计价每个来源 10 分钟 300 次……超过返回 429」；
    验收：429 响应体为 {"detail":"rate_limited"}，不回显请求内容。"""
    for _ in range(QUOTE_LIMIT):
        assert _quote(client).status_code == 200

    response = _quote(client)

    assert response.status_code == 429
    assert response.json() == RATE_LIMITED
    assert SECRET_SKU not in response.text
    assert fake.count(QUOTE_BUCKET, SOURCE) == QUOTE_LIMIT + 1


def test_quote_limit_is_per_source(client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「按访客来源限流」：一个来源超限不影响另一来源。"""
    for _ in range(QUOTE_LIMIT + 1):
        _quote(client)
    assert _quote(client).status_code == 429

    assert _quote(client, OTHER_SOURCE).status_code == 200
    assert fake.count(QUOTE_BUCKET, OTHER_SOURCE) == 1


def test_quote_limit_recovers_after_window(client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「计价每个来源 10 分钟 300 次」：窗口从第一次计数算起，过后恢复。"""
    for _ in range(QUOTE_LIMIT):
        _quote(client)
    fake.now = QUOTE_WINDOW - 1
    assert _quote(client).status_code == 429

    fake.now = QUOTE_WINDOW
    assert _quote(client).status_code == 200
    assert fake.count(QUOTE_BUCKET, SOURCE) == 1


def test_quote_429_headers_match_existing_errors(client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「超过返回 429」；验收：429 的响应头与该接口现有的错误响应相同。"""
    too_large = client.post(
        QUOTE_URL,
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers=_headers(**{"Content-Type": "application/json"}),
    )
    for _ in range(QUOTE_LIMIT):
        _quote(client)

    response = _quote(client)

    assert too_large.status_code == 413
    assert response.status_code == 429
    assert set(response.headers) == set(too_large.headers)
    assert "cache-control" not in response.headers


def test_quote_413_comes_before_counting(client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「计价……按访客来源限流」的计数位置（验收：413 之后计数）：
    过大的请求体 413 且不计数，已超限的来源也仍是 413。"""
    oversized = b"x" * (MAX_BODY_BYTES + 1)
    headers = _headers(**{"Content-Type": "application/json"})

    assert client.post(QUOTE_URL, content=oversized, headers=headers).status_code == 413
    assert fake.count(QUOTE_BUCKET, SOURCE) == 0

    for _ in range(QUOTE_LIMIT + 1):
        _quote(client)
    assert client.post(QUOTE_URL, content=oversized, headers=headers).status_code == 413
    assert fake.count(QUOTE_BUCKET, SOURCE) == QUOTE_LIMIT + 1


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"{not json", "application/json"),
        (b'{"lines": [], "extra": 1}', "application/json"),
        (b"hello", "text/plain"),
    ],
    ids=["broken-json", "invalid-fields", "not-json"],
)
def test_quote_invalid_body_is_counted_then_same_422(
    app: FastAPI, client: TestClient, fake: FakeRedis, content: bytes, content_type: str
) -> None:
    """守住 Kelvin 2026-10-06「计价……按访客来源限流」（验收：成功失败都计，JSON 解析与字段校验
    之前计数，非 JSON 仍是现有的 422、不新增 415）：计数一次后返回与未配置 Redis 时相同的 422；
    已超限时同样的请求是 429。"""
    headers = _headers(**{"Content-Type": content_type})

    response = client.post(QUOTE_URL, content=content, headers=headers)

    assert response.status_code == 422
    assert fake.count(QUOTE_BUCKET, SOURCE) == 1
    _unconfigured(app)
    baseline = client.post(QUOTE_URL, content=content, headers=headers)
    assert baseline.status_code == 422
    assert response.json() == baseline.json()
    assert set(response.headers) == set(baseline.headers)

    app.dependency_overrides[get_optional_redis_client] = lambda: fake
    for _ in range(QUOTE_LIMIT):
        _quote(client)
    limited = client.post(QUOTE_URL, content=content, headers=headers)
    assert limited.status_code == 429
    assert limited.json() == RATE_LIMITED


# ---------------------------------------------------------------------------
# 游客下单接口
# ---------------------------------------------------------------------------


def test_order_allows_60th_and_rejects_61st_without_creating(
    db: Session, client: TestClient, fake: FakeRedis
) -> None:
    """守住 Kelvin 2026-10-06「下单每个来源 1 小时 60 次，超过返回 429」；验收：幂等重放也计，
    429 不读写订单、库存与授权，响应体 {"detail":"rate_limited"}、不回显请求内容。"""
    _catalog(db)
    key = _key()
    assert _order(client, key).status_code == 201
    for _ in range(ORDER_LIMIT - 1):
        assert _order(client, key).status_code == 200
    assert fake.count(ORDER_BUCKET, SOURCE) == ORDER_LIMIT
    orders = _count(db, Order)
    grants = _count(db, OrderAccessGrant)
    sessions = _count(db, OrderAccessSession)
    stock = _stock(db)

    new_key = _key()
    response = _order(client, new_key)

    assert response.status_code == 429
    assert response.json() == RATE_LIMITED
    for secret in (NAME, ADDRESS, "345 6789", new_key):
        assert secret not in response.text
    assert response.headers.get_list("set-cookie") == []
    assert _count(db, Order) == orders == 1
    assert _count(db, OrderAccessGrant) == grants
    assert _count(db, OrderAccessSession) == sessions
    assert _stock(db) == stock == STOCK - 1
    # 已超限后同键重放也是 429。
    assert _order(client, key).status_code == 429
    assert fake.count(ORDER_BUCKET, SOURCE) == ORDER_LIMIT + 2


def test_order_limit_is_per_source(db: Session, client: TestClient, fake: FakeRedis) -> None:
    """守住 Kelvin 2026-10-06「按访客来源限流」：一个来源超限不影响另一来源下单。"""
    _catalog(db)
    key = _key()
    for _ in range(ORDER_LIMIT + 1):
        _order(client, key)
    assert _order(client, key).status_code == 429

    assert _order(client, _key(), OTHER_SOURCE).status_code == 201
    assert fake.count(ORDER_BUCKET, OTHER_SOURCE) == 1


def test_order_429_headers_match_existing_errors(
    db: Session, client: TestClient, fake: FakeRedis
) -> None:
    """守住 Kelvin 2026-10-06「超过返回 429」；验收：响应头与下单接口现有的错误相同，
    现有错误不带 no-store，429 也不另加。"""
    _catalog(db)
    not_json = client.post(
        ORDER_URL,
        content=b"{}",
        headers=_headers(**{"Content-Type": "text/plain", "Idempotency-Key": _key()}),
    )
    key = _key()
    for _ in range(ORDER_LIMIT):
        _order(client, key)

    response = _order(client, key)

    assert not_json.status_code == 415
    assert response.status_code == 429
    assert set(response.headers) == set(not_json.headers)
    assert "cache-control" not in response.headers


@pytest.mark.parametrize(
    ("content", "content_type", "status"),
    [
        (b"x" * (MAX_BODY_BYTES + 1), "application/json", 413),
        (b"{}", "text/plain", 415),
        (b"{}", None, 415),
    ],
    ids=["too-large", "text-plain", "no-content-type"],
)
def test_order_413_and_415_come_before_counting(
    client: TestClient, fake: FakeRedis, content: bytes, content_type: str | None, status: int
) -> None:
    """守住 Kelvin 2026-10-06「下单……按访客来源限流」的计数位置（验收：413 与 415 检查之后
    计数）：这两种请求不计数，已超限的来源也仍得到 413 或 415。"""
    headers = _headers(**{"Idempotency-Key": _key()})
    if content_type is not None:
        headers["Content-Type"] = content_type

    assert client.post(ORDER_URL, content=content, headers=headers).status_code == status
    assert fake.count(ORDER_BUCKET, SOURCE) == 0

    broken = _headers(**{"Content-Type": "application/json"})
    for _ in range(ORDER_LIMIT + 1):
        client.post(ORDER_URL, content=b"{", headers=broken)
    assert client.post(ORDER_URL, content=content, headers=headers).status_code == status
    assert fake.count(ORDER_BUCKET, SOURCE) == ORDER_LIMIT + 1


@pytest.mark.parametrize(
    ("content", "key"),
    [
        (b"{not json", "valid-key-0123456789"),
        (b'{"lines": [], "extra": 1}', "valid-key-0123456789"),
        (None, "short"),
        (None, None),
    ],
    ids=["broken-json", "invalid-fields", "bad-key", "missing-key"],
)
def test_order_invalid_request_is_counted_then_same_422(
    db: Session,
    app: FastAPI,
    client: TestClient,
    fake: FakeRedis,
    content: bytes | None,
    key: str | None,
) -> None:
    """守住 Kelvin 2026-10-06「下单……按访客来源限流」（验收：JSON 解析、幂等键与其他校验之前
    计数）：计数一次后返回与未配置 Redis 时相同的 422，不创建订单；已超限时同样的请求是 429。"""
    _catalog(db)
    body = content if content is not None else json.dumps(_order_payload()).encode()
    headers = _headers(**{"Content-Type": "application/json"})
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post(ORDER_URL, content=body, headers=headers)

    assert response.status_code == 422
    assert fake.count(ORDER_BUCKET, SOURCE) == 1
    _unconfigured(app)
    baseline = client.post(ORDER_URL, content=body, headers=headers)
    assert baseline.status_code == 422
    assert response.json() == baseline.json()
    assert set(response.headers) == set(baseline.headers)
    assert _count(db, Order) == 0

    app.dependency_overrides[get_optional_redis_client] = lambda: fake
    for _ in range(ORDER_LIMIT):
        client.post(ORDER_URL, content=body, headers=headers)
    limited = client.post(ORDER_URL, content=body, headers=headers)
    assert limited.status_code == 429
    assert limited.json() == RATE_LIMITED


# ---------------------------------------------------------------------------
# Redis 不可用与未配置
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError(f"Error connecting to {SECRET_URL}"),
        redis.exceptions.TimeoutError(f"Timeout reading from {SECRET_URL}"),
    ],
    ids=["connection-error", "timeout"],
)
def test_store_errors_let_both_endpoints_through_with_one_warning(
    db: Session,
    client: TestClient,
    fake: FakeRedis,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """守住 Kelvin 2026-10-06「Redis……不可用时这两个接口放行（……连不上、超时或出错时记一条
    警告；不同于查单的拒绝）」：两个接口照常处理，各记一条只含桶名与固定说明的警告，
    不含来源地址、请求内容、连接串或异常消息。"""
    _catalog(db)
    fake.error = error
    caplog.set_level(logging.DEBUG)

    quote = _quote(client)
    quote_records = _app_records(caplog)
    caplog.clear()
    order = _order(client, _key())
    order_records = _app_records(caplog)

    assert quote.status_code == 200
    assert order.status_code == 201
    assert _count(db, Order) == 1
    for records, bucket in ((quote_records, QUOTE_BUCKET), (order_records, ORDER_BUCKET)):
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        message = records[0].getMessage()
        assert bucket in message
        for secret in (SOURCE, SECRET_URL, "secret-host", "hunter2", str(error), SECRET_SKU, NAME):
            assert secret not in message
        assert records[0].exc_info is None


def test_unconfigured_store_lets_both_endpoints_through_silently(
    db: Session,
    app: FastAPI,
    client: TestClient,
    fake: FakeRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """守住 Kelvin 2026-10-06「Redis 未配置……时这两个接口放行（未配置时不记日志）」：
    可选依赖返回空时照常处理、不计数、不记日志，超过阈值也不拒绝。"""
    _catalog(db)
    _unconfigured(app)
    caplog.set_level(logging.DEBUG)

    for _ in range(QUOTE_LIMIT + 1):
        assert _quote(client).status_code == 200
    key = _key()
    assert _order(client, key).status_code == 201
    for _ in range(ORDER_LIMIT):
        assert _order(client, key).status_code == 200

    assert fake.values == {}
    assert _app_records(caplog) == []


def test_real_dependency_without_url_lets_requests_through(
    engine: Engine, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """守住 Kelvin 2026-10-06「Redis 未配置……时这两个接口放行（未配置时不记日志）」：
    不覆盖可选依赖、连接串为空时也照常处理且不记日志。"""
    _catalog(db)
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, base_url="https://testserver")
    caplog.set_level(logging.DEBUG)

    assert _quote(client).status_code == 200
    assert _order(client, _key()).status_code == 201
    assert _app_records(caplog) == []


@pytest.mark.parametrize(
    "redis_url", ["", "http://secret-redis-host.internal/7"], ids=["empty", "malformed"]
)
def test_optional_dependency_returns_none_when_unavailable(redis_url: str) -> None:
    """守住 Kelvin 2026-10-06「Redis 未配置……时这两个接口放行」（验收：可选取客户端依赖遇
    RateLimitUnavailable 返回空，原来的取客户端依赖照旧抛出）。"""
    state = SimpleNamespace(settings=Settings(_env_file=None, redis_url=redis_url))
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    with pytest.raises(RateLimitUnavailable):
        get_redis_client(request)  # type: ignore[arg-type]
    assert get_optional_redis_client(request) is None  # type: ignore[arg-type]

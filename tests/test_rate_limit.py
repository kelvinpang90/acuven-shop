"""短时限流：Redis 客户端、固定窗口计数与访客来源。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
「短时限流计数存共享 infra_redis 为本项目分配的独立库编号，多 API 进程共用；Redis 数据丢失后
这些计数清零可接受」与「查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝请求」。
每条测试的文档字符串引用它守住的那一句；
设计没有直接原句的，写明守住的是 SHOP-TASK-026 验收的哪一条。

不连真 Redis：FakeRedis 是本文件自写的内存替身，只实现被用到的 GET 与事务管道（INCR、EXPIRE）。
期望的键在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Annotated

import pytest
import redis
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import Settings
from app.services import rate_limit
from app.services.rate_limit import (
    UNKNOWN_SOURCE,
    RateLimitUnavailable,
    client_source,
    current_count,
    get_redis_client,
    hit,
)

SECRET_URL = "redis://:hunter2@secret-redis-host.internal:6379/7"
SECRET_HOST = "secret-redis-host.internal"
ORDER_NUMBER = "AS20261005ABCD"


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻，以及可注入的故障。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.pipelines: list[FakePipeline] = []
        self.fail_on: str | None = None
        self.error: Exception | None = None

    def advance(self, seconds: int) -> None:
        self.now += seconds

    def ttl(self, key: str) -> int | None:
        self._purge(key)
        if key not in self.expires_at:
            return None
        return self.expires_at[key] - self.now

    def _purge(self, key: str) -> None:
        if key in self.expires_at and self.expires_at[key] <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    def _maybe_fail(self, where: str) -> None:
        if self.fail_on == where:
            assert self.error is not None
            raise self.error

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        pipe = FakePipeline(self, transaction)
        self.pipelines.append(pipe)
        return pipe

    def get(self, key: str) -> bytes | None:
        self._maybe_fail("get")
        self._purge(key)
        if key not in self.values:
            return None
        return str(self.values[key]).encode()

    def _incr(self, key: str) -> int:
        self._purge(key)
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def _expire(self, key: str, seconds: int, nx: bool) -> bool:
        # 与 Redis 7 相同：NX 只在键没有过期时间时设置；不带 NX 时每次都覆盖。
        self._purge(key)
        if key not in self.values:
            return False
        if nx and key in self.expires_at:
            return False
        self.expires_at[key] = self.now + seconds
        return True


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行（MULTI ... EXEC）。"""

    def __init__(self, store: FakeRedis, transaction: bool) -> None:
        self.store = store
        self.transaction = transaction
        self.queued: list[tuple] = []
        self.executed: list[list[tuple]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.store._maybe_fail("incr")
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key, seconds, nx))
        return self

    def execute(self) -> list:
        self.store._maybe_fail("execute")
        batch, self.queued = self.queued, []
        self.executed.append(batch)
        results: list = []
        for command in batch:
            if command[0] == "incr":
                results.append(self.store._incr(command[1]))
            else:
                results.append(self.store._expire(command[1], command[2], command[3]))
        return results


def _key(bucket: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return "acuven_shop:rate_limit:" + bucket + ":" + digest


def _request(headers: list[tuple[bytes, bytes]], peer: tuple[str, int] | None) -> Request:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers, "client": peer}
    return Request(scope)


def _settings_request(redis_url: str) -> SimpleNamespace:
    state = SimpleNamespace(settings=Settings(redis_url=redis_url))
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.fixture(autouse=True)
def _fresh_client_cache():
    rate_limit._client_for.cache_clear()
    yield
    rate_limit._client_for.cache_clear()


# --- 固定窗口计数 ---


def test_allows_up_to_limit_then_rejects() -> None:
    """守住「短时限流计数」：上限内放行，超过上限拒绝（超限后继续拒绝）。"""
    fake = FakeRedis()
    results = [hit(fake, "order_lookup", "203.0.113.5", 3, 60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_increment_and_expire_run_in_one_transaction_with_nx() -> None:
    """守住「多 API 进程共用」：自增与设置过期在同一个 MULTI 事务里，过期用 EXPIRE NX。"""
    fake = FakeRedis()
    hit(fake, "order_lookup", "203.0.113.5", 3, 60)
    (pipe,) = fake.pipelines
    assert pipe.transaction is True
    key = _key("order_lookup", "203.0.113.5")
    assert pipe.executed == [[("incr", key), ("expire", key, 60, True)]]


def test_expire_set_only_on_first_increment_and_not_extended() -> None:
    """守住「短时限流」的固定窗口（验收：只在键尚无过期时间时设置过期）：
    之后的自增不延长窗口。"""
    fake = FakeRedis()
    key = _key("order_lookup", "203.0.113.5")
    hit(fake, "order_lookup", "203.0.113.5", 10, 60)
    assert fake.ttl(key) == 60
    fake.advance(50)
    hit(fake, "order_lookup", "203.0.113.5", 10, 60)
    hit(fake, "order_lookup", "203.0.113.5", 10, 600)
    assert fake.ttl(key) == 10
    assert current_count(fake, "order_lookup", "203.0.113.5") == 3


def test_count_restarts_after_window() -> None:
    """守住「短时限流」：窗口过后键过期，计数从零开始，再次放行。"""
    fake = FakeRedis()
    assert [hit(fake, "admin_login", "203.0.113.5", 1, 60) for _ in range(2)] == [True, False]
    fake.advance(60)
    assert current_count(fake, "admin_login", "203.0.113.5") == 0
    assert hit(fake, "admin_login", "203.0.113.5", 1, 60) is True


def test_lost_counts_start_from_zero() -> None:
    """守住「Redis 数据丢失后这些计数清零可接受」：计数只在 Redis，清空后从零开始计。"""
    fake = FakeRedis()
    hit(fake, "order_lookup", "203.0.113.5", 1, 60)
    assert hit(fake, "order_lookup", "203.0.113.5", 1, 60) is False
    fake.values.clear()
    fake.expires_at.clear()
    assert hit(fake, "order_lookup", "203.0.113.5", 1, 60) is True


def test_current_count_does_not_increment() -> None:
    """守住验收「只读函数返回当前计数且不自增（键不存在为 0）」。"""
    fake = FakeRedis()
    assert current_count(fake, "order_lookup", ORDER_NUMBER) == 0
    assert current_count(fake, "order_lookup", ORDER_NUMBER) == 0
    hit(fake, "order_lookup", ORDER_NUMBER, 5, 60)
    hit(fake, "order_lookup", ORDER_NUMBER, 5, 60)
    assert current_count(fake, "order_lookup", ORDER_NUMBER) == 2
    assert current_count(fake, "order_lookup", ORDER_NUMBER) == 2
    assert fake.values == {_key("order_lookup", ORDER_NUMBER): 2}
    assert len(fake.pipelines) == 2


def test_key_is_prefix_bucket_and_digest_without_identifier() -> None:
    """守住验收「键为固定前缀加桶名加标识的 SHA-256 十六进制摘要，标识原文不进 Redis」。"""
    fake = FakeRedis()
    hit(fake, "order_lookup", ORDER_NUMBER, 5, 60)
    hit(fake, "order_lookup_source", "203.0.113.5", 5, 60)
    keys = set(fake.values) | set(fake.expires_at)
    expected = {_key("order_lookup", ORDER_NUMBER), _key("order_lookup_source", "203.0.113.5")}
    assert keys == expected
    for key in keys:
        assert ORDER_NUMBER not in key
        assert "203.0.113.5" not in key


def test_buckets_and_identifiers_are_independent() -> None:
    """守住「按规范化手机号、来源、国家及全站限流」各自计数：不同桶、不同标识互不影响。"""
    fake = FakeRedis()
    assert hit(fake, "order_lookup", "203.0.113.5", 1, 60) is True
    assert hit(fake, "order_lookup", "203.0.113.5", 1, 60) is False
    assert hit(fake, "order_lookup", "203.0.113.6", 1, 60) is True
    assert hit(fake, "admin_login", "203.0.113.5", 1, 60) is True
    assert current_count(fake, "order_lookup", "203.0.113.5") == 2
    assert current_count(fake, "order_lookup", "203.0.113.6") == 1
    assert current_count(fake, "admin_login", "203.0.113.5") == 1


@pytest.mark.parametrize(
    "bucket", ["", "Order_lookup", "order-lookup", "order:lookup", "1order", None]
)
def test_bucket_must_be_lowercase_underscore(bucket: object) -> None:
    """守住验收「桶名（小写下划线）」：其他桶名是编程错误，不写 Redis。"""
    fake = FakeRedis()
    with pytest.raises(ValueError):
        hit(fake, bucket, "203.0.113.5", 1, 60)  # type: ignore[arg-type]
    assert fake.pipelines == []


@pytest.mark.parametrize("limit, window", [(0, 60), (1, 0), (-1, 60), (True, 60), (1, 1.5)])
def test_limit_and_window_must_be_positive_integers(limit: object, window: object) -> None:
    """守住验收「具体阈值由调用的任务传入」：上限与窗口秒数须为正整数，否则是编程错误。"""
    fake = FakeRedis()
    with pytest.raises(ValueError) as info:
        hit(fake, "order_lookup", ORDER_NUMBER, limit, window)  # type: ignore[arg-type]
    assert ORDER_NUMBER not in str(info.value)
    assert fake.pipelines == []


# --- Redis 不可用时 fail closed ---


def test_empty_url_is_unavailable() -> None:
    """守住「Redis 不可用时依赖限流的敏感接口拒绝请求」：连接串未配置时抛不可用异常。"""
    with pytest.raises(RateLimitUnavailable):
        get_redis_client(_settings_request(""))  # type: ignore[arg-type]


def test_invalid_url_is_unavailable_without_url_in_message() -> None:
    """守住「Redis 不可用时……拒绝请求」与验收「消息不含连接串」：
    连接串格式不对也当不可用。"""
    bad_url = "http://secret-redis-host.internal/7"
    with pytest.raises(RateLimitUnavailable) as info:
        get_redis_client(_settings_request(bad_url))  # type: ignore[arg-type]
    assert SECRET_HOST not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_client_is_lazy_reused_with_short_timeouts_and_no_retry() -> None:
    """守住「多 API 进程共用」与验收「惰性创建、进程内复用，
    超时各不超过 0.5 秒，不自动重试」。"""
    request = _settings_request(SECRET_URL)
    client = get_redis_client(request)  # type: ignore[arg-type]
    assert get_redis_client(request) is client  # type: ignore[arg-type]
    kwargs = client.connection_pool.connection_kwargs
    assert 0 < kwargs["socket_connect_timeout"] <= 0.5
    assert 0 < kwargs["socket_timeout"] <= 0.5
    assert kwargs["retry"]._retries == 0
    assert kwargs["db"] == 7


@pytest.mark.parametrize("where", ["incr", "execute"])
@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError(f"Error connecting to {SECRET_HOST}:6379. {SECRET_URL}"),
        redis.exceptions.TimeoutError(f"Timeout reading from {SECRET_HOST}:6379"),
        redis.exceptions.ResponseError(f"ERR wrong number of arguments {SECRET_URL}"),
    ],
    ids=["connection", "timeout", "response"],
)
def test_redis_errors_during_hit_are_unavailable(where: str, error: Exception) -> None:
    """守住「Redis 不可用时依赖限流的敏感接口拒绝请求」：连接错误、超时或 Redis 报错时
    抛不可用异常（不当作未超限放行），消息与异常链不含连接串、主机、计数键或标识。"""
    fake = FakeRedis()
    fake.fail_on, fake.error = where, error
    with pytest.raises(RateLimitUnavailable) as info:
        hit(fake, "order_lookup", ORDER_NUMBER, 5, 60)
    _assert_hidden(info.value)


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError(f"Error connecting to {SECRET_HOST}:6379"),
        redis.exceptions.TimeoutError(f"Timeout reading from {SECRET_HOST}:6379"),
    ],
    ids=["connection", "timeout"],
)
def test_redis_errors_during_read_are_unavailable(error: Exception) -> None:
    """守住「Redis 不可用时依赖限流的敏感接口拒绝请求」：只读计数时出错也抛不可用异常。"""
    fake = FakeRedis()
    fake.fail_on, fake.error = "get", error
    with pytest.raises(RateLimitUnavailable) as info:
        current_count(fake, "order_lookup", ORDER_NUMBER)
    _assert_hidden(info.value)


def _assert_hidden(error: BaseException) -> None:
    message = str(error)
    for secret in (SECRET_URL, SECRET_HOST, "hunter2", ORDER_NUMBER, "acuven_shop:rate_limit"):
        assert secret not in message
    assert error.__cause__ is None
    assert error.__suppress_context__


def test_dependency_can_be_overridden() -> None:
    """守住验收「一个供 FastAPI 依赖注入的取客户端函数，测试可以覆盖它」。"""
    app = FastAPI()
    app.state.settings = Settings(redis_url="")
    fake = FakeRedis()

    @app.get("/probe")
    def probe(client: Annotated[redis.Redis, Depends(get_redis_client)]) -> dict[str, bool]:
        return {"allowed": hit(client, "probe", "x", 1, 60)}

    app.dependency_overrides[get_redis_client] = lambda: fake
    with TestClient(app) as client:
        assert client.get("/probe").json() == {"allowed": True}
        assert client.get("/probe").json() == {"allowed": False}


def test_module_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    """守住验收「模块不写日志」（设计：不得把……完整手机号写进日志）。"""
    fake = FakeRedis()
    with caplog.at_level(logging.DEBUG):
        hit(fake, "order_lookup", ORDER_NUMBER, 1, 60)
        current_count(fake, "order_lookup", ORDER_NUMBER)
        fake.fail_on, fake.error = "execute", redis.exceptions.ConnectionError(SECRET_URL)
        with pytest.raises(RateLimitUnavailable):
            hit(fake, "order_lookup", ORDER_NUMBER, 1, 60)
    assert [r for r in caplog.records if r.name.startswith("app.")] == []


# --- 访客来源 ---

PEER = ("198.51.100.20", 51234)


def test_source_uses_valid_real_ip() -> None:
    """守住「按……来源……限流」与验收「X-Real-IP 是合法的 IPv4 或 IPv6 地址时使用」。"""
    assert client_source(_request([(b"x-real-ip", b"203.0.113.5")], PEER)) == "203.0.113.5"
    assert client_source(_request([(b"x-real-ip", b" 2001:DB8:0::1 ")], PEER)) == "2001:db8::1"


@pytest.mark.parametrize(
    "value", [b"not-an-ip", b"", b"203.0.113.999", b"unknown", b"203.0.113.5:80"]
)
def test_source_falls_back_on_invalid_real_ip(value: bytes) -> None:
    """守住验收「X-Real-IP 非法时退回连接的对端地址」。"""
    assert client_source(_request([(b"x-real-ip", value)], PEER)) == "198.51.100.20"


def test_source_falls_back_on_missing_real_ip() -> None:
    """守住验收「X-Real-IP 缺失时退回对端地址，再没有时用固定的占位值」；
    不读 X-Forwarded-For。"""
    forwarded = [(b"x-forwarded-for", b"192.0.2.1, 203.0.113.5")]
    assert client_source(_request(forwarded, PEER)) == "198.51.100.20"
    assert client_source(_request(forwarded, None)) == UNKNOWN_SOURCE


@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-real-ip", b"192.0.2.1"), (b"x-real-ip", b"203.0.113.5")],
        [(b"x-real-ip", b"192.0.2.1, 203.0.113.5")],
    ],
    ids=["two-headers", "comma-list"],
)
def test_source_falls_back_on_multiple_real_ip_values(headers: list[tuple[bytes, bytes]]) -> None:
    """守住验收「X-Real-IP 含多个值时退回对端地址」：访客追加的值不被采用。"""
    assert client_source(_request(headers, PEER)) == "198.51.100.20"
    assert client_source(_request(headers, None)) == UNKNOWN_SOURCE

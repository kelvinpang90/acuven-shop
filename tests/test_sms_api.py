"""发送短信验证码接口：POST /api/sms/send（app/api/sms.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 1 条（下称「第 1 条」）：
  “服务端按所选国家码或输入的国家码规范化为 E.164……并以规范化结果判定是否属于白名单”；
  “号码属于短信白名单国家时，先过人机挑战再发短信验证码”；“号码不属于白名单时不发短信”。
- 「权限与资料保护」第 3 条（下称「第 3 条」）：“注册、短信登录与结账验证是同一个短信验证
  流程”；“不暴露账号是否存在或是否设了密码”；“账号注销须先以短信验证码确认”。
- 「权限与资料保护」第 6 条（下称「第 6 条」）：“应用日志与监控不记录姓名、完整电话、
  地址、验证码”。
- 「失败、并发与重试」第 2 条（下称「重试第 2 条」）：“结账时白名单号码的短信无法送达或
  短信服务停发，允许改为游客下单”；第 3 条（下称「重试第 3 条」）：“短信验证开关关闭时，
  所有短信发送请求在人机挑战核验、调用服务商与预占预算之前即被拒绝，不写验证记录”；
  第 4 条（下称「重试第 4 条」）：“发送前采用托管人机挑战，并在后端核验令牌。按规范化
  手机号、来源、国家及全站限流”；“Redis 或 MySQL 不可用时短信停发”；“短信服务覆盖不足时
  展示明确错误”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：“每个号码
  60 秒 1 条”；“限流与访客未通过人机挑战也不降级；人机挑战服务本身不可用（连不上或出错）
  按停发处理”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-070 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_sms_verification.py 相同，由引擎发 BEGIN，
预占在保存点里插入当日行；另因 TestClient 在另一个线程里调用接口，允许跨线程用同一连接）。
测试里读写库都用随即关闭的会话，请求之间不留未结束的事务。接口的会话依赖换成每个请求一个
绑定同一内存库的会话；服务商与人机挑战用 SHOP-TASK-067 的测试替身（服务商另加逐次不同的
请求 ID）；本接口取 Redis 客户端的依赖 get_sms_redis_client 换成本文件的内存替身 FakeRedis
（只实现事务管道的 INCR 与 EXPIRE，不模拟过期），或换成 None。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.sms import MAX_BODY_BYTES, get_sms_redis_client
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, SiteSetting, VerificationAttempt
from app.models.site import SITE_SETTING_ID
from app.services import sms_verification
from app.services.captcha import (
    MAX_TOKEN_LENGTH,
    CaptchaResult,
    FakeCaptchaVerifier,
    get_captcha_verifier,
)
from app.services.sms_budget import SmsBudgetError
from app.services.sms_provider import FakeSmsProvider, SendResult, SendStatus, get_sms_provider
from app.services.sms_verification import BUCKET_PHONE_MINUTE, BUCKET_SOURCE_HOUR

URL = "/api/sms/send"

PHONE_LOCAL = "012-345 6789"
PHONE_MY = "+60123456789"
PHONE_MY_OTHER = "+60198765432"
PHONE_SG = "+6581234567"
PHONE_UK = "+447911123456"
TOKEN = "turnstile-token-secret-value"
SOURCE = "203.0.113.7"
COST_MY = 60_000
COST_SG = 50_000

NO_STORE = "no-store"
SENT = {"status": "sent"}

# 替身逐次受理时的请求 ID 在整个测试进程里不重复。
_REQUEST_IDS = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：计数值；不模拟过期，可注入连接错误。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.fail = False

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    def count(self, bucket: str, identifier: str) -> int:
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        return self.values.get(f"acuven_shop:rate_limit:{bucket}:{digest}", 0)


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
        if self.store.fail:
            raise redis.exceptions.ConnectionError("Error connecting to redis-host:6379")
        results: list[Any] = []
        for command, key in self.queued:
            if command == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            else:
                results.append(True)
        return results


class Provider(FakeSmsProvider):
    """SHOP-TASK-067 的替身；未预设发起结果时每次受理给一个新的请求 ID。"""

    def __init__(self) -> None:
        super().__init__()
        self.sequential = True

    def returns(self, result: SendResult) -> None:
        self.send_result = result
        self.sequential = False

    def start_verification(self, phone_e164: str) -> SendResult:
        result = super().start_verification(phone_e164)
        if self.sequential:
            return SendResult(SendStatus.ACCEPTED, f"VE{next(_REQUEST_IDS):032x}")
        return result


# ---------------------------------------------------------------------------
# 夹具与辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # 与 tests/test_sms_verification.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 保存点与提交才和 MySQL 上一样。
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
    yield engine
    engine.dispose()


def make_settings(**overrides: Any) -> Settings:
    # 连接串显式为空：不覆盖依赖时即「未配置」。
    values: dict[str, Any] = {
        "database_url": "",
        "redis_url": "",
        "sms_max_cost_micro_usd_my": COST_MY,
        "sms_max_cost_micro_usd_sg": COST_SG,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def captcha() -> FakeCaptchaVerifier:
    return FakeCaptchaVerifier()


@pytest.fixture
def app(
    engine: Engine, fake: FakeRedis, provider: Provider, captcha: FakeCaptchaVerifier
) -> FastAPI:
    app = create_app(make_settings())

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_sms_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_provider] = lambda: provider
    app.dependency_overrides[get_captcha_verifier] = lambda: captcha
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def enabled(engine: Engine) -> None:
    set_switch(engine, True)


def set_switch(engine: Engine, value: bool) -> None:
    with Session(engine) as session:
        row = session.get(SiteSetting, SITE_SETTING_ID)
        if row is None:
            session.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=value))
        else:
            row.sms_verification_enabled = value
        session.commit()


def add_member(engine: Engine, phone: str) -> None:
    with Session(engine) as session:
        session.add(Member(phone=phone, status="active"))
        session.commit()


def attempts(engine: Engine) -> list[tuple[str, str, str]]:
    """全部验证记录的（号码, 用途, 状态），按 ID。"""
    with Session(engine) as session:
        rows = session.execute(
            select(
                VerificationAttempt.phone,
                VerificationAttempt.purpose,
                VerificationAttempt.status,
            ).order_by(VerificationAttempt.id)
        ).all()
    return [tuple(row) for row in rows]


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "phone": PHONE_LOCAL,
        "phone_region": "MY",
        "purpose": "checkout",
        "captcha_token": TOKEN,
    }
    body.update(overrides)
    return body


def without(field: str) -> dict[str, Any]:
    body = payload()
    del body[field]
    return body


def post_raw(client: TestClient, content: Any, content_type: str | None) -> Any:
    headers = {} if content_type is None else {"Content-Type": content_type}
    return client.post(URL, content=content, headers=headers)


def assert_error(response: Any, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json() == {"detail": code}
    assert response.headers["cache-control"] == NO_STORE


def assert_untouched(
    engine: Engine, fake: FakeRedis, provider: Provider, captcha: FakeCaptchaVerifier
) -> None:
    """人机挑战、限流、服务商都未被调用，也没有写记录。"""
    assert captcha.calls == 0
    assert fake.values == {}
    assert provider.send_calls == 0
    assert attempts(engine) == []


# ---------------------------------------------------------------------------
# 发送成功
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_sent_is_200_with_status_sent_and_no_store(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """第 1 条“先过人机挑战再发短信验证码”。验收：sent 为 200 {"status":"sent"}，带 no-store；
    不需要会话与 CSRF（请求不带任何 cookie 与 CSRF 请求头）。
    """
    response = client.post(URL, json=payload())

    assert response.status_code == 200
    assert response.json() == SENT
    assert response.headers["cache-control"] == NO_STORE
    assert captcha.calls == 1
    assert provider.send_calls == 1
    assert attempts(engine) == [(PHONE_MY, "checkout", "sent")]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "region", "expected"),
    [
        (PHONE_LOCAL, "MY", PHONE_MY),
        ("+60 12-345 6789", "SG", PHONE_MY),
        ("8123 4567", "SG", PHONE_SG),
        ("+65 8123 4567", "MY", PHONE_SG),
    ],
)
def test_phone_is_normalized_with_region_like_guest_order(
    client: TestClient, engine: Engine, phone: str, region: str, expected: str
) -> None:
    """第 1 条“服务端按所选国家码或输入的国家码规范化为 E.164”。验收：规范化方式与游客下单的
    phone 与 phone_region 相同（以加号开头时以输入为准）。
    """
    response = client.post(URL, json=payload(phone=phone, phone_region=region))

    assert response.status_code == 200
    assert attempts(engine) == [(expected, "checkout", "sent")]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("purpose", ["checkout", "register", "login", "reset_password"])
def test_each_allowed_purpose_is_sent_and_recorded(
    client: TestClient, engine: Engine, purpose: str
) -> None:
    """第 3 条“注册、短信登录与结账验证是同一个短信验证流程”，“已有密码的重设再次短信验证”。
    验收：用途为 checkout、register、login、reset_password 之一。
    """
    response = client.post(URL, json=payload(purpose=purpose))

    assert response.status_code == 200
    assert response.json() == SENT
    assert attempts(engine) == [(PHONE_MY, purpose, "sent")]


@pytest.mark.usefixtures("enabled")
def test_source_is_taken_from_client_source(client: TestClient, fake: FakeRedis) -> None:
    """重试第 4 条“按规范化手机号、来源、国家及全站限流”。验收：来源取 client_source
    （X-Real-IP 恰好一个合法地址时用它）。
    """
    response = client.post(URL, json=payload(), headers={"X-Real-IP": SOURCE})

    assert response.status_code == 200
    assert fake.count(BUCKET_SOURCE_HOUR, SOURCE) == 1
    assert fake.count(BUCKET_PHONE_MINUTE, PHONE_MY) == 1


# ---------------------------------------------------------------------------
# 各结果的状态码与错误体
# ---------------------------------------------------------------------------


def test_switch_off_is_403_sms_disabled_before_captcha(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """重试第 3 条“短信验证开关关闭时，所有短信发送请求在人机挑战核验、调用服务商与预占预算
    之前即被拒绝，不写验证记录”。验收：sms_disabled 为 403。无设置行即关闭。
    """
    assert_error(client.post(URL, json=payload()), 403, "sms_disabled")
    set_switch(engine, False)
    assert_error(client.post(URL, json=payload()), 403, "sms_disabled")
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
def test_not_whitelisted_is_422(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """第 1 条“号码不属于白名单时不发短信”。验收：not_whitelisted 为 422
    {"detail":"not_whitelisted"}。
    """
    response = client.post(URL, json=payload(phone=PHONE_UK))

    assert_error(response, 422, "not_whitelisted")
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
def test_captcha_failed_is_403(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """重试第 4 条“发送前采用托管人机挑战，并在后端核验令牌”；Kelvin 10-08“访客未通过人机挑战
    也不降级”。验收：captcha_failed 为 403。
    """
    captcha.result = CaptchaResult.FAILED

    assert_error(client.post(URL, json=payload()), 403, "captcha_failed")
    assert captcha.calls == 1
    assert fake.values == {}
    assert provider.send_calls == 0
    assert attempts(engine) == []


@pytest.mark.usefixtures("enabled")
def test_rate_limited_is_429(client: TestClient, engine: Engine, provider: Provider) -> None:
    """Kelvin 10-08“每个号码 60 秒 1 条”，“限流……也不降级”。验收：rate_limited 为 429。"""
    assert client.post(URL, json=payload()).status_code == 200

    assert_error(client.post(URL, json=payload()), 429, "rate_limited")
    assert provider.send_calls == 1
    assert attempts(engine) == [(PHONE_MY, "checkout", "sent")]


@pytest.mark.usefixtures("enabled")
def test_undeliverable_is_422_sms_undeliverable(
    client: TestClient, engine: Engine, provider: Provider
) -> None:
    """重试第 4 条“短信服务覆盖不足时展示明确错误”；重试第 2 条“短信无法送达……允许改为游客
    下单”。验收：undeliverable 为 422 sms_undeliverable。
    """
    provider.returns(SendResult(SendStatus.UNDELIVERABLE))

    assert_error(client.post(URL, json=payload()), 422, "sms_undeliverable")
    assert attempts(engine) == [(PHONE_MY, "checkout", "undeliverable")]


@pytest.mark.usefixtures("enabled")
def test_provider_unavailable_is_503_sms_suspended(
    client: TestClient, engine: Engine, provider: Provider
) -> None:
    """重试第 2 条“短信服务停发，允许改为游客下单”。验收：suspended 为 503 sms_suspended。"""
    provider.returns(SendResult(SendStatus.UNAVAILABLE))

    assert_error(client.post(URL, json=payload()), 503, "sms_suspended")
    assert attempts(engine) == [(PHONE_MY, "checkout", "suspended")]


@pytest.mark.usefixtures("enabled")
def test_captcha_service_unavailable_is_503_sms_suspended(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """Kelvin 10-08“人机挑战服务本身不可用（连不上或出错）按停发处理”。验收：suspended 为
    503 sms_suspended。
    """
    captcha.result = CaptchaResult.UNAVAILABLE

    assert_error(client.post(URL, json=payload()), 503, "sms_suspended")
    assert provider.send_calls == 0
    assert attempts(engine) == [(PHONE_MY, "checkout", "suspended")]


@pytest.mark.usefixtures("enabled")
def test_redis_error_is_503_sms_suspended(
    client: TestClient, engine: Engine, fake: FakeRedis, provider: Provider
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”。验收：suspended 为 503 sms_suspended。"""
    fake.fail = True

    assert_error(client.post(URL, json=payload()), 503, "sms_suspended")
    assert provider.send_calls == 0
    assert attempts(engine) == [(PHONE_MY, "checkout", "suspended")]


@pytest.mark.usefixtures("enabled")
def test_mysql_error_is_503_sms_suspended_without_leaking(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”。验收：处理中 MySQL 出错为 503
    sms_suspended；错误响应不含号码。原异常的参数里带完整号码。
    """

    def broken(_db: Session) -> bool:
        raise OperationalError("SELECT 1", {"phone": PHONE_MY}, Exception("gone away"))

    monkeypatch.setattr(sms_verification, "is_sms_verification_enabled", broken)

    response = client.post(URL, json=payload())

    assert_error(response, 503, "sms_suspended")
    assert "123456789" not in response.text
    assert provider.send_calls == 0


@pytest.mark.usefixtures("enabled")
def test_budget_error_is_503_sms_suspended_and_rolled_back(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”。验收：处理中 MySQL 出错为 503
    sms_suspended；结算时每日预算记录不成立（SmsBudgetError）按同样回答，第二个事务回滚，
    不留 sent 记录。
    """

    def broken(_db: Session, _reservation: object) -> None:
        raise SmsBudgetError("sms budget settle would go negative")

    monkeypatch.setattr(sms_verification, "settle_sms", broken)

    assert_error(client.post(URL, json=payload()), 503, "sms_suspended")
    assert provider.send_calls == 1
    assert attempts(engine) == []


def test_database_not_configured_keeps_get_session_answer(
    engine: Engine, fake: FakeRedis, provider: Provider, captcha: FakeCaptchaVerifier
) -> None:
    """验收：数据库未配置时照 app/db/session.py 的 get_session 现有回答（503），不另转换；
    经路由类带 no-store。
    """
    app = create_app(make_settings())
    app.dependency_overrides[get_sms_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_provider] = lambda: provider
    app.dependency_overrides[get_captcha_verifier] = lambda: captcha

    response = TestClient(app).post(URL, json=payload())

    assert_error(response, 503, "database is not configured")
    assert_untouched(engine, fake, provider, captcha)


# ---------------------------------------------------------------------------
# Redis 客户端依赖
# ---------------------------------------------------------------------------


def _request(redis_url: str) -> Any:
    settings = make_settings(redis_url=redis_url)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


@pytest.mark.parametrize("redis_url", ["", "not-a-redis-url"])
def test_redis_dependency_returns_none_when_unavailable(redis_url: str) -> None:
    """验收：本文件的依赖函数调用 get_redis_client，抛 RateLimitUnavailable 时返回 None
    （未配置与连接串格式不对两例）。
    """
    assert get_sms_redis_client(_request(redis_url)) is None


def test_redis_dependency_returns_client_when_configured() -> None:
    """验收：Redis 客户端由本文件里的依赖函数经 get_redis_client 取得（不连接）。"""
    client = get_sms_redis_client(_request("redis://redis-host:6379/5"))

    assert isinstance(client, redis.Redis)


def test_redis_not_configured_is_503_sms_suspended_not_service_unavailable(
    engine: Engine, provider: Provider, captcha: FakeCaptchaVerifier
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”。验收：RateLimitUnavailable 时依赖返回
    None（不让异常经 _NoStoreRoute 变成 503 service_unavailable），None 交给发送函数在开关、
    白名单与人机挑战之后按停发处理，不得放行发送。不覆盖本接口的 Redis 依赖。
    """
    set_switch(engine, True)
    app = create_app(make_settings())

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_sms_provider] = lambda: provider
    app.dependency_overrides[get_captcha_verifier] = lambda: captcha
    client = TestClient(app)

    assert_error(client.post(URL, json=payload()), 503, "sms_suspended")
    assert captcha.calls == 1
    assert provider.send_calls == 0
    assert attempts(engine) == [(PHONE_MY, "checkout", "suspended")]

    # 开关、白名单与人机挑战仍在停发之前判定。
    assert_error(client.post(URL, json=payload(phone=PHONE_UK)), 422, "not_whitelisted")
    captcha.result = CaptchaResult.FAILED
    assert_error(client.post(URL, json=payload()), 403, "captcha_failed")
    set_switch(engine, False)
    assert_error(client.post(URL, json=payload()), 403, "sms_disabled")
    assert provider.send_calls == 0
    assert attempts(engine) == [(PHONE_MY, "checkout", "suspended")]


# ---------------------------------------------------------------------------
# 不暴露号码是否已注册
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("purpose", ["login", "reset_password"])
def test_login_and_reset_answer_the_same_for_registered_and_unregistered(
    client: TestClient, engine: Engine, provider: Provider, purpose: str
) -> None:
    """第 3 条“不暴露账号是否存在”。验收：登录与重设用途对未注册号码照常发送，响应不暴露号码
    是否已注册（状态码、响应头与响应体都相同）。
    """
    add_member(engine, PHONE_MY)

    registered = client.post(URL, json=payload(phone=PHONE_MY, purpose=purpose))
    unregistered = client.post(URL, json=payload(phone=PHONE_MY_OTHER, purpose=purpose))

    assert registered.status_code == unregistered.status_code == 200
    assert registered.json() == unregistered.json() == SENT
    assert dict(registered.headers) == dict(unregistered.headers)
    assert provider.send_calls == 2
    assert attempts(engine) == [
        (PHONE_MY, purpose, "sent"),
        (PHONE_MY_OTHER, purpose, "sent"),
    ]


# ---------------------------------------------------------------------------
# 413 → 415 → 422
# ---------------------------------------------------------------------------


def _padded(size: int) -> bytes:
    """合法请求体，末尾以空白补到 size 字节（JSON 允许）。"""
    raw = json.dumps(payload()).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


@pytest.mark.usefixtures("enabled")
def test_body_at_4_kb_is_accepted_and_one_byte_more_is_413(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """验收：请求体逐块读，超过 4 KB 为 413，先于一切；413 不回显请求内容。"""
    assert MAX_BODY_BYTES == 4 * 1024

    over = post_raw(client, _padded(MAX_BODY_BYTES + 1), "application/json")
    assert_error(over, 413, "request body too large")
    assert_untouched(engine, fake, provider, captcha)

    at_limit = post_raw(client, _padded(MAX_BODY_BYTES), "application/json")
    assert at_limit.status_code == 200
    assert at_limit.json() == SENT


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/json"],
    ids=["not-json", "no-content-type", "json"],
)
def test_oversized_body_is_413_before_content_type_and_structure(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    content_type: str | None,
) -> None:
    """验收：413（超过 4 KB）先于 415 与 422；分块发送、不带 Content-Length 时也按实际读到的
    字节判断。请求体不合法且多出字段。
    """
    chunks = [b'{"phone": "' + b"9" * 3000, b'", "member_id": "' + b"x" * 3000 + b'"}']

    response = post_raw(client, iter(chunks), content_type)

    assert_error(response, 413, "request body too large")
    assert "999" not in response.text
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"],
)
def test_not_json_is_415_before_structure(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    content_type: str | None,
) -> None:
    """验收：不是 JSON 为 415，在 413 之后、422 之前（合法与多出字段的请求体都是 415）。"""
    for body in (payload(), payload(extra="x")):
        response = post_raw(client, json.dumps(body).encode(), content_type)
        assert_error(response, 415, "request body must be JSON")
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
def test_json_content_type_with_charset_is_accepted(client: TestClient) -> None:
    """验收：检查顺序沿用 app/api/orders.py 的写法（媒体类型不分大小写、可带参数）。"""
    raw = json.dumps(payload()).encode()

    response = post_raw(client, raw, "Application/JSON; charset=utf-8")

    assert response.status_code == 200


STRUCTURE_CASES = [
    pytest.param(without("phone"), ("body", "phone"), "missing", id="missing-phone"),
    pytest.param(without("phone_region"), ("body", "phone_region"), "missing", id="missing-region"),
    pytest.param(without("purpose"), ("body", "purpose"), "missing", id="missing-purpose"),
    pytest.param(
        without("captcha_token"), ("body", "captcha_token"), "missing", id="missing-token"
    ),
    pytest.param(
        payload(member_id="SECRET-EXTRA"),
        ("body", "member_id"),
        "extra_forbidden",
        id="extra-field",
    ),
    pytest.param(payload(phone=60123456789), ("body", "phone"), "string_type", id="phone-int"),
    pytest.param(
        payload(phone_region=None), ("body", "phone_region"), "string_type", id="region-null"
    ),
    pytest.param(
        payload(captcha_token=12345), ("body", "captcha_token"), "string_type", id="token-int"
    ),
    pytest.param(
        payload(captcha_token=""), ("body", "captcha_token"), "string_too_short", id="token-empty"
    ),
    pytest.param(
        payload(captcha_token="S" * (MAX_TOKEN_LENGTH + 1)),
        ("body", "captcha_token"),
        "string_too_long",
        id="token-too-long",
    ),
    pytest.param(
        payload(purpose="SECRET-PURPOSE"), ("body", "purpose"), "literal_error", id="unknown"
    ),
    pytest.param(
        payload(purpose="delete_account"),
        ("body", "purpose"),
        "literal_error",
        id="delete-account",
    ),
    pytest.param(
        payload(purpose="CHECKOUT"), ("body", "purpose"), "literal_error", id="purpose-case"
    ),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("body", "loc", "error_type"), STRUCTURE_CASES)
def test_structure_errors_are_422_without_echo(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """第 3 条“账号注销须先以短信验证码确认”由会员注销任务接上，本接口不收 delete_account。
    验收：四个字段都必填、严格类型、多出字段 422；用途只有四种，未知用途（含 delete_account）
    422；captcha_token 为 1 到 2048 个字符；422 只给位置、类型与固定消息，不回显请求内容。
    """
    response = client.post(URL, json=body)

    assert response.status_code == 422
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    for secret in ("SECRET", TOKEN, "345 6789", "SSSS"):
        assert secret not in response.text
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
def test_token_of_2048_characters_is_accepted(client: TestClient) -> None:
    """验收：captcha_token 为 1 到 2048 个字符的字符串（上限可用）。"""
    response = client.post(URL, json=payload(captcha_token="t" * MAX_TOKEN_LENGTH))

    assert response.status_code == 200


@pytest.mark.usefixtures("enabled")
def test_invalid_json_is_422_without_echo(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
) -> None:
    """验收：声明为 JSON 而内容不合法时 422（结构），不回显请求内容。"""
    response = post_raw(client, b'{"phone": "SECRET-0123456789",', "application/json")

    assert response.status_code == 422
    assert [e["type"] for e in response.json()["detail"]] == ["json_invalid"]
    assert "SECRET" not in response.text
    assert "0123456789" not in response.text
    assert_untouched(engine, fake, provider, captcha)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "region"),
    [
        ("0123", "MY"),
        ("SECRET", "MY"),
        ("012-345 6789", "my"),
        ("012-345 6789", "XX"),
        ("012-345 6789", ""),
        ("", "MY"),
        ("+60 12 345 6789 0000 0000 0000 0000", "MY"),
    ],
)
def test_invalid_phone_is_422_phone_invalid_without_echo(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    phone: str,
    region: str,
) -> None:
    """第 1 条“服务端按所选国家码或输入的国家码规范化为 E.164”。验收：号码不成立为 422，类型
    phone_invalid，经 normalize_phone（地区代码不合法也是），不回显请求内容。
    """
    response = client.post(URL, json=payload(phone=phone, phone_region=region))

    assert response.status_code == 422
    assert response.headers["cache-control"] == NO_STORE
    expected = {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    assert response.json() == {"detail": [expected]}
    assert_untouched(engine, fake, provider, captcha)


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


def test_wrong_method_is_405_without_request_content(client: TestClient) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.get(URL, params={"phone": PHONE_MY, "captcha_token": TOKEN})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert TOKEN not in response.text


@pytest.mark.usefixtures("enabled")
def test_no_logs_with_phone_or_token(
    client: TestClient, provider: Provider, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码”。验收：不写日志（发送成功、停发与
    结构错误都不产生应用日志，任何日志都不含号码与令牌）。
    """
    caplog.set_level(logging.DEBUG)

    client.post(URL, json=payload())
    provider.returns(SendResult(SendStatus.UNAVAILABLE))
    client.post(URL, json=payload(phone=PHONE_SG))
    client.post(URL, json=payload(extra=TOKEN))

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        assert "123456789" not in message
        assert "81234567" not in message
        assert TOKEN not in message

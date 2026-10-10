"""会员注销的短信验证接口：POST /api/member/delete/send-code 与 POST /api/member/delete/verify
（app/api/member_deletion.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：
  “账号注销须先以短信验证码确认（自动注册的账号可能从未设过密码），再撤销会话、删除手机号
  与密码”；“会话到期、退出与服务端授权检查”；第 5 条（下称「第 5 条」）：会话以服务端会话
  实现，写操作另须 CSRF 令牌；第 6 条（下称「第 6 条」）：“应用日志与监控不记录……完整
  电话……验证码”。
- 「边界与原则」第 4 条（下称「边界第 4 条」）：“下单、发送短信、提交验证码与确认注销时，
  服务端都按当时读取的开关值判定：开关在流程途中被关闭后，提交的验证码不再核验……也不能以
  短信完成注销确认”。
- 「失败、并发与重试」第 3 条（下称「重试第 3 条」）：“短信验证开关关闭时，所有短信发送
  请求在人机挑战核验、调用服务商与预占预算之前即被拒绝，不写验证记录”；第 4 条（下称「重试
  第 4 条」）：“发送前采用托管人机挑战，并在后端核验令牌。按规范化手机号、来源、国家及全站
  限流”，“Redis 或 MySQL 不可用时短信停发”，“短信服务覆盖不足时展示明确错误”。
- docs/UX.md 0.10 P13（下称「UX P13」）：“<V1：用途「注销确认」，号码固定为本账号>”；
  “注销须先以短信验证码确认（V1），验证通过后才出现 [account.delete_confirm]”；“注销确认时
  短信发送失败或号码不在白名单，只显示 [auth.sms_not_sent_no_change]”。
- docs/HANDOFF.md 0.45 记录的 Kelvin 2026-10-10 决定（1）（下称「Kelvin 10-10」）：
  “注销验证码核验通过后，服务端在共享 Redis 本项目的库编号里记一条绑定当前会员会话的一次性
  批准，10 分钟有效、使用一次即删除，确认注销时取用”，“批准绑定会员会话，凭登录会话与 CSRF
  令牌、不另发 cookie”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-081 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_member_auth_api.py 相同）。接口的会话依赖换成
每个请求一个绑定同一内存库的会话（可换成提交即抛错的子类），请求结束关闭时回滚未提交的改动；检查
结果一律在另一个数据库会话里读。服务商与人机挑战用 SHOP-TASK-067 的测试替身（服务商另加逐次不同
的请求 ID，照 tests/test_sms_api.py）；两个取 Redis 客户端的依赖（get_redis_client 与
get_sms_redis_client）都换成本文件的内存替身 FakeRedis（PING、带 EX 的 SET、事务管道 GET、DEL、
INCR 与 EXPIRE，可拨动的时钟与按命令注入的连接错误；未加依赖）。会员与会员会话直接写库；期望的
Redis 键与 CSRF 令牌在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.sms import get_sms_redis_client
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, MemberSession, SiteSetting, VerificationAttempt
from app.models.site import SITE_SETTING_ID
from app.services import sms_verification
from app.services.captcha import CaptchaResult, FakeCaptchaVerifier, get_captcha_verifier
from app.services.member_auth import issue_member_session
from app.services.member_deletion import consume_delete_approval
from app.services.rate_limit import get_redis_client
from app.services.sms_budget import SmsBudgetError
from app.services.sms_provider import (
    CheckStatus,
    FakeSmsProvider,
    SendResult,
    SendStatus,
    get_sms_provider,
)

SEND_URL = "/api/member/delete/send-code"
VERIFY_URL = "/api/member/delete/verify"
SESSION_URL = "/api/member/session"
MAX_BODY_BYTES = 4 * 1024

SESSION_COOKIE_NAME = "__Host-shop_member_session"
CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"
APPROVAL_PREFIX = "acuven_shop:member_delete_approval:"
TEN_MINUTES = 600

PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
PHONE_UK = "+447911123456"
CODE = "482915"
TOKEN = "turnstile-token-secret-value"
REDIS_HOST = "redis-host:6379"
COST_MY = 60_000
COST_SG = 50_000

NO_STORE = "no-store"
SENT = {"status": "sent"}

_REQUEST_IDS = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻、PING 次数与按命令注入的错误。

    fail 里含 "ping"、"set" 或 "execute" 时，该命令抛带主机与端口的连接错误。
    """

    def __init__(self) -> None:
        self.now = 0
        self.values: dict[str, Any] = {}
        self.expires: dict[str, int] = {}
        self.pings = 0
        self.fail: set[str] = set()

    def advance(self, seconds: int) -> None:
        self.now += seconds

    def _maybe_fail(self, command: str) -> None:
        if command in self.fail:
            raise redis.exceptions.ConnectionError(f"Error connecting to {REDIS_HOST}")

    def _purge(self, key: str) -> None:
        expires = self.expires.get(key)
        if expires is not None and expires <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def approval_keys(self) -> list[str]:
        """未过期的注销批准键（不含限流计数）。"""
        for key in list(self.values):
            self._purge(key)
        return sorted(key for key in self.values if key.startswith(APPROVAL_PREFIX))

    def ttl(self, key: str) -> int:
        return self.expires[key] - self.now

    def ping(self) -> bool:
        self.pings += 1
        self._maybe_fail("ping")
        return True

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._maybe_fail("set")
        self.values[key] = str(value).encode()
        if ex is None:
            self.expires.pop(key, None)
        else:
            self.expires[key] = self.now + ex
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行（同 MULTI / EXEC）。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple[str, str, int | None]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def get(self, key: str) -> FakePipeline:
        self.queued.append(("get", key, None))
        return self

    def delete(self, key: str) -> FakePipeline:
        self.queued.append(("delete", key, None))
        return self

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key, None))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key, seconds))
        return self

    def execute(self) -> list[Any]:
        self.store._maybe_fail("execute")
        results: list[Any] = []
        for command, key, seconds in self.queued:
            self.store._purge(key)
            if command == "get":
                results.append(self.store.values.get(key))
            elif command == "delete":
                existed = key in self.store.values
                self.store.values.pop(key, None)
                self.store.expires.pop(key, None)
                results.append(1 if existed else 0)
            elif command == "incr":
                self.store.values[key] = int(self.store.values.get(key, 0)) + 1
                results.append(self.store.values[key])
            else:
                if key not in self.store.expires and seconds is not None:
                    self.store.expires[key] = self.store.now + seconds
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


class RaisingProvider(Provider):
    """发起与核验时都抛出带号码与验证码的异常（模拟服务商调用出错）。"""

    def start_verification(self, phone_e164: str) -> SendResult:
        super().start_verification(phone_e164)
        raise RuntimeError(f"start failed for {phone_e164}")

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        super().check_verification(request_id, code)
        raise RuntimeError(f"check failed for {PHONE_MY} with {code}")


class CommitFailingSession(Session):
    """提交时抛出带号码参数的数据库异常（模拟提交时连接断开）。"""

    def commit(self) -> None:
        raise OperationalError("COMMIT", {"phone": PHONE_MY}, Exception("gone away"))


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

    # 与 tests/test_member_auth_api.py 相同：关掉驱动的事务处理、由引擎发 BEGIN。
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


def make_settings() -> Settings:
    # 连接串显式为空：不覆盖依赖时即「未配置」。马新单次最高费用已知，预占才会成立。
    return Settings(
        _env_file=None,
        database_url="",
        redis_url="",
        sms_max_cost_micro_usd_my=COST_MY,
        sms_max_cost_micro_usd_sg=COST_SG,
    )


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
def session_class() -> list[type[Session]]:
    # 测试可把它换成 CommitFailingSession。
    return [Session]


@pytest.fixture
def app(
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    session_class: list[type[Session]],
) -> FastAPI:
    app = create_app(make_settings())

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_provider] = lambda: provider
    app.dependency_overrides[get_captcha_verifier] = lambda: captcha
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def enabled(engine: Engine) -> None:
    set_switch(engine, True)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def set_switch(engine: Engine, value: bool) -> None:
    with Session(engine) as session:
        row = session.get(SiteSetting, SITE_SETTING_ID)
        if row is None:
            session.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=value))
        else:
            row.sms_verification_enabled = value
        session.commit()


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def add_member(engine: Engine, phone: str = PHONE_MY) -> int:
    with Session(engine) as session:
        row = Member(
            phone=phone,
            password_hash=None,
            status="active",
            created_at=_now() - timedelta(days=100),
            deleted_at=None,
        )
        session.add(row)
        session.commit()
        return row.id


def login(engine: Engine, member_id: int) -> tuple[str, int]:
    """直接写库签发一个会员会话并提交，返回（令牌原文, 会话 ID）。"""
    with Session(engine) as session:
        member = session.get(Member, member_id)
        assert member is not None
        issued = issue_member_session(session, member, _now())
        session.commit()
        return issued.token, issued.session.id


def revoke(engine: Engine, session_id: int) -> None:
    with Session(engine) as session:
        stmt = update(MemberSession).where(MemberSession.id == session_id)
        session.execute(stmt.values(revoked_at=_now()))
        session.commit()


def csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def auth(token: str | None, csrf: str | None = "auto") -> dict[str, str]:
    """会员会话 cookie 与 CSRF 请求头；csrf 为 "auto" 时按 cookie 另算。"""
    headers: dict[str, str] = {}
    if token is not None:
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={token}"
        if csrf == "auto":
            headers["X-CSRF-Token"] = csrf_for(token)
    if csrf not in (None, "auto"):
        headers["X-CSRF-Token"] = csrf
    return headers


def add_attempt(engine: Engine, purpose: str = "delete_account", phone: str = PHONE_MY) -> int:
    """一条一分钟前发出、待核验（sent）的验证记录，请求 ID 逐次不同。"""
    created_at = _now() - timedelta(minutes=1)
    with Session(engine) as session:
        result = session.execute(
            insert(VerificationAttempt).values(
                phone=phone,
                purpose=purpose,
                status="sent",
                provider_request_id=f"VE{next(_REQUEST_IDS):032x}",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        session.commit()
        return result.inserted_primary_key[0]


def attempt_status(engine: Engine, attempt_id: int) -> str:
    stmt = select(VerificationAttempt.status).where(VerificationAttempt.id == attempt_id)
    return _read(engine, lambda s: s.execute(stmt).scalar_one())


def attempts(engine: Engine) -> list[tuple[str, str, str]]:
    """全部验证记录的（号码, 用途, 状态），按 ID。"""
    columns = (VerificationAttempt.phone, VerificationAttempt.purpose, VerificationAttempt.status)
    stmt = select(*columns).order_by(VerificationAttempt.id)
    return _read(engine, lambda s: [tuple(row) for row in s.execute(stmt).all()])


def members(engine: Engine) -> list[tuple[int, str | None, str]]:
    """全部会员的（ID, 号码, 状态），按 ID。"""
    stmt = select(Member.id, Member.phone, Member.status).order_by(Member.id)
    return _read(engine, lambda s: [tuple(row) for row in s.execute(stmt).all()])


def revoked_sessions(engine: Engine) -> list[int]:
    """已撤销的会员会话 ID。"""
    stmt = select(MemberSession.id).where(MemberSession.revoked_at.is_not(None))
    return _read(engine, lambda s: list(s.scalars(stmt)))


def approval_key(session_id: int) -> str:
    return APPROVAL_PREFIX + str(session_id)


def send(client: TestClient, token: str | None, csrf: str | None = "auto", **body: Any) -> Any:
    payload: dict[str, Any] = {"captcha_token": TOKEN}
    payload.update(body)
    return client.post(SEND_URL, json=payload, headers=auth(token, csrf))


def verify(client: TestClient, token: str | None, csrf: str | None = "auto", **body: Any) -> Any:
    payload: dict[str, Any] = {"code": CODE}
    payload.update(body)
    return client.post(VERIFY_URL, json=payload, headers=auth(token, csrf))


def assert_error(response: Any, status_code: int, detail: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE


def assert_no_secrets(response: Any, *extra: str) -> None:
    """响应体与响应头不含号码原文、验证码、人机挑战令牌、Redis 主机与给出的值。"""
    text = response.text + json.dumps(list(response.headers.items()))
    for value in ("123456789", CODE, TOKEN, REDIS_HOST, *extra):
        assert value not in text


@pytest.fixture
def member(engine: Engine) -> int:
    return add_member(engine)


@pytest.fixture
def signed_in(engine: Engine, member: int) -> tuple[str, int]:
    return login(engine, member)


# ---------------------------------------------------------------------------
# 发送：成功与号码、用途
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_send_code_uses_own_phone_and_delete_account_purpose(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    member: int,
    signed_in: tuple[str, int],
) -> None:
    """UX P13“<V1：用途「注销确认」，号码固定为本账号>”；第 3 条“账号注销须先以短信验证码确认”。
    验收：号码取当前会员的手机号，以用途 delete_account 调用 send_verification；sent 为 200
    {"status":"sent"}，带 no-store；响应不含号码原文与令牌；不注销、不改会员、不撤销会话。
    """
    token, _ = signed_in

    response = send(client, token)

    assert response.status_code == 200, response.text
    assert response.json() == SENT
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    assert_no_secrets(response, token)
    assert captcha.calls == 1
    assert provider.send_calls == 1
    assert attempts(engine) == [(PHONE_MY, "delete_account", "sent")]
    assert members(engine) == [(member, PHONE_MY, "active")]
    assert revoked_sessions(engine) == []


@pytest.mark.usefixtures("enabled")
def test_send_code_uses_the_signed_in_members_phone(
    client: TestClient, engine: Engine, member: int
) -> None:
    """UX P13“号码固定为本账号”。验收：号码一律取当前会员的手机号——另一会员登录发送时，记录
    的是该会员自己的号码。
    """
    other = add_member(engine, phone=PHONE_SG)
    token, _ = login(engine, other)

    assert send(client, token).status_code == 200

    assert attempts(engine) == [(PHONE_SG, "delete_account", "sent")]


# ---------------------------------------------------------------------------
# 发送：各结果的状态码与错误体
# ---------------------------------------------------------------------------


def test_send_switch_off_is_403_before_captcha(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    signed_in: tuple[str, int],
) -> None:
    """重试第 3 条“短信验证开关关闭时，所有短信发送请求在人机挑战核验、调用服务商与预占预算之前
    即被拒绝，不写验证记录”；边界第 4 条“发送短信……时，服务端都按当时读取的开关值判定”。验收：
    sms_disabled 为 403（无设置行与关闭都是）。
    """
    token, _ = signed_in

    assert_error(send(client, token), 403, "sms_disabled")
    set_switch(engine, False)
    assert_error(send(client, token), 403, "sms_disabled")

    assert captcha.calls == 0
    assert provider.send_calls == 0
    assert fake.values == {}
    assert attempts(engine) == []


@pytest.mark.usefixtures("enabled")
def test_send_not_whitelisted_is_422(
    client: TestClient, engine: Engine, provider: Provider, captcha: FakeCaptchaVerifier
) -> None:
    """UX P13“注销确认时短信发送失败或号码不在白名单，只显示 [auth.sms_not_sent_no_change]”。
    验收：结果同 POST /api/sms/send——not_whitelisted 为 422 {"detail":"not_whitelisted"}。
    """
    token, _ = login(engine, add_member(engine, phone=PHONE_UK))

    response = send(client, token)

    assert_error(response, 422, "not_whitelisted")
    assert "7911123456" not in response.text
    assert captcha.calls == 0
    assert provider.send_calls == 0
    assert attempts(engine) == []


@pytest.mark.usefixtures("enabled")
def test_send_captcha_failed_is_403(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    signed_in: tuple[str, int],
) -> None:
    """重试第 4 条“发送前采用托管人机挑战，并在后端核验令牌”。验收：结果同 POST /api/sms/send——
    captcha_failed 为 403。
    """
    captcha.result = CaptchaResult.FAILED

    assert_error(send(client, signed_in[0]), 403, "captcha_failed")
    assert provider.send_calls == 0
    assert attempts(engine) == []


@pytest.mark.usefixtures("enabled")
def test_send_rate_limited_is_429(
    client: TestClient, engine: Engine, provider: Provider, signed_in: tuple[str, int]
) -> None:
    """重试第 4 条“按规范化手机号、来源、国家及全站限流”。验收：结果同 POST /api/sms/send——
    rate_limited 为 429。
    """
    token, _ = signed_in
    assert send(client, token).status_code == 200

    assert_error(send(client, token), 429, "rate_limited")
    assert provider.send_calls == 1
    assert attempts(engine) == [(PHONE_MY, "delete_account", "sent")]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("send_status", "status_code", "code", "recorded"),
    [
        (SendStatus.UNDELIVERABLE, 422, "sms_undeliverable", "undeliverable"),
        (SendStatus.UNAVAILABLE, 503, "sms_suspended", "suspended"),
    ],
    ids=["undeliverable", "suspended"],
)
def test_send_provider_outcomes(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    signed_in: tuple[str, int],
    send_status: SendStatus,
    status_code: int,
    code: str,
    recorded: str,
) -> None:
    """重试第 4 条“短信服务覆盖不足时展示明确错误”，“Redis 或 MySQL 不可用时短信停发”。验收：
    结果同 POST /api/sms/send——undeliverable 为 422 sms_undeliverable，suspended 为 503
    sms_suspended。
    """
    provider.returns(SendResult(send_status))

    response = send(client, signed_in[0])

    assert_error(response, status_code, code)
    assert_no_secrets(response)
    assert attempts(engine) == [(PHONE_MY, "delete_account", recorded)]


@pytest.mark.usefixtures("enabled")
def test_send_redis_unavailable_is_503_sms_suspended(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    provider: Provider,
    signed_in: tuple[str, int],
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”。验收：Redis 客户端用 get_sms_redis_client
    （不可用时为 None，交给发送函数按停发处理），503 sms_suspended 而不是 service_unavailable，
    不调用服务商。
    """
    app.dependency_overrides[get_sms_redis_client] = lambda: None

    assert_error(send(client, signed_in[0]), 503, "sms_suspended")
    assert provider.send_calls == 0
    assert attempts(engine) == [(PHONE_MY, "delete_account", "suspended")]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("failure", ["budget", "provider"])
def test_send_budget_or_provider_error_is_503_and_rolled_back(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    signed_in: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”；第 6 条“应用日志与监控不记录……完整电话”。
    验收：SmsBudgetError 与 SmsVerificationError 时回滚，503 sms_suspended，不留 sent 记录；错误
    不含号码。
    """
    if failure == "budget":

        def broken(_db: Session, _reservation: object) -> None:
            raise SmsBudgetError("sms budget settle would go negative")

        monkeypatch.setattr(sms_verification, "settle_sms", broken)
    else:
        app.dependency_overrides[get_sms_provider] = lambda: RaisingProvider()

    response = send(client, signed_in[0])

    assert_error(response, 503, "sms_suspended")
    assert_no_secrets(response)
    assert attempts(engine) == []


# ---------------------------------------------------------------------------
# 核验：approved
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_approved_records_approval_bound_to_current_session(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    member: int,
    signed_in: tuple[str, int],
) -> None:
    """Kelvin 10-10“注销验证码核验通过后，服务端……记一条绑定当前会员会话的一次性批准，10 分钟
    有效”，“凭登录会话与 CSRF 令牌、不另发 cookie”。验收：approved 时提交后签发批准，204 且响应
    体为空；Redis 里只有键为当前会话 ID 的批准、值为会员 ID、过期 600 秒；验证记录已提交为
    approved；不设 cookie；no-store。
    """
    token, session_id = signed_in
    attempt_id = add_attempt(engine)

    response = verify(client, token)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    assert_no_secrets(response, token)
    assert fake.approval_keys() == [approval_key(session_id)]
    assert fake.values[approval_key(session_id)] == str(member).encode()
    assert fake.ttl(approval_key(session_id)) == TEN_MINUTES
    assert fake.pings == 1
    assert attempt_status(engine, attempt_id) == "approved"


@pytest.mark.usefixtures("enabled")
def test_approved_does_not_delete_member_or_revoke_session(
    client: TestClient, engine: Engine, member: int, signed_in: tuple[str, int]
) -> None:
    """UX P13“验证通过后才出现 [account.delete_confirm]”（验证本身不注销）。验收：两个接口都不
    注销、不改会员、不撤销会话——核验通过后会员仍为 active、号码不变，会话仍有效（取当前会话
    200）。
    """
    token, _ = signed_in
    add_attempt(engine)

    assert verify(client, token).status_code == 204

    assert members(engine) == [(member, PHONE_MY, "active")]
    assert revoked_sessions(engine) == []
    assert client.get(SESSION_URL, headers=auth(token, None)).status_code == 200


@pytest.mark.usefixtures("enabled")
def test_other_sessions_approval_cannot_be_taken(
    client: TestClient, engine: Engine, fake: FakeRedis, member: int, signed_in: tuple[str, int]
) -> None:
    """Kelvin 10-10“记一条绑定当前会员会话的一次性批准……确认注销时取用”。验收：另一个会话的
    批准不能被本会话取用——同一会员的会话 A 核验通过后，以会话 B 取用为假且不影响 A 的批准；
    A 取用一次为真。
    """
    token_a, session_a = signed_in
    _, session_b = login(engine, member)
    add_attempt(engine)

    assert verify(client, token_a).status_code == 204

    assert fake.approval_keys() == [approval_key(session_a)]
    assert consume_delete_approval(fake, session_b, member) is False
    assert fake.approval_keys() == [approval_key(session_a)]
    assert consume_delete_approval(fake, session_a, member) is True
    assert fake.approval_keys() == []


@pytest.mark.usefixtures("enabled")
def test_verify_uses_own_phone_and_delete_account_purpose(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
) -> None:
    """UX P13“<V1：用途「注销确认」，号码固定为本账号>”。验收：以用途 delete_account 与当前会员
    的号码调用 verify_code——只有登录用途的记录或另一号码的注销记录时为 no_pending（422
    code_expired），不调用服务商、不签发批准，那些记录不变。
    """
    login_attempt = add_attempt(engine, purpose="login")
    other_phone = add_attempt(engine, phone=PHONE_SG)

    response = verify(client, signed_in[0])

    assert_error(response, 422, "code_expired")
    assert provider.check_calls == 0
    assert fake.approval_keys() == []
    assert attempt_status(engine, login_attempt) == "sent"
    assert attempt_status(engine, other_phone) == "sent"


# ---------------------------------------------------------------------------
# 核验：不通过、开关、Redis 与数据库
# ---------------------------------------------------------------------------


NON_APPROVED_CASES = [
    pytest.param(CheckStatus.WRONG_CODE, True, 422, "code_wrong", "sent", id="wrong_code"),
    pytest.param(CheckStatus.EXPIRED, True, 422, "code_expired", "rejected", id="expired"),
    pytest.param(CheckStatus.APPROVED, False, 422, "code_expired", None, id="no_pending"),
    pytest.param(CheckStatus.UNAVAILABLE, True, 503, "sms_unavailable", "sent", id="unavailable"),
]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("check", "pending", "status_code", "code", "after"), NON_APPROVED_CASES)
def test_non_approved_results_answer_like_sms_login(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    member: int,
    signed_in: tuple[str, int],
    check: CheckStatus,
    pending: bool,
    status_code: int,
    code: str,
    after: str | None,
) -> None:
    """UX P13“验证通过后才出现 [account.delete_confirm]”（不通过不进入确认）。验收：非 approved
    的结果与 sms-login 相同——wrong_code 422 code_wrong，expired 与 no_pending 422 code_expired，
    unavailable 503 sms_unavailable；都先提交再回答（expired 时记录已提交为 rejected）；不签发
    批准，会员与会话不变。
    """
    provider.check_result = check
    attempt_id = add_attempt(engine) if pending else None

    response = verify(client, signed_in[0])

    assert_error(response, status_code, code)
    assert_no_secrets(response)
    assert fake.approval_keys() == []
    assert members(engine) == [(member, PHONE_MY, "active")]
    assert revoked_sessions(engine) == []
    if attempt_id is not None:
        assert attempt_status(engine, attempt_id) == after


@pytest.mark.parametrize("setting", ["no_row", "off"])
def test_verify_sms_disabled_is_403_without_ping_or_check(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
    setting: str,
) -> None:
    """边界第 4 条“开关在流程途中被关闭后，提交的验证码不再核验……也不能以短信完成注销确认”。
    验收：按当次从数据库读取的开关判定，关闭（含无设置行）即 403 sms_disabled，不 PING、不核验、
    不签发批准，记录仍为 sent。
    """
    attempt_id = add_attempt(engine)
    if setting == "off":
        set_switch(engine, False)

    response = verify(client, signed_in[0])

    assert_error(response, 403, "sms_disabled")
    assert fake.pings == 0
    assert provider.check_calls == 0
    assert fake.approval_keys() == []
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.parametrize("redis_state", ["failing", "not_configured"])
def test_verify_sms_disabled_wins_over_redis_trouble(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
    redis_state: str,
) -> None:
    """边界第 4 条“提交验证码……时，服务端都按当时读取的开关值判定”。验收：开关关闭时即使 Redis
    故障（PING 会失败）或未配置，也是 403 sms_disabled 而不是 503——先判开关，不取 Redis 客户端、
    不 PING。
    """
    add_attempt(engine)
    set_switch(engine, False)
    if redis_state == "failing":
        fake.fail = {"ping", "set", "execute"}
    else:
        del app.dependency_overrides[get_redis_client]

    response = verify(client, signed_in[0])

    assert_error(response, 403, "sms_disabled")
    assert fake.pings == 0
    assert provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
def test_verify_redis_not_configured_is_503_without_check(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    provider: Provider,
    signed_in: tuple[str, int],
) -> None:
    """重试第 4 条“查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝请求”；Kelvin 10-10 批准存于
    共享 Redis。验收：Redis 未配置时由路由类回答 503 service_unavailable，不核验、不消耗验证码
    （不调用服务商，记录仍为 sent）。
    """
    del app.dependency_overrides[get_redis_client]
    attempt_id = add_attempt(engine)

    response = verify(client, signed_in[0])

    assert_error(response, 503, "service_unavailable")
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_verify_ping_failure_is_503_without_check(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
) -> None:
    """Kelvin 10-10 批准存于共享 Redis。验收：核验前执行一次 PING，失败（RedisError 转为
    RateLimitUnavailable）时 503 service_unavailable，不核验、不消耗验证码（不调用服务商，记录
    仍为 sent）；错误不含 Redis 主机。
    """
    attempt_id = add_attempt(engine)
    fake.fail = {"ping"}

    response = verify(client, signed_in[0])

    assert_error(response, 503, "service_unavailable")
    assert_no_secrets(response)
    assert fake.pings == 1
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_issue_failure_after_commit_is_503_service_unavailable(
    client: TestClient, engine: Engine, fake: FakeRedis, signed_in: tuple[str, int]
) -> None:
    """Kelvin 10-10“记一条……一次性批准”（记不下即不能确认）。验收：提交之后签发时 Redis 出错为
    503 service_unavailable，没有批准；验证记录已为 approved（须重新发送验证码）。
    """
    attempt_id = add_attempt(engine)
    fake.fail = {"set"}

    response = verify(client, signed_in[0])

    assert_error(response, 503, "service_unavailable")
    assert_no_secrets(response)
    assert fake.approval_keys() == []
    assert attempt_status(engine, attempt_id) == "approved"


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("failure", ["provider", "commit"])
def test_verify_database_or_provider_error_is_503_sms_unavailable(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    session_class: list[type[Session]],
    fake: FakeRedis,
    signed_in: tuple[str, int],
    failure: str,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码”。验收：核验或提交时数据库出错（含核验
    函数的 SmsVerificationError 与提交失败）回滚，503 sms_unavailable；不签发批准，记录仍为
    sent；错误不含号码与验证码。
    """
    attempt_id = add_attempt(engine)
    if failure == "provider":
        app.dependency_overrides[get_sms_provider] = lambda: RaisingProvider()
    else:
        session_class[0] = CommitFailingSession

    response = verify(client, signed_in[0])

    assert_error(response, 503, "sms_unavailable")
    assert_no_secrets(response)
    assert fake.approval_keys() == []
    assert attempt_status(engine, attempt_id) == "sent"


# ---------------------------------------------------------------------------
# 检查顺序：413 → 415 → 401 → 403 → 422
# ---------------------------------------------------------------------------


def _valid(url: str) -> dict[str, Any]:
    return {"captcha_token": TOKEN} if url == SEND_URL else {"code": CODE}


URLS = [pytest.param(SEND_URL, id="send-code"), pytest.param(VERIFY_URL, id="verify")]


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/json"],
    ids=["not-json", "no-content-type", "json"],
)
@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("signed", [False, True], ids=["no-session", "session"])
def test_oversized_body_is_413_before_everything(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    provider: Provider,
    signed: bool,
    url: str,
    content_type: str | None,
) -> None:
    """验收：413（逐块读，超过 4 KB）先于一切——没有会话、开关关闭、Redis 未配置时也是 413；分块
    发送、不带 Content-Length 也按实际读到的字节判断；不回显请求内容。
    """
    del app.dependency_overrides[get_redis_client]
    headers = auth(login(engine, add_member(engine))[0]) if signed else {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    chunks = [b'{"phone": "' + b"9" * 3000, b'", "code": "' + b"4" * 3000 + b'"}']

    response = client.post(url, content=iter(chunks), headers=headers)

    assert_error(response, 413, "request body too large")
    assert "999" not in response.text
    assert fake.pings == 0
    assert provider.send_calls == provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("url", URLS)
def test_body_at_4_kb_is_accepted(
    client: TestClient, engine: Engine, signed_in: tuple[str, int], url: str
) -> None:
    """验收：超过 4 KB 才 413——恰好 4 KB 的合法请求体照常处理。"""
    add_attempt(engine)
    raw = json.dumps(_valid(url)).encode()
    headers = {**auth(signed_in[0]), "Content-Type": "application/json"}

    padded = raw + b" " * (MAX_BODY_BYTES - len(raw))

    response = client.post(url, content=padded, headers=headers)

    assert response.status_code in (200, 204), response.text


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"],
)
@pytest.mark.parametrize("url", URLS)
def test_not_json_is_415_before_session(
    client: TestClient, provider: Provider, fake: FakeRedis, url: str, content_type: str | None
) -> None:
    """验收：不是 JSON 为 415，在 413 之后、401 之前——没有会话时合法与多出字段的请求体都是 415；
    不回显请求内容。
    """
    for body in (_valid(url), {**_valid(url), "phone": PHONE_MY}):
        headers = {} if content_type is None else {"Content-Type": content_type}
        response = client.post(url, content=json.dumps(body).encode(), headers=headers)
        assert_error(response, 415, "request body must be JSON")
        assert_no_secrets(response)
    assert fake.pings == 0
    assert provider.send_calls == provider.check_calls == 0


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("session_state", ["none", "malformed", "unknown", "revoked"])
def test_no_valid_session_is_401_before_structure(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    url: str,
    session_state: str,
) -> None:
    """第 3 条“会话到期、退出与服务端授权检查”。验收：require_member 先于 CSRF 与结构——没有会话、
    cookie 格式不对、会话不存在或已撤销时，结构不对（多出 phone）也是 401
    member_session_required；不发送、不核验、不 PING。
    """
    token: str | None
    if session_state == "none":
        token = None
    elif session_state == "malformed":
        token = "not-a-token"
    elif session_state == "unknown":
        token = "A" * 43
    else:
        token, session_id = login(engine, add_member(engine))
        revoke(engine, session_id)
    body = {**_valid(url), "phone": PHONE_SG}

    response = client.post(url, json=body, headers=auth(token))

    assert_error(response, 401, "member_session_required")
    assert_no_secrets(response)
    assert fake.pings == 0
    assert provider.send_calls == provider.check_calls == 0


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated", "empty"])
def test_bad_csrf_is_403_before_structure(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
    url: str,
    csrf: str,
) -> None:
    """第 5 条：写操作另须 CSRF 令牌。验收：require_member_csrf 在会话之后、结构之前——
    缺失、错误、重复（即使都正确）或为空的 X-CSRF-Token 都 403 csrf_failed，结构不对（多出
    phone）也是 403；不发送、不核验、不 PING。
    """
    token, _ = signed_in
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}
    body = json.dumps({**_valid(url), "phone": PHONE_SG}).encode()
    headers: Any
    if csrf == "missing":
        headers = cookie
    elif csrf == "wrong":
        headers = {**cookie, "X-CSRF-Token": csrf_for("B" * 43)}
    elif csrf == "duplicated":
        headers = [
            ("Cookie", cookie["Cookie"]),
            ("X-CSRF-Token", csrf_for(token)),
            ("X-CSRF-Token", csrf_for(token)),
        ]
    else:
        headers = {**cookie, "X-CSRF-Token": ""}
    if isinstance(headers, dict):
        headers["Content-Type"] = "application/json"
    else:
        headers.append(("Content-Type", "application/json"))

    response = client.post(url, content=body, headers=headers)

    assert_error(response, 403, "csrf_failed")
    assert_no_secrets(response, token)
    assert fake.pings == 0
    assert provider.send_calls == provider.check_calls == 0


SEND_STRUCTURE_CASES = [
    pytest.param({}, ("body", "captcha_token"), "missing", id="missing"),
    pytest.param(
        {"captcha_token": TOKEN, "phone": PHONE_SG},
        ("body", "phone"),
        "extra_forbidden",
        id="phone",
    ),
    pytest.param(
        {"captcha_token": TOKEN, "purpose": "delete_account"},
        ("body", "purpose"),
        "extra_forbidden",
        id="purpose",
    ),
    pytest.param({"captcha_token": 123}, ("body", "captcha_token"), "string_type", id="int"),
    pytest.param({"captcha_token": ""}, ("body", "captcha_token"), "string_too_short", id="empty"),
    pytest.param(
        {"captcha_token": "SECRET" + "x" * 2043},
        ("body", "captcha_token"),
        "string_too_long",
        id="2049",
    ),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]

VERIFY_STRUCTURE_CASES = [
    pytest.param({}, ("body", "code"), "missing", id="missing"),
    pytest.param(
        {"code": CODE, "phone": PHONE_SG},
        ("body", "phone"),
        "extra_forbidden",
        id="phone",
    ),
    pytest.param(
        {"code": CODE, "phone_region": "MY"},
        ("body", "phone_region"),
        "extra_forbidden",
        id="region",
    ),
    pytest.param(
        {"code": CODE, "purpose": "delete_account"},
        ("body", "purpose"),
        "extra_forbidden",
        id="purpose",
    ),
    pytest.param({"code": 482915}, ("body", "code"), "string_type", id="int"),
    pytest.param({"code": ""}, ("body", "code"), "string_too_short", id="empty"),
    pytest.param({"code": "4" * 17}, ("body", "code"), "string_too_long", id="17"),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


def _assert_structure_error(response: Any, loc: tuple[str, ...], error_type: str) -> None:
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    for secret in ("SECRET", "8123", "4" * 17, CODE, TOKEN):
        assert secret not in response.text


@pytest.mark.parametrize(("body", "loc", "error_type"), SEND_STRUCTURE_CASES)
def test_send_structure_errors_are_422_without_echo(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    captcha: FakeCaptchaVerifier,
    signed_in: tuple[str, int],
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """UX P13“号码固定为本账号”。验收：send-code 的请求体只有 captcha_token（1 到 2048 个字符的
    字符串），严格类型，多出字段（含 phone）422，不接受号码；沿用 _body_errors，只给位置、
    类型与固定消息，不回显请求内容；排在开关之前（开关关闭也是 422），不发送、不写记录。
    """
    response = client.post(SEND_URL, json=body, headers=auth(signed_in[0]))

    _assert_structure_error(response, loc, error_type)
    assert captcha.calls == 0
    assert provider.send_calls == 0
    assert attempts(engine) == []


@pytest.mark.parametrize(("body", "loc", "error_type"), VERIFY_STRUCTURE_CASES)
def test_verify_structure_errors_are_422_without_echo(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    signed_in: tuple[str, int],
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """UX P13“号码固定为本账号”。验收：verify 的请求体只有 code（1 到 16 个字符的字符串），严格
    类型，多出字段（含 phone）422，不接受号码；沿用 _body_errors，不回显请求内容；排在开关与
    Redis 之前（开关关闭、Redis 未配置也是 422），不 PING、不核验。
    """
    del app.dependency_overrides[get_redis_client]
    attempt_id = add_attempt(engine)

    response = client.post(VERIFY_URL, json=body, headers=auth(signed_in[0]))

    _assert_structure_error(response, loc, error_type)
    assert fake.pings == 0
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.parametrize("url", URLS)
def test_check_order_step_by_step(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    provider: Provider,
    fake: FakeRedis,
    url: str,
) -> None:
    """验收：两个接口的检查顺序为 413 → 415 → 401 member_session_required → 403 csrf_failed →
    422；verify 之后再是开关（403 sms_disabled）→ Redis（503 service_unavailable）。每一步都以
    “本步不通过、其后各步也不通过”的请求验证；开关关闭，Redis 未配置。
    """
    del app.dependency_overrides[get_redis_client]
    token, _ = login(engine, add_member(engine))
    bad = {**_valid(url), "phone": PHONE_SG}
    oversized = json.dumps({**bad, "pad": "x" * MAX_BODY_BYTES}).encode()
    raw = json.dumps(bad).encode()

    too_large = client.post(url, content=oversized, headers={"Content-Type": "application/json"})
    assert_error(too_large, 413, "request body too large")
    not_json = client.post(url, content=raw, headers={"Content-Type": "text/plain"})
    assert_error(not_json, 415, "request body must be JSON")
    assert_error(client.post(url, json=bad), 401, "member_session_required")
    assert_error(client.post(url, json=bad, headers=auth(token, None)), 403, "csrf_failed")
    assert client.post(url, json=bad, headers=auth(token)).status_code == 422
    assert_error(client.post(url, json=_valid(url), headers=auth(token)), 403, "sms_disabled")

    if url == VERIFY_URL:
        add_attempt(engine)
        set_switch(engine, True)
        assert_error(verify(client, token), 503, "service_unavailable")
        assert provider.check_calls == 0
    assert fake.pings == 0
    assert provider.send_calls == 0


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [SEND_URL, VERIFY_URL])
def test_wrong_method_is_405_without_request_content(client: TestClient, url: str) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.get(url, params={"phone": PHONE_MY, "code": CODE, "captcha_token": TOKEN})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert CODE not in response.text
    assert TOKEN not in response.text


@pytest.mark.usefixtures("enabled")
def test_no_logs_with_phone_code_or_token(
    client: TestClient,
    engine: Engine,
    provider: Provider,
    signed_in: tuple[str, int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码”。验收：发送、核验通过、验证码错误、CSRF
    不通过与结构错误都不产生 app 下的日志，任何日志都不含号码、验证码、令牌与会话 cookie。
    """
    caplog.set_level(logging.DEBUG)
    token, _ = signed_in

    send(client, token)
    verify(client, token)
    provider.check_result = CheckStatus.WRONG_CODE
    add_attempt(engine)
    verify(client, token)
    verify(client, token, csrf=csrf_for("B" * 43))
    verify(client, token, phone=PHONE_MY)

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", CODE, TOKEN, token, csrf_for(token)):
            assert value not in message

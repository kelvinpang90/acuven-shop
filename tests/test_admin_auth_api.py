"""管理员登录、退出与会话接口：POST /api/admin/login、POST /api/admin/logout、
GET /api/admin/session 与会话依赖 require_admin（app/api/admin_auth.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条：「管理员登录按来源与账号
组合限流，连续失败后短时锁定并告警；锁定解除不绕过密码校验。单一管理员也须服务端授权与会话
到期」，第 5 条「后台单一管理员也须认证，不把权限检查留给前端」，第 6 条「应用日志……不记录
……密码」，以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员登录决定
（下称「Kelvin 2026-10-04」，含 2026-10-05 补充）。每条测试（参数化的测试是每个用例）的
文档字符串写明它守住的设计原句或 Kelvin 的哪一项决定；没有直接原句的，写明是 SHOP-TASK-036
验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。接口的会话依赖
换成每个请求一个绑定同一内存库的会话，请求结束关闭时回滚未提交的改动；检查结果一律在另一个
数据库会话里读，读得到即说明接口已提交。SHOP-TASK-026 的取客户端依赖换成本文件的内存替身
FakeRedis（只实现用到的 GET 与事务管道的 INCR、EXPIRE，可按桶名注入连接错误）。访客来源用
X-Real-IP 指定。期望的令牌摘要、CSRF 令牌与锁定标识在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_auth as admin_auth_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AdminSession, AuditEvent
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.pw_hash import hash_password
from app.services.rate_limit import get_redis_client

LOGIN_URL = "/api/admin/login"
LOGOUT_URL = "/api/admin/logout"
SESSION_URL = "/api/admin/session"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
THIRTY_DAYS_SECONDS = 30 * 24 * 3600
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

USERNAME = "shop_admin"
PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)
WRONG_PASSWORD = "wrong horse battery"
UNKNOWN_USERNAME = "nobody_here"
SOURCE = "203.0.113.5"
OTHER_SOURCE = "198.51.100.20"

FAILURE_BUCKET = "admin_login_failures"
LOCK_BUCKET = "admin_login_lock"

LOGIN_FAILED = {"detail": "login_failed"}
LOGIN_LOCKED = {"detail": "login_locked"}
UNAVAILABLE = {"detail": "service_unavailable"}
SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}

SUCCEEDED = "admin_login_succeeded"
FAILED = "admin_login_failed"
LOCKED = "admin_login_locked"
LOGOUT = "admin_logout"


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

    def count(self, bucket: str, source: str, username: str) -> int:
        # 标识另算：[来源, 去掉首尾空白并转小写的用户名] 的 JSON 编码。
        identifier = json.dumps([source, username.strip().lower()], ensure_ascii=True)
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
# 夹具与工具
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
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture
def account_id(engine: Engine) -> int:
    with Session(engine) as session:
        row = AdminAccount(
            username=USERNAME,
            password_hash=PASSWORD_HASH,
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        session.add(row)
        session.commit()
        return row.id


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(engine: Engine, fake: FakeRedis) -> FastAPI:
    # 连接串显式为空：不覆盖取客户端依赖时即「未配置」。
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _login(
    client: TestClient,
    username: str = USERNAME,
    password: str = PASSWORD,
    source: str = SOURCE,
) -> Any:
    return client.post(
        LOGIN_URL,
        json={"username": username, "password": password},
        headers={"X-Real-IP": source},
    )


def _cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _actions(engine: Engine) -> list[str]:
    return _read(
        engine,
        lambda s: list(s.scalars(select(AuditEvent.action).order_by(AuditEvent.id))),
    )


def _sessions(engine: Engine) -> list[tuple[int, str, datetime, datetime, datetime | None]]:
    return _read(
        engine,
        lambda s: [
            (row.admin_account_id, row.token_hash, row.created_at, row.expires_at, row.revoked_at)
            for row in s.scalars(select(AdminSession).order_by(AdminSession.id))
        ],
    )


def _stored_session(engine: Engine, account_id: int, *, issued_at: datetime, revoke: bool) -> str:
    """直接写库签发一个会话（可选撤销）并提交，返回令牌原文。"""
    with Session(engine) as session:
        issued = issue_admin_session(session, account_id, issued_at)
        if revoke:
            revoke_admin_session(session, issued.session, issued_at + timedelta(minutes=1))
        session.commit()
        return issued.token


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


def _touches_accounts(statements: list[str]) -> bool:
    return any(re.search(r"\badmin_accounts\b", sql) for sql in statements)


@pytest.fixture
def verify_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录接口调用密码校验的次数（只记被校验的哈希是否为假哈希，不记密码）。"""
    calls: list[str] = []
    original = admin_auth_api.verify_password

    def counting(password: str, encoded: str) -> bool:
        calls.append("dummy" if encoded == admin_auth_api.DUMMY_PASSWORD_HASH else "real")
        return original(password, encoded)

    monkeypatch.setattr(admin_auth_api, "verify_password", counting)
    return calls


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _assert_no_cookie(response: Any) -> None:
    assert response.headers.get_list("set-cookie") == []


def _logged_in(client: TestClient) -> str:
    """登录并返回 cookie 里的令牌原文。"""
    response = _login(client)
    assert response.status_code == 204, response.text
    token = client.cookies.get(COOKIE_NAME)
    assert token
    return token


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------


def test_login_success_sets_cookie_session_and_audit(
    engine: Engine, account_id: int, client: TestClient
) -> None:
    """「单一管理员也须服务端授权与会话到期」；Kelvin 2026-10-04「后台会话自登录起 30 天到期，
    不随使用延长」「本轮审计只记登录成功……」。SHOP-TASK-036 验收：204 且响应体为空；cookie
    __Host-shop_admin_session 带 HttpOnly、Secure、SameSite=Strict、Path=/、不设 Domain、
    Max-Age 30 天；库里一条会话（只存令牌摘要）与一条 admin_login_succeeded，且已提交。
    """
    response = _login(client)

    assert response.status_code == 204, response.text
    assert response.content == b""
    _assert_no_store(response)
    (cookie,) = response.headers.get_list("set-cookie")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    name, token = name_value.split("=", 1)
    assert name == COOKIE_NAME
    lowered = [attribute.lower() for attribute in attributes]
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert f"max-age={THIRTY_DAYS_SECONDS}" in lowered
    assert not any(attribute.startswith("domain") for attribute in lowered)

    (stored,) = _sessions(engine)
    owner, token_hash, created_at, expires_at, revoked_at = stored
    assert owner == account_id
    assert token_hash == hashlib.sha256(token.encode("ascii")).hexdigest()
    assert expires_at - created_at == timedelta(days=30)
    assert revoked_at is None
    assert _actions(engine) == [SUCCEEDED]


def test_unknown_username_and_wrong_password_are_identical(
    engine: Engine, account_id: int, client: TestClient, fake: FakeRedis, verify_calls: list[str]
) -> None:
    """Kelvin 2026-10-04 的 2026-10-05 补充：「用户名不存在与密码错误返回相同的响应，密码校验以
    假哈希对齐耗时」；「登录失败与锁定只在提交的用户名对应已有账号时写审计（用户名不存在的
    失败只计入锁定计数）」。SHOP-TASK-036 验收：两者 401 login_failed，状态码、响应体与响应头
    完全相同；用户名不存在不写审计；两者各计一次失败；不设 cookie、no-store。
    """
    unknown = _login(client, UNKNOWN_USERNAME, PASSWORD)
    wrong = _login(client, USERNAME, WRONG_PASSWORD)

    assert unknown.status_code == 401, unknown.text
    assert unknown.json() == LOGIN_FAILED
    _assert_no_store(unknown)
    _assert_no_cookie(unknown)
    assert wrong.status_code == unknown.status_code
    assert wrong.content == unknown.content
    assert list(wrong.headers.items()) == list(unknown.headers.items())
    assert verify_calls == ["dummy", "real"]
    assert fake.count(FAILURE_BUCKET, SOURCE, UNKNOWN_USERNAME) == 1
    assert fake.count(FAILURE_BUCKET, SOURCE, USERNAME) == 1
    assert _actions(engine) == [FAILED]
    assert _sessions(engine) == []


def test_failed_login_audit_is_committed(
    engine: Engine, account_id: int, client: TestClient
) -> None:
    """Kelvin 2026-10-04「本轮审计只记登录成功、失败、锁定……」。SHOP-TASK-036 验收：已有账号的
    失败在返回前提交（会话依赖在请求结束时回滚未提交的改动），在另一个数据库会话里能读到一条
    admin_login_failed，它属于该账号。
    """
    response = _login(client, USERNAME, WRONG_PASSWORD)

    assert response.status_code == 401, response.text
    events = _read(
        engine,
        lambda s: [(e.action, e.admin_account_id) for e in s.scalars(select(AuditEvent))],
    )
    assert events == [(FAILED, account_id)]


def test_tenth_failure_writes_exactly_one_locked_audit(
    engine: Engine, account_id: int, client: TestClient, fake: FakeRedis
) -> None:
    """「连续失败后短时锁定并告警」；Kelvin 2026-10-04「同一来源与用户名失败满 10 次即锁定
    1 小时」「锁定告警在运营告警邮件接入后补，在那之前锁定只写审计记录」。SHOP-TASK-036 验收：
    前 10 次都是 401；第 10 次失败另有且只有一条 admin_login_locked；之后被锁定的请求 429，
    不再写审计、不再计失败。
    """
    responses = [_login(client, USERNAME, WRONG_PASSWORD) for _ in range(10)]

    assert [r.status_code for r in responses] == [401] * 10
    assert _actions(engine) == [FAILED] * 9 + [FAILED, LOCKED]

    blocked = _login(client, USERNAME, WRONG_PASSWORD)

    assert blocked.status_code == 429, blocked.text
    assert _actions(engine) == [FAILED] * 10 + [LOCKED]
    assert fake.count(FAILURE_BUCKET, SOURCE, USERNAME) == 10
    assert fake.count(LOCK_BUCKET, SOURCE, USERNAME) == 1


def test_lock_rejects_correct_password_but_not_other_source(
    engine: Engine,
    account_id: int,
    client: TestClient,
    fake: FakeRedis,
    verify_calls: list[str],
) -> None:
    """「管理员登录按来源与账号组合限流」「锁定解除不绕过密码校验」；Kelvin 2026-10-04「按来源与
    用户名组合计数，攻击者无法把唯一的管理员锁在外面」。SHOP-TASK-036 验收：同一来源与用户名
    第 10 次失败后正确密码（含大小写与首尾空白不同的写法）也 429 login_locked，不查账号、不校验
    密码、不签发会话；另一来源用正确密码 204。
    """
    for _ in range(10):
        assert _login(client, USERNAME, WRONG_PASSWORD).status_code == 401
    verify_calls.clear()

    with _sql(engine) as statements:
        blocked = _login(client, USERNAME, PASSWORD)
        variant = _login(client, "  SHOP_Admin ", PASSWORD)

    for response in [blocked, variant]:
        assert response.status_code == 429, response.text
        assert response.json() == LOGIN_LOCKED
        _assert_no_store(response)
        _assert_no_cookie(response)
    assert not _touches_accounts(statements)
    assert verify_calls == []
    assert _sessions(engine) == []

    other = _login(client, USERNAME, PASSWORD, source=OTHER_SOURCE)

    assert other.status_code == 204, other.text
    assert len(_sessions(engine)) == 1


@pytest.mark.parametrize("failure", ["unconfigured", "lock_read_error"])
def test_redis_unavailable_at_lock_check_is_503_without_account_query(
    app: FastAPI,
    engine: Engine,
    account_id: int,
    client: TestClient,
    fake: FakeRedis,
    verify_calls: list[str],
    failure: str,
) -> None:
    """「失败、并发与重试」第 4 条：「查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝
    请求」。SHOP-TASK-036 验收：Redis 未配置或查锁定时连接出错，
    503 service_unavailable，没有查账号、没有校验密码、不签发会话、不写审计；no-store。
    """
    if failure == "unconfigured":
        app.dependency_overrides.pop(get_redis_client)
    else:
        fake.fail_get.add(LOCK_BUCKET)

    with _sql(engine) as statements:
        response = _login(client)

    assert response.status_code == 503, response.text
    assert response.json() == UNAVAILABLE
    _assert_no_store(response)
    _assert_no_cookie(response)
    assert not _touches_accounts(statements)
    assert verify_calls == []
    assert _sessions(engine) == []
    assert _actions(engine) == []


@pytest.mark.parametrize("username", [USERNAME, UNKNOWN_USERNAME])
def test_redis_error_when_counting_failure_is_503(
    engine: Engine, account_id: int, client: TestClient, fake: FakeRedis, username: str
) -> None:
    """「依赖 Redis 限流的敏感接口也拒绝请求」。SHOP-TASK-036 验收：只在计失败时抛连接错误时，
    密码错误与用户名不存在都返回同一个 503 而不是 401；未计入的失败不写审计。同时正确密码
    照常 204（查锁定不受影响）。
    """
    fake.fail_incr.add(FAILURE_BUCKET)

    wrong = _login(client, username, WRONG_PASSWORD)

    assert wrong.status_code == 503, wrong.text
    assert wrong.json() == UNAVAILABLE
    _assert_no_store(wrong)
    _assert_no_cookie(wrong)
    assert _actions(engine) == []

    right = _login(client)

    assert right.status_code == 204, right.text


def test_non_json_is_415_without_echo(
    engine: Engine, account_id: int, client: TestClient, fake: FakeRedis
) -> None:
    """「应用日志与监控不记录……密码」，错误响应不回显。SHOP-TASK-036 验收：只接受 JSON（否则
    415）；不回显请求内容、不计失败、不查账号；no-store。
    """
    body = json.dumps({"username": USERNAME, "password": PASSWORD})

    for headers in [{"Content-Type": "text/plain"}, {}]:
        with _sql(engine) as statements:
            response = client.post(LOGIN_URL, content=body, headers=headers)

        assert response.status_code == 415, response.text
        _assert_no_store(response)
        assert PASSWORD not in response.text
        assert USERNAME not in response.text
        assert not _touches_accounts(statements)
    assert fake.values == {}


@pytest.mark.parametrize(
    "headers",
    [{"Content-Type": "application/json"}, {"Content-Type": "text/plain"}],
)
def test_oversized_body_is_413_first(
    app: FastAPI, engine: Engine, account_id: int, client: TestClient, headers: dict[str, str]
) -> None:
    """SHOP-TASK-036 验收：请求体上限 8 KB 且先于其他校验——带多余字段、不是 JSON、Redis 未配置
    时都是 413；不回显请求内容、不查账号；no-store。
    """
    app.dependency_overrides.pop(get_redis_client)
    padding = "0123456789" * (MAX_BODY_BYTES // 10)
    body = {"username": USERNAME, "password": PASSWORD, "padding": padding}

    with _sql(engine) as statements:
        response = client.post(LOGIN_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    _assert_no_store(response)
    assert "0123456789" not in response.text
    assert PASSWORD not in response.text
    assert not _touches_accounts(statements)


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        ({"extra": "secret-extra-value"}, ["body", "extra"]),
        # Kelvin 2026-10-07（HANDOFF 0.37）「列加长到 254 个字符」：登录名上限随之为 254。
        ({"username": "u" * 255}, ["body", "username"]),
        ({"username": ""}, ["body", "username"]),
        ({"password": "p" * 257}, ["body", "password"]),
        ({"password": ""}, ["body", "password"]),
        ({"password": 12345678901234}, ["body", "password"]),
        ({"drop": "password"}, ["body", "password"]),
    ],
)
def test_invalid_body_is_422_without_echo_or_counting(
    engine: Engine,
    account_id: int,
    client: TestClient,
    fake: FakeRedis,
    body: dict[str, Any],
    loc: list[str],
) -> None:
    """「应用日志与监控不记录……密码」，错误响应不回显。SHOP-TASK-036 验收：请求体只有 username
    与 password 两个字符串，多出字段 422；用户名 1 到 254（Kelvin 2026-10-07 的邮箱上限，
    SHOP-TASK-049）、密码 1 到 256 个字符，否则 422；
    每条错误只含位置、类型与固定消息，不回显请求内容；这类 422 不计失败、不查账号；no-store。
    """
    request: dict[str, Any] = {"username": USERNAME, "password": WRONG_PASSWORD}
    request |= {key: value for key, value in body.items() if key != "drop"}
    if "drop" in body:
        del request[body["drop"]]

    with _sql(engine) as statements:
        response = client.post(LOGIN_URL, json=request, headers={"X-Real-IP": SOURCE})

    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in ["secret-extra-value", WRONG_PASSWORD, "u" * 255, "p" * 257, "12345678901234"]:
        assert value not in response.text
    assert not _touches_accounts(statements)
    assert fake.values == {}
    assert _actions(engine) == []


def test_length_boundaries_are_accepted(
    account_id: int, client: TestClient, fake: FakeRedis
) -> None:
    """SHOP-TASK-036 验收：用户名 64 个字符、密码 256 个字符仍是合格的请求体，按正常登录处理
    （查不到账号即 401 并计一次失败），而不是 422。
    """
    response = _login(client, "u" * 64, "p" * 256)

    assert response.status_code == 401, response.text
    assert fake.count(FAILURE_BUCKET, SOURCE, "u" * 64) == 1


def test_254_character_email_can_log_in(engine: Engine, client: TestClient) -> None:
    """Kelvin 2026-10-07（HANDOFF 0.37）「取值改为邮箱，列加长到 254 个字符」「登录时照旧去掉首尾
    空白并转小写后比对」。SHOP-TASK-049 验收：254 个字符的邮箱账号能登录（大写的写法也能，仍是
    254 个字符），204 并签发会话。
    """
    email = "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
    assert len(email) == 254
    with Session(engine) as session:
        row = AdminAccount(
            username=email,
            password_hash=PASSWORD_HASH,
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        session.add(row)
        session.commit()
        account_id = row.id

    exact = _login(client, email, PASSWORD)
    variant = _login(client, email.upper(), PASSWORD)

    assert exact.status_code == 204, exact.text
    assert variant.status_code == 204, variant.text
    assert [owner for owner, *_ in _sessions(engine)] == [account_id, account_id]
    assert _actions(engine) == [SUCCEEDED, SUCCEEDED]


# ---------------------------------------------------------------------------
# 当前会话与退出
# ---------------------------------------------------------------------------


def test_session_returns_username_expiry_and_csrf(
    engine: Engine, account_id: int, client: TestClient
) -> None:
    """「单一管理员也须服务端授权与会话到期」；第 5 条「后台单一管理员也须认证」。
    SHOP-TASK-036 验收：经 require_admin 返回 {username, expires_at, csrf_token}；
    到期时间为带 Z 的 UTC，与库里会话的到期时间相同；CSRF 令牌由 cookie 算出；no-store。
    """
    token = _logged_in(client)

    response = client.get(SESSION_URL)

    assert response.status_code == 200, response.text
    _assert_no_store(response)
    body = response.json()
    assert set(body) == {"username", "expires_at", "csrf_token"}
    assert body["username"] == USERNAME
    assert body["csrf_token"] == _csrf_for(token)
    assert body["expires_at"].endswith("Z")
    ((_, _, _, expires_at, _),) = _sessions(engine)
    assert datetime.fromisoformat(body["expires_at"]) == expires_at.replace(tzinfo=UTC)


@pytest.mark.parametrize("state", ["missing", "malformed", "unknown", "expired", "revoked"])
def test_session_and_logout_require_valid_session(
    engine: Engine, account_id: int, client: TestClient, state: str
) -> None:
    """「单一管理员也须服务端授权与会话到期」；Kelvin 2026-10-04「后台会话自登录起 30 天
    到期」。SHOP-TASK-036 验收：取会话与退出在无 cookie、会话不存在、已过期、已撤销时都 401
    admin_session_required，不区分原因；退出不写审计；no-store。
    """
    now = _now()
    token = None
    if state == "malformed":
        token = "not-a-token"
    elif state == "unknown":
        token = "A" * 43
    elif state == "expired":
        issued_at = now - timedelta(days=30, seconds=1)
        token = _stored_session(engine, account_id, issued_at=issued_at, revoke=False)
    elif state == "revoked":
        token = _stored_session(engine, account_id, issued_at=now, revoke=True)
    headers = {} if token is None else _cookie_header(token)
    logout_headers = dict(headers)
    if token is not None:
        logout_headers["X-CSRF-Token"] = _csrf_for(token)

    session = client.get(SESSION_URL, headers=headers)
    logout = client.post(LOGOUT_URL, headers=logout_headers)

    for response in [session, logout]:
        assert response.status_code == 401, response.text
        assert response.json() == SESSION_REQUIRED
        _assert_no_store(response)
        _assert_no_cookie(response)
    assert LOGOUT not in _actions(engine)


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated"])
def test_logout_without_valid_csrf_is_403_and_keeps_session(
    engine: Engine, account_id: int, client: TestClient, csrf: str
) -> None:
    """「单一管理员也须服务端授权」；SHOP-TASK-035 记录「后台写操作须同时通过会话校验与 CSRF
    校验」。SHOP-TASK-036 验收：退出缺或错 X-CSRF-Token 时 403 csrf_failed，不撤销会话、不写
    审计、不清 cookie，之后取会话照常 200；no-store。
    """
    token = _logged_in(client)
    if csrf == "missing":
        headers: Any = {}
    elif csrf == "wrong":
        headers = {"X-CSRF-Token": _csrf_for("B" * 43)}
    else:
        headers = [("X-CSRF-Token", _csrf_for(token)), ("X-CSRF-Token", _csrf_for(token))]

    response = client.post(LOGOUT_URL, headers=headers)

    assert response.status_code == 403, response.text
    assert response.json() == CSRF_FAILED
    _assert_no_store(response)
    _assert_no_cookie(response)
    ((_, _, _, _, revoked_at),) = _sessions(engine)
    assert revoked_at is None
    assert _actions(engine) == [SUCCEEDED]
    assert client.get(SESSION_URL, headers=_cookie_header(token)).status_code == 200


def test_logout_revokes_session_and_clears_cookie(
    engine: Engine, account_id: int, client: TestClient
) -> None:
    """「单一管理员也须服务端授权与会话到期」；Kelvin 2026-10-04「本轮审计只记登录成功、失败、
    锁定、退出……」。SHOP-TASK-036 验收：CSRF 通过后撤销当前会话、写 admin_logout 并提交，清除
    cookie，204 且响应体为空；之后用原 cookie 取会话 401；no-store。
    """
    token = _logged_in(client)

    response = client.post(LOGOUT_URL, headers={"X-CSRF-Token": _csrf_for(token)})

    assert response.status_code == 204, response.text
    assert response.content == b""
    _assert_no_store(response)
    (cookie,) = response.headers.get_list("set-cookie")
    lowered = [part.strip().lower() for part in cookie.split(";")]
    assert lowered[0].startswith(f"{COOKIE_NAME.lower()}=")
    assert "max-age=0" in lowered
    assert "secure" in lowered
    assert "path=/" in lowered
    ((owner, _, _, _, revoked_at),) = _sessions(engine)
    assert owner == account_id
    assert revoked_at is not None
    assert _actions(engine) == [SUCCEEDED, LOGOUT]

    again = client.get(SESSION_URL, headers=_cookie_header(token))

    assert again.status_code == 401, again.text
    assert again.json() == SESSION_REQUIRED


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_endpoints_write_no_app_logs(
    account_id: int, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录……密码」（「权限与资料保护」第 6 条）。SHOP-TASK-036 验收：
    不写日志——登录失败、成功、取会话与退出都不产生 app 下的日志记录。
    """
    with caplog.at_level(logging.DEBUG):
        _login(client, UNKNOWN_USERNAME, WRONG_PASSWORD)
        _login(client, USERNAME, WRONG_PASSWORD)
        token = _logged_in(client)
        client.get(SESSION_URL)
        client.post(LOGOUT_URL, headers={"X-CSRF-Token": _csrf_for(token)})

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []

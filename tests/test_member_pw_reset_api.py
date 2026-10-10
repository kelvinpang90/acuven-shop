"""会员重设密码接口：POST /api/member/password-reset/verify 与 POST /api/member/password-reset
（app/api/member_pw_reset.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：“已有密码的
  重设再次短信验证”，“密码至少 8 位、不强制复杂度”，“短信验证开关关闭期间：注册、短信登录与
  密码重设暂停”；第 6 条（下称「第 6 条」）：“应用日志与监控不记录……完整电话……验证码……
  密码”。
- 「边界与原则」第 4 条（下称「边界第 4 条」）：“提交验证码时，服务端都按当时读取的开关值判定：
  开关在流程途中被关闭后，提交的验证码不再核验，不登录、不注册、不重设密码”。
- docs/UX.md 0.10 P12 忘记密码（下称「UX P12」）：“V1 验证通过后，号码已注册才显示新密码输入；
  未设过密码的账号验证通过后同样可设置（等同首次设密码）。号码未注册时不创建账号”；“重设完成
  → 登录”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：“重设密码与注销时
  撤销该会员的全部会话”。
- 0.44 记录的 Kelvin 2026-10-10 决定（下称「Kelvin 10-10」）：“服务端签发一次性重设凭据，存于
  共享 Redis……10 分钟有效、使用一次即删除，经 HttpOnly、Secure、SameSite=Lax 的 cookie 只交给
  本浏览器，提交新密码另须 CSRF 令牌；Redis 不可用时重设暂停……凭据丢失只需重新验证”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-079 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_member_auth_api.py 相同）。接口的会话依赖
换成每个请求一个绑定同一内存库的会话（可换成提交即抛错的子类），请求结束关闭时回滚未提交的
改动；检查结果一律在另一个数据库会话里读。服务商用 SHOP-TASK-067 的测试替身 FakeSmsProvider；
取 Redis 客户端的依赖换成本文件的内存替身 FakeRedis（PING、带 EX 的 SET、事务管道 GET 与 DEL，
可拨动的时钟与按命令注入的连接错误；未加依赖）。期望的 Redis 键与 CSRF 令牌在这里另算，不取
实现里的函数。
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

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, MemberSession, SiteSetting, VerificationAttempt
from app.models.site import SITE_SETTING_ID
from app.services.member_auth import authenticate_member, issue_member_session
from app.services.pw_hash import hash_password
from app.services.rate_limit import get_redis_client
from app.services.sms_provider import CheckStatus, FakeSmsProvider, get_sms_provider

VERIFY_URL = "/api/member/password-reset/verify"
RESET_URL = "/api/member/password-reset"
SESSION_URL = "/api/member/session"
MAX_BODY_BYTES = 4 * 1024

COOKIE_NAME = "__Host-shop_member_pw_reset"
SESSION_COOKIE_NAME = "__Host-shop_member_session"
TEN_MINUTES = 600
KEY_PREFIX = "acuven_shop:member_pw_reset:"
CSRF_PREFIX = b"acuven-shop/member-pw-reset/csrf\x00"
SESSION_CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"

PHONE_LOCAL = "012-345 6789"
PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
CODE = "482915"
OLD_PASSWORD = "SECRET-PW old horse"
OLD_HASH = hash_password(OLD_PASSWORD)
NEW_PASSWORD = "SECRET-PW new battery"
OTHER_PASSWORD = "SECRET-PW other staple"
REDIS_HOST = "redis-host:6379"

NO_STORE = "no-store"

_REQUEST_IDS = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：值（按 Redis 存成字节）、按替身时钟计的过期时刻、PING 次数与按命令注入的错误。

    fail 里含 "ping"、"set" 或 "execute" 时，该命令抛带主机与端口的连接错误。
    """

    def __init__(self) -> None:
        self.now = 0
        self.values: dict[str, bytes] = {}
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

    def live_keys(self) -> list[str]:
        for key in list(self.values):
            self._purge(key)
        return sorted(self.values)

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
        self.queued: list[tuple[str, str]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def get(self, key: str) -> FakePipeline:
        self.queued.append(("get", key))
        return self

    def delete(self, key: str) -> FakePipeline:
        self.queued.append(("delete", key))
        return self

    def execute(self) -> list[Any]:
        self.store._maybe_fail("execute")
        results: list[Any] = []
        for command, key in self.queued:
            self.store._purge(key)
            if command == "get":
                results.append(self.store.values.get(key))
            elif key in self.store.values:
                del self.store.values[key]
                self.store.expires.pop(key, None)
                results.append(1)
            else:
                results.append(0)
        return results


class RaisingProvider(FakeSmsProvider):
    """核验时抛出带号码与验证码的异常（模拟服务商调用出错）。"""

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


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def provider() -> FakeSmsProvider:
    return FakeSmsProvider()


@pytest.fixture
def session_class() -> list[type[Session]]:
    # 测试可把它换成 CommitFailingSession。
    return [Session]


@pytest.fixture
def app(
    engine: Engine,
    fake: FakeRedis,
    provider: FakeSmsProvider,
    session_class: list[type[Session]],
) -> FastAPI:
    # 连接串显式为空：不覆盖依赖时即「未配置」。
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_provider] = lambda: provider
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def enabled(engine: Engine) -> None:
    set_switch(engine, True)


def fresh(client: TestClient) -> TestClient:
    """同一个应用、不带任何 cookie 的另一个浏览器。"""
    return TestClient(client.app, base_url="https://testserver")


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


def add_attempt(engine: Engine, purpose: str = "reset_password", phone: str = PHONE_MY) -> int:
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


def add_member(engine: Engine, phone: str = PHONE_MY, password_hash: str | None = OLD_HASH) -> int:
    with Session(engine) as session:
        row = Member(
            phone=phone,
            password_hash=password_hash,
            status="active",
            created_at=_now() - timedelta(days=100),
            deleted_at=None,
        )
        session.add(row)
        session.commit()
        return row.id


def delete_member(engine: Engine, member_id: int) -> None:
    """照注销的结果直接改库：清空号码与密码哈希、状态改为 deleted。"""
    with Session(engine) as session:
        session.execute(
            update(Member)
            .where(Member.id == member_id)
            .values(phone=None, password_hash=None, status="deleted", deleted_at=_now())
        )
        session.commit()


def members(engine: Engine) -> list[tuple[int, str | None, str | None, str]]:
    """全部会员的（ID, 号码, 密码哈希, 状态），按 ID。"""
    return _read(
        engine,
        lambda s: [
            (row.id, row.phone, row.password_hash, row.status)
            for row in s.scalars(select(Member).order_by(Member.id))
        ],
    )


def password_hash_of(engine: Engine, member_id: int) -> str | None:
    stmt = select(Member.password_hash).where(Member.id == member_id)
    return _read(engine, lambda s: s.execute(stmt).scalar_one())


def sessions(engine: Engine) -> list[tuple[int, datetime | None]]:
    """全部会员会话的（会员 ID, 撤销时间），按 ID。"""
    return _read(
        engine,
        lambda s: [
            (row.member_id, row.revoked_at)
            for row in s.scalars(select(MemberSession).order_by(MemberSession.id))
        ],
    )


def stored_session(engine: Engine, member_id: int) -> str:
    """直接写库签发一个会员会话并提交，返回令牌原文。"""
    with Session(engine) as session:
        member = session.get(Member, member_id)
        assert member is not None
        issued = issue_member_session(session, member, _now())
        session.commit()
        return issued.token


def authenticates(engine: Engine, phone: str, password: str) -> bool:
    return _read(engine, lambda s: authenticate_member(s, phone, password)) is not None


def verify_payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"phone": PHONE_LOCAL, "phone_region": "MY", "code": CODE}
    body.update(overrides)
    return body


def without(name: str) -> dict[str, Any]:
    body = verify_payload()
    del body[name]
    return body


def redis_key(token: str) -> str:
    return KEY_PREFIX + hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def cookie_parts(cookie: str) -> tuple[str, str, list[str]]:
    """Set-Cookie 的（名称, 值, 小写的属性列表）。"""
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    name, value = name_value.split("=", 1)
    return name, value, [attribute.lower() for attribute in attributes]


def assert_error(response: Any, status_code: int, detail: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE


def assert_no_cookie(response: Any) -> None:
    assert response.headers.get_list("set-cookie") == []


def assert_no_secrets(response: Any, *extra: str) -> None:
    """响应体与响应头不含号码原文、验证码、密码、Redis 主机与给出的值（凭据等）。"""
    text = response.text + json.dumps(list(response.headers.items()))
    for value in ("123456789", "345 6789", CODE, "SECRET-PW", REDIS_HOST, *extra):
        assert value not in text


def verified(client: TestClient, engine: Engine) -> tuple[str, str]:
    """以已注册号码通过验证，返回（cookie 里的凭据原文, 响应里的 CSRF 令牌）。"""
    add_attempt(engine)
    response = client.post(VERIFY_URL, json=verify_payload())
    assert response.status_code == 200, response.text
    token = client.cookies.get(COOKIE_NAME)
    assert token
    return token, response.json()["csrf_token"]


def submit(client: TestClient, csrf: str | None, password: str = NEW_PASSWORD) -> Any:
    headers = {} if csrf is None else {"X-CSRF-Token": csrf}
    return client.post(RESET_URL, json={"password": password}, headers=headers)


# ---------------------------------------------------------------------------
# 验证：approved
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_registered_phone_gets_reset_token_and_cookie(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """UX P12“V1 验证通过后，号码已注册才显示新密码输入”；Kelvin 10-10“服务端签发一次性重设
    凭据，存于共享 Redis……10 分钟有效……经 HttpOnly、Secure、SameSite=Lax 的 cookie 只交给本
    浏览器”。验收：200 {"registered": true, "csrf_token": <由凭据算出>}；cookie 名
    __Host-shop_member_pw_reset，值为凭据原文，HttpOnly、Secure、SameSite=Lax、Path=/、不设
    Domain、Max-Age 600；Redis 里只有该凭据的摘要键，值为会员 ID、过期 600 秒；验证记录已提交
    为 approved；凭据原文不在响应体里；no-store。
    """
    member_id = add_member(engine)
    attempt_id = add_attempt(engine)

    response = client.post(VERIFY_URL, json=verify_payload())

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == NO_STORE
    (cookie,) = response.headers.get_list("set-cookie")
    name, token, attributes = cookie_parts(cookie)
    assert name == COOKIE_NAME
    assert len(token) == 43
    assert "httponly" in attributes
    assert "secure" in attributes
    assert "samesite=lax" in attributes
    assert "path=/" in attributes
    assert f"max-age={TEN_MINUTES}" in attributes
    assert not any(attribute.startswith("domain") for attribute in attributes)
    assert response.json() == {"registered": True, "csrf_token": csrf_for(token)}
    assert token not in response.text
    assert_no_secrets(response)
    assert fake.live_keys() == [redis_key(token)]
    assert fake.values[redis_key(token)] == str(member_id).encode()
    assert fake.ttl(redis_key(token)) == TEN_MINUTES
    assert attempt_status(engine, attempt_id) == "approved"


@pytest.mark.usefixtures("enabled")
def test_registered_phone_does_not_get_member_session(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """UX P12“重设完成 → 登录”（重设流程本身不登录）。验收：验证接口不签发会员会话——库里没有
    会员会话、不设会员会话 cookie，随后取当前会话为 401；密码与会员不变。
    """
    member_id = add_member(engine)
    verified(client, engine)

    assert sessions(engine) == []
    assert client.cookies.get(SESSION_COOKIE_NAME) is None
    assert client.get(SESSION_URL).status_code == 401
    assert members(engine) == [(member_id, PHONE_MY, OLD_HASH, "active")]


@pytest.mark.usefixtures("enabled")
def test_unregistered_phone_creates_nothing(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """UX P12“号码未注册时不创建账号”。验收：200 {"registered": false}，不建会员、不签发凭据、
    不设 cookie、不签发会员会话；验证记录已提交为 approved；另一号码的会员不受影响。
    """
    other_id = add_member(engine, phone=PHONE_SG)
    attempt_id = add_attempt(engine)

    response = client.post(VERIFY_URL, json=verify_payload())

    assert response.status_code == 200, response.text
    assert response.json() == {"registered": False}
    assert response.headers["cache-control"] == NO_STORE
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert members(engine) == [(other_id, PHONE_SG, OLD_HASH, "active")]
    assert sessions(engine) == []
    assert fake.live_keys() == []
    assert attempt_status(engine, attempt_id) == "approved"


@pytest.mark.usefixtures("enabled")
def test_deleted_member_phone_is_not_registered(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """UX P12“号码已注册才显示新密码输入”。验收：按号码找 status 为 active 的会员——已注销的
    会员（号码已清空）不算，200 {"registered": false}，不签发凭据、不设 cookie。
    """
    member_id = add_member(engine)
    delete_member(engine, member_id)
    add_attempt(engine)

    response = client.post(VERIFY_URL, json=verify_payload())

    assert response.status_code == 200, response.text
    assert response.json() == {"registered": False}
    assert_no_cookie(response)
    assert fake.live_keys() == []


@pytest.mark.usefixtures("enabled")
def test_other_purpose_attempt_does_not_verify_reset(
    client: TestClient, engine: Engine, provider: FakeSmsProvider, fake: FakeRedis
) -> None:
    """第 3 条“已有密码的重设再次短信验证”。验收：以用途 reset_password 调用 verify_code——
    只有登录用途的待核验记录时为 no_pending（422 code_expired），不调用服务商、不签发凭据。
    """
    add_member(engine)
    attempt_id = add_attempt(engine, purpose="login")

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 422, "code_expired")
    assert_no_cookie(response)
    assert provider.check_calls == 0
    assert fake.live_keys() == []
    assert attempt_status(engine, attempt_id) == "sent"


# ---------------------------------------------------------------------------
# 验证：不通过、开关、Redis 与数据库
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
    provider: FakeSmsProvider,
    fake: FakeRedis,
    check: CheckStatus,
    pending: bool,
    status_code: int,
    code: str,
    after: str | None,
) -> None:
    """UX P12“V1 验证通过后，号码已注册才显示新密码输入”（不通过不进入下一步）。验收：非
    approved 的结果与 sms-login 相同——wrong_code 422 code_wrong，expired 与 no_pending 422
    code_expired，unavailable 503 sms_unavailable；都先提交再回答（expired 时记录已提交为
    rejected）；不签发凭据、不设 cookie。
    """
    add_member(engine)
    provider.check_result = check
    attempt_id = add_attempt(engine) if pending else None

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, status_code, code)
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert fake.live_keys() == []
    assert sessions(engine) == []
    if attempt_id is not None:
        assert attempt_status(engine, attempt_id) == after


@pytest.mark.parametrize("setting", ["no_row", "off"])
def test_verify_sms_disabled_is_403_without_ping_or_check(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    fake: FakeRedis,
    setting: str,
) -> None:
    """第 3 条“短信验证开关关闭期间……密码重设暂停”；边界第 4 条“开关在流程途中被关闭后，提交
    的验证码不再核验……不重设密码”。验收：按当次从数据库读取的开关判定，关闭（含无设置行）即
    403 sms_disabled，不 PING、不调用服务商、不签发凭据。
    """
    add_member(engine)
    attempt_id = add_attempt(engine)
    if setting == "off":
        set_switch(engine, False)

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 403, "sms_disabled")
    assert_no_cookie(response)
    assert fake.pings == 0
    assert provider.check_calls == 0
    assert fake.live_keys() == []
    assert attempt_status(engine, attempt_id) == "sent"


def test_verify_sms_disabled_wins_over_failing_redis(
    client: TestClient, engine: Engine, provider: FakeSmsProvider, fake: FakeRedis
) -> None:
    """第 3 条“短信验证开关关闭期间……密码重设暂停”。验收：开关关闭且 Redis 故障（PING 会失败）
    时回答 403 sms_disabled 而不是 503——先判开关，不 PING。
    """
    add_attempt(engine)
    set_switch(engine, False)
    fake.fail = {"ping", "set", "execute"}

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 403, "sms_disabled")
    assert fake.pings == 0
    assert provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
def test_verify_redis_not_configured_is_503_without_check(
    app: FastAPI, client: TestClient, engine: Engine, provider: FakeSmsProvider
) -> None:
    """Kelvin 10-10“Redis 不可用时重设暂停”。验收：Redis 未配置时由路由类回答 503
    service_unavailable，不核验、不消耗验证码（不调用服务商，记录仍为 sent），不设 cookie。
    """
    del app.dependency_overrides[get_redis_client]
    add_member(engine)
    attempt_id = add_attempt(engine)

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 503, "service_unavailable")
    assert_no_cookie(response)
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_verify_ping_failure_is_503_without_check(
    client: TestClient, engine: Engine, provider: FakeSmsProvider, fake: FakeRedis
) -> None:
    """Kelvin 10-10“Redis 不可用时重设暂停”。验收：已取得客户端但 PING 失败时 503
    service_unavailable，不核验、不消耗验证码（不调用服务商，记录仍为 sent），不设 cookie；
    错误不含 Redis 主机。
    """
    add_member(engine)
    attempt_id = add_attempt(engine)
    fake.fail = {"ping"}

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 503, "service_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert fake.pings == 1
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_issue_failure_after_commit_is_503_without_cookie(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """Kelvin 10-10“Redis 不可用时重设暂停……凭据丢失只需重新验证”。验收：PING 之后、提交之后
    签发凭据时 Redis 出错为 503 service_unavailable、不设 cookie；验证记录已为 approved（访客须
    重新发送验证码）。
    """
    add_member(engine)
    attempt_id = add_attempt(engine)
    fake.fail = {"set"}

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 503, "service_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert fake.live_keys() == []
    assert attempt_status(engine, attempt_id) == "approved"


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("failure", ["provider", "commit"])
def test_verify_database_or_provider_error_is_503_sms_unavailable(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    session_class: list[type[Session]],
    fake: FakeRedis,
    failure: str,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码”。验收：数据库出错（含核验函数的
    SmsVerificationError 与提交失败）时回滚，503 sms_unavailable；不签发凭据、不设 cookie，
    记录仍为 sent；错误不含号码与验证码。
    """
    add_member(engine)
    attempt_id = add_attempt(engine)
    if failure == "provider":
        app.dependency_overrides[get_sms_provider] = lambda: RaisingProvider()
    else:
        session_class[0] = CommitFailingSession

    response = client.post(VERIFY_URL, json=verify_payload())

    assert_error(response, 503, "sms_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert fake.live_keys() == []
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_verify_ignores_member_session(client: TestClient, engine: Engine) -> None:
    """UX P12 忘记密码面向未登录访客。验收：验证接口不需要也不读会员会话——带着不存在的会员
    会话 cookie 照常通过，不回答 401。
    """
    add_member(engine)
    add_attempt(engine)
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={'A' * 43}"}

    response = client.post(VERIFY_URL, json=verify_payload(), headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["registered"] is True


# ---------------------------------------------------------------------------
# 验证：413 → 415 → 422
# ---------------------------------------------------------------------------


def _padded(body: dict[str, Any], size: int) -> bytes:
    """合法请求体，末尾以空白补到 size 字节（JSON 允许）。"""
    raw = json.dumps(body).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


@pytest.mark.usefixtures("enabled")
def test_verify_body_at_4_kb_is_accepted_and_one_byte_more_is_413(
    client: TestClient, engine: Engine, provider: FakeSmsProvider, fake: FakeRedis
) -> None:
    """验收：请求体逐块读，超过 4 KB 为 413，先于一切（不 PING、不调用服务商）；恰好 4 KB 放行。"""
    add_member(engine)
    add_attempt(engine)
    headers = {"Content-Type": "application/json"}

    over = client.post(
        VERIFY_URL, content=_padded(verify_payload(), MAX_BODY_BYTES + 1), headers=headers
    )
    assert_error(over, 413, "request body too large")
    assert fake.pings == 0
    assert provider.check_calls == 0

    at_limit = client.post(
        VERIFY_URL, content=_padded(verify_payload(), MAX_BODY_BYTES), headers=headers
    )
    assert at_limit.status_code == 200, at_limit.text


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/json"],
    ids=["not-json", "no-content-type", "json"],
)
@pytest.mark.parametrize("url", [VERIFY_URL, RESET_URL], ids=["verify", "reset"])
def test_oversized_body_is_413_before_everything(
    app: FastAPI, client: TestClient, fake: FakeRedis, content_type: str | None, url: str
) -> None:
    """验收：两个接口的 413（逐块读，超过 4 KB）先于 415、422 与一切——开关关闭（无设置行）、
    没有重设 cookie、Redis 未配置时都是 413；分块发送、不带 Content-Length 也按实际读到的字节
    判断；不回显请求内容。
    """
    del app.dependency_overrides[get_redis_client]
    chunks = [b'{"phone": "' + b"9" * 3000, b'", "password": "SECRET-PW' + b"x" * 3000 + b'"}']
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = client.post(url, content=iter(chunks), headers=headers)

    assert_error(response, 413, "request body too large")
    assert "999" not in response.text
    assert_no_secrets(response)
    assert fake.pings == 0


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"],
)
@pytest.mark.parametrize("url", [VERIFY_URL, RESET_URL], ids=["verify", "reset"])
def test_not_json_is_415_before_structure(
    client: TestClient,
    provider: FakeSmsProvider,
    fake: FakeRedis,
    content_type: str | None,
    url: str,
) -> None:
    """验收：两个接口不是 JSON 都为 415，在 413 之后、422 与开关之前（合法与多出字段的请求体、
    开关关闭时都是 415）；不回显请求内容。
    """
    bodies = (
        [verify_payload(), verify_payload(extra="SECRET-PW")]
        if url == VERIFY_URL
        else [{"password": NEW_PASSWORD}, {"password": NEW_PASSWORD, "extra": "x"}]
    )
    for body in bodies:
        headers = {} if content_type is None else {"Content-Type": content_type}
        response = client.post(url, content=json.dumps(body).encode(), headers=headers)
        assert_error(response, 415, "request body must be JSON")
        assert_no_secrets(response)
    assert fake.pings == 0
    assert provider.check_calls == 0


VERIFY_STRUCTURE_CASES = [
    pytest.param(without("phone"), ("body", "phone"), "missing", id="missing-phone"),
    pytest.param(without("phone_region"), ("body", "phone_region"), "missing", id="missing-region"),
    pytest.param(without("code"), ("body", "code"), "missing", id="missing-code"),
    pytest.param(
        verify_payload(purpose="reset_password"),
        ("body", "purpose"),
        "extra_forbidden",
        id="purpose",
    ),
    pytest.param(
        verify_payload(member_id="SECRET-EXTRA"),
        ("body", "member_id"),
        "extra_forbidden",
        id="extra",
    ),
    pytest.param(verify_payload(phone=60123456789), ("body", "phone"), "string_type", id="phone"),
    pytest.param(
        verify_payload(phone_region=None),
        ("body", "phone_region"),
        "string_type",
        id="region-none",
    ),
    pytest.param(verify_payload(code=482915), ("body", "code"), "string_type", id="code-int"),
    pytest.param(verify_payload(code=""), ("body", "code"), "string_too_short", id="code-empty"),
    pytest.param(verify_payload(code="4" * 17), ("body", "code"), "string_too_long", id="code-17"),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("body", "loc", "error_type"), VERIFY_STRUCTURE_CASES)
def test_verify_structure_errors_are_422_without_echo(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    fake: FakeRedis,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """验收：验证接口的请求体只有 phone、phone_region 与 code（1 到 16 个字符的字符串），都必填、
    严格类型，多出字段（含 purpose）422；沿用 _body_errors，只给位置、类型与固定消息，不回显
    请求内容；排在 Redis 之前（不 PING），不调用服务商、不设 cookie。
    """
    attempt_id = add_attempt(engine)

    response = client.post(VERIFY_URL, json=body)

    assert response.status_code == 422
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    for secret in ("SECRET", "345 6789", "4" * 17, CODE):
        assert secret not in response.text
    assert_no_cookie(response)
    assert fake.pings == 0
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "region"),
    [("0123", "MY"), ("SECRET", "MY"), ("012-345 6789", "my"), ("012-345 6789", "XX"), ("", "MY")],
)
def test_verify_invalid_phone_is_422_phone_invalid(
    client: TestClient, provider: FakeSmsProvider, fake: FakeRedis, phone: str, region: str
) -> None:
    """验收：phone、phone_region 的规范化与 sms-login 相同，不成立 422 phone_invalid，不回显
    请求内容，不 PING、不调用服务商。
    """
    response = client.post(VERIFY_URL, json=verify_payload(phone=phone, phone_region=region))

    expected = {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    assert_error(response, 422, [expected])
    assert "SECRET" not in response.text
    assert fake.pings == 0
    assert provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
def test_verify_phone_is_normalized_like_sms_login(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """验收：号码规范化与 sms-login 相同（以加号开头时以输入为准），核验与找会员都用规范化
    结果——新加坡号码以 SG 地区写本地格式即可找到会员。
    """
    member_id = add_member(engine, phone=PHONE_SG)
    add_attempt(engine, phone=PHONE_SG)

    response = client.post(VERIFY_URL, json=verify_payload(phone="8123 4567", phone_region="SG"))

    assert response.status_code == 200, response.text
    assert response.json()["registered"] is True
    (key,) = fake.live_keys()
    assert fake.values[key] == str(member_id).encode()


# ---------------------------------------------------------------------------
# 提交新密码：成功
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_reset_changes_password_revokes_sessions_and_clears_cookie(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """第 3 条“已有密码的重设再次短信验证”；Kelvin 10-08“重设密码……时撤销该会员的全部会话”；
    Kelvin 10-10“使用一次即删除”；UX P12“重设完成 → 登录”。验收：204 且响应体为空；旧密码不能、
    新密码能经 authenticate_member 通过；该会员原有的会员会话已撤销（取当前会话 401），其他会员
    的会话不受影响；重设 cookie 已清除（名称、Path、Secure、HttpOnly、SameSite 同设置时，不设
    Domain，Max-Age 0）；Redis 里的凭据已删除；不签发会员会话；no-store。
    """
    member_id = add_member(engine)
    other_id = add_member(engine, phone=PHONE_SG)
    old_session = stored_session(engine, member_id)
    other_session = stored_session(engine, other_id)
    token, csrf = verified(client, engine)

    response = submit(client, csrf)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    (cookie,) = response.headers.get_list("set-cookie")
    name, _, attributes = cookie_parts(cookie)
    assert name == COOKIE_NAME
    assert token not in cookie
    assert "max-age=0" in attributes
    assert "httponly" in attributes
    assert "secure" in attributes
    assert "samesite=lax" in attributes
    assert "path=/" in attributes
    assert not any(attribute.startswith("domain") for attribute in attributes)
    assert client.cookies.get(COOKIE_NAME) is None
    assert client.cookies.get(SESSION_COOKIE_NAME) is None

    assert not authenticates(engine, PHONE_MY, OLD_PASSWORD)
    assert authenticates(engine, PHONE_MY, NEW_PASSWORD)
    assert authenticates(engine, PHONE_SG, OLD_PASSWORD)
    assert fake.live_keys() == []
    revoked = dict(sessions(engine))
    assert revoked[member_id] is not None
    assert revoked[other_id] is None
    session_cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={old_session}"}
    assert fresh(client).get(SESSION_URL, headers=session_cookie).status_code == 401
    other_cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={other_session}"}
    assert fresh(client).get(SESSION_URL, headers=other_cookie).status_code == 200
    assert len(sessions(engine)) == 2


@pytest.mark.usefixtures("enabled")
def test_member_without_password_can_set_one(client: TestClient, engine: Engine) -> None:
    """UX P12“未设过密码的账号验证通过后同样可设置（等同首次设密码）”。验收：未设过密码的会员
    经验证与提交后 204，新密码可经 authenticate_member 通过。
    """
    member_id = add_member(engine, password_hash=None)
    _, csrf = verified(client, engine)

    response = submit(client, csrf)

    assert response.status_code == 204, response.text
    assert password_hash_of(engine, member_id) is not None
    assert authenticates(engine, PHONE_MY, NEW_PASSWORD)


@pytest.mark.usefixtures("enabled")
def test_same_token_second_submit_is_401(client: TestClient, engine: Engine) -> None:
    """Kelvin 10-10“一次性重设凭据……使用一次即删除”。验收：同一凭据第二次提交（重新附上已被
    清除的 cookie 与原 CSRF 令牌）401 reset_expired；密码仍是第一次设的。
    """
    add_member(engine)
    token, csrf = verified(client, engine)
    assert submit(client, csrf).status_code == 204

    again = fresh(client).post(
        RESET_URL,
        json={"password": OTHER_PASSWORD},
        headers={**cookie_header(token), "X-CSRF-Token": csrf},
    )

    assert_error(again, 401, "reset_expired")
    assert_no_cookie(again)
    assert_no_secrets(again, token)
    assert authenticates(engine, PHONE_MY, NEW_PASSWORD)
    assert not authenticates(engine, PHONE_MY, OTHER_PASSWORD)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("elapsed", "status_code"), [(TEN_MINUTES - 1, 204), (TEN_MINUTES, 401)])
def test_token_expires_after_ten_minutes(
    client: TestClient, engine: Engine, fake: FakeRedis, elapsed: int, status_code: int
) -> None:
    """Kelvin 10-10“10 分钟有效”。验收：凭据过期后 401 reset_expired——差 1 秒到 10 分钟仍可
    重设，满 10 分钟起 401 且密码不变。
    """
    add_member(engine)
    _, csrf = verified(client, engine)
    fake.advance(elapsed)

    response = submit(client, csrf)

    assert response.status_code == status_code, response.text
    if status_code == 401:
        assert_error(response, 401, "reset_expired")
        assert authenticates(engine, PHONE_MY, OLD_PASSWORD)


@pytest.mark.usefixtures("enabled")
def test_member_deleted_after_verify_is_401(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """Kelvin 10-10“凭据丢失只需重新验证”（注销后不再可重设）。验收：会员在验证后注销时，重设
    函数返回 False，401 reset_expired；会员仍为已注销、密码哈希仍为空；凭据已取用、不能再用。
    """
    member_id = add_member(engine)
    _, csrf = verified(client, engine)
    delete_member(engine, member_id)

    response = submit(client, csrf)

    assert_error(response, 401, "reset_expired")
    ((_, phone, password_hash, status),) = members(engine)
    assert (phone, password_hash, status) == (None, None, "deleted")
    assert fake.live_keys() == []


# ---------------------------------------------------------------------------
# 提交新密码：检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_switch_off_at_submit_is_403_and_token_survives(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """第 3 条“短信验证开关关闭期间……密码重设暂停”；边界第 4 条“开关在流程途中被关闭后……不重设
    密码”。验收：提交时按当次读取的开关判定，关闭即 403 sms_disabled、不取用凭据（密码不变）；
    开关重新开启后同一凭据仍可用。
    """
    add_member(engine)
    token, csrf = verified(client, engine)
    set_switch(engine, False)

    response = submit(client, csrf)

    assert_error(response, 403, "sms_disabled")
    assert_no_cookie(response)
    assert fake.live_keys() == [redis_key(token)]
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)

    set_switch(engine, True)
    assert submit(client, csrf).status_code == 204


@pytest.mark.usefixtures("enabled")
def test_switch_off_wins_over_failing_redis_at_submit(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """第 3 条“短信验证开关关闭期间……密码重设暂停”。验收：开关检查先于 Redis，开关关闭且 Redis
    故障时提交为 403 sms_disabled 而不是 503。
    """
    add_member(engine)
    _, csrf = verified(client, engine)
    set_switch(engine, False)
    fake.fail = {"ping", "set", "execute"}

    assert_error(submit(client, csrf), 403, "sms_disabled")


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("csrf_header", ["missing", "valid"])
def test_no_cookie_is_401_not_403(
    client: TestClient, engine: Engine, fake: FakeRedis, csrf_header: str
) -> None:
    """Kelvin 10-10“经……cookie 只交给本浏览器”。验收：没有重设 cookie 时 401 reset_expired，
    先于 CSRF（不带 CSRF 令牌也是 401 而不是 403）；另一浏览器拿着 CSRF 令牌也不能用，原凭据
    不被取用。
    """
    add_member(engine)
    token, csrf = verified(client, engine)

    response = submit(fresh(client), csrf if csrf_header == "valid" else None)

    assert_error(response, 401, "reset_expired")
    assert_no_cookie(response)
    assert fake.live_keys() == [redis_key(token)]
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("cookie", ["not-a-token", "!" * 43, "A" * 42, ""])
def test_malformed_cookie_is_401(
    app: FastAPI, client: TestClient, fake: FakeRedis, cookie: str
) -> None:
    """Kelvin 10-10“一次性重设凭据”。验收：重设 cookie 格式不对时 401 reset_expired，先于 CSRF
    与 Redis（Redis 未配置也是 401），不回显 cookie 值。
    """
    del app.dependency_overrides[get_redis_client]
    headers = {"Cookie": f"{COOKIE_NAME}={cookie}", "X-CSRF-Token": "0" * 64}

    response = client.post(RESET_URL, json={"password": NEW_PASSWORD}, headers=headers)

    assert_error(response, 401, "reset_expired")
    if cookie:
        assert cookie not in response.text


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated", "member_session_prefix"])
def test_bad_csrf_is_403_and_token_survives(
    client: TestClient, engine: Engine, fake: FakeRedis, csrf: str
) -> None:
    """Kelvin 10-10“提交新密码另须 CSRF 令牌”。验收：X-CSRF-Token 须恰好一个且与由 cookie 里的
    凭据算出的值一致——缺失、错误、重复（即使都正确）或用会员会话前缀算出的值都 403
    csrf_failed，不取用凭据，之后带正确令牌仍可重设。
    """
    add_member(engine)
    token, good = verified(client, engine)
    body = {"password": NEW_PASSWORD}
    if csrf == "missing":
        response = client.post(RESET_URL, json=body)
    elif csrf == "wrong":
        response = client.post(RESET_URL, json=body, headers={"X-CSRF-Token": csrf_for("B" * 43)})
    elif csrf == "duplicated":
        headers = [("X-CSRF-Token", good), ("X-CSRF-Token", good)]
        response = client.post(RESET_URL, json=body, headers=headers)
    else:
        value = hashlib.sha256(SESSION_CSRF_PREFIX + token.encode()).hexdigest()
        response = client.post(RESET_URL, json=body, headers={"X-CSRF-Token": value})

    assert_error(response, 403, "csrf_failed")
    assert_no_cookie(response)
    assert_no_secrets(response, token)
    assert fake.live_keys() == [redis_key(token)]
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)

    assert submit(client, good).status_code == 204


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("password", ["1234567", "short", "x"])
def test_short_password_is_422_and_token_survives(
    client: TestClient, engine: Engine, fake: FakeRedis, password: str
) -> None:
    """第 3 条“密码至少 8 位、不强制复杂度”。验收：不满足 password_length_ok 时 422
    {"detail": "password_too_short"}，不取用凭据、可改后重交（8 个字符即可）。
    """
    add_member(engine)
    token, csrf = verified(client, engine)

    response = submit(client, csrf, password)

    assert_error(response, 422, "password_too_short")
    assert fake.live_keys() == [redis_key(token)]
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)

    assert submit(client, csrf, "12345678").status_code == 204
    assert authenticates(engine, PHONE_MY, "12345678")


RESET_STRUCTURE_CASES = [
    pytest.param({}, ("body", "password"), "missing", id="missing"),
    pytest.param(
        {"password": NEW_PASSWORD, "member_id": "SECRET-EXTRA"},
        ("body", "member_id"),
        "extra_forbidden",
        id="extra",
    ),
    pytest.param({"password": 12345678}, ("body", "password"), "string_type", id="int"),
    pytest.param({"password": ""}, ("body", "password"), "string_too_short", id="empty"),
    pytest.param(
        {"password": "SECRET-PW" + "x" * 248}, ("body", "password"), "string_too_long", id="257"
    ),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.parametrize(("body", "loc", "error_type"), RESET_STRUCTURE_CASES)
def test_reset_structure_errors_are_422_before_switch_and_cookie(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """验收：提交接口的请求体只有 password（1 到 256 个字符的字符串），多出字段 422；结构检查
    先于开关（关闭）与 cookie（没有）；沿用 _body_errors，不回显请求内容。
    """
    set_switch(engine, False)

    response = client.post(RESET_URL, json=body)

    assert response.status_code == 422
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    assert "SECRET" not in response.text
    assert fake.pings == 0


@pytest.mark.usefixtures("enabled")
def test_reset_accepts_256_characters(client: TestClient, engine: Engine) -> None:
    """第 3 条“不强制复杂度”。验收：password 上限 256 个字符，恰好 256 个可重设。"""
    add_member(engine)
    _, csrf = verified(client, engine)
    password = "密" * 256

    assert submit(client, csrf, password).status_code == 204
    assert authenticates(engine, PHONE_MY, password)


@pytest.mark.usefixtures("enabled")
def test_check_order_between_steps(
    app: FastAPI, client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """验收：检查顺序为 开关 → cookie → CSRF → 长度 → Redis 客户端——开关关闭且没有 cookie 为
    403 sms_disabled；没有 cookie 且没有 CSRF 为 401；CSRF 错且密码太短为 403 csrf_failed；密码
    太短且 Redis 未配置为 422；其余都通过而 Redis 未配置为 503 service_unavailable，凭据都未被
    取用。
    """
    add_member(engine)
    token, csrf = verified(client, engine)
    other = fresh(client)

    set_switch(engine, False)
    assert_error(submit(other, None), 403, "sms_disabled")
    set_switch(engine, True)
    assert_error(submit(other, None), 401, "reset_expired")
    assert_error(submit(client, csrf_for("B" * 43), "short"), 403, "csrf_failed")
    del app.dependency_overrides[get_redis_client]
    assert_error(submit(client, csrf, "short"), 422, "password_too_short")
    assert_error(submit(client, csrf), 503, "service_unavailable")

    assert fake.live_keys() == [redis_key(token)]
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)


# ---------------------------------------------------------------------------
# 提交新密码：Redis 与数据库出错
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_consume_failure_is_503_without_change(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """Kelvin 10-10“Redis 不可用时重设暂停”。验收：取用凭据时 Redis 出错为 503
    service_unavailable，不改密码、不清 cookie；错误不含 Redis 主机与凭据。
    """
    add_member(engine)
    token, csrf = verified(client, engine)
    fake.fail = {"execute"}

    response = submit(client, csrf)

    assert_error(response, 503, "service_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response, token)
    assert authenticates(engine, PHONE_MY, OLD_PASSWORD)


@pytest.mark.usefixtures("enabled")
def test_commit_failure_after_consume_is_503_and_token_gone(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    session_class: list[type[Session]],
) -> None:
    """Kelvin 10-10“凭据丢失只需重新验证”。验收：取用之后数据库出错时回滚，503
    service_unavailable；密码与会员会话都不变；凭据已删，同一凭据再交为 401 reset_expired。
    """
    member_id = add_member(engine)
    stored_session(engine, member_id)
    _, csrf = verified(client, engine)
    session_class[0] = CommitFailingSession

    response = submit(client, csrf)

    assert_error(response, 503, "service_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert password_hash_of(engine, member_id) == OLD_HASH
    assert sessions(engine) == [(member_id, None)]
    assert fake.live_keys() == []

    session_class[0] = Session
    assert_error(submit(client, csrf), 401, "reset_expired")


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "url"), [("GET", VERIFY_URL), ("GET", RESET_URL)])
def test_wrong_method_is_405_without_request_content(
    client: TestClient, method: str, url: str
) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.request(
        method, url, params={"phone": PHONE_MY, "code": CODE, "password": NEW_PASSWORD}
    )

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert CODE not in response.text
    assert NEW_PASSWORD not in response.text


@pytest.mark.usefixtures("enabled")
def test_no_logs_with_phone_code_token_or_password(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码……密码”。验收：凭据原文不出现在日志
    里——验证、错误的 CSRF、过短与成功的提交、验证码错误与结构错误都不产生 app 下的日志，任何
    日志都不含号码、验证码、凭据、CSRF 令牌与密码。
    """
    caplog.set_level(logging.DEBUG)
    add_member(engine)

    token, csrf = verified(client, engine)
    submit(client, csrf_for("B" * 43))
    submit(client, csrf, "short")
    submit(client, csrf)
    provider.check_result = CheckStatus.WRONG_CODE
    add_attempt(engine)
    client.post(VERIFY_URL, json=verify_payload())
    client.post(VERIFY_URL, json=verify_payload(extra=CODE))

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", CODE, token, csrf, NEW_PASSWORD, "SECRET-PW"):
            assert value not in message

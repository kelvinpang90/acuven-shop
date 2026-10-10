"""确认会员注销的接口：POST /api/member/delete（app/api/member_deletion.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：
  “账号注销须先以短信验证码确认（自动注册的账号可能从未设过密码），再撤销会话、删除手机号
  与密码”；“短信验证开关关闭期间……已设密码的会员注销时输入当前密码确认，未设密码的会员在
  已登录会话内经二次确认后注销”；“注销……并把其订单上的会员 ID 清空以解除关联”；第 5 条
  （下称「第 5 条」）：会话以服务端会话实现，写操作另须 CSRF 令牌；第 6 条（下称「第 6 条」）：
  “应用日志与监控不记录……完整电话……密码”。
- 「边界与原则」第 4 条（下称「边界第 4 条」）：“下单、发送短信、提交验证码与确认注销时，
  服务端都按当时读取的开关值判定：开关在流程途中被关闭后……也不能以短信完成注销确认”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（3）（下称「Kelvin 10-08」）：“同一号码加
  来源 15 分钟内密码登录失败 5 次即锁 15 分钟”，“注销时撤销该会员的全部会话”。
- docs/HANDOFF.md 0.45 记录的 Kelvin 2026-10-10 决定（下称「Kelvin 10-10」）：（1）“注销验证码
  核验通过后，服务端……记一条绑定当前会员会话的一次性批准，10 分钟有效、使用一次即删除，确认
  注销时取用”；（2）“开关关闭时，已设密码的会员以当前密码确认注销，输错次数与密码登录共用同一
  组锁定计数……登录与注销互相计入”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-082 验收标准里的约定（下称「验收」）。

照 tests/test_member_deletion_api.py 的写法：SQLite 内存库按模型建表，接口的会话依赖换成每个
请求一个绑定同一内存库的会话（可换成提交即抛错的子类），检查结果一律在另一个数据库会话里读；
服务商用 SHOP-TASK-067 的 FakeSmsProvider；Redis 用本文件的内存替身 FakeRedis（PING、GET、带 EX
的 SET、事务管道 GET、DEL、INCR 与 EXPIRE，按命令注入的连接错误；未加依赖）。核验与密码登录
接口经 dependency_overrides[get_redis_client] 拿到同一个替身；确认注销按确认方式在处理函数里
直接调用 get_redis_client（不声明为依赖），这里以 monkeypatch 把模块里的这个名字换成记次数的
RedisGetter，可切换为「未配置」（调用真实的 get_redis_client，连接串为空即抛错）。会员与会员会话
直接写库；CSRF 令牌在这里另算。访客来源一律用 X-Real-IP 指定。
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
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import member_deletion as deletion_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, MemberSession, Order, SiteSetting, VerificationAttempt
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.models.site import SITE_SETTING_ID
from app.services.member_auth import issue_member_session
from app.services.pw_hash import hash_password
from app.services.rate_limit import get_redis_client
from app.services.sms_provider import FakeSmsProvider, get_sms_provider

DELETE_URL = "/api/member/delete"
VERIFY_URL = "/api/member/delete/verify"
LOGIN_URL = "/api/member/password-login"
SESSION_URL = "/api/member/session"
MAX_BODY_BYTES = 4 * 1024

SESSION_COOKIE_NAME = "__Host-shop_member_session"
CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"
APPROVAL_PREFIX = "acuven_shop:member_delete_approval:"
RATE_LIMIT_PREFIX = "acuven_shop:rate_limit:"

PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
CODE = "482915"
PASSWORD = "SECRET-PW correct horse"
PASSWORD_HASH = hash_password(PASSWORD)
WRONG_PASSWORD = "SECRET-PW wrong horse"
SOURCE = "203.0.113.5"
OTHER_SOURCE = "198.51.100.20"
REDIS_HOST = "redis-host:6379"
FINGERPRINT = "0" * 64
# Kelvin 10-08：15 分钟内失败 5 次即锁。
FAILURES_TO_LOCK = 5

NO_STORE = "no-store"

_REQUEST_IDS = itertools.count(1)
_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻与按命令注入的错误。

    fail 里含 "ping"、"get"、"set" 或 "execute" 时，该命令抛带主机与端口的连接错误。
    """

    def __init__(self) -> None:
        self.now = 0
        self.values: dict[str, Any] = {}
        self.expires: dict[str, int] = {}
        self.fail: set[str] = set()

    def _maybe_fail(self, command: str) -> None:
        if command in self.fail:
            raise redis.exceptions.ConnectionError(f"Error connecting to {REDIS_HOST}")

    def _purge(self, key: str) -> None:
        expires = self.expires.get(key)
        if expires is not None and expires <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def approval_keys(self) -> list[str]:
        """未过期的注销批准键。"""
        for key in list(self.values):
            self._purge(key)
        return sorted(key for key in self.values if key.startswith(APPROVAL_PREFIX))

    def rate_limit_keys(self) -> list[str]:
        """限流计数键（失败计数与锁定桶）。"""
        return sorted(key for key in self.values if key.startswith(RATE_LIMIT_PREFIX))

    def ping(self) -> bool:
        self._maybe_fail("ping")
        return True

    def get(self, key: str) -> bytes | None:
        self._maybe_fail("get")
        self._purge(key)
        value = self.values.get(key)
        return None if value is None else str(value).encode()

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._maybe_fail("set")
        self.values[key] = str(value)
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
                value = self.store.values.get(key)
                results.append(None if value is None else str(value).encode())
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


class RedisGetter:
    """换掉 app/api/member_deletion.py 里的 get_redis_client：记调用次数，可切换为未配置。"""

    def __init__(self, fake: FakeRedis) -> None:
        self.fake = fake
        self.configured = True
        self.calls = 0

    def __call__(self, request: Request) -> Any:
        self.calls += 1
        if not self.configured:
            # 真实的依赖函数：测试设置的连接串为空，抛 RateLimitUnavailable。
            return get_redis_client(request)
        return self.fake


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
    # 连接串显式为空：不覆盖依赖时即「未配置」。
    return Settings(_env_file=None, database_url="", redis_url="")


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
def redis_getter(fake: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> RedisGetter:
    getter = RedisGetter(fake)
    monkeypatch.setattr(deletion_api, "get_redis_client", getter)
    return getter


@pytest.fixture
def app(
    engine: Engine,
    fake: FakeRedis,
    provider: FakeSmsProvider,
    session_class: list[type[Session]],
    redis_getter: RedisGetter,
) -> FastAPI:
    app = create_app(make_settings())

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    # 核验（SHOP-TASK-081）与密码登录（SHOP-TASK-077）经依赖取 Redis 客户端：同一个替身。
    app.dependency_overrides[get_redis_client] = lambda: fake
    app.dependency_overrides[get_sms_provider] = lambda: provider
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def enabled(engine: Engine) -> None:
    set_switch(engine, True)


@pytest.fixture
def disabled(engine: Engine) -> None:
    set_switch(engine, False)


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


def add_member(engine: Engine, phone: str = PHONE_MY, password_hash: str | None = None) -> int:
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


def add_order(engine: Engine, member_id: int) -> int:
    """一张会员订单（不可认领）；直接写库。"""
    n = next(_ORDER_SEQUENCE)
    created_at = _now() - timedelta(days=3)
    with Session(engine) as session:
        result = session.execute(
            insert(Order).values(
                order_number=f"{n:016d}",
                status=STATUS_AWAITING_PAYMENT,
                subtotal_sen=2500,
                coupon_discount_sen=0,
                points_redeemed=0,
                shipping_fee_sen=800,
                total_sen=3300,
                points_earned=0,
                shipping_zone_code="MY-10",
                shipping_rate_version=1,
                idempotency_key=f"order-key-{n}",
                request_fingerprint=FINGERPRINT,
                created_at=created_at,
                payment_expires_at=created_at + timedelta(minutes=15),
                paid_at=None,
                member_id=member_id,
                claim_status=CLAIM_NOT_CLAIMABLE,
            )
        )
        session.commit()
        return result.inserted_primary_key[0]


def orders(engine: Engine) -> list[tuple[int, int | None, str]]:
    """全部订单的（ID, 会员 ID, 认领状态），按 ID。"""
    stmt = select(Order.id, Order.member_id, Order.claim_status).order_by(Order.id)
    return _read(engine, lambda s: [tuple(row) for row in s.execute(stmt).all()])


def add_attempt(engine: Engine, phone: str = PHONE_MY) -> int:
    """一条一分钟前发出、待核验（sent）的注销验证记录。"""
    created_at = _now() - timedelta(minutes=1)
    with Session(engine) as session:
        result = session.execute(
            insert(VerificationAttempt).values(
                phone=phone,
                purpose="delete_account",
                status="sent",
                provider_request_id=f"VE{next(_REQUEST_IDS):032x}",
                created_at=created_at,
                updated_at=created_at,
            )
        )
        session.commit()
        return result.inserted_primary_key[0]


def member_row(engine: Engine, member_id: int) -> tuple[str | None, str | None, str, bool]:
    """会员的（号码, 密码哈希, 状态, 是否有注销时间）。"""
    stmt = select(Member.phone, Member.password_hash, Member.status, Member.deleted_at).where(
        Member.id == member_id
    )
    phone, password_hash, status, deleted_at = _read(engine, lambda s: s.execute(stmt).one())
    return phone, password_hash, status, deleted_at is not None


def is_active(engine: Engine, member_id: int) -> bool:
    return member_row(engine, member_id)[2] == "active"


def assert_deleted(engine: Engine, member_id: int) -> None:
    assert member_row(engine, member_id) == (None, None, "deleted", True)


def revoked_sessions(engine: Engine) -> list[int]:
    stmt = select(MemberSession.id).where(MemberSession.revoked_at.is_not(None))
    return _read(engine, lambda s: sorted(s.scalars(stmt)))


def csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def auth(token: str | None, csrf: str | None = "auto", source: str = SOURCE) -> dict[str, str]:
    """会员会话 cookie、CSRF 请求头与访客来源；csrf 为 "auto" 时按 cookie 另算。"""
    headers: dict[str, str] = {"X-Real-IP": source}
    if token is not None:
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={token}"
        if csrf == "auto":
            headers["X-CSRF-Token"] = csrf_for(token)
    if csrf not in (None, "auto"):
        headers["X-CSRF-Token"] = csrf
    return headers


def delete(
    client: TestClient,
    token: str | None,
    csrf: str | None = "auto",
    source: str = SOURCE,
    **body: Any,
) -> Any:
    return client.post(DELETE_URL, json=body, headers=auth(token, csrf, source))


def verify(client: TestClient, token: str) -> Any:
    return client.post(VERIFY_URL, json={"code": CODE}, headers=auth(token))


def password_login(client: TestClient, password: str, source: str = SOURCE) -> Any:
    body = {"phone": PHONE_MY, "phone_region": "MY", "password": password}
    response = client.post(LOGIN_URL, json=body, headers={"X-Real-IP": source})
    # 登录成功时 TestClient 会记下 cookie；清掉，之后的请求只带测试显式给出的 cookie。
    client.cookies.clear()
    return response


def assert_error(response: Any, status_code: int, detail: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []


def assert_no_secrets(response: Any, *extra: str) -> None:
    """响应体与响应头不含号码原文、密码、Redis 主机与给出的值。"""
    text = response.text + json.dumps(list(response.headers.items()))
    for value in ("123456789", "SECRET-PW", REDIS_HOST, *extra):
        assert value not in text


def assert_deleted_response(response: Any, token: str) -> None:
    """204、响应体为空、no-store，会员会话 cookie 以 Max-Age 0 清除。"""
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    (cookie,) = response.headers.get_list("set-cookie")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    assert name_value.split("=", 1)[0] == SESSION_COOKIE_NAME
    assert token not in cookie
    lowered = [attribute.lower() for attribute in attributes]
    assert "max-age=0" in lowered
    assert "path=/" in lowered
    assert "secure" in lowered
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert_no_secrets(response, token)


@pytest.fixture
def member(engine: Engine) -> int:
    """未设密码的会员。"""
    return add_member(engine)


@pytest.fixture
def pw_member(engine: Engine) -> int:
    """已设密码的会员。"""
    return add_member(engine, password_hash=PASSWORD_HASH)


# ---------------------------------------------------------------------------
# 开关开启：注销批准
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_switch_on_without_approval_is_403_delete_not_verified(
    client: TestClient, engine: Engine, member: int
) -> None:
    """第 3 条“账号注销须先以短信验证码确认”；Kelvin 10-10（1）“确认注销时取用”。验收：开关开启
    时取不到当前会话与会员的注销批准即 403 delete_not_verified；会员仍为 active，会话仍有效。
    """
    token, _ = login(engine, member)

    response = delete(client, token)

    assert_error(response, 403, "delete_not_verified")
    assert_no_secrets(response, token)
    assert is_active(engine, member)
    assert revoked_sessions(engine) == []
    assert client.get(SESSION_URL, headers=auth(token, None)).status_code == 200


@pytest.mark.usefixtures("enabled")
def test_switch_on_approval_from_verify_deletes_member(
    client: TestClient, engine: Engine, fake: FakeRedis, member: int
) -> None:
    """第 3 条“再撤销会话、删除手机号与密码”，“把其订单上的会员 ID 清空以解除关联”；Kelvin
    10-08“注销时撤销该会员的全部会话”；Kelvin 10-10（1）“使用一次即删除”。验收：批准经
    SHOP-TASK-081 的 verify 取得后注销 204、清除会话 cookie；会员为 deleted、号码与密码哈希为空；
    该会员全部会话撤销（取当前会话 401）；其订单 member_id 为空、认领状态不变，其他会员的订单
    不变；批准已删除。
    """
    token, session_a = login(engine, member)
    _, session_b = login(engine, member)
    own_order = add_order(engine, member)
    other = add_member(engine, phone=PHONE_SG)
    other_order = add_order(engine, other)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    assert len(fake.approval_keys()) == 1

    response = delete(client, token)

    assert_deleted_response(response, token)
    assert_deleted(engine, member)
    assert is_active(engine, other)
    assert revoked_sessions(engine) == [session_a, session_b]
    assert orders(engine) == [
        (own_order, None, CLAIM_NOT_CLAIMABLE),
        (other_order, other, CLAIM_NOT_CLAIMABLE),
    ]
    assert fake.approval_keys() == []
    assert_error(client.get(SESSION_URL, headers=auth(token, None)), 401, "member_session_required")
    assert_error(delete(client, token), 401, "member_session_required")


@pytest.mark.usefixtures("enabled")
def test_approval_is_single_use_even_when_commit_fails(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    session_class: list[type[Session]],
    member: int,
) -> None:
    """Kelvin 10-10（1）“一次性批准……使用一次即删除”。验收：提交时数据库出错回滚，503
    service_unavailable（开关开启时批准已被取用，须重新验证）——会员仍为 active、会话仍有效、
    不清 cookie；同一批准不能再用，再次确认为 403 delete_not_verified；重新验证后可注销。
    """
    token, _ = login(engine, member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    session_class[0] = CommitFailingSession

    failed = delete(client, token)

    assert_error(failed, 503, "service_unavailable")
    assert_no_secrets(failed, token)
    assert is_active(engine, member)
    assert revoked_sessions(engine) == []
    assert fake.approval_keys() == []

    session_class[0] = Session
    assert_error(delete(client, token), 403, "delete_not_verified")
    assert is_active(engine, member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    assert_deleted_response(delete(client, token), token)
    assert_deleted(engine, member)


@pytest.mark.usefixtures("enabled")
def test_correct_password_does_not_replace_approval(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """第 3 条“账号注销须先以短信验证码确认”；边界第 4 条“确认注销时，服务端都按当时读取的开关值
    判定”。验收：开关开启时只接受注销批准，password 不使用——给出正确密码也是 403
    delete_not_verified，会员仍为 active，不碰密码登录的锁定计数。
    """
    token, _ = login(engine, pw_member)

    response = delete(client, token, password=PASSWORD)

    assert_error(response, 403, "delete_not_verified")
    assert_no_secrets(response, token)
    assert is_active(engine, pw_member)
    assert fake.rate_limit_keys() == []


@pytest.mark.usefixtures("enabled")
def test_other_sessions_approval_cannot_confirm(
    client: TestClient, engine: Engine, fake: FakeRedis, member: int
) -> None:
    """Kelvin 10-10（1）“记一条绑定当前会员会话的一次性批准”。验收：以当前会话与会员取用批准——
    同一会员的会话 A 核验通过后，以会话 B 确认为 403 delete_not_verified，会员仍为 active。
    """
    token_a, _ = login(engine, member)
    token_b, _ = login(engine, member)
    add_attempt(engine)
    assert verify(client, token_a).status_code == 204

    assert_error(delete(client, token_b), 403, "delete_not_verified")
    assert is_active(engine, member)


def test_approval_not_accepted_after_switch_turned_off(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """边界第 4 条“开关在流程途中被关闭后……也不能以短信完成注销确认”。验收：开关在核验之后被
    关闭时批准不再被接受——已设密码者不给密码为 422 password_required、会员仍为 active、批准未被
    取用；给出当前密码才注销。
    """
    set_switch(engine, True)
    token, session_id = login(engine, pw_member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    set_switch(engine, False)

    response = delete(client, token)

    assert_error(response, 422, "password_required")
    assert is_active(engine, pw_member)
    assert fake.approval_keys() == [APPROVAL_PREFIX + str(session_id)]
    assert_deleted_response(delete(client, token, password=PASSWORD), token)
    assert_deleted(engine, pw_member)


# ---------------------------------------------------------------------------
# 开关关闭、已设密码：当前密码与共用的锁定计数
# ---------------------------------------------------------------------------


SWITCH_OFF = [pytest.param(None, id="no_row"), pytest.param(False, id="off")]


@pytest.mark.parametrize("switch", SWITCH_OFF)
def test_switch_off_missing_password_is_422_without_redis(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    pw_member: int,
    switch: bool | None,
) -> None:
    """第 3 条“短信验证开关关闭期间……已设密码的会员注销时输入当前密码确认”。验收：开关关闭（含
    无设置行）且会员已设密码时，未给 password 为 422 {"detail":"password_required"}，在取 Redis
    客户端之前；会员仍为 active。
    """
    if switch is not None:
        set_switch(engine, switch)
    token, _ = login(engine, pw_member)

    assert_error(delete(client, token), 422, "password_required")
    assert redis_getter.calls == 0
    assert fake.rate_limit_keys() == []
    assert is_active(engine, pw_member)


@pytest.mark.usefixtures("disabled")
def test_switch_off_wrong_password_is_403_and_counts(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """Kelvin 10-10（2）“已设密码的会员以当前密码确认注销，输错次数与密码登录共用同一组锁定
    计数”。验收：密码不通过时以 record_login_failure 计一次失败，403 {"detail":"password_wrong"}；
    会员仍为 active；错误不含密码。
    """
    token, _ = login(engine, pw_member)

    response = delete(client, token, password=WRONG_PASSWORD)

    assert_error(response, 403, "password_wrong")
    assert_no_secrets(response, token)
    assert is_active(engine, pw_member)
    assert len(fake.rate_limit_keys()) == 1


@pytest.mark.usefixtures("disabled")
def test_switch_off_correct_password_deletes(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """第 3 条“已设密码的会员注销时输入当前密码确认”，“再撤销会话、删除手机号与密码”。验收：开关
    关闭时正确密码可注销，204 并清除 cookie；会员为 deleted、密码哈希为空；其订单 member_id 为空；
    会话撤销；不计失败。
    """
    token, session_id = login(engine, pw_member)
    order_id = add_order(engine, pw_member)

    response = delete(client, token, password=PASSWORD)

    assert_deleted_response(response, token)
    assert_deleted(engine, pw_member)
    assert orders(engine) == [(order_id, None, CLAIM_NOT_CLAIMABLE)]
    assert revoked_sessions(engine) == [session_id]
    assert fake.rate_limit_keys() == []
    assert_error(client.get(SESSION_URL, headers=auth(token, None)), 401, "member_session_required")


@pytest.mark.usefixtures("disabled")
def test_switch_off_locked_after_five_failures_is_429(
    client: TestClient, engine: Engine, pw_member: int
) -> None:
    """Kelvin 10-08“同一号码加来源 15 分钟内密码登录失败 5 次即锁 15 分钟”；Kelvin 10-10（2）
    注销与登录共用这组计数。验收：login_locked 为真时 429 {"detail":"rate_limited"}，不校验密码
    ——正确密码也是 429，会员仍为 active；另一来源不受影响（组合锁定），可以注销。
    """
    token, _ = login(engine, pw_member)
    for _ in range(FAILURES_TO_LOCK):
        assert_error(delete(client, token, password=WRONG_PASSWORD), 403, "password_wrong")

    locked = delete(client, token, password=PASSWORD)

    assert_error(locked, 429, "rate_limited")
    assert_no_secrets(locked, token)
    assert is_active(engine, pw_member)
    other_source = delete(client, token, source=OTHER_SOURCE, password=PASSWORD)
    assert_deleted_response(other_source, token)
    assert_deleted(engine, pw_member)


@pytest.mark.usefixtures("disabled")
def test_locked_request_does_not_count_another_failure(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """Kelvin 10-08 的锁定由 SHOP-TASK-071 的 password_login 顺序实现。验收：锁定时 429“不校验
    密码、不计失败”——锁定后再输错，失败计数不变。
    """
    token, _ = login(engine, pw_member)
    for _ in range(FAILURES_TO_LOCK):
        delete(client, token, password=WRONG_PASSWORD)
    before = dict(fake.values)

    assert_error(delete(client, token, password=WRONG_PASSWORD), 429, "rate_limited")
    assert fake.values == before


@pytest.mark.usefixtures("disabled")
def test_login_failures_lock_deletion(client: TestClient, engine: Engine, pw_member: int) -> None:
    """Kelvin 10-10（2）“输错次数与密码登录共用同一组锁定计数……登录与注销互相计入”。验收：与
    POST /api/member/password-login 交叉验证——从同一来源对同一号码登录失败 5 次后，注销即使给出
    正确密码也 429 rate_limited，会员仍为 active。
    """
    token, _ = login(engine, pw_member)
    for _ in range(FAILURES_TO_LOCK):
        assert password_login(client, WRONG_PASSWORD).status_code == 401

    assert_error(delete(client, token, password=PASSWORD), 429, "rate_limited")
    assert is_active(engine, pw_member)


@pytest.mark.usefixtures("disabled")
def test_deletion_failures_lock_login(client: TestClient, engine: Engine, pw_member: int) -> None:
    """Kelvin 10-10（2）“登录与注销互相计入”。验收：与 POST /api/member/password-login 交叉验证
    ——从同一来源对同一号码注销输错 5 次后，正确密码登录为 401 login_failed（被锁定）；另一来源
    照常登录。
    """
    token, _ = login(engine, pw_member)
    for _ in range(FAILURES_TO_LOCK):
        assert_error(delete(client, token, password=WRONG_PASSWORD), 403, "password_wrong")

    assert_error(password_login(client, PASSWORD), 401, "login_failed")
    assert password_login(client, PASSWORD, source=OTHER_SOURCE).status_code == 204


@pytest.mark.usefixtures("disabled")
def test_mixed_login_and_deletion_failures_add_up(
    client: TestClient, engine: Engine, pw_member: int
) -> None:
    """Kelvin 10-10（2）“共用同一组锁定计数”。验收：共用计数——登录失败 3 次加注销失败 2 次即满
    5 次，之后注销 429、登录 401 login_failed；第 5 次之前正确密码登录仍可通过。
    """
    token, _ = login(engine, pw_member)
    for _ in range(3):
        assert password_login(client, WRONG_PASSWORD).status_code == 401
    assert_error(delete(client, token, password=WRONG_PASSWORD), 403, "password_wrong")
    assert password_login(client, PASSWORD).status_code == 204
    assert_error(delete(client, token, password=WRONG_PASSWORD), 403, "password_wrong")

    assert_error(delete(client, token, password=PASSWORD), 429, "rate_limited")
    assert_error(password_login(client, PASSWORD), 401, "login_failed")
    assert is_active(engine, pw_member)


# ---------------------------------------------------------------------------
# 开关关闭、未设密码：会话与 CSRF 即确认
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("switch", SWITCH_OFF)
@pytest.mark.parametrize("body", [{}, {"password": "SECRET-PW anything"}], ids=["empty", "pw"])
def test_switch_off_no_password_deletes_with_session_and_csrf(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    member: int,
    switch: bool | None,
    body: dict[str, Any],
) -> None:
    """第 3 条“未设密码的会员在已登录会话内经二次确认后注销”（二次确认由前台确认框完成）。验收：
    开关关闭（含无设置行）且会员未设密码时凭当前会话与 CSRF 即确认，不需要 password（给出也不
    使用），不取 Redis 客户端；204 并清除 cookie，会员为 deleted，其订单 member_id 为空。
    """
    if switch is not None:
        set_switch(engine, switch)
    token, session_id = login(engine, member)
    order_id = add_order(engine, member)

    response = delete(client, token, **body)

    assert_deleted_response(response, token)
    assert_deleted(engine, member)
    assert orders(engine) == [(order_id, None, CLAIM_NOT_CLAIMABLE)]
    assert revoked_sessions(engine) == [session_id]
    assert redis_getter.calls == 0
    assert fake.values == {}


# ---------------------------------------------------------------------------
# Redis 与数据库出错
# ---------------------------------------------------------------------------


REDIS_TROUBLE = ["not_configured", "failing"]


def _break_redis(fake: FakeRedis, getter: RedisGetter, trouble: str) -> None:
    if trouble == "not_configured":
        getter.configured = False
    else:
        fake.fail = {"ping", "get", "set", "execute"}


@pytest.mark.parametrize("trouble", REDIS_TROUBLE)
def test_switch_on_redis_unavailable_is_503_without_deleting(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    member: int,
    trouble: str,
) -> None:
    """Kelvin 10-10（1）批准存于共享 Redis；SHOP-TASK-080“Redis……不可用时……调用方必须拒绝注销，
    不得放行”。验收：开关开启时 Redis 不可用（未配置或出错）由路由类回答 503 service_unavailable，
    未注销；错误不含 Redis 主机。
    """
    set_switch(engine, True)
    token, _ = login(engine, member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    _break_redis(fake, redis_getter, trouble)

    response = delete(client, token)

    assert_error(response, 503, "service_unavailable")
    assert_no_secrets(response, token)
    assert is_active(engine, member)
    assert revoked_sessions(engine) == []


@pytest.mark.usefixtures("enabled")
def test_verify_then_redis_outage_keeps_member(
    client: TestClient, engine: Engine, fake: FakeRedis, member: int
) -> None:
    """Kelvin 10-10（1）“确认注销时取用”。验收：取用时 Redis 出错为 503 service_unavailable 且
    未注销；Redis 恢复后同一批准仍可取用（取用未执行），注销成功。
    """
    token, _ = login(engine, member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    fake.fail = {"execute"}

    assert_error(delete(client, token), 503, "service_unavailable")
    assert is_active(engine, member)

    fake.fail = set()
    assert_deleted_response(delete(client, token), token)


@pytest.mark.usefixtures("disabled")
@pytest.mark.parametrize("trouble", REDIS_TROUBLE)
def test_switch_off_with_password_redis_unavailable_is_503(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    pw_member: int,
    trouble: str,
) -> None:
    """Kelvin 10-08 的锁定计数在 Redis；rate_limit「Redis 不可用……调用方必须拒绝」。验收：开关
    关闭且已设密码时 Redis 不可用为 503 service_unavailable，正确密码也不注销。
    """
    token, _ = login(engine, pw_member)
    _break_redis(fake, redis_getter, trouble)

    response = delete(client, token, password=PASSWORD)

    assert_error(response, 503, "service_unavailable")
    assert_no_secrets(response, token)
    assert is_active(engine, pw_member)


@pytest.mark.usefixtures("disabled")
@pytest.mark.parametrize("trouble", REDIS_TROUBLE)
def test_switch_off_no_password_deletes_despite_redis_trouble(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    member: int,
    trouble: str,
) -> None:
    """第 3 条“未设密码的会员在已登录会话内经二次确认后注销”。验收：开关关闭且未设密码时不取
    Redis 客户端，Redis 未配置或出错时照样注销。
    """
    token, _ = login(engine, member)
    _break_redis(fake, redis_getter, trouble)

    assert_deleted_response(delete(client, token), token)
    assert_deleted(engine, member)
    assert redis_getter.calls == 0


@pytest.mark.usefixtures("disabled")
@pytest.mark.parametrize("password_hash", [None, PASSWORD_HASH], ids=["no_pw", "pw"])
def test_commit_failure_is_503_and_rolled_back(
    client: TestClient,
    engine: Engine,
    session_class: list[type[Session]],
    password_hash: str | None,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话”。验收：提交时数据库出错回滚，503
    service_unavailable；会员仍为 active、号码不变、会话仍有效、订单 member_id 不变，不清 cookie；
    错误不含号码与密码。
    """
    member_id = add_member(engine, password_hash=password_hash)
    token, _ = login(engine, member_id)
    order_id = add_order(engine, member_id)
    session_class[0] = CommitFailingSession

    response = delete(client, token, password=PASSWORD)

    assert_error(response, 503, "service_unavailable")
    assert_no_secrets(response, token)
    assert member_row(engine, member_id) == (PHONE_MY, password_hash, "active", False)
    assert orders(engine) == [(order_id, member_id, CLAIM_NOT_CLAIMABLE)]
    assert revoked_sessions(engine) == []


@pytest.mark.usefixtures("disabled")
def test_delete_member_false_is_401(
    client: TestClient, engine: Engine, member: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：SHOP-TASK-080 的注销函数返回 False（会员已不是 active）时 401
    member_session_required，不清 cookie。
    """
    token, _ = login(engine, member)
    monkeypatch.setattr(deletion_api, "delete_member", lambda *_args: False)

    assert_error(delete(client, token), 401, "member_session_required")
    assert is_active(engine, member)


# ---------------------------------------------------------------------------
# 检查顺序：413 → 415 → 401 → 403 → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/json"],
    ids=["not-json", "no-content-type", "json"],
)
@pytest.mark.parametrize("signed", [False, True], ids=["no-session", "session"])
def test_oversized_body_is_413_before_everything(
    client: TestClient,
    engine: Engine,
    redis_getter: RedisGetter,
    signed: bool,
    content_type: str | None,
) -> None:
    """验收：413（逐块读，超过 4 KB）先于一切——没有会话、开关关闭、Redis 未配置时也是 413；分块
    发送、不带 Content-Length 也按实际读到的字节判断；不回显请求内容（含密码）。
    """
    redis_getter.configured = False
    headers = auth(login(engine, add_member(engine))[0]) if signed else {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    chunks = [b'{"password": "SECRET-PW' + b"9" * 3000, b'", "phone": "' + b"4" * 3000 + b'"}']

    response = client.post(DELETE_URL, content=iter(chunks), headers=headers)

    assert_error(response, 413, "request body too large")
    assert "SECRET-PW" not in response.text
    assert redis_getter.calls == 0


@pytest.mark.usefixtures("disabled")
def test_body_at_4_kb_is_accepted(client: TestClient, engine: Engine, member: int) -> None:
    """验收：超过 4 KB 才 413——恰好 4 KB 的合法请求体照常处理。"""
    token, _ = login(engine, member)
    raw = b"{}"
    padded = raw + b" " * (MAX_BODY_BYTES - len(raw))
    headers = {**auth(token), "Content-Type": "application/json"}

    assert_deleted_response(client.post(DELETE_URL, content=padded, headers=headers), token)


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"],
)
def test_not_json_is_415_before_session(client: TestClient, content_type: str | None) -> None:
    """验收：不是 JSON 为 415，在 413 之后、401 之前——没有会话时合法与多出字段的请求体都是 415；
    不回显请求内容。
    """
    for body in ({"password": PASSWORD}, {"password": PASSWORD, "phone": PHONE_MY}):
        headers = {} if content_type is None else {"Content-Type": content_type}
        response = client.post(DELETE_URL, content=json.dumps(body).encode(), headers=headers)
        assert_error(response, 415, "request body must be JSON")
        assert_no_secrets(response)


@pytest.mark.parametrize("session_state", ["none", "malformed", "unknown", "revoked"])
def test_no_valid_session_is_401_before_structure(
    client: TestClient, engine: Engine, redis_getter: RedisGetter, session_state: str
) -> None:
    """第 3 条“会话到期、退出与服务端授权检查”。验收：require_member 先于 CSRF 与结构——没有会话、
    cookie 格式不对、会话不存在或已撤销时，结构不对（多出 phone）也是 401
    member_session_required；不取 Redis 客户端、不注销。
    """
    token: str | None
    member_id = add_member(engine)
    if session_state == "none":
        token = None
    elif session_state == "malformed":
        token = "not-a-token"
    elif session_state == "unknown":
        token = "A" * 43
    else:
        token, session_id = login(engine, member_id)
        revoke(engine, session_id)

    response = delete(client, token, phone=PHONE_SG, password=PASSWORD)

    assert_error(response, 401, "member_session_required")
    assert_no_secrets(response)
    assert redis_getter.calls == 0
    assert is_active(engine, member_id)


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated", "empty"])
def test_bad_csrf_is_403_before_structure_and_keeps_approval(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    member: int,
    csrf: str,
) -> None:
    """第 5 条：写操作另须 CSRF 令牌。验收：require_member_csrf 在会话之后、结构之前——缺失、
    错误、重复（即使都正确）或为空的 X-CSRF-Token 都 403 csrf_failed，结构不对也是 403；不取
    Redis 客户端，已取得的注销批准不被耗掉，会员仍为 active。
    """
    set_switch(engine, True)
    token, session_id = login(engine, member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204
    cookie = f"{SESSION_COOKIE_NAME}={token}"
    body = json.dumps({"phone": PHONE_SG, "password": PASSWORD}).encode()
    headers: list[tuple[str, str]] = [
        ("Cookie", cookie),
        ("Content-Type", "application/json"),
        ("X-Real-IP", SOURCE),
    ]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", csrf_for("B" * 43)))
    elif csrf == "duplicated":
        headers += [("X-CSRF-Token", csrf_for(token)), ("X-CSRF-Token", csrf_for(token))]
    elif csrf == "empty":
        headers.append(("X-CSRF-Token", ""))

    response = client.post(DELETE_URL, content=body, headers=headers)

    assert_error(response, 403, "csrf_failed")
    assert_no_secrets(response, token)
    assert redis_getter.calls == 0
    assert fake.approval_keys() == [APPROVAL_PREFIX + str(session_id)]
    assert is_active(engine, member)


STRUCTURE_CASES = [
    pytest.param(
        {"password": PASSWORD, "phone": PHONE_SG}, ("body", "phone"), "extra_forbidden", id="phone"
    ),
    pytest.param(
        {"code": CODE, "password": PASSWORD}, ("body", "code"), "extra_forbidden", id="code"
    ),
    pytest.param({"password": 12345678}, ("body", "password"), "string_type", id="int"),
    pytest.param({"password": ""}, ("body", "password"), "string_too_short", id="empty"),
    pytest.param(
        {"password": "SECRET-PW" + "x" * 248},
        ("body", "password"),
        "string_too_long",
        id="257",
    ),
    pytest.param(["SECRET-PW-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.parametrize(("body", "loc", "error_type"), STRUCTURE_CASES)
def test_structure_errors_are_422_without_echo(
    client: TestClient,
    engine: Engine,
    fake: FakeRedis,
    redis_getter: RedisGetter,
    pw_member: int,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……密码”。验收：请求体只有可省略的 password（给出时
    为 1 到 256 个字符的字符串），多出字段 422；422 只给位置、类型与固定消息，不回显请求内容
    （尤其是密码）；排在开关与 Redis 之前（开关开启且有批准时也是 422，批准不被取用），会员仍为
    active。
    """
    set_switch(engine, True)
    token, _ = login(engine, pw_member)
    add_attempt(engine)
    assert verify(client, token).status_code == 204

    response = client.post(DELETE_URL, json=body, headers=auth(token))

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    for secret in ("SECRET-PW", "8123", "12345678", CODE):
        assert secret not in response.text
    assert redis_getter.calls == 0
    assert len(fake.approval_keys()) == 1
    assert is_active(engine, pw_member)


@pytest.mark.usefixtures("disabled")
def test_password_of_256_chars_is_checked(
    client: TestClient, engine: Engine, fake: FakeRedis, pw_member: int
) -> None:
    """验收：password 给出时为 1 到 256 个字符的字符串——256 个字符通过结构检查、照常校验（不是
    当前密码即 403 password_wrong）；长度不足 8 个字符也照常校验，不按长度规则预先拒绝。
    """
    token, _ = login(engine, pw_member)

    assert_error(delete(client, token, password="x" * 256), 403, "password_wrong")
    assert_error(delete(client, token, password="short"), 403, "password_wrong")
    assert is_active(engine, pw_member)


def test_check_order_step_by_step(
    client: TestClient, engine: Engine, redis_getter: RedisGetter
) -> None:
    """验收：检查顺序与 SHOP-TASK-081 的两个接口相同——413 → 415 → 401 member_session_required →
    403 csrf_failed → 422（结构），之后按开关判定（开启时 Redis 未配置为 503
    service_unavailable）。每一步都以“本步不通过、其后各步也不通过”的请求验证。
    """
    set_switch(engine, True)
    redis_getter.configured = False
    member_id = add_member(engine, password_hash=PASSWORD_HASH)
    token, _ = login(engine, member_id)
    bad = {"password": PASSWORD, "phone": PHONE_SG}
    oversized = json.dumps({**bad, "pad": "x" * MAX_BODY_BYTES}).encode()
    raw = json.dumps(bad).encode()

    json_type = {"Content-Type": "application/json"}
    too_large = client.post(DELETE_URL, content=oversized, headers=json_type)
    assert_error(too_large, 413, "request body too large")
    not_json = client.post(DELETE_URL, content=raw, headers={"Content-Type": "text/plain"})
    assert_error(not_json, 415, "request body must be JSON")
    assert_error(client.post(DELETE_URL, json=bad), 401, "member_session_required")
    assert_error(client.post(DELETE_URL, json=bad, headers=auth(token, None)), 403, "csrf_failed")
    assert client.post(DELETE_URL, json=bad, headers=auth(token)).status_code == 422
    assert redis_getter.calls == 0

    assert_error(delete(client, token), 503, "service_unavailable")
    assert redis_getter.calls == 1
    assert is_active(engine, member_id)


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


def test_wrong_method_is_405_without_request_content(client: TestClient) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.get(DELETE_URL, params={"phone": PHONE_MY, "password": PASSWORD})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert "SECRET-PW" not in response.text


def test_no_logs_with_phone_or_password(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……密码”。验收：不写日志——批准缺失、密码错误、
    缺密码、CSRF 不通过、结构错误与成功注销都不产生 app 下的日志，任何日志都不含号码、密码与
    会话 cookie。
    """
    caplog.set_level(logging.DEBUG)
    member_id = add_member(engine, password_hash=PASSWORD_HASH)
    token, _ = login(engine, member_id)

    set_switch(engine, True)
    delete(client, token, password=PASSWORD)
    set_switch(engine, False)
    delete(client, token, password=WRONG_PASSWORD)
    delete(client, token)
    delete(client, token, csrf=csrf_for("B" * 43), password=PASSWORD)
    delete(client, token, password=PASSWORD, phone=PHONE_MY)
    assert delete(client, token, password=PASSWORD).status_code == 204

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", PASSWORD, WRONG_PASSWORD, token, csrf_for(token)):
            assert value not in message

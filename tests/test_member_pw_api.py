"""会员密码登录与首次设置密码接口：POST /api/member/password-login 与
POST /api/member/password（app/api/member_pw.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：
  会员可用手机号加密码登录；“未设密码的账号只能短信登录，密码登录对其与对错误密码
  返回相同的通用失败，不暴露账号是否存在或是否设了密码”；首次设置密码在已登录会话内
  进行；“密码至少 8 位、不强制复杂度”；短信验证开关关闭期间已设密码的会员仍可密码
  登录，已登录会员首次设置密码不受影响。
  第 2 条（下称「第 2 条」）：相同的 E.164 规范化结果。
  第 5 条（下称「第 5 条」）：写操作另须 CSRF 令牌。
  第 6 条（下称「第 6 条」）：应用日志不记录完整电话与密码。
- 「失败、并发与重试」第 4 条（下称「重试第 4 条」）：
  “依赖 Redis 限流的敏感接口也拒绝请求”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：
  “同一号码加来源 15 分钟内密码登录失败 5 次即锁 15 分钟，失败一律通用提示”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，
写明是 SHOP-TASK-077 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_member_auth_api.py 相同）。接口的
会话依赖换成每个请求一个绑定同一内存库的会话（可换成提交即抛错的子类），请求结束
关闭时回滚未提交的改动；检查结果一律在另一个数据库会话里读，读得到即说明接口已提交。
取 Redis 客户端的依赖换成本文件的内存替身 FakeRedis（照 tests/test_admin_auth_api.py
写，另加一个可拨动的时钟模拟过期）。访客来源用 X-Real-IP 指定。期望的令牌摘要、
CSRF 令牌与锁定标识在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import pytest
import redis
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.pool import StaticPool

from app.api.member_auth import MemberContext, require_member
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, MemberSession, Order, OrderRecipient, SiteSetting
from app.models.order import CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.site import SITE_SETTING_ID
from app.services.member_auth import issue_member_session, revoke_member_session
from app.services.pw_hash import hash_password, verify_password
from app.services.rate_limit import get_redis_client

LOGIN_URL = "/api/member/password-login"
PASSWORD_URL = "/api/member/password"
SESSION_URL = "/api/member/session"
MAX_BODY_BYTES = 4 * 1024

COOKIE_NAME = "__Host-shop_member_session"
THIRTY_DAYS_SECONDS = 30 * 24 * 3600
CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"

PHONE_LOCAL = "012-345 6789"
PHONE_MY = "+60123456789"
PHONE_OTHER = "+60123456780"
PASSWORD = "SECRET-PW correct horse"
PASSWORD_HASH = hash_password(PASSWORD)
WRONG_PASSWORD = "SECRET-PW wrong horse"
NEW_PASSWORD = "SECRET-PW new battery"
# 257 个字符。
LONG_PASSWORD = "SECRET-PW" + "x" * 248
SOURCE = "203.0.113.5"
OTHER_SOURCE = "198.51.100.20"
FINGERPRINT = "0" * 64

FAILURE_BUCKET = "member_login_failures"
LOCK_BUCKET = "member_login_lock"
LOCK_SECONDS = 15 * 60

NO_STORE = "no-store"
LOGIN_FAILED = "login_failed"
UNAVAILABLE = "service_unavailable"
SESSION_REQUIRED = "member_session_required"
CSRF_FAILED = "csrf_failed"
TOO_SHORT = "password_too_short"
ALREADY_SET = "password_already_set"

_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：计数值、过期时刻（按本替身的时钟，秒）与按桶名注入的连接错误。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, int] = {}
        self.expires: dict[str, float] = {}
        self.fail_get: set[str] = set()
        self.fail_incr: set[str] = set()

    @staticmethod
    def bucket_of(key: str) -> str:
        # 键为 acuven_shop:rate_limit:<桶名>:<摘要>。
        return key.split(":")[2]

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def purge(self, key: str) -> None:
        expires = self.expires.get(key)
        if expires is not None and expires <= self.now:
            self.values.pop(key, None)
            self.expires.pop(key, None)

    def get(self, key: str) -> bytes | None:
        if self.bucket_of(key) in self.fail_get:
            raise redis.exceptions.ConnectionError("connection refused")
        self.purge(key)
        value = self.values.get(key)
        return None if value is None else str(value).encode()

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    def count(self, bucket: str, source: str, phone: str) -> int:
        # 标识另算：[来源, 规范化 E.164 号码] 的 JSON 编码。
        identifier = json.dumps([source, phone], ensure_ascii=True)
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        key = f"acuven_shop:rate_limit:{bucket}:{digest}"
        self.purge(key)
        return self.values.get(key, 0)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行；EXPIRE 只支持 NX。"""

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
        for _, key, _ in self.queued:
            if self.store.bucket_of(key) in self.store.fail_incr:
                raise redis.exceptions.ConnectionError("connection refused")
        results: list[Any] = []
        for command, key, seconds in self.queued:
            self.store.purge(key)
            if command == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            elif key in self.store.expires:
                results.append(False)
            else:
                self.store.expires[key] = self.store.now + seconds
                results.append(True)
        return results


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
def session_class() -> list[type[Session]]:
    # 测试可把它换成 CommitFailingSession。
    return [Session]


@pytest.fixture
def app(engine: Engine, fake: FakeRedis, session_class: list[type[Session]]) -> FastAPI:
    # 连接串显式为空：不覆盖依赖时即「未配置」。
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


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


def add_member(
    engine: Engine, phone: str = PHONE_MY, password_hash: str | None = PASSWORD_HASH
) -> int:
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


def password_hash_of(engine: Engine, member_id: int) -> str | None:
    stmt = select(Member.password_hash).where(Member.id == member_id)
    return _read(engine, lambda s: s.execute(stmt).scalar_one())


def sessions(engine: Engine) -> list[tuple[int, str, datetime, datetime, datetime | None]]:
    """全部会员会话的（会员 ID, 令牌摘要, 创建, 到期, 撤销），按 ID。"""
    return _read(
        engine,
        lambda s: [
            (row.member_id, row.token_hash, row.created_at, row.expires_at, row.revoked_at)
            for row in s.scalars(select(MemberSession).order_by(MemberSession.id))
        ],
    )


def add_order(engine: Engine, phone: str = PHONE_MY) -> int:
    """一张三天前下的游客订单及其收货资料，可认领。"""
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
                member_id=None,
                claim_status=CLAIM_OPEN,
            )
        )
        order_id = result.inserted_primary_key[0]
        session.execute(
            insert(OrderRecipient).values(
                order_id=order_id,
                name="Demo Recipient",
                phone=phone,
                country_code="MY",
                region="MY-10",
                address="1 Jalan Demo",
                postal_code="50000",
            )
        )
        session.commit()
        return order_id


def claim_of(engine: Engine, order_id: int) -> tuple[int | None, str]:
    """订单的（会员 ID, 认领状态）。"""
    stmt = select(Order.member_id, Order.claim_status).where(Order.id == order_id)
    member_id, claim_status = _read(engine, lambda s: s.execute(stmt).one())
    return member_id, claim_status


def stored_session(engine: Engine, member_id: int, *, revoke: bool = False) -> str:
    """直接写库签发一个会话（可选撤销）并提交，返回令牌原文。"""
    issued_at = _now()
    with Session(engine) as session:
        member = session.get(Member, member_id)
        assert member is not None
        issued = issue_member_session(session, member, issued_at)
        if revoke:
            revoke_member_session(session, issued.session, issued_at + timedelta(seconds=1))
        session.commit()
        return issued.token


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def member_headers(token: str) -> dict[str, str]:
    """会话 cookie 与正确的 CSRF 令牌。"""
    return {**cookie_header(token), "X-CSRF-Token": csrf_for(token)}


def login_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"phone": PHONE_LOCAL, "phone_region": "MY", "password": PASSWORD}
    body.update(overrides)
    return body


def without(name: str) -> dict[str, Any]:
    body = login_body()
    del body[name]
    return body


def login(
    client: TestClient,
    phone: str = PHONE_LOCAL,
    password: str = PASSWORD,
    *,
    region: str = "MY",
    source: str = SOURCE,
) -> Any:
    body = login_body(phone=phone, phone_region=region, password=password)
    return client.post(LOGIN_URL, json=body, headers={"X-Real-IP": source})


def set_password(client: TestClient, token: str, password: str = NEW_PASSWORD) -> Any:
    return client.post(PASSWORD_URL, json={"password": password}, headers=member_headers(token))


def _padded(body: Any, size: int) -> bytes:
    """合法 JSON，末尾以空白补到 size 字节（JSON 允许）。"""
    raw = json.dumps(body).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


def assert_error(response: Any, status_code: int, detail: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE


def assert_no_cookie(response: Any) -> None:
    assert response.headers.get_list("set-cookie") == []


def assert_no_secrets(response: Any, *extra: str) -> None:
    """响应体与响应头不含号码原文、密码与给出的值（令牌、来源）。"""
    text = response.text + "".join(f"{k}:{v}" for k, v in response.headers.multi_items())
    for value in ("123456789", "345 6789", "SECRET", *extra):
        assert value not in text


# ---------------------------------------------------------------------------
# 密码登录：成功
# ---------------------------------------------------------------------------


def test_login_sets_cookie_readable_by_session_and_does_not_claim(
    client: TestClient, engine: Engine
) -> None:
    """第 3 条：会员可用手机号加密码登录。
    验收：返回会员时签发会话、提交、设置 cookie（与 sms-login 相同：__Host- 名、
    HttpOnly、Secure、SameSite=Lax、Path=/、无 Domain、Max-Age 30 天），204 且
    响应体为空；cookie 可被 GET /api/member/session 读到；不认领游客订单；no-store；
    响应不含号码、密码与来源。
    """
    member_id = add_member(engine)
    order_id = add_order(engine)

    response = login(client)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    (cookie,) = response.headers.get_list("set-cookie")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    name, token = name_value.split("=", 1)
    assert name == COOKIE_NAME
    lowered = [attribute.lower() for attribute in attributes]
    for attribute in ("httponly", "secure", "samesite=lax", "path=/"):
        assert attribute in lowered
    assert f"max-age={THIRTY_DAYS_SECONDS}" in lowered
    assert not any(attribute.startswith("domain") for attribute in lowered)
    ((owner, stored_hash, created_at, expires_at, revoked_at),) = sessions(engine)
    assert (owner, stored_hash, revoked_at) == (member_id, token_hash(token), None)
    assert expires_at - created_at == timedelta(days=30)
    assert claim_of(engine, order_id) == (None, CLAIM_OPEN)
    assert_no_secrets(response, SOURCE)

    current = client.get(SESSION_URL)

    assert current.status_code == 200, current.text
    assert current.json()["has_password"] is True
    assert current.json()["csrf_token"] == csrf_for(token)


@pytest.mark.parametrize(
    ("phone", "region"),
    [(PHONE_LOCAL, "MY"), ("+60 12-345 6789", "SG"), (PHONE_MY, "MY")],
)
def test_login_normalizes_phone_like_sms_login(
    client: TestClient, engine: Engine, phone: str, region: str
) -> None:
    """第 2 条：相同的 E.164 规范化结果。
    验收：phone 与 phone_region 的规范化方式与 sms-login 相同（以加号开头时以输入
    为准），按规范化结果找会员。
    """
    add_member(engine)

    response = login(client, phone, region=region)

    assert response.status_code == 204, response.text


# ---------------------------------------------------------------------------
# 密码登录：通用失败与锁定
# ---------------------------------------------------------------------------


def test_five_failure_kinds_are_identical(client: TestClient, engine: Engine) -> None:
    """第 3 条“密码登录对其与对错误密码返回相同的通用失败，不暴露账号是否存在或是否
    设了密码”；Kelvin 10-08“失败一律通用提示”。
    验收：锁定、号码未注册、未设密码、密码错误与已注销一律 401 login_failed，五者的
    状态码、响应体与响应头完全相同；都不设 cookie、不签发会话。
    """
    add_member(engine, phone="+60123450002", password_hash=None)
    add_member(engine, phone="+60123450003")
    delete_member(engine, add_member(engine, phone="+60123450004"))
    add_member(engine, phone="+60123450005")
    for _ in range(5):
        assert login(client, "+60123450005", WRONG_PASSWORD).status_code == 401

    responses = {
        "unregistered": login(client, "+60123450001"),
        "no_password": login(client, "+60123450002"),
        "wrong_password": login(client, "+60123450003", WRONG_PASSWORD),
        "deleted": login(client, "+60123450004"),
        "locked": login(client, "+60123450005"),
    }

    first = responses["unregistered"]
    assert_error(first, 401, LOGIN_FAILED)
    for kind, response in responses.items():
        assert response.status_code == first.status_code, kind
        assert response.content == first.content, kind
        assert response.headers.multi_items() == first.headers.multi_items(), kind
        assert_no_cookie(response)
        assert_no_secrets(response, SOURCE)
    assert sessions(engine) == []


def test_short_password_at_login_is_plain_failure(client: TestClient, engine: Engine) -> None:
    """第 3 条：密码登录失败返回相同的通用失败。
    验收：密码字段是 1 到 256 个字符的字符串，登录时不按长度规则预先拒绝——
    7 个字符的密码照常 401 login_failed，不是 422。
    """
    add_member(engine)

    response = login(client, password="1234567")

    assert_error(response, 401, LOGIN_FAILED)


def test_fifth_failure_locks_even_correct_password_until_15_minutes(
    client: TestClient, engine: Engine, fake: FakeRedis
) -> None:
    """Kelvin 10-08“同一号码加来源 15 分钟内密码登录失败 5 次即锁 15 分钟”。
    验收：第 5 次失败后正确密码也 401 login_failed、不签发会话；15 分钟后可登录。
    """
    add_member(engine)
    for _ in range(5):
        assert_error(login(client, password=WRONG_PASSWORD), 401, LOGIN_FAILED)
    assert fake.count(LOCK_BUCKET, SOURCE, PHONE_MY) == 1

    fake.advance(LOCK_SECONDS - 1)
    blocked = login(client)

    assert_error(blocked, 401, LOGIN_FAILED)
    assert_no_cookie(blocked)
    assert sessions(engine) == []

    fake.advance(1)
    allowed = login(client)

    assert allowed.status_code == 204, allowed.text
    assert len(sessions(engine)) == 1


def test_four_failures_do_not_lock(client: TestClient, engine: Engine, fake: FakeRedis) -> None:
    """Kelvin 10-08“失败 5 次即锁”：不到 5 次不锁。验收：4 次失败后正确密码 204。"""
    add_member(engine)
    for _ in range(4):
        assert login(client, password=WRONG_PASSWORD).status_code == 401

    response = login(client)

    assert response.status_code == 204, response.text
    assert fake.count(LOCK_BUCKET, SOURCE, PHONE_MY) == 0


def test_lock_is_per_source_and_phone(client: TestClient, engine: Engine, fake: FakeRedis) -> None:
    """Kelvin 10-08“同一号码加来源……即锁”。
    验收：按来源与号码组合锁定——某来源对某号码锁定后，另一来源对同一号码、
    同一来源对另一号码用正确密码都 204。
    """
    add_member(engine)
    add_member(engine, phone=PHONE_OTHER)
    for _ in range(5):
        login(client, password=WRONG_PASSWORD)
    assert login(client).status_code == 401

    other_source = login(fresh(client), source=OTHER_SOURCE)
    other_phone = login(fresh(client), PHONE_OTHER)

    assert other_source.status_code == 204, other_source.text
    assert other_phone.status_code == 204, other_phone.text
    assert fake.count(LOCK_BUCKET, OTHER_SOURCE, PHONE_MY) == 0
    assert fake.count(LOCK_BUCKET, SOURCE, PHONE_OTHER) == 0


@pytest.mark.parametrize("failure", ["unconfigured", "lock_read_error", "failure_count_error"])
def test_redis_unavailable_is_503_without_session(
    app: FastAPI, client: TestClient, engine: Engine, fake: FakeRedis, failure: str
) -> None:
    """重试第 4 条“依赖 Redis 限流的敏感接口也拒绝请求”。
    验收：Redis 不可用时由路由类回答 503 service_unavailable，不放行登录、不签发
    会话、不设 cookie；未配置、查锁定时出错（正确密码）与计失败时出错（错误密码）
    三例；no-store。
    """
    add_member(engine)
    password = PASSWORD
    if failure == "unconfigured":
        app.dependency_overrides.pop(get_redis_client)
    elif failure == "lock_read_error":
        fake.fail_get.add(LOCK_BUCKET)
    else:
        fake.fail_incr.add(FAILURE_BUCKET)
        password = WRONG_PASSWORD

    response = login(client, password=password)

    assert_error(response, 503, UNAVAILABLE)
    assert_no_cookie(response)
    assert_no_secrets(response, SOURCE)
    assert sessions(engine) == []


def test_database_error_rolls_back_without_cookie(
    client: TestClient, engine: Engine, session_class: list[type[Session]]
) -> None:
    """验收：数据库出错时回滚，503 service_unavailable，不设置 cookie；错误不含号码。
    以提交时抛带号码参数的数据库异常模拟。
    """
    add_member(engine)
    session_class[0] = CommitFailingSession

    response = login(client)

    assert_error(response, 503, UNAVAILABLE)
    assert_no_cookie(response)
    assert_no_secrets(response, SOURCE)
    assert sessions(engine) == []


def test_login_works_with_sms_switch_off(client: TestClient, engine: Engine) -> None:
    """第 3 条：短信验证开关关闭期间已设密码的会员仍可密码登录。
    验收：不读短信验证开关——无设置行与开关关闭两例都 204。
    """
    add_member(engine)

    assert login(client).status_code == 204
    set_switch(engine, False)
    response = login(fresh(client))

    assert response.status_code == 204, response.text


# ---------------------------------------------------------------------------
# 密码登录：413 → 415 → 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content_type", ["text/plain", None, "application/json"])
def test_login_oversized_body_is_413_first(
    app: FastAPI, client: TestClient, content_type: str | None
) -> None:
    """验收：请求体逐块读，超过 4 KB 为 413，先于一切（415、422 与 Redis）；
    分块发送、不带 Content-Length 时也按实际读到的字节判断；不回显请求内容。
    """
    app.dependency_overrides.pop(get_redis_client)
    chunks = [b'{"password": "SECRET-PW' + b"9" * 3000, b'", "extra": "' + b"x" * 3000 + b'"}']
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = client.post(LOGIN_URL, content=iter(chunks), headers=headers)

    assert_error(response, 413, "request body too large")
    assert_no_secrets(response)


def test_login_body_at_4_kb_is_accepted(client: TestClient, engine: Engine) -> None:
    """验收：超过 4 KB 才 413——恰好 4 KB 照常处理，多 1 字节即 413。"""
    add_member(engine)
    headers = {"Content-Type": "application/json", "X-Real-IP": SOURCE}
    over = _padded(login_body(), MAX_BODY_BYTES + 1)
    at_limit = _padded(login_body(), MAX_BODY_BYTES)

    too_large = client.post(LOGIN_URL, content=over, headers=headers)
    accepted = client.post(LOGIN_URL, content=at_limit, headers=headers)

    assert_error(too_large, 413, "request body too large")
    assert accepted.status_code == 204, accepted.text


@pytest.mark.parametrize(
    "content_type", ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"]
)
def test_login_not_json_is_415_before_structure(
    app: FastAPI, client: TestClient, content_type: str | None
) -> None:
    """验收：不是 JSON 为 415，在 413 之后、422 与 Redis 之前
    （合法与多出字段的请求体都是 415）。
    """
    app.dependency_overrides.pop(get_redis_client)
    headers = {} if content_type is None else {"Content-Type": content_type}
    for body in (login_body(), login_body(extra="x")):
        response = client.post(LOGIN_URL, content=json.dumps(body).encode(), headers=headers)
        assert_error(response, 415, "request body must be JSON")
        assert_no_secrets(response)


LOGIN_STRUCTURE_CASES = [
    pytest.param(without("phone"), ("body", "phone"), "missing", id="missing-phone"),
    pytest.param(without("phone_region"), ("body", "phone_region"), "missing", id="missing-region"),
    pytest.param(without("password"), ("body", "password"), "missing", id="missing-password"),
    pytest.param(login_body(x="SECRET-X"), ("body", "x"), "extra_forbidden", id="extra"),
    pytest.param(login_body(phone=60123456789), ("body", "phone"), "string_type", id="phone-int"),
    pytest.param(
        login_body(phone_region=None), ("body", "phone_region"), "string_type", id="region-null"
    ),
    pytest.param(login_body(password=12345678), ("body", "password"), "string_type", id="pw-int"),
    pytest.param(login_body(password=""), ("body", "password"), "string_too_short", id="pw-empty"),
    pytest.param(
        login_body(password=LONG_PASSWORD), ("body", "password"), "string_too_long", id="257"
    ),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.parametrize(("body", "loc", "error_type"), LOGIN_STRUCTURE_CASES)
def test_login_structure_errors_are_422_without_echo(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """第 6 条：不记录密码。
    验收：请求体只有 phone、phone_region 与 password，严格类型，多出字段 422；
    密码为 1 到 256 个字符的字符串；422 沿用 _body_errors，只给位置、类型与固定
    消息，不回显请求内容（尤其是密码）；先于 Redis（未配置也是 422）；不签发会话。
    """
    app.dependency_overrides.pop(get_redis_client)
    add_member(engine)

    response = client.post(LOGIN_URL, json=body)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    assert_no_secrets(response)
    assert_no_cookie(response)
    assert sessions(engine) == []


def test_login_password_of_256_chars_passes_structure(client: TestClient, engine: Engine) -> None:
    """验收：密码字段是 1 到 256 个字符的字符串——256 个字符（非 ASCII）照常登录。"""
    password = "密" * 256
    add_member(engine, password_hash=hash_password(password))

    response = login(client, password=password)

    assert response.status_code == 204, response.text


@pytest.mark.parametrize(
    ("phone", "region"),
    [("0123", "MY"), ("SECRET", "MY"), (PHONE_LOCAL, "my"), (PHONE_LOCAL, "XX"), ("", "MY")],
)
def test_login_invalid_phone_is_422_phone_invalid(
    client: TestClient, fake: FakeRedis, phone: str, region: str
) -> None:
    """第 2 条：相同的 E.164 规范化结果。
    验收：规范化方式与 sms-login 相同，不成立 422（类型 phone_invalid），
    不回显请求内容，不计失败。
    """
    response = login(client, phone, region=region)

    expected = {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    assert_error(response, 422, [expected])
    assert_no_secrets(response)
    assert fake.values == {}


# ---------------------------------------------------------------------------
# 首次设置密码
# ---------------------------------------------------------------------------


def test_set_password_stores_hash_keeps_session_and_allows_login(
    client: TestClient, engine: Engine
) -> None:
    """第 3 条：首次设置密码在已登录会话内进行；会员可用手机号加密码登录。
    验收：用 hash_password 算哈希后写入并提交，204 且响应体为空；不撤销会话
    （原会话仍有效，has_password 变为 true）；之后可用新密码登录；no-store；
    不设 cookie；响应不含密码。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    response = set_password(client, token)

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    assert_no_cookie(response)
    assert_no_secrets(response, token)
    stored = password_hash_of(engine, member_id)
    assert stored is not None and NEW_PASSWORD not in stored
    assert verify_password(NEW_PASSWORD, stored)
    ((_, _, _, _, revoked_at),) = sessions(engine)
    assert revoked_at is None
    current = client.get(SESSION_URL, headers=cookie_header(token))
    assert current.status_code == 200, current.text
    assert current.json()["has_password"] is True

    relogin = login(fresh(client), password=NEW_PASSWORD)

    assert relogin.status_code == 204, relogin.text


@pytest.mark.parametrize("length", [8, 256])
def test_set_password_accepts_8_to_256_chars_without_complexity(
    client: TestClient, engine: Engine, length: int
) -> None:
    """第 3 条“密码至少 8 位、不强制复杂度”。
    验收：8 个字符（全是同一个字母）与 256 个字符都 204。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    response = set_password(client, token, "a" * length)

    assert response.status_code == 204, response.text
    stored = password_hash_of(engine, member_id)
    assert stored is not None and verify_password("a" * length, stored)


@pytest.mark.parametrize("password", ["1", "1234567", "密码密码密码密"])
def test_set_password_shorter_than_8_is_422(
    client: TestClient, engine: Engine, password: str
) -> None:
    """第 3 条“密码至少 8 位”。
    验收：不满足 password_length_ok 时 422 {"detail": "password_too_short"}，
    不写入；不回显密码。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    response = set_password(client, token, password)

    assert_error(response, 422, TOO_SHORT)
    assert password not in response.text
    assert password_hash_of(engine, member_id) is None


def test_set_password_when_already_set_is_409_and_keeps_hash(
    client: TestClient, engine: Engine
) -> None:
    """第 3 条：这里只是首次设置密码（重设另有流程）。
    验收：会员已设密码时 409 {"detail": "password_already_set"}，原哈希不变，
    原密码仍可登录、新密码不能。
    """
    member_id = add_member(engine)
    token = stored_session(engine, member_id)

    response = set_password(client, token)

    assert_error(response, 409, ALREADY_SET)
    assert password_hash_of(engine, member_id) == PASSWORD_HASH
    assert login(fresh(client), password=NEW_PASSWORD).status_code == 401
    assert login(fresh(client)).status_code == 204


def test_concurrent_set_completed_first_is_409_and_keeps_hash(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    """第 3 条：首次设置密码。
    验收：以条件更新（仅当该会员仍为 active 且密码哈希仍为空）写入；并发的另一次
    设置先完成时 409 password_already_set，原哈希不变。以会员依赖读到「尚未设密码」
    的旧值、库里已有哈希模拟：本请求读会员之后，另一次设置已提交。
    """
    member_id = add_member(engine)
    token = stored_session(engine, member_id)

    def stale_member(
        request: Request, db: Annotated[Session, Depends(get_session)]
    ) -> MemberContext:
        context = require_member(request, db)
        # 不标记为改动：这只是读到的旧值，不会被写回。
        set_committed_value(context.member, "password_hash", None)
        return context

    app.dependency_overrides[require_member] = stale_member

    response = set_password(client, token)

    assert_error(response, 409, ALREADY_SET)
    assert password_hash_of(engine, member_id) == PASSWORD_HASH


def test_set_password_works_with_sms_switch_off(client: TestClient, engine: Engine) -> None:
    """第 3 条：短信验证开关关闭期间，已登录会员首次设置密码不受影响。
    验收：不读短信验证开关。
    """
    set_switch(engine, False)
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    response = set_password(client, token)

    assert response.status_code == 204, response.text
    assert login(fresh(client), password=NEW_PASSWORD).status_code == 204


@pytest.mark.parametrize("state", ["missing", "malformed", "unknown", "revoked", "member_deleted"])
def test_set_password_requires_session(client: TestClient, engine: Engine, state: str) -> None:
    """第 3 条：首次设置密码在已登录会话内进行。
    验收：会话不通过 401 member_session_required（require_member），即使带着
    正确的 CSRF 令牌；不写入。
    """
    member_id = add_member(engine, password_hash=None)
    token: str | None = None
    if state == "malformed":
        token = "not-a-token"
    elif state == "unknown":
        token = "A" * 43
    elif state == "revoked":
        token = stored_session(engine, member_id, revoke=True)
    elif state == "member_deleted":
        token = stored_session(engine, member_id)
        delete_member(engine, member_id)
    headers = {} if token is None else member_headers(token)

    response = client.post(PASSWORD_URL, json={"password": NEW_PASSWORD}, headers=headers)

    assert_error(response, 401, SESSION_REQUIRED)
    assert_no_cookie(response)
    assert password_hash_of(engine, member_id) is None


@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated"])
def test_set_password_without_valid_csrf_is_403(
    client: TestClient, engine: Engine, csrf: str
) -> None:
    """第 5 条：写操作另须 CSRF 令牌。
    验收：CSRF 不通过 403 csrf_failed，此时不改密码。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)
    headers: Any
    if csrf == "missing":
        headers = cookie_header(token)
    elif csrf == "wrong":
        headers = {**cookie_header(token), "X-CSRF-Token": csrf_for("B" * 43)}
    else:
        headers = [
            ("Cookie", f"{COOKIE_NAME}={token}"),
            ("X-CSRF-Token", csrf_for(token)),
            ("X-CSRF-Token", csrf_for(token)),
        ]

    response = client.post(PASSWORD_URL, json={"password": NEW_PASSWORD}, headers=headers)

    assert_error(response, 403, CSRF_FAILED)
    assert password_hash_of(engine, member_id) is None


# ---------------------------------------------------------------------------
# 首次设置密码：413 → 415 → 401 → 403 → 422 → 长度规则
# ---------------------------------------------------------------------------


def test_set_password_oversized_without_session_is_413(client: TestClient) -> None:
    """验收：检查顺序 413 → 415 → 401——请求体超过 4 KB、没有会话也不是 JSON 时
    为 413；分块发送也按实际读到的字节判断；不回显请求内容。
    """
    chunks = [b'{"password": "SECRET-PW' + b"9" * 3000, b'", "x": "' + b"x" * 3000 + b'"}']
    headers = {"Content-Type": "text/plain"}

    response = client.post(PASSWORD_URL, content=iter(chunks), headers=headers)

    assert_error(response, 413, "request body too large")
    assert_no_secrets(response)


def test_set_password_body_at_4_kb_is_accepted(client: TestClient, engine: Engine) -> None:
    """验收：超过 4 KB 才 413——恰好 4 KB 照常处理，多 1 字节即 413。"""
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)
    headers = {**member_headers(token), "Content-Type": "application/json"}
    over = _padded({"password": NEW_PASSWORD}, MAX_BODY_BYTES + 1)
    at_limit = _padded({"password": NEW_PASSWORD}, MAX_BODY_BYTES)

    too_large = client.post(PASSWORD_URL, content=over, headers=headers)
    accepted = client.post(PASSWORD_URL, content=at_limit, headers=headers)

    assert_error(too_large, 413, "request body too large")
    assert accepted.status_code == 204, accepted.text


@pytest.mark.parametrize("content_type", ["text/plain", None, "application/jsonp"])
def test_set_password_not_json_without_session_is_415(
    client: TestClient, content_type: str | None
) -> None:
    """验收：检查顺序 413 → 415 → 401——不是 JSON 且没有会话时为 415。"""
    headers = {} if content_type is None else {"Content-Type": content_type}
    content = json.dumps({"password": NEW_PASSWORD}).encode()

    response = client.post(PASSWORD_URL, content=content, headers=headers)

    assert_error(response, 415, "request body must be JSON")
    assert_no_secrets(response)


def test_set_password_bad_structure_without_session_is_401(client: TestClient) -> None:
    """验收：检查顺序 401 → 403 → 422——没有会话且请求体结构不对时为 401，
    不回显请求内容。
    """
    response = client.post(PASSWORD_URL, json={"password": 12345678, "x": "SECRET-X"})

    assert_error(response, 401, SESSION_REQUIRED)
    assert_no_secrets(response)


def test_set_password_bad_body_without_csrf_is_403(client: TestClient, engine: Engine) -> None:
    """第 5 条：写操作另须 CSRF 令牌。
    验收：检查顺序 403 → 422——有会话、没有 CSRF 且请求体结构不对时为 403。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)
    body = {"password": 1, "x": "SECRET-X"}

    response = client.post(PASSWORD_URL, json=body, headers=cookie_header(token))

    assert_error(response, 403, CSRF_FAILED)
    assert_no_secrets(response)


SET_STRUCTURE_CASES = [
    pytest.param({}, ("body", "password"), "missing", id="missing"),
    pytest.param({"password": NEW_PASSWORD, "x": 1}, ("body", "x"), "extra_forbidden", id="extra"),
    pytest.param({"password": 12345678}, ("body", "password"), "string_type", id="int"),
    pytest.param({"password": None}, ("body", "password"), "string_type", id="null"),
    pytest.param({"password": ""}, ("body", "password"), "string_too_short", id="empty"),
    pytest.param({"password": LONG_PASSWORD}, ("body", "password"), "string_too_long", id="257"),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.parametrize(("body", "loc", "error_type"), SET_STRUCTURE_CASES)
def test_set_password_structure_errors_are_422_without_echo(
    client: TestClient, engine: Engine, body: Any, loc: tuple[str, ...], error_type: str
) -> None:
    """第 6 条：不记录密码。
    验收：请求体只有 password（1 到 256 个字符的字符串），严格类型，多出字段 422，
    在会话与 CSRF 之后、长度规则之前；422 不回显请求内容（尤其是密码）；不写入。
    """
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    response = client.post(PASSWORD_URL, json=body, headers=member_headers(token))

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    assert_no_secrets(response, token)
    assert password_hash_of(engine, member_id) is None


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [LOGIN_URL, PASSWORD_URL])
def test_wrong_method_is_405_without_request_content(client: TestClient, url: str) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.get(url, params={"phone": PHONE_MY, "password": PASSWORD})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert "SECRET" not in response.text


def test_no_logs_with_phone_password_or_token(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条：应用日志不记录完整电话与密码。
    验收：不写日志——设置密码（长度不足、成功、已设密码）、登录成功与失败、结构错误
    都不产生 app 下的日志；任何日志都不含号码、密码、令牌与来源。
    """
    caplog.set_level(logging.DEBUG)
    member_id = add_member(engine, password_hash=None)
    token = stored_session(engine, member_id)

    set_password(client, token, "short")
    set_password(client, token)
    set_password(client, token)
    login(fresh(client), password=NEW_PASSWORD)
    login(fresh(client), password=WRONG_PASSWORD)
    fresh(client).post(LOGIN_URL, json=login_body(extra=PASSWORD))

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", "SECRET", token, csrf_for(token), SOURCE):
            assert value not in message

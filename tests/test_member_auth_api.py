"""会员短信登录或注册、当前会话与退出接口：POST /api/member/sms-login、GET /api/member/session、
POST /api/member/logout 与依赖 require_member、require_member_csrf（app/api/member_auth.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 1 条（下称「第 1 条」）：“验证通过
  后，号码已注册且账号有效则登录该账号，未注册则创建无密码会员并登录，随后……自动认领该号码
  的游客订单”；第 3 条（下称「第 3 条」）：“注册、短信登录与结账验证是同一个短信验证流程”，
  “会话到期、退出与服务端授权检查”，“短信验证开关关闭期间：注册、短信登录与密码重设暂停”；
  第 5 条（下称「第 5 条」）：会话以服务端会话实现，经 HttpOnly、Secure、SameSite=Lax 的 cookie
  交给浏览器，写操作另须 CSRF 令牌；第 6 条（下称「第 6 条」）：“应用日志与监控不记录……完整
  电话……验证码”。
- 「失败、并发与重试」第 4 条（下称「重试第 4 条」）：“Redis 或 MySQL 不可用时短信停发”，
  “不得把验证码、短信凭据或完整手机号写进日志”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：“会员会话 30 天、
  不随使用延长”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；没有直接原句的，写明是
SHOP-TASK-076 验收标准里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_sms_api.py 相同：由引擎发 BEGIN，建会员的
保存点与 MySQL 上一样；允许跨线程用同一连接）。接口的会话依赖换成每个请求一个绑定同一内存库
的会话，请求结束关闭时回滚未提交的改动；检查结果一律在另一个数据库会话里读，读得到即说明
接口已提交。服务商用 SHOP-TASK-067 的测试替身 FakeSmsProvider；验证记录、会员、会话与订单
直接写库。期望的令牌摘要与 CSRF 令牌在这里另算，不取实现里的函数。
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.member_auth import mask_phone
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Member,
    MemberSession,
    Order,
    OrderRecipient,
    SiteSetting,
    VerificationAttempt,
)
from app.models.order import CLAIM_CLAIMED, CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.site import SITE_SETTING_ID
from app.services import member_sms_login
from app.services.member_auth import issue_member_session, revoke_member_session
from app.services.sms_provider import CheckStatus, FakeSmsProvider, get_sms_provider

LOGIN_URL = "/api/member/sms-login"
SESSION_URL = "/api/member/session"
LOGOUT_URL = "/api/member/logout"
MAX_BODY_BYTES = 4 * 1024

COOKIE_NAME = "__Host-shop_member_session"
THIRTY_DAYS_SECONDS = 30 * 24 * 3600
CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"

PHONE_LOCAL = "012-345 6789"
PHONE_MY = "+60123456789"
PHONE_MY_MASKED = "+60*****6789"
PHONE_SG = "+6581234567"
CODE = "482915"
PASSWORD_HASH = "scrypt$existing-password-hash"
FINGERPRINT = "0" * 64

NO_STORE = "no-store"
SESSION_REQUIRED = {"detail": "member_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}

_REQUEST_IDS = itertools.count(1)
_ORDER_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class RaisingProvider(FakeSmsProvider):
    """核验时抛出带号码与验证码的异常（模拟服务商调用出错）。"""

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        super().check_verification(request_id, code)
        raise RuntimeError(f"check failed for {PHONE_MY} with {code}")


def failing_claim(*_args: object, **_kwargs: object) -> int:
    """认领时抛出带号码参数的数据库异常（模拟认领时连接断开）。"""
    raise OperationalError("UPDATE orders", {"phone": PHONE_MY}, Exception("lost connection"))


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

    # 与 tests/test_sms_api.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
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


def make_settings() -> Settings:
    # 连接串显式为空：不覆盖会话依赖时即「未配置」。
    return Settings(_env_file=None, database_url="", redis_url="")


@pytest.fixture
def provider() -> FakeSmsProvider:
    return FakeSmsProvider()


@pytest.fixture
def session_class() -> list[type[Session]]:
    # 测试可把它换成 CommitFailingSession。
    return [Session]


@pytest.fixture
def app(engine: Engine, provider: FakeSmsProvider, session_class: list[type[Session]]) -> FastAPI:
    app = create_app(make_settings())

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_sms_provider] = lambda: provider
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
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


def add_attempt(engine: Engine, purpose: str = "login", phone: str = PHONE_MY) -> int:
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


def members(engine: Engine) -> list[tuple[int, str | None, str | None, str]]:
    """全部会员的（ID, 号码, 密码哈希, 状态），按 ID。"""
    return _read(
        engine,
        lambda s: [
            (row.id, row.phone, row.password_hash, row.status)
            for row in s.scalars(select(Member).order_by(Member.id))
        ],
    )


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


def stored_session(
    engine: Engine, member_id: int, *, issued_at: datetime | None = None, revoke: bool = False
) -> str:
    """直接写库签发一个会话（可选撤销）并提交，返回令牌原文。"""
    issued_at = issued_at or _now()
    with Session(engine) as session:
        member = session.get(Member, member_id)
        assert member is not None
        issued = issue_member_session(session, member, issued_at)
        if revoke:
            revoke_member_session(session, issued.session, issued_at + timedelta(seconds=1))
        session.commit()
        return issued.token


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "phone": PHONE_LOCAL,
        "phone_region": "MY",
        "purpose": "login",
        "code": CODE,
    }
    body.update(overrides)
    return body


def without(name: str) -> dict[str, Any]:
    body = payload()
    del body[name]
    return body


def post_raw(client: TestClient, content: Any, content_type: str | None) -> Any:
    headers = {} if content_type is None else {"Content-Type": content_type}
    return client.post(LOGIN_URL, content=content, headers=headers)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def assert_error(response: Any, status_code: int, detail: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == NO_STORE


def assert_no_cookie(response: Any) -> None:
    assert response.headers.get_list("set-cookie") == []


def assert_no_secrets(response: Any, *extra: str) -> None:
    """响应体不含号码原文、验证码与给出的令牌。"""
    for value in ("123456789", "345 6789", CODE, *extra):
        assert value not in response.text


def logged_in(client: TestClient, engine: Engine) -> str:
    """以短信登录（新号码即注册）并返回 cookie 里的令牌原文。"""
    add_attempt(engine)
    response = client.post(LOGIN_URL, json=payload())
    assert response.status_code == 200, response.text
    token = client.cookies.get(COOKIE_NAME)
    assert token
    return token


# ---------------------------------------------------------------------------
# 登录或注册
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("purpose", ["checkout", "register", "login"])
def test_registered_phone_logs_in_for_each_purpose(
    client: TestClient, engine: Engine, purpose: str
) -> None:
    """第 3 条“注册、短信登录与结账验证是同一个短信验证流程”；第 1 条“号码已注册且账号有效则
    登录该账号……随后……自动认领该号码的游客订单”。验收：approved 时核验记录、会员、认领与
    会话在同一个事务里提交，200 {"created": false}，不返回号码、会员 ID 与认领数。
    """
    member_id = add_member(engine)
    attempt_id = add_attempt(engine, purpose=purpose)
    order_id = add_order(engine)

    response = client.post(LOGIN_URL, json=payload(purpose=purpose))

    assert response.status_code == 200, response.text
    assert response.json() == {"created": False}
    assert response.headers["cache-control"] == NO_STORE
    token = client.cookies.get(COOKIE_NAME)
    assert token
    assert_no_secrets(response, token)
    assert members(engine) == [(member_id, PHONE_MY, PASSWORD_HASH, "active")]
    assert attempt_status(engine, attempt_id) == "approved"
    assert claim_of(engine, order_id) == (member_id, CLAIM_CLAIMED)
    ((owner, stored_hash, _, _, revoked_at),) = sessions(engine)
    assert owner == member_id
    assert stored_hash == token_hash(token)
    assert revoked_at is None


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("purpose", ["checkout", "register", "login"])
def test_unregistered_phone_registers_for_each_purpose(
    client: TestClient, engine: Engine, purpose: str
) -> None:
    """第 1 条“未注册则创建无密码会员并登录，随后……自动认领该号码的游客订单”；第 3 条“同一个
    短信验证流程”。验收：200 {"created": true}；会员（active、无密码）、认领与会话都已提交。
    另一号码的会员不受影响。
    """
    other_id = add_member(engine, phone=PHONE_SG)
    attempt_id = add_attempt(engine, purpose=purpose)
    order_id = add_order(engine)

    response = client.post(LOGIN_URL, json=payload(purpose=purpose))

    assert response.status_code == 200, response.text
    assert response.json() == {"created": True}
    ((_, _, _, _), (new_id, phone, password_hash, status)) = members(engine)
    assert new_id != other_id
    assert (phone, password_hash, status) == (PHONE_MY, None, "active")
    assert attempt_status(engine, attempt_id) == "approved"
    assert claim_of(engine, order_id) == (new_id, CLAIM_CLAIMED)
    ((owner, stored_hash, _, _, _),) = sessions(engine)
    assert owner == new_id
    assert stored_hash == token_hash(client.cookies[COOKIE_NAME])
    assert str(new_id) not in response.text


@pytest.mark.usefixtures("enabled")
def test_cookie_name_and_attributes(client: TestClient, engine: Engine) -> None:
    """第 5 条：会话经 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器；Kelvin 10-08“会员
    会话 30 天”。验收：cookie 名 __Host-shop_member_session，值为签发的令牌原文，Path=/、不设
    Domain，Max-Age 为 30 天的秒数；库里只有令牌摘要，到期为签发加 30 天。
    """
    add_attempt(engine)

    response = client.post(LOGIN_URL, json=payload())

    assert response.status_code == 200, response.text
    (cookie,) = response.headers.get_list("set-cookie")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    name, token = name_value.split("=", 1)
    assert name == COOKIE_NAME
    lowered = [attribute.lower() for attribute in attributes]
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert f"max-age={THIRTY_DAYS_SECONDS}" in lowered
    assert not any(attribute.startswith("domain") for attribute in lowered)
    ((_, stored_hash, created_at, expires_at, _),) = sessions(engine)
    assert stored_hash == token_hash(token)
    assert expires_at - created_at == timedelta(days=30)
    assert token not in response.text


@pytest.mark.usefixtures("enabled")
def test_existing_cookie_is_overwritten_by_new_session(client: TestClient, engine: Engine) -> None:
    """第 3 条“同一个短信验证流程”。验收：请求已带会员会话 cookie 时照常处理并以新会话覆盖
    cookie（第二次登录不新建会员）。
    """
    first = logged_in(client, engine)
    add_attempt(engine)

    response = client.post(LOGIN_URL, json=payload())

    assert response.status_code == 200, response.text
    assert response.json() == {"created": False}
    second = client.cookies.get(COOKIE_NAME)
    assert second and second != first
    assert len(members(engine)) == 1
    assert [stored_hash for _, stored_hash, *_ in sessions(engine)] == [
        token_hash(first),
        token_hash(second),
    ]


# ---------------------------------------------------------------------------
# 核验不通过与数据库错误
# ---------------------------------------------------------------------------


NON_APPROVED_CASES = [
    pytest.param(CheckStatus.WRONG_CODE, True, 422, "code_wrong", "sent", id="wrong_code"),
    pytest.param(CheckStatus.EXPIRED, True, 422, "code_expired", "rejected", id="expired"),
    pytest.param(CheckStatus.APPROVED, False, 422, "code_expired", None, id="no_pending"),
    pytest.param(CheckStatus.UNAVAILABLE, True, 503, "sms_unavailable", "sent", id="unavailable"),
]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("check", "pending", "status_code", "code", "after"), NON_APPROVED_CASES)
def test_non_approved_results_answer_without_cookie_or_member(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    check: CheckStatus,
    pending: bool,
    status_code: int,
    code: str,
    after: str | None,
) -> None:
    """第 1 条：只有“验证通过后”才登录或注册并认领。验收：wrong_code 为 422 code_wrong；expired
    与 no_pending 都为 422 code_expired（须重新发送）；unavailable 为 503 sms_unavailable；
    不设 cookie、不建会员、不签发会话、不认领；非 approved 时也先提交——expired 时记录已提交为
    rejected。
    """
    provider.check_result = check
    attempt_id = add_attempt(engine) if pending else None
    order_id = add_order(engine)

    response = client.post(LOGIN_URL, json=payload())

    assert_error(response, status_code, code)
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert members(engine) == []
    assert sessions(engine) == []
    assert claim_of(engine, order_id) == (None, CLAIM_OPEN)
    if attempt_id is not None:
        assert attempt_status(engine, attempt_id) == after


def test_sms_disabled_is_403_without_cookie(
    client: TestClient, engine: Engine, provider: FakeSmsProvider
) -> None:
    """第 3 条“短信验证开关关闭期间：注册、短信登录与密码重设暂停”。验收：sms_disabled 为 403
    sms_disabled，不设 cookie、不建会员；无设置行与开关关闭两例，都不调用服务商。
    """
    attempt_id = add_attempt(engine)

    assert_error(client.post(LOGIN_URL, json=payload()), 403, "sms_disabled")
    set_switch(engine, False)
    response = client.post(LOGIN_URL, json=payload())

    assert_error(response, 403, "sms_disabled")
    assert_no_cookie(response)
    assert provider.check_calls == 0
    assert members(engine) == []
    assert sessions(engine) == []
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
def test_wrong_code_does_not_log_in_registered_member(
    client: TestClient, engine: Engine, provider: FakeSmsProvider
) -> None:
    """第 1 条：只有“验证通过后”才登录该账号。验收：验证码错误时 422 code_wrong，已注册号码的
    会员不被登录（不签发会话、不设 cookie）。
    """
    member_id = add_member(engine)
    add_attempt(engine)
    provider.check_result = CheckStatus.WRONG_CODE

    response = client.post(LOGIN_URL, json=payload())

    assert_error(response, 422, "code_wrong")
    assert_no_cookie(response)
    assert members(engine) == [(member_id, PHONE_MY, PASSWORD_HASH, "active")]
    assert sessions(engine) == []


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("code", ["abc", "123", "12345678901", "48 2915"])
def test_code_format_is_judged_by_verify_function(
    client: TestClient, engine: Engine, provider: FakeSmsProvider, code: str
) -> None:
    """验收：code 为 1 到 16 个字符的字符串即通过结构校验，是否为 4 到 10 位数字由核验函数
    判断——不合即 422 code_wrong，不调用服务商；不回显验证码。
    """
    attempt_id = add_attempt(engine)

    response = client.post(LOGIN_URL, json=payload(code=code))

    assert_error(response, 422, "code_wrong")
    assert_no_cookie(response)
    assert code not in response.text
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("failure", ["provider", "claim", "commit"])
def test_database_or_provider_error_rolls_back_without_cookie(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    session_class: list[type[Session]],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """重试第 4 条“Redis 或 MySQL 不可用时短信停发”，“不得把验证码……或完整手机号写进日志”。
    验收：核验或本流程出现数据库错误（含 SmsVerificationError 与提交失败）时回滚，503
    sms_unavailable，不设置 cookie。服务商抛异常（SmsVerificationError）、认领时数据库异常
    （MemberSmsLoginError）与提交失败三例；记录仍为 sent、不留会员与会话、订单未认领；错误
    不含号码与验证码。
    """
    attempt_id = add_attempt(engine)
    order_id = add_order(engine)
    if failure == "provider":
        app.dependency_overrides[get_sms_provider] = lambda: RaisingProvider()
    elif failure == "claim":
        monkeypatch.setattr(member_sms_login, "_claim_guest_orders", failing_claim)
    else:
        session_class[0] = CommitFailingSession

    response = client.post(LOGIN_URL, json=payload())

    assert_error(response, 503, "sms_unavailable")
    assert_no_cookie(response)
    assert_no_secrets(response)
    assert attempt_status(engine, attempt_id) == "sent"
    assert members(engine) == []
    assert sessions(engine) == []
    assert claim_of(engine, order_id) == (None, CLAIM_OPEN)


def test_database_not_configured_keeps_get_session_answer(provider: FakeSmsProvider) -> None:
    """验收：数据库未配置时照 app/db/session.py 的 get_session 现有回答（503），不另转换；
    经路由类带 no-store；不设 cookie、不调用服务商。三个接口都是。
    """
    app = create_app(make_settings())
    app.dependency_overrides[get_sms_provider] = lambda: provider
    client = TestClient(app, base_url="https://testserver")
    token = "A" * 43
    headers = {**cookie_header(token), "X-CSRF-Token": csrf_for(token)}

    responses = [
        client.post(LOGIN_URL, json=payload()),
        client.get(SESSION_URL, headers=cookie_header(token)),
        client.post(LOGOUT_URL, headers=headers),
    ]

    for response in responses:
        assert_error(response, 503, "database is not configured")
        assert_no_cookie(response)
    assert provider.check_calls == 0


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
    client: TestClient, engine: Engine, provider: FakeSmsProvider
) -> None:
    """验收：请求体逐块读，超过 4 KB 为 413，先于一切；413 不回显请求内容。"""
    add_attempt(engine)

    over = post_raw(client, _padded(MAX_BODY_BYTES + 1), "application/json")
    assert_error(over, 413, "request body too large")
    assert_no_cookie(over)
    assert provider.check_calls == 0

    at_limit = post_raw(client, _padded(MAX_BODY_BYTES), "application/json")
    assert at_limit.status_code == 200, at_limit.text


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/json"],
    ids=["not-json", "no-content-type", "json"],
)
def test_oversized_body_is_413_before_content_type_and_structure(
    client: TestClient, provider: FakeSmsProvider, content_type: str | None
) -> None:
    """验收：413（超过 4 KB）先于 415 与 422；分块发送、不带 Content-Length 时也按实际读到的
    字节判断。请求体不合法且多出字段，不回显。
    """
    chunks = [b'{"phone": "' + b"9" * 3000, b'", "member_id": "' + b"x" * 3000 + b'"}']

    response = post_raw(client, iter(chunks), content_type)

    assert_error(response, 413, "request body too large")
    assert "999" not in response.text
    assert provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", None, "application/x-www-form-urlencoded", "application/jsonp"],
)
def test_not_json_is_415_before_structure(
    client: TestClient, provider: FakeSmsProvider, content_type: str | None
) -> None:
    """验收：不是 JSON 为 415，在 413 之后、422 之前（合法与多出字段的请求体都是 415）。"""
    for body in (payload(), payload(extra="x")):
        response = post_raw(client, json.dumps(body).encode(), content_type)
        assert_error(response, 415, "request body must be JSON")
        assert_no_secrets(response)
    assert provider.check_calls == 0


STRUCTURE_CASES = [
    pytest.param(without("phone"), ("body", "phone"), "missing", id="missing-phone"),
    pytest.param(without("phone_region"), ("body", "phone_region"), "missing", id="missing-region"),
    pytest.param(without("purpose"), ("body", "purpose"), "missing", id="missing-purpose"),
    pytest.param(without("code"), ("body", "code"), "missing", id="missing-code"),
    pytest.param(
        payload(member_id="SECRET-EXTRA"), ("body", "member_id"), "extra_forbidden", id="extra"
    ),
    pytest.param(payload(phone=60123456789), ("body", "phone"), "string_type", id="phone-int"),
    pytest.param(payload(phone_region=None), ("body", "phone_region"), "string_type", id="region"),
    pytest.param(payload(code=482915), ("body", "code"), "string_type", id="code-int"),
    pytest.param(payload(code=""), ("body", "code"), "string_too_short", id="code-empty"),
    pytest.param(payload(code="4" * 17), ("body", "code"), "string_too_long", id="code-17"),
    pytest.param(
        payload(purpose="reset_password"), ("body", "purpose"), "literal_error", id="reset"
    ),
    pytest.param(
        payload(purpose="delete_account"), ("body", "purpose"), "literal_error", id="delete"
    ),
    pytest.param(payload(purpose="LOGIN"), ("body", "purpose"), "literal_error", id="upper"),
    pytest.param(["SECRET-LIST"], ("body",), "model_type", id="not-an-object"),
]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("body", "loc", "error_type"), STRUCTURE_CASES)
def test_structure_errors_are_422_without_echo(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    body: Any,
    loc: tuple[str, ...],
    error_type: str,
) -> None:
    """第 3 条“注册、短信登录与结账验证是同一个短信验证流程”（用途只有这三种）。验收：四个
    字段都必填、严格类型，多出字段 422；code 为 1 到 16 个字符；422 沿用 _body_errors，只给
    位置、类型与固定消息，不回显请求内容；不调用服务商、不设 cookie。
    """
    attempt_id = add_attempt(engine)

    response = client.post(LOGIN_URL, json=body)

    assert response.status_code == 422
    assert response.headers["cache-control"] == NO_STORE
    errors = response.json()["detail"]
    assert [(tuple(e["loc"]), e["type"]) for e in errors] == [(loc, error_type)]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
    for secret in ("SECRET", "345 6789", "4" * 17, "482915"):
        assert secret not in response.text
    assert_no_cookie(response)
    assert provider.check_calls == 0
    assert attempt_status(engine, attempt_id) == "sent"


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "region"),
    [("0123", "MY"), ("SECRET", "MY"), ("012-345 6789", "my"), ("012-345 6789", "XX"), ("", "MY")],
)
def test_invalid_phone_is_422_phone_invalid_without_echo(
    client: TestClient, provider: FakeSmsProvider, phone: str, region: str
) -> None:
    """第 2 条“注册、查单和订单认领都用相同的 E.164 规范化结果”。验收：规范化方式与
    POST /api/sms/send 相同（经 normalize_phone），号码不成立为 422，类型 phone_invalid，
    不回显请求内容。
    """
    response = client.post(LOGIN_URL, json=payload(phone=phone, phone_region=region))

    expected = {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    assert_error(response, 422, [expected])
    assert "SECRET" not in response.text
    assert provider.check_calls == 0


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "region", "stored"),
    [
        (PHONE_LOCAL, "MY", PHONE_MY),
        ("+60 12-345 6789", "SG", PHONE_MY),
        ("8123 4567", "SG", PHONE_SG),
    ],
)
def test_phone_is_normalized_like_sms_send(
    client: TestClient, engine: Engine, phone: str, region: str, stored: str
) -> None:
    """第 2 条“相同的 E.164 规范化结果”。验收：phone 与 phone_region 的规范化方式与
    POST /api/sms/send 相同（以加号开头时以输入为准）；核验与建会员都用规范化结果。
    """
    add_attempt(engine, phone=stored)

    response = client.post(LOGIN_URL, json=payload(phone=phone, phone_region=region))

    assert response.status_code == 200, response.text
    ((_, member_phone, _, _),) = members(engine)
    assert member_phone == stored


# ---------------------------------------------------------------------------
# 当前会话
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
def test_session_returns_four_fields_and_does_not_extend(
    client: TestClient, engine: Engine
) -> None:
    """第 3 条“会话到期……与服务端授权检查”；Kelvin 10-08“会员会话 30 天、不随使用延长”。验收：
    GET /api/member/session 返回 phone_masked（前 3 个与最后 4 个字符，中间每个字符换成星号）、
    has_password（新注册无密码为 false）、expires_at（带时区的 UTC，与库里相同）与 csrf_token
    （由 cookie 算出）；只读、不延长会话；no-store；不含号码原文与令牌。
    """
    token = logged_in(client, engine)
    before = sessions(engine)

    first = client.get(SESSION_URL)
    second = client.get(SESSION_URL)

    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == NO_STORE
    body = first.json()
    assert set(body) == {"phone_masked", "has_password", "expires_at", "csrf_token"}
    assert body["phone_masked"] == PHONE_MY_MASKED
    assert body["has_password"] is False
    assert body["csrf_token"] == csrf_for(token)
    assert body["expires_at"].endswith("Z")
    ((_, _, _, expires_at, _),) = before
    assert datetime.fromisoformat(body["expires_at"]) == expires_at.replace(tzinfo=UTC)
    assert second.json() == body
    assert sessions(engine) == before
    assert "123456789" not in first.text
    assert token not in first.text


def test_session_reports_password_for_member_with_password(
    client: TestClient, engine: Engine
) -> None:
    """第 3 条“未设密码的账号只能短信登录”（页面据此区分）。验收：has_password 为是否已设密码，
    已设密码的会员为 true；新加坡号码同样遮盖。
    """
    member_id = add_member(engine, phone=PHONE_SG)
    token = stored_session(engine, member_id)

    response = client.get(SESSION_URL, headers=cookie_header(token))

    assert response.status_code == 200, response.text
    assert response.json()["has_password"] is True
    assert response.json()["phone_masked"] == "+65****4567"


@pytest.mark.parametrize(
    ("phone", "masked"),
    [
        (PHONE_MY, PHONE_MY_MASKED),
        (PHONE_SG, "+65****4567"),
        ("+1234567", "+12*4567"),
        ("+123456", "*******"),
    ],
)
def test_mask_phone_keeps_first_three_and_last_four(phone: str, masked: str) -> None:
    """验收：phone_masked 保留号码前 3 个字符与最后 4 个字符、中间每个字符换成星号。
    不足 8 个字符时前后会重叠，整串换成星号（实现的取舍，见 TODO 记录段）。
    """
    assert mask_phone(phone) == masked
    assert len(mask_phone(phone)) == len(phone)


@pytest.mark.parametrize(
    "state", ["missing", "malformed", "unknown", "expired", "revoked", "member_deleted"]
)
def test_session_and_logout_require_valid_session(
    client: TestClient, engine: Engine, state: str
) -> None:
    """第 3 条“会话到期、退出与服务端授权检查”；Kelvin 10-08“会员会话 30 天”。验收：
    require_member 不通过即 401 member_session_required，不区分会话不存在、已撤销、已到期或
    会员已注销；已撤销或已到期的会话带着正确的 CSRF 令牌退出也是 401（先于 CSRF）；不设
    cookie；no-store。
    """
    now = _now()
    token: str | None = None
    if state == "malformed":
        token = "not-a-token"
    elif state == "unknown":
        token = "A" * 43
    elif state == "expired":
        member_id = add_member(engine)
        token = stored_session(engine, member_id, issued_at=now - timedelta(days=30, seconds=1))
    elif state == "revoked":
        member_id = add_member(engine)
        token = stored_session(engine, member_id, revoke=True)
    elif state == "member_deleted":
        member_id = add_member(engine)
        token = stored_session(engine, member_id)
        delete_member(engine, member_id)
    headers = {} if token is None else cookie_header(token)
    logout_headers = dict(headers)
    if token is not None:
        logout_headers["X-CSRF-Token"] = csrf_for(token)
    before = sessions(engine)

    session = client.get(SESSION_URL, headers=headers)
    logout = client.post(LOGOUT_URL, headers=logout_headers)

    for response in (session, logout):
        assert_error(response, 401, SESSION_REQUIRED["detail"])
        assert_no_cookie(response)
    assert sessions(engine) == before


@pytest.mark.usefixtures("enabled")
def test_session_survives_sms_switch_off(client: TestClient, engine: Engine) -> None:
    """第 3 条“短信验证开关关闭期间：注册、短信登录与密码重设暂停”——只暂停这三项。验收：
    require_member 不受短信验证开关影响，开关关闭后已建立的会话仍可读取，也仍可退出。
    """
    token = logged_in(client, engine)
    set_switch(engine, False)

    session = client.get(SESSION_URL)
    logout = client.post(LOGOUT_URL, headers={"X-CSRF-Token": csrf_for(token)})

    assert session.status_code == 200, session.text
    assert session.json()["phone_masked"] == PHONE_MY_MASKED
    assert logout.status_code == 204, logout.text


# ---------------------------------------------------------------------------
# 退出
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("csrf", ["missing", "wrong", "duplicated", "admin_prefix"])
def test_logout_without_valid_csrf_is_403_and_keeps_session(
    client: TestClient, engine: Engine, csrf: str
) -> None:
    """第 5 条：写操作另须 CSRF 令牌。验收：退出先过 require_member_csrf，X-CSRF-Token 缺失、
    错误、多个（即使都正确）或用后台会话前缀算出的值都 403 csrf_failed，不撤销会话、不清
    cookie，之后取会话照常 200；no-store。
    """
    token = logged_in(client, engine)
    if csrf == "missing":
        headers: Any = {}
    elif csrf == "wrong":
        headers = {"X-CSRF-Token": csrf_for("B" * 43)}
    elif csrf == "duplicated":
        headers = [("X-CSRF-Token", csrf_for(token)), ("X-CSRF-Token", csrf_for(token))]
    else:
        admin_prefix = b"acuven-shop/admin-session/csrf\x00"
        headers = {"X-CSRF-Token": hashlib.sha256(admin_prefix + token.encode()).hexdigest()}

    response = client.post(LOGOUT_URL, headers=headers)

    assert_error(response, 403, CSRF_FAILED["detail"])
    assert_no_cookie(response)
    ((_, _, _, _, revoked_at),) = sessions(engine)
    assert revoked_at is None
    assert client.get(SESSION_URL).status_code == 200


@pytest.mark.usefixtures("enabled")
def test_logout_revokes_session_and_clears_cookie(client: TestClient, engine: Engine) -> None:
    """第 3 条“会话到期、退出与服务端授权检查”。验收：CSRF 通过后撤销当前会话、提交、清除
    cookie（名称、Path、Secure、HttpOnly、SameSite 与设置时相同，不设 Domain，Max-Age 为 0），
    204 且响应体为空；之后用原 cookie 取会话 401；no-store。
    """
    token = logged_in(client, engine)

    response = client.post(LOGOUT_URL, headers={"X-CSRF-Token": csrf_for(token)})

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert response.headers["cache-control"] == NO_STORE
    (cookie,) = response.headers.get_list("set-cookie")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    assert name_value.split("=", 1)[0] == COOKIE_NAME
    assert token not in cookie
    lowered = [attribute.lower() for attribute in attributes]
    assert "max-age=0" in lowered
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert not any(attribute.startswith("domain") for attribute in lowered)
    ((_, _, _, _, revoked_at),) = sessions(engine)
    assert revoked_at is not None

    again = TestClient(client.app, base_url="https://testserver").get(
        SESSION_URL, headers=cookie_header(token)
    )
    assert_error(again, 401, SESSION_REQUIRED["detail"])


@pytest.mark.usefixtures("enabled")
def test_logout_revokes_only_current_session(client: TestClient, engine: Engine) -> None:
    """第 3 条“退出”只结束当前会话。验收：撤销当前会话；同一会员的另一个会话仍可读取。"""
    token = logged_in(client, engine)
    ((member_id, *_),) = sessions(engine)
    other = stored_session(engine, member_id)

    response = client.post(LOGOUT_URL, headers={"X-CSRF-Token": csrf_for(token)})

    assert response.status_code == 204, response.text
    fresh = TestClient(client.app, base_url="https://testserver")
    assert fresh.get(SESSION_URL, headers=cookie_header(other)).status_code == 200


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [("GET", LOGIN_URL), ("POST", SESSION_URL), ("GET", LOGOUT_URL)],
)
def test_wrong_method_is_405_without_request_content(
    client: TestClient, method: str, url: str
) -> None:
    """验收：路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料。"""
    response = client.request(method, url, params={"phone": PHONE_MY, "code": CODE})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert CODE not in response.text


@pytest.mark.usefixtures("enabled")
def test_no_logs_with_phone_code_or_token(
    client: TestClient,
    engine: Engine,
    provider: FakeSmsProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 6 条“应用日志与监控不记录……完整电话……验证码”；重试第 4 条“不得把验证码……或完整
    手机号写进日志”。验收：cookie 值与令牌不出现在日志里——登录成功、验证码错误、结构错误、
    取会话与退出都不产生 app 下的日志，任何日志都不含号码、验证码与令牌。
    """
    caplog.set_level(logging.DEBUG)

    token = logged_in(client, engine)
    client.get(SESSION_URL)
    client.post(LOGOUT_URL, headers={"X-CSRF-Token": csrf_for(token)})
    provider.check_result = CheckStatus.WRONG_CODE
    add_attempt(engine)
    client.post(LOGIN_URL, json=payload())
    client.post(LOGIN_URL, json=payload(extra=CODE))

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", CODE, token, csrf_for(token)):
            assert value not in message

"""取会员本人完整手机号接口：GET /api/member/phone（app/api/member_auth.py）。

依据 docs/DESIGN.md 1.14（提交 00f8f34）「权限与资料保护」第 8 条「会员结账预填本人手机号」
（下称「第 8 条」）与 docs/HANDOFF.md 0.63。第 8 条里本文件守住的各句：
- 「取本人」：“前台可经一个只认会员会话的只读接口取本人完整的 E.164 手机号，使预填值始终以
  `+` 开头”。
- 「只返回」：“该接口用 GET、不要求 CSRF，只返回这一个值，不带收货资料、令牌或其他字段”。
- 「401」：“会话不通过时与其他会员接口相同回答 401，一律不返回号码”。
- 「开关」：“不受短信验证开关影响”。
- 「限流审计」：“不另设限流与审计记录（读的是本人账号上的号码）”。
- 「no-store」：“响应带 no-store，号码不写入日志、监控、分析事件或错误回显”。
- 「遮掩」：“会员账号上的手机号，会员会话接口只给遮掩后的形式”。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的那一句；只读、不延长会话等没有直接
原句的，写明是 SHOP-TASK-156 验收标准里的约定（下称「验收」）。

夹具照 tests/test_member_auth_api.py：SQLite 内存库按模型建表（由引擎发 BEGIN、允许跨线程用
同一连接），接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动；会员
与会话直接写库，检查结果在另一个数据库会话里读。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Member, MemberSession, SiteSetting
from app.models.site import SITE_SETTING_ID
from app.services.member_auth import issue_member_session, revoke_member_session

PHONE_URL = "/api/member/phone"
SESSION_URL = "/api/member/session"

COOKIE_NAME = "__Host-shop_member_session"

PHONE_MY = "+60123456789"
PHONE_MY_MASKED = "+60*****6789"
PHONE_SG = "+6581234567"
PASSWORD_HASH = "scrypt$existing-password-hash"

NO_STORE = "no-store"
SESSION_REQUIRED = {"detail": "member_session_required"}


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
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


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
    """在另一个数据库会话里读：读得到的只有已提交的改动。"""
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


def all_rows(engine: Engine) -> tuple[list[Any], list[Any]]:
    """会员表与会员会话表的全部行（每一列），按 ID。"""
    members_table = Member.__table__
    sessions_table = MemberSession.__table__
    return _read(
        engine,
        lambda s: (
            [tuple(r) for r in s.execute(select(members_table).order_by(members_table.c.id))],
            [tuple(r) for r in s.execute(select(sessions_table).order_by(sessions_table.c.id))],
        ),
    )


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def get_phone(client: TestClient, token: str, **headers: str) -> Any:
    return client.get(PHONE_URL, headers={**cookie_header(token), **headers})


def assert_phone(response: Any, phone: str) -> None:
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == NO_STORE
    assert response.json() == {"phone": phone}


def assert_session_required(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == SESSION_REQUIRED
    assert response.headers["cache-control"] == NO_STORE
    assert response.headers.get_list("set-cookie") == []
    for value in ("123456789", "81234567", "6789", "4567"):
        assert value not in response.text


# ---------------------------------------------------------------------------
# 200
# ---------------------------------------------------------------------------


def test_logged_in_member_gets_only_own_e164_phone(client: TestClient, engine: Engine) -> None:
    """第 8 条「取本人」“取本人完整的 E.164 手机号，使预填值始终以 `+` 开头”；「只返回」“只返回
    这一个值，不带收货资料、令牌或其他字段”；「no-store」“响应带 no-store”。验收：响应体的键
    只有 phone，值为会员账号上的号码原文；不设 cookie，不含令牌。
    """
    member_id = add_member(engine)
    token = stored_session(engine, member_id)

    response = get_phone(client, token)

    assert_phone(response, PHONE_MY)
    assert set(response.json()) == {"phone"}
    assert response.json()["phone"].startswith("+")
    assert response.headers.get_list("set-cookie") == []
    assert token not in response.text


def test_two_members_each_get_their_own_phone(app: FastAPI, engine: Engine) -> None:
    """第 8 条「取本人」“只认会员会话的只读接口取本人完整的 E.164 手机号”。验收：两个会员各自
    只取到自己的号码，取不到对方的。
    """
    my_token = stored_session(engine, add_member(engine, phone=PHONE_MY))
    sg_token = stored_session(engine, add_member(engine, phone=PHONE_SG, password_hash=None))

    my = get_phone(TestClient(app, base_url="https://testserver"), my_token)
    sg = get_phone(TestClient(app, base_url="https://testserver"), sg_token)

    assert_phone(my, PHONE_MY)
    assert_phone(sg, PHONE_SG)
    assert PHONE_SG not in my.text
    assert PHONE_MY not in sg.text


@pytest.mark.parametrize("csrf", ["missing", "wrong"])
def test_csrf_token_is_not_required(client: TestClient, engine: Engine, csrf: str) -> None:
    """第 8 条「只返回」“该接口用 GET、不要求 CSRF”。验收：不带 X-CSRF-Token（或带了不对的值）
    照常 200。
    """
    token = stored_session(engine, add_member(engine))
    headers = {} if csrf == "missing" else {"X-CSRF-Token": "0" * 64}

    response = get_phone(client, token, **headers)

    assert_phone(response, PHONE_MY)


@pytest.mark.parametrize("switch", ["no_row", "enabled", "disabled"])
def test_sms_switch_does_not_matter(client: TestClient, engine: Engine, switch: str) -> None:
    """第 8 条「开关」“不受短信验证开关影响”。验收：开关开启、关闭与没有设置行三种情形结果
    相同，都 200 本人号码。
    """
    if switch != "no_row":
        set_switch(engine, switch == "enabled")
    token = stored_session(engine, add_member(engine))

    response = get_phone(client, token)

    assert_phone(response, PHONE_MY)


def test_reading_writes_nothing_and_does_not_extend_session(
    client: TestClient, engine: Engine
) -> None:
    """第 8 条「取本人」“只读接口”；「限流审计」“不另设限流与审计记录”。验收：只读，不写库、
    不延长会话——连读两次前后会员表与会员会话表的各列都不变，也没有新增行。
    """
    member_id = add_member(engine)
    token = stored_session(engine, member_id)
    add_member(engine, phone=PHONE_SG)
    before = all_rows(engine)

    first = get_phone(client, token)
    second = get_phone(client, token)

    assert_phone(first, PHONE_MY)
    assert_phone(second, PHONE_MY)
    assert all_rows(engine) == before


# ---------------------------------------------------------------------------
# 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state", ["missing", "malformed", "unknown", "expired", "revoked", "member_deleted"]
)
def test_invalid_session_is_401_without_phone(
    client: TestClient, engine: Engine, state: str
) -> None:
    """第 8 条「401」“会话不通过时与其他会员接口相同回答 401，一律不返回号码”；「no-store」
    “响应带 no-store”。验收：没有 cookie、格式不对、不存在、已到期、已撤销与会员已注销都 401
    member_session_required，不加区分，响应体不含号码；不设 cookie，不写库。
    """
    token: str | None = None
    if state == "malformed":
        token = "not-a-token"
    elif state == "unknown":
        token = "A" * 43
    elif state == "expired":
        member_id = add_member(engine)
        token = stored_session(engine, member_id, issued_at=_now() - timedelta(days=30, seconds=1))
    elif state == "revoked":
        member_id = add_member(engine)
        token = stored_session(engine, member_id, revoke=True)
    elif state == "member_deleted":
        member_id = add_member(engine)
        token = stored_session(engine, member_id)
        delete_member(engine, member_id)
    # 另一个会员的会话有效，证明 401 不是因为库里没有可用号码。
    stored_session(engine, add_member(engine, phone=PHONE_SG))
    before = all_rows(engine)

    response = client.get(PHONE_URL, headers={} if token is None else cookie_header(token))

    assert_session_required(response)
    assert all_rows(engine) == before


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------


def test_session_endpoint_still_gives_masked_phone_only(client: TestClient, engine: Engine) -> None:
    """第 8 条「遮掩」“会员账号上的手机号，会员会话接口只给遮掩后的形式”。验收：
    GET /api/member/session 的 phone_masked 仍为遮掩形式、响应不含完整号码，同一会话取号接口
    给完整号码。
    """
    token = stored_session(engine, add_member(engine))

    session = client.get(SESSION_URL, headers=cookie_header(token))
    phone = get_phone(client, token)

    assert session.status_code == 200, session.text
    assert session.json()["phone_masked"] == PHONE_MY_MASKED
    assert "phone" not in session.json()
    assert PHONE_MY not in session.text
    assert "123456789" not in session.text
    assert_phone(phone, PHONE_MY)


def test_wrong_method_is_405_without_phone(client: TestClient, engine: Engine) -> None:
    """第 8 条「只返回」“该接口用 GET”。验收：路径存在而方法不匹配时由框架返回的 405 不含请求
    内容与个人资料。
    """
    token = stored_session(engine, add_member(engine))

    response = client.post(PHONE_URL, headers=cookie_header(token), params={"phone": PHONE_SG})

    assert response.status_code == 405
    assert "123456789" not in response.text
    assert "81234567" not in response.text


def test_no_logs_with_phone(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「no-store」“号码不写入日志、监控、分析事件或错误回显”。验收：本模块不写日志——
    200 与 401 都不产生 app 下的日志记录，任何日志都不含号码与令牌。
    """
    caplog.set_level(logging.DEBUG)
    token = stored_session(engine, add_member(engine))

    get_phone(client, token)
    client.get(PHONE_URL, headers=cookie_header("A" * 43))
    client.get(PHONE_URL)

    assert [r for r in caplog.records if r.name == "app" or r.name.startswith("app.")] == []
    for record in caplog.records:
        message = record.getMessage()
        for value in ("123456789", token):
            assert value not in message

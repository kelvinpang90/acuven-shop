"""站点设置表、短信验证开关的读取函数与 GET /api/site-settings。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条与「数据模型」的 SiteSetting 一行。
每条测试的文档字符串写明它守住的设计原句；没有直接原句的写明是 SHOP-TASK-022 验收标准里的约定。

用 SQLite 内存库按模型建表；接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import SiteSetting
from app.services.site_settings import is_sms_verification_enabled

URL = "/api/site-settings"
NOW = datetime(2026, 10, 1, 2, 0)


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app(Settings(_env_file=None))
    # 接口用测试自己的会话：flush 过的数据就看得到，不必提交。
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _setting(db: Session, *, enabled: bool) -> SiteSetting:
    row = SiteSetting(id=1, sms_verification_enabled=enabled, updated_at=NOW)
    db.add(row)
    db.flush()
    return row


def _set_enabled(db: Session, enabled: bool) -> None:
    db.execute(update(SiteSetting).values(sms_verification_enabled=enabled))
    db.flush()


def _record_statements(db: Session) -> list[str]:
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    return statements


# ---- 表与约束 ----


def test_primary_key_1_is_accepted(db: Session) -> None:
    """「首版只有短信验证开关」：主键为 1 的那一行可以写入，是下一条反例的对照。"""
    _setting(db, enabled=False)

    assert db.scalars(select(SiteSetting.id)).all() == [1]


@pytest.mark.parametrize("other_id", [0, 2, -1])
def test_primary_key_other_than_1_is_rejected(db: Session, other_id: int) -> None:
    """「站点级运营开关」全站只有一组：主键不是 1 的行被检查约束拒绝
    （SHOP-TASK-022 验收「表只有一行，主键固定为 1（检查约束保证）」）。"""
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        db.execute(
            insert(SiteSetting).values(id=other_id, sms_verification_enabled=False, updated_at=NOW)
        )


def test_switch_defaults_to_off(db: Session) -> None:
    """「首版只有短信验证开关（布尔，默认关闭）」：新建行不给开关值时，库里的服务端默认值是关闭。"""
    db.execute(insert(SiteSetting).values(id=1, updated_at=NOW))

    assert db.scalar(select(SiteSetting.sms_verification_enabled)) is False


@pytest.mark.parametrize("column", ["sms_verification_enabled", "updated_at"])
def test_columns_are_not_null(db: Session, column: str) -> None:
    """SHOP-TASK-022 验收「短信验证开关（布尔，非空…）与更新时间（非空…）」：两列写入 NULL 被拒，
    开关没有「未知」的第三种状态。"""
    values = {"id": 1, "sms_verification_enabled": False, "updated_at": NOW, column: None}

    with pytest.raises(IntegrityError, match="NOT NULL constraint failed"):
        db.execute(insert(SiteSetting).values(**values))


def test_table_has_only_the_switch() -> None:
    """「首版只有短信验证开关」：表里除主键与更新时间外只有这一个设置项。"""
    assert set(SiteSetting.__table__.columns.keys()) == {
        "id",
        "sms_verification_enabled",
        "updated_at",
    }


# ---- 读取函数 ----


@pytest.mark.parametrize("enabled", [True, False])
def test_read_returns_the_stored_value(db: Session, enabled: bool) -> None:
    """「每次判定都从数据库读取当前值」：读取函数返回库里那一行的开关值。"""
    _setting(db, enabled=enabled)

    assert is_sms_verification_enabled(db) is enabled


def test_missing_row_reads_as_off(db: Session) -> None:
    """「默认关闭」「关闭时全站不发任何短信」：表里没有那一行时视为关闭，不因缺行而开启短信
    （SHOP-TASK-022 验收「表里没有这一行时视为关闭」）。"""
    assert db.scalar(select(SiteSetting.id)) is None

    assert is_sms_verification_enabled(db) is False


def test_each_read_queries_the_database_and_sees_changes(db: Session) -> None:
    """「每次判定都从数据库读取当前值、不缓存，修改对之后的请求立即生效」；「下单、发送短信、
    提交验证码与确认注销时，服务端都按当时读取的开关值判定」：两次读取之间改库，第二次读到新值，
    且每次调用都发出一条 SELECT。"""
    _setting(db, enabled=False)
    statements = _record_statements(db)

    assert is_sms_verification_enabled(db) is False
    _set_enabled(db, True)
    assert is_sms_verification_enabled(db) is True
    _set_enabled(db, False)
    assert is_sms_verification_enabled(db) is False
    db.execute(delete(SiteSetting))
    assert is_sms_verification_enabled(db) is False

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4


def test_read_ignores_a_stale_loaded_object(db: Session) -> None:
    """「每次判定都从数据库读取当前值、不缓存」：会话里已加载的对象仍是旧值时，
    读取函数照样给出库里的新值。"""
    row = _setting(db, enabled=False)
    # 绕过 ORM 改库，会话里的对象不会被同步。
    db.connection().exec_driver_sql("UPDATE site_settings SET sms_verification_enabled = 1")

    assert row.sms_verification_enabled is False
    assert is_sms_verification_enabled(db) is True


# ---- 公开接口 ----


def test_endpoint_follows_the_stored_switch(db: Session, client: TestClient) -> None:
    """「修改对之后的请求立即生效」：接口返回值随库中开关变化，没有那一行时为关闭。"""
    assert client.get(URL).json() == {"sms_verification_enabled": False}

    _setting(db, enabled=False)
    assert client.get(URL).json() == {"sms_verification_enabled": False}

    _set_enabled(db, True)
    assert client.get(URL).json() == {"sms_verification_enabled": True}

    _set_enabled(db, False)
    assert client.get(URL).json() == {"sms_verification_enabled": False}


def test_endpoint_returns_only_the_switch_with_no_store(db: Session, client: TestClient) -> None:
    """「每次判定都从数据库读取当前值、不缓存」：响应带 Cache-Control: no-store，
    浏览器与代理不留旧值；响应体只有 sms_verification_enabled 一个字段
    （SHOP-TASK-022 验收「不返回其他内容」）。"""
    _setting(db, enabled=True)

    response = client.get(URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"sms_verification_enabled": True}
    assert list(response.json()) == ["sms_verification_enabled"]


def test_endpoint_needs_no_login_sets_no_cookie_and_only_reads(
    db: Session, client: TestClient
) -> None:
    """SHOP-TASK-022 验收「不需要登录，不读写 cookie；只发 SELECT」；「不加修改开关的接口」：
    带不带 cookie 结果一样，不设 cookie，只发 SELECT，写方法得到 405。"""
    _setting(db, enabled=True)
    statements = _record_statements(db)

    anonymous = client.get(URL)
    client.cookies.set("session", "anything")
    with_cookie = client.get(URL)

    for response in (anonymous, with_cookie):
        assert response.status_code == 200
        assert response.json() == {"sms_verification_enabled": True}
        assert "set-cookie" not in response.headers
    for method in ("post", "put", "patch", "delete"):
        assert client.request(method.upper(), URL, json={}).status_code == 405

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_endpoint_without_a_database_answers_503() -> None:
    """「每次判定都从数据库读取当前值」：未配置数据库时不猜一个值，明确回答 503。"""
    client = TestClient(create_app(Settings(_env_file=None, database_url="")))

    assert client.get(URL).status_code == 503

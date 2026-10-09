"""站点设置表、短信验证开关的读取函数与 GET /api/site-settings（含 WhatsApp 联系链接）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条、「数据模型」的 SiteSetting 一行
与「上线依赖与设计闸门」（配置 WhatsApp 联系方式），
以及 docs/UX.md 待决问题 Q10（配置缺失时隐藏）。
每条测试的文档字符串写明它守住的设计或 UX 原句；没有直接原句的写明是 SHOP-TASK-022 或
SHOP-TASK-073 验收标准里的约定。

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
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
    }

    _setting(db, enabled=False)
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
    }

    _set_enabled(db, True)
    assert client.get(URL).json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": None,
    }

    _set_enabled(db, False)
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
    }


def test_endpoint_returns_only_the_switch_with_no_store(db: Session, client: TestClient) -> None:
    """「每次判定都从数据库读取当前值、不缓存」：响应带 Cache-Control: no-store，
    浏览器与代理不留旧值；响应体只有 sms_verification_enabled 与 whatsapp_contact_url 两个字段
    （SHOP-TASK-022 验收「不返回其他内容」，SHOP-TASK-073 加上联系链接，未配置时为 null）。"""
    _setting(db, enabled=True)

    response = client.get(URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"sms_verification_enabled": True, "whatsapp_contact_url": None}
    assert list(response.json()) == ["sms_verification_enabled", "whatsapp_contact_url"]


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
        assert response.json() == {"sms_verification_enabled": True, "whatsapp_contact_url": None}
        assert "set-cookie" not in response.headers
    for method in ("post", "put", "patch", "delete"):
        assert client.request(method.upper(), URL, json={}).status_code == 405

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_endpoint_without_a_database_answers_503() -> None:
    """「每次判定都从数据库读取当前值」：未配置数据库时不猜一个值，明确回答 503。"""
    client = TestClient(create_app(Settings(_env_file=None, database_url="")))

    assert client.get(URL).status_code == 503


# ---- WhatsApp 联系链接 ----

CONTACT_URL = "https://chat.example.com/contact?ref=shop"


def _client_with_contact(db: Session, configured: str) -> TestClient:
    app = create_app(Settings(_env_file=None, whatsapp_contact_url=configured))
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_configured_contact_url_is_returned_as_is(db: Session) -> None:
    """DESIGN 1.11「上线依赖与设计闸门」「配置 WhatsApp 联系方式」：配置了合格的链接时，
    接口原样返回它，前台据此显示联系入口；开关字段照常返回。"""
    _setting(db, enabled=True)

    response = _client_with_contact(db, CONTACT_URL).get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": CONTACT_URL,
    }


def test_contact_url_has_surrounding_whitespace_removed(db: Session) -> None:
    """DESIGN 1.11「配置 WhatsApp 联系方式」：私有配置里链接前后多出的空白、换行不算链接的一部分，
    去掉后返回（SHOP-TASK-073 验收「配置值去掉首尾空白后」）。"""
    response = _client_with_contact(db, f"  \t{CONTACT_URL}\n ").get(URL)

    assert response.json()["whatsapp_contact_url"] == CONTACT_URL


def test_contact_url_of_512_characters_is_returned(db: Session) -> None:
    """DESIGN 1.11「配置 WhatsApp 联系方式」：恰好 512 个字符的链接照常返回，是下一条超长反例的对照
    （SHOP-TASK-073 验收「长度不超过 512 个字符」）。"""
    url = "https://chat.example.com/" + "a" * (512 - len("https://chat.example.com/"))
    assert len(url) == 512

    response = _client_with_contact(db, url).get(URL)

    assert response.json()["whatsapp_contact_url"] == url


@pytest.mark.parametrize(
    "configured",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="only-whitespace"),
        pytest.param("http://chat.example.com/contact", id="http"),
        pytest.param("ftp://chat.example.com/contact", id="ftp"),
        pytest.param("javascript:alert(1)", id="javascript"),
        pytest.param("whatsapp://send?phone=0", id="app-scheme"),
        pytest.param("chat.example.com/contact", id="no-scheme"),
        pytest.param("//chat.example.com/contact", id="scheme-relative"),
        pytest.param("https://", id="no-host"),
        pytest.param("https:///contact", id="empty-host"),
        pytest.param("https://:443/contact", id="port-only"),
        pytest.param("https://user@/contact", id="userinfo-only"),
        pytest.param("https:chat.example.com", id="no-slashes"),
        pytest.param("https://[::1/contact", id="broken-ipv6"),
        pytest.param("https://chat.example.com/con tact", id="space"),
        pytest.param("https://chat.example.com/con\ttact", id="tab"),
        pytest.param("https://chat.example.com/con　tact", id="ideographic-space"),
        pytest.param("https://chat.example.com/con\x00tact", id="nul"),
        pytest.param("https://chat.example.com/con\x7ftact", id="del"),
        pytest.param("https://chat.example.com/\r\nSet-Cookie:x", id="crlf"),
        pytest.param(
            "https://chat.example.com/" + "a" * (513 - len("https://chat.example.com/")),
            id="513-characters",
        ),
    ],
)
def test_unusable_contact_url_is_null_without_error_or_log(
    db: Session, configured: str, caplog: pytest.LogCaptureFixture
) -> None:
    """UX Q10「配置缺失时隐藏 WhatsApp 按钮……不显示占位文字」：未配置（空串）或配置不合格
    （非 https 协议、缺主机、含空白或控制字符、超过 512 个字符）时一律返回 null，前台隐藏入口，
    接口不报错、不写日志（SHOP-TASK-073 验收）。"""
    _setting(db, enabled=False)

    with caplog.at_level("DEBUG"):
        response = _client_with_contact(db, configured).get(URL)

    assert response.status_code == 200
    assert response.json() == {"sms_verification_enabled": False, "whatsapp_contact_url": None}
    assert [r for r in caplog.records if r.name.startswith("app")] == []


def test_contact_url_comes_after_the_switch(db: Session) -> None:
    """UX Q10「配置缺失时隐藏」靠前台读这个字段：字段紧跟在 sms_verification_enabled 之后，
    已配置与未配置时字段顺序相同
    （SHOP-TASK-073 验收「在 sms_verification_enabled 之后加字段」）。"""
    _setting(db, enabled=True)

    for configured in (CONTACT_URL, ""):
        response = _client_with_contact(db, configured).get(URL)
        assert list(response.json()) == ["sms_verification_enabled", "whatsapp_contact_url"]


def test_configured_contact_url_keeps_the_endpoint_read_only(db: Session) -> None:
    """DESIGN 1.11「配置 WhatsApp 联系方式」只加一个读配置的字段，接口其余行为不变：
    SHOP-TASK-022 验收「不需要登录，不读写 cookie；只发 SELECT」与 Cache-Control: no-store。"""
    _setting(db, enabled=True)
    client = _client_with_contact(db, CONTACT_URL)
    statements = _record_statements(db)

    anonymous = client.get(URL)
    client.cookies.set("session", "anything")
    with_cookie = client.get(URL)

    for response in (anonymous, with_cookie):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["whatsapp_contact_url"] == CONTACT_URL
        assert "set-cookie" not in response.headers

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

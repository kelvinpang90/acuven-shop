"""站点设置表、短信验证开关的读取函数与 GET /api/site-settings（含 WhatsApp 联系链接）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条、「数据模型」的 SiteSetting 一行
与「上线依赖与设计闸门」（配置 WhatsApp 联系方式），
以及 docs/UX.md 待决问题 Q10（配置缺失时隐藏）；支付方式的读取函数依据 docs/DESIGN.md 1.12
（提交 acf576e）「Stripe 测试环境支付」第 2 条（SHOP-TASK-112）；人机挑战的公开 site key
依据 docs/UX.md V1 的托管人机挑战组件（SHOP-TASK-143）；每日恢复库存开关的读取函数依据
docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 6 条（SHOP-TASK-179）。
每条测试的文档字符串写明它守住的设计或 UX 原句；没有直接原句的写明是 SHOP-TASK-022、
SHOP-TASK-073 或 SHOP-TASK-143 验收标准里的约定。

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
from app.models.site import PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST
from app.services.site_settings import (
    get_payment_method,
    is_daily_stock_reset_enabled,
    is_sms_verification_enabled,
)

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
    # 原为三列。DESIGN 1.12（提交 acf576e）SiteSetting 一行加了「支付方式（模拟 /
    # Stripe 测试，默认模拟）」，SHOP-TASK-110 随加列改为四列；其约束见
    # tests/test_stripe_models.py。DESIGN 1.18（提交 5ba9796）SiteSetting 一行加了「每日
    # 恢复库存开关（1.18，布尔，默认开启）」，SHOP-TASK-177 随加列改为五列；其默认值见
    # tests/test_admin_catalog_schema.py。
    assert set(SiteSetting.__table__.columns.keys()) == {
        "id",
        "sms_verification_enabled",
        "payment_method",
        "daily_stock_reset_enabled",
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


# ---- 支付方式的读取 ----
# 依据 DESIGN 1.12（提交 acf576e）「Stripe 测试环境支付」第 2 条与
# 「数据模型」SiteSetting 一行；只出自 SHOP-TASK-112 验收标准的约定注明「验收：」。


def _set_payment_method_unchecked(db: Session, value: str) -> None:
    # SQLite 上暂时关掉检查约束，写入约束本来会拒绝的值（MySQL 默认排序规则下
    # IN 不区分大小写，大写的值能通过约束）。
    connection = db.connection()
    connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
    connection.exec_driver_sql("UPDATE site_settings SET payment_method = ?", (value,))
    connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")


def test_payment_method_missing_row_reads_as_simulated(db: Session) -> None:
    """第 2 条「支付方式取『模拟』（默认）」；验收：「没有那一行……时视为
    simulated」：表里没有那一行时为 simulated。"""
    assert db.scalar(select(SiteSetting.id)) is None

    assert get_payment_method(db) == PAYMENT_METHOD_SIMULATED


def test_payment_method_defaults_to_simulated(db: Session) -> None:
    """「数据模型」SiteSetting「支付方式（模拟 / Stripe 测试，默认模拟）」：
    行里不给支付方式时取服务端默认值，读取为 simulated。"""
    db.execute(insert(SiteSetting).values(id=1, updated_at=NOW))

    assert get_payment_method(db) == PAYMENT_METHOD_SIMULATED


def test_payment_method_reads_stripe_test(db: Session) -> None:
    """第 2 条「支付方式取『模拟』（默认）或『Stripe 测试』」：库里为
    stripe_test 时读到 stripe_test。"""
    db.execute(
        insert(SiteSetting).values(id=1, payment_method=PAYMENT_METHOD_STRIPE_TEST, updated_at=NOW)
    )

    assert get_payment_method(db) == PAYMENT_METHOD_STRIPE_TEST


@pytest.mark.parametrize("value", ["STRIPE_TEST", "Stripe_Test", "paypal", ""])
def test_payment_method_unknown_value_reads_as_simulated(db: Session, value: str) -> None:
    """第 2 条「支付方式取『模拟』（默认）或『Stripe 测试』」；验收：「值不认识
    时视为 simulated」：大小写不同或其他取值（MySQL 上大写能通过检查约束）
    不当作 Stripe 测试。"""
    _setting(db, enabled=False)
    _set_payment_method_unchecked(db, value)
    assert db.scalar(select(SiteSetting.payment_method)) == value

    assert get_payment_method(db) == PAYMENT_METHOD_SIMULATED


def test_payment_method_each_read_queries_the_database(db: Session) -> None:
    """第 2 条「与短信验证开关一样每次从数据库读取」；验收：「每次一条
    SELECT、不缓存」：两次读取之间改库，第二次读到新值，每次调用发一条
    SELECT。"""
    _setting(db, enabled=False)
    statements = _record_statements(db)

    assert get_payment_method(db) == PAYMENT_METHOD_SIMULATED
    db.execute(update(SiteSetting).values(payment_method=PAYMENT_METHOD_STRIPE_TEST))
    db.flush()
    assert get_payment_method(db) == PAYMENT_METHOD_STRIPE_TEST
    db.execute(delete(SiteSetting))
    assert get_payment_method(db) == PAYMENT_METHOD_SIMULATED

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 3


def test_payment_method_read_ignores_a_stale_loaded_object(db: Session) -> None:
    """「数据模型」SiteSetting「每次判定都从数据库读取当前值、不缓存」：会话里
    已加载的对象仍是旧值时，读取函数照样给出库里的新值。"""
    # 显式给出支付方式：只靠服务端默认值时，对象上的这一列在写入后会过期并在访问时重读。
    row = SiteSetting(
        id=1,
        sms_verification_enabled=False,
        payment_method=PAYMENT_METHOD_SIMULATED,
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    db.connection().exec_driver_sql("UPDATE site_settings SET payment_method = 'stripe_test'")

    assert row.payment_method == PAYMENT_METHOD_SIMULATED
    assert get_payment_method(db) == PAYMENT_METHOD_STRIPE_TEST


# ---- 每日恢复库存开关的读取 ----
# 依据 DESIGN 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 6 条与「数据模型」SiteSetting 一行
# （SHOP-TASK-179）。


def test_daily_stock_reset_missing_row_reads_as_on(db: Session) -> None:
    """第 6 条「表里还没有站点设置那一行时视为开启」：表里没有那一行时为真。"""
    assert db.scalar(select(SiteSetting.id)) is None

    assert is_daily_stock_reset_enabled(db) is True


def test_daily_stock_reset_reads_the_stored_value(db: Session) -> None:
    """「数据模型」SiteSetting「每日恢复库存开关（1.18，布尔，默认开启）」：行里不给开关值时取
    服务端默认值，读到开启；列为假时读到关闭。"""
    db.execute(insert(SiteSetting).values(id=1, updated_at=NOW))
    assert is_daily_stock_reset_enabled(db) is True

    db.execute(update(SiteSetting).values(daily_stock_reset_enabled=False))
    db.flush()

    assert is_daily_stock_reset_enabled(db) is False


def test_daily_stock_reset_each_read_is_one_select(db: Session) -> None:
    """第 6 条「定时任务每次运行时先从数据库读取开关的当前值（不缓存）」：两次读取之间改库，第二次
    读到新值，每次调用只发一条 SELECT，且只查这一列。"""
    _setting(db, enabled=False)
    statements = _record_statements(db)

    assert is_daily_stock_reset_enabled(db) is True
    db.execute(update(SiteSetting).values(daily_stock_reset_enabled=False))
    db.flush()
    assert is_daily_stock_reset_enabled(db) is False
    db.execute(delete(SiteSetting))
    assert is_daily_stock_reset_enabled(db) is True

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 3
    for statement in selects:
        assert "daily_stock_reset_enabled" in statement
        assert "sms_verification_enabled" not in statement
        assert "payment_method" not in statement


def test_daily_stock_reset_read_ignores_a_stale_loaded_object(db: Session) -> None:
    """「数据模型」SiteSetting「每次判定都从数据库读取当前值、不缓存」：会话里已加载的对象仍是
    旧值时，读取函数照样给出库里的新值（不加载 ORM 对象）。"""
    row = SiteSetting(
        id=1,
        sms_verification_enabled=False,
        daily_stock_reset_enabled=True,
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    db.connection().exec_driver_sql("UPDATE site_settings SET daily_stock_reset_enabled = 0")

    assert row.daily_stock_reset_enabled is True
    assert is_daily_stock_reset_enabled(db) is False


# ---- 公开接口 ----


def test_endpoint_follows_the_stored_switch(db: Session, client: TestClient) -> None:
    """「修改对之后的请求立即生效」：接口返回值随库中开关变化，没有那一行时为关闭。"""
    # SHOP-TASK-143 给响应加了 turnstile_site_key；这里未配置，期望补上 null。
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }

    _setting(db, enabled=False)
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }

    _set_enabled(db, True)
    assert client.get(URL).json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }

    _set_enabled(db, False)
    assert client.get(URL).json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }


def test_endpoint_returns_only_the_switch_with_no_store(db: Session, client: TestClient) -> None:
    """「每次判定都从数据库读取当前值、不缓存」：响应带 Cache-Control: no-store，
    浏览器与代理不留旧值；响应体只有 sms_verification_enabled、whatsapp_contact_url 与
    turnstile_site_key 三个字段（SHOP-TASK-022 验收「不返回其他内容」，SHOP-TASK-073 加上联系链接，
    SHOP-TASK-143 加上人机挑战的公开 site key，未配置时都为 null）。"""
    _setting(db, enabled=True)

    response = client.get(URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    # SHOP-TASK-143 给响应加了 turnstile_site_key；这里未配置，期望补上 null，字段列表补上它。
    assert response.json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }
    assert list(response.json()) == [
        "sms_verification_enabled",
        "whatsapp_contact_url",
        "turnstile_site_key",
    ]


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
        # SHOP-TASK-143 给响应加了 turnstile_site_key；这里未配置，期望补上 null。
        assert response.json() == {
            "sms_verification_enabled": True,
            "whatsapp_contact_url": None,
            "turnstile_site_key": None,
        }
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
    # SHOP-TASK-143 给响应加了 turnstile_site_key；这里未配置，期望补上 null。
    assert response.json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": CONTACT_URL,
        "turnstile_site_key": None,
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
    # SHOP-TASK-143 给响应加了 turnstile_site_key；这里未配置，期望补上 null。
    assert response.json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }
    assert [r for r in caplog.records if r.name.startswith("app")] == []


def test_contact_url_comes_after_the_switch(db: Session) -> None:
    """UX Q10「配置缺失时隐藏」靠前台读这个字段：字段紧跟在 sms_verification_enabled 之后，
    已配置与未配置时字段顺序相同
    （SHOP-TASK-073 验收「在 sms_verification_enabled 之后加字段」）。"""
    _setting(db, enabled=True)

    for configured in (CONTACT_URL, ""):
        response = _client_with_contact(db, configured).get(URL)
        # SHOP-TASK-143 在联系链接之后加了 turnstile_site_key，字段列表补上它。
        assert list(response.json()) == [
            "sms_verification_enabled",
            "whatsapp_contact_url",
            "turnstile_site_key",
        ]


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


# ---- 人机挑战的公开 site key ----

SITE_KEY = "0x4AAAAAAAexample-site_key"
TURNSTILE_SECRET = "0x4AAAAAAAexample-secret-do-not-leak"
TURNSTILE_VERIFY_URL = "https://turnstile.example.com/siteverify"


def _client_with_turnstile(db: Session, **overrides: str) -> TestClient:
    app = create_app(Settings(_env_file=None, **overrides))
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def test_configured_site_key_is_returned_as_is(db: Session) -> None:
    """docs/UX.md V1 的托管人机挑战组件要用公开 site key：配置了合格的值时接口原样返回它，
    其余两个字段照常返回（SHOP-TASK-143 验收「原样返回」）。"""
    _setting(db, enabled=True)

    response = _client_with_turnstile(db, turnstile_site_key=SITE_KEY).get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "sms_verification_enabled": True,
        "whatsapp_contact_url": None,
        "turnstile_site_key": SITE_KEY,
    }


def test_site_key_has_surrounding_whitespace_removed(db: Session) -> None:
    """SHOP-TASK-143 验收「配置值去掉首尾空白后」：私有配置里前后多出的空白、换行不算 key 的一部分，
    去掉后返回。"""
    response = _client_with_turnstile(db, turnstile_site_key=f"  \t{SITE_KEY}\n ").get(URL)

    assert response.json()["turnstile_site_key"] == SITE_KEY


def test_site_key_of_256_characters_is_returned(db: Session) -> None:
    """SHOP-TASK-143 验收「不超过 256 个字符」：恰好 256 个字符的 key 照常返回，是下一条超长反例的
    对照。"""
    key = "k" * 256

    response = _client_with_turnstile(db, turnstile_site_key=key).get(URL)

    assert response.json()["turnstile_site_key"] == key


@pytest.mark.parametrize(
    "configured",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="only-whitespace"),
        pytest.param("k" * 257, id="257-characters"),
        pytest.param("0x4AAA example", id="space"),
        pytest.param("0x4AAA\texample", id="tab"),
        pytest.param("0x4AAA　example", id="ideographic-space"),
        pytest.param("0x4AAA\nexample", id="inner-newline"),
        pytest.param("0x4AAA\x00example", id="nul"),
        pytest.param("0x4AAA\x7fexample", id="del"),
        pytest.param("0x4AAA\x1bexample", id="escape"),
    ],
)
def test_unusable_site_key_is_null_without_error_or_log(
    db: Session, configured: str, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-143 验收「否则（含未配置）为 null；配置不合格时不报错、不写日志」：
    空串、超过 256 个字符、含空白或控制字符时返回 null，接口照常回答 200。"""
    _setting(db, enabled=False)

    with caplog.at_level("DEBUG"):
        response = _client_with_turnstile(db, turnstile_site_key=configured).get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "sms_verification_enabled": False,
        "whatsapp_contact_url": None,
        "turnstile_site_key": None,
    }
    assert [r for r in caplog.records if r.name.startswith("app")] == []


def test_response_never_carries_turnstile_secret_or_verify_url(db: Session) -> None:
    """SHOP-TASK-143 验收「不读、不返回 secret key 与核验地址」「响应只有三个字段」：secret key 与
    核验地址都已配置时，响应仍只有三个字段，两者的配置值都不出现在响应体里；只配了它们、没配
    site key 时 site key 为 null，不拿 secret key 顶替。"""
    _setting(db, enabled=True)

    for site_key, expected in ((SITE_KEY, SITE_KEY), ("", None)):
        client = _client_with_turnstile(
            db,
            turnstile_site_key=site_key,
            turnstile_secret_key=TURNSTILE_SECRET,
            turnstile_verify_url=TURNSTILE_VERIFY_URL,
        )
        response = client.get(URL)

        assert response.status_code == 200
        assert list(response.json()) == [
            "sms_verification_enabled",
            "whatsapp_contact_url",
            "turnstile_site_key",
        ]
        assert response.json()["turnstile_site_key"] == expected
        assert TURNSTILE_SECRET not in response.text
        assert TURNSTILE_VERIFY_URL not in response.text
        assert "turnstile.example.com" not in response.text


def test_configured_site_key_keeps_the_endpoint_read_only(db: Session) -> None:
    """SHOP-TASK-143 验收「其余字段、no-store 与只发 SELECT 不变」：配置了 site key 时，接口仍不需
    登录、不设 cookie、带 Cache-Control: no-store、只发 SELECT。"""
    _setting(db, enabled=True)
    client = _client_with_turnstile(db, turnstile_site_key=SITE_KEY)
    statements = _record_statements(db)

    anonymous = client.get(URL)
    client.cookies.set("session", "anything")
    with_cookie = client.get(URL)

    for response in (anonymous, with_cookie):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["sms_verification_enabled"] is True
        assert response.json()["turnstile_site_key"] == SITE_KEY
        assert "set-cookie" not in response.headers

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

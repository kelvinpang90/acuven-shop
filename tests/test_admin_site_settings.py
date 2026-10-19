"""后台站点设置的支付方式接口：GET /api/admin/site-settings、PUT /api/admin/site-settings
（app/api/admin_site_settings.py 与 app/services/site_settings.py 的 save_payment_method，
SHOP-TASK-122）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 1 条（下称「第 1 条」：「服务端
只接受 sk_test_ 或 rk_test_ 开头的密钥，其他前缀（含 sk_live_、rk_live_）一律不启用 Stripe
支付」）、第 2 条（下称「第 2 条」：「SiteSetting 的支付方式取『模拟』（默认）或『Stripe 测试』……
修改写审计」「凭据未配置时不能切到 Stripe 测试」）、「数据模型」的 SiteSetting 一行（下称「数据
模型」：「后台修改写入 AuditEvent（操作者、时间、新旧值）」「修改对之后的请求立即生效」）；
docs/UX.md 0.11 的 A09（下称「A09」）；Kelvin 2026-10-07 与 2026-10-15 的决定（A09 先只放支付方式
开关，短信验证开关暂不提供入口）与 docs/HANDOFF.md 0.54 的落实写法（下称「HANDOFF 0.54」）。每条
测试（参数化的测试是每个用例）的文档字符串写明它守住的原句；没有直接原句的，写明是 SHOP-TASK-122
验收里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（StaticPool）。管理员、后台会话与站点设置行都直接写库并提交；站点设置
按迁移 0010、0020 的默认值写入（另有测试覆盖设置行不存在）。接口的会话依赖换成每个请求一个绑定同一
内存库的会话，关闭时回滚未提交的改动；检查结果一律在另一个数据库会话里读，读得到即说明接口已提交。
Stripe 配置照 tests/test_stripe_gateway.py 的 make_settings。期望的 CSRF 令牌在这里另算。SQLite
不支持 FOR UPDATE，锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Select, create_engine, event, select, update
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.admin_site_settings import PaymentMethodName
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AuditEvent, SiteSetting
from app.models.site import (
    PAYMENT_METHOD_SIMULATED,
    PAYMENT_METHOD_STRIPE_TEST,
    PAYMENT_METHODS,
    SITE_SETTING_ID,
)
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.site_settings import get_payment_method, lock_site_setting_statement

URL = "/api/admin/site-settings"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

CHANGED = "admin_payment_method_changed"
SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_CONFIGURED = {"detail": "stripe_not_configured"}
SECRET = "SECRETVALUE"
SEEDED_AT = datetime(2026, 10, 1, 2, 0)

# 与 tests/test_stripe_gateway.py 相同的已配置取值。
SECRET_KEY = "sk_test_" + "51Hx" * 6
WEBHOOK_SECRET = "whsec_" + "c0ffee" * 5
DEMO_EMAIL = "demo-buyer@example.test"
BASE_URL = "https://stripe-api.example.test"
SITE_URL = "https://shop.example.test"

SIMULATED = {"payment_method": PAYMENT_METHOD_SIMULATED}
STRIPE_TEST = {"payment_method": PAYMENT_METHOD_STRIPE_TEST}

# 凭据不可用的几种配置（stripe_configured 为假）。
UNAVAILABLE = [
    pytest.param(
        {
            "stripe_api_base_url": "",
            "stripe_secret_key": "",
            "stripe_webhook_secret": "",
            "stripe_demo_email": "",
            "public_site_url": "",
        },
        id="not-configured",
    ),
    pytest.param({"stripe_secret_key": "sk_live_" + "51Hx" * 6}, id="live-secret-key"),
    pytest.param({"stripe_secret_key": "rk_live_" + "51Hx" * 6}, id="live-restricted-key"),
    pytest.param({"stripe_webhook_secret": "wh_" + "c0ffee" * 5}, id="webhook-secret-prefix"),
    pytest.param({"stripe_webhook_secret": ""}, id="webhook-secret-missing"),
]


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


def make_settings(**overrides: str) -> Settings:
    values = {
        "stripe_api_base_url": BASE_URL,
        "stripe_secret_key": SECRET_KEY,
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_demo_email": DEMO_EMAIL,
        "public_site_url": SITE_URL,
        **overrides,
    }
    return Settings(_env_file=None, redis_url="", **values)


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def account_id(db: Session) -> int:
    row = AdminAccount(
        username="admin@example.com",
        password_hash="not-a-real-hash",
        created_at=_now() - timedelta(days=1),
        password_updated_at=_now() - timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row.id


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, _now() - timedelta(minutes=1))
    db.commit()
    return issued.token


@pytest.fixture
def seeded(db: Session) -> None:
    """按迁移 0010、0020 写入的那一行：短信验证关闭、支付方式模拟。"""
    db.add(
        SiteSetting(
            id=SITE_SETTING_ID,
            sms_verification_enabled=False,
            payment_method=PAYMENT_METHOD_SIMULATED,
            updated_at=SEEDED_AT,
        )
    )
    db.commit()


def _app(engine: Engine, settings: Settings) -> FastAPI:
    app = create_app(settings)

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


def _client(engine: Engine, settings: Settings) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(_app(engine, settings), base_url="https://testserver")


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    """Stripe 测试凭据已配置的应用。"""
    return _app(engine, make_settings())


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _get(client: TestClient, token: str | None) -> Any:
    return client.get(URL, headers=_headers(token))


def _put(client: TestClient, token: str | None, body: Any, *, csrf: str | None = "auto") -> Any:
    """csrf 默认按 token 算出正确的令牌；给 None 即不带请求头。"""
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    return client.put(URL, json=body, headers=_headers(token, csrf))


def _put_raw(
    client: TestClient, token: str | None, content: bytes, content_type: str | None
) -> Any:
    headers = _headers(token, None if token is None else _csrf_for(token))
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.put(URL, content=content, headers=headers)


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """库里的站点设置各行（ID、短信验证开关、支付方式、更新时间）与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        rows = session.scalars(select(SiteSetting).order_by(SiteSetting.id)).all()
        audits = session.scalars(select(AuditEvent.id)).all()
        return (
            [
                (row.id, row.sms_verification_enabled, row.payment_method, row.updated_at)
                for row in rows
            ],
            len(audits),
        )

    return _read(engine, read)


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """支付方式修改的审计记录：管理员、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        rows = session.scalars(
            select(AuditEvent).where(AuditEvent.action == CHANGED).order_by(AuditEvent.id)
        )
        return [
            (row.admin_account_id, row.target_type, row.target_id, row.old_value, row.new_value)
            for row in rows
        ]

    return _read(engine, read)


def _set_method(db: Session, value: str) -> None:
    db.execute(update(SiteSetting).values(payment_method=value))
    db.commit()


def _set_method_unchecked(db: Session, value: str) -> None:
    # SQLite 上暂时关掉检查约束，写入约束本来会拒绝的值（MySQL 默认排序规则下 IN 不区分大小写，
    # 大写的值能通过约束）。写法同 tests/test_site_settings.py。
    connection = db.connection()
    connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
    connection.exec_driver_sql("UPDATE site_settings SET payment_method = ?", (value,))
    connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
    db.commit()


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _assert_plain_errors(response: Any) -> None:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert SECRET not in response.text


# ---------------------------------------------------------------------------
# 会话、CSRF 与请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_state", ["missing", "malformed", "unknown", "revoked"])
def test_both_endpoints_require_an_admin_session(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    seeded: None,
    session_state: str,
) -> None:
    """DESIGN 1.12「权限与资料保护」：A09 是后台页；验收「两个接口都经 require_admin（401
    admin_session_required）」：没有 cookie、格式不对、库里没有与已撤销的会话都 401，数据不变。"""
    token: str | None
    if session_state == "missing":
        token = None
    elif session_state == "malformed":
        token = "not-a-token"
    elif session_state == "unknown":
        token = "A" * 43
    else:
        issued = issue_admin_session(db, account_id, _now() - timedelta(minutes=5))
        revoke_admin_session(db, issued.session, _now() - timedelta(minutes=1))
        db.commit()
        token = issued.token
    before = _snapshot(engine)

    get = _get(client, token)
    put = _put(client, token, STRIPE_TEST, csrf="0" * 64)

    _assert_error(get, 401, SESSION_REQUIRED)
    _assert_error(put, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_save_without_a_valid_csrf_token_is_403(
    client: TestClient, engine: Engine, token: str, seeded: None, csrf: str | None
) -> None:
    """验收「检查顺序 413 → 415 → 401 → 403 csrf_failed → 422」：X-CSRF-Token 缺少、为空或不一致
    都 403 csrf_failed；不合格的请求体也先回答 403；数据与审计不变。"""
    before = _snapshot(engine)

    responses = [
        _put(client, token, STRIPE_TEST, csrf=csrf),
        _put(client, token, {"sms_verification_enabled": SECRET}, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("logged_in", [True, False])
def test_non_json_is_415(
    client: TestClient,
    engine: Engine,
    token: str,
    seeded: None,
    content_type: str | None,
    logged_in: bool,
) -> None:
    """验收「检查顺序 413 → 415 → 401」：不是 JSON 时有没有会话都 415，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps(STRIPE_TEST).encode()

    response = _put_raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_oversized_body_is_413_first(
    client: TestClient,
    engine: Engine,
    token: str,
    seeded: None,
    content_type: str,
    logged_in: bool,
) -> None:
    """验收「413（8 KB）」且排在最前：不是 JSON、没有会话时也是 413；不回显请求内容，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({"payment_method": SECRET * 1000}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _put_raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 413, {"detail": "request body too large"})
    assert SECRET not in response.text
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """验收「413（8 KB）」：恰好 8 KB（用 JSON 空白补足）的合法请求体照常保存。"""
    content = json.dumps(STRIPE_TEST).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    assert _ok(_put_raw(client, token, content, "application/json")) == STRIPE_TEST
    assert len(_audits(engine)) == 1


def test_invalid_body_without_a_session_is_401(client: TestClient, seeded: None) -> None:
    """验收「检查顺序 413 → 415 → 401 → 403 → 422」：不合格的 JSON 请求体在没有会话时先回答
    401。"""
    _assert_error(_put(client, None, {"payment_method": SECRET}), 401, SESSION_REQUIRED)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**SIMULATED, "sms_verification_enabled": True}, id="with-sms-toggle"),
        pytest.param({**SIMULATED, "sms_verification_enabled": SECRET}, id="sms-toggle-secret"),
        pytest.param({"sms_verification_enabled": True}, id="only-sms-toggle"),
        pytest.param({"payment_method": SECRET}, id="unknown-value"),
        pytest.param({"payment_method": "paypal"}, id="other-method"),
        pytest.param({"payment_method": "STRIPE_TEST"}, id="uppercase-stripe"),
        pytest.param({"payment_method": "Simulated"}, id="capitalised-simulated"),
        pytest.param({"payment_method": " stripe_test"}, id="leading-space"),
        pytest.param({"payment_method": ""}, id="empty"),
        pytest.param({"payment_method": None}, id="null"),
        pytest.param({"payment_method": 1}, id="number"),
        pytest.param({"payment_method": True}, id="bool"),
        pytest.param({"payment_method": [PAYMENT_METHOD_STRIPE_TEST]}, id="list"),
        pytest.param({**STRIPE_TEST, "extra": SECRET}, id="extra-field"),
        pytest.param({}, id="missing"),
        pytest.param([], id="not-an-object"),
        pytest.param(PAYMENT_METHOD_STRIPE_TEST, id="bare-string"),
    ],
)
def test_malformed_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, seeded: None, body: Any
) -> None:
    """第 2 条「支付方式取『模拟』（默认）或『Stripe 测试』」；Kelvin 2026-10-07 与 2026-10-15「A09
    先只放支付方式开关」（不提供短信验证开关的写入）；验收「请求体只有 payment_method，取两个常量
    之一，extra forbid、strict，含短信字段或未知值一律 422，错误沿用 _body_errors 不回显」：大小写
    不同、多出字段与类型不对都 422，不回显所给的值，数据与审计不变。"""
    before = _snapshot(engine)

    response = _put(client, token, body)

    _assert_plain_errors(response)
    assert "STRIPE_TEST" not in response.text
    assert "Simulated" not in response.text
    assert _snapshot(engine) == before


def test_body_literal_matches_the_model_constants() -> None:
    """验收「取两个常量之一」：接口接受的值与 app/models/site.py 的 PAYMENT_METHODS 一致。"""
    assert set(get_args(PaymentMethodName)) == set(PAYMENT_METHODS)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_get_returns_method_availability_and_csrf_token(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """A09「服务端告知 Stripe 测试凭据……未配置」；验收 GET 的字段：payment_method、stripe_available
    与 csrf_token，不返回短信验证开关；只读，不写审计。凭据已配置时 stripe_available 为真。"""
    before = _snapshot(engine)

    body = _ok(_get(client, token))

    assert body == {
        "payment_method": PAYMENT_METHOD_SIMULATED,
        "stripe_available": True,
        "csrf_token": _csrf_for(token),
    }
    assert list(body) == ["payment_method", "stripe_available", "csrf_token"]
    assert _snapshot(engine) == before


def test_get_reads_the_current_value(
    db: Session, client: TestClient, token: str, seeded: None
) -> None:
    """数据模型「每次判定都从数据库读取当前值、不缓存」；验收「payment_method 照
    get_payment_method」：库里改为 stripe_test 后 GET 随即读到；不认识的值（MySQL 上大写能通过
    检查约束）读作 simulated。"""
    first = _ok(_get(client, token))["payment_method"]
    _set_method(db, PAYMENT_METHOD_STRIPE_TEST)
    second = _ok(_get(client, token))["payment_method"]
    _set_method_unchecked(db, "STRIPE_TEST")
    third = _ok(_get(client, token))["payment_method"]

    assert (first, second, third) == (
        PAYMENT_METHOD_SIMULATED,
        PAYMENT_METHOD_STRIPE_TEST,
        PAYMENT_METHOD_SIMULATED,
    )


def test_get_without_a_setting_row_is_simulated(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 2 条「支付方式取『模拟』（默认）」：设置行不存在时为 simulated，不补写那一行。"""
    body = _ok(_get(client, token))

    assert body["payment_method"] == PAYMENT_METHOD_SIMULATED
    assert _snapshot(engine) == ([], 0)


# ---------------------------------------------------------------------------
# 凭据不可用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", UNAVAILABLE)
def test_unavailable_credentials_block_switching_to_stripe(
    engine: Engine, token: str, seeded: None, overrides: dict[str, str]
) -> None:
    """第 1 条「服务端只接受 sk_test_ 或 rk_test_ 开头的密钥，其他前缀（含 sk_live_、rk_live_）一律
    不启用 Stripe 支付」；第 2 条「凭据未配置时不能切到 Stripe 测试」；A09「凭据……未配置或密钥不是
    测试密钥时，[admin.payment_stripe] 禁用」「保存时服务端再次检查，被拒则……本次保存不生效」；
    HANDOFF 0.54「以 stripe_configured 的五项检查判定」：未配置、密钥为正式环境前缀、签名密钥前缀
    不对或缺少时 GET 的 stripe_available 为假，PUT stripe_test 422 stripe_not_configured，数据
    与审计不变，get_payment_method 仍为 simulated。"""
    client = _client(engine, make_settings(**overrides))
    before = _snapshot(engine)

    get = _ok(_get(client, token))
    put = _put(client, token, STRIPE_TEST)

    assert get["stripe_available"] is False
    assert get["payment_method"] == PAYMENT_METHOD_SIMULATED
    _assert_error(put, 422, NOT_CONFIGURED)
    assert _snapshot(engine) == before
    assert _read(engine, get_payment_method) == PAYMENT_METHOD_SIMULATED


@pytest.mark.parametrize("overrides", UNAVAILABLE)
def test_saving_stripe_again_with_unavailable_credentials_is_rejected(
    db: Session, engine: Engine, token: str, seeded: None, overrides: dict[str, str]
) -> None:
    """A09「当前已是『Stripe 测试』而凭据后来失效时，两项照常显示当前值」「保存时服务端再次检查」；
    HANDOFF 0.54「凭据不可用时保存『Stripe 测试』一律被拒（当前已是该值也一样）」：GET 照常返回
    stripe_test 与 stripe_available 为假；再保存 stripe_test 为 422 stripe_not_configured，数据与
    审计不变。"""
    _set_method(db, PAYMENT_METHOD_STRIPE_TEST)
    client = _client(engine, make_settings(**overrides))
    before = _snapshot(engine)

    get = _ok(_get(client, token))
    put = _put(client, token, STRIPE_TEST)

    assert (get["payment_method"], get["stripe_available"]) == (PAYMENT_METHOD_STRIPE_TEST, False)
    _assert_error(put, 422, NOT_CONFIGURED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("overrides", UNAVAILABLE)
def test_switching_back_to_simulated_ignores_the_credentials(
    db: Session,
    engine: Engine,
    account_id: int,
    token: str,
    seeded: None,
    overrides: dict[str, str],
) -> None:
    """A09「当前已是『Stripe 测试』而凭据后来失效时……仍可切回模拟」；验收「切回 simulated 不看
    配置」：凭据不可用时从 stripe_test 切回 simulated 成功，写一条审计
    （stripe_test → simulated）。"""
    _set_method(db, PAYMENT_METHOD_STRIPE_TEST)
    client = _client(engine, make_settings(**overrides))

    assert _ok(_put(client, token, SIMULATED)) == SIMULATED
    assert _read(engine, get_payment_method) == PAYMENT_METHOD_SIMULATED
    assert _audits(engine) == [
        (
            account_id,
            "site_setting",
            SITE_SETTING_ID,
            PAYMENT_METHOD_STRIPE_TEST,
            PAYMENT_METHOD_SIMULATED,
        )
    ]


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def test_switching_writes_the_value_and_an_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, seeded: None
) -> None:
    """第 2 条「修改写审计」；数据模型「后台修改写入 AuditEvent（操作者、时间、新旧值）」
    「修改对之后的请求立即生效」：凭据已配置时切到 stripe_test 成功，另一个会话的
    get_payment_method 随即读到新值、GET 也读到；更新时间改变，短信验证开关不变；审计的
    操作者、对象与新旧值正确。再切回 simulated 写第二条审计。"""
    to_stripe = _ok(_put(client, token, STRIPE_TEST))

    assert to_stripe == STRIPE_TEST
    assert _read(engine, get_payment_method) == PAYMENT_METHOD_STRIPE_TEST
    assert _ok(_get(client, token))["payment_method"] == PAYMENT_METHOD_STRIPE_TEST
    rows, _ = _snapshot(engine)
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (SITE_SETTING_ID, False, PAYMENT_METHOD_STRIPE_TEST)
    ]
    assert rows[0][3] > SEEDED_AT
    assert _audits(engine) == [
        (
            account_id,
            "site_setting",
            SITE_SETTING_ID,
            PAYMENT_METHOD_SIMULATED,
            PAYMENT_METHOD_STRIPE_TEST,
        )
    ]

    to_simulated = _ok(_put(client, token, SIMULATED))

    assert to_simulated == SIMULATED
    assert _read(engine, get_payment_method) == PAYMENT_METHOD_SIMULATED
    assert _audits(engine)[1] == (
        account_id,
        "site_setting",
        SITE_SETTING_ID,
        PAYMENT_METHOD_STRIPE_TEST,
        PAYMENT_METHOD_SIMULATED,
    )


def test_saving_does_not_touch_the_sms_toggle(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """Kelvin 2026-10-07 与 2026-10-15「A09 先只放支付方式开关」（短信验证开关暂不提供入口）：
    保存支付方式不改短信验证开关的当前值。"""
    db.execute(update(SiteSetting).values(sms_verification_enabled=True))
    db.commit()

    _ok(_put(client, token, STRIPE_TEST))

    rows, _ = _snapshot(engine)
    assert rows[0][1] is True


@pytest.mark.parametrize("method", list(PAYMENT_METHODS))
def test_saving_the_current_value_writes_nothing(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None, method: str
) -> None:
    """数据模型「后台修改写入 AuditEvent」（没有修改即不写）；验收「与当前值相同时回 200、不写入、
    不写审计」：两种取值各保存一次当前值，更新时间与审计记录数都不变。"""
    _set_method(db, method)
    before = _snapshot(engine)

    assert _ok(_put(client, token, {"payment_method": method})) == {"payment_method": method}
    assert _snapshot(engine) == before


def test_unknown_stored_value_is_read_as_simulated_when_saving(
    db: Session, client: TestClient, engine: Engine, account_id: int, token: str, seeded: None
) -> None:
    """验收「旧值与新值为 simulated 或 stripe_test」「payment_method 照 get_payment_method」：库里是
    不认识的值（MySQL 上大写的 STRIPE_TEST 能通过检查约束）时按 simulated 解读——保存 simulated
    视为相同、不写；保存 stripe_test 写入并记旧值 simulated。"""
    _set_method_unchecked(db, "STRIPE_TEST")
    before = _snapshot(engine)

    assert _ok(_put(client, token, SIMULATED)) == SIMULATED
    assert _snapshot(engine) == before

    assert _ok(_put(client, token, STRIPE_TEST)) == STRIPE_TEST
    assert _audits(engine) == [
        (
            account_id,
            "site_setting",
            SITE_SETTING_ID,
            PAYMENT_METHOD_SIMULATED,
            PAYMENT_METHOD_STRIPE_TEST,
        )
    ]


def test_save_without_a_setting_row_inserts_it(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """验收「锁住唯一的站点设置行（行不存在时照它的写法插入）」：表为空时保存 simulated 视为与默认
    值相同、不写审计；保存 stripe_test 写出那一行（短信验证关闭）与审计，旧值为默认的 simulated。
    （SQLite 的驱动在释放最外层保存点时就提交，相同时回滚能否撤掉插入的行在这里不检查。）"""
    assert _ok(_put(client, token, SIMULATED)) == SIMULATED
    assert _audits(engine) == []
    assert _read(engine, get_payment_method) == PAYMENT_METHOD_SIMULATED

    assert _ok(_put(client, token, STRIPE_TEST)) == STRIPE_TEST

    rows, _ = _snapshot(engine)
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (SITE_SETTING_ID, False, PAYMENT_METHOD_STRIPE_TEST)
    ]
    assert _audits(engine) == [
        (
            account_id,
            "site_setting",
            SITE_SETTING_ID,
            PAYMENT_METHOD_SIMULATED,
            PAYMENT_METHOD_STRIPE_TEST,
        )
    ]


def test_failed_saves_write_no_audit(
    engine: Engine, client: TestClient, token: str, seeded: None
) -> None:
    """数据模型「后台修改写入 AuditEvent」；验收「不写入」：凭据不可用、缺 CSRF、请求体不合格的
    保存都不写审计。"""
    unavailable = _client(engine, make_settings(stripe_secret_key=""))

    _put(unavailable, token, STRIPE_TEST)
    _put(client, token, STRIPE_TEST, csrf=None)
    _put(client, token, {"payment_method": "STRIPE_TEST"})
    _put(client, token, {**STRIPE_TEST, "sms_verification_enabled": False})

    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 锁定、缓存、日志与路由
# ---------------------------------------------------------------------------


def test_lock_statement_is_select_for_update_on_the_row() -> None:
    """验收「锁住唯一的站点设置行」「用 MySQL 方言编译锁定语句含 FOR UPDATE」。"""
    sql = str(lock_site_setting_statement().compile(dialect=mysql.dialect()))

    assert "FROM site_settings" in sql
    assert "WHERE site_settings.id = " in sql
    assert sql.rstrip().endswith("FOR UPDATE")


def test_save_locks_the_row_before_reading_it(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """验收「照 save_store_design 在一个事务里完成——锁住唯一的站点设置行」：保存时对 site_settings
    的第一条查询就是锁定查询，且只锁一次。"""
    seen: list[str] = []

    def _record(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        # 只编译 SELECT：SQLite 上 ORM 的 INSERT 可能带 RETURNING，MySQL 方言编译不了。
        if isinstance(clauseelement, Select):
            sql = str(clauseelement.compile(dialect=mysql.dialect()))
            if "site_settings" in sql:
                seen.append("lock" if "FOR UPDATE" in sql else "read")

    event.listen(engine, "before_execute", _record)
    try:
        _ok(_put(client, token, STRIPE_TEST))
    finally:
        event.remove(engine, "before_execute", _record)

    assert seen == ["lock"]


def test_all_responses_are_no_store_and_nothing_is_logged(
    engine: Engine,
    client: TestClient,
    token: str,
    seeded: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验收「这些接口的处理函数与依赖产生的响应（含错误）都带 no-store」「接口不写日志」：成功、
    相同值、401、403、413、415、422 与 stripe_not_configured 都带 no-store；凭据为测试密钥时应用
    不记日志（非测试密钥的 stripe_key_not_test 由 SHOP-TASK-111 记，不在此列）。"""
    caplog.set_level(logging.DEBUG)
    unavailable = _client(engine, make_settings(stripe_webhook_secret=""))
    oversized = json.dumps({"payment_method": "x" * MAX_BODY_BYTES}).encode()
    responses = [
        _get(client, token),
        _get(client, None),
        _get(unavailable, token),
        _put(client, token, STRIPE_TEST),
        _put(client, token, STRIPE_TEST),
        _put(client, None, STRIPE_TEST),
        _put(client, token, STRIPE_TEST, csrf=None),
        _put(client, token, {"payment_method": "STRIPE_TEST"}),
        _put(unavailable, token, STRIPE_TEST),
        _put_raw(client, token, b"{}", "text/plain"),
        _put_raw(client, token, oversized, "application/json"),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 401, 200, 200, 200, 401, 403, 422, 422, 415, 413]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_only_get_and_put(app: FastAPI) -> None:
    """验收「两个接口」：该路径下只有 GET 与 PUT。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("GET", URL), ("PUT", URL)}


def test_public_site_settings_still_has_no_payment_method(client: TestClient) -> None:
    """验收「公开的 GET /api/site-settings……不变」：公开接口仍不带支付方式或凭据状态。"""
    body = _ok(client.get("/api/site-settings"))

    assert "payment_method" not in body
    assert "stripe_available" not in body

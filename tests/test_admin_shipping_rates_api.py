"""后台运费与演示汇率的读写接口：GET /api/admin/shipping-rates、
PUT /api/admin/shipping-rates（app/api/admin_shipping_rates.py，SHOP-TASK-171；
规则见 SHOP-TASK-170 的 app/services/admin_shipping_rates.py）。

依据 docs/DESIGN.md 1.16（提交 e9a1c93）「计价、优惠、积分与库存」第 9 条
（下称「第 9 条」）、docs/UX.md 0.15 的 A06「保存与状态」（下称「A06」）
与 docs/HANDOFF.md 0.67 的落实写法（下称「HANDOFF 0.67」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句；
没有直接原句的，写明是 SHOP-TASK-171 验收里的约定（下称「验收」）。
马来西亚一组依据 DESIGN 1.17（提交 80e3775）同节第 10 条（下称「第 10 条」）与
docs/HANDOFF.md 0.69（下称「HANDOFF 0.69」）：字段由 states 改名为 malaysia，西马在前。

夹具照 tests/test_admin_site_settings.py：SQLite 内存库按模型建表（StaticPool），
管理员与后台会话直接写库并提交；运费与汇率写入与迁移 0005、0023 之后相同的演示数据
（同 tests/test_admin_shipping_rates.py）。接口的会话依赖换成每个请求一个绑定
同一内存库的会话，关闭时回滚未提交的改动；检查结果一律在另一个数据库会话里
只查列值读出，读得到即说明接口已提交。期望的 CSRF 令牌在这里另算。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AuditEvent, DemoFxRate, ShippingRate
from app.services.admin_auth import issue_admin_session, revoke_admin_session

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 演示汇率至多 6 位小数，读回与写入相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

URL = "/api/admin/shipping-rates"
MAX_BODY_BYTES = 64 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
RATES_INVALID = {"detail": "rates_invalid"}
RATES_CHANGED = {"detail": "rates_changed"}
SECRET = "SECRETVALUE"

SHIPPING_CHANGED = "admin_shipping_rate_changed"
SHIPPING_REMOVED = "admin_shipping_rate_removed"
SHIPPING_ADDED = "admin_shipping_rate_added"
FX_CHANGED = "admin_fx_rate_changed"
FX_REMOVED = "admin_fx_rate_removed"
FX_ADDED = "admin_fx_rate_added"
SHIPPING = "shipping_rate"
FX = "demo_fx_rate"

# 与迁移 0005、0023 之后相同的演示数据。马来西亚两档东马先写入：读取仍西马在前
# （第 10 条「两行按西马、东马的顺序排列」）。
MALAYSIA_FEES = {"MY-E": 1500, "MY-W": 800}
COUNTRY_FEES = {
    "SG": 2000,
    "BN": 2500,
    "TH": 3000,
    "CN": 3500,
    "JP": 4500,
    "AU": 5000,
    "US": 6000,
    "GB": 6000,
}
OTHER_FEE = 8000
FX_RATES = (
    ("SG", "SGD", 2, Decimal("0.310000")),
    ("TH", "THB", 2, Decimal("7.600000")),
    ("CN", "CNY", 2, Decimal("1.650000")),
    ("JP", "JPY", 0, Decimal("34.000000")),
    ("AU", "AUD", 2, Decimal("0.350000")),
    ("US", "USD", 2, Decimal("0.230000")),
    ("GB", "GBP", 2, Decimal("0.170000")),
    ("HK", "HKD", 2, Decimal("1.800000")),
)

PAGE_FIELDS = ["malaysia", "other", "countries", "fx"]
ZONE_FIELDS = ["id", "version", "zone_code", "fee_sen"]
FX_FIELDS = ["id", "version", "country_code", "currency_code", "currency_decimals", "rate"]


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        for code, fee in MALAYSIA_FEES.items():
            seed.add(ShippingRate(zone_type="my_region", zone_code=code, fee_sen=fee, version=1))
        for code, fee in COUNTRY_FEES.items():
            seed.add(ShippingRate(zone_type="country", zone_code=code, fee_sen=fee, version=1))
        seed.add(ShippingRate(zone_type="other", zone_code="OTHER", fee_sen=OTHER_FEE, version=1))
        for country, currency, decimals, rate in FX_RATES:
            seed.add(
                DemoFxRate(
                    country_code=country,
                    currency_code=currency,
                    currency_decimals=decimals,
                    rate=rate,
                    version=1,
                )
            )
        seed.commit()
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
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

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
    """两张表的各行（只查列值，按 ID）与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        zones_stmt = select(
            ShippingRate.id,
            ShippingRate.zone_code,
            ShippingRate.fee_sen,
            ShippingRate.version,
        ).order_by(ShippingRate.id)
        rates_stmt = select(
            DemoFxRate.id,
            DemoFxRate.country_code,
            DemoFxRate.currency_code,
            DemoFxRate.currency_decimals,
            DemoFxRate.rate,
            DemoFxRate.version,
        ).order_by(DemoFxRate.id)
        zones = [tuple(row) for row in session.execute(zones_stmt)]
        rates = [tuple(row) for row in session.execute(rates_stmt)]
        return zones, rates, len(session.scalars(select(AuditEvent.id)).all())

    return _read(engine, read)


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """全部审计记录：管理员、操作、对象类别、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = select(
            AuditEvent.admin_account_id,
            AuditEvent.action,
            AuditEvent.target_type,
            AuditEvent.old_value,
            AuditEvent.new_value,
        ).order_by(AuditEvent.id)
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


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


def _page(client: TestClient, token: str) -> dict[str, Any]:
    return _ok(_get(client, token))


def _body(page: dict[str, Any]) -> dict[str, Any]:
    """把读到的整页原样列成保存请求（每行保留、值不变）。"""

    def keep_fee(row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "version": row["version"], "fee_sen": row["fee_sen"]}

    return {
        "malaysia": [keep_fee(row) for row in page["malaysia"]],
        "other": keep_fee(page["other"]),
        "countries": [keep_fee(row) for row in page["countries"]],
        "fx": [
            {"id": row["id"], "version": row["version"], "rate": row["rate"]} for row in page["fx"]
        ],
    }


def _index(page: dict[str, Any], group: str, code: str) -> int:
    key = "country_code" if group == "fx" else "zone_code"
    return next(i for i, row in enumerate(page[group]) if row[key] == code)


def _row(page: dict[str, Any], group: str, code: str) -> dict[str, Any]:
    return page[group][_index(page, group, code)]


def _ref(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"id": row["id"], "version": row["version"], **extra}


def _saved_page(page: dict[str, Any]) -> dict[str, Any]:
    """GET 的整页去掉 csrf_token，即 PUT 成功时应回的数据。"""
    return {key: value for key, value in page.items() if key != "csrf_token"}


Mutation = Callable[[dict[str, Any], dict[str, Any]], Any]


def _mutated(page: dict[str, Any], mutate: Mutation) -> Any:
    """在整页原样的请求上改动；mutate 返回非 None 时以返回值为请求体。"""
    body = _body(page)
    replaced = mutate(page, body)
    return body if replaced is None else replaced


def _set(group: str, code: str, field: str, value: Any) -> Mutation:
    def mutate(page: dict[str, Any], body: dict[str, Any]) -> None:
        body[group][_index(page, group, code)][field] = value

    return mutate


def _set_row(group: str, code: str, row: Callable[[dict[str, Any]], dict[str, Any]]) -> Mutation:
    def mutate(page: dict[str, Any], body: dict[str, Any]) -> None:
        body[group][_index(page, group, code)] = row(_row(page, group, code))

    return mutate


def _append(group: str, row: dict[str, Any]) -> Mutation:
    def mutate(_page: dict[str, Any], body: dict[str, Any]) -> None:
        body[group].append(row)

    return mutate


def _set_other(field: str, value: Any) -> Mutation:
    def mutate(_page: dict[str, Any], body: dict[str, Any]) -> None:
        body["other"][field] = value

    return mutate


def _set_top(key: str, value: Any) -> Mutation:
    def mutate(_page: dict[str, Any], body: dict[str, Any]) -> None:
        body[key] = value

    return mutate


def _drop_top(key: str) -> Mutation:
    def mutate(_page: dict[str, Any], body: dict[str, Any]) -> None:
        del body[key]

    return mutate


def _add_country_twice(_page: dict[str, Any], body: dict[str, Any]) -> None:
    body["countries"].append({"country_code": "NZ", "fee_sen": 100})
    body["countries"].append({"country_code": "NZ", "fee_sen": 200})


def _list_a_row_twice(_page: dict[str, Any], body: dict[str, Any]) -> None:
    body["malaysia"][1] = dict(body["malaysia"][0])


def _leave_out_sg(page: dict[str, Any], body: dict[str, Any]) -> None:
    del body["countries"][_index(page, "countries", "SG")]


def _bump_version(model: Any, column: Any, code: str) -> Callable[[Session], None]:
    """另一处保存过：把某一行的版本号加一并提交。"""

    def change(session: Session) -> None:
        session.execute(update(model).where(column == code).values(version=model.version + 1))
        session.commit()

    return change


# ---------------------------------------------------------------------------
# 会话、CSRF 与请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_state", ["missing", "malformed", "unknown", "revoked"])
def test_both_endpoints_require_an_admin_session(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    session_state: str,
) -> None:
    """验收「require_admin（没有有效后台会话时 401 admin_session_required）」；
    第 9 条「管理员在后台 A06 一次保存整页」：没有 cookie、格式不对、库里没有与
    已撤销的会话，GET 与 PUT 都 401，数据与审计不变。"""
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
    put = _put(client, token, {"states": []}, csrf="0" * 64)

    _assert_error(get, 401, SESSION_REQUIRED)
    _assert_error(put, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_save_without_a_valid_csrf_token_is_403(
    client: TestClient, engine: Engine, token: str, csrf: str | None
) -> None:
    """验收「检查顺序 413 → 415 → 401 → 403 csrf_failed → 422 请求体」：
    X-CSRF-Token 缺少、为空或不一致都 403 csrf_failed；结构不合格的请求体也先回答
    403；数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    responses = [
        _put(client, token, _body(page), csrf=csrf),
        _put(client, token, {"states": SECRET}, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("logged_in", [True, False])
def test_non_json_is_415(
    client: TestClient, engine: Engine, token: str, content_type: str | None, logged_in: bool
) -> None:
    """验收「检查顺序 413 → 415（不是 JSON）→ 401」：不是 JSON 时有没有会话都
    415，数据不变。"""
    content = json.dumps(_body(_page(client, token))).encode()
    before = _snapshot(engine)

    response = _put_raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_oversized_body_is_413_first(
    client: TestClient, engine: Engine, token: str, content_type: str, logged_in: bool
) -> None:
    """HANDOFF 0.67「请求体上限 64 KB，接口文件自己逐块读取」；验收「413（64 KB，
    按实际读到的字节逐块判断，先于一切校验）」：超过 64 KB 时不是 JSON、没有会话
    也是 413；不回显请求内容，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({"states": SECRET * (MAX_BODY_BYTES // len(SECRET))}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _put_raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 413, {"detail": "request body too large"})
    assert SECRET not in response.text
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(client: TestClient, token: str) -> None:
    """HANDOFF 0.67「请求体上限 64 KB」「app/api/pay.py 的 _json_body 上限固定为
    8 KB」：恰好 64 KB（远超 8 KB，用 JSON 空白补足）的合法整页照常保存，回 200。"""
    page = _page(client, token)
    content = json.dumps(_body(page)).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    assert _ok(_put_raw(client, token, content, "application/json")) == _saved_page(page)


def test_invalid_body_without_a_session_is_401(client: TestClient) -> None:
    """验收「检查顺序 413 → 415 → 401 → 403 → 422」：结构不合格的 JSON 请求体在
    没有会话时先回答 401。"""
    _assert_error(_put(client, None, {"states": SECRET}), 401, SESSION_REQUIRED)


MALFORMED = [
    # 整数字段 strict：布尔值、字符串与浮点数一律不收。
    pytest.param(_set("malaysia", "MY-W", "fee_sen", True), id="fee-bool"),
    pytest.param(_set("malaysia", "MY-W", "fee_sen", SECRET), id="fee-string"),
    pytest.param(_set("malaysia", "MY-E", "fee_sen", "800"), id="fee-numeric-string"),
    pytest.param(_set("countries", "SG", "fee_sen", 2000.0), id="fee-float"),
    pytest.param(_set("countries", "SG", "id", SECRET), id="id-string"),
    pytest.param(_set_other("version", True), id="version-bool"),
    # 汇率、国家与币种只接受 JSON 字符串。
    pytest.param(_set("fx", "JP", "rate", 34), id="rate-number"),
    pytest.param(_set("fx", "SG", "rate", 0.31), id="rate-float"),
    # 删除只收 delete 为 true，且不能同时给新值。
    pytest.param(
        _set_row("countries", "SG", lambda row: _ref(row, delete=False)), id="delete-false"
    ),
    pytest.param(_set_row("fx", "SG", lambda row: _ref(row, delete="true")), id="delete-string"),
    pytest.param(_set("countries", "SG", "delete", True), id="keep-and-delete"),
    # 马来西亚与兜底的行只接受 id、version、fee_sen。
    pytest.param(
        _set_row("malaysia", "MY-W", lambda row: _ref(row, delete=True)), id="malaysia-delete"
    ),
    pytest.param(_set_other("delete", True), id="other-delete"),
    # 已有行带币种或国家代码、新增行带行 ID 或缺字段。
    pytest.param(_set("fx", "SG", "currency_code", SECRET), id="existing-fx-with-currency"),
    pytest.param(_set("fx", "SG", "currency_decimals", 2), id="existing-fx-with-decimals"),
    pytest.param(_set("countries", "SG", "country_code", SECRET), id="existing-fee-with-country"),
    pytest.param(
        _append("countries", {"id": 999, "country_code": "NZ", "fee_sen": 100}),
        id="new-fee-with-id",
    ),
    pytest.param(_append("countries", {"country_code": 64, "fee_sen": 100}), id="new-country-int"),
    pytest.param(
        _append("fx", {"country_code": "NZ", "currency_code": "NZD", "rate": "0.36"}),
        id="new-fx-missing-decimals",
    ),
    pytest.param(
        _append(
            "fx",
            {"country_code": "NZ", "currency_code": "NZD", "currency_decimals": "2", "rate": "1"},
        ),
        id="new-fx-decimals-string",
    ),
    pytest.param(
        _append(
            "fx",
            {"country_code": "NZ", "currency_code": "NZD", "currency_decimals": 2, "rate": 0.36},
        ),
        id="new-fx-rate-number",
    ),
    # 多出字段或缺字段。
    pytest.param(_set("malaysia", "MY-W", "note", SECRET), id="extra-row-field"),
    pytest.param(_set_top("note", SECRET), id="extra-top-level"),
    pytest.param(_drop_top("malaysia"), id="missing-malaysia"),
    pytest.param(_drop_top("other"), id="missing-other"),
    pytest.param(_drop_top("countries"), id="missing-countries"),
    pytest.param(_drop_top("fx"), id="missing-fx"),
    pytest.param(lambda page, body: {**body, "other": [body["other"]]}, id="other-as-list"),
    pytest.param(_set_top("other", None), id="other-null"),
    pytest.param(_set_top("malaysia", SECRET), id="malaysia-not-a-list"),
    pytest.param(lambda page, body: [], id="not-an-object"),
    pytest.param(lambda page, body: SECRET, id="bare-string"),
]


@pytest.mark.parametrize("mutate", MALFORMED)
def test_malformed_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """验收「整数字段 strict（布尔值、字符串与浮点数一律不收），rate、country_code
    与 currency_code 只接受 JSON 字符串；多出字段或缺字段 422，错误沿用 _body_errors
    不回显」；第 9 条「马来西亚与兜底的行只有保留」「已有汇率行只给新汇率，不给币种与
    小数位」「新增的行不带行 ID 与版本号」：布尔值或字符串运费、数字汇率、delete
    为 false、已有行带币种、多出字段等都 422，不回显所给的值，数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, mutate))

    _assert_plain_errors(response)
    assert _snapshot(engine) == before


def _states_instead_of_malaysia(_page: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {"states": body["malaysia"], **{k: v for k, v in body.items() if k != "malaysia"}}


def _states_with_secret(_page: dict[str, Any], body: dict[str, Any]) -> None:
    body["states"] = [{"id": 1, "version": 1, "fee_sen": SECRET}]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_states_instead_of_malaysia, id="states-instead-of-malaysia"),
        pytest.param(_set_top("states", []), id="states-alongside-malaysia"),
        pytest.param(_states_with_secret, id="states-with-secret"),
    ],
)
def test_old_states_field_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """第 10 条「读取与保存接口里这一组的字段由 states 改名为 malaysia」；HANDOFF 0.69
    「请求体仍带 states 时照多出字段回 422」：只给旧字段名、或在 malaysia 之外另带 states，
    都 422，错误沿用 _body_errors 不回显所给的值，数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, mutate))

    _assert_plain_errors(response)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_get_returns_the_page_and_csrf_token(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 9 条「读取时每行带行 ID 与版本号」「读取接口与审计都用规范形式」；A06「页面
    改显示服务端返回的保存后数据（新的版本号）」；第 10 条「这一组的字段由 states 改名为
    malaysia，两行按西马、东马的顺序排列」；验收 GET 的字段依次为 malaysia、
    other、countries、fx 与 csrf_token，运费行为 id、version、zone_code、fee_sen，
    汇率行为 id、version、country_code、currency_code、currency_decimals、rate；
    马来西亚恰为两行、西马在前，国家按代码排列；只读，不写审计。"""
    before = _snapshot(engine)

    body = _page(client, token)

    assert list(body) == [*PAGE_FIELDS, "csrf_token"]
    assert body["csrf_token"] == _csrf_for(token)
    assert all(list(row) == ZONE_FIELDS for row in [*body["malaysia"], *body["countries"]])
    assert list(body["other"]) == ZONE_FIELDS
    assert all(list(row) == FX_FIELDS for row in body["fx"])

    malaysia = [(row["zone_code"], row["fee_sen"], row["version"]) for row in body["malaysia"]]
    countries = [(row["zone_code"], row["fee_sen"]) for row in body["countries"]]
    fx = [
        (row["country_code"], row["currency_code"], row["currency_decimals"], row["rate"])
        for row in body["fx"]
    ]
    assert malaysia == [("MY-W", 800, 1), ("MY-E", 1500, 1)]
    assert (body["other"]["zone_code"], body["other"]["fee_sen"]) == ("OTHER", OTHER_FEE)
    assert countries == sorted(COUNTRY_FEES.items())
    assert fx == [
        ("AU", "AUD", 2, "0.35"),
        ("CN", "CNY", 2, "1.65"),
        ("GB", "GBP", 2, "0.17"),
        ("HK", "HKD", 2, "1.8"),
        ("JP", "JPY", 0, "34"),
        ("SG", "SGD", 2, "0.31"),
        ("TH", "THB", 2, "7.6"),
        ("US", "USD", 2, "0.23"),
    ]
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 取值不合格与数据已被改动
# ---------------------------------------------------------------------------


INVALID_VALUES = [
    pytest.param(_set("malaysia", "MY-W", "fee_sen", -1), id="fee-negative"),
    pytest.param(_set("countries", "SG", "fee_sen", 1_000_000), id="fee-too-large"),
    pytest.param(_set("fx", "JP", "rate", "1e5"), id="rate-exponent"),
    pytest.param(_set("fx", "JP", "rate", "0"), id="rate-zero"),
    pytest.param(_set("fx", "JP", "rate", " 34"), id="rate-leading-space"),
    pytest.param(_set("fx", "JP", "rate", "34."), id="rate-trailing-point"),
    pytest.param(_append("countries", {"country_code": "my", "fee_sen": 100}), id="country-my"),
    pytest.param(_append("countries", {"country_code": "MY", "fee_sen": 100}), id="country-MY"),
    pytest.param(_append("countries", {"country_code": "nz", "fee_sen": 100}), id="country-lower"),
    pytest.param(
        _append(
            "fx",
            {"country_code": "NZ", "currency_code": "MYR", "currency_decimals": 2, "rate": "1"},
        ),
        id="currency-myr",
    ),
    pytest.param(
        _append(
            "fx",
            {"country_code": "NZ", "currency_code": "NZD", "currency_decimals": 4, "rate": "1"},
        ),
        id="decimals-4",
    ),
    pytest.param(_add_country_twice, id="country-added-twice"),
    pytest.param(_list_a_row_twice, id="row-id-twice"),
]


@pytest.mark.parametrize("mutate", INVALID_VALUES)
def test_invalid_values_are_rates_invalid(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """第 9 条「先不碰数据库检查请求本身——结构、格式、取值范围、新增的国家是否在
    列表里且不是马来西亚，以及同一组的新增行之间国家不重复——不通过即回答『取值
    不合格』」「每步不通过都整次保存不生效」；HANDOFF 0.67「取值不合格回 422
    rates_invalid」；验收「请求模型只校验结构与 JSON 类型……一律交给服务层」：
    运费 -1、汇率 "1e5"、国家 my 等都 422 rates_invalid，数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, mutate))

    _assert_error(response, 422, RATES_INVALID)
    assert _snapshot(engine) == before


STALE = [
    pytest.param(_set("malaysia", "MY-W", "version", 2), id="malaysia-version-ahead"),
    pytest.param(_set_other("version", 0), id="other-version-behind"),
    pytest.param(_leave_out_sg, id="country-row-left-out"),
    pytest.param(
        _append("countries", {"country_code": "SG", "fee_sen": 100}), id="added-country-exists"
    ),
    pytest.param(
        _set_row("fx", "JP", lambda row: {**_ref(row, rate="34"), "id": 9999}), id="unknown-row-id"
    ),
]


@pytest.mark.parametrize("mutate", STALE)
def test_stale_request_is_rates_changed(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """第 9 条「按行 ID 核对：请求里每个行 ID 都须是该组现有的行、版本号与当前相同，
    现有的行一个不漏；此外新增的国家在该组不得已有行」「不通过即回答『数据已被
    改动』」；HANDOFF 0.67「数据已被改动回 409 rates_changed」：版本号不符、漏列、
    行 ID 不存在、新增已有的国家都 409，数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, mutate))

    _assert_error(response, 409, RATES_CHANGED)
    assert _snapshot(engine) == before


def test_save_after_a_concurrent_change_is_rates_changed(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A06「服务端告知『数据已被改动』（另一个标签页或会话已保存过）时……整次保存
    不生效」：读取之后库里 JP 汇率的版本号被另一处加一，拿旧页面保存（改的是
    别的行）回 409 rates_changed，数据与审计不变。"""
    page = _page(client, token)
    _read(engine, _bump_version(DemoFxRate, DemoFxRate.country_code, "JP"))
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, _set("malaysia", "MY-W", "fee_sen", 900)))

    _assert_error(response, 409, RATES_CHANGED)
    assert _snapshot(engine) == before


def test_invalid_and_stale_together_is_rates_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 9 条「服务端分两步核对，每步不通过都整次保存不生效」；A06「服务端先查取值、
    再查数据是否已被改动，两者都有时先显示取值不合格」：运费 -1 与过期的版本号同在
    一次保存里时回 422 rates_invalid，数据与审计不变。"""
    page = _page(client, token)
    _read(engine, _bump_version(ShippingRate, ShippingRate.zone_code, "SG"))
    before = _snapshot(engine)

    response = _put(client, token, _mutated(page, _set("malaysia", "MY-W", "fee_sen", -1)))

    _assert_error(response, 422, RATES_INVALID)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def test_save_returns_the_saved_page_and_writes_audits(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 9 条「改了值的行版本号加一；删除的行从表里删除；新增的行版本号为 1」「每改、
    增、删一行写一条审计」；A06「成功……页面改显示服务端返回的保存后数据（新的
    版本号）」；HANDOFF 0.67「旧值与新值写成『代码 金额或汇率 v版本号』」；验收
    「成功回 200 与保存后的整页数据（字段同 GET，不含 csrf_token）」「审计的操作者
    正确」；第 10 条「第 9 条的『马来西亚』一组即这两行，只能改运费」「西马、东马的顺序」：
    一次保存改西马、兜底、国家运费与汇率，删一行国家运费与一行汇率，
    新增一行国家运费与一行汇率；响应等于随后 GET 的整页（去掉 csrf_token），马来西亚一组
    仍西马在前，审计取值写成「MY-W 800 v1」这样的代码、金额与版本号，操作者都是当前管理员。"""
    page = _page(client, token)
    gb = _row(page, "countries", "GB")
    hk = _row(page, "fx", "HK")
    body = _body(page)
    body["malaysia"][_index(page, "malaysia", "MY-W")]["fee_sen"] = 900
    body["other"]["fee_sen"] = 8500
    body["countries"][_index(page, "countries", "SG")]["fee_sen"] = 2100
    body["countries"][_index(page, "countries", "GB")] = _ref(gb, delete=True)
    body["countries"].append({"country_code": "NZ", "fee_sen": 7000})
    body["fx"][_index(page, "fx", "JP")]["rate"] = "35.50"
    body["fx"][_index(page, "fx", "HK")] = _ref(hk, delete=True)
    body["fx"].append(
        {"country_code": "NZ", "currency_code": "NZD", "currency_decimals": 2, "rate": "0.360"}
    )

    saved = _ok(_put(client, token, body))

    assert list(saved) == PAGE_FIELDS
    assert saved == _saved_page(_page(client, token))
    assert [row["zone_code"] for row in saved["malaysia"]] == ["MY-W", "MY-E"]
    west = _row(saved, "malaysia", "MY-W")
    assert (west["fee_sen"], west["version"]) == (900, 2)
    assert _row(saved, "malaysia", "MY-E")["version"] == 1
    assert (saved["other"]["fee_sen"], saved["other"]["version"]) == (8500, 2)
    countries = [row["zone_code"] for row in saved["countries"]]
    fx_countries = [row["country_code"] for row in saved["fx"]]
    assert countries == sorted({*COUNTRY_FEES, "NZ"} - {"GB"})
    assert fx_countries == sorted({country for country, *_ in FX_RATES} - {"HK"} | {"NZ"})
    assert _row(saved, "countries", "NZ")["version"] == 1
    assert (_row(saved, "fx", "JP")["rate"], _row(saved, "fx", "JP")["version"]) == ("35.5", 2)
    assert _row(saved, "fx", "NZ") | {"id": None} == {
        "id": None,
        "version": 1,
        "country_code": "NZ",
        "currency_code": "NZD",
        "currency_decimals": 2,
        "rate": "0.36",
    }

    audits = _audits(engine)
    assert len(audits) == 8
    assert set(audits) == {
        (account_id, SHIPPING_CHANGED, SHIPPING, "MY-W 800 v1", "MY-W 900 v2"),
        (account_id, SHIPPING_CHANGED, SHIPPING, "SG 2000 v1", "SG 2100 v2"),
        (account_id, SHIPPING_REMOVED, SHIPPING, "GB 6000 v1", None),
        (account_id, SHIPPING_CHANGED, SHIPPING, "OTHER 8000 v1", "OTHER 8500 v2"),
        (account_id, SHIPPING_ADDED, SHIPPING, None, "NZ 7000 v1"),
        (account_id, FX_CHANGED, FX, "JP JPY 0 34 v1", "JP JPY 0 35.5 v2"),
        (account_id, FX_REMOVED, FX, "HK HKD 2 1.8 v1", None),
        (account_id, FX_ADDED, FX, None, "NZ NZD 2 0.36 v1"),
    }


def test_remove_then_add_the_same_country_in_one_save(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 9 条「新增的国家在该组不得已有行（同一请求里把该行列为删除的除外，相当于
    先删后增）」「行 ID 不复用」「拿着旧页面保存的请求一定落在这一步」「删除后再
    新增同一国家，版本号从 1 重新开始」；HANDOFF 0.67「同一事务里先删后增时先写
    删除再插入」「删后再增各写一条审计」：同一次保存里删掉 SG 的运费与汇率再各新增
    一行，成功，新行的行 ID 与旧的不同、版本号为 1，写四条审计；之后拿旧页面保存
    回 409 rates_changed。"""
    page = _page(client, token)
    old_fee = _row(page, "countries", "SG")
    old_fx = _row(page, "fx", "SG")
    body = _body(page)
    body["countries"][_index(page, "countries", "SG")] = _ref(old_fee, delete=True)
    body["countries"].append({"country_code": "SG", "fee_sen": 2200})
    body["fx"][_index(page, "fx", "SG")] = _ref(old_fx, delete=True)
    body["fx"].append(
        {"country_code": "SG", "currency_code": "SGD", "currency_decimals": 3, "rate": "0.32"}
    )

    saved = _ok(_put(client, token, body))

    new_fee = _row(saved, "countries", "SG")
    new_fx = _row(saved, "fx", "SG")
    assert new_fee["id"] != old_fee["id"]
    assert (new_fee["fee_sen"], new_fee["version"]) == (2200, 1)
    assert new_fx["id"] != old_fx["id"]
    assert (new_fx["currency_decimals"], new_fx["rate"], new_fx["version"]) == (3, "0.32", 1)
    assert _audits(engine) == [
        (account_id, SHIPPING_REMOVED, SHIPPING, "SG 2000 v1", None),
        (account_id, FX_REMOVED, FX, "SG SGD 2 0.31 v1", None),
        (account_id, SHIPPING_ADDED, SHIPPING, None, "SG 2200 v1"),
        (account_id, FX_ADDED, FX, None, "SG SGD 3 0.32 v1"),
    ]

    before = _snapshot(engine)
    _assert_error(_put(client, token, _body(page)), 409, RATES_CHANGED)
    assert _snapshot(engine) == before


def test_saving_the_unchanged_page_writes_nothing(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 9 条「值与当前相同的行不改版本、不写审计」「汇率按数值比较（0.31 与
    0.310000 相同）」：原样保存整页（SG 汇率写成 0.310000）回 200 与当前数据，
    数据与审计不变。"""
    page = _page(client, token)
    before = _snapshot(engine)

    body = _mutated(page, _set("fx", "SG", "rate", "0.310000"))

    assert _ok(_put(client, token, body)) == _saved_page(page)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """验收「这些接口的处理函数与依赖产生的响应（含错误）都带 Cache-Control 为
    no-store」「接口不写日志」：GET、保存成功、401、403、413、415、请求体 422、
    rates_invalid 与 rates_changed 都带 no-store，应用不记日志。"""
    caplog.set_level(logging.DEBUG)
    page = _page(client, token)
    oversized = json.dumps({"states": "x" * MAX_BODY_BYTES}).encode()
    responses = [
        _get(client, token),
        _get(client, None),
        _put(client, token, _mutated(page, _set("malaysia", "MY-W", "fee_sen", 900))),
        _put(client, None, _body(page)),
        _put(client, token, _body(page), csrf=None),
        _put_raw(client, token, oversized, "application/json"),
        _put_raw(client, token, b"{}", "text/plain"),
        _put(client, token, {"states": []}),
        _put(client, token, _mutated(_page(client, token), _set("fx", "JP", "rate", "1e5"))),
        _put(client, token, _body(page)),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 401, 200, 401, 403, 413, 415, 422, 422, 409]
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

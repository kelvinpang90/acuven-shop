"""后台分类的读取、新建与保存接口：GET /api/admin/categories、POST /api/admin/categories、
PUT /api/admin/categories/{category_id}（app/api/admin_categories.py，SHOP-TASK-181；
规则见 SHOP-TASK-180 的 app/services/admin_categories.py）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 14 条
（下称「第 14 条」）、docs/UX.md 0.17 的 A10（下称「A10」）与 docs/HANDOFF.md 0.76 的
落实写法（下称「HANDOFF 0.76」）。每条测试（参数化的测试是每个用例）的文档字符串写明
它守住的原句；没有直接原句的，写明是 SHOP-TASK-181 验收里的约定（下称「验收」）。

夹具照 tests/test_admin_shipping_rates_api.py：SQLite 内存库按模型建表（StaticPool），
管理员与后台会话直接写库并提交；分类与商品写入与 tests/test_admin_categories.py 相同的
种子数据。接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动；
检查结果一律在另一个数据库会话里只查列值读出，读得到即说明接口已提交。期望的 CSRF
令牌在这里另算。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
from app.models import AdminAccount, AuditEvent, Category, Product, ProductVariant
from app.services.admin_auth import issue_admin_session, revoke_admin_session

URL = "/api/admin/categories"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
CATEGORY_INVALID = {"detail": "category_invalid"}
CATEGORY_CHANGED = {"detail": "category_changed"}
SECRET = "SECRETVALUE"

CREATED = "admin_category_created"
CHANGED = "admin_category_changed"
CATEGORY = "category"

CATEGORY_FIELDS = [
    "id",
    "slug",
    "name_en",
    "name_zh",
    "name_ms",
    "is_active",
    "version",
    "visible_products",
]

# 种子数据：分类 1 启用、分类 2 停用、分类 3 停用且没有英文名称。
KITCHEN = {
    "id": 1,
    "slug": "kitchen-dining",
    "name_en": "Kitchen & Dining",
    "name_zh": "厨房",
    "name_ms": None,
    "is_active": True,
    "version": 1,
    "visible_products": 1,
}
GARDEN = {
    "id": 2,
    "slug": "garden",
    "name_en": "Garden",
    "name_zh": None,
    "name_ms": None,
    "is_active": False,
    "version": 1,
    "visible_products": 0,
}
MISC = {
    "id": 3,
    "slug": "misc",
    "name_en": None,
    "name_zh": "杂项",
    "name_ms": None,
    "is_active": False,
    "version": 1,
    "visible_products": 0,
}
SEED = [KITCHEN, GARDEN, MISC]


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


def _product(db: Session, category_id: int, slug: str, **fields: object) -> None:
    """一件满足发布规则的商品（带一个启用的 SKU）；fields 覆盖默认值。"""
    defaults = {
        "name_en": slug.title(),
        "description_en": f"A {slug}.",
        "is_active": True,
        "max_per_order": 10,
    }
    product = Product(category_id=category_id, slug=slug, **(defaults | fields))
    db.add(product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=product.id,
            sku=f"{slug}-std",
            price_sen=1990,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        )
    )
    db.flush()


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
        for row in SEED:
            seed.add(
                Category(
                    slug=row["slug"],
                    name_en=row["name_en"],
                    name_zh=row["name_zh"],
                    name_ms=row["name_ms"],
                    is_active=row["is_active"],
                    version=1,
                )
            )
            seed.flush()
        # 分类 1：pan 可见；pot 下架、lid 缺英文描述，都不计。分类 2 停用，rake 不计。
        _product(seed, 1, "pan")
        _product(seed, 1, "pot", is_active=False)
        _product(seed, 1, "lid", description_en=None)
        _product(seed, 2, "rake")
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


def _auto_csrf(token: str | None, csrf: str | None) -> str | None:
    """csrf 为 "auto" 时按 token 算出正确的令牌；给 None 即不带请求头。"""
    if csrf == "auto":
        return None if token is None else _csrf_for(token)
    return csrf


def _get(client: TestClient, token: str | None) -> Any:
    return client.get(URL, headers=_headers(token))


def _post(client: TestClient, token: str | None, body: Any, *, csrf: str | None = "auto") -> Any:
    return client.post(URL, json=body, headers=_headers(token, _auto_csrf(token, csrf)))


def _put(
    client: TestClient,
    token: str | None,
    category_id: int | str,
    body: Any,
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.put(
        f"{URL}/{category_id}", json=body, headers=_headers(token, _auto_csrf(token, csrf))
    )


def _raw(
    client: TestClient,
    method: str,
    path: str,
    token: str | None,
    content: bytes,
    content_type: str | None,
) -> Any:
    headers = _headers(token, _auto_csrf(token, "auto"))
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.request(method, path, content=content, headers=headers)


def _new(**fields: Any) -> dict[str, Any]:
    """一个合格的新建请求体；fields 覆盖默认值。"""
    return {"name_en": "Toys", "name_zh": "玩具", "name_ms": "Mainan", "is_active": True} | fields


def _edit(row: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """把读到的一项原样列成保存请求（空名称写成空串）；fields 覆盖。"""
    body = {
        "version": row["version"],
        "name_en": row["name_en"] or "",
        "name_zh": row["name_zh"] or "",
        "name_ms": row["name_ms"] or "",
        "is_active": row["is_active"],
    }
    return body | fields


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """分类表的各行（只查列值，按 ID）与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        stmt = select(
            Category.id,
            Category.slug,
            Category.name_en,
            Category.name_zh,
            Category.name_ms,
            Category.is_active,
            Category.version,
        ).order_by(Category.id)
        rows = [tuple(row) for row in session.execute(stmt)]
        return rows, len(session.scalars(select(AuditEvent.id)).all())

    return _read(engine, read)


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """全部审计记录：管理员、操作、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = select(
            AuditEvent.admin_account_id,
            AuditEvent.action,
            AuditEvent.target_type,
            AuditEvent.target_id,
            AuditEvent.old_value,
            AuditEvent.new_value,
        ).order_by(AuditEvent.id)
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


def _fill_to(engine: Engine, count: int) -> None:
    """直接写库补足到 count 个分类（停用、英文名称各不相同）。"""

    def fill(session: Session) -> None:
        existing = len(session.scalars(select(Category.id)).all())
        for n in range(existing + 1, count + 1):
            session.add(Category(slug=f"filler-{n}", name_en=f"Filler {n}", is_active=False))
        session.commit()

    _read(engine, fill)


def _bump_version(category_id: int) -> Callable[[Session], None]:
    """另一处保存过：把分类的版本号加一并提交。"""

    def change(session: Session) -> None:
        session.execute(
            update(Category).where(Category.id == category_id).values(version=Category.version + 1)
        )
        session.commit()

    return change


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any, status_code: int = 200) -> Any:
    assert response.status_code == status_code, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _assert_plain_errors(response: Any) -> list[dict[str, Any]]:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert SECRET not in response.text
    return errors


def _categories(client: TestClient, token: str) -> list[dict[str, Any]]:
    return _ok(_get(client, token))["categories"]


# ---------------------------------------------------------------------------
# 会话、CSRF 与请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_state", ["missing", "malformed", "unknown", "revoked"])
def test_all_endpoints_require_an_admin_session(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    session_state: str,
) -> None:
    """验收「经 require_admin（没有有效后台会话 401 admin_session_required）」；第 14 条
    「管理员在后台维护分类」：没有 cookie、格式不对、库里没有与已撤销的会话，GET、POST
    与 PUT 都 401（PUT 的路径 ID 不合法也先 401），数据与审计不变。"""
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

    responses = [
        _get(client, token),
        _post(client, token, _new(), csrf="0" * 64),
        _post(client, token, {"name_en": SECRET}, csrf="0" * 64),
        _put(client, token, 1, _edit(KITCHEN), csrf="0" * 64),
        _put(client, token, "abc", {"name_en": SECRET}, csrf="0" * 64),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_writes_without_a_valid_csrf_token_are_403(
    client: TestClient, engine: Engine, token: str, csrf: str | None
) -> None:
    """验收「X-CSRF-Token（403 csrf_failed）」「先声明 CsrfDep 依赖、再声明路径 ID 依赖，
    使 403 先于路径的 422」：X-CSRF-Token 缺少、为空或不一致时，新建与保存都 403
    csrf_failed；结构不合格的请求体与不合法的路径 ID 也先回答 403；数据与审计不变。"""
    before = _snapshot(engine)

    responses = [
        _post(client, token, _new(), csrf=csrf),
        _post(client, token, {"name_en": SECRET}, csrf=csrf),
        _put(client, token, 1, _edit(KITCHEN, name_ms="Dapur"), csrf=csrf),
        _put(client, token, 1, {"name_en": SECRET}, csrf=csrf),
        _put(client, token, "0", _edit(KITCHEN), csrf=csrf),
        _put(client, token, SECRET, _edit(KITCHEN), csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


WRITE_PATHS = [
    pytest.param("POST", URL, id="create"),
    pytest.param("PUT", f"{URL}/1", id="save"),
    pytest.param("PUT", f"{URL}/0", id="save-invalid-id"),
]


@pytest.mark.parametrize(("method", "path"), WRITE_PATHS)
@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_oversized_body_is_413_first(
    client: TestClient,
    engine: Engine,
    token: str,
    method: str,
    path: str,
    content_type: str,
    logged_in: bool,
) -> None:
    """验收「请求体按实际读到的字节逐块判断超过 8 KB 即 413……先于一切校验」：超过
    8 KB 时不是 JSON、没有会话、路径 ID 不合法也是 413；不回显请求内容，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({"name_en": SECRET * (MAX_BODY_BYTES // len(SECRET))}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _raw(client, method, path, token if logged_in else None, content, content_type)

    _assert_error(response, 413, {"detail": "request body too large"})
    assert SECRET not in response.text
    assert _snapshot(engine) == before


@pytest.mark.parametrize(("method", "path"), WRITE_PATHS)
@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("logged_in", [True, False])
def test_non_json_is_415(
    client: TestClient,
    engine: Engine,
    token: str,
    method: str,
    path: str,
    content_type: str | None,
    logged_in: bool,
) -> None:
    """验收「不是 JSON 415（先于一切校验，照 app/api/admin_site_settings.py 的
    _save_body）」：不是 JSON 时有没有会话、路径 ID 合不合法都 415，数据不变。"""
    content = json.dumps(_new()).encode()
    before = _snapshot(engine)

    response = _raw(client, method, path, token if logged_in else None, content, content_type)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(client: TestClient, token: str) -> None:
    """验收「请求体 8 KB 上限」：恰好 8 KB（用 JSON 空白补足）的合法新建请求照常处理，
    回 201。"""
    content = json.dumps(_new()).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    _ok(_raw(client, "POST", URL, token, content, "application/json"), 201)


def _drop(key: str) -> Callable[[dict[str, Any]], Any]:
    def mutate(body: dict[str, Any]) -> Any:
        del body[key]
        return body

    return mutate


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], Any]:
    def mutate(body: dict[str, Any]) -> Any:
        body[key] = value
        return body

    return mutate


MALFORMED_COMMON = [
    pytest.param(_set("note", SECRET), id="extra-field"),
    pytest.param(_set("slug", SECRET), id="slug-given"),
    pytest.param(_drop("name_en"), id="missing-name-en"),
    pytest.param(_drop("name_zh"), id="missing-name-zh"),
    pytest.param(_drop("name_ms"), id="missing-name-ms"),
    pytest.param(_drop("is_active"), id="missing-is-active"),
    pytest.param(_set("name_en", None), id="name-en-null"),
    pytest.param(_set("name_ms", None), id="name-ms-null"),
    pytest.param(_set("name_zh", 12), id="name-number"),
    pytest.param(_set("name_en", [SECRET]), id="name-list"),
    pytest.param(_set("is_active", "true"), id="is-active-string"),
    pytest.param(_set("is_active", 1), id="is-active-int"),
    pytest.param(_set("is_active", None), id="is-active-null"),
    pytest.param(lambda body: [body], id="not-an-object"),
    pytest.param(lambda body: SECRET, id="bare-string"),
]

MALFORMED_SAVE = [
    pytest.param(_drop("version"), id="missing-version"),
    pytest.param(_set("version", True), id="version-bool"),
    pytest.param(_set("version", "1"), id="version-string"),
    pytest.param(_set("version", 1.0), id="version-float"),
    pytest.param(_set("version", None), id="version-null"),
]


@pytest.mark.parametrize("mutate", MALFORMED_COMMON)
def test_malformed_create_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Callable[[dict[str, Any]], Any]
) -> None:
    """验收「name_en、name_zh、name_ms 为字符串，is_active 为布尔值，四项必填，多出字段
    或类型不对 422，照 app/api/pay.py 的 _body_errors 不回显」：多出字段、缺字段、null、
    数字名称、字符串或整数的 is_active 等都 422，不回显所给的值，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_post(client, token, mutate(_new(name_en=SECRET))))
    assert _snapshot(engine) == before


@pytest.mark.parametrize("mutate", MALFORMED_COMMON + MALFORMED_SAVE)
def test_malformed_save_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Callable[[dict[str, Any]], Any]
) -> None:
    """验收「请求体模型（同新建，另有必填的整数 version）」：新建的各种不合格之外，缺
    version、布尔值当整数、字符串、浮点数与 null 的 version 都 422，不回显所给的值，
    数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_put(client, token, 1, mutate(_edit(KITCHEN, name_en=SECRET))))
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "category_id",
    [
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
        pytest.param(SECRET, id="not-a-number"),
        pytest.param("1.0", id="decimal"),
        pytest.param("01", id="leading-zero"),
        pytest.param("+1", id="plus-sign"),
        pytest.param("2147483648", id="above-int-max"),
        pytest.param("1" * 11, id="too-long"),
        pytest.param("9" * 200, id="very-long"),
    ],
)
def test_invalid_path_id_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, category_id: str
) -> None:
    """验收「路径 ID 照 app/api/admin_orders.py 的 _order_id 校验（不合法 422，只给固定
    消息、不回显）」：0、负数、非数字、小数、前导零、超出 INT 上限与超长都 422，错误只有
    path.category_id 一条，数据与审计不变。"""
    before = _snapshot(engine)

    errors = _assert_plain_errors(_put(client, token, category_id, _edit(KITCHEN)))

    assert errors == [
        {
            "type": "category_id_invalid",
            "loc": ["path", "category_id"],
            "msg": "Category ID should be a positive integer",
        }
    ]
    assert _snapshot(engine) == before


def test_invalid_path_id_is_checked_before_the_body(client: TestClient, token: str) -> None:
    """验收保存的步骤「路径 ID……（不合法 422）、请求体模型」依次进行：路径 ID 与请求体都
    不合格时只回路径的一条错误。"""
    errors = _assert_plain_errors(_put(client, token, "0", {"name_en": SECRET}))

    assert [error["loc"] for error in errors] == [["path", "category_id"]]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_get_returns_all_categories_and_csrf_token(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A10「列表与各分类的前台可见商品件数」；验收「返回 categories（load_admin_categories
    的全部项，字段依次为 id、slug、name_en、name_zh、name_ms、is_active、version、
    visible_products，名称为空时为 null）与由 cookie 算出的 csrf_token；不写审计」：
    含停用的全部分类按 id 排列；下架、缺英文描述的商品与停用分类下的商品都不计入
    visible_products；只读。"""
    before = _snapshot(engine)

    body = _ok(_get(client, token))

    assert list(body) == ["categories", "csrf_token"]
    assert body["csrf_token"] == _csrf_for(token)
    assert all(list(row) == CATEGORY_FIELDS for row in body["categories"])
    assert body["categories"] == SEED
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 新建
# ---------------------------------------------------------------------------


def test_create_returns_201_and_writes_one_audit(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """A10「新建」；第 14 条新建时按英文名称生成 slug、版本号 1；验收「成功回 201 与新分类
    （字段同读取的一项）」「新建成功 201 且审计一条」：名称去掉首尾空白，slug 由英文名称
    生成，响应等于随后读取到的该项，写一条 admin_category_created，操作者为当前管理员。"""
    created = _ok(
        _post(client, token, _new(name_en="  Toys & Games ", name_ms="", is_active=False)), 201
    )

    assert list(created) == CATEGORY_FIELDS
    assert created == {
        "id": 4,
        "slug": "toys-games",
        "name_en": "Toys & Games",
        "name_zh": "玩具",
        "name_ms": None,
        "is_active": False,
        "version": 1,
        "visible_products": 0,
    }
    assert _categories(client, token)[-1] == created
    assert _audits(engine) == [(account_id, CREATED, CATEGORY, 4, None, "toys-games")]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_new(name_en=""), id="english-empty"),
        pytest.param(_new(name_en="   "), id="english-blank"),
        pytest.param(_new(name_en="", is_active=False), id="english-empty-inactive"),
        pytest.param(_new(name_zh="字" * 201), id="name-too-long"),
        pytest.param(_new(name_en=" kitchen & DINING "), id="english-duplicate"),
    ],
)
def test_invalid_create_values_are_category_invalid(
    client: TestClient, engine: Engine, token: str, body: dict[str, Any]
) -> None:
    """第 14 条「英文名称在新建与启用时必填」「名称每种语言 0–200 个字符」「英文名称……不得
    重复」；HANDOFF 0.76「取值不合格回 422 category_invalid」；验收「英文名称重复 422」：
    缺英文名称、超长与大小写及首尾空白不同的重复英文名称都 422 category_invalid，数据与
    审计不变。"""
    before = _snapshot(engine)

    _assert_error(_post(client, token, body), 422, CATEGORY_INVALID)
    assert _snapshot(engine) == before


def test_the_31st_category_is_category_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 14 条「全站至多 30 个分类」；验收「第 31 个 422 category_invalid」：已有 30 个
    分类时新建 422 category_invalid，数据与审计不变；第 30 个照常 201。"""
    _fill_to(engine, 29)
    _ok(_post(client, token, _new()), 201)
    before = _snapshot(engine)
    assert len(before[0]) == 30

    _assert_error(_post(client, token, _new(name_en="Books")), 422, CATEGORY_INVALID)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def test_save_returns_the_saved_category_and_writes_audits(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """A10「逐个保存」；第 14 条「有改动才加版本、每改一项写一条审计」，slug「之后改名称
    也不变」；验收「成功……回 200 与保存后的分类」：改英文与马来文名称，版本号加一、slug
    不变，响应等于随后读取到的该项，按字段顺序写两条审计，操作者为当前管理员。"""
    saved = _ok(_put(client, token, 1, _edit(KITCHEN, name_en="Kitchen", name_ms=" Dapur ")))

    assert list(saved) == CATEGORY_FIELDS
    assert saved == KITCHEN | {"name_en": "Kitchen", "name_ms": "Dapur", "version": 2}
    assert _categories(client, token)[0] == saved
    assert _audits(engine) == [
        (account_id, CHANGED, CATEGORY, 1, "name_en Kitchen & Dining", "name_en Kitchen"),
        (account_id, CHANGED, CATEGORY, 1, "name_ms", "name_ms Dapur"),
    ]


def test_saving_unchanged_values_is_200_and_writes_nothing(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 14 条「有改动才加版本」；验收「成功或未变都回 200 与保存后的分类」：原样保存
    （名称带首尾空白也算未变）回 200 与当前分类，不加版本，数据与审计不变。"""
    before = _snapshot(engine)

    saved = _ok(_put(client, token, 1, _edit(KITCHEN, name_zh=" 厨房 ")))

    assert saved == KITCHEN
    assert _snapshot(engine) == before


def test_active_category_without_english_name_is_category_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 14 条「英文名称在新建与启用时必填」；验收「启用但没有英文名称 422」：启用没有
    英文名称的分类 3、或把启用的分类 1 的英文名称清空，都 422 category_invalid，数据与
    审计不变。"""
    before = _snapshot(engine)

    _assert_error(_put(client, token, 3, _edit(MISC, is_active=True)), 422, CATEGORY_INVALID)
    _assert_error(_put(client, token, 1, _edit(KITCHEN, name_en=" ")), 422, CATEGORY_INVALID)
    assert _snapshot(engine) == before


def test_inactive_category_with_empty_english_name_is_saved(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 14 条「英文名称在新建与启用时必填」（停用时可为空）；验收「停用且英文名称为空时
    200」：清空停用的分类 2 的英文名称回 200，英文名称为 null，写一条审计。"""
    saved = _ok(_put(client, token, 2, _edit(GARDEN, name_en="")))

    assert saved == GARDEN | {"name_en": None, "version": 2}
    assert _audits(engine) == [(account_id, CHANGED, CATEGORY, 2, "name_en Garden", "name_en")]


def test_save_with_a_duplicate_english_name_is_category_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 14 条「英文名称……不得重复」：把分类 2 的英文名称改成与分类 1 只差大小写的名称，
    422 category_invalid，数据与审计不变。"""
    before = _snapshot(engine)

    response = _put(client, token, 2, _edit(GARDEN, name_en="KITCHEN & dining"))

    _assert_error(response, 422, CATEGORY_INVALID)
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    ("category_id", "version"),
    [
        pytest.param(1, 2, id="version-ahead"),
        pytest.param(1, 0, id="version-behind"),
        pytest.param(99, 1, id="category-missing"),
        pytest.param(2147483647, 1, id="category-missing-int-max"),
    ],
)
def test_stale_or_missing_category_is_category_changed(
    client: TestClient, engine: Engine, token: str, category_id: int, version: int
) -> None:
    """第 14 条「保存核对版本号」；HANDOFF 0.76「分类已被改动回 409 category_changed」
    「不存在的分类按已被改动回 409」；验收「保存版本号不符与分类不存在 409
    category_changed」：数据与审计不变。"""
    before = _snapshot(engine)

    response = _put(client, token, category_id, _edit(KITCHEN, version=version, name_ms="Dapur"))

    _assert_error(response, 409, CATEGORY_CHANGED)
    assert _snapshot(engine) == before


def test_save_after_a_concurrent_change_is_category_changed(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A10「分类已被改动」（另一个标签页或会话已保存过）：读取之后分类 1 的版本号被另一处
    加一，拿旧的一项保存回 409 category_changed，数据与审计不变。"""
    row = _categories(client, token)[0]
    _read(engine, _bump_version(1))
    before = _snapshot(engine)

    _assert_error(_put(client, token, 1, _edit(row, name_ms="Dapur")), 409, CATEGORY_CHANGED)
    assert _snapshot(engine) == before


def test_invalid_and_stale_together_is_category_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 14 条与服务层的两步核对（先不碰数据库检查请求本身，再锁行核对版本号）：启用而
    没有英文名称与过期的版本号同在一次保存里时回 422 category_invalid。"""
    before = _snapshot(engine)

    response = _put(client, token, 3, _edit(MISC, version=5, is_active=True))

    _assert_error(response, 422, CATEGORY_INVALID)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient, engine: Engine, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """验收「三个接口的处理函数与依赖产生的全部响应（含错误）都带 no-store」「不写日志」：
    GET、新建 201、保存 200、401、403、413、415、请求体 422、路径 422、category_invalid
    与 category_changed 都带 no-store，应用不记日志。"""
    caplog.set_level(logging.DEBUG)
    oversized = json.dumps({"name_en": "x" * MAX_BODY_BYTES}).encode()
    responses = [
        _get(client, token),
        _get(client, None),
        _post(client, token, _new()),
        _put(client, token, 1, _edit(KITCHEN, name_ms="Dapur")),
        _post(client, None, _new()),
        _put(client, None, 1, _edit(KITCHEN)),
        _post(client, token, _new(), csrf=None),
        _put(client, token, 1, _edit(KITCHEN), csrf=None),
        _raw(client, "POST", URL, token, oversized, "application/json"),
        _raw(client, "PUT", f"{URL}/1", token, oversized, "application/json"),
        _raw(client, "POST", URL, token, b"{}", "text/plain"),
        _raw(client, "PUT", f"{URL}/1", token, b"{}", "text/plain"),
        _post(client, token, {"name_en": "x"}),
        _put(client, token, 1, {"name_en": "x"}),
        _put(client, token, "0", _edit(KITCHEN)),
        _post(client, token, _new(name_en="")),
        _put(client, token, 3, _edit(MISC, is_active=True)),
        _put(client, token, 1, _edit(KITCHEN)),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [
        200,
        401,
        201,
        200,
        401,
        401,
        403,
        403,
        413,
        413,
        415,
        415,
        422,
        422,
        422,
        422,
        422,
        409,
    ]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_only_get_post_and_put(app: FastAPI) -> None:
    """验收「GET /api/admin/categories、POST /api/admin/categories 与
    PUT /api/admin/categories/{category_id}」「app/main.py 注册新路由」：该路径下只有这三个。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("GET", URL), ("POST", URL), ("PUT", f"{URL}/{{category_id}}")}

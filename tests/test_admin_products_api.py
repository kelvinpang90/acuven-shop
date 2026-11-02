"""后台商品的列表、读取、新建与保存接口：GET /api/admin/products、
GET /api/admin/products/{product_id}、POST /api/admin/products、
PUT /api/admin/products/{product_id}（app/api/admin_products.py，SHOP-TASK-186；
规则见 SHOP-TASK-183 至 185 的 app/services/admin_products.py）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（下称「第 11 条」）、
docs/UX.md 0.17 的 A04（下称「A04」）与 docs/HANDOFF.md 0.77 的落实写法。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句；没有直接原句的，写明是
SHOP-TASK-186 验收里的约定（下称「验收」）。

夹具照 tests/test_admin_shipping_rates_api.py：SQLite 内存库按模型建表（StaticPool），
分类、管理员与后台会话直接写库并提交。种子商品经 create_product 造：
圆领 T 恤（商品 1；规格名 1 Colour、2 Size；规格值 1 Black、2 White、3 S、4 M；
SKU 1 black-s、2 black-m、3 white-m；一张图片）与搪瓷杯（商品 2，未上架，没有规格名；
SKU 4）。分类 1 启用，分类 2 停用。接口的会话依赖换成每个请求一个绑定同一内存库的会话，
关闭时回滚未提交的改动；检查结果一律在另一个数据库会话里只查列值读出，读得到即说明
接口已提交。期望的 CSRF 令牌在这里另算。
"""

from __future__ import annotations

import copy
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
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
)
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.admin_products import (
    OptionInput,
    ProductInput,
    ValueInput,
    ValueRef,
    VariantInput,
    create_product,
)

URL = "/api/admin/products"
MAX_BODY_BYTES = 256 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
PRODUCT_INVALID = {"detail": "product_invalid"}
PRODUCT_CHANGED = {"detail": "product_changed"}
PRODUCT_NOT_FOUND = {"detail": "product_not_found"}
SECRET = "SECRETVALUE"

SEEDED_AT = datetime(2026, 10, 31, 3, 0)
TEE_ID = 1
MUG_ID = 2
IMAGE_REF = "/static/products/tee.jpg"
TEE_SKUS = ["crew-neck-tee-black-s", "crew-neck-tee-black-m", "crew-neck-tee-white-m"]

LIST_FIELDS = ["products", "csrf_token"]
LIST_ITEM_FIELDS = [
    "id",
    "slug",
    "name_en",
    "name_zh",
    "name_ms",
    "is_active",
    "category_id",
    "category_name_en",
    "category_name_zh",
    "category_name_ms",
]
PRODUCT_FIELDS = [
    "id",
    "slug",
    "version",
    "category_id",
    "name_en",
    "name_zh",
    "name_ms",
    "description_en",
    "description_zh",
    "description_ms",
    "is_active",
    "max_per_order",
    "options",
    "variants",
    "images",
]
OPTION_FIELDS = ["id", "code", "name_en", "name_zh", "name_ms", "values"]
VALUE_FIELDS = ["id", "code", "name_en", "name_zh", "name_ms"]
VARIANT_FIELDS = [
    "id",
    "sku",
    "price_sen",
    "daily_initial_stock",
    "available_stock",
    "is_active",
    "option_value_ids",
]
IMAGE_FIELDS = ["id", "storage_ref"]

PRODUCT_CREATED = "admin_product_created"
OPTION_CREATED = "admin_product_option_created"
VALUE_CREATED = "admin_product_option_value_created"
VARIANT_CREATED = "admin_product_variant_created"
PRODUCT_CHANGED_AUDIT = "admin_product_changed"
VARIANT_CHANGED = "admin_product_variant_changed"


def _value_out(value_id: int, code: str, name_en: str) -> dict[str, Any]:
    return {"id": value_id, "code": code, "name_en": name_en, "name_zh": None, "name_ms": None}


def _variant_out(
    variant_id: int, sku: str, price_sen: int, option_value_ids: list[int]
) -> dict[str, Any]:
    return {
        "id": variant_id,
        "sku": sku,
        "price_sen": price_sen,
        "daily_initial_stock": 10,
        "available_stock": 10,
        "is_active": True,
        "option_value_ids": option_value_ids,
    }


# 单件读取圆领 T 恤应得的结果。
TEE_OUT: dict[str, Any] = {
    "id": TEE_ID,
    "slug": "crew-neck-tee",
    "version": 1,
    "category_id": 1,
    "name_en": "Crew Neck Tee",
    "name_zh": "圆领 T 恤",
    "name_ms": None,
    "description_en": "A soft cotton tee.",
    "description_zh": None,
    "description_ms": None,
    "is_active": True,
    "max_per_order": 10,
    "options": [
        {
            "id": 1,
            "code": "colour",
            "name_en": "Colour",
            "name_zh": "颜色",
            "name_ms": None,
            "values": [_value_out(1, "black", "Black"), _value_out(2, "white", "White")],
        },
        {
            "id": 2,
            "code": "size",
            "name_en": "Size",
            "name_zh": None,
            "name_ms": None,
            "values": [_value_out(3, "s", "S"), _value_out(4, "m", "M")],
        },
    ],
    "variants": [
        _variant_out(1, TEE_SKUS[0], 2500, [1, 3]),
        _variant_out(2, TEE_SKUS[1], 2600, [1, 4]),
        _variant_out(3, TEE_SKUS[2], 2700, [2, 4]),
    ],
    "images": [{"id": 1, "storage_ref": IMAGE_REF}],
}


# ---------------------------------------------------------------------------
# 种子商品（经服务层的 create_product 造）
# ---------------------------------------------------------------------------


def _seed_value(temp_id: str, name_en: str) -> ValueInput:
    return ValueInput(id=None, temp_id=temp_id, name_en=name_en, name_zh="", name_ms="")


def _seed_variant(temp_id: str, price_sen: int, *value_temp_ids: str) -> VariantInput:
    return VariantInput(
        id=None,
        temp_id=temp_id,
        option_values=[ValueRef(id=None, temp_id=value) for value in value_temp_ids],
        price_sen=price_sen,
        daily_initial_stock=10,
        is_active=True,
    )


TEE = ProductInput(
    category_id=1,
    name_en="Crew Neck Tee",
    name_zh="圆领 T 恤",
    name_ms="",
    description_en="A soft cotton tee.",
    description_zh="",
    description_ms="",
    is_active=True,
    max_per_order=10,
    options=[
        OptionInput(
            id=None,
            name_en="Colour",
            name_zh="颜色",
            name_ms="",
            values=[_seed_value("black", "Black"), _seed_value("white", "White")],
        ),
        OptionInput(
            id=None,
            name_en="Size",
            name_zh="",
            name_ms="",
            values=[_seed_value("s", "S"), _seed_value("m", "M")],
        ),
    ],
    variants=[
        _seed_variant("v1", 2500, "black", "s"),
        _seed_variant("v2", 2600, "black", "m"),
        _seed_variant("v3", 2700, "white", "m"),
    ],
)

MUG = ProductInput(
    category_id=1,
    name_en="Enamel Mug",
    name_zh="",
    name_ms="Mug Enamel",
    description_en="",
    description_zh="",
    description_ms="",
    is_active=False,
    max_per_order=4,
    options=[],
    variants=[_seed_variant("v1", 1800)],
)


# ---------------------------------------------------------------------------
# 夹具
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
        seed.add(
            Category(
                slug="apparel",
                name_en="Apparel",
                name_zh="服饰",
                name_ms="",
                is_active=True,
            )
        )
        seed.flush()
        seed.add(Category(slug="archive", name_en="Archive", is_active=False))
        # 管理员账号只能有一个：种子商品与接口用同一个。
        account = AdminAccount(
            username="admin@example.com",
            password_hash="not-a-real-hash",
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        seed.add(account)
        seed.commit()
        account_id = account.id
        create_product(seed, account_id, TEE, SEEDED_AT)
        create_product(seed, account_id, MUG, SEEDED_AT + timedelta(hours=1))
        seed.add(ProductImage(product_id=TEE_ID, storage_ref=IMAGE_REF, sort_order=0))
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def account_id(db: Session) -> int:
    found = db.scalar(select(AdminAccount.id))
    assert found is not None
    return found


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


# ---------------------------------------------------------------------------
# 请求与读取
# ---------------------------------------------------------------------------


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


def _list(client: TestClient, token: str | None) -> Any:
    return client.get(URL, headers=_headers(token))


def _get(client: TestClient, token: str | None, product_id: int | str) -> Any:
    return client.get(f"{URL}/{product_id}", headers=_headers(token))


def _post(client: TestClient, token: str | None, body: Any, *, csrf: str | None = "auto") -> Any:
    return client.post(URL, json=body, headers=_headers(token, _auto_csrf(token, csrf)))


def _put(
    client: TestClient,
    token: str | None,
    product_id: int | str,
    body: Any,
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.put(
        f"{URL}/{product_id}", json=body, headers=_headers(token, _auto_csrf(token, csrf))
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


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _rows(session: Session, *columns: Any) -> list[tuple[Any, ...]]:
    """各列的值，按第一列（行 ID）排列。"""
    stmt = select(*columns).order_by(columns[0])
    return [tuple(row) for row in session.execute(stmt)]


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """商品、规格名、规格值与 SKU 的各行（只查列值，按 ID）与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        return (
            _rows(session, Product.id, Product.slug, Product.version, Product.category_id),
            _rows(session, Product.id, Product.name_en, Product.name_ms, Product.is_active),
            _rows(session, ProductOption.id, ProductOption.code, ProductOption.name_en),
            _rows(session, ProductOptionValue.id, ProductOptionValue.code),
            _rows(session, ProductOptionValue.id, ProductOptionValue.name_en),
            _rows(session, ProductVariant.id, ProductVariant.sku, ProductVariant.price_sen),
            _rows(session, ProductVariant.id, ProductVariant.daily_initial_stock),
            _rows(session, ProductVariant.id, ProductVariant.available_stock),
            _rows(session, ProductVariant.id, ProductVariant.is_active),
            len(session.scalars(select(AuditEvent.id)).all()),
        )

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


def _bump_version(product_id: int) -> Callable[[Session], None]:
    """另一处保存过：把商品的版本号加一并提交。"""

    def change(session: Session) -> None:
        session.execute(
            update(Product).where(Product.id == product_id).values(version=Product.version + 1)
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


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


def _new_value(temp_id: str, name_en: str) -> dict[str, Any]:
    return {"temp_id": temp_id, "name_en": name_en, "name_zh": "", "name_ms": ""}


def _new_variant(temp_id: str, refs: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    variant = {
        "temp_id": temp_id,
        "option_values": refs,
        "price_sen": 3900,
        "daily_initial_stock": 20,
        "is_active": True,
    }
    return variant | fields


def _new(**fields: Any) -> dict[str, Any]:
    """一个合格的新建请求体（帆布袋：Colour 下 Black、Natural 两个值与两个 SKU）；
    fields 覆盖顶层字段。"""
    body = {
        "category_id": 1,
        "name_en": "Canvas Tote",
        "name_zh": "帆布袋",
        "name_ms": "",
        "description_en": "A canvas tote.",
        "description_zh": "",
        "description_ms": "",
        "is_active": True,
        "max_per_order": 5,
        "options": [
            {
                "name_en": "Colour",
                "name_zh": "",
                "name_ms": "",
                "values": [_new_value("b", "Black"), _new_value("n", "Natural")],
            }
        ],
        "variants": [
            _new_variant("v1", [{"temp_id": "b"}]),
            _new_variant("v2", [{"temp_id": "n"}], price_sen=3500, daily_initial_stock=0),
        ],
    }
    return body | fields


def _edit(product: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """把单件读取的结果原样列成保存请求（已有行只带 id，已有 SKU 不带 option_values，
    空文案写成空串）；fields 覆盖顶层字段。"""

    def names(row: dict[str, Any]) -> dict[str, str]:
        return {key: row[key] or "" for key in ("name_en", "name_zh", "name_ms")}

    body = {
        "version": product["version"],
        "category_id": product["category_id"],
        **names(product),
        "description_en": product["description_en"] or "",
        "description_zh": product["description_zh"] or "",
        "description_ms": product["description_ms"] or "",
        "is_active": product["is_active"],
        "max_per_order": product["max_per_order"],
        "options": [
            {
                "id": option["id"],
                **names(option),
                "values": [{"id": value["id"], **names(value)} for value in option["values"]],
            }
            for option in product["options"]
        ],
        "variants": [
            {
                "id": variant["id"],
                "price_sen": variant["price_sen"],
                "daily_initial_stock": variant["daily_initial_stock"],
                "is_active": variant["is_active"],
            }
            for variant in product["variants"]
        ],
    }
    return body | fields


Path = tuple[str | int, ...]
Mutation = Callable[[dict[str, Any]], Any]


def _set(path: Path, value: Any) -> Mutation:
    """把 path 所指的字段设为 value（字段不存在时加上）。"""

    def mutate(body: dict[str, Any]) -> Any:
        target: Any = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = copy.deepcopy(value)
        return body

    return mutate


def _drop(path: Path) -> Mutation:
    def mutate(body: dict[str, Any]) -> Any:
        target: Any = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        return body

    return mutate


def _append(path: Path, item: Any) -> Mutation:
    def mutate(body: dict[str, Any]) -> Any:
        target: Any = body
        for key in path:
            target = target[key]
        target.append(copy.deepcopy(item))
        return body

    return mutate


def _both(*mutations: Mutation) -> Mutation:
    def mutate(body: dict[str, Any]) -> Any:
        for each in mutations:
            body = each(body)
        return body

    return mutate


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
    """验收「四个接口经 require_admin（401 admin_session_required）」：没有 cookie、
    格式不对、库里没有与已撤销的会话，列表、单件、新建与保存都 401（单件与保存的
    路径 ID 不合法也先 401），数据与审计不变。"""
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
        _list(client, token),
        _get(client, token, TEE_ID),
        _get(client, token, "abc"),
        _post(client, token, _new(), csrf="0" * 64),
        _post(client, token, {"name_en": SECRET}, csrf="0" * 64),
        _put(client, token, TEE_ID, _edit(TEE_OUT), csrf="0" * 64),
        _put(client, token, "abc", {"name_en": SECRET}, csrf="0" * 64),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_writes_without_a_valid_csrf_token_are_403(
    client: TestClient, engine: Engine, token: str, csrf: str | None
) -> None:
    """验收「X-CSRF-Token（403 csrf_failed）」「照 app/api/admin_coupons.py 先声明 CsrfDep
    依赖、再声明路径 ID 依赖」：X-CSRF-Token 缺少、为空或不一致时，新建与保存都 403
    csrf_failed；结构不合格的请求体与不合法的路径 ID 也先回答 403；数据与审计不变。"""
    before = _snapshot(engine)

    responses = [
        _post(client, token, _new(), csrf=csrf),
        _post(client, token, {"name_en": SECRET}, csrf=csrf),
        _put(client, token, TEE_ID, _edit(TEE_OUT, name_ms="Baju"), csrf=csrf),
        _put(client, token, TEE_ID, {"name_en": SECRET}, csrf=csrf),
        _put(client, token, "0", _edit(TEE_OUT), csrf=csrf),
        _put(client, token, SECRET, _edit(TEE_OUT), csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


WRITE_PATHS = [
    pytest.param("POST", URL, id="create"),
    pytest.param("PUT", f"{URL}/{TEE_ID}", id="save"),
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
    """HANDOFF 0.77「请求体按实际读到的字节逐块判断，上限 256 KB」；验收「请求体
    逐块读取超过 256 KB 即 413」「413 先于登录检查」：超过 256 KB 时不是 JSON、
    没有会话、路径 ID 不合法也是 413；不回显请求内容，数据不变。"""
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
    """验收「不是 JSON 415」，排在 413 之后、require_admin 之前：不是 JSON 时有没有会话、
    路径 ID 合不合法都 415，数据不变。"""
    content = json.dumps(_new()).encode()
    before = _snapshot(engine)

    response = _raw(client, method, path, token if logged_in else None, content, content_type)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(client: TestClient, token: str) -> None:
    """HANDOFF 0.77「上限 256 KB」：恰好 256 KB（远超 app/api/pay.py 的 8 KB 与运费接口的
    64 KB，用 JSON 空白补足）的合法新建请求照常处理，回 201。"""
    content = json.dumps(_new()).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    _ok(_raw(client, "POST", URL, token, content, "application/json"), 201)


# 新建与保存的请求体里都有的字段。
MALFORMED_COMMON = [
    # 多出字段、缺字段。
    pytest.param(_set(("note",), SECRET), id="extra-field"),
    pytest.param(_set(("slug",), SECRET), id="slug-given"),
    pytest.param(_set(("options", 0, "code"), SECRET), id="option-code-given"),
    pytest.param(_set(("options", 0, "values", 0, "code"), SECRET), id="value-code-given"),
    pytest.param(_set(("variants", 0, "sku"), SECRET), id="sku-given"),
    pytest.param(_set(("variants", 0, "available_stock"), 5), id="available-stock-given"),
    pytest.param(_drop(("name_zh",)), id="missing-name-zh"),
    pytest.param(_drop(("description_ms",)), id="missing-description-ms"),
    pytest.param(_drop(("category_id",)), id="missing-category"),
    pytest.param(_drop(("options",)), id="missing-options"),
    pytest.param(_drop(("variants",)), id="missing-variants"),
    pytest.param(_drop(("options", 0, "values")), id="missing-values"),
    pytest.param(_drop(("options", 0, "values", 0, "name_ms")), id="missing-value-name"),
    pytest.param(_drop(("variants", 0, "price_sen")), id="missing-price"),
    pytest.param(_drop(("variants", 0, "is_active")), id="missing-variant-is-active"),
    # null：必填字段与可省略的 id、temp_id、option_values 都不收。
    pytest.param(_set(("name_en",), None), id="name-null"),
    pytest.param(_set(("description_zh",), None), id="description-null"),
    pytest.param(_set(("max_per_order",), None), id="max-per-order-null"),
    pytest.param(_set(("options", 0, "id"), None), id="option-id-null"),
    pytest.param(_set(("options", 0, "values", 0, "temp_id"), None), id="value-temp-id-null"),
    pytest.param(_set(("variants", 0, "id"), None), id="variant-id-null"),
    pytest.param(_set(("variants", 0, "option_values"), None), id="option-values-null"),
    pytest.param(_set(("variants", 0, "daily_initial_stock"), None), id="stock-null"),
    # 整数字段 strict：布尔值、字符串与浮点数一律不收；金额以整数仙收发。
    pytest.param(_set(("category_id",), True), id="category-bool"),
    pytest.param(_set(("category_id",), "1"), id="category-string"),
    pytest.param(_set(("max_per_order",), 10.0), id="max-per-order-float"),
    pytest.param(_set(("variants", 0, "price_sen"), 39.0), id="price-float"),
    pytest.param(_set(("variants", 0, "price_sen"), 39.5), id="price-fraction"),
    pytest.param(_set(("variants", 0, "price_sen"), True), id="price-bool"),
    pytest.param(_set(("variants", 0, "price_sen"), "3900"), id="price-string"),
    pytest.param(_set(("variants", 0, "daily_initial_stock"), False), id="stock-bool"),
    pytest.param(_set(("options", 0, "values", 0, "id"), "1"), id="value-id-string"),
    pytest.param(_set(("options", 0, "values", 0, "temp_id"), 5), id="value-temp-id-int"),
    # 文案为字符串，状态为布尔值。
    pytest.param(_set(("name_ms",), 12), id="name-number"),
    pytest.param(_set(("description_en",), [SECRET]), id="description-list"),
    pytest.param(_set(("options", 0, "name_zh"), False), id="option-name-bool"),
    pytest.param(_set(("is_active",), "true"), id="is-active-string"),
    pytest.param(_set(("is_active",), 1), id="is-active-int"),
    pytest.param(_set(("variants", 0, "is_active"), 0), id="variant-is-active-int"),
    # options、values、variants 为数组。
    pytest.param(_set(("options",), {"0": SECRET}), id="options-object"),
    pytest.param(_set(("options", 0, "values"), SECRET), id="values-string"),
    pytest.param(_set(("variants",), None), id="variants-null"),
    pytest.param(_set(("variants", 0, "option_values"), {"id": 1}), id="option-values-object"),
    pytest.param(lambda body: [body], id="not-an-object"),
    pytest.param(lambda body: SECRET, id="bare-string"),
]

# 只有新建的请求体里有的（ValueRef 只出现在新增的 SKU 上）。
MALFORMED_CREATE = [
    pytest.param(_set(("version",), 1), id="version-in-create"),
    pytest.param(_set(("variants", 0, "option_values", 0, "id"), None), id="ref-id-null"),
    pytest.param(_set(("variants", 0, "option_values", 0, "temp_id"), 1), id="ref-temp-id-int"),
    pytest.param(_set(("variants", 0, "option_values", 0, "id"), True), id="ref-id-bool"),
    pytest.param(_set(("variants", 0, "option_values", 0, "name_en"), SECRET), id="ref-extra"),
    pytest.param(_set(("variants", 0, "option_values", 0), SECRET), id="ref-string"),
]

MALFORMED_SAVE = [
    pytest.param(_drop(("version",)), id="missing-version"),
    pytest.param(_set(("version",), True), id="version-bool"),
    pytest.param(_set(("version",), "1"), id="version-string"),
    pytest.param(_set(("version",), 1.0), id="version-float"),
    pytest.param(_set(("version",), None), id="version-null"),
]


@pytest.mark.parametrize("mutate", MALFORMED_COMMON + MALFORMED_CREATE)
def test_malformed_create_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """验收「请求体模型只校验类型、必填与多出字段（strict，多出字段、缺字段、null 或类型不对
    422）」「可省略的 id、temp_id、option_values……类型不允许 None」「照 app/api/pay.py 的
    _body_errors 不回显」；HANDOFF 0.77「金额一律以整数仙收发」：多出字段（含 slug、代码与
    SKU）、缺字段、null、布尔值当整数、浮点数价格、数字名称、对象当数组等都 422，不回显
    所给的值，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_post(client, token, mutate(_new(name_en=SECRET))))
    assert _snapshot(engine) == before


@pytest.mark.parametrize("mutate", MALFORMED_COMMON + MALFORMED_SAVE)
def test_malformed_save_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """验收「请求体模型（新建的字段加必填的整数 version）」：新建的各种不合格之外，
    缺 version、布尔值当整数、字符串、浮点数与 null 的 version 都 422，不回显所给的值，
    数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_put(client, token, TEE_ID, mutate(_edit(TEE_OUT, name_en=SECRET))))
    assert _snapshot(engine) == before


BAD_PATH_IDS = [
    pytest.param("0", id="zero"),
    pytest.param("-1", id="negative"),
    pytest.param(SECRET, id="not-a-number"),
    pytest.param("1.0", id="decimal"),
    pytest.param("01", id="leading-zero"),
    pytest.param("+1", id="plus-sign"),
    pytest.param("2147483648", id="above-int-max"),
    pytest.param("1" * 11, id="too-long"),
    pytest.param("9" * 200, id="very-long"),
]

PATH_ID_ERROR = {
    "type": "product_id_invalid",
    "loc": ["path", "product_id"],
    "msg": "Product ID should be a positive integer",
}


@pytest.mark.parametrize("product_id", BAD_PATH_IDS)
def test_invalid_path_id_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, product_id: str
) -> None:
    """验收「路径 ID 照 app/api/admin_orders.py 的 _order_id 校验（不合法 422 不回显）」：
    单件读取与保存的路径 ID 为 0、负数、非数字、小数、前导零、超出 INT 上限与超长都 422，
    错误只有 path.product_id 一条，数据与审计不变。"""
    before = _snapshot(engine)

    read = _assert_plain_errors(_get(client, token, product_id))
    saved = _assert_plain_errors(_put(client, token, product_id, _edit(TEE_OUT)))

    assert read == [PATH_ID_ERROR]
    assert saved == [PATH_ID_ERROR]
    assert _snapshot(engine) == before


def test_invalid_path_id_is_checked_before_the_body(client: TestClient, token: str) -> None:
    """验收保存的步骤「路径 ID 422、请求体模型」依次进行：路径 ID 与请求体都不合格时
    只回路径的一条错误。"""
    errors = _assert_plain_errors(_put(client, token, "0", {"name_en": SECRET}))

    assert [error["loc"] for error in errors] == [["path", "product_id"]]


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_list_returns_all_products_and_csrf_token(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A04「全部商品（含未上架的），按新建时间从新到旧，不分页」；验收「返回 products
    （load_admin_products 的全部项）与由 cookie 算出的 csrf_token」「都不写审计」：
    每项的字段依次为 id、slug、三语名称、上架状态、分类 ID 与分类的三语名称，
    空文案为 null；数据与审计不变。"""
    before = _snapshot(engine)

    body = _ok(_list(client, token))

    assert list(body) == LIST_FIELDS
    assert body["csrf_token"] == _csrf_for(token)
    assert all(list(item) == LIST_ITEM_FIELDS for item in body["products"])
    assert body["products"] == [
        {
            "id": MUG_ID,
            "slug": "enamel-mug",
            "name_en": "Enamel Mug",
            "name_zh": None,
            "name_ms": "Mug Enamel",
            "is_active": False,
            "category_id": 1,
            "category_name_en": "Apparel",
            "category_name_zh": "服饰",
            "category_name_ms": None,
        },
        {
            "id": TEE_ID,
            "slug": "crew-neck-tee",
            "name_en": "Crew Neck Tee",
            "name_zh": "圆领 T 恤",
            "name_ms": None,
            "is_active": True,
            "category_id": 1,
            "category_name_en": "Apparel",
            "category_name_zh": "服饰",
            "category_name_ms": None,
        },
    ]
    assert _snapshot(engine) == before


def test_get_returns_one_product(client: TestClient, engine: Engine, token: str) -> None:
    """A04「单件编辑」；验收「返回 load_admin_product 的结果」「都不写审计」：
    字段与顺序同服务层的结果，规格名与规格值带代码，SKU 带代码、整数仙价格、库存与
    按规格名顺序的规格值 ID，另有图片；空文案为 null；数据与审计不变。"""
    before = _snapshot(engine)

    body = _ok(_get(client, token, TEE_ID))

    assert list(body) == PRODUCT_FIELDS
    assert all(list(option) == OPTION_FIELDS for option in body["options"])
    assert all(
        list(value) == VALUE_FIELDS for option in body["options"] for value in option["values"]
    )
    assert all(list(variant) == VARIANT_FIELDS for variant in body["variants"])
    assert all(list(image) == IMAGE_FIELDS for image in body["images"])
    assert body == TEE_OUT
    assert _snapshot(engine) == before


def test_get_product_without_options(client: TestClient, token: str) -> None:
    """A04「没有规格名的商品固定一个 SKU」：搪瓷杯的 options 与 images 为空数组，唯一的
    SKU 为 slug 接 -std，规格值 ID 为空数组。"""
    body = _ok(_get(client, token, MUG_ID))

    assert (body["options"], body["images"]) == ([], [])
    skus = [(row["sku"], row["option_value_ids"]) for row in body["variants"]]
    assert skus == [("enamel-mug-std", [])]


@pytest.mark.parametrize("product_id", [3, 999, 2**31 - 1])
def test_get_missing_product_is_404(client: TestClient, token: str, product_id: int) -> None:
    """HANDOFF 0.77「读取不存在的商品回 404 product_not_found」：合法但不存在的 ID
    （含 INT 上限）都 404 {"detail": "product_not_found"}。"""
    _assert_error(_get(client, token, product_id), 404, PRODUCT_NOT_FOUND)


# ---------------------------------------------------------------------------
# 新建
# ---------------------------------------------------------------------------


def test_create_returns_201_and_the_new_product(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 11 条「代码」（原文见 app/services/admin_codes.py：英文名称只保留 ASCII
    字母与数字并转小写）；A04「代码由服务端在保存时生成」「新增 SKU 保存后，当日可用库存
    等于它的每日初始库存」；验收「成功回 201 与新商品（同单件读取）」：帆布袋的 slug 为
    canvas-tote，Colour 沿用全站的 colour，Black 沿用 black，Natural 为 natural，两个 SKU
    为 slug 接各值的代码；响应等于随后的单件读取，版本号为 1；按商品、规格名、规格值、SKU
    的顺序每个新行写一条审计，操作者为当前管理员。"""
    before = _audits(engine)

    created = _ok(_post(client, token, _new()), 201)

    assert list(created) == PRODUCT_FIELDS
    assert created == _ok(_get(client, token, created["id"]))
    assert created["id"] == 3
    assert (created["slug"], created["version"], created["images"]) == ("canvas-tote", 1, [])
    assert (created["name_en"], created["name_ms"]) == ("Canvas Tote", None)
    assert created["max_per_order"] == 5
    [option] = created["options"]
    assert (option["code"], option["name_en"]) == ("colour", "Colour")
    assert [value["code"] for value in option["values"]] == ["black", "natural"]
    value_ids = [value["id"] for value in option["values"]]
    variants = [
        (
            row["sku"],
            row["price_sen"],
            row["daily_initial_stock"],
            row["available_stock"],
            row["is_active"],
            row["option_value_ids"],
        )
        for row in created["variants"]
    ]
    assert variants == [
        ("canvas-tote-black", 3900, 20, 20, True, [value_ids[0]]),
        ("canvas-tote-natural", 3500, 0, 0, True, [value_ids[1]]),
    ]
    variant_ids = [row["id"] for row in created["variants"]]
    assert _audits(engine)[len(before) :] == [
        (account_id, PRODUCT_CREATED, "product", created["id"], None, "canvas-tote"),
        (account_id, OPTION_CREATED, "product_option", option["id"], None, "colour"),
        (account_id, VALUE_CREATED, "product_option_value", value_ids[0], None, "black"),
        (account_id, VALUE_CREATED, "product_option_value", value_ids[1], None, "natural"),
        (
            account_id,
            VARIANT_CREATED,
            "product_variant",
            variant_ids[0],
            None,
            "canvas-tote-black",
        ),
        (
            account_id,
            VARIANT_CREATED,
            "product_variant",
            variant_ids[1],
            None,
            "canvas-tote-natural",
        ),
    ]


def test_create_without_options_dedupes_the_slug(client: TestClient, token: str) -> None:
    """第 11 条「代码」（原文见 app/services/admin_codes.py「去重时依次试后缀（无、-2、
    -3……）」）；验收「新建 201 且返回的代码正确」：不带规格名、英文名称与圆领 T 恤相同
    （大小写与首尾空白不同）的未上架商品，slug 为 crew-neck-tee-2，唯一的 SKU 为
    crew-neck-tee-2-std，名称去掉首尾空白；省略的 id、temp_id 不当作 null 提交。"""
    body = _new(
        name_en="  CREW neck tee ",
        is_active=False,
        options=[],
        variants=[_new_variant("only", [], price_sen=0, daily_initial_stock=0, is_active=False)],
    )

    created = _ok(_post(client, token, body), 201)

    assert created["slug"] == "crew-neck-tee-2"
    assert created["name_en"] == "CREW neck tee"
    assert [row["sku"] for row in created["variants"]] == ["crew-neck-tee-2-std"]
    assert created["options"] == []


def _too_many_options(body: dict[str, Any]) -> Any:
    body["options"] = [
        {
            "name_en": f"Option {n}",
            "name_zh": "",
            "name_ms": "",
            "values": [_new_value(f"t{n}", "One")],
        }
        for n in range(4)
    ]
    body["variants"] = [_new_variant("v", [{"temp_id": f"t{n}"} for n in range(4)])]
    return body


INVALID_CREATE = [
    # ValueRef 恰带 id、temp_id 之一，交给服务层判定。
    pytest.param(
        _set(("variants", 0, "option_values", 0), {"id": 1, "temp_id": "b"}), id="ref-both"
    ),
    pytest.param(_set(("variants", 0, "option_values", 0), {}), id="ref-neither"),
    # 新建商品不带任何行 ID。
    pytest.param(_set(("options", 0, "id"), 1), id="option-with-id"),
    pytest.param(_set(("options", 0, "values", 0, "id"), 1), id="value-with-id"),
    pytest.param(_drop(("options", 0, "values", 0, "temp_id")), id="value-without-temp-id"),
    pytest.param(_set(("variants", 0, "id"), 1), id="variant-with-id"),
    pytest.param(_drop(("variants", 0, "option_values")), id="new-variant-without-values"),
    # 取值范围与上限。
    pytest.param(_set(("variants", 0, "price_sen"), -1), id="price-negative"),
    pytest.param(_set(("variants", 0, "price_sen"), 50_000_001), id="price-too-large"),
    pytest.param(_set(("variants", 0, "daily_initial_stock"), 10_000), id="stock-too-large"),
    pytest.param(_set(("max_per_order",), 0), id="max-per-order-zero"),
    pytest.param(_set(("max_per_order",), 1001), id="max-per-order-too-large"),
    pytest.param(_set(("category_id",), 2**40), id="category-above-int"),
    pytest.param(_set(("name_en",), " "), id="name-en-blank"),
    pytest.param(_set(("name_zh",), "x" * 201), id="name-too-long"),
    pytest.param(_set(("variants",), []), id="no-variants"),
    pytest.param(_too_many_options, id="four-options"),
    pytest.param(_set(("options", 0, "values", 1, "name_en"), " black "), id="duplicate-value"),
    pytest.param(_set(("options", 0, "values", 1, "temp_id"), "b c"), id="temp-id-format"),
    pytest.param(_set(("variants", 1, "option_values"), [{"temp_id": "b"}]), id="same-combo"),
    pytest.param(_set(("variants", 0, "option_values"), [{"temp_id": "x"}]), id="unknown-ref"),
    # 上架条件与分类。
    pytest.param(_set(("description_en",), ""), id="active-without-description"),
    pytest.param(
        _both(
            _set(("variants", 0, "is_active"), False),
            _set(("variants", 1, "is_active"), False),
        ),
        id="active-without-active-variant",
    ),
    pytest.param(_set(("category_id",), 2), id="inactive-category"),
    pytest.param(_set(("category_id",), 999), id="missing-category"),
]


@pytest.mark.parametrize("mutate", INVALID_CREATE)
def test_invalid_create_values_are_product_invalid(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """A04「服务端回答『取值不合格』时……整次保存不生效」；HANDOFF 0.77「取值不合格回
    422 product_invalid」；验收「id 与 temp_id 的搭配（两者都有或都没有……）交给服务层
    判定、回 422 product_invalid」：ValueRef 同时带 id 与 temp_id 或都不带、新建带行 ID、
    价格 -1、四个规格名、上架没有英文描述、分类停用或不存在等都 422 product_invalid，
    数据与审计不变。"""
    before = _snapshot(engine)

    _assert_error(_post(client, token, mutate(_new())), 422, PRODUCT_INVALID)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def test_save_returns_the_saved_product(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """A04「成功时页面改显示服务端返回的保存后数据（版本号随之更新）」「保存核对版本号
    与全部行 ID……每改一项写审计」；验收「成功或未变都回 200 与保存后的商品」：改马来文
    名称、一个 SKU 的价格与另一个 SKU 的启用状态，回 200，版本号为 2，响应等于随后的单件
    读取，slug 与 SKU 代码不变，每改一项写一条审计。"""
    body = _edit(TEE_OUT, name_ms="Baju")
    body["variants"][0]["price_sen"] = 2400
    body["variants"][2]["is_active"] = False
    before = _audits(engine)

    saved = _ok(_put(client, token, TEE_ID, body))

    assert list(saved) == PRODUCT_FIELDS
    assert saved == _ok(_get(client, token, TEE_ID))
    assert (saved["version"], saved["name_ms"], saved["slug"]) == (2, "Baju", "crew-neck-tee")
    assert [row["sku"] for row in saved["variants"]] == TEE_SKUS
    assert [row["price_sen"] for row in saved["variants"]] == [2400, 2600, 2700]
    assert [row["is_active"] for row in saved["variants"]] == [True, True, False]
    assert saved["options"] == TEE_OUT["options"]
    assert [(row[0], row[1], row[3]) for row in _audits(engine)[len(before) :]] == [
        (account_id, PRODUCT_CHANGED_AUDIT, TEE_ID),
        (account_id, VARIANT_CHANGED, 1),
        (account_id, VARIANT_CHANGED, 3),
    ]


def test_saving_unchanged_values_is_200_and_writes_nothing(
    client: TestClient, engine: Engine, token: str
) -> None:
    """验收「成功或未变都回 200 与保存后的商品」；A04「保存核对版本号与全部行 ID……每改
    一项写审计」：原样保存回 200 与当前的商品，版本号仍为 1，数据与审计不变。"""
    before = _snapshot(engine)

    saved = _ok(_put(client, token, TEE_ID, _edit(TEE_OUT)))

    assert saved == TEE_OUT
    assert _snapshot(engine) == before


def test_save_adds_option_values_and_variants(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """A04「本次新增……的规格值与 SKU」「每个规格名下各选一个值（可选同一次新增的值）」
    「新增 SKU 保存后，当日可用库存等于它的每日初始库存」；验收「保存时新增规格值与 SKU」
    「option_values 的每项为带 id 或 temp_id 的对象」：
    在 Size 下新增 L、新增 White/L 的 SKU（White 以 id、L 以 temp_id 引用），回 200，
    版本号为 2，L 排在 M 之后、代码为 l，SKU 代码为 crew-neck-tee-white-l，当日可用库存
    等于每日初始库存；已有 SKU 不变；新增的两行各写一条审计。"""
    body = _edit(TEE_OUT)
    body["options"][1]["values"].append(_new_value("l", "L"))
    body["variants"].append(
        _new_variant("v4", [{"id": 2}, {"temp_id": "l"}], price_sen=2900, daily_initial_stock=7)
    )
    before = _audits(engine)

    saved = _ok(_put(client, token, TEE_ID, body))

    assert saved == _ok(_get(client, token, TEE_ID))
    assert saved["version"] == 2
    sizes = saved["options"][1]["values"]
    assert [value["code"] for value in sizes] == ["s", "m", "l"]
    new_value = sizes[-1]
    new_variant = saved["variants"][-1]
    assert saved["variants"][:3] == TEE_OUT["variants"]
    assert {key: value for key, value in new_variant.items() if key != "id"} == {
        "sku": "crew-neck-tee-white-l",
        "price_sen": 2900,
        "daily_initial_stock": 7,
        "available_stock": 7,
        "is_active": True,
        "option_value_ids": [2, new_value["id"]],
    }
    assert _audits(engine)[len(before) :] == [
        (account_id, VALUE_CREATED, "product_option_value", new_value["id"], None, "l"),
        (
            account_id,
            VARIANT_CREATED,
            "product_variant",
            new_variant["id"],
            None,
            "crew-neck-tee-white-l",
        ),
    ]


INVALID_SAVE = [
    pytest.param(_set(("variants", 0, "price_sen"), -1), id="price-negative"),
    pytest.param(_set(("name_en",), ""), id="active-without-english-name"),
    pytest.param(_set(("category_id",), 2), id="inactive-category"),
    pytest.param(
        _set(("variants", 0, "option_values"), [{"id": 1}, {"id": 3}]),
        id="existing-variant-with-values",
    ),
    pytest.param(_set(("options", 0, "values", 0, "temp_id"), "x"), id="value-id-and-temp-id"),
    pytest.param(_set(("variants", 0, "temp_id"), "x"), id="variant-id-and-temp-id"),
    pytest.param(
        _append(
            ("variants",),
            _new_variant("v4", [{"id": 2, "temp_id": "x"}, {"id": 3}]),
        ),
        id="ref-both",
    ),
    pytest.param(
        _append(("options",), {"name_en": "Fit", "name_zh": "", "name_ms": "", "values": []}),
        id="option-added",
    ),
    pytest.param(
        _append(("variants",), _new_variant("v4", [{"id": 1}, {"id": 3}])),
        id="combination-exists",
    ),
]


@pytest.mark.parametrize("mutate", INVALID_SAVE)
def test_invalid_save_values_are_product_invalid(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """A04「规格名只在新建商品时给出……之后不能增减」「已有 SKU 的规格值组合不能改」；
    HANDOFF 0.77「取值不合格回 422 product_invalid」；验收「id 与 temp_id 的搭配（两者
    都有或都没有、已有 SKU 带 option_values 等）交给服务层判定」：价格 -1、上架而英文
    名称为空、停用的分类、已有 SKU 带 option_values、已有行另带 temp_id、ValueRef 同时带
    id 与 temp_id、新增规格名、新增与已有 SKU 组合相同都 422 product_invalid，数据与审计
    不变。"""
    before = _snapshot(engine)

    _assert_error(_put(client, token, TEE_ID, mutate(_edit(TEE_OUT))), 422, PRODUCT_INVALID)
    assert _snapshot(engine) == before


STALE = [
    pytest.param(_set(("version",), 2), id="version-ahead"),
    pytest.param(_set(("version",), 0), id="version-behind"),
    pytest.param(_drop(("variants", 2)), id="variant-left-out"),
    pytest.param(_set(("options", 0, "values", 1, "id"), 999), id="unknown-value-id"),
    pytest.param(_set(("variants", 0, "id"), 4), id="other-products-variant"),
]


@pytest.mark.parametrize("mutate", STALE)
def test_stale_request_is_product_changed(
    client: TestClient, engine: Engine, token: str, mutate: Mutation
) -> None:
    """A04「保存核对版本号与全部行 ID，『商品已被改动』时不自动合并」；HANDOFF 0.77「商品
    已被改动回 409 product_changed」；验收「版本号不符 409」：版本号不符、漏列 SKU、规格值
    ID 不存在、列了别的商品的 SKU 都 409 product_changed，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_error(_put(client, token, TEE_ID, mutate(_edit(TEE_OUT))), 409, PRODUCT_CHANGED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("product_id", [3, 999, 2**31 - 1])
def test_save_missing_product_is_product_changed(
    client: TestClient, engine: Engine, token: str, product_id: int
) -> None:
    """验收「GET 不存在 404、PUT 不存在 409 product_changed」：合法但不存在的商品 ID（含
    INT 上限）保存都 409，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_error(_put(client, token, product_id, _edit(TEE_OUT)), 409, PRODUCT_CHANGED)
    assert _snapshot(engine) == before


def test_save_after_a_concurrent_change_is_product_changed(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A04「服务端回答『商品已被改动』时……未保存的修改丢弃，不自动合并」：读取之后库里的
    版本号被另一处加一，拿旧页面保存回 409 product_changed，数据与审计不变。"""
    page = _ok(_get(client, token, TEE_ID))
    _read(engine, _bump_version(TEE_ID))
    before = _snapshot(engine)

    response = _put(client, token, TEE_ID, _edit(page, name_ms="Baju"))

    _assert_error(response, 409, PRODUCT_CHANGED)
    assert _snapshot(engine) == before


def test_invalid_and_stale_together_is_product_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """A04「服务端回答『取值不合格』时同样显示。两者都整次保存不生效」；第 11 条第一步
    只看请求、不碰数据库（app/services/admin_products.py），先于锁行核对：价格 -1 与过期的
    版本号同在一次保存里时回 422 product_invalid，数据与审计不变。"""
    _read(engine, _bump_version(TEE_ID))
    before = _snapshot(engine)
    body = _edit(TEE_OUT)
    body["variants"][0]["price_sen"] = -1

    _assert_error(_put(client, token, TEE_ID, body), 422, PRODUCT_INVALID)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """验收「四个接口的处理函数与依赖产生的全部响应（含错误）都带 no-store」「不写日志」：
    列表、单件、新建 201、保存 200、401、403、413、415、请求体 422、路径 422、404、
    product_invalid 与 product_changed 都带 no-store，应用不记日志。"""
    caplog.set_level(logging.DEBUG)
    oversized = json.dumps({"name_en": "x" * MAX_BODY_BYTES}).encode()
    responses = [
        _list(client, token),
        _get(client, token, TEE_ID),
        _post(client, token, _new()),
        _put(client, token, TEE_ID, _edit(TEE_OUT, name_ms="Baju")),
        _list(client, None),
        _get(client, None, TEE_ID),
        _post(client, None, _new()),
        _put(client, None, TEE_ID, _edit(TEE_OUT)),
        _post(client, token, _new(), csrf=None),
        _put(client, token, TEE_ID, _edit(TEE_OUT), csrf=None),
        _raw(client, "POST", URL, token, oversized, "application/json"),
        _raw(client, "PUT", f"{URL}/{TEE_ID}", token, oversized, "application/json"),
        _raw(client, "POST", URL, token, b"{}", "text/plain"),
        _raw(client, "PUT", f"{URL}/{TEE_ID}", token, b"{}", "text/plain"),
        _post(client, token, {"name_en": "x"}),
        _put(client, token, TEE_ID, {"name_en": "x"}),
        _get(client, token, "0"),
        _put(client, token, "0", _edit(TEE_OUT)),
        _get(client, token, 999),
        _post(client, token, _new(max_per_order=0)),
        _put(client, token, TEE_ID, _edit(TEE_OUT, max_per_order=0)),
        _put(client, token, TEE_ID, _edit(TEE_OUT)),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [
        200,
        200,
        201,
        200,
        401,
        401,
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
        404,
        422,
        422,
        409,
    ]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_the_four_endpoints(app: FastAPI) -> None:
    """验收「GET /api/admin/products、GET /api/admin/products/{product_id}、
    POST /api/admin/products 与 PUT /api/admin/products/{product_id}」「app/main.py 注册新
    路由」：该路径下只有这四个（SHOP-TASK-190 的图片接口在 /images 之下，不在此列）。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL) and not path.endswith("/images") and "/images/" not in path
        for method in operations
    }

    assert routes == {
        ("GET", URL),
        ("POST", URL),
        ("GET", f"{URL}/{{product_id}}"),
        ("PUT", f"{URL}/{{product_id}}"),
    }

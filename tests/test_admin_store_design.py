"""后台店铺装修接口：GET /api/admin/store-design、PUT /api/admin/store-design
（app/api/admin_store_design.py 与 app/services/store_design.py，SHOP-TASK-051）。

依据 docs/REQUIREMENTS.md「店铺装修」（下称「需求」：每款主题提供若干预先校过对比度的主色，管理员
只能从中选一个；首页由四个区块组成，管理员可调整它们的顺序与显示或隐藏；精选商品由管理员挑选最多
4 件已上架商品并排定顺序；装修保存后对所有访客生效；修改记入后台审计记录）、docs/UX.md 0.10 的
A08（下称「A08」）与 docs/HANDOFF.md 0.34 记录的 Kelvin 2026-10-06 决定（首版不含标志图；精选
商品复用目录的 published() 判定），以及 0.40 记录的 Kelvin 2026-10-08 决定（原已在精选里、之后
才下架的商品保存时放行，SHOP-TASK-061）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的需求
或 A08 原句；没有直接原句的，写明是 SHOP-TASK-051 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。管理员、后台会话、
商品与装修设置都直接写库并提交；装修设置按迁移 0016 的默认值写入（另有测试覆盖设置行不存在）。
接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动；检查结果一律在另一个
数据库会话里读，读得到即说明接口已提交。期望的 CSRF 令牌在这里另算。SQLite 不支持 FOR UPDATE，
锁定查询另用 MySQL 方言编译检查。
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
from sqlalchemy import Engine, Select, create_engine, event, select, text, update
from sqlalchemy.dialects import mysql
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
    ProductVariant,
    StoreDesignSetting,
    StoreFeaturedProduct,
    StoreHomeBlock,
)
from app.models.admin import AUDIT_VALUE_MAX_LENGTH
from app.models.store_design import (
    DEFAULT_HOME_BLOCKS,
    HOME_BLOCKS,
    SINGLETON_SLOT,
    THEME_ACCENTS,
)
from app.services import store_design
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.store_design import (
    StoreDesignInvalid,
    check_theme_and_accent,
    lock_setting_statement,
)

URL = "/api/admin/store-design"
PUBLIC_URL = "/api/store-design"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

SAVED = "admin_store_design_saved"
SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
SECRET = "SECRETVALUE"

DEFAULT_BLOCKS_JSON = [{"block": block, "visible": True} for block in HOME_BLOCKS]
# 每款主题配空主色，以及每款主题配它自己的每个主色。
NULL_ACCENTS = [(theme, None) for theme in THEME_ACCENTS]
ALL_ACCENTS = [(theme, accent) for theme, accents in THEME_ACCENTS.items() for accent in accents]


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

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
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
    """按迁移 0016 写入的默认设置：pandan、主色为空、四个区块按默认顺序全部显示、没有精选。"""
    db.add(StoreDesignSetting(singleton_slot=SINGLETON_SLOT, theme="pandan", accent=None))
    for block, position, visible in DEFAULT_HOME_BLOCKS:
        db.add(StoreHomeBlock(block=block, position=position, is_visible=visible))
    db.commit()


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


def _category(db: Session, slug: str, *, active: bool = True) -> Category:
    row = Category(slug=slug, name_en=slug.title(), is_active=active)
    db.add(row)
    db.flush()
    return row


def _product(
    db: Session,
    slug: str,
    category: Category,
    *,
    active: bool = True,
    product_id: int | None = None,
    name_zh: str | None = None,
) -> Product:
    """一件带一个启用规格的商品并提交；active 为假即已下架（不满足 published()）。"""
    product = Product(
        id=product_id,
        category_id=category.id,
        slug=slug,
        name_en=f"Name {slug}",
        name_zh=name_zh,
        description_en="Description",
        is_active=active,
    )
    db.add(product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=product.id,
            sku=f"{slug}-sku",
            price_sen=1000,
            daily_initial_stock=5,
            available_stock=5,
            is_active=True,
        )
    )
    db.commit()
    return product


def _feature(db: Session, *products: Product) -> None:
    for position, product in enumerate(products, start=1):
        db.add(StoreFeaturedProduct(position=position, product_id=product.id))
    db.commit()


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _get(client: TestClient, token: str | None, lang: str | None = None) -> Any:
    params = {} if lang is None else {"lang": lang}
    return client.get(URL, params=params, headers=_headers(token))


def _put(
    client: TestClient,
    token: str | None,
    body: Any,
    *,
    csrf: str | None = "auto",
    lang: str | None = None,
) -> Any:
    """csrf 默认按 token 算出正确的令牌；给 None 即不带请求头。"""
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    params = {} if lang is None else {"lang": lang}
    return client.put(URL, json=body, params=params, headers=_headers(token, csrf))


def _put_raw(
    client: TestClient, token: str | None, content: bytes, content_type: str | None
) -> Any:
    headers = _headers(token, None if token is None else _csrf_for(token))
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.put(URL, content=content, headers=headers)


def _body(
    theme: str = "batik",
    accent: str | None = "sogan",
    blocks: list[tuple[str, bool]] | None = None,
    featured: list[int] | None = None,
) -> dict[str, Any]:
    """合法的请求体；区块默认为精选、主视觉（隐藏）、演示怎么玩、按分类浏览。"""
    if blocks is None:
        blocks = [("featured", True), ("hero", False), ("how", True), ("categories", True)]
    return {
        "theme": theme,
        "accent": accent,
        "home_blocks": [{"block": block, "visible": visible} for block, visible in blocks],
        "featured_product_ids": [] if featured is None else featured,
    }


def _with_blocks(*blocks: Any) -> dict[str, Any]:
    return {**_body(), "home_blocks": list(blocks)}


def _without(field: str) -> dict[str, Any]:
    return {key: value for key, value in _body().items() if key != field}


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """库里的装修设置：设置各行（主题、主色、更新时间）、区块各行（区块、位置、是否显示）、
    精选各行（位置、商品 ID）与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        settings = session.scalars(select(StoreDesignSetting)).all()
        blocks = session.scalars(select(StoreHomeBlock).order_by(StoreHomeBlock.position)).all()
        featured_stmt = select(StoreFeaturedProduct).order_by(StoreFeaturedProduct.position)
        featured = session.scalars(featured_stmt).all()
        audits = session.scalars(select(AuditEvent.id)).all()
        return (
            [(row.theme, row.accent, row.updated_at) for row in settings],
            [(row.block, row.position, row.is_visible) for row in blocks],
            [(row.position, row.product_id) for row in featured],
            len(audits),
        )

    return _read(engine, read)


def _audits(engine: Engine, action: str = SAVED) -> list[tuple[Any, ...]]:
    """该操作的审计记录：管理员、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        rows = session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
        )
        return [
            (row.admin_account_id, row.target_type, row.target_id, row.old_value, row.new_value)
            for row in rows
        ]

    return _read(engine, read)


def _setting_id(engine: Engine) -> int:
    return _read(engine, lambda session: session.scalars(select(StoreDesignSetting.id)).one())


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
    """A08 是后台页；SHOP-TASK-051 验收「两个接口都经 require_admin（无有效后台会话 401
    admin_session_required）」：没有 cookie、格式不对、库里没有与已撤销的会话都 401，
    数据不变。"""
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
    put = _put(client, token, _body(), csrf="0" * 64)

    _assert_error(get, 401, SESSION_REQUIRED)
    _assert_error(put, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_save_without_a_valid_csrf_token_is_403(
    client: TestClient, engine: Engine, token: str, seeded: None, csrf: str | None
) -> None:
    """SHOP-TASK-051 验收「PUT 须带 X-CSRF-Token」「缺或错 CSRF 403 且数据不变」：缺少、为空或
    不一致都 403 csrf_failed；结构不合格的请求体也先回答 403（403 先于 422）。"""
    before = _snapshot(engine)

    responses = [
        _put(client, token, _body(), csrf=csrf),
        _put(client, token, {"extra": SECRET}, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


def test_save_with_two_csrf_headers_is_403(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「缺或错 CSRF 403 且数据不变」：X-CSRF-Token 给了两个（即使都正确）也
    403，与 require_admin_csrf「恰好一个」相同。"""
    before = _snapshot(engine)
    headers = [
        ("Cookie", f"{COOKIE_NAME}={token}"),
        ("Content-Type", "application/json"),
        ("X-CSRF-Token", _csrf_for(token)),
        ("X-CSRF-Token", _csrf_for(token)),
    ]

    response = client.put(URL, content=json.dumps(_body()).encode(), headers=headers)

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
    """SHOP-TASK-051 验收「只接受 JSON（否则 415）」「检查顺序 413 → 415 → 401」：不是 JSON 时
    有没有会话都 415，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps(_body()).encode()

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
    """SHOP-TASK-051 验收「请求体上限 8 KB 且先于其他校验（超出 413）」：不是 JSON、没有会话时
    也是 413；不回显请求内容，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({**_body(), "theme": SECRET * 1000}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _put_raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 413, {"detail": "request body too large"})
    assert SECRET not in response.text
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「请求体上限 8 KB」：恰好 8 KB（用 JSON 空白补足）的合法请求体
    照常保存。"""
    content = json.dumps(_body()).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    body = _ok(_put_raw(client, token, content, "application/json"))

    assert body["theme"] == "batik"
    assert len(_audits(engine)) == 1


def test_invalid_body_without_a_session_is_401(client: TestClient, seeded: None) -> None:
    """SHOP-TASK-051 验收「检查顺序 413 → 415 → 401 → 403 → 422」：结构不合格的 JSON 请求体
    在没有会话时先回答 401。"""
    _assert_error(_put(client, None, {"extra": SECRET}), 401, SESSION_REQUIRED)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**_body(), "logo": SECRET}, id="extra-field"),
        pytest.param(_without("theme"), id="missing-theme"),
        pytest.param(_without("accent"), id="missing-accent"),
        pytest.param(_without("home_blocks"), id="missing-blocks"),
        pytest.param(_without("featured_product_ids"), id="missing-featured"),
        pytest.param({**_body(), "theme": 7}, id="theme-not-string"),
        pytest.param({**_body(), "accent": 7}, id="accent-not-string"),
        pytest.param(
            _body(blocks=[("hero", True), ("hero", True), ("how", True), ("categories", True)]),
            id="blocks-duplicate",
        ),
        pytest.param(_with_blocks(*DEFAULT_BLOCKS_JSON[:3]), id="blocks-three"),
        pytest.param(_with_blocks(*DEFAULT_BLOCKS_JSON, DEFAULT_BLOCKS_JSON[3]), id="blocks-five"),
        pytest.param(_with_blocks(), id="blocks-empty"),
        pytest.param(
            _with_blocks(*DEFAULT_BLOCKS_JSON[:3], {"block": SECRET, "visible": True}),
            id="blocks-unknown",
        ),
        pytest.param(
            _with_blocks({"block": "hero", "visible": "yes"}, *DEFAULT_BLOCKS_JSON[1:]),
            id="blocks-visible-not-bool",
        ),
        pytest.param(
            _with_blocks({"block": "hero", "visible": True, "x": SECRET}, *DEFAULT_BLOCKS_JSON[1:]),
            id="blocks-extra-field",
        ),
        pytest.param(_body(featured=[1, 2, 3, 4, 5]), id="featured-five"),
        pytest.param(_body(featured=[1, 1]), id="featured-duplicate"),
        pytest.param(_body(featured=[0]), id="featured-zero"),
        pytest.param(_body(featured=[-1]), id="featured-negative"),
        pytest.param(_body(featured=[2**31]), id="featured-too-large"),
        pytest.param({**_body(), "featured_product_ids": ["1"]}, id="featured-string"),
        pytest.param({**_body(), "featured_product_ids": [1.0]}, id="featured-float"),
        pytest.param({**_body(), "featured_product_ids": [True]}, id="featured-bool"),
        pytest.param([], id="not-an-object"),
    ],
)
def test_malformed_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, seeded: None, body: Any
) -> None:
    """SHOP-TASK-051 验收「请求体只有 theme、accent、home_blocks（恰好 4 项，block 恰为
    四个区块的一个排列）与 featured_product_ids（0 到 4 个不重复的正整数），多出字段或结构
    不合格 422（detail 为不回显输入值的错误数组）」；需求「首页由四个区块组成」「最多 4 件」；
    A08「同一商品不重复」。数据不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_put(client, token, body))
    assert _snapshot(engine) == before


def test_invalid_language_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「lang=en|zh|ms（默认 en，不合法时 422 沿用 _language，不回显所给的
    值）」：GET 与 PUT 都是 422，PUT 不写入。"""
    before = _snapshot(engine)

    _assert_plain_errors(_get(client, token, lang=SECRET))
    _assert_plain_errors(_put(client, token, _body(), lang=SECRET))
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_get_returns_the_settings_choices_and_csrf_token(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """A08「之后下架的商品仍留在列表中，前台不显示它」「从已上架商品中挑选」；SHOP-TASK-051 验收
    GET 的字段：主题、主色、按位置排序的区块、按位置排序的精选（已不满足 published() 的也列出、
    published 为假）、choices（全部满足 published() 的商品，按商品 ID 升序）与 csrf_token；
    不写审计。"""
    db.add(StoreDesignSetting(singleton_slot=SINGLETON_SLOT, theme="malam", accent="lime"))
    for block, position, visible in [
        ("categories", 1, True),
        ("hero", 2, False),
        ("featured", 3, True),
        ("how", 4, False),
    ]:
        db.add(StoreHomeBlock(block=block, position=position, is_visible=visible))
    shop = _category(db, "shop")
    closed = _category(db, "closed", active=False)
    lamp = _product(db, "lamp", shop)
    gone = _product(db, "gone", shop, active=False)
    hidden = _product(db, "hidden", closed)
    mug = _product(db, "mug", shop)
    _feature(db, mug, gone, lamp, hidden)
    before = _snapshot(engine)

    body = _ok(_get(client, token))

    assert body == {
        "theme": "malam",
        "accent": "lime",
        "home_blocks": [
            {"block": "categories", "visible": True},
            {"block": "hero", "visible": False},
            {"block": "featured", "visible": True},
            {"block": "how", "visible": False},
        ],
        "featured": [
            {"product_id": mug.id, "slug": "mug", "name": "Name mug", "published": True},
            {"product_id": gone.id, "slug": "gone", "name": "Name gone", "published": False},
            {"product_id": lamp.id, "slug": "lamp", "name": "Name lamp", "published": True},
            {"product_id": hidden.id, "slug": "hidden", "name": "Name hidden", "published": False},
        ],
        "choices": [
            {"product_id": lamp.id, "slug": "lamp", "name": "Name lamp"},
            {"product_id": mug.id, "slug": "mug", "name": "Name mug"},
        ],
        "csrf_token": _csrf_for(token),
    }
    assert list(body) == ["theme", "accent", "home_blocks", "featured", "choices", "csrf_token"]
    assert _snapshot(engine) == before


def test_get_names_follow_the_language(db: Session, client: TestClient, token: str) -> None:
    """SHOP-TASK-051 验收「按语言取的 name」：有中文名的给中文名，没有的回退英文；默认 en。"""
    shop = _category(db, "shop")
    tee = _product(db, "tee", shop, name_zh="T恤")
    mug = _product(db, "mug", shop)
    _feature(db, tee, mug)

    zh = _ok(_get(client, token, lang="zh"))
    default = _ok(_get(client, token))

    assert [item["name"] for item in zh["featured"]] == ["T恤", "Name mug"]
    assert [item["name"] for item in zh["choices"]] == ["T恤", "Name mug"]
    assert [item["name"] for item in default["featured"]] == ["Name tee", "Name mug"]


def test_get_without_a_setting_row_returns_the_defaults(
    client: TestClient, engine: Engine, token: str
) -> None:
    """SHOP-TASK-051 验收「设置行不存在时按 SHOP-TASK-045 的默认值返回」：默认主题 pandan、主色
    null、四个区块按默认顺序全部显示、没有精选；不补写任何行。"""
    body = _ok(_get(client, token))

    assert body == {
        "theme": "pandan",
        "accent": None,
        "home_blocks": DEFAULT_BLOCKS_JSON,
        "featured": [],
        "choices": [],
        "csrf_token": _csrf_for(token),
    }
    assert _snapshot(engine) == ([], [], [], 0)


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def test_save_writes_everything_and_another_session_reads_it(
    db: Session, client: TestClient, engine: Engine, account_id: int, token: str, seeded: None
) -> None:
    """需求「管理员可调整它们的顺序与显示或隐藏」「挑选最多 4 件已上架商品并排定顺序」「装修保存
    后对所有访客生效」「修改记入后台审计记录」：保存后另一个会话读到新设置，公开接口 GET
    /api/store-design 随之改变，写一条审计（旧值与新值为无空格的 JSON 数组）。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    mug = _product(db, "mug", shop)
    _product(db, "tee", shop)
    public_before = _ok(client.get(PUBLIC_URL))

    body = _ok(_put(client, token, _body(featured=[mug.id, lamp.id])))

    assert body == {
        "theme": "batik",
        "accent": "sogan",
        "home_blocks": [
            {"block": "featured", "visible": True},
            {"block": "hero", "visible": False},
            {"block": "how", "visible": True},
            {"block": "categories", "visible": True},
        ],
        "featured": [
            {"product_id": mug.id, "slug": "mug", "name": "Name mug", "published": True},
            {"product_id": lamp.id, "slug": "lamp", "name": "Name lamp", "published": True},
        ],
    }
    settings, blocks, featured, _ = _snapshot(engine)
    assert [(theme, accent) for theme, accent, _ in settings] == [("batik", "sogan")]
    assert blocks == [
        ("featured", 1, True),
        ("hero", 2, False),
        ("how", 3, True),
        ("categories", 4, True),
    ]
    assert featured == [(1, mug.id), (2, lamp.id)]
    assert _audits(engine) == [
        (
            account_id,
            "store_design",
            _setting_id(engine),
            '["pandan",null,"H1W1C1F1",[]]',
            f'["batik","sogan","F1H0W1C1",[{mug.id},{lamp.id}]]',
        )
    ]
    assert public_before["theme"] == "pandan"
    assert _ok(client.get(PUBLIC_URL)) == {
        "theme": "batik",
        "accent": "sogan",
        "home_blocks": body["home_blocks"],
        "featured_slugs": ["mug", "lamp"],
    }
    assert _ok(_get(client, token))["featured"] == body["featured"]


def test_save_updates_the_timestamp(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「改写主题、主色与更新时间」：保存后更新时间晚于保存前。"""
    old = datetime(2026, 1, 1)
    db.execute(update(StoreDesignSetting).values(updated_at=old))
    db.commit()

    _ok(_put(client, token, _body(theme="litar", accent=None)))

    settings = _snapshot(engine)[0]
    assert settings[0][:2] == ("litar", None)
    assert settings[0][2] > old


def test_save_reorders_and_swaps_blocks_and_featured(
    db: Session, client: TestClient, engine: Engine, account_id: int, token: str, seeded: None
) -> None:
    """需求「调整它们的顺序」「排定顺序」；A08 (↑)(↓) 调整顺序：两件精选交换位置、区块整体倒序
    （区块、位置与商品的唯一约束在逐行就地改时都会冲突）都保存成功，各写一条审计。"""
    shop = _category(db, "shop")
    first = _product(db, "first", shop)
    second = _product(db, "second", shop)
    original = [(block, True) for block in HOME_BLOCKS]
    _ok(_put(client, token, _body(blocks=original, featured=[first.id, second.id])))

    reversed_blocks = [(block, block != "how") for block in reversed(HOME_BLOCKS)]
    body = _ok(_put(client, token, _body(blocks=reversed_blocks, featured=[second.id, first.id])))

    assert [item["product_id"] for item in body["featured"]] == [second.id, first.id]
    _, blocks, featured, _ = _snapshot(engine)
    assert blocks == [
        ("featured", 1, True),
        ("categories", 2, True),
        ("how", 3, False),
        ("hero", 4, True),
    ]
    assert featured == [(1, second.id), (2, first.id)]
    audits = _audits(engine)
    assert len(audits) == 2
    assert audits[1][3:] == (
        f'["batik","sogan","H1W1C1F1",[{first.id},{second.id}]]',
        f'["batik","sogan","F1C1W0H1",[{second.id},{first.id}]]',
    )


def test_save_can_clear_the_featured_products(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """A08「(✗) 移出精选」；SHOP-TASK-051 验收「0 到 4 个」：空数组清空精选各行。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    _feature(db, lamp)

    body = _ok(_put(client, token, _body(featured=[])))

    assert body["featured"] == []
    assert _snapshot(engine)[2] == []


def test_save_accepts_four_featured_products(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """需求「挑选最多 4 件」：恰好 4 件可以保存，按数组顺序排定位置。"""
    shop = _category(db, "shop")
    ids = [_product(db, f"p-{n}", shop).id for n in range(4)]
    picked = [ids[2], ids[0], ids[3], ids[1]]

    _ok(_put(client, token, _body(featured=picked)))

    assert _snapshot(engine)[2] == [(1, ids[2]), (2, ids[0]), (3, ids[3]), (4, ids[1])]


def test_identical_save_is_200_without_audit(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「与请求完全相同时返回 200 与当前设置，不写入、不写审计」：
    第二次提交同样的设置时更新时间与审计记录数都不变。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    first = _ok(_put(client, token, _body(featured=[lamp.id])))
    before = _snapshot(engine)

    second = _ok(_put(client, token, _body(featured=[lamp.id])))

    assert second == first
    assert _snapshot(engine) == before
    assert len(_audits(engine)) == 1


def test_save_of_the_migration_defaults_is_identical(
    client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「与请求完全相同时……不写入、不写审计」：提交与迁移默认值相同的设置
    返回当前设置，库里不变。"""
    before = _snapshot(engine)
    blocks = [(block, True) for block in HOME_BLOCKS]

    body = _ok(_put(client, token, _body(theme="pandan", accent=None, blocks=blocks)))

    assert body == {
        "theme": "pandan",
        "accent": None,
        "home_blocks": DEFAULT_BLOCKS_JSON,
        "featured": [],
    }
    assert _snapshot(engine) == before


def test_save_without_a_setting_row_inserts_it(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """SHOP-TASK-051 验收「单例行不存在时……在保存点里按默认值插入……然后再锁定」：三张表都空时
    保存写出设置行、四个区块与审计；旧值为默认值（区块表原为空，按读取规则补齐）。"""
    body = _ok(_put(client, token, _body(theme="gula", accent="mint")))

    settings, blocks, featured, _ = _snapshot(engine)
    assert [(theme, accent) for theme, accent, _ in settings] == [("gula", "mint")]
    assert [block for block, _, _ in blocks] == ["featured", "hero", "how", "categories"]
    assert featured == []
    assert body["theme"] == "gula"
    assert _audits(engine) == [
        (
            account_id,
            "store_design",
            _setting_id(engine),
            '["pandan",null,"H1W1C1F1",[]]',
            '["gula","mint","F1H0W1C1",[]]',
        )
    ]


@pytest.mark.parametrize(("theme", "accent"), [*NULL_ACCENTS, *ALL_ACCENTS])
def test_every_theme_and_its_accents_are_accepted(
    client: TestClient, engine: Engine, token: str, seeded: None, theme: str, accent: str | None
) -> None:
    """需求「从 10 款预设主题中选一款」「管理员只能从中选一个」：每款主题配空主色或它自己的任一
    主色都能保存。"""
    body = _ok(_put(client, token, _body(theme=theme, accent=accent)))

    assert (body["theme"], body["accent"]) == (theme, accent)
    assert _snapshot(engine)[0][0][:2] == (theme, accent)


@pytest.mark.parametrize(
    ("theme", "accent", "code"),
    [
        pytest.param("unknown", None, "theme_invalid", id="theme-unknown"),
        pytest.param("Pandan", None, "theme_invalid", id="theme-case"),
        pytest.param("", None, "theme_invalid", id="theme-empty"),
        pytest.param("unknown", "ink", "theme_invalid", id="theme-checked-first"),
        pytest.param("pandan", "ink", "accent_invalid", id="accent-of-another-theme"),
        pytest.param("pandan", "#00ff00", "accent_invalid", id="accent-arbitrary-colour"),
        pytest.param("pandan", "", "accent_invalid", id="accent-empty"),
    ],
)
def test_invalid_theme_or_accent_is_422(
    client: TestClient,
    engine: Engine,
    token: str,
    seeded: None,
    theme: str,
    accent: str | None,
    code: str,
) -> None:
    """需求「从 10 款预设主题中选一款」「每款主题提供若干预先校过对比度的主色，管理员只能从中选
    一个，不能输入任意颜色」：主题不在 THEMES 为 theme_invalid；主色不属于所选主题（含属于别的
    主题的主色）为 accent_invalid；数据不变。"""
    before = _snapshot(engine)

    _assert_error(_put(client, token, _body(theme=theme, accent=accent)), 422, {"detail": code})
    assert _snapshot(engine) == before


def test_theme_is_checked_against_themes(monkeypatch: pytest.MonkeyPatch) -> None:
    """SHOP-TASK-051 验收「主题与主色以 THEMES 与 THEME_ACCENTS 为准」「主题不在 THEMES 为
    theme_invalid」：主题以 THEMES 判定，不以 THEME_ACCENTS 的键代替；不在 THEMES 的主题即使
    THEME_ACCENTS 里有它也是 theme_invalid，主色只在所选主题的 THEME_ACCENTS 里找。"""
    monkeypatch.setattr(store_design, "THEMES", ("pandan",))

    with pytest.raises(StoreDesignInvalid) as excinfo:
        check_theme_and_accent("batik", None)
    assert excinfo.value.code == "theme_invalid"
    with pytest.raises(StoreDesignInvalid) as excinfo:
        check_theme_and_accent("batik", "sogan")
    assert excinfo.value.code == "theme_invalid"
    check_theme_and_accent("pandan", None)
    check_theme_and_accent("pandan", THEME_ACCENTS["pandan"][0])
    with pytest.raises(StoreDesignInvalid) as excinfo:
        check_theme_and_accent("pandan", "sogan")
    assert excinfo.value.code == "accent_invalid"


@pytest.mark.parametrize("which", ["missing", "inactive", "closed-category", "no-description"])
def test_unavailable_featured_product_is_422(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None, which: str
) -> None:
    """需求「精选商品由管理员挑选最多 4 件已上架商品」；Kelvin 2026-10-06 复用目录的 published()
    判定：任一精选商品不存在、已下架、所属分类停用或资料不全时 422 featured_unavailable，主题与
    其余合法的商品也不写入，数据不变。"""
    shop = _category(db, "shop")
    good = _product(db, "good", shop)
    if which == "missing":
        bad_id = good.id + 100
    elif which == "inactive":
        bad_id = _product(db, "bad", shop, active=False).id
    elif which == "closed-category":
        bad_id = _product(db, "bad", _category(db, "closed", active=False)).id
    else:
        bad = _product(db, "bad", shop)
        bad.description_en = " "
        db.commit()
        bad_id = bad.id
    before = _snapshot(engine)

    response = _put(client, token, _body(theme="litar", accent="red", featured=[good.id, bad_id]))

    _assert_error(response, 422, {"detail": "featured_unavailable"})
    assert _snapshot(engine) == before


def test_keeping_a_delisted_featured_product_is_allowed(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """A08「之后下架的商品仍留在列表中，前台不显示它」；Kelvin 2026-10-08「精选里原已挑选、之后
    才下架的商品，保存时放行」（取代 SHOP-TASK-051 的「任一精选商品此刻不满足 published() 即
    featured_unavailable」，所以原先的 422 改为放行、测试改名）：精选之后下架的商品在 GET 里
    published 为假；原样再提交为 200 且库里与审计都不变；只改主题时 200，它留在原位置，审计新值
    含它的 ID；调整它的位置也能保存；前台仍不显示它。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    mug = _product(db, "mug", shop)
    _ok(_put(client, token, _body(featured=[lamp.id, mug.id])))
    db.execute(update(Product).where(Product.id == lamp.id).values(is_active=False))
    db.commit()
    before = _snapshot(engine)

    listed = _ok(_get(client, token))["featured"]
    same = _ok(_put(client, token, _body(featured=[lamp.id, mug.id])))

    assert [(item["product_id"], item["published"]) for item in listed] == [
        (lamp.id, False),
        (mug.id, True),
    ]
    assert same["featured"] == listed
    assert _snapshot(engine) == before
    assert len(_audits(engine)) == 1

    kept = [lamp.id, mug.id]
    themed = _ok(_put(client, token, _body(theme="litar", accent=None, featured=kept)))

    assert themed["theme"] == "litar"
    assert themed["featured"] == listed
    assert _snapshot(engine)[2] == [(1, lamp.id), (2, mug.id)]
    audits = _audits(engine)
    assert len(audits) == 2
    assert audits[1][4] == f'["litar",null,"F1H0W1C1",[{lamp.id},{mug.id}]]'

    _ok(_put(client, token, _body(theme="litar", accent=None, featured=[mug.id, lamp.id])))

    assert _snapshot(engine)[2] == [(1, mug.id), (2, lamp.id)]
    assert _audits(engine)[2][4] == f'["litar",null,"F1H0W1C1",[{mug.id},{lamp.id}]]'
    assert _ok(client.get(PUBLIC_URL))["featured_slugs"] == ["mug"]


def test_re_adding_a_removed_delisted_product_is_422(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """Kelvin 2026-10-08「只有新加入的商品须此刻上架」；A08「(✗) 移出精选」：放行只看保存前的
    当前精选，已下架的商品移出并保存后再加回即为新加入，422 featured_unavailable，数据不变。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    mug = _product(db, "mug", shop)
    _ok(_put(client, token, _body(featured=[lamp.id, mug.id])))
    db.execute(update(Product).where(Product.id == lamp.id).values(is_active=False))
    db.commit()
    _ok(_put(client, token, _body(featured=[mug.id])))
    before = _snapshot(engine)

    response = _put(client, token, _body(featured=[mug.id, lamp.id]))

    _assert_error(response, 422, {"detail": "featured_unavailable"})
    assert _snapshot(engine) == before


def test_save_response_names_follow_the_language(
    db: Session, client: TestClient, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「返回 200 与保存后的设置（字段同 GET，不含 choices 与 csrf_token）」：
    PUT 返回的精选名称同样按 lang 取，默认 en。"""
    shop = _category(db, "shop")
    tee = _product(db, "tee", shop, name_zh="T恤")

    body = _ok(_put(client, token, _body(featured=[tee.id]), lang="zh"))

    assert body["featured"] == [
        {"product_id": tee.id, "slug": "tee", "name": "T恤", "published": True}
    ]
    assert list(body) == ["theme", "accent", "home_blocks", "featured"]


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


def test_audit_value_fits_with_the_longest_valid_settings(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """需求「修改记入后台审计记录」；SHOP-TASK-051 验收「取最长合法值时也不超过 255 个字符，
    且不含商品名称」：最长的主题与主色组合、四件 ID 最大的精选商品时，旧值与新值都不超过
    审计列长、无空格，且不含商品名称或 slug。"""
    theme, accent = max(ALL_ACCENTS, key=lambda pair: len(pair[0]) + len(pair[1]))
    shop = _category(db, "shop")
    top = 2**31 - 1
    products = [_product(db, f"long-{n}", shop, product_id=top - n) for n in range(4)]
    ids = [product.id for product in products]

    _ok(_put(client, token, _body(theme=theme, accent=accent, featured=ids)))
    _ok(_put(client, token, _body(theme="pandan", accent=None, featured=list(reversed(ids)))))

    audits = _audits(engine)
    assert len(audits) == 2
    expected = json.dumps([theme, accent, "F1H0W1C1", ids], separators=(",", ":"))
    assert audits[0][4] == expected
    assert audits[1][3] == expected
    for _, _, _, old_value, new_value in audits:
        for value in (old_value, new_value):
            assert len(value) <= AUDIT_VALUE_MAX_LENGTH
            assert " " not in value
            assert "Name" not in value
            assert "long-" not in value


def test_failed_saves_write_no_audit(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「任一校验不通过时不写入任何东西」：各种失败的保存都不写审计。"""
    shop = _category(db, "shop")
    gone = _product(db, "gone", shop, active=False)

    _put(client, token, _body(theme="nope"))
    _put(client, token, _body(accent="nope"))
    _put(client, token, _body(featured=[gone.id]))
    _put(client, token, _body(), csrf=None)
    _put(client, token, {"extra": 1})

    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 锁定、缓存与日志
# ---------------------------------------------------------------------------


def test_lock_statement_is_select_for_update_on_the_singleton() -> None:
    """SHOP-TASK-051 验收「以 SELECT … FOR UPDATE 锁定装修设置单例行」「用 MySQL 方言编译锁定
    查询含 FOR UPDATE」。"""
    sql = str(lock_setting_statement().compile(dialect=mysql.dialect()))

    assert "FROM store_design_settings" in sql
    assert "WHERE store_design_settings.singleton_slot = " in sql
    assert sql.rstrip().endswith("FOR UPDATE")


def test_save_locks_the_singleton_before_reading(
    db: Session, client: TestClient, engine: Engine, token: str, seeded: None
) -> None:
    """SHOP-TASK-051 验收「先以 SELECT … FOR UPDATE 锁定装修设置单例行……锁定后读取当前设置」：
    保存时锁定查询先于读取区块、精选与商品的查询。"""
    shop = _category(db, "shop")
    lamp = _product(db, "lamp", shop)
    # 提交后 lamp 已过期，取 lamp.id 会查 products：在开始记录之前取出，只记录接口发的查询。
    body = _body(featured=[lamp.id])
    tables = ("store_home_blocks", "store_featured_products", "products")
    seen: list[str] = []

    def _record(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        # 只编译 SELECT：SQLite 上 ORM 的 INSERT 可能带 RETURNING，MySQL 方言编译不了。
        if isinstance(clauseelement, Select):
            sql = str(clauseelement.compile(dialect=mysql.dialect()))
            if "FOR UPDATE" in sql:
                seen.append("lock")
            elif any(table in sql for table in tables):
                seen.append("read")

    event.listen(engine, "before_execute", _record)
    try:
        _ok(_put(client, token, body))
    finally:
        event.remove(engine, "before_execute", _record)

    assert seen[0] == "lock"
    assert seen.count("lock") == 1
    assert "read" in seen


def test_all_responses_are_no_store_and_nothing_is_logged(
    db: Session,
    client: TestClient,
    token: str,
    seeded: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SHOP-TASK-051 验收「两个接口的处理函数与依赖产生的响应（含错误）都带 Cache-Control 为
    no-store」「不写日志」：成功、相同设置、401、403、415、422 与业务 422 都带 no-store，应用
    不记日志。"""
    caplog.set_level(logging.DEBUG)
    responses = [
        _get(client, token),
        _get(client, None),
        _get(client, token, lang="xx"),
        _put(client, token, _body()),
        _put(client, token, _body()),
        _put(client, None, _body()),
        _put(client, token, _body(), csrf=None),
        _put(client, token, {"extra": 1}),
        _put(client, token, _body(theme="nope")),
        _put_raw(client, token, b"{}", "text/plain"),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 401, 422, 200, 200, 401, 403, 422, 422, 415]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_only_get_and_put(app: FastAPI) -> None:
    """SHOP-TASK-051 验收「两个接口」；Kelvin 2026-10-06 首版不含标志图：该路径下只有 GET
    与 PUT，没有标志图上传的接口。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("GET", URL), ("PUT", URL)}

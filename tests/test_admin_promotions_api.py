"""后台促销活动的列表、读取、新建、保存与状态操作接口（app/api/admin_promotions.py，
SHOP-TASK-242；规则见 SHOP-TASK-239、249 与 241 的 app/services/admin_promotions.py）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条（下称「第 N 条」）、
docs/UX.md 0.20 的 A12（下称「A12」）与 SHOP-TASK-242 的落实写法与验收（下称「落实写法」
「验收」）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

夹具照 tests/test_admin_categories_api.py 与 tests/test_promotion_poster_api.py：SQLite 内存库按
模型建表（StaticPool、开外键），写入发布锁那一行、管理员、两个分类与三件上架商品；https 的
base_url，以 issue_admin_session 登录，期望的 CSRF 令牌在这里另算。时间固定为 NOW
（monkeypatch 本接口模块与 app.api.admin_auth 的 _utcnow）。本接口模块引用的九个服务层函数
一律包一层记录调用（calls），用来判断服务层是否被调用；映射异常与请求体上限的用例再以替身
代替其中一个。检查结果在另一个数据库会话里只查列值读出，读得到即说明接口已提交。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_promotions as promotions_api
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
    Promotion,
    PromotionPublishLock,
    PromotionRule,
)
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionChanged,
    PromotionDetail,
    PromotionEnded,
    PromotionInput,
    PromotionInvalid,
    PromotionNotFound,
    PromotionOverlap,
    PromotionProblem,
    PromotionStateInvalid,
    RuleInput,
    TierInput,
)

URL = "/api/admin/promotions"
MAX_BODY_BYTES = 64 * 1024
# app/api/pay.py 的上限：最多规则的请求体须超过它，才说明 64 KB 的上限有必要。
DEFAULT_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。
NOW = datetime(2026, 11, 13, 4, 0)
SECRET = "SECRETVALUE"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
CHANGED = {"detail": "promotion_changed"}
NOT_FOUND = {"detail": "promotion_not_found"}
ENDED = {"detail": "promotion_ended"}
TOO_LARGE = {"detail": "request body too large"}
NOT_JSON = {"detail": "request body must be JSON"}

# 马来西亚本地时间的活动时段：相对 NOW 未开始与进行中。
SCHEDULED = ("2026-12-01T00:00", "2026-12-10T00:00")
RUNNING = ("2026-11-10T00:00", "2026-11-20T00:00")

SERVICE_NAMES = (
    "list_promotions",
    "read_promotion",
    "create_promotion",
    "save_promotion",
    "publish_promotion",
    "withdraw_promotion",
    "end_promotion",
    "copy_promotion",
    "delete_promotion",
)

SUMMARY_FIELDS = ["id", "name", "state", "starts_at", "ends_at", "rule_count", "updated_at"]
DETAIL_FIELDS = [
    "id",
    "version",
    "state",
    "ended_early_at",
    "name",
    "starts_at",
    "ends_at",
    "coupon_combinable",
    "points_combinable",
    "rules",
    "decoration",
    "copy",
    "poster_desktop_url",
    "poster_phone_url",
]
RULE_FIELDS = [
    "rule_id",
    "kind",
    "discount_mode",
    "percent",
    "amount_sen",
    "buy_quantity",
    "get_quantity",
    "get_percent",
    "threshold_sen",
    "product_ids",
    "category_ids",
    "tiers",
    "products",
]
COPY_FIELDS = [
    f"{name}_{lang}"
    for name in ("title", "subtitle", "badge", "strip", "poster_alt")
    for lang in ("en", "zh", "ms")
]

DECORATION = {
    "pack": "raya",
    "palette": "theme",
    "ambient": "none",
    "home_poster": "split",
    "list_strip": "band",
    "card_badge": "tag",
    "detail_ribbon": "ribbon",
    "cart_progress": "bar",
    "poster_mode": "template",
}

# 种子商品：pan、pot 在分类 1，rake 在分类 2，都上架。
PAN = {"id": 1, "slug": "pan", "name_en": "Pan"}
POT = {"id": 2, "slug": "pot", "name_en": "Pot"}

# 四类规则各一条：商品级打折选两件商品，买 X 享 Y 选分类 2，满额减两档，满额免运费。
RULES_IN: list[dict[str, Any]] = [
    {"kind": "item_discount", "discount_mode": "percent", "percent": 20, "product_ids": [2, 1]},
    {
        "kind": "buy_x_get_y",
        "buy_quantity": 2,
        "get_quantity": 1,
        "get_percent": 50,
        "category_ids": [2],
    },
    {
        "kind": "spend_discount",
        "discount_mode": "amount",
        "tiers": [
            {"threshold_sen": 10000, "amount_sen": 1000, "percent": None},
            {"threshold_sen": 20000, "amount_sen": 2500, "percent": None},
        ],
    },
    {"kind": "free_shipping", "threshold_sen": 5000},
]
_NO_PARAMS = {
    "discount_mode": None,
    "percent": None,
    "amount_sen": None,
    "buy_quantity": None,
    "get_quantity": None,
    "get_percent": None,
    "threshold_sen": None,
    "product_ids": [],
    "category_ids": [],
    "tiers": [],
    "products": [],
}


def _rule_out(kind: str, **params: Any) -> dict[str, Any]:
    """读出的一条规则（不含 rule_id）：没给的参数为 null，范围、满额档与 products 为空。"""
    return {"kind": kind} | _NO_PARAMS | params


# 读出的四条规则：商品与分类 ID 从小到大，products 与 product_ids 一一对应。
RULES_OUT: list[dict[str, Any]] = [
    _rule_out(
        "item_discount",
        discount_mode="percent",
        percent=20,
        product_ids=[1, 2],
        products=[PAN, POT],
    ),
    _rule_out(
        "buy_x_get_y",
        buy_quantity=2,
        get_quantity=1,
        get_percent=50,
        category_ids=[2],
    ),
    _rule_out("spend_discount", discount_mode="amount", tiers=RULES_IN[2]["tiers"]),
    _rule_out("free_shipping", threshold_sen=5000),
]
COPY_OUT = {name: None for name in COPY_FIELDS} | {"title_en": "Raya Sale", "title_zh": "开斋特卖"}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _product(db: Session, category_id: int, slug: str) -> None:
    """一件满足发布规则的商品（带一个启用的 SKU）。"""
    product = Product(
        category_id=category_id,
        slug=slug,
        name_en=slug.title(),
        description_en=f"A {slug}.",
        is_active=True,
        max_per_order=10,
    )
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

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(PromotionPublishLock(singleton_slot=1))
        seed.add(
            AdminAccount(
                username="admin@example.com",
                password_hash="not-a-real-hash",
                created_at=NOW - timedelta(days=1),
                password_updated_at=NOW - timedelta(days=1),
            )
        )
        for slug, name in (("kitchen", "Kitchen"), ("garden", "Garden")):
            seed.add(Category(slug=slug, name_en=name, is_active=True, version=1))
        seed.flush()
        _product(seed, 1, "pan")
        _product(seed, 1, "pot")
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
    found = db.scalar(select(AdminAccount.id))
    assert found is not None
    return found


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(promotions_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)


@pytest.fixture(autouse=True)
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """本接口模块引用的服务层函数各包一层，按调用顺序记下函数名，再调用原函数。"""
    seen: list[str] = []
    for name in SERVICE_NAMES:
        original = getattr(promotions_api, name)

        def wrapper(*args: Any, _name: str = name, _original: Any = original, **kwargs: Any) -> Any:
            seen.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(promotions_api, name, wrapper)
    return seen


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
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


@pytest.fixture
def draft(client: TestClient, token: str, calls: list[str]) -> dict[str, Any]:
    """经接口新建的一个草稿（未开始的时段、四条规则），返回新建的响应；清空调用记录。"""
    created = _ok(_send(client, "POST", URL, token, _new()), 201)
    calls.clear()
    return created


# ---------------------------------------------------------------------------
# 请求与读取
# ---------------------------------------------------------------------------


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = "auto") -> dict[str, str]:
    """csrf 为 "auto" 时按 token 算出正确的令牌；给 None 即不带请求头。"""
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _get(client: TestClient, token: str | None, path: str = URL) -> Any:
    return client.get(path, headers=_headers(token, None))


def _send(
    client: TestClient,
    method: str,
    path: str,
    token: str | None,
    body: Any = None,
    *,
    csrf: str | None = "auto",
) -> Any:
    """body 为 None 时不带请求体（复制）。"""
    extra = {} if body is None else {"json": body}
    return client.request(method, path, headers=_headers(token, csrf), **extra)


def _raw(
    client: TestClient,
    method: str,
    path: str,
    token: str | None,
    content: bytes,
    content_type: str | None,
) -> Any:
    headers = _headers(token)
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.request(method, path, content=content, headers=headers)


def _item(promotion_id: int | str, action: str = "") -> str:
    return f"{URL}/{promotion_id}" + (f"/{action}" if action else "")


def _new(**fields: Any) -> dict[str, Any]:
    """一个合格的新建请求体（未开始的时段、四类规则各一条）；fields 覆盖默认值。"""
    body = {
        "name": "Raya",
        "starts_at": SCHEDULED[0],
        "ends_at": SCHEDULED[1],
        "coupon_combinable": False,
        "points_combinable": True,
        "rules": json.loads(json.dumps(RULES_IN)),
        "decoration": dict(DECORATION),
        "copy": {"title_en": " Raya Sale ", "title_zh": "开斋特卖", "badge_en": ""},
    }
    return body | fields


def _edit(detail: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """把读到的活动原样列成保存请求（规则去掉 rule_id 与 products）；fields 覆盖。"""
    body = {
        "version": detail["version"],
        "name": detail["name"],
        "starts_at": detail["starts_at"],
        "ends_at": detail["ends_at"],
        "coupon_combinable": detail["coupon_combinable"],
        "points_combinable": detail["points_combinable"],
        "rules": [
            {key: value for key, value in rule.items() if key not in ("rule_id", "products")}
            for rule in detail["rules"]
        ],
        "decoration": detail["decoration"],
        "copy": detail["copy"],
    }
    return body | fields


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """全部活动的各列（只查列值，按 ID）、规则条数与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        stmt = select(
            Promotion.id,
            Promotion.version,
            Promotion.status,
            Promotion.name,
            Promotion.starts_at,
            Promotion.ends_at,
            Promotion.ended_early_at,
            Promotion.updated_at,
        ).order_by(Promotion.id)
        rows = [tuple(row) for row in session.execute(stmt)]
        rules = len(session.scalars(select(PromotionRule.id)).all())
        return rows, rules, len(session.scalars(select(AuditEvent.id)).all())

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


def _assert_detail(body: dict[str, Any]) -> None:
    """PromotionDetail 的字段与次序，以及每条规则的字段与次序（rule_id 为整数）。"""
    assert list(body) == DETAIL_FIELDS
    assert list(body["decoration"]) == list(DECORATION)
    assert list(body["copy"]) == COPY_FIELDS
    for rule in body["rules"]:
        assert list(rule) == RULE_FIELDS
        assert type(rule["rule_id"]) is int


def _without_rule_ids(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in rule.items() if key != "rule_id"} for rule in rules]


def _expected_detail(promotion_id: int, **fields: Any) -> dict[str, Any]:
    """_new() 建成的草稿读出来的样子（不含规则 ID）；fields 覆盖。"""
    detail = {
        "id": promotion_id,
        "version": 1,
        "state": "draft",
        "ended_early_at": None,
        "name": "Raya",
        "starts_at": SCHEDULED[0],
        "ends_at": SCHEDULED[1],
        "coupon_combinable": False,
        "points_combinable": True,
        "rules": RULES_OUT,
        "decoration": DECORATION,
        "copy": COPY_OUT,
        "poster_desktop_url": None,
        "poster_phone_url": None,
    }
    return detail | fields


def _comparable(body: dict[str, Any]) -> dict[str, Any]:
    return body | {"rules": _without_rule_ids(body["rules"])}


def _stub_detail(promotion_id: int = 7) -> PromotionDetail:
    """替身服务层返回的一个最小的活动。"""
    return PromotionDetail(
        id=promotion_id,
        version=1,
        state="draft",
        ended_early_at=None,
        name="Stub",
        starts_at=None,
        ends_at=None,
        coupon_combinable=False,
        points_combinable=False,
        rules=(),
        decoration=DecorationInput(**DECORATION),
        copy=CopyInput(),
        poster_desktop_url=None,
        poster_phone_url=None,
    )


# ---------------------------------------------------------------------------
# 会话、CSRF 与请求格式（401、403、413、415、422 的顺序，且不调用服务层）
# ---------------------------------------------------------------------------


def _all_requests(
    client: TestClient,
    token: str | None,
    promotion_id: int | str,
    *,
    csrf: str | None,
    writes_only: bool = False,
) -> list[Any]:
    """九个路由各一次（带请求体的写操作都带合格的请求体）；writes_only 时不发两个读取。"""
    version = {"version": 1}
    save_body = _edit(_new() | {"version": 1})
    reads = [] if writes_only else [_get(client, token), _get(client, token, _item(promotion_id))]
    return [
        *reads,
        _send(client, "POST", URL, token, _new(), csrf=csrf),
        _send(client, "PUT", _item(promotion_id), token, save_body, csrf=csrf),
        _send(client, "POST", _item(promotion_id, "publish"), token, version, csrf=csrf),
        _send(client, "POST", _item(promotion_id, "withdraw"), token, version, csrf=csrf),
        _send(client, "POST", _item(promotion_id, "end"), token, version, csrf=csrf),
        _send(client, "POST", _item(promotion_id, "copy"), token, csrf=csrf),
        _send(client, "DELETE", _item(promotion_id), token, version, csrf=csrf),
    ]


@pytest.mark.parametrize("session_state", ["missing", "malformed", "unknown", "revoked"])
def test_all_endpoints_require_an_admin_session(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    draft: dict[str, Any],
    calls: list[str],
    session_state: str,
) -> None:
    """验收「没有会话 401 admin_session_required」；第 2 条只有管理员在后台建立与操作活动：
    没有 cookie、格式不对、库里没有与已撤销的会话，全部接口都 401（路径 ID 不合法、请求体
    不合格也先 401），不调用服务层，数据与审计不变。"""
    token: str | None
    if session_state == "missing":
        token = None
    elif session_state == "malformed":
        token = "not-a-token"
    elif session_state == "unknown":
        token = "A" * 43
    else:
        issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=5))
        revoke_admin_session(db, issued.session, NOW - timedelta(minutes=1))
        db.commit()
        token = issued.token
    before = _snapshot(engine)

    responses = _all_requests(client, token, draft["id"], csrf="0" * 64)
    responses += _all_requests(client, token, SECRET, csrf="0" * 64)
    responses.append(_send(client, "POST", URL, token, {"name": SECRET}, csrf="0" * 64))

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert calls == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_writes_without_a_valid_csrf_token_are_403(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    csrf: str | None,
) -> None:
    """验收「写操作都要 CSRF（403 csrf_failed）」；落实写法「照 admin_categories 的
    CsrfDep」（先 CSRF、再路径 ID 与请求体）：X-CSRF-Token 缺少、为空或不一致时，新建、保存、
    发布、撤回、提前结束、复制与删除都 403，路径 ID 不合法与请求体不合格的也先 403；不调用
    服务层，数据与审计不变。"""
    before = _snapshot(engine)

    responses = _all_requests(client, token, draft["id"], csrf=csrf, writes_only=True)
    responses += _all_requests(client, token, "0", csrf=csrf, writes_only=True)
    for method, path in WRITE_PATHS_WITH_BODY:
        responses.append(_send(client, method, path, token, {"name": SECRET}, csrf=csrf))

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert calls == []
    assert _snapshot(engine) == before


WRITE_PATHS_WITH_BODY = [
    ("POST", URL),
    ("PUT", _item(1)),
    ("POST", _item(1, "publish")),
    ("POST", _item(1, "withdraw")),
    ("POST", _item(1, "end")),
    ("DELETE", _item(1)),
    ("PUT", _item(0)),
    ("POST", _item(SECRET, "publish")),
]
WRITE_PARAMS = [
    pytest.param(method, path, id=f"{method}-{path.removeprefix(URL) or '/'}")
    for method, path in WRITE_PATHS_WITH_BODY
]


@pytest.mark.parametrize(("method", "path"), WRITE_PARAMS)
@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_oversized_body_is_413_first(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    method: str,
    path: str,
    content_type: str,
    logged_in: bool,
) -> None:
    """落实写法「WriteBodyDep 读请求体但上限另设为 64 KB」；验收「请求体超过 64 KB 为 413」：
    超过 64 KB 时不是 JSON、没有会话、路径 ID 不合法也是 413；不回显请求内容，不调用服务层，
    数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({"name": SECRET * (MAX_BODY_BYTES // len(SECRET))}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _raw(client, method, path, token if logged_in else None, content, content_type)

    _assert_error(response, 413, TOO_LARGE)
    assert SECRET not in response.text
    assert calls == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(("method", "path"), WRITE_PARAMS)
@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("logged_in", [True, False])
def test_non_json_is_415(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    method: str,
    path: str,
    content_type: str | None,
    logged_in: bool,
) -> None:
    """验收「不是 JSON 为 415」（照 admin_categories 的 WriteBodyDep，先于会话与 CSRF）：
    不是 JSON 时有没有会话、路径 ID 合不合法都 415，不调用服务层，数据不变。"""
    content = json.dumps(_new()).encode()
    before = _snapshot(engine)

    response = _raw(client, method, path, token if logged_in else None, content, content_type)

    _assert_error(response, 415, NOT_JSON)
    assert calls == []
    assert _snapshot(engine) == before


def test_body_at_the_size_limit_is_accepted(client: TestClient, token: str) -> None:
    """落实写法「上限另设为 64 KB」：恰好 64 KB（用 JSON 空白补足）的合法新建请求照常处理，
    回 201。"""
    content = json.dumps(_new()).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    _ok(_raw(client, "POST", URL, token, content, "application/json"), 201)


def test_the_largest_rule_set_fits_in_the_body_limit(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落实写法「规则可带 20 × 230 个商品与分类 ID，8 KB 不够」；第 3 条「最多 20 条商品级
    规则」「各至多 200 件、30 个」；第 12 条文案的长度上限：20 条商品级规则各带 200 件商品与
    30 个分类 ID（都取 10 位数的 ID）、文案各项写满（中文按 UTF-8 发送），紧凑 JSON 超过 8 KB
    而不超过 64 KB，通过请求体大小与结构校验，原样传给服务层（以替身代替，不建这么多数据）。"""
    received: list[PromotionInput] = []

    def stub(db: Session, admin_account_id: int, data: PromotionInput, now: datetime) -> Any:
        received.append(data)
        return _stub_detail()

    monkeypatch.setattr(promotions_api, "create_promotion", stub)
    base = 2_000_000_000
    rules = [
        {
            "kind": "item_discount",
            "discount_mode": "percent",
            "percent": 10,
            "amount_sen": None,
            "buy_quantity": None,
            "get_quantity": None,
            "get_percent": None,
            "threshold_sen": None,
            "product_ids": [base + number * 1000 + n for n in range(200)],
            "category_ids": [base + number * 1000 + n for n in range(30)],
            "tiers": [],
        }
        for number in range(20)
    ]
    limits = {"title": 40, "subtitle": 80, "badge": 12, "strip": 60, "poster_alt": 150}
    copy = {
        f"{name}_{lang}": ("节" if lang == "zh" else "x") * length
        for name, length in limits.items()
        for lang in ("en", "zh", "ms")
    }
    body = _new(name="n" * 100, rules=rules, copy=copy)
    content = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert DEFAULT_BODY_BYTES < len(content) <= MAX_BODY_BYTES

    _ok(_raw(client, "POST", URL, token, content, "application/json"), 201)

    [data] = received
    assert len(data.rules) == 20
    for number, rule in enumerate(data.rules):
        assert rule == RuleInput(
            kind="item_discount",
            discount_mode="percent",
            percent=10,
            product_ids=tuple(base + number * 1000 + n for n in range(200)),
            category_ids=tuple(base + number * 1000 + n for n in range(30)),
        )
    assert data.copy == CopyInput(**copy)


def _set_in(path: tuple[str | int, ...], value: Any) -> Callable[[dict[str, Any]], Any]:
    def mutate(body: dict[str, Any]) -> Any:
        target: Any = body
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return body

    return mutate


def _drop_in(path: tuple[str | int, ...]) -> Callable[[dict[str, Any]], Any]:
    def mutate(body: dict[str, Any]) -> Any:
        target: Any = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
        return body

    return mutate


MALFORMED_PROMOTION = [
    pytest.param(_set_in(("note",), SECRET), id="extra-field"),
    pytest.param(_set_in(("state",), "draft"), id="state-given"),
    pytest.param(_drop_in(("name",)), id="missing-name"),
    pytest.param(_drop_in(("starts_at",)), id="missing-starts-at"),
    pytest.param(_drop_in(("rules",)), id="missing-rules"),
    pytest.param(_drop_in(("decoration",)), id="missing-decoration"),
    pytest.param(_drop_in(("copy",)), id="missing-copy"),
    pytest.param(_set_in(("copy_text",), {}), id="copy-under-attribute-name"),
    pytest.param(_set_in(("name",), None), id="name-null"),
    pytest.param(_set_in(("name",), [SECRET]), id="name-list"),
    pytest.param(_set_in(("starts_at",), 20261201), id="starts-at-number"),
    pytest.param(_set_in(("coupon_combinable",), 1), id="flag-int"),
    pytest.param(_set_in(("points_combinable",), "true"), id="flag-string"),
    pytest.param(_set_in(("coupon_combinable",), None), id="flag-null"),
    pytest.param(_set_in(("rules",), {}), id="rules-object"),
    pytest.param(_set_in(("rules", 0, "note"), SECRET), id="rule-extra-field"),
    pytest.param(_drop_in(("rules", 0, "kind")), id="rule-missing-kind"),
    pytest.param(_set_in(("rules", 0, "percent"), True), id="rule-percent-bool"),
    pytest.param(_set_in(("rules", 0, "percent"), "20"), id="rule-percent-string"),
    pytest.param(_set_in(("rules", 0, "percent"), 20.0), id="rule-percent-float"),
    pytest.param(_set_in(("rules", 0, "product_ids"), ["1"]), id="product-id-string"),
    pytest.param(_set_in(("rules", 0, "product_ids"), None), id="product-ids-null"),
    pytest.param(_set_in(("rules", 1, "category_ids"), [True]), id="category-id-bool"),
    pytest.param(_drop_in(("rules", 2, "tiers", 0, "percent")), id="tier-missing-field"),
    pytest.param(_set_in(("rules", 2, "tiers", 0, "note"), SECRET), id="tier-extra-field"),
    pytest.param(_drop_in(("decoration", "pack")), id="decoration-missing-field"),
    pytest.param(_set_in(("decoration", "note"), SECRET), id="decoration-extra-field"),
    pytest.param(_set_in(("decoration", "pack"), None), id="decoration-null"),
    pytest.param(_set_in(("copy", "note"), SECRET), id="copy-extra-field"),
    pytest.param(_set_in(("copy", "title_en"), 12), id="copy-number"),
    pytest.param(lambda body: [body], id="not-an-object"),
    pytest.param(lambda body: SECRET, id="bare-string"),
]

MALFORMED_VERSION = [
    pytest.param(_drop_in(("version",)), id="missing-version"),
    pytest.param(_set_in(("version",), True), id="version-bool"),
    pytest.param(_set_in(("version",), "1"), id="version-string"),
    pytest.param(_set_in(("version",), 1.0), id="version-float"),
    pytest.param(_set_in(("version",), None), id="version-null"),
]


@pytest.mark.parametrize("mutate", MALFORMED_PROMOTION)
def test_malformed_create_body_is_422_without_echo(
    client: TestClient,
    engine: Engine,
    token: str,
    calls: list[str],
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    """落实写法「请求模型 extra forbid 与 strict；422 照 app/api/pay.py 的 _body_errors 不回显
    输入」；验收「请求模型字段名与 SHOP-TASK-239 的输入数据类相同（嵌套的规则、满额档、装饰
    与文案），类型严格」「结构或类型不对为 422（不回显输入）」：各层多出字段、缺字段、null、
    布尔值或字符串当整数、整数当布尔值等都 422，不调用服务层，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_send(client, "POST", URL, token, mutate(_new(name=SECRET))))
    assert calls == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("mutate", MALFORMED_PROMOTION + MALFORMED_VERSION)
def test_malformed_save_body_is_422_without_echo(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    """验收「保存，请求体为 PromotionInput 的字段加 version」「结构或类型不对为 422（不回显
    输入）」：新建的各种不合格之外，缺 version、布尔值、字符串、浮点数与 null 的 version 都
    422，不调用服务层，数据与审计不变。"""
    before = _snapshot(engine)
    body = _edit(draft, name=SECRET)
    body["rules"] = _new()["rules"]

    _assert_plain_errors(_send(client, "PUT", _item(draft["id"]), token, mutate(body)))
    assert calls == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("action", ["publish", "withdraw", "end", "delete"])
@pytest.mark.parametrize(
    "mutate",
    [
        *MALFORMED_VERSION,
        pytest.param(_set_in(("name",), SECRET), id="extra-field"),
        pytest.param(lambda body: [body], id="not-an-object"),
        pytest.param(lambda body: SECRET, id="bare-string"),
    ],
)
def test_malformed_version_body_is_422_without_echo(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    action: str,
    mutate: Callable[[dict[str, Any]], Any],
) -> None:
    """验收「/publish、/withdraw、/end（请求体只有 version）」「DELETE（请求体只有 version）」
    「结构或类型不对为 422（不回显输入）」：缺 version、类型不对或多出字段都 422，不调用服务层，
    数据与审计不变。"""
    before = _snapshot(engine)
    if action == "delete":
        method, path = "DELETE", _item(draft["id"])
    else:
        method, path = "POST", _item(draft["id"], action)

    _assert_plain_errors(_send(client, method, path, token, mutate({"version": 1})))
    assert calls == []
    assert _snapshot(engine) == before


ITEM_ROUTES = [
    pytest.param("GET", "", id="read"),
    pytest.param("PUT", "", id="save"),
    pytest.param("POST", "publish", id="publish"),
    pytest.param("POST", "withdraw", id="withdraw"),
    pytest.param("POST", "end", id="end"),
    pytest.param("POST", "copy", id="copy"),
    pytest.param("DELETE", "", id="delete"),
]


def _item_request(
    client: TestClient, token: str, method: str, action: str, promotion_id: str, body: Any
) -> Any:
    path = _item(promotion_id, action)
    if method == "GET":
        return _get(client, token, path)
    if action == "copy":
        return _send(client, method, path, token)
    return _send(client, method, path, token, body)


@pytest.mark.parametrize(("method", "action"), ITEM_ROUTES)
@pytest.mark.parametrize(
    "promotion_id",
    [
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
        pytest.param(SECRET, id="not-a-number"),
        pytest.param("1.0", id="decimal"),
        pytest.param("01", id="leading-zero"),
        pytest.param("+1", id="plus-sign"),
        pytest.param("2147483648", id="above-int-max"),
        pytest.param("9" * 200, id="very-long"),
    ],
)
def test_invalid_path_id_is_422_without_echo(
    client: TestClient,
    engine: Engine,
    token: str,
    draft: dict[str, Any],
    calls: list[str],
    method: str,
    action: str,
    promotion_id: str,
) -> None:
    """落实写法「路径 ID 照 _category_id 校验」：0、负数、非数字、小数、前导零、超出 INT
    上限与超长都 422，错误只有 path.promotion_id 一条、不回显所给的值，不调用服务层，数据与
    审计不变。"""
    before = _snapshot(engine)
    body = _edit(draft) if method == "PUT" else {"version": 1}

    errors = _assert_plain_errors(_item_request(client, token, method, action, promotion_id, body))

    assert errors == [
        {
            "type": "promotion_id_invalid",
            "loc": ["path", "promotion_id"],
            "msg": "Promotion ID should be a positive integer",
        }
    ]
    assert calls == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(("method", "action"), [p for p in ITEM_ROUTES if p.id != "read"])
def test_invalid_path_id_is_checked_before_the_body(
    client: TestClient, token: str, calls: list[str], method: str, action: str
) -> None:
    """落实写法「照 admin_categories 的写法」（保存的步骤依次为路径 ID、请求体）：路径 ID 与
    请求体都不合格时只回路径的一条错误，不调用服务层。"""
    response = _item_request(client, token, method, action, "0", {"name": SECRET})

    errors = _assert_plain_errors(response)
    assert [error["loc"] for error in errors] == [["path", "promotion_id"]]
    assert calls == []


def test_copy_does_not_read_the_request_body(
    client: TestClient, token: str, draft: dict[str, Any]
) -> None:
    """验收「/copy（没有请求体，201）」：复制不读请求体，附带超过 64 KB 的非 JSON 内容也照常
    复制成新草稿，不回 413 或 415。"""
    content = b"x" * (MAX_BODY_BYTES + 1)
    path = _item(draft["id"], "copy")

    copied = _ok(_raw(client, "POST", path, token, content, "text/plain"), 201)

    assert copied["id"] != draft["id"]


# ---------------------------------------------------------------------------
# 各路由的成功响应
# ---------------------------------------------------------------------------


def test_list_returns_promotions_and_csrf_token(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any], calls: list[str]
) -> None:
    """A12 列表「<内部名称> <状态> <开始>–<结束> <n>」「按进行中、未开始（开始时间从早到晚）、
    草稿（最近修改在前）、已结束排列」；落实写法「列表响应为 promotions（PromotionSummary 的
    列表）与 csrf_token 两个字段」：已发布未开始的在草稿之前，各项字段依次为 id、name、state、
    starts_at、ends_at、rule_count、updated_at，时间为马来西亚本地「YYYY-MM-DDTHH:MM」或
    null；只读，不写审计。"""
    _ok(_send(client, "POST", _item(draft["id"], "publish"), token, {"version": 1}))
    empty = _new(name="Merdeka", starts_at=None, ends_at=None, rules=[])
    other = _ok(_send(client, "POST", URL, token, empty), 201)
    calls.clear()
    before = _snapshot(engine)
    # updated_at 由服务层或模型在写入时自己取时间，不一定是 NOW：期望值从库里读出。
    updated = dict(
        _read(
            engine,
            lambda session: session.execute(select(Promotion.id, Promotion.updated_at)).all(),
        )
    )

    body = _ok(_get(client, token))

    assert list(body) == ["promotions", "csrf_token"]
    assert body["csrf_token"] == _csrf_for(token)
    assert all(list(row) == SUMMARY_FIELDS for row in body["promotions"])
    assert body["promotions"] == [
        {
            "id": draft["id"],
            "name": "Raya",
            "state": "scheduled",
            "starts_at": SCHEDULED[0],
            "ends_at": SCHEDULED[1],
            "rule_count": 4,
            "updated_at": updated[draft["id"]].isoformat(),
        },
        {
            "id": other["id"],
            "name": "Merdeka",
            "state": "draft",
            "starts_at": None,
            "ends_at": None,
            "rule_count": 0,
            "updated_at": updated[other["id"]].isoformat(),
        },
    ]
    assert calls == ["list_promotions"]
    assert _snapshot(engine) == before


def test_list_is_empty_without_promotions(client: TestClient, token: str) -> None:
    """A12「空：[admin.promo_empty]」：没有活动时 promotions 为空列表，仍带 csrf_token。"""
    assert _ok(_get(client, token)) == {"promotions": [], "csrf_token": _csrf_for(token)}


def test_read_returns_the_detail_and_csrf_token(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any]
) -> None:
    """A12 编辑区（基本信息、规则、同用开关、文案、装饰与海报）；落实写法「读取接口带
    csrf_token」；验收「响应为 PromotionDetail……的同名字段」：字段依次为 PromotionDetail 的
    字段再加 csrf_token；规则按排序，商品与分类 ID 从小到大，products 带 ID、slug 与英文名称
    （不返回个人资料）；只读，不写审计。"""
    before = _snapshot(engine)

    body = _ok(_get(client, token, _item(draft["id"])))

    assert list(body) == [*DETAIL_FIELDS, "csrf_token"]
    assert body.pop("csrf_token") == _csrf_for(token)
    _assert_detail(body)
    assert _comparable(body) == _expected_detail(draft["id"])
    assert body == draft
    assert _snapshot(engine) == before


def test_create_returns_201_and_the_new_draft(
    client: TestClient, engine: Engine, account_id: int, token: str, calls: list[str]
) -> None:
    """A12「新建」；第 2 条新建的活动为草稿；验收「POST /api/admin/promotions（新建，201）」
    「响应为 PromotionDetail」：回 201 与新草稿（版本号 1，名称与文案按服务层去掉首尾空白、
    空串为 null），不带 csrf_token；服务层写一条 admin_promotion_created 审计，操作者为当前
    管理员。"""
    created = _ok(_send(client, "POST", URL, token, _new()), 201)

    _assert_detail(created)
    assert _comparable(created) == _expected_detail(created["id"])
    assert calls == ["create_promotion"]
    assert _audits(engine) == [
        (account_id, "admin_promotion_created", "promotion", created["id"], None, "Raya")
    ]


def test_save_returns_the_saved_detail(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any]
) -> None:
    """A12「草稿 | 全部 | 保存……」；第 2 条有改动即版本号加一；验收「PUT ……（保存，请求体为
    PromotionInput 的字段加 version）」：改内部名称、两个开关与时间、去掉免运费规则，回 200 与
    保存后的活动（版本号 2），等于随后读取到的内容。"""
    body = _edit(
        draft,
        name="Raya 2",
        starts_at="2026-12-02T09:30",
        coupon_combinable=True,
        rules=_edit(draft)["rules"][:3],
    )

    saved = _ok(_send(client, "PUT", _item(draft["id"]), token, body))

    _assert_detail(saved)
    assert _comparable(saved) == _expected_detail(
        draft["id"],
        version=2,
        name="Raya 2",
        starts_at="2026-12-02T09:30",
        coupon_combinable=True,
        rules=RULES_OUT[:3],
    )
    read_back = _ok(_get(client, token, _item(draft["id"])))
    read_back.pop("csrf_token")
    assert read_back == saved


def test_publish_withdraw_and_end_return_the_detail(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any]
) -> None:
    """A12 状态表（草稿可发布、已发布未开始可撤回为草稿、进行中可提前结束）；第 2 条；验收
    「/publish、/withdraw、/end（请求体只有 version）」：发布后为 scheduled、撤回后为 draft，
    把进行中的时段保存后发布为 running、提前结束为 ended（ended_early_at 为 NOW 的马来西亚
    本地时刻），每步版本号加一，都回 200 与 PromotionDetail。"""
    published = _ok(_send(client, "POST", _item(draft["id"], "publish"), token, {"version": 1}))
    _assert_detail(published)
    assert _comparable(published) == _expected_detail(draft["id"], version=2, state="scheduled")

    withdrawn = _ok(_send(client, "POST", _item(draft["id"], "withdraw"), token, {"version": 2}))
    _assert_detail(withdrawn)
    assert _comparable(withdrawn) == _expected_detail(draft["id"], version=3)

    body = _edit(withdrawn, starts_at=RUNNING[0], ends_at=RUNNING[1])
    _ok(_send(client, "PUT", _item(draft["id"]), token, body))
    running = _ok(_send(client, "POST", _item(draft["id"], "publish"), token, {"version": 4}))
    assert running["state"] == "running"

    ended = _ok(_send(client, "POST", _item(draft["id"], "end"), token, {"version": 5}))
    _assert_detail(ended)
    assert _comparable(ended) == _expected_detail(
        draft["id"],
        version=6,
        state="ended",
        ended_early_at="2026-11-13T12:00",
        starts_at=RUNNING[0],
        ends_at=RUNNING[1],
    )


def test_copy_returns_201_and_the_new_draft(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any], calls: list[str]
) -> None:
    """A12「复制成功后进入新草稿的编辑区」；第 2 条复制为新草稿、不复制时间与海报图；验收
    「/copy（没有请求体，201）」：回 201 与新草稿（新 ID、版本号 1、时间为 null，规则、开关、
    装饰与文案相同），原活动不变。"""
    before = _snapshot(engine)

    copied = _ok(_send(client, "POST", _item(draft["id"], "copy"), token), 201)

    _assert_detail(copied)
    assert copied["id"] != draft["id"]
    assert _comparable(copied) == _expected_detail(copied["id"], starts_at=None, ends_at=None)
    assert calls == ["copy_promotion"]
    assert _snapshot(engine)[0][0] == before[0][0]


def test_delete_returns_204_without_a_body(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any]
) -> None:
    """A12「草稿 | 全部 | ……删除」；第 2 条草稿可删除；验收「DELETE ……（请求体只有 version，
    成功 204）」；落实写法「删除成功为 204、没有响应体」：204、响应体为空且带 no-store，之后
    读取 404 promotion_not_found。"""
    response = _send(client, "DELETE", _item(draft["id"]), token, {"version": 1})

    assert response.status_code == 204, response.text
    assert response.content == b""
    _assert_no_store(response)
    assert _snapshot(engine)[0] == []
    _assert_error(_get(client, token, _item(draft["id"])), 404, NOT_FOUND)


# ---------------------------------------------------------------------------
# 服务层异常的映射
# ---------------------------------------------------------------------------

PROBLEMS = [
    PromotionProblem("scope_overlap", "rules.2", product_id=1, product_name="Pan"),
    PromotionProblem("name", "name"),
]
INVALID_BODY = {
    "detail": {
        "code": "promotion_invalid",
        "problems": [
            {"code": "scope_overlap", "field": "rules.2", "product_id": 1, "product_name": "Pan"},
            {"code": "name", "field": "name", "product_id": None, "product_name": None},
        ],
    }
}

# 每个调用服务层的路由与它调用的函数（列表只发 SELECT，不抛这些异常）。
STUBBED_ROUTES: list[tuple[str, str, str]] = [
    ("create_promotion", "POST", ""),
    ("read_promotion", "GET", ""),
    ("save_promotion", "PUT", ""),
    ("publish_promotion", "POST", "publish"),
    ("withdraw_promotion", "POST", "withdraw"),
    ("end_promotion", "POST", "end"),
    ("copy_promotion", "POST", "copy"),
    ("delete_promotion", "DELETE", ""),
]


@pytest.mark.parametrize(
    ("exc", "status_code", "body"),
    [
        pytest.param(PromotionInvalid(PROBLEMS), 422, INVALID_BODY, id="invalid"),
        pytest.param(
            PromotionOverlap("Merdeka"),
            409,
            {"detail": {"code": "promotion_overlap", "name": "Merdeka"}},
            id="overlap",
        ),
        pytest.param(PromotionChanged(SECRET), 409, CHANGED, id="changed"),
        pytest.param(PromotionNotFound(SECRET), 404, NOT_FOUND, id="not-found"),
        pytest.param(
            PromotionStateInvalid("running"),
            409,
            {"detail": {"code": "promotion_state_invalid", "state": "running"}},
            id="state-invalid",
        ),
        pytest.param(PromotionEnded(SECRET), 409, ENDED, id="ended"),
    ],
)
def test_service_exceptions_are_mapped(
    client: TestClient,
    token: str,
    draft: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    status_code: int,
    body: dict[str, Any],
) -> None:
    """落实写法的错误映射：PromotionInvalid 422 {"code": "promotion_invalid", "problems":
    [...]}（每项 code、field、product_id、product_name）；PromotionOverlap 409
    {"code": "promotion_overlap", "name": 对方内部名称}；PromotionChanged 409
    promotion_changed；PromotionNotFound 404 promotion_not_found；PromotionStateInvalid 409
    {"code": "promotion_state_invalid", "state": 当前状态}；PromotionEnded 409 promotion_ended。
    每个调用服务层的路由（以替身抛出）都照同一映射回答，带 no-store，不带异常的说明文字。"""

    def raiser(*args: Any, **kwargs: Any) -> Any:
        raise exc

    for name, method, action in STUBBED_ROUTES:
        monkeypatch.setattr(promotions_api, name, raiser)
        if name == "create_promotion":
            response = _send(client, method, URL, token, _new())
        else:
            payload = _edit(draft) if method == "PUT" else {"version": 1}
            response = _item_request(client, token, method, action, str(draft["id"]), payload)

        _assert_error(response, status_code, body)
        assert SECRET not in response.text


def test_real_service_rejections_reach_the_client(
    client: TestClient, engine: Engine, token: str, draft: dict[str, Any]
) -> None:
    """A12「取值超出范围、商品级规则重叠……时整次不生效：[admin.promo_invalid] 下逐条列出问题
    （重叠为 [admin.promo_scope_overlap]，{product} 为商品名称）」「与其他已发布活动时间重叠
    为 [admin.promo_overlap]（{name} 为对方的内部名称）」「版本不符为 [admin.promo_changed]」；
    第 1–3 条：经真实的服务层，空名称与两条规则共有一件商品 422 promotion_invalid（列出
    问题与商品），时间重叠 409 promotion_overlap，状态不允许 409 promotion_state_invalid，版本
    不符 409 promotion_changed，不存在 404 promotion_not_found；都不改数据。"""
    _ok(_send(client, "POST", _item(draft["id"], "publish"), token, {"version": 1}))
    overlapping = _ok(
        _send(client, "POST", URL, token, _new(name="Merdeka", starts_at="2026-12-05T00:00")),
        201,
    )
    before = _snapshot(engine)
    shared = {"kind": "item_discount", "discount_mode": "percent", "percent": 10}

    _assert_error(
        _send(client, "POST", URL, token, _new(name=" ")),
        422,
        {
            "detail": {
                "code": "promotion_invalid",
                "problems": [
                    {"code": "name", "field": "name", "product_id": None, "product_name": None}
                ],
            }
        },
    )
    rules = [shared | {"product_ids": [1]}, shared | {"product_ids": [1, 2]}]
    _assert_error(
        _send(client, "POST", URL, token, _new(rules=rules)),
        422,
        {
            "detail": {
                "code": "promotion_invalid",
                "problems": [
                    {
                        "code": "scope_overlap",
                        "field": "rules.2",
                        "product_id": 1,
                        "product_name": "Pan",
                    }
                ],
            }
        },
    )
    _assert_error(
        _send(client, "POST", _item(overlapping["id"], "publish"), token, {"version": 1}),
        409,
        {"detail": {"code": "promotion_overlap", "name": "Raya"}},
    )
    _assert_error(
        _send(client, "POST", _item(draft["id"], "publish"), token, {"version": 2}),
        409,
        {"detail": {"code": "promotion_state_invalid", "state": "scheduled"}},
    )
    _assert_error(
        _send(client, "PUT", _item(overlapping["id"]), token, _edit(overlapping, version=9)),
        409,
        CHANGED,
    )
    _assert_error(_get(client, token, _item(999)), 404, NOT_FOUND)
    _assert_error(_send(client, "POST", _item(999, "copy"), token), 404, NOT_FOUND)
    assert _snapshot(engine) == before


def test_request_fields_reach_the_service_unchanged(
    client: TestClient, token: str, draft: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 1–3、12 条「后台接口只传递与校验字段，规则、核对与审计在服务层」；验收「请求模型字段名
    与 SHOP-TASK-239 的输入数据类相同」：保存请求的各字段（含嵌套的规则、满额档、装饰与文案，
    数据类有默认值的字段可省略）原样换成 PromotionInput 与路径 ID、版本号一起传给服务层，取值
    不在接口里检查（超出范围的百分比与格式不对的时间也照传）。"""
    received: list[tuple[int, int, PromotionInput]] = []

    def stub(
        db: Session, admin_id: int, promotion_id: int, version: int, data: PromotionInput, now: Any
    ) -> Any:
        assert now == NOW
        received.append((promotion_id, version, data))
        return _stub_detail(promotion_id)

    monkeypatch.setattr(promotions_api, "save_promotion", stub)
    body = _new(
        name=" Raya ",
        starts_at="2026-12-1 9:00",
        ends_at=None,
        rules=[
            {"kind": "item_discount", "discount_mode": "percent", "percent": 999},
            RULES_IN[2],
        ],
        copy={"badge_ms": "Jimat"},
    )
    body["version"] = 3

    _ok(_send(client, "PUT", _item(draft["id"]), token, body))

    assert received == [
        (
            draft["id"],
            3,
            PromotionInput(
                name=" Raya ",
                starts_at="2026-12-1 9:00",
                ends_at=None,
                coupon_combinable=False,
                points_combinable=True,
                rules=(
                    RuleInput(kind="item_discount", discount_mode="percent", percent=999),
                    RuleInput(
                        kind="spend_discount",
                        discount_mode="amount",
                        tiers=(TierInput(10000, 1000, None), TierInput(20000, 2500, None)),
                    ),
                ),
                decoration=DecorationInput(**DECORATION),
                copy=CopyInput(badge_ms="Jimat"),
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient, token: str, draft: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """验收「所有响应带 no-store」：列表、读取、新建 201、保存、发布、撤回、复制 201、删除
    204、401、403、413、415、请求体 422、路径 422、promotion_invalid、promotion_changed、
    promotion_state_invalid 与 404 都带 no-store，应用不记日志。"""
    caplog.set_level(logging.DEBUG)
    oversized = json.dumps({"name": "x" * MAX_BODY_BYTES}).encode()
    promotion_id = draft["id"]
    created = _send(client, "POST", URL, token, _new(starts_at=None, ends_at=None))
    responses = [
        _get(client, token),
        _get(client, token, _item(promotion_id)),
        created,
        _send(client, "PUT", _item(promotion_id), token, _edit(draft, name="Raya 2")),
        _send(client, "POST", _item(promotion_id, "publish"), token, {"version": 2}),
        _send(client, "POST", _item(promotion_id, "withdraw"), token, {"version": 3}),
        _send(client, "POST", _item(promotion_id, "copy"), token),
        _send(client, "DELETE", _item(created.json()["id"]), token, {"version": 1}),
        _get(client, None),
        _send(client, "POST", _item(promotion_id, "end"), token, {"version": 4}, csrf=None),
        _raw(client, "PUT", _item(promotion_id), token, oversized, "application/json"),
        _raw(client, "POST", URL, token, b"{}", "text/plain"),
        _send(client, "POST", URL, token, {"name": "x"}),
        _get(client, token, _item("0")),
        _send(client, "POST", URL, token, _new(name="")),
        _send(client, "POST", _item(promotion_id, "publish"), token, {"version": 9}),
        _send(client, "POST", _item(promotion_id, "end"), token, {"version": 4}),
        _get(client, token, _item(999)),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses[:8] == [200, 200, 201, 200, 200, 200, 201, 204]
    assert statuses[8:] == [401, 403, 413, 415, 422, 422, 422, 409, 409, 404]
    assert responses[-2].json()["detail"]["code"] == "promotion_state_invalid"
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_registered(app: FastAPI) -> None:
    """验收「路由——GET /api/admin/promotions（列表）、POST（新建）、GET、PUT 与 DELETE
    /{promotion_id}、POST /{promotion_id}/publish、/withdraw、/end 与 /copy……在 app/main.py
    后台段照现有顺序注册」「路由清单的断言只列本文件 router 上的路由」：本模块的 router 上恰好
    这九个，且都已注册到应用（照 tests/test_admin_categories_api.py 从应用的 OpenAPI 路径读出，
    只查这九个是否在其中）。"""
    item = f"{URL}/{{promotion_id}}"
    expected = {
        ("GET", URL),
        ("POST", URL),
        ("GET", item),
        ("PUT", item),
        ("DELETE", item),
        ("POST", f"{item}/publish"),
        ("POST", f"{item}/withdraw"),
        ("POST", f"{item}/end"),
        ("POST", f"{item}/copy"),
    }
    own = {
        (method, route.path)
        for route in promotions_api.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }

    assert own == expected
    assert expected <= registered

"""管理员预览草稿与未开始活动的数据接口（app/api/admin_promotion_preview.py，SHOP-TASK-245）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 8、11 条（下称「第 N 条」）与
docs/UX.md 0.20 的 A12「前台预览」（下称「A12」）。每条测试（参数化的测试是每个用例）的文档
字符串写明它守住的第 11 条或 A12 的哪一句。

夹具照 tests/test_admin_promotions_api.py 与 tests/test_promotion_display.py：SQLite 内存库按模型
建表（StaticPool、开外键），写入管理员、西马与东马运费和第 8 条算例的商品（A、B、C，另有不在任何
规则里的 D）并提交；活动以模型行写入并提交（promotion_display 读完回滚）。接口每次请求新开会话，
https 的 base_url，以 issue_admin_session 登录，期望的 CSRF 令牌在这里另算。时间固定为 NOW：
monkeypatch 本接口模块与 app.api.admin_auth 的 _utcnow，同时调用的公开接口（活动、目录、计价）的
_utcnow 也固定为 NOW，使「未开始」不随真实时钟变成「进行中」。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_promotion_preview as preview_api
import app.api.catalog as catalog_api
import app.api.checkout as checkout_api
import app.api.promotion as promotion_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    Category,
    Order,
    Product,
    ProductVariant,
    Promotion,
    PromotionDecoration,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    ShippingRate,
)
from app.services.admin_auth import issue_admin_session
from app.services.promotion_display import promotion_display

URL = "/api/admin/promotions"
PUBLIC_PROMOTION = "/api/promotion"
PUBLIC_PRODUCTS = "/api/catalog/products"
PUBLIC_QUOTE = "/api/checkout/quote"
# 游客计价接口的请求体上限。
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

# 不带时区的 UTC。
NOW = datetime(2026, 11, 13, 4, 0)
STARTS = NOW + timedelta(days=1)
ENDS = NOW + timedelta(days=8)
SECRET = "SECRETVALUE"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "promotion_not_found"}
UNAVAILABLE = {"detail": "promotion_preview_unavailable"}
MISSING_ID = 999

# 第 8 条算例的购物车：A 2 件、B 2 件、C 1 件，收货西马（运费 RM8.00）。
SECTION_8_LINES = [
    {"sku": "A", "quantity": 2},
    {"sku": "B", "quantity": 2},
    {"sku": "C", "quantity": 1},
]
SECTION_8_BODY = {
    "lines": SECTION_8_LINES,
    "country_code": "MY",
    "state_code": "MY-10",
}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _add(db: Session, row: Any) -> Any:
    db.add(row)
    db.flush()
    return row


def _product(db: Session, category: Category, slug: str, price_sen: int) -> None:
    """没有规格名的已发布商品，唯一的 SKU 为 slug 的大写。"""
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=slug,
            name_en=slug.upper(),
            description_en=f"{slug}.",
            is_active=True,
            max_per_order=10,
        ),
    )
    _add(
        db,
        ProductVariant(
            product_id=product.id,
            sku=slug.upper(),
            price_sen=price_sen,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        ),
    )


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
        seed.add(
            AdminAccount(
                username="admin@example.com",
                password_hash="not-a-real-hash",
                created_at=NOW - timedelta(days=1),
                password_updated_at=NOW - timedelta(days=1),
            )
        )
        for zone_code, fee_sen in (("MY-W", 800), ("MY-E", 1500)):
            seed.add(
                ShippingRate(zone_type="my_region", zone_code=zone_code, fee_sen=fee_sen, version=1)
            )
        gifts = _add(seed, Category(slug="gifts", name_en="Gifts", is_active=True))
        home = _add(seed, Category(slug="home", name_en="Home", is_active=True))
        _product(seed, gifts, "a", 2500)
        _product(seed, home, "b", 1200)
        _product(seed, home, "c", 900)
        _product(seed, gifts, "d", 3000)
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (preview_api, admin_auth_api, promotion_api, catalog_api, checkout_api):
        monkeypatch.setattr(module, "_utcnow", lambda: NOW)


@pytest.fixture
def token(db: Session) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    account_id = db.scalar(select(AdminAccount.id))
    assert account_id is not None
    issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
    db.commit()
    return issued.token


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@dataclass(frozen=True)
class _Seeded:
    promotion_id: int
    item_rule_id: int
    bxgy_rule_id: int


def _promotion(
    db: Session,
    *,
    status: str = "draft",
    starts_at: datetime | None = STARTS,
    ends_at: datetime | None = ENDS,
    ended_early_at: datetime | None = None,
) -> _Seeded:
    """第 8 条算例的活动：A 打 8 折；「家居」买 2 享 1、享受的件 100%；满 RM50 减 RM5、满 RM100
    减 RM12；满 RM60 免运费。带装饰行（模板海报，英文与中文标题），写入后提交。
    """
    promotion = _add(
        db,
        Promotion(
            name="Internal-Name",
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            ended_early_at=ended_early_at,
            coupon_combinable=True,
            points_combinable=True,
        ),
    )
    a = db.scalars(select(Product).where(Product.slug == "a")).one()
    home = db.scalars(select(Category).where(Category.slug == "home")).one()
    item_rule = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id,
            position=1,
            kind="item_discount",
            discount_mode="percent",
            percent=20,
        ),
    )
    _add(db, PromotionRuleProduct(rule_id=item_rule.id, product_id=a.id))
    bxgy = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id,
            position=2,
            kind="buy_x_get_y",
            buy_quantity=2,
            get_quantity=1,
            get_percent=100,
        ),
    )
    _add(db, PromotionRuleCategory(rule_id=bxgy.id, category_id=home.id))
    spend = _add(
        db,
        PromotionRule(
            promotion_id=promotion.id, position=3, kind="spend_discount", discount_mode="amount"
        ),
    )
    for position, threshold_sen, amount_sen in [(1, 5000, 500), (2, 10000, 1200)]:
        _add(
            db,
            PromotionSpendTier(
                rule_id=spend.id,
                position=position,
                threshold_sen=threshold_sen,
                amount_sen=amount_sen,
            ),
        )
    _add(
        db,
        PromotionRule(
            promotion_id=promotion.id, position=4, kind="free_shipping", threshold_sen=6000
        ),
    )
    _add(
        db,
        PromotionDecoration(
            promotion_id=promotion.id,
            pack="cny",
            palette="theme",
            ambient="none",
            home_poster="split",
            list_strip="band",
            card_badge="tag",
            detail_ribbon="ribbon",
            cart_progress="bar",
            poster_mode="template",
            title_en="Year-End Sale",
            title_zh="年终特卖",
        ),
    )
    seeded = _Seeded(promotion.id, item_rule.id, bxgy.id)
    db.commit()
    return seeded


# 可预览的三种活动：草稿、已发布未开始、开始时间已过的草稿（A12「唯一的禁用条件是……没有开始
# 时间或结束时间，或结束时间已过」，草稿的开始时间已过不在其中）。
PREVIEWABLE = [
    pytest.param({"status": "draft"}, id="draft"),
    pytest.param({"status": "published"}, id="scheduled"),
    pytest.param({"status": "draft", "starts_at": NOW - timedelta(days=1)}, id="draft_started"),
]


# ---------------------------------------------------------------------------
# 请求
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


def _path(promotion_id: int | str, rest: str = "") -> str:
    return f"{URL}/{promotion_id}/preview{rest}"


def _get(client: TestClient, token: str | None, path: str, **params: Any) -> Any:
    return client.get(path, params=params, headers=_headers(token, None))


def _quote(
    client: TestClient,
    token: str | None,
    promotion_id: int | str,
    body: Any = None,
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.post(
        _path(promotion_id, "/quote"),
        json=SECTION_8_BODY if body is None else body,
        headers=_headers(token, csrf),
    )


def _all_requests(client: TestClient, token: str | None, promotion_id: int | str) -> list[Any]:
    """四个预览路由各一次（计价带合法的 CSRF 令牌与第 8 条的购物车）。"""
    return [
        _get(client, token, _path(promotion_id)),
        _get(client, token, _path(promotion_id, "/products")),
        _get(client, token, _path(promotion_id, "/products/a")),
        _quote(client, token, promotion_id),
    ]


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _assert_error(response: Any, status_code: int, body: Any) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    assert response.headers["cache-control"] == "no-store"


def _assert_plain_errors(response: Any) -> list[dict[str, Any]]:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert SECRET not in response.text
    return errors


def _card(rule_id: int, kind: str, **params: Any) -> dict[str, Any]:
    empty = {
        "discount_mode": None,
        "percent": None,
        "amount_sen": None,
        "buy_quantity": None,
        "get_quantity": None,
        "get_percent": None,
    }
    return {"rule_id": rule_id, "kind": kind} | empty | params


def _a_card(seeded: _Seeded) -> dict[str, Any]:
    return _card(seeded.item_rule_id, "item_discount", discount_mode="percent", percent=20)


def _home_card(seeded: _Seeded) -> dict[str, Any]:
    return _card(
        seeded.bxgy_rule_id, "buy_x_get_y", buy_quantity=2, get_quantity=1, get_percent=100
    )


def _order_count(engine: Engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(Order)) or 0


# ---------------------------------------------------------------------------
# 预览的数据
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fields", PREVIEWABLE)
def test_preview_display_is_as_if_the_promotion_were_active(
    db: Session, client: TestClient, token: str, fields: dict[str, Any]
) -> None:
    """第 11 条「管理员可在前台以预览方式查看草稿或未开始活动的首页……看到的装饰与价格如同该活动
    正在生效」：GET …/preview 的响应同 GET /api/promotion，只有 promotion 与 server_time；
    promotion 即该活动的展示数据（所选语言的标题、装饰、实际结束时间与四条规则），
    server_time 为服务端当前时间。
    """
    seeded = _promotion(db, **fields)

    body = _ok(_get(client, token, _path(seeded.promotion_id), lang="zh"))

    assert list(body) == ["promotion", "server_time"]
    assert body["server_time"] == "2026-11-13T04:00:00Z"
    expected = promotion_display(db, seeded.promotion_id, "zh")
    assert body["promotion"] == jsonable_encoder(expected)
    promotion = body["promotion"]
    assert (promotion["promotion_id"], promotion["title"]) == (seeded.promotion_id, "年终特卖")
    assert promotion["ends_at"] == "2026-11-21T04:00:00Z"
    kinds = [rule["kind"] for rule in promotion["rules"]]
    assert kinds == ["item_discount", "buy_x_get_y", "spend_discount", "free_shipping"]


@pytest.mark.parametrize("fields", PREVIEWABLE)
def test_preview_products_carry_sale_prices_badges_and_the_sale_list(
    db: Session, client: TestClient, token: str, fields: dict[str, Any]
) -> None:
    """第 11 条「……商品列表……与专题页，看到的装饰与价格如同该活动正在生效」：预览的商品列表里
    A 带打 8 折的规则与最低活动单价 2000，B、C 带买 2 享 1 的角标参数、价格不变，D 不在任何
    规则里；sale=true 的专题页列表恰为规则范围内的 A、B、C。
    """
    seeded = _promotion(db, **fields)

    listing = _ok(_get(client, token, _path(seeded.promotion_id, "/products")))
    sale = _ok(_get(client, token, _path(seeded.promotion_id, "/products"), sale="true"))

    items = {item["slug"]: item for item in listing["items"]}
    assert (items["a"]["promotion"], items["a"]["min_sale_price_sen"]) == (_a_card(seeded), 2000)
    home = (_home_card(seeded), None)
    for slug in ("b", "c"):
        assert (items[slug]["promotion"], items[slug]["min_sale_price_sen"]) == home
    assert (items["d"]["promotion"], items["d"]["min_sale_price_sen"]) == (None, None)
    assert sale["total"] == 3
    assert {item["slug"] for item in sale["items"]} == {"a", "b", "c"}


@pytest.mark.parametrize("fields", PREVIEWABLE)
def test_preview_product_detail_carries_sale_price_and_badge(
    db: Session, client: TestClient, token: str, fields: dict[str, Any]
) -> None:
    """第 11 条「……商品详情……看到的装饰与价格如同该活动正在生效」：预览的 A 详情带打 8 折的
    规则、SKU 的活动单价 2000；B 带买 2 享 1 的角标参数、活动单价为空；不存在的商品照商品详情
    404 product not found。
    """
    seeded = _promotion(db, **fields)

    a = _ok(_get(client, token, _path(seeded.promotion_id, "/products/a")))
    b = _ok(_get(client, token, _path(seeded.promotion_id, "/products/b")))
    missing = _get(client, token, _path(seeded.promotion_id, "/products/no-such-product"))

    assert a["promotion"] == _a_card(seeded)
    assert [(v["price_sen"], v["sale_price_sen"]) for v in a["variants"]] == [(2500, 2000)]
    assert b["promotion"] == _home_card(seeded)
    assert [(v["price_sen"], v["sale_price_sen"]) for v in b["variants"]] == [(1200, None)]
    _assert_error(missing, 404, {"detail": "product not found"})


@pytest.mark.parametrize("fields", PREVIEWABLE)
def test_preview_quote_gives_the_section_8_guest_total(
    db: Session, engine: Engine, client: TestClient, token: str, fields: dict[str, Any]
) -> None:
    """第 11 条「……购物车……看到的……价格如同该活动正在生效」与第 8 条算例（游客不用券，收货西马
    运费 RM8.00）：S1 = RM64.00，整单减 RM5.00，免去运费 RM8.00，合计 5900；A12「购物车预览用
    本浏览器的购物车计价，不能结账、不能下单」：计价之后没有订单。
    """
    seeded = _promotion(db, **fields)

    quote = _ok(_quote(client, token, seeded.promotion_id))

    promotion = quote["promotion"]
    assert promotion["promotion_id"] == seeded.promotion_id
    assert (promotion["item_discount_sen"], promotion["order_discount_sen"]) == (1900, 500)
    assert promotion["after_item_subtotal_sen"] == 6400
    assert (promotion["shipping_waived_sen"], promotion["participates"]) == (800, True)
    assert quote["subtotal_sen"] == 8300
    assert quote["shipping"] == {"fee_sen": 800, "zone_code": "MY-W", "version": 1}
    assert quote["total_sen"] == 5900
    assert _order_count(engine) == 0


# ---------------------------------------------------------------------------
# 隔离
# ---------------------------------------------------------------------------


def test_public_endpoints_stay_null_while_previewing(
    db: Session, client: TestClient, token: str
) -> None:
    """第 11 条「公开接口不接受任何预览参数」「预览不改变访客看到的内容」：同时调用预览与公开接口，
    公开的 GET /api/promotion 的 promotion 为 null，商品目录没有活动价、角标与专题页商品，游客
    计价没有活动、合计为 8300 + 800；公开接口带上活动 ID 与 preview 之类的查询参数也一样。
    """
    seeded = _promotion(db, status="published")
    for response in _all_requests(client, token, seeded.promotion_id):
        _ok(response)
    preview_params = {"promotion_id": seeded.promotion_id, "preview": "1"}
    cookie = _headers(token)

    for params in ({}, preview_params):
        public = client.get(PUBLIC_PROMOTION, params=params, headers=cookie)
        listing = client.get(PUBLIC_PRODUCTS, params=params, headers=cookie)
        sale = client.get(PUBLIC_PRODUCTS, params={"sale": "true", **params}, headers=cookie)
        detail = client.get(f"{PUBLIC_PRODUCTS}/a", params=params, headers=cookie)
        quote = client.post(PUBLIC_QUOTE, params=params, json=SECTION_8_BODY, headers=cookie)

        assert _ok(public)["promotion"] is None
        assert all(item["promotion"] is None for item in _ok(listing)["items"])
        assert _ok(sale)["total"] == 0
        assert _ok(detail)["variants"][0]["sale_price_sen"] is None
        assert quote.status_code == 200, quote.text
        assert (quote.json()["promotion"], quote.json()["total_sen"]) == (None, 9100)


def test_preview_quote_is_not_bound_by_the_guest_source_limit(
    db: Session, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：预览计价是后台接口，不经游客计价的
    来源限流——限流替身让游客计价一律 429 时，预览计价照常回答。
    """
    seeded = _promotion(db)

    async def _always_limited(request: Request, *args: Any) -> None:
        raise HTTPException(status_code=429, detail="rate_limited")

    monkeypatch.setattr(checkout_api, "enforce_source_limit", _always_limited)

    assert client.post(PUBLIC_QUOTE, json=SECTION_8_BODY).status_code == 429
    assert _ok(_quote(client, token, seeded.promotion_id))["total_sen"] == 5900


def test_only_the_four_preview_routes_are_registered(app: FastAPI) -> None:
    """第 11 条「预览不……产生订单」与 A12「不能结账、不能下单」：本模块的 router 上恰好四个路由
    （三个 GET 与计价的 POST，没有下单接口），且都已注册到应用。
    """
    item = f"{URL}/{{promotion_id}}/preview"
    expected = {
        ("GET", item),
        ("GET", f"{item}/products"),
        ("GET", f"{item}/products/{{slug}}"),
        ("POST", f"{item}/quote"),
    }
    own = {
        (method, route.path)
        for route in preview_api.router.routes
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


# ---------------------------------------------------------------------------
# 可预览的条件与错误
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"status": "published", "starts_at": NOW - timedelta(days=1)}, id="running"),
        pytest.param(
            {
                "status": "published",
                "starts_at": NOW - timedelta(days=3),
                "ends_at": NOW - timedelta(days=1),
            },
            id="ended",
        ),
        pytest.param(
            {
                "status": "published",
                "starts_at": NOW - timedelta(days=3),
                "ended_early_at": NOW - timedelta(days=1),
            },
            id="ended_early",
        ),
        pytest.param({"status": "draft", "starts_at": None, "ends_at": None}, id="draft_no_times"),
        pytest.param({"status": "draft", "ends_at": None}, id="draft_no_end"),
        pytest.param({"status": "draft", "starts_at": None}, id="draft_no_start"),
        pytest.param(
            {
                "status": "draft",
                "starts_at": NOW - timedelta(days=3),
                "ends_at": NOW - timedelta(days=1),
            },
            id="draft_end_passed",
        ),
        pytest.param(
            {"status": "draft", "starts_at": NOW - timedelta(days=3), "ends_at": NOW},
            id="draft_ends_now",
        ),
    ],
)
def test_unpreviewable_promotions_are_409_on_every_route(
    db: Session, client: TestClient, token: str, fields: dict[str, Any]
) -> None:
    """第 11 条「查看草稿或未开始活动」与 A12「进行中……不提供预览」「前台预览的唯一禁用条件是：
    已保存的内容里没有开始时间或结束时间，或结束时间已过」：进行中、已结束、已提前结束、草稿
    缺开始或结束时间、草稿的结束时间已过（含恰为当前时刻）时，四个路由都是 409
    promotion_preview_unavailable。
    """
    seeded = _promotion(db, **fields)

    for response in _all_requests(client, token, seeded.promotion_id):
        _assert_error(response, 409, UNAVAILABLE)


def test_missing_promotion_is_404_on_every_route(client: TestClient, token: str) -> None:
    """第 11 条「草稿或未开始活动」：不存在的活动无从预览，四个路由都是 404 promotion_not_found。"""
    for response in _all_requests(client, token, MISSING_ID):
        _assert_error(response, 404, NOT_FOUND)


def test_without_an_admin_session_every_route_is_401(
    db: Session, client: TestClient, token: str
) -> None:
    """第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：没有会话 cookie 或会话不存在时，
    四个路由都是 401 admin_session_required，先于路径 ID、请求体与读活动。
    """
    seeded = _promotion(db)

    for promotion_id in (seeded.promotion_id, MISSING_ID, "abc"):
        for response in _all_requests(client, None, promotion_id):
            _assert_error(response, 401, SESSION_REQUIRED)
        for response in _all_requests(client, "not-a-session", promotion_id):
            _assert_error(response, 401, SESSION_REQUIRED)
    unread = _quote(client, None, seeded.promotion_id, {"lines": SECRET})
    _assert_error(unread, 401, SESSION_REQUIRED)


def test_preview_quote_without_csrf_is_403(db: Session, client: TestClient, token: str) -> None:
    """第 11 条「预览的数据只经后台接口（凭管理员会话）取得」与落实写法「计价是 POST，照后台写操作
    要 CSRF」：不带或带错 X-CSRF-Token 时 403 csrf_failed，先于路径 ID、请求体与读活动。
    """
    seeded = _promotion(db)

    for promotion_id in (seeded.promotion_id, MISSING_ID, "abc"):
        for csrf in (None, "0" * 64):
            response = _quote(client, token, promotion_id, {"lines": SECRET}, csrf=csrf)
            _assert_error(response, 403, CSRF_FAILED)


@pytest.mark.parametrize("bad_id", ["0", "01", "abc", str(2**31)])
def test_invalid_path_id_is_422_on_every_route(client: TestClient, token: str, bad_id: str) -> None:
    """第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：会话（与计价的 CSRF）通过后，不合法
    的活动 ID 在四个路由都是 422，只指出路径参数、不回显所给的值；计价的请求体此时还没有校验。
    """
    responses = _all_requests(client, token, bad_id)
    responses.append(_quote(client, token, bad_id, {"lines": SECRET}))

    for response in responses:
        errors = _assert_plain_errors(response)
        assert [error["loc"] for error in errors] == [["path", "promotion_id"]]


@pytest.mark.parametrize(
    ("rest", "params"),
    [
        pytest.param("", {"lang": SECRET}, id="preview_lang"),
        pytest.param("/products", {"lang": SECRET}, id="products_lang"),
        pytest.param("/products", {"page": "0"}, id="products_page"),
        pytest.param("/products", {"sale": SECRET}, id="products_sale"),
        pytest.param("/products", {"slug": "x" * 101}, id="products_slug"),
        pytest.param("/products/a", {"lang": SECRET}, id="detail_lang"),
    ],
)
def test_query_errors_come_before_reading_the_promotion(
    db: Session, client: TestClient, token: str, rest: str, params: dict[str, str]
) -> None:
    """第 11 条「公开接口不接受任何预览参数」——预览的查询参数照公开接口校验：参数不合法时，
    不存在的活动与不可预览的活动都是 422 而不是 404、409。
    """
    running = _promotion(db, status="published", starts_at=NOW - timedelta(days=1))

    for promotion_id in (MISSING_ID, running.promotion_id):
        response = _get(client, token, _path(promotion_id, rest), **params)
        _assert_plain_errors(response)


def test_option_parse_error_comes_before_reading_the_promotion(
    client: TestClient, token: str
) -> None:
    """第 11 条「……商品列表……如同该活动正在生效」——参数同公开的商品列表：option 不是
    <规格名>:<规格值> 时照目录接口 422，先于读活动（不存在的活动也是 422）。
    """
    response = _get(client, token, _path(MISSING_ID, "/products"), option="color")

    _assert_error(response, 422, {"detail": "option must be <option>:<value>"})


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"lines": []}, id="no_lines"),
        pytest.param({"lines": [{"sku": "A", "quantity": 0}]}, id="zero_quantity"),
        pytest.param({"lines": [{"sku": "A", "quantity": "2"}]}, id="string_quantity"),
        pytest.param({"lines": [{"sku": "A", "quantity": 1}], "price_sen": 1}, id="extra_field"),
        pytest.param(
            {"lines": [{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 1}]},
            id="duplicate_sku",
        ),
        pytest.param(
            {"lines": [{"sku": SECRET, "quantity": 1}], "state_code": "MY-10"}, id="state"
        ),
    ],
)
def test_quote_body_errors_come_before_reading_the_promotion(
    client: TestClient, token: str, body: dict[str, Any]
) -> None:
    """第 11 条「……购物车……」——请求体同游客计价：不合格时 422，错误位置在 body 下、不回显
    所给的值，先于读活动（不存在的活动也是 422）。
    """
    errors = _assert_plain_errors(_quote(client, token, MISSING_ID, body))

    assert all(error["loc"][0] == "body" for error in errors)


def test_quote_body_size_and_content_type_follow_the_guest_quote(
    db: Session, client: TestClient, token: str
) -> None:
    """第 11 条「……购物车……」——请求体的写法照游客计价：超过 8 KB 即 413（先于读活动）；
    不查 Content-Type，不是 JSON 的内容是 422，text/plain 发送的合法 JSON 照常计价。
    """
    seeded = _promotion(db)
    headers = _headers(token) | {"Content-Type": "text/plain"}
    url = _path(MISSING_ID, "/quote")

    oversized = client.post(url, content=b" " * (MAX_BODY_BYTES + 1), headers=headers)
    not_json = client.post(url, content=SECRET.encode(), headers=headers)
    plain = client.post(
        _path(seeded.promotion_id, "/quote"),
        content=json.dumps(SECTION_8_BODY).encode(),
        headers=headers,
    )

    _assert_error(oversized, 413, {"detail": "request body too large"})
    _assert_plain_errors(not_json)
    assert _ok(plain)["total_sen"] == 5900


def test_shipping_errors_come_after_reading_the_promotion(
    db: Session, client: TestClient, token: str
) -> None:
    """第 11 条「……购物车……」——运费照游客计价：马来西亚缺州属代码时 422 invalid country or
    state code，没有应有的运费行（夹具没有新加坡与其他地区的运费）时 503 shipping is
    unavailable；这两种在读活动之后，不存在的活动仍是 404。
    """
    seeded = _promotion(db)
    lines = SECTION_8_BODY["lines"]

    for body, status_code, detail in [
        ({"lines": lines, "country_code": "MY"}, 422, "invalid country or state code"),
        ({"lines": lines, "country_code": "SG"}, 503, "shipping is unavailable"),
    ]:
        _assert_error(_quote(client, token, MISSING_ID, body), 404, NOT_FOUND)
        response = _quote(client, token, seeded.promotion_id, body)
        _assert_error(response, status_code, {"detail": detail})

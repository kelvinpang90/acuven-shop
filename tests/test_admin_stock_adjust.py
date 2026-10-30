"""后台当日库存调整：app/services/admin_stock_adjust.py 与
POST /api/admin/variants/{variant_id}/available-stock
（app/api/admin_stock_adjust.py，SHOP-TASK-182）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 12 条（下称「第 12 条」）、
docs/UX.md 0.17 的 A04 与 docs/HANDOFF.md 0.76 的落实写法（下称「HANDOFF 0.76」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的第 12 条原句；
没有直接原句的，写明是 SHOP-TASK-182 验收里的约定（下称「验收」）。

夹具照 tests/test_admin_shipping_rates_api.py：SQLite 内存库按模型建表（StaticPool），管理员与
后台会话直接写库并提交；接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交
的改动；检查结果一律在另一个数据库会话里只查列值读出，读得到即说明已提交。期望的 CSRF 令牌在
这里另算。引擎另照 tests/test_stock_reset.py 关掉驱动的事务处理、由引擎发 BEGIN 并打开外键
检查，每日重置在这个库上的事务边界才和 MySQL 上一样；所以测试里的数据库会话都用完即关，不在
接口或重置运行时占着事务。
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
from sqlalchemy import Engine, create_engine, event, select, update
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
    Order,
    OrderItem,
    Product,
    ProductVariant,
)
from app.models.order import STATUS_AWAITING_PAYMENT, STATUS_CANCELLED
from app.services import stock_reset
from app.services.admin_auth import issue_admin_session
from app.services.admin_stock_adjust import (
    ADMIN_VARIANT_STOCK_ADJUSTED,
    AUDIT_TARGET_PRODUCT_VARIANT,
    StockChanged,
    StockInvalid,
    VariantNotFound,
    adjust_available_stock,
    adjust_stock_statement,
)
from app.services.order_rules import generate_order_number

MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
STOCK_INVALID = {"detail": "stock_invalid"}
VARIANT_NOT_FOUND = {"detail": "variant_not_found"}
SECRET = "SECRETVALUE"

# UTC 03:00 即马来西亚 11:00，营业日期 2026-10-05（同 tests/test_stock_reset.py）。
NOW = datetime(2026, 10, 5, 3, 0, 0)
PRICE = 1500
SHIPPING = 800
SKU = "TEE-RED-M"
INITIAL = 20
AVAILABLE = 12
MISSING_ID = 999_999


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

    # 同 tests/test_stock_reset.py：由引擎发 BEGIN，事务边界与 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _variant(
    engine: Engine,
    *,
    sku: str = SKU,
    initial: int = INITIAL,
    available: int = AVAILABLE,
    active: bool = True,
) -> int:
    """写一件商品与它的一个 SKU 并提交；返回 SKU 规格 ID。"""
    with Session(engine) as session:
        category = Category(slug=f"cat-{generate_order_number()}", name_en="Cat", is_active=True)
        session.add(category)
        session.flush()
        product = Product(
            category_id=category.id,
            slug=f"p-{generate_order_number()}",
            name_en="Tee",
            is_active=True,
            max_per_order=10,
        )
        session.add(product)
        session.flush()
        variant = ProductVariant(
            product_id=product.id,
            sku=sku,
            price_sen=PRICE,
            daily_initial_stock=initial,
            available_stock=available,
            is_active=active,
        )
        session.add(variant)
        session.flush()
        variant_id = variant.id
        session.commit()
    return variant_id


def _order(engine: Engine, variant_id: int, quantity: int, status: str) -> int:
    """直接写一张游客订单（一行）并提交；返回订单 ID。写法同 tests/test_stock_reset.py。"""
    expires_at = NOW + timedelta(minutes=10)
    subtotal = PRICE * quantity
    with Session(engine) as session:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=subtotal,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=subtotal + SHIPPING,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"key-{generate_order_number()}",
            request_fingerprint="0" * 64,
            created_at=expires_at - timedelta(minutes=15),
            payment_expires_at=expires_at,
            paid_at=None,
        )
        session.add(order)
        session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                line_index=0,
                variant_id=variant_id,
                sku=SKU,
                product_name_en="Tee",
                product_name_zh="T恤",
                product_name_ms="Tee",
                variant_label_en="",
                variant_label_zh="",
                variant_label_ms="",
                unit_price_sen=PRICE,
                quantity=quantity,
                line_subtotal_sen=subtotal,
            )
        )
        order_id = order.id
        session.commit()
    return order_id


@pytest.fixture
def variant_id(engine: Engine) -> int:
    return _variant(engine)


@pytest.fixture
def account_id(engine: Engine) -> int:
    with Session(engine) as session:
        row = AdminAccount(
            username="admin@example.com",
            password_hash="not-a-real-hash",
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        session.add(row)
        session.flush()
        row_id = row.id
        session.commit()
    return row_id


@pytest.fixture
def token(engine: Engine, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    with Session(engine) as session:
        issued = issue_admin_session(session, account_id, _now() - timedelta(minutes=1))
        value = issued.token
        session.commit()
    return value


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


def _url(variant_id: int | str) -> str:
    return f"/api/admin/variants/{variant_id}/available-stock"


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _post(
    client: TestClient,
    token: str | None,
    variant_id: int | str,
    body: Any,
    *,
    csrf: str | None = "auto",
) -> Any:
    """csrf 默认按 token 算出正确的令牌；给 None 即不带请求头。"""
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    return client.post(_url(variant_id), json=body, headers=_headers(token, csrf))


def _post_raw(
    client: TestClient,
    token: str | None,
    variant_id: int | str,
    content: bytes,
    content_type: str | None,
) -> Any:
    headers = _headers(token, None if token is None else _csrf_for(token))
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.post(_url(variant_id), content=content, headers=headers)


def _stock(expected: Any, available_stock: Any) -> dict[str, Any]:
    return {"expected": expected, "available_stock": available_stock}


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _variant_row(engine: Engine, variant_id: int) -> tuple[Any, ...]:
    """SKU 代码、初始库存、当日可用库存、是否启用与所属商品的版本号。"""

    def read(session: Session) -> tuple[Any, ...]:
        stmt = (
            select(
                ProductVariant.sku,
                ProductVariant.daily_initial_stock,
                ProductVariant.available_stock,
                ProductVariant.is_active,
                Product.version,
            )
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.id == variant_id)
        )
        return tuple(session.execute(stmt).one())

    return _read(engine, read)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """全部 SKU（只查列值，按 ID）、商品版本号、订单状态与审计记录数。"""

    def read(session: Session) -> tuple[Any, ...]:
        variants_stmt = select(
            ProductVariant.id,
            ProductVariant.daily_initial_stock,
            ProductVariant.available_stock,
        ).order_by(ProductVariant.id)
        products_stmt = select(Product.id, Product.version).order_by(Product.id)
        orders_stmt = select(Order.id, Order.status).order_by(Order.id)
        return (
            [tuple(row) for row in session.execute(variants_stmt)],
            [tuple(row) for row in session.execute(products_stmt)],
            [tuple(row) for row in session.execute(orders_stmt)],
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


def _set_available(engine: Engine, variant_id: int, value: int) -> None:
    """另一处（访客下单或订单取消释放）改了当日可用库存并提交。"""

    def change(session: Session) -> None:
        stmt = update(ProductVariant).where(ProductVariant.id == variant_id)
        session.execute(stmt.values(available_stock=value))
        session.commit()

    _read(engine, change)


def _adjust(
    engine: Engine, account_id: int, variant_id: int, expected: Any, new_value: Any
) -> tuple[str, int]:
    """用一个新的数据库会话调用服务层。"""
    with Session(engine) as session:
        return adjust_available_stock(session, account_id, variant_id, expected, new_value, NOW)


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


def _changed(available_stock: int) -> dict[str, Any]:
    return {"detail": "stock_changed", "available_stock": available_stock}


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------


def test_service_adjusts_available_stock_and_writes_one_audit(
    engine: Engine, account_id: int, variant_id: int
) -> None:
    """第 12 条「管理员可把某个 SKU……的当日可用库存直接改为一个值（0–9,999 的整数），不改每日
    初始库存」「调整不改已有预留，也不改订单，不改商品的版本号；每次写入写一条审计（对象为该
    SKU，旧值与新值写成『SKU 代码 available_stock 值』）」：12 改为 30，初始库存与商品版本号
    不变，一条审计，返回 SKU 代码与调整后的值。"""
    assert _adjust(engine, account_id, variant_id, AVAILABLE, 30) == (SKU, 30)

    assert _variant_row(engine, variant_id) == (SKU, INITIAL, 30, True, 1)
    assert _audits(engine) == [
        (
            account_id,
            ADMIN_VARIANT_STOCK_ADJUSTED,
            AUDIT_TARGET_PRODUCT_VARIANT,
            variant_id,
            f"{SKU} available_stock {AVAILABLE}",
            f"{SKU} available_stock 30",
        )
    ]


@pytest.mark.parametrize(
    ("expected", "new_value"),
    [
        pytest.param(-1, 5, id="expected-negative"),
        pytest.param(AVAILABLE, -1, id="new-negative"),
        pytest.param(AVAILABLE, 10_000, id="new-too-large"),
        pytest.param(10_000, 5, id="expected-too-large"),
        pytest.param(True, 5, id="expected-bool"),
        pytest.param(AVAILABLE, False, id="new-bool"),
        pytest.param(AVAILABLE, "5", id="new-string"),
        pytest.param(AVAILABLE, 5.0, id="new-float"),
    ],
)
def test_service_rejects_values_outside_the_range_without_touching_the_database(
    engine: Engine, account_id: int, variant_id: int, expected: Any, new_value: Any
) -> None:
    """第 12 条「直接改为一个值（0–9,999 的整数）」；验收「先不碰数据库检查 expected 与
    new_value 都是 0 到 9,999 的整数（布尔值不算），不通过抛 StockInvalid」：-1、10000、
    布尔值、字符串与浮点数都抛 StockInvalid，不发任何 SQL，数据与审计不变。"""
    before = _snapshot(engine)
    statements: list[str] = []

    def _record(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        statements.append(str(clauseelement))

    event.listen(engine, "before_execute", _record)
    try:
        with pytest.raises(StockInvalid):
            _adjust(engine, account_id, variant_id, expected, new_value)
    finally:
        event.remove(engine, "before_execute", _record)

    assert statements == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("new_value", [0, 9_999])
def test_service_accepts_both_ends_of_the_range(
    engine: Engine, account_id: int, variant_id: int, new_value: int
) -> None:
    """第 12 条「0–9,999 的整数」：改为 0 与 9,999 都成功。"""
    assert _adjust(engine, account_id, variant_id, AVAILABLE, new_value) == (SKU, new_value)
    assert _variant_row(engine, variant_id)[2] == new_value


def test_service_stale_expected_raises_stock_changed_with_the_current_value(
    engine: Engine, account_id: int, variant_id: int
) -> None:
    """第 12 条「服务端以一条带条件的更新写入（当前值仍等于该值才写）：不等即回答『库存已
    变动』并返回当前的当日可用库存、不写入」：页面显示 12，其间访客下单扣到 9，拿 12 调整
    为 30 抛 StockChanged（当前值 9），数据与审计不变。"""
    _set_available(engine, variant_id, 9)
    before = _snapshot(engine)

    with pytest.raises(StockChanged) as caught:
        _adjust(engine, account_id, variant_id, AVAILABLE, 30)

    assert caught.value.available_stock == 9
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    ("expected", "new_value"),
    [pytest.param(AVAILABLE, 30, id="different"), pytest.param(AVAILABLE, AVAILABLE, id="same")],
)
def test_service_missing_variant_raises_variant_not_found(
    engine: Engine, account_id: int, variant_id: int, expected: int, new_value: int
) -> None:
    """验收「SKU 不存在抛 VariantNotFound」；第 12 条「管理员可把某个 SKU……的当日可用库存
    直接改为一个值」：不存在的 SKU 不论新值是否与调整前的值相同都抛 VariantNotFound，数据与
    审计不变。"""
    before = _snapshot(engine)

    with pytest.raises(VariantNotFound):
        _adjust(engine, account_id, MISSING_ID, expected, new_value)

    assert _snapshot(engine) == before


def test_update_statement_is_one_conditional_update() -> None:
    """第 12 条「服务端以一条带条件的更新写入（当前值仍等于该值才写）」：UPDATE 语句按 MySQL
    方言编译，只改 available_stock，条件为 SKU 规格 ID 与 available_stock 的等值。"""
    sql = str(adjust_stock_statement(7, 12, 30).compile(dialect=mysql.dialect()))

    assert sql.startswith("UPDATE product_variants SET available_stock=%s")
    assert "daily_initial_stock" not in sql
    assert "WHERE product_variants.id = %s AND product_variants.available_stock = %s" in sql


# ---------------------------------------------------------------------------
# 会话、CSRF、路径 ID 与请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_token", [None, "not-a-token", "A" * 43])
def test_adjust_requires_an_admin_session(
    client: TestClient, engine: Engine, variant_id: int, session_token: str | None
) -> None:
    """验收「require_admin（401）」；第 12 条「管理员可把……当日可用库存直接改为一个值」：
    没有 cookie、格式不对与库里没有的会话都 401 admin_session_required，结构不合格的请求体
    也先回答 401，数据与审计不变。"""
    before = _snapshot(engine)

    responses = [
        _post(client, session_token, variant_id, _stock(AVAILABLE, 30), csrf="0" * 64),
        _post(client, session_token, variant_id, {"note": SECRET}, csrf="0" * 64),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_adjust_without_a_valid_csrf_token_is_403(
    client: TestClient, engine: Engine, token: str, variant_id: int, csrf: str | None
) -> None:
    """验收「X-CSRF-Token（403 csrf_failed，照 app/api/admin_coupons.py 先声明 CsrfDep 依赖、
    再声明路径 ID 依赖）」：缺少、为空或不一致都 403 csrf_failed，且先于路径 ID 与请求体的
    422；数据与审计不变。"""
    before = _snapshot(engine)

    responses = [
        _post(client, token, variant_id, _stock(AVAILABLE, 30), csrf=csrf),
        _post(client, token, "0", _stock(AVAILABLE, 30), csrf=csrf),
        _post(client, token, variant_id, {"note": SECRET}, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_oversized_body_is_413_first(
    client: TestClient,
    engine: Engine,
    token: str,
    variant_id: int,
    content_type: str,
    logged_in: bool,
) -> None:
    """验收「请求体 8 KB 上限 413」，步骤在 415 与 require_admin 之前：超过 8 KB 时不是 JSON、
    没有会话、路径 ID 不合法也是 413；不回显请求内容，数据不变。"""
    before = _snapshot(engine)
    content = json.dumps({"note": SECRET * (MAX_BODY_BYTES // len(SECRET))}).encode()
    assert len(content) > MAX_BODY_BYTES

    for path_id in (variant_id, "0"):
        response = _post_raw(client, token if logged_in else None, path_id, content, content_type)
        _assert_error(response, 413, {"detail": "request body too large"})
        assert SECRET not in response.text
    assert _snapshot(engine) == before


@pytest.mark.parametrize("content_type", ["text/plain", None])
@pytest.mark.parametrize("logged_in", [True, False])
def test_non_json_is_415(
    client: TestClient,
    engine: Engine,
    token: str,
    variant_id: int,
    content_type: str | None,
    logged_in: bool,
) -> None:
    """验收「不是 JSON 415」，在 require_admin 之前：不是 JSON 时有没有会话都 415，数据
    不变。"""
    before = _snapshot(engine)
    content = json.dumps(_stock(AVAILABLE, 30)).encode()

    response = _post_raw(client, token if logged_in else None, variant_id, content, content_type)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "path_id",
    ["0", "-1", "abc", "1.5", "01", "+1", "2147483648", "9" * 11, SECRET],
)
def test_invalid_path_id_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, path_id: str
) -> None:
    """验收「路径 ID 照 app/api/admin_orders.py 的 _order_id 校验（不合法 422 不回显）」，
    且先于请求体：0、负数、非数字、小数、前导零、+1、超出 INT 上限与超长都 422，结构
    不合格的请求体也先回答路径的 422；数据与审计不变。"""
    before = _snapshot(engine)

    for body in (_stock(AVAILABLE, 30), {"note": SECRET}):
        response = _post(client, token, path_id, body)
        _assert_plain_errors(response)
        assert [error["loc"] for error in response.json()["detail"]] == [["path", "variant_id"]]
    assert _snapshot(engine) == before


MALFORMED = [
    pytest.param(_stock(True, 30), id="expected-bool"),
    pytest.param(_stock(AVAILABLE, False), id="new-bool"),
    pytest.param(_stock(AVAILABLE, SECRET), id="new-string"),
    pytest.param(_stock(str(AVAILABLE), 30), id="expected-numeric-string"),
    pytest.param(_stock(AVAILABLE, 30.0), id="new-float"),
    pytest.param(_stock(None, 30), id="expected-null"),
    pytest.param({"available_stock": 30}, id="missing-expected"),
    pytest.param({"expected": AVAILABLE}, id="missing-new"),
    pytest.param({**_stock(AVAILABLE, 30), "note": SECRET}, id="extra-field"),
    pytest.param([AVAILABLE, 30], id="not-an-object"),
    pytest.param(SECRET, id="bare-string"),
]


@pytest.mark.parametrize("body", MALFORMED)
def test_malformed_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, variant_id: int, body: Any
) -> None:
    """验收「请求体模型（expected 与 available_stock 两个必填整数，strict，多出字段 422，照
    app/api/pay.py 的 _body_errors 不回显）」；第 12 条「0–9,999 的整数」：布尔值、字符串、
    浮点数、null、缺字段与多出字段都 422，不回显所给的值，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_plain_errors(_post(client, token, variant_id, body))
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(_stock(AVAILABLE, -1), id="new-negative"),
        pytest.param(_stock(AVAILABLE, 10_000), id="new-too-large"),
        pytest.param(_stock(-1, 5), id="expected-negative"),
        pytest.param(_stock(10_000, 5), id="expected-too-large"),
    ],
)
def test_out_of_range_values_are_stock_invalid(
    client: TestClient, engine: Engine, token: str, variant_id: int, body: dict[str, Any]
) -> None:
    """第 12 条「直接改为一个值（0–9,999 的整数）」；HANDOFF 0.76「取值不合格回 422
    stock_invalid」：-1 与 10000 都 422 stock_invalid，数据与审计不变。"""
    before = _snapshot(engine)

    _assert_error(_post(client, token, variant_id, body), 422, STOCK_INVALID)
    assert _snapshot(engine) == before


def test_out_of_range_for_a_missing_variant_is_stock_invalid(
    client: TestClient, engine: Engine, token: str
) -> None:
    """验收「先不碰数据库检查……不通过抛 StockInvalid」：取值不合格先于 SKU 是否存在，
    不存在的 SKU 配 10000 回 422 stock_invalid 而不是 404。"""
    _assert_error(_post(client, token, MISSING_ID, _stock(AVAILABLE, 10_000)), 422, STOCK_INVALID)


# ---------------------------------------------------------------------------
# 调整
# ---------------------------------------------------------------------------


def test_adjust_changes_available_stock_only_and_writes_one_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, variant_id: int
) -> None:
    """第 12 条「管理员可把某个 SKU……的当日可用库存直接改为一个值……不改每日初始库存」
    「调整不改已有预留，也不改订单，不改商品的版本号；每次写入写一条审计（对象为该 SKU，
    旧值与新值写成『SKU 代码 available_stock 值』）」：12 改为 3，回 200 与 variant_id、sku、
    available_stock；初始库存、商品版本号与已有订单不变，一条审计，操作者为当前管理员。"""
    order_id = _order(engine, variant_id, 2, STATUS_AWAITING_PAYMENT)

    body = _ok(_post(client, token, variant_id, _stock(AVAILABLE, 3)))

    assert body == {"variant_id": variant_id, "sku": SKU, "available_stock": 3}
    assert list(body) == ["variant_id", "sku", "available_stock"]
    assert _variant_row(engine, variant_id) == (SKU, INITIAL, 3, True, 1)
    status = _read(engine, lambda s: s.scalar(select(Order.status).where(Order.id == order_id)))
    assert status == STATUS_AWAITING_PAYMENT
    assert _audits(engine) == [
        (
            account_id,
            ADMIN_VARIANT_STOCK_ADJUSTED,
            AUDIT_TARGET_PRODUCT_VARIANT,
            variant_id,
            f"{SKU} available_stock {AVAILABLE}",
            f"{SKU} available_stock 3",
        )
    ]


def test_each_write_writes_one_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, variant_id: int
) -> None:
    """第 12 条「每次写入写一条审计」：连续两次调整（12→0、0→9999）各写一条，旧值接着上一次
    的新值。"""
    _ok(_post(client, token, variant_id, _stock(AVAILABLE, 0)))
    _ok(_post(client, token, variant_id, _stock(0, 9_999)))

    assert [(audit[4], audit[5]) for audit in _audits(engine)] == [
        (f"{SKU} available_stock {AVAILABLE}", f"{SKU} available_stock 0"),
        (f"{SKU} available_stock 0", f"{SKU} available_stock 9999"),
    ]
    assert _variant_row(engine, variant_id)[2] == 9_999


def test_stale_expected_is_409_with_the_current_value(
    client: TestClient, engine: Engine, token: str, variant_id: int
) -> None:
    """第 12 条「请求带调整前页面显示的当日可用库存……当前值仍等于该值才写：不等即回答『库存
    已变动』并返回当前的当日可用库存、不写入——其间若有访客下单或订单取消释放，管理员看到的
    值已过期，不能把那次变动盖掉」；HANDOFF 0.76「库存已变动回 409 {"detail":"stock_changed",
    "available_stock":当前值}」：页面显示 12，其间一张订单扣了 2 件，拿 12 调整为 30 回 409
    与当前值 10，数据与审计不变。"""
    _order(engine, variant_id, 2, STATUS_AWAITING_PAYMENT)
    _set_available(engine, variant_id, AVAILABLE - 2)
    before = _snapshot(engine)

    response = _post(client, token, variant_id, _stock(AVAILABLE, 30))

    _assert_error(response, 409, _changed(AVAILABLE - 2))
    assert _snapshot(engine) == before


def test_same_value_is_200_without_writing(
    client: TestClient, engine: Engine, token: str, variant_id: int
) -> None:
    """第 12 条「先比调整前的值：相等之后，新值与当前值相同时照常回答成功，不写入、不记
    审计」：调整前 12、新值 12 回 200 与当前值，数据与审计不变。"""
    before = _snapshot(engine)

    body = _ok(_post(client, token, variant_id, _stock(AVAILABLE, AVAILABLE)))

    assert body == {"variant_id": variant_id, "sku": SKU, "available_stock": AVAILABLE}
    assert _snapshot(engine) == before


def test_same_value_with_stale_expected_is_409(
    client: TestClient, engine: Engine, token: str, variant_id: int
) -> None:
    """第 12 条「先比调整前的值：相等之后，新值与当前值相同时照常回答成功」「不等即回答『库存
    已变动』并返回当前的当日可用库存」：页面显示 12、新值也是 12，而当前已被取消释放加到 14，
    先比调整前的值不等，回 409 与当前值 14，数据与审计不变。"""
    _set_available(engine, variant_id, AVAILABLE + 2)
    before = _snapshot(engine)

    response = _post(client, token, variant_id, _stock(AVAILABLE, AVAILABLE))

    _assert_error(response, 409, _changed(AVAILABLE + 2))
    assert _snapshot(engine) == before


def test_inactive_variant_can_be_adjusted(
    client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 12 条「管理员可把某个 SKU（含停用的）的当日可用库存直接改为一个值」：停用的 SKU
    调整成功，仍停用，写一条审计。"""
    inactive_id = _variant(engine, sku="TEE-OLD", available=4, active=False)

    body = _ok(_post(client, token, inactive_id, _stock(4, 0)))

    assert body == {"variant_id": inactive_id, "sku": "TEE-OLD", "available_stock": 0}
    assert _variant_row(engine, inactive_id) == ("TEE-OLD", INITIAL, 0, False, 1)
    assert [audit[3:] for audit in _audits(engine)] == [
        (inactive_id, "TEE-OLD available_stock 4", "TEE-OLD available_stock 0")
    ]


@pytest.mark.parametrize(
    "body",
    [pytest.param(_stock(AVAILABLE, 30), id="different"), pytest.param(_stock(5, 5), id="same")],
)
def test_missing_variant_is_404(
    client: TestClient, engine: Engine, token: str, variant_id: int, body: dict[str, Any]
) -> None:
    """HANDOFF 0.76「SKU 不存在回 404 variant_not_found」；第 12 条「管理员可把某个 SKU……的
    当日可用库存直接改为一个值」：不存在的 SKU 不论新值是否与调整前的值相同都 404，数据与
    审计不变。"""
    before = _snapshot(engine)

    _assert_error(_post(client, token, MISSING_ID, body), 404, VARIANT_NOT_FOUND)
    assert _snapshot(engine) == before


def test_daily_reset_after_an_adjustment_recomputes_from_the_initial_stock(
    client: TestClient, engine: Engine, token: str, variant_id: int
) -> None:
    """第 12 条「下一次每日重置照第 6 条按初始库存减去仍有效的预留重算，调整不延续到那次重置
    之后」「调整不改已有预留，也不改订单」：一张待支付订单预留 3 件、一张已取消订单不算，
    管理员把 12 调为 1；之后 reset_daily_stock 把当日可用库存重算为 20 − 3 = 17，初始库存
    不变，待支付订单仍在。"""
    held_order = _order(engine, variant_id, 3, STATUS_AWAITING_PAYMENT)
    _order(engine, variant_id, 4, STATUS_CANCELLED)
    _ok(_post(client, token, variant_id, _stock(AVAILABLE, 1)))
    assert _variant_row(engine, variant_id)[1:3] == (INITIAL, 1)

    with Session(engine) as session:
        outcome = stock_reset.reset_daily_stock(session, NOW)

    assert outcome.performed
    assert _variant_row(engine, variant_id)[1:3] == (INITIAL, INITIAL - 3)
    status = _read(engine, lambda s: s.scalar(select(Order.status).where(Order.id == held_order)))
    assert status == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient,
    engine: Engine,
    token: str,
    variant_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验收「处理函数与依赖产生的全部响应（含错误）都带 no-store」「不写日志」：成功、未变、
    401、403、413、415、路径 422、请求体 422、stock_invalid、404 与 409 都带 no-store，
    应用不记日志。"""
    caplog.set_level(logging.DEBUG)
    oversized = json.dumps({"note": "x" * MAX_BODY_BYTES}).encode()
    responses = [
        _post(client, token, variant_id, _stock(AVAILABLE, 5)),
        _post(client, token, variant_id, _stock(5, 5)),
        _post(client, None, variant_id, _stock(5, 6)),
        _post(client, token, variant_id, _stock(5, 6), csrf=None),
        _post_raw(client, token, variant_id, oversized, "application/json"),
        _post_raw(client, token, variant_id, b"{}", "text/plain"),
        _post(client, token, "0", _stock(5, 6)),
        _post(client, token, variant_id, _stock(5, True)),
        _post(client, token, variant_id, _stock(5, 10_000)),
        _post(client, token, MISSING_ID, _stock(5, 6)),
        _post(client, token, variant_id, _stock(AVAILABLE, 6)),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 401, 403, 413, 415, 422, 422, 422, 404, 409]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_route_is_only_post(app: FastAPI) -> None:
    """验收「新增……POST /api/admin/variants/{variant_id}/available-stock」：该前缀下只有这一个
    路由。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/admin/variants")
        for method in operations
    }

    assert routes == {("POST", "/api/admin/variants/{variant_id}/available-stock")}

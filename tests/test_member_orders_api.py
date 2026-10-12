"""会员计价与下单接口：POST /api/member/checkout/quote 与 POST /api/member/orders
（app/api/member_orders.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1 条
  （下称「计价第 1 条」）：“优惠券先抵商品额，积分再抵剩余商品额，二者均不抵运费”。
- 「失败、并发与重试」第 1 条（下称「重试第 1 条」）：“相同键相同请求返回原结果，
  同键不同内容报冲突”；库存、券与积分预占及订单创建在同一事务，失败全部回滚。
- 「权限与资料保护」第 1 条（下称「权限第 1 条」）：已登录会员结账不再验证。
  第 2 条（下称「权限第 2 条」）：收货电话默认为会员手机号，可改为其他号码或虚构号码；
  会员改填的收货电话以收货国家作为默认区号解析，明确输入加号国家码时以该号码为准，
  只检查格式。
- docs/UX.md 0.10 的 P05（下称「UX P05」）：会员表单有优惠券代码与使用积分，
  摘要显示券与积分两行。
- docs/HANDOFF.md 0.47 记录的 Kelvin 2026-10-11 决定（下称「Kelvin 10-11」）：
  下单时券或积分已不可用即整单拒绝，不建订单、不预留库存。
每条测试的文档字符串写明它守住的那一句；没有直接原句的，写明是 SHOP-TASK-089 验收标准
里的约定（下称「验收」）。

用 SQLite 内存库按模型建表（引擎设置同 tests/test_guest_order.py），接口的会话依赖换成
每个请求一个绑定同一内存库的会话（可换成提交即抛错的子类）；会员会话经 SHOP-TASK-076
所用的 issue_member_session 签发并提交，cookie 与 CSRF 令牌照 tests/test_member_pw_api.py
放进请求头。积分经 earn_points 发放，已用的券额度经 reserve_coupon 预占。结果一律在另一个
数据库会话里读。Redis 默认未配置（限流放行）；限流的测试把可选 Redis 依赖换成内存替身。
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import phonenumbers
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Coupon,
    CouponUse,
    DemoFxRate,
    Member,
    Order,
    OrderItem,
    OrderRecipient,
    PointEntry,
    Product,
    ProductVariant,
    ShippingRate,
    SiteSetting,
)
from app.models.coupon import TYPE_PERCENT
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT, STATUS_PAID
from app.models.site import SITE_SETTING_ID
from app.services.coupons import reserve_coupon
from app.services.member_auth import csrf_token_for_cookie, issue_member_session
from app.services.points import earn_points
from app.services.rate_limit import get_optional_redis_client
from app.services.shipping import convert_sen

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

QUOTE_URL = "/api/member/checkout/quote"
ORDER_URL = "/api/member/orders"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_member_session"
NO_STORE = "no-store"
SESSION_REQUIRED = "member_session_required"

MEMBER_PHONE = "+60123456789"
PHONE_LOCAL = "012-345 6789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"
COUPON_CODE = "TENOFF"
SOURCE = "203.0.113.5"

# 计价第 4 条算例：A 商品 2 件、各 RM10；B 商品 1 件、RM5。寄马来西亚 MY-10，运费 RM8。
LINES = [{"sku": "A-SKU", "quantity": 2}, {"sku": "B-SKU", "quantity": 1}]
SUBTOTAL = 2500
SHIPPING = 800
SG_SHIPPING = 2000
OTHER_SHIPPING = 3000
SG_RATE = Decimal("0.310000")
STOCK = (5, 3)

ORDER_FIELDS = {
    "order_number",
    "status",
    "subtotal_sen",
    "coupon_discount_sen",
    "points_redeemed",
    "shipping_fee_sen",
    "total_sen",
    "points_earned",
    "payment_expires_at",
}
MEMBER_QUOTE_FIELDS = {
    "coupon_applied",
    "coupon_discount_sen",
    "points_available",
    "points_max",
    "points_accepted",
    "points_redeemed",
    "points_earned",
}
GUEST_QUOTE_FIELDS = {
    "lines",
    "subtotal_sen",
    "shipping",
    "total_sen",
    "fx_reference",
    "can_place_order",
}

# 省略该字段。
OMIT = object()

_ORDER_SEQUENCE = itertools.count(1)
_KEY_SEQUENCE = itertools.count(1)
_PHONE_SEQUENCE = itertools.count(100_000_000)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """可选 Redis 依赖的内存替身：只有限流用到的事务管道（INCR 与 EXPIRE NX）。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.keys: list[str] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.keys = []

    def incr(self, key: str) -> FakePipeline:
        self.keys.append(key)
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        assert nx
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for key in self.keys:
            self.store.values[key] = self.store.values.get(key, 0) + 1
            results.extend([self.store.values[key], True])
        return results


class CommitFailingSession(Session):
    """提交时抛出数据库异常（模拟提交时连接断开）。"""

    def commit(self) -> None:
        raise OperationalError("COMMIT", {}, Exception("gone away"))


def rate_key(bucket: str, source: str = SOURCE) -> str:
    # 键另算：固定前缀 + 桶名 + 来源的 SHA-256。
    return f"acuven_shop:rate_limit:{bucket}:{hashlib.sha256(source.encode()).hexdigest()}"


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
def session_class() -> list[type[Session]]:
    # 测试可把它换成 CommitFailingSession。
    return [Session]


@pytest.fixture
def client(engine: Engine, session_class: list[type[Session]]) -> TestClient:
    # 连接串显式为空：Redis 未配置，计价与下单的限流放行。
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def _session() -> Iterator[Session]:
        with session_class[0](engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    # https：会员会话 cookie 带 Secure。
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _write[R](engine: Engine, action: Callable[[Session], R]) -> R:
    with Session(engine) as session:
        result = action(session)
        session.commit()
        return result


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def catalog(engine: Engine) -> None:
    """A 商品 A-SKU 1000 仙、库存 5；B 商品 B-SKU 500 仙、库存 3、每单限购 2。
    运费：MY-10 州属行 800、SG 国家行 2000、兜底行 3000；SG 有演示汇率。
    """

    def action(session: Session) -> None:
        category = _add(session, Category(slug="goods", name_en="Goods", is_active=True))
        for slug, sku, price, stock, limit in (
            ("a", "A-SKU", 1000, STOCK[0], 10),
            ("b", "B-SKU", 500, STOCK[1], 2),
        ):
            product = Product(
                category_id=category.id,
                slug=slug,
                name_en=slug.upper(),
                description_en=f"{slug}.",
                is_active=True,
                max_per_order=limit,
            )
            _add(session, product)
            variant = ProductVariant(
                product_id=product.id,
                sku=sku,
                price_sen=price,
                daily_initial_stock=stock,
                available_stock=stock,
                is_active=True,
            )
            _add(session, variant)
        session.add_all(
            [
                ShippingRate(zone_type="my_state", zone_code="MY-10", fee_sen=SHIPPING, version=1),
                ShippingRate(zone_type="country", zone_code="SG", fee_sen=SG_SHIPPING, version=3),
                ShippingRate(
                    zone_type="other", zone_code="OTHER", fee_sen=OTHER_SHIPPING, version=2
                ),
                DemoFxRate(
                    country_code="SG",
                    currency_code="SGD",
                    currency_decimals=2,
                    rate=SG_RATE,
                    version=5,
                ),
            ]
        )

    _write(engine, action)


def add_member(engine: Engine, phone: str | None = None) -> int:
    phone = phone or f"+601{next(_PHONE_SEQUENCE)}"
    member = Member(phone=phone, status=MEMBER_ACTIVE, created_at=_now() - timedelta(days=100))
    return _write(engine, lambda session: _add(session, member).id)


@pytest.fixture
def member_id(engine: Engine, catalog: None) -> int:
    return add_member(engine, MEMBER_PHONE)


@pytest.fixture
def token(engine: Engine, member_id: int) -> str:
    """经 issue_member_session 签发并提交的会话令牌原文。"""

    def action(session: Session) -> str:
        member = session.get(Member, member_id)
        assert member is not None
        return issue_member_session(session, member, _now()).token

    return _write(engine, action)


def _prior_order(session: Session, member_id: int) -> Order:
    """该会员昨天已支付的一张订单，供发放积分或预占券额度。"""
    n = next(_ORDER_SEQUENCE)
    earned_at = _now() - timedelta(days=1)
    order = Order(
        order_number=f"{n:016d}",
        status=STATUS_PAID,
        subtotal_sen=6000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=800,
        total_sen=6800,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"prior-key-{n:08d}",
        request_fingerprint="0f" * 32,
        created_at=earned_at,
        payment_expires_at=earned_at + timedelta(minutes=15),
        paid_at=earned_at,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    return _add(session, order)


def earn(engine: Engine, member_id: int, points: int) -> None:
    """经 earn_points 给该会员一个昨天取得的批次。"""

    def action(session: Session) -> None:
        order = _prior_order(session, member_id)
        earned_at = (_now() - timedelta(days=1)).replace(microsecond=0)
        assert earn_points(session, member_id, order.id, points, earned_at) is not None

    _write(engine, action)


def add_coupon(engine: Engine, **overrides: Any) -> int:
    """10% 券 TENOFF，十天前起、三十天后止，不设最低消费，两个上限都不限。"""
    now = _now()
    values: dict[str, Any] = {
        "code": COUPON_CODE,
        "coupon_type": TYPE_PERCENT,
        "amount_off_sen": None,
        "percent_off": 10,
        "min_subtotal_sen": 0,
        "starts_at": now - timedelta(days=10),
        "ends_at": now + timedelta(days=30),
        "total_use_limit": None,
        "per_member_use_limit": None,
        "is_active": True,
        "created_at": now - timedelta(days=10),
    }
    coupon = Coupon(**(values | overrides))
    return _write(engine, lambda session: _add(session, coupon).id)


def use_up_coupon(engine: Engine, coupon_id: int) -> None:
    """另一会员的已付订单预占了这张券，用满总次数 1。"""
    other_id = add_member(engine)

    def action(session: Session) -> None:
        order = _prior_order(session, other_id)
        check = reserve_coupon(
            session, coupon_id, order.id, other_id, 100, 6000, _now() - timedelta(hours=1)
        )
        assert check.ok

    _write(engine, action)


def set_switch(engine: Engine, value: bool) -> None:
    def action(session: Session) -> None:
        row = session.get(SiteSetting, SITE_SETTING_ID)
        if row is None:
            session.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=value))
        else:
            row.sms_verification_enabled = value

    _write(engine, action)


def state(engine: Engine) -> dict[str, Any]:
    """会被下单改动的数据：各表行数与两个规格的可用库存。"""

    def action(session: Session) -> dict[str, Any]:
        models = (Order, OrderItem, OrderRecipient, CouponUse, PointEntry)
        result: dict[str, Any] = {
            model.__name__: session.scalar(select(func.count()).select_from(model))
            for model in models
        }
        stock = select(ProductVariant.available_stock).order_by(ProductVariant.id)
        result["stock"] = tuple(session.scalars(stock))
        return result

    return _read(engine, action)


def recipient_phone(engine: Engine, order_number: str) -> str:
    stmt = (
        select(OrderRecipient.phone)
        .join(Order, Order.id == OrderRecipient.order_id)
        .where(Order.order_number == order_number)
    )
    return _read(engine, lambda session: session.scalars(stmt).one())


def key() -> str:
    return f"member-key-{next(_KEY_SEQUENCE):08d}"


def _body(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = defaults | overrides
    return {name: value for name, value in merged.items() if value is not OMIT}


def quote_body(**overrides: Any) -> dict[str, Any]:
    """算例的购物车，寄 MY-10，10% 券与 300 积分。"""
    defaults = {
        "lines": LINES,
        "country_code": "MY",
        "state_code": "MY-10",
        "coupon_code": COUPON_CODE,
        "points": 300,
    }
    return _body(defaults, overrides)


def order_body(**overrides: Any) -> dict[str, Any]:
    """算例的购物车，寄 MY-10，收货电话为会员手机号的本地写法，10% 券与 300 积分。"""
    defaults = {
        "lines": LINES,
        "phone": PHONE_LOCAL,
        "name": NAME,
        "address": ADDRESS,
        "postal_code": POSTAL,
        "country_code": "MY",
        "state_code": "MY-10",
        "coupon_code": COUPON_CODE,
        "points": 300,
    }
    return _body(defaults, overrides)


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def order_headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    """会话 cookie、正确的 CSRF 令牌与幂等键。"""
    csrf = csrf_token_for_cookie(token)
    assert csrf is not None
    return {
        **cookie_header(token),
        "X-CSRF-Token": csrf,
        "Idempotency-Key": idempotency_key or key(),
    }


def assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == NO_STORE


def assert_not_echoed(response: Any, *values: str) -> None:
    for value in (NAME, ADDRESS, PHONE_LOCAL, MEMBER_PHONE, COUPON_CODE, *values):
        assert value not in response.text


# ---------------------------------------------------------------------------
# 检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [QUOTE_URL, ORDER_URL])
def test_oversized_body_is_413_before_everything(client: TestClient, url: str) -> None:
    """验收：“请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验”——
    不是 JSON、没有会话也先是 413。
    """
    response = client.post(
        url, content=b"x" * (MAX_BODY_BYTES + 1), headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 413
    assert_no_store(response)


@pytest.mark.parametrize("url", [QUOTE_URL, ORDER_URL])
def test_not_json_is_415_before_session(client: TestClient, url: str) -> None:
    """验收：“不是 JSON 415”，在 require_member 之前。"""
    response = client.post(url, content=b"{}", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert_no_store(response)


@pytest.mark.parametrize("url", [QUOTE_URL, ORDER_URL])
def test_without_session_is_401_before_body_checks(client: TestClient, url: str) -> None:
    """权限第 1 条“已登录会员结账”：没有会员会话一律 401 member_session_required；
    验收：请求体恰好 8 KB 不是 413，结构不对也先是 401（请求体在会话之后才校验）。
    """
    response = client.post(
        url, content=b" " * MAX_BODY_BYTES, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": SESSION_REQUIRED}
    assert_no_store(response)


def test_order_without_csrf_is_403_and_writes_nothing(
    client: TestClient, engine: Engine, token: str
) -> None:
    """验收：“只有下单再经 require_member_csrf（403 csrf_failed）”，先于请求体校验，
    不建订单、不扣库存。
    """
    before = state(engine)
    for headers in (
        {**cookie_header(token), "Idempotency-Key": key()},
        {**cookie_header(token), "Idempotency-Key": key(), "X-CSRF-Token": "0" * 64},
    ):
        response = client.post(ORDER_URL, json={"unexpected": True}, headers=headers)
        assert response.status_code == 403
        assert response.json() == {"detail": "csrf_failed"}
        assert_no_store(response)
    assert state(engine) == before


def test_quote_does_not_need_csrf(client: TestClient, token: str) -> None:
    """验收：只有下单再经 require_member_csrf；计价只读，不要 CSRF 令牌。"""
    response = client.post(QUOTE_URL, json=quote_body(), headers=cookie_header(token))
    assert response.status_code == 200
    assert_no_store(response)


@pytest.mark.parametrize(
    ("url", "make_body"),
    [(QUOTE_URL, quote_body), (ORDER_URL, order_body)],
)
def test_extra_field_is_422_without_echo(
    client: TestClient, token: str, url: str, make_body: Callable[..., dict[str, Any]]
) -> None:
    """验收：“多出字段 422，422 每条错误只给位置、类型与固定消息、不回显请求内容”；
    下单不收 phone_region。
    """
    secret = "SECRET-EXTRA-VALUE"
    for extra in ({"phone_region": secret}, {"price_sen": secret}):
        response = client.post(url, json=make_body(**extra), headers=order_headers(token))
        assert response.status_code == 422
        assert_no_store(response)
        errors = response.json()["detail"]
        assert errors
        for error in errors:
            assert set(error) == {"type", "loc", "msg"}
        assert any(error["type"] == "extra_forbidden" for error in errors)
        assert_not_echoed(response, secret)


@pytest.mark.parametrize(
    "overrides",
    [
        {"coupon_code": ""},
        {"coupon_code": "X" * 65},
        {"coupon_code": 123},
        {"points": -1},
        {"points": 100_000_001},
        {"points": 2.0},
        {"points": True},
        {"points": "300"},
        {"lines": LINES + [{"sku": "A-SKU", "quantity": 1}]},
        {"lines": []},
    ],
)
def test_quote_field_rules(client: TestClient, token: str, overrides: dict[str, Any]) -> None:
    """验收：coupon_code 为 1 到 64 个字符的字符串，points 为 0 到 100000000 的整数，
    lines 同计价接口（1 到 20 行，SKU 不重复）；不合即 422。
    """
    response = client.post(QUOTE_URL, json=quote_body(**overrides), headers=cookie_header(token))
    assert response.status_code == 422
    assert_no_store(response)


def test_order_needs_idempotency_key(client: TestClient, engine: Engine, token: str) -> None:
    """验收：Idempotency-Key 的规则同 POST /api/orders/guest；缺少或不合即 422，不建订单。"""
    before = state(engine)
    headers = order_headers(token)
    del headers["Idempotency-Key"]
    for extra in ({}, {"Idempotency-Key": "short"}):
        response = client.post(ORDER_URL, json=order_body(), headers=headers | extra)
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["header", "Idempotency-Key"]
        assert_no_store(response)
    assert state(engine) == before


def test_order_duplicate_sku_is_422(client: TestClient, engine: Engine, token: str) -> None:
    """验收：下单的 lines 规则同 POST /api/orders/guest（与计价接口相同，SKU 不重复）；
    重复 SKU 在请求校验即 422，不进入下单函数，不建订单、不扣库存。
    """
    before = state(engine)
    lines = LINES + [{"sku": "A-SKU", "quantity": 1}]
    response = client.post(ORDER_URL, json=order_body(lines=lines), headers=order_headers(token))
    assert response.status_code == 422
    assert_no_store(response)
    errors = response.json()["detail"]
    duplicate = {
        "type": "value_error",
        "loc": ["body", "lines"],
        "msg": "each SKU may appear only once",
    }
    assert duplicate in errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert state(engine) == before


def test_quote_invalid_lang_is_422_without_echo(client: TestClient, token: str) -> None:
    """验收：“lang 查询参数同计价接口”，且“422 每条错误只给位置、类型与固定消息、
    不回显请求内容”——不合法的 lang 不出现在响应里。
    """
    secret = "SECRET-LANG-VALUE"
    response = client.post(
        QUOTE_URL, params={"lang": secret}, json=quote_body(), headers=cookie_header(token)
    )
    assert response.status_code == 422
    assert_no_store(response)
    errors = response.json()["detail"]
    msg = "Input should be 'en', 'zh' or 'ms'"
    assert errors == [{"type": "literal_error", "loc": ["query", "lang"], "msg": msg}]
    assert_not_echoed(response, secret)


@pytest.mark.parametrize(
    ("url", "bucket", "limit"),
    [(QUOTE_URL, "checkout_quote_source", 300), (ORDER_URL, "guest_order_source", 60)],
)
def test_source_limit_shares_guest_buckets_before_session(
    client: TestClient, url: str, bucket: str, limit: int
) -> None:
    """验收：按访客来源限流，计价与 POST /api/checkout/quote 共用桶 checkout_quote_source，
    下单与 POST /api/orders/guest 共用桶 guest_order_source，及其上限；
    限流在 require_member 之前。
    """
    fake = FakeRedis()
    client.app.dependency_overrides[get_optional_redis_client] = lambda: fake
    headers = {"X-Real-IP": SOURCE}

    response = client.post(url, json={}, headers=headers)
    assert response.status_code == 401
    assert fake.values == {rate_key(bucket): 1}

    fake.values[rate_key(bucket)] = limit
    response = client.post(url, json={}, headers=headers)
    assert response.status_code == 429
    assert response.json() == {"detail": "rate_limited"}
    assert_no_store(response)


# ---------------------------------------------------------------------------
# 计价
# ---------------------------------------------------------------------------


def test_quote_shows_coupon_and_points_lines(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """UX P05“摘要显示券与积分两行”；计价第 1 条“优惠券先抵商品额，积分再抵剩余商品额，
    二者均不抵运费”：10% 券减 250，300 积分抵 300，合计为 1950 加运费；只读，不写库。
    """
    add_coupon(engine)
    earn(engine, member_id, 300)
    before = state(engine)

    response = client.post(QUOTE_URL, json=quote_body(), headers=cookie_header(token))

    assert response.status_code == 200
    assert_no_store(response)
    body = response.json()
    assert set(body) == GUEST_QUOTE_FIELDS | MEMBER_QUOTE_FIELDS
    assert body["subtotal_sen"] == SUBTOTAL
    assert body["shipping"]["fee_sen"] == SHIPPING
    assert body["can_place_order"] is True
    assert body["coupon_applied"] is True
    assert body["coupon_discount_sen"] == 250
    assert body["points_available"] == 300
    assert body["points_max"] == 300
    assert body["points_accepted"] is True
    assert body["points_redeemed"] == 300
    assert body["points_earned"] == 19
    assert body["total_sen"] == SUBTOTAL - 250 - 300 + SHIPPING
    assert body["fx_reference"] is None
    assert state(engine) == before
    assert COUPON_CODE not in response.text


def test_quote_unusable_coupon_is_false_and_not_counted(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """验收：“券不可用时 coupon_applied 为 false 且不计入”（已过期的券）；积分照常抵扣。"""
    now = _now()
    add_coupon(engine, starts_at=now - timedelta(days=30), ends_at=now - timedelta(days=1))
    earn(engine, member_id, 300)

    response = client.post(QUOTE_URL, json=quote_body(), headers=cookie_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["coupon_applied"] is False
    assert body["coupon_discount_sen"] == 0
    assert body["points_max"] == 300
    assert body["points_redeemed"] == 300
    assert body["total_sen"] == SUBTOTAL - 300 + SHIPPING


def test_quote_without_coupon_code_or_points(client: TestClient, token: str) -> None:
    """验收：coupon_applied “未给代码……时为 null”；points 可省略、默认 0。"""
    body = quote_body(coupon_code=OMIT, points=OMIT)
    response = client.post(QUOTE_URL, json=body, headers=cookie_header(token))

    assert response.status_code == 200
    result = response.json()
    assert result["coupon_applied"] is None
    assert result["coupon_discount_sen"] == 0
    assert result["points_available"] == 0
    assert result["points_max"] == 0
    assert result["points_accepted"] is True
    assert result["points_redeemed"] == 0
    assert result["total_sen"] == SUBTOTAL + SHIPPING


def test_quote_points_over_cap_not_accepted(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """计价第 1 条“积分再抵剩余商品额”：抵扣积分超过可用余额时不接受、不计入。"""
    add_coupon(engine)
    earn(engine, member_id, 300)

    response = client.post(QUOTE_URL, json=quote_body(points=400), headers=cookie_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["points_available"] == 300
    assert body["points_max"] == 300
    assert body["points_accepted"] is False
    assert body["points_redeemed"] == 0
    assert body["total_sen"] == SUBTOTAL - 250 + SHIPPING


def test_quote_fx_reference_is_for_total_after_deductions(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """验收：“total_sen 与 fx_reference 为扣除券与积分之后的合计及其参考外币”。"""
    add_coupon(engine)
    earn(engine, member_id, 300)
    body = quote_body(country_code="SG", state_code=OMIT)

    response = client.post(QUOTE_URL, json=body, headers=cookie_header(token))

    assert response.status_code == 200
    result = response.json()
    total = SUBTOTAL - 250 - 300 + SG_SHIPPING
    assert result["total_sen"] == total
    assert result["fx_reference"]["currency_code"] == "SGD"
    assert result["fx_reference"]["amount_minor"] == convert_sen(total, SG_RATE, 2)


def test_quote_without_destination_has_no_total(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """验收：合计与参考外币“未给收货国家时为 null”；券与积分照样判定。"""
    add_coupon(engine)
    earn(engine, member_id, 300)
    body = quote_body(country_code=OMIT, state_code=OMIT)

    response = client.post(QUOTE_URL, json=body, headers=cookie_header(token))

    assert response.status_code == 200
    result = response.json()
    assert (result["total_sen"], result["fx_reference"], result["shipping"]) == (None, None, None)
    assert result["coupon_applied"] is True
    assert result["points_redeemed"] == 300


def test_quote_invalid_destination_and_missing_rate(client: TestClient, token: str) -> None:
    """验收：“运费缺失与无效目的地同计价接口（503 与 422）”。"""
    headers = cookie_header(token)
    response = client.post(QUOTE_URL, json=quote_body(state_code="MY-99"), headers=headers)
    assert response.status_code == 422
    assert_no_store(response)
    response = client.post(QUOTE_URL, json=quote_body(state_code="MY-05"), headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "shipping is unavailable"}
    assert_no_store(response)


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------


def test_order_created_then_replayed(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """重试第 1 条“相同键相同请求返回原结果”：新订单 201、重放 200，
    库存、券与积分只预占一次；验收：响应只有订单号、状态、金额、获得积分
    与带 UTC 时区的支付到期时间，不设置 cookie。
    """
    add_coupon(engine)
    earn(engine, member_id, 300)
    before = state(engine)
    headers = order_headers(token)

    response = client.post(ORDER_URL, json=order_body(), headers=headers)

    assert response.status_code == 201
    assert_no_store(response)
    assert "set-cookie" not in response.headers
    body = response.json()
    assert set(body) == ORDER_FIELDS
    assert body["status"] == STATUS_AWAITING_PAYMENT
    assert (body["subtotal_sen"], body["coupon_discount_sen"]) == (SUBTOTAL, 250)
    assert (body["points_redeemed"], body["shipping_fee_sen"]) == (300, SHIPPING)
    assert (body["total_sen"], body["points_earned"]) == (1950 + SHIPPING, 19)
    expires = datetime.fromisoformat(body["payment_expires_at"])
    assert expires.utcoffset() == timedelta(0)
    assert_not_echoed(response, headers["Idempotency-Key"], token)

    after = state(engine)
    assert after["Order"] == before["Order"] + 1
    assert after["CouponUse"] == before["CouponUse"] + 1
    assert after["stock"] == (STOCK[0] - 2, STOCK[1] - 1)
    assert recipient_phone(engine, body["order_number"]) == MEMBER_PHONE

    replay = client.post(ORDER_URL, json=order_body(), headers=headers)
    assert replay.status_code == 200
    assert_no_store(replay)
    assert "set-cookie" not in replay.headers
    assert replay.json() == body
    assert state(engine) == after


def test_replay_uses_normalized_phone(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """验收：收货电话的“规范化结果写入收货资料并计入请求指纹”——同一号码换一种写法
    重放 200，另一个号码 409 idempotency_conflict。
    """
    headers = order_headers(token)
    body = order_body(coupon_code=OMIT, points=OMIT)
    first = client.post(ORDER_URL, json=body, headers=headers)
    assert first.status_code == 201
    after = state(engine)

    same = client.post(ORDER_URL, json=body | {"phone": "+60 12-345 6789"}, headers=headers)
    assert same.status_code == 200
    assert same.json() == first.json()

    other = client.post(ORDER_URL, json=body | {"phone": "+60123456780"}, headers=headers)
    assert other.status_code == 409
    assert other.json() == {"detail": "idempotency_conflict"}
    assert_no_store(other)
    assert state(engine) == after


def test_same_key_different_request_conflicts(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """重试第 1 条“同键不同内容报冲突”：抵扣积分不同即 409，不改原订单与库存。"""
    add_coupon(engine)
    earn(engine, member_id, 300)
    headers = order_headers(token)
    assert client.post(ORDER_URL, json=order_body(), headers=headers).status_code == 201
    after = state(engine)

    response = client.post(ORDER_URL, json=order_body(points=200), headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "idempotency_conflict"}
    assert_no_store(response)
    assert state(engine) == after


def test_used_up_coupon_rejects_order(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """Kelvin 10-11“下单时券或积分已不可用即整单拒绝，不建订单、不预留库存”：
    券被用满后下单 409 coupon_invalid（页面的 checkout.coupon_invalid），库存未扣。
    """
    coupon_id = add_coupon(engine, total_use_limit=1)
    use_up_coupon(engine, coupon_id)
    earn(engine, member_id, 300)
    before = state(engine)

    response = client.post(ORDER_URL, json=order_body(), headers=order_headers(token))

    assert response.status_code == 409
    assert response.json() == {"detail": "coupon_invalid"}
    assert_no_store(response)
    assert state(engine) == before
    assert before["stock"] == STOCK


def test_points_over_cap_rejects_order(
    client: TestClient, engine: Engine, member_id: int, token: str
) -> None:
    """Kelvin 10-11“下单时券或积分已不可用即整单拒绝”：抵扣积分超过可用余额，
    409 points_unavailable，不建订单、库存未扣。
    """
    add_coupon(engine)
    earn(engine, member_id, 300)
    before = state(engine)

    body = order_body(points=400)
    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 409
    assert response.json() == {"detail": "points_unavailable"}
    assert_no_store(response)
    assert state(engine) == before


def test_unplaceable_order_is_409(client: TestClient, engine: Engine, token: str) -> None:
    """验收：409 order_not_placeable（超出每单限购，整单拒绝），不建订单。"""
    before = state(engine)
    body = order_body(lines=[{"sku": "B-SKU", "quantity": 3}], coupon_code=OMIT, points=OMIT)

    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 409
    assert response.json() == {"detail": "order_not_placeable"}
    assert state(engine) == before


def test_order_missing_rate_is_503(client: TestClient, engine: Engine, token: str) -> None:
    """验收：下单“运费缺失 503”，同计价接口；不建订单。"""
    before = state(engine)
    body = order_body(state_code="MY-05", coupon_code=OMIT, points=OMIT)

    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 503
    assert response.json() == {"detail": "shipping is unavailable"}
    assert_no_store(response)
    assert state(engine) == before


def test_database_error_rolls_back_to_503(
    client: TestClient,
    engine: Engine,
    member_id: int,
    token: str,
    session_class: list[type[Session]],
) -> None:
    """验收：“数据库出错时回滚，503 service_unavailable”；重试第 1 条：失败全部回滚。"""
    add_coupon(engine)
    earn(engine, member_id, 300)
    before = state(engine)
    session_class[0] = CommitFailingSession

    response = client.post(ORDER_URL, json=order_body(), headers=order_headers(token))

    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert_no_store(response)
    assert state(engine) == before


# ---------------------------------------------------------------------------
# 收货电话
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "phone", "expected"),
    [
        ("MY", "012-345 6789", "+60123456789"),
        ("SG", "8123 4567", "+6581234567"),
        ("SG", "+60 12-345 6789", "+60123456789"),
        ("MY", "+65 8123 4567", "+6581234567"),
    ],
)
def test_phone_uses_destination_as_default_region(
    client: TestClient, engine: Engine, token: str, country: str, phone: str, expected: str
) -> None:
    """权限第 2 条：会员改填的收货电话“以收货国家作为默认区号解析，明确输入加号国家码时
    以该号码为准”；可改为会员手机号以外的号码。
    """
    state_code = "MY-10" if country == "MY" else OMIT
    body = order_body(
        country_code=country, state_code=state_code, phone=phone, coupon_code=OMIT, points=OMIT
    )

    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 201
    assert recipient_phone(engine, response.json()["order_number"]) == expected


def test_unsupported_country_needs_plus_prefix(
    client: TestClient, engine: Engine, token: str
) -> None:
    """验收：收货国家不在 phonenumbers.SUPPORTED_REGIONS 里时，以加号开头的输入以该号码为准，
    否则 422 phone_invalid。
    """
    assert "ZZ" not in phonenumbers.SUPPORTED_REGIONS
    body = order_body(country_code="ZZ", state_code=OMIT, coupon_code=OMIT, points=OMIT)
    headers = order_headers(token)

    response = client.post(ORDER_URL, json=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    ]

    response = client.post(ORDER_URL, json=body | {"phone": "+65 8123 4567"}, headers=headers)
    assert response.status_code == 201
    assert response.json()["shipping_fee_sen"] == OTHER_SHIPPING
    assert recipient_phone(engine, response.json()["order_number"]) == "+6581234567"


@pytest.mark.parametrize("phone", ["12", "012-345 67890123", "call me", "+0 123", ""])
def test_impossible_phone_is_422(
    client: TestClient, engine: Engine, token: str, phone: str
) -> None:
    """权限第 2 条“只检查格式”：格式不成立 422（类型 phone_invalid），
    不回显号码，不建订单。
    """
    before = state(engine)
    body = order_body(phone=phone, coupon_code=OMIT, points=OMIT)

    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"type": "phone_invalid", "loc": ["body", "phone"], "msg": "invalid phone number"}
    ]
    assert_no_store(response)
    if phone:
        assert phone not in response.text
    assert state(engine) == before


def test_whitelisted_member_orders_with_sms_switch_on(
    client: TestClient, engine: Engine, token: str
) -> None:
    """权限第 1 条“已登录会员结账不再验证”：短信验证开关开启时，马新号码的会员照常下单，
    不读开关、不按白名单拒绝。
    """
    set_switch(engine, True)
    body = order_body(coupon_code=OMIT, points=OMIT)

    response = client.post(ORDER_URL, json=body, headers=order_headers(token))

    assert response.status_code == 201
    assert response.json()["total_sen"] == SUBTOTAL + SHIPPING
    assert recipient_phone(engine, response.json()["order_number"]) == MEMBER_PHONE


def test_deleted_member_session_is_401(client: TestClient, engine: Engine, token: str) -> None:
    """验收：两个接口在会员不是 active 时都为 401 member_session_required，不建订单。"""
    # 照注销的结果直接改库：清空号码、状态改为 deleted。
    stmt = (
        update(Member)
        .where(Member.phone == MEMBER_PHONE)
        .values(phone=None, status="deleted", deleted_at=_now())
    )
    _write(engine, lambda session: session.execute(stmt))
    before = state(engine)
    for url, body in ((QUOTE_URL, quote_body()), (ORDER_URL, order_body())):
        response = client.post(url, json=body, headers=order_headers(token))
        assert response.status_code == 401
        assert response.json() == {"detail": SESSION_REQUIRED}
        assert_no_store(response)
    assert state(engine) == before


def test_no_logs(
    client: TestClient,
    engine: Engine,
    member_id: int,
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验收：“本模块不写日志”；任何日志都不含收货资料、券代码、幂等键或令牌。"""
    add_coupon(engine)
    earn(engine, member_id, 300)
    headers = order_headers(token)
    with caplog.at_level(logging.DEBUG):
        client.post(QUOTE_URL, json=quote_body(), headers=headers)
        client.post(ORDER_URL, json=order_body(), headers=headers)
        client.post(ORDER_URL, json=order_body(points=1), headers=headers)
    assert not [record for record in caplog.records if record.name.startswith("app")]
    for value in (NAME, ADDRESS, PHONE_LOCAL, COUPON_CODE, headers["Idempotency-Key"], token):
        assert value not in caplog.text

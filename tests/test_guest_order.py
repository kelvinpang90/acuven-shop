"""游客下单接口 POST /api/orders/guest。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「计价、优惠、积分与库存」
第 1、2、3、6、8 条；「数据模型」的 Order / OrderItem、OrderRecipient、
PaymentAttempt / OrderEvent 三行；「失败、并发与重试」第 1、2 条；
「边界与原则」第 3、4 条；「权限与资料保护」第 1、2、6、7 条。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句；
没有直接原句的，写明是 SHOP-TASK-020 验收标准里的实现约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。
接口的会话依赖换成每个请求一个绑定同一内存库的会话，与正式的
get_session 一样；测试自己的会话写完数据先提交。内存库经 StaticPool
只有一个连接，pysqlite 在第一条写语句前才开始事务，所以在下单请求
还只做过查询时，用第二个会话写入并提交，就能模拟同键并发请求先提交。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    DemoFxRate,
    Order,
    OrderAccessGrant,
    OrderAccessSession,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    Product,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    ShippingRate,
    SiteSetting,
    VariantOptionValue,
    VerificationAttempt,
)
from app.models.member import (
    PURPOSE_CHECKOUT,
    PURPOSE_REGISTER,
    VERIFICATION_APPROVED,
    VERIFICATION_SENT,
    VERIFICATION_SUSPENDED,
    VERIFICATION_UNDELIVERABLE,
)
from app.models.order import ACTOR_GUEST, CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.models.site import SITE_SETTING_ID
from app.services import ordering
from app.services.checkout import CartItem
from app.services.order_access import COOKIE_NAME, check_order_access, csrf_token_for_cookie
from app.services.order_rules import generate_order_number
from app.services.ordering import GuestOrderRequest, request_fingerprint

# pysqlite 没有原生定点小数，SQLAlchemy 经浮点存取并对此告警；
# 示例汇率只有 6 位小数，读回相等。
pytestmark = pytest.mark.filterwarnings(
    "ignore:Dialect sqlite\\+pysqlite does \\*not\\* support Decimal:sqlalchemy.exc.SAWarning"
)

URL = "/api/orders/guest"
MAX_BODY_BYTES = 8 * 1024

PHONE_RAW = "012-345 6789"
PHONE_E164 = "+60123456789"
NAME = "Zubaidah Secretname"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "50000"

# 默认请求：TEE-RED-M 2 件（2590 仙）、MUG 1 件（1500 仙），
# 寄马来西亚 MY-10（运费 800 仙）。
SUBTOTAL = 2 * 2590 + 1500
SHIPPING = 800

PUBLIC_FIELDS = {
    "order_number",
    "status",
    "subtotal_sen",
    "shipping_fee_sen",
    "total_sen",
    "payment_expires_at",
    "csrf_token",
}


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
def client(engine: Engine) -> TestClient:
    app = create_app(Settings(_env_file=None))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    # https：cookie 带 Secure，TestClient 只在 https 下保存并回送它。
    return TestClient(app, base_url="https://testserver")


@dataclass(frozen=True)
class _Catalog:
    red_m: int
    blue_m: int
    mug: int


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _option(
    db: Session,
    product: Product,
    code: str,
    sort_order: int,
    names: tuple[str, str | None],
) -> ProductOption:
    option = ProductOption(
        product_id=product.id,
        code=code,
        sort_order=sort_order,
        name_en=names[0],
        name_zh=names[1],
    )
    return _add(db, option)


def _value(
    db: Session,
    option: ProductOption,
    code: str,
    sort_order: int,
    names: tuple[str, str | None],
) -> ProductOptionValue:
    value = ProductOptionValue(
        product_id=option.product_id,
        option_id=option.id,
        code=code,
        sort_order=sort_order,
        name_en=names[0],
        name_zh=names[1],
    )
    return _add(db, value)


def _variant(
    db: Session,
    product: Product,
    sku: str,
    price_sen: int,
    stock: int,
    *values: ProductOptionValue,
) -> int:
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        price_sen=price_sen,
        daily_initial_stock=stock,
        available_stock=stock,
        is_active=True,
    )
    _add(db, variant)
    for value in values:
        link = VariantOptionValue(
            variant_id=variant.id,
            product_id=product.id,
            option_id=value.option_id,
            option_value_id=value.id,
        )
        _add(db, link)
    return variant.id


def _catalog(db: Session) -> _Catalog:
    """两件已发布商品、运费与汇率，写完即提交。

    Tee：两个规格名（颜色在前），英文齐全，中文缺尺码值，马来文全缺；
    每单限购取默认值 10。TEE-RED-M 2590 仙、库存 5；TEE-BLUE-M
    2190 仙、库存 5（规格 ID 比红色大）。
    Mug：无规格，三语名称齐全，每单限购 2；MUG 1500 仙、库存 3。
    运费：MY-10 州属行、SG 国家行、兜底行；MY-05 没有运费行。
    """
    category = _add(db, Category(slug="goods", name_en="Goods", is_active=True))
    tee = Product(
        category_id=category.id,
        slug="tee",
        name_en="Tee",
        name_zh="T恤",
        description_en="A tee.",
        is_active=True,
    )
    _add(db, tee)
    color = _option(db, tee, "color", 0, ("Colour", "颜色"))
    size = _option(db, tee, "size", 1, ("Size", "尺码"))
    red = _value(db, color, "red", 0, ("Red", "红色"))
    blue = _value(db, color, "blue", 1, ("Blue", "蓝色"))
    medium = _value(db, size, "m", 0, ("M", None))
    red_m = _variant(db, tee, "TEE-RED-M", 2590, 5, red, medium)
    blue_m = _variant(db, tee, "TEE-BLUE-M", 2190, 5, blue, medium)

    mug = Product(
        category_id=category.id,
        slug="mug",
        name_en="Mug",
        name_zh="马克杯",
        name_ms="Mug Kopi",
        description_en="A mug.",
        is_active=True,
        max_per_order=2,
    )
    _add(db, mug)
    mug_id = _variant(db, mug, "MUG", 1500, 3)

    zones = [
        ("my_state", "MY-10", SHIPPING, 1),
        ("country", "SG", 2000, 3),
        ("other", "OTHER", 8000, 4),
    ]
    for zone_type, zone_code, fee_sen, version in zones:
        rate = ShippingRate(
            zone_type=zone_type,
            zone_code=zone_code,
            fee_sen=fee_sen,
            version=version,
        )
        _add(db, rate)
    fx = DemoFxRate(
        country_code="SG",
        currency_code="SGD",
        currency_decimals=2,
        rate=Decimal("0.310000"),
        version=5,
    )
    _add(db, fx)
    db.commit()
    return _Catalog(red_m=red_m, blue_m=blue_m, mug=mug_id)


def _sms_switch(db: Session, enabled: bool) -> None:
    db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=enabled))
    db.commit()


def _verification(
    db: Session,
    *,
    status: str,
    purpose: str = PURPOSE_CHECKOUT,
    minutes_ago: int = 5,
    phone: str = PHONE_E164,
) -> None:
    created = _now() - timedelta(minutes=minutes_ago)
    provider_id = None if status == VERIFICATION_SUSPENDED else f"VE{uuid.uuid4().hex}"
    attempt = VerificationAttempt(
        phone=phone,
        purpose=purpose,
        status=status,
        provider_request_id=provider_id,
        created_at=created,
        updated_at=created,
    )
    db.add(attempt)
    db.commit()


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "lines": [{"sku": "TEE-RED-M", "quantity": 2}, {"sku": "MUG", "quantity": 1}],
        "phone": PHONE_RAW,
        "phone_region": "MY",
        "name": f"  {NAME} ",
        "address": f" {ADDRESS}\n",
        "postal_code": f" {POSTAL} ",
        "country_code": "MY",
        "state_code": "MY-10",
    }
    return body | overrides


def _without(body: dict[str, Any], name: str) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != name}


def _default_request() -> GuestOrderRequest:
    """_payload() 经校验与规范化之后的样子。"""
    return GuestOrderRequest(
        lines=(CartItem(sku="TEE-RED-M", quantity=2), CartItem(sku="MUG", quantity=1)),
        phone=PHONE_E164,
        name=NAME,
        address=ADDRESS,
        postal_code=POSTAL,
        country_code="MY",
        state_code="MY-10",
        region=None,
    )


def _post(client: TestClient, body: object, key: str | None = None) -> Any:
    return client.post(URL, json=body, headers={"Idempotency-Key": key or _key()})


def _stocks(db: Session, catalog: _Catalog) -> tuple[int, int, int]:
    """TEE-RED-M、TEE-BLUE-M、MUG 的当日可用库存。"""
    stmt = select(ProductVariant.id, ProductVariant.available_stock)
    # Result 有 keys()，dict() 会把它当映射按键取值；先转成元组列表。
    stock = dict(db.execute(stmt).tuples().all())
    return stock[catalog.red_m], stock[catalog.blue_m], stock[catalog.mug]


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _orders(db: Session) -> list[Order]:
    db.expire_all()
    return list(db.scalars(select(Order).order_by(Order.id)))


def _column_length(column: str) -> int:
    return OrderRecipient.__table__.c[column].type.length


def _cookie_token(response: Any) -> str | None:
    cookies = response.headers.get_list("set-cookie")
    if not cookies:
        return None
    assert len(cookies) == 1
    match = re.match(rf"{re.escape(COOKIE_NAME)}=([^;]+)", cookies[0])
    assert match is not None
    return match.group(1)


def _committed_order(engine: Engine, key: str, fingerprint: str) -> str:
    """用第二个数据库会话写入并提交一张同键的待支付游客订单。"""
    now = _now()
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        created_at=now,
        payment_expires_at=now + timedelta(minutes=15),
        paid_at=None,
        member_id=None,
        claim_status=CLAIM_OPEN,
    )
    order_number = order.order_number
    with Session(engine) as other:
        other.add(order)
        other.commit()
    return order_number


def _set_stock(engine: Engine, variant_id: int, value: int) -> None:
    """用第二个数据库会话改当日可用库存并提交，模拟并发请求先扣了库存。"""
    stmt = (
        update(ProductVariant).where(ProductVariant.id == variant_id).values(available_stock=value)
    )
    with Session(engine) as other:
        other.execute(stmt)
        other.commit()


def _around_quote(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before: Callable[[], None] | None = None,
    after: Callable[[], None] | None = None,
) -> None:
    """在下单服务第一次重新计价之前或之后插入一段操作（只插一次）。"""
    real = ordering.quote_cart
    calls: list[int] = []

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        first = not calls
        calls.append(1)
        if first and before is not None:
            before()
        quote = real(*args, **kwargs)
        if first and after is not None:
            after()
        return quote

    monkeypatch.setattr(ordering, "quote_cart", wrapper)


# ---------------------------------------------------------------------------
# 成功下单
# ---------------------------------------------------------------------------


def test_new_order_response_has_only_public_fields(db: Session, client: TestClient) -> None:
    """「订单摘要不依赖收货资料记录存在」；凭据不写入「错误回显」；
    第 1 条「最终应付不得低于运费」（游客无券与积分，应付即小计加运费）。
    SHOP-TASK-020 验收：新订单 201，响应只含订单号、状态、商品小计、
    运费、应付、支付到期时间与 CSRF 令牌，不含收货资料、会话令牌或幂等键。
    """
    _catalog(db)
    key = _key()

    response = _post(client, _payload(), key)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == PUBLIC_FIELDS
    assert body["status"] == STATUS_AWAITING_PAYMENT
    assert body["subtotal_sen"] == SUBTOTAL
    assert body["shipping_fee_sen"] == SHIPPING
    assert body["total_sen"] == SUBTOTAL + SHIPPING
    token = _cookie_token(response)
    assert token is not None
    for secret in (NAME, ADDRESS, PHONE_E164, "345 6789", key, token):
        assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_new_order_row_values(db: Session, client: TestClient) -> None:
    """「数据模型」Order 一行：可选会员 ID、状态、金额快照、幂等键、
    创建与支付时间；「下单：awaiting_demo_payment」；「下单存计算规则
    版本与金额快照」；第 3 条「游客不积累积分」；第 6 条「15 分钟未支付
    自动取消」（支付到期时间为创建时间加 15 分钟）；游客订单可认领（open）。
    """
    _catalog(db)
    key = _key()

    response = _post(client, _payload(), key)

    assert response.status_code == 201, response.text
    (order,) = _orders(db)
    assert order.order_number == response.json()["order_number"]
    assert order.status == STATUS_AWAITING_PAYMENT
    assert order.member_id is None
    assert order.claim_status == CLAIM_OPEN
    assert order.subtotal_sen == SUBTOTAL
    assert order.coupon_discount_sen == 0
    assert order.points_redeemed == 0
    assert order.points_earned == 0
    assert order.shipping_fee_sen == SHIPPING
    assert order.shipping_zone_code == "MY-10"
    assert order.shipping_rate_version == 1
    assert order.total_sen == order.subtotal_sen + order.shipping_fee_sen
    assert order.idempotency_key == key
    assert order.request_fingerprint == request_fingerprint(_default_request())
    assert order.payment_expires_at == order.created_at + timedelta(minutes=15)
    assert order.paid_at is None
    expires = datetime.fromisoformat(response.json()["payment_expires_at"])
    assert expires == order.payment_expires_at.replace(tzinfo=UTC)


def test_new_order_items_and_units(db: Session, client: TestClient) -> None:
    """「商品名称、规格、数量、单价，以及逐件优惠、积分、现金实付、
    获得积分的分摊快照」；「文案缺少当前语言时回退英文」；第 2 条
    「先将商品按稳定的订单行序与件序展开」，逐件分摊之和等于商品小计；
    第 3 条「游客不积累积分」（逐件获得积分为 0）。
    """
    catalog = _catalog(db)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text
    (order,) = _orders(db)
    stmt = select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.line_index)
    tee, mug = db.scalars(stmt)
    assert (tee.line_index, tee.sku, tee.variant_id) == (0, "TEE-RED-M", catalog.red_m)
    assert (mug.line_index, mug.sku, mug.variant_id) == (1, "MUG", catalog.mug)
    names = (tee.product_name_en, tee.product_name_zh, tee.product_name_ms)
    assert names == ("Tee", "T恤", "Tee")
    assert tee.variant_label_en == "Colour: Red / Size: M"
    assert tee.variant_label_zh == "颜色：红色 / 尺码：M"
    assert tee.variant_label_ms == "Colour: Red / Size: M"
    assert (tee.unit_price_sen, tee.quantity, tee.line_subtotal_sen) == (2590, 2, 5180)
    names = (mug.product_name_en, mug.product_name_zh, mug.product_name_ms)
    assert names == ("Mug", "马克杯", "Mug Kopi")
    assert (mug.variant_label_en, mug.variant_label_zh, mug.variant_label_ms) == ("", "", "")
    assert (mug.unit_price_sen, mug.quantity, mug.line_subtotal_sen) == (1500, 1, 1500)

    stmt = (
        select(OrderItem.line_index, OrderItemUnit)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .order_by(OrderItem.line_index, OrderItemUnit.unit_index)
    )
    units = list(db.execute(stmt).tuples())
    positions = [(line, unit.unit_index, unit.original_price_sen) for line, unit in units]
    assert positions == [(0, 0, 2590), (0, 1, 2590), (1, 0, 1500)]
    assert sum(unit.original_price_sen for _, unit in units) == order.subtotal_sen
    assert sum(unit.cash_paid_sen for _, unit in units) == order.subtotal_sen
    assert all(unit.coupon_discount_sen == 0 for _, unit in units)
    assert all(unit.points_discount == 0 for _, unit in units)
    assert all(unit.points_earned == 0 for _, unit in units)


def test_new_order_recipient_event_and_stock(db: Session, client: TestClient) -> None:
    """「OrderRecipient：收货姓名、规范化 E.164 电话、地址、邮编等原文」
    （去掉首尾空白；马来西亚的地区即州属代码）；「事件不写收货资料原文」，
    下单事件迁移前状态为空、操作者 guest；第 6 条「下单事务预留当日可用
    库存」（按件数扣减，未下单的 SKU 不变）。
    """
    catalog = _catalog(db)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text
    (order,) = _orders(db)
    recipient = db.scalars(select(OrderRecipient)).one()
    assert recipient.order_id == order.id
    assert (recipient.name, recipient.phone) == (NAME, PHONE_E164)
    assert (recipient.country_code, recipient.region) == ("MY", "MY-10")
    assert (recipient.address, recipient.postal_code) == (ADDRESS, POSTAL)
    placed = db.scalars(select(OrderEvent)).one()
    assert placed.order_id == order.id
    assert placed.from_status is None
    assert placed.to_status == STATUS_AWAITING_PAYMENT
    assert placed.actor_type == ACTOR_GUEST
    assert _stocks(db, catalog) == (5 - 2, 5, 3 - 1)


def test_other_country_region_and_fallback_rate(db: Session, client: TestClient) -> None:
    """「国家运费、马来西亚州属运费、“其他国家”兜底运费」；「下单存计算
    规则版本与金额快照」。SHOP-TASK-020 验收：其他国家的地区是去掉首尾
    空白的自由文本，去掉空白后为空时存空。
    """
    _catalog(db)
    tokyo = _without(_payload(country_code="JP", region="  Tokyo  "), "state_code")
    blank = _payload(country_code="SG", state_code=None, region="   ")

    first = _post(client, tokyo, _key())
    second = _post(client, blank, _key())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    japan, singapore = _orders(db)
    assert (japan.shipping_zone_code, japan.shipping_rate_version) == ("OTHER", 4)
    assert japan.total_sen == SUBTOTAL + 8000
    assert (singapore.shipping_zone_code, singapore.shipping_fee_sen) == ("SG", 2000)
    stmt = select(OrderRecipient.order_id, OrderRecipient.region)
    assert dict(db.execute(stmt).tuples().all()) == {japan.id: "Tokyo", singapore.id: None}


def test_set_cookie_and_grant_pass_the_check(db: Session, client: TestClient) -> None:
    """「每张游客订单…创建成功后，服务端只给当前浏览器发一个不可猜测、
    30 分钟有效且仅限该单的短期凭据」；「以 HttpOnly、Secure、
    SameSite=Lax 的 cookie 交给浏览器」；写操作「另须 CSRF 令牌」。
    名称与属性按 SHOP-TASK-019，授权通过校验函数，CSRF 令牌由 cookie 算出。
    """
    _catalog(db)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text
    (cookie,) = response.headers.get_list("set-cookie")
    attributes = [part.strip().lower() for part in cookie.split(";")]
    assert attributes[0].startswith(f"{COOKIE_NAME.lower()}=")
    expected = {"httponly", "secure", "samesite=lax", "path=/", "max-age=1800"}
    assert expected <= set(attributes)
    assert not any(part.startswith(("domain", "expires")) for part in attributes)
    token = _cookie_token(response)
    (order,) = _orders(db)
    assert check_order_access(db, token, order.id, SCOPE_GUEST_CHECKOUT, _now())
    assert response.json()["csrf_token"] == csrf_token_for_cookie(token)


@pytest.mark.parametrize(
    ("phone", "phone_region", "country", "expected"),
    [
        pytest.param("9123 4567", "SG", "MY", "+6591234567", id="sg-number-to-my"),
        pytest.param("202-555-0123", "US", "SG", "+12025550123", id="us-number-to-sg"),
        pytest.param("+65 9123 4567", "MY", "MY", "+6591234567", id="plus-wins"),
    ],
)
def test_phone_follows_request_region_not_country(
    db: Session,
    client: TestClient,
    phone: str,
    phone_region: str,
    country: str,
    expected: str,
) -> None:
    """「游客订单的收货电话就是结账第一步按上一条规范化的号码，下单时
    不再按收货国家重新解析」；「访客明确输入 + 国家码时以输入为准」；
    「之后选择的收货国家不改变已判定的号码」。
    """
    _catalog(db)
    state = "MY-10" if country == "MY" else None
    body = _payload(phone=phone, phone_region=phone_region)
    body |= {"country_code": country, "state_code": state}

    response = _post(client, body, _key())

    assert response.status_code == 201, response.text
    assert db.scalar(select(OrderRecipient.phone)) == expected


# ---------------------------------------------------------------------------
# 短信验证开关
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", [None, False], ids=["no-settings-row", "switch-off"])
def test_switch_off_lets_whitelisted_phone_order(
    db: Session, client: TestClient, row: bool | None
) -> None:
    """「关闭时全站不发任何短信：未登录访客的任何号码（含马新号码）都不
    验证、以游客下单」；「开关关闭即不因号码属于白名单而拒绝游客订单」；
    SiteSetting「默认关闭」（没有那一行视为关闭）。号码没有任何验证记录。
    """
    _catalog(db)
    if row is not None:
        _sms_switch(db, row)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text
    assert _count(db, VerificationAttempt) == 0


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(None, id="no-record"),
        pytest.param(
            {"status": VERIFICATION_UNDELIVERABLE, "minutes_ago": 31},
            id="older-than-30-minutes",
        ),
        pytest.param({"status": VERIFICATION_SENT}, id="sent"),
        pytest.param({"status": VERIFICATION_APPROVED}, id="approved"),
        pytest.param(
            {"status": VERIFICATION_UNDELIVERABLE, "purpose": PURPOSE_REGISTER},
            id="not-checkout",
        ),
        pytest.param(
            {"status": VERIFICATION_SUSPENDED, "phone": "+60123456788"},
            id="other-number",
        ),
    ],
)
def test_switch_on_rejects_whitelisted_phone(
    db: Session, client: TestClient, record: dict[str, Any] | None
) -> None:
    """「开关开启时，服务端创建订单时强制执行：游客订单的收货电话属于
    白名单国家即拒绝，除非服务端记录显示该号码刚遇到短信无法送达或停发
    的降级情形，不依赖前端」。SHOP-TASK-020 验收：只认该号码最近 30 分钟
    内用途为 checkout、状态为 undeliverable 或 suspended 的记录，否则
    403 sms_verification_required，不建订单、不动库存。
    """
    catalog = _catalog(db)
    _sms_switch(db, True)
    if record is not None:
        _verification(db, **record)

    response = _post(client, _payload(), _key())

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "sms_verification_required"}
    assert _orders(db) == []
    assert _stocks(db, catalog) == (5, 5, 3)
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize("status", [VERIFICATION_UNDELIVERABLE, VERIFICATION_SUSPENDED])
def test_switch_on_allows_recent_fallback(db: Session, client: TestClient, status: str) -> None:
    """「结账时白名单号码的短信无法送达或短信服务停发，允许改为游客
    下单」——最近 30 分钟内的 checkout 记录放行。
    """
    _catalog(db)
    _sms_switch(db, True)
    _verification(db, status=status, minutes_ago=29)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text


def test_switch_on_allows_phone_outside_whitelist(db: Session, client: TestClient) -> None:
    """「号码不属于白名单时不发短信，以游客下单」。"""
    _catalog(db)
    _sms_switch(db, True)

    response = _post(client, _payload(phone="202-555-0123", phone_region="US"), _key())

    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# 不可下单与库存
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param([{"sku": "NOPE", "quantity": 1}], id="unavailable"),
        pytest.param([{"sku": "MUG", "quantity": 3}], id="over-limit"),
        pytest.param(
            [{"sku": "TEE-RED-M", "quantity": 1}, {"sku": "TEE-BLUE-M", "quantity": 6}],
            id="insufficient-stock",
        ),
    ],
)
def test_not_placeable_creates_nothing(
    db: Session, client: TestClient, lines: list[dict[str, Any]]
) -> None:
    """第 8 条「存在任何非正常行时不可下单。下单时服务端重新校验，超出
    限购即拒绝整单，不部分下单」；「失败全部回滚」——409
    order_not_placeable，不建订单、不动库存、不签发凭据。
    """
    catalog = _catalog(db)

    response = _post(client, _payload(lines=lines), _key())

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_placeable"}
    assert _orders(db) == []
    assert _stocks(db, catalog) == (5, 5, 3)
    assert _count(db, OrderAccessSession) == 0
    assert response.headers.get_list("set-cookie") == []


def test_second_reservation_failing_rolls_back_the_first(
    db: Session,
    engine: Engine,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「库存预占…及订单创建在同一事务，失败全部回滚」；第 8 条「不部分
    下单」。计价时两行都够；扣库存前另一请求把规格 ID 较大的 TEE-BLUE-M
    扣到 1：第一行先扣成功、第二行扣不到，整个事务回滚，第一行库存不变。
    """
    catalog = _catalog(db)
    _around_quote(monkeypatch, after=lambda: _set_stock(engine, catalog.blue_m, 1))
    lines = [{"sku": "TEE-RED-M", "quantity": 2}, {"sku": "TEE-BLUE-M", "quantity": 2}]

    response = _post(client, _payload(lines=lines), _key())

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_placeable"}
    assert _orders(db) == []
    assert _stocks(db, catalog) == (5, 1, 3)


def test_missing_shipping_rate_is_503(db: Session, client: TestClient) -> None:
    """「数据模型」ShippingRate 一行，下单存运费快照。SHOP-TASK-020 验收：
    运费行缺失时与计价接口一样 503，不以零运费代替，不建订单、不动库存。
    """
    catalog = _catalog(db)

    response = _post(client, _payload(state_code="MY-05"), _key())

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "shipping is unavailable"}
    assert _orders(db) == []
    assert _stocks(db, catalog) == (5, 5, 3)


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


def test_same_key_same_request_replays(db: Session, client: TestClient) -> None:
    """「相同键相同请求返回原结果」——重放 200 与同一订单，不再扣库存、
    不多写事件。指纹按规范化后的内容计算：电话换一种写法、收货资料首尾
    空白不同仍是同一请求。订单仍待支付且未过期，重新签发凭据并设 cookie。
    """
    catalog = _catalog(db)
    key = _key()
    first = _post(client, _payload(), key)
    assert first.status_code == 201, first.text

    again = _payload(phone="+60 12-345 6789", name=NAME, address=f"\t{ADDRESS}")
    second = _post(client, again, key)

    assert second.status_code == 200, second.text
    assert second.json()["order_number"] == first.json()["order_number"]
    assert set(second.json()) == PUBLIC_FIELDS
    assert len(_orders(db)) == 1
    assert _count(db, OrderEvent) == 1
    assert _stocks(db, catalog) == (3, 5, 2)
    token = _cookie_token(second)
    assert token is not None
    assert second.json()["csrf_token"] == csrf_token_for_cookie(token)


def test_same_key_different_request_conflicts(db: Session, client: TestClient) -> None:
    """「同键不同内容报冲突」——409 idempotency_conflict，不重新计价、
    不动库存、不多建订单。
    """
    catalog = _catalog(db)
    key = _key()
    assert _post(client, _payload(), key).status_code == 201

    lines = [{"sku": "TEE-RED-M", "quantity": 1}, {"sku": "MUG", "quantity": 1}]
    response = _post(client, _payload(lines=lines), key)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "idempotency_conflict"}
    assert len(_orders(db)) == 1
    assert _stocks(db, catalog) == (3, 5, 2)


def test_replay_of_expired_order_sets_no_cookie(db: Session, client: TestClient) -> None:
    """凭据「用于该单的模拟支付、失败重试、取消与结果页」；第 6 条
    「15 分钟未支付自动取消」。SHOP-TASK-020 验收：重放时订单已过支付
    到期时间，只返回订单信息，不签发授权、不设 cookie。
    """
    _catalog(db)
    key = _key()
    first = _post(client, _payload(), key)
    assert first.status_code == 201, first.text
    db.execute(update(Order).values(payment_expires_at=_now() - timedelta(minutes=1)))
    db.commit()
    grants = _count(db, OrderAccessGrant)
    client.cookies.clear()

    response = _post(client, _payload(), key)

    assert response.status_code == 200, response.text
    assert response.json()["order_number"] == first.json()["order_number"]
    assert response.json()["csrf_token"] is None
    assert response.headers.get_list("set-cookie") == []
    assert _count(db, OrderAccessGrant) == grants


@pytest.mark.parametrize("same_request", [True, False], ids=["replay", "conflict"])
def test_unique_key_violation_on_insert(
    db: Session,
    engine: Engine,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    same_request: bool,
) -> None:
    """「下单…均使用幂等键和数据库唯一约束。相同键相同请求返回原结果；
    同键不同内容报冲突」；「失败全部回滚」。查过幂等键之后、写订单之前，
    另一个数据库会话先提交了同键订单：插入撞上唯一约束，回滚本次的库存
    扣减，不多建订单，按指纹重放它或报 idempotency_conflict。
    """
    catalog = _catalog(db)
    key = _key()
    fingerprint = request_fingerprint(_default_request()) if same_request else "0f" * 32
    committed: list[str] = []

    def other_request_commits() -> None:
        committed.append(_committed_order(engine, key, fingerprint))

    _around_quote(monkeypatch, before=other_request_commits)

    response = _post(client, _payload(), key)

    if same_request:
        assert response.status_code == 200, response.text
        assert response.json()["order_number"] == committed[0]
        assert _cookie_token(response) is not None
    else:
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "idempotency_conflict"}
    assert [order.order_number for order in _orders(db)] == committed
    assert _stocks(db, catalog) == (5, 5, 3)
    assert _count(db, OrderItem) == 0
    assert _count(db, OrderEvent) == 0


@pytest.mark.parametrize("phase", ["before-quote", "before-reservation"])
def test_concurrent_same_key_that_used_up_stock_is_replayed(
    db: Session,
    engine: Engine,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """「相同键相同请求返回原结果」；「网络中断时先查询原订单…再允许
    重试」。同键的第一个请求已提交并用尽库存，第二个请求在重新计价
    （库存不足）或扣库存时失败：回滚后按幂等键重新查到第一个请求的
    订单并返回它，而不是 order_not_placeable。
    """
    catalog = _catalog(db)
    key = _key()
    committed: list[str] = []

    def first_request_commits() -> None:
        _set_stock(engine, catalog.red_m, 1)
        fingerprint = request_fingerprint(_default_request())
        committed.append(_committed_order(engine, key, fingerprint))

    if phase == "before-quote":
        _around_quote(monkeypatch, before=first_request_commits)
    else:
        _around_quote(monkeypatch, after=first_request_commits)

    response = _post(client, _payload(), key)

    assert response.status_code == 200, response.text
    assert response.json()["order_number"] == committed[0]
    assert [order.order_number for order in _orders(db)] == committed
    assert _stocks(db, catalog) == (1, 5, 3)


# ---------------------------------------------------------------------------
# 请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_non_json_is_415(db: Session, client: TestClient, content_type: str | None) -> None:
    """SHOP-TASK-020 验收：只接受 JSON（其他 Content-Type 返回 415）。"""
    _catalog(db)
    headers = {"Idempotency-Key": _key()}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post(URL, content=json.dumps(_payload()), headers=headers)

    assert response.status_code == 415, response.text
    assert _orders(db) == []


def test_json_with_charset_is_accepted(db: Session, client: TestClient) -> None:
    """SHOP-TASK-020 验收：只接受 JSON——带 charset 的 application/json
    仍是 JSON。
    """
    _catalog(db)
    headers = {"Idempotency-Key": _key(), "Content-Type": "application/json; charset=utf-8"}

    response = client.post(URL, content=json.dumps(_payload()), headers=headers)

    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json", "Idempotency-Key": "k" * 20}, id="json"),
        pytest.param({"Content-Type": "application/json"}, id="no-key"),
        pytest.param({"Content-Type": "text/plain"}, id="not-json"),
    ],
)
def test_oversized_body_is_413_before_any_check(
    db: Session, client: TestClient, headers: dict[str, str]
) -> None:
    """SHOP-TASK-020 验收：请求体上限 8 KB（与计价接口相同），超过即
    413，先于一切校验——带多余字段、缺幂等键或不是 JSON 的超限请求体
    都是 413，不是 422 或 415。
    """
    _catalog(db)
    body = json.dumps(_payload(padding="x" * MAX_BODY_BYTES))

    response = client.post(URL, content=body, headers=headers)

    assert response.status_code == 413, response.text
    assert _orders(db) == []


KEY_LOC = ["header", "Idempotency-Key"]
TWO_MUGS = [{"sku": "MUG", "quantity": 1}, {"sku": "MUG", "quantity": 1}]
JAPAN = {"country_code": "JP", "state_code": None}


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"key": None}, KEY_LOC, id="missing-key"),
        pytest.param({"key": "a" * 15}, KEY_LOC, id="short-key"),
        pytest.param({"key": "a" * 65}, KEY_LOC, id="long-key"),
        pytest.param({"key": "a" * 20 + "!"}, KEY_LOC, id="bad-char-key"),
        pytest.param({"key": "a" * 20 + "."}, KEY_LOC, id="dot-key"),
        pytest.param({"body": {"total_sen": 1}}, ["body", "total_sen"], id="extra-field"),
        pytest.param({"drop": "state_code"}, ["body", "state_code"], id="my-no-state"),
        pytest.param({"body": {"state_code": "MY-17"}}, ["body", "state_code"], id="my-bad-state"),
        pytest.param({"body": {"country_code": "SG"}}, ["body", "state_code"], id="sg-with-state"),
        pytest.param({"body": {"region": "Selangor"}}, ["body", "region"], id="my-with-region"),
        pytest.param({"body": {"country_code": "my"}}, ["body", "country_code"], id="lower-cc"),
        pytest.param({"body": {"name": " \t "}}, ["body", "name"], id="blank-name"),
        pytest.param({"body": {"address": "   "}}, ["body", "address"], id="blank-address"),
        pytest.param({"body": {"postal_code": " "}}, ["body", "postal_code"], id="blank-postal"),
        pytest.param(
            {"body": {"name": NAME + "x" * _column_length("name")}},
            ["body", "name"],
            id="long-name",
        ),
        pytest.param(
            {"body": {"address": ADDRESS + "x" * _column_length("address")}},
            ["body", "address"],
            id="long-address",
        ),
        pytest.param(
            {"body": {"postal_code": "9" * (_column_length("postal_code") + 1)}},
            ["body", "postal_code"],
            id="long-postal",
        ),
        pytest.param(
            {"body": JAPAN | {"region": "r" * (_column_length("region") + 1)}},
            ["body", "region"],
            id="long-region",
        ),
        pytest.param({"body": {"phone": "12ab"}}, ["body", "phone"], id="phone-letters"),
        pytest.param({"body": {"phone": "123"}}, ["body", "phone"], id="phone-too-short"),
        pytest.param({"body": {"phone_region": "ZZ"}}, ["body", "phone"], id="phone-bad-region"),
        pytest.param({"drop": "phone"}, ["body", "phone"], id="phone-missing"),
        pytest.param({"body": {"lines": TWO_MUGS}}, ["body", "lines"], id="duplicate-sku"),
        pytest.param(
            {"body": {"lines": [{"sku": "MUG", "quantity": 100}]}},
            ["body", "lines", 0, "quantity"],
            id="quantity-100",
        ),
    ],
)
def test_invalid_request_is_422_without_echo(
    db: Session, client: TestClient, change: dict[str, Any], loc: list[Any]
) -> None:
    """「格式不成立则提示修改、不创建订单」；凭据不写入「错误回显」。
    SHOP-TASK-020 验收：缺幂等键或不合法、多出字段、马来西亚缺州属、
    其他国家给州属、马来西亚给地区、去掉空白后为空、超过列长度、非法
    电话（phone_invalid）都 422；响应只指出出错的字段，不含提交的姓名、
    电话、地址；不建订单、不动库存。
    """
    catalog = _catalog(db)
    body = _payload(**change.get("body", {}))
    if "drop" in change:
        body = _without(body, change["drop"])
    headers = {"Content-Type": "application/json"}
    key = change.get("key", _key())
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post(URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    if loc == ["body", "phone"] and "drop" not in change:
        assert [error["type"] for error in errors] == ["phone_invalid"]
    submitted = [NAME, ADDRESS, PHONE_RAW, "3456789", "12ab"]
    assert not [value for value in submitted if value in response.text]
    assert _orders(db) == []
    assert _stocks(db, catalog) == (5, 5, 3)


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址」；凭据不写入「日志」。
    SHOP-TASK-020 验收：不写日志。
    """
    _catalog(db)
    caplog.set_level(logging.DEBUG)

    response = _post(client, _payload(), _key())

    assert response.status_code == 201, response.text
    assert [record for record in caplog.records if record.name.startswith("app")] == []

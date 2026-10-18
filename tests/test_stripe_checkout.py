"""支付途径判定、发起 Stripe 测试 Checkout 会话与创建结果未定时的重试和对账。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2、3、9、
10 条，docs/HANDOFF.md 0.53 记录的核验结论（创建会话已不接受
payment_method_types；过期后以同一幂等键重放返回当初保存的响应，须再查询
会话取实际状态；换新键带过去的 expires_at 被 400 拒绝、param 为
expires_at，即原请求没有建成），与 Kelvin 2026-10-15 确认的取值（行项目
三语名称；Stripe 到期时间取订单支付到期时间与当前时间加 31 分钟中较晚者）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句或核验
结论；只出自 SHOP-TASK-112 验收标准的约定注明「验收：」。

用 SQLite 内存库按模型建表，Stripe 一律用 SHOP-TASK-111 的
FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会提交与
回滚，测试数据都先提交。SQLite 不支持行锁，加锁顺序只能按代码与 MySQL 行为
审阅，这里不验证。
"""

from __future__ import annotations

import itertools
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import Order, OrderEvent, SiteSetting, StripeCheckout
from app.models.order import ACTOR_SYSTEM, STATUS_AWAITING_PAYMENT, STATUS_CANCELLED
from app.models.site import PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
)
from app.services.stripe_checkout import (
    LOG_CREATE_REJECTED,
    LOG_NEEDS_REVIEW,
    PaymentRoute,
    ReconcileOutcome,
    StartResult,
    checkout_session_form,
    deserialize_form,
    payment_route,
    reconcile_creating_checkout,
    serialize_form,
    start_checkout,
    stripe_expires_at,
)
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    Form,
    PaymentIntentInfo,
    StripeError,
    StripeOutcome,
    StripeResult,
)

CREATED = datetime(2026, 10, 18, 2, 0)
# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起。
ORDER_EXPIRES = CREATED + timedelta(minutes=15)
NOW = CREATED + timedelta(minutes=1)
# 当前时间加 31 分钟晚于订单支付到期时间。
STRIPE_EXPIRES = NOW + timedelta(minutes=31)
AFTER_STRIPE_EXPIRY = STRIPE_EXPIRES + timedelta(minutes=1)
FINGERPRINT = "0f" * 32
DEMO_EMAIL = "demo@example.com"
SITE_URL = "https://shop.example.com"
NEW_URL = "https://checkout.stripe.example.com/c/pay/cs_test_new"
SESSIONS_PATH = "/v1/checkout/sessions"
EXPAND = (("expand[]", "payment_intent.latest_charge"),)
LOGGER = "app.services.stripe_checkout"

CONFIGURED = Settings(
    _env_file=None,
    stripe_api_base_url="https://api.stripe.example.com",
    stripe_secret_key="sk_test_placeholder",
    stripe_webhook_secret="whsec_placeholder",
    stripe_demo_email=DEMO_EMAIL,
    public_site_url=SITE_URL,
)
UNCONFIGURED = Settings(_env_file=None)


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：被测函数提交与回滚，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


_ORDER_SEQUENCE = itertools.count(1)
_CHECKOUT_SEQUENCE = itertools.count(1)


def _setting(db: Session, method: str) -> None:
    row = SiteSetting(id=1, sms_verification_enabled=False, payment_method=method)
    row.updated_at = NOW
    db.add(row)
    db.commit()


def _order(
    db: Session,
    *,
    total_sen: int = 6800,
    status: str = STATUS_AWAITING_PAYMENT,
    payment_expires_at: datetime = ORDER_EXPIRES,
) -> int:
    """游客订单（运费 0，应付等于商品小计），提交后返回订单 ID。"""
    n = next(_ORDER_SEQUENCE)
    order = Order(
        order_number=f"{n:016d}",
        status=status,
        subtotal_sen=total_sen,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=total_sen,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=CREATED,
        payment_expires_at=payment_expires_at,
        paid_at=None,
        member_id=None,
    )
    db.add(order)
    db.commit()
    return order.id


def _form(order_id: int, key: str, expires_at: datetime) -> tuple[tuple[str, str], ...]:
    return checkout_session_form(
        order_id=order_id,
        total_sen=6800,
        create_key=key,
        lang="en",
        demo_email=DEMO_EMAIL,
        public_site_url=SITE_URL,
        expires_at=expires_at,
    )


def _checkout(
    db: Session,
    order_id: int,
    status: str,
    *,
    created_at: datetime = NOW,
    expires_at: datetime = STRIPE_EXPIRES,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），
    提交后返回记录 ID。"""
    n = next(_CHECKOUT_SEQUENCE)
    key = f"existing-key-{n}"
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": key,
        "amount_sen": 6800,
        "currency": "myr",
        "status": status,
        "create_params": serialize_form(_form(order_id, key, expires_at)),
        "expires_at": expires_at,
        "created_at": created_at,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    if status == CHECKOUT_OPEN:
        fields["checkout_session_id"] = f"cs_test_existing_{n}"
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/existing_{n}"
        fields["payment_status"] = PAYMENT_UNPAID
    if status == CHECKOUT_COMPLETE:
        fields["checkout_session_id"] = f"cs_test_existing_{n}"
        fields["payment_status"] = PAYMENT_PAID
        fields["completed_at"] = NOW
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return record.id


def _records(db: Session, order_id: int) -> list[StripeCheckout]:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.order_id == order_id)
    return list(db.scalars(stmt.order_by(StripeCheckout.id)))


def _record(db: Session, checkout_id: int) -> StripeCheckout:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.id == checkout_id)
    return db.scalars(stmt).one()


def _order_status(db: Session, order_id: int) -> str:
    db.rollback()
    return db.scalars(select(Order.status).where(Order.id == order_id)).one()


def _session(
    status: str = "open",
    *,
    session_id: str = "cs_test_new",
    url: str | None = NEW_URL,
    payment_status: str = PAYMENT_UNPAID,
    payment_intent: str | None = None,
) -> CheckoutSession:
    return CheckoutSession(
        id=session_id,
        status=status,
        payment_status=payment_status,
        livemode=False,
        currency="myr",
        amount_total=6800,
        url=url,
        payment_intent=None if payment_intent is None else PaymentIntentInfo(payment_intent),
    )


def _ok(session: CheckoutSession | None = None, *, replayed: bool = False) -> StripeResult:
    return StripeResult(StripeOutcome.OK, session or _session(), replayed)


def _rejected(
    status: int = 400, error_type: str | None = "invalid_request_error", param: str | None = None
) -> StripeResult:
    return StripeResult(StripeOutcome.REJECTED, error=StripeError(status, error_type, param))


UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)
LIVE = StripeResult(StripeOutcome.LIVE, _session())


class _HookGateway(FakeStripeGateway):
    """替身之上，在创建调用发生时先执行 hook（看数据库此刻的样子，或模拟并发的 webhook）。"""

    def __init__(self, hook: Callable[[], None], *results: StripeResult) -> None:
        super().__init__(*results)
        self._hook = hook

    def create_checkout_session(self, form: Form, idempotency_key: str) -> StripeResult:
        self._hook()
        return super().create_checkout_session(form, idempotency_key)


def _record_statements(db: Session) -> list[str]:
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    return statements


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def _start(db: Session, gateway: FakeStripeGateway, order_id: int, now: datetime = NOW):
    return start_checkout(db, gateway, CONFIGURED, order_id, "en", now)


# ---------------------------------------------------------------- 支付途径


@pytest.mark.parametrize("status", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
@pytest.mark.parametrize("method", [PAYMENT_METHOD_SIMULATED, None])
def test_route_with_active_record_is_stripe_whatever_the_switch(
    db: Session, status: str, method: str | None
) -> None:
    """第 2 条「订单只要有一条不是 expired 的 Stripe 记录（creating、open 或
    complete），不论开关当前取值，都不接受模拟支付」：开关为模拟或表里没有
    那一行时，有这样的记录仍为 stripe。"""
    if method is not None:
        _setting(db, method)
    order_id = _order(db)
    _checkout(db, order_id, status)

    assert payment_route(db, order_id, 200) is PaymentRoute.STRIPE


def test_route_with_active_record_ignores_the_minimum(db: Session) -> None:
    """第 2 条「不论开关当前取值……只继续该会话」：已有会话的订单即使应付
    低于最低收款额（配置项之后调高）也仍为 stripe。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_OPEN)

    assert payment_route(db, order_id, 10_000) is PaymentRoute.STRIPE


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (PAYMENT_METHOD_SIMULATED, PaymentRoute.SIMULATED),
        (PAYMENT_METHOD_STRIPE_TEST, PaymentRoute.STRIPE),
    ],
)
def test_route_with_only_expired_records_follows_the_switch(
    db: Session, method: str, expected: PaymentRoute
) -> None:
    """第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有
    模拟支付流程不变」：只有 expired 记录时按开关判定。"""
    _setting(db, method)
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_EXPIRED)
    _checkout(db, order_id, CHECKOUT_EXPIRED)

    assert payment_route(db, order_id, 200) is expected


@pytest.mark.parametrize(
    ("total_sen", "expected"),
    [(199, PaymentRoute.SIMULATED), (200, PaymentRoute.STRIPE), (201, PaymentRoute.STRIPE)],
)
def test_route_below_the_minimum_is_simulated(
    db: Session, total_sen: int, expected: PaymentRoute
) -> None:
    """第 2 条「订单应付低于 Stripe 对 MYR 的最低收款额……时，即使为
    『Stripe 测试』也走模拟支付」：199 仙为 simulated，等于与高于 200 仙为
    stripe（验收：「不低于最低收款额时为 stripe」）。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db, total_sen=total_sen)

    assert payment_route(db, order_id, 200) is expected


@pytest.mark.parametrize("method", [PAYMENT_METHOD_SIMULATED, None])
def test_route_with_switch_simulated_is_simulated(db: Session, method: str | None) -> None:
    """第 2 条「支付方式取『模拟』（默认）」：开关为模拟或表里没有那一行时，
    没有记录的订单走模拟。"""
    if method is not None:
        _setting(db, method)
    order_id = _order(db)

    assert payment_route(db, order_id, 200) is PaymentRoute.SIMULATED


def test_route_only_reads_and_sees_the_current_switch(db: Session) -> None:
    """验收：「途径判定……只读」；第 2 条「每次从数据库读取」：判定只发
    SELECT，且读到开关的当前值（中途改库后结果随之改变）。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    statements = _record_statements(db)

    first = payment_route(db, order_id, 200)
    db.execute(update(SiteSetting).values(payment_method=PAYMENT_METHOD_SIMULATED))
    db.flush()
    second = payment_route(db, order_id, 200)

    assert first is PaymentRoute.STRIPE
    assert second is PaymentRoute.SIMULATED
    reads = [s for s in statements if not s.lstrip().upper().startswith("UPDATE")]
    assert reads
    assert all(s.lstrip().upper().startswith("SELECT") for s in reads)


# ---------------------------------------------------------------- 创建参数


def test_form_lists_exactly_the_designed_parameters_in_order() -> None:
    """第 3 条「会话为 payment 模式，币种 MYR，金额等于订单应付快照，只有一个
    行项目，名称为三语文案里的『演示订单』」「`client_reference_id` 放本站
    订单的内部 ID；会话元数据放订单内部 ID……与创建幂等键……不放其他内容」
    「预填配置的固定演示邮箱」「语言按当前站点语言传给 Stripe」「成功与取消
    的返回地址由配置的公开站点地址拼出」：表单逐项、按顺序与此相同，再无
    其他参数。"""
    expires_at = datetime(2026, 10, 18, 2, 32)
    form = checkout_session_form(
        order_id=42,
        total_sen=6800,
        create_key="create-key",
        lang="en",
        demo_email=DEMO_EMAIL,
        public_site_url=SITE_URL,
        expires_at=expires_at,
    )

    assert form == (
        ("mode", "payment"),
        ("line_items[0][price_data][currency]", "myr"),
        ("line_items[0][price_data][unit_amount]", "6800"),
        ("line_items[0][price_data][product_data][name]", "Demo order"),
        ("line_items[0][quantity]", "1"),
        ("client_reference_id", "42"),
        ("metadata[order_id]", "42"),
        ("metadata[create_key]", "create-key"),
        ("customer_email", DEMO_EMAIL),
        ("locale", "en"),
        ("success_url", "https://shop.example.com/pay/result"),
        ("cancel_url", "https://shop.example.com/pay"),
        ("expires_at", str(int(expires_at.replace(tzinfo=UTC).timestamp()))),
    )


@pytest.mark.parametrize(
    ("lang", "name"),
    [("en", "Demo order"), ("zh", "演示订单"), ("ms", "Pesanan demo")],
)
def test_form_line_item_name_and_locale_follow_the_language(lang: str, name: str) -> None:
    """第 3 条「名称为三语文案里的『演示订单』」「语言按当前站点语言传给
    Stripe」：三种语言各取 Kelvin 2026-10-15 确认的名称，locale 为 en、zh
    或 ms（核验：Stripe 接受 ms 与 zh）。"""
    form = dict(
        checkout_session_form(
            order_id=1,
            total_sen=500,
            create_key="k",
            lang=lang,  # type: ignore[arg-type]
            demo_email=DEMO_EMAIL,
            public_site_url=SITE_URL,
            expires_at=STRIPE_EXPIRES,
        )
    )

    assert form["line_items[0][price_data][product_data][name]"] == name
    assert form["locale"] == lang


def test_sent_form_has_no_payment_method_types_order_number_or_recipient(db: Session) -> None:
    """核验结论「创建 Checkout 会话已不接受 payment_method_types」；第 3 条
    「不含商品名、订单号或收货资料」；第 10 条「不发送姓名、电话、地址或
    订单号」：发起时发出的表单没有 payment_method_types、没有订单号，参数名
    只有设计列出的那几项。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    order_number = db.scalars(select(Order.order_number).where(Order.id == order_id)).one()
    gateway = FakeStripeGateway(_ok())

    _start(db, gateway, order_id)

    form = gateway.calls[0].form
    names = [name for name, _ in form]
    assert not any(name.startswith("payment_method") for name in names)
    assert names == [
        "mode",
        "line_items[0][price_data][currency]",
        "line_items[0][price_data][unit_amount]",
        "line_items[0][price_data][product_data][name]",
        "line_items[0][quantity]",
        "client_reference_id",
        "metadata[order_id]",
        "metadata[create_key]",
        "customer_email",
        "locale",
        "success_url",
        "cancel_url",
        "expires_at",
    ]
    assert all(order_number not in value for _, value in form)


def test_form_return_urls_do_not_double_the_slash() -> None:
    """第 3 条「成功与取消的返回地址由配置的公开站点地址拼出……地址不带
    订单号、会话 ID 或任何凭据」：配置地址末尾带斜杠时照样拼成 /pay/result
    与 /pay。"""
    form = dict(
        checkout_session_form(
            order_id=7,
            total_sen=500,
            create_key="k",
            lang="en",
            demo_email=DEMO_EMAIL,
            public_site_url=SITE_URL + "/",
            expires_at=STRIPE_EXPIRES,
        )
    )

    assert form["success_url"] == "https://shop.example.com/pay/result"
    assert form["cancel_url"] == "https://shop.example.com/pay"


@pytest.mark.parametrize(
    ("payment_expires_at", "expected"),
    [
        # 当前时间加 31 分钟较晚（新订单的常见情形）。
        (ORDER_EXPIRES, NOW + timedelta(minutes=31)),
        # 订单支付到期时间较晚。
        (NOW + timedelta(minutes=40), NOW + timedelta(minutes=40)),
        # 两者相同。
        (NOW + timedelta(minutes=31), NOW + timedelta(minutes=31)),
    ],
)
def test_stripe_expiry_is_the_later_of_order_expiry_and_now_plus_31_minutes(
    payment_expires_at: datetime, expected: datetime
) -> None:
    """第 3 条「会话的 Stripe 到期时间取订单支付到期时间与 Stripe 允许的最短
    时长（创建后 30 分钟）中较晚者」，按 Kelvin 2026-10-15 确认取当前时间加
    31 分钟。"""
    assert stripe_expires_at(payment_expires_at, NOW) == expected


def test_stripe_expiry_rounds_up_to_a_whole_second() -> None:
    """第 3 条「Stripe 允许的最短时长（创建后 30 分钟）」：Stripe 的到期时间
    只收整秒，不足一秒的部分进到下一秒，不会因截断而短于 31 分钟。"""
    now = NOW + timedelta(microseconds=250_000)

    assert stripe_expires_at(ORDER_EXPIRES, now) == NOW + timedelta(minutes=31, seconds=1)


def test_snapshot_round_trips_in_order() -> None:
    """第 3 条「把这次创建的全部参数……存为快照」「以同一幂等键与同一快照
    重试」：快照是 JSON，原样取出的参数与顺序都不变（含中文名称）。"""
    form = checkout_session_form(
        order_id=3,
        total_sen=900,
        create_key="k",
        lang="zh",
        demo_email=DEMO_EMAIL,
        public_site_url=SITE_URL,
        expires_at=STRIPE_EXPIRES,
    )
    snapshot = serialize_form(form)

    assert json.loads(snapshot)[0] == ["mode", "payment"]
    assert deserialize_form(snapshot) == form


# ---------------------------------------------------------------- 发起：各结果


def test_start_writes_creating_before_calling_stripe_then_open(db: Session) -> None:
    """第 3 条「先在加锁读订单的同一事务里写一条 `creating` 状态的
    `StripeCheckout`……提交；再以该键与快照调用 Stripe」「只在记录仍为
    creating 时改为 `open`」：调用发生时 creating 记录已提交、会话不在事务里
    （不持有锁）；之后记录为 open，发起返回付款页地址。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    seen: list[tuple[bool, list[str]]] = []

    def probe() -> None:
        in_transaction = db.in_transaction()
        stmt = select(StripeCheckout.status).where(StripeCheckout.order_id == order_id)
        statuses = list(db.scalars(stmt))
        db.rollback()
        seen.append((in_transaction, statuses))

    gateway = _HookGateway(probe, _ok(_session(payment_intent="pi_test_1")))

    start = _start(db, gateway, order_id)

    assert seen == [(False, [CHECKOUT_CREATING])]
    assert start.result is StartResult.REDIRECT
    assert start.checkout_url == NEW_URL
    [record] = _records(db, order_id)
    assert record.status == CHECKOUT_OPEN
    assert record.checkout_session_id == "cs_test_new"
    assert record.checkout_url == NEW_URL
    assert record.payment_intent_id == "pi_test_1"
    assert record.payment_status == PAYMENT_UNPAID
    assert record.active_order_id == order_id
    assert record.amount_sen == 6800
    assert record.currency == "myr"
    assert record.created_at == NOW
    assert record.completed_at is None


def test_start_uses_a_new_uuid_key_and_stores_the_sent_snapshot(db: Session) -> None:
    """第 3 条「生成本站的创建幂等键，并把这次创建的全部参数……存为快照」
    「（幂等键同时写进会话元数据）」；验收：「创建幂等键为新生成的 UUID
    字符串」「Stripe 到期时间另存单列」：发出的幂等键是 UUID、等于记录的键
    与元数据里的 create_key；快照等于发出的表单；到期时间列等于快照里的值。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    gateway = FakeStripeGateway(_ok())

    start_checkout(db, gateway, CONFIGURED, order_id, "ms", NOW)

    [call] = gateway.calls
    [record] = _records(db, order_id)
    assert call.method == "POST"
    assert call.path == SESSIONS_PATH
    assert str(uuid.UUID(call.idempotency_key or "")) == call.idempotency_key
    assert call.idempotency_key == record.idempotency_key
    assert dict(call.form)["metadata[create_key]"] == record.idempotency_key
    assert deserialize_form(record.create_params) == call.form
    assert call.form == checkout_session_form(
        order_id=order_id,
        total_sen=6800,
        create_key=record.idempotency_key,
        lang="ms",
        demo_email=DEMO_EMAIL,
        public_site_url=SITE_URL,
        expires_at=STRIPE_EXPIRES,
    )
    assert record.expires_at == STRIPE_EXPIRES


def test_each_creating_record_gets_its_own_key(db: Session) -> None:
    """第 3 条「生成本站的创建幂等键」：两张订单各自发起，幂等键不同
    （每次新生成）。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    first, second = _order(db), _order(db)
    gateway = FakeStripeGateway(
        _ok(_session(session_id="cs_test_a")), _ok(_session(session_id="cs_test_b"))
    )

    _start(db, gateway, first)
    _start(db, gateway, second)

    keys = [call.idempotency_key for call in gateway.calls]
    assert len(set(keys)) == 2


def test_start_with_open_record_returns_its_url_without_calling_stripe(db: Session) -> None:
    """第 3 条「已有 open 就直接返回它的付款地址」；第 2 条「不另建会话……
    只继续该会话」：不调用 Stripe、不写新记录；开关已切回模拟也一样。"""
    _setting(db, PAYMENT_METHOD_SIMULATED)
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_OPEN)
    url = _record(db, checkout_id).checkout_url
    gateway = FakeStripeGateway()

    start = _start(db, gateway, order_id)

    assert start.result is StartResult.REDIRECT
    assert start.checkout_url == url
    assert gateway.calls == []
    assert [r.id for r in _records(db, order_id)] == [checkout_id]


def test_start_with_complete_record_returns_complete(db: Session) -> None:
    """第 2 条「支付页先按第 4 条向 Stripe 查询并确认，再只继续该会话」；
    验收：「已有 complete 记录返回 complete（由调用方按 SHOP-TASK-113
    确认）」：不调用 Stripe、不改记录。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_COMPLETE)
    gateway = FakeStripeGateway()

    start = _start(db, gateway, order_id)

    assert start.result is StartResult.COMPLETE
    assert start.checkout_url is None
    assert gateway.calls == []
    assert _record(db, checkout_id).status == CHECKOUT_COMPLETE


def test_start_for_an_order_that_is_not_awaiting_payment_is_not_payable(db: Session) -> None:
    """「订单与退款状态」：只有 awaiting_demo_payment 的订单可以支付；验收：
    「订单不是 awaiting_demo_payment 返回 not_payable」：已取消的订单不写
    记录、不调用 Stripe。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db, status=STATUS_CANCELLED)
    gateway = FakeStripeGateway()

    start = _start(db, gateway, order_id)

    assert start.result is StartResult.NOT_PAYABLE
    assert gateway.calls == []
    assert _records(db, order_id) == []


@pytest.mark.parametrize("with_expired_record", [False, True])
def test_start_after_local_expiry_without_session_cancels_and_writes_nothing(
    db: Session, with_expired_record: bool
) -> None:
    """「计价、优惠、积分与库存」第 6 条「15 分钟未支付自动取消并释放」；
    验收：「没有不是 expired 的记录而订单已过本地支付到期时间时……执行
    expire_overdue_orders……返回 not_payable，不写记录（15 分钟期限只让已有
    会话继续付款，不允许过期订单新建会话）」：订单按超时取消（操作者
    system），不写新记录、不调用 Stripe；只有 expired 记录时也一样。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    if with_expired_record:
        _checkout(db, order_id, CHECKOUT_EXPIRED)
    before = len(_records(db, order_id))
    gateway = FakeStripeGateway()

    start = _start(db, gateway, order_id, ORDER_EXPIRES)

    assert start.result is StartResult.NOT_PAYABLE
    assert gateway.calls == []
    assert len(_records(db, order_id)) == before
    assert _order_status(db, order_id) == STATUS_CANCELLED
    stmt = select(OrderEvent.actor_type).where(
        OrderEvent.order_id == order_id, OrderEvent.to_status == STATUS_CANCELLED
    )
    assert db.scalars(stmt).all() == [ACTOR_SYSTEM]


def test_start_with_open_record_after_local_expiry_still_redirects(db: Session) -> None:
    """第 4 条「确认不看订单的 15 分钟支付到期时间」与第 5 条「查看入口只对
    没有 Stripe 记录（或只有 expired 记录）的订单执行超时取消」：已有 open
    会话的订单过了本地到期时间，发起仍返回该会话，订单不取消。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_OPEN)

    start = _start(db, FakeStripeGateway(), order_id, ORDER_EXPIRES)

    assert start.result is StartResult.REDIRECT
    assert _order_status(db, order_id) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize(
    ("method", "total_sen"),
    [(PAYMENT_METHOD_SIMULATED, 6800), (PAYMENT_METHOD_STRIPE_TEST, 199)],
)
def test_start_when_the_route_is_simulated_is_method_changed(
    db: Session, method: str, total_sen: int
) -> None:
    """第 2 条「服务端在访客发起支付时按当时的值判定」「应付低于……最低
    收款额……走模拟支付」；验收：「途径为 simulated 返回 method_changed」：
    开关为模拟或应付低于 200 仙时不写记录、不调用 Stripe。"""
    _setting(db, method)
    order_id = _order(db, total_sen=total_sen)
    gateway = FakeStripeGateway()

    start = _start(db, gateway, order_id)

    assert start.result is StartResult.METHOD_CHANGED
    assert gateway.calls == []
    assert _records(db, order_id) == []


def test_start_when_stripe_is_not_configured_is_unavailable(db: Session) -> None:
    """第 9 条「创建会话……时 Stripe 不可用，支付页提示稍后再试……不自动改用
    模拟支付」；验收：「不看 Stripe 配置是否齐全」「配置判定为未配置时不写
    记录、返回 unavailable」：开关为 Stripe 测试而未配置时途径仍为 stripe，
    发起不写记录、不调用，返回 unavailable 而不是 method_changed。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    gateway = FakeStripeGateway(_ok())

    start = start_checkout(db, gateway, UNCONFIGURED, order_id, "en", NOW)

    assert start.result is StartResult.UNAVAILABLE
    assert gateway.calls == []
    assert _records(db, order_id) == []
    assert payment_route(db, order_id, UNCONFIGURED.stripe_min_amount_sen) is PaymentRoute.STRIPE


@pytest.mark.parametrize(
    ("lang", "now"),
    [("fr", NOW), ("en", NOW.replace(tzinfo=UTC))],
    ids=["unknown-language", "aware-time"],
)
def test_start_rejects_bad_arguments_before_touching_the_database(
    db: Session, lang: str, now: datetime
) -> None:
    """验收：「界面语言 en、zh 或 ms」；库里的时间一律是不带时区的 UTC：
    其他语言或带时区的时间抛 ValueError，不发任何语句。"""
    statements = _record_statements(db)

    with pytest.raises(ValueError):
        start_checkout(db, FakeStripeGateway(), CONFIGURED, 1, lang, now)  # type: ignore[arg-type]

    assert statements == []


# ---------------------------------------------------------------- 首次调用的结果


def test_first_call_param_error_marks_expired_and_logs(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「Stripe 以参数不合格……拒绝，按 Stripe 幂等规则说明原请求没有
    建成会话，记为 expired」；验收：「首次调用 rejected 且 HTTP 状态码为
    400、错误 type 为 invalid_request_error 并带 param 时……记录改为 expired
    （active_order_id 置空）、返回 unavailable，并记一条 error 日志（类别
    stripe_create_rejected，只含记录 ID）」：本次调用不再另建记录。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    gateway = FakeStripeGateway(_rejected(param="line_items[0][price_data][unit_amount]"))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        start = _start(db, gateway, order_id)

    assert start.result is StartResult.UNAVAILABLE
    assert len(gateway.calls) == 1
    [record] = _records(db, order_id)
    assert record.status == CHECKOUT_EXPIRED
    assert record.active_order_id is None
    assert record.checkout_session_id is None
    assert _messages(caplog) == [f"{LOG_CREATE_REJECTED} checkout={record.id}"]
    assert [r.levelno for r in caplog.records if r.name == LOGGER] == [logging.ERROR]


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(_rejected(401, "invalid_request_error", None), id="401"),
        pytest.param(_rejected(402, "invalid_request_error", "amount"), id="402"),
        pytest.param(_rejected(403, "invalid_request_error", None), id="403"),
        pytest.param(_rejected(404, "invalid_request_error", "id"), id="404"),
        pytest.param(_rejected(400, "invalid_request_error", None), id="400-without-param"),
        pytest.param(_rejected(400, "api_error", "mode"), id="400-other-type"),
        pytest.param(LIVE, id="live"),
        pytest.param(UNAVAILABLE, id="unavailable"),
    ],
)
def test_first_call_other_failures_keep_creating(
    db: Session, result: StripeResult, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「调用结果未知（超时、断线）时，记录保持 `creating`」；第 9 条
    「Stripe 不可用，支付页提示稍后再试」；验收：「其他 rejected（401、402、
    403、404，如密钥被轮换，不能证明原请求没建成）、live 与 unavailable 时
    记录保持 creating、返回 unavailable」：不记 expired，本模块不写日志。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    gateway = FakeStripeGateway(result)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        start = _start(db, gateway, order_id)

    assert start.result is StartResult.UNAVAILABLE
    [record] = _records(db, order_id)
    assert record.status == CHECKOUT_CREATING
    assert record.active_order_id == order_id
    assert record.checkout_session_id is None
    assert _messages(caplog) == []


def test_first_call_complete_session_records_completion_time(db: Session) -> None:
    """验收：「状态与付款状态（按会话实际状态）」「会话为 complete 时完成
    时间取本站首次得知的当前时间」：Stripe 返回已完成的会话时记为
    complete、完成时间为 now，发起返回 complete；确认时间留给
    SHOP-TASK-113。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    session = _session("complete", url=None, payment_status=PAYMENT_PAID, payment_intent="pi_1")

    start = _start(db, FakeStripeGateway(_ok(session)), order_id)

    assert start.result is StartResult.COMPLETE
    [record] = _records(db, order_id)
    assert record.status == CHECKOUT_COMPLETE
    assert record.completed_at == NOW
    assert record.payment_status == PAYMENT_PAID
    assert record.payment_intent_id == "pi_1"
    assert record.confirmed_at is None


# ---------------------------------------------------------------- 已有 creating：重试


def test_retry_before_stripe_expiry_uses_the_same_key_and_snapshot(db: Session) -> None:
    """第 3 条「之后的发起或定时任务以同一幂等键与同一快照重试，取得同一个
    会话」：第一次结果未定，第二次发起（另一种界面语言、稍晚的时间）发出的
    幂等键与表单都与第一次相同，记录改为 open。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    gateway = FakeStripeGateway(UNAVAILABLE, _ok())

    first = _start(db, gateway, order_id)
    later = NOW + timedelta(minutes=2)
    second = start_checkout(db, gateway, CONFIGURED, order_id, "zh", later)

    assert first.result is StartResult.UNAVAILABLE
    assert second.result is StartResult.REDIRECT
    one, two = gateway.calls
    assert two.idempotency_key == one.idempotency_key
    assert two.form == one.form
    [record] = _records(db, order_id)
    assert record.status == CHECKOUT_OPEN
    assert deserialize_form(record.create_params) == one.form


def test_retry_does_not_look_at_the_switch(db: Session) -> None:
    """第 2 条「已经发起的会话，其确认、作废与退款不受之后切换的影响」「订单
    只要有一条不是 expired 的 Stripe 记录……不论开关当前取值」：开关切回
    模拟后，creating 记录照样重试，而不是 method_changed。"""
    _setting(db, PAYMENT_METHOD_SIMULATED)
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_ok())

    start = _start(db, gateway, order_id)

    assert start.result is StartResult.REDIRECT
    assert len(gateway.calls) == 1


def test_retry_still_unknown_stays_creating(db: Session) -> None:
    """第 3 条「调用结果未知……记录保持 `creating`」：重试仍不可用时返回
    unavailable，记录不变。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)

    start = _start(db, FakeStripeGateway(UNAVAILABLE), order_id)

    assert start.result is StartResult.UNAVAILABLE
    assert _record(db, checkout_id).status == CHECKOUT_CREATING


def test_retry_param_error_expires_and_creates_a_new_record_in_the_same_call(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「Stripe 以参数不合格……拒绝……记为 expired」；验收：「重试与
    对账里的 400 参数错误把记录记为 expired 后，按上一条在同一调用里继续建
    新记录，不返回 unavailable」：旧记录 expired，新记录用新的幂等键并改为
    open，发起返回新会话的地址。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    old_id = _checkout(db, order_id, CHECKOUT_CREATING)
    old_key = _record(db, old_id).idempotency_key
    gateway = FakeStripeGateway(_rejected(param="locale"), _ok())

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        start = _start(db, gateway, order_id)

    assert start.result is StartResult.REDIRECT
    assert start.checkout_url == NEW_URL
    old, new = _records(db, order_id)
    assert old.id == old_id
    assert old.status == CHECKOUT_EXPIRED
    assert old.active_order_id is None
    assert new.status == CHECKOUT_OPEN
    assert new.idempotency_key != old_key
    assert [call.idempotency_key for call in gateway.calls] == [old_key, new.idempotency_key]
    assert _messages(caplog) == [f"{LOG_CREATE_REJECTED} checkout={old_id}"]


def test_reconcile_expired_then_rechecks_local_expiry_before_creating(db: Session) -> None:
    """验收：「本次发起在同一调用里按『没有记录』继续——提交后重新加锁读订单
    并重查（含本地到期），再写新的 creating 记录」；「计价、优惠、积分与
    库存」第 6 条：对账确认没有建成会话时订单已过本地支付到期时间，就按超时
    取消、不建新记录，返回 not_payable。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    old_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_rejected(param="expires_at"))

    start = _start(db, gateway, order_id, AFTER_STRIPE_EXPIRY)

    assert start.result is StartResult.NOT_PAYABLE
    assert len(gateway.calls) == 1
    [record] = _records(db, order_id)
    assert record.id == old_id
    assert record.status == CHECKOUT_EXPIRED
    assert _order_status(db, order_id) == STATUS_CANCELLED


def test_reconcile_expired_creates_a_new_record_in_the_same_call(db: Session) -> None:
    """验收：「对账记为 expired 后同一调用建新记录」：Stripe 到期时间已过、
    对账以 expires_at 被拒记为 expired，订单仍在本地支付期限内时（这里把
    订单的到期时间设得较晚），同一调用写新记录并取得新会话。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db, payment_expires_at=AFTER_STRIPE_EXPIRY + timedelta(minutes=10))
    old_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_rejected(param="expires_at"), _ok())

    start = _start(db, gateway, order_id, AFTER_STRIPE_EXPIRY)

    assert start.result is StartResult.REDIRECT
    old, new = _records(db, order_id)
    assert old.id == old_id
    assert old.status == CHECKOUT_EXPIRED
    assert new.status == CHECKOUT_OPEN
    assert new.expires_at == AFTER_STRIPE_EXPIRY + timedelta(minutes=31)


@pytest.mark.parametrize(
    ("concurrent", "expected_status", "expected_result"),
    [
        pytest.param(
            {
                "status": CHECKOUT_COMPLETE,
                "checkout_session_id": "cs_test_new",
                "payment_status": PAYMENT_PAID,
                "payment_intent_id": "pi_from_webhook",
                "completed_at": NOW - timedelta(seconds=5),
            },
            CHECKOUT_COMPLETE,
            StartResult.COMPLETE,
            id="webhook-complete",
        ),
        pytest.param(
            {"status": CHECKOUT_EXPIRED, "active_order_id": None},
            CHECKOUT_EXPIRED,
            StartResult.UNAVAILABLE,
            id="reconciled-expired",
        ),
    ],
)
def test_result_after_webhook_or_reconcile_only_fills_missing_fields(
    db: Session,
    concurrent: dict[str, Any],
    expected_status: str,
    expected_result: StartResult,
) -> None:
    """第 3 条「若 webhook 已先把它记为 complete（或对账已记为 expired），
    只补齐缺失的会话 ID 与地址，不改状态与付款结果」：创建调用进行中别处先
    改了记录，调用返回后只补地址（与缺失的会话 ID），状态、付款状态、
    PaymentIntent 与完成时间保持别处写的值。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)

    def concurrent_update() -> None:
        stmt = update(StripeCheckout).where(StripeCheckout.order_id == order_id)
        stmt = stmt.values(**concurrent).execution_options(synchronize_session=False)
        db.execute(stmt)
        db.commit()

    gateway = _HookGateway(concurrent_update, _ok(_session(payment_intent="pi_from_response")))

    start = _start(db, gateway, order_id)

    assert start.result is expected_result
    [record] = _records(db, order_id)
    assert record.status == expected_status
    assert record.checkout_session_id == "cs_test_new"
    assert record.checkout_url == NEW_URL
    if expected_status == CHECKOUT_COMPLETE:
        assert record.payment_status == PAYMENT_PAID
        assert record.payment_intent_id == "pi_from_webhook"
        assert record.completed_at == NOW - timedelta(seconds=5)
    else:
        assert record.payment_status is None
        assert record.payment_intent_id is None
        assert record.active_order_id is None


# ---------------------------------------------------------------- 对账：Stripe 到期时间之后


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("open", ReconcileOutcome.OPEN),
        ("complete", ReconcileOutcome.COMPLETE),
        ("expired", ReconcileOutcome.EXPIRED),
    ],
)
def test_reconcile_after_expiry_replays_then_retrieves_the_actual_status(
    db: Session, actual: str, expected: ReconcileOutcome
) -> None:
    """第 3 条「快照里的 Stripe 到期时间过后，以同一幂等键再请求一次来对账……
    Stripe 回放原会话，说明当时已建成，记下会话后按它的实际状态处理（已付款
    按第 4 条确认，已过期才算 expired）」；核验结论「内容是当初保存的响应
    （status 仍为 open），须再查询会话才得实际状态」：回放之后按会话 ID
    查询一次，按查询到的状态记下。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    stored = _record(db, checkout_id)
    key, snapshot = stored.idempotency_key, deserialize_form(stored.create_params)
    replay = _ok(_session("open", session_id="cs_test_replayed"), replayed=True)
    current_url = "https://checkout.stripe.example.com/c/pay/cs_test_replayed"
    current = _session(
        actual,
        session_id="cs_test_replayed",
        url=current_url if actual == "open" else None,
        payment_status=PAYMENT_PAID if actual == "complete" else PAYMENT_UNPAID,
    )
    gateway = FakeStripeGateway(replay, _ok(current))

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, AFTER_STRIPE_EXPIRY)

    assert outcome is expected
    create, retrieve = gateway.calls
    assert (create.method, create.path) == ("POST", SESSIONS_PATH)
    assert create.idempotency_key == key
    assert create.form == snapshot
    assert (retrieve.method, retrieve.path) == ("GET", f"{SESSIONS_PATH}/cs_test_replayed")
    assert retrieve.form == EXPAND
    record = _record(db, checkout_id)
    assert record.status == actual
    assert record.checkout_session_id == "cs_test_replayed"
    if actual == "expired":
        assert record.active_order_id is None
    else:
        assert record.active_order_id == order_id
    if actual == "complete":
        assert record.completed_at == AFTER_STRIPE_EXPIRY
        assert record.payment_status == PAYMENT_PAID
    else:
        assert record.completed_at is None


@pytest.mark.parametrize(
    "retrieved",
    [UNAVAILABLE, _rejected(404, "invalid_request_error", "id")],
    ids=["unavailable", "404"],
)
def test_reconcile_after_expiry_replay_without_a_retrieve_waits(
    db: Session, retrieved: StripeResult
) -> None:
    """核验结论「须再查询会话才得实际状态」；第 3 条「Stripe 不可用或答复
    冲突则留到下一轮」：回放之后查询不成，不按回放内容记 open，记录保持
    creating。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_ok(_session("open"), replayed=True), retrieved)

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, AFTER_STRIPE_EXPIRY)

    assert outcome is ReconcileOutcome.PENDING
    assert len(gateway.calls) == 2
    assert _record(db, checkout_id).status == CHECKOUT_CREATING


def test_reconcile_after_expiry_expires_at_rejection_marks_expired(db: Session) -> None:
    """第 3 条「Stripe 以参数不合格（到期时间已过）拒绝，按 Stripe 幂等规则
    说明原请求没有建成会话，记为 expired」；核验结论「带过去的 `expires_at`
    被 400 拒绝（param 为 expires_at），即原请求没有建成」：只请求一次、
    不查询，记录 expired 并释放活动会话位。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_rejected(param="expires_at"))

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, STRIPE_EXPIRES)

    assert outcome is ReconcileOutcome.EXPIRED
    assert len(gateway.calls) == 1
    record = _record(db, checkout_id)
    assert record.status == CHECKOUT_EXPIRED
    assert record.active_order_id is None


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(_rejected(400, "invalid_request_error", "locale"), id="400-other-param"),
        pytest.param(_rejected(401, "invalid_request_error", "expires_at"), id="401"),
        pytest.param(LIVE, id="live"),
        pytest.param(UNAVAILABLE, id="unavailable"),
    ],
)
def test_reconcile_after_expiry_other_results_wait(db: Session, result: StripeResult) -> None:
    """第 3 条「Stripe 不可用或答复冲突则留到下一轮」；验收：「其他结果留待
    下次」：到期之后的请求得到其他结果时记录保持 creating。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(result)

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, AFTER_STRIPE_EXPIRY)

    assert outcome is ReconcileOutcome.PENDING
    assert _record(db, checkout_id).status == CHECKOUT_CREATING


def test_reconcile_before_expiry_param_error_marks_expired(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「以同一幂等键与同一快照重试」；验收：「Stripe 到期时间之前，
    以同一幂等键与同一快照重试，结果按上一条处理」：单独调用对账函数
    （SHOP-TASK-114、116 的用法）时，400 参数错误同样记 expired 并记日志，
    返回 expired 而不另建记录。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_rejected(param="mode"))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        outcome = reconcile_creating_checkout(db, gateway, checkout_id, NOW)

    assert outcome is ReconcileOutcome.EXPIRED
    assert len(_records(db, order_id)) == 1
    assert _messages(caplog) == [f"{LOG_CREATE_REJECTED} checkout={checkout_id}"]


@pytest.mark.parametrize("status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE, CHECKOUT_EXPIRED])
def test_reconcile_of_a_record_no_longer_creating_does_not_call_stripe(
    db: Session, status: str
) -> None:
    """第 3 条「只在记录仍为 creating 时改为 `open`」：别处已处理过的记录
    不再调用 Stripe，按当前状态返回。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, status)
    gateway = FakeStripeGateway()

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, NOW)

    assert outcome == status
    assert gateway.calls == []


# ---------------------------------------------------------------- 24 小时转人工


def test_creating_for_24_hours_goes_to_review_once(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对，订单在此
    期间不取消、库存继续预留」；验收：「写告警时间并记一条 error 日志（类别
    stripe_checkout_needs_review，只含记录 ID，同一记录只记一次）……发起返回
    under_review」：满 24 小时不再调用 Stripe；再次发起与对账都返回
    under_review、不再记日志；订单已过本地支付到期时间也不取消。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(_ok(), _ok())
    review = NOW + timedelta(hours=24)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        first = _start(db, gateway, order_id, review)
        again = _start(db, gateway, order_id, review + timedelta(hours=1))
        job = reconcile_creating_checkout(db, gateway, checkout_id, review + timedelta(hours=2))

    assert first.result is StartResult.UNDER_REVIEW
    assert again.result is StartResult.UNDER_REVIEW
    assert job is ReconcileOutcome.UNDER_REVIEW
    assert gateway.calls == []
    record = _record(db, checkout_id)
    assert record.status == CHECKOUT_CREATING
    assert record.alerted_at == review
    assert record.active_order_id == order_id
    assert _messages(caplog) == [f"{LOG_NEEDS_REVIEW} checkout={checkout_id}"]
    assert _order_status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_creating_just_under_24_hours_still_calls_stripe(db: Session) -> None:
    """第 3 条「24 小时内仍无法确定」：差一秒满 24 小时时照常向 Stripe 对账，
    不写告警时间。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING)
    gateway = FakeStripeGateway(UNAVAILABLE)
    almost = NOW + timedelta(hours=24) - timedelta(seconds=1)

    outcome = reconcile_creating_checkout(db, gateway, checkout_id, almost)

    assert outcome is ReconcileOutcome.PENDING
    assert len(gateway.calls) == 1
    assert _record(db, checkout_id).alerted_at is None


# ---------------------------------------------------------------- 唯一约束


@pytest.mark.parametrize("second", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_a_second_active_record_for_the_same_order_is_rejected(db: Session, second: str) -> None:
    """第 3 条「同一订单至多一条不是 expired 的记录，由数据库唯一约束保证」：
    已有 creating 记录时，同一订单再写一条不是 expired 的记录被唯一约束
    拦下；expired 的记录不受限。"""
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_CREATING)
    _checkout(db, order_id, CHECKOUT_EXPIRED)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        _checkout(db, order_id, second)
    db.rollback()

"""支付页视图的 Stripe 字段（app/services/payment.py 的 pay_orders）与按订单确认 Stripe 付款
（app/services/stripe_confirm.py 的 confirm_order_payment）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2、3、4、6 条，docs/UX.md 0.11
的 P06、P07（服务端给出支付途径；未发起、创建结果未定、转人工核对、有进行中的会话、已有付款完成的
会话；结果页的成功、确认中、尚未付款、已取消且已发起退款），以及 docs/HANDOFF.md 0.53 的落实决定
（核对不符的已付款会话写完退款尝试后标为 expired、释放活动会话位）与 0.54 的落实写法（结果页的
确认结果为 paid、confirming、not_paid、refunded 四种，Stripe 不可用与确认返回 needs_review 都按
「确认中」显示；P06 打开时已有 complete 会话由前端调用确认接口，订单视图保持只读）。每条测试
（参数化的测试是每个用例）的文档字符串写明它守住的设计或 UX 原句；只出自 SHOP-TASK-119 验收
标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表，订单、会话记录与
退款尝试直接按模型写入。Stripe 一律用 FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。
被测函数会提交与回滚，测试数据都先提交。SQLite 不支持行锁，加锁顺序只按代码审阅，这里不验证。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import Order, OrderEvent, SiteSetting, StripeCheckout, StripeRefund
from app.models.order import (
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.site import PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import payment
from app.services.payment import PayOrder, pay_orders
from app.services.stripe_confirm import OrderPaymentResult, confirm_order_payment
from app.services.stripe_gateway import (
    CheckoutSession,
    FakeStripeGateway,
    PaymentIntentInfo,
    StripeOutcome,
    StripeResult,
)

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起，5 分钟时查看或确认。
CREATED = datetime(2026, 10, 18, 2, 0)
STARTED = CREATED + timedelta(minutes=1)
NOW = CREATED + timedelta(minutes=5)
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)
# 创建满 24 小时仍无法确定、已写告警时间（SHOP-TASK-112 的转人工核对）。
ALERTED = STARTED + timedelta(hours=24)

# 游客订单：商品小计 6000 仙、运费 800 仙，应付 6800 仙，高于默认最低收款额 200 仙。
SUBTOTAL = 6000
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING

FINGERPRINT = "0f" * 32
UNAVAILABLE = StripeResult(StripeOutcome.UNAVAILABLE)

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
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


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不读本地 .env：最低收款额取默认的 200 仙。
    monkeypatch.setattr(payment, "get_settings", lambda: Settings(_env_file=None))


def _setting(db: Session, method: str) -> None:
    row = SiteSetting(id=1, sms_verification_enabled=False, payment_method=method)
    row.updated_at = CREATED
    db.add(row)
    db.commit()


def _order(db: Session, *, status: str = STATUS_AWAITING_PAYMENT) -> int:
    """游客订单（没有券与积分，应付 6800 仙）；写完即提交，返回订单 ID。"""
    n = next(_SEQUENCE)
    order = Order(
        order_number=f"{n:016d}",
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=TOTAL,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=CREATED,
        payment_expires_at=CREATED + timedelta(minutes=15),
        paid_at=CREATED if status == STATUS_PAID else None,
        member_id=None,
        claim_status=CLAIM_OPEN,
    )
    db.add(order)
    db.commit()
    return order.id


def _checkout(
    db: Session,
    order_id: int,
    status: str = CHECKOUT_OPEN,
    *,
    payment_status: str = PAYMENT_UNPAID,
    alerted_at: datetime | None = None,
    confirmed: bool = False,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），提交后返回记录 ID。"""
    n = next(_SEQUENCE)
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "create_params": "[]",
        "expires_at": STRIPE_EXPIRES,
        "created_at": STARTED,
        "alerted_at": alerted_at,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    if status != CHECKOUT_CREATING:
        fields["checkout_session_id"] = f"cs_test_{n}"
        fields["payment_status"] = payment_status
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status == CHECKOUT_COMPLETE:
        fields["completed_at"] = STARTED
    if confirmed:
        fields["confirmed_at"] = STARTED
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return record.id


def _refund(db: Session, checkout_id: int, status: str = STRIPE_REFUND_PENDING) -> None:
    """直接写一条来源为该会话的退款尝试（模拟之前的迟到付款处理或退款任务留下的）。"""
    db.add(
        StripeRefund(
            payment_intent_id=f"pi_test_{checkout_id}",
            source_type=SOURCE_CHECKOUT,
            checkout_id=checkout_id,
            amount_sen=TOTAL,
            status=status,
            call_count=1,
            created_at=STARTED,
            succeeded_at=STARTED if status == STRIPE_REFUND_SUCCEEDED else None,
            active_checkout_id=None if status == STRIPE_REFUND_FAILED else checkout_id,
        )
    )
    db.commit()


def _record(db: Session, checkout_id: int) -> StripeCheckout:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.id == checkout_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _record_state(db: Session, checkout_id: int) -> dict[str, Any]:
    record = _record(db, checkout_id)
    return {name: getattr(record, name) for name in StripeCheckout.__table__.columns.keys()}


def _status(db: Session, order_id: int) -> str:
    db.rollback()
    return db.scalars(select(Order.status).where(Order.id == order_id)).one()


def _event_count(db: Session, order_id: int) -> int:
    db.rollback()
    return len(db.scalars(select(OrderEvent.id).where(OrderEvent.order_id == order_id)).all())


def _refund_statuses(db: Session, checkout_id: int) -> list[str]:
    db.rollback()
    stmt = select(StripeRefund.status).where(StripeRefund.checkout_id == checkout_id)
    return list(db.scalars(stmt.order_by(StripeRefund.id)))


def _session(db: Session, checkout_id: int, status: str, **overrides: Any) -> CheckoutSession:
    """与记录、订单相符的会话对象（未付款）；已付款时 payment_intent 已展开、方式为 card。
    overrides 改其中的字段。"""
    record = _record(db, checkout_id)
    paid = overrides.get("payment_status") == PAYMENT_PAID
    fields: dict[str, Any] = {
        "id": record.checkout_session_id or f"cs_test_for_{checkout_id}",
        "status": status,
        "payment_status": PAYMENT_UNPAID,
        "livemode": False,
        "currency": "myr",
        "amount_total": TOTAL,
        "client_reference_id": str(record.order_id),
        "order_id": str(record.order_id),
        "create_key": record.idempotency_key,
        "url": None,
        "payment_intent": None,
    }
    if paid:
        fields["payment_intent"] = PaymentIntentInfo(
            id=f"pi_test_{checkout_id}",
            status="succeeded",
            livemode=False,
            payment_method_type="card",
        )
    fields.update(overrides)
    db.rollback()
    return CheckoutSession(**fields)


def _ok(value: CheckoutSession) -> StripeResult:
    return StripeResult(StripeOutcome.OK, value)


def _view(db: Session, order_id: int) -> PayOrder:
    (view,) = pay_orders(db, [order_id], "en", NOW)
    return view


def _stripe_fields(view: PayOrder) -> tuple[str | None, str | None, bool]:
    return (view.payment_route, view.stripe_state, view.stripe_refunded)


# ---------------------------------------------------------------------------
# 支付页视图：payment_route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_row", [False, True], ids=["no-setting-row", "simulated"])
def test_route_simulated_by_switch(db: Session, with_row: bool) -> None:
    """DESIGN 1.12 第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有模拟支付
    流程不变」；UX P06「开关为『模拟』……是模拟」。"""
    if with_row:
        _setting(db, PAYMENT_METHOD_SIMULATED)
    order_id = _order(db)

    assert _stripe_fields(_view(db, order_id)) == ("simulated", None, False)


def test_route_stripe_by_switch(db: Session) -> None:
    """UX P06「开关为『Stripe 测试』且应付达到 Stripe 最低收款额时是 Stripe 测试」；UX P06 未发起
    （本单没有 Stripe 记录）。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)

    assert _stripe_fields(_view(db, order_id)) == ("stripe", None, False)


def test_route_stripe_at_exact_minimum(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """DESIGN 1.12 第 2 条：应付「低于」最低收款额才走模拟支付，等于时为 Stripe；验收：最低收款额
    与 submit_payment 一样取 get_settings().stripe_min_amount_sen。"""
    settings = Settings(_env_file=None, stripe_min_amount_sen=TOTAL)
    monkeypatch.setattr(payment, "get_settings", lambda: settings)
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)

    assert _view(db, order_id).payment_route == "stripe"


def test_route_simulated_below_minimum(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """DESIGN 1.12 第 2 条「订单应付低于 Stripe 对 MYR 的最低收款额……时，即使为『Stripe 测试』
    也走模拟支付」；UX P06「应付低于 Stripe 最低收款额……的订单，即使开关为『Stripe 测试』也显示
    模拟支付页面」。"""
    settings = Settings(_env_file=None, stripe_min_amount_sen=TOTAL + 1)
    monkeypatch.setattr(payment, "get_settings", lambda: settings)
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db)

    assert _stripe_fields(_view(db, order_id)) == ("simulated", None, False)


@pytest.mark.parametrize("record_status", [CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_route_stripe_by_record_regardless_of_switch(
    db: Session, monkeypatch: pytest.MonkeyPatch, record_status: str
) -> None:
    """DESIGN 1.12 第 2 条「订单只要有一条不是 expired 的 Stripe 记录（creating、open 或
    complete），不论开关当前取值，都不接受模拟支付」；UX P06「不论开关当前取值与订单金额，一律是
    Stripe 测试」。开关为模拟、应付低于最低收款额也一样。"""
    settings = Settings(_env_file=None, stripe_min_amount_sen=TOTAL + 1)
    monkeypatch.setattr(payment, "get_settings", lambda: settings)
    _setting(db, PAYMENT_METHOD_SIMULATED)
    order_id = _order(db)
    _checkout(db, order_id, record_status)

    assert _view(db, order_id).payment_route == "stripe"


def test_route_ignores_expired_record(db: Session) -> None:
    """DESIGN 1.12 第 2 条「为『模拟』且没有 Stripe 记录（或只有 expired 记录）时，现有模拟支付
    流程不变」。"""
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_EXPIRED)

    assert _stripe_fields(_view(db, order_id)) == ("simulated", None, False)


@pytest.mark.parametrize("status", [STATUS_PAID, STATUS_CANCELLED])
def test_route_null_when_not_awaiting(db: Session, status: str) -> None:
    """验收：payment_route 只在订单为 awaiting_demo_payment 时给出，其他状态为 null（UX P06 的支付
    途径只用于待支付订单的操作区）。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    order_id = _order(db, status=status)

    assert _stripe_fields(_view(db, order_id)) == (None, None, False)


# ---------------------------------------------------------------------------
# 支付页视图：stripe_state 与 stripe_refunded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record_status", "alerted_at", "expected"),
    [
        (CHECKOUT_CREATING, None, "creating"),
        (CHECKOUT_CREATING, ALERTED, "under_review"),
        (CHECKOUT_OPEN, None, "open"),
        (CHECKOUT_COMPLETE, None, "complete"),
    ],
    ids=["creating", "creating-alerted", "open", "complete"],
)
def test_stripe_state(
    db: Session, record_status: str, alerted_at: datetime | None, expected: str
) -> None:
    """UX P06：创建结果未定（记录为创建中）、「该记录已超过 24 小时仍无法确定、转人工核对」（DESIGN
    1.12 第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对」）、有进行中的会话、「本单若
    已有付款完成的会话」各自由服务端给出。"""
    order_id = _order(db)
    _checkout(db, order_id, record_status, alerted_at=alerted_at)

    assert _stripe_fields(_view(db, order_id)) == ("stripe", expected, False)


def test_stripe_state_open_with_alert_stays_open(db: Session) -> None:
    """验收：只有 creating 且告警时间非空才是 under_review；其他状态的告警时间（如第 5 条会话完成
    24 小时后的告警）不改 stripe_state。"""
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_COMPLETE, alerted_at=ALERTED)

    assert _view(db, order_id).stripe_state == "complete"


def test_stripe_state_null_with_only_expired_records(db: Session) -> None:
    """UX P06 未发起「本单没有 Stripe 记录或只有已作废的」：只有 expired 记录时 stripe_state 为
    null。"""
    order_id = _order(db)
    _checkout(db, order_id, CHECKOUT_EXPIRED)
    _checkout(db, order_id, CHECKOUT_EXPIRED)

    assert _view(db, order_id).stripe_state is None


def test_stripe_state_on_paid_order(db: Session) -> None:
    """验收：stripe_state 不看订单状态，已由会话付款的订单仍给出它的 complete 记录；
    payment_route 为 null。"""
    order_id = _order(db, status=STATUS_PAID)
    _checkout(db, order_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID, confirmed=True)

    assert _stripe_fields(_view(db, order_id)) == (None, "complete", False)


@pytest.mark.parametrize(
    "refund_status", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED, STRIPE_REFUND_FAILED]
)
def test_refunded_after_late_payment(db: Session, refund_status: str) -> None:
    """DESIGN 1.12 第 6 条「确认时订单已不是待支付且不是由这个会话付款（已取消……），订单状态不变，
    服务端对这笔付款……全额退款，记为来源为该会话的 StripeRefund 尝试」；UX P07「服务端告知本单有
    来源为会话的 Stripe 退款尝试时（不论该尝试当前状态……）」。"""
    order_id = _order(db, status=STATUS_CANCELLED)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, payment_status=PAYMENT_PAID)
    _refund(db, checkout_id, refund_status)

    assert _stripe_fields(_view(db, order_id)) == (None, None, True)


def test_refunded_mismatch_keeps_order_awaiting(db: Session) -> None:
    """HANDOFF 0.53：核对不符的已付款会话写完退款尝试后标为 expired，「订单随后可再发起支付」；
    验收：stripe_refunded 不看订单状态，订单仍待支付时也为真，途径照常判定。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, payment_status=PAYMENT_PAID)
    _refund(db, checkout_id)

    assert _stripe_fields(_view(db, order_id)) == ("simulated", None, True)


def test_view_batches_orders_and_does_not_write(engine: Engine, db: Session) -> None:
    """HANDOFF 0.54「P06 打开时已有 complete 会话，由前端调用确认接口完成『服务端先确认』，订单
    视图保持只读」；验收：pay_orders 按订单批量读取，保持只读、不调用 Stripe、不加锁。几张订单
    一次读取，各自的取值不串，期间只发 SELECT。"""
    _setting(db, PAYMENT_METHOD_STRIPE_TEST)
    plain = _order(db)
    creating = _order(db)
    _checkout(db, creating, CHECKOUT_CREATING, alerted_at=ALERTED)
    complete = _order(db)
    complete_id = _checkout(db, complete, CHECKOUT_COMPLETE)
    late = _order(db, status=STATUS_CANCELLED)
    _refund(db, _checkout(db, late, CHECKOUT_EXPIRED, payment_status=PAYMENT_PAID))
    before = _record_state(db, complete_id)
    db.rollback()

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        views = pay_orders(db, [late, complete, plain, creating], "en", NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert [_stripe_fields(view) for view in views] == [
        (None, None, True),
        ("stripe", "complete", False),
        ("stripe", None, False),
        ("stripe", "under_review", False),
    ]
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert not db.new and not db.dirty and not db.deleted
    assert _record_state(db, complete_id) == before
    assert _status(db, complete) == STATUS_AWAITING_PAYMENT


# ---------------------------------------------------------------------------
# confirm_order_payment
# ---------------------------------------------------------------------------


def test_confirm_paid_session(db: Session) -> None:
    """DESIGN 1.12 第 4 条「全部相符且订单仍为 awaiting_demo_payment 时，按模拟支付成功的同一事务
    进入 demo_paid」；UX P07 成功。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_COMPLETE)
    session = _session(db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(_ok(session))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.PAID
    assert len(gateway.calls) == 1
    assert _status(db, order_id) == STATUS_PAID
    assert _record(db, checkout_id).confirmed_at == NOW


def test_confirm_repeat_returns_paid_without_stripe(db: Session) -> None:
    """DESIGN 1.12 第 4 条「同一会话或同一 PaymentIntent 的重复确认（订单已由它付款）直接返回原
    结果，不退款、不重复发放」；验收：支付时间非空时不调用 Stripe。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_COMPLETE)
    session = _session(db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID)
    assert confirm_order_payment(db, FakeStripeGateway(_ok(session)), order_id, NOW) == "paid"
    events = _event_count(db, order_id)

    gateway = FakeStripeGateway()
    result = confirm_order_payment(db, gateway, order_id, NOW + timedelta(minutes=1))

    assert result is OrderPaymentResult.PAID
    assert gateway.calls == []
    assert _event_count(db, order_id) == events


def test_confirm_paid_order_without_record(db: Session) -> None:
    """验收：订单的支付时间非空时直接返回 paid，不调用 Stripe（已由模拟支付付款的订单也一样）。"""
    order_id = _order(db, status=STATUS_PAID)
    gateway = FakeStripeGateway()

    assert confirm_order_payment(db, gateway, order_id, NOW) is OrderPaymentResult.PAID
    assert gateway.calls == []


@pytest.mark.parametrize("alerted_at", [None, ALERTED], ids=["creating", "under-review"])
def test_confirm_creating_is_not_paid_without_stripe(
    db: Session, alerted_at: datetime | None
) -> None:
    """UX P06 创建结果未定「服务端还没有付款页地址」：没有可付款的会话，P07 为尚未付款；验收：
    creating 时不调用 Stripe（创建结果由发起与定时任务按第 3 条对账），记录与订单不变。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_CREATING, alerted_at=alerted_at)
    before = _record_state(db, checkout_id)
    gateway = FakeStripeGateway()

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.NOT_PAID
    assert gateway.calls == []
    assert _record_state(db, checkout_id) == before
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize("record_status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_confirm_stripe_unavailable_is_confirming(db: Session, record_status: str) -> None:
    """UX P07 确认中「服务端尚未确认（付款结果未定，或查询时 Stripe 暂不可用）」；HANDOFF 0.54
    「Stripe 不可用……按『确认中』显示」。订单与记录不变。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, record_status)
    before = _record_state(db, checkout_id)
    gateway = FakeStripeGateway(UNAVAILABLE)

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.CONFIRMING
    assert len(gateway.calls) == 1
    assert _record_state(db, checkout_id) == before
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_confirm_open_unpaid_is_not_paid(db: Session) -> None:
    """UX P07 尚未付款「本单的会话仍进行中、未付款（例如直接打开本页）」；DESIGN 1.12 第 4 条
    「返回地址本身不作依据」。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_OPEN)
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, CHECKOUT_OPEN)))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.NOT_PAID
    assert len(gateway.calls) == 1
    assert _record(db, checkout_id).status == CHECKOUT_OPEN
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize("record_status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_confirm_complete_payment_pending_is_confirming(db: Session, record_status: str) -> None:
    """UX P07 确认中「服务端尚未确认（付款结果未定……）」：会话已 complete 而付款未定时为
    confirming，记录为（或更新为）complete，订单仍待支付。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, record_status)
    gateway = FakeStripeGateway(_ok(_session(db, checkout_id, CHECKOUT_COMPLETE)))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.CONFIRMING
    assert _record(db, checkout_id).status == CHECKOUT_COMPLETE
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_confirm_needs_review_is_confirming(db: Session) -> None:
    """HANDOFF 0.54「确认返回 needs_review 都按『确认中』显示」：金额不符而会话缺 amount_total、
    没写成退款尝试（第 6 条交人工核对），订单仍待支付、记录保留活动位。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_COMPLETE)
    session = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID, amount_total=None
    )
    gateway = FakeStripeGateway(_ok(session))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.CONFIRMING
    assert _refund_statuses(db, checkout_id) == []
    assert _record(db, checkout_id).active_order_id == order_id
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_confirm_late_payment_on_cancelled_order_is_refunded(db: Session) -> None:
    """DESIGN 1.12 第 6 条「确认时订单已不是待支付且不是由这个会话付款（已取消……），订单状态不变，
    服务端对这笔付款在 Stripe 测试环境全额退款」；UX P07 已取消、已发起退款。"""
    order_id = _order(db, status=STATUS_CANCELLED)
    checkout_id = _checkout(db, order_id, CHECKOUT_COMPLETE)
    session = _session(db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID)
    gateway = FakeStripeGateway(_ok(session))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.REFUNDED
    assert _refund_statuses(db, checkout_id) == [STRIPE_REFUND_PENDING]
    assert _record(db, checkout_id).status == CHECKOUT_EXPIRED
    assert _status(db, order_id) == STATUS_CANCELLED


def test_confirm_mismatch_on_awaiting_order_is_not_paid(db: Session) -> None:
    """DESIGN 1.12 第 6 条「金额、币种不符，订单状态不变，服务端对这笔付款……全额退款」与 HANDOFF
    0.53「订单随后可再发起支付或按现有规则取消」：退款后订单仍待支付，结果为尚未付款，可再付款。"""
    order_id = _order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_OPEN)
    session = _session(
        db, checkout_id, CHECKOUT_COMPLETE, payment_status=PAYMENT_PAID, amount_total=TOTAL - 100
    )
    gateway = FakeStripeGateway(_ok(session))

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.NOT_PAID
    assert _refund_statuses(db, checkout_id) == [STRIPE_REFUND_PENDING]
    assert _record(db, checkout_id).active_order_id is None
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_confirm_without_record_after_late_refund(db: Session) -> None:
    """UX P07 已取消、已发起退款（「不论该尝试当前状态」）：迟到付款已处理、记录已 expired 时不再
    调用 Stripe，订单已取消且有来源为 checkout 的退款尝试即为 refunded。"""
    order_id = _order(db, status=STATUS_CANCELLED)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, payment_status=PAYMENT_PAID)
    _refund(db, checkout_id, STRIPE_REFUND_FAILED)
    gateway = FakeStripeGateway()

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.REFUNDED
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("status", "expired_record", "refunded"),
    [
        (STATUS_AWAITING_PAYMENT, False, False),
        (STATUS_AWAITING_PAYMENT, True, True),
        (STATUS_CANCELLED, False, False),
        (STATUS_CANCELLED, True, False),
    ],
    ids=["not-started", "mismatch-refunded", "cancelled", "cancelled-expired"],
)
def test_confirm_without_record_is_not_paid(
    db: Session, status: str, expired_record: bool, refunded: bool
) -> None:
    """UX P06 未发起「本单没有 Stripe 记录或只有已作废的」与 HANDOFF 0.53（不符的付款退款后订单仍
    待支付、可再发起支付）：没有不是 expired 的记录时不调用 Stripe；订单仍待支付，或已取消而没有
    来源为 checkout 的退款尝试，都为 not_paid。"""
    order_id = _order(db, status=status)
    if expired_record:
        checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED)
        if refunded:
            _refund(db, checkout_id)
    gateway = FakeStripeGateway()

    result = confirm_order_payment(db, gateway, order_id, NOW)

    assert result is OrderPaymentResult.NOT_PAID
    assert gateway.calls == []
    assert _status(db, order_id) == status


def test_confirm_rejects_aware_now(db: Session) -> None:
    """验收：入参的当前时间为不带时区的 UTC，与 confirm_checkout 一致；带时区时先于访问数据库抛
    ValueError。"""
    order_id = _order(db)
    gateway = FakeStripeGateway()

    with pytest.raises(ValueError):
        confirm_order_payment(db, gateway, order_id, NOW.replace(tzinfo=UTC))
    assert gateway.calls == []

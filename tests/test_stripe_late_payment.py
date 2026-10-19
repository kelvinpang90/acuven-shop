"""迟到与不符的 Stripe 测试付款，与已为 expired 的会话记录的去向。

被测模块为 app/services/stripe_confirm.py。依据 docs/DESIGN.md 1.12（提交 acf576e）
「Stripe 测试环境支付」第 3 条末句、第 6 条与第 8 条的合计上限，以及
docs/HANDOFF.md 0.53 记录的 Kelvin 2026-10-15 落实决定（核对不符的已付款会话
写完退款尝试后把记录标为 expired、释放订单的活动会话位）。每条测试（参数化的
测试是每个用例）的文档字符串写明它守住的设计原句或落实决定；只出自
SHOP-TASK-117 验收标准的约定注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表，
订单与会话记录直接按模型写入。Stripe 一律用 SHOP-TASK-111 的 FakeStripeGateway
（第 9 条：CI 与本地测试不调用 Stripe）；本任务只写 pending 的退款尝试，不调用
Stripe 退款接口。被测函数会提交与回滚，测试数据都先提交。SQLite 不支持行锁，
加锁顺序与并发下的排队只按代码与 MySQL 行为审阅，这里不验证。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Order, OrderEvent, StripeCheckout, StripeRefund
from app.models.order import (
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
)
from app.services import payment
from app.services.stripe_confirm import (
    LOG_LATE_PAYMENT,
    LOG_LATE_UNREFUNDABLE,
    LOG_REFUND_CAP,
    LOG_SESSION_MISMATCH,
    ConfirmResult,
    confirm_checkout,
)
from app.services.stripe_gateway import CheckoutSession, FakeStripeGateway, PaymentIntentInfo

# 订单在 CREATED 下单，支付到期为下单加 15 分钟；访客在下单 1 分钟后发起，5 分钟时确认。
CREATED = datetime(2026, 10, 18, 2, 0)
STARTED = CREATED + timedelta(minutes=1)
NOW = CREATED + timedelta(minutes=5)
LATER = NOW + timedelta(minutes=1)
STRIPE_EXPIRES = STARTED + timedelta(minutes=31)
# 告警时间先因别的原因（SHOP-TASK-112、116）写过。
EARLIER_ALERT = CREATED + timedelta(minutes=2)

# 游客订单：商品小计 6000 仙、运费 800 仙，应付 6800 仙。
SUBTOTAL = 6000
SHIPPING = 800
TOTAL = SUBTOTAL + SHIPPING

FINGERPRINT = "0f" * 32
LOGGER = "app.services.stripe_confirm"

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


def _guest_order(db: Session, *, status: str = STATUS_AWAITING_PAYMENT) -> int:
    """游客订单（没有券与积分）；写完即提交，返回订单 ID。"""
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
    payment_status: str | None = PAYMENT_UNPAID,
    session_id: bool = True,
    alerted_at: datetime | None = None,
) -> int:
    """直接写一条合法的会话记录（模拟之前的发起、webhook 或对账留下的），提交后返回记录 ID。

    session_id 为假时只用于 expired：对账确认没有建成会话、本站没有记下会话 ID。
    """
    n = next(_SEQUENCE)
    fields: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"create-key-{n}",
        "amount_sen": TOTAL,
        "currency": "myr",
        "status": status,
        "payment_status": payment_status,
        "create_params": "[]",
        "expires_at": STRIPE_EXPIRES,
        "created_at": STARTED,
        "alerted_at": alerted_at,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    if session_id:
        fields["checkout_session_id"] = f"cs_test_{n}"
    if status == CHECKOUT_OPEN:
        fields["checkout_url"] = f"https://checkout.stripe.example.com/c/pay/cs_test_{n}"
    if status == CHECKOUT_COMPLETE:
        fields["completed_at"] = STARTED
    record = StripeCheckout(**fields)
    db.add(record)
    db.commit()
    return record.id


def _record(db: Session, checkout_id: int) -> StripeCheckout:
    db.rollback()
    stmt = select(StripeCheckout).where(StripeCheckout.id == checkout_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _record_state(db: Session, checkout_id: int) -> dict[str, Any]:
    record = _record(db, checkout_id)
    return {name: getattr(record, name) for name in StripeCheckout.__table__.columns.keys()}


def _object(db: Session, checkout_id: int, **overrides: Any) -> CheckoutSession:
    """与记录、订单全部相符的已付款会话对象（payment_intent 已展开、方式为 card）；
    overrides 改其中的字段。"""
    record = _record(db, checkout_id)
    fields: dict[str, Any] = {
        "id": record.checkout_session_id or f"cs_test_for_{checkout_id}",
        "status": "complete",
        "payment_status": PAYMENT_PAID,
        "livemode": False,
        "currency": "myr",
        "amount_total": TOTAL,
        "client_reference_id": str(record.order_id),
        "order_id": str(record.order_id),
        "create_key": record.idempotency_key,
        "url": None,
        "payment_intent": _intent(f"pi_test_{checkout_id}"),
    }
    fields.update(overrides)
    db.rollback()
    return CheckoutSession(**fields)


def _intent(intent_id: str) -> PaymentIntentInfo:
    return PaymentIntentInfo(
        id=intent_id, status="succeeded", livemode=False, payment_method_type="card"
    )


def _add_refund(
    db: Session, checkout_id: int, payment_intent_id: str, amount: int, status: str
) -> None:
    """直接写一条来源为该会话的退款尝试（模拟之前的迟到付款处理或退款任务留下的）。"""
    active = None if status == STRIPE_REFUND_FAILED else checkout_id
    db.add(
        StripeRefund(
            payment_intent_id=payment_intent_id,
            source_type=SOURCE_CHECKOUT,
            checkout_id=checkout_id,
            amount_sen=amount,
            status=status,
            call_count=1,
            created_at=STARTED,
            active_checkout_id=active,
        )
    )
    db.commit()


def _refunds(db: Session, checkout_id: int) -> list[tuple[Any, ...]]:
    """这个会话的退款尝试：（来源类别、PaymentIntent、金额、状态、活动来源列）。"""
    db.rollback()
    stmt = (
        select(
            StripeRefund.source_type,
            StripeRefund.payment_intent_id,
            StripeRefund.amount_sen,
            StripeRefund.status,
            StripeRefund.active_checkout_id,
        )
        .where(StripeRefund.checkout_id == checkout_id)
        .order_by(StripeRefund.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _order(db: Session, order_id: int) -> tuple[str, datetime | None, int]:
    """（状态、支付时间、事件条数）。"""
    db.rollback()
    status, paid_at = db.execute(
        select(Order.status, Order.paid_at).where(Order.id == order_id)
    ).one()
    events = db.scalars(select(OrderEvent.id).where(OrderEvent.order_id == order_id)).all()
    return status, paid_at, len(events)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


def _confirm(
    db: Session,
    checkout_id: int,
    stripe_session: CheckoutSession,
    now: datetime = NOW,
) -> ConfirmResult:
    return confirm_checkout(db, FakeStripeGateway(), checkout_id, stripe_session, now)


def _pending_refund(checkout_id: int, amount: int = TOTAL) -> tuple[Any, ...]:
    return (SOURCE_CHECKOUT, f"pi_test_{checkout_id}", amount, STRIPE_REFUND_PENDING, checkout_id)


# ---------------------------------------------------------------------------
# 迟到与不符的付款：写一条 pending 的退款尝试
# ---------------------------------------------------------------------------


def _late_case(db: Session, case: str) -> tuple[int, int, CheckoutSession]:
    if case == "order-cancelled":
        order_id = _guest_order(db, status=STATUS_CANCELLED)
        checkout_id = _checkout(db, order_id)
        return order_id, checkout_id, _object(db, checkout_id)
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    overrides: dict[str, Any] = {
        "amount": {"amount_total": TOTAL - 1},
        "currency": {"currency": "sgd"},
        "metadata-order": {"order_id": str(order_id + 1000)},
        "client-reference": {"client_reference_id": str(order_id + 1000)},
    }[case]
    return order_id, checkout_id, _object(db, checkout_id, **overrides)


@pytest.mark.parametrize(
    "case", ["amount", "currency", "metadata-order", "client-reference", "order-cancelled"]
)
def test_late_or_mismatched_payment_writes_one_pending_refund(
    db: Session, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条「确认时订单已不是待支付且不是由这个会话付款（已取消……），或金额、币种不符，订单
    状态不变，服务端对这笔付款在 Stripe 测试环境全额退款，记为来源为该会话的 `StripeRefund`
    尝试，并向运营告警邮件报告」；验收：「写一条来源为该会话、金额为 amount_total、状态 pending
    的退款尝试」「记一条 error 日志（类别 stripe_late_payment，只含记录 ID）」「返回 refunded」：
    订单状态与事件不变，只有一条 pending 尝试（金额为会话的 amount_total），只记一条日志。"""
    order_id, checkout_id, stripe_session = _late_case(db, case)
    order_before = _order(db, order_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, stripe_session)

    assert result is ConfirmResult.REFUNDED
    assert _order(db, order_id) == order_before
    amount = stripe_session.amount_total
    assert amount is not None
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id, amount)]
    assert _messages(caplog) == [f"{LOG_LATE_PAYMENT} checkout={checkout_id}"]


def test_repeated_confirmation_of_a_late_payment_writes_nothing_more(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 7 条「不同事件指向同一动作时只生效一次」；验收：「已有则不再写新尝试、不核对上限、
    不再记日志（重复确认与 webhook 重发都只生效一次）」：同一不符的会话再确认两次（webhook
    重发、结果页查询）都返回 refunded，仍只有一条尝试，第二、三次不记任何日志（含
    stripe_refund_cap），记录不再改。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    stripe_session = _object(db, checkout_id, currency="sgd")
    assert _confirm(db, checkout_id, stripe_session) is ConfirmResult.REFUNDED
    record_before = _record_state(db, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        again = _confirm(db, checkout_id, stripe_session, LATER)
        third = _confirm(db, checkout_id, stripe_session, LATER + timedelta(minutes=1))

    assert [again, third] == [ConfirmResult.REFUNDED] * 2
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id)]
    assert _record_state(db, checkout_id) == record_before
    assert _messages(caplog) == []


def test_session_paid_after_simulated_payment_of_the_same_order_is_refunded(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条「订单已不是待支付且不是由这个会话付款（……已由另一途径付款）……订单状态不变」；
    验收：「订单已不是待支付而该记录没有确认时间（不是由这个会话付款，含旧会话已 expired、订单
    随后经模拟支付……的情形）」：旧会话记为 expired 后订单经模拟支付进入 demo_paid，这个会话
    才迟到付款；订单的支付时间与事件不变，写一条 pending 尝试，记录保持 expired。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED)
    outcome, _ = payment.submit_payment(db, order_id, "card", "success", "pay-key-1", NOW)
    assert outcome.status == STATUS_PAID
    order_before = _order(db, order_id)
    stripe_session = _object(db, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, stripe_session, LATER)

    assert result is ConfirmResult.REFUNDED
    assert _order(db, order_id) == order_before
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id)]
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id, record.confirmed_at) == (
        CHECKOUT_EXPIRED,
        None,
        None,
    )
    assert _messages(caplog) == [f"{LOG_LATE_PAYMENT} checkout={checkout_id}"]


def test_existing_failed_attempt_is_not_replaced(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「Stripe 以请求错误明确拒绝创建……后一种不自动重试」；验收：「已有尝试为 failed 时
    也不自动另建」：这个会话已有一条 failed 尝试时不写新尝试、不记日志，返回 refunded，仍占着
    活动位的不符记录照样释放。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    _add_refund(db, checkout_id, f"pi_test_{checkout_id}", TOTAL - 1, STRIPE_REFUND_FAILED)
    before = _refunds(db, checkout_id)
    stripe_session = _object(db, checkout_id, amount_total=TOTAL - 1)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, stripe_session)

    assert result is ConfirmResult.REFUNDED
    assert _refunds(db, checkout_id) == before
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _messages(caplog) == []


# ---------------------------------------------------------------------------
# 释放活动位与没写成退款尝试的情形
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("then", ["start-again", "cancel"])
@pytest.mark.parametrize("status", [CHECKOUT_OPEN, CHECKOUT_COMPLETE])
def test_released_mismatched_record_lets_the_order_start_again_or_cancel(
    db: Session, status: str, then: str
) -> None:
    """HANDOFF 0.53 的落实决定「写完来源为该会话的退款尝试后把这条会话记录标为
    expired、释放订单的活动会话位，订单随后可再发起支付或按现有规则取消」：open 或
    complete 的记录收到金额不符的已付款会话后改为 expired、活动列为空，订单仍待支付；
    同一订单随后可再写一条活动记录（唯一约束不拦），或按现有规则取消。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, status)
    stripe_session = _object(db, checkout_id, amount_total=TOTAL + 100)

    assert _confirm(db, checkout_id, stripe_session) is ConfirmResult.REFUNDED

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.payment_status == PAYMENT_PAID
    assert record.payment_intent_id == f"pi_test_{checkout_id}"
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id, TOTAL + 100)]

    if then == "start-again":
        again = _checkout(db, order_id)
        assert _record(db, again).active_order_id == order_id
    else:
        cancelled = payment.cancel_order(db, order_id, LATER)
        assert cancelled.status == STATUS_CANCELLED


def _refund_of_another_checkout(db: Session, payment_intent_id: str, status: str) -> None:
    """同一 PaymentIntent 上另一来源的退款尝试（另一张订单的会话记录，金额 1 仙）。"""
    other = _checkout(db, _guest_order(db), CHECKOUT_EXPIRED)
    _add_refund(db, other, payment_intent_id, 1, status)


def test_refund_cap_holds_the_record_and_alerts_once(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额……超出即不建
    并告警；这同时约束第 6 条的全额退款」；验收：「因合计上限……而没写成退款尝试时保留活动位、
    订单不变」「在同一事务里把记录的付款状态写为 paid 并写告警时间」「返回 needs_review」「重复
    确认……只告警一次」：同一 PaymentIntent 已有 1 仙 pending 时不写尝试，记录仍占活动位、
    付款状态 paid、告警时间为本次时间，只记一条 stripe_refund_cap；再确认返回 needs_review、
    不记日志、告警时间不变。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    _refund_of_another_checkout(db, "pi_test_shared", STRIPE_REFUND_PENDING)
    stripe_session = _object(
        db, checkout_id, currency="sgd", payment_intent=_intent("pi_test_shared")
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        first = _confirm(db, checkout_id, stripe_session)
        again = _confirm(db, checkout_id, stripe_session, LATER)

    assert [first, again] == [ConfirmResult.NEEDS_REVIEW] * 2
    assert _refunds(db, checkout_id) == []
    record = _record(db, checkout_id)
    assert record.active_order_id == order_id
    assert record.status != CHECKOUT_EXPIRED
    assert record.payment_status == PAYMENT_PAID
    assert record.alerted_at == NOW
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _messages(caplog) == [f"{LOG_REFUND_CAP} checkout={checkout_id}"]


def test_failed_attempts_do_not_count_toward_the_cap(db: Session) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额（failed
    不计）」：同一 PaymentIntent 上另一来源的尝试为 failed 时不计入合计，照常写尝试。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    _refund_of_another_checkout(db, "pi_test_shared", STRIPE_REFUND_FAILED)
    stripe_session = _object(
        db, checkout_id, currency="sgd", payment_intent=_intent("pi_test_shared")
    )

    assert _confirm(db, checkout_id, stripe_session) is ConfirmResult.REFUNDED
    assert _refunds(db, checkout_id) == [
        (SOURCE_CHECKOUT, "pi_test_shared", TOTAL, STRIPE_REFUND_PENDING, checkout_id)
    ]


def test_session_without_amount_total_is_unrefundable_and_alerts_once(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """验收：「会话缺少 amount_total 或 PaymentIntent ID 而无法写退款尝试时不写，记一条 error
    日志（类别 stripe_late_payment_unrefundable）交人工处理」「没写成而保留活动位时返回
    needs_review」；第 6 条「订单状态不变」：缺 amount_total 的已付款会话不写尝试，记录保留
    活动位并写告警时间，只记一次日志。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    stripe_session = _object(db, checkout_id, amount_total=None)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        first = _confirm(db, checkout_id, stripe_session)
        again = _confirm(db, checkout_id, stripe_session, LATER)

    assert [first, again] == [ConfirmResult.NEEDS_REVIEW] * 2
    assert _refunds(db, checkout_id) == []
    record = _record(db, checkout_id)
    assert (record.active_order_id, record.payment_status, record.alerted_at) == (
        order_id,
        PAYMENT_PAID,
        NOW,
    )
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _messages(caplog) == [f"{LOG_LATE_UNREFUNDABLE} checkout={checkout_id}"]


def test_first_sighting_alerts_even_when_already_alerted_for_another_reason(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """验收：「告警时间先因别的原因（SHOP-TASK-112、116）写过时，首次看到付款仍会记一次」
    「告警时间已非空则不改」：记录已有告警时间而付款状态为 unpaid，超出合计上限时记一条
    stripe_refund_cap、告警时间不变；再确认不再记。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, alerted_at=EARLIER_ALERT)
    _refund_of_another_checkout(db, "pi_test_shared", STRIPE_REFUND_PENDING)
    stripe_session = _object(
        db, checkout_id, currency="sgd", payment_intent=_intent("pi_test_shared")
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        first = _confirm(db, checkout_id, stripe_session)
        again = _confirm(db, checkout_id, stripe_session, LATER)

    assert [first, again] == [ConfirmResult.NEEDS_REVIEW] * 2
    assert _record(db, checkout_id).alerted_at == EARLIER_ALERT
    assert _messages(caplog) == [f"{LOG_REFUND_CAP} checkout={checkout_id}"]


# ---------------------------------------------------------------------------
# 已为 expired 的记录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_id", [True, False], ids=["known-session", "lost-session-id"])
def test_expired_record_paid_with_the_order_still_awaiting_is_confirmed(
    db: Session, session_id: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条末句「万一有本站没记下会话 ID 的会话被付款，其 webhook 按元数据里的创建幂等键与
    订单 ID 对上这条记录，照第 4、6 条处理」；第 4 条「全部相符且订单仍为
    `awaiting_demo_payment` 时……进入 `demo_paid`」；验收：「改回时从已核对的会话对象补齐
    会话 ID、付款地址与 PaymentIntent ID，完成时间为空则写本次处理的当前时间」：
    已为 expired 的记录（含对账记为 expired、没有会话 ID 的）收到相符的已付款会话，
    订单仍待支付、没有别的活动记录，改回 complete、重新占用活动位并确认；不写退款
    尝试、不记日志。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, session_id=session_id)
    url = "https://checkout.stripe.example.com/c/pay/cs_test_found"
    stripe_session = _object(db, checkout_id, url=url)
    if not session_id:
        assert _record(db, checkout_id).checkout_session_id is None

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, stripe_session)

    assert result is ConfirmResult.PAID
    assert _order(db, order_id) == (STATUS_PAID, NOW, 1)
    record = _record(db, checkout_id)
    assert record.status == CHECKOUT_COMPLETE
    assert record.active_order_id == order_id
    assert record.checkout_session_id == stripe_session.id
    assert record.checkout_url == url
    assert record.payment_intent_id == f"pi_test_{checkout_id}"
    assert (record.completed_at, record.confirmed_at) == (NOW, NOW)
    assert _refunds(db, checkout_id) == []
    assert _messages(caplog) == []


def test_expired_record_with_mismatched_amount_is_not_revived(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 6 条「金额、币种不符，订单状态不变，服务端对这笔付款……全额退款」；验收：「改回
    complete 只在金额、币种与所属订单都相符时进行……不符时记录保持 expired、不占活动位——已付款
    按下一条处理，付款未定时只补付款状态、不写退款尝试、返回 pending」：金额不符的 complete 而
    未付款的会话只补付款状态、返回 pending；之后同一会话已付款时写一条尝试、返回 refunded，
    记录始终是 expired、活动列为空，订单仍待支付。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, payment_status=None)
    unpaid = _object(
        db, checkout_id, amount_total=TOTAL - 1, payment_status=PAYMENT_UNPAID, payment_intent=None
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        pending = _confirm(db, checkout_id, unpaid)

    assert pending is ConfirmResult.PENDING
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.payment_status == PAYMENT_UNPAID
    assert record.completed_at is None
    assert _refunds(db, checkout_id) == []
    assert _messages(caplog) == []

    paid = _object(db, checkout_id, amount_total=TOTAL - 1)
    assert _confirm(db, checkout_id, paid, LATER) is ConfirmResult.REFUNDED

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.payment_status == PAYMENT_PAID
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id, TOTAL - 1)]
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)


def test_expired_record_complete_and_unpaid_is_revived_to_wait(db: Session) -> None:
    """第 5 条「complete 而付款未定……订单继续待支付、库存继续预留，等
    `checkout.session.async_payment_succeeded` 按第 4 条确认……定时任务也定期查询该会话的付款
    状态」；验收：「订单仍为待支付且没有其他不是 expired 的记录才改回 complete 继续等待（交给
    定时任务）」：已为 expired 的记录收到相符而付款未定的 complete 会话，改回
    complete、重新占用活动位、写完成时间与付款状态，订单不变，返回 pending。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED)
    unpaid = _object(db, checkout_id, payment_status=PAYMENT_UNPAID, payment_intent=None)

    assert _confirm(db, checkout_id, unpaid) is ConfirmResult.PENDING

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_COMPLETE, order_id)
    assert (record.payment_status, record.completed_at) == (PAYMENT_UNPAID, NOW)
    assert record.confirmed_at is None
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)


@pytest.mark.parametrize("case", ["another-active-record", "order-cancelled"])
def test_expired_record_complete_and_unpaid_otherwise_keeps_expired(db: Session, case: str) -> None:
    """验收：「订单仍为待支付且没有其他不是 expired 的记录才改回 complete 继续等待……否则保留
    expired、只补付款状态，返回 pending」；第 2 条「同一订单至多一条不是 expired 的记录」：订单
    另有活动记录或已取消时，记录保持 expired、活动列为空，只补付款状态；另一条记录与
    订单不变。"""
    status = STATUS_CANCELLED if case == "order-cancelled" else STATUS_AWAITING_PAYMENT
    order_id = _guest_order(db, status=status)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED, payment_status=None)
    other = _checkout(db, order_id) if case == "another-active-record" else None
    other_before = None if other is None else _record_state(db, other)
    order_before = _order(db, order_id)
    unpaid = _object(db, checkout_id, payment_status=PAYMENT_UNPAID, payment_intent=None)

    assert _confirm(db, checkout_id, unpaid) is ConfirmResult.PENDING

    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert (record.payment_status, record.completed_at) == (PAYMENT_UNPAID, None)
    assert _order(db, order_id) == order_before
    if other is not None:
        assert _record_state(db, other) == other_before


def test_expired_record_paid_while_another_session_is_active(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """验收：「该记录已为 expired 而订单另有不是 expired 的记录」为迟到的付款（先于正常确认）
    「保留 expired 与空的活动列……不碰唯一约束」；之后「这个会话没有任何退款尝试（不论状态；已有
    尝试说明它曾按迟到付款处理，第 6、8 条：不再确认订单……返回 refunded）」：订单另有 open
    记录时旧记录的相符付款写一条尝试、保持 expired，订单与另一条记录不变；那条会话随后
    expired、订单仍待支付且再无活动记录，旧会话再确认也只返回 refunded，不确认订单、不新增
    尝试、不记日志。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED)
    other = _checkout(db, order_id)
    other_before = _record_state(db, other)
    stripe_session = _object(db, checkout_id)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, stripe_session)

    assert result is ConfirmResult.REFUNDED
    record = _record(db, checkout_id)
    assert (record.status, record.active_order_id) == (CHECKOUT_EXPIRED, None)
    assert record.payment_intent_id == f"pi_test_{checkout_id}"
    assert record.payment_method_type == "card"
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id)]
    assert _record_state(db, other) == other_before
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _messages(caplog) == [f"{LOG_LATE_PAYMENT} checkout={checkout_id}"]

    expired = _record(db, other)
    expired.status = CHECKOUT_EXPIRED
    expired.active_order_id = None
    db.commit()
    record_before = _record_state(db, checkout_id)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        again = _confirm(db, checkout_id, stripe_session, LATER)

    assert again is ConfirmResult.REFUNDED
    assert _order(db, order_id) == (STATUS_AWAITING_PAYMENT, None, 0)
    assert _record_state(db, checkout_id) == record_before
    assert _refunds(db, checkout_id) == [_pending_refund(checkout_id)]
    assert _messages(caplog) == []


def test_old_session_object_given_to_the_new_record_changes_nothing(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 3 条「webhook 按元数据里的创建幂等键与订单 ID 对上这条记录」；验收：「旧会话已
    expired、同单已有新的活动会话时，把旧会话的付款对象交给新记录确认时不迁移订单、不改写
    记录」：旧会话的对象（创建幂等键是旧记录的）交给新记录确认返回 unavailable、记
    stripe_session_mismatch，两条记录、订单与退款尝试都不变。"""
    order_id = _guest_order(db)
    old = _checkout(db, order_id, CHECKOUT_EXPIRED)
    new = _checkout(db, order_id)
    old_object = _object(db, old)
    before = (_record_state(db, old), _record_state(db, new), _order(db, order_id))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, new, old_object)

    assert result is ConfirmResult.UNAVAILABLE
    assert (_record_state(db, old), _record_state(db, new), _order(db, order_id)) == before
    assert _refunds(db, old) == [] and _refunds(db, new) == []
    assert _messages(caplog) == [f"{LOG_SESSION_MISMATCH} checkout={new}"]


@pytest.mark.parametrize("status", ["expired", "open"])
def test_expired_record_with_an_unpaid_session_changes_nothing(
    db: Session, status: str, caplog: pytest.LogCaptureFixture
) -> None:
    """验收：「记录已为 expired 而会话为 expired 或 open 且未付款时（正常的会话过期通知）不改任何
    东西、返回 pending、不记日志」：记录、订单与退款尝试都不变。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id, CHECKOUT_EXPIRED)
    unpaid = _object(
        db, checkout_id, status=status, payment_status=PAYMENT_UNPAID, payment_intent=None
    )
    before = (_record_state(db, checkout_id), _order(db, order_id))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = _confirm(db, checkout_id, unpaid)

    assert result is ConfirmResult.PENDING
    assert (_record_state(db, checkout_id), _order(db, order_id)) == before
    assert _refunds(db, checkout_id) == []
    assert _messages(caplog) == []


def test_mismatched_unpaid_active_record_is_pending_without_refund(db: Session) -> None:
    """验收：「不符而未付款的活动记录照 SHOP-TASK-113 按会话状态处理（返回 pending，不写退款
    尝试）」：open 记录收到金额不符、complete 而未付款的会话时不写尝试、不改记录。"""
    order_id = _guest_order(db)
    checkout_id = _checkout(db, order_id)
    unpaid = _object(
        db, checkout_id, amount_total=TOTAL - 1, payment_status=PAYMENT_UNPAID, payment_intent=None
    )
    before = _record_state(db, checkout_id)

    assert _confirm(db, checkout_id, unpaid) is ConfirmResult.PENDING
    assert _record_state(db, checkout_id) == before
    assert _refunds(db, checkout_id) == []

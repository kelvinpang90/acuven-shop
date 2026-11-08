"""后台「Stripe 待处理」列表（SHOP-TASK-224）。

覆盖 app/services/admin_stripe_pending.py 的 stripe_pending。依据 docs/DESIGN.md 1.22
（提交 ffb043e）「Stripe 测试环境支付」第 11 条；「失败时间」以最新一次尝试的创建时间
（attempted_at）代替，见 docs/HANDOFF.md 0.90。每条测试（参数化的测试是每个用例）的文档
字符串写明它守住的第 11 条哪一句；只出自 SHOP-TASK-224 验收标准的约定注明「验收：」。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）；订单、收货
资料、结账记录与退款尝试直接按模型写入。本任务不调用 Stripe。
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, Select, create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Order, OrderRecipient, StripeCheckout, StripeRefund
from app.models.order import (
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
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
from app.services.admin_stripe_pending import (
    FailedLateRefund,
    PendingCheckout,
    StripePendingOut,
    stripe_pending,
)
from app.services.order_rules import generate_order_number

TOTAL = 6800
SHIPPING = 800

# 收货资料：列表里不得出现。
RECIPIENT_NAME = "Siti Recipient"
RECIPIENT_PHONE = "+60123456789"
RECIPIENT_ADDRESS = "12 Jalan Rahsia"

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
    def _connect(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


def _base() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0) - timedelta(days=2)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _make_order(engine: Engine, status: str = STATUS_AWAITING_PAYMENT) -> tuple[int, str]:
    """直接写一张游客订单与它的收货资料并提交，返回订单 ID 与订单号。"""
    created = _base()
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=TOTAL - SHIPPING,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=TOTAL,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"key-{uuid.uuid4().hex}",
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=created + timedelta(minutes=5) if status in PAID_STATUSES else None,
        )
        db.add(order)
        db.flush()
        db.add(
            OrderRecipient(
                order_id=order.id,
                name=RECIPIENT_NAME,
                phone=RECIPIENT_PHONE,
                country_code="MY",
                region="MY-10",
                address=RECIPIENT_ADDRESS,
                postal_code="40000",
            )
        )
        db.commit()
        return order.id, order.order_number


def _checkout(
    engine: Engine,
    order_id: int,
    status: str,
    alerted_at: datetime | None,
    created_at: datetime | None = None,
) -> int:
    """直接写一条结账记录并提交，返回记录 ID。各状态按模型的检查约束填会话 ID、付款页地址、
    付款状态与完成时间；expired 时按第 6 条写成退款尝试后的样子（paid、活动列为空）。"""
    n = next(_SEQUENCE)
    started = created_at or _base()
    has_session = status != CHECKOUT_CREATING
    paid = status in (CHECKOUT_COMPLETE, CHECKOUT_EXPIRED)
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_{n}" if has_session else None,
            payment_intent_id=f"pi_test_{n}" if paid else None,
            amount_sen=TOTAL,
            currency="myr",
            status=status,
            payment_status=(PAYMENT_PAID if paid else PAYMENT_UNPAID) if has_session else None,
            payment_method_type="card" if paid else None,
            checkout_url=f"https://checkout.stripe.test/{n}" if has_session else None,
            create_params='[["customer_email", "demo@example.test"]]',
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            completed_at=started + timedelta(minutes=4) if paid else None,
            alerted_at=alerted_at,
            active_order_id=None if status == CHECKOUT_EXPIRED else order_id,
        )
        db.add(record)
        db.commit()
        return record.id


def _attempt(
    engine: Engine,
    checkout_id: int,
    status: str,
    created_at: datetime,
    error_type: str | None = None,
) -> int:
    """直接插入一条来源为该结账记录的尝试并提交，返回尝试 ID。"""
    n = next(_SEQUENCE)
    succeeded = status == STRIPE_REFUND_SUCCEEDED
    with Session(engine) as db:
        attempt = StripeRefund(
            payment_intent_id=f"pi_test_late_{checkout_id}",
            source_type=SOURCE_CHECKOUT,
            checkout_id=checkout_id,
            stripe_refund_id=f"re_test_{n}" if status != STRIPE_REFUND_PENDING else None,
            amount_sen=TOTAL,
            status=status,
            call_count=1,
            last_error_type=error_type,
            created_at=created_at,
            succeeded_at=created_at + timedelta(minutes=1) if succeeded else None,
            active_checkout_id=None if status == STRIPE_REFUND_FAILED else checkout_id,
        )
        db.add(attempt)
        db.commit()
        return attempt.id


def _late_checkout(engine: Engine, order_status: str = STATUS_CANCELLED) -> tuple[int, str]:
    """第 6 条处理过的迟到付款：订单（默认已取消）上一条 expired 的结账记录；返回记录 ID 与
    订单号。"""
    order_id, order_number = _make_order(engine, order_status)
    return _checkout(engine, order_id, CHECKOUT_EXPIRED, None), order_number


def _pending(engine: Engine) -> StripePendingOut:
    with Session(engine) as db:
        return stripe_pending(db)


# ---------------------------------------------------------------------------
# 第（1）项：待处理的结账记录
# ---------------------------------------------------------------------------


def test_model_and_field_names() -> None:
    """第 11 条第（1）项「每行显示结账记录 ID、记录状态、订单号、记录创建时间与告警时间」与
    第（2）项「每行显示结账记录 ID、最新尝试 ID、订单号、金额、失败时间与错误类别」：每行
    只有这些字段（失败时间为 attempted_at，HANDOFF 0.90）；验收：三个模型名与字段名照写，
    之后的接口原样返回。"""
    assert set(StripePendingOut.model_fields) == {"checkouts", "refunds"}
    assert set(PendingCheckout.model_fields) == {
        "checkout_id",
        "status",
        "order_number",
        "created_at",
        "alerted_at",
    }
    assert set(FailedLateRefund.model_fields) == {
        "checkout_id",
        "attempt_id",
        "order_number",
        "amount_sen",
        "attempted_at",
        "error_type",
    }


def test_pending_checkouts_rows_and_order(engine: Engine) -> None:
    """第 11 条第（1）项「已写告警时间、状态不是 expired、所属订单仍为
    `awaiting_demo_payment` 的 `StripeCheckout`」「按告警时间从早到晚列出，每行显示结账记录
    ID、记录状态、订单号、记录创建时间与告警时间」：creating、open、complete 都列出，按
    告警时间从早到晚；验收：同一告警时间按 ID 从小到大；时间带 UTC 时区。"""
    base = _base()
    early, late = base + timedelta(hours=25), base + timedelta(hours=30)
    rows = []
    for status, alerted in [
        (CHECKOUT_CREATING, late),
        (CHECKOUT_OPEN, early),
        (CHECKOUT_COMPLETE, early),
    ]:
        order_id, order_number = _make_order(engine)
        created = base + timedelta(minutes=next(_SEQUENCE))
        checkout_id = _checkout(engine, order_id, status, alerted, created_at=created)
        rows.append((checkout_id, status, order_number, created, alerted))

    result = _pending(engine)

    expected = [rows[1], rows[2], rows[0]]
    assert result.checkouts == [
        PendingCheckout(
            checkout_id=checkout_id,
            status=status,
            order_number=order_number,
            created_at=_utc(created),
            alerted_at=_utc(alerted),
        )
        for checkout_id, status, order_number, created, alerted in expected
    ]
    assert result.refunds == []


@pytest.mark.parametrize(
    ("order_status", "checkout_status", "alerted"),
    [
        (STATUS_AWAITING_PAYMENT, CHECKOUT_OPEN, False),
        (STATUS_AWAITING_PAYMENT, CHECKOUT_EXPIRED, True),
        (STATUS_CANCELLED, CHECKOUT_OPEN, True),
        (STATUS_PAID, CHECKOUT_COMPLETE, True),
    ],
    ids=["not_alerted", "expired", "order_cancelled", "order_paid"],
)
def test_pending_checkout_exit_conditions(
    engine: Engine, order_status: str, checkout_status: str, alerted: bool
) -> None:
    """第 11 条第（1）项「已写告警时间」才列出，「第（1）项在记录已为 expired 或订单已不是
    待支付时不再列出」：告警时间为空、记录已 expired、订单已取消或已付款时都不列出。"""
    order_id, _ = _make_order(engine, order_status)
    alerted_at = _base() + timedelta(hours=25) if alerted else None
    _checkout(engine, order_id, checkout_status, alerted_at)

    result = _pending(engine)

    assert result.checkouts == []
    assert result.refunds == []


# ---------------------------------------------------------------------------
# 第（2）项：失败的迟到付款退款
# ---------------------------------------------------------------------------


def test_failed_late_refunds_rows_and_order(engine: Engine) -> None:
    """第 11 条第（2）项「来源为会话（第 6 条）、该来源最新一次尝试为 failed 的
    `StripeRefund`。每行显示结账记录 ID、最新尝试 ID、订单号、金额、失败时间与错误类别」：
    每个来源只列最新（ID 最大）的那条 failed 尝试，较早的 failed 不另列；失败时间按
    HANDOFF 0.90 给该尝试的创建时间（attempted_at）；错误类别为空照给空。验收：按该尝试的
    ID 从小到大（与结账记录 ID 的顺序无关）。"""
    base = _base()
    first_id, first_number = _late_checkout(engine)
    second_id, second_number = _late_checkout(engine)
    _attempt(engine, first_id, STRIPE_REFUND_FAILED, base, "invalid_request_error")
    second_attempt = _attempt(engine, second_id, STRIPE_REFUND_FAILED, base + timedelta(hours=1))
    first_attempt = _attempt(
        engine, first_id, STRIPE_REFUND_FAILED, base + timedelta(hours=2), "card_error"
    )

    result = _pending(engine)

    assert result.refunds == [
        FailedLateRefund(
            checkout_id=second_id,
            attempt_id=second_attempt,
            order_number=second_number,
            amount_sen=TOTAL,
            attempted_at=_utc(base + timedelta(hours=1)),
            error_type=None,
        ),
        FailedLateRefund(
            checkout_id=first_id,
            attempt_id=first_attempt,
            order_number=first_number,
            amount_sen=TOTAL,
            attempted_at=_utc(base + timedelta(hours=2)),
            error_type="card_error",
        ),
    ]
    assert result.checkouts == []


@pytest.mark.parametrize("status", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_failed_late_refund_exits_when_latest_not_failed(engine: Engine, status: str) -> None:
    """第 11 条第（2）项「只看来源与最新一次尝试，最新一次已不是 failed 时不再列出」：
    重试建成的新尝试为 pending 或 succeeded 时，该来源不再列出，较早的 failed 不算。"""
    base = _base()
    checkout_id, _ = _late_checkout(engine)
    _attempt(engine, checkout_id, STRIPE_REFUND_FAILED, base, "invalid_request_error")
    _attempt(engine, checkout_id, status, base + timedelta(hours=1))

    assert _pending(engine).refunds == []


@pytest.mark.parametrize(
    ("order_status", "checkout_status"),
    [
        (STATUS_CANCELLED, CHECKOUT_EXPIRED),
        (STATUS_PAID, CHECKOUT_EXPIRED),
        (STATUS_AWAITING_PAYMENT, CHECKOUT_EXPIRED),
        (STATUS_AWAITING_PAYMENT, CHECKOUT_COMPLETE),
    ],
    ids=["cancelled_expired", "paid_expired", "awaiting_expired", "awaiting_complete"],
)
def test_failed_late_refund_listed_regardless_of_order_and_checkout(
    engine: Engine, order_status: str, checkout_status: str
) -> None:
    """第 11 条第（2）项「不论订单状态与结账记录是否已为 expired（第 6 条写成退款尝试后，
    记录本来就会记为 expired，订单也可能早已取消）」：订单已取消、已由另一途径付款或仍待
    支付，记录已 expired 或尚未 expired，最新一次为 failed 时都列出。"""
    order_id, order_number = _make_order(engine, order_status)
    checkout_id = _checkout(engine, order_id, checkout_status, None)
    attempt_id = _attempt(engine, checkout_id, STRIPE_REFUND_FAILED, _base())

    result = _pending(engine)

    assert [(row.checkout_id, row.attempt_id, row.order_number) for row in result.refunds] == [
        (checkout_id, attempt_id, order_number)
    ]
    assert result.checkouts == []


# ---------------------------------------------------------------------------
# 资料保护与只读
# ---------------------------------------------------------------------------


def test_lists_exclude_recipient_and_stripe_ids(engine: Engine) -> None:
    """第 11 条「只显示对账字段（本站记录 ID、状态、金额、时间、错误类别）与订单号，不显示
    收货资料、Stripe 的任何 ID 与 Stripe 返回的客户资料（第 10 条）」；验收：也不给付款页
    地址与创建参数快照。两类各有一行时，整份结果里没有收货资料、会话 / PaymentIntent /
    退款 ID、付款页地址、创建幂等键与快照。"""
    order_id, _ = _make_order(engine)
    _checkout(engine, order_id, CHECKOUT_OPEN, _base() + timedelta(hours=25))
    checkout_id, _ = _late_checkout(engine)
    _attempt(engine, checkout_id, STRIPE_REFUND_FAILED, _base(), "invalid_request_error")

    result = _pending(engine)

    assert len(result.checkouts) == 1 and len(result.refunds) == 1
    dumped = result.model_dump_json()
    for secret in (
        RECIPIENT_NAME,
        RECIPIENT_PHONE,
        RECIPIENT_ADDRESS,
        "cs_test_",
        "pi_test_",
        "re_test_",
        "checkout.stripe.test",
        "create-key-",
        "customer_email",
        "demo@example.test",
    ):
        assert secret not in dumped


def test_stripe_pending_is_read_only_and_ends_read_transaction(engine: Engine) -> None:
    """第 11 条「由管理员在后台『Stripe 待处理』页处置。只有下面两类」：列表只把两类读给
    页面，处置另有操作；验收：查询只读（只发 SELECT）、结束读事务。"""
    order_id, _ = _make_order(engine)
    _checkout(engine, order_id, CHECKOUT_CREATING, _base() + timedelta(hours=25))
    checkout_id, _ = _late_checkout(engine)
    _attempt(engine, checkout_id, STRIPE_REFUND_FAILED, _base())
    statements: list[object] = []

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        statements.append(clauseelement)

    event.listen(engine, "before_execute", on_execute)
    try:
        with Session(engine) as db:
            result = stripe_pending(db)
            assert not db.in_transaction()
    finally:
        event.remove(engine, "before_execute", on_execute)

    assert len(result.checkouts) == 1 and len(result.refunds) == 1
    assert statements and all(isinstance(stmt, Select) for stmt in statements)

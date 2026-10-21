"""Stripe webhook 退款事件的同事务处理（app/services/stripe_webhook.py 的 handle_webhook_event）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 7、8 条与 docs/HANDOFF.md 0.55
的落实写法。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句；只出自
SHOP-TASK-128 验收标准的约定注明「验收：」。Checkout 事件见 tests/test_stripe_webhook.py，写入
规则本身见 tests/test_stripe_refund.py，这里不重复。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表；订单、会话记录与
退款尝试直接按模型写入（写法照 tests/test_stripe_refund.py，在本文件里另写），尝试的来源一律用
已付款的 expired 会话记录。Stripe 一律用 FakeStripeGateway（第 9 条：CI 与本地测试不调用
Stripe）。被测函数会提交与回滚，测试数据都先提交。SQLite 不支持行锁，加锁只按代码与 MySQL
行为审阅。
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
from app.models import Order, StripeCheckout, StripeEvent, StripeRefund
from app.models.order import STATUS_CANCELLED
from app.models.stripe import (
    CHECKOUT_EXPIRED,
    EVENT_IGNORED,
    EVENT_PROCESSED,
    PAYMENT_PAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import stripe_webhook
from app.services.stripe_gateway import (
    FakeStripeGateway,
    Refund,
    StripeError,
    StripeOutcome,
    StripeResult,
    WebhookEvent,
)
from app.services.stripe_refund import LOG_REFUND_FAILED_AFTER_SUCCESS, LOG_REFUND_MISMATCH
from app.services.stripe_webhook import (
    LOG_WEBHOOK_REFUND_REJECTED,
    WebhookResult,
    handle_webhook_event,
)

# 尝试在 CREATED 创建；webhook 在 NOW 送达。
CREATED = datetime(2026, 10, 19, 2, 0)
NOW = CREATED + timedelta(hours=1)
EARLIER = CREATED + timedelta(minutes=5)

TOTAL = 6800
AMOUNT = 2500
PI = "pi_test_paid"
OTHER_PI = "pi_test_other"
REFUND_ID = "re_test_1"

CREATED_EVENT = "refund.created"
UPDATED_EVENT = "refund.updated"
FAILED_EVENT = "refund.failed"

UNAVAILABLE: StripeResult[Any] = StripeResult(StripeOutcome.UNAVAILABLE)
QUERY_REJECTED: StripeResult[Any] = StripeResult(
    StripeOutcome.REJECTED,
    error=StripeError(404, type="invalid_request_error", code="resource_missing"),
)

FINGERPRINT = "0f" * 32

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


def _checkout(db: Session) -> int:
    """已取消的游客订单与它已付款的 expired 会话记录（迟到付款）；提交后返回记录 ID。"""
    n = next(_SEQUENCE)
    order = Order(
        order_number=f"{n:016d}",
        status=STATUS_CANCELLED,
        subtotal_sen=6000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=800,
        total_sen=TOTAL,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-key-{n}",
        request_fingerprint=FINGERPRINT,
        created_at=CREATED,
        payment_expires_at=CREATED + timedelta(minutes=15),
        paid_at=None,
        member_id=None,
    )
    db.add(order)
    db.flush()
    record = StripeCheckout(
        order_id=order.id,
        idempotency_key=f"create-key-{n}",
        checkout_session_id=f"cs_test_{n}",
        amount_sen=TOTAL,
        currency="myr",
        status=CHECKOUT_EXPIRED,
        payment_status=PAYMENT_PAID,
        create_params="[]",
        expires_at=CREATED + timedelta(minutes=31),
        created_at=CREATED,
        completed_at=CREATED,
        active_order_id=None,
    )
    db.add(record)
    db.commit()
    return record.id


def _attempt(
    db: Session, status: str = STRIPE_REFUND_PENDING, *, stripe_refund_id: str | None = None
) -> int:
    """直接写一条合法的退款尝试（来源为会话），提交后返回尝试 ID。"""
    checkout_id = _checkout(db)
    attempt = StripeRefund(
        payment_intent_id=PI,
        source_type=SOURCE_CHECKOUT,
        checkout_id=checkout_id,
        stripe_refund_id=stripe_refund_id,
        amount_sen=AMOUNT,
        status=status,
        call_count=0,
        created_at=CREATED,
        succeeded_at=EARLIER if status == STRIPE_REFUND_SUCCEEDED else None,
        active_checkout_id=None if status == STRIPE_REFUND_FAILED else checkout_id,
    )
    db.add(attempt)
    db.commit()
    return attempt.id


def _state(db: Session, attempt_id: int) -> StripeRefund:
    db.rollback()
    stmt = select(StripeRefund).where(StripeRefund.id == attempt_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _snapshot(db: Session, attempt_id: int) -> dict[str, Any]:
    attempt = _state(db, attempt_id)
    snapshot = {name: getattr(attempt, name) for name in StripeRefund.__table__.columns.keys()}
    db.rollback()
    return snapshot


def _refund(
    attempt_id: int,
    status: str | None = "pending",
    *,
    refund_id: str = REFUND_ID,
    payment_intent: str | None = PI,
    attempt_ref: str | None = None,
) -> Refund:
    """与尝试相符的退款对象（元数据里的尝试 ID 等于本尝试）；参数改其中的字段。"""
    return Refund(
        id=refund_id,
        status=status,
        amount=AMOUNT,
        payment_intent=payment_intent,
        attempt_id=str(attempt_id) if attempt_ref is None else attempt_ref,
    )


def _ok(refund: Refund) -> StripeResult[Refund]:
    return StripeResult(StripeOutcome.OK, refund)


def _webhook(event_type: str, refund: Refund, *, event_id: str | None = None) -> WebhookEvent:
    """签名校验通过、已解析的测试环境退款事件；事件对象是给出的退款快照（只作触发）。"""
    return WebhookEvent(
        id=event_id or f"evt_test_{next(_SEQUENCE)}",
        type=event_type,
        livemode=False,
        refund=refund,
    )


def _handle(db: Session, gateway: Any, webhook: WebhookEvent) -> WebhookResult:
    return handle_webhook_event(db, gateway, webhook, NOW)


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _stripe_events(db: Session) -> list[tuple[str, str, str, datetime]]:
    db.rollback()
    columns = (StripeEvent.event_id, StripeEvent.event_type, StripeEvent.outcome)
    stmt = select(*columns, StripeEvent.processed_at).order_by(StripeEvent.id)
    rows = [tuple(row) for row in db.execute(stmt)]
    db.rollback()
    return rows


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    """本站服务记下的 error 级日志（告警邮件接入前的告警）。"""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.startswith("app.services.")
    ]


# ---------------------------------------------------------------------------
# 先查询再写入
# ---------------------------------------------------------------------------


def test_updated_event_writes_the_queried_succeeded_refund(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「Stripe 退款 ID、调用结果与之后的退款事件……更新记录状态」「退款事件只作触发，
    收到后先查询再更新」与第 7 条「事件 ID 的去重记录与它引起的……退款改动在同一个数据库
    事务里提交」：refund.updated 而查询到 succeeded 时 pending 改为 succeeded，事件记录为
    processed。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))
    webhook = _webhook(UPDATED_EVENT, _refund(attempt_id, "pending"), event_id="evt_test_updated")

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    attempt = _state(db, attempt_id)
    assert (attempt.status, attempt.succeeded_at, attempt.last_checked_at) == (
        STRIPE_REFUND_SUCCEEDED,
        NOW,
        NOW,
    )
    assert _stripe_events(db) == [("evt_test_updated", UPDATED_EVENT, EVENT_PROCESSED, NOW)]
    assert _alerts(caplog) == []


def test_failed_event_follows_the_queried_refund(db: Session) -> None:
    """第 8 条「退款事件只作触发，收到后先查询再更新，不按事件里的快照改，所以先后颠倒送达的
    事件不会让状态倒退」：refund.failed（快照为 failed）而查询到 succeeded 时按查询结果改为
    succeeded，不改为 failed。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))
    webhook = _webhook(FAILED_EVENT, _refund(attempt_id, "failed"))

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.active_checkout_id == attempt.checkout_id
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


def test_stale_pending_event_does_not_regress_succeeded(db: Session) -> None:
    """第 8 条「不按事件里的快照改，所以先后颠倒送达的事件不会让状态倒退」与「只接受
    pending → succeeded、pending → failed、succeeded → failed 三种改动」：已 succeeded 的
    尝试收到晚到的 refund.created（快照 pending）、查询到 pending 时仍为 succeeded。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "pending")))
    webhook = _webhook(CREATED_EVENT, _refund(attempt_id, "pending"))

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    attempt = _state(db, attempt_id)
    assert (attempt.status, attempt.succeeded_at) == (STRIPE_REFUND_SUCCEEDED, EARLIER)


def test_failed_after_success_is_written_and_alerted_once(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「Stripe 允许已成功的退款之后变为失败，这时照查询结果改为 failed、释放额度」
    「发现变为 failed 就照上面改并告警」：已 succeeded 的尝试查询到 failed 时改为 failed，提交
    之后记一次 stripe_refund_failed_after_success（只含尝试 ID）。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "failed")))
    webhook = _webhook(FAILED_EVENT, _refund(attempt_id, "failed"))

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.active_checkout_id is None
    assert _alerts(caplog) == [f"{LOG_REFUND_FAILED_AFTER_SUCCESS} attempt={attempt_id}"]
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


def test_attempt_without_refund_id_is_found_by_metadata(db: Session) -> None:
    """第 8 条「之后的退款事件（按 Stripe 退款 ID 或元数据里的记录 ID 关联）更新记录状态」：
    还没有 Stripe 退款 ID 的尝试按元数据里的尝试 ID 找到，按查询结果写入退款 ID。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "pending")))
    webhook = _webhook(CREATED_EVENT, _refund(attempt_id, "pending"))

    assert _handle(db, gateway, webhook) is WebhookResult.PROCESSED

    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    attempt = _state(db, attempt_id)
    assert (attempt.stripe_refund_id, attempt.status, attempt.last_checked_at) == (
        REFUND_ID,
        STRIPE_REFUND_PENDING,
        NOW,
    )


def test_queried_payment_intent_mismatch_alerts_without_writing(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「退款接口的响应、webhook 与定时查询共用同一写入规则」与 HANDOFF 0.54 告警时间列
    「只给会反复出现的告警（Stripe 退款对象与尝试不符等）去重」：查询到的 PaymentIntent 不符时
    不改状态与退款 ID，写告警时间并在提交之后记 stripe_refund_mismatch。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)
    wrong = _refund(attempt_id, "succeeded", payment_intent=OTHER_PI)
    gateway = FakeStripeGateway(_ok(wrong))

    assert _handle(db, gateway, _webhook(UPDATED_EVENT, wrong)) is WebhookResult.PROCESSED

    after = _snapshot(db, attempt_id)
    assert after.pop("alerted_at") == NOW
    before.pop("alerted_at")
    assert after == before
    assert _alerts(caplog) == [f"{LOG_REFUND_MISMATCH} attempt={attempt_id}"]
    assert [row[2] for row in _stripe_events(db)] == [EVENT_PROCESSED]


# ---------------------------------------------------------------------------
# 不处理与无法完成
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attempt_ref", [None, "", "999999", "x1", "-1", "١", "9" * 19])
def test_refund_without_a_site_attempt_is_ignored(db: Session, attempt_ref: str | None) -> None:
    """HANDOFF 0.55「找不到本站记录的事件记为 ignored、回 200」与第 8 条「按 Stripe 退款 ID 或
    元数据里的记录 ID 关联」：退款 ID 对不上、元数据的尝试 ID 缺失、不是十进制数字或不存在时
    （含不是本站发起的退款）为 ignored，不调用 Stripe，尝试不变，只写事件记录。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)
    stranger = Refund(
        id="re_test_stranger", status="succeeded", payment_intent=PI, attempt_id=attempt_ref
    )
    gateway = FakeStripeGateway(_ok(stranger))
    webhook = _webhook(UPDATED_EVENT, stranger, event_id="evt_test_stranger")

    assert _handle(db, gateway, webhook) is WebhookResult.IGNORED

    assert gateway.calls == []
    assert _snapshot(db, attempt_id) == before
    assert _stripe_events(db) == [("evt_test_stranger", UPDATED_EVENT, EVENT_IGNORED, NOW)]


def test_stripe_unavailable_leaves_no_event_record(db: Session) -> None:
    """第 7 条「处理失败整个事务回滚、回 5xx，由 Stripe 重发」与 HANDOFF 0.55「处理中 Stripe
    不可用……不留事件记录、回 503」：查询不可用为 unavailable，没有事件记录、尝试不变。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)
    gateway = FakeStripeGateway(UNAVAILABLE)

    result = _handle(db, gateway, _webhook(UPDATED_EVENT, _refund(attempt_id, "succeeded")))

    assert result is WebhookResult.UNAVAILABLE
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    assert _snapshot(db, attempt_id) == before
    assert _stripe_events(db) == []


def test_rejected_query_is_unavailable_and_alerts(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 7 条「处理失败整个事务回滚、回 5xx，由 Stripe 重发」与 HANDOFF 0.55「已找到本站记录、
    但向 Stripe……查询被拒……整个事务回滚、不留事件记录、回 503」：为 unavailable，没有事件
    记录、尝试不变，记一次只含尝试 ID 的 stripe_webhook_refund_rejected。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)
    gateway = FakeStripeGateway(QUERY_REJECTED)

    result = _handle(db, gateway, _webhook(UPDATED_EVENT, _refund(attempt_id, "succeeded")))

    assert result is WebhookResult.UNAVAILABLE
    assert _snapshot(db, attempt_id) == before
    assert _stripe_events(db) == []
    assert _alerts(caplog) == [f"{LOG_WEBHOOK_REFUND_REJECTED} attempt={attempt_id}"]


# ---------------------------------------------------------------------------
# 去重与同一事务
# ---------------------------------------------------------------------------


def test_same_event_again_is_duplicate_without_calling_stripe(db: Session) -> None:
    """第 7 条「已提交过的事件直接回 200」：同一退款事件再来为 duplicate，不调用 Stripe、尝试与
    事件记录不变。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    webhook = _webhook(UPDATED_EVENT, _refund(attempt_id, "succeeded"))
    first = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))
    assert _handle(db, first, webhook) is WebhookResult.PROCESSED
    before = (_snapshot(db, attempt_id), _stripe_events(db))
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "failed")))

    assert _handle(db, gateway, webhook) is WebhookResult.DUPLICATE

    assert gateway.calls == []
    assert (_snapshot(db, attempt_id), _stripe_events(db)) == before


def test_concurrent_same_event_is_duplicate_and_rolls_back_the_change(
    db: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 7 条「事件 ID 的去重记录与它引起的订单、积分或退款改动在同一个数据库事务里提交」：
    普通读没查到而写事件记录时撞上同一事件 ID，返回 duplicate，退款改动随之回滚、尝试未改，
    不记告警（本会成功后又失败而告警）。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)
    db.add(
        StripeEvent(
            event_id="evt_test_race",
            event_type=FAILED_EVENT,
            outcome=EVENT_PROCESSED,
            processed_at=EARLIER,
        )
    )
    db.commit()
    before = _snapshot(db, attempt_id)
    monkeypatch.setattr(stripe_webhook, "_already_recorded", lambda _db, _event_id: False)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "failed")))
    webhook = _webhook(FAILED_EVENT, _refund(attempt_id, "failed"), event_id="evt_test_race")

    assert _handle(db, gateway, webhook) is WebhookResult.DUPLICATE

    assert _snapshot(db, attempt_id) == before
    assert _stripe_events(db) == [("evt_test_race", FAILED_EVENT, EVENT_PROCESSED, EARLIER)]
    assert _alerts(caplog) == []

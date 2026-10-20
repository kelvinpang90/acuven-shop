"""Stripe 退款尝试的定时处理。

被测模块为 app/services/stripe_refund_overdue.py。依据 docs/DESIGN.md 1.12
（提交 acf576e）「Stripe 测试环境支付」第 8 条，以及 docs/HANDOFF.md 0.54
记录的 Kelvin 2026-10-19 决定（每 5 分钟重试、从未调用过的立即处理，有 ID
的 pending 每 10 分钟查询，满 24 小时的每小时对账一次、告警只记一次）。每条
测试的文档字符串都引用它守住的 DESIGN 1.12 或 HANDOFF 0.54 原句；SHOP-TASK-126
验收标准在原句之上细化的约定另注明「验收：」。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型
建表，订单、会话记录与退款尝试直接按模型写入；尝试的来源一律用已付款的
expired 会话记录（第 6 条的迟到付款），定时处理不看来源类别。Stripe 一律用
FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会提交与
回滚，测试数据都先提交。SQLite 不支持行锁；「调用 Stripe 时不持有任何数据库
锁」以调用 Stripe 时会话不在事务中检查。边界取验收标准的字面：「早于 5 分钟
前」等为严格早于，所以测试取边界两侧各差 1 秒的时刻。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Order, StripeCheckout, StripeRefund
from app.models.order import STATUS_CANCELLED
from app.models.stripe import (
    CHECKOUT_EXPIRED,
    PAYMENT_PAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services.stripe_gateway import (
    FakeStripeGateway,
    Form,
    Refund,
    RefundPage,
    StripeOutcome,
    StripeResult,
)
from app.services.stripe_refund import LOG_REFUND_FAILED_AFTER_SUCCESS
from app.services.stripe_refund_overdue import (
    LOG_REFUND_UNRESOLVED,
    has_due_refunds,
    process_stripe_refunds,
)

# 尝试在 CREATED 创建；NOW 时第一次处理。
CREATED = datetime(2026, 10, 19, 2, 0)
NOW = CREATED + timedelta(minutes=1)
SECOND = timedelta(seconds=1)
DAY = timedelta(days=1)
WINDOW = timedelta(hours=24)

TOTAL = 6800
AMOUNT = 2500
PI = "pi_test_paid"
REFUND_ID = "re_test_1"
LIMIT = 100

UNAVAILABLE: StripeResult[Any] = StripeResult(StripeOutcome.UNAVAILABLE)

FINGERPRINT = "0f" * 32
LOGGERS = ("app.services.stripe_refund_overdue", "app.services.stripe_refund")

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


@pytest.fixture
def alerts(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    for name in LOGGERS:
        caplog.set_level(logging.INFO, logger=name)
    return caplog


def _checkout(db: Session) -> int:
    """已取消的游客订单与它已付款的 expired 会话记录（迟到付款）；
    提交后返回记录 ID。"""
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
    db.flush()
    checkout_id = record.id
    db.commit()
    return checkout_id


def _attempt(
    db: Session,
    status: str = STRIPE_REFUND_PENDING,
    *,
    stripe_refund_id: str | None = None,
    created_at: datetime = CREATED,
    call_count: int = 0,
    last_checked_at: datetime | None = None,
    succeeded_at: datetime | None = None,
    alerted_at: datetime | None = None,
) -> int:
    """直接写一条合法的退款尝试（来源为会话），提交后返回尝试 ID。"""
    checkout_id = _checkout(db)
    if status == STRIPE_REFUND_SUCCEEDED and succeeded_at is None:
        succeeded_at = created_at
    attempt = StripeRefund(
        payment_intent_id=PI,
        source_type=SOURCE_CHECKOUT,
        checkout_id=checkout_id,
        stripe_refund_id=stripe_refund_id,
        amount_sen=AMOUNT,
        status=status,
        call_count=call_count,
        created_at=created_at,
        succeeded_at=succeeded_at,
        alerted_at=alerted_at,
        last_checked_at=last_checked_at,
        active_checkout_id=None if status == STRIPE_REFUND_FAILED else checkout_id,
    )
    db.add(attempt)
    db.flush()
    attempt_id = attempt.id
    db.commit()
    return attempt_id


def _state(db: Session, attempt_id: int) -> StripeRefund:
    db.rollback()
    stmt = select(StripeRefund).where(StripeRefund.id == attempt_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def _refund(attempt_id: int, status: str = "pending", *, refund_id: str = REFUND_ID) -> Refund:
    """元数据里的尝试 ID 等于 attempt_id 的退款对象。"""
    return Refund(
        id=refund_id,
        status=status,
        amount=AMOUNT,
        payment_intent=PI,
        attempt_id=str(attempt_id),
    )


def _other(refund_id: str) -> Refund:
    """同一 PaymentIntent 上别的尝试的退款。"""
    return Refund(id=refund_id, status="succeeded", amount=100, payment_intent=PI, attempt_id="0")


def _ok(value: Any) -> StripeResult[Any]:
    return StripeResult(StripeOutcome.OK, value)


def _page(*refunds: Refund, has_more: bool | None = False) -> StripeResult[RefundPage]:
    return _ok(RefundPage(tuple(refunds), has_more))


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name in LOGGERS and record.levelno == logging.ERROR
    ]


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


def _run(db: Session, gateway: FakeStripeGateway, now: datetime, limit: int = LIMIT) -> int:
    return process_stripe_refunds(db, gateway, now, limit)


# ---------------------------------------------------------------------------
# 还没有 Stripe 退款 ID、创建未满 24 小时：以同一幂等键重试
# ---------------------------------------------------------------------------


def test_never_called_attempt_is_submitted_next_round(db: Session) -> None:
    """HANDOFF 0.54：「在那之前及调用结果未知时，由定时任务对从未调用过的
    尝试在下一轮发起」；DESIGN 1.12 第 8 条：「以该记录的本站 ID 为幂等键，
    并写进退款元数据」。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id)))

    assert _run(db, gateway, NOW) == 1

    assert _paths(gateway) == [("POST", "/v1/refunds")]
    assert gateway.calls[0].idempotency_key == str(attempt_id)
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.call_count == 1
    assert attempt.last_checked_at == NOW


def test_never_called_attempt_ignores_recent_check(db: Session) -> None:
    """HANDOFF 0.54：「每 5 分钟以同一幂等键重试一次（从未调用过的立即
    处理）」；验收：调用次数为 0 的不看上次复查时间。"""
    attempt_id = _attempt(db, last_checked_at=NOW - SECOND)
    gateway = FakeStripeGateway(UNAVAILABLE)

    assert _run(db, gateway, NOW) == 1

    assert _paths(gateway) == [("POST", "/v1/refunds")]
    assert _state(db, attempt_id).call_count == 1


def test_retry_every_five_minutes_with_same_key(db: Session) -> None:
    """HANDOFF 0.54：「还没有 Stripe 退款 ID 的尝试在创建后 24 小时内每 5
    分钟以同一幂等键重试一次」；DESIGN 1.12 第 8 条：「同一幂等键的并发请求
    不会在 Stripe 产生两笔退款」；验收：Stripe 不可用时这一条写上次复查时间、
    留到下一轮。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(UNAVAILABLE, UNAVAILABLE)

    assert _run(db, gateway, NOW) == 1
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.last_checked_at == NOW

    within = NOW + timedelta(minutes=4, seconds=59)
    assert _run(db, gateway, within) == 0
    assert len(gateway.calls) == 1

    after = NOW + timedelta(minutes=5) + SECOND
    assert _run(db, gateway, after) == 1
    assert [call.idempotency_key for call in gateway.calls] == [str(attempt_id)] * 2
    assert gateway.calls[0].form == gateway.calls[1].form
    attempt = _state(db, attempt_id)
    assert attempt.call_count == 2
    assert attempt.last_checked_at == after


def test_no_create_refund_after_24_hours(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「由定时任务在该尝试创建后 24 小时内（Stripe
    保留幂等键的最短期限）以同一幂等键自动重试……24 小时后不再调用退款
    接口，改为列出该 PaymentIntent 退款的全部分页」。"""
    attempt_id = _attempt(db, call_count=3, last_checked_at=CREATED + timedelta(hours=23))
    gateway = FakeStripeGateway(UNAVAILABLE, _page())

    last_call = CREATED + WINDOW - SECOND
    assert _run(db, gateway, last_call) == 1
    assert _paths(gateway) == [("POST", "/v1/refunds")]

    reconcile = CREATED + WINDOW + timedelta(hours=1)
    assert _run(db, gateway, reconcile) == 1
    assert _paths(gateway)[1:] == [("GET", "/v1/refunds")]
    assert gateway.calls[1].form == (("payment_intent", PI), ("limit", "100"))
    attempt = _state(db, attempt_id)
    assert attempt.call_count == 4
    assert attempt.status == STRIPE_REFUND_PENDING


# ---------------------------------------------------------------------------
# 还没有 Stripe 退款 ID、创建满 24 小时：分页对账
# ---------------------------------------------------------------------------


def _overdue(db: Session, **kwargs: Any) -> int:
    """创建满 24 小时、还没有 Stripe 退款 ID 的尝试（调用过一次）。"""
    return _attempt(db, created_at=NOW - WINDOW - DAY, call_count=1, **kwargs)


def test_reconcile_adopts_refund_found_by_metadata(
    db: Session, alerts: pytest.LogCaptureFixture
) -> None:
    """DESIGN 1.12 第 8 条：「改为列出该 PaymentIntent 退款的全部分页、按
    元数据里的尝试 ID 对账，找到就采用」；「退款接口的响应、webhook 与定时
    查询共用同一写入规则」。"""
    attempt_id = _overdue(db)
    gateway = FakeStripeGateway(_page(_other("re_test_a"), _refund(attempt_id, "succeeded")))

    assert _run(db, gateway, NOW) == 1

    assert _paths(gateway) == [("GET", "/v1/refunds")]
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.succeeded_at == NOW
    assert attempt.call_count == 1
    assert attempt.alerted_at is None
    assert _alerts(alerts) == []


def test_reconcile_adopts_refund_on_second_page(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「列出该 PaymentIntent 退款的全部分页、按元数据
    里的尝试 ID 对账，找到就采用」：第二页才找到也采用，第二页从第一页最后
    一笔之后接着列。"""
    attempt_id = _overdue(db)
    gateway = FakeStripeGateway(
        _page(_other("re_test_a"), _other("re_test_b"), has_more=True),
        _page(_refund(attempt_id, "pending")),
    )

    assert _run(db, gateway, NOW) == 1

    assert _paths(gateway) == [("GET", "/v1/refunds")] * 2
    assert gateway.calls[1].form == (
        ("payment_intent", PI),
        ("limit", "100"),
        ("starting_after", "re_test_b"),
    )
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.status == STRIPE_REFUND_PENDING


def test_reconcile_not_found_stays_pending_and_alerts_once(
    db: Session, alerts: pytest.LogCaptureFixture
) -> None:
    """DESIGN 1.12 第 8 条：「找不到或无法列完都保持 pending、占用额度，告警
    由运营者在 Stripe 后台跟进；本站不提供把这类尝试改为 failed 的手段」；
    HANDOFF 0.54：「满 24 小时转对账的每小时列一次全部分页、直到找到（告警
    只记一次）」。"""
    attempt_id = _overdue(db)
    gateway = FakeStripeGateway(_page(_other("re_test_a")), _page(_other("re_test_a")))

    assert _run(db, gateway, NOW) == 1
    later = NOW + timedelta(hours=1) + SECOND
    assert _run(db, gateway, later) == 1

    assert _paths(gateway) == [("GET", "/v1/refunds")] * 2
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.stripe_refund_id is None
    assert attempt.active_checkout_id == attempt.checkout_id
    assert attempt.alerted_at == NOW
    assert attempt.last_checked_at == later
    assert _alerts(alerts) == [f"{LOG_REFUND_UNRESOLVED} attempt={attempt_id}"]


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param((UNAVAILABLE,), id="first-page-unavailable"),
        pytest.param(
            (_page(_other("re_test_a"), has_more=True), UNAVAILABLE),
            id="second-page-unavailable",
        ),
        pytest.param((_page(has_more=None),), id="has-more-missing"),
        pytest.param((_page(has_more=True),), id="empty-page-with-more"),
    ],
)
def test_reconcile_incomplete_listing_stays_pending_and_alerts_once(
    db: Session, alerts: pytest.LogCaptureFixture, pages: tuple[StripeResult[Any], ...]
) -> None:
    """DESIGN 1.12 第 8 条：「找不到或无法列完都保持 pending、占用额度，告警
    由运营者在 Stripe 后台跟进」；HANDOFF 0.54：告警时间列只给「24 小时后对账
    找不到或列不完」等会反复出现的告警去重，这类告警每条尝试至多一次。"""
    attempt_id = _overdue(db)
    gateway = FakeStripeGateway(*pages)

    assert _run(db, gateway, NOW) == 1
    assert _run(db, gateway, NOW + timedelta(hours=2)) == 1

    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.stripe_refund_id is None
    assert attempt.alerted_at == NOW
    assert ("POST", "/v1/refunds") not in _paths(gateway)
    assert _alerts(alerts) == [f"{LOG_REFUND_UNRESOLVED} attempt={attempt_id}"]


def test_reconcile_skips_alert_when_already_alerted(
    db: Session, alerts: pytest.LogCaptureFixture
) -> None:
    """HANDOFF 0.54：告警时间列「只给会反复出现的告警（24 小时后对账找不到或
    列不完、Stripe 退款对象与尝试不符）去重，这类告警每条尝试至多一次」：
    已因不符告警过的尝试对账找不到时不再告警。"""
    earlier = NOW - DAY
    attempt_id = _overdue(db, alerted_at=earlier)
    gateway = FakeStripeGateway(_page())

    assert _run(db, gateway, NOW) == 1

    assert _state(db, attempt_id).alerted_at == earlier
    assert _alerts(alerts) == []


def test_reconcile_not_repeated_within_an_hour(db: Session) -> None:
    """HANDOFF 0.54：「满 24 小时转对账的每小时列一次全部分页」：上次复查
    不满 1 小时不再列，之后再列。"""
    attempt_id = _overdue(db)
    gateway = FakeStripeGateway(_page(), _page())

    assert _run(db, gateway, NOW) == 1
    assert _run(db, gateway, NOW + timedelta(minutes=59)) == 0
    assert len(gateway.calls) == 1

    assert _run(db, gateway, NOW + timedelta(hours=1) + SECOND) == 1
    assert len(gateway.calls) == 2
    assert _state(db, attempt_id).status == STRIPE_REFUND_PENDING


# ---------------------------------------------------------------------------
# 有 Stripe 退款 ID：pending 每 10 分钟、succeeded 每天或每周查询
# ---------------------------------------------------------------------------


def test_pending_with_id_is_queried_every_ten_minutes(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「已有 Stripe 退款 ID 而状态未终结的，等退款事件
    或由定时任务查询更新」；HANDOFF 0.54：「有 ID 而仍为 pending 的每 10 分钟
    查询一次」。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID, call_count=1)
    gateway = FakeStripeGateway(UNAVAILABLE, _ok(_refund(attempt_id, "succeeded")))

    assert _run(db, gateway, NOW) == 1
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    assert _state(db, attempt_id).last_checked_at == NOW

    assert _run(db, gateway, NOW + timedelta(minutes=9, seconds=59)) == 0
    assert len(gateway.calls) == 1

    after = NOW + timedelta(minutes=10) + SECOND
    assert _run(db, gateway, after) == 1
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.succeeded_at == after
    # 查询不调用退款接口。
    assert attempt.call_count == 1


def test_succeeded_queried_daily_within_30_days(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「已 succeeded 的尝试，定时任务在成功后 30 天内
    每天……再查询一次」。"""
    succeeded = NOW - timedelta(days=10)
    attempt_id = _attempt(
        db,
        STRIPE_REFUND_SUCCEEDED,
        stripe_refund_id=REFUND_ID,
        succeeded_at=succeeded,
        last_checked_at=NOW,
    )
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))

    assert _run(db, gateway, NOW + DAY - SECOND) == 0
    assert gateway.calls == []

    assert _run(db, gateway, NOW + DAY + SECOND) == 1
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.last_checked_at == NOW + DAY + SECOND


def test_succeeded_queried_weekly_after_30_days(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「已 succeeded 的尝试，定时任务在成功后 30 天内
    每天、之后每周再查询一次，不设终止」。"""
    attempt_id = _attempt(
        db,
        STRIPE_REFUND_SUCCEEDED,
        stripe_refund_id=REFUND_ID,
        succeeded_at=NOW - timedelta(days=400),
        last_checked_at=NOW - timedelta(days=6),
    )
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))

    assert _run(db, gateway, NOW) == 0
    assert gateway.calls == []

    later = NOW + DAY + SECOND
    assert _run(db, gateway, later) == 1
    assert _state(db, attempt_id).last_checked_at == later


def test_succeeded_never_checked_is_queried(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「已 succeeded 的尝试，定时任务……再查询一次」；
    验收：上次复查时间为空的 succeeded 尝试到期。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID, succeeded_at=NOW)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))

    assert _run(db, gateway, NOW) == 1
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]


def test_succeeded_turning_failed_releases_source_and_alerts(
    db: Session, alerts: pytest.LogCaptureFixture
) -> None:
    """DESIGN 1.12 第 8 条：「Stripe 允许已成功的退款之后变为失败，这时照
    查询结果改为 failed、释放额度」「发现变为 failed 就照上面改并告警，防退款
    失败的 webhook 漏收」。"""
    attempt_id = _attempt(
        db,
        STRIPE_REFUND_SUCCEEDED,
        stripe_refund_id=REFUND_ID,
        succeeded_at=NOW - timedelta(days=3),
        last_checked_at=NOW - timedelta(days=2),
    )
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "failed")))

    assert _run(db, gateway, NOW) == 1

    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.active_checkout_id is None
    assert _alerts(alerts) == [f"{LOG_REFUND_FAILED_AFTER_SUCCESS} attempt={attempt_id}"]

    # failed 不再到期。
    assert _run(db, gateway, NOW + timedelta(days=30)) == 0
    assert len(gateway.calls) == 1


def test_failed_attempts_are_never_due(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「后一种不自动重试」「failed → 任何状态……忽略」：
    failed 的尝试不再调用或查询。"""
    _attempt(db, STRIPE_REFUND_FAILED)
    _attempt(db, STRIPE_REFUND_FAILED, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway()

    assert not has_due_refunds(db, NOW + timedelta(days=30))
    assert _run(db, gateway, NOW + timedelta(days=30)) == 0
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# 上限、轮转、处理条数与不持锁
# ---------------------------------------------------------------------------


def test_limit_takes_never_checked_first_then_oldest(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「演示站量小，查询成本可忽略」——每轮仍有上限；
    验收：按上次复查时间从早到晚、从没查过的排最前（同时刻按 ID），最多
    limit 条；返回处理条数。"""
    oldest = _overdue(db, last_checked_at=NOW - timedelta(hours=3))
    newer = _overdue(db, last_checked_at=NOW - timedelta(hours=2))
    fresh = _attempt(db, created_at=NOW)
    gateway = FakeStripeGateway(UNAVAILABLE, _page())

    assert _run(db, gateway, NOW, limit=2) == 2

    assert _paths(gateway) == [("POST", "/v1/refunds"), ("GET", "/v1/refunds")]
    assert gateway.calls[0].idempotency_key == str(fresh)
    assert _state(db, oldest).last_checked_at == NOW
    assert _state(db, newer).last_checked_at == NOW - timedelta(hours=2)


def test_long_unresolved_attempts_do_not_crowd_out_new_ones(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「找不到或无法列完都保持 pending」——这类尝试
    不设终止、每小时都到期；验收：长期不能确定的不挤掉新尝试，处理过的轮到
    队尾。"""
    first = _overdue(db)
    second = _overdue(db)
    gateway = FakeStripeGateway()

    assert _run(db, gateway, NOW, limit=1) == 1
    assert _state(db, first).last_checked_at == NOW

    later = NOW + timedelta(hours=1) + SECOND
    assert _run(db, gateway, later, limit=1) == 1
    assert _state(db, second).last_checked_at == later

    newcomer = _attempt(db, created_at=later)
    latest = later + timedelta(hours=1) + SECOND
    assert _run(db, gateway, latest, limit=1) == 1
    assert gateway.calls[-1].idempotency_key == str(newcomer)
    assert _state(db, newcomer).call_count == 1


def test_processed_count_includes_unavailable(db: Session) -> None:
    """DESIGN 1.12 第 9 条：「创建会话或查询时 Stripe 不可用」不改状态；
    验收：Stripe 不可用时这一条写上次复查时间、留到下一轮；返回处理条数。"""
    retry = _attempt(db)
    query = _attempt(db, stripe_refund_id=REFUND_ID, call_count=1)
    gateway = FakeStripeGateway()

    assert _run(db, gateway, NOW) == 2

    for attempt_id in (retry, query):
        attempt = _state(db, attempt_id)
        assert attempt.status == STRIPE_REFUND_PENDING
        assert attempt.last_checked_at == NOW


def test_has_due_refunds(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「由定时任务在该尝试创建后 24 小时内……自动
    重试」；验收：has_due_refunds 判断是否有到期的尝试。"""
    assert not has_due_refunds(db, NOW)
    _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID, last_checked_at=NOW)
    assert not has_due_refunds(db, NOW)
    _attempt(db)
    assert has_due_refunds(db, NOW)


class _NoTransactionGateway(FakeStripeGateway):
    """每次调用 Stripe 时断言被测会话不在事务中（没有读事务，也就不持有锁）。"""

    def __init__(self, db: Session, *results: StripeResult[Any]) -> None:
        super().__init__(*results)
        self._db = db

    def _record(
        self, method: str, path: str, form: Form = (), idempotency_key: str | None = None
    ) -> StripeResult[Any]:
        assert not self._db.in_transaction()
        return super()._record(method, path, form, idempotency_key)


def test_stripe_is_called_outside_transactions(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「写入时锁住该尝试的行」；验收：调用 Stripe 时
    不持有任何数据库锁，逐条在各自的事务里处理。"""
    _attempt(db)
    overdue = _overdue(db)
    _attempt(db, stripe_refund_id="re_test_q", call_count=1)
    more = _page(_other("re_test_a"), has_more=True)
    gateway = _NoTransactionGateway(db, UNAVAILABLE, more, UNAVAILABLE, UNAVAILABLE)

    assert _run(db, gateway, NOW) == 3
    assert len(gateway.calls) == 4
    assert _state(db, overdue).alerted_at == NOW


def test_aware_now_is_rejected(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「在成功后 30 天内每天、之后每周」「24 小时后」
    都与库里不带时区的 UTC 时间比较；带时区的当前时间抛 ValueError。"""
    _attempt(db)
    aware = NOW.replace(tzinfo=UTC)

    with pytest.raises(ValueError):
        process_stripe_refunds(db, FakeStripeGateway(), aware, LIMIT)
    with pytest.raises(ValueError):
        has_due_refunds(db, aware)

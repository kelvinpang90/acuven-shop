"""Stripe 退款尝试的写入规则与调用 Stripe 退款接口。

被测模块为 app/services/stripe_refund.py。依据 docs/DESIGN.md 1.12
（提交 acf576e）「Stripe 测试环境支付」第 8、9 条，以及 docs/HANDOFF.md
0.54 记录的 Kelvin 2026-10-19 决定（明确拒绝以适配器归为 rejected 为准，
记下错误类别，不自动重试；告警时间列只给会反复出现的告警去重）。每条测试
（参数化的测试是每个用例）的文档字符串都引用它守住的 DESIGN 1.12 或
HANDOFF 0.54 原句；SHOP-TASK-124 验收标准在原句之上细化的约定另注明
「验收：」，不单独作为测试的依据。

用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型
建表，订单、会话记录与退款尝试直接按模型写入；尝试的来源一律用已付款的
expired 会话记录（第 6 条的迟到付款），写入规则不看来源类别。Stripe 一律用
FakeStripeGateway（第 9 条：CI 与本地测试不调用 Stripe）。被测函数会提交与
回滚，测试数据都先提交。SQLite 不支持行锁，锁定语句只以 MySQL 方言编译检查
含 FOR UPDATE，并发下的排队按代码与 MySQL 行为审阅。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.dialects import mysql
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
    StripeError,
    StripeOutcome,
    StripeResult,
)
from app.services.stripe_refund import (
    LOG_REFUND_FAILED_AFTER_SUCCESS,
    LOG_REFUND_MISMATCH,
    LOG_REFUND_REJECTED,
    ApplyResult,
    RefreshResult,
    SubmitResult,
    alert_category,
    apply_refund_object,
    lock_attempt_statement,
    map_refund_status,
    refresh_refund_attempt,
    refunded_total,
    refunded_total_statement,
    submit_refund_attempt,
)

# 尝试在 CREATED 创建；NOW 时第一次处理，LATER 时再处理一次。
CREATED = datetime(2026, 10, 19, 2, 0)
NOW = CREATED + timedelta(hours=1)
LATER = NOW + timedelta(minutes=10)
WINDOW_END = CREATED + timedelta(hours=24)
EARLIER_SUCCESS = CREATED + timedelta(minutes=5)
EARLIER_ALERT = CREATED + timedelta(minutes=30)

TOTAL = 6800
AMOUNT = 2500
PI = "pi_test_paid"
OTHER_PI = "pi_test_other"
REFUND_ID = "re_test_1"

# 适配器的两种「查询不成」：不可用，与被拒（如 404）。
UNAVAILABLE: StripeResult[Any] = StripeResult(StripeOutcome.UNAVAILABLE)
QUERY_REJECTED: StripeResult[Any] = StripeResult(
    StripeOutcome.REJECTED, error=StripeError(404, type="invalid_request_error")
)

FINGERPRINT = "0f" * 32
LOGGER = "app.services.stripe_refund"

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
    caplog.set_level(logging.INFO, logger=LOGGER)
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
    db.commit()
    return record.id


def _attempt(
    db: Session,
    status: str = STRIPE_REFUND_PENDING,
    *,
    stripe_refund_id: str | None = None,
    checkout_id: int | None = None,
    payment_intent_id: str = PI,
    amount: int = AMOUNT,
    created_at: datetime = CREATED,
    alerted_at: datetime | None = None,
) -> int:
    """直接写一条合法的退款尝试（来源为会话），提交后返回尝试 ID。"""
    if checkout_id is None:
        checkout_id = _checkout(db)
    attempt = StripeRefund(
        payment_intent_id=payment_intent_id,
        source_type=SOURCE_CHECKOUT,
        checkout_id=checkout_id,
        stripe_refund_id=stripe_refund_id,
        amount_sen=amount,
        status=status,
        call_count=0,
        created_at=created_at,
        succeeded_at=EARLIER_SUCCESS if status == STRIPE_REFUND_SUCCEEDED else None,
        alerted_at=alerted_at,
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
    return {name: getattr(attempt, name) for name in StripeRefund.__table__.columns.keys()}


def _refund(
    attempt_id: int,
    status: str | None = "pending",
    *,
    refund_id: str = REFUND_ID,
    payment_intent: str | None = PI,
    attempt_ref: str | None = None,
) -> Refund:
    """与尝试相符的退款对象（元数据里的尝试 ID 等于本尝试）；
    参数改其中的字段。"""
    return Refund(
        id=refund_id,
        status=status,
        amount=AMOUNT,
        payment_intent=payment_intent,
        attempt_id=str(attempt_id) if attempt_ref is None else attempt_ref,
    )


def _ok(refund: Refund, *, replayed: bool = False) -> StripeResult[Refund]:
    return StripeResult(StripeOutcome.OK, refund, replayed)


def _rejected(type_: str | None = None, code: str | None = None) -> StripeResult[Refund]:
    return StripeResult(StripeOutcome.REJECTED, error=StripeError(400, type=type_, code=code))


def _alerts(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.ERROR
    ]


def _paths(gateway: FakeStripeGateway) -> list[tuple[str, str]]:
    return [(call.method, call.path) for call in gateway.calls]


# ---------------------------------------------------------------------------
# 状态映射
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stripe_status", "expected"),
    [
        ("pending", STRIPE_REFUND_PENDING),
        ("requires_action", STRIPE_REFUND_PENDING),
        ("succeeded", STRIPE_REFUND_SUCCEEDED),
        ("failed", STRIPE_REFUND_FAILED),
        ("canceled", STRIPE_REFUND_FAILED),
    ],
)
def test_status_mapping(stripe_status: str, expected: str) -> None:
    """DESIGN 1.12 第 8 条：「`pending` 与 `requires_action` 记为
    pending，`succeeded` 记为 succeeded，`failed` 与 `canceled` 记为
    failed」。"""
    assert map_refund_status(stripe_status) == expected


@pytest.mark.parametrize("stripe_status", [None, "", "unknown", "SUCCEEDED", "cancelled"])
def test_unknown_status_does_not_map(stripe_status: str | None) -> None:
    """DESIGN 1.12 第 8 条：「只有 Stripe 明确给出 failed 或 canceled……
    才记为 failed」「其余一律保持 pending」；验收：其他值不改状态。"""
    assert map_refund_status(stripe_status) is None


@pytest.mark.parametrize("start", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_unknown_status_leaves_attempt_unchanged(db: Session, start: str) -> None:
    """DESIGN 1.12 第 8 条：「其余一律保持 pending、占用额度」；
    验收：其他值不改状态。"""
    attempt_id = _attempt(db, start, stripe_refund_id=REFUND_ID)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, "unknown"), NOW)
    db.commit()

    assert result is ApplyResult.UNCHANGED
    attempt = _state(db, attempt_id)
    assert attempt.status == start
    assert attempt.last_checked_at == NOW


# ---------------------------------------------------------------------------
# 写入规则：三种允许的改动与被忽略的改动
# ---------------------------------------------------------------------------


def test_pending_to_succeeded(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「只接受 pending → succeeded……三种改动」；
    验收：改为 succeeded 写成功时间，Stripe 退款 ID 只在为空时写入，
    写上次复查时间。"""
    attempt_id = _attempt(db)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, "succeeded"), NOW)
    db.commit()

    assert result is ApplyResult.CHANGED
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.succeeded_at == NOW
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.last_checked_at == NOW
    assert attempt.active_checkout_id == attempt.checkout_id


@pytest.mark.parametrize("stripe_status", ["failed", "canceled"])
def test_pending_to_failed_releases_source(db: Session, stripe_status: str) -> None:
    """DESIGN 1.12 第 8 条：「只接受……pending → failed……三种改动」
    「只有 Stripe 明确给出 failed 或 canceled……才记为 failed」；
    验收：改为 failed 清空两个活动来源列。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, stripe_status), NOW)
    db.commit()

    assert result is ApplyResult.CHANGED
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.active_checkout_id is None
    assert attempt.active_refund_request_id is None
    assert attempt.succeeded_at is None


def test_succeeded_to_failed_returns_failed_after_success(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「Stripe 允许已成功的退款之后变为失败，这时照
    查询结果改为 failed、释放额度」；验收：succeeded 改为 failed 时须告警
    （类别 stripe_refund_failed_after_success）。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, "failed"), NOW)
    db.commit()

    assert result is ApplyResult.FAILED_AFTER_SUCCESS
    assert alert_category(result) == LOG_REFUND_FAILED_AFTER_SUCCESS
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.active_checkout_id is None
    # 模型说明：succeeded 改为 failed 后成功时间保留。
    assert attempt.succeeded_at == EARLIER_SUCCESS


@pytest.mark.parametrize(
    ("start", "stripe_status"),
    [
        (STRIPE_REFUND_SUCCEEDED, "pending"),
        (STRIPE_REFUND_SUCCEEDED, "requires_action"),
        (STRIPE_REFUND_FAILED, "pending"),
        (STRIPE_REFUND_FAILED, "succeeded"),
        (STRIPE_REFUND_FAILED, "failed"),
        (STRIPE_REFUND_SUCCEEDED, "succeeded"),
        (STRIPE_REFUND_PENDING, "pending"),
    ],
)
def test_other_changes_are_ignored(db: Session, start: str, stripe_status: str) -> None:
    """DESIGN 1.12 第 8 条：「其余（如 succeeded → pending、failed →
    任何状态）忽略」。"""
    attempt_id = _attempt(db, start, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, stripe_status), NOW)
    db.commit()

    assert result is ApplyResult.UNCHANGED
    assert alert_category(result) is None
    after = _snapshot(db, attempt_id)
    assert after.pop("last_checked_at") == NOW
    before.pop("last_checked_at")
    assert after == before


def test_reverse_order_does_not_regress(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「所以并发查询以相反顺序提交也不会倒退」
    「先后颠倒送达的事件不会让状态倒退」：先写较新的 succeeded，再写
    较早查到的 pending，仍为 succeeded。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)

    first = apply_refund_object(db, attempt_id, _refund(attempt_id, "succeeded"), NOW)
    db.commit()
    second = apply_refund_object(db, attempt_id, _refund(attempt_id, "pending"), LATER)
    db.commit()

    assert (first, second) == (ApplyResult.CHANGED, ApplyResult.UNCHANGED)
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.succeeded_at == NOW


def test_apply_only_flushes(db: Session) -> None:
    """DESIGN 1.12 第 7 条：「事件 ID 的去重记录与它引起的订单、积分或
    退款改动在同一个数据库事务里提交」「处理失败整个事务回滚」；第 8 条：
    「退款接口的响应、webhook 与定时查询共用同一写入规则」；验收：
    apply_refund_object「只 flush 不提交，供 webhook 把事件去重放进同一
    事务」：调用方回滚后改动不留下。"""
    attempt_id = _attempt(db)

    result = apply_refund_object(db, attempt_id, _refund(attempt_id, "succeeded"), NOW)
    assert result is ApplyResult.CHANGED
    db.rollback()

    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.stripe_refund_id is None


def test_apply_does_not_log(db: Session, alerts: pytest.LogCaptureFixture) -> None:
    """DESIGN 1.12 第 7 条：「处理失败整个事务回滚、回 5xx，由 Stripe
    重发」——写入规则里记下的告警会随回滚失效而日志不会，所以告警不能在
    写入规则里记；第 8 条：「发现变为 failed 就照上面改并告警」；验收：
    apply_refund_object「不自己记日志，而是返回枚举结果……由调用方在提交
    之后按结果记告警」。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)
    other = _refund(attempt_id, "failed", refund_id="re_test_other")

    apply_refund_object(db, attempt_id, _refund(attempt_id, "failed"), NOW)
    apply_refund_object(db, attempt_id, other, NOW)
    db.commit()

    assert _alerts(alerts) == []


def test_failed_after_success_frees_source_and_alerts(
    db: Session, alerts: pytest.LogCaptureFixture
) -> None:
    """DESIGN 1.12 第 8 条：「发现变为 failed 就照上面改并告警」；
    HANDOFF 0.54：「状态改动引起的告警（请求被明确拒绝、成功后又失败）
    随改动只发生一次，不受它限制」——先因不符告警过的尝试也照记；释放
    来源后同一来源可再建一条尝试（第 8 条：同一来源至多一条不是 failed
    的尝试）。"""
    checkout_id = _checkout(db)
    attempt_id = _attempt(
        db,
        STRIPE_REFUND_SUCCEEDED,
        stripe_refund_id=REFUND_ID,
        checkout_id=checkout_id,
        alerted_at=EARLIER_ALERT,
    )
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "failed")))

    result = refresh_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is RefreshResult.FAILED_AFTER_SUCCESS
    assert _alerts(alerts) == [f"{LOG_REFUND_FAILED_AFTER_SUCCESS} attempt={attempt_id}"]
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.active_checkout_id is None
    assert attempt.alerted_at == EARLIER_ALERT
    # 同一来源随后可再建一条（手动重试）：不撞活动来源的唯一约束。
    retry_id = _attempt(db, checkout_id=checkout_id)
    assert _state(db, retry_id).active_checkout_id == checkout_id


# ---------------------------------------------------------------------------
# 写入规则：核对不符
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"payment_intent": OTHER_PI}, id="other-payment-intent"),
        pytest.param({"payment_intent": None}, id="missing-payment-intent"),
        pytest.param({"attempt_ref": "999999"}, id="other-attempt-id"),
        pytest.param({"attempt_ref": ""}, id="empty-attempt-id"),
    ],
)
def test_mismatch_before_refund_id_is_known_writes_nothing(
    db: Session, overrides: dict[str, Any]
) -> None:
    """DESIGN 1.12 第 8 条：退款事件「按 Stripe 退款 ID 或元数据里的记录
    ID 关联」；验收：核对 payment_intent 等于尝试的 PaymentIntent ID，
    还没记下 Stripe 退款 ID 时元数据里的尝试 ID 必须等于本尝试；不符时
    不改状态与 Stripe 退款 ID，只在告警时间为空时写它。"""
    attempt_id = _attempt(db)
    before = _snapshot(db, attempt_id)
    refund = _refund(attempt_id, "succeeded", **overrides)

    result = apply_refund_object(db, attempt_id, refund, NOW)
    db.commit()

    assert result is ApplyResult.MISMATCH
    assert alert_category(result) == LOG_REFUND_MISMATCH
    after = _snapshot(db, attempt_id)
    assert after.pop("alerted_at") == NOW
    before.pop("alerted_at")
    assert after == before


def test_mismatch_with_other_refund_id(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「有 Stripe 退款对象的尝试，状态一律以向
    Stripe 查询到的该退款当前对象为准」——别的退款对象不能改这条尝试；
    HANDOFF 0.54：告警时间列「只给会反复出现的告警（……Stripe 退款对象与
    尝试不符）去重」；验收：已记下 Stripe 退款 ID 时 Refund 的 ID 必须等于
    它（元数据里的尝试 ID 相符也不行），不符时不改状态与 Stripe 退款 ID。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    refund = _refund(attempt_id, "failed", refund_id="re_test_other")

    result = apply_refund_object(db, attempt_id, refund, NOW)
    db.commit()

    assert result is ApplyResult.MISMATCH
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.active_checkout_id == attempt.checkout_id


def test_known_refund_id_matches_without_metadata(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「按 Stripe 退款 ID 或元数据里的记录 ID
    关联」：已记下 Stripe 退款 ID 时按它核对，元数据缺失也照写。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    refund = Refund(id=REFUND_ID, status="succeeded", amount=AMOUNT, payment_intent=PI)

    result = apply_refund_object(db, attempt_id, refund, NOW)
    db.commit()

    assert result is ApplyResult.CHANGED
    assert _state(db, attempt_id).status == STRIPE_REFUND_SUCCEEDED


def test_mismatch_alerted_once(db: Session, alerts: pytest.LogCaptureFixture) -> None:
    """HANDOFF 0.54：告警时间列「只给会反复出现的告警（……Stripe 退款对象
    与尝试不符）去重，这类告警每条尝试至多一次」：两次查到不符的对象只
    告警一次，告警时间是第一次的时间。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    wrong = _refund(attempt_id, "succeeded", payment_intent=OTHER_PI)
    gateway = FakeStripeGateway(_ok(wrong), _ok(wrong))

    first = refresh_refund_attempt(db, gateway, attempt_id, NOW)
    second = refresh_refund_attempt(db, gateway, attempt_id, LATER)

    assert (first, second) == (RefreshResult.MISMATCH, RefreshResult.MISMATCH)
    assert _alerts(alerts) == [f"{LOG_REFUND_MISMATCH} attempt={attempt_id}"]
    attempt = _state(db, attempt_id)
    assert attempt.alerted_at == NOW
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.last_checked_at == LATER


def test_apply_mismatch_distinguishes_first_and_repeat(db: Session) -> None:
    """HANDOFF 0.54「这类告警每条尝试至多一次」；验收：返回结果区分
    「首次不符」（调用方提交后记告警）与「已告警过的不符」（不再记）。"""
    attempt_id = _attempt(db)
    wrong = _refund(attempt_id, "succeeded", attempt_ref="999999")

    first = apply_refund_object(db, attempt_id, wrong, NOW)
    db.commit()
    second = apply_refund_object(db, attempt_id, wrong, LATER)
    db.commit()

    assert (first, second) == (ApplyResult.MISMATCH, ApplyResult.MISMATCH_ALERTED)
    assert alert_category(second) is None
    assert _state(db, attempt_id).alerted_at == NOW


def test_aware_now_is_rejected(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「已 succeeded 的尝试，定时任务在成功后 30 天
    内每天、之后每周再查询一次」「24 小时后不再调用退款接口」——写入规则
    写下的成功时间与上次复查时间要与库里不带时区的 UTC 时间直接比较，
    带时区的当前时间抛 ValueError，不写入（模型说明：时间一律是不带时区
    的 UTC）。"""
    attempt_id = _attempt(db)
    aware = NOW.replace(tzinfo=UTC)

    with pytest.raises(ValueError):
        apply_refund_object(db, attempt_id, _refund(attempt_id), aware)


# ---------------------------------------------------------------------------
# 调用退款接口
# ---------------------------------------------------------------------------


def test_submit_form_and_idempotency_key(db: Session, alerts: pytest.LogCaptureFixture) -> None:
    """DESIGN 1.12 第 8 条：「以该记录的本站 ID 为幂等键，并写进退款
    元数据」；验收：表单为 payment_intent、amount（尝试金额）与
    metadata[attempt_id]，幂等键为尝试 ID 的十进制字符串；之后调用次数
    加一、写上次复查时间，按写入规则写入。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "pending")))

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.RECORDED
    assert _paths(gateway) == [("POST", "/v1/refunds")]
    call = gateway.calls[0]
    assert call.form == (
        ("payment_intent", PI),
        ("amount", str(AMOUNT)),
        ("metadata[attempt_id]", str(attempt_id)),
    )
    assert call.idempotency_key == str(attempt_id)
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.call_count == 1
    assert attempt.last_checked_at == NOW
    assert _alerts(alerts) == []


def test_submit_succeeded_response(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「Stripe 退款 ID、调用结果……更新记录状态」：
    卡退款即时 succeeded（HANDOFF 0.53 核验），按响应写为 succeeded。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.RECORDED
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.succeeded_at == NOW


def test_submit_mismatch_not_written(db: Session, alerts: pytest.LogCaptureFixture) -> None:
    """DESIGN 1.12 第 8 条：「退款接口的响应、webhook 与定时查询共用同一
    写入规则」；HANDOFF 0.54：告警时间列「只给会反复出现的告警（……Stripe
    退款对象与尝试不符）去重，这类告警每条尝试至多一次」；验收：ok 时按
    apply_refund_object 写入：PaymentIntent 不符时不写
    Stripe 退款 ID 与状态，首次不符记 stripe_refund_mismatch；调用次数
    照加。"""
    attempt_id = _attempt(db)
    wrong = _refund(attempt_id, "succeeded", payment_intent=OTHER_PI)
    gateway = FakeStripeGateway(_ok(wrong))

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.MISMATCH
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id is None
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.call_count == 1
    assert attempt.alerted_at == NOW
    assert _alerts(alerts) == [f"{LOG_REFUND_MISMATCH} attempt={attempt_id}"]


@pytest.mark.parametrize(
    ("error_type", "code", "expected"),
    [
        ("invalid_request_error", "amount_too_large", "invalid_request_error"),
        (None, "charge_already_refunded", "charge_already_refunded"),
        (None, None, None),
        ("x" * 80, None, "x" * 64),
    ],
)
def test_submit_rejected_records_failed_without_retry(
    db: Session,
    alerts: pytest.LogCaptureFixture,
    error_type: str | None,
    code: str | None,
    expected: str | None,
) -> None:
    """DESIGN 1.12 第 8 条：「Stripe 以请求错误明确拒绝创建……才记为
    failed；后一种不自动重试，记下错误类别并告警」；HANDOFF 0.54：
    「以适配器归为 rejected 为准……记为 failed 并记下错误类别（Stripe
    错误的 type），不自动重试」；验收：type 为空时取 code，截到 64 字符。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(_rejected(error_type, code))

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)
    again = submit_refund_attempt(db, gateway, attempt_id, LATER)

    assert (result, again) == (SubmitResult.REJECTED, SubmitResult.SKIPPED)
    assert len(gateway.calls) == 1
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_FAILED
    assert attempt.last_error_type == expected
    assert attempt.active_checkout_id is None
    assert attempt.stripe_refund_id is None
    assert attempt.call_count == 1
    assert attempt.last_checked_at == NOW
    # HANDOFF 0.54：状态改动引起的告警不看也不写告警时间。
    assert attempt.alerted_at is None
    assert _alerts(alerts) == [f"{LOG_REFUND_REJECTED} attempt={attempt_id}"]


class _ConcurrentWriteGateway(FakeStripeGateway):
    """调用退款接口期间，另一个会话（如 webhook）先把尝试写为 succeeded
    并提交。"""

    def __init__(self, engine: Engine, attempt_id: int, *results: StripeResult[Any]) -> None:
        super().__init__(*results)
        self._engine = engine
        self._attempt_id = attempt_id

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        with Session(self._engine) as other:
            attempt = other.get(StripeRefund, self._attempt_id)
            assert attempt is not None
            attempt.stripe_refund_id = REFUND_ID
            attempt.status = STRIPE_REFUND_SUCCEEDED
            attempt.succeeded_at = NOW
            other.commit()
        return super().create_refund(form, idempotency_key)


def test_submit_rejected_after_concurrent_write_is_ignored(
    db: Session, engine: Engine, alerts: pytest.LogCaptureFixture
) -> None:
    """DESIGN 1.12 第 8 条：「有 Stripe 退款对象的尝试，状态一律以向
    Stripe 查询到的该退款当前对象为准（上面 4xx 明确拒绝创建的情形没有
    退款对象，是唯一例外）」；验收：rejected 而尝试已不是 pending 或已有
    Stripe 退款 ID 时只加调用次数与写上次复查时间、不改状态、不告警。"""
    attempt_id = _attempt(db)
    rejected = _rejected("invalid_request_error")
    gateway = _ConcurrentWriteGateway(engine, attempt_id, rejected)

    result = submit_refund_attempt(db, gateway, attempt_id, LATER)

    assert result is SubmitResult.REJECTED_IGNORED
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.last_error_type is None
    assert attempt.active_checkout_id == attempt.checkout_id
    assert attempt.call_count == 1
    assert attempt.last_checked_at == LATER
    assert _alerts(alerts) == []


@pytest.mark.parametrize("outcome", [StripeOutcome.UNAVAILABLE, StripeOutcome.LIVE])
def test_submit_unavailable_keeps_pending(
    db: Session, alerts: pytest.LogCaptureFixture, outcome: StripeOutcome
) -> None:
    """DESIGN 1.12 第 8 条：「其余一律保持 pending、占用额度」「还没有
    Stripe 退款 ID（调用结果未知或可重试的错误）的，由定时任务……以同一
    幂等键自动重试」；验收：unavailable 与 live 保持 pending、调用次数
    加一。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(StripeResult(outcome))

    first = submit_refund_attempt(db, gateway, attempt_id, NOW)
    second = submit_refund_attempt(db, gateway, attempt_id, LATER)

    assert (first, second) == (SubmitResult.UNAVAILABLE, SubmitResult.UNAVAILABLE)
    assert [call.idempotency_key for call in gateway.calls] == [str(attempt_id)] * 2
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.stripe_refund_id is None
    assert attempt.active_checkout_id == attempt.checkout_id
    assert attempt.call_count == 2
    assert attempt.last_checked_at == LATER
    assert _alerts(alerts) == []


def test_submit_replay_retrieves_before_writing(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「有 Stripe 退款对象的尝试，状态一律以向
    Stripe 查询到的该退款当前对象为准」，HANDOFF 0.53 核验：回放的是
    当初保存的响应；验收：回放时先 retrieve_refund 再写。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway(
        _ok(_refund(attempt_id, "pending"), replayed=True),
        _ok(_refund(attempt_id, "succeeded")),
    )

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.RECORDED
    assert _paths(gateway) == [
        ("POST", "/v1/refunds"),
        ("GET", f"/v1/refunds/{REFUND_ID}"),
    ]
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.call_count == 1


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(UNAVAILABLE, id="unavailable"),
        pytest.param(QUERY_REJECTED, id="rejected"),
    ],
)
def test_submit_replay_unqueried_writes_refund_id_only(
    db: Session, query: StripeResult[Any]
) -> None:
    """DESIGN 1.12 第 8 条：「已有 Stripe 退款 ID 而状态未终结的，等退款
    事件或由定时任务查询更新」；验收：查询不可用或被拒时只按回放内容写入
    Stripe 退款 ID，不改状态，留给之后的查询。"""
    attempt_id = _attempt(db)
    replay = _ok(_refund(attempt_id, "succeeded"), replayed=True)
    gateway = FakeStripeGateway(replay, query)

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.REPLAY_PENDING
    attempt = _state(db, attempt_id)
    assert attempt.stripe_refund_id == REFUND_ID
    assert attempt.status == STRIPE_REFUND_PENDING
    assert attempt.succeeded_at is None
    assert attempt.call_count == 1


def test_submit_replay_mismatch_writes_no_id(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「Stripe 退款 ID、调用结果与之后的退款事件
    （按 Stripe 退款 ID 或元数据里的记录 ID 关联）更新记录状态」——元数据
    里的记录 ID 不是本尝试的退款不关联到它；验收：回放而查询不成时只按
    回放内容写入 Stripe 退款 ID，核对同
    apply_refund_object：元数据里的尝试 ID 不符则不写。"""
    attempt_id = _attempt(db)
    replay = _ok(_refund(attempt_id, "pending", attempt_ref="999999"), replayed=True)
    gateway = FakeStripeGateway(replay, UNAVAILABLE)

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.MISMATCH
    assert _state(db, attempt_id).stripe_refund_id is None


@pytest.mark.parametrize(
    ("start", "refund_id"),
    [
        (STRIPE_REFUND_PENDING, REFUND_ID),
        (STRIPE_REFUND_SUCCEEDED, REFUND_ID),
        (STRIPE_REFUND_FAILED, None),
    ],
)
def test_submit_skips_attempts_not_awaiting_call(
    db: Session, start: str, refund_id: str | None
) -> None:
    """DESIGN 1.12 第 8 条：「已有 Stripe 退款 ID 而状态未终结的，等退款
    事件或由定时任务查询更新」「后一种不自动重试」；验收：只处理 pending
    且还没有 Stripe 退款 ID 的尝试。"""
    attempt_id = _attempt(db, start, stripe_refund_id=refund_id)
    gateway = FakeStripeGateway()

    result = submit_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is SubmitResult.SKIPPED
    assert gateway.calls == []
    assert _state(db, attempt_id).call_count == 0


def test_submit_window_closed_after_24_hours(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「24 小时后不再调用退款接口」；验收：创建满
    24 小时不调用、返回 window_closed，不足 24 小时照常调用。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway()

    closed = submit_refund_attempt(db, gateway, attempt_id, WINDOW_END)

    assert closed is SubmitResult.WINDOW_CLOSED
    assert gateway.calls == []
    assert _state(db, attempt_id).call_count == 0

    just_before = WINDOW_END - timedelta(seconds=1)
    still_open = submit_refund_attempt(db, gateway, attempt_id, just_before)

    assert still_open is SubmitResult.UNAVAILABLE
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def test_refresh_writes_queried_object(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「退款事件只作触发，收到后先查询再更新」；
    验收：对已有 Stripe 退款 ID 的尝试调用 retrieve_refund，按
    apply_refund_object 写入并提交。"""
    attempt_id = _attempt(db, stripe_refund_id=REFUND_ID)
    gateway = FakeStripeGateway(_ok(_refund(attempt_id, "succeeded")))

    result = refresh_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is RefreshResult.CHANGED
    assert _paths(gateway) == [("GET", f"/v1/refunds/{REFUND_ID}")]
    attempt = _state(db, attempt_id)
    assert attempt.status == STRIPE_REFUND_SUCCEEDED
    assert attempt.last_checked_at == NOW
    # 查询不调用退款接口，不计调用次数。
    assert attempt.call_count == 0


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(UNAVAILABLE, id="unavailable"),
        pytest.param(QUERY_REJECTED, id="rejected"),
    ],
)
def test_refresh_unavailable_only_writes_checked_time(
    db: Session, alerts: pytest.LogCaptureFixture, query: StripeResult[Any]
) -> None:
    """DESIGN 1.12 第 9 条：「创建会话或查询时 Stripe 不可用」；第 8 条：
    「有 Stripe 退款对象的尝试，状态一律以向 Stripe 查询到的该退款当前
    对象为准」——没查到当前对象就不改状态；验收：Stripe
    不可用或查询被拒时只写上次复查时间，不改状态、不告警。"""
    attempt_id = _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id=REFUND_ID)
    before = _snapshot(db, attempt_id)

    result = refresh_refund_attempt(db, FakeStripeGateway(query), attempt_id, NOW)

    assert result is RefreshResult.UNAVAILABLE
    after = _snapshot(db, attempt_id)
    assert after.pop("last_checked_at") == NOW
    before.pop("last_checked_at")
    assert after == before
    assert _alerts(alerts) == []


def test_refresh_skips_attempt_without_refund_id(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「还没有 Stripe 退款 ID（调用结果未知或可重试
    的错误）的，由定时任务在该尝试创建后 24 小时内（Stripe 保留幂等键的
    最短期限）以同一幂等键自动重试」；验收：refresh_refund_attempt 只处理
    已有 Stripe 退款 ID 的尝试。"""
    attempt_id = _attempt(db)
    gateway = FakeStripeGateway()

    result = refresh_refund_attempt(db, gateway, attempt_id, NOW)

    assert result is RefreshResult.SKIPPED
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# 合计与锁定语句
# ---------------------------------------------------------------------------


def test_refunded_total_excludes_failed(db: Session) -> None:
    """DESIGN 1.12 第 8 条：「同一 PaymentIntent 的 pending 与 succeeded
    退款合计不得超过付款金额（failed 不计）」。"""
    _attempt(db, amount=1000)
    _attempt(db, STRIPE_REFUND_SUCCEEDED, stripe_refund_id="re_a", amount=2000)
    _attempt(db, STRIPE_REFUND_FAILED, stripe_refund_id="re_b", amount=3000)
    _attempt(db, payment_intent_id=OTHER_PI, amount=4000)

    assert refunded_total(db, PI) == 3000
    assert refunded_total(db, OTHER_PI) == 4000
    assert refunded_total(db, "pi_test_none") == 0


def test_lock_statements_compile_for_update() -> None:
    """DESIGN 1.12 第 8 条：「写入时锁住该尝试的行」「新建前在锁住该付款
    的事务里核对」：以 MySQL 方言编译锁定语句含 FOR UPDATE（SQLite 不支持
    行锁）。"""
    lock_sql = str(lock_attempt_statement(7).compile(dialect=mysql.dialect()))
    total_sql = str(refunded_total_statement(PI).compile(dialect=mysql.dialect()))

    assert "FROM stripe_refunds" in lock_sql
    assert "WHERE stripe_refunds.id = " in lock_sql
    assert lock_sql.rstrip().endswith("FOR UPDATE")
    assert "FROM stripe_refunds" in total_sql
    assert "stripe_refunds.payment_intent_id = " in total_sql
    assert "stripe_refunds.status IN " in total_sql
    assert total_sql.rstrip().endswith("FOR UPDATE")

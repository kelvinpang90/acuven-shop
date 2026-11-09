"""迟到与不符付款的退款手动重试（SHOP-TASK-224）。

覆盖 app/services/stripe_refund.py 的 retry_checkout_attempt。依据 docs/DESIGN.md 1.22
（提交 ffb043e）「Stripe 测试环境支付」第 11 条第（2）项与第 8 条。每条测试（参数化的测试
是每个用例）的文档字符串写明它守住的第 11 条哪一句；只出自 SHOP-TASK-224 验收标准的约定
注明「验收：」。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开），并照
tests/test_stripe_refund_approval.py 关掉 pysqlite 的事务处理、由引擎发 BEGIN，保存点才和
MySQL 上一样。订单、结账记录与退款尝试直接按模型写入（第 6 条写成的迟到付款退款：记录为
expired、付款状态 paid，尝试来源为该记录、金额等于付款金额）。本任务不调用 Stripe。SQLite
不执行 FOR UPDATE：锁定与锁顺序以 SQLAlchemy 的连接事件记录语句、按 MySQL 方言编译检查，
并发下的排队按代码审阅。ops_alert.alert 一律以 monkeypatch 替换为记录调用的替身（不入队、
不连 SMTP 与 Redis）。
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select, text
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
from app.services import ops_alert, stripe_refund
from app.services.order_rules import generate_order_number
from app.services.stripe_refund import (
    LOG_REFUND_CAP,
    NoRefundAttempt,
    RefundCapExceeded,
    RetryCheckoutNotFound,
    RetryNotAllowed,
    retry_checkout_attempt,
)

# 订单应付，即迟到付款的金额（会话的 amount_total）。
TOTAL = 6800
SHIPPING = 800

REFUND_LOGGER = "app.services.stripe_refund"

# ops_alert.alert 的（类别, 对象）。
Alert = tuple[str, object]

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

    # 关掉驱动的事务处理、由引擎发 BEGIN，保存点才和 MySQL 上一样。
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
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[Alert]:
    """以 monkeypatch 替换 ops_alert.alert，按调用顺序记下（类别, 对象）。"""
    calls: list[Alert] = []

    def record(category: str, target: object = None, **_: object) -> None:
        calls.append((category, target))

    monkeypatch.setattr(ops_alert, "alert", record)
    return calls


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _make_order(engine: Engine) -> int:
    """直接写一张已取消的游客订单（迟到付款的典型情形）并提交，返回订单 ID。"""
    created = _now() - timedelta(days=1)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=STATUS_CANCELLED,
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
        )
        db.add(order)
        db.commit()
        return order.id


def _late_checkout(engine: Engine, order_id: int) -> tuple[int, str]:
    """直接写一条第 6 条处理过的迟到付款结账记录（expired、付款状态 paid、有完成时间，
    没有确认时间），返回记录 ID 与 PaymentIntent ID。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=1)
    intent_id = f"pi_test_late_{n}"
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_{n}",
            payment_intent_id=intent_id,
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_EXPIRED,
            payment_status=PAYMENT_PAID,
            payment_method_type="card",
            create_params="[]",
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            completed_at=started + timedelta(minutes=40),
            active_order_id=None,
        )
        db.add(record)
        db.commit()
        return record.id, intent_id


def _insert_attempt(
    engine: Engine,
    checkout_id: int,
    intent_id: str,
    status: str,
    amount: int = TOTAL,
    error_type: str | None = None,
) -> int:
    """直接插入一条来源为该结账记录的尝试，返回尝试 ID。"""
    active = None if status == STRIPE_REFUND_FAILED else checkout_id
    succeeded_at = _now() if status == STRIPE_REFUND_SUCCEEDED else None
    with Session(engine) as db:
        attempt = StripeRefund(
            payment_intent_id=intent_id,
            source_type=SOURCE_CHECKOUT,
            checkout_id=checkout_id,
            amount_sen=amount,
            status=status,
            call_count=1,
            last_error_type=error_type,
            created_at=_now() - timedelta(hours=1),
            succeeded_at=succeeded_at,
            active_checkout_id=active,
        )
        db.add(attempt)
        db.commit()
        return attempt.id


def _failed_late_refund(engine: Engine) -> tuple[int, str, int]:
    """一笔迟到付款，全额退款的尝试已为 failed；返回结账记录 ID、PaymentIntent ID 与尝试 ID。"""
    checkout_id, intent_id = _late_checkout(engine, _make_order(engine))
    attempt_id = _insert_attempt(
        engine, checkout_id, intent_id, STRIPE_REFUND_FAILED, error_type="invalid_request_error"
    )
    return checkout_id, intent_id, attempt_id


def _retry(engine: Engine, checkout_id: int, now: datetime | None = None) -> int:
    """在一个新会话里手动重试，由这里（之后的接口）提交。"""
    with Session(engine) as db:
        attempt_id = retry_checkout_attempt(db, checkout_id, now or _now())
        db.commit()
    return attempt_id


def _retry_fails(engine: Engine, checkout_id: int, expected: type[Exception]) -> None:
    """手动重试抛出 expected；会话关闭时回滚。"""
    with Session(engine) as db, pytest.raises(expected):
        retry_checkout_attempt(db, checkout_id, _now())


def _attempts(engine: Engine) -> list[tuple[Any, ...]]:
    """全部退款尝试，按 ID：（ID、来源类别、申请、会话、PaymentIntent、金额、状态、
    活动来源申请列、活动来源会话列）。"""
    with Session(engine) as db:
        rows = db.scalars(select(StripeRefund).order_by(StripeRefund.id))
        return [
            (
                row.id,
                row.source_type,
                row.refund_request_id,
                row.checkout_id,
                row.payment_intent_id,
                row.amount_sen,
                row.status,
                row.active_refund_request_id,
                row.active_checkout_id,
            )
            for row in rows
        ]


def _messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger]


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的事务边界与语句（同 tests/test_stripe_refund_approval.py）：事务边界记为
    begin、rollback、commit；查询记为「select:」加 MySQL 方言编译的 SQL；其余记类名。"""
    log: list[str] = []

    def on_begin(_conn) -> None:
        log.append("begin")

    def on_rollback(_conn) -> None:
        log.append("rollback")

    def on_commit(_conn) -> None:
        log.append("commit")

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if isinstance(clauseelement, str):
            return
        if isinstance(clauseelement, Select):
            log.append("select:" + str(clauseelement.compile(dialect=mysql.dialect())))
        else:
            log.append(type(clauseelement).__name__)

    hooks: list[tuple[str, Callable[..., None]]] = [
        ("begin", on_begin),
        ("rollback", on_rollback),
        ("commit", on_commit),
        ("before_execute", on_execute),
    ]
    for name, hook in hooks:
        event.listen(engine, name, hook)
    try:
        yield log
    finally:
        for name, hook in hooks:
            event.remove(engine, name, hook)


def _locks(log: list[str], table: str) -> list[int]:
    """记录里锁定 table 的查询（含 FOR UPDATE、选出的是该表的列）的位置。锁订单的语句以
    子查询读结账记录，只按选出的列归属，不算锁结账记录。"""
    return [
        index
        for index, entry in enumerate(log)
        if entry.startswith(f"select:SELECT {table}.") and "FOR UPDATE" in entry
    ]


# ---------------------------------------------------------------------------
# 手动重试
# ---------------------------------------------------------------------------


def test_retry_after_failed_creates_pending_attempt(
    engine: Engine, caplog: pytest.LogCaptureFixture, alerts: list[Alert]
) -> None:
    """第 11 条第（2）项「管理员的操作是『重试』，规则与第 8 条退款申请的手动重试相同：
    新建一条尝试，金额等于原尝试金额（该付款的全额）」：返回新尝试的 ID，原 failed 尝试
    不变；新尝试为 pending、PaymentIntent 与金额同原尝试、来源与活动来源列为该结账记录，
    没有 Stripe 退款 ID、调用次数为 0、创建时间为 now；没有日志与告警。"""
    checkout_id, intent_id, first = _failed_late_refund(engine)
    now = _now()

    new_id = _retry(engine, checkout_id, now)

    assert new_id > first
    failed = STRIPE_REFUND_FAILED
    pending = STRIPE_REFUND_PENDING
    assert _attempts(engine) == [
        (first, SOURCE_CHECKOUT, None, checkout_id, intent_id, TOTAL, failed, None, None),
        (new_id, SOURCE_CHECKOUT, None, checkout_id, intent_id, TOTAL, pending, None, checkout_id),
    ]
    with Session(engine) as db:
        attempt = db.get(StripeRefund, new_id)
        assert attempt is not None
        assert (
            attempt.stripe_refund_id,
            attempt.call_count,
            attempt.created_at,
            attempt.last_error_type,
            attempt.succeeded_at,
            attempt.alerted_at,
        ) == (None, 0, now, None, None, None)
    assert _messages(caplog, REFUND_LOGGER) == []
    assert alerts == []


def test_retry_rejects_aware_now_before_database(engine: Engine) -> None:
    """第 11 条第（2）项「新建前在同一事务里按『订单 → 来源结账记录』加锁」：参数不合格时
    不开始这个事务；验收：now 带时区抛 ValueError，先于访问数据库。"""
    checkout_id, _, _ = _failed_late_refund(engine)
    before = _attempts(engine)

    with Session(engine) as db:
        with _trace(engine) as log, pytest.raises(ValueError):
            retry_checkout_attempt(db, checkout_id, datetime.now(UTC))

    assert log == []
    assert _attempts(engine) == before


def test_retry_rolls_back_then_locks_order_then_checkout(engine: Engine) -> None:
    """第 11 条第（2）项「新建前在同一事务里按『订单 → 来源结账记录』加锁，重查该来源最新
    一次尝试仍为 failed，取该尝试的 PaymentIntent，核对该 PaymentIntent 的 pending 与
    succeeded 合计」；验收：先结束此前的事务，订单行是新事务的第一条加锁语句，之后锁该
    记录、再锁同一 PaymentIntent 的尝试；只 flush、不提交。"""
    checkout_id, _, _ = _failed_late_refund(engine)

    with Session(engine) as db:
        assert db.get(StripeCheckout, checkout_id) is not None
        with _trace(engine) as log:
            retry_checkout_attempt(db, checkout_id, _now())

    order_locks = _locks(log, "orders")
    checkout_locks = _locks(log, "stripe_checkouts")
    attempt_locks = _locks(log, "stripe_refunds")
    assert len(order_locks) == 1, log
    assert log[: order_locks[0]] == ["rollback", "begin"], log
    assert len(checkout_locks) == 1 and len(attempt_locks) == 1, log
    assert order_locks[0] < checkout_locks[0] < attempt_locks[0], log
    assert attempt_locks[0] < log.index("Insert", attempt_locks[0])
    assert not {"begin", "rollback", "commit"} & set(log[order_locks[0] :])
    # 会话关闭时回滚：没有提交。
    assert len(_attempts(engine)) == 1


def test_retry_unknown_checkout_raises(engine: Engine) -> None:
    """第 11 条第（2）项「重试」的对象是结账记录；验收：结账记录不存在抛
    RetryCheckoutNotFound，不写库。"""
    checkout_id, _, _ = _failed_late_refund(engine)
    before = _attempts(engine)

    _retry_fails(engine, checkout_id + 1000, RetryCheckoutNotFound)

    assert _attempts(engine) == before


def test_retry_without_attempt_raises(engine: Engine) -> None:
    """第 11 条第（2）项只列「来源为会话（第 6 条）、该来源最新一次尝试为 failed」的记录：
    该记录没有来源为它的尝试（如没写成退款尝试）时抛 NoRefundAttempt、不写库；别的记录的
    尝试不算。"""
    _failed_late_refund(engine)
    checkout_id, _ = _late_checkout(engine, _make_order(engine))
    before = _attempts(engine)

    _retry_fails(engine, checkout_id, NoRefundAttempt)

    assert _attempts(engine) == before


@pytest.mark.parametrize("status", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_retry_refused_unless_latest_failed(engine: Engine, status: str) -> None:
    """第 11 条第（2）项「重查该来源最新一次尝试仍为 failed（不是则回滚并回答已变化）」：
    该来源 ID 最大的尝试为 pending 或 succeeded 时抛 RetryNotAllowed、不写库；更早的一条为
    failed 也不算。"""
    checkout_id, intent_id, _ = _failed_late_refund(engine)
    _insert_attempt(engine, checkout_id, intent_id, status)
    before = _attempts(engine)

    _retry_fails(engine, checkout_id, RetryNotAllowed)

    assert _attempts(engine) == before


@pytest.mark.parametrize(
    ("status", "amount", "created"),
    [
        (STRIPE_REFUND_PENDING, 1, False),
        (STRIPE_REFUND_SUCCEEDED, 1, False),
        (STRIPE_REFUND_FAILED, TOTAL, True),
    ],
    ids=["pending_over", "succeeded_over", "failed_not_counted"],
)
def test_retry_checks_refunded_total_against_payment(
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
    alerts: list[Alert],
    status: str,
    amount: int,
    created: bool,
) -> None:
    """第 11 条第（2）项「核对该 PaymentIntent 的 pending 与 succeeded 合计加上新尝试金额
    不超过付款金额，超出即不建、回滚，之后照常记日志并告警（`stripe_refund_cap`，对象为
    结账记录；告警不随事务回滚）」：同一 PaymentIntent 另有 pending 或 succeeded 的尝试时，
    抛 RefundCapExceeded、不写库，抛出前记一条 stripe_refund_cap checkout=<ID> 日志，紧接着
    以 ("stripe_checkout", 结账记录 ID) 调用一次 ops_alert.alert；failed 不计入，照常建成、
    不记日志、不告警。"""
    checkout_id, intent_id, _ = _failed_late_refund(engine)
    # 同一 PaymentIntent 上别的来源（另一条结账记录）的尝试。
    other_id, _ = _late_checkout(engine, _make_order(engine))
    _insert_attempt(engine, other_id, intent_id, status, amount=amount)
    before = _attempts(engine)

    if created:
        new_id = _retry(engine, checkout_id)
        assert [row[0] for row in _attempts(engine)] == [row[0] for row in before] + [new_id]
        assert _messages(caplog, REFUND_LOGGER) == []
        assert alerts == []
    else:
        _retry_fails(engine, checkout_id, RefundCapExceeded)
        assert _attempts(engine) == before
        assert _messages(caplog, REFUND_LOGGER) == [f"{LOG_REFUND_CAP} checkout={checkout_id}"]
        assert alerts == [(LOG_REFUND_CAP, ("stripe_checkout", checkout_id))]


def test_concurrent_retry_rejected_by_unique_constraint(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 11 条第（2）项「受同一来源至多一条不是 failed 的唯一约束，重复点击与并发只有一个
    成功」「唯一约束冲突同样回答已变化」：另一次重试已先提交了一条 pending 尝试，本次读到的
    最新一条却仍是 failed、合计也没算进那条（替换两处读取模拟并发），插入撞上活动来源列的
    唯一约束，只回滚保存点（外层事务仍在）并抛 RetryNotAllowed，尝试不增加。"""
    checkout_id, _, first = _failed_late_refund(engine)
    _retry(engine, checkout_id)
    before = _attempts(engine)

    def stale(db: Session, _checkout_id: int) -> StripeRefund | None:
        return db.get(StripeRefund, first)

    monkeypatch.setattr(stripe_refund, "_latest_checkout_attempt", stale)
    monkeypatch.setattr(stripe_refund, "refunded_total", lambda _db, _intent: 0)

    with Session(engine) as db:
        with pytest.raises(RetryNotAllowed):
            retry_checkout_attempt(db, checkout_id, _now())
        assert db.in_transaction()
        assert not db.in_nested_transaction()

    assert _attempts(engine) == before

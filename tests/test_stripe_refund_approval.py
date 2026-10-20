"""批准退款时建 Stripe 退款尝试与手动重试（SHOP-TASK-125）。

覆盖 app/services/refund_review.py 的 approve_refund 新加的一步，与
app/services/stripe_refund.py 的 add_request_attempt、retry_request_attempt。
依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 8 条
与「订单与退款状态」，以及 docs/HANDOFF.md 0.54。每条测试（参数化的测试是每个用例）的
文档字符串写明它守住的设计原句；只出自 SHOP-TASK-125 验收标准的约定注明「验收：」。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开），并照
tests/test_refund_review.py 关掉 pysqlite 的事务处理、由引擎发 BEGIN，保存点才和 MySQL
上一样。订单（含逐件分摊快照）与会话记录照 SHOP-TASK-117 的测试
（tests/test_stripe_late_payment.py）直接按模型写入，退款申请经 SHOP-TASK-029 的
submit_refund 提交。本任务不调用 Stripe。SQLite 不执行 FOR UPDATE：锁定与锁顺序以
SQLAlchemy 的连接事件记录语句、按 MySQL 方言编译检查，并发下的排队按代码审阅。
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
from app.models import (
    AdminAccount,
    Order,
    OrderItem,
    OrderItemUnit,
    RefundRequest,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import STATUS_PAID
from app.models.refund import REFUND_APPROVED, REFUND_REQUESTED
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_EXPIRED,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    SOURCE_REFUND_REQUEST,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import refund_review, stripe_refund
from app.services.order_rules import generate_order_number
from app.services.refunds import RefundLineRequest, submit_refund
from app.services.stripe_refund import (
    LOG_REFUND_CAP,
    LOG_REFUND_UNREFUNDABLE,
    NoRefundAttempt,
    RefundCapExceeded,
    RefundRequestNotApproved,
    RetryNotAllowed,
    RetryRequestNotFound,
    retry_request_attempt,
)

# 订单行：单价、件数与每件的用券折扣。第 1 行整件用券抵掉，逐件现金实付为 0。
LINES = [(3000, 2, 0), (1500, 1, 1500)]
SUBTOTAL = 2 * 3000 + 1500
COUPON = 1500
SHIPPING = 800
# 应付 6800 仙：会话记录的 amount_sen，即付款金额。
TOTAL = SUBTOTAL - COUPON + SHIPPING

# 第 0 行一件的现金实付。
UNIT_CASH = 3000

REVIEW_LOGGER = "app.services.refund_review"
REFUND_LOGGER = "app.services.stripe_refund"

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


@pytest.fixture
def admin_id(engine: Engine) -> int:
    with Session(engine) as db:
        row = AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        db.add(row)
        db.flush()
        account_id = row.id
        db.commit()
    return account_id


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _make_order(engine: Engine) -> int:
    """直接写一张已支付（demo_paid）的游客订单与逐件分摊快照并提交，返回订单 ID。"""
    created = _now() - timedelta(days=1)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=STATUS_PAID,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=COUPON,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=TOTAL,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=created + timedelta(minutes=5),
        )
        db.add(order)
        db.flush()
        for line_index, (price, quantity, coupon) in enumerate(LINES):
            item = OrderItem(
                order_id=order.id,
                line_index=line_index,
                variant_id=None,
                sku=f"SKU-{line_index}",
                product_name_en="Item",
                product_name_zh="商品",
                product_name_ms="Barang",
                variant_label_en="",
                variant_label_zh="",
                variant_label_ms="",
                unit_price_sen=price,
                quantity=quantity,
                line_subtotal_sen=price * quantity,
            )
            db.add(item)
            db.flush()
            for unit_index in range(quantity):
                db.add(
                    OrderItemUnit(
                        order_item_id=item.id,
                        unit_index=unit_index,
                        original_price_sen=price,
                        coupon_discount_sen=coupon,
                        points_discount=0,
                        cash_paid_sen=price - coupon,
                        points_earned=0,
                    )
                )
        db.flush()
        order_id = order.id
        db.commit()
    return order_id


def _stripe_paid(engine: Engine, order_id: int, *, intent: bool = True) -> tuple[int, str | None]:
    """直接写该单由 Stripe 付款的会话记录（complete、paid、确认时间非空），
    返回记录 ID 与 PaymentIntent ID；intent 为假时 PaymentIntent ID 为空。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=1)
    intent_id = f"pi_test_{n}" if intent else None
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_{n}",
            payment_intent_id=intent_id,
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_COMPLETE,
            payment_status=PAYMENT_PAID,
            payment_method_type="card",
            create_params="[]",
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            completed_at=started + timedelta(minutes=4),
            confirmed_at=started + timedelta(minutes=4),
            active_order_id=order_id,
        )
        db.add(record)
        db.commit()
        return record.id, intent_id


def _expired_unpaid(engine: Engine, order_id: int) -> int:
    """直接写一条没有付款的 expired 会话记录（访客发起后改用模拟支付）。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=1)
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_{n}",
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_EXPIRED,
            payment_status=PAYMENT_UNPAID,
            create_params="[]",
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            active_order_id=None,
        )
        db.add(record)
        db.commit()
        return record.id


def _submit(engine: Engine, order_id: int, lines: list[tuple[int, int]]) -> int:
    """经 submit_refund 提交一笔退款申请（各行为行序与件数），返回申请 ID。"""
    key = _key()
    requested = [RefundLineRequest(line_index, quantity) for line_index, quantity in lines]
    with Session(engine) as db:
        submit_refund(db, order_id, requested, key, _now())
        stmt = select(RefundRequest.id).where(RefundRequest.idempotency_key == key)
        return db.scalars(stmt).one()


def _approve(
    engine: Engine,
    request_id: int,
    admin_id: int,
    key: str | None = None,
    now: datetime | None = None,
) -> tuple[refund_review.ReviewOutcome, bool]:
    """在一个新会话里批准，由这里（调用方）提交。"""
    with Session(engine) as db:
        result = refund_review.approve_refund(
            db, request_id, admin_id, None, key or _key(), now or _now()
        )
        db.commit()
    return result


def _reject(engine: Engine, request_id: int, admin_id: int) -> None:
    with Session(engine) as db:
        refund_review.reject_refund(db, request_id, admin_id, "Not eligible", _key(), _now())
        db.commit()


def _retry(engine: Engine, request_id: int, now: datetime | None = None) -> int:
    """在一个新会话里手动重试，由这里（之后的接口）提交。"""
    with Session(engine) as db:
        attempt_id = retry_request_attempt(db, request_id, now or _now())
        db.commit()
    return attempt_id


def _retry_fails(engine: Engine, request_id: int, expected: type[Exception]) -> None:
    """手动重试抛出 expected；会话关闭时回滚。"""
    with Session(engine) as db, pytest.raises(expected):
        retry_request_attempt(db, request_id, _now())


def _attempts(engine: Engine) -> list[tuple[Any, ...]]:
    """全部退款尝试，按 ID：（ID、来源类别、申请、会话、PaymentIntent、金额、状态、
    活动来源申请列）。"""
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
            )
            for row in rows
        ]


def _request_attempts(engine: Engine, request_id: int) -> list[tuple[Any, ...]]:
    return [row for row in _attempts(engine) if row[2] == request_id]


def _set_status(engine: Engine, attempt_id: int, status: str) -> None:
    """把一条尝试改为 status（模拟退款任务之后按 Stripe 写入的结果）。"""
    with Session(engine) as db:
        attempt = db.get(StripeRefund, attempt_id)
        assert attempt is not None
        attempt.status = status
        if status == STRIPE_REFUND_SUCCEEDED:
            attempt.succeeded_at = _now()
        if status == STRIPE_REFUND_FAILED:
            attempt.active_refund_request_id = None
            attempt.active_checkout_id = None
        db.commit()


def _insert_checkout_attempt(
    engine: Engine, checkout_id: int, intent_id: str, amount: int, status: str
) -> None:
    """直接插入一条复用该付款 PaymentIntent ID 的尝试（来源为该会话记录）。"""
    active = None if status == STRIPE_REFUND_FAILED else checkout_id
    succeeded_at = _now() if status == STRIPE_REFUND_SUCCEEDED else None
    with Session(engine) as db:
        db.add(
            StripeRefund(
                payment_intent_id=intent_id,
                source_type=SOURCE_CHECKOUT,
                checkout_id=checkout_id,
                amount_sen=amount,
                status=status,
                call_count=1,
                created_at=_now() - timedelta(hours=1),
                succeeded_at=succeeded_at,
                active_checkout_id=active,
            )
        )
        db.commit()


def _status(engine: Engine, request_id: int) -> str:
    with Session(engine) as db:
        return db.scalars(select(RefundRequest.status).where(RefundRequest.id == request_id)).one()


def _messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger]


def _approved_stripe_request(engine: Engine, admin_id: int) -> tuple[int, int, str]:
    """Stripe 付款订单上一笔已批准、现金退款额 3000 仙的申请；
    返回申请 ID、会话记录 ID 与 PaymentIntent ID。"""
    order_id = _make_order(engine)
    checkout_id, intent_id = _stripe_paid(engine, order_id)
    assert intent_id is not None
    request_id = _submit(engine, order_id, [(0, 1)])
    _approve(engine, request_id, admin_id)
    return request_id, checkout_id, intent_id


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的事务边界与语句（同 tests/test_refund_review.py）：事务边界记为
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
    """记录里锁定 table（含 FOR UPDATE、读该表）的查询的位置。"""
    return [
        index
        for index, entry in enumerate(log)
        if entry.startswith("select:") and "FOR UPDATE" in entry and f"FROM {table}" in entry
    ]


def _reads(log: list[str], table: str) -> list[int]:
    """记录里读 table 的查询（不论是否加锁）的位置。"""
    return [
        index
        for index, entry in enumerate(log)
        if entry.startswith("select:") and f"FROM {table}" in entry
    ]


# ---------------------------------------------------------------------------
# 批准时建尝试
# ---------------------------------------------------------------------------


def test_approve_stripe_order_writes_one_pending_attempt(
    engine: Engine, admin_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「管理员批准退款的事务不变；订单经 Stripe 付款且本次批准的现金退款额
    （不含运费）大于 0 时，同一事务另写一条 pending 的 `StripeRefund` 尝试，来源为该退款
    申请，金额为该现金退款额」：一条 pending 尝试，金额等于申请的 amount_sen（第 0 行
    一件 3000 仙；用券抵掉的第 1 行与运费不计），PaymentIntent 取该付款，活动来源列为
    该申请；批准结果照旧，不记日志。"""
    order_id = _make_order(engine)
    _, intent_id = _stripe_paid(engine, order_id)
    request_id = _submit(engine, order_id, [(0, 1), (1, 1)])
    now = _now()

    outcome, reviewed = _approve(engine, request_id, admin_id, now=now)

    assert reviewed is True
    assert outcome.status == REFUND_APPROVED
    assert _status(engine, request_id) == REFUND_APPROVED
    with Session(engine) as db:
        request = db.get(RefundRequest, request_id)
        assert request is not None
        assert request.amount_sen == UNIT_CASH
        attempt = db.scalars(select(StripeRefund)).one()
        assert (
            attempt.source_type,
            attempt.refund_request_id,
            attempt.checkout_id,
            attempt.payment_intent_id,
            attempt.amount_sen,
            attempt.status,
            attempt.active_refund_request_id,
            attempt.active_checkout_id,
            attempt.stripe_refund_id,
            attempt.call_count,
            attempt.created_at,
        ) == (
            SOURCE_REFUND_REQUEST,
            request_id,
            None,
            intent_id,
            UNIT_CASH,
            STRIPE_REFUND_PENDING,
            request_id,
            None,
            None,
            0,
            now,
        )
    assert _messages(caplog, REVIEW_LOGGER) == []


def test_approve_lock_order(engine: Engine, admin_id: int) -> None:
    """第 8 条「新建前在锁住该付款的事务里核对」；验收：锁顺序为订单、会员、
    Stripe 会话记录、退款尝试，新增的读取都在锁订单之后，approve_refund 不新增
    begin、rollback 或 commit（游客订单没有会员行）。"""
    order_id = _make_order(engine)
    _stripe_paid(engine, order_id)
    request_id = _submit(engine, order_id, [(0, 1)])

    with Session(engine) as db:
        assert db.get(AdminAccount, admin_id) is not None
        with _trace(engine) as log:
            refund_review.approve_refund(db, request_id, admin_id, None, _key(), _now())

    order_locks = _locks(log, "orders")
    checkout_locks = _locks(log, "stripe_checkouts")
    attempt_locks = _locks(log, "stripe_refunds")
    assert len(order_locks) == 1, log
    assert log[: order_locks[0]] == ["rollback", "begin"], log
    assert len(checkout_locks) == 1 and len(attempt_locks) == 1, log
    assert order_locks[0] < checkout_locks[0] < attempt_locks[0], log
    assert attempt_locks[0] < log.index("Insert", attempt_locks[0])
    assert not {"begin", "rollback", "commit"} & set(log[order_locks[0] :])


def test_replay_by_review_key_does_not_create_again(engine: Engine, admin_id: int) -> None:
    """第 8 条「管理员批准退款的事务不变」：按审核幂等键重放返回原结果、不改数据；
    验收：只在不是按幂等键重放时建尝试。首次批准的尝试已为 failed（来源已释放），
    重放仍不另建。"""
    request_id, _, _ = _approved_stripe_request(engine, admin_id)
    with Session(engine) as db:
        key = db.scalars(
            select(RefundRequest.review_idempotency_key).where(RefundRequest.id == request_id)
        ).one()
    assert key is not None
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)
    before = _attempts(engine)

    _, reviewed = _approve(engine, request_id, admin_id, key=key)

    assert reviewed is False
    assert _attempts(engine) == before


def test_zero_cash_request_does_not_create_or_read_stripe(engine: Engine, admin_id: int) -> None:
    """「订单与退款状态」与第 8 条「大于 0 时」：现金退款额为 0 时不建尝试；验收：
    金额为 0 的申请不读 Stripe 记录。只退用券抵掉的那件，批准照常成功。"""
    order_id = _make_order(engine)
    _stripe_paid(engine, order_id)
    request_id = _submit(engine, order_id, [(1, 1)])

    with Session(engine) as db:
        with _trace(engine) as log:
            refund_review.approve_refund(db, request_id, admin_id, None, _key(), _now())
        db.commit()

    assert _status(engine, request_id) == REFUND_APPROVED
    assert _attempts(engine) == []
    assert not [entry for entry in log if "stripe_" in entry], log


@pytest.mark.parametrize("record", ["none", "expired_unpaid"])
def test_mock_paid_order_does_not_create(engine: Engine, admin_id: int, record: str) -> None:
    """第 8 条「订单经 Stripe 付款」才建：经模拟支付的订单（没有会话记录，或只有没付款的
    expired 记录）批准照常成功、不建尝试；验收：只会查到没有确认时间非空的会话记录，
    不锁也不写。"""
    order_id = _make_order(engine)
    if record == "expired_unpaid":
        _expired_unpaid(engine, order_id)
    request_id = _submit(engine, order_id, [(0, 1)])

    with Session(engine) as db:
        with _trace(engine) as log:
            refund_review.approve_refund(db, request_id, admin_id, None, _key(), _now())
        db.commit()

    assert _status(engine, request_id) == REFUND_APPROVED
    assert _attempts(engine) == []
    assert _reads(log, "stripe_checkouts"), log
    assert _locks(log, "stripe_checkouts") == [], log
    assert _reads(log, "stripe_refunds") == [], log


@pytest.mark.parametrize(
    ("status", "amount", "created"),
    [
        (STRIPE_REFUND_PENDING, TOTAL - UNIT_CASH + 1, False),
        (STRIPE_REFUND_SUCCEEDED, TOTAL - UNIT_CASH + 1, False),
        (STRIPE_REFUND_FAILED, TOTAL - UNIT_CASH + 1, True),
        (STRIPE_REFUND_PENDING, TOTAL - UNIT_CASH, True),
    ],
    ids=["pending_over", "succeeded_over", "failed_not_counted", "pending_equal"],
)
def test_approve_checks_refunded_total_against_payment(
    engine: Engine,
    admin_id: int,
    caplog: pytest.LogCaptureFixture,
    status: str,
    amount: int,
    created: bool,
) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额
    （failed 不计），新建前在锁住该付款的事务里核对，超出即不建并告警」：已有的 pending
    或 succeeded 加本次超过付款金额时不建、告警一次（stripe_refund_cap，只含申请 ID），
    批准照常成功；failed 不计入；合计正好等于付款金额不算超过。"""
    order_id = _make_order(engine)
    checkout_id, intent_id = _stripe_paid(engine, order_id)
    assert intent_id is not None
    _insert_checkout_attempt(engine, checkout_id, intent_id, amount, status)
    request_id = _submit(engine, order_id, [(0, 1)])

    outcome, reviewed = _approve(engine, request_id, admin_id)

    assert (outcome.status, reviewed) == (REFUND_APPROVED, True)
    assert _status(engine, request_id) == REFUND_APPROVED
    attempts = _request_attempts(engine, request_id)
    if created:
        assert [row[4:] for row in attempts] == [
            (intent_id, UNIT_CASH, STRIPE_REFUND_PENDING, request_id)
        ]
        assert _messages(caplog, REVIEW_LOGGER) == []
    else:
        assert attempts == []
        assert _messages(caplog, REVIEW_LOGGER) == [f"{LOG_REFUND_CAP} refund_request={request_id}"]


def test_missing_payment_intent_does_not_create_and_alerts(
    engine: Engine, admin_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    """「数据模型」的 StripeRefund 一行：尝试有「所属 PaymentIntent」；验收：该记录缺
    PaymentIntent ID 时不建并告警（stripe_refund_unrefundable，只含申请 ID），批准照常成功。"""
    order_id = _make_order(engine)
    _stripe_paid(engine, order_id, intent=False)
    request_id = _submit(engine, order_id, [(0, 1)])

    _approve(engine, request_id, admin_id)

    assert _status(engine, request_id) == REFUND_APPROVED
    assert _attempts(engine) == []
    assert _messages(caplog, REVIEW_LOGGER) == [
        f"{LOG_REFUND_UNREFUNDABLE} refund_request={request_id}"
    ]


# ---------------------------------------------------------------------------
# 手动重试
# ---------------------------------------------------------------------------


def test_retry_after_failed_creates_new_attempt(engine: Engine, admin_id: int) -> None:
    """第 8 条「最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试」：
    返回新尝试的 ID，原 failed 尝试不变，新尝试为 pending、来源为该申请、金额为申请的
    现金退款额。"""
    request_id, _, intent_id = _approved_stripe_request(engine, admin_id)
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)
    now = _now()

    new_id = _retry(engine, request_id, now)

    attempts = _request_attempts(engine, request_id)
    assert [row[0] for row in attempts] == [first[0], new_id]
    assert new_id > first[0]
    assert attempts[0][6] == STRIPE_REFUND_FAILED
    assert attempts[1][1:] == (
        SOURCE_REFUND_REQUEST,
        request_id,
        None,
        intent_id,
        UNIT_CASH,
        STRIPE_REFUND_PENDING,
        request_id,
    )
    with Session(engine) as db:
        attempt = db.get(StripeRefund, new_id)
        assert attempt is not None
        assert (attempt.created_at, attempt.call_count) == (now, 0)


def test_retry_rolls_back_then_locks_in_order(engine: Engine, admin_id: int) -> None:
    """第 8 条「新建前在锁住该付款的事务里核对」；验收：retry_request_attempt 先 rollback
    再锁订单，锁顺序为订单、Stripe 会话记录、退款尝试，只 flush、不提交。"""
    request_id, _, _ = _approved_stripe_request(engine, admin_id)
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)

    with Session(engine) as db:
        assert db.get(AdminAccount, admin_id) is not None
        with _trace(engine) as log:
            retry_request_attempt(db, request_id, _now())

    order_locks = _locks(log, "orders")
    checkout_locks = _locks(log, "stripe_checkouts")
    attempt_locks = _locks(log, "stripe_refunds")
    assert len(order_locks) == 1, log
    assert log[: order_locks[0]] == ["rollback", "begin"], log
    assert len(checkout_locks) == 1 and len(attempt_locks) == 1, log
    assert order_locks[0] < checkout_locks[0] < attempt_locks[0], log
    assert not {"begin", "rollback", "commit"} & set(log[order_locks[0] :])
    # 会话关闭时回滚：没有提交。
    assert len(_request_attempts(engine, request_id)) == 1


@pytest.mark.parametrize("status", [STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED])
def test_retry_refused_unless_latest_failed(engine: Engine, admin_id: int, status: str) -> None:
    """第 8 条「最新一次尝试为 failed 时」才可手动重试：最新一条（尝试 ID 最大）
    为 pending 或 succeeded 时抛不能重试、不写库；更早的一条为 failed 也不算。"""
    request_id, _, _ = _approved_stripe_request(engine, admin_id)
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)
    second = _retry(engine, request_id)
    if status == STRIPE_REFUND_SUCCEEDED:
        _set_status(engine, second, STRIPE_REFUND_SUCCEEDED)
    before = _attempts(engine)

    _retry_fails(engine, request_id, RetryNotAllowed)

    assert _attempts(engine) == before


def test_retry_requires_approved_request(engine: Engine, admin_id: int) -> None:
    """「订单与退款状态」：requested → 管理员 approved 或 rejected，退款只对 approved；
    验收：申请不存在、未批准（requested、rejected）、没有尝试（现金退款额为 0 不建）时
    各抛自己的异常、不写库。"""
    order_id = _make_order(engine)
    _stripe_paid(engine, order_id)
    requested = _submit(engine, order_id, [(0, 1)])
    rejected = _submit(engine, order_id, [(0, 1)])
    _reject(engine, rejected, admin_id)
    zero = _submit(engine, order_id, [(1, 1)])
    _approve(engine, zero, admin_id)

    _retry_fails(engine, requested, RefundRequestNotApproved)
    _retry_fails(engine, rejected, RefundRequestNotApproved)
    _retry_fails(engine, zero, NoRefundAttempt)
    _retry_fails(engine, zero + 1000, RetryRequestNotFound)

    assert _status(engine, requested) == REFUND_REQUESTED
    assert _attempts(engine) == []


def test_retry_over_cap_raises_without_writing(
    engine: Engine, admin_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额
    （failed 不计），新建前在锁住该付款的事务里核对，超出即不建并告警」：重试时合计超过
    付款金额，抛 RefundCapExceeded、不写库，并在抛出前记一条 stripe_refund_cap 日志
    （只含申请 ID）。"""
    request_id, checkout_id, intent_id = _approved_stripe_request(engine, admin_id)
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)
    _insert_checkout_attempt(
        engine, checkout_id, intent_id, TOTAL - UNIT_CASH + 1, STRIPE_REFUND_PENDING
    )
    before = _attempts(engine)

    _retry_fails(engine, request_id, RefundCapExceeded)

    assert _attempts(engine) == before
    assert _messages(caplog, REFUND_LOGGER) == [f"{LOG_REFUND_CAP} refund_request={request_id}"]


def test_concurrent_retry_rejected_by_unique_constraint(
    engine: Engine, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 8 条「重试是新建一条尝试（受上述唯一约束，重复点击与并发只有一个成功）」：
    另一次重试已先提交了一条 pending 尝试，本次读到的最新一条却仍是 failed（替换读取
    模拟并发），插入撞上活动来源列的唯一约束，只回滚保存点（外层事务仍在）并抛不能重试，
    尝试不增加。"""
    request_id, _, _ = _approved_stripe_request(engine, admin_id)
    (first,) = _request_attempts(engine, request_id)
    _set_status(engine, first[0], STRIPE_REFUND_FAILED)
    _retry(engine, request_id)
    before = _attempts(engine)

    def stale(db: Session, _request_id: int) -> StripeRefund | None:
        return db.get(StripeRefund, first[0])

    monkeypatch.setattr(stripe_refund, "_latest_request_attempt", stale)

    with Session(engine) as db:
        with pytest.raises(RetryNotAllowed):
            retry_request_attempt(db, request_id, _now())
        assert db.in_transaction()
        assert not db.in_nested_transaction()

    assert _attempts(engine) == before

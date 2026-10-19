"""Stripe 事件表与退款尝试表的数据库约束。

依据 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的 StripeCheckout /
StripeEvent / StripeRefund 一行，「Stripe 测试环境支付」第 6、7、8、10 条
（事件 ID 去重；每条退款尝试有自己的本站 ID，同一来源至多一条不是 failed
的尝试，由数据库唯一约束保证）。每条测试（参数化的测试是每个用例）写明它
守住的设计原句；只出自本任务验收标准的约束注明「验收：」。

先写入合法的事件（两种处理结果）与退款尝试（两种来源、三种状态，各用不同
的来源）作对照，再逐个写反例。检查约束的反例先在独立的 SQLite 连接上逐条
求值该表的全部检查约束，断言目标约束确实不成立，再断言写入被拒、且报出的
是不成立的约束之一。非空约束的反例用 Core insert 显式写入 NULL：ORM 会略过
值为 None 的列、改用默认值。用 SQLite 内存库按模型建表；SQLite 默认不检查
外键，每个连接都须打开。退款申请用 requested 状态、会话记录用已付款的
expired 记录（迟到付款）：库不检查来源的状态，只在批准或核对不符时建尝试，
由规则任务保证。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Order, RefundRequest, StripeCheckout, StripeEvent, StripeRefund
from app.models.order import ACTOR_GUEST, STATUS_AWAITING_PAYMENT
from app.models.refund import REFUND_REQUESTED
from app.models.stripe import (
    ACTIVE_STRIPE_REFUND_STATUSES,
    CHECKOUT_EXPIRED,
    CURRENCY_MYR,
    EVENT_IGNORED,
    EVENT_OUTCOMES,
    EVENT_PROCESSED,
    PAYMENT_PAID,
    REFUND_SOURCE_TYPES,
    SOURCE_CHECKOUT,
    SOURCE_REFUND_REQUEST,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_STATUSES,
    STRIPE_REFUND_SUCCEEDED,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 15, 2, 0)
COMPLETED = CREATED + timedelta(minutes=5)
SUCCEEDED = CREATED + timedelta(minutes=10)
FINGERPRINT = "0f" * 32

# 覆盖值里的占位：写入时换成这组来源的退款申请 ID、会话记录 ID，或一个不存在的 ID。
_REFUND_REQUEST = "<refund request>"
_CHECKOUT = "<checkout>"
_OTHER = "<other>"
_MISSING_ID = 999_999

# 只用来对单行求值检查约束表达式，不建任何表。
_EVAL_ENGINE = create_engine("sqlite://")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield db
    engine.dispose()


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


_ORDER_SEQUENCE = itertools.count(1)
_REQUEST_SEQUENCE = itertools.count(1)
_CHECKOUT_SEQUENCE = itertools.count(1)
_EVENT_SEQUENCE = itertools.count(1)
_ATTEMPT_SEQUENCE = itertools.count(1)


def _order(session: Session) -> Order:
    """待支付的游客订单，商品 RM60、运费 RM8；每次给不同的订单号与幂等键。"""
    n = next(_ORDER_SEQUENCE)
    return _add(
        session,
        Order(
            order_number=f"{n:016d}",
            status=STATUS_AWAITING_PAYMENT,
            subtotal_sen=6000,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=6800,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"order-key-{n}",
            request_fingerprint=FINGERPRINT,
            created_at=CREATED,
            payment_expires_at=CREATED + timedelta(minutes=15),
            paid_at=None,
            member_id=None,
        ),
    )


def _refund_request(session: Session, order_id: int) -> RefundRequest:
    n = next(_REQUEST_SEQUENCE)
    return _add(
        session,
        RefundRequest(
            order_id=order_id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_GUEST,
            idempotency_key=f"refund-key-{n}",
            request_fingerprint=FINGERPRINT,
            amount_sen=1000,
            created_at=CREATED,
        ),
    )


def _late_checkout(session: Session, order_id: int) -> StripeCheckout:
    """第 6 条的迟到付款：已付款、已标为 expired 的会话记录。"""
    n = next(_CHECKOUT_SEQUENCE)
    session_id = f"cs_test_{n:06d}"
    return _add(
        session,
        StripeCheckout(
            order_id=order_id,
            idempotency_key=f"checkout-key-{n}",
            checkout_session_id=session_id,
            payment_intent_id=f"pi_test_{n:06d}",
            amount_sen=6800,
            currency=CURRENCY_MYR,
            status=CHECKOUT_EXPIRED,
            payment_status=PAYMENT_PAID,
            payment_method_type="fpx",
            checkout_url=f"https://checkout.stripe.com/c/pay/{session_id}",
            create_params="mode=payment&currency=myr",
            expires_at=CREATED + timedelta(minutes=31),
            created_at=CREATED,
            completed_at=COMPLETED,
            active_order_id=None,
        ),
    )


@dataclass
class Sources:
    """一组退款来源：同一订单上的一条退款申请与一条迟到付款的会话记录。"""

    refund_request_id: int
    checkout_id: int
    payment_intent_id: str


def _sources(session: Session) -> Sources:
    order = _order(session)
    request = _refund_request(session, order.id)
    checkout = _late_checkout(session, order.id)
    assert checkout.payment_intent_id is not None
    return Sources(request.id, checkout.id, checkout.payment_intent_id)


def _resolve(sources: Sources, value: Any) -> Any:
    if value == _REFUND_REQUEST:
        return sources.refund_request_id
    if value == _CHECKOUT:
        return sources.checkout_id
    if value == _OTHER:
        return _MISSING_ID
    return value


def _refund_values(
    sources: Sources, source_type: str, status: str, /, **overrides: Any
) -> dict[str, Any]:
    """该来源类别与状态一条合法的退款尝试；每次给不同的 Stripe 退款 ID。
    前三个参数只按位置传，overrides 里才能再带 source_type 与 status 覆盖。
    """
    n = next(_ATTEMPT_SEQUENCE)
    by_request = source_type == SOURCE_REFUND_REQUEST
    active = status in ACTIVE_STRIPE_REFUND_STATUSES
    failed = status == STRIPE_REFUND_FAILED
    values: dict[str, Any] = {
        "payment_intent_id": sources.payment_intent_id,
        "source_type": source_type,
        "refund_request_id": sources.refund_request_id if by_request else None,
        "checkout_id": None if by_request else sources.checkout_id,
        # 明确拒绝创建（4xx）时没有建成退款，没有 Stripe 退款 ID。
        "stripe_refund_id": None if failed else f"re_test_{n:06d}",
        "amount_sen": 1000 if by_request else 6800,
        "status": status,
        "call_count": 1,
        "last_error_type": "invalid_request_error" if failed else None,
        "created_at": CREATED,
        "succeeded_at": SUCCEEDED if status == STRIPE_REFUND_SUCCEEDED else None,
        "last_checked_at": None,
        "active_refund_request_id": sources.refund_request_id if active and by_request else None,
        "active_checkout_id": sources.checkout_id if active and not by_request else None,
    }
    return values | {key: _resolve(sources, value) for key, value in overrides.items()}


def _attempt(
    session: Session, sources: Sources, source_type: str, status: str, /, **overrides: Any
) -> StripeRefund:
    values = _refund_values(sources, source_type, status, **overrides)
    return _add(session, StripeRefund(**values))


def _event_values(**overrides: Any) -> dict[str, Any]:
    n = next(_EVENT_SEQUENCE)
    values: dict[str, Any] = {
        "event_id": f"evt_test_{n:06d}",
        "event_type": "checkout.session.completed",
        "outcome": EVENT_PROCESSED,
        "processed_at": CREATED,
    }
    return values | overrides


def _check_constraints(model: type[Base]) -> list[CheckConstraint]:
    return [c for c in model.__table__.constraints if isinstance(c, CheckConstraint)]


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints(model):
            sql = text(f"SELECT ({constraint.sqltext}) FROM (SELECT {columns})")
            if connection.execute(sql, params).scalar_one() == 0:
                failing.append(str(constraint.name))
    return failing


def _assert_check_rejects(
    session: Session, model: type[Base], constraint: str, values: dict[str, Any]
) -> None:
    failing = _failing_checks(model, values)
    assert constraint in failing
    pattern = r"CHECK constraint failed: (?:" + "|".join(failing) + r")\b"

    with pytest.raises(IntegrityError, match=pattern):
        session.execute(insert(model).values(**values))


def _assert_null_rejected(
    session: Session, model: type[Base], column: str, values: dict[str, Any]
) -> None:
    table = model.__table__.name
    message = f"NOT NULL constraint failed: {table}.{column}"

    with pytest.raises(IntegrityError, match=message):
        session.execute(insert(model).values(**(values | {column: None})))


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_events_are_accepted(session: Session) -> None:
    """StripeEvent 一行「Stripe 事件 ID（唯一）、类型、处理结果与时间」与第 7 条
    「只处理第 4、5 条的 Checkout 事件与 Stripe 退款状态事件，其他类型回 200
    不处理」：处理过的 Checkout 事件、退款事件与不处理的其他类型各一条都写得
    进去。这是下面各条事件拒绝测试的对照。
    """
    rows = [
        _event_values(event_type="checkout.session.completed", outcome=EVENT_PROCESSED),
        _event_values(event_type="refund.updated", outcome=EVENT_PROCESSED),
        _event_values(event_type="customer.created", outcome=EVENT_IGNORED),
    ]
    for values in rows:
        assert _failing_checks(StripeEvent, values) == []
        _add(session, StripeEvent(**values))

    outcomes = session.scalars(select(StripeEvent.outcome).order_by(StripeEvent.id)).all()
    assert outcomes == [EVENT_PROCESSED, EVENT_PROCESSED, EVENT_IGNORED]
    assert set(outcomes) == set(EVENT_OUTCOMES)


def test_valid_refund_attempts_of_both_sources_and_every_status_are_accepted(
    session: Session,
) -> None:
    """StripeRefund 一行「来源（退款申请或迟到付款的会话）及其 ID……状态
    （pending / succeeded / failed）」、第 6 条「记为来源为该会话的
    `StripeRefund` 尝试」与第 8 条「另写一条 pending 的 `StripeRefund` 尝试，
    来源为该退款申请」：两种来源、三种状态各一条（各用不同的来源）都写得进去；
    不是 failed 的四条与来源类别对应的活动列等于来源，failed 的两条活动列都
    为空。这是下面各条退款尝试拒绝测试的对照：同样的建表与外键检查下，合法
    数据写得进去，且求值辅助函数对每种组合都不报任何约束不成立。
    """
    for source_type in REFUND_SOURCE_TYPES:
        for status in STRIPE_REFUND_STATUSES:
            values = _refund_values(_sources(session), source_type, status)
            assert _failing_checks(StripeRefund, values) == []
            _add(session, StripeRefund(**values))

    rows = session.scalars(select(StripeRefund).order_by(StripeRefund.id)).all()
    assert [(row.source_type, row.status) for row in rows] == [
        (source_type, status)
        for source_type in REFUND_SOURCE_TYPES
        for status in STRIPE_REFUND_STATUSES
    ]
    for row in rows:
        active = row.status != STRIPE_REFUND_FAILED
        by_request = row.source_type == SOURCE_REFUND_REQUEST
        expected_request = row.refund_request_id if active and by_request else None
        expected_checkout = row.checkout_id if active and not by_request else None
        assert row.active_refund_request_id == expected_request
        assert row.active_checkout_id == expected_checkout


def test_refund_id_prefix_is_not_checked(session: Session) -> None:
    """验收「核验显示 FPX、GrabPay 的退款 ID 前缀不是 re_，不校验前缀」与
    StripeRefund 一行「Stripe 退款 ID（可空，唯一）」：不以 re_ 开头的 Stripe
    退款 ID 也写得进去。
    """
    sources = _sources(session)
    values = _refund_values(
        sources, SOURCE_CHECKOUT, STRIPE_REFUND_PENDING, stripe_refund_id="pyr_test_000001"
    )
    assert _failing_checks(StripeRefund, values) == []

    row = _add(session, StripeRefund(**values))

    assert row.stripe_refund_id == "pyr_test_000001"


def test_succeeded_attempt_can_become_failed(session: Session) -> None:
    """第 8 条「只接受 pending → succeeded、pending → failed、succeeded →
    failed 三种改动」「Stripe 允许已成功的退款之后变为失败，这时照查询结果改为
    failed、释放额度」：pending 改为 succeeded（写成功时间），再改为 failed
    并清空活动列都写得进去，成功时间保留；释放后同一来源又可新建一条 pending。
    """
    sources = _sources(session)
    row = _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)

    row.status = STRIPE_REFUND_SUCCEEDED
    row.succeeded_at = SUCCEEDED
    session.flush()
    row.status = STRIPE_REFUND_FAILED
    row.active_refund_request_id = None
    session.flush()
    _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)

    session.expire_all()
    reloaded = session.get(StripeRefund, row.id)
    assert reloaded is not None
    assert reloaded.status == STRIPE_REFUND_FAILED
    assert reloaded.succeeded_at == SUCCEEDED


def test_amount_and_call_count_are_integers(session: Session) -> None:
    """「金额以 MYR 的仙（sen）为整数单位运算」、StripeRefund 一行「金额
    （仙）……调用次数」：金额与调用次数读回仍是 int，不经浮点。
    """
    row = _attempt(session, _sources(session), SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)
    session.expire_all()

    reloaded = session.get(StripeRefund, row.id)
    assert reloaded is not None
    assert type(reloaded.amount_sen) is int
    assert reloaded.amount_sen == 1000
    assert type(reloaded.call_count) is int


def test_call_count_defaults_to_zero(session: Session) -> None:
    """第 8 条「同一事务另写一条 pending 的 `StripeRefund` 尝试」「提交后调用
    Stripe 退款接口」与验收「调用次数（非负整数，服务端默认 0）」：批准事务里
    刚写入、尚未调用 Stripe 的尝试不给调用次数时，库里的服务端默认值是 0。
    """
    values = _refund_values(_sources(session), SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)
    del values["call_count"]

    session.execute(insert(StripeRefund).values(**values))

    assert session.scalar(select(StripeRefund.call_count)) == 0


def test_tables_store_only_reconciliation_fields() -> None:
    """StripeEvent 一行「不存原文」、第 7 条「不存、不记录事件原文」与第 10 条
    「本站只存『数据模型』`StripeCheckout` / `StripeEvent` / `StripeRefund` 一行
    所列的对账字段」：两张表的列正是所列的字段，没有事件原文、Stripe 请求与
    响应、客户资料的列。
    """
    event_columns = {column.name for column in StripeEvent.__table__.columns}
    refund_columns = {column.name for column in StripeRefund.__table__.columns}

    assert event_columns == {"id", "event_id", "event_type", "outcome", "processed_at"}
    assert refund_columns == {
        "id",
        "payment_intent_id",
        "source_type",
        "refund_request_id",
        "checkout_id",
        "stripe_refund_id",
        "amount_sen",
        "status",
        "call_count",
        "last_error_type",
        "created_at",
        "succeeded_at",
        # SHOP-TASK-123：HANDOFF 0.54「退款尝试表补告警时间列，『数据模型』一行所说
        # 『错误类别与时间』含此列」。
        "alerted_at",
        "last_checked_at",
        "active_refund_request_id",
        "active_checkout_id",
    }


def test_status_and_source_groups() -> None:
    """StripeRefund 一行「状态（pending / succeeded / failed）」「同一来源至多一条
    不是 failed 的尝试」与「来源（退款申请或迟到付款的会话）」：占用来源的状态
    正是除 failed 外的两种，来源类别只有两种。
    """
    not_failed = set(STRIPE_REFUND_STATUSES) - {STRIPE_REFUND_FAILED}

    assert set(ACTIVE_STRIPE_REFUND_STATUSES) == not_failed
    assert len(STRIPE_REFUND_STATUSES) == 3
    assert set(REFUND_SOURCE_TYPES) == {SOURCE_REFUND_REQUEST, SOURCE_CHECKOUT}


# ---------------------------------------------------------------------------
# 事件表
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["failed", "pending", ""])
def test_unknown_event_outcome_is_rejected(session: Session, outcome: str) -> None:
    """验收「处理结果（processed 或 ignored）」与第 7 条「其他类型回 200 不处理」
    「处理失败整个事务回滚」：处理结果只有已处理与不处理两种，失败的事件随事务
    回滚、不留记录，其他取值被拒。
    """
    values = _event_values(outcome=outcome)
    _assert_check_rejects(session, StripeEvent, "ck_stripe_events_outcome_valid", values)


def test_duplicate_event_id_is_rejected(session: Session) -> None:
    """StripeEvent 一行「Stripe 事件 ID（唯一）」与第 7 条「事件 ID 的去重记录……
    已提交过的事件直接回 200」：同一事件 ID 不能写入第二次，即使类型与处理结果
    都不同。
    """
    _add(session, StripeEvent(**_event_values(event_id="evt_test_dup")))
    values = _event_values(
        event_id="evt_test_dup", event_type="refund.updated", outcome=EVENT_IGNORED
    )
    assert _failing_checks(StripeEvent, values) == []
    message = r"UNIQUE constraint failed: stripe_events\.event_id\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeEvent(**values))


@pytest.mark.parametrize("column", ["event_id", "event_type", "outcome", "processed_at"])
def test_event_required_columns_reject_null(session: Session, column: str) -> None:
    """StripeEvent 一行「Stripe 事件 ID（唯一）、类型、处理结果与时间」：这些列
    写入 NULL 被拒；事件 ID 为空时唯一约束不比较、处理结果为空时检查约束会放行，
    只能靠非空约束拒绝。
    """
    _assert_null_rejected(session, StripeEvent, column, _event_values())


def test_event_has_no_nullable_columns() -> None:
    """StripeEvent 一行没有写明可空的字段：事件表没有可空的列。"""
    assert [column.name for column in StripeEvent.__table__.columns if column.nullable] == []


def test_event_unique_constraints_and_indexes() -> None:
    """StripeEvent 一行「Stripe 事件 ID（唯一）」：事件 ID 有唯一约束，不另建
    索引。
    """
    uniques = [
        [column.name for column in c.columns]
        for c in StripeEvent.__table__.constraints
        if isinstance(c, UniqueConstraint)
    ]

    assert uniques == [["event_id"]]
    assert not StripeEvent.__table__.indexes
    assert not StripeEvent.__table__.foreign_keys


# ---------------------------------------------------------------------------
# 退款尝试的检查约束
# ---------------------------------------------------------------------------

# （守住的设计原句，来源类别，状态，应不成立的约束，覆盖值）。
_CHECK_CASES = [
    (
        "StripeRefund「来源（退款申请或迟到付款的会话）」：来源类别取未知值",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_source_type_valid",
        {"source_type": "order"},
    ),
    (
        "StripeRefund「来源（退款申请或迟到付款的会话）」：来源类别为空串",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_source_type_valid",
        {"source_type": ""},
    ),
    (
        "StripeRefund「Stripe 的 requires_action 记为 pending」：不直接存 requires_action",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_status_valid",
        {"status": "requires_action"},
    ),
    (
        "StripeRefund「canceled 记为 failed」：不直接存 canceled",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_status_valid",
        {"status": "canceled"},
    ),
    (
        "第 8 条「现金退款额为 0……不建尝试、不调用 Stripe」：金额为 0",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_amount_positive",
        {"amount_sen": 0},
    ),
    (
        "第 8 条「本次批准的现金退款额（不含运费）大于 0 时」：金额为负",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_amount_positive",
        {"amount_sen": -100},
    ),
    (
        "验收「调用次数（非负整数……）」：调用次数为负",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_call_count_non_negative",
        {"call_count": -1},
    ),
    (
        "第 8 条「来源为该退款申请」：来源类别为退款申请却缺退款申请",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_refund_request_source",
        {"refund_request_id": None},
    ),
    (
        "第 8 条「来源为该退款申请」：来源类别为退款申请却另带会话记录",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_refund_request_source",
        {"checkout_id": _CHECKOUT},
    ),
    (
        "第 6 条「记为来源为该会话的 `StripeRefund` 尝试」：来源类别为会话却缺会话记录",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_checkout_source",
        {"checkout_id": None},
    ),
    (
        "第 6 条「记为来源为该会话的 `StripeRefund` 尝试」：来源类别为会话却另带退款申请",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_checkout_source",
        {"refund_request_id": _REFUND_REQUEST},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：failed 的退款申请尝试仍占着活动列",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_failed_has_no_active_source",
        {"active_refund_request_id": _REFUND_REQUEST},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：failed 的会话尝试仍占着活动列",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_FAILED,
        "ck_stripe_refunds_failed_has_no_active_source",
        {"active_checkout_id": _CHECKOUT},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：pending 的退款申请尝试活动列为空",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_refund_request_is_source",
        {"active_refund_request_id": None},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：succeeded 的退款申请尝试活动列为空",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_SUCCEEDED,
        "ck_stripe_refunds_active_refund_request_is_source",
        {"active_refund_request_id": None},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：退款申请尝试的活动列指向另一个申请",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_refund_request_is_source",
        {"active_refund_request_id": _OTHER},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：退款申请尝试另占会话的活动列",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_refund_request_is_source",
        {"active_checkout_id": _CHECKOUT},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：pending 的会话尝试活动列为空",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_checkout_is_source",
        {"active_checkout_id": None},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：succeeded 的会话尝试活动列为空",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_SUCCEEDED,
        "ck_stripe_refunds_active_checkout_is_source",
        {"active_checkout_id": None},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：会话尝试的活动列指向另一条会话",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_checkout_is_source",
        {"active_checkout_id": _OTHER},
    ),
    (
        "StripeRefund「只在非 failed 时等于来源的列」：会话尝试另占退款申请的活动列",
        SOURCE_CHECKOUT,
        STRIPE_REFUND_PENDING,
        "ck_stripe_refunds_active_checkout_is_source",
        {"active_refund_request_id": _REFUND_REQUEST},
    ),
    (
        "StripeRefund「创建与成功时间」：succeeded 缺成功时间",
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_SUCCEEDED,
        "ck_stripe_refunds_succeeded_has_succeeded_at",
        {"succeeded_at": None},
    ),
]


@pytest.mark.parametrize(
    ("design", "source_type", "status", "constraint", "overrides"),
    _CHECK_CASES,
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_refund_check_constraints_reject(
    session: Session,
    design: str,
    source_type: str,
    status: str,
    constraint: str,
    overrides: dict[str, Any],
) -> None:
    """退款尝试表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    values = _refund_values(_sources(session), source_type, status, **overrides)
    _assert_check_rejects(session, StripeRefund, constraint, values)


def test_every_refund_check_constraint_has_a_rejection_case() -> None:
    """验收「检查约束逐条保证」：上一条测试的用例覆盖表上的全部检查约束。"""
    covered = {case[3] for case in _CHECK_CASES}

    assert covered == {str(c.name) for c in _check_constraints(StripeRefund)}


@pytest.mark.parametrize(
    "column",
    ["payment_intent_id", "source_type", "amount_sen", "status", "call_count", "created_at"],
)
def test_refund_required_columns_reject_null(session: Session, column: str) -> None:
    """StripeRefund 一行「所属 PaymentIntent、来源……金额（仙）、状态……调用次数
    ……创建与成功时间」与验收「除下面写明可空的列外全部非空」：这些列写入 NULL
    被拒；来源类别、状态、金额或调用次数为空时检查约束会放行，只能靠非空约束
    拒绝。
    """
    values = _refund_values(_sources(session), SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)
    _assert_null_rejected(session, StripeRefund, column, values)


def test_only_designated_refund_columns_are_nullable() -> None:
    """StripeRefund 一行「Stripe 退款 ID（可空，唯一）」与验收列出的其余可空列：
    只有这些列可空。
    """
    actual = {column.name for column in StripeRefund.__table__.columns if column.nullable}

    assert actual == {
        "refund_request_id",
        "checkout_id",
        "stripe_refund_id",
        "last_error_type",
        "succeeded_at",
        # SHOP-TASK-123：告警时间在还没发过会反复出现的告警时为空（HANDOFF 0.54）。
        "alerted_at",
        "last_checked_at",
        "active_refund_request_id",
        "active_checkout_id",
    }


def test_alerted_at_is_nullable_writable_and_outside_constraints(session: Session) -> None:
    """HANDOFF 0.54「退款尝试表补告警时间列……它只给会反复出现的告警（24 小时后
    对账找不到或列不完、Stripe 退款对象与尝试不符）去重，这类告警每条尝试至多
    一次」：不给告警时间时读回为空（还没告警过）；两种来源、三种状态的尝试都能
    写入告警时间并原样读回，求值辅助函数不报任何约束不成立；没有检查约束、唯一
    约束或索引引用这一列，所以它不改变现有约束——带告警时间的第二条不是 failed
    的尝试仍被同一来源的活动列唯一约束拒绝。
    """
    alerted = CREATED + timedelta(hours=24, minutes=1)
    sources = _sources(session)
    plain = _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)

    alerted_ids: list[int] = []
    for source_type in REFUND_SOURCE_TYPES:
        for status in STRIPE_REFUND_STATUSES:
            values = _refund_values(_sources(session), source_type, status, alerted_at=alerted)
            assert _failing_checks(StripeRefund, values) == []
            alerted_ids.append(_add(session, StripeRefund(**values)).id)

    session.expire_all()
    reloaded = session.get(StripeRefund, plain.id)
    assert reloaded is not None
    assert reloaded.alerted_at is None
    for row_id in alerted_ids:
        row = session.get(StripeRefund, row_id)
        assert row is not None
        assert row.alerted_at == alerted

    table = StripeRefund.__table__
    for constraint in _check_constraints(StripeRefund):
        assert "alerted_at" not in str(constraint.sqltext)
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            assert "alerted_at" not in [column.name for column in constraint.columns]
    for index in table.indexes:
        assert "alerted_at" not in [column.name for column in index.columns]

    values = _refund_values(
        sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING, alerted_at=alerted
    )
    assert _failing_checks(StripeRefund, values) == []
    message = r"UNIQUE constraint failed: stripe_refunds\.active_refund_request_id\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeRefund(**values))


# ---------------------------------------------------------------------------
# 退款尝试的唯一约束
# ---------------------------------------------------------------------------

_ACTIVE_COLUMN = {
    SOURCE_REFUND_REQUEST: "active_refund_request_id",
    SOURCE_CHECKOUT: "active_checkout_id",
}


@pytest.mark.parametrize("source_type", REFUND_SOURCE_TYPES)
@pytest.mark.parametrize(
    ("existing", "second"),
    [
        (STRIPE_REFUND_PENDING, STRIPE_REFUND_PENDING),
        (STRIPE_REFUND_SUCCEEDED, STRIPE_REFUND_PENDING),
        (STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED),
    ],
)
def test_second_active_attempt_for_same_source_is_rejected(
    session: Session, source_type: str, existing: str, second: str
) -> None:
    """第 8 条「同一来源至多一条不是 failed 的尝试，由数据库唯一约束保证」与
    「重试是新建一条尝试（受上述唯一约束，重复点击与并发只有一个成功）」：同一
    退款申请（或同一会话）已有一条不是 failed 的尝试时，再写一条不是 failed 的
    尝试被对应活动列的唯一约束拒绝。
    """
    sources = _sources(session)
    _attempt(session, sources, source_type, existing)
    values = _refund_values(sources, source_type, second)
    assert _failing_checks(StripeRefund, values) == []
    message = rf"UNIQUE constraint failed: stripe_refunds\.{_ACTIVE_COLUMN[source_type]}\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeRefund(**values))


@pytest.mark.parametrize("source_type", REFUND_SOURCE_TYPES)
def test_failed_attempts_do_not_block_a_new_one(session: Session, source_type: str) -> None:
    """第 8 条「最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试」与
    StripeRefund「只在非 failed 时等于来源的列」：同一来源两条 failed 加一条
    pending 可以并存；把 pending 改为 failed、清空活动列后，同一来源又可写一条
    pending。
    """
    sources = _sources(session)
    _attempt(session, sources, source_type, STRIPE_REFUND_FAILED)
    _attempt(session, sources, source_type, STRIPE_REFUND_FAILED)
    current = _attempt(session, sources, source_type, STRIPE_REFUND_PENDING)

    current.status = STRIPE_REFUND_FAILED
    setattr(current, _ACTIVE_COLUMN[source_type], None)
    session.flush()
    _attempt(session, sources, source_type, STRIPE_REFUND_PENDING)

    statuses = session.scalars(select(StripeRefund.status).order_by(StripeRefund.id)).all()
    assert statuses == [STRIPE_REFUND_FAILED] * 3 + [STRIPE_REFUND_PENDING]


def test_active_attempts_of_different_sources_coexist(session: Session) -> None:
    """第 8 条「同一来源至多一条」：唯一只按来源，不同退款申请、不同会话，以及
    同一订单上的一条退款申请与一条会话，各有一条 pending 互不影响。
    """
    first = _sources(session)
    second = _sources(session)
    for sources in (first, second):
        for source_type in REFUND_SOURCE_TYPES:
            _attempt(session, sources, source_type, STRIPE_REFUND_PENDING)

    assert len(session.scalars(select(StripeRefund)).all()) == 4


def test_duplicate_stripe_refund_id_is_rejected(session: Session) -> None:
    """StripeRefund 一行「Stripe 退款 ID（可空，唯一）」与第 7 条「第 8 条按退款
    记录」去重：同一 Stripe 退款 ID 不能出现在第二条尝试上；第二条是另一组来源
    上的 failed 尝试，被拒只因这一列重复。
    """
    _attempt(
        session,
        _sources(session),
        SOURCE_REFUND_REQUEST,
        STRIPE_REFUND_PENDING,
        stripe_refund_id="re_test_dup",
    )
    values = _refund_values(
        _sources(session), SOURCE_CHECKOUT, STRIPE_REFUND_FAILED, stripe_refund_id="re_test_dup"
    )
    assert _failing_checks(StripeRefund, values) == []
    message = r"UNIQUE constraint failed: stripe_refunds\.stripe_refund_id\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeRefund(**values))


def test_null_stripe_refund_ids_do_not_collide(session: Session) -> None:
    """StripeRefund 一行「Stripe 退款 ID（可空，唯一）」与第 8 条「还没有 Stripe
    退款 ID（调用结果未知或可重试的错误）的」：空值不参与唯一比较，多条还没有
    Stripe 退款 ID 的尝试（同一来源的 failed 与 pending）可以并存。
    """
    sources = _sources(session)
    _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_FAILED)
    _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_FAILED)
    _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING, stripe_refund_id=None)

    assert len(session.scalars(select(StripeRefund)).all()) == 3


def test_attempts_of_same_payment_intent_coexist(session: Session) -> None:
    """第 8 条「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款
    金额……这同时约束第 6 条的全额退款与本条的部分退款」：同一 PaymentIntent 可以
    有多条尝试（不同来源），PaymentIntent 不唯一；合计由规则任务在锁住付款的
    事务里核对。
    """
    sources = _sources(session)
    other = _sources(session)
    other.payment_intent_id = sources.payment_intent_id
    _attempt(session, sources, SOURCE_REFUND_REQUEST, STRIPE_REFUND_SUCCEEDED)
    _attempt(session, other, SOURCE_REFUND_REQUEST, STRIPE_REFUND_PENDING)

    intents = session.scalars(select(StripeRefund.payment_intent_id)).all()
    assert intents == [sources.payment_intent_id] * 2


def test_refund_unique_constraints_and_indexes() -> None:
    """验收「两个活动列各建唯一约束……按 PaymentIntent ID 与（状态、创建时间）建
    索引」与 StripeRefund 一行「Stripe 退款 ID（可空，唯一）」：唯一约束与普通
    索引都在。
    """
    uniques = sorted(
        [column.name for column in c.columns]
        for c in StripeRefund.__table__.constraints
        if isinstance(c, UniqueConstraint)
    )
    indexes = {index.name: index for index in StripeRefund.__table__.indexes}

    assert uniques == [["active_checkout_id"], ["active_refund_request_id"], ["stripe_refund_id"]]
    expected = {
        "ix_stripe_refunds_payment_intent_id": ["payment_intent_id"],
        "ix_stripe_refunds_status_created_at": ["status", "created_at"],
    }
    assert {name: [c.name for c in index.columns] for name, index in indexes.items()} == expected
    assert not any(index.unique for index in indexes.values())


# ---------------------------------------------------------------------------
# 退款尝试的外键
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_type", "overrides"),
    [
        (
            SOURCE_REFUND_REQUEST,
            {"refund_request_id": _OTHER, "active_refund_request_id": _OTHER},
        ),
        (SOURCE_CHECKOUT, {"checkout_id": _OTHER, "active_checkout_id": _OTHER}),
    ],
    ids=["refund_request_id", "checkout_id"],
)
def test_attempt_must_reference_existing_source(
    session: Session, source_type: str, overrides: dict[str, Any]
) -> None:
    """StripeRefund 一行「来源（退款申请或迟到付款的会话）及其 ID」：尝试必须指向
    存在的退款申请或会话记录；活动列与来源列一起改成不存在的 ID，检查约束成立，
    被拒只因外键。
    """
    values = _refund_values(_sources(session), source_type, STRIPE_REFUND_PENDING, **overrides)
    assert _failing_checks(StripeRefund, values) == []

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, StripeRefund(**values))


@pytest.mark.parametrize("source_type", REFUND_SOURCE_TYPES)
def test_deleting_source_referenced_by_attempt_is_rejected(
    session: Session, source_type: str
) -> None:
    """「资料保留」与 StripeRefund「Stripe 测试环境的对账记录」：被退款尝试引用的
    退款申请与会话记录不能物理删除，外键 RESTRICT 拒绝，不级联删掉对账记录。用
    failed 尝试（活动列为空），删除被拒只因来源列的外键。
    """
    sources = _sources(session)
    _attempt(session, sources, source_type, STRIPE_REFUND_FAILED)
    model, row_id = {
        SOURCE_REFUND_REQUEST: (RefundRequest, sources.refund_request_id),
        SOURCE_CHECKOUT: (StripeCheckout, sources.checkout_id),
    }[source_type]

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(model).where(model.id == row_id))


def test_unreferenced_sources_can_be_deleted(session: Session) -> None:
    """对照上一条：没有退款尝试的退款申请与会话记录删除不受这张表的外键限制，
    拒绝确实来自尝试的引用。
    """
    sources = _sources(session)

    session.execute(delete(RefundRequest).where(RefundRequest.id == sources.refund_request_id))
    session.execute(delete(StripeCheckout).where(StripeCheckout.id == sources.checkout_id))

    assert session.scalars(select(RefundRequest)).all() == []
    assert session.scalars(select(StripeCheckout)).all() == []


def test_refund_foreign_keys_are_restrict() -> None:
    """验收「退款申请（外键 refund_requests，可空）、来源会话记录（外键
    stripe_checkouts，可空）」与外键一律 RESTRICT：退款尝试只有这两个外键，写明
    为 RESTRICT；两个活动列与 active_order_id 一样不建外键。
    """
    foreign_keys = list(StripeRefund.__table__.foreign_keys)
    targets = sorted((fk.parent.name, fk.column.table.name) for fk in foreign_keys)

    assert targets == [
        ("checkout_id", "stripe_checkouts"),
        ("refund_request_id", "refund_requests"),
    ]
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys)
    columns = StripeRefund.__table__.columns
    assert not columns["active_refund_request_id"].foreign_keys
    assert not columns["active_checkout_id"].foreign_keys

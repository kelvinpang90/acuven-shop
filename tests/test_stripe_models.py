"""Stripe 会话记录表与站点设置支付方式列的数据库约束。

依据 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的 SiteSetting 一行
（支付方式：模拟 / Stripe 测试，默认模拟）与 StripeCheckout / StripeEvent /
StripeRefund 一行，「Stripe 测试环境支付」第 2、3、4、10 条，以及
docs/HANDOFF.md 0.53 记录的 Kelvin 2026-10-15 落实决定（核对不符的已付款
会话标为 expired、释放订单的活动会话位）。每条测试（参数化的测试是每个
用例）写明它守住的设计或决定原句；只出自本任务验收标准的约束注明「验收：」。

先写入合法的会话记录（四种状态各一条，分属不同订单）作对照，再逐个写
反例。检查约束的反例先在独立的 SQLite 连接上逐条求值该表的全部检查约束，
断言目标约束确实不成立，再断言写入被拒、且报出的是不成立的约束之一。
非空约束的反例用 Core insert 显式写入 NULL：ORM 会略过值为 None 的列、
改用默认值。用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接
都须打开。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
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
from app.models import Order, SiteSetting, StripeCheckout
from app.models.order import STATUS_AWAITING_PAYMENT
from app.models.site import PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    ACTIVE_CHECKOUT_STATUSES,
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    CHECKOUT_STATUSES,
    CURRENCY_MYR,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 15, 2, 0)
# 订单支付到期为创建加 15 分钟；会话的 Stripe 到期取它与当前时间加 31 分钟中较晚者。
EXPIRES = CREATED + timedelta(minutes=31)
COMPLETED = CREATED + timedelta(minutes=5)
CONFIRMED = CREATED + timedelta(minutes=6)
NOW = CREATED
FINGERPRINT = "0f" * 32
# 发给 Stripe 的表单参数快照；customer_email 是站点配置的固定演示邮箱，不是客户资料。
CREATE_PARAMS = (
    "mode=payment&currency=myr&line_items[0][price_data][currency]=myr"
    "&line_items[0][price_data][unit_amount]=6800"
    "&line_items[0][price_data][product_data][name]=Demo+order&line_items[0][quantity]=1"
    "&client_reference_id=1&metadata[order_id]=1&metadata[idempotency_key]=checkout-key"
    "&locale=en&customer_email=demo%40example.com"
    "&success_url=https%3A%2F%2Fshop.example.com%2Fpay%2Fresult"
    "&cancel_url=https%3A%2F%2Fshop.example.com%2Fpay&expires_at=1792030260"
)

# 覆盖值里的占位：写入时换成本记录的订单 ID 与一个不同的订单 ID。
_SAME_ORDER = "<same order>"
_OTHER_ORDER = "<other order>"

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
_CHECKOUT_SEQUENCE = itertools.count(1)


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


def _resolve(order_id: int, value: Any) -> Any:
    if value == _SAME_ORDER:
        return order_id
    if value == _OTHER_ORDER:
        return order_id + 1000
    return value


def _checkout_values(order_id: int, status: str, **overrides: Any) -> dict[str, Any]:
    """该状态一条合法的会话记录；每次给不同的幂等键、会话 ID 与 PaymentIntent ID。"""
    n = next(_CHECKOUT_SEQUENCE)
    session_id = f"cs_test_{n:06d}"
    creating = status == CHECKOUT_CREATING
    complete = status == CHECKOUT_COMPLETE
    if creating:
        payment_status = None
    else:
        payment_status = PAYMENT_PAID if complete else PAYMENT_UNPAID
    values: dict[str, Any] = {
        "order_id": order_id,
        "idempotency_key": f"checkout-key-{n}",
        "checkout_session_id": None if creating else session_id,
        "payment_intent_id": f"pi_test_{n:06d}" if complete else None,
        "amount_sen": 6800,
        "currency": CURRENCY_MYR,
        "status": status,
        "payment_status": payment_status,
        "payment_method_type": "card" if complete else None,
        "checkout_url": None if creating else f"https://checkout.stripe.com/c/pay/{session_id}",
        "create_params": CREATE_PARAMS,
        "expires_at": EXPIRES,
        "created_at": CREATED,
        "completed_at": COMPLETED if complete else None,
        "confirmed_at": CONFIRMED if complete else None,
        "alerted_at": None,
        "last_polled_at": None,
        "active_order_id": None if status == CHECKOUT_EXPIRED else order_id,
    }
    return values | {key: _resolve(order_id, value) for key, value in overrides.items()}


def _checkout(session: Session, order_id: int, status: str, **overrides: Any) -> StripeCheckout:
    return _add(session, StripeCheckout(**_checkout_values(order_id, status, **overrides)))


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


def _site_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": 1,
        "sms_verification_enabled": False,
        "payment_method": PAYMENT_METHOD_SIMULATED,
        "updated_at": NOW,
    }
    return values | overrides


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_checkouts_in_every_status_are_accepted(session: Session) -> None:
    """StripeCheckout 一行「状态（creating / open / complete / expired）与付款
    状态」、第 3 条「先……写一条 `creating` 状态的 `StripeCheckout`」「成功后
    ……改为 `open`」与第 4 条「记下 PaymentIntent ID，以及……付款方式类型」：
    四种状态各一条（分属不同订单）都写得进去；creating 尚无会话 ID、付款页
    地址与付款状态，complete 带 PaymentIntent、付款方式类型、完成与确认
    时间；不是 expired 的三条 active_order_id 等于订单，expired 的为空。
    这是下面各条拒绝测试的对照：同样的建表与外键检查下，合法数据写得进去，
    且求值辅助函数对每种状态都不报任何约束不成立。
    """
    for status in CHECKOUT_STATUSES:
        order = _order(session)
        values = _checkout_values(order.id, status)
        assert _failing_checks(StripeCheckout, values) == []
        _add(session, StripeCheckout(**values))

    rows = session.scalars(select(StripeCheckout).order_by(StripeCheckout.id)).all()
    assert [row.status for row in rows] == list(CHECKOUT_STATUSES)
    assert len({row.order_id for row in rows}) == 4
    for row in rows:
        expected = None if row.status == CHECKOUT_EXPIRED else row.order_id
        assert row.active_order_id == expected
    creating = rows[0]
    assert creating.checkout_session_id is None
    assert creating.checkout_url is None
    assert creating.payment_status is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"payment_status": PAYMENT_PAID, "completed_at": COMPLETED},
            id="paid-with-completed-at",
        ),
        pytest.param(
            {"payment_status": PAYMENT_PAID, "payment_intent_id": "pi_test_late"},
            id="paid-with-payment-intent",
        ),
        pytest.param(
            {"checkout_session_id": None, "checkout_url": None, "payment_status": None},
            id="never-created",
        ),
    ],
)
def test_expired_checkout_variants_are_accepted(
    session: Session, overrides: dict[str, Any]
) -> None:
    """Kelvin 2026-10-15（HANDOFF 0.53）「Stripe 会话已付款但……核对不符而
    订单仍为待支付时，写完来源为该会话的退款尝试后把这条会话记录标为
    expired、释放订单的活动会话位」，与第 3 条「Stripe 以参数不合格……拒绝，
    ……说明原请求没有建成会话，记为 expired」：expired 的记录可以带付款状态
    paid、完成时间与 PaymentIntent，也可以没有会话 ID 与付款页地址。这是
    「确认时间非空而状态不是 complete」被拒的对照。
    """
    order = _order(session)
    values = _checkout_values(order.id, CHECKOUT_EXPIRED, **overrides)
    assert _failing_checks(StripeCheckout, values) == []

    row = _add(session, StripeCheckout(**values))

    assert row.status == CHECKOUT_EXPIRED
    assert row.active_order_id is None


def test_amount_is_integer_sen(session: Session) -> None:
    """「金额以 MYR 的仙（sen）为整数单位运算」与 StripeCheckout 一行「金额
    （仙）」：金额读回仍是 int，不经浮点。
    """
    order = _order(session)
    row = _checkout(session, order.id, CHECKOUT_OPEN)
    session.expire_all()

    reloaded = session.get(StripeCheckout, row.id)
    assert reloaded is not None
    assert type(reloaded.amount_sen) is int
    assert reloaded.amount_sen == 6800


# ---------------------------------------------------------------------------
# 检查约束
# ---------------------------------------------------------------------------

# （守住的设计原句，记录的状态，应不成立的约束，覆盖值）。
_CHECK_CASES = [
    (
        "StripeCheckout「状态（creating / open / complete / expired）」：未知状态",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_status_valid",
        {"status": "pending"},
    ),
    (
        "StripeCheckout「与付款状态」（Stripe 的 unpaid、paid、no_payment_required）：未知付款状态",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_payment_status_valid",
        {"payment_status": "failed"},
    ),
    (
        "第 3 条「币种 MYR」：币种不是 myr",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_currency_myr",
        {"currency": "usd"},
    ),
    (
        "第 3 条「金额等于订单应付快照」：金额为 0",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_amount_positive",
        {"amount_sen": 0},
    ),
    (
        "第 3 条「金额等于订单应付快照」：金额为负",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_amount_positive",
        {"amount_sen": -100},
    ),
    (
        "第 3 条「expired 时为空的列」：expired 仍占着订单",
        CHECKOUT_EXPIRED,
        "ck_stripe_checkouts_expired_has_no_active_order",
        {"active_order_id": _SAME_ORDER},
    ),
    (
        "第 3 条「只在 creating、open、complete 时等于订单 ID」：open 记录的 active_order_id 为空",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_active_order_is_order",
        {"active_order_id": None},
    ),
    (
        "第 3 条「只在 creating、open、complete 时等于订单 ID」：creating 记录的活动列为空",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_active_order_is_order",
        {"active_order_id": None},
    ),
    (
        "第 3 条「只在 creating、open、complete 时等于订单 ID」：complete 记录的活动列为空",
        CHECKOUT_COMPLETE,
        "ck_stripe_checkouts_active_order_is_order",
        {"active_order_id": None},
    ),
    (
        "第 3 条「只在 creating、open、complete 时等于订单 ID」：活动列指向另一张订单",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_active_order_is_order",
        {"active_order_id": _OTHER_ORDER},
    ),
    (
        "第 4 条「全部相符且订单仍为 `awaiting_demo_payment` 时……进入"
        " `demo_paid`」：确认时间非空而状态为 open",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_confirmed_is_complete_paid",
        {"confirmed_at": CONFIRMED},
    ),
    (
        "第 6 条与 HANDOFF 0.53：迟到或不符的付款记为 expired、不确认订单，expired 带确认时间",
        CHECKOUT_EXPIRED,
        "ck_stripe_checkouts_confirmed_is_complete_paid",
        {
            "payment_status": PAYMENT_PAID,
            "completed_at": COMPLETED,
            "confirmed_at": CONFIRMED,
        },
    ),
    (
        "第 4 条「`payment_status` 为 `paid`」：付款状态为 unpaid 却有确认时间",
        CHECKOUT_COMPLETE,
        "ck_stripe_checkouts_confirmed_is_complete_paid",
        {"payment_status": PAYMENT_UNPAID},
    ),
    (
        "第 4 条「`payment_status` 为 `paid`」：付款状态为空却有确认时间",
        CHECKOUT_COMPLETE,
        "ck_stripe_checkouts_confirmed_is_complete_paid",
        {"payment_status": None},
    ),
    (
        "第 3 条「成功后……写回会话 ID 与付款页地址」：creating 带会话 ID",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_creating_has_no_session",
        {"checkout_session_id": "cs_test_creating"},
    ),
    (
        "第 3 条「成功后……写回会话 ID 与付款页地址」：creating 带付款页地址",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_creating_has_no_session",
        {"checkout_url": "https://checkout.stripe.com/c/pay/cs_test_creating"},
    ),
    (
        "第 3 条「成功后……写回会话 ID 与付款页地址」：creating 带付款状态",
        CHECKOUT_CREATING,
        "ck_stripe_checkouts_creating_has_no_session",
        {"payment_status": PAYMENT_UNPAID},
    ),
    (
        "第 3 条「写回会话 ID 与付款页地址……改为 `open`」：open 缺付款页地址",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_open_has_session_and_url",
        {"checkout_url": None},
    ),
    (
        "第 3 条「写回会话 ID 与付款页地址……改为 `open`」：open 缺会话 ID",
        CHECKOUT_OPEN,
        "ck_stripe_checkouts_open_has_session_and_url",
        {"checkout_session_id": None},
    ),
    (
        "StripeCheckout「Checkout 会话 ID」与第 3 条 webhook 按元数据对上记录：complete 缺会话 ID",
        CHECKOUT_COMPLETE,
        "ck_stripe_checkouts_complete_has_session_and_time",
        {"checkout_session_id": None},
    ),
    (
        "StripeCheckout「创建与完成时间」与第 5 条「会话完成 24 小时后」：complete 缺完成时间",
        CHECKOUT_COMPLETE,
        "ck_stripe_checkouts_complete_has_session_and_time",
        {"completed_at": None},
    ),
]


@pytest.mark.parametrize(
    ("design", "status", "constraint", "overrides"),
    _CHECK_CASES,
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_checkout_check_constraints_reject(
    session: Session, design: str, status: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """会话记录表每个检查约束至少一个反例；守住的设计或决定原句见每个用例的
    design。
    """
    assert design
    order = _order(session)
    values = _checkout_values(order.id, status, **overrides)
    _assert_check_rejects(session, StripeCheckout, constraint, values)


def test_every_checkout_check_constraint_has_a_rejection_case() -> None:
    """验收「检查约束逐条保证」：上一条测试的用例覆盖表上的全部检查约束。"""
    covered = {case[2] for case in _CHECK_CASES}

    assert covered == {str(c.name) for c in _check_constraints(StripeCheckout)}


@pytest.mark.parametrize(
    "column",
    [
        "order_id",
        "idempotency_key",
        "amount_sen",
        "currency",
        "status",
        "create_params",
        "expires_at",
        "created_at",
    ],
)
def test_checkout_required_columns_reject_null(session: Session, column: str) -> None:
    """StripeCheckout 一行「所属订单、本站创建幂等键（唯一）……金额（仙）、
    币种、状态……创建参数快照（金额、语言、返回地址、Stripe 到期时间）、
    创建与完成时间」与验收「除下面写明可空的列外全部非空」：这些列写入
    NULL 被拒；状态、币种或金额为空时检查约束会放行，只能靠非空约束拒绝。
    """
    order = _order(session)
    values = _checkout_values(order.id, CHECKOUT_CREATING)
    _assert_null_rejected(session, StripeCheckout, column, values)


def test_only_designated_checkout_columns_are_nullable() -> None:
    """StripeCheckout 一行「Checkout 会话 ID（可空，唯一）、PaymentIntent ID
    （可空，唯一）……实际付款方式类型（可空）」与验收列出的其余可空列：
    只有这些列可空。
    """
    actual = {column.name for column in StripeCheckout.__table__.columns if column.nullable}

    assert actual == {
        "checkout_session_id",
        "payment_intent_id",
        "payment_status",
        "payment_method_type",
        "checkout_url",
        "completed_at",
        "confirmed_at",
        "alerted_at",
        "last_polled_at",
        "active_order_id",
    }


def test_active_statuses_are_all_but_expired() -> None:
    """第 2 条「不是 expired 的 Stripe 记录（creating、open 或 complete）」：
    占用订单的状态正是除 expired 外的三种。
    """
    assert set(ACTIVE_CHECKOUT_STATUSES) == set(CHECKOUT_STATUSES) - {CHECKOUT_EXPIRED}
    assert len(CHECKOUT_STATUSES) == 4


# ---------------------------------------------------------------------------
# 唯一约束
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "second"),
    [
        (CHECKOUT_CREATING, CHECKOUT_CREATING),
        (CHECKOUT_OPEN, CHECKOUT_CREATING),
        (CHECKOUT_COMPLETE, CHECKOUT_OPEN),
        (CHECKOUT_CREATING, CHECKOUT_COMPLETE),
    ],
)
def test_second_active_checkout_for_same_order_is_rejected(
    session: Session, existing: str, second: str
) -> None:
    """第 3 条「同一订单至多一条不是 expired 的记录，由数据库唯一约束保证」
    与第 2 条「订单只要有一条不是 expired 的 Stripe 记录……不另建会话」：
    已有一条不是 expired 的记录时，同一订单再写一条不是 expired 的记录被
    active_order_id 的唯一约束拒绝。
    """
    order = _order(session)
    _checkout(session, order.id, existing)
    values = _checkout_values(order.id, second)
    assert _failing_checks(StripeCheckout, values) == []
    message = r"UNIQUE constraint failed: stripe_checkouts\.active_order_id\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeCheckout(**values))


def test_expired_checkouts_do_not_block_a_new_one(session: Session) -> None:
    """第 2 条「没有 Stripe 记录（或只有 expired 记录）时」与第 3 条「expired
    时为空」：同一订单两条 expired 加一条 open 可以并存；把 open 记为
    expired、清空活动列（HANDOFF 0.53「释放订单的活动会话位」）后，同一
    订单又可写一条 creating。
    """
    order = _order(session)
    _checkout(session, order.id, CHECKOUT_EXPIRED)
    _checkout(
        session,
        order.id,
        CHECKOUT_EXPIRED,
        payment_status=PAYMENT_PAID,
        completed_at=COMPLETED,
    )
    current = _checkout(session, order.id, CHECKOUT_OPEN)

    current.status = CHECKOUT_EXPIRED
    current.active_order_id = None
    session.flush()
    _checkout(session, order.id, CHECKOUT_CREATING)

    statuses = session.scalars(
        select(StripeCheckout.status)
        .where(StripeCheckout.order_id == order.id)
        .order_by(StripeCheckout.id)
    ).all()
    assert statuses == [CHECKOUT_EXPIRED, CHECKOUT_EXPIRED, CHECKOUT_EXPIRED, CHECKOUT_CREATING]


def test_active_checkouts_of_different_orders_coexist(session: Session) -> None:
    """第 3 条「同一订单至多一条」：唯一只按订单，不同订单各有一条 open
    互不影响。
    """
    first = _order(session)
    second = _order(session)
    _checkout(session, first.id, CHECKOUT_OPEN)
    _checkout(session, second.id, CHECKOUT_OPEN)

    assert len(session.scalars(select(StripeCheckout)).all()) == 2


@pytest.mark.parametrize(
    ("column", "status", "value"),
    [
        ("idempotency_key", CHECKOUT_CREATING, "checkout-key-dup"),
        ("checkout_session_id", CHECKOUT_OPEN, "cs_test_dup"),
        ("payment_intent_id", CHECKOUT_COMPLETE, "pi_test_dup"),
    ],
)
def test_duplicate_stripe_identifiers_are_rejected(
    session: Session, column: str, status: str, value: str
) -> None:
    """StripeCheckout 一行「本站创建幂等键（唯一）、Checkout 会话 ID（可空，
    唯一）、PaymentIntent ID（可空，唯一）」与第 7 条「第 4 条按会话与
    PaymentIntent」去重：同一幂等键、会话 ID 或 PaymentIntent ID 不能出现在
    第二条记录上；第二条是另一张订单上的 expired 记录，被拒只因这一列重复。
    """
    _checkout(session, _order(session).id, status, **{column: value})
    other = _order(session)
    values = _checkout_values(
        other.id,
        CHECKOUT_EXPIRED,
        payment_status=PAYMENT_PAID,
        completed_at=COMPLETED,
        **{column: value},
    )
    assert _failing_checks(StripeCheckout, values) == []
    message = rf"UNIQUE constraint failed: stripe_checkouts\.{column}\b"

    with pytest.raises(IntegrityError, match=message):
        _add(session, StripeCheckout(**values))


def test_null_stripe_identifiers_do_not_collide(session: Session) -> None:
    """StripeCheckout 一行「Checkout 会话 ID（可空，唯一）、PaymentIntent ID
    （可空，唯一）」：空值不参与唯一比较，多条 creating 记录（分属不同
    订单）都没有会话 ID 与 PaymentIntent 也可并存。
    """
    for _ in range(3):
        _checkout(session, _order(session).id, CHECKOUT_CREATING)

    assert len(session.scalars(select(StripeCheckout)).all()) == 3


def test_checkout_unique_constraints_and_indexes() -> None:
    """验收「对 active_order_id 建唯一约束……按订单、（状态、Stripe 到期
    时间）与（状态、上次查询时间）建索引」与 StripeCheckout 一行的三个
    「唯一」：唯一约束与普通索引都在。
    """
    uniques = sorted(
        [column.name for column in c.columns]
        for c in StripeCheckout.__table__.constraints
        if isinstance(c, UniqueConstraint)
    )
    indexes = {index.name: index for index in StripeCheckout.__table__.indexes}

    assert uniques == [
        ["active_order_id"],
        ["checkout_session_id"],
        ["idempotency_key"],
        ["payment_intent_id"],
    ]
    expected = {
        "ix_stripe_checkouts_order_id": ["order_id"],
        "ix_stripe_checkouts_status_expires_at": ["status", "expires_at"],
        "ix_stripe_checkouts_status_last_polled_at": ["status", "last_polled_at"],
    }
    assert {name: [c.name for c in index.columns] for name, index in indexes.items()} == expected
    assert not any(index.unique for index in indexes.values())


# ---------------------------------------------------------------------------
# 外键
# ---------------------------------------------------------------------------


def test_checkout_must_reference_existing_order(session: Session) -> None:
    """StripeCheckout 一行「所属订单」：记录必须指向存在的订单；活动列与
    订单列一起改成不存在的订单，检查约束成立，被拒只因外键。
    """
    _order(session)
    values = _checkout_values(999, CHECKOUT_CREATING)
    assert _failing_checks(StripeCheckout, values) == []

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, StripeCheckout(**values))


def test_deleting_order_referenced_by_checkout_is_rejected(session: Session) -> None:
    """「资料保留」与 StripeCheckout「Stripe 测试环境的对账记录」：被会话
    记录引用的订单不能物理删除，外键 RESTRICT 拒绝，不级联删掉对账记录。
    用 expired 记录（活动列为空），删除被拒只因订单列的外键。
    """
    order = _order(session)
    _checkout(session, order.id, CHECKOUT_EXPIRED)

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(Order).where(Order.id == order.id))


def test_unreferenced_order_can_be_deleted(session: Session) -> None:
    """对照上一条：没有会话记录的订单删除不受这张表的外键限制，拒绝确实
    来自会话记录的引用。
    """
    order = _order(session)

    session.execute(delete(Order).where(Order.id == order.id))

    assert session.scalars(select(Order)).all() == []


def test_foreign_keys_are_restrict() -> None:
    """验收「外键删除行为一律 RESTRICT」与「它不另建外键」：会话记录只有
    订单列一个外键，写明为 RESTRICT；active_order_id 不建外键。
    """
    foreign_keys = list(StripeCheckout.__table__.foreign_keys)
    targets = [(fk.parent.name, fk.column.table.name) for fk in foreign_keys]

    assert targets == [("order_id", "orders")]
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys)
    assert not StripeCheckout.__table__.columns["active_order_id"].foreign_keys


# ---------------------------------------------------------------------------
# 站点设置的支付方式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", [PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST])
def test_site_payment_method_accepts_both_values(session: Session, method: str) -> None:
    """SiteSetting 一行「支付方式（模拟 / Stripe 测试，默认模拟）」：两种取值
    都写得进去，是下一条反例的对照。
    """
    values = _site_values(payment_method=method)
    assert _failing_checks(SiteSetting, values) == []

    session.execute(insert(SiteSetting).values(**values))

    assert session.scalar(select(SiteSetting.payment_method)) == method


@pytest.mark.parametrize("method", ["stripe", "card", ""])
def test_site_unknown_payment_method_is_rejected(session: Session, method: str) -> None:
    """第 2 条「`SiteSetting` 的支付方式取『模拟』（默认）或『Stripe 测试』」：
    其他取值被拒。
    """
    values = _site_values(payment_method=method)
    _assert_check_rejects(session, SiteSetting, "ck_site_settings_payment_method_valid", values)


def test_site_payment_method_defaults_to_simulated(session: Session) -> None:
    """SiteSetting 一行「支付方式（模拟 / Stripe 测试，默认模拟）」：不给支付
    方式时，库里的服务端默认值是 simulated（迁移 0020 也靠它给已有的那一行
    补值）。
    """
    session.execute(insert(SiteSetting).values(id=1, updated_at=NOW))

    assert session.scalar(select(SiteSetting.payment_method)) == PAYMENT_METHOD_SIMULATED


def test_site_payment_method_rejects_null(session: Session) -> None:
    """SiteSetting 一行「支付方式（模拟 / Stripe 测试……）」只有两种取值：写入
    NULL 被拒，没有「未知」的第三种状态；取值为空时检查约束会放行，只能靠
    非空约束拒绝。
    """
    _assert_null_rejected(session, SiteSetting, "payment_method", _site_values())

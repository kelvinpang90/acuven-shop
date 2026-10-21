"""后台退款详情的 Stripe 付款与退款尝试（SHOP-TASK-130）。

覆盖 GET /api/admin/refunds/{refund_id} 的 paid_via_stripe、payment_method_type 与
stripe_refunds（app/api/admin_refunds.py 与 app/services/admin_refunds.py）。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 8 条（「后台退款详情
显示各次尝试的 Stripe 退款状态；最新一次尝试为 failed 时管理员可手动重试」；「后台退款
详情按记下的实际付款方式类型显示期限提示」；「经模拟支付的订单退款不变」）与第 10 条
（「本站不读取、不保存 Stripe 返回的客户资料、卡号片段或账单地址」），以及 docs/UX.md
0.11 的 A03「Stripe 测试（0.11）」与 docs/HANDOFF.md 0.55。每条测试（参数化的测试是每个
用例）的文档字符串写明它守住的设计或 UX 原句；只出自 SHOP-TASK-130 验收标准的约定注明
「验收：」。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）；引擎、夹具与
后台登录照 tests/test_admin_refunds.py。订单、退款申请照该文件直接写库；Stripe 付款的会话
记录与退款尝试照 tests/test_stripe_refund_approval.py 的 _stripe_paid 直接按模型写入。本任务
不调用 Stripe。SQLite 不生成 FOR UPDATE：是否加锁以 MySQL 方言编译的语句检查（照
tests/test_admin_refund_points.py）。
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Select, create_engine, event, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Order,
    OrderItem,
    OrderItemUnit,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import ACTOR_GUEST, STATUS_SHIPPED
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
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
from app.services.admin_auth import issue_admin_session
from app.services.admin_refunds import refund_detail
from app.services.order_rules import generate_order_number

REFUNDS_URL = "/api/admin/refunds"
COOKIE_NAME = "__Host-shop_admin_session"

# 一行三件、每件 3000 仙，不用券与积分；运费 800 仙。应付即会话记录的付款金额。
UNIT_CASH = 3000
QUANTITY = 3
SHIPPING = 800
TOTAL = UNIT_CASH * QUANTITY + SHIPPING

STRIPE_FIELDS = ("paid_via_stripe", "payment_method_type", "stripe_refunds")
ATTEMPT_KEYS = {"id", "created_at", "amount_sen", "status", "stripe_refund_id"}

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # 关掉驱动的事务处理、由引擎发 BEGIN，与 tests/test_admin_refunds.py 相同。
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


@pytest.fixture
def token(engine: Engine, admin_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    with Session(engine) as db:
        issued = issue_admin_session(db, admin_id, _now() - timedelta(minutes=1))
        db.commit()
    return issued.token


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _make_order(engine: Engine) -> int:
    """直接写一张已发货的游客订单（一行三件与逐件分摊快照）并提交，返回订单 ID。"""
    created = _now() - timedelta(days=2)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=STATUS_SHIPPED,
            subtotal_sen=UNIT_CASH * QUANTITY,
            coupon_discount_sen=0,
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
            paid_at=created + timedelta(minutes=4),
        )
        db.add(order)
        db.flush()
        item = OrderItem(
            order_id=order.id,
            line_index=0,
            variant_id=None,
            sku="SKU-0",
            product_name_en="Tee",
            product_name_zh="T恤",
            product_name_ms="Tee",
            variant_label_en="",
            variant_label_zh="",
            variant_label_ms="",
            unit_price_sen=UNIT_CASH,
            quantity=QUANTITY,
            line_subtotal_sen=UNIT_CASH * QUANTITY,
        )
        db.add(item)
        db.flush()
        for unit_index in range(QUANTITY):
            db.add(
                OrderItemUnit(
                    order_item_id=item.id,
                    unit_index=unit_index,
                    original_price_sen=UNIT_CASH,
                    coupon_discount_sen=0,
                    points_discount=0,
                    cash_paid_sen=UNIT_CASH,
                    points_earned=0,
                )
            )
        db.flush()
        order_id = order.id
        db.commit()
    return order_id


def _add_request(
    engine: Engine,
    order_id: int,
    quantity: int,
    *,
    status: str = REFUND_REQUESTED,
    reviewer_id: int | None = None,
) -> int:
    """直接写一笔退款申请并提交，返回申请 ID（照 tests/test_admin_refunds.py 的
    _add_request）。按件序取第 0 行尚未被占用的 quantity 件；quantity 为 0 时没有申请行、
    现金退款额为 0。approved 与 rejected 另写审核各列，rejected 的件不占用。"""
    review: dict[str, Any] = {}
    if status != REFUND_REQUESTED:
        assert reviewer_id is not None
        review = {
            "reviewed_at": _now(),
            "reviewer_admin_id": reviewer_id,
            "review_reason": "Demo rejection" if status == REFUND_REJECTED else None,
            "review_idempotency_key": _key(),
            "review_fingerprint": "1" * 64,
        }
    with Session(engine) as db:
        request = RefundRequest(
            order_id=order_id,
            status=status,
            actor_type=ACTOR_GUEST,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            amount_sen=0,
            created_at=_now() - timedelta(days=1),
            **review,
        )
        db.add(request)
        db.flush()
        if quantity:
            item = db.scalars(select(OrderItem).where(OrderItem.order_id == order_id)).one()
            taken = select(RefundLineUnit.order_item_unit_id).where(RefundLineUnit.occupied == 1)
            unit_stmt = (
                select(OrderItemUnit)
                .where(OrderItemUnit.order_item_id == item.id, OrderItemUnit.id.not_in(taken))
                .order_by(OrderItemUnit.unit_index)
                .limit(quantity)
            )
            units = list(db.scalars(unit_stmt))
            assert len(units) == quantity
            line = RefundLine(
                refund_request_id=request.id,
                order_item_id=item.id,
                quantity=quantity,
                amount_sen=UNIT_CASH * quantity,
            )
            db.add(line)
            db.flush()
            for unit in units:
                db.add(
                    RefundLineUnit(
                        refund_line_id=line.id,
                        order_item_unit_id=unit.id,
                        cash_paid_sen=unit.cash_paid_sen,
                        occupied=None if status == REFUND_REJECTED else 1,
                    )
                )
            request.amount_sen = UNIT_CASH * quantity
        db.flush()
        request_id = request.id
        db.commit()
    return request_id


def _stripe_paid(engine: Engine, order_id: int, method: str | None = "card") -> tuple[int, str]:
    """直接写该单由 Stripe 付款的会话记录（complete、paid、确认时间非空，照
    tests/test_stripe_refund_approval.py 的 _stripe_paid），付款方式类型为 method；
    返回记录 ID 与 PaymentIntent ID。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=2)
    intent_id = f"pi_test_paid_{n}"
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_paid_{n}",
            payment_intent_id=intent_id,
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_COMPLETE,
            payment_status=PAYMENT_PAID,
            payment_method_type=method,
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


def _unconfirmed_checkout(engine: Engine, order_id: int, kind: str) -> tuple[int, str | None]:
    """直接写一条确认时间为空的会话记录，返回记录 ID 与 PaymentIntent ID：
    - expired_unpaid：访客发起后没有付款、改用模拟支付；
    - expired_late_paid：迟到的付款（HANDOFF 0.53：expired 可带 paid 与完成时间）；
    - complete_unconfirmed：会话完成而本站未据它确认订单。"""
    n = next(_SEQUENCE)
    started = _now() - timedelta(days=2)
    paid = kind != "expired_unpaid"
    intent_id = f"pi_test_other_{n}" if paid else None
    with Session(engine) as db:
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=f"create-key-{n}",
            checkout_session_id=f"cs_test_other_{n}",
            payment_intent_id=intent_id,
            amount_sen=TOTAL,
            currency="myr",
            status=CHECKOUT_COMPLETE if kind == "complete_unconfirmed" else CHECKOUT_EXPIRED,
            payment_status=PAYMENT_PAID if paid else PAYMENT_UNPAID,
            payment_method_type="fpx" if paid else None,
            create_params="[]",
            expires_at=started + timedelta(minutes=30),
            created_at=started,
            completed_at=started + timedelta(minutes=20) if paid else None,
            active_order_id=order_id if kind == "complete_unconfirmed" else None,
        )
        db.add(record)
        db.commit()
        return record.id, intent_id


def _add_attempt(
    engine: Engine,
    intent_id: str,
    *,
    status: str,
    created_at: datetime,
    request_id: int | None = None,
    checkout_id: int | None = None,
    amount: int = UNIT_CASH,
    stripe_refund_id: str | None = None,
    last_error_type: str | None = None,
) -> int:
    """直接插入一条退款尝试，来源为 request_id 或 checkout_id（照
    tests/test_stripe_refund_approval.py 的写法）；返回尝试 ID。"""
    active = status != STRIPE_REFUND_FAILED
    with Session(engine) as db:
        attempt = StripeRefund(
            payment_intent_id=intent_id,
            source_type=SOURCE_REFUND_REQUEST if request_id is not None else SOURCE_CHECKOUT,
            refund_request_id=request_id,
            checkout_id=checkout_id,
            stripe_refund_id=stripe_refund_id,
            amount_sen=amount,
            status=status,
            call_count=2,
            last_error_type=last_error_type,
            created_at=created_at,
            succeeded_at=created_at if status == STRIPE_REFUND_SUCCEEDED else None,
            alerted_at=created_at,
            last_checked_at=created_at,
            active_refund_request_id=request_id if active else None,
            active_checkout_id=checkout_id if active else None,
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        db.commit()
    return attempt_id


def _detail(client: TestClient, token: str, refund_id: int) -> Any:
    response = client.get(
        f"{REFUNDS_URL}/{refund_id}",
        params={"lang": "en"},
        headers={"Cookie": f"{COOKIE_NAME}={token}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response


def _stripe(body: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(body[field] for field in STRIPE_FIELDS)


def _stored_attempt(engine: Engine, attempt_id: int) -> dict[str, Any]:
    """库里这条尝试的五个字段，按接口的格式。"""
    with Session(engine) as db:
        row = db.get(StripeRefund, attempt_id)
        assert row is not None
        return {
            "id": row.id,
            "created_at": _iso(row.created_at),
            "amount_sen": row.amount_sen,
            "status": row.status,
            "stripe_refund_id": row.stripe_refund_id,
        }


_SNAPSHOT_MODELS = (
    Order,
    RefundRequest,
    RefundLine,
    RefundLineUnit,
    StripeCheckout,
    StripeRefund,
    AuditEvent,
)


def _snapshot(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """订单、申请、申请行与件、会话记录、退款尝试与审计的全部列值：用来断言「不写库」。"""
    with Session(engine) as db:
        result: dict[str, list[tuple[Any, ...]]] = {}
        for model in _SNAPSHOT_MODELS:
            table = model.__table__
            rows = db.execute(select(table).order_by(*table.primary_key.columns))
            result[table.name] = [tuple(row) for row in rows]
        return result


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的语句：查询记为「select:」加 MySQL 方言编译的 SQL，其余语句记类名。"""
    log: list[str] = []

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if isinstance(clauseelement, str):
            return
        if isinstance(clauseelement, Select):
            log.append("select:" + str(clauseelement.compile(dialect=mysql.dialect())))
        else:
            log.append(type(clauseelement).__name__)

    event.listen(engine, "before_execute", on_execute)
    try:
        yield log
    finally:
        event.remove(engine, "before_execute", on_execute)


# ---------------------------------------------------------------------------
# Stripe 付款：已批准的申请与它的尝试
# ---------------------------------------------------------------------------


def test_approved_stripe_request_lists_own_attempts_newest_first(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 8 条「后台退款详情显示各次尝试的 Stripe 退款状态；最新一次尝试为 failed
    时管理员可手动重试」与 UX A03「每次尝试一行，最新的在最上」；验收：paid_via_stripe 为真、
    payment_method_type 原样（fpx），stripe_refunds 只含来源为本申请的尝试、按尝试 ID 倒序，
    五个字段与库里相同（没有 Stripe 退款 ID 时为 null）；同一订单另一申请的尝试与来源为
    会话（迟到付款）的尝试不出现。"""
    order_id = _make_order(engine)
    _, intent_id = _stripe_paid(engine, order_id, "fpx")
    late_id, late_intent = _unconfirmed_checkout(engine, order_id, "expired_late_paid")
    assert late_intent is not None
    request_id = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    other_id = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    base = _now() - timedelta(hours=5)
    first = _add_attempt(
        engine,
        intent_id,
        request_id=request_id,
        status=STRIPE_REFUND_FAILED,
        created_at=base,
        stripe_refund_id="re_test_first",
    )
    other = _add_attempt(
        engine,
        intent_id,
        request_id=other_id,
        status=STRIPE_REFUND_SUCCEEDED,
        created_at=base + timedelta(hours=1),
        stripe_refund_id="re_test_other",
    )
    _add_attempt(
        engine,
        late_intent,
        checkout_id=late_id,
        amount=TOTAL,
        status=STRIPE_REFUND_PENDING,
        created_at=base + timedelta(hours=2),
        stripe_refund_id="re_test_late",
    )
    second = _add_attempt(
        engine,
        intent_id,
        request_id=request_id,
        status=STRIPE_REFUND_PENDING,
        created_at=base + timedelta(hours=3),
    )

    body = _detail(client, token, request_id).json()
    other_body = _detail(client, token, other_id).json()

    assert body["paid_via_stripe"] is True
    assert body["payment_method_type"] == "fpx"
    assert body["stripe_refunds"] == [
        _stored_attempt(engine, second),
        _stored_attempt(engine, first),
    ]
    assert body["stripe_refunds"] == [
        {
            "id": second,
            "created_at": _iso(base + timedelta(hours=3)),
            "amount_sen": UNIT_CASH,
            "status": STRIPE_REFUND_PENDING,
            "stripe_refund_id": None,
        },
        {
            "id": first,
            "created_at": _iso(base),
            "amount_sen": UNIT_CASH,
            "status": STRIPE_REFUND_FAILED,
            "stripe_refund_id": "re_test_first",
        },
    ]
    assert _stripe(other_body) == (True, "fpx", [_stored_attempt(engine, other)])


@pytest.mark.parametrize("method", ["grabpay", "card", None])
def test_payment_method_type_is_taken_as_recorded(
    engine: Engine, client: TestClient, admin_id: int, token: str, method: str | None
) -> None:
    """DESIGN 第 8 条「后台退款详情按记下的实际付款方式类型显示期限提示 … 其他方式或类型
    为空时为『以 Stripe 为准』」与 UX A03「付款方式类型取确认付款时记下的实际类型 … 原样
    作为 {type} 显示；尚未补齐时显示 [admin.stripe_method_unknown]」：payment_method_type
    原样给出，类型为空时为 null、paid_via_stripe 仍为真。"""
    order_id = _make_order(engine)
    _, intent_id = _stripe_paid(engine, order_id, method)
    request_id = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    attempt = _add_attempt(
        engine,
        intent_id,
        request_id=request_id,
        status=STRIPE_REFUND_SUCCEEDED,
        created_at=_now() - timedelta(hours=1),
        stripe_refund_id=f"re_test_{method}",
    )

    body = _detail(client, token, request_id).json()

    assert _stripe(body) == (True, method, [_stored_attempt(engine, attempt)])


def test_stripe_request_before_approval_and_without_cash_has_no_attempts(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """UX A03「[admin.paid_via_stripe] 与付款方式在审核中、已批准、已拒绝三种状态都显示」与
    「现金退款额为 0：[admin.stripe_refund_none]（不显示尝试表）」；DESIGN 第 8 条只在批准时
    建尝试：审核中、已拒绝与现金退款额为 0 的已批准申请 paid_via_stripe 都为真、付款方式
    原样，尝试为空列表。"""
    order_id = _make_order(engine)
    _stripe_paid(engine, order_id, "fpx")
    requested = _add_request(engine, order_id, 1)
    rejected = _add_request(engine, order_id, 1, status=REFUND_REJECTED, reviewer_id=admin_id)
    zero_cash = _add_request(engine, order_id, 0, status=REFUND_APPROVED, reviewer_id=admin_id)

    for request_id in (requested, rejected, zero_cash):
        assert _stripe(_detail(client, token, request_id).json()) == (True, "fpx", [])


# ---------------------------------------------------------------------------
# 经模拟支付的订单
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "record", ["none", "expired_unpaid", "expired_late_paid", "complete_unconfirmed"]
)
def test_mock_paid_order_is_not_stripe(
    engine: Engine, client: TestClient, admin_id: int, token: str, record: str
) -> None:
    """DESIGN 第 8 条「经模拟支付的订单退款不变」与 UX A03「只对经 Stripe 测试付款的订单的
    申请显示下面的内容；经模拟支付的订单与上面相同」；验收：「由 Stripe 付款」与
    add_request_attempt 判定相同（确认时间非空的会话记录），没有记录、只有 expired 或未确认
    记录的订单在三种申请状态下都为假、null 与空列表；迟到付款的来源为会话的尝试也不出现。"""
    order_id = _make_order(engine)
    if record != "none":
        checkout_id, intent_id = _unconfirmed_checkout(engine, order_id, record)
        if intent_id is not None:
            _add_attempt(
                engine,
                intent_id,
                checkout_id=checkout_id,
                amount=TOTAL,
                status=STRIPE_REFUND_PENDING,
                created_at=_now() - timedelta(hours=1),
            )
    approved = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    rejected = _add_request(engine, order_id, 1, status=REFUND_REJECTED, reviewer_id=admin_id)
    requested = _add_request(engine, order_id, 1)

    for request_id in (approved, rejected, requested):
        assert _stripe(_detail(client, token, request_id).json()) == (False, None, [])


# ---------------------------------------------------------------------------
# 不返回的字段与只读
# ---------------------------------------------------------------------------


def test_response_has_no_error_type_or_stripe_internals(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 10 条「本站不读取、不保存 Stripe 返回的客户资料、卡号片段或账单地址」与
    第 8 条「后台退款详情显示各次尝试的 Stripe 退款状态」（只给状态）；验收：每条尝试只有
    id、created_at、amount_sen、status、stripe_refund_id 五个字段，不返回 last_error_type、
    调用次数、告警与复查时间、PaymentIntent ID 与会话 ID。"""
    order_id = _make_order(engine)
    _, intent_id = _stripe_paid(engine, order_id, "card")
    request_id = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    _add_attempt(
        engine,
        intent_id,
        request_id=request_id,
        status=STRIPE_REFUND_FAILED,
        created_at=_now() - timedelta(hours=1),
        last_error_type="invalid_request_error",
    )
    with Session(engine) as db:
        session_id = db.scalars(
            select(StripeCheckout.checkout_session_id).where(StripeCheckout.order_id == order_id)
        ).one()
    assert session_id is not None

    response = _detail(client, token, request_id)

    (attempt,) = response.json()["stripe_refunds"]
    assert set(attempt) == ATTEMPT_KEYS
    assert attempt["status"] == STRIPE_REFUND_FAILED
    for secret in ("invalid_request_error", intent_id, session_id):
        assert secret not in response.text
    for field in ("last_error_type", "call_count", "alerted_at", "last_checked_at"):
        assert field not in response.text


def test_detail_is_read_only_without_locks(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 8 条「后台退款详情显示各次尝试的 Stripe 退款状态」（显示，不改状态、不调用
    Stripe）；验收：refund_detail 仍只读——接口读取前后订单、申请、会话记录、退款尝试与审计
    各表不变；直接调用时只有查询、以 MySQL 方言编译都不带 FOR UPDATE，且读了会话记录与
    退款尝试两表，会话里没有待写对象。"""
    order_id = _make_order(engine)
    _, intent_id = _stripe_paid(engine, order_id, "fpx")
    request_id = _add_request(engine, order_id, 1, status=REFUND_APPROVED, reviewer_id=admin_id)
    _add_attempt(
        engine,
        intent_id,
        request_id=request_id,
        status=STRIPE_REFUND_PENDING,
        created_at=_now() - timedelta(hours=1),
    )
    before = _snapshot(engine)

    _detail(client, token, request_id)

    assert _snapshot(engine) == before
    with Session(engine) as db:
        with _trace(engine) as log:
            found = refund_detail(db, request_id, "en", _now())
        assert not db.new and not db.dirty and not db.deleted

    assert found is not None and found.paid_via_stripe is True
    assert log and all(entry.startswith("select:") for entry in log), log
    assert not any("FOR UPDATE" in entry for entry in log), log
    assert any("FROM stripe_checkouts" in entry for entry in log), log
    assert any("FROM stripe_refunds" in entry for entry in log), log
    assert _snapshot(engine) == before

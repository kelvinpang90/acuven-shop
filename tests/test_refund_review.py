"""退款审核规则与全部退款后冻结确认收货（SHOP-TASK-040）。

覆盖 app/services/refund_review.py、app/services/order_lookup.py 的 confirm_receipt、
app/services/admin_orders.py 的 advance_order 与 POST /api/orders/confirm-receipt。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」「失败、并发与重试」第 1 条
与 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定（下称「Kelvin 2026-10-06」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句
或 Kelvin 的哪一项决定；没有直接原句的，写明是 SHOP-TASK-040 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。
按 SQLAlchemy 文档的做法关掉 pysqlite 的事务处理、由引擎发 BEGIN
（与 tests/test_jobs.py 相同），保存点才和 MySQL 上一样：
最外层的 RELEASE 不会提前提交。内存库只有一条共享连接，所以每个会话用完都结束事务。
管理员与订单（含逐件分摊快照）直接写库建立，
退款申请经 SHOP-TASK-029 的 submit_refund 提交。
确认收货接口的会话依赖换成每个请求一个绑定同一内存库的会话。
SQLite 不执行 FOR UPDATE：锁定查询另用 MySQL 方言编译检查，
语句与事务边界的顺序用 SQLAlchemy 的连接事件记录。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Select, create_engine, event, func, select, text
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
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    ReceiptConfirmation,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.order import (
    ACTOR_GUEST,
    STATUS_AWAITING_PAYMENT,
    STATUS_COMPLETED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_LOOKUP
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services import admin_orders, order_lookup, refund_review
from app.services.order_access import COOKIE_NAME, issue_order_access
from app.services.order_rules import generate_order_number
from app.services.refund_review import ReviewOutcome
from app.services.refunds import RefundLineRequest, submit_refund

CONFIRM_URL = "/api/orders/confirm-receipt"

APPROVED_ACTION = "admin_refund_approved"
REJECTED_ACTION = "admin_refund_rejected"
TARGET = "refund_request"

# 订单行：单价与件数。没有券与积分，逐件现金实付即单价。
LINES = [(3000, 3), (1500, 1)]
SUBTOTAL = 3 * 3000 + 1500
SHIPPING = 800

REASON = "Item already shipped"

Review = Callable[[Session, int, int, str | None, str, datetime], tuple[ReviewOutcome, bool]]
REVIEWS: dict[str, Review] = {
    "approve": refund_review.approve_refund,
    "reject": refund_review.reject_refund,
}
RESULTS = {"approve": REFUND_APPROVED, "reject": REFUND_REJECTED}

# 审核之前申请的审核各列：状态、审核时间、审核人、理由、审核幂等键、审核请求指纹。
UNREVIEWED = (REFUND_REQUESTED, None, None, None, None, None)


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


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _make_order(engine: Engine, status: str) -> _Placed:
    """直接写一张已支付的游客订单（订单行、逐件分摊快照与下单事件）并提交。"""
    created = _now() - timedelta(days=1)
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=SUBTOTAL + SHIPPING,
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
        for line_index, (price, quantity) in enumerate(LINES):
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
                        coupon_discount_sen=0,
                        points_discount=0,
                        cash_paid_sen=price,
                        points_earned=0,
                    )
                )
        db.add(
            OrderEvent(
                order_id=order.id,
                from_status=None,
                to_status=STATUS_AWAITING_PAYMENT,
                actor_type=ACTOR_GUEST,
                created_at=created,
            )
        )
        db.flush()
        placed = _Placed(id=order.id, number=order.order_number)
        db.commit()
    return placed


def _submit(engine: Engine, order_id: int, lines: list[tuple[int, int]]) -> int:
    """经 submit_refund 提交一笔退款申请（各行为行序与件数），返回申请 ID。"""
    key = _key()
    requested = [RefundLineRequest(line_index, quantity) for line_index, quantity in lines]
    with Session(engine) as db:
        submit_refund(db, order_id, requested, key, _now())
        stmt = select(RefundRequest.id).where(RefundRequest.idempotency_key == key)
        return db.scalars(stmt).one()


def _run(
    engine: Engine,
    name: str,
    request_id: int,
    admin_id: int,
    reason: str | None,
    key: str,
    now: datetime | None = None,
) -> tuple[ReviewOutcome, bool]:
    """在一个新会话里批准或拒绝，由这里（调用方）提交。"""
    with Session(engine) as db:
        result = REVIEWS[name](db, request_id, admin_id, reason, key, now or _now())
        db.commit()
    return result


def _fails(
    engine: Engine,
    name: str,
    expected: type[Exception],
    request_id: int,
    admin_id: int,
    reason: str | None,
    key: str,
) -> pytest.ExceptionInfo[Any]:
    """批准或拒绝抛出 expected；会话关闭时回滚。"""
    with Session(engine) as db, pytest.raises(expected) as info:
        REVIEWS[name](db, request_id, admin_id, reason, key, _now())
    return info


def _columns(engine: Engine, request_id: int) -> tuple[Any, ...]:
    """申请的审核各列：状态、审核时间、审核人、理由、审核幂等键、审核请求指纹。"""
    with Session(engine) as db:
        row = db.get(RefundRequest, request_id)
        assert row is not None
        return (
            row.status,
            row.reviewed_at,
            row.reviewer_admin_id,
            row.review_reason,
            row.review_idempotency_key,
            row.review_fingerprint,
        )


def _units(engine: Engine, request_id: int) -> list[tuple[int, int | None]]:
    """申请各件：所属逐件分摊快照 ID 与占用标记，按记录 ID。"""
    with Session(engine) as db:
        stmt = (
            select(RefundLineUnit.order_item_unit_id, RefundLineUnit.occupied)
            .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
            .where(RefundLine.refund_request_id == request_id)
            .order_by(RefundLineUnit.id)
        )
        return [tuple(row) for row in db.execute(stmt)]


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """审计记录：管理员、操作名、对象类别、对象 ID、旧值、新值，按写入顺序。"""
    with Session(engine) as db:
        rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id))
        return [
            (
                row.admin_account_id,
                row.action,
                row.target_type,
                row.target_id,
                row.old_value,
                row.new_value,
            )
            for row in rows
        ]


def _order(engine: Engine, order_id: int) -> tuple[str, int, int]:
    """订单状态、事件数与确认收货记录数。"""
    with Session(engine) as db:
        status = db.scalars(select(Order.status).where(Order.id == order_id)).one()
        events = db.scalar(
            select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == order_id)
        )
        confirmations = db.scalar(
            select(func.count())
            .select_from(ReceiptConfirmation)
            .where(ReceiptConfirmation.order_id == order_id)
        )
        return status, int(events or 0), int(confirmations or 0)


def _fingerprint(request_id: int, result: str, reason: str) -> str:
    """验收约定的指纹规则，在这里另算一遍。"""
    content = {"reason": reason, "refund_request_id": request_id, "result": result}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def _confirm(client: TestClient, engine: Engine, placed: _Placed) -> Any:
    """给一个新浏览器签发该单的 lookup 授权，再以新幂等键确认收货。"""
    with Session(engine) as db:
        issued = issue_order_access(db, None, placed.id, SCOPE_LOOKUP, _now())
        db.commit()
    assert issued.new_token is not None
    headers = {
        "Cookie": f"{COOKIE_NAME}={issued.new_token}",
        "X-CSRF-Token": issued.csrf_token,
        "Idempotency-Key": _key(),
    }
    return client.post(CONFIRM_URL, json={"order_number": placed.number}, headers=headers)


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的事务边界与语句，按发生顺序。

    事务边界记为 begin、rollback、commit；查询记为「select:」加 MySQL 方言编译的 SQL；
    其余语句记类名（保存点为 SavepointClause、RollbackToSavepointClause、
    ReleaseSavepointClause）。
    """
    log: list[str] = []

    def on_begin(_conn) -> None:
        log.append("begin")

    def on_rollback(_conn) -> None:
        log.append("rollback")

    def on_commit(_conn) -> None:
        log.append("commit")

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if isinstance(clauseelement, str):
            # 夹具的 begin 钩子以驱动语句发出的 BEGIN，已记为 begin。
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


def _order_locks(log: list[str]) -> list[int]:
    """记录里锁定订单行的查询（含 FOR UPDATE、读 orders 表）的位置。"""
    return [
        index
        for index, entry in enumerate(log)
        if entry.startswith("select:") and "FOR UPDATE" in entry and "FROM orders" in entry
    ]


# ---------------------------------------------------------------------------
# 批准
# ---------------------------------------------------------------------------


def test_approve_records_review_and_keeps_units(engine: Engine, admin_id: int) -> None:
    """「requested → 管理员 approved」「退款与履约状态分开存储」。
    Kelvin 2026-10-06：批准时没有理由存空值；审核幂等键与请求指纹存在退款申请表。
    SHOP-TASK-040 验收：写审核时间、审核人、幂等键与指纹，各件保持占用，
    审计一条（旧值、新值为审核前后的状态），订单状态与事件不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 2), (1, 1)])
    units = _units(engine, request_id)
    before = _order(engine, placed.id)
    key = _key()
    now = _now()

    outcome, reviewed = _run(engine, "approve", request_id, admin_id, None, key, now)

    fingerprint = _fingerprint(request_id, REFUND_APPROVED, "")
    assert reviewed is True
    assert outcome == ReviewOutcome(
        refund_request_id=request_id,
        status=REFUND_APPROVED,
        reviewed_at=now.replace(tzinfo=UTC),
        review_reason=None,
    )
    assert _columns(engine, request_id) == (
        REFUND_APPROVED,
        now,
        admin_id,
        None,
        key,
        fingerprint,
    )
    assert len(fingerprint) == 64 and set(fingerprint) <= set("0123456789abcdef")
    assert _units(engine, request_id) == units
    assert [occupied for _, occupied in units] == [1, 1, 1]
    assert _audits(engine) == [
        (admin_id, APPROVED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_APPROVED)
    ]
    assert _order(engine, placed.id) == before


@pytest.mark.parametrize(
    ("reason", "stored"),
    [
        (None, None),
        ("", None),
        (" \t\n ", None),
        ("  Checked with the customer.\n", "Checked with the customer."),
        (" " + "好" * 500 + " ", "好" * 500),
    ],
    ids=["none", "empty", "blank", "trimmed", "500_chars"],
)
def test_approve_reason_is_trimmed_or_empty(
    engine: Engine, admin_id: int, reason: str | None, stored: str | None
) -> None:
    """Kelvin 2026-10-06：批准时理由可空，去掉首尾空白后 1 到 500 个字符；
    批准时没有理由存空值。SHOP-TASK-040 验收：理由一律先去掉首尾空白，
    存去掉之后的文本，指纹按去掉之后的理由计算。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])

    outcome, _ = _run(engine, "approve", request_id, admin_id, reason, _key())

    columns = _columns(engine, request_id)
    assert outcome.review_reason == stored
    assert columns[3] == stored
    assert columns[5] == _fingerprint(request_id, REFUND_APPROVED, stored or "")


@pytest.mark.parametrize("reason", ["x" * 501, "  " + "x" * 501 + "  "], ids=["501", "padded"])
def test_approve_reason_over_500_is_rejected(engine: Engine, admin_id: int, reason: str) -> None:
    """Kelvin 2026-10-06：理由去掉首尾空白后至多 500 个字符。
    SHOP-TASK-040 验收：超过时抛理由不合法异常，申请、各件与审计都不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])
    units = _units(engine, request_id)

    _fails(
        engine,
        "approve",
        refund_review.ReviewReasonInvalid,
        request_id,
        admin_id,
        reason,
        _key(),
    )

    assert _columns(engine, request_id) == UNREVIEWED
    assert _units(engine, request_id) == units
    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [None, "", "   ", " \t\n ", "x" * 501],
    ids=["none", "empty", "spaces", "blank", "501"],
)
def test_reject_requires_reason(engine: Engine, admin_id: int, reason: str | None) -> None:
    """Kelvin 2026-10-06：拒绝时审核理由必填，去掉首尾空白后 1 到 500 个字符。
    SHOP-TASK-040 验收：无理由、只有空白或超长时抛理由不合法异常且数据不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 3)])
    units = _units(engine, request_id)
    before = _order(engine, placed.id)

    _fails(
        engine,
        "reject",
        refund_review.ReviewReasonInvalid,
        request_id,
        admin_id,
        reason,
        _key(),
    )

    assert _columns(engine, request_id) == UNREVIEWED
    assert _units(engine, request_id) == units
    assert _audits(engine) == []
    assert _order(engine, placed.id) == before


def test_reject_records_reason_and_releases_units(engine: Engine, admin_id: int) -> None:
    """「requested → 管理员 rejected」；Kelvin 2026-10-01（HANDOFF 0.27）：
    被拒绝的申请释放所占的件。Kelvin 2026-10-06：拒绝时理由必填。
    SHOP-TASK-040 验收：状态、理由（去掉首尾空白）与审计正确，各件占用标记置空，
    同样的件可再次申请；订单状态不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 3)])
    unit_ids = [unit_id for unit_id, _ in _units(engine, request_id)]
    before = _order(engine, placed.id)
    key = _key()
    now = _now()

    outcome, reviewed = _run(engine, "reject", request_id, admin_id, f"  {REASON}\n", key, now)

    assert reviewed is True
    assert outcome.status == REFUND_REJECTED
    assert outcome.review_reason == REASON
    assert _columns(engine, request_id) == (
        REFUND_REJECTED,
        now,
        admin_id,
        REASON,
        key,
        _fingerprint(request_id, REFUND_REJECTED, REASON),
    )
    assert _units(engine, request_id) == [(unit_id, None) for unit_id in unit_ids]
    assert _audits(engine) == [
        (admin_id, REJECTED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_REJECTED)
    ]
    assert _order(engine, placed.id) == before

    again = _submit(engine, placed.id, [(0, 3)])

    assert _columns(engine, again)[0] == REFUND_REQUESTED
    assert _units(engine, again) == [(unit_id, 1) for unit_id in unit_ids]


# ---------------------------------------------------------------------------
# 幂等与已审核
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "first_reason", "again_reason"),
    [
        ("approve", None, "  "),
        ("approve", "Fine", " Fine\n"),
        ("reject", REASON, f"\t{REASON}  "),
    ],
    ids=["approve_empty", "approve_reason", "reject"],
)
def test_same_key_same_request_replays(
    engine: Engine,
    admin_id: int,
    name: str,
    first_reason: str | None,
    again_reason: str | None,
) -> None:
    """「相同键相同请求返回原结果」（失败、并发与重试第 1 条）。
    SHOP-TASK-040 验收：同键且指纹相同即返回原结果，不改数据、不写审计；
    理由先去掉首尾空白再算指纹。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])
    key = _key()
    first_at = _now() - timedelta(minutes=5)

    first, created = _run(engine, name, request_id, admin_id, first_reason, key, first_at)
    columns = _columns(engine, request_id)
    units = _units(engine, request_id)

    again, created_again = _run(engine, name, request_id, admin_id, again_reason, key)

    assert (created, created_again) == (True, False)
    assert again == first
    assert again.reviewed_at == first_at.replace(tzinfo=UTC)
    assert _columns(engine, request_id) == columns
    assert _units(engine, request_id) == units
    assert len(_audits(engine)) == 1


@pytest.mark.parametrize(
    ("first", "first_reason", "again", "again_reason"),
    [
        ("approve", None, "reject", REASON),
        ("reject", REASON, "approve", None),
        ("reject", REASON, "approve", REASON),
        ("reject", REASON, "reject", "Another reason"),
        ("approve", "Fine", "approve", "Also fine"),
        ("approve", None, "approve", "Fine"),
    ],
    ids=[
        "approve_then_reject",
        "reject_then_approve",
        "same_reason_other_result",
        "reject_other_reason",
        "approve_other_reason",
        "approve_added_reason",
    ],
)
def test_same_key_other_content_conflicts(
    engine: Engine,
    admin_id: int,
    first: str,
    first_reason: str | None,
    again: str,
    again_reason: str | None,
) -> None:
    """「同键不同内容报冲突」（失败、并发与重试第 1 条）。
    SHOP-TASK-040 验收：同键不同结果或不同理由抛幂等冲突异常，数据与审计不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])
    key = _key()
    _run(engine, first, request_id, admin_id, first_reason, key)
    columns = _columns(engine, request_id)
    units = _units(engine, request_id)

    _fails(
        engine,
        again,
        refund_review.IdempotencyConflict,
        request_id,
        admin_id,
        again_reason,
        key,
    )

    assert _columns(engine, request_id) == columns
    assert _units(engine, request_id) == units
    assert len(_audits(engine)) == 1


def test_same_key_other_request_conflicts(engine: Engine, admin_id: int) -> None:
    """「同键不同内容报冲突」：指纹含申请 ID。SHOP-TASK-040 验收：
    同一审核幂等键用于另一笔申请时抛幂等冲突，那笔申请保持 requested。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    first = _submit(engine, placed.id, [(0, 1)])
    second = _submit(engine, placed.id, [(1, 1)])
    key = _key()
    _run(engine, "approve", first, admin_id, None, key)

    _fails(engine, "approve", refund_review.IdempotencyConflict, second, admin_id, None, key)

    assert _columns(engine, second) == UNREVIEWED
    assert len(_audits(engine)) == 1


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("again", ["approve", "reject"])
def test_new_key_on_reviewed_request_raises_already_reviewed(
    engine: Engine, admin_id: int, first: str, again: str
) -> None:
    """「已批准的退款不可再次批准」；requested 才可审核。
    SHOP-TASK-040 验收：用新键对已审核的申请（同一结果或相反结果）都抛已审核异常
    并带当前状态，数据与审计不变。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])
    _run(engine, first, request_id, admin_id, REASON, _key())
    columns = _columns(engine, request_id)
    units = _units(engine, request_id)

    info = _fails(
        engine,
        again,
        refund_review.RefundAlreadyReviewed,
        request_id,
        admin_id,
        REASON,
        _key(),
    )

    assert info.value.status == RESULTS[first]
    assert _columns(engine, request_id) == columns
    assert _units(engine, request_id) == units
    assert len(_audits(engine)) == 1


@pytest.mark.parametrize("name", ["approve", "reject"])
def test_unknown_request_raises_not_found(engine: Engine, admin_id: int, name: str) -> None:
    """SHOP-TASK-040 验收：申请不存在时抛不存在异常，不写审计。"""
    placed = _make_order(engine, STATUS_SHIPPED)
    request_id = _submit(engine, placed.id, [(0, 1)])

    for missing in (request_id + 1, 2**31 - 1):
        _fails(
            engine,
            name,
            refund_review.RefundRequestNotFound,
            missing,
            admin_id,
            REASON,
            _key(),
        )

    assert _columns(engine, request_id) == UNREVIEWED
    assert _audits(engine) == []


def test_audit_and_guest_view_omit_reason(engine: Engine, admin_id: int) -> None:
    """Kelvin 2026-10-06：理由只给管理员看，访客在 P09 的退款记录只显示状态。
    SHOP-TASK-040 验收：审计不写理由原文；查单视图里没有理由。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    approved = _submit(engine, placed.id, [(0, 1)])
    rejected = _submit(engine, placed.id, [(1, 1)])
    _run(engine, "approve", approved, admin_id, "SECRET-APPROVE-REASON", _key())
    _run(engine, "reject", rejected, admin_id, "SECRET-REJECT-REASON", _key())

    audits = _audits(engine)
    with Session(engine) as db:
        views = order_lookup.lookup_orders(db, [placed.id], "en", _now())
    view = views[0].model_dump_json()

    assert [row[1] for row in audits] == [APPROVED_ACTION, REJECTED_ACTION]
    assert "SECRET" not in json.dumps(audits)
    assert "SECRET" not in view
    statuses = {refund.status for refund in views[0].refund_requests}
    assert statuses == {REFUND_APPROVED, REFUND_REJECTED}


# ---------------------------------------------------------------------------
# 全部退款后冻结确认收货
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("split", [False, True], ids=["one_request", "two_requests"])
def test_confirm_is_frozen_after_all_units_approved(
    engine: Engine, client: TestClient, admin_id: int, split: bool
) -> None:
    """「所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，
    保留退款前履约状态」；Kelvin 2026-10-06：全部件都批准后冻结确认收货，
    订单状态保持不变。SHOP-TASK-040 验收：409 fulfilment_frozen 带订单当前状态、
    no-store，不写确认收货记录与事件。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    groups = [[(0, 3)], [(1, 1)]] if split else [[(0, 3), (1, 1)]]
    for lines in groups:
        _run(engine, "approve", _submit(engine, placed.id, lines), admin_id, None, _key())
    before = _order(engine, placed.id)

    response = _confirm(client, engine, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "fulfilment_frozen", "status": STATUS_SHIPPED}
    assert response.headers["cache-control"] == "no-store"
    assert _order(engine, placed.id) == before
    assert before[0] == STATUS_SHIPPED
    assert before[2] == 0


@pytest.mark.parametrize("rest", ["none", "requested", "rejected"])
def test_confirm_after_partial_refund_completes(
    engine: Engine, client: TestClient, admin_id: int, rest: str
) -> None:
    """「所有购买件数均已批准退款时」才冻结：只批准一部分件（其余未申请、
    审核中或被拒）时照常确认收货，进入 demo_completed。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    _run(engine, "approve", _submit(engine, placed.id, [(0, 3)]), admin_id, None, _key())
    if rest != "none":
        other = _submit(engine, placed.id, [(1, 1)])
        if rest == "rejected":
            _run(engine, "reject", other, admin_id, REASON, _key())

    response = _confirm(client, engine, placed)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": STATUS_COMPLETED}
    status, _, confirmations = _order(engine, placed.id)
    assert (status, confirmations) == (STATUS_COMPLETED, 1)


def test_confirm_checks_status_before_frozen(
    engine: Engine, client: TestClient, admin_id: int
) -> None:
    """SHOP-TASK-040 验收：锁定后先照旧检查状态，不是 demo_shipped 仍按 SHOP-TASK-027
    返回 order_not_confirmable，即使全部已退。
    """
    placed = _make_order(engine, STATUS_PACKED)
    request_id = _submit(engine, placed.id, [(0, 3), (1, 1)])
    _run(engine, "approve", request_id, admin_id, None, _key())

    response = _confirm(client, engine, placed)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "order_not_confirmable", "status": STATUS_PACKED}
    assert _order(engine, placed.id)[0] == STATUS_PACKED


# ---------------------------------------------------------------------------
# 锁定协议
# ---------------------------------------------------------------------------


def test_lock_statements_compile_to_for_update_on_mysql() -> None:
    """Kelvin 2026-10-06：批准与拒绝都先锁定所属订单行，与推进发货、访客确认收货
    同一锁定协议。SHOP-TASK-040 验收：以 MySQL 方言编译三处锁定订单的查询，
    都含 FOR UPDATE。
    """
    statements = {
        "review": refund_review.lock_order_for_request_statement(7),
        "confirm": order_lookup.lock_order_statement(7),
        "advance": admin_orders.lock_order_statement(7),
    }

    for name, stmt in statements.items():
        sql = str(stmt.compile(dialect=mysql.dialect()))
        assert "FROM orders" in sql, name
        assert sql.rstrip().endswith("FOR UPDATE"), name
    review_sql = str(statements["review"].compile(dialect=mysql.dialect()))
    assert "FROM refund_requests" in review_sql


@pytest.mark.parametrize("operation", ["review", "confirm", "advance"])
def test_lock_is_first_statement_of_its_transaction(
    engine: Engine, admin_id: int, operation: str
) -> None:
    """Kelvin 2026-10-06：锁定前先结束此前的事务，使锁定成为新事务的第一条语句
    （REPEATABLE READ 下快照在第一条非锁定读时建立）。SHOP-TASK-040 验收：
    审核、确认收货与推进发货锁定订单的查询都是所在事务的第一条语句，
    读取占用发生在锁定之后。
    """
    status = STATUS_PAID if operation == "advance" else STATUS_SHIPPED
    placed = _make_order(engine, status)
    request_id = _submit(engine, placed.id, [(0, 1)])

    with Session(engine) as db:
        # 本请求此前的读取（如接口先校验授权）：会话已在一个只读事务里。
        assert db.get(AdminAccount, admin_id) is not None
        assert db.in_transaction()
        with _trace(engine) as log:
            if operation == "review":
                refund_review.approve_refund(db, request_id, admin_id, None, _key(), _now())
            elif operation == "confirm":
                order_lookup.confirm_receipt(db, placed.id, _key(), _now())
            else:
                admin_orders.advance_order(db, admin_id, placed.id, STATUS_PACKED, _now())

    locks = _order_locks(log)
    assert len(locks) == 1, log
    lock = locks[0]
    assert log[:lock] == ["rollback", "begin"], log
    reads = [
        index
        for index, entry in enumerate(log)
        if entry.startswith("select:") and "refund_line_units" in entry
    ]
    assert reads, log
    assert min(reads) > lock


def test_review_key_conflict_rolls_back_savepoint_only(
    engine: Engine, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「审核均使用幂等键和数据库唯一约束」。SHOP-TASK-040 验收：审核写入放在保存点里，
    遇审核幂等键唯一约束冲突只回滚保存点、订单行的锁保持，再按键重新查询后按幂等规则返回。
    模拟：锁定后按键查询时还看不到同键的审核（替换查询使第一次查不到），
    写入时才撞上唯一约束。
    """
    placed = _make_order(engine, STATUS_SHIPPED)
    target = _submit(engine, placed.id, [(0, 1)])
    other = _submit(engine, placed.id, [(1, 1)])
    key = _key()
    _run(engine, "approve", other, admin_id, None, key)
    units = _units(engine, target)

    real = refund_review._request_by_review_key
    calls: list[bool] = []

    def stale(db: Session, review_key: str, *, locking: bool = False) -> Any:
        calls.append(locking)
        if not locking:
            return None
        return real(db, review_key, locking=locking)

    monkeypatch.setattr(refund_review, "_request_by_review_key", stale)

    with Session(engine) as db:
        with _trace(engine) as log, pytest.raises(refund_review.IdempotencyConflict):
            refund_review.approve_refund(db, target, admin_id, None, key, _now())
        # 外层事务（含订单行的锁）仍在，只有保存点被回滚。
        assert db.in_transaction()
        assert not db.in_nested_transaction()
        status = db.scalars(select(RefundRequest.status).where(RefundRequest.id == target)).one()
        assert status == REFUND_REQUESTED

    assert calls == [False, True]
    locks = _order_locks(log)
    assert len(locks) == 1, log
    after = log[locks[0] :]
    assert "SavepointClause" in after
    assert "RollbackToSavepointClause" in after
    assert "ReleaseSavepointClause" not in after
    assert not {"begin", "rollback", "commit"} & set(after)
    assert _columns(engine, target) == UNREVIEWED
    assert _units(engine, target) == units
    assert _audits(engine) == [
        (admin_id, APPROVED_ACTION, TARGET, other, REFUND_REQUESTED, REFUND_APPROVED)
    ]

"""发货满 7 天自动确认收货：app/services/auto_complete.py 与 app/jobs.py 的定时任务、补跑命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：「若访客不操作，模拟发货满 7 天自动确认收货。自动完成只改演示状态，
  不影响 30 天退款期。」「所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，
  保留退款前履约状态。」
- 「失败、并发与重试」：「每日库存重置、积分过期均可重复运行……只生效一次；失败有告警与人工
  补跑办法」（SHOP-TASK-031 起对所有定时任务适用）。
每条测试（参数化的是每个用例）的文档字符串写明它守住的设计原句；没有直接原句的，写明是
SHOP-TASK-042 验收里的约定。

用 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档关掉 pysqlite
的事务处理、由引擎发 BEGIN，与 tests/test_jobs.py 相同）按模型建表。订单（含逐件分摊快照与状态
事件）与退款申请都直接写库建立；内存库只有一条共享连接，所以每个辅助函数用自己的会话、用完即
结束事务。SQLite 不执行 FOR UPDATE：锁定查询另用 MySQL 方言编译检查，语句与事务边界的顺序用
SQLAlchemy 的连接事件记录。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, Select, create_engine, event, func, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import Settings
from app.db.base import Base
from app.models import (
    AdminAccount,
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
    ACTOR_ADMIN,
    ACTOR_GUEST,
    ACTOR_SYSTEM,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services import auto_complete
from app.services.auto_complete import auto_complete_shipped_orders
from app.services.order_rules import generate_order_number
from app.services.refunds import refund_deadline

NOW = datetime(2026, 10, 20, 3, 0, 0)
# 恰好满 7×24 小时的发货时间。
DUE = NOW - timedelta(days=7)
CREATED = datetime(2026, 10, 1, 3, 0, 0)
PAID_AT = CREATED + timedelta(minutes=2)
PRICE = 1500
SHIPPING = 800
QUANTITY = 2
SECRET = "sentinel-connection-detail"


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 关掉驱动的事务处理、由引擎发 BEGIN，事务边界才和 MySQL 上一样。
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
def factory(engine: Engine) -> jobs.SessionFactory:
    return lambda: Session(engine)


def _order(
    engine: Engine,
    status: str = STATUS_SHIPPED,
    shipped: tuple[datetime, ...] = (DUE,),
) -> int:
    """直接写一张游客订单（一行 QUANTITY 件，含逐件分摊快照）与它的事件并提交；返回订单 ID。

    事件为下单与 shipped 里每个时间各一条迁移到 demo_shipped 的事件（操作者 admin）。
    """
    paid_at = PAID_AT if status in PAID_STATUSES else None
    subtotal = PRICE * QUANTITY
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=subtotal,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=subtotal + SHIPPING,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"key-{generate_order_number()}",
            request_fingerprint="0" * 64,
            created_at=CREATED,
            payment_expires_at=CREATED + timedelta(minutes=15),
            paid_at=paid_at,
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
            unit_price_sen=PRICE,
            quantity=QUANTITY,
            line_subtotal_sen=subtotal,
        )
        db.add(item)
        db.flush()
        for unit_index in range(QUANTITY):
            db.add(
                OrderItemUnit(
                    order_item_id=item.id,
                    unit_index=unit_index,
                    original_price_sen=PRICE,
                    coupon_discount_sen=0,
                    points_discount=0,
                    cash_paid_sen=PRICE,
                    points_earned=0,
                )
            )
        db.add(
            OrderEvent(
                order_id=order.id,
                from_status=None,
                to_status=STATUS_AWAITING_PAYMENT,
                actor_type=ACTOR_GUEST,
                created_at=CREATED,
            )
        )
        for at in shipped:
            db.add(
                OrderEvent(
                    order_id=order.id,
                    from_status=STATUS_PACKED,
                    to_status=STATUS_SHIPPED,
                    actor_type=ACTOR_ADMIN,
                    created_at=at,
                )
            )
        order_id = order.id
        db.commit()
    return order_id


def _refund(
    engine: Engine, order_id: int, status: str, units: int = QUANTITY, first: int = 0
) -> None:
    """写一笔退款申请并提交，占用该单从件序 first 起的 units 件（占用标记 1）。

    approved 与 rejected 另写审核人、审核幂等键与审核请求指纹，rejected 另写理由（SHOP-TASK-039
    的检查约束要求）；审核人是库里唯一的管理员账号，还没有时先建一个。被拒的申请释放所占的件。
    """
    with Session(engine) as db:
        review: dict[str, object] = {}
        if status != REFUND_REQUESTED:
            admin_id = db.scalar(select(AdminAccount.id))
            if admin_id is None:
                admin = AdminAccount(
                    username="shop_admin",
                    password_hash="not-a-real-hash",
                    created_at=CREATED,
                    password_updated_at=CREATED,
                )
                db.add(admin)
                db.flush()
                admin_id = admin.id
            review = {
                "reviewed_at": NOW - timedelta(days=1),
                "reviewer_admin_id": admin_id,
                "review_idempotency_key": f"review-{generate_order_number()}",
                "review_fingerprint": "1" * 64,
                "review_reason": "Demo rejection" if status == REFUND_REJECTED else None,
            }
        item = db.scalars(select(OrderItem).where(OrderItem.order_id == order_id)).one()
        unit_stmt = (
            select(OrderItemUnit)
            .where(OrderItemUnit.order_item_id == item.id)
            .order_by(OrderItemUnit.unit_index)
            .offset(first)
            .limit(units)
        )
        taken = list(db.scalars(unit_stmt))
        amount = sum(unit.cash_paid_sen for unit in taken)
        request = RefundRequest(
            order_id=order_id,
            status=status,
            actor_type=ACTOR_GUEST,
            idempotency_key=f"refund-{generate_order_number()}",
            request_fingerprint="0" * 64,
            amount_sen=amount,
            created_at=NOW - timedelta(days=2),
            **review,
        )
        db.add(request)
        db.flush()
        line = RefundLine(
            refund_request_id=request.id,
            order_item_id=item.id,
            quantity=len(taken),
            amount_sen=amount,
        )
        db.add(line)
        db.flush()
        for unit in taken:
            db.add(
                RefundLineUnit(
                    refund_line_id=line.id,
                    order_item_unit_id=unit.id,
                    cash_paid_sen=unit.cash_paid_sen,
                    occupied=None if status == REFUND_REJECTED else 1,
                )
            )
        db.commit()


def _status(engine: Engine, order_id: int) -> str:
    with Session(engine) as db:
        return db.get(Order, order_id).status


def _paid_at(engine: Engine, order_id: int) -> datetime | None:
    with Session(engine) as db:
        return db.get(Order, order_id).paid_at


def _events(engine: Engine, order_id: int) -> list[tuple[str | None, str, str, datetime]]:
    """该单的事件 (迁移前状态, 迁移后状态, 操作者, 时间)，按写入顺序。"""
    stmt = (
        select(
            OrderEvent.from_status,
            OrderEvent.to_status,
            OrderEvent.actor_type,
            OrderEvent.created_at,
        )
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    with Session(engine) as db:
        return list(db.execute(stmt).tuples())


def _completion_events(engine: Engine, order_id: int) -> list[tuple[str | None, str, str]]:
    return [
        (from_status, to_status, actor)
        for from_status, to_status, actor, _at in _events(engine, order_id)
        if to_status == STATUS_COMPLETED
    ]


def _receipts(engine: Engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(ReceiptConfirmation))


def _order_number(engine: Engine, order_id: int) -> str:
    with Session(engine) as db:
        return db.get(Order, order_id).order_number


def _run(engine: Engine, now: datetime = NOW, limit: int = 100) -> int:
    with Session(engine) as db:
        count = auto_complete_shipped_orders(db, now, limit)
        assert not db.in_transaction()
    return count


def _job_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的事务边界与语句，按发生顺序。

    事务边界记为 begin、rollback、commit；查询记为「select:」加 MySQL 方言编译的 SQL；
    其余语句记类名（如 Update、Insert）。
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


def _transaction_end(log: list[str], start: int) -> int:
    """记录里 start 之后第一个事务边界（begin、rollback 或 commit）的位置。"""
    for index in range(start + 1, len(log)):
        if log[index] in ("begin", "rollback", "commit"):
            return index
    raise AssertionError(log)


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------


def test_order_shipped_seven_days_ago_is_completed_by_system(engine: Engine) -> None:
    """「若访客不操作，模拟发货满 7 天自动确认收货。」SHOP-TASK-042 验收：发货满 7×24 小时
    （恰好满也算）的订单改为 demo_completed，写一条操作者为 system 的事件，不写确认收货记录。"""
    order_id = _order(engine, shipped=(DUE,))

    assert _run(engine) == 1

    assert _status(engine, order_id) == STATUS_COMPLETED
    assert _events(engine, order_id)[-1] == (
        STATUS_SHIPPED,
        STATUS_COMPLETED,
        ACTOR_SYSTEM,
        NOW,
    )
    assert _completion_events(engine, order_id) == [
        (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)
    ]
    assert _receipts(engine) == 0


def test_auto_completion_leaves_the_refund_window_unchanged(engine: Engine) -> None:
    """「自动完成只改演示状态，不影响 30 天退款期。」退款截止时间按支付时间算，自动完成不改
    支付时间。"""
    order_id = _order(engine, shipped=(DUE,))
    deadline = refund_deadline(_paid_at(engine, order_id))

    assert _run(engine) == 1

    assert _paid_at(engine, order_id) == PAID_AT
    assert refund_deadline(_paid_at(engine, order_id)) == deadline


def test_one_second_short_of_seven_days_is_not_completed(engine: Engine) -> None:
    """「模拟发货满 7 天自动确认收货。」SHOP-TASK-042 验收：差一秒未满 7×24 小时的不处理。"""
    order_id = _order(engine, shipped=(DUE + timedelta(seconds=1),))
    events = _events(engine, order_id)

    assert _run(engine) == 0

    assert _status(engine, order_id) == STATUS_SHIPPED
    assert _events(engine, order_id) == events

    # 一秒之后满 7 天，照常完成。
    assert _run(engine, NOW + timedelta(seconds=1)) == 1
    assert _status(engine, order_id) == STATUS_COMPLETED


def test_fully_refunded_order_is_frozen(engine: Engine) -> None:
    """「所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，保留退款前履约状态。」
    SHOP-TASK-042 验收：全部已退（SHOP-TASK-029 的 refund_state）的订单跳过，状态与事件不变。"""
    order_id = _order(engine, shipped=(DUE - timedelta(days=3),))
    _refund(engine, order_id, REFUND_APPROVED)
    events = _events(engine, order_id)

    assert _run(engine) == 0

    assert _status(engine, order_id) == STATUS_SHIPPED
    assert _events(engine, order_id) == events
    assert _receipts(engine) == 0


@pytest.mark.parametrize(
    "refunds",
    [
        pytest.param([(REFUND_APPROVED, 1, 0)], id="one_of_two_approved"),
        pytest.param([(REFUND_REQUESTED, QUANTITY, 0)], id="all_requested"),
        pytest.param([(REFUND_REJECTED, QUANTITY, 0)], id="all_rejected"),
        pytest.param([(REFUND_APPROVED, 1, 0), (REFUND_REQUESTED, 1, 1)], id="approved_requested"),
    ],
)
def test_partly_refunded_order_is_still_completed(
    engine: Engine, refunds: list[tuple[str, int, int]]
) -> None:
    """「所有购买件数均已批准退款时」才冻结；SHOP-TASK-042 验收：只退一部分的照常完成
    （有件未申请、在审核中或被拒都不是全部已退）。"""
    order_id = _order(engine, shipped=(DUE,))
    for status, units, first in refunds:
        _refund(engine, order_id, status, units, first)

    assert _run(engine) == 1

    assert _status(engine, order_id) == STATUS_COMPLETED
    assert _completion_events(engine, order_id) == [
        (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)
    ]


@pytest.mark.parametrize(
    "status",
    [STATUS_AWAITING_PAYMENT, STATUS_PAID, STATUS_PACKED, STATUS_COMPLETED, STATUS_CANCELLED],
)
def test_orders_not_in_shipped_are_not_touched(engine: Engine, status: str) -> None:
    """「模拟发货满 7 天自动确认收货」只针对模拟发货（demo_shipped）的订单；「已完成的履约
    状态不可倒退」。SHOP-TASK-042 验收：已完成、已取消与其他状态不处理——即使库里有一条
    满 7 天的迁移到 demo_shipped 的事件，状态与事件也不变。"""
    order_id = _order(engine, status, shipped=(DUE - timedelta(days=1),))
    events = _events(engine, order_id)

    assert _run(engine) == 0

    assert _status(engine, order_id) == status
    assert _events(engine, order_id) == events


def test_shipped_order_without_shipped_event_is_not_touched(engine: Engine) -> None:
    """SHOP-TASK-042 验收：发货时间以迁移到 demo_shipped 的状态事件为准；没有这条事件的订单
    不是候选。"""
    order_id = _order(engine, shipped=())

    assert _run(engine) == 0

    assert _status(engine, order_id) == STATUS_SHIPPED


def test_latest_shipped_event_counts(engine: Engine) -> None:
    """「模拟发货满 7 天」。SHOP-TASK-042 验收：以最近一次迁移到 demo_shipped 的时间为准——
    较早一次已满 7 天、最近一次未满的不处理；两次都满的照常完成。"""
    recent = _order(engine, shipped=(DUE - timedelta(days=2), DUE + timedelta(hours=1)))
    both_due = _order(engine, shipped=(DUE - timedelta(days=2), DUE))

    assert _run(engine) == 1

    assert _status(engine, recent) == STATUS_SHIPPED
    assert _status(engine, both_due) == STATUS_COMPLETED


# ---------------------------------------------------------------------------
# 单次上限、排序与重复运行
# ---------------------------------------------------------------------------


def test_limit_counts_only_completed_orders(engine: Engine) -> None:
    """「所有购买件数均已批准退款时，冻结……确认收货的推进」与「可重复运行」。SHOP-TASK-042
    验收：单次上限只计完成的张数；排在前面的全部已退订单超过上限张时，之后的订单仍会完成。"""
    frozen = [_order(engine, shipped=(DUE - timedelta(days=3, minutes=i),)) for i in range(3)]
    for order_id in frozen:
        _refund(engine, order_id, REFUND_APPROVED)
    first = _order(engine, shipped=(DUE - timedelta(days=2),))
    second = _order(engine, shipped=(DUE - timedelta(days=1),))
    third = _order(engine, shipped=(DUE,))

    assert _run(engine, limit=2) == 2

    assert [_status(engine, i) for i in frozen] == [STATUS_SHIPPED] * 3
    assert [_status(engine, i) for i in (first, second, third)] == [
        STATUS_COMPLETED,
        STATUS_COMPLETED,
        STATUS_SHIPPED,
    ]

    # 下一次照常越过冻结的订单，完成剩下的一张。
    assert _run(engine, limit=2) == 1
    assert _status(engine, third) == STATUS_COMPLETED
    assert [_status(engine, i) for i in frozen] == [STATUS_SHIPPED] * 3


def test_candidates_are_processed_by_shipped_time_then_order_id(engine: Engine) -> None:
    """SHOP-TASK-042 验收：按最近一次迁移到 demo_shipped 的时间从早到晚、同一时间按订单 ID
    从小到大依次处理（上限 1 时每次只完成排在最前的一张）。订单 ID 的大小与发货时间的先后
    故意相反。"""
    latest = _order(engine, shipped=(DUE,))
    tie_low = _order(engine, shipped=(DUE - timedelta(days=1),))
    tie_high = _order(engine, shipped=(DUE - timedelta(days=1),))
    earliest = _order(engine, shipped=(DUE - timedelta(days=2),))

    completed_in_order = []
    for _ in range(4):
        before = {i: _status(engine, i) for i in (latest, tie_low, tie_high, earliest)}
        assert _run(engine, limit=1) == 1
        changed = [
            i
            for i in (latest, tie_low, tie_high, earliest)
            if before[i] == STATUS_SHIPPED and _status(engine, i) == STATUS_COMPLETED
        ]
        completed_in_order.extend(changed)

    assert completed_in_order == [earliest, tie_low, tie_high, latest]
    assert _run(engine, limit=1) == 0


def test_scan_pages_past_more_frozen_orders_than_the_limit(engine: Engine) -> None:
    """「所有购买件数均已批准退款时，冻结……确认收货的推进」。SHOP-TASK-042 验收：一直扫描到
    完成上限张或没有更多候选——冻结的订单是上限的好几倍、跨好几页时，之后的订单仍会完成。"""
    frozen = [_order(engine, shipped=(DUE - timedelta(days=2, minutes=i),)) for i in range(5)]
    for order_id in frozen:
        _refund(engine, order_id, REFUND_APPROVED)
    later = _order(engine, shipped=(DUE,))

    assert _run(engine, limit=2) == 1

    assert _status(engine, later) == STATUS_COMPLETED
    assert [_status(engine, i) for i in frozen] == [STATUS_SHIPPED] * 5


def test_running_twice_at_the_same_instant_takes_effect_once(engine: Engine) -> None:
    """「失败、并发与重试」：定时任务可重复运行、只生效一次。SHOP-TASK-042 验收：同一时刻
    连续执行两次只生效一次。"""
    order_id = _order(engine, shipped=(DUE,))

    assert _run(engine) == 1
    assert _run(engine) == 0

    assert _status(engine, order_id) == STATUS_COMPLETED
    assert _completion_events(engine, order_id) == [
        (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)
    ]
    assert _receipts(engine) == 0


def test_zero_limit_completes_nothing(engine: Engine) -> None:
    """SHOP-TASK-042 验收：单次上限计的是完成的张数（上限 0 时一张也不完成，也不留未结束的
    事务）。"""
    order_id = _order(engine, shipped=(DUE,))

    assert _run(engine, limit=0) == 0

    assert _status(engine, order_id) == STATUS_SHIPPED


# ---------------------------------------------------------------------------
# 锁定协议
# ---------------------------------------------------------------------------


def test_lock_statement_compiles_to_for_update_on_mysql() -> None:
    """「失败、并发与重试」：可重复运行、只生效一次。SHOP-TASK-042 验收：用 MySQL 方言编译
    锁定订单的查询，含 FOR UPDATE（与 SHOP-TASK-040 的访客确认收货同一条查询）。"""
    sql = str(auto_complete.lock_order_statement(7).compile(dialect=mysql.dialect()))

    assert "FROM orders" in sql
    assert sql.rstrip().endswith("FOR UPDATE")


def test_each_lock_is_the_first_statement_of_its_transaction(engine: Engine) -> None:
    """「所有购买件数均已批准退款时，冻结……确认收货的推进」与「只生效一次」。SHOP-TASK-042
    验收（SHOP-TASK-040 的锁定协议）：每张订单锁定订单行之前先结束此前的事务，锁定查询是所在
    事务的第一条语句；读取退款占用（refund_state）在锁定之后、同一事务里；完成的提交、跳过的
    回滚，返回时不留未结束的事务。"""
    due = _order(engine, shipped=(DUE - timedelta(days=1),))
    frozen = _order(engine, shipped=(DUE - timedelta(hours=1),))
    _refund(engine, frozen, REFUND_APPROVED)
    also_due = _order(engine, shipped=(DUE,))

    with Session(engine) as db:
        # 调用前此会话已在一个只读事务里。
        assert db.get(Order, due) is not None
        assert db.in_transaction()
        with _trace(engine) as log:
            assert auto_complete_shipped_orders(db, NOW, 10) == 2
        assert not db.in_transaction()

    locks = _order_locks(log)
    assert len(locks) == 3, log
    for lock in locks:
        assert log[lock - 1] == "begin", log
        assert log[lock - 2] in ("rollback", "commit"), log
        # 锁定到本事务结束之间：先读退款占用，再按结果提交或回滚。
        inside = log[lock + 1 : _transaction_end(log, lock)]
        assert any(e.startswith("select:") and "refund_line_units" in e for e in inside), log
    # 第一张、第三张完成并提交；第二张（全部已退）回滚。
    ends = [log[_transaction_end(log, lock)] for lock in locks]
    assert ends == ["commit", "rollback", "commit"], log
    assert _status(engine, due) == STATUS_COMPLETED
    assert _status(engine, frozen) == STATUS_SHIPPED
    assert _status(engine, also_due) == STATUS_COMPLETED


# ---------------------------------------------------------------------------
# 定时任务与补跑命令
# ---------------------------------------------------------------------------


def test_job_is_registered_after_existing_jobs_with_own_transactions() -> None:
    """「失败、并发与重试」：定时任务可重复运行、只生效一次。SHOP-TASK-042 验收：default_jobs
    在已有任务之后加 auto_complete_shipped，以 own_transactions=True 注册（运行器不包外层
    事务与保存点），单轮上限与超时取消相同。"""
    default = jobs.default_jobs()

    assert [job.name for job in default] == [
        "cancel_expired_orders",
        "reset_daily_stock",
        "auto_complete_shipped",
        # SHOP-TASK-072 在其后加了删除满 30 天的短信验证记录；它同样以 own_transactions=True
        # 注册，所以下一行的 default[-1] 现在检查的是它，auto_complete_shipped 由再下一行检查。
        "delete_expired_verifications",
    ]
    assert default[-1].own_transactions is True
    assert jobs.auto_complete_job().own_transactions is True
    assert jobs.AUTO_COMPLETE_BATCH_LIMIT == jobs.CANCEL_BATCH_LIMIT


def test_runner_round_completes_due_orders(
    engine: Engine,
    factory: jobs.SessionFactory,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「若访客不操作，模拟发货满 7 天自动确认收货。」SHOP-TASK-042 验收：运行器每轮执行该任务；
    日志只记录任务名与完成张数，不记录订单号。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    order_id = _order(engine, shipped=(DUE,))
    number = _order_number(engine, order_id)
    heartbeat = tmp_path / "heartbeat"
    runner = jobs.Runner([jobs.auto_complete_job()], factory, heartbeat, now=lambda: NOW)

    runner.run_round()
    runner.run_round()

    assert _status(engine, order_id) == STATUS_COMPLETED
    assert _completion_events(engine, order_id) == [
        (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)
    ]
    assert _job_messages(caplog) == ["job auto_complete_shipped done: 1 changed"]
    assert all(number not in message for message in caplog.messages)
    assert heartbeat.exists()


def test_complete_shipped_command_succeeds(
    engine: Engine, factory: jobs.SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-042 验收：complete-shipped 执行一次后以 0 退出，
    日志只含任务名与完成张数、不含订单号；再执行一次不重复生效。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    order_id = _order(engine, shipped=(DUE,))
    number = _order_number(engine, order_id)

    assert jobs.complete_shipped(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert jobs.complete_shipped(factory, now=lambda: NOW) == jobs.EXIT_OK

    assert _status(engine, order_id) == STATUS_COMPLETED
    assert _completion_events(engine, order_id) == [
        (STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM)
    ]
    assert _job_messages(caplog) == [
        "job auto_complete_shipped done: 1 changed",
        "job auto_complete_shipped done: 0 changed",
    ]
    assert all(number not in message for message in caplog.messages)


def test_complete_shipped_command_fails_nonzero_on_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-042 验收：complete-shipped 失败以 1 退出（与已有
    子命令相同），日志只含任务名与异常类名、不含连接信息。"""
    caplog.set_level(logging.INFO, logger="app.jobs")

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(SECRET))

    assert jobs.complete_shipped(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job auto_complete_shipped failed: OperationalError"]
    assert all(SECRET not in message for message in caplog.messages)


def test_main_complete_shipped_without_database_exits_two(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-042 验收：complete-shipped 子命令的退出码与已有
    子命令相同——未配置 SHOP_DATABASE_URL 时为 2。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))

    assert jobs.main(["complete-shipped"]) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]


def test_main_complete_shipped_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-042 验收：complete-shipped 经 SHOP_DATABASE_URL 与
    会话工厂执行，失败以 1 退出，日志不含连接串。这里的内存库没有建表，所以任务失败。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    url = "sqlite://"
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=url))

    assert jobs.main(["complete-shipped"]) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job auto_complete_shipped failed: OperationalError"]
    assert all(url not in message for message in caplog.messages)

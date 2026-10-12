"""退款批准接入积分返还与追回（SHOP-TASK-092）。

覆盖 app/services/refund_review.py 的批准与拒绝调用 SHOP-TASK-087 的 apply_refund_points。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 4、5 条与「订单与退款状态」
（批准模拟退款时按原计价分摊回退未过期的已抵扣积分，并追回该商品产生的积分；余额不足扣至 0，
剩余记待抵扣），以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（下称「Kelvin
2026-10-10」：返还以整单为单位，从后到期的批次起返还）。每条测试的文档字符串写明它守住的设计原句
或 Kelvin 的决定；没有直接原句的，写明是 SHOP-TASK-092 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）；引擎设置与
tests/test_refund_review.py 相同（关掉 pysqlite 的事务处理、由引擎发 BEGIN），保存点才和 MySQL
上一样。会员、订单与逐件分摊快照直接写库，积分批次与流水经 SHOP-TASK-086 的 earn_points、
reserve_points 与 forfeit_points 准备，退款申请经 SHOP-TASK-029 的 submit_refund 提交。
apply_refund_points 以原函数外包一层记录入参，判断审核是否调用它。SQLite 不执行 FOR UPDATE：
锁的先后只以 MySQL 方言编译的语句顺序检查，死锁只在 MySQL 上发生，这里不演练。
"""

from __future__ import annotations

import itertools
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, Select, create_engine, event, func, select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AdminAccount,
    AuditEvent,
    Member,
    Order,
    OrderItem,
    OrderItemUnit,
    PointEntry,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
from app.models.order import CLAIM_NOT_CLAIMABLE, CLAIM_OPEN, STATUS_SHIPPED
from app.models.points import KIND_DEBT_ADD, KIND_REFUND_RETURN, KIND_REFUND_REVERSAL
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED, UNIT_OCCUPIED
from app.services import refund_review
from app.services.order_rules import generate_order_number
from app.services.points import (
    available_points,
    debt_points,
    earn_points,
    forfeit_points,
    reserve_points,
)
from app.services.refunds import RefundLineRequest, submit_refund

APPROVED_ACTION = "admin_refund_approved"
REJECTED_ACTION = "admin_refund_rejected"
TARGET = "refund_request"
REASON = "Item damaged in transit"

# 马来西亚时间 2026-10-10 12:00 支付成功，2026-10-12 12:00 提交申请。
PAID = datetime(2026, 10, 10, 4, 0)
NOW = datetime(2026, 10, 12, 4, 0)
REVIEW_AT = NOW + timedelta(hours=1)
SHIPPING = 800

# 逐件分摊快照，按订单行、件序：（原价、分摊券额、分摊积分、获得积分），现金实付由此算出。
Unit = tuple[int, int, int, int]
# DESIGN 第 4 条算例：A 2 件各 RM10、B 1 件 RM5；券 RM2.50、积分 300、获得 19（8、7、4）。
DESIGN_UNITS: list[list[Unit]] = [
    [(1000, 100, 120, 8), (1000, 100, 120, 7)],
    [(500, 50, 60, 4)],
]
# 不用券与积分的会员订单：现金 RM25，获得 25（10、10、5）。
CASH_UNITS: list[list[Unit]] = [
    [(1000, 0, 0, 10), (1000, 0, 0, 10)],
    [(500, 0, 0, 5)],
]
# 游客订单：没有券与积分，也不获得积分。
GUEST_UNITS: list[list[Unit]] = [
    [(1000, 0, 0, 0), (1000, 0, 0, 0)],
    [(500, 0, 0, 0)],
]

_PHONE_SEQUENCE = itertools.count(300_000_000)


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
            created_at=PAID - timedelta(days=1),
            password_updated_at=PAID - timedelta(days=1),
        )
        db.add(row)
        db.flush()
        account_id = row.id
        db.commit()
    return account_id


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """记录审核调用 apply_refund_points 的入参（订单 ID、申请 ID、两个积分数、当前时间）。"""
    real = refund_review.apply_refund_points
    recorded: list[tuple[Any, ...]] = []

    def spy(db: Session, order_id: int, request_id: int, back: int, rev: int, now: datetime):
        recorded.append((order_id, request_id, back, rev, now))
        return real(db, order_id, request_id, back, rev, now)

    monkeypatch.setattr(refund_review, "apply_refund_points", spy)
    return recorded


@dataclass(frozen=True)
class _Setup:
    member_id: int
    order_id: int
    # 下单之前已有的批次，按给出的顺序。
    prior_lot_ids: tuple[int, ...]
    own_lot_id: int | None


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _order_row(db: Session, member_id: int | None, units: list[list[Unit]] | None = None) -> Order:
    """一张已发货的订单；units 为空时不写订单行（只作积分批次的来源或预占的去处）。"""
    flat = [unit for line in units or [] for unit in line]
    subtotal = sum(unit[0] for unit in flat)
    coupon = sum(unit[1] for unit in flat)
    points = sum(unit[2] for unit in flat)
    created = PAID - timedelta(minutes=10)
    order = _add(
        db,
        Order(
            order_number=generate_order_number(),
            status=STATUS_SHIPPED,
            subtotal_sen=subtotal,
            coupon_discount_sen=coupon,
            points_redeemed=points,
            shipping_fee_sen=SHIPPING,
            total_sen=subtotal - coupon - points + SHIPPING,
            points_earned=sum(unit[3] for unit in flat),
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=PAID,
            member_id=member_id,
            claim_status=CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE,
        ),
    )
    for line_index, line in enumerate(units or []):
        price = line[0][0]
        item = _add(
            db,
            OrderItem(
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
                quantity=len(line),
                line_subtotal_sen=price * len(line),
            ),
        )
        for unit_index, (original, unit_coupon, unit_points, earned) in enumerate(line):
            db.add(
                OrderItemUnit(
                    order_item_id=item.id,
                    unit_index=unit_index,
                    original_price_sen=original,
                    coupon_discount_sen=unit_coupon,
                    points_discount=unit_points,
                    cash_paid_sen=original - unit_coupon - unit_points,
                    points_earned=earned,
                )
            )
    db.flush()
    return order


def _member_order(
    engine: Engine,
    units: list[list[Unit]],
    prior_lots: list[tuple[int, datetime]],
    *,
    lot_points: int | None = None,
) -> _Setup:
    """会员先在 prior_lots 的各时刻获得积分（各自一张来源订单），再下这张订单：
    下单时预占快照的抵扣积分（先到期先用），支付成功时获得 lot_points（默认为快照合计）。
    """
    with Session(engine) as db:
        phone = f"+601{next(_PHONE_SEQUENCE)}"
        member = _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=PAID))
        prior: list[int] = []
        for points, acquired_at in prior_lots:
            lot = earn_points(db, member.id, _order_row(db, member.id).id, points, acquired_at)
            assert lot is not None
            prior.append(lot.id)
        order = _order_row(db, member.id, units)
        reserve_points(db, member.id, order.id, order.points_redeemed, PAID - timedelta(minutes=5))
        earned = order.points_earned if lot_points is None else lot_points
        own = earn_points(db, member.id, order.id, earned, PAID)
        setup = _Setup(member.id, order.id, tuple(prior), own.id if own is not None else None)
        db.commit()
    return setup


def _design_order(engine: Engine, **kwargs: Any) -> _Setup:
    """DESIGN 第 4 条算例：会员有一个 500 积分的批次，下单抵扣 300、支付成功获得 19。"""
    return _member_order(engine, DESIGN_UNITS, [(500, NOW - timedelta(days=30))], **kwargs)


def _submit(engine: Engine, order_id: int, lines: list[tuple[int, int]]) -> int:
    """经 submit_refund 提交一笔退款申请（各行为行序与件数），返回申请 ID。"""
    key = _key()
    requested = [RefundLineRequest(line_index, quantity) for line_index, quantity in lines]
    with Session(engine) as db:
        submit_refund(db, order_id, requested, key, NOW)
        stmt = select(RefundRequest.id).where(RefundRequest.idempotency_key == key)
        return db.scalars(stmt).one()


def _approve(
    engine: Engine, request_id: int, admin_id: int, key: str, now: datetime = REVIEW_AT
) -> bool:
    """在一个新会话里批准并提交，返回是否实际审核。"""
    with Session(engine) as db:
        _, reviewed = refund_review.approve_refund(db, request_id, admin_id, None, key, now)
        db.commit()
    return reviewed


def _refund_entries(engine: Engine, request_id: int) -> list[tuple[str, int | None, int]]:
    """该申请的积分流水：（类别、批次、积分），按流水 ID。"""
    with Session(engine) as db:
        rows = db.execute(
            select(PointEntry.kind, PointEntry.lot_id, PointEntry.points)
            .where(PointEntry.refund_request_id == request_id)
            .order_by(PointEntry.id)
        )
        return [tuple(row) for row in rows]


def _entry_count(engine: Engine) -> int:
    with Session(engine) as db:
        return int(db.scalar(select(func.count()).select_from(PointEntry)) or 0)


def _remaining(engine: Engine, lot_id: int) -> int:
    with Session(engine) as db:
        stmt = select(func.coalesce(func.sum(PointEntry.points), 0)).where(
            PointEntry.lot_id == lot_id
        )
        return int(db.scalar(stmt) or 0)


def _balances(engine: Engine, member_id: int, now: datetime) -> tuple[int, int]:
    """可用余额与待抵扣。"""
    with Session(engine) as db:
        return available_points(db, member_id, now), debt_points(db, member_id)


def _request(engine: Engine, request_id: int) -> tuple[Any, ...]:
    """申请的状态、审核时间、审核人与审核幂等键。"""
    with Session(engine) as db:
        row = db.get(RefundRequest, request_id)
        assert row is not None
        return row.status, row.reviewed_at, row.reviewer_admin_id, row.review_idempotency_key


def _occupied(engine: Engine, request_id: int) -> list[int | None]:
    with Session(engine) as db:
        stmt = (
            select(RefundLineUnit.occupied)
            .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
            .where(RefundLine.refund_request_id == request_id)
            .order_by(RefundLineUnit.id)
        )
        return list(db.scalars(stmt))


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


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的事务边界与语句（写法同 tests/test_refund_review.py）。

    事务边界记为 begin、rollback、commit；查询记为「select:」加 MySQL 方言编译的 SQL；
    其余语句记类名。
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
    """记录里对 table 加锁（含 FOR UPDATE、读 table 表）的查询的位置。"""
    pattern = re.compile(rf"\bFROM {table}\b")
    return [
        index
        for index, entry in enumerate(log)
        if entry.startswith("select:") and "FOR UPDATE" in entry and pattern.search(entry)
    ]


# ---------------------------------------------------------------------------
# 批准时返还与追回
# ---------------------------------------------------------------------------


def test_design_example_first_a_returns_120_and_reverses_8(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 4 条算例「仅退第一件 A 且原抵扣积分尚未到期时 … 返还原抵扣的 120 积分，并追回
    该件获得的 8 积分」。SHOP-TASK-092 验收：两个积分数取该申请各件的逐件快照 points_discount 与
    points_earned 之和，以订单 ID、申请 ID、这两个数与当前时间调用；审计的旧值与新值不变。
    """
    setup = _design_order(engine)
    (old_lot,) = setup.prior_lot_ids
    assert setup.own_lot_id is not None
    request_id = _submit(engine, setup.order_id, [(0, 1)])

    assert _approve(engine, request_id, admin_id, _key()) is True

    assert calls == [(setup.order_id, request_id, 120, 8, REVIEW_AT)]
    assert _refund_entries(engine, request_id) == [
        (KIND_REFUND_RETURN, old_lot, 120),
        (KIND_REFUND_REVERSAL, setup.own_lot_id, -8),
    ]
    assert _remaining(engine, old_lot) == 200 + 120
    assert _remaining(engine, setup.own_lot_id) == 19 - 8
    assert _balances(engine, setup.member_id, REVIEW_AT) == (331, 0)
    assert _request(engine, request_id)[0] == REFUND_APPROVED
    assert _audits(engine) == [
        (admin_id, APPROVED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_APPROVED)
    ]


def test_two_partial_refunds_return_later_lot_first_and_stay_within_limits(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 5 条「任何次数的部分退款满足 … 累计返还抵扣积分 ≤ 该单实际抵扣积分；累计追回
    获得积分 ≤ 该单实际获得积分」与 Kelvin 2026-10-10「返还以整单为单位，从后到期的批次起返还」：
    该单从先到期的批次抵扣 100、后到期的批次抵扣 200；先退第一件 A，再退第二件 A 与 B。
    第一次的 120 全部还给后到期的批次；第二次的 180 先还后到期批次余下的 80，再还先到期批次
    的 100。两次合计返还 300、追回 19，正好等于实际抵扣与获得。
    """
    setup = _member_order(
        engine,
        DESIGN_UNITS,
        [(100, NOW - timedelta(days=200)), (250, NOW - timedelta(days=100))],
    )
    early, late = setup.prior_lot_ids
    assert setup.own_lot_id is not None

    first = _submit(engine, setup.order_id, [(0, 1)])
    _approve(engine, first, admin_id, _key())
    second = _submit(engine, setup.order_id, [(0, 1), (1, 1)])
    _approve(engine, second, admin_id, _key())

    assert [call[2:4] for call in calls] == [(120, 8), (180, 11)]
    assert _refund_entries(engine, first) == [
        (KIND_REFUND_RETURN, late, 120),
        (KIND_REFUND_REVERSAL, setup.own_lot_id, -8),
    ]
    assert _refund_entries(engine, second) == [
        (KIND_REFUND_RETURN, late, 80),
        (KIND_REFUND_RETURN, early, 100),
        (KIND_REFUND_REVERSAL, setup.own_lot_id, -11),
    ]
    entries = _refund_entries(engine, first) + _refund_entries(engine, second)
    returned = sum(points for kind, _, points in entries if kind == KIND_REFUND_RETURN)
    reversed_ = -sum(points for kind, _, points in entries if kind == KIND_REFUND_REVERSAL)
    with Session(engine) as db:
        order = db.get(Order, setup.order_id)
        assert order is not None
        assert (returned, reversed_) == (order.points_redeemed, order.points_earned) == (300, 19)
    assert (_remaining(engine, early), _remaining(engine, late)) == (100, 250)
    assert _remaining(engine, setup.own_lot_id) == 0


def test_shortfall_is_recorded_as_debt(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """「订单与退款状态」「积分余额不足时扣至 0，剩余记待抵扣积分 … 不出现负余额」与 DESIGN
    第 5 条「积分余额不足时扣至 0，缺口记待抵扣」：会员已把本单获得的 25 积分全部用在另一单，
    批准退第一件 A（追回 10）时余额为 0，10 全部记入待抵扣。
    """
    setup = _member_order(engine, CASH_UNITS, [])
    with Session(engine) as db:
        other = _order_row(db, setup.member_id)
        reserve_points(db, setup.member_id, other.id, 25, PAID + timedelta(days=1))
        db.commit()
    request_id = _submit(engine, setup.order_id, [(0, 1)])

    _approve(engine, request_id, admin_id, _key())

    assert calls == [(setup.order_id, request_id, 0, 10, REVIEW_AT)]
    assert _refund_entries(engine, request_id) == [(KIND_DEBT_ADD, None, 10)]
    assert _balances(engine, setup.member_id, REVIEW_AT) == (0, 10)


# ---------------------------------------------------------------------------
# 不调用的情形
# ---------------------------------------------------------------------------


def test_guest_order_approval_writes_no_points(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 3 条「游客不积累积分」。SHOP-TASK-092 验收：订单的 member_id 为空时不调用
    退款积分函数，批准照常完成，不写积分流水。
    """
    with Session(engine) as db:
        order_id = _order_row(db, None, GUEST_UNITS).id
        db.commit()
    request_id = _submit(engine, order_id, [(0, 2), (1, 1)])

    assert _approve(engine, request_id, admin_id, _key()) is True

    assert calls == []
    assert _entry_count(engine) == 0
    assert _request(engine, request_id)[0] == REFUND_APPROVED


def test_cleared_member_order_approval_writes_no_points(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」：注销后订单的 member_id 已清空。
    SHOP-TASK-092 验收：member_id 为空时不调用退款积分函数，批准不写积分流水。
    """
    setup = _design_order(engine)
    with Session(engine) as db:
        forfeit_points(db, setup.member_id, NOW)
        member = db.get(Member, setup.member_id)
        order = db.get(Order, setup.order_id)
        assert member is not None and order is not None
        member.status = MEMBER_DELETED
        member.phone = None
        member.password_hash = None
        member.deleted_at = NOW
        order.member_id = None
        db.commit()
    before = _entry_count(engine)
    request_id = _submit(engine, setup.order_id, [(0, 1)])

    assert _approve(engine, request_id, admin_id, _key()) is True

    assert calls == []
    assert _entry_count(engine) == before
    assert _refund_entries(engine, request_id) == []
    assert _request(engine, request_id)[0] == REFUND_APPROVED


def test_reject_writes_no_points(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """「订单与退款状态」：积分只在「批准模拟退款时」回退与追回。SHOP-TASK-092 验收：
    拒绝不调用退款积分函数，不写积分流水。
    """
    setup = _design_order(engine)
    before = _entry_count(engine)
    request_id = _submit(engine, setup.order_id, [(0, 2)])

    with Session(engine) as db:
        refund_review.reject_refund(db, request_id, admin_id, REASON, _key(), REVIEW_AT)
        db.commit()

    assert calls == []
    assert _entry_count(engine) == before
    assert _request(engine, request_id)[0] == REFUND_REJECTED
    assert _audits(engine) == [
        (admin_id, REJECTED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_REJECTED)
    ]


def test_replay_does_not_apply_points_again(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """「失败、并发与重试」第 1 条「相同键相同请求返回原结果」。SHOP-TASK-092 验收：按审核幂等键
    重放时不再调用退款积分函数，积分流水与审计都不增加。
    """
    setup = _design_order(engine)
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    key = _key()
    _approve(engine, request_id, admin_id, key)
    entries = _refund_entries(engine, request_id)
    count = _entry_count(engine)

    assert _approve(engine, request_id, admin_id, key, REVIEW_AT + timedelta(days=1)) is False

    assert len(calls) == 1
    assert _refund_entries(engine, request_id) == entries
    assert _entry_count(engine) == count
    assert _audits(engine) == [
        (admin_id, APPROVED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_APPROVED)
    ]


# ---------------------------------------------------------------------------
# 出错与锁
# ---------------------------------------------------------------------------


def test_points_error_rolls_back_review(
    engine: Engine, admin_id: int, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 5 条「累计追回获得积分 ≤ 该单实际获得积分」与「订单与退款状态」「事务内写订单、
    流水和事件」。SHOP-TASK-092 验收：退款积分函数抛 ValueError（这里本单批次只有 5 积分，
    快照却要追回 8，即数据有误）时原样抛出，申请仍为 requested、各件仍占用，不写审计与积分流水。
    """
    setup = _design_order(engine, lot_points=5)
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    before = _entry_count(engine)

    with Session(engine) as db, pytest.raises(ValueError):
        refund_review.approve_refund(db, request_id, admin_id, None, _key(), REVIEW_AT)

    assert calls == [(setup.order_id, request_id, 120, 8, REVIEW_AT)]
    assert _request(engine, request_id) == (REFUND_REQUESTED, None, None, None)
    assert _occupied(engine, request_id) == [UNIT_OCCUPIED]
    assert _audits(engine) == []
    assert _entry_count(engine) == before


def test_order_row_is_locked_before_member_row(engine: Engine, admin_id: int) -> None:
    """Kelvin 2026-10-06（HANDOFF 0.33）：批准先锁定所属订单行。SHOP-TASK-092 验收：锁顺序沿用
    现有的锁定协议，先锁订单行（新事务的第一条语句），退款积分函数再锁会员行。
    """
    setup = _design_order(engine)
    request_id = _submit(engine, setup.order_id, [(0, 1)])

    with Session(engine) as db:
        # 本请求此前的读取：会话已在一个只读事务里。
        assert db.get(AdminAccount, admin_id) is not None
        with _trace(engine) as log:
            refund_review.approve_refund(db, request_id, admin_id, None, _key(), REVIEW_AT)
        db.commit()

    order_locks = _locks(log, "orders")
    member_locks = _locks(log, "members")
    assert order_locks and member_locks, log
    assert log[: order_locks[0]] == ["rollback", "begin"], log
    assert order_locks[0] < member_locks[0]
    assert _refund_entries(engine, request_id) != []

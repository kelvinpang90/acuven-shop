"""退款申请：退款期、可退件与金额的计算、查询，以及游客提交退款申请。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：支付成功后 30 天内可申请退款，包含已发货和已完成订单；requested → 管理员
  approved 或 rejected；重复申请同一可退数量被拒绝；部分退款后余量仍可再次申请，总批准数量不超过
  购买数量；只选商品与数量，金额按原单逐件现金实付快照计算，示例运费始终不退；系统显示累计已退及
  剩余可退金额；所有购买件数均已批准退款时冻结后续履约推进。
- 「计价、优惠、积分与库存」第 4 条：「每件实际支付商品额」只指现金实付，不含积分抵扣；仅退第一件
  A 时退该件的现金实付。
- 「失败、并发与重试」第 1 条：退款申请使用幂等键和数据库唯一约束，相同键相同请求返回原结果，
  同键不同内容报冲突。
- docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 决定：同一订单行只退其中几件时，按件序从前往后
  取尚未被审核中或已批准的申请占用的件，金额为这几件的逐件现金实付之和，被拒绝的申请释放所占的件；
  截止时间为支付时刻加 30×24 小时。

规则：
- 退款截止时间 = 支付时间 + 30×24 小时；当前时间不晚于它时在退款期内。未支付没有截止时间。
- 某订单行的可退件是该行逐件分摊快照中未被占用（占用标记为 1）的件，按件序从小到大；
  申请 k 件即取可退件中件序最小的 k 件，金额为它们的现金实付之和。
- 累计已退 = 该单 approved 申请的金额之和；剩余可退 = 该单所有未被占用的件的现金实付之和；
  全部件都已被 approved 申请占用时为全部已退（供之后的审核任务冻结履约）。
- 运费不计入任何金额。

访问授权、CSRF 与请求格式由调用方（app/api/refunds.py）先行校验；这里只按订单 ID 操作，
申请方固定为 guest。审核（批准、拒绝及审核人与理由，拒绝时把逐件记录的占用标记置空）、全部退款后
冻结打包、发货与确认收货、积分返还与追回、会员模式（申请方 member）都由之后的任务扩展，
这里不预留参数。

本模块不写日志；异常消息不含个人资料、订单号、幂等键或令牌。时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderItem,
    OrderItemUnit,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.order import ACTOR_GUEST, PAID_STATUSES
from app.models.refund import REFUND_APPROVED, REFUND_REQUESTED, UNIT_OCCUPIED

# 「支付成功后 30 天内」：支付时刻加 30×24 小时。
REFUND_WINDOW = timedelta(hours=30 * 24)


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的退款申请。"""


class OrderNotRefundable(Exception):
    """订单未支付（待支付或已取消）。"""


class RefundWindowClosed(Exception):
    """当前时间晚于退款截止时间。"""


class UnknownOrderLine(Exception):
    """请求里的行序不属于该订单。"""


class RefundNothingLeft(Exception):
    """该单已没有任何可退件。"""


class RefundDuplicate(Exception):
    """某行申请件数超过该行可退件数（与已有的有效申请重复）。"""


@dataclass(frozen=True)
class RefundLineRequest:
    """申请的一行：订单行序（order_items.line_index）与件数。"""

    line_index: int
    quantity: int


@dataclass(frozen=True)
class RefundUnit:
    """一件逐件分摊快照：快照 ID、件序与现金实付。"""

    unit_id: int
    unit_index: int
    cash_paid_sen: int


@dataclass(frozen=True)
class LineRefundState:
    """一个订单行的可退件，按件序从小到大。"""

    order_item_id: int
    line_index: int
    refundable: tuple[RefundUnit, ...]

    @property
    def estimates_sen(self) -> list[int]:
        return refund_estimates(self.refundable)


@dataclass(frozen=True)
class RefundState:
    """一张订单的退款情况。lines 按行序。"""

    lines: tuple[LineRefundState, ...]
    # 累计已退：approved 申请的金额之和。
    refunded_sen: int
    # 剩余可退：所有未被占用的件的现金实付之和。
    refundable_left_sen: int
    # 全部件都已被 approved 申请占用。
    fully_refunded: bool

    @property
    def nothing_left(self) -> bool:
        return not any(line.refundable for line in self.lines)

    def line(self, line_index: int) -> LineRefundState | None:
        for line in self.lines:
            if line.line_index == line_index:
                return line
        return None


@dataclass(frozen=True)
class RefundRecord:
    """一笔申请与它的各行（订单行快照与申请行），各行按行序。"""

    request: RefundRequest
    lines: tuple[tuple[OrderItem, RefundLine], ...]


class RefundOutcomeLine(BaseModel):
    line_index: int
    quantity: int


class RefundOutcome(BaseModel):
    """申请的状态、金额与各行件数（按行序）。"""

    status: str
    amount_sen: int
    lines: list[RefundOutcomeLine]


# ---------------------------------------------------------------------------
# 纯计算
# ---------------------------------------------------------------------------


def refund_deadline(paid_at: datetime | None) -> datetime | None:
    """支付时间加 30×24 小时；未支付为空。"""
    if paid_at is None:
        return None
    return paid_at + REFUND_WINDOW


def in_refund_window(paid_at: datetime | None, now: datetime) -> bool:
    """当前时间不晚于退款截止时间时在退款期内；未支付不在。"""
    deadline = refund_deadline(paid_at)
    return deadline is not None and now <= deadline


def pick_units(refundable: Sequence[RefundUnit], quantity: int) -> tuple[RefundUnit, ...]:
    """申请 quantity 件：取可退件（已按件序从小到大）中件序最小的 quantity 件。"""
    if quantity < 1 or quantity > len(refundable):
        raise ValueError("quantity is outside the refundable units")
    return tuple(refundable[:quantity])


def refund_estimates(refundable: Sequence[RefundUnit]) -> list[int]:
    """每种件数的申请金额：第 i 项（从 1 数起）为申请 i 件时取到的那几件的现金实付之和。"""
    estimates = []
    total = 0
    for unit in refundable:
        total += unit.cash_paid_sen
        estimates.append(total)
    return estimates


def refund_fingerprint(order_id: int, lines: Sequence[RefundLineRequest]) -> str:
    """退款申请请求指纹。

    {"lines": [{"line_index": 行序, "quantity": 件数}, ...（按行序）], "order_id": 订单 ID}
    的 JSON（键排序、无空白）的 SHA-256 十六进制。
    """
    ordered = sorted(lines, key=lambda line: line.line_index)
    content = {
        "order_id": order_id,
        "lines": [{"line_index": line.line_index, "quantity": line.quantity} for line in ordered],
    }
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def refund_state(db: Session, order_id: int) -> RefundState:
    """读该单各行的可退件、累计已退、剩余可退与是否全部已退；只读。"""
    item_stmt = (
        select(OrderItem.id, OrderItem.line_index)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItem.line_index)
    )
    items = list(db.execute(item_stmt).tuples())

    unit_stmt = (
        select(
            OrderItemUnit.id,
            OrderItemUnit.order_item_id,
            OrderItemUnit.unit_index,
            OrderItemUnit.cash_paid_sen,
        )
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .where(OrderItem.order_id == order_id)
        .order_by(OrderItemUnit.order_item_id, OrderItemUnit.unit_index)
    )
    units = list(db.execute(unit_stmt).tuples())

    # 被占用的件与占用它的申请的状态；唯一约束保证每件至多一条占用记录。
    occupied_stmt = (
        select(RefundLineUnit.order_item_unit_id, RefundRequest.status)
        .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
        .join(RefundRequest, RefundRequest.id == RefundLine.refund_request_id)
        .join(OrderItemUnit, OrderItemUnit.id == RefundLineUnit.order_item_unit_id)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .where(OrderItem.order_id == order_id, RefundLineUnit.occupied == UNIT_OCCUPIED)
    )
    occupied = dict(db.execute(occupied_stmt).tuples().all())

    refunded_stmt = select(func.coalesce(func.sum(RefundRequest.amount_sen), 0)).where(
        RefundRequest.order_id == order_id,
        RefundRequest.status == REFUND_APPROVED,
    )
    refunded = int(db.scalar(refunded_stmt) or 0)

    refundable: dict[int, list[RefundUnit]] = {item_id: [] for item_id, _ in items}
    for unit_id, item_id, unit_index, cash_paid in units:
        if unit_id not in occupied:
            refundable[item_id].append(RefundUnit(unit_id, unit_index, cash_paid))

    lines = tuple(
        LineRefundState(item_id, line_index, tuple(refundable[item_id]))
        for item_id, line_index in items
    )
    left = sum(unit.cash_paid_sen for line in lines for unit in line.refundable)
    fully = bool(units) and all(occupied.get(unit_id) == REFUND_APPROVED for unit_id, *_ in units)
    return RefundState(
        lines=lines,
        refunded_sen=refunded,
        refundable_left_sen=left,
        fully_refunded=fully,
    )


def refund_records(db: Session, order_id: int) -> list[RefundRecord]:
    """该单的退款申请，按创建时间从新到旧（同时创建按 ID 从大到小）；只读。"""
    request_stmt = (
        select(RefundRequest)
        .where(RefundRequest.order_id == order_id)
        .order_by(RefundRequest.created_at.desc(), RefundRequest.id.desc())
        .execution_options(populate_existing=True)
    )
    requests = list(db.scalars(request_stmt))
    if not requests:
        return []

    line_stmt = (
        select(OrderItem, RefundLine)
        .join(OrderItem, OrderItem.id == RefundLine.order_item_id)
        .where(RefundLine.refund_request_id.in_([request.id for request in requests]))
        .order_by(RefundLine.refund_request_id, OrderItem.line_index)
    )
    lines: dict[int, list[tuple[OrderItem, RefundLine]]] = {req.id: [] for req in requests}
    for item, line in db.execute(line_stmt).tuples():
        lines[line.refund_request_id].append((item, line))
    return [RefundRecord(request, tuple(lines[request.id])) for request in requests]


# ---------------------------------------------------------------------------
# 提交申请
# ---------------------------------------------------------------------------


def submit_refund(
    db: Session,
    order_id: int,
    lines: Sequence[RefundLineRequest],
    idempotency_key: str,
    now: datetime,
) -> tuple[RefundOutcome, bool]:
    """游客提交退款申请，返回申请结果与是否新写了申请（重放为假）。

    lines 的行序不重复、件数为正，由调用方校验。按以下顺序，每一步不通过即停止：
    1. 按幂等键查申请：指纹相同返回原申请，不同抛 IdempotencyConflict；都不再写入。
    2. 订单未支付（待支付或已取消）抛 OrderNotRefundable。
    3. 当前时间晚于退款截止时间抛 RefundWindowClosed。
    4. 请求里的行序不属于该订单抛 UnknownOrderLine。
    5. 该单已没有任何可退件抛 RefundNothingLeft。
    6. 任一行申请件数超过该行可退件数抛 RefundDuplicate；整笔不受理、不部分受理。
    7. 同一事务里写申请（requested、guest）、各申请行与逐件记录（占用标记 1）并提交。
    写入撞上唯一约束（同键，或同一件已被另一笔有效申请占用）时回滚，再按幂等键重新查询：
    同键同指纹重放，同键不同指纹抛 IdempotencyConflict，否则按重新读取的可退件抛
    RefundNothingLeft 或 RefundDuplicate。所以同一件不会被两笔有效申请占用。
    """
    fingerprint = refund_fingerprint(order_id, lines)
    existing = _request_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint), False

    order = _load_order(db, order_id)
    if order.status not in PAID_STATUSES or order.paid_at is None:
        raise OrderNotRefundable
    if not in_refund_window(order.paid_at, now):
        raise RefundWindowClosed

    state = refund_state(db, order_id)
    line_states = []
    for line in lines:
        line_state = state.line(line.line_index)
        if line_state is None:
            raise UnknownOrderLine
        line_states.append(line_state)
    if state.nothing_left:
        raise RefundNothingLeft
    if any(
        line.quantity > len(line_state.refundable)
        for line, line_state in zip(lines, line_states, strict=True)
    ):
        raise RefundDuplicate

    pairs = sorted(zip(lines, line_states, strict=True), key=lambda pair: pair[0].line_index)
    chosen = [
        (line_state, pick_units(line_state.refundable, line.quantity)) for line, line_state in pairs
    ]
    request = RefundRequest(
        order_id=order_id,
        status=REFUND_REQUESTED,
        actor_type=ACTOR_GUEST,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        amount_sen=sum(unit.cash_paid_sen for _, picked in chosen for unit in picked),
        created_at=now,
        reviewed_at=None,
    )
    db.add(request)
    try:
        db.flush()
        for line_state, picked in chosen:
            refund_line = RefundLine(
                refund_request_id=request.id,
                order_item_id=line_state.order_item_id,
                quantity=len(picked),
                amount_sen=sum(unit.cash_paid_sen for unit in picked),
            )
            db.add(refund_line)
            db.flush()
            for unit in picked:
                db.add(
                    RefundLineUnit(
                        refund_line_id=refund_line.id,
                        order_item_unit_id=unit.unit_id,
                        cash_paid_sen=unit.cash_paid_sen,
                        occupied=UNIT_OCCUPIED,
                    )
                )
        db.flush()
    except IntegrityError:
        # 同键或争用同一件的并发申请先提交了：回滚本次的改动，按它的结果回答。
        db.rollback()
        return _after_lost_race(db, order_id, idempotency_key, fingerprint), False

    outcome = RefundOutcome(
        status=REFUND_REQUESTED,
        amount_sen=request.amount_sen,
        lines=[
            RefundOutcomeLine(line_index=line_state.line_index, quantity=len(picked))
            for line_state, picked in chosen
        ],
    )
    db.commit()
    return outcome, True


def _load_order(db: Session, order_id: int) -> Order:
    # populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _request_by_key(db: Session, idempotency_key: str) -> RefundRequest | None:
    stmt = (
        select(RefundRequest)
        .where(RefundRequest.idempotency_key == idempotency_key)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one_or_none()


def _replay(db: Session, request: RefundRequest, fingerprint: str) -> RefundOutcome:
    """同键同请求返回原申请（当前状态、金额与各行件数）；同键不同请求报冲突。都不写入。"""
    if not hmac.compare_digest(request.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    line_stmt = (
        select(OrderItem.line_index, RefundLine.quantity)
        .join(OrderItem, OrderItem.id == RefundLine.order_item_id)
        .where(RefundLine.refund_request_id == request.id)
        .order_by(OrderItem.line_index)
    )
    return RefundOutcome(
        status=request.status,
        amount_sen=request.amount_sen,
        lines=[
            RefundOutcomeLine(line_index=line_index, quantity=quantity)
            for line_index, quantity in db.execute(line_stmt).tuples()
        ],
    )


def _after_lost_race(
    db: Session, order_id: int, idempotency_key: str, fingerprint: str
) -> RefundOutcome:
    """回滚之后：同键已提交则重放或冲突，否则按重新读取的可退件拒绝。"""
    existing = _request_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint)
    if refund_state(db, order_id).nothing_left:
        raise RefundNothingLeft
    raise RefundDuplicate

"""后台订单列表、订单详情（含原始收货资料并写审计）与模拟发货推进。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：管理员依次推进 demo_paid → demo_packed → demo_shipped；
  所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，保留退款前履约状态；
  所有状态迁移校验当前状态、操作者与数量，事务内写订单与事件。
- 「权限与资料保护」：仅管理员可见所有订单原始资料、推进发货；收货资料访问受服务端
  权限控制并留审计记录，首版不提供后台导出；日志不记录姓名、完整电话、地址与订单
  查询参数。
- 「数据模型」PaymentAttempt / OrderEvent：事件不写收货资料原文。
以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-05 决定：后台订单列表按下单时间从新到旧、
每页 20 条，订单号搜索先按查单的规则规范化再精确匹配；全部退款后冻结履约按
SHOP-TASK-029 的全部已退判定。

复用而不复制：订单号规范化与订单行、金额的组装用 app/services/order_lookup.py 的
normalize_order_number 与 lookup_orders（全部已退由它调用 SHOP-TASK-029 的
refund_state 算出）；迁移判定用 SHOP-TASK-010 的 is_transition_allowed；推进前的
全部已退判定直接用 refund_state；审计用 SHOP-TASK-035 的 record_audit。

锁定协议（推进与之后的退款审核共用）：推进在同一事务里先以 SELECT … FOR UPDATE
（lock_order_statement，即 SQLAlchemy 的 with_for_update）锁定该订单行，之后才读取
当前状态与退款占用并判断；通过时用带条件的 UPDATE（状态仍为检查时的状态才改）改状态、
写事件与审计并提交。之后的退款审核任务在批准退款前必须以同一查询锁定同一订单行，
再读取退款占用并写入批准。两者因此串行化：不会出现推进读到「未全部已退」、并发的
批准同时把最后几件批准、两边都提交的情形。条件 UPDATE 另作兜底：未命中时回滚、
重新读取订单，不出现一单两次推进。

访问授权、CSRF 与请求格式由调用方（app/api/admin_orders.py）先行校验；这里只按订单
内部 ID 操作，操作者固定为 admin。本模块不写日志；异常消息不含订单号、收货资料、
用户名或令牌。库里的时间一律是不带时区的 UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.models import Order, OrderEvent, RefundRequest
from app.models.order import ACTOR_ADMIN
from app.models.refund import REFUND_REQUESTED
from app.services.admin_auth import record_audit
from app.services.catalog import Language
from app.services.order_lookup import lookup_orders, normalize_order_number
from app.services.order_rules import is_transition_allowed, is_valid_order_number
from app.services.payment import PayRecipient
from app.services.refunds import refund_state

# 审计操作名与对象类别。
ADMIN_ORDER_VIEWED = "admin_order_viewed"
ADMIN_ORDER_STATUS_CHANGED = "admin_order_status_changed"
AUDIT_TARGET_ORDER = "order"

PAGE_SIZE = 20
MAX_PAGE = 10000

# 「订单与退款状态」的六种订单状态，与 app/models/order.py 的 ORDER_STATUSES 相同。
OrderStatus = Literal[
    "awaiting_demo_payment",
    "demo_paid",
    "demo_packed",
    "demo_shipped",
    "demo_completed",
    "demo_cancelled",
]
# 管理员可推进到的目标状态。
AdvanceTarget = Literal["demo_packed", "demo_shipped"]


class OrderNotFound(Exception):
    """订单内部 ID 不存在。"""


class OrderNotAdvanceable(Exception):
    """迁移判定不允许从当前状态推进到目标状态；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class FulfilmentFrozen(Exception):
    """全部件都已批准退款，履约冻结；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class AdminOrderRow(BaseModel):
    """列表的一行，不含收货资料。时间带 UTC 时区。"""

    id: int
    order_number: str
    created_at: datetime
    status: str
    total_sen: int
    # 审核中（requested）的退款申请数。
    refunds_pending: int


class AdminOrderPage(BaseModel):
    """符合条件的订单总数与本页各单（按下单时间从新到旧，同时下单按内部 ID 从大到小）。"""

    total: int
    page: int
    page_size: int
    orders: list[AdminOrderRow]


class AdminOrderLine(BaseModel):
    """订单行快照：按请求语言取的名称与规格说明、件数、单价、行小计；逐件实付按件序。"""

    name: str
    variant_label: str
    quantity: int
    unit_price_sen: int
    line_subtotal_sen: int
    unit_cash_paid_sen: list[int]


class AdminOrderEvent(BaseModel):
    """一条状态事件：时间（带 UTC 时区）、迁移后状态与操作者类别；不含收货资料。"""

    created_at: datetime
    status: str
    actor_type: str


class AdminOrderDetail(BaseModel):
    """一张订单的后台详情。时间都带 UTC 时区。"""

    id: int
    order_number: str
    status: str
    created_at: datetime
    # 未支付为空。
    paid_at: datetime | None
    # 按行序。
    lines: list[AdminOrderLine]
    subtotal_sen: int
    # UX A02「金额明细」的券折扣与积分抵扣两行（后台对游客订单也照常显示）；
    # 积分抵扣为订单的 points_redeemed，1 积分抵 1 仙。
    coupon_discount_sen: int
    points_discount_sen: int
    shipping_fee_sen: int
    total_sen: int
    # 收货资料原文；订单摘要不依赖收货资料记录存在，没有那一条时为空。
    recipient: PayRecipient | None
    # 按时间从早到晚，同一时间按事件 ID 从小到大。
    events: list[AdminOrderEvent]
    refunds_pending: int
    # 全部件都已批准退款：冻结履约推进。
    fully_refunded: bool


class AdvanceOutcome(BaseModel):
    status: str


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def pending_refund_counts(db: Session, order_ids: list[int]) -> dict[int, int]:
    """这些订单各自审核中（requested）的退款申请数；没有的不在字典里。只读。"""
    if not order_ids:
        return {}
    stmt = (
        select(RefundRequest.order_id, func.count())
        .where(RefundRequest.order_id.in_(order_ids), RefundRequest.status == REFUND_REQUESTED)
        .group_by(RefundRequest.order_id)
    )
    return {order_id: int(count) for order_id, count in db.execute(stmt).tuples()}


def query_orders(
    db: Session,
    order_number: str | None,
    status: str | None,
    page: int,
) -> AdminOrderPage:
    """后台订单列表；只读，不写审计。

    按下单时间从新到旧、同时下单按内部 ID 从大到小，每页 PAGE_SIZE 条。
    订单号先按查单的规则规范化，再与订单号精确比较；规范化后格式不合法时返回空列表。
    状态与页码（1 到 MAX_PAGE）由调用方校验。
    """
    empty = AdminOrderPage(total=0, page=page, page_size=PAGE_SIZE, orders=[])
    conditions = []
    if order_number is not None:
        number = normalize_order_number(order_number)
        if not is_valid_order_number(number):
            return empty
        conditions.append(Order.order_number == number)
    if status is not None:
        conditions.append(Order.status == status)

    total = int(db.scalar(select(func.count()).select_from(Order).where(*conditions)) or 0)
    if total == 0:
        return empty
    stmt = (
        select(Order.id, Order.order_number, Order.created_at, Order.status, Order.total_sen)
        .where(*conditions)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    )
    rows = list(db.execute(stmt).tuples())
    pending = pending_refund_counts(db, [row[0] for row in rows])
    orders = [
        AdminOrderRow(
            id=order_id,
            order_number=row_number,
            created_at=_utc(created_at),
            status=order_status,
            total_sen=total_sen,
            refunds_pending=pending.get(order_id, 0),
        )
        for order_id, row_number, created_at, order_status, total_sen in rows
    ]
    return AdminOrderPage(total=total, page=page, page_size=PAGE_SIZE, orders=orders)


def order_detail(
    db: Session, order_id: int, lang: Language, now: datetime
) -> AdminOrderDetail | None:
    """一张订单的后台详情；订单不存在时为空。只读，不写审计。

    订单行、金额、收货资料与全部已退取自 lookup_orders 的查单视图，不另行组装；
    查单视图不含的券折扣与积分抵扣直接取订单上的快照列。
    """
    views = lookup_orders(db, [order_id], lang, now)
    if not views:
        return None
    view = views[0]
    discount_stmt = select(Order.coupon_discount_sen, Order.points_redeemed).where(
        Order.id == order_id
    )
    coupon_discount_sen, points_redeemed = db.execute(discount_stmt).tuples().one()

    event_stmt = (
        select(OrderEvent.created_at, OrderEvent.to_status, OrderEvent.actor_type)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.created_at, OrderEvent.id)
    )
    events = [
        AdminOrderEvent(created_at=_utc(created_at), status=to_status, actor_type=actor_type)
        for created_at, to_status, actor_type in db.execute(event_stmt).tuples()
    ]
    return AdminOrderDetail(
        id=order_id,
        order_number=view.order_number,
        status=view.status,
        created_at=view.created_at,
        paid_at=view.paid_at,
        lines=[
            AdminOrderLine(
                name=line.name,
                variant_label=line.variant_label,
                quantity=line.quantity,
                unit_price_sen=line.unit_price_sen,
                line_subtotal_sen=line.line_subtotal_sen,
                unit_cash_paid_sen=line.unit_cash_paid_sen,
            )
            for line in view.lines
        ],
        subtotal_sen=view.subtotal_sen,
        coupon_discount_sen=coupon_discount_sen,
        points_discount_sen=points_redeemed,
        shipping_fee_sen=view.shipping_fee_sen,
        total_sen=view.total_sen,
        recipient=view.recipient,
        events=events,
        refunds_pending=pending_refund_counts(db, [order_id]).get(order_id, 0),
        fully_refunded=view.fully_refunded,
    )


def view_order(
    db: Session,
    admin_account_id: int,
    order_id: int,
    lang: Language,
    now: datetime,
) -> AdminOrderDetail | None:
    """管理员查看订单详情；订单不存在时为空，不写审计。

    成功时在同一事务里写一条 admin_order_viewed 审计（对象为该单内部 ID，不写旧值与
    新值）并提交，然后才返回。
    """
    detail = order_detail(db, order_id, lang, now)
    if detail is None:
        return None
    record_audit(
        db,
        ADMIN_ORDER_VIEWED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_ORDER,
        target_id=order_id,
    )
    db.commit()
    return detail


def lock_order_statement(order_id: int) -> Select[tuple[Order]]:
    """锁定订单行的查询（SELECT … FOR UPDATE）。退款审核批准前须用它锁定同一行。"""
    return (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def _lock_order(db: Session, order_id: int) -> Order | None:
    return db.scalars(lock_order_statement(order_id)).one_or_none()


def _current_status(db: Session, order_id: int) -> str:
    stmt = select(Order.status).where(Order.id == order_id)
    return db.scalars(stmt.execution_options(populate_existing=True)).one()


def advance_order(
    db: Session,
    admin_account_id: int,
    order_id: int,
    target: str,
    now: datetime,
) -> tuple[AdvanceOutcome, bool]:
    """管理员推进订单状态，返回订单状态与是否实际推进（已是目标状态为假）。

    同一事务里先锁定订单行，再按以下顺序判断，每一步不通过即回滚（释放锁）并停止：
    1. 订单不存在抛 OrderNotFound。
    2. 已是目标状态：返回当前状态，不写事件与审计（重复点击安全）。
    3. 迁移判定以操作者 admin 不允许（含跳过打包、待支付、已完成、已取消）时抛
       OrderNotAdvanceable。
    4. 读取退款占用，全部已退抛 FulfilmentFrozen，即使迁移本身允许。
    5. 带条件的 UPDATE（状态仍为检查时的状态才改）改状态，写一条操作者为 admin 的事件
       与一条 admin_order_status_changed 审计（旧值、新值为迁移前后的状态）并提交。
    UPDATE 未命中时回滚、重新读取订单：已是目标状态返回它（不算推进），否则抛
    OrderNotAdvanceable 与当前状态。所以同一步至多推进一次。
    """
    order = _lock_order(db, order_id)
    if order is None:
        db.rollback()
        raise OrderNotFound
    current = order.status
    if current == target:
        db.rollback()
        return AdvanceOutcome(status=current), False
    if not is_transition_allowed(current, target, ACTOR_ADMIN):
        db.rollback()
        raise OrderNotAdvanceable(current)
    if refund_state(db, order_id).fully_refunded:
        db.rollback()
        raise FulfilmentFrozen(current)

    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == current)
        .values(status=target)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        status = _current_status(db, order_id)
        if status == target:
            return AdvanceOutcome(status=status), False
        raise OrderNotAdvanceable(status)

    db.add(
        OrderEvent(
            order_id=order_id,
            from_status=current,
            to_status=target,
            actor_type=ACTOR_ADMIN,
            created_at=now,
        )
    )
    record_audit(
        db,
        ADMIN_ORDER_STATUS_CHANGED,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_ORDER,
        target_id=order_id,
        old_value=current,
        new_value=target,
    )
    db.commit()
    return AdvanceOutcome(status=target), True

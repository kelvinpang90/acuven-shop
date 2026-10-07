"""后台退款申请列表与详情（只读，不写审计）。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：requested → 管理员 approved 或 rejected；系统显示累计已退及
  剩余可退金额；示例运费始终不退。
- 「权限与资料保护」：仅管理员可见所有订单原始资料；日志不记录姓名、完整电话、地址与
  订单查询参数。退款审核用不到收货资料，这里一律不读收货资料。
以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定：审核理由只给管理员看
（本模块的视图只供后台接口使用）；0.35 记录的同日决定：列表可按订单内部 ID 筛选，供 A02
订单的待审退款数链接到只含该单申请的 A03 列表（SHOP-TASK-046）。

复用而不复制：累计已退与剩余可退用 SHOP-TASK-029 的 refund_state；分页常量沿用
app/services/admin_orders.py。批准与拒绝在 app/services/refund_review.py（SHOP-TASK-040）。

访问授权由调用方（app/api/admin_refunds.py）先行校验；这里只按申请的内部 ID 操作。
本模块不写日志。库里的时间一律是不带时区的 UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AdminAccount, Order, OrderItem, RefundLine, RefundRequest
from app.models.refund import REFUND_APPROVED
from app.services.admin_orders import PAGE_SIZE
from app.services.catalog import Language
from app.services.refunds import refund_state

# 「订单与退款状态」的三种申请状态，与 app/models/refund.py 的 REFUND_STATUSES 相同。
RefundStatus = Literal["requested", "approved", "rejected"]


class AdminRefundRowLine(BaseModel):
    """列表一行里的申请行：按请求语言取的商品名称快照与申请件数。"""

    name: str
    quantity: int


class AdminRefundRow(BaseModel):
    """列表的一行，不含收货资料与理由。时间带 UTC 时区。"""

    id: int
    created_at: datetime
    order_id: int
    order_number: str
    status: str
    amount_sen: int
    # 按订单行序。
    lines: list[AdminRefundRowLine]


class AdminRefundPage(BaseModel):
    """符合条件的申请总数与本页各申请（按申请时间从新到旧，同时申请按 ID 从大到小）。"""

    total: int
    page: int
    page_size: int
    refunds: list[AdminRefundRow]


class AdminRefundLine(BaseModel):
    """详情的一行：名称与规格说明快照、申请件数、该行购买件数与该行已批准件数。"""

    name: str
    variant_label: str
    quantity: int
    purchased_quantity: int
    # 该订单行被 approved 申请占用的件数（含本申请，若它已批准）。
    approved_quantity: int


class AdminRefundDetail(BaseModel):
    """一笔退款申请的后台详情，不含收货资料。时间都带 UTC 时区。"""

    id: int
    status: str
    created_at: datetime
    # 未审核为空。
    reviewed_at: datetime | None
    reviewer_username: str | None
    # 只给管理员看；批准时没有理由、未审核时为空。
    review_reason: str | None
    order_id: int
    order_number: str
    order_status: str
    amount_sen: int
    # 按订单行序。
    lines: list[AdminRefundLine]
    # 该单累计已退与剩余可退（SHOP-TASK-029 的 refund_state）。
    refunded_sen: int
    refundable_left_sen: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def query_refunds(
    db: Session,
    status: str | None,
    page: int,
    lang: Language,
    *,
    order_id: int | None = None,
) -> AdminRefundPage:
    """后台退款申请列表；只读，不写审计。

    按申请时间从新到旧、同时申请按 ID 从大到小，每页 PAGE_SIZE 条。
    状态、页码（1 到 10000）、订单内部 ID 与语言由调用方校验。给出 order_id 时只列该订单的
    申请（docs/HANDOFF.md 0.35 记录的 Kelvin 2026-10-06 决定，SHOP-TASK-046）；订单不存在
    或没有申请时与其他无结果的筛选一样是总数为 0 的空列表。
    """
    conditions = [] if status is None else [RefundRequest.status == status]
    if order_id is not None:
        conditions.append(RefundRequest.order_id == order_id)
    total = int(db.scalar(select(func.count()).select_from(RefundRequest).where(*conditions)) or 0)
    if total == 0:
        return AdminRefundPage(total=0, page=page, page_size=PAGE_SIZE, refunds=[])
    stmt = (
        select(
            RefundRequest.id,
            RefundRequest.created_at,
            RefundRequest.order_id,
            Order.order_number,
            RefundRequest.status,
            RefundRequest.amount_sen,
        )
        .join(Order, Order.id == RefundRequest.order_id)
        .where(*conditions)
        .order_by(RefundRequest.created_at.desc(), RefundRequest.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    )
    rows = list(db.execute(stmt).tuples())

    lines: dict[int, list[AdminRefundRowLine]] = {row[0]: [] for row in rows}
    if rows:
        line_stmt = (
            select(RefundLine.refund_request_id, OrderItem, RefundLine.quantity)
            .join(OrderItem, OrderItem.id == RefundLine.order_item_id)
            .where(RefundLine.refund_request_id.in_(list(lines)))
            .order_by(RefundLine.refund_request_id, OrderItem.line_index)
        )
        for request_id, item, quantity in db.execute(line_stmt).tuples():
            name = getattr(item, f"product_name_{lang}")
            lines[request_id].append(AdminRefundRowLine(name=name, quantity=quantity))

    refunds = [
        AdminRefundRow(
            id=request_id,
            created_at=_utc(created_at),
            order_id=order_id,
            order_number=order_number,
            status=request_status,
            amount_sen=amount_sen,
            lines=lines[request_id],
        )
        for request_id, created_at, order_id, order_number, request_status, amount_sen in rows
    ]
    return AdminRefundPage(total=total, page=page, page_size=PAGE_SIZE, refunds=refunds)


def refund_detail(db: Session, refund_request_id: int, lang: Language) -> AdminRefundDetail | None:
    """一笔退款申请的后台详情；申请不存在时为空。只读，不写审计。"""
    stmt = (
        select(RefundRequest, Order.order_number, Order.status, AdminAccount.username)
        .join(Order, Order.id == RefundRequest.order_id)
        .outerjoin(AdminAccount, AdminAccount.id == RefundRequest.reviewer_admin_id)
        .where(RefundRequest.id == refund_request_id)
    )
    found = db.execute(stmt).tuples().one_or_none()
    if found is None:
        return None
    request, order_number, order_status, reviewer_username = found

    line_stmt = (
        select(OrderItem, RefundLine.quantity)
        .join(OrderItem, OrderItem.id == RefundLine.order_item_id)
        .where(RefundLine.refund_request_id == refund_request_id)
        .order_by(OrderItem.line_index)
    )
    line_rows = list(db.execute(line_stmt).tuples())
    approved = _approved_quantities(db, [item.id for item, _ in line_rows])
    state = refund_state(db, request.order_id)

    return AdminRefundDetail(
        id=request.id,
        status=request.status,
        created_at=_utc(request.created_at),
        reviewed_at=None if request.reviewed_at is None else _utc(request.reviewed_at),
        reviewer_username=reviewer_username,
        review_reason=request.review_reason,
        order_id=request.order_id,
        order_number=order_number,
        order_status=order_status,
        amount_sen=request.amount_sen,
        lines=[
            AdminRefundLine(
                name=getattr(item, f"product_name_{lang}"),
                variant_label=getattr(item, f"variant_label_{lang}"),
                quantity=quantity,
                purchased_quantity=item.quantity,
                approved_quantity=approved.get(item.id, 0),
            )
            for item, quantity in line_rows
        ],
        refunded_sen=state.refunded_sen,
        refundable_left_sen=state.refundable_left_sen,
    )


def _approved_quantities(db: Session, order_item_ids: list[int]) -> dict[int, int]:
    """这些订单行各自被 approved 申请占用的件数；没有的不在字典里。"""
    if not order_item_ids:
        return {}
    stmt = (
        select(RefundLine.order_item_id, func.sum(RefundLine.quantity))
        .join(RefundRequest, RefundRequest.id == RefundLine.refund_request_id)
        .where(
            RefundLine.order_item_id.in_(order_item_ids),
            RefundRequest.status == REFUND_APPROVED,
        )
        .group_by(RefundLine.order_item_id)
    )
    return {item_id: int(count) for item_id, count in db.execute(stmt).tuples()}

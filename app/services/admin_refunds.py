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

详情的积分返还与追回（SHOP-TASK-106）依据 DESIGN 1.11「计价、优惠、积分与库存」
第 5 条（退款批准时返还未过期的抵扣积分并追回获得积分，余额不足记待抵扣）、
docs/UX.md 0.10 的 A03（详情显示 admin.refund_points）与 docs/HANDOFF.md 0.46：
- placed_by_member：订单的 claim_status 为 not_claimable（会员下单；注销后仍为真，
  认领的游客订单为假），与 app/services/order_lookup.py 的同名字段相同。
- approved：取该申请的实际积分流水。points_returned 为 refund_return 之和，
  points_reversed 为 refund_reversal 之和取负加 debt_add 之和（从余额追回与记入
  待抵扣之和）；points_estimated 为假。游客订单与注销后批准的订单没有流水，为 0。
- requested：以该申请各件逐件快照的 points_discount 与 points_earned 之和调用
  SHOP-TASK-087 的 estimate_refund_points（订单 member_id 为空或会员不是 active 时
  为零）；points_returned 为预估的实际返还，points_reversed 为预估的 reversed_total，
  points_estimated 为真。预估与批准时可能因到期或余额变化而不同，以批准时为准。
- rejected：三个积分字段都为空。

详情的 Stripe 付款与退款尝试（SHOP-TASK-130）依据 DESIGN 1.12（提交 acf576e）「Stripe
测试环境支付」第 8 条（「后台退款详情显示各次尝试的 Stripe 退款状态」；「后台退款详情按
记下的实际付款方式类型显示期限提示」；「最新一次尝试为 failed 时管理员可手动重试」）与
第 10 条（不读取、不保存 Stripe 返回的客户资料），docs/UX.md 0.11 的 A03「Stripe 测试
（0.11）」与 docs/HANDOFF.md 0.55。期限提示与能否重试由页面从下面三个字段推出，这里不另给：
- paid_via_stripe：订单「由 Stripe 付款」，与 app/services/stripe_refund.py 的
  add_request_attempt 判定相同，即有确认时间非空的会话记录（至多一条）；经模拟支付的订单
  （没有记录，或只有 expired、未确认的记录）为假。三种申请状态的取值规则相同。
- payment_method_type：该记录的实际付款方式类型原样（尚未补齐时为空）；不是 Stripe 付款
  时为空。
- stripe_refunds：来源为本申请的尝试，按尝试 ID 从大到小（最新在前），只给 ID、创建时间、
  金额、状态与 Stripe 退款 ID；来源为会话的尝试与同一订单其他申请的尝试不在其中。错误
  类别、调用次数、告警与复查时间、PaymentIntent 与会话 ID 不读也不返回。

访问授权由调用方（app/api/admin_refunds.py）先行校验；这里只按申请的内部 ID 操作。
本模块不写日志。库里的时间一律是不带时区的 UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AdminAccount,
    Order,
    OrderItem,
    OrderItemUnit,
    PointEntry,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
    StripeCheckout,
    StripeRefund,
)
from app.models.order import CLAIM_NOT_CLAIMABLE
from app.models.points import KIND_DEBT_ADD, KIND_REFUND_RETURN, KIND_REFUND_REVERSAL
from app.models.refund import REFUND_APPROVED, REFUND_REQUESTED
from app.services.admin_orders import PAGE_SIZE
from app.services.catalog import Language
from app.services.points_refund import estimate_refund_points
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


class AdminRefundStripeRefund(BaseModel):
    """详情里的一次 Stripe 退款尝试。时间带 UTC 时区。"""

    id: int
    created_at: datetime
    amount_sen: int
    # pending、succeeded 或 failed。
    status: str
    # 调用结果未知或 Stripe 明确拒绝创建时为空。
    stripe_refund_id: str | None


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
    # 订单的 claim_status 为 not_claimable（会员下单）。
    placed_by_member: bool
    # 积分返还与追回（SHOP-TASK-106）：approved 为实际流水，requested 为预估，
    # rejected 都为空。points_reversed 为从余额追回与记入待抵扣之和。
    points_returned: int | None
    points_reversed: int | None
    points_estimated: bool | None
    # Stripe 付款与退款尝试（SHOP-TASK-130）：订单有确认时间非空的会话记录时为真，
    # 付款方式类型取该记录（尚未补齐或不是 Stripe 付款时为空）；尝试只含来源为本申请的，
    # 最新在前。
    paid_via_stripe: bool
    payment_method_type: str | None
    stripe_refunds: list[AdminRefundStripeRefund]


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


def refund_detail(
    db: Session, refund_request_id: int, lang: Language, now: datetime
) -> AdminRefundDetail | None:
    """一笔退款申请的后台详情；申请不存在时为空。只读，不写审计，不锁行。

    now 为不带时区的 UTC，只用于审核中申请的积分预估（判断批次是否已过期）。
    Stripe 付款与退款尝试只读本站的记录，不调用 Stripe。
    """
    stmt = (
        select(
            RefundRequest,
            Order.order_number,
            Order.status,
            Order.claim_status,
            AdminAccount.username,
        )
        .join(Order, Order.id == RefundRequest.order_id)
        .outerjoin(AdminAccount, AdminAccount.id == RefundRequest.reviewer_admin_id)
        .where(RefundRequest.id == refund_request_id)
    )
    found = db.execute(stmt).tuples().one_or_none()
    if found is None:
        return None
    request, order_number, order_status, claim_status, reviewer_username = found

    line_stmt = (
        select(OrderItem, RefundLine.quantity)
        .join(OrderItem, OrderItem.id == RefundLine.order_item_id)
        .where(RefundLine.refund_request_id == refund_request_id)
        .order_by(OrderItem.line_index)
    )
    line_rows = list(db.execute(line_stmt).tuples())
    approved = _approved_quantities(db, [item.id for item, _ in line_rows])
    state = refund_state(db, request.order_id)
    points_returned, points_reversed, points_estimated = _refund_points(db, request, now)
    paid_via_stripe, payment_method_type = _stripe_payment(db, request.order_id)

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
        placed_by_member=claim_status == CLAIM_NOT_CLAIMABLE,
        points_returned=points_returned,
        points_reversed=points_reversed,
        points_estimated=points_estimated,
        paid_via_stripe=paid_via_stripe,
        payment_method_type=payment_method_type,
        stripe_refunds=_stripe_refunds(db, request.id),
    )


def _stripe_payment(db: Session, order_id: int) -> tuple[bool, str | None]:
    """该订单的（是否由 Stripe 付款、付款方式类型）。

    与 app/services/stripe_refund.py 的 _lock_paid_checkout 判定相同（确认时间非空的会话
    记录，至多一条），但只普通读取、不锁。没有这样的记录时为（假、空）。
    """
    stmt = select(StripeCheckout.payment_method_type).where(
        StripeCheckout.order_id == order_id, StripeCheckout.confirmed_at.is_not(None)
    )
    found = db.execute(stmt).tuples().one_or_none()
    return (False, None) if found is None else (True, found[0])


def _stripe_refunds(db: Session, refund_request_id: int) -> list[AdminRefundStripeRefund]:
    """来源为该申请的 Stripe 退款尝试，按尝试 ID 从大到小。"""
    stmt = (
        select(
            StripeRefund.id,
            StripeRefund.created_at,
            StripeRefund.amount_sen,
            StripeRefund.status,
            StripeRefund.stripe_refund_id,
        )
        .where(StripeRefund.refund_request_id == refund_request_id)
        .order_by(StripeRefund.id.desc())
    )
    rows = db.execute(stmt).tuples()
    return [
        AdminRefundStripeRefund(
            id=attempt_id,
            created_at=_utc(created_at),
            amount_sen=amount_sen,
            status=status,
            stripe_refund_id=stripe_refund_id,
        )
        for attempt_id, created_at, amount_sen, status, stripe_refund_id in rows
    ]


def _refund_points(
    db: Session, request: RefundRequest, now: datetime
) -> tuple[int | None, int | None, bool | None]:
    """该申请的（返还、追回、是否预估）；追回为从余额追回与记入待抵扣之和。"""
    if request.status == REFUND_APPROVED:
        stmt = select(PointEntry.kind, PointEntry.points).where(
            PointEntry.refund_request_id == request.id
        )
        entries = db.execute(stmt).tuples().all()
        returned = sum(points for kind, points in entries if kind == KIND_REFUND_RETURN)
        reversed_ = -sum(points for kind, points in entries if kind == KIND_REFUND_REVERSAL)
        reversed_ += sum(points for kind, points in entries if kind == KIND_DEBT_ADD)
        return returned, reversed_, False
    if request.status == REFUND_REQUESTED:
        unit_stmt = (
            select(OrderItemUnit.points_discount, OrderItemUnit.points_earned)
            .join(RefundLineUnit, RefundLineUnit.order_item_unit_id == OrderItemUnit.id)
            .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
            .where(RefundLine.refund_request_id == request.id)
        )
        rows = db.execute(unit_stmt).tuples().all()
        points_back = sum(discount for discount, _ in rows)
        points_reversed = sum(earned for _, earned in rows)
        estimate = estimate_refund_points(db, request.order_id, points_back, points_reversed, now)
        return estimate.returned, estimate.reversed_total, True
    return None, None, None


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

"""订单查询、查单模式的订单视图与确认收货。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「权限与资料保护」第 2 条：查单先按高熵订单号定位，再与该单 OrderRecipient 的规范化号码
  比对；查询成功后可见完整姓名和地址；查询接口严格限流、防批量枚举、不缓存敏感响应。
- 第 6 条：查单通过后，仅对该订单在本浏览器保持 30 分钟授权，只能查看该单、确认收货和申请
  退款，不能支付或取消，也不能替代其他订单的订单号加电话验证。
- 「失败、并发与重试」第 1 条：确认收货使用幂等键和数据库唯一约束，相同键相同请求返回原结果，
  同键不同内容报冲突；第 4 条：查单依赖 Redis 限流，Redis 不可用时拒绝请求。
- 「订单与退款状态」：访客在查单页确认收货后进入 demo_completed；所有状态迁移校验当前状态与
  操作者，事务内写订单与事件；已完成的履约状态不可倒退。

限流阈值按 Kelvin 的决定：按访客来源 10 分钟 30 次（成功失败都计）；按规范化后的订单号
1 小时失败 10 次。计数用 app/services/rate_limit.py，Redis 不可用时它抛的 RateLimitUnavailable
原样向上抛，由接口回答暂不可用。

访问授权、CSRF 与请求格式由调用方（app/api/order_lookup.py）先行校验；确认收货只按订单 ID 操作。
模拟发货满 7 天自动完成与会员访问都由之后的任务扩展，这里不预留参数。

全部退款后冻结确认收货（SHOP-TASK-040）：依据「订单与退款状态」
「所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，保留退款前履约状态」
与 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定。
订单是 demo_shipped 时再按 SHOP-TASK-029 的 refund_state 判断，
全部已退抛 FulfilmentFrozen，订单状态不变、不写确认收货记录与事件。

锁定协议（与 app/services/admin_orders.py 的推进发货、
app/services/refund_review.py 的退款审核相同）：
确认收货锁定订单行之前先结束本请求此前的数据库事务（此前只有读取，回滚即可），
使 SELECT … FOR UPDATE（lock_order_statement，即 with_for_update）
成为新事务的第一条语句。MySQL 默认的 REPEATABLE READ 下，
读取快照在事务第一条非锁定读时建立，这样锁定之后的读取（含 refund_state）
才能看到等锁期间别人提交的审核、发货或确认。确认收货与退款批准因此串行化：
不会出现确认读到「未全部已退」、并发的批准同时把最后几件批准、两边都提交的情形。

查单视图另带退款部分（SHOP-TASK-029，依据「订单与退款状态」与 docs/HANDOFF.md 0.27 的 Kelvin
退款决定）：订单行序、退款截止时间与是否在退款期内、累计已退、剩余可退、是否全部已退、每行可退
件数与预计金额列表、该单的退款申请记录；都由 app/services/refunds.py 按服务端规则算出，
不含积分相关字段（游客订单）。

本模块不写日志；异常消息不含个人资料、订单号、电话、幂等键或令牌。库里的时间一律是不带时区的
UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime

import redis
from pydantic import BaseModel
from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    ReceiptConfirmation,
)
from app.models.order import ACTOR_GUEST, STATUS_COMPLETED, STATUS_SHIPPED
from app.models.order_access import SCOPE_LOOKUP
from app.services.catalog import Language
from app.services.order_access import IssuedAccess, issue_order_access
from app.services.order_rules import is_transition_allowed, is_valid_order_number
from app.services.payment import PayRecipient
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.rate_limit import current_count, hit
from app.services.refunds import (
    RefundRecord,
    in_refund_window,
    refund_deadline,
    refund_estimates,
    refund_records,
    refund_state,
)

# 按访客来源：10 分钟窗口 30 次，成功失败都计。
SOURCE_BUCKET = "order_lookup_source"
SOURCE_LIMIT = 30
SOURCE_WINDOW_SECONDS = 10 * 60

# 按规范化后的订单号：1 小时窗口内失败已达 10 次即拒绝，电话正确也拒绝。
FAILURE_BUCKET = "order_lookup_failures"
FAILURE_LIMIT = 10
FAILURE_WINDOW_SECONDS = 60 * 60

# 电话不以加号开头时的默认地区（与结账第 1 步页面的默认国家码相同）。
DEFAULT_PHONE_REGION = "MY"

_SEPARATORS = re.compile(r"[ -]")
# Crockford Base32 的易混字符：I、L 读作 1，O 读作 0。
_LOOKALIKES = str.maketrans({"I": "1", "L": "1", "O": "0"})


class LookupRateLimited(Exception):
    """来源计数超过上限，或该订单号的失败计数已达上限。"""


class LookupNotFound(Exception):
    """订单号格式不合法、订单不存在、电话无法规范化或电话不符；四者不加区分。"""


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的确认收货请求。"""


class OrderNotConfirmable(Exception):
    """订单不是 demo_shipped，或并发的确认先提交；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class FulfilmentFrozen(Exception):
    """订单是 demo_shipped，但全部件都已批准退款，确认收货冻结；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class LookupLine(BaseModel):
    """订单行快照：行序、按请求语言取的名称与规格说明、件数、单价、行小计；逐件实付按件序。

    另带该行的可退件数与预计金额列表：第 i 项（从 1 数起）为申请 i 件时的金额，即可退件中
    件序最小的 i 件的现金实付之和；列表长度等于可退件数。
    """

    line_index: int
    name: str
    variant_label: str
    quantity: int
    unit_price_sen: int
    line_subtotal_sen: int
    unit_cash_paid_sen: list[int]
    refundable_quantity: int
    refund_estimates_sen: list[int]


class LookupRefundLine(BaseModel):
    """退款申请的一行：订单行的名称与规格说明快照（按请求语言）、件数与该行金额。"""

    name: str
    variant_label: str
    quantity: int
    amount_sen: int


class LookupRefund(BaseModel):
    """一笔退款申请：创建时间（带 UTC 时区）、状态、申请金额合计与各行（按行序）。"""

    created_at: datetime
    status: str
    amount_sen: int
    lines: list[LookupRefundLine]


class LookupOrder(BaseModel):
    """查单模式订单详情所需的一张订单。时间都带 UTC 时区。

    不含优惠券与积分两项：现有订单都是游客订单，docs/UX.md 0.7 的 P09 对游客订单不显示这两行；
    会员订单由之后的会员任务扩展。退款部分同样不含积分返还与追回。
    """

    order_number: str
    status: str
    created_at: datetime
    # 未支付为空。
    paid_at: datetime | None
    server_time: datetime
    lines: list[LookupLine]
    subtotal_sen: int
    shipping_fee_sen: int
    total_sen: int
    # 订单摘要不依赖收货资料记录存在：没有那一条时为空。
    recipient: PayRecipient | None
    # 支付时间加 30×24 小时；未支付为空。
    refund_deadline: datetime | None
    # 服务端判定：当前时间不晚于退款截止时间；未支付为假。
    refund_window_open: bool
    # 累计已退：approved 申请的金额之和。
    refunded_total_sen: int
    # 剩余可退：所有未被审核中或已批准的申请占用的件的现金实付之和。
    refundable_left_sen: int
    # 全部件都已被 approved 申请占用。
    fully_refunded: bool
    # 按创建时间从新到旧。
    refund_requests: list[LookupRefund]


class ConfirmOutcome(BaseModel):
    status: str


def normalize_order_number(raw: str) -> str:
    """查单输入的订单号：去掉空格与连字符、转大写，再把 I、L 换成 1、O 换成 0。

    不校验格式；调用方再用 is_valid_order_number 检查。
    """
    return _SEPARATORS.sub("", raw).upper().translate(_LOOKALIKES)


def lookup_order(
    db: Session,
    client: redis.Redis,
    source: str,
    order_number: str,
    phone: str,
    cookie_value: str | None,
    now: datetime,
) -> IssuedAccess:
    """凭订单号与电话查单，通过时签发该单 lookup 范围的授权并提交。

    按以下顺序，每一步不通过即停止：
    1. 按来源计一次，超过上限抛 LookupRateLimited。
    2. 规范化订单号，格式不合法抛 LookupNotFound（不计失败）。
    3. 读该订单号的失败计数，已达上限抛 LookupRateLimited。
    4. 按订单号查订单，以默认地区 MY 规范化电话，与该单收货资料的电话常量时间比较；
       订单不存在、电话无法规范化或不符时给该订单号的失败计数加一，再抛 LookupNotFound。
    5. 在同一事务里签发授权并提交。查单不改订单状态，任何状态的订单都可查。

    Redis 不可用时 RateLimitUnavailable 原样抛出：发生在 1、3 时还没有查订单；
    发生在 4 的失败计数加一时不签发授权。
    """
    if not hit(client, SOURCE_BUCKET, source, SOURCE_LIMIT, SOURCE_WINDOW_SECONDS):
        raise LookupRateLimited
    number = normalize_order_number(order_number)
    if not is_valid_order_number(number):
        raise LookupNotFound
    if current_count(client, FAILURE_BUCKET, number) >= FAILURE_LIMIT:
        raise LookupRateLimited

    stmt = (
        select(Order.id, OrderRecipient.phone)
        .outerjoin(OrderRecipient, OrderRecipient.order_id == Order.id)
        .where(Order.order_number == number)
    )
    row = db.execute(stmt).tuples().one_or_none()
    order_id, stored = (None, None) if row is None else row
    try:
        given = normalize_phone(phone, DEFAULT_PHONE_REGION).e164
    except InvalidPhoneNumber:
        given = None
    # 订单不存在或电话无法规范化时也做一次比较，各情形耗时相近。
    matched = hmac.compare_digest((given or "").encode(), (stored or "").encode())
    if order_id is None or given is None or stored is None or not matched:
        hit(client, FAILURE_BUCKET, number, FAILURE_LIMIT, FAILURE_WINDOW_SECONDS)
        raise LookupNotFound

    issued = issue_order_access(db, cookie_value, order_id, SCOPE_LOOKUP, now)
    db.commit()
    return issued


def lookup_orders(
    db: Session,
    order_ids: list[int],
    lang: Language,
    now: datetime,
) -> list[LookupOrder]:
    """按 order_ids 的顺序返回这些订单的查单视图；只读。订单行按行序，逐件实付按件序。"""
    if not order_ids:
        return []
    order_stmt = select(Order).where(Order.id.in_(order_ids))
    order_stmt = order_stmt.execution_options(populate_existing=True)
    orders = {order.id: order for order in db.scalars(order_stmt)}

    item_stmt = select(OrderItem).where(OrderItem.order_id.in_(order_ids))
    items = list(db.scalars(item_stmt.order_by(OrderItem.order_id, OrderItem.line_index)))

    unit_paid: dict[int, list[int]] = {item.id: [] for item in items}
    if items:
        unit_stmt = (
            select(OrderItemUnit.order_item_id, OrderItemUnit.cash_paid_sen)
            .where(OrderItemUnit.order_item_id.in_(list(unit_paid)))
            .order_by(OrderItemUnit.order_item_id, OrderItemUnit.unit_index)
        )
        for item_id, cash_paid in db.execute(unit_stmt).tuples():
            unit_paid[item_id].append(cash_paid)

    states = {order_id: refund_state(db, order_id) for order_id in orders}

    lines: dict[int, list[LookupLine]] = {order_id: [] for order_id in order_ids}
    for item in items:
        line_state = states[item.order_id].line(item.line_index)
        refundable = () if line_state is None else line_state.refundable
        line = LookupLine(
            line_index=item.line_index,
            name=getattr(item, f"product_name_{lang}"),
            variant_label=getattr(item, f"variant_label_{lang}"),
            quantity=item.quantity,
            unit_price_sen=item.unit_price_sen,
            line_subtotal_sen=item.line_subtotal_sen,
            unit_cash_paid_sen=unit_paid[item.id],
            refundable_quantity=len(refundable),
            refund_estimates_sen=refund_estimates(refundable),
        )
        lines[item.order_id].append(line)

    recipient_stmt = select(OrderRecipient).where(OrderRecipient.order_id.in_(order_ids))
    recipients = {row.order_id: row for row in db.scalars(recipient_stmt)}

    views = []
    for order_id in order_ids:
        order = orders.get(order_id)
        if order is None:
            continue
        recipient = recipients.get(order_id)
        state = states[order_id]
        deadline = refund_deadline(order.paid_at)
        view = LookupOrder(
            order_number=order.order_number,
            status=order.status,
            created_at=_utc(order.created_at),
            paid_at=None if order.paid_at is None else _utc(order.paid_at),
            server_time=_utc(now),
            lines=lines[order_id],
            subtotal_sen=order.subtotal_sen,
            shipping_fee_sen=order.shipping_fee_sen,
            total_sen=order.total_sen,
            recipient=None if recipient is None else _recipient(recipient),
            refund_deadline=None if deadline is None else _utc(deadline),
            refund_window_open=in_refund_window(order.paid_at, now),
            refunded_total_sen=state.refunded_sen,
            refundable_left_sen=state.refundable_left_sen,
            fully_refunded=state.fully_refunded,
            refund_requests=[_refund_view(record, lang) for record in refund_records(db, order_id)],
        )
        views.append(view)
    return views


def lock_order_statement(order_id: int) -> Select[tuple[Order]]:
    """锁定订单行的查询（SELECT … FOR UPDATE）。确认收货与推进发货都用它锁定订单行。"""
    return (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def receipt_fingerprint(order_id: int) -> str:
    """确认收货请求指纹：{"order_id": 订单 ID} 的 JSON（键排序、无空白）的 SHA-256 十六进制。"""
    content = {"order_id": order_id}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def confirm_receipt(
    db: Session,
    order_id: int,
    idempotency_key: str,
    now: datetime,
) -> tuple[ConfirmOutcome, bool]:
    """访客确认收货，返回订单状态与是否新写了确认收货记录（重放为假）。

    0. 结束此前的事务（此前只有读取，回滚即可），锁定订单行，作为新事务的第一条语句。
    1. 按幂等键查确认收货记录：指纹相同返回订单当前状态，不同抛 IdempotencyConflict；都不改订单。
    2. 订单不是 demo_shipped（含已经 demo_completed）：抛 OrderNotConfirmable，不写记录与事件。
    3. 读取退款占用，全部已退抛 FulfilmentFrozen，不写记录与事件、不改订单。
    4. 经迁移判定后，同一事务里写确认收货记录（操作者 guest）、用带条件的 UPDATE（状态仍为
       demo_shipped 才改）改为 demo_completed、写一条操作者为 guest 的事件，然后提交。
    写记录撞上唯一约束（同键或同一订单已有记录）或 UPDATE 未命中时回滚，再按幂等键重新查询：
    同键同指纹重放，同键不同指纹抛 IdempotencyConflict，否则抛 OrderNotConfirmable 与当前状态。
    所以一张订单至多完成一次。
    """
    fingerprint = receipt_fingerprint(order_id)
    db.rollback()
    order = db.scalars(lock_order_statement(order_id)).one()

    existing = _confirmation_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint), False

    if order.status != STATUS_SHIPPED:
        raise OrderNotConfirmable(order.status)
    if refund_state(db, order_id).fully_refunded:
        raise FulfilmentFrozen(order.status)

    # 经 SHOP-TASK-010 的迁移判定函数检查；这条迁移在表里，不通过即编程错误。
    if not is_transition_allowed(STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_GUEST):
        raise RuntimeError("order status transition is not allowed")

    db.add(
        ReceiptConfirmation(
            order_id=order_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            actor_type=ACTOR_GUEST,
            created_at=now,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        # 同键或同一订单的并发确认先提交了：回滚本次的改动，按它的结果回答。
        db.rollback()
        return _after_lost_race(db, order_id, idempotency_key, fingerprint), False

    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == STATUS_SHIPPED)
        .values(status=STATUS_COMPLETED)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        return _after_lost_race(db, order_id, idempotency_key, fingerprint), False

    db.add(
        OrderEvent(
            order_id=order_id,
            from_status=STATUS_SHIPPED,
            to_status=STATUS_COMPLETED,
            actor_type=ACTOR_GUEST,
            created_at=now,
        )
    )
    db.commit()
    return ConfirmOutcome(status=STATUS_COMPLETED), True


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _load_order(db: Session, order_id: int) -> Order:
    # populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _confirmation_by_key(db: Session, idempotency_key: str) -> ReceiptConfirmation | None:
    column = ReceiptConfirmation.idempotency_key
    stmt = select(ReceiptConfirmation).where(column == idempotency_key)
    return db.scalars(stmt).one_or_none()


def _replay(db: Session, confirmation: ReceiptConfirmation, fingerprint: str) -> ConfirmOutcome:
    """同键同请求返回订单当前状态；同键不同请求报冲突。都不改订单。"""
    if not hmac.compare_digest(confirmation.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    return ConfirmOutcome(status=_load_order(db, confirmation.order_id).status)


def _after_lost_race(
    db: Session, order_id: int, idempotency_key: str, fingerprint: str
) -> ConfirmOutcome:
    """回滚之后：同键已提交则重放或冲突，否则按订单当前状态拒绝。"""
    existing = _confirmation_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint)
    raise OrderNotConfirmable(_load_order(db, order_id).status)


def _refund_view(record: RefundRecord, lang: Language) -> LookupRefund:
    request = record.request
    return LookupRefund(
        created_at=_utc(request.created_at),
        status=request.status,
        amount_sen=request.amount_sen,
        lines=[
            LookupRefundLine(
                name=getattr(item, f"product_name_{lang}"),
                variant_label=getattr(item, f"variant_label_{lang}"),
                quantity=line.quantity,
                amount_sen=line.amount_sen,
            )
            for item, line in record.lines
        ],
    )


def _recipient(recipient: OrderRecipient) -> PayRecipient:
    return PayRecipient(
        name=recipient.name,
        phone=recipient.phone,
        country_code=recipient.country_code,
        region=recipient.region,
        address=recipient.address,
        postal_code=recipient.postal_code,
    )

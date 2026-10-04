"""模拟支付、取消待支付订单与支付超时取消；支付页 P06 与结果页 P07 的订单视图。

依据 docs/DESIGN.md 1.10（提交 e3b3505）：
- 「订单与退款状态」：模拟支付失败仍停在 awaiting_demo_payment 并记录失败尝试，成功进入
  demo_paid；待支付订单可由下单者在支付页取消，或超时为 demo_cancelled，释放库存；
  已模拟支付订单不走取消；所有状态迁移校验当前状态与操作者，事务内写订单与事件。
- 「计价、优惠、积分与库存」第 6 条：支付成功消耗预留，失败重试期间保持预留，
  15 分钟未支付自动取消并释放。
- 「失败、并发与重试」第 1 条：模拟支付使用幂等键和数据库唯一约束，相同键相同请求返回
  原结果，同键不同内容报冲突。
- 「数据模型」的 PaymentAttempt / OrderEvent 一行：事件不写收货资料原文。

访问授权、CSRF 与请求格式由调用方（app/api/pay.py）先行校验；这里只按订单 ID 操作。
状态改动一律用带条件的 UPDATE（状态仍为 awaiting_demo_payment 才改），未命中即回滚，
所以并发的支付、取消与超时取消对同一订单只有一个生效，库存至多加回一次。加回库存时同一
规格的多行先合并件数，再按 SKU 规格 ID 从小到大逐个更新，与下单扣库存的顺序一致。
券与积分预占的释放由之后的优惠券与积分任务扩展取消与超时路径；
会员访问由之后的会员任务扩展。

本模块不写日志；异常消息不含个人资料、订单号、幂等键或令牌。库里的时间一律是不带时区的
UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderRecipient,
    PaymentAttempt,
    ProductVariant,
)
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_SYSTEM,
    PAYMENT_FAILED,
    PAYMENT_METHOD_BANK,
    PAYMENT_METHOD_CARD,
    PAYMENT_METHOD_EWALLET,
    PAYMENT_SUCCEEDED,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.services.catalog import Language
from app.services.order_rules import is_transition_allowed

PaymentMethod = Literal["card", "bank", "ewallet"]
PaymentResult = Literal["success", "failure"]
CancelledBy = Literal["self", "timeout"]

# 接口里的支付方式与结果 → 库里的值。card、bank、ewallet 对应 pay.method_card、
# pay.method_bank、pay.method_ewallet。
_METHODS: dict[str, str] = {
    "card": PAYMENT_METHOD_CARD,
    "bank": PAYMENT_METHOD_BANK,
    "ewallet": PAYMENT_METHOD_EWALLET,
}
_RESULTS: dict[str, str] = {"success": PAYMENT_SUCCEEDED, "failure": PAYMENT_FAILED}
_METHOD_NAMES = {stored: name for name, stored in _METHODS.items()}
_RESULT_NAMES = {stored: name for name, stored in _RESULTS.items()}


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的支付请求。"""


class _OrderStateError(Exception):
    """订单状态不允许本次操作；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class OrderNotPayable(_OrderStateError):
    """订单已模拟支付或已取消，或带条件的 UPDATE 未命中（并发的取消或成功先提交）。"""


class OrderExpired(_OrderStateError):
    """订单已过支付到期时间，已先按超时取消处理。"""


class OrderNotCancellable(_OrderStateError):
    """订单已模拟支付或之后的状态，按设计走退款流程。"""


class PayLine(BaseModel):
    """订单行快照：按请求语言取的名称与规格说明（下单时已按回退英文的规则取好）。"""

    name: str
    variant_label: str
    quantity: int
    unit_price_sen: int
    line_subtotal_sen: int


class PayRecipient(BaseModel):
    """收货资料原文。地区：马来西亚为州属代码，其他国家为自由文本或空。"""

    name: str
    phone: str
    country_code: str
    region: str | None
    address: str
    postal_code: str


class LastPayment(BaseModel):
    method: PaymentMethod
    result: PaymentResult
    created_at: datetime


class PayOrder(BaseModel):
    """支付页与结果页所需的一张订单。时间都带 UTC 时区。"""

    order_number: str
    status: str
    created_at: datetime
    payment_expires_at: datetime
    server_time: datetime
    lines: list[PayLine]
    subtotal_sen: int
    shipping_fee_sen: int
    total_sen: int
    # 订单摘要不依赖收货资料记录存在：没有那一条时为空。
    recipient: PayRecipient | None
    # 最近一次模拟支付；没有为空。
    last_payment: LastPayment | None
    # 已取消时的取消方：self 为下单者本人，timeout 为超时；未取消为空。
    cancelled_by: CancelledBy | None


class PaymentOutcome(BaseModel):
    """一次模拟支付：本次（重放时为原来那次）的方式与结果，订单当前状态与支付时间。"""

    method: PaymentMethod
    result: PaymentResult
    status: str
    paid_at: datetime | None


class CancelOutcome(BaseModel):
    status: str
    cancelled_by: CancelledBy | None


def payment_fingerprint(order_id: int, method: str, result: str) -> str:
    """支付请求指纹：订单 ID、支付方式与结果（接口里的取值）的 JSON 的 SHA-256 十六进制。

    JSON 键排序、无空白，同样的内容总得到同样的字节。
    """
    content = {"order_id": order_id, "method": method, "result": result}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def submit_payment(
    db: Session,
    order_id: int,
    method: PaymentMethod,
    result: PaymentResult,
    idempotency_key: str,
    now: datetime,
) -> tuple[PaymentOutcome, bool]:
    """提交一次模拟支付，返回结果与是否新写了支付尝试（重放为假）。

    1. 按幂等键查支付尝试：指纹相同返回原结果，不同抛 IdempotencyConflict；都不改订单。
    2. 订单已模拟支付或已取消：抛 OrderNotPayable，不写支付尝试。
    3. 已过支付到期时间：先按超时取消处理，再抛 OrderExpired，不写支付尝试。
    4. 失败：只写支付尝试，订单状态、库存与事件都不变，可再次提交。
    5. 成功：同一事务里用带条件的 UPDATE（状态仍为 awaiting_demo_payment 且未到期）改为
       demo_paid 并写支付时间，写支付尝试与一条操作者为 guest 的事件；库存不变（预留即被
       消耗）。UPDATE 未命中时回滚：同键的并发请求已提交则按 1 处理，否则抛 OrderNotPayable。
    写支付尝试撞上幂等键唯一约束时回滚，按 1 处理。
    """
    fingerprint = payment_fingerprint(order_id, method, result)
    existing = _attempt_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint), False

    order = _load_order(db, order_id)
    if order.status != STATUS_AWAITING_PAYMENT:
        raise OrderNotPayable(order.status)
    if now >= order.payment_expires_at:
        expire_overdue_orders(db, now, order_ids=[order_id])
        status = _load_order(db, order_id).status
        if status == STATUS_CANCELLED:
            raise OrderExpired(status)
        raise OrderNotPayable(status)

    succeeded = result == "success"
    if succeeded:
        _require_transition(STATUS_PAID, ACTOR_GUEST)
        not_due = Order.payment_expires_at > now
        if not _update_awaiting(db, order_id, not_due, status=STATUS_PAID, paid_at=now):
            db.rollback()
            existing = _attempt_by_key(db, idempotency_key)
            if existing is not None:
                return _replay(db, existing, fingerprint), False
            raise OrderNotPayable(_load_order(db, order_id).status)

    attempt = PaymentAttempt(
        order_id=order_id,
        method=_METHODS[method],
        result=_RESULTS[result],
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_at=now,
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        # 同键的并发请求先提交了：回滚本次的改动，按它的结果重放或报冲突。
        db.rollback()
        existing = _attempt_by_key(db, idempotency_key)
        if existing is None:
            raise
        return _replay(db, existing, fingerprint), False

    status = STATUS_AWAITING_PAYMENT
    paid_at = None
    if succeeded:
        db.add(_event(order_id, STATUS_PAID, ACTOR_GUEST, now))
        status = STATUS_PAID
        paid_at = now.replace(tzinfo=UTC)
    db.commit()
    return PaymentOutcome(method=method, result=result, status=status, paid_at=paid_at), True


def cancel_order(db: Session, order_id: int, now: datetime) -> CancelOutcome:
    """下单者在支付页取消待支付订单。

    已过支付到期时间的待支付订单先按超时取消处理。仍待支付时同一事务里用带条件的 UPDATE
    （状态仍为 awaiting_demo_payment 且未到期）改为 demo_cancelled、加回库存、写一条操作者
    为 guest 的事件；未命中（并发的支付、取消或超时取消先提交）时回滚。之后订单已取消
    即返回当前状态（重复取消不再写事件或加回库存）；已模拟支付及之后的状态抛
    OrderNotCancellable。
    """
    order = _load_order(db, order_id)
    if order.status == STATUS_AWAITING_PAYMENT and now >= order.payment_expires_at:
        expire_overdue_orders(db, now, order_ids=[order_id])
        order = _load_order(db, order_id)
    if order.status == STATUS_AWAITING_PAYMENT:
        if _cancel(db, order_id, ACTOR_GUEST, now, overdue=False):
            db.commit()
        else:
            db.rollback()
        order = _load_order(db, order_id)
    if order.status != STATUS_CANCELLED:
        raise OrderNotCancellable(order.status)
    cancelled_by = _cancelled_by(db, [order_id])[order_id]
    return CancelOutcome(status=order.status, cancelled_by=cancelled_by)


def expire_overdue_orders(
    db: Session,
    now: datetime,
    *,
    order_ids: Collection[int] | None = None,
    limit: int | None = None,
) -> int:
    """把超过支付到期时间仍未支付的订单取消并释放库存，返回本次取消的张数。

    选出状态为 awaiting_demo_payment 且支付到期时间不晚于 now 的订单（order_ids 可限定
    范围，limit 可限定单次上限；按到期时间从早到晚），逐张在各自的事务里用带条件的 UPDATE
    （状态仍为 awaiting_demo_payment 且已到期才改）改为 demo_cancelled、按订单行加回当日
    可用库存（规格已删除的行跳过；同一规格先合并件数，按 SKU 规格 ID 从小到大）、写一条
    操作者为 system 的事件，然后提交。UPDATE 未命中（并发的支付、取消或另一次超时取消
    先提交）时回滚并跳过这张，所以同一订单只生效一次，重复运行不重复加回库存。

    可重复运行。按分钟定时运行由之后的定时任务负责；本任务只在查看与支付、取消接口里
    对涉及的订单调用它。
    """
    if order_ids is not None and not order_ids:
        return 0
    stmt = (
        select(Order.id)
        .where(Order.status == STATUS_AWAITING_PAYMENT, Order.payment_expires_at <= now)
        .order_by(Order.payment_expires_at, Order.id)
    )
    if order_ids is not None:
        stmt = stmt.where(Order.id.in_(list(order_ids)))
    if limit is not None:
        stmt = stmt.limit(limit)
    overdue = list(db.scalars(stmt))

    cancelled = 0
    for order_id in overdue:
        if _cancel(db, order_id, ACTOR_SYSTEM, now, overdue=True):
            db.commit()
            cancelled += 1
        else:
            db.rollback()
    return cancelled


def pay_orders(
    db: Session,
    order_ids: list[int],
    lang: Language,
    now: datetime,
) -> list[PayOrder]:
    """按 order_ids 的顺序返回这些订单的支付页视图；只读。订单行按行序。"""
    if not order_ids:
        return []
    order_stmt = select(Order).where(Order.id.in_(order_ids))
    order_stmt = order_stmt.execution_options(populate_existing=True)
    orders = {order.id: order for order in db.scalars(order_stmt)}

    lines: dict[int, list[PayLine]] = {order_id: [] for order_id in order_ids}
    item_stmt = select(OrderItem).where(OrderItem.order_id.in_(order_ids))
    items = db.scalars(item_stmt.order_by(OrderItem.order_id, OrderItem.line_index))
    for item in items:
        line = PayLine(
            name=getattr(item, f"product_name_{lang}"),
            variant_label=getattr(item, f"variant_label_{lang}"),
            quantity=item.quantity,
            unit_price_sen=item.unit_price_sen,
            line_subtotal_sen=item.line_subtotal_sen,
        )
        lines[item.order_id].append(line)

    recipient_stmt = select(OrderRecipient).where(OrderRecipient.order_id.in_(order_ids))
    recipients = {row.order_id: row for row in db.scalars(recipient_stmt)}

    attempt_stmt = select(PaymentAttempt).where(PaymentAttempt.order_id.in_(order_ids))
    attempts = db.scalars(attempt_stmt.order_by(PaymentAttempt.created_at, PaymentAttempt.id))
    # 按时间从早到晚，后面的覆盖前面的，留下的是每张订单最近一次。
    last_payments = {attempt.order_id: attempt for attempt in attempts}

    cancelled_by = _cancelled_by(db, order_ids)
    views = []
    for order_id in order_ids:
        order = orders.get(order_id)
        if order is None:
            continue
        recipient = recipients.get(order_id)
        attempt = last_payments.get(order_id)
        view = PayOrder(
            order_number=order.order_number,
            status=order.status,
            created_at=_utc(order.created_at),
            payment_expires_at=_utc(order.payment_expires_at),
            server_time=_utc(now),
            lines=lines[order_id],
            subtotal_sen=order.subtotal_sen,
            shipping_fee_sen=order.shipping_fee_sen,
            total_sen=order.total_sen,
            recipient=None if recipient is None else _recipient(recipient),
            last_payment=None if attempt is None else _last_payment(attempt),
            cancelled_by=cancelled_by[order_id],
        )
        views.append(view)
    return views


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _load_order(db: Session, order_id: int) -> Order:
    # populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _attempt_by_key(db: Session, idempotency_key: str) -> PaymentAttempt | None:
    stmt = select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
    return db.scalars(stmt).one_or_none()


def _replay(db: Session, attempt: PaymentAttempt, fingerprint: str) -> PaymentOutcome:
    """同键同请求返回原结果与订单当前状态；同键不同请求报冲突。都不改订单。"""
    if not hmac.compare_digest(attempt.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    order = _load_order(db, attempt.order_id)
    return PaymentOutcome(
        method=_METHOD_NAMES[attempt.method],
        result=_RESULT_NAMES[attempt.result],
        status=order.status,
        paid_at=None if order.paid_at is None else _utc(order.paid_at),
    )


def _require_transition(target: str, actor: str) -> None:
    # 经 SHOP-TASK-010 的迁移判定函数检查；这里用到的迁移都在表里，不通过即编程错误。
    if not is_transition_allowed(STATUS_AWAITING_PAYMENT, target, actor):
        raise RuntimeError("order status transition is not allowed")


def _update_awaiting(
    db: Session,
    order_id: int,
    due: ColumnElement[bool],
    **values: object,
) -> bool:
    """带条件的 UPDATE：订单状态仍为 awaiting_demo_payment 且满足 due 才改；是否命中。"""
    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == STATUS_AWAITING_PAYMENT, due)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1


def _cancel(db: Session, order_id: int, actor: str, now: datetime, *, overdue: bool) -> bool:
    """带条件地把待支付订单改为已取消、加回库存、写事件；只 flush、不提交。

    overdue 为真（超时取消）时只改已到期的订单，为假（本人取消）时只改未到期的订单。
    UPDATE 未命中返回假，此时本函数没有写任何东西，由调用方回滚。
    """
    _require_transition(STATUS_CANCELLED, actor)
    if overdue:
        due = Order.payment_expires_at <= now
    else:
        due = Order.payment_expires_at > now
    if not _update_awaiting(db, order_id, due, status=STATUS_CANCELLED):
        return False
    _release_stock(db, order_id)
    db.add(_event(order_id, STATUS_CANCELLED, actor, now))
    db.flush()
    return True


def _release_stock(db: Session, order_id: int) -> None:
    """按订单行把件数加回当日可用库存。

    规格已删除（规格 ID 为空）的行跳过；同一规格的多行先合并件数，再按 SKU 规格 ID
    从小到大逐个更新，与下单扣库存的顺序一致，避免与下单、库存重置互相死锁。
    """
    stmt = (
        select(OrderItem.variant_id, func.sum(OrderItem.quantity))
        .where(OrderItem.order_id == order_id, OrderItem.variant_id.is_not(None))
        .group_by(OrderItem.variant_id)
        .order_by(OrderItem.variant_id)
    )
    quantities = db.execute(stmt).tuples().all()
    for variant_id, quantity in quantities:
        increase = (
            update(ProductVariant)
            .where(ProductVariant.id == variant_id)
            .values(available_stock=ProductVariant.available_stock + quantity)
            .execution_options(synchronize_session=False)
        )
        db.execute(increase)


def _event(order_id: int, to_status: str, actor: str, now: datetime) -> OrderEvent:
    return OrderEvent(
        order_id=order_id,
        from_status=STATUS_AWAITING_PAYMENT,
        to_status=to_status,
        actor_type=actor,
        created_at=now,
    )


def _cancelled_by(db: Session, order_ids: list[int]) -> dict[int, CancelledBy | None]:
    """按取消事件的操作者：system 为超时，其余（guest，以后的 member）为本人。"""
    found: dict[int, CancelledBy | None] = dict.fromkeys(order_ids)
    stmt = (
        select(OrderEvent.order_id, OrderEvent.actor_type)
        .where(OrderEvent.order_id.in_(order_ids))
        .where(OrderEvent.to_status == STATUS_CANCELLED)
    )
    for order_id, actor in db.execute(stmt).tuples():
        found[order_id] = "timeout" if actor == ACTOR_SYSTEM else "self"
    return found


def _recipient(recipient: OrderRecipient) -> PayRecipient:
    return PayRecipient(
        name=recipient.name,
        phone=recipient.phone,
        country_code=recipient.country_code,
        region=recipient.region,
        address=recipient.address,
        postal_code=recipient.postal_code,
    )


def _last_payment(attempt: PaymentAttempt) -> LastPayment:
    return LastPayment(
        method=_METHOD_NAMES[attempt.method],
        result=_RESULT_NAMES[attempt.result],
        created_at=_utc(attempt.created_at),
    )

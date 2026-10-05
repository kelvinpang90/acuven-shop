"""订单查询、查单模式的订单视图与确认收货。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「权限与资料保护」第 2 条：查单先按高熵订单号定位，再与该单 OrderRecipient 的规范化号码比对；
  查询接口严格限流、防批量枚举、不缓存敏感响应。
- 「权限与资料保护」第 6 条：查单通过后仅对该订单在本浏览器保持 30 分钟授权，只能查看该单、
  确认收货和申请退款，不能支付或取消；确认收货等写操作另须 CSRF 令牌。
- 「失败、并发与重试」第 1 条：确认收货使用幂等键和数据库唯一约束，相同键相同请求返回原结果，
  同键不同内容报冲突；第 4 条：查单依赖 Redis 限流，Redis 不可用时拒绝请求。
- 「订单与退款状态」第 2 条：访客在查单页确认收货后进入 demo_completed；所有状态迁移校验当前
  状态与操作者，事务内写订单与事件。

限流阈值按 Kelvin 2026-10-01 的决定（记录见 docs/HANDOFF.md）：同一访客来源 10 分钟内最多查询
30 次（成功失败都计）；同一订单号 1 小时内失败满 10 次后拒绝查询（电话正确也拒绝）。
计数用 SHOP-TASK-026 的 app/services/rate_limit.py；Redis 不可用时它抛 RateLimitUnavailable，
本模块不捕获，由调用的接口拒绝请求。

访问授权、CSRF 与请求格式由调用方（app/api/order_lookup.py）先行校验；确认收货只按订单 ID 操作。
全部退款后冻结确认收货、模拟发货满 7 天自动完成与会员凭会话访问都由之后的任务扩展，
本模块不预留参数。

本模块不写日志；异常消息不含个人资料、订单号、电话、幂等键或令牌。库里的时间一律是不带时区的
UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import string
from datetime import UTC, datetime

import redis
from pydantic import BaseModel
from sqlalchemy import select, update
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
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.rate_limit import current_count, hit

# 按访客来源：10 分钟内最多 30 次，成功失败都计。
SOURCE_BUCKET = "order_lookup_source"
SOURCE_LIMIT = 30
SOURCE_WINDOW_SECONDS = 10 * 60

# 按规范化后的订单号：1 小时内失败满 10 次即拒绝。
FAILURE_BUCKET = "order_lookup_failures"
FAILURE_LIMIT = 10
FAILURE_WINDOW_SECONDS = 60 * 60

# 查单电话的默认地区：页面国家码默认 +60；以加号开头时以输入为准。
LOOKUP_PHONE_REGION = "MY"

# 只转换 ASCII 小写：str.upper 会把少数非 ASCII 字母（如 ı、ſ）变成 ASCII 字母。
_ASCII_UPPER = str.maketrans(string.ascii_lowercase, string.ascii_uppercase)
# 订单号用 Crockford Base32，不含 I、L、O；访客照抄时常把 1、0 看成这几个字母。
_LOOKALIKES = str.maketrans("ILO", "110")


class LookupRateLimited(Exception):
    """来源超过查询上限，或该订单号的失败次数已满。"""


class LookupNotFound(Exception):
    """订单号格式不合法、订单不存在、电话无法规范化或电话不符；四者不区分。"""


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的确认收货请求。"""


class OrderNotConfirmable(Exception):
    """订单不是 demo_shipped，或带条件的 UPDATE 未命中；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class LookupLine(BaseModel):
    """订单行快照：按请求语言取的名称与规格说明（下单时已按回退英文的规则取好）。"""

    name: str
    variant_label: str
    quantity: int
    unit_price_sen: int
    line_subtotal_sen: int
    # 逐件实付快照（不含积分抵扣），按件序。
    unit_cash_paid_sen: list[int]


class LookupRecipient(BaseModel):
    """收货资料原文。地区：马来西亚为州属代码，其他国家为自由文本或空。"""

    name: str
    phone: str
    country_code: str
    region: str | None
    address: str
    postal_code: str


class LookupOrder(BaseModel):
    """订单详情 P09 查单模式所需的一张订单。时间都带 UTC 时区。

    不含优惠券与积分两项：现有订单都是游客订单，P09 不显示这两行；会员任务扩展。
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
    recipient: LookupRecipient | None


class ConfirmOutcome(BaseModel):
    """确认收货的结果：订单当前状态。"""

    status: str


def normalize_order_number(raw: str) -> str:
    """去掉空格与连字符，ASCII 小写转大写，再把 I、L 换成 1、O 换成 0。

    结果不一定合法，须再经 is_valid_order_number 检查。
    """
    text = raw.replace(" ", "").replace("-", "")
    return text.translate(_ASCII_UPPER).translate(_LOOKALIKES)


def lookup_order(
    db: Session,
    client: redis.Redis,
    source: str,
    order_number: str,
    phone: str,
    cookie_value: str | None,
    now: datetime,
) -> IssuedAccess:
    """凭订单号与电话查单；通过时给当前浏览器签发该单 lookup 范围的授权并提交。

    依次执行，每一步不通过即停止：
    1. 按来源计一次（成功失败都计），超过上限抛 LookupRateLimited。
    2. 规范化订单号并检查格式，不合法抛 LookupNotFound（不计失败）。
    3. 读该订单号的失败计数，已满抛 LookupRateLimited。
    4. 按订单号查订单；再按默认地区 MY 规范化电话，以常量时间与该单收货资料的电话比较。
       订单不存在、电话无法规范化或不符时把该订单号的失败计数加一，再抛 LookupNotFound。
    5. 在同一事务里签发授权并提交。查单不改订单状态，任何状态的订单都可查。

    Redis 不可用时 rate_limit 抛 RateLimitUnavailable，本函数不捕获：发生在第 1、3 步时还没有
    查订单；发生在第 4 步给失败计数加一时，不签发授权。
    """
    if not hit(client, SOURCE_BUCKET, source, SOURCE_LIMIT, SOURCE_WINDOW_SECONDS):
        raise LookupRateLimited
    number = normalize_order_number(order_number)
    if not is_valid_order_number(number):
        raise LookupNotFound
    if current_count(client, FAILURE_BUCKET, number) >= FAILURE_LIMIT:
        raise LookupRateLimited

    order_id = db.scalar(select(Order.id).where(Order.order_number == number))
    if order_id is None or not _phone_matches(db, order_id, phone):
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

    cash_paid: dict[int, list[int]] = {item.id: [] for item in items}
    if items:
        unit_stmt = (
            select(OrderItemUnit.order_item_id, OrderItemUnit.cash_paid_sen)
            .where(OrderItemUnit.order_item_id.in_(list(cash_paid)))
            .order_by(OrderItemUnit.order_item_id, OrderItemUnit.unit_index)
        )
        for item_id, cash in db.execute(unit_stmt).tuples():
            cash_paid[item_id].append(cash)

    lines: dict[int, list[LookupLine]] = {order_id: [] for order_id in order_ids}
    for item in items:
        line = LookupLine(
            name=getattr(item, f"product_name_{lang}"),
            variant_label=getattr(item, f"variant_label_{lang}"),
            quantity=item.quantity,
            unit_price_sen=item.unit_price_sen,
            line_subtotal_sen=item.line_subtotal_sen,
            unit_cash_paid_sen=cash_paid[item.id],
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
        )
        views.append(view)
    return views


def confirm_fingerprint(order_id: int) -> str:
    """确认收货请求指纹：{"order_id": <订单 ID>} 的 JSON 的 SHA-256 十六进制。

    JSON 键排序、无空白，同一订单总得到同样的字节。
    """
    content = {"order_id": order_id}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def confirm_receipt(
    db: Session,
    order_id: int,
    idempotency_key: str,
    now: datetime,
) -> ConfirmOutcome:
    """访客在查单页确认收货，返回订单当前（或新的）状态。

    1. 按幂等键查确认收货记录：指纹相同返回订单当前状态，不同抛 IdempotencyConflict；
       都不改订单。
    2. 订单不是 demo_shipped（含已经 demo_completed）：抛 OrderNotConfirmable，不写记录与事件。
    3. 经迁移判定函数检查后，同一事务里用带条件的 UPDATE（状态仍为 demo_shipped 才改）改为
       demo_completed，写确认收货记录与一条操作者为 guest 的事件，提交。
    UPDATE 未命中或写记录撞上唯一约束（同键，或同一订单已有记录）时回滚，再按幂等键查：
    同键按 1 处理，否则抛 OrderNotConfirmable 与当前状态。所以不会出现一单两次完成。
    """
    fingerprint = confirm_fingerprint(order_id)
    existing = _confirmation_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint)

    order = _load_order(db, order_id)
    if order.status != STATUS_SHIPPED:
        raise OrderNotConfirmable(order.status)
    # 经 SHOP-TASK-010 的迁移判定函数检查；这条迁移在表里，不通过即编程错误。
    if not is_transition_allowed(STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_GUEST):
        raise RuntimeError("order status transition is not allowed")

    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == STATUS_SHIPPED)
        .values(status=STATUS_COMPLETED)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        return _after_race(db, order_id, idempotency_key, fingerprint)

    confirmation = ReceiptConfirmation(
        order_id=order_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        actor_type=ACTOR_GUEST,
        created_at=now,
    )
    db.add(confirmation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return _after_race(db, order_id, idempotency_key, fingerprint)

    event = OrderEvent(
        order_id=order_id,
        from_status=STATUS_SHIPPED,
        to_status=STATUS_COMPLETED,
        actor_type=ACTOR_GUEST,
        created_at=now,
    )
    db.add(event)
    db.commit()
    return ConfirmOutcome(status=STATUS_COMPLETED)


def _phone_matches(db: Session, order_id: int, phone: str) -> bool:
    """电话按默认地区 MY 规范化后，以常量时间与该单收货资料的电话比较。"""
    try:
        e164 = normalize_phone(phone, LOOKUP_PHONE_REGION).e164
    except InvalidPhoneNumber:
        return False
    stored = db.scalar(select(OrderRecipient.phone).where(OrderRecipient.order_id == order_id))
    if stored is None:
        return False
    return hmac.compare_digest(e164.encode("utf-8"), stored.encode("utf-8"))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _load_order(db: Session, order_id: int) -> Order:
    # populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _confirmation_by_key(db: Session, idempotency_key: str) -> ReceiptConfirmation | None:
    key_matches = ReceiptConfirmation.idempotency_key == idempotency_key
    return db.scalars(select(ReceiptConfirmation).where(key_matches)).one_or_none()


def _replay(db: Session, confirmation: ReceiptConfirmation, fingerprint: str) -> ConfirmOutcome:
    """同键同请求返回订单当前状态；同键不同请求报冲突。都不改订单。"""
    if not hmac.compare_digest(confirmation.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    return ConfirmOutcome(status=_load_order(db, confirmation.order_id).status)


def _after_race(
    db: Session, order_id: int, idempotency_key: str, fingerprint: str
) -> ConfirmOutcome:
    """回滚之后：同键的并发请求已提交则重放或报冲突，否则按当前状态拒绝。"""
    existing = _confirmation_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint)
    raise OrderNotConfirmable(_load_order(db, order_id).status)


def _recipient(recipient: OrderRecipient) -> LookupRecipient:
    return LookupRecipient(
        name=recipient.name,
        phone=recipient.phone,
        country_code=recipient.country_code,
        region=recipient.region,
        address=recipient.address,
        postal_code=recipient.postal_code,
    )

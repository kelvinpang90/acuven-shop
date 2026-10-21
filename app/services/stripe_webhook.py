"""Stripe webhook 事件的处理：事件去重，Checkout 事件与退款事件的同事务处理。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 7 条：「事件 ID 的去重记录与它引起的订单、积分或退款改动在同一个数据库事务里提交：已提交
  过的事件直接回 200；处理失败整个事务回滚」；业务动作另按对象去重（第 8 条按退款记录）；「只
  处理第 4、5 条的 Checkout 事件与 Stripe 退款状态事件，其他类型回 200 不处理」；「不存、不记录
  事件原文」。
- 第 1 条：「webhook 事件、会话或付款的 `livemode` 为真即拒绝」。
- 第 3 条末句：「万一有本站没记下会话 ID 的会话被付款，其 webhook 按元数据里的创建幂等键与订单
  ID 对上这条记录」。
- 第 4 条：确认的两个来源（webhook 与本站向 Stripe 的查询）走同一个幂等处理，即 SHOP-TASK-113
  的 confirm_checkout。
- 第 5 条：会话 complete 而付款未定时等 async_payment_succeeded 确认，async_payment_failed 后按
  现有规则取消（与定时任务同一规则，经 cancel_after_stripe_end）。
- 第 8 条：「Stripe 退款 ID、调用结果与之后的退款事件（按 Stripe 退款 ID 或元数据里的记录 ID
  关联）更新记录状态」；「退款事件只作触发，收到后先查询再更新，不按事件里的快照改，所以先后
  颠倒送达的事件不会让状态倒退」；「退款接口的响应、webhook 与定时查询共用同一写入规则」，即
  SHOP-TASK-124 的 apply_refund_object。
以及 docs/HANDOFF.md 0.55 的落实写法：处理的事件类型见 app/services/stripe_gateway.py 的
CHECKOUT_EVENT_TYPES 与 REFUND_EVENT_TYPES（退款事件的 data.object 都是退款对象）；
checkout.session.expired 不处理，由定时任务查询；事件只作触发，按向 Stripe 查询到的对象处理；
确认与取消函数以可选回调 record_event 在每次提交前写入事件记录，事件 ID 撞唯一约束时以
EventAlreadyRecorded 返回「已处理过」，不被确认函数的并发重试吞掉；退款事件由本模块在调用
apply_refund_object 之后、提交之前写入事件记录；已找到本站记录而向 Stripe 查询不可用、为正式
环境或被拒时整个事务回滚、不留事件记录，由 Stripe 重发。webhook 接口（签名校验、请求体大小与
livemode 的拒绝）由 SHOP-TASK-129 实现。

调用 Stripe 时不持有任何数据库锁、不留着读事务；写入由被调用的函数按统一顺序加锁。运营告警邮件
尚未接入：Checkout 查询结果为 live 与 rejected 时先记一条 error 级日志，只含类别与本站记录 ID
（沿用 stripe_confirm.py 的类别）；退款查询被拒记 stripe_webhook_refund_rejected，退款写入在
提交之后按 alert_category 的结果记一条（沿用 stripe_refund.py 的类别），都只含尝试 ID；除此之外
本模块不写日志，日志不含事件 ID 与事件、会话或退款原文。库里的时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import StripeCheckout, StripeEvent, StripeRefund
from app.models.order import ACTOR_SYSTEM
from app.models.stripe import CHECKOUT_COMPLETE, EVENT_IGNORED, EVENT_PROCESSED, PAYMENT_PAID
from app.services.payment import StripeEndResult, cancel_after_stripe_end
from app.services.stripe_confirm import (
    LOG_CONFIRM_REJECTED,
    LOG_LIVEMODE,
    ConfirmResult,
    confirm_checkout,
)
from app.services.stripe_gateway import (
    CHECKOUT_EVENT_TYPES,
    REFUND_EVENT_TYPES,
    CheckoutSession,
    Refund,
    StripeGateway,
    StripeOutcome,
    WebhookEvent,
)
from app.services.stripe_overdue import FAILED_INTENT_STATUSES
from app.services.stripe_refund import alert_category, apply_refund_object

logger = logging.getLogger(__name__)

# 告警邮件接入前的 error 级日志类别（只含尝试 ID）：已找到本站尝试而退款查询被拒。
LOG_WEBHOOK_REFUND_REJECTED = "stripe_webhook_refund_rejected"

# 元数据里的订单 ID 与尝试 ID：十进制数字。位数上限只为不让超出整数列的取值进查询，超出按
# 找不到。
_ORDER_ID_PATTERN = re.compile(r"[0-9]{1,18}")
_ATTEMPT_ID_PATTERN = _ORDER_ID_PATTERN


class WebhookResult(StrEnum):
    # 已按第 4、5、8 条处理（含查询到的对象不需改动），事件记录与改动同一事务提交。
    PROCESSED = "processed"
    # 其他类型、checkout.session.expired 或找不到本站记录：只写事件记录。
    IGNORED = "ignored"
    # 同一事件 ID 已有记录：不处理。
    DUPLICATE = "duplicate"
    # Stripe 不可用或无法完成：整个事务回滚、不留事件记录，由 Stripe 重发。
    UNAVAILABLE = "unavailable"


class EventAlreadyRecorded(Exception):
    """写事件记录撞上事件 ID 的唯一约束：并发的同一事件已先提交。不是 SQLAlchemy 异常的子类，
    确认函数不会把它当作 IntegrityError 重试。"""


@dataclass(frozen=True)
class _CheckoutView:
    """普通读取得的本站会话记录。"""

    checkout_id: int
    order_id: int
    status: str
    session_id: str | None
    idempotency_key: str


class _Recorder:
    """传给确认与取消函数的 record_event：在它们提交之前写一条 processed 的事件记录。"""

    def __init__(self, event: WebhookEvent, now: datetime) -> None:
        self._event = event
        self._now = now
        self.called = False

    def __call__(self, db: Session) -> None:
        self.called = True
        db.add(_event_row(self._event, EVENT_PROCESSED, self._now))
        try:
            db.flush()
        except IntegrityError:
            raise EventAlreadyRecorded from None


def handle_webhook_event(
    db: Session, gateway: StripeGateway, event: WebhookEvent, now: datetime
) -> WebhookResult:
    """处理一条签名校验通过、已解析的事件，返回 processed、ignored、duplicate 或 unavailable。

    now 为不带时区的 UTC；带时区或 event.livemode 为真（应由接口先拒绝）时抛 ValueError，先于
    访问数据库。调用方不得持有数据库锁、不得有未提交的改动：本函数会提交或回滚当前事务。
    1. 先结束当前事务，以普通读查到该事件 ID 已有记录：duplicate，不调用 Stripe。
    2. Checkout 事件：见 _handle_checkout。
    3. 退款事件：见 _handle_refund。
    4. 其余类型：ignored。
    不需改动的情形由 _record_only 在只写这一行的事务里写事件记录；撞上事件 ID 的唯一约束时
    回滚并返回 duplicate。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    if event.livemode:
        raise ValueError("livemode events must be rejected before handling")
    db.rollback()
    if _already_recorded(db, event.id):
        db.rollback()
        return WebhookResult.DUPLICATE
    db.rollback()
    if event.type in CHECKOUT_EVENT_TYPES and event.session is not None:
        return _handle_checkout(db, gateway, event, event.session, now)
    if event.type in REFUND_EVENT_TYPES and event.refund is not None:
        return _handle_refund(db, gateway, event, event.refund, now)
    return _record_only(db, event, EVENT_IGNORED, now)


def _handle_checkout(
    db: Session,
    gateway: StripeGateway,
    event: WebhookEvent,
    event_session: CheckoutSession,
    now: datetime,
) -> WebhookResult:
    """Checkout 事件：找回本站记录，以事件会话 ID 向 Stripe 查询一次，按查询到的会话分派。

    找不到记录为 ignored，不调用 Stripe。查询 unavailable 为 unavailable；live 记
    stripe_livemode、rejected 记 stripe_confirm_rejected（只含记录 ID），都为 unavailable。这三种
    都不留事件记录、订单与记录不变。分派见 _dispatch；它没有经 record_event 写事件记录而结果为
    processed 时另由 _record_only 写。捕获 EventAlreadyRecorded 时回滚并返回 duplicate。
    """
    view = _find_checkout(db, event_session)
    db.rollback()
    if view is None:
        return _record_only(db, event, EVENT_IGNORED, now)

    result = gateway.retrieve_checkout_session(event_session.id)
    if result.outcome is StripeOutcome.LIVE:
        logger.error("%s checkout=%d", LOG_LIVEMODE, view.checkout_id)
        return WebhookResult.UNAVAILABLE
    if result.outcome is StripeOutcome.REJECTED:
        logger.error("%s checkout=%d", LOG_CONFIRM_REJECTED, view.checkout_id)
        return WebhookResult.UNAVAILABLE
    if result.outcome is not StripeOutcome.OK or result.value is None:
        return WebhookResult.UNAVAILABLE

    recorder = _Recorder(event, now)
    try:
        outcome = _dispatch(db, gateway, view, result.value, recorder, now)
    except EventAlreadyRecorded:
        db.rollback()
        return WebhookResult.DUPLICATE
    if outcome is WebhookResult.PROCESSED and not recorder.called:
        return _record_only(db, event, EVENT_PROCESSED, now)
    return outcome


def _dispatch(
    db: Session,
    gateway: StripeGateway,
    view: _CheckoutView,
    current: CheckoutSession,
    recorder: _Recorder,
    now: datetime,
) -> WebhookResult:
    """按查询到的会话分派，不看事件类型（事件可能乱序）。

    - 已付款：经 confirm_checkout（传入查询到的会话）确认。
    - 会话 complete、ID 与创建幂等键都等于记录、会话 livemode 为假、PaymentIntent 的 livemode
      不为真且状态为 canceled 或 requires_payment_method（Stripe 给出的付款失败）：以 system
      给记录 ID 调用 cancel_after_stripe_end，seen_status 为查询前读到的状态（与定时任务同一
      取消规则）。
    - 其余 complete（第 5 条付款未定）：经 confirm_checkout 只记下会话，不确认不取消。
    - open、expired 等：不改，processed。
    confirm_checkout 返回 unavailable 为 unavailable，其余为 processed；cancel_after_stripe_end
    返回 cancelled 或 kept 为 processed，changed 为 unavailable。
    """
    if current.payment_status == PAYMENT_PAID:
        return _confirm(db, gateway, view, current, recorder, now)
    if current.status != CHECKOUT_COMPLETE:
        return WebhookResult.PROCESSED
    intent = current.payment_intent
    if (
        current.id == view.session_id
        and current.create_key == view.idempotency_key
        and current.livemode is False
        and intent is not None
        and intent.livemode is not True
        and intent.status in FAILED_INTENT_STATUSES
    ):
        ended = cancel_after_stripe_end(
            db,
            view.order_id,
            ACTOR_SYSTEM,
            now,
            checkout_id=view.checkout_id,
            seen_status=view.status,
            record_event=recorder,
        )
        if ended is StripeEndResult.CHANGED:
            return WebhookResult.UNAVAILABLE
        return WebhookResult.PROCESSED
    return _confirm(db, gateway, view, current, recorder, now)


def _confirm(
    db: Session,
    gateway: StripeGateway,
    view: _CheckoutView,
    current: CheckoutSession,
    recorder: _Recorder,
    now: datetime,
) -> WebhookResult:
    # confirm_checkout 可能按 _needs_query 再查询一次以取实际付款方式类型。
    result = confirm_checkout(db, gateway, view.checkout_id, current, now, record_event=recorder)
    if result is ConfirmResult.UNAVAILABLE:
        return WebhookResult.UNAVAILABLE
    return WebhookResult.PROCESSED


def _handle_refund(
    db: Session,
    gateway: StripeGateway,
    event: WebhookEvent,
    event_refund: Refund,
    now: datetime,
) -> WebhookResult:
    """退款事件：找回本站尝试，以事件退款 ID 向 Stripe 查询一次，按查询到的退款写入。

    找不到尝试（含不是本站发起的退款）为 ignored，不调用 Stripe。查询 unavailable 为
    unavailable；rejected 记 stripe_webhook_refund_rejected（只含尝试 ID），也为 unavailable。
    这两种都不留事件记录、尝试不变。查询 ok 时在同一事务里调用 apply_refund_object（它锁住
    尝试并核对 PaymentIntent 与退款 ID 或元数据），再写 processed 的事件记录并 flush：撞上
    事件 ID 的唯一约束时回滚并返回 duplicate、不记告警；否则提交，提交之后按 alert_category
    记告警（类别与尝试 ID）。不按事件里的退款快照写状态（第 8 条：先查询再更新）；不调用
    refresh_refund_attempt 与 submit_refund_attempt（它们自己提交）。
    """
    attempt_id = _find_attempt(db, event_refund)
    db.rollback()
    if attempt_id is None:
        return _record_only(db, event, EVENT_IGNORED, now)

    result = gateway.retrieve_refund(event_refund.id)
    if result.outcome is StripeOutcome.REJECTED:
        logger.error("%s attempt=%d", LOG_WEBHOOK_REFUND_REJECTED, attempt_id)
        return WebhookResult.UNAVAILABLE
    if result.outcome is not StripeOutcome.OK or result.value is None:
        return WebhookResult.UNAVAILABLE

    applied = apply_refund_object(db, attempt_id, result.value, now)
    db.add(_event_row(event, EVENT_PROCESSED, now))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return WebhookResult.DUPLICATE
    db.commit()
    log = alert_category(applied)
    if log is not None:
        logger.error("%s attempt=%d", log, attempt_id)
    return WebhookResult.PROCESSED


# ---------------------------------------------------------------- 读取与事件记录


def _already_recorded(db: Session, event_id: str) -> bool:
    """普通读：该事件 ID 已有事件记录。不结束读事务。"""
    stmt = select(StripeEvent.id).where(StripeEvent.event_id == event_id)
    return db.scalar(stmt) is not None


def _find_checkout(db: Session, event_session: CheckoutSession) -> _CheckoutView | None:
    """普通读找回本站记录；不结束读事务。

    先按会话 ID 找；找不到且元数据的创建幂等键非空、订单 ID 为十进制数字时，按创建幂等键与
    订单 ID 都相等找（第 3 条末句，含 creating 与 expired 的记录）。
    """
    stmt = select(StripeCheckout).where(StripeCheckout.checkout_session_id == event_session.id)
    record = db.scalars(stmt.execution_options(populate_existing=True)).one_or_none()
    if record is None:
        create_key = event_session.create_key
        order_ref = event_session.order_id
        if not create_key or order_ref is None or not _ORDER_ID_PATTERN.fullmatch(order_ref):
            return None
        stmt = select(StripeCheckout).where(
            StripeCheckout.idempotency_key == create_key,
            StripeCheckout.order_id == int(order_ref),
        )
        record = db.scalars(stmt.execution_options(populate_existing=True)).one_or_none()
        if record is None:
            return None
    return _CheckoutView(
        checkout_id=record.id,
        order_id=record.order_id,
        status=record.status,
        session_id=record.checkout_session_id,
        idempotency_key=record.idempotency_key,
    )


def _find_attempt(db: Session, event_refund: Refund) -> int | None:
    """普通读找回本站尝试 ID；不结束读事务。

    先按 Stripe 退款 ID 等于事件退款 ID 找；找不到且元数据的尝试 ID 为十进制数字时按尝试 ID
    找（第 8 条：「按 Stripe 退款 ID 或元数据里的记录 ID 关联」，含还没记下退款 ID 的尝试）。
    """
    stmt = select(StripeRefund.id).where(StripeRefund.stripe_refund_id == event_refund.id)
    attempt_id = db.scalars(stmt).one_or_none()
    if attempt_id is not None:
        return attempt_id
    attempt_ref = event_refund.attempt_id
    if attempt_ref is None or not _ATTEMPT_ID_PATTERN.fullmatch(attempt_ref):
        return None
    stmt = select(StripeRefund.id).where(StripeRefund.id == int(attempt_ref))
    return db.scalars(stmt).one_or_none()


def _record_only(db: Session, event: WebhookEvent, outcome: str, now: datetime) -> WebhookResult:
    """在只写这一行的事务里插入事件记录并提交；撞上事件 ID 的唯一约束时回滚并返回 duplicate。"""
    db.rollback()
    db.add(_event_row(event, outcome, now))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return WebhookResult.DUPLICATE
    return WebhookResult(outcome)


def _event_row(event: WebhookEvent, outcome: str, now: datetime) -> StripeEvent:
    return StripeEvent(
        event_id=event.id,
        event_type=event.type,
        outcome=outcome,
        processed_at=now,
    )

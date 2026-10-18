"""确认 Stripe 测试付款，与补齐实际付款方式类型。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 4 条：「订单只在服务端确认 Stripe 付款成功后才进入 `demo_paid`，返回地址本身不作
  依据」；「确认时核对会话属于该订单、处于测试环境、币种为 MYR、金额等于订单应付快照；
  全部相符且订单仍为 `awaiting_demo_payment` 时，按模拟支付成功的同一事务进入
  `demo_paid`（消耗库存预留、核销用券记录、按快照发放获得积分），并记下 PaymentIntent
  ID，以及从该 PaymentIntent 实际成功的那笔 Charge 读取的付款方式类型
  （`payment_method_details.type`，如 card、fpx、grabpay）；读不到时留空，不用会话的可用
  方式列表代替，由定时任务之后向 Stripe 重查补齐（只补这一字段，不再执行付款成功的状态
  迁移）」；「确认不看订单的 15 分钟支付到期时间，只看订单是否仍待支付」；「同一会话或
  同一 PaymentIntent 的重复确认（订单已由它付款）直接返回原结果，不退款、不重复发放」。
- 第 3 条：webhook「按元数据里的创建幂等键与订单 ID 对上这条记录」。
- 第 1 条：「webhook 事件、会话或付款的 `livemode` 为真即拒绝」。
- 第 6 条（迟到与不符的付款）：本模块只判定、返回 needs_review，不退款、不改订单与记录，
  由 SHOP-TASK-117 接着处理。
以及 docs/HANDOFF.md 0.53 的核验结论：付款方式类型取 PaymentIntent 的
latest_charge.payment_method_details.type（查询时 expand 为 payment_intent.latest_charge）。

会话对象只来自签名校验通过的 webhook 或本次向 Stripe 的查询，调用方不得传入其他来源的
对象。确认先以普通读取得记录与订单；向 Stripe 查询时不持有任何数据库锁、不留着读事务；
写入时按统一顺序「会员 → 订单 → Stripe 会话记录 → 用券 → 积分 → 退款尝试」加锁并重查，
支付成功的迁移经 app/services/payment.py 的 mark_paid（操作者 system、不带「未到期」
条件、不写支付尝试）。并发的两次确认只有一次迁移：带条件的 UPDATE 未命中时回滚重查，
这时记录已有确认时间（订单由这个会话付款的持久证据），返回 paid。SQLite 不支持行锁，
SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。

调用方不得持有数据库锁、不得有未提交的改动：本函数会提交或回滚当前事务。运营告警邮件
尚未接入：设计写「告警」之处与核对不通过时先记一条 error 级日志，只含类别与本站记录 ID。
不记录会话对象与 Stripe 响应原文。库里的时间一律是不带时区的 UTC。本模块只做业务规则：
webhook、支付页与结果页接口以及定时任务之后调用它。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member, Order, StripeCheckout
from app.models.order import ACTOR_SYSTEM, STATUS_AWAITING_PAYMENT
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    CURRENCY_MYR,
    PAYMENT_METHOD_TYPE_MAX_LENGTH,
    PAYMENT_PAID,
    PAYMENT_STATUSES,
)
from app.services.payment import mark_paid
from app.services.stripe_gateway import CheckoutSession, StripeGateway, StripeOutcome

logger = logging.getLogger(__name__)

# 告警邮件接入前的 error 级日志类别（只含类别与记录 ID）。
LOG_SESSION_MISMATCH = "stripe_session_mismatch"
LOG_LIVEMODE = "stripe_livemode"
LOG_CONFIRM_REJECTED = "stripe_confirm_rejected"
LOG_LATE_PAYMENT = "stripe_late_payment"

# 加锁写入的轮数上限：会员 ID 在普通读与加锁之间被改，或带条件的 UPDATE 未命中时再走一轮。
_MAX_ROUNDS = 3


class ConfirmResult(StrEnum):
    # 订单已由这条会话记录付款（本次迁移，或之前已确认）。
    PAID = "paid"
    # 尚未付款（含会话已 expired 或 open 而未付款）：订单不变。
    PENDING = "pending"
    # 取不到可核对的会话对象，或核对身份、测试环境不通过：不改订单与记录。
    UNAVAILABLE = "unavailable"
    # 第 6 条的迟到或不符的付款：订单与记录都不改，由 SHOP-TASK-117 处理。
    NEEDS_REVIEW = "needs_review"


class FillResult(StrEnum):
    # 已写入付款方式类型。
    FILLED = "filled"
    # Stripe 仍给不出类型（latest_charge 缺失）：留待下次。
    MISSING = "missing"
    # 不需要补：记录未付款、已有类型，或没有会话 ID。
    SKIPPED = "skipped"
    # 查询不成或查询结果核对不通过：留待下次。
    UNAVAILABLE = "unavailable"


class _Step(StrEnum):
    """一次判定的结论；CONFIRM 与 RECORD_PENDING 要加锁写入，其余不写。"""

    MISMATCH = "mismatch"
    LIVE = "live"
    UNVERIFIABLE = "unverifiable"
    PENDING = "pending"
    # 会话已 complete 而未付款：只把记录更新为 complete，订单不变。
    RECORD_PENDING = "record_pending"
    LATE = "late"
    ALREADY_PAID = "already_paid"
    CONFIRM = "confirm"


_RESULTS = {
    _Step.MISMATCH: ConfirmResult.UNAVAILABLE,
    _Step.LIVE: ConfirmResult.UNAVAILABLE,
    _Step.UNVERIFIABLE: ConfirmResult.UNAVAILABLE,
    _Step.PENDING: ConfirmResult.PENDING,
    _Step.RECORD_PENDING: ConfirmResult.PENDING,
    _Step.LATE: ConfirmResult.NEEDS_REVIEW,
    _Step.ALREADY_PAID: ConfirmResult.PAID,
    _Step.CONFIRM: ConfirmResult.PAID,
}

_LOGS = {
    _Step.MISMATCH: LOG_SESSION_MISMATCH,
    _Step.LIVE: LOG_LIVEMODE,
    _Step.LATE: LOG_LATE_PAYMENT,
}


@dataclass(frozen=True)
class _RecordView:
    order_id: int
    idempotency_key: str
    session_id: str | None
    status: str
    confirmed: bool


@dataclass(frozen=True)
class _OrderView:
    status: str
    total_sen: int


# ---------------------------------------------------------------- 确认


def confirm_checkout(
    db: Session,
    gateway: StripeGateway,
    checkout_id: int,
    stripe_session: CheckoutSession | None = None,
    now: datetime | None = None,
) -> ConfirmResult:
    """按一条 Stripe 会话记录确认付款，返回 paid、pending、unavailable 或 needs_review。

    stripe_session 只能来自签名校验通过的 webhook 或本次查询；now 为不带时区的 UTC（带时区
    抛 ValueError，先于访问数据库），省略时取当前时间。
    1. 以普通读取得记录与订单，结束读事务。
    2. 没有给出会话对象，或给出的对象缺 livemode、payment_intent 未展开、缺其 livemode 或
       latest_charge 时，向 Stripe 查询一次（expand 为 payment_intent.latest_charge）：按
       给出对象的会话 ID，没有给出时按记录的会话 ID；两者都没有返回 unavailable、不调用。
       unavailable 返回 unavailable；live 与 rejected 也返回 unavailable，并记一条 error 日志
       （stripe_livemode 或 stripe_confirm_rejected，含记录 ID）。都不改任何东西。
    3. 按 _decide 判定；需要写入时按统一顺序加锁、重查并写入（_write）。
    核对身份不符记 stripe_session_mismatch、会话或 PaymentIntent 的 livemode 为真记
    stripe_livemode、迟到或不符的付款记 stripe_late_payment，各一条 error 日志，只含记录 ID。
    """
    now = _resolve_now(now)
    record, order = _read(db, checkout_id)
    db.rollback()

    if stripe_session is not None and not _needs_query(stripe_session):
        session = stripe_session
    else:
        session_id = stripe_session.id if stripe_session is not None else record.session_id
        if session_id is None:
            return ConfirmResult.UNAVAILABLE
        result = gateway.retrieve_checkout_session(session_id)
        if result.outcome is StripeOutcome.LIVE:
            logger.error("%s checkout=%d", LOG_LIVEMODE, checkout_id)
            return ConfirmResult.UNAVAILABLE
        if result.outcome is StripeOutcome.REJECTED:
            logger.error("%s checkout=%d", LOG_CONFIRM_REJECTED, checkout_id)
            return ConfirmResult.UNAVAILABLE
        if result.outcome is not StripeOutcome.OK or result.value is None:
            return ConfirmResult.UNAVAILABLE
        session = result.value

    step = _decide(record, order, session)
    if step in (_Step.CONFIRM, _Step.RECORD_PENDING):
        step = _write(db, checkout_id, record.order_id, session, now)
    log = _LOGS.get(step)
    if log is not None:
        logger.error("%s checkout=%d", log, checkout_id)
    return _RESULTS[step]


def _needs_query(stripe_session: CheckoutSession) -> bool:
    if stripe_session.livemode is None:
        return True
    intent = stripe_session.payment_intent
    # 未展开的 PaymentIntent 只有 ID；latest_charge 缺失时付款方式类型为空。
    return intent is None or intent.livemode is None or intent.payment_method_type is None


def _decide(record: _RecordView, order: _OrderView, stripe_session: CheckoutSession) -> _Step:
    """按记录、订单与会话对象判定；纯函数，普通读与加锁重查共用。

    依次：
    1. 会话状态不是 complete 而 payment_status 为 paid（Stripe 正常不会这样给出）：视为取不
       到可核对的对象。
    2. 身份：metadata[create_key] 不等于记录的创建幂等键，或记录已有会话 ID 而与会话的 id
       不同：mismatch。
    3. 会话或 PaymentIntent 的 livemode 为真：live。会话缺 livemode，或已付款而缺
       PaymentIntent 或其 livemode：取不到可核对的对象。
    4. 记录已为 expired：会话为 complete 时为迟到的付款，否则（expired 或 open 且未付款）
       pending。
    5. 未付款：会话为 complete、其余相符且记录为 open 或 creating 时只更新记录，否则 pending。
    6. 已付款：记录已有确认时间为已付款；所属订单、币种或金额不符，或订单已不是待支付，为
       迟到或不符的付款；否则确认。
    """
    session = stripe_session
    paid = session.payment_status == PAYMENT_PAID
    if paid and session.status != CHECKOUT_COMPLETE:
        return _Step.UNVERIFIABLE
    if session.create_key != record.idempotency_key:
        return _Step.MISMATCH
    if record.session_id is not None and session.id != record.session_id:
        return _Step.MISMATCH
    intent = session.payment_intent
    if session.livemode is True or (intent is not None and intent.livemode is True):
        return _Step.LIVE
    if session.livemode is None:
        return _Step.UNVERIFIABLE
    if paid and (intent is None or intent.livemode is None):
        return _Step.UNVERIFIABLE

    complete = session.status == CHECKOUT_COMPLETE
    if record.status == CHECKOUT_EXPIRED:
        return _Step.LATE if complete else _Step.PENDING

    order_ref = str(record.order_id)
    matches = (
        session.order_id == order_ref
        and session.client_reference_id == order_ref
        and session.currency == CURRENCY_MYR
        and session.amount_total == order.total_sen
    )
    if not paid:
        if complete and matches and record.status in (CHECKOUT_OPEN, CHECKOUT_CREATING):
            return _Step.RECORD_PENDING
        return _Step.PENDING
    if record.confirmed:
        return _Step.ALREADY_PAID
    if not matches or order.status != STATUS_AWAITING_PAYMENT:
        return _Step.LATE
    return _Step.CONFIRM


def _write(
    db: Session,
    checkout_id: int,
    order_id: int,
    stripe_session: CheckoutSession,
    now: datetime,
) -> _Step:
    """按「会员 → 订单 → Stripe 会话记录」加锁，重查后写入并提交；返回重查的结论。

    确认：按会话更新记录，经 mark_paid（system，不带「未到期」条件）进入 demo_paid，写确认
    时间，同一事务提交。只更新记录：按会话更新记录后提交。重查的结论不再要求写入时回滚、
    原样返回。会员 ID 在普通读与加锁之间被改（认领或注销），或带条件的 UPDATE 未命中时
    回滚再走一轮；轮数用完返回 unverifiable（unavailable），不改任何东西。
    """
    for _ in range(_MAX_ROUNDS):
        db.rollback()
        member_id = db.scalar(select(Order.member_id).where(Order.id == order_id))
        if member_id is not None:
            db.execute(select(Member.id).where(Member.id == member_id).with_for_update())
        order = _lock_order(db, order_id)
        if order.member_id != member_id:
            continue
        record = _lock_checkout(db, checkout_id)
        step = _decide(_record_view(record), _order_view(order), stripe_session)
        if step not in (_Step.CONFIRM, _Step.RECORD_PENDING):
            db.rollback()
            return step
        _apply_session(record, stripe_session, now)
        if step is _Step.CONFIRM:
            if not mark_paid(db, order_id, ACTOR_SYSTEM, now, unexpired_only=False):
                continue
            record.confirmed_at = now
        db.commit()
        return step
    db.rollback()
    return _Step.UNVERIFIABLE


def _apply_session(record: StripeCheckout, stripe_session: CheckoutSession, now: datetime) -> None:
    """按 complete 的会话更新已锁住的记录；只改属性、不提交。

    只把 open 或 creating 改为 complete 并写完成时间（本站首次得知的当前时间）；会话 ID 与
    付款页地址记录尚无时写入；付款状态按会话（不认识时留空）；PaymentIntent ID 与
    latest_charge.payment_method_details.type 有则写，类型读不到时留空。
    """
    if record.status in (CHECKOUT_OPEN, CHECKOUT_CREATING):
        record.status = CHECKOUT_COMPLETE
        record.completed_at = now
    if record.checkout_session_id is None:
        record.checkout_session_id = stripe_session.id
    if record.checkout_url is None and stripe_session.url:
        record.checkout_url = stripe_session.url
    payment_status = stripe_session.payment_status
    record.payment_status = payment_status if payment_status in PAYMENT_STATUSES else None
    intent = stripe_session.payment_intent
    if intent is not None:
        record.payment_intent_id = intent.id
        if _storable_type(intent.payment_method_type):
            record.payment_method_type = intent.payment_method_type


# ---------------------------------------------------------------- 补齐付款方式类型


def fill_payment_method_type(db: Session, gateway: StripeGateway, checkout_id: int) -> FillResult:
    """对已付款而付款方式类型为空的记录向 Stripe 查询一次，补上类型；供定时任务调用。

    调用方不得持有数据库锁、不得有未提交的改动。记录的付款状态不是 paid、已有类型或没有
    会话 ID 时返回 skipped、不调用 Stripe。查询不是 ok，或查询到的会话 ID、创建幂等键、
    PaymentIntent ID（记录已有时）与记录不符、会话或 PaymentIntent 的 livemode 不为假时返回
    unavailable；latest_charge 仍缺失时返回 missing。都留待下次、不改记录。否则锁住记录重查，
    只写付款方式类型这一列并提交，不执行状态迁移、不改订单。本函数不写日志。
    """
    record = _load_checkout(db, checkout_id)
    session_id = record.checkout_session_id
    if (
        record.payment_status != PAYMENT_PAID
        or record.payment_method_type is not None
        or session_id is None
    ):
        db.rollback()
        return FillResult.SKIPPED
    create_key = record.idempotency_key
    intent_id = record.payment_intent_id
    db.rollback()

    result = gateway.retrieve_checkout_session(session_id)
    if result.outcome is not StripeOutcome.OK or result.value is None:
        return FillResult.UNAVAILABLE
    session = result.value
    intent = session.payment_intent
    if (
        session.id != session_id
        or session.create_key != create_key
        or session.livemode is not False
        or intent is None
        or intent.livemode is not False
        or (intent_id is not None and intent.id != intent_id)
    ):
        return FillResult.UNAVAILABLE
    method_type = intent.payment_method_type
    if not _storable_type(method_type):
        return FillResult.MISSING

    record = _lock_checkout(db, checkout_id)
    if record.payment_status != PAYMENT_PAID or record.payment_method_type is not None:
        db.rollback()
        return FillResult.SKIPPED
    record.payment_method_type = method_type
    db.commit()
    return FillResult.FILLED


# ---------------------------------------------------------------- 读取与校验


def _storable_type(value: str | None) -> bool:
    # 超出列宽的取值不写（按读不到处理），不让写入因列宽失败。
    return value is not None and 0 < len(value) <= PAYMENT_METHOD_TYPE_MAX_LENGTH


def _resolve_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    return now


def _read(db: Session, checkout_id: int) -> tuple[_RecordView, _OrderView]:
    """普通读取得记录与订单（不加锁）。"""
    record = _load_checkout(db, checkout_id)
    stmt = (
        select(Order).where(Order.id == record.order_id).execution_options(populate_existing=True)
    )
    order = db.scalars(stmt).one()
    return _record_view(record), _order_view(order)


def _record_view(record: StripeCheckout) -> _RecordView:
    return _RecordView(
        order_id=record.order_id,
        idempotency_key=record.idempotency_key,
        session_id=record.checkout_session_id,
        status=record.status,
        confirmed=record.confirmed_at is not None,
    )


def _order_view(order: Order) -> _OrderView:
    return _OrderView(status=order.status, total_sen=order.total_sen)


def _load_checkout(db: Session, checkout_id: int) -> StripeCheckout:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _lock_order(db: Session, order_id: int) -> Order:
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _lock_checkout(db: Session, checkout_id: int) -> StripeCheckout:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()

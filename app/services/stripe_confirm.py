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
- 第 3 条末句：「万一有本站没记下会话 ID 的会话被付款，其 webhook 按元数据里的创建幂等键与
  订单 ID 对上这条记录，照第 4、6 条处理」：已为 expired 的记录（含对账记为 expired、没有
  会话 ID 的）收到相符的已付款会话，订单仍待支付且没有别的活动记录时改回 complete 并确认。
- 第 6 条：「确认时订单已不是待支付且不是由这个会话付款（已取消，或已由另一途径付款），或
  金额、币种不符，订单状态不变，服务端对这笔付款在 Stripe 测试环境全额退款，记为来源为该
  会话的 `StripeRefund` 尝试，并向运营告警邮件报告」（SHOP-TASK-117）。本模块只写 pending
  的尝试；向 Stripe 发起退款由之后的退款任务的定时重试完成。
- 第 8 条：「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额（failed
  不计），新建前在锁住该付款的事务里核对，超出即不建并告警；这同时约束第 6 条的全额退款」；
  明确拒绝的尝试不自动重试：这个会话已有任何尝试（含 failed）就不再另建。
以及 docs/HANDOFF.md 0.53 的核验结论：付款方式类型取 PaymentIntent 的
latest_charge.payment_method_details.type（查询时 expand 为 payment_intent.latest_charge）；
与同节记录的 Kelvin 2026-10-15 落实决定（不改设计文本）：核对不符的已付款会话写完退款尝试
后，把这条记录标为 expired、释放订单的活动会话位，订单随后可再发起支付或按现有规则取消。

会话对象只来自签名校验通过的 webhook 或本次向 Stripe 的查询，调用方不得传入其他来源的
对象。确认先以普通读取得记录与订单；向 Stripe 查询时不持有任何数据库锁、不留着读事务；
写入时按统一顺序「会员 → 订单 → Stripe 会话记录 → 用券 → 积分 → 退款尝试」加锁并重查
（依次锁住订单、这条会话记录、同一订单的其他会话记录，最后才以加锁读查看这个会话的退款
尝试；同一订单的确认都先在订单行排队），
支付成功的迁移经 app/services/payment.py 的 mark_paid（操作者 system、不带「未到期」
条件、不写支付尝试）。并发的两次确认只有一次迁移：带条件的 UPDATE 未命中时回滚重查，
这时记录已有确认时间（订单由这个会话付款的持久证据），返回 paid。SQLite 不支持行锁，
SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。

调用方不得持有数据库锁、不得有未提交的改动：本函数会提交或回滚当前事务。运营告警邮件
尚未接入：设计写「告警」之处与核对不通过时先记一条 error 级日志，只含类别与本站记录 ID。
不记录会话对象与 Stripe 响应原文。库里的时间一律是不带时区的 UTC。本模块只做业务规则：
webhook、支付页与结果页接口以及定时任务之后调用它。

按订单确认（confirm_order_payment）供之后的支付页发起与结果页确认接口调用，依据第 4 条
（「同一会话或同一 PaymentIntent 的重复确认（订单已由它付款）直接返回原结果」）、第 6 条、
docs/UX.md 0.11 P07（成功、确认中、尚未付款、已取消且已发起退款）与 docs/HANDOFF.md 0.54
的落实写法（结果页的确认结果为 paid、confirming、not_paid、refunded 四种，Stripe 不可用与
确认返回 needs_review 都按「确认中」显示）。它自己找记录时只读、结束读事务后才调用
confirm_checkout，确认的事务与锁沿用 confirm_checkout。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Member, Order, StripeCheckout, StripeRefund
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
    SOURCE_CHECKOUT,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
)
from app.services.payment import mark_paid
from app.services.stripe_gateway import CheckoutSession, StripeGateway, StripeOutcome

logger = logging.getLogger(__name__)

# 告警邮件接入前的 error 级日志类别（只含类别与记录 ID）。
LOG_SESSION_MISMATCH = "stripe_session_mismatch"
LOG_LIVEMODE = "stripe_livemode"
LOG_CONFIRM_REJECTED = "stripe_confirm_rejected"
# 第 6 条：本次为迟到或不符的付款写了退款尝试。
LOG_LATE_PAYMENT = "stripe_late_payment"
# 第 8 条：合计会超过付款金额，没写退款尝试。
LOG_REFUND_CAP = "stripe_refund_cap"
# 会话缺 amount_total 或 PaymentIntent ID，无法写退款尝试。
LOG_LATE_UNREFUNDABLE = "stripe_late_payment_unrefundable"

# 加锁写入的轮数上限：会员 ID 在普通读与加锁之间被改，带条件的 UPDATE 未命中，或写入撞上
# 唯一约束（并发的确认先写了同一来源的退款尝试或同一订单的活动记录）时再走一轮。
_MAX_ROUNDS = 3


class ConfirmResult(StrEnum):
    # 订单已由这条会话记录付款（本次迁移，或之前已确认）。
    PAID = "paid"
    # 尚未付款（含会话已 expired 或 open 而未付款）：订单不变。
    PENDING = "pending"
    # 取不到可核对的会话对象，或核对身份、测试环境不通过：不改订单与记录。
    UNAVAILABLE = "unavailable"
    # 第 6 条的迟到或不符的付款，这个会话已有或本次写成退款尝试：订单不变；记录不占活动位。
    # 不说明退款进行到哪一步。
    REFUNDED = "refunded"
    # 第 6 条的迟到或不符的付款，因合计上限或缺字段没写成退款尝试：订单不变、记录保留原来
    # 的活动位，交人工核对。
    NEEDS_REVIEW = "needs_review"


class OrderPaymentResult(StrEnum):
    """confirm_order_payment 的结果：结果页 P07 的四种状态。"""

    # 订单已付款（本次确认，或之前已付款）。
    PAID = "paid"
    # 付款结果还不能确定：会话已完成而付款未定、Stripe 不可用、核对取不到对象或交人工核对。
    CONFIRMING = "confirming"
    # 尚未付款：订单仍待支付（会话未完成、创建结果未定、不符的付款已退款），或已取消而没有
    # 迟到付款的退款尝试。
    NOT_PAID = "not_paid"
    # 订单已不是待支付，本单有来源为会话的退款尝试（迟到的付款，第 6 条）。
    REFUNDED = "refunded"


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
    """一次判定的结论；_WRITES 里的要加锁写入，其余不写。"""

    MISMATCH = "mismatch"
    LIVE = "live"
    UNVERIFIABLE = "unverifiable"
    PENDING = "pending"
    # 会话已 complete 而未付款：只把记录更新为 complete（expired 的记录改回 complete），
    # 订单不变。
    RECORD_PENDING = "record_pending"
    # 记录已为 expired、会话 complete 而未付款，不能改回 complete：只补付款状态。
    PAYMENT_STATUS = "payment_status"
    # 迟到或不符的付款：加锁后按 _settle_late 写退款尝试，结论为 REFUNDED 或 HELD。
    LATE = "late"
    # 这个会话已有或本次写成退款尝试。
    REFUNDED = "refunded"
    # 没写成退款尝试，交人工核对。
    HELD = "held"
    ALREADY_PAID = "already_paid"
    CONFIRM = "confirm"


_WRITES = (_Step.CONFIRM, _Step.RECORD_PENDING, _Step.PAYMENT_STATUS, _Step.LATE)

_RESULTS = {
    _Step.MISMATCH: ConfirmResult.UNAVAILABLE,
    _Step.LIVE: ConfirmResult.UNAVAILABLE,
    _Step.UNVERIFIABLE: ConfirmResult.UNAVAILABLE,
    _Step.PENDING: ConfirmResult.PENDING,
    _Step.RECORD_PENDING: ConfirmResult.PENDING,
    _Step.PAYMENT_STATUS: ConfirmResult.PENDING,
    _Step.REFUNDED: ConfirmResult.REFUNDED,
    _Step.HELD: ConfirmResult.NEEDS_REVIEW,
    _Step.ALREADY_PAID: ConfirmResult.PAID,
    _Step.CONFIRM: ConfirmResult.PAID,
}

_LOGS = {
    _Step.MISMATCH: LOG_SESSION_MISMATCH,
    _Step.LIVE: LOG_LIVEMODE,
}


@dataclass(frozen=True)
class _RecordView:
    order_id: int
    idempotency_key: str
    session_id: str | None
    status: str
    confirmed: bool
    # 这个会话已有任何退款尝试（不论状态）。
    refunded: bool


@dataclass(frozen=True)
class _OrderView:
    status: str
    total_sen: int
    # 订单另有不是 expired 的会话记录。
    other_active: bool


# ---------------------------------------------------------------- 确认


def confirm_checkout(
    db: Session,
    gateway: StripeGateway,
    checkout_id: int,
    stripe_session: CheckoutSession | None = None,
    now: datetime | None = None,
) -> ConfirmResult:
    """按一条 Stripe 会话记录确认付款，返回 paid、pending、unavailable、refunded 或
    needs_review。

    stripe_session 只能来自签名校验通过的 webhook 或本次查询；now 为不带时区的 UTC（带时区
    抛 ValueError，先于访问数据库），省略时取当前时间。
    1. 以普通读取得记录与订单，结束读事务。
    2. 没有给出会话对象，或给出的对象缺 livemode、payment_intent 未展开、缺其 livemode 或
       latest_charge 时，向 Stripe 查询一次（expand 为 payment_intent.latest_charge）：按
       给出对象的会话 ID，没有给出时按记录的会话 ID；两者都没有返回 unavailable、不调用。
       unavailable 返回 unavailable；live 与 rejected 也返回 unavailable，并记一条 error 日志
       （stripe_livemode 或 stripe_confirm_rejected，含记录 ID）。都不改任何东西。
    3. 按 _decide 判定；需要写入时按统一顺序加锁、重查并写入（_write，迟到与不符的付款见
       _settle_late）。
    核对身份不符记 stripe_session_mismatch、会话或 PaymentIntent 的 livemode 为真记
    stripe_livemode；迟到或不符的付款本次写了退款尝试记 stripe_late_payment，因合计上限或
    缺字段没写成时按 _settle_late 记 stripe_refund_cap 或 stripe_late_payment_unrefundable。
    各一条 error 日志，只含记录 ID，在提交之后记。
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
    log = _LOGS.get(step)
    if step in _WRITES:
        step, log = _write(db, checkout_id, record.order_id, session, now)
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
    4. 记录已为 expired（它不占活动位）：会话不是 complete（expired 或 open 且未付款）时
       pending、不改；已付款而这个会话已有退款尝试时为已退款（第 6、8 条：曾按迟到付款处理，
       不再确认订单）。其余按「可改回」——所属订单、币种与金额都相符，订单仍待支付，且订单
       没有其他不是 expired 的记录——分：未付款时可改回则只更新记录（改回 complete），否则只
       补付款状态；已付款时可改回则确认（第 3 条末句），否则为迟到的付款。
    5. 未付款：会话为 complete、其余相符且记录为 open 或 creating 时只更新记录，否则 pending。
    6. 已付款：记录已有确认时间为已付款；这个会话已有退款尝试、所属订单、币种或金额不符，或
       订单已不是待支付，为迟到或不符的付款；否则确认。
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
    order_ref = str(record.order_id)
    matches = (
        session.order_id == order_ref
        and session.client_reference_id == order_ref
        and session.currency == CURRENCY_MYR
        and session.amount_total == order.total_sen
    )
    awaiting = order.status == STATUS_AWAITING_PAYMENT

    if record.status == CHECKOUT_EXPIRED:
        if not complete:
            return _Step.PENDING
        if paid and record.refunded:
            return _Step.REFUNDED
        revivable = matches and awaiting and not order.other_active
        if not paid:
            return _Step.RECORD_PENDING if revivable else _Step.PAYMENT_STATUS
        return _Step.CONFIRM if revivable else _Step.LATE

    if not paid:
        if complete and matches and record.status in (CHECKOUT_OPEN, CHECKOUT_CREATING):
            return _Step.RECORD_PENDING
        return _Step.PENDING
    if record.confirmed:
        return _Step.ALREADY_PAID
    if record.refunded or not matches or not awaiting:
        return _Step.LATE
    return _Step.CONFIRM


def _write(
    db: Session,
    checkout_id: int,
    order_id: int,
    stripe_session: CheckoutSession,
    now: datetime,
) -> tuple[_Step, str | None]:
    """按「会员 → 订单 → Stripe 会话记录 → 退款尝试」加锁，重查后写入并提交；返回重查的结论与要记的
    日志类别（提交之后由调用方记）。

    重查时先以加锁读查看同一订单的其他会话记录，再查看这个会话的退款尝试（会话记录先于退款
    尝试）。确认：按会话更新记录
    （expired 的记录改回 complete、重新占用活动位），经 mark_paid（system，不带「未到期」
    条件）进入 demo_paid，写确认时间，同一事务提交。只更新记录：按会话更新记录后提交。只补
    付款状态：记录保持 expired。迟到或不符的付款：按 _settle_late。重查的结论不再要求写入时
    回滚、原样返回。会员 ID 在普通读与加锁之间被改（认领或注销）、带条件的 UPDATE 未命中，或
    写入撞上唯一约束（并发的确认先写了这个会话的退款尝试，或订单另有了活动记录）时回滚再走
    一轮；轮数用完返回 unverifiable（unavailable），不改任何东西。
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
        # 先锁同一订单的其他会话记录，再锁这个会话的退款尝试，守住统一锁顺序。
        order_view = _order_view(db, order, checkout_id, lock=True)
        view = _record_view(db, record, lock=True)
        step = _decide(view, order_view, stripe_session)
        if step not in _WRITES:
            db.rollback()
            return step, _LOGS.get(step)
        log = None
        try:
            if step is _Step.LATE:
                step, log = _settle_late(db, record, view.refunded, stripe_session, now)
            elif step is _Step.PAYMENT_STATUS:
                record.payment_status = _payment_status(stripe_session)
            else:
                _apply_session(record, stripe_session, now)
                if step is _Step.CONFIRM:
                    if not mark_paid(db, order_id, ACTOR_SYSTEM, now, unexpired_only=False):
                        continue
                    record.confirmed_at = now
            db.commit()
        except IntegrityError:
            continue
        return step, log
    db.rollback()
    return _Step.UNVERIFIABLE, None


def _apply_session(record: StripeCheckout, stripe_session: CheckoutSession, now: datetime) -> None:
    """按 complete 的会话更新已锁住的记录；只改属性、不提交。

    把 open、creating 或可改回的 expired 记录改为 complete，占用订单的活动位（expired 的记录
    由此重新占用），完成时间为空时写本站首次得知的当前时间；付款页地址记录尚无时写入；其余
    按 _fill_session。
    """
    if record.status != CHECKOUT_COMPLETE:
        record.status = CHECKOUT_COMPLETE
        record.active_order_id = record.order_id
        if record.completed_at is None:
            record.completed_at = now
    if record.checkout_url is None and stripe_session.url:
        record.checkout_url = stripe_session.url
    _fill_session(record, stripe_session)


def _fill_session(record: StripeCheckout, stripe_session: CheckoutSession) -> None:
    """会话 ID 记录尚无时写入；付款状态按会话（不认识时留空）；PaymentIntent ID 与
    latest_charge.payment_method_details.type 有则写，类型读不到时留空。不改状态与活动位。
    """
    if record.checkout_session_id is None:
        record.checkout_session_id = stripe_session.id
    record.payment_status = _payment_status(stripe_session)
    intent = stripe_session.payment_intent
    if intent is not None:
        record.payment_intent_id = intent.id
        if _storable_type(intent.payment_method_type):
            record.payment_method_type = intent.payment_method_type


def _payment_status(stripe_session: CheckoutSession) -> str | None:
    payment_status = stripe_session.payment_status
    return payment_status if payment_status in PAYMENT_STATUSES else None


# ---------------------------------------------------------------- 按订单确认


def confirm_order_payment(
    db: Session, gateway: StripeGateway, order_id: int, now: datetime
) -> OrderPaymentResult:
    """按订单确认 Stripe 付款，返回 paid、confirming、not_paid 或 refunded。

    now 为不带时区的 UTC（带时区抛 ValueError，先于访问数据库）。调用方不得持有数据库锁、不得
    有未提交的改动。先以普通读取得订单与它不是 expired 的记录，结束读事务：
    1. 订单的支付时间非空：paid，不调用 Stripe（第 4 条：重复确认直接返回原结果）。
    2. 没有这样的记录：订单已不是 awaiting_demo_payment 且本单有来源为 checkout 的退款尝试时
       refunded，否则 not_paid（不符的付款退款后订单仍待支付、可再发起支付，HANDOFF 0.53）。
    3. creating：not_paid，不调用 Stripe（创建结果由发起与定时任务对账）。
    4. open 或 complete：按 confirm_checkout 确认（向 Stripe 查询一次）。paid 照传；unavailable
       与 needs_review 为 confirming；refunded 与 pending 时重读该记录与订单：记录为 complete
       （会话已完成而付款未定）为 confirming，订单已不是 awaiting_demo_payment 且本单有来源为
       checkout 的退款尝试为 refunded，其余为 not_paid。
    """
    now = _resolve_now(now)
    order = _load_order(db, order_id)
    if order.paid_at is not None:
        db.rollback()
        return OrderPaymentResult.PAID
    record = _load_active_checkout(db, order_id)
    if record is None:
        result = _settled(db, order_id, None)
        db.rollback()
        return result
    checkout_id = record.id
    status = record.status
    db.rollback()
    if status == CHECKOUT_CREATING:
        return OrderPaymentResult.NOT_PAID

    confirmed = confirm_checkout(db, gateway, checkout_id, None, now)
    if confirmed is ConfirmResult.PAID:
        return OrderPaymentResult.PAID
    if confirmed in (ConfirmResult.UNAVAILABLE, ConfirmResult.NEEDS_REVIEW):
        return OrderPaymentResult.CONFIRMING
    result = _settled(db, order_id, checkout_id)
    db.rollback()
    return result


def _settled(db: Session, order_id: int, checkout_id: int | None) -> OrderPaymentResult:
    """普通读重读订单（与给出的记录），按 confirm_order_payment 第 2、4 条归为 confirming、
    refunded 或 not_paid；不结束读事务。"""
    order = _load_order(db, order_id)
    if checkout_id is not None and _load_checkout(db, checkout_id).status == CHECKOUT_COMPLETE:
        return OrderPaymentResult.CONFIRMING
    if order.status != STATUS_AWAITING_PAYMENT and _has_checkout_refund(db, order_id):
        return OrderPaymentResult.REFUNDED
    return OrderPaymentResult.NOT_PAID


def _has_checkout_refund(db: Session, order_id: int) -> bool:
    """本单任一会话记录有来源为 checkout 的退款尝试（不论尝试状态）。"""
    stmt = (
        select(StripeRefund.id)
        .join(StripeCheckout, StripeRefund.checkout_id == StripeCheckout.id)
        .where(StripeCheckout.order_id == order_id, StripeRefund.source_type == SOURCE_CHECKOUT)
        .limit(1)
    )
    return db.scalar(stmt) is not None


# ---------------------------------------------------------------- 迟到与不符的付款


def _settle_late(
    db: Session,
    record: StripeCheckout,
    refunded: bool,
    stripe_session: CheckoutSession,
    now: datetime,
) -> tuple[_Step, str | None]:
    """第 6 条：在锁住订单与该会话记录的事务里处理迟到或不符的付款；只改属性、flush，不提交。

    订单状态一律不变。先按会话补齐记录：已为 expired 的记录保持 expired 与空的活动列，只补
    会话 ID、PaymentIntent ID、付款状态与付款方式类型（不碰活动列的唯一约束）；仍占活动位的
    记录按 _apply_session 改为 complete（与 Stripe 的会话状态一致）。之后：
    - 这个会话已有任何退款尝试（不论状态；failed 也不另建，第 8 条：明确拒绝的不自动重试）：
      不写、不核对上限、不记日志，返回 refunded。
    - 会话缺 amount_total（或不为正）或 PaymentIntent ID：不写，记
      stripe_late_payment_unrefundable，返回 held。
    - 锁住同一 PaymentIntent 的退款尝试，不是 failed 的金额合计加上 amount_total 超过付款金额
      （amount_total）：不写，记 stripe_refund_cap，返回 held。
    - 否则写一条来源为该会话、金额为 amount_total、状态 pending 的退款尝试，记
      stripe_late_payment，返回 refunded。并发的另一次确认先写了同一来源的尝试时撞上活动来源
      的唯一约束，由 _write 回滚重查。
    已有或本次写成尝试时，记录仍占着活动列就改为 expired、活动列置空（HANDOFF 0.53 的落实
    决定），订单随后可再发起支付或取消；held 时保留活动位，写告警时间（已非空则不改）。
    held 的两类日志只在本次之前记录的付款状态不是 paid（首次看到这笔付款）或告警时间为空时
    记，重复确认与定时任务每轮的重试只告警一次。
    """
    first_seen = record.payment_status != PAYMENT_PAID or record.alerted_at is None
    if record.status == CHECKOUT_EXPIRED:
        _fill_session(record, stripe_session)
    else:
        _apply_session(record, stripe_session, now)

    if refunded:
        _release(record)
        return _Step.REFUNDED, None
    intent = stripe_session.payment_intent
    amount = stripe_session.amount_total
    if intent is None or not intent.id or amount is None or amount <= 0:
        return _hold(record, now, LOG_LATE_UNREFUNDABLE if first_seen else None)
    if _refunded_total(db, intent.id) + amount > amount:
        return _hold(record, now, LOG_REFUND_CAP if first_seen else None)
    db.add(
        StripeRefund(
            payment_intent_id=intent.id,
            source_type=SOURCE_CHECKOUT,
            checkout_id=record.id,
            amount_sen=amount,
            status=STRIPE_REFUND_PENDING,
            call_count=0,
            created_at=now,
            active_checkout_id=record.id,
        )
    )
    db.flush()
    _release(record)
    return _Step.REFUNDED, LOG_LATE_PAYMENT


def _release(record: StripeCheckout) -> None:
    # HANDOFF 0.53：写完（或已有）退款尝试后释放订单的活动会话位。
    if record.status != CHECKOUT_EXPIRED:
        record.status = CHECKOUT_EXPIRED
        record.active_order_id = None


def _hold(record: StripeCheckout, now: datetime, log: str | None) -> tuple[_Step, str | None]:
    if record.alerted_at is None:
        record.alerted_at = now
    return _Step.HELD, log


def _refunded_total(db: Session, payment_intent_id: str) -> int:
    """锁住同一 PaymentIntent 的退款尝试，返回不是 failed 的金额合计（第 8 条）。"""
    stmt = (
        select(StripeRefund.amount_sen)
        .where(
            StripeRefund.payment_intent_id == payment_intent_id,
            StripeRefund.status != STRIPE_REFUND_FAILED,
        )
        .with_for_update()
    )
    return sum(db.scalars(stmt))


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
    order_view = _order_view(db, order, checkout_id, lock=False)
    return _record_view(db, record, lock=False), order_view


def _record_view(db: Session, record: StripeCheckout, *, lock: bool) -> _RecordView:
    """lock 为真时以加锁读查看这个会话的退款尝试（锁顺序的最后一项「退款尝试」）。"""
    stmt = select(StripeRefund.id).where(StripeRefund.checkout_id == record.id)
    if lock:
        stmt = stmt.with_for_update()
    return _RecordView(
        order_id=record.order_id,
        idempotency_key=record.idempotency_key,
        session_id=record.checkout_session_id,
        status=record.status,
        confirmed=record.confirmed_at is not None,
        refunded=bool(db.scalars(stmt).all()),
    )


def _order_view(db: Session, order: Order, checkout_id: int, *, lock: bool) -> _OrderView:
    """lock 为真时以加锁读查看同一订单的其他活动记录：已锁住订单行，同一订单的确认都先在
    订单行排队；并发新建的记录由活动列的唯一约束兜底。须在 _record_view 锁退款尝试之前调用
    （锁顺序「Stripe 会话记录 → 退款尝试」）。"""
    stmt = select(StripeCheckout.id).where(
        StripeCheckout.active_order_id == order.id, StripeCheckout.id != checkout_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return _OrderView(
        status=order.status,
        total_sen=order.total_sen,
        other_active=bool(db.scalars(stmt).all()),
    )


def _load_checkout(db: Session, checkout_id: int) -> StripeCheckout:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _load_order(db: Session, order_id: int) -> Order:
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _load_active_checkout(db: Session, order_id: int) -> StripeCheckout | None:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.active_order_id == order_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one_or_none()


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

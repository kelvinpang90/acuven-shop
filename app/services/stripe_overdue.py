"""有 Stripe 记录的订单的定时处理：按会话的实际状态取消或确认，
与补齐付款方式类型。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 3 条：「调用结果未知（超时、断线）时，记录保持 `creating`，
  之后的发起或定时任务以同一幂等键与同一快照重试」；「快照里的
  Stripe 到期时间过后，以同一幂等键再请求一次来对账」；「24 小时内
  仍无法确定就不再自动处理、告警由人工核对，订单在此期间不取消、
  库存继续预留」。
- 第 4 条：付款方式类型「读不到时留空……由定时任务之后向 Stripe
  重查补齐（只补这一字段，不再执行付款成功的状态迁移）」。
- 第 5 条：「有 Stripe 记录的订单，取消一律先处理会话，所有取消入口
  （……定时任务）用同一规则」；「会话为 creating 时先按第 3 条取得
  会话或对账，只有对账确认没有建成会话（记为 expired）才取消」；
  「会话 complete 且已付款，按第 4 条确认、不取消；complete 而付款
  未定……订单继续待支付、库存继续预留」；「定时任务也定期查询该会话
  的付款状态，以 Stripe 给出的成功或失败为准，不因时间取消。会话
  完成 24 小时后仍无结果就告警」；「Stripe 不可用时……定时任务跳过
  该单留到下一轮；订单保持待支付、库存继续预留，恢复后按会话的实际
  状态处理，不以本地时钟过了会话到期时间代替 Stripe 的状态确认。
  24 小时仍无法查询就告警由人工核对，订单不自动取消」。

每轮先做会话处理、再做付款方式类型补齐，两种扫描各自最多 limit
条，都按上次查询时间从早到晚取（从没查过的排最前，同时刻按 ID），
逐条先写上次查询时间并提交、再处理，所以长期不能确定的记录轮到
队尾，不会挤掉新记录：
- 会话处理只选待支付订单的、不是 expired 的记录中需要动作的：
  creating（不论是否已过 Stripe 到期时间，都交给 SHOP-TASK-112 的
  reconcile_creating_checkout 重试或对账）、Stripe 到期时间已过的
  open（查询会话），以及 complete（查询会话的付款状态）。
- 补齐单独选付款状态为 paid、付款方式类型为空且有会话 ID 的记录
  （不论订单状态：确认之后订单已是 demo_paid），逐条调用
  SHOP-TASK-113 的 fill_payment_method_type。补齐只是补字段，Stripe
  不可用或未配置时静默留到下一轮，不告警。

取消一律经 SHOP-TASK-114 的 cancel_after_stripe_end，操作者为
system；给了记录 ID 时记录的 expired 与订单的取消在它的同一事务里
完成，它返回 changed（记录在查询 Stripe 之后被 webhook 或另一次
确认改过）时这条留到下一轮。调用 Stripe 时不持有任何数据库锁、
不留着读事务；之后由被调用的函数按统一顺序「会员 → 订单 → Stripe
会话记录」加锁并重查。本模块自己只写两列：上次查询时间与告警时间，
各以只改这一列的 UPDATE 写入并立即提交，期间不再加别的锁。SQLite
不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。

运营告警邮件尚未接入：设计写「告警」之处先记一条 error 级日志，
只含类别与本站记录 ID。每条记录至多告警一次：告警时间已非空（含
SHOP-TASK-112 的 stripe_checkout_needs_review 与 SHOP-TASK-117 在
确认返回 needs_review 时写的）就不再记任何告警，告警时间也不清空。
本模块不提供人工处置的入口。库里的时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.orm import Session

from app.models import Order, StripeCheckout
from app.models.order import ACTOR_SYSTEM, STATUS_AWAITING_PAYMENT
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
)
from app.services.payment import cancel_after_stripe_end
from app.services.stripe_checkout import ReconcileOutcome, reconcile_creating_checkout
from app.services.stripe_confirm import ConfirmResult, confirm_checkout, fill_payment_method_type
from app.services.stripe_gateway import CheckoutSession, StripeGateway, StripeOutcome

logger = logging.getLogger(__name__)

# 第 3、5 条的「24 小时」。
ALERT_AFTER = timedelta(hours=24)

# 告警邮件接入前的 error 级日志类别（只含类别与记录 ID）。
# creating 与 open 记录 24 小时仍无法确定（creating 自记录创建、
# open 自 Stripe 到期时间起算）。
LOG_UNREACHABLE = "stripe_unreachable"
# complete 记录完成 24 小时后付款仍无结果（含查询不可用）。
LOG_PAYMENT_NEEDS_REVIEW = "stripe_payment_needs_review"

# Stripe 给出的付款失败：PaymentIntent 被取消，或付款失败后回到等待付款方式。
FAILED_INTENT_STATUSES = ("canceled", "requires_payment_method")


@dataclass(frozen=True)
class _View:
    """普通读取得的一条记录与它的订单的支付到期时间。"""

    checkout_id: int
    order_id: int
    status: str
    session_id: str | None
    idempotency_key: str
    created_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    alerted: bool
    order_expires_at: datetime


def process_stripe_checkouts(db: Session, gateway: StripeGateway, now: datetime, limit: int) -> int:
    """执行一轮定时处理，返回处理条数：两种扫描选中并处理过的记录数
    之和（Stripe 不可用而跳过的也算处理过）。

    调用方不得持有数据库锁、不得有未提交的改动：本函数逐条提交或
    回滚。now 为不带时区的 UTC（带时区抛 ValueError，先于访问数据库）。
    每条记录的处理见 _process；补齐见模块说明。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    processed = 0
    for checkout_id in _select_ids(db, _due_query(now), limit):
        _touch(db, checkout_id, now)
        _process(db, gateway, checkout_id, now)
        processed += 1
    for checkout_id in _select_ids(db, _unfilled_query(), limit):
        _touch(db, checkout_id, now)
        # 结果（filled、missing、skipped、unavailable）都不再处理：留到下一轮，不告警。
        fill_payment_method_type(db, gateway, checkout_id)
        processed += 1
    return processed


def has_pending_checkouts(db: Session, now: datetime) -> bool:
    """是否有会话处理要选的记录（不含只需补齐付款方式类型的）；只读，
    结束读事务。Stripe 未配置时定时任务据此决定静默跳过，还是照常
    处理（按不可用计时与告警）。"""
    found = db.scalar(_due_query(now).limit(1)) is not None
    db.rollback()
    return found


# ---------------------------------------------------------------- 选取


def _due_query(now: datetime) -> Select[tuple[int]]:
    """待支付订单的不是 expired 的记录中：creating、Stripe 到期时间
    已过的 open，以及 complete。"""
    return (
        select(StripeCheckout.id)
        .join(Order, Order.id == StripeCheckout.order_id)
        .where(
            Order.status == STATUS_AWAITING_PAYMENT,
            or_(
                StripeCheckout.status == CHECKOUT_CREATING,
                and_(StripeCheckout.status == CHECKOUT_OPEN, StripeCheckout.expires_at <= now),
                StripeCheckout.status == CHECKOUT_COMPLETE,
            ),
        )
    )


def _unfilled_query() -> Select[tuple[int]]:
    """付款状态为 paid 而付款方式类型为空、有会话 ID 的记录，不论
    订单与记录状态。"""
    return select(StripeCheckout.id).where(
        StripeCheckout.payment_status == PAYMENT_PAID,
        StripeCheckout.payment_method_type.is_(None),
        StripeCheckout.checkout_session_id.is_not(None),
    )


def _select_ids(db: Session, stmt: Select[tuple[int]], limit: int) -> list[int]:
    # 从没查过的排最前（MySQL 不支持 NULLS FIRST，按 IS NOT NULL 排），再按上次查询时间与 ID。
    ordered = stmt.order_by(
        StripeCheckout.last_polled_at.is_not(None),
        StripeCheckout.last_polled_at,
        StripeCheckout.id,
    )
    ids = list(db.scalars(ordered.limit(max(limit, 0))))
    db.rollback()
    return ids


def _touch(db: Session, checkout_id: int, now: datetime) -> None:
    """写上次查询时间并提交（只改这一列），先于处理：处理中途出错的
    记录也轮到队尾。"""
    stmt = (
        update(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .values(last_polled_at=now)
        .execution_options(synchronize_session=False)
    )
    db.execute(stmt)
    db.commit()


def _read(db: Session, checkout_id: int) -> _View | None:
    """普通读取得记录与订单并结束读事务；记录已是 expired 或订单已
    不是待支付时为空。"""
    stmt = (
        select(StripeCheckout, Order.status, Order.payment_expires_at)
        .join(Order, Order.id == StripeCheckout.order_id)
        .where(StripeCheckout.id == checkout_id)
        .execution_options(populate_existing=True)
    )
    record, order_status, order_expires_at = db.execute(stmt).one()
    view = _View(
        checkout_id=record.id,
        order_id=record.order_id,
        status=record.status,
        session_id=record.checkout_session_id,
        idempotency_key=record.idempotency_key,
        created_at=record.created_at,
        expires_at=record.expires_at,
        completed_at=record.completed_at,
        alerted=record.alerted_at is not None,
        order_expires_at=order_expires_at,
    )
    db.rollback()
    if view.status == CHECKOUT_EXPIRED or order_status != STATUS_AWAITING_PAYMENT:
        return None
    return view


# ---------------------------------------------------------------- 会话处理


def _process(db: Session, gateway: StripeGateway, checkout_id: int, now: datetime) -> None:
    """处理一条选中的记录；选中之后记录已是 expired 或订单已不是
    待支付时不做任何事。

    - creating：调用 reconcile_creating_checkout（到期前以同一幂等键
      与同一快照重试，到期后对账）。记为 expired 时按
      _cancel_if_overdue；取得 open 或 complete 时重读记录，按会话的
      实际状态接着处理；结果不明时订单保留不动，不凭时间取消；
      under_review 已由 SHOP-TASK-112 告警。
    - open：见 _process_open。
    - complete：见 _process_complete。
    """
    view = _read(db, checkout_id)
    if view is None:
        return
    if view.status == CHECKOUT_CREATING:
        outcome = reconcile_creating_checkout(db, gateway, checkout_id, now)
        if outcome is ReconcileOutcome.EXPIRED:
            _cancel_if_overdue(db, view, now)
            return
        if outcome is ReconcileOutcome.PENDING:
            # 创建满 24 小时时对账函数已先转人工（stripe_checkout_needs_review），
            # 这里只在它没写告警时间时兜底。
            _unresolved(db, view, view.created_at, LOG_UNREACHABLE, now)
            return
        if outcome is ReconcileOutcome.UNDER_REVIEW:
            return
        view = _read(db, checkout_id)
        if view is None:
            return
    if view.status == CHECKOUT_OPEN:
        _process_open(db, gateway, view, now)
    elif view.status == CHECKOUT_COMPLETE:
        _process_complete(db, gateway, view, now)


def _process_open(db: Session, gateway: StripeGateway, view: _View, now: datetime) -> None:
    """open 记录：Stripe 到期时间之前不动；之后查询会话。

    - 会话 expired（核对属于这条记录）：给记录 ID 以 system 调用
      cancel_after_stripe_end，记录记为 expired、订单取消（不看本地
      支付到期时间）。
    - 会话 complete 或已付款：调用 confirm_checkout（用这次查询到的
      对象）。paid 不再处理；refunded 按 _cancel_if_overdue；
      needs_review 已由 SHOP-TASK-117 告警；pending（会话 complete 而
      付款未定，记录已改为 complete）留给之后各轮按 complete 处理；
      unavailable（如会话为 open 而已付款，取不到可核对的对象）按
      Stripe 不可用。
    - 其余（仍为 open、状态不认识、核对不符）与查询不成：按 Stripe
      不可用，自 Stripe 到期时间满 24 小时告警 stripe_unreachable。
    """
    if now < view.expires_at or view.session_id is None:
        return
    session = _retrieve(gateway, view.session_id)
    if session is None:
        _unresolved(db, view, view.expires_at, LOG_UNREACHABLE, now)
        return
    if session.status == CHECKOUT_EXPIRED and _belongs(view, session):
        cancel_after_stripe_end(
            db,
            view.order_id,
            ACTOR_SYSTEM,
            now,
            checkout_id=view.checkout_id,
            seen_status=CHECKOUT_OPEN,
        )
        return
    if session.status == CHECKOUT_COMPLETE or session.payment_status == PAYMENT_PAID:
        result = confirm_checkout(db, gateway, view.checkout_id, session, now)
        if result is ConfirmResult.REFUNDED:
            _cancel_if_overdue(db, view, now)
        elif result is ConfirmResult.UNAVAILABLE:
            _unresolved(db, view, view.expires_at, LOG_UNREACHABLE, now)
        return
    _unresolved(db, view, view.expires_at, LOG_UNREACHABLE, now)


def _process_complete(db: Session, gateway: StripeGateway, view: _View, now: datetime) -> None:
    """complete 记录：查询会话的付款状态。

    - 已付款：调用 confirm_checkout（用这次查询到的对象）；结果的去向
      同 _process_open，只是 pending 与 unavailable 都按仍无结果。
    - 未付款，会话核对属于这条记录、仍为 complete，且 PaymentIntent
      为 canceled 或 requires_payment_method（Stripe 给出的付款失败）：
      给记录 ID 以 system 调用 cancel_after_stripe_end，记录记为
      expired、订单取消。
    - 其余（processing、requires_action 等状态，查询不到 PaymentIntent，
      核对不符）与查询不成：继续等待，自完成时间满 24 小时告警
      stripe_payment_needs_review（查询不可用也用这一类）。
    """
    since = view.completed_at or view.created_at
    session = None if view.session_id is None else _retrieve(gateway, view.session_id)
    if session is None:
        _unresolved(db, view, since, LOG_PAYMENT_NEEDS_REVIEW, now)
        return
    if session.payment_status == PAYMENT_PAID:
        result = confirm_checkout(db, gateway, view.checkout_id, session, now)
        if result is ConfirmResult.REFUNDED:
            _cancel_if_overdue(db, view, now)
        elif result in (ConfirmResult.PENDING, ConfirmResult.UNAVAILABLE):
            _unresolved(db, view, since, LOG_PAYMENT_NEEDS_REVIEW, now)
        return
    intent = session.payment_intent
    if (
        session.status == CHECKOUT_COMPLETE
        and _belongs(view, session)
        and intent is not None
        and intent.livemode is not True
        and intent.status in FAILED_INTENT_STATUSES
    ):
        cancel_after_stripe_end(
            db,
            view.order_id,
            ACTOR_SYSTEM,
            now,
            checkout_id=view.checkout_id,
            seen_status=CHECKOUT_COMPLETE,
        )
        return
    _unresolved(db, view, since, LOG_PAYMENT_NEEDS_REVIEW, now)


def _retrieve(gateway: StripeGateway, session_id: str) -> CheckoutSession | None:
    """查询会话；不是 ok（unavailable、live、rejected）时为空，按
    Stripe 不可用处理。"""
    result = gateway.retrieve_checkout_session(session_id)
    if result.outcome is not StripeOutcome.OK:
        return None
    return result.value


def _belongs(view: _View, session: CheckoutSession) -> bool:
    """会话 ID、元数据里的创建幂等键与记录相同，且处于测试环境
    （第 1 条）。"""
    return (
        session.id == view.session_id
        and session.create_key == view.idempotency_key
        and session.livemode is False
    )


def _cancel_if_overdue(db: Session, view: _View, now: datetime) -> None:
    """记录已是 expired（对账确认没有建成，或核对不符写成退款尝试后
    释放）：订单已过本地支付到期时间才不给记录 ID 以 system 调用
    cancel_after_stripe_end；未过期的订单留给访客再发起或之后的超时
    取消。"""
    if now >= view.order_expires_at:
        cancel_after_stripe_end(db, view.order_id, ACTOR_SYSTEM, now)


def _unresolved(db: Session, view: _View, since: datetime, category: str, now: datetime) -> None:
    """仍无法确定：订单不取消、库存继续预留，留到下一轮。

    自 since 满 24 小时、告警时间仍为空时写告警时间并提交，再记一条
    error 日志（类别与记录 ID）；带条件的 UPDATE 保证至多一次。
    """
    if view.alerted or now < since + ALERT_AFTER:
        return
    stmt = (
        update(StripeCheckout)
        .where(
            StripeCheckout.id == view.checkout_id,
            StripeCheckout.status == view.status,
            StripeCheckout.alerted_at.is_(None),
        )
        .values(alerted_at=now)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        return
    db.commit()
    logger.error("%s checkout=%d", category, view.checkout_id)

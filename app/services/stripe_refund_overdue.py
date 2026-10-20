"""Stripe 退款尝试的定时处理：24 小时内以同一幂等键重试、之后分页对账，
与有 Stripe 退款 ID 的尝试的复查。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 8 条：
「已有 Stripe 退款 ID 而状态未终结的，等退款事件或由定时任务查询更新；
已 succeeded 的尝试，定时任务在成功后 30 天内每天、之后每周再查询一次，
不设终止……发现变为 failed 就照上面改并告警」；「还没有 Stripe 退款 ID
（调用结果未知或可重试的错误）的，由定时任务在该尝试创建后 24 小时内
（Stripe 保留幂等键的最短期限）以同一幂等键自动重试……24 小时后不再调用
退款接口，改为列出该 PaymentIntent 退款的全部分页、按元数据里的尝试 ID
对账，找到就采用；找不到或无法列完都保持 pending、占用额度，告警由运营者
在 Stripe 后台跟进」。
以及 docs/HANDOFF.md 0.54 记录的 Kelvin 2026-10-19 决定（不改设计文本）：
「由定时任务对从未调用过的尝试在下一轮发起」；「还没有 Stripe 退款 ID 的
尝试在创建后 24 小时内每 5 分钟以同一幂等键重试一次（从未调用过的立即
处理），有 ID 而仍为 pending 的每 10 分钟查询一次，满 24 小时转对账的每
小时列一次全部分页、直到找到（告警只记一次），已 succeeded 的照第 8 条在
成功后 30 天内每天、之后每周查询一次」；告警时间列只给会反复出现的告警
（24 小时后对账找不到或列不完、Stripe 退款对象与尝试不符）去重，这类告警
每条尝试至多一次。

每轮只选到期的尝试（四种规则见 _due_query），按上次复查时间从早到晚取
（从没查过的排最前，同时刻按 ID），最多 limit 条；逐条先写上次复查时间并
提交、再处理，所以长期不能确定的尝试轮到队尾，不会挤掉新尝试。调用、查询
与写入规则都用 SHOP-TASK-124 的 app/services/stripe_refund.py：调用 Stripe
时不持有任何数据库锁、不留着读事务，写入时在各自的事务里锁住该尝试并提交，
提交之后才记日志。本模块自己只写两列：上次复查时间与对账告警的告警时间，
各以带条件的 UPDATE 写入并立即提交。SQLite 不支持行锁，SQLAlchemy 在
SQLite 上不生成 FOR UPDATE。

运营告警邮件尚未接入：设计写「告警」之处先记一条 error 级日志，只含类别与
本站尝试 ID。stripe_refund_unresolved（对账找不到或列不完）以告警时间去重，
每条尝试至多一次（告警时间已因不符写过的也不再记）；改为 failed 等状态改动
的告警照 SHOP-TASK-124 的规则。本模块不提供把尝试改为 failed 的手段。库里的
时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Select, and_, or_, select, update
from sqlalchemy.orm import Session

from app.models import StripeRefund
from app.models.stripe import STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED
from app.services.stripe_gateway import Refund, StripeGateway, StripeOutcome
from app.services.stripe_refund import (
    RETRY_WINDOW,
    alert_category,
    apply_refund_object,
    refresh_refund_attempt,
    submit_refund_attempt,
)

logger = logging.getLogger(__name__)

# 告警邮件接入前的 error 级日志类别（只含类别与尝试 ID）。
# 会反复出现的告警：创建满 24 小时的尝试对账找不到或列不完；每条尝试至多一次。
LOG_REFUND_UNRESOLVED = "stripe_refund_unresolved"

# HANDOFF 0.54 的频率。
RETRY_INTERVAL = timedelta(minutes=5)
RECONCILE_INTERVAL = timedelta(hours=1)
PENDING_CHECK_INTERVAL = timedelta(minutes=10)
# 第 8 条：已 succeeded 的在成功后 30 天内每天、之后每周。
SUCCEEDED_RECENT = timedelta(days=30)
SUCCEEDED_DAILY = timedelta(days=1)
SUCCEEDED_WEEKLY = timedelta(days=7)

# 对账一次最多列这么多页（每页 100 笔）；超过按无法列完处理，防分页游标不前进时空转。
MAX_LIST_PAGES = 100

# 有 Stripe 退款 ID 时要查询的状态。
_QUERIED_STATUSES = (STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED)


@dataclass(frozen=True)
class _View:
    """普通读取得的一条尝试。"""

    attempt_id: int
    status: str
    stripe_refund_id: str | None
    payment_intent_id: str
    created_at: datetime


def process_stripe_refunds(db: Session, gateway: StripeGateway, now: datetime, limit: int) -> int:
    """执行一轮退款尝试的定时处理，返回处理条数：选中并处理过的尝试数
    （Stripe 不可用而留到下一轮的也算处理过）。

    调用方不得持有数据库锁、不得有未提交的改动：本函数逐条提交或回滚。
    now 为不带时区的 UTC（带时区抛 ValueError，先于访问数据库）。每条
    尝试的处理见 _process。
    """
    _check_now(now)
    processed = 0
    for attempt_id in _select_ids(db, now, limit):
        _touch(db, attempt_id, now)
        _process(db, gateway, attempt_id, now)
        processed += 1
    return processed


def has_due_refunds(db: Session, now: datetime) -> bool:
    """是否有到期的尝试；只读，结束读事务。Stripe 未配置时定时任务据此
    决定静默跳过，还是照常处理（按不可用留到下一轮、对账照常告警）。"""
    _check_now(now)
    found = db.scalar(_due_query(now).limit(1)) is not None
    db.rollback()
    return found


# ---------------------------------------------------------------- 选取


def _due_query(now: datetime) -> Select[tuple[int]]:
    """到期的尝试（四种规则之一）：

    1. pending、没有 Stripe 退款 ID、创建未满 24 小时，调用次数为 0 或
       上次复查时间为空或早于 5 分钟前：以同一幂等键调用退款接口。
    2. pending、没有 Stripe 退款 ID、创建满 24 小时，上次复查时间为空或
       早于 1 小时前：分页对账。
    3. pending、有 Stripe 退款 ID，上次复查时间为空或早于 10 分钟前：查询。
    4. succeeded（有 Stripe 退款 ID），上次复查时间为空，或成功时间在 30 天
       内且上次复查时间早于 1 天前，或成功时间满 30 天且上次复查时间早于
       7 天前：查询。
    """
    window_start = now - RETRY_WINDOW
    recent_start = now - SUCCEEDED_RECENT
    pending_without_id = and_(
        StripeRefund.status == STRIPE_REFUND_PENDING,
        StripeRefund.stripe_refund_id.is_(None),
    )
    retry = and_(
        pending_without_id,
        StripeRefund.created_at > window_start,
        or_(StripeRefund.call_count == 0, _checked_before(now - RETRY_INTERVAL)),
    )
    reconcile = and_(
        pending_without_id,
        StripeRefund.created_at <= window_start,
        _checked_before(now - RECONCILE_INTERVAL),
    )
    pending_check = and_(
        StripeRefund.status == STRIPE_REFUND_PENDING,
        StripeRefund.stripe_refund_id.is_not(None),
        _checked_before(now - PENDING_CHECK_INTERVAL),
    )
    succeeded_check = and_(
        StripeRefund.status == STRIPE_REFUND_SUCCEEDED,
        StripeRefund.stripe_refund_id.is_not(None),
        or_(
            StripeRefund.last_checked_at.is_(None),
            and_(
                StripeRefund.succeeded_at > recent_start,
                StripeRefund.last_checked_at < now - SUCCEEDED_DAILY,
            ),
            and_(
                StripeRefund.succeeded_at <= recent_start,
                StripeRefund.last_checked_at < now - SUCCEEDED_WEEKLY,
            ),
        ),
    )
    return select(StripeRefund.id).where(or_(retry, reconcile, pending_check, succeeded_check))


def _checked_before(cutoff: datetime) -> ColumnElement[bool]:
    """上次复查时间为空或早于 cutoff。"""
    return or_(StripeRefund.last_checked_at.is_(None), StripeRefund.last_checked_at < cutoff)


def _select_ids(db: Session, now: datetime, limit: int) -> list[int]:
    # 从没查过的排最前（MySQL 不支持 NULLS FIRST，按 IS NOT NULL 排），再按上次复查时间与 ID。
    ordered = _due_query(now).order_by(
        StripeRefund.last_checked_at.is_not(None),
        StripeRefund.last_checked_at,
        StripeRefund.id,
    )
    ids = list(db.scalars(ordered.limit(max(limit, 0))))
    db.rollback()
    return ids


def _touch(db: Session, attempt_id: int, now: datetime) -> None:
    """写上次复查时间并提交（只改这一列），先于处理：处理中途出错的
    尝试也轮到队尾。"""
    stmt = (
        update(StripeRefund)
        .where(StripeRefund.id == attempt_id)
        .values(last_checked_at=now)
        .execution_options(synchronize_session=False)
    )
    db.execute(stmt)
    db.commit()


def _read(db: Session, attempt_id: int) -> _View:
    """普通读取得尝试并结束读事务。"""
    stmt = (
        select(StripeRefund)
        .where(StripeRefund.id == attempt_id)
        .execution_options(populate_existing=True)
    )
    attempt = db.scalars(stmt).one()
    view = _View(
        attempt_id=attempt.id,
        status=attempt.status,
        stripe_refund_id=attempt.stripe_refund_id,
        payment_intent_id=attempt.payment_intent_id,
        created_at=attempt.created_at,
    )
    db.rollback()
    return view


# ---------------------------------------------------------------- 处理


def _process(db: Session, gateway: StripeGateway, attempt_id: int, now: datetime) -> None:
    """按尝试此刻的状态处理一条选中的尝试（选中之后可能已被别处改过）：

    - pending、没有 Stripe 退款 ID、创建未满 24 小时：submit_refund_attempt
      （同一幂等键；结果的写入与告警由它完成）。
    - pending、没有 Stripe 退款 ID、创建满 24 小时：见 _reconcile。
    - 有 Stripe 退款 ID、pending 或 succeeded：refresh_refund_attempt
      （succeeded 改为 failed 时由它释放来源并告警）。
    - 其余（failed）：不做任何事。
    """
    view = _read(db, attempt_id)
    if view.status == STRIPE_REFUND_PENDING and view.stripe_refund_id is None:
        if now - view.created_at < RETRY_WINDOW:
            submit_refund_attempt(db, gateway, attempt_id, now)
        else:
            _reconcile(db, gateway, view, now)
        return
    if view.stripe_refund_id is not None and view.status in _QUERIED_STATUSES:
        refresh_refund_attempt(db, gateway, attempt_id, now)


def _reconcile(db: Session, gateway: StripeGateway, view: _View, now: datetime) -> None:
    """创建满 24 小时、仍没有 Stripe 退款 ID 的尝试：不再调用退款接口，
    不持锁逐页列出该 PaymentIntent 的退款，按元数据里的尝试 ID 找。

    - 找到：在自己的事务里经 apply_refund_object 采用（核对与状态改动照
      写入规则），提交后按 alert_category 记告警。
    - 找不到或无法列完（某一页不可用或被拒、has_more 不是布尔值、还有下一页
      却是空页、超过 MAX_LIST_PAGES 页）：保持 pending；仍为 pending、仍没有
      Stripe 退款 ID 且告警时间为空时写告警时间并提交，再记一条
      stripe_refund_unresolved。上次复查时间已在处理前写好。
    """
    refund = _find_refund(gateway, view.payment_intent_id, str(view.attempt_id))
    if refund is not None:
        result = apply_refund_object(db, view.attempt_id, refund, now)
        db.commit()
        category = alert_category(result)
        if category is not None:
            logger.error("%s attempt=%d", category, view.attempt_id)
        return
    stmt = (
        update(StripeRefund)
        .where(
            StripeRefund.id == view.attempt_id,
            StripeRefund.status == STRIPE_REFUND_PENDING,
            StripeRefund.stripe_refund_id.is_(None),
            StripeRefund.alerted_at.is_(None),
        )
        .values(alerted_at=now)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        db.rollback()
        return
    db.commit()
    logger.error("%s attempt=%d", LOG_REFUND_UNRESOLVED, view.attempt_id)


def _find_refund(gateway: StripeGateway, intent_id: str, attempt_ref: str) -> Refund | None:
    """逐页列出该 PaymentIntent 的退款，返回元数据里的尝试 ID 等于
    attempt_ref 的第一笔；找到即停。列完仍没有或无法列完时为空。"""
    starting_after: str | None = None
    for _ in range(MAX_LIST_PAGES):
        result = gateway.list_refunds(intent_id, starting_after)
        page = result.value if result.outcome is StripeOutcome.OK else None
        if page is None:
            return None
        for refund in page.refunds:
            if refund.attempt_id == attempt_ref:
                return refund
        if page.has_more is not True or not page.refunds:
            # has_more 为假即已列完；为空或还有下一页却是空页都算无法列完。
            return None
        starting_after = page.refunds[-1].id
    return None


def _check_now(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")

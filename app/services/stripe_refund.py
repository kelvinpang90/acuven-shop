"""Stripe 退款尝试的写入规则与调用 Stripe 退款接口。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 8 条：「以该记录的本站 ID 为幂等键，并写进退款元数据」；
  「`pending` 与 `requires_action` 记为 pending，`succeeded` 记为 succeeded，
  `failed` 与 `canceled` 记为 failed」；
  「只有 Stripe 明确给出 failed 或 canceled，或 Stripe 以请求错误明确拒绝创建
  （4xx，不含 409 与 429；这时没有建成退款）才记为 failed；后一种不自动重试，
  记下错误类别并告警。其余一律保持 pending、占用额度」；
  「有 Stripe 退款对象的尝试，状态一律以向 Stripe 查询到的该退款当前对象为准」；
  「写入时锁住该尝试的行，只接受 pending → succeeded、pending → failed、
  succeeded → failed 三种改动，其余（如 succeeded → pending、failed → 任何状态）
  忽略」；
  「Stripe 允许已成功的退款之后变为失败，这时照查询结果改为 failed、释放额度」；
  「发现变为 failed 就照上面改并告警」；「24 小时后不再调用退款接口」；
  「同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额（failed
  不计），新建前在锁住该付款的事务里核对」。
- 第 9 条：「CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe」。
- 第 10 条：「日志不记录 Stripe 请求与事件原文」。
以及 docs/HANDOFF.md 0.54 记录的 Kelvin 2026-10-19 决定（不改设计文本）：
「Stripe 以请求错误明确拒绝创建」以适配器归为 rejected 为准，记为 failed 并记下
错误类别（Stripe 错误的 type），不自动重试；告警时间列只给会反复出现的告警
（Stripe 退款对象与尝试不符等）去重，这类告警每条尝试至多一次；状态改动引起的
告警（请求被明确拒绝、成功后又失败）随改动只发生一次，不受它限制。

apply_refund_object 是共用的写入规则：只 flush、不提交（webhook 把事件去重
记录放进同一事务），不写日志，返回枚举结果，由调用方在提交之后按 alert_category
记告警。submit_refund_attempt 与 refresh_refund_attempt 调用 Stripe 时不持有
任何数据库锁、不留着读事务，之后在自己的事务里锁住该尝试写入并提交，提交后才
记日志。调用方不得持有数据库锁、不得有未提交的改动：这两个函数会提交或回滚
当前事务。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。

运营告警邮件尚未接入：设计写「告警」之处先记一条 error 级日志，只含类别与本站
尝试 ID，不记录 Stripe 请求、响应与退款原文。库里的时间一律是不带时区的 UTC。
本模块只做业务规则：批准与手动重试、定时任务、webhook 与后台接口由之后的任务
调用它（SHOP-TASK-117 写的来源为会话的尝试也走这里）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from app.models import StripeRefund
from app.models.stripe import (
    ACTIVE_STRIPE_REFUND_STATUSES,
    ERROR_TYPE_MAX_LENGTH,
    STRIPE_REFUND_FAILED,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services.stripe_gateway import Refund, StripeError, StripeGateway, StripeOutcome

logger = logging.getLogger(__name__)

# 告警邮件接入前的 error 级日志类别（只含类别与尝试 ID）。
# 会反复出现的告警：Stripe 退款对象与尝试不符；每条尝试至多一次。
LOG_REFUND_MISMATCH = "stripe_refund_mismatch"
# 状态改动引起的告警，随改动只记一次，不看也不写告警时间。
LOG_REFUND_REJECTED = "stripe_refund_rejected"
LOG_REFUND_FAILED_AFTER_SUCCESS = "stripe_refund_failed_after_success"

# 第 8 条：「24 小时后不再调用退款接口」（Stripe 保留幂等键的最短期限）。
RETRY_WINDOW = timedelta(hours=24)

# 第 8 条的状态映射；不在表里的 Stripe 状态不改本站状态。
_STATUS_MAP = {
    "pending": STRIPE_REFUND_PENDING,
    "requires_action": STRIPE_REFUND_PENDING,
    "succeeded": STRIPE_REFUND_SUCCEEDED,
    "failed": STRIPE_REFUND_FAILED,
    "canceled": STRIPE_REFUND_FAILED,
}

# 第 8 条只接受的三种改动。
_ALLOWED_CHANGES = frozenset(
    {
        (STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED),
        (STRIPE_REFUND_PENDING, STRIPE_REFUND_FAILED),
        (STRIPE_REFUND_SUCCEEDED, STRIPE_REFUND_FAILED),
    }
)


class ApplyResult(StrEnum):
    """apply_refund_object 的结果。"""

    # 状态按允许的改动改了（成功后又失败另列）。
    CHANGED = "changed"
    # 相符而状态没改（相同、未知或被忽略的改动）；退款 ID 可能补上了。
    UNCHANGED = "unchanged"
    # 不符，本次写了告警时间：调用方提交后记 stripe_refund_mismatch。
    MISMATCH = "mismatch"
    # 不符，告警时间早已非空：不再记告警。
    MISMATCH_ALERTED = "mismatch_alerted"
    # succeeded 改为 failed、释放来源：调用方提交后记告警。
    FAILED_AFTER_SUCCESS = "failed_after_success"


class SubmitResult(StrEnum):
    """submit_refund_attempt 的结果。"""

    # 尝试不是 pending 或已有 Stripe 退款 ID：不调用。
    SKIPPED = "skipped"
    # 尝试创建已满 24 小时：不调用，留给对账。
    WINDOW_CLOSED = "window_closed"
    # 取得退款对象，相符并按写入规则写入（不区分状态是否改动）。
    RECORDED = "recorded"
    # 回放而查询不成：只写了 Stripe 退款 ID，状态留给之后的查询。
    REPLAY_PENDING = "replay_pending"
    # 退款对象与尝试不符：不改状态与 Stripe 退款 ID。
    MISMATCH = "mismatch"
    # 尝试期间已被别处写为 succeeded，退款对象却已失败：改为 failed。
    FAILED_AFTER_SUCCESS = "failed_after_success"
    # Stripe 明确拒绝创建：记为 failed、记下错误类别，不自动重试。
    REJECTED = "rejected"
    # 明确拒绝，而尝试期间已被别处写入：不改状态、不告警。
    REJECTED_IGNORED = "rejected_ignored"
    # Stripe 不可用（含 live）：保持 pending，留给下一轮。
    UNAVAILABLE = "unavailable"


class RefreshResult(StrEnum):
    """refresh_refund_attempt 的结果。"""

    # 尝试还没有 Stripe 退款 ID：不调用。
    SKIPPED = "skipped"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    MISMATCH = "mismatch"
    FAILED_AFTER_SUCCESS = "failed_after_success"
    # 查询不可用或被拒：只写上次复查时间。
    UNAVAILABLE = "unavailable"


_ALERTS = {
    ApplyResult.MISMATCH: LOG_REFUND_MISMATCH,
    ApplyResult.FAILED_AFTER_SUCCESS: LOG_REFUND_FAILED_AFTER_SUCCESS,
}

_SUBMIT_RESULTS = {
    ApplyResult.CHANGED: SubmitResult.RECORDED,
    ApplyResult.UNCHANGED: SubmitResult.RECORDED,
    ApplyResult.MISMATCH: SubmitResult.MISMATCH,
    ApplyResult.MISMATCH_ALERTED: SubmitResult.MISMATCH,
    ApplyResult.FAILED_AFTER_SUCCESS: SubmitResult.FAILED_AFTER_SUCCESS,
}

_REFRESH_RESULTS = {
    ApplyResult.CHANGED: RefreshResult.CHANGED,
    ApplyResult.UNCHANGED: RefreshResult.UNCHANGED,
    ApplyResult.MISMATCH: RefreshResult.MISMATCH,
    ApplyResult.MISMATCH_ALERTED: RefreshResult.MISMATCH,
    ApplyResult.FAILED_AFTER_SUCCESS: RefreshResult.FAILED_AFTER_SUCCESS,
}


# ---------------------------------------------------------------- 状态映射与告警类别


def map_refund_status(stripe_status: str | None) -> str | None:
    """第 8 条的状态映射：pending 与 requires_action 为 pending，
    succeeded 为 succeeded，failed 与 canceled 为 failed；
    其他值（含空）返回 None，即不改状态。"""
    if stripe_status is None:
        return None
    return _STATUS_MAP.get(stripe_status)


def alert_category(result: ApplyResult) -> str | None:
    """apply_refund_object 的结果要在提交之后记的告警类别；
    不需要告警时为 None。"""
    return _ALERTS.get(result)


# ---------------------------------------------------------------- 写入规则


def apply_refund_object(
    db: Session, attempt_id: int, stripe_refund: Refund, now: datetime
) -> ApplyResult:
    """按适配器返回的 Refund 写入一条退款尝试；只 flush、不提交、不写日志。

    stripe_refund 应是查询到的当前对象或退款接口的响应（第 8 条：状态以
    查询到的当前对象为准，由调用方保证）。now 为不带时区的 UTC（带时区抛
    ValueError，先于访问数据库）。锁住该尝试的行后核对：Refund 的
    payment_intent 等于尝试的 PaymentIntent ID，并且已记下 Stripe 退款 ID
    时 Refund 的 ID 等于它、还没记下时元数据里的尝试 ID 等于本尝试。
    - 不符：不改状态、Stripe 退款 ID 与上次复查时间；以带条件的更新在
      告警时间为空时写入它。写到了为 mismatch（调用方提交后记告警），
      早已非空为 mismatch_alerted（不再记）。
    - 相符：Stripe 退款 ID 只在为空时写入；状态只接受三种允许的改动
      （改为 succeeded 写成功时间，改为 failed 清空两个活动来源列），
      其余忽略；写上次复查时间。succeeded 改为 failed 为
      failed_after_success（调用方提交后记告警，不看告警时间），其余
      改动为 changed，没改为 unchanged。
    """
    _check_now(now)
    attempt = _lock_attempt(db, attempt_id)
    return _apply(db, attempt, stripe_refund, now, write_status=True)


def _apply(
    db: Session, attempt: StripeRefund, refund: Refund, now: datetime, *, write_status: bool
) -> ApplyResult:
    """对已锁住的尝试按 apply_refund_object 写入。write_status 为假时只核对
    并写 Stripe 退款 ID（回放而查询不成，状态留给之后的查询）。"""
    if not _matches(attempt, refund):
        return _mark_mismatch(db, attempt, now)
    if attempt.stripe_refund_id is None:
        attempt.stripe_refund_id = refund.id
    if not write_status:
        db.flush()
        return ApplyResult.UNCHANGED
    attempt.last_checked_at = now
    old = attempt.status
    new = map_refund_status(refund.status)
    if new is None or (old, new) not in _ALLOWED_CHANGES:
        db.flush()
        return ApplyResult.UNCHANGED
    attempt.status = new
    if new == STRIPE_REFUND_SUCCEEDED:
        attempt.succeeded_at = now
    else:
        # 改为 failed 释放来源：同一来源随后可再建一条尝试（手动重试）。
        attempt.active_refund_request_id = None
        attempt.active_checkout_id = None
    db.flush()
    if old == STRIPE_REFUND_SUCCEEDED:
        return ApplyResult.FAILED_AFTER_SUCCESS
    return ApplyResult.CHANGED


def _matches(attempt: StripeRefund, refund: Refund) -> bool:
    if refund.payment_intent is None or refund.payment_intent != attempt.payment_intent_id:
        return False
    if attempt.stripe_refund_id is not None:
        return refund.id == attempt.stripe_refund_id
    return refund.attempt_id == str(attempt.id)


def _mark_mismatch(db: Session, attempt: StripeRefund, now: datetime) -> ApplyResult:
    # 带条件的更新：并发的两次写入只有一次写到告警时间，告警只记一次。
    db.flush()
    stmt = (
        update(StripeRefund)
        .where(StripeRefund.id == attempt.id, StripeRefund.alerted_at.is_(None))
        .values(alerted_at=now)
        .execution_options(synchronize_session=False)
    )
    written = db.execute(stmt).rowcount == 1
    db.expire(attempt, ["alerted_at"])
    return ApplyResult.MISMATCH if written else ApplyResult.MISMATCH_ALERTED


# ---------------------------------------------------------------- 调用退款接口


def submit_refund_attempt(
    db: Session, gateway: StripeGateway, attempt_id: int, now: datetime
) -> SubmitResult:
    """以尝试的本站 ID 为幂等键调用 Stripe 退款接口，再按结果写入并提交。

    now 为不带时区的 UTC（带时区抛 ValueError，先于访问数据库）。
    调用方不得持有数据库锁、不得有未提交的改动。
    1. 普通读取得尝试，结束读事务。不是 pending 或已有 Stripe 退款 ID
       为 skipped；创建已满 24 小时为 window_closed。都不调用 Stripe。
    2. 不持有任何数据库锁调用 create_refund：表单为 payment_intent、
       amount（尝试金额）与 metadata[attempt_id]（尝试 ID），幂等键为
       尝试 ID 的十进制字符串。响应为同一幂等键的回放时（内容可能不是
       当前状态），同样不持锁再 retrieve_refund 一次。
    3. 在自己的事务里锁住该尝试，调用次数加一、写上次复查时间，然后：
       - ok：按 apply_refund_object 写入。回放而查询不可用、被拒或查到的
         ID 与回放不同时，只按回放内容写入 Stripe 退款 ID（核对相同），
         不改状态。
       - rejected：重新锁住后仍为 pending 且仍没有 Stripe 退款 ID 时记为
         failed、清空活动来源列，最后的错误类别写 Stripe 错误的 type
         （为空取 code，都为空留空，截到 64 字符），告警
         stripe_refund_rejected；否则（期间被别处写入）不改状态、不告警。
       - unavailable 与 live：保持 pending。
    4. 提交后才记日志（每类一条 error 日志，只含尝试 ID）。
    """
    _check_now(now)
    attempt = _load_attempt(db, attempt_id)
    if attempt.status != STRIPE_REFUND_PENDING or attempt.stripe_refund_id is not None:
        db.rollback()
        return SubmitResult.SKIPPED
    if now - attempt.created_at >= RETRY_WINDOW:
        db.rollback()
        return SubmitResult.WINDOW_CLOSED
    form = refund_form(attempt)
    db.rollback()

    result = gateway.create_refund(form, str(attempt_id))
    created = result.value if result.outcome is StripeOutcome.OK else None
    current = created
    if created is not None and result.replayed:
        # 回放的是当初保存的响应，须再查询当前对象（HANDOFF 0.53 的核验结论）。
        queried = gateway.retrieve_refund(created.id)
        fresh = queried.value if queried.outcome is StripeOutcome.OK else None
        current = fresh if fresh is not None and fresh.id == created.id else None

    attempt = _lock_attempt(db, attempt_id)
    attempt.call_count += 1
    attempt.last_checked_at = now
    log: str | None = None
    if created is not None:
        write_status = current is not None
        applied = _apply(db, attempt, current or created, now, write_status=write_status)
        log = alert_category(applied)
        outcome = _SUBMIT_RESULTS[applied]
        if not write_status and outcome is SubmitResult.RECORDED:
            outcome = SubmitResult.REPLAY_PENDING
    elif result.outcome is StripeOutcome.REJECTED:
        if attempt.status == STRIPE_REFUND_PENDING and attempt.stripe_refund_id is None:
            attempt.status = STRIPE_REFUND_FAILED
            attempt.active_refund_request_id = None
            attempt.active_checkout_id = None
            attempt.last_error_type = _error_type(result.error)
            log = LOG_REFUND_REJECTED
            outcome = SubmitResult.REJECTED
        else:
            outcome = SubmitResult.REJECTED_IGNORED
    else:
        outcome = SubmitResult.UNAVAILABLE
    db.commit()
    if log is not None:
        logger.error("%s attempt=%d", log, attempt_id)
    return outcome


def refund_form(attempt: StripeRefund) -> tuple[tuple[str, str], ...]:
    """退款接口的表单：payment_intent、amount（尝试金额，仙）与
    metadata[attempt_id]（尝试 ID）。"""
    return (
        ("payment_intent", attempt.payment_intent_id),
        ("amount", str(attempt.amount_sen)),
        ("metadata[attempt_id]", str(attempt.id)),
    )


def _error_type(error: StripeError | None) -> str | None:
    # 只存 Stripe 错误的类别代码，不存错误原文。
    if error is None:
        return None
    value = error.type or error.code
    return value[:ERROR_TYPE_MAX_LENGTH] if value else None


def refresh_refund_attempt(
    db: Session, gateway: StripeGateway, attempt_id: int, now: datetime
) -> RefreshResult:
    """向 Stripe 查询已有 Stripe 退款 ID 的尝试，按查询到的当前对象写入并提交。

    now 为不带时区的 UTC（带时区抛 ValueError，先于访问数据库）。
    调用方不得持有数据库锁、不得有未提交的改动。还没有 Stripe 退款 ID
    为 skipped、不调用。不持锁调用 retrieve_refund，再在自己的事务里
    锁住该尝试：ok 时按 apply_refund_object 写入；查询不可用或被拒时
    不改状态、不告警；两种情形都写上次复查时间。提交后按结果记日志。
    """
    _check_now(now)
    attempt = _load_attempt(db, attempt_id)
    refund_id = attempt.stripe_refund_id
    db.rollback()
    if refund_id is None:
        return RefreshResult.SKIPPED

    result = gateway.retrieve_refund(refund_id)
    refund = result.value if result.outcome is StripeOutcome.OK else None

    attempt = _lock_attempt(db, attempt_id)
    log: str | None = None
    if refund is not None:
        applied = _apply(db, attempt, refund, now, write_status=True)
        log = alert_category(applied)
        outcome = _REFRESH_RESULTS[applied]
    else:
        outcome = RefreshResult.UNAVAILABLE
    attempt.last_checked_at = now
    db.commit()
    if log is not None:
        logger.error("%s attempt=%d", log, attempt_id)
    return outcome


# ---------------------------------------------------------------- 合计核对


def refunded_total_statement(payment_intent_id: str) -> Select[tuple[int]]:
    """锁住同一 PaymentIntent 的 pending 与 succeeded 尝试、取金额的
    语句（FOR UPDATE）。"""
    return (
        select(StripeRefund.amount_sen)
        .where(
            StripeRefund.payment_intent_id == payment_intent_id,
            StripeRefund.status.in_(ACTIVE_STRIPE_REFUND_STATUSES),
        )
        .with_for_update()
    )


def refunded_total(db: Session, payment_intent_id: str) -> int:
    """锁住同一 PaymentIntent 的 pending 与 succeeded 尝试，返回金额合计
    （failed 不计）。供批准与手动重试在新建尝试前核对「合计不得超过付款
    金额」（第 8 条）；不提交。"""
    return sum(db.scalars(refunded_total_statement(payment_intent_id)))


# ---------------------------------------------------------------- 读取与加锁


def lock_attempt_statement(attempt_id: int) -> Select[tuple[StripeRefund]]:
    """锁住一条尝试的语句（FOR UPDATE，读出最新内容）。"""
    return (
        select(StripeRefund)
        .where(StripeRefund.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _lock_attempt(db: Session, attempt_id: int) -> StripeRefund:
    return db.scalars(lock_attempt_statement(attempt_id)).one()


def _load_attempt(db: Session, attempt_id: int) -> StripeRefund:
    stmt = (
        select(StripeRefund)
        .where(StripeRefund.id == attempt_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _check_now(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")

"""退款审核：管理员批准与拒绝退款申请。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：requested → 管理员 approved 或 rejected；
  已批准的退款不可再次批准；退款与履约状态分开存储；所有购买件数均已批准退款时
  冻结后续打包、发货与确认收货的推进，保留退款前履约状态；
  所有状态迁移校验当前状态与操作者。
- 「失败、并发与重试」第 1 条：退款审核使用幂等键和数据库唯一约束，
  相同键相同请求返回原结果，同键不同内容报冲突。
- 「数据模型」RefundRequest / RefundLine 一行的「审核人及理由」。
以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定：拒绝时审核理由必填，
批准时可空，去掉首尾空白后 1 到 500 个字符；批准时没有理由存空值；
理由只给管理员看，不出现在访客接口（查单视图只给申请状态）；
批准与拒绝都先锁定所属订单行，与推进发货、访客确认收货同一锁定协议。

规则：
- 审核理由一律先去掉首尾空白，存去掉之后的文本。
  批准：去掉后为空串即存空值，非空时 1 到 500 个字符；
  拒绝：去掉后必须为 1 到 500 个字符。
  不合法抛 ReviewReasonInvalid，不碰数据库。
- 请求指纹见 review_fingerprint。按审核幂等键查到申请时：
  指纹相同返回原结果（不改数据、不写审计），不同抛 IdempotencyConflict。
  没有该键时，申请为 requested 才审核；
  已审核（不论结果）抛 RefundAlreadyReviewed 并带当前状态。
- 批准：申请改为 approved，写审核时间、审核人、审核幂等键与审核请求指纹，
  理由可空；各件保持占用。
- 拒绝：申请改为 rejected，另写理由，并把该申请各件的占用标记置空以释放
  这些件（SHOP-TASK-029 约定由审核置空），这些件之后可再被申请。
- 订单状态在批准与拒绝时都不改。
- 每次实际改变状态都用 SHOP-TASK-035 的 record_audit 写一条审计：
  操作名 admin_refund_approved 或 admin_refund_rejected，
  对象类别 refund_request、对象 ID 为申请 ID，
  旧值与新值为审核前后的状态，不写理由原文。

锁定协议（与 app/services/admin_orders.py 的推进发货、
app/services/order_lookup.py 的访客确认收货相同）：
锁定订单行之前先结束本请求此前的数据库事务（此前只有读取，回滚即可），
使 SELECT … FOR UPDATE（with_for_update）成为新事务的第一条语句。
MySQL 默认的 REPEATABLE READ 下，读取快照在事务的第一条非锁定读时建立；
锁定之前若已读过，快照就早于等锁期间别人提交的审核、发货或确认，
锁定之后的读取（含 SHOP-TASK-029 的 refund_state）会看不到它们。
锁定查询以子查询按申请 ID 找到所属订单，不先单独读申请；
锁定之后才按审核幂等键查申请、读取申请与各件的占用并判断。
审核写入（申请、各件与审计）放在保存点里（begin_nested）：
遇审核幂等键唯一约束冲突（同键已被另一张订单上的审核占用，那张订单不在本次的锁里）
只回滚保存点，订单行的锁保持，再按键重新查询后按上面的规则返回；
重新查询用共享锁读取（锁定读看到最新提交的版本，不受快照限制）。
本模块只 flush、不提交，由调用方提交；
抛出异常时尚未写入任何数据，由调用方回滚并释放锁。

积分返还与追回、会员订单（申请方 member）由之后的会员与积分账本任务接入；
现有订单都是游客订单。后台接口由 SHOP-TASK-041 交付，
管理员授权、CSRF 与请求格式由它先行校验。

本模块不写日志；异常消息不含理由、幂等键或个人资料。
时间一律是不带时区的 UTC。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, RefundLine, RefundLineUnit, RefundRequest
from app.models.refund import (
    REFUND_APPROVED,
    REFUND_REJECTED,
    REFUND_REQUESTED,
    REVIEW_REASON_MAX_LENGTH,
    UNIT_OCCUPIED,
)
from app.services.admin_auth import record_audit

# 审计操作名与对象类别。
ADMIN_REFUND_APPROVED = "admin_refund_approved"
ADMIN_REFUND_REJECTED = "admin_refund_rejected"
AUDIT_TARGET_REFUND_REQUEST = "refund_request"


class RefundRequestNotFound(Exception):
    """申请 ID 不存在。"""


class ReviewReasonInvalid(Exception):
    """审核理由去掉首尾空白后不符合要求（拒绝时为空，或超过 500 个字符）。"""


class IdempotencyConflict(Exception):
    """同一审核幂等键已用于内容不同的审核请求。"""


class RefundAlreadyReviewed(Exception):
    """申请已审核（不论结果）；status 为申请当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class ReviewOutcome(BaseModel):
    """审核结果：申请 ID、状态、审核时间（带 UTC 时区）与审核理由（只给管理员看）。"""

    refund_request_id: int
    status: str
    reviewed_at: datetime
    review_reason: str | None


@dataclass(frozen=True)
class _Review:
    result: str
    action: str
    # 去掉首尾空白之后的理由；批准时没有理由为空。
    reason: str | None


def review_fingerprint(refund_request_id: int, result: str, reason: str) -> str:
    """审核请求指纹。

    {"reason": 去掉首尾空白后的理由（没有理由为空串）, "refund_request_id": 申请 ID,
    "result": "approved" 或 "rejected"} 的 JSON
    （键排序、无空白、非 ASCII 字符转义）的 SHA-256 十六进制。
    """
    content = {"refund_request_id": refund_request_id, "result": result, "reason": reason}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def lock_order_for_request_statement(refund_request_id: int) -> Select[tuple[Order]]:
    """锁定申请所属订单行的查询（SELECT … FOR UPDATE）；申请不存在时查不到行。"""
    order_id = (
        select(RefundRequest.order_id)
        .where(RefundRequest.id == refund_request_id)
        .scalar_subquery()
    )
    return (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        # 会话里可能留着并发提交之前读到的旧值。
        .execution_options(populate_existing=True)
    )


def approve_refund(
    db: Session,
    refund_request_id: int,
    admin_account_id: int,
    reason: str | None,
    review_idempotency_key: str,
    now: datetime,
) -> tuple[ReviewOutcome, bool]:
    """批准退款申请，返回审核结果与是否实际审核（重放为假）。

    理由可空。只 flush、不提交。
    """
    stripped = _strip(reason)
    if len(stripped) > REVIEW_REASON_MAX_LENGTH:
        raise ReviewReasonInvalid
    review = _Review(REFUND_APPROVED, ADMIN_REFUND_APPROVED, stripped or None)
    return _review(db, refund_request_id, admin_account_id, review, review_idempotency_key, now)


def reject_refund(
    db: Session,
    refund_request_id: int,
    admin_account_id: int,
    reason: str | None,
    review_idempotency_key: str,
    now: datetime,
) -> tuple[ReviewOutcome, bool]:
    """拒绝退款申请并释放各件，返回审核结果与是否实际审核（重放为假）。

    理由必填。只 flush、不提交。
    """
    stripped = _strip(reason)
    if not 1 <= len(stripped) <= REVIEW_REASON_MAX_LENGTH:
        raise ReviewReasonInvalid
    review = _Review(REFUND_REJECTED, ADMIN_REFUND_REJECTED, stripped)
    return _review(db, refund_request_id, admin_account_id, review, review_idempotency_key, now)


def _strip(reason: str | None) -> str:
    return "" if reason is None else reason.strip()


def _review(
    db: Session,
    refund_request_id: int,
    admin_account_id: int,
    review: _Review,
    review_idempotency_key: str,
    now: datetime,
) -> tuple[ReviewOutcome, bool]:
    """按以下顺序，每一步不通过即停止：
    1. 结束此前的事务，锁定申请所属订单行（新事务的第一条语句）；
       查不到抛 RefundRequestNotFound。
    2. 按审核幂等键查申请：指纹相同返回原结果，
       不同抛 IdempotencyConflict；都不写入。
    3. 读取申请：不是 requested 抛 RefundAlreadyReviewed 与当前状态。
    4. 读取该申请各件的占用；在保存点里写申请的审核各列、
       拒绝时置空各件的占用标记，写一条审计。
    写入遇唯一约束冲突时只回滚保存点（锁保持），按键重新查询：
    查到时按第 2 步返回，查不到原样抛出。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    fingerprint = review_fingerprint(refund_request_id, review.result, review.reason or "")

    db.rollback()
    if db.scalars(lock_order_for_request_statement(refund_request_id)).one_or_none() is None:
        raise RefundRequestNotFound

    existing = _request_by_review_key(db, review_idempotency_key)
    if existing is not None:
        return _replay(existing, fingerprint), False

    request = _load_request(db, refund_request_id)
    if request.status != REFUND_REQUESTED:
        raise RefundAlreadyReviewed(request.status)

    units = _request_units(db, refund_request_id)
    # requested 的申请占用它的每一件（SHOP-TASK-029）；不成立即数据有误。
    if any(unit.occupied != UNIT_OCCUPIED for unit in units):
        raise RuntimeError("refund request units are not occupied")

    try:
        with db.begin_nested():
            request.status = review.result
            request.reviewed_at = now
            request.reviewer_admin_id = admin_account_id
            request.review_reason = review.reason
            request.review_idempotency_key = review_idempotency_key
            request.review_fingerprint = fingerprint
            if review.result == REFUND_REJECTED:
                for unit in units:
                    unit.occupied = None
            db.flush()
            record_audit(
                db,
                review.action,
                admin_account_id,
                now,
                target_type=AUDIT_TARGET_REFUND_REQUEST,
                target_id=refund_request_id,
                old_value=REFUND_REQUESTED,
                new_value=review.result,
            )
    except IntegrityError:
        # 保存点已回滚，订单行的锁仍在。
        # 同键的审核在另一张订单上先提交了：按它的结果回答。
        existing = _request_by_review_key(db, review_idempotency_key, locking=True)
        if existing is None:
            raise
        return _replay(existing, fingerprint), False

    return _outcome(request), True


def _request_by_review_key(
    db: Session, review_idempotency_key: str, *, locking: bool = False
) -> RefundRequest | None:
    stmt = (
        select(RefundRequest)
        .where(RefundRequest.review_idempotency_key == review_idempotency_key)
        .execution_options(populate_existing=True)
    )
    if locking:
        # 锁定读取最新提交的版本：快照建立之后才提交的同键审核也看得到。
        stmt = stmt.with_for_update(read=True)
    return db.scalars(stmt).one_or_none()


def _load_request(db: Session, refund_request_id: int) -> RefundRequest:
    stmt = (
        select(RefundRequest)
        .where(RefundRequest.id == refund_request_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _request_units(db: Session, refund_request_id: int) -> list[RefundLineUnit]:
    stmt = (
        select(RefundLineUnit)
        .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
        .where(RefundLine.refund_request_id == refund_request_id)
        .order_by(RefundLineUnit.id)
        .execution_options(populate_existing=True)
    )
    return list(db.scalars(stmt))


def _replay(request: RefundRequest, fingerprint: str) -> ReviewOutcome:
    """同键同请求返回原结果；同键不同请求报冲突。都不写入。"""
    if request.review_fingerprint is None or not hmac.compare_digest(
        request.review_fingerprint, fingerprint
    ):
        raise IdempotencyConflict
    return _outcome(request)


def _outcome(request: RefundRequest) -> ReviewOutcome:
    if request.reviewed_at is None:
        raise RuntimeError("reviewed refund request has no review time")
    return ReviewOutcome(
        refund_request_id=request.id,
        status=request.status,
        reviewed_at=request.reviewed_at.replace(tzinfo=UTC),
        review_reason=request.review_reason,
    )

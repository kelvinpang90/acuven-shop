"""短信验证记录满 30 天删除。

依据 docs/DESIGN.md 1.11（提交 2d13250）「资料保留」第 4 条（短信验证请求及发送记录短期保留
用于防滥用）与 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（4）（短信验证记录（含完整
手机号）保留 30 天后删除），以及「失败、并发与重试」第 6 条（定时任务可重复运行、只生效一次）。

只删 SHOP-TASK-012 的 verification_attempts：创建时间不晚于当前时间减 30 天（恰满 30 天即删）
的行，不分状态与用途。不碰短信每日用量、会员、会话与订单。由 app/jobs.py 的定时任务与补跑命令
调用。

时间一律是不带时区的 UTC。本模块不写日志，也不提交：调用方提交或回滚。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import VerificationAttempt

# 保留期（Kelvin 2026-10-08）：创建满这么久的记录删除。
RETENTION = timedelta(days=30)


def retention_cutoff(now: datetime) -> datetime:
    """创建时间不晚于这一时刻的记录到期。now 须为不带时区的 UTC。"""
    if now.tzinfo is not None:
        raise ValueError("now must be naive UTC")
    return now - RETENTION


def delete_expired_verifications(db: Session, now: datetime, limit: int) -> int:
    """删除最多 limit 条到期的短信验证记录，返回删除条数；上限为 0 或负数时不删、返回 0。

    按创建时间从早到晚、同一时间按 ID 从小到大取一批 ID，再以这些 ID 且仍到期为条件删除
    （MySQL 不支持在 DELETE 的子查询里读同一张表）。重复执行时已删的行不再命中；
    另一个执行者同时删掉的行也不计入本次条数。
    """
    cutoff = retention_cutoff(now)
    if limit <= 0:
        return 0
    ids = list(
        db.scalars(
            select(VerificationAttempt.id)
            .where(VerificationAttempt.created_at <= cutoff)
            .order_by(VerificationAttempt.created_at, VerificationAttempt.id)
            .limit(limit)
        )
    )
    if not ids:
        return 0
    result = db.execute(
        delete(VerificationAttempt)
        .where(VerificationAttempt.id.in_(ids), VerificationAttempt.created_at <= cutoff)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount

"""短信验证记录满 30 天删除（SHOP-TASK-072）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「资料保留」第 4 条：「短信验证请求及发送记录短期保留
用于防滥用」；保留期按 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（4）：「短信验证记录
（含完整手机号）保留 30 天后删除。」

delete_expired_verifications 删除 created_at 不晚于 now 减 30 天（恰满 30 天即删除，差一秒保留）
的 verification_attempts 行，不分状态与用途，每次最多 limit 行，按创建时间从早到晚、同一时间按
ID 从小到大；返回删除的行数。剩余的由下一次继续。可重复运行：已删除的行不再是候选，重复执行
只删除新到期的行，没有到期行时返回 0。

只删 verification_attempts：不碰短信每日用量（sms_daily_usage）、会员、会员会话与订单。没有
别的表引用 verification_attempts，删除不受外键约束影响。

事务：先读出候选 ID，再按 ID 删除（MySQL 不支持在 DELETE 的 IN 子查询里用 LIMIT，也不能在
子查询里读正在删除的表），删除时再带上时间条件，然后提交；没有候选时回滚只读事务。函数返回时
不留未结束的事务，所以运行器以 own_transactions 注册它。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import VerificationAttempt

# Kelvin 2026-10-08：短信验证记录保留 30 天（30×24 小时）。
VERIFICATION_RETENTION = timedelta(days=30)


def retention_cutoff(now: datetime) -> datetime:
    """created_at 不晚于这个时刻（不带时区的 UTC）的记录已满保留期。"""
    return now - VERIFICATION_RETENTION


def delete_expired_verifications(db: Session, now: datetime, limit: int) -> int:
    """删除最多 limit 条已满 30 天的短信验证记录并提交，返回删除条数。

    now 是不带时区的 UTC。limit 为 0 或负数时不查询、返回 0。
    """
    if limit <= 0:
        return 0
    cutoff = retention_cutoff(now)
    candidates = (
        select(VerificationAttempt.id)
        .where(VerificationAttempt.created_at <= cutoff)
        .order_by(VerificationAttempt.created_at, VerificationAttempt.id)
        .limit(limit)
    )
    ids = list(db.scalars(candidates))
    if not ids:
        db.rollback()
        return 0
    stmt = (
        delete(VerificationAttempt)
        .where(VerificationAttempt.id.in_(ids), VerificationAttempt.created_at <= cutoff)
        .execution_options(synchronize_session=False)
    )
    deleted = db.execute(stmt).rowcount
    db.commit()
    return deleted

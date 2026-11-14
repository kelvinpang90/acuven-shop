"""AI 生成装饰提案：每日调用计数与每次调用的记录。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条「限制」
一段（下称「第 13 条」），以及 SHOP-TASK-246 写定的表名、列名、类型与约束。

- 计数表以马来西亚日期（UTC+8）为主键，每天至多一行；不另设 id 列。
- 记录表每次调用一行，只记时间、结果类别与输入、输出 token 数；
  不记描述原文与模型回答原文，不关联活动与管理员，不碰个人资料与金额，
  所以不建外键。

时间是不带时区的 UTC。检查约束只用比较、IN 与 IS NULL，MySQL 与 SQLite
都能执行。这里只建表、约束与常量：「不存在则插入」、锁行加一与调用模型
由 SHOP-TASK-248 的服务层实现。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 调用的结果类别（第 13 条「无论成功、不合格、超时或出错都占用当天的一次」）：
# ok 为合格的提案，invalid 为模型回答不合格，timeout 为超时，
# error 为其余失败（含 Billing Hub 拒绝）。
AI_RESULT_OK = "ok"
AI_RESULT_INVALID = "invalid"
AI_RESULT_TIMEOUT = "timeout"
AI_RESULT_ERROR = "error"
AI_RESULTS = (AI_RESULT_OK, AI_RESULT_INVALID, AI_RESULT_TIMEOUT, AI_RESULT_ERROR)


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class PromotionAiDailyUsage(Base):
    """全站每天的 AI 提案调用次数，每个马来西亚日期一行（第 13 条）。

    日期即主键，同一天不会有第二行；调用前先「不存在则插入、主键冲突即忽略」，
    再锁住该行、未达上限才加一。加一后不退回。
    """

    __tablename__ = "promotion_ai_daily_usage"
    __table_args__ = (
        CheckConstraint("call_count >= 0", name="call_count_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    # 马来西亚时间的日期。
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)


class PromotionAiCall(Base):
    """一次 AI 提案调用的记录（第 13 条「每次调用记时间、结果类别与用量」）。

    不记描述原文与模型回答原文；没拿到用量时两个 token 数为空。
    """

    __tablename__ = "promotion_ai_calls"
    __table_args__ = (
        CheckConstraint(f"result IN ({_in_list(AI_RESULTS)})", name="result_valid"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_non_negative"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_non_negative"
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    called_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    result: Mapped[str] = mapped_column(String(16))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

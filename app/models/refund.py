"""退款申请、申请行与逐件记录。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 RefundRequest / RefundLine 一行、
「订单与退款状态」（requested → approved 或 rejected；重复申请同一可退数量被拒绝；部分退款后
余量仍可再次申请，总批准数量不超过购买数量）与「失败、并发与重试」第 1 条（退款申请使用幂等键和
数据库唯一约束），以及 docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 决定：同一订单行只退其中
几件时按件序从前往后取尚未被审核中或已批准的申请占用的件，被拒绝的申请释放所占的件。

审核人、审核理由、审核幂等键与审核请求指纹由 SHOP-TASK-039 加上，依据同一行的「审核人及理由」、
「失败、并发与重试」第 1 条（退款审核使用幂等键和数据库唯一约束）与 docs/HANDOFF.md 0.33 记录的
Kelvin 2026-10-06 决定：拒绝时理由必填，批准时可空且存空值（不存空串）；理由去掉首尾空白后
1 到 500 个字符由写入方保证，库里只保证非空串，上限只由列长保证（MySQL 的 LENGTH 按字节计）。
审核幂等键非空时全表唯一（MySQL 与 SQLite 的唯一约束都允许多个空值）。批准、拒绝与冻结的规则
由 SHOP-TASK-040、接口由 SHOP-TASK-041 实现。

金额一律是 MYR 整数仙；时间一律是不带时区的 UTC。外键一律 RESTRICT，不级联删除：
被申请引用为审核人的管理员账号不能物理删除。不存任何个人资料。

逐件记录的占用标记只允许 1 或空：申请为 requested 或 approved 时为 1，审核拒绝时由审核任务置空。
（所属逐件分摊快照, 占用标记）唯一：MySQL 与 SQLite 的唯一约束都允许多个空值，所以同一件同时
最多被一笔有效申请占用，被拒申请释放后可再申请。

检查约束的写法与 app/models/order.py 相同（只用比较、LENGTH、IN 与 IS NULL）；请求指纹与审核
请求指纹库里只保证长 64，是否全为十六进制由写入方的 hexdigest 保证（申请为
app/services/refunds.py）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_MEMBER,
    REQUEST_FINGERPRINT_LENGTH,
    _sql_in,
    _utcnow,
)

# 申请状态（「订单与退款状态」）。
REFUND_REQUESTED = "requested"
REFUND_APPROVED = "approved"
REFUND_REJECTED = "rejected"

REFUND_STATUSES = (REFUND_REQUESTED, REFUND_APPROVED, REFUND_REJECTED)
REVIEWED_STATUSES = (REFUND_APPROVED, REFUND_REJECTED)

# 申请方：查单页的访客与「我的订单」的会员。
REFUND_ACTOR_TYPES = (ACTOR_GUEST, ACTOR_MEMBER)

# 逐件记录的占用标记：有效申请占用时为 1，被拒释放后为空。
UNIT_OCCUPIED = 1

# 审核理由的列长（Kelvin 2026-10-06：去掉首尾空白后 1 到 500 个字符）。
REVIEW_REASON_MAX_LENGTH = 500


class RefundRequest(Base):
    """一笔退款申请：所属订单、状态、申请方、幂等键与请求指纹、申请金额合计、创建与审核时间，
    以及审核人、审核理由、审核幂等键与审核请求指纹。

    申请金额合计是各申请行金额之和，由写入方在同一事务里保证；运费不计入。
    审核的四列在 requested 时都为空；approved 与 rejected 时审核人、审核幂等键与审核请求指纹
    都非空，rejected 另须有理由。
    """

    __tablename__ = "refund_requests"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(f"status IN {_sql_in(REFUND_STATUSES)}", name="status_valid"),
        CheckConstraint(
            f"actor_type IN {_sql_in(REFUND_ACTOR_TYPES)}",
            name="actor_type_valid",
        ),
        CheckConstraint("LENGTH(idempotency_key) >= 1", name="idempotency_key_not_empty"),
        CheckConstraint(
            f"LENGTH(request_fingerprint) = {REQUEST_FINGERPRINT_LENGTH}",
            name="request_fingerprint_length",
        ),
        CheckConstraint("amount_sen >= 0", name="amount_sen_non_negative"),
        # 分成两条，未知状态只由 status_valid 拒绝。
        CheckConstraint(
            f"status <> '{REFUND_REQUESTED}' OR reviewed_at IS NULL",
            name="requested_has_no_reviewed_at",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(REVIEWED_STATUSES)} OR reviewed_at IS NOT NULL",
            name="reviewed_status_has_reviewed_at",
        ),
        # 审核的各列（SHOP-TASK-039）。排在上面几条之后：SQLite 按定义顺序报第一条不成立的
        # 约束，缺审核时间的 approved 与 rejected 仍报 reviewed_status_has_reviewed_at。
        UniqueConstraint("review_idempotency_key"),
        CheckConstraint(
            f"status <> '{REFUND_REQUESTED}' OR (reviewer_admin_id IS NULL"
            " AND review_reason IS NULL AND review_idempotency_key IS NULL"
            " AND review_fingerprint IS NULL)",
            name="requested_has_no_review",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(REVIEWED_STATUSES)} OR (reviewer_admin_id IS NOT NULL"
            " AND review_idempotency_key IS NOT NULL AND review_fingerprint IS NOT NULL)",
            name="reviewed_status_has_review",
        ),
        CheckConstraint(
            f"status <> '{REFUND_REJECTED}' OR review_reason IS NOT NULL",
            name="rejected_has_review_reason",
        ),
        CheckConstraint(
            "review_reason IS NULL OR LENGTH(review_reason) >= 1",
            name="review_reason_not_empty",
        ),
        CheckConstraint(
            "review_idempotency_key IS NULL OR LENGTH(review_idempotency_key) >= 1",
            name="review_idempotency_key_not_empty",
        ),
        CheckConstraint(
            "review_fingerprint IS NULL"
            f" OR LENGTH(review_fingerprint) = {REQUEST_FINGERPRINT_LENGTH}",
            name="review_fingerprint_length",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16))
    actor_type: Mapped[str] = mapped_column(String(10))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    # 请求内容的 SHA-256，64 位十六进制。
    request_fingerprint: Mapped[str] = mapped_column(String(REQUEST_FINGERPRINT_LENGTH))
    amount_sen: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # requested 时为空；approved、rejected 时为审核时间。
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 审核的管理员；requested 时为空。
    reviewer_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("admin_accounts.id", ondelete="RESTRICT"),
        index=True,
    )
    # 只给管理员看；拒绝时必填，批准时没有理由存空值。
    review_reason: Mapped[str | None] = mapped_column(String(REVIEW_REASON_MAX_LENGTH))
    review_idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # 审核请求内容的 SHA-256，64 位十六进制。
    review_fingerprint: Mapped[str | None] = mapped_column(String(REQUEST_FINGERPRINT_LENGTH))


class RefundLine(Base):
    """申请里的一行：所属申请、所属订单行、件数与该行金额（这几件的逐件现金实付之和）。"""

    __tablename__ = "refund_lines"
    __table_args__ = (
        UniqueConstraint("refund_request_id", "order_item_id"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint("amount_sen >= 0", name="amount_sen_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refund_request_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("refund_requests.id", ondelete="RESTRICT"),
    )
    order_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order_items.id", ondelete="RESTRICT"),
    )
    quantity: Mapped[int] = mapped_column(Integer)
    amount_sen: Mapped[int] = mapped_column(Integer)


class RefundLineUnit(Base):
    """申请行占用的一件：所属申请行、所属逐件分摊快照、该件现金实付与占用标记。

    现金实付在写入时取自逐件分摊快照（SHOP-TASK-010 的 order_item_units）。
    """

    __tablename__ = "refund_line_units"
    __table_args__ = (
        UniqueConstraint("refund_line_id", "order_item_unit_id"),
        UniqueConstraint("order_item_unit_id", "occupied"),
        CheckConstraint("cash_paid_sen >= 0", name="cash_paid_sen_non_negative"),
        CheckConstraint(
            f"occupied IS NULL OR occupied = {UNIT_OCCUPIED}",
            name="occupied_valid",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    refund_line_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("refund_lines.id", ondelete="RESTRICT"),
    )
    order_item_unit_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order_item_units.id", ondelete="RESTRICT"),
    )
    cash_paid_sen: Mapped[int] = mapped_column(Integer)
    # 1：被 requested 或 approved 的申请占用；空：所属申请被拒、已释放。
    occupied: Mapped[int | None] = mapped_column(Integer)

"""积分批次与积分流水。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 PointLedger 一行（获得、抵扣、到期、
退款追回、待抵扣积分的不可覆盖流水；每笔关联订单或退款，余额不得小于零）与「计价、优惠、积分
与库存」第 3、5 条，以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定：积分按批次记账，
每单获得的积分为一个批次，抵扣先用先到期的批次；退款返还按抵扣的相反顺序（后到期的先还），
已过期的批次不返还；批次在取得日（马来西亚时间）加 12 个自然月那一天的 23:59:59（马来西亚
时间）到期，没有那一天取该月最后一天；已预占的积分以预占时为准。会员注销时作废批次剩余积分
依据「权限与资料保护」第 3 条。

两张表都只追加：批次行写入后不再修改，流水行不修改不删除。余额不存列，由流水求和得出：
- 批次非空的流水合计为该批次剩余可用积分（earn 写入批次的获得积分，其后的抵扣、释放、到期、
  返还、追回、作废与偿还都记在该批次上）；
- 批次为空的流水构成该会员的待抵扣账户，合计为仍欠的积分（debt_add 记入缺口，批次为空的
  debt_repay 减少缺口）；
二者不小于零由之后的规则任务在会员行锁下保证，库里不能用检查约束表达。某单在某批次的净抵扣
为该单在该批次的 redeem、release 与 refund_return 流水之和取负；退款返还以整单为单位按
HANDOFF 0.46 从后到期的批次起返还，不需要逐件与批次的对应。

时间一律是不带时区的 UTC；积分一律是整数，不与仙混用。外键一律 RESTRICT：被流水引用的批次、
被批次或流水引用的会员、订单与退款申请都不能物理删除。除设计指定可空的列外全部非空：检查约束
遇到空值会放行，非空约束不能省。检查约束只用比较、IN、NOT IN 与 IS NULL，MySQL 与 SQLite
都能执行。流水以（批次、会员）复合外键指向批次的（id、会员），保证流水与批次属于同一会员；
批次为空时复合外键不检查（MySQL 与 SQLite 都按 MATCH SIMPLE）。
这里只建表与约束：余额计算、预占、释放、到期、返还、追回、作废与偿还都由之后的规则任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 流水类别。订单列记哪一单见各行说明。
# earn：支付成功后获得积分，写入新批次；订单为该批次的来源订单。
KIND_EARN = "earn"
# redeem：结账抵扣（下单时预占即写入），从批次扣出；订单为所用订单。
KIND_REDEEM = "redeem"
# release：订单取消或支付超时，把预占的积分还回原批次；订单为所用订单。
KIND_RELEASE = "release"
# expire：批次到期，扣去剩余积分（已预占的部分不在剩余里）；订单为该批次的来源订单。
KIND_EXPIRE = "expire"
# refund_return：退款批准时返还原抵扣、未过期的积分；订单为被退款的订单。
KIND_REFUND_RETURN = "refund_return"
# refund_reversal：退款批准时追回获得积分，从批次扣出；订单为被退款的订单。
KIND_REFUND_REVERSAL = "refund_reversal"
# forfeit：会员注销时作废批次剩余积分；订单为该批次的来源订单。
KIND_FORFEIT = "forfeit"
# debt_add：追回时余额不足的缺口记入待抵扣账户（批次为空）；订单为被退款的订单。
KIND_DEBT_ADD = "debt_add"
# debt_repay：以后获得积分先偿还缺口，成对写入：批次非空的一条从新批次扣出，批次为空的一条
# 减少待抵扣；订单为新批次的来源订单。
KIND_DEBT_REPAY = "debt_repay"

ENTRY_KINDS = (
    KIND_EARN,
    KIND_REDEEM,
    KIND_RELEASE,
    KIND_EXPIRE,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_FORFEIT,
    KIND_DEBT_ADD,
    KIND_DEBT_REPAY,
)
# 积分变动大于 0 与小于 0 的类别。
POSITIVE_KINDS = (KIND_EARN, KIND_RELEASE, KIND_REFUND_RETURN, KIND_DEBT_ADD)
NEGATIVE_KINDS = (KIND_REDEEM, KIND_EXPIRE, KIND_REFUND_REVERSAL, KIND_FORFEIT, KIND_DEBT_REPAY)
# 批次必须非空的类别；debt_add 批次必须为空，debt_repay 两者皆可。
LOT_KINDS = (
    KIND_EARN,
    KIND_REDEEM,
    KIND_RELEASE,
    KIND_EXPIRE,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_FORFEIT,
)
# 退款申请必须非空的类别；其余类别退款申请必须为空。
REFUND_KINDS = (KIND_REFUND_RETURN, KIND_REFUND_REVERSAL, KIND_DEBT_ADD)
NON_REFUND_KINDS = tuple(kind for kind in ENTRY_KINDS if kind not in REFUND_KINDS)

KIND_MAX_LENGTH = 20


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class PointLot(Base):
    """一个积分批次：一单获得的积分，所属会员、来源订单、获得积分、取得与到期时间。

    一单至多一个批次（来源订单全表唯一）。到期时间是仍有效的最后一刻，由写入方按 Kelvin
    2026-10-10 的决定（取得日的马来西亚日期加 12 个自然月、当天 23:59:59 马来西亚时间，没有那天
    取该月最后一天）换算成 UTC。行写入后不再修改，剩余可用积分由批次上的流水求和。按（会员、
    到期时间）的索引供之后先到期先使用与到期处理；（id、会员）唯一是流水复合外键的目标。
    """

    __tablename__ = "point_lots"
    __table_args__ = (
        UniqueConstraint("order_id"),
        # 复合外键的目标：流水的批次须属于流水的会员。
        UniqueConstraint("id", "member_id"),
        Index("ix_point_lots_member_id_expires_at", "member_id", "expires_at"),
        CheckConstraint("points > 0", name="points_positive"),
        CheckConstraint("expires_at > acquired_at", name="expires_after_acquired"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("members.id", ondelete="RESTRICT"),
    )
    # 来源订单：获得这批积分的那一单。
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    points: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime)
    # 仍有效的最后一刻。
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PointEntry(Base):
    """一条积分流水：所属会员、批次（可空）、类别、有符号积分变动、订单、退款申请（可空）与时间。

    订单非空：获得、抵扣、释放、到期、作废与偿还记来源或所用订单，其中到期与作废记该批次的
    来源订单；返还、追回与待抵扣记被退款的订单。各类别的变动符号、批次与退款申请是否为空
    逐条由检查约束保证；debt_repay 的成对写入由规则任务保证。只追加，不修改不删除。
    """

    __tablename__ = "point_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["lot_id", "member_id"],
            ["point_lots.id", "point_lots.member_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_point_entries_member_id_created_at", "member_id", "created_at"),
        Index("ix_point_entries_lot_id", "lot_id"),
        Index("ix_point_entries_order_id", "order_id"),
        CheckConstraint(f"kind IN {_sql_in(ENTRY_KINDS)}", name="kind_valid"),
        CheckConstraint("points <> 0", name="points_not_zero"),
        # 下面各条都只约束已知类别，未知类别只由 kind_valid 拒绝。
        CheckConstraint(
            f"kind NOT IN {_sql_in(POSITIVE_KINDS)} OR points > 0",
            name="positive_kind_points",
        ),
        CheckConstraint(
            f"kind NOT IN {_sql_in(NEGATIVE_KINDS)} OR points < 0",
            name="negative_kind_points",
        ),
        CheckConstraint(
            f"kind NOT IN {_sql_in(LOT_KINDS)} OR lot_id IS NOT NULL",
            name="lot_kind_has_lot",
        ),
        CheckConstraint(
            f"kind <> '{KIND_DEBT_ADD}' OR lot_id IS NULL",
            name="debt_add_has_no_lot",
        ),
        CheckConstraint(
            f"kind NOT IN {_sql_in(REFUND_KINDS)} OR refund_request_id IS NOT NULL",
            name="refund_kind_has_refund",
        ),
        CheckConstraint(
            f"kind NOT IN {_sql_in(NON_REFUND_KINDS)} OR refund_request_id IS NULL",
            name="non_refund_kind_has_no_refund",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("members.id", ondelete="RESTRICT"),
    )
    # 空：待抵扣账户的流水（debt_add 与成对 debt_repay 中减少待抵扣的一条）。
    lot_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(KIND_MAX_LENGTH))
    # 有符号：增加为正，减少为负，不为 0。
    points: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    # refund_return、refund_reversal 与 debt_add 为批准的退款申请，其余类别为空。
    refund_request_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("refund_requests.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

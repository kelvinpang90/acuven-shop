"""Point lots and point entries.

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 PointLedger 一行与「计价、优惠、积分与
库存」第 3、5 条，以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（积分按批次记账，
先用先到期的批次，退款按抵扣的相反顺序返还；批次在取得日加 12 个自然月那天结束时到期；预占以
预占时为准），与 app/models/points.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。alembic check 不比较检查约束与表选项，改这里或
模型时须人工核对两边一致。取值直接写出，不 import app：模型以后改了，本迁移不跟着变。
时间是不带时区的 UTC；积分是整数。外键一律 RESTRICT。
upgrade 先建 point_lots 及其索引，再建引用它与 members、orders、refund_requests 的
point_entries 及其三个索引；downgrade 按依赖倒序删除（先 point_entries 连同其索引，再
point_lots）。迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "point_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "points > 0",
            name=op.f("ck_point_lots_points_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > acquired_at",
            name=op.f("ck_point_lots_expires_after_acquired"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_point_lots_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_point_lots_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_lots")),
        sa.UniqueConstraint("order_id", name=op.f("uq_point_lots_order_id")),
        sa.UniqueConstraint("id", "member_id", name=op.f("uq_point_lots_id_member_id")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_point_lots_member_id_expires_at"),
        "point_lots",
        ["member_id", "expires_at"],
        unique=False,
    )
    op.create_table(
        "point_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("refund_request_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('earn', 'redeem', 'release', 'expire', 'refund_return',"
            " 'refund_reversal', 'forfeit', 'debt_add', 'debt_repay')",
            name=op.f("ck_point_entries_kind_valid"),
        ),
        sa.CheckConstraint(
            "points <> 0",
            name=op.f("ck_point_entries_points_not_zero"),
        ),
        sa.CheckConstraint(
            "kind NOT IN ('earn', 'release', 'refund_return', 'debt_add') OR points > 0",
            name=op.f("ck_point_entries_positive_kind_points"),
        ),
        sa.CheckConstraint(
            "kind NOT IN ('redeem', 'expire', 'refund_reversal', 'forfeit', 'debt_repay')"
            " OR points < 0",
            name=op.f("ck_point_entries_negative_kind_points"),
        ),
        sa.CheckConstraint(
            "kind NOT IN ('earn', 'redeem', 'release', 'expire', 'refund_return',"
            " 'refund_reversal', 'forfeit') OR lot_id IS NOT NULL",
            name=op.f("ck_point_entries_lot_kind_has_lot"),
        ),
        sa.CheckConstraint(
            "kind <> 'debt_add' OR lot_id IS NULL",
            name=op.f("ck_point_entries_debt_add_has_no_lot"),
        ),
        sa.CheckConstraint(
            "kind NOT IN ('refund_return', 'refund_reversal', 'debt_add')"
            " OR refund_request_id IS NOT NULL",
            name=op.f("ck_point_entries_refund_kind_has_refund"),
        ),
        sa.CheckConstraint(
            "kind NOT IN ('earn', 'redeem', 'release', 'expire', 'forfeit', 'debt_repay')"
            " OR refund_request_id IS NULL",
            name=op.f("ck_point_entries_non_refund_kind_has_no_refund"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_point_entries_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id", "member_id"],
            ["point_lots.id", "point_lots.member_id"],
            name=op.f("fk_point_entries_lot_id_point_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_point_entries_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["refund_request_id"],
            ["refund_requests.id"],
            name=op.f("fk_point_entries_refund_request_id_refund_requests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_point_entries")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_point_entries_member_id_created_at"),
        "point_entries",
        ["member_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_point_entries_lot_id"),
        "point_entries",
        ["lot_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_point_entries_order_id"),
        "point_entries",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    # 索引随表一起删，不先单独删：MySQL 会让外键改用以同列开头的这些索引、去掉自动建的索引，
    # 逐个删到外键所需的那个会报错（与 0018 相同）。
    op.drop_table("point_entries")
    op.drop_table("point_lots")

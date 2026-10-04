"""Order access sessions and per-order access grants.

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条（游客短期凭据与查单授权
以服务端保存的会话实现）与第 7 条，与 app/models/order_access.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；外键一律 RESTRICT。授权表的会话外键由唯一约束
(session_id, order_id, scope) 的最左列支撑，订单外键由 ix_order_access_grants_order_id 支撑，
MySQL 不另建隐式索引。
downgrade 先删引用会话的授权表（其索引随表删除），再删会话表。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "order_access_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "LENGTH(token_hash) = 64",
            name=op.f("ck_order_access_sessions_token_hash_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_order_access_sessions_expires_after_created"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_access_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_order_access_sessions_token_hash")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "order_access_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "scope IN ('guest_checkout', 'lookup')",
            name=op.f("ck_order_access_grants_scope_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_order_access_grants_expires_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["order_access_sessions.id"],
            name=op.f("fk_order_access_grants_session_id_order_access_sessions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_access_grants_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_access_grants")),
        sa.UniqueConstraint(
            "session_id",
            "order_id",
            "scope",
            name=op.f("uq_order_access_grants_session_id_order_id_scope"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_order_access_grants_order_id"),
        "order_access_grants",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    # 不先单独删订单 ID 索引：MySQL 不许删除外键仍需要的索引；删表时索引一并删除。
    op.drop_table("order_access_grants")
    op.drop_table("order_access_sessions")

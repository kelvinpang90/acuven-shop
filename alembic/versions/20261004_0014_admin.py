"""Admin accounts, admin sessions and audit events.

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 AdminAccount / AuditEvent 一行、
「权限与资料保护」第 4–6 条与 docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员登录决定，
与 app/models/admin.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；指向管理员账号的外键一律 RESTRICT。

账号表的单例槽只能是 1 且全表唯一，故库里至多一个管理员账号。会话与审计记录到账号的外键
由 MySQL 自行建索引（与 member_sessions.member_id 相同）；审计记录另按发生时间建普通索引。

downgrade 按依赖倒序删表：先删审计记录与会话，再删账号。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("singleton_slot", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("password_updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "LENGTH(username) >= 3 AND LENGTH(username) <= 32",
            name=op.f("ck_admin_accounts_username_length"),
        ),
        sa.CheckConstraint(
            "singleton_slot = 1",
            name=op.f("ck_admin_accounts_singleton_slot_one"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_accounts")),
        sa.UniqueConstraint("username", name=op.f("uq_admin_accounts_username")),
        sa.UniqueConstraint("singleton_slot", name=op.f("uq_admin_accounts_singleton_slot")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_account_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "LENGTH(token_hash) = 64",
            name=op.f("ck_admin_sessions_token_hash_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_admin_sessions_expires_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_account_id"],
            ["admin_accounts.id"],
            name=op.f("fk_admin_sessions_admin_account_id_admin_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_admin_sessions_token_hash")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("admin_account_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("old_value", sa.String(length=255), nullable=True),
        sa.Column("new_value", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "LENGTH(action) >= 1 AND LENGTH(action) <= 40",
            name=op.f("ck_audit_events_action_length"),
        ),
        sa.CheckConstraint(
            "target_type IS NULL OR (LENGTH(target_type) >= 1 AND LENGTH(target_type) <= 20)",
            name=op.f("ck_audit_events_target_type_length"),
        ),
        sa.CheckConstraint(
            "(target_type IS NULL AND target_id IS NULL)"
            " OR (target_type IS NOT NULL AND target_id IS NOT NULL)",
            name=op.f("ck_audit_events_target_both_or_neither"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_account_id"],
            ["admin_accounts.id"],
            name=op.f("fk_audit_events_admin_account_id_admin_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_audit_events_occurred_at"),
        "audit_events",
        ["occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("admin_sessions")
    op.drop_table("admin_accounts")

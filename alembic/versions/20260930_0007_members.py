"""Members, member sessions, SMS verification attempts, SMS daily usage; order member link.

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Member / VerificationAttempt 一行，
以及「失败、并发与重试」「权限与资料保护」「资料保留」，与 app/models/member.py、
app/models/order.py 一致。约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
时间是不带时区的 UTC；短信费用是整数微美元。指向会员的外键一律 RESTRICT，会员行不物理删除。

订单表的两条认领状态检查约束在模型里挂在列上（原因见 app/models/order.py 的 Order），
这里在 MySQL 上用 ALTER TABLE 加成表级约束，名字与表达式相同。已有订单都是游客订单，
认领状态先以服务端默认值 open 补齐，再去掉默认值，与模型一致。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'deleted')",
            name=op.f("ck_members_status_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'active' OR (phone IS NOT NULL AND deleted_at IS NULL)",
            name=op.f("ck_members_active_has_phone"),
        ),
        sa.CheckConstraint(
            "status <> 'deleted'"
            " OR (phone IS NULL AND password_hash IS NULL AND deleted_at IS NOT NULL)",
            name=op.f("ck_members_deleted_cleared"),
        ),
        sa.CheckConstraint(
            "phone IS NULL OR (phone LIKE '+%' AND LENGTH(phone) <= 16)",
            name=op.f("ck_members_phone_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_members")),
        sa.UniqueConstraint("phone", name=op.f("uq_members_phone")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "member_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "LENGTH(token_hash) = 64",
            name=op.f("ck_member_sessions_token_hash_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_member_sessions_expires_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            name=op.f("fk_member_sessions_member_id_members"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_member_sessions_token_hash")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "verification_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "phone LIKE '+%' AND LENGTH(phone) <= 16",
            name=op.f("ck_verification_attempts_phone_format"),
        ),
        sa.CheckConstraint(
            "purpose IN ('checkout', 'register', 'login', 'reset_password', 'delete_account')",
            name=op.f("ck_verification_attempts_purpose_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'approved', 'rejected', 'undeliverable', 'suspended')",
            name=op.f("ck_verification_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('sent', 'approved', 'rejected') OR provider_request_id IS NOT NULL",
            name=op.f("ck_verification_attempts_accepted_has_request_id"),
        ),
        sa.CheckConstraint(
            "status <> 'suspended' OR provider_request_id IS NULL",
            name=op.f("ck_verification_attempts_suspended_no_request_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_attempts")),
        sa.UniqueConstraint(
            "provider_request_id",
            name=op.f("uq_verification_attempts_provider_request_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_verification_attempts_phone_created_at"),
        "verification_attempts",
        ["phone", "created_at"],
        unique=False,
    )
    op.create_table(
        "sms_daily_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("settled_micro_usd", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "sent_count >= 0",
            name=op.f("ck_sms_daily_usage_sent_count_non_negative"),
        ),
        sa.CheckConstraint(
            "reserved_micro_usd >= 0",
            name=op.f("ck_sms_daily_usage_reserved_micro_usd_non_negative"),
        ),
        sa.CheckConstraint(
            "settled_micro_usd >= 0",
            name=op.f("ck_sms_daily_usage_settled_micro_usd_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_daily_usage")),
        sa.UniqueConstraint("usage_date", name=op.f("uq_sms_daily_usage_usage_date")),
        **TABLE_OPTIONS,
    )

    op.add_column("orders", sa.Column("member_id", sa.Integer(), nullable=True))
    op.add_column(
        "orders",
        sa.Column("claim_status", sa.String(length=16), nullable=False, server_default="open"),
    )
    op.alter_column(
        "orders",
        "claim_status",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=None,
    )
    op.create_index(op.f("ix_orders_member_id"), "orders", ["member_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_orders_member_id_members"),
        "orders",
        "members",
        ["member_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_orders_claim_status_valid"),
        "orders",
        "claim_status IN ('open', 'claimed', 'not_claimable')",
    )
    op.create_check_constraint(
        op.f("ck_orders_member_claim_not_open"),
        "orders",
        "member_id IS NULL OR claim_status <> 'open'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_orders_member_claim_not_open"), "orders", type_="check")
    op.drop_constraint(op.f("ck_orders_claim_status_valid"), "orders", type_="check")
    op.drop_constraint(op.f("fk_orders_member_id_members"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_member_id"), table_name="orders")
    op.drop_column("orders", "claim_status")
    op.drop_column("orders", "member_id")
    op.drop_table("sms_daily_usage")
    op.drop_index(
        op.f("ix_verification_attempts_phone_created_at"),
        table_name="verification_attempts",
    )
    op.drop_table("verification_attempts")
    op.drop_table("member_sessions")
    op.drop_table("members")

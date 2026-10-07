"""Admin login name becomes an email: widen admin_accounts.username to 254 characters.

依据 docs/HANDOFF.md 0.37 记录的 Kelvin 2026-10-07 决定（唯一管理员以邮箱登录：列名仍叫
username，取值改为邮箱，列加长到 254 个字符），与 app/models/admin.py 一致。
约束名按 app/db/base.py 的命名约定写出。alembic check 不比较检查约束，改这里或模型时须人工核对
两边一致。长度直接写数字，不 import app：模型以后改了，本迁移不跟着变。

upgrade 先删长度检查约束，把列改为 VARCHAR(254) 非空，再以同名重建为长度 3 到 254。
downgrade 先确认没有超过 32 个字符的登录名，有则抛错、不截断（截断会改掉管理员的登录名，
且可能与唯一约束冲突）；通过后删约束、把列改回 VARCHAR(32) 非空，再以同名重建为长度 3 到 32。
登录名都是 ASCII，MySQL 按字节计的 LENGTH 与字符数相同；downgrade 的确认用 CHAR_LENGTH。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "admin_accounts"
LENGTH_CHECK = "ck_admin_accounts_username_length"
USERNAME_MIN_LENGTH = 3
OLD_MAX_LENGTH = 32
NEW_MAX_LENGTH = 254


def _set_username_length(old_length: int, new_length: int) -> None:
    op.drop_constraint(op.f(LENGTH_CHECK), TABLE, type_="check")
    op.alter_column(
        TABLE,
        "username",
        existing_type=sa.String(length=old_length),
        type_=sa.String(length=new_length),
        existing_nullable=False,
        nullable=False,
    )
    op.create_check_constraint(
        op.f(LENGTH_CHECK),
        TABLE,
        f"LENGTH(username) >= {USERNAME_MIN_LENGTH} AND LENGTH(username) <= {new_length}",
    )


def upgrade() -> None:
    _set_username_length(OLD_MAX_LENGTH, NEW_MAX_LENGTH)


def downgrade() -> None:
    too_long = (
        op.get_bind()
        .execute(
            sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE CHAR_LENGTH(username) > :limit"),
            {"limit": OLD_MAX_LENGTH},
        )
        .scalar_one()
    )
    if too_long:
        # 不输出登录名本身：它是管理员的邮箱。
        raise RuntimeError(
            f"cannot downgrade: {too_long} admin login name(s) longer than {OLD_MAX_LENGTH}"
            " characters; shorten them first (this migration does not truncate)"
        )
    _set_username_length(NEW_MAX_LENGTH, OLD_MAX_LENGTH)

"""Empty baseline: no tables yet.

Business tables arrive with their numbered tasks; this revision only gives
later migrations a fixed root and lets the deploy pipeline exercise
`alembic upgrade head` against the shared database from day one.

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

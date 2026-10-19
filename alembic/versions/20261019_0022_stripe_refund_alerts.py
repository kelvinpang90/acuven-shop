"""Stripe refund attempts: alerted_at column.

依据 docs/DESIGN.md 1.12（提交 acf576e）「数据模型」的 StripeCheckout /
StripeEvent / StripeRefund 一行（错误类别与时间）与「Stripe 测试环境支付」
第 8 条，以及 docs/HANDOFF.md 0.54 记录的 Kelvin 2026-10-19 决定（退款尝试表
补告警时间列，给会反复出现的告警去重）；与 app/models/stripe.py 一致。
时间是不带时区的 UTC，写法与 stripe_checkouts.alerted_at 相同。

upgrade 只给 stripe_refunds 加可空的 alerted_at，已有行为空（还没发过这类
告警）；downgrade 只删除它。列不在任何约束与索引里，可以直接删。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stripe_refunds", sa.Column("alerted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("stripe_refunds", "alerted_at")

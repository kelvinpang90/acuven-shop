"""Orders, order items, unit allocations, recipients, payment attempts, order events.

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Order / OrderItem、OrderRecipient、
PaymentAttempt / OrderEvent 三行，与 app/models/order.py 一致。
约束名按 app/db/base.py 的命名约定逐个写出。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致。
金额是 MYR 整数仙，积分是整数积分；时间是不带时区的 UTC。
订单及其子表的外键一律 RESTRICT；订单行到 SKU 规格的外键在规格被删除时置空。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同；迁移不 import app，模型以后改了旧迁移不跟着变。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

ORDER_STATUSES = (
    "('awaiting_demo_payment', 'demo_paid', 'demo_packed', 'demo_shipped',"
    " 'demo_completed', 'demo_cancelled')"
)


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("subtotal_sen", sa.Integer(), nullable=False),
        sa.Column("coupon_discount_sen", sa.Integer(), nullable=False),
        sa.Column("points_redeemed", sa.Integer(), nullable=False),
        sa.Column("shipping_fee_sen", sa.Integer(), nullable=False),
        sa.Column("total_sen", sa.Integer(), nullable=False),
        sa.Column("points_earned", sa.Integer(), nullable=False),
        sa.Column("shipping_zone_code", sa.String(length=5), nullable=False),
        sa.Column("shipping_rate_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payment_expires_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "LENGTH(order_number) = 16",
            name=op.f("ck_orders_order_number_length"),
        ),
        sa.CheckConstraint(
            f"status IN {ORDER_STATUSES}",
            name=op.f("ck_orders_status_valid"),
        ),
        sa.CheckConstraint(
            "subtotal_sen >= 0",
            name=op.f("ck_orders_subtotal_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "coupon_discount_sen >= 0",
            name=op.f("ck_orders_coupon_discount_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "points_redeemed >= 0",
            name=op.f("ck_orders_points_redeemed_non_negative"),
        ),
        sa.CheckConstraint(
            "shipping_fee_sen >= 0",
            name=op.f("ck_orders_shipping_fee_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "total_sen >= 0",
            name=op.f("ck_orders_total_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "points_earned >= 0",
            name=op.f("ck_orders_points_earned_non_negative"),
        ),
        sa.CheckConstraint(
            "coupon_discount_sen + points_redeemed <= subtotal_sen",
            name=op.f("ck_orders_discounts_within_subtotal"),
        ),
        sa.CheckConstraint(
            "total_sen = subtotal_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen",
            name=op.f("ck_orders_total_formula"),
        ),
        sa.CheckConstraint(
            "total_sen >= shipping_fee_sen",
            name=op.f("ck_orders_total_covers_shipping"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('demo_paid', 'demo_packed', 'demo_shipped', 'demo_completed')"
            " OR paid_at IS NOT NULL",
            name=op.f("ck_orders_paid_status_has_paid_at"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('awaiting_demo_payment', 'demo_cancelled') OR paid_at IS NULL",
            name=op.f("ck_orders_unpaid_status_no_paid_at"),
        ),
        sa.CheckConstraint(
            "LENGTH(idempotency_key) >= 1",
            name=op.f("ck_orders_idempotency_key_not_empty"),
        ),
        sa.CheckConstraint(
            "LENGTH(request_fingerprint) = 64",
            name=op.f("ck_orders_request_fingerprint_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("order_number", name=op.f("uq_orders_order_number")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_orders_idempotency_key")),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("product_name_en", sa.String(length=200), nullable=False),
        sa.Column("product_name_zh", sa.String(length=200), nullable=False),
        sa.Column("product_name_ms", sa.String(length=200), nullable=False),
        sa.Column("variant_label_en", sa.String(length=300), nullable=False),
        sa.Column("variant_label_zh", sa.String(length=300), nullable=False),
        sa.Column("variant_label_ms", sa.String(length=300), nullable=False),
        sa.Column("unit_price_sen", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_subtotal_sen", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "line_index >= 0",
            name=op.f("ck_order_items_line_index_non_negative"),
        ),
        sa.CheckConstraint(
            "unit_price_sen >= 0",
            name=op.f("ck_order_items_unit_price_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "quantity >= 1",
            name=op.f("ck_order_items_quantity_positive"),
        ),
        sa.CheckConstraint(
            "line_subtotal_sen = unit_price_sen * quantity",
            name=op.f("ck_order_items_line_subtotal_formula"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_order_items_variant_id_product_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
        sa.UniqueConstraint(
            "order_id",
            "line_index",
            name=op.f("uq_order_items_order_id_line_index"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "order_item_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("unit_index", sa.Integer(), nullable=False),
        sa.Column("original_price_sen", sa.Integer(), nullable=False),
        sa.Column("coupon_discount_sen", sa.Integer(), nullable=False),
        sa.Column("points_discount", sa.Integer(), nullable=False),
        sa.Column("cash_paid_sen", sa.Integer(), nullable=False),
        sa.Column("points_earned", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "unit_index >= 0",
            name=op.f("ck_order_item_units_unit_index_non_negative"),
        ),
        sa.CheckConstraint(
            "original_price_sen >= 0",
            name=op.f("ck_order_item_units_original_price_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "coupon_discount_sen >= 0",
            name=op.f("ck_order_item_units_coupon_discount_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "points_discount >= 0",
            name=op.f("ck_order_item_units_points_discount_non_negative"),
        ),
        sa.CheckConstraint(
            "cash_paid_sen >= 0",
            name=op.f("ck_order_item_units_cash_paid_sen_non_negative"),
        ),
        sa.CheckConstraint(
            "points_earned >= 0",
            name=op.f("ck_order_item_units_points_earned_non_negative"),
        ),
        sa.CheckConstraint(
            "cash_paid_sen = original_price_sen - coupon_discount_sen - points_discount",
            name=op.f("ck_order_item_units_cash_paid_formula"),
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name=op.f("fk_order_item_units_order_item_id_order_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_item_units")),
        sa.UniqueConstraint(
            "order_item_id",
            "unit_index",
            name=op.f("uq_order_item_units_order_item_id_unit_index"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "order_recipients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "phone LIKE '+%' AND LENGTH(phone) <= 16",
            name=op.f("ck_order_recipients_phone_format"),
        ),
        sa.CheckConstraint(
            "LENGTH(country_code) = 2",
            name=op.f("ck_order_recipients_country_code_length"),
        ),
        sa.CheckConstraint(
            "country_code <> 'MY'"
            " OR (region IS NOT NULL AND LENGTH(region) = 5 AND region LIKE 'MY-%')",
            name=op.f("ck_order_recipients_region_matches_country"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_recipients_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_recipients")),
        sa.UniqueConstraint("order_id", name=op.f("uq_order_recipients_order_id")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_order_recipients_phone"),
        "order_recipients",
        ["phone"],
        unique=False,
    )
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=10), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "method IN ('demo_card', 'demo_bank', 'demo_ewallet')",
            name=op.f("ck_payment_attempts_method_valid"),
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name=op.f("ck_payment_attempts_result_valid"),
        ),
        sa.CheckConstraint(
            "LENGTH(idempotency_key) >= 1",
            name=op.f("ck_payment_attempts_idempotency_key_not_empty"),
        ),
        sa.CheckConstraint(
            "LENGTH(request_fingerprint) = 64",
            name=op.f("ck_payment_attempts_request_fingerprint_length"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_payment_attempts_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_payment_attempts_idempotency_key"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN {ORDER_STATUSES}",
            name=op.f("ck_order_events_from_status_valid"),
        ),
        sa.CheckConstraint(
            f"to_status IN {ORDER_STATUSES}",
            name=op.f("ck_order_events_to_status_valid"),
        ),
        sa.CheckConstraint(
            "(from_status IS NULL AND to_status = 'awaiting_demo_payment')"
            " OR (from_status IS NOT NULL AND to_status <> 'awaiting_demo_payment')",
            name=op.f("ck_order_events_placement_has_no_from_status"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('guest', 'member', 'admin', 'system')",
            name=op.f("ck_order_events_actor_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_events_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
        **TABLE_OPTIONS,
    )


def downgrade() -> None:
    op.drop_table("order_events")
    op.drop_table("payment_attempts")
    op.drop_index(op.f("ix_order_recipients_phone"), table_name="order_recipients")
    op.drop_table("order_recipients")
    op.drop_table("order_item_units")
    op.drop_table("order_items")
    op.drop_table("orders")

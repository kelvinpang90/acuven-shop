"""Order promotion snapshot: promotion, item- and order-level promotion discounts, waived shipping.

依据 docs/DESIGN.md 1.23（提交 b7f9355）「数据模型」的
Order / OrderItem 一行与「节日促销活动」第 7 条，以及 SHOP-TASK-230
写定的列名与约束；与 app/models/order.py 一致。约束名按 app/db/base.py
的命名约定逐个写出（照 0025）。alembic check 不比较检查约束与服务端默认值，
改这里或模型时须人工核对两边一致；测试逐字核对本文件的检查约束常量与模型。
金额是整数仙。

- orders 加 promotion_id（可空，到 promotions RESTRICT，建索引）与
  promotion_item_discount_sen、promotion_order_discount_sen、
  shipping_waived_sen（非空整数，服务端默认 0）；已有订单的金额列取 0、
  活动 ID 为空，不改写已有金额。
- order_item_units 加 promotion_discount_sen（非空整数，服务端默认 0）与
  promotion_rule_id（可空，到 promotion_rules RESTRICT）。
- 新检查约束见 NEW_CHECK_CONSTRAINTS；discounts_within_subtotal、
  total_formula 与 cash_paid_formula 改为含活动优惠（先删后建，约束名不变）。

执行顺序：加列、改约束与删列在 MySQL 上是 DDL，会隐式提交，不随迁移的事务回滚。
- upgrade：先加列（已有的行金额列取 0、两个外键列为空，新约束建立时即可
  通过），再建索引、外键与新约束，最后删掉三条旧约束、按新表达式重建同名约束。
- downgrade：先读出 promotion_id 不为空或三项活动金额不全为 0 的订单数，
  与 promotion_discount_sen 不为 0 或 promotion_rule_id 不为空的逐件行数，
  任一大于 0 即抛 RuntimeError，此时还没有改动，库保持原样（这些订单按旧公式
  不成立）；判断通过后先删掉三条改写后的约束、按旧表达式建回同名约束，
  再删新增的检查约束（MySQL 8.0 不许删除被检查约束引用的列）、外键与索引，
  最后删列。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0025）：模型以后改了，这个迁移不跟着变。
# 以下与 app/models/order.py 逐字相同。
# (约束名, 表, 文本)：upgrade 按这个顺序建，downgrade 倒序删。
NEW_CHECK_CONSTRAINTS = (
    (
        "ck_orders_promotion_item_discount_sen_non_negative",
        "orders",
        "promotion_item_discount_sen >= 0",
    ),
    (
        "ck_orders_promotion_order_discount_sen_non_negative",
        "orders",
        "promotion_order_discount_sen >= 0",
    ),
    ("ck_orders_shipping_waived_sen_non_negative", "orders", "shipping_waived_sen >= 0"),
    (
        "ck_orders_promotion_amounts_match",
        "orders",
        "(promotion_id IS NULL AND promotion_item_discount_sen = 0"
        " AND promotion_order_discount_sen = 0 AND shipping_waived_sen = 0)"
        " OR (promotion_id IS NOT NULL AND promotion_item_discount_sen"
        " + promotion_order_discount_sen + shipping_waived_sen > 0)",
    ),
    (
        "ck_orders_shipping_waived_means_free",
        "orders",
        "shipping_waived_sen = 0 OR shipping_fee_sen = 0",
    ),
    (
        "ck_order_item_units_promotion_discount_sen_non_negative",
        "order_item_units",
        "promotion_discount_sen >= 0",
    ),
)

# (约束名, 表, 旧文本, 新文本)：旧文本与 0006 建的约束逐字相同，
# 新文本与模型逐字相同。
REWRITTEN_CHECK_CONSTRAINTS = (
    (
        "ck_orders_discounts_within_subtotal",
        "orders",
        "coupon_discount_sen + points_redeemed <= subtotal_sen",
        "promotion_item_discount_sen + promotion_order_discount_sen"
        " + coupon_discount_sen + points_redeemed <= subtotal_sen",
    ),
    (
        "ck_orders_total_formula",
        "orders",
        "total_sen = subtotal_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen",
        "total_sen = subtotal_sen - promotion_item_discount_sen"
        " - promotion_order_discount_sen - coupon_discount_sen - points_redeemed"
        " + shipping_fee_sen",
    ),
    (
        "ck_order_item_units_cash_paid_formula",
        "order_item_units",
        "cash_paid_sen = original_price_sen - coupon_discount_sen - points_discount",
        "cash_paid_sen = original_price_sen - promotion_discount_sen"
        " - coupon_discount_sen - points_discount",
    ),
)

ORDER_AMOUNT_COLUMNS = (
    "promotion_item_discount_sen",
    "promotion_order_discount_sen",
    "shipping_waived_sen",
)
PROMOTION_INDEX = "ix_orders_promotion_id"
ORDER_PROMOTION_FK = "fk_orders_promotion_id_promotions"
UNIT_RULE_FK = "fk_order_item_units_promotion_rule_id_promotion_rules"

orders = sa.table(
    "orders",
    sa.column("promotion_id", sa.Integer),
    *[sa.column(column, sa.Integer) for column in ORDER_AMOUNT_COLUMNS],
)
order_item_units = sa.table(
    "order_item_units",
    sa.column("promotion_discount_sen", sa.Integer),
    sa.column("promotion_rule_id", sa.Integer),
)


def _count(table: sa.TableClause, condition: sa.ColumnElement[bool]) -> int:
    stmt = sa.select(sa.func.count()).select_from(table).where(condition)
    return op.get_bind().execute(stmt).scalar_one()


def upgrade() -> None:
    op.add_column("orders", sa.Column("promotion_id", sa.Integer(), nullable=True))
    for column in ORDER_AMOUNT_COLUMNS:
        op.add_column(
            "orders",
            sa.Column(column, sa.Integer(), server_default="0", nullable=False),
        )
    op.add_column(
        "order_item_units",
        sa.Column("promotion_discount_sen", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("order_item_units", sa.Column("promotion_rule_id", sa.Integer(), nullable=True))

    op.create_index(op.f(PROMOTION_INDEX), "orders", ["promotion_id"], unique=False)
    op.create_foreign_key(
        op.f(ORDER_PROMOTION_FK),
        "orders",
        "promotions",
        ["promotion_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f(UNIT_RULE_FK),
        "order_item_units",
        "promotion_rules",
        ["promotion_rule_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name, table, condition in NEW_CHECK_CONSTRAINTS:
        op.create_check_constraint(op.f(name), table, condition)

    for name, table, _old, new in REWRITTEN_CHECK_CONSTRAINTS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, new)


def downgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    promotion_orders = _count(
        orders,
        sa.or_(
            orders.c.promotion_id.is_not(None),
            *[orders.c[column] != 0 for column in ORDER_AMOUNT_COLUMNS],
        ),
    )
    promotion_units = _count(
        order_item_units,
        sa.or_(
            order_item_units.c.promotion_discount_sen != 0,
            order_item_units.c.promotion_rule_id.is_not(None),
        ),
    )
    if promotion_orders or promotion_units:
        raise RuntimeError(
            "cannot downgrade 0028: "
            f"{promotion_orders} orders with a promotion, "
            f"{promotion_units} order item units with a promotion discount or rule"
        )

    for name, table, old, _new in REWRITTEN_CHECK_CONSTRAINTS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, old)

    for name, table, _condition in reversed(NEW_CHECK_CONSTRAINTS):
        op.drop_constraint(op.f(name), table, type_="check")
    op.drop_constraint(op.f(UNIT_RULE_FK), "order_item_units", type_="foreignkey")
    op.drop_constraint(op.f(ORDER_PROMOTION_FK), "orders", type_="foreignkey")
    op.drop_index(op.f(PROMOTION_INDEX), table_name="orders")

    op.drop_column("order_item_units", "promotion_rule_id")
    op.drop_column("order_item_units", "promotion_discount_sen")
    for column in reversed(ORDER_AMOUNT_COLUMNS):
        op.drop_column("orders", column)
    op.drop_column("orders", "promotion_id")

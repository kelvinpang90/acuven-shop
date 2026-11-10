"""Festival promotions: promotions, rules, rule scope, spend tiers, decorations, publish lock.

依据 docs/DESIGN.md 1.23（提交 b7f9355）「数据模型」的 Promotion /
PromotionRule / PromotionDecoration 一行与「节日促销活动」第 1–3、12 条，
以及 SHOP-TASK-229 写定的表名、列名、类型与约束；与 app/models/promotion.py
一致。约束名按 app/db/base.py 的命名约定逐个写出（照 0025）。
alembic check 不比较检查约束与表选项，改这里或模型时须人工核对两边一致；
测试逐字核对本文件的检查约束常量与模型。时间是不带时区的 UTC；
金额是整数仙。

- promotions：活动；按（status、starts_at）建一个索引。
- promotion_rules：规则，到活动 CASCADE；（promotion_id、position）唯一。
- promotion_rule_products、promotion_rule_categories：商品级规则的
  适用范围，到规则 CASCADE，到商品与分类 RESTRICT。
- promotion_spend_tiers：满额减或满额折的各档，到规则 CASCADE。
- promotion_decorations：装饰、海报与三语文案，到活动 CASCADE，
  每个活动至多一行。
- promotion_publish_locks：发布与改时间时先锁住的单行锁表，
  upgrade 写入唯一的一行。

执行顺序：
- upgrade：按外键依赖建七张表（被引用的在前），再写入发布锁那一行。
- downgrade：先读出 promotions 的行数，大于 0 即抛 RuntimeError，
  此时还没有改动，库保持原样（删表会丢掉运营者配置的活动）；
  判断通过后按外键依赖的逆序删表，索引随表一起删。
迁移只在 MySQL 上执行，不要求能在 SQLite 上执行。

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移不 import app，常量在这里自带（照 0025）：模型以后改了，这个迁移不跟着变。
# 与 app.db.base.MYSQL_TABLE_OPTIONS 相同。
TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

# 建表顺序（被引用的在前）；downgrade 倒序删。
TABLES = (
    "promotions",
    "promotion_rules",
    "promotion_rule_products",
    "promotion_rule_categories",
    "promotion_spend_tiers",
    "promotion_decorations",
    "promotion_publish_locks",
)

# 以下与 app/models/promotion.py 逐字相同。表名 → ((约束名, 文本), ...)。
CHECK_CONSTRAINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "promotions": (
        ("ck_promotions_name_not_empty", "LENGTH(name) >= 1"),
        ("ck_promotions_status_valid", "status IN ('draft', 'published')"),
        (
            "ck_promotions_published_has_times",
            "status <> 'published' OR (starts_at IS NOT NULL AND ends_at IS NOT NULL)",
        ),
        (
            "ck_promotions_ends_after_starts",
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
        ),
        (
            "ck_promotions_ended_early_within",
            "ended_early_at IS NULL OR (status = 'published'"
            " AND ended_early_at >= starts_at AND ended_early_at < ends_at)",
        ),
        ("ck_promotions_version_positive", "version >= 1"),
    ),
    "promotion_rules": (
        ("ck_promotion_rules_position_positive", "position >= 1"),
        (
            "ck_promotion_rules_kind_valid",
            "kind IN ('item_discount', 'buy_x_get_y', 'spend_discount', 'free_shipping')",
        ),
        (
            "ck_promotion_rules_item_discount_params",
            "kind <> 'item_discount' OR (discount_mode IS NOT NULL"
            " AND ((discount_mode = 'percent' AND percent IS NOT NULL"
            " AND percent >= 1 AND percent <= 90"
            " AND amount_sen IS NULL)"
            " OR (discount_mode = 'amount' AND amount_sen IS NOT NULL"
            " AND amount_sen >= 1 AND amount_sen <= 50000000 AND percent IS NULL))"
            " AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent IS NULL"
            " AND threshold_sen IS NULL)",
        ),
        (
            "ck_promotion_rules_buy_x_get_y_params",
            "kind <> 'buy_x_get_y' OR (buy_quantity IS NOT NULL"
            " AND buy_quantity >= 1 AND buy_quantity <= 10"
            " AND get_quantity IS NOT NULL"
            " AND get_quantity >= 1 AND get_quantity <= 10"
            " AND get_percent IS NOT NULL"
            " AND get_percent >= 1 AND get_percent <= 100"
            " AND discount_mode IS NULL AND percent IS NULL AND amount_sen IS NULL"
            " AND threshold_sen IS NULL)",
        ),
        (
            "ck_promotion_rules_spend_discount_params",
            "kind <> 'spend_discount' OR (discount_mode IS NOT NULL"
            " AND discount_mode IN ('percent', 'amount')"
            " AND percent IS NULL AND amount_sen IS NULL AND buy_quantity IS NULL"
            " AND get_quantity IS NULL AND get_percent IS NULL AND threshold_sen IS NULL)",
        ),
        (
            "ck_promotion_rules_free_shipping_params",
            "kind <> 'free_shipping' OR (threshold_sen IS NOT NULL"
            " AND threshold_sen >= 0 AND threshold_sen <= 99999999"
            " AND discount_mode IS NULL AND percent IS NULL AND amount_sen IS NULL"
            " AND buy_quantity IS NULL AND get_quantity IS NULL AND get_percent IS NULL)",
        ),
    ),
    "promotion_rule_products": (),
    "promotion_rule_categories": (),
    "promotion_spend_tiers": (
        ("ck_promotion_spend_tiers_position_range", "position >= 1 AND position <= 5"),
        (
            "ck_promotion_spend_tiers_threshold_range",
            "threshold_sen >= 1 AND threshold_sen <= 99999999",
        ),
        (
            "ck_promotion_spend_tiers_amount_or_percent",
            "(amount_sen IS NOT NULL AND percent IS NULL)"
            " OR (amount_sen IS NULL AND percent IS NOT NULL)",
        ),
        (
            "ck_promotion_spend_tiers_amount_below_threshold",
            "amount_sen IS NULL OR (amount_sen >= 1 AND amount_sen < threshold_sen)",
        ),
        (
            "ck_promotion_spend_tiers_percent_range",
            "percent IS NULL OR (percent >= 1 AND percent <= 90)",
        ),
    ),
    "promotion_decorations": (
        (
            "ck_promotion_decorations_pack_known",
            "pack IN ('cny', 'raya', 'deepavali', 'christmas', 'merdeka', 'mega_sale', 'generic')",
        ),
        (
            "ck_promotion_decorations_palette_known",
            "palette IN ('theme', 'cny', 'raya', 'deepavali', 'christmas', 'merdeka',"
            " 'mega_sale', 'generic')",
        ),
        (
            "ck_promotion_decorations_ambient_known",
            "ambient IN ('garland', 'corners', 'fall', 'none')",
        ),
        ("ck_promotion_decorations_home_poster_known", "home_poster IN ('split', 'centered')"),
        ("ck_promotion_decorations_list_strip_known", "list_strip IN ('band', 'ticket')"),
        ("ck_promotion_decorations_card_badge_known", "card_badge IN ('tag', 'seal')"),
        ("ck_promotion_decorations_detail_ribbon_known", "detail_ribbon IN ('ribbon', 'panel')"),
        ("ck_promotion_decorations_cart_progress_known", "cart_progress IN ('bar', 'steps')"),
        (
            "ck_promotion_decorations_poster_mode_valid",
            "poster_mode IN ('template', 'image')",
        ),
        (
            "ck_promotion_decorations_poster_desktop_all_or_none",
            "(poster_desktop_file_name IS NULL AND poster_desktop_source_format IS NULL"
            " AND poster_desktop_width IS NULL"
            " AND poster_desktop_height IS NULL AND poster_desktop_byte_size IS NULL)"
            " OR (poster_desktop_file_name IS NOT NULL"
            " AND poster_desktop_source_format IS NOT NULL"
            " AND poster_desktop_width IS NOT NULL AND poster_desktop_height IS NOT NULL"
            " AND poster_desktop_byte_size IS NOT NULL)",
        ),
        (
            "ck_promotion_decorations_poster_desktop_source_format_valid",
            "poster_desktop_source_format IS NULL"
            " OR poster_desktop_source_format IN ('jpeg', 'png', 'webp')",
        ),
        (
            "ck_promotion_decorations_poster_desktop_display_size_range",
            "poster_desktop_width IS NULL OR (poster_desktop_width >= 1"
            " AND poster_desktop_width <= 1600"
            " AND poster_desktop_height >= 1 AND poster_desktop_height <= 1600"
            " AND poster_desktop_byte_size >= 1)",
        ),
        (
            "ck_promotion_decorations_poster_phone_all_or_none",
            "(poster_phone_file_name IS NULL AND poster_phone_source_format IS NULL"
            " AND poster_phone_width IS NULL"
            " AND poster_phone_height IS NULL AND poster_phone_byte_size IS NULL)"
            " OR (poster_phone_file_name IS NOT NULL"
            " AND poster_phone_source_format IS NOT NULL"
            " AND poster_phone_width IS NOT NULL AND poster_phone_height IS NOT NULL"
            " AND poster_phone_byte_size IS NOT NULL)",
        ),
        (
            "ck_promotion_decorations_poster_phone_source_format_valid",
            "poster_phone_source_format IS NULL"
            " OR poster_phone_source_format IN ('jpeg', 'png', 'webp')",
        ),
        (
            "ck_promotion_decorations_poster_phone_display_size_range",
            "poster_phone_width IS NULL OR (poster_phone_width >= 1"
            " AND poster_phone_width <= 1600"
            " AND poster_phone_height >= 1 AND poster_phone_height <= 1600"
            " AND poster_phone_byte_size >= 1)",
        ),
    ),
    "promotion_publish_locks": (
        ("ck_promotion_publish_locks_singleton_slot_one", "singleton_slot = 1"),
    ),
}

# 发布锁单例行的单例槽。
SINGLETON_SLOT = 1

# 装饰表的 ID 列（素材包、配色与六个装饰位）与三语文案列（列名前缀 → 长度）。
DECORATION_ID_COLUMNS = (
    "pack",
    "palette",
    "ambient",
    "home_poster",
    "list_strip",
    "card_badge",
    "detail_ribbon",
    "cart_progress",
)
DECORATION_TEXT_COLUMNS = (
    ("poster_alt", 150),
    ("title", 40),
    ("subtitle", 80),
    ("badge", 12),
    ("strip", 60),
)
LANGUAGES = ("en", "zh", "ms")

promotions_table = sa.table("promotions", sa.column("id", sa.Integer))


def _checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(condition, name=op.f(name))
        for name, condition in CHECK_CONSTRAINTS[table]
    ]


def _poster_columns(prefix: str) -> list[sa.Column]:
    return [
        sa.Column(f"{prefix}file_name", sa.String(length=32), nullable=True),
        sa.Column(f"{prefix}source_format", sa.String(length=4), nullable=True),
        sa.Column(f"{prefix}width", sa.Integer(), nullable=True),
        sa.Column(f"{prefix}height", sa.Integer(), nullable=True),
        sa.Column(f"{prefix}byte_size", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("ended_early_at", sa.DateTime(), nullable=True),
        sa.Column("coupon_combinable", sa.Boolean(), nullable=False),
        sa.Column("points_combinable", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_checks("promotions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotions")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        op.f("ix_promotions_status_starts_at"),
        "promotions",
        ["status", "starts_at"],
        unique=False,
    )
    op.create_table(
        "promotion_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("discount_mode", sa.String(length=8), nullable=True),
        sa.Column("percent", sa.Integer(), nullable=True),
        sa.Column("amount_sen", sa.Integer(), nullable=True),
        sa.Column("buy_quantity", sa.Integer(), nullable=True),
        sa.Column("get_quantity", sa.Integer(), nullable=True),
        sa.Column("get_percent", sa.Integer(), nullable=True),
        sa.Column("threshold_sen", sa.Integer(), nullable=True),
        *_checks("promotion_rules"),
        sa.ForeignKeyConstraint(
            ["promotion_id"],
            ["promotions.id"],
            name=op.f("fk_promotion_rules_promotion_id_promotions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_rules")),
        sa.UniqueConstraint(
            "promotion_id",
            "position",
            name=op.f("uq_promotion_rules_promotion_id_position"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "promotion_rule_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["promotion_rules.id"],
            name=op.f("fk_promotion_rule_products_rule_id_promotion_rules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_promotion_rule_products_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_rule_products")),
        sa.UniqueConstraint(
            "rule_id",
            "product_id",
            name=op.f("uq_promotion_rule_products_rule_id_product_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "promotion_rule_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["promotion_rules.id"],
            name=op.f("fk_promotion_rule_categories_rule_id_promotion_rules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_promotion_rule_categories_category_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_rule_categories")),
        sa.UniqueConstraint(
            "rule_id",
            "category_id",
            name=op.f("uq_promotion_rule_categories_rule_id_category_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "promotion_spend_tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("threshold_sen", sa.Integer(), nullable=False),
        sa.Column("amount_sen", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Integer(), nullable=True),
        *_checks("promotion_spend_tiers"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["promotion_rules.id"],
            name=op.f("fk_promotion_spend_tiers_rule_id_promotion_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_spend_tiers")),
        sa.UniqueConstraint(
            "rule_id",
            "position",
            name=op.f("uq_promotion_spend_tiers_rule_id_position"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "promotion_decorations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        *[
            sa.Column(column, sa.String(length=16), nullable=False)
            for column in DECORATION_ID_COLUMNS
        ],
        sa.Column("poster_mode", sa.String(length=8), nullable=False),
        *_poster_columns("poster_desktop_"),
        *_poster_columns("poster_phone_"),
        *[
            sa.Column(f"{prefix}_{language}", sa.String(length=length), nullable=True)
            for prefix, length in DECORATION_TEXT_COLUMNS
            for language in LANGUAGES
        ],
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_checks("promotion_decorations"),
        sa.ForeignKeyConstraint(
            ["promotion_id"],
            ["promotions.id"],
            name=op.f("fk_promotion_decorations_promotion_id_promotions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_decorations")),
        sa.UniqueConstraint("promotion_id", name=op.f("uq_promotion_decorations_promotion_id")),
        **TABLE_OPTIONS,
    )
    locks = op.create_table(
        "promotion_publish_locks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("singleton_slot", sa.Integer(), nullable=False),
        *_checks("promotion_publish_locks"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_publish_locks")),
        sa.UniqueConstraint(
            "singleton_slot",
            name=op.f("uq_promotion_publish_locks_singleton_slot"),
        ),
        **TABLE_OPTIONS,
    )
    op.bulk_insert(locks, [{"singleton_slot": SINGLETON_SLOT}])


def downgrade() -> None:
    # 判断在改表之前：不通过时库保持原样。
    stmt = sa.select(sa.func.count()).select_from(promotions_table)
    count = op.get_bind().execute(stmt).scalar_one()
    if count:
        raise RuntimeError(f"cannot downgrade 0027: {count} promotions exist")

    # 索引随表一起删，不先单独删（照 0018）。
    for table in reversed(TABLES):
        op.drop_table(table)

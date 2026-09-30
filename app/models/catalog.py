"""商品目录：分类、商品、图片引用、规格名与规格值、SKU 规格。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行。
金额一律是 MYR 整数仙，不用浮点或 Decimal。
三语文案（en / zh / ms）都可为空；是否发布由查询按「缺少当前语言回退英文，
再缺失则不发布」判断，不在表里另存。库存预留与每日重置是之后的任务。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("slug"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str | None] = mapped_column(String(200))
    name_zh: Mapped[str | None] = mapped_column(String(200))
    name_ms: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("slug"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 分类下还有商品时不许删分类，不连带删商品。
    category_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("categories.id", ondelete="RESTRICT"),
    )
    slug: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str | None] = mapped_column(String(200))
    name_zh: Mapped[str | None] = mapped_column(String(200))
    name_ms: Mapped[str | None] = mapped_column(String(200))
    description_en: Mapped[str | None] = mapped_column(Text)
    description_zh: Mapped[str | None] = mapped_column(Text)
    description_ms: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean)
    # UTC，供按最新排序。
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProductImage(Base):
    """只存存储引用与排列序号，不存图片内容。"""

    __tablename__ = "product_images"
    __table_args__ = (UniqueConstraint("product_id", "sort_order"), MYSQL_TABLE_OPTIONS)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
    )
    storage_ref: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer)


class ProductOption(Base):
    """规格名（如颜色、尺寸），属于一件商品。"""

    __tablename__ = "product_options"
    __table_args__ = (
        UniqueConstraint("product_id", "code"),
        # 复合外键的目标：规格值要求所属规格名与自己属于同一商品。
        UniqueConstraint("id", "product_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
    )
    code: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer)
    name_en: Mapped[str | None] = mapped_column(String(100))
    name_zh: Mapped[str | None] = mapped_column(String(100))
    name_ms: Mapped[str | None] = mapped_column(String(100))


class ProductOptionValue(Base):
    """规格值（如红色、M），属于一个规格名，冗余存商品以便复合外键。"""

    __tablename__ = "product_option_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["option_id", "product_id"],
            ["product_options.id", "product_options.product_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("option_id", "code"),
        # 复合外键的目标：SKU 关联的值须属于所关联的规格名与 SKU 的商品。
        UniqueConstraint("id", "option_id", "product_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer)
    option_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer)
    name_en: Mapped[str | None] = mapped_column(String(100))
    name_zh: Mapped[str | None] = mapped_column(String(100))
    name_ms: Mapped[str | None] = mapped_column(String(100))


class ProductVariant(Base):
    """SKU 规格：单价是 MYR 整数仙；库存是件数。"""

    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("sku"),
        # 复合外键的目标：SKU 的规格值关联行须与 SKU 属于同一商品。
        UniqueConstraint("id", "product_id"),
        CheckConstraint("price_sen >= 0", name="price_sen_non_negative"),
        CheckConstraint("daily_initial_stock >= 0", name="daily_initial_stock_non_negative"),
        CheckConstraint("available_stock >= 0", name="available_stock_non_negative"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
    )
    sku: Mapped[str] = mapped_column(String(64))
    price_sen: Mapped[int] = mapped_column(Integer)
    # 管理员设定的每日初始库存；当日可用库存由之后的预留与每日重置任务维护。
    daily_initial_stock: Mapped[int] = mapped_column(Integer)
    available_stock: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)


class VariantOptionValue(Base):
    """SKU 与规格值的关联：同一 SKU 在同一规格名下至多一个值。

    两个复合外键保证 SKU、规格名、规格值属于同一商品，且规格值属于 option_id。
    每个规格名都有值由之后的后台维护任务校验。
    """

    __tablename__ = "variant_option_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["variant_id", "product_id"],
            ["product_variants.id", "product_variants.product_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["option_value_id", "option_id", "product_id"],
            [
                "product_option_values.id",
                "product_option_values.option_id",
                "product_option_values.product_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("variant_id", "option_id"),
        MYSQL_TABLE_OPTIONS,
    )

    variant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    option_value_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer)
    option_id: Mapped[int] = mapped_column(Integer)

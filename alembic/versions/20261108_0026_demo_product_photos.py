"""Demo product photos: replace each seed product's category placeholder with five photos.

迁移 0003 给 30 件示例商品各写了一张所属分类的占位图（/demo-images/<分类>.svg）。这里把仍是那张
占位图的商品换成五张照片，引用是站点根路径 /demo-images/products/<slug>-<1..5>.webp，随前端
frontend/public/ 发布；照片来自 Pixabay（Pixabay Content License），来源逐张记在
frontend/public/demo-images/products/CREDITS.md。与 0003 的占位图一样是示例数据，元数据五列
（file_name、source_format、width、height、byte_size）全为空，不经图片存储。

只改动「图片恰好是 0003 写入的那一张占位图」的商品：后台已经改过图片的商品（上传、删除或调过顺序）
原样保留，不覆盖运营者的改动。商品按 slug 查找，找不到的跳过。

- upgrade：符合条件的商品删掉那张占位图，按顺序写入五张照片（sort_order 0..4，第一张为封面）。
- downgrade：图片恰好是这五张照片、顺序不变的商品，删掉五张并写回 0003 的占位图；其余不动。

数据全是迁移内的常量，不 import app：模型以后改了，这个迁移写入的内容不跟着变。

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHOTOS_PER_PRODUCT = 5

# (商品 slug, 0003 写入时所属分类的 slug)，与 0003 的 PRODUCTS 逐件对应。
SEED_PRODUCTS = (
    ("crew-neck-tee", "apparel"),
    ("pullover-hoodie", "apparel"),
    ("cotton-cap", "apparel"),
    ("knit-beanie", "apparel"),
    ("ankle-socks-3-pack", "apparel"),
    ("canvas-tote-bag", "bags"),
    ("everyday-backpack", "bags"),
    ("zip-pouch", "bags"),
    ("drawstring-bag", "bags"),
    ("weekender-duffel", "bags"),
    ("linen-cushion-cover", "home"),
    ("cotton-bath-towel", "home"),
    ("soy-wax-candle", "home"),
    ("woven-storage-basket", "home"),
    ("round-wall-clock", "home"),
    ("ceramic-mug", "kitchen"),
    ("stainless-water-bottle", "kitchen"),
    ("bamboo-cutting-board", "kitchen"),
    ("cotton-apron", "kitchen"),
    ("glass-food-container-set", "kitchen"),
    ("dotted-notebook", "stationery"),
    ("gel-pen-set", "stationery"),
    ("desk-organiser", "stationery"),
    ("sticky-notes-pack", "stationery"),
    ("zip-pencil-case", "stationery"),
    ("wireless-mouse", "gadgets"),
    ("braided-charging-cable", "gadgets"),
    ("folding-phone-stand", "gadgets"),
    ("compact-power-bank", "gadgets"),
    ("portable-speaker", "gadgets"),
)

products = sa.table(
    "products",
    sa.column("id", sa.Integer),
    sa.column("slug", sa.String),
)
product_images = sa.table(
    "product_images",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("storage_ref", sa.String),
    sa.column("sort_order", sa.Integer),
)


def placeholder_ref(category: str) -> str:
    return f"/demo-images/{category}.svg"


def photo_refs(slug: str) -> list[str]:
    return [f"/demo-images/products/{slug}-{n}.webp" for n in range(1, PHOTOS_PER_PRODUCT + 1)]


def _swap(
    from_refs: Callable[[str, str], list[str]], to_refs: Callable[[str, str], list[str]]
) -> None:
    conn = op.get_bind()
    slugs = [slug for slug, _ in SEED_PRODUCTS]
    ids = dict(
        conn.execute(
            sa.select(products.c.slug, products.c.id).where(products.c.slug.in_(slugs))
        ).all()
    )
    for slug, category in SEED_PRODUCTS:
        product_id = ids.get(slug)
        if product_id is None:
            continue
        current = [
            ref
            for (ref,) in conn.execute(
                sa.select(product_images.c.storage_ref)
                .where(product_images.c.product_id == product_id)
                .order_by(product_images.c.sort_order)
            )
        ]
        if current != from_refs(slug, category):
            continue
        conn.execute(product_images.delete().where(product_images.c.product_id == product_id))
        op.bulk_insert(
            product_images,
            [
                {"product_id": product_id, "storage_ref": ref, "sort_order": index}
                for index, ref in enumerate(to_refs(slug, category))
            ],
        )


def _placeholder(_slug: str, category: str) -> list[str]:
    return [placeholder_ref(category)]


def _photos(slug: str, _category: str) -> list[str]:
    return photo_refs(slug)


def upgrade() -> None:
    _swap(_placeholder, _photos)


def downgrade() -> None:
    _swap(_photos, _placeholder)

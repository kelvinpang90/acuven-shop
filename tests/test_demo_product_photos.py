"""迁移 0026：示例商品的分类占位图换成每件五张照片，以及随仓库发布的照片文件。

用 SQLite 内存库按模型建表，先执行 0003 写入示例目录，再执行 0026 的 upgrade / downgrade，
查询实际写入的行（照 tests/test_demo_catalog_seed.py）。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from PIL import Image
from sqlalchemy import Connection, create_engine, event, select

from app.db.base import Base
from app.models import Product, ProductImage

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "alembic" / "versions"
PUBLIC_DIR = ROOT / "frontend" / "public"
PHOTO_DIR = PUBLIC_DIR / "demo-images" / "products"
# 展示尺寸：桌面详情主图约 400px 宽，1.5 倍像素密度够用；单张上限控制仓库与首屏体积。
PHOTO_SIZE = (640, 640)
MAX_PHOTO_BYTES = 100 * 1024


def _load(file_name: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, VERSIONS / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEED = _load("20260930_0003_demo_catalog.py", "demo_catalog_migration_0003")
PHOTOS = _load("20261108_0026_demo_product_photos.py", "demo_product_photos_migration_0026")


@pytest.fixture
def conn() -> Iterator[Connection]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    with engine.connect() as connection:
        Base.metadata.create_all(connection)
        _run(connection, SEED, "upgrade")
        yield connection
    engine.dispose()


def _run(connection: Connection, module: ModuleType, step: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, step)()


def _refs(connection: Connection) -> dict[str, list[str]]:
    rows = connection.execute(
        select(Product.slug, ProductImage.storage_ref)
        .join(ProductImage, ProductImage.product_id == Product.id)
        .order_by(Product.slug, ProductImage.sort_order)
    )
    refs: dict[str, list[str]] = {}
    for slug, ref in rows:
        refs.setdefault(slug, []).append(ref)
    return refs


def _orders(connection: Connection, product_id: int) -> list[int]:
    return list(
        connection.execute(
            select(ProductImage.sort_order)
            .where(ProductImage.product_id == product_id)
            .order_by(ProductImage.sort_order)
        ).scalars()
    )


def _seed_placeholders() -> dict[str, list[str]]:
    return {p["slug"]: [f"/demo-images/{p['category']}.svg"] for p in SEED.PRODUCTS}


def test_seed_product_list_matches_0003() -> None:
    """0026 自带的 (slug, 分类) 必须与 0003 写入的逐件相同，否则会漏换或认不出占位图。"""
    assert list(PHOTOS.SEED_PRODUCTS) == [(p["slug"], p["category"]) for p in SEED.PRODUCTS]


def test_upgrade_gives_every_seed_product_five_photos_in_order(conn: Connection) -> None:
    """每件示例商品换成五张照片，第一张为封面；示例数据不经图片存储，元数据五列全为空。"""
    _run(conn, PHOTOS, "upgrade")

    assert _refs(conn) == {slug: PHOTOS.photo_refs(slug) for slug, _ in PHOTOS.SEED_PRODUCTS}
    for image in conn.execute(select(ProductImage)):
        assert image.sort_order in range(5)
        assert (
            image.file_name,
            image.source_format,
            image.width,
            image.height,
            image.byte_size,
        ) == (None,) * 5


def test_products_edited_in_admin_are_left_alone(conn: Connection) -> None:
    """后台改过图片的商品（多传一张、或换掉占位图）不被覆盖：迁移不能抹掉运营者的改动。"""
    mug = conn.execute(select(Product.id).where(Product.slug == "ceramic-mug")).scalar_one()
    conn.execute(
        ProductImage.__table__.insert().values(
            product_id=mug, storage_ref="/uploaded.webp", sort_order=1
        )
    )
    tee = conn.execute(select(Product.id).where(Product.slug == "crew-neck-tee")).scalar_one()
    conn.execute(
        ProductImage.__table__.update()
        .where(ProductImage.product_id == tee)
        .values(storage_ref="/replaced.webp")
    )
    # 只调过顺序号、引用没变的也算改动过
    cap = conn.execute(select(Product.id).where(Product.slug == "cotton-cap")).scalar_one()
    conn.execute(
        ProductImage.__table__.update().where(ProductImage.product_id == cap).values(sort_order=1)
    )

    _run(conn, PHOTOS, "upgrade")

    refs = _refs(conn)
    assert refs["ceramic-mug"] == ["/demo-images/kitchen.svg", "/uploaded.webp"]
    assert refs["crew-neck-tee"] == ["/replaced.webp"]
    assert refs["cotton-cap"] == ["/demo-images/apparel.svg"]
    assert _orders(conn, cap) == [1]
    assert refs["knit-beanie"] == PHOTOS.photo_refs("knit-beanie")


def test_downgrade_restores_the_placeholders(conn: Connection) -> None:
    """downgrade 只把仍是这五张照片的商品换回 0003 的占位图，之后被改过的商品不动。"""
    _run(conn, PHOTOS, "upgrade")
    mouse = conn.execute(select(Product.id).where(Product.slug == "wireless-mouse")).scalar_one()
    conn.execute(
        ProductImage.__table__.delete().where(
            ProductImage.product_id == mouse, ProductImage.sort_order == 4
        )
    )
    # 只调过顺序号（五张整体后移）的也不还原
    speaker = conn.execute(
        select(Product.id).where(Product.slug == "portable-speaker")
    ).scalar_one()
    conn.execute(
        ProductImage.__table__.update()
        .where(ProductImage.product_id == speaker)
        .values(sort_order=ProductImage.sort_order + 10)
    )

    _run(conn, PHOTOS, "downgrade")

    expected = _seed_placeholders()
    expected["wireless-mouse"] = PHOTOS.photo_refs("wireless-mouse")[:4]
    expected["portable-speaker"] = PHOTOS.photo_refs("portable-speaker")
    assert _refs(conn) == expected
    assert _orders(conn, speaker) == [10, 11, 12, 13, 14]


@pytest.mark.parametrize("slug", [slug for slug, _ in PHOTOS.SEED_PRODUCTS])
def test_photo_files_ship_with_the_frontend(slug: str) -> None:
    """迁移引用的每张照片都随前端发布，是 640×640 的 WebP、不超过 100 KB，且五张互不相同。"""
    contents = set()
    for ref in PHOTOS.photo_refs(slug):
        path = PUBLIC_DIR / ref.lstrip("/")
        raw = path.read_bytes()
        assert len(raw) <= MAX_PHOTO_BYTES, ref
        with Image.open(path) as image:
            assert image.format == "WEBP", ref
            assert image.size == PHOTO_SIZE, ref
        contents.add(raw)
    assert len(contents) == 5


def test_every_photo_has_a_credit() -> None:
    """照片来自第三方图库：每张都在 CREDITS.md 里记有来源，目录里没有未被引用的文件。"""
    credits = (PHOTO_DIR / "CREDITS.md").read_text(encoding="utf-8")
    referenced = {
        Path(ref).name for slug, _ in PHOTOS.SEED_PRODUCTS for ref in PHOTOS.photo_refs(slug)
    }
    for name in referenced:
        assert f"`{name}`" in credits, name
    assert {p.name for p in PHOTO_DIR.glob("*.webp")} == referenced

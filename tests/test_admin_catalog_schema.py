"""商品与分类的版本号、商品图片的元数据、每日恢复库存开关与迁移 0024 的常量（SHOP-TASK-177）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「数据模型」的 Product / Variant 一行（下称「商品一行」）
与 SiteSetting 一行，「计价、优惠、积分与库存」第 6、11、13、14 条，以及 docs/HANDOFF.md 0.74
记录的落实写法（下称「HANDOFF 0.74」）。每条测试的文档字符串引用它守住的那一句。

用 SQLite 内存库（打开外键检查）按模型建表。检查约束的反例每次只让目标约束不成立，
断言报出的是该约束的名称。迁移 0024 只在 MySQL 上执行，这里不在 SQLite 上执行它，
只经 importlib 载入、核对 revision 与约束常量；往返由 CI 的 backend 检查在 MySQL 8 上做。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, Table, create_engine, event, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Category, Product, ProductImage, SiteSetting

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20261030_0024_admin_catalog_schema.py"

NOW = datetime(2026, 10, 30, 2, 0)
FILE_NAME = "0123456789abcdef0123456789abcdef"
# 后台上传的图片：五列全有值。
METADATA = {
    "file_name": FILE_NAME,
    "source_format": "jpeg",
    "width": 1600,
    "height": 1200,
    "byte_size": 245_000,
}


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("admin_catalog_schema_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        yield session
    engine.dispose()


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _category(db: Session, **fields: object) -> Category:
    values = {"slug": "things", "name_en": "Things", "is_active": True}
    return _add(db, Category(**(values | fields)))


def _product(db: Session, **fields: object) -> Product:
    category = _category(db)
    values = {"category_id": category.id, "slug": "tee", "name_en": "Tee", "is_active": True}
    return _add(db, Product(**(values | fields)))


def _image(db: Session, product: Product, sort_order: int = 0, **fields: object) -> ProductImage:
    values = {
        "product_id": product.id,
        "storage_ref": f"/api/product-images/{fields.get('file_name')}.webp",
        "sort_order": sort_order,
    }
    return _add(db, ProductImage(**(values | fields)))


def _model_checks(table: Table) -> dict[str, str]:
    """表上检查约束的完整名称（按 app/db/base.py 的命名约定）→ 文本。"""
    prefix = f"ck_{table.name}_"
    checks = {}
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            name = str(constraint.name)
            checks[name if name.startswith(prefix) else prefix + name] = str(constraint.sqltext)
    return checks


# ---- 商品与分类的版本号 ----


def test_new_category_and_product_have_version_1(db: Session) -> None:
    """商品一行「商品与分类各有版本号（1.18，正整数，新建为 1…）」：写入时不给版本号，
    库里由服务端默认值补成 1（直接从库里读回，不看 Python 对象）。
    """
    product = _product(db)

    assert db.scalar(select(Category.version).where(Category.id == product.category_id)) == 1
    assert db.scalar(select(Product.version).where(Product.id == product.id)) == 1


def test_category_version_0_is_rejected(db: Session) -> None:
    """商品一行「商品与分类各有版本号（1.18，正整数…）」与第 14 条「每个分类带版本号
    （新建为 1）」：分类的版本号 0 被检查约束拒绝。
    """
    with pytest.raises(IntegrityError, match="CHECK constraint failed: ck_categories_version"):
        _category(db, version=0)


def test_product_version_0_is_rejected(db: Session) -> None:
    """商品一行「商品与分类各有版本号（1.18，正整数…）」与第 11 条「新建商品的版本号为 1」：
    商品的版本号 0 被检查约束拒绝。
    """
    with pytest.raises(IntegrityError, match="CHECK constraint failed: ck_products_version"):
        _product(db, version=0)


# ---- 商品图片的元数据 ----


def test_image_without_metadata_is_accepted(db: Session) -> None:
    """第 13 条「示例数据的占位图引用（/demo-images/…）不变」：五列全空的图片行能写入。"""
    product = _product(db)

    image = _add(
        db, ProductImage(product_id=product.id, storage_ref="/demo-images/tee.svg", sort_order=0)
    )

    stored = db.execute(
        select(
            ProductImage.file_name,
            ProductImage.source_format,
            ProductImage.width,
            ProductImage.height,
            ProductImage.byte_size,
        ).where(ProductImage.id == image.id)
    ).one()
    assert tuple(stored) == (None, None, None, None, None)


@pytest.mark.parametrize("source_format", ["jpeg", "png", "webp"])
def test_image_with_full_metadata_is_accepted(db: Session, source_format: str) -> None:
    """商品一行「后台上传的图片另记服务端生成的文件名（唯一）、上传时的格式与展示图的宽、
    高与字节数」：五列全有值的行能写入并读回，三种上传格式都收。
    """
    product = _product(db)
    metadata = METADATA | {"source_format": source_format}

    image = _image(db, product, **metadata)

    stored = db.execute(
        select(
            ProductImage.file_name,
            ProductImage.source_format,
            ProductImage.width,
            ProductImage.height,
            ProductImage.byte_size,
        ).where(ProductImage.id == image.id)
    ).one()
    assert tuple(stored) == tuple(metadata.values())


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param(["source_format", "width", "height", "byte_size"], id="only-file-name"),
        pytest.param(["file_name"], id="no-file-name"),
        pytest.param(["source_format"], id="no-source-format"),
        pytest.param(["width"], id="no-width"),
        pytest.param(["height"], id="no-height"),
        pytest.param(["byte_size"], id="no-byte-size"),
    ],
)
def test_partial_metadata_is_rejected(db: Session, missing: list[str]) -> None:
    """商品一行「后台上传的图片另记…文件名…、上传时的格式与展示图的宽、高与字节数」与
    HANDOFF 0.74「五列要么全空（示例数据的占位图引用）要么全有值」：只填部分列被拒。
    """
    product = _product(db)
    partial = {column: value for column, value in METADATA.items() if column not in missing}

    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_product_images_metadata_all_or_none",
    ):
        _image(db, product, **partial)


@pytest.mark.parametrize("source_format", ["gif", "tiff", "mpo"])
def test_source_format_outside_the_three_is_rejected(db: Session, source_format: str) -> None:
    """第 13 条「解码出的格式须为 JPEG（含手机常见的 MPO，只取主图，按 JPEG 记录）、PNG 或
    WebP」：上传时的格式只能是 jpeg、png、webp，其他值（MPO 按 JPEG 记，不单列）被拒。
    不用大小写变体：MySQL 默认排序规则比较时不分大小写，那样的反例只在 SQLite 上成立。
    """
    product = _product(db)

    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_product_images_source_format_valid",
    ):
        _image(db, product, **(METADATA | {"source_format": source_format}))


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"width": 0}, id="width-0"),
        pytest.param({"width": 1601}, id="width-1601"),
        pytest.param({"height": 0}, id="height-0"),
        pytest.param({"height": 1601}, id="height-1601"),
        pytest.param({"byte_size": 0}, id="byte-size-0"),
    ],
)
def test_display_size_out_of_range_is_rejected(db: Session, bad: dict[str, int]) -> None:
    """第 13 条「长边超过 1,600 像素时等比缩到 1,600」与 HANDOFF 0.74「宽高 1–1600、
    字节数至少 1」：展示图的宽或高为 0 或 1601、字节数为 0 都被拒。
    """
    product = _product(db)

    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_product_images_display_size_range",
    ):
        _image(db, product, **(METADATA | bad))


def test_display_size_bounds_are_accepted(db: Session) -> None:
    """上一条的对照（第 13 条「长边…缩到 1,600」）：宽高为 1 与 1600、字节数为 1 都能写入。"""
    product = _product(db)

    _image(db, product, 0, **(METADATA | {"width": 1, "height": 1600, "byte_size": 1}))
    _image(db, product, 1, **(METADATA | {"file_name": "f" * 32, "width": 1600, "height": 1}))

    assert len(db.scalars(select(ProductImage.id)).all()) == 2


def test_duplicate_file_name_is_rejected(db: Session) -> None:
    """商品一行「服务端生成的文件名（唯一）」与第 13 条「按文件名查到 ProductImage 的行」：
    同一商品的另一张图片（不同的排列序号）用了同一文件名也被唯一约束拒绝。
    """
    product = _product(db)
    _image(db, product, 0, **METADATA)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: product_images.file_name"):
        _image(db, product, 1, **METADATA)


# ---- 每日恢复库存开关 ----


def test_daily_stock_reset_defaults_to_on(db: Session) -> None:
    """SiteSetting 一行「每日恢复库存开关（1.18，布尔，默认开启…）」与第 6 条「默认开启
    （与 1.18 之前相同）」「按默认值插入该行时也写开启」：新建行不给开关值时，
    库里的服务端默认值是开启。
    """
    db.execute(insert(SiteSetting).values(id=1, updated_at=NOW))

    assert db.scalar(select(SiteSetting.daily_stock_reset_enabled)) is True


def test_daily_stock_reset_is_not_null(db: Session) -> None:
    """SiteSetting 一行「每日恢复库存开关（1.18，布尔…）」：开关写入 NULL 被拒，
    没有「未知」的第三种状态（HANDOFF 0.74「新增的非空列」）。
    """
    values = {"id": 1, "daily_stock_reset_enabled": None, "updated_at": NOW}

    with pytest.raises(IntegrityError, match="NOT NULL constraint failed"):
        db.execute(insert(SiteSetting).values(**values))


# ---- 迁移 0024 ----


def test_migration_revision_follows_0023() -> None:
    """HANDOFF 0.74「迁移 0024 照 0023 的写法」：revision 为 0024、接在 0023 之后。"""
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0024", "0023")


def test_migration_check_constraints_match_the_models() -> None:
    """商品一行「商品与分类各有版本号」「后台上传的图片另记…」与「每单限购件数（…1–1000…）」：
    迁移 0024 建的每个检查约束与模型的同名约束逐字一致（alembic check 不比较检查约束）。
    """
    models = (
        _model_checks(Category.__table__)
        | _model_checks(Product.__table__)
        | _model_checks(ProductImage.__table__)
    )

    for name, _table, condition in MIGRATION.CHECK_CONSTRAINTS:
        assert models[name] == condition, name
    assert models[MIGRATION.MAX_PER_ORDER_CONSTRAINT_NAME] == MIGRATION.NEW_MAX_PER_ORDER_CONSTRAINT


def test_migration_downgrade_restores_the_1_to_99_constraint() -> None:
    """商品一行「1–1000（1.18 起；此前为 1–99）」与 HANDOFF 0.74「恢复 1–99 的约束」：
    downgrade 建回的约束与迁移 0008 建的逐字相同（期望值照 0008 抄下，不从模型取），
    回退前拦下的门槛是 99。
    """
    assert MIGRATION.OLD_MAX_PER_ORDER_CONSTRAINT == "max_per_order >= 1 AND max_per_order <= 99"
    assert MIGRATION.OLD_MAX_PER_ORDER == 99


def test_migration_file_name_unique_matches_the_model(db: Session) -> None:
    """商品一行「服务端生成的文件名（唯一）」：按模型建的表在 file_name 上有唯一约束，
    其名称（app/db/base.py 的命名约定给出）与迁移 0024 建的相同。
    """
    uniques = inspect(db.get_bind()).get_unique_constraints("product_images")
    columns = {unique["name"]: unique["column_names"] for unique in uniques}

    assert columns[MIGRATION.FILE_NAME_UNIQUE] == ["file_name"]

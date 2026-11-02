"""后台商品图片的读取、上传入库、调序与移除（app/services/admin_product_images.py，SHOP-TASK-189）。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条（下称「第 13 条」）
与 docs/HANDOFF.md 0.79。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool），经 create_product 造商品；示例数据的占位图（引用
/demo-images/…、元数据为空）直接插入。ProcessedImage 直接构造，不经 Pillow；
app.services.image_store 的 put_image 与 delete_image 以 monkeypatch 替换并记录调用，不访问 R2。
SQLite 不生成行锁：锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import AdminAccount, AuditEvent, Category, Product, ProductImage
from app.services import image_store
from app.services.admin_product_images import (
    ADMIN_PRODUCT_IMAGE_ADDED,
    ADMIN_PRODUCT_IMAGE_REMOVED,
    ADMIN_PRODUCT_IMAGES_REORDERED,
    ImagesChanged,
    ImagesFull,
    ProductMissing,
    add_image,
    load_product_images,
    remove_image,
    reorder_images,
)
from app.services.admin_products import (
    AdminProductImage,
    ProductInput,
    VariantInput,
    create_product,
)
from app.services.image_processing import ProcessedImage
from app.services.image_store import ImageStoreUnavailable

NOW = datetime(2026, 11, 2, 3, 0)
SETTINGS = Settings(_env_file=None)
IMAGE = ProcessedImage(data=b"RIFF....WEBPVP8 display", source_format="png", width=800, height=600)
PLACEHOLDER_REF = "/demo-images/apparel.svg"
IMAGE_ACTIONS = (
    ADMIN_PRODUCT_IMAGE_ADDED,
    ADMIN_PRODUCT_IMAGES_REORDERED,
    ADMIN_PRODUCT_IMAGE_REMOVED,
)


def _product(name_en: str) -> ProductInput:
    return ProductInput(
        category_id=1,
        name_en=name_en,
        name_zh="",
        name_ms="",
        description_en=f"A {name_en.lower()}.",
        description_zh="",
        description_ms="",
        is_active=True,
        max_per_order=10,
        options=[],
        variants=[
            VariantInput(
                id=None,
                temp_id="v1",
                option_values=[],
                price_sen=2500,
                daily_initial_stock=10,
                is_active=True,
            )
        ],
    )


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(
            Category(
                slug="apparel",
                name_en="Apparel",
                name_zh="服饰",
                name_ms="Pakaian",
                is_active=True,
                version=1,
            )
        )
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin_id(db: Session) -> int:
    account = AdminAccount(
        username="admin@example.test", password_hash="x" * 60, password_updated_at=NOW
    )
    db.add(account)
    db.commit()
    return account.id


@pytest.fixture
def product_id(db: Session, admin_id: int) -> int:
    return create_product(db, admin_id, _product("Linen Shirt"), NOW).id


@pytest.fixture
def other_id(db: Session, admin_id: int, product_id: int) -> int:
    return create_product(db, admin_id, _product("Canvas Tote"), NOW).id


class FakeStore:
    """替换 image_store.put_image 与 delete_image，按顺序记录调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_put = False
        self.on_delete: Callable[[str], None] | None = None

    def put_image(self, settings: Settings, file_name: str, data: bytes) -> None:
        self.calls.append(("put", file_name, data))
        if self.fail_put:
            raise ImageStoreUnavailable

    def delete_image(self, settings: Settings, file_name: str) -> None:
        self.calls.append(("delete", file_name))
        if self.on_delete is not None:
            self.on_delete(file_name)

    @property
    def puts(self) -> list[str]:
        return [call[1] for call in self.calls if call[0] == "put"]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "put_image", fake.put_image)
    monkeypatch.setattr(image_store, "delete_image", fake.delete_image)
    return fake


# ---- 辅助 ----


def _placeholder(db: Session, product_id: int, sort_order: int) -> int:
    row = ProductImage(product_id=product_id, storage_ref=PLACEHOLDER_REF, sort_order=sort_order)
    db.add(row)
    db.commit()
    return row.id


def _add(db: Session, admin_id: int, product_id: int) -> list[AdminProductImage]:
    return add_image(db, SETTINGS, admin_id, product_id, IMAGE, NOW)


def _images(db: Session) -> list[tuple]:
    table = ProductImage.__table__
    return [tuple(row) for row in db.execute(select(table).order_by(table.c.id))]


def _sort_orders(db: Session) -> list[tuple[int, int]]:
    stmt = select(ProductImage.id, ProductImage.sort_order).order_by(ProductImage.id)
    return [tuple(row) for row in db.execute(stmt)]


def _image_audits(db: Session) -> list[tuple]:
    stmt = (
        select(
            AuditEvent.action,
            AuditEvent.target_type,
            AuditEvent.target_id,
            AuditEvent.old_value,
            AuditEvent.new_value,
            AuditEvent.admin_account_id,
            AuditEvent.occurred_at,
        )
        .where(AuditEvent.action.in_(IMAGE_ACTIONS))
        .order_by(AuditEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _version(db: Session, product_id: int) -> int:
    return db.scalar(select(Product.version).where(Product.id == product_id))


def _ids(images: list[AdminProductImage] | None) -> list[int]:
    assert images is not None
    return [image.id for image in images]


def _record_sequence(engine: Engine) -> list[str]:
    """回滚记为 ROLLBACK，SELECT 按 MySQL 方言编译，其他语句记类名。"""
    seen: list[str] = []

    @event.listens_for(engine, "rollback")
    def _rollback(_conn) -> None:
        seen.append("ROLLBACK")

    @event.listens_for(engine, "before_execute")
    def _execute(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        # 只编译 SELECT：SQLite 上 ORM 的 INSERT 可能带 RETURNING，MySQL 方言编译不了。
        if isinstance(clauseelement, Select):
            seen.append(str(clauseelement.compile(dialect=mysql.dialect())))
        else:
            seen.append(type(clauseelement).__name__)

    return seen


# ---- 读取 ----


def test_load_orders_by_sort_order(db: Session, product_id: int, other_id: int) -> None:
    """第 13 条「第一张为商品的封面」：图片按排列序号排列，只列该商品的。"""
    later = _placeholder(db, product_id, 2)
    first = _placeholder(db, product_id, 0)
    _placeholder(db, other_id, 0)
    middle = _placeholder(db, product_id, 1)

    images = load_product_images(db, product_id)

    assert images == [
        AdminProductImage(id=first, storage_ref=PLACEHOLDER_REF),
        AdminProductImage(id=middle, storage_ref=PLACEHOLDER_REF),
        AdminProductImage(id=later, storage_ref=PLACEHOLDER_REF),
    ]


def test_load_missing_product_is_none(db: Session, product_id: int) -> None:
    """第 13 条「只能传给已经保存过的商品」：商品不存在时为 None，商品存在而没有图片时为空表。"""
    assert load_product_images(db, 999) is None
    assert load_product_images(db, 2**40) is None
    assert load_product_images(db, product_id) == []


def test_load_only_selects(db: Session, engine: Engine, product_id: int) -> None:
    """第 13 条「排列与移除」的读取不写库：只发 SELECT。"""
    _placeholder(db, product_id, 0)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    load_product_images(db, product_id)

    assert statements
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


# ---- 上传 ----


def test_add_inserts_row_and_audit(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「文件名由服务端随机生成（32 位小写十六进制）……展示图存为存储桶里键为
    product-images/<文件名>.webp 的对象」「`ProductImage` 记文件名（唯一）、上传时的格式与展示图
    的宽、高与字节数，新图的排列序号为该商品现有的最大序号加一（没有图片时为 0）」「`ProductImage`
    的引用存这个路径」「图片的上传、排序与移除不改商品的版本号，各写一条审计（对象为商品，以商品
    ID 记录……）：上传的新值……为图片 ID」。"""
    images = _add(db, admin_id, product_id)

    [file_name] = store.puts
    assert re.fullmatch(r"[0-9a-f]{32}", file_name)
    assert store.calls == [("put", file_name, IMAGE.data)]
    ref = f"/api/product-images/{file_name}.webp"
    [row] = _images(db)
    image_id = row[0]
    assert row == (image_id, product_id, ref, 0, file_name, "png", 800, 600, len(IMAGE.data))
    assert images == [AdminProductImage(id=image_id, storage_ref=ref)]
    assert _image_audits(db) == [
        (ADMIN_PRODUCT_IMAGE_ADDED, "product", product_id, None, str(image_id), admin_id, NOW)
    ]
    assert _version(db, product_id) == 1


def test_add_sort_order_is_max_plus_one(
    db: Session, admin_id: int, product_id: int, other_id: int, store: FakeStore
) -> None:
    """第 13 条「新图的排列序号为该商品现有的最大序号加一」：现有序号 0 与 4 时新图为 5，
    别的商品的序号不算。"""
    first = _placeholder(db, product_id, 0)
    fifth = _placeholder(db, product_id, 4)
    _placeholder(db, other_id, 9)

    images = _add(db, admin_id, product_id)

    new_id = _ids(images)[-1]
    assert _ids(images) == [first, fifth, new_id]
    assert (new_id, 5) in _sort_orders(db)


def test_add_store_failure_inserts_nothing(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「写入失败或超时即回答『图片存储不可用』，不插入行」。"""
    store.fail_put = True

    with pytest.raises(ImageStoreUnavailable):
        _add(db, admin_id, product_id)

    assert _images(db) == []
    assert _image_audits(db) == []
    assert [call[0] for call in store.calls] == ["put"]


def test_ninth_image_is_full_and_deletes_object(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「该商品已有 8 张即回答『图片已满』」「写入成功后在一个事务里锁住商品行、数一次
    图片：已满即回滚、删除刚写入的对象（只尝试 1 次）后回答『图片已满』」。"""
    for _ in range(8):
        _add(db, admin_id, product_id)
    before = _images(db), _image_audits(db)

    with pytest.raises(ImagesFull):
        _add(db, admin_id, product_id)

    ninth = store.puts[-1]
    assert store.calls[-2:] == [("put", ninth, IMAGE.data), ("delete", ninth)]
    assert [call for call in store.calls if call[0] == "delete"] == [("delete", ninth)]
    assert (_images(db), _image_audits(db)) == before
    assert len(_images(db)) == 8


def test_add_missing_product_deletes_object(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「只能传给已经保存过的商品」「删除刚写入的对象（只尝试 1 次）」：锁商品行时商品已不
    存在即删除刚写入的对象、不插入行。"""
    with pytest.raises(ProductMissing):
        add_image(db, SETTINGS, admin_id, 999, IMAGE, NOW)

    [file_name] = store.puts
    assert store.calls == [("put", file_name, IMAGE.data), ("delete", file_name)]
    assert _images(db) == []
    assert _image_audits(db) == []


def test_add_commit_failure_deletes_object(
    db: Session,
    admin_id: int,
    product_id: int,
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 13 条「未满则插入行并提交，提交失败即删除该对象（只尝试 1 次）」。"""

    def _fail() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", _fail)

    with pytest.raises(RuntimeError):
        _add(db, admin_id, product_id)

    [file_name] = store.puts
    assert store.calls == [("put", file_name, IMAGE.data), ("delete", file_name)]
    assert _images(db) == []
    assert _image_audits(db) == []


def test_add_audit_failure_deletes_object(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「未满则插入行并提交」「删除刚写入的对象（只尝试 1 次）」：审计被拒（时间带时区）
    时回滚、不留下行，并删除刚写入的对象。"""
    with pytest.raises(ValueError):
        add_image(db, SETTINGS, admin_id, product_id, IMAGE, NOW.replace(tzinfo=UTC))

    [file_name] = store.puts
    assert store.calls == [("put", file_name, IMAGE.data), ("delete", file_name)]
    assert _images(db) == []


# ---- 调序 ----


@pytest.fixture
def three(db: Session, product_id: int, other_id: int) -> list[int]:
    """该商品的三张占位图（排列序号 0、1、2），另给别的商品一张。"""
    ids = [_placeholder(db, product_id, order) for order in range(3)]
    _placeholder(db, other_id, 0)
    return ids


def test_reorder_rewrites_sort_orders(
    db: Session, admin_id: int, product_id: int, three: list[int], store: FakeStore
) -> None:
    """第 13 条「可调整顺序：请求列出该商品现有的全部图片 ID 的新顺序」「改写排列序号时须避开
    （商品、排列序号）唯一约束的中间冲突」「调序不碰 R2」「不改商品的版本号……调序的旧值与新值为
    前后的图片 ID 序列」。"""
    a, b, c = three

    images = reorder_images(db, admin_id, product_id, [c, a, b], NOW)

    assert _ids(images) == [c, a, b]
    assert _ids(load_product_images(db, product_id)) == [c, a, b]
    orders = dict(_sort_orders(db))
    assert (orders[c], orders[a], orders[b]) == (0, 1, 2)
    assert _image_audits(db) == [
        (
            ADMIN_PRODUCT_IMAGES_REORDERED,
            "product",
            product_id,
            f"{a},{b},{c}",
            f"{c},{a},{b}",
            admin_id,
            NOW,
        )
    ]
    assert _version(db, product_id) == 1
    assert store.calls == []


@pytest.mark.parametrize(
    "case",
    ["missing", "extra", "duplicate", "other_product", "empty"],
)
def test_reorder_rejects_ids_not_exactly_current(
    db: Session,
    admin_id: int,
    product_id: int,
    three: list[int],
    store: FakeStore,
    case: str,
) -> None:
    """第 13 条「不是恰好这些 ID 即回答『图片已被改动』」：缺、多、重复、属于别的商品或为空都是
    ImagesChanged，顺序不变、不写审计。"""
    a, b, c = three
    foreign = a + 3
    requested = {
        "missing": [c, b],
        "extra": [c, b, a, foreign],
        "duplicate": [c, b, b],
        "other_product": [c, b, foreign],
        "empty": [],
    }[case]
    before = _images(db), _image_audits(db)

    with pytest.raises(ImagesChanged):
        reorder_images(db, admin_id, product_id, requested, NOW)

    assert (_images(db), _image_audits(db)) == before


def test_reorder_unchanged_writes_nothing(
    db: Session, admin_id: int, product_id: int, three: list[int], store: FakeStore
) -> None:
    """第 13 条「各写一条审计」（HANDOFF 0.79：顺序未变不写库、不写审计）：返回现有顺序。"""
    before = _images(db), _image_audits(db)

    images = reorder_images(db, admin_id, product_id, list(three), NOW)

    assert _ids(images) == three
    assert (_images(db), _image_audits(db)) == before


def test_reorder_missing_product(db: Session, admin_id: int, product_id: int) -> None:
    """第 13 条「只能传给已经保存过的商品」：商品不存在即 ProductMissing。"""
    with pytest.raises(ProductMissing):
        reorder_images(db, admin_id, 999, [], NOW)


# ---- 移除 ----


def test_remove_deletes_row_then_object(
    db: Session,
    engine: Engine,
    admin_id: int,
    product_id: int,
    store: FakeStore,
) -> None:
    """第 13 条「可移除：删除数据库行，提交后再删存储桶里的对象」「移除的旧值为图片 ID」「不改
    商品的版本号」：替换的 delete_image 里另开会话查库，该行已不存在。"""
    first = _ids(_add(db, admin_id, product_id))[0]
    kept = _ids(_add(db, admin_id, product_id))[1]
    file_name = store.puts[0]
    remaining: list[int] = []

    def _check(name: str) -> None:
        with Session(engine) as check:
            stmt = select(ProductImage.id).where(ProductImage.file_name == name)
            remaining.extend(check.scalars(stmt))

    store.on_delete = _check

    images = remove_image(db, SETTINGS, admin_id, product_id, first, NOW)

    assert store.calls[-1] == ("delete", file_name)
    assert remaining == []
    assert _ids(images) == [kept]
    assert [row[0] for row in _images(db)] == [kept]
    assert _image_audits(db)[-1] == (
        ADMIN_PRODUCT_IMAGE_REMOVED,
        "product",
        product_id,
        str(first),
        None,
        admin_id,
        NOW,
    )
    assert _version(db, product_id) == 1


def test_remove_placeholder_does_not_call_store(
    db: Session, admin_id: int, product_id: int, store: FakeStore
) -> None:
    """第 13 条「示例数据的占位图引用（/demo-images/…）不变」：占位图没有存储桶里的对象，移除时
    只删行、不调用 delete_image。"""
    image_id = _placeholder(db, product_id, 0)

    images = remove_image(db, SETTINGS, admin_id, product_id, image_id, NOW)

    assert images == []
    assert _images(db) == []
    assert store.calls == []
    assert len(_image_audits(db)) == 1


def test_remove_first_keeps_others_order(
    db: Session, admin_id: int, product_id: int, three: list[int], store: FakeStore
) -> None:
    """第 13 条「第一张为商品的封面」（HANDOFF 0.79：移除后其余图片的排列序号不重排，第一张仍按
    排列序号最小者）。"""
    a, b, c = three

    images = remove_image(db, SETTINGS, admin_id, product_id, a, NOW)

    assert _ids(images) == [b, c]
    orders = dict(_sort_orders(db))
    assert (orders[b], orders[c]) == (1, 2)
    assert a not in orders


@pytest.mark.parametrize("case", ["other_product", "missing", "out_of_range"])
def test_remove_rejects_image_not_of_product(
    db: Session,
    admin_id: int,
    product_id: int,
    three: list[int],
    store: FakeStore,
    case: str,
) -> None:
    """第 13 条「排列与移除」（HANDOFF 0.79：移除时请求的图片不属于该商品即『图片已被改动』）：
    属于别的商品或不存在都是 ImagesChanged，不删行、不写审计、不调用存储。"""
    image_id = {"other_product": three[0] + 3, "missing": 999, "out_of_range": 2**40}[case]
    before = _images(db), _image_audits(db)

    with pytest.raises(ImagesChanged):
        remove_image(db, SETTINGS, admin_id, product_id, image_id, NOW)

    assert (_images(db), _image_audits(db)) == before
    assert store.calls == []


def test_remove_missing_product(
    db: Session, admin_id: int, product_id: int, three: list[int], store: FakeStore
) -> None:
    """第 13 条「只能传给已经保存过的商品」：商品不存在即 ProductMissing。"""
    with pytest.raises(ProductMissing):
        remove_image(db, SETTINGS, admin_id, 999, three[0], NOW)

    assert store.calls == []


# ---- 锁 ----


@pytest.mark.parametrize("operation", ["add", "reorder", "remove"])
def test_changes_lock_product_first_after_rollback(
    db: Session,
    engine: Engine,
    admin_id: int,
    product_id: int,
    three: list[int],
    store: FakeStore,
    operation: str,
) -> None:
    """第 13 条「写入成功后在一个事务里锁住商品行、数一次图片」（HANDOFF 0.79：三个改动函数都先
    回滚此前的事务，再以 lock_product_statement 锁商品行）：回滚之后的第一条语句是按 MySQL 方言
    编译带 FOR UPDATE 的商品行锁定查询，之后不再加锁。"""
    a, b, c = three
    load_product_images(db, product_id)
    seen = _record_sequence(engine)

    if operation == "add":
        _add(db, admin_id, product_id)
    elif operation == "reorder":
        reorder_images(db, admin_id, product_id, [c, b, a], NOW)
    else:
        remove_image(db, SETTINGS, admin_id, product_id, a, NOW)

    assert seen[0] == "ROLLBACK"
    lock = seen[1]
    assert "FROM products" in lock
    assert lock.rstrip().endswith("FOR UPDATE")
    assert [sql for sql in seen if "FOR UPDATE" in sql or "SHARE" in sql] == [lock]

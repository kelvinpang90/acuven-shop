"""店铺装修设置的读取函数与 GET /api/store-design（SHOP-TASK-045）。

依据 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01，以及 docs/HANDOFF.md 0.34
记录的 Kelvin 2026-10-06 决定（下称「Kelvin 2026-10-06」：存储与公开读取首版不含标志图；精选商品
只显示前台目录可见的商品，复用目录的 published() 判定）。
每条测试（参数化的测试是每个用例）写明它守住的需求原句或 Kelvin 的决定；没有直接原句的写明是
SHOP-TASK-045 验收标准里的约定。

用 SQLite 内存库按模型建表并打开外键检查；接口的会话依赖换成测试自己的会话。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, func, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Product,
    ProductVariant,
    StoreDesignSetting,
    StoreFeaturedProduct,
    StoreHomeBlock,
)
from app.models.store_design import (
    DEFAULT_ACCENT,
    DEFAULT_THEME,
    HOME_BLOCKS,
    SINGLETON_SLOT,
)
from app.services.store_design import HomeBlock, StoreDesign, read_store_design

URL = "/api/store-design"


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app(Settings(_env_file=None))
    # 接口用测试自己的会话：flush 过的数据就看得到，不必提交。
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _category(db: Session, slug: str, *, active: bool = True) -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=active))


def _product(
    db: Session,
    slug: str,
    category: Category,
    *,
    active: bool = True,
    variant_active: bool | None = True,
    description_en: str | None = "Description",
) -> Product:
    """variant_active 为 None 时不建规格。"""
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=slug,
            name_en=slug.title(),
            description_en=description_en,
            is_active=active,
        ),
    )
    if variant_active is not None:
        _add(
            db,
            ProductVariant(
                product_id=product.id,
                sku=f"{slug}-sku",
                price_sen=1000,
                daily_initial_stock=5,
                available_stock=5,
                is_active=variant_active,
            ),
        )
    return product


def _setting(db: Session, theme: str, accent: str | None) -> None:
    _add(db, StoreDesignSetting(singleton_slot=SINGLETON_SLOT, theme=theme, accent=accent))


def _blocks(db: Session, *rows: tuple[str, int, bool]) -> None:
    for block, position, visible in rows:
        _add(db, StoreHomeBlock(block=block, position=position, is_visible=visible))


def _feature(db: Session, *products: tuple[int, Product]) -> None:
    for position, product in products:
        _add(db, StoreFeaturedProduct(position=position, product_id=product.id))


def _record_statements(db: Session) -> list[str]:
    statements: list[str] = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    return statements


def _all_visible() -> list[HomeBlock]:
    return [HomeBlock(block=block, visible=True) for block in HOME_BLOCKS]


# ---- 读取函数 ----


def test_read_orders_blocks_and_featured_by_position(db: Session) -> None:
    """「管理员可调整它们的顺序与显示或隐藏」「挑选最多 4 件已上架商品并排定顺序；前台按该顺序
    ……显示」：区块与精选都按位置排序，与写入顺序、商品 id 无关；
    隐藏的区块照样列出且标为不显示。"""
    _setting(db, "batik", "sogan")
    # 故意按与位置不同的顺序写入。
    _blocks(
        db,
        ("hero", 3, False),
        ("featured", 1, True),
        ("categories", 4, True),
        ("how", 2, False),
    )
    category = _category(db, "home")
    first, second, third, fourth = (_product(db, f"p-{n}", category) for n in range(1, 5))
    _feature(db, (4, first), (2, second), (1, third), (3, fourth))

    assert read_store_design(db) == StoreDesign(
        theme="batik",
        accent="sogan",
        home_blocks=[
            HomeBlock(block="featured", visible=True),
            HomeBlock(block="how", visible=False),
            HomeBlock(block="hero", visible=False),
            HomeBlock(block="categories", visible=True),
        ],
        featured_slugs=["p-3", "p-2", "p-4", "p-1"],
    )


def test_read_skips_featured_products_hidden_from_the_catalog_and_keeps_order(
    db: Session,
) -> None:
    """「前台按该顺序只显示仍上架的商品」；Kelvin 2026-10-06「精选商品只显示前台目录可见的商品
    （复用目录的 published() 判定）」：停用的商品、所属分类停用的商品、
    没有启用规格的商品（规格全部停用或没有规格）与缺英文描述的商品都被略去，其余仍按位置排序。"""
    shop = _category(db, "shop")
    closed = _category(db, "closed", active=False)
    visible_a = _product(db, "visible-a", shop)
    inactive = _product(db, "inactive", shop, active=False)
    in_closed_category = _product(db, "in-closed-category", closed)
    visible_b = _product(db, "visible-b", shop)
    _feature(db, (1, inactive), (2, visible_b), (3, in_closed_category), (4, visible_a))

    assert read_store_design(db).featured_slugs == ["visible-b", "visible-a"]

    db.execute(delete(StoreFeaturedProduct))
    variant_off = _product(db, "variant-off", shop, variant_active=False)
    no_variant = _product(db, "no-variant", shop, variant_active=None)
    no_description = _product(db, "no-description", shop, description_en=" ")
    _feature(db, (1, variant_off), (2, visible_a), (3, no_variant), (4, no_description))

    assert read_store_design(db).featured_slugs == ["visible-a"]


def test_read_keeps_hidden_featured_rows_in_the_table(db: Session) -> None:
    """docs/UX.md A08「之后下架的商品仍留在列表中，前台不显示它」：读取只是略去，不删精选行；
    商品重新启用后又按原位置出现。"""
    shop = _category(db, "shop")
    first = _product(db, "first", shop)
    second = _product(db, "second", shop, active=False)
    _feature(db, (1, second), (2, first))

    assert read_store_design(db).featured_slugs == ["first"]
    assert db.scalar(select(func.count()).select_from(StoreFeaturedProduct)) == 2

    second.is_active = True
    db.flush()
    assert read_store_design(db).featured_slugs == ["second", "first"]


def test_read_with_nothing_featured_returns_an_empty_list(db: Session) -> None:
    """SHOP-TASK-045 验收：「未挑选或挑选的都不可见时显示最新 4 件」
    由前台按设置显示的任务实现，读取不补最新商品；没有精选或都不可见时给空列表。"""
    shop = _category(db, "shop")
    _product(db, "newest", shop)
    hidden = _product(db, "hidden", shop, active=False)

    assert read_store_design(db).featured_slugs == []

    _feature(db, (1, hidden))
    assert read_store_design(db).featured_slugs == []


def test_read_without_a_setting_row_returns_the_defaults(db: Session) -> None:
    """SHOP-TASK-045 验收「设置行不存在时主题与主色返回 SHOP-TASK-044 的默认值常量」；
    区块与精选照常从各自的表读。"""
    _blocks(
        db,
        ("how", 1, False),
        ("hero", 2, True),
        ("featured", 3, True),
        ("categories", 4, True),
    )

    design = read_store_design(db)

    assert (design.theme, design.accent) == (DEFAULT_THEME, DEFAULT_ACCENT)
    assert design.home_blocks[0] == HomeBlock(block="how", visible=False)


def test_read_with_empty_tables_returns_the_full_default(db: Session) -> None:
    """「首页由四个区块组成」；SHOP-TASK-044 的默认值：三张表都空时是默认主题、默认主色、四个
    区块按默认顺序全部显示、没有精选。"""
    assert read_store_design(db) == StoreDesign(
        theme=DEFAULT_THEME,
        accent=DEFAULT_ACCENT,
        home_blocks=_all_visible(),
        featured_slugs=[],
    )


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        pytest.param(
            [("featured", 1, False), ("how", 3, True)],
            [("featured", False), ("how", True), ("hero", True), ("categories", True)],
            id="two-missing",
        ),
        pytest.param(
            [("categories", 2, False)],
            [("categories", False), ("hero", True), ("how", True), ("featured", True)],
            id="three-missing",
        ),
        pytest.param(
            [("hero", 4, False), ("how", 3, False), ("categories", 2, False)],
            [("categories", False), ("how", False), ("hero", False), ("featured", True)],
            id="one-missing",
        ),
    ],
)
def test_read_fills_missing_blocks_in_default_order(
    db: Session, rows: list[tuple[str, int, bool]], expected: list[tuple[str, bool]]
) -> None:
    """「首页由四个区块组成」；SHOP-TASK-045 验收「区块表缺少某些区块时，
    缺少的区块按默认顺序补在已有区块之后并显示，结果总是四个区块」。"""
    _blocks(db, *rows)

    blocks = read_store_design(db).home_blocks

    assert [(block.block, block.visible) for block in blocks] == expected
    assert sorted(block.block for block in blocks) == sorted(HOME_BLOCKS)


def test_read_only_selects_and_writes_nothing_back(db: Session) -> None:
    """SHOP-TASK-045 验收「读取不写库」：只发 SELECT；缺少的区块与设置行不会被补写进库里。"""
    _blocks(db, ("hero", 1, True))
    statements = _record_statements(db)

    read_store_design(db)
    db.flush()

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert db.scalars(select(StoreHomeBlock.block)).all() == ["hero"]
    assert db.scalar(select(StoreDesignSetting.id)) is None


def test_read_does_not_flush_pending_or_dirty_changes(db: Session) -> None:
    """SHOP-TASK-045 验收「读取不写库」：调用方会话里有未 flush 的新行与改动时，读取也只发
    SELECT，不把它们 flush 进库；读到的是库里已有的数据，改动仍留在会话里等调用方处理。"""
    _setting(db, "batik", "sogan")
    _blocks(db, ("hero", 1, True))
    setting = db.scalars(select(StoreDesignSetting)).one()
    setting.theme, setting.accent = "litar", "red"
    pending = StoreHomeBlock(block="how", position=2, is_visible=False)
    db.add(pending)
    statements = _record_statements(db)

    design = read_store_design(db)

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert (design.theme, design.accent) == ("batik", "sogan")
    assert design.home_blocks == _all_visible()
    assert pending in db.new
    assert setting in db.dirty


# ---- 公开接口 ----


def test_endpoint_returns_the_fields(db: Session, client: TestClient) -> None:
    """「装修保存后对所有访客生效」；Kelvin 2026-10-06「公开读取首版不含标志图」：接口返回主题、
    主色、按位置排序的区块（区块名与是否显示）与精选 slug，字段只有这四个。"""
    _setting(db, "malam", "lime")
    _blocks(
        db,
        ("featured", 1, True),
        ("hero", 2, False),
        ("how", 3, True),
        ("categories", 4, True),
    )
    shop = _category(db, "shop")
    _feature(db, (2, _product(db, "lamp", shop)), (1, _product(db, "mug", shop)))

    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "theme": "malam",
        "accent": "lime",
        "home_blocks": [
            {"block": "featured", "visible": True},
            {"block": "hero", "visible": False},
            {"block": "how", "visible": True},
            {"block": "categories", "visible": True},
        ],
        "featured_slugs": ["mug", "lamp"],
    }
    assert list(response.json()) == ["theme", "accent", "home_blocks", "featured_slugs"]


def test_endpoint_returns_null_accent(db: Session, client: TestClient) -> None:
    """SHOP-TASK-044「主色为空即该主题的默认主色」；SHOP-TASK-045 验收「accent 为空时返回
    null」：库里主色为空与设置行不存在时都给 null，不替前台换成某个主色 id。"""
    assert client.get(URL).json() == {
        "theme": DEFAULT_THEME,
        "accent": None,
        "home_blocks": [{"block": block, "visible": True} for block in HOME_BLOCKS],
        "featured_slugs": [],
    }

    _setting(db, "galeri", None)
    body = client.get(URL).json()
    assert (body["theme"], body["accent"]) == ("galeri", None)


def test_endpoint_follows_changes_without_caching(db: Session, client: TestClient) -> None:
    """「装修保存后对所有访客生效」：响应带 Cache-Control: no-store，库里改了之后下一次请求就是
    新值。"""
    _setting(db, "pandan", None)
    first = client.get(URL)

    db.execute(update(StoreDesignSetting).values(theme="litar", accent="red"))
    second = client.get(URL)

    for response in (first, second):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert (second.json()["theme"], second.json()["accent"]) == ("litar", "red")


def test_endpoint_without_a_database_answers_503_with_no_store() -> None:
    """SHOP-TASK-045 验收「数据库未配置时与其他公开接口相同返回 503」
    「所有响应 no-store（含数据库未配置的 503）」。"""
    client = TestClient(create_app(Settings(_env_file=None, database_url="")))

    response = client.get(URL)

    assert response.status_code == 503
    assert response.json() == {"detail": "database is not configured"}
    assert response.headers["cache-control"] == "no-store"


def test_endpoint_needs_no_session_or_csrf_and_writes_no_log(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-045 验收「不读写会话、不要求 CSRF、不写日志」「不做后台修改接口」：
    带不带 cookie 结果一样、不设 cookie、不需要 CSRF 头，应用不记日志，只发 SELECT，
    写方法得到 405。"""
    _setting(db, "receipt", "cobalt")
    statements = _record_statements(db)

    with caplog.at_level(logging.DEBUG):
        anonymous = client.get(URL)
        client.cookies.set("session", "anything")
        with_cookies = client.get(URL)

    for response in (anonymous, with_cookies):
        assert response.status_code == 200
        assert response.json()["accent"] == "cobalt"
        assert "set-cookie" not in response.headers
    assert [record for record in caplog.records if record.name.startswith("app")] == []
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert client.request(method, URL, json={}).status_code == 405

"""店铺装修设置的三张表、主题与主色常量表与默认值（SHOP-TASK-044）。

依据 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01，以及 docs/HANDOFF.md 0.34
记录的 Kelvin 2026-10-06 决定（下称「Kelvin 2026-10-06」：店铺装修设置的存储首版不含标志图）。
主题与主色的取值以 docs/design/tokens/themes.json 为准。
每条测试（参数化的测试是每个用例）写明它守住的需求原句或 Kelvin 的决定。

先写入合法的设置、四个区块与精选商品作对照，再逐个写反例。检查约束的反例先在独立的 SQLite
连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，再断言写入被拒、且报出的是不成立的
约束之一（与 tests/test_refund_review_models.py 相同）。
用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
迁移 0016 的默认值另在一个空的 SQLite 连接上经 Alembic 的 Operations 执行 upgrade 后查回。
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import (
    CheckConstraint,
    Connection,
    Table,
    create_engine,
    delete,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Product,
    StoreDesignSetting,
    StoreFeaturedProduct,
    StoreHomeBlock,
)
from app.models.store_design import (
    DEFAULT_ACCENT,
    DEFAULT_HOME_BLOCKS,
    DEFAULT_THEME,
    FEATURED_MAX,
    HOME_BLOCKS,
    THEME_ACCENTS,
    THEMES,
)

ROOT = Path(__file__).resolve().parents[1]
THEMES_JSON = ROOT / "docs" / "design" / "tokens" / "themes.json"
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20261006_0016_store_design.py"

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"
UPDATED = datetime(2026, 10, 6, 2, 0)

STORE_TABLES = ("store_design_settings", "store_home_blocks", "store_featured_products")

# 只用来对单行求值检查约束表达式，不建任何表。
_EVAL_ENGINE = create_engine("sqlite://")


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("store_design_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield db
    engine.dispose()


@pytest.fixture
def migrated() -> Iterator[Connection]:
    """空的 SQLite 连接上只执行迁移 0016 的 upgrade（不按模型建表）。"""
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        _run_migration(conn, "upgrade")
        yield conn
    engine.dispose()


def _run_migration(conn: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(MIGRATION, step)()


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def _products(session: Session, count: int) -> list[Product]:
    category = _add(session, Category(slug="home", name_en="Home", is_active=True))
    return [
        _add(
            session,
            Product(
                category_id=category.id,
                slug=f"product-{n}",
                name_en=f"Product {n}",
                description_en="Demo",
                is_active=True,
            ),
        )
        for n in range(1, count + 1)
    ]


def _setting_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "singleton_slot": 1,
        "theme": DEFAULT_THEME,
        "accent": DEFAULT_ACCENT,
        "updated_at": UPDATED,
    }
    return values | overrides


def _block_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"block": "hero", "position": 1, "is_visible": True}
    return values | overrides


def _add_default_blocks(session: Session) -> None:
    for block, position, is_visible in DEFAULT_HOME_BLOCKS:
        _add(session, StoreHomeBlock(block=block, position=position, is_visible=is_visible))


def _check_constraints(table: Table) -> list[CheckConstraint]:
    constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    for column in table.columns:
        constraints += [c for c in column.constraints if isinstance(c, CheckConstraint)]
    return constraints


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值该表的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints(model.__table__):
            sql = text(f"SELECT ({constraint.sqltext}) FROM (SELECT {columns})")
            if connection.execute(sql, params).scalar_one() == 0:
                failing.append(str(constraint.name))
    return failing


def _assert_check_rejects(
    session: Session, model: type[Base], constraint: str, values: dict[str, Any]
) -> None:
    failing = _failing_checks(model, values)
    assert constraint in failing
    pattern = r"CHECK constraint failed: (?:" + "|".join(failing) + r")\b"

    with pytest.raises(IntegrityError, match=pattern):
        _add(session, model(**values))


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_setting_blocks_and_featured_products_are_accepted(session: Session) -> None:
    """「从 10 款预设主题中选一款」「只能从中选一个」主色；「首页由四个区块组成……管理员可调整
    它们的顺序与显示或隐藏」；「挑选最多 4 件……并排定顺序」。这是下面各条拒绝测试的对照：
    同样的建表与外键检查下，默认设置、换成另一款主题及其主色（含最长的主题 id）、调过顺序且
    有隐藏的四个区块与满 4 件的精选商品都写得进去，且求值辅助函数对它们不报任何约束不成立。
    """
    setting_values = _setting_values()
    assert _failing_checks(StoreDesignSetting, setting_values) == []
    setting = _add(session, StoreDesignSetting(**setting_values))
    for theme in THEMES:
        changed = _setting_values(theme=theme, accent=THEME_ACCENTS[theme][-1])
        assert _failing_checks(StoreDesignSetting, changed) == []
    setting.theme = "kopitiam"
    setting.accent = "teh"
    session.flush()

    reordered = [
        ("featured", 1, True),
        ("hero", 2, False),
        ("categories", 3, True),
        ("how", 4, False),
    ]
    for block, position, is_visible in reordered:
        values = _block_values(block=block, position=position, is_visible=is_visible)
        assert _failing_checks(StoreHomeBlock, values) == []
        _add(session, StoreHomeBlock(**values))

    products = _products(session, FEATURED_MAX)
    for position, product in enumerate(reversed(products), start=1):
        values = {"position": position, "product_id": product.id}
        assert _failing_checks(StoreFeaturedProduct, values) == []
        _add(session, StoreFeaturedProduct(**values))

    settings = StoreDesignSetting.__table__.c
    stored_setting = session.execute(
        select(settings.singleton_slot, settings.theme, settings.accent)
    ).all()
    assert [tuple(row) for row in stored_setting] == [(1, "kopitiam", "teh")]
    blocks = StoreHomeBlock.__table__.c
    stored_blocks = session.execute(
        select(blocks.block, blocks.position, blocks.is_visible).order_by(blocks.position)
    ).all()
    assert [tuple(row) for row in stored_blocks] == reordered
    stored_featured = session.scalars(
        select(StoreFeaturedProduct.product_id).order_by(StoreFeaturedProduct.position)
    ).all()
    assert stored_featured == [product.id for product in reversed(products)]


def test_columns_nullability_and_lengths() -> None:
    """「主色……管理员只能从中选一个」，未选即该主题的默认主色：只有主色可空，其余列都非空
    （检查约束遇到空值会放行，非空约束不能省）；主题与主色列长 20。Kelvin 2026-10-06「首版先不含
    标志图」：设置表没有标志图的列。
    """
    settings = StoreDesignSetting.__table__.c
    assert [column.name for column in settings] == [
        "id",
        "singleton_slot",
        "theme",
        "accent",
        "updated_at",
    ]
    assert [column.name for column in settings if column.nullable] == ["accent"]
    assert settings.theme.type.length == 20
    assert settings.accent.type.length == 20
    for model in (StoreHomeBlock, StoreFeaturedProduct):
        assert not any(column.nullable for column in model.__table__.c)


# ---------------------------------------------------------------------------
# 检查约束
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "model", "constraint", "values"),
    [
        pytest.param(
            "设置只有一行（单例槽只能是 1）：第二行设置换用槽 2",
            StoreDesignSetting,
            "ck_store_design_settings_singleton_slot_one",
            _setting_values(singleton_slot=2),
            id="setting-slot-2",
        ),
        pytest.param(
            "「从 10 款预设主题中选一款」：未知主题",
            StoreDesignSetting,
            "ck_store_design_settings_theme_known",
            _setting_values(theme="neon"),
            id="unknown-theme",
        ),
        pytest.param(
            "「从 10 款预设主题中选一款」：主题 id 按 themes.json 原样，大小写不同即未知",
            StoreDesignSetting,
            "ck_store_design_settings_theme_known",
            _setting_values(theme="Pandan"),
            id="theme-wrong-case",
        ),
        pytest.param(
            "「管理员只能从中选一个」，不选即为空（不存空串）：主色为空串",
            StoreDesignSetting,
            "ck_store_design_settings_accent_length",
            _setting_values(accent=""),
            id="empty-accent",
        ),
        pytest.param(
            "「不能输入任意颜色」，主色是选项 id：主色长 21",
            StoreDesignSetting,
            "ck_store_design_settings_accent_length",
            _setting_values(accent="a" * 21),
            id="accent-too-long",
        ),
        pytest.param(
            "「首页由四个区块组成：主视觉、演示怎么玩、按分类浏览、精选商品」：未知区块",
            StoreHomeBlock,
            "ck_store_home_blocks_block_known",
            _block_values(block="banner"),
            id="unknown-block",
        ),
        pytest.param(
            "「首页由四个区块组成」，★ home.demo_hint 不属于任何区块（UX P01）：提示不能成为区块",
            StoreHomeBlock,
            "ck_store_home_blocks_block_known",
            _block_values(block="demo_hint"),
            id="demo-hint-block",
        ),
        pytest.param(
            "四个区块的顺序是位置 1 到 4：位置 0",
            StoreHomeBlock,
            "ck_store_home_blocks_position_range",
            _block_values(position=0),
            id="block-position-0",
        ),
        pytest.param(
            "四个区块的顺序是位置 1 到 4：位置 5",
            StoreHomeBlock,
            "ck_store_home_blocks_position_range",
            _block_values(position=5),
            id="block-position-5",
        ),
        pytest.param(
            "「挑选最多 4 件……并排定顺序」：精选位置 0",
            StoreFeaturedProduct,
            "ck_store_featured_products_position_range",
            {"position": 0, "product_id": "product"},
            id="featured-position-0",
        ),
        pytest.param(
            "「挑选最多 4 件」：第 5 件精选（位置 5）",
            StoreFeaturedProduct,
            "ck_store_featured_products_position_range",
            {"position": 5, "product_id": "product"},
            id="featured-position-5",
        ),
    ],
)
def test_check_constraints_reject(
    session: Session,
    requirement: str,
    model: type[Base],
    constraint: str,
    values: dict[str, Any],
) -> None:
    """三张表的每个检查约束至少一个反例；守住的需求原句见每个用例的 requirement。

    商品写成占位 "product" 的用例换成真实商品的 ID，使外键成立、只有检查约束能拒绝它。
    """
    assert requirement
    if values.get("product_id") == "product":
        values = values | {"product_id": _products(session, 1)[0].id}

    _assert_check_rejects(session, model, constraint, values)


# ---------------------------------------------------------------------------
# 唯一约束与外键
# ---------------------------------------------------------------------------


def test_second_setting_row_is_rejected(session: Session) -> None:
    """设置只有一行（单例槽全表唯一）：已有设置时再写一行（槽同为 1）被唯一约束拒绝。"""
    _add(session, StoreDesignSetting(**_setting_values()))
    second = _setting_values(theme="litar", accent="violet")

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: store_design_settings.singleton_slot"
    ):
        _add(session, StoreDesignSetting(**second))


@pytest.mark.parametrize(
    ("requirement", "values", "column"),
    [
        pytest.param(
            "「首页由四个区块组成」，每个区块一行：重复的区块（换一个空着的位置也不行）",
            _block_values(block="hero", position=4),
            "block",
            id="duplicate-block",
        ),
        pytest.param(
            "「调整它们的顺序」，每个位置只有一个区块：两个区块同在位置 1",
            _block_values(block="featured", position=1),
            "position",
            id="duplicate-block-position",
        ),
    ],
)
def test_duplicate_home_block_is_rejected(
    session: Session, requirement: str, values: dict[str, Any], column: str
) -> None:
    """区块与位置各自全表唯一；守住的需求原句见每个用例的 requirement。"""
    assert requirement
    _add(session, StoreHomeBlock(**_block_values(block="hero", position=1)))

    with pytest.raises(
        IntegrityError, match=f"UNIQUE constraint failed: store_home_blocks.{column}"
    ):
        _add(session, StoreHomeBlock(**values))


def test_duplicate_featured_product_is_rejected(session: Session) -> None:
    """UX A08「同一商品不重复」：同一商品再精选一次（换一个空着的位置）被唯一约束拒绝。"""
    product = _products(session, 1)[0]
    _add(session, StoreFeaturedProduct(position=1, product_id=product.id))

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: store_featured_products.product_id"
    ):
        _add(session, StoreFeaturedProduct(position=2, product_id=product.id))


def test_duplicate_featured_position_is_rejected(session: Session) -> None:
    """「挑选最多 4 件……并排定顺序」：两件精选同在位置 1 被唯一约束拒绝（位置 1 到 4 各至多
    一件，故至多四行）。
    """
    first, second = _products(session, 2)
    _add(session, StoreFeaturedProduct(position=1, product_id=first.id))

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: store_featured_products.position"
    ):
        _add(session, StoreFeaturedProduct(position=1, product_id=second.id))


def test_featured_product_must_exist(session: Session) -> None:
    """「精选商品由管理员挑选……已上架商品」：精选必须指向存在的商品。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, StoreFeaturedProduct(position=1, product_id=999))


def test_featured_product_cannot_be_deleted(session: Session) -> None:
    """UX A08「之后下架的商品仍留在列表中」：被精选引用的商品不能物理删除（RESTRICT），
    精选不会被连带删掉。
    """
    product = _products(session, 1)[0]
    _add(session, StoreFeaturedProduct(position=1, product_id=product.id))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(Product).where(Product.id == product.id))


# ---------------------------------------------------------------------------
# 常量表与 themes.json
# ---------------------------------------------------------------------------


def test_theme_accents_match_themes_json() -> None:
    """「从 10 款预设主题中选一款」「每款主题提供若干预先校过对比度的主色」：常量表的主题 id
    （含顺序）与每款主题可选主色的 id（含顺序，第一项为默认主色）与 docs/design/tokens/themes.json
    逐一相同。
    """
    data = json.loads(THEMES_JSON.read_text(encoding="utf-8"))
    expected = [
        (theme["id"], tuple(option["id"] for option in theme["accentOptions"]))
        for theme in data["themes"]
    ]

    assert len(expected) == 10
    assert list(THEME_ACCENTS.items()) == expected
    assert THEMES == tuple(theme_id for theme_id, _ in expected)
    assert all(len(accents) == len(set(accents)) for accents in THEME_ACCENTS.values())
    assert DEFAULT_THEME == data["default"]


def test_default_constants() -> None:
    """默认值：主题 pandan、主色为空（即该主题的默认主色），四个区块按主视觉、演示怎么玩、
    按分类浏览、精选商品的顺序全部显示；精选最多 4 件。Kelvin 2026-10-06：默认值里没有标志图。
    """
    assert DEFAULT_THEME == "pandan"
    assert DEFAULT_ACCENT is None
    assert HOME_BLOCKS == ("hero", "how", "categories", "featured")
    assert DEFAULT_HOME_BLOCKS == (
        ("hero", 1, True),
        ("how", 2, True),
        ("categories", 3, True),
        ("featured", 4, True),
    )
    assert FEATURED_MAX == 4


# ---------------------------------------------------------------------------
# 迁移 0016 写入的默认值与表结构
# ---------------------------------------------------------------------------


def test_migration_writes_the_default_constants(migrated: Connection) -> None:
    """默认值常量与迁移写入的相同：设置一行（槽 1、主题 pandan、主色为空、有更新时间），四个
    区块按 hero、how、categories、featured 的顺序全部显示，「未挑选」时没有精选商品。迁移用的
    主题 id、区块与默认值就是模型的常量本身（共用同一份，不是副本）。
    """
    settings = migrated.execute(
        text("SELECT singleton_slot, theme, accent, updated_at FROM store_design_settings")
    ).all()
    blocks = migrated.execute(
        text("SELECT block, position, is_visible FROM store_home_blocks ORDER BY position")
    ).all()
    featured = migrated.execute(text("SELECT COUNT(*) FROM store_featured_products")).scalar_one()

    assert len(settings) == 1
    slot, theme, accent, updated_at = settings[0]
    assert (slot, theme, accent) == (1, DEFAULT_THEME, DEFAULT_ACCENT)
    assert updated_at is not None
    assert [(block, position, bool(visible)) for block, position, visible in blocks] == list(
        DEFAULT_HOME_BLOCKS
    )
    assert featured == 0
    assert MIGRATION.THEMES is THEMES
    assert MIGRATION.HOME_BLOCKS is HOME_BLOCKS
    assert MIGRATION.DEFAULT_THEME is DEFAULT_THEME
    assert MIGRATION.DEFAULT_ACCENT is DEFAULT_ACCENT
    assert MIGRATION.DEFAULT_HOME_BLOCKS is DEFAULT_HOME_BLOCKS


def _schema(conn: Connection) -> dict[str, Any]:
    inspector = inspect(conn)
    schema: dict[str, Any] = {}
    for table in STORE_TABLES:
        schema[table] = {
            "columns": [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ],
            "checks": sorted(
                (check["name"], check["sqltext"])
                for check in inspector.get_check_constraints(table)
            ),
            "uniques": sorted(
                (unique["name"], tuple(unique["column_names"]))
                for unique in inspector.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (
                    fk["name"],
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk["options"].get("ondelete"),
                )
                for fk in inspector.get_foreign_keys(table)
            ),
        }
    return schema


def test_migration_schema_matches_models(migrated: Connection) -> None:
    """alembic check 不比较检查约束：迁移建出的三张表与按模型建出的逐列（类型、可空）、逐个
    检查约束（名字与表达式）、唯一约束与外键（含 RESTRICT）相同。downgrade 删掉三张表。
    """
    engine = create_engine("sqlite://")
    with engine.connect() as model_conn:
        Base.metadata.create_all(model_conn)
        expected = _schema(model_conn)
    engine.dispose()

    assert _schema(migrated) == expected
    assert expected["store_design_settings"]["checks"]
    assert expected["store_featured_products"]["foreign_keys"] == [
        (
            "fk_store_featured_products_product_id_products",
            ("product_id",),
            "products",
            ("id",),
            "RESTRICT",
        )
    ]

    _run_migration(migrated, "downgrade")
    remaining = set(inspect(migrated).get_table_names())
    assert remaining.isdisjoint(STORE_TABLES)

"""装修设置单例行的店铺标志五列与迁移 0025 的常量（SHOP-TASK-209）。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条（下称「第 15 条」）
「记录」一段，以及 docs/HANDOFF.md 0.84 记录的落实写法（下称「HANDOFF 0.84」：标志五列照
ProductImage 的五列加 logo_ 前缀，三个检查约束照 ProductImage 的同名约束换列名）。每条测试的
文档字符串引用它守住的那一句。

用 SQLite 内存库（打开外键检查）按模型建表，写法照 tests/test_admin_catalog_schema.py。检查约束的
反例每次只让目标约束不成立，断言报出的是该约束的名称。迁移 0025 只在 MySQL 上执行，这里不在
SQLite 上执行它，只经 importlib 载入、核对 revision 与约束常量；往返由 CI 的 backend 检查在
MySQL 8 上做。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, Table, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import StoreDesignSetting

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20261105_0025_store_logo.py"

NOW = datetime(2026, 11, 5, 2, 0)
# 已上传标志的设置行：五列全有值。
LOGO = {
    "logo_file_name": "0123456789abcdef0123456789abcdef",
    "logo_source_format": "png",
    "logo_width": 1600,
    "logo_height": 400,
    "logo_byte_size": 52_000,
}


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("store_logo_migration", MIGRATION_PATH)
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


def _setting(db: Session, **fields: object) -> StoreDesignSetting:
    values = {"singleton_slot": 1, "theme": "pandan", "accent": None, "updated_at": NOW}
    row = StoreDesignSetting(**(values | fields))
    db.add(row)
    db.flush()
    return row


def _stored_logo(db: Session, setting: StoreDesignSetting) -> tuple[object, ...]:
    stored = db.execute(
        select(
            StoreDesignSetting.logo_file_name,
            StoreDesignSetting.logo_source_format,
            StoreDesignSetting.logo_width,
            StoreDesignSetting.logo_height,
            StoreDesignSetting.logo_byte_size,
        ).where(StoreDesignSetting.id == setting.id)
    ).one()
    return tuple(stored)


def _model_checks(table: Table) -> dict[str, str]:
    """表上检查约束的完整名称（按 app/db/base.py 的命名约定）→ 文本。"""
    prefix = f"ck_{table.name}_"
    checks = {}
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            name = str(constraint.name)
            checks[name if name.startswith(prefix) else prefix + name] = str(constraint.sqltext)
    return checks


# ---- 五列全空与全有值 ----


def test_setting_without_logo_is_accepted(db: Session) -> None:
    """第 15 条「五项同为空（没有标志）」：五列全空的设置行能写入，读回全为空。"""
    setting = _setting(db)

    assert _stored_logo(db, setting) == (None, None, None, None, None)


@pytest.mark.parametrize("source_format", ["jpeg", "png", "webp"])
def test_setting_with_full_logo_is_accepted(db: Session, source_format: str) -> None:
    """第 15 条「新增标志的文件名…、上传时的格式与展示图的宽、高与字节数五项，五项…同不为空」：
    五列全有值的设置行能写入并读回，三种上传格式都收（格式同第 13 条）。
    """
    logo = LOGO | {"logo_source_format": source_format}

    setting = _setting(db, **logo)

    assert _stored_logo(db, setting) == tuple(logo.values())


# ---- logo_all_or_none ----


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param(
            ["logo_source_format", "logo_width", "logo_height", "logo_byte_size"],
            id="only-file-name",
        ),
        pytest.param(["logo_file_name"], id="no-file-name"),
        pytest.param(["logo_source_format"], id="no-source-format"),
        pytest.param(["logo_width"], id="no-width"),
        pytest.param(["logo_height"], id="no-height"),
        pytest.param(["logo_byte_size"], id="no-byte-size"),
    ],
)
def test_partial_logo_is_rejected(db: Session, missing: list[str]) -> None:
    """第 15 条「五项同为空（没有标志）或同不为空」与 HANDOFF 0.84「三个检查约束照
    ProductImage 的同名约束换列名」：只填部分列被 logo_all_or_none 拒绝。
    """
    partial = {column: value for column, value in LOGO.items() if column not in missing}

    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_store_design_settings_logo_all_or_none",
    ):
        _setting(db, **partial)


# ---- logo_source_format_valid ----


def test_logo_source_format_gif_is_rejected(db: Session) -> None:
    """第 15 条「格式…同第 13 条」（解码出的格式须为 JPEG、PNG 或 WebP）：上传时的格式 gif
    被 logo_source_format_valid 拒绝。不用大小写变体：MySQL 默认排序规则比较时不分大小写，
    那样的反例只在 SQLite 上成立。
    """
    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_store_design_settings_logo_source_format_valid",
    ):
        _setting(db, **(LOGO | {"logo_source_format": "gif"}))


# ---- logo_display_size_range ----


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"logo_width": 0}, id="width-0"),
        pytest.param({"logo_width": 1601}, id="width-1601"),
        pytest.param({"logo_height": 0}, id="height-0"),
        pytest.param({"logo_height": 1601}, id="height-1601"),
        pytest.param({"logo_byte_size": 0}, id="byte-size-0"),
    ],
)
def test_logo_display_size_out_of_range_is_rejected(db: Session, bad: dict[str, int]) -> None:
    """第 15 条「处理：同第 13 条（…长边超过 1,600 像素等比缩到 1,600…）」与 HANDOFF 0.84
    「三个检查约束照 ProductImage 的同名约束换列名」（宽高 1–1600、字节数至少 1）：
    展示图的宽或高为 0 或 1601、字节数为 0 都被 logo_display_size_range 拒绝。
    """
    with pytest.raises(
        IntegrityError,
        match="CHECK constraint failed: ck_store_design_settings_logo_display_size_range",
    ):
        _setting(db, **(LOGO | bad))


@pytest.mark.parametrize(
    "bounds",
    [
        pytest.param({"logo_width": 1, "logo_height": 1600, "logo_byte_size": 1}, id="1x1600"),
        pytest.param({"logo_width": 1600, "logo_height": 1, "logo_byte_size": 1}, id="1600x1"),
    ],
)
def test_logo_display_size_bounds_are_accepted(db: Session, bounds: dict[str, int]) -> None:
    """上一条的对照（第 15 条「长边超过 1,600 像素等比缩到 1,600」）：宽高为 1 与 1600、
    字节数为 1 都能写入并读回。单例行只有一行，两组边界分作两个用例。
    """
    logo = LOGO | bounds

    setting = _setting(db, **logo)

    assert _stored_logo(db, setting) == tuple(logo.values())


# ---- 迁移 0025 ----


def test_migration_revision_follows_0024() -> None:
    """HANDOFF 0.84「迁移 0025 照 0024」：revision 为 0025、接在 0024 之后。"""
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0025", "0024")


def test_migration_check_constraints_match_the_model() -> None:
    """第 15 条「五项同为空（没有标志）或同不为空」与 HANDOFF 0.84「三个检查约束照
    ProductImage 的同名约束换列名」：迁移 0025 建的三个检查约束与模型的同名约束逐字一致
    （alembic check 不比较检查约束），加的五列与模型的标志列相同。
    """
    models = _model_checks(StoreDesignSetting.__table__)

    assert [name for name, _condition in MIGRATION.CHECK_CONSTRAINTS] == [
        "ck_store_design_settings_logo_all_or_none",
        "ck_store_design_settings_logo_source_format_valid",
        "ck_store_design_settings_logo_display_size_range",
    ]
    for name, condition in MIGRATION.CHECK_CONSTRAINTS:
        assert models[name] == condition, name
    assert MIGRATION.LOGO_COLUMNS == tuple(LOGO)

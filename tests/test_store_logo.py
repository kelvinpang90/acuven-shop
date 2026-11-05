"""店铺标志图的读取、替换与移除（app/services/store_logo.py，SHOP-TASK-211）。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条（下称「第 15 条」）
与 docs/HANDOFF.md 0.84。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool）并写入装修设置单例行。ProcessedImage 直接构造，不经
Pillow；app.services.image_store 的 put_image 与 delete_image 以 monkeypatch 替换并记录调用与
prefix，不访问 R2。SQLite 不生成行锁：锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from sqlalchemy import Engine, Select, create_engine, delete, event, select, update
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import AdminAccount, AuditEvent, StoreDesignSetting
from app.models.store_design import HOME_BLOCKS, SINGLETON_SLOT
from app.services import image_store
from app.services.image_processing import ProcessedImage
from app.services.image_store import LOGO_KEY_PREFIX, ImageStoreUnavailable
from app.services.store_design import (
    ADMIN_STORE_DESIGN_SAVED,
    AUDIT_TARGET_STORE_DESIGN,
    HomeBlock,
    read_admin_store_design,
    read_store_design,
    save_store_design,
)
from app.services.store_logo import (
    ADMIN_STORE_LOGO_REMOVED,
    ADMIN_STORE_LOGO_UPLOADED,
    current_logo_file_name,
    logo_path,
    remove_logo,
    replace_logo,
)

OLD = datetime(2026, 11, 1, 9, 0)
NOW = datetime(2026, 11, 5, 3, 0)
SETTINGS = Settings(_env_file=None)
IMAGE = ProcessedImage(data=b"RIFF....WEBPVP8 logo", source_format="png", width=800, height=200)
OLD_NAME = "0123456789abcdef0123456789abcdef"
OLD_LOGO = (OLD_NAME, "jpeg", 640, 160, 1234)
NO_LOGO = (None, None, None, None, None)
FILE_NAME = re.compile(r"[0-9a-f]{32}")
LOGO_ACTIONS = (ADMIN_STORE_LOGO_UPLOADED, ADMIN_STORE_LOGO_REMOVED)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(
            StoreDesignSetting(
                singleton_slot=SINGLETON_SLOT, theme="pandan", accent=None, updated_at=OLD
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
def setting_id(db: Session) -> int:
    return db.scalars(select(StoreDesignSetting.id)).one()


class FakeStore:
    """替换 image_store.put_image 与 delete_image，按顺序记录调用与 prefix。

    prefix 只限关键字且没有默认值：漏传即 TypeError。
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_put = False
        self.on_delete: Callable[[str], None] | None = None

    def put_image(self, settings: Settings, file_name: str, data: bytes, *, prefix: str) -> None:
        self.calls.append(("put", file_name, data, prefix))
        if self.fail_put:
            raise ImageStoreUnavailable

    def delete_image(self, settings: Settings, file_name: str, *, prefix: str) -> None:
        self.calls.append(("delete", file_name, prefix))
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


def _with_logo(db: Session) -> None:
    name, source_format, width, height, byte_size = OLD_LOGO
    db.execute(
        update(StoreDesignSetting).values(
            logo_file_name=name,
            logo_source_format=source_format,
            logo_width=width,
            logo_height=height,
            logo_byte_size=byte_size,
        )
    )
    db.commit()


def _logo_columns(db: Session) -> tuple:
    """单例行的标志五列与更新时间。"""
    stmt = select(
        StoreDesignSetting.logo_file_name,
        StoreDesignSetting.logo_source_format,
        StoreDesignSetting.logo_width,
        StoreDesignSetting.logo_height,
        StoreDesignSetting.logo_byte_size,
        StoreDesignSetting.updated_at,
    )
    return tuple(db.execute(stmt).one())


def _audits(db: Session, actions: tuple[str, ...] = LOGO_ACTIONS) -> list[tuple]:
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
        .where(AuditEvent.action.in_(actions))
        .order_by(AuditEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _current_name(db: Session) -> str | None:
    return db.scalar(select(StoreDesignSetting.logo_file_name))


def _all_visible() -> list[HomeBlock]:
    return [HomeBlock(block=block, visible=True) for block in HOME_BLOCKS]


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


def test_logo_path_and_current_file_name_only_select(db: Session, engine: Engine) -> None:
    """第 15 条「标志经 GET /api/store-logos/<文件名>.webp 提供」：提供路径按文件名拼成；当前
    文件名取自单例行，没有标志或没有单例行时为空，读取只发 SELECT。"""
    assert logo_path(OLD_NAME) == f"/api/store-logos/{OLD_NAME}.webp"
    assert current_logo_file_name(db) is None

    _with_logo(db)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    assert current_logo_file_name(db) == OLD_NAME
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

    event.remove(engine, "before_cursor_execute", _record)
    db.execute(delete(StoreDesignSetting))
    db.commit()
    assert current_logo_file_name(db) is None


def test_reads_carry_the_logo_path(db: Session) -> None:
    """第 15 条「公开的 GET /api/store-design 与后台的装修读取都新增 logo 字段：有标志时为上面的
    提供路径，没有时为 null」：没有标志、有标志与设置行不存在三种情形。"""
    assert read_store_design(db).logo is None
    assert read_admin_store_design(db, "en").logo is None

    _with_logo(db)
    assert read_store_design(db).logo == f"/api/store-logos/{OLD_NAME}.webp"
    assert read_admin_store_design(db, "en").logo == f"/api/store-logos/{OLD_NAME}.webp"

    db.execute(delete(StoreDesignSetting))
    db.commit()
    assert read_store_design(db).logo is None
    assert read_admin_store_design(db, "en").logo is None


# ---- 替换 ----


def test_replace_without_logo_writes_columns_and_audit(
    db: Session, admin_id: int, setting_id: int, store: FakeStore
) -> None:
    """第 15 条「展示图存为存储桶里键为 store-logos/<文件名>.webp 的对象」「改写五项与更新时间、
    写审计并提交」「上传的旧值为原有文件名（原来没有标志时为空）、新值为新文件名」：原来没有标志
    时写入五列，审计旧值为空，不删任何对象。"""
    path = replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)

    [name] = store.puts
    assert FILE_NAME.fullmatch(name)
    assert path == f"/api/store-logos/{name}.webp"
    assert store.calls == [("put", name, IMAGE.data, LOGO_KEY_PREFIX)]
    assert _logo_columns(db) == (name, "png", 800, 200, len(IMAGE.data), NOW)
    assert _audits(db) == [
        (
            ADMIN_STORE_LOGO_UPLOADED,
            AUDIT_TARGET_STORE_DESIGN,
            setting_id,
            None,
            name,
            admin_id,
            NOW,
        )
    ]


def test_replace_with_logo_deletes_old_object_after_commit(
    db: Session, admin_id: int, setting_id: int, store: FakeStore
) -> None:
    """第 15 条「提交成功后，原来有标志的，删除原有对象（只尝试 1 次；失败只记日志，留下的孤立
    对象不再被提供）」：删除原有对象时单例行已提交为新文件名；审计旧值为原有文件名。"""
    _with_logo(db)
    seen_in_delete: list[str | None] = []
    store.on_delete = lambda _name: seen_in_delete.append(_current_name(db))

    path = replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)

    [name] = store.puts
    assert name != OLD_NAME
    assert path == logo_path(name)
    assert store.calls == [
        ("put", name, IMAGE.data, LOGO_KEY_PREFIX),
        ("delete", OLD_NAME, LOGO_KEY_PREFIX),
    ]
    assert seen_in_delete == [name]
    assert _logo_columns(db) == (name, "png", 800, 200, len(IMAGE.data), NOW)
    assert _audits(db) == [
        (
            ADMIN_STORE_LOGO_UPLOADED,
            AUDIT_TARGET_STORE_DESIGN,
            setting_id,
            OLD_NAME,
            name,
            admin_id,
            NOW,
        )
    ]


def test_replace_put_failure_keeps_the_record(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 15 条「再在任何数据库事务之外写入新对象，写入失败或超时即回答『图片存储不可用』、记录
    不变」：写入失败抛 ImageStoreUnavailable，五列与更新时间不变，不写审计，不删除任何对象。"""
    _with_logo(db)
    store.fail_put = True

    with pytest.raises(ImageStoreUnavailable):
        replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)

    [name] = store.puts
    assert store.calls == [("put", name, IMAGE.data, LOGO_KEY_PREFIX)]
    assert _logo_columns(db) == (*OLD_LOGO, OLD)
    assert _audits(db) == []


def test_replace_commit_failure_deletes_new_object(
    db: Session, admin_id: int, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 15 条「提交失败即删除新对象（只尝试 1 次）」：记录与审计都不变，原有对象不删除。"""
    _with_logo(db)

    def _fail() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", _fail)

    with pytest.raises(RuntimeError):
        replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)

    [name] = store.puts
    assert store.calls == [
        ("put", name, IMAGE.data, LOGO_KEY_PREFIX),
        ("delete", name, LOGO_KEY_PREFIX),
    ]
    assert _logo_columns(db) == (*OLD_LOGO, OLD)
    assert _audits(db) == []


# ---- 移除 ----


def test_remove_clears_columns_and_deletes_after_commit(
    db: Session, admin_id: int, setting_id: int, store: FakeStore
) -> None:
    """第 15 条「有标志时五项清空、改更新时间、写审计并提交，提交后删除对象（只尝试 1 次，失败
    只记日志）」「移除的旧值为原有文件名、新值为空」：删除对象时清空已提交。"""
    _with_logo(db)
    seen_in_delete: list[str | None] = []
    store.on_delete = lambda _name: seen_in_delete.append(_current_name(db))

    assert remove_logo(db, SETTINGS, admin_id, NOW) is None

    assert store.calls == [("delete", OLD_NAME, LOGO_KEY_PREFIX)]
    assert seen_in_delete == [None]
    assert _logo_columns(db) == (*NO_LOGO, NOW)
    assert _audits(db) == [
        (
            ADMIN_STORE_LOGO_REMOVED,
            AUDIT_TARGET_STORE_DESIGN,
            setting_id,
            OLD_NAME,
            None,
            admin_id,
            NOW,
        )
    ]
    assert read_store_design(db).logo is None


def test_remove_without_logo_changes_nothing(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 15 条「没有标志时回滚，照样回答成功，不写审计」：正常返回，不调用存储，五列与更新时间
    不变。"""
    assert remove_logo(db, SETTINGS, admin_id, NOW) is None

    assert store.calls == []
    assert _logo_columns(db) == (*NO_LOGO, OLD)
    assert _audits(db) == []


def test_remove_commit_failure_keeps_the_logo(
    db: Session, admin_id: int, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 15 条「有标志时五项清空、改更新时间、写审计并提交，提交后删除对象」：提交失败时回滚，
    标志与审计都不变，也不删除对象。"""
    _with_logo(db)

    def _fail() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", _fail)

    with pytest.raises(RuntimeError):
        remove_logo(db, SETTINGS, admin_id, NOW)

    assert store.calls == []
    assert _logo_columns(db) == (*OLD_LOGO, OLD)
    assert _audits(db) == []


# ---- 与 A08 的保存 ----


def test_save_store_design_keeps_the_logo(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 15 条「A08 的保存不读写标志，标志的上传与移除也不改 A08 保存的其余设置」：替换与移除
    之后保存装修，标志五列不变、返回的设置带当前标志，保存的审计值不含文件名。"""
    path = replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)
    [name] = store.puts
    later = datetime(2026, 11, 6, 8, 0)

    design, changed = save_store_design(
        db, admin_id, "batik", "sogan", _all_visible(), [], "en", later
    )

    assert changed is True
    assert design.logo == path
    assert _logo_columns(db) == (name, "png", 800, 200, len(IMAGE.data), later)
    assert read_store_design(db).logo == path
    design, changed = save_store_design(
        db, admin_id, "batik", "sogan", _all_visible(), [], "en", later
    )
    assert (changed, design.logo) == (False, path)

    remove_logo(db, SETTINGS, admin_id, NOW)
    design, changed = save_store_design(
        db, admin_id, "litar", None, _all_visible(), [], "en", later
    )

    assert changed is True
    assert design.logo is None
    assert _logo_columns(db) == (*NO_LOGO, later)
    saved = _audits(db, (ADMIN_STORE_DESIGN_SAVED,))
    assert len(saved) == 2
    for _action, _type, _id, old_value, new_value, _admin, _at in saved:
        assert name not in old_value
        assert name not in new_value


# ---- 锁 ----


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_changes_lock_setting_first_after_rollback(
    db: Session, engine: Engine, admin_id: int, store: FakeStore, operation: str
) -> None:
    """第 15 条「写入成功后在一个事务里锁定装修设置的单例行（锁法同 A08 的保存：锁定是新事务的
    第一条语句）」与「移除」的「锁定单例行」：回滚之后的第一条语句是按 MySQL 方言编译带
    FOR UPDATE 的单例行锁定查询，之后不再加锁。"""
    _with_logo(db)
    current_logo_file_name(db)
    seen = _record_sequence(engine)

    if operation == "replace":
        replace_logo(db, SETTINGS, admin_id, IMAGE, NOW)
    else:
        remove_logo(db, SETTINGS, admin_id, NOW)

    assert seen[0] == "ROLLBACK"
    lock = seen[1]
    assert "FROM store_design_settings" in lock
    assert lock.rstrip().endswith("FOR UPDATE")
    assert [sql for sql in seen if "FOR UPDATE" in sql or "SHARE" in sql] == [lock]

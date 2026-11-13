"""节日促销海报图的处理、替换与移除（SHOP-TASK-240）。

覆盖 app/services/promotion_poster.py 与 process_image 的 min_width。依据
docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 12 条（下称「第 12 条」）
与第 2 条、「计价、优惠、积分与库存」第 15 条（下称「第 15 条」），docs/HANDOFF.md
0.93 确认的一点（下称「HANDOFF 0.93」），以及 SHOP-TASK-240 的落实写法（下称
「落实写法」）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库（StaticPool，打开外键检查）按模型建表，写入发布锁那一行与管理员；
活动以 create_promotion 建成草稿，已发布的直接改状态列（发布由 SHOP-TASK-241 提供）。
ProcessedImage 直接构造；app.services.image_store 的 put_image 与 delete_image 以
monkeypatch 替换并记录调用与 prefix，不访问 R2。process_image 的测试用 Pillow 在
内存里生成图片。
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from PIL import Image
from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import (
    AdminAccount,
    AuditEvent,
    Promotion,
    PromotionDecoration,
    PromotionPublishLock,
)
from app.services import image_store
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionEnded,
    PromotionInput,
    PromotionNotFound,
    RuleInput,
    create_promotion,
    read_promotion,
)
from app.services.image_processing import ImageRejected, ProcessedImage, process_image
from app.services.image_store import POSTER_KEY_PREFIX, ImageStoreUnavailable
from app.services.promotion_poster import (
    ADMIN_PROMOTION_POSTER_REMOVED,
    ADMIN_PROMOTION_POSTER_UPLOADED,
    POSTER_DESKTOP_MIN_WIDTH,
    POSTER_MIN_WIDTHS,
    POSTER_PHONE_MIN_WIDTH,
    PosterRequired,
    poster_file_names,
    poster_path,
    remove_poster,
    replace_poster,
)

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。活动以 CREATED 建成，海报列以 OLD 写入。
NOW = datetime(2026, 11, 13, 4, 0)
CREATED = datetime(2026, 11, 9, 2, 0)
OLD = datetime(2026, 11, 9, 3, 0)
SETTINGS = Settings(_env_file=None)
IMAGE = ProcessedImage(
    data=b"RIFF....WEBPVP8 poster",
    source_format="jpeg",
    width=1600,
    height=533,
)
NEW_COLUMNS = ("jpeg", 1600, 533, len(IMAGE.data))
OLD_NAME = "0123456789abcdef0123456789abcdef"
OTHER_NAME = "fedcba9876543210fedcba9876543210"
OLD_POSTER = (OLD_NAME, "png", 1600, 600, 4321)
OTHER_POSTER = (OTHER_NAME, "png", 600, 900, 77)
NO_POSTER = (None, None, None, None, None)
FILE_NAME = re.compile(r"[0-9a-f]{32}")
POSTER_ACTIONS = (ADMIN_PROMOTION_POSTER_UPLOADED, ADMIN_PROMOTION_POSTER_REMOVED)
COLUMN_SUFFIXES = ("file_name", "source_format", "width", "height", "byte_size")

# 马来西亚本地时间的活动时段：相对 NOW 未开始（草稿同）、进行中、已结束。
WINDOWS = {
    "draft": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "scheduled": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "running": ("2026-11-10T00:00", "2026-11-20T00:00"),
    "ended": ("2026-11-01T00:00", "2026-11-05T00:00"),
}


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(PromotionPublishLock(singleton_slot=1))
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


def _make(db: Session, admin_id: int, state: str, poster_mode: str = "template") -> int:
    """按 WINDOWS 建一个处于 state 的活动（非草稿的直接改为已发布），返回活动 ID。"""
    starts_at, ends_at = WINDOWS[state]
    data = PromotionInput(
        name="Raya",
        starts_at=starts_at,
        ends_at=ends_at,
        coupon_combinable=False,
        points_combinable=False,
        rules=(RuleInput(kind="free_shipping", threshold_sen=0),),
        decoration=DecorationInput(
            pack="raya",
            palette="theme",
            ambient="none",
            home_poster="split",
            list_strip="band",
            card_badge="tag",
            detail_ribbon="ribbon",
            cart_progress="bar",
            poster_mode=poster_mode,
        ),
        copy=CopyInput(title_en="Raya Sale", poster_alt_en="Raya poster"),
    )
    promotion_id = create_promotion(db, admin_id, data, CREATED).id
    if state != "draft":
        db.execute(
            update(Promotion)
            .where(Promotion.id == promotion_id)
            .values(status="published", updated_at=CREATED)
        )
        db.commit()
    assert read_promotion(db, promotion_id, NOW).state == state
    return promotion_id


def _set_poster(db: Session, promotion_id: int, slot: str, values: tuple) -> None:
    """直接写装饰行 slot 位置的五列，更新时间写为 OLD（模型带 onupdate，不写明会被盖掉）。"""
    columns = {
        f"poster_{slot}_{suffix}": value
        for suffix, value in zip(COLUMN_SUFFIXES, values, strict=True)
    }
    db.execute(
        update(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == promotion_id)
        .values(**columns, updated_at=OLD)
    )
    db.commit()


def _poster_columns(db: Session, promotion_id: int, slot: str) -> tuple:
    columns = [getattr(PromotionDecoration, f"poster_{slot}_{s}") for s in COLUMN_SUFFIXES]
    stmt = select(*columns).where(PromotionDecoration.promotion_id == promotion_id)
    return tuple(db.execute(stmt).one())


def _decoration_updated_at(db: Session, promotion_id: int) -> datetime:
    stmt = select(PromotionDecoration.updated_at).where(
        PromotionDecoration.promotion_id == promotion_id
    )
    return db.scalar(stmt)


def _version_and_updated_at(db: Session, promotion_id: int) -> tuple:
    stmt = select(Promotion.version, Promotion.updated_at).where(Promotion.id == promotion_id)
    return tuple(db.execute(stmt).one())


def _audits(db: Session) -> list[tuple]:
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
        .where(AuditEvent.action.in_(POSTER_ACTIONS))
        .order_by(AuditEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _image_bytes(
    width: int, height: int, image_format: str = "PNG", orientation: int | None = None
) -> bytes:
    """一张纯色图；orientation 不为空时写入 EXIF 方向标签。"""
    buffer = io.BytesIO()
    options: dict[str, object] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        options["exif"] = exif.tobytes()
    Image.new("RGB", (width, height), (200, 80, 40)).save(buffer, format=image_format, **options)
    return buffer.getvalue()


def _size(image: ProcessedImage) -> tuple[int, int]:
    return image.width, image.height


# ---- 处理：宽度下限 ----


@pytest.mark.parametrize(
    ("slot", "width", "height", "accepted"),
    [
        ("desktop", 1199, 400, False),
        ("desktop", 1200, 400, True),
        ("phone", 599, 800, False),
        ("phone", 600, 800, True),
    ],
)
def test_poster_width_minimum(slot: str, width: int, height: int, accepted: bool) -> None:
    """第 12 条「像素尺寸为桌面图宽至少 1,200、手机图宽至少 600」：按位置的下限调用
    process_image，比下限窄 1 像素即 ImageRejected，正好等于下限即通过。"""
    assert POSTER_MIN_WIDTHS == {
        "desktop": POSTER_DESKTOP_MIN_WIDTH,
        "phone": POSTER_PHONE_MIN_WIDTH,
    }
    assert (POSTER_DESKTOP_MIN_WIDTH, POSTER_PHONE_MIN_WIDTH) == (1200, 600)
    data = _image_bytes(width, height)

    if accepted:
        assert _size(process_image(data, min_width=POSTER_MIN_WIDTHS[slot])) == (width, height)
    else:
        with pytest.raises(ImageRejected):
            process_image(data, min_width=POSTER_MIN_WIDTHS[slot])


def test_width_minimum_uses_display_orientation() -> None:
    """落实写法「按 EXIF 方向转正之后的宽（即显示方向的宽）小于 min_width 即
    ImageRejected」：存成 400×1300、EXIF 方向 6 的图显示为 1300 宽，通过桌面下限；
    存成 1300×400、方向 6 的图显示为 400 宽，被拒。"""
    upright = _image_bytes(400, 1300, "JPEG", orientation=6)
    assert _size(process_image(upright, min_width=POSTER_DESKTOP_MIN_WIDTH)) == (1300, 400)

    sideways = _image_bytes(1300, 400, "JPEG", orientation=6)
    with pytest.raises(ImageRejected):
        process_image(sideways, min_width=POSTER_DESKTOP_MIN_WIDTH)


def test_width_minimum_checks_the_original_before_downscaling() -> None:
    """落实写法「宽度下限按上传的原图判断……之后照常把长边缩到 1,600，展示图的宽
    可能小于下限，接受」：1,200×2,400 的图通过桌面下限，展示图缩为 800×1,600。"""
    data = _image_bytes(1200, 2400)
    assert _size(process_image(data, min_width=POSTER_DESKTOP_MIN_WIDTH)) == (800, 1600)


def test_process_image_without_min_width_is_unchanged() -> None:
    """验收「现有调用不传，结果不变」与落实写法「min_width（默认 0，即不另限宽度）」：
    不传与传 0 的结果相同；200 宽的图照样通过、199 宽的照样被拒；EXIF 转正后变窄
    的图不另受宽度限制。"""
    narrow = _image_bytes(200, 300)
    assert process_image(narrow) == process_image(narrow, min_width=0)
    assert _size(process_image(narrow)) == (200, 300)

    rotated = _image_bytes(1300, 400, "JPEG", orientation=6)
    assert _size(process_image(rotated)) == (400, 1300)

    with pytest.raises(ImageRejected):
        process_image(_image_bytes(199, 300))


# ---- 路径与被引用的文件名 ----


def test_poster_path_and_referenced_file_names(db: Session, admin_id: int) -> None:
    """落实写法「poster_path(file_name) 返回 /api/promotion-posters/ 加文件名加
    .webp；poster_file_names(db) 返回所有活动装饰行引用的海报文件名集合」：草稿与
    已结束活动的桌面、手机图都算在内，没有图时为空集合。"""
    assert poster_path(OLD_NAME) == f"/api/promotion-posters/{OLD_NAME}.webp"
    draft = _make(db, admin_id, "draft")
    assert poster_file_names(db) == set()

    ended = _make(db, admin_id, "ended")
    _set_poster(db, draft, "desktop", OLD_POSTER)
    _set_poster(db, ended, "phone", OTHER_POSTER)

    assert poster_file_names(db) == {OLD_NAME, OTHER_NAME}


# ---- 替换 ----


def test_replace_without_poster_writes_slot_and_audit(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """第 12 条「上传即替换该位置的图」「另用自己的存储键前缀」与第 15 条「改写五项
    与更新时间、写审计并提交」：原来没有图时写入该位置五列与装饰行的更新时间，另一
    位置不变，对象写在 promotion-posters/ 下，不删任何对象；审计旧值只写 slot，
    新值为「slot 文件名」。"""
    promotion_id = _make(db, admin_id, "draft")

    path = replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)

    [name] = store.puts
    assert FILE_NAME.fullmatch(name)
    assert path == f"/api/promotion-posters/{name}.webp"
    assert POSTER_KEY_PREFIX == "promotion-posters/"
    assert store.calls == [("put", name, IMAGE.data, POSTER_KEY_PREFIX)]
    assert _poster_columns(db, promotion_id, "desktop") == (name, *NEW_COLUMNS)
    assert _poster_columns(db, promotion_id, "phone") == NO_POSTER
    assert _decoration_updated_at(db, promotion_id) == NOW
    assert _audits(db) == [
        (
            ADMIN_PROMOTION_POSTER_UPLOADED,
            "promotion",
            promotion_id,
            "desktop",
            f"desktop {name}",
            admin_id,
            NOW,
        )
    ]
    assert read_promotion(db, promotion_id, NOW).poster_desktop_url == path


def test_replace_with_poster_deletes_old_object_after_commit(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """第 15 条「提交成功后，原来有标志的，删除原有对象（只尝试 1 次；失败只记日志）」
    与落实写法「替换与移除后删除旧对象」：删除原有对象时装饰行已提交为新文件名，另一
    位置不变；审计旧值为「slot 原有文件名」。"""
    promotion_id = _make(db, admin_id, "running", poster_mode="image")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)
    _set_poster(db, promotion_id, "phone", OTHER_POSTER)
    seen_in_delete: list[tuple] = []

    def _on_delete(_name: str) -> None:
        seen_in_delete.append(_poster_columns(db, promotion_id, "phone"))

    store.on_delete = _on_delete

    path = replace_poster(db, SETTINGS, admin_id, promotion_id, "phone", IMAGE, NOW)

    [name] = store.puts
    assert path == poster_path(name)
    assert store.calls == [
        ("put", name, IMAGE.data, POSTER_KEY_PREFIX),
        ("delete", OTHER_NAME, POSTER_KEY_PREFIX),
    ]
    assert seen_in_delete == [(name, *NEW_COLUMNS)]
    assert _poster_columns(db, promotion_id, "desktop") == OLD_POSTER
    assert _audits(db) == [
        (
            ADMIN_PROMOTION_POSTER_UPLOADED,
            "promotion",
            promotion_id,
            f"phone {OTHER_NAME}",
            f"phone {name}",
            admin_id,
            NOW,
        )
    ]


def test_replace_put_failure_keeps_the_record(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 15 条「再在任何数据库事务之外写入新对象，写入失败或超时即回答『图片存储
    不可用』、记录不变」：写入失败抛 ImageStoreUnavailable，五列与更新时间不变，
    不写审计，不删除任何对象。"""
    promotion_id = _make(db, admin_id, "draft")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)
    store.fail_put = True

    with pytest.raises(ImageStoreUnavailable):
        replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)

    [name] = store.puts
    assert store.calls == [("put", name, IMAGE.data, POSTER_KEY_PREFIX)]
    assert _poster_columns(db, promotion_id, "desktop") == OLD_POSTER
    assert _decoration_updated_at(db, promotion_id) == OLD
    assert _audits(db) == []


def test_replace_commit_failure_deletes_new_object(
    db: Session, admin_id: int, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 15 条「提交失败即删除新对象（只尝试 1 次）」：记录与审计都不变，原有对象
    不删除。"""
    promotion_id = _make(db, admin_id, "draft")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)

    def _fail() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", _fail)

    with pytest.raises(RuntimeError):
        replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)

    [name] = store.puts
    assert store.calls == [
        ("put", name, IMAGE.data, POSTER_KEY_PREFIX),
        ("delete", name, POSTER_KEY_PREFIX),
    ]
    assert _poster_columns(db, promotion_id, "desktop") == OLD_POSTER
    assert _audits(db) == []


# ---- 移除 ----


def test_remove_clears_slot_and_deletes_after_commit(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """第 15 条「有标志时五项清空、改更新时间、写审计并提交，提交后删除对象」与落实
    写法「旧值与新值为『slot 文件名』，没有图的一侧只写 slot」：删除对象时清空已
    提交，另一位置不变。"""
    promotion_id = _make(db, admin_id, "scheduled")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)
    _set_poster(db, promotion_id, "phone", OTHER_POSTER)
    seen_in_delete: list[tuple] = []

    def _on_delete(_name: str) -> None:
        seen_in_delete.append(_poster_columns(db, promotion_id, "desktop"))

    store.on_delete = _on_delete

    assert remove_poster(db, SETTINGS, admin_id, promotion_id, "desktop", NOW) is None

    assert store.calls == [("delete", OLD_NAME, POSTER_KEY_PREFIX)]
    assert seen_in_delete == [NO_POSTER]
    assert _poster_columns(db, promotion_id, "desktop") == NO_POSTER
    assert _poster_columns(db, promotion_id, "phone") == OTHER_POSTER
    assert _decoration_updated_at(db, promotion_id) == NOW
    assert _audits(db) == [
        (
            ADMIN_PROMOTION_POSTER_REMOVED,
            "promotion",
            promotion_id,
            f"desktop {OLD_NAME}",
            "desktop",
            admin_id,
            NOW,
        )
    ]
    assert read_promotion(db, promotion_id, NOW).poster_desktop_url is None


def test_remove_without_poster_changes_nothing(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """第 15 条「没有标志时回滚，照样回答成功，不写审计」与验收「remove_poster 照
    remove_logo（没有图时照样成功、不写审计）」：正常返回，不调用存储，五列与更新
    时间不变。"""
    promotion_id = _make(db, admin_id, "draft")

    assert remove_poster(db, SETTINGS, admin_id, promotion_id, "phone", NOW) is None

    assert store.calls == []
    assert _poster_columns(db, promotion_id, "phone") == NO_POSTER
    assert _decoration_updated_at(db, promotion_id) == CREATED
    assert _audits(db) == []


# ---- 不改活动的版本号 ----


def test_upload_and_remove_keep_promotion_version(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """HANDOFF 0.93「活动海报图的上传与替换立即生效、不随保存提交、不改活动的版本号」
    与落实写法「不改活动的 version 与 updated_at」：上传、替换与移除之后活动的版本号
    与修改时间不变，装饰行的更新时间取 now，每次各写一条审计。"""
    promotion_id = _make(db, admin_id, "draft")
    before = _version_and_updated_at(db, promotion_id)
    assert before == (1, CREATED)

    replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)
    replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)
    remove_poster(db, SETTINGS, admin_id, promotion_id, "desktop", NOW)

    assert _version_and_updated_at(db, promotion_id) == before
    assert read_promotion(db, promotion_id, NOW).version == 1
    assert _decoration_updated_at(db, promotion_id) == NOW
    assert [row[0] for row in _audits(db)] == [
        ADMIN_PROMOTION_POSTER_UPLOADED,
        ADMIN_PROMOTION_POSTER_UPLOADED,
        ADMIN_PROMOTION_POSTER_REMOVED,
    ]


# ---- 状态与异常 ----


def test_missing_promotion_is_not_found(db: Session, admin_id: int, store: FakeStore) -> None:
    """验收「活动不存在抛 249 的 PromotionNotFound」与第 15 条「提交失败即删除新对象」
    的同一处理：替换时已写入的新对象被删除；移除不调用存储；都不写审计。"""
    with pytest.raises(PromotionNotFound):
        replace_poster(db, SETTINGS, admin_id, 999, "desktop", IMAGE, NOW)
    [name] = store.puts
    assert store.calls == [
        ("put", name, IMAGE.data, POSTER_KEY_PREFIX),
        ("delete", name, POSTER_KEY_PREFIX),
    ]

    store.calls.clear()
    with pytest.raises(PromotionNotFound):
        remove_poster(db, SETTINGS, admin_id, 999, "desktop", NOW)
    assert store.calls == []
    assert _audits(db) == []


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_ended_promotion_is_rejected(
    db: Session, admin_id: int, store: FakeStore, operation: str
) -> None:
    """落实写法「上传与移除只对存在且不是 ended 的活动」：已结束的活动抛
    PromotionEnded，五列与审计不变；替换写入的新对象被删除，原有对象不删除。"""
    promotion_id = _make(db, admin_id, "ended")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)

    with pytest.raises(PromotionEnded):
        if operation == "replace":
            replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)
        else:
            remove_poster(db, SETTINGS, admin_id, promotion_id, "desktop", NOW)

    if operation == "replace":
        [name] = store.puts
        assert store.calls == [
            ("put", name, IMAGE.data, POSTER_KEY_PREFIX),
            ("delete", name, POSTER_KEY_PREFIX),
        ]
    else:
        assert store.calls == []
    assert _poster_columns(db, promotion_id, "desktop") == OLD_POSTER
    assert _audits(db) == []


@pytest.mark.parametrize("state", ["scheduled", "running"])
def test_published_image_mode_cannot_remove(
    db: Session, admin_id: int, store: FakeStore, state: str
) -> None:
    """落实写法「已发布（scheduled 或 running）且海报为上传图模式的活动不能移除图
    （第 2 条的发布条件要求两张图都有）」：两个位置的移除都抛 PosterRequired，五列、
    审计与存储都不变；替换照常可以。"""
    promotion_id = _make(db, admin_id, state, poster_mode="image")
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)
    _set_poster(db, promotion_id, "phone", OTHER_POSTER)

    for slot in ("desktop", "phone"):
        with pytest.raises(PosterRequired):
            remove_poster(db, SETTINGS, admin_id, promotion_id, slot, NOW)
    assert store.calls == []
    assert _poster_columns(db, promotion_id, "desktop") == OLD_POSTER
    assert _poster_columns(db, promotion_id, "phone") == OTHER_POSTER
    assert _audits(db) == []

    replace_poster(db, SETTINGS, admin_id, promotion_id, "desktop", IMAGE, NOW)
    assert [row[0] for row in _audits(db)] == [ADMIN_PROMOTION_POSTER_UPLOADED]


@pytest.mark.parametrize(("state", "poster_mode"), [("draft", "image"), ("running", "template")])
def test_draft_or_template_mode_can_remove(
    db: Session, admin_id: int, store: FakeStore, state: str, poster_mode: str
) -> None:
    """落实写法「已发布……且海报为上传图模式的活动不能移除图」只限这一种情形：上传图
    模式的草稿与模板模式的进行中活动都可以移除，审计照常写。"""
    promotion_id = _make(db, admin_id, state, poster_mode=poster_mode)
    _set_poster(db, promotion_id, "desktop", OLD_POSTER)

    remove_poster(db, SETTINGS, admin_id, promotion_id, "desktop", NOW)

    assert store.calls == [("delete", OLD_NAME, POSTER_KEY_PREFIX)]
    assert _poster_columns(db, promotion_id, "desktop") == NO_POSTER
    assert [row[0] for row in _audits(db)] == [ADMIN_PROMOTION_POSTER_REMOVED]


def test_unknown_slot_is_value_error(db: Session, admin_id: int, store: FakeStore) -> None:
    """落实写法「位置 slot 为 desktop 或 phone」：其他取值抛 ValueError，不调用存储、
    不写审计。"""
    promotion_id = _make(db, admin_id, "draft")

    with pytest.raises(ValueError):
        replace_poster(db, SETTINGS, admin_id, promotion_id, "tablet", IMAGE, NOW)
    with pytest.raises(ValueError):
        remove_poster(db, SETTINGS, admin_id, promotion_id, "Desktop", NOW)

    assert store.calls == []
    assert _audits(db) == []

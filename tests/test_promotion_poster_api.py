"""节日促销海报图的后台上传与移除接口 PUT 与 DELETE
/api/admin/promotions/{promotion_id}/poster/{slot}（app/api/admin_promotion_poster.py），以及公开
提供接口 GET /api/promotion-posters/{name}（app/api/promotion_posters.py），SHOP-TASK-250；规则见
SHOP-TASK-240 的 app/services/promotion_poster.py。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 12 条（下称「第 12 条」）与
「计价、优惠、积分与库存」第 13、15 条（下称「第 13 条」「第 15 条」），以及 SHOP-TASK-250 的
落实写法与验收（下称「落实写法」「验收」）。每条测试（参数化的测试是每个用例）的文档字符串写明
它守住的原句。

夹具照 tests/test_admin_store_logo_api.py 与 tests/test_store_logos_api.py：SQLite 内存库按模型
建表（StaticPool、关闭 check_same_thread、开外键），写入发布锁那一行与管理员；https 的 base_url，
以 issue_admin_session 登录，CSRF 令牌在这里另算。活动以 create_promotion 建成草稿，已发布的直接
改状态列（同 tests/test_promotion_poster.py）。Settings 以 R2_ENDPOINT_URL 等四个原名构造为已配置；
未配置的用例不传。时间固定为 NOW（monkeypatch 本接口模块与 app.api.admin_auth 的 _utcnow）。
app.services.image_store 的 put_image、get_image 与 delete_image 一律以 monkeypatch 替换为替身并
记录调用与 prefix，不访问 R2。读请求体的依赖 _image_body 经 dependency_overrides 包一层计数，用来
判断请求体是否被读。上传的样本用 Pillow 现场生成 PNG。检查结果在另一个数据库会话里读出，读得到
即说明接口已提交。
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_promotion_poster as poster_api
from app.api.admin_product_images import _image_body
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Promotion,
    PromotionDecoration,
    PromotionPublishLock,
)
from app.services import image_store
from app.services.admin_auth import issue_admin_session
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionInput,
    RuleInput,
    create_promotion,
    read_promotion,
)
from app.services.image_processing import ProcessedImage
from app.services.image_store import ImageNotFound, ImageStoreUnavailable
from app.services.promotion_poster import remove_poster, replace_poster

ADMIN_URL = "/api/admin/promotions"
PUBLIC_URL = "/api/promotion-posters"

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。活动以 CREATED 建成。
NOW = datetime(2026, 11, 13, 4, 0)
CREATED = datetime(2026, 11, 9, 2, 0)
MAX_IMAGE_BYTES = 5_242_880
POSTER_PREFIX = "promotion-posters/"
SECRET = "SECRETVALUE"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "promotion_not_found"}
ENDED = {"detail": "promotion_ended"}
STORE_UNAVAILABLE = {"detail": "image_store_unavailable"}
TYPE_UNSUPPORTED = {"detail": "image_type_unsupported"}
TOO_LARGE = {"detail": "image_too_large"}
POSTER_INVALID = {"detail": "poster_invalid"}
POSTER_REQUIRED = {"detail": "poster_required"}
IMAGE_NOT_FOUND = {"detail": "image_not_found"}

UPLOADED = "admin_promotion_poster_uploaded"
REMOVED = "admin_promotion_poster_removed"
POSTER_ACTIONS = (UPLOADED, REMOVED)
COLUMN_SUFFIXES = ("file_name", "source_format", "width", "height", "byte_size")
NO_POSTER = (None, None, None, None, None)

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
POSTER_PATH = re.compile(r"/api/promotion-posters/([0-9a-f]{32})\.webp")
# 格式正确但从未被任何活动引用的文件名，以及一个不存在的活动 ID。
OTHER_NAME = "fedcba9876543210fedcba9876543210"
MISSING_ID = 999
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 promotion-poster-bytes"

SUCCESS_HEADERS = {
    "content-type": "image/webp",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; sandbox",
    "cache-control": "public, max-age=31536000, immutable",
}

# 马来西亚本地时间的活动时段：相对 NOW 未开始（草稿同）、进行中、已结束。
WINDOWS = {
    "draft": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "scheduled": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "running": ("2026-11-10T00:00", "2026-11-20T00:00"),
    "ended": ("2026-11-01T00:00", "2026-11-05T00:00"),
}

# 真正的删除函数，在替身替换之前取出（存储未配置时只记日志的用例要用它）。
_REAL_DELETE_IMAGE = image_store.delete_image


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


# 桌面图宽恰为 1,200；手机图宽恰为 600。
DESKTOP_PNG = _png(1200, 400)
PHONE_PNG = _png(600, 900)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(PromotionPublishLock(singleton_slot=1))
        seed.add(
            AdminAccount(
                username="admin@example.com",
                password_hash="not-a-real-hash",
                created_at=NOW - timedelta(days=1),
                password_updated_at=NOW - timedelta(days=1),
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
def account_id(db: Session) -> int:
    found = db.scalar(select(AdminAccount.id))
    assert found is not None
    return found


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poster_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
    db.commit()
    return issued.token


@dataclass
class FakeStore:
    """替换 image_store 的 put_image、get_image 与 delete_image，记录 (文件名, prefix)。

    prefix 只限关键字且没有默认值：漏传即 TypeError。put_fails 为真时写入抛
    ImageStoreUnavailable；get_error 不为 None 时 get_image 抛出它（异常文本里带 SECRET，
    检查不转给访客）。
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    puts: list[tuple[str, str]] = field(default_factory=list)
    gets: list[tuple[str, str]] = field(default_factory=list)
    deletes: list[tuple[str, str]] = field(default_factory=list)
    put_fails: bool = False
    get_error: type[Exception] | None = None

    def put_image(self, settings: Settings, file_name: str, data: bytes, *, prefix: str) -> None:
        if self.put_fails:
            raise ImageStoreUnavailable
        self.puts.append((file_name, prefix))
        self.objects[file_name] = data

    def get_image(self, settings: Settings, file_name: str, *, prefix: str) -> bytes:
        self.gets.append((file_name, prefix))
        if self.get_error is not None:
            raise self.get_error(SECRET)
        return self.objects[file_name]

    def delete_image(self, settings: Settings, file_name: str, *, prefix: str) -> None:
        self.deletes.append((file_name, prefix))


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "put_image", fake.put_image)
    monkeypatch.setattr(image_store, "get_image", fake.get_image)
    monkeypatch.setattr(image_store, "delete_image", fake.delete_image)
    return fake


@dataclass
class BodyReads:
    """读请求体的依赖被调用的次数；为 0 即请求体没有被读。"""

    count: int = 0


@pytest.fixture
def reads() -> BodyReads:
    return BodyReads()


def _app(engine: Engine, settings: Settings, reads: BodyReads) -> FastAPI:
    app = create_app(settings)

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    async def _counted_body(request: Request) -> bytes:
        reads.count += 1
        return await _image_body(request)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[_image_body] = _counted_body
    return app


@pytest.fixture
def app(engine: Engine, reads: BodyReads) -> FastAPI:
    # 以四个原名构造为已配置（同 tests/test_image_store.py）；端点是不可解析的示例值。
    r2 = {
        "R2_ENDPOINT_URL": "https://r2-endpoint.example.invalid",
        "R2_BUCKET": "bucket-name-for-tests",
        "R2_ACCESS_KEY_ID": "access-key-id-for-tests",
        "R2_SECRET_ACCESS_KEY": "secret-access-key-for-tests",
    }
    return _app(engine, Settings(_env_file=None, redis_url="", **r2), reads)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def unconfigured(engine: Engine, reads: BodyReads, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """存储未配置的应用：四项都不传，环境里也没有。"""
    for name in R2_NAMES:
        monkeypatch.delenv(name, raising=False)
    unconfigured_app = _app(engine, Settings(_env_file=None, redis_url=""), reads)
    return TestClient(unconfigured_app, base_url="https://testserver")


# ---------------------------------------------------------------------------
# 活动与读取
# ---------------------------------------------------------------------------


def _make(engine: Engine, state: str, poster_mode: str = "template") -> int:
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
    with Session(engine) as session:
        admin_id = session.scalars(select(AdminAccount.id)).one()
        promotion_id = create_promotion(session, admin_id, data, CREATED).id
        if state != "draft":
            session.execute(
                update(Promotion)
                .where(Promotion.id == promotion_id)
                .values(status="published", updated_at=CREATED)
            )
            session.commit()
        assert read_promotion(session, promotion_id, NOW).state == state
    return promotion_id


def _replace(app: FastAPI, engine: Engine, promotion_id: int, slot: str, data: bytes = WEBP) -> str:
    """经 replace_poster 设好该位置的海报图，返回新的文件名（32 位）。"""
    processed = ProcessedImage(data=data, source_format="png", width=1200, height=400)
    with Session(engine) as session:
        admin_id = session.scalars(select(AdminAccount.id)).one()
        settings = app.state.settings
        path = replace_poster(session, settings, admin_id, promotion_id, slot, processed, NOW)
    match = POSTER_PATH.fullmatch(path)
    assert match is not None
    return match.group(1)


def _remove_directly(app: FastAPI, engine: Engine, promotion_id: int, slot: str) -> None:
    with Session(engine) as session:
        admin_id = session.scalars(select(AdminAccount.id)).one()
        remove_poster(session, app.state.settings, admin_id, promotion_id, slot, NOW)


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _poster_columns(engine: Engine, promotion_id: int, slot: str) -> tuple[Any, ...]:
    """装饰行 slot 位置的五列：文件名、上传时的格式、展示图的宽、高与字节数。"""
    columns = [getattr(PromotionDecoration, f"poster_{slot}_{s}") for s in COLUMN_SUFFIXES]
    stmt = select(*columns).where(PromotionDecoration.promotion_id == promotion_id)
    return _read(engine, lambda session: tuple(session.execute(stmt).one()))


def _version_and_updated_at(engine: Engine, promotion_id: int) -> tuple[Any, ...]:
    stmt = select(Promotion.version, Promotion.updated_at).where(Promotion.id == promotion_id)
    return _read(engine, lambda session: tuple(session.execute(stmt).one()))


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """海报的审计记录：时间、管理员、操作、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = (
            select(
                AuditEvent.occurred_at,
                AuditEvent.admin_account_id,
                AuditEvent.action,
                AuditEvent.target_type,
                AuditEvent.target_id,
                AuditEvent.old_value,
                AuditEvent.new_value,
            )
            .where(AuditEvent.action.in_(POSTER_ACTIONS))
            .order_by(AuditEvent.id)
        )
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """全部活动的版本号、修改时间与两个位置的海报五列，以及海报审计。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        columns = [
            getattr(PromotionDecoration, f"poster_{slot}_{suffix}")
            for slot in ("desktop", "phone")
            for suffix in COLUMN_SUFFIXES
        ]
        stmt = (
            select(Promotion.id, Promotion.version, Promotion.updated_at, *columns)
            .join(PromotionDecoration, PromotionDecoration.promotion_id == Promotion.id)
            .order_by(Promotion.id)
        )
        return [tuple(row) for row in session.execute(stmt)]

    return (_read(engine, read), _audits(engine))


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = "auto") -> dict[str, str]:
    """csrf 为 "auto" 时按 token 算出正确的令牌；给 None 即不带请求头。"""
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _url(promotion_id: int | str, slot: str) -> str:
    return f"{ADMIN_URL}/{promotion_id}/poster/{slot}"


def _upload(
    client: TestClient,
    token: str | None,
    promotion_id: int | str,
    slot: str = "desktop",
    content: Any = DESKTOP_PNG,
    *,
    content_type: str | None = "image/png",
    csrf: str | None = "auto",
    extra: dict[str, str] | None = None,
) -> Any:
    headers = _headers(token, csrf)
    if content_type is not None:
        headers["Content-Type"] = content_type
    headers.update(extra or {})
    return client.put(_url(promotion_id, slot), content=content, headers=headers)


def _remove(
    client: TestClient,
    token: str | None,
    promotion_id: int | str,
    slot: str = "desktop",
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.delete(_url(promotion_id, slot), headers=_headers(token, csrf))


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> Any:
    """200，带 no-store，响应体只有 poster。"""
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    body = response.json()
    assert list(body) == ["poster"]
    return body


def _file_name(body: dict[str, Any]) -> str:
    match = POSTER_PATH.fullmatch(body["poster"])
    assert match is not None
    return match.group(1)


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)
    assert SECRET not in response.text


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["draft", "scheduled", "running"])
def test_upload_returns_200_with_the_poster_path_and_writes_an_audit(
    client: TestClient,
    engine: Engine,
    account_id: int,
    token: str,
    store: FakeStore,
    state: str,
) -> None:
    """第 12 条「海报图沿用『计价、优惠、积分与库存』第 13 条的格式、文件大小与存储规则（另用
    自己的存储键前缀）」「桌面图宽至少 1,200」；验收「成功 200，响应体只有 poster 一个字段（新
    路径）」；落实写法「只对存在且不是 ended 的活动」：草稿、未开始与进行中的活动上传宽 1,200
    的 PNG 都回 200 与 /api/promotion-posters/<32 位小写十六进制>.webp；写入存储的是 WebP、
    前缀 promotion-posters/；桌面位置五列记 png、1200×400 与展示图字节数；一条审计；活动的
    版本号与修改时间不变；没有删除对象。"""
    promotion_id = _make(engine, state)
    before = _version_and_updated_at(engine, promotion_id)

    body = _ok(_upload(client, token, promotion_id))

    file_name = _file_name(body)
    assert store.puts == [(file_name, POSTER_PREFIX)]
    data = store.objects[file_name]
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    assert _poster_columns(engine, promotion_id, "desktop") == (
        file_name,
        "png",
        1200,
        400,
        len(data),
    )
    assert _poster_columns(engine, promotion_id, "phone") == NO_POSTER
    assert _audits(engine) == [
        (NOW, account_id, UPLOADED, "promotion", promotion_id, "desktop", f"desktop {file_name}")
    ]
    assert _version_and_updated_at(engine, promotion_id) == before
    assert store.deletes == []


def test_phone_upload_of_width_600_is_accepted(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 12 条「手机图宽至少 600」：宽恰为 600 的 PNG 上传到 phone 位置回 200，只改手机位置
    的五列。"""
    promotion_id = _make(engine, "draft")

    body = _ok(_upload(client, token, promotion_id, "phone", PHONE_PNG))

    assert _poster_columns(engine, promotion_id, "phone")[:4] == (
        _file_name(body),
        "png",
        600,
        900,
    )
    assert _poster_columns(engine, promotion_id, "desktop") == NO_POSTER


@pytest.mark.parametrize(
    ("slot", "content"),
    [
        pytest.param("desktop", _png(1199, 400), id="desktop-1199-wide"),
        pytest.param("desktop", PHONE_PNG, id="desktop-phone-sized"),
        pytest.param("phone", _png(599, 900), id="phone-599-wide"),
        pytest.param("phone", b"not an image", id="not-an-image"),
        pytest.param("desktop", b"", id="empty"),
        pytest.param("desktop", DESKTOP_PNG[: len(DESKTOP_PNG) // 2], id="truncated"),
    ],
)
def test_bad_poster_is_422_poster_invalid_without_put(
    client: TestClient, engine: Engine, token: str, store: FakeStore, slot: str, content: bytes
) -> None:
    """第 12 条「像素尺寸为桌面图宽至少 1,200、手机图宽至少 600」；第 13 条格式不合格即回答
    「图片不合格」；落实写法「读请求体与处理（422 poster_invalid）」：桌面位置宽 1,199 与宽
    600 的图、手机位置宽 599 的图、不是图片、空请求体与截断的 PNG 都 422 poster_invalid；
    不调用 put_image，数据不变。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)

    _assert_error(_upload(client, token, promotion_id, slot, content), 422, POSTER_INVALID)
    assert store.puts == []
    assert _snapshot(engine) == before


def test_second_upload_replaces_and_deletes_the_old_object(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 12 条「上传即替换该位置的图，存放与替换的顺序照『计价、优惠、积分与库存』第 15 条」；
    第 15 条「提交成功后，原来有标志的，删除原有对象」：同一位置再次上传回 200 与新路径，记录
    改为新文件名，删除了原有对象（前缀 promotion-posters/），第二条审计的旧值为「desktop 原文
    件名」；另一位置不受影响。"""
    promotion_id = _make(engine, "draft")
    phone = _file_name(_ok(_upload(client, token, promotion_id, "phone", PHONE_PNG)))
    first = _file_name(_ok(_upload(client, token, promotion_id)))
    second = _file_name(_ok(_upload(client, token, promotion_id, content=_png(1600, 500))))

    assert first != second
    assert [name for name, _ in store.puts] == [phone, first, second]
    assert store.deletes == [(first, POSTER_PREFIX)]
    assert _poster_columns(engine, promotion_id, "desktop")[:4] == (second, "png", 1600, 500)
    assert _poster_columns(engine, promotion_id, "phone")[0] == phone
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        UPLOADED,
        "promotion",
        promotion_id,
        f"desktop {first}",
        f"desktop {second}",
    )


def test_store_write_failure_is_503_and_the_record_is_unchanged(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 15 条「写入失败或超时即回答『图片存储不可用』、记录不变」；验收「503 照
    app/api/admin_store_logo.py」：put_image 失败时 503 image_store_unavailable，数据不变。"""
    promotion_id = _make(engine, "draft")
    store.put_fails = True
    before = _snapshot(engine)

    _assert_error(_upload(client, token, promotion_id), 503, STORE_UNAVAILABLE)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 检查顺序
# ---------------------------------------------------------------------------


def _all_upload_failures(
    client: TestClient,
    unconfigured: TestClient,
    token: str | None,
    promotion_id: int | str,
    slot: str = "desktop",
    csrf: str | None = "auto",
) -> list[Any]:
    """合格的、存储未配置、类型不对、声明过大与坏图的上传。"""
    return [
        _upload(client, token, promotion_id, slot, csrf=csrf),
        _upload(unconfigured, token, promotion_id, slot, csrf=csrf),
        _upload(client, token, promotion_id, slot, b"x", content_type="text/plain", csrf=csrf),
        _upload(
            client,
            token,
            promotion_id,
            slot,
            csrf=csrf,
            extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)},
        ),
        _upload(client, token, promotion_id, slot, _png(1199, 400), csrf=csrf),
    ]


@pytest.mark.parametrize("session_state", ["missing", "unknown"])
def test_no_admin_session_is_401_before_everything(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    store: FakeStore,
    reads: BodyReads,
    session_state: str,
) -> None:
    """落实写法「两个方法都先查会话（401 admin_session_required）」：没有或不认识的会话时，
    上传（含存储未配置、类型不对、声明过大与坏图，以及不合法的路径与已结束、不存在的活动）与
    移除都 401；不读请求体、不碰存储，数据不变。"""
    token = None if session_state == "missing" else "A" * 43
    ended = _make(engine, "ended")
    before = _snapshot(engine)

    responses = []
    for promotion_id, slot in ((ended, "desktop"), (MISSING_ID, "phone"), ("0", "tablet")):
        responses.extend(
            _all_upload_failures(client, unconfigured, token, promotion_id, slot, "0" * 64)
        )
        responses.append(_remove(client, token, promotion_id, slot, csrf="0" * 64))

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_without_a_valid_csrf_token_is_403(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    reads: BodyReads,
    csrf: str | None,
) -> None:
    """落实写法「会话（401）、CSRF（403 csrf_failed）、路径……」：X-CSRF-Token 缺少、为空或不
    一致时，上传（含不合法的路径、不存在与已结束的活动、存储未配置、类型不对、声明过大与坏图）
    与移除都 403 csrf_failed；不读请求体、不碰存储，数据不变。"""
    draft = _make(engine, "draft")
    ended = _make(engine, "ended")
    before = _snapshot(engine)

    targets = ((draft, "desktop"), (ended, "phone"), (MISSING_ID, "x"), ("01", "phone"))
    responses = []
    for promotion_id, slot in targets:
        responses.extend(
            _all_upload_failures(client, unconfigured, token, promotion_id, slot, csrf)
        )
        responses.append(_remove(client, token, promotion_id, slot, csrf=csrf))

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    ("promotion_id", "slot", "loc"),
    [
        pytest.param("0", "desktop", "promotion_id", id="zero"),
        pytest.param("01", "desktop", "promotion_id", id="leading-zero"),
        pytest.param("-1", "desktop", "promotion_id", id="negative"),
        pytest.param("abc", "desktop", "promotion_id", id="not-a-number"),
        pytest.param("2147483648", "desktop", "promotion_id", id="over-int"),
        pytest.param(str(MISSING_ID), "tablet", "slot", id="unknown-slot-missing-promotion"),
        pytest.param(str(MISSING_ID), "Desktop", "slot", id="uppercase-slot"),
    ],
)
def test_bad_path_is_422_before_reading_the_promotion(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    reads: BodyReads,
    promotion_id: str,
    slot: str,
    loc: str,
) -> None:
    """落实写法「路径（活动 ID 照 app/api/admin_categories.py 的 _category_id 校验，slot 不是
    desktop 或 phone，都为 422），再以普通读取看活动」：不合法的活动 ID 与不是 desktop 或 phone
    的 slot（含活动不存在的）在上传与移除都 422，错误位置指向该路径参数、不回显所给的值；先于
    存储与请求体的检查，不读请求体、不碰存储。"""
    before = _snapshot(engine)
    responses = [
        *_all_upload_failures(client, unconfigured, token, promotion_id, slot),
        _remove(client, token, promotion_id, slot),
        _remove(unconfigured, token, promotion_id, slot),
    ]

    for response in responses:
        assert response.status_code == 422, response.text
        _assert_no_store(response)
        [error] = response.json()["detail"]
        assert error["loc"] == ["path", loc]
        assert "input" not in error
        assert f"{promotion_id}/poster/{slot}" not in response.text
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    ("state", "status_code", "body"),
    [
        pytest.param("missing", 404, NOT_FOUND, id="missing"),
        pytest.param("ended", 409, ENDED, id="ended"),
    ],
)
def test_missing_or_ended_promotion_is_answered_before_store_and_body_checks(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    reads: BodyReads,
    state: str,
    status_code: int,
    body: dict[str, str],
) -> None:
    """落实写法「再以普通读取看活动（不存在 404 promotion_not_found，ended 409
    promotion_ended）。PUT 接着查存储是否配置（503）、Content-Type（415）、声明长度（413）、
    读请求体与处理（422 poster_invalid）」；验收「不存在与 ended 先于存储与请求体的检查，
    不读请求体」：不存在与已结束的活动，合格的上传、存储未配置、类型不对、声明过大与坏图都
    回答 404 或 409，移除（含存储未配置的应用）同样；请求体没有被读，不碰存储，数据不变。"""
    promotion_id = MISSING_ID if state == "missing" else _make(engine, "ended")
    before = _snapshot(engine)

    for slot in ("desktop", "phone"):
        responses = [
            *_all_upload_failures(client, unconfigured, token, promotion_id, slot),
            _remove(client, token, promotion_id, slot),
            _remove(unconfigured, token, promotion_id, slot),
        ]
        for response in responses:
            _assert_error(response, status_code, body)
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


def test_store_unconfigured_is_503_without_reading_the_body(
    unconfigured: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 13 条「存储不可用时：上传……回答『图片存储不可用』（503、带 no-store），不读请求体、
    不访问 R2」；落实写法「PUT 接着查存储是否配置（503）、Content-Type（415）、声明长度
    （413）」：存在的活动在存储未配置时，合格的 PNG、类型不对、声明过大与坏图都 503
    image_store_unavailable；请求体没有被读，数据不变。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)

    for response in [
        _upload(unconfigured, token, promotion_id),
        _upload(unconfigured, token, promotion_id, content=b"x", content_type="image/gif"),
        _upload(
            unconfigured, token, promotion_id, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}
        ),
        _upload(unconfigured, token, promotion_id, content=_png(1199, 400)),
    ]:
        _assert_error(response, 503, STORE_UNAVAILABLE)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param(None, id="missing"),
        pytest.param("image/gif", id="gif"),
        pytest.param("image/svg+xml", id="svg"),
        pytest.param("application/octet-stream", id="octet-stream"),
    ],
)
def test_unsupported_content_type_is_415_before_the_declared_length(
    client: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    reads: BodyReads,
    content_type: str | None,
) -> None:
    """第 13 条「Content-Type 为 image/jpeg、image/png 或 image/webp 之一（否则『格式不
    支持』）」；落实写法「Content-Type（415）、声明长度（413）」：内容是合格的 PNG 也按请求头
    415 image_type_unsupported，声明过大也是 415；不读请求体、不写存储，数据不变。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)

    for extra in ({}, {"Content-Length": str(MAX_IMAGE_BYTES + 1)}):
        response = _upload(client, token, promotion_id, content_type=content_type, extra=extra)
        _assert_error(response, 415, TYPE_UNSUPPORTED)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


def test_declared_length_over_5_mb_is_413_without_reading_the_body(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 13 条「请求头声明的长度超过即拒绝……回答『文件太大』」；落实写法「声明长度
    （413）」：请求头声明 5,242,881 时，即使实际只有一张合格的 PNG 也 413 image_too_large；
    请求体没有被读，不写存储，数据不变。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)
    declared = {"Content-Length": str(MAX_IMAGE_BYTES + 1)}

    _assert_error(_upload(client, token, promotion_id, extra=declared), 413, TOO_LARGE)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


def test_undeclared_length_over_5_mb_is_413_while_reading(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 13 条「未声明长度时读到超过即停止读取并拒绝，回答『文件太大』」；验收「请求体读取
    照 app/api/admin_store_logo.py」：不带 Content-Length 的 5,242,881 字节请求体由读请求体的
    依赖 413 image_too_large；不写存储，数据不变。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)

    def chunks() -> Iterator[bytes]:
        yield DESKTOP_PNG
        yield b"\0" * (MAX_IMAGE_BYTES + 1 - len(DESKTOP_PNG))

    response = _upload(client, token, promotion_id, content=chunks())

    _assert_error(response, 413, TOO_LARGE)
    assert response.request.headers.get("content-length") is None
    assert reads.count == 1
    assert store.puts == []
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 移除
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["draft", "scheduled", "running"])
def test_remove_clears_the_poster_and_deletes_the_object(
    client: TestClient,
    engine: Engine,
    account_id: int,
    token: str,
    store: FakeStore,
    state: str,
) -> None:
    """第 12 条「存放与替换的顺序照『计价、优惠、积分与库存』第 15 条」；第 15 条「有标志时五项
    清空、改更新时间、写审计并提交，提交后删除对象」；验收「成功 200，响应体只有 poster 一个
    字段（移除后为 null）」：模板模式的草稿、未开始与进行中的活动移除桌面图回 200 与 poster 为
    null，五列清空，删除了该对象（前缀 promotion-posters/），写一条审计（旧值「desktop 文件
    名」、新值 desktop）；手机图不受影响。"""
    promotion_id = _make(engine, state)
    desktop = _file_name(_ok(_upload(client, token, promotion_id)))
    phone = _file_name(_ok(_upload(client, token, promotion_id, "phone", PHONE_PNG)))

    body = _ok(_remove(client, token, promotion_id))

    assert body == {"poster": None}
    assert _poster_columns(engine, promotion_id, "desktop") == NO_POSTER
    assert _poster_columns(engine, promotion_id, "phone")[0] == phone
    assert store.deletes == [(desktop, POSTER_PREFIX)]
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        REMOVED,
        "promotion",
        promotion_id,
        f"desktop {desktop}",
        "desktop",
    )


def test_remove_without_a_poster_is_200_without_audit(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """落实写法「DELETE 没有请求体……直接调用移除服务——照 remove_logo」；第 15 条「没有标志时
    回滚，照样回答成功，不写审计」：该位置没有图时移除（含带了请求体的）都回 200 与 poster 为
    null；不写审计、不碰存储，请求体不读。"""
    promotion_id = _make(engine, "draft")
    before = _snapshot(engine)

    _ok(_remove(client, token, promotion_id, "phone"))
    with_body = client.request(
        "DELETE", _url(promotion_id, "phone"), content=b"not read", headers=_headers(token)
    )

    assert _ok(with_body) == {"poster": None}
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("state", ["scheduled", "running"])
@pytest.mark.parametrize("slot", ["desktop", "phone"])
def test_published_image_mode_remove_is_409_poster_required(
    client: TestClient, engine: Engine, token: str, store: FakeStore, state: str, slot: str
) -> None:
    """第 12 条「上传图（桌面、手机各一张……）」与 SHOP-TASK-240 落实写法「已发布（scheduled 或
    running）且海报为上传图模式的活动不能移除图」；验收「PosterRequired 为 409
    poster_required」：已发布的上传图模式活动移除任一位置都 409 poster_required，图与审计不变、
    不删除对象；同一活动照样可以上传替换。"""
    promotion_id = _make(engine, state, poster_mode="image")
    _ok(_upload(client, token, promotion_id))
    _ok(_upload(client, token, promotion_id, "phone", PHONE_PNG))
    store.deletes.clear()
    before = _snapshot(engine)

    _assert_error(_remove(client, token, promotion_id, slot), 409, POSTER_REQUIRED)
    assert store.deletes == []
    assert _snapshot(engine) == before
    _ok(_upload(client, token, promotion_id))


def test_draft_image_mode_remove_is_allowed(client: TestClient, engine: Engine, token: str) -> None:
    """SHOP-TASK-240 落实写法「已发布（scheduled 或 running）且海报为上传图模式的活动不能移除
    图」：只限已发布的；上传图模式的草稿移除照样 200，五列清空。"""
    promotion_id = _make(engine, "draft", poster_mode="image")
    _ok(_upload(client, token, promotion_id))

    _ok(_remove(client, token, promotion_id))

    assert _poster_columns(engine, promotion_id, "desktop") == NO_POSTER


def test_remove_works_when_the_store_is_unconfigured(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    account_id: int,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """落实写法「DELETE 没有请求体、不查存储配置，直接调用移除服务——照 remove_logo，存储未
    配置时照常清空记录并提交，只记日志、不调用存储」：已配置时上传的海报图，在存储未配置的应用
    里移除照样 200，五列清空并写审计；用真正的 delete_image，它只记一条未配置的日志，不创建
    R2 client。"""
    promotion_id = _make(engine, "draft")
    file_name = _file_name(_ok(_upload(client, token, promotion_id)))
    monkeypatch.setattr(image_store, "delete_image", _REAL_DELETE_IMAGE)

    def _no_client(settings: Settings) -> Any:
        raise AssertionError("R2 client must not be created")

    monkeypatch.setattr(image_store, "delete_client", _no_client)
    caplog.set_level(logging.INFO, logger=image_store.__name__)

    _ok(_remove(unconfigured, token, promotion_id))

    assert _poster_columns(engine, promotion_id, "desktop") == NO_POSTER
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        REMOVED,
        "promotion",
        promotion_id,
        f"desktop {file_name}",
        "desktop",
    )
    messages = [r.getMessage() for r in caplog.records if r.name == image_store.__name__]
    assert messages == [f"image_store_unconfigured operation=delete file={file_name}"]


# ---------------------------------------------------------------------------
# 后台接口的缓存与路由
# ---------------------------------------------------------------------------


def test_all_admin_responses_are_no_store(
    client: TestClient, unconfigured: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """验收「请求体读取、415、413、503 与 no-store 照 app/api/admin_store_logo.py」：200、
    401、403、404、409、413、415、422 与两种 503 都带 no-store。"""
    draft = _make(engine, "draft")
    ended = _make(engine, "ended")
    responses = [
        _upload(client, token, draft),
        _remove(client, token, draft),
        _upload(client, None, draft),
        _remove(client, token, draft, csrf=None),
        _upload(client, token, MISSING_ID),
        _remove(client, token, ended),
        _upload(client, token, draft, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
        _upload(client, token, draft, content_type="image/gif"),
        _upload(client, token, draft, "tablet"),
        _upload(client, token, draft, content=_png(1199, 400)),
        _upload(unconfigured, token, draft),
    ]
    store.put_fails = True
    responses.append(_upload(client, token, draft))

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 401, 403, 404, 409, 413, 415, 422, 422, 503, 503]
    for response in responses:
        _assert_no_store(response)


def test_admin_routes_are_put_and_delete(app: FastAPI) -> None:
    """验收「PUT 与 DELETE /api/admin/promotions/{promotion_id}/poster/{slot}」「两个路由在
    app/main.py 照现有顺序注册」：该路径下只有 PUT 与 DELETE。"""
    path = f"{ADMIN_URL}/{{promotion_id}}/poster/{{slot}}"
    routes = {
        (method.upper(), route)
        for route, operations in app.openapi()["paths"].items()
        if route.startswith(ADMIN_URL) and "/poster" in route
        for method in operations
    }

    assert routes == {("PUT", path), ("DELETE", path)}


# ---------------------------------------------------------------------------
# 公开提供
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["draft", "scheduled", "running"])
def test_referenced_poster_is_served_with_long_cache(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore, state: str
) -> None:
    """第 12 条「海报图沿用『计价、优惠、积分与库存』第 13 条的……存储规则（另用自己的存储键
    前缀）」；第 13 条「提供」（成功时同一文件名的内容永不改变，可长期缓存）；落实写法「公开提供
    只认 240 的 poster_file_names 里的文件名（草稿的也提供，供管理员预览）」：草稿、未开始与
    进行中活动的海报图不登录即得到 200、写入的字节与四个响应头，读取用的前缀为
    promotion-posters/。"""
    promotion_id = _make(engine, state)
    data = b"RIFF\x20\x00\x00\x00WEBPVP8 uploaded-poster"
    file_name = _replace(app, engine, promotion_id, "phone", data)

    response = client.get(f"{PUBLIC_URL}/{file_name}.webp")

    assert response.status_code == 200
    assert response.content == data
    for name, value in SUCCESS_HEADERS.items():
        assert response.headers[name] == value
    assert store.gets == [(file_name, POSTER_PREFIX)]


def test_admin_upload_path_is_served(client: TestClient, engine: Engine, token: str) -> None:
    """验收「响应体只有 poster 一个字段（新路径）」与「GET /api/promotion-posters/{name}」：后台
    上传返回的路径即可公开读取，内容为写入存储的展示图。"""
    promotion_id = _make(engine, "draft")
    poster = _ok(_upload(client, token, promotion_id))["poster"]

    response = client.get(poster)

    assert response.status_code == 200
    assert response.content[:4] == b"RIFF"


def test_unreferenced_names_are_404_without_reading_store(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """验收「文件名不在 poster_file_names 里即 404」；第 15 条「被替换的旧路径从此回答 404」：
    从未被引用的、被替换的与被移除的文件名都 404 image_not_found（存储里留着对象也一样），
    不调用 get_image；当前引用的照常提供。"""
    promotion_id = _make(engine, "draft")
    replaced = _replace(app, engine, promotion_id, "desktop")
    current = _replace(app, engine, promotion_id, "desktop")
    removed = _replace(app, engine, promotion_id, "phone")
    _remove_directly(app, engine, promotion_id, "phone")
    for name in (OTHER_NAME, replaced, removed):
        store.objects[name] = WEBP

    for name in (OTHER_NAME, replaced, removed):
        _assert_error(client.get(f"{PUBLIC_URL}/{name}.webp"), 404, IMAGE_NOT_FOUND)
    assert store.gets == []
    assert client.get(f"{PUBLIC_URL}/{current}.webp").status_code == 200


@pytest.mark.parametrize("case", ["uppercase", "31-digits", "png", "no-extension"])
def test_malformed_name_is_404_before_unconfigured_store(
    app: FastAPI,
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    store: FakeStore,
    case: str,
) -> None:
    """验收「文件名格式、404、503……照 app/api/store_logos.py」：被引用的文件名改成大写、
    31 位、.png 与不带扩展名都由处理函数回答 404 image_not_found（存储未配置时也是 404），
    不调用 get_image。"""
    file_name = _replace(app, engine, _make(engine, "draft"), "desktop")
    name = {
        "uppercase": f"{file_name.upper()}.webp",
        "31-digits": f"{file_name[:31]}.webp",
        "png": f"{file_name}.png",
        "no-extension": file_name,
    }[case]

    _assert_error(client.get(f"{PUBLIC_URL}/{name}"), 404, IMAGE_NOT_FOUND)
    _assert_error(unconfigured.get(f"{PUBLIC_URL}/{name}"), 404, IMAGE_NOT_FOUND)
    assert store.gets == []


def test_unconfigured_store_is_503(
    app: FastAPI, engine: Engine, unconfigured: TestClient, store: FakeStore
) -> None:
    """验收「503……照 app/api/store_logos.py」；第 13 条「存储不可用时……回答『图片存储不
    可用』」：被引用的文件名在存储未配置时 503 image_store_unavailable，不调用 get_image。"""
    file_name = _replace(app, engine, _make(engine, "draft"), "desktop")

    response = unconfigured.get(f"{PUBLIC_URL}/{file_name}.webp")

    _assert_error(response, 503, STORE_UNAVAILABLE)
    assert store.gets == []


@pytest.mark.parametrize(
    ("error", "status_code", "body"),
    [
        pytest.param(ImageNotFound, 404, IMAGE_NOT_FOUND, id="not-found"),
        pytest.param(ImageStoreUnavailable, 503, STORE_UNAVAILABLE, id="unavailable"),
    ],
)
def test_store_errors(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    store: FakeStore,
    error: type[Exception],
    status_code: int,
    body: dict[str, str],
) -> None:
    """第 13 条「提供」（R2 对象不存在 404、其他错误 503、都带 no-store）：get_image 抛
    ImageNotFound 即 404 image_not_found，抛 ImageStoreUnavailable 即 503
    image_store_unavailable；响应里没有异常文本，成功之外不带长期缓存。"""
    file_name = _replace(app, engine, _make(engine, "draft"), "desktop")
    store.get_error = error

    response = client.get(f"{PUBLIC_URL}/{file_name}.webp")

    _assert_error(response, status_code, body)
    assert store.gets == [(file_name, POSTER_PREFIX)]


def test_public_route_is_registered(app: FastAPI) -> None:
    """验收「GET /api/promotion-posters/{name}」「两个路由在 app/main.py 照现有顺序注册」：该
    前缀下只有这一个 GET。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(PUBLIC_URL)
        for method in operations
    }

    assert routes == {("GET", f"{PUBLIC_URL}/{{name}}")}

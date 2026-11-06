"""后台店铺标志图的上传与移除接口：PUT 与 DELETE /api/admin/store-design/logo
（app/api/admin_store_logo.py，SHOP-TASK-212；规则见 SHOP-TASK-211 的
app/services/store_logo.py），以及后台装修读取 GET /api/admin/store-design 新增的 logo。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条（下称
「第 15 条」；它照搬的第 13 条写作「第 13 条」）、docs/UX.md 0.18 的 A08 标志段（下称
「A08」）与 docs/HANDOFF.md 0.84 的落实写法。每条测试（参数化的测试是每个用例）的文档
字符串写明它守住的原句；没有直接原句的，写明是 SHOP-TASK-212 验收里的约定（下称「验收」）。

夹具照 tests/test_admin_product_images_api.py：SQLite 内存库按模型建表（StaticPool、
关闭 check_same_thread、开外键），https 的 base_url，以 issue_admin_session 登录
（SHOP-TASK-036），CSRF 令牌在这里另算。装修设置按迁移的默认值写入（pandan、主色为空、
四个区块全部显示），没有标志。Settings 以 R2_ENDPOINT_URL 等四个原名构造为已配置；
未配置的用例不传。时间固定为 NOW（monkeypatch 本接口模块与 app.api.admin_auth 的
_utcnow）。app.services.image_store 的 put_image 与 delete_image 一律以 monkeypatch
替换并记录调用，不访问 R2。读请求体的依赖 _image_body 经 dependency_overrides 包一层
计数，用来判断请求体是否被读。上传的样本用 Pillow 现场生成 PNG。检查结果在另一个数据库
会话里读出，读得到即说明接口已提交。
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
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_store_logo as logo_api
from app.api.admin_product_images import _image_body
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AuditEvent, StoreDesignSetting, StoreHomeBlock
from app.models.store_design import DEFAULT_HOME_BLOCKS, HOME_BLOCKS, SINGLETON_SLOT
from app.services import image_store
from app.services.admin_auth import issue_admin_session
from app.services.image_store import ImageStoreUnavailable

URL = "/api/admin/store-design/logo"
DESIGN_URL = "/api/admin/store-design"

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

NOW = datetime(2026, 11, 6, 3, 0)
MAX_IMAGE_BYTES = 5_242_880
LOGO_PREFIX = "store-logos/"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
STORE_UNAVAILABLE = {"detail": "image_store_unavailable"}
TYPE_UNSUPPORTED = {"detail": "image_type_unsupported"}
TOO_LARGE = {"detail": "image_too_large"}
LOGO_INVALID = {"detail": "logo_invalid"}

UPLOADED = "admin_store_logo_uploaded"
REMOVED = "admin_store_logo_removed"

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
LOGO_PATH = re.compile(r"/api/store-logos/([0-9a-f]{32})\.webp")
# HANDOFF 0.84：共享反向代理放宽到 6m 的那条正则 location。
PROXY_LOCATION = r"/api/admin/(products/[0-9]+/images|store-design/logo)"


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


# 横条标志：高度不足 200，长边 300。
PNG = _png(300, 60)


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
        seed.add(
            AdminAccount(
                username="admin@example.com",
                password_hash="not-a-real-hash",
                created_at=NOW - timedelta(days=1),
                password_updated_at=NOW - timedelta(days=1),
            )
        )
        seed.add(
            StoreDesignSetting(
                singleton_slot=SINGLETON_SLOT,
                theme="pandan",
                accent=None,
                updated_at=NOW - timedelta(days=1),
            )
        )
        for block, position, visible in DEFAULT_HOME_BLOCKS:
            seed.add(StoreHomeBlock(block=block, position=position, is_visible=visible))
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
    monkeypatch.setattr(logo_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
    db.commit()
    return issued.token


@dataclass
class FakeStore:
    """替换 image_store 的 put_image 与 delete_image，记录调用（含前缀）。"""

    puts: list[tuple[str, bytes, str]] = field(default_factory=list)
    deletes: list[tuple[str, str]] = field(default_factory=list)
    put_fails: bool = False

    def put_image(self, settings: Settings, file_name: str, data: bytes, *, prefix: str) -> None:
        if self.put_fails:
            raise ImageStoreUnavailable
        self.puts.append((file_name, data, prefix))

    def delete_image(self, settings: Settings, file_name: str, *, prefix: str) -> None:
        self.deletes.append((file_name, prefix))


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "put_image", fake.put_image)
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
# 请求与读取
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


def _upload(
    client: TestClient,
    token: str | None,
    content: Any = PNG,
    *,
    content_type: str | None = "image/png",
    csrf: str | None = "auto",
    extra: dict[str, str] | None = None,
) -> Any:
    headers = _headers(token, csrf)
    if content_type is not None:
        headers["Content-Type"] = content_type
    headers.update(extra or {})
    return client.put(URL, content=content, headers=headers)


def _remove(client: TestClient, token: str | None, *, csrf: str | None = "auto") -> Any:
    return client.delete(URL, headers=_headers(token, csrf))


def _get_design(client: TestClient, token: str) -> Any:
    return client.get(DESIGN_URL, headers=_headers(token, None))


def _save_design(client: TestClient, token: str) -> Any:
    body = {
        "theme": "batik",
        "accent": "sogan",
        "home_blocks": [{"block": block, "visible": True} for block in HOME_BLOCKS],
        "featured_product_ids": [],
    }
    return client.put(DESIGN_URL, json=body, headers=_headers(token))


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _logo_row(engine: Engine) -> tuple[Any, ...]:
    """单例行的标志五列：文件名、上传时的格式、展示图的宽、高与字节数。"""

    def read(session: Session) -> tuple[Any, ...]:
        stmt = select(
            StoreDesignSetting.logo_file_name,
            StoreDesignSetting.logo_source_format,
            StoreDesignSetting.logo_width,
            StoreDesignSetting.logo_height,
            StoreDesignSetting.logo_byte_size,
        )
        return tuple(session.execute(stmt).one())

    return _read(engine, read)


def _design_row(engine: Engine) -> tuple[Any, ...]:
    """A08 保存的其余设置：主题、主色与区块各行（区块、位置、是否显示）。"""

    def read(session: Session) -> tuple[Any, ...]:
        setting_stmt = select(StoreDesignSetting.theme, StoreDesignSetting.accent)
        columns = (StoreHomeBlock.block, StoreHomeBlock.position, StoreHomeBlock.is_visible)
        blocks_stmt = select(*columns).order_by(StoreHomeBlock.position)
        setting = tuple(session.execute(setting_stmt).one())
        return (setting, [tuple(row) for row in session.execute(blocks_stmt)])

    return _read(engine, read)


def _updated_at(engine: Engine) -> datetime:
    stmt = select(StoreDesignSetting.updated_at)
    return _read(engine, lambda session: session.scalars(stmt).one())


def _setting_id(engine: Engine) -> int:
    return _read(engine, lambda session: session.scalars(select(StoreDesignSetting.id)).one())


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """全部审计记录：时间、管理员、操作、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = select(
            AuditEvent.occurred_at,
            AuditEvent.admin_account_id,
            AuditEvent.action,
            AuditEvent.target_type,
            AuditEvent.target_id,
            AuditEvent.old_value,
            AuditEvent.new_value,
        ).order_by(AuditEvent.id)
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    return (_logo_row(engine), _design_row(engine), _updated_at(engine), _audits(engine))


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> Any:
    """200，带 no-store，响应体只有 logo。"""
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    body = response.json()
    assert list(body) == ["logo"]
    return body


def _ok_design(response: Any) -> Any:
    """后台装修读取或保存的 200，带 no-store。"""
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _file_name(body: dict[str, Any]) -> str:
    match = LOGO_PATH.fullmatch(body["logo"])
    assert match is not None
    return match.group(1)


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


def test_upload_returns_200_with_the_logo_path_and_writes_an_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 15 条「上传：PUT /api/admin/store-design/logo，请求体就是图片文件本身」
    「展示图存为存储桶里键为 store-logos/<文件名>.webp 的对象」
    「改写五项与更新时间、写审计并提交」
    「上传的旧值为原有文件名（原来没有标志时为空）、新值为新文件名」
    「上传成功的回答带新的 logo 路径，页面据此显示」：
    300×60 的 PNG 回 200 与 /api/store-logos/<32 位小写十六进制>.webp；写入存储的是
    WebP 展示图、前缀 store-logos/；五列记 png、300×60 与展示图的字节数，更新时间改为
    此刻；一条审计；没有删除对象。"""
    body = _ok(_upload(client, token))

    file_name = _file_name(body)
    [(put_name, put_data, prefix)] = store.puts
    assert (put_name, prefix) == (file_name, LOGO_PREFIX)
    assert put_data[:4] == b"RIFF" and put_data[8:12] == b"WEBP"
    assert _logo_row(engine) == (file_name, "png", 300, 60, len(put_data))
    assert _updated_at(engine) == NOW
    assert _audits(engine) == [
        (NOW, account_id, UPLOADED, "store_design", _setting_id(engine), None, file_name)
    ]
    assert store.deletes == []


@pytest.mark.parametrize(
    ("width", "height"),
    [
        pytest.param(40, 200, id="40x200"),
        pytest.param(300, 60, id="300x60"),
        pytest.param(200, 40, id="200x40"),
    ],
)
def test_logo_sizes_within_the_logo_bounds_are_accepted(
    client: TestClient, engine: Engine, token: str, store: FakeStore, width: int, height: int
) -> None:
    """第 15 条「像素尺寸为宽与高都在 40 到 4,000 像素之间、且长边至少 200 像素
    （横条标志的高度常不足 200）」：40×200、300×60 与 200×40 都 200，记录的宽与高
    即原尺寸。"""
    body = _ok(_upload(client, token, _png(width, height)))

    assert len(store.puts) == 1
    assert _logo_row(engine)[:4] == (_file_name(body), "png", width, height)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(_png(39, 200), id="39x200"),
        pytest.param(_png(150, 150), id="150x150-long-side-short"),
        pytest.param(_png(200, 39), id="200x39"),
        pytest.param(b"not an image", id="not-an-image"),
        pytest.param(b"", id="empty"),
        pytest.param(PNG[: len(PNG) // 2], id="truncated"),
    ],
)
def test_bad_logo_is_422_logo_invalid_without_put(
    client: TestClient, engine: Engine, token: str, store: FakeStore, content: bytes
) -> None:
    """第 15 条「宽与高都在 40 到 4,000 像素之间、且长边至少 200 像素……不合格即回答
    『图片不合格』」；HANDOFF 0.84「422 logo_invalid」；A08「图片不合格用
    `[admin.logo_invalid]`」：39×200、长边不足 200 的 150×150、200×39、不是图片、
    空请求体与截断的 PNG 都 422 logo_invalid；不调用 put_image，数据不变。"""
    before = _snapshot(engine)

    _assert_error(_upload(client, token, content), 422, LOGO_INVALID)
    assert store.puts == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param("  IMAGE/PNG ; charset=binary", id="params-case-space"),
        pytest.param("image/jpeg", id="declared-jpeg-content-png"),
        pytest.param("image/webp", id="declared-webp-content-png"),
    ],
)
def test_content_type_is_only_a_gate_and_the_content_decides(
    client: TestClient, engine: Engine, token: str, content_type: str
) -> None:
    """第 15 条「Content-Type（同第 13 条）」；第 13 条「Content-Type 与内容不一致时以
    内容为准」；验收「Content-Type 去掉参数与首尾空白、转小写后」比较：带参数、大小写与
    空白不同的 image/png，以及声明为 JPEG 或 WebP 的 PNG 都 200，记录的格式按内容为
    png。"""
    _ok(_upload(client, token, content_type=content_type))

    assert _logo_row(engine)[1] == "png"


def test_admin_read_shows_the_uploaded_logo(client: TestClient, token: str) -> None:
    """第 15 条「公开的 GET /api/store-design 与后台的装修读取都新增 logo 字段：有标志时
    为上面的提供路径，没有时为 null」：上传前后台读取的 logo 为 null，上传后等于上传
    返回的路径。"""
    assert _ok_design(_get_design(client, token))["logo"] is None

    body = _ok(_upload(client, token))

    assert _ok_design(_get_design(client, token))["logo"] == body["logo"]


def test_second_upload_replaces_and_deletes_the_old_object(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 15 条「上传即替换当前标志」「提交成功后，原来有标志的，删除原有对象」
    「上传的旧值为原有文件名……新值为新文件名」；A08「有标志时 `[admin.upload]` 即替换，
    不另确认」：再次上传回 200 与新路径，记录改为新文件名，删除了原有对象（前缀
    store-logos/），第二条审计的旧值为原文件名；后台读取的 logo 为新路径。"""
    first = _file_name(_ok(_upload(client, token)))
    second_body = _ok(_upload(client, token, _png(40, 200)))
    second = _file_name(second_body)

    assert first != second
    assert [name for name, _, _ in store.puts] == [first, second]
    assert store.deletes == [(first, LOGO_PREFIX)]
    assert _logo_row(engine)[:4] == (second, "png", 40, 200)
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        UPLOADED,
        "store_design",
        _setting_id(engine),
        first,
        second,
    )
    assert _ok_design(_get_design(client, token))["logo"] == second_body["logo"]


def test_upload_and_remove_do_not_change_the_other_a08_settings(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 15 条「标志的上传与移除也不改 A08 保存的其余设置」：上传与移除前后主题、主色与
    区块都不变。"""
    before = _design_row(engine)

    _ok(_upload(client, token))
    assert _design_row(engine) == before
    _ok(_remove(client, token))
    assert _design_row(engine) == before


def test_store_write_failure_is_503_and_the_record_is_unchanged(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 15 条「写入失败或超时即回答『图片存储不可用』、记录不变」；验收「replace_logo
    抛 ImageStoreUnavailable 即 503」：put_image 失败时 503 image_store_unavailable，
    数据不变。"""
    store.put_fails = True
    before = _snapshot(engine)

    _assert_error(_upload(client, token), 503, STORE_UNAVAILABLE)
    assert _snapshot(engine) == before


def test_there_is_no_upload_count_limit(client: TestClient, engine: Engine, token: str) -> None:
    """第 15 条「没有张数上限这一项」「店铺至多一张标志图」：连续上传 10 次都 200，每次
    只替换，记录始终是最后一次上传的那张。"""
    for _ in range(10):
        body = _ok(_upload(client, token))

    assert _logo_row(engine)[0] == _file_name(body)


# ---------------------------------------------------------------------------
# 上传的检查顺序
# ---------------------------------------------------------------------------


def _all_upload_failures(
    client: TestClient, unconfigured: TestClient, token: str | None, csrf: str | None
) -> list[Any]:
    """合格的、存储未配置、类型不对、声明过大与坏图的上传。"""
    return [
        _upload(client, token, csrf=csrf),
        _upload(unconfigured, token, csrf=csrf),
        _upload(client, token, b"x", content_type="text/plain", csrf=csrf),
        _upload(client, token, csrf=csrf, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
        _upload(client, token, _png(150, 150), csrf=csrf),
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
    """第 15 条「按以下顺序检查，前一项不通过即回答、不再往下：管理员会话与 CSRF；……」
    「移除：……检查管理员会话与 CSRF」；验收「没有会话 401 先于一切」：没有或不认识的
    会话时，上传（含存储未配置、类型不对、声明过大与坏图）与移除都 401
    admin_session_required；不读请求体、不碰存储，数据不变。"""
    token = None if session_state == "missing" else "A" * 43
    before = _snapshot(engine)

    responses = [
        *_all_upload_failures(client, unconfigured, token, "0" * 64),
        _remove(client, token, csrf="0" * 64),
        _remove(unconfigured, token, csrf="0" * 64),
    ]

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
    """第 15 条「管理员会话与 CSRF」排在第一项；验收「X-CSRF-Token（403 csrf_failed）」：
    已有标志时，X-CSRF-Token 缺少、为空或不一致的上传（存储未配置、类型不对、声明过大与
    坏图也先 403）与移除都 403 csrf_failed；不读请求体、不碰存储，数据不变。"""
    _ok(_upload(client, token))
    store.puts.clear()
    reads.count = 0
    before = _snapshot(engine)

    responses = [
        *_all_upload_failures(client, unconfigured, token, csrf),
        _remove(client, token, csrf=csrf),
        _remove(unconfigured, token, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


def test_store_unconfigured_is_503_without_reading_the_body(
    unconfigured: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 15 条「存储已配置（否则『图片存储不可用』）」排在 Content-Type 与文件大小之前；
    第 13 条「存储不可用时：上传……回答『图片存储不可用』（503、带 no-store），不读
    请求体、不访问 R2」；A08「其他失败（含图片存储不可用）显示 `[common.error_retry]`」：
    合格的 PNG、类型不对、声明过大与坏图都 503 image_store_unavailable；请求体没有被读，
    put_image 没有被调用，数据不变。"""
    before = _snapshot(engine)

    for response in [
        _upload(unconfigured, token),
        _upload(unconfigured, token, b"x", content_type="image/gif"),
        _upload(unconfigured, token, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
        _upload(unconfigured, token, _png(39, 200)),
    ]:
        _assert_error(response, 503, STORE_UNAVAILABLE)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("image/gif", id="gif"),
        pytest.param("image/svg+xml", id="svg"),
        pytest.param("application/octet-stream", id="octet-stream"),
        pytest.param("multipart/form-data; boundary=x", id="multipart"),
        pytest.param("image/png-x", id="lookalike"),
    ],
)
def test_unsupported_content_type_is_415(
    client: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    reads: BodyReads,
    content_type: str | None,
) -> None:
    """第 15 条「Content-Type（同第 13 条）」；第 13 条「Content-Type 为 image/jpeg、
    image/png 或 image/webp 之一（否则『格式不支持』）」；HANDOFF 0.84「415
    image_type_unsupported」：内容是合格的 PNG 也按请求头 415，排在文件大小之前（声明
    过大也是 415）；不读请求体、不写存储，数据不变。"""
    before = _snapshot(engine)

    for extra in ({}, {"Content-Length": str(MAX_IMAGE_BYTES + 1)}):
        response = _upload(client, token, content_type=content_type, extra=extra)
        _assert_error(response, 415, TYPE_UNSUPPORTED)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


def test_declared_length_over_5_mb_is_413_without_reading_the_body(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 15 条「文件不超过 5 MB（同第 13 条）」；第 13 条「请求头声明的长度超过即
    拒绝……回答『文件太大』」；HANDOFF 0.84「5 MB 指 5,242,880 字节」「413
    image_too_large」：请求头声明 5,242,881 时，即使实际只有一张合格的 PNG 也 413；
    请求体没有被读，不写存储，数据不变。"""
    before = _snapshot(engine)
    declared = {"Content-Length": str(MAX_IMAGE_BYTES + 1)}

    _assert_error(_upload(client, token, extra=declared), 413, TOO_LARGE)
    assert reads.count == 0
    assert store.puts == []
    assert _snapshot(engine) == before


def test_undeclared_length_over_5_mb_is_413_while_reading(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 15 条「文件不超过 5 MB（同第 13 条）」；第 13 条「未声明长度时读到超过即停止
    读取并拒绝，回答『文件太大』」：不带 Content-Length（分块传送）的 5,242,881 字节
    请求体由读请求体的依赖 413 image_too_large；不写存储，数据不变。"""
    before = _snapshot(engine)

    def chunks() -> Iterator[bytes]:
        yield PNG
        yield b"\0" * (MAX_IMAGE_BYTES + 1 - len(PNG))

    response = _upload(client, token, chunks())

    _assert_error(response, 413, TOO_LARGE)
    assert response.request.headers.get("content-length") is None
    assert reads.count == 1
    assert store.puts == []
    assert _snapshot(engine) == before


def test_body_of_exactly_5_mb_is_not_too_large(
    client: TestClient, token: str, store: FakeStore
) -> None:
    """HANDOFF 0.84「5 MB 指 5,242,880 字节」：恰好 5,242,880 字节的请求体不是
    「文件太大」，照常往下按内容判断（这里不是图片，回 422 logo_invalid）。"""
    content = b"\0" * MAX_IMAGE_BYTES

    _assert_error(_upload(client, token, content), 422, LOGO_INVALID)
    assert store.puts == []


# ---------------------------------------------------------------------------
# 移除
# ---------------------------------------------------------------------------


def test_remove_clears_the_logo_and_deletes_the_object(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 15 条「移除：DELETE /api/admin/store-design/logo」「有标志时五项清空、改更新
    时间、写审计并提交，提交后删除对象」「移除的旧值为原有文件名、新值为空」「移除成功后
    页面改显示文字品牌」；A08「移除即恢复文字品牌」：回 200 与 logo 为 null，五列清空，
    删除了该对象（前缀 store-logos/），写一条审计，后台读取的 logo 为 null。"""
    file_name = _file_name(_ok(_upload(client, token)))

    body = _ok(_remove(client, token))

    assert body == {"logo": None}
    assert _logo_row(engine) == (None, None, None, None, None)
    assert store.deletes == [(file_name, LOGO_PREFIX)]
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        REMOVED,
        "store_design",
        _setting_id(engine),
        file_name,
        None,
    )
    assert _ok_design(_get_design(client, token))["logo"] is None


def test_remove_without_a_logo_is_200_without_audit(
    client: TestClient, engine: Engine, token: str, store: FakeStore, reads: BodyReads
) -> None:
    """第 15 条「移除：……没有请求体」「没有标志时回滚，照样回答成功，不写审计」：
    没有标志时移除（含带了请求体的）都回 200 与 logo 为 null；不写审计、不改更新时间、
    不碰存储，请求体不读。"""
    before = _snapshot(engine)

    _ok(_remove(client, token))
    with_body = client.request("DELETE", URL, content=b"not read", headers=_headers(token))

    assert _ok(with_body) == {"logo": None}
    assert reads.count == 0
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


def test_remove_works_when_the_store_is_unconfigured(
    client: TestClient, unconfigured: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """第 15 条「存储未配置时照常清空并提交，不调用 R2，只记日志」；验收「存储未配置时
    照常清空，服务层只记日志」：已配置时上传的标志，在存储未配置的应用里移除照样 200，
    五列清空并写审计。"""
    file_name = _file_name(_ok(_upload(client, token)))

    _ok(_remove(unconfigured, token))

    assert _logo_row(engine) == (None, None, None, None, None)
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        REMOVED,
        "store_design",
        _setting_id(engine),
        file_name,
        None,
    )


# ---------------------------------------------------------------------------
# 后台装修的保存、缓存、日志与路由
# ---------------------------------------------------------------------------


def test_a08_save_neither_reads_nor_writes_the_logo(
    client: TestClient, engine: Engine, token: str
) -> None:
    """第 15 条「不随 A08 的保存提交；A08 的保存不读写标志」；验收「PUT
    /api/admin/store-design 的响应不变」：有标志时保存 A08，响应只有主题、主色、区块与
    精选（不含 logo），标志五列不变，后台读取的 logo 仍是原路径。"""
    uploaded = _ok(_upload(client, token))
    logo_row = _logo_row(engine)

    saved = _ok_design(_save_design(client, token))

    assert list(saved) == ["theme", "accent", "home_blocks", "featured"]
    assert saved["theme"] == "batik"
    assert _logo_row(engine) == logo_row
    assert _ok_design(_get_design(client, token))["logo"] == uploaded["logo"]


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient,
    unconfigured: TestClient,
    token: str,
    store: FakeStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 13 条「存储不可用时：上传……回答『图片存储不可用』（503、带 no-store）」；
    验收「两个新接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control
    no-store」「不写日志」：200、401、403、413、415、422 与两种 503 都带 no-store，
    应用不记日志（存储已替换）。"""
    caplog.set_level(logging.DEBUG)
    responses = [
        _upload(client, token),
        _remove(client, token),
        _remove(client, token),
        _upload(client, None),
        _remove(client, None),
        _upload(client, token, csrf=None),
        _remove(client, token, csrf=None),
        _upload(client, token, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
        _upload(client, token, content_type="image/gif"),
        _upload(client, token, _png(39, 200)),
        _upload(unconfigured, token),
    ]
    store.put_fails = True
    responses.append(_upload(client, token))

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 200, 401, 401, 403, 403, 413, 415, 422, 503, 503]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_put_and_delete_on_the_proxy_path(app: FastAPI) -> None:
    """第 15 条「上传：PUT /api/admin/store-design/logo」「移除：DELETE
    /api/admin/store-design/logo」；HANDOFF 0.84「上传路径须与反向代理放宽到 6m 的
    路径一致」（^/api/admin/(products/[0-9]+/images|store-design/logo)$）；验收
    「在 app/main.py 注册路由」：该路径下只有 PUT 与 DELETE，路径符合放宽的那一条。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("PUT", URL), ("DELETE", URL)}
    assert re.fullmatch(PROXY_LOCATION, URL)

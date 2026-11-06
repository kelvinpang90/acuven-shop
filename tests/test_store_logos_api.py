"""店铺标志图的提供接口：GET /api/store-logos/<文件名>.webp（app/api/store_logos.py，
SHOP-TASK-213），以及公开读取 GET /api/store-design 新增的 logo。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条「提供」与「读取与
显示」两段（下称「第 15 条」）与 docs/HANDOFF.md 0.84 的落实写法。每条测试（参数化的测试是每个
用例）的文档字符串写明它守住的第 15 条原句；另引 SHOP-TASK-213 验收里的约定（下称「验收」）作
补充。

夹具照 tests/test_product_images_api.py：SQLite 内存库按模型建表（StaticPool、关闭
check_same_thread），装修设置单例行按迁移默认值写入，标志经 SHOP-TASK-211 的 replace_logo 设好、
经 remove_logo 移除；不登录。Settings 以 R2_ENDPOINT_URL 等四个原名构造为已配置；未配置的用例
不传。app.services.image_store 的 put_image、get_image 与 delete_image 一律以 monkeypatch 替换并
记录调用与 prefix，不访问 R2。接口发出的 SQL 由引擎的 before_cursor_execute 事件记录，用来判断
「不查库」。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, StoreDesignSetting
from app.models.store_design import DEFAULT_ACCENT, DEFAULT_THEME, SINGLETON_SLOT
from app.services import image_store
from app.services.image_processing import ProcessedImage
from app.services.image_store import LOGO_KEY_PREFIX, ImageNotFound, ImageStoreUnavailable
from app.services.store_logo import remove_logo, replace_logo

URL = "/api/store-logos"
PUBLIC_URL = "/api/store-design"

NOW = datetime(2026, 11, 6, 3, 0)
SECRET = "SECRETVALUE"

IMAGE_NOT_FOUND = {"detail": "image_not_found"}
STORE_UNAVAILABLE = {"detail": "image_store_unavailable"}

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

# 格式正确但从未记为标志的文件名。
OTHER_NAME = "fedcba9876543210fedcba9876543210"
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 store-logo-bytes"

SUCCESS_HEADERS = {
    "content-type": "image/webp",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; sandbox",
    "cache-control": "public, max-age=31536000, immutable",
}


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
        # 迁移写入的单例行：默认主题与主色，没有标志。
        seed.add(
            StoreDesignSetting(
                singleton_slot=SINGLETON_SLOT,
                theme=DEFAULT_THEME,
                accent=DEFAULT_ACCENT,
                updated_at=NOW - timedelta(days=1),
            )
        )
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def statements(engine: Engine) -> list[str]:
    """引擎此后发出的全部 SQL；测试在发请求前清空。"""
    recorded: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _many: bool
    ) -> None:
        recorded.append(statement)

    return recorded


@dataclass
class FakeStore:
    """替换 image_store 的 put_image、get_image 与 delete_image，记录 (文件名, prefix)。
    get_error 不为 None 时 get_image 抛出它（异常文本里带 SECRET，检查不转给访客）。"""

    objects: dict[str, bytes] = field(default_factory=dict)
    puts: list[tuple[str, str]] = field(default_factory=list)
    gets: list[tuple[str, str]] = field(default_factory=list)
    deletes: list[tuple[str, str]] = field(default_factory=list)
    get_error: type[Exception] | None = None

    def put_image(
        self, settings: Settings, file_name: str, data: bytes, *, prefix: str = "product-images/"
    ) -> None:
        self.puts.append((file_name, prefix))
        self.objects[file_name] = data

    def get_image(
        self, settings: Settings, file_name: str, *, prefix: str = "product-images/"
    ) -> bytes:
        self.gets.append((file_name, prefix))
        if self.get_error is not None:
            raise self.get_error(SECRET)
        return self.objects[file_name]

    def delete_image(
        self, settings: Settings, file_name: str, *, prefix: str = "product-images/"
    ) -> None:
        self.deletes.append((file_name, prefix))


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "put_image", fake.put_image)
    monkeypatch.setattr(image_store, "get_image", fake.get_image)
    monkeypatch.setattr(image_store, "delete_image", fake.delete_image)
    return fake


def _app(engine: Engine, settings: Settings) -> FastAPI:
    app = create_app(settings)

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    # 以四个原名构造为已配置（同 tests/test_image_store.py）；端点是不可解析的示例值。
    r2 = {
        "R2_ENDPOINT_URL": "https://r2-endpoint.example.invalid",
        "R2_BUCKET": "bucket-name-for-tests",
        "R2_ACCESS_KEY_ID": "access-key-id-for-tests",
        "R2_SECRET_ACCESS_KEY": "secret-access-key-for-tests",
    }
    return _app(engine, Settings(_env_file=None, redis_url="", **r2))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def unconfigured(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """存储未配置的应用：四项都不传，环境里也没有。"""
    for name in R2_NAMES:
        monkeypatch.delenv(name, raising=False)
    unconfigured_app = _app(engine, Settings(_env_file=None, redis_url=""))
    return TestClient(unconfigured_app, base_url="https://testserver")


def _account_id(session: Session) -> int:
    account_id = session.scalar(select(AdminAccount.id))
    assert account_id is not None
    return account_id


def _replace(app: FastAPI, engine: Engine, data: bytes = WEBP) -> str:
    """经 replace_logo 设好标志，返回新的文件名（32 位）。"""
    processed = ProcessedImage(data=data, source_format="png", width=400, height=100)
    with Session(engine) as session:
        path = replace_logo(session, app.state.settings, _account_id(session), processed, NOW)
    file_name = path.removeprefix(f"{URL}/").removesuffix(".webp")
    assert path == f"{URL}/{file_name}.webp"
    return file_name


def _remove(app: FastAPI, engine: Engine) -> None:
    with Session(engine) as session:
        remove_logo(session, app.state.settings, _account_id(session), NOW)


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _assert_error(response: Any, status_code: int, body: dict[str, str]) -> None:
    assert response.status_code == status_code
    assert response.json() == body
    _assert_no_store(response)
    assert SECRET not in response.text


# ---------------------------------------------------------------------------
# 成功与公开读取
# ---------------------------------------------------------------------------


def test_current_logo_is_served_with_long_cache(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """第 15 条「标志经 GET /api/store-logos/<文件名>.webp 提供，不要求会话」「公开的 GET
    /api/store-design 与后台的装修读取都新增 logo 字段：有标志时为上面的提供路径」「其余回答、
    响应头与缓存同第 13 条的「提供」（……成功时同一文件名的内容永不改变，可长期缓存）」「展示图
    存为存储桶里键为 store-logos/<文件名>.webp 的对象」：经 replace_logo 设好标志后，公开读取的
    logo 路径不登录即得到 200、写入的字节与四个响应头，读取用的前缀为 store-logos/。"""
    data = b"RIFF\x20\x00\x00\x00WEBPVP8 uploaded-store-logo"
    file_name = _replace(app, engine, data)
    assert store.puts == [(file_name, LOGO_KEY_PREFIX)]

    design = client.get(PUBLIC_URL)
    assert design.status_code == 200
    logo = design.json()["logo"]
    assert logo == f"{URL}/{file_name}.webp"

    response = client.get(logo)

    assert response.status_code == 200
    assert response.content == data
    for name, value in SUCCESS_HEADERS.items():
        assert response.headers[name] == value
    assert store.gets == [(file_name, LOGO_KEY_PREFIX)]


def test_public_read_logo_is_null_without_logo(client: TestClient, statements: list[str]) -> None:
    """第 15 条「公开的 GET /api/store-design 与后台的装修读取都新增 logo 字段：有标志时为上面的
    提供路径，没有时为 null」「公开接口仍带 no-store」：没有标志时 logo 为 null，响应带 no-store，
    只发 SELECT。"""
    statements.clear()

    response = client.get(PUBLIC_URL)

    assert response.status_code == 200
    assert response.json()["logo"] is None
    _assert_no_store(response)
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_public_read_follows_replace_and_remove(
    app: FastAPI, client: TestClient, engine: Engine
) -> None:
    """第 15 条「公开接口仍带 no-store，替换或移除提交后访客的下一次读取即得到新值」：替换后
    logo 为新路径，移除后为 null。"""
    first = _replace(app, engine)
    second = _replace(app, engine)
    assert client.get(PUBLIC_URL).json()["logo"] == f"{URL}/{second}.webp"
    assert first != second

    _remove(app, engine)

    assert client.get(PUBLIC_URL).json()["logo"] is None


# ---------------------------------------------------------------------------
# 检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["uppercase", "31-digits", "png", "no-extension"])
def test_malformed_name_is_404_without_lookup(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    store: FakeStore,
    statements: list[str],
    case: str,
) -> None:
    """第 15 条「文件名不符合格式即 404」；HANDOFF 0.84 文件名格式与错误照
    app/api/product_images.py：当前标志的文件名改成大写、31 位、.png 与不带扩展名都由处理函数
    回答 404 image_not_found（不是框架的默认 404），不查库、不调用 get_image。"""
    file_name = _replace(app, engine)
    name = {
        "uppercase": f"{file_name.upper()}.webp",
        "31-digits": f"{file_name[:31]}.webp",
        "png": f"{file_name}.png",
        "no-extension": file_name,
    }[case]
    statements.clear()

    response = client.get(f"{URL}/{name}")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
    assert statements == []
    assert store.gets == []


def test_unconfigured_store_is_503(
    app: FastAPI,
    engine: Engine,
    unconfigured: TestClient,
    store: FakeStore,
    statements: list[str],
) -> None:
    """第 15 条「存储未配置即 503」：是当前标志也回答 503 image_store_unavailable，不查库、
    不调用 get_image。"""
    file_name = _replace(app, engine)
    statements.clear()

    response = unconfigured.get(f"{URL}/{file_name}.webp")

    _assert_error(response, 503, STORE_UNAVAILABLE)
    assert statements == []
    assert store.gets == []


def test_malformed_name_is_404_before_unconfigured_store(unconfigured: TestClient) -> None:
    """第 15 条「文件名不符合格式即 404；存储未配置即 503」与验收「前一项不通过即回答、不再
    往下」：存储未配置时格式不对的文件名仍是 404。"""
    response = unconfigured.get(f"{URL}/{OTHER_NAME.upper()}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)


def test_name_without_any_logo_is_404_without_reading_store(
    client: TestClient, store: FakeStore
) -> None:
    """第 15 条「文件名不是单例行当前记录的标志（含已被替换或移除的）即 404（带 no-store，不长期
    缓存）」：没有标志时格式正确的文件名 404 image_not_found，不调用 get_image。"""
    store.objects[OTHER_NAME] = WEBP

    response = client.get(f"{URL}/{OTHER_NAME}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
    assert store.gets == []


def test_other_name_is_404_without_reading_store(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """第 15 条「文件名不是单例行当前记录的标志……即 404（带 no-store，不长期缓存）」：有标志时
    另一个格式正确的文件名 404，不调用 get_image。"""
    _replace(app, engine)
    store.objects[OTHER_NAME] = WEBP

    response = client.get(f"{URL}/{OTHER_NAME}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
    assert store.gets == []


def test_replaced_logo_name_is_404_without_reading_store(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """第 15 条「文件名不是单例行当前记录的标志（含已被替换或移除的）即 404（带 no-store，不长期
    缓存）」「被替换的旧路径从此回答 404」：替换之后旧文件名 404 image_not_found，不调用
    get_image（存储里留着旧对象也一样）；新文件名照常提供。"""
    old = _replace(app, engine)
    new = _replace(app, engine)
    store.objects[old] = WEBP

    response = client.get(f"{URL}/{old}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
    assert store.gets == []
    assert client.get(f"{URL}/{new}.webp").status_code == 200


def test_removed_logo_name_is_404_without_reading_store(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """第 15 条「文件名不是单例行当前记录的标志（含已被替换或移除的）即 404（带 no-store，不长期
    缓存）」：移除之后原文件名 404 image_not_found，不调用 get_image。"""
    file_name = _replace(app, engine)
    _remove(app, engine)
    store.objects[file_name] = WEBP

    response = client.get(f"{URL}/{file_name}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
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
    """第 15 条「其余回答、响应头与缓存同第 13 条的「提供」（R2 对象不存在 404、其他错误 503、
    都带 no-store……）」：get_image 抛 ImageNotFound 即 404 image_not_found，抛
    ImageStoreUnavailable 即 503 image_store_unavailable；响应里没有异常文本。"""
    file_name = _replace(app, engine)
    store.get_error = error

    response = client.get(f"{URL}/{file_name}.webp")

    _assert_error(response, status_code, body)
    assert store.gets == [(file_name, LOGO_KEY_PREFIX)]


# ---------------------------------------------------------------------------
# 全部响应
# ---------------------------------------------------------------------------


def test_errors_are_no_store_and_nothing_is_logged(
    app: FastAPI,
    client: TestClient,
    engine: Engine,
    unconfigured: TestClient,
    store: FakeStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 15 条「文件名不是单例行当前记录的标志……即 404（带 no-store，不长期缓存）」「R2 对象
    不存在 404、其他错误 503、都带 no-store；成功时同一文件名的内容永不改变，可长期缓存」；
    验收「错误响应都带 Cache-Control no-store」「不写日志」：每种错误都只带 no-store，成功时
    不带；应用不记日志（存储已替换）。"""
    old = _replace(app, engine)
    current = _replace(app, engine)
    caplog.set_level(logging.DEBUG)
    errors = [
        client.get(f"{URL}/{current}.PNG"),
        unconfigured.get(f"{URL}/{current}.webp"),
        client.get(f"{URL}/{OTHER_NAME}.webp"),
        client.get(f"{URL}/{old}.webp"),
    ]
    for error in (ImageNotFound, ImageStoreUnavailable):
        store.get_error = error
        errors.append(client.get(f"{URL}/{current}.webp"))
    store.get_error = None
    success = client.get(f"{URL}/{current}.webp")

    assert [response.status_code for response in errors] == [404, 503, 404, 404, 404, 503]
    for response in errors:
        _assert_no_store(response)
    assert success.status_code == 200
    assert success.headers["cache-control"] == SUCCESS_HEADERS["cache-control"]
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_route_is_registered(app: FastAPI) -> None:
    """第 15 条「标志经 GET /api/store-logos/<文件名>.webp 提供，不要求会话」；验收「路由写成
    /api/store-logos/{name}」「在 app/main.py 注册路由」：该前缀下只有这一个 GET。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("GET", f"{URL}/{{name}}")}

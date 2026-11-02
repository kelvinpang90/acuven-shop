"""商品展示图的提供接口：GET /api/product-images/<文件名>.webp（app/api/product_images.py，
SHOP-TASK-191；存储见 SHOP-TASK-187 的 app/services/image_store.py）。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条「提供」一段（下称
「第 13 条」）与 docs/HANDOFF.md 0.79 的落实写法。每条测试（参数化的测试是每个用例）的文档字符串
写明它守住的原句；没有直接原句的，写明是 SHOP-TASK-191 验收里的约定（下称「验收」）。

夹具照 tests/test_admin_products_api.py：SQLite 内存库按模型建表（StaticPool、关闭
check_same_thread），种子商品经 create_product 造（未上架）；不登录。Settings 以
R2_ENDPOINT_URL 等四个原名构造为已配置；未配置的用例不传。
app.services.image_store 的 put_image、get_image 与 delete_image 一律以 monkeypatch
替换并记录调用，不访问 R2。接口发出的 SQL 由引擎的 before_cursor_execute 事件记录，
用来判断「不查库」。
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
from app.models import AdminAccount, Category, ProductImage
from app.services import image_store
from app.services.admin_product_images import add_image
from app.services.admin_products import ProductInput, VariantInput, create_product
from app.services.image_processing import ProcessedImage
from app.services.image_store import ImageNotFound, ImageStoreUnavailable

URL = "/api/product-images"

NOW = datetime(2026, 11, 2, 3, 0)
TEE_ID = 1
SECRET = "SECRETVALUE"

IMAGE_NOT_FOUND = {"detail": "image_not_found"}
STORE_UNAVAILABLE = {"detail": "image_store_unavailable"}

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")

# 直接入库的一行（像经上传入库的，五个元数据列都有值）；其对象的字节由 FakeStore 提供。
STORED_NAME = "0123456789abcdef0123456789abcdef"
MISSING_NAME = "fedcba9876543210fedcba9876543210"
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 display-image-bytes"

SUCCESS_HEADERS = {
    "content-type": "image/webp",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; sandbox",
    "cache-control": "public, max-age=31536000, immutable",
}


def _seed_product(name_en: str) -> ProductInput:
    return ProductInput(
        category_id=1,
        name_en=name_en,
        name_zh="",
        name_ms="",
        description_en="",
        description_zh="",
        description_ms="",
        is_active=False,
        max_per_order=5,
        options=[],
        variants=[
            VariantInput(
                id=None,
                temp_id="v1",
                option_values=[],
                price_sen=1500,
                daily_initial_stock=10,
                is_active=True,
            )
        ],
    )


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
        seed.add(Category(slug="apparel", name_en="Apparel", is_active=True))
        account = AdminAccount(
            username="admin@example.com",
            password_hash="not-a-real-hash",
            created_at=NOW - timedelta(days=1),
            password_updated_at=NOW - timedelta(days=1),
        )
        seed.add(account)
        seed.commit()
        account_id = account.id
        create_product(seed, account_id, _seed_product("Crew Neck Tee"), NOW - timedelta(hours=1))
        seed.add(
            ProductImage(
                product_id=TEE_ID,
                storage_ref=f"{URL}/{STORED_NAME}.webp",
                sort_order=0,
                file_name=STORED_NAME,
                source_format="png",
                width=300,
                height=300,
                byte_size=len(WEBP),
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
    """替换 image_store 的 put_image、get_image 与 delete_image，记录调用。
    get_error 不为 None 时 get_image 抛出它（异常文本里带 SECRET，检查不转给访客）。"""

    objects: dict[str, bytes] = field(default_factory=lambda: {STORED_NAME: WEBP})
    puts: list[str] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    get_error: type[Exception] | None = None

    def put_image(self, settings: Settings, file_name: str, data: bytes) -> None:
        self.puts.append(file_name)
        self.objects[file_name] = data

    def get_image(self, settings: Settings, file_name: str) -> bytes:
        self.gets.append(file_name)
        if self.get_error is not None:
            raise self.get_error(SECRET)
        return self.objects[file_name]

    def delete_image(self, settings: Settings, file_name: str) -> None:
        self.deletes.append(file_name)


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


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _assert_error(response: Any, status_code: int, body: dict[str, str]) -> None:
    assert response.status_code == status_code
    assert response.json() == body
    _assert_no_store(response)
    assert SECRET not in response.text


# ---------------------------------------------------------------------------
# 成功
# ---------------------------------------------------------------------------


def test_uploaded_image_is_served_with_long_cache(
    app: FastAPI, client: TestClient, engine: Engine, store: FakeStore
) -> None:
    """第 13 条「展示图经 GET /api/product-images/<文件名>.webp 提供，不要求会话」「成功时回答
    Content-Type image/webp、X-Content-Type-Options nosniff 与 Content-Security-Policy
    （default-src 'none'; sandbox），同一文件名的内容永不改变，可长期缓存（public、一年、
    immutable）」「`ProductImage` 的引用存这个路径」：经 add_image 入库后，GET 该行的
    storage_ref 得到 200、写入的字节与四个响应头；商品未上架也照常提供（验收「不查商品是否
    上架」）。"""
    data = b"RIFF\x20\x00\x00\x00WEBPVP8 uploaded-display-image"
    processed = ProcessedImage(data=data, source_format="png", width=300, height=300)
    with Session(engine) as session:
        account_id = session.scalar(select(AdminAccount.id))
        assert account_id is not None
        images = add_image(session, app.state.settings, account_id, TEE_ID, processed, NOW)
    [file_name] = store.puts
    storage_ref = images[-1].storage_ref
    assert storage_ref == f"{URL}/{file_name}.webp"

    response = client.get(storage_ref)

    assert response.status_code == 200
    assert response.content == data
    for name, value in SUCCESS_HEADERS.items():
        assert response.headers[name] == value
    assert store.gets == [file_name]


# ---------------------------------------------------------------------------
# 检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(f"{STORED_NAME.upper()}.webp", id="uppercase"),
        pytest.param(f"{STORED_NAME[:31]}.webp", id="31-digits"),
        pytest.param(f"{STORED_NAME}.png", id="png"),
        pytest.param(STORED_NAME, id="no-extension"),
    ],
)
def test_malformed_name_is_404_without_lookup(
    client: TestClient, store: FakeStore, statements: list[str], name: str
) -> None:
    """第 13 条「文件名不符合上面的格式（含大写字母）即 404」；HANDOFF 0.79「其他扩展名也回答
    带 no-store 的 image_not_found」：大写、31 位、.png 与不带扩展名都由处理函数回答 404
    image_not_found（不是框架的默认 404），不查库、不调用 get_image。"""
    statements.clear()

    response = client.get(f"{URL}/{name}")

    _assert_error(response, 404, IMAGE_NOT_FOUND)
    assert statements == []
    assert store.gets == []


def test_unconfigured_store_is_503(
    unconfigured: TestClient, store: FakeStore, statements: list[str]
) -> None:
    """第 13 条「存储未配置即 503（1.19）」：已有该行也回答 503 image_store_unavailable，
    不查库、不调用 get_image。"""
    statements.clear()

    response = unconfigured.get(f"{URL}/{STORED_NAME}.webp")

    _assert_error(response, 503, STORE_UNAVAILABLE)
    assert statements == []
    assert store.gets == []


def test_malformed_name_is_404_before_unconfigured_store(unconfigured: TestClient) -> None:
    """第 13 条「文件名不符合上面的格式（含大写字母）即 404；存储未配置即 503」与验收「前一项
    不通过即回答、不再往下」：存储未配置时格式不对的文件名仍是 404。"""
    response = unconfigured.get(f"{URL}/{STORED_NAME.upper()}.webp")

    _assert_error(response, 404, IMAGE_NOT_FOUND)


def test_missing_row_is_404_without_reading_store(client: TestClient, store: FakeStore) -> None:
    """第 13 条「按文件名查到 `ProductImage` 的行才从存储桶读取该对象，查不到行回答 404（带
    no-store，不长期缓存）」：格式正确但没有该行，404 image_not_found，不调用 get_image。"""
    store.objects[MISSING_NAME] = WEBP

    response = client.get(f"{URL}/{MISSING_NAME}.webp")

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
    client: TestClient,
    store: FakeStore,
    error: type[Exception],
    status_code: int,
    body: dict[str, str],
) -> None:
    """第 13 条「R2 回答对象不存在（NoSuchKey 或 HTTP 404）即 404（带 no-store），其余错误（含
    凭据错误与超时）一律 503（带 no-store），不把错误内容转给访客」：get_image 抛 ImageNotFound
    即 404 image_not_found，抛 ImageStoreUnavailable 即 503 image_store_unavailable；响应里没有
    异常文本。"""
    store.get_error = error

    response = client.get(f"{URL}/{STORED_NAME}.webp")

    _assert_error(response, status_code, body)
    assert store.gets == [STORED_NAME]


# ---------------------------------------------------------------------------
# 全部响应
# ---------------------------------------------------------------------------


def test_errors_are_no_store_and_nothing_is_logged(
    client: TestClient,
    unconfigured: TestClient,
    store: FakeStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 13 条「查不到行回答 404（带 no-store，不长期缓存）」「R2 回答对象不存在……即 404（带
    no-store），其余错误……一律 503（带 no-store）」；HANDOFF 0.79「错误为 404
    image_not_found 与 503 image_store_unavailable，带 no-store」；验收「不写日志」：每种错误
    都只带 no-store，成功时不带；应用不记日志（存储已替换）。"""
    caplog.set_level(logging.DEBUG)
    errors = [
        client.get(f"{URL}/{STORED_NAME}.PNG"),
        client.get(f"{URL}/{MISSING_NAME}.webp"),
        unconfigured.get(f"{URL}/{STORED_NAME}.webp"),
    ]
    for error in (ImageNotFound, ImageStoreUnavailable):
        store.get_error = error
        errors.append(client.get(f"{URL}/{STORED_NAME}.webp"))
    store.get_error = None
    success = client.get(f"{URL}/{STORED_NAME}.webp")

    assert [response.status_code for response in errors] == [404, 404, 503, 404, 503]
    for response in errors:
        _assert_no_store(response)
    assert success.status_code == 200
    assert success.headers["cache-control"] == SUCCESS_HEADERS["cache-control"]
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_route_is_registered(app: FastAPI) -> None:
    """验收「路由写成 /api/product-images/{name}」「在 app/main.py 注册路由」：该前缀下
    只有这一个 GET。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL)
        for method in operations
    }

    assert routes == {("GET", f"{URL}/{{name}}")}

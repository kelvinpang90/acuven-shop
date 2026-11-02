"""后台商品图片的上传、调序与移除接口：POST 与 PUT /api/admin/products/{product_id}/images、
DELETE /api/admin/products/{product_id}/images/{image_id}（app/api/admin_product_images.py，
SHOP-TASK-190；规则见 SHOP-TASK-189 的 app/services/admin_product_images.py）。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条（下称「第 13 条」）、
docs/UX.md 0.17 的 A04 图片部分（下称「A04」）与 docs/HANDOFF.md 0.79 的落实写法。每条测试
（参数化的测试是每个用例）的文档字符串写明它守住的原句；没有直接原句的，写明是 SHOP-TASK-190
验收里的约定（下称「验收」）。

夹具照 tests/test_admin_products_api.py：SQLite 内存库按模型建表（StaticPool、关闭
check_same_thread、开外键），https 的 base_url，以 issue_admin_session 登录。种子商品经
create_product 造：商品 1 带一张示例占位图（图片 1，没有文件名），商品 2 没有图片。
Settings 以 R2_ENDPOINT_URL 等四个原名构造为已配置；未配置的用例不传。时间固定为 NOW
（monkeypatch 本接口模块与 app.api.admin_auth 的 _utcnow）。app.services.image_store 的
put_image 与 delete_image 一律以 monkeypatch 替换并记录调用，不访问 R2。上传的样本用
Pillow 现场生成 300×300 的 PNG。检查结果在另一个数据库会话里读出，读得到即说明接口已提交。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_product_images as images_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AuditEvent, Category, Product, ProductImage
from app.services import image_store
from app.services.admin_auth import issue_admin_session
from app.services.admin_products import ProductInput, VariantInput, create_product
from app.services.image_store import ImageStoreUnavailable

URL = "/api/admin/products"

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

NOW = datetime(2026, 11, 2, 3, 0)
TEE_ID = 1
MUG_ID = 2
MISSING_ID = 999
PLACEHOLDER_ID = 1
PLACEHOLDER_REF = "/demo-images/tee.jpg"
MAX_IMAGE_BYTES = 5_242_880
SECRET = "SECRETVALUE"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
PRODUCT_NOT_FOUND = {"detail": "product_not_found"}
STORE_UNAVAILABLE = {"detail": "image_store_unavailable"}
TYPE_UNSUPPORTED = {"detail": "image_type_unsupported"}
TOO_LARGE = {"detail": "image_too_large"}
IMAGES_FULL = {"detail": "images_full"}
IMAGE_INVALID = {"detail": "image_invalid"}
IMAGES_CHANGED = {"detail": "images_changed"}

IMAGE_ADDED = "admin_product_image_added"
IMAGES_REORDERED = "admin_product_images_reordered"
IMAGE_REMOVED = "admin_product_image_removed"

R2_NAMES = ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
STORAGE_REF = re.compile(r"/api/product-images/([0-9a-f]{32})\.webp")


def _png(side: int = 300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (side, side), (180, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


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

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

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
        create_product(seed, account_id, _seed_product("Crew Neck Tee"), NOW - timedelta(hours=2))
        create_product(seed, account_id, _seed_product("Enamel Mug"), NOW - timedelta(hours=1))
        seed.add(ProductImage(product_id=TEE_ID, storage_ref=PLACEHOLDER_REF, sort_order=0))
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
    monkeypatch.setattr(images_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
    db.commit()
    return issued.token


@dataclass
class FakeStore:
    """替换 image_store 的 put_image 与 delete_image，记录调用。"""

    puts: list[tuple[str, bytes]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    put_fails: bool = False

    def put_image(self, settings: Settings, file_name: str, data: bytes) -> None:
        if self.put_fails:
            raise ImageStoreUnavailable
        self.puts.append((file_name, data))

    def delete_image(self, settings: Settings, file_name: str) -> None:
        self.deletes.append(file_name)


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "put_image", fake.put_image)
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
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def unconfigured(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """存储未配置的应用：四项都不传，环境里也没有。"""
    for name in R2_NAMES:
        monkeypatch.delenv(name, raising=False)
    unconfigured_app = _app(engine, Settings(_env_file=None, redis_url=""))
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
    product_id: int | str,
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
    return client.post(f"{URL}/{product_id}/images", content=content, headers=headers)


def _reorder(
    client: TestClient,
    token: str | None,
    product_id: int | str,
    body: Any,
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.put(f"{URL}/{product_id}/images", json=body, headers=_headers(token, csrf))


def _reorder_raw(
    client: TestClient, token: str | None, content: bytes, content_type: str | None
) -> Any:
    headers = _headers(token)
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.put(f"{URL}/{TEE_ID}/images", content=content, headers=headers)


def _remove(
    client: TestClient,
    token: str | None,
    product_id: int | str,
    image_id: int | str,
    *,
    csrf: str | None = "auto",
) -> Any:
    return client.delete(f"{URL}/{product_id}/images/{image_id}", headers=_headers(token, csrf))


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _image_rows(engine: Engine) -> list[tuple[Any, ...]]:
    """全部图片行：ID、商品、引用、排列序号、文件名、格式、宽、高，按 ID。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = select(
            ProductImage.id,
            ProductImage.product_id,
            ProductImage.storage_ref,
            ProductImage.sort_order,
            ProductImage.file_name,
            ProductImage.source_format,
            ProductImage.width,
            ProductImage.height,
        ).order_by(ProductImage.id)
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


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


def _versions(engine: Engine) -> list[tuple[int, int]]:
    return _read(
        engine,
        lambda session: [
            (row[0], row[1])
            for row in session.execute(select(Product.id, Product.version).order_by(Product.id))
        ],
    )


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    return (_image_rows(engine), _audits(engine), _versions(engine))


def _add_rows(engine: Engine, product_id: int, count: int, *, files: bool = False) -> list[int]:
    """给商品直接插入 count 张图片（排列序号接在现有的之后），返回新行的 ID。
    files 为真时五个元数据列都有值（像经上传入库的），否则是没有文件名的占位图。"""

    def insert(session: Session) -> list[int]:
        existing = session.scalars(
            select(ProductImage.sort_order).where(ProductImage.product_id == product_id)
        ).all()
        start = max(existing, default=-1) + 1
        rows = []
        for offset in range(count):
            sort_order = start + offset
            if files:
                name = f"{product_id:02x}{sort_order:030x}"
                row = ProductImage(
                    product_id=product_id,
                    storage_ref=f"/api/product-images/{name}.webp",
                    sort_order=sort_order,
                    file_name=name,
                    source_format="png",
                    width=300,
                    height=300,
                    byte_size=1000,
                )
            else:
                row = ProductImage(
                    product_id=product_id,
                    storage_ref=f"/demo-images/{product_id}-{sort_order}.jpg",
                    sort_order=sort_order,
                )
            session.add(row)
            rows.append(row)
        session.commit()
        return [row.id for row in rows]

    return _read(engine, insert)


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any, status_code: int = 200) -> Any:
    assert response.status_code == status_code, response.text
    _assert_no_store(response)
    body = response.json()
    assert list(body) == ["images"]
    assert all(list(image) == ["id", "storage_ref"] for image in body["images"])
    return body


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _assert_plain_errors(response: Any) -> list[dict[str, Any]]:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert SECRET not in response.text
    return errors


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


def test_upload_returns_201_with_the_images_and_writes_an_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 13 条「请求体就是图片文件本身（不用多部分表单）」「编码为 WebP……作为展示图」
    「图片的上传……不改商品的版本号，各写一条审计（对象为商品……上传的新值……为图片 ID）」；
    A04「上传……立即生效，成功后显示服务端返回的图片列表」：300×300 的 PNG 回 201，
    images 为占位图之后接新图，新图的引用为 /api/product-images/<32 位小写十六进制>.webp；
    写入存储的是 WebP 展示图，行记 png、300×300；一条审计，版本号不变。"""
    versions = _versions(engine)

    body = _ok(_upload(client, token, TEE_ID), 201)

    [placeholder, added] = body["images"]
    assert placeholder == {"id": PLACEHOLDER_ID, "storage_ref": PLACEHOLDER_REF}
    match = STORAGE_REF.fullmatch(added["storage_ref"])
    assert match is not None
    file_name = match.group(1)
    [(put_name, put_data)] = store.puts
    assert put_name == file_name
    assert put_data[:4] == b"RIFF" and put_data[8:12] == b"WEBP"
    assert _image_rows(engine)[-1] == (
        added["id"],
        TEE_ID,
        added["storage_ref"],
        1,
        file_name,
        "png",
        300,
        300,
    )
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        IMAGE_ADDED,
        "product",
        TEE_ID,
        None,
        str(added["id"]),
    )
    assert _versions(engine) == versions
    assert store.deletes == []


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        pytest.param("image/png", PNG, id="png"),
        pytest.param("  IMAGE/PNG ; charset=binary", PNG, id="params-case-space"),
        pytest.param("image/jpeg", PNG, id="declared-jpeg-content-png"),
        pytest.param("image/webp", PNG, id="declared-webp-content-png"),
    ],
)
def test_content_type_is_only_a_gate_and_the_content_decides(
    client: TestClient, engine: Engine, token: str, content_type: str, content: bytes
) -> None:
    """第 13 条「Content-Type 为 image/jpeg、image/png 或 image/webp 之一」「Content-Type 与内容
    不一致时以内容为准」；验收「Content-Type 去掉分号后的参数与首尾空白、转小写后」比较：
    带参数、大小写与空白不同的 image/png，以及声明为 JPEG 或 WebP 的 PNG 都 201，记录的格式按
    内容为 png。"""
    body = _ok(_upload(client, token, MUG_ID, content, content_type=content_type), 201)

    assert len(body["images"]) == 1
    assert _image_rows(engine)[-1][5] == "png"


# 不经过上传检查的样本：路径 ID 不合法、商品不存在、存储未配置、类型不对、声明过大。
def _all_upload_failures(client: TestClient, token: str | None, csrf: str | None) -> list[Any]:
    return [
        _upload(client, token, TEE_ID, csrf=csrf),
        _upload(client, token, SECRET, csrf=csrf),
        _upload(client, token, MISSING_ID, csrf=csrf),
        _upload(client, token, TEE_ID, b"x", content_type="text/plain", csrf=csrf),
        _upload(
            client, token, TEE_ID, csrf=csrf, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}
        ),
    ]


@pytest.mark.parametrize("session_state", ["missing", "unknown"])
def test_no_admin_session_is_401_before_everything(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    store: FakeStore,
    session_state: str,
) -> None:
    """第 13 条「按以下顺序检查，前一项不通过即回答、不再往下：管理员会话与 CSRF；……」；
    验收「未登录 401 先于一切」：没有或不认识的会话时，上传（含路径 ID 不合法、商品不存在、
    存储未配置、类型不对、声明过大）、调序（含请求体不合格）与移除都 401 admin_session_required；
    不写存储，数据不变。"""
    token = None if session_state == "missing" else "A" * 43
    before = _snapshot(engine)

    responses = [
        *_all_upload_failures(client, token, "0" * 64),
        _upload(unconfigured, token, TEE_ID, csrf="0" * 64),
        _reorder(client, token, TEE_ID, {"image_ids": [PLACEHOLDER_ID]}, csrf="0" * 64),
        _reorder(client, token, "0", {"image_ids": SECRET}, csrf="0" * 64),
        _remove(client, token, TEE_ID, PLACEHOLDER_ID, csrf="0" * 64),
        _remove(client, token, "0", SECRET, csrf="0" * 64),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize("csrf", [None, "", "0" * 64])
def test_without_a_valid_csrf_token_is_403(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    csrf: str | None,
) -> None:
    """第 13 条「管理员会话与 CSRF」排在第一项；验收「X-CSRF-Token（403 csrf_failed）」先于
    路径 ID：X-CSRF-Token 缺少、为空或不一致时，上传、调序与移除都 403 csrf_failed（路径 ID
    不合法、商品不存在、存储未配置也先 403）；不写存储，数据不变。"""
    before = _snapshot(engine)

    responses = [
        *_all_upload_failures(client, token, csrf),
        _upload(unconfigured, token, TEE_ID, csrf=csrf),
        _reorder(client, token, TEE_ID, {"image_ids": [PLACEHOLDER_ID]}, csrf=csrf),
        _reorder(client, token, "0", {"image_ids": [PLACEHOLDER_ID]}, csrf=csrf),
        _remove(client, token, TEE_ID, PLACEHOLDER_ID, csrf=csrf),
        _remove(client, token, SECRET, SECRET, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert store.puts == [] and store.deletes == []
    assert _snapshot(engine) == before


BAD_PATH_IDS = [
    pytest.param("0", id="zero"),
    pytest.param("-1", id="negative"),
    pytest.param(SECRET, id="not-a-number"),
    pytest.param("01", id="leading-zero"),
    pytest.param("2147483648", id="above-int-max"),
    pytest.param("9" * 200, id="very-long"),
]

PRODUCT_ID_ERROR = {
    "type": "product_id_invalid",
    "loc": ["path", "product_id"],
    "msg": "Product ID should be a positive integer",
}
IMAGE_ID_ERROR = {
    "type": "image_id_invalid",
    "loc": ["path", "image_id"],
    "msg": "Image ID should be a positive integer",
}


@pytest.mark.parametrize("bad_id", BAD_PATH_IDS)
def test_invalid_path_ids_are_422_without_echo(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    store: FakeStore,
    bad_id: str,
) -> None:
    """验收「路径 ID（照 app/api/admin_products.py 的 _product_id，不合法 422）」「两个路径 ID
    （照 _product_id 的写法）」：上传（存储未配置时也先 422）、调序与移除的商品 ID，以及移除的
    图片 ID 不合法都 422，错误只有对应的一条，不回显所给的值；数据不变。"""
    before = _snapshot(engine)

    for response in [
        _upload(client, token, bad_id),
        _upload(unconfigured, token, bad_id, b"x", content_type="text/plain"),
        _reorder(client, token, bad_id, {"image_ids": [PLACEHOLDER_ID]}),
        _remove(client, token, bad_id, PLACEHOLDER_ID),
    ]:
        assert _assert_plain_errors(response) == [PRODUCT_ID_ERROR]
    assert _assert_plain_errors(_remove(client, token, TEE_ID, bad_id)) == [IMAGE_ID_ERROR]
    assert store.puts == []
    assert _snapshot(engine) == before


def test_missing_product_is_404_before_store_unavailable(
    client: TestClient, unconfigured: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「商品存在（否则 404）；存储已配置（否则『图片存储不可用』）」依次检查；
    验收「商品不存在 404 先于存储未配置的 503」：不存在的商品在存储未配置、类型不对与声明过大
    时也是 404 product_not_found；不写存储。"""
    for target in (client, unconfigured):
        for response in [
            _upload(target, token, MISSING_ID),
            _upload(target, token, MISSING_ID, b"x", content_type="text/plain"),
            _upload(target, token, MISSING_ID, extra={"Content-Length": "6000000"}),
        ]:
            _assert_error(response, 404, PRODUCT_NOT_FOUND)
    assert store.puts == []


def test_store_unconfigured_is_503_without_put(
    unconfigured: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「存储不可用时：上传……回答『图片存储不可用』（503、带 no-store），不读请求体、
    不访问 R2」，排在 Content-Type 与文件大小之前；验收「未配置 503 不调用 put_image」：
    合格的 PNG、类型不对、声明过大与已满的商品都 503 image_store_unavailable，put_image 没有被
    调用，数据不变。"""
    _add_rows(engine, MUG_ID, 8)
    before = _snapshot(engine)

    for response in [
        _upload(unconfigured, token, TEE_ID),
        _upload(unconfigured, token, TEE_ID, b"x", content_type="image/gif"),
        _upload(unconfigured, token, TEE_ID, extra={"Content-Length": "6000000"}),
        _upload(unconfigured, token, MUG_ID, b"not an image"),
    ]:
        _assert_error(response, 503, STORE_UNAVAILABLE)
    assert store.puts == []
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("image/gif", id="gif"),
        pytest.param("image/heic", id="heic"),
        pytest.param("application/octet-stream", id="octet-stream"),
        pytest.param("multipart/form-data; boundary=x", id="multipart"),
        pytest.param("image/png-x", id="lookalike"),
    ],
)
def test_unsupported_content_type_is_415(
    client: TestClient, engine: Engine, token: str, store: FakeStore, content_type: str | None
) -> None:
    """第 13 条「Content-Type 为 image/jpeg、image/png 或 image/webp 之一（否则『格式不支持』）」；
    HANDOFF 0.79「415 image_type_unsupported」：内容是合格的 PNG 也按请求头 415，排在文件大小
    之前（声明过大也是 415）；不写存储，数据不变。"""
    before = _snapshot(engine)

    for extra in ({}, {"Content-Length": "6000000"}):
        response = _upload(client, token, TEE_ID, content_type=content_type, extra=extra)
        _assert_error(response, 415, TYPE_UNSUPPORTED)
    assert store.puts == []
    assert _snapshot(engine) == before


def test_declared_length_over_5_mb_is_413(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「文件不超过 5 MB（请求头声明的长度超过即拒绝……回答『文件太大』）」；
    HANDOFF 0.79「5 MB 指 5,242,880 字节」「413 image_too_large」；验收「声明长度 5,242,881 的
    413」：请求头声明 5,242,881 时，即使实际只有一张合格的 PNG 也 413，排在图片已满之前；
    不写存储，数据不变。"""
    _add_rows(engine, MUG_ID, 8)
    before = _snapshot(engine)
    declared = {"Content-Length": str(MAX_IMAGE_BYTES + 1)}

    for product_id in (TEE_ID, MUG_ID):
        _assert_error(_upload(client, token, product_id, extra=declared), 413, TOO_LARGE)
    assert store.puts == []
    assert _snapshot(engine) == before


def test_undeclared_length_over_5_mb_is_413_while_reading(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「未声明长度时读到超过即停止读取并拒绝，回答『文件太大』」：不带 Content-Length
    （分块传送）的 5,242,881 字节请求体 413 image_too_large；不写存储，数据不变。"""
    before = _snapshot(engine)

    def chunks() -> Iterator[bytes]:
        yield PNG
        yield b"\0" * (MAX_IMAGE_BYTES + 1 - len(PNG))

    response = _upload(client, token, TEE_ID, chunks())

    _assert_error(response, 413, TOO_LARGE)
    assert response.request.headers.get("content-length") is None
    assert store.puts == []
    assert _snapshot(engine) == before


def test_body_of_exactly_5_mb_is_not_too_large(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """HANDOFF 0.79「5 MB 指 5,242,880 字节」：恰好 5,242,880 字节的请求体不是「文件太大」，
    照常往下按内容判断（这里不是图片，回 422 image_invalid）。"""
    content = b"\0" * MAX_IMAGE_BYTES

    _assert_error(_upload(client, token, TEE_ID, content), 422, IMAGE_INVALID)
    assert store.puts == []


def test_eight_images_is_images_full_even_for_a_non_image(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「该商品已有 8 张即回答『图片已满』；最后按文件内容判断」；验收「已有 8 张时即使
    请求体不是图片也回 409 images_full」：已有 8 张的商品上传不是图片的内容与合格的 PNG 都 409
    images_full；不写存储，数据不变；7 张的商品照常上传到第 8 张。"""
    _add_rows(engine, MUG_ID, 8)
    _add_rows(engine, TEE_ID, 6)
    before = _snapshot(engine)

    for content in (b"not an image", PNG):
        _assert_error(_upload(client, token, MUG_ID, content), 409, IMAGES_FULL)
    assert store.puts == []
    assert _snapshot(engine) == before

    assert len(_ok(_upload(client, token, TEE_ID), 201)["images"]) == 8
    _assert_error(_upload(client, token, TEE_ID, b"not an image"), 409, IMAGES_FULL)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"not an image", id="not-an-image"),
        pytest.param(b"", id="empty"),
        pytest.param(PNG[: len(PNG) // 2], id="truncated"),
        pytest.param(_png(199), id="too-small"),
    ],
)
def test_bad_image_is_422_image_invalid(
    client: TestClient, engine: Engine, token: str, store: FakeStore, content: bytes
) -> None:
    """第 13 条「宽与高都在 200 到 4,000 像素之间……不合格即回答『图片不合格』，不留文件」；
    HANDOFF 0.79「422 image_invalid」：不是图片、空请求体、截断的 PNG 与 199×199 的 PNG 都 422
    image_invalid；不写存储，数据不变。"""
    before = _snapshot(engine)

    _assert_error(_upload(client, token, TEE_ID, content), 422, IMAGE_INVALID)
    assert store.puts == []
    assert _snapshot(engine) == before


def test_store_write_failure_is_503(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「写入失败或超时即回答『图片存储不可用』，不插入行」；验收「add_image 的
    ImageStoreUnavailable 503」：put_image 失败时 503 image_store_unavailable，数据不变。"""
    store.put_fails = True
    before = _snapshot(engine)

    _assert_error(_upload(client, token, TEE_ID), 503, STORE_UNAVAILABLE)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 调序
# ---------------------------------------------------------------------------


def test_reorder_returns_the_new_order_and_writes_an_audit(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 13 条「可调整顺序：请求列出该商品现有的全部图片 ID 的新顺序」「调序的旧值与新值为
    前后的图片 ID 序列」「第一张为商品的封面」；A04「排序……立即生效，成功后显示服务端返回的
    图片列表」：[c, a, b] 回 200 与按新顺序的 images，写一条审计，不碰存储，版本号不变。"""
    second, third = _add_rows(engine, TEE_ID, 2)
    versions = _versions(engine)
    wanted = [third, PLACEHOLDER_ID, second]

    body = _ok(_reorder(client, token, TEE_ID, {"image_ids": wanted}))

    assert [image["id"] for image in body["images"]] == wanted
    assert body["images"][1] == {"id": PLACEHOLDER_ID, "storage_ref": PLACEHOLDER_REF}
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        IMAGES_REORDERED,
        "product",
        TEE_ID,
        f"{PLACEHOLDER_ID},{second},{third}",
        f"{third},{PLACEHOLDER_ID},{second}",
    )
    assert _versions(engine) == versions
    assert store.puts == [] and store.deletes == []


def test_reorder_unchanged_is_200_and_writes_nothing(
    client: TestClient, engine: Engine, token: str
) -> None:
    """验收「成功 200 与 images」；第 13 条的调序（SHOP-TASK-189：顺序未变不写库、不写审计）：
    原样的顺序回 200 与当前的图片，数据与审计不变。"""
    before = _snapshot(engine)

    body = _ok(_reorder(client, token, TEE_ID, {"image_ids": [PLACEHOLDER_ID]}))

    assert body == {"images": [{"id": PLACEHOLDER_ID, "storage_ref": PLACEHOLDER_REF}]}
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "image_ids",
    [
        pytest.param("missing", id="missing-one"),
        pytest.param("duplicate", id="duplicate"),
        pytest.param("other-product", id="other-products-image"),
        pytest.param("unknown", id="unknown-id"),
        pytest.param("empty", id="empty"),
    ],
)
def test_reorder_with_other_ids_is_images_changed(
    client: TestClient, engine: Engine, token: str, image_ids: str
) -> None:
    """第 13 条「不是恰好这些 ID 即回答『图片已被改动』」；HANDOFF 0.79「409 images_changed」；
    A04「被拒时按服务端的回答显示」：漏列、重复、列了别的商品的图片、不存在的 ID 与空数组都
    409 images_changed，数据不变。"""
    [second] = _add_rows(engine, TEE_ID, 1)
    [mug_image] = _add_rows(engine, MUG_ID, 1)
    ids = {
        "missing": [second],
        "duplicate": [second, PLACEHOLDER_ID, second],
        "other-product": [second, PLACEHOLDER_ID, mug_image],
        "unknown": [second, 999],
        "empty": [],
    }[image_ids]
    before = _snapshot(engine)

    _assert_error(_reorder(client, token, TEE_ID, {"image_ids": ids}), 409, IMAGES_CHANGED)
    assert _snapshot(engine) == before


def test_reorder_missing_product_is_404(client: TestClient, engine: Engine, token: str) -> None:
    """验收「reorder_images 的 ProductMissing 404」：合法但不存在的商品 ID（含 INT 上限）都 404
    product_not_found，数据不变。"""
    before = _snapshot(engine)

    for product_id in (MISSING_ID, 2**31 - 1):
        response = _reorder(client, token, product_id, {"image_ids": [PLACEHOLDER_ID]})
        _assert_error(response, 404, PRODUCT_NOT_FOUND)
    assert _snapshot(engine) == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"image_ids": [PLACEHOLDER_ID], "note": SECRET}, id="extra-field"),
        pytest.param({}, id="missing-field"),
        pytest.param({"image_ids": None}, id="null"),
        pytest.param({"image_ids": [None]}, id="null-item"),
        pytest.param({"image_ids": SECRET}, id="string"),
        pytest.param({"image_ids": [SECRET]}, id="string-item"),
        pytest.param({"image_ids": ["1"]}, id="numeric-string-item"),
        pytest.param({"image_ids": [True]}, id="bool-item"),
        pytest.param({"image_ids": [1.0]}, id="float-item"),
        pytest.param({"image_ids": {"0": 1}}, id="object"),
        pytest.param([PLACEHOLDER_ID], id="bare-array"),
        pytest.param(SECRET, id="bare-string"),
    ],
)
def test_malformed_reorder_body_is_422_without_echo(
    client: TestClient, engine: Engine, token: str, body: Any
) -> None:
    """验收「请求体为 JSON 对象 image_ids（整数数组，strict，多出字段、缺字段、null 或类型不对
    422，不回显请求内容，同 app/api/pay.py 的 _body_errors）」：都 422，每条错误只有位置、类型与
    固定消息，不回显所给的值，数据不变。"""
    before = _snapshot(engine)

    errors = _assert_plain_errors(_reorder(client, token, TEE_ID, body))

    assert all(error["loc"][0] == "body" for error in errors)
    assert _snapshot(engine) == before


@pytest.mark.parametrize("logged_in", [True, False])
def test_reorder_body_limits_come_before_the_session(
    client: TestClient, engine: Engine, token: str, logged_in: bool
) -> None:
    """验收「请求体照 _write_body 逐块读取、上限 8 KB（413）、不是 JSON 415，先于会话检查」：
    超过 8 KB 时 413、不是 JSON 时 415，有没有会话都一样；恰好 8 KB 的合格请求照常 200
    （只在登录时）。数据不变。"""
    before = _snapshot(engine)
    who = token if logged_in else None
    oversized = json.dumps({"image_ids": [PLACEHOLDER_ID], "pad": "x" * 8192}).encode()

    _assert_error(
        _reorder_raw(client, who, oversized, "application/json"),
        413,
        {"detail": "request body too large"},
    )
    _assert_error(
        _reorder_raw(client, who, b'{"image_ids": [1]}', "text/plain"),
        415,
        {"detail": "request body must be JSON"},
    )
    _assert_error(
        _reorder_raw(client, who, b'{"image_ids": [1]}', None),
        415,
        {"detail": "request body must be JSON"},
    )
    if logged_in:
        content = json.dumps({"image_ids": [PLACEHOLDER_ID]}).encode()
        content += b" " * (8 * 1024 - len(content))
        _ok(_reorder_raw(client, who, content, "application/json"))
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 移除
# ---------------------------------------------------------------------------


def test_remove_returns_the_rest_and_deletes_the_object(
    client: TestClient, engine: Engine, account_id: int, token: str, store: FakeStore
) -> None:
    """第 13 条「可移除：删除数据库行，提交后再删存储桶里的对象」「移除的旧值为图片 ID」；
    A04「移除……立即生效，成功后显示服务端返回的图片列表」：移除上传入库的图片回 200 与其余
    图片，行已删除，删除了它的对象，写一条审计，版本号不变。"""
    [uploaded] = _add_rows(engine, TEE_ID, 1, files=True)
    file_name = _image_rows(engine)[-1][4]
    versions = _versions(engine)

    body = _ok(_remove(client, token, TEE_ID, uploaded))

    assert body == {"images": [{"id": PLACEHOLDER_ID, "storage_ref": PLACEHOLDER_REF}]}
    assert [row[0] for row in _image_rows(engine)] == [PLACEHOLDER_ID]
    assert store.deletes == [file_name]
    assert _audits(engine)[-1] == (
        NOW,
        account_id,
        IMAGE_REMOVED,
        "product",
        TEE_ID,
        str(uploaded),
        None,
    )
    assert _versions(engine) == versions


def test_remove_works_when_the_store_is_unconfigured(
    unconfigured: TestClient, engine: Engine, token: str
) -> None:
    """第 13 条「存储不可用时：……移除照常删行」；验收「存储未配置时照常移除（服务层只记
    日志）」：存储未配置时移除占位图与上传入库的图片都 200，行已删除，列表为空。"""
    [uploaded] = _add_rows(engine, TEE_ID, 1, files=True)

    _ok(_remove(unconfigured, token, TEE_ID, PLACEHOLDER_ID))
    body = _ok(_remove(unconfigured, token, TEE_ID, uploaded))

    assert body == {"images": []}
    assert _image_rows(engine) == []


def test_remove_other_or_unknown_image_is_images_changed(
    client: TestClient, engine: Engine, token: str, store: FakeStore
) -> None:
    """第 13 条「排列与移除」；HANDOFF 0.79「409 images_changed」：移除别的商品的图片、不存在的
    图片与已移除的图片都 409 images_changed，不删对象，数据不变。"""
    [mug_image] = _add_rows(engine, MUG_ID, 1, files=True)
    _ok(_remove(client, token, TEE_ID, PLACEHOLDER_ID))
    before = _snapshot(engine)

    for image_id in (mug_image, 999, PLACEHOLDER_ID):
        _assert_error(_remove(client, token, TEE_ID, image_id), 409, IMAGES_CHANGED)
    assert store.deletes == []
    assert _snapshot(engine) == before


def test_remove_from_missing_product_is_404(client: TestClient, engine: Engine, token: str) -> None:
    """验收「remove_image 的 ProductMissing 404」：合法但不存在的商品 ID 移除 404
    product_not_found，数据不变。"""
    before = _snapshot(engine)

    _assert_error(_remove(client, token, MISSING_ID, PLACEHOLDER_ID), 404, PRODUCT_NOT_FOUND)
    assert _snapshot(engine) == before


# ---------------------------------------------------------------------------
# 缓存、日志与路由
# ---------------------------------------------------------------------------


def test_all_responses_are_no_store_and_nothing_is_logged(
    client: TestClient,
    unconfigured: TestClient,
    engine: Engine,
    token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 13 条「存储不可用时：上传……回答『图片存储不可用』（503、带 no-store）」；验收
    「三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control no-store」「不写日志」：
    201、200、401、403、404、409、413、415、422 与 503 都带 no-store，
    应用不记日志（存储已替换）。"""
    caplog.set_level(logging.DEBUG)
    [second] = _add_rows(engine, TEE_ID, 1)
    _add_rows(engine, MUG_ID, 8)
    responses = [
        _reorder(client, token, TEE_ID, {"image_ids": [second, PLACEHOLDER_ID]}),
        _remove(client, token, TEE_ID, second),
        _upload(client, token, TEE_ID),
        _upload(client, None, TEE_ID),
        _reorder(client, None, TEE_ID, {"image_ids": []}),
        _remove(client, None, TEE_ID, PLACEHOLDER_ID),
        _upload(client, token, TEE_ID, csrf=None),
        _reorder(client, token, TEE_ID, {"image_ids": []}, csrf=None),
        _remove(client, token, TEE_ID, PLACEHOLDER_ID, csrf=None),
        _upload(client, token, MISSING_ID),
        _reorder(client, token, MISSING_ID, {"image_ids": []}),
        _remove(client, token, MISSING_ID, PLACEHOLDER_ID),
        _upload(client, token, MUG_ID),
        _reorder(client, token, TEE_ID, {"image_ids": []}),
        _remove(client, token, TEE_ID, 999),
        _upload(client, token, TEE_ID, extra={"Content-Length": str(MAX_IMAGE_BYTES + 1)}),
        _reorder_raw(client, token, b"[" + b" " * 9000 + b"]", "application/json"),
        _upload(client, token, TEE_ID, content_type="image/gif"),
        _reorder_raw(client, token, b"{}", "text/plain"),
        _upload(client, token, TEE_ID, b"not an image"),
        _upload(client, token, "0"),
        _reorder(client, token, TEE_ID, {"image_ids": None}),
        _remove(client, token, TEE_ID, "0"),
        _upload(unconfigured, token, TEE_ID),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 201] + [401] * 3 + [403] * 3 + [404] * 3 + [409] * 3 + [
        413,
        413,
        415,
        415,
        422,
        422,
        422,
        422,
        503,
    ]
    for response in responses:
        _assert_no_store(response)
    assert [record for record in caplog.records if record.name.startswith("app")] == []


def test_routes_are_the_three_image_endpoints(app: FastAPI) -> None:
    """验收「POST 与 PUT /api/admin/products/{product_id}/images、DELETE
    /api/admin/products/{product_id}/images/{image_id}」「在 app/main.py 注册路由」；HANDOFF 0.79
    「上传路径须与反向代理放宽到 6m 的路径一致」（^/api/admin/products/[0-9]+/images$）：
    /images 结尾与 /images/ 之下只有这三个，上传路径符合放宽的那一条。"""
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(URL) and (path.endswith("/images") or "/images/" in path)
        for method in operations
    }

    assert routes == {
        ("POST", f"{URL}/{{product_id}}/images"),
        ("PUT", f"{URL}/{{product_id}}/images"),
        ("DELETE", f"{URL}/{{product_id}}/images/{{image_id}}"),
    }
    assert re.fullmatch(r"/api/admin/products/[0-9]+/images", f"{URL}/{TEE_ID}/images")

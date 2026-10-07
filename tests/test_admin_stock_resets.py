"""后台库存重置结果接口：GET /api/admin/stock-resets 与
GET /api/admin/stock-resets/{reset_id}（app/api/admin_stock_resets.py 与
app/services/admin_stock_resets.py）。

依据 docs/UX.md 0.10 的 A07（下称「A07」）：
- 目的「查看每日按马来西亚时间自动重置示例库存的结果」；入口「后台导航」；
- 列头 admin.col_date_myt、admin.col_result、admin.col_sku_count，每行
  「<日期> [admin.stock_reset_ok] <n>」或「<日期> [admin.stock_reset_failed] <n>」；
- 「▸（展开后）<sku> [admin.stock_reset_breakdown] [M1]」；
- M1「当日可用库存 = 初始库存 − 仍有效预留；不改历史订单」。
字段取自 SHOP-TASK-032 的 stock_resets 与 stock_reset_lines。每条测试（参数化的测试是
每个用例）的文档字符串写明它守住的是 A07 的哪一句；A07 没有直接原句的，写明是
SHOP-TASK-052 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。管理员、
后台会话、商品规格与重置记录（成功、失败各若干天，含规格已删除即 variant_id 为空的
明细行）都直接写库建立。接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时
回滚未提交的改动；检查结果在另一个数据库会话里读。
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Product,
    ProductVariant,
    StockReset,
    StockResetLine,
)
from app.models.stock_reset import RESET_FAILED, RESET_SUCCEEDED
from app.services.admin_auth import issue_admin_session, revoke_admin_session

URL = "/api/admin/stock-resets"
COOKIE_NAME = "__Host-shop_admin_session"

FIRST_DAY = date(2026, 9, 1)
# 失败记录的异常类名：不能出现在任何响应里。
ERROR_CLASS = "OperationalErrorSecretname"

SESSION_REQUIRED = {"detail": "admin_session_required"}
NOT_FOUND = {"detail": "not_found"}
PAGE_ERROR = {
    "detail": [
        {
            "type": "page_invalid",
            "loc": ["query", "page"],
            "msg": "Page should be an integer from 1 to 10000",
        }
    ]
}
RESET_ID_ERROR = {
    "detail": [
        {
            "type": "reset_id_invalid",
            "loc": ["path", "reset_id"],
            "msg": "Stock reset ID should be a positive integer",
        }
    ]
}

BAD_PAGES = [
    "0",
    "-1",
    "+1",
    "01",
    "10001",
    "99999999999999999999",
    "1.5",
    "1e2",
    "abc",
    "",
    " 1",
    "２",
]
BAD_RESET_IDS = [
    "0",
    "01",
    "-1",
    "+1",
    "2147483648",
    "99999999999",
    "1.0",
    "1e3",
    "abc",
    "２",
    "%201",
]


# ---------------------------------------------------------------------------
# 夹具与数据
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
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture
def admin_id(engine: Engine) -> int:
    with Session(engine) as db:
        row = AdminAccount(
            username="admin@example.com",
            password_hash="not-a-real-hash",
            created_at=_now() - timedelta(days=1),
            password_updated_at=_now() - timedelta(days=1),
        )
        db.add(row)
        db.flush()
        account_id = row.id
        db.commit()
    return account_id


@pytest.fixture
def token(engine: Engine, admin_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    with Session(engine) as db:
        issued = issue_admin_session(db, admin_id, _now() - timedelta(minutes=1))
        db.commit()
    return issued.token


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _completed_at(day: date) -> datetime:
    """马来西亚 0 点（前一天 UTC 16 点）后一分钟完成，不带时区的 UTC。"""
    return datetime.combine(day, time()) - timedelta(hours=8) + timedelta(minutes=1)


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _variant(engine: Engine, sku: str) -> int:
    """直接写一个上架商品的规格并提交，返回规格 ID。"""
    with Session(engine) as db:
        category = Category(slug=f"cat-{sku.lower()}", name_en="Cat", is_active=True)
        db.add(category)
        db.flush()
        product = Product(
            category_id=category.id,
            slug=f"p-{sku.lower()}",
            name_en="Tee",
            is_active=True,
            max_per_order=10,
        )
        db.add(product)
        db.flush()
        variant = ProductVariant(
            product_id=product.id,
            sku=sku,
            price_sen=1000,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        )
        db.add(variant)
        db.flush()
        variant_id = variant.id
        db.commit()
    return variant_id


def _add_reset(
    engine: Engine,
    day: date,
    result: str = RESET_SUCCEEDED,
    lines: tuple[tuple[str, int, int, int | None], ...] = (),
) -> int:
    """直接写一次重置并提交，返回重置 ID。

    lines 为（SKU, 初始库存, 有效预留, 规格 ID 或空）；当日可用按 M1 算（不低于 0）。
    succeeded：尝试 1 次、有完成时间、SKU 数为明细行数。failed 与 SHOP-TASK-032 的
    失败记录相同：尝试 3 次、无完成时间、SKU 数 0、无明细、记下异常类名。
    """
    succeeded = result == RESET_SUCCEEDED
    assert succeeded or not lines
    with Session(engine) as db:
        reset = StockReset(
            business_date=day,
            result=result,
            attempts=1 if succeeded else 3,
            started_at=_completed_at(day) - timedelta(seconds=30),
            completed_at=_completed_at(day) if succeeded else None,
            sku_count=len(lines),
            error_class=None if succeeded else ERROR_CLASS,
        )
        db.add(reset)
        db.flush()
        for sku, initial, held, variant_id in lines:
            db.add(
                StockResetLine(
                    stock_reset_id=reset.id,
                    variant_id=variant_id,
                    sku=sku,
                    initial_stock=initial,
                    held_quantity=held,
                    available_stock=max(initial - held, 0),
                )
            )
        reset_id = reset.id
        db.commit()
    return reset_id


def _stored(engine: Engine) -> list[tuple[Any, ...]]:
    """全部重置记录与明细的各列：用来断言「数据不变」。"""
    with Session(engine) as db:
        resets = db.execute(select(StockReset.__table__).order_by(StockReset.id)).all()
        lines = db.execute(select(StockResetLine.__table__).order_by(StockResetLine.id)).all()
    return [tuple(row) for row in resets] + [tuple(row) for row in lines]


def _audit_count(engine: Engine) -> int:
    with Session(engine) as db:
        return int(db.scalar(select(func.count()).select_from(AuditEvent)) or 0)


def _headers(token: str | None) -> dict[str, str]:
    return {} if token is None else {"Cookie": f"{COOKIE_NAME}={token}"}


def _list(client: TestClient, token: str | None, page: str | int | None = None) -> Any:
    params = {} if page is None else {"page": str(page)}
    return client.get(URL, params=params, headers=_headers(token))


def _detail(client: TestClient, token: str | None, reset_id: int | str) -> Any:
    return client.get(f"{URL}/{reset_id}", headers=_headers(token))


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _row(reset_id: int, day: date, result: str, sku_count: int) -> dict[str, Any]:
    completed = _iso(_completed_at(day)) if result == RESET_SUCCEEDED else None
    return {
        "id": reset_id,
        "business_date": day.isoformat(),
        "result": result,
        "sku_count": sku_count,
        "completed_at": completed,
    }


def _line(sku: str, initial: int, held: int, available: int) -> dict[str, Any]:
    return {
        "sku": sku,
        "initial_stock": initial,
        "held_quantity": held,
        "available_stock": available,
    }


# ---------------------------------------------------------------------------
# 授权与路由
# ---------------------------------------------------------------------------


def _session_cookie(engine: Engine, admin_id: int, kind: str) -> str | None:
    if kind == "none":
        return None
    if kind == "unknown":
        # 格式合法但库里没有的令牌。
        return secrets.token_urlsafe(32)
    with Session(engine) as db:
        if kind == "expired":
            issued = issue_admin_session(db, admin_id, _now() - timedelta(days=31))
        else:
            issued = issue_admin_session(db, admin_id, _now() - timedelta(minutes=5))
            revoke_admin_session(db, issued.session, _now() - timedelta(minutes=1))
        db.commit()
    return issued.token


@pytest.mark.parametrize("kind", ["none", "unknown", "expired", "revoked"])
def test_both_endpoints_require_admin_session(
    engine: Engine, client: TestClient, admin_id: int, kind: str
) -> None:
    """A07「入口：后台导航」：结果只在后台查看。SHOP-TASK-052 验收：两个接口都经
    require_admin，没有有效后台会话时 401 admin_session_required（带 no-store），
    不含重置内容；页码或路径 ID 不合法时也先回答 401。
    """
    reset_id = _add_reset(engine, FIRST_DAY, lines=(("TEE-M", 5, 1, None),))
    cookie = _session_cookie(engine, admin_id, kind)

    responses = [
        _list(client, cookie),
        _list(client, cookie, "0"),
        _detail(client, cookie, reset_id),
        _detail(client, cookie, "abc"),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
        assert "TEE-M" not in response.text


def test_routes_are_two_gets_with_page_only(app: FastAPI) -> None:
    """A07「目的：查看…的结果」：只查看，不改重置。SHOP-TASK-052 验收：只有两个只读的
    GET 接口，查询参数只有 page。
    """
    paths = app.openapi()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(URL)
        for method in operations
    }
    query_params = {
        parameter["name"]
        for path, operations in paths.items()
        if path.startswith(URL)
        for operation in operations.values()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query"
    }

    assert routes == {("GET", URL), ("GET", f"{URL}/{{reset_id}}")}
    assert query_params == {"page"}


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_newest_business_date_first_thirty_per_page(
    engine: Engine, client: TestClient, token: str
) -> None:
    """A07「查看每日按马来西亚时间自动重置示例库存的结果」与列头 admin.col_date_myt：
    按马来西亚营业日期从新到旧。SHOP-TASK-052 验收：每页 30 条、默认第 1 页，第 2 页
    接着给；超出的页 resets 为空、total 照常。写入顺序打乱，排序不依赖 ID。
    """
    days = [FIRST_DAY + timedelta(days=(i * 7) % 33) for i in range(33)]
    ids: dict[date, int] = {}
    for i, day in enumerate(days):
        result = RESET_FAILED if i % 4 == 0 else RESET_SUCCEEDED
        ids[day] = _add_reset(engine, day, result)
    expected = [ids[day] for day in sorted(ids, reverse=True)]
    assert len(expected) == 33
    assert expected != sorted(expected, reverse=True)

    first = _ok(_list(client, token))
    explicit_first = _ok(_list(client, token, 1))
    second = _ok(_list(client, token, 2))
    third = _ok(_list(client, token, 3))

    assert first == explicit_first
    assert (first["total"], first["page"], first["page_size"]) == (33, 1, 30)
    assert [row["id"] for row in first["resets"]] == expected[:30]
    assert (second["total"], second["page"], second["page_size"]) == (33, 2, 30)
    assert [row["id"] for row in second["resets"]] == expected[30:]
    assert third == {"total": 33, "page": 3, "page_size": 30, "resets": []}
    dates = [row["business_date"] for row in first["resets"] + second["resets"]]
    assert dates == sorted(dates, reverse=True)


def test_list_rows_fields_and_failed_without_completed_at(
    engine: Engine, client: TestClient, token: str
) -> None:
    """A07 每行「<日期> [admin.stock_reset_ok] <n>」「<日期> [admin.stock_reset_failed] <n>」：
    日期、结果与 SKU 数。SHOP-TASK-052 验收：每项恰为 id、business_date（YYYY-MM-DD）、
    result（succeeded 或 failed）、sku_count、completed_at（带 Z 的 UTC，失败时为
    null）；不返回异常类名与尝试次数。
    """
    ok_day = FIRST_DAY + timedelta(days=1)
    ok_id = _add_reset(engine, ok_day, lines=(("MUG-1", 4, 0, None), ("TEE-M", 5, 2, None)))
    failed_id = _add_reset(engine, FIRST_DAY, RESET_FAILED)

    response = _list(client, token)
    body = _ok(response)

    assert body == {
        "total": 2,
        "page": 1,
        "page_size": 30,
        "resets": [
            _row(ok_id, ok_day, RESET_SUCCEEDED, 2),
            _row(failed_id, FIRST_DAY, RESET_FAILED, 0),
        ],
    }
    assert body["resets"][0]["completed_at"] == "2026-09-01T16:01:00Z"
    assert body["resets"][1]["completed_at"] is None
    assert ERROR_CLASS not in response.text
    assert "attempts" not in response.text
    assert "error_class" not in response.text


def test_list_empty(client: TestClient, token: str) -> None:
    """A07「查看…的结果」：还没有任何重置时列表为空。SHOP-TASK-052 验收：total 为 0。"""
    assert _ok(_list(client, token)) == {"total": 0, "page": 1, "page_size": 30, "resets": []}


def test_list_largest_page_is_accepted(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-052 验收：page 为 1 到 10000 的整数；10000 合法，超出时 resets 为空、
    total 照常。
    """
    _add_reset(engine, FIRST_DAY)

    assert _ok(_list(client, token, 10000)) == {
        "total": 1,
        "page": 10000,
        "page_size": 30,
        "resets": [],
    }


@pytest.mark.parametrize("raw", BAD_PAGES)
def test_invalid_page_is_plain_422(
    engine: Engine, client: TestClient, token: str, raw: str
) -> None:
    """SHOP-TASK-052 验收：page 不是 1 到 10000 的整数（不带符号与前导零）时 422，
    detail 为不含输入值的固定错误数组（写法同 app/api/pay.py 的 _language）。
    """
    _add_reset(engine, FIRST_DAY)

    _assert_error(_list(client, token, raw), 422, PAGE_ERROR)


# ---------------------------------------------------------------------------
# 明细
# ---------------------------------------------------------------------------


def test_detail_lines_sorted_by_sku_including_deleted_variant(
    engine: Engine, client: TestClient, token: str
) -> None:
    """A07「▸（展开后）<sku> [admin.stock_reset_breakdown] [M1]」与 M1「当日可用库存 =
    初始库存 − 仍有效预留」：每个 SKU 的初始库存、有效预留与当日可用。SHOP-TASK-052
    验收：lines 按 SKU 升序，每项恰为 sku、initial_stock、held_quantity、
    available_stock；规格已删除（variant_id 为空）的行照常返回；别的日期的明细不混入。
    """
    live = _variant(engine, "TEE-M")
    gone = _variant(engine, "CAP-RED")
    day = FIRST_DAY + timedelta(days=2)
    lines = (
        ("TEE-M", 10, 3, live),
        ("MUG-1", 2, 5, None),
        ("CAP-RED", 6, 0, gone),
        ("BAG-L", 0, 0, live),
    )
    reset_id = _add_reset(engine, day, lines=lines)
    _add_reset(engine, FIRST_DAY, lines=(("AAA-0", 1, 0, None),))
    with Session(engine) as db:
        db.execute(delete(ProductVariant).where(ProductVariant.id == gone))
        db.commit()
        stmt = select(StockResetLine.variant_id).where(StockResetLine.sku == "CAP-RED")
        assert db.scalars(stmt).one() is None

    body = _ok(_detail(client, token, reset_id))

    assert body == {
        **_row(reset_id, day, RESET_SUCCEEDED, 4),
        "lines": [
            _line("BAG-L", 0, 0, 0),
            _line("CAP-RED", 6, 0, 6),
            _line("MUG-1", 2, 5, 0),
            _line("TEE-M", 10, 3, 7),
        ],
    }


def test_detail_of_failed_reset(engine: Engine, client: TestClient, token: str) -> None:
    """A07「<日期> [admin.stock_reset_failed] <n>」：失败的那天照常可查。SHOP-TASK-052
    验收：completed_at 为 null，不返回异常类名与尝试次数；SHOP-TASK-032 失败时回滚
    明细，所以 lines 为空。
    """
    reset_id = _add_reset(engine, FIRST_DAY, RESET_FAILED)

    response = _detail(client, token, reset_id)

    assert _ok(response) == {**_row(reset_id, FIRST_DAY, RESET_FAILED, 0), "lines": []}
    assert ERROR_CLASS not in response.text
    assert "attempts" not in response.text


def test_detail_not_found(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-052 验收：重置不存在时 404 not_found；最大的合法 ID 也是 404 而不是
    422。
    """
    reset_id = _add_reset(engine, FIRST_DAY)

    _assert_error(_detail(client, token, reset_id + 1), 404, NOT_FOUND)
    _assert_error(_detail(client, token, 2**31 - 1), 404, NOT_FOUND)


@pytest.mark.parametrize("raw", BAD_RESET_IDS)
def test_invalid_reset_id_is_plain_422(
    engine: Engine, client: TestClient, token: str, raw: str
) -> None:
    """SHOP-TASK-052 验收：路径 ID 只接受不带符号与前导零、不超过 2147483647 的十进制
    整数，否则 422 reset_id_invalid（固定消息，不回显所给的值）。
    """
    _add_reset(engine, FIRST_DAY)

    _assert_error(_detail(client, token, raw), 422, RESET_ID_ERROR)


# ---------------------------------------------------------------------------
# 只读、no-store、不写审计与日志
# ---------------------------------------------------------------------------


def test_responses_are_no_store_read_only_without_audit_or_logs(
    engine: Engine, client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A07「目的：查看…的结果」与 M1「不改历史订单」：只查看。SHOP-TASK-052 验收：两个
    接口的处理函数与依赖产生的响应（200、401、404、422）都带 no-store；只读，重置记录
    与明细不变；不写审计，应用不记日志。
    """
    reset_id = _add_reset(engine, FIRST_DAY, lines=(("TEE-M", 5, 1, None),))
    _add_reset(engine, FIRST_DAY + timedelta(days=1), RESET_FAILED)
    before = _stored(engine)
    caplog.set_level(logging.DEBUG)

    responses = [
        _list(client, token),
        _list(client, token, 2),
        _list(client, None),
        _list(client, token, "x"),
        _detail(client, token, reset_id),
        _detail(client, None, reset_id),
        _detail(client, token, reset_id + 100),
        _detail(client, token, "x"),
    ]

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 200, 401, 422, 200, 401, 404, 422]
    for response in responses:
        _assert_no_store(response)
    assert _audit_count(engine) == 0
    assert [record for record in caplog.records if record.name.startswith("app")] == []
    assert _stored(engine) == before

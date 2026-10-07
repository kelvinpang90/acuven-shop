"""后台退款申请列表按订单筛选：POST /api/admin/refunds/query 的可选 order_id
（app/api/admin_refunds.py 与 app/services/admin_refunds.py，SHOP-TASK-046）。

依据 docs/HANDOFF.md 0.35 记录的 Kelvin 2026-10-06 决定（下称「Kelvin 2026-10-06」）：
「A02 订单的待审退款数链接到只含该单申请的 A03 列表，为此退款申请列表接口加可选的订单内部 ID
筛选」；同一记录里「路径带内部整数 ID，订单号仍不进网址」。每条测试（参数化的测试是每个用例）的
文档字符串写明它守住的是该决定的哪一项；没有直接原句的，写明是 SHOP-TASK-046 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。管理员、后台会话、
订单与退款申请都直接写库建立，建订单与申请的辅助函数从 tests/test_admin_refunds.py 导入（不改
该文件）。接口的会话依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services.admin_auth import issue_admin_session
from app.services.admin_refunds import query_refunds
from tests.test_admin_refunds import (
    BASE,
    QUERY_URL,
    SESSION_REQUIRED,
    _add_request,
    _assert_error,
    _assert_plain_errors,
    _headers,
    _make_order,
    _now,
    _ok,
    _query,
)

MAX_ORDER_ID = 2**31 - 1
EMPTY_PAGE = {"total": 0, "page": 1, "page_size": 20, "refunds": []}


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
    def _connect(dbapi_connection, _connection_record) -> None:
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
            username="shop_admin",
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


def _ids(page: dict[str, Any]) -> list[int]:
    return [row["id"] for row in page["refunds"]]


def _two_orders(engine: Engine, admin_id: int) -> tuple[int, int, dict[str, int]]:
    """两张订单：第一张有 requested、approved、rejected 各一笔，第二张有一笔 requested。

    申请时间交错，使两单的申请在全部列表里互相穿插。返回两张订单的内部 ID 与各申请的 ID。
    """
    first = _make_order(engine).id
    second = _make_order(engine).id
    requests = {
        "first_requested": _add_request(
            engine, first, ((0, 1),), created_at=BASE + timedelta(minutes=1)
        ),
        "second_requested": _add_request(
            engine, second, ((1, 1),), created_at=BASE + timedelta(minutes=2)
        ),
        "first_approved": _add_request(
            engine,
            first,
            ((0, 1),),
            status=REFUND_APPROVED,
            reviewer_id=admin_id,
            created_at=BASE + timedelta(minutes=3),
        ),
        "first_rejected": _add_request(
            engine,
            first,
            ((1, 1),),
            status=REFUND_REJECTED,
            reviewer_id=admin_id,
            created_at=BASE + timedelta(minutes=4),
        ),
    }
    return first, second, requests


# ---------------------------------------------------------------------------
# 筛选
# ---------------------------------------------------------------------------


def test_filter_returns_only_that_orders_requests(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """Kelvin 2026-10-06「A02 订单的待审退款数链接到只含该单申请的 A03 列表」。
    SHOP-TASK-046 验收：给出 order_id 时只返回该订单的申请，总数为该单的申请数；排序与每行
    字段与不筛选时相同。
    """
    first, second, requests = _two_orders(engine, admin_id)

    everything = _ok(_query(client, token))
    first_page = _ok(_query(client, token, {"order_id": first}))
    second_page = _ok(_query(client, token, {"order_id": second}))

    assert (first_page["total"], first_page["page"], first_page["page_size"]) == (3, 1, 20)
    assert _ids(first_page) == [
        requests["first_rejected"],
        requests["first_approved"],
        requests["first_requested"],
    ]
    assert first_page["refunds"] == [
        row for row in everything["refunds"] if row["order_id"] == first
    ]
    assert second_page["total"] == 1
    assert _ids(second_page) == [requests["second_requested"]]
    assert second_page["refunds"][0]["order_id"] == second


def test_filter_keeps_twenty_per_page(engine: Engine, client: TestClient, token: str) -> None:
    """Kelvin 2026-10-06「为此退款申请列表接口加可选的订单内部 ID 筛选」（只加筛选）。
    SHOP-TASK-046 验收：按订单筛选时排序（申请时间从新到旧、同时按 ID 从大到小）、每页 20 条与
    总数不变。
    """
    first = _make_order(engine).id
    second = _make_order(engine).id
    created = []
    for i in range(22):
        at = BASE + timedelta(minutes=i)
        created.append((at, _add_request(engine, first, created_at=at)))
        _add_request(engine, second, created_at=at)
    expected = [request_id for _, request_id in sorted(created, reverse=True)]

    page_one = _ok(_query(client, token, {"order_id": first}))
    page_two = _ok(_query(client, token, {"order_id": first, "page": 2}))

    assert (page_one["total"], page_one["page"], page_one["page_size"]) == (22, 1, 20)
    assert _ids(page_one) == expected[:20]
    assert (page_two["total"], page_two["page"], page_two["page_size"]) == (22, 2, 20)
    assert _ids(page_two) == expected[20:]


def test_filter_combines_with_status(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """Kelvin 2026-10-06「A02 订单的待审退款数链接到只含该单申请的 A03 列表」（待审即
    requested）。SHOP-TASK-046 验收：order_id 可与状态筛选同时用，两者都满足才列出。
    """
    first, second, requests = _two_orders(engine, admin_id)

    pending = _ok(_query(client, token, {"order_id": first, "status": REFUND_REQUESTED}))
    approved = _ok(_query(client, token, {"status": REFUND_APPROVED, "order_id": first}))
    none_left = _ok(_query(client, token, {"order_id": second, "status": REFUND_REJECTED}))

    assert pending["total"] == 1
    assert _ids(pending) == [requests["first_requested"]]
    assert _ids(approved) == [requests["first_approved"]]
    assert none_left == EMPTY_PAGE


@pytest.mark.parametrize("which", ["missing", "max", "without_requests"])
def test_unknown_order_or_no_requests_is_empty_list(
    engine: Engine, client: TestClient, admin_id: int, token: str, which: str
) -> None:
    """Kelvin 2026-10-06「只含该单申请的 A03 列表」。SHOP-TASK-046 验收：订单不存在或没有
    申请时返回 total 为 0 的空列表而不是 404。
    """
    first, second, _ = _two_orders(engine, admin_id)
    without_requests = _make_order(engine).id
    order_id = {
        "missing": max(first, second, without_requests) + 1,
        "max": MAX_ORDER_ID,
        "without_requests": without_requests,
    }[which]

    assert _ok(_query(client, token, {"order_id": order_id})) == EMPTY_PAGE


@pytest.mark.parametrize("body", [{}, {"order_id": None}, {"order_id": None, "page": 1}])
def test_null_or_absent_lists_everything(
    engine: Engine, client: TestClient, admin_id: int, token: str, body: dict[str, Any]
) -> None:
    """Kelvin 2026-10-06「可选的订单内部 ID 筛选」。SHOP-TASK-046 验收：不给 order_id 或为
    null 时与现在完全相同，列出全部申请。
    """
    _, _, requests = _two_orders(engine, admin_id)

    page = _ok(_query(client, token, body))

    assert page["total"] == 4
    assert _ids(page) == [
        requests["first_rejected"],
        requests["first_approved"],
        requests["second_requested"],
        requests["first_requested"],
    ]
    assert page == _ok(_query(client, token))


def test_service_keyword_defaults_to_no_filter(engine: Engine, admin_id: int) -> None:
    """Kelvin 2026-10-06「为此退款申请列表接口加可选的订单内部 ID 筛选」。SHOP-TASK-046 验收：
    query_refunds 以默认值 None 的关键字参数接收订单 ID，现有调用方式与行为不变。
    """
    first, _, requests = _two_orders(engine, admin_id)

    with Session(engine) as db:
        unchanged = query_refunds(db, None, 1, "en")
        explicit_none = query_refunds(db, None, 1, "en", order_id=None)
        filtered = query_refunds(db, REFUND_REQUESTED, 1, "en", order_id=first)
        with pytest.raises(TypeError):
            # 订单 ID 只能按关键字给。
            query_refunds(db, None, 1, "en", first)

    assert unchanged == explicit_none
    assert unchanged.total == 4
    assert [row.id for row in filtered.refunds] == [requests["first_requested"]]


# ---------------------------------------------------------------------------
# 校验与授权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "secret"),
    [
        (0, None),
        (-987654, "987654"),
        (MAX_ORDER_ID + 1, str(MAX_ORDER_ID + 1)),
        ("secret-order-7781", "secret-order-7781"),
        ("12", None),
        (True, "true"),
        (False, "false"),
        (12.5, "12.5"),
    ],
)
def test_invalid_order_id_is_plain_422(
    engine: Engine, client: TestClient, admin_id: int, token: str, value: Any, secret: str | None
) -> None:
    """Kelvin 2026-10-06「路径带内部整数 ID」（订单内部 ID 是 1 到 INT 上限的整数）。
    SHOP-TASK-046 验收：order_id 为 0、负数、超过上限、字符串、布尔值或小数时 422（沿用请求
    模型的严格类型），错误只给位置、类型与固定消息，不回显所给的值。
    """
    _two_orders(engine, admin_id)

    response = _query(client, token, {"order_id": value})

    _assert_plain_errors(response, secret)
    for error in response.json()["detail"]:
        assert error["loc"][:2] == ["body", "order_id"]


def test_extra_fields_still_422(engine: Engine, client: TestClient, token: str) -> None:
    """Kelvin 2026-10-06「订单号仍不进网址」。SHOP-TASK-046 验收：请求体只加 order_id，
    多出字段（如订单号）仍 422 且不回显所给的值。
    """
    placed = _make_order(engine)

    response = _query(client, token, {"order_id": placed.id, "order_number": placed.number})

    _assert_plain_errors(response, placed.number)


def test_order_id_only_in_body(app: FastAPI) -> None:
    """Kelvin 2026-10-06「路径带内部整数 ID，订单号仍不进网址」。SHOP-TASK-046 验收：订单
    内部 ID 只在请求体里，不进路径或查询参数；列表接口的路径与查询参数不变。
    """
    operations = app.openapi()["paths"][QUERY_URL]

    assert set(operations) == {"post"}
    parameters = operations["post"].get("parameters", [])
    in_url = {(p["in"], p["name"]) for p in parameters if p["in"] in ("path", "query")}
    assert in_url <= {("query", "lang")}


def test_filter_requires_admin_session(engine: Engine, client: TestClient, admin_id: int) -> None:
    """Kelvin 2026-10-06「A03 列表」属于后台。SHOP-TASK-046 验收：带 order_id 时没有有效
    后台会话仍是 401 admin_session_required，响应带 no-store，不含订单号。
    """
    placed = _make_order(engine)
    _add_request(engine, placed.id, ((0, 1),))

    response = client.post(QUERY_URL, json={"order_id": placed.id}, headers=_headers(None))

    _assert_error(response, 401, SESSION_REQUIRED)
    assert placed.number not in response.text


def test_filtered_response_is_no_store(engine: Engine, client: TestClient, token: str) -> None:
    """Kelvin 2026-10-06「A03 列表」属于后台。SHOP-TASK-046 验收：按订单筛选的响应（含空列表）
    仍带 Cache-Control: no-store，接口不要求 CSRF。
    """
    placed = _make_order(engine)
    _add_request(engine, placed.id)

    found = client.post(QUERY_URL, json={"order_id": placed.id}, headers=_headers(token))
    empty = client.post(QUERY_URL, json={"order_id": placed.id + 1}, headers=_headers(token))

    assert _ok(found)["total"] == 1
    assert _ok(empty) == EMPTY_PAGE

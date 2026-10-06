"""后台退款审核接口：POST /api/admin/refunds/query、GET /api/admin/refunds/{refund_id}、
POST /api/admin/refunds/{refund_id}/approve、POST /api/admin/refunds/{refund_id}/reject
（app/api/admin_refunds.py 与 app/services/admin_refunds.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」（requested → 管理员 approved 或
rejected；已批准的退款不可再次批准；系统显示累计已退及剩余可退金额）、「权限与资料保护」
（仅管理员可见所有订单原始资料；后台单一管理员也须认证，不把权限检查留给前端；写操作另须
CSRF 令牌；日志不记录姓名、完整电话、地址与订单查询参数）、「失败、并发与重试」第 1 条（退款
审核使用幂等键和数据库唯一约束，相同键相同请求返回原结果，同键不同内容报冲突），以及
docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定（下称「Kelvin 2026-10-06」：拒绝时审核
理由必填，批准时可空，去掉首尾空白后 1 到 500 个字符；理由只给管理员看；批准与拒绝都带幂等键）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句或 Kelvin 的哪一项决定；
没有直接原句的，写明是 SHOP-TASK-041 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。审核在保存点里
写入，所以按 SQLAlchemy 文档的做法关掉 pysqlite 的事务处理、由引擎发 BEGIN（与
tests/test_refund_review.py 相同）；内存库只有一条共享连接，每个数据库会话用完都结束事务。
管理员、后台会话、订单（含逐件分摊快照与收货资料）与退款申请都直接写库建立。接口的会话依赖
换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动；检查结果一律在另一个数据库
会话里读，读得到即说明接口已提交。期望的 CSRF 令牌在这里另算。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    AuditEvent,
    Order,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.order import ACTOR_GUEST, STATUS_SHIPPED
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.order_rules import generate_order_number

REFUNDS_URL = "/api/admin/refunds"
QUERY_URL = f"{REFUNDS_URL}/query"
GUEST_ORDER_URL = "/api/orders/guest"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

NAME = "Zubaidah Secretname"
PHONE = "+60123456789"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "73915"
REASON = "Secretreason item damaged"

# 订单行：三语名称、三语规格说明、单价、逐件券额。逐件现金实付 = 单价 − 券额。
LINES = [
    (
        ("Tee", "T恤", "Tee"),
        ("Colour: Red", "颜色：红色", "Colour: Red"),
        3000,
        [299, 301, 300],
    ),
    (("Mug", "马克杯", "Mug Kopi"), ("", "", ""), 1500, [0]),
]
SUBTOTAL = 3 * 3000 + 1500
COUPON = 900
SHIPPING = 800
TOTAL = SUBTOTAL - COUPON + SHIPPING

BASE = datetime(2026, 10, 1, 8, 0, 0)

APPROVED_ACTION = "admin_refund_approved"
REJECTED_ACTION = "admin_refund_rejected"
TARGET = "refund_request"
RESULTS = {"approve": REFUND_APPROVED, "reject": REFUND_REJECTED}

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "not_found"}
REASON_INVALID = {"detail": "reason_invalid"}
IDEMPOTENCY_CONFLICT = {"detail": "idempotency_conflict"}
REFUND_ID_ERROR = {
    "type": "refund_id_invalid",
    "loc": ["path", "refund_id"],
    "msg": "Refund request ID should be a positive integer",
}


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

    # 关掉驱动的事务处理、由引擎发 BEGIN，保存点才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

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


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _make_order(engine: Engine, status: str = STATUS_SHIPPED) -> _Placed:
    """直接写一张已支付的游客订单（订单行、逐件分摊快照与收货资料）并提交。"""
    with Session(engine) as db:
        order = Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=COUPON,
            points_redeemed=0,
            shipping_fee_sen=SHIPPING,
            total_sen=TOTAL,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            created_at=BASE,
            payment_expires_at=BASE + timedelta(minutes=15),
            paid_at=BASE + timedelta(minutes=2),
        )
        db.add(order)
        db.flush()
        for line_index, (names, labels, price, coupons) in enumerate(LINES):
            item = OrderItem(
                order_id=order.id,
                line_index=line_index,
                variant_id=None,
                sku=f"SKU-{line_index}",
                product_name_en=names[0],
                product_name_zh=names[1],
                product_name_ms=names[2],
                variant_label_en=labels[0],
                variant_label_zh=labels[1],
                variant_label_ms=labels[2],
                unit_price_sen=price,
                quantity=len(coupons),
                line_subtotal_sen=price * len(coupons),
            )
            db.add(item)
            db.flush()
            for unit_index, coupon in enumerate(coupons):
                db.add(
                    OrderItemUnit(
                        order_item_id=item.id,
                        unit_index=unit_index,
                        original_price_sen=price,
                        coupon_discount_sen=coupon,
                        points_discount=0,
                        cash_paid_sen=price - coupon,
                        points_earned=0,
                    )
                )
        db.add(
            OrderRecipient(
                order_id=order.id,
                name=NAME,
                phone=PHONE,
                country_code="MY",
                region="MY-10",
                address=ADDRESS,
                postal_code=POSTAL,
            )
        )
        db.flush()
        placed = _Placed(id=order.id, number=order.order_number)
        db.commit()
    return placed


def _add_request(
    engine: Engine,
    order_id: int,
    lines: tuple[tuple[int, int], ...] = (),
    *,
    status: str = REFUND_REQUESTED,
    reviewer_id: int | None = None,
    reason: str | None = None,
    created_at: datetime | None = None,
    reviewed_at: datetime | None = None,
) -> int:
    """直接写一笔退款申请并提交，返回申请 ID。

    lines 为（行序, 件数）：按件序取该行尚未被占用的件，与 SHOP-TASK-029 相同。
    approved 与 rejected 另写审核时间、审核人、审核幂等键与审核请求指纹（SHOP-TASK-039 的
    检查约束要求），rejected 必有理由，其件的占用标记为空。
    """
    review: dict[str, Any] = {}
    if status != REFUND_REQUESTED:
        assert reviewer_id is not None
        if status == REFUND_REJECTED and reason is None:
            reason = "Demo rejection"
        review = {
            "reviewed_at": reviewed_at or _now(),
            "reviewer_admin_id": reviewer_id,
            "review_reason": reason,
            "review_idempotency_key": _key(),
            "review_fingerprint": "1" * 64,
        }
    with Session(engine) as db:
        request = RefundRequest(
            order_id=order_id,
            status=status,
            actor_type=ACTOR_GUEST,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            amount_sen=0,
            created_at=created_at or _now(),
            **review,
        )
        db.add(request)
        db.flush()
        taken = select(RefundLineUnit.order_item_unit_id).where(RefundLineUnit.occupied == 1)
        for line_index, quantity in lines:
            item_stmt = select(OrderItem).where(
                OrderItem.order_id == order_id, OrderItem.line_index == line_index
            )
            item = db.scalars(item_stmt).one()
            unit_stmt = (
                select(OrderItemUnit)
                .where(OrderItemUnit.order_item_id == item.id, OrderItemUnit.id.not_in(taken))
                .order_by(OrderItemUnit.unit_index)
                .limit(quantity)
            )
            units = list(db.scalars(unit_stmt))
            assert len(units) == quantity
            amount = sum(unit.cash_paid_sen for unit in units)
            line = RefundLine(
                refund_request_id=request.id,
                order_item_id=item.id,
                quantity=quantity,
                amount_sen=amount,
            )
            db.add(line)
            db.flush()
            for unit in units:
                db.add(
                    RefundLineUnit(
                        refund_line_id=line.id,
                        order_item_unit_id=unit.id,
                        cash_paid_sen=unit.cash_paid_sen,
                        occupied=None if status == REFUND_REJECTED else 1,
                    )
                )
            db.flush()
            request.amount_sen += amount
        request_id = request.id
        db.commit()
    return request_id


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None, key: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _detail_url(refund_id: int | str) -> str:
    return f"{REFUNDS_URL}/{refund_id}"


def _review_url(action: str, refund_id: int | str) -> str:
    return f"{REFUNDS_URL}/{refund_id}/{action}"


def _query(
    client: TestClient, token: str, body: dict[str, Any] | None = None, lang: str | None = None
) -> Any:
    params = {} if lang is None else {"lang": lang}
    body = {} if body is None else body
    return client.post(QUERY_URL, json=body, params=params, headers=_headers(token))


def _detail(client: TestClient, token: str, refund_id: int, lang: str = "en") -> Any:
    return client.get(_detail_url(refund_id), params={"lang": lang}, headers=_headers(token))


def _review(
    client: TestClient,
    token: str,
    action: str,
    refund_id: int | str,
    body: dict[str, Any] | None = None,
    key: str | None = None,
) -> Any:
    headers = _headers(token, _csrf_for(token), key or _key())
    return client.post(_review_url(action, refund_id), json=body or {}, headers=headers)


def _read_review(engine: Engine, request_id: int) -> tuple[Any, ...]:
    """申请的状态、审核时间、审核人、理由、审核幂等键与审核请求指纹。"""
    columns = (
        RefundRequest.status,
        RefundRequest.reviewed_at,
        RefundRequest.reviewer_admin_id,
        RefundRequest.review_reason,
        RefundRequest.review_idempotency_key,
        RefundRequest.review_fingerprint,
    )
    with Session(engine) as db:
        stmt = select(*columns).where(RefundRequest.id == request_id)
        return tuple(db.execute(stmt).one())


def _occupied(engine: Engine, request_id: int) -> list[int | None]:
    """该申请各件的占用标记，按写入顺序。"""
    with Session(engine) as db:
        stmt = (
            select(RefundLineUnit.occupied)
            .join(RefundLine, RefundLine.id == RefundLineUnit.refund_line_id)
            .where(RefundLine.refund_request_id == request_id)
            .order_by(RefundLineUnit.id)
        )
        return list(db.scalars(stmt))


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """审计记录：管理员、操作名、对象类别、对象 ID、旧值、新值，按写入顺序。"""
    with Session(engine) as db:
        rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id))
        return [
            (
                row.admin_account_id,
                row.action,
                row.target_type,
                row.target_id,
                row.old_value,
                row.new_value,
            )
            for row in rows
        ]


def _snapshot(engine: Engine, request_id: int) -> tuple[Any, ...]:
    """申请的审核各列、各件占用与全部审计：用来断言「数据不变」。"""
    return _read_review(engine, request_id), _occupied(engine, request_id), _audits(engine)


@contextmanager
def _sql(engine: Engine) -> Iterator[list[str]]:
    """收集期间执行的 SQL 语句。"""
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def _reads_refunds(statements: list[str]) -> bool:
    return any("refund_requests" in statement for statement in statements)


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


def _assert_no_recipient(content: str) -> None:
    for value in (NAME, PHONE, ADDRESS, POSTAL):
        assert value not in content


def _assert_plain_errors(response: Any, secret: str | None = None) -> None:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    if secret is not None:
        assert secret not in response.text


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
def test_all_endpoints_require_admin_session(
    engine: Engine, client: TestClient, admin_id: int, kind: str
) -> None:
    """「后台单一管理员也须认证，不把权限检查留给前端」。SHOP-TASK-041 验收：四个接口都经
    require_admin，没有有效后台会话时 401 admin_session_required；申请不变、不写审计。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    cookie = _session_cookie(engine, admin_id, kind)
    csrf = None if cookie is None else _csrf_for(cookie)

    responses = [
        client.post(QUERY_URL, json={}, headers=_headers(cookie)),
        client.get(_detail_url(request_id), headers=_headers(cookie)),
        client.post(
            _review_url("approve", request_id), json={}, headers=_headers(cookie, csrf, _key())
        ),
        client.post(
            _review_url("reject", request_id),
            json={"reason": REASON},
            headers=_headers(cookie, csrf, _key()),
        ),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
        _assert_no_recipient(response.text)
        assert placed.number not in response.text
    assert _snapshot(engine, request_id) == before


def test_routes_use_internal_id_only(app: FastAPI) -> None:
    """「首版不提供后台导出」。SHOP-TASK-041 验收：路径只用退款申请的内部整数 ID，订单号与
    收货资料不进路径或查询参数；只有四个接口。
    """
    openapi = app.openapi()
    paths = openapi["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(REFUNDS_URL)
        for method in operations
    }

    assert routes == {
        ("POST", QUERY_URL),
        ("GET", f"{REFUNDS_URL}/{{refund_id}}"),
        ("POST", f"{REFUNDS_URL}/{{refund_id}}/approve"),
        ("POST", f"{REFUNDS_URL}/{{refund_id}}/reject"),
    }
    query_params = {
        parameter["name"]
        for path, operations in paths.items()
        if path.startswith(REFUNDS_URL)
        for operation in operations.values()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "query"
    }
    assert query_params <= {"lang"}


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_newest_first_twenty_per_page(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-041 验收：按申请时间从新到旧、同时按 ID 从大到小，每页 20 条；第 2 页接着给；
    总数为全部符合条件的申请数。
    """
    placed = _make_order(engine)
    created: list[tuple[datetime, int]] = []
    for i in range(23):
        at = BASE + timedelta(minutes=i)
        created.append((at, _add_request(engine, placed.id, created_at=at)))
    # 两笔与第 10 笔同一时间的申请。
    tied_at = BASE + timedelta(minutes=10)
    tied = [_add_request(engine, placed.id, created_at=tied_at) for _ in range(2)]
    created.extend((tied_at, request_id) for request_id in tied)
    expected = [request_id for _, request_id in sorted(created, reverse=True)]

    first = _ok(_query(client, token))
    second = _ok(_query(client, token, {"page": 2}))
    third = _ok(_query(client, token, {"page": 3}))

    assert (first["total"], first["page"], first["page_size"]) == (25, 1, 20)
    assert [row["id"] for row in first["refunds"]] == expected[:20]
    assert (second["total"], second["page"], second["page_size"]) == (25, 2, 20)
    assert [row["id"] for row in second["refunds"]] == expected[20:]
    assert third == {"total": 25, "page": 3, "page_size": 20, "refunds": []}
    tied_positions = [expected.index(request_id) for request_id in sorted(tied, reverse=True)]
    assert tied_positions == sorted(tied_positions)


def test_list_filters_by_status(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「requested → 管理员 approved 或 rejected」。SHOP-TASK-041 验收：状态只允许 requested、
    approved、rejected，按状态精确筛选；不给状态时列出全部。
    """
    placed = _make_order(engine)
    old = _add_request(engine, placed.id, created_at=BASE)
    approved = _add_request(
        engine, placed.id, status=REFUND_APPROVED, reviewer_id=admin_id, created_at=BASE
    )
    new = _add_request(engine, placed.id, created_at=BASE + timedelta(minutes=1))
    rejected = _add_request(
        engine, placed.id, status=REFUND_REJECTED, reviewer_id=admin_id, created_at=BASE
    )

    requested_page = _ok(_query(client, token, {"status": REFUND_REQUESTED}))
    approved_page = _ok(_query(client, token, {"status": REFUND_APPROVED, "page": 1}))
    rejected_page = _ok(_query(client, token, {"status": REFUND_REJECTED}))
    everything = _ok(_query(client, token, {"status": None}))

    assert requested_page["total"] == 2
    assert [row["id"] for row in requested_page["refunds"]] == [new, old]
    assert [row["id"] for row in approved_page["refunds"]] == [approved]
    assert [row["id"] for row in rejected_page["refunds"]] == [rejected]
    assert everything["total"] == 4


def test_list_rows_without_recipient_or_reason(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「仅管理员可见所有订单原始资料」与 Kelvin 2026-10-06「理由只给管理员看」。
    SHOP-TASK-041 验收：每行只有 ID、申请时间、所属订单的内部 ID 与订单号、状态、申请金额
    （整数仙）与各行按语言参数取的商品名称和件数；不返回收货资料与理由；不写审计。
    """
    placed = _make_order(engine)
    first_at = BASE + timedelta(hours=1)
    second_at = BASE + timedelta(hours=2)
    first = _add_request(
        engine,
        placed.id,
        ((0, 2),),
        status=REFUND_REJECTED,
        reviewer_id=admin_id,
        reason=REASON,
        created_at=first_at,
    )
    second = _add_request(engine, placed.id, ((1, 1), (0, 1)), created_at=second_at)

    response = _query(client, token, lang="zh")
    body = _ok(response)

    assert body == {
        "total": 2,
        "page": 1,
        "page_size": 20,
        "refunds": [
            {
                "id": second,
                "created_at": _iso(second_at),
                "order_id": placed.id,
                "order_number": placed.number,
                "status": REFUND_REQUESTED,
                # 被拒的申请已释放件：本申请取到第 1 行的第 1 件（2701）与第 2 行的 1 件。
                "amount_sen": 2701 + 1500,
                "lines": [{"name": "T恤", "quantity": 1}, {"name": "马克杯", "quantity": 1}],
            },
            {
                "id": first,
                "created_at": _iso(first_at),
                "order_id": placed.id,
                "order_number": placed.number,
                "status": REFUND_REJECTED,
                "amount_sen": 2701 + 2699,
                "lines": [{"name": "T恤", "quantity": 2}],
            },
        ],
    }
    _assert_no_recipient(response.text)
    assert REASON not in response.text
    assert _audits(engine) == []


def test_list_language_defaults_to_english(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-041 验收：商品名称按语言参数取快照（语言参数与目录接口相同，默认 en）。"""
    placed = _make_order(engine)
    _add_request(engine, placed.id, ((1, 1),))

    default = _ok(_query(client, token))
    malay = _ok(_query(client, token, lang="ms"))

    assert default["refunds"][0]["lines"] == [{"name": "Mug", "quantity": 1}]
    assert malay["refunds"][0]["lines"] == [{"name": "Mug Kopi", "quantity": 1}]


@pytest.mark.parametrize(
    "body",
    [
        {"status": REFUND_REQUESTED, "extra": "SECRETVALUE"},
        {"order_number": "SECRETVALUE"},
        {"status": "SECRETVALUE"},
        {"status": "demo_paid"},
        {"page": 0},
        {"page": 10001},
        {"page": "2"},
        {"page": True},
        {"page": 1.5},
        {"page": None},
    ],
)
def test_query_body_errors_are_plain(
    engine: Engine, client: TestClient, token: str, body: dict[str, Any]
) -> None:
    """SHOP-TASK-041 验收：列表的字段只有可选的状态（只允许 requested、approved、rejected）与
    页码（1 到 10000 的整数），多出字段或不合法 422；每条错误只给位置、类型与固定消息，
    不回显请求内容。
    """
    _assert_plain_errors(_query(client, token, body), "SECRETVALUE")


def test_query_page_bounds_are_accepted(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-041 验收：页码 1 到 10000 都合法，默认 1。"""
    placed = _make_order(engine)
    _add_request(engine, placed.id)

    assert _ok(_query(client, token, {"page": 1}))["total"] == 1
    assert _ok(_query(client, token, {"page": 10000}))["refunds"] == []


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------


def test_detail_fields_amounts_and_reviewer(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「系统显示累计已退及剩余可退金额」「示例运费始终不退」；Kelvin 2026-10-06「理由只给
    管理员看」（审核理由在后台详情返回）。SHOP-TASK-041 验收：详情返回申请 ID、状态、申请与
    审核时间、审核人用户名、审核理由、所属订单的内部 ID、订单号与订单状态、申请金额、各行名称、
    规格说明、申请件数、该行购买件数与该行已批准件数、累计已退与剩余可退（SHOP-TASK-029 的
    refund_state）与由 cookie 算出的 CSRF 令牌。
    """
    placed = _make_order(engine)
    approved_at = BASE + timedelta(hours=1)
    reviewed_at = BASE + timedelta(hours=3)
    requested_at = BASE + timedelta(hours=2)
    approved = _add_request(
        engine,
        placed.id,
        ((0, 2),),
        status=REFUND_APPROVED,
        reviewer_id=admin_id,
        reason=REASON,
        created_at=approved_at,
        reviewed_at=reviewed_at,
    )
    pending = _add_request(engine, placed.id, ((0, 1),), created_at=requested_at)

    approved_body = _ok(_detail(client, token, approved, "zh"))
    pending_body = _ok(_detail(client, token, pending, "en"))

    # 累计已退：已批准的 2701 + 2699；剩余可退：未被占用的只有第 2 行那件 1500。
    assert approved_body == {
        "id": approved,
        "status": REFUND_APPROVED,
        "created_at": _iso(approved_at),
        "reviewed_at": _iso(reviewed_at),
        "reviewer_username": "shop_admin",
        "review_reason": REASON,
        "order_id": placed.id,
        "order_number": placed.number,
        "order_status": STATUS_SHIPPED,
        "amount_sen": 2701 + 2699,
        "lines": [
            {
                "name": "T恤",
                "variant_label": "颜色：红色",
                "quantity": 2,
                "purchased_quantity": 3,
                "approved_quantity": 2,
            }
        ],
        "refunded_sen": 2701 + 2699,
        "refundable_left_sen": 1500,
        "csrf_token": _csrf_for(token),
    }
    assert pending_body == {
        "id": pending,
        "status": REFUND_REQUESTED,
        "created_at": _iso(requested_at),
        "reviewed_at": None,
        "reviewer_username": None,
        "review_reason": None,
        "order_id": placed.id,
        "order_number": placed.number,
        "order_status": STATUS_SHIPPED,
        "amount_sen": 2700,
        "lines": [
            {
                "name": "Tee",
                "variant_label": "Colour: Red",
                "quantity": 1,
                "purchased_quantity": 3,
                "approved_quantity": 2,
            }
        ],
        "refunded_sen": 2701 + 2699,
        "refundable_left_sen": 1500,
        "csrf_token": _csrf_for(token),
    }


def test_detail_has_no_recipient_and_writes_no_audit(
    engine: Engine, client: TestClient, token: str
) -> None:
    """「仅管理员可见所有订单原始资料」：退款审核用不到收货资料。SHOP-TASK-041 验收：详情不
    返回收货资料，不写审计。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1), (1, 1)))

    response = _detail(client, token, request_id)

    _ok(response)
    _assert_no_recipient(response.text)
    assert "recipient" not in response.json()
    assert _audits(engine) == []


def test_detail_not_found(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-041 验收：申请不存在 404 not_found；最大的合法 ID 也是 404 而不是 422。"""
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id)

    for refund_id in (request_id + 1, 2**31 - 1):
        _assert_error(_detail(client, token, refund_id), 404, NOT_FOUND)


@pytest.mark.parametrize(
    "raw",
    ["abc", "0", "-1", "01", "1.5", "+1", "1e3", "2147483648", "99999999999999999999", "query"],
)
def test_invalid_refund_id_in_detail_path_is_plain_422(
    engine: Engine, client: TestClient, token: str, raw: str
) -> None:
    """SHOP-TASK-041 验收：路径只用退款申请的内部整数 ID；不合法时 422，只含位置、类型与固定
    消息，不含所给的值。
    """
    response = client.get(_detail_url(raw), headers=_headers(token))

    _assert_error(response, 422, {"detail": [REFUND_ID_ERROR]})


def test_invalid_language_is_plain_422(engine: Engine, client: TestClient, token: str) -> None:
    """SHOP-TASK-041 验收：语言参数与目录接口相同，不合法时 422 只含位置、类型与固定消息，
    不含所给的值。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id)

    for response in (
        _detail(client, token, request_id, "SECRETVALUE"),
        _query(client, token, lang="SECRETVALUE"),
    ):
        _assert_error(
            response,
            422,
            {
                "detail": [
                    {
                        "type": "literal_error",
                        "loc": ["query", "lang"],
                        "msg": "Input should be 'en', 'zh' or 'ms'",
                    }
                ]
            },
        )


# ---------------------------------------------------------------------------
# 批准与拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"reason": None}, {"reason": ""}, {"reason": "   "}])
def test_approve_without_reason(
    engine: Engine, client: TestClient, admin_id: int, token: str, body: dict[str, Any]
) -> None:
    """「requested → 管理员 approved」；Kelvin 2026-10-06：批准时理由可空，没有理由存空值。
    SHOP-TASK-041 验收：调用 SHOP-TASK-040 的函数并在成功后提交，返回 200 与申请当前状态；
    写一条审计；订单状态不变，件保持占用。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 2),))
    key = _key()

    result = _ok(_review(client, token, "approve", request_id, body, key))

    status, reviewed_at, reviewer, reason, stored_key, fingerprint = _read_review(
        engine, request_id
    )
    assert (status, reviewer, reason, stored_key) == (REFUND_APPROVED, admin_id, None, key)
    assert fingerprint is not None and len(fingerprint) == 64
    assert result == {
        "refund_request_id": request_id,
        "status": REFUND_APPROVED,
        "reviewed_at": _iso(reviewed_at),
        "review_reason": None,
    }
    assert _occupied(engine, request_id) == [1, 1]
    assert _audits(engine) == [
        (admin_id, APPROVED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_APPROVED)
    ]
    with Session(engine) as db:
        order_status = db.scalars(select(Order.status).where(Order.id == placed.id)).one()
    assert order_status == STATUS_SHIPPED


def test_approve_with_reason_strips_whitespace(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """Kelvin 2026-10-06：批准时可写理由，去掉首尾空白后 1 到 500 个字符。"""
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((1, 1),))

    result = _ok(_review(client, token, "approve", request_id, {"reason": f"  {REASON}  "}))

    assert result["review_reason"] == REASON
    assert _read_review(engine, request_id)[3] == REASON


def test_reject_releases_units_and_writes_audit(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「requested → 管理员 rejected」；Kelvin 2026-10-06：拒绝时审核理由必填。
    SHOP-TASK-041 验收：拒绝成功后状态、理由与审计正确（审计不含理由原文），并提交；
    被拒申请的件被释放（SHOP-TASK-040）。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1), (1, 1)))
    key = _key()

    result = _ok(_review(client, token, "reject", request_id, {"reason": REASON}, key))

    status, reviewed_at, reviewer, reason, stored_key, _ = _read_review(engine, request_id)
    assert (status, reviewer, reason, stored_key) == (REFUND_REJECTED, admin_id, REASON, key)
    assert result == {
        "refund_request_id": request_id,
        "status": REFUND_REJECTED,
        "reviewed_at": _iso(reviewed_at),
        "review_reason": REASON,
    }
    assert _occupied(engine, request_id) == [None, None]
    audits = _audits(engine)
    assert audits == [
        (admin_id, REJECTED_ACTION, TARGET, request_id, REFUND_REQUESTED, REFUND_REJECTED)
    ]
    assert REASON not in json.dumps(audits)


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("reject", {}),
        ("reject", {"reason": None}),
        ("reject", {"reason": ""}),
        ("reject", {"reason": " \t\n "}),
        ("reject", {"reason": "SECRETVALUE" * 46}),
        ("approve", {"reason": "SECRETVALUE" * 46}),
    ],
)
def test_invalid_reason_is_422_and_changes_nothing(
    engine: Engine, client: TestClient, token: str, action: str, body: dict[str, Any]
) -> None:
    """Kelvin 2026-10-06：拒绝时审核理由必填，批准时可空，去掉首尾空白后 1 到 500 个字符。
    SHOP-TASK-041 验收：理由不合法 422 reason_invalid，不回显理由，数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)

    response = _review(client, token, action, request_id, body)

    _assert_error(response, 422, REASON_INVALID)
    assert "SECRETVALUE" not in response.text
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_same_key_replay_returns_original_once(
    engine: Engine, client: TestClient, admin_id: int, token: str, action: str
) -> None:
    """「失败、并发与重试」第 1 条：退款审核使用幂等键和数据库唯一约束，相同键相同请求返回
    原结果。SHOP-TASK-041 验收：同键同请求重放 200 与原结果，不重复写入。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    key = _key()

    first = _ok(_review(client, token, action, request_id, {"reason": REASON}, key))
    after_first = _snapshot(engine, request_id)
    second = _ok(_review(client, token, action, request_id, {"reason": REASON}, key))

    assert second == first
    assert first["status"] == RESULTS[action]
    assert _snapshot(engine, request_id) == after_first
    assert len(_audits(engine)) == 1


@pytest.mark.parametrize(
    ("first", "second", "same_request"),
    [
        (("approve", {"reason": "Fine"}), ("approve", {"reason": "Other"}), True),
        (("approve", {}), ("reject", {"reason": REASON}), True),
        (("reject", {"reason": REASON}), ("approve", {}), True),
        (("approve", {}), ("approve", {}), False),
    ],
)
def test_same_key_different_content_is_409(
    engine: Engine,
    client: TestClient,
    token: str,
    first: tuple[str, dict[str, Any]],
    second: tuple[str, dict[str, Any]],
    same_request: bool,
) -> None:
    """「失败、并发与重试」第 1 条：同键不同内容报冲突。SHOP-TASK-041 验收：同键不同内容（不同
    理由、相反结果或另一笔申请）409 idempotency_conflict，数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    other_id = _add_request(engine, placed.id, ((1, 1),))
    target = request_id if same_request else other_id
    key = _key()
    _ok(_review(client, token, first[0], request_id, first[1], key))
    before = (_snapshot(engine, request_id), _snapshot(engine, other_id))

    response = _review(client, token, second[0], target, second[1], key)

    _assert_error(response, 409, IDEMPOTENCY_CONFLICT)
    assert (_snapshot(engine, request_id), _snapshot(engine, other_id)) == before


@pytest.mark.parametrize("first", ["approve", "reject"])
@pytest.mark.parametrize("second", ["approve", "reject"])
def test_new_key_on_reviewed_request_is_409(
    engine: Engine, client: TestClient, token: str, first: str, second: str
) -> None:
    """「已批准的退款不可再次批准」与「requested → 管理员 approved 或 rejected」（审核只做一次）。
    SHOP-TASK-041 验收：用新键审核已审核的申请 409 refund_already_reviewed 并带当前状态，
    数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    _ok(_review(client, token, first, request_id, {"reason": REASON}))
    before = _snapshot(engine, request_id)

    response = _review(client, token, second, request_id, {"reason": REASON})

    _assert_error(response, 409, {"detail": "refund_already_reviewed", "status": RESULTS[first]})
    assert REASON not in response.text
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_unknown_request_is_404(
    engine: Engine, client: TestClient, token: str, action: str
) -> None:
    """SHOP-TASK-041 验收：申请不存在 404 not_found，不写审计。"""
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id)

    for refund_id in (request_id + 1, 2**31 - 1):
        response = _review(client, token, action, refund_id, {"reason": REASON})
        _assert_error(response, 404, NOT_FOUND)
    assert _audits(engine) == []


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("key_case", ["missing", "short", "bad_chars", "duplicate"])
def test_idempotency_key_errors_match_guest_order(
    engine: Engine, client: TestClient, token: str, action: str, key_case: str
) -> None:
    """「失败、并发与重试」第 1 条：审核使用幂等键。SHOP-TASK-041 验收：须带 Idempotency-Key
    （规则同 SHOP-TASK-020），缺失或不合法时的错误与游客下单接口相同；不读申请、数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    key_headers: list[tuple[str, str]] = []
    if key_case == "short":
        key_headers = [("Idempotency-Key", "short")]
    elif key_case == "bad_chars":
        key_headers = [("Idempotency-Key", "SECRETVALUE*key*0123456789")]
    elif key_case == "duplicate":
        key_headers = [("Idempotency-Key", _key())] * 2
    headers = [
        ("Cookie", f"{COOKIE_NAME}={token}"),
        ("X-CSRF-Token", _csrf_for(token)),
        *key_headers,
    ]

    guest = client.post(GUEST_ORDER_URL, json={}, headers=key_headers)
    expected = [error for error in guest.json()["detail"] if error["loc"][0] == "header"]
    with _sql(engine) as statements:
        response = client.post(
            _review_url(action, request_id), json={"reason": REASON}, headers=headers
        )

    assert guest.status_code == 422
    assert expected
    _assert_error(response, 422, {"detail": expected})
    assert "SECRETVALUE" not in response.text
    assert not _reads_refunds(statements)
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("csrf", ["missing", "wrong", "other_session", "duplicate"])
def test_csrf_failure_changes_nothing(
    engine: Engine, client: TestClient, admin_id: int, token: str, action: str, csrf: str
) -> None:
    """「写操作另须 CSRF 令牌」（后台写操作同样）。SHOP-TASK-041 验收：缺或错 CSRF 403
    csrf_failed，先于读取申请；数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    with Session(engine) as db:
        other = issue_admin_session(db, admin_id, _now() - timedelta(minutes=1)).token
        db.commit()
    headers: list[tuple[str, str]] = [
        ("Cookie", f"{COOKIE_NAME}={token}"),
        ("Idempotency-Key", _key()),
    ]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", "0" * 64))
    elif csrf == "other_session":
        headers.append(("X-CSRF-Token", _csrf_for(other)))
    elif csrf == "duplicate":
        headers.extend([("X-CSRF-Token", _csrf_for(token))] * 2)

    with _sql(engine) as statements:
        response = client.post(
            _review_url(action, request_id), json={"reason": REASON}, headers=headers
        )

    _assert_error(response, 403, CSRF_FAILED)
    assert not _reads_refunds(statements)
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize(
    "raw",
    [
        b'{"reason": "x", "extra": "SECRETVALUE"}',
        b'{"reason": 12345}',
        b'{"reason": ["SECRETVALUE"]}',
        b'{"reason": "SECRETVALUE"',
        b'["SECRETVALUE"]',
        b"",
    ],
)
def test_review_body_errors_are_plain(
    engine: Engine, client: TestClient, token: str, action: str, raw: bytes
) -> None:
    """SHOP-TASK-041 验收：请求体只有理由一个字段，多出字段或结构不合法 422（沿用 _body_errors，
    detail 为不含输入值的错误数组），不回显请求内容；不读申请，数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    headers = _headers(token, _csrf_for(token), _key())
    headers["Content-Type"] = "application/json"

    with _sql(engine) as statements:
        response = client.post(_review_url(action, request_id), content=raw, headers=headers)

    _assert_plain_errors(response, "SECRETVALUE")
    assert not _reads_refunds(statements)
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("raw", ["abc", "0", "-1", "01", "2147483648"])
def test_invalid_refund_id_in_review_path_is_plain_422(
    engine: Engine, client: TestClient, token: str, action: str, raw: str
) -> None:
    """SHOP-TASK-041 验收：路径只用退款申请的内部整数 ID；审核路径里的 ID 不合法时同样 422，
    固定消息，不写入。
    """
    response = _review(client, token, action, raw, {"reason": REASON})

    _assert_error(response, 422, {"detail": [REFUND_ID_ERROR]})
    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 请求格式与检查顺序
# ---------------------------------------------------------------------------


def _post_raw(
    client: TestClient,
    url: str,
    content: bytes,
    content_type: str | None,
    token: str | None,
) -> Any:
    headers = _headers(token, None if token is None else _csrf_for(token), _key())
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.post(url, content=content, headers=headers)


def _post_urls(request_id: int) -> list[str]:
    return [QUERY_URL, _review_url("approve", request_id), _review_url("reject", request_id)]


@pytest.mark.parametrize("which", [0, 1, 2])
@pytest.mark.parametrize("logged_in", [True, False])
@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_non_json_is_415(
    engine: Engine,
    client: TestClient,
    token: str,
    which: int,
    logged_in: bool,
    content_type: str | None,
) -> None:
    """SHOP-TASK-041 验收：三个 POST 接口只接受 JSON，否则 415；先于会话校验；数据不变。"""
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    content = json.dumps({"reason": REASON}).encode()

    response = _post_raw(
        client, _post_urls(request_id)[which], content, content_type, token if logged_in else None
    )

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert REASON not in response.text
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("which", [0, 1, 2])
@pytest.mark.parametrize("logged_in", [True, False])
@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
def test_oversized_body_is_413_first(
    engine: Engine,
    client: TestClient,
    token: str,
    which: int,
    logged_in: bool,
    content_type: str,
) -> None:
    """SHOP-TASK-041 验收：请求体上限 8 KB 且先于其他校验（多余字段、不是 JSON、没有后台会话
    时都是 413）；不回显请求内容，数据不变。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    before = _snapshot(engine, request_id)
    content = json.dumps({"reason": "SECRETVALUE" * 1000}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _post_raw(
        client, _post_urls(request_id)[which], content, content_type, token if logged_in else None
    )

    _assert_error(response, 413, {"detail": "request body too large"})
    assert "SECRETVALUE" not in response.text
    assert _snapshot(engine, request_id) == before


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_check_order(engine: Engine, client: TestClient, token: str, action: str) -> None:
    """SHOP-TASK-041 验收：检查顺序固定为 413 → 415 → 401 → 403 → 422，之后才读取申请。
    每一步用同时违反后面几条的请求验证它先回答。
    """
    placed = _make_order(engine)
    request_id = _add_request(engine, placed.id, ((0, 1),))
    url = _review_url(action, request_id)
    before = _snapshot(engine, request_id)
    bad_body = json.dumps({"extra": "SECRETVALUE"}).encode()
    cookie = {"Cookie": f"{COOKIE_NAME}={token}"}

    # 没有会话、没有 CSRF、没有幂等键、多余字段：415 先于 401。
    response = client.post(url, content=bad_body, headers={"Content-Type": "text/plain"})
    _assert_error(response, 415, {"detail": "request body must be JSON"})
    # 是 JSON，但没有会话：401 先于 403 与 422。
    response = client.post(url, content=bad_body, headers={"Content-Type": "application/json"})
    _assert_error(response, 401, SESSION_REQUIRED)
    # 有会话、没有 CSRF：403 先于 422。
    response = client.post(
        url, content=bad_body, headers={**cookie, "Content-Type": "application/json"}
    )
    _assert_error(response, 403, CSRF_FAILED)
    # CSRF 通过、申请不存在、请求体与幂等键都不合法：422 先于读取申请（不是 404）。
    headers = {**cookie, "X-CSRF-Token": _csrf_for(token), "Content-Type": "application/json"}
    with _sql(engine) as statements:
        response = client.post(
            _review_url(action, request_id + 1), content=bad_body, headers=headers
        )
    _assert_plain_errors(response, "SECRETVALUE")
    assert {tuple(error["loc"]) for error in response.json()["detail"]} == {
        ("header", "Idempotency-Key"),
        ("body", "extra"),
    }
    assert not _reads_refunds(statements)
    assert _snapshot(engine, request_id) == before


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(
    engine: Engine, client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」。SHOP-TASK-041 验收：四个接口
    （成功与失败）都不写日志。
    """
    placed = _make_order(engine)
    first = _add_request(engine, placed.id, ((0, 1),))
    second = _add_request(engine, placed.id, ((1, 1),))
    caplog.set_level(logging.DEBUG)

    _query(client, token)
    _query(client, token, {"extra": 1})
    _detail(client, token, first)
    _detail(client, token, second + 1)
    _review(client, token, "approve", first, {"reason": REASON})
    _review(client, token, "approve", first, {"reason": REASON})
    _review(client, token, "reject", second, {})
    _review(client, token, "reject", second, {"reason": REASON})
    client.post(_review_url("reject", second), json={}, headers=_headers(token))

    assert [record for record in caplog.records if record.name.startswith("app")] == []

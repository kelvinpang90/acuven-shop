"""后台订单接口：POST /api/admin/orders/query、GET /api/admin/orders/{order_id}、
POST /api/admin/orders/{order_id}/status（app/api/admin_orders.py 与
app/services/admin_orders.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」（管理员依次推进
demo_paid → demo_packed → demo_shipped；所有购买件数均已批准退款时冻结后续推进；
所有状态迁移校验当前状态与操作者）、「权限与资料保护」（仅管理员可见所有订单原始
资料，访问受服务端权限控制并留审计记录，首版不提供后台导出；日志不记录姓名、完整
电话、地址与订单查询参数）与「数据模型」（事件不写收货资料原文），以及
docs/HANDOFF.md 记录的 Kelvin 2026-10-05 决定（下称「Kelvin 2026-10-05」：列表按
下单时间从新到旧、每页 20 条，订单号先按查单规则规范化再精确匹配；全部退款后冻结
履约按 SHOP-TASK-029 的全部已退判定）。每条测试（参数化的测试是每个用例）的文档
字符串写明它守住的设计原句或 Kelvin 的哪一项决定；没有直接原句的，写明是
SHOP-TASK-037 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）。管理员、
后台会话、订单（含逐件分摊快照、收货资料与事件）与退款申请都直接写库建立。接口的会话
依赖换成每个请求一个绑定同一内存库的会话，关闭时回滚未提交的改动；检查结果一律在另一个
数据库会话里读，读得到即说明接口已提交。期望的 CSRF 令牌在这里另算。

并发：内存库只有一个连接，pysqlite 在第一条写语句前才开始事务，所以在推进请求还只做过
查询时（读完退款占用之后、条件 UPDATE 之前），用第二个数据库会话推进同一订单并提交，
就能模拟并发的推进先提交。SQLite 不支持 FOR UPDATE，锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Select, create_engine, event, select, text
from sqlalchemy.dialects import mysql
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
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.order import (
    ACTOR_ADMIN,
    ACTOR_GUEST,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services import admin_orders as admin_orders_service
from app.services.admin_auth import issue_admin_session, revoke_admin_session
from app.services.order_rules import generate_order_number

ORDERS_URL = "/api/admin/orders"
QUERY_URL = f"{ORDERS_URL}/query"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

NAME = "Zubaidah Secretname"
PHONE = "+60123456789"
ADDRESS = "77 Hiddenlane Road"
POSTAL = "73915"

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
# 规范化测试用的订单号：含 1 与 0，输入里可写成 I、L 与 O。
NUMBER = "10ABCDEFGH1JK0MN"

VIEWED = "admin_order_viewed"
CHANGED = "admin_order_status_changed"
SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
NOT_FOUND = {"detail": "not_found"}
ORDER_ID_ERROR = {
    "detail": [
        {
            "type": "order_id_invalid",
            "loc": ["path", "order_id"],
            "msg": "Order ID should be a positive integer",
        }
    ]
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

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session


@pytest.fixture
def account_id(db: Session) -> int:
    row = AdminAccount(
        username="shop_admin",
        password_hash="not-a-real-hash",
        created_at=_now() - timedelta(days=1),
        password_updated_at=_now() - timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row.id


@pytest.fixture
def token(db: Session, account_id: int) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    issued = issue_admin_session(db, account_id, _now() - timedelta(minutes=1))
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
    created_at: datetime
    paid_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _make_order(
    db: Session,
    status: str = STATUS_PAID,
    *,
    created_at: datetime = BASE,
    number: str | None = None,
) -> _Placed:
    """直接写一张订单（订单行、逐件分摊快照、收货资料与事件）并提交。

    已支付及之后的状态在创建后 2 分钟支付，另写一条 guest 的支付事件。
    """
    paid_at = created_at + timedelta(minutes=2) if status in PAID_STATUSES else None
    order = Order(
        order_number=number or generate_order_number(),
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
        created_at=created_at,
        payment_expires_at=created_at + timedelta(minutes=15),
        paid_at=paid_at,
    )
    _add(db, order)
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
        _add(db, item)
        for unit_index, coupon in enumerate(coupons):
            unit = OrderItemUnit(
                order_item_id=item.id,
                unit_index=unit_index,
                original_price_sen=price,
                coupon_discount_sen=coupon,
                points_discount=0,
                cash_paid_sen=price - coupon,
                points_earned=0,
            )
            _add(db, unit)
    recipient = OrderRecipient(
        order_id=order.id,
        name=NAME,
        phone=PHONE,
        country_code="MY",
        region="MY-10",
        address=ADDRESS,
        postal_code=POSTAL,
    )
    _add(db, recipient)
    _add(db, _event(order.id, None, STATUS_AWAITING_PAYMENT, ACTOR_GUEST, created_at))
    if paid_at is not None:
        _add(db, _event(order.id, STATUS_AWAITING_PAYMENT, STATUS_PAID, ACTOR_GUEST, paid_at))
    placed = _Placed(
        id=order.id,
        number=order.order_number,
        created_at=created_at,
        paid_at=paid_at,
    )
    db.commit()
    return placed


def _event(
    order_id: int, from_status: str | None, to_status: str, actor: str, at: datetime
) -> OrderEvent:
    return OrderEvent(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor,
        created_at=at,
    )


def _add_refund(
    db: Session, order_id: int, status: str, *, line_indexes: tuple[int, ...] = ()
) -> None:
    """写一笔退款申请并提交；line_indexes 里的各行全部件都被它占用（占用标记 1）。"""
    reviewed_at = None if status == REFUND_REQUESTED else _now()
    request = RefundRequest(
        order_id=order_id,
        status=status,
        actor_type=ACTOR_GUEST,
        idempotency_key=_key(),
        request_fingerprint="0" * 64,
        amount_sen=0,
        created_at=_now(),
        reviewed_at=reviewed_at,
    )
    _add(db, request)
    stmt = select(OrderItem).where(
        OrderItem.order_id == order_id, OrderItem.line_index.in_(line_indexes)
    )
    for item in db.scalars(stmt).all():
        unit_stmt = select(OrderItemUnit).where(OrderItemUnit.order_item_id == item.id)
        units = list(db.scalars(unit_stmt))
        amount = sum(unit.cash_paid_sen for unit in units)
        line = RefundLine(
            refund_request_id=request.id,
            order_item_id=item.id,
            quantity=len(units),
            amount_sen=amount,
        )
        _add(db, line)
        for unit in units:
            _add(
                db,
                RefundLineUnit(
                    refund_line_id=line.id,
                    order_item_unit_id=unit.id,
                    cash_paid_sen=unit.cash_paid_sen,
                    occupied=1,
                ),
            )
        request.amount_sen += amount
    db.commit()


def _refund_all(db: Session, order_id: int) -> None:
    """模拟之后的审核任务已批准全部件：一笔 approved 申请占用两行的全部件。"""
    _add_refund(db, order_id, REFUND_APPROVED, line_indexes=(0, 1))


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = None) -> dict[str, str]:
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _detail_url(order_id: int | str) -> str:
    return f"{ORDERS_URL}/{order_id}"


def _status_url(order_id: int | str) -> str:
    return f"{ORDERS_URL}/{order_id}/status"


def _query(client: TestClient, token: str, body: dict[str, Any] | None = None) -> Any:
    return client.post(QUERY_URL, json={} if body is None else body, headers=_headers(token))


def _detail(client: TestClient, token: str, order_id: int, lang: str = "en") -> Any:
    return client.get(_detail_url(order_id), params={"lang": lang}, headers=_headers(token))


def _advance(client: TestClient, token: str, order_id: int, status: str) -> Any:
    headers = _headers(token, _csrf_for(token))
    return client.post(_status_url(order_id), json={"status": status}, headers=headers)


def _read[R](engine: Engine, action: Callable[[Session], R]) -> R:
    """在另一个数据库会话里读：读得到的只有接口已提交的改动。"""
    with Session(engine) as session:
        return action(session)


def _audits(engine: Engine) -> list[tuple[Any, ...]]:
    """审计记录：管理员、操作名、对象类别、对象 ID、旧值、新值，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        rows = session.scalars(select(AuditEvent).order_by(AuditEvent.id))
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

    return _read(engine, read)


def _events(engine: Engine, order_id: int) -> list[tuple[Any, ...]]:
    """该单的事件：迁移前状态、迁移后状态、操作者类别，按写入顺序。"""

    def read(session: Session) -> list[tuple[Any, ...]]:
        stmt = (
            select(OrderEvent.from_status, OrderEvent.to_status, OrderEvent.actor_type)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.id)
        )
        return [tuple(row) for row in session.execute(stmt)]

    return _read(engine, read)


def _status(engine: Engine, order_id: int) -> str:
    def read(session: Session) -> str:
        return session.scalars(select(Order.status).where(Order.id == order_id)).one()

    return _read(engine, read)


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


def _assert_conflict(response: Any, code: str, status: str) -> None:
    _assert_error(response, 409, {"detail": code, "status": status})


def _assert_no_recipient(content: str) -> None:
    for value in (NAME, PHONE, ADDRESS, POSTAL):
        assert value not in content


def _assert_plain_errors(response: Any, secret: str | None = None) -> None:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    if secret is not None:
        assert secret not in response.text


# ---------------------------------------------------------------------------
# 授权
# ---------------------------------------------------------------------------


def _session_cookie(db: Session, account_id: int, kind: str) -> str | None:
    if kind == "none":
        return None
    if kind == "unknown":
        # 格式合法但库里没有的令牌。
        return secrets.token_urlsafe(32)
    if kind == "expired":
        issued = issue_admin_session(db, account_id, _now() - timedelta(days=31))
    else:
        issued = issue_admin_session(db, account_id, _now() - timedelta(minutes=5))
        revoke_admin_session(db, issued.session, _now() - timedelta(minutes=1))
    db.commit()
    return issued.token


@pytest.mark.parametrize("kind", ["none", "unknown", "expired", "revoked"])
def test_all_endpoints_require_admin_session(
    db: Session, client: TestClient, engine: Engine, account_id: int, kind: str
) -> None:
    """「仅管理员可见所有订单原始资料……推进发货」「后台单一管理员也须认证，不把权限检查
    留给前端」。SHOP-TASK-037 验收：三个接口都经 require_admin，没有有效后台会话时
    401 admin_session_required；不读订单、不写审计。
    """
    placed = _make_order(db, STATUS_PAID)
    cookie = _session_cookie(db, account_id, kind)
    csrf = None if cookie is None else _csrf_for(cookie)
    headers = _headers(cookie, csrf)

    responses = [
        client.post(QUERY_URL, json={}, headers=headers),
        client.get(_detail_url(placed.id), headers=headers),
        client.post(_status_url(placed.id), json={"status": STATUS_PACKED}, headers=headers),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
        _assert_no_recipient(response.text)
    assert _status(engine, placed.id) == STATUS_PAID
    assert _audits(engine) == []


def test_routes_use_internal_id_only_and_offer_no_export(app: FastAPI) -> None:
    """「首版不提供后台导出」。SHOP-TASK-037 验收：订单号与收货资料不进任何路径或查询
    参数，路径只用订单的内部整数 ID；只有三个接口。
    """
    # 用 OpenAPI 文档列出路由：它由应用实际注册的全部路由生成，不依赖 app.routes 里
    # 被包含的路由器以何种形式出现。
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(ORDERS_URL)
        for method in operations
    }

    assert routes == {
        ("POST", QUERY_URL),
        ("GET", f"{ORDERS_URL}/{{order_id}}"),
        ("POST", f"{ORDERS_URL}/{{order_id}}/status"),
    }


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------


def test_list_newest_first_twenty_per_page(db: Session, client: TestClient, token: str) -> None:
    """Kelvin 2026-10-05：后台订单列表按下单时间从新到旧、每页 20 条。
    SHOP-TASK-037 验收：同一时间按内部 ID 从大到小；第 2 页接着给；总数为全部符合条件的
    单数。
    """
    placed = [_make_order(db, created_at=BASE + timedelta(minutes=i)) for i in range(23)]
    # 两张与第 10 张同一时间下单的订单。
    tied = [_make_order(db, created_at=BASE + timedelta(minutes=10)) for _ in range(2)]
    placed.extend(tied)
    newest_first = sorted(placed, key=lambda p: (p.created_at, p.id), reverse=True)
    expected = [p.id for p in newest_first]

    first = _ok(_query(client, token))
    second = _ok(_query(client, token, {"page": 2}))
    third = _ok(_query(client, token, {"page": 3}))

    assert (first["total"], first["page"], first["page_size"]) == (25, 1, 20)
    assert [row["id"] for row in first["orders"]] == expected[:20]
    assert (second["total"], second["page"], second["page_size"]) == (25, 2, 20)
    assert [row["id"] for row in second["orders"]] == expected[20:]
    assert third["total"] == 25
    assert third["orders"] == []
    tied_positions = [expected.index(p.id) for p in sorted(tied, key=lambda p: -p.id)]
    assert tied_positions == sorted(tied_positions)


def test_list_filters_by_status(db: Session, client: TestClient, token: str) -> None:
    """SHOP-TASK-037 验收：状态筛选只允许 docs/DESIGN.md 的六种订单状态，
    按状态精确筛选；不给状态时列出全部。
    """
    paid = _make_order(db, STATUS_PAID, created_at=BASE)
    packed_old = _make_order(db, STATUS_PACKED, created_at=BASE + timedelta(minutes=1))
    _make_order(db, STATUS_SHIPPED, created_at=BASE + timedelta(minutes=2))
    packed_new = _make_order(db, STATUS_PACKED, created_at=BASE + timedelta(minutes=3))
    _make_order(db, STATUS_CANCELLED, created_at=BASE + timedelta(minutes=4))

    packed = _ok(_query(client, token, {"status": STATUS_PACKED}))
    only_paid = _ok(_query(client, token, {"status": STATUS_PAID, "page": 1}))
    completed = _ok(_query(client, token, {"status": STATUS_COMPLETED}))
    everything = _ok(_query(client, token, {"status": None}))

    assert packed["total"] == 2
    assert [row["id"] for row in packed["orders"]] == [packed_new.id, packed_old.id]
    assert {row["status"] for row in packed["orders"]} == {STATUS_PACKED}
    assert [row["id"] for row in only_paid["orders"]] == [paid.id]
    assert completed == {"total": 0, "page": 1, "page_size": 20, "orders": []}
    assert everything["total"] == 5


@pytest.mark.parametrize(
    "raw",
    [
        "10AB CDEF GH1J K0MN",
        "10AB-CDEF-GH1J-K0MN",
        " 10ab-cdef gh1j-k0mn ",
        "LOABCDEFGHIJKOMN",
        "loabcdefghijkomn",
    ],
)
def test_list_order_number_is_normalized_then_exact(
    db: Session, client: TestClient, token: str, raw: str
) -> None:
    """Kelvin 2026-10-05：订单号搜索先按查单的规则规范化再精确匹配（去掉空格与连字符、
    转大写，I、L 换成 1，O 换成 0）。
    """
    target = _make_order(db, number=NUMBER)
    _make_order(db, number="10ABCDEFGH1JK0MP", created_at=BASE + timedelta(minutes=1))

    body = _ok(_query(client, token, {"order_number": raw}))

    assert body["total"] == 1
    assert [row["id"] for row in body["orders"]] == [target.id]
    assert body["orders"][0]["order_number"] == NUMBER


@pytest.mark.parametrize(
    "raw",
    ["", "10ABCDEFGH1JK0M", "10ABCDEFGH1JK0MNP", "10ABCDEFGH1JK0MU", "10ABCDEFGH1JK0M*"],
)
def test_list_invalid_order_number_returns_empty(
    db: Session, client: TestClient, token: str, raw: str
) -> None:
    """SHOP-TASK-037 验收：规范化后格式不合法时返回空列表而不是错误。"""
    _make_order(db, number=NUMBER)

    body = _ok(_query(client, token, {"order_number": raw}))

    assert body == {"total": 0, "page": 1, "page_size": 20, "orders": []}


def test_list_rows_counts_and_no_recipient_or_audit(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """「仅管理员可见所有订单原始资料……访问……留审计记录」：列表不返回收货资料，因此不写
    审计。SHOP-TASK-037 验收：每行只有内部 ID、订单号、下单时间、状态、应付与审核中的
    退款申请数。
    """
    with_refunds = _make_order(db, STATUS_SHIPPED, created_at=BASE + timedelta(minutes=1))
    without = _make_order(db, STATUS_PAID, created_at=BASE)
    _add_refund(db, with_refunds.id, REFUND_REQUESTED)
    _add_refund(db, with_refunds.id, REFUND_REQUESTED)
    _add_refund(db, with_refunds.id, REFUND_APPROVED)
    _add_refund(db, with_refunds.id, REFUND_REJECTED)

    response = _query(client, token)
    body = _ok(response)

    assert body["total"] == 2
    assert body["orders"] == [
        {
            "id": with_refunds.id,
            "order_number": with_refunds.number,
            "created_at": _iso(with_refunds.created_at),
            "status": STATUS_SHIPPED,
            "total_sen": TOTAL,
            "refunds_pending": 2,
        },
        {
            "id": without.id,
            "order_number": without.number,
            "created_at": _iso(without.created_at),
            "status": STATUS_PAID,
            "total_sen": TOTAL,
            "refunds_pending": 0,
        },
    ]
    _assert_no_recipient(response.text)
    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------


def test_detail_fields_recipient_and_events(db: Session, client: TestClient, token: str) -> None:
    """「仅管理员可见所有订单原始资料」：详情返回收货资料原文；
    「事件不写收货资料原文」。SHOP-TASK-037 验收：按请求语言取的订单行快照、金额、
    事件（时间、迁移后状态、操作者类别）、审核中的退款申请数、是否全部已退与由
    cookie 算出的 CSRF 令牌。
    """
    placed = _make_order(db, STATUS_PAID)
    _add_refund(db, placed.id, REFUND_REQUESTED)
    assert placed.paid_at is not None

    body = _ok(_detail(client, token, placed.id, "zh"))

    assert body == {
        "id": placed.id,
        "order_number": placed.number,
        "status": STATUS_PAID,
        "created_at": _iso(placed.created_at),
        "paid_at": _iso(placed.paid_at),
        "lines": [
            {
                "name": "T恤",
                "variant_label": "颜色：红色",
                "quantity": 3,
                "unit_price_sen": 3000,
                "line_subtotal_sen": 9000,
                "unit_cash_paid_sen": [2701, 2699, 2700],
            },
            {
                "name": "马克杯",
                "variant_label": "",
                "quantity": 1,
                "unit_price_sen": 1500,
                "line_subtotal_sen": 1500,
                "unit_cash_paid_sen": [1500],
            },
        ],
        "subtotal_sen": SUBTOTAL,
        "coupon_discount_sen": COUPON,
        "points_discount_sen": 0,
        "shipping_fee_sen": SHIPPING,
        "total_sen": TOTAL,
        "recipient": {
            "name": NAME,
            "phone": PHONE,
            "country_code": "MY",
            "region": "MY-10",
            "address": ADDRESS,
            "postal_code": POSTAL,
        },
        "events": [
            {
                "created_at": _iso(placed.created_at),
                "status": STATUS_AWAITING_PAYMENT,
                "actor_type": ACTOR_GUEST,
            },
            {
                "created_at": _iso(placed.paid_at),
                "status": STATUS_PAID,
                "actor_type": ACTOR_GUEST,
            },
        ],
        "refunds_pending": 1,
        "fully_refunded": False,
        "csrf_token": _csrf_for(token),
    }
    _assert_no_recipient(json.dumps(body["events"]))


def test_detail_amount_breakdown_includes_coupon_and_points(
    db: Session, client: TestClient, token: str
) -> None:
    """docs/UX.md A02「金额明细」[M1]（订单金额快照，DESIGN「数据模型」Order）：后台照常
    给出券折扣与积分抵扣两项（UX 0.5「后台 A02 不变」），取自订单快照，1 积分抵 1 仙。
    """
    placed = _make_order(db, STATUS_PAID)
    order = db.get(Order, placed.id)
    assert order is not None
    order.points_redeemed = 250
    order.total_sen = TOTAL - 250
    db.commit()

    body = _ok(_detail(client, token, placed.id, "en"))

    assert body["subtotal_sen"] == SUBTOTAL
    assert body["coupon_discount_sen"] == COUPON
    assert body["points_discount_sen"] == 250
    assert body["shipping_fee_sen"] == SHIPPING
    assert body["total_sen"] == TOTAL - 250
    assert (
        body["subtotal_sen"]
        - body["coupon_discount_sen"]
        - body["points_discount_sen"]
        + body["shipping_fee_sen"]
        == body["total_sen"]
    )


def test_detail_uses_requested_language(db: Session, client: TestClient, token: str) -> None:
    """SHOP-TASK-037 验收：订单行快照按请求语言取（语言参数与目录接口相同，默认 en）。"""
    placed = _make_order(db, STATUS_PAID)

    default = _ok(client.get(_detail_url(placed.id), headers=_headers(token)))
    malay = _ok(_detail(client, token, placed.id, "ms"))

    assert [line["name"] for line in default["lines"]] == ["Tee", "Mug"]
    assert [line["name"] for line in malay["lines"]] == ["Tee", "Mug Kopi"]


def test_each_view_writes_committed_audit(
    db: Session, client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """「收货资料……访问受服务端权限控制并留审计记录」。SHOP-TASK-037 验收：每次成功查看
    都在同一请求里写一条 admin_order_viewed（对象类别 order、对象 ID 为内部 ID，不写旧值
    与新值）并在返回前提交，另一个数据库会话能读到。
    """
    placed = _make_order(db, STATUS_PAID)

    _ok(_detail(client, token, placed.id))
    assert _audits(engine) == [(account_id, VIEWED, "order", placed.id, None, None)]
    _ok(_detail(client, token, placed.id))
    assert _audits(engine) == [(account_id, VIEWED, "order", placed.id, None, None)] * 2


def test_detail_not_found_writes_no_audit(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """SHOP-TASK-037 验收：订单不存在时 404 not_found，不写审计。
    最大的合法 ID 也是 404 而不是 422。
    """
    placed = _make_order(db, STATUS_PAID)

    for order_id in (placed.id + 1, 2**31 - 1):
        _assert_error(_detail(client, token, order_id), 404, NOT_FOUND)

    assert _audits(engine) == []


def test_detail_fully_refunded_flag(db: Session, client: TestClient, token: str) -> None:
    """「所有购买件数均已批准退款时，冻结后续……推进」；Kelvin 2026-10-05：
    按 SHOP-TASK-029 的全部已退判定。只批准部分件、或全部件只是审核中时不算全部已退。
    """
    full = _make_order(db, STATUS_PAID)
    partial = _make_order(db, STATUS_PAID)
    pending = _make_order(db, STATUS_PAID)
    _refund_all(db, full.id)
    _add_refund(db, partial.id, REFUND_APPROVED, line_indexes=(0,))
    _add_refund(db, pending.id, REFUND_REQUESTED, line_indexes=(0, 1))

    assert _ok(_detail(client, token, full.id))["fully_refunded"] is True
    assert _ok(_detail(client, token, partial.id))["fully_refunded"] is False
    pending_body = _ok(_detail(client, token, pending.id))
    assert pending_body["fully_refunded"] is False
    assert pending_body["refunds_pending"] == 1


def test_events_same_time_ordered_by_id(db: Session, client: TestClient, token: str) -> None:
    """SHOP-TASK-037 验收：事件按时间从早到晚、同一时间按事件 ID 从小到大。"""
    placed = _make_order(db, STATUS_SHIPPED)
    same = BASE + timedelta(hours=1)
    earlier = BASE + timedelta(minutes=30)
    # 写入顺序：同一时间的 shipped、packed，再写一条时间更早的 packed。
    _add(db, _event(placed.id, STATUS_PACKED, STATUS_SHIPPED, ACTOR_ADMIN, same))
    _add(db, _event(placed.id, STATUS_PAID, STATUS_PACKED, ACTOR_ADMIN, same))
    _add(db, _event(placed.id, STATUS_PAID, STATUS_PACKED, ACTOR_ADMIN, earlier))
    db.commit()

    events = _ok(_detail(client, token, placed.id))["events"]

    assert [(event["created_at"], event["status"]) for event in events] == [
        (_iso(BASE), STATUS_AWAITING_PAYMENT),
        (_iso(BASE + timedelta(minutes=2)), STATUS_PAID),
        (_iso(earlier), STATUS_PACKED),
        (_iso(same), STATUS_SHIPPED),
        (_iso(same), STATUS_PACKED),
    ]


# ---------------------------------------------------------------------------
# 推进
# ---------------------------------------------------------------------------


def test_advance_paid_packed_shipped(
    db: Session, client: TestClient, engine: Engine, account_id: int, token: str
) -> None:
    """「管理员依次推进 demo_paid → demo_packed → demo_shipped」
    「事务内写订单、流水和事件」。SHOP-TASK-037 验收：每步写一条操作者为 admin 的
    事件与一条带旧值、新值的 admin_order_status_changed 审计。
    """
    placed = _make_order(db, STATUS_PAID)
    before = _events(engine, placed.id)

    assert _ok(_advance(client, token, placed.id, STATUS_PACKED)) == {"status": STATUS_PACKED}
    assert _status(engine, placed.id) == STATUS_PACKED
    assert _ok(_advance(client, token, placed.id, STATUS_SHIPPED)) == {"status": STATUS_SHIPPED}
    assert _status(engine, placed.id) == STATUS_SHIPPED

    assert _events(engine, placed.id) == [
        *before,
        (STATUS_PAID, STATUS_PACKED, ACTOR_ADMIN),
        (STATUS_PACKED, STATUS_SHIPPED, ACTOR_ADMIN),
    ]
    assert _audits(engine) == [
        (account_id, CHANGED, "order", placed.id, STATUS_PAID, STATUS_PACKED),
        (account_id, CHANGED, "order", placed.id, STATUS_PACKED, STATUS_SHIPPED),
    ]


@pytest.mark.parametrize("target", [STATUS_PACKED, STATUS_SHIPPED])
def test_repeat_same_status_is_safe(
    db: Session, client: TestClient, engine: Engine, token: str, target: str
) -> None:
    """SHOP-TASK-037 验收：订单已是目标状态时返回 200 与当前状态，不写事件与审计
    （重复点击安全）。
    """
    placed = _make_order(db, target)
    events = _events(engine, placed.id)

    for _ in range(2):
        assert _ok(_advance(client, token, placed.id, target)) == {"status": target}

    assert _status(engine, placed.id) == target
    assert _events(engine, placed.id) == events
    assert _audits(engine) == []


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (STATUS_PAID, STATUS_SHIPPED),
        (STATUS_AWAITING_PAYMENT, STATUS_PACKED),
        (STATUS_AWAITING_PAYMENT, STATUS_SHIPPED),
        (STATUS_COMPLETED, STATUS_PACKED),
        (STATUS_COMPLETED, STATUS_SHIPPED),
        (STATUS_CANCELLED, STATUS_PACKED),
        (STATUS_CANCELLED, STATUS_SHIPPED),
        (STATUS_SHIPPED, STATUS_PACKED),
    ],
)
def test_not_advanceable_writes_nothing(
    db: Session, client: TestClient, engine: Engine, token: str, current: str, target: str
) -> None:
    """「管理员依次推进」（不允许跳过打包）、「已完成的履约状态不可倒退」、「已模拟支付
    订单不走取消」。SHOP-TASK-037 验收：用 SHOP-TASK-010 的迁移判定以 admin 检查，跳过
    打包、待支付、已完成、已取消与倒退都 409 order_not_advanceable 与当前状态，不写入。
    """
    placed = _make_order(db, current)
    events = _events(engine, placed.id)

    _assert_conflict(_advance(client, token, placed.id, target), "order_not_advanceable", current)

    assert _status(engine, placed.id) == current
    assert _events(engine, placed.id) == events
    assert _audits(engine) == []


@pytest.mark.parametrize(
    ("current", "target"),
    [(STATUS_PAID, STATUS_PACKED), (STATUS_PACKED, STATUS_SHIPPED)],
)
def test_fully_refunded_order_is_frozen(
    db: Session, client: TestClient, engine: Engine, token: str, current: str, target: str
) -> None:
    """「所有购买件数均已批准退款时，冻结后续打包/发货……的推进，保留退款前履约状态」；
    Kelvin 2026-10-05：按全部已退判定。即使迁移本身允许也 409 fulfilment_frozen。
    """
    placed = _make_order(db, current)
    _refund_all(db, placed.id)
    events = _events(engine, placed.id)

    _assert_conflict(_advance(client, token, placed.id, target), "fulfilment_frozen", current)

    assert _status(engine, placed.id) == current
    assert _events(engine, placed.id) == events
    assert _audits(engine) == []


def test_partially_refunded_order_still_advances(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """「所有购买件数均已批准退款时」才冻结：只批准部分件、其余审核中时照常推进。"""
    placed = _make_order(db, STATUS_PAID)
    _add_refund(db, placed.id, REFUND_APPROVED, line_indexes=(0,))
    _add_refund(db, placed.id, REFUND_REQUESTED, line_indexes=(1,))

    assert _ok(_advance(client, token, placed.id, STATUS_PACKED)) == {"status": STATUS_PACKED}
    assert _status(engine, placed.id) == STATUS_PACKED


def test_advance_unknown_order_is_404(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """SHOP-TASK-037 验收：推进时订单不存在 404 not_found，不写审计。"""
    placed = _make_order(db, STATUS_PAID)

    _assert_error(_advance(client, token, placed.id + 1, STATUS_PACKED), 404, NOT_FOUND)

    assert _audits(engine) == []


def test_lock_statement_compiles_to_for_update_on_mysql() -> None:
    """SHOP-TASK-037 验收：推进先以 SELECT … FOR UPDATE 锁定该订单行（用 MySQL 方言编译，
    SQLite 不支持 FOR UPDATE）。
    """
    stmt = admin_orders_service.lock_order_statement(7)
    sql = str(stmt.compile(dialect=mysql.dialect()))

    assert "FROM orders" in sql
    assert "WHERE orders.id = " in sql
    assert sql.rstrip().endswith("FOR UPDATE")


@pytest.mark.parametrize("fully_refunded", [False, True])
def test_refund_read_happens_after_lock(
    db: Session,
    client: TestClient,
    engine: Engine,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    fully_refunded: bool,
) -> None:
    """「所有购买件数均已批准退款时，冻结后续……推进」与退款审核批准的并发须串行化。
    SHOP-TASK-037 验收：推进实际执行的锁定查询含 FOR UPDATE（以 MySQL 方言编译），
    读取退款占用与判断全部已退都发生在锁定之后。
    """
    placed = _make_order(db, STATUS_PAID)
    if fully_refunded:
        _refund_all(db, placed.id)
    calls: list[str] = []

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if isinstance(clauseelement, Select):
            sql = str(clauseelement.compile(dialect=mysql.dialect()))
            if "FOR UPDATE" in sql:
                assert "FROM orders" in sql
                calls.append("lock")

    real = admin_orders_service.refund_state

    def recording(session: Session, order_id: int) -> Any:
        calls.append("refund_state")
        return real(session, order_id)

    monkeypatch.setattr(admin_orders_service, "refund_state", recording)
    event.listen(engine, "before_execute", on_execute)
    try:
        response = _advance(client, token, placed.id, STATUS_PACKED)
    finally:
        event.remove(engine, "before_execute", on_execute)

    if fully_refunded:
        _assert_conflict(response, "fulfilment_frozen", STATUS_PAID)
    else:
        assert _ok(response) == {"status": STATUS_PACKED}
    assert calls == ["lock", "refund_state"]


@pytest.mark.parametrize("csrf", ["missing", "wrong", "other_session", "duplicate"])
def test_csrf_failure_leaves_order_unchanged(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    token: str,
    csrf: str,
) -> None:
    """「支付、取消……等写操作另须 CSRF 令牌」，后台写操作同样。
    SHOP-TASK-037 验收：缺失或不符 403 csrf_failed，先于读取订单；订单不变、不写事件与
    审计。
    """
    placed = _make_order(db, STATUS_PAID)
    events = _events(engine, placed.id)
    other = issue_admin_session(db, account_id, _now() - timedelta(minutes=1))
    db.commit()
    headers: list[tuple[str, str]] = [("Cookie", f"{COOKIE_NAME}={token}")]
    if csrf == "wrong":
        headers.append(("X-CSRF-Token", "0" * 64))
    elif csrf == "other_session":
        headers.append(("X-CSRF-Token", _csrf_for(other.token)))
    elif csrf == "duplicate":
        headers.extend([("X-CSRF-Token", _csrf_for(token))] * 2)

    with _sql(engine) as statements:
        response = client.post(
            _status_url(placed.id), json={"status": STATUS_PACKED}, headers=headers
        )

    _assert_error(response, 403, CSRF_FAILED)
    assert not any(re.search(r"\borders\b", statement) for statement in statements)
    assert _status(engine, placed.id) == STATUS_PAID
    assert _events(engine, placed.id) == events
    assert _audits(engine) == []


@pytest.mark.parametrize(
    ("rival_steps", "expected_status_code", "final_status"),
    [
        ((STATUS_PACKED,), 200, STATUS_PACKED),
        ((STATUS_PACKED, STATUS_SHIPPED), 409, STATUS_SHIPPED),
    ],
)
def test_concurrent_advance_applies_once(
    db: Session,
    client: TestClient,
    engine: Engine,
    account_id: int,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    rival_steps: tuple[str, ...],
    expected_status_code: int,
    final_status: str,
) -> None:
    """「所有状态迁移校验当前状态、操作者与数量」。SHOP-TASK-037 验收：
    条件 UPDATE 未命中时回滚、重新读取订单，已是目标状态返回 200，否则 409
    order_not_advanceable 与当前状态；不出现一单两次推进。交错点：推进请求读完退款占用
    之后、条件 UPDATE 之前，另一个数据库会话推进同一订单并提交。
    """
    placed = _make_order(db, STATUS_PAID)
    before = _events(engine, placed.id)
    real = admin_orders_service.refund_state
    done: list[bool] = []

    def rival() -> None:
        with Session(engine) as other:
            for step in rival_steps:
                _, advanced = admin_orders_service.advance_order(
                    other, account_id, placed.id, step, _now()
                )
                assert advanced

    def interleaved(session: Session, order_id: int) -> Any:
        state = real(session, order_id)
        if not done:
            done.append(True)
            rival()
        return state

    monkeypatch.setattr(admin_orders_service, "refund_state", interleaved)

    response = _advance(client, token, placed.id, STATUS_PACKED)

    if expected_status_code == 200:
        assert _ok(response) == {"status": STATUS_PACKED}
    else:
        _assert_conflict(response, "order_not_advanceable", STATUS_SHIPPED)
    assert done == [True]
    assert _status(engine, placed.id) == final_status
    transitions = list(zip((STATUS_PAID, *rival_steps), rival_steps, strict=False))
    assert _events(engine, placed.id) == [
        *before,
        *[(old, new, ACTOR_ADMIN) for old, new in transitions],
    ]
    assert _audits(engine) == [
        (account_id, CHANGED, "order", placed.id, old, new) for old, new in transitions
    ]


# ---------------------------------------------------------------------------
# 请求格式与 422
# ---------------------------------------------------------------------------


def _post_raw(
    client: TestClient, url: str, content: bytes, content_type: str | None, token: str | None
) -> Any:
    headers = _headers(token, None if token is None else _csrf_for(token))
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.post(url, content=content, headers=headers)


@pytest.mark.parametrize("which", ["query", "status"])
@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_non_json_is_415(
    db: Session,
    client: TestClient,
    engine: Engine,
    token: str,
    which: str,
    content_type: str | None,
) -> None:
    """SHOP-TASK-037 验收：两个 POST 接口只接受 JSON，否则 415；订单不变。"""
    placed = _make_order(db, STATUS_PAID)
    url = QUERY_URL if which == "query" else _status_url(placed.id)
    content = json.dumps({"status": STATUS_PACKED}).encode()

    response = _post_raw(client, url, content, content_type, token)

    _assert_error(response, 415, {"detail": "request body must be JSON"})
    assert _status(engine, placed.id) == STATUS_PAID


@pytest.mark.parametrize("which", ["query", "status"])
@pytest.mark.parametrize("logged_in", [True, False])
@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
def test_oversized_body_is_413_first(
    db: Session,
    client: TestClient,
    engine: Engine,
    token: str,
    which: str,
    logged_in: bool,
    content_type: str,
) -> None:
    """SHOP-TASK-037 验收：请求体上限 8 KB 且先于其他校验（多余字段、不是 JSON、没有后台
    会话时都是 413）；不回显请求内容。
    """
    placed = _make_order(db, STATUS_PAID)
    url = QUERY_URL if which == "query" else _status_url(placed.id)
    content = json.dumps({"extra": "SECRETVALUE" * 1000}).encode()
    assert len(content) > MAX_BODY_BYTES

    response = _post_raw(client, url, content, content_type, token if logged_in else None)

    _assert_error(response, 413, {"detail": "request body too large"})
    assert "SECRETVALUE" not in response.text
    assert _status(engine, placed.id) == STATUS_PAID


@pytest.mark.parametrize(
    "body",
    [
        {"order_number": "SECRETVALUE", "extra": "SECRETVALUE"},
        {"status": "SECRETVALUE"},
        {"status": "shipped"},
        {"order_number": 12345},
        {"page": 0},
        {"page": 10001},
        {"page": "2"},
        {"page": True},
        {"page": 1.5},
        {"page": None},
    ],
)
def test_query_body_errors_are_plain(
    db: Session, client: TestClient, token: str, body: dict[str, Any]
) -> None:
    """SHOP-TASK-037 验收：列表的字段只有可选的订单号、可选的状态（六种订单状态之一）与
    页码（1 到 10000 的整数），多出字段或不合法 422；每条错误只给位置、类型与固定消息。
    """
    _make_order(db, STATUS_PAID)

    _assert_plain_errors(_query(client, token, body), "SECRETVALUE")


def test_query_page_bounds_are_accepted(db: Session, client: TestClient, token: str) -> None:
    """SHOP-TASK-037 验收：页码 1 到 10000 都合法，默认 1。"""
    _make_order(db, STATUS_PAID)

    assert _ok(_query(client, token, {"page": 1}))["total"] == 1
    assert _ok(_query(client, token, {"page": 10000}))["orders"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"status": STATUS_PACKED, "extra": "SECRETVALUE"},
        {"status": "SECRETVALUE"},
        {"status": STATUS_PAID},
        {"status": STATUS_COMPLETED},
        {"status": None},
        {},
    ],
)
def test_status_body_errors_are_plain(
    db: Session, client: TestClient, engine: Engine, token: str, body: dict[str, Any]
) -> None:
    """SHOP-TASK-037 验收：推进的请求体只有目标状态（只允许 demo_packed、demo_shipped），
    多出字段 422；不回显请求内容，订单不变。
    """
    placed = _make_order(db, STATUS_PAID)
    headers = _headers(token, _csrf_for(token))

    response = client.post(_status_url(placed.id), json=body, headers=headers)

    _assert_plain_errors(response, "SECRETVALUE")
    assert _status(engine, placed.id) == STATUS_PAID
    assert _audits(engine) == []


def test_invalid_language_is_plain_422(
    db: Session, client: TestClient, engine: Engine, token: str
) -> None:
    """SHOP-TASK-037 验收：语言参数与目录接口相同，不合法时 422 只含位置、类型与固定消息，
    不含所给的值，不写审计。
    """
    placed = _make_order(db, STATUS_PAID)

    response = _detail(client, token, placed.id, "SECRETVALUE")

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
    assert _audits(engine) == []


@pytest.mark.parametrize(
    "raw",
    ["abc", "0", "-1", "01", "1.5", "+1", "1e3", "2147483648", "99999999999999999999", "query"],
)
def test_invalid_order_id_in_detail_path_is_plain_422(
    db: Session, client: TestClient, engine: Engine, token: str, raw: str
) -> None:
    """SHOP-TASK-037 验收：路径只用订单的内部整数 ID；不合法时 422，只含位置、类型与固定
    消息，不含所给的值，不写审计。
    """
    _make_order(db, STATUS_PAID)

    response = client.get(_detail_url(raw), headers=_headers(token))

    _assert_error(response, 422, ORDER_ID_ERROR)
    assert _audits(engine) == []


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "01", "2147483648"])
def test_invalid_order_id_in_status_path_is_plain_422(
    db: Session, client: TestClient, engine: Engine, token: str, raw: str
) -> None:
    """SHOP-TASK-037 验收：推进路径里的订单 ID 不合法时同样 422，固定消息，不写入。"""
    placed = _make_order(db, STATUS_PAID)
    headers = _headers(token, _csrf_for(token))

    response = client.post(_status_url(raw), json={"status": STATUS_PACKED}, headers=headers)

    _assert_error(response, 422, ORDER_ID_ERROR)
    assert _status(engine, placed.id) == STATUS_PAID
    assert _audits(engine) == []


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(
    db: Session, client: TestClient, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」。SHOP-TASK-037 验收：
    三个接口（成功与失败）都不写日志。
    """
    placed = _make_order(db, STATUS_PAID, number=NUMBER)
    caplog.set_level(logging.DEBUG)

    _query(client, token, {"order_number": NUMBER})
    _query(client, token, {"extra": 1})
    _detail(client, token, placed.id)
    _detail(client, token, placed.id + 1)
    _advance(client, token, placed.id, STATUS_PACKED)
    _advance(client, token, placed.id, STATUS_PAID)
    client.post(_status_url(placed.id), json={"status": STATUS_SHIPPED}, headers=_headers(token))

    assert [record for record in caplog.records if record.name.startswith("app")] == []

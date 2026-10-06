"""退款申请：POST /api/orders/refunds、GET /api/orders/lookup 的退款部分、
app/services/refunds.py 与 RefundRequest、RefundLine、RefundLineUnit 三个模型。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「订单与退款状态」第 4 条（退款申请与金额）、「计价、
优惠、积分与库存」第 4 条（每件实际支付商品额只指现金实付）、「失败、并发与重试」第 1 条（幂等键
与数据库唯一约束）、「权限与资料保护」第 6、7 条（查单授权、CSRF）与「数据模型」的
RefundRequest / RefundLine 一行；以及 docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 退款决定
（下文简称「HANDOFF 0.27」：按件序从前往后取未被审核中或已批准的申请占用的件，金额为这几件的逐件
现金实付之和，被拒绝的申请释放所占的件；截止时间为支付时刻加 30×24 小时）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的设计原句或 HANDOFF 0.27 的决定；
没有直接原句的，写明是 SHOP-TASK-029 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。订单在测试里直接写库建立
（含逐件分摊快照），其中 T 恤一行三件的现金实付各不相同（2701、2699、2700 仙），用来验证取件
顺序与金额。接口的会话依赖换成每个请求一个绑定同一内存库的会话；SHOP-TASK-026 的取客户端依赖
换成本文件的内存替身 FakeRedis（只实现查单用到的 GET 与事务管道的 INCR、EXPIRE）。授权与
CSRF 令牌经 SHOP-TASK-027 的查单接口取得。

并发：内存库经 StaticPool 只有一个连接，pysqlite 在第一条写语句前才开始事务，所以在一个会话还只
做过查询时，用第二个会话执行并提交另一笔申请，就能模拟并发请求先提交；交错点是 refunds 模块读完
可退件（refund_state）之后、写申请之前。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, event, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import order_lookup as order_lookup_api
from app.api import refunds as refunds_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    Order,
    OrderAccessGrant,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_MEMBER,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.models.refund import REFUND_APPROVED, REFUND_REJECTED, REFUND_REQUESTED
from app.services import refunds
from app.services.order_access import COOKIE_NAME, csrf_token_for_cookie, issue_order_access
from app.services.order_rules import generate_order_number
from app.services.rate_limit import get_redis_client
from app.services.refunds import RefundLineRequest, RefundUnit

LOOKUP_URL = "/api/orders/lookup"
REFUND_URL = "/api/orders/refunds"
MAX_BODY_BYTES = 8 * 1024

PHONE_E164 = "+60123456789"
PHONE_LOCAL = "012-345 6789"
SHIPPING = 800
WINDOW = timedelta(hours=30 * 24)

# 订单行：三语名称、三语规格说明、单价、逐件券额。逐件现金实付 = 单价 − 券额，
# T 恤三件各不相同且不按大小排列，取件顺序只能按件序。
LINES = [
    (
        ("Tee", "T恤", "Tee"),
        ("Colour: Red", "颜色：红色", "Colour: Red"),
        3000,
        [299, 301, 300],
    ),
    (("Mug", "马克杯", "Mug Kopi"), ("", "", ""), 1500, [0]),
]
TEE_CASH = [2701, 2699, 2700]
MUG_CASH = [1500]
SUBTOTAL = 3 * 3000 + 1500
COUPON = 900
# 商品现金实付合计（不含运费）。
CASH_TOTAL = sum(TEE_CASH) + sum(MUG_CASH)

ACCESS_EXPIRED = {"detail": "access_expired"}
REVIEWED_AT = datetime(2026, 10, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Redis 替身
# ---------------------------------------------------------------------------


class FakeRedis:
    """内存替身：查单的来源计数与失败计数。不模拟过期。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        value = self.values.get(key)
        return None if value is None else str(value).encode()

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple[str, str]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for command, key in self.queued:
            if command == "incr":
                self.store.values[key] = self.store.values.get(key, 0) + 1
                results.append(self.store.values[key])
            else:
                results.append(True)
        return results


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
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url=""))
    fake = FakeRedis()

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis_client] = lambda: fake
    return app


def _browser(app: FastAPI) -> TestClient:
    # 每个客户端是一个浏览器。https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return _browser(app)


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str
    paid_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _make_order(
    db: Session,
    status: str = STATUS_SHIPPED,
    *,
    paid_at: datetime | None = None,
) -> _Placed:
    """直接写一张订单（订单行、逐件分摊快照、收货资料与下单事件）并提交。

    已支付及之后的状态默认 1 小时前支付；未支付的状态没有支付时间。
    创建时间为支付（或现在）前 5 分钟。
    """
    now = _now()
    if status in PAID_STATUSES:
        paid_at = paid_at or now - timedelta(hours=1)
    else:
        paid_at = None
    created = (paid_at or now) - timedelta(minutes=5)
    order = Order(
        order_number=generate_order_number(),
        status=status,
        subtotal_sen=SUBTOTAL,
        coupon_discount_sen=COUPON,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=SUBTOTAL - COUPON + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=_key(),
        request_fingerprint="0" * 64,
        created_at=created,
        payment_expires_at=created + timedelta(minutes=15),
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
        name="Zubaidah Secretname",
        phone=PHONE_E164,
        country_code="MY",
        region="MY-10",
        address="77 Hiddenlane Road",
        postal_code="50000",
    )
    _add(db, recipient)
    placement = OrderEvent(
        order_id=order.id,
        from_status=None,
        to_status=STATUS_AWAITING_PAYMENT,
        actor_type=ACTOR_GUEST,
        created_at=created,
    )
    _add(db, placement)
    placed = _Placed(id=order.id, number=order.order_number, paid_at=paid_at)
    db.commit()
    return placed


def _looked_up(client: TestClient, placed: _Placed) -> str:
    """经查单接口取得该单的 lookup 授权，返回本浏览器的 CSRF 令牌。"""
    body = {"order_number": placed.number, "phone": PHONE_LOCAL}
    response = client.post(LOOKUP_URL, json=body)
    assert response.status_code == 204, response.text
    csrf = csrf_token_for_cookie(client.cookies.get(COOKIE_NAME))
    assert csrf is not None
    return csrf


def _refund(
    client: TestClient,
    number: str,
    lines: list[tuple[int, int]],
    csrf: str | None,
    *,
    key: str | None = None,
) -> Any:
    headers = {"Idempotency-Key": key or _key()}
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    body = {
        "order_number": number,
        "lines": [{"line_index": index, "quantity": quantity} for index, quantity in lines],
    }
    return client.post(REFUND_URL, json=body, headers=headers)


def _view(client: TestClient, lang: str = "en") -> dict[str, Any]:
    response = client.get(LOOKUP_URL, params={"lang": lang})
    assert response.status_code == 200, response.text
    return response.json()["orders"][0]


def _counts(db: Session) -> tuple[int, ...]:
    """申请、申请行、逐件记录各自的条数。"""
    models = (RefundRequest, RefundLine, RefundLineUnit)
    return tuple(db.scalar(select(func.count()).select_from(model)) or 0 for model in models)


def _unit_rows(db: Session) -> list[tuple[int, int, int, int | None]]:
    """逐件记录：所属订单行序、件序、记录的现金实付、占用标记，按写入顺序。"""
    stmt = (
        select(
            OrderItem.line_index,
            OrderItemUnit.unit_index,
            RefundLineUnit.cash_paid_sen,
            RefundLineUnit.occupied,
        )
        .join(OrderItemUnit, OrderItemUnit.id == RefundLineUnit.order_item_unit_id)
        .join(OrderItem, OrderItem.id == OrderItemUnit.order_item_id)
        .order_by(RefundLineUnit.id)
    )
    return list(db.execute(stmt).tuples())


def _occupied_once(db: Session) -> bool:
    """没有任何一件同时被两条占用标记为 1 的逐件记录占用。"""
    stmt = (
        select(RefundLineUnit.order_item_unit_id, func.count())
        .where(RefundLineUnit.occupied == 1)
        .group_by(RefundLineUnit.order_item_unit_id)
    )
    return all(count == 1 for _, count in db.execute(stmt).tuples())


def _request_by_key(db: Session, key: str) -> RefundRequest:
    db.expire_all()
    return db.scalars(select(RefundRequest).where(RefundRequest.idempotency_key == key)).one()


def _review(db: Session, key: str, status: str) -> None:
    """模拟之后的审核任务：改状态并写审核时间、审核人、审核幂等键与审核请求指纹（SHOP-TASK-039
    的检查约束要求），拒绝时另写理由；拒绝时把该申请的逐件记录占用标记置空。

    库里至多一个管理员账号，已有就沿用。
    """
    request = _request_by_key(db, key)
    admin_id = db.scalar(select(AdminAccount.id))
    if admin_id is None:
        admin = AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            password_updated_at=_now(),
        )
        admin_id = _add(db, admin).id
    request.status = status
    request.reviewed_at = _now()
    request.reviewer_admin_id = admin_id
    request.review_idempotency_key = _key()
    request.review_fingerprint = "b" * 64
    if status == REFUND_REJECTED:
        request.review_reason = "Demo rejection"
        line_ids = select(RefundLine.id).where(RefundLine.refund_request_id == request.id)
        stmt = update(RefundLineUnit).where(RefundLineUnit.refund_line_id.in_(line_ids))
        db.execute(stmt.values(occupied=None))
    db.commit()


def _expire_grants(db: Session) -> None:
    """把所有授权改为 10 分钟前到期并提交（创建时间同时提前，满足到期晚于创建）。"""
    now = _now()
    created = now - timedelta(minutes=40)
    expired = now - timedelta(minutes=10)
    db.execute(update(OrderAccessGrant).values(created_at=created, expires_at=expired))
    db.commit()


def _issue(db: Session, placed: _Placed, scope: str) -> tuple[str, str]:
    """给一个新浏览器签发该单的授权并提交；返回 cookie 值与 CSRF 令牌。"""
    issued = issue_order_access(db, None, placed.id, scope, _now())
    db.commit()
    assert issued.new_token is not None
    return issued.new_token, issued.csrf_token


def _assert_access_expired(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == ACCESS_EXPIRED
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get_list("set-cookie") == []


def _assert_conflict(response: Any, code: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": code}
    assert response.headers["cache-control"] == "no-store"


def _fix_now(monkeypatch: pytest.MonkeyPatch, module: Any, now: datetime) -> None:
    monkeypatch.setattr(module, "_utcnow", lambda: now)


def _after_first_state_read(monkeypatch: pytest.MonkeyPatch, action: Callable[[], Any]) -> None:
    """refunds 模块第一次读完可退件之后（随后就是写申请）插入一段操作。"""
    real = refunds.refund_state
    done: list[bool] = []

    def wrapper(session: Session, order_id: int) -> refunds.RefundState:
        state = real(session, order_id)
        if not done:
            done.append(True)
            action()
        return state

    monkeypatch.setattr(refunds, "refund_state", wrapper)


def _in_other_session(engine: Engine, action: Callable[[Session], Any]) -> None:
    """用第二个数据库会话执行一个操作（操作自己提交）。"""
    with Session(engine) as other:
        action(other)


# ---------------------------------------------------------------------------
# 纯计算
# ---------------------------------------------------------------------------


def test_deadline_is_paid_at_plus_30x24_hours() -> None:
    """HANDOFF 0.27：「支付成功后 30 天内」的截止时间为支付时刻加 30×24 小时。SHOP-TASK-029
    验收：当前时间不晚于它时退款期内；未支付没有截止时间、不在退款期内。
    """
    paid = datetime(2026, 10, 1, 8, 30, 15)
    deadline = datetime(2026, 10, 31, 8, 30, 15)

    assert refunds.refund_deadline(paid) == deadline
    assert refunds.refund_deadline(None) is None
    assert refunds.in_refund_window(paid, deadline)
    assert refunds.in_refund_window(paid, deadline - timedelta(seconds=1))
    assert not refunds.in_refund_window(paid, deadline + timedelta(microseconds=1))
    assert not refunds.in_refund_window(None, paid)


def test_pick_units_and_estimates_follow_unit_order() -> None:
    """HANDOFF 0.27：按件序从前往后取，金额为这几件的逐件现金实付之和。SHOP-TASK-029 验收：
    申请 k 件即取可退件中件序最小的 k 件；预计金额列表第 i 项为申请 i 件时的金额。
    """
    units = [RefundUnit(11, 0, 2701), RefundUnit(12, 1, 2699), RefundUnit(13, 2, 2700)]

    assert refunds.pick_units(units, 2) == (units[0], units[1])
    assert refunds.refund_estimates(units) == [2701, 5400, 8100]
    assert refunds.refund_estimates([]) == []
    with pytest.raises(ValueError):
        refunds.pick_units(units, 4)


def test_fingerprint_sorts_lines() -> None:
    """「同键不同内容报冲突」。SHOP-TASK-029 验收：指纹是订单 ID 与按行序排序的申请行按固定规则
    序列化后的 SHA-256，请求里行的先后不改变指纹，订单或件数不同则指纹不同。
    """
    forward = [RefundLineRequest(0, 2), RefundLineRequest(1, 1)]
    backward = [RefundLineRequest(1, 1), RefundLineRequest(0, 2)]
    expected = hashlib.sha256(
        b'{"lines":[{"line_index":0,"quantity":2},{"line_index":1,"quantity":1}],"order_id":7}'
    ).hexdigest()

    assert refunds.refund_fingerprint(7, forward) == expected
    assert refunds.refund_fingerprint(7, backward) == expected
    assert refunds.refund_fingerprint(8, forward) != expected
    assert refunds.refund_fingerprint(7, [RefundLineRequest(0, 1)]) != expected


# ---------------------------------------------------------------------------
# 提交申请
# ---------------------------------------------------------------------------


def test_refund_takes_lowest_units(db: Session, client: TestClient) -> None:
    """「只选商品与数量，金额按原单逐件现金实付快照计算」；HANDOFF 0.27「按件序从前往后取」。
    SHOP-TASK-029 验收：201 与状态、金额、各行件数；申请（requested、guest、审核时间为空、指纹为
    订单 ID 与申请行的 SHA-256）、申请行、逐件记录（占用标记 1、现金实付取自快照）各自的条数与
    金额，取的是件序最小的件；no-store。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()

    response = _refund(client, placed.number, [(0, 2)], csrf, key=key)

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    amount = TEE_CASH[0] + TEE_CASH[1]
    assert response.json() == {
        "status": REFUND_REQUESTED,
        "amount_sen": amount,
        "lines": [{"line_index": 0, "quantity": 2}],
    }
    assert _counts(db) == (1, 1, 2)
    request = _request_by_key(db, key)
    assert (request.order_id, request.status, request.actor_type) == (
        placed.id,
        REFUND_REQUESTED,
        ACTOR_GUEST,
    )
    assert (request.amount_sen, request.reviewed_at) == (amount, None)
    content = f'{{"lines":[{{"line_index":0,"quantity":2}}],"order_id":{placed.id}}}'
    assert request.request_fingerprint == hashlib.sha256(content.encode()).hexdigest()
    line = db.scalars(select(RefundLine)).one()
    assert (line.refund_request_id, line.quantity, line.amount_sen) == (request.id, 2, amount)
    assert _unit_rows(db) == [(0, 0, TEE_CASH[0], 1), (0, 1, TEE_CASH[1], 1)]


def test_second_request_takes_next_units(db: Session, client: TestClient) -> None:
    """「部分退款后余量仍可再次申请」；HANDOFF 0.27「按件序从前往后取尚未被审核中…的申请占用的
    件」。SHOP-TASK-029 验收：同一行第二次申请取接下来的件。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    assert _refund(client, placed.number, [(0, 1)], csrf).status_code == 201

    response = _refund(client, placed.number, [(0, 2)], csrf)

    assert response.status_code == 201, response.text
    assert response.json()["amount_sen"] == TEE_CASH[1] + TEE_CASH[2]
    assert _counts(db) == (2, 2, 3)
    assert _unit_rows(db) == [
        (0, 0, TEE_CASH[0], 1),
        (0, 1, TEE_CASH[1], 1),
        (0, 2, TEE_CASH[2], 1),
    ]


def test_rejected_units_can_be_requested_again(db: Session, client: TestClient) -> None:
    """HANDOFF 0.27「被拒绝的申请释放所占的件」。SHOP-TASK-029 验收：把一笔申请改为 rejected 并
    把其逐件记录的占用标记置空后，这些件可再次申请，仍按件序从小取。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()
    assert _refund(client, placed.number, [(0, 3)], csrf, key=key).status_code == 201
    _review(db, key, REFUND_REJECTED)

    response = _refund(client, placed.number, [(0, 2)], csrf)

    assert response.status_code == 201, response.text
    assert response.json()["amount_sen"] == TEE_CASH[0] + TEE_CASH[1]
    assert _counts(db) == (2, 2, 5)
    assert _unit_rows(db)[3:] == [(0, 0, TEE_CASH[0], 1), (0, 1, TEE_CASH[1], 1)]
    assert [row[3] for row in _unit_rows(db)[:3]] == [None, None, None]


def test_shipping_is_never_refunded(db: Session, client: TestClient) -> None:
    """「示例运费始终不退」；「每件实际支付商品额只指现金实付」。SHOP-TASK-029 验收：申请全部
    件数时金额是逐件现金实付合计，不含运费，也不是原价合计。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)

    response = _refund(client, placed.number, [(1, 1), (0, 3)], csrf)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount_sen"] == CASH_TOTAL == SUBTOTAL - COUPON
    assert body["lines"] == [{"line_index": 0, "quantity": 3}, {"line_index": 1, "quantity": 1}]
    view = _view(client)
    assert view["total_sen"] == CASH_TOTAL + SHIPPING
    assert view["refundable_left_sen"] == 0


@pytest.mark.parametrize("status", PAID_STATUSES)
def test_paid_shipped_and_completed_orders_accept(
    db: Session, client: TestClient, status: str
) -> None:
    """「支付成功后 30 天内可申请退款，包含已发货和已完成订单」。SHOP-TASK-029 验收：已支付、
    已打包、已发货、已完成的订单照常可申请。
    """
    placed = _make_order(db, status)
    csrf = _looked_up(client, placed)

    response = _refund(client, placed.number, [(1, 1)], csrf)

    assert response.status_code == 201, response.text
    assert _counts(db) == (1, 1, 1)


@pytest.mark.parametrize("status", [STATUS_AWAITING_PAYMENT, STATUS_CANCELLED])
def test_unpaid_order_is_409(db: Session, client: TestClient, status: str) -> None:
    """「支付成功后 30 天内可申请退款」；「已模拟支付订单不走取消，走退款流程」。SHOP-TASK-029
    验收：待支付与已取消订单 409 order_not_refundable，不写任何行。
    """
    placed = _make_order(db, status)
    csrf = _looked_up(client, placed)

    response = _refund(client, placed.number, [(0, 1)], csrf)

    _assert_conflict(response, "order_not_refundable")
    assert _counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    ("offset", "accepted"),
    [
        pytest.param(timedelta(seconds=-1), True, id="one-second-before-deadline"),
        pytest.param(timedelta(0), True, id="at-deadline"),
        pytest.param(timedelta(seconds=1), False, id="one-second-after-deadline"),
    ],
)
def test_refund_window_boundary(
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    accepted: bool,
) -> None:
    """「支付成功后 30 天内可申请退款」；HANDOFF 0.27 截止时间为支付时刻加 30×24 小时。
    SHOP-TASK-029 验收：当前时间不晚于截止时间时受理；截止后 409 refund_window_closed
    （refund.window_closed），不写任何行。
    """
    now = _now()
    placed = _make_order(db, paid_at=now - WINDOW - offset)
    csrf = _looked_up(client, placed)
    _fix_now(monkeypatch, refunds_api, now)

    response = _refund(client, placed.number, [(0, 1)], csrf)

    if accepted:
        assert response.status_code == 201, response.text
        assert _counts(db) == (1, 1, 1)
    else:
        _assert_conflict(response, "refund_window_closed")
        assert _counts(db) == (0, 0, 0)


def test_unknown_line_index_is_422(db: Session, client: TestClient) -> None:
    """「只选商品与数量」。SHOP-TASK-029 验收：请求里的行序不属于该订单时 422，错误只含位置、
    类型与固定消息，不写任何行。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)

    response = _refund(client, placed.number, [(0, 1), (7, 1)], csrf)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", "lines"]]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert placed.number not in response.text
    assert _counts(db) == (0, 0, 0)


def test_nothing_left_is_409(db: Session, client: TestClient) -> None:
    """「总批准数量不超过购买数量」；P10 的 refund.nothing_left。SHOP-TASK-029 验收：该单已没有
    任何可退件时 409 refund_nothing_left，不写任何行。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    assert _refund(client, placed.number, [(0, 3), (1, 1)], csrf).status_code == 201

    response = _refund(client, placed.number, [(0, 1)], csrf)

    _assert_conflict(response, "refund_nothing_left")
    assert _counts(db) == (1, 2, 4)


@pytest.mark.parametrize("taken", [0, 2], ids=["more-than-bought", "more-than-left"])
def test_over_refundable_is_409_without_partial(
    db: Session, client: TestClient, taken: int
) -> None:
    """「重复申请同一可退数量被拒绝」；P10 的 refund.duplicate。SHOP-TASK-029 验收：任一行申请
    件数超过该行可退件数时 409 refund_duplicate，整笔不受理、不部分受理（另一行也不写）。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    if taken:
        assert _refund(client, placed.number, [(0, taken)], csrf).status_code == 201
    before = _counts(db)

    response = _refund(client, placed.number, [(1, 1), (0, 4 - taken)], csrf)

    _assert_conflict(response, "refund_duplicate")
    assert _counts(db) == before
    assert all(row[0] == 0 for row in _unit_rows(db))


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


def test_same_key_same_request_replays(db: Session, client: TestClient) -> None:
    """「相同键相同请求返回原结果」。SHOP-TASK-029 验收：同键同请求重放 200 与原申请的状态、
    金额与各行件数（行的先后不同也是同一请求），不重复写入。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()
    first = _refund(client, placed.number, [(0, 2), (1, 1)], csrf, key=key)
    assert first.status_code == 201, first.text

    again = _refund(client, placed.number, [(1, 1), (0, 2)], csrf, key=key)

    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert again.headers["cache-control"] == "no-store"
    assert _counts(db) == (1, 2, 3)


def test_same_key_other_request_conflicts(db: Session, client: TestClient) -> None:
    """「同键不同内容报冲突」。SHOP-TASK-029 验收：同键不同请求（件数不同，或另一张订单）409
    idempotency_conflict，不再写入。
    """
    placed = _make_order(db)
    other = _make_order(db)
    _looked_up(client, other)
    csrf = _looked_up(client, placed)
    key = _key()
    assert _refund(client, placed.number, [(0, 2)], csrf, key=key).status_code == 201

    changed = _refund(client, placed.number, [(0, 1)], csrf, key=key)
    other_order = _refund(client, other.number, [(0, 2)], csrf, key=key)

    _assert_conflict(changed, "idempotency_conflict")
    _assert_conflict(other_order, "idempotency_conflict")
    assert _counts(db) == (1, 1, 2)


# ---------------------------------------------------------------------------
# 授权与 CSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["missing", "wrong", "other-browser"])
def test_without_valid_csrf_is_403(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」。SHOP-TASK-029 验收：缺或错
    CSRF 403 csrf_failed，不写任何行。
    """
    placed = _make_order(db)
    _looked_up(client, placed)
    csrf: str | None = None
    if case == "wrong":
        csrf = "0" * 64
    elif case == "other-browser":
        csrf = _looked_up(_browser(app), _make_order(db))

    response = _refund(client, placed.number, [(0, 1)], csrf)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "csrf_failed"}
    assert response.headers["cache-control"] == "no-store"
    assert _counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "case", ["no-cookie", "expired-grant", "guest-checkout-only", "other-browser", "unknown-order"]
)
def test_without_lookup_grant_is_401(
    app: FastAPI, db: Session, client: TestClient, case: str
) -> None:
    """查单授权「过期须重新查单」；短期凭据「不能用于确认收货、退款或其他订单」；「服务端校验
    所属订单、到期时间」。SHOP-TASK-029 验收：无 cookie、授权过期、只有 guest_checkout 授权、
    其他浏览器的 cookie（订单号不存在亦同）都 401 access_expired 且响应与无 cookie 相同，先于
    CSRF 校验，不写任何行。
    """
    placed = _make_order(db)
    browser = _browser(app)
    number = placed.number
    csrf: str | None = "0" * 64
    if case == "expired-grant":
        csrf = _looked_up(client, placed)
        _expire_grants(db)
        browser = client
    elif case == "guest-checkout-only":
        token, csrf = _issue(db, placed, SCOPE_GUEST_CHECKOUT)
        browser.headers.update({"Cookie": f"{COOKIE_NAME}={token}"})
    elif case == "other-browser":
        _looked_up(client, placed)
        browser.headers.update({"Cookie": f"{COOKIE_NAME}={secrets.token_urlsafe(32)}"})
    elif case == "unknown-order":
        csrf = _looked_up(client, placed)
        browser = client
        number = generate_order_number()

    response = _refund(browser, number, [(0, 1)], csrf)
    baseline = _refund(_browser(app), placed.number, [(0, 1)], None)

    _assert_access_expired(response)
    _assert_access_expired(baseline)
    assert response.content == baseline.content
    assert _counts(db) == (0, 0, 0)


def test_grant_for_order_a_cannot_refund_order_b(
    app: FastAPI, db: Session, client: TestClient
) -> None:
    """查单授权「不能替代其他订单的订单号加电话验证」。SHOP-TASK-029 验收：同一会话已授权订单
    A、未授权订单 B 时，借请求体里 B 的订单号申请退款，返回与无 cookie 相同的 401，B 无任何
    退款记录。
    """
    order_a = _make_order(db)
    order_b = _make_order(db)
    csrf = _looked_up(client, order_a)

    response = _refund(client, order_b.number, [(0, 1)], csrf)
    baseline = _refund(_browser(app), order_b.number, [(0, 1)], csrf)

    _assert_access_expired(response)
    assert response.content == baseline.content
    stmt = select(func.count()).where(RefundRequest.order_id == order_b.id)
    assert db.scalar(stmt) == 0
    assert _counts(db) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("other_lines", "code"),
    [
        pytest.param([RefundLineRequest(0, 2)], "refund_duplicate", id="last-units"),
        pytest.param(
            [RefundLineRequest(0, 2), RefundLineRequest(1, 1)],
            "refund_nothing_left",
            id="everything-left",
        ),
    ],
)
def test_two_keys_racing_for_same_units(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    other_lines: list[RefundLineRequest],
    code: str,
) -> None:
    """「退款申请…使用幂等键和数据库唯一约束」；「重复申请同一可退数量被拒绝」。SHOP-TASK-029
    验收：T 恤一行已有一件被占用，两笔不同幂等键争用这一行最后两件，另一会话在本请求读完可退件
    之后、写申请之前先提交：本请求写逐件记录遇唯一约束冲突后回滚，按重新读取的可退件返回 409
    refund_duplicate（马克杯仍可退）或 refund_nothing_left（马克杯也被另一笔占用）；只有一笔
    成功，不出现同一件被两笔有效申请占用。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    first_key, other_key = _key(), _key()
    assert _refund(client, placed.number, [(0, 1)], csrf, key=first_key).status_code == 201

    def submit(other: Session) -> None:
        refunds.submit_refund(other, placed.id, other_lines, other_key, _now())

    _after_first_state_read(monkeypatch, lambda: _in_other_session(engine, submit))

    response = _refund(client, placed.number, [(0, 2)], csrf)

    _assert_conflict(response, code)
    assert set(db.scalars(select(RefundRequest.idempotency_key))) == {first_key, other_key}
    assert _occupied_once(db)
    taken = 1 + sum(line.quantity for line in other_lines)
    assert [row[3] for row in _unit_rows(db)] == [1] * taken


@pytest.mark.parametrize("same_request", [True, False], ids=["replay", "conflict"])
def test_same_key_insert_race(
    engine: Engine,
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    same_request: bool,
) -> None:
    """「相同键相同请求返回原结果；同键不同内容报冲突」。SHOP-TASK-029 验收：同一幂等键并发
    插入，另一会话先提交了同键的申请：本请求写申请遇唯一约束冲突后回滚，按指纹重放 200 或
    返回 409 idempotency_conflict；只留一笔申请。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()
    other_lines = [RefundLineRequest(0, 2)] if same_request else [RefundLineRequest(0, 1)]

    def submit(other: Session) -> None:
        refunds.submit_refund(other, placed.id, other_lines, key, _now())

    _after_first_state_read(monkeypatch, lambda: _in_other_session(engine, submit))

    response = _refund(client, placed.number, [(0, 2)], csrf, key=key)

    if same_request:
        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": REFUND_REQUESTED,
            "amount_sen": TEE_CASH[0] + TEE_CASH[1],
            "lines": [{"line_index": 0, "quantity": 2}],
        }
    else:
        _assert_conflict(response, "idempotency_conflict")
    quantity = other_lines[0].quantity
    assert _counts(db) == (1, 1, quantity)
    assert _occupied_once(db)


# ---------------------------------------------------------------------------
# 查看接口的退款部分：GET /api/orders/lookup
# ---------------------------------------------------------------------------


def test_view_refund_fields_for_paid_order(db: Session, client: TestClient) -> None:
    """「系统显示累计已退及剩余可退金额」；HANDOFF 0.27 截止时间；P09 游客订单不显示积分。
    SHOP-TASK-029 验收：订单行序；截止时间为支付时间加 30×24 小时、在退款期内；累计已退 0、剩余
    可退为商品现金实付合计（不含运费）；每行可退件数与预计金额列表；没有申请记录；不含积分字段。
    """
    placed = _make_order(db)
    _looked_up(client, placed)
    assert placed.paid_at is not None
    deadline = (placed.paid_at + WINDOW).replace(tzinfo=UTC)

    view = _view(client)

    assert datetime.fromisoformat(view["refund_deadline"]) == deadline
    assert view["refund_window_open"] is True
    assert (view["refunded_total_sen"], view["refundable_left_sen"]) == (0, CASH_TOTAL)
    assert view["fully_refunded"] is False
    assert view["refund_requests"] == []
    assert [
        (line["line_index"], line["refundable_quantity"], line["refund_estimates_sen"])
        for line in view["lines"]
    ] == [(0, 3, [2701, 5400, 8100]), (1, 1, [1500])]
    assert "point" not in json.dumps(view)


@pytest.mark.parametrize("status", [STATUS_AWAITING_PAYMENT, STATUS_CANCELLED])
def test_view_unpaid_order_has_no_deadline(db: Session, client: TestClient, status: str) -> None:
    """「支付成功后 30 天内可申请退款」。SHOP-TASK-029 验收：未支付时退款截止时间为空、不在
    退款期内。
    """
    placed = _make_order(db, status)
    _looked_up(client, placed)

    view = _view(client)

    assert (view["refund_deadline"], view["refund_window_open"]) == (None, False)


@pytest.mark.parametrize(
    ("offset", "open_"),
    [
        pytest.param(timedelta(seconds=-1), True, id="one-second-before"),
        pytest.param(timedelta(seconds=1), False, id="one-second-after"),
    ],
)
def test_view_window_open_around_deadline(
    db: Session,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    open_: bool,
) -> None:
    """HANDOFF 0.27 截止时间为支付时刻加 30×24 小时；P09「截止时间以服务端
    order.refund_deadline 为准」。SHOP-TASK-029 验收：是否在退款期内由服务端判定，在截止时刻
    前一秒为真、后一秒为假。
    """
    now = _now()
    placed = _make_order(db, paid_at=now - WINDOW - offset)
    _looked_up(client, placed)
    _fix_now(monkeypatch, order_lookup_api, now)

    view = _view(client)

    assert datetime.fromisoformat(view["refund_deadline"]) == (now - offset).replace(tzinfo=UTC)
    assert view["refund_window_open"] is open_


def test_view_counts_only_approved_and_lists_newest_first(db: Session, client: TestClient) -> None:
    """「系统显示累计已退及剩余可退金额」；「requested → 管理员 approved 或 rejected」；HANDOFF
    0.27「尚未被审核中或已批准的申请占用的件」。SHOP-TASK-029 验收：累计已退只计 approved；
    剩余可退扣除审核中与已批准占用的件、不扣被拒释放的件；每行预计金额列表随之从下一件起算；
    申请记录按创建时间从新到旧，含状态、各行名称与规格说明快照（按语言）、件数与金额。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    approved, requested, rejected = _key(), _key(), _key()
    assert _refund(client, placed.number, [(0, 1)], csrf, key=approved).status_code == 201
    assert _refund(client, placed.number, [(1, 1)], csrf, key=requested).status_code == 201
    assert _refund(client, placed.number, [(0, 1)], csrf, key=rejected).status_code == 201
    _review(db, approved, REFUND_APPROVED)
    _review(db, rejected, REFUND_REJECTED)
    base = _now() - timedelta(hours=3)
    created = {
        approved: base,
        rejected: base + timedelta(hours=1),
        requested: base + timedelta(hours=2),
    }
    for key, at in created.items():
        stmt = update(RefundRequest).where(RefundRequest.idempotency_key == key)
        db.execute(stmt.values(created_at=at))
    db.commit()

    view = _view(client)
    zh = _view(client, "zh")

    assert view["refunded_total_sen"] == TEE_CASH[0]
    assert view["refundable_left_sen"] == TEE_CASH[1] + TEE_CASH[2]
    assert view["fully_refunded"] is False
    lines = [(line["refundable_quantity"], line["refund_estimates_sen"]) for line in view["lines"]]
    assert lines == [(2, [2699, 5399]), (0, [])]
    records = view["refund_requests"]
    assert [datetime.fromisoformat(record["created_at"]) for record in records] == [
        created[key].replace(tzinfo=UTC) for key in (requested, rejected, approved)
    ]
    assert [
        {key: value for key, value in record.items() if key != "created_at"} for record in records
    ] == [
        {
            "status": REFUND_REQUESTED,
            "amount_sen": 1500,
            "lines": [{"name": "Mug", "variant_label": "", "quantity": 1, "amount_sen": 1500}],
        },
        {
            "status": REFUND_REJECTED,
            "amount_sen": TEE_CASH[1],
            "lines": [
                {"name": "Tee", "variant_label": "Colour: Red", "quantity": 1, "amount_sen": 2699}
            ],
        },
        {
            "status": REFUND_APPROVED,
            "amount_sen": TEE_CASH[0],
            "lines": [
                {"name": "Tee", "variant_label": "Colour: Red", "quantity": 1, "amount_sen": 2701}
            ],
        },
    ]
    assert [line["name"] for record in zh["refund_requests"] for line in record["lines"]] == [
        "马克杯",
        "T恤",
        "T恤",
    ]


def test_view_fully_refunded_only_when_all_approved(db: Session, client: TestClient) -> None:
    """「所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进」（冻结由之后的任务做）。
    SHOP-TASK-029 验收：全部件都在审核中时剩余可退为 0 但不是全部已退；全部件都已被 approved
    申请占用时为全部已退，累计已退为商品现金实付合计。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    key = _key()
    assert _refund(client, placed.number, [(0, 3), (1, 1)], csrf, key=key).status_code == 201

    pending = _view(client)
    _review(db, key, REFUND_APPROVED)
    done = _view(client)

    assert (pending["refundable_left_sen"], pending["fully_refunded"]) == (0, False)
    assert pending["refunded_total_sen"] == 0
    assert (done["refundable_left_sen"], done["fully_refunded"]) == (0, True)
    assert done["refunded_total_sen"] == CASH_TOTAL
    assert [line["refund_estimates_sen"] for line in done["lines"]] == [[], []]


# ---------------------------------------------------------------------------
# 请求格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded"],
    ids=["missing", "text", "form"],
)
def test_non_json_is_415(db: Session, client: TestClient, content_type: str | None) -> None:
    """SHOP-TASK-029 验收：只接受 JSON（否则 415），带 no-store，不回显请求内容，不写任何行。"""
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    headers = {"Idempotency-Key": _key(), "X-CSRF-Token": csrf}
    if content_type is not None:
        headers["Content-Type"] = content_type
    body = {"order_number": placed.number, "lines": [{"line_index": 0, "quantity": 1}]}

    response = client.post(REFUND_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == "no-store"
    assert placed.number not in response.text
    assert _counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Content-Type": "application/json", "Idempotency-Key": "k" * 20}, id="json"),
        pytest.param({"Content-Type": "application/json"}, id="no-key"),
        pytest.param({"Content-Type": "text/plain"}, id="not-json"),
    ],
)
def test_oversized_body_is_413_first(app: FastAPI, db: Session, headers: dict[str, str]) -> None:
    """SHOP-TASK-029 验收：请求体上限 8 KB 且先于其他校验（超出 413）——带多余字段、缺幂等键、
    不是 JSON、没有 cookie 与 CSRF 的超限请求体都是 413；不回显请求内容，带 no-store。
    """
    placed = _make_order(db)
    padding = "0123456789" * (MAX_BODY_BYTES // 10)
    body = {"order_number": placed.number, "lines": [], "note": padding}

    response = _browser(app).post(REFUND_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "0123456789" not in response.text
    assert placed.number not in response.text
    assert _counts(db) == (0, 0, 0)


KEY_LOC = ["header", "Idempotency-Key"]


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"key": None}, KEY_LOC, id="missing-key"),
        pytest.param({"key": "a" * 15}, KEY_LOC, id="short-key"),
        pytest.param({"body": {"note": "secret-note"}}, ["body", "note"], id="extra-field"),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": 1, "amount_sen": 4242}]},
            ["body", "lines", 0, "amount_sen"],
            id="extra-line-field",
        ),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": 1}, {"line_index": 0, "quantity": 2}]},
            ["body", "lines"],
            id="duplicate-line-index",
        ),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": 0}]},
            ["body", "lines", 0, "quantity"],
            id="zero-quantity",
        ),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": 100}]},
            ["body", "lines", 0, "quantity"],
            id="quantity-over-99",
        ),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": "2"}]},
            ["body", "lines", 0, "quantity"],
            id="quantity-not-integer",
        ),
        pytest.param(
            {"lines": [{"line_index": index, "quantity": 1} for index in range(21)]},
            ["body", "lines"],
            id="21-lines",
        ),
        pytest.param({"lines": []}, ["body", "lines"], id="no-lines"),
        pytest.param({"drop": True}, ["body", "order_number"], id="no-order-number"),
    ],
)
def test_invalid_request_is_422_without_echo(
    db: Session, client: TestClient, change: dict[str, Any], loc: list[Any]
) -> None:
    """凭据不写入「错误回显」。SHOP-TASK-029 验收：请求体只有订单号与申请行列表（1 到 20 项，
    行序不重复，件数为 1 到 99 的整数），多出字段 422；须带 Idempotency-Key（规则同
    SHOP-TASK-020）；422 每条错误只含位置、类型与固定消息，不回显请求内容，带 no-store；
    不写任何行。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    body: dict[str, Any] = {
        "order_number": placed.number,
        "lines": change.get("lines", [{"line_index": 0, "quantity": 1}]),
    } | change.get("body", {})
    if change.get("drop"):
        del body["order_number"]
    headers = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    key = change.get("key", _key())
    if key is not None:
        headers["Idempotency-Key"] = key

    response = client.post(REFUND_URL, content=json.dumps(body), headers=headers)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in [placed.number, "secret-note", "4242", "a" * 15]:
        assert value not in response.text
    assert _counts(db) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 模型约束
# ---------------------------------------------------------------------------


def _item_and_units(db: Session, placed: _Placed) -> tuple[int, list[int]]:
    """该单第 0 行的订单行 ID 与按件序的逐件分摊快照 ID。"""
    item_id = db.scalars(
        select(OrderItem.id).where(OrderItem.order_id == placed.id, OrderItem.line_index == 0)
    ).one()
    stmt = (
        select(OrderItemUnit.id)
        .where(OrderItemUnit.order_item_id == item_id)
        .order_by(OrderItemUnit.unit_index)
    )
    return item_id, list(db.scalars(stmt))


def _request_row(order_id: int, **values: Any) -> RefundRequest:
    row: dict[str, Any] = {
        "order_id": order_id,
        "status": REFUND_REQUESTED,
        "actor_type": ACTOR_GUEST,
        "idempotency_key": _key(),
        "request_fingerprint": "a" * 64,
        "amount_sen": 2701,
        "created_at": _now(),
        "reviewed_at": None,
    }
    return RefundRequest(**(row | values))


def _line_row(request_id: int, item_id: int, **values: Any) -> RefundLine:
    row: dict[str, Any] = {
        "refund_request_id": request_id,
        "order_item_id": item_id,
        "quantity": 1,
        "amount_sen": 2701,
    }
    return RefundLine(**(row | values))


def _unit_row(line_id: int, unit_id: int, **values: Any) -> RefundLineUnit:
    row: dict[str, Any] = {
        "refund_line_id": line_id,
        "order_item_unit_id": unit_id,
        "cash_paid_sen": 2701,
        "occupied": 1,
    }
    return RefundLineUnit(**(row | values))


@pytest.mark.parametrize(
    ("table", "values", "constraint"),
    [
        pytest.param("refund_requests", {"status": "pending"}, "status_valid", id="status"),
        pytest.param("refund_requests", {"actor_type": "admin"}, "actor_type_valid", id="admin"),
        pytest.param("refund_requests", {"actor_type": "system"}, "actor_type_valid", id="sys"),
        pytest.param(
            "refund_requests", {"idempotency_key": ""}, "idempotency_key_not_empty", id="key"
        ),
        pytest.param(
            "refund_requests",
            {"request_fingerprint": "a" * 63},
            "request_fingerprint_length",
            id="fingerprint",
        ),
        pytest.param(
            "refund_requests", {"amount_sen": -1}, "amount_sen_non_negative", id="request-amount"
        ),
        pytest.param(
            "refund_requests",
            {"reviewed_at": REVIEWED_AT},
            "requested_has_no_reviewed_at",
            id="requested-reviewed",
        ),
        pytest.param(
            "refund_requests",
            {"status": REFUND_APPROVED},
            "reviewed_status_has_reviewed_at",
            id="approved-unreviewed",
        ),
        pytest.param(
            "refund_requests",
            {"status": REFUND_REJECTED},
            "reviewed_status_has_reviewed_at",
            id="rejected-unreviewed",
        ),
        pytest.param("refund_lines", {"quantity": 0}, "quantity_positive", id="line-quantity"),
        pytest.param(
            "refund_lines", {"amount_sen": -1}, "amount_sen_non_negative", id="line-amount"
        ),
        pytest.param(
            "refund_line_units", {"cash_paid_sen": -1}, "cash_paid_sen_non_negative", id="cash"
        ),
        pytest.param("refund_line_units", {"occupied": 0}, "occupied_valid", id="occupied-0"),
        pytest.param("refund_line_units", {"occupied": 2}, "occupied_valid", id="occupied-2"),
    ],
)
def test_model_check_constraints(
    db: Session, table: str, values: dict[str, Any], constraint: str
) -> None:
    """「数据模型」RefundRequest / RefundLine：「申请状态、每个商品规格的申请数量、按原订单快照
    计算的可退商品金额」。SHOP-TASK-029 验收：状态只允许 requested、approved、rejected；申请方只
    允许 guest 与 member；幂等键非空、指纹长 64；金额不小于零；requested 时审核时间为空、approved
    与 rejected 时非空；件数不小于 1；占用标记只允许 1 或空。
    """
    placed = _make_order(db)
    item_id, unit_ids = _item_and_units(db, placed)
    if table == "refund_requests":
        db.add(_request_row(placed.id, **values))
    else:
        request = _add(db, _request_row(placed.id))
        if table == "refund_lines":
            db.add(_line_row(request.id, item_id, **values))
        else:
            line = _add(db, _line_row(request.id, item_id))
            db.add(_unit_row(line.id, unit_ids[0], **values))

    with pytest.raises(IntegrityError, match=rf"CHECK constraint failed: ck_{table}_{constraint}"):
        db.flush()


def test_model_accepts_reviewed_member_and_released_rows(db: Session) -> None:
    """「requested → 管理员 approved 或 rejected」；会员「在「我的订单」…申请退款」；HANDOFF 0.27
    「被拒绝的申请释放所占的件」。SHOP-TASK-029 验收：带审核时间的 approved 与 rejected、申请方
    member、占用标记为空的逐件记录都被接受。审核人、审核幂等键、审核请求指纹与拒绝理由是
    SHOP-TASK-039 的检查约束要求补上的。
    """
    placed = _make_order(db)
    item_id, unit_ids = _item_and_units(db, placed)
    admin = _add(
        db,
        AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            password_updated_at=REVIEWED_AT,
        ),
    )
    for status in (REFUND_APPROVED, REFUND_REJECTED):
        review = {
            "reviewer_admin_id": admin.id,
            "review_idempotency_key": _key(),
            "review_fingerprint": "b" * 64,
            "review_reason": "Demo rejection" if status == REFUND_REJECTED else None,
        }
        _add(db, _request_row(placed.id, status=status, reviewed_at=REVIEWED_AT, **review))
    request = _add(db, _request_row(placed.id, actor_type=ACTOR_MEMBER))
    line = _add(db, _line_row(request.id, item_id))
    _add(db, _unit_row(line.id, unit_ids[0], occupied=None))


@pytest.mark.parametrize(
    ("case", "columns"),
    [
        pytest.param("request-key", "refund_requests.idempotency_key", id="request-key"),
        pytest.param(
            "line-item",
            "refund_lines.refund_request_id, refund_lines.order_item_id",
            id="line-item",
        ),
        pytest.param(
            "unit-in-line",
            "refund_line_units.refund_line_id, refund_line_units.order_item_unit_id",
            id="unit-in-line",
        ),
        pytest.param(
            "unit-occupied",
            "refund_line_units.order_item_unit_id, refund_line_units.occupied",
            id="unit-occupied",
        ),
    ],
)
def test_model_unique_constraints(db: Session, case: str, columns: str) -> None:
    """「退款申请…使用幂等键和数据库唯一约束」；「重复申请同一可退数量被拒绝」。SHOP-TASK-029
    验收：幂等键全表唯一；同一申请与订单行唯一；同一申请行与逐件分摊快照唯一；同一逐件分摊快照与
    占用标记组合唯一（同一件同时最多被一笔有效申请占用）。
    """
    placed = _make_order(db)
    item_id, unit_ids = _item_and_units(db, placed)
    request = _add(db, _request_row(placed.id))
    line = _add(db, _line_row(request.id, item_id))
    if case == "request-key":
        db.add(_request_row(placed.id, idempotency_key=request.idempotency_key))
    elif case == "line-item":
        db.add(_line_row(request.id, item_id))
    elif case == "unit-in-line":
        _add(db, _unit_row(line.id, unit_ids[0], occupied=None))
        db.add(_unit_row(line.id, unit_ids[0], occupied=None))
    else:
        _add(db, _unit_row(line.id, unit_ids[0]))
        other = _add(db, _request_row(placed.id))
        other_line = _add(db, _line_row(other.id, item_id))
        db.add(_unit_row(other_line.id, unit_ids[0]))

    with pytest.raises(IntegrityError, match=f"UNIQUE constraint failed: {re.escape(columns)}"):
        db.flush()


def test_model_released_units_do_not_collide(db: Session) -> None:
    """HANDOFF 0.27「被拒绝的申请释放所占的件」。SHOP-TASK-029 验收：唯一约束允许多个空值，同一
    件可有多条已释放的记录与至多一条占用标记为 1 的记录。
    """
    placed = _make_order(db)
    item_id, unit_ids = _item_and_units(db, placed)
    for occupied in (None, None, 1):
        request = _add(db, _request_row(placed.id))
        line = _add(db, _line_row(request.id, item_id))
        _add(db, _unit_row(line.id, unit_ids[0], occupied=occupied))


@pytest.mark.parametrize(
    "case",
    ["request-order", "line-request", "line-item", "unit-line", "unit-snapshot", "restrict"],
)
def test_model_foreign_keys(db: Session, case: str) -> None:
    """「数据模型」RefundRequest / RefundLine 引用原订单快照。SHOP-TASK-029 验收：申请的所属订单、
    申请行的所属申请与所属订单行、逐件记录的所属申请行与所属逐件分摊快照都是外键，拒绝不存在的
    父行；外键 RESTRICT，被引用的逐件分摊快照不能删除。
    """
    placed = _make_order(db)
    item_id, unit_ids = _item_and_units(db, placed)
    missing = 999_999
    write: Callable[[], Any] = db.flush
    if case == "request-order":
        db.add(_request_row(missing))
    else:
        request = _add(db, _request_row(placed.id))
        if case == "line-request":
            db.add(_line_row(missing, item_id))
        elif case == "line-item":
            db.add(_line_row(request.id, missing))
        else:
            line = _add(db, _line_row(request.id, item_id))
            if case == "unit-line":
                db.add(_unit_row(missing, unit_ids[0]))
            elif case == "unit-snapshot":
                db.add(_unit_row(line.id, missing))
            else:
                _add(db, _unit_row(line.id, unit_ids[0]))
                stmt = delete(OrderItemUnit).where(OrderItemUnit.id == unit_ids[0])
                write = lambda: db.execute(stmt)  # noqa: E731

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        write()


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(
    db: Session, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """「应用日志与监控不记录姓名、完整电话、地址…订单查询参数」。SHOP-TASK-029 验收：
    退款接口与查看接口不写日志。
    """
    placed = _make_order(db)
    csrf = _looked_up(client, placed)
    caplog.set_level(logging.DEBUG)

    _refund(client, placed.number, [(0, 1)], csrf)
    _refund(client, placed.number, [(0, 9)], csrf)
    _refund(client, placed.number, [(0, 1)], None)
    client.get(LOOKUP_URL)

    assert [record for record in caplog.records if record.name.startswith("app")] == []

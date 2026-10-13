"""退款申请页的积分预估（查单模式）：POST /api/orders/refunds/points-estimate（SHOP-TASK-093）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 4、5 条（分摊算例；退款批准时
按原逐件快照返还未过期的抵扣积分并追回获得积分，原抵扣积分批次若已经过期，不返还已过期积分）、
「权限与资料保护」第 6 条（访客仅能访问已通过查询验证的订单），docs/UX.md 0.10 P10（会员订单另见
积分返还与追回 refund.points_back、refund.points_reversed，已过期的抵扣积分不返还），以及
docs/HANDOFF.md 0.27 记录的 Kelvin 2026-10-01 退款决定（下称「HANDOFF 0.27」：按件序从前往后取
尚未被审核中或已批准的申请占用的件）。每条测试的文档字符串写明它守住的设计或 UX 原句；没有直接
原句的，写明是 SHOP-TASK-093 验收里的约定。

写法照 tests/test_refunds.py：SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开），
接口的会话依赖换成每个请求一个绑定同一内存库的会话。会员、订单与逐件分摊快照直接写库，积分批次与
流水经 SHOP-TASK-086 的 earn_points 与 reserve_points 准备，已有的退款申请经 SHOP-TASK-029 的
submit_refund 提交。查单授权直接以 issue_order_access 签发（与 tests/test_refunds.py 的 _issue
相同），不经查单接口，所以不需要 Redis 替身。时间以测试运行时的当前时间为基准。
estimate_refund_points 以原函数外包一层记录入参，判断接口是否调用它。
"""

from __future__ import annotations

import itertools
import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import refunds as refunds_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Member,
    Order,
    OrderItem,
    OrderItemUnit,
    PointEntry,
    PointLot,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP
from app.services.order_access import COOKIE_NAME, issue_order_access
from app.services.order_rules import generate_order_number
from app.services.points import earn_points, lot_expires_at, reserve_points
from app.services.refunds import RefundLineRequest, submit_refund

ESTIMATE_URL = "/api/orders/refunds/points-estimate"
MAX_BODY_BYTES = 8 * 1024
SHIPPING = 800
ACCESS_EXPIRED = {"detail": "access_expired"}

# 逐件分摊快照，按订单行、件序：（原价、分摊券额、分摊积分、获得积分），现金实付由此算出。
Unit = tuple[int, int, int, int]
# DESIGN 第 4 条算例：A 2 件各 RM10、B 1 件 RM5；券 RM2.50、积分 300、获得 19（8、7、4）。
DESIGN_UNITS: list[list[Unit]] = [
    [(1000, 100, 120, 8), (1000, 100, 120, 7)],
    [(500, 50, 60, 4)],
]
# 游客订单：没有积分抵扣，也不获得积分。
GUEST_UNITS: list[list[Unit]] = [
    [(1000, 0, 0, 0), (1000, 0, 0, 0)],
    [(500, 0, 0, 0)],
]

_PHONE_SEQUENCE = itertools.count(400_000_000)


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

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """记录接口调用 estimate_refund_points 的入参（订单 ID、应返还、应追回）。"""
    real = refunds_api.estimate_refund_points
    recorded: list[tuple[Any, ...]] = []

    def spy(session: Session, order_id: int, back: int, rev: int, now: datetime):
        recorded.append((order_id, back, rev))
        return real(session, order_id, back, rev, now)

    monkeypatch.setattr(refunds_api, "estimate_refund_points", spy)
    return recorded


@dataclass(frozen=True)
class _Placed:
    id: int
    number: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _order_row(
    db: Session,
    member_id: int | None,
    paid_at: datetime,
    units: list[list[Unit]] | None = None,
    status: str = STATUS_SHIPPED,
) -> Order:
    """一张订单；units 为空时不写订单行（只作积分批次的来源）。待支付时没有支付时间。"""
    flat = [unit for line in units or [] for unit in line]
    subtotal = sum(unit[0] for unit in flat)
    coupon = sum(unit[1] for unit in flat)
    points = sum(unit[2] for unit in flat)
    created = paid_at - timedelta(minutes=10)
    order = _add(
        db,
        Order(
            order_number=generate_order_number(),
            status=status,
            subtotal_sen=subtotal,
            coupon_discount_sen=coupon,
            points_redeemed=points,
            shipping_fee_sen=SHIPPING,
            total_sen=subtotal - coupon - points + SHIPPING,
            points_earned=sum(unit[3] for unit in flat),
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=_key(),
            request_fingerprint="0" * 64,
            created_at=created,
            payment_expires_at=created + timedelta(minutes=15),
            paid_at=None if status == STATUS_AWAITING_PAYMENT else paid_at,
            member_id=member_id,
            claim_status=CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE,
        ),
    )
    for line_index, line in enumerate(units or []):
        price = line[0][0]
        item = _add(
            db,
            OrderItem(
                order_id=order.id,
                line_index=line_index,
                variant_id=None,
                sku=f"SKU-{line_index}",
                product_name_en="Item",
                product_name_zh="商品",
                product_name_ms="Barang",
                variant_label_en="",
                variant_label_zh="",
                variant_label_ms="",
                unit_price_sen=price,
                quantity=len(line),
                line_subtotal_sen=price * len(line),
            ),
        )
        for unit_index, (original, unit_coupon, unit_points, earned) in enumerate(line):
            db.add(
                OrderItemUnit(
                    order_item_id=item.id,
                    unit_index=unit_index,
                    original_price_sen=original,
                    coupon_discount_sen=unit_coupon,
                    points_discount=unit_points,
                    cash_paid_sen=original - unit_coupon - unit_points,
                    points_earned=earned,
                )
            )
    db.flush()
    return order


def _design_order(db: Session, paid_at: datetime, prior_acquired_at: datetime) -> _Placed:
    """DESIGN 第 4 条算例的会员订单：会员在 prior_acquired_at 获得 500 积分（另一张来源订单），
    下单时预占 300、支付成功时获得 19。提交。
    """
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    member = _add(
        db,
        Member(phone=phone, status=MEMBER_ACTIVE, created_at=prior_acquired_at - timedelta(days=1)),
    )
    source = _order_row(db, member.id, prior_acquired_at)
    assert earn_points(db, member.id, source.id, 500, prior_acquired_at) is not None
    order = _order_row(db, member.id, paid_at, DESIGN_UNITS)
    reserve_points(db, member.id, order.id, order.points_redeemed, paid_at - timedelta(minutes=5))
    assert earn_points(db, member.id, order.id, order.points_earned, paid_at) is not None
    placed = _Placed(order.id, order.order_number)
    db.commit()
    return placed


def _guest_order(db: Session, paid_at: datetime, status: str = STATUS_SHIPPED) -> _Placed:
    order = _order_row(db, None, paid_at, GUEST_UNITS, status)
    placed = _Placed(order.id, order.order_number)
    db.commit()
    return placed


def _browser(app: FastAPI, db: Session, placed: _Placed | None, scope: str = SCOPE_LOOKUP):
    """一个新浏览器；placed 不为空时带上该单该范围的授权 cookie。"""
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    client = TestClient(app, base_url="https://testserver")
    if placed is not None:
        issued = issue_order_access(db, None, placed.id, scope, _now())
        db.commit()
        assert issued.new_token is not None
        client.headers.update({"Cookie": f"{COOKIE_NAME}={issued.new_token}"})
    return client


def _estimate(client: TestClient, number: str, lines: list[tuple[int, int]]) -> Any:
    # 不带幂等键与 CSRF 令牌：预估不要求它们。
    body = {
        "order_number": number,
        "lines": [{"line_index": index, "quantity": quantity} for index, quantity in lines],
    }
    return client.post(ESTIMATE_URL, json=body)


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _assert_conflict(response: Any, code: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": code}
    assert response.headers["cache-control"] == "no-store"


def _assert_access_expired(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == ACCESS_EXPIRED
    assert response.headers["cache-control"] == "no-store"


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """积分批次、积分流水、退款申请、申请行与逐件记录的全部内容。"""
    with Session(engine) as session:
        tables = (PointLot, PointEntry, RefundRequest, RefundLine, RefundLineUnit)
        return tuple(
            sorted(tuple(row) for row in session.execute(select(model.__table__)))
            for model in tables
        )


# ---------------------------------------------------------------------------
# 预估结果
# ---------------------------------------------------------------------------


def test_design_example_first_a_estimates_120_back_and_8_reversed(
    app: FastAPI, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 4 条算例「仅退第一件 A 且原抵扣积分尚未到期时 … 返还原抵扣的 120 积分，并追回
    该件获得的 8 积分」；UX P10「会员订单另见积分返还与追回 refund.points_back、
    refund.points_reversed」。SHOP-TASK-093 验收：应返还与应追回为选中各件的 points_discount 与
    points_earned 之和，交给预估函数；member_order 为真。
    """
    now = _now()
    paid = now - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))

    body = _ok(_estimate(_browser(app, db, placed), placed.number, [(0, 1)]))

    assert body == {"member_order": True, "points_back": 120, "points_reversed": 8}
    assert calls == [(placed.id, 120, 8)]


def test_whole_order_estimates_all_points(
    app: FastAPI, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 5 条「累计返还抵扣积分 ≤ 该单实际抵扣积分；累计追回获得积分 ≤ 该单实际
    获得积分」：申请全部三件时预估返还 300、追回 19，正好是该单实际抵扣与获得。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))

    body = _ok(_estimate(_browser(app, db, placed), placed.number, [(1, 1), (0, 2)]))

    assert body == {"member_order": True, "points_back": 300, "points_reversed": 19}
    assert calls == [(placed.id, 300, 19)]


def test_expired_redeem_lot_estimates_no_points_back(
    app: FastAPI, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 5 条「原抵扣积分批次若已经过期，不返还已过期积分」；UX P10「已过期的抵扣积分
    不返还」：抵扣所用批次在下单后、预估前到期，第一件 A 的预估返还为 0，追回仍为 8。
    """
    now = _now()
    paid = now - timedelta(days=5)
    # 取得后 12 个自然月到期：在支付之后、现在之前。
    acquired = paid - timedelta(days=364)
    assert paid < lot_expires_at(acquired) < now
    placed = _design_order(db, paid, acquired)

    body = _ok(_estimate(_browser(app, db, placed), placed.number, [(0, 1)]))

    assert body == {"member_order": True, "points_back": 0, "points_reversed": 8}
    assert calls == [(placed.id, 120, 8)]


def test_picks_lowest_units_not_taken_by_pending_requests(
    app: FastAPI, engine: Engine, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """HANDOFF 0.27「按件序从前往后取尚未被审核中或已批准的申请占用的件」。SHOP-TASK-093 验收：
    每行用 pick_units 取件序最小的那几件，与提交申请时选件相同：第一件 A 已被审核中的申请占用时，
    申请一件 A 预估的是第二件 A（追回 7）。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))
    with Session(engine) as other:
        submit_refund(other, placed.id, [RefundLineRequest(0, 1)], _key(), _now())

    body = _ok(_estimate(_browser(app, db, placed), placed.number, [(0, 1)]))

    assert body == {"member_order": True, "points_back": 120, "points_reversed": 7}
    assert calls == [(placed.id, 120, 7)]


def test_guest_order_is_not_member_order(
    app: FastAPI, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 3 条「游客不积累积分」；UX P10 只有「会员订单另见积分返还与追回」。
    SHOP-TASK-093 验收：订单的 member_id 为空时 member_order 为假、两个数为 0，不调用预估函数。
    """
    placed = _guest_order(db, _now() - timedelta(days=5))

    body = _ok(_estimate(_browser(app, db, placed), placed.number, [(0, 2), (1, 1)]))

    assert body == {"member_order": False, "points_back": 0, "points_reversed": 0}
    assert calls == []


def test_estimate_writes_nothing(
    app: FastAPI, engine: Engine, db: Session, calls: list[tuple[Any, ...]]
) -> None:
    """DESIGN 第 5 条积分只在「退款批准时」返还与追回。SHOP-TASK-093 验收：接口只读、不写库，
    调用前后积分批次与流水、退款申请（含申请行与逐件记录）都不变。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))
    with Session(engine) as other:
        submit_refund(other, placed.id, [RefundLineRequest(1, 1)], _key(), _now())
    client = _browser(app, db, placed)
    before = _snapshot(engine)

    _ok(_estimate(client, placed.number, [(0, 2)]))
    _ok(_estimate(client, placed.number, [(0, 1)]))
    _assert_conflict(_estimate(client, placed.number, [(1, 1)]), "refund_nothing_left")

    assert _snapshot(engine) == before
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 与提交申请相同的拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("taken", [0, 1], ids=["more-than-bought", "more-than-left"])
def test_over_refundable_is_409(
    app: FastAPI, engine: Engine, db: Session, calls: list[tuple[Any, ...]], taken: int
) -> None:
    """DESIGN「订单与退款状态」「总批准数量不超过购买数量」；UX P10 的 refund.nothing_left。
    SHOP-TASK-093 验收：件数超过该行可退件 409 refund_nothing_left，不调用预估函数。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))
    if taken:
        with Session(engine) as other:
            submit_refund(other, placed.id, [RefundLineRequest(0, taken)], _key(), _now())

    response = _estimate(_browser(app, db, placed), placed.number, [(1, 1), (0, 3 - taken)])

    _assert_conflict(response, "refund_nothing_left")
    assert calls == []


def test_unpaid_order_is_409(app: FastAPI, db: Session) -> None:
    """DESIGN「订单与退款状态」「支付成功后 30 天内可申请退款」。SHOP-TASK-093 验收：与提交申请
    相同，待支付订单 409 order_not_refundable。
    """
    placed = _guest_order(db, _now() - timedelta(hours=1), STATUS_AWAITING_PAYMENT)

    response = _estimate(_browser(app, db, placed), placed.number, [(0, 1)])

    _assert_conflict(response, "order_not_refundable")


def test_window_closed_is_409(app: FastAPI, db: Session, calls: list[tuple[Any, ...]]) -> None:
    """DESIGN「订单与退款状态」「支付成功后 30 天内可申请退款」；UX P10 的 refund.window_closed。
    SHOP-TASK-093 验收：退款期已过 409 refund_window_closed，不调用预估函数。
    """
    paid = _now() - timedelta(days=31)
    placed = _design_order(db, paid, paid - timedelta(days=30))

    response = _estimate(_browser(app, db, placed), placed.number, [(0, 1)])

    _assert_conflict(response, "refund_window_closed")
    assert calls == []


def test_unknown_line_index_is_422(app: FastAPI, db: Session) -> None:
    """DESIGN「订单与退款状态」「只选商品与数量」。SHOP-TASK-093 验收：行序不属于该订单 422，
    错误只含位置、类型与固定消息，不回显订单号。
    """
    placed = _guest_order(db, _now() - timedelta(days=5))

    response = _estimate(_browser(app, db, placed), placed.number, [(0, 1), (7, 1)])

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert [error["loc"] for error in errors] == [["body", "lines"]]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    assert placed.number not in response.text


# ---------------------------------------------------------------------------
# 授权
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["no-cookie", "guest-checkout-only", "other-order"])
def test_without_lookup_grant_is_401(
    app: FastAPI, db: Session, calls: list[tuple[Any, ...]], case: str
) -> None:
    """DESIGN「权限与资料保护」第 6 条「访客仅能访问已通过查询验证的订单」；结账的短期凭据
    不能用于退款。SHOP-TASK-093 验收：没有 cookie、只有 guest_checkout 授权、只有另一张订单的 lookup
    授权都 401 access_expired，不调用预估函数。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))
    if case == "no-cookie":
        client = _browser(app, db, None)
    elif case == "guest-checkout-only":
        client = _browser(app, db, placed, SCOPE_GUEST_CHECKOUT)
    else:
        client = _browser(app, db, _guest_order(db, paid))

    _assert_access_expired(_estimate(client, placed.number, [(0, 1)]))
    assert calls == []


# ---------------------------------------------------------------------------
# 请求格式与检查顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content_type", ["application/json", "text/plain"], ids=["json", "not-json"]
)
def test_oversized_body_is_413_first(app: FastAPI, db: Session, content_type: str) -> None:
    """SHOP-TASK-093 验收：请求体上限 8 KB、逐块判断，先于 415、422 与 401；不回显请求内容，
    带 no-store。
    """
    placed = _guest_order(db, _now() - timedelta(days=5))
    padding = "0123456789" * (MAX_BODY_BYTES // 10)
    body = {"order_number": placed.number, "lines": [], "note": padding}

    response = _browser(app, db, None).post(
        ESTIMATE_URL, content=json.dumps(body), headers={"Content-Type": content_type}
    )

    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "0123456789" not in response.text
    assert placed.number not in response.text


def test_non_json_is_415_before_401(app: FastAPI, db: Session) -> None:
    """SHOP-TASK-093 验收：不是 JSON 415，先于结构校验与授权；带 no-store。"""
    placed = _guest_order(db, _now() - timedelta(days=5))

    response = _browser(app, db, None).post(
        ESTIMATE_URL, content="not json", headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 415, response.text
    assert response.headers["cache-control"] == "no-store"
    assert placed.number not in response.text


@pytest.mark.parametrize(
    ("change", "loc"),
    [
        pytest.param({"body": {"note": "secret-note"}}, ["body", "note"], id="extra-field"),
        pytest.param(
            {"lines": [{"line_index": 0, "quantity": 1, "points_back": 4242}]},
            ["body", "lines", 0, "points_back"],
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
            {"lines": [{"line_index": index, "quantity": 1} for index in range(21)]},
            ["body", "lines"],
            id="21-lines",
        ),
        pytest.param({"lines": []}, ["body", "lines"], id="no-lines"),
    ],
)
def test_invalid_request_is_422_before_401_without_echo(
    app: FastAPI, db: Session, change: dict[str, Any], loc: list[Any]
) -> None:
    """DESIGN「权限与资料保护」凭据不写入错误回显。SHOP-TASK-093 验收：请求体与退款申请
    相同（订单号与 1 到 20 项申请行，件数 1 到 99，行序不重复），多出字段 422；422 先于 401
    （这里没有 cookie），不回显请求内容，带 no-store。
    """
    placed = _guest_order(db, _now() - timedelta(days=5))
    body: dict[str, Any] = {
        "order_number": placed.number,
        "lines": change.get("lines", [{"line_index": 0, "quantity": 1}]),
    } | change.get("body", {})

    response = _browser(app, db, None).post(ESTIMATE_URL, json=body)

    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert loc in [error["loc"] for error in errors]
    assert all(set(error) == {"type", "loc", "msg"} for error in errors)
    for value in [placed.number, "secret-note", "4242"]:
        assert value not in response.text


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------


def test_writes_no_log_records(app: FastAPI, db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """DESIGN「权限与资料保护」「应用日志与监控不记录 … 订单查询参数」。SHOP-TASK-093 验收：
    预估接口不写日志。
    """
    paid = _now() - timedelta(days=5)
    placed = _design_order(db, paid, paid - timedelta(days=30))
    client = _browser(app, db, placed)
    caplog.set_level(logging.DEBUG)

    _estimate(client, placed.number, [(0, 1)])
    _estimate(client, placed.number, [(0, 9)])
    _estimate(_browser(app, db, None), placed.number, [(0, 1)])

    assert [record for record in caplog.records if record.name.startswith("app")] == []

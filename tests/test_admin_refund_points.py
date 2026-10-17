"""后台退款详情的积分返还与追回（SHOP-TASK-106）。

覆盖 GET /api/admin/refunds/{refund_id} 的 placed_by_member、points_returned、
points_reversed 与 points_estimated（app/api/admin_refunds.py 与
app/services/admin_refunds.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 4 条算例
（仅退第一件 A 且原抵扣积分尚未到期时，返还原抵扣的 120 积分，并追回该件获得的
8 积分）与第 5 条（退款批准时按原逐件快照返还未过期的抵扣积分并追回获得积分；
积分余额不足时扣至 0，缺口记待抵扣；原抵扣积分批次若已经过期，不返还已过期积分），
docs/UX.md 0.10 的 A03（详情显示 [admin.refund_points]；首版不显示这一行由
Kelvin 2026-10-06 决定，待会员与积分账本任务接入积分字段时加回），以及
docs/HANDOFF.md 0.46 的积分决定。每条测试的文档字符串写明它守住的设计或 UX 原句；
没有直接原句的，写明是 SHOP-TASK-106 验收里的约定。

用 SQLite 内存库按模型建表（StaticPool，每个连接打开外键检查并断言已打开）；
引擎设置与 tests/test_admin_refunds.py 相同（关掉 pysqlite 的事务处理、由引擎发
BEGIN），批准的保存点才和 MySQL 上一样。管理员与后台会话照
tests/test_admin_refunds.py 建立；会员、订单与逐件分摊快照直接写库，积分批次与
流水经 SHOP-TASK-086 的 earn_points、reserve_points 与 forfeit_points 准备，
退款申请经 SHOP-TASK-029 的 submit_refund 提交，批准与拒绝经 SHOP-TASK-040 的
approve_refund 与 reject_refund（批准时由它调用 SHOP-TASK-087 的
apply_refund_points 写积分流水）。接口用真实的当前时间，所以订单与积分的时间
都取在测试开始前一两天。SQLite 不生成 FOR UPDATE：是否加锁以 MySQL 方言编译的
语句检查。
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
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
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
from app.models.order import CLAIM_CLAIMED, CLAIM_NOT_CLAIMABLE, CLAIM_OPEN, STATUS_SHIPPED
from app.models.points import KIND_DEBT_ADD, KIND_REFUND_RETURN, KIND_REFUND_REVERSAL
from app.services.admin_auth import issue_admin_session
from app.services.admin_refunds import refund_detail
from app.services.order_rules import generate_order_number
from app.services.points import earn_points, forfeit_points, lot_expires_at, reserve_points
from app.services.refund_review import approve_refund, reject_refund
from app.services.refunds import RefundLineRequest, submit_refund

REFUNDS_URL = "/api/admin/refunds"
COOKIE_NAME = "__Host-shop_admin_session"
REASON = "Item damaged in transit"
SHIPPING = 800

# 接口取真实的当前时间：支付在两天前，申请在一天前，批准在申请一小时后。
START = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
PAID = START - timedelta(days=2)
NOW = PAID + timedelta(days=1)
REVIEW_AT = NOW + timedelta(hours=1)

# 逐件分摊快照，按订单行、件序：（原价、分摊券额、分摊积分、获得积分），现金实付由此算出。
Unit = tuple[int, int, int, int]
# DESIGN 第 4 条算例：A 2 件各 RM10、B 1 件 RM5；券 RM2.50、积分 300、获得 19（8、7、4）。
DESIGN_UNITS: list[list[Unit]] = [
    [(1000, 100, 120, 8), (1000, 100, 120, 7)],
    [(500, 50, 60, 4)],
]
# 不用券与积分的会员订单：现金 RM25，获得 25（10、10、5）。
CASH_UNITS: list[list[Unit]] = [
    [(1000, 0, 0, 10), (1000, 0, 0, 10)],
    [(500, 0, 0, 5)],
]
# 游客订单：没有券与积分，也不获得积分。
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
            created_at=PAID - timedelta(days=1),
            password_updated_at=PAID - timedelta(days=1),
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
        now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
        issued = issue_admin_session(db, admin_id, now - timedelta(minutes=1))
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
class _Setup:
    member_id: int
    order_id: int
    # 下单之前已有的批次，按给出的顺序。
    prior_lot_ids: tuple[int, ...]
    own_lot_id: int | None


def _key() -> str:
    return f"key-{uuid.uuid4().hex}"


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _new_member(db: Session) -> Member:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    return _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=PAID))


def _order_row(
    db: Session,
    member_id: int | None,
    units: list[list[Unit]] | None = None,
    *,
    claim_status: str | None = None,
) -> Order:
    """一张已发货的订单；units 为空时不写订单行（只作积分批次的来源或预占的去处）。

    认领状态默认按下单身份：游客 open，会员 not_claimable。
    """
    flat = [unit for line in units or [] for unit in line]
    subtotal = sum(unit[0] for unit in flat)
    coupon = sum(unit[1] for unit in flat)
    points = sum(unit[2] for unit in flat)
    if claim_status is None:
        claim_status = CLAIM_OPEN if member_id is None else CLAIM_NOT_CLAIMABLE
    created = PAID - timedelta(minutes=10)
    order = _add(
        db,
        Order(
            order_number=generate_order_number(),
            status=STATUS_SHIPPED,
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
            paid_at=PAID,
            member_id=member_id,
            claim_status=claim_status,
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


def _member_order(
    engine: Engine, units: list[list[Unit]], prior_lots: list[tuple[int, datetime]]
) -> _Setup:
    """会员先在 prior_lots 的各时刻获得积分（各自一张来源订单），再下这张订单：
    下单时预占快照的抵扣积分（先到期先用），支付成功时获得快照合计的积分。
    """
    with Session(engine) as db:
        member = _new_member(db)
        prior: list[int] = []
        for points, acquired_at in prior_lots:
            lot = earn_points(db, member.id, _order_row(db, member.id).id, points, acquired_at)
            assert lot is not None
            prior.append(lot.id)
        order = _order_row(db, member.id, units)
        reserve_points(db, member.id, order.id, order.points_redeemed, PAID - timedelta(minutes=5))
        own = earn_points(db, member.id, order.id, order.points_earned, PAID)
        setup = _Setup(member.id, order.id, tuple(prior), own.id if own is not None else None)
        db.commit()
    return setup


def _design_order(engine: Engine) -> _Setup:
    """DESIGN 第 4 条算例：会员有一个 500 积分的批次，下单抵扣 300、支付成功获得 19。"""
    return _member_order(engine, DESIGN_UNITS, [(500, NOW - timedelta(days=30))])


def _guest_order(engine: Engine) -> int:
    with Session(engine) as db:
        order_id = _order_row(db, None, GUEST_UNITS).id
        db.commit()
    return order_id


def _submit(engine: Engine, order_id: int, lines: list[tuple[int, int]]) -> int:
    """经 submit_refund 提交一笔退款申请（各行为行序与件数），返回申请 ID。"""
    key = _key()
    requested = [RefundLineRequest(line_index, quantity) for line_index, quantity in lines]
    with Session(engine) as db:
        submit_refund(db, order_id, requested, key, NOW)
        stmt = select(RefundRequest.id).where(RefundRequest.idempotency_key == key)
        return db.scalars(stmt).one()


def _approve(engine: Engine, request_id: int, admin_id: int) -> None:
    with Session(engine) as db:
        approve_refund(db, request_id, admin_id, None, _key(), REVIEW_AT)
        db.commit()


def _reject(engine: Engine, request_id: int, admin_id: int) -> None:
    with Session(engine) as db:
        reject_refund(db, request_id, admin_id, REASON, _key(), REVIEW_AT)
        db.commit()


def _clear_member(engine: Engine, setup: _Setup) -> None:
    """照会员注销的结果：作废余额、会员改为 deleted、订单的 member_id 清空。

    claim_status 不改（仍为 not_claimable）。
    """
    with Session(engine) as db:
        forfeit_points(db, setup.member_id, NOW)
        member = db.get(Member, setup.member_id)
        order = db.get(Order, setup.order_id)
        assert member is not None and order is not None
        member.status = MEMBER_DELETED
        member.phone = None
        member.password_hash = None
        member.deleted_at = NOW
        order.member_id = None
        db.commit()


def _detail(client: TestClient, token: str, refund_id: int) -> dict[str, Any]:
    response = client.get(
        f"{REFUNDS_URL}/{refund_id}",
        params={"lang": "en"},
        headers={"Cookie": f"{COOKIE_NAME}={token}"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _points(body: dict[str, Any]) -> tuple[Any, ...]:
    """placed_by_member、points_returned、points_reversed、points_estimated。"""
    return (
        body["placed_by_member"],
        body["points_returned"],
        body["points_reversed"],
        body["points_estimated"],
    )


def _refund_entries(engine: Engine, request_id: int) -> list[tuple[str, int | None, int]]:
    """该申请的积分流水：（类别、批次、积分），按流水 ID。"""
    with Session(engine) as db:
        rows = db.execute(
            select(PointEntry.kind, PointEntry.lot_id, PointEntry.points)
            .where(PointEntry.refund_request_id == request_id)
            .order_by(PointEntry.id)
        )
        return [tuple(row) for row in rows]


_SNAPSHOT_MODELS = (
    Order,
    PointLot,
    PointEntry,
    RefundRequest,
    RefundLine,
    RefundLineUnit,
    AuditEvent,
)


def _snapshot(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """订单、积分批次与流水、申请、申请行、申请件与审计的全部列值：用来断言「不写库」。"""
    with Session(engine) as db:
        result: dict[str, list[tuple[Any, ...]]] = {}
        for model in _SNAPSHOT_MODELS:
            table = model.__table__
            rows = db.execute(select(table).order_by(*table.primary_key.columns))
            result[table.name] = [tuple(row) for row in rows]
        return result


@contextmanager
def _trace(engine: Engine) -> Iterator[list[str]]:
    """记录期间的语句：查询记为「select:」加 MySQL 方言编译的 SQL，其余语句记类名。"""
    log: list[str] = []

    def on_execute(_conn, clauseelement, _multiparams, _params, _options) -> None:
        if isinstance(clauseelement, str):
            return
        if isinstance(clauseelement, Select):
            log.append("select:" + str(clauseelement.compile(dialect=mysql.dialect())))
        else:
            log.append(type(clauseelement).__name__)

    event.listen(engine, "before_execute", on_execute)
    try:
        yield log
    finally:
        event.remove(engine, "before_execute", on_execute)


# ---------------------------------------------------------------------------
# 已批准：实际流水
# ---------------------------------------------------------------------------


def test_approved_gives_actual_return_and_reversal(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 4 条算例「仅退第一件 A 且原抵扣积分尚未到期时 … 返还原抵扣的
    120 积分，并追回该件获得的 8 积分」与 UX A03 的 [admin.refund_points]。
    SHOP-TASK-106 验收：已批准的申请取该申请的实际积分流水，points_returned 为
    refund_return 之和、points_reversed 为 refund_reversal 之和取负加 debt_add 之和，
    points_estimated 为假；会员下单的订单 placed_by_member 为真；读取不写库。
    """
    setup = _design_order(engine)
    (old_lot,) = setup.prior_lot_ids
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    _approve(engine, request_id, admin_id)
    assert _refund_entries(engine, request_id) == [
        (KIND_REFUND_RETURN, old_lot, 120),
        (KIND_REFUND_REVERSAL, setup.own_lot_id, -8),
    ]
    before = _snapshot(engine)

    body = _detail(client, token, request_id)

    assert _points(body) == (True, 120, 8, False)
    assert _snapshot(engine) == before


def test_approved_reversal_includes_debt(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 5 条「积分余额不足时扣至 0，缺口记待抵扣」与「订单与退款状态」
    「剩余记待抵扣积分」。SHOP-TASK-106 验收：points_reversed 为从余额追回与记入
    待抵扣之和。会员把本单获得的 25 积分用掉 20，批准退第一件 A（追回 10）时余额
    只有 5：从余额追回 5、记入待抵扣 5，详情给出 10。
    """
    setup = _member_order(engine, CASH_UNITS, [])
    with Session(engine) as db:
        other = _order_row(db, setup.member_id)
        reserve_points(db, setup.member_id, other.id, 20, PAID + timedelta(hours=1))
        db.commit()
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    _approve(engine, request_id, admin_id)
    assert _refund_entries(engine, request_id) == [
        (KIND_REFUND_REVERSAL, setup.own_lot_id, -5),
        (KIND_DEBT_ADD, None, 5),
    ]

    body = _detail(client, token, request_id)

    assert _points(body) == (True, 0, 10, False)


# ---------------------------------------------------------------------------
# 审核中：预估
# ---------------------------------------------------------------------------


def test_requested_gives_estimate_and_writes_nothing(
    engine: Engine, client: TestClient, token: str
) -> None:
    """DESIGN 第 4 条算例「仅退第一件 A … 返还原抵扣的 120 积分，并追回该件获得的
    8 积分」。SHOP-TASK-106 验收：审核中的申请按该申请各件逐件快照的
    points_discount 与 points_earned 之和调用 SHOP-TASK-087 的预估函数，给出预估的
    返还与追回，points_estimated 为真；读取不写库（订单、积分批次与流水、申请与
    审计的全部列值都不变）。
    """
    setup = _design_order(engine)
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    before = _snapshot(engine)

    body = _detail(client, token, request_id)

    assert _points(body) == (True, 120, 8, True)
    assert _snapshot(engine) == before
    assert _refund_entries(engine, request_id) == []


def test_requested_estimate_counts_approved_refunds(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 5 条「累计返还抵扣积分 ≤ 该单实际抵扣积分；累计追回获得积分 ≤
    该单实际获得积分」。SHOP-TASK-106 验收：预估按该申请自己的件（第二件 A：抵扣
    120、获得 7），在已批准退款之后的当前状态上计算；已批准的那一笔照旧给出它的
    实际流水。
    """
    setup = _design_order(engine)
    first = _submit(engine, setup.order_id, [(0, 1)])
    _approve(engine, first, admin_id)
    second = _submit(engine, setup.order_id, [(0, 1)])

    assert _points(_detail(client, token, first)) == (True, 120, 8, False)
    assert _points(_detail(client, token, second)) == (True, 120, 7, True)


def test_estimate_uses_the_given_time(engine: Engine) -> None:
    """DESIGN 第 5 条「原抵扣积分批次若已经过期，不返还已过期积分」。
    SHOP-TASK-106 验收：refund_detail 增加当前时间入参（不带时区的 UTC），预估按它
    判断批次是否过期；只读、不锁行（只有查询，以 MySQL 方言编译都不带 FOR UPDATE，
    会话里没有待写对象）。抵扣来自的批次在申请之后到期：到期之前预估返还 120，
    到期之后返还 0；追回 8 都从本单获得、尚未到期的批次扣。
    """
    setup = _design_order(engine)
    request_id = _submit(engine, setup.order_id, [(0, 1)])
    with Session(engine) as db:
        lot = db.get(PointLot, setup.prior_lot_ids[0])
        assert lot is not None
        expired = lot_expires_at(lot.acquired_at) + timedelta(seconds=1)

        with _trace(engine) as log:
            current = refund_detail(db, request_id, "en", REVIEW_AT)
            later = refund_detail(db, request_id, "en", expired)

        assert not db.new and not db.dirty and not db.deleted

    assert current is not None and later is not None
    current_points = (current.points_returned, current.points_reversed, current.points_estimated)
    later_points = (later.points_returned, later.points_reversed, later.points_estimated)
    assert current_points == (120, 8, True)
    assert later_points == (0, 8, True)
    assert log and all(entry.startswith("select:") for entry in log), log
    assert not any("FOR UPDATE" in entry for entry in log), log


# ---------------------------------------------------------------------------
# 已拒绝、游客订单与会员已注销
# ---------------------------------------------------------------------------


def test_rejected_points_are_null(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「订单与退款状态」：积分只在「批准模拟退款时」回退与追回，拒绝不涉及积分。
    SHOP-TASK-106 验收：已拒绝的申请 points_returned、points_reversed 与
    points_estimated 都为 null；placed_by_member 照常给出。
    """
    setup = _design_order(engine)
    request_id = _submit(engine, setup.order_id, [(0, 2)])
    _reject(engine, request_id, admin_id)

    body = _detail(client, token, request_id)

    assert _points(body) == (True, None, None, None)


def test_guest_order_is_not_placed_by_member(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """DESIGN 第 3 条「游客不积累积分」。SHOP-TASK-106 验收：游客订单（claim_status
    为 open）placed_by_member 为假；已批准的申请没有积分流水、两个数为 0，审核中的
    申请按 SHOP-TASK-087 的规则（订单 member_id 为空）预估为零，已拒绝的为 null。
    """
    order_id = _guest_order(engine)
    approved = _submit(engine, order_id, [(0, 1)])
    _approve(engine, approved, admin_id)
    rejected = _submit(engine, order_id, [(0, 1)])
    _reject(engine, rejected, admin_id)
    requested = _submit(engine, order_id, [(1, 1)])

    assert _points(_detail(client, token, approved)) == (False, 0, 0, False)
    assert _points(_detail(client, token, requested)) == (False, 0, 0, True)
    assert _points(_detail(client, token, rejected)) == (False, None, None, None)


def test_claimed_guest_order_is_not_placed_by_member(
    engine: Engine, client: TestClient, token: str
) -> None:
    """DESIGN 第 3 条「认领过去游客订单不追发积分」。SHOP-TASK-106 验收：
    placed_by_member 只看 claim_status 是否为 not_claimable，被认领的游客订单
    （claimed，member_id 不为空）为假；它没有抵扣与获得积分，预估为零。
    """
    with Session(engine) as db:
        member = _new_member(db)
        order_id = _order_row(db, member.id, GUEST_UNITS, claim_status=CLAIM_CLAIMED).id
        db.commit()
    request_id = _submit(engine, order_id, [(0, 1)])

    assert _points(_detail(client, token, request_id)) == (False, 0, 0, True)


def test_cleared_member_order_is_still_placed_by_member(
    engine: Engine, client: TestClient, admin_id: int, token: str
) -> None:
    """「权限与资料保护」第 3 条「注销时积分余额作废」：注销后订单的 member_id
    已清空、claim_status 保留 not_claimable。SHOP-TASK-106 验收：placed_by_member
    仍为真；订单 member_id 为空时预估按 SHOP-TASK-087 的规则为零，此后批准不写
    积分流水、两个数为 0。
    """
    setup = _design_order(engine)
    _clear_member(engine, setup)
    request_id = _submit(engine, setup.order_id, [(0, 1)])

    assert _points(_detail(client, token, request_id)) == (True, 0, 0, True)

    _approve(engine, request_id, admin_id)

    assert _refund_entries(engine, request_id) == []
    assert _points(_detail(client, token, request_id)) == (True, 0, 0, False)

"""会员注销时作废积分余额（注销函数接入 SHOP-TASK-086 的 forfeit_points）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」，讲注销）：
  “积分余额与未用券作废，积分和券使用流水保留去标识化审计记录”，“重新注册不能据此恢复旧订单
  的访问或积分”，“注销后同号重注册不得再次认领或补发积分”。
- 「计价、优惠、积分与库存」第 3 条（下称「积分第 3 条」）：“结账抵扣时预占，失败或取消时释放”。
- docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（下称「Kelvin 0.46」）：积分按批次记账；
  已预占的积分以预占时为准，到期处理跳过已预占的部分。
- docs/UX.md 0.10 的 Q19：优惠券对全部会员相同、不发给个人。
没有直接原句的，写明是 SHOP-TASK-094 验收标准里的约定（下称「验收」），或 SHOP-TASK-086 的作废
规则（已过期的批次记为到期，待抵扣不作废）。

数据库用 SQLite 内存库按模型建表，引擎设置与 tests/test_member_deletion.py 相同（StaticPool，
由引擎发 BEGIN，每个连接打开外键检查并断言已打开）。会员与订单直接写库，积分经 SHOP-TASK-086
的 earn_points、reserve_points 准备，券经 SHOP-TASK-085 的 reserve_coupon、use_coupon 准备。
SQLite 不支持行锁、SQLAlchemy 在 SQLite 上不生成 FOR UPDATE；锁的范围以 MySQL 方言
编译执行到的语句来核对，并发排队只在 MySQL 上成立，这里不能复现。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, insert, select, text, update
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.pool import StaticPool

import app.services.member_deletion as member_deletion
from app.db.base import Base
from app.models import (
    Coupon,
    CouponUse,
    Member,
    Order,
    PointEntry,
    PointLot,
    RefundRequest,
    SiteSetting,
    VerificationAttempt,
)
from app.models.coupon import TYPE_FIXED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_DELETED
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_NOT_CLAIMABLE,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
)
from app.models.points import KIND_DEBT_ADD, KIND_EXPIRE, KIND_FORFEIT, KIND_RELEASE
from app.models.refund import REFUND_REQUESTED
from app.models.site import SITE_SETTING_ID
from app.services.coupons import reserve_coupon, use_coupon
from app.services.member_deletion import delete_member
from app.services.member_sms_login import sms_login_or_register
from app.services.points import (
    available_points,
    debt_points,
    earn_points,
    release_points,
    reserve_points,
)
from app.services.sms_provider import FakeSmsProvider

NOW = datetime(2026, 10, 11, 3, 0, 0)
LATER = NOW + timedelta(hours=1)
# 这一天取得的批次在 NOW 时已过期（马来西亚时间 2026-09-06 23:59:59 到期）。
LONG_AGO = NOW - timedelta(days=400)
PHONE = "+60123456789"
OTHER_PHONE = "+6581234567"
PASSWORD_HASH = "scrypt$existing-password-hash"
FINGERPRINT = "0" * 64
CODE = "482915"

_REQUEST_IDS = itertools.count(1)
_ORDER_SEQUENCE = itertools.count(1)
_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与准备
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_member_deletion.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 保存点与提交才和 MySQL 上一样。
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
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


def add_member(db: Session, phone: str = PHONE) -> int:
    result = db.execute(
        insert(Member).values(
            phone=phone,
            password_hash=PASSWORD_HASH,
            status="active",
            created_at=NOW - timedelta(days=500),
            deleted_at=None,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def add_order(
    db: Session,
    member_id: int,
    status: str = STATUS_PAID,
    created_at: datetime = NOW - timedelta(days=3),
) -> int:
    """会员下单的订单（不可认领）；直接写库，不进会话的对象表。"""
    n = next(_ORDER_SEQUENCE)
    paid = status in PAID_STATUSES
    result = db.execute(
        insert(Order).values(
            order_number=f"{n:016d}",
            status=status,
            subtotal_sen=2500,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=3300,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"order-key-{n}",
            request_fingerprint=FINGERPRINT,
            created_at=created_at,
            payment_expires_at=created_at + timedelta(minutes=15),
            paid_at=created_at + timedelta(minutes=1) if paid else None,
            member_id=member_id,
            claim_status=CLAIM_NOT_CLAIMABLE,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def earn(db: Session, member_id: int, points: int, at: datetime) -> tuple[int, int]:
    """新建一张已支付订单并经 earn_points 在 at 获得积分，提交。

    返回（批次 ID, 来源订单 ID）。
    """
    order_id = add_order(db, member_id)
    lot = earn_points(db, member_id, order_id, points, at)
    assert lot is not None
    lot_id = lot.id
    db.commit()
    return lot_id, order_id


def redeem(db: Session, member_id: int, points: int, status: str, at: datetime) -> int:
    """新建一张订单并经 reserve_points 在 at 预占积分，提交；返回订单 ID。"""
    order_id = add_order(db, member_id, status=status, created_at=at)
    reserve_points(db, member_id, order_id, points, at)
    db.commit()
    return order_id


def add_debt(db: Session, member_id: int, points: int) -> None:
    """退款追回不足时记入的待抵扣（debt_add，批次为空，带退款申请）。

    写法同 tests/test_points.py。
    """
    order_id = add_order(db, member_id)
    refund = RefundRequest(
        order_id=order_id,
        status=REFUND_REQUESTED,
        actor_type=ACTOR_MEMBER,
        idempotency_key=f"refund-key-{next(_SEQUENCE)}",
        request_fingerprint=FINGERPRINT,
        amount_sen=1000,
        created_at=NOW - timedelta(days=2),
    )
    db.add(refund)
    db.flush()
    db.add(
        PointEntry(
            member_id=member_id,
            lot_id=None,
            kind=KIND_DEBT_ADD,
            points=points,
            order_id=order_id,
            refund_request_id=refund.id,
            created_at=NOW - timedelta(days=2),
        )
    )
    db.commit()


def add_coupon(db: Session) -> int:
    coupon = Coupon(
        code=f"SAVE{next(_SEQUENCE)}",
        coupon_type=TYPE_FIXED,
        amount_off_sen=500,
        percent_off=None,
        min_subtotal_sen=0,
        starts_at=datetime(2025, 1, 1),
        ends_at=datetime(2027, 12, 31),
        total_use_limit=None,
        per_member_use_limit=None,
        is_active=True,
    )
    db.add(coupon)
    db.commit()
    return coupon.id


def add_attempt(db: Session, created_at: datetime) -> None:
    """一条待核验（sent）的短信验证记录，写法同 tests/test_member_deletion.py。"""
    db.execute(
        insert(VerificationAttempt).values(
            phone=PHONE,
            purpose="login",
            status="sent",
            provider_request_id=f"VE{next(_REQUEST_IDS):032x}",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.commit()


def table_rows(db: Session, model) -> list[dict]:
    table = model.__table__
    return [dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]


def entries(db: Session, member_id: int, after_id: int = 0) -> list[tuple]:
    """该会员 ID 大于 after_id 的流水：（类别、批次、积分、订单、时间），按流水 ID 升序。"""
    stmt = (
        select(
            PointEntry.kind,
            PointEntry.lot_id,
            PointEntry.points,
            PointEntry.order_id,
            PointEntry.created_at,
        )
        .where(PointEntry.member_id == member_id, PointEntry.id > after_id)
        .order_by(PointEntry.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def last_entry_id(db: Session) -> int:
    return db.scalar(select(PointEntry.id).order_by(PointEntry.id.desc()).limit(1)) or 0


def points_snapshot(db: Session) -> dict[str, list[dict]]:
    return {model.__tablename__: table_rows(db, model) for model in (PointLot, PointEntry)}


# ---------------------------------------------------------------------------
# 作废
# ---------------------------------------------------------------------------


def test_deletion_forfeits_balance_expires_lapsed_lots_and_keeps_debt(db: Session) -> None:
    """第 3 条：“积分余额……作废，积分……流水保留去标识化审计记录”。SHOP-TASK-086 的作废
    规则：剩余大于 0 的批次各写一条等于剩余的 forfeit，已过期的记为 expire，待抵扣不作废。
    验收：注销后可用余额为 0；剩余为 0 的批次不写；注销前的流水与批次不改，仍记在该会员
    ID 下。"""
    member_id = add_member(db)
    lapsed, lapsed_order = earn(db, member_id, 30, LONG_AGO)
    first, first_order = earn(db, member_id, 100, NOW - timedelta(days=10))
    second, second_order = earn(db, member_id, 40, NOW - timedelta(days=1))
    spent, _ = earn(db, member_id, 20, NOW - timedelta(days=20))
    # 先到期先用：先用尽 spent（到期更早），再从 first 扣 15；已支付订单的抵扣不释放。
    redeem(db, member_id, 35, STATUS_PAID, NOW - timedelta(days=5))
    add_debt(db, member_id, 20)
    assert available_points(db, member_id, NOW) == 125
    history = points_snapshot(db)
    mark = last_entry_id(db)

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert entries(db, member_id, mark) == [
        (KIND_EXPIRE, lapsed, -30, lapsed_order, NOW),
        (KIND_FORFEIT, first, -85, first_order, NOW),
        (KIND_FORFEIT, second, -40, second_order, NOW),
    ]
    assert spent not in {row[1] for row in entries(db, member_id, mark)}
    assert available_points(db, member_id, NOW) == 0
    assert debt_points(db, member_id) == 20
    after = points_snapshot(db)
    assert after["point_lots"] == history["point_lots"]
    assert after["point_entries"][: len(history["point_entries"])] == history["point_entries"]


def test_deletion_leaves_other_members_points_unchanged(db: Session) -> None:
    """第 3 条：“积分余额与未用券作废”，作废的是注销的这位会员的余额。验收：其他会员的批次、
    流水与可用余额不变。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    earn(db, member_id, 60, NOW - timedelta(days=2))
    earn(db, other_id, 50, NOW - timedelta(days=2))
    earn(db, other_id, 10, LONG_AGO)
    redeem(db, other_id, 5, STATUS_AWAITING_PAYMENT, NOW - timedelta(minutes=5))
    other_before = entries(db, other_id)
    other_lots = [row for row in table_rows(db, PointLot) if row["member_id"] == other_id]

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert entries(db, other_id) == other_before
    assert [row for row in table_rows(db, PointLot) if row["member_id"] == other_id] == other_lots
    assert available_points(db, other_id, NOW) == 45
    assert available_points(db, member_id, NOW) == 0


def test_already_deleted_or_unknown_member_writes_no_point_entries(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：条件更新未命中（会员已注销或不存在）时不调用作废函数，不写积分流水。已注销的会员
    这里直接按注销后的样子改行（不经注销函数），使其批次仍有剩余，再注销也不作废。"""
    member_id = add_member(db)
    earn(db, member_id, 70, NOW - timedelta(days=2))
    db.execute(
        update(Member)
        .where(Member.id == member_id)
        .values(status=MEMBER_DELETED, phone=None, password_hash=None, deleted_at=NOW)
    )
    db.commit()
    calls: list[tuple] = []
    original = member_deletion.forfeit_points

    def spy(*args: Any) -> int:
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(member_deletion, "forfeit_points", spy)
    before = points_snapshot(db)

    assert delete_member(db, member_id, LATER) is False
    assert delete_member(db, member_id + 100, LATER) is False
    db.commit()

    assert calls == []
    assert points_snapshot(db) == before
    assert available_points(db, member_id, LATER) == 70


def test_deletion_calls_forfeit_with_member_and_now(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：条件更新命中之后、同一事务里调用作废函数，入参为该会员 ID 与当前时间；调用时会员
    行已为 deleted。"""
    member_id = add_member(db)
    calls: list[tuple] = []
    original = member_deletion.forfeit_points

    def spy(session: Session, called_member_id: int, now: datetime) -> int:
        status = session.scalar(select(Member.status).where(Member.id == called_member_id))
        calls.append((session is db, called_member_id, now, status))
        return original(session, called_member_id, now)

    monkeypatch.setattr(member_deletion, "forfeit_points", spy)

    assert delete_member(db, member_id, NOW) is True

    assert calls == [(True, member_id, NOW, MEMBER_DELETED)]


def test_same_phone_reregisters_with_zero_balance(db: Session) -> None:
    """第 3 条：“重新注册不能据此恢复旧订单的访问或积分”，“注销后同号重注册不得……补发积分”。
    验收：注销后同号经 SHOP-TASK-075 的 sms_login_or_register 重新注册为新会员，余额与待抵扣
    从 0 开始，没有记在新会员下的批次与流水；旧会员的批次仍记在旧会员 ID 下。"""
    db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=True))
    db.commit()
    old_id = add_member(db)
    earn(db, old_id, 80, NOW - timedelta(days=2))
    assert delete_member(db, old_id, NOW - timedelta(minutes=30)) is True
    db.commit()

    add_attempt(db, NOW - timedelta(minutes=5))
    result = sms_login_or_register(db, FakeSmsProvider(), PHONE, "login", CODE, NOW)
    db.commit()

    assert result.created_member is True
    assert result.member is not None
    new_id = result.member.id
    assert new_id != old_id
    assert available_points(db, new_id, NOW) == 0
    assert debt_points(db, new_id) == 0
    assert entries(db, new_id) == []
    assert {row["member_id"] for row in table_rows(db, PointLot)} == {old_id}


def test_pending_order_points_forfeited_when_released_after_deletion(db: Session) -> None:
    """积分第 3 条：“结账抵扣时预占，失败或取消时释放”；Kelvin 0.46：已预占的积分以预占时
    为准。验收：注销时有已预占积分的待支付订单，注销只作废未预占的余额；之后超时释放（经
    SHOP-TASK-086 的 release_points，订单的会员 ID 已清空）时释放的积分随即 forfeit，可用余额
    仍为 0。"""
    member_id = add_member(db)
    lot, lot_order = earn(db, member_id, 100, NOW - timedelta(days=10))
    pending = redeem(db, member_id, 30, STATUS_AWAITING_PAYMENT, NOW - timedelta(minutes=5))
    mark = last_entry_id(db)

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert entries(db, member_id, mark) == [(KIND_FORFEIT, lot, -70, lot_order, NOW)]
    assert db.scalar(select(Order.member_id).where(Order.id == pending)) is None
    mark = last_entry_id(db)

    assert release_points(db, pending, LATER) == 30
    db.commit()

    assert entries(db, member_id, mark) == [
        (KIND_RELEASE, lot, 30, pending, LATER),
        (KIND_FORFEIT, lot, -30, lot_order, LATER),
    ]
    assert available_points(db, member_id, LATER) == 0


def test_coupon_uses_kept_unchanged(db: Session) -> None:
    """第 3 条：“券使用流水保留去标识化审计记录”；Q19：券对全部会员相同、不发给个人，没有个人
    持有的未用券可作废。验收：用券记录（已核销与待支付订单预占的）在注销后保留，各列（含状态与
    会员 ID）不变。"""
    member_id = add_member(db)
    coupon_id = add_coupon(db)
    # 两张都在待支付时预占；第一张之后支付成功、核销，第二张注销时仍待支付。
    used_order = add_order(db, member_id, status=STATUS_AWAITING_PAYMENT)
    pending = add_order(db, member_id, status=STATUS_AWAITING_PAYMENT)
    for order_id in (used_order, pending):
        at = NOW - timedelta(days=1)
        assert reserve_coupon(db, coupon_id, order_id, member_id, 500, 2500, at).ok
    assert use_coupon(db, used_order, NOW - timedelta(hours=23))
    db.commit()
    before = table_rows(db, CouponUse)
    assert [(row["status"], row["member_id"]) for row in before] == [
        (USE_USED, member_id),
        (USE_RESERVED, member_id),
    ]

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert table_rows(db, CouponUse) == before


def test_deletion_only_flushes_point_entries(db: Session) -> None:
    """验收：只 flush、不提交——注销函数返回时作废流水已写入当前事务；回滚后积分批次与流水全部
    复原，可用余额恢复。"""
    member_id = add_member(db)
    earn(db, member_id, 90, NOW - timedelta(days=2))
    earn(db, member_id, 15, LONG_AGO)
    before = points_snapshot(db)
    mark = last_entry_id(db)

    assert delete_member(db, member_id, NOW) is True
    assert [row[0] for row in entries(db, member_id, mark)] == [KIND_EXPIRE, KIND_FORFEIT]
    db.rollback()

    assert points_snapshot(db) == before
    assert available_points(db, member_id, NOW) == 90


def test_forfeit_locks_only_the_deleted_member(db: Session) -> None:
    """验收：锁顺序——注销的条件更新已持有会员行锁，作废函数再锁同一行，不另取其他会员的锁。
    以 MySQL 方言编译注销执行到的 ORM 语句：会员表的 UPDATE 在任何加锁读之前；每条加锁读的
    参数都只有该会员 ID（会员行、批次与流水），没有锁其他会员的行。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    earn(db, member_id, 40, NOW - timedelta(days=2))
    earn(db, other_id, 40, NOW - timedelta(days=2))
    executed: list[tuple[str, dict]] = []

    @event.listens_for(db, "do_orm_execute")
    def _record(state: ORMExecuteState) -> None:
        compiled = state.statement.compile(dialect=mysql.dialect())
        executed.append((str(compiled), dict(compiled.params)))

    assert delete_member(db, member_id, NOW) is True
    event.remove(db, "do_orm_execute", _record)

    member_update = next(
        i for i, (sql, _) in enumerate(executed) if sql.startswith("UPDATE members")
    )
    locked = [(i, sql, params) for i, (sql, params) in enumerate(executed) if "FOR UPDATE" in sql]
    assert any("FROM members" in sql for _, sql, _ in locked)
    for i, _, params in locked:
        assert i > member_update
        assert set(params.values()) == {member_id}


def test_deletion_with_points_does_not_log(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """验收：注销不写日志——连同作废积分也不产生 app. 下的日志记录。"""
    member_id = add_member(db)
    earn(db, member_id, 25, NOW - timedelta(days=2))
    with caplog.at_level(logging.DEBUG):
        assert delete_member(db, member_id, NOW) is True
        assert delete_member(db, member_id, NOW) is False

    assert [r for r in caplog.records if r.name.startswith("app.")] == []

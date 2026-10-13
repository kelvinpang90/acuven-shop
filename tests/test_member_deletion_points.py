"""会员注销时作废积分余额（app/services/member_deletion.py 接入 forfeit_points）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条（下称「第 3 条」）：
  “积分余额与未用券作废，积分和券使用流水保留去标识化审计记录”，
  “重新注册不能据此恢复旧订单的访问或积分”，“注销后同号重注册不得再次认领或补发积分”。
- docs/UX.md 0.10 的 Q19（下称「Q19」）：“「我的优惠券」的可用券列出所有启用中且在
  有效期内的券，对全部会员相同”——券不发给个人，没有持有的未用券可作废。
- docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（4）（下称「Kelvin 0.46」）：
  “下单时已预占的积分以预占时为准”。
没有直接原句、只出自 SHOP-TASK-094 验收标准的实现约束，写明「验收：」。

数据库用 SQLite 内存库按模型建表，引擎设置与 tests/test_member_deletion.py 相同（由引擎
发 BEGIN，回滚与提交和 MySQL 上一样），每个连接打开外键检查并断言已打开。积分批次与流水
经 SHOP-TASK-086 的 earn_points、reserve_points 与 release_points 准备，用券记录经
SHOP-TASK-085 的 reserve_coupon 与 use_coupon；待抵扣照 tests/test_points.py 直接写一条
debt_add。SQLite 不支持行锁、不生成 FOR UPDATE：锁的范围只能以 SQL 执行事件钩子断言
读到的是哪个会员。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, func, insert, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_NOT_CLAIMABLE,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
)
from app.models.points import (
    KIND_DEBT_ADD,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_RELEASE,
)
from app.models.refund import REFUND_REQUESTED
from app.models.site import SITE_SETTING_ID
from app.services import member_deletion
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
# 这一天取得的批次在 NOW 时已过期。
LONG_AGO = NOW - timedelta(days=400)
PHONE = "+60123456789"
OTHER_PHONE = "+6581234567"
FINGERPRINT = "0" * 64
CODE = "482915"
SUBTOTAL = 2500
COUPON_OFF = 1000

_SEQUENCE = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_member_deletion.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 回滚与提交才和 MySQL 上一样。
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
            password_hash="scrypt$existing-password-hash",
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
    created_at: datetime = NOW - timedelta(days=3),
    status: str = STATUS_PAID,
) -> int:
    """一张会员订单；直接写库，不提交（由调用方连同积分一起提交）。"""
    n = next(_SEQUENCE)
    paid = status in PAID_STATUSES
    result = db.execute(
        insert(Order).values(
            order_number=f"{n:016d}",
            status=status,
            subtotal_sen=SUBTOTAL,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=SUBTOTAL + 800,
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
    return result.inserted_primary_key[0]


def earn(db: Session, member_id: int, points: int, at: datetime) -> tuple[int, int]:
    """经 earn_points 在 at 取得一个批次并提交，返回（批次 ID, 来源订单 ID）。"""
    order_id = add_order(db, member_id, created_at=at)
    lot = earn_points(db, member_id, order_id, points, at)
    assert lot is not None
    db.commit()
    return lot.id, order_id


def reserve(db: Session, member_id: int, points: int, at: datetime) -> int:
    """一张待支付订单，经 reserve_points 在 at 预占积分并提交，返回订单 ID。"""
    order_id = add_order(db, member_id, created_at=at, status=STATUS_AWAITING_PAYMENT)
    reserve_points(db, member_id, order_id, points, at)
    db.commit()
    return order_id


def add_debt(db: Session, member_id: int, points: int) -> None:
    """退款追回不足时记入的待抵扣（debt_add，批次为空，带退款申请）。"""
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
        amount_off_sen=COUPON_OFF,
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


def entries(db: Session, *conditions: Any) -> list[tuple[str, int | None, int, int]]:
    """（类别、批次、积分、订单），按流水 ID 升序。"""
    rows = db.execute(
        select(PointEntry.kind, PointEntry.lot_id, PointEntry.points, PointEntry.order_id)
        .where(*conditions)
        .order_by(PointEntry.id)
    ).all()
    return [tuple(row) for row in rows]


def entry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(PointEntry))


def table_rows(db: Session, model) -> list[dict]:
    table = model.__table__
    return [dict(row) for row in db.execute(select(table).order_by(table.c.id)).mappings()]


def member_lots(db: Session, member_id: int) -> list[dict]:
    return [row for row in table_rows(db, PointLot) if row["member_id"] == member_id]


def snapshot(db: Session) -> dict[str, list[dict]]:
    """会员、订单、积分与用券相关表的全部行，用来断言「回滚后全部复原」。"""
    models = (Member, Order, PointLot, PointEntry, CouponUse)
    return {model.__tablename__: table_rows(db, model) for model in models}


# ---------------------------------------------------------------------------
# 作废余额
# ---------------------------------------------------------------------------


def test_deletion_forfeits_balance_expires_expired_lots_and_keeps_debt(db: Session) -> None:
    """第 3 条：“积分余额……作废，积分……流水保留去标识化审计记录”。注销后可用余额
    为 0；有余额的未过期批次各有一条等于剩余的 forfeit（订单记批次的来源订单），
    已过期的记为 expire，剩余为 0 的批次不写；待抵扣不变，原有流水都保留。"""
    member_id = add_member(db)
    expired_lot, expired_order = earn(db, member_id, 50, LONG_AGO)
    used_up_lot, used_up_order = earn(db, member_id, 20, NOW - timedelta(days=20))
    first_lot, first_order = earn(db, member_id, 100, NOW - timedelta(days=10))
    second_lot, second_order = earn(db, member_id, 30, NOW - timedelta(days=1))
    # 先到期先用：used_up 整批 20、first 30。
    pending = reserve(db, member_id, 50, NOW - timedelta(hours=1))
    add_debt(db, member_id, 25)
    assert available_points(db, member_id, NOW) == 100
    before = entries(db)

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert available_points(db, member_id, NOW) == 0
    assert entries(db, PointEntry.kind.in_((KIND_EXPIRE, KIND_FORFEIT))) == [
        (KIND_EXPIRE, expired_lot, -50, expired_order),
        (KIND_FORFEIT, first_lot, -70, first_order),
        (KIND_FORFEIT, second_lot, -30, second_order),
    ]
    assert entries(db, PointEntry.lot_id == used_up_lot) == [
        (KIND_EARN, used_up_lot, 20, used_up_order),
        (KIND_REDEEM, used_up_lot, -20, pending),
    ]
    assert debt_points(db, member_id) == 25
    assert entries(db)[: len(before)] == before
    assert entry_count(db) == len(before) + 3


def test_deletion_leaves_other_members_points_unchanged(db: Session) -> None:
    """第 3 条：“积分余额……作废”只针对注销的会员。其他会员的批次、流水、可用余额
    与待抵扣都不变。"""
    member_id = add_member(db)
    other_id = add_member(db, phone=OTHER_PHONE)
    earn(db, member_id, 100, NOW - timedelta(days=10))
    earn(db, other_id, 80, NOW - timedelta(days=5))
    earn(db, other_id, 40, LONG_AGO)
    reserve(db, other_id, 30, NOW - timedelta(hours=1))
    add_debt(db, other_id, 15)
    other_entries = entries(db, PointEntry.member_id == other_id)
    other_lots = member_lots(db, other_id)

    assert delete_member(db, member_id, NOW) is True
    db.commit()

    assert entries(db, PointEntry.member_id == other_id) == other_entries
    assert member_lots(db, other_id) == other_lots
    assert available_points(db, other_id, NOW) == 50
    assert debt_points(db, other_id) == 15


def test_forfeit_called_with_member_and_now_only_when_update_hits(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：条件更新命中之后、同一会话里调用作废函数，入参为该会员 ID 与当前时间；
    条件更新未命中（已注销或不存在）时不调用，也不写积分流水。"""
    member_id = add_member(db)
    earn(db, member_id, 100, NOW - timedelta(days=10))
    calls: list[tuple[int, datetime]] = []
    real = member_deletion.forfeit_points

    def spy(session: Session, called_member_id: int, now: datetime) -> int:
        assert session is db
        calls.append((called_member_id, now))
        return real(session, called_member_id, now)

    monkeypatch.setattr(member_deletion, "forfeit_points", spy)

    assert delete_member(db, member_id, NOW) is True
    db.commit()
    assert calls == [(member_id, NOW)]
    count = entry_count(db)

    assert delete_member(db, member_id, NOW + timedelta(hours=1)) is False
    assert delete_member(db, member_id + 100, NOW) is False
    db.commit()

    assert calls == [(member_id, NOW)]
    assert entry_count(db) == count


def test_already_deleted_member_writes_no_point_entries(db: Session) -> None:
    """第 3 条“积分余额……作废”在注销时作废一次。验收：已注销的会员再注销返回 False、
    不写积分流水——注销后释放待支付订单写下的流水也不被再次处理。"""
    member_id = add_member(db)
    earn(db, member_id, 100, NOW - timedelta(days=10))
    pending = reserve(db, member_id, 60, NOW - timedelta(hours=1))
    assert delete_member(db, member_id, NOW) is True
    db.commit()
    release_points(db, pending, NOW + timedelta(minutes=20))
    db.commit()
    before = entries(db)

    assert delete_member(db, member_id, NOW + timedelta(hours=1)) is False
    db.commit()

    assert entries(db) == before


def test_same_phone_reregisters_with_zero_balance(db: Session) -> None:
    """第 3 条：“重新注册不能据此恢复旧订单的访问或积分”，“注销后同号重注册不得……
    补发积分”。注销后同号经 SHOP-TASK-075 的 sms_login_or_register 重新注册为新会员，
    新会员没有批次与流水，可用余额与待抵扣都从 0 开始；旧会员的流水仍在旧会员名下。"""
    db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=True))
    db.commit()
    old_id = add_member(db)
    earn(db, old_id, 100, NOW - timedelta(days=10))
    add_debt(db, old_id, 25)
    assert delete_member(db, old_id, NOW - timedelta(minutes=30)) is True
    db.commit()
    old_entries = entries(db, PointEntry.member_id == old_id)

    db.execute(
        insert(VerificationAttempt).values(
            phone=PHONE,
            purpose="login",
            status="sent",
            provider_request_id=f"VE{next(_SEQUENCE):032x}",
            created_at=NOW - timedelta(minutes=5),
            updated_at=NOW - timedelta(minutes=5),
        )
    )
    db.commit()
    result = sms_login_or_register(db, FakeSmsProvider(), PHONE, "login", CODE, NOW)
    db.commit()

    assert result.created_member is True
    assert result.member is not None and result.member.id != old_id
    new_id = result.member.id
    assert available_points(db, new_id, NOW) == 0
    assert debt_points(db, new_id) == 0
    assert entries(db, PointEntry.member_id == new_id) == []
    assert member_lots(db, new_id) == []
    assert entries(db, PointEntry.member_id == old_id) == old_entries


# ---------------------------------------------------------------------------
# 待支付订单
# ---------------------------------------------------------------------------


def test_pending_order_reserved_points_forfeit_when_released(db: Session) -> None:
    """第 3 条“积分余额……作废”；Kelvin 0.46“已预占的积分以预占时为准”。注销时只作废
    未预占的余额，待支付订单的预占不变；之后超时释放（经 SHOP-TASK-086 的
    release_points）时 release 后紧接着等量的 forfeit，可用余额仍为 0。"""
    member_id = add_member(db)
    lot_id, source_order = earn(db, member_id, 100, NOW - timedelta(days=10))
    pending = reserve(db, member_id, 60, NOW - timedelta(minutes=20))

    assert delete_member(db, member_id, NOW - timedelta(minutes=10)) is True
    db.commit()
    assert entries(db, PointEntry.order_id == pending) == [(KIND_REDEEM, lot_id, -60, pending)]
    assert db.scalar(select(Order.member_id).where(Order.id == pending)) is None

    assert release_points(db, pending, NOW) == 60
    db.commit()

    assert entries(db, PointEntry.lot_id == lot_id) == [
        (KIND_EARN, lot_id, 100, source_order),
        (KIND_REDEEM, lot_id, -60, pending),
        (KIND_FORFEIT, lot_id, -40, source_order),
        (KIND_RELEASE, lot_id, 60, pending),
        (KIND_FORFEIT, lot_id, -60, source_order),
    ]
    assert available_points(db, member_id, NOW) == 0


# ---------------------------------------------------------------------------
# 用券记录
# ---------------------------------------------------------------------------


def test_coupon_uses_kept_unchanged(db: Session) -> None:
    """第 3 条“券使用流水保留去标识化审计记录”；Q19“对全部会员相同”（券不发给个人，
    没有持有的未用券可作废）。注销后该会员的用券记录（reserved 与 used）都保留，
    各列与券本身都不变。"""
    member_id = add_member(db)
    coupon_id = add_coupon(db)
    paid = add_order(db, member_id)
    pending_at = NOW - timedelta(minutes=5)
    pending = add_order(db, member_id, created_at=pending_at, status=STATUS_AWAITING_PAYMENT)
    for order_id in (paid, pending):
        check = reserve_coupon(db, coupon_id, order_id, member_id, COUPON_OFF, SUBTOTAL, NOW)
        assert check.ok
    assert use_coupon(db, paid, NOW)
    db.commit()
    uses_before = table_rows(db, CouponUse)
    coupons_before = table_rows(db, Coupon)

    assert delete_member(db, member_id, NOW + timedelta(minutes=1)) is True
    db.commit()

    assert table_rows(db, CouponUse) == uses_before
    assert [row["status"] for row in uses_before] == [USE_USED, USE_RESERVED]
    assert table_rows(db, Coupon) == coupons_before


# ---------------------------------------------------------------------------
# 事务、锁的范围与日志
# ---------------------------------------------------------------------------


def test_deletion_only_flushes_and_rollback_restores_points(db: Session) -> None:
    """验收：只 flush、不提交——注销函数返回后作废已写入当前事务，回滚后会员、订单、
    积分批次、积分流水与用券记录全部复原。"""
    member_id = add_member(db)
    earn(db, member_id, 50, LONG_AGO)
    earn(db, member_id, 100, NOW - timedelta(days=10))
    reserve(db, member_id, 30, NOW - timedelta(hours=1))
    before = snapshot(db)

    assert delete_member(db, member_id, NOW) is True
    assert entry_count(db) == len(before["point_entries"]) + 2
    db.rollback()

    assert snapshot(db) == before
    assert available_points(db, member_id, NOW) == 70


def test_lock_scope_reads_only_the_deleted_member(engine: Engine, db: Session) -> None:
    """验收：注销的条件更新已持有该会员的行锁，作废函数再锁同一行与该会员的批次和
    流水，不另取其他会员的锁。SQLite 不生成 FOR UPDATE，这里只断言注销期间读取会员、
    积分批次与积分流水的语句，其整数参数都只是该会员的 ID。"""
    other_id = add_member(db, phone=OTHER_PHONE)
    member_id = add_member(db)
    earn(db, other_id, 80, NOW - timedelta(days=5))
    earn(db, member_id, 100, NOW - timedelta(days=10))
    captured: list[tuple[str, Any]] = []
    tables = ("FROM members", "FROM point_lots", "FROM point_entries")

    def capture(_conn, _cursor, statement, parameters, _context, _executemany) -> None:
        if statement.startswith("SELECT") and any(table in statement for table in tables):
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert delete_member(db, member_id, NOW) is True
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    db.commit()

    assert any("FROM point_lots" in statement for statement, _ in captured)
    assert any("FROM members" in statement for statement, _ in captured)
    for _, parameters in captured:
        ints = {value for value in parameters if type(value) is int}
        assert ints == {member_id}


def test_deletion_with_points_does_not_log(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """验收：不写日志。带积分的注销与再次注销都不产生 app. 下的日志记录。"""
    member_id = add_member(db)
    earn(db, member_id, 100, NOW - timedelta(days=10))
    with caplog.at_level(logging.DEBUG):
        assert delete_member(db, member_id, NOW) is True
        assert delete_member(db, member_id, NOW) is False

    assert [r for r in caplog.records if r.name.startswith("app.")] == []

"""Read-only member-points account queries, built from the real ledger rules."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order, PointEntry, PointLot, RefundRequest
from app.models.member import MEMBER_ACTIVE
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.models.points import KIND_DEBT_REPAY, KIND_EXPIRE
from app.models.refund import REFUND_REQUESTED
from app.services.member_account import get_points_history, get_points_overview
from app.services.points import earn_points, expire_points, release_points, reserve_points
from app.services.points_refund import apply_refund_points

NOW = datetime(2026, 10, 12, 4, 0)
CREATED = datetime(2026, 10, 1, 2, 0)
FINGERPRINT = "0f" * 32
_NUMBERS = itertools.count(1)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection: Any, _record: Any) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _member(db: Session) -> Member:
    number = next(_NUMBERS)
    return _add(db, Member(phone=f"+6010000{number:04d}", status=MEMBER_ACTIVE, created_at=CREATED))


def _order(db: Session, member: Member) -> Order:
    number = next(_NUMBERS)
    return _add(
        db,
        Order(
            order_number=f"{number:016d}",
            status=STATUS_AWAITING_PAYMENT,
            subtotal_sen=1000,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=0,
            total_sen=1000,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"key-{number}",
            request_fingerprint=FINGERPRINT,
            created_at=CREATED,
            payment_expires_at=CREATED + timedelta(minutes=15),
            paid_at=None,
            member_id=member.id,
            claim_status=CLAIM_NOT_CLAIMABLE,
        ),
    )


def _earn(db: Session, member: Member, points: int, at: datetime) -> Order:
    order = _order(db, member)
    assert earn_points(db, member.id, order.id, points, at) is not None
    return order


def _refund(db: Session, order: Order) -> RefundRequest:
    number = next(_NUMBERS)
    return _add(
        db,
        RefundRequest(
            order_id=order.id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_MEMBER,
            idempotency_key=f"refund-{number}",
            request_fingerprint=FINGERPRINT,
            amount_sen=100,
            created_at=NOW,
        ),
    )


def _points_state(db: Session) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    return (
        list(db.execute(select(PointEntry.id, PointEntry.points)).all()),
        list(db.execute(select(PointLot.id, PointLot.points)).all()),
    )


def test_overview_combines_next_malaysia_day_and_does_not_write(db: Session) -> None:
    """UX 0.10 P13 requires \"积分余额/到期/待抵扣与明细\" in the member centre."""
    member = _member(db)
    # These UTC instants cross midnight but are both 13 October in Malaysia.
    _earn(db, member, 30, datetime(2026, 10, 12, 16, 30))
    _earn(db, member, 20, datetime(2026, 10, 13, 15, 0))
    _earn(db, member, 40, datetime(2026, 10, 13, 16, 0))
    before = _points_state(db)

    overview = get_points_overview(db, member.id, NOW)

    assert overview.balance == 90
    assert overview.pending == 0
    assert overview.next_expiry is not None
    assert (overview.next_expiry.date.isoformat(), overview.next_expiry.points) == (
        "2027-10-13",
        50,
    )
    assert _points_state(db) == before
    assert not db.new
    assert not db.dirty


def test_overview_ignores_expired_and_empty_lots_and_returns_no_expiry(db: Session) -> None:
    """DESIGN 1.11 §3 says batches expire after 12 months and are used by expiry."""
    member = _member(db)
    expired = _earn(db, member, 10, NOW - timedelta(days=400))
    usable = _earn(db, member, 10, NOW)
    spend = _order(db, member)
    reserve_points(db, member.id, spend.id, 10, NOW)
    overview = get_points_overview(db, member.id, NOW)
    assert overview.balance == 0
    assert overview.next_expiry is None
    assert overview.pending == 0
    assert usable.id != expired.id


def test_overview_reports_refund_debt_without_making_balance_negative(db: Session) -> None:
    """DESIGN 1.11 §5 says an insufficient refund recovery becomes debt, never negative balance."""
    member = _member(db)
    _earn(db, member, 10, NOW - timedelta(days=5))
    source = _order(db, member)
    reserve_points(db, member.id, source.id, 5, NOW - timedelta(seconds=4))
    assert earn_points(db, member.id, source.id, 20, NOW - timedelta(days=2)) is not None
    reserve_points(db, member.id, _order(db, member).id, 20, NOW - timedelta(seconds=3))
    refund = _refund(db, source)
    apply_refund_points(db, source.id, refund.id, 5, 20, NOW)
    before = _points_state(db)

    overview = get_points_overview(db, member.id, NOW)

    assert overview.balance == 0
    assert overview.pending == 10
    assert overview.next_expiry is None
    assert _points_state(db) == before
    assert not db.new
    assert not db.dirty


def test_history_groups_display_types_orders_and_excludes_other_members(db: Session) -> None:
    """UX 0.10 lists "获得、抵扣、到期、退款返还、退款追回".

    DESIGN 1.11 §5 says later earnings repay debt first.
    """
    member = _member(db)
    other = _member(db)
    # Expire this lot before the refund creates debt: later earnings must repay
    # that debt first under DESIGN 1.11 §5 and would leave no lot to expire.
    old = _earn(db, member, 4, NOW - timedelta(days=400))
    expiry_time = NOW - timedelta(seconds=1)
    assert expire_points(db, expiry_time, 100) == 1
    assert (
        db.scalar(
            select(PointEntry.created_at).where(
                PointEntry.kind == KIND_EXPIRE,
                PointEntry.order_id == old.id,
            )
        )
        == expiry_time
    )
    _earn(db, member, 10, NOW - timedelta(days=5))
    source = _order(db, member)
    reserve_points(db, member.id, source.id, 5, NOW - timedelta(seconds=3))
    assert earn_points(db, member.id, source.id, 20, NOW - timedelta(days=2)) is not None
    cancelled = _order(db, member)
    reserve_points(db, member.id, cancelled.id, 8, NOW - timedelta(seconds=2))
    release_points(db, cancelled.id, NOW - timedelta(seconds=2))
    redeemed = _order(db, member)
    reserve_points(db, member.id, redeemed.id, 20, NOW - timedelta(seconds=4))
    refunded = _refund(db, source)
    # Recovery exceeds the source lot: the refund rule writes refund_reversal and debt_add.
    apply_refund_points(db, source.id, refunded.id, 5, 20, NOW)
    # This later earning repays debt first; its debt_repay entries must stay hidden.
    _earn(db, member, 7, NOW + timedelta(seconds=1))
    other_order = _earn(db, other, 99, NOW)
    assert db.scalars(select(PointEntry).where(PointEntry.kind == KIND_DEBT_REPAY)).all()
    before = _points_state(db)

    history = get_points_history(db, member.id)

    assert [(item.type, item.points) for item in history[:4]] == [
        ("earned", 7),
        ("reversed", -20),
        ("returned", 5),
        ("expired", -4),
    ]
    assert {item.type for item in history} == {
        "earned",
        "redeemed",
        "expired",
        "returned",
        "reversed",
    }
    assert all(item.order_number != cancelled.order_number for item in history)
    assert all(item.order_number != other_order.order_number for item in history)
    overview = get_points_overview(db, member.id, NOW + timedelta(seconds=1))
    assert (overview.balance, overview.pending) == (2, 0)
    assert _points_state(db) == before
    assert not db.new
    assert not db.dirty
    assert old.id != source.id


def test_history_limits_to_100_newest_groups(db: Session) -> None:
    """Kelvin's SHOP-TASK-095 decision says the member-centre history has \"最多 100 行\"."""
    member = _member(db)
    for offset in range(101):
        _earn(db, member, 1, NOW + timedelta(seconds=offset))

    history = get_points_history(db, member.id)

    assert len(history) == 100
    assert history[0].created_at == NOW + timedelta(seconds=100)
    assert history[-1].created_at == NOW + timedelta(seconds=1)

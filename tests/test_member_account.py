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
from app.models import Member, Order, PointEntry, RefundRequest
from app.models.member import MEMBER_ACTIVE
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
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


def _ledger_count(db: Session) -> int:
    return len(db.scalars(select(PointEntry.id)).all())


def test_overview_combines_next_malaysia_day_and_does_not_write(db: Session) -> None:
    """UX P13 says to show balance, refund debt, and the nearest expiry together."""
    member = _member(db)
    _earn(db, member, 30, NOW)
    _earn(db, member, 20, NOW + timedelta(seconds=1))
    before = _ledger_count(db)

    overview = get_points_overview(db, member.id, NOW)

    assert overview.balance == 50
    assert overview.pending == 0
    assert overview.next_expiry is not None
    assert (overview.next_expiry.date.isoformat(), overview.next_expiry.points) == (
        "2027-10-12",
        50,
    )
    assert _ledger_count(db) == before


def test_overview_ignores_expired_and_empty_lots_and_returns_no_expiry(db: Session) -> None:
    """DESIGN 1.11 says only remaining, unexpired batch points are available."""
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


def test_history_groups_display_types_orders_and_excludes_other_members(db: Session) -> None:
    """UX-COPY's five history types exclude cancelled checkout and debt repayment."""
    member = _member(db)
    other = _member(db)
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
    old = _earn(db, member, 4, NOW - timedelta(days=400))
    expire_points(db, NOW - timedelta(seconds=1), 100)
    other_order = _earn(db, other, 99, NOW)
    before = _ledger_count(db)

    history = get_points_history(db, member.id)

    assert [(item.type, item.points) for item in history[:3]] == [
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
    assert all(item.order_number != other_order.order_number for item in history)
    assert _ledger_count(db) == before
    assert old.id != source.id


def test_history_limits_to_100_newest_groups(db: Session) -> None:
    """Acceptance: the member-centre history is bounded at 100 rows."""
    member = _member(db)
    for offset in range(101):
        _earn(db, member, 1, NOW + timedelta(seconds=offset))

    history = get_points_history(db, member.id)

    assert len(history) == 100
    assert history[0].created_at == NOW + timedelta(seconds=100)
    assert history[-1].created_at == NOW + timedelta(seconds=1)

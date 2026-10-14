"""Member points overview and activity queries."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.member_account import point_activity, points_overview
from app.services.points import (
    earn_points,
    expire_points,
    release_points,
    reserve_points,
)
from app.services.points_refund import apply_refund_points

T = datetime(2026, 10, 14, 4, 0)
_NUMBERS = itertools.count(1)


@pytest.fixture
def db() -> Iterator[Session]:
    """The model's foreign keys remain active in the in-memory database."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _member(db: Session) -> Member:
    number = next(_NUMBERS)
    member = Member(phone=f"+60120000{number:03d}", status=MEMBER_ACTIVE, created_at=T)
    db.add(member)
    db.flush()
    return member


def _order(db: Session, member: Member, label: str) -> Order:
    number = next(_NUMBERS)
    order = Order(
        order_number=f"{number:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=100,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=100,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"member-account-{label}-{number}",
        request_fingerprint="a1" * 32,
        created_at=T,
        payment_expires_at=T + timedelta(minutes=15),
        paid_at=None,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add(order)
    db.flush()
    return order


def test_activity_uses_the_registered_refund_and_repayment_scenario(
    db: Session,
) -> None:
    """DESIGN 1.11 says refunds return then recover points and later earns repay debt."""
    member, other = _member(db), _member(db)
    old = _order(db, member, "O")
    earned = _order(db, member, "E")
    refunded = _order(db, member, "S")
    cancelled = _order(db, member, "C")
    redeemed = _order(db, member, "R")
    later = _order(db, member, "L")
    earn_points(db, member.id, old.id, 4, T - timedelta(days=400))
    earn_points(db, member.id, earned.id, 10, T - timedelta(days=5))
    reserve_points(db, member.id, refunded.id, 5, T - timedelta(days=3))
    earn_points(db, member.id, refunded.id, 20, T - timedelta(days=2))
    reserve_points(db, member.id, cancelled.id, 8, T - timedelta(seconds=3))
    release_points(db, cancelled.id, T - timedelta(seconds=3))
    reserve_points(db, member.id, redeemed.id, 20, T - timedelta(seconds=2))
    expire_points(db, T - timedelta(seconds=1), 100)
    overview = points_overview(db, member.id, T)
    assert (overview.available_points, overview.debt_points) == (5, 0)
    refund = _refund(db, refunded)
    apply_refund_points(db, refunded.id, refund, 5, 20, T)
    overview = points_overview(db, member.id, T)
    assert (overview.available_points, overview.debt_points) == (0, 10)
    earn_points(db, member.id, later.id, 12, T + timedelta(seconds=1))
    other_order = _order(db, other, "other")
    earn_points(db, other.id, other_order.id, 99, T + timedelta(seconds=2))

    assert [
        (row.display_type, row.points, row.order_number) for row in point_activity(db, member.id)
    ] == [
        ("earned", 12, later.order_number),
        ("reversed", -20, refunded.order_number),
        ("returned", 5, refunded.order_number),
        ("expired", -4, old.order_number),
        ("redeemed", -20, redeemed.order_number),
        ("earned", 20, refunded.order_number),
        ("redeemed", -5, refunded.order_number),
        ("earned", 10, earned.order_number),
        ("earned", 4, old.order_number),
    ]
    overview = points_overview(db, member.id, T + timedelta(seconds=1))
    assert (overview.available_points, overview.debt_points) == (2, 0)


def _refund(db: Session, order: Order) -> int:
    """The refund service only needs a valid requested refund row."""
    from app.models import RefundRequest
    from app.models.order import ACTOR_MEMBER
    from app.models.refund import REFUND_REQUESTED

    refund = RefundRequest(
        order_id=order.id,
        status=REFUND_REQUESTED,
        actor_type=ACTOR_MEMBER,
        idempotency_key=f"refund-{order.id}",
        request_fingerprint="b2" * 32,
        amount_sen=100,
        created_at=T,
    )
    db.add(refund)
    db.flush()
    return refund.id


def test_overview_groups_the_nearest_malaysia_expiry_and_omits_empty_lots(
    db: Session,
) -> None:
    """DESIGN 1.11 gives lots a 12-month Malaysia-date expiry; UX P13 shows nearest expiry."""
    member, empty = _member(db), _member(db)
    used, expired, first, second, newest = [_order(db, member, name) for name in "UXFSN"]
    earn_points(db, member.id, used.id, 4, T - timedelta(days=10))
    reserve_points(db, member.id, _order(db, member, "spend").id, 4, T)
    earn_points(db, member.id, expired.id, 9, T - timedelta(days=400))
    earn_points(db, member.id, first.id, 7, datetime(2025, 10, 14, 0, 0))
    earn_points(db, member.id, second.id, 3, datetime(2025, 10, 14, 12, 0))
    earn_points(db, member.id, newest.id, 6, T)

    overview = points_overview(db, member.id, T)
    assert (
        overview.nearest_expiry_date.isoformat(),
        overview.nearest_expiry_points,
    ) == ("2026-10-14", 10)
    assert points_overview(db, empty.id, T).nearest_expiry_date is None
    assert points_overview(db, empty.id, T).nearest_expiry_points is None


def test_queries_do_not_flush_or_write_and_activity_is_limited(db: Session) -> None:
    """Task acceptance caps activity at 100 rows and requires both queries to be read-only."""
    member = _member(db)
    for index in range(101):
        order = _order(db, member, f"limit-{index}")
        earn_points(db, member.id, order.id, 1, T + timedelta(seconds=index))
    before = db.execute(text("SELECT COUNT(*) FROM point_entries")).scalar_one()
    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", record_statement)
    try:
        assert len(point_activity(db, member.id)) == 100
        points_overview(db, member.id, T)
    finally:
        event.remove(db.bind, "before_cursor_execute", record_statement)

    assert statements
    assert all(
        not statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in statements
    )
    assert db.execute(text("SELECT COUNT(*) FROM point_entries")).scalar_one() == before

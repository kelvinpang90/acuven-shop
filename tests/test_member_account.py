"""会员积分查询：每项断言说明其守住的设计、UX 或本任务验收要求。"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order, RefundRequest
from app.models.member import MEMBER_ACTIVE
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.models.refund import REFUND_REQUESTED
from app.services.member_account import points_history, points_overview
from app.services.points import earn_points, expire_points, release_points, reserve_points
from app.services.points_refund import apply_refund_points

T = datetime(2026, 10, 14, 4, 0)
_numbers = itertools.count(1)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _add[Value](db: Session, value: Value) -> Value:
    db.add(value)
    db.flush()
    return value


def _member(db: Session) -> Member:
    n = next(_numbers)
    return _add(db, Member(phone=f"+6010000{n:04d}", status=MEMBER_ACTIVE, created_at=T))


def _new_order(member: Member) -> Order:
    n = next(_numbers)
    return Order(
        order_number=f"{n:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=1000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=1000,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"key-{n}",
        request_fingerprint="ab" * 32,
        created_at=T,
        payment_expires_at=T + timedelta(minutes=15),
        paid_at=None,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )


def _order(db: Session, member: Member) -> Order:
    return _add(db, _new_order(member))


def _earn(
    db: Session,
    member: Member,
    points: int,
    when: datetime,
    order: Order | None = None,
) -> Order:
    order = order or _order(db, member)
    assert earn_points(db, member.id, order.id, points, when) is not None
    return order


def _refund(db: Session, order: Order, back: int, reversed_: int, when: datetime) -> None:
    n = next(_numbers)
    request = _add(
        db,
        RefundRequest(
            order_id=order.id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_MEMBER,
            idempotency_key=f"refund-{n}",
            request_fingerprint="cd" * 32,
            amount_sen=100,
            created_at=when,
        ),
    )
    apply_refund_points(db, order.id, request.id, back, reversed_, when)


def test_history_matches_the_registered_refund_and_debt_scenario(db: Session) -> None:
    """守住 DESIGN 1.11 第 5 条。

    退款先返还再追回，不足记待抵扣，之后获得先偿还。
    """
    member, other = _member(db), _member(db)
    old = _earn(db, member, 4, T - timedelta(days=400))
    earned = _earn(db, member, 10, T - timedelta(days=5))
    source = _order(db, member)
    reserve_points(db, member.id, source.id, 5, T - timedelta(days=3))
    _earn(db, member, 20, T - timedelta(days=2), source)
    cancelled = _order(db, member)
    reserve_points(db, member.id, cancelled.id, 8, T - timedelta(seconds=3))
    release_points(db, cancelled.id, T - timedelta(seconds=3))
    reversed_order = _order(db, member)
    reserve_points(db, member.id, reversed_order.id, 20, T - timedelta(seconds=2))
    assert expire_points(db, T - timedelta(seconds=1), 10) == 1
    assert points_overview(db, member.id, T).balance == 5
    assert points_overview(db, member.id, T).debt == 0
    _refund(db, source, 5, 20, T)
    overview_at_refund = points_overview(db, member.id, T)
    assert (overview_at_refund.balance, overview_at_refund.debt) == (0, 10)
    later = _earn(db, member, 12, T + timedelta(seconds=1))
    _earn(db, other, 99, T + timedelta(seconds=2))

    history = points_history(db, member.id)
    assert [(row.display_type, row.points, row.order_number) for row in history] == [
        ("earned", 12, later.order_number),
        ("reversed", -20, source.order_number),
        ("returned", 5, source.order_number),
        ("expired", -4, old.order_number),
        ("redeemed", -20, reversed_order.order_number),
        ("earned", 20, source.order_number),
        ("redeemed", -5, source.order_number),
        ("earned", 10, earned.order_number),
        ("earned", 4, old.order_number),
    ]
    overview_after_repay = points_overview(db, member.id, T + timedelta(seconds=1))
    assert (overview_after_repay.balance, overview_after_repay.debt) == (2, 0)


def test_overview_groups_next_expiry_by_malaysia_date_and_ignores_ineligible_lots(
    db: Session,
) -> None:
    """守住 DESIGN 1.11 第 3 条及 UX P13。

    只显示未过期且剩余大于零的最近到期积分。
    """
    member = _member(db)
    _earn(db, member, 4, T - timedelta(days=10))
    reserve_points(db, member.id, _order(db, member).id, 4, T)
    _earn(db, member, 9, T - timedelta(days=400))
    _earn(db, member, 7, datetime(2025, 10, 14, 0, 0))
    _earn(db, member, 3, datetime(2025, 10, 14, 12, 0))
    _earn(db, member, 6, T)
    overview = points_overview(db, member.id, T)
    assert (overview.next_expiry_date, overview.next_expiry_points) == (
        date(2026, 10, 15),
        10,
    )
    empty = _member(db)
    assert points_overview(db, empty.id, T).next_expiry_date is None
    assert points_overview(db, empty.id, T).next_expiry_points is None


def test_history_is_limited_to_100_rows_and_own_entries(db: Session) -> None:
    """守住验收登记的 100 行上限（设计未规定行数）与 DESIGN 权限与资料保护第 6 条。"""
    member, other = _member(db), _member(db)
    orders = [_earn(db, member, 1, T + timedelta(seconds=n)) for n in range(101)]
    other_order = _earn(db, other, 99, T + timedelta(days=1))
    history = points_history(db, member.id)
    assert len(history) == 100
    assert history[0].order_number == orders[-1].order_number
    assert all(row.order_number != other_order.order_number for row in history)


def test_queries_do_not_write_or_flush(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收：两个会员查询函数只读，不写库、不 flush。"""
    member = _member(db)
    _earn(db, member, 1, T)
    pending = _new_order(member)
    db.add(pending)
    before = (tuple(db.new), tuple(db.dirty), tuple(db.deleted))
    statements: list[str] = []

    def _record_statement(_connection: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", _record_statement)
    monkeypatch.setattr(db, "flush", lambda: pytest.fail("query must not flush"))
    try:
        points_overview(db, member.id, T)
        points_history(db, member.id)
    finally:
        event.remove(bind, "before_cursor_execute", _record_statement)

    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted)) == before

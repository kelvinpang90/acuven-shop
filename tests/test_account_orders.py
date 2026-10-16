"""Member-scoped order queries and member order actions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Member,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    ReceiptConfirmation,
    RefundRequest,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_COMPLETED,
    STATUS_SHIPPED,
)
from app.services.account_orders import find_account_order_id, list_account_orders
from app.services.order_lookup import confirm_receipt, lookup_orders
from app.services.order_rules import generate_order_number
from app.services.refunds import RefundLineRequest, submit_refund

NOW = datetime(2026, 10, 16, 4, 0)


@pytest.fixture
def db() -> Session:
    """The query tests use model tables with SQLite foreign keys enabled."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _member(db: Session, suffix: int) -> Member:
    member = Member(phone=f"+6012000{suffix:04d}", status=MEMBER_ACTIVE, created_at=NOW)
    db.add(member)
    db.flush()
    return member


def _order(
    db: Session,
    label: str,
    created_at: datetime,
    *,
    member_id: int | None = None,
    claim_status: str = CLAIM_OPEN,
    status: str = STATUS_SHIPPED,
    coupon: int = 0,
    points: int = 0,
) -> Order:
    order = Order(
        order_number=generate_order_number(),
        status=status,
        subtotal_sen=1000,
        coupon_discount_sen=coupon,
        points_redeemed=points,
        shipping_fee_sen=100,
        total_sen=1100 - coupon - points,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"account-orders-{label}",
        request_fingerprint="a" * 64,
        created_at=created_at,
        payment_expires_at=created_at + timedelta(minutes=15),
        paid_at=created_at + timedelta(minutes=1),
        member_id=member_id,
        claim_status=claim_status,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=None,
        sku=f"SKU-{label}",
        product_name_en="Item",
        product_name_zh="商品",
        product_name_ms="Item",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=1000,
        quantity=1,
        line_subtotal_sen=1000,
    )
    db.add(item)
    db.flush()
    db.add(
        OrderItemUnit(
            order_item_id=item.id,
            unit_index=0,
            original_price_sen=1000,
            coupon_discount_sen=coupon,
            points_discount=points,
            cash_paid_sen=1000 - coupon - points,
            points_earned=0,
        )
    )
    db.flush()
    return order


def test_list_only_owns_orders_sorts_and_paginates(db: Session) -> None:
    """DESIGN 1.11 权限第 6 条只准会员访问本人下单或认领的订单；验收规定每页 20 张和排序。"""
    member, other = _member(db, 1), _member(db, 2)
    first = _order(db, "first", NOW, member_id=member.id, claim_status=CLAIM_NOT_CLAIMABLE)
    second = _order(db, "second", NOW, member_id=member.id, claim_status=CLAIM_CLAIMED)
    _order(
        db,
        "other",
        NOW + timedelta(seconds=1),
        member_id=other.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    _order(db, "guest", NOW + timedelta(seconds=2))
    _order(db, "deleted", NOW + timedelta(seconds=3), claim_status=CLAIM_NOT_CLAIMABLE)
    remaining = [
        _order(
            db,
            f"own-{index}",
            NOW - timedelta(seconds=index + 1),
            member_id=member.id,
            claim_status=CLAIM_NOT_CLAIMABLE,
        )
        for index in range(19)
    ]

    page_one = list_account_orders(db, member.id, 1)
    page_two = list_account_orders(db, member.id, 2)

    assert page_one.total == 21
    expected_first_page = [second.order_number, first.order_number]
    expected_first_page += [order.order_number for order in remaining][:-1]
    assert [row.order_number for row in page_one.orders] == expected_first_page
    assert [row.order_number for row in page_two.orders] == [remaining[-1].order_number]
    assert list_account_orders(db, member.id, 3).orders == []


def test_find_only_returns_a_valid_number_owned_by_member(db: Session) -> None:
    """DESIGN 1.11 权限第 6 条不让会员用订单号定位他人或游客订单。"""
    member, other = _member(db, 3), _member(db, 4)
    own = _order(db, "own", NOW, member_id=member.id, claim_status=CLAIM_NOT_CLAIMABLE)
    other_order = _order(
        db,
        "other-find",
        NOW,
        member_id=other.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    guest = _order(db, "guest-find", NOW)

    assert find_account_order_id(db, member.id, own.order_number.lower()) == own.id
    assert find_account_order_id(db, member.id, other_order.order_number) is None
    assert find_account_order_id(db, member.id, guest.order_number) is None
    assert find_account_order_id(db, member.id, "not an order") is None


def test_member_actions_and_lookup_snapshots(db: Session) -> None:
    """DESIGN 1.11 订单状态让会员确认和申请退款；UX P09 只给会员下单快照显示券与积分。"""
    member = _member(db, 5)
    member_order = _order(
        db,
        "member-action",
        NOW,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
        coupon=200,
        points=100,
    )
    guest = _order(db, "guest-view", NOW, coupon=50, points=40)
    claimed = _order(
        db,
        "claimed-view",
        NOW,
        member_id=member.id,
        claim_status=CLAIM_CLAIMED,
        coupon=60,
        points=30,
    )
    db.commit()

    outcome, created = confirm_receipt(
        db, member_order.id, "member-receipt", NOW, actor=ACTOR_MEMBER
    )
    refund, refund_created = submit_refund(
        db,
        member_order.id,
        [RefundLineRequest(0, 1)],
        "member-refund",
        NOW,
        actor=ACTOR_MEMBER,
    )

    assert (outcome.status, created, refund.status, refund_created) == (
        STATUS_COMPLETED,
        True,
        "requested",
        True,
    )
    assert db.scalars(select(ReceiptConfirmation.actor_type)).one() == ACTOR_MEMBER
    event_actor = db.scalars(
        select(OrderEvent.actor_type).where(OrderEvent.order_id == member_order.id)
    ).one()
    assert event_actor == ACTOR_MEMBER
    assert db.scalars(select(RefundRequest.actor_type)).one() == ACTOR_MEMBER
    views = lookup_orders(db, [member_order.id, guest.id, claimed.id], "en", NOW)
    snapshots = [
        (view.placed_by_member, view.coupon_discount_sen, view.points_redeemed) for view in views
    ]
    assert snapshots == [
        (True, 200, 100),
        (False, 50, 40),
        (False, 60, 30),
    ]
    with pytest.raises(ValueError):
        confirm_receipt(db, guest.id, "bad-receipt", NOW, actor="admin")
    with pytest.raises(ValueError):
        submit_refund(db, guest.id, [RefundLineRequest(0, 1)], "bad-refund", NOW, actor="admin")

"""Member order APIs added by SHOP-TASK-104.

Each test names the DESIGN 1.11 or UX P09/P13 statement it protects.  The
database is deliberately an in-memory SQLite database shared by TestClient's
thread, matching the member-session API tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    AdminAccount,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    ReceiptConfirmation,
    RefundLine,
    RefundLineUnit,
    RefundRequest,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_LOOKUP
from app.models.refund import REFUND_APPROVED
from app.services.member_auth import csrf_token_for_cookie, issue_member_session
from app.services.order_access import (
    COOKIE_NAME as ORDER_ACCESS_COOKIE,
)
from app.services.order_access import (
    issue_order_access,
)
from app.services.order_rules import generate_order_number

LIST_URL = "/api/member/orders"
VIEW_URL = "/api/member/orders/view"
CONFIRM_URL = "/api/member/orders/confirm-receipt"
COOKIE = "__Host-shop_member_session"
NO_STORE = "no-store"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    application = create_app(Settings(_env_file=None))

    def session() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_session] = session
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _member(db: Session, suffix: int) -> Member:
    member = Member(phone=f"+6012999{suffix:04d}", status=MEMBER_ACTIVE, created_at=_now())
    db.add(member)
    db.flush()
    return member


def _headers(db: Session, member: Member) -> dict[str, str]:
    issued = issue_member_session(db, member, _now())
    db.commit()
    assert (csrf := csrf_token_for_cookie(issued.token)) is not None
    return {"Cookie": f"{COOKIE}={issued.token}", "X-CSRF-Token": csrf}


def _order(
    db: Session,
    member: Member | None,
    *,
    status: str = STATUS_SHIPPED,
    claimed: bool = False,
    created_at: datetime | None = None,
) -> Order:
    created_at = created_at or _now()
    order = Order(
        order_number=generate_order_number(),
        status=status,
        subtotal_sen=1000,
        coupon_discount_sen=100,
        points_redeemed=50,
        shipping_fee_sen=100,
        total_sen=950,
        points_earned=25,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"order-{generate_order_number()}",
        request_fingerprint="a" * 64,
        created_at=created_at,
        payment_expires_at=created_at + timedelta(minutes=15),
        paid_at=created_at if status != STATUS_AWAITING_PAYMENT else None,
        member_id=None if member is None else member.id,
        claim_status=(
            CLAIM_CLAIMED if claimed else (CLAIM_NOT_CLAIMABLE if member else CLAIM_OPEN)
        ),
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=None,
        sku="ACCOUNT-ORDER",
        product_name_en="Account order",
        product_name_zh="订单",
        product_name_ms="Account order",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=1000,
        quantity=1,
        line_subtotal_sen=1000,
    )
    recipient = OrderRecipient(
        order_id=order.id,
        name="Member recipient",
        phone="+60123456789",
        country_code="MY",
        region="MY-10",
        address="Example address",
        postal_code="50000",
    )
    db.add_all([item, recipient])
    db.flush()
    db.add(
        OrderItemUnit(
            order_item_id=item.id,
            unit_index=0,
            original_price_sen=1000,
            coupon_discount_sen=100,
            points_discount=50,
            cash_paid_sen=850,
            points_earned=25,
        )
    )
    db.commit()
    return order


def _lookup_headers(db: Session, order: Order) -> dict[str, str]:
    """Issue a real lookup-only order cookie, never a member session."""
    issued = issue_order_access(db, None, order.id, SCOPE_LOOKUP, _now())
    db.commit()
    assert issued.new_token is not None
    return {"Cookie": f"{ORDER_ACCESS_COOKIE}={issued.new_token}"}


def _fully_refund(db: Session, order: Order) -> None:
    """Create one approved request occupying this fixture order's only unit."""
    admin = AdminAccount(
        username="reviewer@example.test",
        password_hash="not-a-real-hash",
        created_at=_now(),
        password_updated_at=_now(),
    )
    db.add(admin)
    db.flush()
    request = RefundRequest(
        order_id=order.id,
        status=REFUND_APPROVED,
        actor_type=ACTOR_MEMBER,
        idempotency_key="refund-request-key",
        request_fingerprint="a" * 64,
        amount_sen=850,
        created_at=_now(),
        reviewed_at=_now(),
        reviewer_admin_id=admin.id,
        review_reason=None,
        review_idempotency_key="refund-review-key",
        review_fingerprint="b" * 64,
    )
    db.add(request)
    db.flush()
    item = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).one()
    unit = db.scalars(select(OrderItemUnit).where(OrderItemUnit.order_item_id == item.id)).one()
    line = RefundLine(
        refund_request_id=request.id,
        order_item_id=item.id,
        quantity=1,
        amount_sen=850,
    )
    db.add(line)
    db.flush()
    db.add(
        RefundLineUnit(
            refund_line_id=line.id,
            order_item_unit_id=unit.id,
            cash_paid_sen=850,
            occupied=1,
        )
    )
    db.commit()


def _assert_no_store(response: object) -> None:
    assert response.headers["cache-control"] == NO_STORE


def test_member_order_list_is_private_paginated_and_uses_utc_fields(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 clause 6 and UX P13: a member sees only their own orders."""
    member, other = _member(db, 1), _member(db, 2)
    headers = _headers(db, member)
    own = [_order(db, member, created_at=_now() - timedelta(seconds=index)) for index in range(21)]
    _order(db, other)
    _order(db, None)
    first = client.get(LIST_URL, headers=headers)
    second = client.get(LIST_URL, headers=headers, params={"page": "2"})
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == 21 and first.json()["page_size"] == 20
    assert len(first.json()["orders"]) == 20
    row = second.json()["orders"][0]
    assert row["order_number"] == own[-1].order_number
    assert row["status"] == STATUS_SHIPPED and row["total_sen"] == 950
    assert second.json()["orders"][0]["created_at"].endswith("Z")
    _assert_no_store(first)
    _assert_no_store(second)


def test_view_hides_other_and_guest_orders_but_marks_member_snapshot(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 clause 6: other and guest orders look missing."""
    member, other = _member(db, 3), _member(db, 4)
    headers = _headers(db, member)
    own = _order(db, member)
    claimed_guest = _order(db, member, claimed=True)
    outsider, guest = _order(db, other), _order(db, None)
    view = client.post(VIEW_URL, headers=headers, json={"order_number": own.order_number})
    claimed = client.post(
        VIEW_URL, headers=headers, json={"order_number": claimed_guest.order_number}
    )
    assert view.status_code == claimed.status_code == 200
    assert view.json()["placed_by_member"] is True
    assert view.json()["coupon_discount_sen"] == 100
    assert view.json()["points_redeemed"] == 50
    assert claimed.json()["placed_by_member"] is False
    missing = client.post(VIEW_URL, headers=headers, json={"order_number": generate_order_number()})
    assert missing.status_code == 404
    for order in (outsider, guest):
        response = client.post(VIEW_URL, headers=headers, json={"order_number": order.order_number})
        assert response.status_code == 404
        assert response.content == missing.content
        _assert_no_store(response)
    _assert_no_store(missing)


def test_view_expires_overdue_awaiting_payment_order(db: Session, client: TestClient) -> None:
    """DESIGN 1.11 status: viewing an overdue pending order cancels it."""
    member = _member(db, 5)
    headers = _headers(db, member)
    order = _order(
        db,
        member,
        status=STATUS_AWAITING_PAYMENT,
        created_at=_now() - timedelta(hours=1),
    )
    response = client.post(VIEW_URL, headers=headers, json={"order_number": order.order_number})
    assert response.status_code == 200 and response.json()["status"] == STATUS_CANCELLED
    _assert_no_store(response)


def test_confirm_requires_member_csrf_and_records_member_event(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 clause 6: CSRF-protected member confirmation records its actor."""
    member = _member(db, 6)
    headers = _headers(db, member)
    order = _order(db, member)
    denied = client.post(
        CONFIRM_URL,
        headers={"Cookie": headers["Cookie"], "Idempotency-Key": "a" * 16},
        json={"order_number": order.order_number},
    )
    response = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": "b" * 16},
        json={"order_number": order.order_number},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "csrf_failed"}
    assert response.status_code == 200
    assert response.json() == {"status": STATUS_COMPLETED}
    assert db.scalars(select(ReceiptConfirmation.actor_type)).one() == ACTOR_MEMBER
    assert (
        db.scalars(select(OrderEvent.actor_type).where(OrderEvent.order_id == order.id)).one()
        == ACTOR_MEMBER
    )
    _assert_no_store(denied)
    _assert_no_store(response)


def test_confirm_is_private_idempotent_and_matches_lookup_conflicts(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 clause 6: member confirmation preserves lookup-mode outcomes."""
    member, other = _member(db, 7), _member(db, 8)
    headers = _headers(db, member)
    own, outsider, pending, frozen = (
        _order(db, member),
        _order(db, other),
        _order(db, member, status=STATUS_AWAITING_PAYMENT),
        _order(db, member),
    )
    _fully_refund(db, frozen)
    key = "c" * 16
    first = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": key},
        json={"order_number": own.order_number},
    )
    replay = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": key},
        json={"order_number": own.order_number},
    )
    hidden = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": "d" * 16},
        json={"order_number": outsider.order_number},
    )
    conflict = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": key},
        json={"order_number": pending.order_number},
    )
    not_confirmable = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": "e" * 16},
        json={"order_number": pending.order_number},
    )
    frozen_response = client.post(
        CONFIRM_URL,
        headers=headers | {"Idempotency-Key": "f" * 16},
        json={"order_number": frozen.order_number},
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "order_not_found"}
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency_conflict"}
    assert not_confirmable.status_code == 409
    assert not_confirmable.json() == {
        "detail": "order_not_confirmable",
        "status": STATUS_AWAITING_PAYMENT,
    }
    assert frozen_response.status_code == 409
    assert frozen_response.json() == {
        "detail": "fulfilment_frozen",
        "status": STATUS_SHIPPED,
    }
    for response in (
        first,
        replay,
        hidden,
        conflict,
        not_confirmable,
        frozen_response,
    ):
        _assert_no_store(response)


def test_member_order_endpoints_require_member_session_not_lookup_cookie(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 clause 6: a valid lookup cookie cannot access My Orders."""
    member = _member(db, 9)
    order = _order(db, member)
    lookup_headers = _lookup_headers(db, order)
    responses = (
        client.get(LIST_URL),
        client.post(VIEW_URL, json={"order_number": order.order_number}),
        client.post(
            CONFIRM_URL,
            headers={"Idempotency-Key": "g" * 16},
            json={"order_number": order.order_number},
        ),
        client.get(LIST_URL, headers=lookup_headers),
        client.post(
            VIEW_URL,
            headers=lookup_headers,
            json={"order_number": order.order_number},
        ),
        client.post(
            CONFIRM_URL,
            headers=lookup_headers | {"Idempotency-Key": "h" * 16},
            json={"order_number": order.order_number},
        ),
    )
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": "member_session_required"}
        _assert_no_store(response)

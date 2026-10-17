"""Member refund endpoints added by SHOP-TASK-105.

Each test identifies the DESIGN 1.11 or UX P10 sentence it protects.  The
fixtures use the same shared in-memory SQLite/TestClient arrangement as the
member-order APIs, while points are prepared through SHOP-TASK-086 functions.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

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
from app.models import Member, Order, OrderItem, OrderItemUnit
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_SHIPPED,
)
from app.models.order_access import SCOPE_LOOKUP
from app.models.points import PointEntry, PointLot
from app.models.refund import RefundLine, RefundLineUnit, RefundRequest
from app.services.member_auth import csrf_token_for_cookie, issue_member_session
from app.services.order_access import COOKIE_NAME as ORDER_COOKIE
from app.services.order_access import issue_order_access
from app.services.order_rules import generate_order_number
from app.services.points import earn_points, reserve_points

REFUNDS_URL = "/api/member/orders/refunds"
ESTIMATE_URL = "/api/member/orders/refunds/points-estimate"
MEMBER_COOKIE = "__Host-shop_member_session"


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
    application = create_app(Settings(_env_file=None, redis_url=""))

    def session() -> Iterator[Session]:
        with Session(engine) as db:
            yield db

    application.dependency_overrides[get_session] = session
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _member(db: Session, suffix: int) -> Member:
    member = Member(phone=f"+6012666{suffix:04d}", status=MEMBER_ACTIVE, created_at=_now())
    db.add(member)
    db.flush()
    return member


def _headers(db: Session, member: Member, *, csrf: bool = True) -> dict[str, str]:
    issued = issue_member_session(db, member, _now())
    db.commit()
    headers = {"Cookie": f"{MEMBER_COOKIE}={issued.token}"}
    if csrf:
        assert (token := csrf_token_for_cookie(issued.token)) is not None
        headers["X-CSRF-Token"] = token
    return headers


def _order(db: Session, member: Member | None, *, points: bool = False) -> Order:
    now = _now()
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_SHIPPED,
        subtotal_sen=1000,
        coupon_discount_sen=0,
        points_redeemed=120 if points else 0,
        shipping_fee_sen=0,
        total_sen=880 if points else 1000,
        points_earned=8 if points else 0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=uuid.uuid4().hex,
        request_fingerprint="a" * 64,
        created_at=now - timedelta(minutes=5),
        payment_expires_at=now + timedelta(minutes=10),
        paid_at=now,
        member_id=None if member is None else member.id,
        claim_status=CLAIM_NOT_CLAIMABLE if member else CLAIM_OPEN,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        line_index=0,
        variant_id=None,
        sku="REFUND-ITEM",
        product_name_en="Refund item",
        product_name_zh="退款商品",
        product_name_ms="Item refund",
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
            coupon_discount_sen=0,
            points_discount=120 if points else 0,
            cash_paid_sen=880 if points else 1000,
            points_earned=8 if points else 0,
        )
    )
    db.flush()
    return order


def _points_order(db: Session, member: Member) -> Order:
    now = _now()
    source = _order(db, member)
    assert earn_points(db, member.id, source.id, 200, now - timedelta(days=1)) is not None
    order = _order(db, member, points=True)
    reserve_points(db, member.id, order.id, 120, now - timedelta(minutes=1))
    assert earn_points(db, member.id, order.id, 8, now) is not None
    db.commit()
    return order


def _snapshot(engine: Engine) -> tuple[Any, ...]:
    """积分批次、积分流水、退款申请、申请行与逐件记录的全部内容（照 SHOP-TASK-093 的测试）。"""
    with Session(engine) as session:
        tables = (PointLot, PointEntry, RefundRequest, RefundLine, RefundLineUnit)
        return tuple(
            sorted(tuple(row) for row in session.execute(select(model.__table__)))
            for model in tables
        )


def _payload(order: Order) -> dict[str, object]:
    return {"order_number": order.order_number, "lines": [{"line_index": 0, "quantity": 1}]}


def test_member_refund_requires_session_and_csrf(db: Session, client: TestClient) -> None:
    """DESIGN 1.11 permissions: member writes require its session and CSRF protection."""
    member = _member(db, 1)
    order = _order(db, member)
    db.commit()
    missing = client.post(
        REFUNDS_URL, json=_payload(order), headers={"Idempotency-Key": "member-key-unauthorised-01"}
    )
    assert missing.status_code == 401
    assert missing.json() == {"detail": "member_session_required"}
    no_csrf = client.post(
        REFUNDS_URL,
        json=_payload(order),
        headers={
            **_headers(db, member, csrf=False),
            "Idempotency-Key": "member-key-unauthorised-01",
        },
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json() == {"detail": "csrf_failed"}
    assert missing.headers["cache-control"] == no_csrf.headers["cache-control"] == "no-store"


def test_member_refund_is_private_and_replays_as_member(db: Session, client: TestClient) -> None:
    """UX P10: a member submits only their order; replay follows lookup-mode semantics."""
    member = _member(db, 2)
    other = _member(db, 3)
    order = _order(db, member)
    guest = _order(db, None)
    db.commit()
    headers = {**_headers(db, other), "Idempotency-Key": "member-key-private-0001"}
    for hidden in (order, guest):
        response = client.post(REFUNDS_URL, json=_payload(hidden), headers=headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "order_not_found"}
        assert response.headers["cache-control"] == "no-store"
    headers = {**_headers(db, member), "Idempotency-Key": "member-key-replay-00001"}
    created = client.post(REFUNDS_URL, json=_payload(order), headers=headers)
    replay = client.post(REFUNDS_URL, json=_payload(order), headers=headers)
    assert (created.status_code, replay.status_code) == (201, 200)
    assert created.json() == replay.json()
    request = db.scalars(select(RefundRequest).where(RefundRequest.order_id == order.id)).one()
    assert request.actor_type == ACTOR_MEMBER


def test_member_refund_conflict_matches_lookup_mode(db: Session, client: TestClient) -> None:
    """DESIGN 1.11 idempotency: a reused key for different input is a 409 conflict."""
    member = _member(db, 4)
    first = _order(db, member)
    second = _order(db, member)
    db.commit()
    headers = {**_headers(db, member), "Idempotency-Key": "member-key-conflict-0001"}
    assert client.post(REFUNDS_URL, json=_payload(first), headers=headers).status_code == 201
    response = client.post(REFUNDS_URL, json=_payload(second), headers=headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "idempotency_conflict"}
    assert response.headers["cache-control"] == "no-store"


def test_member_points_estimate_is_private_read_only_and_uses_snapshots(
    db: Session, engine: Engine, client: TestClient
) -> None:
    """DESIGN 1.11 example returns 120/8; claimed guest orders return member_order with zeroes."""
    member = _member(db, 5)
    order = _points_order(db, member)
    guest = _order(db, None)
    guest.member_id = member.id
    guest.claim_status = CLAIM_CLAIMED
    other = _member(db, 7)
    db.commit()
    headers = _headers(db, member)
    before = _snapshot(engine)
    response = client.post(ESTIMATE_URL, json=_payload(order), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"member_order": True, "points_back": 120, "points_reversed": 8}
    guest_response = client.post(ESTIMATE_URL, json=_payload(guest), headers=headers)
    assert guest_response.json() == {"member_order": True, "points_back": 0, "points_reversed": 0}
    assert response.headers["cache-control"] == "no-store"
    assert guest_response.headers["cache-control"] == "no-store"
    # 只读：预估前后积分与退款各表内容不变。
    assert _snapshot(engine) == before
    # 本人订单定位：他人的会员订单一律 404 order_not_found。
    hidden = client.post(ESTIMATE_URL, json=_payload(order), headers=_headers(db, other))
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "order_not_found"}
    assert hidden.headers["cache-control"] == "no-store"


def test_lookup_cookie_cannot_authorize_member_refund_endpoints(
    db: Session, client: TestClient
) -> None:
    """DESIGN 1.11 permissions: lookup cookies do not substitute for a member session."""
    member = _member(db, 6)
    order = _order(db, member)
    issued = issue_order_access(db, None, order.id, SCOPE_LOOKUP, _now())
    db.commit()
    assert issued.new_token is not None
    headers = {
        "Cookie": f"{ORDER_COOKIE}={issued.new_token}",
        "Idempotency-Key": "member-key-lookup-cookie-01",
    }
    for url in (REFUNDS_URL, ESTIMATE_URL):
        response = client.post(url, json=_payload(order), headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "member_session_required"}
        assert response.headers["cache-control"] == "no-store"

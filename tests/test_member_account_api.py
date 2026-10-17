"""Contract tests for the member account read APIs."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import member_account as member_account_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Coupon, CouponUse, Member, MemberSession, Order, PointEntry, PointLot
from app.models.coupon import TYPE_FIXED, TYPE_PERCENT, USE_RESERVED, USE_USED
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_PAID
from app.models.points import KIND_EARN
from app.services.member_auth import issue_member_session, revoke_member_session

POINTS_URL = "/api/member/points"
COUPONS_URL = "/api/member/coupons"
COOKIE_NAME = "__Host-shop_member_session"
NO_STORE = "no-store"


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: object, _connection_record: object) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    app = create_app(Settings(_env_file=None, database_url="", redis_url=""))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def add_member(engine: Engine, phone: str) -> int:
    with Session(engine) as session:
        member = Member(
            phone=phone,
            password_hash=None,
            status="active",
            created_at=now(),
            deleted_at=None,
        )
        session.add(member)
        session.commit()
        return member.id


def session_cookie(engine: Engine, member_id: int, *, revoked: bool = False) -> str:
    with Session(engine) as session:
        member = session.get(Member, member_id)
        assert member is not None
        issued = issue_member_session(session, member, now())
        if revoked:
            revoke_member_session(session, issued.session, now())
        session.commit()
        return issued.token


def add_order(session: Session, member_id: int, number: str) -> Order:
    stamp = now() - timedelta(days=1)
    order = Order(
        order_number=number,
        status=STATUS_PAID,
        subtotal_sen=1000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=1000,
        points_earned=10,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"key-{number}",
        request_fingerprint="0" * 64,
        created_at=stamp,
        payment_expires_at=stamp + timedelta(minutes=15),
        paid_at=stamp,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    session.add(order)
    session.flush()
    return order


def read_table_contents(engine: Engine) -> tuple[tuple[tuple[object, ...], ...], ...]:
    """Read every table these endpoints may inspect, preserving all column values."""
    with Session(engine) as session:
        return tuple(
            tuple(session.execute(select(*model.__table__.c).order_by(model.id)).tuples())
            for model in (
                Member,
                MemberSession,
                Order,
                PointLot,
                PointEntry,
                Coupon,
                CouponUse,
            )
        )


def test_unauthenticated_and_revoked_sessions_are_rejected_with_no_store(
    client: TestClient, engine: Engine
) -> None:
    """DESIGN 1.11 权限与资料保护第 6 条：会员只能访问自己的资料。"""
    member_id = add_member(engine, "+60123456789")
    revoked = session_cookie(engine, member_id, revoked=True)
    for url in (POINTS_URL, COUPONS_URL):
        missing = client.get(url)
        assert missing.status_code == 401
        assert missing.headers["cache-control"] == NO_STORE
        response = client.get(url, cookies={COOKIE_NAME: revoked})
        assert response.status_code == 401
        assert response.json() == {"detail": "member_session_required"}
        assert response.headers["cache-control"] == NO_STORE


def test_points_are_member_scoped_typed_and_read_only(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UX 0.10 P13：我的积分显示本会员余额、明细与到期信息。"""
    first = add_member(engine, "+60123456789")
    second = add_member(engine, "+60123456780")
    stamp = datetime(2026, 10, 10, 4, 0, 0)
    # 积分批次 30 天后到期；固定接口时钟在有效期内，测试不随真实日期过期。
    monkeypatch.setattr(member_account_api, "_utcnow", lambda: stamp + timedelta(days=1))
    with Session(engine) as session:
        mine = add_order(session, first, "0000000000000001")
        other = add_order(session, second, "0000000000000002")
        lot = PointLot(
            member_id=first,
            order_id=mine.id,
            points=40,
            acquired_at=stamp,
            expires_at=stamp + timedelta(days=30),
        )
        other_lot = PointLot(
            member_id=second,
            order_id=other.id,
            points=9,
            acquired_at=stamp,
            expires_at=stamp + timedelta(days=30),
        )
        session.add_all([lot, other_lot])
        session.flush()
        session.add_all(
            [
                PointEntry(
                    member_id=first,
                    lot_id=lot.id,
                    kind=KIND_EARN,
                    points=40,
                    order_id=mine.id,
                    refund_request_id=None,
                    created_at=stamp,
                ),
                PointEntry(
                    member_id=second,
                    lot_id=other_lot.id,
                    kind=KIND_EARN,
                    points=9,
                    order_id=other.id,
                    refund_request_id=None,
                    created_at=stamp,
                ),
            ]
        )
        session.commit()
    token = session_cookie(engine, first)
    before = read_table_contents(engine)
    response = client.get(POINTS_URL, cookies={COOKIE_NAME: token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == NO_STORE
    body = response.json()
    assert body["balance"] == 40 and body["pending"] == 0
    assert body["next_expiry"] == {
        "points": 40,
        "date": (stamp + timedelta(days=30)).date().isoformat(),
    }
    assert body["history"] == [
        {
            "type": "earned",
            "points": 40,
            "order_number": "0000000000000001",
            "created_at": stamp.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        }
    ]
    assert read_table_contents(engine) == before


def test_points_next_expiry_is_null_when_no_usable_lots(client: TestClient, engine: Engine) -> None:
    """UX 0.10 P13：没有可显示的积分到期批次时不虚构到期信息。"""
    member_id = add_member(engine, "+60123456789")
    token = session_cookie(engine, member_id)
    before = read_table_contents(engine)
    response = client.get(POINTS_URL, cookies={COOKIE_NAME: token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == NO_STORE
    assert response.json() == {"balance": 0, "pending": 0, "next_expiry": None, "history": []}
    assert read_table_contents(engine) == before


def test_coupons_are_shared_but_used_history_is_member_scoped_and_read_only(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """UX 0.10 P13、Q19：可用券全员相同，使用记录仅列当前会员的已核销券。"""
    first = add_member(engine, "+60123456789")
    second = add_member(engine, "+60123456780")
    stamp = now()
    with Session(engine) as session:
        mine = add_order(session, first, "0000000000000001")
        other = add_order(session, second, "0000000000000002")
        reserved = add_order(session, first, "0000000000000003")
        fixed = Coupon(
            code="SAVE10",
            coupon_type=TYPE_FIXED,
            amount_off_sen=100,
            percent_off=None,
            min_subtotal_sen=500,
            starts_at=stamp - timedelta(days=1),
            ends_at=stamp + timedelta(days=1),
            total_use_limit=1,
            per_member_use_limit=1,
            is_active=True,
        )
        percent = Coupon(
            code="SAVE20",
            coupon_type=TYPE_PERCENT,
            amount_off_sen=None,
            percent_off=20,
            min_subtotal_sen=0,
            starts_at=stamp - timedelta(days=1),
            ends_at=stamp + timedelta(days=2),
            total_use_limit=1,
            per_member_use_limit=1,
            is_active=True,
        )
        session.add_all([fixed, percent])
        session.flush()
        session.add_all(
            [
                CouponUse(
                    coupon_id=fixed.id,
                    order_id=mine.id,
                    member_id=first,
                    status=USE_USED,
                    coupon_discount_sen=100,
                    created_at=stamp,
                    updated_at=stamp,
                ),
                CouponUse(
                    coupon_id=percent.id,
                    order_id=other.id,
                    member_id=second,
                    status=USE_USED,
                    coupon_discount_sen=200,
                    created_at=stamp,
                    updated_at=stamp,
                ),
                CouponUse(
                    coupon_id=percent.id,
                    order_id=reserved.id,
                    member_id=first,
                    status=USE_RESERVED,
                    coupon_discount_sen=200,
                    created_at=stamp,
                    updated_at=stamp,
                ),
            ]
        )
        session.commit()
    first_token = session_cookie(engine, first)
    second_token = session_cookie(engine, second)
    before = read_table_contents(engine)
    caplog.set_level(logging.DEBUG)
    first_response = client.get(COUPONS_URL, cookies={COOKIE_NAME: first_token})
    second_response = client.get(COUPONS_URL, cookies={COOKIE_NAME: second_token})
    assert first_response.status_code == second_response.status_code == 200
    assert first_response.headers["cache-control"] == NO_STORE
    assert second_response.headers["cache-control"] == NO_STORE
    assert first_response.json()["available"] == second_response.json()["available"]
    assert {item["code"] for item in first_response.json()["available"]} == {"SAVE10", "SAVE20"}
    available = {item["code"]: item for item in first_response.json()["available"]}
    assert available["SAVE10"] == {
        "code": "SAVE10",
        "type": "fixed",
        "amount_sen": 100,
        "percent": None,
        "min_spend_sen": 500,
        "valid_until": (stamp + timedelta(days=1))
        .replace(tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    assert available["SAVE20"]["type"] == "percent"
    assert available["SAVE20"]["amount_sen"] is None
    assert available["SAVE20"]["percent"] == 20
    assert available["SAVE20"]["valid_until"].endswith("Z")
    assert first_response.json()["used"] == [
        {"code": "SAVE10", "order_number": "0000000000000001", "discount_sen": 100}
    ]
    assert all(item["order_number"] != "0000000000000003" for item in first_response.json()["used"])
    assert second_response.json()["used"] == [
        {"code": "SAVE20", "order_number": "0000000000000002", "discount_sen": 200}
    ]
    assert read_table_contents(engine) == before
    assert [record for record in caplog.records if record.name.startswith("app")] == []

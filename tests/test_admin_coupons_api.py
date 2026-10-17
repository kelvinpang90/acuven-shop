"""A05 后台优惠券接口；每项测试说明它守住的设计、UX 或 Kelvin 决定。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import admin_auth as admin_auth_api
from app.api import admin_coupons as admin_coupons_api
from app.api.admin_auth import COOKIE_NAME, csrf_token_for_cookie
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount, AuditEvent, Coupon, CouponUse, Member, Order
from app.models.coupon import USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.admin_auth import issue_admin_session
from app.services.admin_coupons import ADMIN_COUPON_CREATED, ADMIN_COUPON_DISABLED

URL = "/api/admin/coupons"
NOW = datetime(2026, 10, 15, 12, 0, 0)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 新建表单的结束日不得早于今天、后台会话 30 天到期；固定两处接口时钟，测试不随真实日期过期。
    monkeypatch.setattr(admin_coupons_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)
    app = create_app(Settings(_env_file=None, database_url="sqlite://"))

    def session_override() -> Iterator[Session]:
        with Session(engine) as session:
            try:
                yield session
            finally:
                session.rollback()

    app.dependency_overrides[get_session] = session_override
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin_token(db: Session) -> str:
    account = AdminAccount(
        username="admin@example.test",
        password_hash="hash",
        created_at=NOW,
        password_updated_at=NOW,
    )
    db.add(account)
    db.flush()
    issued = issue_admin_session(db, account.id, NOW - timedelta(minutes=1))
    db.commit()
    return issued.token


def _headers(token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token_for_cookie(token) or ""}


def _login(client: TestClient, token: str) -> None:
    client.cookies.set(COOKIE_NAME, token)


def _body(**changes: object) -> dict[str, object]:
    return {
        "code": "SAVE10",
        "type": "fixed",
        "amount_off_sen": 1000,
        "percent_off": None,
        "min_subtotal_sen": 5000,
        "starts_on": "2026-10-15",
        "ends_on": "2026-10-16",
        "total_use_limit": 10,
        "per_member_use_limit": None,
    } | changes


def _coupon_use(db: Session, coupon_id: int, status: str, index: int) -> None:
    member = Member(
        phone=f"+6012000{index:04d}",
        password_hash=None,
        status=MEMBER_ACTIVE,
        created_at=NOW,
        deleted_at=None,
    )
    order = Order(
        order_number=f"{index:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=1000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=1000,
        points_earned=0,
        shipping_zone_code="MY-01",
        shipping_rate_version=1,
        idempotency_key=f"coupon-use-{index}",
        request_fingerprint="a" * 64,
        created_at=NOW,
        payment_expires_at=NOW + timedelta(minutes=15),
        paid_at=None,
        member_id=None,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add_all((member, order))
    db.flush()
    db.add(
        CouponUse(
            coupon_id=coupon_id,
            order_id=order.id,
            member_id=member.id,
            status=status,
            coupon_discount_sen=0,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _assert_no_store(response: object) -> None:
    assert response.headers["cache-control"] == "no-store"


def test_all_endpoints_require_admin_session_and_no_store(client: TestClient) -> None:
    """DESIGN 1.11 权限保护第 5 条：单一管理员仍须服务端授权。"""
    responses = (client.get(URL), client.post(URL, json=_body()), client.post(f"{URL}/1/disable"))
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": "admin_session_required"}
        _assert_no_store(response)


def test_list_fields_pagination_and_malaysia_dates(
    client: TestClient, db: Session, admin_token: str
) -> None:
    """HANDOFF 0.48 第 2、3 条：日期按马来西亚日、已用含预占与核销。"""
    _login(client, admin_token)
    for index in range(31):
        db.add(
            Coupon(
                code=f"CODE{index:04d}",
                coupon_type="fixed",
                amount_off_sen=100,
                percent_off=None,
                min_subtotal_sen=0,
                starts_at=datetime(2026, 10, 14, 16),
                ends_at=datetime(2026, 10, 15, 15, 59, 59),
                total_use_limit=None,
                per_member_use_limit=None,
                is_active=index != 0,
                created_at=NOW + timedelta(seconds=index),
            )
        )
    db.flush()
    coupon_id = db.scalar(select(Coupon.id).where(Coupon.code == "CODE0000"))
    assert coupon_id is not None
    _coupon_use(db, coupon_id, USE_RESERVED, 1)
    _coupon_use(db, coupon_id, USE_USED, 2)
    _coupon_use(db, coupon_id, USE_RELEASED, 3)
    db.commit()
    response = client.get(URL, params={"page": "2"})
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 31 and result["page"] == 2 and result["page_size"] == 30
    assert result["coupons"][0]["starts_on"] == "2026-10-15"
    assert result["coupons"][0]["used_count"] == 2
    assert set(result["coupons"][0]) == {
        "id",
        "code",
        "type",
        "amount_off_sen",
        "percent_off",
        "min_subtotal_sen",
        "starts_on",
        "ends_on",
        "total_use_limit",
        "per_member_use_limit",
        "used_count",
        "active",
    }
    assert result["csrf_token"] == csrf_token_for_cookie(admin_token)
    _assert_no_store(response)


def test_create_commits_audit_and_is_visible_to_another_session(
    client: TestClient, db: Session, admin_token: str
) -> None:
    """UX A05 与 Kelvin 2026-10-12 第 1 条：管理员能新建优惠券且操作留审计。"""
    _login(client, admin_token)
    response = client.post(URL, json=_body(), headers=_headers(admin_token))
    assert response.status_code == 201 and response.json()["active"] is True
    assert db.scalar(select(Coupon.code)) == "SAVE10"
    assert db.scalar(select(AuditEvent.action)) == ADMIN_COUPON_CREATED
    _assert_no_store(response)


@pytest.mark.parametrize(
    "body, status", [(_body(amount_off_sen=None), 422), (_body(code="save10"), 409)]
)
def test_create_domain_rejections_do_not_write(
    client: TestClient, db: Session, admin_token: str, body: dict[str, object], status: int
) -> None:
    """HANDOFF 0.48 第 5 条：无效券与已占代码分别拒绝且不产生新写入。"""
    _login(client, admin_token)
    if status == 409:
        client.post(URL, json=_body(), headers=_headers(admin_token))
    before = db.query(Coupon).count()
    response = client.post(URL, json=body, headers=_headers(admin_token))
    assert response.status_code == status
    assert db.query(Coupon).count() == before
    _assert_no_store(response)


def test_create_csrf_body_order_and_private_validation(
    client: TestClient, db: Session, admin_token: str
) -> None:
    """DESIGN 1.11 权限保护：写操作 CSRF；验收约束：错误不回显提交内容。"""
    _login(client, admin_token)
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        response = client.post(URL, json=_body(), headers=headers)
        assert response.status_code == 403 and db.query(Coupon).count() == 0
        _assert_no_store(response)
    malformed = client.post(URL, json=_body(extra="secret-value"), headers=_headers(admin_token))
    internal_name_body = _body()
    internal_name_body["coupon_type"] = internal_name_body.pop("type")
    internal_name = client.post(URL, json=internal_name_body, headers=_headers(admin_token))
    typed = client.post(
        URL, json=_body(min_subtotal_sen="secret-value"), headers=_headers(admin_token)
    )
    for response in (malformed, internal_name, typed):
        assert response.status_code == 422 and "secret-value" not in response.text
        _assert_no_store(response)
    non_json = client.post(URL, content=b"no", headers={"content-type": "text/plain"})
    too_large = client.post(URL, content=b"x" * 8193, headers={"content-type": "application/json"})
    assert non_json.status_code == 415 and too_large.status_code == 413
    _assert_no_store(non_json)
    _assert_no_store(too_large)


def test_disable_is_idempotent_and_validates_path(
    client: TestClient, db: Session, admin_token: str
) -> None:
    """Kelvin 2026-10-12 第 1 条：只能停用，重复停用不再写审计。"""
    _login(client, admin_token)
    created = client.post(URL, json=_body(), headers=_headers(admin_token)).json()
    first = client.post(f"{URL}/{created['id']}/disable", headers=_headers(admin_token))
    second = client.post(f"{URL}/{created['id']}/disable", headers=_headers(admin_token))
    assert first.status_code == second.status_code == 200 and second.json()["active"] is False
    assert db.query(AuditEvent).filter(AuditEvent.action == ADMIN_COUPON_DISABLED).count() == 1
    paths = (
        ("0", 422),
        ("01", 422),
        ("+1", 422),
        ("2147483648", 422),
        ("999", 404),
    )
    for path, expected in paths:
        response = client.post(f"{URL}/{path}/disable", headers=_headers(admin_token))
        assert response.status_code == expected
        _assert_no_store(response)
    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        response = client.post(f"{URL}/01/disable", headers=headers)
        assert response.status_code == 403
        assert response.json() == {"detail": "csrf_failed"}
        _assert_no_store(response)

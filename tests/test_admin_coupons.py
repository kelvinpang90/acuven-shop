"""后台优惠券规则；每项说明其守住的设计、UX 或 Kelvin 决定。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Coupon, CouponUse, Member, Order
from app.models.admin import AdminAccount, AuditEvent
from app.models.coupon import TYPE_FIXED, TYPE_PERCENT, USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.admin_coupons import (
    ADMIN_COUPON_CREATED,
    ADMIN_COUPON_DISABLED,
    AdminCouponCreate,
    CouponCodeTaken,
    CouponInvalid,
    CouponNotFound,
    create_admin_coupon,
    disable_admin_coupon,
    list_admin_coupons,
)

NOW = datetime(2026, 10, 14, 15, 59, 59)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


@pytest.fixture
def admin_id(db: Session) -> int:
    account = AdminAccount(
        username="admin@example.test",
        password_hash="not-a-real-hash",
        created_at=NOW,
        password_updated_at=NOW,
    )
    db.add(account)
    db.commit()
    return account.id


def _request(**changes: object) -> AdminCouponCreate:
    values: dict[str, object] = {
        "code": "SAVE10",
        "coupon_type": TYPE_FIXED,
        "amount_off_sen": 1000,
        "percent_off": None,
        "min_subtotal_sen": 5000,
        "starts_on": date(2026, 10, 14),
        "ends_on": date(2026, 10, 14),
        "total_use_limit": 100,
        "per_member_use_limit": 1,
    }
    return AdminCouponCreate(**(values | changes))


def _coupon(db: Session, admin_id: int, **changes: object):
    return create_admin_coupon(db, admin_id, _request(**changes), NOW)


_sequence = 0


def _coupon_use(db: Session, coupon_id: int, status: str) -> CouponUse:
    """为计数测试建 active 会员及其 not_claimable 的待支付订单。"""
    global _sequence
    _sequence += 1
    member = Member(
        phone=f"+6010000{_sequence:04d}",
        password_hash=None,
        status=MEMBER_ACTIVE,
        created_at=NOW,
        deleted_at=None,
    )
    order = Order(
        order_number=f"{_sequence:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=1000,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=1000,
        points_earned=0,
        shipping_zone_code="MY-01",
        shipping_rate_version=1,
        idempotency_key=f"coupon-use-{_sequence}",
        request_fingerprint="a" * 64,
        created_at=NOW,
        payment_expires_at=NOW + timedelta(minutes=15),
        paid_at=None,
        member_id=None,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add_all((member, order))
    db.flush()
    order.member_id = member.id
    use = CouponUse(
        coupon_id=coupon_id,
        order_id=order.id,
        member_id=member.id,
        status=status,
        coupon_discount_sen=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(use)
    db.flush()
    return use


def test_create_normalizes_dates_and_audits(db: Session, admin_id: int) -> None:
    """UX A05 的创建日期与 DESIGN 的后台审计要求不能丢失。"""
    row = _coupon(db, admin_id, code=" save10 ")
    stored = db.get(Coupon, row.id)
    audit = db.scalars(select(AuditEvent)).one()
    assert (stored.code, stored.starts_at, stored.ends_at) == (
        "SAVE10",
        datetime(2026, 10, 13, 16),
        NOW,
    )
    assert (audit.action, audit.target_type, audit.new_value, audit.old_value) == (
        ADMIN_COUPON_CREATED,
        "coupon",
        "SAVE10",
        None,
    )


def test_create_only_flushes(db: Session, admin_id: int) -> None:
    """本任务契约要求新建仅 flush，以便调用方可整体回滚。"""
    _coupon(db, admin_id)
    db.rollback()
    assert db.scalars(select(Coupon)).all() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "BAD-1"},
        {"code": "ABC"},
        {"code": "A" * 21},
        {"coupon_type": "other"},
        {"amount_off_sen": 0},
        {"amount_off_sen": 10_000_001},
        {"coupon_type": TYPE_PERCENT, "amount_off_sen": None, "percent_off": 0},
        {"coupon_type": TYPE_PERCENT, "amount_off_sen": None, "percent_off": 101},
        {"coupon_type": TYPE_PERCENT, "amount_off_sen": 1, "percent_off": 10},
        {"percent_off": 10},
        {"starts_on": date(2026, 10, 15), "ends_on": date(2026, 10, 14)},
        {"starts_on": date(2026, 10, 13), "ends_on": date(2026, 10, 13)},
        {"min_subtotal_sen": -1},
        {"min_subtotal_sen": 10_000_001},
        {"total_use_limit": 0},
        {"total_use_limit": 1_000_001},
        {"per_member_use_limit": 0},
        {"per_member_use_limit": 1_000_001},
    ],
)
def test_create_rejects_invalid_values(
    db: Session, admin_id: int, changes: dict[str, object]
) -> None:
    """UX A05 的类型、消费门槛、期限与两类次数限制都必须受规则保护。"""
    with pytest.raises(CouponInvalid):
        _coupon(db, admin_id, **changes)


def test_create_accepts_boundaries(db: Session, admin_id: int) -> None:
    """UX A05 允许全部规定边界，Kelvin 决定当天最后一秒仍可新建。"""
    for code, kind, amount, percent, minimum, total, member in (
        ("A001", TYPE_FIXED, 1, None, 0, None, None),
        ("B" * 20, TYPE_FIXED, 10_000_000, None, 10_000_000, 1, 1),
        ("C001", TYPE_PERCENT, None, 1, 0, 1_000_000, 1_000_000),
        ("D001", TYPE_PERCENT, None, 100, 0, None, None),
    ):
        row = _coupon(
            db,
            admin_id,
            code=code,
            coupon_type=kind,
            amount_off_sen=amount,
            percent_off=percent,
            min_subtotal_sen=minimum,
            total_use_limit=total,
            per_member_use_limit=member,
        )
        assert row.code == code
    with pytest.raises(CouponInvalid):
        create_admin_coupon(db, admin_id, _request(), NOW + timedelta(seconds=1))


def test_create_duplicate_normalized_code(db: Session, admin_id: int) -> None:
    """DESIGN 的代码库内大写要求让大小写不同代码也只能有一张券。"""
    _coupon(db, admin_id, code="save10")
    with pytest.raises(CouponCodeTaken):
        _coupon(db, admin_id, code="SAVE10")
    assert len(db.scalars(select(AuditEvent)).all()) == 1
    assert len(db.scalars(select(Coupon)).all()) == 1


def test_list_paginates_orders_and_converts_dates(db: Session, admin_id: int) -> None:
    """UX A05 的列表按新建倒序显示，任务契约固定每页 30 张。"""
    ids = []
    for number in range(31):
        row = _coupon(
            db,
            admin_id,
            code=f"X{number:03d}",
            starts_on=date(2026, 10, 15),
            ends_on=date(2026, 10, 15),
        )
        coupon = db.get(Coupon, row.id)
        coupon.created_at = NOW + timedelta(seconds=300 if number < 2 else 200 - number)
        ids.append(row.id)
    first, second, third = (list_admin_coupons(db, page) for page in (1, 2, 3))
    assert [row.id for row in first.items] == [ids[1], ids[0], *ids[2:30]]
    assert (
        [row.id for row in second.items],
        second.items[0].starts_on,
        second.items[0].ends_on,
    ) == ([ids[30]], date(2026, 10, 15), date(2026, 10, 15))
    assert (third.items, third.total) == ([], 31)


def test_list_counts_reserved_and_used_once(db: Session, admin_id: int) -> None:
    """Kelvin 0.48 决定预占与核销都计已用，released 不计。"""
    first = _coupon(db, admin_id, code="FIRST")
    second = _coupon(db, admin_id, code="SECOND")
    for status in (USE_RESERVED, USE_USED, USE_RELEASED):
        _coupon_use(db, first.id, status)
    _coupon_use(db, second.id, USE_RESERVED)
    listed = {row.id: row.used_count for row in list_admin_coupons(db, 1).items}
    assert listed[first.id] == 2
    assert listed[second.id] == 1


def test_disable_is_idempotent_and_audited(db: Session, admin_id: int) -> None:
    """UX A05 只允许停用，重复操作不应重复写审计。"""
    coupon = _coupon(db, admin_id)
    disabled = disable_admin_coupon(db, admin_id, coupon.id, NOW)
    repeated = disable_admin_coupon(db, admin_id, coupon.id, NOW)
    audit = db.scalars(select(AuditEvent).where(AuditEvent.action == ADMIN_COUPON_DISABLED)).one()
    assert not disabled.is_active and not repeated.is_active
    assert (audit.target_type, audit.old_value, audit.new_value) == ("coupon", "active", "disabled")
    with pytest.raises(CouponNotFound):
        disable_admin_coupon(db, admin_id, 999_999, NOW)


def test_disable_keeps_reserved_use_and_only_flushes(db: Session, admin_id: int) -> None:
    """DESIGN 的后台修改只影响新订单；本任务契约要求停用仅 flush。"""
    coupon = _coupon(db, admin_id)
    use = _coupon_use(db, coupon.id, USE_RESERVED)
    db.commit()
    disabled = disable_admin_coupon(db, admin_id, coupon.id, NOW)
    assert disabled.used_count == 1
    assert db.get(CouponUse, use.id).status == USE_RESERVED
    db.rollback()
    assert db.get(Coupon, coupon.id).is_active

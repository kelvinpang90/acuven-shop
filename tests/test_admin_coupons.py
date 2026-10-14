"""后台优惠券规则；每项守住 DESIGN 1.11、UX A05 或 Kelvin 2026-10-12 决定。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AdminAccount, AuditEvent, Coupon, CouponUse, Member, Order
from app.models.coupon import TYPE_FIXED, USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.admin_coupons import (
    ADMIN_COUPON_CREATED,
    ADMIN_COUPON_DISABLED,
    AdminCouponCreateRequest,
    CouponCodeTaken,
    CouponInvalid,
    CouponNotFound,
    create_coupon,
    disable_coupon,
    list_coupons,
)

NOW = datetime(2026, 10, 14, 15, 59, 59)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _admin(db: Session) -> AdminAccount:
    row = AdminAccount(
        username="admin@example.test",
        password_hash="x",
        password_updated_at=NOW,
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def _request(**changes: object) -> AdminCouponCreateRequest:
    values: dict[str, object] = dict(
        code=" SAVE10 ",
        coupon_type=TYPE_FIXED,
        amount_off_sen=1000,
        percent_off=None,
        min_subtotal_sen=0,
        starts_on=NOW.date(),
        ends_on=NOW.date(),
        total_use_limit=None,
        per_member_use_limit=None,
    )
    values.update(changes)
    return AdminCouponCreateRequest(**values)


def _coupon(db: Session, **changes: object) -> Coupon:
    admin = _admin(db)
    return_coupon = create_coupon(db, admin.id, _request(**changes), NOW)
    return db.get(Coupon, return_coupon.id)


def _use(db: Session, coupon: Coupon, status: str) -> CouponUse:
    number = db.scalar(select(text("count(*)")).select_from(Member)) or 0
    member = Member(phone=f"+601000000{number}", status=MEMBER_ACTIVE, created_at=NOW)
    db.add(member)
    db.flush()
    order = Order(
        order_number=f"{number + 1:016d}",
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
        request_fingerprint="0" * 64,
        created_at=NOW,
        payment_expires_at=NOW + timedelta(minutes=15),
        paid_at=None,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add(order)
    db.flush()
    use = CouponUse(
        coupon_id=coupon.id,
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


def test_create_normalizes_dates_audits_and_rolls_back(db: Session) -> None:
    """Kelvin 的日期边界与 DESIGN 的管理员操作审计；任务契约规定只 flush。"""
    admin = _admin(db)
    row = create_coupon(db, admin.id, _request(code=" save10 "), NOW)
    coupon = db.get(Coupon, row.id)
    assert (coupon.code, coupon.starts_at, coupon.ends_at) == (
        "SAVE10",
        datetime(2026, 10, 13, 16),
        NOW,
    )
    audit = db.scalars(select(AuditEvent)).one()
    assert (audit.action, audit.target_type, audit.new_value, audit.old_value) == (
        ADMIN_COUPON_CREATED,
        "coupon",
        "SAVE10",
        None,
    )
    db.rollback()
    assert db.scalar(select(Coupon)) is None


@pytest.mark.parametrize(
    "request",
    [
        _request(code="BAD-"),
        _request(code="ABC"),
        _request(code="A" * 21),
        _request(amount_off_sen=0),
        _request(amount_off_sen=10_000_001),
        _request(coupon_type="percent", amount_off_sen=None, percent_off=0),
        _request(coupon_type="percent", amount_off_sen=None, percent_off=101),
        _request(coupon_type="percent", amount_off_sen=1, percent_off=1),
        _request(percent_off=1),
        _request(starts_on=datetime(2026, 10, 15).date(), ends_on=NOW.date()),
        _request(
            starts_on=datetime(2026, 10, 13).date(),
            ends_on=datetime(2026, 10, 13).date(),
        ),
        _request(min_subtotal_sen=-1),
        _request(min_subtotal_sen=10_000_001),
        _request(total_use_limit=0),
        _request(total_use_limit=1_000_001),
        _request(per_member_use_limit=0),
        _request(per_member_use_limit=1_000_001),
    ],
)
def test_create_rejects_invalid_a05_fields(db: Session, request: AdminCouponCreateRequest) -> None:
    """UX A05 的面额、日期、最低消费与次数上限只能是任务契约的合法范围。"""
    with pytest.raises(CouponInvalid):
        create_coupon(db, _admin(db).id, request, NOW)


def test_duplicate_is_normalized_and_has_no_extra_audit(db: Session) -> None:
    """代码按 SHOP-TASK-085 规范化，大小写变体不能绕过唯一代码。"""
    admin = _admin(db)
    create_coupon(db, admin.id, _request(code="save10"), NOW)
    with pytest.raises(CouponCodeTaken):
        create_coupon(db, admin.id, _request(code=" SAVE10 "), NOW)
    assert len(db.scalars(select(Coupon)).all()) == len(db.scalars(select(AuditEvent)).all()) == 1


def test_create_accepts_all_a05_boundaries_and_today_last_second(db: Session) -> None:
    """UX A05 的各输入边界，以及 Kelvin 决定的马来西亚整日有效期都可建立。"""
    admin = _admin(db)
    fixed_low = create_coupon(
        db,
        admin.id,
        _request(code="ABCD", amount_off_sen=1, min_subtotal_sen=0),
        NOW,
    )
    fixed_high = create_coupon(
        db,
        admin.id,
        _request(
            code="A" * 20,
            amount_off_sen=10_000_000,
            min_subtotal_sen=10_000_000,
            total_use_limit=1,
            per_member_use_limit=1_000_000,
        ),
        NOW,
    )
    percent_low = create_coupon(
        db,
        admin.id,
        _request(
            code="PLOW",
            coupon_type="percent",
            amount_off_sen=None,
            percent_off=1,
            total_use_limit=1_000_000,
            per_member_use_limit=1,
        ),
        NOW,
    )
    percent_high = create_coupon(
        db,
        admin.id,
        _request(
            code="PHIG",
            coupon_type="percent",
            amount_off_sen=None,
            percent_off=100,
        ),
        NOW,
    )
    assert [fixed_low.code, fixed_high.code, percent_low.percent_off, percent_high.percent_off] == [
        "ABCD",
        "A" * 20,
        1,
        100,
    ]
    with pytest.raises(CouponInvalid):
        create_coupon(db, admin.id, _request(code="LATE"), NOW + timedelta(seconds=1))


def test_list_is_paginated_sorted_and_counts_reserved_and_used(db: Session) -> None:
    """UX A05 显示已用与上限；Kelvin 决定 reserved 和 used 都计次，任务契约每页 30 条。"""
    admin = _admin(db)
    coupons = []
    for n in range(31):
        row = create_coupon(
            db,
            admin.id,
            _request(
                code=f"C{n:03d}",
                starts_on=datetime(2026, 10, 15).date(),
                ends_on=datetime(2026, 10, 15).date(),
            ),
            NOW + timedelta(seconds=300 if n < 2 else 200 - n),
        )
        coupons.append(db.get(Coupon, row.id))
    _use(db, coupons[0], USE_RESERVED)
    _use(db, coupons[0], USE_USED)
    _use(db, coupons[0], USE_RELEASED)
    _use(db, coupons[1], USE_RESERVED)
    first, second, third = list_coupons(db, 1), list_coupons(db, 2), list_coupons(db, 3)
    assert [row.id for row in first.coupons] == [
        coupons[1].id,
        coupons[0].id,
        *[coupon.id for coupon in coupons[2:30]],
    ]
    assert (first.coupons[0].used_count, first.coupons[1].used_count) == (1, 2)
    assert (
        second.total,
        [row.id for row in second.coupons],
        second.coupons[0].starts_on,
    ) == (31, [coupons[30].id], datetime(2026, 10, 15).date())
    assert (third.total, third.coupons) == (31, [])


def test_disable_is_idempotent_counts_uses_and_rollback_restores(db: Session) -> None:
    """Kelvin 决定只停用且不改已预占记录；DESIGN 要求后台维护留审计。"""
    coupon = _coupon(db)
    use = _use(db, coupon, USE_RESERVED)
    admin = db.scalars(select(AdminAccount)).one()
    disabled = disable_coupon(db, admin.id, coupon.id, NOW)
    assert (disabled.is_active, disabled.used_count, db.get(CouponUse, use.id).status) == (
        False,
        1,
        USE_RESERVED,
    )
    assert (
        db.scalars(select(AuditEvent).where(AuditEvent.action == ADMIN_COUPON_DISABLED))
        .one()
        .old_value
        == "active"
    )
    assert (
        db.scalars(select(AuditEvent).where(AuditEvent.action == ADMIN_COUPON_DISABLED))
        .one()
        .new_value
        == "disabled"
    )
    assert disable_coupon(db, admin.id, coupon.id, NOW).is_active is False
    disabled_audits = db.scalars(
        select(AuditEvent).where(AuditEvent.action == ADMIN_COUPON_DISABLED)
    ).all()
    assert len(disabled_audits) == 1
    with pytest.raises(CouponNotFound):
        disable_coupon(db, admin.id, 99999, NOW)
    db.rollback()
    assert db.get(Coupon, coupon.id).is_active is True

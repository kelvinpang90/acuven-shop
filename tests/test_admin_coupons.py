"""后台优惠券规则；每项测试说明所守住的已批准设计或 Kelvin 决定。"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AdminAccount, AuditEvent, Coupon, CouponUse, Member, Order
from app.models.coupon import USE_RELEASED, USE_RESERVED, USE_USED
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.admin_coupons import (
    ADMIN_COUPON_CREATED,
    ADMIN_COUPON_DISABLED,
    CouponCodeTaken,
    CouponCreate,
    CouponInvalid,
    CouponNotFound,
    create_coupon,
    disable_coupon,
    list_coupons,
)

NOW = datetime(2026, 10, 14, 15, 59, 59)


@pytest.fixture
def db() -> Session:
    """按任务契约用开启外键检查的 SQLite 内存库建全部模型表。"""
    engine = create_engine("sqlite://", poolclass=StaticPool)
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    admin = AdminAccount(
        username="admin@example.test",
        password_hash="hash",
        password_updated_at=NOW,
        created_at=NOW,
    )
    session.add(admin)
    session.flush()
    yield session
    session.close()


def request(**changes: object) -> CouponCreate:
    """有效的 fixed 券输入；个案只改和设计规则有关的字段。"""
    values: dict[str, object] = {
        "code": "SAVE10",
        "coupon_type": "fixed",
        "amount_off_sen": 1000,
        "percent_off": None,
        "min_subtotal_sen": 0,
        "start_date": date(2026, 10, 14),
        "end_date": date(2026, 10, 14),
        "total_use_limit": None,
        "per_member_use_limit": None,
    }
    values.update(changes)
    return CouponCreate(**values)


def admin_id(db: Session) -> int:
    return db.scalar(select(AdminAccount.id))


def add_use(db: Session, coupon_id: int, number: int, status: str) -> None:
    """Create the required order and member parents for one coupon reservation."""
    member = Member(phone=f"+6010000{number:04}", status=MEMBER_ACTIVE, created_at=NOW)
    db.add(member)
    db.flush()
    order = Order(
        order_number=f"{number:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=1000,
        coupon_discount_sen=100,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=900,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"coupon-use-{number}",
        request_fingerprint="0f" * 32,
        created_at=NOW,
        payment_expires_at=NOW,
        paid_at=None,
        member_id=member.id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add(order)
    db.flush()
    db.add(
        CouponUse(
            coupon_id=coupon_id,
            order_id=order.id,
            member_id=member.id,
            status=status,
            coupon_discount_sen=100,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()


def test_create_normalizes_dates_converts_to_utc_and_audits_code_only(
    db: Session,
) -> None:
    """Kelvin 2026-10-12：日期按马来西亚时间，且后台操作保留审计记录。"""
    row = create_coupon(db, admin_id(db), request(code=" save10 "), NOW)
    coupon = db.get(Coupon, row.id)
    audit = db.scalar(select(AuditEvent))
    assert (row.code, row.start_date, row.end_date) == (
        "SAVE10",
        date(2026, 10, 14),
        date(2026, 10, 14),
    )
    assert (coupon.starts_at, coupon.ends_at) == (
        datetime(2026, 10, 13, 16),
        datetime(2026, 10, 14, 15, 59, 59),
    )
    assert (audit.action, audit.target_type, audit.new_value, audit.old_value) == (
        ADMIN_COUPON_CREATED,
        "coupon",
        "SAVE10",
        None,
    )


def test_create_does_not_commit(db: Session) -> None:
    """DESIGN 1.11: creating a coupon only flushes, so its transaction remains reversible."""
    create_coupon(db, admin_id(db), request(), NOW)
    db.rollback()
    assert db.scalar(select(Coupon.id)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "bad-"},
        {"code": "ABC"},
        {"code": "A" * 21},
        {"coupon_type": "fixed", "amount_off_sen": 0},
        {"coupon_type": "percent", "amount_off_sen": 1, "percent_off": 10},
        {"coupon_type": "percent", "amount_off_sen": None, "percent_off": 101},
        {"end_date": date(2026, 10, 13)},
        {"total_use_limit": 0},
    ],
)
def test_create_rejects_invalid_admin_coupon_input(db: Session, changes: dict[str, object]) -> None:
    """A05：代码、面额、日期与次数上限必须在后台新建时校验。"""
    with pytest.raises(CouponInvalid):
        create_coupon(db, admin_id(db), request(**changes), NOW)


def test_create_accepts_code_length_and_discount_boundaries(db: Session) -> None:
    """A05: administrators may create fixed or percent coupons at each approved boundary."""
    fixed_low = create_coupon(db, admin_id(db), request(code="ABCD", amount_off_sen=1), NOW)
    fixed_high = create_coupon(
        db, admin_id(db), request(code="B" * 20, amount_off_sen=10_000_000), NOW
    )
    percent_low = create_coupon(
        db,
        admin_id(db),
        request(code="PCTLOW", coupon_type="percent", amount_off_sen=None, percent_off=1),
        NOW,
    )
    percent_high = create_coupon(
        db,
        admin_id(db),
        request(code="PCTHIGH", coupon_type="percent", amount_off_sen=None, percent_off=100),
        NOW,
    )
    assert [fixed_low.amount_off_sen, fixed_high.amount_off_sen] == [1, 10_000_000]
    assert [percent_low.percent_off, percent_high.percent_off] == [1, 100]


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_off_sen": 10_000_001},
        {"coupon_type": "percent", "amount_off_sen": None, "percent_off": 0},
        {"coupon_type": "fixed", "percent_off": 1},
    ],
)
def test_create_rejects_out_of_range_or_wrong_discount_fields(
    db: Session, changes: dict[str, object]
) -> None:
    """A05: discount type selects exactly one valid discount field and its approved range."""
    with pytest.raises(CouponInvalid):
        create_coupon(db, admin_id(db), request(**changes), NOW)


def test_create_validates_dates_against_malaysia_today(db: Session) -> None:
    """Kelvin 2026-10-12: dates use Malaysia time; same-day validity is allowed."""
    create_coupon(db, admin_id(db), request(code="SAMEDAY"), NOW)
    with pytest.raises(CouponInvalid):
        create_coupon(
            db,
            admin_id(db),
            request(
                code="REVERSED",
                start_date=date(2026, 10, 15),
                end_date=date(2026, 10, 14),
            ),
            NOW,
        )
    with pytest.raises(CouponInvalid):
        create_coupon(
            db,
            admin_id(db),
            request(
                code="PASTDATE",
                start_date=date(2026, 10, 13),
                end_date=date(2026, 10, 13),
            ),
            NOW,
        )
    create_coupon(
        db,
        admin_id(db),
        request(code="BEFOREMID", end_date=date(2026, 10, 14)),
        datetime(2026, 10, 14, 15, 59, 59),
    )
    with pytest.raises(CouponInvalid):
        create_coupon(
            db,
            admin_id(db),
            request(code="AFTERMID", end_date=date(2026, 10, 14)),
            datetime(2026, 10, 14, 16),
        )


@pytest.mark.parametrize("limit", [None, 1, 1_000_000])
def test_create_accepts_optional_usage_limit_boundaries(db: Session, limit: int | None) -> None:
    """A05: each total and per-member limit may be unlimited or within its approved range."""
    row = create_coupon(
        db,
        admin_id(db),
        request(
            code=f"LIMIT{len(str(limit))}",
            total_use_limit=limit,
            per_member_use_limit=limit,
        ),
        NOW,
    )
    assert (row.total_use_limit, row.per_member_use_limit) == (limit, limit)


@pytest.mark.parametrize("field", ["total_use_limit", "per_member_use_limit"])
def test_create_rejects_usage_limits_above_boundary(db: Session, field: str) -> None:
    """A05: neither usage limit may exceed one million."""
    with pytest.raises(CouponInvalid):
        create_coupon(db, admin_id(db), request(**{field: 1_000_001}), NOW)


def test_duplicate_normalized_code_rolls_back_savepoint_without_audit(db: Session) -> None:
    """Coupon 的代码唯一且库内大写；大小写不同也不能新建第二张券。"""
    create_coupon(db, admin_id(db), request(), NOW)
    with pytest.raises(CouponCodeTaken):
        create_coupon(db, admin_id(db), request(code="save10"), NOW)
    audits = db.scalars(select(AuditEvent)).all()
    assert len(audits) == 1
    assert audits[0].action == ADMIN_COUPON_CREATED
    assert len(db.scalars(select(Coupon)).all()) == 1


def test_list_is_newest_first_paginated_ties_by_id_and_converts_utc_cross_day(
    db: Session,
) -> None:
    """A05 列表按创建时间倒序，每页 30 张；日期显示马来西亚日期。"""
    coupon_ids = []
    for number in range(31):
        coupon = create_coupon(
            db,
            admin_id(db),
            request(
                code=f"CODE{number:04}",
                start_date=date(2026, 10, 15),
                end_date=date(2026, 10, 15),
            ),
            NOW + timedelta(seconds=100 if number < 2 else 200 - number),
        )
        coupon_ids.append(coupon.id)
    first = list_coupons(db, 1)
    second = list_coupons(db, 2)
    assert (first.total, len(first.coupons), len(second.coupons)) == (31, 30, 1)
    assert [coupon.id for coupon in first.coupons] == [
        coupon_ids[1],
        coupon_ids[0],
        *coupon_ids[2:30],
    ]
    assert (second.coupons[0].id, second.coupons[0].start_date) == (
        coupon_ids[30],
        date(2026, 10, 15),
    )
    assert list_coupons(db, 3).coupons == []


def test_list_counts_only_reserved_and_used_for_each_coupon(db: Session) -> None:
    """HANDOFF 0.48: reserved and used count, released does not, and coupons remain independent."""
    coupon = create_coupon(db, admin_id(db), request(), NOW)
    other = create_coupon(db, admin_id(db), request(code="OTHER"), NOW)
    add_use(db, coupon.id, 1, USE_RESERVED)
    add_use(db, coupon.id, 2, USE_USED)
    add_use(db, coupon.id, 3, USE_RELEASED)
    add_use(db, other.id, 4, USE_RESERVED)
    counts = {row.id: row.used_count for row in list_coupons(db, 1).coupons}
    assert counts == {coupon.id: 2, other.id: 1}


def test_disable_is_idempotent_and_audited_only_when_state_changes(db: Session) -> None:
    """Kelvin 2026-10-12：只停用、不重启用；重复停用不产生第二条审计。"""
    coupon = create_coupon(db, admin_id(db), request(), NOW)
    disabled = disable_coupon(db, admin_id(db), coupon.id, NOW)
    disable_coupon(db, admin_id(db), coupon.id, NOW)
    audits = list(db.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    assert disabled.is_active is False
    assert [audit.action for audit in audits] == [
        ADMIN_COUPON_CREATED,
        ADMIN_COUPON_DISABLED,
    ]
    assert (audits[-1].target_type, audits[-1].old_value, audits[-1].new_value) == (
        "coupon",
        "active",
        "disabled",
    )
    with pytest.raises(CouponNotFound):
        disable_coupon(db, admin_id(db), 999, NOW)


def test_disable_leaves_reserved_use_unchanged_and_does_not_commit(db: Session) -> None:
    """DESIGN 1.11 and Kelvin 2026-10-12: disable affects new orders only and only flushes."""
    coupon = create_coupon(db, admin_id(db), request(), NOW)
    add_use(db, coupon.id, 1, USE_RESERVED)
    db.commit()
    disabled = disable_coupon(db, admin_id(db), coupon.id, NOW)
    assert db.scalar(select(CouponUse.status)) == USE_RESERVED
    assert disabled.used_count == 1
    db.rollback()
    assert db.get(Coupon, coupon.id).is_active is True

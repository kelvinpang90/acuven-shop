"""后台优惠券规则；每项测试说明所守住的已批准设计或 Kelvin 决定。"""

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AdminAccount, AuditEvent, Coupon
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


def test_create_normalizes_dates_converts_to_utc_and_audits_code_only(db: Session) -> None:
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


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "bad-"},
        {"code": "ABC"},
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


def test_duplicate_normalized_code_rolls_back_savepoint_without_audit(db: Session) -> None:
    """Coupon 的代码唯一且库内大写；大小写不同也不能新建第二张券。"""
    create_coupon(db, admin_id(db), request(), NOW)
    with pytest.raises(CouponCodeTaken):
        create_coupon(db, admin_id(db), request(code="save10"), NOW)
    assert db.scalars(select(AuditEvent)).all()[0].action == ADMIN_COUPON_CREATED
    assert len(db.scalars(select(Coupon)).all()) == 1


def test_list_is_newest_first_paginated_and_converts_utc_cross_day(db: Session) -> None:
    """A05 列表按创建时间倒序，每页 30 张；日期显示马来西亚日期。"""
    for number in range(31):
        create_coupon(
            db,
            admin_id(db),
            request(
                code=f"CODE{number:04}",
                start_date=date(2026, 10, 15),
                end_date=date(2026, 10, 15),
            ),
            NOW.replace(second=number),
        )
    first = list_coupons(db, 1)
    second = list_coupons(db, 2)
    assert (first.total, len(first.coupons), len(second.coupons)) == (31, 30, 1)
    assert first.coupons[0].id > first.coupons[-1].id
    assert second.coupons[0].start_date == date(2026, 10, 15)
    assert list_coupons(db, 3).coupons == []


def test_disable_is_idempotent_and_audited_only_when_state_changes(db: Session) -> None:
    """Kelvin 2026-10-12：只停用、不重启用；重复停用不产生第二条审计。"""
    coupon = create_coupon(db, admin_id(db), request(), NOW)
    disabled = disable_coupon(db, admin_id(db), coupon.id, NOW)
    disable_coupon(db, admin_id(db), coupon.id, NOW)
    actions = list(db.scalars(select(AuditEvent.action)))
    assert disabled.is_active is False
    assert actions == [ADMIN_COUPON_CREATED, ADMIN_COUPON_DISABLED]
    with pytest.raises(CouponNotFound):
        disable_coupon(db, admin_id(db), 999, NOW)

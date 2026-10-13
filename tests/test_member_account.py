"""会员积分概览查询；每项断言守住 DESIGN 1.11 的批次与退款积分显示规则。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.member_account import points_overview
from app.services.points import earn_points, release_points, reserve_points


def _order(db: Session, member_id: int, number: int) -> Order:
    row = Order(
        order_number=f"{number:016d}",
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=100,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=0,
        total_sen=100,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"key-{number}",
        request_fingerprint="0" * 64,
        created_at=datetime(2026, 1, 1),
        payment_expires_at=datetime(2026, 1, 2),
        paid_at=None,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add(row)
    db.flush()
    return row


def test_points_overview_omits_cancelled_redemption_and_reports_balance() -> None:
    """守住 DESIGN 1.11：取消订单的 redeem 与 release 合计为零，不显示为积分明细。"""
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys = ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        member = Member(phone="+60123456789", status=MEMBER_ACTIVE, created_at=datetime(2026, 1, 1))
        db.add(member)
        db.flush()
        earned = _order(db, member.id, 1)
        earn_points(db, member.id, earned.id, 50, datetime(2026, 1, 1))
        cancelled = _order(db, member.id, 2)
        reserve_points(db, member.id, cancelled.id, 20, datetime(2026, 2, 1))
        release_points(db, cancelled.id, datetime(2026, 2, 2))
        result = points_overview(db, member.id, datetime(2026, 2, 2))
    assert result.balance == 50
    assert [row.type for row in result.history] == ["earned"]

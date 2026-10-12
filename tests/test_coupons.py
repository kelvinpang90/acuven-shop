"""优惠券的查券、预占、核销、释放与会员中心的两个只读查询。

依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」第 1、2、4、7 条，
docs/UX.md 0.10 P05 与 P13（Q19），以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定
（两个上限可留空表示不限，预占与核销都计次数，退款不恢复，每会员按会员账号计数）。
每条测试（参数化的测试是每个用例）写明它守住的设计、UX 或决定原句。

用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。SQLite 不支持行锁，
SQLAlchemy 在 SQLite 上不生成 FOR UPDATE：并发预占不超发只在 MySQL 的行锁下成立，这里不能
复现，只验证锁下的判定与写入。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Coupon, CouponUse, Member, Order
from app.models.coupon import (
    TYPE_FIXED,
    TYPE_PERCENT,
    USE_RELEASED,
    USE_RESERVED,
    USE_USED,
)
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.coupons import (
    REASON_BELOW_MIN_SPEND,
    REASON_EXPIRED,
    REASON_INACTIVE,
    REASON_MEMBER_EXHAUSTED,
    REASON_NOT_FOUND,
    REASON_NOT_STARTED,
    REASON_TOTAL_EXHAUSTED,
    REASONS,
    AvailableCoupon,
    CouponCheck,
    UsedCoupon,
    check_coupon,
    is_coupon_valid,
    list_available_coupons,
    list_member_used_coupons,
    normalize_coupon_code,
    release_coupon,
    reserve_coupon,
    use_coupon,
)
from app.services.pricing import OrderLine, price_order

CREATED = datetime(2026, 10, 1, 2, 0)
STARTS = datetime(2026, 9, 30, 16, 0)
# 仍有效的最后一刻：马来西亚时间 10 月 31 日 23:59:59。
ENDS = datetime(2026, 10, 31, 15, 59, 59)
NOW = datetime(2026, 10, 12, 4, 0)
LATER = NOW + timedelta(minutes=5)
FINGERPRINT = "0f" * 32
ONE_SECOND = timedelta(seconds=1)

# 第 4 条算例：A 商品 2 件、各 RM10；B 商品 1 件、RM5。
EXAMPLE_LINES = [OrderLine(unit_price=1000, quantity=2), OrderLine(unit_price=500, quantity=1)]

_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


def _add[T](db: Session, row: T) -> T:
    db.add(row)
    db.flush()
    return row


def _member(db: Session) -> Member:
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    return _add(db, Member(phone=phone, status=MEMBER_ACTIVE, created_at=CREATED))


def _order(db: Session, member_id: int | None, **overrides: Any) -> Order:
    """待支付的会员订单，商品 RM60、券抵 RM10、运费 RM8；每次给不同的订单号与幂等键。"""
    n = next(_ORDER_SEQUENCE)
    values: dict[str, Any] = {
        "order_number": f"{n:016d}",
        "status": STATUS_AWAITING_PAYMENT,
        "subtotal_sen": 6000,
        "coupon_discount_sen": 1000,
        "points_redeemed": 0,
        "shipping_fee_sen": 800,
        "total_sen": 5800,
        "points_earned": 0,
        "shipping_zone_code": "MY-10",
        "shipping_rate_version": 1,
        "idempotency_key": f"order-key-{n}",
        "request_fingerprint": FINGERPRINT,
        "created_at": CREATED,
        "payment_expires_at": CREATED + timedelta(minutes=15),
        "paid_at": None,
        "member_id": member_id,
        "claim_status": CLAIM_NOT_CLAIMABLE,
    }
    return _add(db, Order(**(values | overrides)))


def _coupon(db: Session, **overrides: Any) -> Coupon:
    """固定金额券：减 RM10，商品满 RM50 可用，两个上限都不限。"""
    values: dict[str, Any] = {
        "code": "SAVE10",
        "coupon_type": TYPE_FIXED,
        "amount_off_sen": 1000,
        "percent_off": None,
        "min_subtotal_sen": 5000,
        "starts_at": STARTS,
        "ends_at": ENDS,
        "total_use_limit": None,
        "per_member_use_limit": None,
        "is_active": True,
        "created_at": CREATED,
    }
    return _add(db, Coupon(**(values | overrides)))


def _percent_coupon(db: Session, **overrides: Any) -> Coupon:
    values: dict[str, Any] = {
        "code": "PCT10",
        "coupon_type": TYPE_PERCENT,
        "amount_off_sen": None,
        "percent_off": 10,
        "min_subtotal_sen": 0,
    }
    return _coupon(db, **(values | overrides))


def _use(
    db: Session,
    coupon: Coupon,
    member: Member,
    status: str,
    *,
    created_at: datetime = CREATED,
    discount: int = 1000,
) -> CouponUse:
    """该会员在一张新订单上的一条用券记录。"""
    order = _order(db, member.id)
    return _add(
        db,
        CouponUse(
            coupon_id=coupon.id,
            order_id=order.id,
            member_id=member.id,
            status=status,
            coupon_discount_sen=discount,
            created_at=created_at,
            updated_at=created_at,
        ),
    )


def _reserve(
    db: Session, coupon: Coupon, member: Member, order: Order | None = None
) -> CouponCheck:
    if order is None:
        order = _order(db, member.id)
    return reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)


def _uses(db: Session) -> list[CouponUse]:
    return list(db.scalars(select(CouponUse).order_by(CouponUse.id)).all())


# ---------------------------------------------------------------------------
# 代码规范化与有效期
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SAVE10", "SAVE10"),
        ("save10", "SAVE10"),
        ("Save10", "SAVE10"),
        ("  save10\t\n", "SAVE10"),
        ("ABCD", "ABCD"),
        ("1234", "1234"),
        ("A" * 20, "A" * 20),
        ("ABC", None),
        ("A" * 21, None),
        ("  ABC  ", None),
        ("", None),
        ("    ", None),
        ("SAVE-10", None),
        ("SAVE 10", None),
        ("SAVE_10", None),
        ("SAVEß", None),
        ("ＳＡＶＥ10", None),
        ("SAVE１０", None),
        (None, None),
        (1234, None),
    ],
)
def test_normalize_coupon_code(raw: object, expected: str | None) -> None:
    """UX P05「结账输入代码」与验收「去掉首尾空白后转大写，只由 A 到 Z 与 0 到 9 组成且
    长 4 到 20」：大小写与首尾空白不影响结果；含连字符、中间空格、下划线、非 ASCII 字母（含转
    大写后会变成 ASCII 的「ß」）或全角字符、长度不在 4 到 20 时为 None。
    """
    assert normalize_coupon_code(raw) == expected


def test_check_coupon_accepts_lowercase_code_with_whitespace(db: Session) -> None:
    """UX P05「结账输入代码」：会员输入小写并带首尾空白的代码也能查到同一张券；规范化不通过的
    代码按查无此券处理。
    """
    coupon = _coupon(db)
    member = _member(db)

    assert check_coupon(db, "  save10 ", member.id, 6000, NOW).coupon_id == coupon.id
    assert check_coupon(db, "SAVE-10", member.id, 6000, NOW).reason == REASON_NOT_FOUND


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        (STARTS, None),
        (ENDS, None),
        (STARTS - ONE_SECOND, REASON_NOT_STARTED),
        (ENDS + ONE_SECOND, REASON_EXPIRED),
    ],
    ids=["starts_at", "ends_at", "before_starts", "after_ends"],
)
def test_validity_includes_both_ends(db: Session, now: datetime, reason: str | None) -> None:
    """DESIGN「数据模型」Coupon「有效期」与验收「生效时间不晚于当前时间、当前时间不晚于截止时间
    （两端都含）」：恰在生效时间与截止时间（仍有效的最后一刻）可用，早一秒或晚一秒不可用。
    """
    coupon = _coupon(db)
    member = _member(db)

    assert check_coupon(db, "SAVE10", member.id, 6000, now).reason == reason
    assert is_coupon_valid(coupon, now) is (reason is None)


def test_inactive_coupon_is_not_valid(db: Session) -> None:
    """DESIGN「数据模型」Coupon「启停状态」：停用的券在有效期内也不可用。"""
    coupon = _coupon(db, is_active=False)
    member = _member(db)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_INACTIVE
    assert not is_coupon_valid(coupon, NOW)


# ---------------------------------------------------------------------------
# 查券的原因与顺序
# ---------------------------------------------------------------------------


def _setup_not_found(db: Session) -> tuple[str, Member, int]:
    _coupon(db)
    return "NOPE10", _member(db), 6000


def _setup_inactive(db: Session) -> tuple[str, Member, int]:
    _coupon(db, is_active=False)
    return "SAVE10", _member(db), 6000


def _setup_not_started(db: Session) -> tuple[str, Member, int]:
    _coupon(db, starts_at=NOW + ONE_SECOND)
    return "SAVE10", _member(db), 6000


def _setup_expired(db: Session) -> tuple[str, Member, int]:
    _coupon(db, ends_at=NOW - ONE_SECOND)
    return "SAVE10", _member(db), 6000


def _setup_below_min_spend(db: Session) -> tuple[str, Member, int]:
    _coupon(db)
    return "SAVE10", _member(db), 4999


def _setup_total_exhausted(db: Session) -> tuple[str, Member, int]:
    coupon = _coupon(db, total_use_limit=1)
    _use(db, coupon, _member(db), USE_RESERVED)
    return "SAVE10", _member(db), 6000


def _setup_member_exhausted(db: Session) -> tuple[str, Member, int]:
    coupon = _coupon(db, per_member_use_limit=1)
    member = _member(db)
    _use(db, coupon, member, USE_USED)
    return "SAVE10", member, 6000


@pytest.mark.parametrize(
    ("design", "setup", "reason"),
    [
        ("UX P05「结账输入代码」：没有这个代码的券", _setup_not_found, REASON_NOT_FOUND),
        ("DESIGN Coupon「启停状态」：券已停用", _setup_inactive, REASON_INACTIVE),
        ("DESIGN Coupon「有效期」：生效时间晚于当前", _setup_not_started, REASON_NOT_STARTED),
        ("DESIGN Coupon「有效期」：截止时间早于当前", _setup_expired, REASON_EXPIRED),
        (
            "DESIGN Coupon「最低商品消费」：商品小计少 1 仙",
            _setup_below_min_spend,
            REASON_BELOW_MIN_SPEND,
        ),
        (
            "DESIGN Coupon「总使用上限」：另一会员的预占已用满",
            _setup_total_exhausted,
            REASON_TOTAL_EXHAUSTED,
        ),
        (
            "Kelvin 2026-10-10「每会员按会员账号计数」：本会员的核销已用满",
            _setup_member_exhausted,
            REASON_MEMBER_EXHAUSTED,
        ),
    ],
    ids=list(REASONS),
)
def test_each_reason(
    db: Session,
    design: str,
    setup: Callable[[Session], tuple[str, Member, int]],
    reason: str,
) -> None:
    """七种原因各一例；守住的设计、UX 或决定原句见每个用例的 design。不可用时不给券参数。"""
    assert design
    code, member, subtotal = setup(db)

    result = check_coupon(db, code, member.id, subtotal, NOW)

    assert result.reason == reason
    assert not result.ok
    assert result.coupon_id is None
    assert result.price_kwargs() == {}


@pytest.mark.parametrize(
    ("overrides", "subtotal", "reason"),
    [
        ({"is_active": False, "ends_at": NOW - ONE_SECOND}, 4999, REASON_INACTIVE),
        ({"starts_at": NOW + ONE_SECOND}, 4999, REASON_NOT_STARTED),
        ({"ends_at": NOW - ONE_SECOND}, 4999, REASON_EXPIRED),
        ({"total_use_limit": 1}, 4999, REASON_BELOW_MIN_SPEND),
        ({"total_use_limit": 1, "per_member_use_limit": 1}, 6000, REASON_TOTAL_EXHAUSTED),
    ],
    ids=["inactive", "not_started", "expired", "below_min_spend", "total_exhausted"],
)
def test_first_failing_reason_is_reported(
    db: Session, overrides: dict[str, Any], subtotal: int, reason: str
) -> None:
    """验收「按顺序判定」：not_found、inactive、not_started、expired、below_min_spend、
    total_exhausted、member_exhausted，几条同时不成立时报前面的一条。
    """
    coupon = _coupon(db, **overrides)
    member = _member(db)
    _use(db, coupon, member, USE_USED)

    assert check_coupon(db, "SAVE10", member.id, subtotal, NOW).reason == reason


def test_min_spend_exactly_reached_is_usable(db: Session) -> None:
    """DESIGN Coupon「最低商品消费」：商品小计恰好等于最低商品消费即可用，少 1 仙不可用。"""
    _coupon(db, min_subtotal_sen=5000)
    member = _member(db)

    assert check_coupon(db, "SAVE10", member.id, 5000, NOW).ok
    assert check_coupon(db, "SAVE10", member.id, 4999, NOW).reason == REASON_BELOW_MIN_SPEND


def test_check_coupon_does_not_write(db: Session) -> None:
    """验收「只读，不写库」：查券（可用与不可用）后没有新的用券记录，会话里没有待写的改动。"""
    _coupon(db, per_member_use_limit=1)
    member = _member(db)
    db.commit()

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok
    assert check_coupon(db, "SAVE10", member.id, 10, NOW).reason == REASON_BELOW_MIN_SPEND

    assert not db.new and not db.dirty and not db.deleted
    assert _uses(db) == []


# ---------------------------------------------------------------------------
# 计数口径与两个上限
# ---------------------------------------------------------------------------


def test_total_limit_allows_last_reservation_then_rejects(db: Session) -> None:
    """DESIGN「使用额度在下单时预占」与 Kelvin 2026-10-10「预占与核销都计入次数」：总上限 3、
    已有一条预占与一条核销时还能预占最后一次；之后查券与预占都为 total_exhausted，不写记录。
    """
    coupon = _coupon(db, total_use_limit=3)
    _use(db, coupon, _member(db), USE_RESERVED)
    _use(db, coupon, _member(db), USE_USED)
    member = _member(db)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok
    assert _reserve(db, coupon, member).ok

    late = _member(db)
    assert check_coupon(db, "SAVE10", late.id, 6000, NOW).reason == REASON_TOTAL_EXHAUSTED
    assert _reserve(db, coupon, late).reason == REASON_TOTAL_EXHAUSTED
    assert len(_uses(db)) == 3


def test_member_limit_allows_last_reservation_then_rejects(db: Session) -> None:
    """Kelvin 2026-10-10「每会员按会员账号计数」：每会员上限 2、本会员已核销一次时还能预占最后
    一次；之后本会员查券与预占都为 member_exhausted，不写记录；另一会员不受影响。
    """
    coupon = _coupon(db, per_member_use_limit=2)
    member = _member(db)
    _use(db, coupon, member, USE_USED)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok
    assert _reserve(db, coupon, member).ok

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_MEMBER_EXHAUSTED
    assert _reserve(db, coupon, member).reason == REASON_MEMBER_EXHAUSTED
    assert len(_uses(db)) == 2
    assert check_coupon(db, "SAVE10", _member(db).id, 6000, NOW).ok


@pytest.mark.parametrize("limit", ["total_use_limit", "per_member_use_limit"])
def test_released_uses_are_not_counted(db: Session, limit: str) -> None:
    """Kelvin 2026-10-10「取消或支付超时释放后不计」：上限 1、本会员已有两条 released 记录时
    仍可用。
    """
    coupon = _coupon(db, **{limit: 1})
    member = _member(db)
    _use(db, coupon, member, USE_RELEASED)
    _use(db, coupon, member, USE_RELEASED)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok


@pytest.mark.parametrize(
    ("limit", "status", "reason"),
    [
        ("total_use_limit", USE_RESERVED, REASON_TOTAL_EXHAUSTED),
        ("total_use_limit", USE_USED, REASON_TOTAL_EXHAUSTED),
        ("per_member_use_limit", USE_RESERVED, REASON_MEMBER_EXHAUSTED),
        ("per_member_use_limit", USE_USED, REASON_MEMBER_EXHAUSTED),
    ],
)
def test_reserved_and_used_uses_are_counted(
    db: Session, limit: str, status: str, reason: str
) -> None:
    """Kelvin 2026-10-10「待支付订单的预占与已支付订单的核销都计入次数……退款不恢复次数」：
    上限 1 时，一条 reserved 或 used 记录就用满（退款不改 used，照样计入）。
    """
    coupon = _coupon(db, **{limit: 1})
    member = _member(db)
    _use(db, coupon, member, status)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == reason


def test_empty_limits_mean_unlimited(db: Session) -> None:
    """Kelvin 2026-10-10「两个上限都可留空表示不限」：两个上限为空时，同一会员已有 5 条预占与
    核销记录、另有其他会员的记录，仍可查到、可预占。
    """
    coupon = _coupon(db, total_use_limit=None, per_member_use_limit=None)
    member = _member(db)
    for status in (USE_RESERVED, USE_USED, USE_USED, USE_RESERVED, USE_USED):
        _use(db, coupon, member, status)
    _use(db, coupon, _member(db), USE_USED)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok
    assert _reserve(db, coupon, member).ok


def test_other_members_uses_do_not_count_for_this_member(db: Session) -> None:
    """Kelvin 2026-10-10「每会员按会员账号计数」：每会员上限 1 时，另一会员的核销不计入本会员，
    另一张券的使用也不计入；对照总上限 3：各会员的记录都计入总次数。
    """
    coupon = _coupon(db, per_member_use_limit=1, total_use_limit=3)
    other = _member(db)
    _use(db, coupon, other, USE_USED)
    _use(db, _coupon(db, code="OTHER10"), _member(db), USE_USED)
    member = _member(db)

    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok
    assert check_coupon(db, "SAVE10", other.id, 6000, NOW).reason == REASON_MEMBER_EXHAUSTED

    _use(db, coupon, _member(db), USE_RESERVED)
    _use(db, coupon, _member(db), USE_RESERVED)
    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_TOTAL_EXHAUSTED


# ---------------------------------------------------------------------------
# 给 price_order 的券参数
# ---------------------------------------------------------------------------


def test_fixed_coupon_gives_fixed_off_for_price_order(db: Session) -> None:
    """DESIGN 第 2 条「固定金额券取券额与商品小计较小者」：fixed 券给出 fixed_off 的仙数，交给
    price_order 的结果与直接以同一 fixed_off 计价一致；券额大于商品小计时由 price_order
    取小者。
    """
    coupon = _coupon(db, min_subtotal_sen=0)
    member = _member(db)

    result = check_coupon(db, "SAVE10", member.id, 2500, NOW)

    assert result.coupon_id == coupon.id
    assert result.fixed_off == 1000 and type(result.fixed_off) is int
    assert result.percent_off is None
    assert result.price_kwargs() == {"fixed_off": 1000}
    priced = price_order(EXAMPLE_LINES, **result.price_kwargs())
    assert priced == price_order(EXAMPLE_LINES, fixed_off=1000)
    assert priced.coupon_discount == 1000

    small = [OrderLine(unit_price=300, quantity=1)]
    assert price_order(small, **result.price_kwargs()).coupon_discount == 300


def test_percent_coupon_reproduces_design_worked_example(db: Session) -> None:
    """DESIGN 第 4 条「整单 10% 券减 RM2.50，分摊到 A 每件 RM1、B RM0.50；使用 300 积分抵 RM3，
    ……三件现金实付依次 RM7.80、RM7.80、RM3.90」：10% 券给出 Decimal(10)，交给 price_order 得到
    与 SHOP-TASK-002 相同的计价结果。
    """
    coupon = _percent_coupon(db)
    member = _member(db)

    result = check_coupon(db, "PCT10", member.id, 2500, NOW)

    assert result.coupon_id == coupon.id
    assert result.fixed_off is None
    assert result.percent_off == Decimal(10) and isinstance(result.percent_off, Decimal)
    priced = price_order(EXAMPLE_LINES, points_redeemed=300, **result.price_kwargs())
    assert priced == price_order(EXAMPLE_LINES, percent_off=Decimal(10), points_redeemed=300)
    assert priced.coupon_discount == 250
    assert [unit.coupon_discount for unit in priced.units] == [100, 100, 50]
    assert [unit.cash_paid for unit in priced.units] == [780, 780, 390]
    assert priced.points_earned == 19


# ---------------------------------------------------------------------------
# 预占
# ---------------------------------------------------------------------------


def test_reserve_writes_reserved_use_and_count_increases(db: Session) -> None:
    """DESIGN 第 1 条「使用额度在下单时预占」与第 7 条「已有订单……按快照计算」：预占写一条
    reserved 记录，存本单券折扣；之后再查券，该会员的次数已加一（每会员上限 1 即用满）。
    """
    coupon = _coupon(db, per_member_use_limit=1)
    member = _member(db)
    order = _order(db, member.id)
    checked = check_coupon(db, "SAVE10", member.id, 6000, NOW)

    result = reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)

    assert result == checked
    [use] = _uses(db)
    assert (use.coupon_id, use.order_id, use.member_id) == (coupon.id, order.id, member.id)
    assert use.status == USE_RESERVED
    assert use.coupon_discount_sen == 1000
    assert use.created_at == NOW and use.updated_at == NOW
    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_MEMBER_EXHAUSTED


@pytest.mark.parametrize(
    ("overrides", "subtotal", "reason"),
    [
        ({"is_active": False}, 6000, REASON_INACTIVE),
        ({"ends_at": NOW - ONE_SECOND}, 6000, REASON_EXPIRED),
        ({}, 4999, REASON_BELOW_MIN_SPEND),
    ],
    ids=["inactive", "expired", "below_min_spend"],
)
def test_reserve_rechecks_all_conditions(
    db: Session, overrides: dict[str, Any], subtotal: int, reason: str
) -> None:
    """验收「再按查券的全部条件重新判定……不通过时返回原因且不写记录」：停用、过期、未达最低
    商品消费的券预占时返回原因，不写用券记录。
    """
    coupon = _coupon(db, **overrides)
    member = _member(db)
    order = _order(db, member.id)

    result = reserve_coupon(db, coupon.id, order.id, member.id, 1000, subtotal, NOW)

    assert result.reason == reason
    assert _uses(db) == []


def test_reserve_unknown_coupon_id_is_not_found(db: Session) -> None:
    """UX P05「结账输入代码」：券在查券之后不存在时，预占按查无此券回答，不写记录。"""
    member = _member(db)
    order = _order(db, member.id)

    assert reserve_coupon(db, 999, order.id, member.id, 0, 6000, NOW).reason == REASON_NOT_FOUND
    assert _uses(db) == []


def test_reserve_rereads_coupon_instead_of_session_cache(db: Session) -> None:
    """验收「券本身也以加锁读重新载入（不沿用……ORM 缓存）」与 DESIGN 第 7 条「后台修改只影响
    新订单」：会话里已载入的券被另一条语句停用后，预占读到的是停用后的状态。
    """
    coupon = _coupon(db)
    member = _member(db)
    order = _order(db, member.id)
    db.execute(
        update(Coupon)
        .where(Coupon.id == coupon.id)
        .values(is_active=False)
        .execution_options(synchronize_session=False)
    )
    assert coupon.is_active

    result = reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)

    assert result.reason == REASON_INACTIVE
    assert _uses(db) == []


def test_reserve_twice_for_same_order_raises(db: Session) -> None:
    """DESIGN「使用记录与订单关联」（一单至多一张券）：同一订单已有用券记录时再预占抛
    ValueError，换一张券、或已有的记录已释放也一样，不写第二条。
    """
    coupon = _coupon(db)
    other = _percent_coupon(db)
    member = _member(db)
    order = _order(db, member.id)
    assert reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW).ok

    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)
    with pytest.raises(ValueError):
        reserve_coupon(db, other.id, order.id, member.id, 600, 6000, NOW)
    assert release_coupon(db, order.id, LATER)
    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)
    assert len(_uses(db)) == 1


def test_reserve_for_deleted_member_raises(db: Session) -> None:
    """DESIGN 第 1 条「优惠券仅会员可用」：会员已注销（状态不是 active）时预占抛 ValueError，
    不写记录；会员不存在时同样。
    """
    coupon = _coupon(db)
    member = _member(db)
    order = _order(db, member.id)
    member.status = MEMBER_DELETED
    member.phone = None
    member.deleted_at = NOW
    db.flush()

    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)
    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, order.id, 999, 1000, 6000, NOW)
    assert _uses(db) == []


@pytest.mark.parametrize("owner", ["other_member", "guest"])
def test_reserve_for_order_of_someone_else_raises(db: Session, owner: str) -> None:
    """DESIGN 第 1 条「优惠券仅会员可用」与 Kelvin 2026-10-10「每会员按会员账号计数」：订单的
    member_id 不等于该会员（属于另一会员或是游客订单）时预占抛 ValueError，不写记录；订单
    不存在时同样。
    """
    coupon = _coupon(db)
    member = _member(db)
    if owner == "other_member":
        order = _order(db, _member(db).id)
    else:
        order = _order(db, None, claim_status="open")

    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, order.id, member.id, 1000, 6000, NOW)
    with pytest.raises(ValueError):
        reserve_coupon(db, coupon.id, 999, member.id, 1000, 6000, NOW)
    assert _uses(db) == []


# ---------------------------------------------------------------------------
# 核销与释放
# ---------------------------------------------------------------------------


def test_use_marks_reserved_as_used_once(db: Session) -> None:
    """DESIGN 第 1 条与 Kelvin 2026-10-10「已支付订单的核销……计入次数，退款不恢复次数」：
    支付成功把 reserved 改为 used 并写更新时间；重复核销为假，已核销的不能再释放，状态不变。
    """
    coupon = _coupon(db, per_member_use_limit=1)
    member = _member(db)
    order = _order(db, member.id)
    _reserve(db, coupon, member, order)

    assert use_coupon(db, order.id, LATER) is True
    [use] = _uses(db)
    assert use.status == USE_USED
    assert use.updated_at == LATER and use.created_at == NOW

    assert use_coupon(db, order.id, LATER + ONE_SECOND) is False
    assert release_coupon(db, order.id, LATER + ONE_SECOND) is False
    db.expire_all()
    [use] = _uses(db)
    assert use.status == USE_USED
    assert use.updated_at == LATER
    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_MEMBER_EXHAUSTED


def test_release_marks_reserved_as_released_once(db: Session) -> None:
    """DESIGN 第 1 条「取消或支付超时释放」：把 reserved 改为 released 并写更新时间，释放后不再
    计次；重复释放为假，已释放的不能再核销，状态不变。
    """
    coupon = _coupon(db, per_member_use_limit=1)
    member = _member(db)
    order = _order(db, member.id)
    _reserve(db, coupon, member, order)
    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).reason == REASON_MEMBER_EXHAUSTED

    assert release_coupon(db, order.id, LATER) is True
    [use] = _uses(db)
    assert use.status == USE_RELEASED
    assert use.updated_at == LATER

    assert release_coupon(db, order.id, LATER + ONE_SECOND) is False
    assert use_coupon(db, order.id, LATER + ONE_SECOND) is False
    db.expire_all()
    [use] = _uses(db)
    assert use.status == USE_RELEASED
    assert use.updated_at == LATER
    assert check_coupon(db, "SAVE10", member.id, 6000, NOW).ok


def test_use_and_release_without_record_return_false(db: Session) -> None:
    """DESIGN 第 1 条「取消或支付超时释放」：没有用券记录的订单、不存在的订单，
    核销与释放都为假、不出错，也不动其他订单的记录。
    """
    coupon = _coupon(db)
    member = _member(db)
    reserved_order = _order(db, member.id)
    _reserve(db, coupon, member, reserved_order)
    plain = _order(db, member.id)

    assert use_coupon(db, plain.id, LATER) is False
    assert release_coupon(db, plain.id, LATER) is False
    assert release_coupon(db, 999, LATER) is False
    [use] = _uses(db)
    assert use.status == USE_RESERVED


# ---------------------------------------------------------------------------
# 会员中心「我的优惠券」
# ---------------------------------------------------------------------------


def test_available_coupons_lists_all_valid_coupons_in_order(db: Session) -> None:
    """UX P13 与 Q19「可用券列出所有启用中且在有效期内的券，对全部会员相同，不按每会员或总次数
    上限过滤」：已用满的券照列，停用、已过期与未生效的券不列；按截止时间升序、同时刻按 ID。
    """
    late = _coupon(db, code="LATE10")
    early = _percent_coupon(db, code="EARLY5", percent_off=5, ends_at=NOW + timedelta(days=1))
    full = _coupon(db, code="FULL10", total_use_limit=1, per_member_use_limit=1)
    _use(db, full, _member(db), USE_USED)
    _coupon(db, code="OFF10", is_active=False)
    _coupon(db, code="OLD10", ends_at=NOW - ONE_SECOND)
    _coupon(db, code="SOON10", starts_at=NOW + ONE_SECOND)
    edge = _coupon(db, code="EDGE10", ends_at=NOW)

    listed = list_available_coupons(db, NOW)

    assert [item.code for item in listed] == [edge.code, early.code, late.code, full.code]
    assert listed[1] == AvailableCoupon(
        code="EARLY5",
        coupon_type=TYPE_PERCENT,
        amount_off_sen=None,
        percent_off=5,
        min_subtotal_sen=0,
        ends_at=NOW + timedelta(days=1),
    )
    assert listed[2] == AvailableCoupon(
        code="LATE10",
        coupon_type=TYPE_FIXED,
        amount_off_sen=1000,
        percent_off=None,
        min_subtotal_sen=5000,
        ends_at=ENDS,
    )
    assert late.id < full.id


def test_member_used_coupons_lists_only_used_newest_first(db: Session) -> None:
    """UX P13「本人使用记录」（`account.coupons_used`）：只列该会员 used 的记录（不含预占中与
    已释放的，也不含其他会员的），按创建时间降序、同时刻按 ID 降序，给出代码、订单号与
    本单券折扣。
    """
    fixed = _coupon(db)
    percent = _percent_coupon(db)
    member = _member(db)
    first = _use(db, fixed, member, USE_USED, created_at=CREATED, discount=1000)
    same_time = _use(db, percent, member, USE_USED, created_at=CREATED, discount=250)
    newest = _use(db, fixed, member, USE_USED, created_at=NOW, discount=800)
    _use(db, fixed, member, USE_RESERVED, created_at=LATER)
    _use(db, percent, member, USE_RELEASED, created_at=LATER)
    _use(db, fixed, _member(db), USE_USED, created_at=LATER)

    def number(use: CouponUse) -> str:
        order = db.get(Order, use.order_id)
        assert order is not None
        return order.order_number

    assert list_member_used_coupons(db, member.id) == [
        UsedCoupon(code="SAVE10", order_number=number(newest), coupon_discount_sen=800),
        UsedCoupon(code="PCT10", order_number=number(same_time), coupon_discount_sen=250),
        UsedCoupon(code="SAVE10", order_number=number(first), coupon_discount_sen=1000),
    ]
    assert list_member_used_coupons(db, _member(db).id) == []

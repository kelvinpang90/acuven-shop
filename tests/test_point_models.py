"""积分批次与积分流水两张表的数据库约束。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 PointLedger 一行（获得、抵扣、到期、
退款追回、待抵扣积分的不可覆盖流水；每笔关联订单或退款，余额不得小于零）与「计价、优惠、积分
与库存」第 3、5 条，「权限与资料保护」第 3 条（注销时积分作废），以及 docs/HANDOFF.md 0.46
记录的 Kelvin 2026-10-10 决定（积分按批次记账，每单获得的积分为一个批次，先用先到期的批次，
退款按抵扣的相反顺序返还；批次在取得日加 12 个自然月那天结束时到期；预占以预占时为准）。
每条测试（参数化的测试是每个用例）写明它守住的设计或决定原句。

先写入合法的批次与九类流水各一条（含成对的 debt_repay）作对照，再逐个写反例。检查约束的反例
先在独立的 SQLite 连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，再断言写入被拒、
且报出的是不成立的约束之一。非空约束的反例用 Core insert 显式写入 NULL：ORM 会略过值为 None
的列、改用默认值。用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
退款申请用 requested 状态：库不检查流水所指申请的状态，只在批准时写流水由规则任务保证。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, Order, PointEntry, PointLot, RefundRequest
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_MEMBER,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
)
from app.models.points import (
    ENTRY_KINDS,
    KIND_DEBT_ADD,
    KIND_DEBT_REPAY,
    KIND_EARN,
    KIND_EXPIRE,
    KIND_FORFEIT,
    KIND_REDEEM,
    KIND_REFUND_RETURN,
    KIND_REFUND_REVERSAL,
    KIND_RELEASE,
    LOT_KINDS,
    NEGATIVE_KINDS,
    NON_REFUND_KINDS,
    POSITIVE_KINDS,
    REFUND_KINDS,
)
from app.models.refund import REFUND_REQUESTED

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 12, 2, 0)
# 马来西亚时间 2026-10-12 10:00 取得，2027-10-12 23:59:59（马来西亚时间）仍有效，换算成 UTC。
ACQUIRED = datetime(2026, 10, 12, 2, 0)
EXPIRES = datetime(2027, 10, 12, 15, 59, 59)
FINGERPRINT = "0f" * 32

# 只用来对单行求值检查约束表达式，不建任何表。
_EVAL_ENGINE = create_engine("sqlite://")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield db
    engine.dispose()


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)
_REFUND_SEQUENCE = itertools.count(1)


def _member(session: Session) -> Member:
    """每次给不同的手机号，同一测试里可建多个会员。"""
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    return _add(session, Member(phone=phone, status=MEMBER_ACTIVE, created_at=CREATED))


def _order(session: Session, member_id: int | None, **overrides: Any) -> Order:
    """会员订单，商品 RM60、运费 RM8；每次给不同的订单号与幂等键。"""
    n = next(_ORDER_SEQUENCE)
    values: dict[str, Any] = {
        "order_number": f"{n:016d}",
        "status": STATUS_AWAITING_PAYMENT,
        "subtotal_sen": 6000,
        "coupon_discount_sen": 0,
        "points_redeemed": 0,
        "shipping_fee_sen": 800,
        "total_sen": 6800,
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
    return _add(session, Order(**(values | overrides)))


def _refund(session: Session, order_id: int) -> RefundRequest:
    n = next(_REFUND_SEQUENCE)
    return _add(
        session,
        RefundRequest(
            order_id=order_id,
            status=REFUND_REQUESTED,
            actor_type=ACTOR_MEMBER,
            idempotency_key=f"refund-key-{n}",
            request_fingerprint=FINGERPRINT,
            amount_sen=1000,
            created_at=CREATED,
        ),
    )


def _lot_values(member_id: int, order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "member_id": member_id,
        "order_id": order_id,
        "points": 100,
        "acquired_at": ACQUIRED,
        "expires_at": EXPIRES,
        "created_at": CREATED,
    }
    return values | overrides


def _lot(session: Session, member_id: int, order_id: int, **overrides: Any) -> PointLot:
    return _add(session, PointLot(**_lot_values(member_id, order_id, **overrides)))


@dataclass
class Ledger:
    """一个会员的最小账本：来源订单与其批次、所用（并被退款的）订单与其退款申请。"""

    member_id: int
    source_order_id: int
    used_order_id: int
    lot_id: int
    refund_request_id: int


def _ledger(session: Session) -> Ledger:
    member = _member(session)
    source = _order(session, member.id)
    used = _order(session, member.id)
    lot = _lot(session, member.id, source.id)
    refund = _refund(session, used.id)
    return Ledger(member.id, source.id, used.id, lot.id, refund.id)


# 到期、作废、获得与偿还记批次的来源订单；抵扣、释放记所用订单；退款类记被退款的订单。
_SOURCE_ORDER_KINDS = (KIND_EARN, KIND_EXPIRE, KIND_FORFEIT, KIND_DEBT_REPAY)


def _entry_values(ledger: Ledger, kind: str, **overrides: Any) -> dict[str, Any]:
    """该类别一条合法的流水；debt_repay 取批次非空的一条。"""
    values: dict[str, Any] = {
        "member_id": ledger.member_id,
        "lot_id": None if kind == KIND_DEBT_ADD else ledger.lot_id,
        "kind": kind,
        "points": 10 if kind in POSITIVE_KINDS else -10,
        "order_id": (
            ledger.source_order_id if kind in _SOURCE_ORDER_KINDS else ledger.used_order_id
        ),
        "refund_request_id": ledger.refund_request_id if kind in REFUND_KINDS else None,
        "created_at": CREATED,
    }
    return values | overrides


def _check_constraints(model: type[Base]) -> list[CheckConstraint]:
    return [c for c in model.__table__.constraints if isinstance(c, CheckConstraint)]


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints(model):
            sql = text(f"SELECT ({constraint.sqltext}) FROM (SELECT {columns})")
            if connection.execute(sql, params).scalar_one() == 0:
                failing.append(str(constraint.name))
    return failing


def _assert_check_rejects(
    session: Session, model: type[Base], constraint: str, values: dict[str, Any]
) -> None:
    failing = _failing_checks(model, values)
    assert constraint in failing
    pattern = r"CHECK constraint failed: (?:" + "|".join(failing) + r")\b"

    with pytest.raises(IntegrityError, match=pattern):
        _add(session, model(**values))


def _assert_null_rejected(
    session: Session, model: type[Base], column: str, values: dict[str, Any]
) -> None:
    table = model.__table__.name
    message = f"NOT NULL constraint failed: {table}.{column}"

    with pytest.raises(IntegrityError, match=message):
        session.execute(insert(model).values(**(values | {column: None})))


def _lot_sum(session: Session, lot_id: int) -> int:
    return session.scalar(
        select(func.coalesce(func.sum(PointEntry.points), 0)).where(PointEntry.lot_id == lot_id)
    )


def _debt_sum(session: Session, member_id: int) -> int:
    return session.scalar(
        select(func.coalesce(func.sum(PointEntry.points), 0)).where(
            PointEntry.member_id == member_id, PointEntry.lot_id.is_(None)
        )
    )


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_lots_and_all_entry_kinds_are_accepted(session: Session) -> None:
    """PointLedger「获得、抵扣、到期、退款追回、待抵扣积分的不可覆盖流水；每笔关联订单或退款」、
    第 5 条「积分余额不足时扣至 0，缺口记待抵扣」、「以后获得积分先偿还」、「权限与资料保护」
    第 3 条注销作废，与 Kelvin 2026-10-10「每单获得的积分为一个批次」「预占以预占时为准」：
    两个批次与九类流水各至少一条（含成对的 debt_repay）都写得进去；按模块说明求和，批次非空的
    流水合计为批次剩余、批次为空的流水合计为仍欠的积分，这个例子里最后都回到 0。这是下面各条
    拒绝测试的对照：同样的建表与外键检查下，合法数据写得进去，且求值辅助函数对每类都不报任何
    约束不成立。
    """
    member = _member(session)
    order_a = _order(session, member.id)
    order_b = _order(session, member.id)
    order_c = _order(session, member.id)
    order_d = _order(session, member.id)
    lot_a = _lot(session, member.id, order_a.id, points=100)
    # 2028-02-29 取得、次年没有 2 月 29 日，取 2029-02-28 23:59:59（马来西亚时间）。
    lot_d = _lot(
        session,
        member.id,
        order_d.id,
        points=50,
        acquired_at=datetime(2028, 2, 29, 2, 0),
        expires_at=datetime(2029, 2, 28, 15, 59, 59),
    )
    refund_a = _refund(session, order_a.id)
    refund_b = _refund(session, order_b.id)

    def entry(
        kind: str, points: int, order_id: int, lot_id: int | None, refund_id: int | None = None
    ) -> None:
        _add(
            session,
            PointEntry(
                member_id=member.id,
                lot_id=lot_id,
                kind=kind,
                points=points,
                order_id=order_id,
                refund_request_id=refund_id,
                created_at=CREATED,
            ),
        )

    entry(KIND_EARN, 100, order_a.id, lot_a.id)
    entry(KIND_REDEEM, -60, order_b.id, lot_a.id)
    entry(KIND_REDEEM, -10, order_c.id, lot_a.id)
    entry(KIND_RELEASE, 10, order_c.id, lot_a.id)
    # 退 A 要追回 60，批次只剩 40：扣至 0，缺口 20 记待抵扣。
    entry(KIND_REFUND_REVERSAL, -40, order_a.id, lot_a.id, refund_a.id)
    entry(KIND_DEBT_ADD, 20, order_a.id, None, refund_a.id)
    # 退 B，原抵扣的 60 积分所在批次未过期，返还。
    entry(KIND_REFUND_RETURN, 60, order_b.id, lot_a.id, refund_b.id)
    entry(KIND_EXPIRE, -60, order_a.id, lot_a.id)
    # D 获得 50 积分，先偿还 20：成对写入。
    entry(KIND_EARN, 50, order_d.id, lot_d.id)
    entry(KIND_DEBT_REPAY, -20, order_d.id, lot_d.id)
    entry(KIND_DEBT_REPAY, -20, order_d.id, None)
    entry(KIND_FORFEIT, -30, order_d.id, lot_d.id)

    kinds = set(session.scalars(select(PointEntry.kind)).all())
    assert kinds == set(ENTRY_KINDS)
    assert _lot_sum(session, lot_a.id) == 0
    assert _lot_sum(session, lot_d.id) == 0
    assert _debt_sum(session, member.id) == 0

    ledger = Ledger(member.id, order_a.id, order_b.id, lot_a.id, refund_b.id)
    assert _failing_checks(PointLot, _lot_values(member.id, order_a.id)) == []
    for kind in ENTRY_KINDS:
        assert _failing_checks(PointEntry, _entry_values(ledger, kind)) == []
    unlotted_repay = _entry_values(ledger, KIND_DEBT_REPAY, lot_id=None)
    assert _failing_checks(PointEntry, unlotted_repay) == []


def test_points_are_integers(session: Session) -> None:
    """「积分数量一律称『积分』，不把仙与积分混称」与验收「积分一律整数」：批次的获得积分与
    流水的积分变动读回仍是 int，不经浮点。
    """
    ledger = _ledger(session)
    entry = _add(session, PointEntry(**_entry_values(ledger, KIND_EARN)))
    session.expire_all()

    lot = session.get(PointLot, ledger.lot_id)
    reloaded = session.get(PointEntry, entry.id)
    assert lot is not None and reloaded is not None
    assert type(lot.points) is int
    assert type(reloaded.points) is int


# ---------------------------------------------------------------------------
# 积分批次
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "Kelvin 2026-10-10「每单获得的积分为一个批次」：获得积分为 0 不成批次",
            "ck_point_lots_points_positive",
            {"points": 0},
        ),
        (
            "Kelvin 2026-10-10「每单获得的积分为一个批次」：获得积分为负",
            "ck_point_lots_points_positive",
            {"points": -5},
        ),
        (
            "Kelvin 2026-10-10「取得日加 12 个自然月那一天的 23:59:59 到期」：到期等于取得",
            "ck_point_lots_expires_after_acquired",
            {"expires_at": ACQUIRED},
        ),
        (
            "Kelvin 2026-10-10「取得日加 12 个自然月那一天的 23:59:59 到期」：到期早于取得",
            "ck_point_lots_expires_after_acquired",
            {"expires_at": ACQUIRED - timedelta(seconds=1)},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_lot_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """积分批次表每个检查约束至少一个反例；守住的设计或决定原句见每个用例的 design。"""
    assert design
    member = _member(session)
    values = _lot_values(member.id, _order(session, member.id).id, **overrides)
    _assert_check_rejects(session, PointLot, constraint, values)


@pytest.mark.parametrize(
    "column",
    ["member_id", "order_id", "points", "acquired_at", "expires_at", "created_at"],
)
def test_lot_required_columns_reject_null(session: Session, column: str) -> None:
    """Kelvin 2026-10-10「每单获得的积分为一个批次」「批次在取得日加 12 个自然月……到期」：
    批次必须属于一个会员与一张来源订单，有获得积分、取得、到期与创建时间；没有可空的列。
    """
    member = _member(session)
    values = _lot_values(member.id, _order(session, member.id).id)
    _assert_null_rejected(session, PointLot, column, values)


def test_second_lot_for_same_order_is_rejected(session: Session) -> None:
    """Kelvin 2026-10-10「每单获得的积分为一个批次」：同一订单不能有第二个批次。"""
    ledger = _ledger(session)
    message = "UNIQUE constraint failed: point_lots.order_id"

    with pytest.raises(IntegrityError, match=message):
        _lot(session, ledger.member_id, ledger.source_order_id, points=5)


def test_only_lot_has_no_nullable_columns() -> None:
    """验收「除下面写明可空的列外全部非空」：批次没有可空的列。"""
    assert [column.name for column in PointLot.__table__.columns if column.nullable] == []


def test_lot_indexes_and_composite_target() -> None:
    """Kelvin 2026-10-10「抵扣先用先到期的批次」：按（会员、到期时间）建普通索引供之后按到期
    先后取批次与到期处理；（id、会员）唯一作为流水复合外键的目标。
    """
    indexes = {index.name: index for index in PointLot.__table__.indexes}
    uniques = sorted(
        [column.name for column in c.columns]
        for c in PointLot.__table__.constraints
        if isinstance(c, UniqueConstraint)
    )

    by_expiry = indexes["ix_point_lots_member_id_expires_at"]
    assert [column.name for column in by_expiry.columns] == ["member_id", "expires_at"]
    assert not by_expiry.unique
    assert set(indexes) == {by_expiry.name}
    assert uniques == [["id", "member_id"], ["order_id"]]


# ---------------------------------------------------------------------------
# 积分流水
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_entry_with_opposite_sign_is_rejected(session: Session, kind: str) -> None:
    """PointLedger「获得、抵扣、到期、退款追回、待抵扣积分」与第 5 条「不出现负余额」的记账方向：
    获得、释放、返还与记入待抵扣增加积分（大于 0），抵扣、到期、追回、作废与偿还减少积分
    （小于 0）；每类写成相反符号都被拒。
    """
    ledger = _ledger(session)
    values = _entry_values(ledger, kind)
    values["points"] = -values["points"]
    sign = "positive" if kind in POSITIVE_KINDS else "negative"
    _assert_check_rejects(session, PointEntry, f"ck_point_entries_{sign}_kind_points", values)


@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_entry_with_zero_points_is_rejected(session: Session, kind: str) -> None:
    """PointLedger「不可覆盖流水」：每条流水都是一笔实际变动，积分变动为 0 不成流水。"""
    ledger = _ledger(session)
    values = _entry_values(ledger, kind, points=0)
    _assert_check_rejects(session, PointEntry, "ck_point_entries_points_not_zero", values)


def test_unknown_entry_kind_is_rejected(session: Session) -> None:
    """PointLedger「获得、抵扣、到期、退款追回、待抵扣积分」：类别只能是九类之一。"""
    ledger = _ledger(session)
    values = _entry_values(ledger, KIND_EARN, kind="bonus")
    _assert_check_rejects(session, PointEntry, "ck_point_entries_kind_valid", values)


@pytest.mark.parametrize("kind", LOT_KINDS)
def test_lot_kind_without_lot_is_rejected(session: Session, kind: str) -> None:
    """Kelvin 2026-10-10「积分按批次记账」：获得、抵扣、释放、到期、返还、追回与作废都记在某个
    批次上，缺批次被拒。
    """
    ledger = _ledger(session)
    values = _entry_values(ledger, kind, lot_id=None)
    _assert_check_rejects(session, PointEntry, "ck_point_entries_lot_kind_has_lot", values)


def test_debt_add_with_lot_is_rejected(session: Session) -> None:
    """第 5 条「积分余额不足时扣至 0，缺口记待抵扣」：缺口记入待抵扣账户（批次为空的流水），
    不记在任何批次上。
    """
    ledger = _ledger(session)
    values = _entry_values(ledger, KIND_DEBT_ADD, lot_id=ledger.lot_id)
    _assert_check_rejects(session, PointEntry, "ck_point_entries_debt_add_has_no_lot", values)


@pytest.mark.parametrize("kind", REFUND_KINDS)
def test_refund_kind_without_refund_request_is_rejected(session: Session, kind: str) -> None:
    """第 5 条「退款批准时……返还未过期的抵扣积分并追回获得积分；……缺口记待抵扣」与
    PointLedger「每笔关联订单或退款」：返还、追回与记入待抵扣都须指向所属退款申请。
    """
    ledger = _ledger(session)
    values = _entry_values(ledger, kind, refund_request_id=None)
    _assert_check_rejects(session, PointEntry, "ck_point_entries_refund_kind_has_refund", values)


@pytest.mark.parametrize("kind", NON_REFUND_KINDS)
def test_non_refund_kind_with_refund_request_is_rejected(session: Session, kind: str) -> None:
    """PointLedger「每笔关联订单或退款」：只有退款产生的返还、追回与记入待抵扣关联退款申请，
    获得、抵扣、释放、到期、作废与偿还不关联退款申请。
    """
    ledger = _ledger(session)
    values = _entry_values(ledger, kind, refund_request_id=ledger.refund_request_id)
    constraint = "ck_point_entries_non_refund_kind_has_no_refund"
    _assert_check_rejects(session, PointEntry, constraint, values)


@pytest.mark.parametrize("column", ["member_id", "kind", "points", "order_id", "created_at"])
def test_entry_required_columns_reject_null(session: Session, column: str) -> None:
    """PointLedger「每笔关联订单或退款」：流水必须属于一个会员、一张订单，有类别、积分变动与
    时间；只有批次与退款申请可空，类别或变动为空时检查约束会放行，只能靠非空约束拒绝。
    """
    ledger = _ledger(session)
    _assert_null_rejected(session, PointEntry, column, _entry_values(ledger, KIND_EARN))


def test_only_designated_entry_columns_are_nullable() -> None:
    """验收「批次（可空）」「退款申请（可空）」：流水只有这两列可空。"""
    actual = {column.name for column in PointEntry.__table__.columns if column.nullable}

    assert actual == {"lot_id", "refund_request_id"}


def test_entry_kind_groups_cover_every_kind() -> None:
    """验收逐条列出的类别分组：九类各属正负之一；批次非空七类加 debt_add、debt_repay 为全部；
    退款类与非退款类互补。
    """
    assert set(POSITIVE_KINDS) | set(NEGATIVE_KINDS) == set(ENTRY_KINDS)
    assert not set(POSITIVE_KINDS) & set(NEGATIVE_KINDS)
    assert set(LOT_KINDS) | {KIND_DEBT_ADD, KIND_DEBT_REPAY} == set(ENTRY_KINDS)
    assert set(REFUND_KINDS) == {KIND_REFUND_RETURN, KIND_REFUND_REVERSAL, KIND_DEBT_ADD}
    assert set(NON_REFUND_KINDS) == set(ENTRY_KINDS) - set(REFUND_KINDS)
    assert len(ENTRY_KINDS) == 9
    assert {KIND_REDEEM, KIND_RELEASE} <= set(LOT_KINDS)


def test_entry_indexes() -> None:
    """验收「按（会员、创建时间）、批次与订单建索引」：会员按时间查流水、按批次求剩余、按订单求
    某单在各批次的净抵扣都有普通索引。
    """
    indexes = {index.name: index for index in PointEntry.__table__.indexes}

    expected = {
        "ix_point_entries_member_id_created_at": ["member_id", "created_at"],
        "ix_point_entries_lot_id": ["lot_id"],
        "ix_point_entries_order_id": ["order_id"],
    }
    assert {name: [c.name for c in index.columns] for name, index in indexes.items()} == expected
    assert not any(index.unique for index in indexes.values())


# ---------------------------------------------------------------------------
# 外键
# ---------------------------------------------------------------------------


def test_entry_lot_of_another_member_is_rejected(session: Session) -> None:
    """Kelvin 2026-10-10「积分按批次记账」与第 5 条「不出现负余额」按会员计算：流水的批次须属于
    流水的会员，（批次、会员）复合外键拒绝指向另一会员的批次；两个会员与批次都存在，被拒只因
    二者不属于同一会员。
    """
    ledger = _ledger(session)
    other = _member(session)
    values = _entry_values(ledger, KIND_REDEEM, member_id=other.id)

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, PointEntry(**values))


def test_entry_lot_of_same_member_is_accepted(session: Session) -> None:
    """对照上一条：同一会员的批次写得进去，拒绝确实来自会员不符。"""
    ledger = _ledger(session)
    _add(session, PointEntry(**_entry_values(ledger, KIND_REDEEM)))

    assert len(session.scalars(select(PointEntry)).all()) == 1


@pytest.mark.parametrize("missing", ["member_id", "order_id"])
def test_lot_must_reference_existing_rows(session: Session, missing: str) -> None:
    """Kelvin 2026-10-10「每单获得的积分为一个批次」：批次必须指向存在的会员与来源订单。"""
    member = _member(session)
    values = _lot_values(member.id, _order(session, member.id).id) | {missing: 999}

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, PointLot(**values))


@pytest.mark.parametrize("missing", ["member_id", "lot_id", "order_id", "refund_request_id"])
def test_entry_must_reference_existing_rows(session: Session, missing: str) -> None:
    """PointLedger「每笔关联订单或退款」：流水必须指向存在的会员、批次、订单与退款申请。"""
    ledger = _ledger(session)
    values = _entry_values(ledger, KIND_REFUND_RETURN) | {missing: 999}

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, PointEntry(**values))


def test_deleting_lot_referenced_by_entry_is_rejected(session: Session) -> None:
    """PointLedger「不可覆盖流水」：被流水引用的批次不能物理删除，外键 RESTRICT 拒绝，不级联
    删掉流水。
    """
    ledger = _ledger(session)
    _add(session, PointEntry(**_entry_values(ledger, KIND_EARN)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(PointLot).where(PointLot.id == ledger.lot_id))


@pytest.mark.parametrize("parent", ["member", "order", "refund"])
def test_deleting_row_referenced_by_entry_is_rejected(session: Session, parent: str) -> None:
    """PointLedger「不可覆盖流水」与「资料保留」（积分流水与订单、退款一并保存）：被流水引用的
    会员、订单与退款申请都不能物理删除，外键 RESTRICT 拒绝。两张订单都不挂会员 ID、都不是批次
    来源；被删的那一行只被一条流水引用（删订单时用不挂退款申请的偿还流水，删会员与退款申请时
    用记入待抵扣的流水），删除被拒只因流水的引用。
    """
    member = _member(session)
    refunded = _order(session, None, claim_status=CLAIM_OPEN)
    refund = _refund(session, refunded.id)
    if parent == "order":
        order = _order(session, None, claim_status=CLAIM_OPEN)
        values = {
            "member_id": member.id,
            "lot_id": None,
            "kind": KIND_DEBT_REPAY,
            "points": -20,
            "order_id": order.id,
            "refund_request_id": None,
            "created_at": CREATED,
        }
    else:
        order = refunded
        values = {
            "member_id": member.id,
            "lot_id": None,
            "kind": KIND_DEBT_ADD,
            "points": 20,
            "order_id": refunded.id,
            "refund_request_id": refund.id,
            "created_at": CREATED,
        }
    _add(session, PointEntry(**values))
    model, row_id = {
        "member": (Member, member.id),
        "order": (Order, order.id),
        "refund": (RefundRequest, refund.id),
    }[parent]

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(model).where(model.id == row_id))


def test_unreferenced_lot_can_be_deleted(session: Session) -> None:
    """对照删除被拒：没有流水的批次删除不受外键限制，拒绝确实来自流水的引用（批次只追加由规则
    任务保证，库里不禁止删除未被引用的批次）。
    """
    ledger = _ledger(session)

    session.execute(delete(PointLot).where(PointLot.id == ledger.lot_id))

    assert session.scalars(select(PointLot)).all() == []


def test_foreign_keys_are_restrict() -> None:
    """验收「外键删除行为一律 RESTRICT」：批次指向会员与订单、流水指向会员、批次（复合）、订单与
    退款申请的外键都写明为 RESTRICT。
    """
    lot_targets = {fk.column.table.name: fk.ondelete for fk in PointLot.__table__.foreign_keys}
    entry_constraints = [
        c for c in PointEntry.__table__.constraints if isinstance(c, ForeignKeyConstraint)
    ]
    entry_targets = {c.referred_table.name: c for c in entry_constraints}

    assert lot_targets == {"members": "RESTRICT", "orders": "RESTRICT"}
    assert set(entry_targets) == {"members", "point_lots", "orders", "refund_requests"}
    assert all(c.ondelete == "RESTRICT" for c in entry_constraints)
    composite = entry_targets["point_lots"]
    assert composite.column_keys == ["lot_id", "member_id"]
    assert [element.target_fullname for element in composite.elements] == [
        "point_lots.id",
        "point_lots.member_id",
    ]

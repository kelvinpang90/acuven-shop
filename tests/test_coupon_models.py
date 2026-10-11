"""优惠券与用券记录两张表的数据库约束。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 Coupon / CouponUse 一行与「计价、优惠、
积分与库存」第 1 条，以及 docs/HANDOFF.md 0.46 记录的 Kelvin 2026-10-10 决定（两个上限可留空
表示不限，预占与核销都计次数，退款不恢复）。每条测试（参数化的测试是每个用例）写明它守住的
设计或决定原句。

先写入合法的固定金额券、百分比券、上限留空的券及其用券记录作对照，再逐个写反例。检查约束的
反例先在独立的 SQLite 连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，再断言写入
被拒、且报出的是不成立的约束之一。非空约束的反例用 Core insert 显式写入 NULL：ORM 会略过值为
None 的列、改用默认值。用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Coupon, CouponUse, Member, Order
from app.models.coupon import (
    COUNTED_USE_STATUSES,
    TYPE_FIXED,
    TYPE_PERCENT,
    USE_RELEASED,
    USE_RESERVED,
    USE_USED,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 1, 2, 0)
STARTS = datetime(2026, 9, 30, 16, 0)
# 仍有效的最后一刻：马来西亚时间 10 月 31 日 23:59:59。
ENDS = datetime(2026, 10, 31, 15, 59, 59)
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


def _fixed_values(**overrides: Any) -> dict[str, Any]:
    """固定金额券：减 RM10，商品满 RM50 可用，共 100 次，每会员 1 次。"""
    values: dict[str, Any] = {
        "code": "SAVE10",
        "coupon_type": TYPE_FIXED,
        "amount_off_sen": 1000,
        "percent_off": None,
        "min_subtotal_sen": 5000,
        "starts_at": STARTS,
        "ends_at": ENDS,
        "total_use_limit": 100,
        "per_member_use_limit": 1,
        "is_active": True,
        "created_at": CREATED,
    }
    return values | overrides


def _percent_values(**overrides: Any) -> dict[str, Any]:
    """百分比券：商品额 15% 折扣，无最低消费，共 50 次，每会员 2 次。"""
    values: dict[str, Any] = {
        "code": "PCT15",
        "coupon_type": TYPE_PERCENT,
        "amount_off_sen": None,
        "percent_off": 15,
        "min_subtotal_sen": 0,
        "starts_at": STARTS,
        "ends_at": ENDS,
        "total_use_limit": 50,
        "per_member_use_limit": 2,
        "is_active": True,
        "created_at": CREATED,
    }
    return values | overrides


def _use_values(coupon_id: int, order_id: int, member_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "coupon_id": coupon_id,
        "order_id": order_id,
        "member_id": member_id,
        "status": USE_RESERVED,
        "coupon_discount_sen": 1000,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return values | overrides


_PHONE_SEQUENCE = itertools.count(100_000_000)
_ORDER_SEQUENCE = itertools.count(1)


def _member(session: Session) -> Member:
    """每次给不同的手机号，同一测试里可建多个会员。"""
    phone = f"+601{next(_PHONE_SEQUENCE)}"
    return _add(session, Member(phone=phone, status=MEMBER_ACTIVE, created_at=CREATED))


def _order(session: Session, member_id: int | None, **overrides: Any) -> Order:
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
    return _add(session, Order(**(values | overrides)))


def _coupon(session: Session, **overrides: Any) -> Coupon:
    return _add(session, Coupon(**_fixed_values(**overrides)))


def _use_parents(session: Session) -> tuple[Coupon, Order, Member]:
    member = _member(session)
    return _coupon(session), _order(session, member.id), member


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


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_coupons_and_uses_are_accepted(session: Session) -> None:
    """「Coupon / CouponUse：固定金额或百分比、有效期、最低商品消费、总使用上限、每会员使用
    上限、启停状态；使用记录与订单关联」与 Kelvin 2026-10-10「两个上限都可留空表示不限」：
    固定金额券、百分比券（含 1% 与 100%）、两个上限都留空的券、停用的券，以及预占、核销、
    释放三种状态的用券记录（含折扣为 0）都写得进去。这是下面各条拒绝测试的对照：同样的建表
    与外键检查下，合法数据写得进去，且求值辅助函数对它们不报任何约束不成立。
    """
    fixed = _add(session, Coupon(**_fixed_values()))
    percent = _add(session, Coupon(**_percent_values()))
    unlimited = _add(
        session,
        Coupon(**_fixed_values(code="NOLIMIT", total_use_limit=None, per_member_use_limit=None)),
    )
    _add(session, Coupon(**_percent_values(code="ONEPCT", percent_off=1)))
    _add(session, Coupon(**_percent_values(code="FREE100", percent_off=100)))
    _add(session, Coupon(**_fixed_values(code="OFF1", amount_off_sen=1, is_active=False)))
    _add(
        session,
        Coupon(
            **_fixed_values(
                code="ABCDEFGHIJKLMNOPQRST",
                ends_at=STARTS + timedelta(seconds=1),
                total_use_limit=1,
            )
        ),
    )

    member = _member(session)
    _add(session, CouponUse(**_use_values(fixed.id, _order(session, member.id).id, member.id)))
    _add(
        session,
        CouponUse(
            **_use_values(percent.id, _order(session, member.id).id, member.id, status=USE_USED)
        ),
    )
    _add(
        session,
        CouponUse(
            **_use_values(
                unlimited.id,
                _order(session, member.id).id,
                member.id,
                status=USE_RELEASED,
                coupon_discount_sen=0,
            )
        ),
    )

    assert len(session.scalars(select(Coupon)).all()) == 7
    assert len(session.scalars(select(CouponUse)).all()) == 3
    assert unlimited.total_use_limit is None
    assert unlimited.per_member_use_limit is None

    assert _failing_checks(Coupon, _fixed_values()) == []
    assert _failing_checks(Coupon, _percent_values()) == []
    no_limits = _fixed_values(total_use_limit=None, per_member_use_limit=None)
    assert _failing_checks(Coupon, no_limits) == []
    assert _failing_checks(CouponUse, _use_values(1, 1, 1)) == []
    assert _failing_checks(CouponUse, _use_values(1, 1, 1, status=USE_USED)) == []
    assert _failing_checks(CouponUse, _use_values(1, 1, 1, status=USE_RELEASED)) == []


def test_min_subtotal_defaults_to_zero(session: Session) -> None:
    """验收「最低商品消费（仙，默认 0）」：不写最低商品消费的券为 0，即不限。"""
    values = _fixed_values()
    del values["min_subtotal_sen"]
    coupon = _add(session, Coupon(**values))
    session.refresh(coupon)

    assert coupon.min_subtotal_sen == 0


def test_coupon_amounts_are_integers(session: Session) -> None:
    """「金额一律整数仙」：固定金额、最低消费与本单券折扣读回仍是 int，不经浮点。"""
    coupon = _coupon(session)
    member = _member(session)
    use = _add(
        session, CouponUse(**_use_values(coupon.id, _order(session, member.id).id, member.id))
    )
    session.expire_all()

    reloaded = session.get(Coupon, coupon.id)
    reloaded_use = session.get(CouponUse, use.id)
    assert reloaded is not None and reloaded_use is not None
    assert type(reloaded.amount_off_sen) is int
    assert type(reloaded.min_subtotal_sen) is int
    assert type(reloaded_use.coupon_discount_sen) is int


# ---------------------------------------------------------------------------
# 优惠券
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "values"),
    [
        (
            "「固定金额或百分比」：未知类型",
            "ck_coupons_type_valid",
            _fixed_values(coupon_type="gift"),
        ),
        (
            "「固定金额」：fixed 券缺金额",
            "ck_coupons_fixed_amount",
            _fixed_values(amount_off_sen=None),
        ),
        (
            "「固定金额」：fixed 券金额为 0",
            "ck_coupons_fixed_amount",
            _fixed_values(amount_off_sen=0),
        ),
        (
            "「固定金额」：fixed 券金额为负",
            "ck_coupons_fixed_amount",
            _fixed_values(amount_off_sen=-100),
        ),
        (
            "「固定金额或百分比」二选一：fixed 券带百分比",
            "ck_coupons_fixed_amount",
            _fixed_values(percent_off=10),
        ),
        (
            "「百分比」：percent 券缺百分比",
            "ck_coupons_percent_value",
            _percent_values(percent_off=None),
        ),
        (
            "「百分比」：percent 券百分比为 0",
            "ck_coupons_percent_value",
            _percent_values(percent_off=0),
        ),
        (
            "「百分比」且「优惠券先抵商品额」：percent 券百分比为 101，超过商品额",
            "ck_coupons_percent_value",
            _percent_values(percent_off=101),
        ),
        (
            "「固定金额或百分比」二选一：percent 券带金额",
            "ck_coupons_percent_value",
            _percent_values(amount_off_sen=500),
        ),
        (
            "「最低商品消费」：最低消费为负",
            "ck_coupons_min_subtotal_sen_non_negative",
            _fixed_values(min_subtotal_sen=-1),
        ),
        (
            "「有效期」：截止时间等于生效时间",
            "ck_coupons_ends_after_starts",
            _fixed_values(ends_at=STARTS),
        ),
        (
            "「有效期」：截止时间早于生效时间",
            "ck_coupons_ends_after_starts",
            _fixed_values(ends_at=STARTS - timedelta(seconds=1)),
        ),
        (
            "Kelvin 2026-10-10「总使用上限……可留空表示不限」，非空时须不小于 1：上限为 0",
            "ck_coupons_total_use_limit_positive",
            _fixed_values(total_use_limit=0),
        ),
        (
            "Kelvin 2026-10-10「每会员使用上限……可留空表示不限」，非空时须不小于 1：上限为 0",
            "ck_coupons_per_member_use_limit_positive",
            _fixed_values(per_member_use_limit=0),
        ),
        (
            "验收「代码……长度 4 到 20」：代码只有 3 个字符",
            "ck_coupons_code_length",
            _fixed_values(code="ABC"),
        ),
        (
            "验收「代码……长度 4 到 20」：代码有 21 个字符",
            "ck_coupons_code_length",
            _fixed_values(code="A" * 21),
        ),
        (
            "验收「代码……长度 4 到 20」：空代码",
            "ck_coupons_code_length",
            _fixed_values(code=""),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_coupon_check_constraints_reject(
    session: Session, design: str, constraint: str, values: dict[str, Any]
) -> None:
    """优惠券表每个检查约束至少一个反例；守住的设计或决定原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, Coupon, constraint, values)


@pytest.mark.parametrize(
    "column",
    [
        "code",
        "coupon_type",
        "min_subtotal_sen",
        "starts_at",
        "ends_at",
        "is_active",
        "created_at",
    ],
)
def test_coupon_required_columns_reject_null(session: Session, column: str) -> None:
    """「固定金额或百分比、有效期、最低商品消费……启停状态」：除固定金额、百分比与两个上限外，
    券的列都不能为空；类型、有效期或最低消费为空时相应检查约束会放行，只能靠非空约束拒绝。
    """
    _assert_null_rejected(session, Coupon, column, _fixed_values())


def test_duplicate_coupon_code_is_rejected(session: Session) -> None:
    """验收「代码……全表唯一」：结账按代码找券，同一代码不能对应两张券。"""
    _coupon(session)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: coupons.code"):
        _add(session, Coupon(**_percent_values(code="SAVE10")))


def test_only_designated_coupon_columns_are_nullable() -> None:
    """「固定金额或百分比」与 Kelvin 2026-10-10「两个上限都可留空表示不限」：只有固定金额、
    百分比与两个上限可空，其余一律非空。
    """
    actual = {column.name for column in Coupon.__table__.columns if column.nullable}

    assert actual == {"amount_off_sen", "percent_off", "total_use_limit", "per_member_use_limit"}


# ---------------------------------------------------------------------------
# 用券记录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「使用额度在下单时预占，取消或支付超时释放」：未知状态",
            "ck_coupon_uses_status_valid",
            {"status": "refunded"},
        ),
        (
            "「优惠券先抵商品额」：本单券折扣为负",
            "ck_coupon_uses_coupon_discount_sen_non_negative",
            {"coupon_discount_sen": -1},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_coupon_use_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """用券记录表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    coupon, order, member = _use_parents(session)
    values = _use_values(coupon.id, order.id, member.id, **overrides)
    _assert_check_rejects(session, CouponUse, constraint, values)


@pytest.mark.parametrize(
    "column",
    [
        "coupon_id",
        "order_id",
        "member_id",
        "status",
        "coupon_discount_sen",
        "created_at",
        "updated_at",
    ],
)
def test_coupon_use_required_columns_reject_null(session: Session, column: str) -> None:
    """「使用记录与订单关联」「优惠券仅会员可用」与 Kelvin 2026-10-10「每会员按会员账号计数」：
    用券记录必须属于一张券、一张订单与一个会员，有状态、折扣与时间；没有可空的列。
    """
    coupon, order, member = _use_parents(session)
    _assert_null_rejected(session, CouponUse, column, _use_values(coupon.id, order.id, member.id))


def test_second_coupon_use_for_same_order_is_rejected(session: Session) -> None:
    """验收「订单……全表唯一，一单至多一张券」：同一订单不能有第二条用券记录，换一张券也不行。"""
    coupon, order, member = _use_parents(session)
    other = _add(session, Coupon(**_percent_values()))
    _add(session, CouponUse(**_use_values(coupon.id, order.id, member.id)))
    message = "UNIQUE constraint failed: coupon_uses.order_id"

    with pytest.raises(IntegrityError, match=message):
        _add(session, CouponUse(**_use_values(other.id, order.id, member.id)))


def test_same_member_can_use_coupon_on_several_orders(session: Session) -> None:
    """Kelvin 2026-10-10「待支付订单的预占与已支付订单的核销都计入次数，取消或支付超时释放后
    不计」：同一会员同一张券可在多张订单上各有记录，库不按会员唯一；每会员次数由之后的规则按
    计次的状态（reserved、used）在（券、会员、状态）索引上数出来。
    """
    coupon, first_order, member = _use_parents(session)
    _add(
        session,
        CouponUse(**_use_values(coupon.id, first_order.id, member.id, status=USE_RELEASED)),
    )
    _add(
        session,
        CouponUse(
            **_use_values(coupon.id, _order(session, member.id).id, member.id, status=USE_USED)
        ),
    )
    _add(session, CouponUse(**_use_values(coupon.id, _order(session, member.id).id, member.id)))

    counted = session.scalars(
        select(CouponUse).where(
            CouponUse.coupon_id == coupon.id,
            CouponUse.member_id == member.id,
            CouponUse.status.in_(COUNTED_USE_STATUSES),
        )
    ).all()
    assert len(counted) == 2
    assert set(COUNTED_USE_STATUSES) == {USE_RESERVED, USE_USED}


def test_coupon_use_has_counting_indexes() -> None:
    """Kelvin 2026-10-10「预占与核销都计入次数」：按（券、状态）与（券、会员、状态）建普通索引，
    供之后计算总使用次数与每会员使用次数。
    """
    indexes = {index.name: index for index in CouponUse.__table__.indexes}

    by_status = indexes["ix_coupon_uses_coupon_id_status"]
    by_member = indexes["ix_coupon_uses_coupon_id_member_id_status"]
    assert [column.name for column in by_status.columns] == ["coupon_id", "status"]
    assert [column.name for column in by_member.columns] == ["coupon_id", "member_id", "status"]
    assert not by_status.unique
    assert not by_member.unique
    assert set(indexes) == {by_status.name, by_member.name}


# ---------------------------------------------------------------------------
# 外键
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["coupon_id", "order_id", "member_id"])
def test_coupon_use_must_reference_existing_rows(session: Session, missing: str) -> None:
    """「使用记录与订单关联」与「优惠券仅会员可用」：用券记录必须指向存在的券、订单与会员。"""
    coupon, order, member = _use_parents(session)
    values = _use_values(coupon.id, order.id, member.id) | {missing: 999}

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, CouponUse(**values))


@pytest.mark.parametrize("parent", ["coupon", "order", "member"])
def test_deleting_row_referenced_by_coupon_use_is_rejected(session: Session, parent: str) -> None:
    """「启停状态」与 Kelvin 2026-10-10「退款不恢复次数」：被用券记录引用的券只能停用、不能删除，
    订单与会员同样不能物理删除，外键 RESTRICT 拒绝，不级联删掉用券记录（会员注销只清空手机号
    与密码哈希，记录随之去标识化、照常计数）。订单不挂会员 ID，只有用券记录引用这三行。
    """
    member = _member(session)
    coupon = _coupon(session)
    order = _order(session, None, claim_status="open")
    _add(session, CouponUse(**_use_values(coupon.id, order.id, member.id)))
    model, row_id = {
        "coupon": (Coupon, coupon.id),
        "order": (Order, order.id),
        "member": (Member, member.id),
    }[parent]

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(model).where(model.id == row_id))


def test_unreferenced_coupon_can_be_deleted(session: Session) -> None:
    """对照上一条：没有用券记录的券删除不受外键限制，拒绝确实来自用券记录的引用。"""
    coupon = _coupon(session)

    session.execute(delete(Coupon).where(Coupon.id == coupon.id))

    assert session.scalars(select(Coupon)).all() == []


def test_foreign_keys_of_coupon_uses_are_restrict() -> None:
    """验收「外键删除行为一律 RESTRICT」：用券记录指向券、订单与会员的外键都写明为 RESTRICT。"""
    foreign_keys = {fk.column.table.name: fk for fk in CouponUse.__table__.foreign_keys}

    assert set(foreign_keys) == {"coupons", "orders", "members"}
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys.values())

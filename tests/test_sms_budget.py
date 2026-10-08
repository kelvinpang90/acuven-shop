"""短信每日总量与费用预算的预占、结算与释放。

覆盖 app/services/sms_budget.py 与 app/core/config.py 的四个配置项。依据：
- docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条
  （下文简称「设计第 4 条」）：“短信每日总量及费用预算另在本项目 MySQL 库
  以按日原子计数和预算记录兜底：发送前按目的地预占保守的单次最高费用，
  未知费用时停发；结算后更新记录，防 Redis 重置后超过日上限。
  Redis 或 MySQL 不可用时短信停发。……不得把验证码、短信凭据或
  完整手机号写进日志。”
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下文简称
  「Kelvin 决定」）：“全站每天 200 条、费用上限 20 美元；单次预占按目的地
  （马来西亚、新加坡）配置的最高单价，未配置的目的地停发；Twilio Verify
  不即时返回实际价格，按预占价结算；各项都做成配置项”。
- 日期按马来西亚时间切日：SHOP-TASK-012 的 SmsDailyUsage
  “每个马来西亚日期一行”。
每条测试的文档字符串写明它守住的是哪一句；没有直接原句的，
写明是 SHOP-TASK-068 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）。
引擎设置与 tests/test_stock_reset.py 相同（关掉驱动的事务处理、
由引擎发 BEGIN），保存点才和 MySQL 上一样：预占在保存点里插入当日行。
SQLite 会忽略 FOR UPDATE，也不复现 MySQL 的阻塞等待；并发只用两个
数据库会话按先后顺序模拟，验证后到者读到前者提交后的数。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import date, datetime

import pytest
from sqlalchemy import Engine, create_engine, event, insert, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import SmsDailyUsage
from app.services import sms_budget
from app.services.sms_budget import (
    OVER_BUDGET,
    RESERVED,
    UNKNOWN_COST,
    Reservation,
    SmsBudgetError,
    malaysia_date,
    release_sms,
    reserve_sms,
    settle_sms,
)

# UTC 03:00 即马来西亚 11:00，日期 2026-10-08。
NOW = datetime(2026, 10, 8, 3, 0, 0)
TODAY = date(2026, 10, 8)
PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
PHONE_UK = "+447911123456"
COST_MY = 60_000
COST_SG = 50_000
COUNT_LIMIT = 200
COST_LIMIT = 20_000_000

Operation = Callable[[Session, Reservation], None]


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # pysqlite 默认推迟 BEGIN，使最外层的 SAVEPOINT 自己开启事务、
    # RELEASE 即提交；按 SQLAlchemy 文档的做法关掉驱动的事务处理、
    # 由引擎发 BEGIN，保存点才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


def make_settings(**overrides: int) -> Settings:
    values: dict[str, int] = {
        "sms_max_cost_micro_usd_my": COST_MY,
        "sms_max_cost_micro_usd_sg": COST_SG,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def add_row(
    db: Session,
    day: date = TODAY,
    sent: int = 0,
    reserved: int = 0,
    settled: int = 0,
) -> None:
    db.execute(
        insert(SmsDailyUsage).values(
            usage_date=day,
            sent_count=sent,
            reserved_micro_usd=reserved,
            settled_micro_usd=settled,
        )
    )
    db.commit()


def counts(db: Session, day: date = TODAY) -> tuple[int, int, int] | None:
    """该日期行的（条数, 已预占, 已结算）；没有行为空。读完结束事务。"""
    stmt = select(
        SmsDailyUsage.sent_count,
        SmsDailyUsage.reserved_micro_usd,
        SmsDailyUsage.settled_micro_usd,
    ).where(SmsDailyUsage.usage_date == day)
    row = db.execute(stmt).one_or_none()
    db.rollback()
    return None if row is None else (row[0], row[1], row[2])


def row_count(db: Session) -> int:
    value = len(db.scalars(select(SmsDailyUsage.id)).all())
    db.rollback()
    return value


# ---------------------------------------------------------------------------
# 马来西亚日期
# ---------------------------------------------------------------------------


def test_malaysia_date_switches_at_utc_1600() -> None:
    """守住「每个马来西亚日期一行」与验收第 1 条「按马来西亚时间（UTC+8）
    的日期」：UTC 15:59:59 仍是当天，16:00:00 起是马来西亚的下一天。
    """
    assert malaysia_date(datetime(2026, 10, 8, 15, 59, 59)) == date(2026, 10, 8)
    assert malaysia_date(datetime(2026, 10, 8, 16, 0, 0)) == date(2026, 10, 9)
    assert malaysia_date(datetime(2026, 10, 7, 16, 0, 0)) == date(2026, 10, 8)
    assert malaysia_date(datetime(2026, 12, 31, 16, 0, 0)) == date(2027, 1, 1)


def test_reservations_either_side_of_midnight_go_to_separate_rows(db: Session) -> None:
    """守住设计第 4 条「按日原子计数」：UTC 15:59:59 的预占记在当天，
    16:00:00 的记在马来西亚的下一天。
    """
    settings = make_settings()
    before = reserve_sms(db, settings, PHONE_MY, datetime(2026, 10, 8, 15, 59, 59))
    after = reserve_sms(db, settings, PHONE_SG, datetime(2026, 10, 8, 16, 0, 0))
    db.commit()

    assert before.reservation == Reservation(date(2026, 10, 8), COST_MY)
    assert after.reservation == Reservation(date(2026, 10, 9), COST_SG)
    assert counts(db, date(2026, 10, 8)) == (1, COST_MY, 0)
    assert counts(db, date(2026, 10, 9)) == (1, COST_SG, 0)


# ---------------------------------------------------------------------------
# 预占
# ---------------------------------------------------------------------------


def test_first_reservation_inserts_today_row(db: Session) -> None:
    """守住设计第 4 条「发送前按目的地预占保守的单次最高费用」与验收第 1 条
    「不存在时……插入零值行……sent_count 加 1、reserved 加本次费用，
    返回预占凭据（日期与金额）」。
    """
    assert counts(db) is None

    result = reserve_sms(db, make_settings(), PHONE_MY, NOW)
    db.commit()

    assert result.status == RESERVED
    assert result.reservation == Reservation(TODAY, COST_MY)
    assert counts(db) == (1, COST_MY, 0)
    assert row_count(db) == 1


def test_reservation_adds_to_existing_row(db: Session) -> None:
    """守住设计第 4 条「按日原子计数」：已有当日行时在原行上累加，
    不另插行。
    """
    add_row(db, sent=3, reserved=100, settled=900)

    result = reserve_sms(db, make_settings(), PHONE_SG, NOW)
    db.commit()

    assert result.reservation == Reservation(TODAY, COST_SG)
    assert counts(db) == (4, 100 + COST_SG, 900)
    assert row_count(db) == 1


def test_insert_colliding_with_existing_row_rolls_back_savepoint(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """守住验收第 1 条「撞上日期唯一约束即回滚保存点再找」：先找时漏看了
    别人已提交的当日行（MySQL 上快照读可能如此），插入撞约束后只回滚
    保存点，本事务照常在已有行上预占，不重复插行。
    """
    add_row(db, sent=5, reserved=COST_MY, settled=0)
    monkeypatch.setattr(sms_budget, "_find", lambda _db, _day: None)

    result = reserve_sms(db, make_settings(), PHONE_MY, NOW)
    assert result.status == RESERVED
    db.commit()

    assert counts(db) == (6, 2 * COST_MY, 0)
    assert row_count(db) == 1


def test_other_integrity_error_on_insert_propagates(db: Session) -> None:
    """守住验收第 1 条「撞上日期唯一约束即回滚保存点再找」只认日期冲突，
    与验收第 2 条「MySQL 出错时异常交给调用方（调用方据此停发）」：
    插入当日行因别的约束失败时，原 IntegrityError 抛出，不转成别的错误，
    也不留行。
    """
    db.execute(
        text(
            "CREATE TRIGGER block_sms_usage BEFORE INSERT ON sms_daily_usage "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    )
    db.commit()

    with pytest.raises(IntegrityError):
        reserve_sms(db, make_settings(), PHONE_MY, NOW)
    db.rollback()
    assert row_count(db) == 0


@pytest.mark.parametrize(
    ("orig", "expected"),
    [
        (
            Exception(
                1062,
                "Duplicate entry '2026-10-08' for key 'uq_sms_daily_usage_usage_date'",
            ),
            True,
        ),
        (
            Exception(
                1062,
                "Duplicate entry '2026-10-08' for key "
                "'sms_daily_usage.uq_sms_daily_usage_usage_date'",
            ),
            True,
        ),
        (Exception(1062, "Duplicate entry '1' for key 'sms_daily_usage.PRIMARY'"), False),
        (Exception(3819, "Check constraint 'ck_sms_daily_usage_x' is violated."), False),
        (Exception("UNIQUE constraint failed: sms_daily_usage.usage_date"), True),
        (Exception("UNIQUE constraint failed: sms_daily_usage.id"), False),
        (Exception("CHECK constraint failed: sent_count_non_negative"), False),
    ],
    ids=[
        "mysql57-date-key",
        "mysql8-date-key",
        "mysql-other-key",
        "mysql-check",
        "sqlite-date",
        "sqlite-other-unique",
        "sqlite-check",
    ],
)
def test_only_date_unique_conflict_counts_as_duplicate(orig: Exception, expected: bool) -> None:
    """守住验收第 1 条「撞上日期唯一约束即回滚保存点再找」与验收第 2 条
    「MySQL 出错时异常交给调用方」：只有日期唯一约束冲突（MySQL 1062 且键为
    日期唯一键，或 SQLite 对日期列的唯一约束）被当作别人已插入，其余完整性
    错误都不算。
    """
    exc = IntegrityError("INSERT INTO sms_daily_usage", {}, orig)
    assert sms_budget._is_duplicate_date(exc) is expected


def test_reservation_uses_destination_cost(db: Session) -> None:
    """守住 Kelvin 决定「单次预占按目的地（马来西亚、新加坡）配置的
    最高单价」：国家呼叫码 60 用马来西亚的配置，65 用新加坡的配置。
    """
    settings = make_settings()
    my = reserve_sms(db, settings, PHONE_MY, NOW)
    sg = reserve_sms(db, settings, PHONE_SG, NOW)
    db.commit()

    assert my.reservation == Reservation(TODAY, COST_MY)
    assert sg.reservation == Reservation(TODAY, COST_SG)
    assert counts(db) == (2, COST_MY + COST_SG, 0)


@pytest.mark.parametrize(
    ("overrides", "phone"),
    [
        ({"sms_max_cost_micro_usd_my": 0}, PHONE_MY),
        ({"sms_max_cost_micro_usd_sg": 0}, PHONE_SG),
        ({}, PHONE_UK),
        ({"sms_max_cost_micro_usd_my": -1}, PHONE_MY),
    ],
    ids=["my-unconfigured", "sg-unconfigured", "other-country", "my-negative"],
)
def test_unconfigured_destination_is_rejected(
    db: Session, overrides: dict[str, int], phone: str
) -> None:
    """守住设计第 4 条「未知费用时停发」与 Kelvin 决定「未配置的目的地
    停发」：单次最高费用未配置（0）或不是马来西亚、新加坡时为
    unknown_cost，不改数。负数按未配置处理（SHOP-TASK-068 的取舍）。
    """
    add_row(db, sent=2, reserved=COST_MY, settled=COST_SG)

    result = reserve_sms(db, make_settings(**overrides), phone, NOW)
    db.commit()

    assert result.status == UNKNOWN_COST
    assert result.reservation is None
    assert counts(db) == (2, COST_MY, COST_SG)


def test_default_settings_stop_all_destinations(db: Session) -> None:
    """守住 Kelvin 决定「未配置的目的地停发」：两国单次最高费用默认 0，
    按默认配置一律停发、不改数。
    """
    settings = make_settings(sms_max_cost_micro_usd_my=0, sms_max_cost_micro_usd_sg=0)
    for phone in (PHONE_MY, PHONE_SG):
        assert reserve_sms(db, settings, phone, NOW).status == UNKNOWN_COST
    db.commit()
    assert counts(db) == (0, 0, 0)


def test_count_limit_exactly_reached_then_rejected(db: Session) -> None:
    """守住 Kelvin 决定「全站每天 200 条」：第 200 条放行，
    第 201 条为 over_budget 且不改数。
    """
    add_row(db, sent=COUNT_LIMIT - 1)
    settings = make_settings()

    assert reserve_sms(db, settings, PHONE_MY, NOW).status == RESERVED
    db.commit()
    assert counts(db) == (COUNT_LIMIT, COST_MY, 0)

    result = reserve_sms(db, settings, PHONE_MY, NOW)
    db.commit()
    assert result.status == OVER_BUDGET
    assert result.reservation is None
    assert counts(db) == (COUNT_LIMIT, COST_MY, 0)


def test_cost_limit_exactly_reached_is_allowed(db: Session) -> None:
    """守住 Kelvin 决定「费用上限 20 美元」：已预占加已结算加本次费用
    恰为上限时放行。
    """
    reserved = 5_000_000
    settled = COST_LIMIT - reserved - COST_MY
    add_row(db, sent=10, reserved=reserved, settled=settled)

    assert reserve_sms(db, make_settings(), PHONE_MY, NOW).status == RESERVED
    db.commit()
    assert counts(db) == (11, reserved + COST_MY, settled)


def test_cost_limit_exceeded_by_one_unit_is_rejected(db: Session) -> None:
    """守住设计第 4 条「防……超过日上限」与 Kelvin 决定「费用上限
    20 美元」：多出最小单位 1 微美元即为 over_budget，不改数。
    """
    reserved = 5_000_000
    settled = COST_LIMIT - reserved - COST_MY + 1
    add_row(db, sent=10, reserved=reserved, settled=settled)

    result = reserve_sms(db, make_settings(), PHONE_MY, NOW)
    db.commit()
    assert result.status == OVER_BUDGET
    assert counts(db) == (10, reserved, settled)


def test_settled_amount_counts_toward_cost_limit(db: Session) -> None:
    """守住设计第 4 条「结算后更新记录，防 Redis 重置后超过日上限」：
    结算移走的金额仍计入每日费用上限。
    """
    add_row(db, sent=1, reserved=0, settled=COST_LIMIT)

    assert reserve_sms(db, make_settings(), PHONE_SG, NOW).status == OVER_BUDGET
    db.commit()
    assert counts(db) == (1, 0, COST_LIMIT)


def test_limits_come_from_settings(db: Session) -> None:
    """守住 Kelvin 决定「各项都做成配置项」：调低的每日条数与费用上限
    即时生效。
    """
    settings = make_settings(sms_daily_count_limit=1, sms_daily_cost_limit_micro_usd=COST_MY)
    assert reserve_sms(db, settings, PHONE_MY, NOW).status == RESERVED
    assert reserve_sms(db, settings, PHONE_MY, NOW).status == OVER_BUDGET

    settings = make_settings(sms_daily_cost_limit_micro_usd=COST_MY + COST_SG - 1)
    assert reserve_sms(db, settings, PHONE_SG, NOW).status == OVER_BUDGET
    db.commit()
    assert counts(db) == (1, COST_MY, 0)


def test_two_sessions_reserve_in_order(engine: Engine) -> None:
    """守住设计第 4 条「按日原子计数」：两个会话先后预占时，后到者在锁定
    读取时读到前者提交后的数（即使它的会话里还留着更早读到的旧值），
    合计不超过上限。SQLite 不复现 MySQL 的阻塞等待，这里只验证先后顺序。
    """
    settings = make_settings(sms_daily_count_limit=2)
    with Session(engine, expire_on_commit=False) as first, Session(engine) as second:
        assert reserve_sms(first, settings, PHONE_MY, NOW).status == RESERVED
        first.commit()
        stale = first.scalars(select(SmsDailyUsage)).one()
        first.commit()
        assert stale.sent_count == 1

        assert reserve_sms(second, settings, PHONE_SG, NOW).status == RESERVED
        second.commit()

        # first 的会话里这一行仍是旧值（条数 1）；
        # 锁定读取以最新值覆盖后判断，第三条被拒。
        assert stale.sent_count == 1
        assert reserve_sms(first, settings, PHONE_MY, NOW).status == OVER_BUDGET
        first.commit()
        assert stale.sent_count == 2

    with Session(engine) as check:
        assert counts(check) == (2, COST_MY + COST_SG, 0)


# ---------------------------------------------------------------------------
# 结算与释放
# ---------------------------------------------------------------------------


def test_settle_moves_reserved_to_settled_at_reserved_price(db: Session) -> None:
    """守住 Kelvin 决定「按预占价结算」与设计第 4 条「结算后更新记录」：
    凭据金额从已预占移到已结算，条数不变。
    """
    settings = make_settings()
    first = reserve_sms(db, settings, PHONE_MY, NOW).reservation
    second = reserve_sms(db, settings, PHONE_SG, NOW).reservation
    db.commit()
    assert first is not None and second is not None

    settle_sms(db, first)
    db.commit()
    assert counts(db) == (2, COST_SG, COST_MY)

    settle_sms(db, second)
    db.commit()
    assert counts(db) == (2, 0, COST_MY + COST_SG)


def test_release_returns_amount_and_count(db: Session) -> None:
    """守住验收第 2 条「释放把金额从 reserved 减去并把 sent_count 减 1
    （短信未发出时用）」：已结算的另一条不受影响。
    """
    settings = make_settings()
    first = reserve_sms(db, settings, PHONE_MY, NOW).reservation
    second = reserve_sms(db, settings, PHONE_SG, NOW).reservation
    db.commit()
    assert first is not None and second is not None
    settle_sms(db, second)
    db.commit()

    release_sms(db, first)
    db.commit()
    assert counts(db) == (1, 0, COST_SG)


def test_release_frees_budget_for_next_reservation(db: Session) -> None:
    """守住设计第 4 条「按日原子计数和预算记录兜底」：释放还回的条数
    可再预占，未发出的短信不永久占用上限。
    """
    settings = make_settings(sms_daily_count_limit=1)
    reservation = reserve_sms(db, settings, PHONE_MY, NOW).reservation
    assert reservation is not None
    assert reserve_sms(db, settings, PHONE_MY, NOW).status == OVER_BUDGET

    release_sms(db, reservation)
    assert reserve_sms(db, settings, PHONE_MY, NOW).status == RESERVED
    db.commit()
    assert counts(db) == (1, COST_MY, 0)


def test_settle_and_release_use_reservation_date_not_today(db: Session) -> None:
    """守住验收第 2 条「结算与释放都锁定同一日期行、不跨日」：前一天的
    凭据在马来西亚跨日后结算或释放，只改前一天那一行。
    """
    settings = make_settings()
    late = datetime(2026, 10, 8, 15, 59, 59)
    to_settle = reserve_sms(db, settings, PHONE_MY, late).reservation
    to_release = reserve_sms(db, settings, PHONE_SG, late).reservation
    reserve_sms(db, settings, PHONE_MY, datetime(2026, 10, 8, 16, 0, 0))
    db.commit()
    assert to_settle is not None and to_release is not None

    settle_sms(db, to_settle)
    release_sms(db, to_release)
    db.commit()

    assert counts(db, date(2026, 10, 8)) == (1, 0, COST_MY)
    assert counts(db, date(2026, 10, 9)) == (1, COST_MY, 0)


@pytest.mark.parametrize("operation", [settle_sms, release_sms], ids=["settle", "release"])
def test_settle_or_release_twice_is_refused(db: Session, operation: Operation) -> None:
    """守住验收第 2 条「金额与计数不会成为负数……代码另行防护并在违反时
    抛错」：同一凭据第二次结算或释放时已预占不足，抛错且不改数。
    """
    reservation = reserve_sms(db, make_settings(), PHONE_MY, NOW).reservation
    assert reservation is not None
    operation(db, reservation)
    db.commit()
    before = counts(db)

    with pytest.raises(SmsBudgetError):
        operation(db, reservation)
    db.rollback()
    assert counts(db) == before


@pytest.mark.parametrize(
    ("row", "operation"),
    [
        ((1, COST_MY - 1, 0), settle_sms),
        ((1, COST_MY - 1, 0), release_sms),
        ((0, COST_MY, 0), release_sms),
    ],
    ids=["settle-reserved-short", "release-reserved-short", "release-count-zero"],
)
def test_settle_or_release_never_goes_negative(
    db: Session, row: tuple[int, int, int], operation: Operation
) -> None:
    """守住验收第 2 条「金额与计数不会成为负数（库里已有非负约束，
    代码另行防护并在违反时抛错）」：已预占小于凭据金额、或释放时
    条数已为 0，都抛 SmsBudgetError，不改数。
    """
    add_row(db, sent=row[0], reserved=row[1], settled=row[2])

    with pytest.raises(SmsBudgetError):
        operation(db, Reservation(TODAY, COST_MY))
    db.rollback()
    assert counts(db) == row


@pytest.mark.parametrize("operation", [settle_sms, release_sms], ids=["settle", "release"])
@pytest.mark.parametrize("amount", [0, -1, True], ids=["zero", "negative", "bool"])
def test_settle_or_release_rejects_non_positive_amount(
    db: Session, operation: Operation, amount: int
) -> None:
    """守住验收第 2 条「金额与计数不会成为负数」：凭据金额不是正整数时
    抛错，负数金额不会反向加数。
    """
    add_row(db, sent=1, reserved=COST_MY, settled=0)

    with pytest.raises(SmsBudgetError):
        operation(db, Reservation(TODAY, amount))
    db.rollback()
    assert counts(db) == (1, COST_MY, 0)


@pytest.mark.parametrize("operation", [settle_sms, release_sms], ids=["settle", "release"])
def test_settle_or_release_without_row_is_refused(db: Session, operation: Operation) -> None:
    """守住验收第 2 条「结算与释放都锁定同一日期行」：凭据日期没有行时
    抛错，也不插行。
    """
    with pytest.raises(SmsBudgetError):
        operation(db, Reservation(TODAY, COST_MY))
    db.rollback()
    assert row_count(db) == 0


# ---------------------------------------------------------------------------
# 事务、出错与不泄露
# ---------------------------------------------------------------------------


def test_functions_flush_but_do_not_commit(db: Session) -> None:
    """守住验收第 2 条「三个函数只 flush、不提交，由调用方提交」：
    调用方回滚后什么也没留下。
    """
    settings = make_settings()
    reservation = reserve_sms(db, settings, PHONE_MY, NOW).reservation
    assert reservation is not None
    assert db.execute(select(SmsDailyUsage.sent_count)).scalar_one() == 1
    db.rollback()
    assert row_count(db) == 0

    add_row(db, sent=2, reserved=2 * COST_MY, settled=0)
    settle_sms(db, Reservation(TODAY, COST_MY))
    release_sms(db, Reservation(TODAY, COST_MY))
    assert db.execute(select(SmsDailyUsage.sent_count)).scalar_one() == 1
    db.rollback()
    assert counts(db) == (2, 2 * COST_MY, 0)


def test_database_error_propagates_to_caller(db: Session) -> None:
    """守住设计第 4 条「MySQL 不可用时短信停发」与验收第 2 条
    「MySQL 出错时异常交给调用方」：库出错时原异常抛出，不当作放行。
    """
    db.execute(text("DROP TABLE sms_daily_usage"))
    db.commit()

    with pytest.raises(OperationalError):
        reserve_sms(db, make_settings(), PHONE_MY, NOW)
    db.rollback()
    with pytest.raises(OperationalError):
        settle_sms(db, Reservation(TODAY, COST_MY))
    db.rollback()
    with pytest.raises(OperationalError):
        release_sms(db, Reservation(TODAY, COST_MY))
    db.rollback()


@pytest.mark.parametrize("phone", ["60123456789", "+060123456789", "+60 12 345 6789", "", None])
def test_non_e164_phone_raises_without_echoing_it(db: Session, phone: str | None) -> None:
    """守住设计第 4 条「不得把……完整手机号写进日志」与验收第 4 条
    「手机号不出现在异常消息里」：号码不是规范化 E.164 时抛 ValueError，
    消息不含号码，也不插行。
    """
    with pytest.raises(ValueError) as excinfo:
        reserve_sms(db, make_settings(), phone, NOW)  # type: ignore[arg-type]
    assert "123456789" not in str(excinfo.value)
    db.rollback()
    assert row_count(db) == 0


def test_no_logging_and_no_phone_in_results(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    """守住设计第 4 条「不得把……完整手机号写进日志」与验收第 4 条
    「不写日志；手机号不出现在异常消息与返回值里」。
    """
    caplog.set_level(logging.DEBUG)
    settings = make_settings(sms_daily_count_limit=1)
    results = [
        reserve_sms(db, settings, PHONE_MY, NOW),
        reserve_sms(db, settings, PHONE_SG, NOW),
        reserve_sms(db, settings, PHONE_UK, NOW),
    ]
    assert [result.status for result in results] == [RESERVED, OVER_BUDGET, UNKNOWN_COST]
    reservation = results[0].reservation
    assert reservation is not None
    settle_sms(db, reservation)
    with pytest.raises(SmsBudgetError) as excinfo:
        release_sms(db, reservation)
    db.rollback()

    texts = [repr(result) for result in results] + [str(excinfo.value)]
    for phone in (PHONE_MY, PHONE_SG, PHONE_UK):
        assert all(phone[1:] not in item for item in texts)
    assert [r for r in caplog.records if r.name.startswith("app")] == []


# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------

SMS_ENV_NAMES = (
    "SHOP_SMS_DAILY_COUNT_LIMIT",
    "SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD",
    "SHOP_SMS_MAX_COST_MICRO_USD_MY",
    "SHOP_SMS_MAX_COST_MICRO_USD_SG",
)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住 Kelvin 决定「全站每天 200 条、费用上限 20 美元；……未配置的
    目的地停发」与验收第 3 条的默认值：200 条、20000000 微美元、
    两国单次最高费用 0（未配置）。
    """
    for name in SMS_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.sms_daily_count_limit == 200
    assert settings.sms_daily_cost_limit_micro_usd == 20_000_000
    assert settings.sms_max_cost_micro_usd_my == 0
    assert settings.sms_max_cost_micro_usd_sg == 0


def test_settings_read_from_shop_env(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """守住 Kelvin 决定「各项都做成配置项」与验收第 3 条「前缀 SHOP_」：
    四项取自 SHOP_ 环境变量，并用于预占。
    """
    for name, value in zip(SMS_ENV_NAMES, ("1", "70000", "70000", "40000"), strict=True):
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.sms_daily_count_limit == 1
    assert settings.sms_daily_cost_limit_micro_usd == 70_000
    assert settings.sms_max_cost_micro_usd_my == 70_000
    assert settings.sms_max_cost_micro_usd_sg == 40_000

    result = reserve_sms(db, settings, PHONE_MY, NOW)
    assert result.reservation == Reservation(TODAY, 70_000)
    assert reserve_sms(db, settings, PHONE_SG, NOW).status == OVER_BUDGET
    db.commit()
    assert counts(db) == (1, 70_000, 0)

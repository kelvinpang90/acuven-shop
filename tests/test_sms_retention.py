"""短信验证记录满 30 天删除：app/services/sms_retention.py 与 app/jobs.py 的定时任务、补跑命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「资料保留」第 4 条：「短信验证请求及发送记录短期保留用于防滥用」；同一条「会员手机号及密码
  哈希保留至主动注销」。
- 「失败、并发与重试」第 6 条：「每日库存重置、积分过期均可重复运行……只生效一次；失败有告警与
  人工补跑办法」（SHOP-TASK-031 起对所有定时任务适用）；「权限与资料保护」：日志不记录完整电话。
以及 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（4）：「短信验证记录（含完整手机号）
保留 30 天后删除。」
每条测试（参数化的是每个用例）的文档字符串写明它守住的是上述哪一句，并写明对应的
SHOP-TASK-072 验收约定。

用 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档关掉 pysqlite
的事务处理、由引擎发 BEGIN，与 tests/test_jobs.py 相同）按模型建表。验证记录、会员、会员会话、
短信每日用量与订单都直接写库建立；内存库只有一条共享连接，所以每个辅助函数用自己的会话、用完即
结束事务。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import Settings
from app.db.base import Base
from app.models import Member, MemberSession, Order, SmsDailyUsage, VerificationAttempt
from app.models.member import (
    MEMBER_ACTIVE,
    PURPOSE_CHECKOUT,
    VERIFICATION_PURPOSES,
    VERIFICATION_SENT,
    VERIFICATION_STATUSES,
    VERIFICATION_SUSPENDED,
)
from app.models.order import STATUS_AWAITING_PAYMENT
from app.services.order_rules import generate_order_number
from app.services.sms_retention import (
    VERIFICATION_RETENTION,
    delete_expired_verifications,
    retention_cutoff,
)

NOW = datetime(2026, 11, 8, 3, 0, 0)
# 恰好满 30×24 小时的创建时间。
DUE = NOW - timedelta(days=30)
# 测试用的手机号（规范化的 E.164），用来断言它不进日志。
PHONE = "+60123456789"
OTHER_PHONE = "+6591234567"
SECRET = "sentinel-connection-detail"

_request_ids = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 关掉驱动的事务处理、由引擎发 BEGIN，事务边界才和 MySQL 上一样。
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
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> jobs.SessionFactory:
    return lambda: Session(engine)


def _attempt(
    engine: Engine,
    created_at: datetime,
    status: str = VERIFICATION_SENT,
    purpose: str = PURPOSE_CHECKOUT,
    phone: str = PHONE,
) -> int:
    """直接写一条短信验证记录并提交；返回 ID。提供方受理的状态带请求 ID，停发的不带。"""
    request_id = None
    if status != VERIFICATION_SUSPENDED:
        request_id = f"VE{next(_request_ids):032d}"
    with Session(engine) as db:
        attempt = VerificationAttempt(
            phone=phone,
            purpose=purpose,
            status=status,
            provider_request_id=request_id,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(attempt)
        db.commit()
        return attempt.id


def _remaining(engine: Engine) -> list[int]:
    with Session(engine) as db:
        return list(db.scalars(select(VerificationAttempt.id).order_by(VerificationAttempt.id)))


def _run(engine: Engine, now: datetime = NOW, limit: int = 100) -> int:
    with Session(engine) as db:
        count = delete_expired_verifications(db, now, limit)
        assert not db.in_transaction()
    return count


def _other_tables(engine: Engine) -> dict[str, list[tuple]]:
    """除 verification_attempts 以外每张表的全部行（按主键排序）。"""
    snapshot: dict[str, list[tuple]] = {}
    with Session(engine) as db:
        for table in Base.metadata.sorted_tables:
            if table.name == VerificationAttempt.__tablename__:
                continue
            stmt = select(table).order_by(*table.primary_key.columns)
            snapshot[table.name] = [tuple(row) for row in db.execute(stmt)]
    return snapshot


def _seed_other_tables(engine: Engine) -> None:
    """写一个会员（与验证记录同一号码）、他的会话、一行短信每日用量与一张游客订单，
    创建时间都早于保留期。"""
    old = DUE - timedelta(days=10)
    with Session(engine) as db:
        member = Member(phone=PHONE, password_hash=None, status=MEMBER_ACTIVE, created_at=old)
        db.add(member)
        db.flush()
        db.add(
            MemberSession(
                member_id=member.id,
                token_hash="a" * 64,
                created_at=old,
                expires_at=old + timedelta(days=30),
            )
        )
        db.add(
            SmsDailyUsage(
                usage_date=date(2026, 9, 29),
                sent_count=3,
                reserved_micro_usd=0,
                settled_micro_usd=150000,
            )
        )
        db.add(
            Order(
                order_number=generate_order_number(),
                status=STATUS_AWAITING_PAYMENT,
                subtotal_sen=1500,
                coupon_discount_sen=0,
                points_redeemed=0,
                shipping_fee_sen=800,
                total_sen=2300,
                points_earned=0,
                shipping_zone_code="MY-10",
                shipping_rate_version=1,
                idempotency_key=f"key-{generate_order_number()}",
                request_fingerprint="0" * 64,
                created_at=old,
                payment_expires_at=old + timedelta(minutes=15),
                paid_at=None,
            )
        )
        db.commit()


def _job_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


# ---------------------------------------------------------------------------
# 删除规则
# ---------------------------------------------------------------------------


def test_record_exactly_thirty_days_old_is_deleted(engine: Engine) -> None:
    """Kelvin 2026-10-08：「短信验证记录（含完整手机号）保留 30 天后删除。」SHOP-TASK-072 验收：
    created_at 不晚于当前 UTC 时间减 30 天的删除——恰满 30×24 小时即删除，更早的也删除。"""
    exact = _attempt(engine, DUE)
    older = _attempt(engine, DUE - timedelta(days=3))

    assert retention_cutoff(NOW) == DUE
    assert VERIFICATION_RETENTION == timedelta(days=30)
    assert _run(engine) == 2

    remaining = _remaining(engine)
    assert exact not in remaining
    assert older not in remaining


def test_record_one_second_short_of_thirty_days_is_kept(engine: Engine) -> None:
    """「资料保留」第 4 条：短信验证请求及发送记录「短期保留用于防滥用」；Kelvin 2026-10-08：保留
    30 天。SHOP-TASK-072 验收：差一秒未满 30 天的保留，一秒之后满 30 天照常删除。"""
    young = _attempt(engine, DUE + timedelta(seconds=1))
    fresh = _attempt(engine, NOW - timedelta(minutes=5))

    assert _run(engine) == 0
    assert _remaining(engine) == [young, fresh]

    assert _run(engine, NOW + timedelta(seconds=1)) == 1
    assert _remaining(engine) == [fresh]


@pytest.mark.parametrize("purpose", VERIFICATION_PURPOSES)
@pytest.mark.parametrize("status", VERIFICATION_STATUSES)
def test_every_status_and_purpose_is_deleted_by_time_alone(
    engine: Engine, status: str, purpose: str
) -> None:
    """Kelvin 2026-10-08：「短信验证记录（含完整手机号）保留 30 天后删除」不分结果与用途。
    SHOP-TASK-072 验收：各状态与用途一律按创建时间删除——满 30 天的删除，未满的保留。"""
    expired = _attempt(engine, DUE, status=status, purpose=purpose)
    kept = _attempt(engine, DUE + timedelta(seconds=1), status=status, purpose=purpose)

    assert _run(engine) == 1

    assert _remaining(engine) == [kept]
    assert expired not in _remaining(engine)


def test_other_tables_are_not_touched(engine: Engine) -> None:
    """「资料保留」第 4 条：「会员手机号及密码哈希保留至主动注销」——删除验证记录不能连带会员。
    SHOP-TASK-072 验收：不碰 SmsDailyUsage、会员与订单（除 verification_attempts 外每张表的
    每一行都不变，含与被删记录同一号码的会员及其会话）。"""
    _seed_other_tables(engine)
    _attempt(engine, DUE - timedelta(days=1))
    _attempt(engine, DUE, phone=OTHER_PHONE)
    before = _other_tables(engine)
    assert before["members"] and before["member_sessions"]
    assert before["sms_daily_usage"] and before["orders"]

    assert _run(engine) == 2

    assert _remaining(engine) == []
    assert _other_tables(engine) == before


# ---------------------------------------------------------------------------
# 每批上限与重复运行
# ---------------------------------------------------------------------------


def test_each_run_deletes_at_most_one_batch_oldest_first(engine: Engine) -> None:
    """「失败、并发与重试」第 6 条：可重复运行。SHOP-TASK-072 验收：每次最多删除一批，剩余的下一次
    继续；按创建时间从早到晚、同一时间按 ID 从小到大（ID 与时间先后故意相反）。"""
    latest = _attempt(engine, DUE)
    tie_low = _attempt(engine, DUE - timedelta(days=1))
    tie_high = _attempt(engine, DUE - timedelta(days=1))
    earliest = _attempt(engine, DUE - timedelta(days=2))
    kept = _attempt(engine, DUE + timedelta(seconds=1))

    assert _run(engine, limit=2) == 2
    assert _remaining(engine) == [latest, tie_high, kept]
    assert earliest not in _remaining(engine) and tie_low not in _remaining(engine)

    assert _run(engine, limit=2) == 2
    assert _remaining(engine) == [kept]

    assert _run(engine, limit=2) == 0
    assert _remaining(engine) == [kept]


def test_default_batch_limit_is_the_cancel_batch_limit(
    engine: Engine, factory: jobs.SessionFactory
) -> None:
    """Kelvin 2026-10-08：「短信验证记录（含完整手机号）保留 30 天后删除。」「失败、并发与重试」
    第 6 条：定时任务「可重复运行」——一次删不完的到期记录由下一次接着删，最终全部删除。
    SHOP-TASK-072 验收：每次最多删除一批，上限沿用 app/jobs.py 的 CANCEL_BATCH_LIMIT——默认
    任务一次删除上限条，多出的一条留到下一次。"""
    limit = jobs.CANCEL_BATCH_LIMIT
    assert jobs.VERIFICATION_DELETE_BATCH_LIMIT == limit
    for i in range(limit + 1):
        _attempt(engine, DUE - timedelta(seconds=limit + 1 - i))

    assert jobs.run_job(jobs.delete_verifications_job(), factory, NOW) == limit
    assert len(_remaining(engine)) == 1
    assert jobs.run_job(jobs.delete_verifications_job(), factory, NOW) == 1
    assert _remaining(engine) == []


def test_running_twice_does_not_fail_and_deletes_once(engine: Engine) -> None:
    """「失败、并发与重试」第 6 条：定时任务可重复运行、只生效一次。SHOP-TASK-072 验收：重复执行
    不出错——第二次没有到期记录、返回 0，函数返回时不留未结束的事务。"""
    _attempt(engine, DUE)
    kept = _attempt(engine, NOW)

    assert _run(engine) == 1
    assert _run(engine) == 0
    assert _run(engine) == 0

    assert _remaining(engine) == [kept]


def test_zero_limit_deletes_nothing(engine: Engine) -> None:
    """「资料保留」第 4 条：短信验证请求及发送记录「短期保留用于防滥用」——删除只能来自本次
    批次的额度，不能越过上限多删。「失败、并发与重试」第 6 条：「可重复运行」——额度为 0 的一次
    运行不出错，记录留给下一次。SHOP-TASK-072 验收：每次最多删除一批（上限 0 时一条也不删）。"""
    attempt_id = _attempt(engine, DUE)

    assert _run(engine, limit=0) == 0

    assert _remaining(engine) == [attempt_id]


# ---------------------------------------------------------------------------
# 定时任务与补跑命令
# ---------------------------------------------------------------------------


def test_job_is_registered_last_with_own_transactions() -> None:
    """「失败、并发与重试」第 6 条：可重复运行。SHOP-TASK-072 验收：default_jobs 在已有三项之后
    加 delete_expired_verifications，以 own_transactions=True 注册（服务自己提交）。"""
    default = jobs.default_jobs()

    assert [job.name for job in default][:3] == [
        "cancel_expired_orders",
        "reset_daily_stock",
        "auto_complete_shipped",
    ]
    assert default[3].name == "delete_expired_verifications"
    assert len(default) == 4
    assert default[3].own_transactions is True
    assert jobs.delete_verifications_job().own_transactions is True


def test_runner_round_deletes_expired_records_without_logging_phone(
    engine: Engine,
    factory: jobs.SessionFactory,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kelvin 2026-10-08：「保留 30 天后删除」；「权限与资料保护」：日志不记录完整电话。
    SHOP-TASK-072 验收：运行器每轮执行该任务，日志只含任务名与删除条数，不含手机号；第二轮
    没有到期记录、不再记日志。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _attempt(engine, DUE)
    kept = _attempt(engine, DUE + timedelta(seconds=1))
    heartbeat = tmp_path / "heartbeat"
    runner = jobs.Runner([jobs.delete_verifications_job()], factory, heartbeat, now=lambda: NOW)

    runner.run_round()
    runner.run_round()

    assert _remaining(engine) == [kept]
    assert _job_messages(caplog) == ["job delete_expired_verifications done: 1 changed"]
    assert all(PHONE not in message for message in caplog.messages)
    assert heartbeat.exists()


def test_delete_verifications_command_succeeds(
    engine: Engine, factory: jobs.SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收：delete-verifications 执行一次后以 0 退出，
    日志只含删除条数、不含手机号；再执行一次不出错（0 条，仍以 0 退出）。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _attempt(engine, DUE)
    _attempt(engine, DUE - timedelta(days=1), phone=OTHER_PHONE)
    kept = _attempt(engine, NOW)

    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK

    assert _remaining(engine) == [kept]
    assert _job_messages(caplog) == [
        "job delete_expired_verifications done: 2 changed",
        "job delete_expired_verifications done: 0 changed",
    ]
    assert all(PHONE not in message and OTHER_PHONE not in message for message in caplog.messages)


def test_delete_verifications_command_fails_with_one(caplog: pytest.LogCaptureFixture) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收：delete-verifications 失败以 1 退出（与
    complete-shipped 相同），日志只含任务名与异常类名、不含连接信息。"""
    caplog.set_level(logging.INFO, logger="app.jobs")

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(f"{SECRET} {PHONE}"))

    assert jobs.delete_verifications(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job delete_expired_verifications failed: OperationalError"]
    assert all(SECRET not in message and PHONE not in message for message in caplog.messages)


def test_main_delete_verifications_without_database_exits_two(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收：delete-verifications 子命令的退出码与
    complete-shipped 相同——未配置 SHOP_DATABASE_URL 时为 2。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))

    assert jobs.main(["delete-verifications"]) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]


def test_main_delete_verifications_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收：delete-verifications 经 SHOP_DATABASE_URL
    与会话工厂执行，失败以 1 退出，日志只含任务名与异常类名、不含连接串。这里的内存库没有建表，
    所以任务失败。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    url = "sqlite://"
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=url))

    assert jobs.main(["delete-verifications"]) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job delete_expired_verifications failed: OperationalError"]
    assert all(url not in message for message in caplog.messages)

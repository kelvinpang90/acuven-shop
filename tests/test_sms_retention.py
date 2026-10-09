"""短信验证记录满 30 天删除：app/services/sms_retention.py 与 app/jobs.py 的定时任务、补跑命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「资料保留」第 4 条：「短信验证请求及发送记录短期保留用于防滥用」。
- 「失败、并发与重试」第 6 条：定时任务「可重复运行……只生效一次；失败有告警与人工补跑办法」。
- 「权限与资料保护」：「应用日志与监控不记录……完整电话」。
以及 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（4）：「短信验证记录（含完整手机号）
保留 30 天后删除」。每条测试（参数化的是每个用例）的文档字符串写明它守住的设计原句或 Kelvin
决定；没有直接原句的，写明是 SHOP-TASK-072 验收里的约定。

用 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档关掉 pysqlite
的事务处理、由引擎发 BEGIN，与 tests/test_jobs.py 相同）按模型建表。内存库只有一条共享连接，
所以每个辅助函数用自己的会话、用完即结束事务。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, text
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
    VERIFICATION_APPROVED,
    VERIFICATION_PURPOSES,
    VERIFICATION_STATUSES,
    VERIFICATION_SUSPENDED,
)
from app.models.order import STATUS_AWAITING_PAYMENT
from app.services import sms_retention
from app.services.order_rules import generate_order_number
from app.services.sms_retention import delete_expired_verifications

NOW = datetime(2026, 11, 20, 3, 0, 0)
# 恰好满 30×24 小时的创建时间。
CUTOFF = NOW - timedelta(days=30)
PHONE_MY = "+60123456789"
PHONE_SG = "+6591234567"
# 代替连接信息与异常消息原文的标记串（不是真实的连接串），用来断言它们不进日志。
SECRET = "sentinel-connection-detail"
JOB_NAME = "delete_expired_verifications"


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
    *,
    phone: str = PHONE_MY,
    purpose: str = PURPOSE_CHECKOUT,
    status: str = VERIFICATION_APPROVED,
) -> int:
    """直接写一条短信验证记录并提交；返回 ID。经服务商受理的状态带请求 ID，停发的不带。"""
    request_id = None if status == VERIFICATION_SUSPENDED else f"VE{generate_order_number()}"
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
        db.flush()
        attempt_id = attempt.id
        db.commit()
    return attempt_id


def _remaining(engine: Engine) -> list[int]:
    with Session(engine) as db:
        return list(db.scalars(select(VerificationAttempt.id).order_by(VerificationAttempt.id)))


def _job_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


def _delete(engine: Engine, now: datetime = NOW, limit: int = jobs.CANCEL_BATCH_LIMIT) -> int:
    with Session(engine) as db:
        count = delete_expired_verifications(db, now, limit)
        db.commit()
    return count


# ---------------------------------------------------------------------------
# 删除规则
# ---------------------------------------------------------------------------


def test_exactly_thirty_days_is_deleted_one_second_less_is_kept(engine: Engine) -> None:
    """Kelvin 2026-10-08 决定（4）：「短信验证记录（含完整手机号）保留 30 天后删除」——创建恰好
    满 30×24 小时的删除，更早的删除，差一秒未满的保留。"""
    older = _attempt(engine, CUTOFF - timedelta(days=5))
    exactly = _attempt(engine, CUTOFF)
    one_second_short = _attempt(engine, CUTOFF + timedelta(seconds=1))
    fresh = _attempt(engine, NOW - timedelta(minutes=1))

    assert _delete(engine) == 2

    assert _remaining(engine) == [one_second_short, fresh]
    assert older not in _remaining(engine) and exactly not in _remaining(engine)


def test_kept_record_is_deleted_once_it_turns_thirty_days_old(engine: Engine) -> None:
    """Kelvin 2026-10-08 决定（4）：「保留 30 天后删除」——差一秒未满而保留的记录，一秒后
    恰满 30 天即删除。"""
    attempt_id = _attempt(engine, CUTOFF + timedelta(seconds=1))

    assert _delete(engine, NOW) == 0
    assert _remaining(engine) == [attempt_id]
    assert _delete(engine, NOW + timedelta(seconds=1)) == 1
    assert _remaining(engine) == []


@pytest.mark.parametrize("purpose", VERIFICATION_PURPOSES)
@pytest.mark.parametrize("status", VERIFICATION_STATUSES)
def test_every_status_and_purpose_is_deleted_by_age_alone(
    engine: Engine, status: str, purpose: str
) -> None:
    """DESIGN「资料保留」第 4 条：「短信验证请求及发送记录短期保留」，Kelvin 2026-10-08
    决定（4）：「短信验证记录……保留 30 天后删除」——不分状态（含待核验、停发）与用途，
    只按创建时间。"""
    expired = _attempt(engine, CUTOFF, status=status, purpose=purpose)
    fresh = _attempt(engine, CUTOFF + timedelta(seconds=1), status=status, purpose=purpose)

    assert _delete(engine) == 1

    assert _remaining(engine) == [fresh]
    assert expired not in _remaining(engine)


def test_batch_limit_deletes_oldest_first_and_leaves_the_rest(engine: Engine) -> None:
    """「失败、并发与重试」第 6 条：可重复运行。SHOP-TASK-072 验收约定：每次最多删除一批，剩余的
    下次继续；按创建时间从早到晚、同一时间按 ID 从小到大。"""
    first = _attempt(engine, CUTOFF - timedelta(days=2))
    same_time_a = _attempt(engine, CUTOFF - timedelta(days=1))
    same_time_b = _attempt(engine, CUTOFF - timedelta(days=1))
    # latest 的 ID 比 earliest 小但时间晚：按时间排在后面。
    latest = _attempt(engine, CUTOFF)
    earliest = _attempt(engine, CUTOFF - timedelta(days=3))

    # 第一批：earliest、first，再取同一时间里 ID 小的 same_time_a。
    assert _delete(engine, limit=3) == 3
    assert _remaining(engine) == [same_time_b, latest]
    assert {first, earliest, same_time_a}.isdisjoint(_remaining(engine))

    assert _delete(engine, limit=3) == 2
    assert _remaining(engine) == []
    assert _delete(engine, limit=3) == 0


def test_default_batch_limit_is_the_cancel_batch_limit(
    engine: Engine, factory: jobs.SessionFactory
) -> None:
    """SHOP-TASK-072 验收约定：每次最多删除一批，上限沿用 app/jobs.py 的 CANCEL_BATCH_LIMIT；
    补跑命令一次只删一批，再执行一次删掉剩余的。"""
    limit = jobs.CANCEL_BATCH_LIMIT
    assert jobs.VERIFICATION_DELETE_BATCH_LIMIT == limit
    # 第一条最新（恰满 30 天），其余依次早一秒。
    ids = [_attempt(engine, CUTOFF - timedelta(seconds=i)) for i in range(limit + 1)]

    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert _remaining(engine) == [ids[0]]
    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert _remaining(engine) == []


def test_non_positive_limit_deletes_nothing(engine: Engine) -> None:
    """SHOP-TASK-072 验收约定：每次最多删除一批——上限为 0 或负数时不删。"""
    attempt_id = _attempt(engine, CUTOFF)

    assert _delete(engine, limit=0) == 0
    assert _delete(engine, limit=-1) == 0
    assert _remaining(engine) == [attempt_id]


def test_timezone_aware_now_is_rejected(engine: Engine) -> None:
    """SHOP-TASK-072 验收约定：按当前 UTC 时间减 30 天判定（与库里一样不带时区）；带时区的时间
    被拒，不删除。"""
    attempt_id = _attempt(engine, CUTOFF)

    with Session(engine) as db, pytest.raises(ValueError):
        delete_expired_verifications(db, NOW.replace(tzinfo=UTC), 10)
    assert _remaining(engine) == [attempt_id]
    assert sms_retention.retention_cutoff(NOW) == CUTOFF


def test_other_tables_are_untouched(engine: Engine) -> None:
    """DESIGN「资料保留」第 4 条：「会员手机号及密码哈希保留至主动注销」；Kelvin 2026-10-08
    决定（4）只针对短信验证记录。SHOP-TASK-072 验收约定：不碰短信每日用量、会员与订单——
    同一号码的会员、会话、很久以前的每日用量与订单都原样保留。"""
    old = CUTOFF - timedelta(days=60)
    with Session(engine) as db:
        member = Member(phone=PHONE_MY, status=MEMBER_ACTIVE, created_at=old)
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
                usage_date=date(2026, 9, 1),
                sent_count=3,
                reserved_micro_usd=0,
                settled_micro_usd=150_000,
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
    _attempt(engine, old, phone=PHONE_MY)

    def snapshot() -> tuple:
        with Session(engine) as db:
            return (
                list(db.execute(select(Member.id, Member.phone, Member.status))),
                list(db.execute(select(MemberSession.id, MemberSession.revoked_at))),
                list(
                    db.execute(
                        select(
                            SmsDailyUsage.usage_date,
                            SmsDailyUsage.sent_count,
                            SmsDailyUsage.reserved_micro_usd,
                            SmsDailyUsage.settled_micro_usd,
                        )
                    )
                ),
                list(db.execute(select(Order.id, Order.status))),
            )

    before = snapshot()
    assert _delete(engine) == 1
    assert snapshot() == before
    assert before[0] == [(1, PHONE_MY, MEMBER_ACTIVE)]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(VerificationAttempt)) == 0


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------


def test_job_is_registered_after_existing_jobs() -> None:
    """「失败、并发与重试」第 6 条：定时任务可重复运行。SHOP-TASK-072 验收约定：default_jobs 在
    已有三项之后加上 delete_expired_verifications。"""
    default = jobs.default_jobs()

    assert [job.name for job in default] == [
        "cancel_expired_orders",
        "reset_daily_stock",
        "auto_complete_shipped",
        JOB_NAME,
    ]
    assert default[-1].own_transactions is True


def test_runner_round_deletes_and_repeats_without_error(
    engine: Engine, factory: jobs.SessionFactory, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败、并发与重试」第 6 条：定时任务「可重复运行……只生效一次」；Kelvin 2026-10-08
    决定（4）：「保留 30 天后删除」。运行器一轮删除到期记录，第二轮不出错、不再删除；
    日志只记任务名与条数，不含手机号。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _attempt(engine, CUTOFF, phone=PHONE_MY)
    _attempt(engine, CUTOFF - timedelta(days=1), phone=PHONE_SG)
    fresh = _attempt(engine, NOW, phone=PHONE_MY)
    heartbeat = tmp_path / "heartbeat"
    runner = jobs.Runner([jobs.delete_verifications_job()], factory, heartbeat, now=lambda: NOW)

    runner.run_round()
    runner.run_round()

    assert _remaining(engine) == [fresh]
    assert runner.last_success[JOB_NAME] is not None
    assert _job_messages(caplog) == [f"job {JOB_NAME} done: 2 changed"]
    assert all(PHONE_MY not in m and PHONE_SG not in m for m in caplog.messages)
    assert heartbeat.exists()


def test_failed_job_keeps_the_records_and_logs_class_name_only(
    engine: Engine,
    factory: jobs.SessionFactory,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败、并发与重试」第 6 条：失败有告警；「权限与资料保护」：日志不记录完整电话。
    删除失败时记录不丢失、下一轮重试，日志只含任务名与异常类名（异常消息里的手机号不进日志）。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    attempt_id = _attempt(engine, CUTOFF, phone=PHONE_MY)
    real = jobs.delete_expired_verifications

    def failing(db: Session, now: datetime, limit: int) -> int:
        real(db, now, limit)
        raise RuntimeError(f"cannot delete {PHONE_MY} {SECRET}")

    monkeypatch.setattr(jobs, "delete_expired_verifications", failing)
    runner = jobs.Runner(
        [jobs.delete_verifications_job()], factory, tmp_path / "heartbeat", now=lambda: NOW
    )
    runner.run_round()

    assert _remaining(engine) == [attempt_id]
    assert _job_messages(caplog) == [f"job {JOB_NAME} failed: RuntimeError"]
    assert all(PHONE_MY not in m and SECRET not in m for m in caplog.messages)

    monkeypatch.setattr(jobs, "delete_expired_verifications", real)
    runner.run_round()
    assert _remaining(engine) == []


# ---------------------------------------------------------------------------
# 补跑命令
# ---------------------------------------------------------------------------


def test_delete_verifications_command_succeeds_and_repeats(
    engine: Engine, factory: jobs.SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收约定：delete-verifications 执行一次后
    以 0 退出（写法沿用 complete-shipped），日志只含删除条数、不含手机号；再执行一次不出错。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _attempt(engine, CUTOFF, phone=PHONE_MY)
    fresh = _attempt(engine, CUTOFF + timedelta(seconds=1), phone=PHONE_SG)

    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert jobs.delete_verifications(factory, now=lambda: NOW) == jobs.EXIT_OK

    assert _remaining(engine) == [fresh]
    assert _job_messages(caplog) == [
        f"job {JOB_NAME} done: 1 changed",
        f"job {JOB_NAME} done: 0 changed",
    ]
    assert all(PHONE_MY not in m and PHONE_SG not in m for m in caplog.messages)


def test_delete_verifications_command_fails_with_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收约定：失败以 1 退出，日志只含任务名与
    异常类名、不含连接信息或手机号。"""
    caplog.set_level(logging.INFO, logger="app.jobs")

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(f"{SECRET} {PHONE_MY}"))

    assert jobs.delete_verifications(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == [f"job {JOB_NAME} failed: OperationalError"]
    assert all(SECRET not in m and PHONE_MY not in m for m in caplog.messages)


def test_main_delete_verifications_without_database_exits_two(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收约定：未配置 SHOP_DATABASE_URL 时
    delete-verifications 以 2 退出（与已有子命令相同）。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))

    assert jobs.main(["delete-verifications"]) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]


def test_main_delete_verifications_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败有告警与人工补跑办法」。SHOP-TASK-072 验收约定：delete-verifications 经
    SHOP_DATABASE_URL 与会话工厂执行，失败以 1 退出，日志不含连接串。这里的内存库没有建表，
    所以任务失败。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    url = "sqlite://"
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=url))

    assert jobs.main(["delete-verifications"]) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == [f"job {JOB_NAME} failed: OperationalError"]
    assert all(url not in message for message in caplog.messages)

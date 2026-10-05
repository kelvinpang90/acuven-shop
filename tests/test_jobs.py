"""定时任务运行器（app/jobs.py）：每轮的超时取消、失败处理、心跳、停止与手动补跑命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「计价、优惠、积分与库存」第 6 条、「订单与退款状态」
第 3 条与「失败、并发与重试」第 6 条。每条测试的文档字符串写明它守住的设计原句；没有直接原句的，
写明是 SHOP-TASK-031 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开），订单在测试里直接写库建立。
运行器的当前时间、计时与睡眠都换成本文件的 FakeClock，测试不真实等待；心跳文件写在 tmp_path。

SHOP-TASK-032 在默认任务列表的超时取消之后加入了每日库存重置，它会把当日可用库存重建为初始
库存减有效预留。检查超时取消加回库存件数的用例因此只运行超时取消任务（_cancel_only），断言不变；
库存重置在运行器里的行为见 tests/test_stock_reset.py。
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import Settings
from app.db.base import Base
from app.models import Category, Order, OrderEvent, OrderItem, Product, ProductVariant
from app.models.order import ACTOR_SYSTEM, STATUS_AWAITING_PAYMENT, STATUS_CANCELLED
from app.services.order_rules import generate_order_number

START = datetime(2026, 10, 5, 3, 0, 0)
PRICE = 1500
SHIPPING = 800
STOCK = 5
# 代替连接信息与异常消息原文的标记串（不是真实的连接串），用来断言它们不进日志。
SECRET = "sentinel-connection-detail"


def _cancel_only() -> list[jobs.Job]:
    """默认任务列表里的超时取消任务（不含之后的每日库存重置）。"""
    return [job for job in jobs.default_jobs() if job.name == "cancel_expired_orders"]


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # pysqlite 默认推迟 BEGIN，使最外层的 SAVEPOINT 自己开启事务、RELEASE 即提交；按
    # SQLAlchemy 文档的做法关掉驱动的事务处理、由引擎发 BEGIN，保存点才和 MySQL 上一样。
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
    """测试自己的会话。内存库只有一条共享连接，这个会话每次读写后都结束事务，
    不在任务运行时占着事务。"""
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


@pytest.fixture
def factory(engine: Engine) -> jobs.SessionFactory:
    return lambda: Session(engine)


@pytest.fixture
def heartbeat(tmp_path: Path) -> Path:
    return tmp_path / "heartbeat"


class FakeClock:
    """当前 UTC 时间与 monotonic 计时一起前进；sleep 只记下时长并前进，不真实等待。"""

    def __init__(self, start: datetime = START) -> None:
        self.wall = start
        self.mono = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _runner(
    job_list: list[jobs.Job],
    factory: jobs.SessionFactory | None,
    heartbeat: Path,
    clock: FakeClock,
) -> jobs.Runner:
    return jobs.Runner(
        job_list,
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def _variant(db: Session, stock: int = STOCK) -> int:
    category = Category(slug=f"cat-{generate_order_number()}", name_en="Cat", is_active=True)
    db.add(category)
    db.flush()
    product = Product(
        category_id=category.id,
        slug=f"p-{generate_order_number()}",
        name_en="Tee",
        is_active=True,
        max_per_order=10,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{generate_order_number()}",
        price_sen=PRICE,
        daily_initial_stock=stock + 10,
        available_stock=stock,
        is_active=True,
    )
    db.add(variant)
    db.flush()
    variant_id = variant.id
    db.commit()
    return variant_id


def _order(db: Session, variant_id: int, expires_at: datetime, quantity: int = 2) -> int:
    """直接写一张待支付的游客订单（一行）并提交；返回订单 ID。库存视为下单时已扣。"""
    subtotal = PRICE * quantity
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=subtotal,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=SHIPPING,
        total_sen=subtotal + SHIPPING,
        points_earned=0,
        shipping_zone_code="MY-10",
        shipping_rate_version=1,
        idempotency_key=f"key-{generate_order_number()}",
        request_fingerprint="0" * 64,
        created_at=expires_at - timedelta(minutes=15),
        payment_expires_at=expires_at,
        paid_at=None,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            line_index=0,
            variant_id=variant_id,
            sku="SKU",
            product_name_en="Tee",
            product_name_zh="T恤",
            product_name_ms="Tee",
            variant_label_en="",
            variant_label_zh="",
            variant_label_ms="",
            unit_price_sen=PRICE,
            quantity=quantity,
            line_subtotal_sen=subtotal,
        )
    )
    order_id = order.id
    db.commit()
    return order_id


def _status(db: Session, order_id: int) -> str:
    db.expire_all()
    status = db.get(Order, order_id).status
    db.rollback()
    return status


def _stock(db: Session, variant_id: int) -> int:
    db.expire_all()
    stock = db.get(ProductVariant, variant_id).available_stock
    db.rollback()
    return stock


def _order_number(db: Session, order_id: int) -> str:
    number = db.get(Order, order_id).order_number
    db.rollback()
    return number


def _cancel_events(db: Session, order_id: int) -> list[str]:
    stmt = select(OrderEvent.actor_type).where(
        OrderEvent.order_id == order_id, OrderEvent.to_status == STATUS_CANCELLED
    )
    actors = list(db.scalars(stmt))
    db.rollback()
    return actors


def _job_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


def _beat_once(runner: jobs.Runner, heartbeat: Path) -> bool:
    """执行一轮，返回这一轮是否写了心跳。"""
    heartbeat.unlink(missing_ok=True)
    runner.run_round()
    return heartbeat.exists()


class FlakyJob:
    """可切换成功或失败的任务；失败前先写库，以便检查回滚。"""

    def __init__(self, variant_id: int, fail: bool = True) -> None:
        self.variant_id = variant_id
        self.fail = fail
        self.calls = 0

    def run(self, db: Session, now: datetime) -> int:
        self.calls += 1
        variant = db.get(ProductVariant, self.variant_id)
        variant.available_stock += 100
        db.flush()
        if self.fail:
            raise RuntimeError(f"boom {SECRET}")
        return 1

    def job(self) -> jobs.Job:
        return jobs.Job(name="flaky", run=self.run)


# ---------------------------------------------------------------------------
# 超时取消
# ---------------------------------------------------------------------------


def test_round_cancels_overdue_orders_and_restores_stock(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """「计价、优惠、积分与库存」第 6 条：15 分钟未支付自动取消并释放。
    「订单与退款状态」第 3 条：待支付订单超时为 demo_cancelled，释放库存。"""
    variant_id = _variant(db)
    overdue = _order(db, variant_id, START - timedelta(seconds=1), quantity=2)
    due_now = _order(db, variant_id, START, quantity=1)
    fresh = _order(db, variant_id, START + timedelta(seconds=1), quantity=3)

    _runner(_cancel_only(), factory, heartbeat, clock).run_round()

    assert _status(db, overdue) == STATUS_CANCELLED
    assert _status(db, due_now) == STATUS_CANCELLED
    assert _status(db, fresh) == STATUS_AWAITING_PAYMENT
    assert _stock(db, variant_id) == STOCK + 2 + 1
    assert _cancel_events(db, overdue) == [ACTOR_SYSTEM]
    assert _cancel_events(db, fresh) == []
    assert heartbeat.exists()


def test_two_rounds_at_the_same_instant_take_effect_once(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """「失败、并发与重试」第 6 条：定时任务可重复运行，保证只生效一次。"""
    variant_id = _variant(db)
    overdue = _order(db, variant_id, START - timedelta(minutes=1))
    runner = _runner(_cancel_only(), factory, heartbeat, clock)

    runner.run_round()
    runner.run_round()

    assert _status(db, overdue) == STATUS_CANCELLED
    assert _stock(db, variant_id) == STOCK + 2
    assert _cancel_events(db, overdue) == [ACTOR_SYSTEM]


def test_round_limit_leaves_the_rest_for_the_next_round(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：每轮最多处理固定上限的订单数，剩余的下一轮继续
    （按到期时间从早到晚）。"""
    variant_id = _variant(db)
    first = _order(db, variant_id, START - timedelta(minutes=3))
    second = _order(db, variant_id, START - timedelta(minutes=2))
    third = _order(db, variant_id, START - timedelta(minutes=1))
    runner = _runner([jobs.cancel_expired_job(limit=2)], factory, heartbeat, clock)

    runner.run_round()
    assert [_status(db, i) for i in (first, second, third)] == [
        STATUS_CANCELLED,
        STATUS_CANCELLED,
        STATUS_AWAITING_PAYMENT,
    ]
    assert _stock(db, variant_id) == STOCK + 4

    clock.advance(60)
    runner.run_round()
    assert _status(db, third) == STATUS_CANCELLED
    assert _stock(db, variant_id) == STOCK + 6


def test_default_jobs_use_the_fixed_round_limit(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：run 每轮最多处理一个固定上限的订单数（CANCEL_BATCH_LIMIT）。
    SHOP-TASK-032 验收约定：每轮在超时取消之后执行每日库存重置（它本身会取消全部到期订单，
    所以这里只运行默认列表里的超时取消任务）。"""
    variant_id = _variant(db, stock=0)
    limit = jobs.CANCEL_BATCH_LIMIT
    order_ids = [
        _order(db, variant_id, START - timedelta(seconds=limit + 1 - i), quantity=1)
        for i in range(limit + 1)
    ]

    _runner(_cancel_only(), factory, heartbeat, clock).run_round()

    statuses = [_status(db, i) for i in order_ids]
    assert statuses.count(STATUS_CANCELLED) == limit
    assert statuses[-1] == STATUS_AWAITING_PAYMENT
    assert [job.name for job in jobs.default_jobs()] == [
        "cancel_expired_orders",
        "reset_daily_stock",
    ]


def test_default_clock_is_naive_utc() -> None:
    """SHOP-TASK-031 验收约定：当前时间取 UTC（与库里一样不带时区）。"""
    before = datetime.now(UTC).replace(tzinfo=None)
    value = jobs.utc_now()
    after = datetime.now(UTC).replace(tzinfo=None)
    assert value.tzinfo is None
    assert before <= value <= after


# ---------------------------------------------------------------------------
# 失败处理
# ---------------------------------------------------------------------------


def test_failing_job_rolls_back_and_the_next_round_runs(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败、并发与重试」第 6 条：可重复运行；SHOP-TASK-031 验收约定：任一轮任务失败只回滚、
    记一条只含任务名与异常类名的日志，不退出、不影响下一轮。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    flaky_variant = _variant(db)
    variant_id = _variant(db)
    overdue = _order(db, variant_id, START - timedelta(minutes=1))
    flaky = FlakyJob(flaky_variant)
    runner = _runner([flaky.job(), *_cancel_only()], factory, heartbeat, clock)

    runner.run_round()

    # 失败任务写的库存被回滚；同一轮的超时取消照常生效。
    assert _stock(db, flaky_variant) == STOCK
    assert _status(db, overdue) == STATUS_CANCELLED
    assert "job flaky failed: RuntimeError" in _job_messages(caplog)
    assert all(SECRET not in message and "boom" not in message for message in caplog.messages)

    flaky.fail = False
    clock.advance(60)
    runner.run_round()

    assert flaky.calls == 2
    assert _stock(db, flaky_variant) == STOCK + 100


def test_failure_after_committed_cancellations_rolls_back_the_whole_run(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：每轮的任务在独立事务里提交，任务失败只回滚——
    expire_overdue_orders 逐张提交，同一次运行里之后失败时，已取消的订单也一起回滚。"""
    variant_id = _variant(db)
    first = _order(db, variant_id, START - timedelta(minutes=2))
    second = _order(db, variant_id, START - timedelta(minutes=1))

    def cancel_then_fail(session: Session, now: datetime) -> int:
        assert jobs.cancel_expired_job().run(session, now) == 2
        raise RuntimeError("after cancelling")

    failing = jobs.Job(name="cancel_then_fail", run=cancel_then_fail)
    _runner([failing], factory, heartbeat, clock).run_round()

    assert [_status(db, i) for i in (first, second)] == [STATUS_AWAITING_PAYMENT] * 2
    assert _stock(db, variant_id) == STOCK
    assert _cancel_events(db, first) == []
    assert _cancel_events(db, second) == []

    # 下一轮的超时取消照常生效，且只生效一次。
    _runner(_cancel_only(), factory, heartbeat, clock).run_round()
    assert [_status(db, i) for i in (first, second)] == [STATUS_CANCELLED] * 2
    assert _stock(db, variant_id) == STOCK + 4
    assert _cancel_events(db, first) == [ACTOR_SYSTEM]


def test_database_unavailable_is_logged_by_class_name_only(
    heartbeat: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-031 验收约定：任务失败（含数据库不可用）不退出，日志不含连接串或异常消息原文。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    calls = []

    def broken() -> Session:
        calls.append(1)
        raise OperationalError("SELECT 1", {}, Exception(f"cannot reach {SECRET}"))

    runner = _runner(jobs.default_jobs(), broken, heartbeat, clock)
    runner.run_round()
    clock.advance(60)
    runner.run_round()

    # 每轮两个任务（超时取消与 SHOP-TASK-032 的每日库存重置）各开一次会话、各记一条失败。
    assert len(calls) == 4
    each_round = [
        "job cancel_expired_orders failed: OperationalError",
        "job reset_daily_stock failed: OperationalError",
    ]
    assert _job_messages(caplog) == each_round * 2
    assert all(SECRET not in message for message in caplog.messages)


def test_unconfigured_database_warns_every_round_and_continues(
    heartbeat: Path, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-031 验收约定：未配置数据库时 run 每轮记一条不含连接信息的警告并继续；
    任务按失败计时（偏离见 docs/TODO.md），满 10 分钟后不再写心跳。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    runner = _runner(jobs.default_jobs(), None, heartbeat, clock)

    assert _beat_once(runner, heartbeat)
    clock.advance(60)
    assert _beat_once(runner, heartbeat)
    assert _job_messages(caplog) == ["database is not configured; jobs skipped"] * 2

    clock.advance(600)
    assert not _beat_once(runner, heartbeat)


# ---------------------------------------------------------------------------
# 心跳
# ---------------------------------------------------------------------------


def test_heartbeat_stops_after_ten_minutes_of_failure_and_resumes(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """「失败、并发与重试」第 6 条：失败有告警。SHOP-TASK-031 验收约定：连续失败距进程启动不满
    10 分钟时心跳照写，满 10 分钟后不再写，该任务再次成功后恢复（以容器不健康代替告警）。"""
    flaky = FlakyJob(_variant(db))
    runner = _runner([flaky.job()], factory, heartbeat, clock)

    # 进程启动即开始失败：第 0 到第 540 秒的各轮照写心跳。
    for _ in range(10):
        assert _beat_once(runner, heartbeat)
        clock.advance(60)
    # 第 600 秒：满 10 分钟。
    assert not _beat_once(runner, heartbeat)
    assert not runner.healthy()

    clock.advance(60)
    flaky.fail = False
    assert _beat_once(runner, heartbeat)
    assert runner.healthy()


def test_heartbeat_counts_from_the_most_recent_success(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：运行器记下每个任务最近一次成功的时间，连续失败距它满 10 分钟
    才不再写心跳；另一个一直成功的任务不抵消失败的任务。"""
    flaky = FlakyJob(_variant(db), fail=False)
    steady = jobs.Job(name="steady", run=lambda _db, _now: 0)
    runner = _runner([flaky.job(), steady], factory, heartbeat, clock)

    runner.run_round()
    clock.advance(120)
    runner.run_round()
    last_success = clock.monotonic()
    assert runner.last_success["flaky"] == last_success

    flaky.fail = True
    clock.advance(60)
    while clock.monotonic() - last_success < jobs.UNHEALTHY_AFTER_SECONDS:
        assert _beat_once(runner, heartbeat)
        clock.advance(60)
    assert clock.monotonic() - last_success == jobs.UNHEALTHY_AFTER_SECONDS
    assert not _beat_once(runner, heartbeat)


def test_skipping_counts_as_success(
    heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：判定无需执行而跳过也算成功（没有到期订单时照写心跳）。"""
    runner = _runner(jobs.default_jobs(), factory, heartbeat, clock)
    clock.advance(jobs.UNHEALTHY_AFTER_SECONDS * 2)
    assert _beat_once(runner, heartbeat)
    assert runner.last_success["cancel_expired_orders"] == clock.monotonic()


# ---------------------------------------------------------------------------
# 循环、睡眠与停止
# ---------------------------------------------------------------------------


def test_loop_sleeps_until_sixty_seconds_after_round_start(
    heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：每轮先执行任务、再写心跳，然后睡到距本轮开始满 60 秒。"""
    order: list[str] = []

    def slow(_db: Session, _now: datetime) -> int:
        order.append("job")
        assert not heartbeat.exists()
        clock.advance(5)
        return 0

    def sleep(seconds: float) -> None:
        order.append("sleep")
        assert heartbeat.exists()
        heartbeat.unlink()
        clock.sleep(seconds)
        if len(clock.sleeps) == 3:
            runner.request_stop()

    runner = jobs.Runner(
        [jobs.Job(name="slow", run=slow)],
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=sleep,
    )
    runner.run_forever()

    assert order == ["job", "sleep"] * 3
    assert clock.sleeps == [55.0, 55.0, 55.0]


def test_stop_during_a_round_starts_no_new_round(
    heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：收到 SIGTERM 或 SIGINT 时不再开始新一轮，在 10 秒内退出
    （不再睡眠）。"""
    calls: list[int] = []

    def stopping(_db: Session, _now: datetime) -> int:
        calls.append(1)
        runner.request_stop(signal.SIGTERM, None)
        return 0

    runner = _runner([jobs.Job(name="stopping", run=stopping)], factory, heartbeat, clock)
    runner.run_forever()

    assert calls == [1]
    assert clock.sleeps == []


@pytest.mark.parametrize("signum", ["SIGTERM", "SIGINT"])
def test_signals_request_stop(
    signum: str, heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：收到 SIGTERM 或 SIGINT 时不再开始新一轮。"""
    sig = getattr(signal, signum)
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    rounds: list[int] = []

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        signal.raise_signal(sig)

    runner = jobs.Runner(
        [jobs.Job(name="count", run=lambda _db, _now: rounds.append(1) or 0)],
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=sleep,
    )
    try:
        jobs.install_signal_handlers(runner)
        runner.run_forever()
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)

    assert runner.stop_event.is_set()
    assert rounds == [1]


class FakeTimer:
    """代替看门狗定时器：记下时长与回调，由测试决定何时“到时”。"""

    def __init__(self) -> None:
        self.started: list[tuple[float, Callable[[], None]]] = []
        self.cancelled = False

    def __call__(self, seconds: float, callback: Callable[[], None]) -> FakeTimer:
        self.started.append((seconds, callback))
        return self

    def cancel(self) -> None:
        self.cancelled = True


class ForcedExit(BaseException):
    pass


def test_stop_during_a_blocked_round_forces_exit_within_ten_seconds(
    heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：收到 SIGTERM 或 SIGINT 时在 10 秒内退出——一轮卡住（例如数据库
    调用不返回）时，停止宽限期到即强制退出，不等这一轮做完。"""
    timer = FakeTimer()
    exits: list[int] = []

    def force_exit() -> None:
        exits.append(1)
        raise ForcedExit

    def blocked(_db: Session, _now: datetime) -> int:
        runner.request_stop(signal.SIGTERM, None)
        # 任务仍未返回时宽限期到。
        callback = timer.started[0][1]
        callback()
        raise AssertionError("not reached")

    runner = jobs.Runner(
        [jobs.Job(name="blocked", run=blocked)],
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        force_exit=force_exit,
        timer=timer,
    )
    with pytest.raises(ForcedExit):
        runner.run_forever()

    assert len(timer.started) == 1
    assert timer.started[0][0] == jobs.SHUTDOWN_GRACE_SECONDS < 10
    assert exits == [1]


def test_watchdog_is_cancelled_when_the_round_finishes_in_time(
    heartbeat: Path, factory: jobs.SessionFactory, clock: FakeClock
) -> None:
    """SHOP-TASK-031 验收约定：收到停止信号后不再开始新一轮；这一轮在宽限期内做完时正常退出，
    看门狗取消，不强制退出。重复的信号不另起看门狗。"""
    timer = FakeTimer()
    exits: list[int] = []

    def stopping(_db: Session, _now: datetime) -> int:
        runner.request_stop(signal.SIGTERM, None)
        runner.request_stop(signal.SIGINT, None)
        return 0

    runner = jobs.Runner(
        [jobs.Job(name="stopping", run=stopping)],
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        force_exit=lambda: exits.append(1),
        timer=timer,
    )
    runner.run_forever()

    assert len(timer.started) == 1
    assert timer.cancelled
    assert exits == []
    assert clock.sleeps == []


def test_force_exit_ends_the_process_with_a_nonzero_code(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-031 验收约定：停止宽限期到时立即退出；日志不含异常消息或连接信息。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    codes: list[int] = []
    monkeypatch.setattr(jobs.os, "_exit", codes.append)

    jobs.force_exit()

    assert codes == [jobs.EXIT_FAILED]
    assert _job_messages(caplog) == ["round did not finish within 8 seconds of stop; exiting"]


def test_default_timer_is_a_daemon_thread() -> None:
    """SHOP-TASK-031 验收约定：看门狗不阻止进程在正常停止时退出。"""
    timer = jobs.start_timer(60.0, lambda: None)
    try:
        assert isinstance(timer, threading.Timer)
        assert timer.daemon
    finally:
        timer.cancel()


def test_run_forever_removes_a_stale_heartbeat(heartbeat: Path, clock: FakeClock) -> None:
    """「失败、并发与重试」第 6 条：失败有告警——上一次运行留下的心跳不代表这个进程。"""
    heartbeat.write_text("old\n", encoding="ascii")
    runner = _runner([], None, heartbeat, clock)
    runner.request_stop()
    runner.run_forever()
    assert not heartbeat.exists()


# ---------------------------------------------------------------------------
# 手动补跑命令
# ---------------------------------------------------------------------------


def test_cancel_expired_command_succeeds(
    db: Session, factory: jobs.SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """「失败、并发与重试」第 6 条：失败有人工补跑办法——cancel-expired 执行一次后以 0 退出，
    日志只含任务名与取消张数。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    variant_id = _variant(db)
    overdue = _order(db, variant_id, START - timedelta(minutes=1))
    number = _order_number(db, overdue)

    assert jobs.cancel_expired(factory, now=lambda: START) == jobs.EXIT_OK
    assert _status(db, overdue) == STATUS_CANCELLED
    assert _job_messages(caplog) == ["job cancel_expired_orders done: 1 changed"]
    assert all(number not in message for message in caplog.messages)

    # 再执行一次不重复生效。
    assert jobs.cancel_expired(factory, now=lambda: START) == jobs.EXIT_OK
    assert _stock(db, variant_id) == STOCK + 2


def test_cancel_expired_command_fails_nonzero_on_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SHOP-TASK-031 验收约定：cancel-expired 失败以非零退出，日志只含任务名与异常类名。"""
    caplog.set_level(logging.INFO, logger="app.jobs")

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(SECRET))

    assert jobs.cancel_expired(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job cancel_expired_orders failed: OperationalError"]


def test_main_cancel_expired_without_database_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-031 验收约定：SHOP_DATABASE_URL 未配置时 cancel-expired 以非零退出。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))
    assert jobs.main(["cancel-expired"]) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]


def test_main_cancel_expired_uses_the_session_factory(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SHOP-TASK-031 验收约定：连接沿用 SHOP_DATABASE_URL 与会话工厂；失败日志不含连接串。
    这里的内存库没有建表，所以任务失败、以非零退出。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    url = "sqlite://"
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=url))
    assert jobs.main(["cancel-expired"]) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job cancel_expired_orders failed: OperationalError"]
    assert all(url not in message for message in caplog.messages)


def test_main_requires_a_subcommand() -> None:
    """SHOP-TASK-031 验收约定：提供 run 与 cancel-expired 两个子命令；SHOP-TASK-032 加了
    reset-stock（见 tests/test_stock_reset.py），未知子命令仍被拒绝。"""
    with pytest.raises(SystemExit) as raised:
        jobs.main([])
    assert raised.value.code != 0
    with pytest.raises(SystemExit) as raised:
        jobs.main(["reset-everything"])
    assert raised.value.code != 0

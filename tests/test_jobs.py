"""定时任务运行器（app/jobs.py）：每轮的超时取消、失败处理、心跳、停止与手动补跑命令。

依据 docs/DESIGN.md 1.11（提交 2d13250）：「计价、优惠、积分与库存」第 6 条、「订单与退款状态」
第 3 条与「失败、并发与重试」第 6 条。每条测试的文档字符串写明它守住的设计原句；没有直接原句的，
写明是 SHOP-TASK-031 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开），订单在测试里直接写库建立。
运行器的当前时间、计时与睡眠都换成本文件的 FakeClock，测试不真实等待；心跳文件写在 tmp_path。

SHOP-TASK-032 在默认任务列表的超时取消之后加入了每日库存重置，它会把当日可用库存重建为初始
库存减有效预留。检查超时取消加回库存件数的用例因此只运行超时取消任务（_cancel_only），断言不变；
库存重置在运行器里的行为见 tests/test_stock_reset.py。

SHOP-TASK-097 起超时取消任务自己管理事务（own_transactions），逐张订单真实提交；新增的用例写明
它守住的是本任务的哪条约定。自定义的非 own_transactions 任务仍以保存点执行、失败时整次回滚。

SHOP-TASK-116 在超时取消之后加入了 Stripe 会话的定时处理（stripe_checkouts）；它的各情形见
tests/test_stripe_overdue.py，这里只测任务顺序、未配置时的跳过与手动补跑命令。

SHOP-TASK-126 在积分到期之后追加了 Stripe 退款尝试的定时处理（stripe_refunds）；它的各情形
见 tests/test_stripe_refund_overdue.py，这里同样只测任务顺序、未配置时的跳过与手动补跑命令。

SHOP-TASK-217 加了 send-test-alert 子命令，并让其余一次性子命令退出前等运营告警队列；这里测
退出码、不受限频、等队列与到时记下未发封数、run 不等队列（依据 DESIGN 1.21「运营告警邮件」
第 1、2 条）。发信本身的各情形见 tests/test_ops_alert.py；smtplib.SMTP 以替身代替，不连服务器。

SHOP-TASK-218 在常驻循环里接入 job_failing 与 stock_reset_failed（DESIGN 1.21「运营告警邮件」
第 6、7 条与 HANDOFF 0.88）；这些用例以 monkeypatch 替换 ops_alert.alert 记下调用，不入队。
其余用例里任务连续失败满 10 分钟时照常调用真实的 alert（它不抛异常、不阻塞，告警邮件未配置时
只记一条 warning、不入队），断言只看 app.jobs 的日志，不受影响。
"""

from __future__ import annotations

import logging
import signal
import smtplib
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Category,
    Member,
    Order,
    OrderEvent,
    OrderItem,
    PointEntry,
    Product,
    ProductVariant,
    StripeCheckout,
    StripeRefund,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_SYSTEM,
    CLAIM_NOT_CLAIMABLE,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.points import KIND_EXPIRE
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    PAYMENT_UNPAID,
    SOURCE_CHECKOUT,
    STRIPE_REFUND_PENDING,
    STRIPE_REFUND_SUCCEEDED,
)
from app.services import ops_alert, payment
from app.services.order_rules import generate_order_number
from app.services.points import earn_points

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


def _points_lot(db: Session, acquired_at: datetime) -> int:
    """Create an active member's non-claimable order and its earned-points lot."""
    member = Member(
        phone=f"+601{generate_order_number()[-8:]}", status=MEMBER_ACTIVE, created_at=START
    )
    db.add(member)
    db.flush()
    order_id = _order(db, _variant(db), START)
    order = db.get(Order, order_id)
    assert order is not None
    order.member_id = member.id
    order.claim_status = CLAIM_NOT_CLAIMABLE
    db.flush()
    lot = earn_points(db, member.id, order_id, 10, acquired_at)
    assert lot is not None
    lot_id = lot.id
    db.commit()
    return lot_id


def _expired_entries(db: Session) -> list[int]:
    values = list(db.scalars(select(PointEntry.lot_id).where(PointEntry.kind == KIND_EXPIRE)))
    db.rollback()
    return values


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
    default = jobs.default_jobs()
    # SHOP-TASK-116 在超时取消之后插入了 stripe_checkouts，之后各项后移一位、任务数改为 6。
    assert [job.name for job in default][:5] == [
        "cancel_expired_orders",
        "stripe_checkouts",
        "reset_daily_stock",
        "auto_complete_shipped",
        "delete_expired_verifications",
    ]
    # SHOP-TASK-126 在积分到期之后追加了 stripe_refunds，任务数由 6 改为 7。
    assert len(default) == 7
    assert default[5].name == "expire_points"


def test_points_expiry_job_is_registered_after_existing_jobs() -> None:
    """DESIGN 1.11「失败、并发与重试」第 6 条：定时任务可重复运行、只生效一次。"""
    default = jobs.default_jobs()

    # SHOP-TASK-116 在超时取消之后插入了 stripe_checkouts，积分到期由下标 4 移到 5。
    # SHOP-TASK-126 在积分到期之后追加了 stripe_refunds，积分到期仍在下标 5。
    assert [job.name for job in default] == [
        "cancel_expired_orders",
        "stripe_checkouts",
        "reset_daily_stock",
        "auto_complete_shipped",
        "delete_expired_verifications",
        "expire_points",
        "stripe_refunds",
    ]
    assert default[5].own_transactions is False
    assert jobs.POINTS_EXPIRE_BATCH_LIMIT == jobs.CANCEL_BATCH_LIMIT


def test_points_expiry_job_expires_overdue_lots_once(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock
) -> None:
    """DESIGN 1.11「计价、优惠、积分与库存」第 3 条：积分批次取得后 12 个月到期且只生效一次。"""
    overdue = _points_lot(db, START - timedelta(days=400))
    current = _points_lot(db, START)
    runner = _runner([jobs.expire_points_job()], factory, heartbeat, clock)

    runner.run_round()
    assert _expired_entries(db) == [overdue]
    clock.advance(60)
    runner.run_round()
    assert _expired_entries(db) == [overdue]
    assert current not in _expired_entries(db)


def test_points_expiry_job_continues_remaining_lots_next_round(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DESIGN 1.11「计价、优惠、积分与库存」第 3 条：到期批次分轮处理，余下留给下一轮。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    lot_ids = [_points_lot(db, START - timedelta(days=400)) for _ in range(3)]
    runner = _runner([jobs.expire_points_job(limit=2)], factory, heartbeat, clock)

    runner.run_round()
    assert _expired_entries(db) == lot_ids[:2]
    clock.advance(60)
    runner.run_round()
    assert _expired_entries(db) == lot_ids
    assert _job_messages(caplog) == [
        "job expire_points done: 2 changed",
        "job expire_points done: 1 changed",
    ]


def test_points_expiry_failure_rolls_back_the_round(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DESIGN 1.11「失败、并发与重试」第 6 条：失败的一轮不部分生效。"""
    _points_lot(db, START - timedelta(days=400))
    caplog.set_level(logging.INFO, logger="app.jobs")
    real = jobs.expire_points

    def broken(session: Session, now: datetime, *, batch_size: int) -> int:
        real(session, now, batch_size=batch_size)
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "expire_points", broken)
    _runner([jobs.expire_points_job()], factory, heartbeat, clock).run_round()

    assert _expired_entries(db) == []
    assert _job_messages(caplog) == ["job expire_points failed: RuntimeError"]


def test_main_expire_points_command_succeeds(
    db: Session, factory: jobs.SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DESIGN 1.11「失败、并发与重试」第 6 条：手动命令提供失败后的补跑办法。"""
    lot_id = _points_lot(db, START - timedelta(days=400))
    monkeypatch.setattr(jobs, "configured_session_factory", lambda: factory)

    assert jobs.main(["expire-points"]) == jobs.EXIT_OK
    assert _expired_entries(db) == [lot_id]


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


def test_cancel_job_runs_in_its_own_transactions(
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SHOP-TASK-097 约定：超时取消任务改为 own_transactions——运行器（与 cancel-expired 命令）
    给它不在事务中的新会话，expire_overdue_orders 的逐张提交即真实提交；任务名与每轮上限不变。"""
    seen: list[tuple[bool, int | None]] = []

    def spy(session: Session, now: datetime, *, limit: int | None = None) -> int:
        seen.append((session.in_transaction(), limit))
        return 0

    monkeypatch.setattr(jobs, "expire_overdue_orders", spy)
    job = _cancel_only()[0]
    assert job.own_transactions
    assert job.name == "cancel_expired_orders"

    _runner([job], factory, heartbeat, clock).run_round()
    assert jobs.cancel_expired(factory, now=lambda: START) == jobs.EXIT_OK

    assert seen == [(False, jobs.CANCEL_BATCH_LIMIT)] * 2


def test_each_cancellation_in_a_round_is_committed(
    engine: Engine,
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「失败、并发与重试」第 1 条：库存预占在同一事务——每张订单的取消与加回库存在它自己的事务里。
    SHOP-TASK-097 约定：同一轮里每张订单取消后即已提交，取消下一张之前另开会话就能读到（内存库
    只有一条共享连接：若仍有未提交的外层事务，另开会话的 BEGIN 会出错）。第一张之前不读：候选
    扫描与第一张的取消本来就在同一个事务里。「失败、并发与重试」第 6 条：重复运行只生效一次。"""
    variant_id = _variant(db)
    order_ids = [
        _order(db, variant_id, START - timedelta(minutes=3 - i), quantity=1) for i in range(3)
    ]
    real_cancel = payment._cancel
    calls: list[int] = []
    observed: list[list[str]] = []

    def observe_then_cancel(*args: Any, **kwargs: Any) -> bool:
        calls.append(1)
        if len(calls) > 1:
            with Session(engine) as other:
                stmt = select(Order.status).where(Order.id.in_(order_ids)).order_by(Order.id)
                observed.append(list(other.scalars(stmt)))
        return real_cancel(*args, **kwargs)

    monkeypatch.setattr(payment, "_cancel", observe_then_cancel)
    runner = _runner(_cancel_only(), factory, heartbeat, clock)

    runner.run_round()
    runner.run_round()

    a, c = STATUS_AWAITING_PAYMENT, STATUS_CANCELLED
    assert len(calls) == 3
    assert observed == [[c, a, a], [c, c, a]]
    assert [_status(db, i) for i in order_ids] == [c] * 3
    assert _stock(db, variant_id) == STOCK + 3
    assert all(_cancel_events(db, i) == [ACTOR_SYSTEM] for i in order_ids)


def test_cancel_job_failure_keeps_earlier_cancellations(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SHOP-TASK-097 约定（取代 SHOP-TASK-031 里超时取消任务失败时整次回滚的语义）：某张订单取消
    失败抛异常时，之前已取消的订单保留（各自已提交），本轮记失败（只含任务名与异常类名）、下一轮
    继续；「失败、并发与重试」第 6 条：只生效一次。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    variant_id = _variant(db)
    first = _order(db, variant_id, START - timedelta(minutes=2))
    second = _order(db, variant_id, START - timedelta(minutes=1))
    real_cancel = payment._cancel
    calls: list[int] = []

    def fail_second(*args: Any, **kwargs: Any) -> bool:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError(f"boom {SECRET}")
        return real_cancel(*args, **kwargs)

    monkeypatch.setattr(payment, "_cancel", fail_second)
    runner = _runner(_cancel_only(), factory, heartbeat, clock)

    runner.run_round()

    assert _status(db, first) == STATUS_CANCELLED
    assert _status(db, second) == STATUS_AWAITING_PAYMENT
    assert _stock(db, variant_id) == STOCK + 2
    assert _cancel_events(db, first) == [ACTOR_SYSTEM]
    assert "cancel_expired_orders" not in runner.last_success
    assert _job_messages(caplog) == ["job cancel_expired_orders failed: RuntimeError"]
    assert all(SECRET not in message for message in caplog.messages)

    clock.advance(60)
    runner.run_round()

    assert _status(db, second) == STATUS_CANCELLED
    assert _stock(db, variant_id) == STOCK + 4
    assert _cancel_events(db, first) == [ACTOR_SYSTEM]
    assert _cancel_events(db, second) == [ACTOR_SYSTEM]
    assert runner.last_success["cancel_expired_orders"] == clock.monotonic()


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

    # 每轮六个任务（超时取消、SHOP-TASK-116 的 Stripe 会话定时处理、SHOP-TASK-032 的每日库存
    # 重置、SHOP-TASK-042 的发货满 7 天自动确认收货、SHOP-TASK-072 的删除满 30 天短信验证记录
    # 与 SHOP-TASK-096 的积分到期）各开一次会话、各记一条失败；后三项依次在默认列表末尾加入，
    # SHOP-TASK-116 插在超时取消之后，所以会话数由 10 改为 12、每轮多一条失败日志。
    # SHOP-TASK-126 在积分到期之后追加了 Stripe 退款尝试的定时处理（stripe_refunds），
    # 每轮七个任务，会话数由 12 改为 14、每轮末尾多一条失败日志。
    assert len(calls) == 14
    each_round = [
        "job cancel_expired_orders failed: OperationalError",
        "job stripe_checkouts failed: OperationalError",
        "job reset_daily_stock failed: OperationalError",
        "job auto_complete_shipped failed: OperationalError",
        "job delete_expired_verifications failed: OperationalError",
        "job expire_points failed: OperationalError",
        "job stripe_refunds failed: OperationalError",
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


# ---------------------------------------------------------------------------
# Stripe 会话的定时处理（SHOP-TASK-116）
# ---------------------------------------------------------------------------


def _unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stripe 未配置（不读本地 .env）。"""
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))


def _stripe_record(
    db: Session, order_id: int, status: str, *, paid: bool = False, expires_at: datetime = START
) -> int:
    """直接写一条会话记录（open 或 complete）并提交；返回记录 ID。"""
    total = db.get(Order, order_id).total_sen
    record = StripeCheckout(
        order_id=order_id,
        idempotency_key=f"create-key-{order_id}",
        checkout_session_id=f"cs_test_{order_id}",
        amount_sen=total,
        currency="myr",
        status=status,
        payment_status=PAYMENT_PAID if paid else PAYMENT_UNPAID,
        checkout_url=f"https://checkout.stripe.example.com/c/pay/cs_test_{order_id}",
        create_params="[]",
        expires_at=expires_at,
        created_at=expires_at - timedelta(minutes=31),
        completed_at=expires_at if status == CHECKOUT_COMPLETE else None,
        active_order_id=order_id,
    )
    db.add(record)
    db.flush()
    # 提交前取 ID：提交后读属性会重新加载、让共享连接上留下未结束的事务。
    checkout_id = record.id
    db.commit()
    return checkout_id


def _last_polled(db: Session, checkout_id: int) -> datetime | None:
    db.expire_all()
    value = db.get(StripeCheckout, checkout_id).last_polled_at
    db.rollback()
    return value


def test_stripe_job_runs_right_after_cancel_with_own_transactions() -> None:
    """DESIGN 1.12「Stripe 测试环境支付」第 5 条「所有取消入口（……定时任务）用同一
    规则」；SHOP-TASK-116 验收：app/jobs.py 在超时取消之后加入 stripe_checkouts，
    own_transactions，两种扫描每轮各最多 CANCEL_BATCH_LIMIT 条。"""
    default = jobs.default_jobs()

    assert [job.name for job in default][:3] == [
        "cancel_expired_orders",
        "stripe_checkouts",
        "reset_daily_stock",
    ]
    assert default[1].own_transactions is True
    assert jobs.stripe_checkouts_job().own_transactions is True
    assert jobs.STRIPE_CHECKOUT_BATCH_LIMIT == jobs.CANCEL_BATCH_LIMIT


def test_stripe_job_skips_when_unconfigured_and_nothing_is_pending(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 4 条「由定时任务之后向 Stripe 重查补齐」只是补字段；SHOP-TASK-116 验收：
    Stripe 未配置时没有待处理的记录才静默跳过、算成功——只需补齐付款方式类型的
    记录（订单已是 demo_paid）不算待处理，不建适配器、不写上次查询时间。"""
    _unconfigured(monkeypatch)
    order_id = _order(db, _variant(db), START - timedelta(minutes=30))
    order = db.get(Order, order_id)
    order.status = STATUS_PAID
    order.paid_at = START - timedelta(minutes=20)
    db.commit()
    checkout_id = _stripe_record(db, order_id, CHECKOUT_COMPLETE, paid=True)
    built: list[Settings] = []

    def factory_spy(settings: Settings) -> Any:
        built.append(settings)
        raise AssertionError("gateway must not be built")

    job = jobs.stripe_checkouts_job(gateway_factory=factory_spy)
    runner = _runner([job], factory, heartbeat, clock)

    assert _beat_once(runner, heartbeat)
    assert runner.last_success["stripe_checkouts"] == clock.monotonic()
    assert built == []
    assert _last_polled(db, checkout_id) is None


def test_stripe_job_handles_pending_records_when_unconfigured(
    db: Session, factory: jobs.SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 5 条「Stripe 不可用时……定时任务跳过该单留到下一轮」；SHOP-TASK-116 验收：
    Stripe 未配置而有待处理的记录时照常按不可用处理（处理条数计入、写上次查询时间、
    订单不变），不能因未配置而永远不告警。"""
    _unconfigured(monkeypatch)
    order_id = _order(db, _variant(db), START - timedelta(minutes=40))
    expired_at = START - timedelta(minutes=1)
    checkout_id = _stripe_record(db, order_id, CHECKOUT_OPEN, expires_at=expired_at)

    with factory() as session:
        assert jobs.stripe_checkouts_job().run(session, START) == 1

    assert _last_polled(db, checkout_id) == START
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT


def test_process_stripe_command(
    db: Session,
    factory: jobs.SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败、并发与重试」第 6 条：失败有人工补跑办法；SHOP-TASK-116 验收：手动补跑
    命令 process-stripe 写法照 cancel-expired——执行一次后以 0 退出、日志只含任务名与
    处理条数，未配置数据库以非零退出，任务失败以非零退出且只记异常类名。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _unconfigured(monkeypatch)
    order_id = _order(db, _variant(db), START - timedelta(minutes=40))
    number = _order_number(db, order_id)
    expired_at = START - timedelta(minutes=1)
    checkout_id = _stripe_record(db, order_id, CHECKOUT_OPEN, expires_at=expired_at)

    assert jobs.process_stripe(factory, now=lambda: START) == jobs.EXIT_OK
    assert _job_messages(caplog) == ["job stripe_checkouts done: 1 changed"]
    assert _last_polled(db, checkout_id) == START
    assert all(number not in message for message in caplog.messages)

    monkeypatch.setattr(jobs, "configured_session_factory", lambda: factory)
    assert jobs.main(["process-stripe"]) == jobs.EXIT_OK
    assert _status(db, order_id) == STATUS_AWAITING_PAYMENT

    caplog.clear()
    assert jobs.process_stripe(None) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(SECRET))

    caplog.clear()
    assert jobs.process_stripe(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job stripe_checkouts failed: OperationalError"]
    assert all(SECRET not in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# Stripe 退款尝试的定时处理（SHOP-TASK-126）
# ---------------------------------------------------------------------------


def _refund_attempt(
    db: Session,
    status: str = STRIPE_REFUND_PENDING,
    *,
    refund_id: str | None = None,
    last_checked_at: datetime | None = None,
) -> int:
    """直接写一条退款尝试并提交；来源是一条已付款的 expired 会话记录（待支付订单的迟到
    付款，会话的定时处理不选它）。返回尝试 ID。"""
    order_id = _order(db, _variant(db), START - timedelta(minutes=40))
    total = db.get(Order, order_id).total_sen
    record = StripeCheckout(
        order_id=order_id,
        idempotency_key=f"create-key-{order_id}",
        checkout_session_id=f"cs_test_{order_id}",
        amount_sen=total,
        currency="myr",
        status=CHECKOUT_EXPIRED,
        payment_status=PAYMENT_PAID,
        create_params="[]",
        expires_at=START - timedelta(minutes=9),
        created_at=START - timedelta(minutes=40),
        completed_at=START - timedelta(minutes=10),
        active_order_id=None,
    )
    db.add(record)
    db.flush()
    attempt = StripeRefund(
        payment_intent_id=f"pi_test_{order_id}",
        source_type=SOURCE_CHECKOUT,
        checkout_id=record.id,
        stripe_refund_id=refund_id,
        amount_sen=total,
        status=status,
        call_count=0,
        created_at=START - timedelta(minutes=5),
        succeeded_at=START - timedelta(days=1) if status == STRIPE_REFUND_SUCCEEDED else None,
        last_checked_at=last_checked_at,
        active_checkout_id=record.id,
    )
    db.add(attempt)
    db.flush()
    # 提交前取 ID：提交后读属性会重新加载、让共享连接上留下未结束的事务。
    attempt_id = attempt.id
    db.commit()
    return attempt_id


def _refund_state(db: Session, attempt_id: int) -> tuple[str, int, datetime | None]:
    """尝试的状态、调用次数与上次复查时间。"""
    db.expire_all()
    attempt = db.get(StripeRefund, attempt_id)
    state = (attempt.status, attempt.call_count, attempt.last_checked_at)
    db.rollback()
    return state


def test_stripe_refunds_job_runs_last_with_own_transactions() -> None:
    """DESIGN 1.12「Stripe 测试环境支付」第 8 条「由定时任务在该尝试创建后 24 小时内……
    以同一幂等键自动重试」；SHOP-TASK-126 验收：app/jobs.py 在现有任务之后追加
    stripe_refunds，own_transactions，每轮最多 CANCEL_BATCH_LIMIT 条。"""
    default = jobs.default_jobs()

    assert [job.name for job in default][-2:] == ["expire_points", "stripe_refunds"]
    assert default[6].own_transactions is True
    assert jobs.stripe_refunds_job().own_transactions is True
    assert jobs.STRIPE_REFUND_BATCH_LIMIT == jobs.CANCEL_BATCH_LIMIT


def test_stripe_refunds_job_skips_when_unconfigured_and_nothing_is_due(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 8 条「已 succeeded 的尝试，定时任务在成功后 30 天内每天……再查询一次」；
    SHOP-TASK-126 验收：Stripe 未配置且没有到期的尝试时静默跳过、算成功——一小时前刚
    复查过的 succeeded 尝试未到期，不建适配器、不写上次复查时间。"""
    _unconfigured(monkeypatch)
    checked = START - timedelta(hours=1)
    attempt_id = _refund_attempt(
        db, STRIPE_REFUND_SUCCEEDED, refund_id="re_test_1", last_checked_at=checked
    )
    built: list[Settings] = []

    def factory_spy(settings: Settings) -> Any:
        built.append(settings)
        raise AssertionError("gateway must not be built")

    job = jobs.stripe_refunds_job(gateway_factory=factory_spy)
    runner = _runner([job], factory, heartbeat, clock)

    assert _beat_once(runner, heartbeat)
    assert runner.last_success["stripe_refunds"] == clock.monotonic()
    assert built == []
    assert _refund_state(db, attempt_id) == (STRIPE_REFUND_SUCCEEDED, 0, checked)


def test_stripe_refunds_job_handles_due_attempts_when_unconfigured(
    db: Session, factory: jobs.SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 8 条「其余一律保持 pending、占用额度」；SHOP-TASK-126 验收：Stripe 未配置而有
    到期的尝试时照常处理（不可用时留到下一轮）——从未调用过的尝试计入处理条数、写上次
    复查时间，仍为 pending。"""
    _unconfigured(monkeypatch)
    attempt_id = _refund_attempt(db)

    with factory() as session:
        assert jobs.stripe_refunds_job().run(session, START) == 1

    assert _refund_state(db, attempt_id) == (STRIPE_REFUND_PENDING, 1, START)


def test_process_stripe_refunds_command(
    db: Session,
    factory: jobs.SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """「失败、并发与重试」第 6 条：失败有人工补跑办法；SHOP-TASK-126 验收：手动补跑
    命令 process-stripe-refunds 写法照 process-stripe——执行一次后以 0 退出、日志只含
    任务名与处理条数，未配置数据库以非零退出，任务失败以非零退出且只记异常类名。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _unconfigured(monkeypatch)
    attempt_id = _refund_attempt(db)

    assert jobs.process_stripe_refunds_once(factory, now=lambda: START) == jobs.EXIT_OK
    assert _job_messages(caplog) == ["job stripe_refunds done: 1 changed"]
    assert _refund_state(db, attempt_id) == (STRIPE_REFUND_PENDING, 1, START)

    # 5 分钟内再执行一次：没有到期的尝试，不重复调用。
    later = START + timedelta(minutes=4)
    assert jobs.process_stripe_refunds_once(factory, now=lambda: later) == jobs.EXIT_OK
    assert _refund_state(db, attempt_id) == (STRIPE_REFUND_PENDING, 1, START)

    monkeypatch.setattr(jobs, "configured_session_factory", lambda: factory)
    assert jobs.main(["process-stripe-refunds"]) == jobs.EXIT_OK
    assert _refund_state(db, attempt_id)[0] == STRIPE_REFUND_PENDING

    caplog.clear()
    assert jobs.process_stripe_refunds_once(None) == jobs.EXIT_NOT_CONFIGURED
    assert _job_messages(caplog) == ["database is not configured"]

    def broken() -> Session:
        raise OperationalError("SELECT 1", {}, Exception(SECRET))

    caplog.clear()
    assert jobs.process_stripe_refunds_once(broken) == jobs.EXIT_FAILED
    assert _job_messages(caplog) == ["job stripe_refunds failed: OperationalError"]
    assert all(SECRET not in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# 运营告警邮件（SHOP-TASK-217）
# ---------------------------------------------------------------------------

ALERT_CONFIG = {
    "alert_smtp_host": "smtp.sentinel-host.invalid",
    "alert_smtp_username": "sentinel-smtp-user",
    "alert_smtp_password": "sentinel-smtp-password",
    "alert_from": "sentinel-sender@sentinel-mail.invalid",
    "alert_to": "sentinel-first@sentinel-rcpt.invalid",
}
ONE_SHOT_COMMANDS = [
    "cancel-expired",
    "process-stripe",
    "reset-stock",
    "complete-shipped",
    "delete-verifications",
    "expire-points",
    "process-stripe-refunds",
]


class FakeSmtpConnection:
    """smtplib.SMTP 的替身：login 抛 login_error（为空即成功），记下每次发信。"""

    sent: list[str] = []
    login_error: BaseException | None = None

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        pass

    def starttls(self, *, context: Any = None) -> tuple[int, bytes]:
        return 220, b"ready"

    def login(self, user: str, password: str) -> None:
        if FakeSmtpConnection.login_error is not None:
            raise FakeSmtpConnection.login_error

    def send_message(self, msg: Any, from_addr: str, to_addrs: list[str]) -> dict:
        FakeSmtpConnection.sent.append(msg["Subject"])
        return {}

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtpConnection]:
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtpConnection)
    monkeypatch.setattr(FakeSmtpConnection, "sent", [])
    monkeypatch.setattr(FakeSmtpConnection, "login_error", None)
    return FakeSmtpConnection


def _alert_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.services.ops_alert"]


def test_send_test_alert_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fake_smtp: type[FakeSmtpConnection],
) -> None:
    """DESIGN 1.21「运营告警邮件」第 1 条「最终由服务器接受即以 0 退出；未配置时记上面的
    warning 并以非零退出码退出，最终失败也以非零退出码退出」与 HANDOFF 0.88「send-test-alert
    未配置以 EXIT_NOT_CONFIGURED、最终失败以 EXIT_FAILED 退出」；「不受第 4 条限频」：
    连发两次都发出，且不碰 Redis。"""
    caplog.set_level(logging.INFO, logger="app.services.ops_alert")

    def no_redis(redis_url: str) -> None:
        raise AssertionError("send-test-alert must not touch Redis")

    monkeypatch.setattr(ops_alert, "_client_for", no_redis)
    configured = Settings(_env_file=None, redis_url="redis://sentinel.invalid/7", **ALERT_CONFIG)
    monkeypatch.setattr(jobs, "get_settings", lambda: configured)

    assert jobs.main(["send-test-alert"]) == jobs.EXIT_OK
    assert jobs.main(["send-test-alert"]) == jobs.EXIT_OK
    assert fake_smtp.sent == ["[Acuven Shop] ops_alert_test"] * 2

    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"sentinel-reply")
    assert jobs.main(["send-test-alert"]) == jobs.EXIT_FAILED
    assert _alert_messages(caplog)[-1] == (
        "ops_alert_send_failed category=ops_alert_test error=SMTPAuthenticationError code=535"
    )

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))
    assert jobs.main(["send-test-alert"]) == jobs.EXIT_NOT_CONFIGURED
    assert _alert_messages(caplog)[-1].startswith("ops_alert_not_configured missing=")
    assert all("sentinel" not in message for message in caplog.messages)


@pytest.mark.parametrize("command", ONE_SHOT_COMMANDS)
def test_one_shot_commands_wait_for_the_alert_queue(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """DESIGN 1.21「运营告警邮件」第 2 条「补跑命令退出前等队列发完……最多等 180 秒」
    「退出码仍只反映补跑本身的结果」与 HANDOFF 0.88「补跑命令指 run 与 send-test-alert
    以外的一次性子命令」：每个补跑命令执行完后等一次队列，有未发完的也不改退出码。"""
    waits: list[float] = []

    def wait_until_sent(timeout: float = ops_alert.DRAIN_TIMEOUT_SECONDS) -> int:
        waits.append(timeout)
        return 3

    monkeypatch.setattr(ops_alert, "wait_until_sent", wait_until_sent)
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))
    assert jobs.main([command]) == jobs.EXIT_NOT_CONFIGURED
    assert waits == [180]


def test_one_shot_command_logs_unsent_alerts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, clock: FakeClock
) -> None:
    """DESIGN 1.21「运营告警邮件」第 2 条「到时仍有未发完的，记一条 warning
    ops_alert_unsent count=<封数> 后照常退出，退出码仍只反映补跑本身的结果」：队列里有一封
    发不出去的告警时，等满 180 秒（替身时钟，不真实等待）后记下封数。"""
    caplog.set_level(logging.INFO, logger="app.services.ops_alert")
    monkeypatch.setitem(
        ops_alert.CATEGORIES,
        "demo_failure",
        ops_alert.AlertCategory("演示告警", "按类别码查日志。", False, ops_alert.HOUR_SECONDS),
    )
    waited: list[float] = []

    def wait(condition: threading.Condition, timeout: float) -> None:
        waited.append(timeout)
        clock.advance(timeout)

    configured = Settings(_env_file=None, **ALERT_CONFIG)
    dispatcher = ops_alert.Dispatcher(
        lambda: configured,
        redis_client=lambda: None,
        monotonic=clock.monotonic,
        start_thread=lambda target: None,
        wait=wait,
    )
    monkeypatch.setattr(ops_alert, "_dispatcher", dispatcher)
    ops_alert.alert("demo_failure")

    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))
    assert jobs.main(["cancel-expired"]) == jobs.EXIT_NOT_CONFIGURED
    assert waited == [180]
    assert _alert_messages(caplog) == ["ops_alert_unsent count=1"]


def test_run_does_not_wait_for_the_alert_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESIGN 1.21「运营告警邮件」第 2 条「常驻进程（API、定时任务的常驻循环）退出时不等
    队列，未发出的告警只留在日志里」：run 的循环返回后直接退出，不调用 wait_until_sent；
    send-test-alert 不经队列，同样不等。"""
    calls: list[str] = []

    class StubRunner:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run_forever(self) -> None:
            calls.append("run_forever")

    monkeypatch.setattr(jobs, "Runner", StubRunner)
    monkeypatch.setattr(jobs, "install_signal_handlers", lambda runner: None)
    monkeypatch.setattr(ops_alert, "wait_until_sent", lambda *a, **k: calls.append("wait"))
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None))

    assert jobs.main(["run"]) == jobs.EXIT_OK
    assert jobs.main(["send-test-alert"]) == jobs.EXIT_NOT_CONFIGURED
    assert calls == ["run_forever"]


# ---------------------------------------------------------------------------
# 定时任务失败的告警（SHOP-TASK-218）
# ---------------------------------------------------------------------------

AlertCall = tuple[str, Any, Any]


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[AlertCall]:
    """以替身代替 ops_alert.alert，记下每次调用的（类别, 对象, 异常类名）。"""
    calls: list[AlertCall] = []

    def record(category: str, target: Any = None, *, error_class: Any = None) -> None:
        calls.append((category, target, error_class))

    monkeypatch.setattr(ops_alert, "alert", record)
    return calls


class SwitchableJob:
    """不读写库的任务：error 不为空时抛它的实例（消息含 SECRET），为空时成功。"""

    def __init__(
        self, name: str = "switchable", error: type[Exception] | None = RuntimeError
    ) -> None:
        self.name = name
        self.error = error

    def run(self, db: Session, now: datetime) -> int:
        if self.error is not None:
            raise self.error(f"boom {SECRET}")
        return 0

    def job(self) -> jobs.Job:
        return jobs.Job(name=self.name, run=self.run, own_transactions=True)


def _reset_only() -> list[jobs.Job]:
    """默认任务列表里的每日库存重置任务（与常驻循环用的是同一个）。"""
    return [job for job in jobs.default_jobs() if job.name == "reset_daily_stock"]


def _broken_reset(monkeypatch: pytest.MonkeyPatch, *, enabled: bool, completed: bool) -> None:
    """开关与是否已完成按参数给出；真正执行重置时抛 OperationalError。"""
    monkeypatch.setattr(jobs, "is_daily_stock_reset_enabled", lambda db: enabled)
    monkeypatch.setattr(jobs, "reset_completed", lambda db, now: completed)

    def broken(db: Session, now: datetime) -> Any:
        raise OperationalError("UPDATE product_variants", {}, Exception(SECRET))

    monkeypatch.setattr(jobs, "reset_daily_stock", broken)


def test_job_failing_alerts_once_after_ten_minutes_of_failure(
    factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock, alerts: list[AlertCall]
) -> None:
    """DESIGN 1.21「运营告警邮件」第 7 条「常驻循环里某个任务连续失败（距它最近一次成功，从未
    成功过则距进程启动）满 10 分钟（与心跳同一阈值 UNHEALTHY_AFTER_SECONDS）时，为它发一封
    job_failing；同一段连续失败只发一封」与第 6 条表 job_failing 的对象「任务名，另写最后一次
    失败的异常类名」：未满 10 分钟不告警，满 10 分钟与心跳停写同一轮告警一次、异常类名取最后
    一次失败的，之后同一段失败不再告警；一直成功的任务不告警。"""
    failing = SwitchableJob(error=ValueError)
    steady = jobs.Job(name="steady", run=lambda _db, _now: 0, own_transactions=True)
    runner = _runner([failing.job(), steady], factory, heartbeat, clock)

    # 进程启动即开始失败：第 0 到第 540 秒的各轮不告警。
    for _ in range(10):
        runner.run_round()
        assert alerts == []
        assert runner.healthy()
        clock.advance(60)

    # 第 600 秒：满 10 分钟，最后一次失败换了异常类。
    failing.error = KeyError
    runner.run_round()
    assert alerts == [("job_failing", ("job", "switchable"), "KeyError")]
    assert not runner.healthy()

    for _ in range(5):
        clock.advance(60)
        runner.run_round()
    assert alerts == [("job_failing", ("job", "switchable"), "KeyError")]


def test_job_failing_resets_after_success_and_alerts_again(
    factory: jobs.SessionFactory, heartbeat: Path, clock: FakeClock, alerts: list[AlertCall]
) -> None:
    """DESIGN 1.21「运营告警邮件」第 7 条「该任务再次成功后清零，之后再连续失败满 10 分钟才再次
    入队」「不发「已恢复」邮件」：成功那一轮不告警；再失败时从这次成功起算，未满 10 分钟不告警，
    满 10 分钟再告警一次。"""
    failing = SwitchableJob()
    runner = _runner([failing.job()], factory, heartbeat, clock)

    clock.advance(jobs.UNHEALTHY_AFTER_SECONDS)
    runner.run_round()
    first = ("job_failing", ("job", "switchable"), "RuntimeError")
    assert alerts == [first]

    clock.advance(60)
    failing.error = None
    runner.run_round()
    last_success = clock.monotonic()
    assert alerts == [first]

    failing.error = OSError
    clock.advance(60)
    while clock.monotonic() - last_success < jobs.UNHEALTHY_AFTER_SECONDS:
        runner.run_round()
        assert alerts == [first]
        clock.advance(60)
    runner.run_round()
    assert alerts == [first, ("job_failing", ("job", "switchable"), "OSError")]


def test_job_failing_alerts_every_job_when_the_database_is_not_configured(
    heartbeat: Path, clock: FakeClock, alerts: list[AlertCall]
) -> None:
    """DESIGN 1.21「运营告警邮件」第 7 条「数据库未配置时一轮里全部任务都算失败，各按此告警
    （每个任务名一封）」与第 6 条表「数据库未配置、没有异常时写固定的
    database_not_configured」：满 10 分钟那一轮每个任务名各告警一次，之后不再重复。"""
    names = [job.name for job in jobs.default_jobs()]
    runner = _runner(jobs.default_jobs(), None, heartbeat, clock)

    runner.run_round()
    assert alerts == []

    clock.advance(jobs.UNHEALTHY_AFTER_SECONDS)
    runner.run_round()
    expected = [("job_failing", ("job", name), "database_not_configured") for name in names]
    assert alerts == expected
    assert len(expected) == 7

    clock.advance(60)
    runner.run_round()
    assert alerts == expected


def test_stock_reset_failure_alerts_in_the_loop_but_not_in_the_command(
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DESIGN 1.21「运营告警邮件」第 7 条「每日库存重置另在失败当时就发 stock_reset_failed」
    「补跑命令本身失败不发告警：运营者当场看到退出码与日志」、第 6 条表 stock_reset_failed 的
    对象「马来西亚营业日期，另写异常类名」与第 2 条「照现有写法记一条 error 日志……再把告警……
    放进队列」：常驻循环里重置失败时，在 error 日志之后、任务失败日志之前告警一次；
    reset-stock 子命令经同一任务执行失败时不告警，退出码与日志照旧。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    _broken_reset(monkeypatch, enabled=True, completed=False)
    day = jobs.business_date(START).isoformat()
    calls: list[AlertCall] = []
    logged_before: list[list[str]] = []

    def record(category: str, target: Any = None, *, error_class: Any = None) -> None:
        logged_before.append(_job_messages(caplog))
        calls.append((category, target, error_class))

    monkeypatch.setattr(ops_alert, "alert", record)

    runner = _runner(_reset_only(), factory, heartbeat, clock)
    runner.run_round()
    assert calls == [("stock_reset_failed", ("date", day), "OperationalError")]
    assert logged_before == [[f"stock reset {day} failed: OperationalError"]]
    assert _job_messages(caplog) == [
        f"stock reset {day} failed: OperationalError",
        "job reset_daily_stock failed: OperationalError",
    ]

    caplog.clear()
    assert jobs.reset_stock(factory, now=clock.now) == jobs.EXIT_FAILED
    monkeypatch.setattr(jobs, "configured_session_factory", lambda: factory)
    monkeypatch.setattr(ops_alert, "wait_until_sent", lambda *a, **k: 0)
    assert jobs.main(["reset-stock"]) == jobs.EXIT_FAILED
    assert calls == [("stock_reset_failed", ("date", day), "OperationalError")]
    assert f"stock reset {day} failed: OperationalError" in _job_messages(caplog)
    assert all(SECRET not in message for message in caplog.messages)


@pytest.mark.parametrize(("enabled", "completed"), [(False, False), (True, True)])
def test_stock_reset_skips_do_not_alert(
    factory: jobs.SessionFactory,
    heartbeat: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    alerts: list[AlertCall],
    enabled: bool,
    completed: bool,
) -> None:
    """DESIGN 1.21「运营告警邮件」第 7 条「每日库存重置另在失败当时就发 stock_reset_failed」只在
    失败时告警：每日恢复库存开关关闭、或当天已完成而跳过时（跳过算成功），常驻循环与
    reset-stock 子命令都不告警，也不调用重置。"""
    _broken_reset(monkeypatch, enabled=enabled, completed=completed)

    runner = _runner(_reset_only(), factory, heartbeat, clock)
    runner.run_round()
    assert runner.last_success["reset_daily_stock"] == clock.monotonic()
    assert jobs.reset_stock(factory, now=clock.now) == jobs.EXIT_OK
    assert alerts == []


def test_main_requires_a_subcommand() -> None:
    """SHOP-TASK-031 验收约定：提供 run 与 cancel-expired 两个子命令；SHOP-TASK-032 加了
    reset-stock（见 tests/test_stock_reset.py），未知子命令仍被拒绝。"""
    with pytest.raises(SystemExit) as raised:
        jobs.main([])
    assert raised.value.code != 0
    with pytest.raises(SystemExit) as raised:
        jobs.main(["reset-everything"])
    assert raised.value.code != 0

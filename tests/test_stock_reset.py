"""每日库存重置：app/services/stock_reset.py、app/models/stock_reset.py，以及它在运行器与
reset-stock 子命令里的接入（app/jobs.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 6 条（下文简称「第 6 条」）：“15 分钟未支付自动取消并释放。每天按
  马来西亚时间重建当日可用库存为‘初始库存减去仍有效的预留’，记录重置事件；历史订单不变。”
- 「失败、并发与重试」第 6 条（下文简称「重试第 6 条」）：“每日库存重置、积分过期均可重复运行，
  使用操作标识保证只生效一次；失败有告警与人工补跑办法。”
每条测试的文档字符串写明它守住的设计原句；没有直接原句的，写明是 SHOP-TASK-032 验收标准里的约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）；商品规格与订单在测试里直接写库
建立。引擎设置与 tests/test_jobs.py 相同（关掉驱动的事务处理、由引擎发 BEGIN），保存点才和 MySQL
上一样：重置在认领事务里以保存点执行超时取消。

并发：内存库经 StaticPool 只有一个连接，SQLite 不复现 MySQL 的阻塞等待。用两个数据库会话按可控的
先后顺序模拟：后到者检查到「尚未完成」并结束检查的事务之后、执行认领写入之前（monkeypatch 包住
stock_reset._claim），先用另一个会话完整执行一次重置并提交。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, delete, event, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Category,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    ProductVariant,
    RefundRequest,
    StockReset,
    StockResetLine,
)
from app.models.order import (
    ACTOR_SYSTEM,
    PAID_STATUSES,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
    STATUS_SHIPPED,
)
from app.models.stock_reset import RESET_FAILED, RESET_SUCCEEDED
from app.services import stock_reset
from app.services.order_rules import generate_order_number
from app.services.payment import expire_overdue_orders

# UTC 03:00 即马来西亚 11:00，营业日期 2026-10-05。
NOW = datetime(2026, 10, 5, 3, 0, 0)
TODAY = date(2026, 10, 5)
PRICE = 1500
SHIPPING = 800
# 代替异常消息原文的标记串，用来断言它不进日志与库。
SECRET = "sentinel-exception-detail"


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
    不在重置运行时占着事务。"""
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


class Clock:
    """运行器用的当前 UTC 时间与 monotonic 计时，一起前进；不真实等待。"""

    def __init__(self, start: datetime = NOW) -> None:
        self.wall = start
        self.mono = 1000.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


def _runner(factory: jobs.SessionFactory, heartbeat: Path, clock: Clock) -> jobs.Runner:
    return jobs.Runner(
        jobs.default_jobs(),
        factory,
        heartbeat,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.advance,
    )


def _variant(db: Session, initial: int, available: int | None = None, sku: str = "") -> int:
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
        sku=sku or f"SKU-{generate_order_number()}",
        price_sen=PRICE,
        daily_initial_stock=initial,
        available_stock=initial if available is None else available,
        is_active=True,
    )
    db.add(variant)
    db.flush()
    variant_id = variant.id
    db.commit()
    return variant_id


def _order(
    db: Session,
    lines: list[tuple[int, int]],
    *,
    status: str = STATUS_AWAITING_PAYMENT,
    expires_at: datetime = NOW + timedelta(minutes=10),
) -> int:
    """直接写一张游客订单（lines 为规格 ID 与件数）并提交；返回订单 ID。库存视为下单时已扣。"""
    subtotal = sum(PRICE * quantity for _, quantity in lines)
    order = Order(
        order_number=generate_order_number(),
        status=status,
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
        paid_at=expires_at - timedelta(minutes=5) if status in PAID_STATUSES else None,
    )
    db.add(order)
    db.flush()
    for index, (variant_id, quantity) in enumerate(lines):
        db.add(
            OrderItem(
                order_id=order.id,
                line_index=index,
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
                line_subtotal_sen=PRICE * quantity,
            )
        )
    order_id = order.id
    db.commit()
    return order_id


def _failed_record(db: Session, day: date = TODAY, attempts: int = 1) -> None:
    db.add(
        StockReset(
            business_date=day,
            result=RESET_FAILED,
            attempts=attempts,
            started_at=NOW - timedelta(hours=1),
            completed_at=None,
            sku_count=0,
            error_class="OperationalError",
        )
    )
    db.commit()


def _run(engine: Engine, now: datetime = NOW) -> stock_reset.ResetOutcome:
    """用一个新的数据库会话执行一次重置。"""
    with Session(engine) as session:
        return stock_reset.reset_daily_stock(session, now)


def _rows(db: Session, model: Any) -> list[tuple[Any, ...]]:
    """一张表的全部行（全部列），按主键排序。"""
    db.expire_all()
    table = model.__table__
    rows = [tuple(row) for row in db.execute(select(table).order_by(*table.primary_key.columns))]
    db.rollback()
    return rows


def _record(db: Session, day: date = TODAY) -> dict[str, Any] | None:
    stmt = select(StockReset.__table__).where(StockReset.business_date == day)
    row = db.execute(stmt).mappings().one_or_none()
    db.rollback()
    return None if row is None else dict(row)


def _state(db: Session, day: date = TODAY) -> tuple[str, int, str | None]:
    """重置记录的结果、尝试次数与异常类名。"""
    record = _record(db, day)
    assert record is not None
    return record["result"], record["attempts"], record["error_class"]


def _lines(db: Session) -> list[tuple[int | None, str, int, int, int]]:
    """全部明细的规格 ID、SKU 快照、初始、有效预留与当日可用，按写入顺序。"""
    columns = (
        StockResetLine.variant_id,
        StockResetLine.sku,
        StockResetLine.initial_stock,
        StockResetLine.held_quantity,
        StockResetLine.available_stock,
    )
    lines = [tuple(row) for row in db.execute(select(*columns).order_by(StockResetLine.id))]
    db.rollback()
    return lines


def _stock(db: Session, variant_id: int) -> int:
    db.expire_all()
    stock = db.get(ProductVariant, variant_id).available_stock
    db.rollback()
    return stock


def _set_stock(db: Session, variant_id: int, available: int) -> None:
    """模拟重置之后的一笔下单扣库存。"""
    stmt = update(ProductVariant).where(ProductVariant.id == variant_id)
    db.execute(stmt.values(available_stock=available))
    db.commit()


def _status(db: Session, order_id: int) -> str:
    db.expire_all()
    status = db.get(Order, order_id).status
    db.rollback()
    return status


def _cancel_events(db: Session, order_id: int) -> list[str]:
    stmt = select(OrderEvent.actor_type).where(
        OrderEvent.order_id == order_id, OrderEvent.to_status == STATUS_CANCELLED
    )
    actors = list(db.scalars(stmt))
    db.rollback()
    return actors


def _expire(engine: Engine, now: datetime) -> int:
    """SHOP-TASK-021 的超时取消，在另一个会话里执行。"""
    with Session(engine) as session:
        return expire_overdue_orders(session, now)


def _before_claim(monkeypatch: pytest.MonkeyPatch, action: Callable[[], Any]) -> None:
    """第一次认领写入之前（检查已结束、尚未写入）插入一段操作；之后的认领照常。"""
    real = stock_reset._claim
    done: list[bool] = []

    def wrapper(session: Session, day: date, now: datetime, reset_id: int | None) -> int | None:
        if not done:
            done.append(True)
            action()
        return real(session, day, now, reset_id)

    monkeypatch.setattr(stock_reset, "_claim", wrapper)


def _fail_reset_variants(monkeypatch: pytest.MonkeyPatch, *errors: Exception) -> None:
    """前 len(errors) 次重建库存时先照常写库、再依次抛出 errors；之后照常。"""
    real = stock_reset._reset_variants
    pending = list(errors)

    def wrapper(session: Session, reset_id: int) -> int:
        count = real(session, reset_id)
        if pending:
            raise pending.pop(0)
        return count

    monkeypatch.setattr(stock_reset, "_reset_variants", wrapper)


def _lock_timeout() -> OperationalError:
    return OperationalError("UPDATE stock_resets", {}, Exception(f"lock wait {SECRET}"))


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


# ---------------------------------------------------------------------------
# 营业日期
# ---------------------------------------------------------------------------


def test_business_date_switches_at_16_00_utc(engine: Engine, db: Session) -> None:
    """第 6 条：“每天按马来西亚时间重建当日可用库存”。SHOP-TASK-032 验收：按固定的 UTC+8 偏移
    算出营业日期，UTC 15:59:59 仍是当天，16:00:00 起是马来西亚的下一天，各自是一个操作标识。"""
    assert stock_reset.business_date(datetime(2026, 10, 5, 15, 59, 59)) == date(2026, 10, 5)
    assert stock_reset.business_date(datetime(2026, 10, 5, 16, 0, 0)) == date(2026, 10, 6)
    assert stock_reset.business_date(datetime(2026, 10, 4, 16, 0, 0)) == date(2026, 10, 5)
    _variant(db, initial=5)

    before = _run(engine, datetime(2026, 10, 5, 15, 59, 59))
    after = _run(engine, datetime(2026, 10, 5, 16, 0, 0))

    assert (before.business_date, before.performed) == (date(2026, 10, 5), True)
    assert (after.business_date, after.performed) == (date(2026, 10, 6), True)
    assert _record(db, date(2026, 10, 5))["result"] == RESET_SUCCEEDED
    assert _record(db, date(2026, 10, 6))["result"] == RESET_SUCCEEDED


# ---------------------------------------------------------------------------
# 当日可用 = 初始 − 有效预留
# ---------------------------------------------------------------------------


def test_available_is_initial_minus_active_holds(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 6 条：“重建当日可用库存为‘初始库存减去仍有效的预留’，记录重置事件；历史订单不变。”
    SHOP-TASK-032 验收：有效预留是所有 awaiting_demo_payment 订单（含已过支付到期时间但尚未取消的）
    的订单行件数之和，不含已支付、已取消的订单；明细存初始、有效预留与当日可用；不改初始库存；
    已有订单与退款记录不被改动。超时取消在这里换成什么都不做，以留下已到期但尚未取消的订单。"""
    monkeypatch.setattr(stock_reset, "expire_overdue_orders", lambda _db, _now: 0)
    tee = _variant(db, initial=10, available=1, sku="TEE-M")
    mug = _variant(db, initial=4, sku="MUG")
    _order(db, [(tee, 2), (tee, 1)])
    overdue = _order(db, [(tee, 2)], expires_at=NOW - timedelta(minutes=1))
    paid = _order(db, [(tee, 4)], status=STATUS_PAID, expires_at=NOW - timedelta(hours=1))
    _order(db, [(tee, 5)], status=STATUS_CANCELLED, expires_at=NOW - timedelta(hours=2))
    _order(db, [(mug, 1)], status=STATUS_SHIPPED, expires_at=NOW - timedelta(days=1))
    db.add(
        RefundRequest(
            order_id=paid,
            status="requested",
            actor_type="guest",
            idempotency_key="refund-key",
            request_fingerprint="0" * 64,
            amount_sen=PRICE,
            created_at=NOW - timedelta(minutes=30),
            reviewed_at=None,
        )
    )
    db.commit()
    history_tables = (Order, OrderItem, OrderEvent, RefundRequest)
    history = [_rows(db, model) for model in history_tables]

    outcome = _run(engine)

    assert outcome == stock_reset.ResetOutcome(business_date=TODAY, performed=True, sku_count=2)
    assert _lines(db) == [(tee, "TEE-M", 10, 5, 5), (mug, "MUG", 4, 0, 4)]
    assert (_stock(db, tee), _stock(db, mug)) == (5, 4)
    initial_stmt = select(ProductVariant.daily_initial_stock).order_by(ProductVariant.id)
    assert list(db.scalars(initial_stmt)) == [10, 4]
    db.rollback()
    assert _status(db, overdue) == STATUS_AWAITING_PAYMENT
    assert [_rows(db, model) for model in history_tables] == history
    record = _record(db)
    assert record["result"] == RESET_SUCCEEDED
    assert record["attempts"] == 1
    assert record["started_at"] == NOW
    assert record["completed_at"] == NOW
    assert record["sku_count"] == 2
    assert record["error_class"] is None


def test_holds_above_initial_leave_zero_available(engine: Engine, db: Session) -> None:
    """第 6 条：当日可用库存为初始库存减去仍有效的预留。SHOP-TASK-032 验收：预留大于初始时
    当日可用取 0（初始库存减有效预留与零中较大者）。"""
    tee = _variant(db, initial=2, available=0, sku="TEE-S")
    _order(db, [(tee, 5)])

    _run(engine)

    assert _stock(db, tee) == 0
    assert _lines(db) == [(tee, "TEE-S", 2, 5, 0)]


# ---------------------------------------------------------------------------
# 只生效一次、失败与重试
# ---------------------------------------------------------------------------


def test_second_run_on_the_same_date_changes_nothing(engine: Engine, db: Session) -> None:
    """重试第 6 条：“每日库存重置……可重复运行，使用操作标识保证只生效一次。”同一营业日期第二次
    执行返回已完成，不改库存、记录与明细（之间的下单扣库存不被覆盖）。"""
    tee = _variant(db, initial=10, available=0)
    _order(db, [(tee, 3)])
    assert _run(engine).performed
    assert _stock(db, tee) == 7
    _set_stock(db, tee, 6)
    tables = (ProductVariant, Order, OrderEvent, StockReset, StockResetLine)
    before = [_rows(db, model) for model in tables]

    # 马来西亚 21:00，仍是同一营业日期。
    outcome = _run(engine, NOW + timedelta(hours=10))

    assert outcome == stock_reset.ResetOutcome(business_date=TODAY, performed=False, sku_count=0)
    assert [_rows(db, model) for model in tables] == before
    assert _stock(db, tee) == 6


def test_failure_is_recorded_and_retry_succeeds_once(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：可重复运行、只生效一次；失败有人工补跑办法。SHOP-TASK-032 验收：失败时整个
    事务回滚（库存、超时取消与明细都不留下），独立事务里记为 failed（尝试 1 次、只记异常类名）
    并把原异常交给调用方；再次执行成功后改为 succeeded、尝试次数为 2，明细只有一份。"""
    tee = _variant(db, initial=10, available=2, sku="TEE-L")
    overdue = _order(db, [(tee, 3)], expires_at=NOW - timedelta(minutes=1))
    _order(db, [(tee, 1)])
    before = [_rows(db, model) for model in (ProductVariant, Order, OrderEvent)]
    _fail_reset_variants(monkeypatch, RuntimeError(SECRET))

    with pytest.raises(RuntimeError):
        _run(engine)

    assert [_rows(db, model) for model in (ProductVariant, Order, OrderEvent)] == before
    assert _lines(db) == []
    record = _record(db)
    assert record["result"] == RESET_FAILED
    assert record["attempts"] == 1
    assert record["completed_at"] is None
    assert record["sku_count"] == 0
    assert record["error_class"] == "RuntimeError"
    assert SECRET not in str(_rows(db, StockReset))

    later = NOW + timedelta(minutes=1)
    assert _run(engine, later).performed

    record = _record(db)
    assert record["result"] == RESET_SUCCEEDED
    assert record["attempts"] == 2
    assert record["started_at"] == NOW
    assert record["completed_at"] == later
    assert record["sku_count"] == 1
    assert record["error_class"] is None
    assert _lines(db) == [(tee, "TEE-L", 10, 1, 9)]
    assert _status(db, overdue) == STATUS_CANCELLED
    assert _stock(db, tee) == 9


def test_repeated_failures_count_attempts_and_keep_the_latest_class(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：可重复运行。SHOP-TASK-032 验收：已有 failed 记录时再次失败，用带条件的 UPDATE
    把尝试次数加一并记下最近一次失败的异常类名，仍为 failed。"""
    _variant(db, initial=3)
    _fail_reset_variants(monkeypatch, RuntimeError("first"), ValueError("second"))

    with pytest.raises(RuntimeError):
        _run(engine)
    with pytest.raises(ValueError):
        _run(engine, NOW + timedelta(minutes=1))

    assert _state(db) == (RESET_FAILED, 2, "ValueError")
    assert _record(db)["started_at"] == NOW


def test_overdue_orders_are_cancelled_before_counting(engine: Engine, db: Session) -> None:
    """第 6 条：“15 分钟未支付自动取消并释放”；当日可用库存为初始库存减去仍有效的预留。
    SHOP-TASK-032 验收：认领之后先执行超时取消再统计；重置后再取消订单加回的件数不会重复。"""
    tee = _variant(db, initial=10, available=5)
    overdue = _order(db, [(tee, 2)], expires_at=NOW - timedelta(seconds=1))
    fresh = _order(db, [(tee, 3)], expires_at=NOW + timedelta(minutes=5))

    assert _run(engine).performed

    assert _status(db, overdue) == STATUS_CANCELLED
    assert _cancel_events(db, overdue) == [ACTOR_SYSTEM]
    assert _lines(db)[0][2:] == (10, 3, 7)
    assert _stock(db, tee) == 7

    # 已到期的订单已被重置里的超时取消处理，再执行一次不加回。
    assert _expire(engine, NOW) == 0
    assert _stock(db, tee) == 7

    # 剩下的预留到期取消后加回 3 件，正好回到初始库存。
    assert _expire(engine, NOW + timedelta(minutes=10)) == 1
    assert _status(db, fresh) == STATUS_CANCELLED
    assert _stock(db, tee) == 10
    assert not _run(engine, NOW + timedelta(minutes=10)).performed
    assert _stock(db, tee) == 10


def test_deleted_variant_keeps_its_line(engine: Engine, db: Session) -> None:
    """第 6 条：“记录重置事件”。SHOP-TASK-032 验收：规格被删除时明细保留，外键置空，SKU 快照
    与三个数照常可显示。"""
    tee = _variant(db, initial=3, sku="TEE-XL")
    _run(engine)

    db.execute(delete(ProductVariant).where(ProductVariant.id == tee))
    db.commit()

    assert _lines(db) == [(None, "TEE-XL", 3, 0, 3)]
    assert _record(db)["sku_count"] == 1


# ---------------------------------------------------------------------------
# 并发：同一日期只生效一次
# ---------------------------------------------------------------------------


def test_concurrent_first_runs_take_effect_once(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：“使用操作标识保证只生效一次。”SHOP-TASK-032 验收：同一日期首次执行并发两次，
    后到者在检查到「尚未完成」之后、前者提交之后才插入认领记录，撞上唯一约束、回滚并返回已完成；
    只有一份明细，前者之后的下单扣库存不被后到者改写。"""
    tee = _variant(db, initial=10, available=0, sku="TEE-M")
    _order(db, [(tee, 3)])
    first: list[stock_reset.ResetOutcome] = []

    def earlier_run_then_sale() -> None:
        first.append(_run(engine))
        _set_stock(db, tee, 6)

    _before_claim(monkeypatch, earlier_run_then_sale)

    late = _run(engine)

    assert first[0].performed
    assert late == stock_reset.ResetOutcome(business_date=TODAY, performed=False, sku_count=0)
    assert _state(db) == (RESET_SUCCEEDED, 1, None)
    assert _record(db)["sku_count"] == 1
    assert len(_rows(db, StockReset)) == 1
    assert _lines(db) == [(tee, "TEE-M", 10, 3, 7)]
    assert _stock(db, tee) == 6


def test_concurrent_retries_of_one_failed_record_take_effect_once(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：可重复运行、只生效一次。SHOP-TASK-032 验收：同一条 failed 记录被两个执行者
    并发重试，后到者在检查之后、前者提交之后才执行带条件的 UPDATE，未命中、回滚并返回已完成；
    尝试次数只加一次，只有一份明细。"""
    tee = _variant(db, initial=10, available=0)
    _order(db, [(tee, 4)])
    _failed_record(db)
    first: list[stock_reset.ResetOutcome] = []

    def earlier_run_then_sale() -> None:
        first.append(_run(engine))
        _set_stock(db, tee, 5)

    _before_claim(monkeypatch, earlier_run_then_sale)

    late = _run(engine)

    assert first[0].performed
    assert not late.performed
    assert _state(db) == (RESET_SUCCEEDED, 2, None)
    assert len(_lines(db)) == 1
    assert _lines(db)[0][2:] == (10, 4, 6)
    assert _stock(db, tee) == 5


def test_lock_error_while_claiming_is_recorded_as_failure(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：失败有告警与人工补跑办法。SHOP-TASK-032 验收：认领时遇到数据库锁等待超时或锁
    错误按失败处理——回滚、记为 failed、把原异常交给调用方；库存不变。"""
    tee = _variant(db, initial=10, available=2)

    def lock_timeout(*_args: Any) -> int | None:
        raise _lock_timeout()

    monkeypatch.setattr(stock_reset, "_claim", lock_timeout)

    with pytest.raises(OperationalError):
        _run(engine)

    assert _state(db) == (RESET_FAILED, 1, "OperationalError")
    assert _stock(db, tee) == 2
    assert _lines(db) == []


def test_lock_error_after_another_run_succeeded_keeps_succeeded(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：使用操作标识保证只生效一次。SHOP-TASK-032 验收：后到者等锁超时（前者已提交
    succeeded）按失败记录时，插入撞上唯一约束即放弃，永远不把 succeeded 改回 failed；原异常仍交给
    调用方。"""
    tee = _variant(db, initial=10, available=0)
    real = stock_reset._claim
    started: list[bool] = []
    first: list[stock_reset.ResetOutcome] = []

    def blocked_then_timeout(
        session: Session, day: date, now: datetime, reset_id: int | None
    ) -> int | None:
        if not started:
            # 后到者：前者先完整执行并提交，后到者随后等锁超时。前者的认领走下面的 real。
            started.append(True)
            first.append(_run(engine))
            raise _lock_timeout()
        return real(session, day, now, reset_id)

    monkeypatch.setattr(stock_reset, "_claim", blocked_then_timeout)

    with pytest.raises(OperationalError):
        _run(engine)

    assert first[0].performed
    assert _state(db) == (RESET_SUCCEEDED, 1, None)
    assert _record(db)["completed_at"] == NOW
    assert _lines(db)[0][2:] == (10, 0, 10)
    assert _stock(db, tee) == 10


def test_failure_recorded_after_another_run_succeeded_keeps_succeeded(
    engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重试第 6 条：可重复运行、只生效一次。SHOP-TASK-032 验收：一个执行者失败回滚后、记录失败
    之前，另一个执行者成功提交；前者记录失败时不覆盖 succeeded，仍把原异常交给调用方。"""
    tee = _variant(db, initial=8, available=1)
    _fail_reset_variants(monkeypatch, RuntimeError("first run"))
    real_record = stock_reset._record_failure
    started: list[bool] = []
    other: list[stock_reset.ResetOutcome] = []

    def record_after_other(session: Session, day: date, now: datetime, error_class: str) -> None:
        if not started:
            started.append(True)
            other.append(_run(engine))
        real_record(session, day, now, error_class)

    monkeypatch.setattr(stock_reset, "_record_failure", record_after_other)

    with pytest.raises(RuntimeError):
        _run(engine)

    assert other[0].performed
    assert _state(db) == (RESET_SUCCEEDED, 1, None)
    assert len(_lines(db)) == 1
    assert _stock(db, tee) == 8


# ---------------------------------------------------------------------------
# 约束
# ---------------------------------------------------------------------------


def _reset_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "business_date": TODAY,
        "result": RESET_SUCCEEDED,
        "attempts": 1,
        "started_at": NOW,
        "completed_at": NOW,
        "sku_count": 0,
        "error_class": None,
    }
    values.update(overrides)
    return values


def _assert_rejected(db: Session, row: Any) -> None:
    db.add(row)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "overrides",
    [
        {"result": "running"},
        {"attempts": 0},
        {"sku_count": -1},
        {"result": RESET_SUCCEEDED, "completed_at": None},
        {"result": RESET_FAILED, "completed_at": NOW},
    ],
    ids=["result", "attempts", "sku_count", "succeeded_no_completed", "failed_completed"],
)
def test_stock_reset_check_constraints(db: Session, overrides: dict[str, Any]) -> None:
    """重试第 6 条：使用操作标识保证只生效一次。SHOP-TASK-032 验收：结果只允许 succeeded 与
    failed、尝试次数不小于 1、SKU 数不小于零、succeeded 时完成时间非空、failed 时为空。"""
    _assert_rejected(db, StockReset(**_reset_kwargs(**overrides)))


def test_stock_reset_business_date_is_unique(db: Session) -> None:
    """重试第 6 条：“使用操作标识保证只生效一次。”营业日期全表唯一，即操作标识。"""
    db.add(StockReset(**_reset_kwargs()))
    db.commit()
    _assert_rejected(db, StockReset(**_reset_kwargs(result=RESET_FAILED, completed_at=None)))


def _line_kwargs(reset_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "stock_reset_id": reset_id,
        "variant_id": None,
        "sku": "TEE-M",
        "initial_stock": 5,
        "held_quantity": 2,
        "available_stock": 3,
    }
    values.update(overrides)
    return values


@pytest.fixture
def reset_id(db: Session) -> int:
    record = StockReset(**_reset_kwargs())
    db.add(record)
    db.flush()
    record_id = record.id
    db.commit()
    return record_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"available_stock": 4},
        {"initial_stock": 2, "held_quantity": 5, "available_stock": 1},
        {"held_quantity": -1, "available_stock": 6},
        {"initial_stock": -1, "held_quantity": 0, "available_stock": 0},
        {"stock_reset_id": 999},
        {"variant_id": 999},
    ],
    ids=["formula", "formula_floor", "held", "initial", "reset_fk", "variant_fk"],
)
def test_stock_reset_line_constraints(
    db: Session, reset_id: int, overrides: dict[str, Any]
) -> None:
    """第 6 条：当日可用库存为初始库存减去仍有效的预留。SHOP-TASK-032 验收：当日可用等于初始库存
    减有效预留与零中较大者，三个数都不小于零，两个外键都指向存在的行。"""
    _assert_rejected(db, StockResetLine(**_line_kwargs(reset_id, **overrides)))


def test_stock_reset_line_is_unique_per_reset_and_sku(db: Session, reset_id: int) -> None:
    """重试第 6 条：只生效一次。SHOP-TASK-032 验收：同一重置记录与 SKU 快照唯一，不出现两份明细。"""
    db.add(StockResetLine(**_line_kwargs(reset_id)))
    db.commit()
    _assert_rejected(db, StockResetLine(**_line_kwargs(reset_id)))


def test_stock_reset_with_lines_cannot_be_deleted(db: Session, reset_id: int) -> None:
    """第 6 条：“记录重置事件”。SHOP-TASK-032 验收：明细到重置记录的外键 RESTRICT。"""
    db.add(StockResetLine(**_line_kwargs(reset_id)))
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(delete(StockReset).where(StockReset.id == reset_id))
    db.rollback()
    assert _record(db) is not None


# ---------------------------------------------------------------------------
# 运行器与 reset-stock 子命令
# ---------------------------------------------------------------------------


def test_runner_skips_the_reset_when_today_is_done(
    engine: Engine,
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试第 6 条：可重复运行、只生效一次。SHOP-TASK-032 验收：当前马来西亚营业日期已有 succeeded
    记录时运行器不再调用重置，跳过算成功。"""
    _variant(db, initial=3)
    _run(engine)
    calls: list[int] = []
    monkeypatch.setattr(jobs, "reset_daily_stock", lambda *_args: calls.append(1))
    clock = Clock(NOW + timedelta(hours=1))
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()

    assert calls == []
    assert runner.last_success["reset_daily_stock"] == clock.monotonic()
    assert heartbeat.exists()


def test_runner_resets_after_cancelling_and_retries_next_round(
    engine: Engine,
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """重试第 6 条：失败有告警与人工补跑办法。SHOP-TASK-032 验收：运行器每轮在超时取消之后执行
    当日重置；失败只记日志（营业日期与异常类名，不含订单号或异常消息）、下一轮重试，成功后当天不再
    调用。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    tee = _variant(db, initial=10, available=0)
    order_id = _order(db, [(tee, 2)])
    number = db.get(Order, order_id).order_number
    db.rollback()
    real = jobs.reset_daily_stock
    calls: list[int] = []

    def counted(session: Session, now: datetime) -> stock_reset.ResetOutcome:
        calls.append(1)
        return real(session, now)

    monkeypatch.setattr(jobs, "reset_daily_stock", counted)
    _fail_reset_variants(monkeypatch, RuntimeError(SECRET))
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()
    assert calls == [1]
    assert _record(db)["result"] == RESET_FAILED
    assert _stock(db, tee) == 0
    assert "reset_daily_stock" not in runner.last_success
    assert _messages(caplog) == [
        "stock reset 2026-10-05 failed: RuntimeError",
        "job reset_daily_stock failed: RuntimeError",
    ]

    clock.advance(60)
    runner.run_round()
    assert calls == [1, 1]
    record = _record(db)
    assert (record["result"], record["attempts"]) == (RESET_SUCCEEDED, 2)
    assert _stock(db, tee) == 8
    assert runner.last_success["reset_daily_stock"] == clock.monotonic()

    clock.advance(60)
    runner.run_round()
    assert calls == [1, 1]
    assert _messages(caplog)[2:] == [
        "stock reset 2026-10-05 succeeded: 1 skus",
        "job reset_daily_stock done: 1 changed",
    ]
    assert all(SECRET not in message and number not in message for message in caplog.messages)


def test_reset_stock_command_exit_codes(
    engine: Engine,
    db: Session,
    factory: jobs.SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """重试第 6 条：“失败有告警与人工补跑办法。”SHOP-TASK-032 验收：reset-stock 执行一次当日重置
    后退出，失败非零，成功与当日已完成都返回零。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    tee = _variant(db, initial=6, available=1)
    _fail_reset_variants(monkeypatch, RuntimeError(SECRET))

    assert jobs.reset_stock(factory, now=lambda: NOW) == jobs.EXIT_FAILED
    assert _record(db)["result"] == RESET_FAILED
    assert _stock(db, tee) == 1

    assert jobs.reset_stock(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert _stock(db, tee) == 6
    assert jobs.reset_stock(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert _messages(caplog) == [
        "stock reset 2026-10-05 failed: RuntimeError",
        "job reset_daily_stock failed: RuntimeError",
        "stock reset 2026-10-05 succeeded: 1 skus",
        "job reset_daily_stock done: 1 changed",
        "job reset_daily_stock done: 0 changed",
    ]
    assert all(SECRET not in message for message in caplog.messages)


def test_main_reset_stock_without_database_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """重试第 6 条：失败有人工补跑办法。SHOP-TASK-032 验收：reset-stock 子命令在 SHOP_DATABASE_URL
    未配置时以非零退出。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=""))
    assert jobs.main(["reset-stock"]) == jobs.EXIT_NOT_CONFIGURED
    assert _messages(caplog) == ["database is not configured"]


def test_main_reset_stock_failure_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """重试第 6 条：失败有人工补跑办法。SHOP-TASK-032 验收：reset-stock 失败以非零退出，日志不含
    连接串。这里的内存库没有建表，所以重置失败。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    url = "sqlite://"
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings(_env_file=None, database_url=url))
    assert jobs.main(["reset-stock"]) == jobs.EXIT_FAILED
    assert _messages(caplog)[-1] == "job reset_daily_stock failed: OperationalError"
    assert all(url not in message for message in caplog.messages)

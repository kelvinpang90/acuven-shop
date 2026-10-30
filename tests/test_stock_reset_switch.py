"""每日恢复库存开关：app/jobs.py 的 reset_stock_job 先读开关（运行器与 reset-stock 子命令），以及
app/services/site_settings.py 的 save_site_settings 保存本开关（SHOP-TASK-179）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 6 条（下文简称「第 6 条」）的
每日恢复库存开关一段与 docs/HANDOFF.md 0.76 的落实写法。每条测试的文档字符串写明它守住的第 6 条
原句；没有直接原句的，写明是 SHOP-TASK-179 验收标准里的约定（下称「验收」）。

引擎、会话工厂与运行器的写法照 tests/test_stock_reset.py：SQLite 内存库按模型建表，每个连接打开
外键检查，关掉驱动的事务处理、由引擎发 BEGIN；商品规格、订单、站点设置行与重置记录都直接写库。
当日库存调整（第 12 条，SHOP-TASK-182）尚未交付，测试里直接改库写 available_stock 模拟。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import jobs
from app.db.base import Base
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Order,
    OrderItem,
    Product,
    ProductVariant,
    SiteSetting,
    StockReset,
)
from app.models.order import STATUS_AWAITING_PAYMENT
from app.models.site import PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST, SITE_SETTING_ID
from app.models.stock_reset import RESET_FAILED, RESET_SUCCEEDED
from app.services.order_rules import generate_order_number
from app.services.site_settings import (
    ADMIN_DAILY_STOCK_RESET_CHANGED,
    ADMIN_PAYMENT_METHOD_CHANGED,
    ADMIN_SMS_VERIFICATION_CHANGED,
    SiteSettingsRejected,
    is_daily_stock_reset_enabled,
    save_site_settings,
)

# UTC 03:00 即马来西亚 11:00，营业日期 2026-10-05。
NOW = datetime(2026, 10, 5, 3, 0, 0)
TODAY = date(2026, 10, 5)
PRICE = 1500
SHIPPING = 800
SKIPPED = "stock reset 2026-10-05 skipped (switch off)"


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 同 tests/test_stock_reset.py：关掉驱动的事务处理、由引擎发 BEGIN，保存点才和 MySQL 上一样。
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
    """测试自己的会话。内存库只有一条共享连接，这个会话每次读写后都结束事务。"""
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


def _switch(db: Session, enabled: bool) -> None:
    """写入或改写站点设置行的每日恢复库存开关并提交；其余两项取默认值。"""
    if db.get(SiteSetting, SITE_SETTING_ID) is None:
        db.add(
            SiteSetting(
                id=SITE_SETTING_ID,
                sms_verification_enabled=False,
                payment_method=PAYMENT_METHOD_SIMULATED,
                daily_stock_reset_enabled=enabled,
                updated_at=NOW - timedelta(days=1),
            )
        )
    else:
        db.execute(update(SiteSetting).values(daily_stock_reset_enabled=enabled))
    db.commit()


def _variant(db: Session, initial: int, available: int | None = None) -> int:
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
        daily_initial_stock=initial,
        available_stock=initial if available is None else available,
        is_active=True,
    )
    db.add(variant)
    db.flush()
    variant_id = variant.id
    db.commit()
    return variant_id


def _order(db: Session, variant_id: int, quantity: int) -> None:
    """直接写一张未到期的待支付游客订单（仍有效的预留）并提交。"""
    expires_at = NOW + timedelta(minutes=10)
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
    db.commit()


def _failed_record(db: Session) -> None:
    db.add(
        StockReset(
            business_date=TODAY,
            result=RESET_FAILED,
            attempts=1,
            started_at=NOW - timedelta(hours=1),
            completed_at=None,
            sku_count=0,
            error_class="OperationalError",
        )
    )
    db.commit()


def _records(db: Session) -> list[tuple[date, str, int, str | None]]:
    """全部重置记录的营业日期、结果、尝试次数与异常类名。"""
    columns = (
        StockReset.business_date,
        StockReset.result,
        StockReset.attempts,
        StockReset.error_class,
    )
    rows = [tuple(row) for row in db.execute(select(*columns).order_by(StockReset.id))]
    db.rollback()
    return rows


def _stock(db: Session, variant_id: int) -> int:
    db.expire_all()
    stock = db.get(ProductVariant, variant_id).available_stock
    db.rollback()
    return stock


def _set_stock(db: Session, variant_id: int, available: int) -> None:
    """模拟当日库存调整（SHOP-TASK-182 尚未交付）：直接改写当日可用库存。"""
    stmt = update(ProductVariant).where(ProductVariant.id == variant_id)
    db.execute(stmt.values(available_stock=available))
    db.commit()


def _count_calls(monkeypatch: pytest.MonkeyPatch, name: str) -> list[int]:
    """包住 app.jobs 里的一个函数，照常调用并记下调用次数。"""
    real = getattr(jobs, name)
    calls: list[int] = []

    def counted(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(jobs, name, counted)
    return calls


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "app.jobs"]


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------


def test_runner_skips_the_reset_while_off(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 6 条：「关闭时跳过并正常结束（跳过算成功，不影响任务的健康心跳），不做当日的重置，不改任何
    SKU 的当日可用库存，不新建也不重试重置记录」。验收：不调用 reset_completed 与
    reset_daily_stock，记一条 stock reset <营业日期> skipped (switch off) 的 info 日志。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    tee = _variant(db, initial=10, available=3)
    _switch(db, False)
    completed = _count_calls(monkeypatch, "reset_completed")
    resets = _count_calls(monkeypatch, "reset_daily_stock")
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()

    assert completed == []
    assert resets == []
    assert _records(db) == []
    assert _stock(db, tee) == 3
    assert runner.last_success["reset_daily_stock"] == clock.monotonic()
    assert heartbeat.exists()
    assert _messages(caplog) == [SKIPPED]
    assert [r.levelno for r in caplog.records if r.name == "app.jobs"] == [logging.INFO]


def test_failed_record_is_kept_while_off_and_retried_after_turning_on(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path
) -> None:
    """第 6 条：「不新建也不重试重置记录（关闭期间 A07 没有新的记录；当天此前已有的失败记录保留，
    重新开启后照常重试）」：关闭时当天已有的 failed 记录结果与尝试次数不变；重新开启后下一轮把它
    重试为 succeeded，尝试次数加一。"""
    tee = _variant(db, initial=10, available=0)
    _failed_record(db)
    _switch(db, False)
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()

    assert _records(db) == [(TODAY, RESET_FAILED, 1, "OperationalError")]
    assert _stock(db, tee) == 0

    _switch(db, True)
    clock.advance(60)
    runner.run_round()

    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 2, None)]
    assert _stock(db, tee) == 10


def test_turning_on_overwrites_adjustments_made_while_off(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path
) -> None:
    """第 6 条：「库存只随下单、取消与当日库存调整（第 12 条）变化」「开启后定时任务的下一次运行即按
    『初始库存减去仍有效的预留』重置当天，所以开启当天就恢复，并覆盖当天此前的调整」：关闭期间同一
    营业日期改过的当日可用库存（直接改库模拟当日库存调整）在关闭的一轮里不变，重新开启后的下一轮被
    初始 10 减去仍有效的预留 2 覆盖为 8。"""
    tee = _variant(db, initial=10, available=10)
    _order(db, tee, 2)
    _switch(db, False)
    _set_stock(db, tee, 4)
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()

    assert _stock(db, tee) == 4
    assert _records(db) == []

    _switch(db, True)
    clock.advance(60)
    runner.run_round()

    assert _stock(db, tee) == 8
    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 1, None)]


def test_off_then_on_after_today_succeeded_does_not_reset_again(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 6 条：「当天已经完成重置的，关闭再开启不再重复」「关闭前已经完成的重置不撤销」：当天已
    succeeded 之后关闭一轮、再开启一轮，都不再调用重置，重置后的下单扣库存不被覆盖，记录不变。"""
    tee = _variant(db, initial=10, available=0)
    _switch(db, True)
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)
    runner.run_round()
    assert _stock(db, tee) == 10
    _set_stock(db, tee, 6)
    resets = _count_calls(monkeypatch, "reset_daily_stock")

    _switch(db, False)
    clock.advance(60)
    runner.run_round()
    _switch(db, True)
    clock.advance(60)
    runner.run_round()

    assert resets == []
    assert _stock(db, tee) == 6
    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 1, None)]


def test_switch_change_between_rounds_takes_effect_without_caching(
    db: Session,
    factory: jobs.SessionFactory,
    heartbeat: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 6 条：「定时任务每次运行时先从数据库读取开关的当前值（不缓存），再判断当天是否已完成重置；
    以这次读到的值为准」：同一个 Runner 两轮之间改开关即生效——关闭的一轮不建记录，开启的下一轮完成
    重置；每一轮都重新读开关，且读开关在判断是否已完成之前。"""
    tee = _variant(db, initial=5, available=1)
    _switch(db, False)
    order: list[str] = []
    real_read = jobs.is_daily_stock_reset_enabled
    real_completed = jobs.reset_completed

    def read(session: Session) -> bool:
        order.append("switch")
        return real_read(session)

    def completed(session: Session, now: datetime) -> bool:
        order.append("completed")
        return real_completed(session, now)

    monkeypatch.setattr(jobs, "is_daily_stock_reset_enabled", read)
    monkeypatch.setattr(jobs, "reset_completed", completed)
    clock = Clock()
    runner = _runner(factory, heartbeat, clock)

    runner.run_round()
    assert _records(db) == []
    assert _stock(db, tee) == 1

    _switch(db, True)
    clock.advance(60)
    runner.run_round()
    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 1, None)]
    assert _stock(db, tee) == 5

    _switch(db, False)
    clock.advance(60)
    runner.run_round()

    assert order == ["switch", "switch", "completed", "switch"]
    assert runner.last_success["reset_daily_stock"] == clock.monotonic()


def test_missing_setting_row_resets_as_usual(
    db: Session, factory: jobs.SessionFactory, heartbeat: Path
) -> None:
    """第 6 条：「表里还没有站点设置那一行时视为开启」：没有站点设置行时运行器照常重置，也不补写
    那一行。"""
    tee = _variant(db, initial=7, available=2)
    assert db.scalar(select(SiteSetting.id)) is None
    db.rollback()

    _runner(factory, heartbeat, Clock()).run_round()

    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 1, None)]
    assert _stock(db, tee) == 7
    assert db.scalar(select(SiteSetting.id)) is None
    db.rollback()


# ---------------------------------------------------------------------------
# reset-stock 子命令
# ---------------------------------------------------------------------------


def test_reset_stock_command_skips_while_off(
    db: Session,
    factory: jobs.SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """第 6 条：「运营者手动补跑重置的命令同样受开关约束，关闭时跳过并以成功退出」：关闭时
    reset-stock 以 EXIT_OK 退出，没有新记录，当日可用库存不变；开启后同一命令照常重置。"""
    caplog.set_level(logging.INFO, logger="app.jobs")
    tee = _variant(db, initial=6, available=1)
    _switch(db, False)

    assert jobs.reset_stock(factory, now=lambda: NOW) == jobs.EXIT_OK

    assert _records(db) == []
    assert _stock(db, tee) == 1
    assert _messages(caplog) == [SKIPPED, "job reset_daily_stock done: 0 changed"]

    _switch(db, True)

    assert jobs.reset_stock(factory, now=lambda: NOW) == jobs.EXIT_OK
    assert _records(db) == [(TODAY, RESET_SUCCEEDED, 1, None)]
    assert _stock(db, tee) == 6


# ---------------------------------------------------------------------------
# 保存（save_site_settings）
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_id(db: Session) -> int:
    row = AdminAccount(
        username="admin@example.com",
        password_hash="not-a-real-hash",
        created_at=NOW - timedelta(days=1),
        password_updated_at=NOW - timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row.id


def _save(
    db: Session,
    admin_id: int,
    stock_reset_enabled: bool | None,
    *,
    method: str = PAYMENT_METHOD_SIMULATED,
    sms_enabled: bool | None = None,
    stripe_available: bool = False,
    sms_available: bool = False,
) -> tuple[str, bool, bool, bool]:
    return save_site_settings(
        db,
        admin_id,
        method,
        sms_enabled,
        stripe_available,
        sms_available,
        NOW,
        stock_reset_enabled=stock_reset_enabled,
    )


def _audits(db: Session) -> list[tuple[Any, ...]]:
    """站点设置的全部审计：操作名、管理员、对象类别、对象 ID、旧值、新值，按写入顺序。"""
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    audits = [
        (
            row.action,
            row.admin_account_id,
            row.target_type,
            row.target_id,
            row.old_value,
            row.new_value,
        )
        for row in rows
    ]
    db.rollback()
    return audits


def _current(db: Session) -> bool:
    enabled = is_daily_stock_reset_enabled(db)
    db.rollback()
    return enabled


def test_saving_off_and_on_each_writes_an_audit(db: Session, admin_id: int) -> None:
    """第 6 条：「照 SiteSetting 一行写审计，旧值与新值记 on 或 off」；验收：关闭本身不检查任何配置
    （这里 Stripe 与短信都不可用）。保存关闭与开启各写一条 admin_daily_stock_reset_changed，对象为
    站点设置行；返回值为（支付方式, 短信开关, 每日恢复库存开关, 是否实际改动）。"""
    _switch(db, True)

    assert _save(db, admin_id, False) == (PAYMENT_METHOD_SIMULATED, False, False, True)
    assert _current(db) is False
    assert _save(db, admin_id, True) == (PAYMENT_METHOD_SIMULATED, False, True, True)
    assert _current(db) is True

    assert _audits(db) == [
        (ADMIN_DAILY_STOCK_RESET_CHANGED, admin_id, "site_setting", SITE_SETTING_ID, "on", "off"),
        (ADMIN_DAILY_STOCK_RESET_CHANGED, admin_id, "site_setting", SITE_SETTING_ID, "off", "on"),
    ]


def test_omitted_field_is_not_changed_and_not_audited(db: Session, admin_id: int) -> None:
    """第 6 条：「保存请求里本开关可以省略，省略即不改」：stock_reset_enabled 为 None 时开关保持
    关闭，三项都未变则不写审计、返回未改动；同时改支付方式时只写支付方式的审计。"""
    _switch(db, False)

    assert _save(db, admin_id, None) == (PAYMENT_METHOD_SIMULATED, False, False, False)
    assert _audits(db) == []

    saved = _save(db, admin_id, None, method=PAYMENT_METHOD_STRIPE_TEST, stripe_available=True)

    assert saved == (PAYMENT_METHOD_STRIPE_TEST, False, False, True)
    assert _current(db) is False
    assert [audit[0] for audit in _audits(db)] == [ADMIN_PAYMENT_METHOD_CHANGED]


@pytest.mark.parametrize("enabled", [True, False])
def test_saving_the_current_value_writes_no_audit(
    db: Session, admin_id: int, enabled: bool
) -> None:
    """第 6 条「照 SiteSetting 一行写审计」（没有修改即不写）；验收：三项都未变时回滚、不写审计：
    保存与当前值相同的开关返回未改动，更新时间不变。"""
    _switch(db, enabled)
    before = db.scalar(select(SiteSetting.updated_at))
    db.rollback()

    assert _save(db, admin_id, enabled) == (PAYMENT_METHOD_SIMULATED, False, enabled, False)

    assert _audits(db) == []
    assert db.scalar(select(SiteSetting.updated_at)) == before
    db.rollback()


def test_rejected_stripe_or_sms_keeps_the_switch(db: Session, admin_id: int) -> None:
    """第 6 条：「同一次保存的任何一项被拒（短信配置不齐、Stripe 未配置等）时，本开关的修改同样不
    生效」：Stripe 不可用时同一次提交 stripe_test 与关闭抛 stripe_not_configured，短信不可用时同一次
    提交开启短信与关闭抛 sms_not_configured；两次开关都仍开启，没有审计。"""
    _switch(db, True)

    with pytest.raises(SiteSettingsRejected) as stripe:
        _save(db, admin_id, False, method=PAYMENT_METHOD_STRIPE_TEST)
    db.rollback()
    with pytest.raises(SiteSettingsRejected) as sms:
        _save(db, admin_id, False, sms_enabled=True, stripe_available=True)
    db.rollback()

    assert (stripe.value.code, sms.value.code) == ("stripe_not_configured", "sms_not_configured")
    assert _current(db) is True
    assert _audits(db) == []


def test_three_changes_audit_in_order(db: Session, admin_id: int) -> None:
    """第 6 条「开关与短信验证开关、支付方式一起在『站点设置』整页保存」；验收：同一次保存有多项
    改动时审计依次为支付方式、短信、每日恢复库存。"""
    _switch(db, True)

    saved = _save(
        db,
        admin_id,
        False,
        method=PAYMENT_METHOD_STRIPE_TEST,
        sms_enabled=True,
        stripe_available=True,
        sms_available=True,
    )

    assert saved == (PAYMENT_METHOD_STRIPE_TEST, True, False, True)
    assert [(audit[0], audit[4], audit[5]) for audit in _audits(db)] == [
        (ADMIN_PAYMENT_METHOD_CHANGED, PAYMENT_METHOD_SIMULATED, PAYMENT_METHOD_STRIPE_TEST),
        (ADMIN_SMS_VERIFICATION_CHANGED, "off", "on"),
        (ADMIN_DAILY_STOCK_RESET_CHANGED, "on", "off"),
    ]


def test_saving_without_a_row_inserts_it_with_the_switch_on(db: Session, admin_id: int) -> None:
    """第 6 条：「表里还没有站点设置那一行时视为开启，按默认值插入该行时也写开启」：表为空时保存
    关闭，插入的那一行旧值记 on，审计为 on → off；表为空时只改支付方式，插入的行开关为开启。"""
    assert _save(db, admin_id, False) == (PAYMENT_METHOD_SIMULATED, False, False, True)
    assert [audit[4:] for audit in _audits(db)] == [("on", "off")]

    db.execute(SiteSetting.__table__.delete())
    db.commit()
    # 会话里还留着刚才锁定的那一行的对象，删掉它，插入同一主键的新行时才不冲突。
    db.expunge_all()

    _save(db, admin_id, None, method=PAYMENT_METHOD_STRIPE_TEST, stripe_available=True)

    assert db.scalar(select(SiteSetting.daily_stock_reset_enabled)) is True
    db.rollback()

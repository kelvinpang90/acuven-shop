"""每日库存重置：按马来西亚营业日期把当日可用库存重建为初始库存减去有效预留，同一日期只生效一次。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 6 条：下单预留当日可用库存，支付成功消耗预留，15 分钟未支付
  自动取消并释放；每天按马来西亚时间重建当日可用库存为“初始库存减去仍有效的预留”，记录重置
  事件；历史订单不变。管理员当天修改初始库存仅从次日重置生效（重置只读初始库存、不改它）。
- 「失败、并发与重试」第 6 条：每日库存重置可重复运行，使用操作标识保证只生效一次。操作标识是
  stock_resets 表全表唯一的营业日期。

营业日期按固定的 UTC+8 偏移算出（马来西亚不实行夏令时），不依赖时区数据库：UTC 16:00:00 起
即马来西亚的下一天。

reset_daily_stock 的步骤：
1. 该日期已有 succeeded 记录：不做任何改动，返回未执行（已完成）。
2. 否则结束检查的事务，另开一个事务（MySQL 上用 READ COMMITTED，见 _begin）先认领，按检查时
   看到的记录决定怎么认领：没有记录时插入该日期的
   succeeded 记录（完成时间取当前时间、SKU 数暂为 0、尝试次数 1；未提交前对其他执行者不可见）；
   已有 failed 记录时用带条件的 UPDATE（只在结果仍为 failed 时命中）改为 succeeded、写完成时间、
   清空异常类名并把尝试次数加一。插入遇唯一约束冲突或 UPDATE 未命中即回滚，返回已完成。
   MySQL 上并发的后到者阻塞在同一唯一键（插入）或同一记录的行锁（UPDATE）上：前者提交后插入
   冲突或 UPDATE 未命中，前者回滚时后到者继续认领，所以同一日期只有一次生效。
3. 认领之后执行 SHOP-TASK-021 的超时取消（expire_overdue_orders，不限张数），它逐张提交与
   回滚，这里让它在绑同一连接、以保存点加入本事务的会话里执行，它的提交只释放保存点。
4. 按 SKU 规格 ID 从小到大对全部规格加锁读取（与 SHOP-TASK-020 扣库存、SHOP-TASK-021 加回库存
   的顺序一致，避免死锁），统计每个规格所有 awaiting_demo_payment 订单（含已过支付到期时间但
   尚未取消的）订单行件数之和作为有效预留，当日可用库存 = max(初始库存 − 有效预留, 0)，写入
   逐 SKU 明细，再把 SKU 数更新为实际值后提交。
5. 任一步出错（含认领时的锁等待超时与锁错误）：回滚，再在独立事务里记录失败（见
   _record_failure），然后把原异常交给调用方。永远不把 succeeded 改回 failed。

调用方须传入不在事务中的会话（函数自己提交与回滚），不能是以保存点加入外层事务的会话：
那样记录失败的“独立事务”会随外层回滚一起消失。

不改订单、退款与积分记录（超时取消除外，它本来就会取消已到期的待支付订单），不改初始库存。
本模块不写日志；只记异常类名，不存异常消息原文。时间一律是不带时区的 UTC。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, ProductVariant, StockReset, StockResetLine
from app.models.order import STATUS_AWAITING_PAYMENT
from app.models.stock_reset import ERROR_CLASS_LENGTH, RESET_FAILED, RESET_SUCCEEDED
from app.services.payment import expire_overdue_orders

# 马来西亚时间（MYT）固定为 UTC+8，不实行夏令时。
MALAYSIA_UTC_OFFSET = timedelta(hours=8)


@dataclass(frozen=True)
class ResetOutcome:
    """一次调用的结果。performed 为假表示该日期已完成（本次没有改动任何东西）。"""

    business_date: date
    performed: bool
    # 本次重置的 SKU 数；未执行时为 0。
    sku_count: int


def business_date(now: datetime) -> date:
    """不带时区的 UTC 时间对应的马来西亚营业日期。"""
    return (now + MALAYSIA_UTC_OFFSET).date()


def reset_completed(db: Session, now: datetime) -> bool:
    """now 所在的马来西亚营业日期是否已有 succeeded 记录；只读。"""
    stmt = select(StockReset.result).where(StockReset.business_date == business_date(now))
    return db.scalars(stmt).one_or_none() == RESET_SUCCEEDED


def reset_daily_stock(db: Session, now: datetime) -> ResetOutcome:
    """重置 now 所在马来西亚营业日期的当日可用库存；步骤见模块说明。"""
    day = business_date(now)
    stmt = select(StockReset.id, StockReset.result).where(StockReset.business_date == day)
    existing = db.execute(stmt).one_or_none()
    # 结束只读的事务：认领要在新事务里进行，MySQL 上才能为它设隔离级别。
    db.rollback()
    if existing is not None and existing.result == RESET_SUCCEEDED:
        return ResetOutcome(business_date=day, performed=False, sku_count=0)

    try:
        _begin(db)
        reset_id = _claim(db, day, now, None if existing is None else existing.id)
        if reset_id is None:
            db.rollback()
            return ResetOutcome(business_date=day, performed=False, sku_count=0)
        _expire_overdue(db, now)
        sku_count = _reset_variants(db, reset_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        _record_failure(db, day, now, type(exc).__name__)
        raise
    return ResetOutcome(business_date=day, performed=True, sku_count=sku_count)


def _begin(db: Session) -> None:
    """开始认领事务。

    MySQL 上用 READ COMMITTED：默认的 REPEATABLE READ 在超时取消的第一次查询时取快照，之后统计
    有效预留的普通查询仍读那个快照，会漏掉加锁等待期间已提交的下单。READ COMMITTED 下每条语句
    读最新已提交的数据，统计在锁住全部规格之后进行，所以凡已扣过库存的订单都已提交、都被看到；
    还没扣库存的下单排在规格锁之后，从重置后的值上扣。SQLite 不支持这个级别，也不需要。
    """
    if db.get_bind().dialect.name == "mysql":
        db.connection(execution_options={"isolation_level": "READ COMMITTED"})


def _claim(db: Session, day: date, now: datetime, reset_id: int | None) -> int | None:
    """认领该日期：返回重置记录 ID；已被其他执行者完成（插入冲突或 UPDATE 未命中）返回空。

    reset_id 是检查时看到的 failed 记录；检查时没有记录为空，此时插入。只 flush、不提交；
    返回空时由调用方回滚。唯一约束冲突以外的错误（如锁等待超时）原样抛出。
    """
    if reset_id is None:
        record = StockReset(
            business_date=day,
            result=RESET_SUCCEEDED,
            attempts=1,
            started_at=now,
            completed_at=now,
            sku_count=0,
            error_class=None,
        )
        db.add(record)
        try:
            db.flush()
        except IntegrityError:
            return None
        return record.id

    stmt = (
        update(StockReset)
        .where(StockReset.id == reset_id, StockReset.result == RESET_FAILED)
        .values(
            result=RESET_SUCCEEDED,
            completed_at=now,
            error_class=None,
            attempts=StockReset.attempts + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        return None
    return reset_id


def _expire_overdue(db: Session, now: datetime) -> None:
    """在本事务里执行超时取消：它逐张 commit（未命中时 rollback），这里只作用于保存点。"""
    connection = db.connection()
    with Session(bind=connection, join_transaction_mode="create_savepoint") as nested:
        expire_overdue_orders(nested, now)
        nested.commit()


def _reset_variants(db: Session, reset_id: int) -> int:
    """锁住全部规格、按有效预留重建当日可用库存、写明细与 SKU 数；返回 SKU 数。只 flush。"""
    # 一条按主键升序的加锁读取，按规格 ID 从小到大逐行加锁。
    variant_stmt = (
        select(ProductVariant)
        .order_by(ProductVariant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    variants = list(db.scalars(variant_stmt))

    # 有效预留：所有待支付订单（含已过支付到期时间但尚未取消的）的订单行件数，按规格合计；
    # 已支付、已取消的订单不算。规格已删除（规格 ID 为空）的行没有可重置的规格，不计。
    hold_stmt = (
        select(OrderItem.variant_id, func.sum(OrderItem.quantity))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.status == STATUS_AWAITING_PAYMENT, OrderItem.variant_id.is_not(None))
        .group_by(OrderItem.variant_id)
    )
    holds = {variant_id: int(quantity) for variant_id, quantity in db.execute(hold_stmt).tuples()}

    for variant in variants:
        held = holds.get(variant.id, 0)
        available = max(variant.daily_initial_stock - held, 0)
        variant.available_stock = available
        db.add(
            StockResetLine(
                stock_reset_id=reset_id,
                variant_id=variant.id,
                sku=variant.sku,
                initial_stock=variant.daily_initial_stock,
                held_quantity=held,
                available_stock=available,
            )
        )
    db.flush()

    count_stmt = (
        update(StockReset)
        .where(StockReset.id == reset_id)
        .values(sku_count=len(variants))
        .execution_options(synchronize_session=False)
    )
    db.execute(count_stmt)
    return len(variants)


def _record_failure(db: Session, day: date, now: datetime, error_class: str) -> None:
    """在独立事务里记录失败。

    已有 failed 记录：带条件的 UPDATE（只在结果仍为 failed 时命中）把尝试次数加一、记下异常类名。
    没有命中时插入 failed 记录（尝试次数 1）；插入遇唯一约束冲突（该日期已有记录，含已 succeeded
    的）即放弃。所以永远不把 succeeded 改回 failed。记录失败本身出错时回滚并放弃，由调用方抛出
    原异常。
    """
    error_class = error_class[:ERROR_CLASS_LENGTH]
    try:
        stmt = (
            update(StockReset)
            .where(StockReset.business_date == day, StockReset.result == RESET_FAILED)
            .values(attempts=StockReset.attempts + 1, error_class=error_class)
            .execution_options(synchronize_session=False)
        )
        if db.execute(stmt).rowcount == 1:
            db.commit()
            return
        db.add(
            StockReset(
                business_date=day,
                result=RESET_FAILED,
                attempts=1,
                started_at=now,
                completed_at=None,
                sku_count=0,
                error_class=error_class,
            )
        )
        db.flush()
        db.commit()
    except Exception:
        # 含插入的唯一约束冲突：该日期已有记录，放弃。
        db.rollback()

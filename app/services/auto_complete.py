"""发货满 7 天自动确认收货。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「订单与退款状态」：若访客不操作，模拟发货满 7 天自动确认收货。自动完成只改演示状态，
  不影响 30 天退款期（退款截止时间按支付时间算，见 app/services/refunds.py，这里不碰）。
  所有购买件数均已批准退款时，冻结后续打包/发货/确认收货的推进，保留退款前履约状态。
- 「失败、并发与重试」：定时任务可重复运行、只生效一次。

发货时间取该订单最近一条迁移到 demo_shipped 的状态事件的时间。系统自动完成写一条操作者为
system 的事件，不写确认收货记录（ReceiptConfirmation 只记手动确认）。全部已退的判定沿用
SHOP-TASK-029 的 refund_state。

锁定协议（与 app/services/order_lookup.py 的访客确认收货、app/services/admin_orders.py 的推进
发货、app/services/refund_review.py 的退款审核相同，SHOP-TASK-040）：锁定订单行之前先结束此前
的数据库事务（此前只有读取，回滚即可），使 SELECT … FOR UPDATE（lock_order_statement，即
with_for_update）成为新事务的第一条语句。MySQL 默认的 REPEATABLE READ 下，读取快照在事务第一条
非锁定读时建立，这样锁定之后的读取（含 refund_state）才能看到等锁期间别人提交的审核、发货或
确认。所以本函数自己管理事务，不能放进外层事务以保存点执行（定时任务以 own_transactions 注册）。

本模块不写日志；不记录订单号或个人资料。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import Order, OrderEvent
from app.models.order import ACTOR_SYSTEM, STATUS_COMPLETED, STATUS_SHIPPED
from app.services.order_lookup import lock_order_statement
from app.services.order_rules import is_transition_allowed
from app.services.refunds import refund_state

# 模拟发货满这么久仍未确认收货即由系统完成。
AUTO_COMPLETE_AFTER = timedelta(days=7)


def auto_complete_shipped_orders(db: Session, now: datetime, limit: int) -> int:
    """把模拟发货满 7×24 小时仍未确认收货的订单改为 demo_completed，返回本次完成的张数。

    候选：状态为 demo_shipped、且最近一条迁移到 demo_shipped 的事件时间不晚于
    now − 7×24 小时的订单，按该时间从早到晚、同一时间按订单 ID 从小到大。候选每次按上一页最后
    一张之后取 limit 张，一直扫描到完成 limit 张或没有更多候选；全部已退而跳过的订单不计入
    上限，所以它们堆在前面也不会卡住之后的订单。

    每张订单在自己的事务里：结束此前的事务 → 锁定订单行（新事务的第一条语句）→ 状态已不是
    demo_shipped，或 refund_state 判定全部已退时回滚并跳过 → 经迁移判定后用带条件的 UPDATE
    （状态仍为 demo_shipped 才改）改为 demo_completed、写一条操作者为 system 的事件，然后提交。
    所以同一订单只生效一次，同一时刻连续执行两次第二次完成 0 张。返回时不留未结束的事务。
    """
    cutoff = now - AUTO_COMPLETE_AFTER
    completed = 0
    after: tuple[datetime, int] | None = None
    while completed < limit:
        page = _candidates(db, cutoff, after, limit)
        for order_id, shipped_at in page:
            after = (shipped_at, order_id)
            if _complete(db, order_id, now):
                db.commit()
                completed += 1
                if completed >= limit:
                    break
            else:
                db.rollback()
        if len(page) < limit:
            break
    # 结束最后一次候选扫描的事务。
    db.rollback()
    return completed


def _candidates(
    db: Session, cutoff: datetime, after: tuple[datetime, int] | None, size: int
) -> list[tuple[int, datetime]]:
    """排在 after 之后的至多 size 张候选：(订单 ID, 最近一次迁移到 demo_shipped 的时间)。"""
    shipped = (
        select(OrderEvent.order_id, func.max(OrderEvent.created_at).label("shipped_at"))
        .where(OrderEvent.to_status == STATUS_SHIPPED)
        .group_by(OrderEvent.order_id)
        .subquery()
    )
    stmt = (
        select(Order.id, shipped.c.shipped_at)
        .join(shipped, shipped.c.order_id == Order.id)
        .where(Order.status == STATUS_SHIPPED, shipped.c.shipped_at <= cutoff)
        .order_by(shipped.c.shipped_at, Order.id)
        .limit(size)
    )
    if after is not None:
        at, order_id = after
        stmt = stmt.where(
            or_(
                shipped.c.shipped_at > at,
                and_(shipped.c.shipped_at == at, Order.id > order_id),
            )
        )
    return list(db.execute(stmt).tuples())


def _complete(db: Session, order_id: int, now: datetime) -> bool:
    """在新事务里锁定并完成一张订单；返回是否完成。只 flush，由调用方提交或回滚。"""
    # 此前只有读取（候选扫描或上一张的已结束事务）：回滚，使锁定成为新事务的第一条语句。
    db.rollback()
    order = db.scalars(lock_order_statement(order_id)).one()
    if order.status != STATUS_SHIPPED:
        return False
    if refund_state(db, order_id).fully_refunded:
        return False

    # 经 SHOP-TASK-010 的迁移判定函数检查；这条迁移在表里，不通过即编程错误。
    if not is_transition_allowed(STATUS_SHIPPED, STATUS_COMPLETED, ACTOR_SYSTEM):
        raise RuntimeError("order status transition is not allowed")

    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == STATUS_SHIPPED)
        .values(status=STATUS_COMPLETED)
        .execution_options(synchronize_session=False)
    )
    if db.execute(stmt).rowcount != 1:
        return False
    db.add(
        OrderEvent(
            order_id=order_id,
            from_status=STATUS_SHIPPED,
            to_status=STATUS_COMPLETED,
            actor_type=ACTOR_SYSTEM,
            created_at=now,
        )
    )
    db.flush()
    return True

"""Stripe 测试环境的对账记录：Checkout 会话记录。

依据 docs/DESIGN.md 1.12（提交 acf576e）：
- 「数据模型」的 StripeCheckout / StripeEvent / StripeRefund 一行：
  「Stripe 测试环境的对账记录，不存客户资料。`StripeCheckout`：所属订单、
  本站创建幂等键（唯一）、Checkout 会话 ID（可空，唯一）、PaymentIntent ID
  （可空，唯一）、金额（仙）、币种、状态（creating / open / complete /
  expired）与付款状态、实际付款方式类型（可空）、付款页地址、创建参数快照
  （金额、语言、返回地址、Stripe 到期时间）、创建与完成时间；同一订单至多
  一条不是 expired 的记录，由一个只在 creating、open、complete 时等于订单
  ID 的列上的唯一索引保证。」
- 「Stripe 测试环境支付」第 2 条：「同一订单的两条支付途径互斥：订单只要
  有一条不是 expired 的 Stripe 记录（creating、open 或 complete），不论
  开关当前取值，都不接受模拟支付、不另建会话」。
- 第 3 条：「先在加锁读订单的同一事务里写一条 `creating` 状态的
  `StripeCheckout`：生成本站的创建幂等键，并把这次创建的全部参数（金额、
  语言、返回地址、Stripe 到期时间等）存为快照」；「成功后锁住该记录写回
  会话 ID 与付款页地址：只在记录仍为 creating 时改为 `open`」；「同一订单
  至多一条不是 expired 的记录，由数据库唯一约束保证（记录上一个只在
  creating、open、complete 时等于订单 ID、expired 时为空的列，对它建唯一
  索引）」；「会话为 payment 模式，币种 MYR，金额等于订单应付快照」。
- 第 4 条：「记下 PaymentIntent ID，以及从该 PaymentIntent 实际成功的那笔
  Charge 读取的付款方式类型（`payment_method_details.type`，如 card、fpx、
  grabpay）；读不到时留空」。
- 第 5 条：「会话完成 24 小时后仍无结果就告警」；「定时任务也定期查询该
  会话的付款状态」。
- 第 10 条：「本站不读取、不保存 Stripe 返回的客户资料、卡号片段或账单
  地址」；「本站只存『数据模型』`StripeCheckout` / `StripeEvent` /
  `StripeRefund` 一行所列的对账字段」；「日志不记录 Stripe 请求与事件原文」。
以及 docs/HANDOFF.md 0.53 记录的 Kelvin 2026-10-15 落实决定（不改设计
文本）：第 6 条中会话已付款但核对不符而订单仍待支付时，写完退款尝试后把
这条会话记录标为 expired、释放订单的活动会话位。所以 expired 的记录可以
带付款状态 paid 与完成时间，由 SHOP-TASK-117 写入。

「同一订单至多一条不是 expired 的记录」的表示：active_order_id 在
creating、open、complete 时等于 order_id，expired 时为空，由检查约束逐条
保证；对它建唯一约束，空值不参与唯一比较（MySQL 与 SQLite 都如此）。它
不另建外键，只由检查约束保证等于订单列，订单列本身有外键。

确认时间是「订单由这个会话付款」的持久证据：本记录的确认把订单改为
demo_paid 时写入，只能出现在 complete 且付款状态为 paid 的记录上。创建
参数快照存发给 Stripe 的全部表单参数，由规则任务写入；其中的
customer_email 是站点配置的固定演示邮箱，不是客户资料。Stripe 到期时间与
快照里的 expires_at 相同，单列存放供定时任务查询。不存客户的姓名、邮箱、
电话、地址、卡号片段与 Stripe 响应原文。

时间一律是不带时区的 UTC；金额一律是整数仙。外键一律 RESTRICT：被会话
记录引用的订单不能物理删除。除设计指定可空的列外全部非空：检查约束遇到
空值会放行，非空约束不能省，检查约束里的「非空」一律写成 IS NOT NULL。
检查约束只用比较、IN、NOT IN、IS NULL 与 AND / OR，MySQL 与 SQLite 都能
执行。这里只建表与约束；事件表与退款尝试表由 SHOP-TASK-115 加到本文件；
支付途径判定、创建会话、确认、取消、webhook 与退款由之后的任务实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 会话记录状态。
# creating：已写入幂等键与快照，尚未取得会话（或调用结果未知）。
CHECKOUT_CREATING = "creating"
# open：已取得会话 ID 与付款页地址，等待访客付款。
CHECKOUT_OPEN = "open"
# complete：Stripe 会话已完成。
CHECKOUT_COMPLETE = "complete"
# expired：会话已过期或已作废、对账确认没有建成，
# 或核对不符后释放订单（HANDOFF 0.53）。
CHECKOUT_EXPIRED = "expired"

CHECKOUT_STATUSES = (CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE, CHECKOUT_EXPIRED)
# 占用订单活动会话位的状态：active_order_id 等于订单。
ACTIVE_CHECKOUT_STATUSES = (CHECKOUT_CREATING, CHECKOUT_OPEN, CHECKOUT_COMPLETE)

# Stripe 会话的付款状态（payment_status），可空。
PAYMENT_UNPAID = "unpaid"
PAYMENT_PAID = "paid"
PAYMENT_NO_PAYMENT_REQUIRED = "no_payment_required"

PAYMENT_STATUSES = (PAYMENT_UNPAID, PAYMENT_PAID, PAYMENT_NO_PAYMENT_REQUIRED)

# 第 3 条「币种 MYR」；Stripe 的币种代码是小写。
CURRENCY_MYR = "myr"

IDEMPOTENCY_KEY_MAX_LENGTH = 64
STRIPE_ID_MAX_LENGTH = 255
STATUS_MAX_LENGTH = 16
PAYMENT_STATUS_MAX_LENGTH = 24
PAYMENT_METHOD_TYPE_MAX_LENGTH = 32
CHECKOUT_URL_MAX_LENGTH = 2048


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class StripeCheckout(Base):
    """一条 Stripe Checkout 会话记录：所属订单、幂等键、Stripe 各 ID、金额、
    状态与对账时间。

    状态与各列的关系逐条由检查约束保证：creating 时会话 ID、付款页地址与
    付款状态都为空；open 时会话 ID 与付款页地址都非空；complete 时会话 ID
    与完成时间都非空；确认时间非空时状态为 complete 且付款状态为 paid；
    expired 时 active_order_id 为空，其余状态时等于订单。expired 的记录
    不另加限制，可以带付款状态 paid 与完成时间（迟到或核对不符的付款，
    SHOP-TASK-117）。按订单查该单的记录；按（状态、Stripe 到期时间）与
    （状态、上次查询时间）供定时任务取待处理的记录并轮转处理顺序。
    """

    __tablename__ = "stripe_checkouts"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("checkout_session_id"),
        UniqueConstraint("payment_intent_id"),
        # 同一订单至多一条不是 expired 的记录。
        UniqueConstraint("active_order_id"),
        Index("ix_stripe_checkouts_order_id", "order_id"),
        Index("ix_stripe_checkouts_status_expires_at", "status", "expires_at"),
        Index("ix_stripe_checkouts_status_last_polled_at", "status", "last_polled_at"),
        CheckConstraint(f"status IN {_sql_in(CHECKOUT_STATUSES)}", name="status_valid"),
        CheckConstraint(
            f"payment_status IS NULL OR payment_status IN {_sql_in(PAYMENT_STATUSES)}",
            name="payment_status_valid",
        ),
        CheckConstraint(f"currency = '{CURRENCY_MYR}'", name="currency_myr"),
        CheckConstraint("amount_sen > 0", name="amount_positive"),
        # 下面各条都只约束已知状态，未知状态只由 status_valid 拒绝。
        CheckConstraint(
            f"status <> '{CHECKOUT_EXPIRED}' OR active_order_id IS NULL",
            name="expired_has_no_active_order",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(ACTIVE_CHECKOUT_STATUSES)}"
            " OR (active_order_id IS NOT NULL AND active_order_id = order_id)",
            name="active_order_is_order",
        ),
        CheckConstraint(
            f"confirmed_at IS NULL OR (status = '{CHECKOUT_COMPLETE}'"
            f" AND payment_status IS NOT NULL AND payment_status = '{PAYMENT_PAID}')",
            name="confirmed_is_complete_paid",
        ),
        CheckConstraint(
            f"status <> '{CHECKOUT_CREATING}' OR (checkout_session_id IS NULL"
            " AND checkout_url IS NULL AND payment_status IS NULL)",
            name="creating_has_no_session",
        ),
        CheckConstraint(
            f"status <> '{CHECKOUT_OPEN}' OR (checkout_session_id IS NOT NULL"
            " AND checkout_url IS NOT NULL)",
            name="open_has_session_and_url",
        ),
        CheckConstraint(
            f"status <> '{CHECKOUT_COMPLETE}' OR (checkout_session_id IS NOT NULL"
            " AND completed_at IS NOT NULL)",
            name="complete_has_session_and_time",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    # 本站生成的创建幂等键，同时写进会话元数据。
    idempotency_key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH))
    # Stripe Checkout 会话 ID；creating 时为空。
    checkout_session_id: Mapped[str | None] = mapped_column(String(STRIPE_ID_MAX_LENGTH))
    # 确认付款时记下。
    payment_intent_id: Mapped[str | None] = mapped_column(String(STRIPE_ID_MAX_LENGTH))
    amount_sen: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(STATUS_MAX_LENGTH))
    # Stripe 会话的 payment_status；creating 时为空。
    payment_status: Mapped[str | None] = mapped_column(String(PAYMENT_STATUS_MAX_LENGTH))
    # 实际成功的那笔 Charge 的 payment_method_details.type；读不到时为空。
    payment_method_type: Mapped[str | None] = mapped_column(String(PAYMENT_METHOD_TYPE_MAX_LENGTH))
    # Stripe 返回的付款页地址；creating 时为空。
    checkout_url: Mapped[str | None] = mapped_column(String(CHECKOUT_URL_MAX_LENGTH))
    # 发给 Stripe 的全部表单参数，由规则任务写入；重试与对账用同一快照。
    create_params: Mapped[str] = mapped_column(Text)
    # Stripe 到期时间，与快照里的 expires_at 相同。
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 本记录的确认把订单改为 demo_paid 时写入：订单由这个会话付款的持久证据。
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 定时任务每处理一条就写入，用来轮转处理顺序。
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime)
    # creating、open、complete 时等于 order_id，expired 时为空；唯一。
    active_order_id: Mapped[int | None] = mapped_column(Integer)

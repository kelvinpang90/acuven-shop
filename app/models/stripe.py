"""Stripe 测试环境的对账记录：Checkout 会话记录、事件记录与退款尝试。

依据 docs/DESIGN.md 1.12（提交 acf576e）：
- 「数据模型」的 StripeCheckout / StripeEvent / StripeRefund 一行：
  「Stripe 测试环境的对账记录，不存客户资料。`StripeCheckout`：所属订单、
  本站创建幂等键（唯一）、Checkout 会话 ID（可空，唯一）、PaymentIntent ID
  （可空，唯一）、金额（仙）、币种、状态（creating / open / complete /
  expired）与付款状态、实际付款方式类型（可空）、付款页地址、创建参数快照
  （金额、语言、返回地址、Stripe 到期时间）、创建与完成时间；同一订单至多
  一条不是 expired 的记录，由一个只在 creating、open、complete 时等于订单
  ID 的列上的唯一索引保证。`StripeEvent`：Stripe 事件 ID（唯一）、类型、
  处理结果与时间，与事件引起的改动同一事务提交，不存原文。`StripeRefund`
  （每条是一次退款尝试）：所属 PaymentIntent、来源（退款申请或迟到付款的
  会话）及其 ID、Stripe 退款 ID（可空，唯一）、金额（仙）、状态（pending /
  succeeded / failed，Stripe 的 requires_action 记为 pending、canceled 记为
  failed）、调用次数、最后的错误类别、创建与成功时间、上次复查时间；本站 ID
  即幂等键并写进 Stripe 退款元数据；同一来源至多一条不是 failed 的尝试，由
  一个只在非 failed 时等于来源的列上的唯一索引保证。」
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
- 第 6 条：「服务端对这笔付款在 Stripe 测试环境全额退款，记为来源为该会话的
  `StripeRefund` 尝试」。
- 第 7 条：「事件 ID 的去重记录与它引起的订单、积分或退款改动在同一个数据库
  事务里提交：已提交过的事件直接回 200」；「其他类型回 200 不处理」；「不存、
  不记录事件原文」。
- 第 8 条：「同一事务另写一条 pending 的 `StripeRefund` 尝试，来源为该退款
  申请，金额为该现金退款额」；「每条 `StripeRefund` 是一次尝试，有自己的本站
  ID（即幂等键，并写进 Stripe 退款元数据）；同一来源至多一条不是 failed 的
  尝试，由数据库唯一约束保证」；「只接受 pending → succeeded、pending →
  failed、succeeded → failed 三种改动」；「最新一次尝试为 failed 时管理员可
  手动重试，重试是新建一条尝试（受上述唯一约束，重复点击与并发只有一个
  成功）」；「后一种不自动重试，记下错误类别并告警」；「发现变为 failed 就
  照上面改并告警」；「24 小时后不再调用退款接口，改为列出该 PaymentIntent
  退款的全部分页、按元数据里的尝试 ID 对账，找到就采用；找不到或无法列完都
  保持 pending、占用额度，告警由运营者在 Stripe 后台跟进」。
- 第 10 条：「本站不读取、不保存 Stripe 返回的客户资料、卡号片段或账单
  地址」；「本站只存『数据模型』`StripeCheckout` / `StripeEvent` /
  `StripeRefund` 一行所列的对账字段」；「日志不记录 Stripe 请求与事件原文」。
以及 docs/HANDOFF.md 0.53 记录的 Kelvin 2026-10-15 落实决定（不改设计
文本）：第 6 条中会话已付款但核对不符而订单仍待支付时，写完退款尝试后把
这条会话记录标为 expired、释放订单的活动会话位。所以 expired 的记录可以
带付款状态 paid 与完成时间，由 SHOP-TASK-117 写入。
以及 docs/HANDOFF.md 0.54 记录的 Kelvin 2026-10-19 决定（不改设计文本）：
「退款尝试表补告警时间列，『数据模型』一行所说『错误类别与时间』含此列；它
只给会反复出现的告警（24 小时后对账找不到或列不完、Stripe 退款对象与尝试
不符）去重，这类告警每条尝试至多一次；状态改动引起的告警（请求被明确拒绝、
成功后又失败）随改动只发生一次，不受它限制」。

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

事件记录只存事件 ID、类型、处理结果（processed：已按第 4、5、8 条处理；
ignored：其他类型，回 200 不处理）与处理时间，不存事件原文。

退款尝试的本站 ID 即自增主键，由规则任务用作 Stripe 幂等键并写进退款
元数据。来源类别为 refund_request 时退款申请列非空、会话列为空，为
checkout 时反之。「同一来源至多一条不是 failed 的尝试」的表示：
active_refund_request_id 与 active_checkout_id 两列，pending 与 succeeded
时与来源类别对应的那一列等于来源、另一列为空，failed 时都为空，由检查
约束逐条保证；两列各建唯一约束，空值不参与唯一比较，所以 failed 的尝试
不限条数。succeeded 改为 failed 时须同时清空活动列，释放来源。两列与
active_order_id 一样不另建外键。Stripe 退款 ID 不校验前缀（核验显示 FPX、
GrabPay 的退款 ID 不以 re_ 开头）。错误类别只存 Stripe 错误的类别代码，
不存错误原文。告警时间 alerted_at 记会反复出现的告警的首次告警时间，
为空表示还没发过这类告警，规则任务只在它为空时告警并写入；状态改动引起
的告警不看也不写它。

时间一律是不带时区的 UTC；金额一律是整数仙。外键一律 RESTRICT：被会话
记录引用的订单不能物理删除，被退款尝试引用的退款申请与会话记录也一样。
除设计指定可空的列外全部非空：检查约束遇到空值会放行，非空约束不能省，
检查约束里的「非空」一律写成 IS NOT NULL。检查约束只用比较、IN、NOT IN、
IS NULL 与 AND / OR，MySQL 与 SQLite 都能执行。这里只建表与约束；支付
途径判定、创建会话、确认、取消、webhook、迟到付款与退款规则由之后的任务
实现。
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

# 事件的处理结果。
# processed：第 4、5 条的 Checkout 事件或退款状态事件，已在同一事务里处理。
EVENT_PROCESSED = "processed"
# ignored：其他类型，回 200 不处理。
EVENT_IGNORED = "ignored"

EVENT_OUTCOMES = (EVENT_PROCESSED, EVENT_IGNORED)

# 退款尝试的状态；Stripe 的 requires_action 记为 pending、canceled 记为 failed。
STRIPE_REFUND_PENDING = "pending"
STRIPE_REFUND_SUCCEEDED = "succeeded"
STRIPE_REFUND_FAILED = "failed"

STRIPE_REFUND_STATUSES = (STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED, STRIPE_REFUND_FAILED)
# 占用来源的状态：与来源类别对应的活动列等于来源。
ACTIVE_STRIPE_REFUND_STATUSES = (STRIPE_REFUND_PENDING, STRIPE_REFUND_SUCCEEDED)

# 退款尝试的来源类别。
# refund_request：管理员批准的退款申请（第 8 条）。
SOURCE_REFUND_REQUEST = "refund_request"
# checkout：迟到或不符的付款所在的会话记录（第 6 条）。
SOURCE_CHECKOUT = "checkout"

REFUND_SOURCE_TYPES = (SOURCE_REFUND_REQUEST, SOURCE_CHECKOUT)

EVENT_TYPE_MAX_LENGTH = 64
EVENT_OUTCOME_MAX_LENGTH = 16
SOURCE_TYPE_MAX_LENGTH = 16
ERROR_TYPE_MAX_LENGTH = 64


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


class StripeEvent(Base):
    """一条已处理的 Stripe 事件：事件 ID（唯一）、类型、处理结果与处理时间。

    由 webhook 与事件引起的改动在同一事务里写入；事件 ID 唯一，已提交过的
    事件再来时由它查到、直接回 200。不存事件原文。
    """

    __tablename__ = "stripe_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        CheckConstraint(f"outcome IN {_sql_in(EVENT_OUTCOMES)}", name="outcome_valid"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stripe 事件 ID（evt_…）；唯一。
    event_id: Mapped[str] = mapped_column(String(STRIPE_ID_MAX_LENGTH))
    # Stripe 事件类型，如 checkout.session.completed。
    event_type: Mapped[str] = mapped_column(String(EVENT_TYPE_MAX_LENGTH))
    outcome: Mapped[str] = mapped_column(String(EVENT_OUTCOME_MAX_LENGTH))
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class StripeRefund(Base):
    """一次 Stripe 退款尝试：所属 PaymentIntent、来源、Stripe 退款 ID、金额、
    状态、调用次数、最后的错误类别与各时间。

    来源类别与来源列、状态与活动列的关系逐条由检查约束保证：来源类别为
    refund_request 时退款申请非空、会话为空，为 checkout 时反之；failed 时
    两个活动列都为空，pending 与 succeeded 时与来源类别对应的活动列等于
    来源、另一列为空；succeeded 时成功时间非空。succeeded 改为 failed 后
    成功时间保留，不另加限制。按 PaymentIntent 查该付款的全部尝试（合计
    核对）；按（状态、创建时间）供定时任务取待重试与待复查的尝试。
    """

    __tablename__ = "stripe_refunds"
    __table_args__ = (
        UniqueConstraint("stripe_refund_id"),
        # 同一来源至多一条不是 failed 的尝试。
        UniqueConstraint("active_refund_request_id"),
        UniqueConstraint("active_checkout_id"),
        Index("ix_stripe_refunds_payment_intent_id", "payment_intent_id"),
        Index("ix_stripe_refunds_status_created_at", "status", "created_at"),
        CheckConstraint(
            f"source_type IN {_sql_in(REFUND_SOURCE_TYPES)}",
            name="source_type_valid",
        ),
        CheckConstraint(f"status IN {_sql_in(STRIPE_REFUND_STATUSES)}", name="status_valid"),
        CheckConstraint("amount_sen > 0", name="amount_positive"),
        CheckConstraint("call_count >= 0", name="call_count_non_negative"),
        # 下面各条都只约束已知的来源类别与状态，未知取值只由上面两条 valid 拒绝。
        CheckConstraint(
            f"source_type <> '{SOURCE_REFUND_REQUEST}'"
            " OR (refund_request_id IS NOT NULL AND checkout_id IS NULL)",
            name="refund_request_source",
        ),
        CheckConstraint(
            f"source_type <> '{SOURCE_CHECKOUT}'"
            " OR (checkout_id IS NOT NULL AND refund_request_id IS NULL)",
            name="checkout_source",
        ),
        CheckConstraint(
            f"status <> '{STRIPE_REFUND_FAILED}'"
            " OR (active_refund_request_id IS NULL AND active_checkout_id IS NULL)",
            name="failed_has_no_active_source",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(ACTIVE_STRIPE_REFUND_STATUSES)}"
            f" OR source_type <> '{SOURCE_REFUND_REQUEST}'"
            " OR (active_refund_request_id IS NOT NULL"
            " AND active_refund_request_id = refund_request_id"
            " AND active_checkout_id IS NULL)",
            name="active_refund_request_is_source",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(ACTIVE_STRIPE_REFUND_STATUSES)}"
            f" OR source_type <> '{SOURCE_CHECKOUT}'"
            " OR (active_checkout_id IS NOT NULL"
            " AND active_checkout_id = checkout_id"
            " AND active_refund_request_id IS NULL)",
            name="active_checkout_is_source",
        ),
        CheckConstraint(
            f"status <> '{STRIPE_REFUND_SUCCEEDED}' OR succeeded_at IS NOT NULL",
            name="succeeded_has_succeeded_at",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    # 本站 ID，即 Stripe 退款的幂等键，并写进退款元数据。
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_intent_id: Mapped[str] = mapped_column(String(STRIPE_ID_MAX_LENGTH))
    source_type: Mapped[str] = mapped_column(String(SOURCE_TYPE_MAX_LENGTH))
    # 来源为退款申请时非空。
    refund_request_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("refund_requests.id", ondelete="RESTRICT"),
    )
    # 来源为迟到或不符付款的会话记录时非空。
    checkout_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("stripe_checkouts.id", ondelete="RESTRICT"),
    )
    # Stripe 退款 ID；调用结果未知或明确拒绝创建时为空；不校验前缀。
    stripe_refund_id: Mapped[str | None] = mapped_column(String(STRIPE_ID_MAX_LENGTH))
    amount_sen: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(STATUS_MAX_LENGTH))
    # 调用 Stripe 退款接口的次数；服务端默认 0。
    call_count: Mapped[int] = mapped_column(Integer, server_default="0")
    # 最后一次调用失败时 Stripe 错误的类别代码，不存错误原文。
    last_error_type: Mapped[str | None] = mapped_column(String(ERROR_TYPE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 会反复出现的告警（24 小时后对账找不到或列不完、Stripe 退款对象与尝试不符）
    # 的首次告警时间，这类告警每条尝试至多一次（HANDOFF 0.54）；状态改动引起的
    # 告警（请求被明确拒绝、成功后又失败）随改动只发生一次，不看也不写它。
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 定时任务每复查一条就写入。
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 不是 failed 且来源为退款申请时等于 refund_request_id，否则为空；唯一。
    active_refund_request_id: Mapped[int | None] = mapped_column(Integer)
    # 不是 failed 且来源为会话时等于 checkout_id，否则为空；唯一。
    active_checkout_id: Mapped[int | None] = mapped_column(Integer)

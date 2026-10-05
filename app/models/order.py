"""订单、订单行、逐件分摊快照、收货资料、模拟支付尝试、订单事件、确认收货记录。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Order / OrderItem、OrderRecipient、
PaymentAttempt / OrderEvent 三行，以及「订单与退款状态」「失败、并发与重试」
「权限与资料保护」「资料保留」。

金额一律是 MYR 整数仙，积分一律是整数积分（1 积分抵 1 仙），不用浮点或 Decimal；
时间一律是不带时区的 UTC。订单与收货资料长期保存，订单及其子表的外键一律 RESTRICT，
不级联删除；只有订单行到 SKU 规格的外键在规格被删除时置空，订单行靠快照照常显示。
订单的可空会员 ID 与游客订单认领状态由 SHOP-TASK-012 加上（见 app/models/member.py）；
优惠券、退款与参考外币都不在这些表里，由之后的任务另加。

检查约束只用比较、算术、LENGTH、LIKE、IN 与 IS NULL，MySQL 与 SQLite 都能执行。
MySQL 的 LENGTH 按字节计，所以幂等键只在库里保证非空，不超过 64 个字符由列长保证；
请求指纹库里只保证长 64，是否全为十六进制由写入方校验。
整单金额与逐件分摊合计一致、请求指纹的算法、同键同请求返回原结果与同键不同内容报冲突，
都由之后的下单与支付任务在事务内保证，这里只建列与唯一约束。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import MYSQL_TABLE_OPTIONS, Base

# 订单状态（「订单与退款状态」）。
STATUS_AWAITING_PAYMENT = "awaiting_demo_payment"
STATUS_PAID = "demo_paid"
STATUS_PACKED = "demo_packed"
STATUS_SHIPPED = "demo_shipped"
STATUS_COMPLETED = "demo_completed"
STATUS_CANCELLED = "demo_cancelled"

ORDER_STATUSES = (
    STATUS_AWAITING_PAYMENT,
    STATUS_PAID,
    STATUS_PACKED,
    STATUS_SHIPPED,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
)
# 已模拟支付的状态必有支付时间；未支付的状态必无。
PAID_STATUSES = (STATUS_PAID, STATUS_PACKED, STATUS_SHIPPED, STATUS_COMPLETED)
UNPAID_STATUSES = (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED)

# 操作者类别。
ACTOR_GUEST = "guest"
ACTOR_MEMBER = "member"
ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"

ACTOR_TYPES = (ACTOR_GUEST, ACTOR_MEMBER, ACTOR_ADMIN, ACTOR_SYSTEM)
# 手动确认收货的操作者：查单页的访客与「我的订单」的会员；系统自动完成不写确认收货记录。
RECEIPT_ACTOR_TYPES = (ACTOR_GUEST, ACTOR_MEMBER)

# 模拟支付方式，对应 docs/UX-COPY.md 的 pay.method_card、pay.method_bank、pay.method_ewallet。
PAYMENT_METHOD_CARD = "demo_card"
PAYMENT_METHOD_BANK = "demo_bank"
PAYMENT_METHOD_EWALLET = "demo_ewallet"

PAYMENT_METHODS = (PAYMENT_METHOD_CARD, PAYMENT_METHOD_BANK, PAYMENT_METHOD_EWALLET)

PAYMENT_SUCCEEDED = "succeeded"
PAYMENT_FAILED = "failed"

PAYMENT_RESULTS = (PAYMENT_SUCCEEDED, PAYMENT_FAILED)

# 游客订单认领状态（「权限与资料保护」）：open 是可认领的游客订单；claimed 是已被认领、
# 不可撤销的标记；not_claimable 是会员下单时即标的不可认领。标记不含手机号，
# 注销清空会员 ID 后保留，所以 claimed 与 not_claimable 的订单会员 ID 可以为空。
CLAIM_OPEN = "open"
CLAIM_CLAIMED = "claimed"
CLAIM_NOT_CLAIMABLE = "not_claimable"

CLAIM_STATUSES = (CLAIM_OPEN, CLAIM_CLAIMED, CLAIM_NOT_CLAIMABLE)

ORDER_NUMBER_LENGTH = 16
REQUEST_FINGERPRINT_LENGTH = 64


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _utcnow() -> datetime:
    # 存不带时区的 UTC：MySQL 的 DATETIME 没有时区，读出来一律按 UTC 解释。
    return datetime.now(UTC).replace(tzinfo=None)


class Order(Base):
    """一张订单的金额快照与状态。

    应付 = 商品小计 − 券折扣 − 积分抵扣 + 运费；券与积分都不抵运费，所以应付不低于运费。
    支付到期时间由下单写为创建时间加 15 分钟，超时取消与支付页倒计时都以它为准。

    认领状态的两条检查约束挂在列上而不是 __table_args__：tests/test_order_models.py 只按
    已有各列对 __table__.constraints 逐条求值，表级约束引用新列会让它报错。SQLite 允许列上
    的检查约束引用其他列，按模型建表照常生效；MySQL 的表只由迁移建，迁移用表级的
    ALTER TABLE 加这两条约束（MySQL 不允许列上的检查约束引用其他列，所以不能在 MySQL 上
    按模型 create_all）。认领状态的 Python 默认值是 open（游客订单）；会员下单须写
    not_claimable，忘写时 member_claim_not_open 拒绝。
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_number"),
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            f"LENGTH(order_number) = {ORDER_NUMBER_LENGTH}",
            name="order_number_length",
        ),
        CheckConstraint(f"status IN {_sql_in(ORDER_STATUSES)}", name="status_valid"),
        CheckConstraint("subtotal_sen >= 0", name="subtotal_sen_non_negative"),
        CheckConstraint("coupon_discount_sen >= 0", name="coupon_discount_sen_non_negative"),
        CheckConstraint("points_redeemed >= 0", name="points_redeemed_non_negative"),
        CheckConstraint("shipping_fee_sen >= 0", name="shipping_fee_sen_non_negative"),
        CheckConstraint("total_sen >= 0", name="total_sen_non_negative"),
        CheckConstraint("points_earned >= 0", name="points_earned_non_negative"),
        CheckConstraint(
            "coupon_discount_sen + points_redeemed <= subtotal_sen",
            name="discounts_within_subtotal",
        ),
        CheckConstraint(
            "total_sen = subtotal_sen - coupon_discount_sen - points_redeemed + shipping_fee_sen",
            name="total_formula",
        ),
        CheckConstraint("total_sen >= shipping_fee_sen", name="total_covers_shipping"),
        # 分成两条，未知状态只由 status_valid 拒绝。
        CheckConstraint(
            f"status NOT IN {_sql_in(PAID_STATUSES)} OR paid_at IS NOT NULL",
            name="paid_status_has_paid_at",
        ),
        CheckConstraint(
            f"status NOT IN {_sql_in(UNPAID_STATUSES)} OR paid_at IS NULL",
            name="unpaid_status_no_paid_at",
        ),
        CheckConstraint("LENGTH(idempotency_key) >= 1", name="idempotency_key_not_empty"),
        CheckConstraint(
            f"LENGTH(request_fingerprint) = {REQUEST_FINGERPRINT_LENGTH}",
            name="request_fingerprint_length",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 不可猜测的订单号：16 位 Crockford Base32，见 app/services/order_rules.py。
    order_number: Mapped[str] = mapped_column(String(ORDER_NUMBER_LENGTH))
    status: Mapped[str] = mapped_column(String(32))
    subtotal_sen: Mapped[int] = mapped_column(Integer)
    coupon_discount_sen: Mapped[int] = mapped_column(Integer)
    # 积分数；1 积分抵 1 仙。
    points_redeemed: Mapped[int] = mapped_column(Integer)
    shipping_fee_sen: Mapped[int] = mapped_column(Integer)
    total_sen: Mapped[int] = mapped_column(Integer)
    points_earned: Mapped[int] = mapped_column(Integer)
    # 下单时所用运费区与该行的版本号，之后改运费不影响这张订单。
    shipping_zone_code: Mapped[str] = mapped_column(String(5))
    shipping_rate_version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    # 请求内容的 SHA-256，64 位十六进制。
    request_fingerprint: Mapped[str] = mapped_column(String(REQUEST_FINGERPRINT_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    payment_expires_at: Mapped[datetime] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 会员不物理删除；注销时由注销流程把会员 ID 清空。
    member_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("members.id", ondelete="RESTRICT"),
        index=True,
    )
    claim_status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint(f"claim_status IN {_sql_in(CLAIM_STATUSES)}", name="claim_status_valid"),
        CheckConstraint(
            f"member_id IS NULL OR claim_status <> '{CLAIM_OPEN}'",
            name="member_claim_not_open",
        ),
        default=CLAIM_OPEN,
    )


class OrderItem(Base):
    """订单行：下单时的 SKU、三语名称与规格说明、单价与件数快照。

    三语快照在下单时已按回退英文的规则取好；规格说明对无规格商品可为空串。
    """

    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "line_index"),
        CheckConstraint("line_index >= 0", name="line_index_non_negative"),
        CheckConstraint("unit_price_sen >= 0", name="unit_price_sen_non_negative"),
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        CheckConstraint(
            "line_subtotal_sen = unit_price_sen * quantity",
            name="line_subtotal_formula",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    # 订单内从 0 起的行序，即计价展开时的稳定行序。
    line_index: Mapped[int] = mapped_column(Integer)
    # 规格被删除时置空，订单行凭快照照常显示。
    variant_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("product_variants.id", ondelete="SET NULL"),
    )
    sku: Mapped[str] = mapped_column(String(64))
    product_name_en: Mapped[str] = mapped_column(String(200))
    product_name_zh: Mapped[str] = mapped_column(String(200))
    product_name_ms: Mapped[str] = mapped_column(String(200))
    variant_label_en: Mapped[str] = mapped_column(String(300))
    variant_label_zh: Mapped[str] = mapped_column(String(300))
    variant_label_ms: Mapped[str] = mapped_column(String(300))
    unit_price_sen: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    line_subtotal_sen: Mapped[int] = mapped_column(Integer)


class OrderItemUnit(Base):
    """逐件分摊快照，与 app/services/pricing.py 的 UnitAllocation 一一对应。

    line_index 由所属订单行给出；unit_index、original_price、coupon_discount、
    points_discount、cash_paid、points_earned 依次存为 unit_index、original_price_sen、
    coupon_discount_sen、points_discount、cash_paid_sen、points_earned。
    """

    __tablename__ = "order_item_units"
    __table_args__ = (
        UniqueConstraint("order_item_id", "unit_index"),
        CheckConstraint("unit_index >= 0", name="unit_index_non_negative"),
        CheckConstraint("original_price_sen >= 0", name="original_price_sen_non_negative"),
        CheckConstraint("coupon_discount_sen >= 0", name="coupon_discount_sen_non_negative"),
        CheckConstraint("points_discount >= 0", name="points_discount_non_negative"),
        CheckConstraint("cash_paid_sen >= 0", name="cash_paid_sen_non_negative"),
        CheckConstraint("points_earned >= 0", name="points_earned_non_negative"),
        CheckConstraint(
            "cash_paid_sen = original_price_sen - coupon_discount_sen - points_discount",
            name="cash_paid_formula",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order_items.id", ondelete="RESTRICT"),
    )
    # 订单行内从 0 起的件序。
    unit_index: Mapped[int] = mapped_column(Integer)
    original_price_sen: Mapped[int] = mapped_column(Integer)
    coupon_discount_sen: Mapped[int] = mapped_column(Integer)
    # 积分数；1 积分抵 1 仙。
    points_discount: Mapped[int] = mapped_column(Integer)
    cash_paid_sen: Mapped[int] = mapped_column(Integer)
    points_earned: Mapped[int] = mapped_column(Integer)


class OrderRecipient(Base):
    """收货资料原文，每张订单恰好一条；长期保存，不加密、不到期删除、不匿名化。

    只有「边界与原则」列出的字段。地区只有一列：马来西亚时是州属代码（数据库只保证
    以 MY- 开头且长 5 位，是否为 MY-01 到 MY-16 由写入方校验），其他国家是可空的自由文本。
    电话库里只保证以加号开头且不超过 16 个字符；是否为合法 E.164 由下单接口校验。
    邮编非空，没有邮编的国家由写入方存空串。
    """

    __tablename__ = "order_recipients"
    __table_args__ = (
        UniqueConstraint("order_id"),
        CheckConstraint("phone LIKE '+%' AND LENGTH(phone) <= 16", name="phone_format"),
        CheckConstraint("LENGTH(country_code) = 2", name="country_code_length"),
        CheckConstraint(
            "country_code <> 'MY'"
            " OR (region IS NOT NULL AND LENGTH(region) = 5 AND region LIKE 'MY-%')",
            name="region_matches_country",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    name: Mapped[str] = mapped_column(String(200))
    # 规范化的 E.164，查单按它比对。
    phone: Mapped[str] = mapped_column(String(16), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(500))
    postal_code: Mapped[str] = mapped_column(String(20))


class PaymentAttempt(Base):
    """一次模拟支付：所选方式与结果。不存任何卡号或账户资料。

    失败的尝试不改变订单状态。
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(f"method IN {_sql_in(PAYMENT_METHODS)}", name="method_valid"),
        CheckConstraint(f"result IN {_sql_in(PAYMENT_RESULTS)}", name="result_valid"),
        CheckConstraint("LENGTH(idempotency_key) >= 1", name="idempotency_key_not_empty"),
        CheckConstraint(
            f"LENGTH(request_fingerprint) = {REQUEST_FINGERPRINT_LENGTH}",
            name="request_fingerprint_length",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    method: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(10))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(REQUEST_FINGERPRINT_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class OrderEvent(Base):
    """一次状态变更：前后状态、操作者类别与时间。

    没有任何自由文本列，事件不写收货资料原文。下单事件的迁移前状态为空，
    其余事件都有迁移前状态；没有迁移能回到 awaiting_demo_payment。
    """

    __tablename__ = "order_events"
    __table_args__ = (
        CheckConstraint(
            f"from_status IS NULL OR from_status IN {_sql_in(ORDER_STATUSES)}",
            name="from_status_valid",
        ),
        CheckConstraint(f"to_status IN {_sql_in(ORDER_STATUSES)}", name="to_status_valid"),
        CheckConstraint(
            f"(from_status IS NULL AND to_status = '{STATUS_AWAITING_PAYMENT}')"
            f" OR (from_status IS NOT NULL AND to_status <> '{STATUS_AWAITING_PAYMENT}')",
            name="placement_has_no_from_status",
        ),
        CheckConstraint(f"actor_type IN {_sql_in(ACTOR_TYPES)}", name="actor_type_valid"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor_type: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ReceiptConfirmation(Base):
    """一次手动确认收货：所属订单、幂等键、请求指纹、操作者类别与时间。

    依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 1 条：确认收货使用幂等键和
    数据库唯一约束。每张订单至多一条（一张订单只记一次手动确认），幂等键全表唯一。
    模拟发货满 7 天的自动完成不写这里。不存任何个人资料。
    请求指纹由 app/services/order_lookup.py 按订单 ID 算出；库里只保证长 64。
    """

    __tablename__ = "receipt_confirmations"
    __table_args__ = (
        UniqueConstraint("order_id"),
        UniqueConstraint("idempotency_key"),
        CheckConstraint("LENGTH(idempotency_key) >= 1", name="idempotency_key_not_empty"),
        CheckConstraint(
            f"LENGTH(request_fingerprint) = {REQUEST_FINGERPRINT_LENGTH}",
            name="request_fingerprint_length",
        ),
        CheckConstraint(
            f"actor_type IN {_sql_in(RECEIPT_ACTOR_TYPES)}",
            name="actor_type_valid",
        ),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="RESTRICT"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(REQUEST_FINGERPRINT_LENGTH))
    actor_type: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

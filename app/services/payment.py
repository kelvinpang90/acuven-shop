"""模拟支付、取消待支付订单与支付超时取消；支付页 P06 与结果页 P07 的订单视图。

依据 docs/DESIGN.md 1.10（提交 e3b3505）：
- 「订单与退款状态」：模拟支付失败仍停在 awaiting_demo_payment 并记录失败尝试，成功进入
  demo_paid；待支付订单可由下单者在支付页取消，或超时为 demo_cancelled，释放库存；
  已模拟支付订单不走取消；所有状态迁移校验当前状态与操作者，事务内写订单与事件。
- 「计价、优惠、积分与库存」第 6 条：支付成功消耗预留，失败重试期间保持预留，
  15 分钟未支付自动取消并释放。
- 「失败、并发与重试」第 1 条：模拟支付使用幂等键和数据库唯一约束，相同键相同请求返回
  原结果，同键不同内容报冲突。
- 「数据模型」的 PaymentAttempt / OrderEvent 一行：事件不写收货资料原文。

券与积分依据 docs/DESIGN.md 1.11（提交 2d13250）「计价、优惠、积分与库存」
第 1、3 条与「订单与退款状态」（取消或超时释放库存、券及积分预占；模拟支付
成功后获得积分），以及 docs/HANDOFF.md 0.46、0.47 记录的 Kelvin 决定
（预占与核销都计用券次数；已预占的积分以预占时为准；获得积分按下单时的快照
在支付成功时发放）：
- 支付成功：同一事务里、带条件的 UPDATE 命中之后，以 use_coupon 核销用券记录
  （没有记录时不写），再以加锁读重新读取订单的会员 ID 与会员状态；会员 ID 非空、
  会员为 active 且订单快照 points_earned 大于 0 时以 earn_points 按快照发放，
  否则不发放。
- 本人取消与超时取消：同一事务里、带条件的 UPDATE 命中并加回库存之后，以
  release_coupon 与 release_points 释放（游客订单两者都不写；批次已过期或会员
  已注销时 release_points 随即记为到期或作废）。UPDATE 未命中时整个事务回滚，
  不释放。

支付成功的状态迁移（带条件的 UPDATE 改为 demo_paid 并写支付时间、写一条事件、
_settle_paid 核销用券并按快照发放积分）抽成 mark_paid，由模拟支付与 Stripe 确认
（app/services/stripe_confirm.py，依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe
测试环境支付」第 4 条「按模拟支付成功的同一事务进入 demo_paid」）共用：模拟支付带
「未到期」条件、另写支付尝试，事件操作者为 guest 或 member；Stripe 确认不带「未到期」
条件（第 4 条「确认不看订单的 15 分钟支付到期时间，只看订单是否仍待支付」）、不写
支付尝试，事件操作者为 system。

访问授权、CSRF 与请求格式由调用方（app/api/pay.py，以后的会员接口）先行校验；
这里只按订单 ID 操作。支付与本人取消的操作者由调用方以 actor 给出（guest 或
member，默认 guest），状态迁移的检查与事件都用它；超时取消的操作者为 system。
状态改动一律用带条件的 UPDATE（状态仍为 awaiting_demo_payment 才改），未命中即回滚，
所以并发的支付、取消与超时取消对同一订单只有一个生效，库存、券与积分至多释放
一次。加回库存时同一规格的多行先合并件数，再按 SKU 规格 ID 从小到大逐个更新，
与下单扣库存的顺序一致。

锁顺序（只在 MySQL 上成立：SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成
FOR UPDATE）：支付成功、本人取消与超时取消在带条件的 UPDATE 之前，先以普通读
取得订单的会员 ID，非空时以 SELECT … FOR UPDATE 锁住该会员的行（会员已注销
也锁），为空（游客订单或会员注销后已清空）时不预先锁；之后才由 UPDATE 锁订单行。
于是单张订单的会员下单（会员 → 库存）、取消（会员 → 订单 → 库存 → 用券记录 →
积分）、支付成功（会员 → 订单 → 用券记录 → 积分）与注销（会员 → 订单）都先取
会员锁（mark_paid 的两个调用方相同；Stripe 确认另在订单之后、用券之前锁 Stripe 会话
记录）。会员 ID 已清空的订单在取消时由 release_points 在库存之后才锁会员行，
这时该会员已注销，不会再下单或注销。这依赖超时取消逐张订单真实提交
（SHOP-TASK-097 之后，定时任务与每日库存重置都不再在外层事务里连续取消多张
订单），所以不会跨订单持有库存与券的锁。

有 Stripe 记录的订单依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 2 条
（「同一订单的两条支付途径互斥：订单只要有一条不是 expired 的 Stripe 记录……都不接受模拟支付」；
「现有的模拟支付提交、本人取消、查看触发的超时取消与定时任务都要改为在锁住订单的同一事务里先查
该单的 Stripe 记录」）、第 5 条（「有 Stripe 记录的订单，取消一律先处理会话」；「查看入口只对没有
Stripe 记录（或只有 expired 记录）的订单执行超时取消，其余留给定时任务」；「Stripe 不可用时本人
取消提示稍后再试……不以本地时钟过了会话到期时间代替 Stripe 的状态确认」）与「计价、优惠、积分与
库存」第 6 条，以及 docs/HANDOFF.md 0.53 的核验结论（再作废已过期或已完成的会话都 400，实现时
400 之后查询会话决定）：
- 模拟支付：订单有不是 expired 的 Stripe 记录，或按 SHOP-TASK-112 的途径判定为 stripe 时抛
  PaymentPathMismatch，不写支付尝试。成功的一路在带条件的 UPDATE 命中之后、失败的一路在写支付
  尝试之前以 SELECT … FOR UPDATE 锁住订单行，都再以加锁读重查一次 Stripe 记录，堵住与发起会话
  并发的情形（发起只锁订单行，见 app/services/stripe_checkout.py）。
- 本人取消：有记录时先处理会话（creating 对账、open 请求作废、complete 按 SHOP-TASK-113 确认），
  只有会话确认结束且未付款才经 cancel_after_stripe_end 取消；不能确定时抛 StripeUnavailable，
  订单保持待支付、库存继续预留。
- 超时取消（expire_overdue_orders）只取消没有不是 expired 的 Stripe 记录的订单；有记录的订单
  留给定时任务（SHOP-TASK-116），也经 cancel_after_stripe_end 取消。
- 所有取消在带条件的 UPDATE 命中之后都以加锁读重查一次不是 expired 的 Stripe 记录，有则由调用方
  回滚。
- cancel_after_stripe_end 的仅限关键字参数 record_event 只供 webhook（SHOP-TASK-127）使用，
  依据第 7 条「事件 ID 的去重记录与它引起的订单、积分或退款改动在同一个数据库事务里提交」：
  给出时在返回 kept 与 cancelled 的两处提交之前先 flush、再调用它写事件记录，返回 changed 与
  回滚时不调用。
调用 Stripe 时不持有任何数据库锁；之后按统一顺序「会员 → 订单 → Stripe 会话记录 → 用券 →
积分」加锁并重查。app/services/stripe_checkout.py 与 stripe_confirm.py 在模块级导入本模块，
所以本模块在函数里才导入它们。

支付页视图的 Stripe 字段依据 DESIGN 1.12「Stripe 测试环境支付」第 2 条（支付途径）、第 3 条
（creating 与 24 小时转人工）、第 6 条（迟到付款按来源为会话的退款尝试处理）与 docs/UX.md 0.11
P06、P07「服务端读取订单时一并给出本单的支付途径与 Stripe 状态」，以及 docs/HANDOFF.md 0.54
的落实写法（P06 打开时已有 complete 会话由前端调用确认接口，订单视图保持只读）：
- payment_route：订单待支付时取 SHOP-TASK-112 的 payment_route（最低收款额同 submit_payment
  取 get_settings()），其他状态为空。
- stripe_state：订单不是 expired 的记录的状态 creating、open、complete；creating 且已写告警
  时间为 under_review；没有为空。
- stripe_refunded：本单任一会话记录有来源为 checkout 的退款尝试（不论状态）。
pay_orders 按订单批量读取，只读、不加锁、不调用 Stripe。

本模块不写日志；异常消息不含个人资料、订单号、幂等键或令牌。库里的时间一律是不带时区的
UTC，返回的视图里换成带 UTC 时区的时间。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Member,
    Order,
    OrderEvent,
    OrderItem,
    OrderRecipient,
    PaymentAttempt,
    ProductVariant,
    StripeCheckout,
    StripeRefund,
)
from app.models.member import MEMBER_ACTIVE
from app.models.order import (
    ACTOR_GUEST,
    ACTOR_MEMBER,
    ACTOR_SYSTEM,
    PAYMENT_FAILED,
    PAYMENT_METHOD_BANK,
    PAYMENT_METHOD_CARD,
    PAYMENT_METHOD_EWALLET,
    PAYMENT_SUCCEEDED,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PAID,
)
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    PAYMENT_PAID,
    SOURCE_CHECKOUT,
)
from app.services.catalog import Language
from app.services.coupons import release_coupon, use_coupon
from app.services.order_rules import is_transition_allowed
from app.services.points import earn_points, release_points
from app.services.stripe_gateway import CheckoutSession, StripeGateway, StripeOutcome

PaymentMethod = Literal["card", "bank", "ewallet"]
PaymentResult = Literal["success", "failure"]
CancelledBy = Literal["self", "timeout"]
# 支付与本人取消的操作者：游客或会员（超时取消固定为 system，不由调用方给出）。
PayActor = Literal["guest", "member"]
_PAY_ACTORS = (ACTOR_GUEST, ACTOR_MEMBER)
# 支付页视图的支付途径（SHOP-TASK-112 的 PaymentRoute 的取值）与 Stripe 记录状态。
PayRoute = Literal["stripe", "simulated"]
StripeState = Literal["creating", "open", "complete", "under_review"]
_STRIPE_STATES: dict[str, StripeState] = {
    CHECKOUT_CREATING: "creating",
    CHECKOUT_OPEN: "open",
    CHECKOUT_COMPLETE: "complete",
}
# 会话结束后取消的操作者：本人取消为 guest 或 member，定时任务为 system。
_END_ACTORS = (ACTOR_GUEST, ACTOR_MEMBER, ACTOR_SYSTEM)

# cancel_after_stripe_end 的加锁轮数上限：会员 ID 在普通读与加锁之间被改（认领或注销）时再走一轮。
_MAX_ROUNDS = 3

# 接口里的支付方式与结果 → 库里的值。card、bank、ewallet 对应 pay.method_card、
# pay.method_bank、pay.method_ewallet。
_METHODS: dict[str, str] = {
    "card": PAYMENT_METHOD_CARD,
    "bank": PAYMENT_METHOD_BANK,
    "ewallet": PAYMENT_METHOD_EWALLET,
}
_RESULTS: dict[str, str] = {"success": PAYMENT_SUCCEEDED, "failure": PAYMENT_FAILED}
_METHOD_NAMES = {stored: name for name, stored in _METHODS.items()}
_RESULT_NAMES = {stored: name for name, stored in _RESULTS.items()}


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的支付请求。"""


class _OrderStateError(Exception):
    """订单状态不允许本次操作；status 为订单当前状态。"""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class OrderNotPayable(_OrderStateError):
    """订单已模拟支付或已取消，或带条件的 UPDATE 未命中（并发的取消或成功先提交）。"""


class OrderExpired(_OrderStateError):
    """订单已过支付到期时间，已先按超时取消处理。"""


class OrderNotCancellable(_OrderStateError):
    """订单已模拟支付或之后的状态，按设计走退款流程。"""


class PaymentPathMismatch(Exception):
    """订单的支付途径为 Stripe（有不是 expired 的 Stripe 记录，或按途径判定为 stripe），不接受
    模拟支付；订单不变。之后的接口归为 method_changed。"""


class StripeUnavailable(Exception):
    """有 Stripe 记录的订单暂不能取消：Stripe 不可用，或会话是否结束、付款是否成功还不能确定
    （含交人工核对）。订单保持待支付、库存继续预留；之后的接口归为稍后再试。"""


class StripeEndResult(StrEnum):
    """cancel_after_stripe_end 的结果。"""

    # 订单已取消，库存、券与积分已释放（给了记录 ID 时记录同一事务记为 expired）。
    CANCELLED = "cancelled"
    # 订单已不是待支付或另有不是 expired 的记录：订单不动（给了记录 ID 时只提交它的 expired）。
    KEPT = "kept"
    # 记录在调用方查询 Stripe 之后被改动过：不改记录与订单，由调用方重新处理。
    CHANGED = "changed"


class PayLine(BaseModel):
    """订单行快照：按请求语言取的名称与规格说明（下单时已按回退英文的规则取好）。"""

    name: str
    variant_label: str
    quantity: int
    unit_price_sen: int
    line_subtotal_sen: int


class PayRecipient(BaseModel):
    """收货资料原文。地区：马来西亚为州属代码，其他国家为自由文本或空。"""

    name: str
    phone: str
    country_code: str
    region: str | None
    address: str
    postal_code: str


class LastPayment(BaseModel):
    method: PaymentMethod
    result: PaymentResult
    created_at: datetime


class PayOrder(BaseModel):
    """支付页与结果页所需的一张订单。时间都带 UTC 时区。"""

    order_number: str
    status: str
    created_at: datetime
    payment_expires_at: datetime
    server_time: datetime
    lines: list[PayLine]
    subtotal_sen: int
    # 券折扣、积分抵扣与获得积分取订单快照；游客订单都为 0。
    coupon_discount_sen: int
    points_redeemed: int
    shipping_fee_sen: int
    total_sen: int
    points_earned: int
    # 订单摘要不依赖收货资料记录存在：没有那一条时为空。
    recipient: PayRecipient | None
    # 最近一次模拟支付；没有为空。
    last_payment: LastPayment | None
    # 已取消时的取消方：self 为下单者本人，timeout 为超时；未取消为空。
    cancelled_by: CancelledBy | None
    # 支付途径：待支付时为 stripe 或 simulated，其他状态为空。
    payment_route: PayRoute | None
    # 不是 expired 的 Stripe 记录的状态；creating 且已告警（转人工核对）为 under_review；
    # 没有这样的记录为空。
    stripe_state: StripeState | None
    # 本单有来源为会话的 Stripe 退款尝试（迟到或不符的付款，不论尝试状态）。
    stripe_refunded: bool


class PaymentOutcome(BaseModel):
    """一次模拟支付：本次（重放时为原来那次）的方式与结果，订单当前状态与支付时间。"""

    method: PaymentMethod
    result: PaymentResult
    status: str
    paid_at: datetime | None


class CancelOutcome(BaseModel):
    status: str
    cancelled_by: CancelledBy | None


def payment_fingerprint(order_id: int, method: str, result: str) -> str:
    """支付请求指纹：订单 ID、支付方式与结果（接口里的取值）的 JSON 的 SHA-256 十六进制。

    JSON 键排序、无空白，同样的内容总得到同样的字节。
    """
    content = {"order_id": order_id, "method": method, "result": result}
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def submit_payment(
    db: Session,
    order_id: int,
    method: PaymentMethod,
    result: PaymentResult,
    idempotency_key: str,
    now: datetime,
    *,
    actor: PayActor = "guest",
) -> tuple[PaymentOutcome, bool]:
    """提交一次模拟支付，返回结果与是否新写了支付尝试（重放为假）。

    actor 为操作者（guest 或 member，其他值抛 ValueError，先于访问数据库）。最低收款额取
    get_settings() 的 stripe_min_amount_sen。
    1. 按幂等键查支付尝试：指纹相同返回原结果，不同抛 IdempotencyConflict；都不改订单，不受
       途径检查影响。
    2. 订单已模拟支付或已取消：抛 OrderNotPayable，不写支付尝试。
    3. 订单有不是 expired 的 Stripe 记录：抛 PaymentPathMismatch，订单不变。
    4. 已过支付到期时间：先按超时取消处理，再抛 OrderExpired，不写支付尝试。
    5. 按 SHOP-TASK-112 的途径判定为 stripe：抛 PaymentPathMismatch，订单不变。
    6. 失败：以 SELECT … FOR UPDATE 锁住订单行（只锁订单，与发起会话的加锁一致），重查订单仍待
       支付（否则回滚，同键的并发请求已提交则按 1 处理，否则抛 OrderNotPayable）且没有不是
       expired 的 Stripe 记录（否则回滚、抛 PaymentPathMismatch），再只写支付尝试，订单状态、
       库存、券、积分与事件都不变，可再次提交。
    7. 成功：在同一事务里先锁订单的会员行（会员 ID 非空时），再用带条件的 UPDATE（状态仍为
       awaiting_demo_payment 且未到期）改为 demo_paid 并写支付时间；命中后以加锁读重查一次不是
       expired 的 Stripe 记录（有则回滚、抛 PaymentPathMismatch），再写一条操作者为 actor 的
       事件，核销用券记录、按订单快照发放获得积分（见模块说明）；之后写支付尝试。库存不变
       （预留即被消耗）。UPDATE 未命中时回滚：同键的并发请求已提交则按 1 处理，否则抛
       OrderNotPayable。
    写支付尝试撞上幂等键唯一约束时回滚（连同已写的迁移、事件、核销与发放），按 1 处理。重放
    不再核销或发放。
    """
    _check_actor(actor)
    fingerprint = payment_fingerprint(order_id, method, result)
    existing = _attempt_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint), False

    order = _load_order(db, order_id)
    if order.status != STATUS_AWAITING_PAYMENT:
        raise OrderNotPayable(order.status)
    if _active_checkout_id(db, order_id) is not None:
        raise PaymentPathMismatch
    if now >= order.payment_expires_at:
        expire_overdue_orders(db, now, order_ids=[order_id])
        status = _load_order(db, order_id).status
        if status == STATUS_CANCELLED:
            raise OrderExpired(status)
        if _active_checkout_id(db, order_id) is not None:
            # 超时取消在 UPDATE 之后的重查里见到了并发发起的会话，跳过了这张。
            raise PaymentPathMismatch
        raise OrderNotPayable(status)
    if _stripe_route(db, order_id):
        raise PaymentPathMismatch

    succeeded = result == "success"
    if succeeded:
        if not _update_paid(db, order_id, actor, now, unexpired_only=True):
            return _replay_or_not_payable(db, order_id, idempotency_key, fingerprint), False
        if _active_checkout_id(db, order_id, lock=True) is not None:
            # 与发起会话并发：会话记录先写入，模拟支付让位。
            db.rollback()
            raise PaymentPathMismatch
        _finish_paid(db, order_id, actor, now)
    else:
        locked = _lock_order(db, order_id)
        if locked.status != STATUS_AWAITING_PAYMENT:
            return _replay_or_not_payable(db, order_id, idempotency_key, fingerprint), False
        if _active_checkout_id(db, order_id, lock=True) is not None:
            db.rollback()
            raise PaymentPathMismatch

    attempt = PaymentAttempt(
        order_id=order_id,
        method=_METHODS[method],
        result=_RESULTS[result],
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_at=now,
    )
    db.add(attempt)
    try:
        db.flush()
    except IntegrityError:
        # 同键的并发请求先提交了：回滚本次的改动，按它的结果重放或报冲突。
        db.rollback()
        existing = _attempt_by_key(db, idempotency_key)
        if existing is None:
            raise
        return _replay(db, existing, fingerprint), False

    status = STATUS_AWAITING_PAYMENT
    paid_at = None
    if succeeded:
        status = STATUS_PAID
        paid_at = now.replace(tzinfo=UTC)
    db.commit()
    return PaymentOutcome(method=method, result=result, status=status, paid_at=paid_at), True


def mark_paid(
    db: Session, order_id: int, actor: str, now: datetime, *, unexpired_only: bool
) -> bool:
    """支付成功的状态迁移，模拟支付与 Stripe 确认共用；只 flush、不提交，返回是否命中。

    先经迁移判定检查 actor（不在迁移表里抛 RuntimeError），以普通读取得订单的会员 ID、非空时
    锁住该会员的行，再用带条件的 UPDATE（状态仍为 awaiting_demo_payment；unexpired_only 为真
    时还须未到支付到期时间）改为 demo_paid、支付时间写 now；命中时写一条操作者为 actor 的事件，
    核销用券记录、按订单快照发放获得积分（_settle_paid）。锁顺序为会员 → 订单 → 用券 → 积分。
    未命中返回假，此时本函数没有写任何东西，由调用方回滚。不写支付尝试。

    模拟支付以 unexpired_only=True 调用（submit_payment 另在 UPDATE 命中与写事件之间重查
    Stripe 记录，所以直接用下面两个步骤）；Stripe 确认以 actor 为 system、unexpired_only=False
    调用（DESIGN 1.12 第 4 条：确认不看 15 分钟支付到期时间，只看订单是否仍待支付）。
    """
    if not _update_paid(db, order_id, actor, now, unexpired_only=unexpired_only):
        return False
    _finish_paid(db, order_id, actor, now)
    return True


def _update_paid(
    db: Session, order_id: int, actor: str, now: datetime, *, unexpired_only: bool
) -> bool:
    """mark_paid 的前一半：迁移检查、锁会员行与带条件的 UPDATE；是否命中。"""
    _require_transition(STATUS_PAID, actor)
    _lock_order_member(db, order_id)
    due = Order.payment_expires_at > now if unexpired_only else true()
    return _update_awaiting(db, order_id, due, status=STATUS_PAID, paid_at=now)


def _finish_paid(db: Session, order_id: int, actor: str, now: datetime) -> None:
    """mark_paid 的后一半：写事件，核销用券记录、按订单快照发放获得积分。"""
    db.add(_event(order_id, STATUS_PAID, actor, now))
    _settle_paid(db, order_id, now)


def cancel_order(
    db: Session,
    order_id: int,
    now: datetime,
    *,
    actor: PayActor = "guest",
    gateway: StripeGateway | None = None,
) -> CancelOutcome:
    """下单者在支付页取消待支付订单。

    actor 为操作者（guest 或 member，其他值抛 ValueError，先于访问数据库）；gateway 为 Stripe
    适配器，不给时等同 Stripe 不可用。
    订单已不是待支付：已取消即返回当前状态（重复取消不再写事件、加回库存或释放），已模拟支付
    及之后的状态抛 OrderNotCancellable；不处理会话。
    仍待支付而没有不是 expired 的 Stripe 记录（现有流程）：已过支付到期时间的先按超时取消处理。
    仍待支付时先锁订单的会员行（会员 ID 非空时），再在同一事务里用带条件的 UPDATE（状态仍为
    awaiting_demo_payment 且未到期）改为 demo_cancelled，命中后以加锁读重查一次不是 expired
    的 Stripe 记录，再加回库存、释放券与积分预占、写一条操作者为 actor 的事件；未命中（并发的
    支付、取消或超时取消先提交）时回滚。重查见到记录（与发起会话并发）时回滚，按下面有记录的
    路径重新处理一次。
    仍待支付且有不是 expired 的 Stripe 记录：按 _end_checkout 先处理会话，会话确认结束且未付款
    才经 cancel_after_stripe_end 取消（不看本地支付到期时间）。它返回 changed 时重新处理一次，
    仍为 changed 抛 StripeUnavailable；之后订单已取消返回当前状态，已模拟支付等抛
    OrderNotCancellable，仍待支付（另有活动记录）抛 StripeUnavailable。会话付款未定、结果不明、
    交人工核对或 Stripe 不可用时抛 StripeUnavailable，订单保持待支付、库存继续预留。
    """
    _check_actor(actor)
    order = _load_order(db, order_id)
    if order.status == STATUS_AWAITING_PAYMENT and _active_checkout_id(db, order_id) is None:
        if now >= order.payment_expires_at:
            expire_overdue_orders(db, now, order_ids=[order_id])
            order = _load_order(db, order_id)
        if order.status == STATUS_AWAITING_PAYMENT:
            if _cancel(db, order_id, actor, now, Order.payment_expires_at > now):
                db.commit()
            else:
                db.rollback()
            order = _load_order(db, order_id)
    if order.status == STATUS_AWAITING_PAYMENT and _active_checkout_id(db, order_id) is not None:
        db.rollback()
        return _cancel_with_checkout(db, gateway, order_id, actor, now)
    return _cancel_outcome(db, order)


def _cancel_outcome(db: Session, order: Order) -> CancelOutcome:
    if order.status != STATUS_CANCELLED:
        raise OrderNotCancellable(order.status)
    cancelled_by = _cancelled_by(db, [order.id])[order.id]
    return CancelOutcome(status=order.status, cancelled_by=cancelled_by)


def _cancel_with_checkout(
    db: Session,
    gateway: StripeGateway | None,
    order_id: int,
    actor: str,
    now: datetime,
) -> CancelOutcome:
    """本人取消有 Stripe 记录的订单（第 5 条）；调用方已结束事务。"""
    if gateway is None:
        raise StripeUnavailable
    for _ in range(2):
        if _end_checkout(db, gateway, order_id, actor, now) is not StripeEndResult.CHANGED:
            break
    else:
        raise StripeUnavailable
    order = _load_order(db, order_id)
    if order.status == STATUS_AWAITING_PAYMENT:
        # 另有活动记录（如并发的另一次发起）：留待稍后再试。
        raise StripeUnavailable
    return _cancel_outcome(db, order)


def _end_checkout(
    db: Session, gateway: StripeGateway, order_id: int, actor: str, now: datetime
) -> StripeEndResult:
    """处理订单当前不是 expired 的那条记录，会话确认结束且未付款时取消订单。

    以普通读取得记录后结束读事务，调用 Stripe 时不持有任何数据库锁：
    - 已没有这类记录（并发的处理已把它记为 expired）：不给记录 ID 调用 cancel_after_stripe_end。
    - creating：按 SHOP-TASK-112 的 reconcile_creating_checkout 取得会话或对账。记为 expired 时
      不给记录 ID 调用 cancel_after_stripe_end；取得 open 会话时按 open 处理；complete 按
      complete 处理；结果不明或转人工抛 StripeUnavailable。
    - open：见 _end_open。
    - complete：见 _end_complete。
    订单已由 Stripe 付款时返回 kept（由调用方按订单当前状态处理）。
    """
    from app.services.stripe_checkout import ReconcileOutcome, reconcile_creating_checkout

    record = _load_active_checkout(db, order_id)
    if record is None:
        db.rollback()
        return cancel_after_stripe_end(db, order_id, actor, now)
    checkout_id = record.id
    status = record.status
    db.rollback()

    if status == CHECKOUT_CREATING:
        outcome = reconcile_creating_checkout(db, gateway, checkout_id, now)
        if outcome is ReconcileOutcome.EXPIRED:
            return cancel_after_stripe_end(db, order_id, actor, now)
        if outcome is ReconcileOutcome.OPEN:
            return _end_open(db, gateway, order_id, checkout_id, actor, now)
        if outcome is ReconcileOutcome.COMPLETE:
            return _end_complete(db, gateway, order_id, checkout_id, None, actor, now)
        raise StripeUnavailable
    if status == CHECKOUT_OPEN:
        return _end_open(db, gateway, order_id, checkout_id, actor, now)
    return _end_complete(db, gateway, order_id, checkout_id, None, actor, now)


def _end_open(
    db: Session,
    gateway: StripeGateway,
    order_id: int,
    checkout_id: int,
    actor: str,
    now: datetime,
) -> StripeEndResult:
    """open 记录：请求 Stripe 作废会话。

    作废成功（返回的会话为 expired）时给记录 ID 调用 cancel_after_stripe_end。作废被拒（核验
    结论：再作废已过期或已完成的会话都 400）或返回的会话不是 expired 时查询会话：expired 同上；
    complete 按 _end_complete 处理（用这次查询到的对象）；其余（仍 open 等不能确定）与 Stripe
    不可用都抛 StripeUnavailable。记录在读取之后已不是 open 时返回 changed。
    """
    record = _load_checkout(db, checkout_id)
    seen_status = record.status
    session_id = record.checkout_session_id
    db.rollback()
    if seen_status != CHECKOUT_OPEN or session_id is None:
        return StripeEndResult.CHANGED

    result = gateway.expire_checkout_session(session_id)
    if _session_status(result.outcome, result.value) == CHECKOUT_EXPIRED:
        return cancel_after_stripe_end(
            db, order_id, actor, now, checkout_id=checkout_id, seen_status=seen_status
        )
    if result.outcome not in (StripeOutcome.OK, StripeOutcome.REJECTED):
        raise StripeUnavailable

    # 核验结论：作废被 400 拒绝时不解析消息，查询会话决定。
    current = gateway.retrieve_checkout_session(session_id)
    status = _session_status(current.outcome, current.value)
    if status == CHECKOUT_EXPIRED:
        return cancel_after_stripe_end(
            db, order_id, actor, now, checkout_id=checkout_id, seen_status=seen_status
        )
    if status == CHECKOUT_COMPLETE:
        return _end_complete(db, gateway, order_id, checkout_id, current.value, actor, now)
    raise StripeUnavailable


def _end_complete(
    db: Session,
    gateway: StripeGateway,
    order_id: int,
    checkout_id: int,
    stripe_session: CheckoutSession | None,
    actor: str,
    now: datetime,
) -> StripeEndResult:
    """complete 的会话：按 SHOP-TASK-113 的 confirm_checkout 确认（第 5 条「会话 complete 且已
    付款，按第 4 条确认、不取消」）。

    paid：不取消，返回 kept（订单为 demo_paid，由调用方抛 OrderNotCancellable）。refunded（核对
    不符，确认已写成退款尝试并把记录标为 expired、订单仍待支付）：不给记录 ID 调用
    cancel_after_stripe_end。needs_review（没写成退款尝试、活动位保留，交人工核对）、pending
    （付款未定）与 unavailable：抛 StripeUnavailable，不取消。
    """
    from app.services.stripe_confirm import ConfirmResult, confirm_checkout

    result = confirm_checkout(db, gateway, checkout_id, stripe_session, now)
    if result is ConfirmResult.PAID:
        return StripeEndResult.KEPT
    if result is ConfirmResult.REFUNDED:
        return cancel_after_stripe_end(db, order_id, actor, now)
    raise StripeUnavailable


def _session_status(outcome: StripeOutcome, stripe_session: CheckoutSession | None) -> str | None:
    """Stripe 调用为 ok 时会话的状态，否则为空。"""
    if outcome is not StripeOutcome.OK or stripe_session is None:
        return None
    return stripe_session.status


def cancel_after_stripe_end(
    db: Session,
    order_id: int,
    actor: str,
    now: datetime,
    *,
    checkout_id: int | None = None,
    seen_status: str | None = None,
    record_event: Callable[[Session], None] | None = None,
) -> StripeEndResult:
    """Stripe 会话确认结束且未付款之后取消订单；本人取消（cancel_order）与 SHOP-TASK-116 的
    定时处理都经它取消。

    actor 为 guest、member 或 system（其他值抛 ValueError，先于访问数据库），迁移检查沿用
    app/services/order_rules.py。调用方不得持有数据库锁、不得有未提交的改动：本函数先结束当前
    事务，在自己的事务里按统一顺序「会员 → 订单 → Stripe 会话记录」加锁。
    - 给了记录 ID（只用于调用方已从 Stripe 确认会话结束且未付款的情形；seen_status 为调用方
      查询 Stripe 前读到的该记录状态，必须同时给出）：先重查该记录，仍属这张订单、状态仍等于
      seen_status、付款状态不是 paid 且没有确认时间，才把它记为 expired（active_order_id 置空）；
      否则（查询 Stripe 之后被 webhook 或另一次确认改动过）不改记录与订单、回滚并返回 changed。
    - 不给记录 ID：用于记录已是 expired 的情形（creating 经对账记为 expired，核对不符的已付款
      记录由 SHOP-TASK-117 写成退款尝试后记为 expired），只做下面订单的重查与取消。
    再重查订单仍为 awaiting_demo_payment 且已没有不是 expired 的记录，才用带条件的 UPDATE（不看
    本地支付到期时间：第 5 条，订单在会话结束之前不会被超时取消）改为 demo_cancelled，加回库存、
    释放券与积分预占、写一条操作者为 actor 的事件，提交并返回 cancelled；订单不满足时只提交记录
    的 expired、订单不动，返回 kept。
    给了 record_event（只供 webhook）时，这两处提交之前先 flush 本轮改动、再调用
    record_event(db)，事件记录随改动一并提交；它抛出的异常原样向上传出（不回滚，由调用方回滚）。
    """
    if actor not in _END_ACTORS:
        raise ValueError("actor must be guest, member or system")
    if checkout_id is not None and seen_status is None:
        raise ValueError("seen_status is required with checkout_id")
    _require_transition(STATUS_CANCELLED, actor)
    for _ in range(_MAX_ROUNDS):
        db.rollback()
        member_id = db.scalar(select(Order.member_id).where(Order.id == order_id))
        if member_id is not None:
            db.execute(select(Member.id).where(Member.id == member_id).with_for_update())
        order = _lock_order(db, order_id)
        if order.member_id != member_id:
            continue
        if checkout_id is not None:
            record = _lock_checkout(db, checkout_id)
            if (
                record.order_id != order_id
                or record.status != seen_status
                or record.payment_status == PAYMENT_PAID
                or record.confirmed_at is not None
            ):
                db.rollback()
                return StripeEndResult.CHANGED
            if record.status != CHECKOUT_EXPIRED:
                record.status = CHECKOUT_EXPIRED
                record.active_order_id = None
                db.flush()
        if (
            order.status != STATUS_AWAITING_PAYMENT
            or _active_checkout_id(db, order_id, lock=True) is not None
        ):
            _record_event(db, record_event)
            db.commit()
            return StripeEndResult.KEPT
        if not _cancel(db, order_id, actor, now, true()):
            # 已锁住订单行时不会出现；没有行锁（SQLite）时并发的处理先提交了，重新走一轮。
            continue
        _record_event(db, record_event)
        db.commit()
        return StripeEndResult.CANCELLED
    db.rollback()
    return StripeEndResult.CHANGED


def _record_event(db: Session, record_event: Callable[[Session], None] | None) -> None:
    """提交之前：给了 record_event 时先 flush 本轮改动，再调用它把事件记录放进同一事务。"""
    if record_event is None:
        return
    db.flush()
    record_event(db)


def expire_overdue_orders(
    db: Session,
    now: datetime,
    *,
    order_ids: Collection[int] | None = None,
    limit: int | None = None,
) -> int:
    """把超过支付到期时间仍未支付的订单取消并释放库存、券与积分，返回本次取消的张数。

    选出状态为 awaiting_demo_payment、支付到期时间不晚于 now 且没有不是 expired 的 Stripe
    记录的订单（order_ids 可限定范围，limit 可限定单次上限；按到期时间从早到晚；有记录的订单
    在应用 limit 之前就排除，不会每轮占满上限），逐张在各自的事务里先锁订单的会员行
    （会员 ID 非空时），再用带条件的 UPDATE（状态仍为 awaiting_demo_payment 且已到期才改）
    改为 demo_cancelled，命中后以加锁读重查一次不是 expired 的 Stripe 记录（选单之后才发起的
    会话：回滚并跳过这张），再按订单行加回当日可用库存（规格已删除的行跳过；同一规格先合并
    件数，按 SKU 规格 ID 从小到大）、释放券与积分预占、写一条操作者为 system 的事件，然后提交。
    UPDATE 未命中（并发的支付、取消或另一次超时取消先提交）时回滚并跳过这张，所以同一订单
    只生效一次，重复运行不重复加回库存或释放。逐张提交是模块说明里锁顺序的前提。

    有记录的订单不做任何改动，留给 SHOP-TASK-116 的定时处理（DESIGN 1.12 第 5 条「查看入口只对
    没有 Stripe 记录（或只有 expired 记录）的订单执行超时取消」）。可重复运行；查看与支付、取消
    接口、定时任务与每日库存重置都调用它。
    """
    if order_ids is not None and not order_ids:
        return 0
    active = select(StripeCheckout.id).where(StripeCheckout.active_order_id == Order.id)
    stmt = (
        select(Order.id)
        .where(
            Order.status == STATUS_AWAITING_PAYMENT,
            Order.payment_expires_at <= now,
            ~active.exists(),
        )
        .order_by(Order.payment_expires_at, Order.id)
    )
    if order_ids is not None:
        stmt = stmt.where(Order.id.in_(list(order_ids)))
    if limit is not None:
        stmt = stmt.limit(limit)
    overdue = list(db.scalars(stmt))

    cancelled = 0
    for order_id in overdue:
        if _cancel(db, order_id, ACTOR_SYSTEM, now, Order.payment_expires_at <= now):
            db.commit()
            cancelled += 1
        else:
            db.rollback()
    return cancelled


def pay_orders(
    db: Session,
    order_ids: list[int],
    lang: Language,
    now: datetime,
) -> list[PayOrder]:
    """按 order_ids 的顺序返回这些订单的支付页视图；只读。订单行按行序。

    Stripe 字段见模块说明：会话记录与退款尝试按订单批量读取；支付途径只对待支付订单按
    SHOP-TASK-112 的 payment_route 判定。不加锁、不调用 Stripe。
    """
    from app.services.stripe_checkout import PaymentRoute, payment_route

    if not order_ids:
        return []
    order_stmt = select(Order).where(Order.id.in_(order_ids))
    order_stmt = order_stmt.execution_options(populate_existing=True)
    orders = {order.id: order for order in db.scalars(order_stmt)}

    lines: dict[int, list[PayLine]] = {order_id: [] for order_id in order_ids}
    item_stmt = select(OrderItem).where(OrderItem.order_id.in_(order_ids))
    items = db.scalars(item_stmt.order_by(OrderItem.order_id, OrderItem.line_index))
    for item in items:
        line = PayLine(
            name=getattr(item, f"product_name_{lang}"),
            variant_label=getattr(item, f"variant_label_{lang}"),
            quantity=item.quantity,
            unit_price_sen=item.unit_price_sen,
            line_subtotal_sen=item.line_subtotal_sen,
        )
        lines[item.order_id].append(line)

    recipient_stmt = select(OrderRecipient).where(OrderRecipient.order_id.in_(order_ids))
    recipients = {row.order_id: row for row in db.scalars(recipient_stmt)}

    attempt_stmt = select(PaymentAttempt).where(PaymentAttempt.order_id.in_(order_ids))
    attempts = db.scalars(attempt_stmt.order_by(PaymentAttempt.created_at, PaymentAttempt.id))
    # 按时间从早到晚，后面的覆盖前面的，留下的是每张订单最近一次。
    last_payments = {attempt.order_id: attempt for attempt in attempts}

    cancelled_by = _cancelled_by(db, order_ids)
    stripe_states = _stripe_states(db, order_ids)
    stripe_refunded = _stripe_refunded(db, order_ids)
    min_amount_sen = get_settings().stripe_min_amount_sen
    views = []
    for order_id in order_ids:
        order = orders.get(order_id)
        if order is None:
            continue
        recipient = recipients.get(order_id)
        attempt = last_payments.get(order_id)
        route: PayRoute | None = None
        if order.status == STATUS_AWAITING_PAYMENT:
            stripe = payment_route(db, order_id, min_amount_sen) is PaymentRoute.STRIPE
            route = "stripe" if stripe else "simulated"
        view = PayOrder(
            order_number=order.order_number,
            status=order.status,
            created_at=_utc(order.created_at),
            payment_expires_at=_utc(order.payment_expires_at),
            server_time=_utc(now),
            lines=lines[order_id],
            subtotal_sen=order.subtotal_sen,
            coupon_discount_sen=order.coupon_discount_sen,
            points_redeemed=order.points_redeemed,
            shipping_fee_sen=order.shipping_fee_sen,
            total_sen=order.total_sen,
            points_earned=order.points_earned,
            recipient=None if recipient is None else _recipient(recipient),
            last_payment=None if attempt is None else _last_payment(attempt),
            cancelled_by=cancelled_by[order_id],
            payment_route=route,
            stripe_state=stripe_states.get(order_id),
            stripe_refunded=order_id in stripe_refunded,
        )
        views.append(view)
    return views


def _stripe_states(db: Session, order_ids: list[int]) -> dict[int, StripeState]:
    """各订单不是 expired 的 Stripe 记录（至多一条）的状态；creating 且已写告警时间（24 小时仍
    无法确定、转人工核对）为 under_review。没有这样的记录的订单不在结果里。"""
    columns = (StripeCheckout.active_order_id, StripeCheckout.status, StripeCheckout.alerted_at)
    stmt = select(*columns).where(StripeCheckout.active_order_id.in_(order_ids))
    found: dict[int, StripeState] = {}
    for order_id, status, alerted_at in db.execute(stmt).tuples():
        if order_id is None or status not in _STRIPE_STATES:
            continue
        if status == CHECKOUT_CREATING and alerted_at is not None:
            found[order_id] = "under_review"
        else:
            found[order_id] = _STRIPE_STATES[status]
    return found


def _stripe_refunded(db: Session, order_ids: list[int]) -> set[int]:
    """任一会话记录有来源为 checkout 的退款尝试（不论尝试状态）的订单。"""
    stmt = (
        select(StripeCheckout.order_id)
        .join(StripeRefund, StripeRefund.checkout_id == StripeCheckout.id)
        .where(
            StripeCheckout.order_id.in_(order_ids),
            StripeRefund.source_type == SOURCE_CHECKOUT,
        )
        .distinct()
    )
    return set(db.scalars(stmt))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _load_order(db: Session, order_id: int) -> Order:
    # populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    return db.scalars(stmt).one()


def _lock_order(db: Session, order_id: int) -> Order:
    # 只锁订单行；populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _active_checkout_id(db: Session, order_id: int, *, lock: bool = False) -> int | None:
    """订单不是 expired 的 Stripe 记录（至多一条，唯一约束保证）的 ID；没有为空。

    lock 为真时以加锁读查看（调用方已持有订单行锁，锁顺序「订单 → Stripe 会话记录」）。
    """
    stmt = select(StripeCheckout.id).where(StripeCheckout.active_order_id == order_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def _load_active_checkout(db: Session, order_id: int) -> StripeCheckout | None:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.active_order_id == order_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one_or_none()


def _load_checkout(db: Session, checkout_id: int) -> StripeCheckout:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _lock_checkout(db: Session, checkout_id: int) -> StripeCheckout:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.id == checkout_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _stripe_route(db: Session, order_id: int) -> bool:
    """按 SHOP-TASK-112 的途径判定，订单当前是否走 Stripe；最低收款额取 get_settings()。"""
    from app.services.stripe_checkout import PaymentRoute, payment_route

    min_amount_sen = get_settings().stripe_min_amount_sen
    return payment_route(db, order_id, min_amount_sen) is PaymentRoute.STRIPE


def _attempt_by_key(db: Session, idempotency_key: str) -> PaymentAttempt | None:
    stmt = select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
    return db.scalars(stmt).one_or_none()


def _replay_or_not_payable(
    db: Session, order_id: int, idempotency_key: str, fingerprint: str
) -> PaymentOutcome:
    """回滚之后：同键的并发请求已提交则按它重放，否则抛 OrderNotPayable（订单当前状态）。"""
    db.rollback()
    existing = _attempt_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint)
    raise OrderNotPayable(_load_order(db, order_id).status)


def _replay(db: Session, attempt: PaymentAttempt, fingerprint: str) -> PaymentOutcome:
    """同键同请求返回原结果与订单当前状态；同键不同请求报冲突。都不改订单。"""
    if not hmac.compare_digest(attempt.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    order = _load_order(db, attempt.order_id)
    return PaymentOutcome(
        method=_METHOD_NAMES[attempt.method],
        result=_RESULT_NAMES[attempt.result],
        status=order.status,
        paid_at=None if order.paid_at is None else _utc(order.paid_at),
    )


def _check_actor(actor: object) -> None:
    if actor not in _PAY_ACTORS:
        raise ValueError("actor must be guest or member")


def _require_transition(target: str, actor: str) -> None:
    # 经 SHOP-TASK-010 的迁移判定函数检查；这里用到的迁移都在表里，不通过即编程错误。
    if not is_transition_allowed(STATUS_AWAITING_PAYMENT, target, actor):
        raise RuntimeError("order status transition is not allowed")


def _lock_order_member(db: Session, order_id: int) -> None:
    """带条件的 UPDATE 之前：以普通读取得订单的会员 ID，非空时锁住该会员的行。

    会员已注销也锁；会员 ID 为空（游客订单或注销后已清空）时不预先锁。
    """
    member_id = db.scalar(select(Order.member_id).where(Order.id == order_id))
    if member_id is None:
        return
    db.execute(select(Member.id).where(Member.id == member_id).with_for_update())


def _settle_paid(db: Session, order_id: int, now: datetime) -> None:
    """支付成功的 UPDATE 命中之后：核销用券记录，按订单快照发放获得积分；只 flush、不提交。

    没有用券记录时 use_coupon 不写。订单的会员 ID 与会员状态以加锁读重新读取：会员 ID 为空
    （游客订单或会员已注销）或会员不是 active 时不发放；快照 points_earned 为 0 时不写记录，
    也不再去锁会员行（会员 ID 非空的会员订单已在 UPDATE 之前锁过）。
    """
    use_coupon(db, order_id, now)
    order = db.scalars(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if order.member_id is None or order.points_earned == 0:
        return
    member = db.scalars(
        select(Member)
        .where(Member.id == order.member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if member is None or member.status != MEMBER_ACTIVE:
        return
    earn_points(db, member.id, order_id, order.points_earned, now)


def _update_awaiting(
    db: Session,
    order_id: int,
    due: ColumnElement[bool],
    **values: object,
) -> bool:
    """带条件的 UPDATE：订单状态仍为 awaiting_demo_payment 且满足 due 才改；是否命中。"""
    stmt = (
        update(Order)
        .where(Order.id == order_id, Order.status == STATUS_AWAITING_PAYMENT, due)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return db.execute(stmt).rowcount == 1


def _cancel(
    db: Session, order_id: int, actor: str, now: datetime, due: ColumnElement[bool]
) -> bool:
    """先锁会员行，再带条件地把待支付订单改为已取消、加回库存、释放券与积分预占、写事件；
    只 flush、不提交。

    due 为附加条件：超时取消只改已到期的订单，本人取消（没有 Stripe 记录时）只改未到期的订单，
    会话结束后的取消不看到期时间。UPDATE 命中后以加锁读重查一次不是 expired 的 Stripe 记录
    （DESIGN 1.12 第 2 条：有记录的订单不经这里取消；堵住与发起会话并发的情形）。UPDATE 未命中
    或重查见到记录时返回假，由调用方回滚（后者已改了订单状态，必须回滚）。
    """
    _require_transition(STATUS_CANCELLED, actor)
    _lock_order_member(db, order_id)
    if not _update_awaiting(db, order_id, due, status=STATUS_CANCELLED):
        return False
    if _active_checkout_id(db, order_id, lock=True) is not None:
        return False
    _release_stock(db, order_id)
    # 游客订单没有用券记录与 redeem 流水，两者都不写。
    release_coupon(db, order_id, now)
    release_points(db, order_id, now)
    db.add(_event(order_id, STATUS_CANCELLED, actor, now))
    db.flush()
    return True


def _release_stock(db: Session, order_id: int) -> None:
    """按订单行把件数加回当日可用库存。

    规格已删除（规格 ID 为空）的行跳过；同一规格的多行先合并件数，再按 SKU 规格 ID
    从小到大逐个更新，与下单扣库存的顺序一致，避免与下单、库存重置互相死锁。
    """
    stmt = (
        select(OrderItem.variant_id, func.sum(OrderItem.quantity))
        .where(OrderItem.order_id == order_id, OrderItem.variant_id.is_not(None))
        .group_by(OrderItem.variant_id)
        .order_by(OrderItem.variant_id)
    )
    quantities = db.execute(stmt).tuples().all()
    for variant_id, quantity in quantities:
        increase = (
            update(ProductVariant)
            .where(ProductVariant.id == variant_id)
            .values(available_stock=ProductVariant.available_stock + quantity)
            .execution_options(synchronize_session=False)
        )
        db.execute(increase)


def _event(order_id: int, to_status: str, actor: str, now: datetime) -> OrderEvent:
    return OrderEvent(
        order_id=order_id,
        from_status=STATUS_AWAITING_PAYMENT,
        to_status=to_status,
        actor_type=actor,
        created_at=now,
    )


def _cancelled_by(db: Session, order_ids: list[int]) -> dict[int, CancelledBy | None]:
    """按取消事件的操作者：system 为超时，其余（guest、member）为本人。"""
    found: dict[int, CancelledBy | None] = dict.fromkeys(order_ids)
    stmt = (
        select(OrderEvent.order_id, OrderEvent.actor_type)
        .where(OrderEvent.order_id.in_(order_ids))
        .where(OrderEvent.to_status == STATUS_CANCELLED)
    )
    for order_id, actor in db.execute(stmt).tuples():
        found[order_id] = "timeout" if actor == ACTOR_SYSTEM else "self"
    return found


def _recipient(recipient: OrderRecipient) -> PayRecipient:
    return PayRecipient(
        name=recipient.name,
        phone=recipient.phone,
        country_code=recipient.country_code,
        region=recipient.region,
        address=recipient.address,
        postal_code=recipient.postal_code,
    )


def _last_payment(attempt: PaymentAttempt) -> LastPayment:
    return LastPayment(
        method=_METHOD_NAMES[attempt.method],
        result=_RESULT_NAMES[attempt.result],
        created_at=_utc(attempt.created_at),
    )

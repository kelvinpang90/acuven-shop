"""支付途径判定、发起 Stripe 测试 Checkout 会话，与创建结果未定时的重试和对账。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
- 第 2 条：「同一订单的两条支付途径互斥：订单只要有一条不是 expired 的
  Stripe 记录（creating、open 或 complete），不论开关当前取值，都不接受
  模拟支付、不另建会话」；「为『模拟』且没有 Stripe 记录（或只有 expired
  记录）时，现有模拟支付流程不变」；「订单应付低于 Stripe 对 MYR 的最低
  收款额（配置项，默认 200 仙……）时，即使为『Stripe 测试』也走模拟支付」。
- 第 3 条：「先在加锁读订单的同一事务里写一条 `creating` 状态的
  `StripeCheckout`：生成本站的创建幂等键，并把这次创建的全部参数……存为
  快照，提交；再以该键与快照调用 Stripe 创建 Checkout 会话」；「成功后锁住
  该记录写回会话 ID 与付款页地址：只在记录仍为 creating 时改为 `open`；若
  webhook 已先把它记为 complete（或对账已记为 expired），只补齐缺失的会话
  ID 与地址，不改状态与付款结果」；「调用结果未知（超时、断线）时，记录
  保持 `creating`，之后的发起或定时任务以同一幂等键与同一快照重试」；「快照
  里的 Stripe 到期时间过后，以同一幂等键再请求一次来对账」；「Stripe 以参数
  不合格（到期时间已过）拒绝……记为 expired」；「24 小时内仍无法确定就不再
  自动处理、告警由人工核对，订单在此期间不取消、库存继续预留」；会话参数
  （payment 模式、MYR、一个行项目「演示订单」、client_reference_id 与
  元数据、预填演示邮箱、语言、返回地址、到期时间）。
- 第 9 条：「创建会话或查询时 Stripe 不可用，支付页提示稍后再试……不自动
  改用模拟支付」。
- 第 10 条：「本站发给 Stripe 的只有金额、币种、行项目名称、内部订单 ID、
  语言、返回地址与预填的固定演示邮箱，不发送姓名、电话、地址或订单号」。
以及 docs/HANDOFF.md 0.53 记录的核验结论：创建会话已不接受
payment_method_types；「过期后以同一幂等键重放返回原会话……但内容是当初
保存的响应（status 仍为 open），须再查询会话才得实际状态」；「换新键带过去
的 expires_at 被 400 拒绝（param 为 expires_at），即原请求没有建成」；与
Kelvin 2026-10-15 确认的行项目三语名称与「订单支付到期时间与当前时间加
31 分钟中较晚者」。

锁顺序与 SHOP-TASK-113、114 统一为「会员 → 订单 → Stripe 会话记录 → 用券
→ 积分 → 退款尝试」。发起只锁订单行（不锁会员行），再锁该单的 Stripe 记录；
调用 Stripe 之前提交或回滚、不持有任何锁，调用之后只锁那条 Stripe 记录写回
结果。没有记录而订单已过本地支付到期时间时，先回滚释放订单锁，再调用
app/services/payment.py 的 expire_overdue_orders（它按「会员 → 订单」
加锁）。SQLite 不支持行锁，SQLAlchemy 在 SQLite 上不生成 FOR UPDATE。

运营告警邮件尚未接入：设计写「告警」之处先记一条 error 级日志，只含类别与
本站记录 ID。记录与订单都不写日志以外的个人资料；快照里的 customer_email
是站点配置的固定演示邮箱。本模块只做业务规则：不加接口、不接定时任务；
确认付款由 SHOP-TASK-113、取消与超时由 SHOP-TASK-114 实现。库里的时间一律
是不带时区的 UTC。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Order, StripeCheckout
from app.models.order import STATUS_AWAITING_PAYMENT
from app.models.site import PAYMENT_METHOD_STRIPE_TEST
from app.models.stripe import (
    CHECKOUT_COMPLETE,
    CHECKOUT_CREATING,
    CHECKOUT_EXPIRED,
    CHECKOUT_OPEN,
    CURRENCY_MYR,
    PAYMENT_STATUSES,
)
from app.services.catalog import Language
from app.services.payment import expire_overdue_orders
from app.services.site_settings import get_payment_method
from app.services.stripe_gateway import (
    CheckoutSession,
    Form,
    StripeGateway,
    StripeOutcome,
    StripeResult,
    stripe_configured,
)

logger = logging.getLogger(__name__)

# 第 3 条的三语「演示订单」（UX-COPY 未收，Kelvin 2026-10-15 确认的取值）。
LINE_ITEM_NAMES: dict[str, str] = {"en": "Demo order", "zh": "演示订单", "ms": "Pesanan demo"}

# Stripe 要求会话至少 30 分钟后才到期；多 1 分钟抵消传输与时钟误差。
MIN_SESSION_LIFETIME = timedelta(minutes=31)
# 第 3 条「24 小时内仍无法确定就不再自动处理、告警由人工核对」。
REVIEW_AFTER = timedelta(hours=24)

SUCCESS_PATH = "/pay/result"
CANCEL_PATH = "/pay"

# 告警邮件接入前的 error 级日志类别（只含类别与记录 ID）。
LOG_CREATE_REJECTED = "stripe_create_rejected"
LOG_NEEDS_REVIEW = "stripe_checkout_needs_review"

# 发起的轮数上限：对账记为 expired 后再走一轮建新记录；并发写入撞上唯一约束时再走一轮。
_MAX_ROUNDS = 3

_SESSION_STATUSES = (CHECKOUT_OPEN, CHECKOUT_COMPLETE, CHECKOUT_EXPIRED)


class PaymentRoute(StrEnum):
    STRIPE = "stripe"
    SIMULATED = "simulated"


class StartResult(StrEnum):
    # 有 open 会话：跳转到它的付款页地址。
    REDIRECT = "redirect"
    # 会话已完成：由调用方按 SHOP-TASK-113 确认。
    COMPLETE = "complete"
    # 订单不是待支付，或没有会话而已过本地支付到期时间（已按超时取消处理）。
    NOT_PAYABLE = "not_payable"
    # 途径为模拟支付。
    METHOD_CHANGED = "method_changed"
    # Stripe 未配置、不可用或创建结果未定（记录保持 creating），或被拒绝没有建成。
    UNAVAILABLE = "unavailable"
    # 创建结果 24 小时仍无法确定，已告警由人工核对。
    UNDER_REVIEW = "under_review"


class ReconcileOutcome(StrEnum):
    """一条 creating 记录处理之后的状态。"""

    OPEN = "open"
    COMPLETE = "complete"
    # 确认没有建成会话，或会话已过期：订单的活动会话位已释放。
    EXPIRED = "expired"
    # 仍无法确定，记录保持 creating，留待下次。
    PENDING = "pending"
    # 创建满 24 小时仍无法确定，已写告警时间，不再调用 Stripe。
    UNDER_REVIEW = "under_review"


@dataclass(frozen=True)
class CheckoutStart:
    result: StartResult
    # 只在 redirect 时有：Stripe 返回的付款页地址。
    checkout_url: str | None = None


_OUTCOME_BY_STATUS = {
    CHECKOUT_OPEN: ReconcileOutcome.OPEN,
    CHECKOUT_COMPLETE: ReconcileOutcome.COMPLETE,
    CHECKOUT_EXPIRED: ReconcileOutcome.EXPIRED,
    CHECKOUT_CREATING: ReconcileOutcome.PENDING,
}


# ---------------------------------------------------------------- 支付途径


def payment_route(db: Session, order_id: int, stripe_min_amount_sen: int) -> PaymentRoute:
    """订单当前的支付途径；只读。

    该订单有不是 expired 的 Stripe 记录（creating、open 或 complete）时
    一律为 stripe，不论开关；否则开关为 stripe_test 且订单应付 total_sen
    不低于 stripe_min_amount_sen 时为 stripe；其余为 simulated。不看 Stripe
    配置是否齐全：未配置时由发起返回 unavailable，不自动改用模拟支付。
    """
    active = db.scalar(select(StripeCheckout.id).where(StripeCheckout.active_order_id == order_id))
    if active is not None:
        return PaymentRoute.STRIPE
    total_sen = db.scalars(select(Order.total_sen).where(Order.id == order_id)).one()
    return _route_without_checkout(db, total_sen, stripe_min_amount_sen)


def _route_without_checkout(db: Session, total_sen: int, min_amount_sen: int) -> PaymentRoute:
    if get_payment_method(db) != PAYMENT_METHOD_STRIPE_TEST:
        return PaymentRoute.SIMULATED
    if total_sen < min_amount_sen:
        return PaymentRoute.SIMULATED
    return PaymentRoute.STRIPE


# ---------------------------------------------------------------- 创建参数


def stripe_expires_at(payment_expires_at: datetime, now: datetime) -> datetime:
    """会话的 Stripe 到期时间：订单支付到期时间与 now 加 31 分钟中较晚者。

    不足一秒的部分进到下一秒（Stripe 只收整秒，向上取整不会让会话短于 31 分钟）。
    """
    value = max(payment_expires_at, now + MIN_SESSION_LIFETIME)
    if value.microsecond:
        value = value.replace(microsecond=0) + timedelta(seconds=1)
    return value


def checkout_session_form(
    *,
    order_id: int,
    total_sen: int,
    create_key: str,
    lang: Language,
    demo_email: str,
    public_site_url: str,
    expires_at: datetime,
) -> tuple[tuple[str, str], ...]:
    """创建 Checkout 会话的全部表单参数，按发送顺序；纯函数。

    payment 模式；一个行项目（币种 myr、金额为订单应付、数量 1、名称按语言
    取「演示订单」）；client_reference_id 与 metadata[order_id] 为订单内部
    ID，metadata[create_key] 为创建幂等键；预填固定演示邮箱；locale 为界面
    语言；返回地址由公开站点地址拼出；expires_at 为 Unix 秒。不传
    payment_method_types（核验：已不接受）与其他参数，不含姓名、电话、地址
    或订单号。
    """
    site = public_site_url.rstrip("/")
    return (
        ("mode", "payment"),
        ("line_items[0][price_data][currency]", CURRENCY_MYR),
        ("line_items[0][price_data][unit_amount]", str(total_sen)),
        ("line_items[0][price_data][product_data][name]", LINE_ITEM_NAMES[lang]),
        ("line_items[0][quantity]", "1"),
        ("client_reference_id", str(order_id)),
        ("metadata[order_id]", str(order_id)),
        ("metadata[create_key]", create_key),
        ("customer_email", demo_email),
        ("locale", lang),
        ("success_url", site + SUCCESS_PATH),
        ("cancel_url", site + CANCEL_PATH),
        ("expires_at", str(_unix(expires_at))),
    )


def serialize_form(form: Form) -> str:
    """按顺序把表单参数序列化为 JSON（[[名, 值], …]），存为创建参数快照。"""
    return json.dumps([[name, value] for name, value in form], separators=(",", ":"))


def deserialize_form(snapshot: str) -> tuple[tuple[str, str], ...]:
    """从快照原样取出表单参数，顺序不变。"""
    return tuple((str(name), str(value)) for name, value in json.loads(snapshot))


def _unix(value: datetime) -> int:
    return int(value.replace(tzinfo=UTC).timestamp())


# ---------------------------------------------------------------- 发起


def start_checkout(
    db: Session,
    gateway: StripeGateway,
    settings: Settings,
    order_id: int,
    lang: Language,
    now: datetime,
) -> CheckoutStart:
    """访客发起 Stripe 支付。授权与 CSRF 由调用方先行校验；调用方此前只有读取。

    lang 为 en、zh 或 ms，now 为不带时区的 UTC（不合格抛 ValueError，先于
    访问数据库）。先回滚此前的事务，在以 SELECT … FOR UPDATE 加锁读订单的
    事务里依次判定：
    1. 订单不是 awaiting_demo_payment：not_payable。
    2. 没有不是 expired 的记录而途径为 simulated：method_changed。
    3. 已有 open 记录：redirect 与它的付款页地址，不调用 Stripe。
    4. 已有 complete 记录：complete（由调用方按 SHOP-TASK-113 确认）。
    5. 已有 creating 记录：回滚后按 reconcile_creating_checkout 重试或对账；
       记为 expired 时在本次调用里按「没有记录」重新走一轮（重新加锁读订单
       并重查，含本地到期）。
    6. 没有记录而订单已过本地支付到期时间：回滚释放订单锁，再执行
       expire_overdue_orders，返回 not_payable，不写记录。
    7. Stripe 未配置：不写记录，unavailable。
    8. 写一条 creating 记录（新生成的 UUID 幂等键与创建参数快照）并提交，
       再调用 Stripe 创建会话，结果按 _apply_create_result 写回。
    """
    _check_lang(lang)
    _check_now(now)
    for _ in range(_MAX_ROUNDS):
        db.rollback()
        order = _lock_order(db, order_id)
        if order.status != STATUS_AWAITING_PAYMENT:
            db.rollback()
            return CheckoutStart(StartResult.NOT_PAYABLE)
        checkout = _lock_active_checkout(db, order_id)
        if checkout is None:
            route = _route_without_checkout(db, order.total_sen, settings.stripe_min_amount_sen)
            if route is PaymentRoute.SIMULATED:
                db.rollback()
                return CheckoutStart(StartResult.METHOD_CHANGED)
        else:
            status = checkout.status
            checkout_id = checkout.id
            checkout_url = checkout.checkout_url
            db.rollback()
            if status == CHECKOUT_OPEN:
                return CheckoutStart(StartResult.REDIRECT, checkout_url)
            if status == CHECKOUT_COMPLETE:
                return CheckoutStart(StartResult.COMPLETE)
            outcome = reconcile_creating_checkout(db, gateway, checkout_id, now)
            if outcome is ReconcileOutcome.EXPIRED:
                continue
            return _start_result(db, checkout_id, outcome)

        if now >= order.payment_expires_at:
            # 15 分钟期限只让已有会话继续付款，不允许过期订单新建会话。
            db.rollback()
            expire_overdue_orders(db, now, order_ids=[order_id])
            return CheckoutStart(StartResult.NOT_PAYABLE)
        if not stripe_configured(settings):
            db.rollback()
            return CheckoutStart(StartResult.UNAVAILABLE)

        create_key = str(uuid.uuid4())
        expires_at = stripe_expires_at(order.payment_expires_at, now)
        form = checkout_session_form(
            order_id=order_id,
            total_sen=order.total_sen,
            create_key=create_key,
            lang=lang,
            demo_email=settings.stripe_demo_email,
            public_site_url=settings.public_site_url,
            expires_at=expires_at,
        )
        record = StripeCheckout(
            order_id=order_id,
            idempotency_key=create_key,
            amount_sen=order.total_sen,
            currency=CURRENCY_MYR,
            status=CHECKOUT_CREATING,
            create_params=serialize_form(form),
            expires_at=expires_at,
            created_at=now,
            active_order_id=order_id,
        )
        db.add(record)
        try:
            db.flush()
        except IntegrityError:
            # 并发的发起先写了该单的记录（唯一约束）：回滚后重新走一轮，按它处理。
            db.rollback()
            continue
        checkout_id = record.id
        db.commit()

        result = gateway.create_checkout_session(form, create_key)
        outcome = _apply_create_result(db, checkout_id, result, now)
        return _start_result(db, checkout_id, outcome)
    return CheckoutStart(StartResult.UNAVAILABLE)


def _start_result(db: Session, checkout_id: int, outcome: ReconcileOutcome) -> CheckoutStart:
    if outcome is ReconcileOutcome.OPEN:
        stmt = select(StripeCheckout.checkout_url).where(StripeCheckout.id == checkout_id)
        return CheckoutStart(StartResult.REDIRECT, db.scalar(stmt))
    if outcome is ReconcileOutcome.COMPLETE:
        return CheckoutStart(StartResult.COMPLETE)
    if outcome is ReconcileOutcome.UNDER_REVIEW:
        return CheckoutStart(StartResult.UNDER_REVIEW)
    # pending：结果未定；expired（首次调用被拒或会话已过期）：没有可用的会话。
    return CheckoutStart(StartResult.UNAVAILABLE)


# ---------------------------------------------------------------- 重试与对账


def reconcile_creating_checkout(
    db: Session, gateway: StripeGateway, checkout_id: int, now: datetime
) -> ReconcileOutcome:
    """处理一条 creating 记录：重试、对账或转人工。供发起、SHOP-TASK-114 的本人取消与
    SHOP-TASK-116 的定时任务调用。

    调用方不得持有数据库锁、不得有未提交的改动：本函数调用 Stripe 之前结束
    当前事务，之后只锁这条记录写回结果并提交。now 为不带时区的 UTC。
    - 记录已不是 creating：不调用 Stripe，按当前状态返回（open、complete、
      expired）。
    - 已写告警时间，或创建满 24 小时：不调用 Stripe，返回 under_review；
      第一次时写告警时间并记一条 error 日志（stripe_checkout_needs_review，
      只含记录 ID）。订单不取消、库存继续预留。
    - Stripe 到期时间之前：以同一幂等键与同一快照重试，结果按
      _apply_create_result 处理。
    - 之后：以同一幂等键与同一快照再请求一次。ok（回放的是当初保存的响应）
      时按会话 ID 查询一次，按实际状态记下（open、complete；expired 才记
      expired），查询不成留待下次；400 且 param 为 expires_at 时记为 expired
      （原请求没有建成）；其他结果留待下次。
    """
    _check_now(now)
    record = _load_checkout(db, checkout_id)
    if record.status != CHECKOUT_CREATING:
        outcome = _OUTCOME_BY_STATUS[record.status]
        db.rollback()
        return outcome
    if record.alerted_at is not None:
        db.rollback()
        return ReconcileOutcome.UNDER_REVIEW
    if now >= record.created_at + REVIEW_AFTER:
        return _mark_needs_review(db, checkout_id, now)
    form = deserialize_form(record.create_params)
    create_key = record.idempotency_key
    expires_at = record.expires_at
    # 调用 Stripe 时不持有任何数据库锁，也不留着读事务。
    db.rollback()

    result = gateway.create_checkout_session(form, create_key)
    if now < expires_at:
        return _apply_create_result(db, checkout_id, result, now)

    if result.outcome is StripeOutcome.OK and result.value is not None:
        # 核验结论：回放的内容是当初保存的响应，须再查询会话取实际状态。
        current = gateway.retrieve_checkout_session(result.value.id)
        if current.outcome is StripeOutcome.OK and current.value is not None:
            return _apply_session(db, checkout_id, current.value, now)
        return ReconcileOutcome.PENDING
    if _rejected(result, param="expires_at"):
        # 核验结论：过去的到期时间被 400 拒绝（param 为 expires_at），即原请求没有建成。
        outcome, _ = _mark_expired(db, checkout_id)
        return outcome
    return ReconcileOutcome.PENDING


def _apply_create_result(
    db: Session, checkout_id: int, result: StripeResult[CheckoutSession], now: datetime
) -> ReconcileOutcome:
    """创建调用（首次或到期前的重试）的结果。

    ok：按会话写回（见 _apply_session）。400、invalid_request_error 且带
    param：按 Stripe 幂等规则参数校验失败不保存结果，而同一幂等键与同一快照
    的请求若原已建成会回放原响应，所以没有建成会话，记为 expired 并记一条
    error 日志（stripe_create_rejected，只含记录 ID）。其他 rejected（如
    401、402、403、404，不能证明原请求没建成）、live 与 unavailable：记录
    保持 creating。
    """
    if result.outcome is StripeOutcome.OK and result.value is not None:
        return _apply_session(db, checkout_id, result.value, now)
    if _rejected(result, error_type="invalid_request_error"):
        outcome, changed = _mark_expired(db, checkout_id)
        if changed:
            logger.error("%s checkout=%d", LOG_CREATE_REJECTED, checkout_id)
        return outcome
    return ReconcileOutcome.PENDING


def _rejected(
    result: StripeResult[CheckoutSession],
    *,
    error_type: str | None = None,
    param: str | None = None,
) -> bool:
    """rejected 且 HTTP 状态码为 400、带 param；给了 error_type 或 param 时
    还须与之相同。"""
    error = result.error
    if result.outcome is not StripeOutcome.REJECTED or error is None:
        return False
    if error.http_status != 400 or not error.param:
        return False
    if error_type is not None and error.type != error_type:
        return False
    return param is None or error.param == param


def _apply_session(
    db: Session, checkout_id: int, session: CheckoutSession, now: datetime
) -> ReconcileOutcome:
    """锁住记录写回会话。

    仍为 creating 时写会话 ID、付款页地址、PaymentIntent ID（有则写）、状态
    与付款状态（按会话的实际状态；complete 的完成时间为 now，expired 释放
    活动会话位）；会话状态不认识或 open 而没有地址时保持 creating。已不是
    creating（webhook 记为 complete、对账记为 expired）时只补齐缺失的会话 ID
    与付款页地址，不改状态与付款结果。
    """
    record = _lock_checkout(db, checkout_id)
    if record.status == CHECKOUT_CREATING:
        status = session.status
        if status is None or status not in _SESSION_STATUSES:
            db.rollback()
            return ReconcileOutcome.PENDING
        if status == CHECKOUT_OPEN and not session.url:
            db.rollback()
            return ReconcileOutcome.PENDING
        record.checkout_session_id = session.id
        record.checkout_url = session.url
        if session.payment_intent is not None:
            record.payment_intent_id = session.payment_intent.id
        payment_status = session.payment_status
        record.payment_status = payment_status if payment_status in PAYMENT_STATUSES else None
        record.status = status
        if status == CHECKOUT_COMPLETE:
            # 完成时间取本站首次得知的当前时间。
            record.completed_at = now
        if status == CHECKOUT_EXPIRED:
            record.active_order_id = None
    else:
        if record.checkout_session_id is None:
            record.checkout_session_id = session.id
        if record.checkout_url is None and session.url:
            record.checkout_url = session.url
    outcome = _OUTCOME_BY_STATUS[record.status]
    db.commit()
    return outcome


def _mark_expired(db: Session, checkout_id: int) -> tuple[ReconcileOutcome, bool]:
    """锁住记录，仍为 creating 时记为 expired 并释放活动会话位；否则不改。

    返回之后的状态与这次是否改了记录。
    """
    record = _lock_checkout(db, checkout_id)
    if record.status != CHECKOUT_CREATING:
        outcome = _OUTCOME_BY_STATUS[record.status]
        db.rollback()
        return outcome, False
    record.status = CHECKOUT_EXPIRED
    record.active_order_id = None
    db.commit()
    return ReconcileOutcome.EXPIRED, True


def _mark_needs_review(db: Session, checkout_id: int, now: datetime) -> ReconcileOutcome:
    """锁住记录，仍为 creating 且未告警时写告警时间、提交并记一条 error 日志；
    同一记录只记一次。"""
    record = _lock_checkout(db, checkout_id)
    if record.status != CHECKOUT_CREATING:
        outcome = _OUTCOME_BY_STATUS[record.status]
        db.rollback()
        return outcome
    if record.alerted_at is not None:
        db.rollback()
        return ReconcileOutcome.UNDER_REVIEW
    record.alerted_at = now
    db.commit()
    logger.error("%s checkout=%d", LOG_NEEDS_REVIEW, checkout_id)
    return ReconcileOutcome.UNDER_REVIEW


# ---------------------------------------------------------------- 读取与校验


def _lock_order(db: Session, order_id: int) -> Order:
    # 只锁订单行；populate_existing：会话里可能留着并发提交之前读到的旧值。
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).one()


def _lock_active_checkout(db: Session, order_id: int) -> StripeCheckout | None:
    stmt = (
        select(StripeCheckout)
        .where(StripeCheckout.active_order_id == order_id)
        .with_for_update()
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


def _check_lang(lang: object) -> None:
    if lang not in LINE_ITEM_NAMES:
        raise ValueError("lang must be en, zh or ms")


def _check_now(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")

"""后台「Stripe 待处理」列表（只读，不写审计）。

依据 docs/DESIGN.md 1.22（提交 ffb043e）「Stripe 测试环境支付」第 11 条：
- 「只显示对账字段（本站记录 ID、状态、金额、时间、错误类别）与订单号，不显示收货资料、
  Stripe 的任何 ID 与 Stripe 返回的客户资料（第 10 条）」。
- 第（1）项「待处理的结账记录：已写告警时间、状态不是 expired、所属订单仍为
  `awaiting_demo_payment` 的 `StripeCheckout`」；「按告警时间从早到晚列出，每行显示结账
  记录 ID、记录状态、订单号、记录创建时间与告警时间」。同一告警时间按记录 ID 从小到大。
- 第（2）项「失败的迟到付款退款：来源为会话（第 6 条）、该来源最新一次尝试为 failed 的
  `StripeRefund`。每行显示结账记录 ID、最新尝试 ID、订单号、金额、失败时间与错误类别」；
  按该尝试的 ID 从小到大。
- 「两类各按自己的条件退出列表：第（1）项在记录已为 expired 或订单已不是待支付时不再列出；
  第（2）项只看来源与最新一次尝试，最新一次已不是 failed 时不再列出，不论订单状态与结账
  记录是否已为 expired」。
「失败时间」在数据模型里没有对应字段（StripeRefund 只有创建、成功与上次复查时间），按
docs/HANDOFF.md 0.90 记录的 Kelvin 2026-11-08 决定给最新一次尝试的创建时间（attempted_at），
不加字段、不改设计。

两类都不分页。不读收货资料与会员资料，不返回 Stripe 的任何 ID（会话、PaymentIntent、退款）、
付款页地址与创建参数快照。访问授权由调用方（之后的后台接口，SHOP-TASK-225）先行校验，
它原样返回 StripePendingOut。本模块不写日志。库里的时间一律是不带时区的 UTC，返回的视图
里换成带 UTC 时区的时间。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, StripeCheckout, StripeRefund
from app.models.order import STATUS_AWAITING_PAYMENT
from app.models.stripe import CHECKOUT_EXPIRED, SOURCE_CHECKOUT, STRIPE_REFUND_FAILED


class PendingCheckout(BaseModel):
    """第（1）项的一行：待处理的结账记录。时间带 UTC 时区。"""

    checkout_id: int
    # creating、open 或 complete。
    status: str
    order_number: str
    created_at: datetime
    alerted_at: datetime


class FailedLateRefund(BaseModel):
    """第（2）项的一行：最新一次尝试为 failed 的迟到付款退款。时间带 UTC 时区。"""

    checkout_id: int
    # 该来源 ID 最大的尝试。
    attempt_id: int
    order_number: str
    amount_sen: int
    # 该尝试的创建时间，代替设计的「失败时间」（HANDOFF 0.90）。
    attempted_at: datetime
    # 该尝试的 last_error_type；Stripe 给出 failed 时可能为空。
    error_type: str | None


class StripePendingOut(BaseModel):
    """「Stripe 待处理」页的两类列表，各按所写顺序、不分页。"""

    checkouts: list[PendingCheckout]
    refunds: list[FailedLateRefund]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def stripe_pending(db: Session) -> StripePendingOut:
    """「Stripe 待处理」页的两类列表；只读、不加锁、不写审计，读完结束读事务。"""
    result = StripePendingOut(checkouts=_pending_checkouts(db), refunds=_failed_late_refunds(db))
    db.rollback()
    return result


def _pending_checkouts(db: Session) -> list[PendingCheckout]:
    """告警时间非空、状态不是 expired、订单为 awaiting_demo_payment 的结账记录，
    按告警时间从早到晚、同时刻按 ID 从小到大。"""
    stmt = (
        select(
            StripeCheckout.id,
            StripeCheckout.status,
            Order.order_number,
            StripeCheckout.created_at,
            StripeCheckout.alerted_at,
        )
        .join(Order, Order.id == StripeCheckout.order_id)
        .where(
            StripeCheckout.alerted_at.is_not(None),
            StripeCheckout.status != CHECKOUT_EXPIRED,
            Order.status == STATUS_AWAITING_PAYMENT,
        )
        .order_by(StripeCheckout.alerted_at, StripeCheckout.id)
    )
    return [
        PendingCheckout(
            checkout_id=checkout_id,
            status=status,
            order_number=order_number,
            created_at=_utc(created_at),
            alerted_at=_utc(alerted_at),
        )
        for checkout_id, status, order_number, created_at, alerted_at in db.execute(stmt).tuples()
    ]


def _failed_late_refunds(db: Session) -> list[FailedLateRefund]:
    """来源为会话、该来源 ID 最大的尝试为 failed 的那条尝试，不看订单状态与记录状态，
    按尝试 ID 从小到大。"""
    latest = (
        select(func.max(StripeRefund.id).label("attempt_id"))
        .where(StripeRefund.source_type == SOURCE_CHECKOUT)
        .group_by(StripeRefund.checkout_id)
        .subquery()
    )
    stmt = (
        select(
            StripeRefund.checkout_id,
            StripeRefund.id,
            Order.order_number,
            StripeRefund.amount_sen,
            StripeRefund.created_at,
            StripeRefund.last_error_type,
        )
        .join(latest, latest.c.attempt_id == StripeRefund.id)
        .join(StripeCheckout, StripeCheckout.id == StripeRefund.checkout_id)
        .join(Order, Order.id == StripeCheckout.order_id)
        .where(StripeRefund.status == STRIPE_REFUND_FAILED)
        .order_by(StripeRefund.id)
    )
    rows = db.execute(stmt).tuples()
    return [
        FailedLateRefund(
            checkout_id=checkout_id,
            attempt_id=attempt_id,
            order_number=order_number,
            amount_sen=amount_sen,
            attempted_at=_utc(created_at),
            error_type=error_type,
        )
        for checkout_id, attempt_id, order_number, amount_sen, created_at, error_type in rows
    ]

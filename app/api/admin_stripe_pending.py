"""后台「Stripe 待处理」A11 的接口：GET /api/admin/stripe-pending、
POST /api/admin/stripe-pending/checkouts/{checkout_id}/void、
POST /api/admin/stripe-pending/checkouts/{checkout_id}/refund-retry。

依据 docs/DESIGN.md 1.22（提交 ffb043e）「Stripe 测试环境支付」第 11 条：「页面与接口只认管理员
会话，写操作校验 CSRF，所有响应带 no-store；只显示对账字段（本站记录 ID、状态、金额、时间、错误
类别）与订单号，不显示收货资料、Stripe 的任何 ID 与 Stripe 返回的客户资料（第 10 条）」。列表的
查询见 app/services/admin_stripe_pending.py（SHOP-TASK-224），作废见 app/services/payment.py 的
void_alerted_checkout（SHOP-TASK-223），重试见 app/services/stripe_refund.py 的
retry_checkout_attempt（SHOP-TASK-224）。审计在本模块写（docs/HANDOFF.md 0.90）。路径只用结账
记录的内部整数 ID。

三个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

列表（GET）只读，不要求 CSRF，不写审计：原样返回 stripe_pending 的两个列表，另带由 cookie 算出
的 CSRF 令牌（照 app/api/admin_coupons.py 的列表接口），供页面提交作废与重试。

作废（POST …/void），每一步不通过即停止：
1. 请求体超过上限 413 → 不是 JSON 415（照 app/api/admin_refunds.py 的审核接口，先于会话检查）；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 路径里的记录 ID 与请求体（恰好是 {"verified": true}，多出字段、缺少或不为 true）一并校验，
   不合法 422；
5. 之后才调用 void_alerted_checkout：记录不存在 404 not_found，changed 409
   stripe_pending_changed，unavailable 503 stripe_unavailable，其余 200 {"result": 结果}。
审计 stripe_checkout_voided（对象为该结账记录，新值为结果）：voided 经 on_void 在作废的同一事务里
写；confirmed 与 cancelled 由服务层各自提交之后，另开事务写并提交；changed 与 unavailable 不写。

重试（POST …/refund-retry）照 admin_refunds.py 的 stripe-retry：不读请求体、不要幂等键；
require_admin（401）→ CSRF（403）→ 路径（422）→ retry_checkout_attempt；记录不存在 404
not_found，没有尝试或不能重试（含并发撞唯一约束）409 stripe_refund_not_retryable，超过合计上限
409 stripe_refund_cap（告警由服务层记），每种错误先回滚。成功时在同一事务里写审计
stripe_late_refund_retried（对象为新尝试，新值为结账记录 ID）后提交，再以 submit_refund_attempt
调用 Stripe，200 {"result": "retried"}；它的结果不改变响应，抛出异常时回滚、记一条 error 日志
（stripe_refund_submit_failed 与尝试 ID），由定时任务在下一轮发起。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；422 每条错误只给位置、类型与固定消息，不回显请求内容（请求体同
app/api/pay.py 的 _body_errors）。除上述 stripe_refund_submit_failed 外不写日志；响应不含收货
资料、会员资料、Stripe 的任何 ID、付款页地址与 Stripe 错误原文。
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import (
    GatewayDep,
    _body_errors,
    _conflict,
    _json_body,
    _stripe_unavailable,
    _utcnow,
)
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie, record_audit
from app.services.admin_stripe_pending import StripePendingOut, stripe_pending
from app.services.payment import CheckoutNotFound, VoidResult, void_alerted_checkout
from app.services.stripe_gateway import StripeGateway
from app.services.stripe_refund import (
    NoRefundAttempt,
    RefundCapExceeded,
    RetryCheckoutNotFound,
    RetryNotAllowed,
    retry_checkout_attempt,
    submit_refund_attempt,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/stripe-pending", tags=["admin"], route_class=_NoStoreRoute)

# 审计操作名与对象类别（第 11 条第（1）、（2）项）。
STRIPE_CHECKOUT_VOIDED = "stripe_checkout_voided"
STRIPE_LATE_REFUND_RETRIED = "stripe_late_refund_retried"
AUDIT_TARGET_CHECKOUT = "stripe_checkout"
AUDIT_TARGET_ATTEMPT = "stripe_refund"

# 提交后调用 Stripe 抛出异常时的 error 日志类别（只含类别与尝试 ID）。
LOG_SUBMIT_FAILED = "stripe_refund_submit_failed"

# 结账记录内部 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限。
_CHECKOUT_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_CHECKOUT_ID = 2**31 - 1

# 服务层作废之后要写审计的结果；changed 与 unavailable 不写。
_AUDITED_AFTER = frozenset({VoidResult.CONFIRMED, VoidResult.CANCELLED})


class VoidIn(BaseModel):
    """管理员已勾选「已在 Stripe 测试后台核对过这笔订单的会话与付款」；未勾选服务端拒绝。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 严格布尔再要求为 True：Literal[True] 按相等比较，会放过 1。
    verified: StrictBool

    @field_validator("verified")
    @classmethod
    def _must_be_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("verified must be true")
        return value


class StripePendingPageOut(StripePendingOut):
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交作废与重试。
    csrf_token: str


class VoidOut(BaseModel):
    """作废的处理结果：已确认付款、已取消订单（Stripe 给出确定结果）或凭核实作废。"""

    result: Literal["confirmed", "cancelled", "voided"]


class RefundRetryOut(BaseModel):
    """重试成功；不带 Stripe 退款 ID、错误类别或其他 Stripe 内容，页面随后重新读取列表。"""

    result: Literal["retried"] = "retried"


async def _void_body(request: Request) -> bytes:
    """作废只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


def _checkout_id_errors(checkout_id: str) -> list[dict[str, Any]]:
    """路径里的结账记录内部 ID；不合法时只给固定消息，不回显所给的值。"""
    if not _CHECKOUT_ID.fullmatch(checkout_id) or int(checkout_id) > MAX_CHECKOUT_ID:
        msg = "Checkout ID should be a positive integer"
        return [_error(("path", "checkout_id"), "checkout_id_invalid", msg)]
    return []


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
VoidBodyDep = Annotated[bytes, Depends(_void_body)]
SessionDep = Annotated[Session, Depends(get_session)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


@router.get("", response_model=StripePendingPageOut)
def list_pending(admin: AdminDep, session: SessionDep) -> StripePendingPageOut:
    """两类待处理列表，各按服务层的顺序、不分页；只有对账字段与订单号。不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    found = stripe_pending(session)
    return StripePendingPageOut(
        checkouts=found.checkouts, refunds=found.refunds, csrf_token=csrf_token
    )


@router.post("/checkouts/{checkout_id}/void", response_model=VoidOut)
def void_checkout(
    raw: VoidBodyDep,
    admin: AdminDep,
    checkout_id: str,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> VoidOut | JSONResponse:
    """作废并取消订单：先向 Stripe 查询，给不出确定结果时才凭管理员的核实作废。

    CSRF、路径或请求体不通过时不读记录、不调用 Stripe。
    """
    require_admin_csrf(request, admin)
    errors = _checkout_id_errors(checkout_id)
    try:
        VoidIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors:
        raise RequestValidationError(errors)

    record_id = int(checkout_id)
    account_id = admin.account.id
    now = _utcnow()

    def audit(db: Session, result: VoidResult) -> None:
        record_audit(
            db,
            STRIPE_CHECKOUT_VOIDED,
            account_id,
            now,
            target_type=AUDIT_TARGET_CHECKOUT,
            target_id=record_id,
            old_value=None,
            new_value=result.value,
        )

    try:
        # voided 的审计在作废的同一事务里写，随作废一并提交。
        result = void_alerted_checkout(
            session,
            gateway,
            record_id,
            now,
            on_void=lambda db: audit(db, VoidResult.VOIDED),
        )
    except CheckoutNotFound:
        session.rollback()
        raise _not_found() from None
    if result is VoidResult.CHANGED:
        return _conflict("stripe_pending_changed")
    if result is VoidResult.UNAVAILABLE:
        return _stripe_unavailable()
    if result in _AUDITED_AFTER:
        # 确认与取消已由服务层各自提交；审计另开事务。
        audit(session, result)
        session.commit()
    return VoidOut(result=result.value)


@router.post("/checkouts/{checkout_id}/refund-retry", response_model=RefundRetryOut)
def refund_retry(
    admin: AdminDep,
    checkout_id: str,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> RefundRetryOut | JSONResponse:
    """重试迟到与不符付款的退款：新建一条 pending 尝试、写审计、提交，再以它调用 Stripe。

    不读请求体、不要幂等键：重复点击与并发由活动来源列的唯一约束保证只有一个成功。
    CSRF 或路径不通过时不读记录；每种错误都先回滚，不写库。
    """
    require_admin_csrf(request, admin)
    errors = _checkout_id_errors(checkout_id)
    if errors:
        raise RequestValidationError(errors)
    record_id = int(checkout_id)
    now = _utcnow()
    try:
        attempt_id = retry_checkout_attempt(session, record_id, now)
    except RetryCheckoutNotFound:
        session.rollback()
        raise _not_found() from None
    except (NoRefundAttempt, RetryNotAllowed):
        session.rollback()
        return _conflict("stripe_refund_not_retryable")
    except RefundCapExceeded:
        # 告警已由服务层记。
        session.rollback()
        return _conflict("stripe_refund_cap")
    record_audit(
        session,
        STRIPE_LATE_REFUND_RETRIED,
        admin.account.id,
        now,
        target_type=AUDIT_TARGET_ATTEMPT,
        target_id=attempt_id,
        old_value=None,
        new_value=str(record_id),
    )
    session.commit()
    _submit(session, gateway, attempt_id)
    return RefundRetryOut()


def _submit(session: Session, gateway: StripeGateway, attempt_id: int) -> None:
    """提交之后调用 Stripe 退款接口；结果不改变响应。

    抛出异常时回滚并记一条只含类别与尝试 ID 的 error 日志（不记异常内容），
    尝试保持 pending，由定时任务在下一轮发起。
    """
    try:
        submit_refund_attempt(session, gateway, attempt_id, _utcnow())
    except Exception:
        session.rollback()
        logger.error("%s attempt=%d", LOG_SUBMIT_FAILED, attempt_id)

"""后台退款审核 A03 的接口：POST /api/admin/refunds/query、
GET /api/admin/refunds/{refund_id}、POST /api/admin/refunds/{refund_id}/approve、
POST /api/admin/refunds/{refund_id}/reject、POST /api/admin/refunds/{refund_id}/stripe-retry。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」「权限与资料保护」与 docs/HANDOFF.md
0.33 记录的 Kelvin 2026-10-06 决定；列表与详情见 app/services/admin_refunds.py，批准与拒绝的
规则与锁定协议见 app/services/refund_review.py（SHOP-TASK-040）。字段参照 docs/UX.md 0.8 的
A03。路径只用退款申请的内部整数 ID；订单号与收货资料不进任何路径或查询参数，响应也不含收货
资料。

五个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

列表（POST …/query）只读，不要求 CSRF，不写审计：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有可选的 status（requested、approved、rejected 之一）、page（1 到 10000 的
   整数，默认 1）与 order_id（订单的内部整数 ID，1 到 2147483647，或 null；SHOP-TASK-046，
   依据 docs/HANDOFF.md 0.35 记录的 Kelvin 2026-10-06 决定），多出字段 422。订单内部 ID
   只在请求体里，不进路径或查询参数；订单不存在或没有申请时是空列表而不是 404。
2. require_admin（401）→ 语言参数（422）。
详情（GET）：require_admin（401）→ 路径里的申请 ID 与语言参数（422）→
申请不存在 404 not_found → 返回，另带由 cookie 算出的 CSRF 令牌。不写审计。
积分返还与追回（placed_by_member、points_returned、points_reversed、
points_estimated，SHOP-TASK-106）由 refund_detail 按当前时间给出，
取值规则见 app/services/admin_refunds.py。
批准与拒绝（POST …/approve、…/reject），每一步不通过即停止：
1. 请求体超过 8 KB 413 → 不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 路径里的申请 ID、Idempotency-Key（规则同 SHOP-TASK-020）与请求体（只有 reason）一并
   校验，不合法 422；
5. 之后才调用 approve_refund 或 reject_refund（理由不合法 422 reason_invalid，在读取申请
   之前；申请不存在 404 not_found），成功后提交，200 与审核结果。
409 的 detail：idempotency_conflict（同键不同内容）与 refund_already_reviewed（用新键审核
已审核的申请，另带申请当前状态 status）。

批准后调用 Stripe 与手动重试（SHOP-TASK-131），依据 docs/DESIGN.md 1.12（提交 acf576e）
「Stripe 测试环境支付」第 8 条（提交后调用 Stripe 退款接口，以该记录的本站 ID 为幂等键；
最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试；本站的批准结果不因 Stripe 退款
失败而回滚）与 docs/HANDOFF.md 0.54 第（1）项（在批准请求里提交之后立即调用；在那之前及调用
结果未知时由定时任务在下一轮发起）：
- 批准经 app/api/pay.py 的 GatewayDep 取适配器。只在实际审核（不是按审核幂等键重放）且提交
  之后，找活动来源列等于该申请的尝试，有则以 submit_refund_attempt 调用 Stripe；没有尝试
  （模拟支付、现金退款额为 0、因合计上限或缺 PaymentIntent 没建成）与重放时不调用。
- POST …/stripe-retry 不读请求体、不要幂等键：require_admin（401）→ CSRF（403）→ 路径里的
  申请 ID（422）→ retry_request_attempt；申请不存在 404 not_found，未批准 409
  refund_not_approved，没有尝试或不能重试（含并发撞唯一约束）409 stripe_refund_not_retryable，
  超过合计上限 409 stripe_refund_cap（告警日志由服务层记）。成功时在同一事务里写审计
  admin_stripe_refund_retried 后提交，再调用 Stripe，200 {"result": "retried"}。
- submit_refund_attempt 的结果不改变响应；它抛出异常时回滚、记一条 error 日志
  （stripe_refund_submit_failed 与尝试 ID），仍回原响应，由定时任务在下一轮发起。

五个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；结构与幂等键的 422 每条错误只给位置、类型与固定消息，不回显
请求内容（请求体同 app/api/pay.py 的 _body_errors，语言参数同 _language，路径里的申请 ID
在这里自行校验）。除上述 stripe_refund_submit_failed 外不写日志；错误响应不含订单号、理由、
收货资料、用户名、令牌或 Stripe 内容。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminContext, AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import IDEMPOTENCY_HEADER, _error, _key_errors
from app.api.pay import GatewayDep, _body_errors, _conflict, _json_body, _language, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie, record_audit
from app.services.admin_orders import MAX_PAGE
from app.services.admin_refunds import (
    AdminRefundDetail,
    AdminRefundPage,
    RefundStatus,
    query_refunds,
    refund_detail,
)
from app.services.catalog import Language
from app.services.refund_review import (
    AUDIT_TARGET_REFUND_REQUEST,
    IdempotencyConflict,
    RefundAlreadyReviewed,
    RefundRequestNotFound,
    ReviewOutcome,
    ReviewReasonInvalid,
    approve_refund,
    reject_refund,
)
from app.services.stripe_gateway import StripeGateway
from app.services.stripe_refund import (
    ADMIN_STRIPE_REFUND_RETRIED,
    NoRefundAttempt,
    RefundCapExceeded,
    RefundRequestNotApproved,
    RetryNotAllowed,
    RetryRequestNotFound,
    active_request_attempt_id,
    retry_request_attempt,
    submit_refund_attempt,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/refunds", tags=["admin"], route_class=_NoStoreRoute)

# 提交后调用 Stripe 抛出异常时的 error 日志类别（只含类别与尝试 ID）。
LOG_SUBMIT_FAILED = "stripe_refund_submit_failed"

# 申请内部 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限。
_REFUND_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_REFUND_ID = 2**31 - 1

# approve_refund 与 reject_refund 的签名。
_ReviewFunction = Callable[
    [Session, int, int, str | None, str, datetime], tuple[ReviewOutcome, bool]
]


class RefundQueryIn(BaseModel):
    """可选的申请状态、页码与订单内部 ID。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: RefundStatus | None = None
    page: int = Field(default=1, ge=1, le=MAX_PAGE)
    # 订单的内部 ID 与申请的内部 ID 同为 INT 列，上限相同。
    order_id: int | None = Field(default=None, ge=1, le=MAX_REFUND_ID)


class ReviewIn(BaseModel):
    """只有审核理由；批准可省略或为空，拒绝必填。

    是否必填与长度（去掉首尾空白后至多 500 个字符）由 SHOP-TASK-040 的规则函数判断，
    不合法时 422 reason_invalid。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str | None = Field(default=None, repr=False)


class AdminRefundDetailOut(AdminRefundDetail):
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交批准与拒绝。
    csrf_token: str


class StripeRetryOut(BaseModel):
    """手动重试成功；不带 Stripe 退款 ID、错误类别或其他 Stripe 内容，页面随后重新读取详情。"""

    result: Literal["retried"] = "retried"


async def _query_input(request: Request) -> RefundQueryIn:
    raw = await _json_body(request)
    try:
        return RefundQueryIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


async def _review_body(request: Request) -> bytes:
    """批准与拒绝只在这里判断 413 与 415；结构与幂等键在 CSRF 之后才校验。"""
    return await _json_body(request)


def _refund_id_errors(refund_id: str) -> list[dict[str, Any]]:
    """路径里的申请内部 ID；不合法时只给固定消息，不回显所给的值。"""
    if not _REFUND_ID.fullmatch(refund_id) or int(refund_id) > MAX_REFUND_ID:
        msg = "Refund request ID should be a positive integer"
        return [_error(("path", "refund_id"), "refund_id_invalid", msg)]
    return []


def _refund_id(refund_id: str) -> int:
    errors = _refund_id_errors(refund_id)
    if errors:
        raise RequestValidationError(errors)
    return int(refund_id)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
QueryDep = Annotated[RefundQueryIn, Depends(_query_input)]
ReviewBodyDep = Annotated[bytes, Depends(_review_body)]
RefundIdDep = Annotated[int, Depends(_refund_id)]
LanguageDep = Annotated[Language, Depends(_language)]
SessionDep = Annotated[Session, Depends(get_session)]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not_found")


@router.post("/query", response_model=AdminRefundPage)
def query(
    body: QueryDep, admin: AdminDep, lang: LanguageDep, session: SessionDep
) -> AdminRefundPage:
    """申请列表：排序与分页见 query_refunds；不含收货资料与理由。"""
    return query_refunds(session, body.status, body.page, lang, order_id=body.order_id)


@router.get("/{refund_id}", response_model=AdminRefundDetailOut)
def detail(
    admin: AdminDep,
    refund_id: RefundIdDep,
    lang: LanguageDep,
    session: SessionDep,
) -> AdminRefundDetailOut:
    """申请详情含审核理由（只在后台返回）与积分返还与追回，不含收货资料；不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    found = refund_detail(session, refund_id, lang, _utcnow())
    if found is None:
        raise _not_found()
    return AdminRefundDetailOut.model_validate({**found.model_dump(), "csrf_token": csrf_token})


@router.post("/{refund_id}/approve", response_model=ReviewOutcome)
def approve(
    raw: ReviewBodyDep,
    admin: AdminDep,
    refund_id: str,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> ReviewOutcome | JSONResponse:
    """批准；理由可省略或为空。成功与同键重放都是 200 与审核结果。

    实际审核并提交之后，以本次建的尝试调用 Stripe；结果不改变响应。
    """
    reviewed = _review(approve_refund, raw, admin, refund_id, session, request)
    if isinstance(reviewed, JSONResponse):
        return reviewed
    outcome, created = reviewed
    # 按审核幂等键重放时不调用：那时的尝试（若仍为 pending）留给定时任务。
    if created:
        attempt_id = active_request_attempt_id(session, outcome.refund_request_id)
        if attempt_id is not None:
            _submit(session, gateway, attempt_id)
    return outcome


@router.post("/{refund_id}/reject", response_model=ReviewOutcome)
def reject(
    raw: ReviewBodyDep,
    admin: AdminDep,
    refund_id: str,
    session: SessionDep,
    request: Request,
) -> ReviewOutcome | JSONResponse:
    """拒绝；理由必填。成功与同键重放都是 200 与审核结果。"""
    reviewed = _review(reject_refund, raw, admin, refund_id, session, request)
    if isinstance(reviewed, JSONResponse):
        return reviewed
    return reviewed[0]


@router.post("/{refund_id}/stripe-retry", response_model=StripeRetryOut)
def stripe_retry(
    admin: AdminDep,
    refund_id: str,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> StripeRetryOut | JSONResponse:
    """手动重试 Stripe 退款：新建一条 pending 尝试、写审计、提交，再以它调用 Stripe。

    不读请求体、不要幂等键：重复点击与并发由活动来源列的唯一约束保证只有一个成功。
    CSRF 或路径不通过时不读申请；每种错误都先回滚，不写库。
    """
    require_admin_csrf(request, admin)
    refund_request_id = _refund_id(refund_id)
    now = _utcnow()
    try:
        attempt_id = retry_request_attempt(session, refund_request_id, now)
    except RetryRequestNotFound:
        session.rollback()
        raise _not_found() from None
    except RefundRequestNotApproved:
        session.rollback()
        return _conflict("refund_not_approved")
    except (NoRefundAttempt, RetryNotAllowed):
        session.rollback()
        return _conflict("stripe_refund_not_retryable")
    except RefundCapExceeded:
        # 告警日志已由服务层记。
        session.rollback()
        return _conflict("stripe_refund_cap")
    record_audit(
        session,
        ADMIN_STRIPE_REFUND_RETRIED,
        admin.account.id,
        now,
        target_type=AUDIT_TARGET_REFUND_REQUEST,
        target_id=refund_request_id,
        old_value=None,
        new_value=str(attempt_id),
    )
    session.commit()
    _submit(session, gateway, attempt_id)
    return StripeRetryOut()


def _submit(session: Session, gateway: StripeGateway, attempt_id: int) -> None:
    """提交之后调用 Stripe 退款接口；结果不改变响应（批准结果不因 Stripe 退款失败而回滚）。

    抛出异常时回滚并记一条只含类别与尝试 ID 的 error 日志（不记异常内容），
    尝试保持 pending，由定时任务在下一轮发起。
    """
    try:
        submit_refund_attempt(session, gateway, attempt_id, _utcnow())
    except Exception:
        session.rollback()
        logger.error("%s attempt=%d", LOG_SUBMIT_FAILED, attempt_id)


def _review(
    review: _ReviewFunction,
    raw: bytes,
    admin: AdminContext,
    refund_id: str,
    session: Session,
    request: Request,
) -> tuple[ReviewOutcome, bool] | JSONResponse:
    """CSRF → 路径、幂等键与请求体 → 规则函数 → 提交。CSRF 或 422 不通过时不读申请。

    返回审核结果与是否实际审核（重放为假）；409 时返回响应。
    """
    require_admin_csrf(request, admin)
    errors = _refund_id_errors(refund_id)
    errors.extend(_key_errors(request))
    body: ReviewIn | None = None
    try:
        body = ReviewIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)

    account_id = admin.account.id
    key = request.headers[IDEMPOTENCY_HEADER]
    try:
        outcome, reviewed = review(session, int(refund_id), account_id, body.reason, key, _utcnow())
    except ReviewReasonInvalid:
        session.rollback()
        raise HTTPException(status_code=422, detail="reason_invalid") from None
    except RefundRequestNotFound:
        session.rollback()
        raise _not_found() from None
    except IdempotencyConflict:
        session.rollback()
        return _conflict("idempotency_conflict")
    except RefundAlreadyReviewed as exc:
        session.rollback()
        return _conflict("refund_already_reviewed", exc.status)
    # 重放没有改动，提交只结束事务、释放订单行的锁。
    session.commit()
    return outcome, reviewed

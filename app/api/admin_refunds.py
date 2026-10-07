"""后台退款审核 A03 的接口：POST /api/admin/refunds/query、
GET /api/admin/refunds/{refund_id}、POST /api/admin/refunds/{refund_id}/approve、
POST /api/admin/refunds/{refund_id}/reject。

依据 docs/DESIGN.md 1.11（提交 2d13250）「订单与退款状态」「权限与资料保护」与 docs/HANDOFF.md
0.33 记录的 Kelvin 2026-10-06 决定；列表与详情见 app/services/admin_refunds.py，批准与拒绝的
规则与锁定协议见 app/services/refund_review.py（SHOP-TASK-040）。字段参照 docs/UX.md 0.8 的
A03。路径只用退款申请的内部整数 ID；订单号与收货资料不进任何路径或查询参数，响应也不含收货
资料。

四个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

列表（POST …/query）只读，不要求 CSRF，不写审计：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
   请求体只有可选的 status（requested、approved、rejected 之一）、page（1 到 10000 的
   整数，默认 1）与 order_id（订单的内部整数 ID，1 到 2147483647，或 null；SHOP-TASK-046，
   依据 docs/HANDOFF.md 0.35 记录的 Kelvin 2026-10-06 决定），多出字段 422。订单内部 ID
   只在请求体里，不进路径或查询参数；订单不存在或没有申请时是空列表而不是 404。
2. require_admin（401）→ 语言参数（422）。
详情（GET）：require_admin（401）→ 路径里的申请 ID 与语言参数（422）→
申请不存在 404 not_found → 返回，另带由 cookie 算出的 CSRF 令牌。不写审计。
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

四个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；结构与幂等键的 422 每条错误只给位置、类型与固定消息，不回显
请求内容（请求体同 app/api/pay.py 的 _body_errors，语言参数同 _language，路径里的申请 ID
在这里自行校验）。不写日志；错误响应不含订单号、理由、收货资料、用户名或令牌。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminContext, AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import IDEMPOTENCY_HEADER, _error, _key_errors
from app.api.pay import _body_errors, _conflict, _json_body, _language, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
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
    IdempotencyConflict,
    RefundAlreadyReviewed,
    RefundRequestNotFound,
    ReviewOutcome,
    ReviewReasonInvalid,
    approve_refund,
    reject_refund,
)

router = APIRouter(prefix="/api/admin/refunds", tags=["admin"], route_class=_NoStoreRoute)

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
    """申请详情含审核理由（只在后台返回），不含收货资料；不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    found = refund_detail(session, refund_id, lang)
    if found is None:
        raise _not_found()
    return AdminRefundDetailOut.model_validate({**found.model_dump(), "csrf_token": csrf_token})


@router.post("/{refund_id}/approve", response_model=ReviewOutcome)
def approve(
    raw: ReviewBodyDep,
    admin: AdminDep,
    refund_id: str,
    session: SessionDep,
    request: Request,
) -> ReviewOutcome | JSONResponse:
    """批准；理由可省略或为空。成功与同键重放都是 200 与审核结果。"""
    return _review(approve_refund, raw, admin, refund_id, session, request)


@router.post("/{refund_id}/reject", response_model=ReviewOutcome)
def reject(
    raw: ReviewBodyDep,
    admin: AdminDep,
    refund_id: str,
    session: SessionDep,
    request: Request,
) -> ReviewOutcome | JSONResponse:
    """拒绝；理由必填。成功与同键重放都是 200 与审核结果。"""
    return _review(reject_refund, raw, admin, refund_id, session, request)


def _review(
    review: _ReviewFunction,
    raw: bytes,
    admin: AdminContext,
    refund_id: str,
    session: Session,
    request: Request,
) -> ReviewOutcome | JSONResponse:
    """CSRF → 路径、幂等键与请求体 → 规则函数 → 提交。CSRF 或 422 不通过时不读申请。"""
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
        outcome, _ = review(session, int(refund_id), account_id, body.reason, key, _utcnow())
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
    return outcome

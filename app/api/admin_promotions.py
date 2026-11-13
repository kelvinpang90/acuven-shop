"""后台促销活动 A12 的列表、读取、新建、保存与状态操作接口：
GET 与 POST /api/admin/promotions，GET、PUT 与 DELETE /api/admin/promotions/{promotion_id}，
POST /api/admin/promotions/{promotion_id}/publish、/withdraw、/end 与 /copy。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条（后台接口只传递与校验
字段，规则、核对与审计在服务层；不返回个人资料）与 docs/UX.md 0.20 的 A12；SHOP-TASK-242 的落实
写法。规则见 SHOP-TASK-239、249 与 241 的 app/services/admin_promotions.py；本文件只做请求格式、
会话与 CSRF 的检查，照 app/api/admin_categories.py 的写法，只有请求体上限不同。

全部接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。
时间以马来西亚本地「YYYY-MM-DDTHH:MM」字符串或 null 收发，与服务层相同（格式由服务层检查）。

列表（GET）：require_admin（401）→ promotions（list_promotions 的全部项）与由 cookie 算出的
CSRF 令牌。读取（GET /{promotion_id}）：require_admin（401）→ 路径 ID（不合法 422）→
read_promotion 的 PromotionDetail 与 CSRF 令牌。两者都不写审计。
带请求体的写操作（新建、保存、发布、撤回、提前结束、删除），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 64 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 路径里的活动 ID 照 _category_id 校验，不合法 422（新建没有这一步）；
5. 请求体：新建为 PromotionInput 的字段，保存另有 version，发布、撤回、提前结束与删除只有
   version；多出字段、缺字段或类型不对 422；
6. 服务层。新建成功 201，删除成功 204 且没有响应体，其余 200；都回 PromotionDetail。
复制（POST /{promotion_id}/copy）没有请求体、不读请求体：2.–4. 后 copy_promotion，成功 201 与
新草稿。

服务层的拒绝（说明文字不回给调用方）：
- PromotionInvalid：422 {"code": "promotion_invalid", "problems": [...]}，每项 code、field、
  product_id、product_name；
- PromotionOverlap：409 {"code": "promotion_overlap", "name": 对方的内部名称}；
- PromotionChanged：409 promotion_changed；PromotionNotFound：404 promotion_not_found；
- PromotionStateInvalid：409 {"code": "promotion_state_invalid", "state": 当前状态}；
- PromotionEnded：409 promotion_ended。

全部接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。
不写日志。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminContext, AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionChanged,
    PromotionDetail,
    PromotionEnded,
    PromotionInput,
    PromotionInvalid,
    PromotionNotFound,
    PromotionOverlap,
    PromotionStateInvalid,
    PromotionSummary,
    RuleInput,
    TierInput,
    copy_promotion,
    create_promotion,
    delete_promotion,
    end_promotion,
    list_promotions,
    publish_promotion,
    read_promotion,
    save_promotion,
    withdraw_promotion,
)

router = APIRouter(prefix="/api/admin/promotions", tags=["admin"], route_class=_NoStoreRoute)

# 落实写法：规则可带 20 × 230 个商品与分类 ID，app/api/pay.py 的 8 KB 不够。
MAX_BODY_BYTES = 64 * 1024

# 活动 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限（同分类 ID）。
_PROMOTION_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_PROMOTION_ID = 2**31 - 1


# ---------------------------------------------------------------------------
# 请求体：字段名与 SHOP-TASK-239 的输入数据类相同，只校验结构与 JSON 类型（strict：整数
# 不收布尔值、字符串与浮点数）。取值、长度、格式与条数一律交给服务层。数据类有默认值的
# 字段在这里也可省略，默认值相同。
# ---------------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TierIn(_In):
    threshold_sen: int
    amount_sen: int | None
    percent: int | None


class RuleIn(_In):
    kind: str
    discount_mode: str | None = None
    percent: int | None = None
    amount_sen: int | None = None
    buy_quantity: int | None = None
    get_quantity: int | None = None
    get_percent: int | None = None
    threshold_sen: int | None = None
    product_ids: list[int] = []
    category_ids: list[int] = []
    tiers: list[TierIn] = []


class DecorationIn(_In):
    pack: str
    palette: str
    ambient: str
    home_poster: str
    list_strip: str
    card_badge: str
    detail_ribbon: str
    cart_progress: str
    poster_mode: str


class CopyIn(_In):
    title_en: str | None = None
    title_zh: str | None = None
    title_ms: str | None = None
    subtitle_en: str | None = None
    subtitle_zh: str | None = None
    subtitle_ms: str | None = None
    badge_en: str | None = None
    badge_zh: str | None = None
    badge_ms: str | None = None
    strip_en: str | None = None
    strip_zh: str | None = None
    strip_ms: str | None = None
    poster_alt_en: str | None = None
    poster_alt_zh: str | None = None
    poster_alt_ms: str | None = None


class PromotionIn(_In):
    """新建的请求体。JSON 字段 copy 与 BaseModel.copy 同名，属性另起名、以别名收发。"""

    name: str
    starts_at: str | None
    ends_at: str | None
    coupon_combinable: bool
    points_combinable: bool
    rules: list[RuleIn]
    decoration: DecorationIn
    copy_text: CopyIn = Field(alias="copy")


class PromotionSaveIn(PromotionIn):
    version: int


class VersionIn(_In):
    """发布、撤回、提前结束与删除的请求体：读取时的版本号。"""

    version: int


# ---------------------------------------------------------------------------
# 响应：PromotionDetail 与 PromotionSummary 的同名字段。装饰、文案与满额档与请求同形状。
# ---------------------------------------------------------------------------


class ScopeProductOut(BaseModel):
    id: int
    slug: str
    name_en: str | None


class RuleOut(BaseModel):
    rule_id: int
    kind: str
    discount_mode: str | None
    percent: int | None
    amount_sen: int | None
    buy_quantity: int | None
    get_quantity: int | None
    get_percent: int | None
    threshold_sen: int | None
    product_ids: list[int]
    category_ids: list[int]
    tiers: list[TierIn]
    # 规则显式选的商品，与 product_ids 一一对应。
    products: list[ScopeProductOut]


class PromotionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    version: int
    state: str
    ended_early_at: str | None
    name: str
    starts_at: str | None
    ends_at: str | None
    coupon_combinable: bool
    points_combinable: bool
    rules: list[RuleOut]
    decoration: DecorationIn
    copy_text: CopyIn = Field(alias="copy")
    poster_desktop_url: str | None
    poster_phone_url: str | None


class PromotionPageOut(PromotionOut):
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交保存与状态操作。
    csrf_token: str


class PromotionSummaryOut(BaseModel):
    id: int
    name: str
    state: str
    starts_at: str | None
    # 实际结束（提前结束的时刻或结束时间）。
    ends_at: str | None
    rule_count: int
    # 不带时区的 UTC。
    updated_at: datetime


class PromotionListOut(BaseModel):
    promotions: list[PromotionSummaryOut]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交新建与状态操作。
    csrf_token: str


def _promotion_out(detail: PromotionDetail) -> PromotionOut:
    return PromotionOut(**asdict(detail))


def _summary_out(row: PromotionSummary) -> PromotionSummaryOut:
    return PromotionSummaryOut(**asdict(row))


def _promotion_input(body: PromotionIn) -> PromotionInput:
    return PromotionInput(
        name=body.name,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        coupon_combinable=body.coupon_combinable,
        points_combinable=body.points_combinable,
        rules=tuple(_rule_input(rule) for rule in body.rules),
        decoration=DecorationInput(**body.decoration.model_dump()),
        copy=CopyInput(**body.copy_text.model_dump()),
    )


def _rule_input(rule: RuleIn) -> RuleInput:
    return RuleInput(
        kind=rule.kind,
        discount_mode=rule.discount_mode,
        percent=rule.percent,
        amount_sen=rule.amount_sen,
        buy_quantity=rule.buy_quantity,
        get_quantity=rule.get_quantity,
        get_percent=rule.get_percent,
        threshold_sen=rule.threshold_sen,
        product_ids=tuple(rule.product_ids),
        category_ids=tuple(rule.category_ids),
        tiers=tuple(TierInput(**tier.model_dump()) for tier in rule.tiers),
    )


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def _promotion_id(promotion_id: str) -> int:
    """路径里的活动 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _PROMOTION_ID.fullmatch(promotion_id) or int(promotion_id) > MAX_PROMOTION_ID:
        msg = "Promotion ID should be a positive integer"
        loc = ("path", "promotion_id")
        raise RequestValidationError([_error(loc, "promotion_id_invalid", msg)])
    return int(promotion_id)


async def _write_body(request: Request) -> bytes:
    """逐块读请求体，超过 64 KB 就停下返回 413，不看 Content-Length 是否如实；
    读完之后才判断 Content-Type（照 app/api/pay.py 的 _json_body，只有上限不同）。
    结构在 CSRF 之后才校验。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    if not _is_json(request.headers.get("content-type")):
        raise HTTPException(status_code=415, detail="request body must be JSON")
    return b"".join(chunks)


def _require_admin_csrf(request: Request, admin: AdminDep) -> None:
    """先完成会话与 CSRF 检查，再验证路径参数。"""
    require_admin_csrf(request, admin)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
WriteBodyDep = Annotated[bytes, Depends(_write_body)]
CsrfDep = Annotated[None, Depends(_require_admin_csrf)]
PromotionIdDep = Annotated[int, Depends(_promotion_id)]
SessionDep = Annotated[Session, Depends(get_session)]

_Rejection = (
    PromotionInvalid,
    PromotionOverlap,
    PromotionChanged,
    PromotionNotFound,
    PromotionStateInvalid,
    PromotionEnded,
)


def _parse[M: BaseModel](model: type[M], raw: bytes) -> M:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


def _csrf_token(admin: AdminContext) -> str:
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return csrf_token


def _rejected(session: Session, exc: Exception) -> HTTPException:
    # check_promotion_input 不通过时服务层还没有回滚；说明文字不回给调用方。
    session.rollback()
    if isinstance(exc, PromotionInvalid):
        problems = [asdict(problem) for problem in exc.problems]
        return HTTPException(
            status_code=422, detail={"code": "promotion_invalid", "problems": problems}
        )
    if isinstance(exc, PromotionOverlap):
        return HTTPException(
            status_code=409, detail={"code": "promotion_overlap", "name": exc.other_name}
        )
    if isinstance(exc, PromotionStateInvalid):
        return HTTPException(
            status_code=409, detail={"code": "promotion_state_invalid", "state": exc.state}
        )
    if isinstance(exc, PromotionNotFound):
        return HTTPException(status_code=404, detail="promotion_not_found")
    if isinstance(exc, PromotionEnded):
        return HTTPException(status_code=409, detail="promotion_ended")
    return HTTPException(status_code=409, detail="promotion_changed")


_StateAction = Callable[[Session, int, int, int, datetime], PromotionDetail]


def _change_state(
    action: _StateAction, raw: bytes, admin: AdminContext, promotion_id: int, session: Session
) -> PromotionOut:
    """发布、撤回与提前结束：请求体只有 version，服务层核对版本与状态后提交并写一条审计。"""
    body = _parse(VersionIn, raw)
    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        changed = action(session, account_id, promotion_id, body.version, _utcnow())
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return _promotion_out(changed)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("", response_model=PromotionListOut)
def read_list(admin: AdminDep, session: SessionDep) -> PromotionListOut:
    """全部活动（按状态排列）与 CSRF 令牌；只读，不写审计。"""
    csrf_token = _csrf_token(admin)
    return PromotionListOut(
        promotions=[_summary_out(row) for row in list_promotions(session, _utcnow())],
        csrf_token=csrf_token,
    )


@router.post("", status_code=201, response_model=PromotionOut)
def create(
    raw: WriteBodyDep, admin: AdminDep, session: SessionDep, request: Request
) -> PromotionOut:
    """CSRF → 请求体 → 新建草稿（服务层提交并写一条审计）。"""
    require_admin_csrf(request, admin)
    body = _parse(PromotionIn, raw)

    account_id = admin.account.id
    try:
        created = create_promotion(session, account_id, _promotion_input(body), _utcnow())
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return _promotion_out(created)


@router.get("/{promotion_id}", response_model=PromotionPageOut)
def read(admin: AdminDep, promotion_id: PromotionIdDep, session: SessionDep) -> PromotionPageOut:
    """一个活动的全部可改内容、状态、版本号与 CSRF 令牌；只读，不写审计。"""
    csrf_token = _csrf_token(admin)
    try:
        detail = read_promotion(session, promotion_id, _utcnow())
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return PromotionPageOut(**asdict(detail), csrf_token=csrf_token)


@router.put("/{promotion_id}", response_model=PromotionOut)
def save(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    session: SessionDep,
) -> PromotionOut:
    """CSRF → 路径 ID → 请求体 → 保存。都与当前值相同时不加版本、不写审计。"""
    body = _parse(PromotionSaveIn, raw)

    account_id = admin.account.id
    try:
        saved = save_promotion(
            session, account_id, promotion_id, body.version, _promotion_input(body), _utcnow()
        )
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return _promotion_out(saved)


@router.post("/{promotion_id}/publish", response_model=PromotionOut)
def publish(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    session: SessionDep,
) -> PromotionOut:
    """发布草稿（只对 draft）。"""
    return _change_state(publish_promotion, raw, admin, promotion_id, session)


@router.post("/{promotion_id}/withdraw", response_model=PromotionOut)
def withdraw(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    session: SessionDep,
) -> PromotionOut:
    """撤回为草稿（只对未被订单引用的 scheduled）。"""
    return _change_state(withdraw_promotion, raw, admin, promotion_id, session)


@router.post("/{promotion_id}/end", response_model=PromotionOut)
def end(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    session: SessionDep,
) -> PromotionOut:
    """提前结束（只对 running）。"""
    return _change_state(end_promotion, raw, admin, promotion_id, session)


@router.post("/{promotion_id}/copy", status_code=201, response_model=PromotionOut)
def copy(
    admin: AdminDep, _csrf: CsrfDep, promotion_id: PromotionIdDep, session: SessionDep
) -> PromotionOut:
    """复制为新草稿（任何状态）；没有请求体、不读请求体。"""
    account_id = admin.account.id
    try:
        copied = copy_promotion(session, account_id, promotion_id, _utcnow())
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return _promotion_out(copied)


@router.delete("/{promotion_id}", status_code=204, response_class=Response)
def delete(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    session: SessionDep,
    request: Request,
) -> Response:
    """删除草稿（连同它的海报图）；请求体只有 version，成功 204 且没有响应体。"""
    body = _parse(VersionIn, raw)

    account_id = admin.account.id
    settings = request.app.state.settings
    try:
        delete_promotion(session, settings, account_id, promotion_id, body.version, _utcnow())
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return Response(status_code=204)

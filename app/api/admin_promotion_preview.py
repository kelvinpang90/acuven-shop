"""管理员预览草稿与未开始活动的数据接口（A12 前台预览）：
GET /api/admin/promotions/{promotion_id}/preview、…/preview/products、…/preview/products/{slug}
与 POST …/preview/quote。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 11 条：管理员可在前台以预览方式查看
草稿或未开始活动的首页、商品列表、商品详情、购物车与专题页，看到的装饰与价格如同该活动正在
生效；预览的数据只经后台接口（凭管理员会话）取得，公开接口不接受任何预览参数；预览不改变访客
看到的内容，也不产生订单（本模块没有下单接口）。docs/UX.md 0.20 的 A12「前台预览」：草稿与
已发布未开始的活动可预览，唯一的禁用条件是已保存的内容里没有开始时间或结束时间，或结束时间已过。
SHOP-TASK-245 的落实写法；不改服务层规则。

可预览：活动为 draft 或 scheduled（SHOP-TASK-239 的 promotion_state），开始与结束时间都有，且
实际结束晚于当前时刻；不满足即 409 promotion_preview_unavailable，不存在 404 promotion_not_found。
活动以 SHOP-TASK-233 的 load_promotion 读出（不看是否生效），数据分别复用：
- GET …/preview?lang=：SHOP-TASK-243 的 promotion_display，响应同 GET /api/promotion
  （promotion 与 server_time）；
- GET …/preview/products：SHOP-TASK-244 的 list_products，查询参数同 GET /api/catalog/products
  （含 sale）；
- GET …/preview/products/{slug}：同一任务的 get_product，找不到 404 product not found；
- POST …/preview/quote：SHOP-TASK-234 的 quote_cart，请求体与响应同 POST /api/checkout/quote。

每个路由的顺序，每一步不通过即停止：
1. require_admin（401 admin_session_required）；
2. 只有 POST：X-CSRF-Token（403 csrf_failed）；
3. 路径里的活动 ID 照 app/api/admin_promotions.py 的 _promotion_id，不合法 422；
4. 查询参数与请求体：GET 的查询参数 422（含目录接口在处理函数里做的 option 解析）；POST 的请求体
   照游客计价接口：按实际读到的字节逐块判断，超过 8 KB 即 413，不查 Content-Type，按游客计价的
   请求模型校验，不合格 422；不经游客计价的来源限流；
5. 读活动：404 或 409；
6. 取数。只有 quote_cart 内部按收货地址查运费时的目的地不合法（422）与运费行缺失（503）在这一步，
   回答照游客计价接口。

处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由 SHOP-TASK-027 的路由类
统一加上。422 每条错误只给位置、类型与固定消息，不回显请求内容。只读，不写审计、不写日志。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.admin_promotions import _promotion_id
from app.api.catalog import _option_filters, _slug_filter
from app.api.checkout import MAX_BODY_BYTES, QuoteRequest
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import LanguageDep, _body_errors
from app.api.promotion import PromotionOut
from app.db.session import get_session
from app.services.admin_promotions import STATE_DRAFT, STATE_SCHEDULED, promotion_state
from app.services.catalog import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Language,
    ProductDetail,
    ProductPage,
    SortOrder,
    get_product,
    list_products,
)
from app.services.checkout import CartItem, CheckoutQuote, quote_cart
from app.services.promotion_display import promotion_display
from app.services.promotions import LoadedPromotion, load_promotion
from app.services.shipping import InvalidDestination, ShippingRateMissing

router = APIRouter(prefix="/api/admin/promotions", tags=["admin"], route_class=_NoStoreRoute)

_PREVIEWABLE_STATES = (STATE_DRAFT, STATE_SCHEDULED)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _require_admin_csrf(request: Request, admin: AdminDep) -> None:
    """先完成会话与 CSRF 检查，再验证路径参数。"""
    require_admin_csrf(request, admin)


async def _quote_body(request: Request) -> QuoteRequest:
    """照 app/api/checkout.py 的 _quote_request 逐块读请求体，超过上限就停下返回 413，
    不看 Content-Length 是否如实、不查 Content-Type；不按来源计数。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    try:
        return QuoteRequest.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None


# 依赖按声明顺序解析：会话 → CSRF → 路径 ID → 请求体，都在读活动之前。
CsrfDep = Annotated[None, Depends(_require_admin_csrf)]
PromotionIdDep = Annotated[int, Depends(_promotion_id)]
SlugsDep = Annotated[list[str], Depends(_slug_filter)]
QuoteBodyDep = Annotated[QuoteRequest, Depends(_quote_body)]
SessionDep = Annotated[Session, Depends(get_session)]


def _previewable(session: Session, promotion_id: int, now: datetime) -> LoadedPromotion:
    """读出要预览的活动；不存在 404，不满足可预览条件 409。"""
    promotion = load_promotion(session, promotion_id)
    if promotion is None:
        raise HTTPException(status_code=404, detail="promotion_not_found")
    starts_at, effective_end = promotion.starts_at, promotion.effective_end
    # 先确认两个时间都有：已发布的活动缺时间时 promotion_state 抛 ValueError。
    previewable = (
        starts_at is not None
        and effective_end is not None
        and effective_end > now
        and promotion_state(promotion.status, starts_at, effective_end, now) in _PREVIEWABLE_STATES
    )
    if not previewable:
        raise HTTPException(status_code=409, detail="promotion_preview_unavailable")
    return promotion


@router.get("/{promotion_id}/preview", response_model=PromotionOut)
def preview(
    _admin: AdminDep, promotion_id: PromotionIdDep, lang: LanguageDep, session: SessionDep
) -> PromotionOut:
    """活动的展示数据与服务端当前时间，同 GET /api/promotion。"""
    now = _utcnow()
    promotion = _previewable(session, promotion_id, now)
    display = promotion_display(session, promotion.promotion_id, lang)
    return PromotionOut(promotion=display, server_time=now.replace(tzinfo=UTC))


@router.get("/{promotion_id}/preview/products", response_model=ProductPage)
def preview_products(
    _admin: AdminDep,
    promotion_id: PromotionIdDep,
    slugs: SlugsDep,
    session: SessionDep,
    lang: Language = "en",
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[list[str] | None, Query()] = None,
    option: Annotated[list[str] | None, Query()] = None,
    sort: SortOrder = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    sale: bool = False,
) -> ProductPage:
    """商品列表，参数同 GET /api/catalog/products；sale=true 为专题页列表。"""
    options = _option_filters(option or [])
    promotion = _previewable(session, promotion_id, _utcnow())
    return list_products(
        session,
        lang=lang,
        query=q.strip() if q else None,
        categories=category or [],
        options=options,
        sort=sort,
        page=page,
        page_size=page_size,
        slugs=slugs,
        promotion=promotion,
        sale_only=sale,
    )


@router.get("/{promotion_id}/preview/products/{slug}", response_model=ProductDetail)
def preview_product(
    _admin: AdminDep,
    promotion_id: PromotionIdDep,
    slug: str,
    session: SessionDep,
    lang: Language = "en",
) -> ProductDetail:
    """商品详情，同 GET /api/catalog/products/{slug}；未发布与不存在同一个 404。"""
    promotion = _previewable(session, promotion_id, _utcnow())
    detail = get_product(session, slug, lang, promotion=promotion)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return detail


@router.post("/{promotion_id}/preview/quote", response_model=CheckoutQuote)
def preview_quote(
    _csrf: CsrfDep,
    promotion_id: PromotionIdDep,
    body: QuoteBodyDep,
    session: SessionDep,
    lang: Language = "en",
) -> CheckoutQuote:
    """购物车计价（游客），请求体与响应同 POST /api/checkout/quote；只计价，不下单。"""
    promotion = _previewable(session, promotion_id, _utcnow())
    try:
        return quote_cart(
            session,
            [CartItem(sku=line.sku, quantity=line.quantity) for line in body.lines],
            lang=lang,
            country_code=body.country_code,
            state_code=body.state_code,
            promotion=promotion,
        )
    except InvalidDestination:
        raise HTTPException(status_code=422, detail="invalid country or state code") from None
    except ShippingRateMissing:
        # 不回显缺哪一行，也不以零运费代替。
        raise HTTPException(status_code=503, detail="shipping is unavailable") from None

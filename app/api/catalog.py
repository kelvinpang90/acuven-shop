"""商品目录的公开只读接口：全部在 /api/catalog/ 下，只有 GET。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）；字段参照 docs/UX.md 0.2 的 P01–P03 审阅稿。
分类图片、多规格标记与规格筛选项按 docs/UX.md 0.5 的 P01、P02 补充（SHOP-TASK-013）。
lang 只接受 en、zh、ms，默认 en，其他值由 FastAPI 校验拒绝（422）。
未发布与不存在的 slug 返回同一个 404，不暴露未发布的商品是否存在。
商品列表可重复的 slug 筛选供首页按店铺装修取精选商品卡片（SHOP-TASK-053）。
商品列表与详情的活动价与角标、列表的 sale 筛选依据 docs/DESIGN.md 1.23（提交 b7f9355）
「节日促销活动」第 3、10 条与 docs/UX.md 0.20 的 P15（SHOP-TASK-244）：每次请求以
active_promotion(db, _utcnow()) 读生效活动、不缓存；活动价随时间变化，两者的成功响应
带 Cache-Control: no-store（错误响应照旧）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session

from app.api.orders import _error
from app.db.session import get_session
from app.services.catalog import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    CategoryListItem,
    Language,
    OptionDetail,
    ProductDetail,
    ProductPage,
    SortOrder,
    get_product,
    list_categories,
    list_filter_options,
    list_products,
)
from app.services.promotions import active_promotion

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

SessionDep = Annotated[Session, Depends(get_session)]

MAX_SLUGS = 20
MAX_SLUG_LENGTH = 100


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _slug_filter(slug: Annotated[list[str] | None, Query()] = None) -> list[str]:
    """可重复的 slug=<商品 slug>（SHOP-TASK-053）：最多 MAX_SLUGS 个，每个 1 到
    MAX_SLUG_LENGTH 个字符；不合法时 422，写法同 app/api/pay.py 的 _language，不回显所给的值。
    """
    slugs = slug or []
    if len(slugs) > MAX_SLUGS:
        msg = f"At most {MAX_SLUGS} slug values are allowed"
        raise RequestValidationError([_error(("query", "slug"), "too_many_slugs", msg)])
    msg = f"Slug should have 1 to {MAX_SLUG_LENGTH} characters"
    errors = [
        _error(("query", "slug", index), "slug_length_invalid", msg)
        for index, value in enumerate(slugs)
        if not 1 <= len(value) <= MAX_SLUG_LENGTH
    ]
    if errors:
        raise RequestValidationError(errors)
    return slugs


def _option_filters(raw: list[str]) -> dict[str, list[str]]:
    """把可重复的 option=<规格名 code>:<规格值 code> 按规格名分组。"""
    filters: dict[str, list[str]] = {}
    for item in raw:
        option_code, _, value_code = item.partition(":")
        if not option_code or not value_code:
            raise HTTPException(status_code=422, detail="option must be <option>:<value>")
        filters.setdefault(option_code, []).append(value_code)
    return filters


@router.get("/categories", response_model=list[CategoryListItem])
def categories(session: SessionDep, lang: Language = "en") -> list[CategoryListItem]:
    return list_categories(session, lang)


@router.get("/options", response_model=list[OptionDetail])
def options(session: SessionDep, lang: Language = "en") -> list[OptionDetail]:
    """商品列表的规格筛选项；返回的 code 写成 option=<规格名 code>:<规格值 code>。"""
    return list_filter_options(session, lang)


@router.get("/products", response_model=ProductPage)
def products(
    slugs: Annotated[list[str], Depends(_slug_filter)],
    session: SessionDep,
    response: Response,
    lang: Language = "en",
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[list[str] | None, Query()] = None,
    option: Annotated[list[str] | None, Query()] = None,
    sort: SortOrder = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    sale: bool = False,
) -> ProductPage:
    """sale=true 为专题页列表（P15）：只列生效活动商品级规则范围内的商品，没有活动时为空页。"""
    result = list_products(
        session,
        lang=lang,
        query=q.strip() if q else None,
        categories=category or [],
        options=_option_filters(option or []),
        sort=sort,
        page=page,
        page_size=page_size,
        slugs=slugs,
        promotion=active_promotion(session, _utcnow()),
        sale_only=sale,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/products/{slug}", response_model=ProductDetail)
def product_detail(
    slug: str, session: SessionDep, response: Response, lang: Language = "en"
) -> ProductDetail:
    detail = get_product(session, slug, lang, promotion=active_promotion(session, _utcnow()))
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    response.headers["Cache-Control"] = "no-store"
    return detail

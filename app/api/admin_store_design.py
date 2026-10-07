"""后台店铺装修 A08 的接口（不含标志图）：GET /api/admin/store-design、
PUT /api/admin/store-design。

依据 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.10 的 A08 与 docs/HANDOFF.md 0.34 记录的
Kelvin 2026-10-06 决定（首版不含标志图），读取与保存规则见 app/services/store_design.py。主题与
主色以 app/models/store_design.py 的 THEMES 与 THEME_ACCENTS 为准。

两个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

读取（GET）：require_admin（401）→ 语言参数（422）→ 返回当前设置、可挑选的商品与由 cookie
算出的 CSRF 令牌。设置行不存在时按默认值返回。不写审计。
保存（PUT），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 语言参数与请求体一并校验，不合法 422：请求体只有 theme、accent（字符串或 null）、
   home_blocks（恰好 4 项 {block, visible}，block 为四个区块的一个排列，数组顺序即位置）与
   featured_product_ids（0 到 4 个不重复的正整数，数组顺序即位置），多出字段 422；
5. 业务校验（422，{"detail": "<错误码>"}）：theme_invalid、accent_invalid、
   featured_unavailable；之后才写入，成功或与当前完全相同都是 200 与保存后的设置。
语言参数只决定返回的商品名称，默认 en。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（请求体同 app/api/pay.py 的
_body_errors，语言参数同 _language）。不写日志。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _body_errors, _json_body, _language, _utcnow
from app.db.session import get_session
from app.models.store_design import FEATURED_MAX, HOME_BLOCKS
from app.services.admin_auth import csrf_token_for_cookie
from app.services.catalog import Language
from app.services.store_design import (
    AdminStoreDesign,
    HomeBlock,
    StoreDesignInvalid,
    product_choices,
    read_admin_store_design,
    save_store_design,
)

router = APIRouter(prefix="/api/admin/store-design", tags=["admin"], route_class=_NoStoreRoute)

# 商品内部 ID 为 INT 列，上限相同。
MAX_PRODUCT_ID = 2**31 - 1
BLOCK_COUNT = len(HOME_BLOCKS)

BlockName = Literal["hero", "how", "categories", "featured"]
ProductId = Annotated[int, Field(ge=1, le=MAX_PRODUCT_ID)]


class HomeBlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    block: BlockName
    visible: bool


class StoreDesignIn(BaseModel):
    """主题、主色、四个区块（数组顺序即位置）与精选商品 ID（数组顺序即位置）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    theme: str
    accent: str | None
    home_blocks: list[HomeBlockIn] = Field(min_length=BLOCK_COUNT, max_length=BLOCK_COUNT)
    featured_product_ids: list[ProductId] = Field(max_length=FEATURED_MAX)

    @field_validator("home_blocks")
    @classmethod
    def _blocks_are_a_permutation(cls, value: list[HomeBlockIn]) -> list[HomeBlockIn]:
        # 固定消息，不回显所给的区块。
        if sorted(block.block for block in value) != sorted(HOME_BLOCKS):
            raise ValueError("home_blocks must list each block exactly once")
        return value

    @field_validator("featured_product_ids")
    @classmethod
    def _featured_are_distinct(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("featured_product_ids must not repeat a product")
        return value


class HomeBlockOut(BaseModel):
    block: str
    visible: bool


class FeaturedOut(BaseModel):
    product_id: int
    slug: str
    name: str | None
    # 为假即此刻不满足 published()，前台不显示它。
    published: bool


class ChoiceOut(BaseModel):
    product_id: int
    slug: str
    name: str


class StoreDesignOut(BaseModel):
    theme: str
    # 为空即该主题的默认主色。
    accent: str | None
    home_blocks: list[HomeBlockOut]
    featured: list[FeaturedOut]


class AdminStoreDesignOut(StoreDesignOut):
    # 全部满足 published() 的商品，按商品 ID 升序。
    choices: list[ChoiceOut]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交保存。
    csrf_token: str


async def _save_body(request: Request) -> bytes:
    """保存只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
SaveBodyDep = Annotated[bytes, Depends(_save_body)]
LanguageDep = Annotated[Language, Depends(_language)]
SessionDep = Annotated[Session, Depends(get_session)]


def _out(design: AdminStoreDesign) -> dict[str, Any]:
    return {
        "theme": design.theme,
        "accent": design.accent,
        "home_blocks": [
            {"block": block.block, "visible": block.visible} for block in design.home_blocks
        ],
        "featured": [
            {
                "product_id": item.product_id,
                "slug": item.slug,
                "name": item.name,
                "published": item.published,
            }
            for item in design.featured
        ],
    }


@router.get("", response_model=AdminStoreDesignOut)
def read(admin: AdminDep, lang: LanguageDep, session: SessionDep) -> AdminStoreDesignOut:
    """当前设置、可挑选的商品与 CSRF 令牌；不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    design = read_admin_store_design(session, lang)
    choices = [
        {"product_id": choice.product_id, "slug": choice.slug, "name": choice.name}
        for choice in product_choices(session, lang)
    ]
    return AdminStoreDesignOut.model_validate(
        {**_out(design), "choices": choices, "csrf_token": csrf_token}
    )


@router.put("", response_model=StoreDesignOut)
def save(
    raw: SaveBodyDep,
    admin: AdminDep,
    session: SessionDep,
    request: Request,
    lang: str = "en",
) -> StoreDesignOut:
    """CSRF → 语言参数与请求体 → 业务校验 → 保存。与当前完全相同时不写入、不写审计。"""
    require_admin_csrf(request, admin)
    errors: list[dict[str, Any]] = []
    language: Language = "en"
    try:
        language = _language(lang)
    except RequestValidationError as exc:
        errors.extend(exc.errors())
    body: StoreDesignIn | None = None
    try:
        body = StoreDesignIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    if errors or body is None:
        raise RequestValidationError(errors)

    blocks = [HomeBlock(block=item.block, visible=item.visible) for item in body.home_blocks]
    # 保存先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        design, _ = save_store_design(
            session,
            account_id,
            body.theme,
            body.accent,
            blocks,
            list(body.featured_product_ids),
            language,
            _utcnow(),
        )
    except StoreDesignInvalid as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from None
    return StoreDesignOut.model_validate(_out(design))

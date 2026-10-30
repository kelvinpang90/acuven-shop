"""后台分类 A10 的读取、新建与保存接口：GET /api/admin/categories、POST /api/admin/categories、
PUT /api/admin/categories/{category_id}。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 14 条（后台维护分类）；
docs/UX.md 0.17 的 A10（列表与各分类的前台可见商品件数、新建、逐个保存、取值不合格与分类已被
改动两种结果）；docs/HANDOFF.md 0.76 的落实写法。规则见 SHOP-TASK-180 的
app/services/admin_categories.py；本文件只做请求格式、会话与 CSRF 的检查。

三个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

读取（GET）：require_admin（401）→ 返回 categories（load_admin_categories 的全部项）与由 cookie
算出的 CSRF 令牌。不写审计。
新建（POST），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 请求体有必填的 name_en、name_zh、name_ms（JSON 字符串）与 is_active（JSON 布尔值），多出字段
   或类型不对 422；
5. create_category：取值不合格 422 {"detail": "category_invalid"}，分类已被改动 409
   {"detail": "category_changed"}。成功 201 与新分类。
保存（PUT），每一步不通过即停止：
1.–3. 同新建；
4. 路径里的分类 ID 照 app/api/admin_orders.py 的 _order_id 校验，不合法 422；
5. 请求体同新建，另有必填的整数 version；
6. save_category：两种拒绝的回答同新建，不存在的分类也是 category_changed。成功或未变都 200
   与保存后的分类。

三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。
不写日志。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_categories import (
    AdminCategory,
    CategoryChanged,
    CategoryInvalid,
    create_category,
    load_admin_categories,
    save_category,
)

router = APIRouter(prefix="/api/admin/categories", tags=["admin"], route_class=_NoStoreRoute)

# 分类 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限（同订单 ID）。
_CATEGORY_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_CATEGORY_ID = 2**31 - 1


class CategoryIn(BaseModel):
    """新建的请求体。长度、首尾空白与英文名称必填交给服务层，不通过即 category_invalid。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    name_en: str
    name_zh: str
    name_ms: str
    is_active: bool


class CategorySaveIn(CategoryIn):
    version: int


class CategoryOut(BaseModel):
    id: int
    slug: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    is_active: bool
    version: int
    # 该分类下前台可见的商品件数。
    visible_products: int


class CategoryListOut(BaseModel):
    categories: list[CategoryOut]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交新建与保存。
    csrf_token: str


def _category_out(row: AdminCategory) -> CategoryOut:
    return CategoryOut(
        id=row.id,
        slug=row.slug,
        name_en=row.name_en,
        name_zh=row.name_zh,
        name_ms=row.name_ms,
        is_active=row.is_active,
        version=row.version,
        visible_products=row.visible_products,
    )


def _category_id(category_id: str) -> int:
    """路径里的分类 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _CATEGORY_ID.fullmatch(category_id) or int(category_id) > MAX_CATEGORY_ID:
        msg = "Category ID should be a positive integer"
        raise RequestValidationError([_error(("path", "category_id"), "category_id_invalid", msg)])
    return int(category_id)


async def _write_body(request: Request) -> bytes:
    """新建与保存只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


def _require_admin_csrf(request: Request, admin: AdminDep) -> None:
    """保存先完成会话与 CSRF 检查，再验证路径参数。"""
    require_admin_csrf(request, admin)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
WriteBodyDep = Annotated[bytes, Depends(_write_body)]
CsrfDep = Annotated[None, Depends(_require_admin_csrf)]
CategoryIdDep = Annotated[int, Depends(_category_id)]
SessionDep = Annotated[Session, Depends(get_session)]


def _rejected(exc: CategoryInvalid | CategoryChanged) -> HTTPException:
    # 服务层出错时已回滚；说明文字不回给调用方。
    if isinstance(exc, CategoryInvalid):
        return HTTPException(status_code=422, detail="category_invalid")
    return HTTPException(status_code=409, detail="category_changed")


@router.get("", response_model=CategoryListOut)
def read(admin: AdminDep, session: SessionDep) -> CategoryListOut:
    """全部分类（含停用的）与 CSRF 令牌；只读，不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return CategoryListOut(
        categories=[_category_out(row) for row in load_admin_categories(session)],
        csrf_token=csrf_token,
    )


@router.post("", status_code=201, response_model=CategoryOut)
def create(
    raw: WriteBodyDep, admin: AdminDep, session: SessionDep, request: Request
) -> CategoryOut:
    """CSRF → 请求体 → 新建（服务层提交并写一条审计）。"""
    require_admin_csrf(request, admin)
    try:
        body = CategoryIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        created = create_category(
            session,
            account_id,
            body.name_en,
            body.name_zh,
            body.name_ms,
            body.is_active,
            _utcnow(),
        )
    except (CategoryInvalid, CategoryChanged) as exc:
        raise _rejected(exc) from None
    return _category_out(created)


@router.put("/{category_id}", response_model=CategoryOut)
def save(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    category_id: CategoryIdDep,
    session: SessionDep,
) -> CategoryOut:
    """CSRF → 路径 ID → 请求体 → 保存。都与当前值相同时不加版本、不写审计。"""
    try:
        body = CategorySaveIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    account_id = admin.account.id
    try:
        saved = save_category(
            session,
            account_id,
            category_id,
            body.version,
            body.name_en,
            body.name_zh,
            body.name_ms,
            body.is_active,
            _utcnow(),
        )
    except (CategoryInvalid, CategoryChanged) as exc:
        raise _rejected(exc) from None
    return _category_out(saved)

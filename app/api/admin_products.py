"""后台商品 A04 的列表、读取、新建与保存接口：GET /api/admin/products、
GET /api/admin/products/{product_id}、POST /api/admin/products、
PUT /api/admin/products/{product_id}。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（后台维护商品）；
docs/UX.md 0.17 的 A04（列表、单件编辑、新建、保存后的取值不合格与商品已被改动）；
docs/HANDOFF.md 0.77 的落实写法（取值不合格回 422 product_invalid，商品已被改动回 409
product_changed，读取不存在的商品回 404 product_not_found；请求体上限 256 KB，由本文件逐块读取；
JSON 字段名与 SHOP-TASK-183 的请求结构同名；金额一律以整数仙收发）。规则见 SHOP-TASK-183 至 185
的 app/services/admin_products.py；本文件只做请求格式、会话与 CSRF 的检查。

四个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

列表（GET）：require_admin（401）→ 返回 products（load_admin_products 的全部项）
与由 cookie 算出的 CSRF 令牌。
单件（GET）：require_admin（401）→ 路径 ID（照 app/api/admin_orders.py 的 _order_id，
不合法 422）→ load_admin_product，不存在 404 {"detail": "product_not_found"}。
两者都不写审计。
新建（POST），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 256 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 请求体结构与 JSON 类型（strict：多出字段、缺字段、null 或类型不对 422）；
   可省略的 id、temp_id 与 option_values 不收 null，是否提交看 model_fields_set，
   两者的搭配交给服务层；
5. create_product：取值不合格 422 {"detail": "product_invalid"}，商品已被改动 409
   {"detail": "product_changed"}。成功 201 与新商品（同单件读取）。
保存（PUT），每一步不通过即停止：
1.–3. 同新建（照 app/api/admin_coupons.py 先声明 CsrfDep、再声明路径 ID，
   403 先于路径的 422）；
4. 路径 ID 不合法 422；
5. 请求体同新建，另有必填的整数 version；
6. save_product：两种拒绝的回答同新建，不存在的商品也是 product_changed。成功或未变都 200 与
   保存后的商品。

四个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。
不写日志。
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error, _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_products import (
    AdminProduct,
    AdminProductListItem,
    OptionInput,
    ProductChanged,
    ProductInput,
    ProductInvalid,
    ValueInput,
    ValueRef,
    VariantInput,
    create_product,
    load_admin_product,
    load_admin_products,
    save_product,
)

router = APIRouter(prefix="/api/admin/products", tags=["admin"], route_class=_NoStoreRoute)

# HANDOFF 0.77：整件商品的规格名、规格值与 SKU 可能远超 app/api/pay.py 的 8 KB。
MAX_BODY_BYTES = 256 * 1024

# 商品 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限（同订单 ID）。
_PRODUCT_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_PRODUCT_ID = 2**31 - 1


# ---------------------------------------------------------------------------
# 请求体：只校验结构与 JSON 类型（strict：整数不收布尔值、字符串与浮点数）。
# 可省略的字段的默认值只为省略时占位，显式的 null 仍是 422；是否提交看 model_fields_set。
# ---------------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ValueRefIn(_In):
    """新增 SKU 所选的一个规格值：id 引用已有的，temp_id 引用同一请求里新增的。"""

    id: int = 0
    temp_id: str = ""


class ValueIn(_In):
    """规格值：已有的带 id，新增的带 temp_id。"""

    id: int = 0
    temp_id: str = ""
    name_en: str
    name_zh: str
    name_ms: str


class OptionIn(_In):
    """规格名：新建商品时不带 id，保存时带 id。"""

    id: int = 0
    name_en: str
    name_zh: str
    name_ms: str
    values: list[ValueIn]


class VariantIn(_In):
    """SKU：已有的带 id、不带 option_values；新增的带 temp_id 与 option_values。"""

    id: int = 0
    temp_id: str = ""
    option_values: list[ValueRefIn] = []
    price_sen: int
    daily_initial_stock: int
    is_active: bool


class ProductIn(_In):
    """新建的请求体。取值范围、长度、上限与 id、temp_id 的搭配都交给服务层。"""

    category_id: int
    name_en: str
    name_zh: str
    name_ms: str
    description_en: str
    description_zh: str
    description_ms: str
    is_active: bool
    max_per_order: int
    options: list[OptionIn]
    variants: list[VariantIn]


class ProductSaveIn(ProductIn):
    version: int


# ---------------------------------------------------------------------------
# 响应（字段同服务层的读取结果；文案为空时为 null）
# ---------------------------------------------------------------------------


class ProductListItemOut(BaseModel):
    id: int
    slug: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    is_active: bool
    category_id: int
    category_name_en: str | None
    category_name_zh: str | None
    category_name_ms: str | None


class ProductListOut(BaseModel):
    products: list[ProductListItemOut]
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交新建与保存。
    csrf_token: str


class ValueOut(BaseModel):
    id: int
    code: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None


class OptionOut(BaseModel):
    id: int
    code: str
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    values: list[ValueOut]


class VariantOut(BaseModel):
    id: int
    sku: str
    price_sen: int
    daily_initial_stock: int
    available_stock: int
    is_active: bool
    # 按规格名的顺序。
    option_value_ids: list[int]


class ImageOut(BaseModel):
    id: int
    storage_ref: str


class ProductOut(BaseModel):
    id: int
    slug: str
    version: int
    category_id: int
    name_en: str | None
    name_zh: str | None
    name_ms: str | None
    description_en: str | None
    description_zh: str | None
    description_ms: str | None
    is_active: bool
    max_per_order: int
    options: list[OptionOut]
    variants: list[VariantOut]
    images: list[ImageOut]


def _list_item_out(row: AdminProductListItem) -> ProductListItemOut:
    return ProductListItemOut(**asdict(row))


def _product_out(product: AdminProduct) -> ProductOut:
    return ProductOut.model_validate(asdict(product))


# ---------------------------------------------------------------------------
# 请求体转成服务层的请求结构
# ---------------------------------------------------------------------------


def _given[T](item: BaseModel, field: str, value: T) -> T | None:
    """省略的字段为 None。"""
    return value if field in item.model_fields_set else None


def _value_ref(item: ValueRefIn) -> ValueRef:
    return ValueRef(id=_given(item, "id", item.id), temp_id=_given(item, "temp_id", item.temp_id))


def _value_input(item: ValueIn) -> ValueInput:
    return ValueInput(
        id=_given(item, "id", item.id),
        temp_id=_given(item, "temp_id", item.temp_id),
        name_en=item.name_en,
        name_zh=item.name_zh,
        name_ms=item.name_ms,
    )


def _option_input(item: OptionIn) -> OptionInput:
    return OptionInput(
        id=_given(item, "id", item.id),
        name_en=item.name_en,
        name_zh=item.name_zh,
        name_ms=item.name_ms,
        values=[_value_input(value) for value in item.values],
    )


def _variant_input(item: VariantIn) -> VariantInput:
    refs = _given(item, "option_values", item.option_values)
    return VariantInput(
        id=_given(item, "id", item.id),
        temp_id=_given(item, "temp_id", item.temp_id),
        option_values=None if refs is None else [_value_ref(ref) for ref in refs],
        price_sen=item.price_sen,
        daily_initial_stock=item.daily_initial_stock,
        is_active=item.is_active,
    )


def _product_input(body: ProductIn) -> ProductInput:
    return ProductInput(
        category_id=body.category_id,
        name_en=body.name_en,
        name_zh=body.name_zh,
        name_ms=body.name_ms,
        description_en=body.description_en,
        description_zh=body.description_zh,
        description_ms=body.description_ms,
        is_active=body.is_active,
        max_per_order=body.max_per_order,
        options=[_option_input(option) for option in body.options],
        variants=[_variant_input(variant) for variant in body.variants],
    )


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def _product_id(product_id: str) -> int:
    """路径里的商品 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _PRODUCT_ID.fullmatch(product_id) or int(product_id) > MAX_PRODUCT_ID:
        msg = "Product ID should be a positive integer"
        raise RequestValidationError([_error(("path", "product_id"), "product_id_invalid", msg)])
    return int(product_id)


async def _write_body(request: Request) -> bytes:
    """逐块读请求体，超过 256 KB 就停下返回 413，不看 Content-Length 是否如实；
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
    """保存先完成会话与 CSRF 检查，再验证路径参数。"""
    require_admin_csrf(request, admin)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
WriteBodyDep = Annotated[bytes, Depends(_write_body)]
CsrfDep = Annotated[None, Depends(_require_admin_csrf)]
ProductIdDep = Annotated[int, Depends(_product_id)]
SessionDep = Annotated[Session, Depends(get_session)]


def _rejected(session: Session, exc: ProductInvalid | ProductChanged) -> HTTPException:
    # 第一步不通过时服务层还没有回滚；说明文字不回给调用方。
    session.rollback()
    if isinstance(exc, ProductInvalid):
        return HTTPException(status_code=422, detail="product_invalid")
    return HTTPException(status_code=409, detail="product_changed")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("", response_model=ProductListOut)
def list_products(admin: AdminDep, session: SessionDep) -> ProductListOut:
    """全部商品（含未上架的）与 CSRF 令牌；只读，不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return ProductListOut(
        products=[_list_item_out(row) for row in load_admin_products(session)],
        csrf_token=csrf_token,
    )


@router.get("/{product_id}", response_model=ProductOut)
def read(admin: AdminDep, product_id: ProductIdDep, session: SessionDep) -> ProductOut:
    """一件商品的全部可改内容、版本号、各行 ID 与图片；只读，不写审计。"""
    product = load_admin_product(session, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product_not_found")
    return _product_out(product)


@router.post("", status_code=201, response_model=ProductOut)
def create(raw: WriteBodyDep, admin: AdminDep, session: SessionDep, request: Request) -> ProductOut:
    """CSRF → 请求体 → 新建（服务层提交并为每个新行写一条审计）。"""
    require_admin_csrf(request, admin)
    try:
        body = ProductIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        created = create_product(session, account_id, _product_input(body), _utcnow())
    except (ProductInvalid, ProductChanged) as exc:
        raise _rejected(session, exc) from None
    return _product_out(created)


@router.put("/{product_id}", response_model=ProductOut)
def save(
    raw: WriteBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    product_id: ProductIdDep,
    session: SessionDep,
) -> ProductOut:
    """CSRF → 路径 ID → 请求体 → 保存。都与当前值相同时不加版本、不写审计。"""
    try:
        body = ProductSaveIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    account_id = admin.account.id
    try:
        saved = save_product(
            session, account_id, product_id, body.version, _product_input(body), _utcnow()
        )
    except (ProductInvalid, ProductChanged) as exc:
        raise _rejected(session, exc) from None
    return _product_out(saved)

"""后台商品图片的上传、调序与移除接口：POST /api/admin/products/{product_id}/images、
PUT /api/admin/products/{product_id}/images、
DELETE /api/admin/products/{product_id}/images/{image_id}。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条「上传」的检查顺序；
docs/UX.md 0.17 的 A04 图片部分（上传、排序与移除各自立即生效，成功后显示服务端返回的图片
列表，被拒时按服务端的回答显示）；docs/HANDOFF.md 0.79 的落实写法（上传路径与反向代理放宽到
6m 的 ^/api/admin/products/[0-9]+/images$ 一致；错误的 detail 与 UX-COPY 0.18 的键同名；5 MB 指
5,242,880 字节；三个接口成功都回答 images）。规则见 SHOP-TASK-189 的
app/services/admin_product_images.py、SHOP-TASK-188 的 image_processing 与 SHOP-TASK-187 的
image_store；本文件只做请求格式、会话与 CSRF 的检查。

上传（POST），每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. require_admin（401 admin_session_required）；
2. X-CSRF-Token（403 csrf_failed）；
3. 路径 ID（照 app/api/admin_products.py 的 _product_id，不合法 422）；
4. load_product_images 为 None 即 404 product_not_found；
5. 存储未配置即 503 image_store_unavailable，不读请求体；
6. Content-Type 去掉分号后的参数与首尾空白、转小写后不是 image/jpeg、image/png、image/webp
   之一即 415 image_type_unsupported（只用来做这项检查，格式以 process_image 按内容判断的为准）；
7. 请求头声明的长度超过 5,242,880 字节即 413 image_too_large、不读请求体；未声明或声明不超过时
   由最后声明的异步依赖用 request.stream() 逐块读取，读到超过即停止并 413（不用多部分表单）；
8. 已有 8 张即 409 images_full；
9. process_image 抛 ImageRejected 即 422 image_invalid；
10. add_image：ImagesFull 409 images_full、ProductMissing 404 product_not_found、
    ImageStoreUnavailable 503 image_store_unavailable。成功 201 与 images。
调序（PUT）：请求体照 _write_body 逐块读取、上限 8 KB（413）、不是 JSON 415，先于会话检查；之后
require_admin（401）、CSRF（403）、路径 ID（422）、请求体结构（strict 的 image_ids 整数数组，
422 不回显）；reorder_images：ProductMissing 404 product_not_found、ImagesChanged 409
images_changed。成功 200 与 images。
移除（DELETE）：require_admin（401）、CSRF（403）、商品 ID 与图片 ID（不合法 422）；
remove_image：ProductMissing 404、ImagesChanged 409 images_changed；存储未配置时照常移除（服务层
只记日志）。成功 200 与 images。

处理函数都是同步函数，FastAPI 在线程池里执行，图片处理与 R2 调用不阻塞事件循环；只有读请求体
的依赖是异步的。三个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，
由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
不写日志（服务层与存储模块自己的日志除外）。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError

from app.api.admin_auth import AdminDep
from app.api.admin_products import (
    _PRODUCT_ID,
    MAX_PRODUCT_ID,
    CsrfDep,
    ImageOut,
    ProductIdDep,
    SessionDep,
)
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _body_errors, _json_body, _utcnow
from app.services.admin_product_images import (
    MAX_IMAGES,
    ImagesChanged,
    ImagesFull,
    ProductMissing,
    add_image,
    load_product_images,
    remove_image,
    reorder_images,
)
from app.services.admin_products import AdminProductImage
from app.services.image_processing import ImageRejected, process_image
from app.services.image_store import ImageStoreUnavailable, image_store_configured

router = APIRouter(prefix="/api/admin/products", tags=["admin"], route_class=_NoStoreRoute)

# HANDOFF 0.79：5 MB 指 5,242,880 字节。
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 第 13 条「上传」：Content-Type 为其中之一（去掉参数、首尾空白并转小写后比较）。
IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def _reject(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _not_found() -> HTTPException:
    return _reject(404, "product_not_found")


def _store_unavailable() -> HTTPException:
    return _reject(503, "image_store_unavailable")


def _images_full() -> HTTPException:
    return _reject(409, "images_full")


def _images_changed() -> HTTPException:
    return _reject(409, "images_changed")


def _too_large() -> HTTPException:
    return _reject(413, "image_too_large")


# ---------------------------------------------------------------------------
# 请求体与响应
# ---------------------------------------------------------------------------


class ReorderIn(BaseModel):
    """调序的请求体：该商品现有全部图片 ID 的新顺序（是否恰好是这些 ID 交给服务层）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    image_ids: list[int]


class ImagesOut(BaseModel):
    # load_product_images 的列表：按排列序号，第一张为封面。
    images: list[ImageOut]


def _images_out(images: list[AdminProductImage]) -> ImagesOut:
    return ImagesOut(images=[ImageOut(**asdict(image)) for image in images])


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


def _image_id(image_id: str) -> int:
    """路径里的图片 ID；写法同 _product_id，不合法时 422，不回显所给的值。"""
    if not _PRODUCT_ID.fullmatch(image_id) or int(image_id) > MAX_PRODUCT_ID:
        msg = "Image ID should be a positive integer"
        raise RequestValidationError([_error(("path", "image_id"), "image_id_invalid", msg)])
    return int(image_id)


def _is_image_type(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() in IMAGE_CONTENT_TYPES


def _declared_too_large(content_length: str | None) -> bool:
    """请求头声明的长度超过上限。声明的不是十进制整数时按未声明处理，由逐块读取把关。"""
    if content_length is None:
        return False
    value = content_length.strip()
    return value.isdecimal() and value.isascii() and int(value) > MAX_IMAGE_BYTES


def _upload_target(
    request: Request, product_id: ProductIdDep, session: SessionDep
) -> list[AdminProductImage]:
    """读请求体之前的检查：商品存在（404）→ 存储已配置（503）→ Content-Type（415）→
    声明的长度（413）。返回该商品现有的图片。"""
    images = load_product_images(session, product_id)
    if images is None:
        raise _not_found()
    if not image_store_configured(request.app.state.settings):
        raise _store_unavailable()
    if not _is_image_type(request.headers.get("content-type")):
        raise _reject(415, "image_type_unsupported")
    if _declared_too_large(request.headers.get("content-length")):
        raise _too_large()
    return images


async def _image_body(request: Request) -> bytes:
    """逐块读图片文件本身，读到超过 5,242,880 字节即停下 413，不看声明的长度是否如实。"""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


# 上传：先声明会话与 CSRF 依赖、再声明路径 ID 与读请求体之前的检查，请求体依赖最后声明。
UploadTargetDep = Annotated[list[AdminProductImage], Depends(_upload_target)]
ImageBodyDep = Annotated[bytes, Depends(_image_body)]
# 调序：请求体依赖写在会话依赖之前，413 与 415 先于其他一切回答（上限同 app/api/pay.py 的 8 KB）。
ReorderBodyDep = Annotated[bytes, Depends(_json_body)]
ImageIdDep = Annotated[int, Depends(_image_id)]


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.post("/{product_id}/images", status_code=201, response_model=ImagesOut)
def upload(
    admin: AdminDep,
    _csrf: CsrfDep,
    product_id: ProductIdDep,
    current: UploadTargetDep,
    data: ImageBodyDep,
    session: SessionDep,
    request: Request,
) -> ImagesOut:
    """图片已满 → 按内容校验与处理 → 写入存储并入库（服务层提交并写一条审计）。"""
    if len(current) >= MAX_IMAGES:
        raise _images_full()
    try:
        image = process_image(data)
    except ImageRejected:
        raise _reject(422, "image_invalid") from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    settings = request.app.state.settings
    try:
        images = add_image(session, settings, account_id, product_id, image, _utcnow())
    except ImagesFull:
        raise _images_full() from None
    except ProductMissing:
        raise _not_found() from None
    except ImageStoreUnavailable:
        raise _store_unavailable() from None
    return _images_out(images)


@router.put("/{product_id}/images", response_model=ImagesOut)
def reorder(
    raw: ReorderBodyDep,
    admin: AdminDep,
    _csrf: CsrfDep,
    product_id: ProductIdDep,
    session: SessionDep,
) -> ImagesOut:
    """CSRF → 路径 ID → 请求体 → 调序。顺序未变时不写库、不写审计，同样 200。"""
    try:
        body = ReorderIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    account_id = admin.account.id
    try:
        images = reorder_images(session, account_id, product_id, body.image_ids, _utcnow())
    except ProductMissing:
        raise _not_found() from None
    except ImagesChanged:
        raise _images_changed() from None
    return _images_out(images)


@router.delete("/{product_id}/images/{image_id}", response_model=ImagesOut)
def remove(
    admin: AdminDep,
    _csrf: CsrfDep,
    product_id: ProductIdDep,
    image_id: ImageIdDep,
    session: SessionDep,
    request: Request,
) -> ImagesOut:
    """CSRF → 两个路径 ID → 移除。存储未配置时照常删行，服务层只记日志。"""
    account_id = admin.account.id
    settings = request.app.state.settings
    try:
        images = remove_image(session, settings, account_id, product_id, image_id, _utcnow())
    except ProductMissing:
        raise _not_found() from None
    except ImagesChanged:
        raise _images_changed() from None
    return _images_out(images)

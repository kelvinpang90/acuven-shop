"""后台店铺标志图的上传与移除接口：PUT /api/admin/store-design/logo、
DELETE /api/admin/store-design/logo。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条「上传」的检查顺序与
「移除」；docs/UX.md 0.18 的 A08 标志段（上传即替换、移除即恢复文字品牌，都立即生效，被拒时按
服务端的回答显示）；docs/HANDOFF.md 0.84 的落实写法（上传路径与反向代理放宽到 6m 的
^/api/admin/(products/[0-9]+/images|store-design/logo)$ 一致；错误的 detail 与
UX-COPY 的键同名，另有 503 image_store_unavailable；5 MB 指 5,242,880 字节；两个接口成功
都是 200，响应体只有 logo）。规则见 SHOP-TASK-211 的 app/services/store_logo.py 与
SHOP-TASK-210 的 image_processing；本文件只做请求格式、会话与 CSRF 的检查。

上传（PUT），每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. require_admin（401 admin_session_required）；
2. X-CSRF-Token（403 csrf_failed）；
3. 存储未配置即 503 image_store_unavailable，不读请求体；
4. Content-Type 去掉分号后的参数与首尾空白、转小写后不是 image/jpeg、image/png、image/webp
   之一即 415 image_type_unsupported（沿用 admin_product_images 的 _is_image_type）；
5. 请求头声明的长度超过 5,242,880 字节即 413 image_too_large、不读请求体
   （_declared_too_large）；未声明或声明不超过时由最后声明的异步依赖逐块读取，读到超过即
   停止并 413（_image_body）；
6. process_image 以 LOGO_MIN_SIDE 与 LOGO_MIN_LONG_SIDE 处理，抛 ImageRejected 即 422
   logo_invalid；
7. replace_logo 抛 ImageStoreUnavailable 即 503 image_store_unavailable。
成功 200 与 logo（新的提供路径）。没有张数上限这一项。
移除（DELETE）：require_admin（401）、CSRF（403），不读请求体；remove_logo（没有标志时
照样成功；存储未配置时照常清空，服务层只记日志）。成功 200 与 logo 为 null。

处理函数都是同步函数，FastAPI 在线程池里执行，图片处理与 R2 调用不阻塞事件循环；只有读请求体
的依赖是异步的。两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，
由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
不写日志（服务层与存储模块自己的日志除外）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin_auth import AdminDep
from app.api.admin_product_images import _declared_too_large, _image_body, _is_image_type
from app.api.admin_products import CsrfDep, SessionDep
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _utcnow
from app.services.image_processing import (
    LOGO_MIN_LONG_SIDE,
    LOGO_MIN_SIDE,
    ImageRejected,
    process_image,
)
from app.services.image_store import ImageStoreUnavailable, image_store_configured
from app.services.store_logo import remove_logo, replace_logo

router = APIRouter(prefix="/api/admin/store-design/logo", tags=["admin"], route_class=_NoStoreRoute)


def _reject(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _store_unavailable() -> HTTPException:
    return _reject(503, "image_store_unavailable")


class LogoOut(BaseModel):
    # 标志的提供路径 /api/store-logos/<文件名>.webp；移除后为空。
    logo: str | None


def _upload_gate(request: Request) -> None:
    """读请求体之前的检查：存储已配置（503）→ Content-Type（415）→ 声明的长度（413）。"""
    if not image_store_configured(request.app.state.settings):
        raise _store_unavailable()
    if not _is_image_type(request.headers.get("content-type")):
        raise _reject(415, "image_type_unsupported")
    if _declared_too_large(request.headers.get("content-length")):
        raise _reject(413, "image_too_large")


# 上传：先声明会话与 CSRF 依赖、再声明读请求体之前的检查，请求体依赖最后声明。
UploadGateDep = Annotated[None, Depends(_upload_gate)]
ImageBodyDep = Annotated[bytes, Depends(_image_body)]


@router.put("", response_model=LogoOut)
def upload(
    admin: AdminDep,
    _csrf: CsrfDep,
    _gate: UploadGateDep,
    data: ImageBodyDep,
    session: SessionDep,
    request: Request,
) -> LogoOut:
    """按内容校验与处理 → 写入存储并记为标志（服务层提交、写一条审计并删除原有对象）。"""
    try:
        image = process_image(data, min_side=LOGO_MIN_SIDE, min_long_side=LOGO_MIN_LONG_SIDE)
    except ImageRejected:
        raise _reject(422, "logo_invalid") from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    settings = request.app.state.settings
    try:
        logo = replace_logo(session, settings, account_id, image, _utcnow())
    except ImageStoreUnavailable:
        raise _store_unavailable() from None
    return LogoOut(logo=logo)


@router.delete("", response_model=LogoOut)
def remove(
    admin: AdminDep,
    _csrf: CsrfDep,
    session: SessionDep,
    request: Request,
) -> LogoOut:
    """CSRF → 移除。没有标志时不写审计、照样 200；存储未配置时照常清空，服务层只记日志。"""
    account_id = admin.account.id
    remove_logo(session, request.app.state.settings, account_id, _utcnow())
    return LogoOut(logo=None)

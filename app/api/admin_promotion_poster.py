"""后台节日促销海报图的上传与移除接口：PUT /api/admin/promotions/{promotion_id}/poster/{slot}、
DELETE /api/admin/promotions/{promotion_id}/poster/{slot}。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 12 条（海报图沿用「计价、优惠、积分与
库存」第 13 条的格式、文件大小与存储规则，桌面图宽至少 1,200、手机图宽至少 600，上传即替换该位置
的图，存放与替换的顺序照同一节第 15 条）；SHOP-TASK-250 的落实写法。请求体读取、415、413、503 与
no-store 照 app/api/admin_store_logo.py（SHOP-TASK-212）。规则见 SHOP-TASK-240 的
app/services/promotion_poster.py 与 SHOP-TASK-210 的 image_processing；本文件只做请求格式、会话、
CSRF 与活动状态的检查。slot 为 desktop 或 phone。

两个方法共同的检查，每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. require_admin（401 admin_session_required）；
2. X-CSRF-Token（403 csrf_failed）；
3. 路径里的活动 ID 照 app/api/admin_categories.py 的 _category_id 校验，不合法 422；slot 不是
   desktop 或 phone 也是 422（只给位置、类型与固定消息，不回显所给的值）；
4. 以普通读取（不加锁）看活动：不存在 404 promotion_not_found，ended 409 promotion_ended
   （状态用 admin_promotions.promotion_state，时刻取本模块的 _utcnow）。读完即结束只读事务。
上传（PUT）接着：
5. 存储未配置即 503 image_store_unavailable，不读请求体；
6. Content-Type 不是 image/jpeg、image/png、image/webp 之一即 415 image_type_unsupported；
7. 请求头声明的长度超过 5,242,880 字节即 413 image_too_large、不读请求体；未声明或声明不超过时
   由最后声明的异步依赖逐块读取，读到超过即停止并 413；
8. process_image 以 POSTER_MIN_WIDTHS[slot] 为 min_width 处理，抛 ImageRejected 即 422
   poster_invalid；
9. replace_poster（锁内再核一次活动：不存在 404、ended 409），抛 ImageStoreUnavailable 即 503。
成功 200 与 poster（新的提供路径 /api/promotion-posters/<文件名>.webp）。
移除（DELETE）没有请求体、不读请求体、不查存储配置：remove_poster（锁内再核一次：不存在 404、
ended 409；已发布且海报为上传图模式 409 poster_required；该位置没有图时照样成功；存储未配置时
照常清空并提交，只由存储模块记日志）。成功 200 与 poster 为 null。

处理函数都是同步函数，FastAPI 在线程池里执行，图片处理与 R2 调用不阻塞事件循环；只有读请求体
的依赖是异步的。两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，
由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
不写日志（服务层与存储模块自己的日志除外）。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import select

from app.api.admin_auth import AdminDep
from app.api.admin_product_images import _declared_too_large, _image_body, _is_image_type
from app.api.admin_products import CsrfDep, SessionDep
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _error
from app.api.pay import _utcnow
from app.models import Promotion
from app.services.admin_promotions import (
    STATE_ENDED,
    PromotionEnded,
    PromotionNotFound,
    promotion_state,
)
from app.services.image_processing import ImageRejected, process_image
from app.services.image_store import ImageStoreUnavailable, image_store_configured
from app.services.promotion_poster import (
    POSTER_MIN_WIDTHS,
    POSTER_SLOTS,
    PosterRequired,
    remove_poster,
    replace_poster,
)

router = APIRouter(
    prefix="/api/admin/promotions/{promotion_id}/poster",
    tags=["admin"],
    route_class=_NoStoreRoute,
)

# 活动 ID：不带符号与前导零的十进制整数，不超过 INT 列的上限（同分类 ID）。
_PROMOTION_ID = re.compile(r"[1-9][0-9]{0,9}")
MAX_PROMOTION_ID = 2**31 - 1


def _reject(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _store_unavailable() -> HTTPException:
    return _reject(503, "image_store_unavailable")


def _not_found() -> HTTPException:
    return _reject(404, "promotion_not_found")


def _ended() -> HTTPException:
    return _reject(409, "promotion_ended")


class PosterOut(BaseModel):
    # 该位置海报图的提供路径 /api/promotion-posters/<文件名>.webp；移除后为空。
    poster: str | None


def _promotion_id(promotion_id: str) -> int:
    """路径里的活动 ID；不合法时 422，只给固定消息，不回显所给的值。"""
    if not _PROMOTION_ID.fullmatch(promotion_id) or int(promotion_id) > MAX_PROMOTION_ID:
        msg = "Promotion ID should be a positive integer"
        loc = ("path", "promotion_id")
        raise RequestValidationError([_error(loc, "promotion_id_invalid", msg)])
    return int(promotion_id)


def _slot(slot: str) -> str:
    """路径里的海报位置；不是 desktop 或 phone 时 422，不回显所给的值。"""
    if slot not in POSTER_SLOTS:
        msg = "Slot should be desktop or phone"
        raise RequestValidationError([_error(("path", "slot"), "slot_invalid", msg)])
    return slot


PromotionIdDep = Annotated[int, Depends(_promotion_id)]
SlotDep = Annotated[str, Depends(_slot)]


def _open_promotion(promotion_id: PromotionIdDep, session: SessionDep) -> int:
    """普通读取（不加锁）：不存在 404，ended 409。读完结束只读事务，读请求体期间不占着事务。"""
    row = session.execute(
        select(
            Promotion.status,
            Promotion.starts_at,
            Promotion.ends_at,
            Promotion.ended_early_at,
        ).where(Promotion.id == promotion_id)
    ).one_or_none()
    session.rollback()
    if row is None:
        raise _not_found()
    status, starts_at, ends_at, ended_early_at = row
    if promotion_state(status, starts_at, ended_early_at or ends_at, _utcnow()) == STATE_ENDED:
        raise _ended()
    return promotion_id


def _upload_gate(request: Request) -> None:
    """读请求体之前的检查：存储已配置（503）→ Content-Type（415）→ 声明的长度（413）。"""
    if not image_store_configured(request.app.state.settings):
        raise _store_unavailable()
    if not _is_image_type(request.headers.get("content-type")):
        raise _reject(415, "image_type_unsupported")
    if _declared_too_large(request.headers.get("content-length")):
        raise _reject(413, "image_too_large")


# 先声明会话与 CSRF 依赖、再声明路径与活动的检查；上传另声明读请求体之前的检查，请求体依赖最后声明。
OpenPromotionDep = Annotated[int, Depends(_open_promotion)]
UploadGateDep = Annotated[None, Depends(_upload_gate)]
ImageBodyDep = Annotated[bytes, Depends(_image_body)]


@router.put("/{slot}", response_model=PosterOut)
def upload(
    admin: AdminDep,
    _csrf: CsrfDep,
    _checked_id: PromotionIdDep,
    slot: SlotDep,
    promotion_id: OpenPromotionDep,
    _gate: UploadGateDep,
    data: ImageBodyDep,
    session: SessionDep,
    request: Request,
) -> PosterOut:
    """按内容校验与处理 → 写入存储并记为该位置的海报图（服务层提交、写一条审计并删除原有对象）。"""
    try:
        image = process_image(data, min_width=POSTER_MIN_WIDTHS[slot])
    except ImageRejected:
        raise _reject(422, "poster_invalid") from None

    # 服务层先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    settings = request.app.state.settings
    try:
        poster = replace_poster(session, settings, account_id, promotion_id, slot, image, _utcnow())
    except ImageStoreUnavailable:
        raise _store_unavailable() from None
    except PromotionNotFound:
        raise _not_found() from None
    except PromotionEnded:
        raise _ended() from None
    return PosterOut(poster=poster)


@router.delete("/{slot}", response_model=PosterOut)
def remove(
    admin: AdminDep,
    _csrf: CsrfDep,
    _checked_id: PromotionIdDep,
    slot: SlotDep,
    promotion_id: OpenPromotionDep,
    session: SessionDep,
    request: Request,
) -> PosterOut:
    """移除。该位置没有图时不写审计、照样 200；存储未配置时照常清空，只由存储模块记日志。"""
    account_id = admin.account.id
    try:
        remove_poster(
            session, request.app.state.settings, account_id, promotion_id, slot, _utcnow()
        )
    except PromotionNotFound:
        raise _not_found() from None
    except PromotionEnded:
        raise _ended() from None
    except PosterRequired:
        raise _reject(409, "poster_required") from None
    return PosterOut(poster=None)

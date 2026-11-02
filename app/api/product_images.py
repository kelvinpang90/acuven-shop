"""商品展示图的提供接口：GET /api/product-images/<文件名>.webp。

依据 docs/DESIGN.md 1.19（提交 6e8f655）「计价、优惠、积分与库存」第 13 条「提供」一段，以及
docs/HANDOFF.md 0.79 的落实写法：路由取整段文件名（含扩展名），格式由处理函数校验，其他扩展名
也回答带 no-store 的 image_not_found；错误为 404 image_not_found 与 503 image_store_unavailable，
带 no-store；成功的响应长期缓存，所以不用 no-store 路由类，错误响应自行加上 no-store。存储为
SHOP-TASK-187 的 image_store，经模块属性调用（测试替换模块属性）。

不要求会话，前台与后台共用。每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. 路径最后一段不恰好是 32 位小写十六进制加 .webp 即 404 image_not_found；
2. 存储未配置即 503 image_store_unavailable；
3. 以去掉 .webp 的 32 位作文件名，查不到 ProductImage 的行即 404，不访问 R2；
4. get_image 抛 ImageNotFound 即 404，抛 ImageStoreUnavailable 即 503。
前两步在会话依赖之前完成，不查库。错误响应不含 R2 的错误内容。成功 200，响应体为展示图的
字节；不查商品是否上架。

处理函数是同步函数，FastAPI 在线程池里执行，R2 读取不阻塞事件循环。不写日志（存储模块自己的
日志除外）。
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models import ProductImage
from app.services import image_store

router = APIRouter(prefix="/api/product-images", tags=["catalog"])

# 第 13 条「存放」：文件名为 32 位小写十六进制，展示图为 <文件名>.webp。
_IMAGE_NAME = re.compile(r"([0-9a-f]{32})\.webp")

NO_STORE = {"Cache-Control": "no-store"}
# 第 13 条「提供」：同一文件名的内容永不改变，可长期缓存（public、一年、immutable）。
IMAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _reject(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail, headers=NO_STORE)


def _not_found() -> HTTPException:
    return _reject(404, "image_not_found")


def _store_unavailable() -> HTTPException:
    return _reject(503, "image_store_unavailable")


def _file_name(name: str, request: Request) -> str:
    """查库之前的检查：文件名格式（404）→ 存储已配置（503）。返回去掉 .webp 的 32 位。"""
    match = _IMAGE_NAME.fullmatch(name)
    if match is None:
        raise _not_found()
    if not image_store.image_store_configured(request.app.state.settings):
        raise _store_unavailable()
    return match.group(1)


# 文件名依赖先于会话依赖声明：格式不对或存储未配置时不查库。
FileNameDep = Annotated[str, Depends(_file_name)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/{name}")
def product_image(file_name: FileNameDep, session: SessionDep, request: Request) -> Response:
    """查到 ProductImage 的行才从存储桶读取；成功时回答展示图与长期缓存的响应头。"""
    stmt = select(ProductImage.id).where(ProductImage.file_name == file_name)
    if session.scalar(stmt) is None:
        raise _not_found()
    # 读取之前结束只读事务，R2 读取期间不占着数据库连接上的事务。
    session.rollback()
    try:
        data = image_store.get_image(request.app.state.settings, file_name)
    except image_store.ImageNotFound:
        raise _not_found() from None
    except image_store.ImageStoreUnavailable:
        raise _store_unavailable() from None
    return Response(content=data, media_type=image_store.CONTENT_TYPE, headers=IMAGE_HEADERS)

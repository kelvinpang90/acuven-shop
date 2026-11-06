"""店铺标志图的提供接口：GET /api/store-logos/<文件名>.webp。

依据 docs/DESIGN.md 1.20（提交 a870f78）「计价、优惠、积分与库存」第 15 条「提供」一段，以及
docs/HANDOFF.md 0.84 的落实写法：文件名格式、错误的 detail、no-store 与成功时的响应头照
app/api/product_images.py（沿用它的 _IMAGE_NAME、IMAGE_HEADERS 与两个错误，从该模块导入，不改它）；
路由取整段文件名（含扩展名），格式由处理函数校验；成功的响应长期缓存，所以不用 no-store 路由类，
错误响应自行加上 no-store。存储为 SHOP-TASK-210 的 image_store，经模块属性调用（测试替换模块
属性），读取传 prefix=image_store.LOGO_KEY_PREFIX。

不要求会话，前台与后台共用。每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. 路径最后一段不恰好是 32 位小写十六进制加 .webp 即 404 image_not_found；
2. 存储未配置即 503 image_store_unavailable；
3. 去掉 .webp 的 32 位不是单例行当前记录的标志（SHOP-TASK-211 的 current_logo_file_name；含没有
   标志、已被替换或移除的）即 404，不访问 R2；
4. get_image 抛 ImageNotFound 即 404，抛 ImageStoreUnavailable 即 503。
前两步在会话依赖之前完成，不查库。错误响应不含 R2 的错误内容。成功 200，响应体为展示图的字节。

处理函数是同步函数，FastAPI 在线程池里执行，R2 读取不阻塞事件循环。不写日志（存储模块自己的
日志除外）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.product_images import _IMAGE_NAME, IMAGE_HEADERS, _not_found, _store_unavailable
from app.db.session import get_session
from app.services import image_store
from app.services.store_logo import current_logo_file_name

router = APIRouter(prefix="/api/store-logos", tags=["store-design"])


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
def store_logo(file_name: FileNameDep, session: SessionDep, request: Request) -> Response:
    """是单例行当前记录的标志才从存储桶读取；成功时回答展示图与长期缓存的响应头。"""
    if current_logo_file_name(session) != file_name:
        raise _not_found()
    # 读取之前结束只读事务，R2 读取期间不占着数据库连接上的事务。
    session.rollback()
    try:
        data = image_store.get_image(
            request.app.state.settings, file_name, prefix=image_store.LOGO_KEY_PREFIX
        )
    except image_store.ImageNotFound:
        raise _not_found() from None
    except image_store.ImageStoreUnavailable:
        raise _store_unavailable() from None
    return Response(content=data, media_type=image_store.CONTENT_TYPE, headers=IMAGE_HEADERS)

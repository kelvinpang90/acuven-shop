"""节日促销海报图的提供接口：GET /api/promotion-posters/<文件名>.webp。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 12 条（海报图沿用「计价、优惠、积分与
库存」第 13 条的格式、文件大小与存储规则，另用自己的存储键前缀）与 SHOP-TASK-250 的落实写法：
文件名格式、错误的 detail、响应头与缓存照 app/api/store_logos.py（沿用 app/api/product_images.py
的 _IMAGE_NAME、IMAGE_HEADERS 与两个错误，从该模块导入，不改它）；路由取整段文件名（含扩展名），
格式由处理函数校验；成功的响应长期缓存，所以不用 no-store 路由类，错误响应自行加上 no-store。
存储为 SHOP-TASK-210 的 image_store，经模块属性调用（测试替换模块属性），读取传
prefix=image_store.POSTER_KEY_PREFIX。

不要求会话，前台与后台共用。每一步不通过即回答、不再往下（检查用依赖按声明顺序完成）：
1. 路径最后一段不恰好是 32 位小写十六进制加 .webp 即 404 image_not_found；
2. 存储未配置即 503 image_store_unavailable；
3. 去掉 .webp 的 32 位不在 SHOP-TASK-240 的 poster_file_names 里（不被任何活动的装饰行引用，含
   已被替换或移除的）即 404，不访问 R2；草稿活动的海报图也提供，供管理员预览；
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
from app.services.promotion_poster import poster_file_names

router = APIRouter(prefix="/api/promotion-posters", tags=["promotions"])


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
def promotion_poster(file_name: FileNameDep, session: SessionDep, request: Request) -> Response:
    """被某个活动的装饰行引用才从存储桶读取；成功时回答展示图与长期缓存的响应头。"""
    if file_name not in poster_file_names(session):
        raise _not_found()
    # 读取之前结束只读事务，R2 读取期间不占着数据库连接上的事务。
    session.rollback()
    try:
        data = image_store.get_image(
            request.app.state.settings, file_name, prefix=image_store.POSTER_KEY_PREFIX
        )
    except image_store.ImageNotFound:
        raise _not_found() from None
    except image_store.ImageStoreUnavailable:
        raise _store_unavailable() from None
    return Response(content=data, media_type=image_store.CONTENT_TYPE, headers=IMAGE_HEADERS)

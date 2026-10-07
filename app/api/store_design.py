"""店铺装修设置的公开只读接口：只有 GET /api/store-design，不需登录、只发 SELECT。

依据 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01 以及 docs/HANDOFF.md 0.34
记录的 Kelvin 2026-10-06 决定，读取规则见 app/services/store_design.py。返回主题、主色（为空时
null，即该主题的默认主色）、按位置排序的四个首页区块与按位置排序且前台目录可见的精选商品 slug；
不含标志图（Kelvin 2026-10-06：存储与公开读取首版不含标志图）。

处理函数与依赖产生的全部响应（含未配置数据库时 get_session 的 503）都带 Cache-Control: no-store，
由 app/api/order_lookup.py 的路由类统一加上，装修保存后访客的下一次请求即读到新设置；路径存在
但方法不匹配的 405 由框架在路由之外回答，不在此列。不读写会话或 cookie、不要求 CSRF、不写日志。
不加修改接口：修改与审计记录留给管理后台业务接口任务。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.order_lookup import _NoStoreRoute
from app.db.session import get_session
from app.services.store_design import read_store_design

router = APIRouter(prefix="/api/store-design", tags=["store-design"], route_class=_NoStoreRoute)

SessionDep = Annotated[Session, Depends(get_session)]


class HomeBlockOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block: str
    visible: bool


class StoreDesignOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str
    accent: str | None
    home_blocks: list[HomeBlockOut]
    featured_slugs: list[str]


@router.get("", response_model=StoreDesignOut)
def store_design(session: SessionDep) -> StoreDesignOut:
    design = read_store_design(session)
    return StoreDesignOut(
        theme=design.theme,
        accent=design.accent,
        home_blocks=[
            HomeBlockOut(block=block.block, visible=block.visible) for block in design.home_blocks
        ],
        featured_slugs=design.featured_slugs,
    )

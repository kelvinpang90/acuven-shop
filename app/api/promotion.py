"""节日促销活动的公开展示接口：只有 GET /api/promotion?lang=，不需登录、只发 SELECT。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 10、12 条：按当前时刻给出生效活动
的展示数据（app/services/promotion_display.py）与服务端当前时间，前台倒计时以实际结束时间与
服务端当前时间之差为准。先以 SHOP-TASK-233 的 active_promotion(db, _utcnow()) 取生效活动的
ID，再以同一 ID 调用 promotion_display；没有生效活动时 promotion 为 null。每次请求从数据库读、
不缓存；不接受任何预览参数（第 11 条）。

lang 同 app/api/pay.py 的 LanguageDep：只接受 en、zh、ms，默认 en，不合法时 422 不回显所给的值。
处理函数与依赖产生的全部响应（含 422 与未配置数据库时的 503）都带 Cache-Control: no-store，
由 app/api/order_lookup.py 的路由类统一加上（第 10 条「展示接口的响应带 no-store」）。
时间带 UTC 时区输出。不读写会话或 cookie、不要求 CSRF、不写日志。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.order_lookup import _NoStoreRoute
from app.api.pay import LanguageDep
from app.db.session import get_session
from app.services.promotion_display import PromotionDisplay, promotion_display
from app.services.promotions import active_promotion

router = APIRouter(prefix="/api/promotion", tags=["promotion"], route_class=_NoStoreRoute)

SessionDep = Annotated[Session, Depends(get_session)]


class PromotionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 生效活动的展示数据；没有生效活动时为空。
    promotion: PromotionDisplay | None
    # 服务端当前时间（UTC），即判断生效所用的时刻。
    server_time: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.get("", response_model=PromotionOut)
def promotion(lang: LanguageDep, session: SessionDep) -> PromotionOut:
    now = _utcnow()
    active = active_promotion(session, now)
    display = None if active is None else promotion_display(session, active.promotion_id, lang)
    # promotion_display 已结束读事务；没有生效活动时在这里结束。
    session.rollback()
    return PromotionOut(promotion=display, server_time=now.replace(tzinfo=UTC))

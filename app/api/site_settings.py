"""站点设置的公开只读接口：只有 GET /api/site-settings，不需登录、不读写 cookie、只发 SELECT。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条与「数据模型」的 SiteSetting 一行。
只返回 sms_verification_enabled，供前台决定显示哪一组短信相关界面；服务端的判定不依赖它，
下单、发送短信、提交验证码与确认注销都在当次请求里调用 app/services/site_settings.py 重新读取。
响应带 Cache-Control: no-store，开关修改后浏览器与中间代理不会继续给出旧值。
不加修改开关的接口：修改留给管理后台业务接口任务（管理员登录与审计记录就绪后）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.site_settings import is_sms_verification_enabled

router = APIRouter(prefix="/api/site-settings", tags=["site-settings"])

SessionDep = Annotated[Session, Depends(get_session)]


class SiteSettingsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sms_verification_enabled: bool


@router.get("", response_model=SiteSettingsOut)
def site_settings(session: SessionDep, response: Response) -> SiteSettingsOut:
    response.headers["Cache-Control"] = "no-store"
    return SiteSettingsOut(sms_verification_enabled=is_sms_verification_enabled(session))

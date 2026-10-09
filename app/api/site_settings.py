"""站点设置的公开只读接口：只有 GET /api/site-settings，不需登录、不读写 cookie、只发 SELECT。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 4 条与「数据模型」的 SiteSetting 一行。
返回两个字段，供前台决定显示哪些界面：
- sms_verification_enabled：短信验证开关，决定显示哪一组短信相关界面；服务端的判定不依赖它，
  下单、发送短信、提交验证码与确认注销都在当次请求里调用 app/services/site_settings.py 重新读取。
- whatsapp_contact_url：WhatsApp 联系链接，取自配置 SHOP_WHATSAPP_CONTACT_URL（DESIGN 1.11
  「上线依赖与设计闸门」：配置 WhatsApp 联系方式，仓库只记录变量名）。去掉首尾空白后协议为 https、
  主机部分非空、不含空白与控制字符、不超过 512 个字符时原样返回，否则（含未配置）为 null，
  前台据此隐藏联系入口（docs/UX.md Q10：配置缺失时隐藏）。配置不合格时不报错、不写日志。
响应带 Cache-Control: no-store，开关修改后浏览器与中间代理不会继续给出旧值。
不加修改开关的接口：修改留给管理后台业务接口任务（管理员登录与审计记录就绪后）。
"""

from __future__ import annotations

import unicodedata
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.site_settings import is_sms_verification_enabled

router = APIRouter(prefix="/api/site-settings", tags=["site-settings"])

SessionDep = Annotated[Session, Depends(get_session)]

WHATSAPP_CONTACT_URL_MAX_LENGTH = 512


class SiteSettingsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sms_verification_enabled: bool
    whatsapp_contact_url: str | None


def whatsapp_contact_url(configured: str) -> str | None:
    """配置值合格时返回去掉首尾空白后的链接，否则返回 None。"""
    url = configured.strip()
    if not url or len(url) > WHATSAPP_CONTACT_URL_MAX_LENGTH:
        return None
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in url):
        return None
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not hostname:
        return None
    return url


@router.get("", response_model=SiteSettingsOut)
def site_settings(session: SessionDep, request: Request, response: Response) -> SiteSettingsOut:
    response.headers["Cache-Control"] = "no-store"
    return SiteSettingsOut(
        sms_verification_enabled=is_sms_verification_enabled(session),
        whatsapp_contact_url=whatsapp_contact_url(request.app.state.settings.whatsapp_contact_url),
    )

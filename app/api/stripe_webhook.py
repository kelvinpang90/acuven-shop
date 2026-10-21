"""Stripe webhook 接口：POST /api/stripe/webhook。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 7 条（「用原始请求体与 webhook
签名密钥校验 Stripe-Signature（含时间容差）」，不通过回 400、不处理；已提交过的事件直接回 200；
处理失败整个事务回滚、回 5xx，由 Stripe 重发；其他类型回 200 不处理；「不存、不记录事件原文」）、
第 1 条（「webhook 事件……的 livemode 为真即拒绝」）与第 2 条（签名密钥未配置时不启用 Stripe），
以及 docs/HANDOFF.md 0.55 的落实写法（3）。处理见 app/services/stripe_webhook.py 的
handle_webhook_event。

检查顺序，每一步不通过即停止，之前不读写数据库、不调用 Stripe：
1. 请求体照 app/api/pay.py 的 _json_body 逐块读，超过 256 KB 即 413，不看 Content-Type。
2. stripe_configured 为假（含签名密钥未配置、密钥不是测试密钥）回 503 stripe_unavailable，不验签。
3. Stripe-Signature 校验不通过回 400 signature_invalid。
4. 事件内容不合格（parse_webhook_event 返回 None）回 400 event_invalid。
5. livemode 为真回 400 livemode_rejected，记一条 error 日志 stripe_livemode（只含类别），
   不写事件记录。
之后交给 handle_webhook_event：processed、ignored、duplicate 都回 200 {"received": true}；
unavailable 回 503 stripe_unavailable（服务层已回滚、不留事件记录，由 Stripe 重发）。

请求只凭签名校验：不读订单访问 cookie、会员或管理员会话，不要求 CSRF。处理函数与依赖产生的
全部响应（含错误）都带 no-store，由 app/api/order_lookup.py 的路由类加上；未预期的异常不在这里
捕获，由框架回 500，会话关闭时未提交的改动回滚。响应不回显任何输入，不带事件 ID 或任何
Stripe ID。除上面 livemode 一条外不写日志，不记录请求体、请求头与事件内容。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.order_lookup import _NoStoreRoute
from app.api.pay import GatewayDep, SessionDep, _stripe_unavailable, _utcnow
from app.services.stripe_gateway import (
    LOG_LIVEMODE,
    parse_webhook_event,
    stripe_configured,
    verify_webhook_signature,
)
from app.services.stripe_webhook import WebhookResult, handle_webhook_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stripe", tags=["stripe"], route_class=_NoStoreRoute)

MAX_WEBHOOK_BODY_BYTES = 256 * 1024
SIGNATURE_HEADER = "Stripe-Signature"


async def _raw_body(request: Request) -> bytes:
    """逐块读原始请求体，超过上限就停下返回 413，不看 Content-Length 是否如实，也不看
    Content-Type（签名按原始字节校验）。"""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


# 请求体依赖写在会话与适配器依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
BodyDep = Annotated[bytes, Depends(_raw_body)]


def _bad_request(code: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": code})


@router.post("/webhook")
def webhook(
    payload: BodyDep,
    session: SessionDep,
    gateway: GatewayDep,
    request: Request,
) -> JSONResponse:
    settings = request.app.state.settings
    # 第 2 条：签名密钥未配置或密钥不是测试密钥时不启用 Stripe，也不验签。
    if not stripe_configured(settings):
        return _stripe_unavailable()
    values = request.headers.getlist(SIGNATURE_HEADER)
    signature = values[0] if len(values) == 1 else None
    now = _utcnow()
    if not verify_webhook_signature(payload, signature, settings.stripe_webhook_secret, now):
        return _bad_request("signature_invalid")
    event = parse_webhook_event(payload)
    if event is None:
        return _bad_request("event_invalid")
    if event.livemode:
        logger.error(LOG_LIVEMODE)
        return _bad_request("livemode_rejected")
    result = handle_webhook_event(session, gateway, event, now)
    if result is WebhookResult.UNAVAILABLE:
        return _stripe_unavailable()
    return JSONResponse(status_code=200, content={"received": True})

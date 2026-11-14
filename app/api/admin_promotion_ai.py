"""后台促销活动 A12 的 AI 装饰提案接口：
GET /api/admin/promotion-ai/status 与 POST /api/admin/promotion-ai/proposal。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条
（接口只传递与校验字段，出站内容与计数在服务层；不返回个人资料）
与 docs/UX.md 0.20 的 A12 AI 区（未配置时 AI 按钮禁用并说明原因）；
SHOP-TASK-251 的落实写法。规则见 SHOP-TASK-248 的
app/services/promotion_ai.py；本文件只做请求格式、会话与 CSRF 的检查，
照 app/api/admin_categories.py 的写法。路径不放在 /api/admin/promotions/
下，免得被活动 ID 的路由先匹配。

两个接口都经 SHOP-TASK-036 的 require_admin：
没有有效后台会话时 401 admin_session_required。

状态（GET /status）：require_admin（401）→ {"configured": 布尔值}，
即按本应用的配置构造的 AiHubClient 是否已配置。只读，不调用模型、不计数。
提案（POST /proposal），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；
   不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 请求体有必填的 description（JSON 字符串）与 rules（列表，每项为 kind
   与 SHOP-TASK-239 RuleInput 的参数字段，没有商品与分类 ID）；
   多出字段、缺字段或类型不对 422；
5. propose_decoration。成功 200 {"proposal": DecorationProposal 的同名字段}；
   提案不保存。

服务层的拒绝（说明文字不回给调用方）：
- PromotionInvalid：422 {"code": "promotion_invalid", "problems": [...]}，
  每项 code、field、product_id、product_name（写法同 SHOP-TASK-242）；
- AiNotConfigured：503 ai_not_configured；AiDailyLimit：429 ai_daily_limit；
- AiUnavailable：503 ai_unavailable；AiResultInvalid：502 ai_result_invalid。

两个接口的处理函数与依赖产生的全部响应（含错误）都带
Cache-Control: no-store，由 SHOP-TASK-027 的路由类统一加上；
路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容
（同 app/api/pay.py 的 _body_errors）。不写日志。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _body_errors, _json_body, _utcnow
from app.core.config import Settings
from app.db.session import get_session
from app.services.admin_promotions import PromotionInvalid, RuleInput, TierInput
from app.services.ai_hub import AiHubClient
from app.services.promotion_ai import (
    AiDailyLimit,
    AiNotConfigured,
    AiResultInvalid,
    AiUnavailable,
    DecorationProposal,
    propose_decoration,
)

router = APIRouter(prefix="/api/admin/promotion-ai", tags=["admin"], route_class=_NoStoreRoute)


# ---------------------------------------------------------------------------
# 请求体：规则的字段名与 SHOP-TASK-239 的 RuleInput 相同，但没有
# product_ids 与 category_ids（第 13 条不送出商品与分类 ID，这里也不收）。
# 只校验结构与 JSON 类型（strict：整数不收布尔值、字符串与浮点数）；
# 描述的长度与规则参数的取值交给服务层。
# ---------------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TierIn(_In):
    threshold_sen: int
    amount_sen: int | None
    percent: int | None


class RuleIn(_In):
    kind: str
    discount_mode: str | None = None
    percent: int | None = None
    amount_sen: int | None = None
    buy_quantity: int | None = None
    get_quantity: int | None = None
    get_percent: int | None = None
    threshold_sen: int | None = None
    tiers: list[TierIn] = []


class ProposalIn(_In):
    description: str
    rules: list[RuleIn]


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class StatusOut(BaseModel):
    # 未配置时页面禁用 AI 按钮并说明原因（A12）。
    configured: bool


class DecorationProposalOut(BaseModel):
    """DecorationProposal 的二十个同名字段：八个装饰 ID 与四种文案各三语。"""

    pack: str
    palette: str
    ambient: str
    home_poster: str
    list_strip: str
    card_badge: str
    detail_ribbon: str
    cart_progress: str
    title_en: str
    title_zh: str | None
    title_ms: str | None
    subtitle_en: str | None
    subtitle_zh: str | None
    subtitle_ms: str | None
    badge_en: str | None
    badge_zh: str | None
    badge_ms: str | None
    strip_en: str | None
    strip_zh: str | None
    strip_ms: str | None


class ProposalOut(BaseModel):
    proposal: DecorationProposalOut


def _proposal_out(proposal: DecorationProposal) -> ProposalOut:
    return ProposalOut(proposal=DecorationProposalOut(**asdict(proposal)))


def _rule_input(rule: RuleIn) -> RuleInput:
    return RuleInput(
        kind=rule.kind,
        discount_mode=rule.discount_mode,
        percent=rule.percent,
        amount_sen=rule.amount_sen,
        buy_quantity=rule.buy_quantity,
        get_quantity=rule.get_quantity,
        get_percent=rule.get_percent,
        threshold_sen=rule.threshold_sen,
        tiers=tuple(TierInput(**tier.model_dump()) for tier in rule.tiers),
    )


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


async def _write_body(request: Request) -> bytes:
    """提案只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


def _client(settings: Settings) -> AiHubClient:
    return AiHubClient.from_settings(settings)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
WriteBodyDep = Annotated[bytes, Depends(_write_body)]
SessionDep = Annotated[Session, Depends(get_session)]

_Rejection = (
    PromotionInvalid,
    AiNotConfigured,
    AiDailyLimit,
    AiUnavailable,
    AiResultInvalid,
)


def _rejected(session: Session, exc: Exception) -> HTTPException:
    # 描述与规则不合格时服务层还没有回滚；说明文字不回给调用方。
    session.rollback()
    if isinstance(exc, PromotionInvalid):
        problems = [asdict(problem) for problem in exc.problems]
        return HTTPException(
            status_code=422, detail={"code": "promotion_invalid", "problems": problems}
        )
    if isinstance(exc, AiNotConfigured):
        return HTTPException(status_code=503, detail="ai_not_configured")
    if isinstance(exc, AiDailyLimit):
        return HTTPException(status_code=429, detail="ai_daily_limit")
    if isinstance(exc, AiResultInvalid):
        return HTTPException(status_code=502, detail="ai_result_invalid")
    return HTTPException(status_code=503, detail="ai_unavailable")


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
def status(_admin: AdminDep, request: Request) -> StatusOut:
    """AI 是否已配置；只读，不调用模型、不计数。"""
    return StatusOut(configured=_client(request.app.state.settings).configured)


@router.post("/proposal", response_model=ProposalOut)
def proposal(
    raw: WriteBodyDep, admin: AdminDep, session: SessionDep, request: Request
) -> ProposalOut:
    """CSRF → 请求体 → 提案（服务层计数、调用模型并记录；提案不保存）。"""
    require_admin_csrf(request, admin)
    try:
        body = ProposalIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    settings = request.app.state.settings
    rules = tuple(_rule_input(rule) for rule in body.rules)
    try:
        result = propose_decoration(
            session, _client(settings), settings, body.description, rules, _utcnow()
        )
    except _Rejection as exc:
        raise _rejected(session, exc) from None
    return _proposal_out(result)

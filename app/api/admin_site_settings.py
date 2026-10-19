"""后台站点设置 A09 的支付方式接口：GET /api/admin/site-settings、PUT /api/admin/site-settings。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 1 条（只接受测试密钥）、第 2 条
（支付方式取模拟或 Stripe 测试，修改写审计；凭据未配置时不能切到 Stripe 测试）、「数据模型」的
SiteSetting 一行（后台修改写入 AuditEvent）与「权限与资料保护」；docs/UX.md 0.11 的 A09（凭据
未配置或密钥不是测试密钥时 Stripe 选项禁用，保存时服务端再次检查）；Kelvin 2026-10-07 与
2026-10-15 的决定（A09 先只放支付方式开关，不提供短信验证开关的写入）；docs/HANDOFF.md 0.54 的
落实写法（凭据是否可用以 stripe_configured 的五项检查为准，比第 2 条所写的两项更严；凭据不可用
时保存「Stripe 测试」一律被拒，当前已是该值也一样）。保存规则见 app/services/site_settings.py。

两个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

读取（GET）：require_admin（401）→ 返回当前支付方式（get_payment_method）、凭据是否可用
（stripe_available，即当次请求的 stripe_configured）与由 cookie 算出的 CSRF 令牌。不写审计；不返回
短信验证开关。
保存（PUT），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 请求体只有 payment_method（simulated 或 stripe_test，按原样比较、区分大小写），多出字段（含
   短信验证开关）或其他值 422；
5. 值为 stripe_test 而凭据不可用：422 {"detail": "stripe_not_configured"}，不写入。之后才保存，
   成功或与当前值相同都是 200 与保存后的 payment_method。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。
不写日志；stripe_configured 遇到非测试密钥时按 SHOP-TASK-111 记的 stripe_key_not_test 除外。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.pay import _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.site_settings import (
    PaymentMethodRejected,
    get_payment_method,
    save_payment_method,
)
from app.services.stripe_gateway import stripe_configured

router = APIRouter(prefix="/api/admin/site-settings", tags=["admin"], route_class=_NoStoreRoute)

# 与 app/models/site.py 的 PAYMENT_METHODS 相同（测试核对两者一致）。
PaymentMethodName = Literal["simulated", "stripe_test"]


class PaymentMethodIn(BaseModel):
    """只有支付方式；短信验证开关不提供写入。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    payment_method: PaymentMethodName


# 不叫 SiteSettingsOut：公开接口 app/api/site_settings.py 已用这个名字。
class PaymentMethodOut(BaseModel):
    payment_method: str


class AdminSiteSettingsOut(PaymentMethodOut):
    # 为假时页面禁用「Stripe 测试」选项，保存它会被拒。
    stripe_available: bool
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交保存。
    csrf_token: str


async def _save_body(request: Request) -> bytes:
    """保存只在这里判断 413 与 415；结构在 CSRF 之后才校验。"""
    return await _json_body(request)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
SaveBodyDep = Annotated[bytes, Depends(_save_body)]
SessionDep = Annotated[Session, Depends(get_session)]


def _stripe_available(request: Request) -> bool:
    return stripe_configured(request.app.state.settings)


@router.get("", response_model=AdminSiteSettingsOut)
def read(admin: AdminDep, session: SessionDep, request: Request) -> AdminSiteSettingsOut:
    """当前支付方式、凭据是否可用与 CSRF 令牌；只读，不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return AdminSiteSettingsOut(
        payment_method=get_payment_method(session),
        stripe_available=_stripe_available(request),
        csrf_token=csrf_token,
    )


@router.put("", response_model=PaymentMethodOut)
def save(
    raw: SaveBodyDep,
    admin: AdminDep,
    session: SessionDep,
    request: Request,
) -> PaymentMethodOut:
    """CSRF → 请求体 → 凭据检查 → 保存。与当前值相同时不写入、不写审计。"""
    require_admin_csrf(request, admin)
    try:
        body = PaymentMethodIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    # 保存先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        method, _ = save_payment_method(
            session,
            account_id,
            body.payment_method,
            _stripe_available(request),
            _utcnow(),
        )
    except PaymentMethodRejected as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from None
    return PaymentMethodOut(payment_method=method)

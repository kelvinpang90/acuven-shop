"""会员结账的计价与下单接口：POST /api/member/checkout/quote 与 POST /api/member/orders。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 1 条：优惠券先抵商品额，积分再抵剩余商品额，
  二者均不抵运费。
- 「失败、并发与重试」第 1 条：幂等键；相同键相同请求返回原结果，同键不同内容报冲突。
- 「权限与资料保护」第 1、2 条：已登录会员结账不再验证；收货电话默认为会员手机号，
  可改为其他号码或虚构号码；会员改填的收货电话以收货国家作为默认区号解析，
  明确输入加号国家码时以该号码为准，只检查格式。
以及 docs/HANDOFF.md 0.47 记录的 Kelvin 2026-10-11 决定：
下单时券或积分已不可用即整单拒绝。字段参照 docs/UX.md 0.10 的 P05 会员表单
（优惠券代码与使用积分，摘要显示券与积分两行）。规则函数见
app/services/member_ordering.py（SHOP-TASK-088），会话依赖取自 app/api/member_auth.py。

两个接口的检查顺序，每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 8 KB 即 413，先于一切校验；不是 JSON 415。
2. 按访客来源计一次，超过 429 rate_limited：计价与 POST /api/checkout/quote
   共用桶 checkout_quote_source 及其上限，下单与 POST /api/orders/guest
   共用桶 guest_order_source 及其上限。Redis 未配置或出错时放行，
   见 app/api/checkout.py 的 enforce_source_limit。
3. require_member（401 member_session_required）。
4. 只有下单：require_member_csrf（403 csrf_failed）。
5. 之后才校验请求体（下单另有 Idempotency-Key，规则同 POST /api/orders/guest），
   多出字段 422；422 每条错误只给位置、类型与固定消息，不回显请求内容。

下单不收 phone_region：收货电话以收货国家为默认地区规范化（以加号开头时以输入为准），
规范化结果写入收货资料并计入请求指纹；不读短信验证开关，不按白名单拒绝。

错误码：MemberNotActive 为 401 member_session_required；下单的 409 为
idempotency_conflict、order_not_placeable、coupon_invalid（页面的
checkout.coupon_invalid）与 points_unavailable；无效目的地 422、运费缺失 503
（同计价接口）；数据库出错时回滚，503 service_unavailable。
新订单 201，重放 200，不设置任何 cookie。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，
由 SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，
不在此列。本模块不写日志（Redis 出错时限流那一条只含桶名的警告除外）；
响应与错误不含收货资料、券代码原文、幂等键、令牌或 cookie 值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

import phonenumbers
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.checkout import (
    QUOTE_SOURCE_BUCKET,
    QUOTE_SOURCE_LIMIT,
    QUOTE_SOURCE_WINDOW_SECONDS,
    OptionalRedisDep,
    QuoteLineIn,
    QuoteRequest,
    enforce_source_limit,
)
from app.api.member_auth import MemberDep, _rollback, require_member_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import (
    _COUNTRY_CODE,
    GUEST_ORDER_SOURCE_BUCKET,
    GUEST_ORDER_SOURCE_LIMIT,
    GUEST_ORDER_SOURCE_WINDOW_SECONDS,
    IDEMPOTENCY_HEADER,
    RecipientAddress,
    RecipientName,
    RecipientPostalCode,
    RecipientRegion,
    _destination_errors,
    _error,
    _key_errors,
)
from app.api.pay import LanguageDep, _body_errors, _json_body, _utcnow
from app.db.session import get_session
from app.services.checkout import MAX_CART_LINES, CartItem, PricedLine, UnavailableLine
from app.services.member_ordering import (
    CouponUnavailable,
    IdempotencyConflict,
    MemberNotActive,
    MemberOrderRequest,
    OrderNotPlaceable,
    PointsUnavailable,
    place_member_order,
    quote_member_cart,
)
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.shipping import (
    MALAYSIA,
    FxReference,
    InvalidDestination,
    ShippingQuote,
    ShippingRateMissing,
)

router = APIRouter(prefix="/api/member", tags=["member"], route_class=_NoStoreRoute)

# 券代码原文的长度上限；代码是否成立由 app/services/coupons.py 判断。
COUPON_CODE_MAX_CHARS = 64
MAX_POINTS = 100_000_000

CouponCodeIn = Annotated[str, StringConstraints(min_length=1, max_length=COUPON_CODE_MAX_CHARS)]
PointsIn = Annotated[int, Field(ge=0, le=MAX_POINTS)]


class MemberQuoteIn(QuoteRequest):
    """计价接口的购物车行与目的地（规则相同），另加券代码与抵扣积分。"""

    # 省略即不用券；不去空白、不把空白当作省略，是否可用由计价判定。
    coupon_code: CouponCodeIn | None = Field(default=None, repr=False)
    points: PointsIn = 0


class MemberOrderIn(BaseModel):
    """购物车行、收货电话、收货资料与目的地、券代码与抵扣积分。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 与计价接口相同：1 到 20 行，每行 1 到 1000 件，SKU 不重复
    # （重复 SKU 由 _destination_errors 报 422，见 _order_input，不进入下单函数）。
    lines: list[QuoteLineIn] = Field(min_length=1, max_length=MAX_CART_LINES)
    # 收货电话原文：前端预填会员手机号，会员可改为其他号码或虚构号码。
    # 没有 phone_region：以收货国家为默认地区解析，以加号开头时以输入为准。
    phone: str = Field(repr=False)
    name: RecipientName = Field(repr=False)
    address: RecipientAddress = Field(repr=False)
    postal_code: RecipientPostalCode = Field(repr=False)
    country_code: str = Field(pattern=_COUNTRY_CODE)
    # 马来西亚必填，其他国家不给。
    state_code: str | None = None
    # 只有其他国家可给；去掉首尾空白后为空串时按没有地区保存。
    region: RecipientRegion | None = Field(default=None, repr=False)
    coupon_code: CouponCodeIn | None = Field(default=None, repr=False)
    points: PointsIn = 0


@dataclass(frozen=True)
class MemberQuoteOut:
    """计价接口的全部字段，另加券与积分两行。"""

    lines: list[UnavailableLine | PricedLine]
    subtotal_sen: int
    shipping: ShippingQuote | None
    # 扣除券与积分之后的合计（现金实付加运费）及其参考外币；
    # 未给收货国家时为空。
    total_sen: int | None
    fx_reference: FxReference | None
    can_place_order: bool
    # 整单件数与金额上限（DESIGN 1.18 第 8 条），同计价接口。
    unit_limit: int
    units_over_limit: bool
    amount_limit_sen: int
    amount_over_limit: bool
    # 未给代码、有不正常的行或超出整单上限时为空；否则为券是否可用（不给原因）。
    coupon_applied: bool | None
    coupon_discount_sen: int
    points_available: int
    # 可用余额与券后商品额的较小值；有不正常的行或超出整单上限时为 0。
    points_max: int
    points_accepted: bool
    # 计入的抵扣积分；不接受时为 0。
    points_redeemed: int
    points_earned: int


class MemberOrderOut(BaseModel):
    """不含收货资料、券代码、幂等键或令牌。"""

    order_number: str
    status: str
    subtotal_sen: int
    coupon_discount_sen: int
    points_redeemed: int
    shipping_fee_sen: int
    total_sen: int
    points_earned: int
    # UTC。
    payment_expires_at: datetime


@dataclass(frozen=True)
class MemberOrderInput:
    idempotency_key: str = field(repr=False)
    order: MemberOrderRequest


async def _quote_body(request: Request, client: OptionalRedisDep) -> bytes:
    """413、415，再按来源计数；请求体结构在会话之后才校验。"""
    raw = await _json_body(request)
    await enforce_source_limit(
        request, client, QUOTE_SOURCE_BUCKET, QUOTE_SOURCE_LIMIT, QUOTE_SOURCE_WINDOW_SECONDS
    )
    return raw


async def _order_body(request: Request, client: OptionalRedisDep) -> bytes:
    """413、415，再按来源计数；请求体与幂等键在会话与 CSRF 之后才校验。"""
    raw = await _json_body(request)
    await enforce_source_limit(
        request,
        client,
        GUEST_ORDER_SOURCE_BUCKET,
        GUEST_ORDER_SOURCE_LIMIT,
        GUEST_ORDER_SOURCE_WINDOW_SECONDS,
    )
    return raw


# 请求体依赖写在会员与会话依赖之前：FastAPI 按声明顺序解析依赖，
# 413 先于其他一切回答。
QuoteBodyDep = Annotated[bytes, Depends(_quote_body)]
OrderBodyDep = Annotated[bytes, Depends(_order_body)]
SessionDep = Annotated[Session, Depends(get_session)]


def _recipient_phone(body: MemberOrderIn) -> str:
    """收货电话以收货国家为默认地区规范化为 E.164；不成立抛 InvalidPhoneNumber。

    收货国家不在 phonenumbers 支持的地区里时：输入以加号开头则以 MY 作默认地区调用
    （以加号开头时默认地区不参与解析，结果只取决于输入），否则不成立。
    """
    region = body.country_code
    if region not in phonenumbers.SUPPORTED_REGIONS:
        if not body.phone.strip().startswith("+"):
            raise InvalidPhoneNumber
        region = MALAYSIA
    return normalize_phone(body.phone, region).e164


def _order_input(request: Request, raw: bytes) -> MemberOrderInput:
    """校验幂等键与请求体（规则同 POST /api/orders/guest），规范化收货电话。"""
    errors = _key_errors(request)
    body: MemberOrderIn | None = None
    try:
        body = MemberOrderIn.model_validate_json(raw)
    except ValidationError as exc:
        errors.extend(_body_errors(exc))
    phone = None
    if body is not None:
        # SKU 不重复，以及国家、州属与地区的组合（规则同 POST /api/orders/guest）。
        errors.extend(_destination_errors(body))
        try:
            phone = _recipient_phone(body)
        except InvalidPhoneNumber:
            errors.append(_error(("body", "phone"), "phone_invalid", "invalid phone number"))
    if errors or body is None or phone is None:
        raise RequestValidationError(errors)

    order = MemberOrderRequest(
        lines=tuple(CartItem(sku=line.sku, quantity=line.quantity) for line in body.lines),
        phone=phone,
        name=body.name,
        address=body.address,
        postal_code=body.postal_code,
        country_code=body.country_code,
        state_code=body.state_code,
        region=body.region or None,
        coupon_code=body.coupon_code,
        points_to_redeem=body.points,
    )
    return MemberOrderInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], order=order)


def _session_required() -> HTTPException:
    return HTTPException(status_code=401, detail="member_session_required")


def _service_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="service_unavailable")


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=409, detail=code)


@router.post("/checkout/quote", response_model=MemberQuoteOut)
def quote(
    raw: QuoteBodyDep,
    member: MemberDep,
    session: SessionDep,
    lang: LanguageDep,
) -> MemberQuoteOut:
    """只读：不写库、不锁行；结果只供显示，下单时重新判定。

    lang 同计价接口（en、zh、ms，默认 en），在会话之后由 app/api/pay.py 的 _language
    判断；不合法时 422 只给位置、类型与固定消息，不回显所给的值。
    """
    try:
        body = MemberQuoteIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None
    member_id = member.member.id
    try:
        result = quote_member_cart(
            session,
            member_id,
            [CartItem(sku=line.sku, quantity=line.quantity) for line in body.lines],
            body.country_code,
            body.state_code,
            body.coupon_code,
            body.points,
            _utcnow(),
            lang=lang,
        )
    except MemberNotActive:
        raise _session_required() from None
    except InvalidDestination:
        raise HTTPException(status_code=422, detail="invalid country or state code") from None
    except ShippingRateMissing:
        # 与计价接口相同：不回显缺哪一行，也不以零运费代替。
        raise HTTPException(status_code=503, detail="shipping is unavailable") from None
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None
    return MemberQuoteOut(
        lines=result.lines,
        subtotal_sen=result.subtotal_sen,
        shipping=result.shipping,
        total_sen=result.total_sen,
        fx_reference=result.fx_reference,
        can_place_order=result.can_place_order,
        unit_limit=result.unit_limit,
        units_over_limit=result.units_over_limit,
        amount_limit_sen=result.amount_limit_sen,
        amount_over_limit=result.amount_over_limit,
        coupon_applied=result.coupon_applicable,
        coupon_discount_sen=result.coupon_discount_sen,
        points_available=result.available_points,
        points_max=result.points_cap,
        points_accepted=result.points_accepted,
        points_redeemed=result.points_redeemed,
        points_earned=result.points_earned,
    )


@router.post("/orders", status_code=201, response_model=MemberOrderOut)
def place_order(
    raw: OrderBodyDep,
    member: MemberDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> MemberOrderOut:
    """CSRF → 幂等键与请求体 → 下单。新订单 201，重放 200；不设置 cookie。"""
    require_member_csrf(request, member)
    data = _order_input(request, raw)
    # 下单函数会先回滚会话，会员对象随之过期：会员 ID 在此之前取出。
    member_id = member.member.id
    key = data.idempotency_key
    try:
        result = place_member_order(session, member_id, data.order, key, _utcnow())
    except MemberNotActive:
        raise _session_required() from None
    except IdempotencyConflict:
        raise _conflict("idempotency_conflict") from None
    except OrderNotPlaceable:
        raise _conflict("order_not_placeable") from None
    except CouponUnavailable:
        raise _conflict("coupon_invalid") from None
    except PointsUnavailable:
        raise _conflict("points_unavailable") from None
    except InvalidDestination:
        raise HTTPException(status_code=422, detail="invalid country or state code") from None
    except ShippingRateMissing:
        raise HTTPException(status_code=503, detail="shipping is unavailable") from None
    except SQLAlchemyError:
        _rollback(session)
        raise _service_unavailable() from None

    if not result.created:
        response.status_code = 200
    order = result.order
    return MemberOrderOut(
        order_number=order.order_number,
        status=order.status,
        subtotal_sen=order.subtotal_sen,
        coupon_discount_sen=order.coupon_discount_sen,
        points_redeemed=order.points_redeemed,
        shipping_fee_sen=order.shipping_fee_sen,
        total_sen=order.total_sen,
        points_earned=order.points_earned,
        payment_expires_at=order.payment_expires_at.replace(tzinfo=UTC),
    )

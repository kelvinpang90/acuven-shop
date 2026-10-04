"""游客下单的公开接口：只有 POST /api/orders/guest。

不需登录、不读会员会话。依据 docs/DESIGN.md 1.11（提交 2d13250），
规则见 app/services/ordering.py。字段参照 docs/UX.md 0.7 的 P05：
第 1 步的手机号与页面国家码选择，第 3 步的收货人姓名、国家、
马来西亚州属或其他国家的地区、地址、邮编。请求里没有任何价格、
金额、运费、会员或短信验证标记字段。

处理顺序：
1. 请求体按实际读到的字节逐块判断，超过 8 KB（与计价接口相同）
   即停止读取并返回 413，先于一切校验。
2. 只接受 JSON，否则 415。
3. 校验 Idempotency-Key 请求头与请求体，多出的字段一律 422；
   手机号按请求里的地区代码规范化，不成立 422（类型 phone_invalid）。
   422 响应只给出错字段的位置、类型与固定消息，不回显提交的内容。

错误码：403 sms_verification_required；409 idempotency_conflict 与
order_not_placeable；运费行缺失与计价接口一样 503。
新订单 201，重放 200。不写日志。限流留给之后统一处理公开接口限流的任务。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.orm import Session

from app.api.checkout import MAX_BODY_BYTES, QuoteLineIn
from app.db.session import get_session
from app.models import OrderRecipient
from app.services.checkout import MAX_CART_LINES, CartItem
from app.services.order_access import COOKIE_NAME, set_order_access_cookie
from app.services.ordering import (
    GuestOrderRequest,
    IdempotencyConflict,
    OrderNotPlaceable,
    SmsVerificationRequired,
    place_guest_order,
)
from app.services.phone import InvalidPhoneNumber, normalize_phone
from app.services.shipping import (
    MALAYSIA,
    MY_STATE_CODES,
    InvalidDestination,
    ShippingRateMissing,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])

IDEMPOTENCY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,64}")
# 与 app/services/shipping.py 相同：两位大写字母，不转换大小写。
_COUNTRY_CODE = r"^[A-Z]{2}$"


def _column_length(column: str) -> int:
    """收货资料模型的列长度；上限不另写数字。"""
    return OrderRecipient.__table__.c[column].type.length


def _text(column: str, *, required: bool) -> Any:
    """去掉首尾空白后，再按列长度（必填时另要求非空）校验的字符串。"""
    return Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1 if required else 0,
            max_length=_column_length(column),
        ),
    ]


RecipientName = _text("name", required=True)
RecipientAddress = _text("address", required=True)
RecipientPostalCode = _text("postal_code", required=True)
RecipientRegion = _text("region", required=False)


class GuestOrderIn(BaseModel):
    """购物车行、第 1 步手机号与所选地区、收货资料与目的地。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 与计价接口相同：1 到 20 行，每行 1 到 99 件，SKU 不重复。
    lines: list[QuoteLineIn] = Field(min_length=1, max_length=MAX_CART_LINES)
    # 结账第 1 步的手机号原文，与页面国家码选择对应的地区代码
    # （如 MY）；以加号开头时以输入为准。
    phone: str
    phone_region: str
    name: RecipientName
    address: RecipientAddress
    postal_code: RecipientPostalCode
    country_code: str = Field(pattern=_COUNTRY_CODE)
    # 马来西亚必填，其他国家不给。
    state_code: str | None = None
    # 只有其他国家可给；去掉首尾空白后为空串时按没有地区保存。
    region: RecipientRegion | None = None


class GuestOrderOut(BaseModel):
    """不含收货资料、会话令牌或幂等键。"""

    order_number: str
    status: str
    subtotal_sen: int
    shipping_fee_sen: int
    total_sen: int
    # UTC。
    payment_expires_at: datetime
    # 本次签发了授权时，为由会话令牌算出的 CSRF 令牌；
    # 重放的订单已不能支付时为空。
    csrf_token: str | None


@dataclass(frozen=True)
class GuestOrderInput:
    idempotency_key: str
    order: GuestOrderRequest


def _error(loc: tuple[str | int, ...], error_type: str, msg: str) -> dict[str, Any]:
    return {"type": error_type, "loc": loc, "msg": msg}


def _is_json(content_type: str | None) -> bool:
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def _key_errors(request: Request) -> list[dict[str, Any]]:
    values = request.headers.getlist(IDEMPOTENCY_HEADER)
    if not values:
        return [_error(("header", IDEMPOTENCY_HEADER), "missing", "Field required")]
    if len(values) != 1 or not _IDEMPOTENCY_KEY.fullmatch(values[0]):
        msg = "must be 16 to 64 letters, digits, hyphens or underscores"
        return [_error(("header", IDEMPOTENCY_HEADER), "idempotency_key_invalid", msg)]
    return []


def _destination_errors(body: GuestOrderIn) -> list[dict[str, Any]]:
    """SKU 不重复；国家、州属与地区的组合（规则同 app/services/shipping.py）。"""
    errors = []
    skus = [line.sku for line in body.lines]
    if len(set(skus)) != len(skus):
        msg = "each SKU may appear only once"
        errors.append(_error(("body", "lines"), "value_error", msg))
    if body.country_code == MALAYSIA:
        if body.state_code not in MY_STATE_CODES:
            msg = "Malaysia needs a state code MY-01 to MY-16"
            errors.append(_error(("body", "state_code"), "value_error", msg))
        if body.region is not None:
            msg = "region is not accepted for Malaysia"
            errors.append(_error(("body", "region"), "value_error", msg))
    elif body.state_code is not None:
        msg = "state code is only for Malaysia"
        errors.append(_error(("body", "state_code"), "value_error", msg))
    return errors


async def _guest_order_input(request: Request) -> GuestOrderInput:
    """逐块读请求体，超过上限就停下返回 413，不看 Content-Length 是否如实。

    读完之后才判断 Content-Type、幂等键与请求体。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    if not _is_json(request.headers.get("content-type")):
        raise HTTPException(status_code=415, detail="request body must be JSON")

    errors = _key_errors(request)
    body: GuestOrderIn | None = None
    try:
        body = GuestOrderIn.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        # 不回显输入：请求体里有姓名、电话与地址。
        errors.extend(
            _error(("body", *error["loc"]), error["type"], error["msg"])
            for error in exc.errors(include_url=False)
        )
    phone = None
    if body is not None:
        errors.extend(_destination_errors(body))
        try:
            phone = normalize_phone(body.phone, body.phone_region).e164
        except InvalidPhoneNumber:
            errors.append(_error(("body", "phone"), "phone_invalid", "invalid phone number"))
    if errors or body is None or phone is None:
        raise RequestValidationError(errors)

    order = GuestOrderRequest(
        lines=tuple(CartItem(sku=line.sku, quantity=line.quantity) for line in body.lines),
        phone=phone,
        name=body.name,
        address=body.address,
        postal_code=body.postal_code,
        country_code=body.country_code,
        state_code=body.state_code,
        region=body.region or None,
    )
    return GuestOrderInput(idempotency_key=request.headers[IDEMPOTENCY_HEADER], order=order)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，
# 413 先于其他一切回答。
GuestOrderDep = Annotated[GuestOrderInput, Depends(_guest_order_input)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/guest", status_code=201, response_model=GuestOrderOut)
def place_guest(
    body: GuestOrderDep,
    session: SessionDep,
    request: Request,
    response: Response,
) -> GuestOrderOut:
    # 只读订单访问 cookie（沿用本浏览器的订单访问会话），不读会员会话。
    cookie_value = request.cookies.get(COOKIE_NAME)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        result = place_guest_order(session, body.order, body.idempotency_key, cookie_value, now)
    except SmsVerificationRequired:
        raise HTTPException(status_code=403, detail="sms_verification_required") from None
    except IdempotencyConflict:
        raise HTTPException(status_code=409, detail="idempotency_conflict") from None
    except OrderNotPlaceable:
        raise HTTPException(status_code=409, detail="order_not_placeable") from None
    except InvalidDestination:
        raise HTTPException(status_code=422, detail="invalid country or state code") from None
    except ShippingRateMissing:
        # 与计价接口相同：不回显缺哪一行，也不以零运费代替。
        raise HTTPException(status_code=503, detail="shipping is unavailable") from None

    if not result.created:
        response.status_code = 200
    # 响应带 CSRF 令牌，不许缓存。
    response.headers["Cache-Control"] = "no-store"
    csrf_token = None
    if result.access is not None:
        # 沿用会话时以原 cookie 值重设，把 Max-Age 刷新为 30 分钟。
        token = result.access.new_token or cookie_value
        if token is not None:
            set_order_access_cookie(response, token)
        csrf_token = result.access.csrf_token

    order = result.order
    return GuestOrderOut(
        order_number=order.order_number,
        status=order.status,
        subtotal_sen=order.subtotal_sen,
        shipping_fee_sen=order.shipping_fee_sen,
        total_sen=order.total_sen,
        payment_expires_at=order.payment_expires_at.replace(tzinfo=UTC),
        csrf_token=csrf_token,
    )

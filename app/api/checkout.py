"""购物车与结账计价的公开接口：只有 POST /api/checkout/quote，只读、不需登录、不读写 cookie。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）；字段参照 docs/UX.md 的 P04、P05 审阅稿。
每行件数 1–99、最多 20 行依据 docs/DESIGN.md 1.10（提交 e3b3505）
「计价、优惠、积分与库存」第 8 条；超出每单限购不在这里拒绝，由计价把行标为 over_limit
（SHOP-TASK-016）。
lang 与目录接口相同，只接受 en、zh、ms，默认 en。
请求体上限 8 KB，按实际读到的字节逐块判断，先于一切校验：超过即停止读取并返回 413；
未超限的才按请求模型校验，多出的字段一律 422。
接口不改状态，所以不要 CSRF 令牌。限流留给之后统一处理公开接口限流的任务。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.catalog import Language
from app.services.checkout import (
    MAX_CART_LINES,
    MAX_LINE_QUANTITY,
    MAX_SKU_LENGTH,
    CartItem,
    CheckoutQuote,
    quote_cart,
)
from app.services.shipping import InvalidDestination, ShippingRateMissing

router = APIRouter(prefix="/api/checkout", tags=["checkout"])

MAX_BODY_BYTES = 8 * 1024


class QuoteLineIn(BaseModel):
    # strict：件数 2.0、"2" 与 true 都不是整数件数。
    model_config = ConfigDict(extra="forbid", strict=True)

    sku: str = Field(min_length=1, max_length=MAX_SKU_LENGTH)
    quantity: int = Field(ge=1, le=MAX_LINE_QUANTITY)


class QuoteRequest(BaseModel):
    """只有购物车行与收货目的地；没有任何价格、金额、运费、汇率或折扣字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    lines: list[QuoteLineIn] = Field(min_length=1, max_length=MAX_CART_LINES)
    # 代码是否合法按 app/services/shipping.py 的规则判断，不转换大小写。
    country_code: str | None = None
    state_code: str | None = None

    @model_validator(mode="after")
    def _check_lines_and_destination(self) -> QuoteRequest:
        skus = [line.sku for line in self.lines]
        if len(set(skus)) != len(skus):
            raise ValueError("each SKU may appear only once")
        if self.state_code is not None and self.country_code is None:
            raise ValueError("state_code needs country_code")
        return self


async def _quote_request(request: Request) -> QuoteRequest:
    """逐块读请求体，超过上限就停下返回 413，不看 Content-Length 是否如实。"""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    try:
        return QuoteRequest.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        # 不回显输入：请求体可能不是合法的 UTF-8，回显也没有用处。
        errors = [
            {"type": error["type"], "loc": ("body", *error["loc"]), "msg": error["msg"]}
            for error in exc.errors(include_url=False)
        ]
        raise RequestValidationError(errors) from None


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
QuoteRequestDep = Annotated[QuoteRequest, Depends(_quote_request)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/quote", response_model=CheckoutQuote)
def quote(body: QuoteRequestDep, session: SessionDep, lang: Language = "en") -> CheckoutQuote:
    try:
        return quote_cart(
            session,
            [CartItem(sku=line.sku, quantity=line.quantity) for line in body.lines],
            lang=lang,
            country_code=body.country_code,
            state_code=body.state_code,
        )
    except InvalidDestination:
        raise HTTPException(status_code=422, detail="invalid country or state code") from None
    except ShippingRateMissing:
        # 不回显缺哪一行，也不以零运费代替。
        raise HTTPException(status_code=503, detail="shipping is unavailable") from None

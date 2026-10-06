"""购物车与结账计价的公开接口：只有 POST /api/checkout/quote，只读、不需登录、不读写 cookie。

依据 docs/DESIGN.md 1.8（提交 2b68ad0）；字段参照 docs/UX.md 的 P04、P05 审阅稿。
每行件数 1–99、最多 20 行依据 docs/DESIGN.md 1.10（提交 e3b3505）
「计价、优惠、积分与库存」第 8 条；超出每单限购不在这里拒绝，由计价把行标为 over_limit
（SHOP-TASK-016）。
lang 与目录接口相同，只接受 en、zh、ms，默认 en。
请求体上限 8 KB，按实际读到的字节逐块判断，先于一切校验：超过即停止读取并返回 413；
未超限的才按请求模型校验，多出的字段一律 422。
接口不改状态，所以不要 CSRF 令牌。

限流（SHOP-TASK-043，按 docs/HANDOFF.md 记录的 Kelvin 2026-10-06 决定）：读完请求体、413 之后，
JSON 解析与字段校验之前，按访客来源（SHOP-TASK-026 的 client_source）计一次，桶名
checkout_quote_source，10 分钟 300 次，成功失败都计；超过即 429 rate_limited，不回显请求内容。
Redis 未配置、连不上、超时或出错时放行，见 enforce_source_limit。
"""

from __future__ import annotations

import logging
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
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
from app.services.rate_limit import (
    RateLimitUnavailable,
    client_source,
    get_optional_redis_client,
    hit,
)
from app.services.shipping import InvalidDestination, ShippingRateMissing

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkout", tags=["checkout"])

MAX_BODY_BYTES = 8 * 1024

QUOTE_SOURCE_BUCKET = "checkout_quote_source"
QUOTE_SOURCE_LIMIT = 300
QUOTE_SOURCE_WINDOW_SECONDS = 10 * 60

OptionalRedisDep = Annotated[redis.Redis | None, Depends(get_optional_redis_client)]


async def enforce_source_limit(
    request: Request,
    client: redis.Redis | None,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    """按访客来源计一次，超过上限抛 429 rate_limited；计价与游客下单接口共用。

    Redis 不可用时放行，这是 Kelvin 2026-10-06 的决定，与查单的拒绝（503）不同：演示站没有
    真实交易，不能让结账因 Redis 停摆。
    - client 为 None（连接串未配置或客户端创建失败）：直接放行，不记日志。
    - 计数时抛 RateLimitUnavailable（连不上、超时或 Redis 出错）：放行，并记一条只含桶名与固定
      说明的警告；不记来源地址、请求内容、连接串或异常消息。
    """
    if client is None:
        return
    source = client_source(request)
    try:
        # 同步的 Redis 调用放进线程池，不阻塞事件循环。
        allowed = await run_in_threadpool(hit, client, bucket, source, limit, window_seconds)
    except RateLimitUnavailable:
        logger.warning("rate limit store unavailable; %s not limited", bucket)
        return
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limited")


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


async def _quote_request(request: Request, client: OptionalRedisDep) -> QuoteRequest:
    """逐块读请求体，超过上限就停下返回 413，不看 Content-Length 是否如实。

    读完之后先按来源计数（超过 429），再解析与校验。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    await enforce_source_limit(
        request, client, QUOTE_SOURCE_BUCKET, QUOTE_SOURCE_LIMIT, QUOTE_SOURCE_WINDOW_SECONDS
    )
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

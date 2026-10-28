"""后台 A06 运费与演示汇率的读写接口：GET /api/admin/shipping-rates、
PUT /api/admin/shipping-rates。

依据 docs/DESIGN.md 1.16（提交 e9a1c93）「计价、优惠、积分与库存」第 9 条（下称「第 9 条」：
整页一次保存；先不碰数据库检查请求本身，不通过回答「取值不合格」；再锁行按行 ID 与版本号核对，
不通过回答「数据已被改动」；每步不通过都整次保存不生效）、docs/UX.md 0.15 的 A06（读取整页、
整页一次保存、保存后显示成功、取值不合格或数据已被改动）与 docs/HANDOFF.md 0.67 的落实写法
（取值不合格回 422 rates_invalid，数据已被改动回 409 rates_changed；请求体上限 64 KB，由本文件
逐块读取）。规则见 app/services/admin_shipping_rates.py（SHOP-TASK-170），本文件只做结构与 JSON
类型的校验，取值范围、格式、长度、国家与币种是否合格、重复等一律交给服务层。

两个接口都经 SHOP-TASK-036 的 require_admin：没有有效后台会话时 401 admin_session_required。

读取（GET）：require_admin（401）→ 返回 load_admin_rates 的整页数据（states、other、countries、
fx）与由 cookie 算出的 CSRF 令牌。不写审计。
保存（PUT），每一步不通过即停止：
1. 请求体按实际读到的字节逐块判断，超过 64 KB 即 413，先于一切校验；不是 JSON 415；
2. require_admin（401）；
3. X-CSRF-Token（403 csrf_failed）；
4. 请求体结构与 JSON 类型（四项必填，各行只接受规定的字段组合，多出或缺字段 422）；
5. 服务层 RatesInvalid：422 {"detail": "rates_invalid"}；
6. 服务层 RatesChanged：409 {"detail": "rates_changed"}。
   两种拒绝都不写入、不写审计。成功回 200 与保存后的整页数据（不含 csrf_token）。

两个接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control: no-store，由
SHOP-TASK-027 的路由类统一加上；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列。
422 每条错误只给位置、类型与固定消息，不回显请求内容（同 app/api/pay.py 的 _body_errors）。
不写日志。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.api.admin_auth import AdminDep, require_admin_csrf
from app.api.order_lookup import _NoStoreRoute
from app.api.orders import _is_json
from app.api.pay import _body_errors, _utcnow
from app.db.session import get_session
from app.services.admin_auth import csrf_token_for_cookie
from app.services.admin_shipping_rates import (
    AddCountryFee,
    AddFxRate,
    AdminRates,
    CountryItem,
    FxItem,
    FxRateRow,
    KeepFee,
    KeepFxRate,
    RatesChanged,
    RatesInvalid,
    RemoveRow,
    ShippingRateRow,
    load_admin_rates,
    save_admin_rates,
)

router = APIRouter(prefix="/api/admin/shipping-rates", tags=["admin"], route_class=_NoStoreRoute)

# HANDOFF 0.67：整页的国家与汇率行可能超过 app/api/pay.py 的 8 KB。
MAX_BODY_BYTES = 64 * 1024


# ---------------------------------------------------------------------------
# 请求体：只校验结构与 JSON 类型（strict：整数不收布尔值、字符串与浮点数）
# ---------------------------------------------------------------------------


class _RowIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KeepFeeIn(_RowIn):
    """保留一行运费（州属、兜底或国家）并给出新运费。"""

    id: int
    version: int
    fee_sen: int


class RemoveRowIn(_RowIn):
    """删除一行国家运费或汇率；delete 只收 true。"""

    id: int
    version: int
    delete: Literal[True]


class AddCountryFeeIn(_RowIn):
    country_code: str
    fee_sen: int


class KeepFxRateIn(_RowIn):
    """保留一行汇率并给出新汇率；不收币种与小数位。"""

    id: int
    version: int
    rate: str


class AddFxRateIn(_RowIn):
    country_code: str
    currency_code: str
    currency_decimals: int
    rate: str


class AdminRatesIn(BaseModel):
    """四项都必填；other 为兜底的那一行，与读取时的形状相同。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    states: list[KeepFeeIn]
    other: KeepFeeIn
    countries: list[KeepFeeIn | RemoveRowIn | AddCountryFeeIn]
    fx: list[KeepFxRateIn | RemoveRowIn | AddFxRateIn]


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------


class ShippingRateOut(BaseModel):
    id: int
    version: int
    zone_code: str
    fee_sen: int


class FxRateOut(BaseModel):
    id: int
    version: int
    country_code: str
    currency_code: str
    currency_decimals: int
    # 规范形式的十进制字符串（canonical_rate）。
    rate: str


class AdminRatesOut(BaseModel):
    states: list[ShippingRateOut]
    # 迁移 0005 已写入兜底行；只有库里缺这一行时为 None。
    other: ShippingRateOut | None
    countries: list[ShippingRateOut]
    fx: list[FxRateOut]


class AdminRatesPageOut(AdminRatesOut):
    # 由 cookie 重新算出的 CSRF 令牌，供页面提交保存。
    csrf_token: str


def _zone_out(row: ShippingRateRow) -> ShippingRateOut:
    return ShippingRateOut(
        id=row.id, version=row.version, zone_code=row.zone_code, fee_sen=row.fee_sen
    )


def _fx_out(row: FxRateRow) -> FxRateOut:
    return FxRateOut(
        id=row.id,
        version=row.version,
        country_code=row.country_code,
        currency_code=row.currency_code,
        currency_decimals=row.currency_decimals,
        rate=row.rate,
    )


def _page_fields(rates: AdminRates) -> dict[str, object]:
    return {
        "states": [_zone_out(row) for row in rates.states],
        "other": None if rates.other is None else _zone_out(rates.other),
        "countries": [_zone_out(row) for row in rates.countries],
        "fx": [_fx_out(row) for row in rates.fx_rates],
    }


# ---------------------------------------------------------------------------
# 请求体转成服务层的行
# ---------------------------------------------------------------------------


def _keep_fee(item: KeepFeeIn) -> KeepFee:
    return KeepFee(item.id, item.version, item.fee_sen)


def _country_item(item: KeepFeeIn | RemoveRowIn | AddCountryFeeIn) -> CountryItem:
    if isinstance(item, KeepFeeIn):
        return _keep_fee(item)
    if isinstance(item, RemoveRowIn):
        return RemoveRow(item.id, item.version)
    return AddCountryFee(item.country_code, item.fee_sen)


def _fx_item(item: KeepFxRateIn | RemoveRowIn | AddFxRateIn) -> FxItem:
    if isinstance(item, KeepFxRateIn):
        return KeepFxRate(item.id, item.version, item.rate)
    if isinstance(item, RemoveRowIn):
        return RemoveRow(item.id, item.version)
    return AddFxRate(item.country_code, item.currency_code, item.currency_decimals, item.rate)


async def _save_body(request: Request) -> bytes:
    """逐块读请求体，超过 64 KB 就停下返回 413，不看 Content-Length 是否如实；
    读完之后才判断 Content-Type（照 app/api/pay.py 的 _json_body，只有上限不同）。
    结构在 CSRF 之后才校验。
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
    return b"".join(chunks)


# 请求体依赖写在会话依赖之前：FastAPI 按声明顺序解析依赖，413 先于其他一切回答。
SaveBodyDep = Annotated[bytes, Depends(_save_body)]
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=AdminRatesPageOut)
def read(admin: AdminDep, session: SessionDep) -> AdminRatesPageOut:
    """整页的运费与演示汇率与 CSRF 令牌；只读，不写审计。"""
    csrf_token = csrf_token_for_cookie(admin.cookie_value)
    if csrf_token is None:
        # require_admin 通过时 cookie 格式必然合法；这里只为类型收窄。
        raise HTTPException(status_code=401, detail="admin_session_required")
    return AdminRatesPageOut(**_page_fields(load_admin_rates(session)), csrf_token=csrf_token)


@router.put("", response_model=AdminRatesOut)
def save(
    raw: SaveBodyDep,
    admin: AdminDep,
    session: SessionDep,
    request: Request,
) -> AdminRatesOut:
    """CSRF → 请求体 → 取值检查 → 锁行核对 → 保存。整页都未变时不写入、不写审计。"""
    require_admin_csrf(request, admin)
    try:
        body = AdminRatesIn.model_validate_json(raw)
    except ValidationError as exc:
        raise RequestValidationError(_body_errors(exc)) from None

    # 保存先回滚此前的事务，会话里的 ORM 对象随之过期：管理员 ID 在此之前取出。
    account_id = admin.account.id
    try:
        saved = save_admin_rates(
            session,
            account_id,
            [_keep_fee(item) for item in body.states],
            [_keep_fee(body.other)],
            [_country_item(item) for item in body.countries],
            [_fx_item(item) for item in body.fx],
            _utcnow(),
        )
    except RatesInvalid:
        session.rollback()
        raise HTTPException(status_code=422, detail="rates_invalid") from None
    except RatesChanged:
        # 服务层已回滚。
        raise HTTPException(status_code=409, detail="rates_changed") from None
    return AdminRatesOut(**_page_fields(saved))

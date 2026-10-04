"""游客下单：重新计价、预留库存、写订单与收货资料、签发短期凭据；
同一幂等键的重复提交返回原订单。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 1、2 条：以规格价格快照计算商品小计，
  逐件分摊快照；第 3 条：游客不积累积分；第 6 条：下单事务预留
  当日可用库存，15 分钟未支付自动取消；第 8 条：下单时服务端重新
  校验，超出限购即拒绝整单，不部分下单。
- 「数据模型」的 Order / OrderItem、OrderRecipient、
  PaymentAttempt / OrderEvent 三行。
- 「失败、并发与重试」第 1 条：幂等键和数据库唯一约束；相同键相同
  请求返回原结果，同键不同内容报冲突；库存预占及订单创建在同一事务，
  失败全部回滚。
- 「边界与原则」第 4 条与「权限与资料保护」第 1、2 条：创建订单时按
  短信验证开关的当前值判定；开关开启时，游客订单的收货电话属于白名单
  国家即拒绝，除非服务端记录显示该号码刚遇到短信无法送达或停发；
  收货电话即结账第 1 步规范化的号码，不按收货国家重新解析。
- 「权限与资料保护」第 6 条：游客订单创建成功后，给当前浏览器一个
  30 分钟有效、仅限该单的短期凭据。

请求格式校验与手机号规范化由调用方（app/api/orders.py）完成。
本模块不写日志；异常消息不含个人资料、订单号、幂等键或令牌。
成功时由本模块提交事务，任何失败都回滚。时间一律是不带时区的 UTC。
会员下单、优惠券与积分、模拟支付、取消与支付超时都不在这里。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    Product,
    ProductVariant,
    VerificationAttempt,
)
from app.models.member import (
    PURPOSE_CHECKOUT,
    VERIFICATION_SUSPENDED,
    VERIFICATION_UNDELIVERABLE,
)
from app.models.order import ACTOR_GUEST, CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.order_access import SCOPE_GUEST_CHECKOUT
from app.services.catalog import Language, localized
from app.services.checkout import (
    CartItem,
    PricedLine,
    SelectedOption,
    purchasable,
    quote_cart,
    selected_options,
)
from app.services.order_access import IssuedAccess, issue_order_access
from app.services.order_rules import generate_order_number
from app.services.phone import is_sms_whitelisted
from app.services.pricing import OrderLine, price_order
from app.services.shipping import MALAYSIA
from app.services.site_settings import is_sms_verification_enabled

# 第 6 条：15 分钟未支付自动取消。
PAYMENT_WINDOW = timedelta(minutes=15)
# 白名单号码降级为游客下单：该号码最近 30 分钟内的
# 结账短信无法送达或停发。
SMS_FALLBACK_WINDOW = timedelta(minutes=30)
SMS_FALLBACK_STATUSES = (VERIFICATION_UNDELIVERABLE, VERIFICATION_SUSPENDED)

_LANGUAGES: tuple[Language, ...] = ("en", "zh", "ms")
# 规格说明：「规格名: 规格值」按规格名的排列序号以「 / 」连接；
# 无规格商品为空串。
_NAME_VALUE_SEPARATOR: dict[Language, str] = {"en": ": ", "zh": "：", "ms": ": "}
_OPTION_SEPARATOR = " / "


class SmsVerificationRequired(Exception):
    """开关开启时，白名单号码没有短信降级记录。"""


class IdempotencyConflict(Exception):
    """同一幂等键已用于内容不同的请求。"""


class OrderNotPlaceable(Exception):
    """有不可购买、超出限购或库存不足的行，或扣库存失败；没有建订单。"""


@dataclass(frozen=True)
class GuestOrderRequest:
    """经校验与规范化的游客下单请求。

    phone 是规范化的 E.164；姓名、地址、邮编、地区已去掉首尾空白。
    马来西亚时 state_code 是 MY-01 到 MY-16 之一、region 为空；
    其他国家时 state_code 为空，region 是自由文本或空。
    个人资料不进 repr。
    """

    lines: tuple[CartItem, ...]
    phone: str = field(repr=False)
    name: str = field(repr=False)
    address: str = field(repr=False)
    postal_code: str = field(repr=False)
    country_code: str
    state_code: str | None
    region: str | None = field(repr=False)


@dataclass(frozen=True)
class OrderSummary:
    """响应所需的订单信息；不含收货资料与幂等键。"""

    order_number: str
    status: str
    subtotal_sen: int
    shipping_fee_sen: int
    total_sen: int
    payment_expires_at: datetime


@dataclass(frozen=True)
class GuestOrderResult:
    order: OrderSummary
    # 新建订单为真，按幂等键重放为假。
    created: bool
    # 签发的 guest_checkout 授权；重放的订单已不是待支付
    # 或已过支付到期时间时为空。
    access: IssuedAccess | None


def request_fingerprint(request: GuestOrderRequest) -> str:
    """请求指纹：规范化请求内容按固定规则序列化后的 SHA-256 十六进制。

    行按请求顺序；JSON 键排序、无空白、非 ASCII 字符转义，
    同样的内容总得到同样的字节。手机号原文与页面所选地区不进指纹，
    只有规范化后的电话。
    """
    content = {
        "lines": [[item.sku, item.quantity] for item in request.lines],
        "phone": request.phone,
        "name": request.name,
        "address": request.address,
        "postal_code": request.postal_code,
        "country_code": request.country_code,
        "state_code": request.state_code,
        "region": request.region,
    }
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def place_guest_order(
    db: Session,
    request: GuestOrderRequest,
    idempotency_key: str,
    cookie_value: str | None,
    now: datetime,
) -> GuestOrderResult:
    """下一张游客订单，或按幂等键重放原订单。

    1. 按幂等键查订单：有则重放或报冲突，不重新计价、不动库存。
    2. 按短信验证开关的当前值判定能否以游客下单。
    3. 按收货国家与州属重新计价；运费行缺失抛 ShippingRateMissing。
    4. 按 SKU 规格 ID 从小到大逐行扣库存。
    5. 同一事务里写订单、订单行、逐件分摊、收货资料、下单事件，
       签发 guest_checkout 授权，然后提交。

    计价判定不可下单、扣库存失败或插入订单撞上幂等键唯一约束时，
    回滚后再按幂等键查一次：同键的并发请求可能已经提交。
    """
    fingerprint = request_fingerprint(request)
    existing = _order_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(db, existing, fingerprint, cookie_value, now)

    _require_guest_allowed(db, request.phone, now)

    quote = quote_cart(
        db,
        list(request.lines),
        lang="en",
        country_code=request.country_code,
        state_code=request.state_code,
    )
    lines = [line for line in quote.lines if isinstance(line, PricedLine)]
    if not quote.can_place_order or quote.shipping is None or len(lines) != len(request.lines):
        return _after_failure(db, idempotency_key, fingerprint, cookie_value, now)

    found = purchasable(db, [item.sku for item in request.lines])
    if any(item.sku not in found for item in request.lines):
        return _after_failure(db, idempotency_key, fingerprint, cookie_value, now)
    if not _reserve_stock(db, request.lines, found):
        return _after_failure(db, idempotency_key, fingerprint, cookie_value, now)

    pricing = price_order(
        [OrderLine(unit_price=line.unit_price_sen, quantity=line.quantity) for line in lines]
    )
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=pricing.subtotal,
        coupon_discount_sen=0,
        points_redeemed=0,
        shipping_fee_sen=quote.shipping.fee_sen,
        total_sen=pricing.subtotal + quote.shipping.fee_sen,
        points_earned=0,
        shipping_zone_code=quote.shipping.zone_code,
        shipping_rate_version=quote.shipping.version,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_at=now,
        payment_expires_at=now + PAYMENT_WINDOW,
        paid_at=None,
        member_id=None,
        claim_status=CLAIM_OPEN,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        # 同键的并发请求先提交了：回滚本次的库存扣减，
        # 按它的订单重放或报冲突。
        db.rollback()
        existing = _order_by_key(db, idempotency_key)
        if existing is None:
            raise
        return _replay(db, existing, fingerprint, cookie_value, now)

    items = _order_items(db, order.id, request.lines, lines, found)
    db.add_all(items)
    db.flush()
    db.add_all(
        OrderItemUnit(
            order_item_id=items[unit.line_index].id,
            unit_index=unit.unit_index,
            original_price_sen=unit.original_price,
            coupon_discount_sen=unit.coupon_discount,
            points_discount=unit.points_discount,
            cash_paid_sen=unit.cash_paid,
            # 游客不积累积分（第 3 条），与订单的获得积分 0 一致。
            points_earned=0,
        )
        for unit in pricing.units
    )
    db.add(
        OrderRecipient(
            order_id=order.id,
            name=request.name,
            phone=request.phone,
            country_code=request.country_code,
            region=request.state_code if request.country_code == MALAYSIA else request.region,
            address=request.address,
            postal_code=request.postal_code,
        )
    )
    db.add(
        OrderEvent(
            order_id=order.id,
            from_status=None,
            to_status=STATUS_AWAITING_PAYMENT,
            actor_type=ACTOR_GUEST,
            created_at=now,
        )
    )
    access = issue_order_access(db, cookie_value, order.id, SCOPE_GUEST_CHECKOUT, now)
    summary = _summary(order)
    db.commit()
    return GuestOrderResult(order=summary, created=True, access=access)


def _order_by_key(db: Session, idempotency_key: str) -> Order | None:
    return db.scalars(select(Order).where(Order.idempotency_key == idempotency_key)).one_or_none()


def _summary(order: Order) -> OrderSummary:
    return OrderSummary(
        order_number=order.order_number,
        status=order.status,
        subtotal_sen=order.subtotal_sen,
        shipping_fee_sen=order.shipping_fee_sen,
        total_sen=order.total_sen,
        payment_expires_at=order.payment_expires_at,
    )


def _replay(
    db: Session,
    order: Order,
    fingerprint: str,
    cookie_value: str | None,
    now: datetime,
) -> GuestOrderResult:
    """同键同请求返回原订单，不重新计价、不动库存；同键不同请求报冲突。

    订单仍待支付且未过支付到期时间，才签发新的 guest_checkout 授权。
    """
    if not hmac.compare_digest(order.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    summary = _summary(order)
    access = None
    if order.status == STATUS_AWAITING_PAYMENT and now < order.payment_expires_at:
        access = issue_order_access(db, cookie_value, order.id, SCOPE_GUEST_CHECKOUT, now)
        db.commit()
    return GuestOrderResult(order=summary, created=False, access=access)


def _after_failure(
    db: Session,
    idempotency_key: str,
    fingerprint: str,
    cookie_value: str | None,
    now: datetime,
) -> GuestOrderResult:
    """不可下单或扣库存失败：回滚，再按幂等键查一次，没有才报不可下单。"""
    db.rollback()
    existing = _order_by_key(db, idempotency_key)
    if existing is None:
        raise OrderNotPlaceable
    return _replay(db, existing, fingerprint, cookie_value, now)


def _require_guest_allowed(db: Session, phone: str, now: datetime) -> None:
    """按当次读取的短信验证开关，判定这个号码能否以游客下单。

    开关关闭时一律放行；开启时白名单外号码放行。白名单号码只有在
    最近 30 分钟内（按记录的创建时间）有该号码用途为 checkout、
    状态为无法送达或停发的短信验证记录时放行。
    """
    if not is_sms_verification_enabled(db):
        return
    if not is_sms_whitelisted(phone):
        return
    fallback = db.scalars(
        select(VerificationAttempt.id)
        .where(
            VerificationAttempt.phone == phone,
            VerificationAttempt.purpose == PURPOSE_CHECKOUT,
            VerificationAttempt.status.in_(SMS_FALLBACK_STATUSES),
            VerificationAttempt.created_at >= now - SMS_FALLBACK_WINDOW,
        )
        .limit(1)
    ).first()
    if fallback is None:
        raise SmsVerificationRequired


def _reserve_stock(
    db: Session,
    items: tuple[CartItem, ...],
    found: dict[str, tuple[ProductVariant, Product]],
) -> bool:
    """按 SKU 规格 ID 从小到大逐行扣减当日可用库存，可用库存不小于件数才扣。

    有一行扣不到即返回假，由调用方回滚整个事务。
    SKU 不重复，所以每个规格只有一行。
    """
    reservations = sorted((found[item.sku][0].id, item.quantity) for item in items)
    for variant_id, quantity in reservations:
        result = db.execute(
            update(ProductVariant)
            .where(ProductVariant.id == variant_id, ProductVariant.available_stock >= quantity)
            .values(available_stock=ProductVariant.available_stock - quantity)
        )
        if result.rowcount != 1:
            return False
    return True


def _order_items(
    db: Session,
    order_id: int,
    items: tuple[CartItem, ...],
    lines: list[PricedLine],
    found: dict[str, tuple[ProductVariant, Product]],
) -> list[OrderItem]:
    """按请求顺序的订单行；三语名称与规格说明按目录的回退英文规则取好。"""
    product_ids = {product.id for _, product in found.values()}
    variant_ids = {variant.id for variant, _ in found.values()}
    options = {lang: selected_options(db, product_ids, variant_ids, lang) for lang in _LANGUAGES}

    rows = []
    for line_index, (item, line) in enumerate(zip(items, lines, strict=True)):
        variant, product = found[item.sku]
        snapshot: dict[str, str] = {}
        for lang in _LANGUAGES:
            snapshot[f"product_name_{lang}"] = localized(product, "name", lang).text
            snapshot[f"variant_label_{lang}"] = _variant_label(options[lang][variant.id], lang)
        rows.append(
            OrderItem(
                order_id=order_id,
                line_index=line_index,
                variant_id=variant.id,
                sku=item.sku,
                unit_price_sen=line.unit_price_sen,
                quantity=line.quantity,
                line_subtotal_sen=line.line_subtotal_sen,
                **snapshot,
            )
        )
    return rows


def _variant_label(options: list[SelectedOption], lang: Language) -> str:
    """「规格名: 规格值」以「 / 」连接。

    超过列长度时截断，不让订单因规格说明过长而失败。
    """
    separator = _NAME_VALUE_SEPARATOR[lang]
    label = _OPTION_SEPARATOR.join(
        f"{option.name.text}{separator}{option.value.name.text}" for option in options
    )
    return label[: OrderItem.__table__.c[f"variant_label_{lang}"].type.length]

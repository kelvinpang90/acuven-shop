"""会员结账：计价（含优惠券与积分）与下单；同一幂等键的重复提交返回原订单。

依据 docs/DESIGN.md 1.11（提交 2d13250）：
- 「计价、优惠、积分与库存」第 1 条：优惠券先抵商品额，积分再抵剩余商品额，二者均不抵运费，
  最终应付不得低于运费；优惠券仅会员可用，使用额度在下单时预占。第 2 条：逐件分摊。
  第 3 条：结账抵扣时预占。第 6 条：下单事务预留当日可用库存，15 分钟未支付自动取消。
  第 8 条：下单时服务端重新校验，超出限购即拒绝整单，不部分下单。
- 「失败、并发与重试」第 1 条：幂等键和数据库唯一约束；相同键相同请求返回原结果，同键不同
  内容报冲突；库存预占、券/积分预占及订单创建在同一事务，失败全部回滚。
- 「权限与资料保护」第 1 条：已登录会员结账不再验证；第 3 条：会员下单的订单创建时即标记为
  不可认领。
以及 docs/HANDOFF.md 0.47 记录的 Kelvin 2026-10-11 决定：下单时券或积分已不可用即整单拒绝，
不建订单、不预留库存；会员订单的获得积分在下单时按计价结果写入整单与逐件快照，模拟支付成功时
才按快照发放（发放、取消与超时的接线留给 SHOP-TASK-090）。

- 计价（quote_member_cart）：只读，不写库、不锁行。先按 quote_cart 计价；有不正常的行或超出
  任一整单上限（docs/DESIGN.md 1.18 第 8 条，SHOP-TASK-178）时不判定券与积分。
  所有行都正常时券代码非空则查券（只给是否可用，不给原因），不可用不计入；积分上限为
  可用余额与券后商品额的较小值，抵扣积分超过上限时不计入并标明不接受。
- 节日促销活动（DESIGN 1.23，提交 b7f9355，「节日促销活动」第 4、6、10 条与「计价、优惠、积分
  与库存」第 1、2 条，SHOP-TASK-236）：计价以 active_promotion 读生效活动传给 quote_cart；
  券以原价小计查一次，可用即两种方案都有这张券。有运费报价且本单参加活动时由
  decide_member_promotion 判定方案（会员选了券或填了积分才比较甲乙）；取甲时券与积分在活动后
  的金额上计算，券的最低消费也按活动后的金额判断，合计按实际运费。下单（SHOP-TASK-237）复用
  同一个函数，计价与下单得到同一结果。
- 下单（place_member_order）的加锁顺序：结束此前的只读事务后，以 SELECT … FOR UPDATE 锁住
  该会员的行作为新事务的第一条语句（同 app/services/refund_review.py 的锁定协议：之后的读取
  都不早于这个锁，看得到等锁期间别人提交的预占），再按 SKU 规格 ID 从小到大扣库存，写订单，
  然后由 reserve_coupon 锁订单与券、reserve_points 锁积分批次与流水。会员 → 订单 → 券与
  app/services/coupons.py、app/services/points.py 一致。SQLite 不支持行锁，SQLAlchemy 在
  SQLite 上不生成 FOR UPDATE，并发只在 MySQL 上成立。
- 任何一步拒绝或失败都回滚整个事务，再按幂等键查一次：同键的并发请求已提交则按重放处理。

请求格式校验、手机号规范化与登录会话由调用方（之后的接口任务 SHOP-TASK-089）完成。
本模块不写日志；异常消息不含个人资料、订单号、券代码、幂等键或令牌。
成功时由本模块提交事务。时间一律是不带时区的 UTC；金额一律整数仙，积分一律整数。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member, Order, OrderEvent, OrderItemUnit, OrderRecipient
from app.models.member import MEMBER_ACTIVE
from app.models.order import ACTOR_MEMBER, CLAIM_NOT_CLAIMABLE, STATUS_AWAITING_PAYMENT
from app.services.catalog import Language
from app.services.checkout import (
    CartItem,
    PricedLine,
    PromotionQuote,
    UnavailableLine,
    purchasable,
    quote_cart,
)
from app.services.coupons import CouponCheck, check_coupon, normalize_coupon_code, reserve_coupon
from app.services.order_rules import generate_order_number
from app.services.ordering import (
    PAYMENT_WINDOW,
    IdempotencyConflict,
    OrderNotPlaceable,
    _order_by_key,
    _order_items,
    _reserve_stock,
)
from app.services.points import InsufficientPoints, available_points, reserve_points
from app.services.pricing import (
    CouponTerms,
    OrderLine,
    PricingResult,
    choose_promotion_plan,
    price_order,
)
from app.services.promotion_pricing import PromotionPricing
from app.services.promotions import (
    LoadedPromotion,
    PromoInputLine,
    active_promotion,
    price_lines,
)
from app.services.shipping import MALAYSIA, FxReference, ShippingQuote, reference_amount

# 第 6 条所取的方案（promotion_plan）。
PLAN_PROMOTION = "promotion"
PLAN_NO_PROMOTION = "no_promotion"
# 取甲且券本身可用而甲不用券的原因（coupon_note）。
COUPON_NOT_COMBINABLE = "not_combinable"
COUPON_BELOW_MIN_AFTER_SALE = "below_min_after_sale"


class MemberNotActive(Exception):
    """会员不存在或不是 active。"""


class CouponUnavailable(Exception):
    """下单时券代码不成立或券不可用；没有建订单。"""


class PointsUnavailable(Exception):
    """下单时抵扣积分超过可用余额或券后商品额；没有建订单。"""


@dataclass(frozen=True)
class MemberCheckoutQuote:
    """会员计价结果。

    lines、subtotal_sen、shipping、fx_reference、can_place_order、四个整单上限字段与
    promotion 沿用 quote_cart；total_sen 在给了收货国家时为现金实付合计加实际运费（取甲时为
    运费报价减免去的运费，否则为运费报价），否则为空。
    """

    lines: list[UnavailableLine | PricedLine]
    subtotal_sen: int
    shipping: ShippingQuote | None
    total_sen: int | None
    fx_reference: FxReference | None
    can_place_order: bool
    unit_limit: int
    units_over_limit: bool
    amount_limit_sen: int
    amount_over_limit: bool
    # 没给券代码、有不正常的行或超出整单上限时为空；否则为券是否可用（不给原因）。
    coupon_applicable: bool | None
    coupon_discount_sen: int
    # 计入的抵扣积分；不接受时为 0。
    points_redeemed: int
    points_earned: int
    available_points: int
    # 可用余额与券后商品额（取甲时为活动与券之后）的较小值；有不正常的行或超出整单上限时，
    # 以及取甲而活动不与积分同用时为 0。
    points_cap: int
    points_accepted: bool
    # quote_cart 给出的活动结果，原样转出；没有生效活动时为空。
    promotion: PromotionQuote | None
    # PLAN_PROMOTION 或 PLAN_NO_PROMOTION；没有运费报价、没有生效活动、本单不参加活动、
    # 有不正常的行或超出整单上限时为空。
    promotion_plan: str | None
    # 甲与乙的积分抵扣之前的应付；只在比较过时给出。
    promotion_pre_points_total_sen: int | None
    other_pre_points_total_sen: int | None
    # 取甲且券本身可用而甲不用券时为 COUPON_NOT_COMBINABLE 或 COUPON_BELOW_MIN_AFTER_SALE。
    coupon_note: str | None
    # 取甲、会员填了积分而活动不与积分同用时为真。
    points_not_combinable: bool


@dataclass(frozen=True)
class MemberPromotionPlan:
    """decide_member_promotion 的结果：本单按哪种方案计价。

    plan 为空（不比较也不参加）或 PLAN_NO_PROMOTION 时，promotion_pricing 为空、
    shipping_waived_sen 为 0，券与积分照没有活动时使用（use_coupon 即券可用，points_allowed
    为真）。plan 为 PLAN_PROMOTION 时 promotion_pricing 为活动的计价结果（units 按行序、件序，
    与 price_order 的展开一一对应），shipping_waived_sen 为免去的运费，use_coupon 与
    points_allowed 为甲是否用券、是否接受积分。两个积分前应付只在比较过时给出。
    """

    plan: str | None
    promotion_pricing: PromotionPricing | None
    shipping_waived_sen: int
    use_coupon: bool
    points_allowed: bool
    promotion_pre_points_total_sen: int | None
    other_pre_points_total_sen: int | None

    @property
    def promotion_discounts(self) -> list[int] | None:
        """传给 price_order 的每件活动优惠；不取甲时为 None。"""
        if self.promotion_pricing is None:
            return None
        return [unit.promotion_discount for unit in self.promotion_pricing.units]


@dataclass(frozen=True)
class MemberOrderRequest:
    """经校验与规范化的会员下单请求。

    phone 是规范化的 E.164；姓名、地址、邮编、地区已去掉首尾空白。
    马来西亚时 state_code 是 MY-01 到 MY-16 之一、region 为空；
    其他国家时 state_code 为空，region 是自由文本或空。
    coupon_code 为空即不用券；points_to_redeem 为 0 即不抵扣。
    个人资料与券代码不进 repr。
    """

    lines: tuple[CartItem, ...]
    phone: str = field(repr=False)
    name: str = field(repr=False)
    address: str = field(repr=False)
    postal_code: str = field(repr=False)
    country_code: str
    state_code: str | None
    region: str | None = field(repr=False)
    coupon_code: str | None = field(default=None, repr=False)
    points_to_redeem: int = 0


@dataclass(frozen=True)
class MemberOrderSummary:
    """响应所需的订单信息；不含收货资料、券代码与幂等键。"""

    order_number: str
    status: str
    subtotal_sen: int
    coupon_discount_sen: int
    points_redeemed: int
    shipping_fee_sen: int
    total_sen: int
    points_earned: int
    payment_expires_at: datetime


@dataclass(frozen=True)
class MemberOrderResult:
    order: MemberOrderSummary
    # 新建订单为真，按幂等键重放为假。
    created: bool


def _naive(now: datetime) -> datetime:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    return now


def _points_value(points: object) -> int:
    # bool 是 int 的子类，但把 True 当 1 积分显然是调用错误。
    if type(points) is not int:
        raise TypeError(f"points must be int, got {type(points).__name__}")
    if points < 0:
        raise ValueError("points must not be negative")
    return points


def _fingerprint_coupon_code(code: str | None) -> str | None:
    """指纹里的券代码：规范化成立时取规范化结果，不成立时取去掉首尾空白的原文。"""
    if code is None:
        return None
    normalized = normalize_coupon_code(code)
    return normalized if normalized is not None else code.strip()


def member_request_fingerprint(member_id: int, request: MemberOrderRequest) -> str:
    """请求指纹：写法同 app/services/ordering.py 的 request_fingerprint，
    内容另含会员 ID、券代码与抵扣积分。
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
        "member_id": member_id,
        "coupon_code": _fingerprint_coupon_code(request.coupon_code),
        "points_redeemed": request.points_to_redeem,
    }
    data = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# 计价
# ---------------------------------------------------------------------------


def _coupon_terms(coupon: CouponCheck | None) -> CouponTerms | None:
    """可用券组成的 CouponTerms；没给券代码或券不可用时为 None。"""
    if coupon is None or not coupon.ok:
        return None
    if coupon.min_subtotal_sen is None:
        raise ValueError("a usable coupon check must carry min_subtotal_sen")
    return CouponTerms(
        percent_off=coupon.percent_off,
        fixed_off=coupon.fixed_off,
        min_spend_sen=coupon.min_subtotal_sen,
    )


def decide_member_promotion(
    promotion: LoadedPromotion | None,
    lines: Sequence[PromoInputLine],
    *,
    shipping_fee_sen: int | None,
    coupon: CouponCheck | None,
    points_to_redeem: int,
) -> MemberPromotionPlan:
    """「节日促销活动」第 4 条末句与第 6 条：判定会员本单的方案。计价与下单共用。

    lines 为全部正常行（行序严格递增，单价与件数同 price_order 的订单行）；shipping_fee_sen
    为未免的运费报价，None 即还没有运费报价；coupon 为以原价小计查券的结果，没给券代码时为
    None；points_to_redeem 为会员所填的抵扣积分。

    没有运费报价、没有活动或本单不参加活动时 plan 为空。参加活动而没选券也没填积分时取甲、
    不比较。否则以 choose_promotion_plan 比较（coupon 为可用券组成的 CouponTerms 或 None，
    两个开关取活动的设置）。
    """
    points_to_redeem = _points_value(points_to_redeem)
    usable = coupon is not None and coupon.ok
    if promotion is None or shipping_fee_sen is None:
        return MemberPromotionPlan(None, None, 0, usable, True, None, None)
    pricing = price_lines(promotion, lines, shipping_fee_sen=shipping_fee_sen)
    if not pricing.participates:
        return MemberPromotionPlan(None, None, 0, usable, True, None, None)

    waived = pricing.shipping_waived_sen or 0
    if coupon is None and points_to_redeem == 0:
        return MemberPromotionPlan(
            PLAN_PROMOTION, pricing, waived, False, promotion.points_combinable, None, None
        )

    chosen = choose_promotion_plan(
        [OrderLine(unit_price=line.unit_price, quantity=line.quantity) for line in lines],
        [unit.promotion_discount for unit in pricing.units],
        shipping_fee_sen=shipping_fee_sen,
        shipping_waived_sen=waived,
        coupon=_coupon_terms(coupon),
        coupon_combinable=promotion.coupon_combinable,
        points_combinable=promotion.points_combinable,
    )
    if chosen.use_promotion:
        return MemberPromotionPlan(
            PLAN_PROMOTION,
            pricing,
            waived,
            chosen.use_coupon,
            chosen.points_allowed,
            chosen.promotion_pre_points_total,
            chosen.other_pre_points_total,
        )
    return MemberPromotionPlan(
        PLAN_NO_PROMOTION,
        None,
        0,
        chosen.use_coupon,
        chosen.points_allowed,
        chosen.promotion_pre_points_total,
        chosen.other_pre_points_total,
    )


def quote_member_cart(
    db: Session,
    member_id: int,
    items: Sequence[CartItem],
    country_code: str | None,
    state_code: str | None,
    coupon_code: str | None,
    points_to_redeem: int,
    now: datetime,
    *,
    lang: Language = "en",
) -> MemberCheckoutQuote:
    """会员结账计价。只读，不写库、不锁行；结果只供显示，下单时重新判定。

    会员不是 active 时抛 MemberNotActive。给了收货国家时，目的地不合法或运费行缺失照
    quote_cart 抛出。生效活动按 now 从数据库读，不缓存；方案由 decide_member_promotion 判定。
    """
    now = _naive(now)
    points_to_redeem = _points_value(points_to_redeem)
    member = db.scalars(
        select(Member).where(Member.id == member_id).execution_options(populate_existing=True)
    ).one_or_none()
    if member is None or member.status != MEMBER_ACTIVE:
        raise MemberNotActive

    promotion = active_promotion(db, now)
    quote = quote_cart(
        db,
        items,
        lang=lang,
        country_code=country_code,
        state_code=state_code,
        promotion=promotion,
    )
    balance = available_points(db, member_id, now)

    over_cap = quote.units_over_limit or quote.amount_over_limit
    if over_cap or not all(line.status == "ok" for line in quote.lines):
        # 不判定券、积分与方案；合计照 quote_cart（无券无积分，参加活动时按活动后的金额
        # 与实际运费）。
        return MemberCheckoutQuote(
            lines=quote.lines,
            subtotal_sen=quote.subtotal_sen,
            shipping=quote.shipping,
            total_sen=quote.total_sen,
            fx_reference=quote.fx_reference,
            can_place_order=quote.can_place_order,
            unit_limit=quote.unit_limit,
            units_over_limit=quote.units_over_limit,
            amount_limit_sen=quote.amount_limit_sen,
            amount_over_limit=quote.amount_over_limit,
            coupon_applicable=None,
            coupon_discount_sen=0,
            points_redeemed=0,
            points_earned=0,
            available_points=balance,
            points_cap=0,
            points_accepted=False,
            promotion=quote.promotion,
            promotion_plan=None,
            promotion_pre_points_total_sen=None,
            other_pre_points_total_sen=None,
            coupon_note=None,
            points_not_combinable=False,
        )

    # 到这里所有行都正常，与请求的行一一对应。
    lines = [line for line in quote.lines if isinstance(line, PricedLine)]
    priced = [OrderLine(unit_price=line.unit_price_sen, quantity=line.quantity) for line in lines]
    check = None
    coupon_applicable = None
    if coupon_code is not None:
        # 以原价小计查一次：可用即两种方案都有这张券，不可用即都不用券。
        check = check_coupon(db, coupon_code, member_id, quote.subtotal_sen, now)
        coupon_applicable = check.ok

    # 有运费报价且本单参加活动时才判定方案；每件的活动优惠照 SHOP-TASK-235 以 price_lines 重算。
    participating = (
        promotion is not None
        and quote.promotion is not None
        and quote.promotion.participates is True
    )
    promo_lines: list[PromoInputLine] = []
    if participating:
        found = purchasable(db, [item.sku for item in items])
        promo_lines = [
            PromoInputLine(
                line_index=line_index,
                unit_price=line.unit_price_sen,
                quantity=line.quantity,
                product_id=found[item.sku][1].id,
                category_id=found[item.sku][1].category_id,
            )
            for line_index, (item, line) in enumerate(zip(items, lines, strict=True))
        ]
    decision = decide_member_promotion(
        promotion if participating else None,
        promo_lines,
        shipping_fee_sen=None if quote.shipping is None else quote.shipping.fee_sen,
        coupon=check,
        points_to_redeem=points_to_redeem,
    )

    coupon_kwargs: dict[str, int | Decimal] = {}
    if check is not None and decision.use_coupon:
        coupon_kwargs = check.price_kwargs()
    discounts = decision.promotion_discounts
    after_coupon = price_order(priced, promotion_discounts=discounts, **coupon_kwargs)
    pricing = after_coupon
    points_not_combinable = False
    if decision.points_allowed:
        cap = min(
            balance,
            after_coupon.subtotal - after_coupon.promotion_discount - after_coupon.coupon_discount,
        )
        accepted = points_to_redeem <= cap
        if accepted and points_to_redeem > 0:
            pricing = price_order(
                priced,
                promotion_discounts=discounts,
                points_redeemed=points_to_redeem,
                **coupon_kwargs,
            )
    else:
        # 活动不与积分同用：不是积分输入错误，所以照样接受、不计入。
        cap = 0
        accepted = True
        points_not_combinable = points_to_redeem > 0

    coupon_note = None
    if (
        promotion is not None
        and decision.plan == PLAN_PROMOTION
        and coupon_applicable
        and not decision.use_coupon
    ):
        coupon_note = (
            COUPON_BELOW_MIN_AFTER_SALE if promotion.coupon_combinable else COUPON_NOT_COMBINABLE
        )

    total = None
    fx = None
    if quote.shipping is not None:
        total = pricing.cash_total + quote.shipping.fee_sen - decision.shipping_waived_sen
        fx = reference_amount(db, country_code, total)
    return MemberCheckoutQuote(
        lines=quote.lines,
        subtotal_sen=quote.subtotal_sen,
        shipping=quote.shipping,
        total_sen=total,
        fx_reference=fx,
        can_place_order=quote.can_place_order,
        unit_limit=quote.unit_limit,
        units_over_limit=quote.units_over_limit,
        amount_limit_sen=quote.amount_limit_sen,
        amount_over_limit=quote.amount_over_limit,
        coupon_applicable=coupon_applicable,
        coupon_discount_sen=pricing.coupon_discount,
        points_redeemed=pricing.points_redeemed,
        points_earned=pricing.points_earned,
        available_points=balance,
        points_cap=cap,
        points_accepted=accepted,
        promotion=quote.promotion,
        promotion_plan=decision.plan,
        promotion_pre_points_total_sen=decision.promotion_pre_points_total_sen,
        other_pre_points_total_sen=decision.other_pre_points_total_sen,
        coupon_note=coupon_note,
        points_not_combinable=points_not_combinable,
    )


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------


def place_member_order(
    db: Session,
    member_id: int,
    request: MemberOrderRequest,
    idempotency_key: str,
    now: datetime,
) -> MemberOrderResult:
    """下一张会员订单，或按幂等键重放原订单。

    1. 按幂等键查订单：有则在其会员 ID 等于该会员且指纹相同时重放（不重新计价，不动库存、
       券与积分），否则抛 IdempotencyConflict。
    2. 回滚此前的只读事务，锁会员行（新事务的第一条语句），核对为 active。
    3. 按收货国家与州属重新计价，按 SKU 规格 ID 从小到大扣库存。
    4. 判定券与积分，按 price_order 计价。
    5. 同一事务里写订单、订单行、逐件分摊（含获得积分快照）、收货资料与下单事件，
       预占券与积分，然后提交。

    任何一步拒绝或失败（含插入订单撞上幂等键唯一约束）都回滚整个事务，再按幂等键查一次：
    同键的并发请求已提交则按重放处理，否则抛出原异常。
    """
    now = _naive(now)
    _points_value(request.points_to_redeem)
    fingerprint = member_request_fingerprint(member_id, request)
    existing = _order_by_key(db, idempotency_key)
    if existing is not None:
        return _replay(existing, member_id, fingerprint)

    try:
        summary = _create_order(db, member_id, request, idempotency_key, fingerprint, now)
        db.commit()
    except Exception:
        db.rollback()
        existing = _order_by_key(db, idempotency_key)
        if existing is None:
            raise
        return _replay(existing, member_id, fingerprint)
    return MemberOrderResult(order=summary, created=True)


def _lock_member(db: Session, member_id: int) -> Member | None:
    return db.scalars(
        select(Member)
        .where(Member.id == member_id)
        .with_for_update()
        # 会话里可能留着等锁之前读到的旧值。
        .execution_options(populate_existing=True)
    ).one_or_none()


def _create_order(
    db: Session,
    member_id: int,
    request: MemberOrderRequest,
    idempotency_key: str,
    fingerprint: str,
    now: datetime,
) -> MemberOrderSummary:
    """写订单及其子表并预占券与积分，只 flush、不提交；拒绝时抛出，由调用方回滚。"""
    db.rollback()
    member = _lock_member(db, member_id)
    if member is None or member.status != MEMBER_ACTIVE:
        raise MemberNotActive

    quote = quote_cart(
        db,
        list(request.lines),
        lang="en",
        country_code=request.country_code,
        state_code=request.state_code,
    )
    lines = [line for line in quote.lines if isinstance(line, PricedLine)]
    if not quote.can_place_order or quote.shipping is None or len(lines) != len(request.lines):
        raise OrderNotPlaceable

    found = purchasable(db, [item.sku for item in request.lines])
    if any(item.sku not in found for item in request.lines):
        raise OrderNotPlaceable
    if not _reserve_stock(db, request.lines, found):
        raise OrderNotPlaceable

    priced = [OrderLine(unit_price=line.unit_price_sen, quantity=line.quantity) for line in lines]
    coupon_id = None
    coupon_kwargs: dict[str, int | Decimal] = {}
    if request.coupon_code is not None:
        code = normalize_coupon_code(request.coupon_code)
        if code is None:
            raise CouponUnavailable
        check = check_coupon(db, code, member_id, quote.subtotal_sen, now)
        if not check.ok:
            raise CouponUnavailable
        coupon_id = check.coupon_id
        coupon_kwargs = check.price_kwargs()

    points = request.points_to_redeem
    after_coupon = price_order(priced, **coupon_kwargs)
    goods_after_coupon = after_coupon.subtotal - after_coupon.coupon_discount
    if points > available_points(db, member_id, now) or points > goods_after_coupon:
        raise PointsUnavailable
    pricing = price_order(priced, points_redeemed=points, **coupon_kwargs)

    order = _write_order(db, member_id, idempotency_key, fingerprint, now, quote.shipping, pricing)
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
            # 获得积分在下单时写快照（Kelvin 2026-10-11 决定 3），支付成功时才发放。
            points_earned=unit.points_earned,
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
            actor_type=ACTOR_MEMBER,
            created_at=now,
        )
    )
    db.flush()

    if coupon_id is not None:
        reserved = reserve_coupon(
            db, coupon_id, order.id, member_id, pricing.coupon_discount, pricing.subtotal, now
        )
        if not reserved.ok:
            raise CouponUnavailable
    if points > 0:
        try:
            reserve_points(db, member_id, order.id, points, now)
        except InsufficientPoints:
            raise PointsUnavailable from None
    return _summary(order)


def _write_order(
    db: Session,
    member_id: int,
    idempotency_key: str,
    fingerprint: str,
    now: datetime,
    shipping: ShippingQuote,
    pricing: PricingResult,
) -> Order:
    order = Order(
        order_number=generate_order_number(),
        status=STATUS_AWAITING_PAYMENT,
        subtotal_sen=pricing.subtotal,
        coupon_discount_sen=pricing.coupon_discount,
        points_redeemed=pricing.points_redeemed,
        shipping_fee_sen=shipping.fee_sen,
        # 券与积分都不抵运费，应付不低于运费。
        total_sen=pricing.cash_total + shipping.fee_sen,
        points_earned=pricing.points_earned,
        shipping_zone_code=shipping.zone_code,
        shipping_rate_version=shipping.version,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        created_at=now,
        payment_expires_at=now + PAYMENT_WINDOW,
        paid_at=None,
        member_id=member_id,
        claim_status=CLAIM_NOT_CLAIMABLE,
    )
    db.add(order)
    # 撞上幂等键唯一约束时在这里抛 IntegrityError，由 place_member_order 回滚后按键重放。
    db.flush()
    return order


def _summary(order: Order) -> MemberOrderSummary:
    return MemberOrderSummary(
        order_number=order.order_number,
        status=order.status,
        subtotal_sen=order.subtotal_sen,
        coupon_discount_sen=order.coupon_discount_sen,
        points_redeemed=order.points_redeemed,
        shipping_fee_sen=order.shipping_fee_sen,
        total_sen=order.total_sen,
        points_earned=order.points_earned,
        payment_expires_at=order.payment_expires_at,
    )


def _replay(order: Order, member_id: int, fingerprint: str) -> MemberOrderResult:
    """同一会员同请求返回原订单，不重新计价，不动库存、券与积分；否则报冲突。"""
    if order.member_id != member_id:
        raise IdempotencyConflict
    if not hmac.compare_digest(order.request_fingerprint, fingerprint):
        raise IdempotencyConflict
    return MemberOrderResult(order=_summary(order), created=False)

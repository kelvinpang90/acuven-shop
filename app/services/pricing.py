"""计价逐件分摊与参加、不参加活动取更优的纯函数。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「计价、优惠、积分与库存」第 1–4 条
与「节日促销活动」第 6、7 条。
只用标准库，不碰数据库、库存、积分批次与退款。
金额一律整数仙，积分一律整数积分；100 积分抵 RM1，
所以 1 积分抵 1 仙，积分抵扣额与积分数在数值上相同。
任何参数都不接受 float；百分比券的比率以 Decimal 传入，Decimal(10) 表示 10%。
每件的活动优惠由 app.services.promotion_pricing 算出后传入；
券是否在有效期、次数是否用完由调用方先判断，再把券的优惠与最低消费传进来。
券与积分都不抵运费：price_order 不含运费，运费只在 choose_promotion_plan 的比较里出现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

# 100 仙（RM1）现金实付给 1 积分（第 3 条）。
SEN_PER_EARNED_POINT = 100


@dataclass(frozen=True)
class OrderLine:
    """订单行：规格价格快照（每件原价，仙）与件数。"""

    unit_price: int
    quantity: int


@dataclass(frozen=True)
class UnitAllocation:
    """展开后的一件商品的分摊快照。"""

    line_index: int
    unit_index: int
    original_price: int
    coupon_discount: int
    points_discount: int
    cash_paid: int
    points_earned: int
    promotion_discount: int


@dataclass(frozen=True)
class PricingResult:
    units: tuple[UnitAllocation, ...]
    subtotal: int
    coupon_discount: int
    points_redeemed: int
    cash_total: int
    points_earned: int
    promotion_discount: int


def _require_non_negative_int(name: str, value: object) -> int:
    # bool 是 int 的子类，但把 True 当 1 仙显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def _require_percent(name: str, percent: object) -> Decimal:
    if not isinstance(percent, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(percent).__name__}")
    if not percent.is_finite() or percent < 0 or percent > 100:
        raise ValueError(f"{name} must be between 0 and 100, got {percent}")
    return percent


@dataclass(frozen=True)
class CouponTerms:
    """调用方判断可用之后的一张券：percent_off 与 fixed_off 恰有一个不为 None。"""

    percent_off: Decimal | None
    fixed_off: int | None
    min_spend_sen: int

    def __post_init__(self) -> None:
        if (self.percent_off is None) == (self.fixed_off is None):
            raise ValueError("a coupon needs exactly one of percent_off and fixed_off")
        if self.percent_off is not None:
            _require_percent("percent_off", self.percent_off)
        else:
            _require_non_negative_int("fixed_off", self.fixed_off)
        _require_non_negative_int("min_spend_sen", self.min_spend_sen)


@dataclass(frozen=True)
class PromotionPlan:
    """第 6 条所取的方案：use_promotion 为 True 即甲（参加活动），False 即乙。"""

    use_promotion: bool
    use_coupon: bool
    points_allowed: bool
    promotion_pre_points_total: int
    other_pre_points_total: int


def allocate_largest_remainder(total: int, weights: Sequence[int]) -> list[int]:
    """把 total 按 weights 比例分摊。

    先向下取整，余下的单位按小数余数从大到小各补 1，平手按序号。
    分母（weights 之和）为零时每份都是零。
    """
    _require_non_negative_int("total", total)
    for i, weight in enumerate(weights):
        _require_non_negative_int(f"weights[{i}]", weight)

    denominator = sum(weights)
    if denominator == 0:
        return [0] * len(weights)

    shares: list[int] = []
    remainders: list[int] = []
    for weight in weights:
        share, remainder = divmod(total * weight, denominator)
        shares.append(share)
        # 余数的分母相同，直接比较分子即可，不引入任何浮点或舍入。
        remainders.append(remainder)

    leftover = total - sum(shares)
    by_remainder = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for i in by_remainder[:leftover]:
        shares[i] += 1
    return shares


def percent_coupon_discount(subtotal: int, percent: Decimal) -> int:
    """百分比券对整单商品小计算一次，十进制定点数四舍五入到整仙，恰好半仙进一仙。"""
    _require_non_negative_int("subtotal", subtotal)
    _require_percent("percent", percent)

    with localcontext() as ctx:
        # 精度留足，保证乘法与移位都精确，唯一的舍入发生在 quantize。
        ctx.prec = len(str(subtotal)) + len(percent.as_tuple().digits) + 10
        exact = (Decimal(subtotal) * percent).scaleb(-2)
        return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def earned_points(cash_paid: Sequence[int]) -> list[int]:
    """整单现金实付合计每满 100 仙给 1 积分，再按逐件现金实付比例分摊。

    不按行分别取整。
    """
    total = sum(_require_non_negative_int(f"cash_paid[{i}]", c) for i, c in enumerate(cash_paid))
    return allocate_largest_remainder(total // SEN_PER_EARNED_POINT, cash_paid)


def _expand(lines: Sequence[OrderLine]) -> tuple[list[int], list[tuple[int, int]]]:
    """按订单行序、件序展开，返回每件原价与（行序, 件序）。"""
    prices: list[int] = []
    positions: list[tuple[int, int]] = []
    for line_index, line in enumerate(lines):
        unit_price = _require_non_negative_int(f"lines[{line_index}].unit_price", line.unit_price)
        quantity = _require_non_negative_int(f"lines[{line_index}].quantity", line.quantity)
        for unit_index in range(quantity):
            prices.append(unit_price)
            positions.append((line_index, unit_index))
    return prices, positions


def _promotion_shares(promotion_discounts: Sequence[int] | None, prices: list[int]) -> list[int]:
    """每件的活动优惠：None 即每件为 0；否则逐件为不超过该件原价的非负整数。"""
    if promotion_discounts is None:
        return [0] * len(prices)
    if len(promotion_discounts) != len(prices):
        raise ValueError(
            f"promotion_discounts has {len(promotion_discounts)} items, "
            f"order has {len(prices)} units"
        )
    shares: list[int] = []
    for i, (discount, price) in enumerate(zip(promotion_discounts, prices, strict=True)):
        _require_non_negative_int(f"promotion_discounts[{i}]", discount)
        if discount > price:
            raise ValueError(f"promotion_discounts[{i}] {discount} exceeds unit price {price}")
        shares.append(discount)
    return shares


def _coupon_total(goods_total: int, percent_off: Decimal | None, fixed_off: int | None) -> int:
    """百分比券对整单商品额算一次；固定券取券额与整单商品额较小者（第 2 条）。"""
    if percent_off is not None:
        return percent_coupon_discount(goods_total, percent_off)
    if fixed_off is not None:
        return min(_require_non_negative_int("fixed_off", fixed_off), goods_total)
    return 0


def price_order(
    lines: Sequence[OrderLine],
    *,
    promotion_discounts: Sequence[int] | None = None,
    percent_off: Decimal | None = None,
    fixed_off: int | None = None,
    points_redeemed: int = 0,
) -> PricingResult:
    """按订单行序、件序展开商品，逐件扣去活动优惠、分摊券与积分，并算出逐件获得积分。

    promotion_discounts 为展开后每件的活动优惠，None 即每件为 0（与 1.22 的算法逐仙相同）。
    券在活动后的金额上计算与分摊，积分在券后的金额上分摊。
    percent_off 与 fixed_off 至多给一个；最低消费由调用方判断。
    points_redeemed 超过券后商品额时拒绝。
    返回的 points_earned 是模拟支付成功后应给的积分；何时发放由调用方决定。
    """
    if percent_off is not None and fixed_off is not None:
        raise ValueError("an order takes at most one coupon")
    _require_non_negative_int("points_redeemed", points_redeemed)

    prices, positions = _expand(lines)
    promotions = _promotion_shares(promotion_discounts, prices)
    subtotal = sum(prices)

    after_promotion = [price - promo for price, promo in zip(prices, promotions, strict=True)]
    promotion_total = sum(promotions)
    after_promotion_total = subtotal - promotion_total

    coupon_total = _coupon_total(after_promotion_total, percent_off, fixed_off)
    coupons = allocate_largest_remainder(coupon_total, after_promotion)

    after_coupon = [a - coupon for a, coupon in zip(after_promotion, coupons, strict=True)]
    after_coupon_total = after_promotion_total - coupon_total
    if points_redeemed > after_coupon_total:
        raise ValueError(
            f"points_redeemed {points_redeemed} exceeds goods amount after coupon "
            f"{after_coupon_total}"
        )
    points = allocate_largest_remainder(points_redeemed, after_coupon)

    cash = [amount - point for amount, point in zip(after_coupon, points, strict=True)]
    earned = earned_points(cash)

    units = tuple(
        UnitAllocation(
            line_index=line_index,
            unit_index=unit_index,
            original_price=prices[i],
            coupon_discount=coupons[i],
            points_discount=points[i],
            cash_paid=cash[i],
            points_earned=earned[i],
            promotion_discount=promotions[i],
        )
        for i, (line_index, unit_index) in enumerate(positions)
    )
    return PricingResult(
        units=units,
        subtotal=subtotal,
        coupon_discount=coupon_total,
        points_redeemed=points_redeemed,
        cash_total=sum(cash),
        points_earned=sum(earned),
        promotion_discount=promotion_total,
    )


def _require_bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be bool, got {type(value).__name__}")
    return value


def choose_promotion_plan(
    lines: Sequence[OrderLine],
    promotion_discounts: Sequence[int],
    *,
    shipping_fee_sen: int,
    shipping_waived_sen: int,
    coupon: CouponTerms | None,
    coupon_combinable: bool,
    points_combinable: bool,
) -> PromotionPlan:
    """「节日促销活动」第 6 条：比较甲（参加活动）与乙（不参加活动）的积分抵扣之前的应付。

    shipping_fee_sen 为未免的运费，shipping_waived_sen 为活动免去的运费。
    取较低者，相同取甲。何时调用（本单参加活动、会员选了券或填了积分）由调用方判断。
    """
    prices, _ = _expand(lines)
    if promotion_discounts is None:
        raise TypeError("promotion_discounts must be a sequence of int, got NoneType")
    promotions = _promotion_shares(promotion_discounts, prices)
    _require_non_negative_int("shipping_fee_sen", shipping_fee_sen)
    _require_non_negative_int("shipping_waived_sen", shipping_waived_sen)
    if shipping_waived_sen > shipping_fee_sen:
        raise ValueError(
            f"shipping_waived_sen {shipping_waived_sen} exceeds shipping_fee_sen {shipping_fee_sen}"
        )
    if coupon is not None and not isinstance(coupon, CouponTerms):
        raise TypeError(f"coupon must be CouponTerms or None, got {type(coupon).__name__}")
    _require_bool("coupon_combinable", coupon_combinable)
    _require_bool("points_combinable", points_combinable)

    subtotal = sum(prices)
    after_promotion_total = subtotal - sum(promotions)

    # 甲：开关打开的券仍须在活动后的金额上达到最低消费。
    promotion_uses_coupon = (
        coupon is not None and coupon_combinable and after_promotion_total >= coupon.min_spend_sen
    )
    promotion_coupon = (
        _coupon_total(after_promotion_total, coupon.percent_off, coupon.fixed_off)
        if promotion_uses_coupon
        else 0
    )
    promotion_total = (
        after_promotion_total - promotion_coupon + shipping_fee_sen - shipping_waived_sen
    )

    # 乙：不享受任何活动规则（含免运费），券按原价小计判断与计算。
    other_uses_coupon = coupon is not None and subtotal >= coupon.min_spend_sen
    other_coupon = (
        _coupon_total(subtotal, coupon.percent_off, coupon.fixed_off) if other_uses_coupon else 0
    )
    other_total = subtotal - other_coupon + shipping_fee_sen

    use_promotion = promotion_total <= other_total
    return PromotionPlan(
        use_promotion=use_promotion,
        use_coupon=promotion_uses_coupon if use_promotion else other_uses_coupon,
        points_allowed=points_combinable if use_promotion else True,
        promotion_pre_points_total=promotion_total,
        other_pre_points_total=other_total,
    )

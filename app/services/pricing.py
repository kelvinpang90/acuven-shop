"""计价逐件分摊纯函数。

依据 docs/DESIGN.md 1.6（提交 845fd93）「计价、优惠、积分与库存」第 2–4 条。
只用标准库，不碰数据库、库存、积分批次与退款。
金额一律整数仙，积分一律整数积分；100 积分抵 RM1，
所以 1 积分抵 1 仙，积分抵扣额与积分数在数值上相同。
任何参数都不接受 float；百分比券的比率以 Decimal 传入，Decimal(10) 表示 10%。
运费不在这里：券与积分都不抵运费，运费由调用方另计。
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


@dataclass(frozen=True)
class PricingResult:
    units: tuple[UnitAllocation, ...]
    subtotal: int
    coupon_discount: int
    points_redeemed: int
    cash_total: int
    points_earned: int


def _require_non_negative_int(name: str, value: object) -> int:
    # bool 是 int 的子类，但把 True 当 1 仙显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


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
    if not isinstance(percent, Decimal):
        raise TypeError(f"percent must be Decimal, got {type(percent).__name__}")
    if not percent.is_finite() or percent < 0 or percent > 100:
        raise ValueError(f"percent must be between 0 and 100, got {percent}")

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


def price_order(
    lines: Sequence[OrderLine],
    *,
    percent_off: Decimal | None = None,
    fixed_off: int | None = None,
    points_redeemed: int = 0,
) -> PricingResult:
    """按订单行序、件序展开商品，逐件分摊券折扣与积分抵扣，并算出逐件获得积分。

    percent_off 与 fixed_off 至多给一个。points_redeemed 超过券后商品额时拒绝。
    返回的 points_earned 是模拟支付成功后应给的积分；何时发放由调用方决定。
    """
    if percent_off is not None and fixed_off is not None:
        raise ValueError("an order takes at most one coupon")
    _require_non_negative_int("points_redeemed", points_redeemed)

    prices: list[int] = []
    positions: list[tuple[int, int]] = []
    for line_index, line in enumerate(lines):
        unit_price = _require_non_negative_int(f"lines[{line_index}].unit_price", line.unit_price)
        quantity = _require_non_negative_int(f"lines[{line_index}].quantity", line.quantity)
        for unit_index in range(quantity):
            prices.append(unit_price)
            positions.append((line_index, unit_index))
    subtotal = sum(prices)

    if percent_off is not None:
        coupon_total = percent_coupon_discount(subtotal, percent_off)
    elif fixed_off is not None:
        coupon_total = min(_require_non_negative_int("fixed_off", fixed_off), subtotal)
    else:
        coupon_total = 0
    coupons = allocate_largest_remainder(coupon_total, prices)

    after_coupon = [price - coupon for price, coupon in zip(prices, coupons, strict=True)]
    after_coupon_total = subtotal - coupon_total
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
    )

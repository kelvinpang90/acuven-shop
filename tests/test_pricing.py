"""逐件分摊是订单、退款与积分账本共用的金额快照依据。

依据 docs/DESIGN.md 1.6（提交 845fd93）「计价、优惠、积分与库存」第 2–4 条。
每条测试的文档字符串引用它守住的设计原句；
算例测试逐字复现第 4 条的数字，后续退款按这些逐件数字返还与追回。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.pricing import (
    OrderLine,
    allocate_largest_remainder,
    earned_points,
    percent_coupon_discount,
    price_order,
)

# 第 4 条算例：A 商品 2 件、各 RM10；B 商品 1 件、RM5。
EXAMPLE_LINES = [OrderLine(unit_price=1000, quantity=2), OrderLine(unit_price=500, quantity=1)]


def _column(result, field: str) -> list[int]:
    return [getattr(unit, field) for unit in result.units]


def test_design_worked_example_is_reproduced_exactly() -> None:
    """第 4 条：「整单 10% 券减 RM2.50，分摊到 A 每件 RM1、B RM0.50；使用 300 积分抵 RM3，
    分摊到 A 每件 RM1.20、B RM0.60。三件现金实付依次 RM7.80、RM7.80、RM3.90，共 RM19.50，
    整单获得 19 积分，按稳定序分给 8、7、4。」
    """
    result = price_order(EXAMPLE_LINES, percent_off=Decimal(10), points_redeemed=300)

    assert result.subtotal == 2500
    assert result.coupon_discount == 250
    assert _column(result, "coupon_discount") == [100, 100, 50]
    assert _column(result, "points_discount") == [120, 120, 60]
    assert _column(result, "cash_paid") == [780, 780, 390]
    assert result.cash_total == 1950
    assert result.points_earned == 19
    assert _column(result, "points_earned") == [8, 7, 4]


def test_units_are_expanded_in_line_order_then_unit_order() -> None:
    """第 2 条：「先将商品按稳定的订单行序与件序展开。」"""
    result = price_order(EXAMPLE_LINES)

    assert [(u.line_index, u.unit_index) for u in result.units] == [(0, 0), (0, 1), (1, 0)]
    assert _column(result, "original_price") == [1000, 1000, 500]


def test_cash_paid_is_price_minus_coupon_share_minus_points_share() -> None:
    """第 2 条：「每件现金实付 = 原价 − 分摊券额 − 分摊积分额；运费另计。」"""
    lines = [OrderLine(333, 2), OrderLine(777, 3), OrderLine(1, 1)]
    result = price_order(lines, percent_off=Decimal("17.5"), points_redeemed=1234)

    for unit in result.units:
        assert unit.cash_paid == unit.original_price - unit.coupon_discount - unit.points_discount
    assert sum(_column(result, "coupon_discount")) == result.coupon_discount
    assert sum(_column(result, "points_discount")) == 1234
    assert result.cash_total == result.subtotal - result.coupon_discount - 1234


def test_percent_coupon_rounds_an_exact_half_sen_up() -> None:
    """第 2 条：「以十进制定点数按“四舍五入，恰好半仙进一仙”得到整仙折扣」。

    1245 仙的 10% 是 124.5 仙：四舍五入得 125；银行家舍入会得 124，这里必须是 125。
    """
    assert percent_coupon_discount(1245, Decimal(10)) == 125
    assert percent_coupon_discount(25, Decimal(10)) == 3
    assert percent_coupon_discount(1244, Decimal(10)) == 124
    assert percent_coupon_discount(1249, Decimal("10")) == 125
    assert percent_coupon_discount(1, Decimal("49.99")) == 0
    assert percent_coupon_discount(1, Decimal(50)) == 1


def test_percent_coupon_is_computed_once_on_the_whole_subtotal() -> None:
    """第 2 条：「百分比券对整单商品小计计算一次」。

    三件各 5 仙、10%：整单 15 仙 × 10% = 1.5 → 2 仙；逐件各算会得 0.5 → 1 仙 × 3 = 3 仙。
    """
    result = price_order([OrderLine(5, 3)], percent_off=Decimal(10))

    assert result.coupon_discount == 2
    assert _column(result, "coupon_discount") == [1, 1, 0]


def test_fixed_coupon_larger_than_subtotal_is_capped_at_subtotal() -> None:
    """第 2 条：「固定金额券取券额与商品小计较小者。」"""
    result = price_order([OrderLine(300, 1), OrderLine(200, 1)], fixed_off=1000)

    assert result.coupon_discount == 500
    assert _column(result, "coupon_discount") == [300, 200]
    assert _column(result, "cash_paid") == [0, 0]
    assert result.points_earned == 0


def test_fixed_coupon_smaller_than_subtotal_is_used_in_full() -> None:
    """第 2 条：「固定金额券取券额与商品小计较小者。」"""
    result = price_order(EXAMPLE_LINES, fixed_off=500)

    assert result.coupon_discount == 500
    assert _column(result, "coupon_discount") == [200, 200, 100]


def test_leftover_sen_ties_go_by_line_order_then_unit_order() -> None:
    """第 2 条：「余下的仙依小数余数从大到小补 1 仙，平手按订单行序、件序。」

    三件各 100 仙分 200 仙券：每件 66.67，余下 2 仙且余数全部相同，
    依次给第 1 行第 1 件、第 2 行第 1 件，第 2 行第 2 件不补。
    """
    result = price_order([OrderLine(100, 1), OrderLine(100, 2)], fixed_off=200)

    assert _column(result, "coupon_discount") == [67, 67, 66]


def test_leftover_goes_to_larger_remainder_before_earlier_position() -> None:
    """第 2 条：「余下的仙依小数余数从大到小补 1 仙」——余数大小优先于位置。

    第 4 条算例的获得积分即此情形：7.6、7.6、3.8 先补余数 0.8 的 B，再按序补第一件 A。
    """
    assert allocate_largest_remainder(19, [780, 780, 390]) == [8, 7, 4]


def test_zero_subtotal_gives_zero_discounts() -> None:
    """第 2 条：「分母为零时抵扣为零。」"""
    zero_priced = [OrderLine(0, 2)]

    for kwargs in ({"percent_off": Decimal(50)}, {"fixed_off": 500}, {}):
        result = price_order(zero_priced, **kwargs)
        assert result.coupon_discount == 0
        assert _column(result, "coupon_discount") == [0, 0]
        assert _column(result, "points_discount") == [0, 0]
        assert _column(result, "cash_paid") == [0, 0]
        assert _column(result, "points_earned") == [0, 0]

    empty = price_order([], fixed_off=500)
    assert empty.units == ()
    assert empty.subtotal == empty.coupon_discount == empty.cash_total == 0
    assert allocate_largest_remainder(0, [0, 0, 0]) == [0, 0, 0]


def test_points_share_uses_after_coupon_amount_as_weight() -> None:
    """第 2 条：「再将积分抵扣按每件券后金额占整单券后金额的比例分摊。」

    固定券 1000 仙全落在一件 1000 仙与一件 500 仙上后，券后是 333、167，积分按它们分。
    """
    lines = [OrderLine(1000, 1), OrderLine(500, 1)]
    result = price_order(lines, fixed_off=1000, points_redeemed=5)

    assert _column(result, "coupon_discount") == [667, 333]
    assert _column(result, "points_discount") == [3, 2]
    assert _column(result, "cash_paid") == [330, 165]


def test_earned_points_are_floored_on_the_order_total_not_per_line() -> None:
    """第 3 条：「先对整单商品现金实付合计每满 100 仙（RM1）给 1 积分，……不按行分别取整。」

    两行各 150 仙：整单 300 仙得 3 积分；按行取整只会得 1 + 1 = 2。
    """
    result = price_order([OrderLine(150, 1), OrderLine(150, 1)])

    assert result.points_earned == 3
    assert _column(result, "points_earned") == [2, 1]
    assert earned_points([99]) == [0]
    assert earned_points([50, 50]) == [1, 0]


def test_points_redeemed_equal_to_after_coupon_amount_is_allowed() -> None:
    """第 1、2 条：积分只抵剩余商品额；恰好抵完时现金实付为零、不获得积分。"""
    result = price_order(EXAMPLE_LINES, percent_off=Decimal(10), points_redeemed=2250)

    assert _column(result, "points_discount") == [900, 900, 450]
    assert result.cash_total == 0
    assert result.points_earned == 0


def test_points_redeemed_above_after_coupon_amount_are_rejected() -> None:
    """第 1 条：「积分再抵剩余商品额」——超过券后商品额的积分抵扣拒绝，不截断。"""
    with pytest.raises(ValueError):
        price_order(EXAMPLE_LINES, percent_off=Decimal(10), points_redeemed=2251)
    with pytest.raises(ValueError):
        price_order([OrderLine(0, 1)], points_redeemed=1)


@pytest.mark.parametrize(
    "call",
    [
        lambda: price_order([OrderLine(-1, 1)]),
        lambda: price_order([OrderLine(100, -1)]),
        lambda: price_order(EXAMPLE_LINES, fixed_off=-1),
        lambda: price_order(EXAMPLE_LINES, points_redeemed=-1),
        lambda: price_order(EXAMPLE_LINES, percent_off=Decimal(-1)),
        lambda: allocate_largest_remainder(-1, [1]),
        lambda: allocate_largest_remainder(1, [1, -1]),
        lambda: earned_points([100, -1]),
    ],
)
def test_negative_inputs_are_rejected(call) -> None:
    """第 2 条：金额以整仙计、积分以整积分计，分摊只对非负数成立。

    负数输入一律拒绝，不截断为零。
    """
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("percent", [Decimal("100.01"), Decimal("-0.01"), Decimal("NaN")])
def test_percent_outside_zero_to_hundred_is_rejected(percent: Decimal) -> None:
    """第 2 条：「百分比券对整单商品小计计算一次」——比率只能在 0 到 100 之间。"""
    with pytest.raises(ValueError):
        price_order(EXAMPLE_LINES, percent_off=percent)


def test_percent_bounds_zero_and_hundred_are_accepted() -> None:
    """第 1、2 条：券先抵商品额；100% 券抵完商品额，0% 券不抵。"""
    assert price_order(EXAMPLE_LINES, percent_off=Decimal(0)).coupon_discount == 0
    assert price_order(EXAMPLE_LINES, percent_off=Decimal(100)).coupon_discount == 2500


@pytest.mark.parametrize(
    "call",
    [
        lambda: price_order([OrderLine(1000.0, 1)]),
        lambda: price_order([OrderLine(1000, 1.0)]),
        lambda: price_order(EXAMPLE_LINES, percent_off=10.0),
        lambda: price_order(EXAMPLE_LINES, percent_off=10),
        lambda: price_order(EXAMPLE_LINES, fixed_off=100.0),
        lambda: price_order(EXAMPLE_LINES, points_redeemed=300.0),
        lambda: price_order(EXAMPLE_LINES, points_redeemed=True),
        lambda: percent_coupon_discount(1245.0, Decimal(10)),
    ],
)
def test_float_and_non_decimal_ratio_are_rejected(call) -> None:
    """第 2 条：「以十进制定点数」计算、结果为「整仙」。

    金额与积分只收 int，比率只收 Decimal；float 一律拒绝。
    """
    with pytest.raises(TypeError):
        call()


def test_an_order_takes_at_most_one_coupon() -> None:
    """第 1 条：「优惠券先抵商品额」——一张订单只算一张券，同时给百分比与固定额拒绝。"""
    with pytest.raises(ValueError):
        price_order(EXAMPLE_LINES, percent_off=Decimal(10), fixed_off=100)

"""参加与不参加活动的取更优决定订单记不记活动、用不用券、能不能用积分。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 6–8 条与
「计价、优惠、积分与库存」第 1、2 条。
每条测试的文档字符串引用它守住的设计原句；
算例先以 SHOP-TASK-231 的 price_promotion 算出每件活动优惠与免去的运费，
再交给 choose_promotion_plan 与 price_order，逐仙复现第 8 条。
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from app.services.pricing import (
    CouponTerms,
    OrderLine,
    PromotionPlan,
    choose_promotion_plan,
    price_order,
)
from app.services.promotion_pricing import (
    SPEND_AMOUNT,
    BuyXGetYRule,
    FreeShippingRule,
    ItemDiscountRule,
    PromoLine,
    SpendRule,
    price_promotion,
)

# 第 8 条算例的活动：A 打 8 折（规则 1）；「家居」B、C 买 2 享 1、享受的件 100%（规则 2）；
# 满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费。收货西马，运费 RM8.00。
EXAMPLE_ITEM_RULES = {
    1: ItemDiscountRule(rule_id=1, percent=20, amount_sen=None),
    2: BuyXGetYRule(rule_id=2, buy_quantity=2, get_quantity=1, get_percent=100),
}
EXAMPLE_SPEND = SpendRule(mode=SPEND_AMOUNT, tiers=((5000, 500), (10000, 1200)))
EXAMPLE_FREE_SHIPPING = FreeShippingRule(threshold_sen=6000)
WEST_MALAYSIA_FEE = 800

# 会员购物车：A 2 件各 RM25.00、B 2 件各 RM12.00、C 1 件 RM9.00。
EXAMPLE_LINES = (
    PromoLine(line_index=0, unit_price=2500, quantity=2, rule_id=1),
    PromoLine(line_index=1, unit_price=1200, quantity=2, rule_id=2),
    PromoLine(line_index=2, unit_price=900, quantity=1, rule_id=2),
)
# 10% 券（最低消费 RM50）。
TEN_PERCENT_COUPON = CouponTerms(percent_off=Decimal(10), fixed_off=None, min_spend_sen=5000)


def _column(result, field: str) -> list[int]:
    return [getattr(unit, field) for unit in result.units]


def _order_lines(promo_lines) -> list[OrderLine]:
    return [OrderLine(unit_price=line.unit_price, quantity=line.quantity) for line in promo_lines]


def _promotion(promo_lines, **kwargs):
    kwargs.setdefault("item_rules", EXAMPLE_ITEM_RULES)
    kwargs.setdefault("spend_rule", EXAMPLE_SPEND)
    kwargs.setdefault("free_shipping_rule", EXAMPLE_FREE_SHIPPING)
    kwargs.setdefault("shipping_fee_sen", WEST_MALAYSIA_FEE)
    return price_promotion(promo_lines, **kwargs)


def _plan(promo_lines, promotion, **kwargs) -> PromotionPlan:
    kwargs.setdefault("coupon_combinable", True)
    kwargs.setdefault("points_combinable", True)
    return choose_promotion_plan(
        _order_lines(promo_lines),
        _column(promotion, "promotion_discount"),
        shipping_fee_sen=promotion.shipping_fee_sen,
        shipping_waived_sen=promotion.shipping_waived_sen,
        **kwargs,
    )


def test_design_worked_example_takes_the_promotion_and_prices_part_four() -> None:
    """第 8 条（四）：「活动后商品额 RM59.00，达到券的最低消费；券折 RM5.90，
    分摊为 184、184、111、111、0 仙；1,000 积分抵 RM10.00，……分摊为 313、313、187、187、0 仙。
    五件现金实付依次 RM13.47、RM13.47、RM8.08、RM8.08、RM0.00，应付 RM43.10
    = 83.00 − 24.00 − 5.90 − 10.00 + 0；获得 43 积分，分给 14、13、8、8、0。」
    第 6 条「取较低的一种」：甲 59.00 − 5.90 + 0 = RM53.10，乙 83.00 − 8.30 + 8.00 = RM82.70。
    """
    promotion = _promotion(EXAMPLE_LINES)
    plan = _plan(EXAMPLE_LINES, promotion, coupon=TEN_PERCENT_COUPON)

    assert plan == PromotionPlan(
        use_promotion=True,
        use_coupon=True,
        points_allowed=True,
        promotion_pre_points_total=5310,
        other_pre_points_total=8270,
    )

    result = price_order(
        _order_lines(EXAMPLE_LINES),
        promotion_discounts=_column(promotion, "promotion_discount"),
        percent_off=TEN_PERCENT_COUPON.percent_off,
        points_redeemed=1000,
    )
    assert result.subtotal == 8300
    assert result.promotion_discount == 2400
    assert _column(result, "promotion_discount") == [656, 656, 94, 94, 900]
    assert result.coupon_discount == 590
    assert _column(result, "coupon_discount") == [184, 184, 111, 111, 0]
    assert _column(result, "points_discount") == [313, 313, 187, 187, 0]
    assert _column(result, "cash_paid") == [1347, 1347, 808, 808, 0]
    assert result.cash_total + promotion.shipping_actual_sen == 4310
    assert result.cash_total == 8300 - 2400 - 590 - 1000
    assert result.points_earned == 43
    assert _column(result, "points_earned") == [14, 13, 8, 8, 0]


def test_coupon_not_combinable_takes_the_promotion_without_coupon() -> None:
    """第 8 条：「同一购物车若活动关闭了券同用，甲（活动，不用券）的积分前应付为 RM59.00，
    乙（不参加活动，用券）为 83.00 − 8.30 + 8.00 = RM82.70，取甲。」
    """
    promotion = _promotion(EXAMPLE_LINES)
    plan = _plan(EXAMPLE_LINES, promotion, coupon=TEN_PERCENT_COUPON, coupon_combinable=False)

    assert plan.promotion_pre_points_total == 5900
    assert plan.other_pre_points_total == 8270
    assert plan.use_promotion is True
    assert plan.use_coupon is False
    assert plan.points_allowed is True


def test_large_fixed_coupon_beats_the_promotion_on_item_d() -> None:
    """第 8 条：「购物车只有不在任何商品级规则里的 D 1 件 RM200.00、运费 RM8.00，
    券为直减 RM30，活动关闭券同用：甲为 200.00 − 12.00 + 0 = RM188.00，
    乙为 200.00 − 30.00 + 8.00 = RM178.00，取乙（本单不记活动、不免运费）。」
    """
    lines = (PromoLine(line_index=0, unit_price=20000, quantity=1, rule_id=None),)
    promotion = _promotion(lines)
    coupon = CouponTerms(percent_off=None, fixed_off=3000, min_spend_sen=0)
    plan = _plan(lines, promotion, coupon=coupon, coupon_combinable=False)

    assert promotion.shipping_waived_sen == 800
    assert plan == PromotionPlan(
        use_promotion=False,
        use_coupon=True,
        points_allowed=True,
        promotion_pre_points_total=18800,
        other_pre_points_total=17800,
    )


def test_promotion_can_push_the_coupon_below_its_minimum_spend() -> None:
    """第 8 条：「第三例：购物车只有 E 1 件 RM100.00，活动只有满 RM100 减 RM1，
    券为直减 RM50、最低消费 RM100，两个开关都打开，运费 RM8.00：甲的活动后商品额 RM99.00
    够不到券的门槛，积分前应付为 99.00 + 8.00 = RM107.00；
    乙为 100.00 − 50.00 + 8.00 = RM58.00，取乙。」
    第 6 条：「开关打开的券仍须在活动后的金额上达到最低消费，达不到即甲不用券」。
    """
    lines = (PromoLine(line_index=0, unit_price=10000, quantity=1, rule_id=None),)
    promotion = _promotion(
        lines,
        item_rules={},
        spend_rule=SpendRule(mode=SPEND_AMOUNT, tiers=((10000, 100),)),
        free_shipping_rule=None,
    )
    coupon = CouponTerms(percent_off=None, fixed_off=5000, min_spend_sen=10000)
    plan = _plan(lines, promotion, coupon=coupon)

    assert promotion.after_item_subtotal - promotion.order_discount_total == 9900
    assert plan == PromotionPlan(
        use_promotion=False,
        use_coupon=True,
        points_allowed=True,
        promotion_pre_points_total=10700,
        other_pre_points_total=5800,
    )


def test_promotion_plan_with_coupon_under_minimum_spend_does_not_use_it() -> None:
    """第 6 条：「开关打开的券仍须在活动后的金额上达到最低消费，达不到即甲不用券」。

    第三例的券换成直减 RM5：甲 RM107.00，乙 RM103.00 取乙；
    活动优惠加大到 RM10.00 时甲 RM98.00 更低，取甲，而甲仍够不到最低消费、不用券。
    """
    lines = [OrderLine(unit_price=10000, quantity=1)]
    coupon = CouponTerms(percent_off=None, fixed_off=500, min_spend_sen=10000)

    plan = choose_promotion_plan(
        lines,
        [100],
        shipping_fee_sen=800,
        shipping_waived_sen=0,
        coupon=coupon,
        coupon_combinable=True,
        points_combinable=True,
    )
    assert (plan.promotion_pre_points_total, plan.other_pre_points_total) == (10700, 10300)
    assert plan.use_promotion is False

    plan = choose_promotion_plan(
        lines,
        [1000],
        shipping_fee_sen=800,
        shipping_waived_sen=0,
        coupon=coupon,
        coupon_combinable=True,
        points_combinable=True,
    )
    assert (plan.promotion_pre_points_total, plan.other_pre_points_total) == (9800, 10300)
    assert plan.use_promotion is True
    assert plan.use_coupon is False


def test_points_not_combinable_takes_the_promotion_without_points() -> None:
    """第 6 条：「积分按价值计入比较的口径（100 积分 = RM1），所以只关闭积分同用时，
    参加活动的订单不能用积分。」第 8 条的购物车只关闭积分同用：甲 RM53.10 仍低于乙 RM82.70。
    """
    promotion = _promotion(EXAMPLE_LINES)
    plan = _plan(EXAMPLE_LINES, promotion, coupon=TEN_PERCENT_COUPON, points_combinable=False)

    assert plan == PromotionPlan(
        use_promotion=True,
        use_coupon=True,
        points_allowed=False,
        promotion_pre_points_total=5310,
        other_pre_points_total=8270,
    )


def test_equal_totals_take_the_promotion() -> None:
    """第 6 条：「比较两者「积分抵扣之前的应付」……，取较低的一种，相同取甲」。

    RM10.00 一件、活动优惠 RM1.00、不免运费；直减 RM1 的券不可同用：
    甲 9.00 + 8.00 与乙 10.00 − 1.00 + 8.00 都是 RM17.00，取甲且甲不用券。
    """
    plan = choose_promotion_plan(
        [OrderLine(unit_price=1000, quantity=1)],
        [100],
        shipping_fee_sen=800,
        shipping_waived_sen=0,
        coupon=CouponTerms(percent_off=None, fixed_off=100, min_spend_sen=0),
        coupon_combinable=False,
        points_combinable=False,
    )

    assert plan == PromotionPlan(
        use_promotion=True,
        use_coupon=False,
        points_allowed=False,
        promotion_pre_points_total=1700,
        other_pre_points_total=1700,
    )


def test_points_only_compares_promotion_against_full_price() -> None:
    """第 6 条：「会员在结账时选了券或填了积分……都算两种方案」，乙「券与积分照常使用」。

    只填积分、没有券：两方都不用券，乙即原价小计加运费；相同取甲，甲按开关不能用积分。
    """
    plan = choose_promotion_plan(
        [OrderLine(unit_price=1000, quantity=1)],
        [0],
        shipping_fee_sen=800,
        shipping_waived_sen=0,
        coupon=None,
        coupon_combinable=True,
        points_combinable=False,
    )

    assert (plan.promotion_pre_points_total, plan.other_pre_points_total) == (1800, 1800)
    assert plan.use_promotion is True
    assert plan.use_coupon is False
    assert plan.points_allowed is False


def test_plan_and_coupon_terms_are_immutable() -> None:
    """第 6 条：「下单时服务端按同一规则重算，不信任前台的选择」——方案与券条件算出后不可改。"""
    plan = _plan(EXAMPLE_LINES, _promotion(EXAMPLE_LINES), coupon=TEN_PERCENT_COUPON)
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.use_promotion = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        TEN_PERCENT_COUPON.min_spend_sen = 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"percent_off": None, "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": Decimal(10), "fixed_off": 100, "min_spend_sen": 0},
        {"percent_off": Decimal("100.01"), "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": Decimal(-1), "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": Decimal("NaN"), "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": None, "fixed_off": -1, "min_spend_sen": 0},
        {"percent_off": None, "fixed_off": 100, "min_spend_sen": -1},
    ],
)
def test_invalid_coupon_terms_are_rejected(kwargs) -> None:
    """「计价、优惠、积分与库存」第 1、2 条：一张订单一张券，百分比在 0 到 100 之间，
    金额以整仙计且不为负。
    """
    with pytest.raises(ValueError):
        CouponTerms(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"percent_off": 10, "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": 10.0, "fixed_off": None, "min_spend_sen": 0},
        {"percent_off": None, "fixed_off": 100.0, "min_spend_sen": 0},
        {"percent_off": None, "fixed_off": True, "min_spend_sen": 0},
        {"percent_off": None, "fixed_off": 100, "min_spend_sen": 5000.0},
    ],
)
def test_coupon_terms_of_wrong_type_are_rejected(kwargs) -> None:
    """「计价、优惠、积分与库存」第 2 条：「以十进制定点数」计算、结果为「整仙」——
    比率只收 Decimal，金额只收 int。
    """
    with pytest.raises(TypeError):
        CouponTerms(**kwargs)


def _choose(**overrides) -> PromotionPlan:
    kwargs = {
        "lines": [OrderLine(unit_price=1000, quantity=2)],
        "promotion_discounts": [100, 0],
        "shipping_fee_sen": 800,
        "shipping_waived_sen": 800,
        "coupon": None,
        "coupon_combinable": True,
        "points_combinable": True,
    }
    kwargs.update(overrides)
    lines = kwargs.pop("lines")
    promotion_discounts = kwargs.pop("promotion_discounts")
    return choose_promotion_plan(lines, promotion_discounts, **kwargs)


@pytest.mark.parametrize(
    "overrides",
    [
        {"promotion_discounts": [100]},
        {"promotion_discounts": [100, 0, 0]},
        {"promotion_discounts": [-1, 0]},
        {"promotion_discounts": [1001, 0]},
        {"shipping_fee_sen": -1, "shipping_waived_sen": 0},
        {"shipping_waived_sen": -1},
        {"shipping_waived_sen": 801},
        {"lines": [OrderLine(unit_price=-1, quantity=1)], "promotion_discounts": [0]},
    ],
)
def test_invalid_plan_inputs_are_rejected(overrides) -> None:
    """第 4 条（三）：「免去的运费记为原运费」，所以免去的不超过未免的运费；
    第 7 条：每件「现金实付……不为负」，所以每件活动优惠为不超过原价的非负整数。
    """
    with pytest.raises(ValueError):
        _choose(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"promotion_discounts": None},
        {"promotion_discounts": [100.0, 0]},
        {"promotion_discounts": [True, 0]},
        {"shipping_fee_sen": 800.0},
        {"shipping_waived_sen": False},
        {"coupon": Decimal(10)},
        {"coupon_combinable": 1},
        {"points_combinable": None},
    ],
)
def test_plan_inputs_of_wrong_type_are_rejected(overrides) -> None:
    """「计价、优惠、积分与库存」第 2 条：金额为「整仙」——金额只收 int，不收 float 与 bool；
    券只收 CouponTerms，两个开关（第 6 条）只收 bool。
    """
    with pytest.raises(TypeError):
        _choose(**overrides)

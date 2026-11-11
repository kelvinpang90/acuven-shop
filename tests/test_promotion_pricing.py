"""节日促销活动的逐件优惠是订单快照、券与积分分摊共用的依据。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 3–5、8、10 条。
每条测试的文档字符串引用它守住的设计原句；
算例测试逐仙复现第 8 条的（一）至（三），（四）的券与积分见 SHOP-TASK-232。
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.promotion_pricing import (
    SPEND_AMOUNT,
    SPEND_PERCENT,
    BuyXGetYProgress,
    BuyXGetYRule,
    FreeShippingRule,
    ItemDiscountRule,
    PromoLine,
    SpendRule,
    price_promotion,
)

# 第 8 条算例：A 2 件各 RM25.00（规则 1：打 8 折）；
# B 2 件各 RM12.00、C 1 件 RM9.00（规则 2：「家居」买 2 享 1、享受的件 100%）；
# 满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费；运费 RM8.00。
EXAMPLE_LINES = (
    PromoLine(line_index=0, unit_price=2500, quantity=2, rule_id=1),
    PromoLine(line_index=1, unit_price=1200, quantity=2, rule_id=2),
    PromoLine(line_index=2, unit_price=900, quantity=1, rule_id=2),
)
EXAMPLE_ITEM_RULES = {
    1: ItemDiscountRule(rule_id=1, percent=20, amount_sen=None),
    2: BuyXGetYRule(rule_id=2, buy_quantity=2, get_quantity=1, get_percent=100),
}
EXAMPLE_SPEND = SpendRule(mode=SPEND_AMOUNT, tiers=((5000, 500), (10000, 1200)))
EXAMPLE_FREE_SHIPPING = FreeShippingRule(threshold_sen=6000)

LINE = (PromoLine(0, 1000, 1, None),)


def _column(result, field: str) -> list:
    return [getattr(unit, field) for unit in result.units]


def _example(**overrides):
    kwargs = {
        "item_rules": EXAMPLE_ITEM_RULES,
        "spend_rule": EXAMPLE_SPEND,
        "free_shipping_rule": EXAMPLE_FREE_SHIPPING,
        "shipping_fee_sen": 800,
    }
    kwargs.update(overrides)
    return price_promotion(EXAMPLE_LINES, **kwargs)


def _price(lines=LINE, **kwargs):
    kwargs.setdefault("item_rules", {})
    return price_promotion(lines, **kwargs)


def test_design_worked_example_parts_one_to_three_are_reproduced_exactly() -> None:
    """第 8 条：「S0 = RM83.00。（一）A 每件折 RM5.00；B、C 共 3 件，k = 1，
    最便宜的 C 折 RM9.00；S1 = RM64.00。（二）达到 RM50 一档，整单减 RM5.00，
    ……分摊为 156、156、94、94、0 仙。每件活动优惠依次为 656、656、94、94、900 仙，
    合计 RM24.00。（三）S1 达到 RM60，运费为 0，免去 RM8.00。」
    下一档还差 RM36.00 由第 10 条「满额减的当前档与下一档还差的金额」得出。
    """
    result = _example()

    assert result.subtotal == 8300
    assert _column(result, "item_discount") == [500, 500, 0, 0, 900]
    assert result.item_discount_total == 1900
    assert result.after_item_subtotal == 6400
    assert result.spend_tier_reached == 1
    assert result.order_discount_total == 500
    assert _column(result, "order_discount") == [156, 156, 94, 94, 0]
    assert _column(result, "promotion_discount") == [656, 656, 94, 94, 900]
    assert sum(_column(result, "promotion_discount")) == 2400
    assert result.spend_next_gap_sen == 3600
    assert result.free_shipping_reached is True
    assert result.free_shipping_gap_sen == 0
    assert result.shipping_fee_sen == 800
    assert result.shipping_waived_sen == 800
    assert result.shipping_actual_sen == 0
    assert result.participates is True
    assert result.buy_x_get_y == (BuyXGetYProgress(rule_id=2, units_in_scope=3, units_to_next=3),)


def test_units_expand_in_line_then_unit_order_and_carry_the_line_rule() -> None:
    """第 7 条：「逐件快照记活动优惠与命中的商品级规则 ID（可空）」；
    第 5 条：「把该规则范围内的正常行按件展开」——不享受折扣的 B 也记规则 2。
    """
    result = _example()

    assert [(u.line_index, u.unit_index) for u in result.units] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
        (2, 0),
    ]
    assert _column(result, "original_price") == [2500, 2500, 1200, 1200, 900]
    assert _column(result, "rule_id") == [1, 1, 2, 2, 2]


def test_line_index_is_kept_when_non_normal_lines_were_left_out() -> None:
    """第 4 条：「只算计价结果里的正常行」。

    调用方略去非正常行后，每件仍记它在购物车里的行序。
    """
    result = _price([PromoLine(0, 1000, 1, None), PromoLine(3, 500, 2, None)])

    assert [(u.line_index, u.unit_index) for u in result.units] == [(0, 0), (3, 0), (3, 1)]
    assert _column(result, "rule_id") == [None, None, None]
    assert result.subtotal == result.after_item_subtotal == 2000


def test_promotion_discount_is_item_plus_order_share() -> None:
    """第 7 条：「每件的活动优惠 = 该件的商品级折扣 + 分摊到的整单级优惠。」"""
    lines = [PromoLine(0, 333, 3, 1), PromoLine(1, 777, 2, 2), PromoLine(2, 1, 1, None)]
    rules = {1: ItemDiscountRule(1, 17, None), 2: BuyXGetYRule(2, 1, 1, 35)}
    spend = SpendRule(SPEND_PERCENT, ((1000, 11),))
    result = price_promotion(lines, item_rules=rules, spend_rule=spend)

    for unit in result.units:
        assert unit.promotion_discount == unit.item_discount + unit.order_discount
    assert sum(_column(result, "item_discount")) == result.item_discount_total
    assert sum(_column(result, "order_discount")) == result.order_discount_total
    assert result.after_item_subtotal == result.subtotal - result.item_discount_total


# ---- 商品级打折（第 3 条） ----


def test_item_percent_rounds_an_exact_half_sen_up() -> None:
    """第 3 条：「每件折扣 = 该 SKU 单价 × 百分比 ÷ 100，
    按第 2 条的“四舍五入，恰好半仙进一仙”取整仙」。

    1245 仙的 10% 是 124.5 仙，得 125；25 仙得 2.5 → 3；1244 仙得 124.4 → 124。
    """
    lines = [PromoLine(0, 1245, 1, 1), PromoLine(1, 25, 1, 1), PromoLine(2, 1244, 1, 1)]
    result = _price(lines, item_rules={1: ItemDiscountRule(1, 10, None)})

    assert _column(result, "item_discount") == [125, 3, 124]


def test_item_percent_is_computed_per_unit() -> None:
    """第 3 条：「每件折扣 = 该 SKU 单价 × 百分比 ÷ 100」——逐件取整。

    三件各 5 仙打 10%：每件 0.5 → 1 仙，共 3 仙；按行合计 15 仙算只得 2 仙。
    """
    result = _price([PromoLine(0, 5, 3, 1)], item_rules={1: ItemDiscountRule(1, 10, None)})

    assert _column(result, "item_discount") == [1, 1, 1]
    assert result.after_item_subtotal == 12


def test_item_amount_is_capped_at_the_unit_price() -> None:
    """第 3 条：「每件直降（1–50,000,000 仙，每件折扣取直降额与单价较小者）」。"""
    lines = [PromoLine(0, 500, 1, 1), PromoLine(1, 1000, 2, 1), PromoLine(2, 0, 1, 1)]
    result = _price(lines, item_rules={1: ItemDiscountRule(1, None, 700)})

    assert _column(result, "item_discount") == [500, 700, 700, 0]
    assert result.after_item_subtotal == 2500 - 1900


def test_lines_without_a_rule_get_no_item_discount() -> None:
    """第 3 条：「适用范围为若干商品与若干分类」——不在范围里（rule_id 为空）的行不打折。"""
    lines = [PromoLine(0, 1000, 1, None), PromoLine(1, 1000, 1, 1)]
    result = _price(lines, item_rules={1: ItemDiscountRule(1, 50, None)})

    assert _column(result, "item_discount") == [0, 500]
    assert _column(result, "rule_id") == [None, 1]


# ---- 买 X 享 Y（第 3、5、10 条） ----


def test_buy_x_get_y_gives_nothing_below_x_plus_y_units() -> None:
    """第 5 条：「享受折扣的件数 k = ⌊n ÷ (X + Y)⌋ × Y」——n 不足 X + Y 时 k = 0；
    第 10 条：「再加几件可多享 Y 件（(X + Y) − n mod (X + Y)）」。
    """
    result = _price([PromoLine(0, 1000, 2, 1)], item_rules={1: BuyXGetYRule(1, 2, 1, 100)})

    assert _column(result, "item_discount") == [0, 0]
    assert result.item_discount_total == 0
    assert result.buy_x_get_y == (BuyXGetYProgress(1, 2, 1),)


def test_buy_x_get_y_counts_several_groups_and_takes_the_cheapest_units() -> None:
    """第 5 条：「k = ⌊n ÷ (X + Y)⌋ × Y。按单价从低到高……取前 k 件……
    即享受折扣的总是本规则范围内最便宜的件。」

    买 2 享 1、50%：n = 6，k = 2，取 300 仙与 500 仙各一件，折 150 与 250。
    """
    lines = [PromoLine(0, 800, 3, 1), PromoLine(1, 500, 2, 1), PromoLine(2, 300, 1, 1)]
    result = _price(lines, item_rules={1: BuyXGetYRule(1, 2, 1, 50)})

    assert _column(result, "item_discount") == [0, 0, 0, 250, 0, 150]
    assert result.buy_x_get_y == (BuyXGetYProgress(1, 6, 3),)


def test_buy_x_get_y_with_y_above_one_counts_y_units_per_group() -> None:
    """第 5 条：「k = ⌊n ÷ (X + Y)⌋ × Y」——买 1 享 2，n = 7 时 k = 4；
    第 10 条：再加 (1 + 2) − 7 mod 3 = 2 件可多享 2 件。
    """
    result = _price([PromoLine(0, 100, 7, 1)], item_rules={1: BuyXGetYRule(1, 1, 2, 100)})

    assert _column(result, "item_discount") == [100, 100, 100, 100, 0, 0, 0]
    assert result.buy_x_get_y == (BuyXGetYProgress(1, 7, 2),)


def test_buy_x_get_y_same_price_goes_by_line_order_then_unit_order() -> None:
    """第 5 条：「按单价从低到高、同价按行序与件序取前 k 件」。

    买 1 享 1：同为 500 仙的三件取第 2 行的两件（行序在前），不取第 3 行那件；
    同一行三件同价、买 2 享 1 时取件序最前的一件。
    """
    lines = [PromoLine(0, 600, 1, 1), PromoLine(1, 500, 2, 1), PromoLine(2, 500, 1, 1)]
    result = _price(lines, item_rules={1: BuyXGetYRule(1, 1, 1, 100)})
    assert _column(result, "item_discount") == [0, 500, 500, 0]

    one_line = _price([PromoLine(0, 500, 3, 1)], item_rules={1: BuyXGetYRule(1, 2, 1, 100)})
    assert _column(one_line, "item_discount") == [500, 0, 0]


def test_buy_x_get_y_mixes_units_across_lines_of_the_same_rule_only() -> None:
    """第 3 条：「同一规则范围内的商品可混搭计件」——其他规则与无规则的行不计入。

    规则 3 买 1 享 1 送：2,000 与 1,500 两行合为 n = 2，送较便宜的 1,500；
    规则 7 买 2 享 1：n = 2 不够；无规则的 300 仙行不算进任何一条。
    """
    lines = [
        PromoLine(0, 1000, 1, 7),
        PromoLine(1, 2000, 1, 3),
        PromoLine(2, 400, 1, 7),
        PromoLine(3, 300, 1, None),
        PromoLine(4, 1500, 1, 3),
    ]
    rules = {7: BuyXGetYRule(7, 2, 1, 50), 3: BuyXGetYRule(3, 1, 1, 100)}
    result = _price(lines, item_rules=rules)

    assert _column(result, "item_discount") == [0, 0, 0, 0, 1500]
    assert result.after_item_subtotal == 5200 - 1500
    assert result.buy_x_get_y == (BuyXGetYProgress(3, 2, 2), BuyXGetYProgress(7, 2, 1))


def test_buy_x_get_y_hundred_percent_is_free_and_half_sen_rounds_up() -> None:
    """第 3 条：「享受的折扣为 1–100 的整数百分比（100 即送，50 即半价）」；
    第 5 条：「每件折扣为该件单价 × 百分比 ÷ 100 按同一规则取整仙」。

    25 仙半价是 12.5 仙，得 13。
    """
    free = _price([PromoLine(0, 999, 2, 1)], item_rules={1: BuyXGetYRule(1, 1, 1, 100)})
    assert _column(free, "item_discount") == [999, 0]

    half = _price([PromoLine(0, 25, 2, 1)], item_rules={1: BuyXGetYRule(1, 1, 1, 50)})
    assert _column(half, "item_discount") == [13, 0]


def test_buy_x_get_y_progress_lists_every_rule_by_rule_id() -> None:
    """第 10 条：「每条买 X 享 Y 规则范围内的件数 n 与再加几件可多享 Y 件」。

    按规则 ID 升序；没有件命中的规则 n = 0、还差 X + Y；
    恰好凑满一组时还差一整组；打折规则不列出。
    """
    rules = {
        9: BuyXGetYRule(9, 3, 2, 100),
        4: BuyXGetYRule(4, 1, 1, 50),
        1: ItemDiscountRule(1, 10, None),
    }
    result = _price([PromoLine(0, 100, 2, 4), PromoLine(1, 100, 1, 1)], item_rules=rules)

    assert result.buy_x_get_y == (BuyXGetYProgress(4, 2, 2), BuyXGetYProgress(9, 0, 5))


# ---- 满额减或满额折（第 3 条、第 4 条（二）） ----


def test_spend_takes_the_highest_tier_reached() -> None:
    """第 3 条：「只取达到的最高一档」；第 4 条（二）：按 S1 判断，达到即大于或等于门槛。"""
    spend = SpendRule(SPEND_AMOUNT, ((1000, 100), (2000, 300), (3000, 600)))

    middle = _price([PromoLine(0, 2500, 1, None)], spend_rule=spend)
    assert middle.spend_tier_reached == 2
    assert middle.order_discount_total == 300
    assert middle.spend_next_gap_sen == 500

    top = _price([PromoLine(0, 3000, 1, None)], spend_rule=spend)
    assert top.spend_tier_reached == 3
    assert top.order_discount_total == 600
    assert top.spend_next_gap_sen is None

    first = _price([PromoLine(0, 1000, 1, None)], spend_rule=spend)
    assert first.spend_tier_reached == 1
    assert first.order_discount_total == 100
    assert first.spend_next_gap_sen == 1000


def test_spend_below_the_first_tier_gives_nothing() -> None:
    """第 4 条（二）：「按 S1 判断满额减或满额折的档」——一档也没达到时整单级优惠为 0；
    第 10 条：「满额减的当前档与下一档还差的金额」——这时下一档即第一档。
    """
    spend = SpendRule(SPEND_AMOUNT, ((1000, 100),))
    result = _price([PromoLine(0, 999, 1, None)], spend_rule=spend)

    assert result.spend_tier_reached is None
    assert result.order_discount_total == 0
    assert _column(result, "order_discount") == [0]
    assert result.spend_next_gap_sen == 1


def test_spend_and_free_shipping_thresholds_are_judged_on_s1_not_s0() -> None:
    """第 4 条：「两个门槛都按 S1 判断」。

    原价 1,200 仙打 8 折后 S1 为 960，够不到 1,000 的满额门槛与免运费门槛。
    """
    result = _price(
        [PromoLine(0, 1200, 1, 1)],
        item_rules={1: ItemDiscountRule(1, 20, None)},
        spend_rule=SpendRule(SPEND_AMOUNT, ((1000, 100),)),
        free_shipping_rule=FreeShippingRule(1000),
    )

    assert result.after_item_subtotal == 960
    assert result.spend_tier_reached is None
    assert result.spend_next_gap_sen == 40
    assert result.free_shipping_reached is False
    assert result.free_shipping_gap_sen == 40


def test_no_spend_rule_gives_no_tier_and_no_gap() -> None:
    """第 3 条：「至多一条满额减或满额折」——没有时整单级优惠为 0、没有档与差额。"""
    result = _price([PromoLine(0, 5000, 1, None)])

    assert result.spend_tier_reached is None
    assert result.spend_next_gap_sen is None
    assert result.order_discount_total == 0


def test_fixed_amount_tier_is_allocated_by_after_item_amount() -> None:
    """第 4 条（二）：「再按每件商品级折扣后的金额占 S1 的比例分摊到件」。

    满 RM20 减 RM2、满 RM50 减 RM7：S1 = 6,000 达到第二档，
    700 仙按 3,000、1,500、1,500 分为 350、175、175。
    """
    lines = [PromoLine(0, 3000, 1, None), PromoLine(1, 1500, 2, None)]
    result = _price(lines, spend_rule=SpendRule(SPEND_AMOUNT, ((2000, 200), (5000, 700))))

    assert result.spend_tier_reached == 2
    assert result.order_discount_total == 700
    assert _column(result, "order_discount") == [350, 175, 175]


def test_percent_tier_rounds_an_exact_half_sen_up_and_is_allocated() -> None:
    """第 4 条（二）：「为百分比时为 S1 × 百分比 ÷ 100 按同一规则取整仙」。

    S1 = 1,245 打 10% 是 124.5 仙，得 125；按 1,000、245 分为 100.40、24.60，
    余下 1 仙补给余数大的第二件。
    """
    lines = [PromoLine(0, 1000, 1, None), PromoLine(1, 245, 1, None)]
    result = _price(lines, spend_rule=SpendRule(SPEND_PERCENT, ((1000, 10), (2000, 20))))

    assert result.spend_tier_reached == 1
    assert result.order_discount_total == 125
    assert _column(result, "order_discount") == [100, 25]
    assert result.spend_next_gap_sen == 755


def test_percent_tier_is_computed_once_on_s1() -> None:
    """第 4 条（二）：「S1 × 百分比 ÷ 100」——对 S1 算一次，不逐件取整。

    三件各 5 仙、满 1 仙打 10%：15 × 10% = 1.5 → 2 仙；逐件各算会得 3 仙。
    """
    result = _price([PromoLine(0, 5, 3, None)], spend_rule=SpendRule(SPEND_PERCENT, ((1, 10),)))

    assert result.order_discount_total == 2
    assert _column(result, "order_discount") == [1, 1, 0]


# ---- 免运费与是否参加（第 3 条、第 4 条（三）与末段） ----


def test_free_shipping_threshold_zero_covers_every_order() -> None:
    """第 3 条：「一个门槛（0–99,999,999 仙，0 即本活动期间全部订单免运费）」。"""
    for lines in ([PromoLine(0, 100, 1, None)], [PromoLine(0, 0, 1, None)]):
        result = _price(lines, free_shipping_rule=FreeShippingRule(0), shipping_fee_sen=800)
        assert result.free_shipping_reached is True
        assert result.free_shipping_gap_sen == 0
        assert result.shipping_waived_sen == 800
        assert result.shipping_actual_sen == 0
        assert result.participates is True


def test_free_shipping_reached_exactly_at_the_threshold() -> None:
    """第 4 条（三）：「S1 达到门槛（大于或等于）时运费为 0，免去的运费记为原运费」。"""
    rule = FreeShippingRule(6000)

    exact = _price([PromoLine(0, 6000, 1, None)], free_shipping_rule=rule, shipping_fee_sen=800)
    assert exact.free_shipping_reached is True
    assert exact.free_shipping_gap_sen == 0
    assert (exact.shipping_actual_sen, exact.shipping_waived_sen) == (0, 800)

    short = _price([PromoLine(0, 5999, 1, None)], free_shipping_rule=rule, shipping_fee_sen=800)
    assert short.free_shipping_reached is False
    assert short.free_shipping_gap_sen == 1
    assert (short.shipping_actual_sen, short.shipping_waived_sen) == (800, 0)
    assert short.participates is False


def test_without_a_shipping_quote_only_the_threshold_is_reported() -> None:
    """第 4 条（三）：「还没有运费报价时只返回是否达到门槛」；
    末段：「按（一）（二）给出商品级与整单级优惠，……不判定是否参加活动」。
    """
    result = _example(shipping_fee_sen=None)

    assert result.free_shipping_reached is True
    assert result.free_shipping_gap_sen == 0
    assert result.shipping_fee_sen is None
    assert result.shipping_actual_sen is None
    assert result.shipping_waived_sen is None
    assert result.participates is None
    assert _column(result, "promotion_discount") == [656, 656, 94, 94, 900]


def test_no_free_shipping_rule_gives_no_gap() -> None:
    """第 3 条：「至多一条满额免运费」——没有时不免运费，也没有免运费还差的金额。"""
    result = _price(shipping_fee_sen=800)

    assert result.free_shipping_reached is False
    assert result.free_shipping_gap_sen is None
    assert (result.shipping_actual_sen, result.shipping_waived_sen) == (800, 0)


def test_no_benefit_from_any_rule_means_not_participating() -> None:
    """第 4 条：「本单所有规则都没有带来优惠（商品级折扣、整单级优惠与免去的运费都为 0）
    时，视为不参加活动」。

    规则都在，但买 2 享 1 只有 2 件、满额与免运费都没达到；
    另一例达到免运费但运费报价为 0；再一例没有任何规则与商品。
    """
    nothing = _price(
        [PromoLine(0, 1000, 2, 1)],
        item_rules={1: BuyXGetYRule(1, 2, 1, 100)},
        spend_rule=SpendRule(SPEND_AMOUNT, ((5000, 500),)),
        free_shipping_rule=FreeShippingRule(6000),
        shipping_fee_sen=800,
    )
    assert nothing.item_discount_total == nothing.order_discount_total == 0
    assert nothing.shipping_waived_sen == 0
    assert nothing.participates is False

    zero_fee = _price(free_shipping_rule=FreeShippingRule(0), shipping_fee_sen=0)
    assert zero_fee.free_shipping_reached is True
    assert zero_fee.shipping_waived_sen == 0
    assert zero_fee.participates is False

    empty = _price([], shipping_fee_sen=800)
    assert empty.units == ()
    assert empty.subtotal == empty.after_item_subtotal == 0
    assert empty.participates is False


def test_any_single_benefit_means_participating() -> None:
    """第 4 条末段：商品级折扣、整单级优惠与免去的运费三者任一大于 0，即不是
    「所有规则都没有带来优惠」，本单参加活动。
    """
    only_shipping = _price(free_shipping_rule=FreeShippingRule(1000), shipping_fee_sen=1)
    assert only_shipping.participates is True

    only_order = _price(spend_rule=SpendRule(SPEND_AMOUNT, ((1000, 1),)), shipping_fee_sen=800)
    assert only_order.participates is True

    only_item = _price(
        [PromoLine(0, 1000, 1, 1)],
        item_rules={1: ItemDiscountRule(1, None, 1)},
        shipping_fee_sen=800,
    )
    assert only_item.participates is True


def test_results_are_frozen() -> None:
    """「计价、优惠、积分与库存」第 2 条：「整单及逐件分摊快照不可因后来改价而重算」。"""
    result = _example()

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.subtotal = 0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.units[0].item_discount = 0  # type: ignore[misc]


# ---- 参数校验（第 3、4 条） ----


def _spend(mode, *tiers):
    return _price(spend_rule=SpendRule(mode, tiers))


def _rule(rule):
    return _price(item_rules={1: rule})


@pytest.mark.parametrize(
    "call",
    [
        # 第 4 条：lines 只含正常行——行序严格递增，单价非负，件数为正，规则须已给出。
        lambda: _price([PromoLine(-1, 1000, 1, None)]),
        lambda: _price([PromoLine(1, 100, 1, None), PromoLine(1, 100, 1, None)]),
        lambda: _price([PromoLine(2, 100, 1, None), PromoLine(1, 100, 1, None)]),
        lambda: _price([PromoLine(0, -1, 1, None)]),
        lambda: _price([PromoLine(0, 1000, 0, None)]),
        lambda: _price([PromoLine(0, 1000, 1, 0)]),
        lambda: _price([PromoLine(0, 1000, 1, 5)]),
        lambda: _price(shipping_fee_sen=-1),
        # 第 3 条：商品级打折。
        lambda: _rule(ItemDiscountRule(1, 0, None)),
        lambda: _rule(ItemDiscountRule(1, 91, None)),
        lambda: _rule(ItemDiscountRule(1, None, 0)),
        lambda: _rule(ItemDiscountRule(1, None, 50_000_001)),
        lambda: _rule(ItemDiscountRule(1, 10, 100)),
        lambda: _rule(ItemDiscountRule(1, None, None)),
        lambda: _rule(ItemDiscountRule(2, 10, None)),
        lambda: _price(item_rules={0: ItemDiscountRule(0, 10, None)}),
        # 第 3 条：买 X 享 Y。
        lambda: _rule(BuyXGetYRule(1, 0, 1, 100)),
        lambda: _rule(BuyXGetYRule(1, 11, 1, 100)),
        lambda: _rule(BuyXGetYRule(1, 1, 0, 100)),
        lambda: _rule(BuyXGetYRule(1, 1, 11, 100)),
        lambda: _rule(BuyXGetYRule(1, 1, 1, 0)),
        lambda: _rule(BuyXGetYRule(1, 1, 1, 101)),
        # 第 3 条：满额减或满额折。
        lambda: _spend("fixed", (100, 1)),
        lambda: _spend(SPEND_AMOUNT),
        lambda: _spend(SPEND_PERCENT, *((100 * n, n) for n in range(1, 7))),
        lambda: _spend(SPEND_AMOUNT, (0, 1)),
        lambda: _spend(SPEND_AMOUNT, (100_000_000, 1)),
        lambda: _spend(SPEND_AMOUNT, (100, 100)),
        lambda: _spend(SPEND_AMOUNT, (100, 0)),
        lambda: _spend(SPEND_PERCENT, (100, 91)),
        lambda: _spend(SPEND_PERCENT, (100, 0)),
        lambda: _spend(SPEND_AMOUNT, (200, 10), (200, 20)),
        lambda: _spend(SPEND_AMOUNT, (200, 10), (100, 20)),
        lambda: _spend(SPEND_AMOUNT, (100, 20), (200, 20)),
        lambda: _spend(SPEND_PERCENT, (100, 20), (200, 10)),
        lambda: _spend(SPEND_AMOUNT, (100, 1, 2)),
        # 第 3 条：满额免运费。
        lambda: _price(free_shipping_rule=FreeShippingRule(-1)),
        lambda: _price(free_shipping_rule=FreeShippingRule(100_000_000)),
    ],
)
def test_values_outside_the_design_ranges_are_rejected(call) -> None:
    """第 3 条的取值范围——「1–90 的整数」「1–50,000,000 仙」「X 为 1–10、Y 为 1–10」
    「1–100 的整数百分比」「1–5 档」「门槛（1–99,999,999 仙）」「1 仙到小于门槛」
    「门槛与优惠都须逐档严格递增」「0–99,999,999 仙」——与第 4 条「只算计价结果里的
    正常行」所需的行序、件数与规则；超出即拒绝，不截断。
    """
    with pytest.raises(ValueError):
        call()


def test_range_bounds_are_accepted() -> None:
    """第 3 条：取值范围的两端都可用（打折 90%、直降 50,000,000 仙、X 与 Y 为 1 与 10、
    享受 1% 与 100%、5 档、门槛 1 与 99,999,999 仙、固定金额为门槛减 1 仙）。
    """
    rules = {
        1: ItemDiscountRule(1, 90, None),
        2: ItemDiscountRule(2, None, 50_000_000),
        3: BuyXGetYRule(3, 10, 10, 100),
        4: BuyXGetYRule(4, 1, 1, 1),
    }
    tiers = ((2, 1), (3, 2), (4, 3), (5, 4), (99_999_999, 99_999_998))
    result = _price(
        item_rules=rules,
        spend_rule=SpendRule(SPEND_AMOUNT, tiers),
        free_shipping_rule=FreeShippingRule(99_999_999),
        shipping_fee_sen=0,
    )
    assert result.spend_tier_reached == 4

    percent = _spend(SPEND_PERCENT, (1, 1), (99_999_999, 90))
    assert percent.order_discount_total == 10


@pytest.mark.parametrize(
    "call",
    [
        lambda: _price([PromoLine(0, 1000.0, 1, None)]),
        lambda: _price([PromoLine(0, 1000, 1.0, None)]),
        lambda: _price([PromoLine(0, 1000, True, None)]),
        lambda: _price([PromoLine(0.0, 1000, 1, None)]),
        lambda: _price([PromoLine(0, 1000, 1, True)], item_rules=EXAMPLE_ITEM_RULES),
        lambda: _price([(0, 1000, 1, None)]),
        lambda: _price(item_rules=[ItemDiscountRule(1, 10, None)]),
        lambda: _price(item_rules={"1": ItemDiscountRule(1, 10, None)}),
        lambda: _rule(FreeShippingRule(0)),
        lambda: _rule(ItemDiscountRule(1, 10.0, None)),
        lambda: _rule(ItemDiscountRule(1, None, 100.0)),
        lambda: _rule(BuyXGetYRule(1, True, 1, 100)),
        lambda: _rule(BuyXGetYRule(1, 1, 1, 50.0)),
        lambda: _price(spend_rule=FreeShippingRule(100)),
        lambda: _spend(1, (100, 1)),
        lambda: _price(spend_rule=SpendRule(SPEND_AMOUNT, [(100, 1)])),
        lambda: _spend(SPEND_AMOUNT, [100, 1]),
        lambda: _spend(SPEND_AMOUNT, (100.0, 1)),
        lambda: _spend(SPEND_PERCENT, (100, 10.0)),
        lambda: _price(free_shipping_rule=SpendRule(SPEND_AMOUNT, ())),
        lambda: _price(free_shipping_rule=FreeShippingRule(0.0)),
        lambda: _price(shipping_fee_sen=800.0),
        lambda: _price(shipping_fee_sen=False),
    ],
)
def test_wrong_types_are_rejected(call) -> None:
    """第 3 条：金额以整仙、百分比与件数以整数给出（「1–90 的整数」「1–10 的整数」）。

    float、bool 与不对的类型一律拒绝，不隐式转换。
    """
    with pytest.raises(TypeError):
        call()

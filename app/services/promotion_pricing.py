"""节日促销活动的计价纯函数。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 3–5、8、10 条。
只用标准库，不碰数据库；金额一律整数仙。
规则怎样从数据库读出、一行命中哪条商品级规则（商品改分类后重叠时取排序数较小的规则）、
哪些行是正常行，都由调用方决定后传入：lines 只含正常行（第 4 条）。
舍入与分摊复用 app.services.pricing 的 percent_coupon_discount（四舍五入，恰好半仙进一仙）
与 allocate_largest_remainder（最大余数、稳定序）。
规则里的百分比都是 int，算折扣时才转成 Decimal；任何参数都不接受 float 与 bool。
优惠券、积分与参加与不参加活动的取更优不在这里（第 4 条（四）与第 6 条）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.services.pricing import allocate_largest_remainder, percent_coupon_discount

# 满额减或满额折的方式（第 3 条）：每档减固定金额或打百分比。
SPEND_AMOUNT = "amount"
SPEND_PERCENT = "percent"
SPEND_MODES = (SPEND_AMOUNT, SPEND_PERCENT)

# 第 3 条的取值范围。
PERCENT_MIN = 1
DISCOUNT_PERCENT_MAX = 90
GET_PERCENT_MAX = 100
ITEM_AMOUNT_MAX_SEN = 50_000_000
QUANTITY_MIN = 1
QUANTITY_MAX = 10
THRESHOLD_MAX_SEN = 99_999_999
SPEND_TIERS_MAX = 5


@dataclass(frozen=True)
class PromoLine:
    """计价结果里的一条正常行：行序、每件原价（仙）、件数与命中的商品级规则 ID（可空）。"""

    line_index: int
    unit_price: int
    quantity: int
    rule_id: int | None


@dataclass(frozen=True)
class ItemDiscountRule:
    """商品级打折：percent（1–90）与 amount_sen（每件直降，仙）恰有一个不为 None。"""

    rule_id: int
    percent: int | None
    amount_sen: int | None


@dataclass(frozen=True)
class BuyXGetYRule:
    """买 X 享 Y：享受的件按 get_percent（1–100）打折。"""

    rule_id: int
    buy_quantity: int
    get_quantity: int
    get_percent: int


@dataclass(frozen=True)
class SpendRule:
    """满额减或满额折：mode 为 SPEND_AMOUNT 或 SPEND_PERCENT。

    tiers 为 (threshold_sen, value) 的元组，value 为该档减的仙数或百分比。
    """

    mode: str
    tiers: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class FreeShippingRule:
    threshold_sen: int


@dataclass(frozen=True)
class PromoUnit:
    """展开后的一件商品的活动优惠；rule_id 为该行命中的规则，不论这一件是否享受折扣。"""

    line_index: int
    unit_index: int
    original_price: int
    item_discount: int
    order_discount: int
    promotion_discount: int
    rule_id: int | None


@dataclass(frozen=True)
class BuyXGetYProgress:
    """第 10 条：规则范围内的件数 n 与再加几件可多享 Y 件。"""

    rule_id: int
    units_in_scope: int
    units_to_next: int


@dataclass(frozen=True)
class PromotionPricing:
    units: tuple[PromoUnit, ...]
    subtotal: int
    item_discount_total: int
    after_item_subtotal: int
    order_discount_total: int
    spend_tier_reached: int | None
    spend_next_gap_sen: int | None
    free_shipping_reached: bool
    free_shipping_gap_sen: int | None
    shipping_fee_sen: int | None
    shipping_actual_sen: int | None
    shipping_waived_sen: int | None
    participates: bool | None
    buy_x_get_y: tuple[BuyXGetYProgress, ...]


def _require_int(name: str, value: object, *, minimum: int, maximum: int | None = None) -> int:
    # bool 是 int 的子类，但把 True 当 1 仙显然是调用错误。
    if type(value) is not int:
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {bound}, got {value}")
    return value


def _check_item_rule(key: object, rule: object) -> None:
    _require_int("item_rules key", key, minimum=1)
    name = f"item_rules[{key}]"
    if isinstance(rule, ItemDiscountRule):
        _require_int(f"{name}.rule_id", rule.rule_id, minimum=1)
        if (rule.percent is None) == (rule.amount_sen is None):
            raise ValueError(f"{name} needs exactly one of percent and amount_sen")
        if rule.percent is not None:
            _require_int(
                f"{name}.percent", rule.percent, minimum=PERCENT_MIN, maximum=DISCOUNT_PERCENT_MAX
            )
        else:
            _require_int(
                f"{name}.amount_sen", rule.amount_sen, minimum=1, maximum=ITEM_AMOUNT_MAX_SEN
            )
    elif isinstance(rule, BuyXGetYRule):
        _require_int(f"{name}.rule_id", rule.rule_id, minimum=1)
        _require_int(
            f"{name}.buy_quantity", rule.buy_quantity, minimum=QUANTITY_MIN, maximum=QUANTITY_MAX
        )
        _require_int(
            f"{name}.get_quantity", rule.get_quantity, minimum=QUANTITY_MIN, maximum=QUANTITY_MAX
        )
        _require_int(
            f"{name}.get_percent", rule.get_percent, minimum=PERCENT_MIN, maximum=GET_PERCENT_MAX
        )
    else:
        raise TypeError(
            f"{name} must be ItemDiscountRule or BuyXGetYRule, got {type(rule).__name__}"
        )
    if rule.rule_id != key:
        raise ValueError(f"{name}.rule_id is {rule.rule_id}, not its key")


def _check_spend_rule(rule: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(rule, SpendRule):
        raise TypeError(f"spend_rule must be SpendRule, got {type(rule).__name__}")
    if type(rule.mode) is not str:
        raise TypeError(f"spend_rule.mode must be str, got {type(rule.mode).__name__}")
    if rule.mode not in SPEND_MODES:
        raise ValueError(f"spend_rule.mode must be one of {SPEND_MODES}, got {rule.mode!r}")
    if type(rule.tiers) is not tuple:
        raise TypeError(f"spend_rule.tiers must be tuple, got {type(rule.tiers).__name__}")
    if not 1 <= len(rule.tiers) <= SPEND_TIERS_MAX:
        raise ValueError(f"spend_rule needs 1 to {SPEND_TIERS_MAX} tiers, got {len(rule.tiers)}")

    previous: tuple[int, int] | None = None
    for i, tier in enumerate(rule.tiers):
        name = f"spend_rule.tiers[{i}]"
        if type(tier) is not tuple:
            raise TypeError(f"{name} must be tuple, got {type(tier).__name__}")
        if len(tier) != 2:
            raise ValueError(f"{name} must be (threshold_sen, value)")
        threshold = _require_int(f"{name} threshold", tier[0], minimum=1, maximum=THRESHOLD_MAX_SEN)
        if rule.mode == SPEND_AMOUNT:
            # 每档减固定金额：1 仙到小于门槛。
            value = _require_int(f"{name} amount", tier[1], minimum=1, maximum=threshold - 1)
        else:
            value = _require_int(
                f"{name} percent", tier[1], minimum=PERCENT_MIN, maximum=DISCOUNT_PERCENT_MAX
            )
        if previous is not None and (threshold <= previous[0] or value <= previous[1]):
            raise ValueError(f"{name} threshold and value must both exceed the previous tier")
        previous = (threshold, value)
    return rule.tiers


def price_promotion(
    lines: Sequence[PromoLine],
    *,
    item_rules: Mapping[int, ItemDiscountRule | BuyXGetYRule],
    spend_rule: SpendRule | None = None,
    free_shipping_rule: FreeShippingRule | None = None,
    shipping_fee_sen: int | None = None,
) -> PromotionPricing:
    """按第 4 条（一）至（三）算出每件的活动优惠、两个门槛与凑单提示所需的数。

    shipping_fee_sen 为 None（还没有运费报价）时只给出是否达到免运费门槛，
    实际运费、免去的运费与是否参加活动都为 None。
    """
    if not isinstance(item_rules, Mapping):
        raise TypeError(f"item_rules must be a mapping, got {type(item_rules).__name__}")
    for key, rule in item_rules.items():
        _check_item_rule(key, rule)
    tiers = _check_spend_rule(spend_rule) if spend_rule is not None else ()
    if free_shipping_rule is not None:
        if not isinstance(free_shipping_rule, FreeShippingRule):
            kind = type(free_shipping_rule).__name__
            raise TypeError(f"free_shipping_rule must be FreeShippingRule, got {kind}")
        _require_int(
            "free_shipping_rule.threshold_sen",
            free_shipping_rule.threshold_sen,
            minimum=0,
            maximum=THRESHOLD_MAX_SEN,
        )
    if shipping_fee_sen is not None:
        _require_int("shipping_fee_sen", shipping_fee_sen, minimum=0)

    prices: list[int] = []
    positions: list[tuple[int, int]] = []
    rule_ids: list[int | None] = []
    previous_index: int | None = None
    for i, line in enumerate(lines):
        if not isinstance(line, PromoLine):
            raise TypeError(f"lines[{i}] must be PromoLine, got {type(line).__name__}")
        line_index = _require_int(f"lines[{i}].line_index", line.line_index, minimum=0)
        if previous_index is not None and line_index <= previous_index:
            raise ValueError(f"lines[{i}].line_index {line_index} is not after {previous_index}")
        previous_index = line_index
        unit_price = _require_int(f"lines[{i}].unit_price", line.unit_price, minimum=0)
        quantity = _require_int(f"lines[{i}].quantity", line.quantity, minimum=1)
        if line.rule_id is not None:
            _require_int(f"lines[{i}].rule_id", line.rule_id, minimum=1)
            if line.rule_id not in item_rules:
                raise ValueError(f"lines[{i}].rule_id {line.rule_id} is not in item_rules")
        for unit_index in range(quantity):
            prices.append(unit_price)
            positions.append((line_index, unit_index))
            rule_ids.append(line.rule_id)
    subtotal = sum(prices)

    # （一）商品级：打折逐件算；买 X 享 Y 先按规则收齐范围内的件（展开序即行序、件序）。
    item_discounts = [0] * len(prices)
    in_scope: dict[int, list[int]] = {}
    for i, rule_id in enumerate(rule_ids):
        if rule_id is None:
            continue
        rule = item_rules[rule_id]
        if isinstance(rule, ItemDiscountRule):
            if rule.percent is not None:
                item_discounts[i] = percent_coupon_discount(prices[i], Decimal(rule.percent))
            else:
                item_discounts[i] = min(rule.amount_sen, prices[i])
        else:
            in_scope.setdefault(rule_id, []).append(i)

    progress: list[BuyXGetYProgress] = []
    for rule_id in sorted(k for k, r in item_rules.items() if isinstance(r, BuyXGetYRule)):
        rule = item_rules[rule_id]
        scope = in_scope.get(rule_id, [])
        group = rule.buy_quantity + rule.get_quantity
        chosen = len(scope) // group * rule.get_quantity
        # 第 5 条：按单价从低到高、同价按行序与件序取前 k 件。
        for i in sorted(scope, key=lambda j: (prices[j], j))[:chosen]:
            item_discounts[i] = percent_coupon_discount(prices[i], Decimal(rule.get_percent))
        progress.append(BuyXGetYProgress(rule_id, len(scope), group - len(scope) % group))

    after_item = [p - d for p, d in zip(prices, item_discounts, strict=True)]
    item_discount_total = sum(item_discounts)
    after_item_subtotal = subtotal - item_discount_total

    # （二）整单级：门槛逐档严格递增，达到的档数即最高一档的序号。
    reached = sum(1 for threshold, _ in tiers if after_item_subtotal >= threshold)
    order_discount_total = 0
    if reached:
        value = tiers[reached - 1][1]
        if spend_rule.mode == SPEND_AMOUNT:
            order_discount_total = min(value, after_item_subtotal)
        else:
            order_discount_total = percent_coupon_discount(after_item_subtotal, Decimal(value))
    order_discounts = allocate_largest_remainder(order_discount_total, after_item)
    spend_next_gap_sen = None
    if reached < len(tiers):
        spend_next_gap_sen = tiers[reached][0] - after_item_subtotal

    # （三）免运费：按 S1 判断，与整单级优惠无关。
    if free_shipping_rule is None:
        free_shipping_reached = False
        free_shipping_gap_sen = None
    else:
        free_shipping_reached = after_item_subtotal >= free_shipping_rule.threshold_sen
        free_shipping_gap_sen = max(free_shipping_rule.threshold_sen - after_item_subtotal, 0)

    if shipping_fee_sen is None:
        shipping_actual_sen = shipping_waived_sen = None
        participates = None
    else:
        shipping_waived_sen = shipping_fee_sen if free_shipping_reached else 0
        shipping_actual_sen = shipping_fee_sen - shipping_waived_sen
        participates = item_discount_total + order_discount_total + shipping_waived_sen > 0

    units = tuple(
        PromoUnit(
            line_index=line_index,
            unit_index=unit_index,
            original_price=prices[i],
            item_discount=item_discounts[i],
            order_discount=order_discounts[i],
            promotion_discount=item_discounts[i] + order_discounts[i],
            rule_id=rule_ids[i],
        )
        for i, (line_index, unit_index) in enumerate(positions)
    )
    return PromotionPricing(
        units=units,
        subtotal=subtotal,
        item_discount_total=item_discount_total,
        after_item_subtotal=after_item_subtotal,
        order_discount_total=order_discount_total,
        spend_tier_reached=reached or None,
        spend_next_gap_sen=spend_next_gap_sen,
        free_shipping_reached=free_shipping_reached,
        free_shipping_gap_sen=free_shipping_gap_sen,
        shipping_fee_sen=shipping_fee_sen,
        shipping_actual_sen=shipping_actual_sen,
        shipping_waived_sen=shipping_waived_sen,
        participates=participates,
        buy_x_get_y=tuple(progress),
    )

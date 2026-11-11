# SHOP-TASK-231 节日促销活动的计价纯函数（商品级、买 X 享 Y、满额与免运费）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 3–5、8、10 条。纯函数，只用标准库，不碰数据库，只算金额，不碰个人资料。规则从数据库的读取、一行命中哪条商品级规则（含商品改分类后重叠时取排序数较小的规则）、哪些行是正常行，都由之后的服务层决定后传入。不改 `app/services/pricing.py`（券与积分接入活动与取更优方案见 SHOP-TASK-232）；不改数据库、接口、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新建 `app/services/promotion_pricing.py`，公开的数据类（都是 `@dataclass(frozen=True)`）与函数：

  ```python
  SPEND_AMOUNT = "amount"
  SPEND_PERCENT = "percent"

  PromoLine(line_index: int, unit_price: int, quantity: int, rule_id: int | None)
  ItemDiscountRule(rule_id: int, percent: int | None, amount_sen: int | None)
  BuyXGetYRule(rule_id: int, buy_quantity: int, get_quantity: int, get_percent: int)
  SpendRule(mode: str, tiers: tuple[tuple[int, int], ...])  # (threshold_sen, value)
  FreeShippingRule(threshold_sen: int)

  PromoUnit(line_index, unit_index, original_price, item_discount, order_discount,
            promotion_discount, rule_id)
  BuyXGetYProgress(rule_id, units_in_scope, units_to_next)
  PromotionPricing(units: tuple[PromoUnit, ...], subtotal, item_discount_total,
                   after_item_subtotal, order_discount_total,
                   spend_tier_reached: int | None, spend_next_gap_sen: int | None,
                   free_shipping_reached: bool, free_shipping_gap_sen: int | None,
                   shipping_fee_sen: int | None, shipping_actual_sen: int | None,
                   shipping_waived_sen: int | None, participates: bool | None,
                   buy_x_get_y: tuple[BuyXGetYProgress, ...])

  def price_promotion(
      lines: Sequence[PromoLine],
      *,
      item_rules: Mapping[int, ItemDiscountRule | BuyXGetYRule],
      spend_rule: SpendRule | None = None,
      free_shipping_rule: FreeShippingRule | None = None,
      shipping_fee_sen: int | None = None,
  ) -> PromotionPricing
  ```

  另导出第 3 条的取值上下限常量 `PERCENT_MIN`、`DISCOUNT_PERCENT_MAX`、`GET_PERCENT_MAX`、`ITEM_AMOUNT_MAX_SEN`、`QUANTITY_MIN`、`QUANTITY_MAX`、`THRESHOLD_MAX_SEN`、`SPEND_TIERS_MAX` 与 `SPEND_MODES`。
- [x] 计算：
  - 商品级按行序、件序展开；打折百分比每件为 `percent_coupon_discount(单价, Decimal(percent))`，直降每件取直降额与单价较小者；买 X 享 Y 把同一规则的件合在一起共 n 件，k = ⌊n ÷ (X + Y)⌋ × Y，按（单价, 展开序）升序取前 k 件，每件 `percent_coupon_discount(单价, Decimal(get_percent))`。S1 = S0 − 商品级折扣合计。
  - 整单级按 S1 取达到的最高一档：固定金额取该档金额与 S1 较小者，百分比为 `percent_coupon_discount(S1, Decimal(value))`；再用 `allocate_largest_remainder` 按每件商品级折扣后的金额分摊到件。
  - 免运费：有规则且 S1 ≥ 门槛即 `free_shipping_reached`；`shipping_fee_sen` 为 None 时实际运费、免去的运费与 `participates` 都为 None；有报价时达到门槛免去的运费等于报价，`participates` 为三项优惠之和大于 0。
  - 凑单提示：`spend_tier_reached` 从 1 起，`spend_next_gap_sen` 为下一档门槛减 S1（已到最高档或没有满额规则为 None）；`free_shipping_gap_sen` 为 max(门槛 − S1, 0)（没有规则为 None）；`buy_x_get_y` 列出 `item_rules` 里每条买 X 享 Y 规则（含没有件命中的，n = 0），按 rule_id 升序，`units_to_next` = (X + Y) − n mod (X + Y)。
- [x] 参数校验照 `pricing.py` 的 `_require_non_negative_int` 写法（`type(value) is not int` 即 TypeError，不接受 float 与 bool），另写带上下限的 `_require_int`：line_index 非负且严格递增、unit_price 非负、quantity 为正、rule_id 为正或 None 且须在 `item_rules` 里、shipping_fee_sen 非负或 None；规则取值按第 3 条（打折 1–90、直降 1–50,000,000 仙、X 与 Y 1–10、享受 1–100、1–5 档、门槛 1–99,999,999 仙、固定金额 1 仙到小于门槛、满额折 1–90、门槛与优惠逐档严格递增、免运费门槛 0–99,999,999 仙）。超出范围抛 ValueError，类型不对抛 TypeError。
- [x] 测试 `tests/test_promotion_pricing.py`：第 8 条算例（一）至（三）逐仙核对；打折百分比恰好半仙进一仙、逐件取整、直降大于单价；买 X 享 Y 的 n 不足 X + Y、多组、Y 大于 1、同价按行序件序、跨行混搭与只计同一规则、享受 100% 与半价半仙进一仙、进度按 rule_id 升序；满额取最高档、一档未达、门槛按 S1 判断、没有满额规则、固定金额档与百分比档的分摊（含百分比恰好半仙进一仙、对 S1 只算一次）；免运费门槛 0、恰好等于门槛与差 1 仙、没有运费报价、没有免运费规则；所有规则都没带来优惠（含运费报价为 0）时 `participates` 为 False、任一项优惠即为 True；结果不可改；取值越界与类型错误。每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - `item_rules` 的键须是正整数且等于该规则的 `rule_id`，不等抛 ValueError；验收没有单列，避免服务层组装映射时键与规则错位。
  - `item_rules` 里的规则即使没有行命中也照样校验，并出现在 `buy_x_get_y` 里（n = 0、还差 X + Y），供购物车显示「再加几件」。
  - 满额规则的 `mode` 不是字符串、`tiers` 或其中一档不是元组时抛 TypeError；一档不是两项时抛 ValueError。
  - 没有免运费规则时 `free_shipping_reached` 为 False（有运费报价时免去 0、实际等于报价）。
  - 固定金额档按第 3 条小于门槛、门槛又不大于 S1，所以「取该档金额与 S1 较小者」在合格输入下总取该档金额；仍照第 4 条（二）写出。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 第 3–5、8、10 条，手算核对第 8 条算例与各测试里的逐件数字。全部后端测试、ruff check 与 ruff format 由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

# SHOP-TASK-232 优惠券与积分接入活动优惠、参加与不参加活动的取更优（纯函数）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「计价、优惠、积分与库存」第 1、2 条与「节日促销活动」第 6–8 条。纯函数，只用标准库，不碰数据库，只算金额，不碰个人资料。券是否在有效期、次数是否用完仍由 `app/services/coupons.py` 判断，由之后的服务层先判断，再把券的优惠与最低消费传进来；`choose_promotion_plan` 何时调用（本单参加活动、且会员选了券或填了积分，第 6 条）也由服务层判断。不改 `app/services/promotion_pricing.py`、`coupons.py` 与下单服务、数据库、接口、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `app/services/pricing.py` 的 `price_order` 新增只限关键字的参数，现有参数与调用方不变：

  ```python
  def price_order(
      lines: Sequence[OrderLine],
      *,
      promotion_discounts: Sequence[int] | None = None,  # 展开后每件的活动优惠，None 即每件为 0
      percent_off: Decimal | None = None,
      fixed_off: int | None = None,
      points_redeemed: int = 0,
  ) -> PricingResult
  ```

  - `promotion_discounts` 按行序、件序逐件一项（即 `price_promotion(...).units` 的 `promotion_discount`），长度须等于展开后的件数、每项为不超过该件原价的非负整数，否则 ValueError；float、bool 与其他类型 TypeError。
  - 活动后金额 = 原价 − 活动优惠；百分比券对整单活动后商品额算一次，固定券取券额与整单活动后商品额较小者；券按每件活动后金额分摊，积分按券后金额分摊（超过券后商品额照旧 ValueError）；每件现金实付 = 原价 − 活动优惠 − 分摊券额 − 分摊积分额；获得积分照第 3 条。最低消费不在这里判断。
  - `UnitAllocation` 在 `points_earned` 之后加 `promotion_discount`；`PricingResult` 在 `points_earned` 之后加 `promotion_discount`（活动优惠合计）。`subtotal` 仍为原价小计，`cash_total` = `subtotal` − `promotion_discount` − `coupon_discount` − `points_redeemed`。不传或全为 0 时结果与改动前逐字段相同（活动优惠字段为 0）。
- [x] 新增数据类（都是 `@dataclass(frozen=True)`）与函数：

  ```python
  CouponTerms(percent_off: Decimal | None, fixed_off: int | None, min_spend_sen: int)
  PromotionPlan(use_promotion: bool, use_coupon: bool, points_allowed: bool,
                promotion_pre_points_total: int, other_pre_points_total: int)

  def choose_promotion_plan(
      lines: Sequence[OrderLine],
      promotion_discounts: Sequence[int],
      *,
      shipping_fee_sen: int,       # 未免的运费
      shipping_waived_sen: int,    # 活动免去的运费，不超过前者
      coupon: CouponTerms | None,
      coupon_combinable: bool,
      points_combinable: bool,
  ) -> PromotionPlan
  ```

  - `CouponTerms.__post_init__`：`percent_off` 与 `fixed_off` 恰有一个不为 None，`fixed_off` 与 `min_spend_sen` 为非负整数，`percent_off` 为 0–100 的有限 Decimal；不对抛 ValueError，类型不对抛 TypeError。
  - 甲：活动后商品额 = 原价小计 − 活动优惠合计；`coupon` 不为 None、`coupon_combinable` 为 True 且活动后商品额 ≥ 最低消费才用券，券折扣口径同 `price_order`；积分前应付 = 活动后商品额 − 券折扣 + 未免的运费 − 免去的运费。
  - 乙：`coupon` 不为 None 且原价小计 ≥ 最低消费才用券；积分前应付 = 原价小计 − 券折扣 + 未免的运费。
  - 取较低者，相同取甲。`use_coupon` 为所取方案是否用券；`points_allowed` 取甲时为 `points_combinable`、取乙时为 True；`promotion_pre_points_total`、`other_pre_points_total` 分别为甲、乙的积分前应付。
  - 服务层取甲时以 `promotion_discounts` 调 `price_order`（用券时传券的 `percent_off` 或 `fixed_off`），实际运费为未免的运费减免去的运费；取乙时不传 `promotion_discounts`，实际运费为未免的运费，订单不记活动。
- [x] 测试：`tests/test_pricing.py` 加 `promotion_discounts` 的长度、负数、超过原价、float 与 bool 的校验，等于原价可接受，全为 0 与不传逐字段相同，固定券大于活动后商品额时取后者，百分比券按活动后金额计算并分摊（含积分与获得积分的逐件数字）；现有测试不改。新文件 `tests/test_promotion_plans.py` 以 SHOP-TASK-231 的 `price_promotion` 走通第 8 条：首例（甲 5310、乙 8270 取甲，`price_order` 的券分摊、积分分摊、现金实付、应付 4310 与获得积分逐件核对）、关闭券同用（甲 5900、乙 8270 取甲且不用券）、D 例（甲 18800、乙 17800 取乙）、第三例（甲 10700、乙 5800 取乙）、只关闭积分同用（取甲且 `points_allowed` 为 False）、两方案相同取甲；另有甲够不到最低消费仍取甲时不用券、只填积分没有券、结果不可改，以及 `CouponTerms` 与 `choose_promotion_plan` 的取值与类型校验。每条测试的文档字符串写明它守住的第几条哪一句。
- 偏离与落实细节：
  - `choose_promotion_plan` 的 `coupon_combinable` 与 `points_combinable` 不是 bool（含 0、1）时抛 TypeError；`coupon` 不是 `CouponTerms` 或 None 时抛 TypeError；`promotion_discounts` 为 None 时抛 TypeError（这里是必填参数，不同于 `price_order` 的默认 None）。验收没有单列，照现有「类型不对抛 TypeError」的写法补上。
  - 百分比的校验从 `percent_coupon_discount` 抽成 `_require_percent`，供 `CouponTerms` 共用；行的展开、活动优惠的校验与券折扣的计算也抽成私有函数，`price_order` 与 `choose_promotion_plan` 共用，保证「口径同 price_order」。
  - 模块文档字符串的设计依据由 1.6（提交 845fd93）改为 1.23（提交 `b7f9355`）；`tests/test_pricing.py` 现有测试的文档字符串不改，新增测试注明引用 1.23。
- 验证到什么程度：人工逐条对照验收标准与 DESIGN 1.23 上述条文，手算核对第 8 条各例与新增测试里的逐件数字。全部后端测试、ruff check 与 ruff format 由 PR 的必需 CI 检查 `backend` 执行。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

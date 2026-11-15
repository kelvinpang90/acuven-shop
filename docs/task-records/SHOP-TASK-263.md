# SHOP-TASK-263 前台计价模块的活动字段（游客与会员，只改类型）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 4、6、10 条：只加读取计价结果的类型，不计算金额，不碰个人资料。依赖 SHOP-TASK-234（游客计价）与 SHOP-TASK-236（会员计价）。不改页面、后端、`.platform/`、CI 与部署配置，不装依赖，不改设计；购物车与结账的显示见 SHOP-TASK-258、259。
- [x] `frontend/src/api/checkout.ts` 的类型（照 HANDOFF 0.97 落实写法，字段名与可空性同 `app/services/checkout.py`）：
  - 新增 `BuyXGetYQuote`：`rule_id`、`buy_quantity`、`get_quantity`、`get_percent`、`units_in_scope`、`units_to_next`（均为 `number`）。
  - 新增 `PromotionQuote`：`promotion_id`、`item_discount_sen`、`order_discount_sen`、`after_item_subtotal_sen`（`number`）；`spend_mode`（`string | null`）；`spend_tier_reached`、`spend_next_threshold_sen`、`spend_next_value`、`spend_next_gap_sen`、`free_shipping_threshold_sen`、`free_shipping_gap_sen`、`shipping_waived_sen`（`number | null`）；`free_shipping_reached`（`boolean`）；`participates`（`boolean | null`）；`buy_x_get_y`（`BuyXGetYQuote[]`）。
  - `PricedQuoteLine` 加 `promotion_rule_id: number | null` 与 `promotion_item_discount_sen: number`；`CheckoutQuote` 加 `promotion: PromotionQuote | null`（`DestinationQuote` 与 `MemberQuote` 随之继承）。
- [x] `frontend/src/api/memberCheckout.ts` 的 `MemberQuote`（同 `app/api/member_orders.py` 的 `MemberQuoteOut`）加 `promotion_plan: "promotion" | "no_promotion" | null`、`promotion_pre_points_total_sen` 与 `other_pre_points_total_sen`（`number | null`）、`coupon_note: "not_combinable" | "below_min_after_sale" | null`、`points_not_combinable: boolean`。
- [x] 新字段都写成必填（接口总会给出）；照现有写法不做逐字段校验。现有函数的请求与行为不变。
- [x] 测试替身只补新字段（`promotion`、`promotion_rule_id`、`promotion_plan`、两个积分前应付与 `coupon_note` 为 null，`promotion_item_discount_sen` 为 0，`points_not_combinable` 为 false），不改断言：`frontend/src/api/checkout.test.ts`、`frontend/src/api/memberCheckout.test.ts`、`frontend/src/pages/CartPage.test.tsx`、`frontend/src/pages/CheckoutPage.test.tsx`、`frontend/src/pages/CheckoutMember.test.tsx`。
- [x] `frontend/src/api/checkout.test.ts` 与 `frontend/src/api/memberCheckout.test.ts` 各新增一条：接口返回带活动字段的结果时原样交给调用方（含 `buy_x_get_y` 列表、行的命中规则与可空字段为 null；会员一条另含方案、两个积分前应付、`coupon_note` 与 `points_not_combinable`），请求体不变。注释写明守住 DESIGN 1.23 第 10 条「计价接口另返回 S1、……以及第 6 条的方案（没有运费报价时方案字段为空，第 4 条）」原句。
- 偏离与落实细节：
  - `spend_mode` 照后端的 `str | None` 写成 `string | null`，不收窄为字面量（落实写法只要求类型同后端）；`promotion_plan` 与 `coupon_note` 照落实写法写成字面量联合。
  - `CartPage.test.tsx` 的替身经 `WITHIN_LIMITS` 常量补 `promotion: null`（该常量被所有购物车计价替身展开），其余页面测试在各自的 `priced` 与计价替身函数里补字段。
  - 验收条写「计价接口返回……」，DESIGN 第 10 条原文为「计价接口另返回……」，注释照原文引用。
- 验证到什么程度：人工逐条对照验收标准、`app/services/checkout.py` 的 `PricedLine`、`BuyXGetYQuote`、`PromotionQuote`、`CheckoutQuote` 与 `app/api/member_orders.py` 的 `MemberQuoteOut`；新测试的数按第 10 条的公式手算（买 1 享 1 半价、n = 2 时再加 2 件）。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

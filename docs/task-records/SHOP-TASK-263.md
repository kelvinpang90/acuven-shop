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

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 类型——frontend/src/api/checkout.ts 与 frontend/src/api/memberCheckout.ts 照落实写法加类型与字段，字段名与后端相同；现有函数的请求与行为不变
      - 现有测试——frontend/src/api/checkout.test.ts、frontend/src/api/memberCheckout.test.ts、frontend/src/pages/CartPage.test.tsx、frontend/src/pages/CheckoutPage.test.tsx 与 frontend/src/pages/CheckoutMember.test.tsx 里的替身只补新字段（promotion 为 null，promotion_rule_id、promotion_plan、两个积分前应付与 coupon_note 为 null，promotion_item_discount_sen 为 0，points_not_combinable 为 false），不改断言
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/api/checkout.test.ts 与 frontend/src/api/memberCheckout.test.ts 各补一条：接口返回带活动字段的结果时原样交给调用方（含 buy_x_get_y 列表与可空字段为 null）；注释写明它守住的 DESIGN 1.23 第 10 条「计价接口返回……」原句
      - docs/task-records/SHOP-TASK-263.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改页面、后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/DESIGN.md 1.23「节日促销活动」第 4、6、10 条，让 frontend/src/api/checkout.ts 与 frontend/src/api/memberCheckout.ts 的类型带上游客与会员计价接口新增的活动字段（SHOP-TASK-234、236）；购物车与结账的显示见 SHOP-TASK-258、259。不改页面，不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：checkout.ts 新增 BuyXGetYQuote（rule_id、buy_quantity、get_quantity、get_percent、units_in_scope、units_to_next）与 PromotionQuote（promotion_id、item_discount_sen、order_discount_sen、after_item_subtotal_sen、spend_mode、spend_tier_reached、spend_next_threshold_sen、spend_next_value、spend_next_gap_sen、free_shipping_threshold_sen、free_shipping_reached、free_shipping_gap_sen、shipping_waived_sen、participates、buy_x_get_y），类型与可空性同 app/services/checkout.py；PricedQuoteLine 加 promotion_rule_id（数或 null）与 promotion_item_discount_sen（数），CheckoutQuote 加 promotion（PromotionQuote 或 null）。memberCheckout.ts 的 MemberQuote 加 promotion_plan（promotion、no_promotion 或 null）、promotion_pre_points_total_sen 与 other_pre_points_total_sen（数或 null）、coupon_note（not_combinable、below_min_after_sale 或 null）与 points_not_combinable（布尔），同 app/api/member_orders.py。新字段都写成必填（接口总会给出）；照现有写法不做逐字段校验，请求不变。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 4、6、10 条：只加读取计价结果的类型，不计算金额，不碰个人资料。
      拆分：前台计价模块一层（8 个允许路径：两个计价模块各带测试、3 个只补替身字段的页面测试与任务记录）。运营者登记时把字段按必填加进类型，在 main 的导出上跑类型检查，报错的只有两个模块的测试与这 3 个页面测试，所以与类型一起改、只补字段。依赖 SHOP-TASK-234（游客计价）与 236（会员计价）。
```

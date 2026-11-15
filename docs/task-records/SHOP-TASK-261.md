# SHOP-TASK-261 后台订单 A02 的活动行与店铺装修 A08 的活动说明

- [x] 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 7 条：只显示订单金额快照，不计算金额；不增加个人资料的显示。依据 UX 0.20 的 A02（M1）与 A08（节日促销活动）以及 HANDOFF 0.97 的落实写法；后台字段来自 SHOP-TASK-238，`checkout.summary_promo` 与 `checkout.shipping_free` 来自 SHOP-TASK-259。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `frontend/src/api/adminOrders.ts`：`AdminOrderDetail` 加必填的 `promotion_discount_sen`、`shipping_waived_sen`（`isCount`：非负安全整数）与 `promotion_name`（字符串或 null，写法同 `paid_at`：其他值与缺少都为 undefined 即不合格）；`readDetail` 照现有逐字段校验，不合格时整个响应为 `failed`。请求没有改。
- [x] `frontend/src/pages/AdminOrderDetail.tsx`：
  - `promotion_name` 不为 null 时，商品区块之后、金额明细之前单独一行 `admin.order_promotion`（`{name}` 为内部名称，作为 React 文本节点按纯文本显示）。这一行是面板的直接子元素，桌面与手机（面板为 `display: contents`）共用、整页只出现一次，不在金额明细的桌面区块或手机折叠里。
  - `AmountRows`（桌面区块与手机折叠共用）：`promotion_discount_sen` 大于 0 时紧接 `cart.subtotal` 加一行 `checkout.summary_promo`（`common.price_myr`，不加负号）；`shipping_waived_sen` 大于 0 时运费行金额为 `checkout.shipping_free`（`amount` 为 `formatSen(shipping_waived_sen)`），否则照旧为 `shipping_fee_sen`。行序为小计、活动优惠、券、积分、运费、合计；三项都没有时输出与改动前相同。
- [x] `frontend/src/pages/AdminStoreDesignPage.tsx`：取得设置后，`site-admin-store-design__body` 的第一项为 `admin.design_promo_note`（沿用演示说明的 `<p class="site-admin-store-design__note">`），其后为表单；读取中与读取失败都不显示。标题、`admin.design_demo_note`、表单与两份预览没有改，预览不显示活动装饰。
- [x] 文案与样式：`frontend/src/i18n/copy.ts` 加 `admin.order_promotion`（在 `admin.order_detail` 之后）与 `admin.design_promo_note`（在 `admin.design_demo_note` 之后），三语从 UX-COPY 0.22 逐字照抄。样式沿用相邻元素，没有改 `site.css`。没有改 `router.tsx` 与 `App.tsx`（不新增路由）。
- [x] 测试（`vitest` 与 `react-dom/server`），每条测试的注释写明它守住的 UX 0.20 A02 M1 或 A08 原句：
  - `frontend/src/api/adminOrders.test.ts`：现有合格响应替身 `DETAIL` 补上三个字段（0、0 与 null，只为此改）；新增记有活动的合格响应（含空字符串名称）与请求不变、三个字段各种不合格值以及缺少任一字段时为 `failed`。
  - `frontend/src/pages/AdminOrderDetail.test.tsx`：替身 `ORDER` 补上三个字段（没有活动，其余断言不变）；新增「promotion」一组，三语下名称一行的位置与只出现一次、桌面与手机折叠的六行与顺序（活动优惠行、免运费）、三项各自成立、三项都没有时与改动前相同、含标签与实体的名称按纯文本显示。
  - `frontend/src/pages/AdminStoreDesignPage.test.tsx`：新增三语下表单区第一项为活动说明、读取中与读取失败不显示、两份预览不含说明且与单独渲染的预览逐字相同。现有测试里没有按顺序列出读取完成后表单区内容的断言（读取中与读取失败的整段断言不受影响），所以没有改现有断言。
- 偏离与落实细节：
  - 名称一行没有加 class，沿用面板的网格间距（桌面）与详情区的间距（手机）；字号与颜色不声明，照默认正文。
  - 店铺装修的活动说明只在读取成功后显示：验收写「读取完成后的表单区开头」，而读取失败时 UX 与现有测试要求表单区只有提示。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 A02 M1 与 A08 原句、UX-COPY 0.22 的三语文案与后端后台详情的字段名。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23（提交 b7f9355）；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 后台订单模块——frontend/src/api/adminOrders.ts 照落实写法加字段与校验，缺少或不合格时按现有写法返回失败；请求不变
      - 订单详情——AdminOrderDetail 照落实写法显示活动名称、活动优惠行与免运费；三项都没有时与现在完全相同
      - 店铺装修——AdminStoreDesignPage 照落实写法显示 admin.design_promo_note，其余不变；现有测试里按顺序列出读取完成后表单区内容的断言只为加入这一句改
      - 文案与样式——frontend/src/i18n/copy.ts 加 admin.order_promotion 与 admin.design_promo_note，三语与 docs/UX-COPY.md 0.22 逐字相同；样式沿用相邻元素，需要时才改 frontend/src/styles/site.css（只写布局、显隐与媒体查询，不声明颜色、字体、圆角属性）
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/api/adminOrders.test.ts 补三个字段的合格与不合格响应（现有的合格响应替身补上三个字段，只为此改）；frontend/src/pages/AdminOrderDetail.test.tsx 与 frontend/src/pages/AdminStoreDesignPage.test.tsx 用 vitest 与 react-dom/server 覆盖三种语言下活动名称、活动优惠行、免运费、三项都没有时与现在相同、名称按纯文本显示、店铺装修页的说明；其余现有断言不改；每条测试的注释写明它守住的 UX 0.20 A02 或 A08 原句
      - docs/task-records/SHOP-TASK-261.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 App.tsx 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 A02（M1）与 A08（节日促销活动），在后台订单详情显示订单记录的活动（内部名称）、活动优惠与活动免运费（SHOP-TASK-238 的后台字段），并在店铺装修页加一句活动说明。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：frontend/src/api/adminOrders.ts 的 AdminOrderDetail 加 promotion_discount_sen、shipping_waived_sen（非负整数）与 promotion_name（字符串或 null），按现有逐字段校验的写法校验，不合格按现有写法返回失败。frontend/src/pages/AdminOrderDetail.tsx：promotion_name 不为 null 时在金额明细之前单独一行显示 admin.order_promotion（name 为内部名称，按纯文本显示；桌面与手机都只显示这一次）；promotion_discount_sen 大于 0 时金额明细在商品小计之后加 checkout.summary_promo 一行；shipping_waived_sen 大于 0 时运费行金额为 checkout.shipping_free（amount 为 shipping_waived_sen）。frontend/src/pages/AdminStoreDesignPage.tsx 在读取完成后的表单区开头（site-admin-store-design__body 里的第一项）显示 admin.design_promo_note，读取中不显示（UX 0.20 A08 只说页内显示、没有线框位置；放在表单区里，标题、admin.design_demo_note 与读取中的现有断言不受影响），预览不显示活动装饰。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 7 条：只显示订单金额快照，不计算金额；不增加个人资料的显示。
      拆分：后台两处显示一层（11 个允许路径：后台订单模块、订单详情与店铺装修页各带测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-238（后台字段）与 259（文案键）。
```

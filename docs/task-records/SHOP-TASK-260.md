# SHOP-TASK-260 订单详情 P09 的活动优惠行与免运费

- [x] 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 7 条：只显示订单金额快照，不计算金额；不增加个人资料的显示。依据 UX 0.20 的 P09（M3）与 HANDOFF 0.97 的落实写法；查单字段来自 SHOP-TASK-238，文案键来自 SHOP-TASK-259。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `frontend/src/api/orderLookup.ts`：`LookupOrder` 加必填的 `promotion_discount_sen`（商品级与整单级活动优惠之和）与 `shipping_waived_sen`（免去的运费），与 `app/services/order_lookup.py` 的查单字段同名；请求与现有函数没有改。会员模式经 `frontend/src/api/memberOrders.ts` 用同一类型，该文件没有改。
- [x] `frontend/src/pages/TrackOrderPage.tsx` 的 `AmountRows`（查单模式与会员模式、桌面明细与手机折叠共用）：
  - `promotion_discount_sen` 大于 0 时紧接 `cart.subtotal` 加一行 `checkout.summary_promo`，金额按 `common.price_myr` 格式化、不加负号；游客订单也显示，与 `placed_by_member` 无关。
  - `shipping_waived_sen` 大于 0 时运费行金额为 `checkout.shipping_free`（`amount` 为 `formatSen(shipping_waived_sen)`），否则照旧为 `shipping_fee_sen`。
  - 行序为商品小计、活动优惠、优惠券、积分、运费、合计；现有各行的显示条件没有改；两项都为 0 时输出与改动前相同。
- [x] 文案与样式：只用 SHOP-TASK-259 已加的 `checkout.summary_promo` 与 `checkout.shipping_free`，没有加新键；新行沿用相邻的 `acs-row` 与 `acs-num`，没有改 `site.css`。没有改 `router.tsx`、`App.tsx` 与 `copy.ts`（不需要，不新增路由）。
- [x] 测试（`vitest` 与 `react-dom/server`）：
  - `frontend/src/pages/TrackOrderPage.test.tsx` 新增「sale discount and free shipping rows」：三语下游客订单与会员订单在两种模式、桌面与手机折叠里的行与顺序（活动优惠行、免运费的运费行）；三语下两项为 0 时游客三行、会员五行，不出现活动优惠与免运费文字；只有活动优惠或只免运费时各自成立，金额照快照原样。每条测试的注释写明它守住的 UX 0.20 P09 M3 原句。
  - `frontend/src/api/orderLookup.test.ts`、`frontend/src/api/memberOrders.test.ts`、`frontend/src/pages/RefundPage.test.tsx` 与 `TrackOrderPage.test.tsx` 里完整写出 `LookupOrder` 的替身补上两个字段、值为 0（只为类型检查）；其余现有断言没有改。
- 偏离与落实细节：
  - 活动优惠金额与券、积分两行和结账页（SHOP-TASK-259）一样按 `common.price_myr` 显示、不加负号；颜色与字重不声明，照 `.acs-row` 的默认样式。
  - 免运费时运费行只显示 `checkout.shipping_free`，不另显示实际运费（为 0）；手机折叠标题行仍为合计，没有改。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 P09 线框与 M3 原句、后端查单字段名。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23（提交 b7f9355）；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 查单模块——frontend/src/api/orderLookup.ts 照落实写法加字段，请求与现有函数行为不变
      - 金额明细——TrackOrderPage 的查单模式与会员模式都照落实写法显示，行的顺序为商品小计、活动优惠、优惠券、积分、运费、合计（现有各行的显示条件不变）
      - 文案与样式——用 SHOP-TASK-259 已加的 checkout.summary_promo 与 checkout.shipping_free，不加新键；样式沿用相邻金额行，需要时才改 frontend/src/styles/site.css（只写布局、显隐与媒体查询，不声明颜色、字体、圆角属性）
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/pages/TrackOrderPage.test.tsx 用 vitest 与 react-dom/server 覆盖三种语言下游客订单与会员订单的活动优惠行、免运费的运费行、两项为 0 时与现在相同；frontend/src/api/orderLookup.test.ts、frontend/src/api/memberOrders.test.ts 与 frontend/src/pages/RefundPage.test.tsx 里完整写出 LookupOrder 的替身补上两个字段、值为 0（只为此改，否则类型检查不通过；运营者登记时把两字段按必填加进类型，在 main 的导出上跑类型检查，只有这三个文件与 TrackOrderPage.test.tsx 报错）；其余现有断言不改；每条测试的注释写明它守住的 UX 0.20 P09 M3 原句
      - docs/task-records/SHOP-TASK-260.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx、App.tsx 与 copy.ts 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 P09（M3）与 docs/DESIGN.md 1.23「节日促销活动」第 7 条，在订单详情（查单模式与会员模式）的金额明细显示订单快照里的活动优惠与活动免运费（SHOP-TASK-238 的查单字段）。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：frontend/src/api/orderLookup.ts 的 LookupOrder 加 promotion_discount_sen 与 shipping_waived_sen（会员模式经 frontend/src/api/memberOrders.ts 用同一类型，不改该文件）。frontend/src/pages/TrackOrderPage.tsx 的 AmountRows：promotion_discount_sen 大于 0 时在商品小计之后加 checkout.summary_promo 一行（游客订单也显示，与优惠券、积分两行是否显示无关）；shipping_waived_sen 大于 0 时运费行金额为 checkout.shipping_free（amount 为 shipping_waived_sen）。两项都为 0 时金额明细与现在完全相同。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 7 条：只显示订单金额快照，不计算金额；不增加个人资料的显示。
      拆分：P09 金额明细一层（11 个允许路径：查单模块与页面各带测试、两个只补替身字段的测试文件，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-238（查单字段）与 259（文案键）。
```

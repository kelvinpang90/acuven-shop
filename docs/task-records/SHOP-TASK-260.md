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

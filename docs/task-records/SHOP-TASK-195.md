# SHOP-TASK-195 结账 P05 的整单上限提示

- [x] 按 `docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 8 条、`docs/UX.md` 0.17 的 P05「整单上限」一段、`docs/UX-COPY.md` 0.18 与 `docs/HANDOFF.md` 0.80 的落实写法，在结账页 P05 的 `cart.item_changed` 位置加入两条整单上限提示。改动只在 `frontend/src/pages/CheckoutPage.tsx`、`frontend/src/pages/CheckoutPage.test.tsx`、`frontend/src/pages/CheckoutMember.test.tsx` 与本文件。未新增路由（`router.tsx` 与 `App.tsx` 未改）；`i18n/copy.ts` 未改（两条文案已由 SHOP-TASK-194 加入）；`site.css` 未改（见偏离）；未装依赖；未改后端、设计、`docs/TODO.md`、`.platform/`、CI、部署配置与生成的 `styles/acuven-shop.css`。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 8 条与 HANDOFF 0.80，整单上限的提示，判定与下单校验在服务端。
- [x] 显示条件（`pages/CheckoutPage.tsx`）：新增导出纯函数 `overOrderLimit(quote)`，计价结果的 `units_over_limit` 或 `amount_over_limit` 为真时为真，没有结果（null）时为否。`CheckoutView` 里 `ChangedNotice` 改为在 `changed`（有非正常行或下单 409 后，判定不变）或 `overOrderLimit(ready)` 时显示；`ready` 即页面已有的、与 `cart.item_changed` 共用的当前计价结果（游客与会员共用同一个视图，会员时为会员计价）：重新计价进行中（pending）时沿用上一次的结果，首次读取中或读取失败时为 null，不显示上限提示。
- [x] 提示（`ChangedNotice`）：依次为 `cart.item_changed`（只在 `changed` 时）、`cart.units_over_limit`（`{count}` 为 `unit_limit`）、`cart.amount_over_limit`（`{amount}` 为 `formatSen(amount_limit_sen)`），每条都是新提取的 `DangerAlert`（`acs-alert acs-alert--danger`、`role="alert"`、`cart.item_changed` 原有的同一图形，`cart.item_changed` 的输出不变）；其下只有一个回购物车的链接（`common.nav_cart`，写法不变）。
- [x] 「任一步都显示」的落实写法（HANDOFF 0.80）：`ChangedNotice` 原本就在 `site-checkout__main` 里各步内容之前、不按步骤判断，上限提示放进同一块，所以第 1、2、3 步都在同一位置显示、共用同一个回购物车链接；第 1 步未选目的地时的计价已能给出 `units_over_limit`，金额上限在选了目的地后才可能出现（UX 0.17 P05 所述）。
- [x] 下单按钮：`canPlaceOrder` 不另加判定，照旧依赖接口的 `can_place_order`（超出时服务端给 false）；只在注释里写明这一点。
- [x] 新增测试（vitest 与 `react-dom/server`，以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击；每条写明所守的 UX、HANDOFF 或任务验收的哪一句）：
  - `pages/CheckoutPage.test.tsx` 的「order limits (SHOP-TASK-195)」：只超出件数时提示在各步内容之前、文字带 2000、没有 `cart.item_changed` 与金额一条、两个下单按钮禁用、`canPlaceOrder` 为否（三种语言）；只超出金额时提示带 999999.99、换成 12345 仙时为 123.45、按钮禁用（三种语言）；两者同时时先件数后金额、两条同一图形（三种语言）；与非正常行同时时依次为 `cart.item_changed`、件数、金额，只有一个回购物车链接与一个提示块，下单 409 后（`notice` 为 changed）同样只有一个链接（三种语言）；未超出时第 1 步与第 3 步都没有提示块（三种语言），并直接调用 `overOrderLimit`；第 1 步、第 2 步与第 3 步都显示；重新计价进行中沿用上一次结果显示，读取中与失败时不显示；超出时接口给 `can_place_order` 为 false、按钮禁用，`canPlaceOrder` 只看 `can_place_order`。
  - 同一文件「页面文字全部来自字典」一条补两个状态（非正常行与两条上限提示同时显示的第 3 步、第 1 步的件数提示）；字典键列表补 `cart.units_over_limit`、`cart.amount_over_limit`。
  - `pages/CheckoutMember.test.tsx` 的「placing the order」：会员计价超出两种上限时（服务端照有不正常行的写法返回）提示依次为件数、金额，之下一个回购物车链接，在各步内容之前，按钮禁用、`canPlaceOrder` 为否（三种语言）。
- [x] 改动的现有测试：无（只新增测试、在字典测试的状态列表与键列表里追加条目）。
- 偏离与取舍，请审阅（未改设计与文案）：
  - UX 0.17 P05 写「第 3 步」显示；按 HANDOFF 0.80 落实为任一步都显示（见上）。
  - P03-P04-caps 画板上两条上限提示之间是标注用的 14px 间距；P05 沿用 `.site-checkout__changed` 现有的 12px 间距（与 `cart.item_changed` 和链接之间相同），所以 `site.css` 未改。
  - P05-states 没有画上限提示，提示条与链接的样式照 `cart.item_changed` 现有写法。
  - `site.css` 写不了而未搬的视觉稿属性（评审不以此要求修改）：无新增。
  - 没有用到 UX-COPY 以外的界面文字。
- 验证到什么程度：人工逐条对照验收标准、UX 0.17 P05「整单上限」一段、UX-COPY 0.18 的两条原句、DESIGN 1.18 第 8 条与 HANDOFF 0.80，样式对照 P03-P04-caps 与 P05-states；上面列出的测试在 `pages/CheckoutPage.test.tsx` 与 `pages/CheckoutMember.test.tsx` 中。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查结果由 Worker 另行记录。未做浏览器验收（未在真实浏览器中打开页面，也未与视觉稿逐像素对比）。

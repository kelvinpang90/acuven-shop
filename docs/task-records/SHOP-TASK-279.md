# SHOP-TASK-279 购物车 P04 在预览中的计价与禁用结账

- [x] 计价（`frontend/src/pages/CartPage.tsx`，HANDOFF 0.98 落实写法）：新增导出的 `cartQuoteRequest(preview)`——`preview` 为 `null` 时就是 `api/checkout.ts` 的 `fetchQuote`（与之前同一个函数）；不为 `null` 时返回以预览上下文的活动 ID 与 CSRF 令牌调用 SHOP-TASK-277 的 `fetchPreviewQuote(promotionId, csrfToken, language, lines, signal)` 的计价函数。`CartPage` 以 `usePreview()` 的结果经现有的 `latestQuoter(cartQuoteRequest(preview))` 计价；请求标识、重新计价、作废与 pending 的写法不变。
- [x] 结账（同一文件）：新增 `CheckoutAction`，桌面小计栏与手机底部固定栏的 `cart.checkout` 都经它渲染。在预览上下文里为禁用的 `<button type="button" disabled>`（不是链接、不跳转，class 与之前的链接相同），紧接其下为 `<p class="acs-body-s acs-muted site-cart__preview-note">` 的 `promo.preview_no_checkout`（视觉稿 A12-states 的预览购物车：按钮之下一行说明）；预览之外照旧按路由规则链到 `/checkout`。有计价结果时预览中的 `<main>` 另加 `site-cart--preview`，供手机版式加高页面底部的留白。会员说明、金额行、凑单提示、进度条与角标等其余内容照现有写法显示。
- [x] 文案（`frontend/src/i18n/copy.ts`）：加 `promo.preview_no_checkout`，英、中、马三列逐字取自 docs/UX-COPY.md 0.22 的同名行。
- [x] 样式（`frontend/src/styles/site.css`）：手机媒体查询里加 `.site-cart--preview { padding-bottom: 160px; }`（底部固定栏多一行说明，页面底部留出的高度从 120px 加高）。桌面的按钮与说明随 `acs-summary` 现有的网格间距排列，未另写规则；只写布局，没有颜色、字体与圆角。
- [x] 测试（`frontend/src/pages/CartPage.test.tsx`，vitest 与 react-dom/server，计价以 fetch 替身给出；每条新测试的注释写明守住的 UX 0.20 A12「前台预览」原句——「购物车预览用本浏览器的购物车计价，不能结账、不能下单」「预览凭管理员会话经后台接口取数」、线框「P04 的 ( [cart.checkout] ) 禁用，旁边显示 [promo.preview_no_checkout]」与「<前台页面…按该活动正在生效时的样子显示>」）：
  - 新增 `describe("cart in a storefront preview")`：三种语言下以渲染时取出的 `usePreview()` 结果，`PreviewProvider` 里经 `latestQuoter` 计价走 `/api/admin/promotions/7/preview/quote?lang=…`，带 `X-CSRF-Token`、`same-origin`、`no-store`、购物车的 SKU 与件数和中止信号，结果照常采用；之外 `cartQuoteRequest` 就是 `fetchQuote`，地址与选项逐字同公开计价接口；三种语言下预览里两处 `cart.checkout` 为禁用按钮、各自之下 `promo.preview_no_checkout`、页面没有链到 `/checkout` 的链接；把两处换回链接并去掉预览 class 后与预览之外逐字相同（含会员说明）；预览之外不显示说明、没有禁用的结账按钮，两处照旧链到 `/checkout`。
  - `render` 加可选的 `preview`（不给时不包 `PreviewProvider`）；`CART_KEYS` 加 `promo.preview_no_checkout`。其余现有断言未改。
- 偏离：
  - 计价函数抽成导出的 `cartQuoteRequest(preview)`，以便在没有 DOM 的服务端渲染测试里验证计价函数与参数（与 SHOP-TASK-278 的 `fetchSaleProducts(preview)` 同一写法）；`CartPage` 本身的 effect 在服务端渲染测试里不运行，没有页面级的请求测试。
  - `latestQuoter` 照现有写法在页面打开时建一次，计价函数随之按当时的预览上下文选定；预览上下文在页面存续期间不变（预览页由 SHOP-TASK-280 接入）。
  - UX 线框写「旁边显示」；视觉稿 A12-states 的预览购物车把说明放在按钮之下，按视觉稿排在按钮之后。
- 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 11 条：预览的计价只经后台接口，结账与下单按钮禁用，不产生订单；页面只显示计价结果，不碰个人资料。本任务预览中的计价只发往 `POST /api/admin/promotions/{ID}/preview/quote`，两处结账为禁用按钮、不跳转，不下单；页面不读写个人资料，公开计价接口的地址与参数不变。
- 未改：`router.tsx`、`App.tsx`（不新增路由）、`preview.tsx`、`api/adminPromotionPreview.ts`、`api/checkout.ts`；后端、`.platform/`、CI 与部署配置；没有装依赖，没有改设计。
- 留给之后的任务：预览页本身（SHOP-TASK-280）。
- 验证到什么程度：人工逐条对照 UX 0.20 A12「前台预览」、UX-COPY 0.22 的 `promo.preview_no_checkout`、视觉稿 A12-states、DESIGN 1.23 第 11 条、HANDOFF 0.98 落实写法与 SHOP-TASK-277 的 `fetchPreviewQuote`；上述测试为本次新增的测试代码，前端检查由 PR 的必需 CI 检查 `frontend` 执行。

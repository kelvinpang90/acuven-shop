# SHOP-TASK-165 会员中心 P13 的我的积分与我的优惠券两段

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 3、5 条（会话到期与服务端授权检查；会员仅能访问自己的资料）与「计价、优惠、积分与库存」第 3、5 条（获得积分、退款返还与追回、待抵扣），以及 HANDOFF 0.63。依据 docs/UX.md 0.13 的 P13（含 Q19）、docs/UX-COPY.md 0.14，样式取视觉稿 `docs/design/pages/P13-desktop-points.html`、`P13-phone-points.html`、`P13-desktop-coupons.html` 与 `P13-phone-coupons.html`。读取经 SHOP-TASK-164（`api/memberAccount.ts` 的 `readMemberPoints` 与 `readMemberCoupons`），会话经 SHOP-TASK-151（`api/memberSession.ts`）。

- [x] 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.14 逐字加入 `account.points_type_earned`、`account.points_type_redeemed`、`account.points_type_expired`、`account.points_type_returned`、`account.points_type_reversed`、`account.points`、`account.points_balance`、`account.points_pending`、`account.points_expiry`、`account.points_history`、`account.coupons`、`account.coupons_available`、`account.coupon_value_fixed`、`account.coupon_value_percent`、`account.coupon_min_spend`、`account.coupon_valid_until`、`account.coupon_use_hint`、`account.coupons_available_empty`、`account.coupons_used`、`account.coupon_used_on` 与 `account.coupons_used_empty`（照 UX-COPY 的顺序，放在 `account.order_pay` 之后、`account.demo_hint` 之前），每种语言一行，写法同相邻条目。没有用到 UX-COPY 以外的文字。
- [x] 段表（`frontend/src/pages/AccountPage.tsx`）：`ACCOUNT_SECTIONS` 依次为我的订单（`account.orders`）、我的积分（`account.points`）与我的优惠券（`account.coupons`）；桌面左侧导航（页内锚点 `#account-points`、`#account-coupons`，当前段 `aria-current`）与手机分段标签（`account-points-tab` 等，段内容为 tabpanel）照现有写法按表渲染。`AccountScreen` 的已登录状态加 `points` 与 `coupons`，`sessionStep` 让三段都从读取中开始；`SectionPanel` 与 `sectionBusy` 各加两个分支；另导出 `withPoints`、`withCoupons` 两个改动。
- [x] 读取（`openAccount`）：会话为 member 后同时读取我的订单、我的积分与我的优惠券（`Promise.all`，请求依次发出）。读取中该段标 `aria-busy`。积分与优惠券的结果经 `settleSection` 按到达时的状态换上该段；signed_out 时与我的订单一样重读会话并以 replace 进入 `/login`。三段可能同时回 signed_out，同一次打开共用一个离开（`leaveOnce`），只重读一次会话、只跳转一次；`loadOrders` 为此加了可选的第四个参数（不给时自建，单独调用的行为不变）。failed 显示 `common.error_retry`，network 显示 `common.network_check`，都在该段里。离开页面时中止三个请求（页面的 AbortController），之后不再更新；会话的重读不带 signal、不中止，离开后不再据其结果跳转。
- [x] 新文件 `frontend/src/pages/AccountPointsPanel.tsx`：状态类型 `PointsState`、纯函数 `pointsStep`（ok、signed_out 为 login、failed、network）、`pointsTypeLabel`、`signedPoints`，接收 AbortSignal 与回调（settle）的异步函数 `loadPoints`，以及只依赖 props 的 `PointsPanel`。
  - 桌面段标题 h2 `account.points`（手机由标签代替）；余额一块依次为 `account.points_balance`（`{points}` 为 balance）、pending 大于 0 时 `account.points_pending`、next_expiry 不为 null 时 `account.points_expiry`（`{date}` 照 `AdminCouponsPage.tsx` 导出的 `formatCouponDate`，按马来西亚日期）。
  - 明细按接口顺序，每条为日期（`TrackOrderPage.tsx` 的 `formatDate`，按浏览器时区）、`account.points_type_*` 中对应的键（未知类型不显示名称）、带正负号的积分数（正数前加加号，0 与负数原样）与订单号（`acs-num`）。桌面为 h3 `account.points_history` 与四列各行；手机为默认展开的 `details`（summary 为 `account.points_history`），每行为日期与积分数、类型与订单号两行。两份由 site.css 按宽度显隐。
- [x] 新文件 `frontend/src/pages/AccountCouponsPanel.tsx`：状态类型 `CouponsState`、纯函数 `couponsStep`、`couponValue`，常量 `COUPON_TIME_ZONE`（`Asia/Kuala_Lumpur`），异步函数 `loadCoupons`，以及只依赖 props 的 `CouponsPanel`。
  - 桌面段标题 h2 `account.coupons`；h3 `account.coupons_available` 与各张券（`acs-card` 卡片，按接口顺序）：代码（`acs-num`）、面额（固定额为 `account.coupon_value_fixed`，`{amount}` 为 `formatSen(amount_sen)`；百分比为 `account.coupon_value_percent`；类型与值对不上时不显示面额）、min_spend_sen 大于 0 时 `account.coupon_min_spend`（`formatSen`）、`account.coupon_valid_until`（`formatDate` 按 `Asia/Kuala_Lumpur` 取日期），其后 `account.coupon_use_hint`；没有可用券时只有 `account.coupons_available_empty`。
  - h3 `account.coupons_used` 与使用记录：每条为代码、`account.coupon_used_on`（`{orderNo}` 为订单号）与 `common.price_myr` 格式化的 `discount_sen`；桌面三列，手机为代码与金额、其下 `account.coupon_used_on` 两行，两份由 site.css 显隐；没有记录时为 `account.coupons_used_empty`。金额都是原样格式化接口的整数仙，不在浏览器计算。
- [x] 布局与显隐写在 `frontend/src/styles/site.css`（桌面一段，手机写在 767px 及以下的媒体查询里），不声明颜色、字体与圆角，视觉稿的固定间距与尺寸直接写像素值；未改 `acuven-shop.css`。
- [x] 测试（vitest 与 react-dom/server，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，会话读取用 `createMemberSessionReader()` 配同一个替身，路由的 replace 与 settle 以回调替身代替；以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，点击处理从元素树取出后直接调用，不模拟点击；每条注释写明守住的是验收、UX、视觉稿或 HANDOFF 的哪一句）：
  - `frontend/src/pages/AccountPointsPanel.test.tsx`：读取的 ok（请求地址与 signal）、signed_out 交出 login、failed 与 network 的提示、读取中只有段标题、离开页面后不交出结果；余额（三语）、pending 为 0 且没有到期时只有余额、pending 大于 0 时的尚欠、到期按马来西亚日期；明细桌面与手机各行的日期、类型、正负号与订单号（三语，按接口顺序）、手机折叠默认展开、正负号与类型名的纯函数、空明细只有标题；页面文字全部来自字典（`visibleTexts`、`textNodes`、`unescapeHtml` 照 `AdminRefundDetail.test.tsx`）。
  - `frontend/src/pages/AccountCouponsPanel.test.tsx`：读取的 ok、signed_out、failed、network、读取中、离开页面后；固定额与最低消费、百分比且最低消费为 0 时不显示、有效期按马来西亚日期（与 UTC 日期不同的一张）、面额纯函数、没有可用券；使用记录桌面与手机各行、没有记录、两种空状态；页面文字全部来自字典。
  - `frontend/src/pages/AccountPage.test.tsx` 新增：段表三项的顺序与桌面导航（当前段 `aria-current`）、三个标签与三个 tabpanel、每个导航项与标签都交出自己的段、积分或优惠券读取中时只有该段 `aria-busy`；打开后与我的订单一起读取两段并换上结果；积分、优惠券或两者都回 signed_out 时只重读一次会话、只 replace 一次到 `/login`；重读期间已离开时不跳转。
- [x] `AccountPage.test.tsx` 中现有测试的修改（只在必要时）：
  - `memberScreen` 的结果形状多了 `points` 与 `coupons`，默认为已取到的空积分与空优惠券（页面上只有字典文字，我的订单、标题行与退出的断言照旧）。
  - 「lists only my orders in the desktop navigation」与「shows a single selected tab for my orders on phones」随段表扩展改为三项（并改了名称与注释）；「hands navigation and tab clicks to the page」改为点每一项。
  - 「rereads the session instead of using the earlier shared state」「ignores later changes of the shared session」「stops after leaving the page while the shared reread still settles」只为多出的积分与优惠券两个读取补了替身、请求清单与两次 update（后者另期望这两个请求带页面的 signal）；期望的会话、我的订单与跳转去向不变。
  - 「has every key this page uses」里 `sessionStep` 的期望随状态扩展改为三段都在读取中。
  - demo_hint 位置一条与我的订单、标题行、退出的断言都没有改。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - HANDOFF 0.63 第（6）条：积分明细为空时只显示 `account.points_history` 标题（桌面 h3、手机折叠的 summary，UX-COPY 没有空明细的键）；没有即将到期的积分时不显示 `account.points_expiry`；最低消费为 0 的券不显示 `account.coupon_min_spend`。退款后尚欠大于 0 才显示 `account.points_pending`（UX 线框）。
  - UX 与视觉稿冲突一处（以 UX 为准，PR 里说明）：手机明细的 summary 在视觉稿为 flex，会去掉展开标记；UX 手机线框为「▸ [account.points_history]」，这里保留原生标记，以上下内边距 6px 代替垂直居中。
  - 视觉稿里 site.css 写不了的样式不搬：余额的字号（桌面 22px、手机 20px）与字重 700，券代码的字号 18px 与字重 700，面额的字重 600，手机 summary 的字重 600，明细与使用记录各行之间、手机折叠上下的分隔线（颜色），以及桌面导航当前项的底色、圆角与字重（同 SHOP-TASK-152）。没有借用其他组件的类来取颜色；卡片用视觉稿本身的 `acs-card`。
  - 负数照验收原样显示接口的整数（`-12`，连字符），视觉稿画的是减号 `−12`；订单号原样显示接口的值，视觉稿画的是带连字符的缩短写法；日期按 `formatDate`、`formatCouponDate` 与当前语言格式化，视觉稿为英文示例。
  - 没有可用券时不显示 `account.coupon_use_hint`（验收「其后 account.coupon_use_hint；没有可用券时为 account.coupons_available_empty」与视觉稿的空状态）。
  - 视觉稿手机的小标题为 h2 `acs-display-s`；这里桌面与手机共用一份 h3 `acs-display-s`（样式类相同，只有标题层级不同）。
  - 三段同时读取；几段都回 signed_out 时只重读一次会话、只跳转一次（验收未写，避免重复请求与跳转）。
  - 桌面照现有框架把三段依次排在右栏（视觉稿每张画板只画当前段）；视觉稿导航画了设置，本任务只渲染已实现的三项。
  - 未知的积分类型不显示类型名，类型与值对不上的券不显示面额（派生实现约束，同我的订单的未知状态）。
- 留给后续任务：设置段（含把手机的遮掩手机号与退出移进设置段）由之后的任务加入段表。
- 改动只在 `frontend/src/pages/AccountPage.tsx`、`frontend/src/pages/AccountPage.test.tsx`、`frontend/src/pages/AccountPointsPanel.tsx`、`frontend/src/pages/AccountPointsPanel.test.tsx`、`frontend/src/pages/AccountCouponsPanel.tsx`、`frontend/src/pages/AccountCouponsPanel.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`frontend/src/router.tsx` 与 `frontend/src/App.tsx` 不需要改，未改。未改 `frontend/src/api/`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX P13、HANDOFF 0.63、四张视觉稿与 `api/memberAccount.ts` 的回答归类自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（桌面与手机的实际排版、卡片与折叠的观感都未在浏览器里看过），未对真实后端接口验证。

# SHOP-TASK-163 订单详情 P09 会员模式的继续支付与会员中心退出时清除暂存

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 5 条（会员仅能访问自己的订单）与第 7 条（从会员订单详情进入支付页时写入暂存、覆盖之前的值；会员退出时清除），以及 HANDOFF 0.63。依据 docs/UX.md 0.13 的 P09 会员模式（待支付订单另有 `[account.order_pay]`，回到 P06 继续支付或取消，Q17 已决）与 P06「会员的当前订单」，文案依据 docs/UX-COPY.md 0.14，样式依据视觉稿 `docs/design/pages/P09-pending.html`。SHOP-TASK-154 记下的「会员模式待支付订单本批不显示 `account.order_pay`」至此取消；查单模式仍显示 `order.lookup_no_pay`。

- [x] 字典：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.14 逐字加入 `account.order_pay`（Continue payment / 继续支付 / Teruskan pembayaran），放在 `account.order_view` 之后，写法与相邻条目相同。没有用到 UX-COPY 以外的界面文字。
- [x] 继续支付（`frontend/src/pages/TrackOrderPage.tsx`）：
  - 会员模式下状态为 `awaiting_demo_payment` 的订单在右栏最后、查单模式显示 `order.lookup_no_pay` 的同一位置显示 `account.order_pay`：指向 `/pay` 的主按钮样式链接（`acs-btn acs-btn--primary site-order__pay`），只有一份，桌面与手机相同；其他状态不显示。`account.orders` 返回链接的位置（★ 提示与进度之间，桌面与手机各一处）不变。
  - 查单模式不变：待支付仍为 `order.lookup_no_pay` 信息提示条，没有 `account.order_pay`。
  - 新增导出函数 `continuePayment(orderNumber, navigate, stash = stashMemberPayOrder)`：先经 SHOP-TASK-158 的 `stashMemberPayOrder` 写入该单订单号（覆盖之前暂存的值），再以站内导航前往 `/pay`。导航只带路径，订单号不进地址、查询参数与历史状态。
  - `TrackOrderViewProps` 新增可选的 `onPay`；链接的点击处理照 `PayResultPage.tsx` 的 `result.track` 写法：普通左键点击时 `preventDefault` 并调用 `onPay`（由它导航），修饰键点击交给浏览器。`MemberOrderPage` 传入 `onPay`，以当前状态里的订单号调用 `continuePayment(orderNumber, navigate)`。处理函数都不返回 Promise。
  - 未改 RefundPage.tsx 引用的导出（`LineName`、`SessionExpired`、`TRACK_ORDER_PATH`、`usePrice`、`OrderScreen`）及 `TrackOrderMode` 等其他导出的类型与签名。
- [x] 退出清除暂存（`frontend/src/pages/AccountPage.tsx`）：`logOut` 在 ok 与 signed_out 时，除现有的 `clearMemberOrder` 外另经 SHOP-TASK-158 的 `clearMemberPayOrder` 清除当前订单暂存（模块变量与 sessionStorage 两处），之后照旧重读会话并以 replace 前往首页；csrf、failed、network 的去向不变，也不清除暂存。`logOut` 的签名未改。
- [x] 样式（`frontend/src/styles/site.css`）：只加 `.site-order__pay { justify-self: start; }`（取自 P09-pending 的 `justify-self: start`），桌面与手机相同，没有媒体查询里的差异；不声明颜色、字体与圆角，未改 `acuven-shop.css`。
- [x] 测试：
  - `frontend/src/pages/TrackOrderPage.test.tsx`：
    - 「shows neither order.lookup_no_pay nor account.order_pay for an unpaid order」按验收改为「shows account.order_pay but not order.lookup_no_pay for an unpaid order」（SHOP-TASK-154 的暂缓至此取消）：会员模式待支付订单有指向 `/pay` 的 `account.order_pay` 主按钮链接、全页只有这一个 `/pay` 链接，仍没有 `order.lookup_no_pay` 与任何按钮，仍显示 `order.status_awaiting`（三语）。原来的「没有 Continue payment 三语原文」与「没有 href="/pay"」两条断言因此改写，其余断言不变。
    - 新增「member mode: continue payment (SHOP-TASK-163)」：其他状态没有 `account.order_pay` 与 `/pay` 链接（三语）；`account.order_pay` 与查单模式的 `order.lookup_no_pay` 都是右栏最后一项且只有一份，`account.orders` 仍是桌面与手机各一处、在进度之前；查单模式各状态都没有 `account.order_pay` 与 `/pay` 链接，待支付仍有 `order.lookup_no_pay`（三语）；`continuePayment` 先写入暂存再前往 `/pay`；默认经 `stashMemberPayOrder` 写入，导航时模块变量与 sessionStorage 替身里已是该单、覆盖了之前的另一张，导航去向只是 `/pay`。
    - `afterEach` 另加 `clearMemberPayOrder()` 复原暂存；会员模式「shows only dictionary text」一条现在也覆盖了待支付订单上的 `account.order_pay`（未改该条）。
  - `frontend/src/pages/AccountPage.test.tsx`：新增「logging out clears the current pay order (SHOP-TASK-163)」：退出 ok 与 signed_out 时暂存（模块变量与 sessionStorage 替身）与交接都已清除、照常 replace 到 `/`；csrf 与 failed 时暂存保留、只更新页面状态。`afterEach` 另加 `clearMemberPayOrder()`；现有断言未改。
  - 以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击；fetch 替身沿用两个文件现有的参数带类型的 `vi.fn((input, init) => …)`，sessionStorage 以 `vi.stubGlobal` 的替身代替。每条测试的注释写明守住的是 UX、DESIGN 1.14 或本任务验收的哪一句。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 视觉稿 P09-pending 只有一张 640 像素的片段，把 `order.lookup_no_pay` 提示条与「My orders + Continue payment」一组上下画在一起，那一组以分隔线与上方隔开、`account.orders` 在主按钮之上。本任务按验收把 `account.order_pay` 单独放在右栏最后（查单模式提示条的位置），`account.orders` 仍在 ★ 提示与进度之间（SHOP-TASK-154 的位置），所以没有这一组，也没有分隔线；分隔线的颜色（`var(--rule)`）site.css 也写不了，未搬。
  - 手机上链接同样靠左、不占满宽度（视觉稿没有手机版；验收写桌面与手机相同）。
  - 修饰键点击（新标签页等）不写入暂存，交给浏览器打开 `/pay`；新标签页里会员没有当前订单，按 UX 0.13 P06 显示 `pay.member_reopen` 与 `account.orders`。
  - 写入暂存用的是 `MemberOrderPage` 状态里打开时取到的订单号（与显示的订单是同一张）。
- 留给后续任务：注销（P13 设置段）时清除暂存留给 SHOP-TASK-167。
- 改动只在 `frontend/src/pages/TrackOrderPage.tsx`、`frontend/src/pages/TrackOrderPage.test.tsx`、`frontend/src/pages/AccountPage.tsx`、`frontend/src/pages/AccountPage.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`router.tsx` 与 `App.tsx` 不需要改，未改。未改 `frontend/src/api/`、`RefundPage.tsx`、`PayPage.tsx`、`PayResultPage.tsx`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX 0.13 P09 与 P06「会员的当前订单」、DESIGN 1.14 第 7 条与 HANDOFF 0.63 自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收，未对真实后端接口验证。

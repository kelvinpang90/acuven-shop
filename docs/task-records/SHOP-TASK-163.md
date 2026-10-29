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

## 登记时的验收标准（原文）

2026-10-29 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.13 的 P09、docs/UX-COPY.md 0.14 与 docs/HANDOFF.md 0.63 为准，样式以 docs/design/pages/ 的 P09-pending（account.order_pay 为主按钮样式的链接）为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.14 逐字加入 account.order_pay，写法与相邻条目相同（每种语言一行）
      - 继续支付——会员模式下状态为待支付（awaiting_demo_payment）的订单显示指向 /pay 的 account.order_pay（主按钮样式的链接，放在订单状态区里查单模式显示 order.lookup_no_pay 的位置，桌面与手机相同；account.orders 返回链接的位置不变；视觉稿 P09-pending 只有一张 640 像素的片段，与此不同处记入任务记录）；点击时先经 SHOP-TASK-158 写入该单订单号（覆盖之前暂存的值），再以站内导航前往 /pay；订单号不进地址、查询参数与历史状态；其他状态不显示；查单模式不变（待支付仍为 order.lookup_no_pay，没有 account.order_pay）
      - 退出清除暂存——AccountPage.tsx 的退出在 ok 与 signed_out 时，除现有的 clearMemberOrder 外另经 SHOP-TASK-158 清除当前订单暂存，其余去向（重读会话、replace 前往首页、csrf、failed、network）不变
      - 代码结构——沿用两个页面现有的写法（导出的纯函数与异步函数、只依赖 props 的 View；写入暂存与导航经回调传入，便于测试）；不改 RefundPage.tsx 引用的 TrackOrderPage.tsx 导出的类型与签名；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch、sessionStorage 与路由以替身代替）验证意图（以 renderToStaticMarkup 渲染 View、直接调用导出的函数，不模拟点击）——frontend/src/pages/TrackOrderPage.test.tsx 会员模式的待支付订单有指向 /pay 的 account.order_pay、其他状态没有，点击时先写入暂存再前往 /pay，查单模式仍只有 order.lookup_no_pay；frontend/src/pages/AccountPage.test.tsx 退出 ok 与 signed_out 时清除暂存、csrf 与 failed 时不清除；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 Kelvin 决定的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - 现有测试只在必要时修改并写明原因——TrackOrderPage.test.tsx「shows neither order.lookup_no_pay nor account.order_pay for an unpaid order」一条改为会员模式待支付订单显示 account.order_pay 且仍没有 order.lookup_no_pay（SHOP-TASK-154 的暂缓至此取消），其余断言不变；AccountPage.test.tsx 现有断言不变
      - docs/task-records/SHOP-TASK-163.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；继续支付、退出清除暂存、偏离、验证到什么程度，只写实际存在的测试，写明未做浏览器验收，并写明注销时的清除留给 SHOP-TASK-167）
      - 改动只落在 allowed_change_paths（不改 frontend/src/api/、RefundPage.tsx、PayPage.tsx 与 PayResultPage.tsx；router.tsx 与 App.tsx 按页面任务的固定写法列入，不需要时不改）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.14 提交 00f8f34 与 HANDOFF 0.63），不合并、不直推
```

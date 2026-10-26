# SHOP-TASK-154 订单详情 P09 的会员模式与会员下单订单的券、积分两行

设计闸门：DESIGN 1.13（提交 450c2fa）「权限与资料保护」第 3、5 条（会话到期与服务端授权检查；会员仅能访问自己认领或下单的订单，确认收货规则与查单页相同，写操作另须 CSRF 令牌）、「订单与退款状态」「失败、并发与重试」（确认收货幂等）与「计价、优惠、积分与库存」第 3、7 条（100 积分抵 RM1；已有订单按快照），以及 HANDOFF 0.61。依据 docs/UX.md 0.13 的 P09（会员模式；M3）、docs/UX-COPY.md 0.14，样式取视觉稿 `docs/design/pages/P09-desktop.html`、`P09-phone.html` 与 `P09-pending.html`（`account.orders` 链接）。读取会话经 SHOP-TASK-151（`api/memberSession.ts`），读取订单、确认收货与订单号交接经 SHOP-TASK-150（`api/memberOrders.ts`）。

- [x] 路由与页面表：`/account/order` 加进 `frontend/src/router.tsx` 的路由表（`/account` 之后、后台页之前，并补了路由表注释）与 `frontend/src/App.tsx` 的页面表，指向 `TrackOrderPage.tsx` 另导出的 `MemberOrderPage`，套前台框架。
- [x] 文案：本页用到的键（`account.orders`、`checkout.summary_coupon`、`checkout.summary_points` 等）字典里都已有，`frontend/src/i18n/copy.ts` 未改。没有用到 UX-COPY 以外的文字。
- [x] 会员模式的打开（`openMemberOrder`、纯函数 `openStep`、`takeMemberOrder`）：
  - 打开时（effect 里经 `AdminOrderDetail.tsx` 的 `startDeferred`）调用读取对象的 `reload` 重读一次会话；读完之前为 `<main class="site-order" aria-busy="true">` 的空主体。
  - guest：以路由的 replace 进入 `/login`。failed（含网络中断）：只显示 `common.error_retry`。重读之后状态不会是 unknown，万一是也按 failed。
  - member：先取 `orderLookup.ts` 的 `submittedRefundOrder`（P10 提交后交来），取到即调用 `clearSubmittedRefund` 清除，在页面状态里记下 `submitted` 以显示 `refund.submitted`；没有时取 `memberOrderInView()`（P13 交接，读取不清除，P13 交出新值时覆盖）；都没有时以 replace 回到 `/account`（Kelvin 2026-10-24 决定）。订单号只在模块内存与页面状态里。
  - 取到订单号后另一个 effect 经 `readMemberOrder` 读取（`loadMemberOrder`、纯函数 `readStep`），切换语言时以新语言重新读取同一订单并中止上一次读取。signed_out：重读会话后以 replace 进入 `/login`；not_found：以 replace 回到 `/account`；failed 与 network：照查单模式显示 `common.error_retry`（首次读取失败时页面只有这一句，已显示订单时保留订单）。
  - 会员模式没有 `order.session_expired` 整页：页面状态（`MemberOrderState`，另立的类型）经 `memberScreen` 换成 View 的 `OrderScreen` 时只有 loading、error、ready。
- [x] 会员模式的内容：`TrackOrderView` 新增可选的 `mode` 属性（`TrackOrderMode`，不给时为查单模式），以 props 区分两种模式，只依赖 props。
  - 与查单模式相同：标题、状态、★ `order.demo_hint`、进度、各行、金额明细、收货资料、确认收货、退款部分、全部已退冻结、`refund.submitted`。
  - `order.lookup_access` 与 `order.lookup_another` 换成去 `/account` 的 `account.orders`，桌面与手机各一处（`site-desktop-only` / `site-phone-only` 的两个链接，位置同查单授权一行，在 ★ 提示之后、进度之前）。
  - `order.request_refund` 的去向为 `memberRefundPath()`：照 `CheckoutPage.tsx` 的 `loginPath` 以 `isRoutePath("/account/order/refund")` 判断，经 `mode.refundTo` 传给 View；为 null 时不渲染这个链接，截止时间、累计已退、剩余可退与申请记录照常。`/account/order/refund` 由 SHOP-TASK-155 加入路由表，届时链接自动出现。查单模式的去向仍是 `/track/order/refund`。
  - 待支付订单不显示 `order.lookup_no_pay`，也不显示 `account.order_pay`（HANDOFF 0.61 第（3）条）。
- [x] 确认收货（`confirmMemberOrder`、纯函数 `confirmStep`、`csrfStep`、`startConfirm`、`startChecking`）：
  - 经 `runMemberConfirmReceipt` 提交，带打开时会话里的 CSRF 令牌，幂等键照查单模式用 `receiptKey`（只有网络中断后确认仍为 demo_shipped 时沿用记下的键）；进行中按钮禁用，网络中断后确认订单期间显示 `common.network_check`。
  - read、error、retry 与查单模式相同；signed_out：重读会话后以 replace 进入 `/login`；not_found：以 replace 回到 `/account`。
  - csrf：重读会话——member 时换用新令牌并显示 `common.error_retry`；guest 时以 replace 进入 `/login`；failed（含网络中断）时保留原令牌并显示 `common.error_retry`；同时按接口重新读取到的订单显示（没有时沿用原来的）。
  - 查单模式的打开、读取与确认收货代码未改。
- [x] 券与积分两行：`AmountRows` 在 `placed_by_member` 为真时（两种模式，桌面与手机折叠）在小计之后依次加 `checkout.summary_coupon`（`coupon_discount_sen`）与 `checkout.summary_points`（`points_redeemed` 按仙格式化，1 积分即 1 仙），为 0 时也显示，金额原样经 `common.price_myr` 格式化、不加负号，不在浏览器计算。`placed_by_member` 为假（游客订单与后来被认领的游客订单）时仍只有三行。
- [x] 代码结构照 `AdminStoreDesignPage.tsx`：导出把请求结果变成页面状态或跳转的纯函数（`openStep`、`readStep`、`confirmStep`、`csrfStep`、`memberScreen`）、接收 AbortSignal 与回调（`MemberOrderMoves`：show、update、replace）的异步函数（`openMemberOrder`、`loadMemberOrder`、`confirmMemberOrder`）与只依赖 props 的 View。离开页面时中止订单读取与确认收货（页面的 AbortController），之后不再更新或跳转；会话的重读（`reader.reload()`）是共享读取，不带 signal、不中止，回来时已离开则不据其结果更新或跳转。传给事件属性的处理函数不返回 Promise。RefundPage.tsx 引用的导出（`OrderScreen`、`SessionExpired`、`LineName`、`usePrice`、`TRACK_ORDER_PATH`）类型与签名未改。
- [x] 样式写在 `frontend/src/styles/site.css`：`.site-order__orders` 靠左单独一行（桌面），767px 及以下让手机那份显示为块；不声明颜色、字体与圆角。未改 `acuven-shop.css`。
- [x] 测试 `frontend/src/pages/TrackOrderPage.test.tsx` 新增（vitest 与 react-dom/server，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，会话读取用 `createMemberSessionReader()` 配同一个 fetch 替身，路由的 replace 以回调替身代替；以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击）：
  - 打开：guest 去 `/login`；读取会话失败与网络中断只有 `common.error_retry`；没有订单号回 `/account`；交接的订单号读取（读完前的空主体、请求体只有订单号、会话读取不带页面的 signal 而订单读取带）；`submittedRefundOrder` 优先于交接、取到即清除，之后取交接的值，P13 交出别的订单号后取新值；not_found 回 `/account`；signed_out 先重读会话再去 `/login`；读取 failed 与 network 的提示；三种语言以同一订单号重新读取；离开页面后不更新；各状态都不是 `order.session_expired`。
  - 内容：`account.orders` 桌面与手机各一处去 `/account`，没有 `order.lookup_access`、`order.lookup_another` 与去 `/track` 的链接，其余各项照常（三语）；位置在 ★ 提示与进度之间；待支付订单没有 `order.lookup_no_pay` 与 `account.order_pay`（按 UX-COPY 三语原文检查，该键尚未进字典）；全部已退冻结；页面文字全部来自字典。
  - 退款入口：props 给出去向时指向 `/account/order/refund`，没有时不渲染，截止时间与已退、可退照常；去向随路由表而定（不断言该路径在不在表里）。
  - 确认收货：done 与 closed 后按重新读取的订单显示（请求头带令牌与幂等键）；进入 confirming 与 `receiptKey` 的沿用规则；signed_out、not_found；csrf 后重读为 member（换新令牌）、guest（去 `/login`）、failed 与 network（保留原令牌）；其他失败；网络中断后的 checking、retry 与已完成；离开页面后不更新。
  - 券与积分两行：两种模式、桌面与手机折叠里五行的顺序与金额（三语，取视觉稿 P09-desktop 的数）；为 0 时也显示；游客订单两种模式都只有三行；金额原样（对不上也不改，1 积分为 RM 0.01）。
  - 每条测试的注释写明守住的是验收、UX、HANDOFF 0.61 或 Kelvin 决定的哪一句。查单模式的现有测试与断言未改（只在文件开头加了 import 与复原交接、fetch 的 `afterEach`）。
- [x] 现有测试只在必要时修改：
  - `frontend/src/router.test.tsx`：路由表断言加入 `/account/order` 并断言 `isRoutePath("/account/order")`；另加两条：路径不带段值、`/account/order` 在前台框架里渲染为空主体。`/account/order/refund` 不在表里的断言不变。
  - `frontend/src/pages/PayResultPage.test.tsx`「has the register link but no order detail or points」：只删去断言 `/account/order` 不在路由表的一行（会员模式进表后不再成立），加了一行说明原因的注释；`isRoutePath` 文件里另有用处，import 不变。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 会员模式的待支付订单本批不显示 `account.order_pay`（UX P09 线框有，由结账会员路径的任务加入），也不显示 `order.lookup_no_pay`（HANDOFF 0.61 第（3）条）。
  - 从 P07 的 `result.track` 进入会员模式留给会员结账任务，本任务未改 `PayResultPage.tsx`。
  - `account.orders` 照视觉稿 P09-pending 画成单独一行的普通链接；UX-COPY 的备注称其为「返回按钮」，UX 线框写作 `([account.orders])`，与查单模式同样写法的 `([order.lookup_another])` 现在也是链接，视为不冲突，未改成按钮。
  - P09-pending 里这个链接在待支付说明之下、与 `account.order_pay` 一组；本任务按 UX 线框把它放在标题与 ★ 提示之后、进度之前（与查单授权一行同一位置），所有状态都显示。
  - 视觉稿里 site.css 写不了的样式不搬：P09-desktop 查单链接的字重（会员模式的链接视觉稿没有字重）、P09-pending 链接上方的分隔线颜色。没有借用其他组件的类来取颜色。
  - 会员模式的读取会话失败只显示 `common.error_retry`，用查单模式现有的读取失败画面（`ErrorNotice`）。
  - 会员读取会话为 guest 时不取 P10 交来的订单号（只在 member 时取并清除）。
- 留给后续任务：`/account/order/refund`（P10 会员模式）由 SHOP-TASK-155 加入路由表，届时退款入口自动出现；`account.order_pay` 与 P07 的 `result.track` 由结账会员路径的任务加入。
- 改动只在 `frontend/src/pages/TrackOrderPage.tsx`、`frontend/src/pages/TrackOrderPage.test.tsx`、`frontend/src/pages/PayResultPage.test.tsx`、`frontend/src/router.tsx`、`frontend/src/router.test.tsx`、`frontend/src/App.tsx`、`frontend/src/styles/site.css` 与本文件；未改 `frontend/src/api/`、`RefundPage.tsx`、`PayResultPage.tsx`、`copy.ts`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX P09、HANDOFF 0.61 与 `api/memberOrders.ts`、`api/memberSession.ts` 的回答归类自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（桌面与手机的实际排版、刷新后回到 `/account`、切换语言后的重新读取都未在浏览器里看过），未对真实后端接口验证。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.13 的 P09、docs/UX-COPY.md 0.14 与 docs/HANDOFF.md 0.61 为准，样式以 docs/design/pages/ 的 P09-desktop、P09-phone 与 P09-pending（account.orders 链接）为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.14 逐字加入本页用到而字典里还没有的键（预计没有），写法与相邻条目相同（每种语言一行）
      - 路由 /account/order 加进 frontend/src/router.tsx 的路由表与 frontend/src/App.tsx 的页面表（指向 TrackOrderPage.tsx 另导出的会员模式页面组件），套前台框架；会员模式打开时先经 SHOP-TASK-151 的读取对象重读会话（读完前为 aria-busy 的空主体；guest 时用 replace 进入 /login；failed 时只显示 common.error_retry），member 时取订单号——先取 orderLookup.ts 的 submittedRefundOrder（从 P10 提交回来），取到后即照查单模式调用 clearSubmittedRefund 清除（只在页面状态里记下，用来判断是否显示 refund.submitted），没有时取 SHOP-TASK-150 交接的订单号；都没有时用 replace 回到 /account；有时经 SHOP-TASK-150 读取详情——signed_out 时重读会话并用 replace 进入 /login，not_found 时用 replace 回到 /account，failed 与 network 照查单模式显示 common.error_retry；切换语言时重新读取同一订单；会员模式不出现 order.session_expired 整页
      - 会员模式的内容——与查单模式相同（标题、状态、★ order.demo_hint、进度、各行、金额明细、收货资料、确认收货、退款部分、全部已退冻结、refund.submitted），不同处只有——order.lookup_access 与 order.lookup_another 换成去 /account 的 account.orders（桌面与手机各一处，UX P09 线框「会员模式：([account.orders]) → P13」）；order.request_refund 指向 /account/order/refund，该路径进入路由表之前不渲染这个链接（截止时间、已退与可退照常），照 frontend/src/pages/CheckoutPage.tsx 的 loginPath 写法以 isRoutePath 判断并以 props 传给 View；待支付订单不显示 order.lookup_no_pay 与 account.order_pay（记入任务记录）
      - 确认收货——会员模式经 SHOP-TASK-150 的会员版提交去向，以会话里的 CSRF 令牌提交，幂等键规则与查单模式相同（receiptKey）；signed_out 时重读会话并用 replace 进入 /login；not_found 时用 replace 回到 /account；csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时用 replace 进入 /login，重读得到 failed（含网络中断）时保留原令牌并显示 common.error_retry；read、error 与 retry 与查单模式相同；查单模式的行为不变
      - 券与积分两行——两种模式下 placed_by_member 为真时，金额明细（桌面与手机折叠）依次为 cart.subtotal、checkout.summary_coupon（coupon_discount_sen）、checkout.summary_points（points_redeemed，1 积分抵 1 仙，按仙格式化）、checkout.summary_shipping 与 checkout.summary_total，两行为 0 时也显示，金额原样格式化、不加负号（照视觉稿 P09-desktop），不在浏览器计算；placed_by_member 为假（游客订单与后来被认领的游客订单）时仍只有三行（UX P09 M3）
      - 代码结构——新增的会员模式照 frontend/src/pages/AdminStoreDesignPage.tsx 的写法（导出把请求结果变成页面状态或跳转的纯函数、接收 AbortSignal 与回调的异步函数，TrackOrderView 只依赖 props，以 props 区分两种模式）；查单模式现有的结构与行为不重构；不改 RefundPage.tsx 引用的 TrackOrderPage.tsx 导出（OrderScreen、SessionExpired、LineName、usePrice、TRACK_ORDER_PATH）的类型与签名，会员模式需要的新状态另立类型；离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/TrackOrderPage.test.tsx 验证意图（以 renderToStaticMarkup 渲染 View、直接调用导出的函数，不模拟点击）——会员模式打开时的各分支（guest、没有订单号、not_found、signed_out 的去向，submittedRefundOrder 优先于交接，读取后即清除，之后从 P13 交出别的订单号时取交接的值）；会员模式有 account.orders、没有 order.lookup_access、order.lookup_another、order.lookup_no_pay 与 account.order_pay；退款入口在 props 给出去向时指向 /account/order/refund、没有时不渲染（测试不断言该路径不在路由表，以免 SHOP-TASK-155 加路由后失败）；确认收货各结果；两种模式下会员下单订单的五行顺序与金额、游客订单仍为三行；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 Kelvin 决定的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）；现有测试只在必要时修改并写明原因——frontend/src/router.test.tsx 的路由表断言；frontend/src/pages/PayResultPage.test.tsx「has the register link but no order detail or points」一条只删去断言 /account/order 不在路由表的那一行（游客结果页仍没有 result.track），其余不改；TrackOrderPage.test.tsx 查单模式的现有断言不变
      - docs/task-records/SHOP-TASK-154.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；会员模式的打开、内容、确认收货、券与积分两行、偏离（含待支付订单本批不显示 account.order_pay、result.track 留给会员结账任务）、验证到什么程度，只写实际存在的测试，写明未做浏览器验收）；改动只落在 allowed_change_paths（不改 frontend/src/api/、RefundPage.tsx 与 PayResultPage.tsx）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.13 提交 450c2fa 与 HANDOFF 0.61），不合并、不直推
```

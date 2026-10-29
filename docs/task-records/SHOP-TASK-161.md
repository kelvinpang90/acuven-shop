# SHOP-TASK-161 支付页 P06 的会员模式

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 5 条（会员仅能访问自己的订单，写操作另须 CSRF 令牌）、第 7 条（当前订单暂存的读取顺序与清除时机）与「Stripe 测试环境支付」第 3、5 条（发起与取消按现有授权），以及 HANDOFF 0.63（运营者落实写法第（1）项：打开时游客读取回 401 才读会员会话，游客之后的写操作与重读回 401 仍为 `pay.session_expired`；Kelvin 2026-10-26 决定：没画的会员 P06 正常态照最接近的画板实现）。依据 docs/UX.md 0.13 的 P06「会员的当前订单」、docs/UX-COPY.md 0.14，样式取视觉稿 `docs/design/pages/` 的 P06-desktop、P06-phone、P06-stripe-desktop、P06-stripe-phone-open（会员去掉 `pay.guest_access`）与 P06-P07-member-reopen。会员支付接口与暂存经 SHOP-TASK-158（`api/memberPay.ts`），会员会话经 SHOP-TASK-151（`api/memberSession.ts`）。

- [x] 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.14 逐字加入 `pay.member_reopen`（放在 `pay.guest_access` 之后、`pay.session_expired` 之前，同 UX-COPY 的顺序，每种语言一行）。没有用到 UX-COPY 以外的文字。
- [x] 判定顺序（`openPayOrder`，导出的异步函数，返回与页面无关的 `PayOpening`：member 为会员订单、暂存的订单号与令牌；guest 为游客读取取到的订单与令牌；reopen、expired、error 分别对应 `pay.member_reopen`、`pay.session_expired`、`common.error_retry`；不依赖 PayScreen 与页面的状态设置函数，暂存以 `PayStash` 参数传入，默认为 `api/memberPay.ts` 的 `stashedMemberPayOrder` 与 `clearMemberPayOrder`，供 SHOP-TASK-162 直接复用）：
  - ① 暂存里有订单号（先内存、后 sessionStorage）时经 `readMemberPayOrder` 读取：ok 后调用读取对象的 `reload` 重读会话取 CSRF 令牌——member 为会员订单；guest 与 signed_out 相同：清除暂存并转 ②；not_found 清除暂存并为 `pay.member_reopen`；failed、network 与重读会话 failed（含尚未读完）为 `common.error_retry`，不清除暂存。
  - ② 没有订单号时照现在调用 `readPayOrder`：ok 照游客显示；401 时重读会员会话（`guestExpiredStep`）——member 为 `pay.member_reopen`，guest 为 `pay.session_expired`，failed 为 `common.error_retry`；游客读取 failed 与 network 照现有写法为 `common.error_retry`，不读会话。
  - 页面（`PayPage`）在打开时（以及会员按 ② 重新判定时）调用它；判定出的访问方式（游客或会员与订单号）记在页面状态与一个 ref 里，读取的 effect 取 ref，判定完成本身不再触发读取。之后的重读（切换语言、倒计时到 0、支付途径已改变、Stripe 发起之后）：游客照现有写法调用 `readPayOrder`，401 仍为 `pay.session_expired`、不读会话；会员经 `rereadMemberOrder` 以当前语言读取同一订单号，令牌不变。
- [x] 会员 P06 的内容：`PayView` 新增可选的 `member` 属性（不给时为游客），以 props 区分。会员时与游客 P06 相同（订单号与复制、应付金额、倒计时、收货资料、两种支付途径、取消与确认框、Stripe 各状态），只不渲染 `pay.guest_access` 那条提示。订单已不是待支付或有已付款的会话时照游客转到 `/pay/result`，不清除暂存。
- [x] `pay.member_reopen` 整页：`PayScreen` 新增 `reopen`，`PayView` 渲染导出的 `MemberReopen`——信息提示（视觉稿的 ⓘ 图标，即现有 `InfoIcon`）与去 `/account`（router 的 `ACCOUNT_PATH`）的 `account.orders` 主按钮，没有其他内容。
- [x] 会员的写操作（`payAsMember`、`cancelAsMember`、`startStripeAsMember`，接收 AbortSignal 与回调 `MemberMoves`；纯函数 `memberWriteStep`、`memberStripeStep`、`memberReadStep` 把会员接口的结果分成「与游客相同」与会员自己的去向 `MemberTurn`，后者由 `settleMemberTurn` 处理）：
  - 经 `runMemberPayment`、`runMemberCancel`、`startMemberStripe` 提交，请求头带会话里的 CSRF 令牌；模拟支付的幂等键照游客用 `attemptFor`（网络中断后确认仍待支付、方式与结果相同的重试沿用原来的键）。
  - result、method_changed、unavailable、error、retry 与 Stripe 的各结果换成游客的形式，交给页面原有的游客处理（`settle`、`settleCancel`、`stripeMove`）：result 前往 `/pay/result`（Stripe 途径的取消照游客先记下交接），不清除暂存；retry 的订单配上提交时的令牌。
  - signed_out：清除暂存并按 ② 重新判定（页面回到读取中，再调用一次 `openPayOrder`，此时暂存已空，直接走 ②）。not_found：清除暂存并整页显示 `pay.member_reopen`。
  - csrf：重读会话——member 时换用新令牌并显示 `common.error_retry`；guest 时清除暂存并按 ② 重新判定；failed（含网络中断）时保留原令牌并显示 `common.error_retry`。模拟支付与取消另按接口重新读取到的仍待支付订单显示。
  - 游客的写操作代码与行为不变：Stripe 发起之后的去向从点击处理里原样移到页面内的 `settleStripe`，游客与会员共用。
- [x] 代码结构照 `AdminStoreDesignPage.tsx`：导出把请求结果变成页面去向的纯函数（`guestExpiredStep`、`memberReadStep`、`memberWriteStep`、`memberStripeStep`）、接收 AbortSignal 与回调的异步函数（`openPayOrder` 以返回值给结果；`rereadMemberOrder`、`payAsMember`、`cancelAsMember`、`startStripeAsMember`、`settleMemberTurn` 以回调），View 只依赖 props。离开页面时中止请求（读取的 effect 各自的 AbortController 与页面的 `lifetime`），之后不再更新；会员会话的重读（`reader.reload()`）是共享读取，不带 signal、不中止，回来时已离开则不据其结果更新或跳转。传给事件属性的处理函数不返回 Promise。PayResultPage.tsx 引用的导出（`ExpiresLine`、`InfoIcon`、`OrderCard`、`PAY_PATH`、`RecipientDetails`、`SessionExpired`、`useCountdown`、`useMyStates`）类型与签名未改。
- [x] 样式写在 `frontend/src/styles/site.css`：`.site-pay--reopen` 桌面为提示与按钮间距 20px、按钮靠左（宽度与上下内边距沿用 `.site-pay`，同视觉稿的 880px、56px、96px）；767px 及以下间距 16px、上下内边距 32px 与 40px、按钮占满宽度。不声明颜色、字体与圆角，未改 `acuven-shop.css`。
- [x] 测试 `frontend/src/pages/PayPage.test.tsx` 新增（vitest 与 react-dom/server，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，会话读取用 `createMemberSessionReader()` 配同一个 fetch 替身，暂存以 `PayStash` 替身代替；以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击）：
  - ①：ok 与会话 member（请求体只有订单号，订单读取带页面的 signal、会话读取不带，暂存不清除）；signed_out 与「ok 而会话为 guest」都清除暂存一次后转 ② 照游客显示；not_found 清除暂存并为 reopen、不读会话与游客接口；订单读取 failed、network 与重读会话 failed、network 都为 error 且不清除暂存；离开页面后（读取回来时与重读会话期间）不给结果、不清除暂存。
  - ②：没有订单号时只读游客接口、不读会话；游客 401 之后会话 member、guest、failed、network 四种结果；游客读取失败不读会话；`guestExpiredStep` 的四种会话状态。
  - 内容：三语下模拟途径与 Stripe 各状态，会员页与游客页去掉 `pay.guest_access` 那条提示之后逐字相同，游客页仍有；会员页其余各项照常；`pay.member_reopen` 整页只有提示与去 `/account` 的 `account.orders`、没有按钮与订单内容；会员页各状态与该整页的文字全部来自字典。
  - 重读：三语以同一订单号读取；重读回 401 清除并重新判定，404 清除并显示 reopen，failed 与 network 交给游客的失败显示、不清除；`memberReadStep` 各结果。
  - 写操作：模拟支付的请求头（令牌与幂等键）与请求体，result 交给游客处理且不清除暂存；支付、取消、Stripe 发起回 401 与 404 都清除暂存并分别重新判定与显示 reopen；支付 403 之后重读会话为 member（新令牌）、guest（清除并重新判定）、failed 与 network（保留原令牌），取消与 Stripe 发起的 403 同样换用新令牌；method_changed、unavailable、failed 与 409 order_not_payable 原样交给游客处理；网络中断后为 retry（同一幂等键与提交时的令牌）并通知页面显示 `common.network_check`；Stripe 跳转、转人工核对与不可用交给游客处理，取消成功为 result；csrf 之后重读会话期间离开页面则不调用任何处理；`memberWriteStep` 与 `memberStripeStep` 各结果。
  - 每条测试的注释写明守住的是 UX、DESIGN、HANDOFF 0.63 或验收的哪一句。
- [x] 现有测试只在文件开头加了 import 与复原全局 fetch 的 `afterEach`，现有断言未改（现有测试里没有打开函数的测试，不需要补替身）。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 会员 P06 正常态没有视觉稿，照 HANDOFF 0.63 按游客 P06（P06-desktop、P06-phone、P06-stripe-desktop、P06-stripe-phone-open）去掉 `pay.guest_access` 实现；去掉之后订单卡片的最后一项是倒计时（Stripe 进行中等不显示倒计时时为应付金额）。
  - ② 的会员会话检查只用于打开时游客读取回 401 的那一次（HANDOFF 0.63 第（1）项）：游客之后的重读、写操作与 Stripe 发起回 401 仍直接显示 `pay.session_expired`，不读会话。会员按 ② 重新判定（signed_out，或 csrf 后会话为 guest）时整个判定重来一次，此时游客读取回 401 也会读一次会话，视为同一次「打开时」的判定。
  - 打开时的判定为 `common.error_retry` 时页面还没有访问方式，之后切换语言会重新走一次判定，而不是重读。
  - 会员 Stripe 发起回 csrf 时换用会话的新令牌，不再像游客那样重新读取订单取令牌；模拟支付与取消回 csrf 时按接口重新读取到的订单显示，同时清除网络中断后记下的重试（与游客 csrf 之后的 error 相同）。
  - P06-P07-member-reopen 手机版的按钮用 `acs-btn--block`，本页改由 site.css 在 767px 及以下让按钮占满宽度，类名照桌面只用 `acs-btn acs-btn--primary`。视觉稿里这一整页没有 site.css 写不了的颜色、字号或字重需要搬；页头、页脚与横幅由现有前台框架渲染。没有借用其他组件的类来取颜色。
- 改动只在 `frontend/src/pages/PayPage.tsx`、`frontend/src/pages/PayPage.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`router.tsx` 与 `App.tsx` 不需要改（`/pay` 与 `/account` 已在路由表里，`ACCOUNT_PATH` 已导出）。未改 `frontend/src/api/`、`PayResultPage.tsx`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX P06「会员的当前订单」、HANDOFF 0.63 与 `api/memberPay.ts`、`api/memberSession.ts` 的回答归类自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（会员 P06 与 `pay.member_reopen` 整页在桌面与手机的实际排版、刷新与从 Stripe 返回后的读取、切换语言后的重读都未在浏览器里看过），未对真实后端接口验证。

## 登记时的验收标准（原文）

2026-10-29 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.13 的 P06、docs/UX-COPY.md 0.14 与 docs/HANDOFF.md 0.63 为准，样式以 docs/design/pages/ 的 P06-desktop、P06-phone、P06-stripe-desktop 与 P06-stripe-phone-open（会员去掉 pay.guest_access）和 P06-P07-member-reopen 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.14 逐字加入 pay.member_reopen，写法与相邻条目相同（每种语言一行）
      - 判定顺序 ①——打开时先经 SHOP-TASK-158 读取当前订单号（先内存、后 sessionStorage）；有时经会员接口读取：ok 后经读取对象重读会话取 CSRF 令牌（会员读取不返回令牌），member 时显示会员 P06，guest 时与会员读取回 401 相同；signed_out 时清除暂存并转 ②；not_found 时清除暂存并整页显示 pay.member_reopen 与去 /account 的 account.orders；failed 与 network（含重读会话 failed）时显示 common.error_retry、不清除暂存
      - 判定顺序 ②——没有订单号时照现在调用游客接口，取到订单照游客显示（含 pay.guest_access）；游客读取回 401 时经读取对象重读会员会话——member 时整页显示 pay.member_reopen 与 account.orders，guest 时为现在的 pay.session_expired 整页，failed 时显示 common.error_retry；游客路径其余行为不变
      - 会员 P06 的内容——与游客 P06 相同（订单号与复制、应付金额、倒计时、收货资料、两种支付途径、取消与确认框、Stripe 各状态），不显示 pay.guest_access；切换语言时重新读取同一订单
      - 会员的写操作——模拟支付、取消与 Stripe 发起经 SHOP-TASK-158 的会员版，以会话里的 CSRF 令牌提交，幂等键规则与游客相同；result 时与游客相同前往 /pay/result（不清除暂存，由结果页在结果确定后清除）；signed_out 时清除暂存并按 ② 重新判定；not_found 时清除暂存并显示 pay.member_reopen；csrf 时重读会话——member 时换用新令牌并显示 common.error_retry，guest 时清除暂存并按 ② 重新判定，重读得到 failed 时保留原令牌并显示 common.error_retry；method_changed、unavailable、error、retry 与 Stripe 跳转与游客相同；游客的写操作行为不变
      - 代码结构——照 frontend/src/pages/AdminStoreDesignPage.tsx 的写法（导出把请求结果变成页面状态的纯函数、接收 AbortSignal 与回调的异步函数，View 只依赖 props，以 props 区分游客与会员）；打开时的判定写成导出的异步函数，返回与页面无关的结果（会员订单与令牌、游客读取结果、pay.member_reopen、pay.session_expired 或 common.error_retry 之一），不依赖 PayScreen 与 PayPage 的状态设置函数，供 SHOP-TASK-162 的结果页不改 PayPage.tsx 直接复用；游客路径现有的结构与行为不重构，不改 PayResultPage.tsx 引用的 PayPage.tsx 导出（ExpiresLine、InfoIcon、OrderCard、PAY_PATH、RecipientDetails、SessionExpired、useCountdown、useMyStates）的类型与签名；离开页面时中止请求，之后不再更新；会员会话的重读是共享读取，不中止，离开页面后不再据其结果更新或跳转；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch、sessionStorage 与路由以替身代替）在 frontend/src/pages/PayPage.test.tsx 验证意图（以 renderToStaticMarkup 渲染 View、直接调用导出的函数，不模拟点击）——① 的各分支（ok 与会话、signed_out 转 ②、not_found、failed 不清除）、② 的游客 401 之后三种会话结果；会员 P06 没有 pay.guest_access、游客仍有；pay.member_reopen 整页只有提示与去 /account 的 account.orders；会员写操作各结果的去向与暂存的清除或保留；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 Kelvin 决定的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）；现有测试只在必要时修改并写明原因（打开函数多了会话读取与暂存参数的几条只补替身，期望的页面状态不变）
      - docs/task-records/SHOP-TASK-161.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；判定顺序、会员内容、写操作、偏离（含会员正常态照游客去掉 pay.guest_access、② 只用于打开时的游客 401）、验证到什么程度，只写实际存在的测试，写明未做浏览器验收）；改动只落在 allowed_change_paths（不改 frontend/src/api/ 与 PayResultPage.tsx；router.tsx 与 App.tsx 按页面任务的固定写法列入，不需要时不改）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.14 提交 00f8f34 与 HANDOFF 0.63），不合并、不直推
```

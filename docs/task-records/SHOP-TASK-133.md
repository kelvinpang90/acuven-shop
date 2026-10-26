# SHOP-TASK-133 结果页 P07 的 Stripe 确认中与重查

- [x] 按 `docs/UX.md` 0.11「P07 Stripe 测试」、`docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 4、5、6 条与 `docs/HANDOFF.md` 0.54、0.55 的落实写法，在 `frontend/src/pages/PayResultPage.tsx` 接上 Stripe 途径：订单仍待支付且有 Stripe 记录时调用 SHOP-TASK-132 的 `confirmStripe`，按结果显示成功、确认中（自动重查与手动再查）、尚未付款或迟到付款的退款说明；从 P06 取消时会话已付款进入的成功区另显示一句。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，支付状态的显示（确认与退款都在服务端）。不改 P06、`frontend/src/api/pay.ts`、路由、后端与设计，不装依赖。
- 结构（照 `AdminStoreDesignPage.tsx` 与本页现有写法）：导出纯函数 `readStep`（读取结果 → show / pay / confirm / failed）、`confirmStep`（确认结果 → show / reread / confirming）、`keepOnFailure`、`startChecking` 与 `confirming`；接收 AbortSignal 与回调（`ResultMoves`：show、update、replace、notice）的异步函数 `openResult`（读取）、`confirmResult`（确认）、`recheckAutomatically`（自动重查）与 `checkAgain`（手动再查）；只依赖 props 的 `PayResultView`（新增 `paidNotCancelled` 与 `onCheckAgain`）。默认导出的页面只持有状态、调用这些函数并接上路由；`onCheckAgain` 的处理函数在异步调用前加 `void`。
- 判定（`readStep`、`confirmStep`）：
  - 仍待支付且 `stripe_state` 不为 null：先显示读取中（`aria-busy`），以读取给的 CSRF 令牌调用 `confirmStripe` 一次。
  - `paid`、`refunded`：重新读取订单并按读到的状态显示；这次读取 network 或 failed 时按确认中处理并进入自动重查；401 整页 `pay.session_expired`；读到仍待支付且有 Stripe 记录时同样按确认中。
  - `not_paid`：显示 `result.stripe_not_paid` 与链到 `/pay` 的 `result.stripe_back_to_pay`，不再重查。
  - `confirming`、`failed`（含 200 而结果不认得）、`csrf`、`network`：确认中；`expired`：整页 `pay.session_expired`。
  - 仍待支付而 `stripe_state` 为 null：`payment_route` 为 stripe 一律用路由的 replace 换成 `/pay`（即使有模拟支付失败的记录也不显示失败页）；simulated 照现有 `resultKind`（失败页或换成 `/pay`）。`resultKind` 未改。
- 确认中与重查：
  - 显示等待图标、`result.confirming_title`、`result.confirming_body`（两者所在的标题组标 `role="status"`，照视觉稿）、`OrderCard` 与游客的 `result.guest_next`；不显示 `result.demo_hint`、`pay.expires` 与收货资料（UX 0.11 线框没有）。
  - 自动重查（`recheckAutomatically`）：每次经可注入的 delay（默认 `api/pay.ts` 的 `wait`）等 `AUTO_RECHECK_MS`（5000）后重查，最多 `AUTO_RECHECKS`（12）次。每次重查先重新读取订单（取得新令牌）：已不是待支付就按读到的状态显示并停止；仍待支付且有 Stripe 记录时再确认一次（结果照上面的判定）；读取 network 或 failed 与确认失败一样计为一次、停在确认中（沿用上一次读到的订单）；401 整页 `pay.session_expired`。自动重查期间不切换为读取中。第 12 次仍为确认中时停止，显示 `result.confirming_slow` 与 `result.check_again` 按钮。
  - 手动再查（`checkAgain`）：每点一次重查一次；进行中按钮 `disabled` 并标 `aria-busy="true"`（`startChecking`），仍为确认中时按钮恢复可用。
  - 读取、确认与重查共用当前读取的 AbortController；离开页面、切换语言或失败页倒计时到 0 重新读取时中止，中止后的结果不再更新页面。切换语言照现有写法重新读取，自动重查次数从头计。
- 迟到退款：已取消且 `stripe_refunded` 为 true 时，以 `result.stripe_late_refund` 代替 `result.cancelled` 或 `result.cancelled_by_you`（不论取消方），其下照旧是 `result.continue`。
- 取消时已付款：首次读到订单时（`openResult` 的 `takeNotice`，页面以 ref 记住已取过）以其订单号调用一次 `takePaidNotCancelled`；为 true 且订单按成功显示时，成功区顶部另显示 `result.paid_not_cancelled`（`acs-alert--success`，照 P07-stripe-states）。只在页面状态里，切换语言不重取也不清除，刷新后模块重新加载、不再显示。取的时机在读取返回之后，StrictMode 下先被中止的一次 effect 不会消耗记录。
- 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.12 逐字加入 `result.confirming_title`、`result.confirming_body`、`result.confirming_slow`、`result.check_again`、`result.stripe_not_paid`、`result.stripe_back_to_pay`、`result.stripe_late_refund` 与 `result.paid_not_cancelled`（放在 `result.continue` 之后，与 UX-COPY 表的顺序一致）。没有用到 UX-COPY 以外的界面文字。
- 样式（`frontend/src/styles/site.css`，只写布局与尺寸）：`.site-result__pending`（图标框 56px，手机 48px、图标 24px）、`.site-result__confirming`（标题组间距 10px，手机 16px）、`.site-result__check`（桌面靠左，手机占满宽度）、`.site-result__stripe`（尚未付款的提示与按钮一组，间距 12px，靠左）。
- 偏离（视觉稿与现有写法之间的取舍，未改设计）：
  - 确认中图标的圆形底色（`--surface-alt`）、图标颜色（`--accent`）与圆角 site.css 写不了，未搬；图标框只有尺寸与居中，也未借用 `acs-tag` 等其他组件的类来取颜色。
  - P07-stripe-confirming 的主体宽 720px、间距 24px、上内边距 72px；本页各状态共用现有 `.site-pay.site-result` 容器（880px、28px、64px），未为确认中另设。
  - P07-stripe-phone-slow 里 `result.guest_next` 是次要文字段落；本页照现有成功区与 UX 线框同一元素，桌面与手机都用信息提示框。
  - 桌面没有「重查用完」与尚未付款、迟到退款的手机视觉稿：桌面的再查按钮靠左，手机占满宽度（照 P07-stripe-phone-slow）；尚未付款的按钮在各宽度都靠左；迟到退款沿用现有已取消的排法（`result.continue` 手机占满宽度），未另加 P07-stripe-states 那组 12px 的间距。
  - 成功区顶部的 `result.paid_not_cancelled` 之下沿用现有成功区（h1 `acs-display-l` 与 56px 图标），不照 P07-stripe-states 示意的 h2 `acs-display-m` 与 40px 图标。
- 按验收标准未写死之处的取法：
  - 打开或切换语言时的读取失败照现有写法保留已显示的结果（`keepOnFailure`）；当时正显示确认中时停止自动重查并给出 `result.check_again`（`slow`），原本没有结果时为 `common.error_retry`。
  - 切换语言后若读到仍需确认，确认返回前同样按读取中显示。
- 测试（`frontend/src/pages/PayResultPage.test.tsx`，vitest 与 react-dom/server，fetch、delay 与路由以替身代替，不用假时钟、不模拟点击）：确认的四种结果与 failed（含不认得的结果）、csrf、network、expired 各自的显示；paid 之后重新读取 network、failed 与 401；确认中三语的内容、不显示 `result.demo_hint` 与 `pay.expires`；自动重查第 12 次后停止、此时才出现 `result.check_again`、每次重查用新令牌、期间不显示读取中；重查读取失败计为一次；重查中订单已付款或再确认为 paid 时按成功显示；重查 401；等待中与确认请求进行中中止后不再更新；手动再查进行中按钮禁用并标 `aria-busy`、每点一次重查一次；迟到退款代替两种已取消文案；`paid_not_cancelled` 只取一次、订单号不同为 false、只在成功区顶部显示；`stripe_state` 为 null 的待支付订单照原样处理（stripe 途径换成 `/pay`、simulated 失败页或换成 `/pay`，都不调用确认接口）；Stripe 各画面的文字全部来自字典。每条测试的注释写明它守住的 UX、HANDOFF 或验收标准原句。fetch 替身写成参数带类型的 `vi.fn((input: string, init?: RequestInit) => …)`：`api/pay.ts` 只以字符串地址调用 fetch，所以 input 标为 string，从 `mock.calls` 取地址与令牌时用 `String(…)` 收窄；请求体以 `typeof` 收窄（对 `RequestInfo | URL` 或 `BodyInit` 直接调用 `String` 会触发 `@typescript-eslint/no-base-to-string`，同 SHOP-TASK-132 的记录）。现有测试只在 props 辅助函数 `render` 里补了 `paidNotCancelled`（默认 false）与 `onCheckAgain`，其余断言未改。
- 改动只在 `frontend/src/pages/PayResultPage.tsx`、`frontend/src/pages/PayResultPage.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`frontend/src/router.tsx` 与 `frontend/src/App.tsx` 不需要改动（不新增路由）。未改 `PayPage.tsx`、`api/pay.ts`、生成的 `acuven-shop.css`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 留给之后的任务：P06 打开即为 complete 时转到本页由本页确认（SHOP-TASK-134）；P06 的取消与「取消时会话已付款」的 `rememberPaidNotCancelled` 调用（SHOP-TASK-135）。会员前端尚不存在，本任务只接游客接口。
- 验证到什么程度：人工逐条对照验收标准、UX 0.11「P07 Stripe 测试」、UX-COPY 0.12、HANDOFF 0.54、0.55 与三张视觉稿自查。未做浏览器验收（没有在真实浏览器或 Stripe 测试环境里走过返回与确认）。前端的 lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.11 的「P07 Stripe 测试」、docs/UX-COPY.md 0.12 与 docs/HANDOFF.md 0.55 为准，样式以 docs/design/pages/ 的 P07-stripe-confirming、P07-stripe-phone-slow 与 P07-stripe-states 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.12 逐字加入本任务用到的 result.confirming_title、result.confirming_body、result.confirming_slow、result.check_again、result.stripe_not_paid、result.stripe_back_to_pay、result.stripe_late_refund 与 result.paid_not_cancelled，写法与相邻条目相同（每种语言一行）
      - 判定——读到的订单仍待支付且 stripe_state 不为 null 时，以读取给的 CSRF 令牌调用 confirmStripe 一次，确认返回前按读取中显示（标 aria-busy）；paid 与 refunded 重新读取订单并按读到的状态显示（这次重新读取 network 或 failed 时按确认中处理、进入或继续自动重查，下一次重查读到的状态为准；expired 整页 pay.session_expired）；not_paid 显示 result.stripe_not_paid 与回到 /pay 的 result.stripe_back_to_pay；confirming、failed、csrf 与 network 都显示确认中；expired 整页 pay.session_expired。仍待支付而 stripe_state 为 null 时——payment_route 为 stripe 一律用路由的 replace 换成 /pay（Stripe 途径不显示失败页，订单此前即使有模拟支付失败的记录也一样），payment_route 为 simulated 照现有 resultKind 处理（失败页或换成 /pay）；不改 frontend/src/api/pay.ts 的 resultKind，Stripe 的判定写在本页
      - 确认中——显示 result.confirming_title、result.confirming_body、订单卡片（OrderCard）与游客的 result.guest_next，不显示 result.demo_hint 与 pay.expires；每 5 秒自动重查一次、最多 12 次，每次重查是重新读取订单（取得新令牌；已不是待支付就按读到的状态显示）后仍待支付且有 Stripe 记录时再调用 confirmStripe 一次；重查里的重新读取失败（network 或 failed；expired 照判定一条整页 pay.session_expired）与确认失败一样计为一次重查、继续停在确认中画面，自动重查期间不切换为读取中，只在手动再查进行中按钮标 aria-busy；12 次用完仍是确认中时停止自动重查，显示 result.confirming_slow 与 result.check_again 按钮，每点一次重查一次，进行中按钮禁用并标 aria-busy；等待经可注入的 delay（默认 frontend/src/api/pay.ts 的 wait）完成，离开页面或切换语言时中止，中止后的结果不再更新页面；切换语言照现有写法重新读取，自动重查次数从头计
      - 已取消且 stripe_refunded 为 true 时，以 result.stripe_late_refund 代替 result.cancelled 或 result.cancelled_by_you（不论取消方），其下照旧是 result.continue；成功——首次读到订单时以其订单号调用一次 takePaidNotCancelled，为 true 且订单按成功显示时，成功区顶部另显示 result.paid_not_cancelled（只在内存里，刷新后不再显示）；其余成功、失败、已取消与凭据过期的显示不变，Stripe 途径不显示失败页
      - 代码结构照 frontend/src/pages/AdminStoreDesignPage.tsx 与本页现有写法——导出把读取与确认结果变成页面状态的纯函数、接收 AbortSignal 与回调的读取、确认和重查异步函数，以及只依赖 props 的 PayResultView；默认导出的页面只负责持有状态、调用这些函数并接上路由；传给 onClick 的处理函数不返回 Promise（异步调用前加 void）；页面组件与函数的排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch、delay 与路由以替身代替，不用假时钟、不模拟点击）在 frontend/src/pages/PayResultPage.test.tsx 验证意图——确认的四种结果与 failed、csrf、network、expired 各自的显示；确认中不显示 result.demo_hint；自动重查第 12 次后停止并出现 result.check_again、之前不出现、按钮进行中禁用；重查中订单已付款则按成功显示；中止后不再更新；迟到退款代替两种已取消文案；paid_not_cancelled 只显示一次且只在成功时；stripe_state 为 null 的待支付订单仍按原样处理；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 HANDOFF 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出；现有测试只许在 props 辅助函数里补新增属性的默认值，其余断言不改；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-133.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；判定、确认中与重查、迟到退款、取消时已付款、偏离、验证到什么程度，并写明未做浏览器验收、P06 留给 SHOP-TASK-134、135）
      - 改动只落在 allowed_change_paths（不新增路由，不改 frontend/src/pages/PayPage.tsx 与 frontend/src/api/pay.ts）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.55），不合并、不直推
```

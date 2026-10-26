# SHOP-TASK-134 支付页 P06 的 Stripe 显示与跳转

- [x] 按 `docs/UX.md` 0.11「P06 Stripe 测试」、`docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 1、2、3、9、10 条与 `docs/HANDOFF.md` 0.54、0.55 的落实写法，在 `frontend/src/pages/PayPage.tsx` 按服务端给出的支付途径显示 Stripe 测试的标题、提示、测试卡号与各状态，经 SHOP-TASK-132 的 `startStripe` 跳转到服务端返回的付款页地址，并在两种途径上处理「支付途径已改变」。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，支付状态的显示与发起（途径判定与会话创建都在服务端）。不改 `frontend/src/api/pay.ts`、`PayResultPage.tsx`、路由、后端与设计，不装依赖。
- 结构（沿用本页现有写法）：导出纯函数 `isStripeRoute`、`stripeAction`（按 `stripe_state` 决定显示 go / continue / review / 无按钮）、`showsCountdown`（是否显示 `pay.expires`）、`leavesPayPage`（是否换成 P07）与 `stripeMove`（`startStripe` 的结果 → 页面去向）。`PayView` 只依赖 props，新增 `stripeUnavailable`、`methodChanged` 与 `onStripe`；`PayBusy` 新增 `redirecting`。默认导出的页面只持有状态、调用 `startStripe` 与 `window.location.assign` 并接上路由；`onStripe` 的处理函数在异步调用前加 `void`，不返回 Promise。Stripe 的按钮直接写在 `PayView` 里（不另拆子组件），测试才能从元素树取到 `disabled` 与点击处理。
- 途径：
  - `payment_route` 为 simulated 时页面与现有完全相同，不出现任何 Stripe 文字（只多了 `methodChanged` 为真时的 `pay.method_changed`）。
  - 为 stripe 时标题为 `pay.stripe_title`，★ 为 `pay.stripe_demo_hint`；订单卡片、游客 `pay.guest_access`、收货资料与取消按钮同现有；不显示 `pay.choose_method`、三种演示方式、两个模拟按钮与 `pay.action_hint`。◆ `pay.stripe_notice`（现有 `ActionHint` 的菱形标记）与其下的 `pay.stripe_test_card` 在各状态都显示，无勾选框。
- 各状态（`stripeAction`、`showsCountdown`）：`stripe_state` 为 null 显示 `pay.expires` 倒计时与 `pay.stripe_go`；creating 显示 `pay.stripe_go`、不显示倒计时；open 在 ◆ 之上显示 `pay.stripe_in_progress`（信息提示框），按钮为 `pay.stripe_continue`，不显示倒计时；under_review 在按钮的位置显示 `pay.stripe_under_review`（信息提示框），没有跳转按钮，取消按钮照常。倒计时只在显示 `pay.expires` 时计（`useCountdown` 只在此时拿到订单），到 0 照现有写法重新读取。
- complete 交给 P07：读取（打开、重新读取、切换语言）到 `stripe_state` 为 complete 或订单已不是待支付（`leavesPayPage`）时用路由的 replace 换成 `/pay/result`，不显示本页内容；确认接口由 P07（SHOP-TASK-133）调用，本页不调用。
- 跳转（`onStripe` 与 `stripeMove`）：以读取给的 CSRF 令牌调用 `startStripe`；进行中 `busy` 为 `redirecting`，所有按钮禁用，跳转按钮标 `aria-busy`，按钮下显示 `pay.stripe_redirecting`。
  - redirect：`window.location.assign` 在当前标签页打开结果里的地址（只用服务端返回的地址，不开新窗口），按钮保持禁用。
  - complete、closed：换成 `/pay/result`。
  - unavailable：按钮下显示 `pay.stripe_unavailable`（危险提示框），按钮恢复可点。
  - under_review：重新读取，按读到的状态显示。
  - method_changed：显示 `pay.method_changed` 并重新读取，按新的途径显示。
  - expired：整页 `pay.session_expired`。
  - csrf：重新读取取得新令牌（读到 401 整页过期、读到已不是待支付或 complete 换成 P07），显示 `common.error_retry`。
  - failed、network：显示 `common.error_retry` 并重新读取（已建成会话的按读到的 open 显示 `pay.stripe_continue`）。
  - 离开页面时以现有的 `lifetime` 中止请求，中止后的结果不再更新页面。
- 途径已改变：模拟支付（`runPayment`）得到 `method_changed` 时同样显示 `pay.method_changed`、清除网络中断重试的幂等键并重新读取；`pay.method_changed` 在两种途径都显示在操作区顶部。它与 `pay.stripe_unavailable`、`common.error_retry` 在下一次操作（支付、跳转、确认取消）开始时一起收起。
- 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.12 逐字加入 `pay.stripe_title`、`pay.stripe_demo_hint`、`pay.stripe_notice`、`pay.stripe_test_card`、`pay.stripe_go`、`pay.stripe_redirecting`、`pay.stripe_in_progress`、`pay.stripe_under_review`、`pay.stripe_continue`、`pay.stripe_unavailable` 与 `pay.method_changed`（放在 `pay.demo_hint` 之后，与 UX-COPY 表的顺序一致）。没有用到 UX-COPY 以外的界面文字。`pay.cancel_confirm_stripe` 与 `pay.cancel_try_later` 属取消，留给 SHOP-TASK-135。
- 样式（`frontend/src/styles/site.css`，只写布局与尺寸）：Stripe 操作区沿用本页的 `.site-pay__choose`（上分隔与内边距），另加 `.site-pay__stripe`（间距 18px，手机 16px）；`.site-pay__test-card`（桌面图标在左、间距 16px、内边距 18px 20px，手机图标 26px 在上、间距 10px、内边距 14px 16px）；`.site-pay__go`（按钮与其下提示间距 10px，桌面靠左，手机按钮占满宽度）；跳转中加 `.site-pay__go--redirecting`（照 P06-stripe-states 间距 8px）；`.site-pay__in-progress`（照 P06-stripe-phone-open，手机在进行中提示上方另加 8px 外边距）。跳转中的按钮照 P06-stripe-states 不带外链图标，其余状态带。未改生成的 `acuven-shop.css`。
- 偏离（视觉稿与现有写法之间的取舍，未改设计）：
  - 测试卡号框的虚线边框、圆角与 `--surface-alt` 底色 site.css 写不了，未搬（只保留内边距与图标排法），也未借用其他组件的类来取颜色。
  - 视觉稿里测试卡号 `4242 4242 4242 4242` 加粗并加字距、手机版测试卡号为 `acs-body-s` 小字；`pay.stripe_test_card` 是一整条文案，页面不拆开文案，字重与字号也不在 site.css 写，均未搬。
  - P06-stripe-phone-open 里 `pay.guest_access` 是次要文字段落；按 UX 0.11「游客授权说明…同上」沿用现有信息提示框。
  - 视觉稿与 UX 线框都没有给出 `pay.method_changed` 的位置；两种途径都放在操作区顶部（P06-stripe-states 的信息提示框与刷新图标）。
  - `pay.stripe_unavailable` 照 P06-stripe-states 用带图标的危险提示框，不用现有 `ErrorNotice`（后者只给 `common.error_retry`）。
- 按验收标准未写死之处的取法：
  - 重新读取沿用现有的 `reads` 计数（与倒计时到 0 同一条路）；`busy` 随即恢复为空，读取返回之前按钮可点。重新读取失败照现有写法保留已显示的订单并显示 `common.error_retry`。
  - 取消的现有行为不变：取消得到 `method_changed` 或 `unavailable` 时仍照原样显示 `common.error_retry`，交给 SHOP-TASK-135。
  - 跳转后经浏览器的往返缓存（bfcache）回到本页时按钮仍为跳转中；UX 0.11 所说的返回是 Stripe 取消返回地址 `/pay` 的新页面载入，不受影响，未另做处理。
- 测试（`frontend/src/pages/PayPage.test.tsx`，vitest 与 react-dom/server，路由以 `RouterProvider` 的初始路径代替，不模拟点击）：模拟途径各状态没有任何 Stripe 文字；Stripe 途径三语换标题与 ★、隐藏演示方式与两个模拟按钮与 `pay.action_hint`、订单卡片与取消按钮同现有；◆（菱形标记）与测试卡号在各状态都在、没有勾选框；Stripe 线框顺序（未发起与进行中）；四种 `stripe_state` 的按钮、提示与倒计时及 `stripeAction`；`showsCountdown`；`leavesPayPage`（complete 与已不是待支付）；跳转中按钮禁用、标 `aria-busy` 与 `pay.stripe_redirecting`；两个按钮都交给 `onStripe`；`stripeMove` 对 `startStripe` 每种结果的去向；不可用时提示在按钮之后、按钮可点、跳转中不显示；失败后 `common.error_retry` 与可点的 `pay.stripe_continue`；两种途径的 `pay.method_changed`；Stripe 各状态的文字全部来自字典。每条测试的注释写明它守住的 UX、HANDOFF 或验收原句。现有测试只在 props 辅助函数里补了 `stripeUnavailable`、`methodChanged`（默认 false）与 `onStripe`，其余断言未改。页面组件本身（`window.location.assign` 与读取的接线）没有单元测试，由纯函数与视图测试覆盖其判定。
- 改动只在 `frontend/src/pages/PayPage.tsx`、`frontend/src/pages/PayPage.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`frontend/src/router.tsx` 与 `frontend/src/App.tsx` 不需要改动（不新增路由）。未改 `api/pay.ts`、`PayResultPage.tsx`、生成的 `acuven-shop.css`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 留给之后的任务：取消的 Stripe 处理（取消确认框里的 `pay.cancel_confirm_stripe`、取消时 Stripe 不可用的 `pay.cancel_try_later`、取消时会话已付款的 `rememberPaidNotCancelled`）留给 SHOP-TASK-135。会员前端尚不存在，本任务只接游客接口。
- 验证到什么程度：人工逐条对照验收标准、UX 0.11「P06 Stripe 测试」、UX-COPY 0.12、HANDOFF 0.54、0.55 与三张视觉稿（P06-stripe-desktop、P06-stripe-phone-open、P06-stripe-states）自查。未做浏览器验收（没有在真实浏览器或 Stripe 测试环境里走过跳转与返回）。前端的 lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.11 的「P06 Stripe 测试」、docs/UX-COPY.md 0.12 与 docs/HANDOFF.md 0.55 为准，样式以 docs/design/pages/ 的 P06-stripe-desktop、P06-stripe-phone-open 与 P06-stripe-states（取消未成一项除外）为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.12 逐字加入本任务用到的 pay.stripe_title、pay.stripe_demo_hint、pay.stripe_notice、pay.stripe_test_card、pay.stripe_go、pay.stripe_redirecting、pay.stripe_in_progress、pay.stripe_under_review、pay.stripe_continue、pay.stripe_unavailable 与 pay.method_changed，写法与相邻条目相同（每种语言一行）
      - payment_route 为 simulated 时页面与现有完全相同、不出现任何 Stripe 文字；为 stripe 时标题换成 pay.stripe_title，★ 换成 pay.stripe_demo_hint，订单卡片、游客 pay.guest_access、收货资料与取消按钮同现有；不显示 pay.choose_method、三种演示方式、pay.simulate_success、pay.simulate_failure 与 pay.action_hint；◆ pay.stripe_notice 与其下的 pay.stripe_test_card 总是显示（用现有 ActionHint 的菱形标记，无勾选框）
      - 各状态——stripe_state 为 null 显示 pay.expires 倒计时与 pay.stripe_go；creating 显示 pay.stripe_go、不显示倒计时；open 显示 pay.stripe_in_progress 与 pay.stripe_continue、不显示倒计时；under_review 显示 pay.stripe_under_review、没有跳转按钮；读到 complete（或订单已不是待支付）时用路由的 replace 换成 /pay/result，不显示本页内容；倒计时到 0 照现有写法重新读取（只在显示倒计时时）
      - 跳转——点 pay.stripe_go 或 pay.stripe_continue 以读取给的 CSRF 令牌调用 startStripe，进行中所有按钮禁用并显示 pay.stripe_redirecting；redirect 以 window.location.assign 在当前标签页打开结果里的地址（不开新窗口，页面只用服务端返回的地址，按钮保持禁用）；complete 与 closed 换成 /pay/result；unavailable 在按钮下显示 pay.stripe_unavailable、按钮恢复可点；under_review 重新读取（按读到的状态显示）；method_changed 显示 pay.method_changed 并重新读取、按新的途径显示；expired 整页 pay.session_expired；csrf 照现有写法重新读取取得新令牌后显示 common.error_retry；failed 与 network 显示 common.error_retry 并重新读取（已建成会话的按读到的 open 显示 pay.stripe_continue）。模拟途径的支付遇到 SHOP-TASK-132 的 method_changed 时同样显示 pay.method_changed 并重新读取；pay.method_changed 在下一次操作开始时收起
      - 代码结构沿用本页现有写法——把 startStripe 的结果变成页面去向的纯函数与按订单决定显示哪个按钮的纯函数都导出，PayView 只依赖 props，默认导出的页面只负责持有状态、调用请求与 window.location.assign 并接上路由；传给 onClick 的处理函数不返回 Promise（异步调用前加 void）；页面组件与函数的排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替，不模拟点击）在 frontend/src/pages/PayPage.test.tsx 验证意图——模拟途径没有任何 Stripe 文字；Stripe 途径换标题与 ★、隐藏演示方式与两个模拟按钮、◆ 与测试卡号总在；四种 stripe_state 的按钮与倒计时；跳转中按钮禁用与 pay.stripe_redirecting；startStripe 每种结果的去向（直接调用导出的纯函数）；不可用时提示与按钮可点；两种途径的 pay.method_changed；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 HANDOFF 的哪一句；现有测试只许在 props 辅助函数里补新增属性的默认值，其余断言不改；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-134.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；途径与各状态、跳转、途径已改变、complete 交给 P07、偏离、验证到什么程度，并写明未做浏览器验收、取消的 Stripe 处理留给 SHOP-TASK-135）
      - 改动只落在 allowed_change_paths（不新增路由，不改 frontend/src/api/pay.ts 与 frontend/src/pages/PayResultPage.tsx，取消的现有行为不变）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.55），不合并、不直推
```

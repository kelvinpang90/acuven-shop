# SHOP-TASK-142 后台退款详情的 Stripe 退款手动重试 A03

- [x] 按 `docs/UX.md` 0.12 的 A03「Stripe 测试（0.11）」、`docs/UX-COPY.md` 0.13、`docs/HANDOFF.md` 0.57 与 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条，在 SHOP-TASK-141 的 Stripe 退款段上加入手动重试：最新一次尝试为失败时显示 `admin.stripe_refund_retry`，点按经 SHOP-TASK-140 的 `retryStripeRefund` 调用 SHOP-TASK-131 的重试接口。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.57，退款（手动重试的触发与结果显示；能否重试、合计上限与审计在服务端）。样式参照视觉稿 A03-stripe-phone-failed 与 A03-stripe-states。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.13 逐字加入 `admin.stripe_refund_retry`（在 `admin.stripe_refund_failed_hint` 之后）与 `admin.stripe_refund_blocked`（在 `admin.stripe_refund_not_sent` 之后），每种语言一行，与相邻条目相同。
- 按钮条件（导出的 `canRetryStripeRefund`）：只在 `stripeRefundSection` 为尝试表（即已批准、`paid_via_stripe` 为真、`amount_sen` 大于 0、`stripe_refunds` 不为空）且第一条（最新）为 `failed` 时显示，位于 `admin.stripe_refund_failed_hint` 提示框之下。最新为 pending 或 succeeded（含较早一条为 failed）、没有尝试（`admin.stripe_refund_not_sent`）、`amount_sen` 为 0（`admin.stripe_refund_none`）、审核中、已拒绝与模拟支付都不显示；处理中的尝试没有改为失败或取消的按钮。
- 提交（`AdminRefundDetailContent` 的 `onRetry` 与导出的 `submitRetry`）：以详情给的 `csrf_token` 调用 `retryStripeRefund`（不带请求体与幂等键）；进行中按钮 `disabled` 并标 `aria-busy`，另以 ref 挡住重新渲染之前的再次点按，不发第二次请求；点按时收起上一次的提示。重试的 POST 不中止（`retryStripeRefund` 本来就不收 signal，与审核相同），离开页面或换申请（组件的 `lifetime` 中止）之后其结果与重新读取都不再更新页面。换申请时 `AdminRefundsPage.tsx` 以 `key={refundId}` 重新挂载 `AdminRefundDetailContent`（本任务未改该文件），提交中状态、ref 与按钮位置的提示随旧组件一起丢弃，不会带到下一笔申请。重新读取用 `languageRef.current`（打开时的界面语言），与打开详情相同。重试不调用 `onReviewed`，左侧列表不重新查询（`RetryMoves` 没有这一步）。
- 结果（导出的纯函数 `retryStep` 把回答变成下一步：在按钮位置显示提示、进入登录或重新读取）：
  - done：照现有 `reread` 以打开时的界面语言重新读取详情，取到后显示新详情，按钮位置没有提示；多出的一条尝试按 SHOP-TASK-141 的规则显示，提示与按钮按最新一条的当前状态（新尝试可能已是 pending、succeeded 或 failed，页面不假定为处理中；已 failed 时按钮仍显示）。
  - cap（409 `stripe_refund_cap`）：不重新读取、详情不变，按钮下方显示 `admin.stripe_refund_blocked`（`AdminAlert`，按钮仍可点）。
  - not_retryable（409 `stripe_refund_not_retryable` 与 `refund_not_approved`）与 csrf（403）：重新读取后显示 `common.error_retry`。
  - none（401，含重新读取时的 401）：用路由的 replace 进入 `/admin/login`。
  - network：`common.network_check`；failed（其他，含别的 409、404 与 422）：`common.error_retry`；都不重新读取。
  - 这些提示都显示在 Stripe 退款段的末尾：有按钮时在按钮之下，重新读取后按钮不再显示时仍在同一位置（处理中提示、`admin.stripe_refund_not_sent` 或 `admin.stripe_refund_none` 之后）。再次点按时收起；重新读取成功时换成这一步给的提示（done 为无）。
- 代码结构沿用 `reviewStep` 与 `submitReview`：导出 `retryStep`（纯函数）、`submitRetry`（接收 AbortSignal 与回调 `RetryMoves`：`show`、`replace`、`retried`）与 `canRetryStripeRefund`；`AdminRefundDetailView` 只依赖 props，新增 `retrying`、`retryNotice` 与 `onRetry`。按钮的 onClick 不返回 Promise（页面里以 `void submitRetry(...)` 调用）。审核、幂等键与读取的现有逻辑未改。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - UX 线框写「成功后重新读取，表中多出一行处理中的尝试」；按 HANDOFF 0.57 第（3）项与本任务验收，新的一行照读到的状态显示，不一定是处理中。
  - 重试提示的状态放在页面组件自己的 state 里（`retrying`、`retryNotice`），不放进 `DetailState` 的 `notice`：那个字段是审核的提示，只在审核中显示，合用会改变审核后提示的显示。重新读取取到新详情时 `DetailState` 的 `notice` 置为 null（已批准的申请本来不显示它）。
  - 200 之后的重新读取遇到网络中断或其他失败时，照 `reread` 保留原来的详情，其 `common.network_check` 或 `common.error_retry` 改在按钮的位置显示；申请已不存在时显示不存在（验收标准没有写这几种，照审核后的重新读取处理）。
  - 视觉稿 A03-stripe-states（桌面）的按钮靠左、不占满宽度，提示框在按钮之下、间距 10px（沿用 Stripe 退款段已有的 10px 间距）；A03-stripe-phone-failed（手机）的按钮占满宽度、文字居中、最小高度 44px，写在 `site.css` 767px 及以下的媒体查询里（新类 `site-admin-refund__stripe-retry`，按钮本身沿用主按钮 `acs-admin__btn`）。
  - 视觉稿里提示框（`admin.stripe_refund_blocked` 等）的字号 13px site.css 写不了，没有搬。
  - 提交中的按钮文字不变（UX-COPY 没有「提交中」的文案）。
- 测试（`frontend/src/pages/AdminRefundDetail.test.tsx`，vitest 与 react-dom/server，以 `renderToStaticMarkup` 渲染 View、直接调用导出的函数，不模拟点击；新增 `retrying a failed Stripe refund` 一组，每条注释写明守住的 UX、HANDOFF 0.57 或 SHOP-TASK-131 记录的原句）：
  - 最新为 failed 时失败提示框之下有一个按钮（类、type、未禁用、`aria-busy="false"`）；较早一条 failed 而最新为 pending、最新为 succeeded、没有尝试、现金退款额为 0、审核中、已拒绝与模拟支付都没有按钮（并直接调用 `canRetryStripeRefund`）。
  - 提交中按钮禁用、`aria-busy="true"`。
  - done：POST `/api/admin/refunds/9/stripe-retry` 带详情的 CSRF 令牌、不带请求体、幂等键与 signal，地址里没有令牌，之后 GET 详情并显示；新尝试为 pending、succeeded、failed 三种情形下表格多出一行、首行照新状态显示，按钮只在新尝试为 failed 时仍在。
  - cap：只有一次请求、详情不变，`admin.stripe_refund_blocked` 在按钮之下（三种语言）。
  - not_retryable（两种错误码）与 csrf：重新读取后显示 `common.error_retry`，按钮已不显示时提示仍在段末；没有尝试或现金退款额为 0 的段同样在段末。
  - 重试与重新读取的 401 进入 `/admin/login`；网络中断与其他失败（500、404、422、别的 409）只显示提示；200 后重新读取失败的三种情形；signal 中止之后不再更新；`retryStep` 的各个回答。
  - 按钮、提交中、各种提示与重新读取后的详情，页面文字全部来自字典。
  - fetch 替身沿用该文件现有的 `stubFetch`（参数带类型的 `vi.fn((input, init) => …)`，不从 `mock.calls` 取值）。
  - 现有测试的修改：`render` 补上 View 新增的三个必需属性的默认值（没有在重试、没有提示）；`shows the hint for the latest attempt` 原先断言段内没有按钮、提示为段内最后一段文字，最新为 failed 时现在有重试按钮，改为断言此时只有这一个按钮且在提示之后，其余情形不变（注释写明原因）。其余现有测试未改。
- 页面组件里的点按处理（`onRetry`：ref 挡住重复点按、点按时收起提示、提交中状态的设置与结束）没有测试覆盖：测试不模拟点击，只验证导出的函数与 View。
- 改动只在 `frontend/src/pages/AdminRefundDetail.tsx`、`frontend/src/pages/AdminRefundDetail.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；未改 `router.tsx`、`App.tsx`（不新增路由、不改导航）、`frontend/src/api/adminRefunds.ts`、生成的 `acuven-shop.css`、`docs/TODO.md`、`.platform/`、CI、部署配置与后端，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX A03「Stripe 测试（0.11）」原句、UX-COPY 0.13 的文案、HANDOFF 0.57 第（2）至（4）项、SHOP-TASK-131 记录与两张视觉稿自查。上面列出的测试由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建），Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（桌面与手机的实际排版与点按未在浏览器里看过）。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.12 的 A03「Stripe 测试（0.11）」、docs/UX-COPY.md 0.13 与 docs/HANDOFF.md 0.57 为准，样式以 docs/design/pages/ 的 A03-stripe-phone-failed 与 A03-stripe-states 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.13 逐字加入 admin.stripe_refund_retry 与 admin.stripe_refund_blocked，写法与相邻条目相同（每种语言一行）
      - 按钮——admin.stripe_refund_retry 只在已批准、paid_via_stripe 为真、stripe_refunds 不为空且第一条（最新）为 failed 时显示，位于 admin.stripe_refund_failed_hint 之下；最新为 pending 或 succeeded、没有尝试（admin.stripe_refund_not_sent）、amount_sen 为 0（admin.stripe_refund_none）、审核中、已拒绝与模拟支付都不显示；处理中的尝试不提供改为失败或取消的按钮
      - 提交——点按以详情给的 csrf_token 调用 SHOP-TASK-140 的重试请求函数；进行中按钮禁用并标 aria-busy，再点不发第二次请求；重试的 POST 不中止（与审核相同），离开页面或换申请之后其结果与重新读取都不再更新页面；重试不让左侧列表重新查询（申请状态不变）
      - 结果——done 照现有 reread 重新读取详情（取到后显示新详情，多出的一条尝试按 SHOP-TASK-141 的显示规则显示；接口在回 200 之前已调用 Stripe（SHOP-TASK-131 记录），新尝试可能已是 pending、succeeded 或 failed，页面不假定它为处理中）；cap 保留当前详情，在按钮下方显示 admin.stripe_refund_blocked；not_retryable 与 csrf 重新读取后显示 common.error_retry；none 用路由的 replace 进入 /admin/login；network 显示 common.network_check；failed 显示 common.error_retry；这些提示显示在 Stripe 退款段里按钮所在的位置，重新读取后按钮不再显示时仍显示；再次点按或重新读取成功时收起
      - 代码结构沿用 frontend/src/pages/AdminRefundDetail.tsx 的 reviewStep、submitReview 写法——导出把重试结果变成下一步（显示、进入登录或重新读取）的纯函数与接收 AbortSignal 与回调的异步函数，View 组件只依赖 props（新增按钮的回调与提交中状态）；不改审核与幂等键的现有逻辑；传给 onClick 等事件属性的处理函数不返回 Promise（异步调用前加 void）；页面组件与函数的排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改（copy.ts 的写法照上一条）
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/AdminRefundDetail.test.tsx 验证意图，写法照该文件现有测试（以 renderToStaticMarkup 渲染 View、直接调用导出的纯函数与异步函数，不模拟点击）——按钮只在最新为 failed 时出现（含较早一条 failed 而最新为 pending 时不出现）；提交中按钮禁用与 aria-busy；done 后重新读取并显示多出的一行（覆盖新尝试为 pending 的情形，以及新尝试已为 failed 时按钮仍按最新一条显示）；cap 显示 admin.stripe_refund_blocked 且详情不变；not_retryable 与 csrf 重新读取后的 common.error_retry（按钮已不显示时提示仍在）；none 进入 /admin/login；network 与 failed；中止后不再更新；重试请求带详情的 csrf_token；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 SHOP-TASK-131 记录的哪一句；现有测试只在必要时修改并写明原因；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出（CI 的 eslint 带类型规则）；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-142.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；按钮条件、提交、结果、偏离、验证到什么程度，只写实际存在的测试，并写明未做浏览器验收）；改动只落在 allowed_change_paths（不新增路由，不改导航，不改 frontend/src/api/adminRefunds.ts）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.57），不合并、不直推
```

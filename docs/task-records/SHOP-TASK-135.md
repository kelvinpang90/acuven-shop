# SHOP-TASK-135 支付页 P06 取消时的 Stripe 处理

- [x] 按 `docs/UX.md` 0.11「P06 Stripe 测试」（取消确认框的补句、取消时 Stripe 暂不可用、取消时会话已付款）、`docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 5 条与 `docs/HANDOFF.md` 0.55 第（5）项的落实写法，在 SHOP-TASK-134 的 P06（`frontend/src/pages/PayPage.tsx`）上接上取消的 Stripe 处理；会话已付款时经 SHOP-TASK-132 的内存交接（`rememberPaidNotCancelled`）让 SHOP-TASK-133 的 P07 显示 `result.paid_not_cancelled`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，取消订单的显示（取消规则在服务端）。不改 `frontend/src/api/pay.ts`、`PayResultPage.tsx`、路由、后端与设计，不装依赖。
- 结构（沿用本页现有写法）：导出纯函数 `confirmsStripeCancel`（确认框是否带补句）与 `cancelMove`（`runCancel` 的结果 → 页面去向 `CancelMove`），另导出 `leaveAfterCancel`（转结果页：需要交接时先以订单号调用 `rememberPaidNotCancelled`，再以传入的路由 `replace` 换成 `/pay/result`）。`PayView` 只依赖 props，新增 `cancelTryLater`。默认导出的页面以 `settleCancel` 按 `cancelMove` 更新状态；原来支付与取消共用的 `settle` 只留给模拟支付（去掉 `action` 参数，支付的各分支行为不变）。`onConfirmCancel` 的处理函数在异步调用前加 `void`，不返回 Promise。
- 确认框补句：订单为 Stripe 途径且 `stripe_state` 为 open 时，`pay.cancel_confirm` 之下另一段 `pay.cancel_confirm_stripe`（照 P06-stripe-cancel-confirm 用 `acs-body-s acs-muted`），并以 `aria-describedby` 作为对话框的说明；模拟途径、未发起、creating、under_review 的确认框与现有逐字相同（没有补句，也没有 `aria-describedby`）。两段之间的间距来自 `acs-dialog` 自身的 gap。
- 取消未成：`runCancel` 得到 `unavailable`（503 `stripe_unavailable`）时确认框已在确认时关闭，取消区块里在取消按钮之上显示 `pay.cancel_try_later`（照 P06-stripe-states 用带警示图标的危险提示框，`role="alert"`），订单照常显示、保持待支付，`busy` 恢复为空、取消按钮可点；下一次操作（支付、跳转、确认取消）开始时与其他提示一起收起，操作进行中不显示。
- 取消时会话已付款的交接：取消结果为转到结果页（`result`，含 409 `order_not_cancellable`，以及 csrf、网络中断后重新读取到订单已不是待支付）时，Stripe 途径的订单先以该订单号调用 `rememberPaidNotCancelled`，再用路由的 replace 换成 `/pay/result`；P07 读到订单后自行以 `takePaidNotCancelled` 取出，只在按成功显示时显示 `result.paid_not_cancelled`（订单已被超时取消等其他结果不显示，由 P07 决定）。模拟途径的取消转结果页与现有相同，不调用 remember。途径以发起取消时页面上的订单为准。
- 其余结果照现有写法：expired 整页 `pay.session_expired`；csrf（重新读取取得新令牌）、failed、network（确认仍待支付）显示 `common.error_retry` 并显示重新读取到的订单；取消得到 `method_changed` 仍照原样显示 `common.error_retry`（验收没有给取消的 `method_changed` 另定写法）。
- 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.12 逐字加入 `pay.cancel_confirm_stripe` 与 `pay.cancel_try_later`（放在 `pay.method_changed` 之后，与 UX-COPY 表的顺序一致，每种语言一行）。没有用到 UX-COPY 以外的界面文字。
- 样式（`frontend/src/styles/site.css`，只写布局）：取消未成时取消区块加 `.site-pay__cancel--try-later`（照 P06-stripe-states 为 grid，提示与按钮间距 10px、靠左；上分隔与 20px 上内边距沿用 `.site-pay__cancel`）；手机在 767px 及以下的媒体查询里改为 `justify-items: stretch`，提示与现有占满宽度的取消按钮同宽。确认框补句不需要新样式。未改生成的 `acuven-shop.css`。
- 偏离（视觉稿与现有写法之间的取舍，未改设计）：
  - P06-stripe-states 的取消未成只有桌面一项，没有手机画板；手机上提示占满宽度是比照 SHOP-TASK-134 的跳转按钮下提示与现有占满宽度的取消按钮取的。
  - 视觉稿的确认框补句以行内样式去掉外边距；本页由现有的 `.site-pay p { margin: 0 }` 覆盖，未另写。没有 site.css 写不了的颜色、字号、字重或底色需要搬。
- 按验收标准未写死之处的取法：
  - 取消得到 `unavailable` 时不论页面上的途径都显示 `pay.cancel_try_later`（服务端只在本单有 Stripe 记录时回 503 `stripe_unavailable`，以服务端的回答为准）；模拟途径正常情况下不会得到这一结果。
  - `pay.cancel_try_later` 与 `pay.stripe_unavailable` 一样只在 `busy` 为空时显示。
- 测试（`frontend/src/pages/PayPage.test.tsx`，vitest 与 react-dom/server，路由以替身代替，不模拟点击）：三语 open 时补句紧接 `pay.cancel_confirm`、在按钮之前并作为 `aria-describedby`；模拟、未发起、creating、under_review 的确认框与模拟途径的逐字相同且没有补句，及 `confirmsStripeCancel`；三语四种 `stripe_state` 下取消未成时提示在取消按钮之上、订单号与金额仍显示、没有确认框、取消按钮可点；进行中与未设时不显示、取消区块与现有相同；`cancelMove` 对 `runCancel` 每种结果在两种途径的去向；直接调用 `cancelMove` 与 `leaveAfterCancel`，以 `replace` 替身在被调用时用 `takePaidNotCancelled` 核对 Stripe 途径已记下交接、模拟途径没有；补句与取消未成各状态的文字全部来自字典。每条测试的注释写明它守住的 UX、DESIGN、HANDOFF 或验收原句。SHOP-TASK-134 与更早的测试只在 props 辅助函数里补了 `cancelTryLater`（默认 false），其余断言未改（另调整了导入）。页面组件本身（`settleCancel` 的接线）没有单元测试，由纯函数与视图测试覆盖其判定。
- 改动只在 `frontend/src/pages/PayPage.tsx`、`frontend/src/pages/PayPage.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；`frontend/src/router.tsx` 与 `frontend/src/App.tsx` 不需要改动（不新增路由）。未改 `api/pay.ts`、`PayResultPage.tsx`、生成的 `acuven-shop.css`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX 0.11「P06 Stripe 测试」、UX-COPY 0.12、DESIGN 1.12 第 5 条、HANDOFF 0.55 与两张视觉稿（P06-stripe-cancel-confirm、P06-stripe-states 的取消未成）自查。未做浏览器验收（没有在真实浏览器或 Stripe 测试环境里走过取消与转到 P07）。前端的 lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.11 的「P06 Stripe 测试」、docs/UX-COPY.md 0.12 与 docs/HANDOFF.md 0.55 为准，样式以 docs/design/pages/ 的 P06-stripe-cancel-confirm 与 P06-stripe-states 的取消未成一项为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.12 逐字加入 pay.cancel_confirm_stripe 与 pay.cancel_try_later，写法与相邻条目相同（每种语言一行）
      - 取消确认框——订单为 Stripe 途径且 stripe_state 为 open 时，pay.cancel_confirm 之下另显示 pay.cancel_confirm_stripe；其他情形（模拟途径、未发起、creating、under_review）确认框与现有相同
      - 取消的结果——unavailable（SHOP-TASK-132 的 503 stripe_unavailable）时确认框已关闭、在取消按钮旁显示 pay.cancel_try_later、订单保持待支付、按钮恢复可点，下一次操作开始时收起；Stripe 途径的订单取消结果为转到结果页（现有 result，含 409 order_not_cancellable）时，先以该订单号调用 rememberPaidNotCancelled 再用路由的 replace 换成 /pay/result（P07 只在按成功显示时才显示 result.paid_not_cancelled，订单已被超时取消等其他结果不显示）；模拟途径的取消与现有完全相同、不调用 remember；其余结果（expired、csrf、failed、network）照现有写法
      - 代码结构沿用本页现有写法——把取消结果变成页面去向的纯函数导出，PayView 只依赖 props；传给 onClick 的处理函数不返回 Promise；页面组件与函数的排版不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替，不模拟点击）在 frontend/src/pages/PayPage.test.tsx 验证意图——确认框只在 open 时有补句；不可用时 pay.cancel_try_later 与订单仍显示；Stripe 途径取消转结果页前记下交接、模拟途径不记（直接调用导出的纯函数，并以 takePaidNotCancelled 核对）；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 HANDOFF 的哪一句；SHOP-TASK-134 与更早的测试只许在 props 辅助函数里补新增属性的默认值，其余断言不改；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-135.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；确认框补句、取消未成、取消时已付款的交接、偏离、验证到什么程度，并写明未做浏览器验收）
      - 改动只落在 allowed_change_paths（不新增路由，不改 frontend/src/api/pay.ts 与 frontend/src/pages/PayResultPage.tsx）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.55），不合并、不直推
```

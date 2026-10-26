# SHOP-TASK-141 后台退款详情显示 Stripe 付款与退款尝试 A03

- [x] 按 `docs/UX.md` 0.12 的 A03「Stripe 测试（0.11）」、`docs/UX-COPY.md` 0.13 与 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8、10 条，在 `frontend/src/pages/AdminRefundDetail.tsx` 的申请详情上显示 SHOP-TASK-140 取到的三个 Stripe 字段：付款方式、按付款方式的退款期限提示、审核中的 ◆ 提示，以及已批准申请的 Stripe 退款尝试表与相应提示。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.57。样式参照视觉稿 A03-stripe-review、A03-stripe-approved、A03-stripe-phone-failed 与 A03-stripe-states。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.13 逐字加入 `admin.paid_via_stripe`、`admin.stripe_method`、`admin.stripe_method_unknown`、`admin.stripe_deadline_fpx`、`admin.stripe_deadline_grabpay`、`admin.stripe_deadline_other`、`admin.stripe_refund_hint`、`admin.stripe_refund_title`、`admin.col_stripe_refund_id`、`admin.stripe_refund_pending`、`admin.stripe_refund_succeeded`、`admin.stripe_refund_failed`、`admin.stripe_refund_pending_hint`、`admin.stripe_refund_failed_hint`、`admin.stripe_refund_none` 与 `admin.stripe_refund_not_sent`（每种语言一行）。没有加入 `admin.stripe_refund_retry` 与 `admin.stripe_refund_blocked`。
- 显示条件（全部由 `paid_via_stripe`、`payment_method_type`、`stripe_refunds`、`status` 与 `amount_sen` 决定，页面只显示、不计算、不自动刷新）：
  - 付款方式区块：只在 `paid_via_stripe` 为真时显示，位于金额区块之后、理由输入框或审核记录之前，审核中、已批准、已拒绝都显示。依次为 `admin.paid_via_stripe`、`admin.stripe_method`（`{type}` 原样）或类型为 null 时的 `admin.stripe_method_unknown`；审核中与已批准另有期限提示（导出的 `stripeDeadline`：fpx、grabpay 各一条，其他类型或 null 为 `admin.stripe_deadline_other`），已拒绝不显示。
  - 审核中：`admin.refund_hint` 之后以同样的 ◆ 标记（同一个 `site-admin-refund__hint` 类）显示 `admin.stripe_refund_hint`；手机由原有的 `order: -1` 把两条提示一起排在按钮之前，顺序与 UX 手机线框相同。
  - Stripe 退款段（导出的 `stripeRefundSection`）：只在已批准且 `paid_via_stripe` 为真时，于内容面板（含审核记录）之后显示标题 `admin.stripe_refund_title`；`amount_sen` 为 0 时其下只有 `admin.stripe_refund_none`；大于 0 而 `stripe_refunds` 为空时只有 `admin.stripe_refund_not_sent`（UX 0.12，Kelvin 2026-10-23 决定）；否则为尝试表，列头 `admin.col_time`、`admin.col_amount`、`admin.filter_status`、`admin.col_stripe_refund_id`，每次尝试一行、按接口给的顺序（不重新排序），时间照 `formatDateTime` 按界面语言，金额照 `formatSen`（经 `usePrice`，即 `common.price_myr`，与 A03 列表相同、对应视觉稿的「RM 49.00」），状态经导出的 `STRIPE_REFUND_LABEL`，没有 Stripe 退款 ID 时为次要文字 —（照 `AdminOrdersPage.tsx` 的写法）。表下按第一条（最新）尝试的状态显示提示（导出的 `stripeRefundHint`）：pending 为 `admin.stripe_refund_pending_hint`（`role="status"`），failed 为 `admin.stripe_refund_failed_hint`（照视觉稿用 `AdminAlert`，即 `acs-admin__alert`），succeeded 不显示。审核中与已拒绝不显示这一段。
  - 手机：尝试表不显示，每次尝试一张卡片（照 UX 依次为时间、金额、状态标签，再一行 `admin.col_stripe_refund_id` 与 ID 或 —）；外层面板本来就是 `display: contents`，Stripe 退款段因此与内容面板并列，与视觉稿 A03-stripe-phone-failed 相同。显隐与间距写在 `site.css` 767px 及以下的媒体查询里。
  - `paid_via_stripe` 为假（经模拟支付）时不渲染上面任何元素，页面与之前相同。
- `admin.stripe_refund_failed_hint` 里提到的重试按钮由 SHOP-TASK-142 加入；本任务照原文显示这条提示，页面上没有重试按钮，也没有 `admin.stripe_refund_blocked`。
- 读取、审核与幂等键的逻辑未改；`frontend/src/api/adminRefunds.ts`、`router.tsx` 与 `App.tsx` 未改（不新增路由、不改导航）。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - UX 与视觉稿冲突，以 UX 为准：视觉稿 A03-stripe-approved 与 A03-stripe-phone-failed 把付款方式区块放在审核记录之后，UX 线框与本任务验收标准放在金额区块之后，页面照 UX 放在金额之后（已批准、已拒绝时在审核记录之前）。
  - UX 与视觉稿冲突，以 UX 为准：视觉稿 A03-stripe-phone-failed 的手机卡片把状态标签放在首行右侧（与时间同一行），其下为金额；UX 手机线框「<时间> [M4] <金额> [状态] [admin.col_stripe_refund_id] <ID>」与本任务验收标准为时间、金额、状态，再一行 ID，页面照 UX 依次排列。
  - 已退（succeeded）的状态标签视觉稿没有给出，沿用 A03 已批准申请所用的成功标签 `acs-tag--success`；处理中与失败照视觉稿用 `acs-tag--neutral` 与 `acs-tag--danger`。这三个都是标签组件本身的类，没有借用其他组件的类来取颜色。
  - `admin.stripe_refund_none`、`admin.stripe_refund_not_sent` 与 `admin.stripe_refund_pending_hint` 用自己的类 `site-admin-refund__stripe-note`（只有内边距 10px 12px，`role="status"`）。视觉稿里它们的底色（`--a-select`）、字号 13px 与圆角 site.css 写不了，没有搬；`admin.stripe_refund_not_sent` 没有视觉稿，与 `admin.stripe_refund_none` 用同样的写法。
  - 其他 site.css 写不了、没有搬的样式：`admin.paid_via_stripe` 的字重 600；期限提示与失败提示框的字号 13px；Stripe 退款 ID 的等宽字体与 12px 字号；标题 h3 的 15px 字号；付款方式区块与 Stripe 退款段的上边框颜色（与已有区块相同，只保留 12px 上内边距）。
  - 视觉稿把 Stripe 退款 ID 显示成省略的 `re_3Qx…T8kP`；UX 与验收标准没有要求截断，页面显示接口给的完整 ID。
  - 手机视觉稿的标题为 h2，页面在桌面与手机都用 h3（位于详情 h2 之下）。
- 测试（`frontend/src/pages/AdminRefundDetail.test.tsx`，vitest 与 react-dom/server，写法照该文件现有测试；新增 `paid with Stripe test mode` 一组，每条注释写明守住的 UX 原句或 Kelvin 决定）：
  - 三种状态的付款方式区块在金额之后、理由输入框或审核记录之前，已拒绝没有期限提示；类型为 null 时为 `admin.stripe_method_unknown` 与 `admin.stripe_deadline_other`。
  - 期限提示按 fpx、grabpay、card 与 null 选择（直接调用 `stripeDeadline` 并渲染审核中与已批准），已拒绝不显示任何一条。
  - 审核中 `admin.refund_hint` 之后的 ◆ `admin.stripe_refund_hint`（图标不读出），已批准与已拒绝没有这条。
  - 已批准的尝试表：标题在审核记录之后，列头，两条尝试的顺序、时间、金额、状态文案与 —，状态标签的类，金额列靠右；手机卡片的内容与顺序（时间、金额、状态，再一行 ID）。
  - 最新为 pending、failed、succeeded 时的提示（以及较早的为 succeeded、最新为 failed 的情形），并直接调用 `stripeRefundHint`；段内没有按钮。
  - `amount_sen` 为 0 时只有 `admin.stripe_refund_none`、大于 0 而没有尝试时只有 `admin.stripe_refund_not_sent`，都没有表格、卡片与按钮；`stripeRefundSection` 的各种结果。
  - 审核中与已拒绝没有 Stripe 退款段；模拟支付的三种状态（含会员订单）没有任何 Stripe 文字与 Stripe 区块。
  - Stripe 付款各状态的页面文字全部来自字典（照 `visibleTexts`、`textNodes` 与 `unescapeHtml`，— 照 `AdminOrdersPage.test.tsx` 单独加入）。
  - 现有测试未改，只在文件里加了 `AdminRefundStripeRefund` 类型与四个新导出的引入及 Stripe 样例。
- 改动只在 `frontend/src/pages/AdminRefundDetail.tsx`、`frontend/src/pages/AdminRefundDetail.test.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；未改 `router.tsx`、`App.tsx`、`frontend/src/api/adminRefunds.ts`、生成的 `acuven-shop.css`、`docs/TODO.md`、`.platform/`、CI、部署配置与后端，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX A03「Stripe 测试（0.11）」原句、UX-COPY 0.13 的文案与四张视觉稿自查。上面列出的测试由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建），Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（桌面与手机的实际排版未在浏览器里看过）。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.12 的 A03「Stripe 测试（0.11）」、docs/UX-COPY.md 0.13 与 docs/HANDOFF.md 0.57 为准，样式以 docs/design/pages/ 的 A03-stripe-review、A03-stripe-approved、A03-stripe-phone-failed 与 A03-stripe-states 为准，二者冲突时以 UX 文档为准并在 PR 里说明；视觉稿里的重试按钮与 admin.stripe_refund_blocked 留给 SHOP-TASK-142，本任务不做；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - frontend/src/i18n/copy.ts 按 UX-COPY 0.13 逐字加入 admin.paid_via_stripe、admin.stripe_method、admin.stripe_method_unknown、admin.stripe_deadline_fpx、admin.stripe_deadline_grabpay、admin.stripe_deadline_other、admin.stripe_refund_hint、admin.stripe_refund_title、admin.col_stripe_refund_id、admin.stripe_refund_pending、admin.stripe_refund_succeeded、admin.stripe_refund_failed、admin.stripe_refund_pending_hint、admin.stripe_refund_failed_hint、admin.stripe_refund_none 与 admin.stripe_refund_not_sent，写法与相邻条目相同（每种语言一行）；admin.stripe_refund_failed_hint 里提到的重试按钮由 SHOP-TASK-142 加入，本任务照原文显示并记入任务记录
      - 付款方式——只在 paid_via_stripe 为真时，于金额区块之后显示 admin.paid_via_stripe 与付款方式一行（payment_method_type 不为 null 时为 admin.stripe_method，{type} 原样显示；为 null 时为 admin.stripe_method_unknown），审核中、已批准、已拒绝都显示；期限提示只在审核中与已批准显示，类型为 fpx 用 admin.stripe_deadline_fpx、grabpay 用 admin.stripe_deadline_grabpay、其他类型或 null 用 admin.stripe_deadline_other，只给提示、不计算日期；审核中另在 admin.refund_hint 之后以同样的 ◆ 标记显示 admin.stripe_refund_hint；paid_via_stripe 为假（经模拟支付）时页面与现在完全相同
      - Stripe 退款段——只在已批准且 paid_via_stripe 为真时，于审核记录（admin.reviewed_by）之后显示标题 admin.stripe_refund_title；amount_sen 为 0 时其下只显示 admin.stripe_refund_none；amount_sen 大于 0 而 stripe_refunds 为空时只显示 admin.stripe_refund_not_sent（UX 0.12，Kelvin 2026-10-23 决定）；否则显示尝试表，列头为 admin.col_time、admin.col_amount、admin.filter_status 与 admin.col_stripe_refund_id，每次尝试一行、按接口给的顺序（最新在上），时间照 formatDateTime 按界面语言、金额照 formatSen，状态 pending、succeeded、failed 分别为 admin.stripe_refund_pending、admin.stripe_refund_succeeded、admin.stripe_refund_failed，没有 Stripe 退款 ID 时显示 —（照 frontend/src/pages/AdminOrdersPage.tsx 待审数为 0 的写法）；表下按第一条（最新）尝试的状态显示 admin.stripe_refund_pending_hint（pending）或 admin.stripe_refund_failed_hint（failed），succeeded 不显示提示；手机每次尝试为一张卡片（时间、金额、状态，再一行 admin.col_stripe_refund_id 与 ID），差异写在 site.css 的媒体查询里；已拒绝与审核中不显示这一段；页面不自动刷新
      - 代码结构沿用 frontend/src/pages/AdminRefundDetail.tsx 的现有写法——导出按付款方式类型选期限提示、按尝试状态选文案、按申请决定 Stripe 退款段显示什么的纯函数，组件只依赖 props；不改读取、审核与幂等键的现有逻辑；传给事件属性的处理函数不返回 Promise；页面组件与函数的排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改（copy.ts 的写法照上一条）
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/AdminRefundDetail.test.tsx 验证意图，写法照该文件现有测试（以 renderToStaticMarkup 渲染 View、直接调用导出的纯函数，不模拟点击）——三种状态下付款方式一行（含类型为 null）、期限提示按 fpx、grabpay、card 与 null 选择且已拒绝不显示、审核中的 ◆ 提示；已批准的尝试表（两条尝试的顺序、时间、金额、状态文案与 —）、最新为 pending、failed、succeeded 时的提示；amount_sen 为 0 时只有 admin.stripe_refund_none、amount_sen 大于 0 而没有尝试时只有 admin.stripe_refund_not_sent；模拟支付的三种状态没有任何 Stripe 文字；页面文字全部来自字典（照该文件的 visibleTexts、textNodes 与 unescapeHtml，— 照 AdminOrdersPage.test.tsx 的写法单独处理）；每条测试写明它守住的是 UX、设计或 Kelvin 决定的哪一句；现有测试只在必要时修改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-141.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；显示条件、偏离、验证到什么程度，只写实际存在的测试，写明未做浏览器验收，并写明重试按钮留给 SHOP-TASK-142）；改动只落在 allowed_change_paths（不新增路由，不改导航，不改 frontend/src/api/adminRefunds.ts）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.57），不合并、不直推
```

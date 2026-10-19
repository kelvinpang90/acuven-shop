# SHOP-TASK-058 A02 待审退款数链接到该单的退款申请

- [x] 按 `docs/UX.md` 0.10 的 A02「状态与补充（0.9）」（「桌面表格里待审数大于 0 时 `[admin.refunds_pending]` 是链接，进入只含该单申请的 A03；为 0 时显示 —。手机卡片整张进入订单详情，待审数只作文字，为 0 时不显示。」）与 `docs/design/pages/` 的 A02-desktop-list，把 A02 桌面表格里大于 0 的待审退款数改为站内链接，指向 SHOP-TASK-056 的 `/admin/refunds/order/<订单内部 ID>`。改动：`frontend/src/pages/AdminOrdersPage.tsx`、`pages/AdminOrdersPage.test.tsx`、`styles/site.css`。沿用 `router.tsx` 已有的 `adminRefundsOrderPath`，未新增路由，未改 `router.tsx`、`App.tsx` 与 `i18n/copy.ts`；未新增文案；未装依赖（未改 `package.json`、锁文件），未改后端、CI、部署配置、`.platform/`、设计或生成的 `styles/acuven-shop.css`。设计闸门：不适用（只加一个站内链接）。拆分：不拆。
- 页面：`OrderRow` 的待审数单元格在 `refunds_pending` 大于 0 时为 `Link`（class `site-admin-orders__refunds`），文字仍为 `admin.refunds_pending`；为 0 时仍为 `acs-admin__muted` 的 —。网址只有订单内部 ID，订单号不进网址。手机卡片（`OrderCard`）未改：整张卡片一个链接进入订单详情，待审数是链接里的文字，为 0 时不显示，卡片里不嵌套链接。
- 样式：`site.css` 新增 `.site-admin-orders__refunds { text-decoration: underline; text-underline-offset: 2px; }`（照 A02-desktop-list）；颜色沿用已有的 `.site-admin-orders__table a { color: inherit; }`，未声明颜色、字体与圆角。
- 偏离与取舍，请审阅（评审不以此要求修改）：A02-desktop-list 给链接写了 `color: var(--a-ink)`，`site.css` 不写颜色值与变量，链接改为随单元格的文字颜色（`inherit`），未借用其他组件的类取色。
- 已有测试的改动（只按需要，注释写明原因）：`pages/AdminOrdersPage.test.tsx` 的「shows each order in a row」由「订单号之外的单元格没有链接」改为日期、状态与合计单元格没有链接，待审数单元格大于 0 时一个链接、为 0 时没有；「links each order number to its detail by internal ID」由取表格里全部链接改为只取每行订单号单元格里的链接；「keeps the pending refund count as text」（SHOP-TASK-055 守住「待审退款数仍为文字」）改为「links the pending refund count while an order is open」，断言选中订单时待审数同样链接到 `/admin/refunds/order/41`。其余断言未动。
- 新增测试：「links the pending refund count to the order's refund requests」（三种语言）——大于 0 时单元格里只有一个链接，指向 `/admin/refunds/order/<订单内部 ID>`，文字为 `admin.refunds_pending`，网址里没有订单号；为 0 时是不带链接的 —。「keeps the pending refund count as text inside the card link」——每张卡片只有一个指向订单详情的链接、没有嵌套链接、卡片里没有 `/admin/refunds` 网址，待审数是普通文字。注释写明守住的是 UX A02「状态与补充（0.9）」的上述原句。
- 验证到什么程度：人工逐条对照验收标准、UX A02「状态与补充（0.9）」原句与 A02-desktop-list 的链接样式自查；测试用 vitest 与 `react-dom/server` 的服务端渲染断言链接目标与结构。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。真实点击后的导航与 A03 按订单筛选的实际显示未在 DOM 中执行。未做浏览器验收（未在真实浏览器或手机上打开页面，未连真实后端点击链接进入 A03，也未与视觉稿对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 以 docs/UX.md 0.10 的 A02「状态与补充（0.9）」与 docs/design/pages/ 的 A02-desktop-list 为准；不新增文案、不改设计；需要时布局写在 frontend/src/styles/site.css（不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - frontend/src/pages/AdminOrdersPage.tsx 桌面表格中待审数大于 0 时 admin.refunds_pending 为站内链接，指向 /admin/refunds/order/<该订单内部 ID>；为 0 时仍显示 —；手机卡片不变（SHOP-TASK-055 起整张卡片为一个链接进入订单详情，待审数只作文字，为 0 时不显示，卡片里不嵌套链接）；订单号不进网址
      - frontend/src/pages/AdminOrdersPage.test.tsx 新增断言——链接目标、为 0 时不是链接、手机卡片里不出现嵌套链接；写明守住的是 UX A02 的哪一句；现有断言只按需要改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-058 记录段（改动、偏离、验证到什么程度，并写明未做浏览器验收）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（不新增路由）；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

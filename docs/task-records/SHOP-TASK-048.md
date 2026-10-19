# SHOP-TASK-048 后台订单列表 A02

- [x] 按 `docs/UX.md` 0.9 的 A02（含「状态与补充（0.9）」）、`docs/UX-COPY.md` 与 `docs/design/pages/` 的 A02-desktop 列表部分、A02-desktop-list、A02-desktop-empty、A02-phone-list 与 A02-phone-list-pages，在 SHOP-TASK-047 的 `/admin/orders` 上实现订单列表：订单号搜索、状态筛选与翻页，桌面为表格、手机为卡片列表，调用 SHOP-TASK-037 的 `POST /api/admin/orders/query`。未做订单详情、收货资料与模拟发货，待审退款数暂不链接到 A03；未装依赖（未改 `package.json`、锁文件），未改后端、CI、部署配置、`.platform/`、设计或生成的 `styles/acuven-shop.css`；未改 `router.tsx` 与 `App.tsx`（不新增路由）。设计闸门：不适用（只调用已登记的列表接口显示订单号、日期、状态与金额快照，不计算金额，不显示收货资料）。拆分：列表与详情、发货分开。
- 请求与字段（`frontend/src/api/adminOrders.ts`，新）：`queryAdminOrders` 以 `POST /api/admin/orders/query` 查询，同源 cookie、`cache: "no-store"`，带中止用的 signal；JSON 请求体恰好是 `order_number`（不筛选时为 null）、`status`（全部时为 null）与 `page` 三个字段。结果归为 `ok`（只取 `total`、`page`、`page_size` 与每行的 `id`、`order_number`、`created_at`、`status`、`total_sen`、`refunds_pending`，多出的字段不带进结果）、`none`（401）、`failed`（其他状态，以及不合格的响应体：计数不是非负整数、页码或每页条数不是正整数、`orders` 不是数组、任一行字段类型不对、下单时间解析不出日期、状态不是六种之一、金额或待审数不是非负整数）与 `network`。订单号只在页面内存与请求体里，不进网址、查询参数或浏览器存储。
- 页面（`frontend/src/pages/AdminOrdersPage.tsx`）：框架内容 `AdminOrdersContent` 依次为 h1 `admin.nav_orders`（SHOP-TASK-047 的标题不变）、搜索与筛选、列表区；展示部分 `AdminOrdersView` 与查询规则 `searchFor`、`filterBy`、`pagerOf`、`listStep`、`loadOrders` 另行导出供测试。
  - 搜索与筛选：`<form role="search">` 里为 `type="search"` 的输入框（可访问名称与占位文字都是 `admin.search_order`，照视觉稿）与状态下拉（可访问名称 `admin.filter_status`，第一项值为空、文字 `admin.filter_status` 表示全部，其余依次为六种 `order.status_*`）。按回车提交搜索：输入原样作为 `order_number`，去掉首尾空白后为空则为 null；状态不变、回到第 1 页。改变筛选时已提交的订单号不变、回到第 1 页。输入框文字、已提交的查询都只在 React 状态里，不进网址。
  - 桌面表格（`acs-admin__panel` 里的 `acs-admin__table`）：列头依次为 `pay.order_no`、`admin.col_date`、`admin.filter_status`、`admin.col_total`（靠右）、`admin.col_refunds`；每行为订单号、`TrackOrderPage.tsx` 的 `formatDate` 按界面语言的日期、状态标签（已取消为描边标签，其余为中性标签，照视觉稿）、`common.price_myr` 与 `formatSen` 的合计（RM 加金额）、待审数（大于 0 时 `admin.refunds_pending`，为 0 时次要文字 —）。行与单元格里没有链接或按钮。
  - 手机卡片（`ul` 里每张 `acs-admin__panel`）：第一行订单号与状态标签分列两端，第二行为日期 · 合计，待审数大于 0 时再加 · `admin.refunds_pending`，为 0 时不显示。分隔符 · 以 `aria-hidden` 标出。卡片不可点。表格与卡片都渲染，由 `site.css` 按宽度只显示其一。
  - 没有符合条件的订单时桌面只有列头（`tbody` 为空），不渲染卡片与翻页，不另写空状态文字。
- 翻页：`total` 超过 `page_size` 时列表下方为两个次要按钮，可见文字 ‹ 与 ›，可访问名称 `common.a11y_page_prev` 与 `common.a11y_page_next`；页码取自响应，第一页禁用上一页，`page × page_size` 达到总数时禁用下一页；桌面与手机是同一组按钮，不显示页码文字。翻页沿用当前订单号与状态。
- 错误处理与请求生命周期：列表区（`site-admin-orders__list`）在请求进行中标 `aria-busy="true"`，翻页或换筛选时保留上一页的列表直到新结果到来；401 用路由的 `replace` 进入 `/admin/login`；网络中断在列表区显示 `common.network_check`、其他失败显示 `common.error_retry`（框架的 `AdminAlert`，`role="alert"`），失败时不显示表格、卡片与翻页，搜索与筛选照常。每次查询变化都由 effect 发出新请求并中止上一次的，离开页面时同样中止；`loadOrders` 在 signal 已中止时不显示也不跳转，所以旧请求的结果不覆盖新查询。
- 字典（`i18n/copy.ts`）：从 UX-COPY 原样抄入 `admin.filter_status`、`admin.search_order`、`admin.col_date`、`admin.col_total`、`admin.col_refunds`、`admin.refunds_pending` 六个键。
- 样式（`styles/site.css`）：桌面标题、搜索与筛选、列表依次排列（间距 16px），搜索框 280px 与状态下拉同一行（间距 8px），表格容器可横向滚动，合计列靠右，翻页按钮靠右，卡片列表不显示；767px 及以下：间距 10px，搜索框与下拉各占一行并加高到 44px，表格不显示、卡片显示（内边距 12px 14px，第一行两端对齐），翻页按钮居中、至少 44×44。没有颜色、字体或圆角属性。
- 已有测试的改动（只按验收允许的范围）：`components/AdminFrame.test.tsx` 里作为框架内容的 `AdminOrdersContent` 换成测试文件内自写、只渲染 h1 `admin.nav_orders` 的替身组件（不再从订单页引入）；「其他导航项文字不出现」改为只查内容区（`acs-admin__main`）以外，因为 A02 的列头 `admin.col_refunds` 与 `admin.nav_refunds` 英文与马来文相同；「只出现字典文字」里对 `title` 与 `placeholder` 的检查由不许出现改为其值必须是当前语言的字典文案。其余断言未动。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - 手机卡片：A02-phone-list 与 A02-phone-list-pages 的卡片是整张链接、右侧带 › 图形；按验收行不可点（订单详情留给之后的任务），卡片改为列表项，不带链接与 › 图形。UX「手机卡片整张进入订单详情」随详情任务加上。
  - 桌面待审数：A02-desktop-list 与 UX「状态与补充（0.9）」里大于 0 时是链接；按验收暂为文字，随 A03 页面任务加上链接。
  - 日期：按验收用 `formatDate`（`dateStyle: "medium"`，按界面语言与浏览器时区），英文显示为「Sep 10, 2026」这类格式，视觉稿写的是「10 Sep 2026」。
  - 手机视觉稿的内容区没有 h1（标题只在顶栏）；按验收 SHOP-TASK-047 的标题行为不变，手机仍显示 h1。
  - 视觉稿没有搜索按钮，搜索以回车提交表单；UX-COPY 也没有搜索按钮文案。
  - 卡片第一行的 600 字重与第二行的 13px 字号需写字体属性，未搬；卡片第二行沿用 `acs-admin__muted`。视觉稿 A02 的状态下拉 `<option>` 只画了 `Status` 一项，其余选项按 UX 线框「状态筛选选项为各 [order.status_*]」。
  - 失败时清空列表只留提示、读取中保留上一页列表：UX 与视觉稿没有画这两种状态。
  - 页面上不来自字典的文字只有接口数据（订单号、格式化的日期与金额）与 UX、视觉稿给定的符号 —、·、‹、›。
- 留给之后的任务：订单详情（含收货资料、金额明细与事件记录）与模拟发货、手机卡片进入详情、桌面选中行与右侧详情；待审退款数链接到只含该单申请的 A03 列表（随 A03 页面任务）。
- 验证到什么程度：人工逐条对照验收标准自查。新增测试 `api/adminOrders.test.ts` 与 `pages/AdminOrdersPage.test.tsx`（vitest 与 `react-dom/server`，未加测试依赖；`fetch` 以按顺序回答、可模拟网络中断的替身，路由以记录 `replace` 的替身，localStorage、sessionStorage、`document.cookie` 与 history 以 `vi.stubGlobal` 替身记录写入），每条注释写明它守住的验收、UX、DESIGN 或视觉稿原句。覆盖：请求地址、方法、同源 cookie、no-store、signal 与恰好三个字段的请求体，订单号不进地址或浏览器存储；只取约定字段，401、其他状态与各种不合格响应体、网络中断的归类，六种状态都合格；搜索框与下拉的标签、占位文字、选项顺序与选中项；提交搜索与改变筛选回到第 1 页、空白输入不筛选、订单号原样；五个列头的顺序；每行的订单号、日期、状态名、RM 合计与待审数（为 0 时 —），表格里没有链接或按钮；卡片的内容与顺序、为 0 时不显示待审数、分隔符 `aria-hidden`；空列表只有列头；翻页按钮的显示条件、禁用、可访问名称、可见文字与不显示页码；读取中 `aria-busy`；200、401、网络中断、500 与不合格响应体各自的结果；失败提示；旧查询中止后（不论回答列表、401 或失败）只显示新查询的结果；离开页面后不再更新；各状态下页面文字只来自字典与接口数据。浏览器相关的部分（effect 与请求的实际接线、真实输入与回车、下拉选择与按钮点击、媒体查询下的显隐与布局）未在 DOM 中执行，只以纯函数、替身驱动的请求流程与服务端渲染的各状态验证。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，未连真实后端走一遍搜索、筛选与翻页，也未与视觉稿对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.9 的 A02（含「状态与补充（0.9）」）与 docs/UX-COPY.md 为准，样式以 docs/design/pages/ 的 A02-desktop 的列表部分、A02-desktop-list、A02-desktop-empty、A02-phone-list 与 A02-phone-list-pages 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性，视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）
      - 新文件 frontend/src/api/adminOrders.ts 调用 POST /api/admin/orders/query（JSON 请求体只有 order_number、status 与 page；同源 cookie，cache 为 no-store），结果归为 ok（只取 total、page、page_size 与每行的 id、order_number、created_at、status、total_sen、refunds_pending，响应体不合格算失败）、none（401）、failed 与 network；订单号只在请求体里，不进网址、查询参数或浏览器存储
      - 列表上方为搜索框（admin.search_order，提交时把输入原样作为 order_number，去掉首尾空白后为空则不筛选）与状态筛选下拉（第一项为 admin.filter_status 表示全部，其余依次为六种 order.status_*）；提交搜索或改变筛选回到第 1 页；搜索与筛选只在页面内存里，不进网址
      - 桌面表格列头依次为 pay.order_no、admin.col_date、admin.filter_status、admin.col_total、admin.col_refunds；手机为卡片列表（订单号与状态、日期、合计与待审数），由 site.css 按宽度显隐；日期用 frontend/src/pages/TrackOrderPage.tsx 的 formatDate 按界面语言显示，合计用 common.price_myr 与 frontend/src/format.ts 的 formatSen（桌面与手机都显示为 RM 加金额）；状态显示对应的 order.status_*；待审数大于 0 时显示 admin.refunds_pending（{count} 为该数），为 0 时桌面表格显示 —、手机卡片不显示（照 A02-phone-list）；行不可点（订单详情留给之后的任务）；没有符合条件的订单时只显示列头，不显示行，也不另写空状态文字（UX-COPY 没有后台空列表文案）
      - 翻页（按 docs/UX.md 0.9 的 A02 与画板 A02-desktop-list、A02-phone-list-pages）：总数超过每页条数时在列表下方显示上一页与下一页两个按钮，可见文字为 ‹ 与 ›，可访问名称为 common.a11y_page_prev 与 common.a11y_page_next，第一页禁用上一页、最后一页禁用下一页，桌面与手机相同；不显示页码文字
      - 请求进行中列表区标 aria-busy；401 用路由的 replace 进入 /admin/login；网络中断显示 common.network_check，其他失败显示 common.error_retry；离开页面或发出新查询时中止旧请求，旧请求的结果不再更新页面；SHOP-TASK-047 的框架、标题与导航行为不变；frontend/src/components/AdminFrame.test.tsx 只许这样改（开启前的骨架实测发现它拿订单页内容当框架内容测，列表一加就冲突），其余断言不动——作为框架内容的 AdminOrdersContent 换成测试文件内自写、只渲染 admin.nav_orders 标题（h1）的替身组件；「其他导航项文字不出现」的检查只查内容区以外（A02 的列头 admin.col_refunds 与 admin.nav_refunds 文字相同）；「只出现字典文字」里对 title 与 placeholder 的检查由不许出现改为其值必须是当前语言的字典文案（A02 视觉稿的搜索框以 admin.search_order 作占位文字）；不新增路由（frontend/src/router.tsx 与 frontend/src/App.tsx 按页面任务的惯例列入允许路径，本任务预计不改）
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/api/adminOrders.test.ts 与 frontend/src/pages/AdminOrdersPage.test.tsx 验证意图：请求体只有订单号、状态与页码且订单号不进网址或浏览器存储；响应体不合格算失败；表格列头与卡片内容；日期、合计、状态与待审数（为 0 时桌面显示 —、手机不显示）；搜索与筛选回到第 1 页；翻页按钮的禁用与页码；401 进入 /admin/login，网络中断与其他失败的提示；旧请求结果不覆盖新查询；页面文字全部来自字典；每条测试写明它守住的是 UX、DESIGN 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-048 记录段（请求与字段、搜索与筛选、翻页、错误处理、偏离、验证到什么程度，并写明未做浏览器验收、订单详情与发货、待审退款数链接留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]；改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

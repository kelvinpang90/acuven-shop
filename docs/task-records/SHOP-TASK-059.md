# SHOP-TASK-059 后台库存重置页的路由与导航 A07

- [x] 按 `docs/UX.md` 0.10 的 A07 与「管理后台总体」、`docs/UX-COPY.md` 0.10、`docs/HANDOFF.md` 0.35（Kelvin 2026-10-06：后台导航只显示已上线页面的项）与 0.39（Kelvin 2026-10-07：A02、A03、A07 页面决定），以及 `docs/design/pages/` 的 A07-desktop 与 A07-phone 的页头部分，加上后台库存重置页：路由 `/admin/stock-resets`，用 SHOP-TASK-047 的后台框架渲染，当前导航项为库存重置，内容区只有页标题与说明；后台导航按 UX 顺序加上库存重置。改动：`frontend/src/pages/AdminStockResetsPage.tsx`（新）、`pages/AdminStockResetsPage.test.tsx`（新）、`components/AdminFrame.tsx`、`components/AdminFrame.test.tsx`、`pages/AdminRefundsPage.test.tsx`、`router.tsx`、`router.test.tsx`、`App.tsx`、`i18n/copy.ts`、`styles/site.css`。页面不调用接口；未装依赖（未改 `package.json`、锁文件），未改后端、CI、部署配置、`.platform/`、设计或生成的 `styles/acuven-shop.css`。设计闸门：不适用（只加路由、导航与静态文字）。拆分：A07 拆成路由与导航（本任务）和列表与明细（SHOP-TASK-060）。
- 路由：`router.tsx` 路由表在 `/admin/refunds/:id` 之后加 `/admin/stock-resets`（没有动态段），新增常量 `ADMIN_STOCK_RESETS_PATH`；`App.tsx` 页面表把它映射到 `AdminStockResetsPage`（以 `/admin` 开头，不套前台框架）。
- 页面（`pages/AdminStockResetsPage.tsx`）：`AdminFrame current="stockResets"` 里放内容区 `AdminStockResetsContent`（另行导出供测试）：h1 `admin.stock_reset_title`（`acs-admin__h`），其下为说明 `admin.stock_reset_note`（`p`）。本页不引入接口模块、没有 effect，打开时唯一的请求是框架读取会话。
- 导航变化（`components/AdminFrame.tsx`）：`AdminNavItem` 加 `stockResets`，`ADMIN_NAV` 由订单、退款两项改为依 UX「管理后台总体」顺序的订单、退款、库存重置三项（库存重置指向 `/admin/stock-resets`，文字 `admin.nav_stock_resets`）；桌面左侧导航、手机 ☰ 菜单与手机顶栏名称都按此；商品、优惠券、运费、店铺装修与站点设置仍不出现。
- 字典（`i18n/copy.ts`）：从 UX-COPY 原样抄入 `admin.nav_stock_resets`、`admin.stock_reset_title`、`admin.stock_reset_note` 三个键。未用到 UX-COPY 以外的界面文字。
- 样式（`styles/site.css`）：`.site-admin-stock-resets` 为标题与说明上下排列（间距 16px），内容最宽 960px（A07-desktop 的 `main` 写的是 `max-width: 960px`，后台框架的 `main` 为各页共用，所以写在本页容器上）；说明去掉 `p` 的外边距、内边距 10px 12px。767px 及以下间距 10px（照 A07-phone）。没有颜色、字体或圆角属性。
- 已有测试的改动（只按验收允许的范围）：`components/AdminFrame.test.tsx` 只把「只含订单与退款两项」的断言改为按 UX 顺序只含订单、退款与库存重置三项、其他 `admin.nav_*` 不出现——「lists only the orders and refunds items」一条改名为「lists only the orders, refunds and stock resets items」，断言 `ADMIN_NAV` 为三项、UX-COPY 的导航键里这三项依次出现（原为「前两个导航键即这两项」，库存重置在 UX-COPY 里排第 6，中间隔着未上线的项），桌面导航的三个链接依次指向 `/admin/orders`（`aria-current="page"`）、`/admin/refunds` 与 `/admin/stock-resets`；「opens the menu」一条里菜单导航的断言同样改为这三项；找出「其他导航项文字」所用的 `LIVE_NAV_KEYS` 随之加上 `admin.nav_stock_resets`。`pages/AdminRefundsPage.test.tsx` 只把 navigation 一组里「依次为订单与退款两项」改为依次为订单、退款与库存重置三项（退款仍标 `aria-current="page"`，手机顶栏名称仍为 `admin.nav_refunds`）。`router.test.tsx` 的路由表断言加上 `/admin/stock-resets`（并断言它在表里）。两个导航测试文件的其余断言未动。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - UX 与视觉稿没有冲突：UX A07 线框与两张视觉稿的页头都是标题之下为说明。视觉稿的桌面导航画了完整的 8 项，按 Kelvin 2026-10-06 决定只显示已上线的三项。
  - `site.css` 写不了、未搬的视觉：说明的 `--demo-soft` 底色、`--on-demo-soft` 文字色、4px 圆角与 13px 字号；A07-phone 里 h1 的 17px 字号。说明未借用其他组件的类取色。评审不以此要求修改。
  - 说明用 `p` 元素（视觉稿为 `div`），只影响语义，外边距在 `site.css` 里去掉。
- 留给之后的任务：每日重置的列表（日期、结果、SKU 数）与展开的每 SKU 明细、调用 SHOP-TASK-052 的两个接口、失败行与空明细的显示（SHOP-TASK-060）；A04 与 A07 之间的入口与去向（随 A04）；上面列出的颜色、字号与圆角偏离。
- 验证到什么程度：人工逐条对照验收标准、UX A07 线框与「管理后台总体」的导航顺序、HANDOFF 0.35 与 0.39 的决定原句自查，人工核对新增的 3 个字典键与 UX-COPY 原文（逐字比较由已有的 `i18n/copy.test.ts` 覆盖）。测试用 vitest 与 `react-dom/server`（未加测试依赖；路由以给定初始路径的 `RouterProvider` 代替，会话以 `AdminFrameView` 的 `state` 替身代替），每条注释写明它守住的 UX、Kelvin 决定或验收的哪一句。`pages/AdminStockResetsPage.test.tsx` 覆盖：经路由打开 `/admin/stock-resets` 渲染后台框架（演示横幅、会话未返回时内容区为空并标 `aria-busy`）、不套前台框架；桌面导航与 ☰ 菜单依次为订单、退款与库存重置三项、库存重置为当前项；手机顶栏名称为 `admin.nav_stock_resets`；已登录时内容区只有 h1 `admin.stock_reset_title` 与其下的 `admin.stock_reset_note`，没有表格、列表或按钮；页面源码不引入接口模块、不调用 `fetch`、没有 effect；三种语言下各状态的页面文字只来自字典。浏览器相关的部分（会话读取的实际接线、点击导航项换页、媒体查询下的间距与布局）未在 DOM 中执行。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器或手机上打开页面，也未与视觉稿对比）；列表与每日明细留给 SHOP-TASK-060。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的 A07、docs/UX-COPY.md 0.10 与 docs/HANDOFF.md 0.35、0.39 为准，样式以 docs/design/pages/ 的 A07-desktop 与 A07-phone 的页头部分为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 路由 /admin/stock-resets（frontend/src/router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项），新文件 frontend/src/pages/AdminStockResetsPage.tsx 用后台框架渲染，当前导航项为库存重置（手机顶栏标题为 admin.nav_stock_resets），内容区为 h1 admin.stock_reset_title 与其下的 admin.stock_reset_note；页面不发起自己的请求（列表由 SHOP-TASK-060 接上）
      - frontend/src/components/AdminFrame.tsx 的导航按 UX 顺序只含已上线的订单、退款与库存重置三项（桌面导航与 ☰ 菜单同样）；frontend/src/components/AdminFrame.test.tsx 只许把「只含订单与退款两项」的断言（桌面导航与手机菜单）改为按 UX 顺序只含这三项、其他 admin.nav_* 不出现；frontend/src/pages/AdminRefundsPage.test.tsx 只许把 navigation 一组里「依次为订单与退款两项」的断言改为依次为订单、退款与库存重置三项（退款仍标 aria-current="page"，手机顶栏名称仍为 admin.nav_refunds）；两个文件的其余断言不动
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替）在 frontend/src/pages/AdminStockResetsPage.test.tsx 验证意图——经路由打开页面渲染后台框架、不套前台框架；当前导航项为库存重置且三项顺序正确；标题与说明；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-059 记录段（路由、导航变化、改动的现有测试、偏离、验证到什么程度，并写明未做浏览器验收、列表与明细留给 SHOP-TASK-060），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

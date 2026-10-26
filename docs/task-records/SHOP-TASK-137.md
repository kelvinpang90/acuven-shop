# SHOP-TASK-137 后台站点设置页的路由与导航 A09

- [x] 按 `docs/UX.md` 0.11 的 A09 与「管理后台总体」、`docs/UX-COPY.md` 0.12，加上后台站点设置页 `/admin/settings`：用后台框架渲染，当前导航项为站点设置（手机顶栏标题为 `admin.nav_settings`），内容区只有 h1 `admin.nav_settings`；页面不发起自己的请求。本任务不调用接口、不装依赖（未改 `package.json` 与锁文件）、不改后端、设计、`docs/TODO.md`、`.platform/`、CI、部署配置与生成的 `styles/acuven-shop.css`。设计闸门：不适用（只加路由、导航与静态文字）。拆分：A09 拆成路由与导航（本任务）、请求模块（SHOP-TASK-138）与支付方式表单（SHOP-TASK-139），照 A05 的 SHOP-TASK-100 至 102；14 个允许路径，导航加一项须同时改后台框架与退款、库存重置、店铺装修、优惠券四页已有的导航断言，拆不开。
- 路由：`router.tsx` 的路由表在 `/admin/store-design` 之后加 `/admin/settings`，并导出 `ADMIN_SITE_SETTINGS_PATH`；`App.tsx` 的页面表加一项映射到新文件 `pages/AdminSiteSettingsPage.tsx`（以 `/admin` 开头，不套前台框架）；`router.test.tsx` 的路由表断言加上它，并断言 `isRoutePath("/admin/settings")`。
- 文案：`i18n/copy.ts` 在 `admin.nav_store_design` 之后收录 `admin.nav_settings`，三语逐字取自 UX-COPY 0.12（由 `copy.test.ts` 现有的逐字比较守住）；没有用到 UX-COPY 以外的界面文字。
- 导航变化：`components/AdminFrame.tsx` 的 `AdminNavItem` 与 `ADMIN_NAV` 加 `settings`（指向 `/admin/settings`，文字 `admin.nav_settings`），按 UX「管理后台总体」的顺序排在店铺装修之后；桌面导航与 ☰ 菜单同为订单、退款、优惠券、库存重置、店铺装修与站点设置六项。
- 样式（`site.css`）：`.site-admin-settings` 为上下排列（间距 16px），内容最宽 720px（A09-stripe-desktop 的 `main` 的 `max-width`）；767px 及以下间距 14px（A09-stripe-phone），h1 只留给读屏（与店铺装修页的做法相同）。未声明颜色、字体与圆角。
- 改动的现有测试（只改导航断言，其余断言未动）：
  - `components/AdminFrame.test.tsx`：`LIVE_NAV_KEYS` 加 `admin.nav_settings`；桌面导航一条把「只含五项」改为按 UX 顺序只含六项（`ADMIN_NAV`、链接与 `aria-current` 各加一项、`admin.nav_settings` 的文字与在店铺装修之后的顺序），其他 `admin.nav_*` 仍不出现；手机菜单一条同样加上第六项。
  - `pages/AdminRefundsPage.test.tsx`、`pages/AdminStockResetsPage.test.tsx`、`pages/AdminStoreDesignPage.test.tsx`、`pages/AdminCouponsPage.test.tsx`：navigation 一组里「依次为这五项」改为依次为这六项（链接与文字各加 `/admin/settings` 与 `admin.nav_settings`，取文字的 `slice(0, 5)` 改为 `slice(0, 6)`）；各自的当前项与手机顶栏名称不变。
- 新增测试（`pages/AdminSiteSettingsPage.test.tsx`，vitest 与 `react-dom/server`，路由以给定 `initialPath` 的 `RouterProvider` 代替，每条写明所守的 UX 或 Kelvin 决定）：经路由打开渲染后台框架、不套前台框架；桌面导航与 ☰ 菜单六项顺序正确、站点设置为当前项；手机顶栏名称为 `admin.nav_settings`；内容区只有 h1；页面任何地方（含 ☰ 菜单展开时）没有 UX-COPY 里 `admin.sms_*` 的文字、也没有表单与单选；渲染内容不调用 fetch；页面文字全部来自字典。
- 偏离与取舍，请审阅（未改设计与文案）：
  - 短信验证段不做：A09-stripe-desktop、A09-stripe-phone 与 UX A09 线框里的 `admin.sms_toggle` 一组（开关、说明与费用提示）按 Kelvin 2026-10-07 与 2026-10-15 的决定不做，本页先只放支付方式开关（由 SHOP-TASK-139 接上）。
  - A09-stripe-phone 不画标题（顶栏已是 Site settings），桌面稿与 UX 桌面线框有 h1；本页 h1 两端都在，手机由 `site.css` 只留给读屏，与 UX 手机线框（顶栏「☰ [admin.nav_settings]」、内容区不另写标题）一致。
  - 视觉稿的 `main` 另有 `align-items: start`；本页内容为单个 grid 元素，`site.css` 不改共用的 `.acs-admin__main`，`max-width` 写在本页自己的容器上。A09-stripe-phone 的 `main` 内边距 16px 来自后台框架现有样式，本任务未改。
  - 页头部分没有 `site.css` 写不了而需要搬的颜色、字号、字重或底色；视觉稿里表单部分的这些属性留给 SHOP-TASK-139。
- 留给之后的任务：请求模块（站点设置的读取与保存）由 SHOP-TASK-138 实现；支付方式表单（模拟 / Stripe 测试、凭据未配置时禁用与 `admin.stripe_not_configured`、保存与提示）由 SHOP-TASK-139 实现。短信验证开关按 Kelvin 2026-10-07 的决定等短信验证服务与会员结账路径上线后再做。
- 验证到什么程度：人工逐条对照验收标准、UX 0.11 A09 与「管理后台总体」的导航顺序、UX-COPY 0.12 的 `admin.nav_settings` 原句，样式对照 A09-stripe-desktop 与 A09-stripe-phone 的页头部分。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（未在真实浏览器中打开页面，也未与视觉稿逐像素对比）。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.11 的 A09 与 docs/UX-COPY.md 0.12 为准，样式以 docs/design/pages/ 的 A09-stripe-desktop 与 A09-stripe-phone 的页头部分为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 路由 /admin/settings（frontend/src/router.tsx 加 ADMIN_SITE_SETTINGS_PATH，router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项），新文件 frontend/src/pages/AdminSiteSettingsPage.tsx 用后台框架渲染，当前导航项为站点设置（手机顶栏标题为 admin.nav_settings），内容区只有 h1 admin.nav_settings；frontend/src/i18n/copy.ts 按 UX-COPY 0.12 逐字加入 admin.nav_settings；页面不发起自己的请求（读取与保存由 SHOP-TASK-139 接上）；视觉稿里的短信验证段不做（Kelvin 2026-10-07 与 2026-10-15 的决定），记入偏离
      - frontend/src/components/AdminFrame.tsx 的导航按 UX 顺序只含已上线的订单、退款、优惠券、库存重置、店铺装修与站点设置六项（桌面导航与 ☰ 菜单同样）；frontend/src/components/AdminFrame.test.tsx 只许把「只含订单、退款、优惠券、库存重置与店铺装修五项」的断言（桌面导航与手机菜单，含 LIVE_NAV_KEYS）改为按 UX 顺序只含这六项、其他 admin.nav_* 不出现；frontend/src/pages/AdminRefundsPage.test.tsx、frontend/src/pages/AdminStockResetsPage.test.tsx、frontend/src/pages/AdminStoreDesignPage.test.tsx 与 frontend/src/pages/AdminCouponsPage.test.tsx 只许把 navigation 一组里「依次为这五项」的断言改为依次为这六项（各自的当前项与手机顶栏名称不变）；五个文件的其余断言不动
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替）在 frontend/src/pages/AdminSiteSettingsPage.test.tsx 验证意图——经路由打开页面渲染后台框架、不套前台框架；当前导航项为站点设置且六项顺序正确；内容区只有标题、没有任何 admin.sms_* 文字；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-137.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；路由、导航变化、改动的现有测试、偏离、验证到什么程度，并写明未做浏览器验收、请求模块与支付方式表单留给 SHOP-TASK-138、139）
      - 改动只落在 allowed_change_paths；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

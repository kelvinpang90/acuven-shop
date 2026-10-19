# SHOP-TASK-062 后台店铺装修页的路由与导航 A08

- [x] 按 `docs/UX.md` 0.10 的 A08 与「管理后台总体」、`docs/UX-COPY.md` 0.10、`docs/HANDOFF.md` 0.35（Kelvin 2026-10-06：后台导航只显示已上线页面的项）与 0.40，以及 `docs/design/pages/` 的 A08-desktop 与 A08-phone 的页头部分，加上后台店铺装修页：路由 `/admin/store-design`，用 SHOP-TASK-047 的后台框架渲染，当前导航项为店铺装修，内容区只有页标题与演示说明；后台导航按 UX 顺序加上店铺装修。改动：`frontend/src/pages/AdminStoreDesignPage.tsx`（新）、`pages/AdminStoreDesignPage.test.tsx`（新）、`components/AdminFrame.tsx`、`components/AdminFrame.test.tsx`、`pages/AdminRefundsPage.test.tsx`、`pages/AdminStockResetsPage.test.tsx`、`router.tsx`、`router.test.tsx`、`App.tsx`、`i18n/copy.ts`、`styles/site.css`、本文件。页面不调用接口；未装依赖（未改 `package.json`、锁文件），未改后端、CI、部署配置、`.platform/`、设计或生成的 `styles/acuven-shop.css`。设计闸门：不适用（只加路由、导航与静态文字）。拆分：A08 拆成路由与导航（本任务）、请求模块（SHOP-TASK-063）、主题主色区块与保存（SHOP-TASK-064）、精选商品（SHOP-TASK-065）与预览（SHOP-TASK-066）。
- 路由：`router.tsx` 路由表在 `/admin/stock-resets` 之后加 `/admin/store-design`（没有动态段），新增常量 `ADMIN_STORE_DESIGN_PATH`；`App.tsx` 页面表把它映射到 `AdminStoreDesignPage`（以 `/admin` 开头，不套前台框架，也不放进前台的店铺装修设置提供者）。
- 页面（`pages/AdminStoreDesignPage.tsx`）：`AdminFrame current="storeDesign"` 里放内容区 `AdminStoreDesignContent`（另行导出供测试）：h1 `admin.nav_store_design`（`acs-admin__h`），其下为演示说明 `admin.design_demo_note`（`p`）。本页不引入接口模块、没有 effect，打开时唯一的请求是框架读取会话。
- 导航变化（`components/AdminFrame.tsx`）：`AdminNavItem` 加 `storeDesign`，`ADMIN_NAV` 由订单、退款、库存重置三项改为依 UX「管理后台总体」顺序的订单、退款、库存重置、店铺装修四项（店铺装修指向 `/admin/store-design`，文字 `admin.nav_store_design`）；桌面左侧导航、手机 ☰ 菜单与手机顶栏名称都按此；商品、优惠券、运费与站点设置仍不出现。
- 字典（`i18n/copy.ts`）：从 UX-COPY 原样抄入 `admin.nav_store_design`、`admin.design_demo_note` 两个键。未用到 UX-COPY 以外的界面文字。
- 样式（`styles/site.css`）：`.site-admin-store-design` 为标题与说明上下排列（间距 16px，照 A08-desktop 左栏）；说明去掉 `p` 的外边距、内边距 10px 12px。767px 及以下：间距 14px（照 A08-phone 的 `main`），h1 只留给读屏（绝对定位、1px、`clip-path: inset(50%)`、不换行），见下面的偏离。没有颜色、字体或圆角属性。
- 已有测试的改动（只按验收允许的范围，注释写明原因）：`components/AdminFrame.test.tsx` 只把「只含订单、退款与库存重置三项」的断言改为按 UX 顺序只含订单、退款、库存重置与店铺装修四项、其他 `admin.nav_*` 不出现——「lists only the orders, refunds and stock resets items」一条改名为「lists only the orders, refunds, stock resets and store design items」，断言 `ADMIN_NAV` 为四项、UX-COPY 的导航键里这四项依次出现，桌面导航的四个链接依次指向 `/admin/orders`（`aria-current="page"`）、`/admin/refunds`、`/admin/stock-resets` 与 `/admin/store-design`，并加上店铺装修排在库存重置之后；「opens the menu」一条里菜单导航的断言同样改为这四项；找出「其他导航项文字」所用的 `LIVE_NAV_KEYS` 随之加上 `admin.nav_store_design`（新增常量 `STORE_DESIGN_PATH`）。`pages/AdminRefundsPage.test.tsx` 与 `pages/AdminStockResetsPage.test.tsx` 只把 navigation 一组里「依次为订单、退款与库存重置三项」改为依次为这四项（各自的当前项与手机顶栏名称不变）。`router.test.tsx` 的路由表断言加上 `/admin/store-design`（并断言它在表里）。三个导航测试文件的其余断言未动。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - 手机的标题：UX A08 手机线框与 A08-phone 都是顶栏「☰ [admin.nav_store_design]」之下直接为演示说明，页面里没有标题；验收要求内容区为 h1 `admin.nav_store_design` 与其下的说明。两者都照：h1 留在页面里，767px 及以下在 `site.css` 里只留给读屏（不占位置、不显示），桌面照常显示。
  - 视觉稿的桌面导航画了完整的 8 项，按 Kelvin 2026-10-06 决定只显示已上线的四项。
  - A08-desktop 的演示说明前有一个 ★ 图形（填色 `--demo-mark`）；UX A08 只把 ★ 标在 `admin.demo_banner` 上，A08-phone 的说明也没有图形，图形的颜色 `site.css` 也写不了，本页不画这个图形。
  - `site.css` 写不了、未搬的视觉：说明的 `--demo-soft` 底色、`--on-demo-soft` 文字色、4px 圆角与 13px 字号，以及上面的 ★ 图形。说明未借用其他组件的类取色。评审不以此要求修改。
  - 说明用 `p` 元素（视觉稿为 `div`），只影响语义，外边距在 `site.css` 里去掉（与 SHOP-TASK-059 的 A07 说明相同）。
  - A08-desktop 的 `main` 为左栏与 460px 预览栏两列；本任务只有页头，两列布局随预览留给 SHOP-TASK-066。
- 留给之后的任务：读取与保存的请求模块（SHOP-TASK-063）；主题、主色、首页区块的表单与保存（SHOP-TASK-064）；精选商品挑选（SHOP-TASK-065）；预览与桌面两列布局（SHOP-TASK-066）；标志图（另行登记）；上面列出的颜色、字号、圆角与 ★ 图形偏离。
- 验证到什么程度：人工逐条对照验收标准、UX A08 线框与「管理后台总体」的导航顺序、HANDOFF 0.35 的决定原句自查，人工核对新增的 2 个字典键与 UX-COPY 原文（逐字比较由已有的 `i18n/copy.test.ts` 覆盖）。测试用 vitest 与 `react-dom/server`（未加测试依赖；路由以给定初始路径的 `RouterProvider` 代替，会话以 `AdminFrameView` 的 `state` 替身代替），每条注释写明它守住的 UX、Kelvin 决定或验收的哪一句。`pages/AdminStoreDesignPage.test.tsx` 覆盖：经路由打开 `/admin/store-design` 渲染后台框架（演示横幅、会话未返回时内容区为空并标 `aria-busy`）、不套前台框架；桌面导航与 ☰ 菜单依次为订单、退款、库存重置与店铺装修四项、店铺装修为当前项；手机顶栏名称为 `admin.nav_store_design`；已登录时内容区只有 h1 `admin.nav_store_design` 与其下的 `admin.design_demo_note`，没有表单、按钮或链接；演示横幅与说明都在、没有关闭控件；页面源码不引入接口模块、不调用 `fetch`、没有 effect；三种语言下各状态的页面文字只来自字典。浏览器相关的部分（会话读取的实际接线、点击导航项换页、媒体查询下的间距与手机上只留给读屏的标题）未在 DOM 中执行。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器或手机上打开页面，也未与视觉稿对比）；请求模块、表单、精选与预览留给 SHOP-TASK-063 到 066。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的 A08、docs/UX-COPY.md 0.10 与 docs/HANDOFF.md 0.35、0.40 为准，样式以 docs/design/pages/ 的 A08-desktop 与 A08-phone 的页头部分为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色（如演示说明的底色）不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 路由 /admin/store-design（frontend/src/router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项），新文件 frontend/src/pages/AdminStoreDesignPage.tsx 用后台框架渲染，当前导航项为店铺装修（手机顶栏标题为 admin.nav_store_design），内容区为 h1 admin.nav_store_design 与其下的 admin.design_demo_note；页面不发起自己的请求（表单由 SHOP-TASK-064 接上）
      - frontend/src/components/AdminFrame.tsx 的导航按 UX 顺序只含已上线的订单、退款、库存重置与店铺装修四项（桌面导航与 ☰ 菜单同样）；frontend/src/components/AdminFrame.test.tsx 只许把「只含订单、退款与库存重置三项」的断言（桌面导航与手机菜单）改为按 UX 顺序只含这四项、其他 admin.nav_* 不出现；frontend/src/pages/AdminRefundsPage.test.tsx 与 frontend/src/pages/AdminStockResetsPage.test.tsx 只许把 navigation 一组里「依次为订单、退款与库存重置三项」的断言改为依次为这四项（各自的当前项与手机顶栏名称不变）；三个文件的其余断言不动
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替）在 frontend/src/pages/AdminStoreDesignPage.test.tsx 验证意图——经路由打开页面渲染后台框架、不套前台框架；当前导航项为店铺装修且四项顺序正确；标题与演示说明；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-062 记录段（路由、导航变化、改动的现有测试、偏离、验证到什么程度，并写明未做浏览器验收、请求模块、表单、精选与预览留给 SHOP-TASK-063 到 066），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

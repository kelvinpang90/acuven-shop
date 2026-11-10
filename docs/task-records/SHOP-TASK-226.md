# SHOP-TASK-226 后台 Stripe 待处理页的路由与导航 A11

- [x] 路由：按 `docs/UX.md` 0.19 A11（「独立一页 `/admin/stripe-pending`」）与「管理后台总体」、`docs/UX-COPY.md` 0.21 增加 `/admin/stripe-pending`：`frontend/src/router.tsx` 的路由表在 `/admin/refunds/:id` 之后加一项并导出 `ADMIN_STRIPE_PENDING_PATH`，文件头说明后台页的注释补上该路径（不带段值）；`frontend/src/App.tsx` 的页面表加 `"/admin/stripe-pending": AdminStripePendingPage`。本任务不装依赖、不改后端、不改设计、不调用接口。设计闸门：不适用（只加路由、导航与标题，不碰金额与个人资料）。
- [x] 页面：新文件 `frontend/src/pages/AdminStripePendingPage.tsx` 用后台框架渲染（`AdminFrame` 的 `current` 为 `stripePending`），内容区由另导出的 `AdminStripePendingContent` 渲染，只有 `<h1 class="acs-admin__h">` `admin.nav_stripe_pending`；手机顶栏名称随框架为 `admin.nav_stripe_pending`。没有加其他文案键。
- [x] 文案：`frontend/src/i18n/copy.ts` 在 `admin.nav_refunds` 之后加 `admin.nav_stripe_pending`，三语按 UX-COPY 0.21 逐字为 `Stripe follow-ups`、`Stripe 待处理`、`Susulan Stripe`（每种语言一行，写法同相邻条目）。
- [x] 导航变化：`frontend/src/components/AdminFrame.tsx` 的 `AdminNavItem`、`ADMIN_NAV` 与 `NAV_ENTRY` 在 `refunds` 之后加入 `stripePending`（指向 `ADMIN_STRIPE_PENDING_PATH`，文字 `admin.nav_stripe_pending`），文件头注释的导航顺序随之更新；桌面导航与 ☰ 菜单都依次为订单、退款、Stripe 待处理、商品、分类、优惠券、运费与演示汇率、库存重置、店铺装修、站点设置十项，即 UX 0.19「管理后台总体」的全部导航项。
- [x] 改动的现有测试（只改按顺序列出导航项或路由表的断言及其测试名，其余断言不动；原因都是导航或路由表多了一项，旧的九项期望必然失效）。各改动处的测试注释加了一段「SHOP-TASK-226 改动」，引 UX 0.19「管理后台总体」的导航顺序原句：
  - `frontend/src/components/AdminFrame.test.tsx`：文件顶部 `LIVE_NAV_KEYS` 在 `admin.nav_refunds` 之后加 `admin.nav_stripe_pending`，另加常量 `STRIPE_PENDING_PATH`；「lists only … in the navigation」一条把 UX-COPY 0.21 改成的「UX-COPY 的导航项多于已上线项」（`filter` 后相等加 `length` 大于）改回两者相同（`DOC_NAV_KEYS` 与 `LIVE_NAV_KEYS` 相等），`ADMIN_NAV`、桌面导航 href 与 aria-current 的期望改为十项，加 `admin.nav_stripe_pending` 的文字断言，原来退款 < 商品的顺序断言换成退款 < Stripe 待处理 < 商品，测试名加入 Stripe follow-ups；「opens the menu …」一条的手机菜单 href/aria-current 期望加入 `/admin/stripe-pending`，并加该项的文字断言。`otherNavLabels` 随之为空表。
  - `frontend/src/router.test.tsx`：路由表断言在 `/admin/refunds/:id` 之后加 `/admin/stripe-pending`。
  - `frontend/src/pages/AdminCategoriesPage.test.tsx`：文件顶部 `NAV_PATHS` 与 `NAV_KEYS` 在退款之后加 Stripe 待处理；「marks categories current …」的 aria-current 期望改为十项、`slice(0, 9)` 改为 `slice(0, 10)`（当前项仍为分类）。
  - `frontend/src/pages/AdminCouponsPage.test.tsx`：文件顶部 `NAV_PATHS` 加 `/admin/stripe-pending`；「marks coupons current …」的 aria-current 期望改为十项（当前项仍为优惠券）。
  - `frontend/src/pages/AdminProductsPage.test.tsx`：文件顶部 `NAV_PATHS` 与 `NAV_KEYS` 加该项；「marks products current …」的 aria-current 期望改为十项、`slice` 改为前十个；商品不再紧跟退款，测试名的「after refunds」改为「after Stripe follow-ups」（当前项仍为商品）。
  - `frontend/src/pages/AdminRefundsPage.test.tsx`：「marks the refunds item as current」的 href/aria-current 期望与导航文字期望在退款之后加入 Stripe 待处理（当前项仍为退款）。
  - `frontend/src/pages/AdminShippingPage.test.tsx`：文件顶部 `NAV_PATHS` 与 `NAV_KEYS` 加该项；「marks shipping current …」的 aria-current 期望改为十项、`slice` 改为前十个（当前项仍为运费与演示汇率）。
  - `frontend/src/pages/AdminSiteSettingsPage.test.tsx`：文件顶部 `NAV_PATHS` 与 `NAV_KEYS` 加该项；「marks site settings current …」的 aria-current 期望改为十项、`slice` 改为前十个（当前项仍为站点设置）。
  - `frontend/src/pages/AdminStockResetsPage.test.tsx`：「marks the stock resets item as current …」的 href/aria-current 与导航文字期望加入该项、`slice` 改为前十个，测试名的「after …」加入 Stripe follow-ups（当前项仍为库存重置）。
  - `frontend/src/pages/AdminStoreDesignPage.test.tsx`：「marks the store design item as current …」同上，测试名「after the other seven before it」改为「after the other eight before it」（当前项仍为店铺装修）。
  - 各文件的手机顶栏名称断言未改。
- [x] 新增测试 `frontend/src/pages/AdminStripePendingPage.test.tsx`（vitest 与 react-dom/server，路由用 `RouterProvider` 的 `initialPath`，fetch 以替身代替）：经路由打开 `/admin/stripe-pending` 渲染后台框架、不套前台框架，路径不带段值；三种语言下桌面导航与 ☰ 菜单的十项顺序、Stripe 待处理在退款之后与商品之前、只有它标 `aria-current="page"`；手机顶栏名称 `admin.nav_stripe_pending`；已登录时内容区只有 h1 `admin.nav_stripe_pending`，会话返回前内容区没有 h1；渲染不调用 fetch；页面文字全部来自字典。每条测试注释写明守住的 UX 0.19「管理后台总体」或 A11 的原句。
- 偏离：视觉稿 A11-phone 的 h1 `font-size: 17px` 是 site.css 不能声明的字号，没有搬入；A11-phone 内容区的网格（`grid-template-columns: minmax(0, 1fr); gap: 10px`）与 A11-desktop 的两个 h2 区块属于之后的列表内容，本任务内容区只有标题，未加；因此 `frontend/src/styles/site.css` 未改动。没有借用其他组件的类取颜色。UX 与视觉稿在页头与导航部分不冲突（视觉稿 README：其他后台画板未重画，导航顺序以 UX 0.19 为准）。
- 留给之后的任务：请求模块由 SHOP-TASK-227 实现；两个列表、作废确认框与退款重试由 SHOP-TASK-228 实现。未做浏览器验收。
- 验证到什么程度：人工逐条对照 UX 0.19「管理后台总体」的导航顺序、A11 的入口与线框（桌面「☰ 导航 | [admin.nav_stripe_pending]」、手机「☰  [admin.nav_stripe_pending]」）与 UX-COPY 0.21 的 `admin.nav_stripe_pending` 一行；上述测试为本次新增或修改的测试代码，前端检查由 PR 的必需 CI 检查 `frontend` 执行。

## 登记时的验收标准（原文）

2026-11-10 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 路由——frontend/src/router.tsx 的路由表在 /admin/refunds/:id 之后加 /admin/stripe-pending，导出 ADMIN_STRIPE_PENDING_PATH；frontend/src/App.tsx 的页面表加 AdminStripePendingPage；文件头注释补上该路径不带段值
      - 导航——frontend/src/components/AdminFrame.tsx 的 AdminNavItem、ADMIN_NAV 与 NAV_ENTRY 在 refunds 之后加 stripePending（指向 /admin/stripe-pending，文字 admin.nav_stripe_pending），桌面导航与手机菜单都按此顺序；frontend/src/i18n/copy.ts 加 admin.nav_stripe_pending，三语与 docs/UX-COPY.md 逐字相同
      - 页面——AdminStripePendingPage 用后台框架渲染，current 为 stripePending，内容区只有 h1 admin.nav_stripe_pending；本任务不加其他文案键、不调用接口；布局沿用现有后台页面，需要时才改 frontend/src/styles/site.css（只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性；视觉稿里 site.css 写不了的记入任务记录的偏离）
      - 现有测试——frontend/src/components/AdminFrame.test.tsx 的 LIVE_NAV_KEYS 在 admin.nav_refunds 之后加 admin.nav_stripe_pending，并把 UX-COPY 0.21 改成的「UX-COPY 的导航项多于已上线项」改回「两者相同」（DOC_NAV_KEYS 与 LIVE_NAV_KEYS 相等），导航项、href 与 aria-current 的期望改为十项；router.test.tsx 与 8 个后台页面测试（Categories、Coupons、Products、Refunds、Shipping、SiteSettings、StockResets、StoreDesign）只改按顺序列出导航项或路由表的断言与测试名，加入这一项，其余断言不动
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——新文件 frontend/src/pages/AdminStripePendingPage.test.tsx 断言三种语言下标题、当前导航项（aria-current 为 page）与它在退款之后、商品之前；每条新增或改动的测试注释写明它守住的 UX 0.19「管理后台总体」或 A11 的原句
      - docs/task-records/SHOP-TASK-226.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明不适用及理由），不合并、不直推
```

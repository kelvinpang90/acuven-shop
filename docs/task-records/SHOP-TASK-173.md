# SHOP-TASK-173 后台运费与演示汇率页的路由与导航 A06

- [x] 按 `docs/UX.md` 0.16「管理后台总体」与 A06、`docs/UX-COPY.md` 0.17 与 `docs/HANDOFF.md` 0.72 增加 `/admin/shipping`：`frontend/src/router.tsx` 的路由表在 `/admin/coupons` 之后加一项并导出 `ADMIN_SHIPPING_PATH`，`frontend/src/App.tsx` 的页面表加一项；新文件 `frontend/src/pages/AdminShippingPage.tsx` 用后台框架渲染（`AdminFrame` 的 `current` 为 `shipping`），手机顶栏标题为 `admin.nav_shipping`，内容区只有 `<h1 class="acs-admin__h">` `admin.shipping_title`，页面不发起自己的请求。`frontend/src/i18n/copy.ts` 按 UX-COPY 0.17 逐字加入 `admin.nav_shipping` 与 `admin.shipping_title`（每种语言一行）。本任务不装依赖、不改后端、不改设计。设计闸门：不适用（只加路由、导航与标题，不碰金额与个人资料）。
- [x] 导航变化：`frontend/src/components/AdminFrame.tsx` 的 `AdminNavItem`、`ADMIN_NAV` 与导航表在 `coupons` 之后加入 `shipping`（指向 `ADMIN_SHIPPING_PATH`，文字 `admin.nav_shipping`）；桌面导航与 ☰ 菜单依次为订单、退款、优惠券、运费与演示汇率、库存重置、店铺装修、站点设置七项，`admin.nav_products` 仍不出现。
- [x] 改动的现有测试（只改按顺序列出导航项或路由表的断言及其测试标题，其余断言不动；原因都是导航或路由表多了一项，旧的六项期望必然失效）：
  - `frontend/src/components/AdminFrame.test.tsx`：文件顶部 `LIVE_NAV_KEYS` 加 `admin.nav_shipping`，另加常量 `SHIPPING_PATH`；「lists only … in the navigation」一条的 `ADMIN_NAV` 期望、桌面导航 href 与 aria-current 的期望数组改为七项，加 `admin.nav_shipping` 的文字断言与优惠券 < 运费 < 库存重置的顺序断言，测试标题加入 shipping；「opens the menu …」一条的手机菜单 href/aria-current 期望加入 `/admin/shipping`，并加 `admin.nav_shipping` 的文字断言。`otherNavLabels` 随 `LIVE_NAV_KEYS` 不再包含运费一项。
  - `frontend/src/pages/AdminCouponsPage.test.tsx`：文件顶部 `NAV_PATHS` 在优惠券之后加 `/admin/shipping`；「marks coupons current …」的 aria-current 期望数组改为七项（当前项仍为优惠券）。
  - `frontend/src/pages/AdminRefundsPage.test.tsx`：「marks the refunds item as current」的 href/aria-current 期望与导航文字期望在优惠券之后加入运费一项（当前项仍为退款）。
  - `frontend/src/pages/AdminSiteSettingsPage.test.tsx`：文件顶部 `NAV_PATHS` 与 `NAV_KEYS` 在优惠券之后加运费一项；「marks site settings current …」的 aria-current 期望数组与按项数截取的 `slice(0, 6)` 改为七项（当前项仍为站点设置）。
  - `frontend/src/pages/AdminStockResetsPage.test.tsx`：「marks the stock resets item as current …」的 href/aria-current 期望与导航文字期望加入运费一项，`slice(0, 6)` 改为 `slice(0, 7)`（当前项仍为库存重置）。
  - `frontend/src/pages/AdminStoreDesignPage.test.tsx`：「marks the store design item as current …」同上（当前项仍为店铺装修）。
  - `frontend/src/router.test.tsx`：路由表断言在 `/admin/coupons` 之后加 `/admin/shipping`。
  - 各改动处的测试注释加了一行「SHOP-TASK-173 改动」说明；各文件的手机顶栏名称断言未改。
- [x] 新增测试 `frontend/src/pages/AdminShippingPage.test.tsx`（vitest 与 react-dom/server，fetch 以替身代替，路由用 `RouterProvider` 的 `initialPath`）：经路由打开 `/admin/shipping` 渲染后台框架、不套前台框架；桌面导航与 ☰ 菜单的七项顺序与运费项的 `aria-current="page"`；手机顶栏标题 `admin.nav_shipping`；已登录时内容区只有 h1 `admin.shipping_title`；页面不调用 fetch；页面文字全部来自字典。
- 偏离：视觉稿 A06-phone 的 h1 `font-size: 17px` 是 site.css 不能声明的字号，没有搬入；A06-desktop 内容区的两栏网格（`minmax(0, 1fr) 460px`）属于之后的表单内容，本任务内容区只有标题，未加；因此 `frontend/src/styles/site.css` 未改动。没有借用其他组件的类取颜色。UX 与视觉稿在页头与导航部分不冲突（视觉稿里的 `admin.nav_products` 属尚未上线页面，按 Kelvin 2026-10-06 的决定不出现）。
- 留给之后的任务：请求模块由 SHOP-TASK-174 实现；读取、显示与保存由 SHOP-TASK-175 实现；增删行由 SHOP-TASK-176 实现。未做浏览器验收。
- 验证到什么程度：人工逐条对照 UX「管理后台总体」的导航顺序、A06 线框的手机顶栏与标题、UX-COPY 0.17 的两条文案；上述测试为本次新增或修改的测试代码，前端检查由 PR 的必需 CI `frontend` 执行。

## 登记时的验收标准（原文）

2026-10-30 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.16 的「管理后台总体」与 A06、docs/UX-COPY.md 0.17 与 docs/HANDOFF.md 0.72 为准，样式以 docs/design/pages/ 的 A06-desktop 与 A06-phone 的页头与导航部分为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 路由 /admin/shipping——frontend/src/router.tsx 的路由表在 /admin/coupons 之后加一项并导出 ADMIN_SHIPPING_PATH，frontend/src/App.tsx 的页面表加一项；新文件 frontend/src/pages/AdminShippingPage.tsx 用后台框架渲染（AdminFrame 的 current 为新增的导航项），手机顶栏标题为 admin.nav_shipping，内容区只有 h1 admin.shipping_title（class 含视觉稿的 acs-admin__h）；frontend/src/i18n/copy.ts 按 UX-COPY 0.17 逐字加入 admin.nav_shipping 与 admin.shipping_title，写法与相邻条目相同（每种语言一行）；页面不发起自己的请求
      - frontend/src/components/AdminFrame.tsx 的导航项类型、ADMIN_NAV 与导航表在 coupons 之后加入 shipping（指向 ADMIN_SHIPPING_PATH，文字 admin.nav_shipping），桌面导航与 ☰ 菜单同样依次为订单、退款、优惠券、运费与演示汇率、库存重置、店铺装修、站点设置七项；其他尚未上线页面的导航项仍不出现
      - 现有测试只许改按顺序列出导航项或路由表的断言及其测试标题——frontend/src/components/AdminFrame.test.tsx 的 LIVE_NAV_KEYS、ADMIN_NAV 的期望与桌面导航、手机菜单「只含六项」的断言改为按 UX 顺序只含这七项、其他 admin.nav_* 不出现；frontend/src/pages/ 下 AdminCouponsPage.test.tsx、AdminRefundsPage.test.tsx、AdminSiteSettingsPage.test.tsx、AdminStockResetsPage.test.tsx 与 AdminStoreDesignPage.test.tsx 里按顺序列出导航项的断言（含其使用的文件顶部常量，如 NAV_PATHS、NAV_KEYS，以及按项数截取的 slice 与 aria-current 的期望数组）在优惠券之后加入 admin.nav_shipping（各自的当前项与手机顶栏名称不变）；frontend/src/router.test.tsx 的路由表断言加 /admin/shipping；七个文件的其余断言不动，并在任务记录里逐个写明改了哪条、为什么
      - 用 vitest 与 react-dom/server（不加测试依赖，路由与 fetch 以替身代替）在新文件 frontend/src/pages/AdminShippingPage.test.tsx 验证意图——经路由打开 /admin/shipping 渲染后台框架、不套前台框架；当前导航项为运费与演示汇率（aria-current 为 page）且七项顺序正确（桌面导航与手机菜单）；手机顶栏标题；h1；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-173.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；路由、导航变化、改动的现有测试、偏离、验证到什么程度；只写实际存在的测试，写明未做浏览器验收，请求模块与页面内容留给 SHOP-TASK-174 到 176）
      - 改动只落在 allowed_change_paths；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

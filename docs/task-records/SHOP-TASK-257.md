# SHOP-TASK-257 活动专题页 P15（路由 /sale）与页头的活动入口

- [x] 设计闸门：不适用。本任务只显示公开接口 `GET /api/promotion` 的活动数据与目录接口（`sale=true`）的商品数据，不计算金额，不碰个人资料，不改后端与设计。依据 UX 0.20 的 P15、「1. 全局框架」「节日促销活动」，UX-COPY 0.22，DESIGN 1.23「节日促销活动」第 10 条与 HANDOFF 0.97 的落实写法；样式以 `P15-desktop`、`P15-phone`、`P15-states` 为准。依赖 SHOP-TASK-255（`PromoStrip`、`saleLinkPath`）、SHOP-TASK-256（`PromoRuleList`）、SHOP-TASK-252（`ruleTexts`、`scopeText`）与 SHOP-TASK-262（目录的 `sale` 参数）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 路由：`frontend/src/router.tsx` 的 `ROUTE_PATHS` 在 `/products/:slug` 之后加 `/sale`，文件头注释补上该路径（不带段值、不用查询参数，页码只在页面状态里）；`frontend/src/App.tsx` 的页面表加 `"/sale": SalePage`；`frontend/src/router.test.tsx` 只改按顺序列出路由表的断言（加入 `/sale`）与它的测试名，其余断言未改。`PromoParts.tsx` 的 `saleLinkPath()` 随之返回 `/sale`，P01 海报、P02 横条与 P03 规则区的去 P15 链接自动出现（各自的测试本来就按 `saleLinkPath()` 的返回值断言）。
- [x] `frontend/src/pages/SalePage.tsx`：
  - 读取中（`usePromotion` 的 `status` 为 loading）主体只有 ★ `promo.demo_hint`，`main` 标 `aria-busy`。
  - 没有活动（含倒计时到 0 后重新读取的结果）：★ 提示之后 `div.acs-card.site-sale__ended`，内为 `promo.ended` 与去 `/products` 的 `promo.browse_all`。
  - 有活动：★ 提示；`div.site-sale__intro` 内为 `PromoStrip`（文字为活动标题，`viewAll` 为假）与副标题（为 null 或空串不显示）；`div.site-sale__rules` 内为 `section.acs-promo-rules`（`h2` 为 `promo.rules_title`，其后 `PromoRuleList`）；然后是商品区。
  - 规则说明 `saleRuleLines`：先按 `position` 列商品级规则（`item_discount`、`buy_x_get_y`）的 `ruleTexts`，说明后以「 — 」附 `scopeText` 的结果（没有分类时不附）；再按 `position` 列整单级规则的 `ruleTexts`。
  - 只有整单级规则时商品区换成 `div.acs-panel.site-sale__note`：`promo.order_level_only` 与去 `/products` 的 `promo.browse_all`。
  - 商品区 `section.site-sale__products`：`promo.products_title`、`list.results_count`（接口的总数）与 `ProductCard` 网格（沿用 P02 的 `site-list__grid`）。目录返回 0 件时为结果数与 `list.empty`，不放 `list.filter_clear`。请求为 `saleProductsUrl`：`productsUrl(language, { sort: "newest", page, sale: true })`，不带 `page_size`（同 P02 由接口决定）。
  - 页码只在页面状态里：读取状态放在不依赖 React 的 `createSaleList`（同 `promotion.tsx` 的写法，组件经 `useSyncExternalStore` 订阅），桌面为按钮式页码（样式、显隐与第一页、最后一页的不可用状态同 P02，类名 `site-list__pages`），手机为 `list.load_more`（`site-list__more`）。同一时刻只有一个读取，新的读取中止之前未完成的读取；卸载时中止未完成的读取。
  - 失败照 P02：首次读取与桌面翻页失败时商品区标题之后是 `ErrorNotice`；手机加载更多失败时保留已显示的商品，手机显示 `ErrorNotice`（`site-list__more-error`），可再按 `list.load_more`。
  - 商品区以 `saleListKey`（语言与 `promotion_id`）为 React key：活动换了、切换语言时换一个新的商品区，从第 1 页重新读取；变为没有活动时商品区不渲染，活动回来时同样是新的商品区。同一活动重新读取（generation 变化、ID 不变）时保持所在页。
  - `SalePage` 另有只供测试的可选属性 `fetcher`（目录读取的替身）与 `initial`（商品区一开始的状态，给出时挂载后不读取）；页面表里不传。
- [x] `frontend/src/components/SiteFrame.tsx`：`HEADER_NAV` 在 `common.nav_shop` 之后加 `/sale` 的 `promo.nav`（`promo: true`），`availableNav` 只在 `usePromotion` 有活动时放入；桌面导航与手机 ☰ 菜单共用新的 `NavLink`。活动入口为 `a.site-header__promo`，文字前是读屏忽略的 `span.acs-promo-motif`（P15-desktop）；其余导航项的标记与原来逐字相同。没有活动（含读取中）时页头与原来完全相同。
- [x] `frontend/src/i18n/copy.ts` 在 `promo.points_not_combinable` 之后加 `promo.nav`、`promo.demo_hint`、`promo.rules_title`、`promo.products_title`、`promo.order_level_only`、`promo.browse_all`、`promo.ended`，三语从 UX-COPY 0.22 逐字照抄。
- [x] `frontend/src/styles/site.css`（只写布局、显隐与媒体查询，固定间距与尺寸为像素值，没有颜色、字体与圆角）：页头活动入口 inline-flex、间距 6px、图案 16px；P15 的 ★ 提示外边距 24px、横条与副标题 grid 间距 12px（上 20px）、规则区上 32px、商品区 grid 间距 24px（56px 96px）、标题行 flex 两端对齐（基线）、提示框与结束框的间距；767px 以下照 P15-phone 收窄（★ 提示 16px，横条区 14px、间距 10px，规则区 20px，商品区 36px 56px、间距 16px，标题与结果数上下排列）。
- [x] 测试（`vitest` 与 `react-dom/server`；有活动的状态以 `PromotionProvider` 的 `initial` 传入，目录读取以替身代替）：
  - 新文件 `frontend/src/pages/SalePage.test.tsx`：三语下读取中只有 ★ 提示；没有活动与活动到期后的 `promo.ended`、`promo.browse_all`；有活动时各部分的顺序；横条为活动标题、带倒计时、没有 `promo.view_all`、ticket 变体；副标题为 null 或空串不显示；规则顺序（接口顺序打乱时按 position，商品级在前）与 `promo.rule_scope`、`promo.rule_scope_with_items`、不附范围三种情形；不同用说明；只有整单级规则时的 `promo.order_level_only`；目录返回 0 件；商品卡、结果数、角标与活动价；商品读取中；首次读取与翻页失败；页码为按钮、没有地址；第一页、最后一页与只有一页；加载更多的追加与失败；`/sale` 在前台框架里渲染。`createSaleList`：请求带 `sale=true` 与 `sort=newest`、不带 `page_size`、语言随页面；翻页与加载更多不碰地址栏与历史记录（以替身 `window` 断言）；首次读取、翻页与加载更多失败；停止与换页时中止未完成的读取；给出初始状态时不读取；`saleListKey` 随活动 ID 与语言变化、同一活动不变，新的商品区从第 1 页读取。
  - `frontend/src/components/SiteFrame.test.tsx` 追加「promotion entry」：三语下有活动时桌面导航与手机菜单的完整标记（`promo.nav` 在 `common.nav_shop` 之后、`common.nav_track` 之前）；在 `/sale` 时两处标为当前；没有活动、读取中与 App 的首次渲染时两处与原来逐字相同、两处导航没有去 `/sale` 的链接、页头没有 `promo.nav`（在 `/sale` 上语言切换链接本来就指向当前路径，所以 `/sale` 链接只在导航范围内检查）。
  - 现有断言除 `router.test.tsx` 的路由表外未改。每条新测试的注释写明它守住的 UX 0.20 P15 或「1. 全局框架」的原句（如 P15「排序与分页同 P02 的默认排序」、线框「(‹) 1 2 3 (›)」「( [list.load_more] )」，或「节日促销活动」「到 0 时页面重新读取活动数据」），HANDOFF 0.97 落实写法的原句并列其后。「给出初始状态时不读取」并入「第一页到达后显示它」的测试。
- 偏离与落实细节：
  - 没有活动时的框：视觉稿是 1px `--rule` 边框、`--radius-card` 圆角、内边距 40px 的框；site.css 不写边框颜色与圆角，这里用 `acs-card`（边框宽度取 `--border-w`，另有 `--surface-raised` 底色），内边距照视觉稿 40px（手机 24px，视觉稿没有手机版）。
  - 手机稿的副标题没有 `acs-body-l`、结果数另有 `acs-body-s`；同一份标记不能按宽度换字体类（site.css 不声明字体），这里桌面与手机都用桌面稿的类。
  - 商品卡沿用 P02 的 `site-list__grid`（桌面为 site.css 里 P02 的横排卡片），没有照视觉稿的竖排卡片：落实写法要求「商品卡网格……同 P02」。
  - 规则说明与范围之间的「 — 」取自 P15-desktop 的文字；`PromoRuleList` 只收字符串，说明与范围合成一行字符串交给它。
  - 规则区在说明行为空时照样渲染标题（落实写法没有为空的情形）。
  - 桌面翻页后把商品区的开头滚入视野（`scrollIntoView`），而不是像 P02 那样回到页面顶部：P15 的商品区在横条与规则区之下，回到顶部要再往下找。
  - 只有整单级规则时的提示框与规则区之间 20px 取自 P15-states；手机的提示框与结束框外边距（16px、56px）视觉稿没有给出，按手机商品区的间距取。
  - 页头视觉稿里登录在右侧与购物车同组；现有页头把登录放在导航里，本任务只在 `common.nav_shop` 之后加入口，没有改动其他项的位置。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 P15 与「1. 全局框架」的原句、UX-COPY 0.22 的七行与三张视觉稿的标记。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明不适用及理由（只显示公开接口的活动与目录数据，不计算金额，不碰个人资料）；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 路由——frontend/src/router.tsx 的路由表在 /products/:slug 之后加 /sale，文件头注释补上该路径；frontend/src/App.tsx 的页面表加 SalePage；frontend/src/router.test.tsx 只改按顺序列出路由表的断言与它的测试名，加入这一项，其余断言不动（运营者登记时在 main 的导出上做骨架实测：只加路由、页面表、只有 ★ 提示的空页面与框架的包裹元素，类型检查通过，全部测试中只有这一条失败）
      - 页面——frontend/src/pages/SalePage.tsx 照落实写法渲染三种状态（有活动、只有整单级规则、没有活动）与读取中；商品区随页码与加载更多读取，离开页面时中止未完成的读取
      - 页头——frontend/src/components/SiteFrame.tsx 的桌面导航与手机菜单在 common.nav_shop 之后加 promo.nav（链到 /sale），只在 usePromotion 有活动时显示；没有活动时页头与现在完全相同
      - 文案与样式——frontend/src/i18n/copy.ts 加 promo.nav、promo.demo_hint、promo.rules_title、promo.products_title、promo.order_level_only、promo.browse_all、promo.ended，三语与 docs/UX-COPY.md 0.22 逐字相同；以 docs/design/pages/P15-desktop、P15-phone、P15-states 为准，布局写在 frontend/src/styles/site.css（手机写在 767px 及以下的媒体查询里，只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性）；site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——新文件 frontend/src/pages/SalePage.test.tsx 用 vitest 与 react-dom/server（有活动的状态以 PromotionProvider 的 initial 传入，目录读取以替身代替）覆盖三种语言下三种状态与读取中、目录返回 0 件、规则顺序与两种范围文案、目录请求带 sale=true 与默认排序、翻页与加载更多不改地址、活动换了与切换语言回到第 1 页、商品读取失败、横条显示活动标题且不显示 view_all；frontend/src/components/SiteFrame.test.tsx 补充入口只在有活动时显示且排在 common.nav_shop 之后（桌面与手机菜单）；现有断言除路由表外不改；每条测试的注释写明它守住的 UX 0.20 P15 或「1. 全局框架」原句
      - docs/task-records/SHOP-TASK-257.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明不适用及理由），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 P15、「1. 全局框架」「节日促销活动」（页头导航在 common.nav_shop 之后加 promo.nav，手机 ☰ 菜单同位置）与 docs/DESIGN.md 1.23「节日促销活动」第 10 条，新建路由 /sale 与页面 frontend/src/pages/SalePage.tsx，并在页头与手机菜单加活动入口。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：活动内容只取 usePromotion 的数据，读取中只显示 ★ promo.demo_hint；没有活动（含倒计时到 0 后重新读取的结果）时显示 promo.ended 与 promo.browse_all（链到 /products）。有活动时依次为 ★ promo.demo_hint、SHOP-TASK-255 的 PromoStrip（不显示 view_all）、副标题（为空不显示）、promo.rules_title 与 SHOP-TASK-256 的 PromoRuleList（规则按 position：商品级规则的说明后附 SHOP-TASK-252 scopeText 的结果，再列整单级规则），然后是 promo.products_title、list.results_count 与商品卡网格（以 SHOP-TASK-262 的 sale 参数读取目录，默认排序与每页条数同 P02）；P02 的分页组件写死 /products 的地址，P15 不复用它：页码只放在页面状态里、不进地址（路由的查询参数只给商品列表用），桌面的分页控件为按钮，手机为 list.load_more，样式与显隐照 P02；商品级规则范围内没有上架商品（目录返回 0 件）时显示 list.results_count 与 list.empty（不显示 list.filter_clear）；只有整单级规则时商品区换成 promo.order_level_only 与 promo.browse_all。generation 变化且活动换了（promotion_id 不同或变为没有活动）时、以及切换语言时，回到第 1 页重新读取商品。商品读取失败时照 P02：首次读取与桌面翻页失败显示 ErrorNotice，手机加载更多失败照 P02 加载更多的失败状态。页头入口只在有活动时显示。
      设计闸门：不适用（只显示公开接口的活动与目录数据，不计算金额，不碰个人资料）。
      拆分：P15 页面一层（10 个允许路径：页面与测试、框架与测试、router.tsx 与 router.test.tsx（新路由必然改路由表断言）、App.tsx、copy.ts、site.css 与任务记录），依赖 SHOP-TASK-255（横条）与 256（规则列表）。
```

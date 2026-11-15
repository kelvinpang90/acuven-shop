# SHOP-TASK-255 首页 P01 的活动海报与商品列表 P02 的活动横条

- [x] 设计闸门：不适用。只显示公开接口 `GET /api/promotion` 给的活动文案、图片与倒计时，不计算金额，不碰个人资料（DESIGN 1.23「节日促销活动」第 10、12 条）。依据 UX 0.20 的 P01（活动海报）与 P02（活动横条）、UX-COPY 0.22、COMPONENTS「FestivalPromotion」的 Home poster 与 Sale strip、HANDOFF 0.97 的落实写法；样式以 `P01-promo-desktop`、`P01-promo-phone`、`P01-promo-posters`、`P02-promo-desktop`、`P02-promo-phone` 为准。依赖 SHOP-TASK-254（`PromoParts.tsx`）与 SHOP-TASK-253（`usePromotion`、`PromotionCountdown`、`PromotionProvider`）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `frontend/src/components/PromoParts.tsx`（P15 在 SHOP-TASK-257 复用）：
  - `saleLinkPath()`：`/sale` 在 `ROUTE_PATHS` 里时返回它，否则 null；本任务不加路由，所以目前为 null，SHOP-TASK-257 加入路由后按钮与链接自动出现。
  - `PromoCount()`：视觉稿的 `.acs-promo-count`（时钟图形与 `PromotionCountdown` 的文字）；没有活动或 `ends_at` 为 null 时不渲染。
  - `PromoStrip({ text, viewAll })`：`.acs-promo-strip.acs-promo-strip--band` 或 `--ticket`（按活动的 `list_strip`，其他取值按 band），内含 `aria-hidden` 的 `.acs-promo-motif`、`__title`（传入的文字）与 `__end`（`PromoCount` 与 `promo.view_all` 链接；`viewAll` 为 false 或 `saleLinkPath()` 为 null 时不放链接）；没有活动时不渲染。
- [x] `frontend/src/pages/HomePage.tsx`：`HomeView` 在 ★ `home.demo_hint` 之后、区块之前放活动海报，不属于任何区块、不经区块设置；内容只取 `usePromotion`，没有活动（含读取中）时不渲染。
  - 模板模式：`section.acs-promo-poster.acs-promo-poster--split` 或 `--centered`（按 `home_poster`，其他取值按 split），`aria-label` 为标题；`__text` 里依次为图案冠饰、标题、副标题（null 或空串时整行不显示）、`PromoCount` 与 `promo.shop_now` 按钮（`.acs-btn.acs-btn--lg.acs-btn--promo`，链到 `saleLinkPath()`，为 null 时不渲染）；`__art` 为空且 `aria-hidden`。
  - 上传图模式（`poster_mode` 为 `image` 且桌面图与手机图都有）：`picture` 里 `source media="(max-width: 767px)"` 给手机图、`img` 给桌面图，`alt` 为 `poster_alt`；整图为链到 `saleLinkPath()` 的 `a.acs-promo-poster-img`，为 null 时换成同类名的 `div`。缺任一张时按模板模式显示。
- [x] `frontend/src/pages/ProductListPage.tsx`：有活动时在标题行（标题与 ★ 提示）之后、侧栏与商品区之前放 `div.site-list__strip` 包着的 `PromoStrip`，文字为活动的 `strip`，为 null 或空串时用 `title`，放 `promo.view_all`；没有活动时不渲染该元素，其余输出不变，排序与筛选未改。
- [x] `frontend/src/i18n/copy.ts` 在 `promo.rule_scope` 之后加 `promo.shop_now`、`promo.view_all`，三语从 UX-COPY 0.22 逐字照抄。
- [x] `frontend/src/styles/site.css`（只写布局，没有颜色、字体与圆角，没有改 `acuven-shop.css`）：海报在 ★ 提示之后 16px（手机 12px）；上传图的 `picture` 以块显示并撑满框，`img` 才能按生成样式的 `object-fit: cover` 居中裁切（3:1 与手机 4:5 的框已在 `acuven-shop.css`）；横条外层左右为 `--gutter`、下方 32px（手机 16px），有横条时标题行下内边距改为 24px（手机 16px，与原值相同），没有横条时标题行照旧。
- [x] 测试（`vitest` 与 `react-dom/server`，有活动的状态以 `PromotionProvider` 的 `initial` 传入；去 P15 的链接一律按 `saleLinkPath()` 的返回值断言，SHOP-TASK-257 加路由后不必改）：
  - `PromoParts.test.tsx` 追加：`saleLinkPath` 与路由表一致；三语 × band/ticket 的横条完整标记；`viewAll` 为 false 时没有链接；没有活动不渲染；`ends_at` 为 null 时不放倒计时框。
  - `HomePage.test.tsx` 追加：三语 × split/centered 的模板海报完整标记；副标题为 null 与空串时没有该行；三语上传图的 `picture`、两张图、替代文字与外层元素；缺桌面图、缺手机图、两张都缺时退回模板；四种区块设置（默认、倒序、只显示精选、全部隐藏）下海报紧接 ★ 提示、在所有显示的区块之前；没有活动与读取中时与不带活动的渲染逐字相同。
  - `ProductListPage.test.tsx` 追加：三语下横条的文字、倒计时、`promo.view_all` 与位置（★ 提示之后、`site-list__body` 紧接其后）；band 与 ticket；`strip` 为 null 与空串时用标题；默认与「价格从低到高 + 分类」条件下，有活动时去掉横条后与没有活动时逐字相同；没有活动与读取中时与不带活动的渲染逐字相同。
  - 现有断言未改。每条测试的注释写明它守住的 UX 0.20 原句。
- 偏离与落实细节：
  - 视觉稿的海报标题是 `h1`；首页主视觉已有 `h1`（`home.hero_title`），为保持一页一个 `h1`，海报标题用 `h2`，类名照视觉稿 `acs-display-l acs-promo-poster__title`，外观不变。
  - 手机视觉稿的海报标题为 `acs-display-m`，这里桌面与手机都用 `acs-display-l`（生成样式在 767px 以下已把它缩到 32px）；改字号要在 site.css 声明字体属性，按约定不写，记为偏离。
  - 视觉稿 P02 手机的横条在标题行的网格里（与 ★ 提示间距 14px）；这里横条是标题行之后的独立元素，间距为 16px（标题行手机下内边距的原值），差 2px。
  - 有横条时调整标题行下内边距用了 `:has(+ .site-list__strip)`，只是选择器，没有横条时规则不生效。
  - 活动标题为 null（接口英文也空时）时海报不放标题行、`section` 不设 `aria-label`；`poster_alt` 为 null 时 `alt` 为空串。
  - 没有改 `router.tsx` 与 `App.tsx`（不需要；`PromotionProvider` 已在 `App.tsx`，本任务不新增路由）。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 P01 与 P02 的原句、UX-COPY 0.22 的两行与五张视觉稿的标记。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明不适用及理由；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 首页——有活动时海报显示在 ★ 提示之后、所有区块之前，不属于任何区块、不受 A08 区块设置影响；没有活动或读取中时不占位；模板模式与上传图模式照落实写法，上传图缺任一张时按模板模式显示
      - 列表——frontend/src/pages/ProductListPage.tsx 有活动时显示横条（位置照视觉稿 P02-promo-desktop、P02-promo-phone），没有活动时与现在完全相同；「价格」排序与筛选不变
      - 组件——frontend/src/components/PromoParts.tsx 加 saleLinkPath 与 PromoStrip（.acs-promo-strip--band 或 --ticket，含 motif、__title 与 __end）；frontend/src/i18n/copy.ts 加 promo.shop_now 与 promo.view_all，三语与 docs/UX-COPY.md 0.22 逐字相同
      - 样式——以 docs/design/pages/P01-promo-desktop、P01-promo-phone、P01-promo-posters、P02-promo-desktop、P02-promo-phone 为准，布局写在 frontend/src/styles/site.css（手机写在 767px 及以下的媒体查询里，只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性；不改生成的 acuven-shop.css）；site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/pages/HomePage.test.tsx、frontend/src/pages/ProductListPage.test.tsx 与 frontend/src/components/PromoParts.test.tsx 用 vitest 与 react-dom/server（有活动的状态以 PromotionProvider 的 initial 传入）覆盖三种语言下两种海报版式、副标题为空、上传图模式的两张图与替代文字、缺图退回模板、海报在区块之前且不受区块设置影响、横条两种变体与 strip 为空时用标题、没有活动时不渲染任何活动内容、链接的有无按 saleLinkPath() 断言；现有断言不改；每条测试的注释写明它守住的 UX 0.20 原句
      - docs/task-records/SHOP-TASK-255.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 App.tsx 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明不适用及理由），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 P01（活动海报）与 P02（活动横条）、docs/DESIGN.md 1.23「节日促销活动」第 10、12 条与 docs/design/COMPONENTS.md「FestivalPromotion」的 Home poster 与 Sale strip，在首页 ★ 提示之后、所有区块之前显示活动海报，在商品列表显示活动横条；横条做成可复用的组件放进 frontend/src/components/PromoParts.tsx（P15 在 SHOP-TASK-257 复用）。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：活动内容只取 usePromotion 的数据。海报在模板模式按 home_poster（split 或 centered）排版，标题、副标题（为空时整行不显示）、PromotionCountdown 与 promo.shop_now 按钮（链到 /sale）。P15 路由在 SHOP-TASK-257 才加入路由表，所以 PromoParts.tsx 导出 saleLinkPath()：/sale 在 ROUTE_PATHS 里时返回它，否则返回 null；为 null 时按钮不渲染、整图不做成链接（a.acs-promo-poster-img 换成同类名的 div），257 合并后自动出现；本任务与 256 的测试按 saleLinkPath() 的返回值断言有无链接，不写死，257 加路由后不必改这些测试。上传图模式用 picture 元素，桌面图与手机图以 767px 的媒体条件切换（桌面 3:1、手机 4:5 的框居中裁切，object-fit 为 cover），alt 为 poster_alt。横条按 list_strip（band 或 ticket），文字为 strip、为空时用 title，末尾为倒计时与 promo.view_all（saleLinkPath() 为 null 时不渲染）；横条组件以参数接收显示的文字与是否显示 view_all（P15 传活动标题、不显示 view_all）。
      设计闸门：不适用（只显示公开接口的活动文案、图片与倒计时，不计算金额，不碰个人资料）。
      拆分：P01 与 P02 的活动内容一层（11 个允许路径：两个页面与组件各带测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-254。
```

# SHOP-TASK-254 商品卡的活动价与角标（P01 精选、P02、P15 共用）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 10 条：只显示目录接口给的活动价，不计算金额，不碰个人资料。依据 UX 0.20「1. 全局框架」「节日促销活动」「商品卡价格与角标」、UX-COPY 0.22 第 11 节与 COMPONENTS「FestivalPromotion」的 Badges 与 Prices；样式以 `P02-promo-desktop`、`P02-promo-phone` 为准。依赖 SHOP-TASK-262（目录的 `promotion` 与 `min_sale_price_sen`）与 SHOP-TASK-253（`usePromotion`、`PromotionProvider`）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `frontend/src/components/PromoParts.tsx`（P03、P04 之后复用）：
  - `PromoBadges({ phrase, rule, style })`：`.acs-promo-badges` 里先放角标短语、再放规则角标，为 null 或空串的不放，两者都空时不渲染；每个角标为 `.acs-promo-badge.acs-promo-badge--tag`，`style` 为 `seal` 时为 `.acs-promo-badge--seal` 并以 `aria-hidden` 的 `.acs-promo-motif` 开头。
  - `PromoPrices({ original, sale })`：`.acs-prices` 里 `.acs-price-was` 在前、`.acs-price-sale` 在后，各以 `.acs-vh` 的 `promo.a11y_original`、`promo.a11y_sale` 开头；价格文字由调用方套好模板传入。
  - `shownRule(promotion, rule)`：只有正在显示的活动的 `rules` 里有该 `rule_id` 时返回卡片规则，否则 null（没有活动、倒计时到 0 后目录的旧数据都不再显示活动）。
- [x] `frontend/src/components/ProductCard.tsx`：经 `usePromotion` 与 `shownRule` 判定；命中打折规则（`item_discount`）且 `min_sale_price_sen` 不为空时，价格处以 `PromoPrices` 取代 `.acs-pcard__price`（不嵌套）：划线原价为 `min_price_sen` 套 `common.price_myr`、不加「起」，活动价为 `min_sale_price_sen` 照原有 `priceCopy` 规则（多于一个启用规格用 `list.price_from`）；图片角在图片之前放 `PromoBadges`（短语为活动的 `badge`，规则角标为 SHOP-TASK-252 的 `badgeText`，样式为 `card_badge`）。命中买 X 享 Y 时只加角标、价格照旧。售罄标签照旧放在价格之后。没有命中、没有活动或 `rule_id` 不在当前活动里时输出与原来逐字相同。
- [x] `frontend/src/i18n/copy.ts` 在 `promo.badge_percent` 之前加 `promo.a11y_original`、`promo.a11y_sale`，三语从 UX-COPY 第 11 节逐字照抄，由现有 `copy.test.ts` 对照文档。
- [x] `frontend/src/styles/site.css`：角标框的定位（`.acs-pcard__img` 的 `position: relative`、`.acs-promo-badges` 贴左上 10px）、角标与价格的样式已在生成的 `acuven-shop.css` 里，只加一条布局：角标里的文字 `min-width: 0; overflow-wrap: anywhere`，长短语在窄图块里换行。没有声明颜色、字体与圆角，没有改 `acuven-shop.css`。
- [x] 测试：新增 `frontend/src/components/PromoParts.test.tsx`（短语在前、短语为 null 与空串、tag 与 seal、两者都空不渲染、三语读屏标签与价格顺序、`shownRule` 的保留与丢弃）；`frontend/src/components/ProductCard.test.tsx` 末尾追加两组（以 `PromotionProvider` 的 `initial` 传入活动，`renderToStaticMarkup` 渲染）：三语下百分比与每件直降的角标与价格、多规格的「起」只加在活动价上、买 X 享 Y（100% 与 50%）只有角标价格照旧、短语为空与不为空、seal、售罄仍显示活动价与 `list.out_of_stock`；没有活动、`rule_id` 不在当前活动里、活动已到期与商品没有命中时，输出与不带活动的原渲染逐字相同。现有断言未改。每条测试的注释写明它守住的 UX 0.20 原句。
- 偏离与落实细节：
  - 视觉稿里角标短语的文字直接放在角标里、规则文字包一层 `<span>`；这里两者都包一层 `<span>`，以便上面那条换行规则对两种角标都生效，外观不变。
  - 视觉稿的 P02 桌面卡片为竖排三列；站点桌面卡片沿用之前任务的横排布局（图片占左半），角标框照样在图块左上角，没有为活动另改卡片布局。
  - 售罄卡片的图块按 `acuven-shop.css` 的 `.acs-pcard--oos .acs-pcard__img` 半透明，角标在图块里随之变淡；视觉稿没有售罄加活动的样张，site.css 不另行覆盖。
  - 命中打折规则但 `min_sale_price_sen` 为 null（接口不应出现）时只显示角标、价格照旧，不自行计算活动价。
  - 没有改 `router.tsx` 与 `App.tsx`（不需要）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 10 条、UX 0.20 的相关段落、UX-COPY 第 11 节与 `P02-promo-desktop`、`P02-promo-phone` 的标记；测试的期望价格按文档规则手算（3900 仙 → `RM 39.00`，3120 仙 → `RM 31.20`，500 仙 → `RM 5.00`）。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 组件——frontend/src/components/PromoParts.tsx 导出 PromoBadges（.acs-promo-badges 里依次放角标短语与规则角标，各为 .acs-promo-badge--tag 或 --seal）与 PromoPrices（.acs-prices 里 .acs-price-was 在前、.acs-price-sale 在后，各以 .acs-vh 的 promo.a11y_original、promo.a11y_sale 开头）；frontend/src/i18n/copy.ts 加这两个键，三语与 docs/UX-COPY.md 0.22 逐字相同
      - 商品卡——命中打折规则时价格处以 PromoPrices 取代现有的 .acs-pcard__price 元素（不嵌套），图片角显示 PromoBadges；命中买 X 享 Y 时只显示角标、价格照旧；没有命中、没有活动或 rule_id 不在当前活动里时与现在完全相同；售罄商品照样显示活动价与 list.out_of_stock
      - 样式——角标与价格的布局写在 frontend/src/styles/site.css（只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性；不改生成的 acuven-shop.css），样式以 docs/design/pages/P02-promo-desktop、P02-promo-phone 为准；site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——新文件 frontend/src/components/PromoParts.test.tsx 与 frontend/src/components/ProductCard.test.tsx 用 vitest 与 react-dom/server（以 renderToStaticMarkup 渲染，有活动的状态以 SHOP-TASK-253 的 PromotionProvider 的 initial 传入）覆盖三种语言下百分比、每件直降、买 X 享 Y 的角标与价格，多规格的「起」只加在活动价上，角标短语为空与不为空，tag 与 seal，售罄，rule_id 不在当前活动里与没有活动时不显示；现有断言不改；每条测试的注释写明它守住的 UX 0.20 原句
      - docs/task-records/SHOP-TASK-254.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 App.tsx 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20「1. 全局框架」「商品卡价格与角标」、docs/DESIGN.md 1.23「节日促销活动」第 10 条与 docs/design/COMPONENTS.md「FestivalPromotion」的 Badges 与 Prices，让 frontend/src/components/ProductCard.tsx 在商品命中商品级规则时显示活动价与角标，并把角标与划线价做成可复用的组件放进新文件 frontend/src/components/PromoParts.tsx（P03 与 P04 之后复用）。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：目录模块的活动字段由 SHOP-TASK-262 加好，本任务只用。卡片只在 usePromotion 有活动、且卡片规则的 rule_id 在该活动的 rules 里时显示活动内容（倒计时到 0 后旧数据不再显示活动）。划线原价用 min_price_sen（打折规则下活动单价随原价单调不减，活动价最低且原价最低的 SKU 就是原价最低的 SKU），按 common.price_myr、不加「起」；活动价用 min_sale_price_sen，多于一个启用规格时用 list.price_from、否则 common.price_myr。角标先放活动的角标短语（PromotionDisplay 的 badge，为空不放），再放 SHOP-TASK-252 badgeText 的文字，样式按 card_badge（tag 或 seal，seal 以 .acs-promo-motif 开头）。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 10 条：只显示目录接口给的活动价，不计算金额，不碰个人资料。
      拆分：商品卡一层（9 个允许路径：商品卡与新组件各带测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-262（目录字段）与 253（共享读取）。
```

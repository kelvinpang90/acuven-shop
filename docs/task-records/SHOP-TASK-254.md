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

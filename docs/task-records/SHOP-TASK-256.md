# SHOP-TASK-256 商品详情 P03 的价格飘带与活动规则区

- [x] 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 3、10 条。只显示目录接口给的价格（`price_sen`、`sale_price_sen`）与公开接口 `GET /api/promotion` 的规则参数，不计算金额，不碰个人资料。依据 UX 0.20 的 P03（活动）、UX-COPY 0.22、COMPONENTS「FestivalPromotion」的 Price ribbon 与 HANDOFF 0.97 的落实写法；样式以 `P03-promo-desktop`、`P03-promo-phone` 为准。依赖 SHOP-TASK-255（`promo.view_all`、`saleLinkPath`、`PromoCount`）、SHOP-TASK-254（`PromoBadges`、`shownRule`）与 SHOP-TASK-252（`ruleTexts`、`badgeText`）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `frontend/src/pages/productOptions.ts`（纯函数，`detailPriceCopy` 未改）：
  - `detailSalePriceCopy(product, variant, rule)`：只在 rule 为打折规则时给出 `{ original, sale }`；选全后为所选 SKU 的 `price_sen` 与 `sale_price_sen`，都按 `common.price_myr`；选全前划线价为启用 SKU 的最低单价（`common.price_myr`，不加「起」），活动价为启用 SKU 中 `sale_price_sen` 的最小值，多于一个启用 SKU 时 `list.price_from`。买 X 享 Y、没有命中或接口没给活动单价时为 null。
  - `detailRuleLines(language, promotion, rule)`：先列本商品命中、且在当前活动 `rules` 里的商品级规则的 `ruleTexts`，再按 `position` 列满额（`spend_discount`）与免运费（`free_shipping`）的 `ruleTexts`（不附范围）；没有活动时为空列表。
- [x] `frontend/src/components/PromoParts.tsx`：`PromoRuleList({ lines, couponCombinable, pointsCombinable })` 渲染说明行的 `ul`，其后在两个开关为假时分别放 `p.acs-body-s` 的 `promo.coupon_not_combinable`、`promo.points_not_combinable`；标题、倒计时与链接由调用方放（P15 在 SHOP-TASK-257 复用）。
- [x] `frontend/src/pages/ProductDetailPage.tsx`（`ProductDetailView`）：
  - 价格飘带：`usePromotion` 有活动且 `shownRule` 给出命中规则时，价格区换成 `div.acs-promo-ribbon.acs-promo-ribbon--ribbon` 或 `--panel`（按 `detail_ribbon`，其他取值按 ribbon）；`__prices` 为 `.acs-price-was.acs-price--l` 与 `.acs-price-sale.acs-promo-ribbon__sale`（各以 `promo.a11y_original`、`promo.a11y_sale` 读屏标签开头），买 X 享 Y 时为现有的 `.acs-price.acs-price--l` 价格文字；`__meta` 为 `PromoBadges`（角标短语与规则角标，都为空时不放 `__meta`）。价格随所选 SKU 的接口数据刷新。其余情况价格区与原来相同。
  - 活动规则区：★ `detail.demo_hint` 之后放 `section.acs-promo-rules`：`h2` 为 `promo.detail_title`，活动标题不为空时其后加图形分隔点与标题；`PromoRuleList`；末行 `div.site-detail__promo-end` 为 `PromoCount` 与 `promo.view_all`（`saleLinkPath()` 为 null 时不放；两者都没有时不放末行）。说明行为空（两类规则都没有）时整区不渲染。
- [x] `frontend/src/i18n/copy.ts` 在 `promo.view_all` 之后加 `promo.detail_title`、`promo.coupon_not_combinable`、`promo.points_not_combinable`，三语从 UX-COPY 0.22 逐字照抄。
- [x] `frontend/src/styles/site.css`（只写布局，没有颜色、字体与圆角，没有改 `acuven-shop.css`）：飘带 `__meta` 里的 `.acs-promo-badges` 改为 `display: contents`（生成样式里它为商品卡图块绝对定位），角标随 `__meta` 的 flex 排列；规则区标题行 flex、间距 4px 8px；末行 flex 换行、间距 10px 16px（取自视觉稿）。
- [x] 测试（`vitest` 与 `react-dom/server`；有活动的状态以 `PromotionProvider` 的 `initial` 传入，去 P15 的链接一律按 `saleLinkPath()` 的返回值断言）：
  - `productOptions.test.ts` 追加：三语下选全后所选 SKU 的原价与活动价、选全前的最低价与「起」；单规格不加「起」；买 X 享 Y、没有命中、没有活动单价时为 null；`detailPriceCopy` 照旧；规则说明行的顺序（本商品规则在前，满额与免运费按 position，接口顺序打乱时也按 position）、买 X 享 Y 在前、只有整单级规则、规则不在当前活动里、没有活动与两类都没有时为空。
  - `PromoParts.test.tsx` 追加：`PromoRuleList` 的 `ul` 与三语下两个不同用说明的各种组合。
  - `ProductDetailPage.test.tsx` 追加：三语下选全前后的飘带完整标记（多规格的「起」与单规格）；ribbon、panel 与其他取值；买 X 享 Y 只显示角标、价格不划线；角标短语为空；没有活动、没有命中、规则不在当前活动里时整页与不带活动的渲染逐字相同；只有整单级规则时价格区照旧；三语下规则区的完整标记与位置（★ 提示之后、描述之前）；两个不同用说明分别出现；只有整单级规则的商品也显示规则区；只有本商品规则；两类都没有时不显示；活动标题为空与不计时。`render` 辅助函数加了可选的 `promotion` 参数，不给时与原来相同。
  - 现有断言未改。每条测试的注释写明它守住的 UX 0.20 P03（或其引用的「1. 全局框架」）原句。
- 偏离与落实细节：
  - 视觉稿的划线原价行内写了 `font-size: 20px`，规则区标题行内写了 `font-size: 20px`、活动标题写了字体族、字号与字重；按约定 site.css 不声明字体属性，这里划线价照 `.acs-price--l`（24px）、标题照 `.acs-display-s`、活动标题随标题样式，记为偏离。
  - 视觉稿标题与活动标题之间是文字「·」；本页文字都来自字典（SHOP-TASK-017），这里与面包屑的分隔符同样用读屏忽略的图形分隔点。
  - 视觉稿里角标短语的 `span` 内没有再包一层 `span`；这里复用 `PromoBadges`，结构与商品卡相同。
  - 手机稿与桌面稿的飘带与规则区版式相同（都在信息列里，间距随 `.site-detail__info` 的手机间距 18px），767px 以下的媒体查询没有新增规则。
  - 选全前的划线价按落实写法取启用 SKU 的最低单价，与商品卡取 `min_price_sen` 同理。
  - 没有改 `router.tsx` 与 `App.tsx`（不需要；`PromotionProvider` 已在 `App.tsx`，本任务不新增路由）。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 P03 的原句、UX-COPY 0.22 的三行与两张视觉稿的标记。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23（提交 b7f9355）；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 价格飘带——frontend/src/pages/ProductDetailPage.tsx 照落实写法显示，切换规格时随服务端数据刷新；没有活动、没有命中或 rule_id 不在当前活动里时价格区与现在完全相同
      - 规则区——照落实写法的内容与顺序显示；规则列表组件 PromoRuleList 放在 frontend/src/components/PromoParts.tsx，接收说明行与两个同用开关，渲染 ul 与不同用说明；frontend/src/i18n/copy.ts 加 promo.detail_title、promo.coupon_not_combinable、promo.points_not_combinable，三语与 docs/UX-COPY.md 0.22 逐字相同
      - 纯函数——frontend/src/pages/productOptions.ts 导出选全前后的划线价与活动价文案、以及本商品规则区说明行的函数，现有 detailPriceCopy 不变
      - 样式——以 docs/design/pages/P03-promo-desktop、P03-promo-phone 为准，布局写在 frontend/src/styles/site.css（手机写在 767px 及以下的媒体查询里，只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性；不改生成的 acuven-shop.css）；site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/pages/productOptions.test.ts、frontend/src/pages/ProductDetailPage.test.tsx 与 frontend/src/components/PromoParts.test.tsx 覆盖三种语言下选全前后的价格（单规格与多规格的「起」）、买 X 享 Y 只显示角标、规则区的顺序（本商品规则在前、满额各档与免运费在后）、只有整单级规则的商品也显示规则区、两类都没有时不显示、两个不同用说明、ribbon 与 panel、没有活动时与现在相同；现有断言不改；每条测试的注释写明它守住的 UX 0.20 P03 原句
      - docs/task-records/SHOP-TASK-256.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 App.tsx 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 P03（活动）、docs/DESIGN.md 1.23「节日促销活动」第 3、10 条与 docs/design/COMPONENTS.md「FestivalPromotion」的 Price ribbon，在商品详情显示所选规格的划线原价、活动单价与角标，以及其下的活动规则区；规则列表做成可复用的组件放进 frontend/src/components/PromoParts.tsx（P15 在 SHOP-TASK-257 复用）。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：活动内容只在 usePromotion 有活动、且商品的 promotion.rule_id 在该活动的 rules 里时显示（规则区的整单级部分只要有活动就显示）。价格飘带按 detail_ribbon（ribbon 或 panel）：命中打折规则且选全规格时划线价为所选 SKU 的 price_sen、活动价为它的 sale_price_sen（都按 common.price_myr）；选全之前划线价为启用 SKU 的最低单价（不加「起」），活动价为启用 SKU 中 sale_price_sen 的最小值，多于一个启用 SKU 时加「起」（list.price_from），与商品卡同理；命中买 X 享 Y 时同样以 detail_ribbon 包裹，__prices 里是现有的价格文字（不划线），__meta 里是角标；角标复用 SHOP-TASK-254 的 PromoBadges。活动规则区 .acs-promo-rules 在 ★ 提示之后：标题为 promo.detail_title 加活动标题，先列本商品命中规则的说明，再按 position 列满额与免运费的说明（SHOP-TASK-252 的 ruleTexts，不附范围），coupon_combinable、points_combinable 为假时分别显示 promo.coupon_not_combinable、promo.points_not_combinable，最后是 PromotionCountdown 与 promo.view_all（SHOP-TASK-255 的 saleLinkPath() 为 null 时不渲染，测试按它的返回值断言）；两类规则都没有时整区不显示。价格与规则的选用写成导出的纯函数，放在 frontend/src/pages/productOptions.ts。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 3、10 条：只显示目录接口给的价格与公开接口的规则参数，不计算金额，不碰个人资料。
      拆分：P03 的活动内容一层（11 个允许路径：页面、价格函数与组件各带测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-255（promo.view_all、saleLinkPath，同改 PromoParts.tsx）。
```

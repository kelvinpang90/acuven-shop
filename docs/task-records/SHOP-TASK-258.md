# SHOP-TASK-258 购物车 P04 的活动优惠、凑单提示与进度条

- [x] 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 4、5、10 条。购物车的活动金额是计价接口（SHOP-TASK-234）的暂算结果，页面只做显示所需的减法与进度比例，不判定是否参加活动，不碰个人资料。依据 UX 0.20 的 P04（活动、M1、M8、M9）、UX-COPY 0.22、COMPONENTS「FestivalPromotion」的 Cart progress 与 HANDOFF 0.97 的落实写法；样式以 `P04-promo-desktop`、`P04-promo-phone` 为准。依赖 SHOP-TASK-263（计价类型 `PromotionQuote`）与 SHOP-TASK-254（`PromoBadges`、`PromoPrices`）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `frontend/src/pages/CartPage.tsx`：
  - 纯函数：`promoDiscount(quote)` 为 `item_discount_sen + order_discount_sen`（没有活动为 0）；`matchedPromotion(display, quote)` 在共享读取的活动与计价结果 `promotion_id` 相同时给出该活动，否则 null；`cartHints(language, quote, display)` 依次给出满额下一档（`spend_next_gap_sen` 不为空时按 `spend_mode` 用 `cart.hint_tier_amount`（`save` 为 `spend_next_value` 的金额）或 `cart.hint_tier_percent`）、免运费（门槛大于 0 时未达 `cart.hint_free_shipping`、已达 `cart.free_shipping_reached`）与买 X 享 Y（`units_in_scope` 大于 0 且共享读取里有同 `rule_id` 规则时 `cart.hint_bxgy`，`rule` 为该规则的 `badgeText`）；`cartProgress(quote)` 按落实写法取满额下一档与未达免运费中差额较小的一个（相等取满额），百分比 floor((门槛 − 差额) × 100 ÷ 门槛)，没有候选而有满额规则或门槛大于 0 的免运费规则时 100% 不带标签，都没有时为 null。
  - 摘要：桌面 `cart.subtotal` 之后依次为 `cart.promo_discount`、`cart.after_promo`（活动优惠大于 0 时），其后进度条与提示一组（`div.site-cart__promo`），再后会员说明，再后原有的运费与价格说明。手机在各行与提示条之后、`cart.shipping_later` 之前另有一块 `acs-summary site-phone-only site-cart__promo-phone`（进度条、提示、两行金额、会员说明；都没有时不渲染）；底部固定栏在活动优惠大于 0 时改为 `cart.after_promo` 与活动后金额。
  - 行：`promotion_item_discount_sen` 大于 0 时行小计处为 `PromoPrices`（划线行小计与行小计减该行优惠）；`promotion_rule_id` 不为空、共享读取的活动与计价结果一致且其中有同 `rule_id` 规则时，名称与规格之下为 `PromoBadges`（活动角标短语、规则角标、`card_badge` 样式）。
  - 进度条：`cart_progress` 为 `steps` 时 `__steps` 十格、点亮 floor(百分比 ÷ 10) 格（`data-on`），其他取值为 `__track` + `__fill`（`--progress`）；`__labels`（aria-hidden）为已达金额与门槛。进度条与行角标只在共享读取与计价结果一致时显示；金额行、划线价与文字提示照计价结果显示。
  - 会员说明：`useMemberSession` 为 member 且活动优惠大于 0 时显示 `cart.member_scheme_note`（`p.acs-body-s`）。
  - 重新计价：`quoteKey` 增加可选的 `generation`（默认 0）；`CartPage` 用 `usePromotion` 的 generation 组成请求标识，generation 变化（读取完成、倒计时到 0）即重新计价，新结果返回前沿用现有写法显示上一份结果（pending）。
- [x] `frontend/src/i18n/copy.ts` 在 `cart.demo_hint` 之后加 `cart.promo_discount`、`cart.after_promo`、`cart.hint_tier_amount`、`cart.hint_tier_percent`、`cart.hint_free_shipping`、`cart.free_shipping_reached`、`cart.hint_bxgy`、`cart.member_scheme_note`，三语从 UX-COPY 0.22 逐字照抄。
- [x] `frontend/src/styles/site.css`（只写布局，没有颜色、字体与圆角，没有改 `acuven-shop.css`）：行内 `.acs-promo-badges` 改为 `position: static`（生成样式里它为商品卡图块绝对定位）并上移 2px 间距（信息列 4px + 2px 合视觉稿的 6px）；打折行的两个行小计改为靠右的上下排列（桌面间距 2px，手机 0）；桌面进度条与提示一组上下留 14px、组内 10px；手机块在 767px 及以下的媒体查询里显示为 grid、内边距 18px。
- [x] 测试 `frontend/src/pages/CartPage.test.tsx`（`vitest` 与 `react-dom/server`；计价结果以替身给出，有活动的状态以 `PromotionProvider` 的 `initial` 传入、读取中以不给 `initial` 且读取不返回表示，会员状态以 `MemberSessionProvider` 的替身读取对象给出）：
  - 三语下两行金额的顺序与金额、手机块与底部固定栏；为 0 时两行不显示、底栏为商品小计；计价结果没有活动、共享读取没有活动、金额为 0 且没有门槛时与不包共享读取的渲染逐字相同。
  - 三语下打折行的划线行小计、活动价行小计与角标位置；买 X 享 Y 行只有角标、不划线；规则不在共享读取里时不显示角标。
  - 三语下满额减与满额折的下一档提示、已到最高档时不显示；免运费未达、已达与门槛 0；买 X 享 Y 提示只给范围内已有商品且共享读取里有的规则，不包共享读取时不显示。
  - 进度条 bar 与 steps 两种变体的百分比与格数向下取整、标签；取较近门槛与差额相等取满额；全部达到时满格不带标签；两类规则都没有（或免运费门槛为 0）时不画；读取中、没有活动、不包共享读取与 `promotion_id` 不一致时不画进度条、不显示行角标，金额行与提示照显示。
  - 三语下会员说明只给会员（游客、尚未读完、读取失败与活动优惠为 0 时不显示），位置在提示之后、运费说明之前。
  - generation 变化时请求标识变化、新结果返回前显示上一份结果（aria-busy），新结果没有活动时去掉全部活动内容。
  - 三语下有活动时页面文字仍只有字典文案、商品数据与活动的角标短语；`CART_KEYS` 加入 8 个新键。`render` 辅助函数加了可选的 `promotion`、`session` 参数，不给时与原来相同；现有断言未改。每条新测试的注释写明它守住的 UX 0.20 P04 原句。
- 偏离与落实细节：
  - 视觉稿的活动优惠金额写作「−RM 48.40」；字典里没有带负号的金额模板，本页文字都来自字典（SHOP-TASK-017），这里与结账页的优惠券、积分两行同样按 `common.price_myr` 显示、不加负号。
  - 视觉稿的商品小计行写了 `font-size: 18px`，活动后金额行（桌面与手机底栏）写了 `font-weight: 700`；按约定 site.css 不声明字体属性，这里照 `.acs-row` 的默认样式，记为偏离。
  - 视觉稿桌面的进度条与提示一组上下各有一条分隔线（`border-top`/`border-bottom: 1px solid var(--rule)`）；分隔线要声明颜色，site.css 写不了，这里只保留上下 14px 的间距，记为偏离。
  - 视觉稿手机块左右各有 16px 外边距；本页手机主体已按 `--gutter` 留边，块与各行同宽，没有另加外边距。
  - 进度条的 `--progress` 是行内的 CSS 自定义属性（COMPONENTS 规定的写法），只在画 bar 变体时出现；没有活动的页面仍没有行内样式。
  - 进度条与标签都对读屏隐藏，其意思由同一组的凑单提示文字说明。
  - 视觉稿里角标短语的 `span` 内没有再包一层 `span`；这里复用 `PromoBadges`，结构与商品卡、P03 相同。
  - 共享读取在页面打开时从读取中变为已读取，generation 加一，购物车因此会再计价一次；这是落实写法「generation 变化时重新计价」的直接结果。
  - 没有改 `router.tsx` 与 `App.tsx`（不需要；`PromotionProvider` 与 `MemberSessionProvider` 已在 `App.tsx`，本任务不新增路由）。
- 验证到什么程度：人工逐条对照验收标准、UX 0.20 P04 的原句、UX-COPY 0.22 的八行与两张视觉稿的标记。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23（提交 b7f9355）；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 摘要——frontend/src/pages/CartPage.tsx 的桌面摘要在 cart.subtotal 之后依次为 cart.promo_discount 与 cart.after_promo，手机底部固定栏在有活动优惠时显示 after_promo 的金额（照视觉稿 P04-promo-phone）；为 0、promotion 为 null 或没有活动时与现在完全相同
      - 行、提示与进度条——照落实写法显示；会员说明只对已登录会员显示，游客不显示
      - 重新计价——倒计时到 0 后共享读取的 generation 变化时重新计价，新结果没有活动时去掉全部活动内容
      - 文案与样式——frontend/src/i18n/copy.ts 加 cart.promo_discount、cart.after_promo、cart.hint_tier_amount、cart.hint_tier_percent、cart.hint_free_shipping、cart.free_shipping_reached、cart.hint_bxgy、cart.member_scheme_note，三语与 docs/UX-COPY.md 0.22 逐字相同；以 docs/design/pages/P04-promo-desktop、P04-promo-phone 为准，布局写在 frontend/src/styles/site.css（手机写在 767px 及以下的媒体查询里，只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性）；site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/pages/CartPage.test.tsx 用 vitest 与 react-dom/server（计价结果以替身给出，有活动的状态以 PromotionProvider 的 initial 传入，会员状态以替身代替）覆盖三种语言下两行金额与为 0 时不显示、打折行与买 X 享 Y 行的显示、满额减与满额折的下一档提示、免运费未达与已达与门槛 0、买 X 享 Y 提示只给范围内已有商品的规则且规则不在共享读取里时不显示、进度条两种变体的百分比与格数取整、取较近门槛与差额相等取满额、标签、全部达到满格与两类规则都没有时不画、共享读取没有活动或 promotion_id 不一致时不画进度条与行角标、会员说明只给会员、generation 变化重新计价；CART_KEYS 加入新键；现有断言不改；每条测试的注释写明它守住的 UX 0.20 P04 原句
      - docs/task-records/SHOP-TASK-258.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 App.tsx 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20 的 P04（活动、M1、M8、M9）、docs/DESIGN.md 1.23「节日促销活动」第 4、5、10 条与 docs/design/COMPONENTS.md「FestivalPromotion」的 Cart progress，在购物车显示游客计价接口（SHOP-TASK-234）返回的活动优惠、活动后金额、凑单提示与进度条。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：计价模块的活动类型由 SHOP-TASK-263 加好，本任务只用。页面只显示接口结果：promo_discount＝item_discount_sen 加 order_discount_sen，after_promo＝subtotal_sen 减它，为 0 时两行都不显示；行的 promotion_item_discount_sen 大于 0 时该行显示划线的行小计与活动价行小计（行小计减该行优惠），promotion_rule_id 不为空时行下显示 SHOP-TASK-254 的 PromoBadges（规则取 usePromotion 里同 rule_id 的规则，找不到时不显示）。凑单提示每条一行：spend_next_gap_sen 不为空时按 spend_mode 用 cart.hint_tier_amount（save 为 spend_next_value 的金额）或 cart.hint_tier_percent（percent 为 spend_next_value）；free_shipping_threshold_sen 大于 0 时未达用 cart.hint_free_shipping、已达用 cart.free_shipping_reached；buy_x_get_y 里 units_in_scope 大于 0 的每条用 cart.hint_bxgy（rule 为 usePromotion 里同 rule_id 规则的 badgeText，找不到该规则时这一条不显示；n 为 units_to_next，y 为 get_quantity）。进度条按 cart_progress（bar 用 --progress，steps 为 10 格 data-on）：未达门槛的候选为满额下一档（spend_next_gap_sen 不为空）与免运费（门槛大于 0 且未达），有候选时只画差额较小的一个（相等时取满额），进度百分比＝（门槛减差额）乘 100 除以门槛向下取整，steps 点亮的格数为百分比除以 10 向下取整，__labels（aria-hidden）依次为已达金额（门槛减差额）与门槛；没有候选、而有满额规则（spend_mode 不为空）或门槛大于 0 的免运费规则时满格（百分比 100，__labels 不显示）；都没有时不画。进度条与行下角标要用共享读取的装饰与规则，只在 usePromotion 有活动且其 promotion_id 与计价结果的 promotion.promotion_id 相同时显示（读取中、没有活动或不一致时不画进度条、不显示行角标）；金额行与文字提示照计价结果显示。已登录会员（useMemberSession 为 member）且 promo_discount 大于 0 时显示 cart.member_scheme_note。usePromotion 的 generation 变化时重新计价；重新计价期间沿用现有写法显示上一份结果（pending），新结果到达后再按它显示或去掉活动内容。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 4、5、10 条：购物车的活动金额是计价接口的暂算结果，页面只做显示所需的减法与进度比例，不判定是否参加活动，不碰个人资料。
      拆分：P04 的活动内容一层（7 个允许路径：页面与测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-263（计价类型）与 254（角标组件）。
```

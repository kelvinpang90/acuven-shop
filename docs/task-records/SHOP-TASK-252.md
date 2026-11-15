# SHOP-TASK-252 前台活动的读取模块与规则、角标、倒计时文案函数（不改页面）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 10 条：只把接口给的规则参数与金额套进文案模板，不计算金额，不碰个人资料。依据 UX 0.20「1. 全局框架」「节日促销活动」「商品卡价格与角标」、P03、P15 与 UX-COPY 0.22 第 11 节。依赖 SHOP-TASK-243（`GET /api/promotion`）。不改页面、后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `frontend/src/api/promotion.ts`（写法照 `frontend/src/api/catalog.ts`）：
  - 类型 `PromotionResponse`（`promotion: PromotionDisplay | null`、`server_time: string`）、`PromotionDisplay`、`RuleDisplay`、`TierDisplay`、`CategoryDisplay`，字段名与可空性同 `app/services/promotion_display.py` 与 `app/api/promotion.py`；`ends_at` 与 `server_time` 为带 Z 的 UTC 字符串，`kind`、`discount_mode`、`poster_mode` 为 `string`（接口不收窄）。
  - `promotionUrl(language)` → `/api/promotion?lang=<语言>`。
  - `fetchPromotion(url, signal?)`：GET、`credentials: "omit"`、`Accept: application/json`；非 2xx 抛 `PromotionError(status)`；网络错误与中止原样抛出；响应按类型直接取用，不逐字段校验。
- [x] 新增 `frontend/src/promoText.ts`（都以 `Language` 为参数，内部用 `translate` 与 `formatSen`，不读组件上下文）：
  - `PromoRuleParams`（`kind`、`discount_mode`、`percent`、`amount_sen`、`buy_quantity`、`get_quantity`、`get_percent`；除 `kind` 外都可为 null），`RuleDisplay` 与商品卡的规则都可直接传入。
  - `badgeText(language, rule) -> string | null`：打折按 `discount_mode` 为 `promo.badge_percent` / `promo.badge_amount`；买 X 享 Y 在 `get_percent` 为 100 时为 `promo.badge_bxgy_free`，否则 `promo.badge_bxgy_pct`；满额、免运费与不认识的类别为 null。
  - `ruleTexts(language, rule: RuleDisplay) -> string[]`：打折 `promo.rule_item_percent` / `promo.rule_item_amount`；买 X 享 Y `promo.rule_bxgy_free` / `promo.rule_bxgy_pct`；满额每档一行（档的 `amount_sen` 不为空用 `promo.rule_tier_amount`，否则 `promo.rule_tier_percent`）；免运费门槛 0 用 `promo.rule_free_shipping_all`，否则 `promo.rule_free_shipping`；不认识的类别为空列表。
  - `scopeText(language, rule: RuleDisplay) -> string | null`：有分类时按接口顺序连起分类名称（zh 用「、」，en 与 ms 用「, 」），`has_products` 为真套 `promo.rule_scope_with_items`，否则 `promo.rule_scope`；没有分类为 null。
  - `countdownText(language, remainingMs) -> string`：`promo.ends_in`；剩余按整秒向下取整，不大于 0（含 NaN）按 0；整天数 ≥ 1 时 `{time}` 用 `promo.countdown_days`，否则 `promo.countdown`，`hh`、`mm`、`ss` 补成两位。
  - 另导出类别常量 `KIND_ITEM_DISCOUNT`、`KIND_BUY_X_GET_Y`、`KIND_SPEND_DISCOUNT`、`KIND_FREE_SHIPPING`（值同 `app/models/promotion.py`），供之后的页面任务判断整单级与商品级规则。
- [x] `frontend/src/i18n/copy.ts` 在字典末尾加入 17 个 `promo.*` 键（`badge_percent`、`badge_amount`、`badge_bxgy_free`、`badge_bxgy_pct`、`ends_in`、`countdown_days`、`countdown`、`rule_item_percent`、`rule_item_amount`、`rule_bxgy_free`、`rule_bxgy_pct`、`rule_tier_amount`、`rule_tier_percent`、`rule_free_shipping`、`rule_free_shipping_all`、`rule_scope_with_items`、`rule_scope`），三语从 UX-COPY 第 11 节逐字照抄（含 `−`（U+2212）与 `—`（U+2014）），由现有 `copy.test.ts` 逐字对照文档。没有加其他键。
- [x] 测试：`frontend/src/api/promotion.test.ts`（fetch 替身：三种语言的地址、GET 与 `credentials: "omit"`、404/422/500/503 抛带状态码的 `PromotionError`、中止时原样抛出替身给的中止错误）；`frontend/src/promoText.test.ts`（三种语言下四类规则的角标与说明，含买 X 享 Y 的 100% 与 50%、满额减与满额折各两档、免运费门槛 0 与 RM60；整单级与不认识类别没有角标；商品卡的裸参数可直接传入；分类名称的顺序与分隔符、两种范围文案、没有分类与单个分类；倒计时在 1 天整、2 天 03:04:05、23:59:59（两种毫秒值）、0、999 毫秒与负数时的文字）。每条测试的注释写明它守住的 UX 0.20 或 DESIGN 1.23 第 10 条原句。
- 偏离与落实细节：
  - 参数不齐（如 `discount_mode` 为 percent 而 `percent` 为 null，或买 X 享 Y 缺 X、Y 或百分比）时，角标为 null、说明不出该行，不把 `{变量}` 或 null 显示给访客；接口的数据库约束保证正常数据不会出现这种情况，所以没有为它另写测试。满额的档两项都为空时同样跳过该档。
  - 金额一律经 `formatSen` 格式化（`RM {amount}` 的 `{amount}` 为两位小数）；百分比、X、Y 与天数原样作为整数填入。
  - 倒计时只接受调用方算好的剩余毫秒（`ends_at − server_time` 加本地经过的时间由 SHOP-TASK-253 负责），本模块不读时钟。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 10 条、UX 0.20 的相关段落与 UX-COPY 第 11 节；测试里的期望文字按文档模板手算（如 500 仙 → `RM 5.00`，`((2 × 24 + 3) × 3600 + 4 × 60 + 5)` 秒 → `2d 03:04:05`）。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 读取模块——frontend/src/api/promotion.ts 导出 PromotionDisplay、RuleDisplay、TierDisplay、CategoryDisplay、PromotionResponse 类型（字段名与类型同 app/services/promotion_display.py 与 app/api/promotion.py 的实际返回），promotionUrl(language) 返回 /api/promotion?lang=<语言>，fetchPromotion(url, signal) 以 GET、credentials 为 omit 读取，非 2xx 抛 PromotionError(status)，中止时抛出原错误
      - 角标——promoText.ts 导出 PromoRuleParams（kind、discount_mode、percent、amount_sen、buy_quantity、get_quantity、get_percent，RuleDisplay 与商品卡的规则都可直接传入）与 badgeText(language, rule)；打折按 discount_mode 为 promo.badge_percent 或 promo.badge_amount，买 X 享 Y 在 get_percent 为 100 时为 promo.badge_bxgy_free、否则为 promo.badge_bxgy_pct，满额与免运费返回 null
      - 规则说明——ruleTexts(language, rule) 返回该规则的说明行，打折为 promo.rule_item_percent 或 promo.rule_item_amount，买 X 享 Y 为 promo.rule_bxgy_free 或 promo.rule_bxgy_pct，满额每档一行（tiers 的 amount_sen 不为空时 promo.rule_tier_amount，否则 promo.rule_tier_percent），免运费门槛为 0 时 promo.rule_free_shipping_all、否则 promo.rule_free_shipping；scopeText(language, rule) 在 categories 不为空时把分类名称按接口顺序连起来（中文用顿号，英文与马来文用逗号加空格），has_products 为真时套 promo.rule_scope_with_items、否则 promo.rule_scope，没有分类时返回 null；ruleTexts 与 scopeText 的参数类型为 RuleDisplay，kind 不在四类之内时 ruleTexts 返回空列表、badgeText 返回 null
      - 倒计时——countdownText(language, remainingMs) 返回 promo.ends_in，剩余按整秒向下取整、小于 0 按 0；剩余 1 天以上时 time 用 promo.countdown_days（d 为整天数，hh、mm、ss 为两位），否则用 promo.countdown
      - 文案——frontend/src/i18n/copy.ts 加入上面用到的 17 个 promo 键，三语与 docs/UX-COPY.md 0.22 逐字相同；不加其他键
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/api/promotion.test.ts 以 fetch 替身断言地址、方法、credentials、非 2xx 抛 PromotionError 与中止；frontend/src/promoText.test.ts 覆盖三种语言下四类规则的角标与说明（含买 X 享 Y 的 100% 与不足 100%、满额减与满额折各档、免运费门槛 0）、分类名称的分隔符与两种范围文案、倒计时在 1 天整、23:59:59、0 与负数时的文字；每条测试的注释写明它守住的 UX 0.20 或 DESIGN 1.23 第 10 条原句
      - docs/task-records/SHOP-TASK-252.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改页面、后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/DESIGN.md 1.23「节日促销活动」第 10 条与 docs/UX.md 0.20「1. 全局框架」「节日促销活动」「商品卡价格与角标」、P03、P15，新建前台活动的公开读取模块 frontend/src/api/promotion.ts（GET /api/promotion，SHOP-TASK-243）与纯函数模块 frontend/src/promoText.ts（规则说明、角标与倒计时的文字，只套 docs/UX-COPY.md 0.22 第 11 节的文案模板）。页面接入见 SHOP-TASK-253 起的各任务。不改页面，不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：读取模块照 frontend/src/api/catalog.ts（credentials 为 omit，语言用 ?lang=，非 2xx 抛错误类，响应按类型直接取用、不做逐字段校验）；类型照接口实际返回（promotion 为 null 或 PromotionDisplay，server_time 与 ends_at 为带 Z 的 UTC 字符串，规则含 tiers、categories 与 has_products）。文案函数都以 Language 为参数、内部用 translate 与 formatSen，不读组件上下文；金额只格式化接口给的整数仙，不另算。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 10 条：只把接口给的规则参数与金额套进文案模板，不计算金额，不碰个人资料。
      拆分：前台活动的第一层（6 个允许路径：读取模块与纯函数各带测试、copy.ts 与任务记录），依赖 SHOP-TASK-243（公开展示接口）。
```

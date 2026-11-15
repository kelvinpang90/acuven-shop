# SHOP-TASK-262 前台目录模块的活动字段与专题页参数（只改类型与地址）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 10 条：只加读取目录活动价的类型，不计算金额，不碰个人资料。依赖 SHOP-TASK-244（商品目录接口新增的活动字段与 `sale` 参数）。不改页面、后端、`.platform/`、CI 与部署配置，不装依赖，不改设计；页面的显示见 SHOP-TASK-254 起的任务。
- [x] `frontend/src/api/catalog.ts` 的类型（照 HANDOFF 0.97 落实写法，字段名与可空性同 `app/services/catalog.py`）：
  - 新增 `CardPromotion`：`rule_id: number`、`kind: string`、`discount_mode`、`percent`、`amount_sen`、`buy_quantity`、`get_quantity`、`get_percent`（后六项为 `number | null`，`discount_mode` 为 `string | null`）。
  - `ProductSummary` 加 `promotion: CardPromotion | null` 与 `min_sale_price_sen: number | null`；`VariantDetail` 加 `sale_price_sen: number | null`；`ProductDetail` 加 `promotion: CardPromotion | null`。都写成必填（接口总会给出）；照现有写法不做逐字段校验。
- [x] `ProductListRequest` 加 `sale?: boolean | undefined`；`productsUrl` 在 `sale` 为真时于现有参数（含 `page_size`）之后加 `sale=true`，省略或为假时地址与之前逐字相同。其余地址函数不变。
- [x] 测试替身只补新字段（`promotion`、`min_sale_price_sen`、`sale_price_sen` 为 null），不改断言：`frontend/src/components/ProductCard.test.tsx`、`frontend/src/pages/HomePage.test.tsx`、`frontend/src/pages/ProductListPage.test.tsx`、`frontend/src/pages/ProductDetailPage.test.tsx`、`frontend/src/pages/productOptions.test.ts`。
- [x] `frontend/src/api/catalog.test.ts` 新增两条：`sale` 为真时 `sale=true` 排在全部现有参数之后（含只给 `sale` 时的完整地址）；省略与为假时完整地址与之前逐字相同，精选商品的两个地址不带 `sale`。注释写明守住 UX 0.20 P15「商品区列出生效活动全部商品级规则范围内的上架商品」。
- 偏离与落实细节：无偏离。`sale` 只认 `true`（`request.sale === true`），不写 `sale=false`，以保持未用专题页时的地址不变。
- 验证到什么程度：人工逐条对照验收标准、`app/services/catalog.py` 的数据类与 `app/api/catalog.py` 的 `sale` 参数；测试里的期望地址按 `URLSearchParams` 的编码手写（如 `size:m` → `size%3Am`）。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 类型——frontend/src/api/catalog.ts 照落实写法加 CardPromotion 与四处字段，字段名与 app/services/catalog.py 相同
      - 地址——productsUrl 照落实写法支持 sale；其余地址函数不变
      - 现有测试——frontend/src/components/ProductCard.test.tsx、frontend/src/pages/HomePage.test.tsx、frontend/src/pages/ProductListPage.test.tsx、frontend/src/pages/ProductDetailPage.test.tsx 与 frontend/src/pages/productOptions.test.ts 里的替身只补新字段（promotion 与 min_sale_price_sen、sale_price_sen 为 null），不改断言
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——frontend/src/api/catalog.test.ts 补 sale 为真时地址带 sale=true、省略与为假时与现在逐字相同；注释写明它守住的 UX 0.20 P15「商品区列出生效活动全部商品级规则范围内的上架商品」
      - docs/task-records/SHOP-TASK-262.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改页面、后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/DESIGN.md 1.23「节日促销活动」第 10 条，让 frontend/src/api/catalog.ts 的类型带上商品目录接口新增的活动字段（SHOP-TASK-244），并给商品列表地址加专题页用的 sale 参数；页面的显示见 SHOP-TASK-254 起的任务。不改页面，不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：新增 CardPromotion（rule_id、kind、discount_mode、percent、amount_sen、buy_quantity、get_quantity、get_percent，类型同 app/services/catalog.py）；ProductSummary 加 promotion（CardPromotion 或 null）与 min_sale_price_sen（数或 null），VariantDetail 加 sale_price_sen（数或 null），ProductDetail 加 promotion（CardPromotion 或 null），都写成必填（接口总会给出）；ProductListRequest 加 sale（布尔，可省略），为真时 productsUrl 在现有参数之后加 sale=true，省略或为假时地址与现在逐字相同。照现有写法不做逐字段校验。
      设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 10 条：只加读取目录活动价的类型，不计算金额，不碰个人资料。
      拆分：前台目录模块一层（8 个允许路径：目录模块与测试、5 个只补替身字段的测试文件与任务记录）。字段写成必填后，完整写出这些类型的测试替身必然过不了类型检查：运营者登记时把字段按必填加进类型，在 main 的导出上跑类型检查，报错的只有下列 5 个测试文件，所以与类型一起改、只补字段。依赖 SHOP-TASK-244。
```

# SHOP-TASK-244 商品目录的活动价与角标字段、活动专题页的商品列表

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 3、10 条：展示的活动价与计价同一算法，不碰个人资料；UX 0.20「1. 全局框架」的商品卡规则、P03 与 P15。依赖 SHOP-TASK-231（`price_promotion`）与 233（`LoadedPromotion`、`active_promotion`、`rule_for_product`）。不改计价、下单、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] `app/services/catalog.py`：
  - 新增数据类 `CardPromotion(rule_id, kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent)`。
  - `ProductSummary` 末尾加 `promotion`、`min_sale_price_sen`；`ProductDetail` 末尾加 `promotion`；`VariantDetail` 末尾加 `sale_price_sen`。
  - `list_products` 新增只限关键字参数 `promotion`（`LoadedPromotion | None`，默认 `None`）与 `sale_only`（默认 `False`）；`get_product` 新增只限关键字参数 `promotion`（默认 `None`）。现有调用不变。
  - 命中的规则取 `rule_for_product(promotion, product.id, product.category_id)`（商品当时所属的分类，重叠时取排序小者）；一件的活动单价为 `price_promotion` 对数量 1 的一行（只带命中的那条规则）的 `after_item_subtotal`，不另写舍入。
- [x] `app/api/catalog.py`：`GET /api/catalog/products` 与 `GET /api/catalog/products/{slug}` 每次请求以 `active_promotion(db, _utcnow())` 读生效活动（不缓存）后传入；成功响应加 `Cache-Control: no-store`（只设响应头，不用 `_NoStoreRoute`；404、422、503 等错误响应与格式照旧）。`_utcnow` 为模块级函数，测试以 monkeypatch 固定。
- [x] JSON 字段（供前台任务引用）：
  - `promotion`（商品列表每项、商品详情）：没有生效活动或商品不在任何商品级规则范围内时为 `null`；否则为 `{rule_id, kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent}`：
    - 打折：`kind` 为 `"item_discount"`，`discount_mode` 为 `"percent"`（`percent` 有值，`amount_sen` 为 `null`）或 `"amount"`（`amount_sen` 为每件直降的仙数，`percent` 为 `null`），三个买 X 享 Y 参数为 `null`。
    - 买 X 享 Y：`kind` 为 `"buy_x_get_y"`，`buy_quantity` 为 X、`get_quantity` 为 Y、`get_percent` 为享受的件的折扣百分比（100 即送），`discount_mode`、`percent`、`amount_sen` 为 `null`。
  - `min_sale_price_sen`（商品列表每项，含首页按 `slug` 取的精选）：命中打折规则时为各启用 SKU 活动单价的最小值（整数仙，可为 0），否则 `null`。「起」照原有的 `has_multiple_variants`。`min_price_sen` 照旧是原价最低单价，价格排序仍按原价。UX 要求的划线原价（与最低活动单价出自同一 SKU）列表不另给字段：活动单价随原价单调不减，前台可直接用 `min_price_sen`。
  - `variants[].sale_price_sen`（商品详情）：命中打折规则时为该 SKU 的活动单价，否则 `null`。
  - 买 X 享 Y 时 `promotion` 有值，`min_sale_price_sen` 与各 `sale_price_sen` 都为 `null`，显示单价不变。
  - 查询参数 `sale`（布尔，默认 `false`）：`GET /api/catalog/products?sale=true` 为 P15 专题页的商品列表，只列生效活动全部商品级规则范围（显式选的商品，加所选分类下的商品，按商品当前所属分类）内满足 `published()` 的商品；排序与分页照常（专题页不传 `sort` 即 `newest`），`q`、`category`、`option`、`slug` 等其他筛选照常可用。没有生效活动、或活动只有整单级规则时为空页（`total` 为 0）。
- [x] 测试：新文件 `tests/test_catalog_promotion.py`（SQLite 内存库，接口用测试自己的会话，`_utcnow` 固定为 2026-11-13 04:00 UTC）：八折的各 SKU 活动单价与列表的最低活动单价（停用 SKU 不算，首页按 slug 取的同样带）；九折下恰好半仙进一仙且与 `price_lines` 一件的结果相同；直降与直降大于单价时为 0；买 X 享 Y 只给角标参数；按分类与按商品命中、改分类后按当时分类命中；两条规则都命中时取排序小者（排序小者 ID 较大）；`sale` 列表的范围、未上架不列、分页、与其他筛选组合；没有生效活动或只有整单级规则时 `sale` 为空页；草稿、未开始、已结束、已提前结束的活动下新字段都为 `null` 且其余与现在相同；成功响应带 no-store、活动每次请求重新读取、404 格式照旧。每条测试的文档字符串写明它守住的第 3、10 条或 UX 原句。`tests/test_catalog_api.py` 只为新字段（为 `null`）改了列表与详情的整体比较。
- 偏离与落实细节：
  - `min_sale_price_sen` 对页内命中打折规则的商品另发一次 SELECT 取启用 SKU 的原价，逐个经 `price_promotion` 算出后取最小；不在 SQL 里计算，免得与计价的舍入不同。
  - `sale_only` 为真而 `promotion` 为 `None`、或活动没有商品级规则时，不发查询直接返回空页（`page`、`page_size` 照所给）。
  - 活动数据异常使 `price_promotion` 拒绝规则参数时照它抛出，不在目录里吞掉。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 3、10 条与 UX 0.20 的商品卡规则、P03、P15。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 字段——新增数据类 CardPromotion(rule_id, kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent)；ProductSummary 末尾加 promotion（CardPromotion 或 None）与 min_sale_price_sen（命中打折规则时为各启用 SKU 活动单价的最小值，否则 None）；ProductDetail 末尾加 promotion；VariantDetail 末尾加 sale_price_sen（命中打折规则时为该 SKU 的活动单价，否则 None）。买 X 享 Y 时 promotion 有值而两个价格字段为 None
      - 专题页列表——GET /api/catalog/products 新增查询参数 sale（布尔，默认 false）：为 true 时只列商品级规则范围（显式选的商品加所选分类下的商品）内满足现有发布规则的商品，排序与分页照默认（newest），其他筛选参数照常可用；没有生效活动时为空页（total 为 0）
      - 接口——列表与详情每次请求读生效活动（不缓存），成功响应带 Cache-Control no-store；没有生效活动时新字段都为 null，其余字段与现在相同
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_catalog_promotion.py：八折与直降的活动单价（含恰好半仙进一仙、直降大于单价时为 0）、多规格的最低活动单价、买 X 享 Y 只给角标参数、按分类与按商品命中、两条规则都命中时取排序小者、sale 列表的范围与分页、没有生效活动时与现在相同；tests/test_catalog_api.py 对列表与详情的整体比较只为加上新字段（为 null）而改。每条测试的文档字符串写明它守住的第 3 或 10 条或 UX 的哪一句
      - docs/task-records/SHOP-TASK-244.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明 JSON 字段名与 sale 参数，供前台任务引用）；改动只落在 allowed_change_paths；不改计价、下单、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

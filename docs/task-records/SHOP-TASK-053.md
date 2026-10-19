# SHOP-TASK-053 商品列表接口按 slug 筛选

- [x] 给公开的商品列表接口 `GET /api/catalog/products` 加可重复的查询参数 `slug`，供首页按店铺装修的精选商品取商品卡片：`app/api/catalog.py`（依赖 `_slug_filter`，把参数传给服务函数）、`app/services/catalog.py`（`list_products` 新增带默认值空元组的关键字参数 `slugs`）；测试见新文件 `tests/test_catalog_slug_filter.py`。未改其他筛选、排序、分页与字段，未改已有目录测试、前端代码、设计文档、`.platform/`、CI 或部署配置，未加表或迁移，未装依赖。设计闸门：不适用（只读筛选，商品与价格字段不变）。拆分：接口层，不拆。
- 参数：`slug=<商品 slug>` 可重复，最多 20 个（`MAX_SLUGS`），每个 1 到 100 个字符（`MAX_SLUG_LENGTH`，与商品 slug 的列长相同）。超过 20 个时 422，`detail` 为一条 `{"type":"too_many_slugs","loc":["query","slug"],"msg":"At most 20 slug values are allowed"}`；有空串或超过 100 个字符的值时 422，每个不合格的值一条 `{"type":"slug_length_invalid","loc":["query","slug",<序号>],"msg":"Slug should have 1 to 100 characters"}`。写法同 `app/api/pay.py` 的 `_language`（`RequestValidationError` 加 `app/api/orders.py` 的 `_error`），不含所给的值。
- 规则：给出时只返回 slug 在其中且满足 `published()` 的商品（`Product.slug IN (...)` 加进同一组条件），与搜索、分类、规格筛选为且；不存在或不满足 `published()` 的 slug 被略去，不报错；同一 slug 重复给出只算一次。排序（默认 newest，不按参数顺序）、分页、每页条数、总数的算法与每项字段不变。不给时条件不变，与之前完全相同；`list_products` 不给 `slugs` 时行为不变。
- 偏离与取舍，请审阅（未改需求与设计，未发现须停下的需求或设计问题）：
  - 结果按所选排序返回，不按 slug 参数的顺序；首页要按装修设置的位置显示时，由前台按 `featured_slugs` 的顺序重排卡片（留给 SHOP-TASK-054）。
  - slug 的校验依赖放在会话依赖之前：数据库未配置时，不合法的 slug 先得到 422，合法时仍是 503。
  - 错误类型 `too_many_slugs`、`slug_length_invalid` 与消息由本任务确定，合同只规定了写法。
- 留给之后的任务：前台按店铺装修显示精选商品（调用本参数、按位置排卡片、精选为空时取最新 4 件）由 SHOP-TASK-054 实现。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_catalog_slug_filter.py` 用 TestClient 与 SQLite 内存库（StaticPool，打开外键检查并断言已打开，会话依赖换成测试自己的会话），建数据的辅助函数从 `tests/test_catalog_api.py` 导入（不改该文件），每条测试的文档字符串写明它守住的规则，覆盖：多个 slug 只返回这些商品、总数正确、按 newest 排序、分页与 price_desc 照常、每项与不筛选时的对应项相同、重复 slug 不重复返回；不存在、商品停用、分类停用、没有启用 SKU 的 slug 被略去且仍 200，全部略去时为空页；与分类筛选、搜索同时用时为且；不给 slug 时结果与之前相同、服务函数不给与给空元组相同；21 个、101 个字符、空串时 422，错误只有类型、位置与固定消息、不回显所给的值；20 个与 100 个字符的边界被接受。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未在真 MySQL 上验证。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - GET /api/catalog/products 新增可重复的查询参数 slug（最多 20 个，每个 1 到 100 个字符，超出 422，detail 为不含输入值的错误数组，写法同 app/api/pay.py 的 _language），给出时只返回 slug 在其中且满足 published() 的商品，与其他筛选为且；不存在或不满足 published() 的 slug 被略去而不是报错；排序、分页、每页条数与每项字段不变；不给时与现在完全相同
      - app/services/catalog.py 的 list_products 以带默认值空元组的关键字参数接收 slug 列表，现有调用方式与行为不变；app/api/catalog.py 把查询参数传入
      - tests/test_catalog_slug_filter.py 用 TestClient 与 SQLite 内存库（开启外键检查）按现有目录测试的方式建数据，每条测试写明它守住的是哪一条规则，验证——按多个 slug 只返回这些商品且总数正确；不存在与不满足 published() 的 slug 被略去；与分类筛选同时用时为且；不给 slug 时结果与现在相同；超过 20 个或单个超过 100 个字符时 422；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；已有目录测试不改并照常通过
      - docs/TODO.md 新增 SHOP-TASK-053 记录段（参数、规则、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

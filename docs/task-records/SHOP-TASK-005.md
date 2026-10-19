# SHOP-TASK-005 商品目录只读查询与筛选 API

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 Product / Variant 一行与「边界与原则」，在 SHOP-TASK-004 的表上提供公开只读的商品目录接口：`app/db/session.py`、`app/services/catalog.py`、`app/api/catalog.py`，`app/main.py` 挂上路由，测试见 `tests/test_catalog_api.py`
- 接口清单（只有 GET，全部在 `/api/catalog/` 下；`lang` 只接受 `en`、`zh`、`ms`，默认 `en`，其他值 422）：
  - `GET /api/catalog/categories?lang=`：启用且有英文名称的分类，按 id 排序；每项 `slug`、`name`。
  - `GET /api/catalog/products?lang=&q=&category=&option=&sort=&page=&page_size=`：已发布商品的一页，返回 `total`、`page`、`page_size`、`items`；每项 `slug`、`name`、`category`（`slug`、`name`）、`image`（排列序号最小的图片引用，没有图片为 null）、`min_price_sen`（启用 SKU 最低单价）、`sold_out_today`（所有启用 SKU 的当日可用库存均为 0）。`category` 可重复，多个分类为或；`option` 可重复，写作 `<规格名 code>:<规格值 code>`，同一规格名下多个值为或、不同规格名之间为且，且须由同一个启用 SKU 同时满足，格式不对 422；`sort` 只接受 `newest`（默认）、`price_asc`、`price_desc`，价格按最低单价，平手一律按商品 id 升序；`page` 从 1 起，`page_size` 默认 24、上限 48，超过上限 422。
  - `GET /api/catalog/products/{slug}?lang=`：`slug`、`name`、`description`、`category`、`images`（按排列序号）、`options`（规格名按排列序号，每个含 `code`、`name` 与按序的 `values`）、`variants`（每个启用 SKU 的 `sku`、`options`（规格名 code → 所选规格值 code）、`price_sen`、`available_stock`，按 id 排序）。未发布与不存在的 slug 返回同一个 404。
  - 所有文案字段都是 `{text, english_fallback}`：缺少请求语言时给英文并把 `english_fallback` 置真，供页面显示「仅英文」标签；文案为 NULL、空串或只有空格都算缺少。金额只有 MYR 整数仙，不含参考外币、每日初始库存、启用状态等后台字段。
  - 发布规则在一个 SQL 条件里判断：商品与所属分类都启用，分类英文名称、商品英文名称与英文描述、该商品所有规格名与规格值的英文名称齐全，且至少有一个启用的 SKU。搜索按当前语言名称或英文名称做不区分大小写的包含匹配，`%`、`_` 按字面匹配。
  - `app/db/session.py`：`get_session` 依赖在第一次请求时按 `SHOP_DATABASE_URL` 建引擎与会话工厂（同一连接串只建一次），每个请求一个会话、不提交；未配置数据库时应用照常启动、健康检查不受影响，目录接口返回 503。
- 偏离：未改设计与审阅稿。与 `docs/UX.md` 0.2 的出入：P02 线框的规格筛选栏需要列出可选的规格名与规格值，验收标准的列表字段里没有这一项，本任务未提供，留给页面任务按需另行登记；P01 线框的分类卡片带图，`categories` 表没有图片列，分类列表不给图片。规格名属于单件商品，跨商品筛选按规格名与规格值的 code 匹配。「最新」的平手同样按商品 id 升序。「今日售罄」与当日可用库存直接读 `available_stock`，每日重置由之后的任务维护。`app/core/config.py` 中 `database_url` 的注释「只有 alembic 用到；应用本身还没有任何表」已过时，该文件不在本任务可改范围内，记为待清理项。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_catalog_api.py` 用 TestClient 与 SQLite 内存库（StaticPool，覆盖 `get_session`）覆盖：已发布商品的列表项与详情整体相等（多一个后台字段即失败，含默认英文）；发布规则的八个条件逐一使商品从列表消失、详情 404 且与不存在的 slug 响应相同（每例先确认改动前是发布的）；分类列表只含启用且有英文名称的分类；回退英文及逐字段标记（中文、马来文）；最低单价与售罄只算启用 SKU、停用 SKU 不出现在详情；搜索（当前语言名、英文名、大小写、英文界面不按中文名匹配、`%` 字面匹配）；分类筛选为或；规格筛选同名为或、异名为且，含反例（颜色只由 SKU-A 命中、尺寸只由 SKU-B 命中时不出现；唯一满足的 SKU 停用时不出现）；三种排序、按最低单价而非最高单价、稳定平手、非法排序值被拒；分页总数与每页条数上限；非法语言参数（含 `EN`）在三个接口都被拒；格式不对的规格筛选被拒；三个接口只发 SELECT、写方法 405；未配置数据库时健康检查 200、目录接口 503。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未在真 MySQL 上跑这些查询（CI 的 MySQL 只用于迁移检查）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.8「数据模型」「边界与原则」一致；接口字段参照 docs/UX.md 0.2 的 P01–P03 审阅稿，与设计冲突时以设计为准并在 PR 里说明，不改设计与审阅稿
      - app/db/session.py 按 SHOP_DATABASE_URL 惰性创建引擎与会话，并提供 FastAPI 依赖；未配置数据库时应用照常启动，健康检查不受影响
      - 只有 GET 接口，全部在 /api/catalog/ 下：分类列表、商品列表、按 slug 取商品详情；语言参数只接受 en、zh、ms，默认 en
      - 发布规则：商品与所属分类都启用，分类英文名称、商品英文名称与英文描述、该商品所有规格名与规格值的英文名称齐全，且至少有一个启用的 SKU 规格，才出现在列表与详情里；否则详情返回 404，与不存在的 slug 同一响应；分类列表只返回启用且有英文名称的分类
      - 文案按请求语言返回，缺少时回退英文，并标出哪些字段回退了英文，供页面显示「仅英文」标签
      - 列表每项给出 slug、名称、分类、首张图片引用、启用 SKU 中的最低单价（整数仙，供「RM x 起」）以及今日是否售罄（所有启用 SKU 当日可用库存均为 0）；详情给出按序的图片引用、规格名与规格值、每个启用 SKU 的 SKU、所选规格值、单价与当日可用库存
      - 搜索按当前语言名称或英文名称做不区分大小写的包含匹配；筛选：同一规格名下多个值为或、不同规格名之间为且，且须由同一个启用 SKU 同时满足；排序只接受最新、价格从低到高、价格从高到低（价格按最低单价），平手按商品 id 保持稳定；分页返回总数，每页条数有上限
      - 响应只含 MYR 整数仙，不含参考外币、每日初始库存、启用状态等后台字段；接口不写数据库
      - tests/test_catalog_api.py 用 TestClient 与 SQLite 内存库（覆盖会话依赖）验证：发布规则的每个条件（含分类或规格缺英文名称）与 404、回退英文及其标记、最低单价与售罄、搜索、筛选的或与且（含反例：颜色只由 SKU-A 命中、尺寸只由 SKU-B 命中时该商品不出现）、三种排序与稳定平手、分页总数、非法语言参数被拒；每条测试写明它守住的是哪一句规则；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-005 记录段（接口清单、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表、不加迁移、不加写接口
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.8，提交 2b68ad0），不合并、不直推
```

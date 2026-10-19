# SHOP-TASK-045 店铺装修设置的公开读取接口

- [x] 按 `docs/REQUIREMENTS.md`「店铺装修」、`docs/UX.md` 0.8 的 A08 与 P01 以及 `docs/HANDOFF.md` 0.34 记录的 Kelvin 2026-10-06 决定（存储与公开读取首版不含标志图；精选商品只显示前台目录可见的商品，复用目录的 published() 判定），实现店铺装修设置的读取函数与公开只读接口：`app/services/store_design.py`（`read_store_design`）、`app/api/store_design.py`（`GET /api/store-design`），`app/main.py` 挂上路由；测试见新文件 `tests/test_store_design.py`。未改 `app/services/catalog.py`、SHOP-TASK-044 的模型与迁移、已有测试、前端代码（`frontend/src/storeDesign.ts` 仍是固定值）、`.platform/`、CI 或部署配置，未加表或迁移，未装依赖。设计闸门：不适用（理由同 SHOP-TASK-044：店铺装修只改变前台外观，不涉及金额计算与个人资料；本任务只读）。拆分：读取规则只有两条，与只读接口一起交付，照 SHOP-TASK-037 的做法。
- 读取规则（`read_store_design`，只发 SELECT、只查列值不加载 ORM 对象，不写库、不写日志；查询期间用 `db.no_autoflush` 关闭 autoflush，调用方会话里未 flush 的新行与改动不会因读取被写进库，读到的是库里已有的数据）：
  - 主题与主色取单例槽为 1 的设置行；该行不存在时取 SHOP-TASK-044 的 `DEFAULT_THEME`、`DEFAULT_ACCENT`（`pandan`、空）。主色原样返回，为空即该主题的默认主色。
  - 区块按位置排序，带区块名与是否显示；区块表缺少某些区块时，缺少的按 `HOME_BLOCKS` 的默认顺序（hero、how、categories、featured）补在已有区块之后并显示，结果总是四个区块。补的区块不写回库里。
  - 精选商品按位置排序，只留满足 `app/services/catalog.py` 的 `published()` 的商品（直接在查询里调用该判定，不另写一套）；不可见的精选行仍留在表里，只是不出现在结果中，其余的相对顺序不变。结果可能为空。
- 接口 `GET /api/store-design`：返回 `{theme, accent, home_blocks, featured_slugs}`，`home_blocks` 是 `{block, visible}` 的数组，`accent` 为空时是 `null`。不需登录，不读写会话或 cookie，不要求 CSRF，不写日志；数据库未配置时由 `get_session` 返回与其他公开接口相同的 503 `{"detail": "database is not configured"}`。处理函数与依赖产生的全部响应（含该 503）都带 `Cache-Control: no-store`：路由器复用 `app/api/order_lookup.py` 的 `_NoStoreRoute`（与订单查询、退款、后台各接口相同）；路径存在但方法不匹配的 405 由框架在路由之外回答，不带 `no-store`。没有修改接口。
- 偏离与取舍，请审阅（未改需求与设计，未发现须停下的需求或设计问题）：
  - 读取不校验主色是否属于所选主题，库里是什么就返回什么；该校验按 SHOP-TASK-044 的约定由写入方负责（`read_store_design` 的 docstring 写明）。精选最多 4 件、挑选时须满足 `published()`、四个区块在同一事务里保持齐全同样由之后的管理后台业务接口任务实现。
  - 「未挑选或挑选的都不可见时显示最新 4 件」不在读取里做：`featured_slugs` 为空时由前台按设置显示的任务取商品列表接口 `newest` 的前 4 件（与 SHOP-TASK-015 现在的做法相同）。
  - 站点设置接口 `GET /api/site-settings` 的 503 不带 `no-store`（只有处理函数设响应头）；本接口按验收要求连 503 也带，所以用路由类而不是在处理函数里设响应头。`_NoStoreRoute` 是 `order_lookup` 模块的私有名字，已有四个接口模块以同样方式导入它。
  - 一次读取发三条 SELECT（设置、区块、精选），不在同一条语句里；同一请求同一会话，MySQL 默认的可重复读下三条读的是同一快照。
- 留给之后的任务：后台修改（主色校验、精选挑选与排序、区块顺序与显隐，含审计记录）与 A08 页面，由管理后台业务接口任务与后台店铺装修 A08 任务实现；前台按设置显示主题、主色、区块与精选商品（含精选为空时显示最新 4 件，并把 `frontend/src/storeDesign.ts` 改为读取本接口），由前台按设置显示的任务实现；标志图随图片规则另做。
- 验证到什么程度：人工逐条对照验收标准与需求原句自查。`tests/test_store_design.py` 用 SQLite 内存库（StaticPool，打开外键检查并断言已打开）按模型建表、TestClient 调用接口（会话依赖换成测试自己的会话），每条测试的文档字符串写明它守住的需求原句、Kelvin 2026-10-06 的决定或验收约定，覆盖：区块与精选按位置排序（写入顺序与位置、商品 id 故意不同，隐藏区块照样列出）；精选里停用的商品、所属分类停用的商品、规格全部停用与没有规格的商品、缺英文描述的商品都被略去且顺序保持；略去不删精选行，重新启用后按原位置出现；没有精选或都不可见时为空列表；设置行不存在时返回默认主题与主色；三张表都空时为完整默认值；缺一、二、三个区块时按默认顺序补齐为四个；读取只发 SELECT、不补写区块与设置行；会话里有未 flush 的新区块行与改过的设置行时读取仍只发 SELECT、返回库里已有的值、改动仍留在会话里；接口字段与顺序、`accent` 为 `null`（主色为空与设置行不存在）；库里改了之后下一次请求即新值且带 `no-store`；数据库未配置时 503 带 `no-store`；带不带 cookie 结果一样、不设 cookie、不需要 CSRF 头、应用不记日志、只发 SELECT，写方法 405。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未在真 MySQL 上验证。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.8 的 A08 与 P01 以及 docs/HANDOFF.md 0.34 记录的 Kelvin 2026-10-06 决定一致；发现需求或设计本身有问题时停下并在 PR 里说明，不自行改
      - app/services/store_design.py 提供读取函数，返回主题、主色、按位置排序的四个区块（区块名与是否显示）与按位置排序的精选商品 slug；精选商品只含满足 app/services/catalog.py 的 published() 判定（即前台目录可见：商品启用、分类启用、英文资料齐全、至少一件启用规格）的商品，复用该判定、不另写一套
      - 设置行不存在时主题与主色返回 SHOP-TASK-044 的默认值常量；区块表缺少某些区块时，缺少的区块按默认顺序补在已有区块之后并显示，结果总是四个区块；读取不写库；docstring 写明写入与校验（主色须属于所选主题、精选最多 4 件且须满足 published()、四个区块在同一事务里保持齐全）由之后的管理后台业务接口任务实现，未挑选或挑选的都不可见时显示最新 4 件的规则由前台按设置显示的任务实现
      - GET /api/store-design 返回 {theme, accent, home_blocks, featured_slugs}（home_blocks 为 {block, visible} 的数组；accent 为空时返回 null）；该接口处理函数与依赖产生的所有响应（含 503 等错误）都带 Cache-Control 为 no-store，路径存在但方法不匹配时由框架返回的 405 不在此列；不读写会话、不要求 CSRF、不写日志；数据库未配置时与其他公开接口相同返回 503；app/main.py 挂上路由
      - 不改 app/services/catalog.py 与 SHOP-TASK-044 的模型；已有测试照常通过
      - tests/test_store_design.py 用 SQLite 内存库（开启外键检查）与 TestClient 验证：读取按位置排序；精选里停用的商品、所属分类停用的商品、没有启用规格的商品都被略去且顺序保持；设置行不存在时返回默认值；缺少区块时按默认顺序补齐为四个；接口字段与 accent 为 null 的情形；所有响应 no-store（含数据库未配置的 503）；每条测试写明它守住的是需求里哪一句或 Kelvin 的哪一项决定；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；docs/TODO.md 新增 SHOP-TASK-045 记录段（读取规则、接口、偏离、验证到什么程度，并写明后台修改与前台显示留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改前端代码（frontend/src/storeDesign.ts 由前台按设置显示的任务改）
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

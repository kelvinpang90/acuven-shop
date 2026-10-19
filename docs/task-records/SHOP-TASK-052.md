# SHOP-TASK-052 后台库存重置结果的查询接口

- [x] 按 `docs/UX.md` 0.10 的 A07 实现后台库存重置结果的两个只读接口（A07 的后端）：读取与整形在新文件 `app/services/admin_stock_resets.py`，接口在新文件 `app/api/admin_stock_resets.py`，`app/main.py` 挂上路由；测试见新文件 `tests/test_admin_stock_resets.py`。字段取自 SHOP-TASK-032 的 `stock_resets` 与 `stock_reset_lines`（`app/models/stock_reset.py`）。未改 `app/services/stock_reset.py`、模型与迁移、前端、已有测试、`.platform/`、CI 或部署配置，未加表或迁移，未装依赖。设计闸门：不适用（只读演示库存的重置记录，不改金额与库存规则，不含个人资料）。拆分：接口层（含只读查询），不拆。
- 接口与字段：两个接口都经 SHOP-TASK-036 的 `require_admin`（无有效后台会话 401 `admin_session_required`，先于页码与路径 ID 的校验），只读，不要求 CSRF，不返回 CSRF 令牌，不写审计、不写日志、不提交。路由器用 SHOP-TASK-027 的 `_NoStoreRoute`，处理函数与依赖产生的全部响应（含错误）都带 `Cache-Control: no-store`（路径存在但方法不匹配的 405 由框架回答，不在此列）。
  - `GET /api/admin/stock-resets?page=N`：返回 `{total, page, page_size, resets}`，`page_size` 为 30；每项 `id`、`business_date`（`YYYY-MM-DD`，马来西亚营业日期）、`result`（`succeeded` 或 `failed`）、`sku_count`、`completed_at`（带 `Z` 的 UTC，失败时为 `null`）。不返回尝试次数、首次开始时间与异常类名。
  - `GET /api/admin/stock-resets/{reset_id}`：返回上述各字段与 `lines`，每项 `sku`、`initial_stock`、`held_quantity`、`available_stock`（对应 `admin.stock_reset_breakdown` 的三个数）；不返回规格 ID，规格已删除（`variant_id` 为空）的行凭 SKU 快照照常返回。重置不存在 404 `not_found`。
- 排序与分页：列表按营业日期从新到旧（营业日期全表唯一，不需要第二排序键）；明细按 SKU 升序。`page` 为 1 到 10000 的十进制整数（不带符号与前导零），默认 1；不合法时 422，`detail` 为固定的一条错误 `{"type":"page_invalid","loc":["query","page"],"msg":"Page should be an integer from 1 to 10000"}`，不含所给的值（写法同 `app/api/pay.py` 的 `_language`）。超出的页 `resets` 为空、`total` 照常。路径 ID 只接受不带符号与前导零、不超过 2147483647 的十进制整数，否则 422 `{"type":"reset_id_invalid","loc":["path","reset_id"],"msg":"Stock reset ID should be a positive integer"}`。
- 偏离与取舍，请审阅（未改需求与设计）：
  - 失败记录的明细（对照 A07 后判断不是设计问题，接口不需要停下）：A07 线框的「▸（展开后）<sku> [admin.stock_reset_breakdown]」在桌面与手机稿里都画在整张列表之后，表示「每行可展开看各 SKU」的通用写法，没有规定失败那天必须有 SKU 明细；按 SHOP-TASK-032 失败时整笔回滚，失败记录本来就没有明细（`sku_count` 为 0）。接口照实返回：失败那天的 `lines` 为空数组，没有新造或推算数据，也不与 A07 的任何一句冲突。失败行展开后空明细怎么显示属于页面细节，留给 A07 页面的任务。
  - `page` 不合法时的错误类型 `page_invalid` 与消息由本任务确定，合同只规定了写法。
  - 明细的 SKU 升序交给数据库排序：MySQL 按列的排序规则（默认不区分大小写），SQLite 按字节。SKU 只有大小写不同时两边顺序可能不同；测试只用大写 SKU。
- 留给之后的任务：A07 页面（后台库存重置结果页，调用本任务的两个接口，包括 `admin.stock_reset_failed` 那一行和展开明细的显示），以及在 `ADMIN_NAV` 里加上它的导航项；运营告警邮件接入之前，`admin.stock_reset_failed`「已告警运营者」仍按 `docs/HANDOFF.md` 2026-10-01 的决定处理。
- 验证到什么程度：人工逐条对照验收标准与 A07 原句自查。`tests/test_admin_stock_resets.py` 用 TestClient 与 SQLite 内存库（StaticPool，打开外键检查并断言已打开；会话依赖换成每个请求一个绑定同一内存库的会话，结果在另一个会话里读），自建管理员、后台会话、商品规格与重置记录（成功、失败各若干天；一条明细的规格在写入后被删除，在库里确认 `variant_id` 已置空）。每条测试（参数化的是每个用例）的文档字符串写明它守住的是 A07 的哪一句或验收里的哪项约定。覆盖：无会话、库里没有的令牌、已到期与已撤销的会话时两个接口都 401（页码或路径 ID 不合法时也先 401）；只有两个 GET 路由、查询参数只有 `page`；33 天的记录乱序写入，列表按营业日期从新到旧，第 1 页 30 条，第 2 页 3 条，第 3 页为空、`total` 照常，默认与显式第 1 页一致；字段恰为约定的五个，`completed_at` 带 `Z`，失败时为 `null`，响应里没有异常类名与尝试次数；空列表；`page=10000` 合法；不合法的页码（0、负数、带 `+`、前导零、10001、超长、小数、指数、字母、空串、带空格、全角数字）都是固定的 422；明细的四个字段与按 SKU 升序，`variant_id` 为空的行照常返回，别的日期的明细不混入；失败记录的明细 `lines` 为空、不含异常类名；不存在与最大合法 ID 都是 404；不合法的路径 ID 都是固定的 422；200、401、404、422 都带 no-store，不写审计，应用不记日志，重置记录与明细不变。未覆盖：真 MySQL 上的排序规则与时间序列化。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/UX.md 0.10 的 A07 一致，字段取自 SHOP-TASK-032 的 stock_resets 与 stock_reset_lines；发现需求或设计本身有问题时停下并在 PR 里说明，不自行改
      - 读取与整形放在新文件 app/services/admin_stock_resets.py，接口在新文件 app/api/admin_stock_resets.py（app/main.py 挂上路由），两个接口都经 SHOP-TASK-036 的 require_admin（无有效后台会话 401 admin_session_required），只读，不要求 CSRF，不写审计、不写日志；两个接口的处理函数与依赖产生的响应（含错误）都带 Cache-Control 为 no-store，沿用 SHOP-TASK-027 的路由类 _NoStoreRoute（路径存在但方法不匹配时由框架返回的 405 不在此列）
      - GET /api/admin/stock-resets?page=N（1 到 10000 的整数，默认 1；不合法时 422，detail 为不含输入值的错误数组，写法同 app/api/pay.py 的 _language）返回 {total, page, page_size（30）, resets}，按马来西亚营业日期从新到旧，每项 id、business_date（YYYY-MM-DD）、result（succeeded 或 failed）、sku_count、completed_at（带 Z 的 UTC，失败时为 null）；不返回异常类名与尝试次数；page 超出时 resets 为空、total 照常
      - GET /api/admin/stock-resets/{reset_id} 返回该次重置的上述字段与 lines（按 SKU 升序，每项 sku、initial_stock、held_quantity、available_stock）；路径 ID 只接受不带符号与前导零、不超过 2147483647 的十进制整数，否则 422（{"type":"reset_id_invalid","loc":["path","reset_id"],"msg":"Stock reset ID should be a positive integer"}），不存在 404 not_found
      - tests/test_admin_stock_resets.py 用 TestClient 与 SQLite 内存库（开启外键检查，覆盖会话依赖）自建管理员、后台会话与重置记录（成功、失败各若干天，含规格已删除即 variant_id 为空的明细行），每条测试写明它守住的是 UX A07 的哪一句，验证——无会话时两个接口都 401；列表排序、每页 30 条与第 2 页、字段、失败时 completed_at 为 null、不含异常类名；明细的行与排序、variant_id 为空的行照常返回；路径 ID 不合法 422、不存在 404；所有响应 no-store；不写审计；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-052 记录段（接口与字段、排序与分页、偏离、验证到什么程度，并写明 A07 页面留给之后的任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改 app/services/stock_reset.py；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

# SHOP-TASK-046 后台退款申请列表按订单筛选

- [x] 按 `docs/HANDOFF.md` 0.35 记录的 Kelvin 2026-10-06 决定（A02 订单的待审退款数链接到只含该单申请的 A03 列表，为此退款申请列表接口加可选的订单内部 ID 筛选），给 SHOP-TASK-041 的 `POST /api/admin/refunds/query` 加可选的 `order_id`：`app/api/admin_refunds.py`（请求模型 `RefundQueryIn` 与处理函数）、`app/services/admin_refunds.py`（`query_refunds`）；测试见新文件 `tests/test_admin_refund_order_filter.py`。未改详情、批准与拒绝接口，未改 `tests/test_admin_refunds.py`、前端代码、`.platform/`、CI 或部署配置，未加表或迁移，未装依赖。设计闸门：不适用（只读筛选，不改退款、金额与审核规则，列表仍不含收货资料与理由）。拆分：不拆。
- 字段：请求体在可选的 `status` 与 `page` 之外新增可选的 `order_id`，为订单的内部整数 ID（1 到 2147483647，与申请内部 ID 同为 INT 列的上限，沿用 `MAX_REFUND_ID`）或 `null`，默认 `null`。沿用请求模型的 `strict=True`：布尔值、字符串（含数字字符串）与小数都 422；多出字段仍 422。错误经 `_body_errors`，只给位置、类型与固定消息，不回显所给的值。订单内部 ID 只在请求体里，不进路径或查询参数。
- 筛选规则：给出 `order_id` 时只列 `refund_requests.order_id` 等于它的申请，与状态筛选同时给时两者都满足才列出；排序（申请时间从新到旧、同时按 ID 从大到小）、每页 20 条、总数（符合全部条件的申请数）与每行字段不变。订单不存在或没有申请时返回 `total` 为 0 的空列表，不是 404。不给或为 `null` 时与之前完全相同。`query_refunds` 以带默认值 `None` 的仅关键字参数 `order_id` 接收订单 ID，现有按位置的调用方式与行为不变。接口仍只读、不要求 CSRF、不写审计，响应仍带 `no-store`，检查顺序（413 → 415 → 请求体 422 → 401 → 语言参数 422）与错误码不变。
- 偏离与取舍，请审阅（未改需求与设计，未发现须停下的需求或设计问题）：
  - 订单不存在与订单存在但没有申请不作区分，都是空列表；筛选只查退款申请表，不另查订单表。
  - 数字字符串（如 `"12"`）同样 422，与 `page` 的严格类型一致。
- 留给之后的任务：A03 页面按订单打开列表（从 A02 订单的待审退款数链接过来、带上订单内部 ID 调用本接口）留给之后的页面任务；A02 订单列表与详情由 SHOP-TASK-048 及之后的页面任务实现。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_admin_refund_order_filter.py` 用 TestClient 与 SQLite 内存库（StaticPool，打开外键检查并断言已打开，会话依赖换成测试自己的会话），自建管理员与后台会话，建订单与申请的辅助函数从 `tests/test_admin_refunds.py` 导入（不改该文件），每条测试（参数化的是每个用例）的文档字符串写明它守住的是 Kelvin 2026-10-06 的哪一项决定与验收约定，覆盖：两张订单申请交错时按订单筛选只返回该单申请、总数正确、行与不筛选时的对应行相同；按订单筛选时每页 20 条与第 2 页；与状态筛选组合（含组合后为空）；订单不存在、ID 为上限、订单存在但没有申请时为空列表；不给与 `null` 时返回全部且与不给请求字段时相同；服务函数不给关键字参数与给 `None` 结果相同、订单 ID 不能按位置传；`order_id` 为 0、负数、超过上限、字符串、数字字符串、布尔值与小数时 422 且只给位置、类型与固定消息、不回显所给的值；多出字段（订单号）仍 422 且不回显；列表接口的路径与查询参数里没有订单 ID；无后台会话时 401 且带 `no-store`；筛选结果（含空列表）带 `no-store` 且不需要 CSRF 头。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未在真 MySQL 上验证。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 按 docs/HANDOFF.md 0.35 记录的 Kelvin 2026-10-06 决定实现；POST /api/admin/refunds/query 的请求体在可选的状态与页码之外新增可选的 order_id（订单的内部整数 ID，1 到 2147483647，或 null；沿用请求模型的严格类型，布尔值、字符串与小数都 422），多出字段仍 422；订单内部 ID 只在请求体里，不进路径或查询参数
      - 给出 order_id 时只返回该订单的申请，可与状态筛选同时用，排序、每页 20 条、总数与每行字段不变；订单不存在或没有申请时返回 total 为 0 的空列表而不是 404；不给或为 null 时与现在完全相同
      - app/services/admin_refunds.py 的 query_refunds 以带默认值 None 的关键字参数接收订单 ID，现有调用方式与行为不变；接口仍只读、不要求 CSRF、不写审计，响应仍带 no-store，检查顺序与错误码不变（不合法的 order_id 与其他请求体错误一样是不回显请求内容的 422）
      - 不改详情、批准与拒绝接口；tests/test_admin_refunds.py 不改并照常通过
      - tests/test_admin_refund_order_filter.py 用 TestClient 与 SQLite 内存库（开启外键检查，覆盖会话依赖；自建管理员、后台会话、两张订单与各自的申请，可从 tests/test_admin_refunds.py 导入建数据的辅助函数，不改该文件）验证：按订单筛选只返回该单申请且总数正确、与状态筛选组合、订单不存在时空列表、null 与不给时返回全部、order_id 为 0、负数、超过上限、字符串、布尔值或小数时 422 且不回显所给的值、无后台会话时 401、响应带 no-store；每条测试写明它守住的是 Kelvin 的哪一项决定；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-046 记录段（字段、筛选规则、偏离、验证到什么程度，并写明 A03 页面按订单打开列表留给之后的页面任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

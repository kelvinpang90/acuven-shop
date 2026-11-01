# SHOP-TASK-182 当日库存调整（服务层与接口）

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 12 条（当日库存调整）；`docs/UX.md` 0.17 的 A04「当日库存调整」；`docs/HANDOFF.md` 0.76 的落实写法（库存已变动 409 带当前值、取值不合格 422 `stock_invalid`、SKU 不存在 404 `variant_not_found`；审计操作名 `admin_variant_stock_adjusted`）。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 12 条与 HANDOFF 0.76。不做前端，不改模型与迁移、`app/services/stock_reset.py`、现有测试与设计；页面 A04 另行登记。
- [x] 服务层（新增 `app/services/admin_stock_adjust.py`）：`adjust_available_stock(db, admin_account_id, variant_id, expected, new_value, now)`，参数写法照 `save_site_settings`，返回 `(SKU 代码, 调整后的当日可用库存)`。
  - 先不碰数据库检查 `expected` 与 `new_value` 都是 0 到 9,999 的整数（布尔值不算），不通过抛 `StockInvalid`；然后回滚此前的事务。
  - 两值不同：执行一条 `UPDATE product_variants SET available_stock = 新值 WHERE id = … AND available_stock = expected`（`adjust_stock_statement`）。命中一行即在同一事务里只查列值读出该行的 SKU 代码，写一条审计（`admin_variant_stock_adjusted`，对象类别 `product_variant`、对象 ID 为 SKU 规格 ID，旧值与新值为 `SKU 代码 available_stock 值`）后提交；未命中则回滚、读出当前值后再回滚：SKU 不存在抛 `VariantNotFound`，存在抛 `StockChanged`（带当前值）。
  - 两值相同：只读出当前值后回滚，等于 `expected` 即成功（不写入、不记审计），不等抛 `StockChanged`，不存在抛 `VariantNotFound`。
  - 停用的 SKU 照样可调；只改 `available_stock` 一列，不改 `daily_initial_stock`、订单、预留与商品的 `version`。不写日志。
- [x] 接口（新增 `app/api/admin_stock_adjust.py`，路由类为 SHOP-TASK-027 的 `_NoStoreRoute`，`app/main.py` 注册）：`POST /api/admin/variants/{variant_id}/available-stock`，请求体 8 KB 上限 413 → 不是 JSON 415（`_adjust_body`，即 `app/api/pay.py` 的 `_json_body`）→ `require_admin`（401）→ X-CSRF-Token（403 `csrf_failed`，照 `admin_coupons.py` 先声明 `CsrfDep`）→ 路径 ID（`_variant_id`，照 `admin_orders.py` 的 `_order_id`：不带符号与前导零、至多 10 位、不超过 2³¹−1，不合法 422 `variant_id_invalid`，固定消息）→ 请求体模型 `StockIn`（`expected`、`available_stock` 两个必填整数，`extra="forbid"`、`strict=True`，错误经 `_body_errors` 不回显）→ 服务层。`StockInvalid` 回 422 `{"detail": "stock_invalid"}`，`VariantNotFound` 回 404 `{"detail": "variant_not_found"}`，`StockChanged` 回 409 `{"detail": "stock_changed", "available_stock": 当前值}`；成功（含未变）回 200 `{"variant_id", "sku", "available_stock"}`。管理员 ID 在调用服务层之前取出。全部响应（含错误）带 no-store，框架的 405 不在此列；不写日志。
- [x] 测试：新增 `tests/test_admin_stock_adjust.py`（SQLite 内存库按模型建表，接口部分照 `tests/test_admin_shipping_rates_api.py`；引擎照 `tests/test_stock_reset.py` 由引擎发 BEGIN 并打开外键检查，以便在同一个库上调用 `reset_daily_stock`；每条测试的文档字符串写明它守住的第 12 条原句或本任务验收的约定）。覆盖服务层调整成功与审计格式、-1/10000/布尔值/字符串/浮点数抛 `StockInvalid` 且不发任何 SQL、0 与 9,999 两端、过期的调整前值抛 `StockChanged` 带当前值、SKU 不存在抛 `VariantNotFound`；`UPDATE` 语句按 MySQL 方言编译带 `id` 与 `available_stock` 的等值条件、不含 `daily_initial_stock`；接口没有会话 401、CSRF 缺少/为空/不一致 403 且先于路径与请求体的 422、超过 8 KB 413 先于 415 与登录检查、不是 JSON 415、路径 ID 不合法 422 不回显且先于请求体、布尔值/字符串/浮点数/null/缺字段/多出字段 422 不回显、-1 与 10000 422 `stock_invalid`（先于 SKU 是否存在）；调整成功 200 且只改当日可用库存、不改初始库存、商品版本号与订单、一条审计；连续两次写入各一条审计；其间有订单扣减时 409 带当前值、不写入；新值与当前相同 200 不写审计；新值与调整前相同但当前值已变 409；停用 SKU 可调；SKU 不存在 404；调整之后 `reset_daily_stock` 按初始库存减去仍有效的预留重算；所有响应带 no-store、应用不记日志；该前缀下只有这一个路由。
- 偏离与取舍：
  - 服务层的取值检查在函数开头、不依赖接口：接口的 strict 模型已拒绝布尔值、字符串与浮点数（请求体 422），服务层对它们同样抛 `StockInvalid`，直接调用服务层时也守住「布尔值不算」。
  - 未命中与两值相同时读出当前值之后都回滚，结束只读事务，不在会话里留着事务。
  - 服务层未捕获的数据库错误不另转成 503（同 `admin_shipping_rates.py`，验收未列出）。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。

## 登记时的验收标准（原文）

2026-11-01 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 服务层——新增 app/services/admin_stock_adjust.py，adjust_available_stock(db, admin_account_id, variant_id, expected, new_value, now) 参数写法照 save_site_settings；先不碰数据库检查 expected 与 new_value 都是 0 到 9,999 的整数（布尔值不算），不通过抛 StockInvalid；然后回滚此前的事务
      - 写入——expected 与 new_value 不同时执行一条 UPDATE product_variants SET available_stock = new_value WHERE id = variant_id AND available_stock = expected；命中一行则写一条审计（admin_variant_stock_adjusted，对象类别 product_variant，对象 ID 为 SKU 的 ID，旧值「SKU 代码 available_stock expected」、新值「SKU 代码 available_stock new_value」，SKU 代码取自同一事务里读出的该行）后提交；未命中则回滚，读出该 SKU 当前的当日可用库存——SKU 不存在抛 VariantNotFound，存在则抛 StockChanged（带当前值）；expected 与 new_value 相同时只读出当前值——等于 expected 即成功、不写入不记审计，不等抛 StockChanged，不存在抛 VariantNotFound；停用的 SKU 照样可调；不改 daily_initial_stock、订单、预留与商品的 version；返回 (SKU 代码, 调整后的当日可用库存)
      - 接口——新增 app/api/admin_stock_adjust.py 的 POST /api/admin/variants/{variant_id}/available-stock，步骤依次为请求体 8 KB 上限 413、不是 JSON 415、require_admin（401）、X-CSRF-Token（403 csrf_failed，照 app/api/admin_coupons.py 先声明 CsrfDep 依赖、再声明路径 ID 依赖）、路径 ID 照 app/api/admin_orders.py 的 _order_id 校验（不合法 422 不回显）、请求体模型（expected 与 available_stock 两个必填整数，strict，多出字段 422，照 app/api/pay.py 的 _body_errors 不回显）、服务层；StockInvalid 回 422 {"detail":"stock_invalid"}，VariantNotFound 回 404 {"detail":"variant_not_found"}，StockChanged 回 409 {"detail":"stock_changed", "available_stock":当前值}；成功回 200 {"variant_id", "sku", "available_stock"}；处理函数与依赖产生的全部响应（含错误）都带 no-store（SHOP-TASK-027 的路由类），框架返回的 405 不在此列；不写日志；app/main.py 注册新路由
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_admin_stock_adjust.py（SQLite 内存库按模型建表，接口部分照 tests/test_admin_shipping_rates_api.py 的写法），每条测试的文档字符串写明它守住的 DESIGN 1.18 第 12 条原句——调整成功改当日可用库存、不改每日初始库存与商品版本号、写一条审计且旧值新值格式正确；expected 不等于当前值时不写入、409 带当前值（模拟其间有订单扣减）；新值与当前相同时 200、不写审计；停用 SKU 可调；-1、10000、布尔值、字符串 422 stock_invalid 或请求体 422；SKU 不存在 404；没有会话 401、CSRF 不符 403、413 先于登录检查、415；响应带 no-store；调整之后每日重置照初始库存减去仍有效的预留重算（调用 app/services/stock_reset.py 的 reset_daily_stock 验证）；UPDATE 语句按 MySQL 方言编译带 available_stock 的等值条件
      - docs/task-records/SHOP-TASK-182.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）
      - 改动只落在 allowed_change_paths；不改模型与迁移、app/services/stock_reset.py、现有测试、前端、.platform/、CI 与部署配置，不装依赖，不改设计
```

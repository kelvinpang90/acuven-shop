# SHOP-TASK-016 每单限购：商品限购字段、迁移与计价接口校验

- [x] 按 `docs/DESIGN.md` 1.10（提交 `e3b3505`）「数据模型」的 Product / Variant 一行与「计价、优惠、积分与库存」第 7、8 条，给商品加每单限购件数并接入计价接口与商品详情：`app/models/catalog.py`、迁移 `alembic/versions/20261001_0008_purchase_limit.py`（revision `0008`，down_revision `0007`）、`app/services/catalog.py`、`app/services/checkout.py`、`app/api/checkout.py`（只改模块说明，件数上限取自服务层常量）；测试见 `tests/test_purchase_limit.py`，`tests/test_checkout_quote.py` 与 `tests/test_catalog_api.py` 按验收允许的范围调整。设计闸门：DESIGN 1.10（提交 `e3b3505`）。
- 新列与约束：`products.max_per_order` Integer，非空，服务端默认 `10`；检查约束 `ck_products_max_per_order_range`：`max_per_order >= 1 AND max_per_order <= 99`（只用比较与 AND，SQLite 与 MySQL 都支持）。只加在商品上，不按规格设置；其他列与约束未改。迁移 upgrade 先加列（已有商品由服务端默认值补成 10），再加检查约束；默认值保留，与模型一致；downgrade 先删检查约束再删列（MySQL 不许删除仍被检查约束引用的列）。`alembic check` 不比较检查约束与服务端默认值，这两项只经人工核对模型与迁移一致。
- 行状态与优先级（`POST /api/checkout/quote`）：
  - 每行件数上限由 10 改为 99（1 到 99 的整数，越界 422）；最多 20 行、SKU 不重复及其余请求校验不变。
  - 先把同一已发布商品的所有可购买行（正常或库存不足）的件数按商品合计；不可购买的行（SKU 不存在、停用或商品未发布）不计入任何商品的合计。合计超过该商品的 `max_per_order` 时，该商品的这些行都标为 `over_limit`，不拒绝整个请求。
  - 每行的状态按优先级取：`unavailable` → `over_limit` → `insufficient_stock` → `ok`。
  - 超出限购的行与库存不足的行一样返回商品信息、单价与行小计，并计入商品小计；`can_place_order` 仍要求全部行为 `ok` 且给了收货国家，所以有任何超出限购的行即不可下单。运费、合计与参考外币的计算不变。接口仍只发 SELECT、不需登录、不读写 cookie。
- 接口字段：
  - 计价接口：正常、库存不足、超出限购的行新增 `max_per_order`（该商品的每单限购件数）；`status` 新增取值 `over_limit`；不可购买的行仍只有 `sku`、`quantity`、`status`。
  - `GET /api/catalog/products/{slug}` 新增 `max_per_order`；商品列表与分类接口不变。
- 偏离：未改设计、需求、审阅稿、前端代码、CI 或部署配置；未发现设计本身的问题。说明几处取舍，请审阅：
  - `available_stock` 原先只在库存不足的行给出。按第 8 条「限购与库存各自独立生效」，现在凡是当日可用库存小于件数的行都给出可用库存，包括同时超出限购而标为 `over_limit` 的行；其余行仍为 null。页面据此可在用户减到限购之内前就提示缺货。
  - 下单时的整单校验（「下单时服务端重新校验，超出限购即拒绝整单，不部分下单」）留给游客下单任务（SHOP-TASK-020）；本任务不建订单、不预留库存。修改限购件数的后台接口（第 7 条只影响之后的计价与下单）留给管理后台业务接口任务。
  - 示例种子数据未改：已有的 30 件示例商品经迁移都取默认值 10。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并人工核对迁移中的列类型、可空性、服务端默认值、约束名与表达式同模型按命名约定生成的一致。`tests/test_purchase_limit.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）与 TestClient，每条测试的文档字符串写明它守住的设计或验收原句，覆盖：不给限购件数时库里为 10；0、100、-1 被检查约束拒绝，1 与 99 可写入；同一商品两个 SKU 合计等于限购两行都正常、超过限购两行都标为超出限购，另一件限购 1 的商品不受影响；默认限购 10 在计价中生效；单行超过限购；超出限购同时库存不足标为超出限购（仍给出可用库存），只缺货为库存不足；缺货的行计入商品合计；同一商品停用 SKU 与不存在的 SKU 不计入合计且只回 SKU、件数与状态；超出限购的行带商品信息、单价、行小计并计入商品小计，合计仍为小计加运费，不可下单，减到限购之内即可下单；三类有商品信息的行各带自己商品的 `max_per_order`；99 件接受、100 件 422；商品详情返回 `max_per_order`，商品列表与分类项没有。`tests/test_checkout_quote.py` 只把原 11 件被拒的用例改为 100 件被拒、件数上限的对照改为 99 件可接受（含两处文档字符串里的上限），并在 `_red_line` 的整体相等期望里补上 `max_per_order`；`tests/test_catalog_api.py` 只在商品详情的整体相等断言里补上 `max_per_order`。这些测试与迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 及 `alembic check` 只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；计价查询未在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.10「数据模型」Product / Variant 一行及「计价、优惠、积分与库存」第 7、8 条一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/catalog.py 的商品模型加每单限购件数：非空整数、服务端默认 10，检查约束保证 1 到 99（约束名按命名约定写出，表达式 SQLite 与 MySQL 都支持）；不按规格设置，不改其他列与约束
      - 新迁移文件 alembic/versions/20261001_0008_purchase_limit.py，revision 为 0008、down_revision 为 0007；upgrade 加列（已有商品取默认 10）与检查约束，downgrade 撤销；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行
      - 商品详情接口新增字段 max_per_order（该商品的每单限购件数）；商品列表与分类接口不变
      - 计价接口每行件数上限由 10 改为 99（1 到 99 的整数），最多 20 行与其余请求校验不变；件数越界仍为 422
      - 计价时把同一已发布商品的所有正常或库存不足的行按件数合计，合计超过该商品的每单限购件数时，这些行的状态改为超出限购（over_limit）；状态优先级为不可购买、超出限购、库存不足；不可购买的行不计入任何商品的合计
      - 所有返回商品信息的行（正常、库存不足、超出限购）另返回该商品的 max_per_order；超出限购的行与库存不足的行一样返回商品信息、单价与行小计并计入商品小计；有任何超出限购的行时不可下单；不可购买的行仍只返回 SKU、件数与状态
      - 接口仍只发 SELECT、不写数据库，不需要登录，不读写 cookie；不改运费、合计与参考外币的计算
      - tests/test_checkout_quote.py 只允许把件数上限相关的用例（原 11 件被拒）改为 99 件可接受、100 件被拒，并在整体相等的期望值里补上 max_per_order，不删除、不放宽其他测试；tests/test_catalog_api.py 只允许在商品详情的整体相等断言里补上 max_per_order；两个文件的其余测试全部照常通过
      - tests/test_purchase_limit.py 用 SQLite 内存库（开启外键检查）与 TestClient 验证：限购件数默认 10，0 与 100 被检查约束拒绝，1 与 99 可写入；同一商品两个 SKU 合计等于限购为正常、超过限购时两行都标为超出限购，另一商品的行不受影响；单行超过限购同样标出；超出限购同时库存不足时标为超出限购；不可购买的行不计入合计；超出限购的行计入商品小计且不可下单；各类行的 max_per_order；商品详情返回 max_per_order；99 件可接受、100 件 422；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-016 记录段（新列与约束、行状态与优先级、接口字段、偏离、验证到什么程度，并写明下单时的整单校验留给游客下单任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.10，提交 e3b3505），不合并、不直推
```

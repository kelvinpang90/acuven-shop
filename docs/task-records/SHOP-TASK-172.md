# SHOP-TASK-172 马来西亚运费改为西马、东马两档（模型、迁移、计价与后台维护）

- [x] 按 `docs/DESIGN.md` 1.17（提交 `80e3775`）「数据模型」`ShippingRate` 一行、「计价、优惠、积分与库存」第 9 条与第 10 条，以及 `docs/HANDOFF.md` 0.69 的落实写法，把运费表里马来西亚的 16 个州属行（`my_state`）换成西马 `MY-W` 与东马 `MY-E` 两行（`my_region`；东马为 MY-12、MY-13、MY-15，其余 13 个为西马），并在同一步里改模型、新迁移 0023、结账计价与后台维护的服务层与接口，以及按州属建运费行的测试。改动只在允许的 16 个路径内。不改前端、`app/services/regions.py`、`app/api/orders.py`、`app/api/checkout.py`、`app/services/ordering.py`、`app/services/member_ordering.py`、订单模型与已有迁移、`docs/TODO.md`、`.platform/`、CI、部署配置；未装依赖；未改设计。设计闸门：DESIGN 1.17（提交 `80e3775`）与 HANDOFF 0.69，马来西亚运费两档（碰运费金额与数据结构）。
- 模型（`app/models/shipping.py`）：去掉 `ZONE_MY_STATE`，新增 `ZONE_MY_REGION = "my_region"` 与 `MY_WEST_ZONE_CODE = "MY-W"`、`MY_EAST_ZONE_CODE = "MY-E"`。检查约束 `zone_code_matches_type` 改为 `(zone_type = 'my_region' AND zone_code IN ('MY-W', 'MY-E'))`，国家行与兜底行两段照旧；列、其余约束、唯一约束与 `sqlite_autoincrement` 不变。模块与类的说明改为两档并引用 DESIGN 1.17 第 10 条。
- 迁移 `alembic/versions/20261028_0023_my_shipping_tiers.py`（revision `0023`，down_revision `0022`，不 import app，常量自带，照 0004）：
  - 纯函数 `tier_fees(fees)`：州属代码 → 仙数，返回只有 `MY-W`、`MY-E` 两个键的字典（西马在前），每档取该档州属运费的最大值；出现 MY-01 到 MY-16 以外的代码、或某一档一个州属都没有时抛 `RuntimeError`。
  - 纯函数 `state_fees(fees)`：两档字典 → MY-01 到 MY-16 共 16 个键的字典；缺任一档（或出现两档以外的代码）时抛 `RuntimeError`。
  - `upgrade`：先读出全部 `my_state` 行并调用 `tier_fees` 完成判断，再 `op.drop_constraint` 删原检查约束、删除州属行、`op.create_check_constraint` 建新检查约束、写入两档行（版本号 1），最后 `print(..., flush=True)` 把两档的代码与运费仙数写一行到标准输出（`alembic/env.py` 不配置 logging，部署时 `alembic upgrade head` 的标准输出进入部署日志）。
  - `downgrade`：先读出两档行并调用 `state_fees` 完成判断，再删新约束、删除两档行、恢复原约束（与 0004 逐字相同）、写入 16 个州属行（版本号 1）。
  - 约束名用 `op.f("ck_shipping_rates_zone_code_matches_type")`；新约束文本 `REGION_CONSTRAINT` 与模型逐字一致。不写审计、不改订单。模块说明写明执行顺序的原因（改约束在 MySQL 上是 DDL，不随事务回滚，判断不通过时库须保持原样）与只在 MySQL 上执行。
- 计价（`app/services/shipping.py`）：`quote_shipping` 对马来西亚照旧要求 `MY_STATE_CODES` 之一（缺少或未知仍抛 `InvalidDestination`，`MY_STATE_CODES` 不变），按新增的 `EAST_MALAYSIA_STATE_CODES`（MY-12、MY-13、MY-15）换算成 `MY-E`，其余为 `MY-W`，取 `my_region` 的该行；该行不存在时抛 `ShippingRateMissing`。返回的 `zone_code` 与 `version` 为档的行的，下单写入订单快照的 `shipping_zone_code` 因此为 `MY-W` 或 `MY-E`。国家行、兜底行与 `reference_amount` 不变。
- 后台维护：
  - `app/services/admin_shipping_rates.py`：`AdminRates` 与 `save_admin_rates` 的马来西亚一组由 `states` 改名为 `malaysia`，取 `my_region` 的行，按 MY-W、MY-E 的顺序排列（不按代码排序）。第 9 条的两步核对、锁行顺序、行 ID 与版本号、审计（取值如「MY-W 800 v1」）与其余三组不变；第一步「只有保留」的拒绝消息随之改为 Malaysia。
  - `app/api/admin_shipping_rates.py`：读取响应与保存请求体里这一组的字段改名为 `malaysia`（字段顺序 `malaysia`、`other`、`countries`、`fx`，读取另带 `csrf_token`）；请求体仍带 `states` 时由 `extra="forbid"` 照多出字段回 422，沿用 `_body_errors` 不回显。其余检查顺序与回答不变。
- 测试：
  - `tests/test_shipping.py`：16 个州属各自换算到的档、运费与版本号；东马恰为三个州属、州属代码集合不变；两档行缺一时所选州属所属的档抛 `ShippingRateMissing`；州属代码缺少或未知（含把 `MY-W`、`MY-E` 当州属）仍拒绝；`my_state` 行被模型的检查约束拒绝；约束的拒绝用例改为档的代码。
  - `tests/test_demo_shipping_seed.py`：改为在空的 SQLite 连接上先执行 0004 再执行 0005（新模型的约束不收州属行），原有的 0005 断言照旧；删去对种子州属行直接计价的两条断言（保留马来西亚无参考外币一条）；新增对 0023 纯函数的验证——0005 实际写入的州属运费换算为 MY-W 800、MY-E 1500，同档不一致取最大，按现有的州属取，某档无州属或出现未知代码时 `RuntimeError`，`state_fees` 重建 16 个州属的种子运费且缺一档时 `RuntimeError`；另验证 revision 与新约束文本与模型逐字一致。
  - `tests/test_admin_shipping_rates.py`、`tests/test_admin_shipping_rates_api.py`：夹具改为两档行（东马先写入，以区分「西马在前」与按代码或行 ID 排序）；验证 `malaysia` 一组恰为两行且西马在前、改两档运费的保存与审计取值（「MY-W 800 v1」这样的代码、金额与版本号）、改东马后寄 MY-13 按新值计价、请求体只给 `states`、另带 `states`、`states` 里带值时 422 且不回显；原「州属」用例（取值不合格、数据已被改动、结构不合格）改为对应的档。
  - 每条新增或改写的测试在文档字符串写明守住的 DESIGN 1.17 第 9 条、第 10 条或 HANDOFF 0.69 的原句。
- 改动的现有测试与原因（模型的检查约束一改，按州属建的运费行写不进去）：
  - `tests/test_checkout_quote.py`、`tests/test_guest_order.py`、`tests/test_demo_payment.py`、`tests/test_member_ordering.py`、`tests/test_member_orders_api.py`、`tests/test_public_rate_limits.py` 里的 `MY-10` 运费行改为 `MY-W`、`MY-13` 改为 `MY-E`，费用与版本号照原值；对计价 `zone_code` 与订单 `shipping_zone_code` 的期望随之改为 `MY-E` / `MY-W`。
  - 依赖「MY-05 没有运费行」的四个缺运费用例（`test_checkout_quote.py` 的 `test_missing_shipping_row_is_503_without_details` 的 `malaysia-state-row-missing`、`test_guest_order.py` 的 `test_missing_shipping_rate_is_503`、`test_member_orders_api.py` 的 `test_quote_invalid_destination_and_missing_rate` 与 `test_order_missing_rate_is_503`）改为寄东马 MY-12 且夹具不建 `MY-E`：改成两档后 MY-05 归西马、有运费。`test_checkout_quote.py` 的 `_rates` 加关键字参数 `east`（默认 True）供该用例不建 MY-E，不回显的检查改为 `MY-12` 与 `MY-E`；相关文档字符串随之改写。其余断言不变。
  - 直接写入订单行的夹具里的 `shipping_zone_code`（如 `MY-10`）与收货地址的州属代码未改（已有订单的快照不改写，HANDOFF 0.69 第 5 点）。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - `state_fees` 除缺任一档外，对两档以外的键同样抛 `RuntimeError`（新约束下库里不会有这样的行，加上它只是让函数的输入严格等于「两档字典」）。
  - 后台按 MY-W、MY-E 排序时对两档以外的代码排在最后，不报错：MySQL 默认排序规则不区分大小写，检查约束不保证代码为大写，读取不应因此失败；正常数据下只有这两行。
  - 只在 upgrade 打印换算结果（第 10 条要求记入交接单的是这一项）；downgrade 不打印。
  - 测试里只构造请求体的几处无关字段（401、403、413 用例里的 `{"states": ...}` 垃圾请求体）未改名：它们验证的是先于结构校验的回答，与字段名无关。
- 留给之后：部署后由运营者从部署日志读出 0023 打印的两档运费，补记到交接单（HANDOFF 0.69 第 7 点）。A06 页面待视觉稿按两档重画、经 Kelvin 审阅后另行登记。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.17 第 9、10 条与 HANDOFF 0.69 原句自查；核对新约束文本在模型与 0023 之间逐字一致、恢复的原约束与 0004 逐字一致。pytest 只验证迁移 0023 的两个纯函数（及其 revision 与约束文本），不在 SQLite 上执行 0023；0023 的 upgrade、downgrade 再 upgrade 的往返与 `alembic check` 由 PR 的必需 CI 检查 backend 在 MySQL 8 上按种子数据执行。测试由该检查执行（Worker 沙箱不跑后端测试）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-29 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 模型——app/models/shipping.py 去掉 ZONE_MY_STATE，新增 ZONE_MY_REGION（值 my_region）与两档的区域代码常量（MY-W、MY-E）；ShippingRate 的检查约束 zone_code_matches_type 改为（zone_type 为 my_region 且 zone_code 为 MY-W 或 MY-E）或国家行或兜底行（后两者照旧），迁移 0023 建的新约束与之逐字一致；列、其余约束、唯一约束与 sqlite_autoincrement 不变；模块与类的说明随之改为两档并引用 DESIGN 1.17 第 10 条
      - 迁移——新增 alembic/versions/20261028_0023_my_shipping_tiers.py（revision 为 0023，down_revision 为 0022；不 import app，照 0004 的写法自带常量）。模块里两个纯函数——tier_fees 接收州属代码到运费仙数的映射，返回只有 MY-W 与 MY-E 两个键的字典（每档取该档州属运费的最大值；出现 MY-01 到 MY-16 以外的代码、或某一档一个州属都没有时抛 RuntimeError）；state_fees 接收这样的两档字典，返回 MY-01 到 MY-16 共 16 个键的字典（缺任一档时抛 RuntimeError）。upgrade 先读出全部 my_state 行并调用 tier_fees 完成判断，再删去原检查约束、删除州属行、建新检查约束、写入两档行（版本号 1），并用 print（flush 为真）把换算结果（两档的代码与运费仙数）写一行到标准输出（alembic/env.py 不配置 logging，INFO 日志不会输出；部署时 alembic upgrade head 的标准输出进入部署日志）；downgrade 先读出两档行并调用 state_fees 完成判断，再删去新约束、删除两档行、恢复原约束、写入 16 个州属行（版本号 1）（与 DESIGN 1.17 第 10 条的顺序一致）；约束名用 op.f 照 0004；不写审计、不改订单；模块说明写明执行顺序的原因（改约束在 MySQL 上不随事务回滚，判断不通过时库须保持原样）与只在 MySQL 上执行
      - 计价——app/services/shipping.py 的 quote_shipping 对马来西亚照旧要求 MY_STATE_CODES 之一（缺少或未知仍抛 InvalidDestination，MY_STATE_CODES 与 app/services/regions.py 不变），按新增的东马州属集合（MY-12、MY-13、MY-15）换算成 MY-E，其余为 MY-W，取 my_region 的该行；该行不存在时抛 ShippingRateMissing（不以零运费代替）；返回的 ShippingQuote 的 zone_code 与 version 为该档行的；国家行、兜底行与 reference_amount 不变；下单写入订单快照的 shipping_zone_code 因此为 MY-W 或 MY-E，app/services/ordering.py 与 member_ordering.py 不改
      - 后台维护——app/services/admin_shipping_rates.py 的 AdminRates 与 save_admin_rates 里马来西亚一组由 states 改名为 malaysia，取 my_region 的行，按 MY-W、MY-E 的顺序排列（不按代码排序）；第 9 条的两步核对、锁行顺序、行 ID 与版本号、审计（取值写成「MY-W 800 v1」这样的代码、金额与版本号）与其余三组不变。app/api/admin_shipping_rates.py 的读取响应与保存请求体里这一组的字段同样改名为 malaysia（字段顺序为 malaysia、other、countries、fx，读取另带 csrf_token），请求体仍带 states 时照多出字段回 422、沿用 _body_errors 不回显；其余检查顺序与回答不变
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——tests/test_shipping.py 验证 16 个州属各自换算到的档与版本号、东马恰为三个州属、两档行缺一时抛 ShippingRateMissing、州属代码缺少或未知仍拒绝、my_state 行被模型的检查约束拒绝；tests/test_demo_shipping_seed.py 改为在空的 SQLite 连接上先执行迁移 0004 再执行 0005（新模型的约束不收州属行，原有的 0005 断言照旧），删去对种子州属行直接计价的两条断言，改为对迁移 0023 的纯函数验证——种子运费换算为 MY-W 800、MY-E 1500，同档不一致取最大，某档无州属或出现未知代码时 RuntimeError，state_fees 重建 16 个州属的运费且缺一档时 RuntimeError；tests/test_admin_shipping_rates.py 与 tests/test_admin_shipping_rates_api.py 的夹具改为两档行，验证 malaysia 一组为两行且西马在前、改两档运费的保存与审计取值、请求体带 states 时 422；每条新增或改写的测试写明守住的是 DESIGN 1.17 第 9 条、第 10 条或 HANDOFF 0.69 的哪一句
      - 现有测试只在必要时修改——tests/test_checkout_quote.py、tests/test_guest_order.py、tests/test_demo_payment.py、tests/test_member_ordering.py、tests/test_member_orders_api.py、tests/test_public_rate_limits.py 里按州属建的运费行改为对应的档的行（MY-10 等西马州属对应 MY-W，MY-13 对应 MY-E，费用与版本号照原值），对 zone_code 与订单 shipping_zone_code 的期望随之改为档的代码；依赖 MY-05 没有运费行的缺运费用例（test_checkout_quote.py 的 test_missing_shipping_row_is_503_without_details 的 malaysia-state-row-missing、test_guest_order.py 的 test_missing_shipping_rate_is_503、test_member_orders_api.py 的 test_quote_invalid_destination_and_missing_rate 与 test_order_missing_rate_is_503）改为东马州属（如 MY-12）且夹具不建 MY-E——只建 MY-W 的夹具照旧，test_checkout_quote.py 的 _rates 加一个不建 MY-E 的关键字参数供该用例使用，该用例不回显的检查随之改为 MY-12 与 MY-E，相关文档字符串里「MY-05 没有运费行」的说法随之改；其余断言不变；直接写入订单行的夹具里的 shipping_zone_code 与收货地址的州属代码不改（已有订单的快照不改写）
      - docs/task-records/SHOP-TASK-172.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；模型、迁移 0023 的两个纯函数与执行顺序、计价换算、后台一组改名、改动的现有测试与原因、偏离、验证到什么程度——pytest 只验证迁移的两个纯函数、不在 SQLite 上执行 0023，CI 的必需检查 backend 在 MySQL 8 上按种子数据对迁移做 upgrade、downgrade 再 upgrade 的往返与 alembic check，写明这一点；并写明 A06 页面待视觉稿重画后另行登记）
      - 改动只落在 allowed_change_paths；不改前端、app/services/regions.py、app/api/orders.py、app/api/checkout.py、订单模型与已有迁移；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.17 提交 80e3775 与 HANDOFF 0.69），不合并、不直推
```

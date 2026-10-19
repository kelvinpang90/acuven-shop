# SHOP-TASK-007 运费区与演示汇率的数据模型、示例费率与查询函数

- [x] 按 `docs/DESIGN.md` 1.8（提交 `2b68ad0`）「数据模型」的 ShippingRate / DemoFxRate 一行与「边界与原则」建立运费区与演示汇率两张表：`app/models/shipping.py`、迁移 `alembic/versions/20260930_0004_shipping_fx.py`（revision `0004`，down_revision `0003`）、数据迁移 `alembic/versions/20260930_0005_demo_shipping_fx.py`（revision `0005`，down_revision `0004`）、只读查询 `app/services/shipping.py`；测试见 `tests/test_shipping.py` 与 `tests/test_demo_shipping_seed.py`
- 两张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；检查约束只用 LENGTH、LIKE、比较与 IN）：
  - `shipping_rates`：`id`（`pk_shipping_rates`）、`zone_type` String(10)（`my_state` / `country` / `other`）、`zone_code` String(5)（唯一 `uq_shipping_rates_zone_code`）、`fee_sen` Integer（MYR 整数仙）、`version` Integer，全部非空。检查约束：
    - `ck_shipping_rates_zone_code_matches_type`：`(zone_type = 'my_state' AND LENGTH(zone_code) = 5 AND zone_code LIKE 'MY-%') OR (zone_type = 'country' AND LENGTH(zone_code) = 2 AND zone_code <> 'MY') OR (zone_type = 'other' AND zone_code = 'OTHER')`，同时把类型限定为这三种
    - `ck_shipping_rates_fee_sen_non_negative`：`fee_sen >= 0`
    - `ck_shipping_rates_version_positive`：`version >= 1`
  - `demo_fx_rates`：`id`（`pk_demo_fx_rates`）、`country_code` String(2)（唯一 `uq_demo_fx_rates_country_code`）、`currency_code` String(3)、`currency_decimals` Integer、`rate` Numeric(18, 6, asdecimal=True)（1 MYR 等于多少该币种；全项目唯一用定点小数存的数，金额仍一律整数仙）、`version` Integer，全部非空。检查约束：
    - `ck_demo_fx_rates_country_code_valid`：`LENGTH(country_code) = 2 AND country_code <> 'MY'`
    - `ck_demo_fx_rates_currency_code_valid`：`LENGTH(currency_code) = 3 AND currency_code <> 'MYR'`
    - `ck_demo_fx_rates_currency_decimals_range`：`currency_decimals >= 0 AND currency_decimals <= 3`
    - `ck_demo_fx_rates_rate_positive`：`rate > 0`
    - `ck_demo_fx_rates_version_positive`：`version >= 1`
  - 0004 的 downgrade 先删 `demo_fx_rates` 再删 `shipping_rates`。
- 示例数值（0005 写入，版本号均为 1，全部是迁移内常量，用 `sa.table` 轻量表定义与 `op.bulk_insert` 写入，不 import app 的模型）：
  - 运费（仙）：州属 MY-12、MY-13、MY-15 为 1500，MY-01 至 MY-11、MY-14、MY-16 为 800；国家 SG 2000、BN 2500、TH 3000、CN 3500、JP 4500、AU 5000、US 6000、GB 6000；兜底 OTHER 8000。共 25 行。
  - 汇率（虚构的固定演示值，不是市场汇率；国家、币种、小数位、1 MYR 等于）：SG SGD 2 0.310000；TH THB 2 7.600000；CN CNY 2 1.650000；JP JPY 0 34.000000；AU AUD 2 0.350000；US USD 2 0.230000；GB GBP 2 0.170000；HK HKD 2 1.800000。共 8 行。BN 刻意不配汇率（只显示 MYR），HK 刻意不配国家运费（用兜底运费）。
  - downgrade 按区域代码与国家代码删除这批行；只保证在写入后未经后台编辑的库上成立，部署只执行 upgrade、从不降级。
- 查询函数（`app/services/shipping.py`，只发 SELECT，不写库、不缓存费率）：
  - `quote_shipping(session, country_code, state_code) -> ShippingQuote(fee_sen, zone_code, version)`。国家代码须是两位大写 ASCII 字母，否则抛 `InvalidDestination`（`ValueError` 子类），不转换大小写。`MY` 须给 `MY-01` 到 `MY-16` 之一，缺少或未知抛 `InvalidDestination`；其他国家给了州属代码（含空串）抛 `InvalidDestination`，有国家行用国家行，否则用 `OTHER` 行。兜底行不存在、或马来西亚州属代码合法但表里没有该行时抛 `ShippingRateMissing`（`LookupError` 子类），不返回零运费，州属缺行也不退回兜底。
  - `reference_amount(session, country_code, amount_sen) -> FxReference(currency_code, currency_decimals, amount_minor, version) | None`。国家代码校验同上；金额为负抛 `ValueError`，不是 `int`（含 `bool`、`float`）抛 `TypeError`，在查库前校验；`MY` 与没有汇率行的国家返回 `None`，不猜测、不请求外部汇率。
  - `convert_sen(amount_sen, rate, currency_decimals) -> int`：不碰数据库的纯函数。`amount_sen × rate ÷ 100` 再移到该币种最小单位，用 `decimal` 的 `ROUND_HALF_UP` 取整（恰好半个最小单位进一），精度按输入位数留足，唯一的舍入在最后一步，全程不用浮点。`rate` 不是 `Decimal`（含 `float`、`int`、字符串）抛 `TypeError`；非有限、不大于零、或去掉尾随零后超过 6 位小数抛 `ValueError`；小数位须是 0 到 3 的 `int`。
- 偏离：无，未改设计、接口、`app/main.py` 与前端代码。说明几处取舍，请审阅：
  - 数据库不保证大小写与字母：MySQL 默认排序规则不区分大小写（`'my'` 与 `'MY'` 比较相等、唯一约束也按不区分大小写判断），SQLite 的 LIKE 对 ASCII 不区分大小写而 `<>` 区分；MySQL 的 LENGTH 按字节计。代码是大写字母由写入方（本任务的 0005，之后的后台维护）与查询函数校验，`tests/test_demo_shipping_seed.py` 确认 0005 写入的代码都是大写。
  - 州属行的检查约束按验收标准只要求以 `MY-` 开头且长 5 位，`MY-99` 这类代码数据库不拒绝；是否属于 MY-01 到 MY-16 由写入方与查询函数校验。国家代码是否真实存在留给之后带国家列表的结账任务。
  - 类型与代码的搭配写成一个检查约束，它同时拒绝三种以外的类型，没有另设类型枚举约束。
  - 汇率「超过 6 位小数」按数值判断：`Decimal("0.3100000")` 去掉尾随零后只有 2 位，接受；`Decimal("0.3100001")` 拒绝。
  - SQLite 没有原生定点小数，SQLAlchemy 经浮点存取并发出 SAWarning；两个测试文件用 `filterwarnings` 只忽略这一条，并只断言 6 位小数的示例汇率读回相等，不在 SQLite 上断言完整 18 位精度的往返。MySQL 上是 DECIMAL(18, 6)，PyMySQL 直接返回 Decimal。
  - 版本号：本任务的行一律为 1；每次修改一行费率把该行版本号加一由之后的后台维护任务实现，本任务不实现修改。
- 验证到什么程度：人工逐条对照验收标准自查，并逐个核对 0004 的列类型、可空性、约束名与表达式、表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）；人工按实现的算法推算算例（1950 仙 × 0.310000 = 6.045 SGD → 605；25 仙 × 34 = 8.5 JPY → 9；1949 仙 → 604；1000 仙 × 1.8 → HKD 1800）。`tests/test_shipping.py` 用 SQLite 内存库按模型建表，覆盖：三种合法行与零运费可写入（对照）；负运费、运费行与汇率行版本号 0、类型与代码搭配不符（12 例，含国家行为 MY、未知类型）、重复区域代码、零与负汇率、小数位 -1 与 4、币种 MYR 与长度不对、汇率国家 MY 与长度不对、重复汇率国家各被对应的约束拒绝；汇率读回是相等的 Decimal；运费查询的州属、未知与缺少州属（含小写 `my-01`）被拒、州属缺行报错、非马来西亚带州属被拒、国家行、兜底、兜底缺失报错、非法国家代码（含小写、全角、非字符串）被拒、改行后立即读到新值、查询只发 SELECT；参考外币的有汇率、无汇率、马来西亚返回空、非法国家代码与金额被拒；换算钉住三个算例与半位以下舍去，拒绝负数、`bool`、`float` 金额与 `float`、`int`、字符串、零、负、超过 6 位小数、NaN、无穷的汇率。`tests/test_demo_shipping_seed.py` 经 Alembic 的 `Operations` 执行 0005 的 upgrade，查询实际写入的行逐项核对 16 个州属、8 个国家、兜底与 8 条汇率（期望值按验收标准另写，不取迁移常量）、行数恰好、版本号均为 1、代码均为大写，并用查询函数验证 SG、BN、HK、FR（两表都没有）与 MY-10、MY-13；再执行 downgrade，示例行删净、另插入的 NZ 运费行与汇率行保留。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；查询函数未在真 MySQL 上执行（CI 的 MySQL 只用于迁移检查）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.8「数据模型」ShippingRate / DemoFxRate 一行及「边界与原则」一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/shipping.py 定义运费表与演示汇率表，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项；app/models/__init__.py 导出它们
      - 运费表每行有区域类型（马来西亚州属、国家、其他国家兜底三种）、全表唯一且非空的区域代码、运费（整数仙）与版本号；州属行的区域代码是 ISO 3166-2 马来西亚代码 MY-01 到 MY-16，国家行是两位大写字母（ISO 3166-1 两位代码的格式）且不能是 MY，兜底行的区域代码固定为 OTHER；类型与代码的搭配（州属行以 MY- 开头且长 5 位、国家行长 2 位且不是 MY、兜底行等于 OTHER）、运费不小于零、版本号不小于 1 都由显式命名的检查约束保证，只用 LENGTH、LIKE、比较与 IN，不用 GLOB、REGEXP 或指定排序规则；MySQL 默认排序规则不区分大小写，大小写与字母是否合法由写入方（本任务的数据迁移、之后的后台维护）与查询函数校验，不由数据库保证，PR 里如实说明；代码是否是真实存在的国家不在本任务校验，留给之后带国家列表的结账任务
      - 演示汇率表每行有收货国家代码（两位大写字母，全表唯一，不能是 MY）、币种代码（三位大写字母，ISO 4217 的格式，不能是 MYR）、币种小数位（0 到 3）、汇率与版本号；汇率含义为 1 MYR 等于多少该币种，列类型为 Numeric(18, 6, asdecimal=True)，大于零；长度、不等于 MY 与 MYR、小数位范围、汇率大于零、版本号不小于 1 都由显式命名的检查约束保证，表达式限制与大小写说明同运费表。汇率不是金额，这是全项目唯一用定点小数存的数；金额仍一律整数仙
      - 版本号：本任务的行一律从 1 开始；之后的后台维护任务每次修改一行费率把该行版本号加一，本任务不实现修改
      - 新迁移文件 alembic/versions/20260930_0004_shipping_fx.py（revision 为 0004、down_revision 为 0003）建两张表，downgrade 删除两张表；迁移按命名约定逐个写出约束名，PR 正文逐表列出检查约束与表选项，供审查核对与模型一致
      - 新数据迁移 alembic/versions/20260930_0005_demo_shipping_fx.py（revision 为 0005、down_revision 为 0004），数据写成迁移文件内的常量，用迁移内的轻量表定义写入，不 import app 的模型；upgrade 写入下面三条的全部示例行、版本号均为 1，downgrade 按区域代码与国家代码删除这批行；部署只执行 upgrade、从不降级，downgrade 只保证在写入后未经后台编辑的库上成立
      - 示例运费（整数仙）：马来西亚州属 MY-12、MY-13、MY-15 为 1500，其余 13 个州属与联邦直辖区为 800；国家 SG 2000、BN 2500、TH 3000、CN 3500、JP 4500、AU 5000、US 6000、GB 6000；兜底 OTHER 8000
      - 示例汇率（虚构的固定演示值，不是市场汇率；国家、币种、小数位、1 MYR 等于）：SG SGD 2 0.310000；TH THB 2 7.600000；CN CNY 2 1.650000；JP JPY 0 34.000000；AU AUD 2 0.350000；US USD 2 0.230000；GB GBP 2 0.170000；HK HKD 2 1.800000。BN 刻意不配汇率，用来演示只显示 MYR；HK 刻意不配国家运费，用来演示兜底运费
      - app/services/shipping.py 提供运费查询：接收会话、收货国家代码与马来西亚州属代码，返回运费（整数仙）、所用区域代码与版本号。马来西亚必须给 16 个州属代码之一，缺少或未知即拒绝；其他国家给了州属代码即拒绝，有国家行用国家行，否则用兜底行；国家代码不是两位大写字母即拒绝，不自动转换大小写；兜底行不存在时明确报错，不返回零运费
      - 同一文件提供参考外币查询：接收会话、收货国家代码与 MYR 金额（整数仙），该国有汇率时返回币种代码、币种小数位、以该币种最小单位计的整数参考金额与版本号，没有汇率（含马来西亚）时返回空，不猜测、不请求外部汇率；换算为仙乘以汇率除以 100，用 decimal 按四舍五入、恰好半个最小单位进一取整到该币种小数位，全程不用浮点；换算本身写成不碰数据库的纯函数，汇率参数只接受 Decimal（float 即拒绝），不大于零或超过 6 位小数即拒绝；金额为负、不是 int（含 bool、float）即拒绝
      - 查询函数只读、不写数据库、不缓存费率；不加接口、不改 app/main.py
      - tests/test_shipping.py 用 SQLite 内存库按模型建表，验证每个检查约束与唯一约束各至少一个被拒的反例（负运费、版本号 0、类型与代码搭配不符、国家行为 MY、重复区域代码、汇率为零或为负、小数位 4、币种 MYR、汇率国家为 MY、重复汇率国家）；运费查询覆盖州属、未知与缺少州属被拒、非马来西亚带州属被拒、国家行、兜底、兜底缺失报错、非法国家代码（含小写）被拒；换算钉住算例 1950 仙按 0.310000 得 SGD 605（6.045 半位进一）、25 仙按 34.000000 得 JPY 9（8.5 半位进一）、0 仙得 0，示例汇率经 SQLite 读回是与写入相等的 Decimal（完整 18 位精度的往返不在 SQLite 上断言）；无汇率与马来西亚返回空；负数、bool 与 float 金额被拒，float 汇率、零或负汇率、超过 6 位小数的汇率被拒；每条测试写明它守住的是哪一句规则
      - tests/test_demo_shipping_seed.py 用 SQLite 内存库按模型建表，经 Alembic 的 Operations 执行 0005 迁移的 upgrade，查询实际写入的行逐项核对上面的示例运费与示例汇率（16 个州属齐全、行数恰好、版本号均为 1），并用查询函数验证 SG、BN（有运费无汇率）、HK（兜底运费有汇率）、一个两表都没有的国家（兜底运费、无汇率）与一个马来西亚州属的结果；再执行 downgrade 验证这批行全部删除、另插入的非示例行保留；每条测试写明它守住的是哪一句规则
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异；以上测试与已有后端测试都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-007 记录段（两张表与关键约束、示例数值、查询函数的输入输出、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.8，提交 2b68ad0），不合并、不直推
```

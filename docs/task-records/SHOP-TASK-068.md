# SHOP-TASK-068 短信每日总量与费用预算的原子预占与结算

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「失败、并发与重试」第 4 条（短信每日总量及费用预算另在本项目 MySQL 库以按日原子计数和预算记录兜底：发送前按目的地预占保守的单次最高费用，未知费用时停发；结算后更新记录，防 Redis 重置后超过日上限；MySQL 不可用时短信停发）与 `docs/HANDOFF.md` 0.41 记录的 Kelvin 2026-10-08 决定（全站每天 200 条、费用上限 20 美元；单次预占按目的地配置的最高单价，未配置的目的地停发；按预占价结算；各项都做成配置项），在 SHOP-TASK-012 的 `sms_daily_usage` 表上新建 `app/services/sms_budget.py`（预占、结算、释放），配置项加在 `app/core/config.py`；测试见 `tests/test_sms_budget.py`。不发短信，未做 Redis 限流与接口，未加表或迁移（未改 `app/models/`），未改 `.env.example`、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41。
- 预占 `reserve_sms(db, settings, phone_e164, now) -> ReserveResult(status, reservation)`：日期为 `now`（不带时区的 UTC）加 8 小时的马来西亚日期（UTC 16:00:00 起即下一天，`malaysia_date`）。先以不加锁的普通查询找当日行，没有时在保存点里插入零值行（条数与两项金额都为 0），撞上日期唯一约束即回滚保存点（只认 MySQL 错误码 1062 且键名为 `uq_sms_daily_usage_usage_date`，或 SQLite 对 `sms_daily_usage.usage_date` 的唯一约束消息；其他完整性错误原样抛给调用方）；然后以 `SELECT … FOR UPDATE` 锁定当日行（`populate_existing`，会话里的旧值以读到的最新值覆盖）再判断：目的地按 E.164 开头的国家呼叫码取单次最高费用（`+60` 马来西亚、`+65` 新加坡；国家呼叫码是前缀码，按开头判定不会误认），未配置（0）或其他目的地为 `unknown_cost`；`sent_count + 1` 超过每日条数上限，或 `reserved + settled + 本次费用` 超过每日费用上限为 `over_budget`；两种拒绝都不改数。否则 `sent_count` 加 1、`reserved_micro_usd` 加本次费用，返回 `reserved` 与凭据 `Reservation(usage_date, cost_micro_usd)`。
- 结算 `settle_sms(db, reservation)`：按凭据金额（预占价，Kelvin 2026-10-08 决定）从 `reserved_micro_usd` 移到 `settled_micro_usd`，条数不变。释放 `release_sms(db, reservation)`（短信未发出时用）：从 `reserved_micro_usd` 减去凭据金额、`sent_count` 减 1。两者都按凭据里的日期以 `SELECT … FOR UPDATE` 锁定那一行，不看当前时间、不跨日；该行不存在、凭据金额不是正整数（含布尔值）、已预占小于凭据金额、或释放时条数已为 0，都抛 `SmsBudgetError`（消息固定）且不改数。
- 三个函数只 flush、不提交，由调用方提交或回滚；MySQL 出错（含锁等待超时与死锁）时异常原样交给调用方，调用方据此停发。
- 配置项（前缀 `SHOP_`，名称与默认值同 `.env.example` 已登记的四项）：`sms_daily_count_limit`（默认 200）、`sms_daily_cost_limit_micro_usd`（默认 20000000，即 20 美元）、`sms_max_cost_micro_usd_my` 与 `sms_max_cost_micro_usd_sg`（默认 0 即未配置、该目的地停发）。金额一律是整数微美元。
- 不泄露：本模块不写日志；手机号只用来取国家呼叫码，不进返回值、凭据与异常消息。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 验收写的是「找到当日行……以 SELECT … FOR UPDATE 锁定后判断」。先找用不加锁的普通查询、再加锁读取：MySQL 默认的 REPEATABLE READ 下，对不存在的行加锁读取会取间隙锁，两个会话同时取间隙锁再插入会死锁。普通查询读快照、可能漏看别人刚提交的行，此时插入撞上唯一约束，回滚保存点后的加锁读取读最新已提交的数据，能找到它。
  - 号码格式（加号、首位非 0、共 2 到 15 位数字）在访问数据库之前校验，不成立抛 `ValueError`（编程错误，消息不含号码；调用方须先以 SHOP-TASK-011 的规范化得到号码）；费用的判断仍在锁定之后。`unknown_cost` 也会留下当日的零值行（数都不改），由调用方的提交决定是否保留。
  - 单次最高费用配成负数按未配置处理（`unknown_cost`）；条数或费用上限配成 0 或负数时一律 `over_budget`。
  - 验收的「超过一仙拒绝」：本表金额单位是微美元，测试取最小单位，多出 1 微美元即拒绝。
  - 结算与释放只按当日合计防护：同一凭据被结算或释放两次时，只要当日还有别的预占，第二次也会成功（从别人的预占里扣），表里没有逐条预占的记录可以区分。调用方（SHOP-TASK-069）须保证每张凭据只结算或释放一次。结算不检查条数（条数在预占时已计入）。
- 审阅修改第 1 轮：插入当日零值行时原先吞掉所有 `IntegrityError`，其他完整性错误会被误转成后面的 `SmsBudgetError`。改为只在确认是日期唯一约束冲突时回滚保存点后继续（识别方式见上文预占一条），其他完整性错误原样抛给调用方（调用方据此停发）；补测试：插入因别的约束失败时原 `IntegrityError` 抛出且不留行，以及识别函数对 MySQL 1062 日期键（带与不带表名前缀）、1062 其他键、其他错误码与 SQLite 其他约束消息的判定。
- 留给之后的任务：调用预占、结算与释放的发送规则、停发记录与 Redis 限流（SHOP-TASK-069）；发送短信验证码接口（SHOP-TASK-070）；超过每日总量或费用阈值时的运营告警邮件（另行登记的运营告警任务）。上线前运营者须在服务器私有配置里填写两国的单次最高费用，否则两国一律停发。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11「失败、并发与重试」第 4 条与 HANDOFF 0.41 的决定原句自查。`tests/test_sms_budget.py` 用 SQLite 内存库按模型建表（每个连接打开外键检查并断言已打开；引擎设置与 `tests/test_stock_reset.py` 相同，由引擎发 `BEGIN`，保存点与 MySQL 上一致），每条测试的文档字符串写明它守住的设计原句、Kelvin 决定或验收条目，覆盖：马来西亚日期在 UTC 15:59:59 与 16:00:00 前后切换（含跨年），两侧的预占分记两行；首次预占插入当日行，已有行时在原行累加；先找漏看已有行时插入撞约束、只回滚保存点后照常预占且不重复插行（以替换模块里的查找函数模拟）；插入因日期唯一约束以外的完整性错误失败时原异常抛给调用方、不留行（以 SQLite 触发器模拟），MySQL 错误码与键名的识别以构造的异常对象验证；按目的地取费用；两国未配置、配成负数、其他国家与默认配置都为 `unknown_cost` 且不改数；条数恰到 200 放行、第 201 条拒绝且不改数；费用恰到上限放行、多 1 微美元拒绝且不改数；已结算金额计入上限；调低的上限即时生效；结算与释放后的三个数，释放还回的条数可再预占；跨日后按凭据日期结算与释放、不动下一天的行；重复结算或释放、已预占不足、条数为 0、凭据金额为 0、负数或布尔值、行不存在都抛错且不改数；三个函数只 flush（调用方回滚后不留数）；数据库出错时原异常抛给调用方；非 E.164 号码报错且消息不含号码；返回值与异常消息不含号码、不产生日志记录；配置默认值与 `SHOP_` 环境变量。并发：SQLite 会忽略 `FOR UPDATE`，也不复现 MySQL 的阻塞等待；测试只用两个会话按先后顺序预占，验证后到者在锁定读取时读到前者提交后的数（即使它的会话里留着旧值）、合计不超过上限。MySQL 上的行锁排队、间隙锁与唯一约束冲突的实际行为未在真实 MySQL 上验证。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/sms_budget.py——预占按马来西亚时间（UTC+8）的日期找到当日行（不存在时在保存点里插入零值行，撞上日期唯一约束即回滚保存点再找），以 SELECT … FOR UPDATE 锁定后判断：目的地（按 E.164 的国家呼叫码 60 或 65）的单次最高费用未配置或为 0 时拒绝（unknown_cost）；sent_count 加 1 超过每日条数上限、或 reserved 加 settled 加本次费用超过每日费用上限时拒绝（over_budget）；否则 sent_count 加 1、reserved 加本次费用，返回预占凭据（日期与金额）
      - 结算把这次的金额从 reserved 移到 settled（按预占价，Kelvin 2026-10-08 决定）；释放把金额从 reserved 减去并把 sent_count 减 1（短信未发出时用）；结算与释放都锁定同一日期行、不跨日、金额与计数不会成为负数（库里已有非负约束，代码另行防护并在违反时抛错）；三个函数只 flush、不提交，由调用方提交；MySQL 出错时异常交给调用方（调用方据此停发）
      - 配置项加在 app/core/config.py（前缀 SHOP_）——sms_daily_count_limit（默认 200）、sms_daily_cost_limit_micro_usd（默认 20000000）、sms_max_cost_micro_usd_my 与 sms_max_cost_micro_usd_sg（默认 0 即未配置、停发）；金额一律是整数微美元；变量名与默认值已由登记 PR 写进 .env.example，本任务不改它
      - 不写日志；手机号不出现在异常消息与返回值里（只用国家呼叫码判定目的地）
      - tests/test_sms_budget.py 用 SQLite 内存库（开启外键检查）验证意图——马来西亚日期在 UTC 15:59:59 与 16:00:00 前后切换；首次预占插入当日行；条数与费用恰到上限放行、超过一条或一仙拒绝且不改数；未配置目的地拒绝；结算与释放后的三个数；结算与释放不能把数减成负数；两个会话先后预占时按顺序计数（SQLite 不复现 MySQL 的阻塞等待，在 TODO 记录段写明）；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-068 记录段（预占、结算、释放、配置项、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移（不改 app/models/）；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41），不合并、不直推
```

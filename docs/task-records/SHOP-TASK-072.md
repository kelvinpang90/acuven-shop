# SHOP-TASK-072 短信验证记录满 30 天删除

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「资料保留」第 4 条（短信验证请求及发送记录短期保留用于防滥用）、「失败、并发与重试」第 6 条（定时任务可重复运行、只生效一次；失败有告警与人工补跑办法）与 `docs/HANDOFF.md` 0.41 记录的 Kelvin 2026-10-08 决定（4）（短信验证记录含完整手机号保留 30 天后删除），在 SHOP-TASK-031 的定时任务运行器里加一个任务并提供手动执行一次的子命令：新文件 `app/services/sms_retention.py`；改 `app/jobs.py`；测试见新文件 `tests/test_sms_retention.py`。未加表或迁移，未改设计、`.platform/`、CI、Dockerfile、`docker-compose.yml` 或部署配置，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41（个人资料保留期）。拆分：定时任务层（6 个允许路径）。
- 删除规则（`app/services/sms_retention.py` 的 `delete_expired_verifications(db, now, limit)`，`now` 是不带时区的 UTC，返回删除条数）：删除 `verification_attempts` 里 `created_at` 不晚于 `now` 减 30 天（`VERIFICATION_RETENTION` = 30×24 小时；恰满 30 天即删除，差一秒保留）的行，不分状态与用途。每次最多 `limit` 条，按创建时间从早到晚、同一时间按 ID 从小到大；先读出候选 ID，再按 ID 删除（删除语句再带一次时间条件），然后提交；没有候选时回滚只读事务，`limit` 为 0 或负数时不查询、返回 0。函数返回时不留未结束的事务。可重复运行：已删除的行不再是候选，重复执行只删除新到期的行。只删 `verification_attempts`，不碰 `sms_daily_usage`、会员、会员会话与订单；没有别的表引用 `verification_attempts`。
- 运行器（`app/jobs.py`）：`delete_verifications_job()` 名为 `delete_expired_verifications`，以 `own_transactions=True` 注册（服务删除一批后自己提交）；单轮上限 `VERIFICATION_DELETE_BATCH_LIMIT` 等于 `CANCEL_BATCH_LIMIT`（100）。`default_jobs()` 依次为超时取消、每日库存重置、自动确认收货、删除短信验证记录。日志沿用运行器：有删除时记 `job delete_expired_verifications done: <条数> changed`，失败记 `job delete_expired_verifications failed: <异常类名>`，不记录手机号。模块说明的依据、用法、任务列表、事务与日志几段补上了这项任务与命令。
- 手动补跑：`docker compose exec shop_jobs python -m app.jobs delete-verifications`（`shop_jobs` 不在时可用 `docker compose run --rm --no-deps shop_jobs python -m app.jobs delete-verifications`）。执行一次（同一上限 100 条）后退出，退出码与日志写法沿用 `complete-shipped`：成功 0（日志总是记一条 `done`，含 0 条）；任务失败 1；未配置 `SHOP_DATABASE_URL` 2。删除条数等于 100 时可能还有剩余，可再执行一次；重复执行不出错。`shop_jobs` 容器沿用 `run` 子命令，部署不用改。
- 改动的现有测试（只按验收改）：`tests/test_jobs.py` 的 `test_default_jobs_use_the_fixed_round_limit` 默认任务名列表加上 `delete_expired_verifications`；`test_database_unavailable_is_logged_by_class_name_only` 每轮多一个任务，会话工厂不可用时每轮多开一次会话、多记一条失败日志，所以两轮的开会话次数由 6 改为 8、每轮的失败日志加上 `job delete_expired_verifications failed: OperationalError`（注释写明原因），断言意图不变。`tests/test_auto_complete.py` 的 `test_job_is_registered_after_existing_jobs_with_own_transactions` 默认任务名列表加上 `delete_expired_verifications`（注释写明 `default[-1]` 现在检查的是这项）；`default[-1].own_transactions` 与 `jobs.auto_complete_job().own_transactions` 两条断言原样保留。其余现有断言未动。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 设计「失败、并发与重试」写的是「使用操作标识保证只生效一次」（点名每日库存重置与积分过期）。本任务不许加表；删除本身天然只生效一次（已删除的行不再是候选），不另记操作标识。
  - 30 天按 30×24 小时、以库里不带时区的 UTC 比较，不按马来西亚日期切日。
  - 每轮只删一批（100 条）；短信每日上限 200 条时，每分钟一轮足以追上，积压时由之后各轮或补跑命令继续。
  - 会员注销不连带删除该号码的验证记录（不在本任务范围）；它们照样满 30 天后由本任务删除。
  - 共享备份中的验证记录不在本任务范围，按「资料保留」的备份边界另行处理。
- 留给之后的任务：运营告警邮件接入任务失败告警（在那之前按 SHOP-TASK-031 以容器不健康体现）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句与 HANDOFF 0.41 的 Kelvin 2026-10-08 决定自查。`tests/test_sms_retention.py` 用 SQLite 内存库（引擎设置与 `tests/test_auto_complete.py` 相同）按模型建表，直接写验证记录、会员、会员会话、短信每日用量与订单，每条测试的文档字符串写明它守住的设计原句、Kelvin 决定或验收约定，覆盖：恰满 30 天与更早的删除；差一秒的保留、一秒后照常删除；五种状态乘五种用途一律按时间删除（满的删、未满的留）；除 `verification_attempts` 外每张表的每一行不变（含与被删记录同一号码的会员及其会话）；每批上限与删除顺序（ID 与时间先后故意相反）；默认上限等于 `CANCEL_BATCH_LIMIT`（上限加一条时一次删上限条、下一次删剩下的一条）；重复执行不出错、返回 0、不留未结束的事务；上限 0；任务名、排在已有三项之后、`own_transactions=True`；运行器一轮删除、第二轮不再记日志，日志不含手机号；`delete-verifications` 成功 0（两次执行，日志只有任务名与条数、不含手机号）、失败 1（日志只有任务名与异常类名，不含连接信息或手机号）、未配置 2、经 `main` 走 SHOP_DATABASE_URL 与会话工厂（未建表的内存库失败 1、日志不含连接串）。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。未在真 MySQL 上验证删除语句与并发；未起容器实际执行子命令。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/sms_retention.py 删除 created_at 不晚于当前 UTC 时间减 30 天（恰满 30 天即删除）的 VerificationAttempt 行，每次最多删除一批（上限沿用 app/jobs.py 的 CANCEL_BATCH_LIMIT），返回删除条数；可重复运行，重复执行不出错；不碰 SmsDailyUsage、会员与订单
      - app/jobs.py 的 default_jobs 在已有三项之后加上这项任务（名称 delete_expired_verifications），以 own_transactions=True 注册、自己提交（tests/test_auto_complete.py 现有的 default[-1].own_transactions 断言在加入后检查的是这项），并加人工补跑子命令 delete-verifications（执行一次后退出，退出码与日志写法沿用 complete-shipped：成功 0、失败 1、未配置 SHOP_DATABASE_URL 2；日志只含删除条数与异常类名）；模块说明的用法一段补上这条命令
      - tests/test_jobs.py 与 tests/test_auto_complete.py 只许把默认任务名列表的断言加上 delete_expired_verifications，并按需要调整会话工厂不可用时每轮多出的一次开会话与一条失败日志的计数，写明原因；其余现有断言不动——tests/test_auto_complete.py 在 main 上已有 default[-1].own_transactions 与 jobs.auto_complete_job().own_transactions 两条断言，两条都原样保留，不增不删不改
      - tests/test_sms_retention.py 用 SQLite 内存库验证意图——恰满 30 天的删除、差一秒的保留；每批上限；各状态与用途一律按时间删除；不碰其他表；补跑命令的退出码与日志不含手机号；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-072 记录段（删除规则、任务与补跑命令、改动的现有测试、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置与 docker-compose；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41），不合并、不直推
```

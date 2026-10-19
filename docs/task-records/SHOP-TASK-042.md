# SHOP-TASK-042 发货满 7 天自动确认收货

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「订单与退款状态」（若访客不操作，模拟发货满 7 天自动确认收货；自动完成只改演示状态，不影响 30 天退款期；所有购买件数均已批准退款时冻结确认收货的推进）与「失败、并发与重试」（定时任务可重复运行、只生效一次；失败有告警与人工补跑办法），在 SHOP-TASK-031 的定时任务运行器里加一个任务并提供手动执行一次的子命令：新文件 `app/services/auto_complete.py`；改 `app/jobs.py`；测试见新文件 `tests/test_auto_complete.py`，`tests/test_jobs.py` 只按验收改了两处（默认任务名称列表加上 `auto_complete_shipped`；数据库不可用的测试按每轮三个任务计会话调用次数与失败日志，注释写明原因）。未改其他定时任务与运行器行为，未加表、迁移或接口，未做页面，未改前端、`.platform/`、CI 工作流、Dockerfile、`docker-compose.yml`、部署脚本或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）。拆分：不拆。
- 规则（`app/services/auto_complete.py` 的 `auto_complete_shipped_orders(db, now, limit)`，`now` 是不带时区的 UTC，返回本次完成的张数）：
  - 候选：状态为 `demo_shipped`，且最近一条迁移到 `demo_shipped` 的状态事件（`order_events` 里该单 `to_status = demo_shipped` 的最大 `created_at`）不晚于 `now` 减 7×24 小时（恰好满也算，差一秒不算）。按该时间从早到晚、同一时间按订单 ID 从小到大。
  - 单次上限计的是完成的张数：候选每次按上一页最后一张（时间, 订单 ID）之后取 `limit` 张，一直扫描到完成 `limit` 张或没有更多候选；跳过的全部已退订单不计入，所以冻结的订单堆在前面也不会卡住之后的订单。
  - 每张订单：锁定后状态已不是 `demo_shipped`，或 SHOP-TASK-029 的 `refund_state` 判定全部已退时回滚并跳过；否则经 SHOP-TASK-010 的 `is_transition_allowed`（操作者 `system`）后，用带条件的 UPDATE（状态仍为 `demo_shipped` 才改）改为 `demo_completed`，写一条 `demo_shipped → demo_completed`、操作者 `system`、时间为本次 `now` 的事件，然后提交。不写确认收货记录（`receipt_confirmations` 只记手动确认），不改支付时间，所以退款截止时间（支付时间加 30 天）不变。
- 锁定（与 SHOP-TASK-040 写明的协议相同，写在模块 docstring 里）：每张订单锁定订单行之前先结束此前的事务（候选扫描或上一张的事务，此前只有读取，`db.rollback()`），使 `SELECT … FOR UPDATE`（`order_lookup.lock_order_statement`，即 `with_for_update`）成为新事务的第一条语句；锁定之后的 `refund_state` 因此能看到等锁期间别人提交的审核、发货或确认。自动完成与退款批准、访客确认收货锁定同一订单行，相互串行。函数返回时不留未结束的事务。
- 运行器（`app/jobs.py`）：`auto_complete_job()` 名为 `auto_complete_shipped`，以 `own_transactions=True` 注册（服务自己结束候选扫描的事务、每张订单开始并结束自己的事务；不用运行器默认的外层事务加保存点，否则外层的读取快照会让锁定后的读取看不到等锁期间的提交）；单轮上限 `AUTO_COMPLETE_BATCH_LIMIT` 等于 `CANCEL_BATCH_LIMIT`（100）。`default_jobs()` 依次为超时取消、每日库存重置、自动确认收货。日志沿用运行器：有完成时记 `job auto_complete_shipped done: <张数> changed`，失败记 `job auto_complete_shipped failed: <异常类名>`，不记录订单号或个人资料。
- 手动补跑：`docker compose exec shop_jobs python -m app.jobs complete-shipped`（`shop_jobs` 不在时可用 `docker compose run --rm --no-deps shop_jobs python -m app.jobs complete-shipped`）。执行一次（同一上限 100 张）后退出：成功 0（日志总是记一条 `done`，含 0 张）；任务失败 1；未配置 `SHOP_DATABASE_URL` 2。完成张数等于 100 时可能还有剩余，可再执行一次；重复执行不重复生效。`shop_jobs` 容器沿用 `run` 子命令，部署不用改。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 设计「失败、并发与重试」写的是「使用操作标识保证只生效一次」（点名每日库存重置与积分过期）。本任务不许加表，只生效一次靠锁定后的状态前提与带条件的 UPDATE：一张订单只能从 `demo_shipped` 完成一次，重复运行时它已不是候选。
  - 因为 `own_transactions`，一次运行中途抛异常时，此前已完成的订单保持完成（每张各自提交、各自完整），不像超时取消那样整次回滚；剩余的下一轮继续。
  - 锁定后已经用行锁保证串行，仍用带条件的 UPDATE 改状态（与访客确认收货相同的写法），在不执行 `FOR UPDATE` 的库上也不会重复完成。
  - 全部已退而冻结的订单一直保持 `demo_shipped`，每轮都会被重新扫描并逐张锁定、读一次退款占用后跳过；数量很大时每轮的开销随之增加。不加表或标记就无法避免，留给之后需要时再定。
  - 发货时间取事件表，没有迁移到 `demo_shipped` 事件的 `demo_shipped` 订单（正常流程不会出现）不会被自动完成。锁定后不再重新比对发货时间：迁移表里只有 `demo_packed → demo_shipped` 能进入该状态，订单已是 `demo_shipped` 时不会再有新的发货事件。
  - 事件时间写本次运行的 `now`，不是发货时间加 7 天。
  - 候选每页的张数取 `limit`；上限为 0 或负数时不扫描、返回 0。
- 留给之后的任务：会员订单（「我的订单」确认收货）由会员任务接入，本任务对所有 `demo_shipped` 订单一视同仁；运营告警邮件接入任务失败告警（在那之前按 SHOP-TASK-031 以容器不健康体现）。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_auto_complete.py` 用 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档关掉 pysqlite 的事务处理、由引擎发 BEGIN，与 `tests/test_jobs.py` 相同）按模型建表，自建订单（含逐件分摊快照）、状态事件与退款申请，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：恰好满 7×24 小时的订单完成、事件操作者 system 与时间、无确认收货记录；支付时间与退款截止时间不变；差一秒未满的不处理、一秒后照常完成；全部已退的跳过（状态与事件不变）；只退一部分（批准一件、全部在审核中、全部被拒、一件批准一件审核中）的照常完成；待支付、已支付、已打包、已完成、已取消即使有满 7 天的发货事件也不处理；没有发货事件的不处理；以最近一次迁移到 `demo_shipped` 的时间为准；单次上限只计完成的张数、排在前面的全部已退订单超过上限张（含跨好几页）时之后的订单仍会完成；按发货时间、同一时间按订单 ID 的顺序（订单 ID 与时间先后故意相反）；上限 0；连续执行两次只生效一次；用 MySQL 方言编译锁定订单的查询含 `FOR UPDATE`；用 SQLAlchemy 连接事件记录事务边界与语句，断言调用前已有只读事务时，每张订单锁定订单的查询都是所在事务的第一条语句（前一条是回滚或提交）、读取退款占用在锁定之后的同一事务里、完成的提交而全部已退的回滚、返回后会话不在事务中；任务名、在默认列表末尾、以 `own_transactions=True` 注册、上限与超时取消相同；运行器一轮完成到期订单、第二轮不重复，日志只有任务名与张数、不含订单号；`complete-shipped` 成功 0（两次执行只生效一次，日志不含订单号）、失败 1（日志只有任务名与异常类名）、未配置 2、经 `main` 走 SHOP_DATABASE_URL 与会话工厂（未建表的内存库失败 1、日志不含连接串）。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。SQLite 不执行 `FOR UPDATE`，未在真 MySQL 上验证行锁、REPEATABLE READ 快照与并发；未起容器实际执行子命令。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「订单与退款状态」（若访客不操作，模拟发货满 7 天自动确认收货；自动完成只改演示状态，不影响 30 天退款期；所有购买件数均已批准退款时冻结确认收货的推进）与「失败、并发与重试」（任务可重复运行、只生效一次）一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/services/auto_complete.py 提供一个函数，接收数据库会话、当前时间（不带时区的 UTC）与单次上限：找出状态为 demo_shipped 且最近一条迁移到 demo_shipped 的状态事件时间不晚于当前时间减 7×24 小时的订单，按该时间从早到晚、同时按订单 ID 从小到大依次处理，单次上限计的是完成的张数：跳过的全部已退订单不计入，一直扫描到完成上限张或没有更多候选，使冻结的订单堆在前面也不会卡住之后的订单
      - 每张订单在自己的事务里先锁定订单行，锁定协议：锁定订单行之前先结束本请求此前的数据库事务（此前只有读取，回滚即可），使 SELECT … FOR UPDATE（SQLAlchemy 的 with_for_update）成为新事务的第一条语句——MySQL 默认的 REPEATABLE READ 下读取快照在事务第一条非锁定读时建立，这样锁定之后的读取（含 SHOP-TASK-029 的 refund_state）才能看到等锁期间别人提交的审核、发货或确认（与 SHOP-TASK-040 写明的协议相同），锁定后重新确认状态仍为 demo_shipped 且用 SHOP-TASK-029 的 refund_state 判断不是全部已退；通过时经 SHOP-TASK-010 的迁移判定函数（操作者 system）把订单改为 demo_completed、写一条操作者为 system 的状态事件，不写确认收货记录；全部已退或状态已变的订单跳过；返回本次完成的张数；同一时刻连续执行两次只生效一次
      - app/jobs.py 新增该任务（名称 auto_complete_shipped，单轮上限与超时取消相同，以 own_transactions=True 注册：服务自己结束候选扫描的事务、每张订单开始并结束自己的事务，不能用运行器默认的外层事务加保存点，否则外层的读取快照会让锁定后的读取看不到等锁期间的提交），在 default_jobs 里排在已有任务之后；新增子命令 complete-shipped 执行一次后退出，退出码与已有子命令的约定相同；日志只记录任务名、完成张数与异常类名，不记录订单号或个人资料；已有任务、子命令与运行器行为不变
      - tests/test_jobs.py 只许这样改，其余测试不动并照常通过：断言 default_jobs 名称列表的那一条加上 auto_complete_shipped；test_database_unavailable_is_logged_by_class_name_only 里按每轮任务数计的会话调用次数与失败日志条数改为按三个任务计（注释写明原因），断言的意图（日志只含任务名与异常类名、不含连接信息）不变
      - tests/test_auto_complete.py 用 SQLite 内存库（开启外键检查）按模型建表、自建订单与状态事件，验证：发货满 7×24 小时的订单完成、事件操作者为 system、无确认收货记录；差一秒未满的不处理；全部已退的跳过、只退一部分的照常完成；已完成、已取消与其他状态不处理；以最近一次迁移到 demo_shipped 的时间为准；单次上限只计完成的张数、排在前面的全部已退订单超过上限张时之后的订单仍会完成、排序生效；连续执行两次只生效一次；用 MySQL 方言编译锁定订单的查询，断言含 FOR UPDATE；用 SQLAlchemy 的事件记录语句，断言每张订单锁定订单的查询都是所在事务的第一条语句；该任务以 own_transactions=True 注册；complete-shipped 子命令的退出码；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；docs/TODO.md 新增 SHOP-TASK-042 记录段（规则、锁定、子命令、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI 工作流、Dockerfile、docker-compose.yml 与部署脚本（shop_jobs 容器沿用 run 子命令，无需改部署）；不装依赖；不加表、迁移或接口；不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

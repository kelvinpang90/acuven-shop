# SHOP-TASK-126 Stripe 退款尝试的定时处理

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条（还没有 Stripe 退款 ID 的尝试由定时任务在创建后 24 小时内以同一幂等键自动重试；24 小时后不再调用退款接口，改为列出该 PaymentIntent 退款的全部分页、按元数据里的尝试 ID 对账，找到就采用，找不到或无法列完都保持 pending 并告警；已有 Stripe 退款 ID 而状态未终结的由定时任务查询；已 succeeded 的在成功后 30 天内每天、之后每周再查询一次，不设终止）与 `docs/HANDOFF.md` 0.54 记录的 Kelvin 2026-10-19 决定（每 5 分钟、每 10 分钟、每小时的频率，从未调用过的尝试立即处理），在 SHOP-TASK-124 之上新增 `app/services/stripe_refund_overdue.py` 并接入 `app/jobs.py`；测试见 `tests/test_stripe_refund_overdue.py` 与 `tests/test_jobs.py`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.54，退款。
- 四种到期规则与频率（`process_stripe_refunds(db, gateway, now, limit)` 只选这些尝试；`has_due_refunds(db, now)` 用同一条件判断是否有到期的尝试，只读并结束读事务）：
  1. pending、没有 Stripe 退款 ID、创建未满 24 小时，调用次数为 0 或上次复查时间为空或早于 5 分钟前：调用 SHOP-TASK-124 的 `submit_refund_attempt`（同一幂等键，即尝试 ID）。
  2. pending、没有 Stripe 退款 ID、创建满 24 小时，上次复查时间为空或早于 1 小时前：不再调用退款接口，不持锁逐页 `list_refunds`（从上一页最后一笔之后接着列），按元数据里的尝试 ID 找到就在自己的事务里经 `apply_refund_object` 采用并提交；找不到或无法列完保持 pending。
  3. pending、有 Stripe 退款 ID，上次复查时间为空或早于 10 分钟前：调用 `refresh_refund_attempt`。
  4. succeeded（有 Stripe 退款 ID），上次复查时间为空，或成功时间在 30 天内且上次复查时间早于 1 天前，或成功时间满 30 天且上次复查时间早于 7 天前：调用 `refresh_refund_attempt`；改为 failed 时由它释放活动来源并按 SHOP-TASK-124 的规则告警。
- 选取与事务：按上次复查时间从早到晚、从没查过的排最前（MySQL 不支持 NULLS FIRST，按 IS NOT NULL 排），同时刻按 ID，最多 limit 条；逐条先以只改这一列的 UPDATE 写上次复查时间并提交、再按尝试此刻的状态处理，所以长期不能确定的尝试轮到队尾，不挤掉新尝试。调用 Stripe 之前都已结束读事务，不持有任何数据库锁；写入在各自的事务里锁住该尝试并提交。Stripe 不可用时这一条只留下上次复查时间、留到下一轮。返回处理条数（选中并处理过的尝试数，含不可用的）。
- 告警：照 SHOP-TASK-124 的写法，提交之后记一条 error 级日志 `"%s attempt=%d"`，只含类别与本站尝试 ID。新增类别 `stripe_refund_unresolved`（对账找不到或无法列完）：会反复出现的告警，以带条件的 UPDATE（仍为 pending、仍没有 Stripe 退款 ID、告警时间为空）写告警时间，写到了才记，每条尝试至多一次；与 `stripe_refund_mismatch` 共用告警时间列，已因不符告警过的尝试不再记。采用找到的退款时按 `alert_category` 记（如不符）；`stripe_refund_rejected` 与 `stripe_refund_failed_after_success` 由 SHOP-TASK-124 的函数随状态改动记。
- 任务顺序：`default_jobs()` 在现有六项（超时取消、Stripe 会话的定时处理、每日库存重置、发货满 7 天自动确认收货、删除满 30 天短信验证记录、积分到期）之后追加 `stripe_refunds`（`stripe_refunds_job`，每轮最多 `STRIPE_REFUND_BATCH_LIMIT`，即 `CANCEL_BATCH_LIMIT` 条，own_transactions，逐条提交）。适配器照 `stripe_checkouts_job` 每次按 `get_settings()` 以 gateway_factory 建；Stripe 未配置且没有到期的尝试时不建适配器、返回 0（跳过算成功），有到期的尝试则照常处理（适配器调用一律为 unavailable，留到下一轮；满 24 小时的对账照常告警）。
- 手动补跑：`python -m app.jobs process-stripe-refunds`（`process_stripe_refunds_once`，写法照 `process-stripe`）：执行一次后以 0 退出，日志只含任务名与处理条数；未配置数据库以 2 退出；任务失败以 1 退出、只记异常类名。只处理已到期的尝试，重复执行不重复调用；不能把尝试改为 failed。`app/jobs.py` 的模块说明（依据、用法、任务列表、事务与日志）随之补充。
- 测试：`tests/test_jobs.py`、`tests/test_auto_complete.py` 与 `tests/test_sms_retention.py` 里断言 `default_jobs` 个数的测试由 6 改为 7（`tests/test_jobs.py` 另把完整名单补上 `stripe_refunds`、数据库不可用一轮的会话数由 12 改为 14 并多一条失败日志），各处写明原因，其余断言未改；`tests/test_jobs.py` 另补任务顺序、未配置且没有到期尝试时跳过、未配置而有到期尝试时照常处理与手动补跑命令。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 时间边界取字面：「早于 5 分钟前」「早于 1 小时前」「早于 10 分钟前」「早于 1 天前」「早于 7 天前」为严格早于；「创建满 24 小时」与 SHOP-TASK-124 的 `now - created_at >= 24h` 一致，含恰好 24 小时；「成功时间在 30 天内」为成功时间晚于当前时间减 30 天，「满 30 天」含恰好 30 天。按每分钟一轮，实际间隔可能比所写频率多出最多一轮。
  - 处理前先写上次复查时间并提交（照 SHOP-TASK-116 的做法），处理中途抛异常的尝试也轮到队尾；异常不逐条捕获，本轮任务记失败、下一轮继续。
  - 第 4 条规则另要求 Stripe 退款 ID 非空：按写入规则 succeeded 的尝试一定有 ID；没有 ID 时 `refresh_refund_attempt` 不调用也不写上次复查时间，会每轮占一个名额。
  - 选中之后按尝试此刻的状态分派（可能已被 webhook 或接口改过）：已是 failed 的不做任何事，仍计入处理条数。
  - 对账在找到第一笔元数据相符的退款即停；某一页不可用或被拒、`has_more` 不是布尔值、还有下一页却是空页、超过 `MAX_LIST_PAGES`（100 页，每页 100 笔）都按无法列完处理。分页上限是为防游标不前进时空转另加的。
  - 采用找到的退款时不另核对金额（与 SHOP-TASK-124 一致）。
  - 告警的去重：`stripe_refund_unresolved` 只在第一次找不到或列不完时记，之后每小时照列、找到即采用，不再告警。
- 留给之后的任务：webhook 的退款事件（第 7、8 条「退款事件只作触发，收到后先查询再更新」）与后台退款详情及重试接口另行登记；批准请求里提交之后立即调用退款接口由之后的后台接口任务接入，在那之前由本任务的定时处理在下一轮发起。本任务未加接口，未改 `app/services/stripe_refund.py` 与其他已有服务、`app/api/`、模型、迁移、`.platform/`、CI、部署配置与前端，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8、9 条与 HANDOFF 0.54 原句自查，并核对与 `app/services/stripe_refund.py`、`app/services/stripe_gateway.py` 的函数与适配器接口、`app/models/stripe.py` 的列与检查约束一致。新增与改动的测试用 SQLite 内存库与 `FakeStripeGateway`，只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；SQLite 不支持行锁，「调用 Stripe 时不持有任何数据库锁」在测试里以调用时会话不在事务中检查，MySQL 上的加锁与并发按代码审阅。检查命令结果由 Worker 另行记录。

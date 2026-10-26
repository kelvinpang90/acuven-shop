# SHOP-TASK-128 Stripe webhook 的退款事件同事务处理

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 7 条（事件 ID 的去重记录与它引起的退款改动在同一个数据库事务里提交；业务动作另按对象去重，第 8 条按退款记录）与第 8 条（Stripe 退款 ID、调用结果与之后的退款事件（按 Stripe 退款 ID 或元数据里的记录 ID 关联）更新记录状态；退款事件只作触发，收到后先查询再更新，不按事件里的快照改；退款接口的响应、webhook 与定时查询共用同一写入规则），以及 `docs/HANDOFF.md` 0.55 的落实写法（处理 `refund.created`、`refund.updated`、`refund.failed`，data.object 都是退款对象），在 SHOP-TASK-127 的 `handle_webhook_event` 里接入退款事件，写入沿用 SHOP-TASK-124 的 `apply_refund_object`；测试见 `tests/test_stripe_webhook_refund.py`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，退款。
- 分派：`handle_webhook_event` 对 type 属于 `REFUND_EVENT_TYPES` 且 `event.refund` 非空的事件改由 `_handle_refund` 处理，不再记 ignored。去重（先结束当前事务、普通读查到事件 ID 即 duplicate）、Checkout 事件、其他类型与 `_record_only` 单独写事件记录及撞约束返回 duplicate 的行为未改。模块说明改写为同时覆盖 Checkout 与退款事件（补第 8 条原句、退款事件的事件记录写入时机与新增的日志类别）。
- 找尝试（`_find_attempt`）：以普通读按 `StripeRefund.stripe_refund_id` 等于事件退款 ID 找；找不到且元数据的尝试 ID 为十进制数字时按尝试 ID 找。都找不到（含不是本站发起的退款）为 ignored，由 `_record_only` 写事件记录，不调用 Stripe。找到后结束读事务。
- 先查询再写入：不持有锁、不留读事务，以事件退款 ID 调用 `retrieve_refund` 一次。unavailable 为 unavailable；rejected 也为 unavailable，并记一条 error 日志 `stripe_webhook_refund_rejected attempt=<尝试 ID>`（第 7 条「处理失败整个事务回滚、回 5xx」，由 Stripe 重发）。这两种都不留事件记录、尝试不变。事件里的退款快照只用于找尝试与取退款 ID，不用来写状态。
- 事件记录与写入同一事务：查询 ok 时调用 `apply_refund_object(db, 尝试 ID, 查询到的退款, now)`（它锁住尝试并核对 PaymentIntent 与退款 ID 或元数据，只 flush），再插入 outcome 为 processed 的事件记录并 flush；撞上事件 ID 唯一约束时回滚（退款改动一并撤销）、返回 duplicate、不记告警；否则提交并返回 processed。不调用 `refresh_refund_attempt` 与 `submit_refund_attempt`（它们自己提交）。
- 告警时机：只在提交之后，按 `alert_category` 的结果照 `refresh_refund_attempt` 的写法记一条 error 日志（`stripe_refund_mismatch` 或 `stripe_refund_failed_after_success`，只含尝试 ID）；回滚的路径不记。日志不含事件 ID 与事件或退款原文。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 元数据尝试 ID 的「十进制数字」与订单 ID 同一取法：ASCII `[0-9]`、1 至 18 位，更长按找不到（ignored）。
  - 退款查询的结果没有 live 这一类（适配器只对会话做 livemode 归类）；ok 以外、rejected 以外的结果，以及 ok 而对象为空，一律按 unavailable。
  - 按元数据找到的尝试已记下另一个 Stripe 退款 ID 时，照常查询并交给 `apply_refund_object`，由它判为不符（写告警时间、记 `stripe_refund_mismatch`），事件记录为 processed。
  - 查询到的退款与尝试不符（mismatch、mismatch_alerted）也视为已处理：事件记录为 processed、回 200，不按 unavailable 让 Stripe 重发（同一对象重发仍不符，告警时间已去重）。
  - 事件类型属于退款而 `event.refund` 为空（只可能由调用方直接构造，解析不会产生）时按其他类型记 ignored。
  - `apply_refund_object` 自身 flush 时若撞上 Stripe 退款 ID 的唯一约束（并发下另一尝试同时写入同一退款 ID），异常原样传出，不当作 duplicate。
- 测试：`tests/test_stripe_webhook_refund.py`（SQLite 内存库与 `FakeStripeGateway`，订单、会话记录与尝试的准备照 `tests/test_stripe_refund.py` 在本文件里另写）覆盖 refund.updated 而查询到 succeeded 时 pending 改为 succeeded、事件记录为 processed；refund.failed 而查询到 succeeded 时按查询结果；已 succeeded 的尝试收到晚到的 refund.created、查询到 pending 时不倒退；已 succeeded 的尝试查询到 failed 时改为 failed 并记一次 `stripe_refund_failed_after_success`；还没有 Stripe 退款 ID 的尝试按元数据的尝试 ID 找到并写入退款 ID；查询到的 PaymentIntent 不符时不改状态、写告警时间并记 `stripe_refund_mismatch`；找不到尝试（元数据尝试 ID 缺失、为空、不存在、不是十进制数字或超长）为 ignored 且不调用 Stripe；Stripe 不可用为 unavailable、没有事件记录且尝试不变；查询被拒为 unavailable、没有事件记录、尝试不变并记一次 `stripe_webhook_refund_rejected`；同一事件再来为 duplicate 且不调用 Stripe；把 `_already_recorded` 替换为恒返回假并预先插入同一事件 ID 时返回 duplicate、尝试未改、不记告警。每条测试的文档字符串写明守住的设计原句。`tests/test_stripe_webhook.py` 与 `tests/test_stripe_refund.py` 未改。
- 留给之后的任务：webhook 接口（请求体上限、凭据不可用、签名校验、内容不合格与 livemode 的拒绝、结果到状态码的映射）由 SHOP-TASK-129 实现。本任务未加接口，未改 `app/services/stripe_refund.py`、`app/services/stripe_gateway.py`、`app/services/stripe_confirm.py`、`app/services/payment.py`、`app/api/`、`app/jobs.py`、模型、迁移、`.platform/`、CI、部署配置与前端，未装依赖，未改 `docs/TODO.md`。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 7、8 条与 HANDOFF 0.55 原句自查，并核对与 `apply_refund_object`、`alert_category`、`refresh_refund_attempt` 的日志写法及 `app/models/stripe.py` 的 `StripeRefund`、`StripeEvent` 唯一约束一致。新增的测试用 SQLite 内存库与 `FakeStripeGateway`，只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；SQLite 不支持行锁，MySQL 上的加锁与并发按代码审阅。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - handle_webhook_event 对 type 属于 REFUND_EVENT_TYPES 的事件不再按其他类型记 ignored，改按下面处理；其余行为（去重、Checkout 事件、其他类型、单独写事件记录与 duplicate）不变；模块说明随之改写
      - 找尝试——以普通读按 stripe_refund_id 等于事件退款 ID 找，找不到且元数据的尝试 ID 为十进制数字时按尝试 ID 找；都找不到（含不是本站发起的退款）为 ignored，不调用 Stripe；找到后结束读事务，以事件退款 ID 调用 retrieve_refund 一次（第 8 条，只作触发、先查询），unavailable 为 unavailable（不留事件记录、尝试不变，Stripe 会重发），rejected 也为 unavailable（不留事件记录、尝试不变，记一条 error 日志 stripe_webhook_refund_rejected，只含尝试 ID——已找到本站尝试而无法核实时照第 7 条「处理失败整个事务回滚、回 5xx」让 Stripe 重发）
      - 写入——查询 ok 时在同一事务里调用 apply_refund_object（尝试 ID、查询到的退款、now；它锁住尝试并核对 PaymentIntent 与退款 ID 或元数据），再插入事件记录（outcome 为 processed）并 flush，撞上事件 ID 唯一约束时回滚、返回 duplicate、不记告警；否则提交，提交之后按 alert_category 的结果照 refresh_refund_attempt 的写法记一条 error 日志（类别与尝试 ID），返回 processed；不调用 refresh_refund_attempt 与 submit_refund_attempt（它们自己提交），不用事件里的退款快照写状态
      - 测试验证意图——新文件 tests/test_stripe_webhook_refund.py 用 SQLite 内存库与 FakeStripeGateway（尝试的准备照 tests/test_stripe_refund.py 的写法在本文件里另写），验证——refund.updated 而查询到 succeeded 时 pending 改为 succeeded、事件记录为 processed；refund.failed 而查询到 succeeded 时按查询结果（守住先查询再更新、事件乱序不倒退）；已 succeeded 的尝试查询到 failed 时改为 failed 并记一次 stripe_refund_failed_after_success；还没有 Stripe 退款 ID 的尝试按元数据的尝试 ID 找到并写入退款 ID；查询到的 PaymentIntent 不符时不改状态、写告警时间并记 stripe_refund_mismatch；找不到尝试为 ignored 且不调用 Stripe；Stripe 不可用为 unavailable、没有事件记录且尝试不变；查询被拒为 unavailable、没有事件记录、尝试不变并记一次 stripe_webhook_refund_rejected；同一事件再来为 duplicate 且不调用 Stripe；把 _already_recorded 替换为恒返回假并预先插入同一事件 ID 时返回 duplicate 且尝试未改、不记告警（守住同一事务）；每条测试写明它守住的是设计的哪一句；tests/test_stripe_webhook.py 与 tests/test_stripe_refund.py 一字不改且照常通过；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/task-records/SHOP-TASK-128.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；找尝试、先查询再写入、事件记录与写入同一事务、告警时机、偏离、验证到什么程度，并写明接口留给 SHOP-TASK-129），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/stripe_refund.py、app/services/stripe_gateway.py、app/services/stripe_confirm.py、app/services/payment.py、app/api/、app/jobs.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.55），不合并、不直推
```

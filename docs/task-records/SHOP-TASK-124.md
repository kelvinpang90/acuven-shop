# SHOP-TASK-124 Stripe 退款尝试的写入规则与调用

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条（以尝试的本站 ID 为幂等键并写进退款元数据；状态映射；只有 Stripe 明确给出 failed 或 canceled，或以请求错误明确拒绝创建才记为 failed；有退款对象的尝试状态以查询到的当前对象为准；写入时锁住该尝试的行，只接受 pending → succeeded、pending → failed、succeeded → failed 三种改动；24 小时后不再调用退款接口）与第 9 条，以及 `docs/HANDOFF.md` 0.54 记录的 Kelvin 2026-10-19 决定（明确拒绝以适配器归为 rejected 为准，记下错误类别，不自动重试；告警时间列只给会反复出现的告警去重），新增 `app/services/stripe_refund.py`；测试见 `tests/test_stripe_refund.py`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.54。
- 写入规则：
  - `map_refund_status`：Stripe 的 pending 与 requires_action 为 pending，succeeded 为 succeeded，failed 与 canceled 为 failed，其他值（含空）为 None，即不改状态。
  - `apply_refund_object(db, attempt_id, stripe_refund, now)`：以 `lock_attempt_statement`（FOR UPDATE）锁住该尝试，核对 Refund 的 payment_intent 等于尝试的 PaymentIntent ID，并且已记下 Stripe 退款 ID 时 Refund 的 ID 等于它、还没记下时元数据里的尝试 ID 等于本尝试。不符时不改状态、Stripe 退款 ID 与上次复查时间，以带条件的 UPDATE（只在 `alerted_at` 为空时）写告警时间，写到了返回 `mismatch`，早已非空返回 `mismatch_alerted`。相符时 Stripe 退款 ID 只在为空时写入；只接受三种改动（改为 succeeded 写成功时间，改为 failed 清空两个活动来源列），其余忽略；写上次复查时间。返回 `changed`、`unchanged` 或 `failed_after_success`（succeeded 改为 failed）。只 flush、不提交、不写日志；`alert_category(result)` 给出调用方提交之后要记的告警类别（`mismatch` 为 `stripe_refund_mismatch`，`failed_after_success` 为 `stripe_refund_failed_after_success`，其余为 None）。
- 调用：
  - `submit_refund_attempt(db, gateway, attempt_id, now)`：普通读取得尝试，不是 pending 或已有 Stripe 退款 ID 返回 `skipped`；创建满 24 小时（`now - created_at >= 24h`）返回 `window_closed`；都不调用。结束读事务后不持锁调用 `create_refund`，表单依次为 `payment_intent`、`amount`（尝试金额）、`metadata[attempt_id]`（尝试 ID），幂等键为尝试 ID 的十进制字符串；响应为回放时同样不持锁先 `retrieve_refund`。之后锁住该尝试，调用次数加一、写上次复查时间：ok 时按写入规则写入（`recorded`、`mismatch` 或 `failed_after_success`）；回放而查询不可用、被拒或查到的 ID 与回放不同时，只按回放内容核对并写入 Stripe 退款 ID、不改状态（`replay_pending`，核对不符时为 `mismatch`）；rejected 时仍为 pending 且仍没有 Stripe 退款 ID 才记为 failed、清空活动来源列、错误类别写 Stripe 错误的 type（为空取 code，都为空留空，截到 64 字符）并告警（`rejected`），否则不改状态、不告警（`rejected_ignored`）；unavailable 与 live 保持 pending（`unavailable`）。提交之后才记日志。
  - `refresh_refund_attempt(db, gateway, attempt_id, now)`：还没有 Stripe 退款 ID 返回 `skipped`、不调用；否则不持锁 `retrieve_refund`，再锁住该尝试按写入规则写入（`changed`、`unchanged`、`mismatch`、`failed_after_success`）；查询不可用或被拒时只写上次复查时间（`unavailable`）。提交之后才记日志。
  - `refunded_total(db, payment_intent_id)`：以 `refunded_total_statement`（FOR UPDATE）锁住同一 PaymentIntent 的 pending 与 succeeded 尝试，返回金额合计（failed 不计），不提交。
- 告警：一律在提交之后记一条 error 级日志，格式照 `app/services/stripe_confirm.py`（`"%s attempt=%d"`），只含类别与本站尝试 ID，不记录 Stripe 请求、响应与退款原文。`stripe_refund_mismatch` 按告警时间每条尝试至多一次；`stripe_refund_rejected` 与 `stripe_refund_failed_after_success` 随状态改动只记一次，不看也不写告警时间，已因不符告警过的尝试照记。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 不符时 `apply_refund_object` 不写上次复查时间（验收标准只要求相符时写）；`submit_refund_attempt` 与 `refresh_refund_attempt` 按各自的规则一律写它。
  - `apply_refund_object` 第三个参数名为 `stripe_refund`。另公开 `map_refund_status`、`alert_category`、`refund_form`、`lock_attempt_statement` 与 `refunded_total_statement`，供之后的任务与测试使用。
  - 回放后查到的退款 ID 与回放不同，按查询不成处理（只核对并写回放的 ID），不采用查到的对象。
  - 调用结果为 live（适配器对退款不会给出）按 unavailable 处理。
  - 相符的 failed 尝试（failed 不再改动）在 Stripe 退款 ID 为空时仍按「只在为空时写入」补上 ID。
  - `refresh_refund_attempt` 不跳过 failed 的尝试；查询到的任何状态都按被忽略的改动处理。
  - 不核对 Refund 的金额（验收标准未要求）。
  - 测试里尝试的来源一律用已付款的 expired 会话记录（写入规则不看来源类别）；SQLite 不支持行锁，锁定语句只以 MySQL 方言编译检查含 FOR UPDATE，并发排队按代码审阅。
- 留给之后的任务：批准退款时建尝试与手动重试（含按 `refunded_total` 核对合计）由 SHOP-TASK-125；定时任务（无 ID 尝试 24 小时内的重试、有 ID 尝试的复查、24 小时后列出全部分页按元数据对账及其告警）由 SHOP-TASK-126；webhook 与后台退款详情及重试接口之后另行登记。本任务未加接口，未接入定时任务，未改 `app/services/` 下的已有文件、`app/api/`、`app/jobs.py`、模型、迁移与前端。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8、9 条与 HANDOFF 0.54 原句自查，并核对与 `app/services/stripe_gateway.py` 的适配器接口、`app/models/stripe.py` 的列与检查约束一致。新增测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/stripe_refund.py 的状态映射——Stripe 的 pending 与 requires_action 为 pending，succeeded 为 succeeded，failed 与 canceled 为 failed，其他值不改状态；公开函数 apply_refund_object（入参数据库会话、尝试 ID、适配器返回的 Refund、当前时间）锁住该尝试的行，核对 Refund 的 payment_intent 等于尝试的 PaymentIntent ID，并且——已记下 Stripe 退款 ID 时 Refund 的 ID 必须等于它，还没记下时元数据里的尝试 ID 必须等于本尝试；不符时不改状态与 Stripe 退款 ID，由它自己在同一 flush 里以带条件的更新写告警时间（只在为空时写），返回结果区分「首次不符」（调用方提交后记告警，类别 stripe_refund_mismatch）与「已告警过的不符」（不再记）；相符时 Stripe 退款 ID 只在为空时写入，只接受上述三种状态改动（改为 succeeded 写成功时间，改为 failed 清空两个活动来源列，其余改动忽略），写上次复查时间；succeeded 改为 failed 时须告警（类别 stripe_refund_failed_after_success）；只 flush 不提交，供 webhook 把事件去重放进同一事务，所以不自己记日志，而是返回枚举结果（至少区分已改动、未改动、不符、成功后又失败），由调用方在提交之后按结果记告警
      - 公开函数 submit_refund_attempt（入参数据库会话、适配器、尝试 ID、当前时间）——只处理 pending 且还没有 Stripe 退款 ID 的尝试；创建满 24 小时不调用、返回 window_closed；调用 create_refund 时不持有任何数据库锁，表单为 payment_intent、amount（尝试金额）与 metadata[attempt_id]（尝试 ID），幂等键为尝试 ID 的十进制字符串；之后在自己的事务里锁住该尝试、调用次数加一、写上次复查时间——ok 时按 apply_refund_object 写入（响应为同一幂等键的回放时先 retrieve_refund 再写；查询不可用或被拒时只按回放内容写入 Stripe 退款 ID（核对同上），不改状态，留给之后的查询），rejected 时，只在重新锁住后该尝试仍为 pending 且仍没有 Stripe 退款 ID 时记为 failed、清空活动来源列、最后的错误类别写 Stripe 错误的 type（为空时取 code，都为空时留空；截到 64 字符）并记一次告警（类别 stripe_refund_rejected），rejected 而尝试已不是 pending 或已有 Stripe 退款 ID（期间被别处写入）时只加调用次数与写上次复查时间、不改状态、不告警；unavailable 与 live 保持 pending；返回枚举结果；提交后才记日志
      - 公开函数 refresh_refund_attempt（入参同上）——对已有 Stripe 退款 ID 的尝试不持锁调用 retrieve_refund，再在自己的事务里按 apply_refund_object 写入并提交；Stripe 不可用或查询被拒时只写上次复查时间，不改状态、不告警；公开函数 refunded_total（入参数据库会话、PaymentIntent ID）以 FOR UPDATE 锁住同一 PaymentIntent 的 pending 与 succeeded 尝试并返回金额合计（failed 不计），供批准与手动重试核对「合计不得超过付款金额」
      - 告警一律是提交之后记一条 error 级日志（格式照 app/services/stripe_confirm.py，只含类别与本站尝试 ID）；会反复出现的告警（本任务的 stripe_refund_mismatch）以带条件的更新写告警时间，告警时间已非空的尝试不再记这类告警；状态改动引起的告警（stripe_refund_rejected、stripe_refund_failed_after_success）随改动只记一次，不看也不写告警时间；不记录 Stripe 请求、响应与退款原文
      - 测试验证意图——新文件 tests/test_stripe_refund.py 用 SQLite 内存库与 FakeStripeGateway 验证——状态映射五种与未知值；三种允许的改动与被忽略的改动（succeeded 到 pending、failed 到任何状态）、以相反顺序写入两个查询结果不倒退；succeeded 改为 failed 释放活动来源（同一来源随后可再建一条）并告警（已因不符告警过的尝试也照记）；不符的告警只记一次；PaymentIntent 或尝试 ID 不符不写；提交表单与幂等键；rejected 记 failed 与错误类别且不再调用；unavailable 保持 pending、调用次数加一；回放时先查询；满 24 小时不调用；refunded_total 不计 failed；用 MySQL 方言编译锁定语句含 FOR UPDATE；每条测试写明它守住的是设计或 HANDOFF 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/task-records/SHOP-TASK-124.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写入规则、调用、告警、偏离、验证到什么程度，并写明批准与手动重试、定时任务、webhook 与后台接口留给之后的任务），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的已有文件、app/api/、app/jobs.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.54），不合并、不直推
```

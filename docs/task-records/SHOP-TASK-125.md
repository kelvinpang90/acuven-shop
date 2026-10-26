# SHOP-TASK-125 批准退款时建 Stripe 退款尝试与手动重试

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条（管理员批准退款的事务不变；订单经 Stripe 付款且本次批准的现金退款额（不含运费）大于 0 时，同一事务另写一条 pending 的尝试，来源为该退款申请；为 0 时不建尝试；同一 PaymentIntent 的 pending 与 succeeded 退款合计不得超过付款金额，新建前在锁住该付款的事务里核对，超出即不建并告警；最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试）与「订单与退款状态」，以及 `docs/HANDOFF.md` 0.54，在 SHOP-TASK-124 之上改造 `app/services/refund_review.py` 的批准，并在 `app/services/stripe_refund.py` 加手动重试的业务函数；测试见 `tests/test_stripe_refund_approval.py`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.54。
- 批准时建尝试（`approve_refund` 的签名、返回值与现有行为不变）：
  - 保持开头的 rollback 与锁订单写法，不新增 begin、rollback 或 commit，提交仍由调用方完成。在现有保存点里、`apply_refund_points` 之后、写审计之前，只在批准且不是按审核幂等键重放时调用新的 `add_request_attempt(db, order_id, refund_request_id, amount_sen, now)`；重放（含唯一约束冲突后按键重查得到的重放）在这之前已返回。
  - 申请的 `amount_sen`（各申请行现金实付之和，不含运费）不为正：不读 Stripe 会话记录，不建。
  - 先普通读取该订单确认时间非空的会话记录（`order_id` 等于该订单，至多一条），查不到（经模拟支付的订单只会有确认时间为空的记录或没有记录）即不锁也不写；查到时按主键锁住（FOR UPDATE）。
  - 该记录缺 PaymentIntent ID：不建，告警 `stripe_refund_unrefundable`。
  - 以 SHOP-TASK-124 的 `refunded_total` 锁住同一 PaymentIntent 的 pending 与 succeeded 尝试，已有合计加本次金额超过该记录的 `amount_sen`：不建，告警 `stripe_refund_cap`；等于不算超过。
  - 否则写一条 pending 尝试：来源 `refund_request`、申请列与活动来源列为该申请、PaymentIntent 取该记录、金额为申请的 `amount_sen`、调用次数 0、创建时间为批准时间。
  - 锁顺序为订单、会员（`apply_refund_points`）、Stripe 会话记录、退款尝试；新增的读取都在锁订单之后。
  - 告警不影响批准，批准照常成功。日志在保存点成功之后、`approve_refund` 返回之前记（error 级，`"%s refund_request=%d"`，只含类别与申请 ID）；这时调用方还没提交，外层提交失败时可能多一条告警，只供人工核对。模块说明里「本模块不写日志」一句改为「除上述告警外本模块不写日志」。
- 手动重试 `retry_request_attempt(db, refund_request_id, now)`（`app/services/stripe_refund.py`），返回新尝试的 ID：
  - 照 `_review` 的写法先 rollback，再以子查询按申请 ID 锁住所属订单行；查不到抛 `RetryRequestNotFound`。
  - 申请不是 approved 抛 `RefundRequestNotApproved`；该申请没有尝试抛 `NoRefundAttempt`；最新一条（按尝试 ID 取最大者）不是 failed 抛 `RetryNotAllowed`。
  - 锁住该订单确认时间非空的会话记录，以 `refunded_total` 核对合计（同批准）；超过时先记一条 `stripe_refund_cap` 日志（只含申请 ID），再抛 `RefundCapExceeded`。
  - 在保存点里写一条 pending 尝试（金额为申请的 `amount_sen`）。同一来源的并发重试撞上活动来源列的唯一约束时只回滚保存点（订单行的锁保持），抛 `RetryNotAllowed`。
  - 所有异常都在写库之前抛出（或保存点已回滚）。只 flush、不提交，由之后的接口提交并调用 `submit_refund_attempt`。
- 调用 Stripe：本任务不调用 Stripe、不加接口。批准与重试新建的 pending 尝试由之后的后台退款详情与重试接口在提交后以 `submit_refund_attempt` 发起（HANDOFF 0.54），在那之前由定时任务（SHOP-TASK-126）发起。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - `app/services/refund_review.py` 导入 `app/services/stripe_refund.py`，后者不能反向导入，所以重试锁订单的语句在 `stripe_refund.py` 里另写一份 `_lock_order_for_request_statement`，内容与 `refund_review.lock_order_for_request_statement` 相同。
  - 申请不存在、未批准、没有尝试、不能重试与超额各用 `stripe_refund.py` 里新定义的异常，不复用 `refund_review` 的异常（理由同上）。
  - 重试时订单查不到确认时间非空的会话记录，或其 PaymentIntent ID 与最新尝试不同，视为数据有误抛 `RuntimeError`（验收标准未列这种情形；正常数据下尝试只会建在这条记录的 PaymentIntent 上）。
  - 重试的 PaymentIntent 取最新那条尝试的（与会话记录相同），付款金额取会话记录的 `amount_sen`。
  - 告警类别 `stripe_refund_cap` 与 `stripe_refund_unrefundable` 的常量放在 `stripe_refund.py`（`LOG_REFUND_CAP`、`LOG_REFUND_UNREFUNDABLE`）；`stripe_refund_cap` 与 `app/services/stripe_confirm.py` 的同名类别取值相同，那个文件未改。批准的告警由 `app.services.refund_review` 的 logger 记，重试的超额告警由 `app.services.stripe_refund` 的 logger 记。
  - 批准时没建成尝试只记日志，不写会话记录或尝试的告警时间（验收标准未要求，告警时间列只给会反复出现的告警去重）。
- 测试（`tests/test_stripe_refund_approval.py`，SQLite 内存库，照 `tests/test_refund_review.py` 由引擎发 BEGIN 以使保存点与 MySQL 一致；订单与会话记录照 SHOP-TASK-117 的测试直接按模型写入）：Stripe 付款订单批准后一条 pending 尝试、金额等于申请的现金退款额（不含用券抵掉的件与运费）、来源为该申请；锁顺序与事务边界；按幂等键重放不再建；金额为 0 时不建、不读 Stripe 记录；模拟支付订单不锁不写；已有 pending 或 succeeded 尝试加本次超过付款金额时不建且告警一次、批准照常成功，failed 不计入，正好相等时照建；缺 PaymentIntent 时不建且告警；手动重试在最新为 failed 时新建，在 pending 或 succeeded 时拒绝，未批准、不存在与没有尝试时各抛异常，超额时抛 `RefundCapExceeded` 且不写库；第二次并发插入被唯一约束拒绝。每条测试的文档字符串写明它守住的设计原句。`tests/test_refund_review.py` 未改。
- 改动只在 `app/services/refund_review.py`、`app/services/stripe_refund.py`、`tests/test_stripe_refund_approval.py` 与本文件；未改 `app/api/`、`app/jobs.py`、`app/services/stripe_confirm.py`、模型、迁移、`.platform/`、CI、部署配置、前端与 `docs/TODO.md`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8 条与 HANDOFF 0.54 原句自查，并核对与 `app/models/stripe.py` 的列与检查约束、`tests/test_refund_review.py` 的现有约束（锁订单是新事务的第一条语句、只有一次锁订单、保存点冲突只回滚保存点）一致。SQLite 不支持行锁，锁定只以 MySQL 方言编译检查含 FOR UPDATE 与先后顺序，并发排队按代码审阅。新增测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - approve_refund 的签名、返回值与现有行为不变；在现有保存点内、apply_refund_points 之后（锁顺序为订单、会员、Stripe 会话记录、退款尝试），只在批准且不是按幂等键重放时，查该订单由 Stripe 付款的会话记录（order_id 等于该订单且确认时间非空，至多一条）——有且退款申请的 amount_sen 大于 0 时锁住该记录，以 SHOP-TASK-124 的 refunded_total 核对已有合计加本次金额不超过该记录的 amount_sen，未超过则写一条 pending 尝试（来源 refund_request、PaymentIntent 取该记录、金额为申请的 amount_sen、活动来源列为该申请）；超过则不建并告警（类别 stripe_refund_cap），该记录缺 PaymentIntent ID 时不建并告警（类别 stripe_refund_unrefundable）；金额为 0 的申请不读 Stripe 记录；经模拟支付的订单只会查到没有确认时间非空的会话记录，不锁也不写
      - approve_refund 保持现有的开头 rollback 与锁订单写法，不新增 begin、rollback 或 commit，提交仍由调用方完成（tests/test_refund_review.py 的现有约束）；retry_request_attempt 照 _review 的写法先 rollback 再锁订单；新增的读取都在锁订单之后；告警的日志在保存点成功之后记（外层提交失败时可能多一条告警，只供人工核对，PR 写明）；模块说明里「本模块不写日志」一句随之改写
      - app/services/stripe_refund.py 新增公开函数 retry_request_attempt（入参数据库会话、退款申请 ID、当前时间）——锁顺序同上；只在申请已批准、该申请最新一条尝试（按尝试 ID 取最大者）为 failed 时新建一条 pending 尝试，返回新尝试的 ID；核对合计同上，超过则不建、记一条告警日志（类别 stripe_refund_cap，这一情形不写库，日志在抛出前记）并抛 RefundCapExceeded；申请不存在、未批准、没有尝试或最新一条不是 failed 时抛各自的异常、不写库；同一来源并发重试由活动来源列的唯一约束保证只有一个成功，失败的一方回滚保存点并抛「不能重试」；只 flush 不提交，由之后的接口提交并调用 submit_refund_attempt
      - 测试验证意图——新文件 tests/test_stripe_refund_approval.py 用 SQLite 内存库与 SHOP-TASK-117 测试里的写法准备 Stripe 付款的订单与退款申请，验证——Stripe 付款订单批准后有一条 pending 尝试、金额等于申请的现金退款额（不含运费）、来源为该申请；按幂等键重放不再建；金额为 0 时不建；模拟支付订单不建；同一 PaymentIntent 已有的 pending 或 succeeded 尝试（测试直接插入复用该付款 PaymentIntent ID 的尝试来构造）加本次超过付款金额时不建且告警一次、批准照常成功、failed 的尝试不计入；缺 PaymentIntent 时不建且告警；手动重试在最新为 failed 时新建、在 pending 或 succeeded 时拒绝、未批准时拒绝、超额时抛 RefundCapExceeded 且不写库；第二次并发插入被唯一约束拒绝；每条测试写明它守住的是设计的哪一句；tests/test_refund_review.py 一字不改且照常通过；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/task-records/SHOP-TASK-125.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；批准时建尝试的条件、合计核对、手动重试、偏离、验证到什么程度，并写明调用 Stripe 由定时任务与之后的后台接口发起），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/api/、app/jobs.py、app/services/stripe_confirm.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.54），不合并、不直推
```

# SHOP-TASK-130 后台退款详情给出 Stripe 付款与退款尝试

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条（后台退款详情显示各次尝试的 Stripe 退款状态；按记下的实际付款方式类型显示期限提示；最新一次尝试为 failed 时管理员可手动重试）与第 10 条（不读取、不保存 Stripe 返回的客户资料），以及 `docs/UX.md` 0.11 的 A03「Stripe 测试（0.11）」，在 `app/services/admin_refunds.py` 的 `AdminRefundDetail` 加入 `paid_via_stripe`、`payment_method_type` 与 `stripe_refunds`；`GET /api/admin/refunds/{refund_id}` 的响应随之多出这三个字段。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55。
- 三个字段的取值（`refund_detail` 的签名不变，仍只读：不写审计、不锁行、不调用 Stripe）：
  - `paid_via_stripe`：该订单有确认时间非空的会话记录（至多一条）时为真，与 `app/services/stripe_refund.py` 的 `add_request_attempt` 判定相同，但只普通读取、不锁。没有记录、只有 expired 或未确认记录（经模拟支付，含迟到付款）时为假。审核中、已批准、已拒绝的取值规则相同。
  - `payment_method_type`：该记录的 `payment_method_type` 原样，尚未补齐时为 null；不是 Stripe 付款时为 null。
  - `stripe_refunds`：`refund_request_id` 等于本申请的尝试，按尝试 ID 从大到小（最新在前），每条只有 `id`、`created_at`（照 `_utc` 带 UTC 时区）、`amount_sen`、`status`（pending / succeeded / failed 原样）与 `stripe_refund_id`（没有时为 null）。新模型 `AdminRefundStripeRefund` 照本模块的写法。来源为会话的尝试与同一订单其他申请的尝试不在其中；没有尝试时为空列表。查询只选这五列，`last_error_type`、调用次数、告警与复查时间、PaymentIntent ID 与会话 ID 不读也不返回。
  - 期限提示（按类型选 fpx / grabpay / 其他）与能否重试（最新一条为 failed）由页面从这三个字段推出，接口不另给。
- `app/api/admin_refunds.py` 未改：`AdminRefundDetailOut` 继承 `AdminRefundDetail`，检查顺序与错误不变。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 判定「由 Stripe 付款」的查询在本模块另写一份（`_stripe_payment`），不复用 `stripe_refund.py` 的 `_lock_paid_checkout`：那个函数会按主键加 FOR UPDATE，而详情须只读、不锁行。
  - 测试里「未确认会话记录」取三种：没有付款的 expired 记录、带 paid 与完成时间的 expired 记录（迟到付款，HANDOFF 0.53）、确认时间为空的 complete 记录。
- 测试：
  - 新文件 `tests/test_admin_refund_stripe_detail.py`（TestClient 与 SQLite 内存库，夹具与后台登录照 `tests/test_admin_refunds.py`，会话记录与尝试照 `tests/test_stripe_refund_approval.py` 的 `_stripe_paid` 直接写入）覆盖：Stripe 付款且已批准的申请为真、类型原样（fpx）、两条尝试按 ID 倒序且五个字段与库里相同；同一订单另一申请的尝试与来源为会话的尝试不出现；类型为 grabpay、card 原样，为空时为 null；审核中、已拒绝与现金退款额为 0 的 Stripe 付款申请为真且列表为空；模拟支付订单（没有记录、expired 或未确认记录）在三种状态下为假、null 与空列表；`last_error_type` 非空的 failed 尝试，响应不含它、PaymentIntent ID、会话 ID 与调用次数等列名，每条尝试只有五个键；接口读取前后各表不变，直接调用 `refund_detail` 只有查询、以 MySQL 方言编译都不带 FOR UPDATE。每条测试的文档字符串写明它守住的设计或 UX 原句。
  - `tests/test_admin_refunds.py` 只改 `test_detail_fields_amounts_and_reviewer`：它对响应整体做相等断言，期望里补上游客模拟支付订单的取值（`paid_via_stripe` 为假、`payment_method_type` 为 null、`stripe_refunds` 为空列表），改动处注明原因；该文件其余测试未改。`tests/test_admin_refund_points.py` 与 `tests/test_admin_refund_order_filter.py` 未改。
- 留给之后的任务：批准后调用 Stripe（以 `submit_refund_attempt` 发起）与手动重试接口留给 SHOP-TASK-131；A03 页面（付款方式、期限提示、尝试表与重试按钮）留给之后的前端任务。
- 改动只在 `app/services/admin_refunds.py`、`tests/test_admin_refund_stripe_detail.py`、`tests/test_admin_refunds.py` 与本文件；未改 `app/api/`、`app/services/stripe_refund.py`、`app/services/refund_review.py`、模型、迁移、`.platform/`、CI、部署配置、前端与 `docs/TODO.md`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8、10 条与 UX A03「Stripe 测试（0.11）」原句自查，并核对 `app/models/stripe.py` 的列与检查约束（测试直接写入的会话记录与尝试满足各检查约束与活动来源列的唯一约束）。新增测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

# SHOP-TASK-224 迟到付款退款的手动重试与 Stripe 待处理列表的查询（服务层）

- [x] 依据：`docs/DESIGN.md` 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条第（2）项与第 8 条；列表依据第 11 条的两类列表与各自的退出条件；`docs/HANDOFF.md` 0.90 记录的 Kelvin 2026-11-08 决定（「失败时间」以最新一次尝试的创建时间 `attempted_at` 代替）。设计闸门：DESIGN 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条第（2）项与第 8 条。只做服务层；审计、提交、调用 Stripe 与响应由 SHOP-TASK-225 的接口处理。不改接口、前端、数据库与迁移、设计，不加依赖。
- [x] `app/services/stripe_refund.py`：新增 `RetryCheckoutNotFound` 与 `retry_checkout_attempt(db, checkout_id, now)`，返回新尝试的 ID。
  - `now` 带时区抛 `ValueError`（先于访问数据库）；先 `rollback` 结束此前的事务，以结账记录 ID 的子查询锁住所属订单行（新事务的第一条加锁语句），再按主键锁住该记录；记录不存在抛 `RetryCheckoutNotFound`。
  - 取 `source_type` 为 checkout、`checkout_id` 为该记录的尝试中 ID 最大的一条：没有抛 `NoRefundAttempt`，不是 failed 抛 `RetryNotAllowed`。
  - 以 `refunded_total` 锁住该尝试 PaymentIntent 的尝试，已有合计加原尝试金额超过原尝试金额时记 error 日志 `stripe_refund_cap checkout=<ID>`，紧接着调用一次 `ops_alert.alert(LOG_REFUND_CAP, ("stripe_checkout", 结账记录 ID))`（对象类别取 `stripe_checkout.ALERT_TARGET_KIND`），再抛 `RefundCapExceeded`。
  - 否则在保存点里写一条 pending 尝试（PaymentIntent 与金额同原尝试，来源与 `active_checkout_id` 为该记录，调用次数 0，创建时间 `now`）；撞上活动来源列的唯一约束只回滚保存点、抛 `RetryNotAllowed`。只 flush、不提交。
  - 模块说明补上本项的依据与落实写法；`NoRefundAttempt`、`RetryNotAllowed` 的文档字符串补上来源为结账记录的情形。
- [x] `app/services/admin_stripe_pending.py`：新文件，Pydantic 模型 `PendingCheckout`、`FailedLateRefund`、`StripePendingOut` 与只读查询 `stripe_pending(db)`。
  - `checkouts`：告警时间非空、状态不是 expired、订单为 `awaiting_demo_payment` 的结账记录，按告警时间从早到晚、同时刻按 ID；字段 `checkout_id`、`status`、`order_number`、`created_at`、`alerted_at`。
  - `refunds`：来源为会话、该来源 ID 最大的尝试为 failed 的那条尝试，不看订单状态与记录是否 expired，按尝试 ID 从小到大；字段 `checkout_id`、`attempt_id`、`order_number`、`amount_sen`、`attempted_at`（尝试的创建时间）、`error_type`（`last_error_type`，可空）。
  - 不分页；不读收货资料与会员资料，不返回 Stripe 的任何 ID、付款页地址与创建参数快照；时间换成带 UTC 时区（同 `admin_refunds.py`）；读完 `rollback` 结束读事务，不写日志。
- [x] `tests/test_stripe_late_refund_retry.py`、`tests/test_admin_stripe_pending.py`：新文件，见下面「验证到什么程度」。

## 偏离与取舍，请审阅

- 第 11 条第（2）项每行显示「失败时间」，而 `StripeRefund` 没有失败时间列（只有创建、成功与上次复查时间）。按 HANDOFF 0.90（Kelvin 2026-11-08 认可），列表给最新一次尝试的创建时间，字段名 `attempted_at`；不加字段、不改设计。
- 合计核对照验收标准写成「已有合计加原尝试金额超过原尝试金额」，即同一 PaymentIntent 另有任何 pending 或 succeeded 的尝试就不建；付款金额取原尝试金额（第 6 条写入时等于会话的 `amount_total`），不读结账记录的 `amount_sen`。
- 锁订单的语句以结账记录的子查询找订单（同 `retry_request_attempt` 以退款申请的子查询），子查询本身不加锁；随后才按主键锁住结账记录，顺序为订单 → 结账记录 → 退款尝试，与模块里写的锁顺序一致。
- `stripe_refund.py` 新从 `app/services/stripe_checkout.py` 导入 `ALERT_TARGET_KIND`；那个模块及其顶层导入都不导入本模块，不形成循环导入。
- 列表的时间与后台其他视图一样换成带 UTC 时区的时间；验收标准没写时区，之后的接口原样返回。

## 验证到什么程度

- 人工逐条对照验收标准与 DESIGN 1.22「Stripe 测试环境支付」第 11 条第（2）项、第 8 条与第 11 条的两类列表自查。
- `tests/test_stripe_late_refund_retry.py`（SQLite 内存库，`ops_alert.alert` 以 monkeypatch 替身记录调用）覆盖：成功时新尝试的各字段、原 failed 尝试不变、不记日志不告警；带时区的 `now` 先于访问数据库被拒；先 rollback、订单行为新事务第一条加锁语句、锁顺序订单 → 结账记录 → 退款尝试、只 flush 不提交；记录不存在抛 `RetryCheckoutNotFound`；没有来源为该记录的尝试抛 `NoRefundAttempt`；最新一次为 pending 或 succeeded 抛 `RetryNotAllowed`；同一 PaymentIntent 另有 pending 或 succeeded 时的日志、告警调用与 `RefundCapExceeded`，failed 不计入；唯一约束冲突只回滚保存点并抛 `RetryNotAllowed`。抛出时都不写库。
- `tests/test_admin_stripe_pending.py` 覆盖：三个模型名与字段名；第（1）项的字段、排序（同一告警时间按 ID）与退出条件（告警时间为空、已 expired、订单已取消或已付款不列出）；第（2）项的字段、只列最新一条、按尝试 ID 排序、错误类别可空，最新一次为 pending 或 succeeded 时不列出，订单已取消、已付款或仍待支付，记录已 expired 或尚未 expired 时仍列出；结果不含收货资料、Stripe 的会话 / PaymentIntent / 退款 ID、付款页地址、创建幂等键与快照；只发 SELECT、结束读事务。每条测试的文档字符串写明它守住的第 11 条哪一句。

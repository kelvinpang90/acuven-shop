# SHOP-TASK-223 作废已告警的 Stripe 结账记录并取消订单（服务层）

- [x] 依据：`docs/DESIGN.md` 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条第（1）项与「订单与退款状态」第 3 条；`docs/HANDOFF.md` 0.90 记录的 Kelvin 2026-11-08 决定（操作者 admin 的取消对访客归为 timeout，不改 UX 与文案）。设计闸门：DESIGN 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条第（1）项与「订单与退款状态」第 3 条。只做服务层；管理员的核实勾选、审计与响应由 SHOP-TASK-225 的接口处理。不改接口、前端、数据库与迁移、设计，不加依赖。
- [x] `app/services/payment.py`：新增 `VoidResult`（`confirmed`、`cancelled`、`voided`、`changed`、`unavailable`）、`CheckoutNotFound` 与 `void_alerted_checkout(db, gateway, checkout_id, now, *, on_void=None)`。
  - 入口：`now` 带时区抛 `ValueError`（先于访问数据库）；先结束当前事务，以普通读取取记录与订单后结束读事务；记录不存在抛 `CheckoutNotFound`；告警时间为空、记录为 expired 或订单不是 `awaiting_demo_payment` 时不调用 Stripe、不写库，返回 `changed`。
  - creating：不调用 Stripe，直接进入人工作废。
  - 有会话 ID：先 `retrieve_checkout_session` 一次，核对会话 ID、元数据里的创建幂等键与记录相同且 `livemode` 为假（`_session_belongs`，同 `stripe_overdue.py` 的 `_belongs`）。已付款或 complete 时以这次查询到的会话调用 `confirm_checkout`：`paid` 为 `confirmed`；`refunded` 时不给记录 ID 以 system 调用 `cancel_after_stripe_end`；`needs_review`、`pending` 进入人工作废；`unavailable` 按给不出确定结果。expired 时给记录 ID 以 system 调用 `cancel_after_stripe_end`。open 时请求 `expire_checkout_session` 一次，返回的会话为 expired（同样核对）则同上，否则按给不出确定结果，不再查询。`cancel_after_stripe_end` 的 `cancelled`、`changed` 照传，`kept` 时按订单当前状态（`demo_paid` 为 `confirmed`，其他为 `changed`）。不复用 `_end_open`。
  - 给不出确定结果：读取时付款状态已是 paid 返回 `unavailable`、不写库；否则进入人工作废。
  - 人工作废（`_void`）：先以普通读取重新取记录；之后一个事务里按会员、订单、记录的顺序加锁重查（记录仍属该订单、状态与重新读到的相同、不是 expired、告警时间非空、订单仍待支付、没有其他不是 expired 的记录，付款状态为 paid 只在 needs_review 时允许），不满足回滚返回 `changed`；满足则记录记为 expired、`active_order_id` 置空，经现有 `_cancel`（带条件的 UPDATE，不看本地支付到期时间；加回库存、释放券与积分预占、写一条 admin 事件），flush 后调用 `on_void(db)`，提交并返回 `voided`。`on_void` 抛出时回滚、异常原样传出。
  - `_cancelled_by`：操作者 system 或 admin 的取消事件都为 `timeout`，guest、member 为 `self`；文档字符串与模块说明随之更新。`cancel_after_stripe_end` 与 `cancel_order` 的操作者范围不变。本函数不写审计、不记日志、不发告警、不清空告警时间。
- [x] `app/services/order_rules.py`：`(awaiting_demo_payment, demo_cancelled)` 的操作者加上 admin，注释与模块说明写明只经 DESIGN 1.22 第 11 条的人工作废；其他迁移不变。`tests/test_order_rules.py` 只改这一处的期望表与对应测试的文档字符串。
- [x] `tests/test_stripe_void.py`：新文件，见下面「验证到什么程度」。

## 偏离与取舍，请审阅

- 会话 complete、未付款而 PaymentIntent 已是 `canceled` 或 `requires_payment_method`（Stripe 给出的付款失败）：按验收标准经 `confirm_checkout`，它返回 `pending`，于是进入人工作废（admin），而不是像 `stripe_overdue.py` 那样以 system 取消。订单同样取消、库存同样释放，差别只在事件操作者与之后的审计结果（`voided` 而不是 `cancelled`）。如要按第 5 条把这种情形算作确定结果，需另开任务。
- 「状态与这次读到的相同」：凭核实作废前重新读取记录。在 `pending`、`needs_review` 两种情形，`confirm_checkout` 自己可能已改了记录（如 open 改为 complete），所以以重新读到的状态为准；在 creating 与给不出确定结果两种情形，本函数没有改过记录，重新读到的状态还须等于最初读到的状态，否则返回 `changed`（避免把并发中刚变为 open 的 creating 记录不经 Stripe 作废）。
- 作废请求的返回也按 `_session_belongs` 核对，与查询的核对一致。
- 会话 complete 而未付款时 Stripe 不会返回展开的 PaymentIntent，`confirm_checkout` 按现有规则会再查询一次（与 `test_stripe_cancel.py` 的同类情形相同）；「不再查询第二次」只针对 open 会话作废之后。
- 状态为 open 或 complete 而没有会话 ID 的记录（正常不会出现）按给不出确定结果处理。
- `confirm_checkout` 自己的告警日志与 `ops_alert.alert`（如 `stripe_late_payment_unrefundable`）照现有规则仍会发生；本函数本身不记日志、不发告警。

## 验证到什么程度

- 人工逐条对照验收标准与 DESIGN 1.22「Stripe 测试环境支付」第 11 条第（1）项、「订单与退款状态」第 3 条自查。
- `tests/test_stripe_void.py`（FakeStripeGateway 与 SQLite 内存库，`ops_alert.alert` 以替身代替）覆盖：带时区的 `now` 先于访问数据库被拒；记录不存在抛 `CheckoutNotFound`；四种前置条件不满足返回 `changed`、不调用 Stripe、不写库；creating 不调用 Stripe 即作废（会员订单的库存、券与积分预占释放，事件操作者 admin，一次提交，告警时间不清空）；作废后 `pay_orders` 的取消原因为 `timeout`；已付款相符为 `confirmed`（只查询、不请求作废）；金额不符写成退款尝试后为 `cancelled`；查询为 open 时作废成功为 `cancelled`、查询为 expired 时不请求作废即 `cancelled`；`cancel_after_stripe_end` 的 `kept`（订单已付款为 `confirmed`、已取消为 `changed`）与 `changed` 的归类；查询不成、会话 ID 或创建幂等键不符、livemode、状态不认识，以及作废不成或仍为 open 时作废（不再查询）；complete 而付款未定（含 open 记录被确认改为 complete 后重新读取）作废；已付款而写不成退款尝试（needs_review）作废且不建退款尝试；确认返回 unavailable 时作废；本地已付款而 Stripe 不可用或答复冲突返回 `unavailable`、不写库；Stripe 处理期间记录被改为 complete、付款状态改为 paid 或订单被取消时返回 `changed`；`on_void` 在提交前以同一会话调用、看得到已作废的记录与订单，审计与作废一次提交；`on_void` 抛出时记录与订单都不变、异常原样传出；作废后同一会话的付款经 `confirm_checkout` 写成全额 pending 退款尝试、订单保持已取消；`cancel_after_stripe_end` 与 `cancel_order` 仍拒绝 admin。每条测试的文档字符串写明它守住的第 11 条哪一句。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

# SHOP-TASK-221 Stripe 退款的告警邮件（被拒、不符、成功后失败、长期未定与无法退款）

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 6 条表中 `stripe_refund_unrefundable`、`stripe_refund_rejected`、`stripe_refund_failed_after_success`、`stripe_refund_mismatch`、`stripe_refund_unresolved`、`stripe_webhook_refund_rejected` 六行与 `stripe_refund_cap` 一行的第 8 条部分，第 2 条（入队位置跟现有 error 日志一致；批准退款时的告警在调用方提交之前入队，接受外层提交失败时多出一封）与第 8 条第（4）项（`stripe_refund_submit_failed` 不接）；`docs/HANDOFF.md` 0.88 记录的落实写法。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」第 6 条与 HANDOFF 0.88。依赖 SHOP-TASK-217 的发送机制与 SHOP-TASK-220 加入的 `stripe_refund_cap`，发送机制本身未改。不改退款规则、数据库、接口、前端与设计，不加依赖。告警不带 Stripe 的任何 ID、金额与个人资料。
- [x] `app/services/ops_alert.py`：`CATEGORIES` 加六类，都不以对象作去重键（`dedupe_by_target=False`），窗口 `HOUR_SECONDS`（3600 秒）；各有一句中文说明与一段处理指引，指引写「按类别码与退款尝试 ID（或退款申请 ID）查日志」「到 Stripe 测试后台核对」这类文字，不含链接、主机与地址。`stripe_refund_cap` 沿用 SHOP-TASK-220 的条目，未改。模块说明补一句本任务接入的六类。
- [x] `app/services/stripe_refund.py`：新增对象类别常量 `ALERT_TARGET_ATTEMPT = "stripe_refund"` 与 `ALERT_TARGET_REQUEST = "refund_request"`。`submit_refund_attempt` 与 `refresh_refund_attempt` 提交之后的 `attempt=<ID>` 日志（`stripe_refund_rejected`、`stripe_refund_mismatch`、`stripe_refund_failed_after_success`）之后各调用一次 `ops_alert.alert(log, ("stripe_refund", 尝试 ID))`；`retry_request_attempt` 的 `stripe_refund_cap refund_request=<ID>` 日志之后、抛 `RefundCapExceeded` 之前调用一次 `ops_alert.alert(LOG_REFUND_CAP, ("refund_request", 申请 ID))`。模块说明、常量注释与三个函数的文档字符串随之更新。
- [x] `app/services/stripe_refund_overdue.py`：`_reconcile` 里对账找到退款后按 `alert_category` 记的日志，与 `stripe_refund_unresolved` 日志之后，各调用一次 `ops_alert.alert(该类别, ("stripe_refund", 尝试 ID))`。模块说明、常量注释与 `_reconcile` 的文档字符串随之更新。
- [x] `app/services/stripe_webhook.py`：`_handle_refund` 里 `stripe_webhook_refund_rejected` 日志与提交之后按 `alert_category` 记的日志之后，各调用一次 `ops_alert.alert(该类别, ("stripe_refund", 尝试 ID))`。模块说明、常量注释与 `_handle_refund` 的文档字符串随之更新；SHOP-TASK-220 接入的结账记录告警未改。
- [x] `app/services/refund_review.py`：`_review` 保存点成功之后的 `refund_request=<ID>` 日志（`stripe_refund_cap`、`stripe_refund_unrefundable`）之后调用一次 `ops_alert.alert(alert, ("refund_request", 申请 ID))`，与日志一样在调用方提交之前。模块说明与 `_review` 的文档字符串随之更新。
- [x] `app/api/admin_refunds.py` 未改（`stripe_refund_submit_failed` 不接，第 8 条第（4）项）。四个模块的日志、返回值与事务都没有改，调用位置紧接在现有日志之后。

## 偏离与取舍，请审阅

- 对象类别常量定义在 `app/services/stripe_refund.py`，其余三个模块从那里导入；`stripe_refund.py` 只导入 `ops_alert` 与 `stripe_gateway`，不形成循环导入。常量名用 `ALERT_TARGET_ATTEMPT`，不沿用 `ALERT_TARGET_KIND`：`stripe_webhook.py` 已从 `stripe_checkout.py` 导入了同名常量（对象为结账记录）。
- `refund_review.py` 里保存点之后的局部变量仍叫 `alert`（现有代码），模块以 `ops_alert.alert` 调用，不冲突。
- `stripe_refund_rejected` 与 `stripe_refund_failed_after_success` 的处理指引写「来源为退款申请的可在后台退款申请页手动重试，其余在 Stripe 测试后台人工处理」：来源为结账记录的尝试（迟到付款）没有手动重试。
- `tests/test_stripe_refund.py` 与 `tests/test_stripe_refund_overdue.py` 原有的 `alerts` 夹具是读 error 日志的 caplog，未改；告警的替身在这两个文件里另起名为 `ops_alerts`。`tests/test_stripe_webhook_refund.py` 与 `tests/test_stripe_refund_approval.py` 没有同名夹具，替身照 SHOP-TASK-219、220 叫 `alerts`。四个替身都是 autouse，每条测试都不入真实队列。
- `tests/test_stripe_refund_overdue.py` 新增一条测试 `test_reconcile_found_but_mismatched_alerts_once`：原有测试没有覆盖对账找到退款而按 `alert_category` 记日志的那一处告警点（对账找到、元数据相符而 PaymentIntent 不符，记 `stripe_refund_mismatch`）。
- `submit_refund_attempt` 里退款对象已失败而尝试期间被写为 succeeded 的 `stripe_refund_failed_after_success` 一支，原有测试没有覆盖，本任务未补；它与同函数里另两类走同一行告警调用。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 2、6、8 条与 HANDOFF 0.88 自查。
- `tests/test_stripe_refund.py`：明确拒绝创建时以 `("stripe_refund", 尝试 ID)` 调用一次 `stripe_refund_rejected`，之后跳过的那次不调用；退款接口响应与查询到的退款不符时各调用一次 `stripe_refund_mismatch`，第二次不符不再调用；查询到成功后失败调用一次 `stripe_refund_failed_after_success`；写入规则本身、相符的 pending 响应、期间被别处写入的拒绝、不可用与 live、查询不可用或被拒都不调用。
- `tests/test_stripe_refund_overdue.py`：对账找不到与列不完时以 `("stripe_refund", 尝试 ID)` 调用一次 `stripe_refund_unresolved`，下一轮不再调用；告警时间已写过时不调用；对账找到而不符时调用一次 `stripe_refund_mismatch`，找到且相符不调用；定时查询到成功后失败调用一次 `stripe_refund_failed_after_success`，之后不再到期、不再调用。
- `tests/test_stripe_webhook_refund.py`：退款查询被拒时以 `("stripe_refund", 尝试 ID)` 调用一次 `stripe_webhook_refund_rejected`；成功后失败与不符各调用一次对应类别；正常改为 succeeded、查询不可用与撞上同一事件 ID 回滚时不调用。
- `tests/test_stripe_refund_approval.py`：批准时合计超过付款金额以 `("refund_request", 申请 ID)` 调用一次 `stripe_refund_cap`，建成尝试时不调用；缺 PaymentIntent ID 调用一次 `stripe_refund_unrefundable`；手动重试超过合计在抛出之前调用一次 `stripe_refund_cap`（对象为退款申请）。
- 每条新断言所在测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

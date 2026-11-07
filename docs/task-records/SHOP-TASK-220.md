# SHOP-TASK-220 Stripe 确认付款与 webhook 的告警邮件（正式环境、查询被拒、会话不符与迟到付款）

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 6 条表中 `stripe_livemode`、`stripe_confirm_rejected`、`stripe_session_mismatch`、`stripe_late_payment`、`stripe_late_payment_unrefundable` 五行与 `stripe_refund_cap` 一行的第 6 条部分，以及第 2 条（入队位置跟现有 error 日志一致）；`docs/HANDOFF.md` 0.88 记录的落实写法。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」第 6 条与 HANDOFF 0.88。依赖 SHOP-TASK-217 的发送机制（`ops_alert.alert`），发送机制本身未改。不改付款确认与 webhook 规则、数据库、前端与设计，不加依赖。告警不带 Stripe 的任何 ID、金额与个人资料。
- [x] `app/services/ops_alert.py`：`CATEGORIES` 加六类，都不以对象作去重键（`dedupe_by_target=False`），窗口 `HOUR_SECONDS`（3600 秒）；各有一句中文说明与一段处理指引，指引写「按类别码与结账记录 ID 查日志」「到 Stripe 测试后台核对」这类文字，不含链接、主机与地址。`stripe_refund_cap` 的说明与指引同时适用于迟到付款（对象为结账记录）与退款申请（对象为退款申请，SHOP-TASK-221 接入）两处。模块说明补一句本任务接入的六类。
- [x] `app/services/stripe_confirm.py`：`confirm_checkout` 里查询结果为 live 与 rejected 的两条 `checkout=<ID>` 日志之后，与按确认结果记的那条日志（`stripe_session_mismatch`、`stripe_livemode`、`stripe_late_payment`、`stripe_late_payment_unrefundable`、`stripe_refund_cap`，在提交之后记）之后，各调用一次 `ops_alert.alert(该日志的类别, ("stripe_checkout", 结账记录 ID))`。类别取日志里的同一常量或变量；对象类别取 `stripe_checkout.ALERT_TARGET_KIND`。模块说明、常量注释与 `confirm_checkout` 的文档字符串随之更新。
- [x] `app/services/stripe_webhook.py`：`_handle_checkout` 里结账记录的 `stripe_livemode` 与 `stripe_confirm_rejected` 两条日志之后各调用一次 `ops_alert.alert(该日志的类别, ("stripe_checkout", 结账记录 ID))`。退款尝试的两条日志（`stripe_webhook_refund_rejected` 与按 `alert_category` 记的那条）未接，留给 SHOP-TASK-221。模块说明与 `_handle_checkout` 的文档字符串随之更新。
- [x] `app/api/stripe_webhook.py`：入口的 `stripe_livemode` 日志之后调用 `ops_alert.alert(LOG_LIVEMODE)`（对象为无），仍回 400 `livemode_rejected`。模块说明第 5 步随之更新。
- [x] 三个模块的日志、返回值、状态码与事务都没有改，调用位置与现有日志相同（日志在提交之后的，告警也在提交之后）。

## 偏离与取舍，请审阅

- 对象类别常量沿用 SHOP-TASK-219 定义在 `app/services/stripe_checkout.py` 的 `ALERT_TARGET_KIND`，`stripe_confirm.py` 与 `stripe_webhook.py` 从那里导入；`stripe_checkout.py` 不导入这两个模块，不形成循环导入。
- `stripe_confirm.py` 里按确认结果记的日志只有一处（`confirm_checkout` 末尾按 `_write` 或 `_decide` 给出的类别记），告警也只接在这一处；给出的对象 livemode 为真（`_Step.LIVE`）也经这一处记 `stripe_livemode checkout=<ID>`，所以同样告警。`_settle_late` 的 held 两类只在首次看到这笔付款时给出类别（SHOP-TASK-117 的「只告警一次」），重复确认没有日志，也不告警。
- webhook 经 `confirm_checkout` 确认时，确认函数里的告警由 `stripe_confirm.py` 发出，`stripe_webhook.py` 不另报。
- 处理指引里 `stripe_refund_cap` 写了「在后台退款申请页或 Stripe 测试后台人工处理」，`stripe_late_payment_unrefundable` 写了「在 Stripe 测试后台人工处理」，都不含链接与主机。
- 测试文件里的替身是四个文件各自的 autouse 夹具 `alerts`（以 monkeypatch 替换 `app.services.ops_alert.alert`，写法照 SHOP-TASK-219），每条测试都不入真实队列。`tests/test_stripe_webhook.py` 里原有的 `_alerts(caplog)` 辅助函数（读 error 日志）未改。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 2、6 条与 HANDOFF 0.88 自查。
- `tests/test_stripe_confirm.py`：查询结果为 live 与 rejected 时以 `("stripe_checkout", 记录 ID)` 各调用一次 `stripe_livemode` 与 `stripe_confirm_rejected`，unavailable 不调用；给出的对象 livemode 为真调用一次 `stripe_livemode`；会话不符调用一次 `stripe_session_mismatch`；迟到或不符的付款调用一次 `stripe_late_payment`，已为 expired 的记录改回并确认不调用；正常确认与未付款的会话不调用。
- `tests/test_stripe_late_payment.py`：写成退款尝试时调用一次 `stripe_late_payment`（用例核对调用时日志已记、另一个数据库会话读到尝试已提交），重复确认与已有 failed 尝试不再调用；合计上限调用一次 `stripe_refund_cap`（含告警时间先因别的原因写过的首次看到），缺 `amount_total` 调用一次 `stripe_late_payment_unrefundable`，再确认都不再调用；旧会话交给新记录调用一次 `stripe_session_mismatch`（对象为新记录）；改回确认与未付款的会话不调用。
- `tests/test_stripe_webhook.py`：查询结果为 live 与 rejected 时以 `("stripe_checkout", 记录 ID)` 调用一次对应类别；查询不可用不调用；经确认函数核对不符时调用一次 `stripe_session_mismatch`。
- `tests/test_stripe_webhook_api.py`：livemode 事件在日志之后以 `("stripe_livemode", None)` 调用一次，仍回 400；测试环境的已付款事件不调用。
- 每条新断言所在测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

## 登记时的验收标准（原文）

2026-11-08 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 接线——app/services/stripe_confirm.py 每条 checkout=<ID> 的告警日志（stripe_livemode、stripe_confirm_rejected，以及按确认结果记的 stripe_session_mismatch、stripe_late_payment、stripe_late_payment_unrefundable、stripe_refund_cap）、app/services/stripe_webhook.py 结账记录的 stripe_livemode 与 stripe_confirm_rejected 两条日志之后，各调用一次 ops_alert.alert(该日志的类别, ("stripe_checkout", 结账记录 ID))；app/api/stripe_webhook.py 入口的 stripe_livemode 日志之后调用 ops_alert.alert(LOG_LIVEMODE)；app/services/stripe_webhook.py 里退款尝试的两条日志不在本任务（SHOP-TASK-221）；调用位置与现有日志相同，不改日志、返回值、状态码与事务
      - CATEGORIES 加上这六类（中文说明、处理指引、不以对象作去重键、窗口 3600 秒），处理指引不含链接与主机，写成到 Stripe 测试后台核对、按类别码查日志这类文字；stripe_refund_cap 的说明同时适用于迟到付款与退款申请两处
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——tests/test_stripe_confirm.py、tests/test_stripe_late_payment.py、tests/test_stripe_webhook.py、tests/test_stripe_webhook_api.py 以 monkeypatch 替换 app.services.ops_alert.alert 记录调用，在现有覆盖这些日志的情形里补断言（每个告警点以所写类别与对象调用一次；没有告警日志的结果不调用；webhook 入口的对象为 None）；现有测试只为替换 alert 而改；每条新断言所在测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行
      - docs/task-records/SHOP-TASK-220.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改付款确认与 webhook 规则、发送机制、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.21 提交 5be346e 与 HANDOFF 0.88），不合并、不直推
```

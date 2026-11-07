# SHOP-TASK-219 Stripe 结账创建与对账的告警邮件（密钥、创建与超时未定）

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 6 条表中 `stripe_key_not_test`、`stripe_checkout_needs_review`、`stripe_create_rejected`、`stripe_unreachable`、`stripe_payment_needs_review` 五行与第 2 条；`docs/HANDOFF.md` 0.88 记录的落实写法（含第 9 项）。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」第 6 条与 HANDOFF 0.88。依赖 SHOP-TASK-217 的发送机制（`ops_alert.alert`），发送机制本身未改。不改支付与对账规则、数据库、接口、前端与设计，不加依赖。告警不带 Stripe 的任何 ID、金额与个人资料。
- [x] `app/services/ops_alert.py`：`CATEGORIES` 加五类，都不以对象作去重键（`dedupe_by_target=False`），窗口 `HOUR_SECONDS`（3600 秒）；各有一句中文说明与一段处理指引，指引写「按类别码与结账记录 ID 查日志」「到 Stripe 测试后台核对」这类文字，不含链接、主机与地址（`stripe_key_not_test` 的指引写核对私有配置里的密钥、只用 `sk_test_` 或 `rk_test_` 测试密钥、改好后重启 API 与定时任务容器）。模块说明补一句本任务接入的五类。
- [x] `app/services/stripe_gateway.py`：`_warn_key_not_test` 在现有的「每个进程第一次判定」之内、`stripe_key_not_test` 日志之后调用 `ops_alert.alert(LOG_KEY_NOT_TEST)`（对象为无），同一进程只调用一次。`_check_livemode` 里不带记录 ID 的 `stripe_livemode` 日志不调用（第 6 条表）。模块说明、`stripe_configured` 文档字符串与常量注释随之更新。
- [x] `app/services/stripe_checkout.py`：新常量 `ALERT_TARGET_KIND = "stripe_checkout"`。`_apply_create_result` 的 `stripe_create_rejected` 日志（创建调用，即首次或到期前的重试，被以 `invalid_request_error` 拒绝、记录已提交为 expired）之后，与 `_mark_needs_review` 的 `stripe_checkout_needs_review` 日志（告警时间已提交）之后，各调用一次 `ops_alert.alert(该日志的类别, ("stripe_checkout", 结账记录 ID))`。模块说明与两个函数的文档字符串随之更新。
- [x] `app/services/stripe_overdue.py`：`_unresolved` 的告警日志（`stripe_unreachable` 或 `stripe_payment_needs_review`，带条件的 UPDATE 写告警时间并提交之后）之后调用一次 `ops_alert.alert(category, ("stripe_checkout", 记录 ID))`；对象类别取 `stripe_checkout.ALERT_TARGET_KIND`。模块说明、常量注释与 `_unresolved` 的文档字符串随之更新。
- [x] 三个模块的日志、返回值与事务都没有改，调用位置与现有日志相同（日志在提交之后的，告警也在提交之后）。

## 偏离与取舍，请审阅

- `stripe_create_rejected`：第 6 条表的触发时机写作「对账时 Stripe 以参数不合格拒绝」，按第 2 条「入队位置跟现有 error 日志一致」与 HANDOFF 0.88 第 9 项，落实为现有日志所在的创建调用（首次或到期前的重试）被拒。到期后对账以 `expires_at` 被拒是「Stripe 测试环境支付」第 3 条的正常路径，现在没有日志，也不调用告警（测试对此有断言）。
- `stripe_unreachable` 的 creating 分支（`_process` 里 `reconcile_creating_checkout` 返回 pending 时的兜底）与 open 分支共用 `_unresolved`，所以也接上了告警；照 SHOP-TASK-116 的记录，实际上满 24 小时的 creating 记录先由对账函数转人工、记 `stripe_checkout_needs_review`，每条记录仍只告警一次。
- 对象类别常量 `ALERT_TARGET_KIND` 定义在 `stripe_checkout.py`，`stripe_overdue.py` 从那里导入（它本来就导入该模块），不在两处各写一遍字符串。
- 测试文件里的替身是三个文件各自的 autouse 夹具 `alerts`（以 monkeypatch 替换 `app.services.ops_alert.alert`），每条测试都不入真实队列；需要核对调用时机的测试在自己的替身里另记调用时已有的日志与另一个数据库会话读到的已提交状态。
- 其他测试文件（如 `tests/test_admin_site_settings.py`、`tests/test_stripe_webhook_api.py` 里用正式环境前缀密钥的用例）会调用真实的 `ops_alert.alert`；它不抛异常、不阻塞，未配置告警邮件时只在 `app.services.ops_alert` 记一条 warning，那些用例不断言这些日志。这些文件不在允许路径内，没有改。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 2、6 条与 HANDOFF 0.88 自查。
- `tests/test_stripe_gateway.py`：非测试密钥在日志之后调用一次 `("stripe_key_not_test", None)`、再次判定不再调用；测试密钥与空密钥不调用；适配器里 `stripe_livemode` 日志不调用。
- `tests/test_stripe_checkout.py`：首次调用与到期前重试被参数错误拒绝时以 `("stripe_checkout", 记录 ID)` 调用一次 `stripe_create_rejected`（首次调用的用例核对调用时日志已记、记录已提交为 expired）；其他失败不调用；到期后对账以 `expires_at` 被拒不调用；满 24 小时调用一次 `stripe_checkout_needs_review`（核对调用时日志已记、告警时间已提交），已有告警时间的记录再次发起与对账不再调用。
- `tests/test_stripe_overdue.py`：定时任务里的重试被拒调用一次 `stripe_create_rejected`、到期后以 `expires_at` 被拒不调用；creating 满 24 小时只调用一次 `stripe_checkout_needs_review`；open 不可用、确认无法核对与未配置时满 24 小时各调用一次 `stripe_unreachable`（open 不可用的用例核对调用时日志已记、告警时间已提交）；complete 满 24 小时仍无结果与查询不可用时调用一次 `stripe_payment_needs_review`；未满 24 小时与已有告警时间的记录不调用。
- 每条新断言所在测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

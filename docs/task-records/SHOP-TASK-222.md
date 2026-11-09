# SHOP-TASK-222 短信预算用尽、管理员登录锁定与会员注销失败的告警邮件

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 6 条表中 `sms_budget_exhausted`、`admin_login_locked`、`member_deletion_failed` 三行，第 2 条（告警点先记一条 error 日志，日志仍是权威记录，再入队；入队位置跟日志一致）与第 8 条第（2）项（短信的其他停发不接）；`docs/HANDOFF.md` 0.88 记录的落实写法。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」第 6 条与 HANDOFF 0.88。依赖 SHOP-TASK-217 的发送机制，发送机制本身未改。不改短信、登录与注销规则，不改数据库、前端与设计，不加依赖。告警与日志不带个人资料、用户名、电话、来源地址与异常消息。
- [x] `app/services/ops_alert.py`：`CATEGORIES` 加三类。`sms_budget_exhausted` 以对象作去重键（`dedupe_by_target=True`），窗口 `DAY_SECONDS`（86400 秒）；`admin_login_locked` 与 `member_deletion_failed` 不以对象作去重键，窗口 `HOUR_SECONDS`（3600 秒）。各有一句中文说明与一段处理指引，指引写「按类别码与马来西亚日期（审计记录 ID、会员 ID）查日志」「到短信服务商后台核对」这类文字，不含链接、主机与地址。模块说明补一句本任务接入的三类。
- [x] `app/services/sms_verification.py`：新增 `logger`、类别常量 `LOG_BUDGET_EXHAUSTED = "sms_budget_exhausted"` 与对象类别 `ALERT_TARGET_DATE = "date"`。`_send` 里预占被拒时照旧 `_suspend`（写 suspended 记录并提交），之后仅当预占结果为 `over_budget` 时记一条 error 日志 `sms_budget_exhausted date=<马来西亚日期>`，紧接着调用一次 `ops_alert.alert("sms_budget_exhausted", ("date", 马来西亚日期的 ISO 写法))`；日期取 `app/services/sms_budget.py` 的 `malaysia_date(now)`。`unknown_cost`、Redis 未配置或不可用、超出限流、人机挑战服务不可用与服务商失败的停发都不记、不告警。停发提交抛异常时照旧换成 `SmsVerificationError` 交给调用方，不告警。返回值与事务不变。模块说明与 `send_verification` 的文档字符串随之更新。
- [x] `app/api/admin_auth.py`：新增 `logger` 与对象类别 `ALERT_TARGET_AUDIT = "audit_event"`；类别码沿用审计操作名 `ADMIN_LOGIN_LOCKED`。登录失败且刚锁定时，在提交之前取出 `admin_login_locked` 审计记录的 ID（`record_audit` 已 flush；提交后 ORM 对象过期，再取会多一次查询），提交之后记一条 error 日志 `admin_login_locked audit_event=<ID>`，紧接着调用一次 `ops_alert.alert("admin_login_locked", ("audit_event", ID))`。用户名不存在的锁定不写审计，也不记、不告警。响应、状态码与审计不变。模块说明随之更新。
- [x] `app/api/member_deletion.py`：新增 `logger`、类别常量 `LOG_DELETION_FAILED = "member_deletion_failed"` 与对象类别 `ALERT_TARGET_MEMBER = "member"`。确认注销时 `delete_member` 或提交抛 `SQLAlchemyError`、`_rollback` 之后记一条 error 日志 `member_deletion_failed member=<会员 ID>`，紧接着调用一次 `ops_alert.alert("member_deletion_failed", ("member", 会员 ID))`，再照旧回答 503 `service_unavailable`。读取开关失败、`delete_member` 返回假（会员已不是 active）时不记、不告警。模块说明随之更新。

## 偏离与取舍，请审阅

- `member_deletion.py` 里 `deleted` 先置为 `None`，异常分支只在 `deleted is not False` 时告警：`delete_member` 返回假之后 `session.rollback()` 本身抛数据库异常的情形，会员本来就不是有效，不属于「删除或提交抛数据库异常」，不告警。回答与原来一样是 503。
- 三处日志都只写类别码、空格与「对象类别=ID」，与 SHOP-TASK-219 至 221 的写法一致（如 `stripe_create_rejected checkout=<ID>`），对象类别在日志里与告警的对象类别同名（`date`、`audit_event`、`member`）。
- `sms_verification.py` 原来写「本模块不写日志」，`admin_auth.py` 与 `member_deletion.py` 原来也写不写日志；三处说明都改为「只写这一条日志」并写明内容。各测试文件原有的「不写日志」测试覆盖的路径都不经过新的告警点，未改。
- `sms_budget_exhausted` 的处理指引写「下一个马来西亚日期按新的一天重新计数」，依据 `sms_budget.py` 按马来西亚日期分行计数；没有写调整上限的具体变量名。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 2、6、8 条与 HANDOFF 0.88 自查。
- 三个测试文件各加一个 autouse 的替身 `alerts`（以 monkeypatch 替换 `app.services.ops_alert.alert`，记下（类别, 对象）），每条测试都不入真实队列；现有测试函数未改，文件的模块说明补了 DESIGN 1.21 对应一行的依据。
- `tests/test_sms_verification.py`：超过费用上限与超过条数上限时以 `("date", "2026-10-09")` 调用一次 `sms_budget_exhausted`，调用时会话已不在事务里（停发已提交），停发记录不变，error 日志只有 `sms_budget_exhausted date=2026-10-09` 一条且日志不含号码、来源与令牌；UTC 16:30 时对象为马来西亚的次日；`unknown_cost`、Redis 未配置或不可用、超出限流、服务商不可用、人机挑战服务不可用与正常发出都不调用、不记日志。
- `tests/test_admin_auth_api.py`：前 9 次失败不调用；第 10 次失败以 `("audit_event", 该审计记录 ID)` 调用一次 `admin_login_locked`，调用时审计已提交（另一个数据库会话读得到），响应仍为 401 `login_failed`，之后的 429 不再调用；app 日志只有 `admin_login_locked audit_event=<ID>` 一条，不含用户名、密码与来源。用户名不存在时失败满 10 次照样锁定，不写审计、不调用、不记日志。
- `tests/test_member_delete_confirm_api.py`：提交抛数据库异常与 `delete_member` 抛数据库异常时以 `("member", 会员 ID)` 调用一次 `member_deletion_failed`，照旧 503 `service_unavailable`、会员仍为 active；app 日志只有 `member_deletion_failed member=<ID>` 一条，不含号码、密码、会话 cookie、来源与异常消息。读取开关出错、会员已不是有效、注销成功与密码错误都不调用。
- 每条新测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

## 登记时的验收标准（原文）

2026-11-08 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - sms_budget_exhausted——app/services/sms_verification.py 在 reserve_sms 的结果为 over_budget、停发（_suspend）提交之后记一条 error 日志并调用一次 ops_alert.alert("sms_budget_exhausted", ("date", 马来西亚日期的 ISO 写法))，日期取 app/services/sms_budget.py 的 malaysia_date；unknown_cost、Redis 不可用、超出限流与服务商失败的停发都不调用；返回值与事务不变
      - admin_login_locked——app/api/admin_auth.py 在登录刚被锁定、写了 admin_login_locked 审计并提交之后记一条 error 日志并调用一次 ops_alert.alert("admin_login_locked", ("audit_event", 该审计记录的 ID))；用户名不存在的锁定不写审计、也不调用；响应、状态码与审计不变
      - member_deletion_failed——app/api/member_deletion.py 的确认注销在 delete_member 或提交抛数据库异常、回滚之后记一条 error 日志并调用一次 ops_alert.alert("member_deletion_failed", ("member", 会员 ID))，然后照现有写法回答暂不可用；读取开关失败、会员已不是有效时不调用
      - CATEGORIES 加上这三类（中文说明、处理指引、去重键与窗口秒数照上），处理指引不含链接与主机；三条新日志不带用户名、电话、来源地址与异常消息
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——tests/test_sms_verification.py、tests/test_admin_auth_api.py、tests/test_member_delete_confirm_api.py 以 monkeypatch 替换 app.services.ops_alert.alert 记录调用，补每个告警点在所写时机以所写类别与对象调用一次、所列不告警的情形不调用、新日志不含电话与用户名；现有测试只为替换 alert 而改；每条测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6 条哪一行
      - docs/task-records/SHOP-TASK-222.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改短信、登录与注销规则、发送机制、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.21 提交 5be346e 与 HANDOFF 0.88），不合并、不直推
```

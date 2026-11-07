# SHOP-TASK-217 运营告警邮件的发送机制（配置、队列与发信线程、限频、测试告警命令）

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 1 至 5 条与第 9 条；`docs/HANDOFF.md` 0.88 记录的落实写法。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」一节与 HANDOFF 0.88（告警邮件的配置、发送、限频与内容约束，邮件不带个人资料与凭据）。本任务只接入测试告警 `ops_alert_test`，不接业务告警点（第 6、7 条的接线是 SHOP-TASK-218 至 222）。不改数据库、接口、前端与设计，不加依赖（只用标准库 `smtplib`、`ssl`、`email`，Redis 沿用已有的 `redis`）。
- [x] 配置（`app/core/config.py`）：`Settings` 加 `alert_smtp_host`、`alert_smtp_port`、`alert_smtp_security`、`alert_smtp_username`、`alert_smtp_password`、`alert_from`、`alert_to` 七个字符串字段（对应 `SHOP_ALERT_` 开头的七个变量），默认都是空串；端口与加密方式不合格不让设置加载失败。是否已配置由 `ops_alert.parse_config` 按第 1 条判断：端口为空即 587、给了须为 1 到 65535 的整数（只认十进制数字，`+25`、`5.0` 不合格）；加密方式为空即 `starttls`、给了须为 `starttls` 或 `ssl`；主机、账号、密码去掉空白后非空；发件人一个地址、收件人逗号分隔 1 到 5 个地址，每个地址去掉两侧空白后恰含一个 `@` 且两侧非空。未配置时告警不入队，进程第一次遇到告警时记一条 warning `ops_alert_not_configured`，带 `missing=`（空的必填变量名）与 `invalid=`（给了但不合格的变量名），不带配置值，之后不再记。
- [x] 新增 `app/services/ops_alert.py`：
  - 入队（第 2 条）：`alert` 只在调用方的线程里做内存操作（读已缓存的设置、加锁改内存队列），不访问数据库与网络，任何情况下都不抛异常、不阻塞。类别不在 `CATEGORIES` 里或为 `ops_alert_test` 时记 warning `ops_alert_unknown_category category=<类别>` 后丢弃；同一限频键已在队列里、正在发送或等待重试时记 info `ops_alert_suppressed category=<类别>`、不入队；队列容量 `QUEUE_CAPACITY = 100`，满时记 warning `ops_alert_dropped category=<类别>` 后丢弃；等待重试的告警放在发信线程的待发列表，不占队列容量。对象与入队时的 UTC 时刻随告警入队。发信线程为守护线程，第一次有告警入队时才启动，每个进程一个（模块级的 `Dispatcher`）。
  - 发信（第 1、3、5 条）：每封新建一次连接，连接、TLS 握手与每条命令的超时都是 10 秒。`starttls` 用 `smtplib.SMTP`，`starttls()` 抛 `SMTPNotSupportedError` 或回 220 以外的状态码都算失败，不登录、不发信；`ssl` 用 `smtplib.SMTP_SSL`；TLS 一律 `ssl.create_default_context()`；一律以账号与密码登录。邮件为纯文本 UTF-8，主题 `[Acuven Shop] <类别码>`；`From` 为发件人，收件人只在 SMTP 信封里，`To: undisclosed-recipients:;`，不设 `Cc` 与 `Bcc`，`Message-ID` 以 `email.utils.make_msgid(domain=<发件人域名>)` 生成，另有 `Date`。正文四项：`类别：<类别码>（<说明>）`、`发生时间（UTC）：…` 与 `发生时间（马来西亚时间 UTC+8）：…` 两行（精确到秒）、`对象：<对象类别> <内部 ID>`（没有对象写「无」；有 `error_class` 时其后加 `（异常类名 <类名>）`）、`处理指引：<指引>`；末尾空一行写限频说明：不以对象作去重键的类别照第 5 条原句，以对象作去重键的写「本类告警按对象限频，限的是本封所写的对象：同一对象在本封之后 60 分钟内（去重键为日期的为「当天」）不再发信，期间只记在日志里，按类别码搜索日志；其他对象照常发信。」；`ops_alert_test` 不写。
  - 失败与重试（第 3 条，见 `_classify` 与 `_settle`）：服务器回了状态码的（`SMTPResponseException` 及其子类，含登录、`SMTPConnectError` 与 STARTTLS 回的码）一律按状态码分类，4xx 临时、其余永久；`SMTPServerDisconnected` 临时；其余 `SMTPException`（含 `SMTPNotSupportedError`）永久；`ssl.SSLError` 先于 `OSError` 判断，`SSLEOFError` 与 `SSLZeroReturnError`（连接被对方中断）临时、其余（含证书校验失败）永久；其余 `OSError`（连不上、超时、连接重置）临时；意外的异常永久。临时失败 60 秒后重试一次，每封最多尝试 2 次；等待重试的放回待发列表，按（到期时刻, 入队序号）取下一封。收件人被拒（部分被拒时 `send_message` 返回的、全部被拒时 `SMTPRecipientsRefused` 带的）按各自的状态码：4xx 的只对这些人重试一次，5xx 的即最终失败，已接受的不重发。一封只记一条最终失败日志 `ops_alert_send_failed category=<类别> error=<异常类名>`，有状态码时另带 `code=`，收件人被拒过的另带 `refused=<最终仍未被接受的人数>`；收件人被拒时 `error=SMTPRecipientsRefused`，`code` 取这些人里按配置顺序第一个人最后一次的状态码；重试在连接阶段失败时 `error` 为该异常类名、没有状态码不带 `code`。发出记 info `ops_alert_sent category=<类别>`。日志不带服务器回复、主机、账号或地址。
  - 限频（第 4 条，见 `RateLimiter`）：每封第一次尝试之前占用一次 `SET <键> 1 NX EX <窗口>`，重试不再占用；占不到记 info `ops_alert_suppressed` 后丢弃；发送失败不删键。键为 `ops_alert:<类别>`，类别以对象作去重键且有对象时为 `ops_alert:<类别>:<内部 ID>`；窗口取类别的 `window_seconds`。Redis 客户端按 `Settings.redis_url` 由本模块的 `_client_for` 自建（连接与读写超时 2 秒、不重试，写法照 `app/services/rate_limit.py`）；未配置、连不上、超时或出错时改在本进程内存里以同样的键与窗口占用，内存里占到的键在窗口内一直有效，之后每次都先查内存、再去 Redis。不访问 MySQL。
  - 补跑命令的等待：`wait_until_sent(timeout=180)` 等队列、待发列表与正在发送的都清空，最多 180 秒，到时仍有未发完的记 warning `ops_alert_unsent count=<封数>`，返回该封数；不抛异常。
  - 测试告警：`send_test_alert(settings)` 在调用的线程里同步发一封 `ops_alert_test`（对象为无），不经队列、不受限频、不碰 Redis，照第 3 条超时与重试（临时失败睡 60 秒重试一次），返回 `SENT`、`NOT_CONFIGURED`（已记 warning `ops_alert_not_configured`）或 `FAILED`（已记 error `ops_alert_send_failed`）。
- [x] `app/jobs.py`：加子命令 `send-test-alert`（`send_test_alert(get_settings())`：发出 0，未配置 `EXIT_NOT_CONFIGURED`，最终失败 `EXIT_FAILED`）；其余七个一次性子命令（`run` 与 `send-test-alert` 以外）执行完后（含抛异常时）调用 `ops_alert.wait_until_sent()`，退出码只取补跑本身的结果；`run` 的常驻循环返回后不等队列。模块说明的用法与退出码注释随之更新。
- [x] `docker-compose.yml`：`shop_api` 与 `shop_jobs` 都加七个 `SHOP_ALERT_` 变量（`${变量名:-}`，照 Stripe 变量）；`shop_jobs` 另加 `SHOP_REDIS_URL: ${SHOP_REDIS_URL:-}`（同 `shop_api`，告警限频与 API 共用）。`.env.example` 的七个变量名已由登记 PR 写好，本任务未改。

## 供 SHOP-TASK-218 至 222 使用

- 引入与调用：`from app.services import ops_alert`，`ops_alert.alert(category, target=None, *, error_class=None)`。调用前照现有写法先记 error 日志（日志仍是权威记录），`alert` 本身不抛异常、不阻塞、不访问数据库与网络，可以在事务之中调用。
- `target` 为（对象类别, 内部 ID）二元组或 `None`。对象类别为小写字母开头的小写字母、数字与下划线（HANDOFF 0.88 的码：`stripe_checkout`、`stripe_refund`、`refund_request`、`job`、`date`、`audit_event`、`member`）；内部 ID 为非负整数，或 1 到 64 个字母、数字、`_`、`.`、`-` 组成的文本（任务名、ISO 日期 `2026-11-07`）。`error_class` 为异常类名（或固定的 `database_not_configured`），只有 `job_failing` 与 `stock_reset_failed` 用。不合格的对象或类名记 warning `ops_alert_invalid_target category=<类别>` 后丢弃（见下面偏离）。
- 类别表：`ops_alert.CATEGORIES: dict[str, AlertCategory]`，每项 `AlertCategory(description, guidance, dedupe_by_target, window_seconds)`：`description` 一句中文说明，`guidance` 一段固定处理指引（两者都不含链接、主机与地址，`tests/test_ops_alert.py` 的 `test_categories_text_has_no_links_or_addresses` 逐项检查），`dedupe_by_target` 为是否以对象作去重键，`window_seconds` 为 `ops_alert.HOUR_SECONDS`（3600）或去重键为日期的 `ops_alert.DAY_SECONDS`（86400）；`None` 表示不限频，只有 `ops_alert_test`。各任务把自己的类别加进这个字典字面量。
- 测试替换：`monkeypatch.setattr(ops_alert, "alert", recorder)`，记下 `(category, target, error_class)` 断言入队时机与次数；调用方必须写 `ops_alert.alert(...)`（经模块属性），不要 `from app.services.ops_alert import alert`。发信本身的替身写法见 `tests/test_ops_alert.py`（`Dispatcher` 可注入配置、SMTP、Redis、时钟、线程启动与等待，`run_due` 同步推进）。

## 偏离与取舍，请审阅

- 对象与异常类名的格式检查：契约没写不合格的对象怎么办。为免调用方把订单号、用户名等文本误写进邮件，`alert` 只接受上面的格式，不合格记 warning `ops_alert_invalid_target category=<类别>`（设计未列出的日志名）后丢弃。
- 未配置的 warning 分 `missing=` 与 `invalid=` 两组变量名（第 1 条只说「可带不合格的变量名」）。账号与密码只判断去掉空白后非空，登录时用原值。
- 不以对象作去重键的类别，末尾限频说明逐字用第 5 条原句（含「（去重键为日期的为当天）」这一括注）；以对象作去重键的类别按 HANDOFF 0.88 写明限的是本封所写的对象。
- `starttls()` 回 220 以外的状态码时 smtplib 不抛异常，这里抛 `SMTPResponseException(<码>)` 按状态码分类（4xx 临时、5xx 永久），日志 `error=SMTPResponseException code=<码>`。
- 邮件加了 `Date` 头（发送时的 UTC）；第 5 条未写，是常规邮件头，不含配置值与地址。
- 发信线程里读取配置时发现已变为未配置（设置是缓存的，正常不会发生），这封丢弃，不另记日志（只在第一次时记 `ops_alert_not_configured`）。
- `send_test_alert` 由 `app/jobs.py` 传入 `jobs.get_settings()` 的结果，测试可沿用替换 `jobs.get_settings` 的写法。
- `docker-compose.yml` 的 `shop_jobs` 原有的注释「运营告警邮件接入前以此代替告警」未改：告警的接线（`job_failing`）在 SHOP-TASK-218。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 1 至 5、9 条与 HANDOFF 0.88 自查。没有连真实的 SMTP 服务器或 Redis 发过信，TLS 握手、证书校验与各服务商的回复只以替身模拟；`smtplib` 的真实行为（`starttls` 的返回值、部分被拒时的返回与 `SMTPRecipientsRefused`）按标准库文档与源码写入替身。
- `tests/test_ops_alert.py`（新增；smtplib 的 SMTP 与 SMTP_SSL 以 `FakeSmtp` 代替，Redis 以 `FakeRedis` 代替，时钟可注入，发信线程不启动、以 `run_due` 同步推进；只有一条测试起真实守护线程，以事件同步）：未配置只记一次 warning、不入队不启动线程；各类不合格配置只记变量名；端口与加密方式默认值、端口边界、地址去空白；入队不连接不碰 Redis、只启动一个线程；模块不引入数据库；未登记类别与 `ops_alert_test` 被拒；`alert` 在配置出错与参数不合格时不抛异常；同一限频键入队前合并（含发送中与等待重试）；队列满即丢弃且等待重试的不占容量；发生时刻为入队时刻；starttls 先完成 STARTTLS 才登录发信、超时 10 秒、默认校验上下文；ssl 用 SMTP_SSL；STARTTLS 不支持或回非 220 时不登录不发信、不重试；邮件头不含收件人地址、To 为空收件人组、Message-ID 用发件人域名；正文只有四项与限频说明、不含配置值与地址；临时失败重试一次且等待期间其他告警照发、按到期先后；十种临时失败各尝试两次、七种永久失败（含 TLS 握手与证书校验失败）不重试；最终失败取最后一次结果；部分被拒 4xx 只重试被拒的、5xx 即最终失败、混合时 code 取配置顺序第一人、全部被拒 4xx 重试、5xx 最终失败、重试连不上时带 refused 不带 code，日志都不带地址；限频占用、失败不释放、窗口过后再发；键与窗口按类别；重试不再占用；Redis 未配置或出错时改用进程内存、恢复后窗口内仍先查内存；默认 Redis 客户端超时 2 秒；`send_test_alert` 同步、不限频、正文无限频说明，三种结果与临时失败的 60 秒重试；`wait_until_sent` 等到重试发完、到 180 秒记下未发封数、空闲时不等；真实发信线程卡住时调用方照常入队；`CATEGORIES` 每项的说明与指引不含链接与地址。
- `tests/test_jobs.py`：`send-test-alert` 的三种退出码、连发两次都发出且不碰 Redis；七个补跑命令都在执行后等一次队列且退出码不受未发封数影响；补跑命令到 180 秒记下 `ops_alert_unsent count=1`；`run` 与 `send-test-alert` 不等队列。
- `tests/test_compose_env.py`：两个服务的七个 `SHOP_ALERT_` 变量写法、Settings 七项为字符串且默认空串（不合格的端口与加密方式不让设置加载失败）、`shop_jobs` 的 `SHOP_REDIS_URL` 与 `shop_api` 相同。
- 每条新增测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第几条哪一句（或 HANDOFF 0.88 的落实写法）。后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

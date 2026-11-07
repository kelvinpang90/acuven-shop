# SHOP-TASK-218 定时任务失败的告警邮件（job_failing 与 stock_reset_failed）

- [x] 依据：`docs/DESIGN.md` 1.21（提交 `5be346e`）「运营告警邮件」第 6 条表中 `job_failing` 与 `stock_reset_failed` 两行和第 7 条；`docs/HANDOFF.md` 0.88 记录的落实写法。设计闸门：DESIGN 1.21（提交 `5be346e`）「运营告警邮件」第 6、7 条与 HANDOFF 0.88。依赖 SHOP-TASK-217 的发送机制（`ops_alert.alert`），发送机制本身未改。不改数据库、接口、前端与设计，不加依赖。心跳与容器不健康的规则不变。
- [x] `app/services/ops_alert.py`：`CATEGORIES` 加两类，都以对象作去重键：
  - `job_failing`：说明「定时任务连续失败满 10 分钟」，窗口 `HOUR_SECONDS`（3600 秒）；处理指引写按类别码与任务名查日志、按异常类名排查，修复后下一轮成功即恢复，也可执行该任务的补跑命令（`python -m app.jobs --help` 列出各子命令）核对。
  - `stock_reset_failed`：说明「当日库存重置失败」，窗口 `DAY_SECONDS`（86400 秒）；处理指引写按类别码与营业日期查日志、常驻循环下一轮会重试，修复后也可执行补跑命令 `python -m app.jobs reset-stock`，再到后台库存重置页核对。
  - 两段指引都不含链接、主机与地址。模块说明补一句本任务接入的两类。
- [x] `app/jobs.py`：
  - `Runner` 加 `last_error`（每个任务最后一次失败的异常类名）与本段连续失败已告警的任务名集合。任务失败时记下异常类名；数据库未配置时一轮里全部任务都算失败，异常类名记为新常量 `DATABASE_NOT_CONFIGURED = "database_not_configured"`。
  - 每轮执行完任务、写心跳之前，`_alert_failing` 对已连续失败满 `UNHEALTHY_AFTER_SECONDS` 的任务（与 `healthy` 同一算法：距最近一次成功，从未成功过则距进程启动；两者共用 `_failing_too_long`）各调用一次 `ops_alert.alert("job_failing", ("job", 任务名), error_class=最后一次失败的异常类名)`，按任务列表顺序。同一段连续失败只调用一次；任务再次成功时清除已告警标记，之后再连续失败满 10 分钟才再调用。不报「已恢复」。计时只在进程内存里。`healthy`、心跳与现有日志的行为不变。
  - `reset_stock_job` 加只限关键字的参数 `alert_on_failure`（默认 `False`）：为真时重置失败在现有 error 日志 `stock reset <日期> failed: <异常类名>` 之后、重新抛出之前调用一次 `ops_alert.alert("stock_reset_failed", ("date", 营业日期的 ISO 写法), error_class=异常类名)`。`default_jobs()`（常驻循环）传 `alert_on_failure=True`；`reset-stock` 子命令（`reset_stock`）显式传 `False`，补跑命令本身失败不告警（第 7 条）。开关关闭与已完成的跳过不调用。
  - 模块说明「失败、并发与重试」第 6 条、每一轮第 2 条与任务列表里库存重置的描述，以及 `Job`、`Runner`、`reset_stock_job`、`default_jobs`、`reset_stock` 的文档字符串随之更新。

## 偏离与取舍，请审阅

- `reset_stock_job` 的参数名取 `alert_on_failure`、默认 `False`：直接调用 `reset_stock_job()` 的地方（含其他测试文件）不告警，只有 `default_jobs()` 打开。
- 同一轮里 `job_failing` 在心跳停写的 error 日志 `heartbeat stopped: ...` 之前调用（`_alert_failing` 在 `_beat` 之前）；告警前已有该任务各轮的 `job <名> failed: <类名>` error 日志，满足第 2 条「先记 error 日志、再入队」。
- 库存重置失败时，常驻循环每次失败都调用一次 `stock_reset_failed`（每轮一次）；「同一营业日期只发一封」由 SHOP-TASK-217 的限频（键加日期、窗口 86400 秒）与入队前合并保证，本任务不另在内存里去重。库存重置连续失败满 10 分钟时另有一封 `job_failing`（对象 `job reset_daily_stock`），与第 7 条一致。
- 其他测试文件里的运行器用例（如 `tests/test_stock_reset.py`、`tests/test_stock_reset_switch.py`）在失败时会调用真实的 `ops_alert.alert`；它不抛异常、不阻塞，未配置告警邮件时只在 `app.services.ops_alert` 记一条 warning，那些用例的断言只看 `app.jobs` 的日志。本任务没有改这些文件（不在允许路径内）。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.21「运营告警邮件」第 6、7 条与 HANDOFF 0.88 自查。
- `tests/test_jobs.py` 新增（以 monkeypatch 替换 `ops_alert.alert` 记下调用）：连续失败未满 10 分钟不告警、满 10 分钟与心跳停写同一轮告警一次且异常类名取最后一次失败的、同一段失败不重复、一直成功的任务不告警；成功那一轮不告警、成功后清零、再失败从这次成功起算满 10 分钟再告警一次；数据库未配置时满 10 分钟那一轮每个任务名各一次、`error_class` 为 `database_not_configured`、之后不重复；库存重置失败在常驻循环里于 error 日志之后、任务失败日志之前告警一次，对象为营业日期、异常类名为 `OperationalError`，`reset_stock` 与 `main(["reset-stock"])` 失败时不告警、退出码为 `EXIT_FAILED`、日志不含异常消息原文；开关关闭与当天已完成而跳过时常驻循环与 `reset_stock` 都不告警、跳过算成功。每条测试的文档字符串写明它守住的 DESIGN 1.21「运营告警邮件」第 6、7 条（及第 2 条）哪一句。
- 后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

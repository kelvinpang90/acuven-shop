# SHOP-TASK-031 定时任务运行器与支付超时取消

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 6 条（15 分钟未支付自动取消并释放）、「订单与退款状态」第 3 条与「失败、并发与重试」第 6 条（可重复运行、只生效一次；失败有告警与人工补跑办法），增加常驻的定时任务进程，每分钟调用 SHOP-TASK-021 的 `expire_overdue_orders`：`app/jobs.py`（运行器与命令）、`docker-compose.yml`（新增 `shop_jobs` 服务）、`deploy/deploy.sh`（回滚时对 `shop_jobs` 的处理）。测试见 `tests/test_jobs.py`。未改 SHOP-TASK-021 已有函数，未加表、迁移或接口，未做页面，未改前端、`.platform/`、CI 工作流、Dockerfile 或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 进程与容器：`shop_jobs` 与 `shop_api` 同一镜像（`SHOP_API_IMAGE`），命令 `python -m app.jobs run`；只挂 `data_net`，不发布端口；日志设置与 `SHOP_DATABASE_URL` 与 `shop_api` 相同（不带 `SHOP_REDIS_URL`，本任务用不到）；内存上限 128m，`restart: unless-stopped`。健康检查用 python 判断心跳文件 `/tmp/acuven_shop_jobs_heartbeat`（`app/jobs.py` 的 `HEARTBEAT_PATH`）存在且修改时间在 180 秒内，间隔 15 秒、超时 5 秒、重试 3 次、`start_period` 30 秒。数据库连接沿用 `SHOP_DATABASE_URL` 与 `app/db/session.py` 的会话工厂（引擎在第一次开会话时才建）。
- 每轮步骤（`Runner.run_round`，循环在 `Runner.run_forever`）：
  1. 按任务列表（`default_jobs()`，本任务只有 `cancel_expired_orders`）逐个执行，每个任务一个新会话、一个独立事务，执行完提交。超时取消的当前时间取 UTC（与库里一样不带时区），每轮最多 `CANCEL_BATCH_LIMIT` = 100 张，按到期时间从早到晚，剩余的下一轮继续。`run_job` 以外层会话开启事务并占住连接，任务拿到的是以保存点加入该事务的会话（`join_transaction_mode="create_savepoint"`）：`expire_overdue_orders` 逐张的 commit 与未命中时的 rollback 在这里只释放或回滚各自的保存点，整次运行在任务返回后一次提交；任务中途抛异常时整个事务回滚，已“取消”的订单也一起回滚。`expire_overdue_orders` 本身未改，接口里直接调用它时仍逐张提交；同一订单只生效一次。
  2. 写心跳文件（内容是本轮的 UTC 时间）。运行器记下每个任务最近一次成功的时间（monotonic）；任一任务已连续失败、且距它最近一次成功（从未成功则距进程启动）满 10 分钟时不写，容器变为不健康；该任务再次成功后恢复写。判定无需执行（没有到期订单）也算成功。
  3. 睡到距本轮开始满 60 秒。睡眠是停止事件的等待：收到 SIGTERM 或 SIGINT 即醒来，不再开始新一轮并退出（退出码 0）。信号到来时若一轮正在执行，信号处理同时起一个 `SHUTDOWN_GRACE_SECONDS` = 8 秒的看门狗（守护线程）：这一轮在 8 秒内做完则正常退出、看门狗取消；到时仍未做完（例如数据库调用卡住）即记一条 `round did not finish within 8 seconds of stop; exiting` 并 `os._exit(1)`，未提交的事务随连接断开由数据库回滚，因为整次任务运行是一个事务，不会留下部分取消。所以收到信号后 10 秒内一定退出，不依赖单轮上限或数据库响应。
  - 当前时间、计时、睡眠与看门狗都可注入（`Runner` 的 `now`、`monotonic`、`sleep`、`force_exit`、`timer`）。之后的每日库存重置等任务写成 `Job` 加入 `default_jobs()`；`app/jobs.py` 的 docstring 写明每个任务必须可重复运行且只生效一次、判定无需执行而跳过也算成功。
- 失败处理：任务抛异常（含数据库不可用、会话工厂建引擎失败）时回滚，记一条 `job <任务名> failed: <异常类名>`，不退出，不影响同一轮的其他任务与下一轮。日志只有任务名、生效条数（超时取消即取消张数，有取消时才记）与异常类名，以及未配置数据库、心跳停写与恢复、心跳文件读写失败（只有异常类名）的提示；不记录订单号、个人资料、连接串或异常消息原文，不打印堆栈。运营告警邮件接入之前，以容器不健康代替告警（设计「失败有告警」）；接入后由运营告警邮件任务把任务失败接到告警。
- 手动补跑：在服务器上本仓库检出的根目录里 `docker compose exec shop_jobs python -m app.jobs cancel-expired`（`shop_jobs` 不在时可用 `docker compose run --rm --no-deps shop_jobs python -m app.jobs cancel-expired`）。执行一次超时取消（同一上限 100 张）后退出：成功 0；任务失败 1；未配置 `SHOP_DATABASE_URL` 2。日志里的取消张数等于 100 时可能还有剩余，可再执行一次；重复执行不重复生效。
- 部署与回滚（`deploy/deploy.sh`）：部署前与 `shop_api`、`shop_web` 一样记下 `shop_jobs` 当前的镜像（用 `docker compose ps -a`，已停止的 `shop_jobs` 容器也算存在；另记一行 `existing jobs container: …`）。`wait_for_health` 可传入服务名，只计这些服务；不传时仍为 compose 文件里的全部服务（现在是三个）。部署成功的流程不变，三个服务都要健康。回滚的两种情形：
  - 部署前没有 `shop_jobs` 容器（这次首次上线它）：记一行 `shop_jobs did not exist before this deploy; stopping and removing it`，`docker compose rm --stop --force shop_jobs`，再以旧镜像 `docker compose up -d --no-build shop_api shop_web`，等待健康只计这两个服务；健康则照旧以非零退出并记 `rolled back to …`，不健康以非零退出并记需要人工处理。停止或重启失败也以非零退出并记 `rollback failed`。
  - 部署前已有 `shop_jobs` 容器（运行中或已停止）：照常 `docker compose up -d --no-build` 一起回滚（`shop_jobs` 随 `SHOP_API_IMAGE` 换回旧镜像），三个服务都要健康；结果与退出码同上。
  - 部署前没有 `shop_api` 或 `shop_web`：仍按原样不回滚、保留现场、非零退出。其余流程、退出码与原有日志不变。
- 留给之后的任务：每日库存重置（SHOP-TASK-032）、发货满 7 天自动确认收货、积分到期，都按同样方式加入任务列表；运营告警邮件接入任务失败告警；券与积分预占的释放由优惠券与积分任务扩展 SHOP-TASK-021 的超时取消路径。
- 偏离与取舍，请审阅（未改设计，未发现设计本身必须停下的问题）：
  - 设计「失败有告警」暂以容器不健康代替，直到运营告警邮件任务接入（验收约定）。
  - 未配置数据库时 `run` 每轮记一条警告并继续，所有任务按失败计时：满 10 分钟后不再写心跳、容器不健康。验收只写了「记警告并继续」，未写它是否算失败；按失败计是因为这时没有任何任务真正执行。生产栈里 compose 要求必须设置 `SHOP_DATABASE_URL`，这种情形只在本地出现。
  - `run` 启动时先删除旧的心跳文件：`restart: unless-stopped` 重启时容器文件系统保留，上一个进程的心跳不代表这个进程。
  - 没有取消时不记日志，以免每分钟一条；有取消时记 `job cancel_expired_orders done: <张数> changed`。
  - 收到停止信号时正在执行的一轮先给 8 秒做完，到时强制退出（见每轮步骤 3），比 Docker 默认 10 秒的停止宽限期早，退出码为 1；正常停止退出码 0。
  - 任务里的提交改为保存点、整次运行一个事务：超时取消一次最多持有 100 张订单及其规格行的行锁直到本次运行提交，期间并发的支付或取消对这些订单会等待，提交后它们的带条件 UPDATE 未命中、按 SHOP-TASK-021 的已有处理返回。
  - 回滚首次上线的情形先移除 `shop_jobs` 再重启另两个服务，避免新版本的任务进程在旧版本 API 恢复期间继续运行。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，人工通读 `deploy/deploy.sh` 两种回滚分支的执行路径。`tests/test_jobs.py` 用 SQLite 内存库（每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档的做法由引擎发 BEGIN，使保存点与 MySQL 上一样只在外层事务提交时生效）按模型建表、直接写库建立商品规格与待支付订单，运行器的时间、计时与睡眠换成测试里的假时钟（不真实等待），心跳写在临时目录，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：一轮执行后已到期（含恰在到期时刻）的待支付订单变为已取消、库存加回、事件操作者 system，未到期的不受影响，并写了心跳；同一时刻连续两轮只生效一次；单轮上限（注入上限 2，以及默认上限 100 时 101 张只取消 100 张）与剩余的下一轮继续；默认时间为不带时区的 UTC；任务抛异常时该任务的改动回滚、同一轮的超时取消照常生效、下一轮照常执行，日志只有任务名与异常类名；任务里 `expire_overdue_orders` 已逐张“提交”两张后抛异常时两张都回滚（状态、库存、事件不变），下一轮照常取消且只生效一次；会话工厂抛数据库异常时每轮照常继续、日志不含连接串；未配置数据库时每轮一条警告、满 10 分钟后不再写心跳；从进程启动起连续失败不满 10 分钟时心跳照写、满 10 分钟后不写、再次成功后恢复；从最近一次成功起计时（另一个一直成功的任务不抵消）；跳过也算成功；循环先执行任务、再写心跳、再睡到距本轮开始满 60 秒；一轮中途请求停止后不再睡眠、不再开始新一轮；以真实的 SIGTERM 与 SIGINT（`signal.raise_signal`）触发停止；请求停止时起一个短于 10 秒的看门狗，一轮卡住时到时即强制退出、按时做完时看门狗取消且不强制退出、重复信号不另起；强制退出以 1 调用 `os._exit`（测试里替换）且只记一条固定日志；默认定时器是守护线程；启动时删除旧心跳；`cancel-expired` 成功 0（日志只有任务名与张数、不含订单号，重复执行不重复生效）、失败 1、未配置 2、经 `main` 走 SHOP_DATABASE_URL 与会话工厂（未建表的内存库失败 1、日志不含连接串）；缺少或未知的子命令非零退出。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。未在真 MySQL 上运行、未构建镜像或起容器：健康检查命令、心跳文件在容器里的写入权限（以 UID 10001 写 `/tmp`）、Docker 发送 SIGTERM 后的实际退出时间、`docker compose ps`、`rm --stop --force` 与只起部分服务的 `up` 在服务器上的实际行为，都未实际执行；`deploy/deploy.sh` 由 CI 的 shellcheck 检查，两种回滚分支未实际演练。检查命令结果由 Worker 另行记录。
- 后续（SHOP-TASK-097）：超时取消任务改为 `own_transactions`，`expire_overdue_orders` 的逐张提交即真实提交，不再整次运行一个事务；某张订单取消失败时之前已取消的订单保留。上文「任务里的提交改为保存点、整次运行一个事务」与收到停止信号强制退出时「不会留下部分取消」对超时取消任务不再成立（每张订单仍是完整的一个事务），见 SHOP-TASK-097 记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11 上述各条一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/jobs.py 可用 python -m app.jobs 运行，提供两个子命令：run 常驻循环，cancel-expired 执行一次超时取消后退出（运营者手动补跑用）；数据库连接沿用 SHOP_DATABASE_URL 与 app/db/session.py 的会话工厂，未配置时 run 每轮记一条不含连接信息的警告并继续，cancel-expired 以非零退出
      - run 每轮先调用超时取消函数（当前时间取 UTC，每轮最多处理一个固定上限的订单数，剩余的下一轮继续）并在独立事务里提交，再写心跳文件（容器内固定的临时目录路径），然后睡到距本轮开始满 60 秒；任一轮任务失败（含数据库不可用）只回滚、记一条只含任务名与异常类名的日志，不退出、不影响下一轮；运行器记下每个任务最近一次成功的时间，任一任务已连续失败且距它最近一次成功（或进程启动）满 10 分钟时不再写心跳，使容器变为不健康，该任务再次成功后恢复写心跳；收到 SIGTERM 或 SIGINT 时不再开始新一轮，在 10 秒内退出
      - 运行器以任务列表的形式组织每轮要执行的任务（本任务只有超时取消），docstring 写明之后的每日库存重置等任务按同样方式加入、每个任务必须可重复运行且只生效一次、判定无需执行而跳过也算成功
      - docker-compose.yml 新增 shop_jobs 服务：镜像与 shop_api 相同（SHOP_API_IMAGE），命令为运行 run 子命令，只挂 data_net，不发布端口，带与 shop_api 相同的日志设置与 SHOP_DATABASE_URL，内存上限 128m，restart unless-stopped；健康检查用 python 判断心跳文件存在且修改时间在 180 秒内，start_period 不短于 30 秒；服务名带 shop_ 前缀并写一行注释说明用途
      - deploy/deploy.sh 在部署前与 shop_api、shop_web 一样记下 shop_jobs 当前的镜像；回滚时若部署前没有 shop_jobs 容器（首次上线它），只以旧镜像重启 shop_api 与 shop_web、停止并移除 shop_jobs，等待健康时只计这两个服务；部署前已有 shop_jobs 时照常一起回滚、三个服务都要健康；其余流程、退出码与日志不变；脚本照常通过 CI 的 shellcheck，PR 正文写明两种回滚情形的预期行为
      - 日志只记录任务名、取消张数与异常类名，不记录订单号、个人资料、连接串或异常消息原文
      - tests/test_jobs.py 用 SQLite 内存库（开启外键检查）按模型建表、自建订单，验证：一轮执行后已到期的待支付订单变为已取消、库存加回、未到期的不受影响；同一时刻连续执行两轮只生效一次；任务抛异常时本轮回滚、下一轮照常执行，连续失败不满 10 分钟时心跳照写、满 10 分钟后不再写、再次成功后恢复；收到停止信号后不再开始新一轮；cancel-expired 子命令的退出码；单轮上限生效；每条测试写明它守住的是设计里哪一句；循环的时间与睡眠以可注入的函数代替，测试不真实等待；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-031 记录段（进程与容器、每轮步骤、失败处理、手动补跑命令、偏离、验证到什么程度，并写明回滚时对 shop_jobs 的处理、以容器不健康代替告警直到运营告警邮件任务接入；库存重置、自动确认收货与积分到期留给之后的任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI 工作流与 Dockerfile；不装依赖；不改 SHOP-TASK-021 已有函数的行为；不加表、迁移或接口；不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

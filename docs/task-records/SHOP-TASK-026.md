# SHOP-TASK-026 Redis 连接与限流基础

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「失败、并发与重试」第 4 条（短时限流计数存共享 `infra_redis` 为本项目分配的独立库编号、多 API 进程共用、Redis 数据丢失后计数清零可接受、Redis 不可用时依赖限流的敏感接口拒绝请求），接入 Redis 并提供固定窗口计数与访客来源函数：`pyproject.toml` 运行时依赖加 `redis>=5.0`（官方 Python 客户端，未加其他依赖或测试依赖）、`app/core/config.py`、`docker-compose.yml`、`app/services/rate_limit.py`；测试见 `tests/test_rate_limit.py`。未接入任何接口、未定具体阈值、未改 `app/main.py`、未加表或迁移、未改前端、`.env.example`、CI、Dockerfile、部署脚本或设计。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 配置项：`Settings.redis_url`（环境变量 `SHOP_REDIS_URL`，默认空串表示未配置；连接串末尾的数字是库编号）。`docker-compose.yml` 的 `shop_api` 以 `${SHOP_REDIS_URL:-}` 传入，未设置时为空串、照常部署，依赖限流的接口回答暂不可用。
- 客户端与超时：`get_redis_client(request)` 是 FastAPI 依赖，读 `request.app.state.settings.redis_url`（与 `get_session` 同样取应用的 Settings），按连接串以 `lru_cache` 惰性创建、进程内复用一个 `redis.Redis`（建客户端不连 Redis，第一次执行命令时才连接）。连接超时与读写超时都是 0.5 秒；连接池与客户端都显式传 `Retry(NoBackoff(), 0)`，不自动重试（redis-py 6 起默认会重试）。测试以 `app.dependency_overrides[get_redis_client]` 换成替身。
- 不可用：连接串为空、格式不对、连接失败、超时或 Redis 返回错误（`RedisError` 的任何子类），以及计数值不是整数时，一律抛模块内的 `RateLimitUnavailable`，消息固定为 `rate limit store unavailable`，以 `from None` 抛出、不带原异常链，不含连接串、主机、计数键、来源或标识。调用方必须拒绝请求，不得当作未超限放行。
- 计数与键规则：
  - `hit(client, bucket, identifier, limit, window_seconds) -> bool`：在一个 `MULTI` 事务管道里 `INCR` 键并 `EXPIRE 键 窗口秒数 NX`（只在键尚无过期时间时设置，共享 Redis 是 7 版），返回自增后的计数是否 `<= limit`。窗口从第一次计数算起，之后的计数不延长窗口；超限后的请求照样计数。
  - `current_count(client, bucket, identifier) -> int`：`GET` 当前计数，不自增；键不存在为 0。
  - 键为 `acuven_shop:rate_limit:<桶名>:<标识的 SHA-256 十六进制摘要>`（标识按 UTF-8 编码），标识原文（来源地址、订单号等）不进 Redis。桶名须为小写字母开头的小写字母、数字与下划线；上限与窗口秒数须为正整数（不接受布尔值）；不合格时抛 `ValueError`（编程错误，不写 Redis，消息不含标识）。
  - 具体阈值由调用的任务按 Kelvin 的决定传入（docstring 已写明），本模块不定任何阈值。
- 来源规则 `client_source(request) -> str`：`X-Real-IP` 恰好出现一次且（去掉首尾空白后）是合法的 IPv4 或 IPv6 地址时，返回规范化后的地址文本（如 IPv6 小写、压缩写法，同一地址的不同写法计为同一来源）；缺失、出现多次、一个值里用逗号写了多个地址或不合法时，退回连接的对端地址；再没有时返回占位值 `unknown`。不读 `X-Forwarded-For`（访客自己能在前面追加伪造值），docstring 写明了理由与上线前的运营者确认。
- 模块不写日志。
- 上线前运营者须完成（本任务未做，代码里不含任何主机或库编号）：在共享 Redis 上为本项目分配独立库编号；在服务器上本项目目录的 `.env` 配置 `SHOP_REDIS_URL`（模板见 `.env.example`）；确认边缘反向代理为本站设置 `X-Real-IP` 为访客的对端地址（覆盖，而不是透传访客送来的值）。
- 偏离：未改设计，未发现设计本身的问题。说明几处取舍，请审阅：
  - 验收说代理不设 `X-Real-IP` 时「所有访客共享一个来源计数，只会更严格、不会放开」。这只在代理既不设置也不转发该请求头时成立；若代理把访客自己送来的 `X-Real-IP` 原样转进来，访客就能每次换一个地址绕过按来源的限流。所以 docstring 与上面的上线事项都写成须确认代理「设置（覆盖）」该请求头，不只是「设置」。
  - 取客户端的依赖在连接串为空时抛的是 `RateLimitUnavailable` 而不是 `HTTPException`（按验收）。FastAPI 对未处理的异常回答 500；第一个接入限流的任务须把它（依赖里与 `hit`、`current_count` 里抛的）转成暂不可用的回答（例如 503），本任务不改 `app/main.py`、不加异常处理器。
  - 标识只做不加盐的 SHA-256（按验收）。IPv4 地址只有 2^32 个，能被穷举反推；计数存在本项目独立库、短时过期，且计数键不进日志与回答，这里不另加盐。如需更强保护须另行决定。
  - 验收标准之外另加的：连接串格式不对（如不是 `redis://`）也当不可用；读到的计数不是整数时也当不可用（fail closed）；上限与窗口秒数的正整数校验；来源地址规范化；访客来源在对端地址也缺失时用 `unknown`，这些请求共用一个计数。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_rate_limit.py` 用本文件自写的内存替身 `FakeRedis`（实现 `GET` 与事务管道的 `INCR`、`EXPIRE`（含 `NX` 语义）、`execute`，带可推进的替身时钟与可注入的故障，不连真 Redis、不加依赖），每条测试的文档字符串写明它守住的设计原句或验收条目，覆盖：上限内放行、超过上限拒绝且之后继续拒绝；自增与过期在同一个 `transaction=True` 的管道里一次执行、过期带 `NX`；过期只在首次自增时设置、之后以不同窗口秒数自增也不延长；窗口过后计数从零开始；清空替身数据后从零开始；只读函数不自增、键不存在为 0；键为前缀、桶名与另算的摘要，不含订单号或来源原文；不同桶、不同标识互不影响；不合格的桶名、上限与窗口秒数报错且不写 Redis；连接串为空、格式不对时抛不可用异常；配置连接串时客户端在进程内复用、两个超时不超过 0.5 秒、重试次数为 0、库编号取自连接串；替身在自增或事务执行时抛 redis 的连接错误、超时错误或 Redis 报错，以及只读时抛连接错误或超时错误，都抛不可用异常，消息不含连接串、主机、密码、计数键或订单号，且不带异常链；依赖可被 `dependency_overrides` 覆盖；模块不产生 `app.` 下的日志记录；`X-Real-IP` 合法（IPv4、IPv6 规范化）、非法（含空值、带端口）、缺失（且不读 `X-Forwarded-For`）与含多个值（两个请求头、逗号分隔）时的来源结果，以及对端地址缺失时的占位值。客户端参数的测试读取 redis-py 的 `Retry._retries` 私有属性。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未连真 Redis 执行过 `MULTI`/`EXPIRE NX`，超时与不重试的实际效果未在真实网络上验证。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「失败、并发与重试」第 4 条（短时限流计数存共享 Redis 为本项目分配的独立库编号、多进程共用、数据丢失后清零可接受、Redis 不可用时依赖限流的敏感接口拒绝请求）一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - pyproject.toml 的运行时依赖加 redis（官方 Python 客户端，下界 5.0），并写一行注释说明用途；不加其他依赖，不加测试依赖
      - app/core/config.py 新增 redis_url 配置项（环境变量 SHOP_REDIS_URL，默认空串表示未配置；变量名与示例值已由登记 PR 写进 .env.example，本任务不改该文件）；docker-compose.yml 的 API 服务把它传进容器，未设置时为空串而不是报错（未配置时照常部署，依赖限流的接口返回暂不可用）
      - app/services/rate_limit.py 提供：按配置惰性创建、进程内复用的 Redis 客户端（连接与读写超时各不超过 0.5 秒，不自动重试）；连接串为空、连接失败、超时或 Redis 返回错误时一律抛同一个模块内定义的不可用异常，消息不含连接串、主机或计数键；一个供 FastAPI 依赖注入的取客户端函数，测试可以覆盖它
      - 同一文件提供固定窗口计数：计一次函数接收客户端、桶名（小写下划线）、标识、上限与窗口秒数，在一个 MULTI 事务里对键自增并只在键尚无过期时间时设置过期（EXPIRE 的 NX 选项，共享 Redis 是 7 版），返回自增后是否仍不超过上限；只读函数返回当前计数且不自增（键不存在为 0）；键为固定前缀加桶名加标识的 SHA-256 十六进制摘要，标识原文（来源地址、订单号等）不进 Redis；docstring 写明具体阈值由调用的任务按 Kelvin 的决定传入
      - 同一文件提供取访客来源的函数：优先取反向代理设置的 X-Real-IP 请求头，值是合法的 IPv4 或 IPv6 地址时使用，否则（缺失、多个值或非法）退回连接的对端地址，再没有时用固定的占位值；docstring 写明为什么不用 X-Forwarded-For（访客自己能在前面追加伪造值），以及上线前须由运营者确认反向代理为本站设置 X-Real-IP，否则所有访客共享一个来源计数，只会更严格、不会放开
      - 模块不写日志；来源地址、标识原文与连接串都不出现在异常消息或返回值里
      - tests/test_rate_limit.py 用测试文件内自写的内存替身客户端（实现所用的命令与事务接口，不连真 Redis、不加依赖）验证：未超过上限时放行、超过时拒绝；只在首次自增时设置过期，之后的自增不延长窗口；只读函数不自增；键里不含标识原文且不同桶、不同标识互不影响；连接串为空时抛不可用异常；替身在自增或事务执行时抛 redis 的连接错误或超时错误时都抛不可用异常，消息不含连接串；X-Real-IP 合法、非法、缺失、含多个值四种情况的来源结果；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-026 记录段（配置项、客户端与超时、计数与键规则、来源规则、偏离、验证到什么程度，并写明上线前运营者须分配 Redis 库编号、在服务器配置连接串并确认反向代理设置 X-Real-IP）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI 工作流、Dockerfile 与部署脚本；不加接口、不改 app/main.py、不加表或迁移、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

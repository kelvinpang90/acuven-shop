# SHOP-TASK-043 公开接口限流（计价、下单）

- [x] 按 `docs/HANDOFF.md` 0.34 记录的 Kelvin 2026-10-06 决定，给计价接口 `POST /api/checkout/quote` 与游客下单接口 `POST /api/orders/guest` 加按访客来源的固定窗口限流，超过返回 429；Redis 未配置或不可用时这两个接口放行。用 SHOP-TASK-026 的 `client_source`（访客来源）与 `hit`（计数）。改 `app/services/rate_limit.py`（只新增可选取客户端依赖 `get_optional_redis_client`，已有函数不变）、`app/api/checkout.py`、`app/api/orders.py`；测试见新文件 `tests/test_public_rate_limits.py`。未改其他接口、查单与管理员登录的限流、已有测试、前端、`.platform/`、CI、部署配置或设计，未加表或迁移，未装依赖。设计闸门：不适用（只在已有接口前计数与拒绝，不改计价、下单、库存与个人资料的规则）。拆分：不拆。
- 桶名与阈值（固定窗口，从第一次计数算起，超限后的请求照样计数）：
  - 计价：`checkout_quote_source`，每个来源 10 分钟 300 次，成功失败都计。
  - 下单：`guest_order_source`，每个来源 1 小时 60 次，幂等重放与之后校验不通过的请求也计。
- 计数位置（都在各自的请求体依赖里）：
  - 计价：逐块读完请求体、413 检查之后，JSON 解析与字段校验之前。该接口原本没有 415，不是 JSON 的请求体仍在计数之后返回现有的 422，未新增 415。
  - 下单：413 与 415 检查之后，JSON 解析、幂等键与其他校验之前。413、415 不计数；JSON 不合法、字段或幂等键不合法的请求计数之后返回与原来相同的 422。
  - 超过即 429 `{"detail": "rate_limited"}`，不回显请求内容；响应头与该接口现有的错误响应相同（下单接口现有错误不带 `no-store`，429 也不另加）。下单的 429 在处理函数之前回答，不读写订单、库存与授权，不设 cookie。
- Redis 不可用时放行（Kelvin 2026-10-06 的决定，与查单的拒绝不同：演示站没有真实交易，不能让结账因 Redis 停摆；写在 `enforce_source_limit` 与 `get_optional_redis_client` 的 docstring 里）：
  - `get_optional_redis_client` 内部调用原来的 `get_redis_client`，遇 `RateLimitUnavailable`（连接串未配置或客户端创建失败）返回 `None`，此时直接放行、不记日志。
  - 计数时 `hit` 抛 `RateLimitUnavailable`（连不上、超时或 Redis 出错）也放行，并以 logger `app.api.checkout` 记一条警告 `rate limit store unavailable; <桶名> not limited`：只含桶名与固定说明，不含来源地址、请求内容、连接串或异常消息。
  - 查单与管理员登录仍用 `get_redis_client`，Redis 不可用时照旧拒绝。
- 前台对 429 的提示沿用各页已有的错误处理，未改前端代码。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 两个接口共用的计数与放行函数 `enforce_source_limit` 与依赖别名 `OptionalRedisDep` 放在 `app/api/checkout.py`，下单接口从那里导入（下单接口已从它导入请求体上限与行模型）：`app/services/rate_limit.py` 按验收只许新增一个依赖，且该模块的已有测试断言它不写日志。因此下单接口的警告也记在 logger `app.api.checkout` 下，靠桶名区分。
  - 连接串格式不对（客户端创建失败）与未配置一样返回 `None`、静默放行不记日志；只有计数时的错误记警告。连接串写错因此不会在日志里出现，要靠部署时核对配置。
  - `hit` 是同步的 Redis 调用，在异步的请求体依赖里经 `run_in_threadpool` 执行，不阻塞事件循环；超时仍是 SHOP-TASK-026 的 0.5 秒。
  - 下单接口的 413、415 不计数，所以只发非 JSON 或超大请求体的来源不受这条限流约束（这两种请求不会读写订单）。
  - 按来源的计数依赖边缘反向代理设置 X-Real-IP（见 SHOP-TASK-026）；代理不设置时所有访客共用一个来源计数，计价 300 次、下单 60 次会被全站共用，上线前须由运营者确认。
  - 429 不写日志。
- 验证到什么程度：人工逐条对照验收标准自查。`tests/test_public_rate_limits.py` 用 TestClient 与 SQLite 内存库（会话依赖换成每个请求一个会话），新增的可选取客户端依赖换成测试文件内自写的内存替身 Redis（事务管道的 INCR 与 EXPIRE NX，带可拨动的时钟，可注入 `redis.exceptions` 的 `ConnectionError` 与 `TimeoutError`；不连真 Redis、未加依赖），访客来源用 X-Real-IP 区分，每条测试的文档字符串写明它守住 Kelvin 2026-10-06 决定的哪一项。覆盖：计价第 300 次放行、第 301 次 429（响应体、不回显 SKU）、另一来源不受影响、窗口差一秒仍 429 而满 10 分钟后恢复；计价 429 与 413 响应头相同且无 `no-store`；计价 413 不计数且已超限时仍是 413；计价 JSON 不完整、字段不合法与非 JSON 请求体计数一次后返回与未配置 Redis 时相同的 422，超限后同样的请求是 429；下单首单 201 加 59 次幂等重放后第 61 次（新键）429，不创建订单、授权或订单访问会话、不扣库存、不设 cookie、不回显姓名、地址、电话与幂等键，超限后同键重放也是 429；下单另一来源不受影响；下单 429 与 415 响应头相同且无 `no-store`；下单 413、两种 415 不计数且已超限时仍是 413 / 415；下单 JSON 不完整、字段不合法、幂等键不合法与缺失计数一次后返回与未配置 Redis 时相同的 422 且不创建订单，超限后是 429；替身抛连接错误或超时时两个接口照常处理（计价 200、下单 201），各记一条 WARNING，只含桶名，不含来源地址、连接串、异常消息与请求内容；可选依赖返回空时超过两个阈值也照常处理、不计数、不记日志；不覆盖任何依赖、连接串为空时照常处理且不记日志；连接串为空或格式不对时原来的依赖仍抛 `RateLimitUnavailable`、可选依赖返回空。已有测试未改：它们不覆盖取客户端依赖、连接串为空，即放行。这些测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未连真 Redis 验证计数与过期，未经真实反向代理验证来源。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 按 docs/HANDOFF.md 0.34 记录的 Kelvin 2026-10-06 决定实现；用 SHOP-TASK-026 的取访客来源函数与计数函数；SHOP-TASK-026 的取客户端依赖在未配置或创建失败时抛 RateLimitUnavailable、接口内接不住，所以 app/services/rate_limit.py 只许新增一个供这两个放行接口用的可选取客户端依赖（内部调用原来的取客户端依赖，遇 RateLimitUnavailable 返回空，测试可用 dependency_overrides 覆盖它），已有函数的行为与测试不变
      - POST /api/checkout/quote 在请求体大小检查（413）之后、JSON 解析与字段校验之前按来源计一次（该接口现有没有 415，非 JSON 仍返回现有的 422，不新增 415）（桶名 checkout_quote_source，窗口 10 分钟，上限 300 次，成功失败都计），超过返回 429（错误码 rate_limited）；POST /api/orders/guest 同样在 413 与 415 检查之后、JSON 解析、幂等键与其他校验之前计一次（桶名 guest_order_source，窗口 1 小时，上限 60 次，幂等重放也计），超过返回 429（错误码 rate_limited），不读写订单、库存与授权；JSON 不合法、字段或幂等键不合法时仍在计数之后返回与现在相同的 422
      - 429 响应体为 {"detail":"rate_limited"}，响应头与该接口现有的错误响应相同（下单接口现有错误不带 no-store，429 也不另加），不回显请求内容
      - Redis 连接串未配置、连不上、超时或返回错误时这两个接口不限流、照常处理请求：可选依赖返回空（连接串未配置）时直接放行且不记日志（tests/test_guest_order.py 现有断言 Redis 未配置时没有应用日志），计数函数抛 RateLimitUnavailable 时也放行，并记一条只含桶名与固定说明的警告日志（不含来源地址、请求内容、连接串或异常消息）；docstring 写明这是 Kelvin 2026-10-06 的决定，与查单的拒绝不同，原因是演示站没有真实交易、不能让结账因 Redis 停摆
      - 现有接口的其他行为、状态码与字段不变；tests/test_checkout_quote.py、tests/test_guest_order.py 等已有测试不改并照常通过（它们不覆盖取客户端依赖时 Redis 未配置，即放行）
      - tests/test_public_rate_limits.py 用 TestClient 与测试文件内自写的内存替身 Redis 客户端（覆盖新增的可选取客户端依赖，不连真 Redis、不加依赖）验证：计价第 300 次放行、第 301 次 429、另一来源不受影响、窗口过后恢复；下单第 60 次放行、第 61 次 429 且没有创建订单或授权、幂等重放也计入；两个接口的 413 与下单接口的 415 先于计数且不计数，JSON 不合法（计价接口含非 JSON）的请求计数后返回与现在相同的 422；替身抛 redis 库的 ConnectionError 或 TimeoutError（redis.exceptions 里的，不是内置异常）时两个接口都照常处理并记一条不含来源地址的警告；可选依赖返回空（连接串未配置）时照常处理且不记计数；429 不回显请求内容；每条测试写明它守住的是 Kelvin 的哪一项决定；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-043 记录段（桶名、阈值、计数位置、Redis 不可用时放行、偏离、验证到什么程度，并写明前台对 429 的提示沿用各页已有的错误处理、未改前端）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

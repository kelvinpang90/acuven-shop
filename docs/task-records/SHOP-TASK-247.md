# SHOP-TASK-247 调用 Billing Hub 的 AI 消息接口（配置与客户端）

- [x] 依据：`docs/DESIGN.md` 1.23（提交 `b7f9355`）「节日促销活动」第 13 条的「链路」与「限制」两段。设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 13 条（出站调用只带管理员的描述与活动的规则参数等，由 SHOP-TASK-248 组装；客户端本身不读订单、会员与收货资料；密钥只在私有配置）。本任务只做配置与客户端；提示词、每日计数与回答的校验在 SHOP-TASK-248。不改数据库、接口、前端、CI、`.platform/` 与设计，不加依赖（只用标准库 `urllib`、`json`）。
- [x] 配置（`app/core/config.py`）：`Settings` 加五个字段——`ai_hub_base_url`、`ai_hub_client_id`、`ai_hub_access_secret`、`ai_model`（`str`，默认空串，空串即未配置）与 `ai_daily_call_limit`（`int`，默认 50），对应变量 `SHOP_AI_HUB_BASE_URL`、`SHOP_AI_HUB_CLIENT_ID`、`SHOP_AI_HUB_ACCESS_SECRET`、`SHOP_AI_MODEL`、`SHOP_AI_DAILY_CALL_LIMIT`（变量名照 `.env.example` 里运营者已加的，本任务未改该文件）。`ai_daily_call_limit` 本任务只登记，计数在 SHOP-TASK-248。
- [x] 新增 `app/services/ai_hub.py`（写法照 `app/services/sms_provider.py`）：
  - 传输：`HttpRequest(url, body, headers)`（三个字段都不进 repr）、`HttpResponse(status, body)`（响应体不进 repr）、`Transport = Callable[[HttpRequest], HttpResponse]`。默认的 `urllib_transport` 只接受 `https://` 地址，发一次 POST，`Content-Type: application/json`、`Accept: application/json`，`timeout=TIMEOUT_SECONDS`（60，连接与每次读取各自的套接字超时），不跟随重定向（3xx 原样返回），响应最多读 `MAX_RESPONSE_BYTES`（64 KB），非 2xx 也作为响应返回。套接字超时（`TimeoutError`，含被 `URLError` 包着的）抛 `TransportTimeout`，其余网络错误与非 https 地址抛 `TransportError`（`TransportTimeout` 是它的子类）；两者消息固定，`from None` 不带原异常链。
  - 客户端：`AiHubClient(base_url, client_id, access_secret, model, transport=urllib_transport)`，`AiHubClient.from_settings(settings, transport=urllib_transport)`。`configured`：四个字符串都不为空且地址（去掉末尾斜杠后）以 `https://` 开头；`repr` 只显示 `AiHubClient(configured=…)`。
  - 调用：`create_message(system, user_text, *, max_tokens, output_schema=None) -> AiReply`。向 `base_url` 去掉末尾斜杠后接 `/v1/messages` 发一次 POST，请求头 `X-Acuven-Client-Id: <客户端 ID>` 与 `Authorization: Bearer <项目密钥>`；请求体只有 `feature`（固定 `shop.promotion_proposal`，模块常量 `FEATURE`）、`model`、`system`、`messages`（一条 `{"role": "user", "content": user_text}`）、`max_tokens`，`output_schema` 不为 `None` 时另加 `output_config: {"format": {"type": "json_schema", "schema": <schema>}}`。
  - 结果与异常：成功（200 且 JSON 对象里有字符串 `text` 与整数的 `usage.input_tokens`、`usage.output_tokens`，布尔不算整数）返回 `AiReply(text, input_tokens, output_tokens)`（`text` 不进 repr）。异常基类 `AiHubError(RuntimeError)`，三个子类：`AiNotConfigured`（未配置，不调用传输函数）、`AiTimeout`（`TransportTimeout` 或 `TimeoutError`）、`AiUnavailable`（其余传输失败与 `OSError`、非 200、响应不是所述的 JSON）。异常消息固定，不带地址、密钥、请求或响应内容；不重试；本模块不写日志。
- [x] `docker-compose.yml`：只为本任务给 `shop_api` 加五个变量，写法照现有 `SHOP_` 变量：前四项 `${变量名:-}`，`SHOP_AI_DAILY_CALL_LIMIT: ${SHOP_AI_DAILY_CALL_LIMIT:-50}`（默认值与 `Settings` 相同）；不写真实地址与密钥。`shop_jobs` 不加（定时任务不调用模型）。
- [x] 测试：新增 `tests/test_ai_hub.py`；`tests/test_compose_env.py` 加 `AI_EXPECTED` 与两条核对，现有的 `test_every_setting_is_passed_to_api` 因 compose 加了五个变量而照常覆盖新字段。

## 供 SHOP-TASK-248 使用

- 引入：`from app.services.ai_hub import AiHubClient, AiNotConfigured, AiReply, AiTimeout, AiUnavailable`；按应用的设置构造 `AiHubClient.from_settings(settings)`，后台按钮是否可用看 `client.configured`。
- 调用：`client.create_message(system, user_text, max_tokens=…, output_schema=…)`；三种异常都是 `AiHubError` 的子类。调用方记结果类别与 `AiReply` 的两个 token 数，不记 `text` 原文。
- 测试替换：构造时传入替身 `transport`（写法见 `tests/test_ai_hub.py` 的 `RecordingTransport`），不发网络请求。

## 上线前 Kelvin 要在服务器私有配置里设置的变量

不设也照常部署，只是 AI 提案按钮禁用（未配置）：

- `SHOP_AI_HUB_BASE_URL`：Billing Hub 的接口地址，只写到主机，须以 `https://` 开头。
- `SHOP_AI_HUB_CLIENT_ID`：Billing Hub 为本项目开通的客户端 ID。
- `SHOP_AI_HUB_ACCESS_SECRET`：Billing Hub 为本项目签发的项目密钥。
- `SHOP_AI_MODEL`：模型名。
- `SHOP_AI_DAILY_CALL_LIMIT`：全站每日调用上限，可以不设（默认 50）。

在 Billing Hub 为本项目开通身份与签发密钥另在 `ai_billing_hub` 的会话里做。

## 偏离与取舍，请审阅

- `output_schema` 给了默认值 `None`（验收只写了「不为 None 时」带 `output_config`），`max_tokens` 无默认值、必须给出；两者都只能以关键字传入。
- `max_tokens` 与 `output_schema` 不在客户端校验，原样发给 Billing Hub（不合格时 Billing Hub 回 422，归为 `AiUnavailable`）；`output_schema` 不能编码为 JSON 时同样抛 `AiUnavailable`、不发请求。
- 传输层另有 `TransportTimeout`（`TransportError` 的子类），用来把超时与其余失败分开；`sms_provider` 只有一种 `TransportError`，因为它不需要区分。
- token 数只检查是整数（布尔不算），不检查非负。
- 本任务没有加 FastAPI 依赖函数（如 `get_ai_hub`）：调用方的接线在 SHOP-TASK-248 及其后。

## 验证到什么程度

- 人工逐条对照验收标准与 DESIGN 1.23「节日促销活动」第 13 条的「链路」与「限制」两段。没有连真实的 Billing Hub；请求体与响应格式按任务所述的 Billing Hub `POST /v1/messages` 约定写入，客户端以替身传输函数、默认传输以替身 opener 模拟。
- `tests/test_ai_hub.py` 以替身传输函数覆盖：请求的地址（含末尾斜杠）、方法、两个请求头、请求体字段与 `feature`、`output_config` 的格式；200 的解析（含多余字段）；超时（`TransportTimeout` 与 `TimeoutError`）为 `AiTimeout`、网络错误与各种非 200 为 `AiUnavailable`，都只发一次；缺 `usage`、`text` 不是字符串、token 数不是整数、不是 JSON 或被截断时为 `AiUnavailable`；四项缺一或地址不是 `https://` 时 `AiNotConfigured` 且不调用传输函数；默认设置未配置、从 `SHOP_AI_*` 环境变量读取；`repr` 只显示 configured，请求、响应、结果与异常的 repr 和消息里没有密钥、请求内容或响应内容；本模块不写日志。默认传输：一次 JSON POST、超时 60 秒、响应最多读 64 KB、非 2xx 作为响应、超时（含被 `URLError` 包着的）为 `TransportTimeout`、其余为 `TransportError`、拒绝 `http://`、不跟随重定向。
- `tests/test_compose_env.py`：五个 `SHOP_AI_` 变量在 `shop_api` 的写法、不在 `shop_jobs`、compose 默认值与 `Settings` 默认值一致（四项字符串为空串、上限为整数 50）。
- 每条新增测试的文档字符串写明它守住的第 13 条哪一句。后端测试由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑后端测试。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 配置——app/core/config.py 的 Settings 照落实写法加五个字段；AiHubClient.from_settings(settings, transport=urllib_transport) 的 configured 属性照落实写法判断，repr 只显示 configured
      - 调用——AiHubClient.create_message(system, user_text, *, max_tokens, output_schema) 发出一次 POST（地址为 base_url 去掉末尾斜杠后接 /v1/messages），output_schema 不为 None 时带 output_config 的 format（type 为 json_schema）；成功（200 且 JSON 里有字符串 text 与整数的 usage.input_tokens、usage.output_tokens）返回 AiReply(text, input_tokens, output_tokens)；未配置抛 AiNotConfigured 且不发请求；超时抛 AiTimeout；其余失败（连不上、非 200、响应不是所述的 JSON）抛 AiUnavailable，异常不带响应内容与密钥；不重试
      - 部署配置——docker-compose.yml 的 shop_api 加五个 SHOP_ 变量（只为此改动这个文件，变量名与 .env.example 里运营者已加的相同），不写真实地址与密钥
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_ai_hub.py 以替身 transport 覆盖：请求的地址、方法、两个请求头、请求体字段与 feature；200 的解析；超时、非 200、缺 usage、text 不是字符串时的异常；未配置时不调用 transport；repr 与异常信息里没有密钥；tests/test_compose_env.py 的现有核对（每个 Settings 字段都传给 api）因新字段需要的期望值照现有写法更新。每条测试的文档字符串写明它守住的第 13 条哪一句
      - docs/task-records/SHOP-TASK-247.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明五个变量名、类与异常名，并列出上线前 Kelvin 要在服务器私有配置里设置的变量）；改动只落在 allowed_change_paths；除 docker-compose.yml 的这五个变量外不改部署配置、CI 与 .platform/，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

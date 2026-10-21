# SHOP-TASK-129 Stripe webhook 接口

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 7 条（端点 POST `/api/stripe/webhook`；用原始请求体与 webhook 签名密钥校验 Stripe-Signature（含时间容差），不通过回 400、不处理；已提交过的事件直接回 200；处理失败整个事务回滚、回 5xx，由 Stripe 重发；其他类型回 200 不处理；不需要会话与 CSRF；不存、不记录事件原文）、第 1 条（webhook 事件 livemode 为真即拒绝）与第 2 条（签名密钥未配置时不启用 Stripe），以及 `docs/HANDOFF.md` 0.55 的落实写法（3），新增 `app/api/stripe_webhook.py`，处理交给 SHOP-TASK-127、128 的 `handle_webhook_event`；`app/main.py` 只加 import 与 `include_router`。测试见 `tests/test_stripe_webhook_api.py`。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，支付状态与退款。
- 路由器：前缀 `/api/stripe`，`route_class` 用 `app/api/order_lookup.py` 的 `_NoStoreRoute`，处理函数与依赖产生的全部响应（含 413 等错误）都带 `Cache-Control: no-store`。路径存在而方法不匹配的 405 由框架在路由之外回答；未预期的异常不在接口里捕获，由框架回 500，`get_session` 关闭会话时未提交的改动回滚。
- 检查顺序（每一步不通过即停止，之前不读写数据库、不调用 Stripe）：
  1. 请求体依赖 `_raw_body` 照 `app/api/pay.py` 的 `_json_body` 逐块读，累计超过 `MAX_WEBHOOK_BODY_BYTES = 256 * 1024` 即 413 `{"detail":"request body too large"}`；不看 Content-Type 与 Content-Length。它声明在会话与适配器依赖之前。
  2. `stripe_configured(request.app.state.settings)` 为假（含签名密钥未配置、密钥不是测试密钥）回 503 `{"detail":"stripe_unavailable"}`，不验签。
  3. `verify_webhook_signature(原始请求体, Stripe-Signature, settings.stripe_webhook_secret, 当前时间)` 为假回 400 `{"detail":"signature_invalid"}`。
  4. `parse_webhook_event` 返回 None 回 400 `{"detail":"event_invalid"}`。
  5. `livemode` 为真回 400 `{"detail":"livemode_rejected"}`，记一条 error 日志 `stripe_livemode`（只含类别），不写事件记录。
  之后以 `get_stripe_gateway` 取得的适配器调用 `handle_webhook_event`；当前时间取 `app/api/pay.py` 的 `_utcnow`，在本模块以同名导入（测试替换 `app.api.stripe_webhook._utcnow`）。
- 各响应：processed、ignored、duplicate 都回 200 `{"received":true}`；unavailable 回 503 `{"detail":"stripe_unavailable"}`（沿用 `app/api/pay.py` 的 `_stripe_unavailable`）。响应体不回显任何输入，不带事件 ID 或任何 Stripe ID。接口不读订单访问 cookie、会员或管理员会话，不要求 CSRF；除 livemode 一条外不写日志，不记录请求体、请求头与事件内容。
- 偏离与按验收标准未写死之处的取法（未改设计，未改服务层）：
  - Stripe-Signature 请求头出现不止一次时按缺失处理（交给 `verify_webhook_signature` 的是 None，回 400 signature_invalid），与 `_require_csrf` 对重复请求头的取法相同。
  - 请求体正好 256 KB 不算超过，继续后面的检查。
  - 会话与适配器依赖（`get_session`、`get_stripe_gateway`）由 FastAPI 在处理函数之前、请求体依赖之后解析：只打开会话、构造适配器，不查询数据库、不调用 Stripe。因此数据库未配置时 `get_session` 的 503 `database is not configured` 会先于第 2 步回答；构造真实适配器时 `stripe_configured` 对非测试密钥记的 `stripe_key_not_test`（同一进程一次）属于服务层按设计记的日志。
- 测试：`tests/test_stripe_webhook_api.py`（TestClient 与 SQLite 内存库，夹具照 `tests/test_stripe_pay_api.py`；Stripe 配置照 `tests/test_stripe_gateway.py` 的 `make_settings` 在本文件里另写；以 `app.state.stripe_gateway` 注入 `FakeStripeGateway`；当前时间替换为固定的 NOW，签名在测试里用测试签名密钥与 NOW 计算；请求都不带 cookie 与 CSRF 令牌）覆盖：completed 已付款事件回 200、订单进入 demo_paid、事件记录为 processed、接口不写日志；同一事件再来回 200 且不再调用 Stripe；其他类型回 200 并记为 ignored；refund.updated 按查询到的 succeeded 更新退款尝试；Stripe 不可用回 503 且没有事件记录；缺签名头、签名不符、时间戳早于或晚于容差回 400，不写数据库、不调用 Stripe；签名密钥为空（签名按空密钥计算）或密钥为 `sk_live_` 前缀时回 503；超过 256 KB 回 413，先于验签与配置检查；正好 256 KB 不是 413；签名正确而内容不合格（不是 JSON、不是对象、会话缺失、livemode 不是布尔值）回 400 event_invalid；livemode 为真回 400、没有事件记录并记一条 `stripe_livemode`。各响应都断言带 no-store 且不出现事件 ID、会话 ID、PaymentIntent ID、创建幂等键、签名等输入。每条测试的文档字符串写明守住的设计原句。现有测试未改。
- 运营者在合并部署后完成（本任务未做）：在 Stripe 测试环境（acuven-shop-test）建 webhook 端点，地址为站点的 `/api/stripe/webhook`；订阅 `CHECKOUT_EVENT_TYPES` 与 `REFUND_EVENT_TYPES` 的六种事件（`checkout.session.completed`、`checkout.session.async_payment_succeeded`、`checkout.session.async_payment_failed`、`refund.created`、`refund.updated`、`refund.failed`）；把该端点的签名密钥（`whsec_` 开头）写进私有配置的 `stripe_webhook_secret`。在那之前 `stripe_configured` 为假，接口一律回 503。
- 本任务未改 `app/services/`、`app/api/` 下的其他文件、模型、迁移、现有测试、`.platform/`、CI、部署配置与前端，未装依赖，未改 `docs/TODO.md`。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 1、2、7 条与 HANDOFF 0.55 原句自查，并核对与 `stripe_configured`、`verify_webhook_signature`、`parse_webhook_event`、`handle_webhook_event` 及 `_NoStoreRoute` 的实际代码一致。新增的测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；检查命令结果由 Worker 另行记录。

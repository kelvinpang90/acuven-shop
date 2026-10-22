# SHOP-TASK-138 后台站点设置的请求模块 A09

- [x] 为 A09 页面新增请求模块 `frontend/src/api/adminSiteSettings.ts` 与测试 `frontend/src/api/adminSiteSettings.test.ts`，调用 SHOP-TASK-122 的 `GET` 与 `PUT /api/admin/site-settings`，供 SHOP-TASK-139 使用；只有支付方式，不含短信验证开关（Kelvin 2026-10-07 与 2026-10-15 的决定）。不做页面，未改路由与导航；未装依赖（未改 `package.json` 与锁文件）；未改后端、设计、`docs/TODO.md`、`.platform/`、CI 与部署配置。设计闸门：不适用（支付方式的凭据检查与审计在 SHOP-TASK-122 的服务端，请求模块只传递与校验字段）。拆分：A09 页面的请求层（3 个允许路径）。
- 请求（写法沿用 `api/adminStoreDesign.ts`，`CSRF_HEADER` 取自 `api/pay.ts`）：
  - `readAdminSiteSettings(signal?)`：`GET /api/admin/site-settings`，不带查询参数，同源 cookie、`cache: "no-store"`、`Accept: application/json`，没有请求体与 CSRF 头。
  - `saveAdminSiteSettings(paymentMethod, csrfToken, signal?)`：`PUT /api/admin/site-settings`，不带查询参数，同源 cookie、`cache: "no-store"`，请求头 `Content-Type: application/json` 与 `X-CSRF-Token` 为调用方给出的令牌，JSON 请求体只有 `{"payment_method": …}`。参数只是支付方式这个值，调用方没有办法把别的字段（含短信验证开关）带进请求体。
- 字段：导出 `PAYMENT_METHODS`（`simulated`、`stripe_test`）与 `PaymentMethod`。读取只取 `payment_method`（两种取值之一，大小写不同不算）、`stripe_available`（布尔值）与 `csrf_token`（非空字符串）；保存只取 `payment_method`（两种取值之一）。多出的字段不带进结果；字段缺少或类型不对、响应体不是对象或不是 JSON 都算不合格。
- 归类：读取为 `ok`（200 且合格）、`none`（401）、`failed`（其他状态，含 200 而响应体不合格）与 `network`（fetch 抛错）。保存为 `ok`（200 且合格，带保存后的 `payment_method`）、`none`（401）、`csrf`（403）、`not_configured`（422 且响应体的 `detail` 恰为字符串 `stripe_not_configured`）、`failed`（其他状态，含其他 422 与 200 而响应体不合格）与 `network`。中止照 `adminStoreDesign.ts`：signal 传给 fetch，fetch 因中止抛错时归为 `network`，页面按 signal 已中止不再更新。
- 测试（vitest，fetch 以 `vi.fn((input: RequestInfo | URL, init?: RequestInit) => …)` 替身代替，未加测试依赖；每条注释写明所守的 SHOP-TASK-122 记录或本任务验收原句）：两个请求的地址、方法、cookie、缓存、请求头与 signal；保存两种取值时请求体只有 `payment_method`、不含任何短信字段；读取多出的字段（含 `sms_verification_enabled`）不进结果；读取与保存的各种不合格（大写与首字母大写、前导空格、未知值、空串、null、数字、`stripe_available` 为字符串或数字、`csrf_token` 为空、null 或数字、字段缺少、不是对象、不是 JSON）都算 `failed`；各状态码的归类（422 的 `detail` 为其他字符串、大写、错误列表、缺少或响应体不是 JSON 时归 `failed`，500 即使 detail 为 `stripe_not_configured` 也归 `failed`）、网络中断与中止。
- 偏离与取舍，请审阅（未改设计）：
  - 验收说从 `mock.calls` 取值时用 `String(…)` 收窄；`input` 的类型含 `Request`、`init.body` 的类型含 `Blob` 等，这些都没有自己的 `toString`，带类型规则里的 `no-base-to-string` 会报。测试改用按类型收窄（地址照 `adminStoreDesign.test.ts` 取 `string`、`URL.href` 或 `Request.url`；请求体只接受字符串），同样不让 `any` 流出，`JSON.parse` 的结果先赋给 `unknown`。
  - fetch 替身在 signal 已中止时与浏览器一样抛 `AbortError`，地址不是本接口时拒绝；中止的测试用同一个替身，不另写一个。
  - 422 只有 `detail` 恰为 `stripe_not_configured` 才归 `not_configured`；SHOP-TASK-122 的请求体不合格错误（`_body_errors` 的列表）与其他 detail 都归 `failed`。
- 留给之后的任务：A09 页面（支付方式两项、`stripe_available` 为假时禁用 Stripe 选项与 `admin.stripe_not_configured`、保存与被拒的提示）由 SHOP-TASK-139 实现，用本模块读取与保存。短信验证开关按 Kelvin 2026-10-07 的决定等短信验证服务与会员结账路径上线后再做。
- 验证到什么程度：人工逐条对照本任务验收标准与 SHOP-TASK-122 记录的接口段（字段、状态码与 `stripe_not_configured`）自查。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端发起请求。

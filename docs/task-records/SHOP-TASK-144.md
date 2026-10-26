# SHOP-TASK-144 会员短信与登录的请求模块，站点设置读取人机挑战公开 key

设计闸门：DESIGN 1.12（提交 acf576e）「权限与资料保护」第 1、3、5 条与 HANDOFF 0.58。

- [x] 新文件 `frontend/src/api/memberAuth.ts`，写法沿用 `frontend/src/api/orders.ts` 与 `frontend/src/api/pay.ts`：同源 cookie、`cache: "no-store"`、请求头 `Accept` 与 `Content-Type` 为 `application/json`，五个请求都不带查询参数；422 是否为号码不成立照 `orders.ts` 的 `hasPhoneInvalid` 看出错列表里的 `type`；其他错误码照 `adminRefunds.ts` 的 `errorCode` 只认字符串 `detail`。手机号原文、地区代码、验证码、密码与人机挑战令牌只在请求体里，重设提交的 CSRF 令牌只在 `X-CSRF-Token` 请求头里（`CSRF_HEADER` 取自 `pay.ts`）；模块不写日志、不碰浏览器存储。五个请求：
  - `sendSmsCode(phone, purpose, captchaToken, signal?)` → `POST /api/sms/send`（SHOP-TASK-070），请求体 `phone`、`phone_region`、`purpose`（`register`、`login`、`reset_password`、`checkout`）、`captcha_token`。归类：`sent`（200，响应体不读）、`disabled`（403 sms_disabled）、`captcha_failed`（403 captcha_failed）、`not_whitelisted`（422 not_whitelisted）、`undeliverable`（422 sms_undeliverable）、`phone_invalid`（422 出错列表含 phone_invalid）、`rate_limited`（429）、`suspended`（503 sms_suspended）、`failed`（其他）、`network`。
  - `smsLogin(phone, purpose, code, signal?)` → `POST /api/member/sms-login`（SHOP-TASK-076），请求体 `phone`、`phone_region`、`purpose`（`register`、`login`、`checkout`）、`code`。归类：`{ kind: "ok", created }`（200 且 `created` 为布尔，否则 `failed`）、`wrong`（422 code_wrong 或 code_expired）、`disabled`（403 sms_disabled）、`unavailable`（503，不论错误码）、`phone_invalid`、`failed`、`network`。
  - `passwordLogin(phone, password, signal?)` → `POST /api/member/password-login`（SHOP-TASK-077），请求体 `phone`、`phone_region`、`password`。归类：`ok`（204）、`login_failed`（401 login_failed）、`unavailable`（503）、`phone_invalid`、`failed`、`network`。
  - `verifyPasswordReset(phone, code, signal?)` → `POST /api/member/password-reset/verify`（SHOP-TASK-079），请求体 `phone`、`phone_region`、`code`。归类：`{ kind: "registered", csrfToken }`（200、`registered` 为 true、`csrf_token` 为非空字符串）、`not_registered`（200、`registered` 为 false）、`wrong`、`disabled`、`unavailable`（503，不论错误码）、`phone_invalid`、`failed`（其他，含 200 而响应体不合格）、`network`。
  - `submitPasswordReset(password, csrfToken, signal?)` → `POST /api/member/password-reset`（SHOP-TASK-079），请求体只有 `password`，请求头另带 `X-CSRF-Token`。归类：`ok`（204）、`expired`（401 reset_expired）、`disabled`（403 sms_disabled）、`csrf`（403 csrf_failed）、`too_short`（422 password_too_short）、`unavailable`（503）、`failed`、`network`。
- [x] `frontend/src/api/siteSettings.ts`：`SiteSettings` 另有 `turnstile_site_key: string | null`，由新函数 `parseTurnstileSiteKey` 取值（非空字符串原样取到；缺少、null、不是字符串或空串为 null），不影响开关的读取与检查（与 `whatsapp_contact_url` 相同）；模块注释补上这一项。其余字段、检查与 WhatsApp 链接的读取不变。现有用到 `fetchSiteSettings` 的结账页只读开关，不受影响。
- [x] 测试：
  - 新文件 `frontend/src/api/memberAuth.test.ts`（vitest，fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替，`mock.calls` 的地址以 `String(…)` 收窄，请求体只在为字符串时 `JSON.parse`）：五个请求的地址、方法、同源 cookie、no-store、中止信号、请求头恰好为约定的几项（重设提交另有 `X-CSRF-Token`）与请求体恰好为约定字段；地址不带 `?`，号码、验证码、密码与令牌的字面值都不出现在地址里；各接口的状态码与错误码逐条归类，含错误码不符、422 不是 phone_invalid 的出错列表、409 与其他未列出的状态归 `failed`，200 而响应体不合格归 `failed`，网络中断归 `network`。每条测试注释写明守住的接口记录或验收的哪一句。
  - `frontend/src/api/siteSettings.test.ts`：两处对结果整体做相等比较的断言补上 `turnstile_site_key: null`，注释写明是本任务的改动；新增 key 为字符串、null、缺少、空串、数字五种情形（开关照常读出），以及 key 合格时开关不合格仍抛错。测试里的 key 是虚构值。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 发送验证码的 503 只认 `sms_suspended`，别的 503 错误码归 `failed`（验收写的是「suspended（503 sms_suspended）」，与短信登录、重设验证的「503，不论错误码」不同，照原文区分）。429 只看状态码。
  - 403、401 与重设提交的 422 照「错误码」判断：错误码不符的归 `failed`（如 403 别的错误码、401 不是 login_failed / reset_expired）。
  - 重设验证 200 时 `registered` 为 false 不再看 `csrf_token`；`registered` 缺少或不是布尔归 `failed`。
  - 短信登录与重设验证的错误归类共用一个内部函数 `codeRejection`，两者的归类与验收逐条相同。
  - 另导出 `parseTurnstileSiteKey` 与五个接口地址常量，供测试与之后的任务使用；不带网络中断后的自动重试（这几个请求都不是幂等写，是否重试由页面决定）。
- 组件与页面留给 SHOP-TASK-145 至 148（短信验证组件 V1、登录 P12、忘记密码 P12、注册 P11）；本任务不做页面与组件，不改路由与页头，不渲染人机挑战。
- 改动只在 `frontend/src/api/memberAuth.ts`、`frontend/src/api/memberAuth.test.ts`、`frontend/src/api/siteSettings.ts`、`frontend/src/api/siteSettings.test.ts` 与本文件；未改后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准与 `app/api/sms.py`、`app/api/member_auth.py`、`app/api/member_pw.py`、`app/api/member_pw_reset.py` 的文档字符串与错误码自查。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未在浏览器里对真实接口验证。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 frontend/src/api/memberAuth.ts，写法沿用 frontend/src/api/orders.ts（同源 cookie，cache 为 no-store，Accept 与 Content-Type 为 application/json，都不带查询参数；422 是否为 phone_invalid 照 orders.ts 判断 detail 列表里的 type；409 与 403 等错误码照 frontend/src/api/adminRefunds.ts 的 errorCode 读取）；手机号、地区、验证码、密码、人机挑战令牌只在请求体里，重设提交的 CSRF 令牌只在 X-CSRF-Token 请求头里（CSRF_HEADER 取自 frontend/src/api/pay.ts），都不进地址、日志或浏览器存储
      - 发送验证码——请求体只有 phone、phone_region、purpose（register、login、reset_password、checkout 之一）与 captcha_token；结果归为 sent（200）、disabled（403 sms_disabled）、captcha_failed（403 captcha_failed）、not_whitelisted（422 not_whitelisted）、undeliverable（422 sms_undeliverable）、phone_invalid、rate_limited（429）、suspended（503 sms_suspended）、failed（其他）与 network
      - 短信登录或注册——请求体只有 phone、phone_region、purpose（register、login、checkout 之一）与 code；结果归为 ok（200，带布尔的 created，响应体不合格算 failed）、wrong（422 code_wrong 或 code_expired，二者都归此类）、disabled（403 sms_disabled）、unavailable（503，不论错误码）、phone_invalid、failed 与 network；密码登录——请求体只有 phone、phone_region 与 password；结果归为 ok（204）、login_failed（401 login_failed）、unavailable（503）、phone_invalid、failed 与 network
      - 重设验证——请求体只有 phone、phone_region 与 code；结果归为 registered（200 且 registered 为真、带非空字符串 csrf_token）、not_registered（200 且 registered 为假）、wrong（422 code_wrong 或 code_expired）、disabled（403 sms_disabled）、unavailable（503，不论错误码）、phone_invalid、failed（其他，含 200 而响应体不合格）与 network；重设提交——请求体只有 password，请求头带调用方给出的 csrf_token；结果归为 ok（204）、expired（401 reset_expired）、disabled（403 sms_disabled）、csrf（403 csrf_failed）、too_short（422 password_too_short）、unavailable（503）、failed 与 network
      - frontend/src/api/siteSettings.ts 的 SiteSettings 另取 turnstile_site_key——字符串原样取到，没有、不是字符串或为空串时为 null，不因它让开关的读取失败（与 whatsapp_contact_url 的做法相同）；其余字段与检查不变
      - 用 vitest（不加测试依赖，fetch 以替身代替）在 frontend/src/api/memberAuth.test.ts 验证意图——五个请求的地址、方法、请求头与请求体（只有约定字段；重设提交带 X-CSRF-Token；都不带查询参数）；各状态码与错误码的归类（含 422 不是 phone_invalid 时归 failed、409 等未列出的状态归 failed）与网络中断；在 frontend/src/api/siteSettings.test.ts 补 turnstile_site_key 的取值（字符串、null、缺少、空串、数字），现有对结果整体做相等比较的断言补上这个字段并注明原因；每条测试写明它守住的是哪个接口记录或 UX 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出（CI 的 eslint 带类型规则）；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-144.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；五个请求、字段与归类、偏离、验证到什么程度，并写明组件与页面留给 SHOP-TASK-145 至 148）
      - 改动只落在 allowed_change_paths（不做页面与组件，不改路由与页头）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.58），不合并、不直推
```

# SHOP-TASK-164 会员中心的请求模块（积分、优惠券、首次设置密码与注销）

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 3 条（首次设置密码在已登录会话内、至少 8 位；注销先以短信验证码确认，开关关闭时以当前密码或会话内二次确认）、第 5 条（写操作另须 CSRF 令牌）与「边界与原则」第 4 条（确认注销时按当时的开关值判定），以及 HANDOFF 0.63。依据 docs/UX.md 0.13 的 P13（我的积分、我的优惠券、设置与注销）。会员前端第三批的请求层之三；更改已有密码走 P12 忘记密码（`memberAuth.ts`），本模块不做。

- [x] 新文件 `frontend/src/api/memberAccount.ts`，写法沿用 `memberAuth.ts` 与 `memberOrders.ts`：同源 cookie、`cache: "no-store"`、`Accept: application/json`，有请求体时另有 `Content-Type: application/json`；`CSRF_HEADER` 取自 `pay.ts`。六个请求都不带查询参数；号码一律由服务端取本账号的，请求里不带。网络中断与中止都归 network。
  - `readMemberPoints(signal?)`：GET `/api/member/points`。ok（200，取 `balance`、`pending`、`next_expiry`（`points` 与马来西亚日期 `date`，或 null）与 `history`（`type` 为 earned、redeemed、expired、returned、reversed 之一，`points` 带正负号，`order_number` 与 `created_at`）），signed_out（401 member_session_required），failed（其他，含 200 以外的 2xx 与不是 JSON），network。
  - `readMemberCoupons(signal?)`：GET `/api/member/coupons`。ok（200，取 `available`（`code`、`type` 为 fixed 或 percent、`amount_sen`、`percent`、`min_spend_sen`、`valid_until`）与 `used`（`code`、`order_number`、`discount_sen`）），signed_out、failed 与 network 同上。
  - `setMemberPassword(password, csrfToken, signal?)`：POST `/api/member/password`，请求体只有 `password`。done（204）、too_short（422 password_too_short）、already_set（409 password_already_set）、signed_out、csrf（403 csrf_failed）、unavailable（503）、failed（其他，含结构不合格的 422）、network。
  - `sendDeleteCode(captchaToken, csrfToken, signal?)`：POST `/api/member/delete/send-code`，请求体只有 `captcha_token`。照 `sendSmsCode` 归类：sent（200，响应体不读）、disabled（403 sms_disabled）、captcha_failed（403 captcha_failed）、not_whitelisted（422 not_whitelisted）、undeliverable（422 sms_undeliverable）、rate_limited（429）、suspended（503 sms_suspended），另有 signed_out 与 csrf；没有 phone_invalid（结构不合格的 422 归 failed）；其余 failed、network。
  - `verifyDeleteCode(code, csrfToken, signal?)`：POST `/api/member/delete/verify`，请求体只有 `code`。verified（204，服务端为当前会话签发 10 分钟一次性批准）、wrong（422 code_wrong 或 code_expired，同样处理）、disabled（403 sms_disabled）、unavailable（503，不论错误码）、signed_out、csrf、failed、network。
  - `confirmDeletion(password, csrfToken, signal?)`：POST `/api/member/delete`。`password` 为字符串时请求体只有 `password`，为 null 时为空对象 `{}`（服务端显式 null 是 422）。deleted（204，服务端清除会话 cookie）、not_verified（403 delete_not_verified）、password_wrong（403 password_wrong）、password_required（422 password_required）、rate_limited（429）、unavailable（503）、signed_out、csrf、failed、network。确认方式由服务端按当时读取的开关判定，页面不判断。
  - 共有归类：六个接口的 401 member_session_required 归 signed_out，四个写接口的 403 csrf_failed 归 csrf；错误码与状态码必须同时对上，否则归 failed（例如读取的 403 csrf_failed、401 别的错误码）。
  - 密码、验证码与人机挑战令牌只在请求体，CSRF 令牌只在 `X-CSRF-Token` 请求头；模块不写日志，不读写 localStorage、sessionStorage 与 cookie，也不在模块变量里保存它们。
- [x] 测试 `frontend/src/api/memberAccount.test.ts`（vitest，fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替，从 `mock.calls` 取值时以 `String(…)` 收窄）：
  - 「member account requests」：两个读取为 GET、地址恰好是接口路径、没有 `?`、同源 cookie、no-store、带调用方的 signal、请求头只有 Accept、没有请求体；五种写调用（确认注销分有无密码）为 POST、请求头恰好是 Accept、Content-Type 与 X-CSRF-Token、请求体只有约定字段，没有密码时为 `{}`；另一条以 localStorage、sessionStorage、document.cookie 与 console 的替身确认六个请求的地址里没有密码、验证码、人机挑战令牌与 CSRF 令牌，且不调用存储、不写 cookie、不写控制台。
  - 「reading points」「reading coupons」：200（含 next_expiry 为 null、空列表）、200 不是 JSON、204、401 两种、403 csrf_failed、503 与网络中断的归类；积分另有一条确认响应体里多出的字段不带进结果。
  - 「setting the first password」「sending the deletion code」「verifying the deletion code」「confirming deletion」：各状态码与错误码的归类（含 401、403 各码、409、422 各码与结构不合格的 422、429、503、未列出的状态）与网络中断。
  - 每条测试的注释写明守住的是哪个接口记录、UX 0.13 P13 或 DESIGN 1.14 的哪一句。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 读取照 `memberOrders.ts` 以类型断言读取，不逐字段校验；只按接口记录的字段取出，不把多出的字段带进结果。200 的 JSON 为 null 时取字段出错，同样归 failed。
  - 发送注销验证码的 503 只有 sms_suspended 归 suspended，别的错误码归 failed（同 `sendSmsCode`）；核验与确认注销的 503 不论错误码都归 unavailable。429 只看状态码（同 `sendSmsCode`）。
  - 确认注销的密码参数以 null 表示「没有给」；空字符串照样作为 `password` 发出，由服务端以结构不合格的 422 拒绝（归 failed）。
- 留给后续任务：页面（P13 我的积分、我的优惠券与设置段的首次设置密码与注销，以及注销后清除暂存）留给 SHOP-TASK-165 至 167。本任务不做页面，不改路由与页头。
- 改动只在 `frontend/src/api/memberAccount.ts`、`frontend/src/api/memberAccount.test.ts` 与本文件；`memberAuth.ts`、`memberOrders.ts` 与 `pay.ts` 未改（只引用 `pay.ts` 导出的 `CSRF_HEADER`）。未改后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、三个后端文件的文档字符串与处理函数、UX 0.13 P13 与 DESIGN 1.14 自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

## 登记时的验收标准（原文）

2026-10-29 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 frontend/src/api/memberAccount.ts，写法沿用 frontend/src/api/memberAuth.ts 与 frontend/src/api/memberOrders.ts（同源 cookie，cache 为 no-store，Accept 为 application/json，有请求体时 Content-Type 为 application/json；CSRF_HEADER 取自 frontend/src/api/pay.ts）；都不带查询参数；六个接口的 401（member_session_required）都归 signed_out，四个写接口的 403 csrf_failed 都归 csrf；密码、验证码、人机挑战令牌与 CSRF 令牌只在请求体与请求头里，不进地址、日志或浏览器存储
      - 积分与优惠券——两个读取都接收 AbortSignal；积分归为 ok（200，取 balance、pending、next_expiry（points 与马来西亚日期 date，或 null）与 history（最多 100 条，每条 type 为 earned、redeemed、expired、returned、reversed 之一，points 带正负号，order_number 与 created_at））、signed_out、failed 与 network；优惠券归为 ok（200，取 available（code、type 为 fixed 或 percent、amount_sen、percent、min_spend_sen 与 valid_until）与 used（code、order_number 与 discount_sen））、signed_out、failed 与 network；照 memberOrders.ts 以类型断言读取，不是 JSON 归 failed
      - 首次设置密码——请求体只有 password，请求头带调用方给出的 CSRF 令牌，接收 AbortSignal；结果归为 done（204）、too_short（422 password_too_short）、already_set（409 password_already_set）、signed_out、csrf、unavailable（503）、failed（其他，含结构不合格的 422）与 network
      - 注销三步——发送的请求体只有 captcha_token（不带号码，服务端取本账号的号码），结果照 memberAuth.ts 的 sendSmsCode 归类（sent 为 200，disabled、captcha_failed、not_whitelisted、undeliverable、rate_limited、suspended，failed 与 network；没有 phone_invalid），另有 signed_out 与 csrf；核验的请求体只有 code，结果归为 verified（204，服务端签发 10 分钟一次性批准）、wrong（422 code_wrong 或 code_expired，二者同样处理）、disabled（403 sms_disabled）、unavailable（503）、signed_out、csrf、failed 与 network；确认注销的请求体只在调用方给出密码时为 password，否则为空对象，结果归为 deleted（204，服务端清除会话 cookie）、not_verified（403 delete_not_verified）、password_wrong（403 password_wrong）、password_required（422 password_required）、rate_limited（429）、unavailable（503）、signed_out、csrf、failed 与 network；三步都带调用方给出的 CSRF 令牌并接收 AbortSignal
      - 用 vitest（不加测试依赖，fetch 以替身代替）在 frontend/src/api/memberAccount.test.ts 验证意图——六个请求的地址、方法、请求头与请求体（只有约定字段；写操作带 X-CSRF-Token，读取不带；都不带查询参数；确认注销没有密码时请求体为空对象）；各状态码与错误码的归类（含 401、403 各码、409、422 各码、429、503）与网络中断；整个过程不写 localStorage、sessionStorage 与 cookie；每条测试写明它守住的是哪个接口记录、UX 或 DESIGN 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-164.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；六个请求、归类、偏离、验证到什么程度，只写实际存在的测试，并写明页面留给 SHOP-TASK-165 至 167）
      - 改动只落在 allowed_change_paths（不改 memberAuth.ts、memberOrders.ts 与 pay.ts，只引用它们导出的内容；不做页面，不改路由与页头）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.14 提交 00f8f34 与 HANDOFF 0.63），不合并、不直推
```

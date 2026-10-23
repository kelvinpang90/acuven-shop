# SHOP-TASK-140 后台退款详情的 Stripe 字段与重试请求 A03

- [x] 在后台退款审核 A03 的请求模块 `frontend/src/api/adminRefunds.ts` 里，让详情另取 SHOP-TASK-130 给出的 `paid_via_stripe`、`payment_method_type` 与 `stripe_refunds`，并新增调用 SHOP-TASK-131 手动重试接口（`POST /api/admin/refunds/{内部 ID}/stripe-retry`）的函数 `retryStripeRefund`，供 SHOP-TASK-141、142 使用。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.57，退款（Stripe 退款尝试的读取与手动重试的请求；状态判定、合计上限与审计在服务端）。拆分：A03 Stripe 部分的请求层；页面的显示（付款方式、期限提示、尝试表）留给 SHOP-TASK-141，重试按钮与重试后的处理留给 SHOP-TASK-142。
- 字段（`AdminRefundDetail`，写法沿用 SHOP-TASK-107 对积分字段的 `readPoints`，新增 `readStripe` 与 `readStripeRefund`）：
  - `paid_via_stripe`：布尔。
  - `payment_method_type`：字符串或 null，原样取，不做取值校验（类型由页面按 fpx / grabpay / 其他选期限提示）。
  - `stripe_refunds`：数组，每条（新类型 `AdminRefundStripeRefund`）只取 `id`、`created_at`、`amount_sen`、`status` 与 `stripe_refund_id` 五个字段；多出的字段（如 `last_error_type`）不带进结果。数组按接口给的顺序原样保留（最新在前），不重新排序。尝试状态导出为 `STRIPE_REFUND_STATUSES`（pending、succeeded、failed）。
- 不合格的情形（都按响应体不合格归为 `failed`）：三个字段缺少或类型不对；任何一条尝试不是对象、`id` 或 `amount_sen` 不是正安全整数（含金额为 0、负数、小数、字符串）、`created_at` 照 `isTime` 不能解析、`status` 不是三种小写值之一（含 `FAILED`、`Succeeded`、`canceled`、`requires_action`）、`stripe_refund_id` 不是字符串或 null（含缺少）；`paid_via_stripe` 为假而 `payment_method_type` 不为 null（含空字符串）或 `stripe_refunds` 不为空。其余字段、检查与 `readAdminRefund` 的归类不变。
- 重试请求与归类（`retryStripeRefund(id, csrfToken)`，地址由新导出的 `adminRefundStripeRetryUrl` 拼出）：POST、同源 cookie、`cache: "no-store"`，没有查询参数与请求体，请求头只有 `Accept` 与 `X-CSRF-Token`（`CSRF_HEADER` 取自 `api/pay.ts`，值为调用方给出的详情 `csrf_token`），不带 `Idempotency-Key` 与 `Content-Type`；路径 ID 照 `parseRefundId`，不合法时不发请求、归 `failed`。结果 `StripeRetryReply`：`done`（200，不读响应体）、`cap`（409 `stripe_refund_cap`）、`not_retryable`（409 `stripe_refund_not_retryable` 或 `refund_not_approved`）、`csrf`（403）、`none`（401）、`failed`（其他，含别的 409 错误码、不是 JSON 的 409、404 与 422）与 `network`（网络中断）。409 的错误码沿用 `reviewAdminRefund` 的 `errorCode` 读取。
- 测试（`frontend/src/api/adminRefunds.test.ts`，vitest，`fetch` 以已有的按顺序回答的替身 `stubFetch` 代替，其 `vi.fn` 参数带类型、调用记录收为字符串地址与 `RequestInit`，不让 any 流出）：三个字段原样取到（Stripe 付款已批准有两条尝试且顺序不变，接口给出从旧到新时也不重排；类型为 null；审核中与已拒绝的 Stripe 付款为真且列表为空；模拟支付的 false、null 与空列表；三种尝试状态各自合格）；多出的字段（`last_error_type`、调用次数、PaymentIntent ID）不带进结果；上面列出的各种不合格；重试请求的地址、方法、请求头与没有请求体；不合法的路径 ID 不发请求；200 不读响应体；各状态码与错误码的归类（409 的其他错误码归 failed）与网络中断；令牌不进地址、不写浏览器存储与 console。每条测试的注释写明它守住的 SHOP-TASK-130、131 记录段或 UX 0.12 A03 的哪一句。
- 已有测试的改动（只按需要，注释写明原因）：`api/adminRefunds.test.ts` 的 `DETAIL` 与 `pages/AdminRefundDetail.test.tsx` 的 `REQUESTED` 补上三个字段，取模拟支付的值（false、null、空列表）；其余共用样例（已批准、已拒绝与会员样例）都由它们展开，得到同样的值，原有断言不变。两个文件都没有删去测试，页面测试也没有新增测试。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 不按申请状态或现金退款额校验 Stripe 字段（例如已拒绝却有尝试、审核中却有尝试），只校验验收列出的情形；这些判定在服务端。
  - `paid_via_stripe` 为真时 `payment_method_type` 为空字符串照样接受（验收只要求字符串或 null）。
  - `retryStripeRefund` 不接收中止用的 `signal`，与 `reviewAdminRefund` 相同（写操作不随页面离开而中止）。
- 改动只在 `frontend/src/api/adminRefunds.ts`、`frontend/src/api/adminRefunds.test.ts`、`frontend/src/pages/AdminRefundDetail.test.tsx` 与本文件；未改 `pages/AdminRefundDetail.tsx` 与其他页面、路由与导航、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与锁文件、后端与设计。
- 留给之后的任务：A03 的付款方式、期限提示与尝试表的显示（SHOP-TASK-141）；重试按钮、提交中禁用、成功后重新读取与 `[admin.stripe_refund_blocked]` 的提示（SHOP-TASK-142）。
- 验证到什么程度：人工逐条对照验收标准、SHOP-TASK-130 与 131 记录段的字段、错误码与请求约定，以及 UX 0.12 A03 自查。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收，未连真实后端。检查命令结果由 Worker 另行记录。

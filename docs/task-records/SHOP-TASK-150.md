# SHOP-TASK-150 会员订单的请求模块（我的订单列表、详情、确认收货、退款申请与积分预估）

设计闸门：DESIGN 1.13（提交 450c2fa）「权限与资料保护」第 3、5 条（会话到期、退出与服务端授权检查；会员仅能访问自己认领或下单的订单，确认收货与退款申请规则与查单页相同，写操作另须 CSRF 令牌）、「订单与退款状态」「失败、并发与重试」（确认收货与退款申请幂等）与 HANDOFF 0.61。依据 docs/UX.md 0.13 的 P13「我的订单」与 P09、P10 的会员模式。会员前端第二批的请求层之二，调用已有的会员订单接口 `app/api/account_orders.py`（SHOP-TASK-104、105）。

- [x] 新文件 `frontend/src/api/memberOrders.ts`，写法沿用 `frontend/src/api/orderLookup.ts`：同源 cookie、cache 为 no-store、Accept（POST 另有 Content-Type）为 application/json；`IDEMPOTENCY_HEADER`、`CSRF_HEADER`、`RECHECK_DELAY_MS` 与 `wait` 取自 `pay.ts`；`SHIPPED`、`parsePointsEstimate` 与类型 `LookupOrder`、`RefundLineRequest`、`RefundRetry`、`RefundRefusal`、`RecheckOptions`、`PointsEstimate` 取自 `orderLookup.ts`。订单号只在请求体里，CSRF 令牌只在 X-CSRF-Token 请求头里，模块不写日志、不碰浏览器存储。
- [x] 五个请求（导出地址常量 `MEMBER_ORDERS_URL`、`MEMBER_ORDER_VIEW_URL`、`MEMBER_CONFIRM_RECEIPT_URL`、`MEMBER_REFUNDS_URL`、`MEMBER_POINTS_ESTIMATE_URL`）：
  - `readMemberOrders(signal?)`：GET /api/member/orders，不带 page 或任何查询参数（服务端默认第 1 页，每页 20 张，按时间倒序）。ok（200，取 `total` 与 `orders`，每项 `MemberOrderSummary` 为 order_number、created_at、status、total_sen，以类型断言读取）、signed_out、failed（其他，含 200 以外的 2xx 与不是 JSON）、network。
  - `readMemberOrder(language, orderNumber, signal?)`：POST /api/member/orders/view，地址只带 lang（`memberOrderViewUrl`，写法同 `lookupOrdersUrl`），请求体只有 order_number。ok（200，`order` 为 `LookupOrder`，没有 csrf_token）、signed_out、not_found、failed（含 422 与不是 JSON）、network。
  - `submitMemberConfirmReceipt(orderNumber, csrfToken, key)`：POST /api/member/orders/confirm-receipt，请求体只有 order_number，请求头带幂等键与 CSRF 令牌。done（200）、closed（409 order_not_confirmable）、signed_out、not_found、csrf（403 csrf_failed）、failed（其他，含 409 fulfilment_frozen 与 idempotency_conflict、422）、network。
  - `submitMemberRefund(orderNumber, csrfToken, key, lines)`：POST /api/member/orders/refunds，请求体只有 order_number 与 lines，请求头带幂等键与 CSRF 令牌。done（201 或 200）、409 按 detail 分为 duplicate、nothing_left、window_closed、not_refundable、conflict（同 `submitRefund`），signed_out、not_found、csrf、failed（含未列出的 409 与 422）、network。
  - `estimateMemberRefundPoints(orderNumber, lines, signal?)`：POST /api/member/orders/refunds/points-estimate，请求体只有 order_number 与 lines，不带 CSRF 令牌与幂等键；响应体用 `parsePointsEstimate` 检查。ok（与三个数并列）、signed_out、not_found、failed（其他，含 409、422、200 以外的 2xx 与响应体不合格）、network。
  - 幂等键由页面沿用 `orderLookup.ts` 的 `receiptKey` 与 `refundKey` 生成，申请行沿用 `refundLines` 的结果。
- [x] 提交之后的去向（照 `runConfirmReceipt` 与 `runRefund`）：
  - `recheckMemberOrder`：用会员详情重读，直到 ok、signed_out 或 not_found，间隔 `RECHECK_DELAY_MS`、可注入 `delay`；中止时返回 null。
  - `runMemberConfirmReceipt` → `MemberConfirmOutcome`：read（200 或 409 order_not_confirmable 后重读到的订单）、signed_out、not_found、csrf（403 后重读到的订单，没有则为空）、error（订单为空时沿用原来的）、retry（网络中断后重读仍为 demo_shipped，带同一幂等键）。
  - `runMemberRefund` → `MemberRefundOutcome`：submitted、leave、signed_out、not_found、refused（带重读到的订单或空）、csrf（同上）、error（含 idempotency_conflict 后重读）、retry（网络中断后申请记录没有多一笔，带同一幂等键与同一请求行）。
  - 结果里带的是 `LookupOrder`（令牌不随订单返回，csrf 由页面重读会员会话取得新令牌后提示重试）；页面已离开（中止）时返回 null。
- [x] 订单号交接：`handOffMemberOrder(orderNumber)`、`memberOrderInView()`、`clearMemberOrder()`，只在模块变量里；读取不清除，交出新值时覆盖，退出登录时由页面调用清除；整页刷新即消失（HANDOFF 0.61）。
- [x] 测试 `frontend/src/api/memberOrders.test.ts`（vitest，fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，地址从 `mock.calls` 取时用 `String(…)`；等待以注入的 `delay` 代替）：
  - 五个请求的地址、方法、同源 cookie、no-store、signal（列表、详情、预估）、请求头（写操作恰有 Idempotency-Key 与 X-CSRF-Token，列表只有 Accept，详情与预估恰为 Accept 与 Content-Type）、请求体只有约定字段；只有详情带 lang，其余地址不带查询参数与订单号；列表 GET 没有请求体。
  - 各接口状态码与错误码的归类：401 member_session_required、404 order_not_found（另有 detail 不是 order_not_found 的 404 归 failed）、403 csrf_failed、409 各错误码与未列出的 409、422、500、200 以外的 2xx、不是 JSON、预估响应体不合格与网络中断。
  - `runMemberConfirmReceipt`：done 与 closed 改用会员详情重读、重读失败为 error、csrf 带订单与为空、提交与重读的 signed_out 与 not_found、fulfilment_frozen 不重读、网络中断后 retry 并以 `receiptKey` 沿用同一键、重读到新状态、重读中断按 `RECHECK_DELAY_MS` 间隔重读、中止返回 null。
  - `runMemberRefund`：submitted 与 leave 不重读、三种 refused 与 conflict 改用会员详情重读、csrf 带订单与为空、signed_out 与 not_found（提交与重读）、网络中断后 retry 并以 `refundKey` 沿用同一键与同一请求体、申请记录多一笔算 submitted、重读中断按间隔重读、中止返回 null（提交前已中止与重读中中止）。
  - 交接：交出后可读取、读取两次不清除、不写 localStorage、sessionStorage 与 history（以替身代替，测试在 node 环境运行）；覆盖与清除。
  - 每条测试的注释写明守住的接口记录、UX 的哪一句或 DESIGN 与 HANDOFF 的哪一条。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 401、404、403 按状态码与错误码一起判定（401 须为 member_session_required、404 须为 order_not_found、403 须为 csrf_failed），错误码不符的归 failed；后端这三个状态码目前只发这三个错误码，这样做是为了不把路由不存在之类的 404 当成订单不存在。查单版只看状态码。
  - csrf 只出现在两个写操作；列表、详情与预估的 403 归 failed。
  - 确认收货的 done 或 closed 之后重读失败（非 signed_out、not_found）为 error、订单为空，与查单版相同；csrf 之后重读失败为 csrf、订单为空。
  - 写操作的两个提交函数与查单版一样不接收 signal；中止只在两个 run 函数里检查。
  - 交接的读取函数取名 `memberOrderInView`（「正在查看的会员订单号」），与查单版 `submittedRefundOrder` 的命名方式相同。
- 页面留给后续任务：P13「我的订单」页、P09 与 P10 的会员模式由 SHOP-TASK-152、154、155 接入；本任务未做页面，未改路由与页头，未改 `orderLookup.ts` 与 `pay.ts`。
- 改动只在 `frontend/src/api/memberOrders.ts`、`frontend/src/api/memberOrders.test.ts` 与本文件；未改后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、`app/api/account_orders.py` 与 `app/api/member_auth.py`（401 member_session_required、403 csrf_failed）的处理顺序与错误码自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

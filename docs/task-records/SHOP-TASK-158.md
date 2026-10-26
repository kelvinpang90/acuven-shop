# SHOP-TASK-158 会员支付的请求模块与当前订单的标签页暂存

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 5 条（会员仅能访问自己认领或下单的订单；支付、取消等写操作另须 CSRF 令牌）、第 7 条（会员支付页与结果页的当前订单：只存订单号这一个值，只有会员流程写入，写入与清除的时机，不进地址、localStorage、cookie）与「Stripe 测试环境支付」第 3、4 条，以及 HANDOFF 0.63（运营者落实写法第（2）项：会员支付请求放新文件 `memberPay.ts`，支付订单类型补券、积分三个快照字段，不加商品行；暂存读取先取模块变量再取 sessionStorage，sessionStorage 不可用时当作没有暂存）。依据 docs/UX.md 0.13 的 P06「会员的当前订单」与 P07（会员凭会话；成功后显示本单获得积分）。接口为 `app/api/member_pay.py` 的五个会员支付接口。

- [x] `frontend/src/api/pay.ts` 的 `PayOrder` 另有三个必填字段 `coupon_discount_sen`（整数仙）、`points_redeemed`（积分数，1 积分抵 1 仙）与 `points_earned`（积分数），都是非负整数、取订单快照，游客订单都为 0（照 `app/services/payment.py` 的 `PayOrder` 与 `app/models/order.py`）；照本文件现有写法以类型断言读取，字段旁注释写明取值。另把内部函数 `conflictDetail`、`postJson`、`isHttpsUrl`、`replyObject` 与 `isConfirmResult` 改为导出，签名与行为不变；其余类型、函数与行为未改。
- [x] `frontend/src/api/pay.test.ts`、`frontend/src/pages/PayPage.test.tsx` 与 `frontend/src/pages/PayResultPage.test.tsx` 的 `payOrder` 样例函数只补三个字段（都为 0），现有断言未改。
- [x] 新文件 `frontend/src/api/memberPay.ts`，写法沿用 `pay.ts`（请求经 `pay.ts` 的 `postJson`：同源 cookie、`cache: "no-store"`、Accept 与 Content-Type 为 `application/json`；409 与 503 的 detail 经 `conflictDetail` 读取）。五个请求：
  - 读取 `readMemberPayOrder`：`POST /api/member/pay/order?lang=…`（`memberPayOrderUrl`，写法同 `payOrdersUrl`），请求体只有 `order_number`，不带 CSRF 令牌与幂等键，接收 AbortSignal。归类：ok（200，订单为 `PayOrder`，没有 CSRF 令牌）、signed_out（401）、not_found（404 `order_not_found`）、failed（其他，含其他 404、200 以外的 2xx、200 而不是 JSON 与 5xx）、network。
  - 支付 `submitMemberPayment`：`POST /api/member/pay/attempts`，请求体只有 `order_number`、`method` 与 `result`，请求头带 `Idempotency-Key`（调用方以 `pay.ts` 的 `attemptFor` 取得）与 `X-CSRF-Token`。
  - 取消 `submitMemberCancel`：`POST /api/member/pay/cancel`，请求体只有 `order_number`，请求头只带 `X-CSRF-Token`。
  - 两个写操作的回答 `MemberWriteReply` 照 `pay.ts` 的 `WriteReply`：done（2xx）、closed（409 `order_expired`、`order_not_payable`、`order_not_cancellable`）、method_changed（409）、unavailable（503 `stripe_unavailable`）、csrf（403）、failed（其他，含 409 `idempotency_conflict` 与其他 503）、network；expired 换成 signed_out（401），另有 not_found（404 `order_not_found`，其他 404 归 failed）。
  - Stripe 发起 `startMemberStripe`：`POST /api/member/pay/stripe/start?lang=…`（`memberStripeStartUrl`），确认 `confirmMemberStripe`：`POST /api/member/pay/stripe/confirm`（不带查询参数）；两者请求体只有 `order_number`、请求头带 `X-CSRF-Token`，成功响应与结果映射照 `startStripe` 与 `confirmStripe`（只跳转到 https 地址；确认只认 paid、confirming、not_paid、refunded），401 归 signed_out，另有 not_found。
  - 订单号只在请求体里，CSRF 令牌只在写操作的请求头里，都不进地址。
- [x] 写操作之后的去向 `runMemberPayment` 与 `runMemberCancel`（结果类型 `MemberWriteOutcome`，选项沿用 `pay.ts` 的 `WriteOptions`）：
  - done、closed 为 result；signed_out、not_found、method_changed、unavailable 各自成为结果；failed 为 error。
  - csrf：以本模块的会员读取重读该单；重读到 signed_out 或 not_found 各自报告，订单已不再待支付为 result，否则归单独的 `{ kind: "csrf", order }`（order 为重读到的订单，读取失败或中断时为空），由页面重读会员会话取得新令牌后提示重试（同 `memberOrders.ts` 的会员版提交去向）。
  - network：调用 `onChecking`，以 `confirmMemberPayOrder` 照 `confirmOrder` 写法重读到 ok、signed_out 或 not_found 为止（失败与中断隔 `RECHECK_DELAY_MS`，等待可注入）；仍待支付为 `{ kind: "retry", order, attempt }`（支付带原来的 attempt，以便 `attemptFor` 沿用同一幂等键；取消为 null），不再待支付为 result。
  - 页面已离开（中止）时返回 null。
- [x] 当前订单暂存（`MEMBER_PAY_ORDER_KEY`、`stashMemberPayOrder`、`stashedMemberPayOrder`、`clearMemberPayOrder`）：写入同时记在模块变量与本标签页 sessionStorage 的固定键 `acuven-shop.member-pay-order`（不含订单号，只存订单号这一个值）；读取先取模块变量，没有再取 sessionStorage，读取不清除；清除同时清两处；sessionStorage 不存在或读写抛错时以 try/catch 当作没有暂存，写入与清除静默跳过。不写 localStorage、cookie、地址与历史状态。游客的函数与流程不读也不写它（`pay.ts` 未引用本模块）。写入与清除的时机由页面决定。
- [x] 测试 `frontend/src/api/memberPay.test.ts` 新增（vitest，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，地址以 `String(…)` 从 `mock.calls` 取；等待与 sessionStorage、localStorage、cookie、history 以替身代替）：
  - 读取：地址带 `lang`、POST、同源 cookie、no-store、带 signal、请求头只有 Accept 与 Content-Type、请求体只有订单号；401、404 两种、422、500、503、201、200 而不是 JSON 与网络中断的归类。
  - 支付与取消：地址不带查询参数，支付的请求头恰好为 JSON 两项加幂等键与 CSRF 令牌、取消只加 CSRF 令牌，请求体只有约定字段；201、200、401、403、404 两种、409 六种、503 两种、422、500 与网络中断的归类（两个接口各一遍）。
  - 写操作去向：不需重读的各回答（result、signed_out、not_found、method_changed、unavailable、error）且只发一次请求；csrf 后以会员读取重读（不带令牌）得到带订单或为空的 csrf，重读到 401、404 与已支付的去向；网络中断后 `onChecking`、仍待支付时 retry 并以同一幂等键与请求体重试，取消的 retry 不带 attempt；重读到已支付、已取消、401、404 的去向；中断与 5xx 时隔 `RECHECK_DELAY_MS` 再读；写请求返回时已中止、确认中途中止都返回 null。
  - Stripe 发起：地址带 `lang`、请求头与请求体、signal；redirect、complete、409 三种、503 `stripe_unavailable`、401、403、404 与网络中断的归类；非 https 地址、内容不合格、其他 404、409、503 与其他状态归 failed。
  - Stripe 确认：地址不带查询参数、请求头与请求体、signal；四种结果、401、403、404、不合格结果与其他状态、网络中断的归类。
  - 暂存：写入只有一次 `setItem`（固定键、值为订单号，键名不含订单号），读取不清除；模块变量在时不读 sessionStorage，模块重新载入后从 sessionStorage 读到，再次写入覆盖两处；清除两处；sessionStorage 不存在与读写抛错时退化为只在内存且不抛错；暂存与五个请求（含网络中断后的确认与重试）全程只写 sessionStorage 的那一个键，不写 localStorage、cookie 与历史状态，请求地址里没有订单号、电话、令牌或幂等键；已有暂存时游客的读取、支付与取消不读、不写也不清 sessionStorage。
  - 每条测试的注释写明守住的是接口记录、UX、DESIGN 1.14、HANDOFF 0.63 或本任务验收的哪一句。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 不加 `lines`：P06、P07 的视觉稿与 UX 都不显示商品行（HANDOFF 0.63 第（2）项）；后端照样返回 `lines`，前端类型不声明它。
  - 会员接口另立新文件、不并入 `pay.ts`：`pay.ts` 的游客流程与 `pay.test.ts`「browser storage and addresses」（游客不写任何浏览器存储）照常成立，游客代码不引用暂存；`pay.ts` 只补字段与导出现有内部函数。
  - `pay.ts` 的 `CLOSED_DETAILS` 与 `START_CONFLICTS` 是常量而非函数，验收只允许导出内部函数，故本模块另写了相同内容的两份（Stripe 发起的那份以会员结果类型声明）。
  - 401 只按状态码归 signed_out（同 `pay.ts` 与 `memberCheckout.ts`），不核对 detail；404 要求 detail 为 `order_not_found`，其他 404 归 failed（同 `memberOrders.ts`）。403 不论 detail 都归 csrf（同 `pay.ts`）。
  - csrf 的去向保留游客版「重读到订单已不再待支付时转结果页」这一分支，只把游客版带新令牌的 error 换成单独的 csrf 结果；因此会员版的 error 只来自 failed，不带订单（页面沿用原来的）。
  - 读取 sessionStorage 时空字符串当作没有暂存；从 sessionStorage 读到的值不回写到模块变量。
- 页面（P06 与 P07 会员模式的读取、写入与清除暂存、获得积分的显示、P09 继续支付、退出与注销时清除暂存等）不在本任务，留给 SHOP-TASK-159、161 至 163 与 167。
- 改动只在 `frontend/src/api/pay.ts`、`frontend/src/api/pay.test.ts`、`frontend/src/api/memberPay.ts`、`frontend/src/api/memberPay.test.ts`、`frontend/src/pages/PayPage.test.tsx`、`frontend/src/pages/PayResultPage.test.tsx` 与本文件；未改 `PayPage.tsx`、`PayResultPage.tsx`、`memberOrders.ts`、`memberSession.ts`、路由与页头、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.14 与 `app/api/member_pay.py`、`app/services/payment.py` 的请求模型与错误码自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

# SHOP-TASK-157 会员结账的请求模块（会员计价、会员下单与取本人手机号）

设计闸门：DESIGN 1.14（提交 00f8f34）「计价、优惠、积分与库存」第 1 条（券先抵商品额、积分再抵剩余商品额，均不抵运费）、「失败、并发与重试」第 1 条（下单幂等）与「权限与资料保护」第 1、2、8 条（会员收货电话默认会员手机号、可改、按收货国家补默认区号；取本人号码的接口与前台限制），以及 HANDOFF 0.63（本人号码只返回给调用方，模块不缓存、不写任何浏览器存储）。依据 docs/UX.md 0.13 的 P05 第 3 步会员表单（优惠券、使用积分、摘要的券与积分两行；收货电话预填会员手机号）。接口为 `app/api/member_orders.py`（SHOP-TASK-088）的 `POST /api/member/checkout/quote`、`POST /api/member/orders` 与 SHOP-TASK-156 的 `GET /api/member/phone`。

- [x] 新文件 `frontend/src/api/memberCheckout.ts`，写法沿用 `api/orders.ts`、`api/checkout.ts` 与 `api/memberOrders.ts`：三个请求共用一个 `send`（同源 cookie、`cache: "no-store"`，POST 的 Accept 与 Content-Type 为 `application/json`，GET 只有 Accept）；`IDEMPOTENCY_HEADER`、`CSRF_HEADER`、`RECHECK_DELAY_MS` 与 `wait` 取自 `api/pay.ts`，购物车行取自 `checkout.ts` 的 `checkoutQuoteBody`，收货资料类型 `GuestRecipient` 与幂等记录类型 `OrderAttempt` 取自 `orders.ts`（只引用类型）。模块不写日志，不读写任何浏览器存储，不缓存任何结果。
- [x] 会员计价（`memberQuoteUrl`、`memberQuoteBody`、`fetchMemberQuote`）：
  - 地址 `/api/member/checkout/quote?lang=…`（写法同 `checkout.ts` 的 `quoteUrl`）；请求体为 `checkoutQuoteBody` 的结果另加 `points`，券代码不为 null 时另加 `coupon_code`；不带 CSRF 令牌与幂等键；接收 AbortSignal。
  - 计价类型 `MemberQuote` 为 `DestinationQuote` 另加 `coupon_applied`（布尔或 null）、`coupon_discount_sen`、`points_available`、`points_max`、`points_accepted`、`points_redeemed`、`points_earned`，照 `checkout.ts` 以类型断言读取。
  - 归类：ok（200）、signed_out（401）、failed（其他，含 200 以外的 2xx、200 而不是 JSON、413、415、422、429 与 503）、network。
- [x] 会员下单（`memberOrderBody`、`memberOrderAttemptFor`、`submitMemberOrder`）：
  - 请求体只有 `lines`、`phone`（原文）、`name`、`address`、`postal_code`、`country_code`，马来西亚另有 `state_code`，其他国家地区去掉首尾空白后非空时另有 `region`（规则同 `guestOrderBody`），券代码不为 null 时另有 `coupon_code`，以及 `points`；没有 `phone_region`。
  - 地址 `/api/member/orders`，不带查询参数；请求头带 `Idempotency-Key` 与调用方给出的 `X-CSRF-Token`。
  - `memberOrderAttemptFor` 是接收会员请求体的 `orderAttemptFor`：请求体不变时沿用同一个键，变了新生成（默认 `crypto.randomUUID`）。
  - 归类：placed（201 或 200，且 `order_number` 为非空字符串、`points_earned` 为非负整数，取成 `orderNumber` 与 `pointsEarned`；不合格归 failed）、409 按 detail 分为 not_placeable、coupon_invalid、points_unavailable、conflict（其他 409 归 failed）、signed_out（401）、csrf（403 `csrf_failed`，其他 403 归 failed）、phone_invalid（422 且出错列表含 `phone_invalid`，判断同 `orders.ts`）、unavailable（503）、failed（其他，含无效目的地的 422）、network。
- [x] 网络中断后的重试（`runMemberOrder`，照 `runGuestOrder`）：网络中断时调用 `onChecking`，隔 `RECHECK_DELAY_MS`（等待可注入）以同一幂等键、同一令牌与同一请求体重试，直到得到接口的回答；中止时返回 null。
- [x] 取本人号码（`parseMemberPhone`、`readMemberPhone`）：`GET /api/member/phone`，不带查询参数、请求体与 CSRF 令牌，接收 AbortSignal；归类 ok（200 且 `phone` 为以加号开头的非空字符串）、signed_out（401）、failed（其他，含 200 而响应体不合格）、network。响应体检查为导出的纯函数 `parseMemberPhone`。号码只返回给调用方，每次调用都重新请求。
- [x] 测试 `frontend/src/api/memberCheckout.test.ts` 新增（vitest，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，地址以 `String(…)` 从 `mock.calls` 取；等待以替身代替）：
  - 计价：请求体另加 `points`、给了券才带 `coupon_code`、不带金额；地址带 `lang`、POST、同源 cookie、no-store、带 signal、请求头只有 Accept 与 Content-Type（没有 CSRF 与幂等键）；401、413、415、422、429、503、201、200 而不是 JSON 与网络中断的归类。
  - 下单请求体：字段恰好为会员下单模型的字段、没有 `phone_region`；马来西亚带州属，空白地区与没有券时不带。
  - 幂等键：请求体不变沿用，改电话、收货资料、券、积分或件数都换新；默认用 `crypto.randomUUID`。
  - 下单：地址、方法、同源 cookie、no-store，请求头带 `Idempotency-Key` 与 `X-CSRF-Token`，请求体里没有键与令牌；201、200 与六种不合格响应体，409 四种与其他，401，403 两种，422 三种，503，500 与网络中断的归类。
  - 重试：两次网络中断后以同一键、令牌与请求体重试，`onChecking` 两次，等待 `RECHECK_DELAY_MS`；中止返回 null 且不再请求；得到回答即返回。
  - 取号：地址无查询参数、GET、同源 cookie、no-store、带 signal、没有请求体、请求头只有 Accept；401、四种不合格的 200、202、503 与网络中断的归类；`parseMemberPhone` 的合格与不合格输入。
  - 浏览器存储：取号两次、计价与下单（含一次网络中断后的重试）全程不写 localStorage、sessionStorage 与 cookie，每次取号都发出请求，请求地址里没有电话、收货资料、券代码、令牌或幂等键。
  - 每条测试的注释写明守住的是接口记录、UX P05、DESIGN 1.14、HANDOFF 0.63 或本任务验收的哪一句。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 三个接口的 401 只按状态码归 signed_out，不再核对 detail 是否为 `member_session_required`（取号的验收写的是「signed_out（401）」，三个接口取同一种写法；后端的 401 只有这一个错误码）。这与 `memberOrders.ts` 核对错误码的写法不同。
  - 券代码「调用方给出」按不为 null 判断：空字符串照原样送出（后端不把空白当作省略，会回 422 归 failed），由页面决定输入为空时传 null。
  - 积分数不在本模块校验是否为非负整数，原样放进请求体，由服务端校验（不合格 422 归 failed）。
  - `parseMemberPhone` 另要求号码不只是一个加号（长度大于 1）。
  - `orders.ts` 的 `hasPhoneInvalid` 没有导出，本模块另写了一份相同的判断（不改 `orders.ts`）。
- 页面（P05 第 3 步会员表单、摘要的券与积分两行、收货电话预填）不在本任务，留给 SHOP-TASK-159。
- 改动只在 `frontend/src/api/memberCheckout.ts`、`frontend/src/api/memberCheckout.test.ts` 与本文件；未改 `checkout.ts`、`orders.ts`、`pay.ts`、页面、路由与页头、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.14 与 `app/api/member_orders.py`、`app/api/member_auth.py` 的请求模型与错误码自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

## 登记时的验收标准（原文）

2026-10-29 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 frontend/src/api/memberCheckout.ts，写法沿用 frontend/src/api/orders.ts 与 frontend/src/api/checkout.ts（同源 cookie——会员接口凭会话 cookie，不同于游客计价的 credentials omit；cache 为 no-store，Accept 与 Content-Type 为 application/json；IDEMPOTENCY_HEADER、CSRF_HEADER、RECHECK_DELAY_MS 与 wait 取自 frontend/src/api/pay.ts；购物车行沿用 checkout.ts 的 checkoutQuoteBody）；只有计价带 lang 查询参数（写法同 checkout.ts 的 quoteUrl），其余不带查询参数；三个接口的 401（member_session_required）都归 signed_out；电话、收货资料、券代码与 CSRF 令牌只在内存、请求体与请求头里，不进地址、日志或浏览器存储
      - 会员计价——请求体为 checkoutQuoteBody 的结果另加 points（非负整数）与 coupon_code（只在调用方给出代码时带），不带 CSRF 令牌与幂等键，接收 AbortSignal；结果归为 ok（200，计价类型为 checkout.ts 的 DestinationQuote 另加 coupon_applied（布尔或 null）、coupon_discount_sen、points_available、points_max、points_accepted、points_redeemed 与 points_earned，照 checkout.ts 以类型断言读取，不是 JSON 归 failed）、signed_out、failed（其他，含 413、415、422、429 与 503）与 network
      - 会员下单——请求体只有 lines、phone（会员填写的收货电话原文，没有 phone_region，服务端按收货国家补默认区号）、name、address、postal_code、country_code，马来西亚另有 state_code，其他国家的地区去掉首尾空白后非空时另有 region（规则同 orders.ts 的 guestOrderBody），coupon_code（只在调用方给出时带）与 points；请求头带幂等键与调用方给出的 CSRF 令牌；幂等键规则同 orders.ts 的 orderAttemptFor（请求体不变时沿用同一个键，变了新生成；可另写接收会员请求体的版本）；结果归为 placed（201 新单或 200 重放，且响应体 order_number 为非空字符串、points_earned 为非负整数，取成 orderNumber 与 pointsEarned；不合格归 failed）、not_placeable（409 order_not_placeable）、coupon_invalid（409 coupon_invalid）、points_unavailable（409 points_unavailable）、conflict（409 idempotency_conflict）、signed_out、csrf（403 csrf_failed）、phone_invalid（422 且出错类型含 phone_invalid，判断同 orders.ts）、unavailable（503）、failed（其他，含无效目的地的 422）与 network
      - 网络中断后的重试——照 orders.ts 的 runGuestOrder 写会员版：网络中断时隔 RECHECK_DELAY_MS 以同一幂等键与同一请求体重试，直到得到接口的回答（上一次若已建单，服务端按重放返回原订单）；网络中断、开始等待时调用 onChecking；间隔可注入；页面已离开（中止）时返回 null
      - 取本人号码——GET /api/member/phone，不带查询参数与 CSRF 令牌，接收 AbortSignal；结果归为 ok（200 且 phone 为以加号开头的非空字符串，取成 phone）、signed_out（401）、failed（其他，含 200 而响应体不合格）与 network；响应体的检查写成导出的纯函数；号码只返回给调用方，模块不缓存、不写任何浏览器存储（DESIGN 1.14 第 8 条）
      - 用 vitest（不加测试依赖，fetch 与等待以替身代替）在 frontend/src/api/memberCheckout.test.ts 验证意图——三个请求的地址、方法、请求头与请求体（计价不带 CSRF 与幂等键、带 lang；下单带 Idempotency-Key 与 X-CSRF-Token、没有 phone_region，没有券时不带 coupon_code；取号不带查询参数与 CSRF）；各状态码与错误码的归类（含 401、403、409 各码、422 的 phone_invalid 与其他、503、200 而响应体不合格）与网络中断；会员版下单在网络中断后以同一键重试、中止返回 null；整个过程不写 localStorage、sessionStorage 与 cookie；每条测试写明它守住的是哪个接口记录、UX 或 DESIGN 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-157.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；三个请求、归类、重试、偏离、验证到什么程度，只写实际存在的测试，并写明页面留给 SHOP-TASK-159）
      - 改动只落在 allowed_change_paths（不改 checkout.ts、orders.ts 与 pay.ts，只引用它们导出的内容；不做页面，不改路由与页头）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.14 提交 00f8f34 与 HANDOFF 0.63），不合并、不直推
```

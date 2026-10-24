# SHOP-TASK-149 查单请求模块补会员下单的券与积分字段，新增游客积分预估请求

设计闸门：DESIGN 1.13（提交 450c2fa）「权限与资料保护」第 5 条（访客仅能访问已通过查询验证的订单）与「计价、优惠、积分与库存」第 3、5、7 条（100 积分抵 RM1；退款时返还未过期的抵扣积分、追回获得积分；已有订单按快照）与 HANDOFF 0.61。依据 docs/UX.md 0.13 的 P09（M3）与 P10（M3）。会员前端第二批的请求层之一，供 SHOP-TASK-150、154、155 使用。

- [x] `frontend/src/api/orderLookup.ts` 的 `LookupOrder` 加三个必填字段，取值照后端 `app/services/order_lookup.py` 的 `LookupOrder`（SHOP-TASK-103），字段旁注释写明：
  - `placed_by_member`（布尔）：下单时是会员为真（后端以 claim_status 为 not_claimable 判定，会员注销后仍为真）；游客订单与后来被认领的游客订单为假。
  - `coupon_discount_sen`（非负整数仙）：下单时的优惠券抵扣快照。
  - `points_redeemed`（非负整数）：下单时抵扣的积分数；100 积分抵 RM1，即 1 积分抵 1 仙，这个数同时是抵扣金额的仙数。
  - 仍以类型断言读取订单响应，不另加解析器。文件开头与类型上方「不含优惠券与积分两项」的说法改为：这两项是下单时的快照，只在下单时是会员的订单显示；退款积分另由预估接口给出。其余类型、函数与行为未改。
- [x] 游客积分预估 `estimateRefundPoints(orderNumber, lines, signal?)`：调用 `POST /api/orders/refunds/points-estimate`（导出常量 `POINTS_ESTIMATE_URL`，SHOP-TASK-093），写法照 `submitRefund`——同源 cookie、cache 为 no-store、Accept 与 Content-Type 为 application/json、不带查询参数；请求体只有 `order_number` 与 `lines`（`refundLines` 的结果），不带 CSRF 令牌与幂等键；另把 signal 传给 fetch。结果 `PointsEstimateRead`：
  - ok：200 且响应体合格，取成 `memberOrder`、`pointsBack`、`pointsReversed`（与 `kind` 并列在同一对象里）；
  - expired：401；
  - failed：其他，含 409 order_not_refundable、refund_window_closed、refund_nothing_left，422，200 以外的 2xx，以及 200 而响应体不合格或不是 JSON；
  - network：网络中断。
  - 响应体检查为导出的纯函数 `parsePointsEstimate(body: unknown): PointsEstimate | null`：`member_order` 为布尔、`points_back` 与 `points_reversed` 为非负整数时取出，否则为 null，供 SHOP-TASK-150 的会员预估复用。
- [x] 显示判断 `showsRefundPoints(estimate)`：`memberOrder` 为真且 `pointsBack` 与 `pointsReversed` 不全为 0 时为真，其余为假（UX P10「积分行只在该单属于会员（下单时为会员）时显示；由会员认领的游客订单…服务端返回零时不显示积分行」），供 SHOP-TASK-155 的两种模式共用。
- [x] 测试：
  - `frontend/src/api/orderLookup.test.ts`、`frontend/src/pages/TrackOrderPage.test.tsx`、`frontend/src/pages/RefundPage.test.tsx`：构造 `LookupOrder` 样例的 `lookupOrder` 只补上三个新字段（`placed_by_member` 为假、两个数为 0，即游客订单），现有断言未改。
  - `frontend/src/api/orderLookup.test.ts` 新增「refund points estimate」一组（vitest，fetch 以替身代替）：请求的地址、方法、同源 cookie、no-store、signal、请求头恰为 Accept 与 Content-Type（没有 X-CSRF-Token 与 Idempotency-Key）、地址不带查询参数与订单号、请求体只有 order_number 与 lines（这一条的替身为参数带类型的 `vi.fn((input, init) => …)`，从 `mock.calls` 取地址时用 `String(…)`）；`refundLines` 的结果原样作为 lines；401、409 三个错误码、422、413、500、201、204 与网络中断的归类；200 而响应体不合格（缺字段、负数、小数、字符串数字、member_order 不是布尔、null、不是 JSON）归 failed；`parsePointsEstimate` 的取出与 null；`showsRefundPoints` 的游客订单、认领单的两个 0、只有一个数非零与两个都非零。每条测试的注释写明守住的接口记录或 UX 的哪一句。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 后端预估以订单的 member_id 判定 member_order，被认领的游客订单也为真但两个数为 0；因此显示判断另要求两个数不全为 0，与 UX P10 的说明一致。`PointsEstimate` 的注释照此写明 memberOrder 含被认领的游客订单。
  - ok 的结果把三个数与 `kind` 放在同一对象里（`{ kind: "ok" } & PointsEstimate`），可直接交给 `showsRefundPoints`。
  - 只有 200 算预估成功；200 以外的 2xx 不是约定的回答，归 failed（与 `submitConfirmReceipt` 只认 200 的写法一致）。
  - signal 为可选参数（与 `readLookupOrder`、`submitLookup` 相同）。
- 页面显示留给后续任务：P09 的优惠与积分两行由 SHOP-TASK-154 接入，P10 的积分返还与追回由 SHOP-TASK-155 接入；本任务未改 `TrackOrderPage.tsx`、`RefundPage.tsx`、路由与页头。
- 改动只在 `frontend/src/api/orderLookup.ts`、`frontend/src/api/orderLookup.test.ts`、`frontend/src/pages/TrackOrderPage.test.tsx`、`frontend/src/pages/RefundPage.test.tsx` 与本文件；未改后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、docs/UX.md 0.13 的 P09 与 P10、后端 `app/services/order_lookup.py` 的 `LookupOrder` 与 `app/api/refunds.py` 的 `PointsEstimateOut` 及预估处理顺序自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

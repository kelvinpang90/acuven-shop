# SHOP-TASK-118 现有支付与取消接口接入 Stripe 适配器并归类新异常

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 2 条（订单有不是 expired 的 Stripe 记录时不接受模拟支付）、第 5 条（有 Stripe 记录的订单取消一律先处理会话；Stripe 不可用时本人取消提示稍后再试）与第 9 条（CI 与本地测试用模拟的 Stripe 适配器），以及 `docs/HANDOFF.md` 0.54 的落实写法（Stripe 不可用时接口回 503 `stripe_unavailable`，模拟支付遇到 Stripe 途径回 409 `method_changed`），让 `app/api/pay.py` 与 `app/api/member_pay.py` 现有的取消接口传入 Stripe 适配器，并把 SHOP-TASK-114 新增的两个异常归类为接口错误。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.54。不加接口，未改服务层、模型、迁移与设计。
- 适配器的传入：`app/api/pay.py` 新增依赖别名 `GatewayDep`（`Annotated[StripeGateway, Depends(get_stripe_gateway)]`，取自 `app/services/stripe_gateway.py` 现有的依赖），POST `/api/pay/cancel` 与 POST `/api/member/pay/cancel` 以它取得适配器并作为 `gateway` 传给 `cancel_order`。依赖写在请求体与会话依赖之后，413、415、422 与授权的检查顺序不变；`get_stripe_gateway` 不读写数据库、不调用 Stripe，没有 Stripe 记录的订单取消时适配器不被调用。两个模拟支付接口（POST `/api/pay/attempts` 与 POST `/api/member/pay/attempts`）不取适配器，对 `submit_payment` 的调用不变。请求体与成功响应不变。
- 两个新错误码：
  - `cancel_order` 抛 `StripeUnavailable` 时回 503 `{"detail": "stripe_unavailable"}`（订单保持待支付、库存继续预留，由服务层保证）。助手 `_stripe_unavailable()` 写在 `app/api/pay.py`，会员接口沿用（与 `_conflict` 一样）。
  - `submit_payment` 抛 `PaymentPathMismatch` 时回 409 `{"detail": "method_changed"}`，不带 `status`（异常不带订单状态；服务层先于写支付尝试抛出）。
  - 游客接口的这两种响应与现有 401、403、409 一样以响应头带 no-store；会员接口另由路由器已有的 `_NoStoreRoute` 加上。取消时会话已付款（服务层确认为 demo_paid 后抛 `OrderNotCancellable`）照现有映射回 409 `order_not_cancellable`，`status` 为 demo_paid。
  - 接口不写日志；服务层按设计记的 error 日志不变。两个模块的说明文档补了这几条。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - `method_changed` 的 409 不带 `status`：现有 409 里只有 `idempotency_conflict` 不带，`PaymentPathMismatch` 与它一样不携带订单状态，订单此时仍待支付，页面按 UX P06 重新读取订单。
  - 会员取消接口用的依赖别名与助手从 `app/api/pay.py` 导入，沿用该模块已从 `pay.py` 导入 `_conflict`、`CancelDep` 等的写法，没有另写一份。
  - 测试里的会员订单不带券与积分预占（只验证适配器的传入与错误归类；券与积分的释放已由 `tests/test_stripe_cancel.py` 与 `tests/test_member_pay_api.py` 覆盖）。
- 留给之后的任务：支付页的 Stripe 发起接口（创建会话、按途径返回跳转地址、转人工时的 409 `stripe_under_review`）与确认接口（结果页的 paid、confirming、not_paid、refunded），留给 HANDOFF 0.54 所列第二批里之后的支付页 Stripe 接口任务；webhook 接口与后台退款详情及重试接口按 HANDOFF 0.54 在退款规则合并后登记。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 2、5、9 条与 HANDOFF 0.54 的落实写法自查，并核对 `app/services/payment.py` 中 `cancel_order`、`submit_payment` 的异常与 `app/api/order_lookup.py` 的 `_NoStoreRoute`。`tests/test_demo_payment.py` 与 `tests/test_member_pay_api.py` 一字未改；人工核对它们的取消请求在没有 Stripe 记录时不会调用适配器，未设置 `app.state.stripe_gateway` 时 `get_stripe_gateway` 按应用配置构造真实适配器但不发请求，查看接口的 OpenAPI 参数（`get_stripe_gateway` 只取 `Request`）不变。新文件 `tests/test_stripe_pay_cancel_api.py` 用 TestClient 与 SQLite 内存库（StaticPool，打开外键检查），订单与会话记录在本文件里按模型写入，以 `app.state.stripe_gateway` 注入 `FakeStripeGateway`，游客与会员各验证：open 会话作废成功后取消 200、记录为 expired、库存释放、事件操作者为本人；Stripe 不可用时取消回 503 `stripe_unavailable`、订单仍待支付、库存仍预留、记录仍为 open、带 no-store、`app.api` 不写日志；作废被拒后查询为已付款时订单变为 demo_paid、回 409 `order_not_cancellable`（status 为 demo_paid）、库存不加回；有 creating、open 或 complete 记录时模拟支付（成功与失败）回 409 `method_changed`、不写支付尝试、订单与库存不变、不调用适配器；没有 Stripe 记录的订单取消成功且适配器的 `calls` 为空。每条测试的文档字符串写明它守住的设计原句、HANDOFF 0.54 的写法或验收条目。测试交由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；未连真实的 Stripe 测试环境。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - POST /api/pay/cancel 与 POST /api/member/pay/cancel 经 app/services/stripe_gateway.py 现有的依赖 get_stripe_gateway 取得适配器并作为 gateway 传给 cancel_order；两个模拟支付接口（POST /api/pay/attempts 与 POST /api/member/pay/attempts）不传适配器、调用不变；其余检查顺序、请求体与成功响应不变
      - 归类——cancel_order 抛 StripeUnavailable 时回 503 {"detail":"stripe_unavailable"}（订单与库存不变）；submit_payment 抛 PaymentPathMismatch 时回 409 {"detail":"method_changed"}（不写支付尝试）；游客接口的这两种响应与现有 401、403、409 一样带 no-store，会员接口照它的路由器已有的 no-store；取消时会话已付款仍按现有映射回 409 order_not_cancellable（status 为 demo_paid）
      - 测试验证意图——新文件 tests/test_stripe_pay_cancel_api.py 用 TestClient 与 SQLite 内存库（照 tests/test_demo_payment.py 与 tests/test_member_pay_api.py 的夹具写法，Stripe 会话记录的准备照 tests/test_stripe_cancel.py 在本文件里另写，不 import 其他测试文件），以 app.state.stripe_gateway 注入 FakeStripeGateway，游客与会员各验证——open 会话作废成功后取消成功、库存释放；Stripe 不可用时取消回 503 stripe_unavailable 且订单仍待支付、库存仍预留、响应带 no-store；作废时 Stripe 答复会话已付款则订单变为 demo_paid、回 409 order_not_cancellable；有不是 expired 的记录时模拟支付回 409 method_changed、不写支付尝试；没有 Stripe 记录的订单取消不调用适配器（FakeStripeGateway 的 calls 为空）；每条测试写明它守住的是设计的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - 现有 tests/test_demo_payment.py 与 tests/test_member_pay_api.py 一字不改且照常通过；接口不写日志（服务层按设计记的 error 日志除外）
      - docs/task-records/SHOP-TASK-118.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；适配器的传入、两个新错误码、偏离、验证到什么程度，并写明发起与确认接口留给之后的任务），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的文件、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.54），不合并、不直推
```

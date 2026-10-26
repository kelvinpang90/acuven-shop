# SHOP-TASK-119 支付页视图的 Stripe 字段与按订单确认 Stripe 付款

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 2、3、4、6 条、`docs/UX.md` 0.11 的 P06、P07 与 `docs/HANDOFF.md` 0.54 的落实写法，在业务规则层给支付页视图 `PayOrder` 加三个 Stripe 字段，并在 `app/services/stripe_confirm.py` 新增按订单确认 Stripe 付款的 `confirm_order_payment`，供之后的发起与确认接口使用。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.54。不加接口，未改 `app/api/`、模型、迁移与设计。
- 三个字段（`app/services/payment.py`）：
  - `payment_route`：订单为 `awaiting_demo_payment` 时取 SHOP-TASK-112 的 `payment_route`（`stripe` 或 `simulated`；最低收款额与 `submit_payment` 一样取 `get_settings().stripe_min_amount_sen`），其他状态为 null。即 UX P06「服务端给出本单的支付途径」：有不是 expired 的记录一律 stripe；否则开关为 stripe_test 且应付不低于最低额为 stripe，其余为 simulated。
  - `stripe_state`：订单不是 expired 的 Stripe 记录的状态 `creating`、`open`、`complete`；`creating` 且告警时间非空（创建满 24 小时转人工核对）为 `under_review`；没有这样的记录（未发起或只有 expired 记录）为 null。对应 P06 的创建结果未定、转人工核对、有进行中的会话、已有付款完成的会话与未发起。其他状态的告警时间不影响取值，也不看订单状态。
  - `stripe_refunded`：本单任一会话记录有来源为 `checkout` 的退款尝试时为 true，不论尝试状态（P07「不论该尝试当前状态」），也不看订单状态。
  - `pay_orders` 按订单批量读取会话记录（按活动列）与退款尝试（与会话记录联接），支付途径只对待支付订单逐张调用 `payment_route`（它本身只读）。保持只读、不加锁、不调用 Stripe。
- 确认结果（`confirm_order_payment(db, gateway, order_id, now)`，返回 `OrderPaymentResult`）：
  - `paid`：订单支付时间非空时直接返回，不调用 Stripe（第 4 条：重复确认直接返回原结果）；或 `confirm_checkout` 返回 paid。
  - `confirming`：`confirm_checkout` 返回 unavailable 或 needs_review（HANDOFF 0.54：都按「确认中」显示）；或返回 refunded、pending 后重读记录为 complete（会话已完成而付款未定）。
  - `refunded`：订单已不是 `awaiting_demo_payment` 且本单有来源为 `checkout` 的退款尝试（迟到付款，第 6 条），在没有活动记录与确认后重读两处判定。
  - `not_paid`：其余情形——creating 记录（不调用 Stripe）、open 未付款、没有活动记录而订单仍待支付，以及金额或币种不符的会话退款后订单仍待支付（HANDOFF 0.53，可再发起支付）。
  - 自己找记录与重读时只用普通读，调用 `confirm_checkout` 前结束读事务、不持锁；确认的事务与锁沿用 `confirm_checkout`。`confirm_checkout`、`fill_payment_method_type` 与 SHOP-TASK-112 至 117 的其他函数未改。
- P06 打开时本单已有 complete 会话：按 HANDOFF 0.54，由前端调用确认接口完成「服务端先确认」，订单视图保持只读，不在 `pay_orders` 里确认。发起与确认接口留给之后的任务（SHOP-TASK-120 游客、121 会员）。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - `refunded` 与 `stripe_refunded` 都按「本单任一会话记录」的退款尝试判定，不限于这次确认的那条记录。
  - 订单已取消而没有来源为 checkout 的退款尝试时 `confirm_order_payment` 返回 `not_paid`（照验收原文）；结果页对已取消订单本来就按订单状态显示，接口任务可另行处理。
  - `now` 带时区时抛 `ValueError`，先于访问数据库（与 `confirm_checkout` 一致）。
- 测试：`tests/test_demo_payment.py` 与 `tests/test_member_pay_api.py` 只把三个新字段加进 `ORDER_FIELDS` 并写明原因，其余断言一字未改。新文件 `tests/test_stripe_pay_view.py` 用 SQLite 内存库（StaticPool，打开外键检查）与 `FakeStripeGateway`，订单、会话记录与退款尝试按模型写入，验证：三个字段在模拟途径（无设置行与开关为模拟）、Stripe 途径（开关为 stripe_test 且应付不低于最低额含恰好等于；或已有 creating、open、complete 记录，不论开关与金额）、低于最低额、只有 expired 记录、非待支付订单下的取值；creating、带告警时间的 creating、open、complete（含带告警时间）与只有 expired 记录时的 `stripe_state`；迟到付款退款后（pending、succeeded、failed 三种尝试）与不符付款退款后订单仍待支付时 `stripe_refunded` 为真；几张订单一次读取取值不串，期间只发 SELECT、会话里没有待写对象、记录与订单不变。`confirm_order_payment` 的每一种返回：已付款会话为 paid 并进入 demo_paid；重复确认与已付款订单为 paid 且不调用适配器；creating（含转人工）为 not_paid 且不调用适配器、记录不变；Stripe 不可用为 confirming；open 未付款为 not_paid；会话 complete 而付款未定为 confirming；needs_review 为 confirming；迟到付款（订单已取消）为 refunded 并写了 pending 尝试；金额不符而订单仍待支付为 not_paid（写了尝试、记录释放活动位）；没有活动记录时按订单状态与退款尝试为 refunded 或 not_paid、不调用适配器；带时区的 now 抛 ValueError。每条测试的文档字符串写明它守住的设计、UX 或 HANDOFF 原句，只出自验收标准的注明「验收：」。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 2、3、4、6 条、UX 0.11 P06、P07 与 HANDOFF 0.53、0.54 自查，并核对 `confirm_checkout` 各结果下记录与订单的去向（`_decide`、`_settle_late`）。测试交由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；SQLite 不支持行锁，加锁行为只按代码审阅；未连真实的 Stripe 测试环境。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/payment.py 的 PayOrder 增加三个字段——payment_route（订单为 awaiting_demo_payment 时取 app/services/stripe_checkout.py 的 payment_route 的值 stripe 或 simulated，最低收款额与 submit_payment 一样取 get_settings().stripe_min_amount_sen；其他状态为 null）、stripe_state（订单不是 expired 的 Stripe 会话记录的状态 creating、open、complete，creating 且告警时间非空时为 under_review；没有这样的记录为 null）、stripe_refunded（本单任一会话记录有来源为 checkout 的退款尝试时为 true，不论尝试状态）；pay_orders 按订单批量读取，保持只读、不调用 Stripe、不加锁
      - app/services/stripe_confirm.py 新增公开函数 confirm_order_payment（入参数据库会话、适配器、订单 ID、当前时间），返回枚举 OrderPaymentResult 的 paid、confirming、not_paid、refunded 之一——订单的支付时间非空时直接返回 paid，不调用 Stripe（第 4 条重复确认直接返回原结果）；否则订单有不是 expired 的记录且为 open 或 complete 时调用 confirm_checkout（不改它），paid 照传，refunded 与 pending 时重读该记录与订单，记录为 complete 时为 confirming，订单已不是 awaiting_demo_payment 而有来源为 checkout 的退款尝试时为 refunded，其余为 not_paid，unavailable 与 needs_review 为 confirming；记录为 creating 时不调用 Stripe、返回 not_paid；没有这样的记录时，订单已不是 awaiting_demo_payment 且有来源为 checkout 的退款尝试返回 refunded，否则返回 not_paid（金额或币种不符的会话退款后订单仍待支付、可再发起支付，HANDOFF 0.53，所以为 not_paid）
      - 确认的事务与锁沿用 confirm_checkout，confirm_order_payment 自己找记录时只读、不持锁调用 Stripe；不改 confirm_checkout、fill_payment_method_type 与 SHOP-TASK-112 至 117 的其他函数的行为
      - 测试验证意图——新文件 tests/test_stripe_pay_view.py 用 SQLite 内存库与 FakeStripeGateway 验证——三个字段在模拟途径、Stripe 途径（开关为 stripe_test 且应付不低于最低额，或已有不是 expired 的记录）、低于最低额、非待支付订单下的取值；creating、带告警时间的 creating、open、complete 与只有 expired 记录时的 stripe_state；迟到付款退款后 stripe_refunded 为真；confirm_order_payment 的每一种返回（含 creating 不调用适配器、Stripe 不可用为 confirming、open 未付款为 not_paid、complete 而付款未定为 confirming、迟到付款（订单已取消）为 refunded、金额不符而订单仍待支付时为 not_paid、已付款的订单为 paid 且不调用适配器）；视图不写库；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - tests/test_demo_payment.py 与 tests/test_member_pay_api.py 只把三个新字段加进 ORDER_FIELDS 并写明原因，其余断言一字不改
      - docs/task-records/SHOP-TASK-119.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；三个字段与确认结果的含义、偏离、验证到什么程度，并写明 P06 打开时已有 complete 会话由前端调用确认接口、发起与确认接口留给之后的任务），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/api/ 下的文件、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.54），不合并、不直推
```

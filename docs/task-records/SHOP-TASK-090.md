# SHOP-TASK-090 模拟支付成功、取消与超时接入优惠券与积分

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 1、3 条（券与积分在取消或支付超时时释放；模拟支付成功后获得积分）、「订单与退款状态」（待支付订单可由下单者取消或超时为 demo_cancelled，释放库存、券及积分预占）与「失败、并发与重试」第 1 条，以及 `docs/HANDOFF.md` 0.46、0.47 记录的 Kelvin 决定（预占与核销都计用券次数；预占的积分以预占时为准；获得积分按下单时的快照在支付成功时发放），把 SHOP-TASK-085、086 的函数接入 `app/services/payment.py` 的支付成功、本人取消与超时取消，并让支付与取消可以由会员操作；测试见 `tests/test_payment_coupons_points.py`，`tests/test_demo_payment.py` 只把视图字段集合补上三个新字段。不做接口；未改 `app/api/pay.py`、`app/jobs.py`、`app/services/coupons.py`、`app/services/points.py`、模型、迁移、`.platform/`、CI、部署配置与设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.46、0.47（优惠券与积分）。拆分：业务规则层（4 个允许路径）。
- 操作者：`submit_payment` 与 `cancel_order` 增加仅限关键字的 `actor`（默认 `guest`，只接受 `guest` 与 `member`，其他值在访问数据库之前抛 `ValueError`），状态迁移的检查（`is_transition_allowed`）与写入的事件都用它；游客接口不传，行为不变。超时取消（含支付与取消时对已到期订单先做的超时取消）的操作者仍为 `system`。取消方的显示照旧：`system` 为超时，`guest` 与 `member` 为本人。
- 锁顺序（会员 → 订单，与会员下单、注销一致）：支付成功、本人取消与超时取消在带条件的 UPDATE 之前，先以普通读取得订单的 `member_id`，非空时以 `SELECT … FOR UPDATE` 锁住该会员的行（会员已注销也锁），为空（游客订单或会员注销后已清空）时不预先锁；锁后再做现有的带条件 UPDATE，未命中时的回滚与重放处理不变。于是单张订单的下单（会员 → 库存）、取消（会员 → 订单 → 库存 → 用券记录 → 积分）、支付成功（会员 → 订单 → 用券记录 → 积分）与注销（会员 → 订单）都先取会员锁；会员 ID 已清空的订单在取消时由 `release_points` 在库存之后才锁会员行，此时该会员已注销，不会再下单或注销。这依赖 SHOP-TASK-097 之后超时取消逐张订单真实提交（定时任务与每日库存重置都不再在外层事务里连续取消多张订单），不跨订单持有库存与券的锁；模块说明写明了这个锁顺序与这一前提。
- 支付成功：同一事务里、带条件的 UPDATE 命中并写入支付尝试与操作者为 `actor` 的事件之后，调用 `use_coupon`（该单没有用券记录时返回 False，不出错）；再以加锁读（`populate_existing`）重新读取订单的 `member_id`，非空时以加锁读读取会员状态，会员为 active 时调用 `earn_points`，获得积分取订单快照 `points_earned`；会员 ID 为空或会员不是 active 时不发放。重放（同键同请求）在这之前返回，不再核销或发放；支付失败不核销、不发放。
- 本人取消与超时取消：同一事务里、带条件的 UPDATE 命中并加回库存之后，调用 `release_coupon` 与 `release_points`（游客订单没有用券记录与 redeem 流水，两者都不写；批次已过期时 `release_points` 紧接着写等量的 `expire`，会员已注销时写等量的 `forfeit`），再写事件。UPDATE 未命中时整个事务回滚，不释放；重复取消与重复运行超时取消不重复释放。
- 视图：`PayOrder`（`pay_orders`）另给 `coupon_discount_sen`、`points_redeemed` 与 `points_earned`，取订单快照（游客订单的快照都为 0）。游客接口 `GET /api/pay/orders` 的每张订单随之多出这三个字段，其余字段不变。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 快照 `points_earned` 为 0 时不调用 `earn_points`、也不读会员行（结果同样是不写记录）。原因：被认领的游客订单（快照为 0）的 `member_id` 可能在预先锁会员之后才变为非空，这时再锁会员行就成了订单 → 会员的顺序；快照大于 0 的只有会员订单，其会员 ID 只会由非空变空，已在 UPDATE 之前锁过。
  - 核销与发放放在写入支付尝试（及幂等键唯一约束的检查）之后、提交之前；撞上唯一约束时整个事务回滚，与验收的「UPDATE 命中之后、同一事务里」一致。
  - 会员在支付前被注销时券照常核销（`use_coupon` 只按订单找记录），只是不发放积分。
  - `cancel_order` 与 `submit_payment` 对已到期订单先调用的 `expire_overdue_orders` 照旧以 `system` 取消并释放，不用调用方的 `actor`。
- 留给之后的任务：支付页与结果页的会员接口（会员会话、CSRF，以 `actor="member"` 调用 `submit_payment` 与 `cancel_order`，按会员身份列出订单）留给 SHOP-TASK-091；游客支付页的前端是否显示新增的三个字段不在本任务；注销时作废积分余额留给 SHOP-TASK-094。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句与 HANDOFF 0.46、0.47 的 Kelvin 决定自查，并人工核对已有调用方（`app/api/pay.py`、`app/api/order_lookup.py`、`app/jobs.py`、`app/services/stock_reset.py`）与替换 `payment._cancel` 的已有测试都不受新增关键字参数影响。`tests/test_payment_coupons_points.py` 用 SQLite 内存库（StaticPool，每个连接打开外键检查，并断言已打开）按模型建表，会员订单按模型写入，券与积分经 `reserve_coupon`、`reserve_points` 预占，来源批次经 `earn_points`，覆盖：支付成功后用券记录为 `used`、写积分批次与 `earn`（数量为订单快照）、事件操作者为 `member`；快照为 0 时不写批次；支付失败不核销不发放；本人取消与超时取消后用券记录为 `released`、积分 `release` 且可用余额恢复、库存加回；取消时批次已过期则随即记为 `expire`；重放支付、换键再付、重复取消与重复运行超时取消都不重复；会员已注销（`member_id` 已清空）后超时取消时释放的积分随即 `forfeit`；会员在支付前被注销或会员已不是 active 时支付成功也不发放积分；取消在 UPDATE 之前被另一会话的支付成功抢先时不释放；以 SQL 执行事件钩子断言会员订单在订单 UPDATE 之前先读会员行、游客订单与会员 ID 已清空的订单在 UPDATE 之前不读会员行；`actor` 为 `system`、`admin`、大小写不同、空串或 None 时两函数都抛 `ValueError` 且不写入；游客订单的支付与取消行为不变（事件 `guest` 或 `system`，不写用券记录与积分流水）；视图给出三个快照字段（游客为 0）。每条测试写明它守住的是设计或 Kelvin 决定的哪一句。SQLite 不支持行锁、不生成 FOR UPDATE，会员行锁下的排队与死锁是否消除只能在真 MySQL 上体现，未在真 MySQL 上做并发验证。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/payment.py 的 submit_payment 与 cancel_order 增加关键字参数 actor（默认 guest，只接受 guest 与 member，其他值抛 ValueError），状态迁移的检查与写入的事件都用它；游客接口不传，行为不变；超时取消的操作者仍为 system
      - 锁顺序与会员下单、注销一致（会员 → 订单）——支付成功、本人取消与超时取消在做带条件的 UPDATE 之前，先以普通读取得订单的 member_id，非空时以 SELECT … FOR UPDATE 锁住该会员的行（会员已注销也锁），为空（游客订单或会员已注销后被清空）时不预先锁；锁后再做现有的带条件 UPDATE，未命中时的回滚与重放处理不变；于是单张订单的下单（会员 → 库存，SHOP-TASK-088）、取消（会员 → 订单 → 库存）与注销（会员 → 订单）都先取会员锁；会员 ID 已清空的订单在取消时由 SHOP-TASK-086 的释放函数在库存之后才锁会员行，此时该会员已注销，不会再下单或注销。超时取消在 SHOP-TASK-097 之后逐张订单真实提交（定时任务与每日库存重置都不再在外层事务里连续取消多张订单），所以不会跨订单持有库存与券的锁；模块说明写明这个锁顺序，并写明它依赖超时取消逐张提交
      - 支付成功时，在同一事务里、带条件的 UPDATE 命中之后——调用 SHOP-TASK-085 的核销函数（该单没有用券记录时返回 False，不出错）；以加锁读重新读取订单的 member_id 与会员状态，非空且会员为 active 时调用 SHOP-TASK-086 的获得函数，获得积分取订单快照 points_earned（为 0 时不写记录），否则不发放；重放不再核销或发放
      - 本人取消与超时取消，在同一事务里、带条件的 UPDATE 命中并加回库存之后——调用 SHOP-TASK-085 的释放函数与 SHOP-TASK-086 的释放函数（游客订单没有用券记录与积分流水，两者都不写）；UPDATE 未命中时整个事务回滚，不释放；重复取消、重复运行超时取消不重复释放
      - pay_orders 的视图另给 coupon_discount_sen、points_redeemed 与 points_earned（取订单快照，游客订单为 0）；游客接口 GET /api/pay/orders 的响应随之多出这三个字段，其余字段不变
      - tests/test_payment_coupons_points.py 用 SQLite 内存库按模型建表、经 SHOP-TASK-085、086 的函数准备会员订单（预占券与积分），验证意图——支付成功后用券记录为 used、写积分批次与 earn（数量为订单快照）；支付失败不核销不发放；本人取消与超时取消后用券记录为 released、积分 release 且可用余额恢复、库存加回；取消时批次已过期则随即记为到期；重放支付与重复取消不重复；会员已注销后超时取消时积分随即作废；会员在支付前被注销（member_id 已清空）时支付成功也不发放积分；以 member 为操作者支付与取消写 member 事件、actor 取其他值抛错；游客订单的支付与取消行为不变；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；tests/test_demo_payment.py 只在现有断言因视图多出三个字段而失效时随之修改；都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-090 记录段（锁顺序、支付成功的核销与发放、取消与超时的释放、视图字段、偏离、验证到什么程度，并写明会员的支付页接口留给 SHOP-TASK-091），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/api/pay.py、app/jobs.py、app/services/coupons.py、app/services/points.py、模型、迁移、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46、0.47），不合并、不直推
```

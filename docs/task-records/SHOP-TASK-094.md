# SHOP-TASK-094 会员注销时作废积分余额

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（注销时积分余额与未用券作废，积分和券使用流水保留去标识化审计记录），把 SHOP-TASK-086 的 `forfeit_points` 接入 SHOP-TASK-080 的 `delete_member`（`app/services/member_deletion.py`）；测试见 `tests/test_member_deletion_points.py`，`tests/test_payment_coupons_points.py` 只改了 `test_timeout_after_member_deleted_forfeits`。不做接口；未改 `app/services/points.py`、`app/api/member_deletion.py`、`tests/test_member_deletion.py`、模型、迁移、`.platform/`、CI、部署配置与设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.45、0.46（会员注销与积分）。拆分：业务规则层（4 个允许路径）。
- 接入位置：条件更新命中（会员由 active 改为 deleted）之后、同一事务里，撤销会话与清空订单会员 ID 之后、最后的 flush 之前，调用 `forfeit_points(db, member_id, now)`：未过期批次的剩余写 `forfeit`，已过期批次的剩余写 `expire`，剩余为 0 的批次不写，待抵扣不作废（都是 SHOP-TASK-086 的规则）。条件更新未命中（会员不存在或已注销）时照旧返回 False，不调用、不写积分流水。其余步骤与返回值不变，只 flush、不提交，不写日志；`forfeit_points` 的返回值不用。
- 锁：条件更新已持有该会员的行锁；`forfeit_points` 再以 SELECT … FOR UPDATE 锁同一会员行，再锁该会员的批次与流水，不取其他会员的锁。放在清空订单会员 ID 之后，与取消路径（会员 → 订单 → 积分）的先后一致。
- 未用券的含义：优惠券对全部会员相同、不发给个人（`docs/UX.md` 0.10 的 Q19），没有「持有的未用券」可作废；用券记录（`reserved`、`used`、`released`）不改，作为券使用流水保留，`member_id` 照旧指向保留下来、已清空手机号与密码的会员行。
- 待支付订单：注销时已预占的积分不在可用余额里，注销不动它；订单的会员 ID 已清空，无人可再访问，超时取消时由 SHOP-TASK-090 调用 `release_points`，释放后对已注销会员随即写等量的 `forfeit`（批次已过期则写 `expire`）。用券记录同样在超时取消时由 `release_coupon` 改为 `released`。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - `tests/test_payment_coupons_points.py` 的 `test_timeout_after_member_deleted_forfeits` 因注销先作废未预占的余额而失效，流水期望在 redeem 与 release 之间加了一条 `forfeit`（数量为 `-(SOURCE_POINTS - REDEEMED)`），文档字符串补了一句；该文件其他测试与断言未改。
  - 删除电话关联防套利索引仍留给之后的任务（不在本任务验收内）。
- 留给之后的任务：会员中心的积分与优惠券查询（SHOP-TASK-095）；积分到期的定时任务（SHOP-TASK-096）；注销删除失败向运营告警邮件报告，留给运营告警邮件任务。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 第 3 条原句、UX 0.10 的 Q19 与 HANDOFF 0.46 的 Kelvin 决定自查，人工核对 `tests/test_member_deletion.py` 的既有用例（没有积分时 `forfeit_points` 不写记录）与 `tests/test_payment_coupons_points.py` 中其他调用 `delete_member` 的用例（只看该订单的流水或只在超时取消期间记录语句）不受接入影响。`tests/test_member_deletion_points.py` 用 SQLite 内存库（引擎设置同 `tests/test_member_deletion.py`：由引擎发 BEGIN，每个连接打开外键检查并断言已打开），积分经 `earn_points`、`reserve_points`、`release_points` 准备，用券记录经 `reserve_coupon`、`use_coupon`，待抵扣照 `tests/test_points.py` 直接写一条 `debt_add`；覆盖：注销后可用余额为 0、有余额的批次各一条 `forfeit`、已过期的记为 `expire`、剩余为 0 的批次不写、待抵扣不变、原有流水保留；其他会员的批次、流水、余额与待抵扣不变；以替身包住 `forfeit_points` 断言命中时入参为该会员 ID 与当前时间、未命中（已注销、不存在）时不调用且不写流水；已注销会员再注销不写积分流水；同号经 `sms_login_or_register` 重新注册为新会员，余额与待抵扣从 0 开始；注销时有预占积分的待支付订单在 `release_points` 释放时随即 `forfeit`；用券记录（`used` 与 `reserved`）与券在注销后不变；注销函数返回后回滚，会员、订单、积分批次、积分流水与用券记录全部复原；以 SQL 执行事件钩子断言注销期间读取会员、积分批次与流水的语句的整数参数只有该会员的 ID；没有 `app.` 日志。每条测试写明它守住的是设计或 Kelvin 决定的哪一句，只出自本任务验收标准的约束注明「验收：」。SQLite 不支持行锁、不生成 FOR UPDATE，锁下的排队只能在真 MySQL 上体现，未在真 MySQL 上做并发验证。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/member_deletion.py 的注销函数在条件更新命中（会员由 active 改为 deleted）之后、同一事务里调用 SHOP-TASK-086 的作废函数（入参为该会员 ID 与当前时间）；条件更新未命中（不存在或已注销）时不调用；其余步骤与返回值不变，只 flush、不提交，不写日志
      - 锁顺序——注销的条件更新已持有会员行锁，作废函数再锁同一行不另取其他会员的锁；已过期的批次由作废函数记为到期，待抵扣不作废（SHOP-TASK-086 的规则），用券记录不改
      - tests/test_member_deletion_points.py 用 SQLite 内存库与 tests/test_member_deletion.py 相同写法的替身，经 SHOP-TASK-086 的函数准备积分，验证意图——注销后该会员的可用余额为 0、有余额的批次各有一条 forfeit、已过期的记为 expire、待抵扣不变、其他会员的积分不变；已注销的会员再注销不写积分流水；注销后同号重新注册为新会员，余额从 0 开始；注销时有已预占积分的待支付订单，超时释放时随即作废（经 SHOP-TASK-086 的释放函数）；用券记录在注销后保留且状态不变；注销函数返回后未提交（回滚后积分流水全部复原）；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；只出自本任务验收标准的实现约束（调用条件与入参、只 flush、不写日志、锁的范围）没有设计原句的，照 tests/test_member_deletion.py 的写法以「验收：」加该条约束注明，评审不以此要求修改；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - tests/test_payment_coupons_points.py 只为 test_timeout_after_member_deleted_forfeits 而改——注销现在先作废未预占的余额，所以流水期望在 redeem 与 release 之间加一条 forfeit（数量为负的 SOURCE_POINTS 减 REDEEMED），超时释放后的 release 与等量 forfeit 照旧，说明里补一句注销时先作废未预占余额；该文件其他测试与断言不增不删不改；这是本任务接入后唯一失效的既有断言（上一次 run 的 backend CI 只此一条失败）
      - docs/TODO.md 新增 SHOP-TASK-094 记录段（接入位置、未用券的含义、待支付订单的处理、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/points.py、app/api/member_deletion.py、tests/test_member_deletion.py、模型、迁移、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.45、0.46），不合并、不直推
```

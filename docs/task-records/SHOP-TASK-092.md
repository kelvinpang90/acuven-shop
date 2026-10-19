# SHOP-TASK-092 退款批准接入积分返还与追回

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 4、5 条与「订单与退款状态」（批准模拟退款时按原计价分摊回退未过期的已抵扣积分，并追回该商品产生的积分；余额不足扣至 0，剩余记待抵扣；事务内写订单、流水和事件），以及 `docs/HANDOFF.md` 0.46 记录的 Kelvin 2026-10-10 决定（返还以整单为单位，从后到期的批次起返还），把 SHOP-TASK-087 的 `apply_refund_points` 接入 `app/services/refund_review.py` 的批准；测试见 `tests/test_refund_review_points.py`。不做接口；未改 `app/services/points_refund.py`、`app/api/admin_refunds.py`、`tests/test_refund_review.py`、模型、迁移、`.platform/`、CI、部署配置与设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.46（积分）。拆分：业务规则层（3 个允许路径）。
- 接入位置：`_review` 在现有保存点（`begin_nested`）里写好申请的审核各列并 `flush` 之后、`record_audit` 之前；只在批准时、且锁定订单行时读到的订单（锁定查询带 `populate_existing`，即锁后的值）`member_id` 非空时调用 `apply_refund_points(db, 订单 ID, 申请 ID, 应返还, 应追回, now)`；`member_id` 为空（游客订单或会员注销后已清空）时不调用；拒绝不调用。锁定查询原来只判断有无结果，现在保留查到的订单对象供这里使用。返回值不使用。
- 两个积分数的来源：该申请各件（`refund_line_units`，即审核前已读取、确认都占用的那些件）对应的逐件分摊快照（`order_item_units`），应返还的抵扣积分为这些件 `points_discount` 之和，应追回的获得积分为这些件 `points_earned` 之和（新增 `_unit_points`，按件的快照 ID 查询后在 Python 里求和）。返还按批次的分配、已过期批次不返还、余额不足记待抵扣与累计上限都由 `apply_refund_points` 处理，本模块不重复。
- 锁顺序与死锁：沿用现有的锁定协议，先锁订单行（新事务的第一条语句），`apply_refund_points` 再锁会员行，顺序为订单 → 会员；与会员注销（会员 → 订单）并发时 MySQL 可能判定死锁并回滚其中一方，异常原样交给调用方现有的数据库错误处理（`app/api/admin_refunds.py` 未改），不另做重试。`apply_refund_points` 抛 `ValueError`（累计追回超过实际获得，即数据有误）时保存点回滚、原样抛出，申请仍为 `requested`，由调用方回滚整个事务；不被审核幂等键唯一约束的 `IntegrityError` 处理吞掉。
- 幂等：按审核幂等键重放（含保存点撞唯一约束后按键重新查询的那条路）都在调用之前返回，不再调用；`apply_refund_points` 自身对同一申请也只写一次。审计的操作名、对象、旧值与新值不变，不写积分数。本模块不写日志。模块说明与 `_review` 的步骤说明随之改写（删去「积分返还与追回由之后的任务接入」）。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 「订单与退款状态」说「事务内写订单、流水和事件」；本任务只在审核的同一事务里写积分流水，批准照旧不改订单状态、不写订单事件（SHOP-TASK-040 的约定：订单状态在批准与拒绝时都不改），验收也没有要求。
  - 被认领的游客订单 `member_id` 非空，照常调用；其快照的抵扣与获得都为 0，`apply_refund_points` 不写记录。会员已不是 active 而订单 `member_id` 尚未清空时同样调用，由 `apply_refund_points` 在会员行锁后判断、返回全零。
  - 申请仍只能由游客路径（`submit_refund`，申请方 `guest`）提交；会员订单经查单页提交的申请照常走本流程。会员模式的退款申请不在本任务。
- 留给之后的任务：退款申请页的积分预估（`estimate_refund_points` 接入申请页或接口、显示 `refund.points_reversed`）留给 SHOP-TASK-093；会员模式的退款申请与「我的订单」不在本任务。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句与 HANDOFF 0.46 的 Kelvin 决定自查，人工核对已有的 `tests/test_refund_review.py`（都是游客订单，不调用积分函数，锁定订单行的查询仍只有一条）与 `app/api/admin_refunds.py` 的调用方式不受影响。`tests/test_refund_review_points.py` 用 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开，由引擎发 BEGIN 使保存点与 MySQL 一致）按模型建表，会员、订单与逐件快照按模型写入，积分批次与流水经 SHOP-TASK-086 的 `earn_points`、`reserve_points`、`forfeit_points` 准备，申请经 `submit_refund` 提交，以外包原函数的替身记录 `apply_refund_points` 的入参；每条测试写明它守住的是设计或 Kelvin 决定的哪一句。覆盖：DESIGN 第 4 条算例批准只退第一件 A 后以（120、8）调用，返还 120、追回 8；从先到期与后到期批次抵扣的订单跨两次部分退款时先还后到期的批次，累计返还 300、追回 19，等于实际抵扣与获得；会员余额为 0 时追回的 10 记入待抵扣；游客订单与 `member_id` 已清空的订单批准时不调用、不写积分流水；拒绝不调用、不写积分流水；同键重放不再调用，流水与审计不增加；本单批次少于快照的获得积分时退款积分函数抛 `ValueError`，原样抛出，申请仍为 `requested`、各件仍占用、没有审计与积分流水；以 MySQL 方言编译的语句顺序断言锁订单行是新事务的第一条语句且早于锁会员行。SQLite 不支持行锁、不生成 FOR UPDATE，与注销并发时的死锁与回滚只在真 MySQL 上发生，未在真 MySQL 上做并发验证。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/refund_review.py 的批准在现有保存点里、写好申请的审核各列之后、写审计之前——取该申请各件（refund_line_units）对应的逐件分摊快照，应返还的抵扣积分为这些件 points_discount 之和、应追回的获得积分为这些件 points_earned 之和；订单的 member_id 非空时调用 SHOP-TASK-087 的退款积分函数（入参为订单 ID、申请 ID、这两个数与当前时间），为空时不调用；拒绝不调用
      - 锁顺序沿用现有的锁定协议（先锁订单行，SHOP-TASK-087 再锁会员行）；与会员注销并发时 MySQL 可能判定死锁并回滚其中一方，由现有的数据库错误处理回答，不另做重试；SHOP-TASK-087 抛 ValueError（累计追回超过实际获得，即数据有误）时原样抛出，整个审核随事务回滚，不改申请状态
      - 按审核幂等键重放时不再调用退款积分函数；审计的旧值与新值不变，不写积分数；本模块不写日志
      - tests/test_refund_review_points.py 用 SQLite 内存库按模型建表、经 SHOP-TASK-086 的函数准备会员订单的积分，验证意图——DESIGN 第 4 条算例批准只退第一件 A 后返还 120、追回 8；批准跨两次部分退款时累计不超过实际抵扣与获得；会员余额不足时记入待抵扣；游客订单与 member_id 已清空的订单批准不写积分流水；拒绝不写积分流水；重放不重复；退款积分函数抛错时申请仍为 requested；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-092 记录段（接入位置、两个积分数的来源、锁顺序与死锁、偏离、验证到什么程度，并写明退款申请页的积分预估留给 SHOP-TASK-093），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/points_refund.py、app/api/admin_refunds.py、tests/test_refund_review.py、模型、迁移、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

# SHOP-TASK-084 积分批次与积分流水的数据模型

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「数据模型」的 PointLedger 一行（获得、抵扣、到期、退款追回、待抵扣积分的不可覆盖流水；每笔关联订单或退款，余额不得小于零）、「计价、优惠、积分与库存」第 3、5 条、「权限与资料保护」第 3 条（注销作废）与 `docs/HANDOFF.md` 0.46 记录的 Kelvin 2026-10-10 决定（积分按批次记账，先用先到期的批次，退款按抵扣的相反顺序返还；批次在取得日加 12 个自然月那天结束时到期；预占以预占时为准）建两张表：`app/models/points.py`（`app/models/__init__.py` 导出 `PointLot`、`PointEntry`）、迁移 `alembic/versions/20261010_0019_points.py`（revision `0019`，down_revision `0018`）；测试见 `tests/test_point_models.py`
- 两张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；积分一律整数，不与仙混用；外键一律 ON DELETE RESTRICT；检查约束只用比较、IN、NOT IN 与 IS NULL / IS NOT NULL 及 AND / OR）：
  - `point_lots`（一单获得的积分为一个批次，行写入后不再修改）：`member_id` → `members.id`（`fk_point_lots_member_id_members`）、`order_id` 来源订单 → `orders.id`（`fk_point_lots_order_id_orders`，唯一 `uq_point_lots_order_id`，一单至多一个批次）、`points` 获得积分、`acquired_at`、`expires_at`（仍有效的最后一刻：取得日的马来西亚日期加 12 个自然月、当天 23:59:59 马来西亚时间，没有那天取该月最后一天，由写入方换算成 UTC）、`created_at`；全部非空。唯一 `uq_point_lots_id_member_id`（`id`, `member_id`）作为流水复合外键的目标；普通索引 `ix_point_lots_member_id_expires_at`（`member_id`, `expires_at`）。检查约束：
    - `ck_point_lots_points_positive`：`points > 0`
    - `ck_point_lots_expires_after_acquired`：`expires_at > acquired_at`
  - `point_entries`（只追加、不修改不删除）：`member_id` → `members.id`（`fk_point_entries_member_id_members`）、`lot_id`（可空；非空时以复合外键 `fk_point_entries_lot_id_point_lots`（`lot_id`, `member_id`）→ `point_lots`（`id`, `member_id`），保证流水与批次属于同一会员）、`kind` String(20)、`points`（有符号，不为 0）、`order_id` → `orders.id`（`fk_point_entries_order_id_orders`，非空）、`refund_request_id`（可空）→ `refund_requests.id`（`fk_point_entries_refund_request_id_refund_requests`）、`created_at`。普通索引 `ix_point_entries_member_id_created_at`（`member_id`, `created_at`）、`ix_point_entries_lot_id`、`ix_point_entries_order_id`。检查约束：
    - `ck_point_entries_kind_valid`：`kind IN ('earn', 'redeem', 'release', 'expire', 'refund_return', 'refund_reversal', 'forfeit', 'debt_add', 'debt_repay')`
    - `ck_point_entries_points_not_zero`：`points <> 0`
    - `ck_point_entries_positive_kind_points`：`kind NOT IN ('earn', 'release', 'refund_return', 'debt_add') OR points > 0`
    - `ck_point_entries_negative_kind_points`：`kind NOT IN ('redeem', 'expire', 'refund_reversal', 'forfeit', 'debt_repay') OR points < 0`
    - `ck_point_entries_lot_kind_has_lot`：`kind NOT IN ('earn', 'redeem', 'release', 'expire', 'refund_return', 'refund_reversal', 'forfeit') OR lot_id IS NOT NULL`
    - `ck_point_entries_debt_add_has_no_lot`：`kind <> 'debt_add' OR lot_id IS NULL`
    - `ck_point_entries_refund_kind_has_refund`：`kind NOT IN ('refund_return', 'refund_reversal', 'debt_add') OR refund_request_id IS NOT NULL`
    - `ck_point_entries_non_refund_kind_has_no_refund`：`kind NOT IN ('earn', 'redeem', 'release', 'expire', 'forfeit', 'debt_repay') OR refund_request_id IS NULL`
  - 九类流水：`earn` 支付成功获得积分、写入新批次（+，订单为来源订单）；`redeem` 结账抵扣、下单预占即写入（−，所用订单）；`release` 取消或支付超时还回预占（+，所用订单）；`expire` 批次到期扣去剩余（−，批次的来源订单）；`refund_return` 退款返还未过期的原抵扣（+，被退款的订单，带退款申请）；`refund_reversal` 退款追回获得积分（−，被退款的订单，带退款申请）；`forfeit` 会员注销作废批次剩余（−，批次的来源订单）；`debt_add` 追回不足的缺口记入待抵扣（+，批次为空，被退款的订单，带退款申请）；`debt_repay` 以后获得积分先偿还（−，成对写入：批次非空的一条从新批次扣出，批次为空的一条减少待抵扣；订单为新批次的来源订单）。
  - 待抵扣账户的表示：批次为空的流水构成该会员的待抵扣账户，合计为仍欠的积分；批次非空的流水合计为该批次剩余可用积分；二者不小于零由规则任务在会员行锁下保证。某单在某批次的净抵扣为该单在该批次的 `redeem`、`release` 与 `refund_return` 流水之和取负，退款返还以整单为单位按 HANDOFF 0.46 从后到期的批次起返还，不需要逐件与批次的对应。
  - 0019 的 upgrade 先建 `point_lots` 及其索引，再建 `point_entries` 及三个索引；downgrade 先删 `point_entries`（索引随表删除），再删 `point_lots`。
- 偏离：未改设计，未发现须停下的设计问题。说明几处取舍，请审阅：
  - 列名：来源订单与流水的订单都叫 `order_id`、获得积分与积分变动都叫 `points`（与订单的 `points_redeemed`、`points_earned` 同样以 points 表示积分）、类别为 `kind`、批次为 `lot_id`。
  - `ix_point_entries_lot_id` 只含 `lot_id`，按验收写；MySQL 要求复合外键（`lot_id`, `member_id`）有以这两列开头的索引，会另自动建一个以外键名命名的索引（`refund_request_id` 的外键同样自动建索引），alembic 的 MySQL 比较会略过这类索引，与 `variant_option_values` 的复合外键、`coupon_uses.member_id` 相同。downgrade 照 0018 随表删索引，不单独删。
  - 批次为空时复合外键不检查（MySQL 与 SQLite 都按 MATCH SIMPLE），`debt_add` 与批次为空的 `debt_repay` 因此可写；会员另有单列外键。
  - 「只追加、不修改不删除」与 `debt_repay` 成对写入库里无法用约束表达，由规则任务保证；库只靠 RESTRICT 拒绝删除被流水引用的批次（未被引用的批次库里可删）。
  - 库里不约束：流水的订单与批次来源订单或退款申请所属订单一致、批次的会员与来源订单上的会员一致（订单上的会员 ID 注销时会被清空）、流水所指退款申请为已批准；这些由写入的规则任务保证。MySQL 默认排序规则不区分大小写，类别由写入方用模型常量写小写。
- 留给之后的任务：余额计算、结账预占与先到期先用、取消或超时释放（已过期批次释放后随即记为到期）、支付成功写批次与偿还待抵扣、到期处理（跳过已预占部分）、退款返还与追回（不足记待抵扣）、注销作废，以及这些规则的接线（会员结账、支付、退款批准、注销、到期定时任务、会员中心查询），由 HANDOFF 2026-10-11 决定所列 SHOP-TASK-088 至 096 等之后的任务实现。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句与 HANDOFF 0.46 的 Kelvin 决定自查，并逐个核对 0019 的列类型、可空性、约束名与表达式、外键（含复合外键）删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_point_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入两个批次（含 2028-02-29 取得、到期取 2029-02-28 的批次）与九类流水各至少一条（含成对的 `debt_repay`）作对照，并按模块说明求和断言两个批次剩余与待抵扣都回到 0；再覆盖：每个检查约束至少一个反例（先在独立连接上逐条求值该表全部检查约束，断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含获得积分为 0 与负、到期等于或早于取得、未知类别、九类各自变动为 0、九类各自符号相反、七类缺批次、`debt_add` 带批次、三类退款类缺退款申请、六类非退款类带退款申请；除自增主键外每个非空列写入 NULL 被非空约束拒绝；同一订单第二个批次被唯一约束拒绝；SQLite 表上确有（`id`, `member_id`）与 `order_id` 两个唯一索引，用另一张来源订单写入与已有批次（`id`, `member_id`）相同的批次被拒（`id` 同为主键，SQLite 可能先报主键，测试匹配两种报法）；流水的批次属于另一会员被复合外键拒绝（另以同一会员可写作对照）；批次与流水指向不存在的行、删除被流水引用的批次、会员、订单或退款申请被外键拒绝（另以未被引用的批次可删作对照）；积分读回为 int；只有批次与退款申请可空、类别分组覆盖九类、索引与复合外键目标存在、外键都是 RESTRICT。每条测试写明它守住的设计或决定原句。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/models/points.py 定义两个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项（写法照 app/models/member.py）；app/models/__init__.py 导出它们；时间一律不带时区的 UTC；外键删除行为一律 RESTRICT；除下面写明可空的列外全部非空；积分一律整数，不与仙混用；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 积分批次表 point_lots（一单获得的积分为一个批次，行写入后不再修改）——会员（外键 members）、来源订单（外键 orders，全表唯一，一单至多一个批次）、获得积分（大于 0）、取得时间、到期时间（仍有效的最后一刻，由写入方按 Kelvin 的决定换算成 UTC）、创建时间；检查约束保证到期时间晚于取得时间；按（会员、到期时间）建索引；另有（id、会员）的唯一约束作为流水复合外键的目标
      - 积分流水表 point_entries（只追加、不修改不删除）——会员（外键 members）、批次（可空）、类别、积分变动（有符号整数，不为 0）、订单（外键 orders，非空：获得、抵扣、释放、到期、作废、偿还记来源或所用订单，其中到期与作废记该批次的来源订单，返还、追回与待抵扣记被退款的订单）、退款申请（外键 refund_requests，可空）、创建时间；批次非空时以（批次、会员）复合外键指向 point_lots 的（id、会员），保证流水与批次属于同一会员；按（会员、创建时间）、批次与订单建索引
      - 类别只允许 earn、redeem、release、expire、refund_return、refund_reversal、forfeit、debt_add、debt_repay（forfeit 为会员注销时作废批次剩余积分，DESIGN「权限与资料保护」第 3 条）；检查约束逐条保证——earn、release、refund_return 的积分变动大于 0，redeem、expire、refund_reversal、forfeit 小于 0；这七类的批次非空；debt_add 的批次为空且变动大于 0；debt_repay 的变动小于 0（批次非空的一条从批次扣出，批次为空的一条减少待抵扣，成对写入由规则任务保证）；refund_return、refund_reversal、debt_add 的退款申请非空，其余类别的退款申请为空；模块说明写明批次为空的流水构成该会员的待抵扣账户（合计为仍欠的积分），批次非空的流水合计为该批次剩余可用积分，二者不小于零由规则任务在会员行锁下保证；某单在某批次的净抵扣为该单在该批次的 redeem、release 与 refund_return 流水之和取负，退款返还以整单为单位按 HANDOFF 0.46 从后到期的批次起返还，不需要逐件与批次的对应
      - 新迁移文件 alembic/versions/20261010_0019_points.py，revision 为 0019、down_revision 为 0018，upgrade 建两张表，downgrade 按依赖倒序删除；约束名按命名约定逐个写出；迁移只在 MySQL 上执行；模型里的检查约束表达式须 SQLite 与 MySQL 都支持（只用比较、IN、NOT IN、IS NULL 与 AND / OR）；PR 正文逐表列出检查约束、复合外键与索引
      - tests/test_point_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的批次与九类流水各一条（含成对的 debt_repay）作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含除自增主键外每个非空列写入空值被拒、未知类别、变动为 0、每类变动符号相反、七类缺批次、debt_add 带批次、退款类缺退款申请、非退款类带退款申请、到期不晚于取得、获得积分为 0、同一订单两个批次、流水的批次属于另一会员被复合外键拒绝、删除被流水引用的批次被拒）；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 无差异；这些都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-084 记录段（两张表、九类流水的含义与约束、待抵扣账户的表示、偏离、验证到什么程度，并写明规则与接线留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改已有模型与迁移、.platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py 与前端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

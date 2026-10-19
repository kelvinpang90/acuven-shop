# SHOP-TASK-106 后台退款详情给出积分返还与追回

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 5 条（退款批准时返还未过期的抵扣积分并追回获得积分，余额不足记待抵扣）与 `docs/UX.md` 0.10 的 A03（详情显示 `admin.refund_points`；首版不显示这一行由 Kelvin 2026-10-06 决定，待会员与积分账本任务接入积分字段时加回），在后台退款详情加入积分返还与追回：`app/services/admin_refunds.py` 的 `AdminRefundDetail` 新增四个字段，`refund_detail` 新增当前时间入参（不带时区的 UTC），`app/api/admin_refunds.py` 的 `GET /api/admin/refunds/{refund_id}` 以 `_utcnow()` 传入；其余字段、检查顺序与错误不变。只读：不写审计、不锁行、不 flush。不做前端；未改 `app/services/points.py`、`app/services/points_refund.py`、`app/services/refund_review.py`、模型、迁移、`.platform/`、CI、部署配置与设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.46（积分）。拆分：业务规则层与接口（5 个允许路径）。
- 字段：`placed_by_member`（订单的 `claim_status` 为 `not_claimable`；会员注销后仍为真，游客订单与被认领的游客订单为假，与查单视图的同名字段同一判定）、`points_returned`、`points_reversed`（从余额追回与记入待抵扣之和）、`points_estimated`。
- 三种状态的取值：
  - `approved`：取该申请的实际积分流水（`point_entries.refund_request_id` 为该申请）。`points_returned` 为 `refund_return` 之和，`points_reversed` 为 `refund_reversal` 之和取负加 `debt_add` 之和，`points_estimated` 为假。游客订单、注销后才批准的订单没有流水，两个数为 0。
  - `requested`：该申请各件（`refund_line_units`）对应逐件快照（`order_item_units`）的 `points_discount` 之和与 `points_earned` 之和，连同订单 ID 与当前时间交给 SHOP-TASK-087 的 `estimate_refund_points`；`points_returned` 为其 `returned`（预估的实际返还，分到已过期批次的部分不计），`points_reversed` 为其 `reversed_total`，`points_estimated` 为真。订单 `member_id` 为空或会员不是 active 时按该函数的规则为零。预估在当前状态（已批准退款之后）上计算，批准时可能因到期或余额变化而不同，以批准时为准。
  - `rejected`：三个积分字段都为 null。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 游客订单的 `approved` 与 `requested` 也给出 0 而不是 null（按验收的取值规则：没有流水即为 0，预估按 SHOP-TASK-087 为零）；页面是否显示这一行由 `placed_by_member` 决定，留给 SHOP-TASK-107。
  - 预估函数因累计追回超过该单实际获得（数据有误）抛 `ValueError` 时不另转换，交给框架（500），与 SHOP-TASK-093 相同；审核中的申请占用的件不会与已批准的重叠，正常数据下不会出现。
  - `tests/test_admin_refunds.py` 只改了 `test_detail_fields_amounts_and_reviewer`：它对响应整体做相等断言，因多出四个字段而失效，期望里补上游客订单的取值（已批准 0、0、假；审核中 0、0、真；`placed_by_member` 为假）；其他测试未改。
- 留给之后的任务：A03 页面显示 `admin.refund_points` 一行（只在 `placed_by_member` 为真且积分字段不为 null 时显示）留给 SHOP-TASK-107。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 第 4、5 条原句、UX 0.10 A03 与 HANDOFF 0.46 自查，人工核对前端 `frontend/src/api/adminRefunds.ts` 的 `readDetail` 不因多出字段而拒绝响应（未改前端）。`tests/test_admin_refund_points.py` 用 `TestClient`（`https://testserver`）与 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开，由引擎发 BEGIN；写法照 `tests/test_admin_refunds.py`），会员、订单与逐件快照按模型写入，积分经 SHOP-TASK-086 的 `earn_points`、`reserve_points`、`forfeit_points` 准备，申请经 `submit_refund` 提交，批准与拒绝经 `approve_refund`、`reject_refund`（批准时由 SHOP-TASK-087 的 `apply_refund_points` 写流水）；每条测试写明它守住的是设计或 UX 的哪一句。覆盖：DESIGN 第 4 条算例已批准只退第一件 A 为（120、8、假）且读取前后各表不变；余额只够追回 5 时从余额追回 5、记入待抵扣 5，详情给出 10；审核中的同一申请预估为（120、8、真），读取前后订单、积分批次与流水、申请、申请行与件、审计全部列值不变；已批准一笔之后，另一笔审核中的第二件 A 预估为（120、7、真）；以服务函数直接传入抵扣批次到期之后的时间，返还为 0、追回仍为 8，且期间只有查询、以 MySQL 方言编译都不带 FOR UPDATE、会话没有待写对象；已拒绝为 null；游客订单 `placed_by_member` 为假、已批准 0、审核中预估 0、已拒绝 null；被认领的游客订单 `placed_by_member` 为假；会员注销后（`member_id` 已清空）`placed_by_member` 仍为真，预估为 0，之后批准不写流水、为 0。SQLite 不生成 FOR UPDATE，未在真 MySQL 上验证；未做浏览器验收。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/services/admin_refunds.py 的 AdminRefundDetail 另给 placed_by_member（订单的 claim_status 为 not_claimable）、points_returned、points_reversed 与 points_estimated；refund_detail 增加当前时间入参（不带时区的 UTC）；只读，不写审计、不锁行
      - 取值——申请为 approved 时取该申请的实际积分流水：points_returned 为该申请 refund_return 流水之和，points_reversed 为该申请 refund_reversal 流水之和取负加上 debt_add 流水之和（即从余额追回与记入待抵扣之和），points_estimated 为假；申请为 requested 时按该申请各件逐件快照的 points_discount 与 points_earned 之和调用 SHOP-TASK-087 的预估函数，points_returned 与 points_reversed 为预估的实际返还与「从余额追回加记入待抵扣」，points_estimated 为真；申请为 rejected 时两个数与 points_estimated 都为 null；订单 member_id 为空时预估按 SHOP-TASK-087 的规则为零
      - app/api/admin_refunds.py 的 GET /api/admin/refunds/{refund_id} 把当前时间传给 refund_detail，响应随之多出这四个字段，其余字段、检查顺序与错误不变
      - tests/test_admin_refund_points.py 用 SQLite 内存库与 TestClient（照 tests/test_admin_refunds.py 的写法，经 SHOP-TASK-086、087 的函数准备会员订单的积分与已批准的退款）验证意图——已批准的申请给出实际返还与追回（含记入待抵扣的部分）且 points_estimated 为假；审核中的申请给出预估（DESIGN 第 4 条算例只退第一件 A 为返还 120、追回 8）且为真、读取不写库；已拒绝为 null；游客订单 placed_by_member 为假；tests/test_admin_refunds.py 只在现有断言因多出字段或新增入参而失效时随之修改；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-106 记录段（字段、三种状态的取值、偏离、验证到什么程度，并写明 A03 页面显示留给 SHOP-TASK-107），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/points.py、app/services/points_refund.py、app/services/refund_review.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

# SHOP-TASK-095 会员中心的积分概览与积分明细查询

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 3、5 条及「权限与资料保护」第 6 条，在 `app/services/member_account.py` 增加会员积分的只读查询；设计闸门为 DESIGN 1.11 与 HANDOFF 0.46。概览复用可用余额与待抵扣的账本口径，最近到期按马来西亚日期合并，排除已过期和余额为零的批次。明细按订单与显示类型合并：`earned` 对应 earn，`redeemed` 对应 redeem 与 release，`expired` 对应 expire，`returned` 对应 refund_return，`reversed` 对应 refund_reversal 与 debt_add（后者按负数显示）；debt_repay 与 forfeit 不显示。按最近流水、订单 ID 与固定显示类型顺序倒排，最多 100 行。
- 偏离与取舍：无设计偏离；查询不判断会员状态、不加锁也不写库，会员中心的两个接口（积分与优惠券，含会员会话校验与响应）留给 SHOP-TASK-108，前端 P13 留给之后的任务。
- 验证到什么程度：`tests/test_member_account.py` 以开启外键检查的 SQLite 内存库，经既有积分获得、抵扣、释放、到期和退款函数准备数据，覆盖登记的退款综合场景、马来西亚日期合并、空/过期/用完批次、100 行上限、其他会员隔离与不写库。每条测试说明守住的设计、UX 或本任务登记约束；由 PR 必需 CI 的 backend 执行。综合场景与概览其他情形的数值照登记写明的流水与期望编写。由运营者接手完成：Worker run c8b78cfe 的实现与测试正确，只因 `ruff check`（import 排序）与 `ruff format` 未过以 checks_failed 失败；运营者以 ruff 修正格式，并在综合场景的三个检查点同时断言余额与尚欠（原先各只断言其一）、在不写库测试里断言确实记录到了语句；按 Codex 审查，两个查询改在 `no_autoflush` 下执行（会话有待写对象时原先会被自动 flush），并补测试：调用后待写对象仍未 flush、100 行上限保留的是最新的 100 行、同一时刻不同订单按订单 ID 从大到小、最早到期但已用完的批次不计入最近到期。本地以 SQLite 跑过本文件的测试；未在真 MySQL 上验证。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/member_account.py 的积分概览函数（只读，不写库、不 flush，入参为数据库会话、会员 ID 与当前时间）——余额为 SHOP-TASK-086 的可用余额，尚欠为待抵扣；最近到期为剩余大于 0 且未过期的批次中到期时刻最早的那个马来西亚日期（按 UTC+8 换算，写法同 app/services/sms_budget.py 的 MALAYSIA_UTC_OFFSET）及该日到期的剩余之和，没有则为空
      - 同一文件的积分明细函数（只读，入参为数据库会话与会员 ID）——按订单与显示类型合并流水，每行为显示类型、积分（正负号按余额增减）、订单号与该组最晚的流水时间；earned 为 earn；redeemed 为该单 redeem 与 release 之和（为 0 时不显示，即取消的订单不出现）；expired 为 expire；returned 为 refund_return；reversed 为 refund_reversal 与 debt_add 之和（debt_add 记为负）；debt_repay 与 forfeit 不显示（偿还体现在余额与尚欠里）；按时间从晚到早、同时刻按订单 ID 从大到小、同一订单再按显示类型的固定顺序（reversed、returned、expired、redeemed、earned），最多 100 行；只含该会员的流水
      - tests/test_member_account.py 用 SQLite 内存库按模型建表（开启外键检查）、经 SHOP-TASK-085、086 的函数与 SHOP-TASK-087 的退款积分函数（退款返还、追回与记入待抵扣）准备数据，验证意图——余额、尚欠与最近到期（同一马来西亚日期的两个批次合并、已过期与已用完的批次不计、没有批次时为空）；明细的五种类型与合并规则（取消的订单不出现、退款追回含记入待抵扣的部分、偿还不显示）、顺序（含同一订单同一时刻的返还与追回按固定类型顺序）、100 行上限、不含其他会员；两个函数都不写库；每条测试写明它守住的是设计、UX 或 Kelvin 决定的哪一句（100 行上限出自本任务登记，设计没有规定行数，测试说明照此写明）；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - 明细的综合测试照下列流水与期望值编写（运营者已在本任务前的 main 上实跑核对，照写即可，不另行推算或改动数值），T 为测试固定的当前时间，按下列顺序依次调用——订单 O 于 T 减 400 天获得 4；订单 E 于 T 减 5 天获得 10；订单 S 于 T 减 3 天抵扣 5、于 T 减 2 天获得 20；订单 C 于 T 减 3 秒抵扣 8 并同刻释放；订单 R 于 T 减 2 秒抵扣 20；以 T 减 1 秒执行一次到期（O 的批次已过期，到期 1 个批次），此时概览为余额 5、尚欠 0；S 于 T 退款（返还 5、追回 20），此时概览为余额 0、尚欠 10（先返还 5，再从余额追回 10，其余 10 记入待抵扣）；订单 L 于 T 加 1 秒获得 12（先偿还 10，写有 debt_repay 流水）；另一会员于 T 加 2 秒获得 99。明细恰为九行，按（类型、积分、订单）依次为 earned 12 L、reversed -20 S、returned 5 S、expired -4 O、redeemed -20 R、earned 20 S、redeemed -5 S、earned 10 E、earned 4 O；之后以 T 加 1 秒查询的概览为余额 2、尚欠 0。概览的其他情形（马来西亚日期合并、已过期与已用完的批次、没有批次）与 100 行上限另写各自的测试，不并入这个场景
      - 概览其他情形的测试照下列数据与期望编写（运营者已在本任务前的 main 上实跑核对，照写即可，不另行推算或改动数值）。批次在获得时的马来西亚日期 12 个月后那天的马来西亚日终到期（SHOP-TASK-086 的 lot_expires_at），T 固定为 UTC 2026 年 10 月 14 日 4 时，按下列顺序依次调用——于 T 减 10 天获得 4，并于 T 由另一订单全部抵扣（已用完）；于 T 减 400 天获得 9（已过期，不执行到期）；于 UTC 2025 年 10 月 14 日 0 时获得 7、同日 12 时获得 3（马来西亚日期同为 2025 年 10 月 14 日，都在马来西亚日期 2026 年 10 月 14 日到期）；于 T 获得 6。以 T 查询的概览最近到期为 2026 年 10 月 14 日、10 积分；没有批次的会员最近到期的日期与积分都为空。验证不写库的测试若用 before_cursor_execute 事件记录 SQL，语句是监听函数的第三个参数（前两个是连接与游标）
      - docs/TODO.md 新增 SHOP-TASK-095 记录段（概览、明细的合并规则与显示类型对应、偏离、验证到什么程度，并写明两个接口留给 SHOP-TASK-108、前端 P13 留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/coupons.py、app/services/points.py、app/services/points_refund.py、app/api/ 下的文件、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

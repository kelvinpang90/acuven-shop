# SHOP-TASK-093 退款申请页的积分预估接口（查单模式）

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「计价、优惠、积分与库存」第 5 条与「权限与资料保护」第 6 条（访客仅能访问已通过查询验证的订单），以及 `docs/UX.md` 0.10 P10（会员订单另见积分返还与追回 `refund.points_back`、`refund.points_reversed`，已过期的抵扣积分不返还），在 SHOP-TASK-087 的 `estimate_refund_points` 之上于 `app/api/refunds.py` 增加 `POST /api/orders/refunds/points-estimate`；测试见 `tests/test_refund_points_estimate_api.py`。不做前端；未改 `app/services/`、`app/api/order_lookup.py`、`tests/test_refunds.py`、`.platform/`、CI、部署配置与设计，未加表或迁移，未装依赖。`POST /api/orders/refunds` 的行为不变，只补了模块说明。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.46（积分）。拆分：接口层（3 个允许路径）。
- 接口：与 `POST /api/orders/refunds` 同一路由器（`_NoStoreRoute`），处理函数与依赖产生的全部响应（含 401、409、413、415、422）都带 `Cache-Control: no-store`。只读、不写库（会话只查询、不 flush 不提交），不要求幂等键与 CSRF。授权与提交申请相同：只凭订单访问 cookie 与 lookup 授权（`_lookup_order_id`），`guest_checkout` 授权不能用，不通过一律 401 `{"detail":"access_expired"}`；不读会员会话。检查顺序为 413（8 KB，逐块，`_json_body`）→ 415 → 422（结构）→ 401。请求体复用 `RefundIn` 与 `_line_errors`：订单号与 1 到 20 项申请行（订单行序与 1 到 99 件，行序不重复），多出字段 422，422 只给位置、类型与固定消息，不回显请求内容。本模块不写日志。
- 选件规则：授权之后与提交申请相同，订单未支付 409 `order_not_refundable`，退款期已过 409 `refund_window_closed`；再按 `refund_state` 取各行可退件，行序不属于该订单 422（先查完全部行序），任一行件数超过该行可退件 409 `{"detail":"refund_nothing_left"}`；每行以 `pick_units` 取件序最小的那几件（与提交申请时选件相同，已被审核中或已批准的申请占用的件不在其中）。应返还与应追回分别为选中各件逐件快照的 `points_discount` 与 `points_earned` 之和。
- 字段：`member_order`（订单的 `member_id` 非空为 true）、`points_back`（`estimate_refund_points` 的 `returned`，即预估实际返还，分到已过期批次的部分不计）、`points_reversed`（`reversed_total`，即从余额追回与记入待抵扣之和）。`member_order` 为 false 时两个数为 0，不调用预估函数。
- 预估与批准时可能因到期或余额变化而不同，以批准时为准：预估在当前状态（已批准退款之后）上计算，不计审核中申请将来批准时的返还与追回；批次在预估之后、批准之前到期，或会员余额在这期间变化，批准时的实际数可能不同。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 件数超过该行可退件时按验收回答 `refund_nothing_left`，而提交申请对同一情形回答 `refund_duplicate`（该单完全没有可退件时两者都是 `refund_nothing_left`）。
  - 会员已不是 active 而订单 `member_id` 尚未清空时，`member_order` 为 true，两个数由预估函数给出 0；会员注销后 `member_id` 已清空的订单按游客订单回答。
  - 预估函数因累计追回超过该单实际获得（数据有误）抛 `ValueError` 时不另转换，交给框架（500）；正常数据下选中的都是未被占用的件，不会出现。
  - 不另做限流，与提交申请接口相同。
- 留给之后的任务：会员模式（凭会员会话的积分预估）随会员订单接口（SHOP-TASK-105）提供；P10 前端显示 `refund.points_back`、`refund.points_reversed` 留给之后的前端任务（规划块「已阻塞」里的会员中心 P13 与退款申请会员模式那一项）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 原句、UX 0.10 P10 与 HANDOFF 0.46 自查，人工核对提交申请接口的代码路径未改。`tests/test_refund_points_estimate_api.py` 用 `TestClient`（`https://testserver`）与 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；写法照 `tests/test_refunds.py`），会员、订单与逐件快照按模型写入，积分批次与流水经 SHOP-TASK-086 的 `earn_points`、`reserve_points` 准备，已有申请经 `submit_refund` 提交，查单授权经 `issue_order_access` 签发，以外包原函数的替身记录 `estimate_refund_points` 的入参；未加依赖。每条测试写明它守住的是设计或 UX 的哪一句。覆盖：DESIGN 第 4 条算例选第一件 A 得返还 120、追回 8；全部三件得 300、19；抵扣批次已过期时返还为 0、追回仍为 8；第一件 A 被审核中的申请占用时选的是第二件 A（追回 7）；游客订单 `member_order` 为 false、两个数为 0 且不调用预估函数；接口前后积分批次、积分流水、申请、申请行与逐件记录都不变；件数超过购买数或剩余可退件 409；待支付订单 409 `order_not_refundable`；退款期已过 409 `refund_window_closed`；行序不属于该订单 422 不回显；没有 cookie、只有 `guest_checkout` 授权、只有另一张订单的授权都 401；413 先于 415、422 与 401，415 先于 401，422（多出字段、行序重复、件数 0 或 100、21 行、没有行）先于 401 且不回显；各响应带 no-store；没有 `app.` 日志。未在真实浏览器或真 MySQL 上验证。这些测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 在 app/api/refunds.py 增加 POST /api/orders/refunds/points-estimate（同一路由器，响应同样带 no-store）；只读、不写库、不要求幂等键与 CSRF；授权与 POST /api/orders/refunds 相同（订单访问 cookie 与 lookup 授权，不通过一律 401 access_expired）；检查顺序为 413（8 KB，逐块）→ 415 → 422（结构）→ 401；请求体与退款申请相同（订单号与申请行列表，1 到 20 项，每项为订单行序与 1 到 99 件，行序不重复），多出字段 422，422 不回显请求内容；不写日志
      - 按 app/services/refunds.py 的 refund_state 取各行可退件，每行用 pick_units 取件序最小的那几件（与提交申请时选件相同）；行序不属于该订单 422，件数超过该行可退件 409 {"detail":"refund_nothing_left"}；应返还与应追回分别为选中各件的 points_discount 与 points_earned 之和，调用 SHOP-TASK-087 的预估函数
      - 响应为 member_order（订单的 member_id 非空为 true）、points_back（预估实际返还）与 points_reversed（从余额追回与记入待抵扣之和）；member_order 为 false 时两个数为 0、不调用预估函数；退款期已过或订单不可退款时与提交申请相同的 409（refund_window_closed、order_not_refundable）
      - tests/test_refund_points_estimate_api.py 用 TestClient 与 SQLite 内存库（照 tests/test_refunds.py 的写法，经 SHOP-TASK-086 的函数准备会员订单的积分，不加依赖）验证意图——DESIGN 第 4 条算例选第一件 A 得返还 120、追回 8；抵扣批次已过期时返还为 0；游客订单 member_order 为 false；没有查单授权 401、guest_checkout 授权不能用；件数超过可退件 409；接口不写库（调用前后积分流水与申请都不变）；所有响应（含错误）带 no-store；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-093 记录段（接口、选件规则、字段、偏离、验证到什么程度，并写明预估与批准时可能因到期或余额变化而不同、以批准时为准，会员模式与前端留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；POST /api/orders/refunds 的行为不变（模块说明可随之补充）；不改 app/services/ 下的文件、app/api/order_lookup.py、tests/test_refunds.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

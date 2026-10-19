# SHOP-TASK-105 会员模式的退款申请与积分预估接口

- [x] 在会员订单路由器提供退款申请与积分预估接口：二者只凭会员会话定位本人订单并统一返回 `no-store`；申请沿用查单模式的请求、幂等、冲突与退款规则，以 `member` 记录申请方并额外验证 CSRF；预估沿用查单模式的选件和积分计算，只读且不要求 CSRF 或幂等键。
- 授权与异同：未登录统一为 `member_session_required`，他人和游客订单统一为 `order_not_found`，不读取查单 cookie。申请的处理顺序为请求体大小与 JSON 类型、会员会话、CSRF、请求校验、本人订单；预估不要求 CSRF。已认领的游客订单属于会员订单但下单时没有积分抵扣与获得，预估两个积分数均为 0。
- 偏离与取舍：无设计偏离；未改服务、模型、迁移、查单接口、前端或设计。P10 的会员模式退款申请与积分显示前端留给之后的任务。
- 验证到什么程度：`tests/test_account_order_refunds_api.py` 以 TestClient 与 SQLite 内存库、会员会话和 SHOP-TASK-086 的积分准备函数，覆盖未登录、CSRF、本人隔离、member 申请记录、重放与幂等冲突、DESIGN 第 4 条算例的返还 120 与追回 8、预估前后不写库、零积分会员订单、查单 cookie 拒绝和所有响应的 `no-store`；两个接口的检查顺序与 422 不回显，每条测试注明守住的 DESIGN 1.11 或 UX P10 句子。由 PR 的必需 CI 检查 backend 执行；未在真实 MySQL 或浏览器中验证。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 在 app/api/account_orders.py 增加 POST /api/member/orders/refunds 与 POST /api/member/orders/refunds/points-estimate（同一路由器，响应同样带 no-store）；都只凭会员会话（require_member，401 member_session_required），不读订单访问 cookie；不写日志
      - POST /api/member/orders/refunds 的请求体、幂等键、成功与重放的状态码与响应体、409 与 422 的 detail 都与 POST /api/orders/refunds 相同，检查顺序为 413（8 KB，逐块）→ 415 → require_member → require_member_csrf（403 csrf_failed）→ 422 → 以 SHOP-TASK-103 的定位函数找本人订单（找不到一律 404 {"detail":"order_not_found"}）；之后调用 submit_refund，actor 为 member
      - POST /api/member/orders/refunds/points-estimate 的请求体、选件规则、计算、响应字段（member_order、points_back、points_reversed）与错误都与 SHOP-TASK-093 的 POST /api/orders/refunds/points-estimate 相同（可直接复用 app/api/refunds.py 里 SHOP-TASK-093 写的选件与计算函数，不改那个文件），只是授权改为 require_member 与本人订单定位（404 order_not_found），检查顺序为 413 → 415 → require_member → 422 → 404；只读，不要求 CSRF 与幂等键
      - tests/test_account_order_refunds_api.py 用 TestClient 与 SQLite 内存库（照 tests/test_refunds.py 与 SHOP-TASK-104 的测试写法，经 SHOP-TASK-086 的函数准备会员订单的积分，不加依赖）验证意图——未登录 401；会员申请退款写 member 申请、缺 CSRF 403、他人与游客订单 404、重放与 409 和查单模式一致；积分预估对 DESIGN 第 4 条算例给出返还 120、追回 8，对已认领的游客订单 member_order 为真但两数为 0（下单时没有抵扣与获得），不写库；查单 cookie 不能用于这些接口；所有响应（含错误）带 no-store；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-105 记录段（两个接口、授权、与查单模式的异同、偏离、验证到什么程度，并写明前端 P10 会员模式留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；SHOP-TASK-104 的三个接口行为不变（模块说明可随之补充）；不改 app/services/ 下的文件、app/api/refunds.py、tests/test_account_orders_api.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

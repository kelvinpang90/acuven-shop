# SHOP-TASK-104 我的订单的列表、详情与确认收货接口

- [x] 提供会员会话范围内的订单列表、详情与确认收货接口：列表返回订单号、UTC 创建时间、状态与总额并固定每页 20 条；详情返回收货资料、优惠券与积分快照及退款部分；确认收货沿用查单模式的幂等和冲突响应，以 member 为操作者。
- 授权与取舍：三个接口只使用会员会话，不读取订单访问 cookie；详情和确认收货对他人或游客订单统一返回 order_not_found，写操作另验 CSRF。没有设计偏离，不改服务、模型、迁移、前端或查单接口。
- 验证到什么程度：`tests/test_account_orders_api.py` 以 TestClient 和 SQLite 内存库、经 SHOP-TASK-076 会话签发，覆盖三个接口的未登录与真实查单 cookie 拒绝、私有列表与分页、详情快照和隔离、超时取消、CSRF、member 事件、重放、幂等冲突、不可确认与全部已退冻结；每条测试注明守住的 DESIGN 1.11 或 UX P09/P13 句子。由 PR 的必需 CI 检查 backend 执行。退款接口留给 SHOP-TASK-105，前端 P13 与 P09 会员模式留给之后的任务。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/api/account_orders.py（app/main.py 挂上路由）提供 GET /api/member/orders、POST /api/member/orders/view 与 POST /api/member/orders/confirm-receipt；路由器用 SHOP-TASK-027 的 _NoStoreRoute，这三个接口的处理函数与依赖产生的响应（含错误）都带 no-store；路径存在但方法不匹配时由框架返回的 405 不在此列（POST /api/member/orders 由 SHOP-TASK-089 的会员下单占用，GET 不与之冲突）；都只凭会员会话（require_member，401 member_session_required），不读订单访问 cookie；订单号只在请求体里，不进任何路径或查询参数；不写日志
      - GET /api/member/orders?page=N（1 到 10000 的整数，默认 1，不合法 422，写法同 app/api/admin_stock_resets.py）返回 {total, page, page_size（20）, orders}，每项为 order_number、created_at（带 UTC 时区）、status 与 total_sen，取自 SHOP-TASK-103 的列表函数；只读
      - POST /api/member/orders/view（语言参数同 GET /api/orders/lookup）的检查顺序为 413（8 KB，逐块，沿用 app/api/pay.py 的 _json_body）→ 415 → require_member → 422（请求体只有订单号，多出字段 422，422 不回显，沿用 app/api/pay.py 的 _body_errors）；以 SHOP-TASK-103 的定位函数找本人订单，找不到一律 404 {"detail":"order_not_found"}；订单待支付且已过支付到期时间时先对它执行 app/services/payment.py 的 expire_overdue_orders；返回 SHOP-TASK-103 扩展后的查单视图（含收货资料、券与积分快照与退款部分）；不要求 CSRF 与幂等键
      - POST /api/member/orders/confirm-receipt 的请求体、幂等键与 409 的 detail 与 POST /api/orders/confirm-receipt 相同，检查顺序为 413（8 KB，逐块，与查单模式相同沿用 app/api/pay.py 的 _json_body）→ 415 → require_member → require_member_csrf（403 csrf_failed）→ 422 → 定位本人订单（404 order_not_found）；之后调用 confirm_receipt，actor 为 member；成功与重放的状态码与响应体和查单模式相同
      - tests/test_account_orders_api.py 用 TestClient 与 SQLite 内存库（照 tests/test_order_lookup.py 与 tests/test_member_pw_api.py 的写法，经 SHOP-TASK-076 的会话签发登录，不加依赖）验证意图——未登录 401；列表只含本人订单、分页与字段；详情对本人下单的订单给出券与积分快照与 placed_by_member 为真、对已认领的游客订单为假，对他人与游客订单 404 且不泄露是否存在；超时的待支付订单查看时已为 demo_cancelled；确认收货写 member 事件、缺 CSRF 403、他人订单 404、重放与 409 和查单模式一致、全部已退时冻结；查单 cookie 不能用于这些接口；所有响应（含错误）带 no-store；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-104 记录段（三个接口、授权、字段、偏离、验证到什么程度，并写明退款接口留给 SHOP-TASK-105、前端 P13 与 P09 会员模式留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的文件、app/api/order_lookup.py、app/api/member_auth.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250），不合并、不直推
```

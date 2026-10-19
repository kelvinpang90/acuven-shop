# SHOP-TASK-108 会员中心的积分与优惠券查询接口

- [x] 提供 `GET /api/member/points` 与 `GET /api/member/coupons`：均以会员会话授权、只读且响应带 no-store。积分返回 `balance`、`pending`、`next_expiry` 与 `history`；优惠券返回全体会员相同的可用券（`code`、类型、金额或百分比、最低消费与有效截止时间）及仅当前会员的已核销记录（`code`、订单号、折扣）。内部无时区 UTC 时间在响应中明确为 UTC；最近积分到期日沿用 SHOP-TASK-095 的马来西亚日期。
- 偏离与取舍：无设计偏离；可用券不因总次数或当前会员已用次数耗尽而隐藏，符合 UX 0.10 Q19；不做 P13 前端页面，留给之后的前端任务。
- 验证到什么程度：`tests/test_member_account_api.py` 按 `tests/test_member_pw_api.py` 的 SQLite 内存库、StaticPool、外键检查、会话依赖与 HTTPS TestClient 方式准备真实会员会话；覆盖未登录与已撤销会话、仅本人积分流水与已核销券（排除本人的预占券）、空到期、共享且即使用尽仍可见的券、UTC 时间、no-store 与读取不写库（读取的会员、会话、订单、积分批次和流水、优惠券及用券记录全部列值不变）。每条测试说明守住的 DESIGN、UX 或 Kelvin 决定；由 PR 必需 CI 的 backend 执行，未在真实 MySQL 上验证。未加依赖。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/api/member_account.py（app/main.py 挂上路由）提供 GET /api/member/points 与 GET /api/member/coupons；路由器用 SHOP-TASK-027 的 _NoStoreRoute，这两个接口的处理函数与依赖产生的响应（含错误）都带 no-store；路径存在但方法不匹配时由框架返回的 405 不在此列；都经 require_member（无有效会员会话 401 member_session_required），只读、不写库、不提交、不要 CSRF；不写日志
      - GET /api/member/points 调用 SHOP-TASK-095 的积分概览与积分明细函数，返回 balance、pending、next_expiry（为 null 或含 points 与 date，date 为 YYYY-MM-DD 的马来西亚日期）与 history（每项 type 取 earned、redeemed、expired、returned、reversed 之一，以及 points、order_number、created_at，created_at 带 UTC 时区）
      - GET /api/member/coupons 返回 available（app/services/coupons.py 的 list_available_coupons，每项 code、type、amount_sen（percent 时为 null）、percent（fixed 时为 null）、min_spend_sen、valid_until，valid_until 带 UTC 时区）与 used（list_member_used_coupons，每项 code、order_number、discount_sen）；当前时间取不带时区的 UTC
      - tests/test_member_account_api.py 用 TestClient 与 SQLite 内存库，引擎、会话依赖与客户端照 tests/test_member_pw_api.py 的 engine、app、client 夹具（StaticPool 加 connect_args 关闭 check_same_thread、开启外键检查、base_url 为 https 的测试主机），不加依赖；已登录用 app/services/member_auth.py 的 issue_member_session 按当前时间签发真实会话、把 cookie 交给客户端，不覆盖 require_member
      - 上一条的测试验证意图——未登录与会话已撤销时两个接口都 401；积分接口的字段与类型、next_expiry 为 null 的情形、history 的 created_at 带时区且只含本人的流水；可用券对两个会员相同且含已用满的券；使用记录只含本人的已核销券；所有响应（含 401）带 no-store；两个接口都不写库；每条测试写明它守住的是设计、UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-108 记录段（两个接口的字段、偏离、验证到什么程度，并写明前端 P13 留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的文件、app/api/member_auth.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.46），不合并、不直推
```

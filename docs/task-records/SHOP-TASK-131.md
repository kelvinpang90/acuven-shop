# SHOP-TASK-131 批准退款后立即调用 Stripe 与手动重试接口

- [x] 按 `docs/DESIGN.md` 1.12（提交 `acf576e`）「Stripe 测试环境支付」第 8 条（提交后调用 Stripe 退款接口，以该记录的本站 ID 为幂等键；最新一次尝试为 failed 时管理员可手动重试，重试是新建一条尝试，受唯一约束，重复点击与并发只有一个成功，再以它的 ID 调用 Stripe；本站的批准结果不因 Stripe 退款失败而回滚）、`docs/HANDOFF.md` 0.54 第（1）项与 0.55 第（4）项、`docs/UX.md` 0.11 的 A03，在 `app/api/admin_refunds.py` 的批准接口接入提交后的调用，并新增手动重试接口。设计闸门：DESIGN 1.12（提交 `acf576e`）与 HANDOFF 0.55，退款。不做前端，不改设计。
- 批准后的调用（`POST /api/admin/refunds/{refund_id}/approve`）：
  - 经 `app/api/pay.py` 的 `GatewayDep` 取适配器；现有检查顺序、错误、提交与响应不变。`_review` 改为把 `approve_refund` / `reject_refund` 的两个返回值都交回处理函数，拒绝接口照旧只返回审核结果。
  - 只在 `approve_refund` 返回实际审核（第二个返回值为真）且提交之后，以 `app/services/stripe_refund.py` 新增的 `active_request_attempt_id(db, refund_request_id)`（只读，返回活动来源列等于该申请的尝试 ID，没有为 `None`）找到本次建的尝试，有则调用 `submit_refund_attempt`。没有尝试（模拟支付、现金退款额为 0、因合计上限或缺 PaymentIntent 没建成）与按审核幂等键重放时不调用 Stripe，重放时的尝试留给定时任务。
  - `submit_refund_attempt` 的任何结果（含 unavailable、rejected）都不改变 200 响应与内容；它抛出异常时回滚、记一条 error 日志 `stripe_refund_submit_failed attempt=<尝试 ID>`（只含类别与尝试 ID，不记异常内容），仍回 200 与审核结果，尝试保持 pending，由定时任务在下一轮发起。
- 手动重试（`POST /api/admin/refunds/{refund_id}/stripe-retry`，同一路由器，沿用 `_NoStoreRoute`）：
  - 不读请求体、不要 Idempotency-Key。检查顺序：`require_admin`（401 `admin_session_required`）→ `require_admin_csrf`（403 `csrf_failed`）→ 路径里的申请 ID（照 `_refund_id_errors`，422 不回显）→ `retry_request_attempt`。
  - 错误码：`RetryRequestNotFound` 404 `not_found`；`RefundRequestNotApproved` 409 `refund_not_approved`；`NoRefundAttempt` 与 `RetryNotAllowed`（含并发撞唯一约束）409 `stripe_refund_not_retryable`；`RefundCapExceeded` 409 `stripe_refund_cap`（照 `_conflict` 不带 status，告警日志已由服务层记）。每种错误都先 rollback。
  - 成功时在同一事务里经 `record_audit` 写审计后提交，再照批准的写法调用 `submit_refund_attempt`（结果与异常同样不改变响应），回 200 `{"result": "retried"}`；响应不带 Stripe 退款 ID、错误类别或其他 Stripe 内容，页面随后重新读取详情（UX 0.11 A03）。
- 审计：操作名 `admin_stripe_refund_retried`（常量 `ADMIN_STRIPE_REFUND_RETRIED` 加在 `app/services/stripe_refund.py`），对象类别沿用 `app/services/refund_review.py` 的 `AUDIT_TARGET_REFUND_REQUEST`，对象 ID 为申请 ID，旧值为空，新值为新尝试 ID 的十进制字符串。
- 日志：接口只记上述 `stripe_refund_submit_failed`；服务层按设计记的告警日志（`stripe_refund_rejected`、`stripe_refund_cap` 等）照旧由服务层记。日志类别常量 `LOG_SUBMIT_FAILED` 放在 `app/api/admin_refunds.py`（`stripe_refund.py` 只许新增查询函数与审计常量）。
- 偏离与按验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 重试接口在 CSRF 之后才校验路径里的申请 ID，所以路径参数按字符串接收、在处理函数里调用 `_refund_id`，与批准、拒绝的写法一致，而不是用 `RefundIdDep`（依赖会先于 CSRF 校验）。
  - `retry_request_attempt` 在数据有误时抛的 `RuntimeError` 不在验收标准的错误表里，接口不另行处理。
  - 捕获 `submit_refund_attempt` 的异常用 `except Exception`，日志不带异常信息（`exc_info`），以免把 Stripe 内容写进日志（DESIGN 1.12 第 10 条）。
- 测试（新文件 `tests/test_admin_refund_stripe_api.py`，TestClient 与 SQLite 内存库，夹具与后台登录照 `tests/test_admin_refunds.py`，Stripe 付款的会话记录照 `tests/test_stripe_refund_approval.py` 的 `_stripe_paid`；以 `app.state.stripe_gateway` 注入 `FakeStripeGateway`，以 monkeypatch 固定 `app.api.admin_refunds._utcnow`）：批准 Stripe 付款订单后适配器恰好收到一次 `create_refund`，幂等键与元数据为新尝试 ID、金额为申请的现金退款额，尝试按结果写入；适配器回 unavailable 或 rejected 时批准仍为 200 且内容相同，尝试分别保持 pending 与记为 failed；同键重放、模拟支付订单与因合计上限没建成尝试时不调用适配器；适配器替身抛出异常时仍为 200、批准已提交、恰有一条 `stripe_refund_submit_failed` 日志；最新为 failed 时重试 200、多一条 pending 尝试、审计一条且新值为新尝试 ID、适配器以新 ID 调用；重试后调用抛出异常时仍 200；最新为 pending 或 succeeded、连续两次重试的第二次、没有尝试、未批准、超额、申请不存在分别回对应错误且不写库、不调用适配器；未登录 401、缺或错 CSRF 403（都先于路径校验）、非法 ID 422 且不回显；所有响应（含错误）带 no-store。每条测试的文档字符串写明它守住的设计或 UX 原句。
- `tests/test_admin_refunds.py` 只改 `test_routes_use_internal_id_only` 一条：穷举的路由加入重试接口（文档字符串写明原因），查询参数仍只有 lang；该文件其余测试一字不改（现有批准都经模拟支付，不调用 Stripe，`test_writes_no_log_records` 不受影响）。
- 改动只在 `app/api/admin_refunds.py`、`app/services/stripe_refund.py`（只新增 `active_request_attempt_id` 与审计常量，现有函数未改）、`tests/test_admin_refund_stripe_api.py`、`tests/test_admin_refunds.py` 与本文件；未改 `app/services/refund_review.py`、`app/services/admin_refunds.py`、`app/services/stripe_refund_overdue.py`、`app/jobs.py`、模型、迁移、`.platform/`、CI、部署配置、前端与 `docs/TODO.md`，未装依赖。
- 留给之后的任务：A03 页面（付款方式、期限提示、尝试表、重试按钮、重试成功后重新读取与被合计上限拒绝时的提示）留给之后的前端任务。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.12 第 8 条、HANDOFF 0.54 第（1）项与 0.55 第（4）项及 UX 0.11 A03 自查，并核对 `retry_request_attempt`、`submit_refund_attempt` 的事务约定（调用方不得持有锁或未提交的改动：两处调用都在提交之后）。SQLite 不支持行锁，并发下的唯一约束只以连续两次重试与服务层既有测试覆盖。新增测试与已有后端测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - POST /api/admin/refunds/{refund_id}/approve 经 app/api/pay.py 的 GatewayDep 取适配器，现有检查顺序、错误、提交与响应不变；只在 approve_refund 返回实际审核（第二个返回值为真）且提交之后，以 app/services/stripe_refund.py 新增的公开函数（入参数据库会话与退款申请 ID，只读，返回活动来源列等于该申请的尝试 ID，没有为 None）找到本次建的尝试，有则调用 submit_refund_attempt；没有尝试（模拟支付、现金退款额为 0、因合计上限或缺 PaymentIntent 没建成）与按审核幂等键重放时不调用 Stripe（重放时的尝试留给定时任务）；拒绝接口不变
      - submit_refund_attempt 的任何结果（含 unavailable、rejected）都不改变批准的 200 响应与内容；它抛出异常时回滚、记一条 error 日志（只含类别 stripe_refund_submit_failed 与尝试 ID）后仍回 200 与审核结果，由定时任务在下一轮发起；接口除这一条外不写日志（服务层按设计记的告警日志除外）
      - 新增 POST /api/admin/refunds/{refund_id}/stripe-retry（同一路由器，沿用 _NoStoreRoute；这些接口的处理函数与依赖产生的响应（含错误）都带 no-store，路径存在而方法不匹配时由框架返回的 405 不在此列），不读请求体、不要 Idempotency-Key；检查顺序 require_admin（401 admin_session_required）→ require_admin_csrf（403 csrf_failed）→ 路径里的申请 ID（照 _refund_id_errors，422 不回显）→ retry_request_attempt；RetryRequestNotFound 回 404 not_found，RefundRequestNotApproved 回 409 refund_not_approved，NoRefundAttempt 与 RetryNotAllowed（含并发撞唯一约束）回 409 stripe_refund_not_retryable，RefundCapExceeded 回 409 stripe_refund_cap（照 _conflict 不带 status，告警日志已由服务层记）；每种错误都先 rollback
      - 重试成功时在同一事务里经 record_audit 写操作 admin_stripe_refund_retried（常量加在 app/services/stripe_refund.py；对象类别沿用 app/services/refund_review.py 的 AUDIT_TARGET_REFUND_REQUEST，对象 ID 为申请 ID，旧值为空，新值为新尝试 ID 的十进制字符串）后提交，再照第 2 条调用 submit_refund_attempt（结果与异常同样不改变响应），回 200 {"result":"retried"}；响应不带 Stripe 退款 ID、错误类别或其他 Stripe 内容，页面随后重新读取详情
      - 测试验证意图——新文件 tests/test_admin_refund_stripe_api.py 用 TestClient 与 SQLite 内存库（夹具与后台登录照 tests/test_admin_refunds.py，Stripe 付款的会话记录照 tests/test_stripe_refund_approval.py 的 _stripe_paid），以 app.state.stripe_gateway 注入 FakeStripeGateway，并以 monkeypatch 固定 app.api.admin_refunds 的 _utcnow，验证——批准 Stripe 付款订单后适配器恰好收到一次 create_refund，幂等键为新尝试 ID、金额为申请的现金退款额，尝试按结果写入；适配器回 unavailable 或 rejected 时批准仍为 200 且内容相同，尝试分别保持 pending 与记为 failed；同键重放与模拟支付订单不调用适配器；适配器替身抛出异常时仍为 200、批准已提交、恰有一条 stripe_refund_submit_failed 日志；最新为 failed 时重试 200、多一条 pending 尝试、审计一条且新值为新尝试 ID、适配器以新 ID 调用；最新为 pending 或 succeeded、没有尝试、未批准、超额、申请不存在分别回对应错误且不写库、不调用适配器；未登录 401、缺 CSRF 403、非法 ID 422 且不回显；所有响应（含错误）带 no-store；每条测试写明它守住的是设计或 UX 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - tests/test_admin_refunds.py 只改 test_routes_use_internal_id_only 一条并写明原因——穷举的路由加入重试接口，查询参数仍只有 lang；该文件其余测试（含 test_writes_no_log_records，现有批准都经模拟支付，不调用 Stripe）一字不改并照常通过
      - docs/task-records/SHOP-TASK-131.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；批准后的调用、重试接口与错误码、审计、偏离、验证到什么程度，并写明 A03 页面留给之后的任务），不改 docs/TODO.md，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；app/services/stripe_refund.py 只新增查询函数与审计常量，现有函数不改；不改 app/services/refund_review.py、app/services/admin_refunds.py、app/services/stripe_refund_overdue.py、app/jobs.py、模型、迁移、.platform/、CI、部署配置与前端；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.55），不合并、不直推
```

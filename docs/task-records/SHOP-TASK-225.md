# SHOP-TASK-225 后台 Stripe 待处理的列表、作废与退款重试接口

- [x] 依据：`docs/DESIGN.md` 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条；`docs/HANDOFF.md` 0.90 记录的运营者落实写法（人工作废的审计经 `on_void` 在作废的同一事务里写，confirmed 与 cancelled 的审计由接口之后另写）。设计闸门：DESIGN 1.22（提交 `ffb043e`）「Stripe 测试环境支付」第 11 条；只认管理员会话，写操作校验 CSRF，响应不含收货资料、Stripe 的任何 ID 与客户资料。依赖 SHOP-TASK-223（`void_alerted_checkout`）与 SHOP-TASK-224（`retry_checkout_attempt`、`stripe_pending`）。不改服务层规则、前端、数据库与迁移、设计，不加依赖。
- [x] `app/api/admin_stripe_pending.py`：新文件，路由前缀 `/api/admin/stripe-pending`，路由类为 `app/api/order_lookup.py` 的 `_NoStoreRoute`。
  - `GET ""`：`require_admin`（401 `admin_session_required`）；`csrf_token_for_cookie` 算出令牌（算不出时 401 `admin_session_required`，照 `admin_coupons.py` 的列表接口），再调用 `stripe_pending(db)`，返回 `checkouts`、`refunds`（原样取自 `StripePendingOut`）与 `csrf_token`；时间为服务层给出的带 UTC 时区的时间，照现有后台接口序列化。不要求 CSRF、不写审计。
  - `POST /checkouts/{checkout_id}/void`：原始请求体以依赖经 `pay._json_body` 读取（413、415，先于 `require_admin`，同 `admin_refunds.py` 的审核接口）→ `require_admin`（401）→ `require_admin_csrf`（403）→ 路径里的记录 ID（`[1-9][0-9]{0,9}` 且不超过 2³¹−1）与请求体 `VoidIn`（`extra="forbid"`、`strict=True`、`verified: Literal[True]`）一并校验，422 照 `_body_errors` 不回显请求内容 → `void_alerted_checkout`（适配器经 `GatewayDep`）。`CheckoutNotFound` 404 `not_found`；changed 409 `stripe_pending_changed`（`pay._conflict`）；unavailable 503 `stripe_unavailable`（`pay._stripe_unavailable`）；confirmed、cancelled、voided 200 `{"result": 结果}`。
  - 作废的审计 `stripe_checkout_voided`（`target_type` 为 `stripe_checkout`、`target_id` 为记录 ID、旧值为空、新值为结果）：voided 由传给服务的 `on_void` 在作废的同一事务里写；confirmed 与 cancelled 在服务返回之后另开事务写并提交；changed 与 unavailable 不写。
  - `POST /checkouts/{checkout_id}/refund-retry`：照 `admin_refunds.py` 的 stripe-retry，不读请求体、不要幂等键；`require_admin`（401）→ `require_admin_csrf`（403）→ 路径（422）→ `retry_checkout_attempt`。`RetryCheckoutNotFound` 404 `not_found`，`NoRefundAttempt` 与 `RetryNotAllowed` 409 `stripe_refund_not_retryable`，`RefundCapExceeded` 409 `stripe_refund_cap`，每种错误先回滚。成功时同一事务写审计 `stripe_late_refund_retried`（`target_type` 为 `stripe_refund`、`target_id` 为新尝试 ID、旧值为空、新值为结账记录 ID 的十进制写法）并提交，再以 `submit_refund_attempt` 调用 Stripe；它抛异常时回滚并记 error 日志 `stripe_refund_submit_failed attempt=<ID>`（不记异常内容），仍回 200 `{"result": "retried"}`。
  - 除上述日志外不写日志；响应只有结果字符串、两类列表的对账字段与订单号、CSRF 令牌。
- [x] `app/main.py`：注册新路由（排在 `admin_refunds` 之后）。
- [x] `tests/test_admin_stripe_pending_api.py`：新文件，见下面「验证到什么程度」。

## 偏离与取舍，请审阅

- 作废接口的路径与请求体照 `admin_refunds.py` 的 `_review` 一并校验，两者都不合法时 422 同时列出（路径在前）；验收标准写的是「依次」，两者都在 CSRF 之后、服务层之前，顺序约束不变。
- 审计操作名与对象类别（`stripe_checkout_voided`、`stripe_late_refund_retried`、`stripe_checkout`、`stripe_refund`）作为常量定义在接口模块：服务层不写审计（SHOP-TASK-223、224 的约定），也就没有现成的常量可引用；对象类别的取值与告警的对象类别码相同。
- `on_void` 抛出时服务层回滚作废、异常原样传出，接口不另捕获（500）；confirmed 与 cancelled 之后写审计失败同样不捕获，此时确认或取消已提交、只缺审计。这两种情形只在数据库出错时出现，验收标准没写响应，按现有后台接口对未预期异常的处理。
- 列表的 `csrf_token` 先于查询算出（同 `admin_coupons.py`），算不出时不查询。

## 验证到什么程度

- 人工逐条对照验收标准、DESIGN 1.22「Stripe 测试环境支付」第 11 条与 `admin_refunds.py` 的 stripe-retry、`admin_coupons.py` 的列表接口自查。
- `tests/test_admin_stripe_pending_api.py`（TestClient、SQLite 内存库、`FakeStripeGateway`、`ops_alert.alert` 以 monkeypatch 替身记录）覆盖：
  - 路由：前缀与三个接口；三个接口的错误响应带 no-store（成功与各错误响应也逐条断言 no-store）。
  - 列表：200 的三个字段与内容（时间带 `Z`）、令牌等于由 cookie 算出的值；不含收货资料、Stripe 的会话 / PaymentIntent / 退款 ID、付款页地址与创建幂等键；不写审计、不写日志；没有会话 401；令牌算不出 401；这两种 401 不调用服务层。
  - 作废：creating 记录 voided（不调用 Stripe，订单以 admin 取消、库存加回，审计一条）；写审计抛出时作废随之回滚（同一事务）；Stripe 已付款 confirmed 与会话作废后 cancelled 各写一条审计；cancelled 之后写审计抛出时取消已提交（另开事务）；不再满足条件（未告警、记录已 expired、订单已取消）409 `stripe_pending_changed`、记录已 paid 而 Stripe 不可用 503 `stripe_unavailable`，都不写库、不写审计；记录不存在 404；请求体缺 `verified`、不为 `true`（含 `false`、字符串、1、1.0、null；`verified` 用严格布尔再要求为 `true`，因 `Literal[True]` 按相等比较会放过 1）、多余字段、不是对象都是 422 且不回显；路径非法 422 不回显；路径与请求体同时非法时一并列出；401 先于 CSRF 与路径、403 先于路径与请求体、413 与 415 先于会话检查，这些情形都不调用服务层。
  - 重试：成功时新尝试与审计在调用 Stripe 时已提交，幂等键为新尝试 ID，响应不含 PaymentIntent ID，接口不写日志；适配器抛异常仍 200、尝试与审计已提交、恰一条 `stripe_refund_submit_failed attempt=<ID>` 且不含异常内容；最新一次为 pending 或 succeeded、连续两次重试的第二次、没有尝试都是 409 `stripe_refund_not_retryable`；超额 409 `stripe_refund_cap` 且服务层告警一次；记录不存在 404；401、403 与路径 422 的顺序且不调用服务层。错误时都不写库、不调用 Stripe。
  - 每条测试的文档字符串写明它守住的第 11 条哪一句，只出自本任务验收标准的约定注「验收：」。

## 登记时的验收标准（原文）

2026-11-10 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 路由——app/api/admin_stripe_pending.py 的路由前缀为 /api/admin/stripe-pending，用 app/api/order_lookup.py 的 _NoStoreRoute，在 app/main.py 注册；这些接口的处理函数与依赖产生的全部响应（含错误）都带 Cache-Control 为 no-store，路径存在但方法不匹配时由框架返回的 405 不在此列
      - 列表——GET 经 require_admin（401 admin_session_required），不要求 CSRF、不写审计；200 返回 checkouts、refunds 与 csrf_token 三个字段：前两个原样取自 SHOP-TASK-224 的 stripe_pending(db)（StripePendingOut 的两个列表），csrf_token 照 app/api/admin_coupons.py 列表接口的写法由 csrf_token_for_cookie 算出（算不出时 401 admin_session_required），供页面的作废与重试请求使用；时间照现有后台接口的写法序列化
      - 作废——POST …/checkouts/{checkout_id}/void 依次 require_admin（401）、require_admin_csrf（403）、路径里的记录 ID（不带符号与前导零的十进制整数，否则 422）、请求体的结构（422，错误写法沿用 app/api/pay.py 的 _body_errors，不回显请求内容），之后才调用 void_alerted_checkout；原始请求体照 app/api/admin_refunds.py 审核接口的写法以依赖经 app/api/pay.py 的 _json_body 读取（超过上限 413、不是 JSON 415，这一步作为依赖先于 require_admin 执行，与审核接口相同）；记录不存在 404 not_found，changed 409 stripe_pending_changed，unavailable 503 stripe_unavailable，其余 200 与结果；Stripe 适配器经 app/api/pay.py 的 GatewayDep 取得
      - 作废的审计——审计 stripe_checkout_voided（target_type 为 stripe_checkout、target_id 为记录 ID、旧值为空、新值为结果）：voided 经 on_void 在作废的同一事务里写入（接口传入写审计的函数）；confirmed 与 cancelled 在服务返回之后另开事务写入并提交；changed 与 unavailable 不写审计
      - 重试——POST …/checkouts/{checkout_id}/refund-retry 依次 require_admin（401）、require_admin_csrf（403）、路径（422），不读请求体；RetryCheckoutNotFound 为 404 not_found，NoRefundAttempt 与 RetryNotAllowed 为 409 stripe_refund_not_retryable，RefundCapExceeded 为 409 stripe_refund_cap（告警已由服务层报），每种错误先回滚；成功时同一事务写审计 stripe_late_refund_retried（target_type 为 stripe_refund、target_id 为新尝试 ID、旧值为空、新值为结账记录 ID 的十进制写法）并提交，再以 submit_refund_attempt 调用 Stripe，它的结果不改变响应，抛异常时回滚并记一条 error 日志 stripe_refund_submit_failed attempt=<ID>（不记异常内容），200 {"result":"retried"}
      - 资料——响应与日志不含收货资料、会员资料、Stripe 的任何 ID、付款页地址与 Stripe 错误原文；除上述 stripe_refund_submit_failed 外不写日志
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_admin_stripe_pending_api.py 用 TestClient 与 FakeStripeGateway，覆盖三个接口的每个状态码与 detail、校验顺序（CSRF 或路径不通过时不调用服务层）、no-store、作废的审计只在三种结果时写且 voided 的审计与作废同一事务、重试的审计与提交后调用 Stripe、submit 抛异常仍回 200，以及响应不含收货资料与 Stripe ID；每条测试的文档字符串写明它守住的 DESIGN 1.22 第 11 条哪一句
      - docs/task-records/SHOP-TASK-225.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths；不改服务层规则、前端、数据库与迁移、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.22 提交 ffb043e），不合并、不直推
```

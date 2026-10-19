# SHOP-TASK-021 模拟支付、取消与支付超时 API

- [x] 按 `docs/DESIGN.md` 1.10（提交 `e3b3505`）「订单与退款状态」「计价、优惠、积分与库存」第 6 条、「失败、并发与重试」第 1、2 条、「数据模型」的 PaymentAttempt / OrderEvent 一行与「权限与资料保护」第 6、7 条，提供支付页 P06 与结果页 P07 所需的三个游客接口与可重复运行的超时取消函数：`app/services/payment.py`（支付、取消、超时取消与订单视图）、`app/api/pay.py`（接口，`app/main.py` 挂上路由）、`app/services/order_access.py` 新增只读的 `list_order_access`（已有函数行为未改，`tests/test_order_access.py` 未改）。测试见 `tests/test_demo_payment.py`。未加表或迁移，未加定时调度，未做页面，未改前端、CI、部署配置或设计。设计闸门：DESIGN 1.10（提交 `e3b3505`）。
- 接口与字段（三个接口都只凭 SHOP-TASK-019 的 cookie `__Host-shop_order_access` 与 `guest_checkout` 授权访问，不读会员会话，`lookup` 授权不能用；订单号不进任何路径或查询参数）：
  - `GET /api/pay/orders?lang=en|zh|ms`（默认 `en`，取值与目录接口相同）：`{"orders": [...], "csrf_token": "<由 cookie 重新算出>"}`，订单按授权到期时间从晚到早（同时到期按授权 ID 从大到小）。每张订单：`order_number`、`status`、`created_at`、`payment_expires_at`、`server_time`（时间都是带 `Z` 的 UTC）、`lines`（每行 `name`、`variant_label`、`quantity`、`unit_price_sen`、`line_subtotal_sen`，按请求语言取下单时的快照，按行序）、`subtotal_sen`、`shipping_fee_sen`、`total_sen`、`recipient`（`name`、`phone`、`country_code`、`region`、`address`、`postal_code` 原文；没有收货资料记录时为 null）、`last_payment`（最近一次模拟支付的 `method`、`result`、`created_at`，没有为 null）、`cancelled_by`（已取消时 `self` 为本人、`timeout` 为超时，否则 null）。返回前先对这些订单执行超时取消。带 `Cache-Control: no-store`。没有有效授权 401。
  - `POST /api/pay/attempts`：请求头 `Idempotency-Key`（规则同 SHOP-TASK-020：16 到 64 个字母、数字、连字符或下划线，只能出现一次）与 `X-CSRF-Token`；请求体只有 `order_number`、`method`（`card`、`bank`、`ewallet`，对应 `pay.method_card`、`pay.method_bank`、`pay.method_ewallet`，库里存为 `demo_card`、`demo_bank`、`demo_ewallet`）、`result`（`success`、`failure`，库里存为 `succeeded`、`failed`）。新写支付尝试 201，按幂等键重放 200；响应 `{"method", "result", "status", "paid_at"}`（重放时方式与结果是原来那次的，状态与支付时间是订单当前的），带 `no-store`。
  - `POST /api/pay/cancel`：请求头 `X-CSRF-Token`，不要求幂等键；请求体只有 `order_number`。200 `{"status": "demo_cancelled", "cancelled_by": "self" | "timeout"}`，带 `no-store`。
- 写接口的处理顺序（每一步不通过即停止，之前不读写订单数据）：逐块读请求体，超过 8 KB 即 413（先于一切校验，含带多余字段或没有 cookie 的超限请求体）→ 不是 `application/json` 即 415 → 幂等键（只有支付要）与请求体一并校验，多出字段（含卡号、账户等）、非法支付方式或结果即 422（`detail` 每项只有 `type`、`loc`、`msg`，不回显提交内容）→ 按订单号查订单 ID，以当前 cookie 与 `guest_checkout` 调用 `check_order_access` → `check_csrf_token` → 才查幂等键与订单状态。
- 错误码：401 `{"detail": "access_expired"}`（页面的 `pay.session_expired`；订单号格式不对或不存在、无 cookie、授权过期、不属于该会话、范围不符一律同一响应，带 `no-store`）；403 `{"detail": "csrf_failed"}`；409 `{"detail": "idempotency_conflict"}`，以及带订单当前状态的 `{"detail": "order_expired" | "order_not_payable" | "order_not_cancellable", "status": "<当前状态>"}`；415、413、422 同上。
- 状态与库存变动：
  - 支付幂等：指纹是 `{"method", "order_id", "result"}`（接口里的取值）的 JSON（键排序、无空白）的 SHA-256；先按幂等键查支付尝试，指纹相同（常量时间比较）返回原结果、不同 409，都不改订单。
  - 订单已支付及之后或已取消：409 `order_not_payable`，不写支付尝试。已过支付到期时间（`now >= payment_expires_at`）：先对该单执行超时取消，再 409 `order_expired`，不写支付尝试。
  - 失败：只写支付尝试，订单状态、库存与事件都不变，可再次提交。
  - 成功：经 `is_transition_allowed(awaiting_demo_payment, demo_paid, guest)` 检查后，同一事务里 `UPDATE orders SET status = 'demo_paid', paid_at = now WHERE id = ? AND status = 'awaiting_demo_payment' AND payment_expires_at > now`，写支付尝试与一条操作者 `guest` 的事件；库存不动（预留即被消耗）。UPDATE 未命中时回滚：同键的并发请求已提交则按幂等规则重放或冲突，否则 409 `order_not_payable` 与当前状态。写支付尝试撞上幂等键唯一约束时回滚，按幂等规则处理。
  - 取消：待支付且未到期时，经迁移判定后同一事务里带条件地改为 `demo_cancelled`（`status = 'awaiting_demo_payment' AND payment_expires_at > now`）、加回库存、写一条操作者 `guest` 的事件；未命中（并发先提交）时回滚后按当前状态回答。已取消直接返回当前状态，不再写事件或加回库存；已支付及之后 409 `order_not_cancellable`（按设计走退款）。
  - 加回库存：按订单行把件数加回 SKU 规格的当日可用库存，规格已删除（规格 ID 为空）的行跳过；同一规格的多行先合并件数，再按 SKU 规格 ID 从小到大逐个 `UPDATE product_variants SET available_stock = available_stock + n WHERE id = ?`，与 SHOP-TASK-020 扣库存的顺序一致。
- 超时取消函数 `expire_overdue_orders(db, now, *, order_ids=None, limit=None) -> int`：选出状态为 `awaiting_demo_payment` 且 `payment_expires_at <= now` 的订单（可限定订单 ID 集合与单次上限，按到期时间从早到晚），逐张在各自的事务里经迁移判定后带条件地改为 `demo_cancelled`（`status = 'awaiting_demo_payment' AND payment_expires_at <= now`）、加回库存、写一条操作者 `system` 的事件并提交；未命中即回滚跳过，所以同一订单只生效一次，重复运行或与取消、支付并发时不重复加回库存。返回本次取消的张数。docstring 写明按分钟定时运行由之后的定时任务负责；本任务只在查看、支付与取消接口里对涉及的订单调用它。
- 留给之后的任务：按分钟定时运行超时取消（定时任务）；会员凭会话访问 P06、P07（会员任务）；券与积分预占的释放（优惠券与积分任务扩展本任务的取消与超时路径，本任务未预留参数）；限流（本接口目前没有任何限流，留给统一处理公开接口限流的任务）；支付或取消后撤销该单的短期凭据（本任务不撤销，结果页仍凭它查看）。
- 偏离与取舍，请审阅（未改设计，未发现设计本身必须停下的问题）：
  - 设计闸门是 DESIGN 1.10（提交 `e3b3505`）；仓库里现为 1.11 候批稿，本任务涉及的「订单与退款状态」「计价、优惠、积分与库存」第 6 条、「失败、并发与重试」第 1、2 条与「权限与资料保护」第 6、7 条与 1.10 相同。字段参照 `docs/UX.md` 的 P06、P07，未发现与设计冲突之处；UX 没有为字段定名，字段名按设计与模型取。
  - 取消接口遇到已过支付到期时间的待支付订单时，先按超时取消（事件操作者 `system`），再返回 200 与 `cancelled_by: "timeout"`，而不是记为本人取消：验收只规定了支付与查看接口先做超时处理，这样三个接口对过期订单的处理一致，库存仍只加回一次。本人取消的带条件 UPDATE 也因此要求未到期。
  - 409 `order_expired`、`order_not_payable`、`order_not_cancellable` 另带 `status` 字段（验收要求条件 UPDATE 未命中时「返回 409 与订单当前状态」，三者统一这样做）；`idempotency_conflict` 只有 `detail`。条件 UPDATE 未命中的错误码用 `order_not_payable`。
  - 支付接口新写 201、重放 200，与下单接口一致。
  - 请求体里的订单号按字符串校验（1 到 16 个字符，多于 16 个 422）；格式不是 16 位大写 Crockford Base32 的不查库，直接与订单号不存在一样 401。
  - 查看接口的 `lang` 不用 FastAPI 的 `Literal` 查询参数校验，而是自己校验后 422：取值与默认值与目录接口相同，但 FastAPI 默认的 422 会回显所给的值，与「422 响应不回显请求内容」不符。目录接口本身未改。
  - 查看接口是 GET，却会执行超时取消（写库）：按验收「返回前先对这些订单执行下面的超时取消」。
  - 超时取消函数每取消一张就提交一次，未命中即回滚，一张失败不影响其他各张；调用方传入的会话因此会被提交或回滚。
  - `cancelled_by` 按取消事件的操作者：`system` 为 `timeout`，`guest`（以后的 `member`）为 `self`，对应 UX 的 `result.cancelled` 与 `result.cancelled_by_you`。
  - 支付与取消后不撤销 `guest_checkout` 授权：结果页 P07 仍凭它查看；撤销由之后的任务按需要实现。
  - 接口与服务都不写日志；异常与错误响应不含个人资料、订单号、幂等键或令牌。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_demo_payment.py` 用 TestClient 与 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开，会话依赖换成每个请求一个会话）按模型建表，订单都经 SHOP-TASK-020 的下单接口建立并从下单响应取得 cookie 与 CSRF 令牌，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：查看接口的字段、订单行快照（含按语言取与回退英文）、收货资料原文、CSRF 令牌由 cookie 算出、no-store；非法语言 422 不回显；多张订单按授权到期时间从晚到早（改授权到期时间后顺序随之改变）；另一个浏览器只看到自己的订单；最近一次模拟支付与取消方；查看时先对过期订单执行超时取消；无 cookie、格式不合法的 cookie、授权过期、只有 lookup 授权都 401 且响应相同；成功后状态、支付时间、事件与支付尝试各一条且库存不变；三种支付方式的存储值；失败只多一条支付尝试且可再试后成功；同键同请求重放 200 不重复写入，同键不同请求（结果或方式不同，或另一张订单）409；缺、错或属于别的浏览器的 CSRF 令牌 403；已支付后再付与取消 409；已取消后再付 409；过期后付款 409 `order_expired` 且订单变为已取消、库存加回、事件操作者 system；恰在到期时刻付款按过期处理；取消后库存加回、事件操作者 guest，重复取消不再加回；取消过期订单按超时取消；取消缺或错 CSRF 403；支付与取消在无 cookie、授权过期、lookup 授权、其他浏览器的 cookie、订单号不存在时都是同一个 401 且不改订单；同一会话已授权 A、未授权 B 时对 B 支付与取消都与无 cookie 的响应逐字节相同，B 的订单、库存、事件与支付尝试不变；订单行规格 ID 顺序与行序相反（另加同一规格的一行与规格为空的一行）时，以 SQL 执行事件钩子断言取消与超时取消加回库存的 UPDATE 按规格 ID 从小到大发出、同一规格合并件数、空规格跳过；超时取消函数重复运行只生效一次、未到期的订单不受影响、到期边界（前一刻不取消、恰在到期时刻取消）、订单 ID 集合与单次上限；并发以第二个数据库会话在条件 UPDATE 之前（或第一次按幂等键查询之后）提交另一操作模拟：先取消后到的成功请求、两笔不同幂等键的成功、超时取消与本人取消（两个先后）、超时取消与成功在到期时刻（两个先后）、两次超时取消同时运行，都断言最终状态唯一、库存恰好加回一次或不加回、支付尝试与状态事件条数；同一幂等键的失败请求插入撞上唯一约束后回滚并重放或返回 `idempotency_conflict`，同键的成功请求先提交时条件 UPDATE 未命中后重放；非 JSON 415（缺失、text/plain、表单）；超过 8 KB 先于其他校验 413（合法 JSON 带多余字段、缺幂等键、非 JSON、没有 cookie）；缺或非法 Idempotency-Key、非法支付方式或结果、缺字段、卡号、CVV、账户号码等多余字段 422 且请求体校验先于授权；取消缺订单号或带多余字段 422；这些错误响应都不回显请求内容；接口与超时取消函数不产生 `app` 日志记录。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；查询与带条件的 UPDATE 未在真 MySQL 上执行，真并发（两个连接、MySQL 的可重复读与行锁）只以上述 SQLite 交错模拟。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.10 上述各条一致；接口字段参照 docs/UX.md 0.6 的 P06、P07，与设计冲突时以设计为准并在 PR 里说明；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - 订单号不进任何路径或查询参数（docs/UX.md「阅读说明」）；三个接口都只凭 SHOP-TASK-019 的 cookie 与 guest_checkout 授权访问，授权不通过时一律 401（错误码 access_expired，对应页面的 pay.session_expired），不区分订单不存在、授权过期或不属于该单；lookup 授权不能用于本任务的任何接口；两个写接口按以下顺序处理，每一步不通过即停止且不读写订单数据：请求体大小与格式校验，按当前 cookie、请求里的订单号对应的订单 ID 与 guest_checkout 范围调用 SHOP-TASK-019 的逐单校验函数（订单号不存在、授权过期、不属于该会话或范围不符一律同一个 401），CSRF 校验，然后才查幂等键与订单状态；同一会话持有订单 A 的授权不能借请求体里的订单号操作订单 B
      - GET /api/pay/orders（语言参数与目录接口相同）返回本浏览器当前有效的 guest_checkout 授权所对应的订单，按授权到期时间从晚到早（即最近签发的在前）；每张订单含订单号、状态、创建时间、支付到期时间、服务器当前时间、按请求语言取的订单行快照（名称、规格说明、件数、单价、行小计）、商品小计、运费、应付、收货资料原文（姓名、电话、国家、地区、地址、邮编）、最近一次模拟支付的结果（没有为空）与已取消时的取消方（本人或超时）；另返回由 cookie 重新算出的 CSRF 令牌；没有有效授权时 401；响应带 Cache-Control 为 no-store；返回前先对这些订单执行下面的超时取消
      - POST /api/pay/attempts 请求体只有订单号、支付方式（card、bank、ewallet 三种，对应 pay.method_card、pay.method_bank、pay.method_ewallet）与结果（success、failure），不接受任何卡号或账户资料，多出字段 422；须带 Idempotency-Key（规则同 SHOP-TASK-020）与 X-CSRF-Token 请求头，CSRF 不通过 403（错误码 csrf_failed）；只接受 JSON，请求体上限 8 KB，规则同 SHOP-TASK-020
      - 支付尝试的幂等：指纹是订单 ID、支付方式与结果按固定规则序列化后的 SHA-256；先按幂等键查支付尝试，指纹相同即返回原结果、不同即 409（错误码 idempotency_conflict），都不再改订单；并发插入同键时以唯一约束为准，回滚后按同样规则处理
      - 订单为 awaiting_demo_payment 且当前时间早于支付到期时间时：成功即在同一事务里写支付尝试、用带条件的 UPDATE（状态仍为 awaiting_demo_payment 且未到期才改）把订单改为 demo_paid 并写支付时间、写一条操作者为 guest 的状态事件，库存不再变动（预留即被消耗）；失败只写支付尝试，订单状态、库存与事件都不变，可再次提交；状态迁移经 SHOP-TASK-010 的迁移判定函数检查
      - 订单已过支付到期时间时先按下面的超时取消处理，再返回 409（错误码 order_expired）且不写支付尝试；订单已是 demo_paid 或之后的状态、或已取消时返回 409（错误码 order_not_payable）且不写支付尝试；条件 UPDATE 未命中（并发的取消或另一笔成功先提交）时回滚并返回 409 与订单当前状态，不出现一单两次成功
      - POST /api/pay/cancel 请求体只有订单号，须带 X-CSRF-Token（不要求幂等键：重复取消已取消的订单直接返回当前状态，不再写事件或释放库存）；订单为 awaiting_demo_payment 时在同一事务里用带条件的 UPDATE 改为 demo_cancelled、按订单行把件数加回对应 SKU 规格的当日可用库存（规格已删除的行跳过；同一规格的多行先合并件数，再按 SKU 规格 ID 从小到大逐个更新，与 SHOP-TASK-020 扣减库存的顺序一致，避免与下单、库存重置互相死锁）、写一条操作者为 guest 的状态事件；已支付及之后的订单返回 409（错误码 order_not_cancellable），已支付订单按设计走退款
      - app/services/payment.py 提供超时取消函数：接收数据库会话与当前时间（可选限定订单 ID 集合与单次上限），把状态为 awaiting_demo_payment 且支付到期时间不晚于当前时间的订单逐张用带条件的 UPDATE 改为 demo_cancelled、加回库存（规则与上面的取消相同，按 SKU 规格 ID 从小到大）、写一条操作者为 system 的状态事件；同一订单只生效一次，重复运行或与取消、支付并发时不重复加回库存；返回取消的张数；docstring 写明按分钟定时运行由之后的定时任务负责
      - 列出本浏览器有效授权所需的查询可在 app/services/order_access.py 新增一个函数（只读，按会话与范围列出未撤销、未到期的授权），不改 SHOP-TASK-019 已有函数的行为，tests/test_order_access.py 不改动并照常通过
      - 券与积分预占的释放由之后的优惠券与积分任务扩展本任务的取消与超时路径，本任务不预留参数；会员凭会话访问 P06、P07 由之后的会员任务扩展
      - 接口不读会员会话；不写日志，或日志只记录不含个人资料、订单号、幂等键与令牌的事件；422 响应不回显请求内容；错误响应不含内部细节；限流不在本任务，PR 里如实说明
      - tests/test_demo_payment.py 用 TestClient 与 SQLite 内存库（开启外键检查，覆盖会话依赖）按模型建表，经 SHOP-TASK-020 的下单接口建单并取得 cookie 与 CSRF 令牌，验证：查看接口返回的字段、收货资料原文、多张订单按授权到期时间从晚到早、no-store；无 cookie、授权过期、lookup 授权、其他浏览器的 cookie 都 401 且响应相同；成功后状态、支付时间、事件与支付尝试各一条且库存不变；失败只多一条支付尝试且可再试后成功；同键同请求重放不重复写入，同键不同请求 409；缺或错 CSRF 403；已支付后再付与取消都 409；过期后付款返回 order_expired 且订单变为已取消、库存加回、事件操作者为 system；取消后库存加回、事件操作者为 guest，重复取消不再加回；超时取消函数重复运行只生效一次，未到期的订单不受影响；订单行的规格 ID 顺序与行序相反时，用 SQL 执行事件钩子断言取消与超时取消加回库存的 UPDATE 按规格 ID 从小到大发出；同一会话已授权订单 A、未授权订单 B 时对 B 支付与取消都返回与无 cookie 相同的 401，且 B 的订单、库存、事件与支付尝试都不变；用两个数据库会话与可控的交错（或在条件 UPDATE 前注入另一会话的提交）验证并发：先取消后到的成功请求、两笔不同幂等键的成功、超时取消与本人取消、超时取消与成功在到期时刻、两次超时取消同时运行，每种都断言最终状态唯一、库存恰好加回一次或不加回、支付尝试与状态事件条数正确；同一幂等键并发插入时遇唯一约束冲突后回滚，并按指纹重放或返回 idempotency_conflict；非 JSON 415、超过 8 KB 先于其他校验 413（含带多余字段的超限请求体）、缺或非法 Idempotency-Key 422、非法支付方式或结果 422、请求体带卡号等多余字段 422，这些错误响应都不回显请求内容；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-021 记录段（接口与字段、错误码、状态与库存变动、超时取消函数、偏离、验证到什么程度，并写明定时运行、会员访问与券积分释放留给之后的任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表、不加迁移、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.10，提交 e3b3505），不合并、不直推
```

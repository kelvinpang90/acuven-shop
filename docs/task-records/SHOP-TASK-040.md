# SHOP-TASK-040 退款审核规则与全部退款后冻结确认收货

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「订单与退款状态」（`requested` → 管理员 `approved` 或 `rejected`；已批准的退款不可再次批准；所有购买件数均已批准退款时冻结后续打包、发货与确认收货的推进，保留退款前履约状态；退款与履约状态分开存储）、「失败、并发与重试」第 1 条（审核使用幂等键和数据库唯一约束）与 `docs/HANDOFF.md` 0.33 记录的 Kelvin 2026-10-06 退款审核决定，提供批准与拒绝两个规则函数，并给访客确认收货补上全部退款后冻结与锁定：新文件 `app/services/refund_review.py`；改 `app/services/order_lookup.py`（确认收货的锁定与冻结、`lock_order_statement` 移到这里）、`app/api/order_lookup.py`（409 `fulfilment_frozen`）、`app/services/admin_orders.py`（推进发货锁定前先结束此前的事务）；测试见 `tests/test_refund_review.py`。未加后台接口（SHOP-TASK-041）、页面、积分返还与追回、自动确认收货（SHOP-TASK-042），未加表或迁移，未改 SHOP-TASK-029、035 的已有函数、前端、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 规则（`app/services/refund_review.py`）：`approve_refund` 与 `reject_refund` 接收数据库会话、申请 ID、管理员账号 ID、审核理由、审核幂等键与当前时间（不带时区的 UTC），返回 `(ReviewOutcome, 是否实际审核)`；`ReviewOutcome` 含申请 ID、状态、审核时间（带 UTC 时区）与理由（只给管理员看）。
  - 理由一律先去掉首尾空白，存去掉之后的文本。批准：去掉后为空串存空值，否则至多 500 个字符；拒绝：去掉后必须为 1 到 500 个字符。不合法抛 `ReviewReasonInvalid`，在碰数据库之前。
  - 请求指纹：`{"reason": 去掉空白后的理由（没有为空串）, "refund_request_id": 申请 ID, "result": "approved" 或 "rejected"}` 的 JSON（键排序、无空白、非 ASCII 转义）的 SHA-256 十六进制（`review_fingerprint`）。
  - 顺序：结束此前的事务并锁定所属订单行 → 申请不存在抛 `RefundRequestNotFound` → 按审核幂等键查申请，同键同指纹返回原结果（不改数据、不写审计），同键不同指纹抛 `IdempotencyConflict` → 申请不是 `requested` 抛 `RefundAlreadyReviewed`（带当前状态，不论结果、不论新键请求的是同一结果还是相反结果）→ 读取该申请各件的占用 → 保存点里写入。
  - 批准写状态、审核时间、审核人、审核幂等键与审核请求指纹，理由可空，各件保持占用；拒绝另写理由，并把该申请各件的占用标记置空释放这些件，之后可再申请。订单状态都不改。每次实际改变状态写一条审计（`record_audit`，操作名 `admin_refund_approved` / `admin_refund_rejected`，对象类别 `refund_request`、对象 ID 为申请 ID，旧值、新值为审核前后的状态，不写理由原文），操作名常量定义在本模块。只 flush、不提交，由调用方提交。
- 锁定协议（写在三个服务模块的 docstring 里）：锁定订单行之前先结束本请求此前的数据库事务（此前只有读取，`db.rollback()`），使 `SELECT … FOR UPDATE`（`with_for_update`）成为新事务的第一条语句——MySQL 默认的 REPEATABLE READ 下读取快照在第一条非锁定读时建立，这样锁定之后的读取（含 `refund_state`）能看到等锁期间别人提交的审核、发货或确认。审核、推进发货与访客确认收货三处都按此协议锁定同一订单行，因此相互串行。审核写入放在保存点（`begin_nested`）里：遇审核幂等键唯一约束冲突只回滚保存点，订单行的锁保持，再按键重新查询后按幂等规则返回（查不到时原样抛出）。
- 访客确认收货（`confirm_receipt`）：新顺序为结束此前的事务 → 锁定订单行 → 按幂等键查重放（同键同指纹返回原结果，同键不同指纹 409 `idempotency_conflict`，与原来相同）→ 不是 `demo_shipped` 仍 409 `order_not_confirmable` → 是 `demo_shipped` 时用 `refund_state` 判断，全部已退抛新的 `FulfilmentFrozen` → 原有的写记录、条件 UPDATE 与事件。接口把冻结映射为 409 `{"detail": "fulfilment_frozen", "status": 订单当前状态}`，响应照常 no-store；不写确认收货记录与事件，订单状态不变。其余行为与幂等重放不变。
- 推进发货（`advance_order`）：锁定前加 `db.rollback()` 结束此前的事务，其余不变。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题；Kelvin 2026-10-06 的决定与 DESIGN 1.11 的这几条一致）：
  - `lock_order_statement` 从 `app/services/admin_orders.py` 移到 `app/services/order_lookup.py`（`admin_orders` 已导入 `order_lookup`，反过来导入会循环），`admin_orders` 导入它使用，`admin_orders.lock_order_statement` 这个名字照常可用，SHOP-TASK-037 的测试不受影响。
  - 审核的锁定查询以子查询按申请 ID 找所属订单（`lock_order_for_request_statement`），不先单独读申请，否则那次读取会先建立快照。MySQL 上这条锁定读也会锁住该申请那一行，无害。
  - 唯一约束冲突后的按键重新查询用共享锁读取（`with_for_update(read=True)`）：冲突的同键审核在另一张订单上、在本事务的快照之后提交，普通读取看不到它，锁定读取读最新提交的版本。
  - 理由校验先于锁定与幂等查询：理由不合法时即使带的是已用过的键也回答理由不合法，而不是重放或冲突（合法的已存理由不会被判不合法，所以真正的重放不受影响）。
  - 申请为 `requested` 而它的某件占用标记不是 1 时抛 `RuntimeError`（SHOP-TASK-029 保证不会出现，视为数据错误）。
  - 规则函数抛异常时不在函数内回滚，锁到调用方结束事务时释放；确认收货的 409 路径也与原来一样不在函数内回滚，由接口会话关闭时回滚。
  - `order_lookup.FulfilmentFrozen` 是新异常类，与 `admin_orders.FulfilmentFrozen` 同名不同类，各自由所在接口映射为同一个错误码。
- 留给之后的任务：后台审核接口（SHOP-TASK-041，含管理员授权、CSRF、请求格式与提交）；发货满 7 天自动确认收货及其冻结（SHOP-TASK-042，按同一锁定协议）；积分返还与追回、会员订单由会员与积分账本任务接入；A03 退款审核页由后台页面任务实现。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查。`tests/test_refund_review.py` 用 TestClient 与 SQLite 内存库（StaticPool，每个连接打开外键检查并断言已打开；按 SQLAlchemy 文档关掉 pysqlite 的事务处理、由引擎发 BEGIN，与 `tests/test_jobs.py` 相同，使保存点与 MySQL 一致），自建管理员与订单，退款申请经 SHOP-TASK-029 的 `submit_refund` 提交，每条测试写明它守住的设计原句或 Kelvin 的哪一项决定，覆盖：批准后状态、审核人、审核时间、幂等键、指纹（另算一遍）与一条审计，件仍占用，订单状态与事件不变；批准时理由为空、空白或缺省存空值，两端空白被去掉，500 个字符可存，超过 500 个字符被拒且数据不变；拒绝时缺省、空串、只有空白或超长被拒且数据不变，有理由时状态、理由与审计正确、件被释放，同样的件可再次申请；同键同请求（理由只差首尾空白）重放返回原结果、不重复写入；同键不同结果、不同理由或另一笔申请抛幂等冲突；用新键对已审核的申请（四种结果组合）抛已审核异常并带当前状态；申请不存在；审计与查单视图都不含理由原文；全部件批准后（一笔或两笔申请）访客确认收货 409 `fulfilment_frozen`、no-store、订单不变且无确认收货记录，只退一部分（其余未申请、审核中或被拒）时照常完成，非 `demo_shipped` 且全部已退时仍是 `order_not_confirmable`；用 MySQL 方言编译三处锁定订单的查询断言含 `FOR UPDATE`；用 SQLAlchemy 连接事件记录事务边界与语句，断言审核、确认收货与推进发货在已有只读事务时都先回滚、锁定查询是新事务的第一条语句、读取占用在锁定之后；同一事务里模拟锁定后按键查不到、写入才撞上审核幂等键唯一约束时只回滚保存点（没有结束外层事务、只锁定一次），申请与件不变并抛幂等冲突。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；SQLite 不执行 `FOR UPDATE`，未在真 MySQL 上验证行锁、REPEATABLE READ 快照与并发。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「订单与退款状态」（requested 到 approved 或 rejected；已批准的退款不可再次批准；所有购买件数均已批准退款时冻结后续打包、发货与确认收货的推进，保留退款前履约状态；退款与履约状态分开存储）、「失败、并发与重试」以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/services/refund_review.py 提供批准与拒绝两个函数，接收数据库会话、申请 ID、管理员账号、审核理由、审核幂等键与当前时间（不带时区的 UTC）；先锁定该申请所属订单的订单行，锁定之后才读取申请与件的占用并判断；锁定协议：锁定订单行之前先结束本请求此前的数据库事务（此前只有读取，回滚即可），使 SELECT … FOR UPDATE（SQLAlchemy 的 with_for_update）成为新事务的第一条语句——MySQL 默认的 REPEATABLE READ 下读取快照在事务第一条非锁定读时建立，这样锁定之后的读取（含 SHOP-TASK-029 的 refund_state）才能看到等锁期间别人提交的审核、发货或确认；模块 docstring 写明这一协议；审核写入放在保存点里（begin_nested），遇审核幂等键唯一约束冲突只回滚保存点、订单行的锁保持；只 flush 不提交，由调用方提交
      - 审核理由一律先去掉首尾空白，存去掉之后的文本；批准：申请为 requested 时改为 approved，写审核时间、审核人、审核幂等键与审核请求指纹，审核理由可空（去掉空白后为空串即存空；非空时 1 到 500 个字符，否则抛理由不合法异常），各件保持占用；拒绝：审核理由去掉空白后必须为 1 到 500 个字符（否则抛理由不合法异常），申请改为 rejected，写审核时间、审核人、理由、审核幂等键与审核请求指纹，并把该申请各件的占用标记置空以释放这些件（SHOP-TASK-029 契约约定由审核任务置空）
      - 幂等（docs/DESIGN.md「失败、并发与重试」第 1 条）：请求指纹是申请 ID、审核结果与去掉空白后的理由按固定规则序列化后的 SHA-256 十六进制；锁定后先按审核幂等键查申请，同键且指纹相同即返回原结果、不改数据不写审计，同键而指纹不同抛幂等冲突异常；没有该键时，申请为 requested 才审核，已审核（不论结果）抛已审核异常并带当前状态；写入遇审核幂等键唯一约束冲突时回滚保存点并按键重新查询后按上述规则返回；申请不存在时抛不存在异常；订单状态在批准与拒绝时都不改；每次实际改变状态都用 SHOP-TASK-035 的审计函数写一条审计（操作名 admin_refund_approved 或 admin_refund_rejected，对象类别 refund_request、对象 ID 为申请 ID，旧值与新值为审核前后的状态，不写理由原文），操作名常量定义在本模块
      - 同一锁定协议补到已有的两处：app/services/admin_orders.py 中 SHOP-TASK-037 的推进发货改为锁定前先结束此前的事务、锁定为新事务的第一条语句；app/services/order_lookup.py 的访客确认收货原先没有锁定订单行，本任务加上，顺序为结束此前的事务、锁定订单行、按幂等键查重放（同键同指纹返回原结果，同键不同指纹 409 idempotency_conflict，与原来相同）、检查状态、检查全部已退；其余行为不变，SHOP-TASK-027、037 的测试照常通过；访客确认收货的冻结：锁定后先照旧检查状态（不是 demo_shipped 仍按 SHOP-TASK-027 返回 order_not_confirmable），状态是 demo_shipped 时再用 SHOP-TASK-029 的 refund_state 判断，全部已退时抛新的冻结异常，不写确认收货记录与事件；app/api/order_lookup.py 把它映射为 409（错误码 fulfilment_frozen）并带订单当前状态，响应照常 no-store；其余确认收货行为与幂等重放不变，SHOP-TASK-027 的测试照常通过
      - docstring 写明：积分返还与追回、会员订单由之后的会员与积分账本任务接入；理由只给管理员看（Kelvin 2026-10-06 决定），不出现在访客接口
      - tests/test_refund_review.py 用 SQLite 内存库（开启外键检查）与 TestClient，自建订单、管理员与退款申请，验证：批准后状态、审核人、审核时间、幂等键、指纹与审计各一，件仍占用；批准时理由为空存空、两端空白被去掉、超过 500 个字符被拒；拒绝时无理由或只有空白被拒且数据不变，有理由时状态、理由与审计正确且件被释放、可再次申请同样的件；同键同请求重放返回原结果且不重复写入，同键不同结果或不同理由抛幂等冲突；用新键对已审核的申请（同一结果或相反结果）都抛已审核异常；申请不存在；全部件都批准后访客确认收货返回 409 fulfilment_frozen 且订单不变，只退一部分时照常可确认；用 MySQL 方言编译审核、确认收货与推进发货时锁定订单的查询，断言含 FOR UPDATE；用 SQLAlchemy 的事件记录语句，断言这三处锁定订单的查询都是所在事务的第一条语句、读取占用发生在锁定之后；同一申请的幂等键冲突只回滚保存点；审计不含理由原文；每条测试写明它守住的是设计里哪一句或 Kelvin 的哪一项决定；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；docs/TODO.md 新增 SHOP-TASK-040 记录段（规则、锁定协议、冻结、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加表或迁移；不改 SHOP-TASK-029、035 已有函数的行为，它们的测试照常通过；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

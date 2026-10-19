# SHOP-TASK-039 退款申请的审核人与审核理由（数据模型）

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「数据模型」RefundRequest / RefundLine 一行（「申请状态……审核人及理由」）、「订单与退款状态」（`requested` → 管理员 `approved` 或 `rejected`）、「失败、并发与重试」第 1 条（退款申请/审核均使用幂等键和数据库唯一约束）与 `docs/HANDOFF.md` 0.33 记录的 Kelvin 2026-10-06 退款审核决定，给退款申请表加审核人、审核理由、审核幂等键与审核请求指纹四列并写迁移：`app/models/refund.py`、迁移 `alembic/versions/20261006_0015_refund_review.py`（revision `0015`，down_revision `0014`）；测试见 `tests/test_refund_review_models.py`。只加列与约束：未写批准、拒绝、冻结或接口逻辑，未做页面，未改 `app/main.py`、其他模型、已有迁移、前端、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）。拆分：规则与确认收货冻结由 SHOP-TASK-040、接口由 SHOP-TASK-041 交付。
- 新列（都可空，沿用 `app/db/base.py` 的基类、命名约定与 InnoDB、utf8mb4；`RefundRequest` 已有的列与约束未改）：
  - `reviewer_admin_id` Integer → `admin_accounts.id`（SHOP-TASK-034 的管理员账号表；`fk_refund_requests_reviewer_admin_id_admin_accounts`，ON DELETE RESTRICT），普通索引 `ix_refund_requests_reviewer_admin_id`。
  - `review_reason` String(500)：只给管理员看；拒绝时必填，批准时没有理由存空值。
  - `review_idempotency_key` String(64)：非空时全表唯一 `uq_refund_requests_review_idempotency_key`（MySQL 与 SQLite 的唯一约束都允许多个空值）。
  - `review_fingerprint` String(64)：审核请求内容的 SHA-256 十六进制，十六进制由写入方的 `hexdigest` 保证，库里只约束长度（沿用 `request_fingerprint_length` 的写法）。
- 新增的检查约束（只用比较、LENGTH、IN 与 IS NULL，SQLite 与 MySQL 都能执行）：
  - `ck_refund_requests_requested_has_no_review`：`status <> 'requested' OR (reviewer_admin_id IS NULL AND review_reason IS NULL AND review_idempotency_key IS NULL AND review_fingerprint IS NULL)`
  - `ck_refund_requests_reviewed_status_has_review`：`status NOT IN ('approved', 'rejected') OR (reviewer_admin_id IS NOT NULL AND review_idempotency_key IS NOT NULL AND review_fingerprint IS NOT NULL)`
  - `ck_refund_requests_rejected_has_review_reason`：`status <> 'rejected' OR review_reason IS NOT NULL`
  - `ck_refund_requests_review_reason_not_empty`：`review_reason IS NULL OR LENGTH(review_reason) >= 1`
  - `ck_refund_requests_review_idempotency_key_not_empty`：`review_idempotency_key IS NULL OR LENGTH(review_idempotency_key) >= 1`
  - `ck_refund_requests_review_fingerprint_length`：`review_fingerprint IS NULL OR LENGTH(review_fingerprint) = 64`
  - 审核理由与审核幂等键的上限只由列长保证（MySQL 的 LENGTH 按字节计，沿用 `app/models/order.py` 的做法）。
- 迁移 0015：upgrade 依次加四列、建索引、建外键（外键直接用先建的索引，与 0007 的 `orders.member_id` 相同）、建唯一约束与六条检查约束，约束名按命名约定逐个写出；对已有行直接加列（迁移前没有 `approved` 或 `rejected` 的申请，审核接口尚未存在；已有的 `requested` 申请四列都为空，满足新约束）。downgrade 按依赖倒序：先删六条检查约束（MySQL 不许删除仍被多列检查约束引用的列）与唯一约束，再删外键与索引，最后删四列。`alembic check` 不比较检查约束与表选项，模型与迁移两边已人工核对一致。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题；Kelvin 2026-10-06 的决定与 DESIGN 1.11 的这几条一致）：
  - 理由「去掉首尾空白后 1 到 500 个字符」：库里只保证不是空串，去空白与字符数上限由 SHOP-TASK-040 的写入方保证（MySQL 的 LENGTH 按字节计，不能在库里按字符数检查上限）；只含空白的理由库里不拒绝。
  - 新检查约束在模型里排在已有的 `reviewed_status_has_reviewed_at` 之后：SQLite 按定义顺序报第一条不成立的约束，`tests/test_refunds.py` 里缺审核时间的 `approved` / `rejected` 反例仍报原约束名，断言不必改。
  - 审核时间仍由已有的两条约束管，未与审核人合并成一条约束。
- 已有测试的改动（只按验收允许的范围，断言不动）：`tests/test_refunds.py` 的 `_review` 改状态时一并写审核人（库里没有管理员账号时先建一个）、审核幂等键、审核请求指纹，拒绝时写理由；`test_model_accepts_reviewed_member_and_released_rows` 先建管理员账号，再给 `approved` 与 `rejected` 两行补上这几列；两处都为此加了 `AdminAccount` 的导入。`tests/test_admin_orders.py` 的 `_add_refund` 对 `approved` 与 `rejected` 补上同样几列（审核人用库里唯一的管理员账号，没有时先建一个）。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，人工核对模型与迁移的列、约束名、约束表达式与索引一致。`tests/test_refund_review_models.py` 用 SQLite 内存库（每个连接打开外键检查并断言已打开）按模型建表，每条测试（参数化的是每个用例）写明它守住的设计原句或 Kelvin 2026-10-06 的哪一项决定，覆盖：合法的两笔 `requested`（审核幂等键都为空）、理由为空与非空的 `approved`、有理由的 `rejected` 都写得进去且求值辅助函数不报任何约束不成立；列长 500 / 64 / 64 与四列可空；`requested` 有审核人、理由、审核幂等键或审核请求指纹各一个反例；`approved` 与 `rejected` 各自缺审核人、审核幂等键或审核请求指纹的反例；`rejected` 无理由；`approved` 与 `rejected` 理由为空串；审核幂等键为空串；指纹长 63 与 65；重复的审核幂等键被唯一约束拒绝；审核人指向不存在的管理员被外键拒绝；删除被申请引用为审核人的管理员被 RESTRICT 拒绝。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；迁移的 upgrade head、downgrade base、再 upgrade head 与 `alembic check` 也由该检查在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「数据模型」RefundRequest / RefundLine 一行（申请状态、审核人及理由）、「订单与退款状态」以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 决定一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/refund.py 的退款申请模型加审核人（可空，外键指向 SHOP-TASK-034 的管理员账号表，RESTRICT，建索引）、审核理由（可空，列长 500）、审核幂等键（可空，1 到 64 个字符，非空时全表唯一）与审核请求指纹（可空，长 64 的 SHA-256 十六进制，由写入方的 hexdigest 保证十六进制，库里只约束长度，沿用 request_fingerprint_length 的写法）；沿用 app/db/base.py 的基类、约束命名约定与表选项，不改该文件里其他模型、列与已有约束的行为
      - 检查约束：状态为 requested 时审核人、审核理由、审核幂等键与审核请求指纹都为空；状态为 approved 或 rejected 时审核人、审核幂等键与审核请求指纹都非空；状态为 rejected 时审核理由非空；任何状态下审核理由要么为空、要么 LENGTH 不小于 1（批准时没有理由存空，不存空串）；审核幂等键非空时 LENGTH 不小于 1，审核请求指纹非空时 LENGTH 等于 64；审核理由上限只由列长保证（MySQL 的 LENGTH 按字节计，沿用 app/models/order.py 的做法）；检查约束只用比较、LENGTH、IN 与 IS NULL，SQLite 与 MySQL 都能执行
      - 新迁移文件 alembic/versions/20261006_0015_refund_review.py，revision 为 0015、down_revision 为 0014；upgrade 加四列、外键、索引、唯一约束与检查约束，downgrade 按依赖倒序撤销；约束名按命名约定逐个写出；对已有行可直接加列（迁移前不会有 approved 或 rejected 的申请，审核接口尚未存在）；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行；PR 正文列出新增的检查约束
      - tests/test_refund_review_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的 requested、approved（理由为空与非空各一）与 rejected 申请作对照，再验证每个新检查约束、唯一约束与外键各至少一个被拒的反例（requested 有审核人、理由、幂等键或指纹，approved 或 rejected 无审核人、幂等键或指纹，rejected 无理由，任何状态理由为空串，指纹长度不是 64，重复的审核幂等键，审核人指向不存在的管理员，删除被申请引用的管理员被拒）；每条测试写明它守住的是设计里哪一句或 Kelvin 的哪一项决定；tests/test_refunds.py 只许给直接写入 approved 或 rejected 申请的辅助函数 _review 与测试 test_model_accepts_reviewed_member_and_released_rows 补上审核人、拒绝理由、审核幂等键与审核请求指纹以满足新约束，断言不动，tests/test_admin_orders.py（SHOP-TASK-037）只许给自建 approved 或 rejected 退款申请的构造数据补上管理员、审核人、拒绝理由、审核幂等键与审核请求指纹，断言不动；其余已有测试文件不改并照常通过；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-039 记录段（新列与约束、迁移、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、不改已有迁移、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

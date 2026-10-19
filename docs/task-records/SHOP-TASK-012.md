# SHOP-TASK-012 会员、会话、短信验证记录与每日预算的数据模型

- [x] 按 `docs/DESIGN.md` 1.9（提交 `361d8bf`）「数据模型」的 Member / VerificationAttempt 一行及「失败、并发与重试」「权限与资料保护」「资料保留」建立四张表，并给订单表加会员 ID 与认领状态：`app/models/member.py`（`app/models/__init__.py` 导出 `Member`、`MemberSession`、`VerificationAttempt`、`SmsDailyUsage`）、`app/models/order.py`、迁移 `alembic/versions/20260930_0007_members.py`（revision `0007`，down_revision `0006`）；测试见 `tests/test_member_models.py`
- 四张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；检查约束只用比较、LENGTH、LIKE、IN / NOT IN 与 IS NULL；指向会员的外键一律 ON DELETE RESTRICT，会员行不物理删除）：
  - `members`：`phone` String(16)（可空，唯一 `uq_members_phone`，多个 NULL 可共存）、`password_hash` String(255)（可空）、`status` String(10)、`created_at`、`deleted_at`（可空）；只有这几列，不存其他个人资料。检查约束：
    - `ck_members_status_valid`：`status IN ('active', 'deleted')`
    - `ck_members_active_has_phone`：`status <> 'active' OR (phone IS NOT NULL AND deleted_at IS NULL)`
    - `ck_members_deleted_cleared`：`status <> 'deleted' OR (phone IS NULL AND password_hash IS NULL AND deleted_at IS NOT NULL)`
    - `ck_members_phone_format`：`phone IS NULL OR (phone LIKE '+%' AND LENGTH(phone) <= 16)`
  - `member_sessions`：`member_id` → `members.id`（RESTRICT）、`token_hash` String(64)（会话令牌的 SHA-256 十六进制摘要，唯一 `uq_member_sessions_token_hash`，令牌原文不入库）、`created_at`、`expires_at`（非空）、`revoked_at`（可空）。检查约束：`ck_member_sessions_token_hash_length`（`LENGTH(token_hash) = 64`）、`ck_member_sessions_expires_after_created`（`expires_at > created_at`）。
  - `verification_attempts`：`phone` String(16)、`purpose` String(20)、`status` String(16)、`provider_request_id` String(64)（可空，唯一 `uq_verification_attempts_provider_request_id`）、`created_at`、`updated_at`；普通索引 `ix_verification_attempts_phone_created_at`（`phone`, `created_at`）；不存验证码、IP 或其他资料。检查约束：
    - `ck_verification_attempts_phone_format`：`phone LIKE '+%' AND LENGTH(phone) <= 16`
    - `ck_verification_attempts_purpose_valid`：`purpose IN ('checkout', 'register', 'login', 'reset_password', 'delete_account')`
    - `ck_verification_attempts_status_valid`：`status IN ('sent', 'approved', 'rejected', 'undeliverable', 'suspended')`
    - `ck_verification_attempts_accepted_has_request_id`：`status NOT IN ('sent', 'approved', 'rejected') OR provider_request_id IS NOT NULL`
    - `ck_verification_attempts_suspended_no_request_id`：`status <> 'suspended' OR provider_request_id IS NULL`
    - `undeliverable` 的请求 ID 可有可无（提供方受理后无法送达时有，受理前即失败时没有）。
  - `sms_daily_usage`：`usage_date` Date（按马来西亚时间切日，唯一 `uq_sms_daily_usage_usage_date`）、`sent_count` Integer、`reserved_micro_usd` BigInteger、`settled_micro_usd` BigInteger（整数微美元，不用浮点或 Decimal）。检查约束：`ck_sms_daily_usage_sent_count_non_negative`、`ck_sms_daily_usage_reserved_micro_usd_non_negative`、`ck_sms_daily_usage_settled_micro_usd_non_negative`（各列 `>= 0`）。
  - 订单表新列：`member_id` Integer（可空，→ `members.id`，外键 `fk_orders_member_id_members` RESTRICT，普通索引 `ix_orders_member_id`）、`claim_status` String(16)（非空，Python 默认值 `open`）。检查约束：`ck_orders_claim_status_valid`（`claim_status IN ('open', 'claimed', 'not_claimable')`）、`ck_orders_member_claim_not_open`（`member_id IS NULL OR claim_status <> 'open'`）。订单表其他列与约束未改。
  - 除会员手机号、密码哈希、注销时间，会话撤销时间，验证记录的提供方请求 ID 与订单会员 ID 外，所有列非空。
  - 0007 的 upgrade 依次建 `members`、`member_sessions`、`verification_attempts`（及索引）、`sms_daily_usage`，再给 `orders` 加两列（认领状态先以服务端默认值 `open` 补齐已有行，再去掉默认值）、索引、外键与两条检查约束；downgrade 按依赖倒序撤销：先删两条检查约束、外键、索引与两列，再删四张表。
- 偏离：未改设计。说明几处取舍，请审阅：
  - 设计闸门写的是 DESIGN 1.9（提交 `361d8bf`）；仓库里现为 1.11 候批稿。1.10 只改 `Product` / `Variant` 一行；1.11 加短信验证开关（开关关闭时被拒的发送请求不写验证记录、注销可改以密码或会话确认），不改本任务各表的字段与约束，未发现冲突。开关本身（`SiteSetting`）不在本任务。
  - 订单表的两条认领状态检查约束在模型里挂在 `claim_status` 列上，而不是 `__table_args__`：`tests/test_order_models.py`（不在本任务可改范围）对 `Order.__table__.constraints` 的每条检查约束只用已有各列求值，表级约束引用新列会让该文件的对照测试报错。SQLite 允许列上的检查约束引用其他列，按模型建表照常生效；迁移在 MySQL 上用 `ALTER TABLE` 加成同名、同表达式的表级约束。代价是不能在 MySQL 上按模型 `create_all`（MySQL 不允许列上的检查约束引用其他列）；MySQL 的表一律只由迁移建。以后改 `tests/test_order_models.py` 的求值辅助函数时可把这两条移回 `__table_args__`，不改库。
  - 认领状态的默认值只在 Python 侧（`open`，即游客订单），迁移补齐已有行后去掉服务端默认值，与模型一致；会员下单忘写 `not_claimable` 时 `ck_orders_member_claim_not_open` 拒绝。
  - 「限流元数据」：设计的 VerificationAttempt 写有「限流元数据」，按验收标准只存号码、用途、状态与时间，不存 IP 或来源；按来源限流的短时计数在 Redis，由之后的短信服务任务实现。
  - 验收标准之外另加的约束：会话摘要长 64（`ck_member_sessions_token_hash_length`）。它由「SHA-256 十六进制摘要（64 个字符）」直接推出。
  - MySQL 默认排序规则不区分大小写、LENGTH 按字节计：状态、用途、认领状态的大小写，以及会话摘要为小写十六进制，都由写入方保证；手机号是否为合法 E.164 由写入方按 `app/services/phone.py` 校验，库里只保证以加号开头且不超过 16 个字符。
  - 每日用量的费用用 BigInteger：Integer 上限约 2147 美元，BigInteger 不会因预算调高而溢出。
- 待办：
  - 验证记录按设计「短信验证请求及发送记录短期保留用于防滥用」，本任务不做到期删除；删除由之后单独登记的任务（规划块的「短信验证记录到期删除」）在短信正式上线前完成。
  - 密码哈希、会话签发与到期时长、令牌生成与 cookie、短信发送与限流、每日预算的原子预占与结算、游客订单认领与注销都由之后的任务实现。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对 0007 的列类型、可空性、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_member_models.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表，先写入无密码与有密码的有效会员、已注销会员、两条会话、七条各状态的验证记录（含两条无请求 ID 的停发记录）、两天的每日用量（含全零），以及游客订单、会员订单、已认领订单与注销后会员 ID 已清空的订单作对照；再覆盖：每个检查约束至少一个反例（先在独立连接上对该行逐条求值该表全部检查约束，含挂在列上的，断言目标约束不成立，再断言写入被拒且报出的是不成立的约束之一），含未知会员状态、有效会员无手机号或有注销时间、已注销会员仍有手机号或密码哈希或无注销时间、手机号不以加号开头或超过 16 个字符、会话到期等于或早于创建、摘要不是 64 个字符、未知用途与状态、sent / approved / rejected 无请求 ID、suspended 有请求 ID、负的条数与两项费用、会员订单认领状态为 open、未知认领状态；设计指定可空之外的每一列写入 NULL 被非空约束拒绝；重复手机号、重复会话摘要、重复请求 ID、重复日期被唯一约束拒绝，多个已注销会员手机号都为空可共存；会话与订单引用不存在的会员、删除被订单或会话引用的会员被外键拒绝；不写认领状态的订单为 open，清空会员 ID 后认领标记保留；各表只有设计指定的列可空、列集合不含验证码、IP、令牌原文或其他个人资料，指向会员的外键都是 RESTRICT，手机号与创建时间索引、订单会员 ID 索引存在。每条测试写明它守住的设计原句。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.9「数据模型」Member / VerificationAttempt 一行、「失败、并发与重试」、「权限与资料保护」与「资料保留」一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/member.py 定义四个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项；app/models/__init__.py 导出它们；时间一律不带时区的 UTC；外键删除行为写明，一律 RESTRICT（会员行不物理删除）；除会员手机号、密码哈希、注销时间，会话撤销时间，验证记录的提供方请求 ID 与订单会员 ID 这几个设计指定可空的字段外，所有列一律非空（检查约束遇到空值会放行，非空约束不能省）
      - 会员表：手机号（规范化 E.164，可空，全表唯一）、密码哈希（可空，自动注册的会员起初没有密码）、状态（只允许 active、deleted）、创建时间、注销时间；检查约束保证 active 时手机号非空、注销时间为空，deleted 时手机号与密码哈希都为空、注销时间非空，手机号非空时以加号开头且不超过 16 个字符；不存其他任何个人资料
      - 会员会话表：所属会员、会话令牌的 SHA-256 十六进制摘要（64 个字符，全表唯一；令牌原文从不入库）、创建时间、到期时间（非空）、撤销时间（可空）；检查约束保证到期时间晚于创建时间；会话时长、令牌生成与 cookie 由之后的接口任务决定
      - 短信验证记录表：规范化 E.164 手机号（非空，同上检查）、用途（只允许 checkout、register、login、reset_password、delete_account）、状态（只允许 sent、approved、rejected、undeliverable、suspended）、提供方请求 ID（可空，非空时全表唯一）、创建时间与更新时间；检查约束保证 sent、approved、rejected 时提供方请求 ID 非空，suspended（本系统停发、未发出）时为空；按手机号与创建时间建索引，供之后判定「该号码刚遇到短信无法送达或停发」的降级与防滥用；不存验证码、IP 或其他资料
      - 验证记录按设计「短期保留用于防滥用」，本任务不做到期删除；在 docs/TODO.md 记录段与 PR 里写明删除由之后单独登记的任务在短信正式上线前完成
      - 短信每日用量表：日期（按马来西亚时间切日，全表唯一）、已发送条数、预占费用、已结算费用；费用一律整数微美元（百万分之一美元），不用浮点或 Decimal；检查约束保证三者不小于零；原子预占与结算由之后的短信服务任务实现
      - app/models/order.py 的订单模型加会员 ID（可空，外键指向会员，RESTRICT，建索引）与认领状态（非空，只允许 open、claimed、not_claimable，迁移中已有行默认 open）；检查约束保证会员 ID 非空时认领状态不是 open；认领状态是不含手机号的标记，注销清空会员 ID 后保留；不改订单表的其他列与约束
      - 新迁移文件 alembic/versions/20260930_0007_members.py，revision 为 0007、down_revision 为 0006，upgrade 建四张表并给订单表加两列、外键、索引与检查约束，downgrade 按依赖倒序撤销；约束名按命名约定逐个写出；迁移同以往一样只在 MySQL 上执行（部署与 CI），对已有订单表可直接加列、外键与检查约束，不要求能在 SQLite 上执行；模型里的检查约束表达式须 SQLite 与 MySQL 都支持，因为测试在 SQLite 上按模型建表；PR 正文逐表列出检查约束与表选项
      - tests/test_member_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的会员、会话、验证记录、每日用量与关联订单作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含设计指定可空之外的每个列写入空值被拒、active 会员无手机号、deleted 会员仍有手机号或密码哈希或无注销时间、手机号不以加号开头、重复手机号、多个 deleted 会员手机号都为空时可共存、重复会话摘要、到期不晚于创建、未知用途与状态、sent 无请求 ID、suspended 有请求 ID、重复请求 ID、重复日期、负的条数与费用、会员订单认领状态为 open、未知认领状态、删除被订单或会话引用的会员被拒）；每条测试写明它守住的是设计里哪一句
      - 迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异；以上测试与已有后端测试都由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-012 记录段（四张表与订单新列的关键约束、偏离、待办、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.9，提交 361d8bf），不合并、不直推
```

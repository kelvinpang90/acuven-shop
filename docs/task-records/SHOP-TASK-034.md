# SHOP-TASK-034 管理员账号、后台会话与审计记录的数据模型

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「数据模型」AdminAccount / AuditEvent 一行（「单一管理员、密码哈希、会话及后台操作记录」）、SiteSetting 一行（后台修改写入 `AuditEvent`：操作者、时间、新旧值）、「权限与资料保护」第 4–6 条与 `docs/HANDOFF.md` 记录的 Kelvin 2026-10-04 管理员登录决定，建立管理员账号、后台会话与审计记录三张表并写迁移：`app/models/admin.py`（`app/models/__init__.py` 导出 `AdminAccount`、`AdminSession`、`AuditEvent`）、迁移 `alembic/versions/20261004_0014_admin.py`（revision `0014`，down_revision `0013`）；测试见 `tests/test_admin_models.py`。只建表与约束：未写密码哈希、建账号命令、会话签发、锁定或登录逻辑，未加接口，未改 `app/main.py`、其他模型、已有迁移、前端、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 三张表（沿用 `app/db/base.py` 的基类、命名约定与 InnoDB、utf8mb4，迁移逐个写出同样的约束名；时间一律不带时区的 UTC；检查约束只用比较、LENGTH 与 IS NULL；指向账号的外键一律 ON DELETE RESTRICT）：
  - `admin_accounts`：`username` String(32)（唯一 `uq_admin_accounts_username`）、`password_hash` String(255)、`singleton_slot` Integer（Python 默认值 1，唯一 `uq_admin_accounts_singleton_slot`）、`created_at`、`password_updated_at`；全部非空，不存姓名、电话、邮箱或其他个人资料。检查约束：
    - `ck_admin_accounts_username_length`：`LENGTH(username) >= 3 AND LENGTH(username) <= 32`
    - `ck_admin_accounts_singleton_slot_one`：`singleton_slot = 1`
    - 单例槽只能是 1 且全表唯一，库里至多一个账号：两个建账号命令并发时，后插入的一个被 `uq_admin_accounts_singleton_slot` 拒绝。
  - `admin_sessions`：`admin_account_id` → `admin_accounts.id`（`fk_admin_sessions_admin_account_id_admin_accounts`，RESTRICT）、`token_hash` String(64)（会话令牌的 SHA-256 十六进制摘要，唯一 `uq_admin_sessions_token_hash`，令牌原文不入库）、`created_at`、`expires_at`、`revoked_at`（唯一可空列）。检查约束：`ck_admin_sessions_token_hash_length`（`LENGTH(token_hash) = 64`，不另加十六进制约束）、`ck_admin_sessions_expires_after_created`（`expires_at > created_at`）。
  - `audit_events`：`occurred_at`（普通索引 `ix_audit_events_occurred_at`）、`admin_account_id` → `admin_accounts.id`（`fk_audit_events_admin_account_id_admin_accounts`，RESTRICT，非空）、`action` String(40)、`target_type` String(20)（可空）、`target_id` Integer（可空）、`old_value` / `new_value` String(255)（各自可空）；不存来源地址、提交的用户名、密码或其他个人资料。检查约束：
    - `ck_audit_events_action_length`：`LENGTH(action) >= 1 AND LENGTH(action) <= 40`（不按枚举约束操作名）
    - `ck_audit_events_target_type_length`：`target_type IS NULL OR (LENGTH(target_type) >= 1 AND LENGTH(target_type) <= 20)`
    - `ck_audit_events_target_both_or_neither`：`(target_type IS NULL AND target_id IS NULL) OR (target_type IS NOT NULL AND target_id IS NOT NULL)`
    - 旧值与新值不加长度检查，上限只由列长保证（MySQL 的 LENGTH 按字节计，沿用 `app/models/order.py` 的做法）。
  - 会话与审计记录到账号的外键与 `member_sessions.member_id` 一样由 MySQL 自行建索引。0014 的 upgrade 依次建 `admin_accounts`、`admin_sessions`、`audit_events` 与其索引；downgrade 按依赖倒序先删索引与 `audit_events`，再删 `admin_sessions`、`admin_accounts`。`alembic check` 不比较检查约束与表选项，两边须人工核对一致。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题；Kelvin 2026-10-04 的决定与 DESIGN 1.11 的这几条一致）：
  - 用户名、操作名与对象类别的长度上限在库里以 LENGTH 检查：这三者由写入方保证为 ASCII（用户名只含小写字母、数字与下划线，后两者取自常量），MySQL 按字节计的 LENGTH 与字符数相同。会话摘要是否为小写十六进制、用户名的字符集、旧值与新值不含个人资料都由写入方保证。
  - 密码哈希只靠列长 255 限制长度，不另加检查约束（与 `members.password_hash` 相同）。
  - 单例槽只在 Python 侧默认 1，不设服务端默认值；`password_updated_at` 没有默认值，由建账号与重设密码的写入方写入。
- 留给之后的任务：密码哈希、建账号命令、后台会话的 30 天时长、令牌生成与 cookie、登录锁定与审计写入由 SHOP-TASK-035、036 实现；后台业务操作（含查看订单原始资料、站点设置修改）的审计由管理后台业务接口任务补。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「数据模型」AdminAccount / AuditEvent 一行、「权限与资料保护」第 4–6 条以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员登录决定一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/admin.py 定义三个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项，时间一律不带时区的 UTC，外键一律 RESTRICT；app/models/__init__.py 导出它们；除后台会话撤销时间与审计记录的对象类别、对象 ID、旧值、新值外所有列一律非空；检查约束只用比较、LENGTH、LIKE、IN / NOT IN 与 IS NULL，SQLite 与 MySQL 都能执行
      - 管理员账号表：用户名（3 到 32 个字符，全表唯一；只含小写字母、数字与下划线由写入方保证，库里检查长度）、密码哈希（非空，不超过 255 个字符）、创建时间、密码更新时间、单例槽（非空整数，检查约束只允许 1，全表唯一，使库里至多一个管理员账号：两个建账号命令并发时，后插入的一个被唯一约束拒绝）；不存姓名、电话、邮箱或其他个人资料
      - 后台会话表：所属管理员、会话令牌的 SHA-256 十六进制摘要（长 64，检查约束沿用 token_hash_length 只约束长度，数据库层不另加十六进制约束，全表唯一，令牌原文从不入库）、创建时间、到期时间、撤销时间（可空）；检查约束保证到期时间晚于创建时间；会话时长（30 天）、令牌生成与 cookie 由 SHOP-TASK-035、036 实现
      - 审计记录表：发生时间、操作的管理员（非空）、操作名（1 到 40 个字符；取值由写入方的常量决定，库里不按枚举约束，以便之后的业务接口任务增加操作而不改约束）、对象类别（可空，1 到 20 个字符）与对象 ID（可空整数），检查约束保证二者同时为空或同时非空；旧值与新值（各自可空，列长 255，供 docs/DESIGN.md 1.11「数据模型」SiteSetting 一行要求的「操作者、时间、新旧值」等修改记录使用；写入方保证不含个人资料；MySQL 的 LENGTH 按字节计，沿用 app/models/order.py 的做法，上限只由列长保证，库里不加长度上限检查）；按发生时间建索引；不存来源地址、提交的用户名、密码或其他个人资料
      - 新迁移文件 alembic/versions/20261004_0014_admin.py，revision 为 0014、down_revision 为 0013；upgrade 建三张表，downgrade 按依赖倒序删除；约束名按命名约定逐个写出；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行；PR 正文逐表列出检查约束与表选项
      - tests/test_admin_models.py 用 SQLite 内存库（开启外键检查）按模型建表，先写入合法的账号、会话与审计记录作对照，再验证每个检查约束、唯一约束与外键各至少一个被拒的反例（含每个非空列写入空值被拒、用户名过短与过长、重复用户名、重复会话摘要、到期不晚于创建、对象类别与对象 ID 只有一个为空、单例槽不为 1、第二个账号（单例槽重复）、删除被会话或审计记录引用的账号被拒）；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；docs/TODO.md 新增 SHOP-TASK-034 记录段（三张表的关键约束、偏离、验证到什么程度），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、不改其他模型与已有迁移、不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

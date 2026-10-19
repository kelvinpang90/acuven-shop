# SHOP-TASK-049 管理员以邮箱登录（存储、建账号命令与登录接口）

- [x] 按 `docs/HANDOFF.md` 0.37 记录的 Kelvin 2026-10-07 决定（唯一管理员以邮箱登录：列名与接口字段名仍叫 username，取值改为邮箱，列加长到 254 个字符；建账号时先去掉首尾空白并转小写再按常见邮箱格式校验；邮箱属个人资料），改账号表的登录名列与约束、建账号命令与登录接口：`app/models/admin.py`、新迁移 `alembic/versions/20261007_0017_admin_email.py`、`app/admin.py`、`app/api/admin_auth.py`；测试改动见 `tests/test_admin_cli.py`、`tests/test_admin_models.py`、`tests/test_admin_auth_api.py`、`tests/test_admin_auth.py`。未改登录、锁定、会话与审计的规则（`app/services/admin_auth.py` 不动），未改已有迁移、前端代码、`.platform/`、CI 或部署配置，未装依赖。设计闸门：`docs/HANDOFF.md` 0.37（Kelvin 2026-10-07 批准的管理员邮箱处理）。拆分：不拆（数据库、命令与接口三处必须一起改才能用邮箱建号并登录）。
- 列与约束：`USERNAME_MAX_LENGTH` 由 32 改为 254，列为 `String(254)`；检查约束 `ck_admin_accounts_username_length` 改为长度 3 到 254（下限不变）。唯一约束 `uq_admin_accounts_username` 不变（utf8mb4 下 254 个字符的索引前缀 1016 字节，在 InnoDB 的 3072 字节上限之内）。
- 迁移 0017（down_revision 0016）：upgrade 先删长度检查约束，把列改为 `VARCHAR(254)` 非空，再以同名重建为 3 到 254；downgrade 先用 `CHAR_LENGTH` 数超过 32 个字符的登录名，有则抛 `RuntimeError`（消息只给个数，不含登录名本身）、不截断，没有才删约束、改回 `VARCHAR(32)` 非空并以同名重建为 3 到 32。长度写成数字、不 import app，与 0014 的做法相同。alembic check 不比较检查约束，模型与迁移两边已人工对照为同一表达式。
- 邮箱格式与规范化（`app/admin.py` 的 `create`）：先用 `normalize_username`（去掉首尾空白、转小写，与登录时比对用的是同一个函数）规范化，再按 Kelvin 2026-10-07 的规则校验：只收 ASCII；本地部分 1 到 64 个字符，只含小写字母、数字与 `. _ % + -`，不以点开头或结尾、不含连续的点；域名至少两段，每段 1 到 63 个小写字母、数字或连字符且不以连字符开头或结尾，最后一段为 2 到 63 个字母；总长 6 到 254。通过后存规范化后的值。不合格时以原有的拒绝退出码 1 退出，错误输出为固定文字 `username must be an email address (6 to 254 ASCII characters)`，不回显所给的值；不读密码、不写入。只校验格式，不发邮件。帮助文字改为说明登录名是邮箱（参数名显示为 `email`）。reset-password 与其余行为不变。
- 接口上限（`app/api/admin_auth.py`）：登录请求体 `username` 上限由 64 改为 254 个字符，下限 1 不变；接口不校验邮箱格式，其余校验、检查顺序、锁定计数、错误码与响应不变。锁定计数仍经 SHOP-TASK-035 的 `login_identifier` 与 SHOP-TASK-026 的计数函数，Redis 键为摘要。本模块仍不写日志。
- 个人资料的处理（Kelvin 2026-10-07）：邮箱只存在管理员账号表；审计记录照旧不存提交的登录名；锁定计数的 Redis 键照旧是 `[来源, 规范化登录名]` 的 SHA-256 摘要，不含邮箱原文；建号命令的拒绝信息、迁移 downgrade 的报错与登录接口的错误响应都不含邮箱，本任务不加日志；只向已登录的管理员显示（会话接口的 `username`、A03 的审核人，均为已有行为，未改）；前台页面不涉及。
- 偏离与取舍，请审阅（未改需求与设计，未发现须停下的需求或设计问题）：
  - 「只收 ASCII」同时查所给的原值：个别非 ASCII 字符转小写后会变成 ASCII 字母（如开尔文符号 U+212A 转小写为 `k`），只查规范化后的值会放过它们，所以原值含非 ASCII 字符也拒绝。
  - 「最后一段至少 2 个字母」同时受「每段 1 到 63 个字符」约束，所以最后一段为 2 到 63 个字母。总长下限 6 与格式本身的最短形式（如 `a@b.co`）相同，照样检查。
  - 登录接口只放宽长度、不校验邮箱格式：格式不合的登录名查不到账号，照常 401 并计一次失败，与之前对任意用户名的处理相同。
  - 管理员账号尚未创建（HANDOFF 0.37），不提供修改已有账号登录名的命令；若库里已有按旧格式建的账号，upgrade 照常成功，它仍可按原登录名登录。
  - `tests/test_admin_auth_api.py` 原有的「64 个字符可作为合格请求体」的测试按验收「其余断言不动」保留未改；新增的 254 个字符正例覆盖新上限。
- 留给之后的任务：登录页 A01 的标签改为 `admin.email`（`admin.username` 停用）由 SHOP-TASK-050 实现，本任务不改前端与文案。
- 验证到什么程度：人工逐条对照验收标准与 HANDOFF 0.37 原句自查。测试用 SQLite 内存库与 TestClient，每条新增或改动的测试写明它守住的是 Kelvin 2026-10-07 的哪一项决定：`tests/test_admin_cli.py` 的登录名常量改为邮箱，邮箱反例（中文与非 ASCII 字母、转小写后变成 ASCII 的开尔文符号、缺 @、两个 @、连续的点、本地部分以点开头或结尾、空格与不允许的字符、本地部分 65 个字符、单段域名、空段、段以连字符开头或结尾、域名含下划线、64 个字符的段、最后一段只有 1 个字母或含数字、总长 255、空串与只有空白）都以拒绝退出码退出、提示须为邮箱且不回显所给的值，正例（大写与首尾空白被规范化后接受并存规范化后的值、总长 6 与 254、含 `. _ % + -` 与多段域名）可以；`tests/test_admin_models.py` 的超长反例改为 255 个字符；`tests/test_admin_auth_api.py` 的超长反例改为 255 个字符，新增 254 个字符的邮箱（含全大写写法）可登录的正例；`tests/test_admin_auth.py` 新增大小写与首尾空白不同的同一邮箱经 `login_identifier` 后落到同一个限流键、键里不含邮箱原文。这些测试与迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 及 alembic check 由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest 与迁移。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 按 docs/HANDOFF.md 0.37 记录的 Kelvin 2026-10-07 决定实现；发现决定或设计本身有问题时停下并在 PR 里说明，不自行改
      - app/models/admin.py 的登录名上限 USERNAME_MAX_LENGTH 改为 254，列为 String(254)，检查约束 ck_admin_accounts_username_length 改为长度 3 到 254（下限不变）；新迁移 alembic/versions/20261007_0017_admin_email.py，revision 为 0017、down_revision 为 0016，upgrade 把列改为 VARCHAR(254) 非空并以同名重建检查约束，downgrade 先确认没有超过 32 个字符的登录名（有则抛错并说明，不截断）再改回；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行；不改已有迁移
      - app/admin.py 的 create 先去掉首尾空白并转小写，再按邮箱格式校验，通过后存规范化后的值；格式为本地部分@域名，只收 ASCII，本地部分 1 到 64 个字符，只含小写字母、数字与 . _ % + -，不以点开头或结尾、不含连续的点；域名至少两段，每段 1 到 63 个字符的小写字母、数字或连字符且不以连字符开头或结尾，最后一段为至少 2 个字母；总长 6 到 254；不合格时以原有的拒绝退出码退出，错误输出说明须为邮箱，不回显所给的值；只校验格式，不发邮件；reset-password 与其余行为不变；帮助文字改为说明登录名为邮箱
      - app/api/admin_auth.py 登录请求体的 username 上限由 64 改为 254 个字符（下限 1 不变），其余校验、检查顺序、锁定计数、错误码与响应不变；锁定计数仍按 SHOP-TASK-035 的 login_identifier 与 SHOP-TASK-026 的计数函数（键为摘要，不含邮箱原文）；不写日志，或日志不含邮箱
      - 现有测试只许这样改，其余断言不动——tests/test_admin_cli.py 把测试用的登录名常量改为邮箱，把合法与不合法登录名的参数化测试改为邮箱规则的正反例（含大写与首尾空白被规范化后接受、非 ASCII、缺 @、连续的点、超长等被拒且不回显）；tests/test_admin_models.py 把「超过 32 个字符」的反例改为超过 254 个字符；tests/test_admin_auth_api.py 把 65 个字符的反例改为 255 个字符并加一条 254 个字符的邮箱可登录的正例；tests/test_admin_auth.py 只新增一条测试：大小写与首尾空白不同的同一邮箱经 login_identifier 后落到同一个限流键，且键里不含邮箱原文（守住 HANDOFF 0.37「锁定计数的 Redis 键不含邮箱原文」）；其他直接写入账号表的测试夹具（如 tests/test_admin_refunds.py 的审核人）不经建号命令、只受长度约束，不改
      - 每条新增或改动的测试写明它守住的是 Kelvin 的哪一项决定；用 SQLite 内存库与 TestClient，由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）；其他已有测试不改并照常通过
      - docs/TODO.md 新增 SHOP-TASK-049 记录段（列与约束、迁移、邮箱格式与规范化、接口上限、个人资料的处理、偏离、验证到什么程度，并写明登录页标签由 SHOP-TASK-050 改）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不改前端代码；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：docs/HANDOFF.md 0.37），不合并、不直推
```

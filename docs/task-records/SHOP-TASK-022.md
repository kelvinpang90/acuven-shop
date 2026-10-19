# SHOP-TASK-022 站点设置：短信验证开关的存储与读取

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`，批准见 `docs/HANDOFF.md`）「边界与原则」第 4 条与「数据模型」的 SiteSetting 一行，建立站点设置表并写迁移，提供每次都查库的读取函数与公开只读接口：`app/models/site.py`（`app/models/__init__.py` 导出 `SiteSetting`）、迁移 `alembic/versions/20261001_0010_site_settings.py`（revision `0010`，down_revision `0009`）、`app/services/site_settings.py`、`app/api/site_settings.py`（`app/main.py` 挂上路由）；测试见 `tests/test_site_settings.py`。未加修改开关的接口，未做后台页面或审计记录，未改前端、设计、CI 或部署配置。设计闸门：DESIGN 1.11（提交 `2d13250`）。
- 表与约束：`site_settings`（显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字）。列：`id` Integer 主键（`pk_site_settings`，不自增）、`sms_verification_enabled` Boolean 非空、服务端默认关闭（MySQL 上为 `DEFAULT false`）、`updated_at` DateTime 非空（不带时区的 UTC，模型写入与更新时自动取当前 UTC）。检查约束 `ck_site_settings_single_row`：`id = 1`，表因此至多一行。不加其他设置项。0010 的 upgrade 建表并写入 `id = 1`、开关关闭、更新时间为执行迁移时 UTC 的一行；downgrade 删表。
- 读取函数 `is_sms_verification_enabled(db) -> bool`：每次调用发一条 `SELECT sms_verification_enabled FROM site_settings WHERE id = 1`，只查列值、不加载 ORM 对象，没有进程内或 Redis 缓存；没有那一行时返回关闭。docstring 写明下单、发送短信、提交验证码与确认注销时都必须在当次请求里调用它、按当时的值判定，前台接口的值只用于显示。
- 接口 `GET /api/site-settings`：响应体恰好 `{"sms_verification_enabled": <布尔>}`，带 `Cache-Control: no-store`；不需登录，不读写 cookie，只发上述 SELECT；其他方法 405。未配置数据库时与其他接口一样回答 503。
- 修改开关留给之后的「管理后台业务接口」任务（站点设置部分），须在管理员登录与审计记录就绪后实现，并按设计把操作者、时间、新旧值写入 `AuditEvent`；本任务没有任何写开关的代码路径，开关只能由迁移写成关闭。
- 偏离：未改设计，未发现设计本身的问题。说明几处取舍，请审阅：
  - 设计说「每次判定都从数据库读取当前值」。读取函数每次都查库，但在同一个数据库事务里，MySQL 默认的可重复读隔离级别可能让同一事务后几次读取看到第一次读取时的快照；docstring 要求在处理该请求的事务里读取，不跨请求复用事务。现有会话依赖每个请求一个会话，符合这一点。
  - 验收标准之外另加的：主键列不自增（主键只能是 1，自增无意义）；未配置数据库时接口回答 503，不猜一个值。
  - 迁移写入那一行的更新时间取执行迁移时的 UTC，不用数据库函数，与模型「不带时区的 UTC」一致。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并核对 0010 的列类型、可空性、服务端默认值、约束名与表达式、表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束、服务端默认值与表选项，这三项只经人工核对）。`tests/test_site_settings.py` 用 SQLite 内存库与 TestClient，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：主键为 1 可写入、主键 0、2、-1 被检查约束拒绝；不给开关值时服务端默认关闭；两列写入 NULL 被拒；表只有主键、开关与更新时间三列；读取函数在开关开、关与没有那一行时的结果；连续读取之间改库（开、关、删行）每次读到新值且每次调用各发一条 SELECT；会话里已加载的对象是旧值时仍读到库里的新值；接口返回值随库中开关变化（含没有那一行）；响应只有这一个字段、带 `no-store`；带不带 cookie 结果相同、不设 cookie、只发 SELECT、写方法 405；未配置数据库时 503。这些测试与迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check` 只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；读取查询未在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.11「边界与原则」第 4 条及「数据模型」SiteSetting 一行一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/site.py 定义站点设置模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项；app/models/__init__.py 导出它；表只有一行，主键固定为 1（检查约束保证），列为短信验证开关（布尔，非空，服务端默认关闭）与更新时间（非空，不带时区的 UTC）；不加其他设置项
      - 新迁移文件 alembic/versions/20261001_0010_site_settings.py，revision 为 0010、down_revision 为 0009；upgrade 建表并写入主键为 1、开关关闭的一行，downgrade 删表；约束名按命名约定逐个写出；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行
      - app/services/site_settings.py 提供读取函数：接收数据库会话，每次调用都查询数据库、不做任何进程内或 Redis 缓存，返回开关是否开启；表里没有这一行时视为关闭；docstring 写明下单、发送短信、提交验证码与确认注销时都必须在当次请求里调用它，按当时的值判定
      - GET /api/site-settings 返回一个字段 sms_verification_enabled（布尔），响应带 Cache-Control 为 no-store；不需要登录，不读写 cookie；只发 SELECT；不返回其他内容；前台只用它决定显示，服务端判定不依赖前端
      - 不加修改开关的接口；修改由之后的管理后台业务接口任务在管理员登录与审计记录就绪后实现，本任务在 docs/TODO.md 记录段写明
      - tests/test_site_settings.py 用 SQLite 内存库与 TestClient 验证：主键不是 1 的行被检查约束拒绝；新建行的开关默认关闭；读取函数在有行与无行时的结果；连续两次读取之间改库，第二次读到新值（证明未缓存）；接口返回值随库中开关变化、响应只有这一个字段且带 no-store、不设 cookie；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-022 记录段（表与约束、读取函数、接口、偏离、验证到什么程度，并写明修改开关留给管理后台业务接口任务）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.11，提交 2d13250），不合并、不直推
```

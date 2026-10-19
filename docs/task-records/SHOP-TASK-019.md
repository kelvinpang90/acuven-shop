# SHOP-TASK-019 订单访问授权：服务端会话、授权记录、cookie 与 CSRF

- [x] 按 `docs/DESIGN.md` 1.10（提交 `e3b3505`）「权限与资料保护」第 6 条（游客短期凭据与查单授权）与第 7 条（日志）建立订单访问会话与按订单的授权两张表并写迁移，另提供签发授权、校验授权、设置 cookie 与算出、校验 CSRF 令牌的函数，供之后的游客下单、模拟支付与查单接口使用：`app/models/order_access.py`（`app/models/__init__.py` 导出 `OrderAccessSession`、`OrderAccessGrant`）、迁移 `alembic/versions/20261001_0009_order_access.py`（revision `0009`，down_revision `0008`）、`app/services/order_access.py`；测试见 `tests/test_order_access.py`。未加接口、未改 `app/main.py`、未建订单、未做查单或支付逻辑、未改前端、未改设计。设计闸门：DESIGN 1.10（提交 `e3b3505`）。
- 两张表（都显式 InnoDB、utf8mb4，约束名按 `app/db/base.py` 的命名约定，迁移逐个写出同样的名字；时间一律不带时区的 UTC；外键一律 ON DELETE RESTRICT；除撤销时间外所有列非空）：
  - `order_access_sessions`：`token_hash` String(64)（会话令牌的 SHA-256 小写十六进制摘要，唯一 `uq_order_access_sessions_token_hash`；令牌原文不入库）、`created_at`、`expires_at`、`revoked_at`（可空）；不存 IP、浏览器标识或任何个人资料。检查约束：
    - `ck_order_access_sessions_token_hash_length`：`LENGTH(token_hash) = 64`
    - `ck_order_access_sessions_expires_after_created`：`expires_at > created_at`
  - `order_access_grants`：`session_id` → `order_access_sessions.id`（`fk_order_access_grants_session_id_order_access_sessions`，RESTRICT）、`order_id` → `orders.id`（`fk_order_access_grants_order_id_orders`，RESTRICT，普通索引 `ix_order_access_grants_order_id`）、`scope` String(16)、`created_at`、`expires_at`、`revoked_at`（可空）；唯一 `uq_order_access_grants_session_id_order_id_scope`（`session_id`, `order_id`, `scope`，以会话 ID 打头，MySQL 用它作会话外键的索引）。检查约束：
    - `ck_order_access_grants_scope_valid`：`scope IN ('guest_checkout', 'lookup')`（`guest_checkout` 即游客短期凭据，`lookup` 即查单授权）
    - `ck_order_access_grants_expires_after_created`：`expires_at > created_at`
  - 0009 的 upgrade 依次建会话表、授权表与订单 ID 索引；downgrade 按依赖倒序先删授权表、再删会话表。订单 ID 索引不单独删：它是订单外键唯一可用的索引，MySQL 不许在外键仍在时删除，删表时一并删除。
- 令牌与 CSRF 规则：
  - 令牌：`secrets.token_urlsafe(32)`，32 字节（256 位）随机数的无填充 Base64url，恰好 43 个 `[A-Za-z0-9_-]` 字符。只有这种格式的 cookie 值才算格式合法；不合法的值校验时不查库直接不通过，签发时视同没有 cookie。
  - 签发 `issue_order_access(db, cookie_value, order_id, scope, now)`：cookie 值格式合法且对应会话未撤销、未到期（`expires_at > now`）时沿用，否则新建会话（创建时间 `now`，到期 `now + 30 分钟`）。授权有效期固定 30 分钟：同一会话、订单与范围已有授权时把到期时间改为 `now + 30 分钟`、清空撤销时间（创建时间不变），否则新建；会话到期时间改为它与新授权到期时间中较晚者。返回 `IssuedAccess(new_token, csrf_token, expires_at)`，沿用会话时 `new_token` 为空；两个令牌字段不进 `repr`。只 flush、不提交。
  - 校验 `check_order_access(db, cookie_value, order_id, scope, now)`：一条查询，只有会话存在、未撤销、未到期，且该会话对该订单、该范围的授权存在、未撤销、未到期时通过；范围互不替代，授权不跨订单。
  - CSRF：令牌不入库，为 SHA-256(`acuven-shop/order-access/csrf` + `\x00` + 令牌原文) 的十六进制，与入库摘要（SHA-256(令牌原文)）不同。`csrf_token_for_cookie` 由 cookie 值重新算出（格式不合法为空），`check_csrf_token(cookie_value, header_value)` 以 `hmac.compare_digest` 常量时间比较；令牌缺失、为空、不一致，或 cookie 缺失、格式不合法都不通过。docstring 写明支付、取消、确认收货与退款申请等写操作必须同时通过 `check_order_access` 与 `check_csrf_token`。
  - 未知范围与带时区的 `now` 抛 `ValueError`（编程错误，消息不含令牌）。模块不写日志；令牌、CSRF 令牌与 cookie 值不出现在异常消息里。
- cookie 属性（`set_order_access_cookie(response, token)`）：只有一个 cookie `__Host-shop_order_access`，`HttpOnly`、`Secure`、`SameSite=Lax`、`Path=/`，不设 `Domain`、不设 `Expires`，`Max-Age=1800`（30 分钟）。一个浏览器的多张订单授权都挂在同一会话下、各自到期。格式不合法的令牌抛 `ValueError`。
- 偏离：未改设计，未发现设计本身的问题。说明几处取舍，请审阅：
  - 设计闸门是 DESIGN 1.10（提交 `e3b3505`）；仓库里现为 1.11 候批稿。1.11 只改「权限与资料保护」第 1、2、3 条等短信验证开关相关内容，第 6、7 条与 1.10 相同，未发现冲突。
  - 沿用会话时按验收不返回新令牌，但浏览器里的 cookie 的 Max-Age 仍从上次设置算起：例如 10:00 首次签发（cookie 到 10:30），10:20 给第二张订单签发（授权与会话到 10:50），若不重设 cookie，浏览器 10:30 就丢掉它，第二张订单的授权实际只剩 10 分钟。故 `IssuedAccess` 与 `set_order_access_cookie` 的 docstring 要求调用方沿用会话时以请求带来的 cookie 值重新调用 `set_order_access_cookie`，刷新 Max-Age；之后的接口任务须照做。
  - `check_csrf_token` 不查库，只判断请求头与由 cookie 值算出的值是否一致；会话是否有效、是否已撤销由 `check_order_access` 判定，写操作两者都要。CSRF 令牌经哪个请求头或表单字段传入，由之后的接口任务决定。
  - 验收标准之外另加的约束：会话摘要长 64（`ck_order_access_sessions_token_hash_length`），由「SHA-256 十六进制摘要（64 个字符）」直接推出，与 `member_sessions` 相同。
  - 本任务只提供签发与校验；撤销（写撤销时间，例如订单取消或支付后撤销该单的短期凭据）与到期行的清理由之后的任务按需要实现。表里不存个人资料，到期行暂不删除。
  - 同一浏览器并发两次签发同一会话、订单与范围时，后一个可能被唯一约束拒绝；由调用方的事务处理（重试或按失败返回），本任务不加锁。
- 验证到什么程度：人工逐条对照验收标准与设计原句自查，并逐个核对 0009 的列类型、可空性、约束名与表达式、外键删除行为、索引与表选项同模型按命名约定生成的一致（`alembic check` 不比较检查约束与表选项，这两项只经人工核对）。`tests/test_order_access.py` 用 SQLite 内存库（每个连接打开外键检查，并断言已打开）按模型建表、自建合法游客订单，每条测试的文档字符串写明它守住的设计原句或验收约定，覆盖：合法会话与授权的对照；两张表每个检查约束的反例（会话与授权到期等于或早于创建、摘要长 62、未知范围）；重复会话摘要、重复的会话 + 订单 + 范围被唯一约束拒绝；授权引用不存在的订单或会话、删除被授权引用的订单或会话被外键拒绝，外键都是 RESTRICT；除撤销时间外各列写入 NULL 被拒、只有撤销时间可空；两表列集合不含 IP、浏览器标识或个人资料；订单 ID 索引；首次签发新建会话、令牌为 43 个 URL 安全字符、库里只有其 SHA-256 摘要且任何一列都查不到令牌原文或 CSRF 令牌；两个无 cookie 的浏览器得到不同令牌；带有效 cookie 再签发沿用同一会话、不返回新令牌、CSRF 令牌不变；cookie 对应会话已到期、已撤销，或 cookie 格式不合法、库里没有、缺失时新建会话；同一会话两张订单的授权各自到期、会话到期延到较晚者且不被缩短；重复签发延长原授权（含已撤销的授权恢复）、不新增行、创建时间不变；签发不提交（回滚后不留行）；给不存在的订单签发被外键拒绝；授权在到期前通过、到期时刻起不通过，会话到期、授权或会话撤销、换订单、换范围（两个方向）、别的浏览器的 cookie 都不通过；格式不合法的 cookie（缺失、空、长度不对、含 `=`、`+` 或非 ASCII）不发出任何 SQL 且不通过；未知范围报错；cookie 的名称、各属性、没有 Domain 与 Expires、只有一个 Set-Cookie；格式不合法的令牌被拒且异常消息不含它；CSRF 令牌对同一会话稳定、与入库摘要和令牌原文不同；CSRF 令牌错误、属于别的会话、缺失、为空、非 ASCII，以及 cookie 缺失、格式不合法时都不通过；签发与校验不产生 `app.services` 的日志记录，签发结果的 repr 不含令牌。这些测试与已有后端测试，以及迁移在真 MySQL 上的 upgrade head、downgrade base、再 upgrade head 与 `alembic check`，都只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；签发与校验的查询未在真 MySQL 上执行。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 实现与 docs/DESIGN.md 1.10「权限与资料保护」第 6 条（游客短期凭据与查单授权）及第 7 条（日志）一致；发现设计本身有问题时停下并在 PR 里说明，不自行改设计
      - app/models/order_access.py 定义两个模型，沿用 app/db/base.py 的基类、约束命名约定与 InnoDB、utf8mb4 表选项；app/models/__init__.py 导出它们；时间一律不带时区的 UTC；外键一律 RESTRICT；除撤销时间外所有列非空
      - 访问会话表：会话令牌的 SHA-256 十六进制摘要（64 个字符，全表唯一；令牌原文从不入库）、创建时间、到期时间、撤销时间（可空）；检查约束保证到期时间晚于创建时间；不存 IP、浏览器标识或任何个人资料
      - 授权表：所属会话、所属订单（建索引）、范围（只允许 guest_checkout 与 lookup 两种，对应设计的游客短期凭据与查单授权）、创建时间、到期时间、撤销时间（可空）；同一会话、订单与范围唯一；检查约束保证到期时间晚于创建时间
      - 新迁移文件 alembic/versions/20261001_0009_order_access.py，revision 为 0009、down_revision 为 0008；upgrade 建两张表，downgrade 按依赖倒序删除；约束名按命名约定逐个写出；迁移在真 MySQL 上 upgrade head、downgrade base、再 upgrade head 全部成功，alembic check 在它比较的范围内无差异，由 PR 的必需 CI 检查 backend 执行；PR 正文逐表列出检查约束与表选项
      - app/services/order_access.py 的签发函数接收数据库会话、请求带来的 cookie 值（可为空）、订单 ID、范围与当前时间；cookie 值格式合法且对应的会话未撤销、未到期时沿用该会话，否则新建会话，令牌用 secrets 生成、至少 256 位随机、只含 URL 安全字符；授权有效期固定 30 分钟，同一会话、订单与范围已有授权时把它的到期时间更新为当前时间加 30 分钟并清空撤销时间，否则新建；会话到期时间更新为它与新授权到期时间中较晚者；返回新令牌（沿用会话时为空）、CSRF 令牌与授权到期时间；不提交事务，由调用方在同一事务里提交
      - 校验函数接收数据库会话、cookie 值、订单 ID、范围与当前时间，只有会话存在、未撤销、未到期，且该会话对该订单、该范围的授权存在、未撤销、未到期时才通过；格式不合法的 cookie 值不查库直接不通过；guest_checkout 授权不能当 lookup 用，反之亦然；一张订单的授权不能用于其他订单
      - cookie 设置函数只用一个 cookie，名为 __Host-shop_order_access，带 HttpOnly、Secure、SameSite=Lax、Path=/，不设 Domain（前缀 __Host- 使同一上级域名下的其他站点无法写入它，防会话固定），Max-Age 为 30 分钟；一个浏览器的多张订单授权都挂在同一会话下、各自到期
      - CSRF 令牌不入库，由会话令牌原文加固定的用途前缀做 SHA-256 得到（与入库的会话摘要不同），因此持有 cookie 的请求随时可由服务端重新算出；校验函数用常量时间比较请求头里的令牌与重新算出的值，不一致、缺失或 cookie 无效都不通过；函数 docstring 写明支付、取消、确认收货与退款申请等写操作必须同时通过授权校验与 CSRF 校验
      - 模块不写日志；令牌、CSRF 令牌与 cookie 值不出现在异常消息里
      - tests/test_order_access.py 用 SQLite 内存库（开启外键检查）按模型建表、自建一张合法订单，验证：两张表的检查约束、唯一约束与外键各至少一个被拒的反例；首次签发新建会话且库里只有令牌摘要、查不到令牌原文；带有效 cookie 再签发沿用同一会话且不返回新令牌；cookie 对应的会话已到期、已撤销或格式不合法时新建会话；同一会话两张订单的授权各自到期；授权到期、撤销、换订单、换范围都不通过；重复签发延长原授权而不新增行；cookie 的名称与各属性（含没有 Domain）；CSRF 令牌对同一会话稳定、与入库摘要不同，错误、缺失与 cookie 无效时都不通过；每条测试写明它守住的是设计里哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-019 记录段（两张表与约束、令牌与 CSRF 规则、cookie 属性、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖；不加接口、不改 app/main.py、不改前端代码
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：DESIGN 1.10，提交 e3b3505），不合并、不直推
```

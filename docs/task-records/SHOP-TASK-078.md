# SHOP-TASK-078 会员重设密码的一次性凭据与重设规则

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（已有密码的重设再次短信验证；密码至少 8 位，安全哈希存储）、`docs/HANDOFF.md` 0.41 记录的 Kelvin 2026-10-08 决定（重设密码时撤销该会员的全部会话）与 0.44 记录的 Kelvin 2026-10-10 决定（短信验证通过后的一次性重设凭据存于 Redis，10 分钟有效、用一次即删，另配 CSRF 令牌；Redis 不可用时重设暂停），新增规则函数 `app/services/member_pw_reset.py`；测试见 `tests/test_member_pw_reset.py`。未加接口、cookie、表或迁移，未改 `app/services/member_auth.py`、`rate_limit.py`、`pw_hash.py`、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41、0.44（会员密码与个人资料）。
- 凭据：`issue_pw_reset(client, member_id)` 生成 `secrets.token_urlsafe(32)`（43 个字符），Redis 键为 `acuven_shop:member_pw_reset:` 加原文的 SHA-256 十六进制摘要（原文不进 Redis），值为会员 ID，以一条 `SET key value EX 600` 写入（不分 SET 与 EXPIRE 两步）；返回 `IssuedPwReset(token, csrf_token)`，两个字段 `repr=False`。会员 ID 不是正整数时抛 `ValueError`（编程错误，不访问 Redis）。
- CSRF：`csrf_token_for_reset(token)` = SHA-256(`acuven-shop/member-pw-reset/csrf\0` + 凭据原文)，不存储；前缀不同于会员会话（`member-session`）、后台会话（`admin-session`）与订单访问（`order-access`）。`check_reset_csrf(token, header_value)` 以 `hmac.compare_digest` 比较，凭据或请求头为 `None`、为空、格式不对或不一致都不通过。写法与 member_auth 相同。
- 一次性取用：`consume_pw_reset(client, token)` 对格式不合法的凭据直接返回 `None`、不访问 Redis（客户端为 `None` 时也是）；否则在一个事务管道（MULTI / EXEC）里 `GET` 与 `DEL`，返回会员 ID；键不存在（从未签发、已过期、已被取用）返回 `None`。不用 `GETDEL`（需 Redis 6.2，共享 Redis 的版本未核实）。并发取用同一凭据时只有先执行的事务读到值。
- Redis 不可用：客户端为 `None`（未配置）、连不上、超时或返回错误时，签发与取用都抛 SHOP-TASK-026 的 `RateLimitUnavailable`（`from None`，消息为固定文本，不含凭据、键与会员 ID），调用方必须拒绝重设。事务里任何一条命令出错（含 EXEC 被放弃、GET 读到值而 DEL 出错）、读到值而 DEL 未删到、或值不是正整数时也抛它，不把读到的会员 ID 当作取用成功。
- 重设密码：`reset_password(db, member_id, new_password, now)` 先拒带时区的 `now`；新密码不满足 SHOP-TASK-071 的 `password_length_ok` 时抛 `ValueError`（消息不含密码），不写库；否则以 `pw_hash.hash_password` 算哈希，以一条条件 UPDATE（`id` 相同且 `status` 为 `active`）写入，更新不到（不存在或已注销）返回 `False`、不撤销任何会话；更新到时以 SHOP-TASK-071 的 `revoke_all_member_sessions` 撤销该会员全部尚未撤销的会话，返回 `True`。未设过密码的会员同样可设置（等同首次设密码，UX P12）。只 flush、不提交；不写日志。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 键前缀验收写作 `acuven_shop:member_pw_reset`，实际在其后加冒号再接摘要，与 `rate_limit` 的 `acuven_shop:rate_limit:<桶>:<摘要>` 写法一致。
  - 签发结果除凭据外另带 `csrf_token`（同为 `repr=False`），与 member_auth 的签发结果写法相同；也可随时以 `csrf_token_for_reset` 重新算出。
  - 签发时 SET 返回假值也按不可用处理（不带 NX 时正常不会发生）。凭据冲突概率可忽略，不用 NX。
  - 取用后由调用方先校验 CSRF 再取用，还是先取用再校验，本任务不定；建议 SHOP-TASK-079 先校验 CSRF、通过后再取用，避免 CSRF 不通过的请求把凭据耗掉。
  - 取用成功后若重设返回 `False`（期间会员已注销）或数据库提交失败，凭据已删除，访客须重新短信验证（Kelvin 2026-10-10「凭据丢失只需重新验证」）。
  - 设计没有写密码长度上限，本任务不定；接口的请求体上限由 SHOP-TASK-079 按 SHOP-TASK-077 的写法另定。
- 留给之后的任务：SHOP-TASK-079 负责重设密码接口与 cookie（短信核验 `reset_password` 用途通过且号码已注册时调用 `issue_pw_reset`，经 HttpOnly、Secure、SameSite=Lax 的 cookie 交给本浏览器、CSRF 令牌交给页面；提交新密码时校验 CSRF、调用 `consume_pw_reset` 与 `reset_password` 并提交；`RateLimitUnavailable` 转成暂不可用的回答）；前端忘记密码 P12。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 第 3、6 条与 HANDOFF 0.41、0.44 的 Kelvin 决定原句自查。`tests/test_member_pw_reset.py` 用 SQLite 内存库（每个连接开启外键检查）与照 `tests/test_member_auth.py` 另写的内存替身 `FakeRedis`（SET 带 EX、事务管道 GET 与 DEL、替身时钟、命令记录、可注入的连接 / 超时 / 响应错误、DEL 出错与执行前插入另一个取用的钩子；未加依赖）；期望的键与 CSRF 令牌在测试里另算；每条测试的文档字符串写明守住的设计原句、Kelvin 决定或验收条目。覆盖：签发后 Redis 里只有另算的摘要键、值为会员 ID、过期 600 秒、只有一条 SET 命令；凭据互不相同；5 种不合法的会员 ID 抛 `ValueError` 且不访问 Redis；取用一次后再取用为 `None`；差 1 秒到 10 分钟可取用、满 10 分钟为 `None`；从未签发的凭据为 `None`；8 种格式不合法的凭据返回 `None` 且不访问 Redis（客户端为 `None` 也是）；交错的两个取用只有一个得到会员 ID；签发时连不上、超时、返回错误与客户端为 `None` 都抛 `RateLimitUnavailable`，消息与异常链不含键与会员 ID；取用时连不上、超时与 EXEC 被放弃都抛它且消息不含凭据、键与会员 ID；GET 读到值而 DEL 出错时抛它；值不是会员 ID 时抛它；CSRF 等于另算的带前缀摘要、不进 Redis、与 member_auth、admin_auth、order_access 的令牌两两不同且它们不能用于重设，缺失、为空、错误、非 ASCII、另一凭据的令牌与凭据缺失或格式不合法都不通过；重设后旧密码不能、新密码能通过 `authenticate_member`，该会员全部会话撤销于 `now`、其他会员的会话与密码不受影响；未设密码的会员可重设；已注销会员返回 `False`、哈希仍为空且不撤销任何会话；不存在的会员返回 `False`；4 种不足 8 个字符或不是字符串的密码抛 `ValueError`、消息不含密码、不改密码不撤会话；8 个非 ASCII 字符可重设；调用方回滚时密码与撤销都不生效；带时区的时间被拒；签发结果 repr 不含凭据、CSRF 令牌与键；无 `app.` 日志。未连真实 Redis 或 MySQL（并发取用只以替身交错模拟，未在真实 Redis 上并发）。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/member_pw_reset.py——签发凭据：入参为 Redis 客户端与会员 ID，凭据原文为 secrets.token_urlsafe(32)，Redis 里的键为 acuven_shop:member_pw_reset 前缀加凭据原文的 SHA-256 十六进制摘要（凭据原文不进 Redis），值为会员 ID，以一条带 600 秒过期的 SET 写入（不分成 SET 与 EXPIRE 两步，避免留下无期限的凭据），返回凭据原文（结果的凭据字段 repr=False）
      - CSRF 令牌不存储，由凭据原文加本模块独有的用途前缀做 SHA-256 得到（前缀不同于 app/services/member_auth.py、app/services/admin_auth.py 与 app/services/order_access.py），比较用常量时间，格式写法与 member_auth 相同；提供由凭据算出 CSRF 令牌与校验 CSRF 令牌两个函数，凭据或请求头为 None、格式不对时校验不通过
      - 一次性取用：入参为 Redis 客户端与凭据原文；格式不对时直接返回 None、不访问 Redis；否则以事务管道（MULTI 与 EXEC）原子地读取并删除该键，不用 GETDEL（共享 Redis 的版本未核实），返回会员 ID，键不存在（含已过期、已被取用）返回 None；并发取用同一凭据时至多一次得到会员 ID
      - Redis 未配置、连不上、超时或返回错误时，签发与取用都抛 app/services/rate_limit.py 的 RateLimitUnavailable（调用方必须拒绝，不得放行），异常消息不含凭据、键与会员 ID
      - 重设密码：入参为数据库会话、会员 ID、新密码与当前 UTC 时间（不带时区）；新密码不满足 SHOP-TASK-071 的 password_length_ok 时抛 ValueError（消息不含密码）；用 app/services/pw_hash.py 的 hash_password 算哈希，以条件更新（仅当该会员仍为 active）写入，更新不到返回 False；更新到时用 SHOP-TASK-071 的 revoke_all_member_sessions 撤销该会员的全部会话，返回 True；未设过密码的会员同样可设置（等同首次设密码，UX P12）；只 flush、不提交；不写日志
      - tests/test_member_pw_reset.py 用 SQLite 内存库与内存 Redis 替身（照 tests/test_member_auth.py 的 FakeRedis 写法另写一个支持本模块所用命令的替身，不加依赖）验证意图——签发后 Redis 里只有摘要键、值为会员 ID、过期 600 秒，且由同一条命令写入值与过期；取用一次后再取用为 None、过期后为 None、格式不对时不访问 Redis；两个取用交错时只有一个得到会员 ID；CSRF 与 member_auth、admin_auth、order_access 的令牌不同，错、缺与格式不对都不通过；Redis 不可用时签发与取用都抛 RateLimitUnavailable，事务执行时报错（EXEC 返回错误）也抛 RateLimitUnavailable、不把读到的会员 ID 当作取用成功；重设后旧密码不能、新密码能通过 authenticate_member，该会员的全部会话已撤销、其他会员的会话不受影响；未设密码的会员可重设；已注销会员返回 False 且不撤销任何会话；长度不足抛 ValueError；异常与 repr 不含凭据、密码与键；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-078 记录段（凭据、CSRF、一次性取用、重设、偏离、验证到什么程度，并写明接口与 cookie 留给 SHOP-TASK-079），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/member_auth.py、app/services/rate_limit.py、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41、0.44），不合并、不直推
```

# SHOP-TASK-071 会员会话与密码登录锁定规则

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（未设密码的账号密码登录与错误密码返回相同的通用失败；密码至少 8 位；会话到期、退出与服务端授权检查）、第 6 条（日志不记录完整电话与密码）与 `docs/HANDOFF.md` 0.41 记录的 Kelvin 2026-10-08 决定（3）（会员会话 30 天、不随使用延长；同一号码加来源 15 分钟内密码登录失败 5 次即锁 15 分钟，失败一律通用提示；重设密码与注销时撤销该会员的全部会话），仿照 `app/services/admin_auth.py` 在 SHOP-TASK-012 的 `MemberSession` 表上提供会员登录的规则函数：`app/services/member_auth.py`；测试见 `tests/test_member_auth.py`。未加接口、cookie、页面、表或迁移，未改 `app/services/admin_auth.py`、`pw_hash.py`、`rate_limit.py`、模型、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41（会员登录与个人资料）。
- 会话：`issue_member_session(db, member, now)` 只给 `status` 为 `active` 的会员签发（否则抛不含号码的 `ValueError`、不写库），令牌为 `secrets.token_urlsafe(32)`（43 个字符），库里只存 SHA-256 十六进制摘要，到期为 now 加 30 天、不随使用延长；返回令牌、CSRF 令牌与会话记录（三个字段 `repr=False`）。`check_member_session(db, cookie_value, now)` 对格式不合法的值不查库；会话不存在、已撤销、到期时刻已到或所属会员已不是 `active` 都返回空，通过时返回 `CheckedMemberSession(session, member)`（字段 `repr=False`）。`revoke_member_session` 写撤销时间（已撤销的保留原时间），`revoke_all_member_sessions(db, member_id, now)` 以一条 UPDATE 撤销该会员全部尚未撤销的会话并返回条数，行都保留。签发与撤销只 flush 不提交。时间一律不带时区的 UTC，带时区时抛 `ValueError`。
- CSRF：`csrf_token_for_cookie` = SHA-256(`acuven-shop/member-session/csrf\0` + 令牌原文)，前缀不同于后台会话（`admin-session`）与订单访问（`order-access`），不入库；`check_csrf_token` 以 `hmac.compare_digest` 比较，令牌缺失、不一致或 cookie 缺失、格式不合法都不通过。写法与 admin_auth 相同。
- 密码校验：`authenticate_member(db, phone, password)` 按规范化 E.164 号码找 `active` 会员，号码未注册（含已注销）或未设密码时以 `pw_hash.DUMMY_PASSWORD_HASH` 照样调用一次 `verify_password`，三种失败都返回 `None`，通过时返回会员。`password_length_ok(password)`（`PASSWORD_MIN_LENGTH = 8`，按字符计、不要求复杂度）供之后的设置与重设密码任务使用；登录时不按长度预先拒绝。
- 登录锁定（计数用 SHOP-TASK-026 的 `hit` 与 `current_count`，未改它们）：标识为 `json.dumps([来源, 规范化 E.164 号码])`；号码须为加号加 1 到 15 位数字，否则抛不含号码的 `ValueError`（规范化由调用方经 `app/services/phone.py` 做）。`record_login_failure` 在失败计数桶 `member_login_failures`（上限 4、窗口 900 秒）计一次；计数超过上限（窗口内第 5 次及之后）时在锁定桶 `member_login_lock`（上限 1、窗口 900 秒，过期只在首次计入时设置）计一次，只有这次锁定桶计数未超过上限时返回「刚锁定」。`login_locked` 只读锁定桶，计数 ≥ 1 即锁定。成功登录不清零失败计数。Redis 不可用时抛 `RateLimitUnavailable`，调用方必须拒绝登录。写法与 admin_auth 的 `login_locked`、`record_login_failure` 一致，桶名不同，会员与后台的锁定互不影响。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 验收之外另加 `password_login(db, client, source, phone, password)`：把一次密码登录的顺序固定下来——锁定即返回 `None`（不校验密码、不计失败）；否则 `authenticate_member`，不通过则 `record_login_failure`；通过时返回会员，由调用方签发会话并提交。锁定与三种密码失败对调用方都是 `None`（Kelvin 2026-10-08「失败一律通用提示」）。会员没有审计记录，因此不像 admin_auth 那样把「刚锁定」交给接口层另写审计；`record_login_failure` 仍返回它，接口层需要时可直接调用三个基础函数。
  - 号码格式不对时锁定函数抛 `ValueError`（编程错误），`authenticate_member` 则照样以假哈希校验一次并返回 `None`；接口层应先经 `normalize_phone` 规范化，规范化失败时直接回答通用失败。
  - 设计没有写密码长度上限，本任务不定；设置与重设密码任务需要时另定。
  - 号码不出现在异常消息与 repr 里；`authenticate_member` 与 `check_member_session` 返回的 `Member` 对象本身带号码列（签发会话要用会员），由调用方不把它写进回答或日志。
- 留给之后的任务：会员登录接口任务（之后登记）负责会话 cookie 的名称与属性、按 `client_source` 取来源、把号码按 `normalize_phone` 规范化、接口的回答与状态码、退出接口；设置与重设密码任务调用 `password_length_ok` 与 `pw_hash.hash_password`，重设后调用 `revoke_all_member_sessions`；会员注销任务调用 `revoke_all_member_sessions`。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11 第 3、6 条与 HANDOFF 0.41 的 Kelvin 2026-10-08 决定原句自查。`tests/test_member_auth.py`（SQLite 内存库、每个连接开启外键检查；照 `tests/test_admin_auth.py` 写的内存替身 `FakeRedis`，不连真 Redis、不加依赖；每条测试的文档字符串写明守住的设计原句、Kelvin 决定或验收条目）覆盖：签发后可校验并返回会话与会员；到期前一秒通过、30 天时刻起不通过、反复校验不延长；库里只有另算的摘要、任何列都不含令牌或 CSRF 令牌；令牌互不相同；已注销会员不能签发、签发后注销的会话不再通过；撤销单个（行保留、再撤销不改时间、另一会话不受影响）与撤销全部（返回条数、已撤销的保留原时间、其他会员不受影响）；调用方回滚时签发与撤销都不生效；8 种格式不合法的 cookie 不发任何 SQL；库里没有的令牌不通过；带时区的时间被拒；CSRF 令牌等于另算的带前缀摘要、与后台和订单访问的两两不同且后两者不能用于会员、错误或缺失或属于另一会话时不通过；正确密码通过；号码未注册、未设密码、密码错误三种失败都返回 `None` 且各恰好调用一次哈希校验（前两种用假哈希），注销后的号码同未注册；密码长度 7 个字符拒绝、8 个字符（含非 ASCII）通过；第 5 次失败返回刚锁定、第 6 次不再返回；锁定期间正确密码被拒且不校验密码、15 分钟后错误密码照常不通过而正确密码通过；锁定期间再计入不延长锁定；中间夹着成功登录照样累计；窗口过后不累计；按来源与号码组合锁定；并发两次失败只报一次刚锁定；IPv6 来源不相混；计数键为另算的 JSON 标识摘要且不含来源与号码；会员锁定不影响后台锁定；6 种不是规范化 E.164 的号码被拒且消息不含号码；替身在读与事务执行时抛连接错误都抛不可用异常且消息不含号码与来源；签发与校验结果的 repr 不含令牌、CSRF 令牌、号码或密码哈希；不产生 `app.` 下的日志。未连真 Redis 或真 MySQL。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/member_auth.py——签发会话：令牌原文为 secrets.token_urlsafe(32)，库里只存其 SHA-256 十六进制摘要，到期为签发时刻加 30 天、不随使用延长，会员状态须为 active；校验 cookie 值：格式不对、会话不存在、已撤销、已到期或会员已不是 active 都不通过，通过时返回会话与会员；撤销单个会话与撤销某会员的全部会话（写撤销时间，行保留，返回撤销条数）；只 flush、不提交
      - CSRF 令牌不入库，由令牌原文加本模块独有的用途前缀做 SHA-256 得到（前缀不同于 app/services/admin_auth.py 与 app/services/order_access.py），比较用常量时间；格式写法与 admin_auth 相同
      - 密码校验沿用 app/services/pw_hash.py——按号码找 active 会员，不存在或未设密码时以 DUMMY_PASSWORD_HASH 照样校验一次，三种失败（号码未注册、未设密码、密码错误）对调用方是同一个结果；密码长度规则（至少 8 个字符）作为函数提供给之后的设置与重设密码任务
      - 登录锁定（计数用 app/services/rate_limit.py）——标识为 [访客来源, 规范化 E.164 号码] 的 JSON 编码；失败计数窗口 15 分钟、窗口内第 5 次失败时在锁定桶计一次，锁定 15 分钟（锁定桶过期只在首次计入时设置）；锁定期间即使密码正确也拒绝，锁定到期后照常校验；成功登录不清零失败计数；Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录；写法与 admin_auth 的 login_locked、record_login_failure 一致
      - 不写日志；令牌、CSRF 令牌、cookie 值、号码、来源与密码不出现在异常消息、repr 或返回值里（签发结果的令牌字段 repr=False）
      - tests/test_member_auth.py 用 SQLite 内存库与内存 Redis 替身（照 tests/test_admin_auth.py 的 FakeRedis 写法，不加依赖）验证意图——签发后只存摘要、30 天到期不续期；各种不通过的情形（含已注销会员、已撤销、已到期、格式不对）；撤销单个与全部；CSRF 与 admin、订单访问的令牌不同、错与缺都不通过；三种密码失败对调用方相同且都调用一次哈希校验；锁定在第 5 次失败时开始、锁定中正确密码也拒绝、15 分钟后照常校验、按来源与号码组合锁定；异常与 repr 不含敏感值；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-071 记录段（会话、CSRF、密码校验、锁定、偏离、验证到什么程度，并写明 cookie 与接口留给会员登录接口任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/admin_auth.py、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41），不合并、不直推
```

# SHOP-TASK-080 会员注销与注销批准的规则

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（注销时撤销会话、删除手机号与密码，并把其订单上的会员 ID 清空以解除关联；已认领与不可认领的标记保留，重新注册不能恢复旧订单的访问）与「资料保留」（注销不删除订单收货资料），以及 `docs/HANDOFF.md` 0.41 记录的 Kelvin 2026-10-08 决定（注销时撤销该会员的全部会话）与 0.45 记录的 Kelvin 2026-10-10 决定（注销验证码核验通过后的一次性批准存于 Redis，绑定当前会员会话，10 分钟有效、用一次即删），新增规则函数 `app/services/member_deletion.py`；测试见 `tests/test_member_deletion.py`。未加接口、表或迁移，未改 `app/services/member_auth.py`、`rate_limit.py`、模型、`.platform/`、CI、部署配置或设计，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41、0.45（会员注销与个人资料）。
- 注销：`delete_member(db, member_id, now)` 先拒带时区的 `now`；以一条条件 UPDATE（`id` 相同且 `status` 为 `active`）把状态改为 `deleted`、`phone` 与 `password_hash` 置空、`deleted_at` 写为 `now`（满足 `deleted_cleared` 约束），会员行保留。更新不到（不存在或已注销）返回 `False`，不做其他改动（已注销会员的注销时间不被改写）。更新到时以 SHOP-TASK-071 的 `revoke_all_member_sessions` 撤销该会员全部尚未撤销的会话（已撤销的保留原撤销时间），再以一条 UPDATE 把该会员所有订单的 `member_id` 置空（不论订单状态），`claim_status` 一律不改，返回 `True`。不删除、不改订单收货资料、订单状态、订单事件与短信验证记录。只 flush、不提交；不写日志。
- 注销批准的签发：`issue_delete_approval(client, session_id, member_id)` 的键为 `acuven_shop:member_delete_approval:` 加会员会话 ID，值为会员 ID，以一条 `SET key value EX 600` 写入（不分 SET 与 EXPIRE 两步）；同一会话再次签发即覆盖并重新计时。
- 注销批准的取用：`consume_delete_approval(client, session_id, member_id)` 在一个事务管道（MULTI / EXEC）里 `GET` 与 `DEL`，键存在且值等于该会员 ID 时返回 `True`；键不存在（从未签发、已过期、已被取用）或值不符时返回 `False`。不用 `GETDEL`（需 Redis 6.2，共享 Redis 的版本未核实）。并发取用同一批准时只有先执行的事务读到值。
- Redis 不可用：客户端为 `None`（未配置）、连不上、超时或返回错误时，签发与取用都抛 SHOP-TASK-026 的 `RateLimitUnavailable`（`from None`，消息为固定文本，不含键、会话 ID 与会员 ID），调用方必须拒绝注销。事务里任何一条命令出错（含 EXEC 被放弃、GET 读到值而 DEL 出错）、读到值而 DEL 未删到时也抛它，不把读到的值当作取用成功。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 键前缀验收写作 `acuven_shop:member_delete_approval`，实际在其后加冒号再接会话 ID，与 SHOP-TASK-078 的 `acuven_shop:member_pw_reset:<摘要>` 写法一致。会话 ID 是 `member_sessions` 的整数主键，不是 cookie 令牌，键里不含令牌或其摘要。
  - 会话 ID 或会员 ID 不是正整数（含布尔值与字符串）时签发与取用都抛 `ValueError`（编程错误，不访问 Redis），写法同 SHOP-TASK-078 的 `_positive_id`。验收未写这一条。
  - 会员 ID 不符时批准也已随同一事务删除（GET 与 DEL 在同一个 MULTI 里，取用前不知道值是否相符）。正常流程里会话只属于一个会员，不会出现；出现时访客须重新验证。
  - 读到的值不能按 ASCII 解码或不是字节或字符串时按 Redis 返回错误处理，抛 `RateLimitUnavailable`。签发时 SET 返回假值也按不可用处理（不带 NX 时正常不会发生）。
  - `delete_member` 不检查注销批准、不核对会话：确认注销的顺序（会话校验、CSRF、取用批准或校验密码、注销、提交）由 SHOP-TASK-081、082 的接口负责。建议先校验 CSRF、再取用批准，避免 CSRF 不通过的请求把批准耗掉；取用后注销返回 `False` 或提交失败时批准已删、不随数据库回滚，访客须重新验证。
  - 订单访问会话与授权（游客查单的短期凭据）不在注销范围内，不改；设计「资料保留」写明游客凭订单号与电话可随时查单，会员注销后收货资料也保留。
  - `delete_member` 的两条 UPDATE 以 `synchronize_session="fetch"` 执行，会话里已加载的会员与订单对象随之更新；会员 ID 不另校验类型，不存在即返回 `False`。
- 留给之后的任务：SHOP-TASK-081 与 082 负责注销接口（短信验证开关开启时核验 `delete_account` 用途的验证码后调用 `issue_delete_approval`，确认时取用并注销；开关关闭时以当前密码确认、与密码登录共用锁定计数，未设密码者在已登录会话内二次确认；`RateLimitUnavailable` 转成暂不可用的回答；清除会话 cookie）；积分余额与未用券作废、删除电话关联防套利索引，随之后的优惠券与积分账本任务实现（这些表尚不存在）；注销删除失败向运营告警邮件报告，留给运营告警邮件任务；前端会员中心 P13 的注销。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11「权限与资料保护」第 3 条与「资料保留」、HANDOFF 0.41、0.45 的 Kelvin 决定原句自查。`tests/test_member_deletion.py` 用 SQLite 内存库（引擎设置同 `tests/test_member_sms_login.py`，由引擎发 BEGIN，每个连接开启外键检查）与照 `tests/test_member_auth.py` 另写的内存替身 `FakeRedis`（SET 带 EX、事务管道 GET 与 DEL、替身时钟、命令记录、可注入的连接 / 超时 / 响应错误、DEL 出错与执行前插入另一个取用的钩子；未加依赖）；期望的键在测试里另算；每条测试的文档字符串写明守住的设计原句、Kelvin 决定或验收条目。覆盖：注销后会员行保留、状态 `deleted`、手机号与密码哈希为空、注销时间为 `now`、其余列不变；该会员全部会话撤销于 `now` 且不再通过校验、已撤销的保留原时间、行都保留，其他会员的会话照常通过；该会员 5 张订单（claimed 与 not_claimable，五种订单状态）`member_id` 为空、`claim_status` 与其余各列不变，其他会员的订单与游客订单不变；收货资料、订单事件、短信验证记录与订单状态不变；已注销会员与不存在的会员返回 `False` 且各表都不变；注销后同号经 SHOP-TASK-075 的 `sms_login_or_register` 重新注册为新会员，旧会员的 claimed 与 not_claimable 订单不被再次认领、旧会话仍不通过；注销函数返回后回滚，各表全部复原、原会话仍通过，已取用的 Redis 批准不随回滚恢复；带时区的时间被拒且不写库；签发后 Redis 里只有该键、值为会员 ID、过期 600 秒且只有一条 SET 命令；同一会话重签后重新计时；取用一次后再取用为 `False`；差 1 秒到 10 分钟可取用、满 10 分钟为 `False`；另一会话的 ID 取用为 `False` 且不影响原批准，会员 ID 不符为 `False`；交错的两个取用只有一个为 `True`；8 种不合法的 ID 抛 `ValueError` 且不访问 Redis；签发时连不上、超时、返回错误与客户端为 `None` 都抛 `RateLimitUnavailable`，消息与异常链不含键、会话 ID 与会员 ID；取用时连不上、超时与 EXEC 被放弃都抛它且消息不含键、会话 ID 与会员 ID；GET 读到值而 DEL 出错时抛它；无 `app.` 日志。未连真实 Redis 或 MySQL（并发取用只以替身交错模拟，未在真实 Redis 上并发）。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/services/member_deletion.py 的注销函数——入参为数据库会话、会员 ID 与当前 UTC 时间（不带时区）；以条件更新（仅当该会员仍为 active）把状态改为 deleted、手机号与密码哈希置空、注销时间写为当前时间（满足 app/models/member.py 的 deleted_cleared 约束），更新不到（不存在或已注销）返回 False 且不做其他改动；更新到时用 SHOP-TASK-071 的 revoke_all_member_sessions 撤销该会员的全部会话，再把该会员所有订单的 member_id 置空（不论订单状态，claim_status 一律不改），返回 True；不删除、不改订单收货资料、订单状态、订单事件与短信验证记录；只 flush、不提交；不写日志
      - 注销批准的签发——入参为 Redis 客户端、会员会话 ID 与会员 ID；键为 acuven_shop:member_delete_approval 前缀加会话 ID，值为会员 ID，以一条带 600 秒过期的 SET 写入（不分成 SET 与 EXPIRE 两步），同一会话再次签发即覆盖并重新计时
      - 注销批准的取用——入参为 Redis 客户端、会员会话 ID 与会员 ID；以事务管道（MULTI 与 EXEC）原子地读取并删除该键，不用 GETDEL（共享 Redis 的版本未核实）；键存在且值等于该会员 ID 时返回 True，键不存在（含已过期、已被取用）或值不符时返回 False；并发取用同一批准时至多一次得到 True
      - Redis 未配置、连不上、超时、返回错误或事务执行报错时，签发与取用都抛 app/services/rate_limit.py 的 RateLimitUnavailable（调用方必须拒绝，不得放行），不把读到的值当作取用成功；异常消息不含键、会话 ID 与会员 ID
      - tests/test_member_deletion.py 用 SQLite 内存库与内存 Redis 替身（照 tests/test_member_auth.py 的 FakeRedis 写法另写一个支持本模块所用命令的替身，不加依赖）验证意图——注销后会员行保留、状态 deleted、手机号与密码哈希为空且注销时间已写；该会员的全部会话被撤销、其他会员的会话不受影响；其订单（含 claimed 与 not_claimable、各种订单状态）的 member_id 为空且 claim_status 不变，收货资料与订单事件不变，其他会员的订单不变；已注销或不存在的会员返回 False 且没有任何改动；注销后同号可经 SHOP-TASK-075 重新注册为新会员且旧订单不被再次认领；签发后键值与 600 秒过期由同一条命令写入；取用一次后再取用为 False，过期后、会员 ID 不符时为 False；两个取用交错时只有一个为 True；Redis 不可用与事务报错都抛 RateLimitUnavailable；注销函数返回后未提交（其数据库改动回滚后全部复原；Redis 里的批准不随数据库回滚）；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-080 记录段（注销、批准的签发与取用、偏离、验证到什么程度，并写明接口留给 SHOP-TASK-081 与 082、积分与优惠券作废留给优惠券与积分账本任务、注销失败告警留给运营告警邮件任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/member_auth.py、app/services/rate_limit.py、模型、.platform/、CI、部署配置；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41、0.45），不合并、不直推
```

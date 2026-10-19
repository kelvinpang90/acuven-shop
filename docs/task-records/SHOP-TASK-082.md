# SHOP-TASK-082 确认会员注销的接口

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（注销须先以短信验证码确认；短信验证开关关闭期间改为已设密码的会员输入当前密码、未设密码的会员在已登录会话内经二次确认）与「边界与原则」第 4 条（确认注销时按当时读取的开关值判定），以及 `docs/HANDOFF.md` 0.45 记录的 Kelvin 2026-10-10 决定（开关开启时取用注销批准；开关关闭时密码输错与密码登录共用锁定计数），在 SHOP-TASK-080 的 `consume_delete_approval`、`delete_member` 与 SHOP-TASK-081 的路由器之上，于 `app/api/member_deletion.py` 新增 `POST /api/member/delete`；测试见 `tests/test_member_delete_confirm_api.py`。SHOP-TASK-081 的两个接口行为未改（只补充了模块说明与 `_body` 的文档字符串）。未改 `app/services/`、`app/api/member_auth.py`、`tests/test_member_deletion_api.py`、`.platform/`、CI、部署配置、前端或设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41、0.45（会员注销与个人资料）。
- 接口：同一路由器（`prefix="/api/member/delete"`，路径为空串），SHOP-TASK-027 的 `_NoStoreRoute`，处理函数与依赖产生的全部响应（含 `RateLimitUnavailable` 转成的 503）都带 `Cache-Control: no-store`。不写日志；响应体与错误不含号码原文、密码、会话 ID 与会员 ID。
- 检查顺序（与 SHOP-TASK-081 的两个接口相同）：413（请求体逐块读，超过 4 KB，先于一切）→ 415（不是 JSON）→ 401 `member_session_required`（`require_member`）→ 403 `csrf_failed`（`require_member_csrf`）→ 422（结构：请求体只有可省略的 `password`，给出时为 1 到 256 个字符的字符串，显式 `null` 也是 422，上限取 `app/api/member_pw.py` 的 `PASSWORD_MAX_CHARS`；严格类型，多出字段 422；沿用 `_body_errors`，不回显请求内容，尤其是密码）。CSRF 与结构写在依赖 `_confirm_input` 里，CSRF 不通过或结构不对时不取用批准、不碰 Redis。
- 确认方式：当次以 `is_sms_verification_enabled` 从数据库读取短信验证开关（无设置行即关闭），按读到的值只走一种方式：

  | 开关与会员 | 确认 | 不通过时 |
  |---|---|---|
  | 开启 | 取 Redis 客户端（`get_redis_client`），以 `consume_delete_approval(客户端, 当前会话 ID, 会员 ID)` 取用批准；`password` 不使用 | 取不到 403 `delete_not_verified` |
  | 关闭，已设密码 | 未给 `password` 先 422 `password_required`（不取 Redis）；取 Redis 客户端与 `client_source`（同密码登录）；`login_locked` 为真 429 `rate_limited`（不校验密码、不计失败）；否则 `authenticate_member` | 不通过或得到的不是当前会员时 `record_login_failure` 计一次失败，403 `password_wrong` |
  | 关闭，未设密码 | 当前会话与 CSRF 即确认（二次确认由前台确认框完成）；`password` 不使用，不取 Redis 客户端 | — |

- 结果与状态码：确认通过后调用 `delete_member`，返回 `True` 时提交，以 `app/api/member_auth.py` 的 `_clear_session_cookie` 清除会员会话 cookie，204 且响应体为空；返回 `False`（会员已不是 active）时回滚，401 `member_session_required`，不清 cookie。Redis 客户端未配置或 Redis 出错（取用批准、读锁定、计失败）由路由类回答 503 `service_unavailable`，不注销。读取开关、校验密码、注销或提交时数据库出错回滚，503 `service_unavailable`；开关开启时批准此时已被取用、不随回滚恢复，须重新验证。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - Redis 客户端在处理函数里按确认方式直接调用 `get_redis_client(request)`，不声明为 FastAPI 依赖：声明为依赖时每个请求都会解析它，Redis 未配置时未设密码的会员就无法注销，与验收「不取 Redis 客户端（Redis 故障时照样可以注销）」相反。因此 `app.dependency_overrides[get_redis_client]` 对本接口不生效，测试以 `monkeypatch` 替换模块里的 `get_redis_client` 名字（核验与密码登录接口仍经依赖覆盖拿到同一个替身）。
  - 读取开关时数据库出错：回滚后 503 `service_unavailable`（与本接口其他数据库错误相同）。验收未单独写这一步。
  - `password` 只有省略才算没给：显式 JSON `null` 不是字符串，422（类型 `string_type`，由 `DeleteConfirmIn` 的 before 校验器拒绝；省略时默认值不经校验）。开关开启或未设密码时给出的 `password` 只经结构检查、不校验、不计失败。
  - 开关关闭时走密码或会话确认，不取用、也不删除之前核验得到的批准：它绑定的会话已随注销撤销，10 分钟后自然过期。
  - 「得到的不是当前会员」：号码在 active 会员中唯一，正常不会出现；出现时照密码错误计失败并 403。
  - 本接口的 403 `password_wrong` 与 429 `rate_limited` 可以区分（密码登录对访客一律 401 `login_failed`）：请求者已凭会话与 CSRF 证明是本人，按验收给出不同的错误码。
- 留给之后的任务：前端会员中心 P13 的注销入口（开关开启时先 V1 发送与核验、204 后显示 `[account.delete_confirm]` 再调用本接口；开关关闭时已设密码者的密码输入与未设密码者的确认框；`delete_not_verified` 时提示重新验证，`password_wrong`、`rate_limited` 的提示；204 后回到未登录状态）随「会员中心 P13 与订单详情、退款申请的会员模式」登记；注销时积分余额与未用券作废、删除电话关联防套利索引，留给优惠券与积分账本任务（注销接入积分作废见 HANDOFF 2026-10-11 决定所列 SHOP-TASK-088 至 096）；注销删除失败向运营告警邮件报告，留给运营告警邮件任务。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11「权限与资料保护」第 3、5、6 条、「边界与原则」第 4 条与 HANDOFF 0.41、0.45 的 Kelvin 决定原句自查。`tests/test_member_delete_confirm_api.py` 照 `tests/test_member_deletion_api.py` 的写法：`TestClient`（`https://testserver`）与 SQLite 内存库，会话依赖换成绑定同一内存库的会话（可换成提交即抛错的子类），服务商用 SHOP-TASK-067 的 `FakeSmsProvider`，Redis 用本文件的内存替身 `FakeRedis`（PING、GET、带 EX 的 SET、事务管道 GET、DEL、INCR 与 EXPIRE，按命令注入的连接错误；未加依赖），本接口取 Redis 客户端的名字换成记调用次数、可切换为未配置的 `RedisGetter`；访客来源用 X-Real-IP 指定；结果都在另一个数据库会话里读；每条测试的文档字符串先写明守住的设计原句或 Kelvin 决定原句，再写落实它的验收条目。覆盖：开关开启时没有批准 403 `delete_not_verified` 且会话仍有效；批准经 SHOP-TASK-081 的 verify 取得后注销 204、cookie 以 Max-Age 0 清除、会员为 `deleted` 且号码与密码哈希为空、该会员全部会话撤销、取当前会话 401、其订单 `member_id` 为空且认领状态不变、其他会员与其订单不变、批准已删除；提交失败 503 后批准已用掉、再确认 403、重新验证后可注销；给出正确密码不能代替批准且不计数；另一会话的批准不能用；开关在核验之后被关闭时已设密码者缺密码 422 且批准未被取用、给出密码才注销；开关关闭（含无设置行）时已设密码者缺密码 422 且不取 Redis、密码错误 403 并计一次失败、正确密码注销、输错 5 次后正确密码也 429 而另一来源可注销、锁定后的请求不再计数、256 个字符与不足 8 个字符的密码照常校验；与 `POST /api/member/password-login` 交叉验证（登录失败 5 次后注销 429，注销失败 5 次后登录 401 `login_failed` 而另一来源可登录，登录 3 次加注销 2 次即锁）；未设密码者凭会话与 CSRF 注销（无设置行与关闭、给不给 `password`）且不取 Redis；开关开启与开关关闭且已设密码时 Redis 未配置或出错 503 且未注销，取用出错后恢复仍可用原批准注销；开关关闭且未设密码时 Redis 未配置或出错照样注销；提交失败 503、会员与订单不变；注销函数返回 `False` 时 401；413（有无会话、三种 Content-Type、分块发送）、恰好 4 KB 放行、415 先于会话、4 种会话无效时结构不对也是 401、4 种 CSRF 不通过时结构不对也是 403 且批准不被耗掉、7 种结构错误（含 `password` 为 `null`）422 不回显密码且不取 Redis、逐步的检查顺序；方法不匹配的 405 不含请求内容；无 `app.` 日志，任何日志都不含号码、密码、会话 cookie 与 CSRF 令牌。未连真实 Twilio Verify、Redis 或 MySQL，未在真实浏览器里验证 cookie 清除。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 在 app/api/member_deletion.py 增加 POST /api/member/delete（同一路由器，响应同样带 no-store）；检查顺序与 SHOP-TASK-081 的两个接口相同——413 → 415 → 401 member_session_required → 403 csrf_failed → 422（结构）；请求体只有可省略的 password（给出时为 1 到 256 个字符的字符串），多出字段 422，422 不回显请求内容（尤其是密码）
      - 之后按当次从数据库读取的短信验证开关判定确认方式（开关开启时只接受注销批准，关闭时只接受密码或会话确认）——开启时 password 不使用，用 app/services/rate_limit.py 的 get_redis_client（不可用时由路由类回答 503 service_unavailable）以 SHOP-TASK-080 的取用函数取用当前会话与会员的注销批准，取不到时 403 {"detail":"delete_not_verified"}
      - 开关关闭且会员已设密码时——未给 password 为 422 {"detail":"password_required"}；Redis 客户端同上（不可用 503 service_unavailable）；访客来源取 app/services/rate_limit.py 的 client_source（与 SHOP-TASK-077 的密码登录相同，才能共用同一组计数）；SHOP-TASK-071 的 login_locked 对 [访客来源, 会员手机号] 为真时 429 {"detail":"rate_limited"}（不校验密码、不计失败）；否则以 authenticate_member 校验，不通过或得到的不是当前会员时用 record_login_failure 计一次失败（与密码登录共用计数，Kelvin 2026-10-10），403 {"detail":"password_wrong"}；开关关闭且会员未设密码时凭当前会话与 CSRF 即确认（二次确认由前台确认框完成），不需要 password，也不取 Redis 客户端（Redis 故障时照样可以注销）
      - 确认通过后调用 SHOP-TASK-080 的注销函数，返回 False 时 401 member_session_required；提交后用 app/api/member_auth.py 的 _clear_session_cookie 清除会员会话 cookie，204；提交或注销时数据库出错回滚，503 service_unavailable（开关开启时批准已被取用，须重新验证）；不写日志，响应与错误不含号码原文与密码
      - tests/test_member_delete_confirm_api.py 用 TestClient、SQLite 内存库、SHOP-TASK-067 的测试替身与内存 Redis 替身（照 tests/test_member_deletion_api.py 的写法，不加依赖）验证意图——检查顺序与不回显密码；开关开启时没有批准为 403 delete_not_verified、批准经 SHOP-TASK-081 的 verify 取得后可注销且只能用一次、给出正确密码也不能代替批准；开关在核验之后被关闭时批准不再被接受（已设密码者须输入密码）；开关关闭时已设密码者缺密码 422、密码错误 403、锁定后 429、正确密码可注销；与 SHOP-TASK-077 的 POST /api/member/password-login 交叉验证共用计数（从同一来源对同一号码，登录失败累计后注销被锁定，注销失败累计后登录被锁定）；未设密码者凭会话与 CSRF 注销；注销后会话失效、cookie 已清除、会员已为 deleted 且其订单的 member_id 为空；开关开启、或开关关闭且已设密码时，Redis 不可用为 503 且未注销；开关关闭且未设密码时 Redis 不可用照样注销；每条测试写明它守住的是设计或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-082 记录段（确认方式、检查顺序、结果与状态码、偏离、验证到什么程度，并写明前端注销入口留给之后的任务、积分与优惠券作废留给优惠券与积分账本任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；app/api/member_deletion.py 里 SHOP-TASK-081 的两个接口行为不变（模块说明可随之补充）；不改 app/services/ 下的文件、app/api/member_auth.py、tests/test_member_deletion_api.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41、0.45），不合并、不直推
```

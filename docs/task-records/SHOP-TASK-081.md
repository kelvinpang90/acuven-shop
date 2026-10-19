# SHOP-TASK-081 发送与核验会员注销验证码的接口

- [x] 按 `docs/DESIGN.md` 1.11（提交 `2d13250`）「权限与资料保护」第 3 条（账号注销须先以短信验证码确认）、「边界与原则」第 4 条（发送短信与提交验证码时按当时读取的开关值判定，开关在流程途中被关闭后不能以短信完成注销确认）与「失败、并发与重试」、`docs/UX.md` 0.10 P13（V1 用途「注销确认」，号码固定为本账号；验证通过后才出现确认注销），以及 `docs/HANDOFF.md` 0.45 记录的 Kelvin 2026-10-10 决定（1）（注销验证码核验通过后记一条绑定当前会员会话的一次性批准，10 分钟有效、用一次即删，凭登录会话与 CSRF 令牌、不另发 cookie），在 SHOP-TASK-069 的 `send_verification`、`verify_code`，SHOP-TASK-076 的 `require_member`、`require_member_csrf` 与 SHOP-TASK-080 的 `issue_delete_approval` 之上新增 `app/api/member_deletion.py`（`app/main.py` 挂上路由），提供 `POST /api/member/delete/send-code` 与 `POST /api/member/delete/verify`；测试见 `tests/test_member_deletion_api.py`。未改 `app/services/`、`app/api/sms.py`、`app/api/member_auth.py`、`.platform/`、CI、部署配置、前端或设计，未加表或迁移，未装依赖。设计闸门：DESIGN 1.11（提交 `2d13250`）与 HANDOFF 0.41、0.45（会员注销与个人资料）。
- 接口：路由器 `prefix="/api/member/delete"`，用 SHOP-TASK-027 的 `_NoStoreRoute`，处理函数与依赖产生的全部响应（含 413、415、401、403、422、503、`RateLimitUnavailable` 转成的 503 与 `get_session` 的 503）都带 `Cache-Control: no-store`；路径存在但方法不匹配的 405 由框架在路由之外回答，不在此列（不含请求内容）。`require_member`、`require_member_csrf`、核验结果的对应表 `_ERRORS`、`CODE_MAX_CHARS` 与 `_rollback` 从 `app/api/member_auth.py` 引用；发送结果的对应表 `_ERRORS` 与 `get_sms_redis_client` 从 `app/api/sms.py` 引用（以模块 `sms` 引入），都未另写。不写日志；号码原文、验证码、人机挑战令牌、cookie 值、会话 ID 与会员 ID 不出现在响应体与错误里。
- 检查顺序（两个接口相同，照 `app/api/admin_store_design.py` 的保存）：413（请求体逐块读，超过 4 KB，先于一切）→ 415（不是 JSON）→ 401 `member_session_required`（`require_member`）→ 403 `csrf_failed`（`require_member_csrf`）→ 422（结构，严格类型，多出字段 422，含 `phone`、`phone_region`、`purpose`；沿用 `app/api/pay.py` 的 `_body_errors`，只给位置、类型与固定消息，不回显请求内容）。CSRF 与结构写在请求体依赖 `_send_input` / `_verify_input` 里，它们以 413 / 415 的读取依赖与 `require_member` 为入参。号码一律取当前会员的手机号，请求体里不接受号码。
- 字段：send-code 只有 `captcha_token`（1 到 2048 个字符的字符串，上限取 `MAX_TOKEN_LENGTH`）；verify 只有 `code`（1 到 16 个字符的字符串，是否为 4 到 10 位数字由核验函数判断）。
- 发送 `POST /api/member/delete/send-code`：Redis 客户端用 `get_sms_redis_client`（不可用时为 `None`，交给发送函数按停发处理），来源取 `client_source`，以用途 `delete_account` 调用 `send_verification`。结果与状态码同 `POST /api/sms/send`：

  | 发送结果 | 回答 |
  |---|---|
  | `sent` | 200 `{"status":"sent"}` |
  | `sms_disabled` | 403 `sms_disabled` |
  | `not_whitelisted` | 422 `not_whitelisted` |
  | `captcha_failed` | 403 `captcha_failed` |
  | `rate_limited` | 429 `rate_limited` |
  | `undeliverable` | 422 `sms_undeliverable` |
  | `suspended` | 503 `sms_suspended` |

  `SmsVerificationError` 与 `SmsBudgetError` 时回滚，503 `sms_suspended`。`app/api/sms.py` 未改，公开发送接口仍不接受 `delete_account`。
- 核验 `POST /api/member/delete/verify`：422 之后，当次从数据库读取的短信验证开关关闭（含无设置行）即 403 `sms_disabled`，不取 Redis 客户端、不 PING、不核验（依赖 `_verify_enabled`，声明在 Redis 客户端依赖之前）→ 取 Redis 客户端（`get_redis_client`；未配置时路由类回答 503 `service_unavailable`）→ 一次 PING，`RedisError` 转为 `RateLimitUnavailable`（503 `service_unavailable`，不核验、不消耗验证码）→ 以用途 `delete_account` 调用 `verify_code`。结果与状态码：

  | 核验结果 | 回答 |
  |---|---|
  | `approved` | 提交后以 `issue_delete_approval(客户端, 当前会话 ID, 会员 ID)` 记批准，204 且响应体为空 |
  | `wrong_code` | 422 `code_wrong` |
  | `expired`、`no_pending` | 422 `code_expired` |
  | `unavailable` | 503 `sms_unavailable` |
  | `sms_disabled` | 403 `sms_disabled` |

  非 `approved` 时也先提交再回答（`expired` 时记录已提交为 `rejected`）。提交之后签发时 Redis 出错为 503 `service_unavailable`（验证记录已为 `approved`，须重新发送验证码）。核验或提交时数据库出错（`SmsVerificationError`、`SQLAlchemyError`）回滚，503 `sms_unavailable`。两个接口都不注销、不改会员、不撤销会话、不设 cookie。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 路径前缀为 `/api/member/delete`，与验收所写的两个路径一致；approved 时的 204 不带响应体，批准只在服务端（Kelvin 10-10「不另发 cookie」），页面在 204 后显示确认注销。
  - 读取开关时数据库出错：回滚后 503 `sms_unavailable`（与本接口其他数据库错误相同，同 SHOP-TASK-079 的验证接口）。验收未单独写这一步。
  - 发送接口在 422 之后不另判开关：开关、白名单、人机挑战、限流与预算的先后都由 `send_verification` 决定。开关关闭时 `get_sms_redis_client` 依赖已被解析，但它只建客户端、不连 Redis。
  - 会员号码不在白名单（如手动写库的英国号码）时 send-code 为 422 `not_whitelisted`，照公开发送接口；正常注册的会员号码都在白名单内。
  - 4 KB 逐块读取在本模块另写一份（写法同 `app/api/member_pw.py`、`app/api/member_pw_reset.py`）：`app/api/pay.py` 的 `_json_body` 上限是该文件的 8 KB。
  - 本接口不另做核验限流：错误验证码的尝试次数由 Twilio Verify 自身的上限兜底（与 sms-login、重设密码相同），发送侧有人机挑战与限流。
- 留给之后的任务：确认注销留给 SHOP-TASK-082（取用 `consume_delete_approval` 后调用 `delete_member`、提交并清除会话 cookie；开关关闭时以当前密码或已登录会话二次确认）；前端会员中心 P13 的注销（V1 用途「注销确认」、204 后显示 `[account.delete_confirm]`、开关关闭时的密码或会话确认）留给之后的任务（随「会员中心 P13 与订单详情、退款申请的会员模式」登记）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11「权限与资料保护」第 3、5、6 条、「边界与原则」第 4 条、「失败、并发与重试」第 3、4 条、UX P13 与 HANDOFF 0.45 的 Kelvin 决定原句自查。`tests/test_member_deletion_api.py` 用 `TestClient`（`https://testserver`）与 SQLite 内存库（引擎设置同 `tests/test_member_auth_api.py`），会话依赖换成绑定同一内存库的会话（可换成提交即抛错的子类），服务商与人机挑战用 SHOP-TASK-067 的测试替身（服务商另加逐次不同的请求 ID 与抛异常的子类），`get_redis_client` 与 `get_sms_redis_client` 都换成本文件的内存替身 `FakeRedis`（PING、带 EX 的 SET、事务管道 GET、DEL、INCR 与 EXPIRE，替身时钟，按命令注入的连接错误；未加依赖）；会员与会员会话直接写库；结果都在另一个数据库会话里读；期望的 Redis 键与 CSRF 令牌在测试里另算；每条测试的文档字符串写明守住的设计原句、UX、Kelvin 决定或验收条目。覆盖：send-code 200 `{"status":"sent"}`、记录为会员自己的号码与用途 `delete_account`（另一会员登录时记录其号码）、会员与会话不变；无设置行与开关关闭 403 且不调用人机挑战与服务商、不写记录；`not_whitelisted` 422、`captcha_failed` 403、`rate_limited` 429、`undeliverable` 422、服务商不可用 503 `sms_suspended`；Redis 客户端为 `None` 时 503 `sms_suspended`；`SmsBudgetError` 与服务商异常 503 `sms_suspended` 且不留记录；verify 通过 204、响应体为空、不设 cookie、Redis 里只有当前会话 ID 的批准、值为会员 ID、过期 600 秒、PING 一次、记录为 `approved`，会员仍为 `active`、会话未撤销且取当前会话 200；同一会员另一个会话的批准不能被本会话取用（以另一会话 ID 取用为假且原批准仍在，原会话取用为真）；只有登录用途或另一号码的注销记录时 422 `code_expired` 且不调用服务商；`wrong_code`、`expired`（记录已提交为 `rejected`）、`no_pending`、`unavailable` 的状态码与错误体；无设置行与开关关闭 403 且不 PING、不核验；开关关闭时 Redis 故障或未配置仍为 403；Redis 未配置与 PING 失败 503 `service_unavailable` 且不调用服务商、记录仍为 `sent`；签发失败 503 `service_unavailable`、无批准、记录为 `approved`；服务商异常与提交失败 503 `sms_unavailable`、记录仍为 `sent`；两个接口的 413（有无会话、三种 Content-Type、Redis 未配置、分块发送都是 413）、恰好 4 KB 放行、415 先于会话、没有会话 / cookie 格式不对 / 会话不存在 / 已撤销时结构不对也是 401、缺失 / 错误 / 重复 / 为空的 CSRF 在结构不对时也是 403、send-code 7 种与 verify 8 种结构错误（含多出 `phone`）422 不回显且不发送、不核验、不 PING，逐步的检查顺序（含 verify 之后的开关与 Redis）；两个路径方法不匹配的 405 不含请求内容；无 `app.` 日志，任何日志都不含号码、验证码、令牌与会话 cookie；响应体与错误不含号码原文、验证码、人机挑战令牌与 Redis 主机。未连真实 Twilio Verify、Turnstile、Redis 或 MySQL。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/api/member_deletion.py（app/main.py 挂上路由）提供 POST /api/member/delete/send-code 与 POST /api/member/delete/verify；路由器用 SHOP-TASK-027 的 _NoStoreRoute，处理函数与依赖产生的响应（含错误）都带 Cache-Control 为 no-store；路径存在但方法不匹配时由框架返回的 405 不含请求内容与个人资料，不在此列；require_member、require_member_csrf 从 app/api/member_auth.py 引用，不另写一份
      - 两个接口的检查顺序照 app/api/admin_store_design.py 的保存接口——413（逐块读，超过 4 KB，先于一切）→ 415（不是 JSON）→ 401 member_session_required（require_member）→ 403 csrf_failed（require_member_csrf）→ 422（结构，严格类型，多出字段 422，不回显请求内容，沿用 app/api/pay.py 的 _body_errors）；号码一律取当前会员的手机号，请求体里不接受号码
      - send-code 的请求体只有 captcha_token（1 到 2048 个字符的字符串）；Redis 客户端用 app/api/sms.py 的 get_sms_redis_client（不可用时为 None，交给发送函数按停发处理）；以用途 delete_account 调用 send_verification，结果到响应与 POST /api/sms/send 相同（沿用 app/api/sms.py 的 _ERRORS 映射；sent 为 200 {"status":"sent"}），SmsVerificationError 与 SmsBudgetError 时回滚，503 sms_suspended；不改 app/api/sms.py（公开发送接口仍不接受 delete_account）
      - verify 的请求体只有 code（1 到 16 个字符的字符串）；先按当次从数据库读取的短信验证开关判定，关闭即 403 sms_disabled（不 PING、不核验）；开启时用 app/services/rate_limit.py 的 get_redis_client 取客户端并在核验前执行一次 PING，未配置或 PING 失败（RedisError 转为 RateLimitUnavailable）由路由类回答 503 service_unavailable，此时不核验、不消耗验证码
      - verify 以用途 delete_account 调用 verify_code，非 approved 的结果到响应与 POST /api/member/sms-login 相同（wrong_code 为 422 code_wrong，expired 与 no_pending 为 422 code_expired，unavailable 为 503 sms_unavailable，sms_disabled 为 403 sms_disabled），都先提交再回答；approved 时提交后用 SHOP-TASK-080 的签发函数为当前会员会话记一条注销批准，204；提交之后签发时 Redis 出错为 503 service_unavailable（验证记录已为 approved，须重新发送验证码）；核验或提交时数据库出错回滚，503 sms_unavailable；两个接口都不注销、不改会员、不撤销会话
      - tests/test_member_deletion_api.py 用 TestClient、SQLite 内存库、SHOP-TASK-067 的测试替身与内存 Redis 替身（照 tests/test_member_auth_api.py 与 tests/test_sms_api.py 的写法，不加依赖）验证意图——两个接口完整的检查顺序（含没有会话且请求体超限为 413、没有会话且结构不对为 401）；请求体里多出 phone 为 422；send-code 用的是会员自己的号码与用途 delete_account，各结果的状态码与错误体；verify 各个非 approved 结果、开关关闭时为 403 且即使 Redis 故障也不是 503、Redis 未配置与 PING 失败时 503 且不调用服务商；approved 后 Redis 里有绑定当前会话的批准、会员仍为 active 且会话仍有效；另一个会话的批准不能被本会话取用；no-store；响应与错误不含号码原文与验证码；每条测试写明它守住的是设计、UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-081 记录段（接口、字段、检查顺序、结果与状态码、偏离、验证到什么程度，并写明确认注销留给 SHOP-TASK-082、前端留给之后的任务），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的文件、app/api/sms.py、app/api/member_auth.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.41、0.45），不合并、不直推
```

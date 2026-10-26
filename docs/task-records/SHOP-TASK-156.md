# SHOP-TASK-156 取会员本人完整手机号的只读接口

设计闸门：DESIGN 1.14（提交 00f8f34）「权限与资料保护」第 8 条（会员结账预填本人手机号：只认会员会话、GET、不要求 CSRF、只返回这一个值；会话不通过 401 且不返回号码；不受短信验证开关影响；不另设限流与审计；no-store；号码不进日志、监控、分析事件或错误回显）与 HANDOFF 0.63，个人资料（手机号）。依据 docs/UX.md 0.13 的 P05 第 3 步（会员收货电话预填会员手机号，可改为其他号码或虚构号码）。

- [x] 接口：`app/api/member_auth.py` 的会员路由器（沿用 `_NoStoreRoute`）新增 `GET /api/member/phone`（处理函数 `member_phone`）。不读请求体，不定义查询参数，不要求 `X-CSRF-Token` 与 `Idempotency-Key`；`app/main.py` 未改（路由器已注册）。
- [x] 授权与错误：只经 `require_member` 授权；会话不通过一律 401 `member_session_required`（不区分会话不存在、已撤销、已到期或会员已注销），响应体不含号码。会员没有号码时照 `current_session` 的类型收窄写法回 401 `member_session_required`。不读短信验证开关，不另设限流与审计记录；只读，不写库、不延长会话。没有请求体与参数，不产生 422，没有用 `_body_errors` 与 `_language`。处理函数与依赖产生的响应（含 401）经路由类带 `Cache-Control: no-store`。
- [x] 响应字段：200 `{"phone": <会员账号上的 E.164 号码原文>}`，响应模型 `MemberPhoneOut` 只有 `phone` 一个字段（`extra="forbid"`，字段 `repr=False`），不带遮掩号码、has_password、expires_at、csrf_token、收货资料或其他字段。
- [x] 模块开头的说明补上第四个接口（依据 DESIGN 1.14 第 8 条与 HANDOFF 0.63；只读、401、no-store、不受开关影响、不设限流与审计；GET /api/member/session 仍只给遮掩号码），「三个接口……都带 no-store」改为「四个接口」。现有三个接口与 `require_member`、`require_member_csrf`、`mask_phone` 未改；本模块仍不写日志。
- [x] 测试 `tests/test_member_phone_api.py` 新增（TestClient 与 SQLite 内存库，夹具、会员与会话的直接写库照 `tests/test_member_auth_api.py`）：
  - 已登录会员 200，键只有 `phone`、值为该会员的 E.164 号码（以加号开头），no-store，不设 cookie、不含令牌。
  - 两个会员（马来西亚与新加坡号码）各自只取到自己的号码。
  - 不带 `X-CSRF-Token` 与带了不对的值都照常 200。
  - 短信验证开关开启、关闭与没有设置行三种情形都 200 本人号码。
  - 连读两次前后会员表与会员会话表的全部行与各列不变。
  - 没有 cookie、格式不对、不存在、已到期、已撤销、会员已注销都 401 `member_session_required`，no-store，不设 cookie，响应体不含号码，不写库（库里另有一个有效会员会话）。
  - `GET /api/member/session` 的 `phone_masked` 仍为遮掩形式、响应不含完整号码。
  - POST 到该路径的 405 不含号码。
  - 200 与 401 都不产生 `app` 下的日志记录，任何日志都不含号码与令牌。
  - 每条测试的文档字符串写明它守住的是第 8 条的哪一句（或 SHOP-TASK-156 验收的哪项约定）。
- [x] 现有测试未改（含 `tests/test_member_auth_api.py`）。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 响应模型禁止多出字段用 `ConfigDict(extra="forbid")`；该约束作用在构造响应模型时，处理函数只给 `phone` 一个参数。
  - 测试另加了格式不对与不存在的 cookie 两种 401 情形（与 `tests/test_member_auth_api.py` 相同），以及 405 不含号码一条（验收写明 405 不在 no-store 之列，只断言不含号码，不断言 no-store）。
  - 前端预填（结账第 3 步取号、401 时按会话失效处理、其他错误时留空）不在本任务，留给 SHOP-TASK-157、159。
- 改动只在 `app/api/member_auth.py`、`tests/test_member_phone_api.py` 与本文件；未改 `app/main.py`、模型、迁移、服务层、`.platform/`、`docs/TODO.md`、CI、部署配置与前端，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.14 第 8 条与 `app/services/member_auth.py` 的 `check_member_session`（只读、不延长会话）自查。上面列的测试只是写入的测试，由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest；检查命令结果由 Worker 另行记录。只在 SQLite 内存库上写了测试，未在真实 MySQL 上验证。

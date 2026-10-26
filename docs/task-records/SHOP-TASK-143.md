# SHOP-TASK-143 短信、人机挑战与联系链接的配置传入容器，站点设置下发人机挑战公开 key

- [x] `docker-compose.yml` 的 `shop_api.environment` 加入 16 项（原先只传数据库、Redis 与 Stripe，`app/core/config.py` 里的短信、人机挑战、短信预算与限流、WhatsApp 联系链接传不进容器，运营者在服务器 `.env` 里填了也不生效）：
  - 写法 `${变量名:-}`，未设置时为空串（未配置）：`SHOP_TWILIO_ACCOUNT_SID`、`SHOP_TWILIO_AUTH_TOKEN`、`SHOP_TWILIO_VERIFY_SERVICE_SID`、`SHOP_TWILIO_VERIFY_BASE_URL`、`SHOP_TURNSTILE_SECRET_KEY`、`SHOP_TURNSTILE_VERIFY_URL`、`SHOP_TURNSTILE_SITE_KEY`、`SHOP_WHATSAPP_CONTACT_URL`。
  - 写法 `${变量名:-默认值}`，默认值与 `app/core/config.py` 相同：`SHOP_SMS_DAILY_COUNT_LIMIT`（200）、`SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD`（20000000）、`SHOP_SMS_MAX_COST_MICRO_USD_MY`（0）、`SHOP_SMS_MAX_COST_MICRO_USD_SG`（0）、`SHOP_SMS_PHONE_LIMIT_PER_MINUTE`（1）、`SHOP_SMS_PHONE_LIMIT_PER_HOUR`（5）、`SHOP_SMS_PHONE_LIMIT_PER_DAY`（10）、`SHOP_SMS_SOURCE_LIMIT_PER_HOUR`（10）。
  - 照 Stripe 一组的写法，两组前各有一行注释说明可以不设、未设置时照常部署；`shop_jobs` 未加（定时任务不发短信，注释写明）。
- [x] `app/core/config.py` 新增 `turnstile_site_key: str = ""`，写在 `turnstile_secret_key`、`turnstile_verify_url` 之后，照它们的写法注释（公开 site key，空串 = 未配置，不合格按未配置处理，服务端核验不用它）。
- [x] `app/api/site_settings.py` 的响应在 `whatsapp_contact_url` 之后多出 `turnstile_site_key`，由新函数 `turnstile_site_key(configured)` 判定（照 `whatsapp_contact_url` 的写法另写）：
  - 取值规则：配置值去掉首尾空白后非空、不超过 256 个字符（`TURNSTILE_SITE_KEY_MAX_LENGTH`）、不含空白（`str.isspace`，含全角空格）与控制字符（Unicode 类别 Cc）时返回去掉首尾空白后的值，否则（含未配置）为 null；不合格时不报错、不写日志。
  - 不读、不返回 `turnstile_secret_key` 与 `turnstile_verify_url`；`sms_verification_enabled`、`whatsapp_contact_url`、`Cache-Control: no-store` 与只发 SELECT 不变。模块文档字符串补上这一项（「返回两个字段」改为三个）。
- [x] 测试：
  - `tests/test_site_settings.py` 新增「人机挑战的公开 site key」一组：合格值原样返回；首尾空白去掉；恰好 256 个字符返回；空串、只有空白、257 个字符、含空格 / 制表符 / 全角空格 / 内部换行 / NUL / DEL / ESC 时为 null，200、不写 app 日志；secret key 与核验地址都已配置时响应仍只有三个字段、两者的值不出现在响应体里、没配 site key 时为 null；配置了 site key 时仍不需登录、不设 cookie、带 no-store、只发 SELECT。
  - 现有测试的修改：对响应整体做相等比较的断言（`test_endpoint_follows_the_stored_switch` 四处、`test_endpoint_returns_only_the_switch_with_no_store`、`test_endpoint_needs_no_login_sets_no_cookie_and_only_reads`、`test_configured_contact_url_is_returned_as_is`、`test_unusable_contact_url_is_null_without_error_or_log`）补上 `"turnstile_site_key": None`；字段列表断言（`test_endpoint_returns_only_the_switch_with_no_store`、`test_contact_url_comes_after_the_switch`）补上第三项；改动处都有注释写明原因。`test_endpoint_returns_only_the_switch_with_no_store` 的文档字符串改为三个字段，模块文档字符串补上依据。其余现有断言未动。
  - 新文件 `tests/test_compose_env.py`（照 `tests/test_stripe_gateway.py` 的 `test_compose_passes_all_six_variables_to_api_and_jobs`，用 yaml 读 `docker-compose.yml`）：16 项都在 `shop_api.environment` 且写法逐字相同；compose 里的默认值与 `Settings` 字段默认值相同；16 项都不在 `shop_jobs.environment`；`Settings` 每个字段（`git_sha` 除外）对应的 `SHOP_` 变量都在 `shop_api.environment` 里（以后新增配置漏传时失败）。
- 偏离与验收标准未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 目的里写「共 15 个变量」，指 `app/core/config.py` 原有的 15 项；加上新增的 `SHOP_TURNSTILE_SITE_KEY` 共传入 16 项，与验收第 1 条列出的一致。
  - 多加了一条测试 `test_compose_defaults_match_settings_defaults`，守住「默认值与 config.py 相同」，验收没有要求单列。
  - `site_settings` 接口里先取 `request.app.state.settings` 再读两项，只是写法调整，行为不变。
- 前台读取留给 SHOP-TASK-144（本任务不改前端）。`.env.example` 的 `SHOP_TURNSTILE_SITE_KEY` 一行已由登记 PR 加入，本任务未改。
- 运营者要在服务器 `.env` 里填写的变量名（不填时照常部署，短信与人机挑战为未配置、联系入口隐藏、预算与限流取默认值）：`SHOP_TWILIO_ACCOUNT_SID`、`SHOP_TWILIO_AUTH_TOKEN`、`SHOP_TWILIO_VERIFY_SERVICE_SID`、`SHOP_TWILIO_VERIFY_BASE_URL`、`SHOP_TURNSTILE_SECRET_KEY`、`SHOP_TURNSTILE_VERIFY_URL`、`SHOP_TURNSTILE_SITE_KEY`、`SHOP_WHATSAPP_CONTACT_URL`，按需调整 `SHOP_SMS_DAILY_COUNT_LIMIT`、`SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD`、`SHOP_SMS_MAX_COST_MICRO_USD_MY`、`SHOP_SMS_MAX_COST_MICRO_USD_SG`（这两项为 0 时该目的地停发）、`SHOP_SMS_PHONE_LIMIT_PER_MINUTE`、`SHOP_SMS_PHONE_LIMIT_PER_HOUR`、`SHOP_SMS_PHONE_LIMIT_PER_DAY`、`SHOP_SMS_SOURCE_LIMIT_PER_HOUR`。仓库只记录变量名，不记录取值。
- 改动只在 `docker-compose.yml`、`app/core/config.py`、`app/api/site_settings.py`、`tests/test_site_settings.py`、`tests/test_compose_env.py` 与本文件；未改 `app/services/captcha.py`、`app/services/sms_provider.py` 与其他短信规则，未改 `.env.example`、前端、迁移、CI、`deploy/deploy.sh`、`.platform/` 与 `docs/TODO.md`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准与 `app/core/config.py` 的字段及默认值自查。上面列出的测试由 PR 的必需 CI 检查 backend 执行（pytest，含 docker compose config 校验），Worker 沙箱不跑 pytest；检查命令结果由 Worker 另行记录。未在服务器上部署验证容器内实际读到的值。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - docker-compose.yml 的 shop_api.environment 加入 SHOP_TWILIO_ACCOUNT_SID、SHOP_TWILIO_AUTH_TOKEN、SHOP_TWILIO_VERIFY_SERVICE_SID、SHOP_TWILIO_VERIFY_BASE_URL、SHOP_TURNSTILE_SECRET_KEY、SHOP_TURNSTILE_VERIFY_URL、SHOP_TURNSTILE_SITE_KEY 与 SHOP_WHATSAPP_CONTACT_URL（写法为 ${变量名:-}），以及 SHOP_SMS_DAILY_COUNT_LIMIT、SHOP_SMS_DAILY_COST_LIMIT_MICRO_USD、SHOP_SMS_MAX_COST_MICRO_USD_MY、SHOP_SMS_MAX_COST_MICRO_USD_SG、SHOP_SMS_PHONE_LIMIT_PER_MINUTE、SHOP_SMS_PHONE_LIMIT_PER_HOUR、SHOP_SMS_PHONE_LIMIT_PER_DAY、SHOP_SMS_SOURCE_LIMIT_PER_HOUR（写法为 ${变量名:-默认值}，默认值与 app/core/config.py 的默认值相同——200、20000000、0、0、1、5、10、10）；未设置时照常部署；shop_jobs 不加（定时任务不发短信）；照现有 Stripe 一组的写法加一行注释说明可以不设
      - app/core/config.py 新增 turnstile_site_key（字符串，默认空串），写在 Turnstile 两项旁边并照它们的写法注释；.env.example 里的 SHOP_TURNSTILE_SITE_KEY 一行已由登记 PR 加入（Worker 不能改该文件），本任务不改它
      - app/api/site_settings.py 的响应多出 turnstile_site_key——配置值去掉首尾空白后非空、不超过 256 个字符、不含空白与控制字符时原样返回，否则（含未配置）为 null；配置不合格时不报错、不写日志（照 whatsapp_contact_url 的写法另写一个函数）；不读、不返回 secret key 与核验地址；其余字段、no-store 与只发 SELECT 不变；模块文档字符串补上这一项
      - 用 pytest 在 tests/test_site_settings.py 验证意图——配置为合格值时原样返回、首尾空白去掉、空串、超长、含空白或控制字符时为 null；响应只有三个字段（secret key 与核验地址的配置值不出现在响应体里）；现有断言里对响应整体做相等比较的，期望补上 turnstile_site_key（取未配置的 null）并在改动处注明原因
      - 新文件 tests/test_compose_env.py 照 tests/test_stripe_gateway.py 的 test_compose_passes_all_six_variables_to_api_and_jobs 的写法，读取 docker-compose.yml，断言第一条列出的 16 个变量在 shop_api.environment 里且写法逐字相同、都不在 shop_jobs.environment 里，并断言 app/core/config.py 的 Settings 每个字段（git_sha 除外）对应的 SHOP_ 变量都出现在 shop_api.environment 里（以后新增配置漏传时失败）；每条测试的文档字符串写明它守住的是什么
      - 由 PR 的必需 CI 检查 backend 执行 pytest（含 docker compose config 校验）并全部通过（Worker 沙箱不跑 pytest）；docs/task-records/SHOP-TASK-143.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；传入的变量、新字段与取值规则、偏离、验证到什么程度，并写明前台读取留给 SHOP-TASK-144、运营者要在服务器 .env 里填写的变量名）
      - 改动只落在 allowed_change_paths；不改 app/services/captcha.py、app/services/sms_provider.py 与其他短信规则，不改 .env.example、前端、迁移、CI、deploy/deploy.sh、.platform/ 与 docs/TODO.md；不装依赖；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

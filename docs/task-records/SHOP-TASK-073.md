# SHOP-TASK-073 站点设置接口提供 WhatsApp 联系链接

- [x] 按 `docs/DESIGN.md` 1.11「上线依赖与设计闸门」（配置 WhatsApp 联系方式，仓库只记录变量名）与 `docs/UX.md` 0.10 待决问题 Q10（配置缺失时隐藏 WhatsApp 按钮，不显示占位文字），给 SHOP-TASK-022 的公开接口 `GET /api/site-settings` 加上 WhatsApp 联系链接：改 `app/core/config.py`、`app/api/site_settings.py`；测试见 `tests/test_site_settings.py`。未改短信验证开关的读取与判定，未加表或迁移，未改 `.env.example`（变量名已由登记 PR 写入）、前端、设计、`.platform/`、CI 或部署配置，未装依赖。设计闸门：不适用（公司公开的联系链接，不涉及金额计算与访客个人资料）。拆分：接口层（4 个允许路径）。
- 配置项：`Settings.whatsapp_contact_url`（环境变量 `SHOP_WHATSAPP_CONTACT_URL`），默认空串即未配置。
- 字段与校验：响应在 `sms_verification_enabled` 之后加 `whatsapp_contact_url`（`str | None`），即 `{"sms_verification_enabled": <布尔>, "whatsapp_contact_url": <字符串或 null>}`。取值由 `app/api/site_settings.py` 的 `whatsapp_contact_url(configured)` 算出：配置值去掉首尾空白（`str.strip()`）后，非空、不超过 512 个字符（`WHATSAPP_CONTACT_URL_MAX_LENGTH`，按字符计）、没有任何空白字符（`str.isspace()`，含全角空格）与控制字符（Unicode 类别 `Cc`，含 NUL、DEL、CR、LF）、经 `urllib.parse.urlsplit` 解析协议为 `https` 且 `hostname` 非空时返回去掉空白后的字符串，否则（含未配置、`urlsplit` 抛 `ValueError`）返回 `null`。配置不合格时不报错、不写日志。配置值每次请求从 `request.app.state.settings` 取，不查库；接口其余行为不变（不需登录、不读写 cookie、只发开关的那条 SELECT、`Cache-Control: no-store`、写方法 405、未配置数据库时 503）。模块说明改写为两个字段各自的用途与依据。
- 改动的现有测试（只按验收改）：`test_endpoint_follows_the_stored_switch` 的四处、`test_endpoint_returns_only_the_switch_with_no_store` 的整体相等与字段列表、`test_endpoint_needs_no_login_sets_no_cookie_and_only_reads` 的整体相等，这些断言都在断言整个响应体只含 `sms_verification_enabled`，现改为另含 `"whatsapp_contact_url": None`（字段列表改为两项）；`test_endpoint_returns_only_the_switch_with_no_store` 的文档字符串随之改为两个字段。测试模块说明补上 DESIGN「上线依赖与设计闸门」与 UX Q10 的依据。其余现有断言未动。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 协议比较不分大小写（`urlsplit` 本身把协议转成小写，`HTTPS://…` 视为 https），返回的是去掉空白后的原字符串，不做规范化。
  - 「主机部分非空」按 `urlsplit(...).hostname` 判断：只有端口（`https://:443/…`）、只有用户信息（`https://user@/…`）、没有 `//`（`https:host`）都算缺主机；不校验主机是否像域名，也不限定 `wa.me` 等具体主机，以便运营者填任意 WhatsApp 联系链接。
  - 只拒 `Cc` 类控制字符与空白；不拒其他不可见的格式字符（如 U+200B，验收未要求）。
  - 512 个字符按去掉首尾空白后的长度计。
  - 测试里的链接一律用 `chat.example.com`，仓库不出现真实号码或主机。
- 留给之后的任务：前台页脚与隐私说明 P14 在已配置时显示 WhatsApp 联系入口、未配置时隐藏（SHOP-TASK-074）。真实链接由运营者在服务器私有配置里填写 `SHOP_WHATSAPP_CONTACT_URL`，仓库不存。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.11「上线依赖与设计闸门」与 UX Q10 原句自查。`tests/test_site_settings.py` 新增的测试用 SQLite 内存库与 TestClient，以 `Settings(_env_file=None, whatsapp_contact_url=…)` 建应用，每条测试的文档字符串写明它守住的设计或 UX Q10 原句与验收条目，覆盖：已配置时原样返回（开关字段照常）；首尾空白与换行去掉；恰好 512 个字符返回；空串、只有空白、http、ftp、javascript、whatsapp 自定义协议、无协议、以 `//` 开头、`https://`、空主机、只有端口、只有用户信息、`https:` 后无 `//`、残缺的 IPv6 方括号、中间含空格、制表符、全角空格、NUL、DEL、CRLF、513 个字符都为 `null`，状态码 200 且没有 `app` 下的日志；已配置与未配置时字段顺序都是开关在前、链接在后；已配置时带不带 cookie 结果相同、不设 cookie、只发 SELECT、带 `no-store`。这些测试只由 PR 的必需 CI 检查 backend 执行，Worker 沙箱不跑 pytest。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - app/core/config.py 加配置项 whatsapp_contact_url（环境变量 SHOP_WHATSAPP_CONTACT_URL，默认空串即未配置；变量名已由登记 PR 写进 .env.example，本任务不改它）
      - GET /api/site-settings 的响应在 sms_verification_enabled 之后加字段 whatsapp_contact_url——配置值去掉首尾空白后，协议为 https、主机部分非空、不含空白与控制字符、长度不超过 512 个字符时为该字符串，否则（含未配置）为 null；配置不合格时不报错、不写日志；接口其余行为不变（不需登录、不读写 cookie、只发 SELECT、Cache-Control 为 no-store）；模块说明随之改写
      - tests/test_site_settings.py 只许把断言整个响应体只含 sms_verification_enabled 一个字段（含字段列表）的现有断言改为另含 whatsapp_contact_url（未配置时为 null），其余现有断言不动；并新增——已配置时原样返回、首尾空白去掉、非 https 协议、缺主机、含空白、超过 512 个字符与空串都为 null、字段顺序；每条测试写明它守住的是设计或 UX Q10 的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-073 记录段（配置项、字段与校验、改动的现有测试、偏离、验证到什么程度，并写明前台显示留给 SHOP-TASK-074、真实链接由运营者在服务器私有配置填写），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

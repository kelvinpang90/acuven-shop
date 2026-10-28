# SHOP-TASK-171 后台运费与演示汇率的读写接口

- [x] 按 `docs/DESIGN.md` 1.16（提交 `e9a1c93`）「计价、优惠、积分与库存」第 9 条（整页一次保存；先不碰数据库检查请求本身，不通过回答「取值不合格」；再锁行按行 ID 与版本号核对，不通过回答「数据已被改动」；每步不通过都整次保存不生效）、`docs/UX.md` 0.15 的 A06「保存与状态」（读取整页、整页一次保存、两种拒绝整次保存不生效、两者都有时先显示取值不合格）与 `docs/HANDOFF.md` 0.67 的落实写法（422 `rates_invalid`、409 `rates_changed`、请求体上限 64 KB 由接口文件逐块读取），新增 `app/api/admin_shipping_rates.py` 的 `GET /api/admin/shipping-rates` 与 `PUT /api/admin/shipping-rates`，调用 SHOP-TASK-170 的 `load_admin_rates` 与 `save_admin_rates`，并在 `app/main.py` 照现有后台路由的写法注册（`admin_site_settings_router` 之后）。改动只在 `app/api/admin_shipping_rates.py`、`app/main.py`、`tests/test_admin_shipping_rates_api.py` 与本文件。不做前端；未改 `app/services/admin_shipping_rates.py`、模型、迁移、现有接口与测试、`.platform/`、CI、部署配置；未装依赖；未改设计。设计闸门：DESIGN 1.16（提交 `e9a1c93`）与 HANDOFF 0.67，运费与演示汇率的后台维护。
- 写法照 `app/api/admin_site_settings.py`：路由类 `_NoStoreRoute`（处理函数与依赖产生的全部响应，含错误，都带 `Cache-Control: no-store`；路径存在而方法不匹配的 405 由框架回答，不在此列）、`AdminDep`（`require_admin`）、`require_admin_csrf`、`_body_errors`（422 只给位置、类型与固定消息，不回显）、`_utcnow`；管理员 ID 在调用 `save_admin_rates` 之前取出（它先回滚，会话里的 ORM 对象随之过期）。接口不写日志、GET 不写审计。
- `GET /api/admin/shipping-rates`：`require_admin`（没有有效后台会话时 401 `admin_session_required`）后返回 `load_admin_rates` 的整页数据与由 cookie 算出的 CSRF 令牌（`csrf_token_for_cookie`）。字段依次为 `states`、`other`、`countries`、`fx`、`csrf_token`；`states`、`countries` 为数组，`other` 为一个对象，运费行为 `id`、`version`、`zone_code`、`fee_sen`；`fx` 为数组，各行为 `id`、`version`、`country_code`、`currency_code`、`currency_decimals`、`rate`（服务层给出的规范形式字符串）。顺序沿用服务层：州属 MY-01 到 MY-16，国家运费与汇率按国家代码。
- `PUT /api/admin/shipping-rates` 的请求体（pydantic，`extra="forbid"`、`strict=True`，只校验结构与 JSON 类型）：
  - 四项 `states`、`other`、`countries`、`fx` 都必填；`states` 为数组，`other` 为一个对象（与 GET 的形状相同，交给服务层时包成一项的列表）。
  - 州属与兜底的行只接受 `{id, version, fee_sen}`。
  - 国家运费的行三选一：已有行 `{id, version, fee_sen}`、删除 `{id, version, delete: true}`（`delete` 为 `Literal[True]`，false 或其他值 422）、新增 `{country_code, fee_sen}`。
  - 汇率的行三选一：已有行 `{id, version, rate}`、删除 `{id, version, delete: true}`、新增 `{country_code, currency_code, currency_decimals, rate}`。
  - 整数字段不收布尔值、字符串与浮点数；`rate`、`country_code`、`currency_code` 只收 JSON 字符串。多出字段、缺字段或不合任何一种行 422。不加取值范围、格式与长度约束：运费越界、汇率格式、国家或币种不合格、行 ID 重复、新增国家重复等一律交给服务层，由 `RatesInvalid` 回 `rates_invalid`。
- 检查顺序与回答（每一步不通过即停止，被拒时不写入、不写审计）：
  1. 413 `request body too large`：按实际读到的字节逐块判断，超过 64 KB（`MAX_BODY_BYTES = 64 * 1024`）即停，先于一切校验；
  2. 415 `request body must be JSON`（读完之后判断 Content-Type，沿用 `app/api/orders.py` 的 `_is_json`）；
  3. 401 `admin_session_required`；
  4. 403 `csrf_failed`；
  5. 422 请求体（`_body_errors`，不回显）；
  6. 服务层 `RatesInvalid`：422 `{"detail":"rates_invalid"}`（接口回滚会话）；
  7. 服务层 `RatesChanged`：409 `{"detail":"rates_changed"}`（服务层已回滚）。
  成功回 200 与 `save_admin_rates` 返回的保存后整页数据，字段同 GET、不含 `csrf_token`。整页都未变时服务层不写入、不写审计，照样回 200 与当前数据。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 逐块读取的函数写在本文件（`_save_body`），与 `app/api/pay.py` 的 `_json_body` 只差上限（HANDOFF 0.67：那里固定 8 KB）；没有把上限做成参数去改 `pay.py`，该文件不在允许改动的路径内。
  - 保存请求里汇率组的键名用 `fx`（与 GET 相同），服务层参数名是 `fx_rates`，由接口转换。
  - 服务层的 `AdminRates.other` 在库里缺兜底行时为 None，GET 此时回 `"other": null`（响应模型声明为可空）；PUT 的 `other` 只收对象，此时任何保存都会在服务层第二步回 409 `rates_changed`。兜底行由迁移 0005 写入、只能改不能删，这种情况只在库被人为改坏时出现，未另加处理。
  - 请求体 422 的 `loc` 在三选一的行上会带 pydantic 的模型名（如 `KeepFeeIn`、`RemoveRowIn`），只是位置，不含提交的值。
- 留给之后的任务：A06 页面（国家运费与汇率的删除、新增汇率、保存后的三种状态）待视觉稿补齐、经 Kelvin 审阅后另行登记（HANDOFF 0.67 第（5）点）。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.16 第 9 条、UX 0.15 A06「保存与状态」与 HANDOFF 0.67 原句自查。新增 `tests/test_admin_shipping_rates_api.py`（夹具照 `tests/test_admin_site_settings.py`，运费与汇率写入与迁移 0005 相同的演示数据），覆盖：GET 的字段与顺序、行字段顺序、规范汇率、CSRF 令牌、不写审计；GET 与 PUT 在没有 cookie、格式不对、未知与已撤销会话时 401；PUT 的 413（超过 64 KB，有无会话、是否 JSON 都先回 413，不回显）、恰好 64 KB 的合法整页照常保存、415、结构不合格时没有会话先回 401、CSRF 缺少/为空/不一致时 403；请求体 422（布尔值、字符串与浮点数运费，字符串行 ID，布尔版本号，数字汇率，delete 为 false 或字符串，保留与删除同时给，州属与兜底带 delete，已有汇率行带币种或小数位，已有运费行带国家代码，新增行带行 ID、缺小数位、小数位为字符串、汇率为数字，多出字段，缺任一组，other 为数组或 null，整体不是对象）且不回显、数据不变；取值不合格（运费 -1 与 1,000,000，汇率 "1e5"、"0"、" 34"、"34."，新增国家 my、MY、nz，币种 MYR，小数位 4，同一组新增同一国家两次，同一组列同一行 ID 两次）回 422 `rates_invalid`；过期的版本号、漏列现有行、未知行 ID、新增已有的国家、读取后另一处改过的行回 409 `rates_changed`；两者都有时回 422 `rates_invalid`；被拒时数据与审计都不变；成功的整页保存（改州属、兜底、国家运费与汇率，删一行国家运费与一行汇率，新增一行国家运费与一行汇率）回 200 与随后 GET 相同的整页、版本号与审计（操作者为当前管理员、旧值新值）正确；同一次保存里删掉 SG 的运费与汇率再各新增一行成功，新行 ID 与旧的不同、版本号为 1、写四条审计，之后拿旧页面保存回 409；原样保存（汇率写成 0.310000）不写入；新增的响应（含错误）带 no-store、应用不记日志；该路径下只有 GET 与 PUT。每条测试的文档字符串写明它守住的 DESIGN 1.16 第 9 条、UX 0.15 A06、HANDOFF 0.67 原句或验收条目。测试交由 PR 的必需 CI 检查 backend 执行（Worker 沙箱不跑后端测试）。SQLite 不支持行锁，锁定在 MySQL 上的效果由 SHOP-TASK-170 的测试与代码审阅负责，本任务不另测。检查命令结果由 Worker 另行记录。

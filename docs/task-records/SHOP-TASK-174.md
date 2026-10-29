# SHOP-TASK-174 后台运费与演示汇率的请求模块 A06

- [x] 新增 `frontend/src/api/adminShippingRates.ts` 与 `frontend/src/api/adminShippingRates.test.ts`：为 A06 页面调用 SHOP-TASK-171 的 `GET /api/admin/shipping-rates` 与 `PUT /api/admin/shipping-rates`（马来西亚一组按 SHOP-TASK-172 为 `malaysia`）。写法沿用 `frontend/src/api/adminSiteSettings.ts`，`isRecord`、`isFilled`、`readBody` 等内部函数在本文件另写；CSRF 请求头名取 `frontend/src/api/pay.ts` 的 `CSRF_HEADER`。改动只在这两个文件与本文件；不做页面，不改路由与导航、后端、设计、`docs/TODO.md`、`.platform/`、CI 与部署配置，未装依赖。设计闸门：不适用（运费与汇率的取值规则、核对与审计在 SHOP-TASK-170 到 172 的服务端，依据 DESIGN 1.17 第 9、10 条；请求模块只传递与校验字段）。
- 请求：两个请求都不带查询参数，凭同源 cookie，`cache: "no-store"`，`Accept: application/json`，调用方可传入 `AbortSignal`。
  - `readAdminShippingRates(signal?)`：GET，没有请求体与 CSRF 头。
  - `saveAdminShippingRates(rates, csrfToken, signal?)`：PUT，`Content-Type: application/json`，`X-CSRF-Token` 为调用方给出的令牌。保存内容 `AdminRatesSave` 有 `malaysia`（`KeepFee` 数组）、`other`（一行 `KeepFee`）、`countries`（`KeepFee | RemoveRow | AddCountryFee`）与 `fx`（`KeepFxRate | RemoveRow | AddFxRate`）四项，各种行的字段与 `app/api/admin_shipping_rates.py` 的 `AdminRatesIn` 一致：保留的运费行 `id`、`version`、`fee_sen`；删除的行 `id`、`version` 与 `delete: true`；新增的国家运费行 `country_code`、`fee_sen`；保留的汇率行 `id`、`version`、`rate`；新增的汇率行 `country_code`、`currency_code`、`currency_decimals`、`rate`。JSON 请求体只有这四项，每行按种类重新组装、只写这些字段（删除行的 `delete` 固定写 `true`），调用方对象上多出的属性不进请求体。
- 字段与合格条件：读取 200 的响应体只取 `AdminRatesPageOut` 的 `malaysia`、`other`、`countries`、`fx` 与 `csrf_token`，各行只取列出的字段，多出的字段不带进结果。
  - `malaysia` 为数组，每行 `id`、`version` 为正整数，`zone_code` 为 `MY-W` 或 `MY-E` 且同一代码不出现两次，`fee_sen` 为 0 到 999999 的整数（HANDOFF 0.72 第（3）点；不要求两档都在）。
  - `other` 为一行，`zone_code` 为 `OTHER`，其余同上；为 null 时不合格（HANDOFF 0.72 第（3）点）。
  - `countries` 为数组，`zone_code` 为两位大写字母且不是 `MY`，其余同上。
  - `fx` 为数组，每行 `id`、`version` 为正整数，`country_code` 为两位大写字母且不是 `MY`，`currency_code` 为三位大写字母，`currency_decimals` 为 0 到 3 的整数，`rate` 为非空字符串。
  - `csrf_token` 为非空字符串。字段缺少或类型不对都算不合格，任一项不合格整个响应体不合格。保存 200 的响应体按同一条件读取，只是不取 `csrf_token`。
- 归类：读取为 `ok`（带 `rates` 与 `csrf_token`）、`none`（401）、`failed`（其他状态，含 200 但响应体不合格或不是 JSON）与 `network`（fetch 抛错，含调用方中止）。保存为 `ok`（带保存后的 `rates`）、`none`（401）、`csrf`（403）、`invalid`（422 且 `detail` 恰为 `rates_invalid`）、`changed`（409 且 `detail` 恰为 `rates_changed`）、`failed`（其他状态，含其他 422、其他 409、413、415 与 200 但响应体不合格）与 `network`。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 保存内容的行种类按有没有 `delete`、`country_code` 区分（先看 `delete`，再看 `country_code`，都没有为保留的行），与 `AdminRatesIn` 各种行的字段组合一致，不另加种类标记。调用方若在保留的行上另带 `country_code` 或 `delete` 属性，会被当作新增或删除的行；类型上各种行不含这些字段，页面按类型构造即可。
  - 读取不要求 `malaysia` 恰为两行、不要求 `countries` 或 `fx` 里国家不重复、不拒绝币种 `MYR`：验收标准没有列这几项，取值以服务端为准。
  - 汇率 `rate` 只校验为非空字符串，不在请求模块里检查十进制格式（交给服务端与 SHOP-TASK-175 的页面输入校验，HANDOFF 0.72 第（4）点）。
- 留给之后：A06 页面的读取显示、改值与整页保存交给 SHOP-TASK-175，删除与新增行交给 SHOP-TASK-176。
- 验证到什么程度：人工逐条对照验收标准、`app/api/admin_shipping_rates.py`、SHOP-TASK-171 与 172 记录、DESIGN 1.17 第 9 条与 HANDOFF 0.72 第（3）点自查。`frontend/src/api/adminShippingRates.test.ts`（vitest，fetch 以参数带类型的 `vi.fn` 替身代替）覆盖：两个请求的地址、方法、同源 cookie、no-store、Accept、Content-Type、CSRF 头与 signal；请求体恰为四项、各种行只有规定字段、删除行的 `delete` 为 true、调用方对象上多出的属性（含 `states`、`csrf_token`）不进请求体、空的组照样发出；读取与保存 200 时多出的字段不带进结果、运费 0 与 999999 和小数位 0 与 3 合格；读取与保存共用的不合格例（各组缺少或不是数组，`other` 为 null、缺少、数组、代码不是 `OTHER`，马来西亚行代码为州属代码、小写、`OTHER` 或重复，行 ID 与版本号为 0、小数或字符串，运费 1000000、小数、负数或字符串，国家代码为 `MY`、小写、三位或 `OTHER`，币种两位、小写或四位，小数位 4、负数、小数或字符串，汇率为空串或数字）与读取的 `csrf_token` 缺少、为空、数字或 null，整体不是对象或不是 JSON；读取与保存各状态码的归类（422 与 409 的 detail 对调、大小写不同、错误列表、不是 JSON 时归 failed，413、415 与其他状态归 failed）、网络中断与中止。每条测试的注释写明它守住的 SHOP-TASK-171、172 记录、DESIGN 1.17 第 9 条或 HANDOFF 0.72 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建；Worker 沙箱不跑前端检查）。本任务不做浏览器验收。检查命令结果由 Worker 另行记录。

# SHOP-TASK-267 节日促销海报图与 AI 提案的请求模块 A12

- [x] 新增 `frontend/src/api/adminPromotionPoster.ts` 与 `frontend/src/api/adminPromotionPoster.test.ts`：为 A12 海报区调用 SHOP-TASK-250 的 `PUT /api/admin/promotions/{活动 ID}/poster/{desktop|phone}`。写法照 `frontend/src/api/adminStoreLogo.ts`（HANDOFF 0.98）；UX 0.20 A12 的海报区只有上传即替换、没有移除，所以不包装同一路径的 `DELETE`。
- [x] 新增 `frontend/src/api/adminPromotionAi.ts` 与 `frontend/src/api/adminPromotionAi.test.ts`：为 A12 AI 区调用 SHOP-TASK-251 的 `GET /api/admin/promotion-ai/status` 与 `POST /api/admin/promotion-ai/proposal`；429 的响应体按 SHOP-TASK-265（`detail` 为 `{"code": "ai_daily_limit", "limit": …}`）。
- 改动只在这四个文件与本文件；不做页面，不改后端、路由与导航、设计、`.platform/`、CI 与部署配置，未装依赖。CSRF 请求头名取 `frontend/src/api/pay.ts` 的 `CSRF_HEADER`；活动 ID 上限与地址取 `frontend/src/api/adminPromotions.ts`（SHOP-TASK-266）的 `MAX_PROMOTION_ID` 与 `adminPromotionUrl`，AI 的规则与档位类型取同一文件的 `PromotionRuleInput` 与 `PromotionTier`。
- 设计闸门：DESIGN 1.23（提交 b7f9355）「节日促销活动」第 12、13 条——请求模块只传递与校验字段；AI 请求只带描述与规则参数，不带商品、分类与任何个人资料。

## 海报模块 `adminPromotionPoster.ts`

- 导出：`POSTER_SLOTS`（`desktop`、`phone`）、类型 `PosterSlot`、`adminPromotionPosterUrl(id, slot)`（`/api/admin/promotions/{id}/poster/{slot}`）、`isPromotionPosterPath(value)`、结果类型 `PosterUpload`。
- `uploadPromotionPoster(promotionId, slot, file, contentType, csrfToken, signal?)`：`PUT`，同源 cookie、`cache: "no-store"`，请求头 `Accept: application/json`、`Content-Type` 为调用方给的类型、`X-CSRF-Token`；请求体为文件本身（不用 FormData）。结果：
  - `ok`（200，带 `poster`）、`none`（401）、`csrf`（403）、`missing`（404，或活动 ID 不是不超过 2³¹−1 的正整数——不发请求）、`ended`（409 且 detail 为 `promotion_ended`）、`type_unsupported`（415 且 detail 为 `image_type_unsupported`）、`too_large`（413，不读响应体）、`invalid`（422 且 detail 为 `poster_invalid`）、`failed`（其他：503、其他 409 / 415 / 422（含 slot 与 ID 的 422）、200 但 poster 不合格、slot 不是 `desktop` 或 `phone`——不发请求）、`network`（fetch 抛错，含调用方中止）。
- `poster` 只认恰为 `/api/promotion-posters/` 加 32 位小写十六进制加 `.webp` 的字符串。

## AI 模块 `adminPromotionAi.ts`

- 导出：`ADMIN_PROMOTION_AI_STATUS_URL`、`ADMIN_PROMOTION_AI_PROPOSAL_URL`；类型 `AiRuleInput`（`PromotionRuleInput` 去掉 product_ids 与 category_ids）、`AiProposalRequest`（description、rules）、`AiProposal`（DecorationProposalOut 的二十个字段）、`AiProposalProblem`（code、field）、结果 `AiStatusRead` 与 `AiProposalResult`。
- `readAiStatus(signal?)`：`GET`，请求头只有 Accept。结果 `ok`（`configured` 为布尔值）、`none`（401）、`failed`、`network`。
- `requestAiProposal(request, csrfToken, signal?)`：`POST`，JSON 请求体，请求头带 Accept、`Content-Type: application/json` 与 `X-CSRF-Token`。结果：
  - `ok`（200，带 `proposal`）、`none`（401）、`csrf`（403）、`invalid`（422 且 `detail.code` 为 `promotion_invalid`，带 problems）、`not_configured`（503 `ai_not_configured`）、`daily_limit`（429 且 detail 为 `{"code": "ai_daily_limit", "limit": 整数}`，带 limit）、`unavailable`（503 `ai_unavailable`）、`result_invalid`（502 `ai_result_invalid`）、`failed`（其他：413、415、FastAPI 的 422、detail 不合格的 422 / 429 / 502 / 503、200 但响应体不合格）、`network`（fetch 抛错，含调用方中止）。
- 请求体：只有 `description` 与 `rules`；每条规则恰为 kind、discount_mode、percent、amount_sen、buy_quantity、get_quantity、get_percent、threshold_sen、tiers，没有值（undefined）的参数提交 null；tiers 每项恰为 threshold_sen、amount_sen、percent 三个键，没有值的为 null。调用方对象上多出的属性（含 product_ids、category_ids、rule_id、products）不带进请求体。
- 校验：proposal 的八个装饰 ID 与 `title_en` 须为字符串，其余十一项文案须在且为字符串或 null，任一项不合格整份为 `failed`；多出的字段不带进结果。problems 每项 code 为字符串，field 为 `description` 或以 `rules.<序号>` 开头，否则整份为 `failed`；每项只取 code 与 field。

## 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）

- 海报路径的检查比落实写法「以 /api/promotion-posters/ 开头、以 .webp 结尾」更严：照标志模块（`isStoreLogoPath`）要求文件名恰为 32 位小写十六进制，因为服务层 `app/services/promotion_poster.py` 的文件名为 `secrets.token_hex(16)`；括号里「照标志模块对提供路径的检查」即指这一判断。
- 参数检查的先后：slot 不合法时先返回 `failed`，再看活动 ID（`missing`）。两者都不合法时为 `failed`。
- 404 不读响应体，都归 `missing`（接口文档只有 `promotion_not_found` 一种 404，同 SHOP-TASK-266）。
- 429 的 limit 接受不小于 0 的整数：接口只写「配置的 ai_daily_call_limit」，没有写下限。
- problems 的 field 另做形状检查（`description` 或 `rules.<序号>` 开头），依据落实写法「描述不合格时 field 为 description，规则参数不合格时为 rules.N 开头」与服务层 `_checked_rules` 的写法；接口文档里的 product_id 与 product_name（AI 接口里总为 null）不带进结果。
- 档位的 threshold_sen 在 `PromotionTier` 里为 `number`（后端 `TierIn.threshold_sen` 不可为 null）；请求体照落实写法在缺少时也补 null，由后端以 422 拒绝，页面按 UX 0.20 A12 在提交前先检查档位。

## 留给之后

- A12 海报区页面（SHOP-TASK-275）与 AI 区页面。

## 验证到什么程度

- 人工逐条对照 `app/api/admin_promotion_poster.py` 与 `app/api/admin_promotion_ai.py` 的请求与响应模型、文档字符串与 UX 0.20 A12 自查。
- `adminPromotionPoster.test.ts`（vitest，fetch 以参数带类型的 `vi.fn` 替身代替）覆盖：两种 slot 的地址、方法、同源 cookie、no-store、请求头、请求体为原样的 Blob 与 signal；ok 只取 poster；不合格的 200（缺少、null、其他前缀与扩展名、大写、31 位、嵌套路径、非字符串、不是 JSON、201）；401、403、404、409、413（不读响应体）、415、422 与各自 detail 不对时的 `failed`、503 与其他状态；slot 不合法与活动 ID 不合法（0、负数、小数、超上限、NaN、Infinity）时不发请求，上限本身照发；网络中断与调用方中止为 `network`。
- `adminPromotionAi.test.ts` 覆盖：状态与提案的地址、方法、请求头与 signal；状态的两种取值、不合格响应与各状态码；提案请求体只取规定字段（给出读回的活动规则与多出属性时不带商品、分类 ID 与其他属性）、缺少的参数与档位键补 null；ok 只取二十个字段、逐字段的不合格；422 problems 的读取与各种不合格；429 的 limit 读取与 detail 不合格时为 `failed`；503 / 502 的各 detail、413、415 与其他状态；网络中断与调用方中止为 `network`。
- 每条测试的注释写明它守住的接口文档字符串、落实写法或 UX 0.20 A12 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（Worker 沙箱不跑前端检查）。本任务不做浏览器验收。

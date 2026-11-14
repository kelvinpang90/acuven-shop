# SHOP-TASK-251 AI 生成装饰提案的后台接口

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 13 条：接口只传递与校验字段，出站内容与计数在 SHOP-TASK-248 的服务层（`app/services/promotion_ai.py`）；不返回个人资料。依据 UX 0.20 A12 的 AI 区（未配置时 AI 按钮禁用并说明原因）。节日促销 AI 提案的第四个任务，依赖 SHOP-TASK-248。不改服务层规则、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `app/api/admin_promotion_ai.py`，照 `app/api/admin_categories.py` 的写法：路由类为 `_NoStoreRoute`（处理函数与依赖产生的全部响应、含错误，都带 `Cache-Control: no-store`）；`AdminDep`、`require_admin_csrf`、`WriteBodyDep`（`app/api/pay.py` 的 `_json_body`，逐块读取，上限 8 KB）；请求模型 `extra="forbid"`、`strict=True`；422 照 `_body_errors` 只给位置、类型与固定消息，不回显输入。路径放在 `/api/admin/promotion-ai/` 下，不放在 `/api/admin/promotions/` 下（免得被活动 ID 的路由先匹配）。不写日志。
- [x] `app/main.py`：后台段在最后（`admin_promotion_preview_router` 之后）注册 `admin_promotion_ai_router`。
- [x] 检查顺序（提案；每一步不通过即回答、不调用服务层）：
  1. 请求体超过 8 KB 413 `request body too large`，不是 JSON 415 `request body must be JSON`，先于一切；
  2. 会话：401 `admin_session_required`；
  3. CSRF（`X-CSRF-Token`）：403 `csrf_failed`；
  4. 请求体结构与 JSON 类型：422。
  状态接口只有 2.，不要 CSRF、不读请求体。
- [x] 供前台 A12 的 AI 任务引用的接口摘要：

  | 方法与路径 | 请求体 | 成功 |
  | --- | --- | --- |
  | `GET /api/admin/promotion-ai/status` | 无 | 200 `{"configured": true 或 false}`（只此一个字段） |
  | `POST /api/admin/promotion-ai/proposal` | `{"description", "rules"}` | 200 `{"proposal": DecorationProposal}`（只此一个字段） |

  - `configured`：按本应用配置构造的 `AiHubClient.configured`（`SHOP_AI_*` 四项都有、接口地址为 `https://`）。为 `false` 时页面禁用 `[admin.ai_generate]` 并显示 `[admin.ai_not_configured]`。只读，不调用模型、不计数。
  - 请求体：`description`（字符串，必填；长度 1–500 由服务层查，去掉首尾空白后计）；`rules`（列表，必填，可为空；顺序即活动规则的排序）。每条规则：`kind`（必填），`discount_mode`、`percent`、`amount_sen`、`buy_quantity`、`get_quantity`、`get_percent`、`threshold_sen`（可省略，默认 `null`），`tiers`（可省略，默认 `[]`；每档 `threshold_sen`、`amount_sen`、`percent` 三项必填，后两项可为 `null`）。**没有** `product_ids`、`category_ids`（带上即 422）。页面可把编辑区当前的规则去掉 `rule_id`、`products`、`product_ids`、`category_ids` 后提交。
  - DecorationProposal：`pack`、`palette`、`ambient`、`home_poster`、`list_strip`、`card_badge`、`detail_ribbon`、`cart_progress`（字符串，装饰 ID），`title_en`（字符串，不为空），`title_zh`、`title_ms`、`subtitle_*`、`badge_*`、`strip_*`（`*` 为 `en`、`zh`、`ms`；字符串或 `null`）。没有海报模式与替代文字，没有规则、时间、开关与金额。提案不保存；点 `[admin.ai_apply]` 后填回本页，管理员确认后以普通保存提交。
  - 错误（`detail`）：

    | 情形 | 状态码 | `detail` | A12 提示 |
    | --- | --- | --- | --- |
    | 没有有效后台会话 | 401 | `admin_session_required` | — |
    | CSRF 不通过（只有提案） | 403 | `csrf_failed` | — |
    | 请求体超过 8 KB | 413 | `request body too large` | — |
    | 请求体不是 JSON | 415 | `request body must be JSON` | — |
    | 请求体结构、类型不对 | 422 | FastAPI 的校验错误列表（`type`、`loc`、`msg`） | — |
    | `PromotionInvalid` | 422 | `{"code": "promotion_invalid", "problems": [{"code", "field", "product_id", "product_name"}…]}` | 描述长度为 `description`/`description`；规则参数为 `field` 带 `rules.<序号>.` 前缀（序号从 1 起） |
    | `AiNotConfigured` | 503 | `ai_not_configured` | `[admin.ai_not_configured]` |
    | `AiDailyLimit` | 429 | `ai_daily_limit` | `[admin.ai_daily_limit]` |
    | `AiUnavailable` | 503 | `ai_unavailable` | `[admin.ai_unavailable]` |
    | `AiResultInvalid` | 502 | `ai_result_invalid` | `[admin.ai_invalid]` |

    页面按 `detail` 是字符串还是对象（看 `code`）区分。服务层异常的说明文字不回给调用方。
- [x] 测试 `tests/test_admin_promotion_ai_api.py`（SQLite 内存库只放一个管理员；固定本接口模块与 `app.api.admin_auth` 的 `_utcnow`；本模块引用的 `propose_decoration` 换成记录调用的替身，默认调用原函数）：未配置、四项缺一、接口地址不是 https 与已配置时 status 的值；状态不要 CSRF；成功的响应形状（只有 `proposal`，字段与次序同 DecorationProposal）；传给服务层的描述、规则（转成 `RuleInput`、没有商品与分类 ID）、客户端、Settings 与当前时间；空规则与省略参数；恰好 8 KB 照常处理；四种 AI 异常与 `PromotionInvalid` 经替身的状态码与响应体；经真实服务层的描述不合格 422 与未配置 503；401、403、413、415、422 的顺序且不调用服务层；422 不回显（含 `product_ids`、`category_ids`、布尔值与浮点数当整数、坏 JSON）；两个路径不被活动 ID 的路由匹配；全部响应带 no-store。每条测试的文档字符串写明它守住的第 13 条或 UX 的哪一句。
- 偏离与落实细节：
  - 规则请求模型照 SHOP-TASK-242 的 `RuleIn`，只去掉 `product_ids` 与 `category_ids`；参数与满额档的默认值与 `RuleInput` 相同。
  - 服务层的五种拒绝一律先回滚会话再回答（描述与规则不合格时服务层尚未回滚，照 242）。服务层对 `rules` 结构抛的 `TypeError` 在这里不会发生（接口总是传 `RuleInput` 的元组），不映射。
  - `AiNotConfigured` 与 `AiUnavailable` 同为 503，靠 `detail` 区分；验收如此指定。
  - 客户端以 `AiHubClient.from_settings(request.app.state.settings)` 每次请求构造（不缓存），配置改动后重启即生效。
- 验证到什么程度：人工逐条对照验收标准、第 13 条、A12 的 AI 区与 SHOP-TASK-248 的记录（签名、异常、字段），以及 `app/api/admin_categories.py` 与 `app/api/admin_promotions.py` 的写法。没有连真实的 Billing Hub。后端测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-14 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 路由——GET /api/admin/promotion-ai/status 返回只有 configured 一个布尔字段的对象；POST /api/admin/promotion-ai/proposal 请求体为 description（字符串）与 rules（每项为 kind 与 SHOP-TASK-239 RuleInput 的参数字段，不含商品与分类 ID），要 CSRF，成功 200 返回只有 proposal 一个字段的对象（字段同 DecorationProposal）；在 app/main.py 后台段照现有顺序注册
      - 错误——没有会话 401 admin_session_required，POST 没有 CSRF 403 csrf_failed，请求体超过 8 KB 413、不是 JSON 415、结构或类型不对 422（不回显）；PromotionInvalid 为 422 promotion_invalid（带问题列表，写法同 SHOP-TASK-242）、AiNotConfigured 为 503 ai_not_configured、AiDailyLimit 为 429 ai_daily_limit、AiUnavailable 为 503 ai_unavailable、AiResultInvalid 为 502 ai_result_invalid；所有响应带 no-store
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新文件 tests/test_admin_promotion_ai_api.py（以替身代替服务层或客户端，固定本模块与 admin_auth 的 _utcnow）：未配置与已配置时 status 的值；成功的响应形状；每个错误码；401、403、413、415、422 的顺序且不调用服务层；no-store。每条测试的文档字符串写明它守住的第 13 条或 UX 的哪一句
      - docs/task-records/SHOP-TASK-251.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条，写明路由、请求与响应字段、错误码，供前台 A12 的 AI 任务引用）；改动只落在 allowed_change_paths；不改服务层、数据库、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.23 提交 b7f9355），不合并、不直推
```

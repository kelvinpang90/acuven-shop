# SHOP-TASK-242 后台促销活动的接口（列表、读取、新建、保存与状态操作）

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 1–3、12 条：后台接口只传递与校验字段，规则、核对与审计在 SHOP-TASK-239、249 与 241 的服务层（`app/services/admin_promotions.py`）；不返回个人资料。依据 UX 0.20 的 A12。节日促销后台层的第六个任务，依赖 SHOP-TASK-249 与 241。不改服务层规则、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `app/api/admin_promotions.py`，照 `app/api/admin_categories.py` 的写法：路由类为 `_NoStoreRoute`（处理函数与依赖产生的全部响应、含错误，都带 `Cache-Control: no-store`）；`AdminDep`、`CsrfDep`、`WriteBodyDep`；请求体上限 64 KB（逐块读取，规则可带 20 × 230 个商品与分类 ID，8 KB 不够）；请求模型 `extra="forbid"`、`strict=True`；422 照 `app/api/pay.py` 的 `_body_errors` 只给位置、类型与固定消息，不回显输入；路径里的活动 ID 照 `_category_id`（不带符号与前导零的十进制整数，不超过 2³¹−1），不合法 422（`loc` 为 `["path", "promotion_id"]`，`type` 为 `promotion_id_invalid`）。不写日志。
- [x] `app/main.py`：后台段在最后（`admin_product_images_router` 之后）注册 `admin_promotions_router`。
- [x] 检查顺序（每一步不通过即回答、不调用服务层）：
  1. 带请求体的写操作（新建、保存、发布、撤回、提前结束、删除）：请求体超过 64 KB 413 `request body too large`，不是 JSON 415 `request body must be JSON`，先于一切；
  2. 会话：401 `admin_session_required`；
  3. 写操作的 CSRF（`X-CSRF-Token`）：403 `csrf_failed`；
  4. 路径 ID：422；
  5. 请求体结构与 JSON 类型：422。
  复制没有请求体、不读请求体，只有 2.–4.；两个读取接口只有 2. 与（读取单个时）4.。
- [x] 供前台 A12 任务引用的接口摘要（时间一律是马来西亚本地 `YYYY-MM-DDTHH:MM` 字符串或 `null`，与服务层相同；金额为整数仙）：

  | 方法与路径 | 请求体 | 成功 |
  | --- | --- | --- |
  | `GET /api/admin/promotions` | 无 | 200 `{"promotions": [PromotionSummary…], "csrf_token"}` |
  | `POST /api/admin/promotions` | PromotionInput | 201 PromotionDetail（新草稿） |
  | `GET /api/admin/promotions/{promotion_id}` | 无 | 200 PromotionDetail 的字段加 `csrf_token` |
  | `PUT /api/admin/promotions/{promotion_id}` | PromotionInput 加 `version` | 200 PromotionDetail（都未变时版本不变） |
  | `POST /api/admin/promotions/{promotion_id}/publish` | `{"version"}` | 200 PromotionDetail |
  | `POST /api/admin/promotions/{promotion_id}/withdraw` | `{"version"}` | 200 PromotionDetail |
  | `POST /api/admin/promotions/{promotion_id}/end` | `{"version"}` | 200 PromotionDetail |
  | `POST /api/admin/promotions/{promotion_id}/copy` | 无（不读） | 201 PromotionDetail（新草稿） |
  | `DELETE /api/admin/promotions/{promotion_id}` | `{"version"}` | 204，没有响应体 |

  - PromotionInput（字段名与 SHOP-TASK-239 的输入数据类相同）：`name`、`starts_at`、`ends_at`（字符串或 `null`，必填）、`coupon_combinable`、`points_combinable`（布尔值）、`rules`（列表）、`decoration`、`copy`。
    - 规则：`kind`（必填），`discount_mode`、`percent`、`amount_sen`、`buy_quantity`、`get_quantity`、`get_percent`、`threshold_sen`（可省略，默认 `null`），`product_ids`、`category_ids`（整数列表）、`tiers`（可省略，默认 `[]`）；满额档 `threshold_sen`、`amount_sen`、`percent` 三项必填（后两项可为 `null`）。
    - 装饰：`pack`、`palette`、`ambient`、`home_poster`、`list_strip`、`card_badge`、`detail_ribbon`、`cart_progress`、`poster_mode`，都必填。
    - 文案：`title_*`、`subtitle_*`、`badge_*`、`strip_*`、`poster_alt_*`（`*` 为 `en`、`zh`、`ms`），可省略，默认 `null`。
    - 取值、长度、格式、条数与引用一律由服务层检查（不合格即 `promotion_invalid`）。
  - PromotionDetail：`id`、`version`、`state`（`draft`、`scheduled`、`running`、`ended`）、`ended_early_at`、`name`、`starts_at`、`ends_at`、`coupon_combinable`、`points_combinable`、`rules`、`decoration`、`copy`、`poster_desktop_url`、`poster_phone_url`。每条规则为规则的全部字段加 `rule_id` 与 `products`（`[{id, slug, name_en}]`，与 `product_ids` 一一对应）；`product_ids`、`category_ids` 从小到大。
  - PromotionSummary：`id`、`name`、`state`、`starts_at`、`ends_at`（实际结束）、`rule_count`、`updated_at`（不带时区的 UTC，ISO 8601）；按进行中、未开始、草稿、已结束排列。
  - 错误（`detail`）：

    | 情形 | 状态码 | `detail` |
    | --- | --- | --- |
    | 没有有效后台会话 | 401 | `admin_session_required` |
    | CSRF 不通过 | 403 | `csrf_failed` |
    | 请求体超过 64 KB | 413 | `request body too large` |
    | 请求体不是 JSON | 415 | `request body must be JSON` |
    | 路径 ID 或请求体结构、类型不对 | 422 | FastAPI 的校验错误列表（`type`、`loc`、`msg`） |
    | `PromotionInvalid` | 422 | `{"code": "promotion_invalid", "problems": [{"code", "field", "product_id", "product_name"}…]}` |
    | `PromotionOverlap` | 409 | `{"code": "promotion_overlap", "name": 对方的内部名称}` |
    | `PromotionChanged` | 409 | `promotion_changed` |
    | `PromotionNotFound` | 404 | `promotion_not_found` |
    | `PromotionStateInvalid` | 409 | `{"code": "promotion_state_invalid", "state": 当前状态}` |
    | `PromotionEnded` | 409 | `promotion_ended` |

    页面按 `detail` 是字符串还是对象（看 `code`）区分。服务层异常的说明文字不回给调用方。
- [x] 测试 `tests/test_admin_promotions_api.py`（照 `tests/test_admin_categories_api.py`：SQLite 内存库，固定本接口模块与 `app.api.admin_auth` 的 `_utcnow`；本模块引用的九个服务层函数包一层记录调用）：每个路由的成功响应形状；路由清单只断言本文件 `router` 上的九个路由并确认已注册到应用；401、403、413、415、422 的顺序且不调用服务层；路径 ID 与各层请求体的 422 不回显输入；复制不读请求体；六种服务层异常经替身逐个路由的映射与响应体，以及经真实服务层的 `promotion_invalid`（含 `scope_overlap` 的商品）、`promotion_overlap`、`promotion_state_invalid`、`promotion_changed`、`promotion_not_found`；20 条规则各带 200 件商品与 30 个分类 ID（10 位数 ID、文案写满）的紧凑 JSON 在 8 KB 与 64 KB 之间、通过请求体大小与结构校验并原样传给服务层（以替身代替服务层）；全部响应带 no-store。每条测试的文档字符串写明它守住的 UX 或设计句子。
- 偏离与落实细节：
  - JSON 字段 `copy` 与 pydantic `BaseModel.copy` 同名，请求与响应模型里的属性名为 `copy_text`、以别名 `copy` 收发（照 `app/api/admin_coupons.py` 的 `type`）；请求体里写 `copy_text` 按多出字段 422。
  - 请求模型里数据类有默认值的字段（规则的参数、范围与满额档，文案各项）可省略，默认值与数据类相同；显式的 `null` 对列表字段为 422。
  - 读取单个活动的响应带 `csrf_token`；新建、保存与状态操作的响应不带（页面沿用读取时的令牌）。
  - 服务层异常一律先回滚会话再回答（`check_promotion_input` 不通过时服务层尚未回滚，照 `app/api/admin_products.py`）。
  - 最大请求体按 10 位数 ID 与写满的文案估算约 57 KB，64 KB 够用；页面应以紧凑 JSON（`JSON.stringify` 默认）提交。
- 验证到什么程度：人工逐条对照验收标准、落实写法的错误映射与 `app/api/admin_categories.py` 的写法；SQLite 不生成 FOR UPDATE，MySQL 上的加锁不在这里验证。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

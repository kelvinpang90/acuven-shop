# SHOP-TASK-245 管理员预览草稿与未开始活动的数据接口

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 11 条：预览只经后台接口（凭管理员会话），不改公开接口，不产生订单，不碰个人资料；UX 0.20 A12「前台预览」的可预览条件。依赖 SHOP-TASK-233（`load_promotion`）、234（`quote_cart`）、239（`promotion_state`）、243（`promotion_display`）与 244（`list_products`、`get_product`）。不改服务层、公开接口、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `app/api/admin_promotion_preview.py`，路由类 `_NoStoreRoute`（全部响应含错误带 `Cache-Control: no-store`），会话照 `app/api/admin_categories.py` 用 `AdminDep`。只读，不写审计、不写日志；没有下单接口。
- [x] `app/main.py`：在后台段 `admin_promotions_router` 之后注册。
- [x] 路由（供前台 A12 预览任务引用；`{promotion_id}` 为活动 ID）：
  - `GET /api/admin/promotions/{promotion_id}/preview?lang=`：响应同 `GET /api/promotion`，`{"promotion": PromotionDisplay, "server_time": "<UTC 时间>"}`；`lang` 同该接口（`LanguageDep`，en、zh、ms，默认 en）。`promotion` 为该活动的展示数据（SHOP-TASK-243 的字段）；装饰行缺失时为 `null`（数据异常）。
  - `GET /api/admin/promotions/{promotion_id}/preview/products`：查询参数与响应同 `GET /api/catalog/products`（`lang`、`q`、`category`、`option`、`sort`、`page`、`page_size`、可重复的 `slug`、`sale`），商品带该活动的规则角标与活动价；`sale=true` 为专题页列表。
  - `GET /api/admin/promotions/{promotion_id}/preview/products/{slug}?lang=`：响应同商品详情；未发布与不存在 404 `{"detail": "product not found"}`。
  - `POST /api/admin/promotions/{promotion_id}/preview/quote?lang=`：请求头 `X-CSRF-Token`；请求体（复用 `app/api/checkout.py` 的 `QuoteRequest`）与响应同 `POST /api/checkout/quote`（游客计价）。
- [x] 可预览的条件：`load_promotion` 读出的活动为 draft 或 scheduled（`promotion_state`），开始与结束时间都有，且实际结束晚于当前时刻；草稿的开始时间已过不影响（A12 的唯一禁用条件只有缺时间与结束时间已过）。
- [x] 错误码与顺序（每一步不通过即停止）：
  1. 401 `admin_session_required`（没有有效后台会话）；
  2. 只有 POST：403 `csrf_failed`；
  3. 路径 ID 不合法 422（`loc` 为 `["path", "promotion_id"]`，写法同 `app/api/admin_promotions.py`）；
  4. GET 的查询参数 422（含 `option` 不是 `<规格名>:<规格值>` 时的 422 `{"detail": "option must be <option>:<value>"}`）；POST 的请求体：超过 8 KB 即 413 `request body too large`，不查 Content-Type，不是 JSON 或不合请求模型 422（`loc` 以 `body` 开头）；
  5. 活动不存在 404 `promotion_not_found`，不满足可预览条件 409 `promotion_preview_unavailable`；
  6. 只有计价：收货目的地不合法 422 `invalid country or state code`，运费行缺失 503 `shipping is unavailable`（写法照游客计价）。
  422 每条只给 `type`、`loc`、`msg`，不回显请求内容。预览计价不经游客计价的来源限流（`checkout_quote_source`）。
- [x] 测试 `tests/test_admin_promotion_preview_api.py`（SQLite 内存库，StaticPool，打开外键检查；第 8 条算例的商品与活动以模型行写入并提交；固定本模块、`app.api.admin_auth` 与同时调用的公开接口模块的 `_utcnow`）：草稿、已发布未开始与开始时间已过的草稿在四个路由给出展示数据、活动价与角标、sale 列表与第 8 条的游客计价（西马 800 时 `total_sen` 5900）；进行中、已结束、已提前结束、草稿缺开始或结束时间、结束时间已过（含恰为当前时刻）时 409；不存在 404；没有会话 401；POST 没有或带错 CSRF 403；路径 ID 不合法 422；查询参数与请求体的 422、413 先于读活动；运费的 422 与 503 在读活动之后；不查 Content-Type；不受游客计价限流；路由恰为四个；预览期间公开的活动、目录与计价接口（带不带预览样的查询参数）都没有活动；计价后没有订单。每条测试的文档字符串写明它守住的第 11 条或 A12 的原句。
- 偏离与落实细节：
  - 路径 ID 的校验复用 `app/api/admin_promotions.py` 的 `_promotion_id`，查询参数的 `slug` 与 `option` 解析复用 `app/api/catalog.py` 的 `_slug_filter` 与 `_option_filters`，请求体上限与模型复用 `app/api/checkout.py` 的 `MAX_BODY_BYTES` 与 `QuoteRequest`；请求体的读取另写一份（游客计价的 `_quote_request` 含来源限流）。
  - 目录接口的 `option` 解析在处理函数里，本模块在读活动之前调用它。
  - 计价请求体在 CSRF 与路径 ID 之后才读取（游客计价没有会话与 CSRF，413 先于一切）；413 因此在 401、403 与路径 422 之后。
- 验证到什么程度：人工逐条对照验收标准、DESIGN 1.23 第 8、11 条、UX 0.20 A12 与所复用的服务层函数签名。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

# SHOP-TASK-265 AI 提案接口次数用完时的响应带上全站每日上限

- [x] 设计闸门：不适用。只改一个错误响应体的形状，不碰金额与个人资料，不改数据库结构。依据 docs/UX-COPY.md 0.22 的 `admin.ai_daily_limit`（文案带 `{count}`，即今天的调用上限）与 docs/DESIGN.md 1.23「节日促销活动」第 13 条「已达上限即拒绝并说明」；Kelvin 2026-11-15 决定（未选：status 接口返回上限、改文案去掉变量）；落实写法见 HANDOFF 0.98。
- [x] `app/api/admin_promotion_ai.py`：`_rejected` 多收本应用的 `Settings`（调用处传 `request.app.state.settings`），服务层抛 `AiDailyLimit` 时 429 的 detail 由字符串改为对象，写法同同文件 `promotion_invalid` 的对象。文件头的拒绝说明同步改写。其他错误码、状态码、响应体与检查顺序不变；服务层、配置、数据库与前端未改。
- [x] 新的 429 响应体（供 SHOP-TASK-267 的请求模块引用）：

  ```json
  {"detail": {"code": "ai_daily_limit", "limit": 50}}
  ```

  `detail` 只有 `code` 与 `limit` 两个键；`code` 固定为 `ai_daily_limit`；`limit` 为整数，等于应用配置的 `ai_daily_call_limit`（环境变量 `SHOP_AI_DAILY_CALL_LIMIT`，默认 50，上例即默认值）。响应仍带 `Cache-Control: no-store`。前端以 `limit` 填 `admin.ai_daily_limit` 的 `{count}`。
- [x] `tests/test_admin_promotion_ai_api.py`：`test_service_errors_map_to_fixed_codes` 的参数里移出 `daily_limit` 一项，其余用例与断言不改；新增 `test_daily_limit_reports_the_configured_limit`，以 `ai_daily_call_limit` 为 3 与 120 各建一个应用请求一次，断言 detail 为 `{"code": "ai_daily_limit", "limit": <所配的值>}`、429、no-store、不带异常消息、服务层只调用一次；文档字符串写明它守住 `admin.ai_daily_limit` 的 `{count}`。`test_every_response_is_no_store` 里 429 一项只断言状态码与 no-store，不受影响，未改。
- 偏离：`test_service_errors_map_to_fixed_codes` 的文档字符串去掉了 AiDailyLimit 与 `admin.ai_daily_limit` 的引述（该用例已移出），改为指向新测试；断言未改。
- 交付：以 Draft PR 交付，不合并、不直推。

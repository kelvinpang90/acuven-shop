# SHOP-TASK-099 后台优惠券的列表、新建与停用接口

- [x] 提供 `GET /api/admin/coupons`、`POST /api/admin/coupons` 与 `POST /api/admin/coupons/{coupon_id}/disable`。三者均由后台会话授权并经 `_NoStoreRoute` 返回 no-store；列表返回分页（每页 30）、由 cookie 算出的 CSRF、券的代码、类型、固定金额或百分比、最低消费、马来西亚起止日期、两个使用上限、已用次数与启停状态。新建严格接收券字段并提交审计；停用为幂等，只有实际停用写审计。
- 检查顺序与错误码：新建依次为逐块读取超过 8 KB 的 413、非 JSON 的 415、后台会话 401、CSRF 403、结构 422、业务校验；业务无效为 `coupon_invalid` 的 422、重复代码为 `coupon_code_taken` 的 409。停用依次为 401、403、无符号无前导零且不超过 INT 上限的路径 ID 422、不存在的 `not_found` 404；数据库故障回滚并为 `service_unavailable` 503。422 不回显请求内容。
- 偏离与取舍：无设计偏离；复用 SHOP-TASK-098 的规则服务，不修改服务、模型或迁移。A05 页面留给 SHOP-TASK-100 至 102。
- 验证到什么程度：`tests/test_admin_coupons_api.py` 以开启外键检查的 SQLite 内存库和 TestClient 建立管理员及后台会话，覆盖无会话、列表字段与分页、马来西亚日期、新建提交与审计、业务与 CSRF 拒绝不写入、413/415/私密 422、停用幂等审计、路径与不存在错误，以及所有响应 no-store；由 PR 必需 CI 的 backend 执行。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 app/api/admin_coupons.py（app/main.py 挂上路由）提供 GET /api/admin/coupons、POST /api/admin/coupons 与 POST /api/admin/coupons/{coupon_id}/disable；路由器用 SHOP-TASK-027 的 _NoStoreRoute，这三个接口的处理函数与依赖产生的响应（含错误）都带 no-store；路径存在但方法不匹配时由框架返回的 405 不在此列；都经 SHOP-TASK-036 的 require_admin（无有效后台会话 401 admin_session_required）；不写日志
      - GET /api/admin/coupons?page=N（1 到 10000 的整数，默认 1；不合法时 422，写法同 app/api/admin_stock_resets.py）返回 {total, page, page_size（30）, coupons, csrf_token}，coupons 每项为 id、code、type、amount_off_sen（percent 时 null）、percent_off（fixed 时 null）、min_subtotal_sen、starts_on 与 ends_on（YYYY-MM-DD 的马来西亚日期）、total_use_limit、per_member_use_limit（不限时 null）、used_count 与 active（布尔），csrf_token 由 cookie 算出（同 GET /api/admin/store-design）；不写审计
      - POST /api/admin/coupons 的检查顺序为 413（请求体逐块读，超过 8 KB，先于一切）→ 415（不是 JSON）→ 401 → 403 csrf_failed（经 require_admin_csrf）→ 422（结构）→ 业务校验；请求体只有 code（1 到 64 个字符的字符串）、type（fixed 或 percent）、amount_off_sen 与 percent_off（整数或 null）、min_subtotal_sen（整数）、starts_on 与 ends_on（YYYY-MM-DD 的日期字符串）、total_use_limit 与 per_member_use_limit（整数或 null），都必填、严格类型，多出字段 422，422 每条错误只给位置、类型与固定消息、不回显请求内容（沿用 app/api/pay.py 的 _body_errors）；CouponInvalid 为 422 {"detail":"coupon_invalid"}，CouponCodeTaken 为 409 {"detail":"coupon_code_taken"}，都不写入；成功时提交，201 与新券（字段同列表项）
      - POST /api/admin/coupons/{coupon_id}/disable 的检查顺序为 401 → 403 csrf_failed → 路径 ID（只接受不带符号与前导零、不超过 2147483647 的十进制整数，否则 422，写法同 GET /api/admin/stock-resets/{reset_id}）→ 不存在 404 {"detail":"not_found"}；不读请求体；成功或已停用都是 200 与该券（字段同列表项），只有实际停用时提交审计；数据库出错时回滚，503 service_unavailable
      - tests/test_admin_coupons_api.py 用 TestClient 与 SQLite 内存库（开启外键检查，覆盖会话依赖，照 tests/test_admin_stock_resets.py 与 tests/test_admin_store_design.py 的写法）自建管理员、后台会话、券与用券记录，验证意图——无会话时三个接口都 401；列表的字段、分页、used_count 与马来西亚日期；新建成功 201 且写一条审计、另一会话读得到；coupon_invalid 与 coupon_code_taken 不写入；缺或错 CSRF 403 且不写入；非 JSON 415、超过 8 KB 413、多余字段与类型不对 422 且不回显；停用 200 且写审计、重复停用 200 不再写、不存在 404、路径 ID 不合法 422；所有响应（含错误）带 no-store；每条测试写明它守住的是设计、UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 backend 执行并全部通过（Worker 沙箱不跑 pytest）
      - docs/TODO.md 新增 SHOP-TASK-099 记录段（三个接口、字段、检查顺序与错误码、偏离、验证到什么程度，并写明 A05 页面留给 SHOP-TASK-100 至 102），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 app/services/ 下的文件、app/api/admin_auth.py、.platform/、CI、部署配置与前端；不装依赖；不加表或迁移；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.11 提交 2d13250 与 HANDOFF 0.48），不合并、不直推
```

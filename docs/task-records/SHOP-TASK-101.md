# SHOP-TASK-101 后台优惠券的请求模块 A05

- [x] 新增 `frontend/src/api/adminCoupons.ts` 与其 vitest 测试：调用后台优惠券的列表、新建与停用接口。列表按页读取并带同源 cookie、`no-store` 与 CSRF；新建的 JSON 请求体严格只有代码、类型、固定金额或百分比、最低消费、起止日期及两个使用上限这九项；停用不带请求体。响应只取分页、CSRF 与券的内部 ID、代码、类型、互斥的金额字段、最低消费、日期、两个上限、已用数与启停状态，并严格校验类型和范围。
- 请求结果：列表区分 `ok`、无会话、失败与网络中断；新建另区分 CSRF、重复代码和无效输入；停用另区分 CSRF 与不存在。调用方可传入 `AbortSignal`，中止沿用既有模块归为网络中断。测试以 fetch 替身覆盖请求地址、方法、页码、头、九字段请求体与无请求体停用，响应字段与边界、全部状态分类和中断。
- 偏离与取舍：无设计偏离；不做页面、路由、导航、后端或设计改动，不增加依赖。页面的列表、新建与停用交给 SHOP-TASK-102。
- 验证到什么程度：由 PR 必需 CI 的 `frontend` 执行 lint、类型检查、vitest、构建与镜像构建；测试意图逐条注明对应 SHOP-TASK-099 记录段或 UX A05。本任务不做浏览器验收。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 新文件 frontend/src/api/adminCoupons.ts 调用 GET /api/admin/coupons?page=N、POST /api/admin/coupons 与 POST /api/admin/coupons/{内部 ID}/disable（同源 cookie，cache 为 no-store；两个 POST 带 X-CSRF-Token 为调用方给出的 csrf_token；新建的 Content-Type 为 application/json，JSON 请求体只有 SHOP-TASK-099 列出的九个字段；停用不带请求体），写法沿用 frontend/src/api/adminStockResets.ts 与 frontend/src/api/adminStoreDesign.ts 的现有做法
      - 只取 SHOP-TASK-099 记录段列出的字段；响应体不合格算失败——total 不是非负整数，page 不是 1 到 10000 的整数，page_size 不等于 30，id 不是正整数，code 不是非空字符串，type 不是 fixed 或 percent，fixed 时 amount_off_sen 不是正整数或 percent_off 不为 null，percent 时 percent_off 不是 1 到 100 的整数或 amount_off_sen 不为 null，min_subtotal_sen 与 used_count 不是非负整数，starts_on 与 ends_on 不是 YYYY-MM-DD，两个上限不是正整数或 null，active 不是布尔值，列表的 csrf_token 不是非空字符串，字段缺少或类型不对都算不合格
      - 列表结果归为 ok、none（401）、failed 与 network；新建结果归为 ok、none（401）、csrf（403）、code_taken（409 且 detail 为 coupon_code_taken）、invalid（422）、failed（其他状态）与 network；停用结果归为 ok、none（401）、csrf（403）、not_found（404）、failed 与 network；调用方可传入 AbortSignal，中止时按现有模块的做法处理
      - 用 vitest（不加测试依赖，fetch 以替身代替）在 frontend/src/api/adminCoupons.test.ts 验证意图——请求地址、方法、页码、请求头与请求体（只有九个字段，停用不带请求体）；字段与响应体不合格算失败（含两种类型带错字段、百分比越界、日期格式不对、上限为 0、page 为 0、page_size 不是 30）；各状态码的归类（409 的 detail 不是 coupon_code_taken 时归 failed）与网络中断；每条测试写明它守住的是 SHOP-TASK-099 记录段或 UX 的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-101 记录段（请求、字段与归类、偏离、验证到什么程度，并写明页面留给 SHOP-TASK-102），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（不改页面、路由与导航）；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

# SHOP-TASK-250 节日促销海报图的后台上传与移除接口、公开提供接口

- [x] 设计闸门：DESIGN 1.23（提交 `b7f9355`）「节日促销活动」第 12 条与「计价、优惠、积分与库存」第 13、15 条：图片的上传与提供，不碰金额与个人资料。照店铺标志的接口（`app/api/admin_store_logo.py` 与 `app/api/store_logos.py`）把 SHOP-TASK-240 的 `app/services/promotion_poster.py` 接成接口。节日促销后台层的第四个任务，依赖 SHOP-TASK-240。不改服务层规则、数据库、前端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `app/api/admin_promotion_poster.py`：`PUT` 与 `DELETE /api/admin/promotions/{promotion_id}/poster/{slot}`，`slot` 为 `desktop` 或 `phone`。路由类为 `_NoStoreRoute`，处理函数与依赖产生的全部响应（含错误）都带 `Cache-Control: no-store`。检查顺序（每一步不通过即回答、不再往下）：
  1. 会话：401 `admin_session_required`；
  2. CSRF（`X-CSRF-Token`）：403 `csrf_failed`；
  3. 路径：活动 ID 照 `app/api/admin_categories.py` 的 `_category_id`（不带符号与前导零的十进制整数，不超过 2³¹−1），不合法 422（`loc` 为 `["path", "promotion_id"]`）；`slot` 不是 `desktop` 或 `phone` 422（`loc` 为 `["path", "slot"]`）；只给位置、类型与固定消息，不回显所给的值；
  4. 普通读取（不加锁）看活动：不存在 404 `promotion_not_found`，ended 409 `promotion_ended`；读完即结束只读事务。
  - `PUT` 接着：5. 存储未配置 503 `image_store_unavailable`（不读请求体）；6. `Content-Type` 不是 `image/jpeg`、`image/png`、`image/webp` 之一 415 `image_type_unsupported`；7. 声明长度超过 5,242,880 字节 413 `image_too_large`（不读请求体），未声明时逐块读取、超过即 413；8. `process_image(data, min_width=POSTER_MIN_WIDTHS[slot])` 不合格 422 `poster_invalid`；9. `replace_poster`（锁内再核一次：不存在 404 `promotion_not_found`、ended 409 `promotion_ended`），写入失败 503 `image_store_unavailable`。成功 200 `{"poster": "/api/promotion-posters/<32 位小写十六进制>.webp"}`。
  - `DELETE` 没有请求体、不读请求体、不查存储配置：直接 `remove_poster`（锁内再核一次：404 `promotion_not_found`、409 `promotion_ended`；已发布即 scheduled 或 running、且海报为上传图模式 409 `poster_required`；该位置没有图时照样成功；存储未配置时照常清空记录并提交，只由存储模块记日志、不调用 R2）。成功 200 `{"poster": null}`。
- [x] 新增 `app/api/promotion_posters.py`：`GET /api/promotion-posters/{name}`，不要求会话。文件名格式（32 位小写十六进制加 `.webp`）不对 404 `image_not_found`；存储未配置 503 `image_store_unavailable`（前两步不查库）；去掉 `.webp` 的 32 位不在 `poster_file_names(db)` 里 404 `image_not_found`，不访问 R2（草稿活动的海报图也提供，供管理员预览）；`get_image(..., prefix=POSTER_KEY_PREFIX)` 抛 `ImageNotFound` 404、抛 `ImageStoreUnavailable` 503。错误响应带 `no-store` 且不含 R2 的错误内容；成功 200，`Content-Type: image/webp`、`X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'; sandbox`、`Cache-Control: public, max-age=31536000, immutable`（照 `app/api/store_logos.py`）。
- [x] `app/main.py`：公开段在 `store_logos_router` 之后注册 `promotion_posters_router`；后台段在 `admin_store_logo_router` 之后注册 `admin_promotion_poster_router`。
- [x] 供前台 A12 任务引用的接口摘要：

  | 方法与路径 | 请求 | 成功 | 错误（`detail`） |
  | --- | --- | --- | --- |
  | `PUT /api/admin/promotions/{promotion_id}/poster/{desktop\|phone}` | 后台会话 cookie、`X-CSRF-Token`；请求体为图片文件本身，`Content-Type` 为 `image/jpeg`、`image/png` 或 `image/webp`，不超过 5,242,880 字节；桌面图宽至少 1,200、手机图宽至少 600 | 200 `{"poster": "/api/promotion-posters/<文件名>.webp"}` | 401 `admin_session_required`；403 `csrf_failed`；422（路径，FastAPI 的校验错误列表）；404 `promotion_not_found`；409 `promotion_ended`；503 `image_store_unavailable`；415 `image_type_unsupported`；413 `image_too_large`；422 `poster_invalid` |
  | `DELETE /api/admin/promotions/{promotion_id}/poster/{desktop\|phone}` | 后台会话 cookie、`X-CSRF-Token`；没有请求体 | 200 `{"poster": null}` | 401 `admin_session_required`；403 `csrf_failed`；422（路径）；404 `promotion_not_found`；409 `promotion_ended`；409 `poster_required` |
  | `GET /api/promotion-posters/{文件名}.webp` | 不要求会话 | 200 展示图字节（WebP），长期缓存 | 404 `image_not_found`；503 `image_store_unavailable` |

  上传与移除立即生效，不随活动的保存提交，不改活动的 `version`；页面以响应里的 `poster` 更新该位置的图。
- [x] 测试 `tests/test_promotion_poster_api.py`（照 `tests/test_admin_store_logo_api.py` 与 `tests/test_store_logos_api.py`：SQLite 内存库，固定本接口模块与 `app.api.admin_auth` 的 `_utcnow`，`image_store` 的写入、读取与删除以替身代替，读请求体的依赖包一层计数）：草稿、未开始与进行中的上传；手机图宽 600；宽 1,199 的桌面图、宽 599 的手机图与坏图 422；替换后删除旧对象；写入失败 503；401、403 先于一切；路径 422 先于读活动；不存在与 ended 先于存储与请求体的检查（不读请求体）；存储未配置 503、415、声明与未声明的 413；移除、没有图的移除、已发布上传图模式移除 409、上传图模式草稿可移除、存储未配置时移除（只记日志、不创建 R2 client）；全部后台响应带 no-store；公开提供被引用（草稿、未开始、进行中）与未被引用（从未引用、被替换、被移除）的文件名、格式不对、存储未配置 503、存储错误；两个路由的注册。每条测试的文档字符串写明它守住的设计句子或本任务的落实写法。
- 偏离与落实细节：
  - 「普通读取」只查活动行的状态与三个时间（不读规则与装饰），按 `admin_promotions.promotion_state` 算状态；读完即回滚，读请求体与处理图片期间不占着事务。服务层锁内再核一次，所以读取与锁定之间活动被删除或结束时仍回答 404 或 409。
  - 路径的两个 422 都在 CSRF 之后、读活动之前，先校验活动 ID 再校验 `slot`。
  - 反向代理为海报上传路径放行至少 5 MB 请求体的上线依赖已记在 `docs/HANDOFF.md`（「节日促销海报图上线前……」一条），本任务不改 HANDOFF。
- 验证到什么程度：人工逐条对照验收标准、落实写法的检查顺序与 `app/api/admin_store_logo.py`、`app/api/store_logos.py` 的写法；存储以替身代替，不访问 R2；SQLite 不生成 FOR UPDATE，MySQL 上的加锁没有在这里验证。测试、ruff check 与 ruff format 由 Worker 与 PR 的必需 CI 检查 `backend` 执行，结果以它们的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明 DESIGN 1.23 提交 `b7f9355`；不合并、不直推。

# SHOP-TASK-213 店铺标志图的提供接口，公开装修读取加上标志

- [x] 依据：`docs/DESIGN.md` 1.20（提交 `a870f78`）「计价、优惠、积分与库存」第 15 条「提供」一段与「读取与显示」一段的公开读取部分；`docs/HANDOFF.md` 0.84 的落实写法（文件名格式、错误 detail、no-store 与成功时的响应头照 `app/api/product_images.py`；文件名不是单例行当前记录的标志即 404、不访问 R2；成功的响应长期缓存，所以不用 no-store 路由类，错误响应自行加 no-store；存储经 `image_store` 模块属性调用；处理函数为同步函数）。设计闸门：DESIGN 1.20（提交 `a870f78`）第 15 条与 HANDOFF 0.84。本任务只做提供接口与公开读取；不做前端，不改服务层、模型与设计。页面为 SHOP-TASK-214 至 216。
- [x] 新增 `app/api/store_logos.py`，在 `app/main.py` 注册：`GET /api/store-logos/{name}`（`name` 取整段文件名、含扩展名），不要求会话，检查依次为，前一项不通过即回答、不再往下：
  - `name` 不恰好是 32 位小写十六进制加 `.webp`（含大写字母、长度不对、其他扩展名与不带扩展名）即 404 `image_not_found`；
  - `image_store_configured` 为 False 即 503 `image_store_unavailable`；
  - （以上两项在会话依赖之前完成，不查库）去掉 `.webp` 的 32 位与 SHOP-TASK-211 的 `current_logo_file_name` 不相同（含没有标志、已被替换或移除的）即 404，不访问 R2；
  - 结束只读事务后以 `image_store.get_image(settings, file_name, prefix=image_store.LOGO_KEY_PREFIX)` 读取，抛 `ImageNotFound` 即 404，抛 `ImageStoreUnavailable` 即 503。
  - 错误响应都带 `Cache-Control: no-store`，不含 R2 的错误内容。成功 200，响应体为 `get_image` 返回的字节，`Content-Type: image/webp`、`X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'; sandbox`、`Cache-Control: public, max-age=31536000, immutable`。处理函数是同步函数，FastAPI 在线程池里执行；不写日志（存储模块自己的日志除外）。
- [x] `app/api/store_design.py`：`GET /api/store-design` 的响应加 `logo`（取 `read_store_design` 已给出的 `StoreDesign.logo`），其余字段、no-store 与只发 SELECT 不变。模块说明里「不含标志图」的句子改为引用 DESIGN 1.20 第 15 条。
- [x] 公开读取的响应形状（供 SHOP-TASK-216）：`GET /api/store-design` 为 `{"theme", "accent", "home_blocks", "featured_slugs", "logo"}`（字段按此顺序），`logo` 有标志时为 `"/api/store-logos/<32 位小写十六进制>.webp"`，没有时为 `null`；响应仍带 `Cache-Control: no-store`。标志图本身经该路径 `GET` 取得，不要求会话；失败为 `{"detail": "image_not_found"}`（404）或 `{"detail": "image_store_unavailable"}`（503），页面在图片加载失败时退回文字品牌。
- [x] 测试：新增 `tests/test_store_logos_api.py`，夹具照 `tests/test_product_images_api.py`（SQLite 内存库、StaticPool、https 的 base_url，不登录；装修设置单例行按迁移默认值写入；标志经 `replace_logo` 设好、经 `remove_logo` 移除）；`app.services.image_store` 的 `put_image`、`get_image` 与 `delete_image` 以 monkeypatch 替换并记录调用与 prefix；接口发出的 SQL 由引擎事件记录。每条测试的文档字符串写明它守住的第 15 条原句。覆盖：设好标志后公开读取的 `logo` 路径取到 200、字节与四个响应头，读取前缀为 `store-logos/`；没有标志时公开读取的 `logo` 为 null、带 no-store、只发 SELECT；替换与移除后公开读取随之改变；大写、31 位、`.png` 与不带扩展名 404 且不查库、不调用 `get_image`；未配置 503 且不查库；未配置时格式不对仍 404；没有标志、另一个文件名、替换后的旧文件名、移除后的原文件名都 404 且不调用 `get_image`；`ImageNotFound` 404、`ImageStoreUnavailable` 503 且不含异常文本；所有错误带 no-store、成功不带、应用不记日志；该前缀下只有一个 GET。
- [x] `tests/test_store_design.py` 与 `tests/test_admin_store_design.py` 只为公开读取新增的 `logo` 字段改现有断言：公开读取的期望响应加 `"logo": None`（字段顺序在 `featured_slugs` 之后），字段列表断言加 `logo`，相应测试的文档字符串改引第 15 条。
- 偏离与取舍：
  - `_IMAGE_NAME`、`IMAGE_HEADERS`、`_not_found` 与 `_store_unavailable` 从 `app/api/product_images.py` 导入复用（不改它），所以文件名格式、错误 detail 与 no-store、成功的响应头与商品图片完全一致；查库之前的两项检查照它的写法放在先于会话依赖声明的 `_file_name` 依赖里。
  - 与当前标志比较用 `current_logo_file_name`（只发 SELECT、关闭 autoflush），比较之后 `session.rollback()` 结束只读事务，再读 R2。
  - 路由用独立前缀 `/api/store-logos`、不用 `_NoStoreRoute`（成功响应要长期缓存）；路径存在但方法不匹配的 405 由框架回答，不带 no-store，与商品图片相同。
- 验证范围：上面列出的是本次新增与修改的测试代码，存储函数全部替换、不访问 R2；结果以 PR 的必需 CI 检查 backend 为准。

## 登记时的验收标准（原文）

2026-11-06 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 提供，判断依次为（前一项不通过即回答、不再往下）——路径最后一段 name 不恰好是 32 位小写十六进制加 .webp（含大写字母、长度不对、其他扩展名，都由处理函数回答）即 404 image_not_found；image_store_configured 为 False 即 503 image_store_unavailable；以去掉 .webp 的 32 位与 SHOP-TASK-211 的 current_logo_file_name 比较，不相同（含没有标志）即 404，不访问 R2；结束只读事务后以 image_store.get_image（prefix 为 LOGO_KEY_PREFIX）读取，抛 ImageNotFound 即 404，抛 ImageStoreUnavailable 即 503；错误响应都带 Cache-Control no-store，不含 R2 的错误内容
      - 成功——200，响应体为 get_image 返回的字节，Content-Type 为 image/webp，X-Content-Type-Options 为 nosniff，Content-Security-Policy 为 default-src 'none'; sandbox，Cache-Control 为 public, max-age=31536000, immutable；不写日志（存储模块自己的日志除外）；在 app/main.py 注册路由
      - 公开读取——GET /api/store-design 的响应加 logo（有标志时为提供路径，没有时为 null），其余字段、no-store 与只发 SELECT 不变；模块说明里「不含标志图」的句子改为引用 DESIGN 1.20 第 15 条
      - 测试验证意图（由 PR 的必需 CI 检查 backend 执行并全部通过，Worker 沙箱不跑后端测试）——新增 tests/test_store_logos_api.py（夹具照 tests/test_product_images_api.py，不登录；用 monkeypatch 替换 app.services.image_store 的 put_image、get_image 与 delete_image 并记录调用与 prefix，测试不触及真实的 R2 调用），每条测试的文档字符串写明它守住的 DESIGN 1.20 第 15 条原句——经 SHOP-TASK-211 的 replace_logo 设好标志后，GET 公开读取得到的 logo 路径能取到 200、字节与四个响应头，读取用的前缀为 store-logos/；大写、31 位、.png 的文件名 404 且不查库不调用 get_image；未配置 503；替换之后旧文件名 404 且不调用 get_image；移除之后 404；ImageNotFound 404、ImageStoreUnavailable 503；所有错误响应带 no-store；没有标志时公开读取的 logo 为 null。tests/test_store_design.py 与 tests/test_admin_store_design.py 只为公开读取新增的 logo 字段改现有断言
      - docs/task-records/SHOP-TASK-213.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；写明公开读取的响应形状，供 SHOP-TASK-216 使用；偏离取舍）
      - 改动只落在 allowed_change_paths；不改服务层、模型与迁移、app/api/product_images.py、前端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明 DESIGN 1.20 提交 a870f78 与 HANDOFF 0.84），不合并、不直推
```

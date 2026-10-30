# SHOP-TASK-181 后台分类的读取、新建与保存接口

- [x] 依据：`docs/DESIGN.md` 1.18（提交 `5ba9796`）「计价、优惠、积分与库存」第 14 条（后台维护分类）；`docs/UX.md` 0.17 的 A10（列表与各分类的前台可见商品件数、新建、逐个保存、取值不合格与分类已被改动两种结果）；`docs/HANDOFF.md` 0.76 的落实写法。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 14 条与 HANDOFF 0.76。本任务只接 SHOP-TASK-180 的服务层，不改服务层、模型与迁移、现有测试、前端与设计；页面 A10 另行登记。
- [x] 接口（新增 `app/api/admin_categories.py`，路由类为 SHOP-TASK-027 的 `_NoStoreRoute`，`app/main.py` 注册）：
  - `GET /api/admin/categories`：`require_admin`（401 `admin_session_required`）→ `categories`（`load_admin_categories` 的全部项，字段依次为 `id`、`slug`、`name_en`、`name_zh`、`name_ms`、`is_active`、`version`、`visible_products`，名称为空时为 null）与由 cookie 算出的 `csrf_token`。不写审计。
  - `POST /api/admin/categories`：请求体 8 KB 上限 413 → 不是 JSON 415（`_write_body`，照 `admin_site_settings.py` 的 `_save_body`）→ `require_admin`（401）→ X-CSRF-Token（403 `csrf_failed`）→ 请求体模型 `CategoryIn`（`name_en`、`name_zh`、`name_ms` 为字符串，`is_active` 为布尔值，四项必填，`extra="forbid"`、`strict=True`，错误经 `_body_errors` 不回显）→ `create_category`。成功 201 与新分类。
  - `PUT /api/admin/categories/{category_id}`：413 → 415 → 401 → 403（先声明 `CsrfDep`，照 `admin_coupons.py`）→ 路径 ID（`_category_id`，照 `admin_orders.py` 的 `_order_id`：不带符号与前导零、至多 10 位、不超过 2³¹−1，不合法 422 `category_id_invalid`，固定消息）→ 请求体模型 `CategorySaveIn`（同新建，另有必填的严格整数 `version`）→ `save_category`。成功或未变都 200 与保存后的分类。
  - `CategoryInvalid` 回 422 `{"detail": "category_invalid"}`，`CategoryChanged`（含分类不存在）回 409 `{"detail": "category_changed"}`；异常里的英文说明不回给调用方。管理员 ID 在调用服务层之前取出（服务层先回滚，ORM 对象随之过期）。不写日志。
- [x] 测试：新增 `tests/test_admin_categories_api.py`（写法照 `tests/test_admin_shipping_rates_api.py`，种子数据同 `tests/test_admin_categories.py`；每条测试的文档字符串写明它守住的第 14 条、A10 或本任务验收的约定）。覆盖三个接口没有会话 401；新建与保存 CSRF 缺少、为空、不一致 403，且先于请求体与路径 ID 的 422；超过 8 KB 413 先于 415、登录与路径检查；不是 JSON 415；恰好 8 KB 照常处理；多出字段、缺字段、null、数字名称、字符串或整数的 `is_active`、缺 `version`、布尔值、字符串、浮点数与 null 的 `version` 422 且不回显；路径 ID 为 0、负数、非数字、小数、前导零、`+1`、超出 INT 上限与超长 422，且先于请求体；读取的字段顺序、停用分类与 `visible_products`；新建 201 且一条审计、缺英文名称、名称超长、英文名称重复（大小写与首尾空白不同）422、第 31 个 422；保存成功 200 与逐项审计、未变 200 不写审计、启用但没有英文名称 422、停用且英文名称为空 200、英文名称重复 422、版本号不符与分类不存在 409、读取后被另一处改过 409、取值不合格与版本过期同在时先 422；所有响应带 no-store、应用不记日志；该路径下只有这三个路由。
- 偏离与取舍：
  - 请求模型不限制名称长度：长度、首尾空白与英文名称必填都交给服务层，不合格时是 `category_invalid` 而不是请求体 422，与 A10 的「取值不合格」一致。
  - 新建与保存时服务层未捕获的数据库错误不另转成 503（同 `admin_site_settings.py`，验收未列出）。
- 验证范围：上面列出的是本次新增的测试代码；后端的 lint、格式检查与测试由 PR 的必需 CI 检查 `backend` 执行（Worker 沙箱不跑后端测试）。

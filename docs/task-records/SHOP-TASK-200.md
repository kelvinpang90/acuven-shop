# SHOP-TASK-200 后台商品的请求模块 A04（列表、单件读取、新建与保存）

- [x] 新增 `frontend/src/api/adminProducts.ts` 与 `frontend/src/api/adminProducts.test.ts`：为 A04 页面调用 SHOP-TASK-186 的 `GET /api/admin/products`、`GET /api/admin/products/{product_id}`、`POST /api/admin/products` 与 `PUT /api/admin/products/{product_id}`，供 SHOP-TASK-202 至 206 使用。写法照 `frontend/src/api/adminShippingRates.ts`（SHOP-TASK-174）与 `frontend/src/api/adminCategories.ts`（SHOP-TASK-198），`isRecord`、`isFilled`、`readRows`、`readBody`、`detailIs` 等内部函数在本文件另写；CSRF 请求头名取 `frontend/src/api/pay.ts` 的 `CSRF_HEADER`。改动只在这两个文件与本文件；不做页面，不改路由与导航、后端、设计、`docs/TODO.md`、`.platform/`、CI 与部署配置，未装依赖。设计闸门：DESIGN 1.18（提交 `5ba9796`）第 11 条与 HANDOFF 0.82——价格以整仙收发，模块不换算金额；取值规则、上架条件、并发核对与审计在 SHOP-TASK-183 至 186 的服务端；请求模块只传递与校验字段，不碰个人资料。
- 导出（供 A04 页面的任务使用）：
  - `ADMIN_PRODUCTS_URL = "/api/admin/products"`。
  - 响应类型（与 `app/api/admin_products.py` 的输出模型一致，空文案为 null）：
    - `interface AdminProductListItem { id: number; slug: string; name_en: string | null; name_zh: string | null; name_ms: string | null; is_active: boolean; category_id: number; category_name_en: string | null; category_name_zh: string | null; category_name_ms: string | null }`（`ProductListItemOut`）。
    - `interface AdminOptionValue { id: number; code: string; name_en: string | null; name_zh: string | null; name_ms: string | null }`（`ValueOut`）。
    - `interface AdminOption { id: number; code: string; name_en: string | null; name_zh: string | null; name_ms: string | null; values: AdminOptionValue[] }`（`OptionOut`）。
    - `interface AdminVariant { id: number; sku: string; price_sen: number; daily_initial_stock: number; available_stock: number; is_active: boolean; option_value_ids: number[] }`（`VariantOut`，`option_value_ids` 按规格名的顺序）。
    - `interface AdminProductImage { id: number; storage_ref: string }`（`ImageOut`）。
    - `interface AdminProduct { id: number; slug: string; version: number; category_id: number; name_en: string | null; name_zh: string | null; name_ms: string | null; description_en: string | null; description_zh: string | null; description_ms: string | null; is_active: boolean; max_per_order: number; options: AdminOption[]; variants: AdminVariant[]; images: AdminProductImage[] }`（`ProductOut`）。
  - 请求类型（与 `ProductIn`、`ProductSaveIn` 一致；HANDOFF 0.82 第（1）点的两种形状写成联合类型）：
    - `interface Names { name_en: string; name_zh: string; name_ms: string }`。
    - 规格值：`interface ExistingValue extends Names { id: number }`、`interface NewValue extends Names { temp_id: string }`。
    - 新增 SKU 所选的值：`interface ExistingValueRef { id: number }`、`interface NewValueRef { temp_id: string }`、`type ValueRef = ExistingValueRef | NewValueRef`。
    - 规格名：`interface NewOption extends Names { values: NewValue[] }`（新建）、`interface SavedOption extends Names { id: number; values: (ExistingValue | NewValue)[] }`（保存）。
    - SKU：`interface ExistingVariant { id: number; price_sen: number; daily_initial_stock: number; is_active: boolean }`、`interface NewVariant { temp_id: string; option_values: ValueRef[]; price_sen: number; daily_initial_stock: number; is_active: boolean }`、`interface CreateVariant { temp_id: string; option_values: NewValueRef[]; price_sen: number; daily_initial_stock: number; is_active: boolean }`（新建，所选的值都是同一次新增的）。
    - `interface ProductFields { category_id: number; name_en: string; name_zh: string; name_ms: string; description_en: string; description_zh: string; description_ms: string; is_active: boolean; max_per_order: number }`。
    - `interface ProductCreate extends ProductFields { options: NewOption[]; variants: CreateVariant[] }`。
    - `interface ProductSave extends ProductFields { version: number; options: SavedOption[]; variants: (ExistingVariant | NewVariant)[] }`。
  - 结果类型：
    - `type ProductsRead = { kind: "ok"; products: AdminProductListItem[]; csrf_token: string } | { kind: "none" } | { kind: "failed" } | { kind: "network" }`。
    - `type ProductRead = { kind: "ok"; product: AdminProduct } | { kind: "none" } | { kind: "failed" } | { kind: "network" }`。
    - `type ProductWrite = { kind: "ok"; product: AdminProduct } | { kind: "none" } | { kind: "csrf" } | { kind: "invalid" } | { kind: "changed" } | { kind: "failed" } | { kind: "network" }`。
  - 函数：
    - `readAdminProducts(signal?: AbortSignal): Promise<ProductsRead>`。
    - `readAdminProduct(productId: number, signal?: AbortSignal): Promise<ProductRead>`。
    - `createAdminProduct(input: ProductCreate, csrfToken: string, signal?: AbortSignal): Promise<ProductWrite>`。
    - `saveAdminProduct(productId: number, input: ProductSave, csrfToken: string, signal?: AbortSignal): Promise<ProductWrite>`。
- 请求：四个请求都不带查询参数，凭同源 cookie，`cache: "no-store"`，`Accept: application/json`，调用方可传入 `AbortSignal`。
  - 列表：GET `/api/admin/products`；单件：GET `/api/admin/products/{商品 ID 的十进制}`（`String(productId)`）。两者没有请求体与 CSRF 头。
  - 新建：POST `/api/admin/products`；保存：PUT `/api/admin/products/{商品 ID 的十进制}`。两者 `Content-Type: application/json`，`X-CSRF-Token` 为调用方给出的令牌。
  - 新建的 JSON 请求体依次只有 `category_id`、`name_en`、`name_zh`、`name_ms`、`description_en`、`description_zh`、`description_ms`、`is_active`、`max_per_order`、`options`、`variants`；规格名只有三语名称与 `values`，每个值只有 `temp_id` 与三语名称；SKU 只有 `temp_id`、`option_values`（每项只有 `temp_id`）、`price_sen`、`daily_initial_stock` 与 `is_active`。
  - 保存的请求体为新建的各项另加 `version`；规格名另有 `id`；规格值、SKU 与新增 SKU 所选的值按形状写出：对象上有 `id` 键的为已有形状（值只写 `id` 与三语名称；SKU 只写 `id`、`price_sen`、`daily_initial_stock`、`is_active`；所选的值只写 `id`），否则为新增形状（值只写 `temp_id` 与三语名称；SKU 只写 `temp_id`、`option_values`、`price_sen`、`daily_initial_stock`、`is_active`；所选的值只写 `temp_id`）。
  - 请求体按上述字段重新组装，调用方对象上多出的属性不进请求体，不写出值为 null 的键；文案原样提交、不去空白；价格为调用方给的整仙，不换算。
- 字段与合格条件：
  - 列表 200 的响应体为对象，`products` 为数组、`csrf_token` 为非空字符串；每项 `id` 与 `category_id` 为正整数、`slug` 为非空字符串、商品与分类的三语名称各为字符串或 null、`is_active` 为布尔值。按接口给的顺序，不重排。
  - 单件 200、新建 201 与保存 200 的响应体按同一条件读取一件商品：`id`、`category_id`、`version` 为正整数，`slug` 为非空字符串，三语名称与描述为字符串或 null，`is_active` 为布尔值，`max_per_order` 为整数；`options` 每项 `id` 为正整数、`code` 为非空字符串、三语名称为字符串或 null、`values` 为数组（每个值同样三项条件）；`variants` 每项 `id` 为正整数、`sku` 为非空字符串、`price_sen`、`daily_initial_stock`、`available_stock` 为不小于 0 的整数、`is_active` 为布尔值、`option_value_ids` 为正整数数组，个数等于规格名个数且第 i 个是第 i 个规格名下某个值的 `id`；`images` 每项 `id` 为正整数、`storage_ref` 为非空字符串。
  - 只取这些字段，各层多出的字段不带进结果；任一项不合格，整个读取或写操作为 `failed`。
- 归类：列表与单件为 `ok`、`none`（401）、`failed`（其他状态，含单件的 404 `product_not_found` 与路径 ID 的 422，以及 200 但响应体不合格或不是 JSON）与 `network`（fetch 抛错，含调用方中止）。新建与保存为 `ok`（新建 201、保存 200，且商品合格）、`none`（401）、`csrf`（403）、`invalid`（422 且 `detail` 恰为 `product_invalid`）、`changed`（409 且 `detail` 恰为 `product_changed`）、`failed`（其他 422 含路径 ID 与请求体的错误列表、其他 409、413、415、404 与其他状态、成功状态但响应体不合格；新建回 200、保存回 201 也归 `failed`）与 `network`。错误体不带进结果。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 已有与新增两种形状以对象上有没有 `id` 键区分（照 `adminShippingRates.ts` 以 `"delete" in row`、`"country_code" in row` 区分）；同时带 `id` 与 `temp_id` 的对象按已有写出、只写 `id`。
  - `max_per_order` 按验收只要求为整数，不在读取时检查 1–1000；价格、库存与数组个数的上限同样不在请求模块里检查，取值以服务端为准，页面的提交前检查交给 A04 页面的任务（HANDOFF 0.82 第（3）点）。
  - 商品 ID 的路径段只做 `String(productId)`，不在请求模块里检查正整数；ID 来自读取结果（已校验为正整数），不合法时服务端回 422 `product_id_invalid`，归 `failed`。
  - 中止归为 `network` 的四条测试守住的是本任务验收「网络中断与调用方中止为 network」：SHOP-TASK-186 记录、DESIGN 1.18 第 11 条与 UX 0.17 A04 都没有关于中止的原句，测试注释照实写明，并引 UX 0.17 A04「没有未保存的修改时直接切换」为最接近的一句（两条读取的注释另引 HANDOFF 0.82 第（2）点「返回会中止未完成的读取」）。
- 留给之后：图片与当日库存调整的请求模块交给 SHOP-TASK-201；A04 页面交给 SHOP-TASK-202 至 208。
- 验证到什么程度：人工逐条对照验收标准、`app/api/admin_products.py`、SHOP-TASK-186 记录、DESIGN 1.18 第 11 条、UX 0.17 A04 与 HANDOFF 0.82 自查。`frontend/src/api/adminProducts.test.ts`（vitest，fetch 以参数带类型的 `vi.fn` 替身代替，从 `mock.calls` 取值时用 `String(…)` 收窄）覆盖：
  - 四个请求的地址（单件与保存含 ID 7 与 2147483647 的十进制路径段）、方法、同源 cookie、no-store、Accept、Content-Type、CSRF 头（两个读取没有）与 signal。
  - 新建请求体恰为十一项、保存恰为十二项（`Object.keys` 与整体 `toStrictEqual`），文案原样（含首尾空白、换行与空串）；新建的规格名、规格值、SKU 与所选的值只有规定的字段；保存的已有值只有 `id` 与三语名称（另带的 `temp_id` 不写出）、新增值只有 `temp_id` 与三语名称、已有 SKU 不带 `option_values`（调用方对象上带着也不写出）、新增 SKU 的所选值分别只有 `id` 或只有 `temp_id`；读取结果上的 `slug`、`code`、`sku`、`available_stock`、`option_value_ids`、`images` 等不进请求体；请求体文本里没有 `null`；没有规格名的新建商品。
  - 列表：按接口顺序带回商品与令牌、空列表、顶层与每项多出的字段不带进结果；每项的 `id`、`category_id`、`slug`、六个名称与 `is_active` 缺少或类型、取值不对；`products` 缺少、不是数组、为 null 或含 null，`csrf_token` 缺少、为空、数字或 null，整体不是对象或不是 JSON。
  - 单件、新建与保存共用的不合格商品：商品各字段缺少、0、负数、小数、字符串、null 或类型不对；`options`、`variants`、`images` 缺少、为 null、为对象或含 null；规格名与规格值的 `id`、`code`、名称与 `values`；SKU 的 `id`、`sku`、`price_sen`（负数、小数、字符串、缺少）、`daily_initial_stock`、`available_stock`、`is_active`；`option_value_ids` 缺少、为 null、含 0 或字符串、少一个、多一个、顺序对调、引用另一个规格名的值、未知的值、没有规格名的商品却带值；图片的 `id` 与 `storage_ref`。另有合格的整件商品原样带回、没有规格名与图片的商品合格、各层多出的字段不带进结果，以及成功状态的响应体为数组、列表、null 或不是 JSON。
  - 各状态码与 detail 的归类：单件的 404 `product_not_found` 与路径 ID 422 为 `failed`；新建与保存的 422 与 409 detail 对调、大小写不同、错误列表、空对象、不是 JSON 时归 `failed`，413（含不是 JSON 的 413）、415、404 与其他状态归 `failed`；新建回 200 或 204、保存回 201 或 204 归 `failed`；网络中断与中止为 `network`。
  - 除四条中止测试（见偏离一节）外，每条测试的注释写明它守住的 SHOP-TASK-186 记录（`app/api/admin_products.py`）、DESIGN 1.18 第 11 条或 UX 0.17 A04 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建；Worker 沙箱不跑前端检查）。本任务不做浏览器验收。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-11-05 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 写法照 frontend/src/api/adminShippingRates.ts（同源 cookie、cache no-store、Accept 与 Content-Type、写操作带调用方传入的 CSRF 令牌于 pay.ts 的 CSRF_HEADER、接收 AbortSignal、只取接口记录列出的字段且多出的字段不带进结果、响应体不合格算失败、页面只按状态码与 detail 区分，不显示错误体）；路径段为 ID 的十进制
      - 列表——GET /api/admin/products：200 且响应体为 products 数组与非空字符串 csrf_token 时为 ok（按接口给的顺序）；每项要求 id 与 category_id 为正整数、slug 为非空字符串、name_en、name_zh、name_ms、category_name_en、category_name_zh、category_name_ms 各为字符串或 null、is_active 为布尔值，任一项不合格整个读取算失败；401 为 none；其他状态与不合格为 failed；网络中断与调用方中止为 network
      - 单件——GET /api/admin/products/{product_id}：200 且响应体为一件合格的商品时为 ok 并带回它；合格的商品要求 id、category_id 与 version 为正整数，slug 为非空字符串，name_en、name_zh、name_ms、description_en、description_zh、description_ms 各为字符串或 null，is_active 为布尔值，max_per_order 为整数；options 为数组，每项 id 为正整数、code 为非空字符串、三语名称各为字符串或 null、values 为数组（每个值 id 为正整数、code 为非空字符串、三语名称各为字符串或 null）；variants 为数组，每项 id 为正整数、sku 为非空字符串、price_sen、daily_initial_stock 与 available_stock 为不小于 0 的整数、is_active 为布尔值、option_value_ids 为正整数数组，个数等于规格名个数且第 i 个是第 i 个规格名下某个值的 id；images 为数组，每项 id 为正整数、storage_ref 为非空字符串；任一项不合格算失败；401 为 none；其他状态（含 404 product_not_found）与不合格为 failed；网络中断与中止为 network
      - 新建——POST /api/admin/products，JSON 请求体只有 category_id、name_en、name_zh、name_ms、description_en、description_zh、description_ms（字符串原样提交，不去空白）、is_active、max_per_order、options 与 variants；options 每项只有三语名称与 values，每个值只有 temp_id 与三语名称；variants 每项只有 temp_id、option_values（每项只有 temp_id）、price_sen、daily_initial_stock 与 is_active；201 且响应体为一件合格的商品时为 ok 并带回它；422 且 detail 为 product_invalid 为 invalid，409 且 detail 为 product_changed 为 changed；401 为 none，403 为 csrf；其他 422、409 与其他状态（含 413）、响应体不合格为 failed；网络中断与中止为 network
      - 保存——PUT /api/admin/products/{product_id}，请求体为新建的各项另加 version（整数）；options 每项另有 id；规格值为已有（只有 id 与三语名称）或新增（只有 temp_id 与三语名称）两种；variants 为已有（只有 id、price_sen、daily_initial_stock 与 is_active，不带 option_values）或新增（只有 temp_id、option_values、price_sen、daily_initial_stock 与 is_active）两种，新增 SKU 的 option_values 每项为只有 id（已有的值）或只有 temp_id（同一次新增的值）；两种形状写成 TypeScript 联合类型，序列化时只写出该形状的字段、不出现值为 undefined 或 null 的键；200 且响应体为一件合格的商品时为 ok 并带回它；其余归类同新建
      - 测试验证意图（不加测试依赖，fetch 以替身代替）——frontend/src/api/adminProducts.test.ts：四个请求的方法、地址、请求头、no-store 与请求体只有规定的字段（含已有与新增两种形状各自的字段）；列表与单件的合格判定（缺字段、类型不对、负数、非整数、option_value_ids 个数不对或引用别的规格名的值、多出字段不带进结果、空 csrf_token）；新建与保存的各种状态与 detail 的归类；中止为 network；每条测试写明它守住的是接口记录（SHOP-TASK-186 的 app/api/admin_products.py）、DESIGN 1.18 第 11 条或 UX 0.17 A04 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-200.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；导出的类型与函数签名，供 A04 页面的任务使用；验证到什么程度，只写实际存在的测试）；改动只落在 allowed_change_paths；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖；不改后端；以 Draft PR 交付（设计闸门：DESIGN 1.18 第 11 条，同上），不合并、不直推
```

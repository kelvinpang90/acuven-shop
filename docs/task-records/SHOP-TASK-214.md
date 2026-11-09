# SHOP-TASK-214 后台店铺标志图的请求模块 A08

- [x] 新增 `frontend/src/api/adminStoreLogo.ts` 与 `frontend/src/api/adminStoreLogo.test.ts`：为 A08 页面调用 SHOP-TASK-212 的 `PUT /api/admin/store-design/logo`（上传）与 `DELETE /api/admin/store-design/logo`（移除）。
- [x] `frontend/src/storeDesign.ts` 新增导出的 `isStoreLogoPath(value)`（提供路径的格式判断，供本任务与页头页脚 SHOP-TASK-216 共用），`frontend/src/storeDesign.test.ts` 补上它的测试；该文件其余内容未改。
- [x] `frontend/src/api/adminStoreDesign.ts` 的 `AdminStoreDesignDetail` 加 `logo: string | null`，后台读取取出并校验 `logo`；保存的请求与结果（`StoreDesignInput`、`AdminStoreDesign`、`StoreDesignSave`）不变。`frontend/src/api/adminStoreDesign.test.ts` 补上 logo 的测试。
- [x] `frontend/src/pages/AdminStoreDesignPage.test.tsx` 只为新增的 `logo` 改两处：常量 `DETAIL` 补 `logo: null`（`PICKING` 与各处 `fresh` 由它展开）；测试 `updates the form from the saved design` 期望的 `base` 补 `logo: DETAIL.logo`。该文件其他测试与断言未动，`frontend/src/pages/AdminStoreDesignPage.tsx` 未改。
- 写法照 `frontend/src/api/adminProductImages.ts`（SHOP-TASK-201）：`isRecord`、`readBody`、`detailIs`、`send` 在 `adminStoreLogo.ts` 里另写；CSRF 请求头名取 `frontend/src/api/pay.ts` 的 `CSRF_HEADER`；logo 的格式判断取 `storeDesign.ts` 的 `isStoreLogoPath`。改动只在允许的八个文件；不做页面，不改路由与导航、后端、设计、`docs/TODO.md`、`.platform/`、CI 与部署配置，未装依赖。
- 设计闸门：DESIGN 1.20（提交 `a870f78`）第 15 条与 HANDOFF 0.84——标志的格式、大小、像素、替换与审计规则在 SHOP-TASK-211、212 的服务端；请求模块只传递与校验字段，不碰金额与个人资料。
- 导出（供 SHOP-TASK-215 的 A08 标志区与 SHOP-TASK-216 的页头页脚使用）：
  - `frontend/src/storeDesign.ts`：
    - `isStoreLogoPath(value: unknown): value is string`——只在 `value` 恰为 `/api/store-logos/` 加 32 位小写十六进制加 `.webp` 的字符串时为真（正则 `^\/api\/store-logos\/[0-9a-f]{32}\.webp$`，不带 m 标志）。
  - `frontend/src/api/adminStoreLogo.ts`：
    - `const ADMIN_STORE_LOGO_URL = "/api/admin/store-design/logo"`。
    - `type LogoUpload = { kind: "ok"; logo: string } | { kind: "none" } | { kind: "csrf" } | { kind: "type_unsupported" } | { kind: "too_large" } | { kind: "invalid" } | { kind: "failed" } | { kind: "network" }`。
    - `type LogoRemove = { kind: "ok" } | { kind: "none" } | { kind: "csrf" } | { kind: "failed" } | { kind: "network" }`。
    - `uploadStoreLogo(file: Blob, contentType: string, csrfToken: string, signal?: AbortSignal): Promise<LogoUpload>`。
    - `removeStoreLogo(csrfToken: string, signal?: AbortSignal): Promise<LogoRemove>`。
  - `frontend/src/api/adminStoreDesign.ts`：
    - `interface AdminStoreDesignDetail extends AdminStoreDesign { choices: ProductChoice[]; logo: string | null; csrf_token: string }`；`AdminStoreDesign`、`StoreDesignInput`、`StoreDesignRead`、`StoreDesignSave` 与两个函数的签名不变。
- 请求：两个请求都不带查询参数，凭同源 cookie，`cache: "no-store"`，`Accept: application/json`，`X-CSRF-Token` 为调用方给出的令牌，调用方可传入 `AbortSignal`。
  - 上传：PUT `/api/admin/store-design/logo`，请求体为调用方给的 `Blob` 本身（不用 `FormData`），`Content-Type` 为调用方给的类型字符串（不取 `Blob.type`）。
  - 移除：DELETE `/api/admin/store-design/logo`，没有请求体，也不带 `Content-Type`。
- 字段与合格条件：
  - 上传 200 的响应体为对象且 `logo` 使 `isStoreLogoPath` 为真；结果只带回 `logo`。
  - 移除 200 的响应体为对象且有 `logo` 一项、值恰为 `null`；结果不带字段。
  - 后台读取的 `logo` 为 `null` 或使 `isStoreLogoPath` 为真的字符串；缺这一项或不合格时整个读取为 `failed`。
- 归类（页面只按状态码与 detail 区分，错误体不带进结果）：
  - 上传：`ok`（200 且 logo 合格）、`none`（401）、`csrf`（403）、`type_unsupported`（415 且 `detail` 恰为 `image_type_unsupported`）、`too_large`（413，不读响应体）、`invalid`（422 且 `detail` 恰为 `logo_invalid`）、`failed`（其他 415 与 422，503 与其他状态，200 但响应体不合格或不是 JSON，回 201）、`network`（fetch 抛错，含调用方中止）。
  - 移除：`ok`（200 且 logo 为 null）、`none`（401）、`csrf`（403）、`failed`（其他状态，200 但 logo 不为 null、缺少或不是 JSON，回 204）、`network`。
- 偏离与取舍，请审阅（未改设计，未发现须停下的设计问题）：
  - 上传的 413 照 HANDOFF 0.84 一律为 `too_large`，不读响应体，所以接口自己的 `image_too_large` 与反向代理的 HTML 413 不加区分。
  - 请求模块不检查文件的类型与大小：页面的初步检查交给 SHOP-TASK-215，取值以服务端为准；`contentType` 原样放进请求头。
  - 移除 200 的响应体缺 `logo` 一项算 `failed`（接口的 `LogoOut` 总会给出 `logo`），不按 `undefined` 视同 `null`。
  - `isStoreLogoPath` 的参数类型为 `unknown`、返回类型守卫，便于直接用于响应体的字段；`null` 不算路径，读取另行允许 `null`。
  - `storeDesign.ts` 文件头注释的「不含标志图」说的是公开接口的店铺装修设置，按「其余内容不变」未改。
  - `adminStoreDesign.test.ts` 原有测试 `keeps only the agreed fields` 的响应体里 `logo: "ignored"`（原本作为多出的字段被丢弃）改为合格的 `logo: null`；顶层多出字段不带进结果，由新增的标志上传测试与 `neither sends nor keeps a logo when saving` 守住。
  - 中止归为 `network` 的两条测试守住的是本任务验收「网络中断与调用方中止为 network」：接口记录、DESIGN 1.20 第 15 条与 UX 0.18 A08 都没有关于中止的原句，测试注释照实写明。
- 留给之后：A08 的标志区交给 SHOP-TASK-215，页头页脚显示标志交给 SHOP-TASK-216。
- 验证到什么程度：人工逐条对照验收标准、`app/api/admin_store_logo.py`、`app/api/admin_store_design.py`、DESIGN 1.20 第 15 条、UX 0.18 A08 与 HANDOFF 0.84 自查。测试（vitest，fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替，从 `mock.calls` 取值时用 `String(…)` 收窄）覆盖：
  - `frontend/src/storeDesign.test.ts`（`store logo paths`）：两个合格路径为真；大写、单个大写字符、31 与 33 位、非十六进制字母、其他前缀、缺开头斜杠、绝对地址、其他扩展名与大写扩展名、带查询串、末尾换行、空串、null、数字与对象都为假。
  - `frontend/src/api/adminStoreLogo.test.ts`：
    - 两个请求的地址、方法、同源 cookie、no-store、Accept、CSRF 头与 signal；移除没有请求体与 `Content-Type`。
    - 上传的请求体为原样的那个 `Blob`（同一对象、不是 `FormData`），`Content-Type` 为给定的 `image/jpeg`、`image/png` 或 `image/webp`（`Blob` 自带的类型不同）。
    - 上传 200 只带回 logo，多出字段不带进结果；logo 缺少、为 null、大写、31 位、其他前缀与扩展名、数字、空串，整体为数组、null 或字符串，不是 JSON 与回 201 都为 `failed`。
    - 上传的归类：401、403、415 `image_type_unsupported`、422 `logo_invalid`；415 与 422 的 detail 对调、大小写不同、错误列表、空对象、不是 JSON，503、404、409、500 为 `failed`；413 来自接口、来自反向代理（HTML）、空响应体与其他 detail 时都为 `too_large`，且响应体未被读取（`bodyUsed` 为 false）；网络中断与中止为 `network`。
    - 移除：200 且 logo 为 null（含多出字段）为 `ok`；200 带合格路径、空串、缺 logo、null、数组、不是 JSON 与回 204 为 `failed`；401、403，以及 404、413、415、422、503、500 为 `failed`；网络中断与中止为 `network`。
  - `frontend/src/api/adminStoreDesign.test.ts`：后台读取的 logo 为 null 与合格路径时原样带进结果；缺少、大写、31 位、其他前缀与扩展名、空串、数字与对象时为 `failed`；保存时调用方对象上的 logo 不进请求体，响应里多出的 logo（合格或不合格）不带进结果、不致失败。现有测试只为 logo 字段补上合格的值。
  - `frontend/src/pages/AdminStoreDesignPage.test.tsx`：只改上面列出的两处。
  - 除两条中止测试（见偏离一节）外，每条新增测试的注释写明它守住的接口记录（`app/api/admin_store_logo.py`、`app/api/admin_store_design.py`）、DESIGN 1.20 第 15 条、UX 0.18 A08 或 HANDOFF 0.84 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建；Worker 沙箱不跑前端检查）。本任务不做浏览器验收。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-11-06 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 写法照 frontend/src/api/adminProductImages.ts（同源 cookie、cache no-store、Accept、写操作带调用方传入的 CSRF 令牌于 pay.ts 的 CSRF_HEADER、接收 AbortSignal、只取接口记录列出的字段且多出的字段不带进结果、响应体不合格算失败、页面只按状态码与 detail 区分，不显示错误体）
      - 格式判断——frontend/src/storeDesign.ts 新增导出的 isStoreLogoPath(value)，只在 value 恰好是 /api/store-logos/ 加 32 位小写十六进制加 .webp 的字符串时为真；storeDesign.ts 其余内容不变
      - 上传——uploadStoreLogo(file, contentType, csrfToken, signal) 发 PUT /api/admin/store-design/logo，请求体为调用方给的 Blob 本身（不用 FormData），Content-Type 为调用方给的类型字符串；200 且 logo 合格为 ok 并带回 logo；415 且 detail 为 image_type_unsupported 为 type_unsupported；413 为 too_large（不看响应体）；422 且 detail 为 logo_invalid 为 invalid；401 为 none，403 为 csrf；其他状态（含 503）、其他 415 与 422、响应体不合格为 failed；网络中断与调用方中止为 network
      - 移除——removeStoreLogo(csrfToken, signal) 发 DELETE /api/admin/store-design/logo，不带请求体；200 且 logo 为 null 为 ok；401 为 none，403 为 csrf；其他为 failed；网络中断与中止为 network
      - 后台读取——frontend/src/api/adminStoreDesign.ts 的 AdminStoreDesignDetail 加 logo（string 或 null），读取时 logo 为 null 或 isStoreLogoPath 为真的字符串才合格，缺这一项或不合格算 failed；保存的请求与结果（AdminStoreDesign、StoreDesignSave）不变
      - 测试验证意图（不加测试依赖，fetch 以替身代替）——frontend/src/api/adminStoreLogo.test.ts、frontend/src/api/adminStoreDesign.test.ts 与 frontend/src/storeDesign.test.ts：两个请求的方法、地址、请求头、no-store 与请求体（上传为原样的 Blob 与给定的 Content-Type，不是 FormData；移除没有请求体）；logo 的合格判定（大写、31 位、其他前缀与扩展名、非字符串都不合格）；各种状态与 detail 的归类（含 413 的响应体不是 JSON 时仍为 too_large、移除的 200 带非 null 的 logo 为 failed）；中止为 network；后台读取的 logo 为 null、合格路径、缺少与不合格四种情形；每条测试写明它守住的是接口记录（app/api/admin_store_logo.py、app/api/admin_store_design.py）、DESIGN 1.20 第 15 条或 UX 0.18 A08 的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，从 mock.calls 取值时用 String(…) 收窄，不让 any 流出；现有测试只为 logo 字段补上合格的值；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - frontend/src/pages/AdminStoreDesignPage.test.tsx 只为后台读取新增的 logo 而改——常量 DETAIL 补 logo 为 null（PICKING 与各处 fresh 由它展开，随之带上）；测试 updates the form from the saved design 里期望的 base 补 logo，取 DETAIL.logo（saveStep 以读到的设置展开保存结果，保存后仍保留读到的 logo）；该文件其他测试与断言不增不删不改，frontend/src/pages/AdminStoreDesignPage.tsx 不改；这是本任务接入后该文件仅有的两处失效（运营者在 main 的导出上实测，类型检查只报 DETAIL 缺 logo，测试只有这条保存测试的三种语言失败）
      - docs/task-records/SHOP-TASK-214.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；导出的类型与函数签名，供 SHOP-TASK-215、216 使用；验证到什么程度，只写实际存在的测试）；改动只落在 allowed_change_paths；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖；不改后端；以 Draft PR 交付（设计闸门：DESIGN 1.20 第 15 条，同上），不合并、不直推
```

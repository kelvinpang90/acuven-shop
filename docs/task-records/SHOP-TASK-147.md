# SHOP-TASK-147 会员忘记密码 P12（短信验证后重设）

设计闸门：DESIGN 1.12（提交 acf576e）「权限与资料保护」第 3 条（重设再次短信验证；未注册号码不创建账号；开关关闭时重设暂停；密码至少 8 位）与 HANDOFF 0.58。内容与文案依据 docs/UX.md 0.12 的 P12 忘记密码部分、docs/UX-COPY.md 0.13；样式依据视觉稿 P12-desktop 的重设部分与 P12-reset-states（P12-phone 没有画重设部分）。

- [x] 路由：`/forgot-password` 加进 `frontend/src/router.tsx` 的路由表（会员登录之后、后台页之前）与 `frontend/src/App.tsx` 的页面表，套前台框架。进表之后，SHOP-TASK-146 登录页的 `forgotPath()` 返回 `/forgot-password`，密码页签的 auth.forgot 随之出现；`LoginPage.tsx` 未改。`router.tsx` 的来源规则本来就把 `/forgot-password` 当作不可作来源的页，未改。
- [x] 页面 `frontend/src/pages/ForgotPasswordPage.tsx`（结构照 `AdminStoreDesignPage.tsx` 与 `LoginPage.tsx`：导出把结果变成状态改动的纯函数 `verifiedStep`、`resetStep`、`startResetting`、`editPassword`、`canReset`、`passwordLength`、`smsEnabledFor`，接收 AbortSignal 与回调的异步函数 `submitReset`，只依赖 props 的 `ResetView`，以及默认导出的 `ForgotPasswordPage`）：
  - 标题 auth.reset_title（h1）与 V1（`SmsVerify`，用途 reset_password）。站点设置照 SHOP-TASK-146 的写法读取——直接复用 `LoginPage.tsx` 导出的 `loadSettings` 与 `LOADING_SETTINGS`（打开页面时读一次，切换语言不重新读取）。读取完成前内容区标 aria-busy、为空，不渲染 V1；读取失败只显示 `ErrorNotice`（common.error_retry）；开关关闭时 V1 自己换成 auth.sms_paused，不显示 auth.sms_scope。
  - 验证通过之后（V1 的回调交给 `verifiedStep`）：
    - registered：记下 V1 交回的 CSRF 令牌，V1 之下显示新密码表单——auth.new_password（type="password"，autocomplete new-password）、其下 auth.password_rule（字段说明 `acs-field__hint`，与输入以 aria-describedby 关联）、整宽主按钮 auth.reset_submit。新密码按 Unicode 码点计数（`Array.from(password).length`，与服务端 Python 的 len 相同），不足 8 个或超过 256 个、提交进行中、已重设或暂停时按钮禁用。
    - not_registered：显示 auth.reset_not_registered（info 提示，照 P12-reset-states）与去 `/register` 的 common.nav_register；`registerPath()`（`LoginPage.tsx` 导出，照 `CheckoutPage.tsx` 的 `loginPath` 写法）在 `/register` 进表之前为 null，只显示文案、不渲染链接。不显示密码输入。
  - 提交：`submitReset` 经 SHOP-TASK-144 的 `submitPasswordReset`（未改 `frontend/src/api/`），请求体只有新密码，CSRF 令牌只在请求头里。结果（`resetStep`）：
    - ok → auth.reset_done（success 提示）与去 `/login` 的 common.nav_login 链接，新密码与令牌清空、表单收起；
    - too_short → 留在表单，auth.password_rule 改为字段错误（`acs-field--invalid`、`acs-field__error`、aria-invalid）；
    - expired 与 csrf → 回到验证前的初始状态（令牌与新密码清空），V1 的 key 加一、重新挂载回到初始状态，V1 之上显示 common.error_retry；再次验证通过时这条提示收起；
    - disabled → 回到验证前，页面之后把开关当作关闭（交给 V1 的 smsEnabled 为假），V1 显示 auth.sms_paused，不另显示 common.error_retry；
    - unavailable → common.service_unavailable，failed → common.error_retry，network → common.network_check，都在按钮之前的 danger 提示条里，新密码保留、可再提交。
  - 离开页面时中止站点设置的读取与进行中的提交（同一个 AbortController），之后不再更新。传给事件属性的处理函数都不返回 Promise（以 `void` 丢弃异步函数的结果）。新密码与 CSRF 令牌只在页面状态与请求里，不进地址、日志或浏览器存储。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.13 逐字加入本页用到而字典里还没有的 auth.reset_title、auth.new_password、auth.password_rule、auth.reset_submit、auth.reset_done 与 auth.reset_not_registered（每种语言一行，放在 auth.forgot 之后）。用到的 auth.sms_paused、common.nav_register、common.nav_login、common.error_retry、common.service_unavailable 与 common.network_check 字典里已有。
- [x] `frontend/src/styles/site.css` 新增 `.site-reset` 一段（只写布局）：桌面为居中的 480px 卡片（页面上 64px、下 96px，卡片内边距 32px，照 P12-desktop 的重设卡片），标题与内容区间距 18px；内容区里提示、V1、提示条与链接间距 16px（照 P12-reset-states）；新密码表单各块 18px；两个链接靠左；字段错误图标不收缩、下移 2px。767px 及以下（P12-phone 没有画重设部分，照同页登录部分的手机写法）：卡片不绘制（display: contents），内容区上 28px、左右 16px、下 40px，间距 16px，表单 16px。未声明颜色、字体与圆角，未改 `frontend/src/styles/acuven-shop.css`。
- [x] 测试：
  - `frontend/src/pages/ForgotPasswordPage.test.tsx`（vitest 与 react-dom/server，以 renderToStaticMarkup 渲染 `ResetView` 与经 `App` 打开 `/forgot-password`，直接调用导出的函数，不模拟点击；fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替）：经路由打开时只有标题、内容区 aria-busy；读取中只有标题；读取失败只有 common.error_retry；初始只有 V1（有 auth.sms_scope 与发送按钮，没有游客文字、新密码、未注册文字与链接）；开关关闭时 V1 只有 auth.sms_paused；registered 后 V1 之下的新密码表单（password 输入、auth.password_rule 说明、开始时禁用的按钮）；按码点计数的按钮条件（7 与 8 个字符、含一个表情符号的 7 与 8 个码点、256 与 257 个表情符号或字母）；未验证、未注册、提交进行中、已重设与暂停时不可提交；提交请求的地址、方法、请求头里的 V1 令牌与只有新密码的请求体；页面上不出现令牌；ok、too_short、expired、csrf、disabled、unavailable、failed、network 各结果的状态与页面（expired 与 csrf 回到初始状态、V1 的 key 换了、common.error_retry 在 V1 之上、再次验证后收起）；离开页面后不更新；not_registered 的文案、目标路由不存在时不渲染注册链接（给出路径时以已有的 `/privacy` 代替）、没有密码输入；三种语言下各状态的文字全部来自字典（+60、+65 除外）。每条测试的注释写明守住的验收、UX、设计、HANDOFF 或接口记录的哪一句。
  - `frontend/src/router.test.tsx`：路由表断言加上 `/forgot-password`，原来断言它不在表里的一行改为在表里（`/register` 仍不在表里）——这是进表必须的改动；新增 `/forgot-password` 经 App 在前台框架里渲染（读取中只有标题、内容区 aria-busy）与路径不带段值两条。
  - `frontend/src/pages/LoginPage.test.tsx`：只改「renders auth.forgot only when the reset page exists」一条——`/forgot-password` 进表后 `forgotPath()` 不再为 null，原断言随之不再成立，改为断言它是 `/forgot-password`、给出时链接指向它；`forgotTo` 为 null 时不渲染的一侧保留。其余测试未改。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 视觉稿 P12-desktop 的重设卡片标题是 h2（与登录卡片画在同一页）；UX 说忘记密码是独立步骤页，本页标题为 h1。
  - 视觉稿重设卡片里的遮掩号码一行（「+60 12•••• XXX」，`acs-num` 加粗）没有字典键，不渲染；V1 在验证通过后仍显示 auth.code_sent（含遮掩号码）。这一行的字重 site.css 不搬。没有其他 site.css 写不了的颜色、字号、字重或底色（卡片、字段、提示条与按钮的样式都来自设计系统类）。
  - 去 `/login` 的 common.nav_login：UX 与视觉稿没有给这个链接的键与样式，按验收取 P11 去 P12 已用的键（HANDOFF 0.58 第（g）条）；样式取 P12-reset-states 里 common.nav_register 的次要按钮（`acs-btn acs-btn--secondary`，靠左），两个链接一致。
  - too_short 时「显示 auth.password_rule」的做法：auth.password_rule 本来一直作为字段说明显示，too_short 时改为字段错误（同一句，带错误图标与 aria-invalid）；不另加文案。
  - 提交按钮在新密码长度不合格时禁用，页面不另显示长度提示（auth.password_rule 一直在字段下方）。
  - 新密码只在 ok、expired、csrf 与 disabled 时清空；too_short 与其他失败时保留，便于修改后再提交。
  - disabled 之后（页面已当作开关关闭）不再重新读取站点设置，刷新页面才会重新读取。
  - 页面不显示 ★ 提示：UX P12 的线框里忘记密码部分只有 auth.reset_title 与 V1，auth.login_demo_hint 属于登录部分。
  - 重设成功后不自动跳转（UX 只写「重设完成 → 登录」，以 common.nav_login 链接前往）。
- 改动只在 `frontend/src/pages/ForgotPasswordPage.tsx`、`frontend/src/pages/ForgotPasswordPage.test.tsx`、`frontend/src/pages/LoginPage.test.tsx`、`frontend/src/router.tsx`、`frontend/src/router.test.tsx`、`frontend/src/App.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；未改 `frontend/src/components/SmsVerify.tsx`、`frontend/src/api/`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、docs/UX.md 0.12 的 P12 与 V1、UX-COPY 0.13、HANDOFF 0.58、视觉稿 P12-desktop、P12-phone 与 P12-reset-states，以及 `frontend/src/api/memberAuth.ts`、`frontend/src/components/SmsVerify.tsx` 自查；另查看了按路由表遍历每页的现有测试（`App.test.tsx`、`SiteFrame.test.tsx`、`router.test.tsx` 的链接检查）对 `/forgot-password` 的要求。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收，未对真实 Turnstile 与后端接口验证。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.12 的 P12、docs/UX-COPY.md 0.13 与 docs/HANDOFF.md 0.58 为准，样式以 docs/design/pages/ 的 P12-desktop、P12-phone 的重设部分与 P12-reset-states 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.13 逐字加入本页用到而字典里还没有的键，写法与相邻条目相同（每种语言一行）
      - 路由 /forgot-password 加进 frontend/src/router.tsx 的路由表与 frontend/src/App.tsx 的页面表，套前台框架；页面为 frontend/src/pages/ForgotPasswordPage.tsx——标题 auth.reset_title 与 V1（用途 reset_password，开关的值与公开 key 照 SHOP-TASK-146 的写法读取站点设置；读取完成前不渲染 V1、标 aria-busy，读取失败只显示 common.error_retry）；开关关闭时由 V1 显示 auth.sms_paused，不显示 auth.sms_scope
      - 验证通过之后——registered 时 V1 之下显示新密码（auth.new_password，输入类型为 password）、auth.password_rule 与 auth.reset_submit，以 V1 交回的 csrf_token 提交；新密码按 Unicode 码点计数（与服务端 Python 的 len 相同，不按 UTF-16 长度），不足 8 个或超过 256 个时按钮禁用（服务端仍会判定）；not_registered 时显示 auth.reset_not_registered 与去 /register 的 common.nav_register（/register 进入路由表之前只显示文案、不渲染链接，照 CheckoutPage.tsx 的 loginPath 写法），不显示密码输入
      - 提交结果——ok 显示 auth.reset_done 与去 /login 的 common.nav_login 链接（UX 与视觉稿没有给这个链接的键，取 P11 去 P12 已用的键），密码输入收起；too_short 留在表单并显示 auth.password_rule；expired 与 csrf 以新的 key 重新挂载 V1、回到验证前的初始状态，并在 V1 之上显示 common.error_retry（同一 csrf_token 重交必然再被拒，凭据须重新验证取得）；disabled 时页面把开关当作关闭，V1 显示 auth.sms_paused，不另显示 common.error_retry；unavailable 显示 common.service_unavailable、failed 显示 common.error_retry，都留在表单可再提交；network 显示 common.network_check；新密码与 csrf_token 只在页面状态与请求里，不进地址、日志或浏览器存储
      - 代码结构照 frontend/src/pages/AdminStoreDesignPage.tsx——导出把请求结果变成页面状态的纯函数、接收 AbortSignal 与回调的异步函数，以及只依赖 props 的 View 组件；离开页面时中止请求，之后不再更新；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/ForgotPasswordPage.test.tsx 验证意图（以 renderToStaticMarkup 渲染 View、直接调用导出的函数，不模拟点击）——初始只有 V1；registered 后的新密码表单与按码点计数的按钮条件（含一个表情符号算一个字符）；提交请求带 V1 交回的 csrf_token；各提交结果（含 expired 与 csrf 回到 V1 初始状态）；not_registered 的文案与注册链接（目标路由不存在时不渲染链接）；开关关闭；页面文字（国家码选项 +60、+65 除外）全部来自字典；每条测试写明它守住的是 UX、设计或接口记录的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）；现有测试（含 router.test.tsx 的路由表断言与 LoginPage.test.tsx 里 auth.forgot 不渲染的断言）只在必要时修改，并写明原因
      - docs/task-records/SHOP-TASK-147.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；验证后的两种结果、提交、偏离、验证到什么程度，只写实际存在的测试，写明未做浏览器验收）；改动只落在 allowed_change_paths（不改 frontend/src/components/SmsVerify.tsx 与 frontend/src/api/）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.12 提交 acf576e 与 HANDOFF 0.58），不合并、不直推
```

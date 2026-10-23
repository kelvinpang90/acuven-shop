# SHOP-TASK-146 会员登录 P12（密码与短信验证码两个页签）

设计闸门：DESIGN 1.12（提交 acf576e）「权限与资料保护」第 3 条（密码登录对未设密码账号与错误密码同一失败提示；开关关闭时已设密码者仍可密码登录）与 HANDOFF 0.58。内容与文案依据 docs/UX.md 0.12 的 P12 登录部分、docs/UX-COPY.md 0.13；样式依据视觉稿 P12-desktop 与 P12-phone 的登录部分，成功提示依据 V1-verified。

- [x] 路由：`/login` 加进 `frontend/src/router.tsx` 的路由表（隐私说明之后、后台页之前）与 `frontend/src/App.tsx` 的页面表，套前台框架。进表之后，页头已有的 common.nav_login（`SiteFrame.tsx`，桌面导航与手机菜单）与结账第 1 步的 checkout.login_password 按原有的路由规则出现并指向 `/login`；这两处代码未改。
- [x] 来源页与去向（`frontend/src/router.tsx` 导出，供 SHOP-TASK-147、148 复用）：
  - `RouterProvider` 的 push（`navigate`）、`replace` 与浏览器前进后退（popstate）都经同一个 `moveTo`：以路由状态里切换前的路径为准，按 `nextSource` 更新只在内存（ref）里的来源——换了路径且离开的是可作来源的页（`isSourcePath`：不以 `/admin` 开头，也不是 `/login`、`/register`、`/forgot-password`）时记下离开的路径，否则保留原来的。只记路径（路由状态里的路径本来就不含查询串），不进地址、历史状态与浏览器存储；刷新后没有来源。同一路径只换查询串不算离开。
  - `useRouter().source()`：读取记下的来源（没有时为 null）。登录页打开时读一次，在本页期间不变。
  - `verifiedPath(source)`：成功后的去向——来源为可作来源的路径时回到来源，没有来源、来源为后台页或登录页本身时去首页 P01（P13 上线后由那个任务改为去 P13）。
  - `guestPath(source)`：auth.continue_guest 的去向——来源为 `/checkout` 时 `/checkout`，否则 `/`。
  - `VERIFIED_REDIRECT_MS`（1500）。
- [x] 页面 `frontend/src/pages/LoginPage.tsx`（结构照 `AdminStoreDesignPage.tsx`：导出把结果变成状态改动的纯函数 `passwordStep`、`startSubmitting`、`canSubmit`、`smsEnabledOf`、`smsVerifiedCopy`，接收 AbortSignal 与回调的异步函数 `loadSettings`、`submitPassword`、`finishSms`、`redirectLater`，只依赖 props 的 `LoginView`，以及默认导出的 `LoginPage`）：
  - 标题 auth.login_title（h1）、★ auth.login_demo_hint 与两个页签（`acs-tabs` 里 role="tab" 的按钮，auth.login_method_password 与 auth.login_method_sms，默认密码页签）。两个页签的内容（role="tabpanel"，与页签以 aria-controls / aria-labelledby 互相指向）都留在页面上，未选的为 hidden，所以切换页签不清空密码页签已填的内容，也不卸载 V1（进度保留）。
  - 站点设置：页面打开时照 `CheckoutPage.tsx` 的写法读一次 `fetchSiteSettings`（effect 无依赖，切换语言不重新读取，已填内容与 V1 进度不受影响），取开关的值与人机挑战公开 key。读取完成之前只有标题、★ 提示与两个页签，页签内容区标 aria-busy、为空（不渲染表单与 V1，页签也不带 aria-controls）。读取失败时密码页签照常可用（登录失败按开关关闭显示 auth.login_failed_sms_off），短信验证码页签只有 `ErrorNotice`（common.error_retry）。
  - 密码页签：auth.phone（国家码下拉 `SMS_REGIONS` 的 +60、+65，默认 +60，以 auth.phone 的标签作读屏名称，与 V1 同一写法；号码为 tel 输入）、auth.password（type="password"，autocomplete current-password）、提示、auth.login_submit（整宽主按钮，提交进行中禁用并标 aria-busy）与 auth.forgot。`forgotPath()` 照 `CheckoutPage.tsx` 的 `loginPath` 写法以 `isRoutePath` 判断，`/forgot-password` 进表之前为 null、不渲染。
  - 提交：`submitPassword` 经 SHOP-TASK-144 的 `passwordLogin`（未改 `frontend/src/api/`），请求体只有号码原文、地区与密码。结果：ok → 已登录；login_failed → 开关开启 auth.login_failed、关闭（含读取失败）auth.login_failed_sms_off；phone_invalid → 号码下 checkout.phone_invalid；unavailable → common.service_unavailable；failed → common.error_retry；network → common.network_check。每种结果都保留号码、清空密码。密码只在页面状态与请求体里。
  - 短信验证码页签：auth.same_flow_note 与 V1（`SmsVerify`，用途 login，传入开关的值与公开 key，`guestTo` 为 `guestPath(来源)`）；开关关闭时由 V1 显示 auth.sms_paused。其下 common.nav_register，`registerPath()` 写法同上，`/register` 进表之前不渲染。
  - 成功之后：密码登录 ok 时密码页签整块换成 auth.verified_login；短信验证通过时 V1 之后显示 `smsVerifiedCopy`（created 为真 auth.verified_registered，否则 auth.verified_login），样式照 V1-verified（success 提示，role="status"）。随后等 `VERIFIED_REDIRECT_MS`，以路由的 replace 前往 `verifiedPath(来源)`。计时以 `Wait`（毫秒与 AbortSignal）注入，页面用 `timerWait`（setTimeout，中止时 clearTimeout）。
  - 离开页面时中止站点设置的读取、进行中的密码登录与成功后的计时（同一个 AbortController），之后不再更新、不再跳转。传给事件属性的处理函数都不返回 Promise（以 `void` 丢弃异步函数的结果）。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.13 逐字加入本页用到而字典里还没有的 common.nav_register、auth.same_flow_note、auth.verified_login、auth.verified_registered、auth.login_title、auth.login_method_password、auth.login_method_sms、auth.login_failed、auth.login_failed_sms_off、auth.forgot 与 auth.login_demo_hint（每种语言一行）。用到的 auth.phone、auth.password、auth.login_submit、checkout.phone_invalid 与 common.service_unavailable、common.error_retry、common.network_check 字典里已有。
- [x] `frontend/src/styles/site.css` 新增 `.site-login` 一段（只写布局）：桌面为居中的 480px 卡片（页面上 64px、下 96px，卡片内边距 32px），标题、提示、页签与页签内容间距 18px，页签内容与密码表单同样 18px；国家码下拉 96px；auth.forgot 与 common.nav_register 靠左，common.nav_register 上方 14px；字段错误图标不收缩、下移 2px。767px 及以下：卡片不绘制（display: contents），内容区上 28px、左右 16px、下 40px，间距 16px；auth.forgot 与 common.nav_register 居中。未选的页签内容以 `:not([hidden])` 限定 display，保证 hidden 生效。未改 `frontend/src/styles/acuven-shop.css`。
- [x] 测试：
  - `frontend/src/pages/LoginPage.test.tsx`（vitest 与 react-dom/server，以 renderToStaticMarkup 渲染 `LoginView` 与经 `App` 打开 `/login`，直接调用导出的函数，不模拟点击；fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替，计时以替身代替，`timerWait` 以 vitest 的替身计时器推进）：经路由打开时的标题、提示与默认选中的密码页签；读取中只有四段文字、内容区 aria-busy、没有表单；两个页签内容都在页面上、未选的 hidden、已填的号码保留；站点设置读一次、读取失败与中止后不更新；读取失败时密码页签可提交、短信页签只有 common.error_retry、登录失败为 auth.login_failed_sms_off；国家码两项默认 +60、号码与密码输入、登录按钮；提交进行中禁用；请求的地址、方法与请求体；开关开闭时的两种登录失败文案与锁定同样处理；phone_invalid、unavailable、failed、network 的提示与保留号码清空密码；提示条与字段错误的位置；auth.forgot 与 common.nav_register 在目标路由不存在时不渲染（给出路径时以已有的 `/privacy` 代替）；短信页签在 auth.same_flow_note 之后嵌入 V1、开关关闭时 auth.sms_paused 与 continue_guest 的去向（来源 `/checkout`、`/cart` 与没有来源）、公开 key 为 null 时的替换；密码登录成功后的事件顺序与去向（来源为前台页、后台页、登录页本身与没有来源）；已登录时表单换成 auth.verified_login；短信通过按 created 的两种文案与跳转；离开页面后不更新、计时期间离开不跳转；三种语言下各状态的文字全部来自字典（+60、+65 除外）。每条测试的注释写明守住的验收、UX、设计或 HANDOFF 的哪一句。
  - `frontend/src/router.test.tsx`：路由表断言加上 `/login`（原来断言 `/login` 不在表里的一行改为在表里，并断言 `/forgot-password`、`/register` 仍不在表里）——这是本任务必须的改动；新增 `/login` 经 App 在前台框架里渲染（读取中、页头登录项为当前）、`/login` 不带段值；`nextSource` 走一串路径时的来源、`isSourcePath`、`verifiedPath`、`guestPath` 与 `VERIFIED_REDIRECT_MS`。
  - `frontend/src/pages/CheckoutPage.test.tsx`：只改「shows the step title, label, hint and continue button but no login link」一条——`/login` 进表后结账第 1 步出现 checkout.login_password，原断言（不渲染）随之不再成立，改为断言该链接指向 `/login` 且只出现一次，标题相应改为「…and the login link to /login」；其余测试未改。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - common.nav_register 的位置：视觉稿把它画在选中密码页签的卡片底部，UX P12 线框与本任务验收把它放在短信验证码页签之下，二者冲突，以 UX 为准放在短信页签内容的末尾（V1 与成功提示之后）；读取失败时短信页签只显示 common.error_retry，所以不显示它。目前 `/register` 不在路由表，不渲染。
  - 视觉稿 P12-desktop 里 common.nav_register 之上的分隔线（`border-top` 的颜色）site.css 不搬，只保留上方 14px；没有其他 site.css 写不了的颜色、字号、字重或底色（页签、卡片、提示条的样式都来自设计系统类）。
  - 视觉稿的国家码下拉带英文读屏标签「Country code」，UX-COPY 没有这一条；不自编文案，改以 auth.phone 的标签作读屏名称（aria-labelledby，与 V1、结账第 1 步同一写法）。
  - 视觉稿把 auth.forgot 画成 quiet 按钮；它是去另一页的入口，做成同样样式（acs-btn acs-btn--quiet）的站内链接。
  - 视觉稿 P12-desktop 的右栏「Reset password」是忘记密码（独立步骤页，SHOP-TASK-147），本页只做登录卡片，桌面居中显示一张。视觉稿页头把 Log in 画在右侧与购物车同一组，页头是已有的 `SiteFrame.tsx`（common.nav_login 在导航组），本任务不改页头。
  - UX P12 线框密码页签的 `[common.rate_limited]` 用不到：密码登录连续失败锁定时服务端仍回 401 login_failed（HANDOFF 0.58 第（g）条），页面显示登录失败文案；字典里未因本页加入别的键。
  - 开关关闭时短信页签仍显示 auth.same_flow_note（UX 只说 V1 换成 auth.sms_paused、不显示 auth.sms_scope，未说隐藏这一句）。
  - 登录按钮只在提交进行中（与已登录后）禁用；号码或密码为空时照常提交，由服务端判定（按 `api/memberAuth.ts` 的归类显示上面的提示），页面不另加未在 UX 出现的校验或文字。
  - 成功之后两个页签仍可切换；密码登录成功时密码页签只剩 auth.verified_login，短信验证通过时 V1 自己进入已验证状态，页面在其后显示结果文字。
  - 读取中页签可以点选（只改选中项），内容区仍为空。
  - V1 在未选的短信页签里仍挂载（为了保留进度），人机挑战因而可能在隐藏的容器里渲染。
  - 页面不查询是否已登录：已登录的会员打开 `/login` 仍看到登录表单；登录后的会员中心入口与页头切换由之后的会员中心任务接入。UX P05「从 P12 返回后保留已填内容」本任务不处理（结账第 1 步的号码只在页面内存里），按 HANDOFF 0.58 留给结账会员路径的任务。
- 改动只在 `frontend/src/pages/LoginPage.tsx`、`frontend/src/pages/LoginPage.test.tsx`、`frontend/src/pages/CheckoutPage.test.tsx`、`frontend/src/router.tsx`、`frontend/src/router.test.tsx`、`frontend/src/App.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；未改 `frontend/src/components/SmsVerify.tsx`、`frontend/src/api/`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、docs/UX.md 0.12 的 P12 与 V1、UX-COPY 0.13、HANDOFF 0.58、视觉稿 P12-desktop、P12-phone 与 V1-verified，以及 `frontend/src/api/memberAuth.ts`、`frontend/src/components/SmsVerify.tsx` 自查；另查看了按路由表遍历每页的现有测试（`App.test.tsx`、`SiteFrame.test.tsx`、`AdminLoginPage.test.tsx`、`router.test.tsx` 的链接检查）对 `/login` 的要求。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收，未对真实 Turnstile 与后端接口验证。

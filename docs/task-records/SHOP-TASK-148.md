# SHOP-TASK-148 会员注册 P11

设计闸门：DESIGN 1.12（提交 acf576e）「权限与资料保护」第 3 条与「失败、并发与重试」（注册即短信验证；短信失败不创建账号；自动认领由服务端完成）与 HANDOFF 0.58。内容与文案依据 docs/UX.md 0.12 的 P11（与 P07 的游客注册链接）、docs/UX-COPY.md 0.13；样式依据视觉稿 P11-desktop 与 P11-phone，成功提示依据 V1-verified，P07 的注册链接依据 P07-phone-guest。

- [x] 路由：`/register` 加进 `frontend/src/router.tsx` 的路由表（忘记密码之后、后台页之前）与 `frontend/src/App.tsx` 的页面表，套前台框架。`router.tsx` 的来源规则本来就把 `/register` 当作不可作来源的页，未改；页头（`SiteFrame.tsx`，未改）的导航表里没有注册项，所以页头不出现注册入口（UX P11「页头只有登录入口」）。进表之后，SHOP-TASK-146 登录页短信验证码页签与 SHOP-TASK-147 重设页「号码未注册」的 common.nav_register 经 `registerPath()` 随之出现并指向 `/register`；`LoginPage.tsx` 与 `ForgotPasswordPage.tsx` 未改。
- [x] 页面 `frontend/src/pages/RegisterPage.tsx`（结构照 `AdminStoreDesignPage.tsx` 与 `LoginPage.tsx`：导出的纯函数 `registerVerify`、异步函数 `finishRegister`、只依赖 props 的 `RegisterView`，以及默认导出的 `RegisterPage`）：
  - 卡片（依次）：auth.register_title（h1）、★ auth.register_demo_hint（`DemoHint`）、auth.same_flow_note、V1 所在的内容区、去 `/login` 的 common.nav_login。
  - 站点设置：直接复用 `LoginPage.tsx` 导出的 `loadSettings` 与 `LOADING_SETTINGS`（打开页面时读一次，切换语言不重新读取）。读取完成前内容区标 aria-busy、为空，不渲染 V1（标题、★ 提示、说明与 common.nav_login 照常）；读取失败内容区只显示 `ErrorNotice`（common.error_retry）。
  - V1：`SmsVerify` 以 `registerVerify` 给出的属性嵌入——用途 register、站点设置里的开关的值与公开 key、auth.continue_guest 的去向 `guestPath(来源)`（router.tsx 导出：来源为 `/checkout` 时 `/checkout`，否则首页 P01）。开关关闭时 V1 自己换成 auth.sms_paused 与 auth.continue_guest，不显示 auth.sms_scope 与 auth.claim_notice；公开 key 为 null 时 V1 换成 auth.sms_failed 与 auth.continue_guest（SHOP-TASK-145 的行为）。号码已注册则登录、未注册则创建无密码会员并登录、自动认领，都由服务端在 V1 的短信登录接口里完成，页面不另发请求。
  - 成功之后：V1 回调交给 `finishRegister`，按 created 显示 auth.verified_registered 或 auth.verified_login（`LoginPage.tsx` 的 `smsVerifiedCopy`；V1 之后、common.nav_login 之前的 success 提示，role="status"，照 V1-verified），再经 `LoginPage.tsx` 的 `redirectLater` 等 `VERIFIED_REDIRECT_MS`（1500 毫秒）后以路由的 replace 前往 `verifiedPath(来源)`——来源为可作来源的前台页时回到来源，没有来源、来源为后台页或会员登录 / 注册 / 重设页时去首页 P01（会员中心 P13 上线后由那个任务改为去 P13）。来源在页面打开时读一次。重设密码的两种结果不会出现在这一用途，忽略。
  - 离开页面时中止站点设置的读取与成功后的计时（同一个 AbortController），之后不再更新、不再跳转；V1 自己在卸载时中止它的请求。传给事件属性的处理函数不返回 Promise（以 `void` 丢弃异步函数的结果）。
- [x] 结果页 P07：`frontend/src/pages/PayResultPage.tsx` 在成功页的 result.guest_next 之后加一个去 `/register` 的 result.guest_register 链接（普通链接，照 P07-phone-guest），并把文件开头注释里注册页尚未实现的说法改为现状。Stripe 确认中等其他显示 result.guest_next 的状态不加；其余未改。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.13 逐字加入 auth.register_title（auth.same_flow_note 之前）、auth.register_demo_hint（auth.login_demo_hint 之后）与 result.guest_register（result.guest_next 之后），每种语言一行。用到的 auth.same_flow_note、auth.verified_*、auth.sms_paused、auth.continue_guest、common.nav_login 与 common.error_retry 字典里已有。
- [x] `frontend/src/styles/site.css`（只写布局，未改 `acuven-shop.css`）：
  - 新增 `.site-register` 一段，桌面照 P11-desktop：居中的 520px 卡片（页面上 64px、下 96px，卡片内边距 32px），各块间距 16px；V1 的发送按钮占满卡片宽度（视觉稿为 `acs-btn--block`，以 `.site-register .site-sms__send` 限定在本页）；common.nav_login 居中、上方 8px。767px 及以下照 P11-phone：不绘制卡片（display: contents），内容区上 28px、左右 16px、下 40px，间距 16px；V1 的验证码输入与验证按钮分行（间距 16px，按钮占满宽度），重发与 common.nav_login 居中。
  - 结果页加 `.site-result__register { justify-self: start; }`（链接靠左，不占满一行）。
- [x] 测试：
  - `frontend/src/pages/RegisterPage.test.tsx`（vitest 与 react-dom/server，以 renderToStaticMarkup 渲染 `RegisterView` 与经 `App` 打开 `/register`，直接调用导出的函数，不模拟点击；fetch 以参数带类型的 `vi.fn((input, init) => …)` 替身代替，计时以替身代替）：经路由打开 `/register` 在前台框架里、内容区 aria-busy、common.nav_login 指向 `/login`；注册页与首页的页头没有注册入口；卡片内容与顺序；读取中只有四段固定文字；读取失败只有 common.error_retry；站点设置只读一次；`registerVerify` 交给 V1 的用途 register、开关、公开 key 与 continue_guest 的去向（来源 `/checkout`、`/cart` 与没有来源）；开关开启时为注册 / 短信登录的 V1；开关关闭时内容区只有 auth.sms_paused 与 auth.continue_guest、没有 auth.sms_scope 与 auth.claim_notice；按 created 的两种成功文案与跳转去向（来源为前台页、后台页、登录页与没有来源）；重设结果不理会；成功提示的位置；离开页面后不更新、计时期间离开不跳转；三种语言下各状态的文字全部来自字典（+60、+65 除外）。每条测试的注释写明守住的验收、UX、设计或视觉稿的哪一句。
  - `frontend/src/router.test.tsx`：只改路由表断言——表里加上 `/register`，原来断言它不在表里的一行改为在表里。
  - `frontend/src/pages/LoginPage.test.tsx`：只改「renders common.nav_register only when the register page exists」一条——`registerPath()` 改为断言 `/register`，链接改以它为去向（原以 `/privacy` 代替）；`registerTo` 为 null 时不渲染的一侧保留。
  - `frontend/src/pages/ForgotPasswordPage.test.tsx`：只改「shows auth.reset_not_registered and the register link only when the page exists」一条——同上改为断言 `/register`，链接指向 `/register`；不渲染的一侧改以 `registerTo` 为 null 守住。
  - `frontend/src/pages/PayResultPage.test.tsx`：只改「has no register, order detail or points」一条（改名为「has the register link but no order detail or points」）——原来断言 `/register` 不在路由表，注册页进表后不再成立；改为断言成功页恰有去 `/register` 与去 `/products` 两个链接、注册链接紧跟 result.guest_next、仍没有 result.track 与获得积分，并断言 Stripe 确认中（也显示 result.guest_next）没有注册链接。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 成功后的去向：UX P11 写「P13（注册或登录成功…）；来源页」，P13 尚未实现，按验收复用 `verifiedPath`，没有来源时去首页 P01。成功文案 auth.verified_registered 里提到在会员中心设置密码，UX 的 `[account.password_none]` 属于 P13，本页不显示。
  - auth.continue_guest 的去向：UX P11 写「P05 或 P01」，按验收取 `guestPath`（来源为 `/checkout` 时回结账，否则首页）。
  - 读取失败时内容区只显示 common.error_retry，没有 V1，因此也没有 auth.continue_guest（与 SHOP-TASK-146 短信页签的写法一致）。
  - 开关关闭时 auth.same_flow_note 仍显示（UX 只说 V1 换成 auth.sms_paused、不显示 auth.sms_scope 与 auth.claim_notice）。
  - V1 的布局在本页按视觉稿调整（桌面发送按钮占满宽度；手机验证码与验证按钮分行、重发居中），以 `.site-register` 限定，`SmsVerify.tsx` 与 V1 段的样式未改。其他页嵌入的 V1 不受影响。
  - 视觉稿里托管挑战占位的虚线框、底色、圆角与文字颜色，以及手机稿挑战完成后的对勾颜色，site.css 不搬（挑战由托管服务渲染，同 SHOP-TASK-145）；common.nav_login 与 result.guest_register 是普通链接，没有写颜色。没有其他 site.css 写不了的颜色、字号、字重或底色（卡片、提示与按钮的样式都来自设计系统类）。
  - 视觉稿的国家码下拉带英文读屏标签「Country code」，UX-COPY 没有这一条；V1 以 auth.phone 的标签作读屏名称（SHOP-TASK-145 的写法），不自编文案。P11-phone 画的是已选 +65、已发送验证码的状态，下拉只画了一项；页面仍是 +60、+65 两项。
  - 验证通过的提示组件在本页另写一份（与登录页的 `VerifiedNotice` 相同的标记）：登录页没有导出它，本任务不改 `LoginPage.tsx`。
  - 页面不查询是否已登录：已登录的会员打开 `/register` 仍看到注册卡片；登录后的页头切换与会员中心由之后的会员中心任务接入。
- 改动只在 `frontend/src/pages/RegisterPage.tsx`、`frontend/src/pages/RegisterPage.test.tsx`、`frontend/src/pages/LoginPage.test.tsx`、`frontend/src/pages/ForgotPasswordPage.test.tsx`、`frontend/src/pages/PayResultPage.tsx`、`frontend/src/pages/PayResultPage.test.tsx`、`frontend/src/router.tsx`、`frontend/src/router.test.tsx`、`frontend/src/App.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css` 与本文件；未改 `frontend/src/components/SmsVerify.tsx`、`frontend/src/components/SiteFrame.tsx`、`frontend/src/api/`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、docs/UX.md 0.12 的 P11、P07 与 V1、UX-COPY 0.13、HANDOFF 0.58、视觉稿 P11-desktop、P11-phone、V1-verified 与 P07-phone-guest，以及 `frontend/src/components/SmsVerify.tsx`、`frontend/src/pages/LoginPage.tsx` 自查；另查看了按路由表遍历每页的现有测试（`App.test.tsx`、`SiteFrame.test.tsx`）与 `acuven-shop.test.ts` 对 site.css 的规则对 `/register` 的要求。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收，未对真实 Turnstile 与后端接口验证。

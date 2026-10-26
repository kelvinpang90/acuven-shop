# SHOP-TASK-153 页头的登录入口按会员会话切换为会员中心

设计闸门：DESIGN 1.13（提交 450c2fa）「权限与资料保护」第 3 条（会话到期、退出与服务端授权检查；页头显示不是授权依据）与 HANDOFF 0.61。依据 docs/UX.md 0.13 的「全局框架」（页头与 ☰ 菜单为 `common.nav_login` 或 `common.nav_account`）与 P13 入口（页头「My account」；P11、P12 登录成功），docs/UX-COPY.md 0.14；样式参照视觉稿 `docs/design/pages/P13-desktop.html` 的页头。会话状态经 SHOP-TASK-151（`api/memberSession.ts`）。

- [x] 切换规则（`frontend/src/components/SiteFrame.tsx`）：`SiteHeader` 以 `useMemberSession()` 取会话状态。`HEADER_NAV` 只留商品与查询订单，原来的登录项改为二选一的最后一项：状态为 member 时是指向 `/account` 的 `common.nav_account`（`ACCOUNT_NAV`），unknown、guest 与 failed 时是原来指向 `/login` 的 `common.nav_login`（`LOGIN_NAV`）。两个路径各自仍经 `availableNav` 按路由表（`isRoutePath`）判断是否渲染；桌面导航行与手机 ☰ 菜单共用同一份 `nav`，所以两处同时切换，在 `/account` 时照其他导航项标 `aria-current="page"`。页头不显示手机号、令牌或其他会话内容。
- [x] 首次渲染：`useMemberSession` 的服务端快照取读取对象当前的状态，本次页面加载的默认读取为 unknown，所以服务端渲染与首次水合仍是登录入口，输出与之前相同；挂载后由 hook 触发首次读取，读到 member 时再切换。`frontend/src/components/SiteFrame.test.tsx` 与 `frontend/src/App.test.tsx` 现有断言未改。
- [x] 挂载（`frontend/src/App.tsx`）：照 `WhatsAppContactProvider` 的写法在其内、`RouterProvider` 之外挂 `MemberSessionProvider`；`AppProps` 新增可选的 `memberSession`（`MemberSessionReader`），测试传入替身，浏览器里不传时用 `api/memberSession.ts` 本次页面加载唯一的读取。只有前台页头（`SiteFrame`）调用 `useMemberSession` 触发读取；后台页不套前台框架，不因此发请求。
- [x] 重读时机：
  - `frontend/src/pages/LoginPage.tsx`：`LoginMoves` 新增 `reloadSession`。`submitPassword` 在回答为 ok 时先调用一次（在 signal 检查之前），其余回答（login_failed、phone_invalid、unavailable、failed、network）不调用；`finishSms`（短信验证通过）开头调用一次。页面传入 `useMemberSessionReader()` 取得的读取对象的 `reload`，以 `void` 发出、不等待结果；成功文案、计时与去向不变。
  - `frontend/src/pages/RegisterPage.tsx`：`RegisterMoves` 新增 `reloadSession`；`finishRegister` 只在结果为 member（验证通过）时调用一次，在 signal 检查之前；重设密码用途的两种结果照旧忽略、不重读。页面同样传入读取对象的 `reload`。
  - 已离开页面（signal 已中止）时仍重读：服务端已发出会话，重读的是共享读取，不带页面的 signal，页面本身照旧不更新、不跳转。
  - 退出后的重读已由 SHOP-TASK-152 完成，未改 `AccountPage.tsx`。
- [x] 代码结构：沿用两页现有写法（导出的纯函数与异步函数、只依赖 props 的 View，重读经 moves 回调传入）；传给事件属性的处理函数都不返回 Promise。`router.tsx`、`copy.ts`（`common.nav_account` 已由 SHOP-TASK-152 加入）与 `site.css` 不需要改，未改。
- [x] 测试（vitest 与 react-dom/server，不加测试依赖；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`）：
  - `frontend/src/components/SiteFrame.test.tsx` 新增 describe「member session entry」：`settledSession` 照 `settledReader` 以替身 fetch 先读完一次会话（只发一次 `GET /api/member/session`），`render` 新增可选的会话读取参数。断言 member 时桌面导航与手机菜单（三语）都是商品、查询订单之后去 `/account` 的 `common.nav_account`，页头没有 `common.nav_login` 与 `/login`；页头没有遮掩手机号与令牌（每个前台页）；在 `/account` 时两处都标 `aria-current`；guest（401 member_session_required）、failed（500、网络中断）与未读时两处仍是去 `/login` 的 `common.nav_login`，没有 `common.nav_account` 与 `/account`；不传读取时页头与传入未读的读取完全相同。
  - `frontend/src/pages/LoginPage.test.tsx` 新增 describe「session reload」：密码登录 ok 恰重读一次且事件不变；五种失败都不重读；短信验证通过（created 真假）恰重读一次且事件不变；已离开页面时仍重读一次、页面不更新。
  - `frontend/src/pages/RegisterPage.test.tsx` 新增 describe「session reload」：验证通过（created 真假）恰重读一次且事件不变；没有登录的两种结果不重读；已离开页面时仍重读一次、页面不更新。
  - 每条测试的注释写明守住的是 UX、验收或实现选择的哪一句；页面文字仍全部来自字典（两页原有的 page text 测试照旧覆盖）。
- [x] 现有测试只在必要时修改：`LoginPage.test.tsx` 与 `RegisterPage.test.tsx` 的 `fakeMoves` 因 `LoginMoves`、`RegisterMoves` 新增必填的 `reloadSession`（类型检查要求）而加入该回调，另行计数、不记进 events，所以现有对 events 的断言都不变；`SiteFrame.test.tsx` 的 `render` 新增可选参数，现有调用不变。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 入口位置：视觉稿 P13-desktop 把「My account」画在导航行右侧、购物车之前；按验收不挪位置，仍在左侧导航的最后（查询订单之后），手机仍在 ☰ 菜单里隐私说明之前。
  - 视觉稿里 site.css 写不了的样式不搬（当前项的颜色、字重等），未借用其他组件的类取颜色；本任务没有新的布局，未改 site.css。
  - 已离开页面时仍重读会话是实现选择（见上），验收未写明这一情形。
  - UX 与视觉稿在本任务范围内的冲突只有上面的入口位置，按 UX 与验收处理，PR 里另作说明。
- 留给后续任务：结账页 P05 短信验证通过后的重读会话留给会员结账任务。
- 改动只在 `frontend/src/components/SiteFrame.tsx`、`frontend/src/components/SiteFrame.test.tsx`、`frontend/src/App.tsx`、`frontend/src/pages/LoginPage.tsx`、`frontend/src/pages/LoginPage.test.tsx`、`frontend/src/pages/RegisterPage.tsx`、`frontend/src/pages/RegisterPage.test.tsx` 与本文件；未改 `frontend/src/api/`、`AccountPage.tsx`、`router.tsx`、`copy.ts`、`site.css`、`acuven-shop.css`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准、UX「全局框架」与 P13 入口、HANDOFF 0.61 与 `api/memberSession.ts` 的状态归类自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（挂载后读取会话、登录成功后页头切换的实际观感都未在浏览器里看过），未对真实后端接口验证。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 内容与文案以 docs/UX.md 0.13 的「全局框架」与 P13 入口、docs/UX-COPY.md 0.14 与 docs/HANDOFF.md 0.61 为准，样式以 docs/design/pages/ 的 P13-desktop 页头为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 不需要新键（common.nav_account 已由 SHOP-TASK-152 加入）
      - 页头切换——桌面导航行与手机 ☰ 菜单里现在的 /login 那一项按会话状态显示：member 时为指向 /account 的 common.nav_account（在 /account 时标 aria-current），unknown、guest 与 failed 时为现在的 common.nav_login；位置与现在的登录项相同（视觉稿把它画在导航行右侧购物车之前，不挪位置，记入任务记录的偏离）；两个路径各自仍按路由表判断是否渲染；页头不显示手机号或其他会话内容；服务端渲染与首次水合时状态为 unknown，输出与现在相同（frontend/src/components/SiteFrame.test.tsx 与 frontend/src/App.test.tsx 现有断言不变）
      - 挂载——frontend/src/App.tsx 挂载 SHOP-TASK-151 的 Provider，写法照 WhatsAppContactProvider（App 的 props 另有可选的会话读取对象，测试传入替身，浏览器里不传时用默认读取）；只有前台页头读取会话，后台页不因此发请求
      - 登录与注册成功——frontend/src/pages/LoginPage.tsx 的密码登录 ok 与短信验证通过、frontend/src/pages/RegisterPage.tsx 的验证通过之后，经 SHOP-TASK-151 取得的读取对象要求重读一次会话（不等待结果，不改变现有的成功文案、计时与去向）；提交失败时不重读；不改 AccountPage.tsx
      - 代码结构——LoginPage.tsx 与 RegisterPage.tsx 沿用现有的写法（导出的纯函数与异步函数、只依赖 props 的 View；重读经回调传入，便于测试），SiteFrame.tsx 按现有写法以 hook 取会话状态；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）验证意图——frontend/src/components/SiteFrame.test.tsx 以会话读取的替身（照该文件 settledReader 的写法先读完一次）断言 member 时桌面导航与手机菜单都是去 /account 的 common.nav_account、没有 common.nav_login，guest、failed 与未读时仍为 common.nav_login，在 /account 页面标 aria-current；frontend/src/pages/LoginPage.test.tsx 与 frontend/src/pages/RegisterPage.test.tsx 断言成功后要求重读会话恰一次、失败时不要求；页面文字全部来自字典；每条测试写明它守住的是 UX、设计或 Kelvin 决定的哪一句；fetch 替身写成参数带类型的 vi.fn((input, init) => …)，不让 any 流出；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）；现有测试只在必要时修改并写明原因
      - docs/task-records/SHOP-TASK-153.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；切换规则、挂载、重读时机、偏离（含入口位置）、验证到什么程度，只写实际存在的测试，写明未做浏览器验收，并写明结账页 P05 短信验证通过后的重读留给会员结账任务）
      - 改动只落在 allowed_change_paths（不改 frontend/src/api/ 与 frontend/src/pages/AccountPage.tsx；router.tsx、copy.ts 与 site.css 不需要时不改）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门写明 DESIGN 1.13 提交 450c2fa 与 HANDOFF 0.61），不合并、不直推
```

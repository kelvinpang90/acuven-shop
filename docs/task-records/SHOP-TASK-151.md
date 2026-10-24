# SHOP-TASK-151 会员会话的请求模块与全站共享的会话状态

设计闸门：DESIGN 1.13（提交 450c2fa）「权限与资料保护」第 3、5 条（会话到期、退出与服务端授权检查；会话 cookie 不交给页面脚本，写操作另须 CSRF 令牌）与 HANDOFF 0.61。依据 docs/UX.md 0.13 的 P13（页头「My account」入口、遮掩后的手机号与退出登录）。会员前端第二批的请求层之三，调用已有的 GET /api/member/session 与 POST /api/member/logout（`app/api/member_auth.py`，SHOP-TASK-076）。

- [x] 新文件 `frontend/src/api/memberSession.ts`，写法沿用 `frontend/src/api/memberAuth.ts`：同源 cookie、cache 为 no-store、Accept 为 application/json，不带查询参数；`CSRF_HEADER` 取自 `pay.ts`。CSRF 令牌与遮掩后的手机号只在内存里（读取对象的模块变量与 React 状态），模块不写日志、不碰浏览器存储，也不放进地址。
- [x] 两个请求（导出地址常量 `MEMBER_SESSION_URL`、`MEMBER_LOGOUT_URL`）：
  - `readMemberSession(signal?)`：GET，没有请求体。ok（200 且响应体合格，取 `phoneMasked`、`hasPassword`、`csrfToken`，不取 expires_at）、none（401 member_session_required）、failed（其他，含 200 而响应体不合格、不是 JSON、401 错误码不符）、network（网络中断与中止）。响应体的检查为导出的纯函数 `parseMemberSession`：phone_masked 与 csrf_token 为非空字符串、has_password 为布尔，否则为 null。
  - `memberLogout(csrfToken, signal?)`：POST，请求体为空，请求头只有 Accept 与 X-CSRF-Token。ok（204）、signed_out（401）、csrf（403 csrf_failed，服务端此时不撤销会话）、failed（其他，含 403 错误码不符）、network。
- [x] 共享状态（照 `siteSettings.ts` 的读取对象与 Provider、`cart.ts` 的 listeners 与 `useSyncExternalStore`）：
  - `MemberSessionState`：unknown（尚未读完）、member（带最近一次 ok 的三项）、guest（none）、failed（failed 或 network）。
  - `createMemberSessionReader()` 返回 `current`、`load`、`reload`、`subscribe`。`load` 只在第一次请求，之后返回同一个 Promise；`reload` 每次都发新请求，读完之前 `current` 保持原状态。
  - 排序：`load` 与 `reload` 共用一个发出序号，回答到达时只有序号大于已采用的才采用，先发而后到的旧回答不覆盖后发的。两者都返回自己的回答处理之后的当前状态（旧回答被丢弃时返回的是后发请求已采用的状态）。
  - 新状态与当前状态相同（种类相同，member 时三项也相同）时保留原对象、不通知订阅者；改变时替换并通知全部订阅者。
  - 本次页面加载唯一的默认读取；`MemberSessionProvider` 以 Context 提供，可传入替身，没有 Provider 时用默认读取。
  - `useMemberSession()`：`useSyncExternalStore` 的客户端与服务端快照都取读取对象的 `current`（新建或默认读取为 unknown），挂载后在 effect 里调用 `load`。`useMemberSessionReader()` 返回读取对象，供页面在登录、退出之后调用 `reload`。
- [x] 测试 `frontend/src/api/memberSession.test.ts`（vitest，node 环境；fetch 替身为参数带类型的 `vi.fn((input, init) => …)`，从 `mock.calls` 取值时用 `String(…)`；需要控制回答先后时用排队的替身，由测试决定哪个请求先回答）：
  - 两个请求的方法、地址（不带查询参数）、同源 cookie、no-store、signal、请求头（读取只有 Accept；退出为 Accept 与 X-CSRF-Token）与没有请求体；令牌不进地址。
  - `parseMemberSession`：取三项不取 expires_at；不是对象、缺字段、空串、has_password 不是布尔等归 null。
  - 读取会话与退出各状态码的归类：200、200 响应体不合格与不是 JSON、401 各情形、204、403 各情形、200（退出）、503 与网络中断。
  - 读取对象：四种状态、`load` 只请求一次并返回同一结果、`reload` 每次请求并返回新状态、读完前保持原状态、首次读取后到不覆盖重读、两次重读之间的先后、订阅与退订后的通知、状态不变时返回同一对象且不通知。
  - hook：以 `react-dom/server` 的 `renderToStaticMarkup` 渲染，没有 Provider、新建读取为 unknown，已读完的替身渲染出 member，渲染时不发请求；`useMemberSessionReader` 取到传入的替身或默认读取。
  - 每条测试的注释写明守住的接口记录、UX 的哪一句或 DESIGN 与本任务验收的哪一条。
- 偏离与验收未写死之处的取法（未改设计，未发现须停下的设计问题）：
  - 退出的 401 只看状态码（验收写「401，会话已不在」）；读取会话的 401 须为 member_session_required，错误码不符归 failed（验收写明了错误码）。
  - 中止与网络中断一样归 network，与 `memberAuth.ts` 相同。
  - `load` 与 `reload` 不接收 signal：共享读取不随某个页面离开而中止，旧回答由序号丢弃。
  - 读取对象的接口成员写成函数类型的属性（`current: () => …`），以便直接交给 `useSyncExternalStore`。
  - 「挂载后触发首次读取」需要在浏览器环境挂载组件，测试环境为 node 且不加测试依赖，本任务没有写这条的测试；由 SHOP-TASK-153 挂载后在页头的测试里覆盖。
- 留给后续任务：Provider 的挂载与页头的登录入口切换由 SHOP-TASK-153 完成；页面由 SHOP-TASK-152、154、155 接入。本任务未改 `frontend/src/App.tsx`、页面、页头、路由与 `memberAuth.ts`。
- 改动只在 `frontend/src/api/memberSession.ts`、`frontend/src/api/memberSession.test.ts` 与本文件；未改后端、`docs/TODO.md`、`.platform/`、CI、部署配置、`package.json` 与 `package-lock.json`，未装依赖。
- 验证到什么程度：人工逐条对照验收标准与 `app/api/member_auth.py`（`current_session`、`logout`、`require_member`、`require_member_csrf` 的状态码与错误码）自查。上面列的测试只是写入的测试。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未对真实后端接口验证。

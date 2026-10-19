# SHOP-TASK-050 后台登录页 A01 以邮箱登录

- [x] 按 `docs/UX.md` 0.10 的 A01、`docs/UX-COPY.md` 0.10 与 `docs/HANDOFF.md` 0.37 记录的 Kelvin 2026-10-07 决定（唯一管理员以邮箱登录，登录页标签用 `admin.email`，`admin.username` 停用），把 A01 的登录名输入框改为邮箱：`frontend/src/pages/AdminLoginPage.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/pages/AdminLoginPage.test.tsx`。未改 `router.tsx`、`App.tsx` 与 `site.css`，未改后端、CI、部署配置、`.platform/`、设计或生成的 `styles/acuven-shop.css`，未装依赖（未改 `package.json`、锁文件），未新增路由。设计闸门：不适用（只改输入框的标签与键盘类型，提交的内容与 SHOP-TASK-049 的接口不变；邮箱的存储与处理见 HANDOFF 0.37）。拆分：不拆。
- 输入框：标签由 `admin.username` 改为 `admin.email`；显式写 `type="text"`，加 `inputMode="email"`（手机弹出邮箱键盘），`autoComplete` 仍为 `username`，加 `autoCapitalize="none"` 与 `spellCheck={false}`。不设 `name`、不加 `required` 或格式约束，与之前相同。输入内容原样提交给 `POST /api/admin/login`，去首尾空白与转小写由服务端做（SHOP-TASK-049），请求体字段名仍为 `username`；页面内部的字段名 `LoginFields.username` 未改。
- 字典：从 UX-COPY 原样抄入 `admin.email`（Email ／ 邮箱 ／ E-mel），放在原 `admin.username` 的位置，删除不再使用的 `admin.username`；其余键未动。
- 已有测试的改动（只按验收允许的范围）：「has the username and password fields」一条的登录名标签由 `admin.username` 改为 `admin.email`，登录名输入框的 `type` 由「没有或为 text」改为必须为 `text`（现已显式写出），注释随之改。新增「labels the login name field as email」，注释写明守住的是 HANDOFF 0.37 记录的 Kelvin 2026-10-07 决定「唯一管理员以邮箱登录…登录页标签新增文案 admin.email，admin.username 停用」：每种语言下第一个 `acs-admin__field` 的标签文字是 `admin.email`，其输入框 `type` 为 `text`、`inputmode` 为 `email`、`autocomplete` 为 `username`、`autocapitalize` 为 `none`、`spellcheck` 为 `false`，值原样显示，字典里没有 `admin.username`。其余断言未动（提交期间禁用、各提示、清空密码框、请求体只有 `username` 与 `password`、凭据不进网址或浏览器存储、语言切换、深浅色、进入 `/admin/orders`、只有字典文字）。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - `admin.login_failed` 的三语文字仍是「用户名或密码不正确」：UX-COPY 0.10 只新增 `admin.email`、停用 `admin.username`，未改这条，按验收其余键不动。
  - 页面与测试里的内部名称（`LoginFields.username`、常量 `USERNAME`、测试标题里的 username）未改名：请求体字段名仍为 `username`，只改界面所见。
- 验证到什么程度：人工逐条对照验收标准、UX 0.10 A01 线框与 HANDOFF 0.37 原句自查。测试以 `react-dom/server` 渲染的标记检查标签与输入框属性，字典与 UX-COPY 的逐字对照由已有的 `i18n/copy.test.ts` 覆盖。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器或手机上打开页面确认邮箱键盘、自动大写与拼写检查的实际表现，未连真实后端以邮箱登录一遍，也未与视觉稿对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 以 docs/UX.md 0.10 的 A01、docs/UX-COPY.md 0.10 与 docs/HANDOFF.md 0.37 为准；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计
      - frontend/src/pages/AdminLoginPage.tsx 的登录名输入框标签由 admin.username 改为 admin.email；输入框为 type="text"，加 inputMode="email"，autocomplete 仍为 username（密码管理按登录名识别），关闭自动大写与拼写检查（autoCapitalize="none"、spellCheck 为 false）；提交内容原样交给 SHOP-TASK-049 的登录接口（规范化由服务端做），请求体字段名仍为 username
      - frontend/src/i18n/copy.ts 从 docs/UX-COPY.md 原样抄入 admin.email，删除不再使用的 admin.username；其余键不动
      - 登录表单的其余行为（提交期间禁用、错误提示、每次提交后清空密码框、凭据不进网址或浏览器存储、语言切换、深浅色、登录后进入 /admin/orders）不变；frontend/src/pages/AdminLoginPage.test.tsx 只改与标签和输入框属性相关的断言，并加一条断言标签为 admin.email、inputMode 为 email、autocomplete 为 username，写明守住的是 Kelvin 2026-10-07 的哪一项决定；其余断言不动
      - 由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）；docs/TODO.md 新增 SHOP-TASK-050 记录段（改动、偏离、验证到什么程度，并写明未做浏览器验收），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；不新增路由；不改生成的 frontend/src/styles/acuven-shop.css；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

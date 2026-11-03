# SHOP-TASK-197 后台未保存修改的离开确认（路由拦截与框架确认框，A04、A10 共用）

- [x] 依据：`docs/UX.md` 0.17 的 A04 与 A10「列表」一段（Kelvin 2026-10-30 决定）、`docs/UX-COPY.md` 0.18、`docs/HANDOFF.md` 0.81 的运营者落实写法与视觉稿 `A10-states` 的未保存确认框。设计闸门：不适用（只做离开前的确认，不碰金额与个人资料；保存与核对仍在服务端）。本任务不装依赖、不改后端、不改设计、不新增路由；没有页面接入（由 SHOP-TASK-199 与 A04 的任务接入）；`frontend/src/App.tsx` 不需要改动，未改。
- [x] 文案：`frontend/src/i18n/copy.ts` 按 UX-COPY 0.18 逐字加入 `admin.unsaved_confirm`、`admin.unsaved_leave` 与 `admin.unsaved_stay`（在 `admin.reload` 之后），写法同相邻条目。没有用到 UX-COPY 以外的界面文字。
- [x] 路由（`frontend/src/router.tsx`）：路由上下文新增 `setLeaveGuard(guard: LeaveGuard | null)`，`LeaveGuard = (proceed: () => void) => void`。拦截只存在内存里。
  - `navigate`：经导出的 `guardedPush(browser, guard, here, next, moveTo)`；设有拦截且目标与当前位置（路径与查询串，`sameLocation`）不同时（`shouldGuard`）先交给拦截函数，拦截函数调用 `proceed` 才 push 并换位置；相同位置照旧只回到顶部、不拦截；没有拦截时与原来相同。
  - `replace`：经导出的 `replaceTo(browser, next, moveTo)`，不经拦截（401 回到登录页等）。
  - 浏览器后退与前进（popstate）：经导出的 `guardedPop(browser, guard, here, moveTo)`；`popGuard` 给出写回的地址（切换前的路径与查询串）与继续时的目标。设有拦截时先以 `pushState` 把地址栏写回本页、页面不换，再交给拦截函数；`proceed` 时以 push 前往 popstate 的目标（历史记录会多一条，HANDOFF 0.81 认可）。没有拦截时照旧换到地址栏的位置。
  - 刷新与关闭标签页：设有拦截期间以导出的 `watchUnload(target, guarded)` 注册 `beforeunload` 监听 `blockUnload`（`preventDefault`，`returnValue` 设为空串），拦截清除后移除；没有拦截时不注册。
  - 路由内部记当前位置的 ref 由只记路径改为记 `RouteLocation`（来源页仍只取其中的路径，行为不变）。
- [x] 框架（`frontend/src/components/AdminFrame.tsx`）：
  - 供内容区接入的 hook：`useUnsavedConfirm(dirty: boolean): (proceed: () => void) => void`，只能在 `AdminFrame` 的内容区里调用（框架外调用抛错）。`dirty` 为页面当前是否有未保存的修改（A04、A10 按 UX 定义：编辑区的值与最近一次读取或保存的不同）。有修改时框架向路由设置拦截，后台导航（桌面导航与手机菜单里的链接都经 `Link` → `navigate`）、浏览器后退与前进都先弹确认框；`dirty` 改为 false 或页面卸载时清除拦截。返回的函数用于页内的切换（另一项的 `common.edit`、`common.create`、手机顶栏的返回）：有修改时弹确认框、选 `admin.unsaved_leave` 才调用传入的回调，没有修改时直接调用。
  - 用法示例（SHOP-TASK-199 与 A04 的任务）：`const confirmLeave = useUnsavedConfirm(isDirty);`，点另一项的编辑时 `confirmLeave(() => { openItem(id); })`。点「已被改动」提示里的 `admin.reload` 是明确放弃修改，不经 `confirmLeave`（Kelvin 2026-10-30）；保存成功或重新读取后让 `isDirty` 变回 false 即清除拦截。
  - `admin.logout`：有未保存的修改时先弹同一个确认框，选离开才退出（`confirmOrProceed(dirty, ask, logOutNow)`）。
  - 确认框的状态变化为导出的纯函数：`LeaveConfirm`（`{ open: false }` 或 `{ open: true; proceed }`）、`CLOSED_CONFIRM`、`askLeave(proceed)`（打开；已打开时以最后一次的回调为准）、`stayHere()`（关闭、不调用回调）、`leaveHere(state)`（关闭并给出要调用的回调）与 `confirmOrProceed(dirty, ask, proceed)`。
  - 确认框 `UnsavedConfirm({ onStay, onLeave })`（导出）：`site-admin__overlay` 里的 `acs-admin__panel`，`role="dialog"`、`aria-modal="true"`，`aria-labelledby` 指向文字 `admin.unsaved_confirm`；按钮依次为 `admin.unsaved_stay`（`acs-admin__btn acs-admin__btn--secondary`，`autoFocus`）与 `admin.unsaved_leave`（`acs-admin__btn`，照视觉稿的主要按钮）；Escape 等同留下。`AdminFrameView` 多一个可省略的 prop `unsaved`（打开时为两个处理，关闭时不给或为 null），确认框渲染在 `acs-admin` 根元素里、内容区之后，同时只有一个。处理函数都不返回 Promise。
- [x] 样式：`frontend/src/styles/site.css` 在后台框架一段之后加入 `.site-admin__overlay`（固定铺满视口、居中、内边距 16px，z-index 同会员中心的确认框）、`.site-admin__confirm`（grid，间距 16px，内边距 20px，至多 440px 宽）、段落去掉外边距与 `.site-admin__confirm-actions`（flex，间距 10px，靠右）。没有声明颜色、字体与圆角，未改 `acuven-shop.css`。
- 偏离与说明：
  - 视觉稿里 site.css 写不了而没有搬入的：确认框的阴影（`box-shadow` 带颜色）。遮罩没有底色（同样因为不声明颜色），只靠铺满视口的透明层挡住下面的点击。
  - 按钮一行加了 `flex-wrap: wrap`，窄屏上马来文按钮放不下时换行；视觉稿没有画出这种情况。
  - 手机菜单里点导航项时菜单先照原来的写法收起，再经路由拦截弹确认框；选留下后菜单保持收起、页面与已填的值不变。
  - 语言切换不是换页，不经拦截（已填的值不受影响）。
  - UX 与视觉稿在本任务的范围内没有冲突。
- [x] 测试（vitest 与 react-dom/server；浏览器以 `router.tsx` 的 `BrowserLike` 替身代替，不模拟点击；每条注明守住的 UX、HANDOFF 0.81 或 Kelvin 决定的句子）：
  - `frontend/src/router.test.tsx` 新增：路由上下文提供 `setLeaveGuard`；有拦截时 `guardedPush` 不换位置、调用继续后才 push；相同位置不拦截、只回到顶部；`replaceTo` 不经拦截；`guardedPop` 先以 pushState 写回切换前的地址、继续后 push 到目标；`popGuard` 写回的地址带查询串、没有拦截或目标即本页时为 null；没有拦截时 navigate 与 popstate 的行为不变；`watchUnload` 只在有拦截时注册、监听调用 `preventDefault` 并把 `returnValue` 设为空串、清除后移除。
  - `frontend/src/components/AdminFrame.test.tsx` 新增：确认框（三种语言）的 class、`role`、`aria-modal`、`aria-labelledby` 与文字，按钮顺序与次要、主要样式，没有打开时不渲染；留下按钮 `autoFocus`、两个按钮与 Escape 的处理；留下不调用回调、离开只调用最后一次的回调；没有修改时直接继续；退出前确认（经 `confirmOrProceed` 与确认框状态函数）及两个退出按钮都交给 `onLogOut`；确认框打开时页面文字全部来自字典。
  - 现有测试的期望未改；`AdminFrame.test.tsx` 只多导入了 `react` 的 `createElement`、`isValidElement` 与类型 `ReactElement`（新增测试用）。`AdminFrameView` 的新 prop 可省略，所以其他页面测试与现有夹具不用改。
  - 没有测试覆盖的：`useUnsavedConfirm` 与 `AdminFrame` 容器里的接线（effect 设置与清除拦截、确认框打开与关闭的 React 状态）——react-dom/server 不执行 effect，也没有加测试依赖；`RouterProvider` 里 popstate 与 beforeunload 监听的注册同样只经上面的导出函数间接验证。
- 验证到什么程度：人工逐条对照 UX A04、A10「列表」一段、UX-COPY 0.18 的三条文案、HANDOFF 0.81 的落实写法与 A10-states 的确认框；上面列出的是本次新增的测试代码，前端的 lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI `frontend` 执行（Worker 沙箱不跑前端检查）。未做浏览器验收（包括确认框的焦点、Escape、浏览器后退与前进被拦下时的地址栏与历史记录、刷新与关闭标签页时浏览器自己的离开提示）。

## 登记时的验收标准（原文）

2026-11-03 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 文案与样式以 docs/UX.md 0.17 的 A04、A10「列表」一段、docs/UX-COPY.md 0.18 与 docs/HANDOFF.md 0.81 为准，确认框样式以 docs/design/pages/A10-states 的未保存确认框为准；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局写在 frontend/src/styles/site.css（不声明颜色、字体、圆角属性，视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色与阴影不搬，记入任务记录的偏离（评审不以此要求修改）；frontend/src/i18n/copy.ts 按 UX-COPY 0.18 逐字加入 admin.unsaved_confirm、admin.unsaved_leave 与 admin.unsaved_stay，写法与相邻条目相同（每种语言一行）
      - 路由——frontend/src/router.tsx 的路由上下文新增设置离开拦截的函数（参数为拦截函数或 null；拦截函数收到一个「继续」回调）；设有拦截时，navigate 换到与当前不同的位置前先交给拦截函数，选继续才换（与当前位置相同时照旧只回到顶部、不拦截）；replace 不拦截；浏览器 popstate 时设有拦截，先以 pushState 把地址栏写回切换前的路径与查询串、页面不换，再交给拦截函数，选继续时以 push 前往 popstate 的目标；设有拦截期间注册 beforeunload 监听（调用 preventDefault 并把 returnValue 设为空串），拦截清除后移除；没有拦截时一切行为与现在相同
      - 框架——frontend/src/components/AdminFrame.tsx 导出供内容区使用的 hook（参数为页面当前是否有未保存的修改，返回「确认后继续」的函数）：有未保存的修改时向路由设置拦截，返回的函数弹出确认框、选离开才调用传入的回调；没有未保存的修改时不设拦截、返回的函数直接调用回调；页面卸载或改为没有修改时清除拦截；admin.logout 在有未保存的修改时先弹同一个确认框，选离开才退出；手机菜单里的导航经同一路由拦截
      - 确认框——框架内渲染一个确认框（acs-admin__panel，role dialog，aria-modal true，以 aria-labelledby 指向文字 admin.unsaved_confirm），按钮依次为 admin.unsaved_stay（次要按钮，打开时取得焦点）与 admin.unsaved_leave（主要按钮）；留下关闭确认框、不调用回调、已填的值不变；离开关闭确认框后调用回调；按 Escape 等同留下；同时只有一个确认框，打开期间再次触发以最后一次的回调为准
      - 代码结构——把拦截的判定（是否拦截、popstate 时写回的地址与继续时的目标）与确认框的状态变化写成导出的函数，View 只依赖 props；传给事件属性的处理函数不返回 Promise；排版（换行、空行、一行写多长）不在验收范围内，前端没有格式化工具，评审不以此要求修改
      - 测试——用 vitest 与 react-dom/server（不加测试依赖；浏览器以 router.tsx 现有的 BrowserLike 替身代替，不模拟点击）验证意图：router.test.tsx 的有拦截时 navigate 不换位置、继续后才换、相同位置不拦截、replace 不拦截、popstate 写回切换前的地址且继续后 push 到目标、没有拦截时行为不变、beforeunload 只在有拦截时阻止；AdminFrame.test.tsx 的确认框文字与按钮顺序、留下与离开、Escape、没有修改时直接继续、退出前确认；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - 现有测试只在必要时修改并写明原因（路由上下文多一个函数，替身与夹具只补这一项）；导航、语言切换、会话读取与退出的现有期望不变
      - docs/task-records/SHOP-TASK-197.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；导出的 hook 与函数的签名和用法，供 199 与 A04 的任务接入；偏离与验证到什么程度，只写实际存在的测试，写明未做浏览器验收）；改动只落在 allowed_change_paths（不新增路由；App.tsx 按页面任务的固定写法列入，不需要时不改）；不改页面；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖；不改后端；以 Draft PR 交付（设计闸门：不适用，理由同上），不合并、不直推
```

# SHOP-TASK-033 框架测试按路由表推算尚未实现的页面

- [x] 把 `frontend/src/components/SiteFrame.test.tsx` 里「renders no navigation item for pages that do not exist yet」与「has no WhatsApp button, placeholder or contact form」两条测试由写死「哪些页面还没做」改为按路由表推算，之后的页面任务（结账、模拟支付、订单查询、退款申请等）不必为这两条测试改这个文件。只改这一个测试文件与本记录，未改页面与源码、其他测试、`package.json` 与锁文件、CI 或部署配置。设计闸门：不适用（只改测试，不碰金额、个人资料与业务代码）。
- 推算规则（辅助代码与两条测试同在该文件，测试上方注释写明规则、依据与改动原因）：
  - 导航：表 `PENDING_PAGES` 列出尚未实现的页面——查询订单 `/track`（`common.nav_track`）、登录 `/login`（`common.nav_login`）、会员中心 `/account`、注册 `/register`。`pendingNavigationFindings(html, language, isImplemented)` 只对 `isImplemented` 为假的项检查：有导航文字的项不出现 `>导航文字<`，所有项都不出现 `href="<路径>` 开头的链接（与原正则一样按前缀匹配）。测试传入 `router.tsx` 的 `isRoutePath`，对路由表每个路径、每种语言断言结果为空；某路径一进路由表即自动不再断言。依据：SHOP-TASK-014 验收的「尚未实现页面的导航项不渲染」。
  - 表单：表 `PAGE_FORM_PATHS` 列出线框里页面主体有填写或选择后提交的表单的页面——结账 `/checkout`、模拟支付 `/pay`、订单查询 `/track`、退款申请 `/track/order/refund`（查单模式）与 `/account/order/refund`（会员模式）、登录与忘记密码 `/login`、`/forgot-password`、注册 `/register`、会员中心 `/account`；这些页面主体的表单由各自页面的测试负责。商品列表 `/products` 的筛选控件不在表单里，不进表。`contactFormFindings(html, path)` 把页面切成框架（根元素开标签、演示横幅与页头即 `<header` 之前的部分，页头去掉 `role="search"` 的搜索表单，最后一个 `<footer` 起的页脚与收尾）与页面主体（`</header>` 与页脚之间）：框架对每页断言没有 `form` 或 `textarea`；主体只对路径不在表里的页面断言；不含 `whatsapp`（不区分大小写）与不含 `{{` 对整页、所有页面断言。依据：`docs/UX.md`「全局框架」页脚一条的「不设站内联系表单」；WhatsApp 两项依据 `docs/UX.md` Q10。
  - 新增两条用例证明推算本身：导航用替身判断（全部未实现、把 `/track` 视为已实现、全部已实现），被判为已实现的项不再出现在结果里，其余项照常出现；表单用自建的页面片段，表里每个路径的主体表单不报、`/`、`/products`、`/privacy` 的主体表单报出，页脚里的表单与 whatsapp、`{{` 在表里的页面上也报出，并断言 `/products` 不在表里。
- 改前与改后对照（当前路由表 `/`、`/products`、`/products/:slug`、`/cart`、`/privacy`）：
  - 导航：改前每个路径、每种语言断言不含 `common.nav_track`、`common.nav_login` 的 `>文字<`，且不匹配 `href="/(track|login|account|register)`；改后四个路径都不在路由表，检查的是同样两个导航文字与同样四个 href 前缀。
  - 表单：改前每个路径断言整页不含 whatsapp、`{{`，并在整页去掉 `role="search"` 表单后不含 `form`、`textarea`；改后整页的 whatsapp、`{{` 不变，五个路径都不在 `PAGE_FORM_PATHS`，所以框架与主体都要求没有 `form`、`textarea`，两部分合起来覆盖整页。
- 偏离与取舍，请审阅：
  - 搜索表单只在页头里去掉，不再在整页去掉：主体或页脚里若出现 `role="search"` 的表单，改后会被报出（改前不会）。当前页面没有这种表单，所以在当前路由表下检查内容不变；这只比改前更严，未放宽。
  - 表单表按路由表里的路径逐字比较（不按前缀）：例如以后若用 `/pay/:id` 这样的模式，需要由那个任务在表里写入该模式；导航表按前缀匹配 href，所以 `/track` 进了路由表后，`/track/…` 开头的链接也不再由本测试断言。
  - 主体的切分以最后一个 `</header>` 与最后一个 `<footer` 为界；找不到页头或页脚时抛错，测试失败而不是跳过。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对当前路由表下改后两条测试检查的内容与改前相同（见上面的对照）。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 只改 frontend/src/components/SiteFrame.test.tsx 里「renders no navigation item for pages that do not exist yet」与「has no WhatsApp button, placeholder or contact form」两条测试及其辅助代码，这个文件其他测试不动；两条测试上方的注释写明推算规则、依据（SHOP-TASK-014 验收的「尚未实现页面的导航项不渲染」；docs/UX.md「全局框架」页脚一条的「不设站内联系表单」；WhatsApp 两项仍依据 docs/UX.md Q10）与改动原因（避免每个新页面任务都要改这个文件）
      - 导航那条：测试文件里列出尚未实现的页面——查询订单（/track，导航文字 common.nav_track）、登录（/login，common.nav_login）、会员中心（/account）、注册（/register）；每一项只在 router.tsx 的 isRoutePath 对该路径为假时断言每个页面、每种语言都不出现该导航文字（有导航文字的项），也不出现 href 以该路径开头的链接（与现在的正则一样按前缀匹配）；该路径一进路由表即自动不再断言
      - 表单那条：每个页面的演示横幅、页头（去掉 role="search" 的搜索表单后）与页脚里都没有 form 或 textarea；页面主体没有 form 或 textarea 的断言只对不在下面这张表里的页面成立——测试文件里列出线框里页面主体有填写或选择后提交的表单的页面路径：结账（/checkout）、模拟支付（/pay）、订单查询（/track）、退款申请（查单模式 /track/order/refund、会员模式 /account/order/refund）、登录与忘记密码（/login、/forgot-password）、注册（/register）、会员中心（/account），这些页面主体的表单由各自页面的测试负责；已实现的商品列表（/products）的筛选控件不在表单里，不进表，主体断言照常适用；不含 whatsapp（不区分大小写）与不含 {{ 两项断言对所有页面不变
      - 推算写成接收「路径是否已实现」判断的函数，测试实际使用 isRoutePath；另加用例用替身判断（例如把 /track 视为已实现）证明：被判为已实现的项不再断言，其余项照常断言，且检查表里的页面主体不做表单断言、表外页面照常断言
      - 在当前路由表下，改后的两条测试与改前检查的内容相同（不删减、不放宽），PR 正文逐项对照列出改前与改后各检查什么；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-033 记录段（推算规则、两张表的内容与依据、偏离、验证到什么程度）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改 frontend/src 下 SiteFrame.test.tsx 以外的任何文件
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

# SHOP-TASK-014 前端基础：设计变量、三语字典、路由、全站框架与隐私说明页 P14

- [x] 按 `docs/UX.md` 0.7「全局框架」与 P14、`docs/UX-COPY.md` 0.6、`docs/design/`（`COMPONENTS.md` 与 `pages/P14-*.html`、`P01-*.html`）接入设计变量与组件样式，搭好三语字典、语言切换、路由与全站框架，实现隐私说明 P14；首页只有固定位置的 ★ `home.demo_hint`。不调用后端接口，不装依赖，未改 `package.json`、锁文件、`index.html`、`vite.config.ts`、CI 或部署配置。设计闸门：不适用（纯前端展示与静态文案，不处理金额，不收集、存储或展示个人资料）。
- 文件结构（全部在 `frontend/src/`）：
  - `styles/acuven-shop.css`：`docs/design/tokens/acuven-shop.css` 的逐字节副本（含开头的 `@font-face`），不手改；`styles/site.css`：全站布局与显隐，桌面为基础，手机差异只写在 `@media (max-width: 767px)` 里，不用 `acs--phone`，不写颜色、字体或圆角。`main.tsx` 先引入前者再引入后者。
  - `storeDesign.ts`：主题取值的唯一来源（`pandan`、`auto`，不设主色），注明之后由店铺装修任务改为读取 A08 设置。
  - `i18n/copy.ts`：文案字典 `COPY`（本任务用到的 30 个键，英、中、马三列从 UX-COPY 原样抄入）、`formatCopy`（`{变量}` 替换，缺变量抛错）、`translate`、语言列表与品牌字样常量 `BRAND`。
  - `i18n/language.tsx`：语言的读取、保存、`html lang` 设置与 `LanguageProvider` / `useLanguage` / `useCopy`。
  - `router.tsx`：路由表 `ROUTE_PATHS`、`RouterProvider` / `useRouter`、只接受表内路径的 `Link`，以及可单独测试的 `resolvePath`、`pushPath`、`canonicalizeLocation`、`settleLocation`、`isPlainLeftClick`。
  - `components/SiteFrame.tsx`：根元素（`class="acs site"`、`data-shop-theme`、`data-mode`）、演示横幅、页头（含手机 ☰ 菜单与当前语言下拉）、页脚、★ 提示组件 `DemoHint`。
  - `pages/HomePage.tsx`、`pages/PrivacyPage.tsx`；`App.tsx` 组合语言、路由、框架，并以按 `RoutePath` 穷举的页面表选页；`vite-env.d.ts` 引入 `vite/client` 类型。
- 路由规则：用 History 接口，不引入依赖。站内链接拦截普通左键点击、`pushState` 后换页，不整页刷新；带修饰键或非左键的点击交给浏览器；点当前页面的链接不重复记历史；`popstate` 时按地址栏换页，前进后退可用；每次换页（含前进后退与首次进入）后回到页面顶部。路由表只有 `/` 与 `/privacy`；未知路径（含 `/privacy/` 与大小写不同的写法）显示首页，并用 `replaceState` 把地址栏换成 `/`。`Link` 的目标类型就是路由表的路径，页头导航项（商品 `/products`、查询订单 `/track`、登录 `/login`）只在路径进了路由表后才渲染，现在都不渲染。路径与查询参数里不放订单号或电话，也不放语言。
- 语言规则：默认英文；页头语言选项（桌面三个链接，手机为当前语言按钮展开的三项，☰ 菜单里也有）指向当前页面本身，点击只切换语言并以 `acuven-shop.language` 为键写入本浏览器的 `localStorage`；取 `localStorage`、读、写任一步抛错都照常工作（读失败为英文，写失败只是不记住）；只接受 `en`、`zh`、`ms`，其他值（含 `ZH`、`zh-CN`、空串）回退英文。不读 `navigator.language` 或 IP，不写 cookie。`html` 的 `lang` 随当前语言设为 `en`、`zh-Hans`、`ms`（中文用 `zh-Hans`：文案是简体，读屏与字体回退按简体选）；语言选项链接各自带对应的 `lang` 属性。
- 字典校验方式：`i18n/copy.test.ts` 以 Vite 的 `?raw` 读入 `docs/UX-COPY.md`（不 import `node:fs`，不给 `tsconfig.app.json` 下的代码引入 Node 类型），把每行 `` | `键` | 英 | 中 | 马 | 提示 | `` 解析成表，断言字典里每个键在文档中恰好出现一次且三列逐字相同、三列非空且变量同名，并先断言解析出上百个键与已知一行（`common.nav_cart`）正确，防止解析失效时空转通过；变量替换用文档里 `common.nav_cart` 的三列真实模板验证，另测重复变量、数字值与缺变量抛错。`App.test.tsx` 把每个路由在三种语言下的服务端渲染结果拆成文本节点与 `aria-label` 等属性，断言每一段都是当前语言那一列的字典值或 `ACUVEN SHOP`。
- 样式校验方式：`styles/acuven-shop.test.ts` 按字节比较两份 `acuven-shop.css`。这里没有用 `?raw`：vitest 默认不处理 CSS，`.css?raw` 读到空字符串，两份空串比较也会“相同”；改为在测试里以运行时字符串动态取得 `node:fs`、只声明用到的 `readFileSync`，同样不引入 Node 类型，并先断言读到的设计稿含 `@font-face`。同一文件另断言 `main.tsx` 先引入 `acuven-shop.css` 再引入 `site.css`，`site.css`（去掉注释后）没有颜色值、颜色与字体与圆角属性、`acs--phone`，且有 767px 的媒体查询。
- 偏离与取舍，请审阅：
  - UX 与视觉稿冲突，按验收标准以 UX 为准：`docs/design/pages/P14-*.html` 仍是 0.6 以前的 `privacy.fictional`、`privacy.sms` 措辞且没有 `privacy.sms_toggle`，页面按 UX-COPY 0.6 的文案与 UX 0.7 线框（`privacy.sms_toggle` 在短信与日志段、`privacy.logs` 之后）实现，样式沿用相邻段落。
  - 视觉稿画了而本任务按验收标准不渲染的：页头搜索框（去 P02）、商品 / 查询订单 / 登录导航与购物车数量（P02、P08、P12、P04 尚未实现）、页脚与 P14 的 WhatsApp 按钮及 P14「联系」段（配置来源由之后单独登记的任务提供，等同 Q10 的配置缺失时隐藏）。因此字典没有收录 `privacy.h_contact`、`privacy.contact`、`privacy.contact_button`、`common.whatsapp_cta`、`common.nav_cart`、`list.search_placeholder`、`common.search`；`common.nav_shop`、`common.nav_track`、`common.nav_login` 已收录，供页头导航表引用，目前不显示。
  - 视觉稿里有、但 UX-COPY 没有对应键的读屏文字（语言切换的 `aria-label="Language"`、搜索框的 `aria-label`）没有渲染，不自行编写文案；需要时请在 UX-COPY 补键。
  - `site.css` 只放布局与显隐，视觉稿里几处行内的非布局样式没有搬过来：手机页头品牌字号（`calc(18px * var(--display-scale))`）、手机菜单与语言按钮的颜色、去下划线与字号字重、页脚品牌的 `color: var(--on-footer)`、页脚 Privacy 链接的字重、手机版 P14 引言不用 `acs-body-l`。页脚品牌因此用组件默认的 `--ink`：班兰主题浅色下与 `--on-footer` 同值、深色下相近；页脚底色深而 `--ink` 也深的其他主题会看不清，接入店铺装修时需要处理（由组件样式或允许 `site.css` 使用颜色变量）。
  - 手机当前语言下拉用按钮加展开的链接列表实现（`aria-expanded`、`aria-controls`），没有用原生 `select`，以便沿用 `.acs-lang` 的样式；☰ 菜单同理。两者都没有处理 Escape 键与点击外部关闭。
  - 演示横幅的桌面与手机两句都在页面里，由 `site.css` 按宽度只显示其一（`display: none` 的一句读屏也不读）。
  - 路由表的路径在 `router.tsx`，路径到页面组件的对应在 `App.tsx`（按 `RoutePath` 穷举，表里加路径不加页面或反之都过不了类型检查）：页面要用 `SiteFrame.tsx` 的 `DemoHint`，路由模块若再 import 页面会形成循环依赖。
  - 原外壳页测试（`App.test.tsx`「labels the site as a demo」）随外壳页一起替换，其意图（每页都标注演示）由新的 `App.test.tsx` 与 `SiteFrame.test.tsx` 守住。
  - 本机工作树把 `docs/design/tokens/acuven-shop.css` 检出为 CRLF（其他已有文件同样如此，推断是 autocrlf 检出、仓库里存 LF），新写的副本是 LF；提交后两份在仓库与 CI 的 Linux 检出里应同为 LF，逐字节比较以 CI 为准。若在本工作树提交前直接跑前端测试，这一条会因换行不同而失败。
- 验证到什么程度：人工逐条对照验收标准自查，并人工核对字典 30 个键与 UX-COPY 原文、两份 CSS 的行数与颜色值行数一致。测试（vitest 与 `react-dom/server`，未加测试依赖）每条写明它守住的 UX 或验收原句，覆盖：每个路由三种语言下演示横幅都在页头之前、含两句与 DEMO 标签、没有按钮链接等控件；根元素的主题属性、没有 `data-accent`、`acs--phone` 与行内样式；品牌链到首页；未实现页面的导航项与搜索表单不渲染，全部链接只指向路由表里的路径且不带查询参数；三种语言选项与当前语言标记、☰ 按钮的读屏标签、当前语言按钮、菜单里的隐私链接；页脚的演示说明与隐私链接，没有 WhatsApp、占位与表单；路由表恰好两项、未知路径解析与渲染为首页、`replaceState` 换地址、`pushState` 换页、同页不重复记历史、换页后回到顶部、只拦截普通左键；语言读取、保存、非法值回退英文、读写抛错、浏览器语言为中文时仍为英文、按保存的语言渲染、`html lang` 设置函数；P14 三种语言下全部文案按线框顺序出现、四个段落、没有「联系」段文案（从 UX-COPY 取原文反查）与 WhatsApp、正文没有链接按钮表单、没有折叠或隐藏；首页主体只有 ★ 提示且在页头之后；页面文字全部来自字典；字典与文档逐字一致与变量替换；CSS 逐字节相同与 `site.css` 的限制。浏览器相关的部分（实际点击、前进后退、滚动、`localStorage` 与 `document.documentElement.lang` 的真实读写、媒体查询下的显隐）只以可替换浏览器对象的函数做单元测试，effect 本身未在 DOM 中执行；不写 cookie 只经代码检查。lint、类型检查、测试、构建与镜像构建只由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，也未与参考图对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容、流程与文案以 docs/UX.md 0.7 与 docs/UX-COPY.md 0.6 为准（P14 在 0.7 改了 privacy.fictional、privacy.sms 并新增 privacy.sms_toggle），样式以 docs/design/ 为准，二者冲突时以 UX 文档为准并在 PR 里说明；发现需要多显示或多收集个人资料、或需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计
      - frontend/src/styles/acuven-shop.css 与 docs/design/tokens/acuven-shop.css 逐字节相同（不手改生成的文件，含其中的字体引入），由应用入口引入；frontend/src/styles/acuven-shop.test.ts 读取两份文件断言相同
      - 前台根元素带 class acs、data-shop-theme 为 pandan、data-mode 为 auto，不设 data-accent；主题取值集中在 frontend/src/storeDesign.ts 一处，写明之后由店铺装修任务改为读取后台设置；页面结构与类名沿用 docs/design/pages/ 对应的静态页面与 docs/design/COMPONENTS.md，桌面与手机的差异由 frontend/src/styles/site.css 承担（由 frontend/src/main.tsx 在 acuven-shop.css 之后引入；只放布局与显隐规则，手机的差异写在 767px 及以下的媒体查询里；只用设计变量，不写颜色、字体或圆角），不使用只供视觉稿的 acs--phone，页面代码里不写死颜色、字体或圆角
      - frontend/src/i18n/copy.ts 收录本任务用到的每个文案键的英、中、马三列，与 docs/UX-COPY.md 逐字相同；{变量} 由统一的格式化函数替换；frontend/src/i18n/copy.test.ts 读取 docs/UX-COPY.md 的表格，断言字典里每个键都在文档中且三列逐字相同，并验证变量替换；读取文档的方式不得给应用代码引入 Node 类型（沿用 tsconfig.app.json 的约定）；界面上不出现字典以外的文字（品牌字样 ACUVEN SHOP 等 UX「阅读说明」列出的不翻译字样除外）
      - 默认语言英文；访客在页头切换中文或马来文后，选择只保存在本浏览器的 localStorage，读写失败时照常工作（只是不记住）；不按 IP 或浏览器语言自动切换，不写 cookie，不放进网址；html 元素的 lang 属性随当前语言变化
      - frontend/src/router.tsx 用浏览器 History 接口实现路由，不引入依赖：站内链接不整页刷新，前进后退可用，切换页面后回到页面顶部；路由表是唯一来源，指向尚未实现页面的入口（导航项、按钮、链接）一律不渲染；未知路径替换为首页；本任务只有首页与 /privacy 两个路由；路径与查询参数里不出现订单号或电话
      - 全站框架按 docs/UX.md「全局框架」：演示横幅常驻、不可关闭，桌面用 common.demo_banner、手机用 common.demo_banner_short；页头有品牌字样（链到首页）、按路由表可用的导航项与语言切换，手机为 ☰ 菜单（读屏标签 common.nav_menu）与当前语言下拉；页脚有 common.footer_demo 与链到 P14 的 common.nav_privacy；WhatsApp 联系链接的配置来源由之后单独登记的任务提供，本任务不渲染任何 WhatsApp 按钮与隐私页的联系段（等同 UX Q10 的「配置缺失时隐藏」）；不设站内联系表单
      - 首页路由只渲染框架与固定位置的 ★ home.demo_hint；隐私说明 P14 按 docs/UX.md 的 P14 线框渲染标题、★ privacy.demo_hint、引言与四个段落（收集什么、保留多久、谁能看到、短信与日志，后者含 privacy.sms_toggle）及其全部文案，手机上各段默认展开；不提供、不暗示访客删除收货资料的入口
      - 用 vitest 与 react-dom/server（不加测试依赖）验证意图：演示横幅在每个路由都渲染且没有关闭控件；未实现页面的导航项不渲染；未知路径落到首页；语言选择的读取、保存、非法值回退英文与存储读写失败时的行为；P14 渲染全部段落文案且没有联系段、没有 WhatsApp 链接、没有删除资料的入口；页面文字全部来自字典；每条测试写明它守住的是 UX 哪一句；原外壳页的测试随外壳页一起替换；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-014 记录段（文件结构、路由与语言规则、字典校验方式、偏离、验证到什么程度，并写明未做浏览器验收）；不改 planning-v1 块；复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端
      - allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

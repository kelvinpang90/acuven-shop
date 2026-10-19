# SHOP-TASK-054 前台按店铺装修显示

- [x] 按 `docs/REQUIREMENTS.md`「店铺装修」、`docs/UX.md` 0.10 的 P01 与 A08 说明、`docs/HANDOFF.md` 0.38 记录的 Kelvin 2026-10-07 决定（本浏览器记住上次取到的主题、主色与首页区块，不记精选商品）与 `app/services/store_design.py` 模块说明里留给本任务的规则（未挑选或挑选的都不可见时显示最新 4 件），让前台按 SHOP-TASK-045 的 `GET /api/store-design` 显示全站主题与主色、首页四个区块的顺序与显隐、精选商品：`frontend/src/storeDesign.ts`、`frontend/src/api/catalog.ts`、`frontend/src/components/SiteFrame.tsx`、`frontend/src/pages/HomePage.tsx`、`frontend/src/App.tsx`；测试见新文件 `frontend/src/storeDesign.test.ts`，以及 `frontend/src/api/catalog.test.ts`、`frontend/src/pages/HomePage.test.tsx`。未改 `router.tsx`、`i18n/copy.ts` 与 `styles/site.css`（不需要新文案与新布局），未新增路由，未改 `SiteFrame.test.tsx` 等其他测试、后端、`.platform/`、CI、部署配置、设计或生成的 `styles/acuven-shop.css`，未装依赖（未改 `package.json`、锁文件）。不含标志图，不做 A08 页面。设计闸门：不适用（只改前台外观，不涉及金额与个人资料；浏览器里只存主题、主色与区块设置）。拆分：不拆。
- 设置的读取（`storeDesign.ts`）：`StoreDesignProvider` 挂载时请求一次 `GET /api/store-design`（相对地址即同源，`cache: "no-store"`，`credentials: "omit"`，只带 `Accept` 头）。非 2xx、网络错误、响应不是 JSON 或字段不合格都算失败：主题须在 `THEME_ACCENTS` 的 10 款之内（大小写不同也不算），主色须为 `null` 或该主题的可选主色（空串、别的主题的主色、任意颜色都不合格），`home_blocks` 须恰为 hero、how、categories、featured 的一个排列且 `visible` 为布尔值，`featured_slugs` 须为最多 4 个非空字符串；任一项不合格整份不用。提供者的初值是本浏览器记住的设置，没有或不合格时为默认值（`DEFAULT_STORE_DESIGN`：班兰、主色 `null`、四个区块按默认顺序全部显示）；请求成功后换成新设置，失败时主题、主色与区块保持当前显示。没有提供者时（单独渲染的组件、现有测试）取默认值，精选按未挑选处理。深浅色仍为 `data-mode="auto"`，跟随访客设备。
- 本浏览器记住（Kelvin 2026-10-07 决定）：请求成功后把 `{theme, accent, home_blocks}`（与接口同一形状）写入 localStorage 的 `acuven-shop.store-design`，不写精选商品；读取时用同一校验，不合格即忽略。存储的取得、读、写与 `JSON.parse` 都包在 try/catch 里，存储不可用时照常显示，只是不记住。请求失败时不写、不清除已记住的值。
- 主题与主色：`SiteFrame` 根元素的 `data-shop-theme` 取当前主题，主色非 `null` 时加 `data-accent`，为 `null` 时不设（即该主题的默认主色）；样式全部来自生成的 `acuven-shop.css` 已有的 `[data-shop-theme][data-accent]` 规则。常量 `THEME_ACCENTS`（10 款主题按 themes.json 顺序，各自主色按 `accentOptions` 顺序）与默认主题由 `storeDesign.test.ts` 读 `docs/design/tokens/themes.json` 比对。
- 区块：首页按设置的顺序与显隐渲染四个区块，★ `home.demo_hint` 照常在所有区块之前、始终显示；隐藏的区块整块不渲染，也不发它的请求（分类隐藏不取分类；主视觉与精选商品都隐藏时既不按 slug 取，也不取最新 4 件）。
- 精选商品与回退规则（`HomePage.tsx` 的 `featuredSource`）：设置返回之前不取精选（区块为读取中，主视觉图块为占位形状）。`featured_slugs` 非空时用新增的 `featuredBySlugsUrl`（可重复的 `slug` 参数，`sort=newest`、`page=1`、`page_size=4`）取商品卡片，再按 `featured_slugs` 的顺序重排，没返回的 slug 略去、重复的只算一次；`featured_slugs` 为空、设置请求失败（`featuredSlugs` 记为空）或按 slug 的请求成功但一件也没返回时，取现有的 `featuredProductsUrl`（最新 4 件）；按 slug 的请求本身失败（网络中断、非 2xx，含 404）时区块显示现有的 `common.error_retry`，不改取最新 4 件。主视觉的三个图块用同一组商品。`productsUrl` 的参数新增可选的 `slugs`，不给时生成的地址与之前相同。
- 偏离与取舍，请审阅（未改需求、设计与文案）：
  - 提供者只包住前台页（`App.tsx` 的 `CurrentPage` 在 `SiteFrame` 外层），后台页不取装修设置；在前台页之间切换时提供者保留，设置只取一次，但从后台页经站内导航回到前台页时会重新挂载并再取一次。
  - 验收写「没有提供者或请求返回前用本浏览器记住的设置」，同时写「没有提供者时取默认值」；这里按后者，没有提供者时只用默认值、不读存储（现有测试不需要改），有提供者时请求返回前用记住的设置。
  - 请求不带 cookie（`credentials: "omit"`）：接口不读写会话或 cookie（SHOP-TASK-045），「同源」按相对地址理解。
  - `featured_slugs` 的合格条件（最多 4 个非空字符串）是本任务定的：验收只列了主题、主色与区块的条件；接口按 A08 最多返回 4 件。
  - 按 slug 的请求返回 404 也按失败显示错误（列表接口正常不会 404）。
  - 第一次来的访客（没有记住的值）会先看到默认主题，取到设置后再换成店铺的主题（Kelvin 2026-10-07 已接受）；首页精选在设置返回后才开始取，比之前多一次往返。
  - 页脚品牌字样（`<span class="acs-brand">`）仍用组件默认的 `--ink`，视觉稿是行内的 `color: var(--on-footer)`（SHOP-TASK-014 记录的已知偏离）。页脚底色深、`--ink` 也深的主题（如小票、画廊浅色）下会看不清；本任务不能在 `site.css` 写颜色、不能改生成的样式与设计，未处理，需由设计系统组件样式（如 `.acs-footer .acs-brand`）或另行决定解决。
  - 没有用到 UX-COPY 以外的界面文字，`i18n/copy.ts` 未改。
- 已有测试的改动（只按需要）：`HomePage.test.tsx` 因 `storeDesign.ts` 不再导出固定的 `storeDesign`，三处默认区块改取 `DEFAULT_STORE_DESIGN.homeBlocks`（同一份默认值），注释写明原因，断言不变。`SiteFrame.test.tsx`、`App.test.tsx` 与其他测试未改。
- 留给之后的任务：标志图（随图片规则另做）；后台店铺装修 A08 页面；页脚品牌字样在各主题下的颜色（见上）。
- 验证到什么程度：人工逐条对照验收标准、REQUIREMENTS「店铺装修」、UX P01 与 A08、HANDOFF 0.38 原句自查。测试用 vitest 与 `react-dom/server`（未加测试依赖；`fetch` 以 `vi.stubGlobal` 替身，存储以内存替身与读写都抛错的替身），每条注释写明它守住的需求、UX、Kelvin 决定或验收的哪一句，覆盖：常量与 themes.json 的主题、主色 id 与顺序及默认主题一致；默认值；合格响应的取出与各类不合格响应（主题、主色、区块、精选各项）整份不用；记住的键与内容（只有主题、主色与区块，不含精选）、读回、各类不合格的记住值被忽略、存储为空或抛错时照常；首次渲染用记住的设置，没有或不合格时用默认值；请求的地址、方法、`no-store` 与只请求一次；成功时更新并记住；非 2xx、网络中断、非 JSON 与不合格响应时保持当前显示且不动已记住的值、精选按取不到处理；写存储抛错时仍用新设置；根元素在记住的主题与主色下带 `data-accent`、主色为 `null` 时不带，没有提供者、没有记住的值、记住的值不合格、存储抛错时为默认主题且不带 `data-accent`；slug 网址的参数顺序与编码（空格、`&`、`=`、`%`、`+`、中文）以及其他列表请求不带 `slug`；首页按记住的区块顺序与显隐首次渲染、无合格记住值时按默认顺序且精选为读取中；精选按挑选顺序显示且主视觉用同一组商品、重复 slug 只显示一次；为空、取到的一件也没有时显示最新 4 件；按 slug 的请求失败时显示错误且不取最新 4 件；设置返回前不取精选；隐藏的区块不发请求。浏览器相关的部分（effect 里的实际请求与状态更新、localStorage 的真实读写、主题与主色在浏览器里的实际显示与深浅色切换）未在 DOM 中执行，只以纯函数、替身与服务端渲染的各状态验证。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，未连真实后端切换主题、主色、区块与精选走一遍，也未与视觉稿对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 以 docs/REQUIREMENTS.md「店铺装修」、docs/UX.md 0.10 的 P01 与 A08 说明、docs/HANDOFF.md 0.38 与 app/services/store_design.py 模块说明里留给本任务的规则为准；样式沿用现有主题与 docs/design/tokens/acuven-shop.css 的 data-shop-theme 与 data-accent；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性，视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）
      - frontend/src/storeDesign.ts 改为提供店铺装修设置：页面加载时请求一次 GET /api/store-design（同源，cache 为 no-store），响应体不合格算失败；主题须是 10 款主题之一、主色须为 null 或该主题的可选主色、区块须为 hero、how、categories、featured 的一个排列，否则整份视为不合格；常量与 docs/design/tokens/themes.json 一致由测试守住；没有提供者或请求返回前用本浏览器记住的设置，没有或不合格时用现有默认值（班兰、默认主色、四个区块按默认顺序全部显示），深浅色仍跟随访客设备
      - 本浏览器记住（Kelvin 2026-10-07 决定）：请求成功后把主题、主色与区块写入 localStorage 的 acuven-shop.store-design（不写精选商品）；读取时校验，不合格即忽略；所有存储读写包在 try/catch 里，存储不可用时照常显示；请求失败时保持当前显示，不清除已记住的设置
      - 全站框架 frontend/src/components/SiteFrame.tsx 的根元素 data-shop-theme 取当前主题，主色非 null 时加 data-accent，为 null 时不设；没有提供者时取默认值，frontend/src/components/SiteFrame.test.tsx 等现有测试不改并照常通过
      - 首页 frontend/src/pages/HomePage.tsx 按设置的顺序与显隐渲染四个区块（★ home.demo_hint 照常始终显示）；精选商品在设置返回后才取——featured_slugs 非空时经 SHOP-TASK-053 的 slug 参数取商品卡片（frontend/src/api/catalog.ts 新增对应网址函数），按 featured_slugs 的顺序显示返回的商品；featured_slugs 为空、设置请求失败或按 slug 的请求成功但一件也没返回时显示最新 4 件（现有 featuredProductsUrl）；按 slug 的请求本身失败（网络中断或非 2xx）时与现在精选请求失败的处理相同（区块显示现有的错误提示），不再改取最新 4 件；主视觉区块用同一组商品；隐藏的区块不发对应请求
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与存储以替身代替）在 frontend/src/storeDesign.test.ts、frontend/src/api/catalog.test.ts 与 frontend/src/pages/HomePage.test.tsx 验证意图——首次渲染用记住的设置、没有时用默认值；请求成功后更新并记住、不记精选商品；响应或记住的值不合格时忽略；存储抛错时照常显示；data-accent 的有无；区块顺序与显隐；精选按挑选顺序显示、为空或取不到时显示最新 4 件；slug 网址的编码；常量与 themes.json 一致；每条测试写明它守住的是需求、UX 或 Kelvin 决定的哪一句；frontend/src/pages/HomePage.test.tsx 现有断言只按需要改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-054 记录段（设置的读取与记住、主题与主色、区块、精选商品与回退规则、偏离、验证到什么程度，并写明未做浏览器验收、标志图与 A08 页面留给之后的任务）；不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（frontend/src/router.tsx 与 frontend/src/App.tsx 按页面任务的惯例列入，App.tsx 只在需要放置设置提供者时改，不新增路由）；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

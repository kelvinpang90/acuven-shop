# SHOP-TASK-253 前台共享的活动读取、服务端时钟倒计时与框架装饰

- [x] 设计闸门：不适用。只显示公开接口 `GET /api/promotion` 的活动与倒计时，不计算金额，不碰个人资料。依据 UX 0.20「1. 全局框架」「节日促销活动」、DESIGN 1.23「节日促销活动」第 10、12 条、docs/design/COMPONENTS.md「FestivalPromotion」与 HANDOFF 0.97 的落实写法。依赖 SHOP-TASK-252（`fetchPromotion`、`promotionUrl`、`countdownText`）。不改后端、`.platform/`、CI 与部署配置，不装依赖，不改设计。
- [x] 新增 `frontend/src/promotion.tsx`：
  - `createPromotionStore({ fetcher, clock, initial, language })`：不依赖 React 的读取对象（`current`、`subscribe`、`start(language)` 返回停止函数、`clock`），写法照 `api/memberSession.ts` 的读取对象。`start` 在当前结果不属于该语言时读取一次（`promotionUrl(language)`），并为正在显示的活动计时；停止时中止未完成的读取并停掉计时，被中止的读取（成功或失败）不改任何状态。
  - 状态 `{ status: "loading" | "ready", promotion, generation, deadline }`：第一次读取完成前为 loading；读取失败（非 2xx、网络错误）按没有活动处理，不提示、不重试；切换语言重新读取期间保持原活动与 ready，新结果到达后替换；每次读取完成与每次到期 `generation` 加一，被中止的读取不加。
  - 剩余时间：`promotionDeadline(response, receivedAt)` = 收到响应时 `clock()` 的读数 + (`Date.parse(ends_at)` − `Date.parse(server_time)`)，计时只比较 `clock()`（默认 `performance.now`）的读数，不读设备时钟的绝对时间；`ends_at` 为 null 时不计时。读取结果的剩余时间不大于 0 时按没有活动处理、不再读取。剩余到 0 时立即 promotion 置为 null、generation 加一，再重新读取一次；每次等待至多 `MAX_TIMEOUT_MS`（2147483647），到点后按时钟重新计算剩余时间，不足时继续分段等待。
  - `PromotionProvider`（`initial?`、`fetcher?`、`clock?`）：按 `useLanguage()` 的语言开始，语言变化或卸载时停止；经 `useSyncExternalStore` 订阅。传入 `initial` 时首次渲染即为 ready 并用它计时，挂载后（含 StrictMode 的重新挂载）不读取；之后到 0 与切换语言时与不传时相同。
  - `usePromotion()` 返回 `{ status, promotion, generation }`；Provider 之外为 ready、promotion 为 null、generation 0。
  - `PromotionCountdown`：只渲染 `countdownText(language, deadline − clock())` 的文字，没有元素、不设 aria-live；没有活动或不计时时不渲染。由 `watchCountdown(deadline, clock, onTick)` 在剩余时间每跨过一个整秒时更新。
- [x] `frontend/src/components/SiteFrame.tsx`：`.acs` 根元素之内始终渲染 `div.site__promo`，包住演示横幅、页头、页面与页脚；有活动时另加 `acs-promo` 类与 `data-promo-pack`，palette 不为 `theme` 时加 `data-promo-palette`（值为 palette），ambient 为 `corners`、`fall` 时加 `data-promo-ambient`，为 `garland` 时在页头之后放 `<div class="acs-promo-garland" aria-hidden="true">`，为 `none` 时什么都不画。
- [x] `frontend/src/App.tsx`：前台页在 `StoreDesignProvider` 之内加 `PromotionProvider`（在前台页之间切换时不重新挂载，即不再读取）；后台页不加，不读取活动。
- [x] `frontend/src/styles/site.css`：`.site__promo` 与 `.site` 一致的纵向弹性布局（`display: flex; flex-direction: column; flex-grow: 1`），页脚仍贴底，没有活动时外观与 0.19 相同；花边不收缩。没有颜色、字体与圆角声明；图案、配色与飘落动画来自生成的 `acuven-shop.css`（未改）。
- [x] 测试：新增 `frontend/src/promotion.test.tsx`（读取函数与时钟用替身，vitest 假计时器连同 Date）：读取成功、失败（503 与网络错误）按没有活动且不重试、没有活动的回答、语言变化重新读取且期间保持原活动、被中止的读取不改状态、同一语言再开始不重读、generation 逐步计数、订阅通知、剩余时间只由 ends_at 与 server_time 决定且设备时钟前后调动不影响到期时刻、到 0 时立即没有活动并只重新读取一次且新活动随之显示与计时、已到期的结果不再读取、到 0 后重读仍已到期时停止、ends_at 为 null 不计时、超过 setTimeout 上限的分段等待（且 setTimeout 从不收到超过上限的等待）、停止后不计时、initial 的首次渲染与不读取及之后到 0 与换语言的读取、Provider 之外的默认值、倒计时三语文字且没有 aria-live、每秒更新与停止。`frontend/src/components/SiteFrame.test.tsx` 新增「promotion decoration」：corners 与 fall 时包裹元素的类与三种属性、garland 紧接页头且读屏忽略、none 不画、palette 为 theme 时不带 `data-promo-palette`、没有活动时包裹元素只有 `site__promo`、后台页没有包裹元素、没有行内样式。现有断言未改。
- 偏离与落实细节：
  - 前端测试环境没有 DOM（不加测试依赖），组件的副作用在测试里不执行：读取、计时与到期逻辑放在 `createPromotionStore` 里直接测；「倒计时每秒更新」测 `PromotionCountdown` 所用的 `watchCountdown`，组件本身以 `renderToStaticMarkup` 测首次渲染的文字。
  - 倒计时按剩余时间的整秒边界更新（下一次在 `剩余 % 1000 + 1` 毫秒后），而不是固定间隔，文字不会比实际晚近一秒；剩余不足一秒（文字已为 0）后停止，到 0 去掉活动由读取对象负责。
  - 到 0 时若已有读取在进行（如刚切换语言），不另发读取，等它的结果。`ends_at` 或 `server_time` 读不出时间时按 `ends_at` 为 null 处理（不计时）。
  - 前台预览需要的「停在 0、不重新读取」未加，留给预览任务。页头的活动入口留给 SHOP-TASK-257，各页的活动内容留给 SHOP-TASK-254 起的任务。
  - `router.tsx` 与 `copy.ts` 不需要改动，未改。
  - 视觉稿 PROMO-slots、PROMO-packs-light、PROMO-packs-dark 的并排多套主题、浅深色对照与 `acs--phone` 手机框是视觉稿的展示方式，前台不照做；装饰本身全部来自 `acuven-shop.css` 的 `.acs-promo`、`[data-promo-*]` 与 `.acs-promo-garland`，site.css 只补包裹元素的布局，没有写不了的部分。
- 验证到什么程度：人工逐条对照验收标准、HANDOFF 0.97 的落实写法、COMPONENTS「FestivalPromotion」与 `acuven-shop.css` 的 `.acs-promo` 规则；测试里的期望时刻按 `ends_at − server_time` 手算（一小时 = 3 600 000 毫秒，上限加 10 秒的分段）。前端检查与全部测试由 PR 的必需 CI 检查 `frontend` 执行，结果以它的记录为准。
- 交付：以 Draft PR 交付，设计闸门写明不适用及理由；不合并、不直推。

## 登记时的验收标准（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 共享读取——frontend/src/promotion.tsx 导出 PromotionProvider、usePromotion 与 PromotionCountdown，行为照 purpose 的落实写法；在 Provider 之外调用 usePromotion 时返回 ready、promotion 为 null（后台页与测试不受影响）；离开页面或语言变化时中止未完成的读取，旧读取的结果不再更新
      - 倒计时——剩余时间照落实写法计算，与访客设备时钟的绝对值无关；剩余到 0 时立即去掉活动内容并只重新读取一次，新读取返回另一个活动时页面随之更新；长于 setTimeout 上限的等待不会立即触发；传入 initial 时首次渲染即显示该活动、不发读取
      - 框架——frontend/src/components/SiteFrame.tsx 照落实写法渲染包裹元素与花边；装饰元素读屏忽略，不遮挡演示横幅与页头；没有活动时包裹元素不带 acs-promo 类与 data-promo-* 属性、不渲染花边；frontend/src/App.tsx 只在前台页加 PromotionProvider，后台页不读取活动
      - 样式——包裹元素与花边的布局写在 frontend/src/styles/site.css（只写布局、显隐与媒体查询，固定间距与尺寸写像素值，不声明颜色、字体、圆角属性；图案、配色与飘落动画由生成的 acuven-shop.css 提供，不改它）；视觉稿 docs/design/pages/PROMO-slots、PROMO-packs-light、PROMO-packs-dark 里 site.css 写不了的记入任务记录的偏离，评审不以此要求修改
      - 测试验证意图（前端检查与全部测试由 PR 的必需 CI 检查 frontend 执行并全部通过，Worker 沙箱不跑前端检查）——新文件 frontend/src/promotion.test.tsx 用 vitest 与 react-dom（不加测试依赖，读取函数与 performance.now 以替身代替，用 vitest 的假计时器）覆盖读取成功、失败按没有活动、语言变化重新读取、剩余时间不受设备时钟改变影响、到 0 时立即没有活动并重新读取一次、generation 的变化、读取结果已到期时不再读取、initial 的首次渲染、超过 setTimeout 上限的分段等待、倒计时文字每秒更新且没有 aria-live；frontend/src/components/SiteFrame.test.tsx 补充有活动时包裹元素的类与三种属性、garland 与 none、palette 为 theme 时不带 data-promo-palette、没有活动时不带这些属性；现有断言不改
      - docs/task-records/SHOP-TASK-253.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条）；改动只落在 allowed_change_paths（不新增路由；router.tsx 与 copy.ts 按页面任务的惯例列入，不需要时不改）；不改后端、.platform/、CI 与部署配置，不装依赖，不改设计；以 Draft PR 交付（设计闸门写明不适用及理由），不合并、不直推
```

## 登记时的目的（原文）

2026-11-15 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    purpose: >-
      按 docs/UX.md 0.20「1. 全局框架」「节日促销活动」、docs/DESIGN.md 1.23「节日促销活动」第 10、12 条与 docs/design/COMPONENTS.md「FestivalPromotion」，新建 frontend/src/promotion.tsx：前台各页共享的活动读取（PromotionProvider 与 usePromotion）、以服务端时间为准的倒计时（PromotionCountdown），并让 frontend/src/components/SiteFrame.tsx 在活动生效时加上装饰包裹元素与页头花边。页头的活动入口随 P15 路由在 SHOP-TASK-257 加；各页的活动内容见 254 起的任务。不装依赖，不改后端，不改设计。
      运营者落实写法（HANDOFF 0.97）：PromotionProvider 在 frontend/src/App.tsx 里只包前台页（与 StoreDesignProvider 同处），打开页面与切换语言时以 SHOP-TASK-252 的 fetchPromotion 读取一次（可由参数换成替身），读取失败按没有活动处理、不显示提示、不自动重试；切换语言重新读取期间继续显示原来的活动（状态仍为 ready），新结果到达后再替换，被中止的读取不改任何状态；usePromotion() 返回读取状态（loading 或 ready）、promotion 与每次读取完成（成功或失败，不含被中止的）就加一的 generation（供购物车与结账在活动变化时重新计价）。剩余时间＝ends_at 减 server_time，再减去收到响应后本机经过的时间（以 performance.now 计，不读设备时钟的绝对时间）；剩余到 0 时立即按没有活动处理（promotion 置为 null，generation 加一），再重新读取一次；读取返回的活动剩余时间不大于 0 时按没有活动处理、不再读取；等待超过 setTimeout 上限（2147483647 毫秒）时分段等待；ends_at 为 null 时不计时。PromotionProvider 可传入 initial（一次读取的结果，即 PromotionResponse）：传入时一开始就是 ready 并用它计时、挂载后不读取，之后到 0 与切换语言时的行为与不传时相同（前台预览需要的「停在 0、不重新读取」留给预览任务另加）（供页面测试以 renderToStaticMarkup 渲染有活动的状态，A12 的前台预览也可沿用）；不传时照上面的写法读取。PromotionCountdown 只渲染 SHOP-TASK-252 countdownText 的文字，自己每秒更新，不设 aria-live。
      包裹元素始终渲染为 div.site__promo（site.css 只写与 .site 一致的纵向弹性布局，没有活动时页面外观与 0.19 相同，也避免活动开始或结束时页面状态被重建）；活动生效时另加 acs-promo 类与 data-promo-pack，palette 不为 theme 时加 data-promo-palette，ambient 为 corners 或 fall 时加 data-promo-ambient，为 garland 时在页头之后放 div.acs-promo-garland（aria-hidden 为 true），为 none 时什么都不画；包裹元素在 .acs 根元素之内、包住演示横幅、页头、页面与页脚。
      设计闸门：不适用（只显示公开接口的活动与倒计时，不计算金额，不碰个人资料）。
      拆分：前台全局框架一层（9 个允许路径：共享读取与测试、框架与测试，router.tsx、App.tsx、copy.ts 与 site.css 按页面任务的惯例列入），依赖 SHOP-TASK-252。
```

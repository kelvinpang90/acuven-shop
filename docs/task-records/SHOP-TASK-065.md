# SHOP-TASK-065 后台店铺装修的精选商品挑选 A08

- [x] 按 `docs/UX.md` 0.10 的 A08（含「精选商品（0.6）」一条）、`docs/UX-COPY.md` 0.10 与 `docs/HANDOFF.md` 0.40（Kelvin 2026-10-08：原已在精选里、之后下架的商品保存时放行，页面照列、不加标记），在 SHOP-TASK-064 的店铺装修表单上实现精选商品的挑选：最多 4 件、可排序、移出，保存时按列表提交。改动：`frontend/src/pages/AdminStoreDesignPage.tsx`、`pages/AdminStoreDesignPage.test.tsx`、`i18n/copy.ts`、`styles/site.css`、本文件。未改 `router.tsx` 与 `App.tsx`（不新增路由，不改导航），未改 `api/adminStoreDesign.ts`、后端、`.platform/`、CI、部署配置、设计或生成的 `styles/acuven-shop.css`，未装依赖（未改 `package.json`、锁文件）。设计闸门：不适用（只改前台外观设置，不涉及金额计算与个人资料）。拆分：A08 的第四步，预览由 SHOP-TASK-066 接上。
- 列表：首页区块一组之后、保存按钮之前为 `fieldset.acs-admin__panel`（legend `admin.featured_pick`）。表单 `DesignForm` 新增按位置排序的 `featured`，读取（及保存返回）时以返回的 `featured` 重置。按列表顺序每行为序号（`acs-admin__muted`，同首页区块）、名称（`name` 为 null 时显示 slug）与三个次要按钮：上移、下移（`aria-label` 为 `admin.block_move_up`、`admin.block_move_down`，第一行上移与最后一行下移禁用，移动为与相邻一件互换，与首页区块共用同一个互换函数）与移出（`aria-label` 为 `admin.featured_remove`）；按钮内只有 `aria-hidden` 的 16px svg（箭头沿用首页区块，移出为 ✗）。`published` 为 false 的照列，写法与其他行完全相同、不加标记。精选为空时没有行。规则 `moveFeatured`、`removeFeatured`、`featuredOptions`、`addFeatured` 另行导出供测试。
- 添加：行之下为下拉（`select.acs-admin__select`，`aria-label` 为 `admin.featured_add`）与按钮（`acs-admin__btn--secondary`，文字 `admin.featured_add`）。下拉按 `choices` 的顺序列出尚未在精选里的商品名称（值为商品 ID），默认选中第一项；所选的一件加入后（或还没选过时）改选剩下的第一项。按钮把所选商品加到末尾（可挑选的商品都满足 `published()`，加入的一件 `published` 为 true）；已在精选里的不重复加入。已有 4 件（`FEATURED_COUNT`，与请求模块校验的是同一个常量）或没有可添加的商品时下拉与按钮都禁用。其下 `admin.featured_hint`（`acs-admin__muted`）。
- 保存：`featured_product_ids` 按列表当前的顺序提交（取代 SHOP-TASK-064 的原样提交）。200 后以返回的 `featured` 更新列表并照旧显示 `admin.design_saved`，`choices` 与 CSRF 令牌沿用读取的（保存响应不含它们）；上移、下移、移出或添加精选都隐藏 `admin.design_saved`（经 SHOP-TASK-064 的 `editForm`）。403 与切换语言照 SHOP-TASK-064 重新读取，列表与下拉按新结果重置。
- 字典（`i18n/copy.ts`）：从 UX-COPY 原样抄入 `admin.featured_pick`、`admin.featured_add`、`admin.featured_remove`、`admin.featured_hint`，共 4 个键；`admin.block_move_up`、`admin.block_move_down` 已有。未用到 UX-COPY 以外的界面文字。
- 样式（`styles/site.css`，写法沿用首页区块一组）：组内间距 4px；每行三列（序号 24px、名称、按钮，间距 12px，上下内边距 8px），名称可在任意处换行；三个按钮沿用区块的 `site-admin-store-design__moves`（间距 4px）；下拉与按钮同一行可换行（间距 8px，上内边距 8px）；提示沿用 `site-admin-store-design__hint`。767px 及以下：组内间距 2px，每行两列（「序号 名称」、按钮，间距 8px，至少 48px 高）；下拉占满一行，下拉与按钮至少 44px 高。没有颜色、字体或圆角属性。
- 已有测试的改动（只按需要，注释写明原因）：「sends the saved featured ids in their order」（SHOP-TASK-064 守住「精选原样提交」）改为「sends the featured ids in the list order」，因为本任务让精选可以排序、移出与添加，原样提交会丢掉这些改动；「has only the theme, accent and home block groups」改为「has the theme, accent, home block and featured groups」（精选在区块之后、保存之前；标志与预览的断言不变）；「reads and saves only through the store design API module」允许多一行只引入 `FEATURED_COUNT` 的 `api/catalog`；「keeps the demo banner and the demo note」的按钮列表加上精选的按钮与添加按钮；`formOf`、`chooseTheme` 与保存成功三条的期望表单加上 `featured`；保存失败一条保留的 fieldset 由 3 个改为 4 个；dictionary 一条加上精选的各状态，并把商品名称与 slug 列为允许的文字（接口返回的数据，不是界面文案）。其他测试文件未动。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - 视觉稿没有画精选商品；样式沿用同页首页区块一组的写法与设计系统已有的 `acs-admin__panel`、`acs-admin__select`、`acs-admin__btn`（PR 里说明）。
  - 添加一行的次序：UX 线框为「[admin.featured_add] [▾ <已上架商品>]」；验收要求下拉的可访问名称与按钮文字都是 `admin.featured_add`，本页先下拉、后按钮（选好再点），线框里下拉前的那段文字即按钮。
  - 下拉没有占位项（验收「默认选中第一项」）；满 4 件时下拉仍列出未选的商品，只是禁用。
  - 移出不弹确认（UX 未写确认；未保存前可以再加回）。
  - `site.css` 写不了、未搬的视觉：legend 的 600 字重、提示的 13px 字号与行之间的分隔线，同 SHOP-TASK-064 记下的首页区块一组；名称与序号的次要文字、提示用 `acs-admin__muted`，未借用其他组件的类取色。评审不以此要求修改。
- 留给之后的任务：预览（取精选里前两件仍上架的商品，Kelvin 2026-10-08）与桌面两列布局、手机的预览折叠块（SHOP-TASK-066）；标志图（另行登记）；上面列出的字重、字号与分隔线偏离。
- 验证到什么程度：人工逐条对照验收标准、UX A08 线框与「精选商品（0.6）」一条、HANDOFF 0.40 的决定原句自查，人工核对新增的 4 个字典键与 UX-COPY 原文（逐字比较由已有的 `i18n/copy.test.ts` 覆盖）。测试用 vitest 与 `react-dom/server`（未加测试依赖；`fetch` 以按顺序回答的替身，路由以记录 `replace` 并保存当前状态的替身），每条注释写明它守住的 UX、Kelvin 决定或验收的哪一句。`pages/AdminStoreDesignPage.test.tsx` 新增覆盖：精选一组的 legend 与位置（区块之后、保存之前）；行的顺序、序号、名称与 slug 回退、已下架商品照列且写法相同；为空时没有行；上移、下移、移出的读屏名称、svg 与首末禁用（含只有一件时）；移动与移出的规则与移动后的编号；下拉的读屏名称、只列未选的商品、按 choices 顺序、值与默认选中第一项、移出后回到下拉；加到末尾、加入后下拉改选剩下的第一项、不重复加入；满 4 件、没有可加的商品与没有可挑选的商品时下拉与按钮都禁用；保存按列表顺序提交（含已下架的一件、全部移出为空数组），200 后以返回的精选更新列表、choices 不变、显示 `admin.design_saved`；改动精选隐藏 `admin.design_saved`；403 后重新读取并重置列表与下拉；各状态下页面文字只来自字典、序号与商品数据。浏览器相关的部分（真实点击按钮与下拉、键盘操作、媒体查询下的布局）未在 DOM 中执行，只以纯函数、替身驱动的请求流程与服务端渲染的各状态验证；下拉所选项的组件内状态只经服务端渲染的默认选中验证。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器或手机上打开页面，未连真实后端读取与保存）；预览留给 SHOP-TASK-066。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的 A08（含「精选商品（0.6）」一条）、docs/UX-COPY.md 0.10 与 docs/HANDOFF.md 0.40 为准；视觉稿没有画精选商品，样式沿用同页首页区块一组的写法与设计系统已有的 acs-admin__panel、acs-admin__select、acs-admin__btn，在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 首页区块一组之后、保存按钮之前为 legend admin.featured_pick 的一组——按读取到的顺序列出精选商品（含 published 为 false 的，照列、不加标记），每行序号、名称（name 为 null 时显示 slug）、上移与下移（可访问名称 admin.block_move_up、admin.block_move_down，规则同首页区块）与移出（可访问名称 admin.featured_remove），按钮内为 aria-hidden 的图形；精选为空时不显示行
      - 添加——下拉（可访问名称 admin.featured_add）按 choices 的顺序列出尚未在精选里的商品名称，默认选中第一项；按钮 admin.featured_add 把所选商品加到末尾；已有 4 件或没有可添加的商品时下拉与按钮都禁用；其下 admin.featured_hint
      - 保存时 featured_product_ids 按列表当前顺序提交（取代 SHOP-TASK-064 的原样提交）；200 后以返回的 featured 更新列表并照旧显示 admin.design_saved，choices 保持读取到的不变（保存响应不含 choices）；改动精选也隐藏 admin.design_saved；403 与切换语言照 SHOP-TASK-064 重新读取并重置
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/AdminStoreDesignPage.test.tsx 验证意图——行的顺序、名称与 slug 回退、已下架商品照列；移动与首末按钮禁用、移出；下拉只列未选的商品、加到末尾、满 4 件与无可加时禁用；保存提交列表顺序并以返回结果更新；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；SHOP-TASK-064 写的「精选原样提交」断言改为按列表提交并写明原因，其余现有断言只按需要改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-065 记录段（列表、添加、保存、偏离、验证到什么程度，并写明未做浏览器验收、预览留给 SHOP-TASK-066），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（不新增路由，不改导航，不改 frontend/src/api/adminStoreDesign.ts）；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

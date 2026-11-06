# SHOP-TASK-216 前台页头与页脚显示店铺标志图

- [x] `frontend/src/storeDesign.ts`（UX 0.18「店铺装修」、DESIGN 1.20 第 15 条「读取与显示」、HANDOFF 0.84）：
  - `StoreDesignResponse` 与 `StoreDesignValue` 加 `logo`（string 或 null）；`StoreDesign` 未改，`parseStoreDesign`、`rememberDesign` 与 `readRememberedDesign` 记住的形状不变。
  - `parseStoreDesignResponse` 只在 `isStoreLogoPath`（SHOP-TASK-214）为真时取用 logo，null、缺少或其他值都按没有标志处理，不让整份响应作废；`isStoreLogoPath` 挪到它之前，内容未改。
  - `initialStoreDesign` 与没有提供者时的默认值 logo 为 null（打开页面时先显示文字品牌）；`settledStoreDesign` 成功时换成返回的 logo，失败时保持当前值。
  - `StoreDesignContext` 改为导出，供测试直接给出带标志的设置（服务端渲染时读取不会返回）；页面仍经 `StoreDesignProvider`。
- [x] `frontend/src/components/SiteFrame.tsx`：
  - 新增导出的、只依赖 props 的 `StoreBrand`（`logo`、`broken`、`onBroken`）：有标志且未加载失败时为 img（class `site-brand-logo`，src 为 logo，alt 为 `BRAND`，onError 把该路径交给 `onBroken`），否则为文字 `BRAND`。页头的品牌链接（`acs-brand`，去向首页、点击收起菜单不变）与页脚的 `<span class="acs-brand">` 都用它。
  - 新增导出的 `storeLogoBroken(logo, brokenLogo)`：只有失败的正是当前路径才算加载失败。加载失败的路径记在根组件 `SiteFrame` 的状态里，页头与页脚一起退回文字品牌；logo 换成另一个路径时即不算失败，重新尝试显示。
  - 传给事件属性的处理函数都不返回 Promise；其余页头页脚内容未改。
- [x] `frontend/src/styles/site.css` 加 `.site-brand-logo`：桌面高 40px、宽按比例且至多 240px，767px 及以下高 28px、至多 160px，页脚同页头；`object-fit: contain`，不拉伸。不声明颜色、字体与圆角，标志不随主题与深浅色改色。
- [x] `frontend/src/storeDesign.test.ts` 与 `frontend/src/components/SiteFrame.test.tsx` 补测试（见下）；现有测试只为 logo 字段补值。
- `router.tsx`、`App.tsx` 与 `i18n/copy.ts` 不需要改，未改（不新增路由，不新增界面文字，品牌沿用 `BRAND`）。未改后端、设计、`acuven-shop.css`、`docs/TODO.md`、`.platform/`、CI 与部署配置，未装依赖。
- 设计闸门：不适用——只显示公开接口给的图片，不碰金额与个人资料；标志的规则在服务端（DESIGN 1.20 第 15 条）。

## 偏离与取舍，请审阅

- 页头与页脚的品牌元素与 class 都沿用原来的 `acs-brand`，img 直接放在里面；视觉稿给品牌加的 `display: inline-flex; align-items: center` 没有照搬——品牌链接与页脚品牌分别是页头行（flex）与页脚主体（grid）的子项，img 以 `display: block` 显示即可，也免得改变文字品牌的排版。
- 视觉稿里页脚品牌的 `color: var(--on-footer)`（文字品牌在页脚的颜色）是颜色属性，site.css 不写，未搬；标志图本身不受颜色影响。视觉稿里的其余行内样式（页头页脚的间距、搜索与语言等）是现有框架已有的部分，本任务未动。
- `acuven-shop.css` 已有 `.acs-brand img { max-height: 40px }`，与桌面高 40px 一致，手机的 28px 也在其内，未覆盖。
- 为让测试给出带标志的设置，导出了 `storeDesign.ts` 的 `StoreDesignContext`（原为模块内常量）。
- 标志只在读取返回后才在浏览器里渲染（初始值为 null），所以服务端渲染的 HTML 里不会有标志 img，不涉及水合前就加载失败、onError 丢失的情形。

## 验证到什么程度

- 人工逐条对照验收标准、UX 0.18「店铺装修」、DESIGN 1.20 第 15 条、HANDOFF 0.84 与视觉稿 P-logo-header-footer 自查。未做浏览器验收；没有用真实标志在各主题与深色模式下看过，尺寸、比例与加载失败的回退都没有在浏览器里试过，加载失败只在测试里直接调用 img 的 onError 验证。
- `frontend/src/storeDesign.test.ts`（vitest，fetch 以替身代替，直接调用导出的函数）：
  - logo 的解析：合格路径原样取出；null、缺少、其他前缀、绝对地址、大写、空串、数字与对象都按没有标志处理，主题、主色、区块与精选照常取用。
  - 初始值的 logo 为 null（记住的值里混进 logo 也一样）；读取成功时换成返回的 logo，换成另一个路径或 null 时随之更换；读取失败（503）时标志与主题等保持当前值。
  - 记住的值里没有 logo，读回的设置也没有 logo。
  - 现有测试只为 `parseStoreDesignResponse`、`initialStoreDesign`、`settledStoreDesign` 与 `loadStoreDesign` 的期望值补 `logo: null`，`StoreDesignValue` 的字面量补 `logo`。
- `frontend/src/components/SiteFrame.test.tsx`（`renderToStaticMarkup` 渲染，不模拟点击）：
  - `StoreBrand` 三种情形：有标志时为 img（class、src 与 alt）；没有标志与加载失败时为文字 `BRAND`；img 的 onError 把路径交给 onBroken。
  - `storeLogoBroken`：只有失败的正是当前路径才为真，换成另一个路径或 null 时为假。
  - 以 `StoreDesignContext` 给出 logo 渲染全站框架：页头链到首页的品牌链接与页脚品牌都是同一个 img，页面没有文字品牌与行内样式；logo 为 null 时两处都是文字品牌、没有 img。
  - 每条新增测试的注释写明它守住的 UX 0.18「店铺装修」或 HANDOFF 0.84 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建；Worker 沙箱不跑前端检查）。

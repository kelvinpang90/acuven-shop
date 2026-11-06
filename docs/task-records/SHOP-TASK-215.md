# SHOP-TASK-215 后台店铺装修 A08 的标志段（上传替换与移除）

- [x] 新增 `frontend/src/pages/AdminStoreLogo.tsx`：A08 的标志段（UX 0.18 A08、UX-COPY 0.19、HANDOFF 0.84 第（7）点，视觉稿 A08-logo-states、A08-desktop、A08-phone），写法照 `AdminProductImages.tsx`（SHOP-TASK-207）：
  - 导出的纯函数：`fileProblem`（类型与 5,242,880 字节的先查，类型与上限取自 `AdminProductImages.tsx` 的 `IMAGE_TYPES`、`MAX_IMAGE_BYTES`）、`logoBroken`、`uploadDisabled`、`removeDisabled`（没有标志或不可用时禁用）、`startOp`、`askRemove`、`cancelRemove`、`finished`、`uploadStep`、`removeStep`、`rereadNotice`。
  - 接收 `AbortSignal` 与回调（`LogoMoves`）的异步函数：`uploadLogo`、`removeLogo`，经 `api/adminStoreLogo.ts`（SHOP-TASK-214）的 `uploadStoreLogo`（文件本身与它的类型）与 `removeStoreLogo`。
  - 只依赖 props 的 `StoreLogoView`（当前 logo、加载失败与否 `broken`、`LogoState`、`locked` 与处理函数）；`LogoMark`（img 的 src 为 logo、alt 为 `BRAND`，没有标志或加载失败时为文字品牌）由标志段与 A08 预览缩样共用；移除确认框照 SHOP-TASK-197（role dialog、aria-modal、aria-labelledby 指向 `admin.logo_remove_confirm`，按钮依次为 `common.cancel`（autoFocus）与 `admin.logo_remove`，Escape 等同取消）。
  - 容器 `AdminStoreLogo`：自己保留 `LogoState`，同一时间只进行一项操作；`onBusy` 告诉页面进行中与否，卸载时中止请求；传给事件属性的处理函数都不返回 Promise。
- [x] `frontend/src/pages/AdminStoreDesignPage.tsx` 只为接入而改：
  - 标志段放在表单里主色之后、首页区块之前，传入 `base.logo`、`base.csrf_token` 与 `locked`（保存进行中）。
  - 新增导出 `changeLogo`（只换 base 的 logo，给出令牌时一并换）、`hideSaved`、`logoRereadStep`、`rereadForLogo`（标志操作 403 后以界面语言重新读取，只取新的 CSRF 令牌与 logo）、`saveDisabled`（保存进行中或标志操作进行中）。
  - 互斥：`common.save` 在标志操作进行中禁用（`aria-busy` 仍只标保存进行中），`onSave` 也不在此时提交；标志段在保存进行中禁用。
  - 预览（两份缩样）的页头改用 `LogoMark` 显示已生效的标志；没有标志时与原来的 `<span class="acs-brand">ACUVEN SHOP</span>` 相同。加载失败按提供路径记在 `AdminStoreDesignView` 的状态里，标志段与两份预览共用，logo 换成另一个路径或变为 null 时即不算失败（`logoBroken`）。
  - 表单的选择、保存请求（`saveInput`）与保存结果的处理（`saveStep`、`submitDesign`）未改；保存成功后 base 由保存时的 base 展开，logo 即当前值（保存进行中标志操作被禁用，所以与保存时相同）。
- [x] `frontend/src/i18n/copy.ts` 按 UX-COPY 0.19 逐字加入 `admin.logo`、`admin.logo_hint`、`admin.logo_remove`、`admin.logo_rules`、`admin.logo_immediate`、`admin.logo_invalid`、`admin.logo_remove_confirm`（七个都还不在字典里），放在 A08 的 `admin.design_saved` 之后。
- [x] `frontend/src/styles/site.css` 加标志段的布局（桌面与 767px 及以下），不声明颜色、字体与圆角。
- [x] 新增 `frontend/src/pages/AdminStoreLogo.test.tsx`；`frontend/src/pages/AdminStoreDesignPage.test.tsx` 为接入补测试并改了几条受接入影响的现有测试（见下）。
- `router.tsx`、`App.tsx` 不需要改，未改（不新增路由）。未改后端、设计、`docs/TODO.md`、`.platform/`、CI 与部署配置，未装依赖。
- 设计闸门：不适用——标志的格式、大小、像素、元数据与存放规则在 SHOP-TASK-209 至 213 的服务端（DESIGN 1.20 第 15 条）；页面只做格式与大小的初步检查，不碰金额与个人资料。

## 偏离与取舍，请审阅

- `admin.design_saved`：验收写「标志操作不改表单、不收起 admin.design_saved 以外的表单提示」，这里理解为标志操作开始时收起 `admin.design_saved`（`hideSaved`），`alert` 与 `saveError` 保留。如意图是连它也不收起，删去 `onLogoBusy` 里的那一步即可。
- 403 后重新读取由页面做（`rereadForLogo`），所以页面源码里 `readAdminStoreDesign(` 多了一处；现有测试 `makes no request for the preview` 的断言随之改为读取、保存、读取三处。取到时提示 `common.error_retry`（请再操作一次），与 A04 图片区相同。
- 标志段的 fieldset 不带 `site-admin-store-design__group`（另用 `site-admin-logo`，样式值相同），以免改变现有测试按下标取四组的写法。
- 说明的分段照 A08-logo-states：`admin.logo_hint`、`admin.image_rules`、`admin.logo_rules` 同一段（三个 span 以空格相连），`admin.logo_immediate` 另一段。UX 手机线框没有列 `admin.image_rules`，验收要求四条常显，手机同样显示。
- 提示（role="alert"）放在标志段末尾（视觉稿只画了提示本身，没画位置），写法同 A04 图片区。
- 手机上 `admin.upload` 与 `admin.logo_remove` 至少 44px 高，照本页其他手机按钮；A08-phone 未给高度。
- 预览缩样页头里的标志沿用标志段的尺寸（高 36px、至多宽 180px）；视觉稿未画有标志的预览。
- site.css 写不了而未搬的视觉稿样式：当前标志框的虚线边框与圆角、有标志时的白底、文字品牌的字体（Young Serif）与字距、legend 的字重、说明的字号（13px）、确认框的阴影。
- `fileProblem` 另写在标志段里（提示类型不同），类型与上限取 `AdminProductImages.tsx` 的常量，未复制数值。

## 验证到什么程度

- 人工逐条对照验收标准、UX 0.18 A08、UX-COPY 0.19、HANDOFF 0.84 第（7）点、DESIGN 1.20 第 15 条与视觉稿 A08-logo-states 自查。未做浏览器验收，没有用真实图片与存储试过上传，图片加载失败的回退只在测试里直接调用 onError 验证。
- `frontend/src/pages/AdminStoreLogo.test.tsx`（vitest，`renderToStaticMarkup` 渲染 `StoreLogoView`，直接调用导出的函数与元素树里的处理函数，fetch 以替身代替，不模拟点击）：
  - 没有标志时为文字品牌、`admin.logo_remove` 禁用；有标志时 img 的 class、src 与 alt；加载失败时为文字品牌，`logoBroken` 按路径判断，img 的 onError 把路径交给 onBroken。
  - 标题、当前标志、两个按钮与四条说明的顺序与分段；隐藏的单选文件输入框（accept、hidden、没有 multiple），选好文件后交给 onFile 并清空输入框。
  - 上传、移除进行中与保存进行中两个按钮都禁用，只有上传中显示 `admin.image_uploading`（role="status"）并标 aria-busy；下一次操作开始时收起上一次的提示。
  - 先查：三种类型 5,242,880 字节合格、多一字节为 `admin.image_too_large`，其他类型为 `admin.image_type_unsupported`；不合格时不发请求。
  - 上传：PUT 的请求体为文件本身、Content-Type 为文件类型、带 CSRF 令牌与 signal，ok 换上返回的 logo；各结果与提示的对应（含 415、413、422、503 与网络中断时不换标志），401 回到 A01，中止后不更新。
  - 移除：确认框的文字、按钮顺序、autoFocus 与 Escape；DELETE 成功换上 null；各结果与提示的对应。
  - 各提示以 role="alert" 显示且当前标志不变；各状态的文字都来自字典或 BRAND。
  - 403：上传与移除都先由页面重新读取，再按结果提示 `common.error_retry` / `common.network_check` 或回到 A01；`rereadForLogo` 以界面语言 GET，只换 base 的 csrf_token 与 logo，表单与其余设置、提示不变。
- `frontend/src/pages/AdminStoreDesignPage.test.tsx`：
  - 新增 `logo` 一组：标志段在表单里并显示当前 logo；标志操作进行中 `common.save` 禁用、保存进行中标志按钮禁用（`saveDisabled` 与 onSave 的判断）；有标志时保存，请求体没有 logo、保存成功后 logo 沿用当前值；两份预览页头在有标志时为 img、没有或加载失败时为文字品牌；`changeLogo` 与 `hideSaved` 只改该改的部分。
  - 因接入而改的现有测试（各自注释写明「SHOP-TASK-215 改动」）：测试辅助 `render` 补标志段的 props；`keeps the demo banner and the demo note` 的按钮列表最前加标志段两个按钮；`has the theme, accent, home block and featured groups` 去掉「没有标志的文字」，改为标志段在主色之后、首页区块之前；`makes no request for the preview` 的读取与保存调用改为三处；保存失败时的 fieldset 数由 4 改为 5；预览的测试辅助与 `shows only dictionary text` 补 logo、broken、onBroken，并加有标志、加载失败与标志操作进行中的状态。
  - 每条新增测试的注释写明它守住的 UX 0.18、DESIGN 1.20 第 15 条或 HANDOFF 0.84 的原句。前端检查交由 PR 的必需 CI 检查 frontend 执行（lint、类型检查、测试、构建与镜像构建；Worker 沙箱不跑前端检查）。

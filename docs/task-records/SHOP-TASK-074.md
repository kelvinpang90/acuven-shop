# SHOP-TASK-074 页脚与隐私说明 P14 在已配置时显示 WhatsApp 联系入口

- [x] 按 `docs/UX.md` 0.10「全局框架」的页脚、P14 的「联系」段与待决问题 Q10（配置缺失时整个按钮或整段隐藏，不显示占位文字）、`docs/UX-COPY.md` 0.10，前台读取 SHOP-TASK-073 在 `GET /api/site-settings` 返回的 `whatsapp_contact_url`，已配置时在每页页脚显示 `common.whatsapp_cta` 按钮、在 P14 显示「联系」段：改 `frontend/src/api/siteSettings.ts`、`frontend/src/components/SiteFrame.tsx`、`frontend/src/pages/PrivacyPage.tsx`、`frontend/src/App.tsx`、`frontend/src/i18n/copy.ts`、`frontend/src/styles/site.css`；测试见 `frontend/src/api/siteSettings.test.ts`、`frontend/src/components/SiteFrame.test.tsx`、`frontend/src/pages/PrivacyPage.test.tsx`。未改 `router.tsx`（未新增路由）、后端、`.platform/`、CI、部署配置、设计与生成的 `styles/acuven-shop.css`，未装依赖（未改 `package.json` 与锁文件）。设计闸门：不适用（显示公司公开的联系链接，不涉及金额计算与访客个人资料）。拆分：前端显示层（11 个允许路径）。
- 读取（`api/siteSettings.ts`）：新增 `parseWhatsAppContactUrl`——值为字符串、以 `https://` 开头（不分大小写）且其后紧接主机（不是 `/` 或 `\`）、能被 `new URL` 解析、协议为 `https:` 且 `hostname` 非空时原样返回该字符串，其余（缺字段、`null`、其他类型、不合格）一律为 `null`。`fetchSiteSettings` 的结果另含 `whatsapp_contact_url`（按上述规则取，不合格时为 `null`，不让开关的读取失败）；开关的判定、请求方式与抛错照旧，结账页只读开关，行为不变。页脚与 P14 共用的读取为 `createWhatsAppContactReader()`：第一次 `load()` 时请求一次（同一地址、`GET`、`credentials: "omit"`、`cache: "no-store"`），之后都返回同一结果、不再请求（失败也不重试）；`current()` 在取得前、未配置与失败时为 `null`。它只看链接，非 2xx、网络错误、不是 JSON、回答不是对象时为 `null`、不抛错。模块里有一个本次页面加载唯一的读取，经 `WhatsAppContactProvider`（`App.tsx` 包在路由之外）提供给 `useWhatsAppContactUrl()`：首次渲染用已取得的值（第一次打开页面时为 `null`），effect 里 `load()` 完成后更新；在前台页之间切换时不再请求，切到的页面直接用已取得的值。`App` 新增仅供测试的 `whatsAppContact` 属性（传入读取的替身），浏览器里不传。
- 页脚（`SiteFrame.tsx`）：链接已取得时在 `site-footer__main` 之后渲染 `WhatsAppButton`——`<a class="acs-btn acs-btn--secondary site-footer__whatsapp">`，`href` 为该地址，`target="_blank"`、`rel="noopener noreferrer"`，文字之前为 `aria-hidden` 的对话气泡 SVG（与视觉稿同一路径），文字 `common.whatsapp_cta`；取得前、未配置或读取失败时整个按钮不渲染，页脚只剩原来的主体，没有占位文字或空容器。`WhatsAppButton` 导出供 P14 共用。模块说明改写为链接的来源与 Q10 的隐藏规则。
- P14（`PrivacyPage.tsx`）：链接已取得时在现有四段之后渲染第五个 `site-privacy__section`：`h2.acs-display-s` 为 `privacy.h_contact`，段落 `privacy.contact`，按钮 `WhatsAppButton`（`site-privacy__contact`，文字 `privacy.contact_button`，同上新标签页与 `rel`）；未取得时整段不渲染。模块说明随之改写。
- 文案（`i18n/copy.ts`）：收录 `common.whatsapp_cta`、`privacy.h_contact`、`privacy.contact`、`privacy.contact_button`，三语逐字取自 UX-COPY（由 `copy.test.ts` 现有的逐字比较守住）；没有用到 UX-COPY 以外的界面文字。
- 样式（`site.css`）：图形不收缩；桌面页脚按钮在页脚第二列靠右（P01-desktop、P14-desktop 的 `justify-self: end`），P14「联系」段的按钮靠左（`justify-self: start`）；767px 及以下两处按钮占满宽度（`display: flex`、`justify-self: stretch`），页脚按钮文字靠左、内边距 12px 16px（P01-phone、P14-phone）。按钮在页脚里的颜色来自 `acuven-shop.css` 已有的 `.acs-footer .acs-btn--secondary`，未声明颜色、字体或圆角。
- 改动的现有测试（只按需要）：
  - `siteSettings.test.ts`「reads the SMS switch」的整体相等改为另含 `whatsapp_contact_url: null`：结果多了这一项，回答里没有该字段时为 `null`。
  - `SiteFrame.test.tsx` 的 `render` 加可选的第三个参数（读取替身），不传时为新建、尚未请求的读取，即取得前的首次渲染，现有调用不变；「has no WhatsApp button, placeholder or contact form」的注释补一句说明它现在守住的是取得前的首次渲染，断言未改。
  - `PrivacyPage.test.tsx`：`renderMain` 加可选的读取参数（同上）；「has no contact section and no WhatsApp link」的三个键改从字典取文字（此前字典里没有，从 `docs/UX-COPY.md` 读），删去读文档的辅助函数与 `?raw` 引入，断言不变；「offers no way to delete shipping details」与「keeps every section expanded」保留原断言，另加已配置时的一次（前者先去掉「联系」段那一个 WhatsApp 链接再断言没有链接、按钮或表单，因为已配置时正文按验收会多出这个链接）。
- 偏离与取舍，请审阅（未改设计与文案）：
  - P14-phone 的「联系」按钮在视觉稿里加了 `acs-btn--block`；这里不加该类（它在桌面也会占满宽度），改在 767px 媒体查询里以 `display: flex` 与 `justify-self: stretch` 占满宽度，效果相同。
  - 手机页脚：视觉稿里品牌、说明、Privacy 与按钮都是页脚的直接子元素（间距 16px）；这里沿用 SHOP-TASK-014 的页脚主体容器（主体内间距 12px），按钮是页脚的第二个子元素，与主体间距 16px。
  - 视觉稿里 site.css 写不了的项本任务没有新增：按钮颜色来自设计系统已有规则；页脚品牌字样的 `color: var(--on-footer)` 与 Privacy 链接的字重是 SHOP-TASK-014、054 已记的偏离，未处理。
  - 新标签页打开没有给读屏的额外提示（UX-COPY 没有这类文字，按验收不自行编写）。
  - 前台的链接校验比接口（SHOP-TASK-073）宽的地方：不去首尾空白、不限 512 个字符、不拒控制字符（接口已处理，前台只防接口之外的值）；比接口严的地方：`https://` 之后紧接 `\` 也算不合格。
  - 读取失败后本次页面加载内不重试，直到刷新页面；`/checkout` 上结账页照旧自己读一次开关，加上页脚的那一次，共两次 `GET /api/site-settings`。
  - `fetchSiteSettings` 在回答不是对象（如 `null`）时现在抛 `SiteSettingsError`，此前读属性时抛 `TypeError`；两者结账页都按失败显示 `common.error_retry`。
- 验证到什么程度：人工逐条对照验收标准、UX 0.10「全局框架」页脚、P14、Q10 与 UX-COPY 的原句自查，样式对照 P01-desktop、P01-phone、P14-desktop、P14-phone 的页脚与「联系」段。测试用 vitest 与 `react-dom/server`（未加测试依赖；`fetch` 以 `vi.stubGlobal` 替身，读取以 `createWhatsAppContactReader` 先读完一次再经 `App` 的 `whatsAppContact` 传入，路由以 `initialPath`），每条注释写明它守住的 UX、Q10 或验收的哪一句，覆盖：合格链接（含大写协议、端口、查询与片段）原样取出；缺字段、`null`、布尔、数字、数组、对象、空串、空白、无协议、`//` 开头、相对路径、http、ftp、javascript、whatsapp 协议、`https://`、`https:///path`、`https:host`、只有端口、只有用户信息、残缺的 IPv6、主机含空格都为 `null`；开关照常读出且不因链接失败；读取只请求一次、并发与重复读取同一结果、请求方式；非 2xx、网络错误、非 JSON、非对象、未配置、缺字段、http 时为 `null` 且不重发；开关不合格时链接照常取出；每个前台页的首次渲染（未取得）与未配置、缺字段、http、503、网络错误时页脚没有按钮（页脚只有主体）、没有 whatsapp 与 `{{`；已取得时每个前台页、每种语言页脚主体之后恰有一个按钮，类名、地址、`target`、`rel`、`aria-hidden` 图形在字典文字之前，没有 `{{` 与行内样式；P14 首次渲染与上述未取得情形下只有四段、没有联系段文字；已取得时第五段在四段之后，依次为 h2、段落与按钮，按钮的类名、地址、`target`、`rel` 与图形，文字都取自字典；已配置时除该链接外正文仍没有链接、按钮或表单，各段仍不折叠。浏览器相关的部分（effect 里的实际请求与状态更新、新标签页的实际打开、页面之间切换时不再请求）未在 DOM 中执行，只以读取本身与服务端渲染的各状态验证。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器中打开页面，未连真实后端走一遍，也未与视觉稿逐像素对比）。真实链接由运营者在服务器私有配置里填写 `SHOP_WHATSAPP_CONTACT_URL`，仓库不存。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的「全局框架」页脚、P14 与 Q10、docs/UX-COPY.md 0.10 为准，样式以 docs/design/pages/ 的 P01-desktop、P01-phone 的页脚与 P14-desktop、P14-phone 的「联系」段为准（按钮为 acs-btn acs-btn--secondary，内含 aria-hidden 的对话气泡图形），二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - frontend/src/api/siteSettings.ts 另取 whatsapp_contact_url——是协议为 https 且主机非空的绝对地址字符串时为该地址，其余（缺字段、null、其他类型或不合格）一律为 null，不因它让现有 fetchSiteSettings 失败（结账页读取开关的行为不变）；另提供供页脚与 P14 共用的读取（同一次页面加载只请求一次，取得前与失败时为 null）
      - 页脚（frontend/src/components/SiteFrame.tsx）——链接已取得时在页脚主体之后显示 common.whatsapp_cta 按钮，链到该地址，在新标签页打开并带 rel 为 noopener noreferrer；未取得、未配置或读取失败时整个按钮不渲染（Q10），不显示占位文字或空容器；模块说明里「配置来源由之后单独登记的任务提供」一句随之改写
      - 隐私说明 P14（frontend/src/pages/PrivacyPage.tsx）——同样在链接已取得时于现有四段之后显示「联系」段——h2 privacy.h_contact、privacy.contact 与链到该地址的 privacy.contact_button 按钮（同上新标签页与 rel）；未取得时整段不渲染；模块说明随之改写
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/api/siteSettings.test.ts、frontend/src/components/SiteFrame.test.tsx 与 frontend/src/pages/PrivacyPage.test.tsx 验证意图——字段的各种取值与不合格时为 null、开关读取不受影响；一次页面加载只请求一次；初始渲染（未取得）与未配置、失败时页脚按钮与「联系」段都不出现；已取得时按钮的文字、地址、target 与 rel、顺序与 aria-hidden 图形；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Q10 的哪一句；现有断言只按需要改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-074 记录段（读取、页脚、P14、改动的现有测试、偏离、验证到什么程度，并写明未做浏览器验收、真实链接由运营者配置），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（不新增路由）；不改 .platform/、CI、部署配置与后端；不装依赖（不改 package.json 与 package-lock.json）；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

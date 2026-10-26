# SHOP-TASK-136 隐私说明 P14 的 Stripe 测试环境资料告知

- [x] 按 `docs/UX.md` 0.11 的 P14（线框「[privacy.h_collect] … [privacy.fictional] [P9] [privacy.stripe]」与 P9「始终显示，不随支付方式开关隐藏」）、`docs/UX-COPY.md` 0.12 与 `docs/DESIGN.md` 1.12「Stripe 测试环境支付」第 1、10 条、「资料保留」，在隐私说明页「收集什么」段加入 Stripe 测试环境的资料告知：改 `frontend/src/i18n/copy.ts`、`frontend/src/pages/PrivacyPage.tsx`、`frontend/src/pages/PrivacyPage.test.tsx`。未改 `router.tsx`（未新增路由）、`App.tsx`、`site.css`、后端、`docs/TODO.md`、`.platform/`、CI、部署配置、设计与生成的 `styles/acuven-shop.css`，未装依赖（未改 `package.json` 与锁文件）。设计闸门：不适用（只加入 Kelvin 2026-10-15 审阅通过的告知文案，不收集、存储、展示或导出个人资料；告知内容依据 DESIGN 1.12 第 10 条）。拆分：P14 的显示层（7 个允许路径）。
- 文案（`i18n/copy.ts`）：在 `privacy.fictional` 之后收录 `privacy.stripe`，三语逐字取自 UX-COPY 0.12（每种语言一行，与相邻条目相同；由 `copy.test.ts` 现有的逐字比较守住）；没有用到 UX-COPY 以外的界面文字。
- P14（`PrivacyPage.tsx`）：「收集什么」段（`privacy.h_collect`）的段落依次为 `privacy.collect`、`privacy.fictional`、`privacy.stripe`，与相邻段落同为普通 `<p>`；始终显示，不读取站点设置或支付方式。段落数仍为四段（另加已配置时的「联系」段），其余段落与「联系」段不变。模块说明补一句 `privacy.stripe` 的依据与始终显示。
- 样式：P14-stripe-desktop 里新段落与 `privacy.fictional` 同样是 `margin: 0` 的段落，段内间距 12px，与现有 `.site-privacy__section`、`.site-privacy p` 相同，`site.css` 无需改动。
- 测试（`PrivacyPage.test.tsx`）：段落顺序 `ORDER` 在 `privacy.fictional` 之后加入 `privacy.stripe`；新增「shows the Stripe paragraph in the collect section whatever the contact link」，守住 UX 0.11 P14 的 P9「始终显示，不随支付方式开关隐藏」与线框中 `[privacy.stripe]` 在 `[privacy.fictional]` 之后的位置——三种语言下，WhatsApp 链接未取得、未配置与已配置时，`privacy.stripe` 的段落都在 `privacy.fictional` 段落之后、`privacy.h_retention` 之前。其余断言未改。
- 偏离与取舍，请审阅（未改设计与文案）：
  - P14-stripe-phone 的「收集什么」段三个段落都加了 `acs-body-s`，而 P14-phone 的同一段与其余各段没有；这是字号，`site.css` 写不了，也不借用该类，本任务不搬，三段沿用现有正文字号。
  - P14-stripe-phone 是只画到「收集什么」段的节选：外层间距 16px、内边距 24px 16px 32px、该段另加 `padding-top: 8px`，与 P14-phone 的整页（外层间距 24px、内边距 28px 16px 40px）不同；`16px + 8px` 与整页的 24px 相同，本任务沿用整页 P14-phone 的布局，段内间距 10px 两稿一致。P14-stripe-desktop 的下内边距 64px 同为节选裁切，沿用 P14-desktop 的 96px。
- 验证到什么程度：人工逐条对照验收标准、UX 0.11 P14 线框与 P9、UX-COPY 0.12 的 `privacy.stripe` 原句自查，样式对照 P14-stripe-desktop、P14-stripe-phone 与 P14-desktop、P14-phone。测试用 vitest 与 `react-dom/server`（未加测试依赖），lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查；检查命令结果由 Worker 另行记录。未做浏览器验收（未在真实浏览器中打开页面，也未与视觉稿逐像素对比）。

## 登记时的验收标准（原文）

2026-10-26 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.11 的 P14 与 docs/UX-COPY.md 0.12 为准，样式以 docs/design/pages/ 的 P14-stripe-desktop 与 P14-stripe-phone 为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局写在 frontend/src/styles/site.css（不声明颜色、字体、圆角属性，视觉稿给出的固定间距直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入任务记录的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 0.12 逐字加入 privacy.stripe，写法与相邻条目相同（每种语言一行）
      - frontend/src/pages/PrivacyPage.tsx 的「收集什么」段（privacy.h_collect）依次为 privacy.collect、privacy.fictional、privacy.stripe；始终显示，不读取站点设置或支付方式；段落数仍为四段（另加已配置时的「联系」段），其余段落与「联系」段不变
      - frontend/src/pages/PrivacyPage.test.tsx 只许把段落顺序 ORDER 在 privacy.fictional 之后加入 privacy.stripe，并另加一条测试验证意图——三种语言下 privacy.stripe 在 privacy.fictional 之后、privacy.h_retention 之前，WhatsApp 链接未取得、未配置与已配置时都在；测试写明它守住的是 UX P14 的哪一句；其余断言一字不改；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/task-records/SHOP-TASK-136.md 新增本任务记录（格式照 docs/TODO.md 约定第 3 条；加入的段落、偏离、验证到什么程度，并写明未做浏览器验收）
      - 改动只落在 allowed_change_paths（不新增路由）；不改 docs/TODO.md、.platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

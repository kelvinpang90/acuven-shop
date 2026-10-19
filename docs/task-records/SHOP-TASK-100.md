# SHOP-TASK-100 后台优惠券页的路由与导航 A05

- [x] 按 `docs/UX.md` 0.10 A05、`docs/UX-COPY.md` 0.11 与 `docs/HANDOFF.md` 0.48 增加 `/admin/coupons`：用后台框架渲染，当前导航项为优惠券，内容区仅有 `admin.nav_coupons` 标题和 `admin.coupon_listed_note`。后台导航按 UX 顺序改为订单、退款、优惠券、库存重置、店铺装修；同步修改后台框架、退款、库存重置、店铺装修页既有导航断言，以及路由表断言。本任务不调用接口、不装依赖、不改后端或设计。设计闸门：不适用（只加路由、导航与静态文字）。
- 偏离：视觉稿中 site.css 无法声明的颜色、字号、字重与底色没有搬入，也没有借用其他组件的类取得这些属性；本页只实现 A05-desktop 与 A05-phone-list 的页头部分，列表、新建和停用不在本任务范围。
- 留给之后的任务：请求模块由 SHOP-TASK-101 实现；列表、新建与停用由 SHOP-TASK-102 实现。未做浏览器验收。
- 验证到什么程度：人工逐条对照 A05、UX-COPY 与 HANDOFF 的导航顺序、标题和会员中心说明；新增页面测试以 vitest 与 react-dom/server 覆盖后台框架、五项导航顺序及当前项、标题和说明均来自字典；前端检查由 PR 的必需 CI `frontend` 执行。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的 A05、docs/UX-COPY.md 0.11 与 docs/HANDOFF.md 0.48 为准，样式以 docs/design/pages/ 的 A05-desktop 与 A05-phone-list 的页头部分为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色
      - 路由 /admin/coupons（frontend/src/router.tsx、frontend/src/App.tsx 与 frontend/src/router.test.tsx 各加一项），新文件 frontend/src/pages/AdminCouponsPage.tsx 用后台框架渲染，当前导航项为优惠券（手机顶栏标题为 admin.nav_coupons），内容区为 h1 admin.nav_coupons 与其下的 admin.coupon_listed_note；frontend/src/i18n/copy.ts 按 UX-COPY 0.11 加入本任务用到的键；页面不发起自己的请求（列表由 SHOP-TASK-102 接上）
      - frontend/src/components/AdminFrame.tsx 的导航按 UX 顺序只含已上线的订单、退款、优惠券、库存重置与店铺装修五项（桌面导航与 ☰ 菜单同样）；frontend/src/components/AdminFrame.test.tsx 只许把「只含订单、退款、库存重置与店铺装修四项」的断言（桌面导航与手机菜单）改为按 UX 顺序只含这五项、其他 admin.nav_* 不出现；frontend/src/pages/AdminRefundsPage.test.tsx、frontend/src/pages/AdminStockResetsPage.test.tsx 与 frontend/src/pages/AdminStoreDesignPage.test.tsx 只许把 navigation 一组里「依次为这四项」的断言改为依次为这五项（各自的当前项与手机顶栏名称不变）；四个文件的其余断言不动
      - 用 vitest 与 react-dom/server（不加测试依赖，路由以替身代替）在 frontend/src/pages/AdminCouponsPage.test.tsx 验证意图——经路由打开页面渲染后台框架、不套前台框架；当前导航项为优惠券且五项顺序正确；标题与说明；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-100 记录段（路由、导航变化、改动的现有测试、偏离、验证到什么程度，并写明未做浏览器验收、请求模块与列表、新建、停用留给 SHOP-TASK-101、102），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

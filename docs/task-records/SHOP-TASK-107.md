# SHOP-TASK-107 后台退款详情显示积分一行 A03

- [x] 按 `docs/UX.md` 0.10 的 A03（线框里退款金额之下的 `[admin.refund_points] [M2]`；「状态与补充（0.9）」首版不显示这一行、待积分字段接入时加回，Kelvin 2026-10-06 决定）与 `docs/UX-COPY.md` 0.11，在后台退款详情显示 SHOP-TASK-106 给出的积分返还与追回。改动：`frontend/src/api/adminRefunds.ts`、`pages/AdminRefundDetail.tsx`、`i18n/copy.ts`、`docs/TODO.md` 与相应测试（`api/adminRefunds.test.ts`、`pages/AdminRefundDetail.test.tsx`）。未改 `router.tsx`、`App.tsx` 与 `styles/site.css`（不新增路由、不改导航；这一行沿用金额区块已有的布局，不需要新样式）；未装依赖（未改 `package.json`、锁文件），未改后端、`.platform/`、CI、部署配置、设计或生成的 `styles/acuven-shop.css`。设计闸门：不适用（金额与积分由服务端按快照与流水算出，页面只显示）。拆分：A03 页面的一行显示。
- 字段（`api/adminRefunds.ts` 的详情）：另取 `placed_by_member`（布尔）、`points_returned`、`points_reversed`（非负安全整数或 null）与 `points_estimated`（布尔或 null）。三个积分字段要么都为 null、要么都不为 null（`readPoints`），只有一部分为 null、缺少字段、类型或取值不对，以及 `placed_by_member` 缺少或不是布尔，都按响应体不合格归为 `failed`。其余字段、检查与归类不变；多出的字段仍不带进结果。
- 显示条件（`pages/AdminRefundDetail.tsx` 的 `PointsLine`）：在金额区块里 `admin.refund_amount` 之下、`order.refunded_total` 之上显示一行 `admin.refund_points`（`{returned}` 为 `points_returned`，`{reversed}` 为 `points_reversed`，原样显示整数）；只在 `placed_by_member` 为真且积分字段不为 null 时显示，所以会员订单的审核中（预估）与已批准显示，已拒绝（null）与游客订单（含被认领的游客订单，积分为 0）不显示；会员订单积分为 0 时照样显示。删去了「首版不显示 admin.refund_points」的注释。
- 字典：从 UX-COPY 原样抄入 `admin.refund_points`（英、中、马三列）。未用到 UX-COPY 以外的界面文字。
- 已有测试的改动（只按需要，注释写明原因）：`pages/AdminRefundDetail.test.tsx` 删去「has no points line」（守的是「首版不显示」，积分字段已接入，按同一句的「加回」改为下面的新测试）；三个共用的申请样例补上四个积分字段，取游客订单的值（审核中 0、0、预估；已批准 0、0、非预估；已拒绝都为 null），所以原有金额与只读等断言不变；「shows only dictionary text」另加会员订单的三种状态与填入两个数的 `admin.refund_points`。`api/adminRefunds.test.ts` 的样例补上四个字段；「keeps only the agreed fields」里原来当作多出字段的 `points_returned` 现在是约定字段，换成 `member_id`，已批准的样例改用带积分的 `APPROVED_DETAIL`。其余断言未动。
- 偏离与取舍，请审阅（未改设计，未新增 UX-COPY 以外的界面文字）：
  - 审核中的申请显示的是预估（`points_estimated` 为真，批准时可能因到期或余额变化而不同）；UX-COPY 没有「预估」的文案，按验收照样显示这一行，与已批准的显示相同，页面不区分预估与实际。`points_estimated` 只取进结果、不用于显示。
  - 不按申请状态校验积分字段（例如已拒绝却给了积分、审核中却不是预估），只校验三者同为 null 或同不为 null（验收只要求这一条）；已拒绝的会员订单若给了积分也会显示。
  - 视觉稿 A03-desktop 与 A03-phone-detail 的积分一行是金额区块里的普通一行，没有另外的颜色、字号或字重，`site.css` 无须改动，也没有未搬的视觉。视觉稿的样例数（146 / 27）只用于对照文案格式。
- 留给之后的任务：P10 会员模式的积分预估显示与 P09 会员订单的券与积分两行（见「已阻塞」里的会员中心与订单详情一项）；A03 的 Stripe 退款部分（等 SHOP-TASK-110 至 117）。
- 验证到什么程度：人工逐条对照验收标准、UX A03 线框与「状态与补充（0.9）」、UX-COPY `admin.refund_points` 原文与 SHOP-TASK-106 记录段的字段与取值自查（字典逐字比较由已有的 `i18n/copy.test.ts` 覆盖）。测试用 vitest 与 `react-dom/server`（未加测试依赖；`fetch` 以按顺序回答的替身，路由以记录 `replace` 的替身），每条注释写明它守住的 UX、Kelvin 决定、SHOP-TASK-106 记录段或验收的哪一句。覆盖：`api/adminRefunds.test.ts`——四个字段原样取到（会员审核中、已批准、已拒绝，游客订单的 0 与 null，零积分）；二十余种不一致或不合格的积分字段与 `placed_by_member` 归为 `failed`。`pages/AdminRefundDetail.test.tsx`——三种语言下会员订单审核中与已批准在退款金额之下显示 `admin.refund_points` 与两个数，已拒绝与游客订单三种状态不显示，积分为 0 的会员订单照样显示；打开详情时积分字段不一致归为读取失败并显示 `common.error_retry`；各状态（含会员订单）页面文字只来自字典与接口数据。lint、类型检查、测试、构建与镜像构建由 PR 的必需 CI 检查 frontend 执行，Worker 沙箱不跑前端检查。未做浏览器验收（未在真实浏览器或手机上打开页面，未连真实后端查看会员订单的申请，也未与视觉稿对比）。检查命令结果由 Worker 另行记录。

## 登记时的验收标准（原文）

2026-10-19 从 `.platform/tasks.yaml` 原样移来，任务已交付；那里只留一行指向本文件。

```yaml
    acceptance_criteria:
      - 页面内容与文案以 docs/UX.md 0.10 的 A03、docs/UX-COPY.md 0.11 为准，样式以 docs/design/pages/ 的 A03 桌面与手机画板为准，二者冲突时以 UX 文档为准并在 PR 里说明；需要 UX-COPY 里没有的界面文字时停下并在 PR 里说明，不自行编写文案、不改设计；布局与显隐写在 frontend/src/styles/site.css（手机的差异写在 767px 及以下的媒体查询里；不声明颜色、字体、圆角属性（链接的 color 可取 inherit，不写颜色值与变量），视觉稿给出的固定间距与尺寸直接写像素值；不改生成的 frontend/src/styles/acuven-shop.css）；视觉稿里 site.css 写不了的颜色、字号、字重与底色不搬，记入 docs/TODO.md 的偏离（评审不以此要求修改），不借用其他组件的类来取颜色；frontend/src/i18n/copy.ts 按 UX-COPY 加入 admin.refund_points
      - frontend/src/api/adminRefunds.ts 的详情另取 placed_by_member（布尔）、points_returned、points_reversed（非负整数或 null）与 points_estimated（布尔或 null），三个积分字段要么都为 null、要么都不为 null，否则算响应体不合格；frontend/src/api/adminRefunds.test.ts 补对应测试；其余字段与归类不变
      - frontend/src/pages/AdminRefundDetail.tsx 在退款金额（admin.refund_amount）之下显示一行 admin.refund_points（{returned} 与 {reversed} 为两个积分数），只在 placed_by_member 为真且积分字段不为 null 时显示；审核中的申请显示的是预估（points_estimated 为真），UX-COPY 没有「预估」的文案，照样显示这一行并记入偏离；删去「首版不显示 admin.refund_points」的注释与相应断言
      - 用 vitest 与 react-dom/server（不加测试依赖，fetch 与路由以替身代替）在 frontend/src/pages/AdminRefundDetail.test.tsx 验证意图——会员订单已批准与审核中的申请显示这一行与两个数、已拒绝与游客订单不显示；三个积分字段不一致时归为失败；页面文字全部来自字典；每条测试写明它守住的是 UX 或 Kelvin 决定的哪一句；现有断言只按需要改并写明原因；由 PR 的必需 CI 检查 frontend 执行 lint、类型检查、测试、构建与镜像构建并全部通过（Worker 沙箱不跑前端检查）
      - docs/TODO.md 新增 SHOP-TASK-107 记录段（字段、显示条件、偏离、验证到什么程度，并写明未做浏览器验收），不改 planning-v1 块，复选框只允许 [ ] / [x]
      - 改动只落在 allowed_change_paths（不新增路由，不改导航）；不改 .platform/、CI、部署配置；不装依赖（不改 package.json 与 package-lock.json）；不改后端；allowed_commands 里的检查全部零退出，skipped 如实报告且不算 passed；以 Draft PR 交付（设计闸门：不适用，理由同目的），不合并、不直推
```

# Acuven Shop 前端页面结构与线框（审阅稿）

> **审阅稿。0.7 已于 2026-10-01 经 Kelvin 审阅通过（记录见 `docs/HANDOFF.md`）；0.8 只在「管理后台总体」与 A09 增加后台退出按钮与视觉稿说明，P01–P14、V1 与 A01–A08 各页内容与 0.7 相同，Kelvin 已于 2026-10-05 认可（记录见 `docs/HANDOFF.md`）。**
> 版本 0.8（2026-10-05），运营者修订（Claude Code），在 0.7（提交 `2d13250`）上按 Kelvin 2026-10-05 的决定增加后台退出按钮；0.7 在 0.6（提交 `e3b3505`）上按 Kelvin 2026-10-01 的短信验证开关决定修改。依据 `docs/REQUIREMENTS.md` 1.12 候批稿与 `docs/DESIGN.md` 1.11 候批稿；未特别注明时，各页「金额与个人资料元素」表引用的小节仍指 DESIGN 1.9（1.10、1.11 未改小节标题）。Kelvin 2026-09-30 对待决问题 Q11、Q14、Q16、Q17、Q18、Q19 的决定见 `docs/HANDOFF.md`。三语文案见 [UX-COPY.md](UX-COPY.md)（0.7）。视觉稿（设计系统、10 款主题、每页静态页面与参考图）见 [design/README.md](design/README.md)，只管样式，内容与流程以本稿为准；本版新增的元素沿用相邻元素的样式。本稿不改设计，冲突之处列入文末「待决问题」。

## 0.8 修订要点

- **后台退出**（Kelvin 2026-10-05 决定）：除 A01 外，后台每页右上角常驻 `[admin.logout]`：桌面在 `[admin.demo_banner]` 右侧，手机在顶栏右端；退出后撤销当前后台会话、回到 A01。
- 后台各页的视觉稿已补齐（`docs/design/` 2026-10-05 补充：全部后台页面的手机稿与 A03、A05、A06、A07、A09 的桌面稿），A09 不再「不另出视觉稿」。

## 0.7 修订要点

- **短信验证开关**（Kelvin 2026-10-01 决定，REQUIREMENTS 1.12、DESIGN 1.11）：新增后台 A09 站点设置，`[admin.sms_toggle]` 开关全站短信，默认关闭。关闭时：V1 在注册、短信登录与重设密码处整个替换为 `[auth.sms_paused]`；P05 第 1 步以 `[checkout.phone_notice_sms_off]` 代替 `[checkout.phone_notice]`，未登录访客的任何号码（含马新号码）继续后显示 `[checkout.guest_sms_off]` 并以游客表单进入第 3 步；P13 注销改为已设密码者输入密码、未设密码者经确认框，未设密码时另显示 `[account.sms_paused_set_password]`；P14 增加 `[privacy.sms_toggle]`。开启时各页与 0.6 相同。
- 前台按服务端提供的开关当前值决定显示哪一组；服务端在下单、发送短信与注销时自行按开关判定，前端显示不是依据。
- A09 沿用后台中性样式，不另出视觉稿。
- 新增与修改的文案键见 UX-COPY 0.6「0.6 修订要点」。待决问题未新增。

## 0.6 修订要点

- **每单限购**（Kelvin 2026-10-01 决定，REQUIREMENTS 1.11「访客与会员流程」第 1 条、DESIGN 1.10「计价、优惠、积分与库存」第 8 条）：P03 显示 `[detail.max_per_order]`；同一商品各规格在购物车中的件数合计达到限购，或购物车已有 20 行而所选规格不在其中时，加入购物车按钮禁用并显示 `[detail.limit_reached]` 或 `[detail.cart_full]`。P04 数量 (+) 在达到限购时禁用；计价接口标为超出限购的行下显示 `[cart.over_limit]`。A04 每件商品加 `[admin.max_per_order]`。
- **精选商品**（Kelvin 2026-10-01 决定，REQUIREMENTS 1.11「店铺装修」）：A08 新增精选商品挑选（最多 4 件，可排序、移除）；P01「精选商品」区块按挑选顺序只显示仍上架的商品，未挑选或都已下架时显示最新的 4 件。
- 新增文案键见 UX-COPY 0.5「0.5 修订要点」。待决问题未新增。

## 0.5 修订要点

- **P09**：游客订单（下单时不是会员的订单，含之后被会员认领的）在前台订单详情的金额明细中不显示 `[checkout.summary_coupon]`、`[checkout.summary_points]` 两行，查单模式与会员模式相同；会员下单的订单照常显示。后台 A02 不变（Kelvin 2026-09-30 决定，REQUIREMENTS 1.10「访客与会员流程」第 3 条）。

## 0.4 修订要点

- **店铺装修**（REQUIREMENTS 1.9「店铺装修」）：新增后台 A08；全局框架写明主题、深浅色与标志图；页面地图与后台导航加 A08；「演示提示汇总」写明装修不能隐藏或弱化任何演示提示。
- **P01**：主视觉、演示怎么玩、按分类浏览、精选商品四个区块的顺序与显隐由 A08 决定；★ `[home.demo_hint]` 从「精选商品」旁移到页头下方、所有区块之前，不属于任何区块，不可隐藏。
- **P01、P02 价格**：商品有多于一个启用规格时一律显示 `[list.price_from]`，即使各规格同价（Kelvin 2026-09-30 决定，REQUIREMENTS 1.9「访客与会员流程」第 1 条）。
- **P03**：缩略图与手机版主图滑动的读屏标签用新键 `[detail.a11y_image]`。
- **P05**：游客结账的摘要不显示 `[checkout.summary_coupon]`、`[checkout.summary_points]` 两行，会员照常显示（Kelvin 2026-09-30 决定，REQUIREMENTS 1.9「访客与会员流程」第 3 条）。
- 新增文案键见 UX-COPY 0.4「0.4 修订要点」。待决问题未新增；店铺装修的规则随 REQUIREMENTS 1.9 一并候批。

## 0.3 修订要点

- **依据更新**：改为 `docs/REQUIREMENTS.md` 1.8 与 `docs/DESIGN.md` 1.9；各「金额与个人资料元素」表头改为「依据 DESIGN 1.9 小节」，小节标题不变。
- **落实 Kelvin 2026-09-30 的决定**（记录见 `docs/HANDOFF.md`）：Q16 结账第 1 步国家码下拉默认 +60、游客收货电话即第 1 步号码（依据 DESIGN 1.9「权限与资料保护」）；Q17 会员可从 P09 会员模式 `[account.order_pay]` 回到 P06 继续支付或取消；Q18 隐私页对外披露注销后的备份残留（`privacy.member_backup` 改写并定稿）；Q19「我的优惠券」列出所有启用中且在有效期内的券，对全部会员相同；Q11 接受剩余风险；Q14 马来文先上线、上线后审校。线框、说明与表格中 Q16–Q19 的待决标注全部去掉。
- **V1 重设密码与注销确认**：短信发送失败或号码不在白名单时不再显示游客文字或 `[auth.continue_guest]`，改用新键 `[auth.sms_not_sent_no_change]`；结账、注册、短信登录三种用途不变。V1、P12、P13 同步。
- **P12 忘记密码**：未注册号码通过短信验证后不创建账号，显示新键 `[auth.reset_not_registered]` 与去 P11 的 `[common.nav_register]`。
- **文案**：`privacy.browser_access` 改写，不再让人理解为该单以后只能在本浏览器打开；前台不再用「现金 / cash / tunai」指不含积分抵扣的实付金额（`order.cash_paid`、`refund.submit_hint`），`account.points_pending` 中文改写；`home.how_2` 不再暗示马新号码可选择游客结账。
- **小修**：P11 入口不再写「页头 Register」；P08 入口删去 P14；手机版结账顶部折叠摘要（及底部固定栏）在选定收货国家前显示商品小计而不是合计；「阅读说明」写明倒计时是 P06 的 15 分钟支付时限，不是 30 分钟的凭据。
- 待决问题：Q11、Q14、Q16–Q19 转为已决；未发现与 DESIGN 1.9 或 REQUIREMENTS 1.8 冲突之处，未新增待决问题。

## 0.2 修订要点

- **结账先填手机号**（P05）：马来西亚、新加坡号码依次经人机挑战与短信验证码，通过后自动注册或登录，再以会员身份继续；白名单外号码走游客流程；短信无法送达或停发时可改为游客下单，验证码错误不降级；已登录会员不再验证，收货电话默认会员手机号且可改。手机号旁告知自动注册与保留期限，收货表单旁告知长期保存；删除全部「30 天后匿名化」文案，不设勾选框，不设访客删除收货资料的入口。
- **两种订单授权**：游客下单后的短期凭据 `[G]`（P06、P07）与查单授权 `[L]`（P08–P10），各自只在当前浏览器、只针对该单、30 分钟内有效，能做的操作互不重叠。
- **支付页取消**（P06）：游客与会员都可取消待支付订单。
- **登录**（P12）：密码登录与短信验证码登录两种方式；注册（P11）与短信登录共用同一短信验证流程 V1。
- **会员中心**（P13）：首次设置密码、「我的订单」确认收货与申请退款（P09、P10 会员模式）、「我的优惠券」列出可用公开券与使用记录、以短信验证码确认注销。
- 落实 Kelvin 对待决问题的决定（演示信用卡/借记卡、「仅英文」标签、参考外币只在结账显示、WhatsApp 未配置时隐藏、后台不设导出）；待决问题更新并新增 Q16–Q19。

## 0. 阅读说明

- 线框为文本线框，只表达区块、顺序与操作，不表达视觉样式。`[key]` 指 [UX-COPY.md](UX-COPY.md) 的文案键；`( … )` 是按钮；`[____]` 是输入框；`<…>` 是运行时数据。
- **凡向用户显示的界面文字（含后台）都写成 `[key]`，在 UX-COPY 中有英、中、马三列。** 线框里其余不在 `[ ]` 内的中文——如「错误：」「提交中：」「会员：」「游客：」「底部固定：」「--- 失败时替换为 ---」「（常驻，不可关闭）」「☰ 导航」「→ P09」——都是给审阅者的**标注，不向用户显示**。
- `<…>` 运行时数据包括商品名称、分类、规格名与规格值、描述（来自后台维护的商品三语文案，见 UX-COPY「约定」）以及订单号、日期、数量、SKU 等；`[图]`、`[主图]`、`[缩略图]` 是图片区块；`☐`、`( )`、`▾`、`▸`、`(-)`、`(+)`、`(‹)`、`(›)`、`⏱`、`✓`、`✗`、`☰` 是无文字的控件或图标，读屏标签用 `common.a11y_*` 与 `common.nav_menu`。品牌字样 `ACUVEN SHOP`，以及 `WhatsApp`、`SKU`、`RM`、`MYR`、`+60`、`+65` 等不翻译。
- **订单号统一标 `[K]`**：订单号是查单凭据之一，线框中凡出现订单号（`<订单号>`、含 `{orderNo}` 的 `order.title` 与 `account.coupon_used_on`、查单输入框、后台订单号列与搜索框）都标 `[K]`，并在该页表格列出 K 行，依据 DESIGN 1.9「权限与资料保护」（订单号不出现在公共索引或分析事件；与电话合起来可看到完整收货资料）。
- **两种订单授权分别标 `[G]` 与 `[L]`**，依据 DESIGN 1.9「权限与资料保护」：
  - `[G]` **游客短期凭据**：每张游客订单（白名单外号码，以及白名单号码短信无法送达或停发时的降级下单）创建成功后，服务端只给当前浏览器发一个不可猜测、30 分钟有效、仅限该单的凭据；只用于该单的模拟支付、失败重试、取消与结果页，这些页面可显示该单收货资料原文；不能用于确认收货、退款或其他订单。
  - `[L]` **查单授权**：以订单号加电话查单通过后，仅对该单在本浏览器保持 30 分钟；只能查看该单、确认收货、申请退款，不能支付或取消；查其他订单须重新输入该单的订单号和电话；过期须重新查单。
  - 两者都是服务端会话，经 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，不交给页面脚本，不写入浏览器持久存储、网址、Referer、日志、分析事件或错误回显；支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌。一个浏览器可同时持有多张订单的授权，各自独立到期。**页面上不显示任何凭据内容，也不显示凭据的剩余时间。** P06、P07 的 `[pay.expires]` 倒计时是该单 15 分钟的支付时限（到时未付自动取消），不是 30 分钟的凭据有效期。
- **默认语言英文**，页头可切换中文、马来文（见 UX-COPY「约定」）。
- 金额与个人资料元素在线框中用 `[M1]`（金额）、`[P1]`（个人资料）、`[K]`、`[G]`、`[L]` 等标记，并在每页「金额与个人资料元素」表中逐处写出所依据的 `docs/DESIGN.md` 1.9 小节标题。DESIGN 1.9 的小节为：「边界与原则」「数据模型」「计价、优惠、积分与库存」「订单与退款状态」「失败、并发与重试」「权限与资料保护」「资料保留」「上线依赖与设计闸门」（原「保留与匿名化」一节自 1.8 起改名为「资料保留」；1.9 未改小节标题）。
- 所有金额由服务端计算后返回，前端只显示，不在浏览器计算价格、优惠、积分、运费或退款（DESIGN 1.9「数据模型」`Cart`、「计价、优惠、积分与库存」）。
- 页面路径仅作示意；**任何路径、查询参数与分析事件都不得包含订单号或电话**（DESIGN 1.9「权限与资料保护」：订单号不出现在公共索引或分析事件；应用日志不记录订单查询参数）。
- 本稿不写任何真实联系方式。WhatsApp 入口只写占位 `{{WHATSAPP_CONTACT_LINK}}`，上线前由私有配置提供（DESIGN 1.9「上线依赖与设计闸门」）；**配置缺失时隐藏所有 WhatsApp 按钮与联系段落，不显示占位文字**（Q10 已决）。

## 1. 全局框架

所有前台页面共用：

- **演示横幅**：页头上方常驻、不可关闭，桌面显示 `[common.demo_banner]`，手机显示 `[common.demo_banner_short]`。这是每一页的基础演示提示；各页另有本页演示提示（★）。
- **页头**：品牌、搜索框、导航（商品、查询订单、登录/会员中心）、语言切换 EN | 中文 | BM、购物车数量。页头「查询订单」是全站导航，进入 P08 须重新输入订单号和电话，不构成任何订单的免验证入口。
- **页脚**：`[common.footer_demo]`、`[common.nav_privacy]` 链到 P14、`[common.whatsapp_cta]` 链到占位 `{{WHATSAPP_CONTACT_LINK}}`（配置缺失时隐藏）。不设站内联系表单。
- **网络中断**：下单、模拟支付、取消订单、确认收货、退款申请提交后如网络中断，先显示 `[common.network_check]` 并查询原订单或申请状态，再允许重试；按钮提交期间禁用（DESIGN 1.9「失败、并发与重试」）。
- **限流/服务不可用**：查单、登录、短信被拒时显示 `[common.rate_limited]` 或 `[common.service_unavailable]`；商品浏览继续可用（DESIGN 1.9「失败、并发与重试」）。结账时短信停发另有降级提示，见 V1 与 P05。
- **短信验证开关**（0.7，DESIGN 1.11「边界与原则」）：A09 关闭短信验证（默认）时全站不发短信，前台各短信入口按 V1「短信验证开关关闭时」显示暂停提示，结账以游客进行；页头导航不变。
- **店铺装修**（0.4，REQUIREMENTS 1.9「店铺装修」）：所有前台页面按 A08 保存的主题与主色显示，版式、区块内容、流程与文案不因主题改变；每款主题有浅色与深色，按访客设备设置自动切换，页面不设切换控件。管理员上传标志图后，页头与页脚的品牌 `ACUVEN SHOP` 显示为该图（读屏文字仍为 `ACUVEN SHOP`），未上传时显示文字。演示横幅与各页演示提示在所有主题下照常显示。

桌面框架：

```text
+-----------------------------------------------------------------------+
| [common.demo_banner]                                 （常驻，不可关闭）|
+-----------------------------------------------------------------------+
| ACUVEN SHOP  [list.search_placeholder_______]([common.search])         |
|   [common.nav_shop]  [common.nav_track]                               |
|   [common.nav_login] 或 [common.nav_account]   [common.nav_cart]       |
|   [common.lang_en] | [common.lang_zh] | [common.lang_ms]              |
+-----------------------------------------------------------------------+
|                                                                       |
|                          <页面主体>                                    |
|                                                                       |
+-----------------------------------------------------------------------+
| [common.footer_demo]                                                  |
| [common.nav_privacy]        ( [common.whatsapp_cta] → 占位链接 )       |
|                             （WhatsApp 未配置时整个按钮隐藏）           |
+-----------------------------------------------------------------------+
```

手机框架：

```text
+--------------------------------+
| [common.demo_banner_short]     |
+--------------------------------+
| ☰  ACUVEN SHOP  <当前语言>▾      |
|              [common.nav_cart] |
| [list.search_placeholder___]   |
+--------------------------------+
|        <页面主体，单列>          |
+--------------------------------+
| [common.footer_demo]           |
| [common.nav_privacy]           |
| ( [common.whatsapp_cta] )      |
|  （未配置时隐藏）                 |
+--------------------------------+
☰ 菜单（读屏标签 [common.nav_menu]）：[common.nav_shop] / [common.nav_track] / [common.nav_login] 或 [common.nav_account] / [common.nav_privacy] / [common.lang_en] · [common.lang_zh] · [common.lang_ms]
<当前语言>▾ 显示当前语言的 [common.lang_*]，点开即上述三项。
```

### V1 短信验证组件（P05、P11、P12、P13 共用）

DESIGN 1.9「权限与资料保护」：注册、短信登录与结账验证是同一个短信验证流程——验证通过后号码已注册则登录，未注册则创建（无密码）会员并登录；密码重设与注销确认也经短信验证。「失败、并发与重试」：结账验证、短信登录、注册与密码重设共用同一白名单、人机挑战、限流和每日预算。本组件按用途嵌入各页，只换标题与成功后的去向：

| 用途 | 嵌入页 | 成功后 |
| --- | --- | --- |
| 结账验证 | P05 第 2 步 | 显示 `[checkout.verified_member]` 或 `[checkout.verified_new]`，以会员身份进入 P05 第 3 步 |
| 注册 / 短信登录 | P11、P12 短信登录页签 | 显示 `[auth.verified_login]` 或 `[auth.verified_registered]`，去来源页或 P13 |
| 重设密码 | P12 忘记密码 | 号码已注册：显示新密码输入；号码未注册：不创建账号，显示 `[auth.reset_not_registered]` 与 `[common.nav_register]` → P11；见 P12 |
| 注销确认 | P13 设置 | 显示 `[account.delete_confirm]` 按钮，见 P13 |

重设密码与注销确认两种用途不创建账号，也不提供游客选项；短信发送失败或号码不在白名单时只显示 `[auth.sms_not_sent_no_change]`（短信未发出、账号未作任何更改、请稍后再试）。

**短信验证开关关闭时（0.7，DESIGN 1.11「边界与原则」「权限与资料保护」）**：注册 / 短信登录、重设密码两种用途的整个组件替换为下面的暂停提示，不显示号码输入、人机挑战与发送按钮；结账验证不出现（见 P05）；注销确认改为 P13 的密码或会话确认，不经短信。开关在流程途中被关闭时，服务端拒绝之后提交的验证码（不登录、不注册、不重设密码、不确认注销），组件改显示 `[auth.sms_paused]`；在结账中则回到 P05 第 1 步按关闭状态继续。

```text
| [auth.sms_paused]                                                     |
| ([auth.continue_guest])                  （仅注册 / 短信登录）          |
```

```text
| [auth.phone] [P1 <国家码> ▾][P1 ______________]  （结账以外只列 +60、+65）|
| [auth.sms_scope]                                                      |
| [auth.claim_notice]            （仅结账验证、注册、短信登录显示）        |
| [auth.challenge]                                                      |
| <托管人机挑战组件>                                                      |
| ( [auth.send_code] )                                                  |
| [auth.code_sent]                                                      |
| [auth.code] [______]   ( [auth.verify_submit] )   ([auth.resend_code]) |
| 提交中：按钮禁用                                                       |
| 错误（不降级，留在本组件）：[auth.challenge_failed] / [auth.code_wrong] / |
|   [auth.code_too_many] / [common.rate_limited]                        |
| 号码不在白名单：                                                        |
|   注册 / 短信登录 → [auth.not_supported_country]                       |
|   重设密码 / 注销确认 → [auth.sms_not_sent_no_change]（无游客文字与按钮） |
| 短信无法送达或停发（服务端判定）：                                       |
|   结账验证 → 见 P05 [checkout.sms_unavailable]                         |
|   注册 / 短信登录 → [auth.sms_failed] ([auth.continue_guest])          |
|   重设密码 / 注销确认 → [auth.sms_not_sent_no_change]（无游客文字与按钮） |
```

说明：`<托管人机挑战组件>` 由人机挑战托管服务渲染，其内文字不在 UX-COPY 中，按当前界面语言请求该服务的对应语言。人机挑战在发送短信前完成，后端核验令牌。**验证码错误或超过尝试次数不降级为游客**；是否属于「无法送达或停发」、能否降级，由服务端判定并记录，前端只按响应显示对应提示（DESIGN 1.9「失败、并发与重试」）。短信失败不创建已验证账号。

V1 金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| P1 | 手机号，服务端规范化为 E.164；白名单马来西亚、新加坡；`{phoneMasked}` 只显示部分号码；不写入日志 | 「边界与原则」「权限与资料保护」「失败、并发与重试」 |
| — | 人机挑战在发送短信前；限流与每日预算；Redis 或 MySQL 不可用时停发 | 「失败、并发与重试」 |
| — | 通过后已注册则登录，未注册则创建无密码会员并登录；随后自动认领该号码近 30 天、未认领的游客订单，不补发积分 | 「权限与资料保护」「数据模型」（`Member` / `VerificationAttempt`）「计价、优惠、积分与库存」（第 3 条） |
| — | 不存验证码，只存提供方请求 ID、结果与限流元数据 | 「数据模型」（`Member` / `VerificationAttempt`）「失败、并发与重试」 |

## 2. 页面地图

| 编号 | 页面 | 示意路径 | 主要去向 |
| --- | --- | --- | --- |
| P01 | 首页 | `/` | P02、P03、P14、WhatsApp 占位 |
| P02 | 商品列表（搜索、分类、属性筛选） | `/products` | P03 |
| P03 | 商品详情（规格、数量、加入购物车） | `/products/<slug>` | P04、P02 |
| P04 | 购物车 | `/cart` | P05、P02 |
| P05 | 结账（手机号与短信验证、收货资料、优惠券、积分、运费、合计） | `/checkout` | P06、P12、P14 |
| P06 | 模拟支付（含取消待支付订单） | `/pay` | P07 |
| P07 | 模拟支付结果（成功/失败/已取消） | `/pay/result` | P06（重试）、P09（会员）、P08（游客过期后）、P02、P11 |
| P08 | 订单查询 | `/track` | P09 |
| P09 | 订单详情（确认收货、退款入口；查单模式 / 会员模式） | `/track/order`、`/account/order` | P10、P08、P13、P06（会员待支付订单） |
| P10 | 退款申请（查单模式 / 会员模式） | `/track/order/refund`、`/account/order/refund` | P09 |
| P11 | 注册（短信验证，V1） | `/register` | P13、来源页、P05（游客继续） |
| P12 | 登录（密码 / 短信验证码）与忘记密码 | `/login`、`/forgot-password` | P13、来源页、P11 |
| P13 | 会员中心（我的订单、积分、优惠券、密码、注销） | `/account` | P09、P06、P12、P01 |
| P14 | 隐私说明 | `/privacy` | 来源页、WhatsApp 占位 |
| A01 | 后台登录 | `/admin/login` | A02 |
| A02 | 后台订单与模拟发货 | `/admin/orders` | A03 |
| A03 | 后台退款审核 | `/admin/refunds` | A02 |
| A04 | 后台商品、图片、规格与库存 | `/admin/products` | A07 |
| A05 | 后台优惠券 | `/admin/coupons` | — |
| A06 | 后台运费区与演示汇率 | `/admin/shipping` | — |
| A07 | 后台库存重置结果 | `/admin/stock-resets` | A04 |
| A08 | 后台店铺装修（0.4） | `/admin/store-design` | — |
| A09 | 后台站点设置（0.7） | `/admin/settings` | — |

会员订单详情路径 `/account/order` 不带订单号；页面以不含订单号的内部引用在请求体中取单，服务端按会员会话校验归属。

主流程：P01 → P02 → P03 → P04 → P05（先填手机号；马新号码短信验证后以会员继续，其他号码以游客继续）→（下单）→ P06 →（成功/失败/取消）→ P07 →（失败重试回 P06）→ 管理员 A02 模拟发货 → 游客 P08 → P09（查单模式）/ 会员 P13 → P09（会员模式）→ 确认收货 → P10（退款）→ 管理员 A03 审核 → P09 显示结果。

---

## P01 首页

- **目的**：一眼说明这是演示站，引导访客走完整购物流程；提供 WhatsApp 联系入口（占位）。
- **入口**：直接访问；任意页的品牌标志。
- **去向**：P02（开始购物、分类、搜索）；P03（精选商品）；P14；WhatsApp 占位链接（未配置时隐藏）。
- **演示提示**：常驻横幅；★ `[home.demo_hint]`（0.4 起位于页头下方、所有区块之前，不可隐藏）；「演示怎么玩」四步。

桌面（区块按 A08 的默认顺序；各区块的顺序与显隐由 A08 决定，0.4）：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| ★ [home.demo_hint]                        （固定位置，不属于任何区块）  |
+---- 区块：主视觉 ---------------------------------------------------+
| [home.hero_title]                              [图][图][图]（装饰展台）|
| [home.hero_body]                                                      |
| ( [home.hero_cta] )                                                   |
+---- 区块：演示怎么玩 -------------------------------------------------+
| [home.how_title]                                                      |
|  ① [home.how_1]  ② [home.how_2]  ③ [home.how_3]  ④ [home.how_4]      |
+---- 区块：按分类浏览 -------------------------------------------------+
| [home.categories]                                                     |
|  [图]<分类>  [图]<分类>  [图]<分类>                                     |
|  [图]<分类>  [图]<分类>  [图]<分类>                                     |
+---- 区块：精选商品 ---------------------------------------------------+
| [home.featured]                                                       |
|  [图]<名称>[M1]  [图]<名称>[M1]  [图]<名称>[M1]  [图]<名称>[M1]          |
+-----------------------------------------------------------------------+
| <页脚，含 WhatsApp 占位；未配置时隐藏按钮>                              |
```

手机：

```text
| [common.demo_banner_short]     |
| <页头>                         |
| ★ [home.demo_hint]（固定）      |
| --- 区块：主视觉 ---            |
| [home.hero_title]              |
| [home.hero_body]               |
| ( [home.hero_cta] )            |
| [图][图][图]（装饰展台）         |
| --- 区块：演示怎么玩 ---         |
| [home.how_title]               |
|  ① [home.how_1]                |
|  ② [home.how_2]                |
|  ③ [home.how_3]                |
|  ④ [home.how_4]                |
| --- 区块：按分类浏览 ---         |
| [home.categories]              |
| [图]<分类> [图]<分类> …（横向滑动）|
| --- 区块：精选商品 ---           |
| [home.featured]                |
| [图]<名称>[M1] | [图]<名称>[M1] |
| <页脚>                         |
```

说明（0.4）：

- 四个区块的顺序与显隐按 A08 保存的设置；隐藏的区块整块不渲染。四个都隐藏时页面只剩演示横幅、页头、★ 提示与页脚。区块内的文案仍全部来自文案键，不随装修改变。
- 主视觉右侧三个图块是装饰性的示例商品图，不是链接；其形状随主题变化。
- 「精选商品」区块（0.6）显示 A08 挑选的商品（最多 4 件），按挑选顺序只显示仍上架的；未挑选或挑选的都已下架时，显示按最新排序的前 4 件。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 商品卡价格（仅 MYR；商品有多于一个启用规格时一律 `[list.price_from]`，即使各规格同价，0.4；不显示参考外币，Q9 已决） | 「数据模型」「边界与原则」 |

---

## P02 商品列表

- **目的**：浏览、搜索、按分类及属性筛选示例商品。
- **入口**：P01 各入口；页头搜索；页头「Shop」。
- **去向**：P03；清除筛选后留在本页。
- **演示提示**：常驻横幅；★ `[list.demo_hint]`；售罄显示 `[list.out_of_stock]`（示例库存每日重置）。

桌面：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| [list.title]              ★ [list.demo_hint]                          |
+------------------+----------------------------------------------------+
| [list.filter_title]| [list.results_count]        [list.sort] ▾        |
| [list.filter_category]                                                |
|  ☐ <分类>        | [图] <名称>        [图] <名称>      [图] <名称>     |
|  ☐ <分类>        |      [M1]               [M1]             [M1]     |
| <规格名>         |                        [list.out_of_stock]         |
|  ☐ <值> ☐ <值>   | [图] <名称>        [图] <名称>      [图] <名称>     |
| <规格名>         |      [M1]               [M1]             [M1]     |
|  ☐ <值> ☐ <值>   |                                                    |
| ([list.filter_clear])  (‹) 1 2 3 (›)                                  |
（<规格名> 如颜色、尺寸；(‹)(›) 读屏标签 [common.a11y_page_prev] / [common.a11y_page_next]）
+------------------+----------------------------------------------------+
空结果：[list.empty] ([list.filter_clear])
```

手机：

```text
| [common.demo_banner_short]     |
| <页头含搜索>                    |
| [list.title]                   |
| ★ [list.demo_hint]             |
| ([list.filter_title]) ([list.sort]▾) |
| [list.results_count]           |
| [图]<名称>     | [图]<名称>     |
|     [M1]       |     [M1]       |
| [图]<名称>     | [图]<名称>     |
|  [list.out_of_stock]           |
| ( [list.load_more] )           |
+-- 筛选抽屉（全屏）-------------+
| [list.filter_category] ☐…      |
| <规格名> ☐…                    |
| ([list.filter_clear]) ([list.filter_apply]) |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 商品卡价格与「价格」排序，仅显示 MYR，不显示参考外币（Q9 已决）；商品有多于一个启用规格时一律 `[list.price_from]`，即使各规格同价（0.4） | 「数据模型」「边界与原则」 |

---

## P03 商品详情

- **目的**：查看图片与描述，选择颜色、尺寸等规格与数量，加入购物车。
- **入口**：P01 精选、P02 商品卡。
- **去向**：P04（查看购物车）；P02（返回）。
- **演示提示**：常驻横幅；★ `[detail.demo_hint]`；`[detail.stock_left]` 标明示例库存。

桌面：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| 面包屑：[common.nav_shop] / <分类> / <名称>                            |
+-------------------------------+---------------------------------------+
| [主图]                        | <商品名称>   [detail.english_only]?   |
|                               | [M1] RM <所选规格单价>                 |
| [缩略图][缩略图][缩略图]        | [detail.options]                      |
|                               |  <规格名>: (<值>) (<值>) (<值>)         |
|                               |  <规格名>: (<值>) (<值>) (<值>)         |
|                               | [detail.quantity]  ( - ) 1 ( + )      |
|                               | [detail.max_per_order]                |
|                               | [detail.stock_left]                   |
|                               | ( [detail.add_to_cart] )              |
|                               | ★ [detail.demo_hint]                  |
+-------------------------------+---------------------------------------+
| [detail.description]                                                  |
| <描述>                                                                |
加入后提示条：[detail.added] ( [detail.view_cart] )
未选全规格点加入：[detail.select_all_options]
达到每单限购（0.6）：( [detail.add_to_cart] ) 禁用，旁边显示 [detail.limit_reached]
购物车已有 20 行且所选规格不在其中（0.6）：( [detail.add_to_cart] ) 禁用，旁边显示 [detail.cart_full]
```

手机：

```text
| [common.demo_banner_short]     |
| <页头>                         |
| [主图，可左右滑动]               |
| <商品名称> [detail.english_only]? |
| [M1] RM <单价>                 |
| [detail.options]               |
|  <规格名>: (<值>)(<值>)(<值>)   |
|  <规格名>: (<值>)(<值>)(<值>)   |
| [detail.quantity] (-) 1 (+)    |
| [detail.max_per_order]         |
| [detail.stock_left]            |
| ★ [detail.demo_hint]           |
| [detail.description] ▾         |
+--------------------------------+
| 底部固定：( [detail.add_to_cart] ) |
```

说明：当前语言缺少商品文案、回退英文时，在商品名称旁显示「仅英文」标签 `[detail.english_only]`（Q8 已决）。桌面缩略图可点选切换主图，手机主图可左右滑动；每张图的读屏标签用 `[detail.a11y_image]`（0.4 新增）。

每单限购（0.6）：`[detail.max_per_order]` 显示该商品的限购件数。同一商品所有规格在本浏览器购物车中的件数合计计入限购；数量 (+) 的上限是限购件数减去购物车中该商品已有的件数，所选规格已在购物车中时加入即合并件数。该商品已达限购时加入按钮禁用并显示 `[detail.limit_reached]`；购物车已有 20 行且所选规格不在其中时加入按钮禁用并显示 `[detail.cart_full]`。两处都只是提示，下单时由服务端再次校验（DESIGN 1.10「计价、优惠、积分与库存」第 8 条）。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 所选规格的 MYR 单价；切换规格时由服务端数据刷新；不显示参考外币 | 「数据模型」「边界与原则」 |
| — | 每单限购件数与购物车 20 行上限：只按本浏览器购物车提示并禁用按钮，下单时服务端再校验（0.6） | 「计价、优惠、积分与库存」（第 8 条，DESIGN 1.10 候批稿） |
| — | 库存剩余只作提示，加入购物车不预留库存 | 「计价、优惠、积分与库存」（第 6 条） |

---

## P04 购物车

- **目的**：查看、修改已选商品规格与数量，进入结账。
- **入口**：页头购物车；P03 加入后的提示条。
- **去向**：P05；P02（继续购物）。
- **演示提示**：常驻横幅；★ `[cart.demo_hint]`；`[cart.price_recheck]`。

桌面：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| [cart.title]                                  ★ [cart.demo_hint]      |
+-----------------------------------------------+-----------------------+
| [图] <名称> / <规格>   (-) 2 (+)   [M1]  ([cart.remove]) | [cart.subtotal] [M2] |
| [图] <名称> / <规格>   (-) 1 (+)   [M1]  ([cart.remove]) | [cart.shipping_later] |
| [cart.item_changed]（服务端校验有变化时）      | [cart.price_recheck]  |
|                                               | ( [cart.checkout] )   |
|                                               | ([cart.continue])     |
+-----------------------------------------------+-----------------------+
空：[cart.empty] ([cart.continue])
```

手机：

```text
| [common.demo_banner_short]     |
| <页头>                         |
| [cart.title]                   |
| ★ [cart.demo_hint]             |
| [图] <名称>                     |
|  <规格>  (-) 2 (+)  [M1]        |
|  ([cart.remove])               |
| [图] <名称> …                   |
| [cart.item_changed]?           |
| [cart.shipping_later]          |
| [cart.price_recheck]           |
+--------------------------------+
| 底部固定：[cart.subtotal] [M2]  |
|        ( [cart.checkout] )     |
```

说明（0.6）：同一商品各行的件数合计达到每单限购时，这些行的数量 (+) 禁用。计价接口把某商品的各行标为超出限购时，在这些行下显示 `[cart.over_limit]`（`{count}` 为接口返回的限购件数），并与其他变化一样显示 `[cart.item_changed]`；访客减少件数或移除后重新计价。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 行小计（单价 × 数量），由服务端按当前价格返回，不信任浏览器保存的价格 | 「数据模型」（`Cart`） |
| M2 | 商品小计（仅 MYR）；运费、优惠、积分不在此计算 | 「数据模型」（`Cart`）「计价、优惠、积分与库存」（第 1 条） |

---

## P05 结账

- **目的**：未登录访客先填手机号，马来西亚、新加坡号码经人机挑战与短信验证后自动注册或登录，以会员身份继续；白名单外号码以游客继续；然后填写收货资料，会员可用优惠券与积分，查看 MYR 小计、抵扣、示例运费、合计及参考外币金额，提交演示订单。
- **入口**：P04 `[cart.checkout]`。
- **去向**：P06（下单成功）；P12（已有密码的会员点 `[checkout.login_password]`，返回后保留购物车与已填内容）；P14（资料处理说明）。
- **演示提示**：常驻横幅；★ `[checkout.demo_hint]`；手机号旁 `[checkout.phone_notice]`（马新号码会收到验证短信并自动注册会员、手机号保留至注销、可在会员中心注销）；收货表单旁 `[checkout.form_notice]`（不真实扣款、不真实发货、收货资料会长期保存）；◆ 下单按钮旁 `[checkout.place_order_hint]`。**两处告知都常显，不设勾选框；不提供访客删除收货资料的入口。**

### 步骤与分支

| 访客状态 | 第 1 步 手机号 | 第 2 步 验证 | 第 3 步 收货与下单 |
| --- | --- | --- | --- |
| 未登录，马来西亚/新加坡号码 | 填手机号 | V1：人机挑战 → 短信验证码 → 注册或登录 | 会员表单（可用券与积分） |
| 未登录，马新号码，短信无法送达或停发（服务端判定） | 填手机号 | 显示 `[checkout.sms_unavailable]`，可点 `[checkout.continue_guest]` | 游客表单（降级） |
| 未登录，马新号码，验证码错误或超过尝试次数 | 填手机号 | 留在 V1，显示 `[auth.code_wrong]` / `[auth.code_too_many]`，**不显示游客选项** | —（不降级） |
| 未登录，白名单外号码 | 填手机号 | 不发短信，显示 `[checkout.guest_other_country]` | 游客表单 |
| 短信验证开关关闭（未登录访客的任何号码，含马新号码；0.7） | 填手机号，旁边显示 `[checkout.phone_notice_sms_off]` | 不发短信，显示 `[checkout.guest_sms_off]` | 游客表单 |
| 已登录会员 | 跳过 | 跳过（不再验证） | 会员表单，收货电话默认会员手机号，可改 |

桌面——第 1 步（未登录）：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| [checkout.title]                              ★ [checkout.demo_hint]  |
+---------------------------------------------+-------------------------+
| [checkout.phone_step_title]                 | [checkout.summary_title] |
| ┌ 手机号旁告知（常显，无勾选框）───────────┐  | <名称>/<规格> x2  [M1]   |
| │ [checkout.phone_notice]                  │  | <名称>/<规格> x1  [M1]   |
| │ ([checkout.form_notice_link] → P14)      │  |-------------------------|
| └──────────────────────────────────────────┘  | [cart.subtotal]    [M2] |
| [auth.phone] [P4 +60 ▾][P4 ______________]   | [cart.shipping_later]   |
|   [checkout.phone_step_hint]                |                         |
|   错误：[checkout.phone_invalid]             |                         |
| ( [checkout.phone_continue] )               |                         |
| [checkout.login_password] → P12             |                         |
+---------------------------------------------+-------------------------+
（国家码下拉列出所有国家，默认 +60；以 + 开头输入时以输入为准；之后选的收货国家不改变已判定的号码。Q16 已决，DESIGN 1.9「权限与资料保护」）
```

桌面——第 2 步（马来西亚/新加坡号码，嵌入 V1）：

```text
+---------------------------------------------+-------------------------+
| [checkout.phone_step_title]                 | <摘要同第 1 步>          |
|   [P4] <号码部分遮盖> ([checkout.phone_change]) |                      |
| [checkout.phone_notice]（常显）              |                         |
| [auth.claim_notice]                         |                         |
| [auth.challenge]                            |                         |
| <托管人机挑战组件>                             |                         |
| ( [auth.send_code] )                        |                         |
| [auth.code_sent]                            |                         |
| [auth.code] [______] ( [auth.verify_submit] ) ([auth.resend_code])    |
| 错误（不降级）：[auth.challenge_failed] / [auth.code_wrong] /            |
|   [auth.code_too_many] / [common.rate_limited]                        |
| --- 服务端判定短信无法送达或停发时 ---                                   |
| [checkout.sms_unavailable]                                            |
| ( [checkout.continue_guest] ) → 第 3 步游客表单                         |
| --- 验证通过 ---                                                       |
| ✓ [checkout.verified_member] 或 ✓ [checkout.verified_new]             |
|   → 页头变为 [common.nav_account]，进入第 3 步会员表单                  |
+---------------------------------------------+-------------------------+
白名单外号码：不出现本步，直接在第 3 步顶部显示 [checkout.guest_other_country]
```

桌面——第 3 步（收货资料与下单）：

```text
+---------------------------------------------+-------------------------+
| 游客：[checkout.guest_other_country] 或（降级时）[checkout.sms_unavailable] | [checkout.summary_title] |
|       [checkout.guest_notice]               | <名称>/<规格> x2  [M1]   |
| [checkout.recipient_title]                  | <名称>/<规格> x1  [M1]   |
| ┌ 收货表单旁提示（常显，无勾选框）─────────┐   |-------------------------|
| │ [checkout.form_notice]                   │ | [cart.subtotal]    [M2] |
| │ ([checkout.form_notice_link] → P14)      │ | [checkout.summary_coupon] [M3] |
| └──────────────────────────────────────────┘ | [checkout.summary_points] [M4] |
| [checkout.name]      [P1 ______________]    | [checkout.summary_shipping][M5]|
| [checkout.country]   [P2 <国家> ▾]           | [checkout.summary_total]  [M6] |
| [checkout.state_my]  [P2 <州属> ▾]（马来西亚）| 选定国家后：[M7] [common.fx_reference] |
|   或 [checkout.region] [P2 ________]（其他） |   [checkout.fx_note]      |
| [checkout.address]   [P1 ______________]    |   或 [checkout.fx_none]   |
| [checkout.postcode]  [P1 ______]            |                          |
| [checkout.phone]                            | ( [checkout.place_order] )|
|  游客：[P3] <第 1 步号码>（只读）([checkout.phone_change]) | ◆ [checkout.place_order_hint] |
|        [checkout.phone_lookup_hint]         |                          |
|  会员：[P3 <会员手机号，预填> ______]        |                          |
|        [checkout.member_phone_hint]         |                          |
|        [checkout.phone_hint]                |                          |
|        错误：[checkout.phone_invalid]        |                          |
|---------------------------------------------|                          |
| 会员：[checkout.coupon] [M3 ______] ([checkout.coupon_apply])          |
|       错误：[checkout.coupon_invalid]                                   |
|       [checkout.points] [M4 ______]  [checkout.points_available]       |
|       [checkout.points_not_shipping]                                   |
| 游客：[checkout.coupon_members_only] [checkout.points_guest]           |
+---------------------------------------------+-------------------------+
提交中：按钮禁用，显示 [checkout.submitting]
游客下单成功：服务端发 [G] 短期凭据（cookie）后进入 P06
```

手机（单列，摘要折叠在顶部）：

```text
| [common.demo_banner_short]     |
| [checkout.title]               |
| ★ [checkout.demo_hint]         |
| ▸ [checkout.summary_title]     |
|   未选收货国家：[cart.subtotal] [M2] |
|   选定收货国家后：[checkout.summary_total] [M6] |
|   （点开见明细）                 |
--- 第 1 步（未登录）---
| [checkout.phone_step_title]    |
| ┌──────────────────────────┐   |
| │[checkout.phone_notice]   │   |
| │([checkout.form_notice_link])│|
| └──────────────────────────┘   |
| [auth.phone]                   |
| [P4 +60▾][P4 ______________]   |
| [checkout.phone_step_hint]     |
| <错误>                          |
| ( [checkout.phone_continue] )  |
| [checkout.login_password]      |
--- 第 2 步（马新号码）---
| [P4] <号码遮盖>                 |
| ([checkout.phone_change])      |
| [checkout.phone_notice]        |
| [auth.claim_notice]            |
| <托管人机挑战组件>                 |
| ( [auth.send_code] )           |
| [auth.code_sent]               |
| [auth.code] [______]           |
| ( [auth.verify_submit] )       |
| ([auth.resend_code])           |
| <错误，不降级>                   |
| 或 [checkout.sms_unavailable]  |
|  ( [checkout.continue_guest] ) |
| 或 ✓ [checkout.verified_member]/[checkout.verified_new] |
--- 第 3 步 ---
| 游客：[checkout.guest_other_country]/[checkout.sms_unavailable] |
|      [checkout.guest_notice]   |
| [checkout.recipient_title]     |
| ┌──────────────────────────┐   |
| │[checkout.form_notice]    │   |
| │([checkout.form_notice_link])│|
| └──────────────────────────┘   |
| [checkout.name]    [P1 ____]   |
| [checkout.country] [P2 ▾]      |
| [checkout.state_my]/[checkout.region] [P2] |
| [checkout.address] [P1 ____]   |
| [checkout.postcode][P1 ____]   |
| [checkout.phone]               |
|  游客：[P3] <号码>（只读）        |
|   ([checkout.phone_change])    |
|   [checkout.phone_lookup_hint] |
|  会员：[P3 <预填> ____]          |
|   [checkout.member_phone_hint] |
|   [checkout.phone_hint]        |
| 会员：[checkout.coupon] [M3]     |
|  ([checkout.coupon_apply])     |
|  [checkout.points] [M4]        |
|  [checkout.points_available]   |
|  [checkout.points_not_shipping]|
| 游客：[checkout.coupon_members_only] |
|      [checkout.points_guest]   |
| [cart.subtotal]            [M2]|
| [checkout.summary_coupon]  [M3]|
| [checkout.summary_points]  [M4]|
| [checkout.summary_shipping][M5]|
| [checkout.summary_total]   [M6]|
| 选定国家后：[M7][common.fx_reference] |
| [checkout.fx_note]             |
+--------------------------------+
| 底部固定：未选收货国家 [cart.subtotal] [M2]，选定后 [checkout.summary_total] [M6] |
| ( [checkout.place_order] )     |
| ◆ [checkout.place_order_hint]  |
```

说明：

- 手机号旁告知与收货表单旁告知在桌面与手机都**常显**，不折叠、不设勾选框。页面不提供任何删除收货资料的入口。
- 第 1 步手机号（Q16 已决，DESIGN 1.9「权限与资料保护」）：国家码下拉列出所有国家、默认 +60；以 `+` 开头输入时以输入为准；服务端按所选或输入的国家码规范化并判定是否属于白名单，之后选的收货国家不改变已判定的号码。
- 游客的收货电话就是第 1 步的号码（即查单凭据），只读，下单时不按收货国家重新解析；要改须点 `[checkout.phone_change]` 回到第 1 步重新判定（改为马新号码则须验证）。以收货国家作为默认区号只用于会员改填的收货电话：会员的收货电话默认会员手机号，可改为其他号码或虚构号码，按收货国家补默认区号（`[checkout.phone_hint]`）。
- 手机版顶部折叠摘要与底部固定栏在选定收货国家前显示商品小计 `[M2]`，不显示合计；选定收货国家、服务端算出示例运费后才显示合计 `[M6]`。
- 游客结账（白名单外号码与降级下单）的摘要不显示 `[checkout.summary_coupon]` `[M3]` 与 `[checkout.summary_points]` `[M4]` 两行，桌面与手机相同；会员照常显示（Kelvin 2026-09-30 决定，0.4）。
- 服务端创建订单时强制执行：游客订单的收货电话属于白名单国家即拒绝，除非服务端记录显示该号码刚遇到短信无法送达或停发；前端游客表单不是这一规则的依据。
- 短信验证开关关闭时（0.7，DESIGN 1.11「权限与资料保护」）：第 1 步以 `[checkout.phone_notice_sms_off]` 代替 `[checkout.phone_notice]`，`[checkout.login_password]` 照常（已设密码的会员仍可密码登录）；号码按所选或输入的国家码规范化后显示 `[checkout.guest_sms_off]`，以游客表单进入第 3 步，马新号码也一样。服务端按下单时开关的当前值判定；开关在填表途中被打开时下单被拒，页面回到第 1 步重新判定。
- 参考外币金额 `[M7]` 只在选定收货国家后显示，改变国家或州属后运费与参考外币由服务端重算；未选国家时不显示 `[M7]`、`[checkout.fx_note]` 或 `[checkout.fx_none]`（Q9 已决）。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 摘要行金额（按服务端返回的价格快照） | 「计价、优惠、积分与库存」（第 1 条）「数据模型」（`Cart`） |
| M2 | 商品小计 | 「计价、优惠、积分与库存」（第 1 条） |
| M3 | 优惠券输入与抵扣额；仅会员；只抵商品额；游客的摘要不显示这一行（0.4） | 「计价、优惠、积分与库存」（第 1、2 条）「数据模型」（`Coupon` / `CouponUse`） |
| M4 | 积分输入、可用积分与抵扣额；100 积分抵 RM1；仅会员；不抵运费；游客的摘要不显示这一行（0.4） | 「计价、优惠、积分与库存」（第 1、3 条）「边界与原则」 |
| M5 | 示例运费（按国家，马来西亚按州属，其他国家兜底） | 「数据模型」（`ShippingRate` / `DemoFxRate`） |
| M6 | MYR 合计，不低于运费；不单列税费 | 「计价、优惠、积分与库存」（第 1 条）「边界与原则」 |
| M7 | 参考外币金额，按收货国家与固定演示汇率，只在选定收货国家后显示；该国无汇率时只显示 MYR | 「边界与原则」「数据模型」（`ShippingRate` / `DemoFxRate`） |
| P4 | 结账手机号（身份判定）：国家码下拉列出所有国家、默认 +60，以 `+` 开头时以输入为准，按所选或输入的国家码规范化为 E.164，之后选的收货国家不改变已判定的号码（Q16 已决）；马新号码经人机挑战与短信验证后注册或登录，白名单外号码游客下单；验证码错误不降级 | 「权限与资料保护」「边界与原则」「失败、并发与重试」 |
| — | 短信验证开关关闭时 `[checkout.phone_notice_sms_off]`：未登录访客无论号码都不发短信、以游客结账（已登录会员不经第 1 步，照常以会员结账）；服务端下单时按开关当前值判定（0.7） | 「边界与原则」「权限与资料保护」（DESIGN 1.11） |
| — | 手机号旁告知 `[checkout.phone_notice]`：自动注册、手机号保留至注销、可在会员中心注销；无勾选框 | 「权限与资料保护」「资料保留」 |
| — | 验证通过后自动认领该号码近 30 天游客订单 `[auth.claim_notice]`，不补发积分 | 「权限与资料保护」「计价、优惠、积分与库存」（第 3 条） |
| P1 | 收货人姓名、地址、邮编（自由文本，可虚构）；长期保存 | 「边界与原则」「数据模型」（`OrderRecipient`）「资料保留」 |
| P2 | 国家、州属/地区（决定运费、参考外币与会员收货电话的默认区号） | 「边界与原则」「权限与资料保护」 |
| P3 | 收货电话：游客为第 1 步号码（查单凭据，只读，下单时不按收货国家重新解析）；会员默认会员手机号，可改为其他或虚构号码，改填时以收货国家作为默认区号；只做格式校验 | 「权限与资料保护」 |
| — | 表单旁告知 `[checkout.form_notice]`：不真实扣款、不真实发货、收货资料长期保存；无勾选框；无访客删除入口 | 「权限与资料保护」「资料保留」 |
| G | 游客下单成功后发给当前浏览器的短期凭据（30 分钟、仅限该单） | 「权限与资料保护」 |
| — | 下单按钮：库存/券/积分预占，15 分钟未付自动取消；幂等提交；服务端拒绝未降级的白名单号码游客订单 | 「计价、优惠、积分与库存」（第 1、3、6 条）「失败、并发与重试」「权限与资料保护」 |

---

## P06 模拟支付

- **目的**：选择演示支付方式并点击「成功」或「失败」，不输入任何银行卡资料；可取消待支付订单；核对该单收货资料。
- **入口**：P05 下单成功；P07 失败后的 `[result.retry]`；会员另可从 P09 会员模式 `[account.order_pay]` 回到本页，继续支付或取消自己的待支付订单（Q17 已决）。
- **去向**：P07（成功/失败/已取消）。
- **访问授权**：游客凭该单短期凭据 `[G]`（当前浏览器、仅该单、30 分钟）；会员凭登录会话访问自己的订单。
- **演示提示**：常驻横幅；★ `[pay.demo_hint]`；◆ 成功/失败按钮旁 `[pay.action_hint]`；`[pay.expires]` 倒计时。

桌面：

```text
| [common.demo_banner]                                                  |
| [pay.title]   [common.demo_badge]            ★ [pay.demo_hint]        |
+-----------------------------------------------------------------------+
| [pay.order_no]  [K] <订单号>  ([common.copy])                          |
| [pay.save_order_no]                                                   |
| [pay.amount_due]  [M1] RM <合计>                                       |
| [pay.expires]  ⏱ <剩余分钟>                                            |
| 游客：[G] [pay.guest_access]                                           |
+-----------------------------------------------------------------------+
| [checkout.recipient_title]                                            |
|  [P1] <姓名> / <电话> / <地址> / <邮编> / <地区> / <国家>               |
+-----------------------------------------------------------------------+
| [pay.choose_method]                                                   |
|  ( ) [pay.method_card]   ( ) [pay.method_bank]   ( ) [pay.method_ewallet] |
|                                                                       |
|  ( [pay.simulate_success] )     ( [pay.simulate_failure] )            |
|  ◆ [pay.action_hint]                                                  |
|                                                                       |
|  ([pay.cancel_order])                                                 |
+-----------------------------------------------------------------------+
提交中：按钮禁用，显示 [pay.processing]
点取消后确认框：[pay.cancel_confirm] ( [pay.cancel_confirm_yes] ) ([pay.cancel_confirm_no])
--- 游客凭据过期或不属于该单时替换整页 ---
| [pay.session_expired]                                                 |
| ( [common.nav_track] ) → P08（须输入订单号和电话）                       |
```

手机：

```text
| [common.demo_banner_short]     |
| [pay.title] [common.demo_badge]|
| ★ [pay.demo_hint]              |
| [pay.order_no]                 |
| [K] <订单号> ([common.copy])    |
| [pay.save_order_no]            |
| [pay.amount_due]               |
| [M1] RM <合计>                  |
| [pay.expires] ⏱               |
| 游客：[G] [pay.guest_access]    |
| ▸ [checkout.recipient_title]   |
|   [P1] <姓名>/<电话>/<地址>     |
| [pay.choose_method]            |
| ( ) [pay.method_card]          |
| ( ) [pay.method_bank]          |
| ( ) [pay.method_ewallet]       |
| ◆ [pay.action_hint]            |
| ( [pay.simulate_success] )     |
| ( [pay.simulate_failure] )     |
| ([pay.cancel_order])           |
--- 过期时 ---
| [pay.session_expired]          |
| ( [common.nav_track] )         |
```

说明：不出现任何卡号、有效期、CVV 输入框；`[pay.method_card]` 写明无需输入卡号（Q6 已决）。参考外币金额只在结账页 P05 选定收货国家后显示，支付页不显示（Q9 已决）。**游客页面上不提供确认收货、退款或其他订单的入口**；凭据过期后只提示凭订单号和电话查单。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 应付金额（订单快照） | 「数据模型」（`Order` / `OrderItem`）「计价、优惠、积分与库存」（第 1 条） |
| P1 | 该单收货资料原文：游客仅凭该单短期凭据可见，会员凭会话可见自己的订单 | 「权限与资料保护」「数据模型」（`OrderRecipient`） |
| G | 游客短期凭据：当前浏览器、仅该单、30 分钟；只用于支付、失败重试、取消与结果页，不能用于确认收货、退款或其他订单；过期提示凭订单号和电话查单 | 「权限与资料保护」 |
| K | 订单号：查单凭据之一，不进路径、查询参数或分析事件 | 「权限与资料保护」 |
| — | 成功/失败按钮：幂等提交，失败停在 `awaiting_demo_payment` 可重试；写操作须 CSRF 令牌 | 「订单与退款状态」「失败、并发与重试」「权限与资料保护」 |
| — | 取消按钮：游客与会员都有；由下单者取消为 `demo_cancelled`，释放库存、券及积分预占 | 「订单与退款状态」「权限与资料保护」「计价、优惠、积分与库存」（第 1、3、6 条） |
| — | 倒计时：15 分钟未付自动取消并释放预占 | 「计价、优惠、积分与库存」（第 6 条）「订单与退款状态」 |

---

## P07 模拟支付结果

- **目的**：显示模拟支付成功、失败或订单已取消（超时或本人取消），并给出下一步。
- **入口**：P06 点击成功/失败/取消后；超时后再次打开支付页。
- **去向**：成功 → 会员 P09（`[result.track]`，会员模式）；游客只见 `[result.guest_next]` 说明，不设订单详情按钮；P02（`[result.continue]`）；游客可去 P11。失败 → P06（`[result.retry]`，同一订单）。已取消 → P02。游客凭据过期 → `[pay.session_expired]` 与 P08。
- **访问授权**：游客凭该单短期凭据 `[G]`；会员凭登录会话。
- **演示提示**：常驻横幅；★ `[result.demo_hint]`；成功文案写明未扣真实款项。

桌面：

```text
| [common.demo_banner]                                                  |
+---------------------------- 成功 ------------------------------------+
| ✓ [result.success_title]                                              |
| [result.success_body]                                                 |
| [pay.order_no] [K] <订单号> ([common.copy])   [pay.save_order_no]      |
| [pay.amount_due] [M1]                                                 |
| [checkout.recipient_title] [P1] <姓名>/<电话>/<地址>/<地区>/<国家>      |
| 会员：[result.points_earned] [M2]   ( [result.track] ) → P09 会员模式  |
| 游客：[G] [result.guest_next]   [result.guest_register] → P11          |
| ★ [result.demo_hint]                                                  |
| ([result.continue])                                                   |
+---------------------------- 失败 ------------------------------------+
| ✗ [result.failure_title]                                              |
| [result.failure_body]                                                 |
| [pay.expires] ⏱                                                       |
| ( [result.retry] )                                                    |
+---------------------------- 已取消 ----------------------------------+
| [result.cancelled] 或 [result.cancelled_by_you]   ([result.continue]) |
+---------------------------- 游客凭据过期 ----------------------------+
| [pay.session_expired]   ( [common.nav_track] ) → P08                  |
```

手机：

```text
| [common.demo_banner_short]     |
| ✓ [result.success_title]       |
| [result.success_body]          |
| [pay.order_no]                 |
| [K] <订单号> ([common.copy])    |
| [pay.save_order_no]            |
| [pay.amount_due] [M1]          |
| ▸ [checkout.recipient_title] [P1] |
| 会员：[result.points_earned] [M2] |
|  ( [result.track] )            |
| 游客：[result.guest_next]        |
|  [result.guest_register]       |
| ★ [result.demo_hint]           |
| ([result.continue])            |
--- 失败时替换为 ---
| ✗ [result.failure_title]       |
| [result.failure_body]          |
| [pay.expires] ⏱               |
| ( [result.retry] )             |
--- 已取消时 ---
| [result.cancelled] 或 [result.cancelled_by_you] |
| ([result.continue])            |
--- 游客凭据过期时 ---
| [pay.session_expired]          |
| ( [common.nav_track] )         |
```

说明：游客结果页**不提供确认收货、退款或其他订单的入口**，也不设直达 P09 的按钮；页头「查询订单」为全站导航，须重新输入订单号和电话。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 已模拟支付金额（快照） | 「数据模型」（`Order` / `OrderItem`、`PaymentAttempt` / `OrderEvent`） |
| M2 | 本单获得积分（整单现金实付每满 RM1 得 1 积分；游客不累积） | 「计价、优惠、积分与库存」（第 3 条） |
| P1 | 该单收货资料原文（游客凭短期凭据；会员凭会话） | 「权限与资料保护」「数据模型」（`OrderRecipient`） |
| G | 游客短期凭据：结果页可用，确认收货、退款须改用查单；过期提示凭订单号和电话查单 | 「权限与资料保护」 |
| K | 订单号：查单凭据之一，不进路径、查询参数或分析事件 | 「权限与资料保护」 |
| — | 重试不重复下单 | 「订单与退款状态」「失败、并发与重试」 |

---

## P08 订单查询

- **目的**：以订单号及下单电话查询订单（不使用短信验证码）。
- **入口**：页头「Track order」；P06/P07 游客凭据过期后；P09 `[order.lookup_another]` 与查单授权过期后。
- **去向**：P09（查询成功，查单模式）；失败留在本页。
- **演示提示**：常驻横幅；★ `[lookup.demo_hint]`；`[lookup.privacy_warning]`（剩余风险告知）；`[lookup.access_note]`（查单授权范围）。

桌面：

```text
| [common.demo_banner]                                                  |
| [lookup.title]                               ★ [lookup.demo_hint]     |
+-----------------------------------------------------------------------+
|   [pay.order_no]   [K ______________________]                         |
|   [lookup.phone]   [P1 ______________________]                        |
|                    [lookup.phone_hint]                                |
|   ( [lookup.submit] )                                                 |
|   [L] [lookup.access_note]                                            |
|   [lookup.privacy_warning]                                            |
|   错误：[lookup.not_found] / [common.rate_limited] / [common.service_unavailable] |
+-----------------------------------------------------------------------+
```

手机：

```text
| [common.demo_banner_short]     |
| [lookup.title]                 |
| ★ [lookup.demo_hint]           |
| [pay.order_no]                 |
| [K ______________________]     |
| [lookup.phone]                 |
| [P1 ______________________]    |
| [lookup.phone_hint]            |
| ( [lookup.submit] )            |
| [lookup.access_note]           |
| [lookup.privacy_warning]       |
| <错误提示>                      |
```

说明：订单号与电话只以请求体提交，不进路径或查询参数；每次打开本页输入框为空，不预填上一单。「不存在」「电话不符」统一显示 `[lookup.not_found]`，不泄露订单是否存在。收货资料长期保存，查单不随时间失效。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| K | 订单号输入：查单凭据之一，只以请求体提交 | 「权限与资料保护」 |
| P1 | 电话输入，按与下单相同的 E.164 规范化后与该单 `OrderRecipient` 比对 | 「权限与资料保护」「数据模型」（`OrderRecipient`） |
| L | 查单通过后授予的查单授权：当前浏览器、仅该单、30 分钟；只可查看、确认收货、申请退款；不能支付或取消 | 「权限与资料保护」 |
| — | 查询限流、防批量枚举、不缓存敏感响应；Redis 不可用时拒绝 | 「权限与资料保护」「失败、并发与重试」 |
| — | 收货资料长期保存，凭订单号与电话可随时查单；剩余风险 `[lookup.privacy_warning]` 随订单累积 | 「资料保留」「权限与资料保护」 |

---

## P09 订单详情（确认收货与退款入口）

- **目的**：查看订单状态、商品与金额、完整收货资料，确认收货，查看退款记录并进入退款申请。
- **两种模式**：
  - **查单模式 `[L]`**：从 P08 查询成功进入；只在当前浏览器、只针对该单、30 分钟内可查看、确认收货、申请退款；**不提供支付或取消**；查其他订单须经 `[order.lookup_another]` 回 P08 重新输入订单号和电话；过期显示 `[order.session_expired]`，须重新查单。
  - **会员模式**：从 P13「我的订单」或 P07 `[result.track]` 进入；凭会员会话，只能访问自己下单或认领的订单；确认收货与退款规则与查单模式相同（Q5 已决）；待支付订单另有 `[account.order_pay]`，回到 P06 继续支付或取消（Q17 已决）。
- **去向**：P10；P08（查单模式返回或查其他订单）；P13（会员模式返回）；P06（会员模式待支付订单 `[account.order_pay]`，继续支付或取消，Q17 已决）。
- **演示提示**：常驻横幅；★ `[order.demo_hint]`；确认收货旁 `[order.confirm_receipt_hint]`；查单模式 `[order.lookup_access]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [order.title] [K]   [order.current_status] [order.status_*]   ★ [order.demo_hint] |
| 查单模式：[L] [order.lookup_access]  ([order.lookup_another]) → P08    |
| 会员模式：([account.orders]) → P13                                    |
| [order.progress] [order.status_awaiting] → [order.status_paid] →      |
|   [order.status_packed] → [order.status_shipped] → [order.status_completed] |
+---------------------------------------------+-------------------------+
| [order.items]                               | [checkout.recipient_title] |
| <名称>/<规格> x2 [order.unit_price][M1]       | [P1] <姓名>             |
|        [order.cash_paid] [M2]               | [P1] <电话>             |
| <名称>/<规格> x1 …                           | [P1] <地址>/<邮编>      |
|---------------------------------------------|      <地区>/<国家>      |
| [cart.subtotal]            [M3]             |                         |
| [checkout.summary_coupon]  [M3]             |                         |
| [checkout.summary_points]  [M3]             |                         |
| [checkout.summary_shipping][M3]             |                         |
| [checkout.summary_total]   [M3]             |                         |
+---------------------------------------------+-------------------------+
| 已发货时：( [order.confirm_receipt] )  [order.confirm_receipt_hint]    |
| 已支付且在退款期内：( [order.request_refund] )  [order.refund_deadline] |
| [order.refunded_total] [M4]   [order.refundable_left] [M4]            |
| [order.refund_requests]                                               |
|  <日期> <商品 x数量> [M4] [order.refund_requested|approved|rejected]   |
| 全部已退时：[order.fulfilment_frozen]                                  |
| 待支付订单——查单模式：[order.lookup_no_pay]（无支付、取消按钮）           |
|            会员模式：( [account.order_pay] ) → P06（继续支付或取消，Q17 已决） |
--- 查单授权过期或不属于该单时替换整页 ---
| [order.session_expired]  ( [common.nav_track] ) → P08（须重新输入订单号和电话） |
```

手机：

```text
| [common.demo_banner_short]     |
| [order.title] [K]              |
| [order.current_status] [order.status_*] |
| ★ [order.demo_hint]            |
| 查单模式：[order.lookup_access]  |
|  ([order.lookup_another])      |
| 会员模式：([account.orders])     |
| [order.progress]（纵向，各步同桌面的 [order.status_*]）|
| [order.items]                  |
| <名称>/<规格> x2               |
|  [M1] / [order.cash_paid][M2]  |
| ▸ [order.amount_breakdown] [M3]|
| ▸ [checkout.recipient_title]   |
|    [P1] <姓名>/<电话>/<地址>    |
| ( [order.confirm_receipt] )    |
| [order.confirm_receipt_hint]   |
| ( [order.request_refund] )     |
| [order.refund_deadline]        |
| [order.refunded_total] [M4]    |
| [order.refundable_left] [M4]   |
| ▸ [order.refund_requests]      |
| [order.fulfilment_frozen]?     |
| 待支付：[order.lookup_no_pay] 或 ( [account.order_pay] ) |
--- 过期时 ---
| [order.session_expired]        |
| ( [common.nav_track] )         |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 商品单价快照 | 「数据模型」（`Order` / `OrderItem`） |
| M2 | 逐件实付快照（不含积分抵扣），界面文字 `[order.cash_paid]` 不用「现金」一词 | 「计价、优惠、积分与库存」（第 2、4 条） |
| M3 | 小计、优惠、积分、示例运费、合计快照；游客订单（下单时不是会员，含之后被认领的）不显示优惠与积分两行，查单模式与会员模式相同（0.5） | 「数据模型」（`Order` / `OrderItem`）「计价、优惠、积分与库存」（第 7 条） |
| M4 | 累计已退、剩余可退、每笔申请金额 | 「订单与退款状态」 |
| P1 | 完整收货姓名、电话、地址：查单模式凭查单授权可见；会员模式仅本人下单或认领的订单可见；长期保存，不再显示「已匿名化」 | 「权限与资料保护」「资料保留」 |
| L | 查单授权：当前浏览器、仅该单、30 分钟；可查看、确认收货、申请退款；不能支付或取消；查其他订单须重新输入订单号和电话；过期须重新查单 | 「权限与资料保护」 |
| K | 页标题 `order.title` 中的订单号 | 「权限与资料保护」 |
| — | 会员模式：仅访问自己认领或下单的订单，确认收货、退款规则与查单页相同 | 「权限与资料保护」「订单与退款状态」 |
| — | 会员模式待支付订单：`[account.order_pay]` 回到 P06，凭登录会话继续支付或取消（Q17 已决）；查单模式不提供 | 「订单与退款状态」「权限与资料保护」 |
| — | 确认收货：仅 `demo_shipped` 可点；7 天自动完成；全退后冻结；幂等，须 CSRF 令牌 | 「订单与退款状态」「失败、并发与重试」「权限与资料保护」 |
| — | 退款入口：支付成功后 30 天内显示，截止时间以服务端 `order.refund_deadline` 为准（Q2 已决） | 「订单与退款状态」 |

---

## P10 退款申请

- **目的**：选择要退的商品与数量，查看系统计算的模拟退款金额（会员另见积分返还与追回），提交申请。
- **入口**：P09 `[order.request_refund]`（查单模式或会员模式）。
- **去向**：P09（提交后显示 `[refund.submitted]` 与记录）。
- **访问授权**：查单模式沿用该单查单授权 `[L]`，过期显示 `[order.session_expired]`；会员模式凭会员会话。
- **演示提示**：常驻横幅；★ `[refund.demo_hint]`；◆ 提交按钮旁 `[refund.submit_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [refund.title]   [order.title] [K]            ★ [refund.demo_hint]    |
| 查单模式：[L] [order.lookup_access]                                    |
+-----------------------------------------------------------------------+
| [refund.select_items]                                                 |
|  <名称>/<规格>  [order.cash_paid] [M1]   [detail.quantity] (-) 0 (+) [refund.max_qty] |
|  <名称>/<规格>  [order.cash_paid] [M1]   [detail.quantity] (-) 0 (+) [refund.max_qty] |
+-----------------------------------------------------------------------+
| [refund.estimate]         [M2]                                        |
| 会员订单：[refund.points_back] [M3]  [refund.points_reversed] [M3]     |
|       [refund.expired_points_note]                                    |
| [refund.shipping_not_refunded]  [refund.coupon_not_restored]          |
| ( [refund.submit] )                                                   |
| ◆ [refund.submit_hint]                                                |
| 错误：[refund.duplicate] / [refund.nothing_left] / [refund.window_closed] / |
|   [order.session_expired]（查单授权过期）                               |
```

手机：

```text
| [common.demo_banner_short]     |
| [refund.title]                 |
| [order.title] [K]              |
| ★ [refund.demo_hint]           |
| 查单模式：[order.lookup_access]  |
| [refund.select_items]          |
| <名称>/<规格>                   |
|  [order.cash_paid] [M1]        |
|  [detail.quantity] (-) 0 (+)   |
|  [refund.max_qty]              |
| <名称>/<规格> …                 |
| [refund.shipping_not_refunded] |
| [refund.coupon_not_restored]   |
| [refund.expired_points_note]   |
+--------------------------------+
| 底部固定：[refund.estimate][M2] |
|  [refund.points_back] [M3]     |
|  [refund.points_reversed] [M3] |
|  ◆ [refund.submit_hint]        |
|  ( [refund.submit] )           |
```

说明：积分行只在该单属于会员（下单时为会员）时显示；由会员认领的游客订单没有获得积分，也没有积分抵扣，服务端返回零时不显示积分行。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 每件现金实付快照；可退数量 = 购买数量 − 已批准 − 审核中 | 「订单与退款状态」「数据模型」（`RefundRequest` / `RefundLine`） |
| M2 | 预计模拟退现金，由服务端按逐件快照计算；运费不退 | 「订单与退款状态」「计价、优惠、积分与库存」（第 4、5 条） |
| M3 | 返还抵扣积分（已过期不返还）、追回获得积分 | 「计价、优惠、积分与库存」（第 5 条）「订单与退款状态」 |
| L | 查单模式：沿用该单查单授权，仅该单、30 分钟；过期须重新查单 | 「权限与资料保护」 |
| K | 页标题 `order.title` 中的订单号 | 「权限与资料保护」 |
| — | 会员模式：仅本人下单或认领的订单，规则与查单页相同 | 「权限与资料保护」「订单与退款状态」 |
| — | 提交幂等并须 CSRF 令牌；同一可退数量重复申请被拒 | 「失败、并发与重试」「订单与退款状态」「权限与资料保护」 |

---

## P11 注册（短信验证）

- **目的**：以马来西亚或新加坡手机号经真实短信验证成为会员；与短信登录、结账验证是同一个 V1 流程——号码已注册则直接登录。注册时不设密码，之后可在 P13 设置。
- **入口**：P12 `[common.nav_register]`（登录页与忘记密码遇到未注册号码时）；P07 `[result.guest_register]`。页头只有登录入口，不设注册入口。
- **去向**：P13（注册或登录成功，显示 `[account.password_none]` 提示可设密码）；来源页；P05 或 P01（`[auth.continue_guest]`）。
- **演示提示**：常驻横幅；★ `[auth.register_demo_hint]`（写明这是真实短信）；`[auth.sms_scope]`；`[auth.same_flow_note]`。
- **短信验证开关关闭时**（0.7）：V1 整个替换为 `[auth.sms_paused]` 与 `[auth.continue_guest]`（见 V1），不显示 `[auth.sms_scope]` 与 `[auth.claim_notice]`。

桌面（居中单栏卡片）：

```text
| [common.demo_banner]                                                  |
|            +-------------------------------------------+              |
|            | [auth.register_title]                     |              |
|            | ★ [auth.register_demo_hint]               |              |
|            | [auth.same_flow_note]                     |              |
|            | <V1：用途「注册 / 短信登录」>                  |              |
|            |  [auth.phone] [+60 ▾][P1 ____________]    |              |
|            |  [auth.sms_scope]                         |              |
|            |  [auth.claim_notice]                      |              |
|            |  [auth.challenge] <托管人机挑战组件>          |              |
|            |  ( [auth.send_code] )                     |              |
|            |  [auth.code_sent]                         |              |
|            |  [auth.code] [______] ( [auth.verify_submit] ) |          |
|            |  ([auth.resend_code])                     |              |
|            |  错误：[auth.not_supported_country] /      |              |
|            |   [auth.sms_failed] / [auth.code_wrong] / |              |
|            |   [auth.code_too_many] / [common.rate_limited] |          |
|            |   → ([auth.continue_guest])（仅前两种）      |              |
|            |  成功：[auth.verified_registered] 或       |              |
|            |        [auth.verified_login] → P13        |              |
|            | [common.nav_login] → P12                  |              |
|            +-------------------------------------------+              |
```

手机：

```text
| [common.demo_banner_short]     |
| [auth.register_title]          |
| ★ [auth.register_demo_hint]    |
| [auth.same_flow_note]          |
| [auth.phone]                   |
| [+60▾][P1 _______________]     |
| [auth.sms_scope]               |
| [auth.claim_notice]            |
| <托管人机挑战组件>                 |
| ( [auth.send_code] )           |
| [auth.code_sent]               |
| [auth.code] [______]           |
| ( [auth.verify_submit] )       |
| ([auth.resend_code])           |
| <错误> ([auth.continue_guest])  |
| [common.nav_login]             |
```

说明：`[+60 ▾]` 为国家码选择（仅 +60、+65）。`[auth.continue_guest]` 只在号码不在白名单或短信发送失败时出现；验证码错误不出现。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| P1 | 手机号（E.164；白名单马来西亚、新加坡；`{phoneMasked}` 只显示部分号码）；保留至注销 | 「边界与原则」「失败、并发与重试」「权限与资料保护」「资料保留」 |
| — | 与短信登录、结账验证同一流程：已注册则登录，未注册则创建无密码会员 | 「权限与资料保护」「数据模型」（`Member` / `VerificationAttempt`） |
| — | 人机挑战在发送短信前；短信失败不创建账号 | 「失败、并发与重试」 |
| — | 自动认领近 30 天、未认领的游客订单，不补发积分（短信降级下单与以后扩大白名单时的认领剩余风险 Kelvin 已接受，Q11 已决；短信验证关闭期间马新号码的游客订单在开启后同样可被认领，Kelvin 2026-10-01 已接受，0.7） | 「权限与资料保护」「计价、优惠、积分与库存」（第 3 条） |

---

## P12 登录与忘记密码

- **目的**：会员以**手机号加密码**或**手机号加短信验证码**登录；已设密码者忘记密码时经短信验证重设。
- **入口**：页头「Log in」；P05 `[checkout.login_password]`；P11；P13 `[account.password_change]`（进入重设）。
- **去向**：登录成功 → 来源页或 P13；P11（`[common.nav_register]`，含忘记密码遇到未注册号码时）；重设完成 → 登录。
- **演示提示**：常驻横幅；★ `[auth.login_demo_hint]`；短信登录与忘记密码显示 `[auth.sms_scope]`。
- **短信验证开关关闭时**（0.7）：密码页签照常，登录失败改显示 `[auth.login_failed_sms_off]`（不提示改用短信验证码）；短信验证码页签与忘记密码的 V1 替换为 `[auth.sms_paused]`（见 V1），不显示 `[auth.sms_scope]`；未设密码的会员要等开关重新开启才能登录。

桌面：

```text
| [common.demo_banner]                                                  |
|    +------------ 登录 ------------------+   +-------- 忘记密码 ---------+ |
|    | [auth.login_title]                 |   | [auth.reset_title]       | |
|    | ★ [auth.login_demo_hint]           |   | <V1：用途「重设密码」>      | |
|    | ([auth.login_method_password]) | ([auth.login_method_sms]) |      | |
|    | --- 密码页签 ---                     |   |  [auth.phone] [P1 ____]  | |
|    | [auth.phone] [+60▾][P1 ________]   |   |  [auth.sms_scope]        | |
|    | [auth.password] [P2 ________]      |   |  <托管人机挑战组件>         | |
|    | ( [auth.login_submit] )            |   |  ( [auth.send_code] )    | |
|    | ([auth.forgot]) → 右栏              |   |  [auth.code] [____]      | |
|    | 错误：[auth.login_failed] /         |   |  ( [auth.verify_submit] )| |
|    |  [common.rate_limited]             |   | 错误：[auth.code_wrong] / | |
|    | --- 短信验证码页签 ---                |   |  [auth.code_too_many] /  | |
|    | [auth.same_flow_note]              |   |  [common.rate_limited] / | |
|    | <V1：用途「注册 / 短信登录」>          |   |  [auth.sms_not_sent_no_change] | |
|    |  成功：[auth.verified_login] 或      |   |  （无游客文字与按钮）       | |
|    |   [auth.verified_registered]       |   | --- 已注册号码验证通过 --- | |
|    | [common.nav_register] → P11        |   | [auth.new_password][P2]  | |
|    +------------------------------------+   | [auth.password_rule]     | |
|                                             | ( [auth.reset_submit] )  | |
|                                             | [auth.reset_done]        | |
|                                             | --- 未注册号码验证通过 --- | |
|                                             | [auth.reset_not_registered] | |
|                                             | [common.nav_register] → P11 | |
|                                             |  （不创建账号）            | |
|                                             +--------------------------+ |
（忘记密码为独立步骤页，桌面以右栏示意）
```

手机：

```text
| [common.demo_banner_short]     |
| [auth.login_title]             |
| ★ [auth.login_demo_hint]       |
| ([auth.login_method_password])([auth.login_method_sms]) |
--- 密码页签 ---
| [auth.phone]                   |
| [+60▾][P1 _______________]     |
| [auth.password] [P2 ______]    |
| ( [auth.login_submit] )        |
| ([auth.forgot])                |
| <错误：[auth.login_failed]>     |
--- 短信验证码页签 ---
| [auth.same_flow_note]          |
| <V1 同 P11>                     |
| [common.nav_register]          |
--- 忘记密码（下一屏）---
| [auth.reset_title]             |
| <V1：[auth.phone] … ( [auth.verify_submit] )> |
| <错误；短信未发出或号码不在白名单：|
|  [auth.sms_not_sent_no_change]，无游客按钮> |
--- 已注册号码验证通过 ---
| [auth.new_password] [P2 ____]  |
| [auth.password_rule]           |
| ( [auth.reset_submit] )        |
| [auth.reset_done]              |
--- 未注册号码验证通过 ---
| [auth.reset_not_registered]    |
| [common.nav_register] → P11    |
```

说明：

- **未设密码的账号与密码错误显示同一条** `[auth.login_failed]`，不暴露账号是否存在或是否设过密码；该文案同时提示可改用短信验证码登录。
- 短信验证码页签与 P11 走同一 V1 流程：号码已注册则登录，未注册则创建会员并登录。
- 忘记密码：V1 验证通过后，号码已注册才显示新密码输入；未设过密码的账号验证通过后同样可设置（等同首次设密码）。号码未注册时**不创建账号**，显示 `[auth.reset_not_registered]`（此号码尚未注册）与去 P11 的 `[common.nav_register]`。
- 忘记密码时短信发送失败或号码不在白名单，只显示 `[auth.sms_not_sent_no_change]`（短信未发出、账号未作任何更改、请稍后再试），不显示游客相关文字或 `[auth.continue_guest]`。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| P1 | 手机号 | 「权限与资料保护」「失败、并发与重试」 |
| P2 | 密码、新密码：至少 8 位，不强制复杂度，安全哈希存储 | 「权限与资料保护」「数据模型」（`Member` / `VerificationAttempt`） |
| — | 密码登录对未设密码账号与错误密码返回相同通用失败；登录/重设限流 | 「权限与资料保护」「失败、并发与重试」 |
| — | 短信登录与注册、结账验证同一流程；重设须再次短信验证，重设流程中未注册号码不创建账号 | 「权限与资料保护」 |

---

## P13 会员中心

- **目的**：查看我的订单（含自动认领的游客订单）并进入订单详情确认收货、申请退款；积分余额/到期/待抵扣与明细；可用优惠券与使用记录；设置或更改密码；退出登录；以短信验证码注销账号。
- **入口**：页头「My account」；P11、P12 登录成功；P05 验证通过后页头。
- **去向**：P09、P10（会员模式）；P06（待支付订单经 P09 会员模式 `[account.order_pay]`，继续支付或取消，Q17 已决）；P12 忘记密码流程（更改已有密码）；P01（退出或注销后）。
- **演示提示**：常驻横幅；★ `[account.demo_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [common.nav_account]   <手机号部分遮盖 [P1]>   ([common.nav_logout])   |
+----------------+------------------------------------------------------+
| [account.orders]| ★ [account.demo_hint]                               |
| [account.points]| [account.orders]                                    |
| [account.coupons]| <日期> [K]<订单号> [order.status_*] [M1] ([account.order_view]) → P09 会员模式 |
| [account.settings]| <日期> [K]<订单号> [order.status_*] [M1] ([account.order_view]) |
|                |   [account.orders_hint]                              |
|                | 无订单时：[account.orders_empty]                      |
|                |------------------------------------------------------|
|                | [account.points]                                     |
|                | [account.points_balance] [M2]                        |
|                | [account.points_pending] [M2]（>0 时显示）             |
|                | [account.points_expiry]  [M2]                        |
|                | [account.points_history]                             |
|                |  <日期> [account.points_type_*] ±<积分> [K]<订单号>    |
|                |------------------------------------------------------|
|                | [account.coupons]                                    |
|                | [account.coupons_available]                          |
|                |  <代码> [account.coupon_value_fixed]/[account.coupon_value_percent] [M3] |
|                |   [account.coupon_min_spend] [M3] [account.coupon_valid_until] |
|                |  [account.coupon_use_hint]                           |
|                |  无可用券：[account.coupons_available_empty]           |
|                | [account.coupons_used]                               |
|                |  <代码> [account.coupon_used_on] [K] [M4]            |
|                |  无记录：[account.coupons_used_empty]                  |
|                |------------------------------------------------------|
|                | [account.settings]                                   |
|                | [account.password_title]                             |
|                |  未设密码：[account.password_none]                     |
|                |   [auth.new_password] [P2 ______] [auth.password_rule] |
|                |   ( [account.password_set_submit] ) → [account.password_set_done] |
|                |  已设密码：[account.password_exists]                   |
|                |   ([account.password_change]) → P12 忘记密码流程       |
|                |------------------------------------------------------|
|                | [account.delete]                                     |
|                | [account.delete_warning]                             |
|                | [account.delete_verify]                              |
|                | <V1：用途「注销确认」，号码固定为本账号>                 |
|                |  短信未发出或号码不在白名单：[auth.sms_not_sent_no_change] |
|                |   （无游客文字与按钮）                                 |
|                |  验证通过后：( [account.delete_confirm] )             |
|                |  完成：[account.deleted] → P01                        |
|                | --- 短信验证开关关闭时代替 [account.delete_verify] 与 V1（0.7） --- |
|                |  已设密码：[account.delete_verify_password]            |
|                |   [auth.password] [P2 ______] ( [account.delete_confirm] ) |
|                |   错误：[account.delete_password_wrong] / [common.rate_limited] |
|                |  未设密码：[account.delete_verify_session]             |
|                |   ( [account.delete_confirm] ) → 确认框 [account.delete_session_confirm] |
|                |     ( [account.delete_confirm] ) ([account.delete_keep]) |
+----------------+------------------------------------------------------+
```

手机（分段标签）：

```text
| [common.demo_banner_short]     |
| [common.nav_account]           |
| ★ [account.demo_hint]          |
| [account.orders][account.points][account.coupons][account.settings] |
--- 订单 ---
| [account.orders_hint]          |
| <日期> [order.status_*]        |
| [K]<订单号> [M1]  >            |
|  （> 读屏标签 [account.order_view]）|
--- 积分 ---
| [account.points_balance] [M2]  |
| [account.points_pending] [M2]  |
| [account.points_expiry] [M2]   |
| ▸ [account.points_history]     |
|  <日期> [account.points_type_*] ±<积分> [K]<订单号> |
--- 优惠券 ---
| [account.coupons_available]    |
| <代码> [account.coupon_value_*] [M3] |
|  [account.coupon_min_spend] [M3] |
|  [account.coupon_valid_until]  |
| [account.coupon_use_hint]      |
| [account.coupons_used]         |
| <代码> [account.coupon_used_on] [K] [M4] |
--- 设置 ---
| [P1] <手机号部分遮盖>           |
| [account.password_title]       |
| [account.password_none]        |
|  [auth.new_password][P2 ____]  |
|  [auth.password_rule]          |
|  ( [account.password_set_submit] ) |
| 或 [account.password_exists]    |
|  ([account.password_change])   |
| ([common.nav_logout])          |
| [account.delete]               |
| [account.delete_warning]       |
| [account.delete_verify]        |
| <V1 注销确认>                    |
| 短信未发出：[auth.sms_not_sent_no_change] |
| ( [account.delete_confirm] )   |
--- 短信验证开关关闭时（0.7）---
| 已设密码：[account.delete_verify_password] |
| [auth.password] [P2 ______]    |
| ( [account.delete_confirm] )   |
| 未设密码：[account.delete_verify_session] |
| ( [account.delete_confirm] ) → 确认框 |
```

说明：

- 「我的订单」中的订单都可点 `[account.order_view]` 进入 P09 会员模式，在其中确认收货与申请退款，规则与查单页相同（Q5 已决）。
- 「我的优惠券」同时列出可用券（`[account.coupons_available]`）与本人使用记录（`[account.coupons_used]`）（Q13 已决）。可用券列出**所有启用中且在有效期内的券，对全部会员相同**，不按每会员或总次数上限过滤（Q19 已决）；已达上限的券在结账时由服务端拒绝，显示 `[checkout.coupon_invalid]`。
- 待支付订单在「我的订单」中点 `[account.order_view]` 进入 P09 会员模式，再经 `[account.order_pay]` 回到 P06 继续支付或取消（Q17 已决）。
- 注销确认时短信发送失败或号码不在白名单，只显示 `[auth.sms_not_sent_no_change]`，不显示游客相关文字或 `[auth.continue_guest]`。
- 首次设置密码在已登录会话内完成，不需短信；更改已有密码须经短信验证（P12 忘记密码流程）。
- 注销须先以短信验证码确认（V1），验证通过后才出现 `[account.delete_confirm]`；自动注册的账号可能从未设过密码，故不以密码确认（Q15 已决）。
- 短信验证开关关闭时（0.7，DESIGN 1.11「权限与资料保护」）：注销不经短信。已设密码的会员输入当前密码后点 `[account.delete_confirm]`，密码错误显示 `[account.delete_password_wrong]`；未设密码的会员显示 `[account.delete_verify_session]`，点 `[account.delete_confirm]` 后弹出确认框 `[account.delete_session_confirm]`，再点 `[account.delete_confirm]` 才注销，`[account.delete_keep]` 关闭确认框。未设密码时密码区另显示 `[account.sms_paused_set_password]`。更改已有密码须经短信，关闭期间 `[account.password_change]` 进入 P12 后显示 `[auth.sms_paused]`。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 订单合计快照 | 「数据模型」（`Order` / `OrderItem`） |
| M2 | 积分余额、待抵扣、到期批次、流水 | 「计价、优惠、积分与库存」（第 3、5 条）「数据模型」（`PointLedger`） |
| M3 | 可用券（所有启用中且在有效期内的券，对全部会员相同，不按使用上限过滤，Q19 已决）的面额（固定金额或百分比）与最低商品消费 | 「数据模型」（`Coupon` / `CouponUse`）「计价、优惠、积分与库存」（第 1 条） |
| M4 | 优惠券使用记录 | 「数据模型」（`Coupon` / `CouponUse`） |
| P1 | 会员手机号（部分遮盖显示）；保留至主动注销 | 「权限与资料保护」「资料保留」 |
| P2 | 首次设置密码：至少 8 位，不强制复杂度，安全哈希；在已登录会话内进行 | 「权限与资料保护」「数据模型」（`Member` / `VerificationAttempt`） |
| K | 订单列表、积分明细与 `account.coupon_used_on` 中的订单号 | 「权限与资料保护」 |
| — | 积分明细类型 `account.points_type_*`（获得、抵扣、到期、退款返还、退款追回） | 「计价、优惠、积分与库存」（第 3、5 条）「数据模型」（`PointLedger`） |
| — | 仅显示本人下单或认领的订单；可在「我的订单」确认收货、申请退款，规则与查单页相同 | 「权限与资料保护」「订单与退款状态」 |
| — | 注销：先短信验证码确认，再撤销会话、删除手机号与密码、清空订单会员 ID；积分与未用券作废；订单收货资料不删除 | 「权限与资料保护」「资料保留」 |
| — | 短信验证开关关闭时注销改以当前密码，未设密码者以已登录会话二次确认（0.7） | 「权限与资料保护」（DESIGN 1.11） |
| — | 注销后手机号与密码哈希仍可能在共享备份中残留，核实前无确定上限；隐私页以 `[privacy.member_backup]` 对外披露，不写天数（Q18 已决） | 「资料保留」 |

---

## P14 隐私说明

- **目的**：说明收集哪些资料、用途、保留期限（收货资料长期保存、会员手机号保留至注销）、两种订单授权、查单剩余风险、短信与日志，以及联系入口（占位）。
- **入口**：页脚「Privacy」；P05 `[checkout.form_notice_link]`。
- **去向**：来源页；WhatsApp 占位链接（未配置时隐藏）。
- **演示提示**：常驻横幅；★ `[privacy.demo_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [privacy.title]                              ★ [privacy.demo_hint]    |
+-----------------------------------------------------------------------+
| [privacy.intro]                                                       |
| [privacy.h_collect]    [P1] [privacy.collect]                          |
|                        [privacy.fictional]                             |
| [privacy.h_retention]  [P2] [privacy.retention_recipient]              |
|                        [P3] [privacy.member]                           |
|                        [P4] [privacy.member_backup]                    |
| [privacy.h_access]     [P5] [privacy.browser_access]                   |
|                        [P6] [privacy.lookup_risk]                      |
| [privacy.h_sms_logs]   [P7] [privacy.sms]  [privacy.logs]              |
|                        [P8] [privacy.sms_toggle]                       |
| [privacy.h_contact]    [privacy.contact]                               |
|                        ( [privacy.contact_button] ) → {{WHATSAPP_CONTACT_LINK}} 占位 |
|                        （WhatsApp 未配置时隐藏整个「联系」段）            |
```

手机：

```text
| [common.demo_banner_short]     |
| [privacy.title]                |
| ★ [privacy.demo_hint]          |
| [privacy.intro]                |
| ▾ [privacy.h_collect] [P1]     |
| ▾ [privacy.h_retention] [P2][P3][P4] |
| ▾ [privacy.h_access] [P5][P6]  |
| ▾ [privacy.h_sms_logs] [P7][P8] |
| [privacy.h_contact]            |
| [privacy.contact]              |
| ( [privacy.contact_button] ) → 占位链接 |
（手机上各段默认展开；▾ 仅示意分段；联系段未配置时隐藏）
```

说明：本页不提供、也不暗示访客删除收货资料的入口（DESIGN 1.9「资料保留」：首版不提供该能力）。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| P1 | 收集字段清单、不收集邮箱与证件、可虚构（短信验证开启时马新结账手机号须能收短信；0.7） | 「边界与原则」 |
| P2 | 收货资料随订单长期保存，不删除、不匿名化，注销后也保留；访客不能自行删除 | 「资料保留」「边界与原则」 |
| P3 | 会员手机号保留至注销，长期未登录不自动注销；短信记录短期保留 | 「资料保留」 |
| P4 | 对外披露注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间；不写天数，不写或暗示备份一定会在某时被清除（Q18 已决） | 「资料保留」「上线依赖与设计闸门」 |
| P5 | 游客短期凭据与查单授权：本浏览器对该单 30 分钟的访问；30 分钟后或在其他浏览器须凭订单号和电话重新查单，不表示该单以后只能在本浏览器打开 | 「权限与资料保护」 |
| P6 | 订单号 + 电话可见完整收货资料的剩余风险，随订单累积 | 「权限与资料保护」「资料保留」 |
| P7 | 真实短信（结账验证、注册、短信登录、重设密码、注销确认）；日志最长 30 天且不含个人资料 | 「失败、并发与重试」「权限与资料保护」「资料保留」 |
| P8 | 短信验证开关：可关闭全站短信，关闭时未登录访客无论号码都以游客结账（已登录会员照常以会员结账），注册、短信登录与重设密码暂停，注销以密码或会话确认（0.7） | 「边界与原则」「权限与资料保护」（DESIGN 1.11） |

---

## 管理后台总体

- 单一管理员；后台同样支持英文、中文、马来文，默认英文。
- 每页顶部常驻 `[admin.demo_banner]`，作为后台每页的演示提示（★）；各页另有操作旁提示。
- 桌面：左侧导航 + 右侧内容；手机：顶部 ☰ 导航，表格改为卡片列表。导航项依次为 `[admin.nav_orders]`、`[admin.nav_refunds]`、`[admin.nav_products]`、`[admin.nav_coupons]`、`[admin.nav_shipping]`、`[admin.nav_stock_resets]`、`[admin.nav_store_design]`（0.4）、`[admin.nav_settings]`（0.7），界面语言切换用 `[common.lang_*]`；A02 线框展开写出导航，A03–A09 以「☰ 导航」标注代替。手机顶栏「☰ [admin.nav_*]」显示当前页的导航项名称。
- 除 A01 外每页右上角常驻 `[admin.logout]`：桌面在 `[admin.demo_banner]` 右侧，手机在顶栏右端；退出后撤销当前后台会话、回到 A01（0.8）。
- 表格列头、状态值、按钮与区块标题同样都用 `[key]`；`<…>` 为运行时数据，与前台约定相同。
- 所有权限由服务端检查，前端隐藏按钮不代替授权（DESIGN 1.9「权限与资料保护」）。
- **后台任何页面都不设导出按钮**（DESIGN 1.9「权限与资料保护」：首版不提供后台导出；Q7 已决）。
- 后台固定使用一套中性样式，不随 A08 选择的店铺主题变化（0.4，REQUIREMENTS 1.9「管理后台」）；深浅色随管理员设备设置。

## A01 后台登录

- **目的**：管理员以用户名与密码登录。
- **入口**：直接访问后台路径（前台不放入口链接）。
- **去向**：A02。
- **演示提示**：★ `[admin.demo_banner]`。

桌面：

```text
| [admin.demo_banner]                                                   |
|            +-------------------------------+                          |
|            | [admin.login_title]           |                          |
|            | [common.lang_en]|[common.lang_zh]|[common.lang_ms] |          |
|            | [admin.username] [________]   |                          |
|            | [auth.password]  [________]   |                          |
|            | ( [auth.login_submit] )       |                          |
|            | 错误：[admin.login_failed] /   |                          |
|            |   [admin.locked]              |                          |
|            +-------------------------------+                          |
```

手机：

```text
| [admin.demo_banner]            |
| [admin.login_title] <当前语言>▾ |
| [admin.username] [________]    |
| [auth.password]  [________]    |
| ( [auth.login_submit] )        |
| <错误>                          |
```

说明：前台 `auth.login_failed` 在 0.2 改为提示可用短信验证码登录，不适用于后台，故后台登录失败改用 `[admin.login_failed]`。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| — | 管理员凭据；按来源与账号限流，连续失败短时锁定并告警 | 「权限与资料保护」「失败、并发与重试」 |

## A02 后台订单与模拟发货

- **目的**：查看全部订单与原始收货资料，推进 `demo_paid` → `demo_packed` → `demo_shipped`。
- **入口**：A01 登录后默认页；后台导航。
- **去向**：A03（该单退款申请）。
- **演示提示**：★ `[admin.demo_banner]`；发货按钮旁 `[admin.ship_hint]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| [admin.nav_orders]  | [admin.search_order][K ________]  [admin.filter_status] ▾ |
| [admin.nav_refunds] | [pay.order_no] [admin.col_date] [admin.filter_status] [admin.col_total] [admin.col_refunds] |
| [admin.nav_products]| [K]<订单号> <日期> [order.status_*] [M1] [admin.refunds_pending] |
| [admin.nav_coupons] |-------------------------------------------------|
| [admin.nav_shipping]| [admin.order_detail]  [K]<订单号>                 |
| [admin.nav_stock_resets] |  [order.items] <名称>/<规格> x2 [M2]         |
| [admin.nav_store_design] |                                              |
| [admin.nav_settings] |                                                |
|           |  [order.amount_breakdown] [M1]                             |
|           |  [admin.recipient_raw]  [P1] <姓名>/<电话>/<地址>/<邮编>    |
|           |    [admin.recipient_audited]                               |
|           |  ( [admin.mark_packed] ) ( [admin.mark_shipped] )          |
|           |  [admin.ship_hint]                                         |
|           |  全部已退：[admin.frozen]（按钮禁用）                        |
|           |  [admin.event_log]                                         |
|           |   [admin.col_time] [admin.col_event] [admin.col_actor]     |
|           |   <时间> [order.status_*] [admin.actor_*]                   |
+-----------+-----------------------------------------------------------+
（状态筛选选项为各 [order.status_*]；事件列显示变更后的 [order.status_*]）
（不设导出按钮，Q7 已决）
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_orders]          |
| [admin.search_order][K ____]   |
| [admin.filter_status] ▾        |
| [K]<订单号> [order.status_*]   |
|  <日期> [M1] [admin.refunds_pending] > |
--- 详情（下一屏）---
| [admin.order_detail] [K]<订单号> |
| [order.items] … [M2]           |
| ▸ [order.amount_breakdown] [M1]|
| ▸ [admin.recipient_raw] [P1]   |
|   [admin.recipient_audited]    |
| ( [admin.mark_packed] )        |
| ( [admin.mark_shipped] )       |
| [admin.ship_hint]              |
| [admin.frozen]?                |
| ▸ [admin.event_log]            |
|   <时间> [order.status_*] [admin.actor_*] |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 订单金额快照 | 「数据模型」（`Order` / `OrderItem`） |
| M2 | 行项目与逐件分摊快照 | 「数据模型」（`Order` / `OrderItem`）「计价、优惠、积分与库存」（第 2 条） |
| P1 | 原始收货资料，仅管理员可见，查看留审计；长期保存，不再显示「已匿名化」；不设导出 | 「权限与资料保护」「资料保留」 |
| K | 订单号列、订单号搜索框与详情标题中的订单号 | 「权限与资料保护」 |
| — | 状态推进校验当前状态，全退后冻结，已完成不可倒退 | 「订单与退款状态」 |
| — | 事件记录不含收货资料原文 | 「数据模型」（`PaymentAttempt` / `OrderEvent`） |

## A03 后台退款审核

- **目的**：查看退款申请，批准或拒绝，填写理由。
- **入口**：后台导航；A02 订单的退款数。
- **去向**：A02（查看订单）。
- **演示提示**：★ `[admin.demo_banner]`；批准按钮旁 `[admin.refund_hint]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.filter_status] ▾（选项：[order.refund_requested] / [order.refund_approved] / [order.refund_rejected]） |
|           | [admin.col_requested_at] [pay.order_no] [order.items] [detail.quantity] [admin.col_amount] [admin.filter_status] |
|           | <时间> [K]<订单号> <名称> x1 [M1] [order.refund_requested] |
|           |-----------------------------------------------------------|
|           | [admin.refund_detail]  [K]<订单号>                         |
|           |  <名称>/<规格> [admin.refund_qty]                          |
|           |  [admin.refund_amount] [M1]                               |
|           |  [admin.refund_points] [M2]                               |
|           |  [order.refunded_total] [M3] [order.refundable_left] [M3] |
|           |  [refund.shipping_not_refunded]                           |
|           |  [admin.refund_reason] [____________]                     |
|           |  ( [admin.refund_approve] )  ( [admin.refund_reject] )    |
|           |  [admin.refund_hint]                                      |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_refunds]         |
| [admin.filter_status] ▾        |
| <时间> [K]<订单号>              |
|  <名称> x1 [M1]                 |
|  [order.refund_requested|approved|rejected] > |
--- 详情 ---
| [admin.refund_detail] [K]<订单号> |
| <名称>/<规格> [admin.refund_qty] |
| [admin.refund_amount] [M1]     |
| [admin.refund_points] [M2]     |
| [order.refunded_total] [M3]    |
| [order.refundable_left] [M3]   |
| [admin.refund_reason] [____]   |
| [admin.refund_hint]            |
| ( [admin.refund_approve] )     |
| ( [admin.refund_reject] )      |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 模拟退现金，按原逐件现金实付快照 | 「订单与退款状态」「计价、优惠、积分与库存」（第 4、5 条） |
| M2 | 返还抵扣积分与追回获得积分，余额不足记待抵扣 | 「计价、优惠、积分与库存」（第 5 条）「订单与退款状态」 |
| M3 | 累计已退与剩余可退，累计上限约束 | 「计价、优惠、积分与库存」（第 5 条） |
| K | 申请列表与详情中的订单号 | 「权限与资料保护」 |
| — | 批准/拒绝幂等；已批准不可再批 | 「失败、并发与重试」「订单与退款状态」 |

## A04 后台商品、图片、规格与库存

- **目的**：维护商品三语文案、分类、图片、规格、SKU、价格、每日初始库存、当日库存调整与上架状态。
- **入口**：后台导航。
- **去向**：A07（查看重置结果）。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.initial_stock_note]`；`[admin.rules_apply_new]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.nav_products]                        ( [common.create] ) |
|           | <名称> <分类> [admin.active] ( [common.edit] )             |
|           |-----------------------------------------------------------|
|           | [admin.product_edit]                                       |
|           |  [admin.content_language] ([common.lang_en]) ([common.lang_zh]) ([common.lang_ms]) |
|           |  [admin.product_name] [__________]                         |
|           |  [admin.product_description] [__________]                  |
|           |  [admin.translation_missing]（缺译时）                     |
|           |  [admin.product_category] [▾]   [admin.active] ☐           |
|           |  [admin.product_images] ( [admin.upload] ) [admin.image_rules] |
|           |  [admin.product_options] <规格名>: <值>,<值>  <规格名>: <值>,<值> |
|           |  [admin.sku] [admin.variant] [admin.price] [admin.initial_stock] [admin.available_today] |
|           |  <sku> <规格值> [M1 ____] [M2 ___] <n> ([admin.adjust_today]) |
|           |  [admin.max_per_order] [M3 __]  [admin.max_per_order_hint]  |
|           |  [admin.initial_stock_note]  [admin.rules_apply_new]       |
|           |  ( [common.save] )                                        |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_products]        |
|   ( [common.create] )          |
| <名称> [admin.active]  >       |
|  （> 读屏标签 [common.edit]）    |
--- 编辑 ---
| [admin.product_edit]           |
| [admin.content_language]       |
| ([common.lang_en])([common.lang_zh])([common.lang_ms]) |
| [admin.product_name] [____]    |
| [admin.product_description] [____] |
| [admin.translation_missing]?   |
| [admin.product_category] [▾]   |
| [admin.active] ☐               |
| [admin.product_images]         |
| ( [admin.upload] )             |
| [admin.image_rules]            |
| [admin.product_options]        |
|  <规格名>: <值>,<值>            |
| [admin.max_per_order] [M3 __]  |
| [admin.max_per_order_hint]     |
| SKU 卡片：                      |
|  [admin.sku] <sku>             |
|  [admin.variant] <规格值>       |
|  [admin.price] [M1 ____]       |
|  [admin.initial_stock][M2 __]  |
|  [admin.available_today] <n>   |
|  ([admin.adjust_today])        |
| [admin.initial_stock_note]     |
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 规格 MYR 价格（非负；只影响新订单） | 「数据模型」（`Product` / `Variant`）「计价、优惠、积分与库存」（第 7 条） |
| M3 | 每单限购件数（1–99 的整数，默认 10，同一商品各规格合计；只影响之后的计价与下单，0.6） | 「数据模型」（`Product` / `Variant`）「计价、优惠、积分与库存」（第 7、8 条，DESIGN 1.10 候批稿） |
| M2 | 每日初始库存（次日生效）与当日库存调整 | 「计价、优惠、积分与库存」（第 6 条） |
| — | 图片类型、大小限制与隔离存储 | 「计价、优惠、积分与库存」（第 7 条）「数据模型」（`AdminAccount` / `AuditEvent`） |

## A05 后台优惠券

- **目的**：创建、停用固定金额或百分比优惠券，设置有效期、最低消费、总次数与每会员次数上限。
- **入口**：后台导航。
- **去向**：—（本页内完成）。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.rules_apply_new]`；`[admin.coupon_listed_note]`（所有启用中且在有效期内的券都列在会员中心「我的优惠券」，对全部会员相同，不按使用上限过滤；Q19 已决，不改数据模型）。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.nav_coupons]                         ( [common.create] ) |
|           | [admin.coupon_listed_note]                                |
|           | [admin.coupon_code] [admin.coupon_type] [admin.coupon_value] [admin.coupon_valid] [admin.coupon_usage] [admin.filter_status] |
|           | <代码> [admin.coupon_percent] [M1] <起止> <n>/<m> [admin.coupon_enabled] ([admin.coupon_disable]) |
|           | <代码> [admin.coupon_fixed] [M1] <起止> <n>/<m> [admin.coupon_disabled] |
|           |-----------------------------------------------------------|
|           | [admin.coupon_code] [______]                              |
|           | [admin.coupon_type] ( ) [admin.coupon_fixed] ( ) [admin.coupon_percent] |
|           | [admin.coupon_value] [M1 ____]                            |
|           | [admin.coupon_valid] [____] ~ [____]                      |
|           | [admin.coupon_min_spend] [M2 ____]                        |
|           | [admin.coupon_total_limit] [__] [admin.coupon_member_limit] [__] |
|           | [admin.rules_apply_new]      ( [common.save] )            |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_coupons]         |
|   ( [common.create] )          |
| [admin.coupon_listed_note]     |
| <代码> [M1] [admin.coupon_enabled]/[admin.coupon_disabled] |
|  [admin.coupon_usage] <n>/<m>  |
|  ([admin.coupon_disable])      |
--- 新建 ---
| [admin.coupon_code] [____]     |
| [admin.coupon_type]            |
|  ( )[admin.coupon_fixed]       |
|  ( )[admin.coupon_percent]     |
| [admin.coupon_value] [M1 ____] |
| [admin.coupon_valid] [__]~[__] |
| [admin.coupon_min_spend][M2 _] |
| [admin.coupon_total_limit][__] |
| [admin.coupon_member_limit][__]|
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 固定金额（RM）或百分比 | 「数据模型」（`Coupon` / `CouponUse`）「计价、优惠、积分与库存」（第 2 条） |
| M2 | 最低商品消费 | 「数据模型」（`Coupon` / `CouponUse`） |
| — | 使用上限与预占；修改只影响新订单 | 「计价、优惠、积分与库存」（第 1、7 条） |

## A06 后台运费区与演示汇率

- **目的**：维护各国示例运费、马来西亚各州属运费、「其他国家」兜底运费，以及参考外币固定演示汇率。
- **入口**：后台导航。
- **去向**：—。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.rules_apply_new]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.shipping_title]                                    |
|           | [admin.shipping_my_state]                                 |
|           |   <州属>  [admin.shipping_fee] [M1 ____]  × 各州           |
|           | [admin.shipping_by_country]                               |
|           |   [admin.shipping_country] <国家> [admin.shipping_fee] [M1 ____] |
|           |   ( [admin.add_country] )                                 |
|           | [admin.shipping_other]  [admin.shipping_fee] [M1 ____]    |
|           |-----------------------------------------------------------|
|           | [admin.fx_title]                                          |
|           |  <国家> → <币种>  [admin.fx_rate] [M2 ____]  [admin.fx_version] |
|           | [admin.rules_apply_new]      ( [common.save] )            |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_shipping]        |
| [admin.shipping_title]         |
| ▸ [admin.shipping_my_state]    |
|   <州属> [M1 ____]              |
| ▸ [admin.shipping_by_country]  |
|   <国家> [M1 ____]              |
|   ( [admin.add_country] )      |
| [admin.shipping_other][M1 __]  |
| ▸ [admin.fx_title]             |
|   <国家> → <币种>               |
|   [admin.fx_rate] [M2 ____]    |
|   [admin.fx_version]           |
| [admin.rules_apply_new]        |
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 国家/州属/兜底示例运费（MYR） | 「数据模型」（`ShippingRate` / `DemoFxRate`） |
| M2 | 固定演示汇率及版本；只作参考不参与结算 | 「边界与原则」「数据模型」（`ShippingRate` / `DemoFxRate`） |

## A07 后台库存重置结果

- **目的**：查看每日按马来西亚时间自动重置示例库存的结果。
- **入口**：后台导航；A04。
- **去向**：A04。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.stock_reset_note]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.stock_reset_title]                                 |
|           | [admin.stock_reset_note]                                  |
|           | [admin.col_date_myt]  [admin.col_result]   [admin.col_sku_count] |
|           | <日期>               [admin.stock_reset_ok]    <n>         |
|           | <日期>               [admin.stock_reset_failed] <n>        |
|           |  ▸（展开后）<sku> [admin.stock_reset_breakdown] [M1]       |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_stock_resets]    |
| [admin.stock_reset_title]      |
| [admin.stock_reset_note]       |
| <日期> [admin.stock_reset_ok]  |
|  [admin.col_sku_count] <n>     |
| <日期> [admin.stock_reset_failed] |
|  ▸ <sku> [admin.stock_reset_breakdown] [M1] |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| M1 | 当日可用库存 = 初始库存 − 仍有效预留；不改历史订单 | 「计价、优惠、积分与库存」（第 6 条） |
| — | 重置可重复运行、只生效一次；失败告警 | 「失败、并发与重试」 |

## A08 后台店铺装修（0.4）

- **目的**：选择店铺主题与主色、上传或移除标志、调整首页四个区块的顺序与显隐、挑选精选商品（REQUIREMENTS 1.11「店铺装修」）。
- **入口**：后台导航 `[admin.nav_store_design]`。
- **去向**：—（本页内完成；保存后前台立即按新设置显示）。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.design_demo_note]`（演示横幅与演示提示始终显示，这里不能关闭）。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.nav_store_design]                                  |
|           | [admin.design_demo_note]                                  |
|           +-------------------------------------+---------------------+
|           | [admin.theme]                       | [admin.preview]     |
|           |  (●)<色块> [admin.theme_pandan]      |  <所选主题与主色的  |
|           |  ( )<色块> [admin.theme_pasar]       |   首页缩样：横幅、   |
|           |  … 共 10 款，两列排列                 |   页头、主视觉标题、 |
|           | [admin.theme_dark_note]             |   按钮、★ 提示、    |
|           |-------------------------------------|   两张商品卡>       |
|           | [admin.accent]                      |  ( [admin.preview_light] ) ( [admin.preview_dark] ) |
|           |  (●)<色块>  ( )<色块>  ( )<色块> …    |                     |
|           |  （读屏标签 [admin.accent_option]）   |                     |
|           | [admin.accent_hint]                 |                     |
|           |-------------------------------------|                     |
|           | [admin.logo]                        |                     |
|           |  <当前标志或文字品牌>  ( [admin.upload] ) ([admin.logo_remove]) |
|           |  [admin.logo_hint]  [admin.image_rules]                   |
|           |-------------------------------------|                     |
|           | [admin.home_blocks]                 |                     |
|           |  1 [admin.block_hero]   ☐ [admin.block_show] (↑)(↓)        |
|           |  2 [home.how_title]     ☐ [admin.block_show] (↑)(↓)        |
|           |  3 [home.categories]    ☐ [admin.block_show] (↑)(↓)        |
|           |  4 [home.featured]      ☐ [admin.block_show] (↑)(↓)        |
|           |  （(↑)(↓) 读屏标签 [admin.block_move_up] / [admin.block_move_down]）|
|           | [admin.home_blocks_hint]            |                     |
|           |-------------------------------------|                     |
|           | [admin.featured_pick]               |                     |
|           |  1 <名称>  (↑)(↓)(✗)                 |                     |
|           |  2 <名称>  (↑)(↓)(✗)                 |                     |
|           |  [admin.featured_add] [▾ <已上架商品>] |                    |
|           |  （(✗) 读屏标签 [admin.featured_remove]）|                   |
|           | [admin.featured_hint]               |                     |
|           | ( [common.save] )   保存后：[admin.design_saved]           |
+-----------+-------------------------------------+---------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_store_design]    |
| [admin.design_demo_note]       |
| [admin.theme]                  |
|  (●)<色块>[admin.theme_pandan]  |
|  … 共 10 款，单列               |
| [admin.theme_dark_note]        |
| [admin.accent]                 |
|  (●)( )( )( )                  |
| [admin.accent_hint]            |
| [admin.logo]                   |
|  <当前标志> ( [admin.upload] )  |
|  ([admin.logo_remove])         |
|  [admin.logo_hint]             |
| [admin.home_blocks]            |
|  1 [admin.block_hero] ☐ (↑)(↓) |
|  2 [home.how_title] ☐ (↑)(↓)   |
|  3 [home.categories] ☐ (↑)(↓)  |
|  4 [home.featured] ☐ (↑)(↓)    |
| [admin.home_blocks_hint]       |
| [admin.featured_pick]          |
|  1 <名称> (↑)(↓)(✗)            |
|  [admin.featured_add] [▾]      |
| [admin.featured_hint]          |
| ▸ [admin.preview]              |
| ( [common.save] )              |
```

说明：

- 选主题后，主色选项换成该主题的一组（默认选第一项）；主色只能从选项中选，每个选项已在浅色与深色下校过对比度。
- 预览只在本页按未保存的选择显示，访客看不到；点 `[common.save]` 后前台所有页面按新设置显示。预览里的文字用当前后台语言。
- 标志图沿用商品图片的格式与大小限制（`[admin.image_rules]`）；移除后前台恢复文字品牌 `ACUVEN SHOP`。
- 首页区块：☐ 勾选为显示；(↑)(↓) 调整顺序，第一项的 (↑) 与最后一项的 (↓) 禁用。★ `[home.demo_hint]` 不在列表中，始终显示。
- 精选商品（0.6）：从已上架商品中挑选，最多 4 件，同一商品不重复；满 4 件时 `[admin.featured_add]` 禁用。(↑)(↓) 调整顺序，规则同首页区块；(✗) 移出精选。之后下架的商品仍留在列表中，前台不显示它；前台规则见 P01。
- 保存写入后台审计记录（操作者、时间、改了哪些设置），不含图片内容。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.9 小节 |
| --- | --- | --- |
| — | 本页不涉及金额与个人资料；标志图按商品图片的类型、大小限制与隔离存储处理；保存记入审计 | 「计价、优惠、积分与库存」（第 7 条）「数据模型」（`AdminAccount` / `AuditEvent`） |

---

## A09 后台站点设置（0.7）

- **目的**：开关全站短信验证（REQUIREMENTS 1.12「管理后台」，DESIGN 1.11「边界与原则」「数据模型」`SiteSetting`）。
- **入口**：后台导航 `[admin.nav_settings]`。
- **去向**：—（本页内完成；保存后对之后的请求立即生效）。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.sms_toggle_cost]`（开启会发送真实短信并产生费用）。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | [admin.nav_settings]                                      |
|           | [admin.sms_toggle]                                        |
|           |  ( ) [admin.sms_on]   (●) [admin.sms_off]                 |
|           |  [admin.sms_toggle_hint]                                  |
|           |  [admin.sms_toggle_cost]                                  |
|           | ( [common.save] )                                         |
|           | 保存成功：[admin.settings_saved]                            |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  [admin.nav_settings]        |
| [admin.sms_toggle]             |
| ( ) [admin.sms_on]             |
| (●) [admin.sms_off]            |
| [admin.sms_toggle_hint]        |
| [admin.sms_toggle_cost]        |
| ( [common.save] )              |
```

说明：

- 默认 `[admin.sms_off]`。开启前运营者须已开通并核实短信服务；开启后结账、注册、短信登录、重设密码与注销确认按 0.6 的短信流程进行，关闭后前台按 V1「短信验证开关关闭时」显示。
- 保存写入后台审计记录（操作者、时间、新旧值）。
- 本页沿用后台中性样式；视觉稿见 `docs/design/pages/` 的 A09 两张（0.8）。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.11 小节 |
| --- | --- | --- |
| — | 短信验证开关：关闭时全站不发短信，马新号码以游客下单，注册、短信登录与重设密码暂停，注销改以密码或已登录会话确认；保存记入审计 | 「边界与原则」「数据模型」（`SiteSetting`）「权限与资料保护」 |

## 演示提示汇总

| 位置 | 提示 |
| --- | --- |
| 所有前台页 | 常驻 `[common.demo_banner]` / `[common.demo_banner_short]`；页脚 `[common.footer_demo]` |
| 每页 ★ | P01 `home.demo_hint`；P02 `list.demo_hint`；P03 `detail.demo_hint`；P04 `cart.demo_hint`；P05 `checkout.demo_hint`；P06 `pay.demo_hint`；P07 `result.demo_hint`；P08 `lookup.demo_hint`；P09 `order.demo_hint`；P10 `refund.demo_hint`；P11 `auth.register_demo_hint`；P12 `auth.login_demo_hint`；P13 `account.demo_hint`；P14 `privacy.demo_hint`；A01–A08 `admin.demo_banner`；A08 另有 `admin.design_demo_note` |
| 店铺装修（0.4） | 演示横幅、每页 ★ 与 ◆ 提示、结账两处告知在所有主题与深浅色下照常显示，A08 不能隐藏或弱化；P01 的 ★ `home.demo_hint` 固定在所有区块之前 |
| ◆ 下单 | P05 下单按钮旁 `[checkout.place_order_hint]` |
| ◆ 模拟支付 | P06 成功/失败按钮旁 `[pay.action_hint]` |
| ◆ 退款 | P10 提交按钮旁 `[refund.submit_hint]`；后台 A03 `[admin.refund_hint]` |
| 结账手机号旁 | P05 `[checkout.phone_notice]`：马新号码会收到验证短信并自动注册会员、手机号保留至注销、可在会员中心注销；**无勾选框**；短信验证开关关闭时改为 `[checkout.phone_notice_sms_off]`（0.7） |
| 收货表单旁 | P05 `[checkout.form_notice]`：不真实扣款、不真实发货、收货资料会长期保存；**无确认勾选框，无删除入口** |
| 真实短信 | V1 `[auth.sms_scope]`；P11 `[auth.register_demo_hint]` |

## 不在本稿范围

- 视觉稿（配色、字体、图标、品牌素材）与具体组件实现。
- 任何 `frontend/`、后端、`docs/DESIGN.md`、`docs/REQUIREMENTS.md`、`docs/HANDOFF.md`、CI 或部署配置的改动。
- 浏览器验收脚本与步骤（页面实现任务再登记）。

## 待决问题

与 [UX-COPY.md](UX-COPY.md)「待决问题」同一编号、同一内容，共 19 项（Q1–Q15 编号不变，Q16–Q19 为 0.2 新增，0.3–0.7 未新增）。本稿只列出，不自行改设计或需求。状态：已决 17 项（Q1、Q2、Q5–Q19）；不再适用 2 项（Q3、Q4）；待决 0 项。Q11、Q14、Q16–Q19 依据 Kelvin 2026-09-30 的决定（记录见 `docs/HANDOFF.md`）在 0.3 转为已决。

- **Q1 下单后支付页与结果页的访问授权。** **已决**，依据 DESIGN 1.9「权限与资料保护」：每张游客订单创建后，服务端只给当前浏览器一个不可猜测、30 分钟有效、仅限该单的短期凭据，用于该单的模拟支付、失败重试、取消与结果页，这些页面可显示该单收货资料原文，凭据不能用于确认收货、退款或其他订单；查单通过后仅对该单在本浏览器保持 30 分钟，只能查看、确认收货和申请退款，不能支付或取消；两者均为服务端会话，经 HttpOnly、Secure、SameSite=Lax 的 cookie 交付，写操作另须 CSRF 令牌。线框见 P06、P07（`[G]`）与 P08–P10（`[L]`）；0.1 中「支付/结果页不显示收货资料原文」的假设随之取消。
- **Q2 第 30 天退款截止与收货资料删除同日。** **已决**，依据 DESIGN 1.9「资料保留」（收货资料不再删除，游客凭订单号与电话可随时查单）与「订单与退款状态」（支付成功后 30 天内可申请退款）：查单不再在第 30 天失效，两者不再冲突；退款截止时间以服务端返回的 `order.refund_deadline` 为准。
- **Q3 共享备份残留期的对外措辞。** **不再适用**：收货资料不再删除或匿名化（DESIGN 1.9「资料保留」），不存在收货资料在备份中残留的对外措辞问题；表单旁「30 天后匿名化」已删除，隐私页 `privacy.retention_recipient` 改写为长期保存，原 `privacy.retention_backup` 删除。会员注销后手机号与密码哈希在备份中残留的措辞另列为 Q18。
- **Q4 「30 天」的起算点。** **不再适用**：收货资料不再按 30 天删除，已无起算点；相关文案已改写。
- **Q5 会员能否在会员中心直接确认收货、申请退款。** **已决**，依据 DESIGN 1.9「订单与退款状态」「权限与资料保护」（会员在「我的订单」对自己认领或下单的订单确认收货、申请退款，规则与查单页相同）。线框以 P09、P10 的会员模式实现。
- **Q6 模拟支付方式清单。** **已决**，依据 Kelvin 2026-09-29 的决定：「演示银行卡」改为「演示信用卡/借记卡（无需输入卡号）」（`pay.method_card`）；演示网上银行、演示电子钱包保留；三项均不输入任何资料。
- **Q7 后台导出。** **已决**，依据 DESIGN 1.9「权限与资料保护」（首版不提供后台导出）：后台不设导出按钮。
- **Q8 商品文案回退英文时是否标示。** **已决**，依据 Kelvin 2026-09-29 的决定：回退英文时保留「仅英文」标签 `detail.english_only`。
- **Q9 参考币种的显示范围。** **已决**，依据 Kelvin 2026-09-29 的决定与 DESIGN 1.9「边界与原则」（按收货国家、不按 IP）：参考外币金额只在结账页选定收货国家后显示；首页、列表、详情、购物车只显示 MYR。
- **Q10 WhatsApp 联系方式未配置时的行为。** **已决**，依据 Kelvin 2026-09-29 的决定：配置缺失时隐藏 WhatsApp 按钮（页脚、隐私页联系段），不显示占位文字或「即将开放」。
- **Q11 虚构电话被真实号码持有人认领。** **已决**，依据 Kelvin 2026-09-30 的决定（见 `docs/HANDOFF.md`）：1.7 起马来西亚、新加坡（短信白名单）号码结账须先短信验证并成为会员，不再用于游客订单（DESIGN 1.9「权限与资料保护」），一般情形下游客订单上不再出现可被他人注册认领的白名单号码。**短信无法送达或停发时降级的游客下单**，以及**以后扩大白名单**时，这些游客订单仍可能被该号码的真实持有人注册后认领并看到收货资料（DESIGN 1.9 也写明认领主要在这两种情形下生效）；Kelvin 接受这一剩余风险。不新增或改动文案。**2026-10-01 补充**（Kelvin 决定，DESIGN 1.11「权限与资料保护」）：短信验证开关默认关闭，关闭期间马新号码也以游客下单；之后开启并由该号码的真实持有人验证时，这些订单同样会被认领并看到其中的收货资料。Kelvin 接受这一风险，照常认领，仍不新增或改动文案。
- **Q12 待支付订单的取消入口与操作者。** **已决**，依据 DESIGN 1.9「订单与退款状态」「权限与资料保护」：待支付订单由下单者在支付页取消，游客凭该单短期凭据，会员凭登录会话；P06 为两者都提供 `pay.cancel_order`；查单页不提供取消。
- **Q13 会员中心「优惠券」的含义。** **已决**，依据 Kelvin 2026-09-29 的决定：「我的优惠券」同时列出当前可用的公开券与本人使用记录（P13）。「公开券」的范围见 Q19。
- **Q14 马来文文案审校。** **已决**，依据 Kelvin 2026-09-30 的决定：先上线，上线后再由马来文母语者审校用词（如 troli、daftar keluar、bayaran balik）。UX-COPY 的马来文仍标为草稿；审校不再是页面实现的前置条件。
- **Q15 密码规则与注销确认方式。** **已决**，依据 DESIGN 1.9「权限与资料保护」：密码至少 8 位、不强制复杂度（`auth.password_rule`）；注销须先以短信验证码确认（P13 嵌入 V1）。**2026-10-01 补充**（Kelvin 决定，DESIGN 1.11「权限与资料保护」）：上述注销确认只在短信验证开关开启时适用；关闭时已设密码的会员以当前密码确认，未设密码的会员在已登录会话内二次确认（P13）。
- **Q16 结账第一步手机号的默认区号。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定与已批准的 DESIGN 1.9「权限与资料保护」：结账第 1 步国家码下拉列出所有国家、默认 +60；以 `+` 开头输入时以输入为准；服务端按所选或输入的国家码规范化为 E.164 并判定是否属于白名单，之后选的收货国家不改变已判定的号码。游客订单的收货电话即第 1 步号码（只读），下单时不按收货国家重新解析；以收货国家作为默认区号只用于会员改填的收货电话。线框见 P05；`checkout.phone_step_hint` 措辞不变。
- **Q17 会员待支付订单能否从会员中心回到支付页。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定（与 DESIGN 1.9「订单与退款状态」中会员凭登录会话在支付页取消一致）：会员可从 P09 会员模式的 `account.order_pay` 回到 P06，继续支付或取消自己的待支付订单。线框见 P06、P09、P13。
- **Q18 会员注销后手机号在共享备份中的残留期措辞。**（0.2 新增，承接原 Q3）**已决**，依据 Kelvin 2026-09-30 的决定与 DESIGN 1.9「资料保留」（注销时在线删除的手机号与密码哈希，最迟在注销后第 max(binlog 残留期, N) 天才从共享备份与 binlog 中消失，运营核实前没有确定上限）：隐私页对外披露。`privacy.member_backup` 改写为：注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间；不写天数，不写或暗示备份一定会在某时被清除，用「密码哈希」而不是「密码」；该键已定稿。
- **Q19 「可用的公开券」的范围。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定：「我的优惠券」的可用券列出所有启用中且在有效期内的券，对全部会员相同，不按每会员或总次数上限过滤；不改 DESIGN 1.9「数据模型」的 `Coupon`。P13 说明与后台 A05 的 `admin.coupon_listed_note` 按同一口径；已达上限的券在结账时由服务端拒绝。

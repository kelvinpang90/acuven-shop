# Acuven Shop 前端页面结构与线框（审阅稿）

> **审阅稿，Kelvin 审阅通过前不实现任何页面。**
> 版本 0.1（2026-09-29），任务 `SHOP-TASK-001`。依据 `docs/REQUIREMENTS.md` 1.5「前端页面设计安排」与 `docs/DESIGN.md` 1.6；三语文案见 [UX-COPY.md](UX-COPY.md)。本稿不改设计与需求，冲突之处列入文末「待决问题」。

## 0. 阅读说明

- 线框为文本线框，只表达区块、顺序与操作，不表达视觉样式。`[key]` 指 [UX-COPY.md](UX-COPY.md) 的文案键；`( … )` 是按钮；`[____]` 是输入框；`<…>` 是运行时数据。
- **默认语言英文**，页头可切换中文、马来文（见 UX-COPY「约定」）。
- 金额与个人资料元素在线框中用 `[M1]`（金额）、`[P1]`（个人资料）等标记，并在每页「金额与个人资料元素」表中逐处写出所依据的 `docs/DESIGN.md` 1.6 小节标题。DESIGN 1.6 的小节为：「边界与原则」「数据模型」「计价、优惠、积分与库存」「订单与退款状态」「失败、并发与重试」「权限与资料保护」「保留与匿名化」「上线依赖与设计闸门」。
- 所有金额由服务端计算后返回，前端只显示，不在浏览器计算价格、优惠、积分、运费或退款（DESIGN 1.6「数据模型」`Cart`、「计价、优惠、积分与库存」）。
- 页面路径仅作示意；**任何路径、查询参数与分析事件都不得包含订单号或电话**（DESIGN 1.6「权限与资料保护」：订单号不出现在公共索引或分析事件；应用日志不记录订单查询参数）。
- 本稿不写任何真实联系方式。WhatsApp 入口只写占位 `{{WHATSAPP_CONTACT_LINK}}`，上线前由私有配置提供（DESIGN 1.6「上线依赖与设计闸门」）。

## 1. 全局框架

所有前台页面共用：

- **演示横幅**：页头上方常驻、不可关闭，桌面显示 `[common.demo_banner]`，手机显示 `[common.demo_banner_short]`。这是每一页的基础演示提示；各页另有本页演示提示（★）。
- **页头**：品牌、搜索框、导航（商品、查询订单、登录/会员中心）、语言切换 EN | 中文 | BM、购物车数量。
- **页脚**：`[common.footer_demo]`、`[common.nav_privacy]` 链到 P14、`[common.whatsapp_cta]` 链到占位 `{{WHATSAPP_CONTACT_LINK}}`（配置缺失时的行为见待决 Q10）。不设站内联系表单。
- **网络中断**：下单、模拟支付、确认收货、退款申请提交后如网络中断，先显示 `[common.network_check]` 并查询原订单或申请状态，再允许重试；按钮提交期间禁用（DESIGN 1.6「失败、并发与重试」）。
- **限流/服务不可用**：查单、登录、短信被拒时显示 `[common.rate_limited]` 或 `[common.service_unavailable]`；商品浏览继续可用（DESIGN 1.6「失败、并发与重试」）。

桌面框架：

```text
+-----------------------------------------------------------------------+
| [common.demo_banner]                                 (常驻，不可关闭) |
+-----------------------------------------------------------------------+
| ACUVEN SHOP  [list.search_placeholder_______][搜]  Shop  Track order  |
|                          EN | 中文 | BM    Log in / My account  Cart(2)|
+-----------------------------------------------------------------------+
|                                                                       |
|                          <页面主体>                                    |
|                                                                       |
+-----------------------------------------------------------------------+
| [common.footer_demo]                                                  |
| [common.nav_privacy]        ( [common.whatsapp_cta] → 占位链接 )       |
+-----------------------------------------------------------------------+
```

手机框架：

```text
+--------------------------------+
| [common.demo_banner_short]     |
+--------------------------------+
| ☰  ACUVEN SHOP    EN▾  Cart(2) |
| [list.search_placeholder___]   |
+--------------------------------+
|        <页面主体，单列>          |
+--------------------------------+
| [common.footer_demo]           |
| [common.nav_privacy]           |
| ( [common.whatsapp_cta] )      |
+--------------------------------+
☰ 菜单：Shop / Track order / Log in 或 My account / Privacy / 语言
```

## 2. 页面地图

| 编号 | 页面 | 示意路径 | 主要去向 |
| --- | --- | --- | --- |
| P01 | 首页 | `/` | P02、P03、P14、WhatsApp 占位 |
| P02 | 商品列表（搜索、分类、属性筛选） | `/products` | P03 |
| P03 | 商品详情（规格、数量、加入购物车） | `/products/<slug>` | P04、P02 |
| P04 | 购物车 | `/cart` | P05、P02 |
| P05 | 结账（收货资料、优惠券、积分、运费、合计） | `/checkout` | P06、P11、P12、P14 |
| P06 | 模拟支付 | `/pay` | P07 |
| P07 | 模拟支付结果（成功/失败/已取消） | `/pay/result` | P06（重试）、P09、P02、P11 |
| P08 | 订单查询 | `/track` | P09 |
| P09 | 订单详情（确认收货、退款入口） | `/track/order` | P10、P08 |
| P10 | 退款申请 | `/track/order/refund` | P09 |
| P11 | 注册（短信验证） | `/register` | P13、P05（游客继续） |
| P12 | 登录与忘记密码 | `/login`、`/forgot-password` | P13、P11 |
| P13 | 会员中心（我的订单、积分、优惠券、注销） | `/account` | P09、P01 |
| P14 | 隐私说明 | `/privacy` | 来源页、WhatsApp 占位 |
| A01 | 后台登录 | `/admin/login` | A02 |
| A02 | 后台订单与模拟发货 | `/admin/orders` | A03 |
| A03 | 后台退款审核 | `/admin/refunds` | A02 |
| A04 | 后台商品、图片、规格与库存 | `/admin/products` | A07 |
| A05 | 后台优惠券 | `/admin/coupons` | — |
| A06 | 后台运费区与演示汇率 | `/admin/shipping` | — |
| A07 | 后台库存重置结果 | `/admin/stock-resets` | A04 |

主流程：P01 → P02 → P03 → P04 → P05 →（下单）→ P06 →（成功/失败）→ P07 →（失败重试回 P06）→ 管理员 A02 模拟发货 → P08 → P09（确认收货）→ P10（退款）→ 管理员 A03 审核 → P09 显示结果。

---

## P01 首页

- **目的**：一眼说明这是演示站，引导访客走完整购物流程；提供 WhatsApp 联系入口（占位）。
- **入口**：直接访问；任意页的品牌标志。
- **去向**：P02（开始购物、分类、搜索）；P03（精选商品）；P14；WhatsApp 占位链接。
- **演示提示**：常驻横幅；★ `[home.demo_hint]`；「演示怎么玩」四步。

桌面：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
+-----------------------------------------------------------------------+
| [home.hero_title]                                                     |
| [home.hero_body]                                                      |
| ( [home.hero_cta] )                                                   |
+-----------------------------------------------------------------------+
| [home.how_title]                                                      |
|  ① [home.how_1]  ② [home.how_2]  ③ [home.how_3]  ④ [home.how_4]      |
+-----------------------------------------------------------------------+
| [home.categories]                                                     |
|  [分类卡]  [分类卡]  [分类卡]  [分类卡]                                  |
+-----------------------------------------------------------------------+
| [home.featured]                          ★ [home.demo_hint]           |
|  [图]<名称>[M1]  [图]<名称>[M1]  [图]<名称>[M1]  [图]<名称>[M1]          |
+-----------------------------------------------------------------------+
| <页脚，含 WhatsApp 占位>                                               |
```

手机：

```text
| [common.demo_banner_short]     |
| <页头>                         |
| [home.hero_title]              |
| [home.hero_body]               |
| ( [home.hero_cta] )            |
| [home.how_title]               |
|  ① [home.how_1]                |
|  ② [home.how_2]                |
|  ③ [home.how_3]                |
|  ④ [home.how_4]                |
| [home.categories] 横向滑动      |
| [home.featured]                |
| ★ [home.demo_hint]             |
| [图]<名称>[M1] | [图]<名称>[M1] |
| <页脚>                         |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 商品卡价格（MYR；多规格时 `[list.price_from]`） | 「数据模型」「边界与原则」 |

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
| <属性，如颜色>     |                        [list.out_of_stock]         |
|  ☐ <值> ☐ <值>   | [图] <名称>        [图] <名称>      [图] <名称>     |
| <属性，如尺寸>     |      [M1]               [M1]             [M1]     |
|  ☐ <值> ☐ <值>   |                                                    |
| ([list.filter_clear])  < 1 2 3 >                                      |
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
| ( 加载更多 )                    |
+-- 筛选抽屉（全屏）-------------+
| [list.filter_category] ☐…      |
| <属性> ☐…                      |
| ([list.filter_clear]) ([list.filter_apply]) |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 商品卡价格与「价格」排序，仅显示 MYR，不显示参考币种（待决 Q9） | 「数据模型」「边界与原则」 |

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
| < 面包屑：Shop / <分类> / <名称> >                                    |
+-------------------------------+---------------------------------------+
| [主图]                        | <商品名称>   [detail.english_only]?   |
|                               | [M1] RM <所选规格单价>                 |
| [缩略图][缩略图][缩略图]        | [detail.options]                      |
|                               |  <颜色>: (红) (蓝) (黑)                |
|                               |  <尺寸>: (S) (M) (L)                   |
|                               | [detail.quantity]  ( - ) 1 ( + )      |
|                               | [detail.stock_left]                   |
|                               | ( [detail.add_to_cart] )              |
|                               | ★ [detail.demo_hint]                  |
+-------------------------------+---------------------------------------+
| [detail.description]                                                  |
| <描述>                                                                |
加入后提示条：[detail.added] ( [detail.view_cart] )
未选全规格点加入：[detail.select_all_options]
```

手机：

```text
| [common.demo_banner_short]     |
| <页头>                         |
| [主图，可左右滑动]               |
| <商品名称>                      |
| [M1] RM <单价>                 |
| [detail.options]               |
|  <颜色>: (红)(蓝)(黑)           |
|  <尺寸>: (S)(M)(L)              |
| [detail.quantity] (-) 1 (+)    |
| [detail.stock_left]            |
| ★ [detail.demo_hint]           |
| [detail.description] ▾         |
+--------------------------------+
| 底部固定：( [detail.add_to_cart] ) |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 所选规格的 MYR 单价；切换规格时由服务端数据刷新 | 「数据模型」「边界与原则」 |
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

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 行小计（单价 × 数量），由服务端按当前价格返回，不信任浏览器保存的价格 | 「数据模型」（`Cart`） |
| M2 | 商品小计；运费、优惠、积分不在此计算 | 「数据模型」（`Cart`）「计价、优惠、积分与库存」（第 1 条） |

---

## P05 结账

- **目的**：填写收货资料，会员可用优惠券与积分，查看 MYR 小计、抵扣、示例运费、合计及参考币种金额，提交演示订单。
- **入口**：P04 `[cart.checkout]`。
- **去向**：P06（下单成功）；P11/P12（游客点注册/登录，返回后保留购物车与已填内容）；P14（资料处理说明）。
- **演示提示**：常驻横幅；★ `[checkout.demo_hint]`；收货表单旁 `[checkout.form_notice]`（**不真实扣款、不真实发货、收货资料 30 天后匿名化；不设确认勾选框**）；◆ 下单按钮旁 `[checkout.place_order_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| <页头>                                                                |
| [checkout.title]                              ★ [checkout.demo_hint]  |
+---------------------------------------------+-------------------------+
| [checkout.guest_notice] ([common.nav_login]) ([common.nav_register])  |
|   （会员登录后不显示此行）                    | 订单摘要                 |
| [checkout.recipient_title]                  | <名称>/<规格> x2  [M1]   |
| ┌ 收货表单旁提示（常显，无勾选框）─────────┐   | <名称>/<规格> x1  [M1]   |
| │ [checkout.form_notice]                   │ |-------------------------|
| │ ([checkout.form_notice_link] → P14)      │ | [cart.subtotal]    [M2] |
| └──────────────────────────────────────────┘ | [checkout.summary_coupon] [M3] |
| [checkout.name]      [P1 ______________]    | [checkout.summary_points] [M4] |
| [checkout.country]   [P2 <国家> ▾]           | [checkout.summary_shipping][M5]|
| [checkout.state_my]  [P2 <州属> ▾]（马来西亚）| [checkout.summary_total]  [M6] |
|   或 [checkout.region] [P2 ________]（其他） | [M7] [common.fx_reference]      |
| [checkout.address]   [P1 ______________]    |   [checkout.fx_note]      |
| [checkout.postcode]  [P1 ______]            |   或 [checkout.fx_none]   |
| [checkout.phone]     [P3 ______________]    |                          |
|   [checkout.phone_hint]                     | ( [checkout.place_order] )|
|   [checkout.phone_lookup_hint]              | ◆ [checkout.place_order_hint] |
|   错误：[checkout.phone_invalid]             |                          |
|---------------------------------------------|                          |
| 会员：[checkout.coupon] [M3 ______] ([checkout.coupon_apply])          |
|       [checkout.points] [M4 ______]  [checkout.points_available]       |
|       [checkout.points_not_shipping]                                   |
| 游客：[checkout.coupon_members_only] [checkout.points_guest]           |
+---------------------------------------------+-------------------------+
提交中：按钮禁用，显示 [checkout.submitting]
```

手机（单列，摘要折叠在顶部）：

```text
| [common.demo_banner_short]     |
| [checkout.title]               |
| ★ [checkout.demo_hint]         |
| ▸ 订单摘要 [M6]（点开见明细）     |
| [checkout.guest_notice]        |
| ([common.nav_login]) ([common.nav_register]) |
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
| [checkout.phone]   [P3 ____]   |
|  [checkout.phone_hint]         |
|  [checkout.phone_lookup_hint]  |
| 会员：优惠券 [M3] / 积分 [M4]     |
|  [checkout.points_not_shipping]|
| 游客：[checkout.points_guest]    |
| [cart.subtotal]            [M2]|
| [checkout.summary_coupon]  [M3]|
| [checkout.summary_points]  [M4]|
| [checkout.summary_shipping][M5]|
| [checkout.summary_total]   [M6]|
| [M7][common.fx_reference]      |
| [checkout.fx_note]             |
+--------------------------------+
| 底部固定：[M6]                  |
| ( [checkout.place_order] )     |
| ◆ [checkout.place_order_hint]  |
```

说明：收货表单旁提示在桌面与手机都**常显在表单上方**，不折叠、不设勾选框。改变国家或州属后，运费与参考币种由服务端重算。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 摘要行金额（按服务端返回的价格快照） | 「计价、优惠、积分与库存」（第 1 条）「数据模型」（`Cart`） |
| M2 | 商品小计 | 「计价、优惠、积分与库存」（第 1 条） |
| M3 | 优惠券输入与抵扣额；仅会员；只抵商品额 | 「计价、优惠、积分与库存」（第 1、2 条）「数据模型」（`Coupon`） |
| M4 | 积分输入、可用积分与抵扣额；100 积分抵 RM1；仅会员；不抵运费 | 「计价、优惠、积分与库存」（第 1、3 条）「边界与原则」 |
| M5 | 示例运费（按国家，马来西亚按州属，其他国家兜底） | 「数据模型」（`ShippingRate`） |
| M6 | MYR 合计，不低于运费；不单列税费 | 「计价、优惠、积分与库存」（第 1 条）「边界与原则」 |
| M7 | 参考币种金额，按收货国家与固定演示汇率；该国无汇率时只显示 MYR | 「边界与原则」「数据模型」（`DemoFxRate`） |
| P1 | 收货人姓名、地址、邮编（自由文本，可虚构） | 「边界与原则」「数据模型」（`OrderRecipient`） |
| P2 | 国家、州属/地区（决定运费、参考币种与默认区号） | 「边界与原则」「权限与资料保护」 |
| P3 | 电话；按收货国家补默认区号，`+` 开头以输入为准；只做格式校验 | 「权限与资料保护」 |
| — | 表单旁告知 `[checkout.form_notice]`，无勾选框 | 「权限与资料保护」「保留与匿名化」 |
| — | 下单按钮：库存/券/积分预占，15 分钟未付自动取消；幂等提交 | 「计价、优惠、积分与库存」（第 1、3、6 条）「失败、并发与重试」 |

---

## P06 模拟支付

- **目的**：选择演示支付方式并点击「成功」或「失败」，不输入任何银行卡资料。
- **入口**：P05 下单成功；P07 失败后的 `[result.retry]`。
- **去向**：P07（成功/失败/已取消）。
- **演示提示**：常驻横幅；★ `[pay.demo_hint]`；◆ 成功/失败按钮旁 `[pay.action_hint]`；`[pay.expires]` 倒计时。

桌面：

```text
| [common.demo_banner]                                                  |
| [pay.title]   [common.demo_badge]            ★ [pay.demo_hint]        |
+-----------------------------------------------------------------------+
| [pay.order_no]  <订单号>  ([common.copy])                              |
| [pay.save_order_no]                                                   |
| [pay.amount_due]  [M1] RM <合计>   [M2] [common.fx_reference]?         |
| [pay.expires]  ⏱ <剩余分钟>                                            |
+-----------------------------------------------------------------------+
| [pay.choose_method]                                                   |
|  ( ) [pay.method_card]   ( ) [pay.method_bank]   ( ) [pay.method_ewallet] |
|                                                                       |
|  ( [pay.simulate_success] )     ( [pay.simulate_failure] )            |
|  ◆ [pay.action_hint]                                                  |
|                                                                       |
|  ([pay.cancel_order])  ← 待决 Q12                                      |
+-----------------------------------------------------------------------+
提交中：两个按钮禁用，显示 [pay.processing]
```

手机：

```text
| [common.demo_banner_short]     |
| [pay.title] [common.demo_badge]|
| ★ [pay.demo_hint]              |
| [pay.order_no]                 |
| <订单号> ([common.copy])        |
| [pay.save_order_no]            |
| [pay.amount_due]               |
| [M1] RM <合计>                  |
| [M2] [common.fx_reference]?    |
| [pay.expires] ⏱               |
| [pay.choose_method]            |
| ( ) [pay.method_card]          |
| ( ) [pay.method_bank]          |
| ( ) [pay.method_ewallet]       |
| ◆ [pay.action_hint]            |
| ( [pay.simulate_success] )     |
| ( [pay.simulate_failure] )     |
| ([pay.cancel_order])           |
```

说明：本页不显示收货资料原文（待决 Q1）。不出现任何卡号、有效期、CVV 输入框。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 应付金额（订单快照） | 「数据模型」（`Order`）「计价、优惠、积分与库存」（第 1 条） |
| M2 | 参考币种金额（下单时的汇率版本快照） | 「边界与原则」「数据模型」（`DemoFxRate`） |
| — | 订单号：查单凭据之一，不进路径、查询参数或分析事件 | 「权限与资料保护」 |
| — | 成功/失败按钮：幂等提交，失败停在 `awaiting_demo_payment` 可重试 | 「订单与退款状态」「失败、并发与重试」 |
| — | 倒计时与取消：15 分钟未付自动取消并释放预占 | 「计价、优惠、积分与库存」（第 6 条）「订单与退款状态」 |

---

## P07 模拟支付结果

- **目的**：显示模拟支付成功、失败或订单已超时取消，并给出下一步。
- **入口**：P06 点击成功/失败后；超时后再次打开支付页。
- **去向**：成功 → P09（`[result.track]`）、P02（`[result.continue]`）、游客可去 P11；失败 → P06（`[result.retry]`，同一订单）；已取消 → P02。
- **演示提示**：常驻横幅；★ `[result.demo_hint]`；成功文案写明未扣真实款项。

桌面：

```text
| [common.demo_banner]                                                  |
+---------------------------- 成功 ------------------------------------+
| ✓ [result.success_title]                                              |
| [result.success_body]                                                 |
| [pay.order_no] <订单号> ([common.copy])   [pay.save_order_no]          |
| [pay.amount_due] [M1]                                                 |
| 会员：[result.points_earned] [M2]   游客：[result.guest_register] → P11 |
| ★ [result.demo_hint]                                                  |
| ( [result.track] )   ([result.continue])                               |
+---------------------------- 失败 ------------------------------------+
| ✗ [result.failure_title]                                              |
| [result.failure_body]                                                 |
| [pay.expires] ⏱                                                       |
| ( [result.retry] )                                                    |
+---------------------------- 已取消 ----------------------------------+
| [result.cancelled]            ([result.continue])                     |
```

手机：

```text
| [common.demo_banner_short]     |
| ✓ [result.success_title]       |
| [result.success_body]          |
| [pay.order_no]                 |
| <订单号> ([common.copy])        |
| [pay.save_order_no]            |
| [M1]                           |
| [result.points_earned] [M2]    |
|  或 [result.guest_register]     |
| ★ [result.demo_hint]           |
| ( [result.track] )             |
| ([result.continue])            |
--- 失败时替换为 ---
| ✗ [result.failure_title]       |
| [result.failure_body]          |
| [pay.expires] ⏱               |
| ( [result.retry] )             |
--- 已取消时 ---
| [result.cancelled]             |
| ([result.continue])            |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 已模拟支付金额（快照） | 「数据模型」（`Order`、`PaymentAttempt`） |
| M2 | 本单获得积分（整单现金实付每满 RM1 得 1 积分；游客不累积） | 「计价、优惠、积分与库存」（第 3 条） |
| — | 重试不重复下单 | 「订单与退款状态」「失败、并发与重试」 |

---

## P08 订单查询

- **目的**：以订单号及下单电话查询订单（不使用短信验证码）。
- **入口**：页头「Track order」；P07；P14。
- **去向**：P09（查询成功）；失败留在本页。
- **演示提示**：常驻横幅；★ `[lookup.demo_hint]`；`[lookup.privacy_warning]`（剩余风险告知）。

桌面：

```text
| [common.demo_banner]                                                  |
| [lookup.title]                               ★ [lookup.demo_hint]     |
+-----------------------------------------------------------------------+
|   [pay.order_no]   [P1 ______________________]                        |
|   [lookup.phone]   [P2 ______________________]                        |
|                    [checkout.phone_hint]                              |
|   ( [lookup.submit] )                                                 |
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
| [P1 ______________________]    |
| [lookup.phone]                 |
| [P2 ______________________]    |
| [checkout.phone_hint]          |
| ( [lookup.submit] )            |
| [lookup.privacy_warning]       |
| <错误提示>                      |
```

说明：订单号与电话只以请求体提交，不进路径或查询参数；「不存在」「电话不符」「已匿名化」统一显示 `[lookup.not_found]`，不泄露订单是否存在。

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| P1 | 订单号输入 | 「权限与资料保护」 |
| P2 | 电话输入，按与下单相同的 E.164 规范化比对 | 「权限与资料保护」 |
| — | 查询限流、防枚举、不缓存敏感响应；Redis 不可用时拒绝 | 「权限与资料保护」「失败、并发与重试」 |
| — | 仅凭电话可查 30 天（资料删除后失效） | 「保留与匿名化」 |

---

## P09 订单详情（确认收货与退款入口）

- **目的**：查看订单状态、商品与金额、完整收货资料，确认收货，查看退款记录并进入退款申请。
- **入口**：P08 查询成功；P07 `[result.track]`；P13 我的订单（会员，待决 Q5）。
- **去向**：P10；P08（返回）。
- **演示提示**：常驻横幅；★ `[order.demo_hint]`；确认收货旁 `[order.confirm_receipt_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [order.title]   状态：[order.status_*]       ★ [order.demo_hint]      |
| 进度：待支付 → 已支付 → 已打包 → 已发货 → 已完成（均带「演示」）       |
+---------------------------------------------+-------------------------+
| [order.items]                               | [checkout.recipient_title] |
| <名称>/<规格> x2 [order.unit_price][M1]       | [P1] <姓名>             |
|        [order.cash_paid] [M2]               | [P1] <电话>             |
| <名称>/<规格> x1 …                           | [P1] <地址>/<邮编>      |
|---------------------------------------------|      <地区>/<国家>      |
| [cart.subtotal]            [M3]             | 或 [order.recipient_anonymised] |
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
```

手机：

```text
| [common.demo_banner_short]     |
| [order.title]                  |
| [order.status_*]               |
| ★ [order.demo_hint]            |
| 进度条（纵向）                  |
| [order.items]                  |
| <名称>/<规格> x2               |
|  [M1] / [order.cash_paid][M2]  |
| ▸ 金额明细 [M3]                 |
| ▸ [checkout.recipient_title]   |
|    [P1] 姓名/电话/地址          |
|    或 [order.recipient_anonymised] |
| ( [order.confirm_receipt] )    |
| [order.confirm_receipt_hint]   |
| ( [order.request_refund] )     |
| [order.refund_deadline]        |
| [order.refunded_total] [M4]    |
| [order.refundable_left] [M4]   |
| ▸ [order.refund_requests]      |
| [order.fulfilment_frozen]?     |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 商品单价快照 | 「数据模型」（`OrderItem`） |
| M2 | 逐件现金实付快照（不含积分抵扣） | 「计价、优惠、积分与库存」（第 2、4 条） |
| M3 | 小计、优惠、积分、示例运费、合计快照 | 「数据模型」（`Order`）「计价、优惠、积分与库存」（第 7 条） |
| M4 | 累计已退、剩余可退、每笔申请金额 | 「订单与退款状态」 |
| P1 | 完整收货姓名、电话、地址（查询通过后可见；到期显示已匿名化） | 「权限与资料保护」「保留与匿名化」 |
| — | 确认收货：仅 `demo_shipped` 可点；7 天自动完成；全退后冻结 | 「订单与退款状态」 |
| — | 退款入口：支付后 30 天内显示（待决 Q2） | 「订单与退款状态」 |

---

## P10 退款申请

- **目的**：选择要退的商品与数量，查看系统计算的模拟退款金额（会员另见积分返还与追回），提交申请。
- **入口**：P09 `[order.request_refund]`。
- **去向**：P09（提交后显示 `[refund.submitted]` 与记录）。
- **演示提示**：常驻横幅；★ `[refund.demo_hint]`；◆ 提交按钮旁 `[refund.submit_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [refund.title]   [order.title]                ★ [refund.demo_hint]    |
+-----------------------------------------------------------------------+
| [refund.select_items]                                                 |
|  <名称>/<规格>  [order.cash_paid] [M1]   数量 (-) 0 (+) [refund.max_qty] |
|  <名称>/<规格>  [order.cash_paid] [M1]   数量 (-) 0 (+) [refund.max_qty] |
+-----------------------------------------------------------------------+
| [refund.estimate]         [M2]                                        |
| 会员：[refund.points_back] [M3]  [refund.points_reversed] [M3]         |
|       [refund.expired_points_note]                                    |
| [refund.shipping_not_refunded]  [refund.coupon_not_restored]          |
| ( [refund.submit] )                                                   |
| ◆ [refund.submit_hint]                                                |
| 错误：[refund.duplicate] / [refund.nothing_left] / [refund.window_closed] |
```

手机：

```text
| [common.demo_banner_short]     |
| [refund.title]                 |
| ★ [refund.demo_hint]           |
| [refund.select_items]          |
| <名称>/<规格>                   |
|  [order.cash_paid] [M1]        |
|  (-) 0 (+) [refund.max_qty]    |
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

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 每件现金实付快照；可退数量 = 购买数量 − 已批准 − 审核中 | 「订单与退款状态」「数据模型」（`RefundLine`） |
| M2 | 预计模拟退现金，由服务端按逐件快照计算；运费不退 | 「订单与退款状态」「计价、优惠、积分与库存」（第 4、5 条） |
| M3 | 返还抵扣积分（已过期不返还）、追回获得积分 | 「计价、优惠、积分与库存」（第 5 条）「订单与退款状态」 |
| — | 提交幂等；同一可退数量重复申请被拒 | 「失败、并发与重试」「订单与退款状态」 |

---

## P11 注册（短信验证）

- **目的**：以马来西亚或新加坡手机号经真实短信验证注册会员，设置密码。
- **入口**：页头「Register」；P05 游客提示；P07 `[result.guest_register]`；P12。
- **去向**：P13（注册成功并登录）；P05 或 P01（`[auth.continue_guest]`）。
- **演示提示**：常驻横幅；★ `[auth.register_demo_hint]`（写明这是真实短信）；`[auth.sms_scope]`。

桌面（居中单栏卡片）：

```text
| [common.demo_banner]                                                  |
|            +-------------------------------------------+              |
|            | [auth.register_title]                     |              |
|            | ★ [auth.register_demo_hint]               |              |
|            | [auth.sms_scope]                          |              |
|            | [auth.phone] [+60 ▾][P1 ______________]   |              |
|            | [auth.challenge]                          |              |
|            | [ 托管人机挑战 ]                           |              |
|            | ( [auth.send_code] )                      |              |
|            | [auth.code_sent]                          |              |
|            | [auth.code]     [______]                  |              |
|            | [auth.password] [P2 ______________]       |              |
|            | [auth.claim_notice]                       |              |
|            | ( [auth.register_submit] )                |              |
|            | 错误：[auth.not_supported_country] /       |              |
|            |   [auth.sms_failed] / [auth.code_wrong] / |              |
|            |   [common.rate_limited]                   |              |
|            |   → ([auth.continue_guest])               |              |
|            +-------------------------------------------+              |
```

手机：

```text
| [common.demo_banner_short]     |
| [auth.register_title]          |
| ★ [auth.register_demo_hint]    |
| [auth.sms_scope]               |
| [auth.phone]                   |
| [+60▾][P1 _______________]     |
| [ 托管人机挑战 ]                 |
| ( [auth.send_code] )           |
| [auth.code_sent]               |
| [auth.code] [______]           |
| [auth.password] [P2 ______]    |
| [auth.claim_notice]            |
| ( [auth.register_submit] )     |
| <错误> ([auth.continue_guest])  |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| P1 | 手机号（E.164；白名单马来西亚、新加坡；`{phoneMasked}` 只显示部分号码） | 「边界与原则」「失败、并发与重试」「权限与资料保护」 |
| P2 | 密码（安全哈希，规则待决 Q15） | 「权限与资料保护」「数据模型」（`Member`） |
| — | 人机挑战在发送短信前；短信失败不创建账号 | 「失败、并发与重试」 |
| — | 自动认领近 30 天、未匿名化的游客订单，不补发积分 | 「权限与资料保护」「计价、优惠、积分与库存」（第 3 条） |

---

## P12 登录与忘记密码

- **目的**：会员以手机号加密码登录；忘记密码时经短信验证重设。
- **入口**：页头「Log in」；P05 游客提示；P11。
- **去向**：登录成功 → 来源页或 P13；P11（未注册）；重设完成 → 登录。
- **演示提示**：常驻横幅；★ `[auth.login_demo_hint]`；忘记密码步骤显示 `[auth.sms_scope]`。

桌面：

```text
| [common.demo_banner]                                                  |
|    +------------ 登录 ------------+   +-------- 忘记密码 ---------+     |
|    | [auth.login_title]           |   | [auth.reset_title]       |     |
|    | ★ [auth.login_demo_hint]     |   | [auth.sms_scope]         |     |
|    | [auth.phone] [+60▾][P1 ____] |   | [auth.phone] [P1 ____]   |     |
|    | [auth.password] [P2 ____]    |   | [ 托管人机挑战 ]          |     |
|    | ( [auth.login_submit] )      |   | ( [auth.send_code] )     |     |
|    | ([auth.forgot]) → 右栏        |   | [auth.code] [____]       |     |
|    | [common.nav_register] → P11  |   | [auth.new_password][P2]  |     |
|    | 错误：[auth.login_failed] /   |   | ( [auth.reset_submit] )  |     |
|    |  [common.rate_limited]       |   | [auth.reset_done]        |     |
|    +------------------------------+   +--------------------------+     |
（忘记密码为独立步骤页，桌面以右栏示意）
```

手机：

```text
| [common.demo_banner_short]     |
| [auth.login_title]             |
| ★ [auth.login_demo_hint]       |
| [auth.phone]                   |
| [+60▾][P1 _______________]     |
| [auth.password] [P2 ______]    |
| ( [auth.login_submit] )        |
| ([auth.forgot])                |
| [common.nav_register]          |
--- 忘记密码（下一屏）---
| [auth.reset_title]             |
| [auth.sms_scope]               |
| [auth.phone] [P1 ____]         |
| [ 托管人机挑战 ]                 |
| ( [auth.send_code] )           |
| [auth.code] [____]             |
| [auth.new_password] [P2 ____]  |
| ( [auth.reset_submit] )        |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| P1 | 手机号 | 「权限与资料保护」「失败、并发与重试」 |
| P2 | 密码、新密码 | 「权限与资料保护」 |
| — | 登录/重设限流；错误不区分号码是否已注册 | 「失败、并发与重试」「权限与资料保护」 |

---

## P13 会员中心

- **目的**：查看我的订单（含自动认领的游客订单）、积分余额/到期/待抵扣与明细、优惠券，退出登录，注销账号。
- **入口**：页头「My account」；P11 注册成功；P12 登录成功。
- **去向**：P09（订单详情，待决 Q5）；P01（退出或注销后）。
- **演示提示**：常驻横幅；★ `[account.demo_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [common.nav_account]   <手机号部分遮盖 [P1]>   ([common.nav_logout])   |
+----------------+------------------------------------------------------+
| [account.orders]| ★ [account.demo_hint]                               |
| [account.points]| [account.orders]                                    |
| [account.coupons]| <日期> <订单号> [order.status_*] [M1]  ( 查看 → P09 )|
| [account.delete]| <日期> <订单号> [order.status_*] [M1]                |
|                |   [order.recipient_anonymised]（已匿名化的只显示摘要）|
|                |------------------------------------------------------|
|                | [account.points]                                     |
|                | [account.points_balance] [M2]                        |
|                | [account.points_pending] [M2]（>0 时显示）             |
|                | [account.points_expiry]  [M2]                        |
|                | [account.points_history]: <日期> <类型> ±<积分> <订单> |
|                |------------------------------------------------------|
|                | [account.coupons]（待决 Q13）                          |
|                | <代码> [account.coupon_used_on] [M3]                 |
|                |------------------------------------------------------|
|                | [account.delete]                                     |
|                | [account.delete_warning]                             |
|                | ( [account.delete_confirm] )（确认方式待决 Q15）        |
+----------------+------------------------------------------------------+
```

手机（分段标签）：

```text
| [common.demo_banner_short]     |
| [common.nav_account]           |
| ★ [account.demo_hint]          |
| [订单][积分][优惠券][设置]        |
--- 订单 ---
| <日期> [order.status_*]        |
| <订单号> [M1]  >               |
--- 积分 ---
| [account.points_balance] [M2]  |
| [account.points_pending] [M2]  |
| [account.points_expiry] [M2]   |
| ▸ [account.points_history]     |
--- 优惠券 ---
| <代码> [account.coupon_used_on] [M3] |
--- 设置 ---
| [P1] <手机号部分遮盖>           |
| ([common.nav_logout])          |
| [account.delete]               |
| [account.delete_warning]       |
| ( [account.delete_confirm] )   |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 订单合计快照；匿名化后仍显示摘要 | 「数据模型」（`Order`）「保留与匿名化」 |
| M2 | 积分余额、待抵扣、到期批次、流水 | 「计价、优惠、积分与库存」（第 3、5 条）「数据模型」（`PointLedger`） |
| M3 | 优惠券使用记录 | 「数据模型」（`Coupon` / `CouponUse`） |
| P1 | 会员手机号（部分遮盖显示） | 「权限与资料保护」「保留与匿名化」 |
| — | 注销：撤销会话、删除手机号与密码、解除订单关联、积分与未用券作废 | 「权限与资料保护」 |
| — | 仅显示本人下单或认领的订单 | 「权限与资料保护」 |

---

## P14 隐私说明

- **目的**：说明收集哪些资料、用途、保留与匿名化时间、查单剩余风险、短信与日志，以及联系入口（占位）。
- **入口**：页脚「Privacy」；P05 `[checkout.form_notice_link]`。
- **去向**：来源页；WhatsApp 占位链接。
- **演示提示**：常驻横幅；★ `[privacy.demo_hint]`。

桌面：

```text
| [common.demo_banner]                                                  |
| [privacy.title]                              ★ [privacy.demo_hint]    |
+-----------------------------------------------------------------------+
| [privacy.intro]                                                       |
| 收集什么   [P1] [privacy.collect]                                      |
|           [privacy.fictional]                                         |
| 保留多久   [P2] [privacy.retention_recipient]                          |
|           [P3] [privacy.retention_backup]（草稿，待决 Q3）              |
|           [P4] [privacy.member]                                       |
| 谁能看到   [P5] [privacy.lookup_risk]                                  |
| 短信与日志 [P6] [privacy.sms]  [privacy.logs]                           |
| 联系      [privacy.contact] ( WhatsApp → {{WHATSAPP_CONTACT_LINK}} 占位 ) |
```

手机：

```text
| [common.demo_banner_short]     |
| [privacy.title]                |
| ★ [privacy.demo_hint]          |
| [privacy.intro]                |
| ▾ 收集什么 [P1]                 |
| ▾ 保留多久 [P2][P3][P4]         |
| ▾ 谁能看到 [P5]                 |
| ▾ 短信与日志 [P6]               |
| [privacy.contact]              |
| ( WhatsApp 占位 )               |
（手机上各段默认展开；▾ 仅示意分段）
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| P1 | 收集字段清单、不收集邮箱与证件、可虚构 | 「边界与原则」 |
| P2 | 收货资料在线 30 天删除及起算点 | 「保留与匿名化」 |
| P3 | 共享备份残留说明（不写天数） | 「保留与匿名化」「上线依赖与设计闸门」 |
| P4 | 会员手机号保留至注销；短信记录短期保留 | 「保留与匿名化」 |
| P5 | 订单号 + 电话可见完整收货资料的剩余风险 | 「权限与资料保护」 |
| P6 | 真实短信；日志最长 30 天且不含个人资料 | 「失败、并发与重试」「权限与资料保护」「保留与匿名化」 |

---

## 管理后台总体

- 单一管理员；后台同样支持英文、中文、马来文，默认英文。
- 每页顶部常驻 `[admin.demo_banner]`，作为后台每页的演示提示（★）；各页另有操作旁提示。
- 桌面：左侧导航 + 右侧内容；手机：顶部 ☰ 导航，表格改为卡片列表。
- 所有权限由服务端检查，前端隐藏按钮不代替授权（DESIGN 1.6「权限与资料保护」）。

## A01 后台登录

- **目的**：管理员以用户名与密码登录。
- **入口**：直接访问后台路径（前台不放入口链接）。
- **去向**：A02。
- **演示提示**：★ `[admin.demo_banner]`。

桌面：

```text
| [admin.demo_banner]                                                   |
|            +-------------------------------+                          |
|            | [admin.login_title]   EN|中文|BM |                          |
|            | [admin.username] [________]   |                          |
|            | [auth.password]  [________]   |                          |
|            | ( [auth.login_submit] )       |                          |
|            | 错误：[auth.login_failed] /    |                          |
|            |   [admin.locked]              |                          |
|            +-------------------------------+                          |
```

手机：

```text
| [admin.demo_banner]            |
| [admin.login_title]  EN▾       |
| [admin.username] [________]    |
| [auth.password]  [________]    |
| ( [auth.login_submit] )        |
| <错误>                          |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
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
| ☰ 导航     | [admin.search_order][________]  [admin.filter_status] ▾   |
| Orders    | 订单号     日期     状态             合计   退款            |
| Refunds   | <订单号>   <日期>   [order.status_*]  [M1]   <n 待审>        |
| Products  |-----------------------------------------------------------|
| Coupons   | 选中订单详情                                               |
| Shipping  |  [order.items] <名称>/<规格> x2 [M2]                       |
| Stock     |  金额明细 [M1]                                             |
| resets    |  [admin.recipient_raw]  [P1] <姓名>/<电话>/<地址>/<邮编>    |
|           |    [admin.recipient_audited] / 或 [order.recipient_anonymised] |
|           |  ( [admin.mark_packed] ) ( [admin.mark_shipped] )          |
|           |  [admin.ship_hint]                                         |
|           |  全部已退：[admin.frozen]（按钮禁用）                        |
|           |  事件记录：<时间> <状态变更> <操作者类别>                    |
+-----------+-----------------------------------------------------------+
（不设导出按钮，待决 Q7）
```

手机：

```text
| [admin.demo_banner]            |
| ☰  Orders                      |
| [admin.search_order][____]     |
| [admin.filter_status] ▾        |
| <订单号> [order.status_*]      |
|  <日期> [M1]  >                |
--- 详情（下一屏）---
| [order.items] … [M2]           |
| ▸ 金额明细 [M1]                 |
| ▸ [admin.recipient_raw] [P1]   |
|   [admin.recipient_audited]    |
| ( [admin.mark_packed] )        |
| ( [admin.mark_shipped] )       |
| [admin.ship_hint]              |
| [admin.frozen]?                |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 订单金额快照 | 「数据模型」（`Order`） |
| M2 | 行项目与逐件分摊快照 | 「数据模型」（`OrderItem`）「计价、优惠、积分与库存」（第 2 条） |
| P1 | 原始收货资料，仅管理员可见，查看留审计；到期显示已匿名化 | 「权限与资料保护」「保留与匿名化」 |
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
| ☰ 导航     | [admin.filter_status] ▾ (requested/approved/rejected)      |
|           | 申请时间  订单号  商品 x数量   金额   状态                  |
|           | <时间>   <订单号> <名称> x1   [M1]   [order.refund_requested] |
|           |-----------------------------------------------------------|
|           | 选中申请                                                   |
|           |  <名称>/<规格> 申请 1 / 购买 2 / 已批准 0                    |
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
| ☰  Refunds                     |
| [admin.filter_status] ▾        |
| <时间> <订单号>                 |
|  <名称> x1 [M1] [状态]  >       |
--- 详情 ---
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

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 模拟退现金，按原逐件现金实付快照 | 「订单与退款状态」「计价、优惠、积分与库存」（第 4、5 条） |
| M2 | 返还抵扣积分与追回获得积分，余额不足记待抵扣 | 「计价、优惠、积分与库存」（第 5 条）「订单与退款状态」 |
| M3 | 累计已退与剩余可退，累计上限约束 | 「计价、优惠、积分与库存」（第 5 条） |
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
| ☰ 导航     | 商品列表：<名称> <分类> [admin.active] ( 编辑 ) ( 新建 )    |
|           |-----------------------------------------------------------|
|           | 编辑商品   语言页签：[EN] [中文] [BM]                        |
|           |  [admin.product_name] [__________]  描述 [__________]      |
|           |  [admin.translation_missing]（缺译时）                     |
|           |  分类 [▾]   [admin.active] ☐                               |
|           |  [admin.product_images] [上传] [admin.image_rules]         |
|           |  [admin.product_options] 颜色: 红,蓝  尺寸: S,M             |
|           |  SKU   规格    [admin.price]  [admin.initial_stock] [admin.available_today] |
|           |  <sku> 红/S    [M1 ____]      [M2 ___]           <n> ([admin.adjust_today]) |
|           |  [admin.initial_stock_note]  [admin.rules_apply_new]       |
|           |  ( [common.save] )                                        |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  Products   ( 新建 )         |
| <名称> [admin.active]  >       |
--- 编辑 ---
| [EN][中文][BM]                 |
| [admin.product_name] [____]    |
| [admin.translation_missing]?   |
| [admin.product_images] [上传]  |
| [admin.image_rules]            |
| SKU 卡片：                      |
|  <sku> 红/S                    |
|  [admin.price] [M1 ____]       |
|  [admin.initial_stock][M2 __]  |
|  [admin.available_today] <n>   |
|  ([admin.adjust_today])        |
| [admin.initial_stock_note]     |
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 规格 MYR 价格（非负；只影响新订单） | 「数据模型」（`Product` / `Variant`）「计价、优惠、积分与库存」（第 7 条） |
| M2 | 每日初始库存（次日生效）与当日库存调整 | 「计价、优惠、积分与库存」（第 6 条） |
| — | 图片类型、大小限制与隔离存储 | 「计价、优惠、积分与库存」（第 7 条）「数据模型」（`AdminAccount` / `AuditEvent`） |

## A05 后台优惠券

- **目的**：创建、停用固定金额或百分比优惠券，设置有效期、最低消费、总次数与每会员次数上限。
- **入口**：后台导航。
- **去向**：—（本页内完成）。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.rules_apply_new]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | 代码   类型   面额   有效期   已用/上限   状态  ( 新建 )     |
|           | <代码> 百分比 [M1]   <起止>   <n>/<m>    启用 ([admin.coupon_disable]) |
|           |-----------------------------------------------------------|
|           | [admin.coupon_code] [______]                              |
|           | ( ) [admin.coupon_fixed] [M1 ____]  ( ) [admin.coupon_percent] [M1 __] |
|           | [admin.coupon_valid] [____] ~ [____]                      |
|           | [admin.coupon_min_spend] [M2 ____]                        |
|           | [admin.coupon_total_limit] [__] [admin.coupon_member_limit] [__] |
|           | [admin.rules_apply_new]      ( [common.save] )            |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  Coupons   ( 新建 )          |
| <代码> [M1] <状态>              |
|  <n>/<m>  ([admin.coupon_disable]) |
--- 新建 ---
| [admin.coupon_code] [____]     |
| 类型 ( )固定 ( )百分比           |
| 面额 [M1 ____]                  |
| [admin.coupon_valid] [__]~[__] |
| [admin.coupon_min_spend][M2 _] |
| [admin.coupon_total_limit][__] |
| [admin.coupon_member_limit][__]|
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 固定金额（RM）或百分比 | 「数据模型」（`Coupon`）「计价、优惠、积分与库存」（第 2 条） |
| M2 | 最低商品消费 | 「数据模型」（`Coupon`） |
| — | 使用上限与预占；修改只影响新订单 | 「计价、优惠、积分与库存」（第 1、7 条） |

## A06 后台运费区与演示汇率

- **目的**：维护各国示例运费、马来西亚各州属运费、「其他国家」兜底运费，以及参考币种固定演示汇率。
- **入口**：后台导航。
- **去向**：—。
- **演示提示**：★ `[admin.demo_banner]`；`[admin.rules_apply_new]`。

桌面：

```text
| [admin.demo_banner]                                                   |
+-----------+-----------------------------------------------------------+
| ☰ 导航     | 运费                                                      |
|           | [admin.shipping_my_state]                                 |
|           |   <州属>  [admin.shipping_fee] [M1 ____]  × 各州           |
|           | [admin.shipping_country] <国家> [M1 ____]  ( 新增国家 )     |
|           | [admin.shipping_other]   [M1 ____]                        |
|           |-----------------------------------------------------------|
|           | 演示汇率                                                   |
|           |  <国家> → <币种>  [admin.fx_rate] [M2 ____]   版本 <n>      |
|           | [admin.rules_apply_new]      ( [common.save] )            |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  Shipping & demo rates       |
| ▸ [admin.shipping_my_state]    |
|   <州属> [M1 ____]              |
| ▸ 国家运费                      |
|   <国家> [M1 ____]              |
| [admin.shipping_other][M1 __]  |
| ▸ 演示汇率                      |
|   <币种> [M2 ____]              |
| [admin.rules_apply_new]        |
| ( [common.save] )              |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 国家/州属/兜底示例运费（MYR） | 「数据模型」（`ShippingRate`） |
| M2 | 固定演示汇率及版本；只作参考不参与结算 | 「边界与原则」「数据模型」（`DemoFxRate`） |

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
|           | 日期（马来西亚时间）  结果                     SKU 数        |
|           | <日期>               [admin.stock_reset_ok]    <n>         |
|           | <日期>               [admin.stock_reset_failed] <n>        |
|           |  ▸ 展开：<sku> 初始 <a> − 有效预留 <b> = 当日可用 [M1]      |
+-----------+-----------------------------------------------------------+
```

手机：

```text
| [admin.demo_banner]            |
| ☰  Stock resets                |
| [admin.stock_reset_note]       |
| <日期> [admin.stock_reset_ok]  |
| <日期> [admin.stock_reset_failed] |
|  ▸ <sku> 当日可用 [M1]          |
```

金额与个人资料元素：

| 标记 | 元素 | 依据 DESIGN 1.6 小节 |
| --- | --- | --- |
| M1 | 当日可用库存 = 初始库存 − 仍有效预留；不改历史订单 | 「计价、优惠、积分与库存」（第 6 条） |
| — | 重置可重复运行、只生效一次；失败告警 | 「失败、并发与重试」 |

---

## 演示提示汇总

| 位置 | 提示 |
| --- | --- |
| 所有前台页 | 常驻 `[common.demo_banner]` / `[common.demo_banner_short]`；页脚 `[common.footer_demo]` |
| 每页 ★ | P01 `home.demo_hint`；P02 `list.demo_hint`；P03 `detail.demo_hint`；P04 `cart.demo_hint`；P05 `checkout.demo_hint`；P06 `pay.demo_hint`；P07 `result.demo_hint`；P08 `lookup.demo_hint`；P09 `order.demo_hint`；P10 `refund.demo_hint`；P11 `auth.register_demo_hint`；P12 `auth.login_demo_hint`；P13 `account.demo_hint`；P14 `privacy.demo_hint`；A01–A07 `admin.demo_banner` |
| ◆ 下单 | P05 下单按钮旁 `[checkout.place_order_hint]` |
| ◆ 模拟支付 | P06 成功/失败按钮旁 `[pay.action_hint]` |
| ◆ 退款 | P10 提交按钮旁 `[refund.submit_hint]`；后台 A03 `[admin.refund_hint]` |
| 收货表单旁 | P05 `[checkout.form_notice]`：不真实扣款、不真实发货、收货资料 30 天后匿名化；**无确认勾选框** |

## 不在本稿范围

- 视觉稿（配色、字体、图标、品牌素材）与具体组件实现。
- 任何 `frontend/`、后端、`docs/DESIGN.md`、`docs/REQUIREMENTS.md`、CI 或部署配置的改动。
- 浏览器验收脚本与步骤（页面实现任务再登记）。

## 待决问题

与 [UX-COPY.md](UX-COPY.md)「待决问题」同一编号、同一内容，共 15 项。本稿只列出，不自行改设计或需求。

- **Q1 下单后支付页与结果页的访问授权。** DESIGN 1.6「权限与资料保护」写「访客仅能访问已通过查询验证的订单」，但游客下单后需要直接进入模拟支付、失败重试与结果页，未定义此时如何授权。线框假设下单响应只给当前浏览器一个仅限该单支付与结果查看的短期凭据，且支付/结果页不显示收货资料原文。同理，查单通过后在本浏览器保持多久也未定义。需 Kelvin 决定；若需改权限规则，可能触及设计闸门。
- **Q2 第 30 天退款截止与收货资料删除同日。** 游客退款须先凭电话查单，而已支付订单的收货资料也在支付后第 30 天删除，届时查单失效，游客退款入口随之关闭；两者的先后与具体截止时刻未定义。线框显示服务端给出的截止时间 `order.refund_deadline`，以服务端判定为准。
- **Q3 共享备份残留期的对外措辞。** REQUIREMENTS 写「收货资料 30 天后匿名化」；DESIGN 1.6「保留与匿名化」写在线第 30 天删除，但副本最迟第 30 + max(binlog 残留期, N) 天才消失，且运营核实前没有确定上限。收货表单旁按验收要求只写「30 天后匿名化」；隐私页草拟 `privacy.retention_backup`，不写天数。是否对外披露及如何措辞需 Kelvin 决定；运营核实前不得写具体天数或「有限期」。
- **Q4 「30 天」的起算点。** DESIGN 区分已支付（支付起算）与未支付（创建起算）。收货表单短文案只写「30 天后」，完整规则放隐私页 `privacy.retention_recipient`。请确认是否接受。
- **Q5 会员能否在会员中心直接确认收货、申请退款。** REQUIREMENTS 写访客「在查询页确认收货」；DESIGN 允许会员访问自己认领或下单的订单。线框假设会员订单详情复用 P09 并提供同样操作，需确认。
- **Q6 模拟支付方式清单。** 需求只写「选择支付方式」。线框用「演示银行卡 / 演示网上银行 / 演示电子钱包」三项，均不输入任何资料；名称与数量待定。「银行卡」字样是否会让访客误以为要填卡号，也请一并判断。
- **Q7 后台导出。** DESIGN 1.6「权限与资料保护」提到「后台导出仍受服务端权限控制并留审计记录」，REQUIREMENTS 未列导出功能。线框不设导出按钮，待定。
- **Q8 商品文案回退英文时是否标示。** DESIGN 只规定回退英文。线框在回退时显示 `detail.english_only` 小标签，待定。
- **Q9 参考币种的显示范围。** REQUIREMENTS 写「按固定演示汇率显示访客国家货币参考金额」；DESIGN 1.6「边界与原则」规定按收货国家、不按 IP 决定。故线框只在结账页选定收货国家后显示参考金额，列表、详情、购物车只显示 MYR。请确认。
- **Q10 WhatsApp 联系方式未配置时的行为。** 线框在配置缺失时隐藏 WhatsApp 按钮（不显示占位文字）；也可显示「即将开放」。待定。
- **Q11 虚构电话被真实号码持有人认领。** 表单鼓励填写虚构资料；DESIGN 规定注册后按手机号自动认领近 30 天游客订单。若游客填的“虚构”号码恰好属于真人，该号码的持有人注册后即可认领该单并看到收货资料。这是设计层面的剩余风险，本稿不改设计，请 Kelvin 判断是否接受或另议。
- **Q12 待支付订单的取消入口与操作者。** DESIGN 写「待支付订单可取消或超时为 `demo_cancelled`」，未写由谁取消。线框在支付页放 `pay.cancel_order`（访客取消），待确认；若不允许访客取消则删除该按钮，仅靠 15 分钟超时。
- **Q13 会员中心「优惠券」的含义。** REQUIREMENTS 写会员可查看优惠券；DESIGN 的 `Coupon` 没有发放给某会员的字段，只有代码与使用记录。线框暂把「我的优惠券」做成使用记录，是否还要列出当前可用的公开券待定。
- **Q14 马来文文案审校。** UX-COPY 的马来文为草稿，须母语者审校用词（如 troli、daftar keluar、bayaran balik）后再实现。
- **Q15 密码规则与注销确认方式。** DESIGN 只要求密码安全哈希，未定长度或复杂度；账号注销的确认方式（再次输入密码或短信验证）也未定义。线框只放确认按钮，待定。

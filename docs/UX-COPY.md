# Acuven Shop 三语文案与演示提示（审阅稿）

> **审阅稿。0.7 已于 2026-10-05 经 Kelvin 认可（记录见 `docs/HANDOFF.md`）；0.8 只新增后台登录页过渡状态的文案键 `admin.logged_in`，其余各键与 0.7 相同，Kelvin 已于 2026-10-05 认可；0.9 只新增 `admin.reviewed_by`，其余各键与 0.8 相同，Kelvin 已于 2026-10-06 认可；0.10 只新增 `admin.email` 并停用 `admin.username`，Kelvin 已于 2026-10-07 批准（记录见 `docs/HANDOFF.md`）；0.11 只新增 A05 新建优惠券被拒时的 `admin.coupon_code_taken` 与 `admin.coupon_invalid`，Kelvin 已于 2026-10-12 认可（记录见 `docs/HANDOFF.md`）。0.12 只新增 Stripe 测试环境支付所需的文案键，其余各键与 0.11 相同，Kelvin 已于 2026-10-15 审阅通过（记录见 `docs/HANDOFF.md`）。0.13 只新增 `admin.stripe_refund_not_sent`，其余各键与 0.12 相同，Kelvin 已于 2026-10-23 审阅通过（记录见 `docs/HANDOFF.md`）。0.14 只新增 `pay.member_reopen`，其余各键与 0.13 相同，Kelvin 已于 2026-10-24 审阅通过（记录见 `docs/HANDOFF.md`）。0.15 只新增 `admin.sms_not_configured`，其余各键与 0.14 相同，Kelvin 已于 2026-10-27 审阅通过（记录见 `docs/HANDOFF.md`）。0.16 只新增 A06 增删与保存结果所需的文案键，其余各键与 0.15 相同，Kelvin 已于 2026-10-27 审阅通过（记录见 `docs/HANDOFF.md`）。0.17 只新增 `admin.shipping_my_west` 与 `admin.shipping_my_east` 并把 `admin.shipping_my_state` 改为区块标题，其余各键与 0.16 相同，Kelvin 已于 2026-10-28 审阅通过（记录见 `docs/HANDOFF.md`）。0.18 按 DESIGN 1.18 新增 A04、A07、A09、A10、整单上限与数量直接输入的文案键，并改 `admin.image_rules`、`admin.max_per_order_hint` 与 `admin.initial_stock_note`，其余各键与 0.17 相同，Kelvin 已于 2026-10-30 审阅通过（记录见 `docs/HANDOFF.md`）。0.19 只新增 A08 店铺标志图的 4 个键，Kelvin 已于 2026-11-05 批准；0.20 只改三个已有键的「已告警运营者」措辞，Kelvin 已于 2026-11-06 认可（记录见 `docs/HANDOFF.md`）。0.21 只新增 A11 后台 Stripe 待处理所需的 26 个文案键，Kelvin 已于 2026-11-08 审阅通过（记录见 `docs/HANDOFF.md`）。**
> 版本 0.22（2026-11-10），运营者修订（Claude Code），在 0.21（提交 `bb8adb7`）上按已批准的 `docs/DESIGN.md` 1.23「节日促销活动」与 `docs/REQUIREMENTS.md` 1.19（Kelvin 2026-11-10 批准，记录见 `docs/HANDOFF.md` 0.92），只新增「11. 节日促销活动」一节的文案键与第 10 节的导航项 `admin.nav_promotions`（排在 `admin.nav_coupons` 之后），其余各键与 0.21 相同；页面见 [UX.md](UX.md) 0.20。以下为 0.21 及更早版本的历史说明：版本 0.21（2026-11-08），运营者修订（Claude Code），在 0.20（提交 `5be346e`）上按已批准的 `docs/DESIGN.md` 1.22「Stripe 测试环境支付」第 11 条，只新增 A11 后台 Stripe 待处理所需的 26 个文案键，其余各键与 0.20 相同；页面见 [UX.md](UX.md) 0.19。以下为 0.20 及更早版本的历史说明：版本 0.20（2026-11-06），运营者修订（Claude Code），在 0.19（提交 `a870f78`）上按已批准的 `docs/DESIGN.md` 1.21「运营告警邮件」第 7 条（告警是记日志并尽力发信，不保证送达），只改 `admin.stock_reset_failed`、`admin.stripe_refund_not_sent` 与 `admin.stripe_refund_blocked` 三个已有键的三语文字：「已告警运营者」改为「已记录运营告警」，其余字句不变；页面结构不变（UX 0.18）。以下为 0.19 及更早版本的历史说明：版本 0.19（2026-11-05），运营者修订（Claude Code），在 0.18（最近一次改动为提交 `d2fe571`）上按 `docs/DESIGN.md` 1.20 候批稿「计价、优惠、积分与库存」第 15 条新增 A08 店铺标志图的 4 个键，页面结构见 [UX.md](UX.md) 0.18。以下为 0.18 及更早版本的历史说明：版本 0.18（2026-10-30），运营者修订（Claude Code），在 0.17（提交 `80e3775`）上按已批准的 `docs/DESIGN.md` 1.18 新增 A04、A07、A09、A10 与整单上限的文案键，并改动 `admin.image_rules`、`admin.max_per_order_hint` 与 `admin.initial_stock_note`，页面结构见 [UX.md](UX.md) 0.17。以下为 0.17 及更早版本的历史说明，其中的版本依据与交叉引用指当时的版本：版本 0.17（2026-10-28），运营者修订（Claude Code），在 0.16（提交 `e9a1c93`）上按 Kelvin 2026-10-28 的决定新增 A06 的 `admin.shipping_my_west` 与 `admin.shipping_my_east`，并把 `admin.shipping_my_state` 改为区块标题「Malaysia」，页面结构见 [UX.md](UX.md) 0.16。以下为 0.16 及更早版本的历史说明，其中的版本依据与交叉引用指当时的版本：版本 0.16（2026-10-27），运营者修订（Claude Code），在 0.15（提交 `1c668e6`）上按 Kelvin 2026-10-27 的决定新增 A06 的 `admin.remove`、`admin.add_fx`、`admin.fx_currency`、`admin.fx_decimals`、`admin.fx_rate_new`、`admin.rates_saved`、`admin.rates_invalid`、`admin.rates_changed` 与 `admin.reload`，页面结构见 [UX.md](UX.md) 0.15；版本 0.15（2026-10-27），运营者修订（Claude Code），在 0.14（提交 `450c2fa`）上按 Kelvin 2026-10-27 的决定新增 `admin.sms_not_configured`，页面结构见 [UX.md](UX.md) 0.14；版本 0.14（2026-10-23），运营者修订（Claude Code），在 0.13（提交 `5e69c3e`）上按 Kelvin 2026-10-23 的决定新增 `pay.member_reopen`，页面结构见 [UX.md](UX.md) 0.13；版本 0.13（2026-10-23），运营者修订（Claude Code），在 0.12（提交 `e922fcf`）上按 Kelvin 2026-10-23 的决定新增 `admin.stripe_refund_not_sent`，页面结构见 [UX.md](UX.md) 0.12；版本 0.12（2026-10-15），运营者修订（Claude Code），在 0.11（提交 `d943045`）上按已批准的 `docs/DESIGN.md` 1.12「Stripe 测试环境支付」与 `docs/REQUIREMENTS.md` 1.13（Kelvin 2026-10-15 批准，记录见 `docs/HANDOFF.md` 0.50）新增 Stripe 测试环境支付的文案键，页面结构见 [UX.md](UX.md) 0.11；版本 0.11（2026-10-12），运营者修订（Claude Code），在 0.10（提交 `eb2bdc4`）上新增 `admin.coupon_code_taken` 与 `admin.coupon_invalid`；0.10（2026-10-07）在 0.9（提交 `1a65f6b`）上新增 `admin.email`；0.9（2026-10-06）在 0.8（提交 `f333b19`）上新增 `admin.reviewed_by`；0.8（2026-10-05）在 0.7（提交 `61f7b8a`）上新增 `admin.logged_in`；0.7 在 0.6（提交 `2d13250`）上新增 `admin.logout`；0.6（2026-10-01）在 0.5（提交 `e3b3505`）上增补短信验证开关所需的文案键，并修改 `privacy.fictional`、`privacy.sms` 两键。页面结构与线框见 [UX.md](UX.md)（0.10）；依据 `docs/REQUIREMENTS.md` 1.12 候批稿与 `docs/DESIGN.md` 1.11 候批稿；「提示」列中依据 DESIGN 的小节除特别注明外仍指 1.9。Kelvin 2026-09-30 对待决问题 Q11、Q14、Q16、Q17、Q18、Q19 的决定见 `docs/HANDOFF.md`。

## 0.22 修订要点

- 依据已批准的 `docs/DESIGN.md` 1.23「节日促销活动」与 UX 0.20 的 P01–P05、P09、P15、A02、A08、A12：新增「11. 节日促销活动」一节，含前台的 `promo.*`（角标、倒计时、规则说明模板、专题页、预览条），购物车与结账新增的 `cart.*`、`checkout.*`，以及后台的 `admin.*`（导航、A12 列表与编辑区、规则、文案、装饰、海报、AI、前台预览的页签、A08 与 A02 的说明）。
- 后台导航项 `admin.nav_promotions` 放在第 10 节的导航项里、`admin.nav_coupons` 之后，与 UX 0.20「管理后台总体」的导航顺序一致（`frontend/src/components/AdminFrame.test.tsx` 按本表导航键的先后核对导航顺序）。
- 规则说明一律用 `promo.rule_*` 模板按规则参数生成，不显示 AI 或管理员写的规则描述（DESIGN 1.23 第 10 条）。活动标题、副标题、角标短语、横条文字与海报替代文字是管理员（或 AI 提案）写的活动文案，不在本表。
- 素材包名称 `admin.pack_*` 共 7 个，与 DESIGN 1.23 第 12 条的清单一致；节日配色以 `admin.palette_of` 套素材包名称显示，不另设键。装饰位变体的名称用 `admin.variant_n`。
- 沿用的键：`common.create`、`common.save`、`common.cancel`、`common.lang_en`/`zh`/`ms`、`list.results_count`、`list.load_more`、`list.price_from`、`common.price_myr`、`cart.checkout`、`cart.subtotal`、`admin.filter_status`、`admin.reload`、`admin.upload`、`admin.image_rules`、`admin.rules_apply_new`、`admin.unsaved_confirm`。
- 已有键未改动；前端尚未使用新键，`frontend/src/i18n/copy.ts` 不改。

## 0.21 修订要点

- 依据已批准的 `docs/DESIGN.md` 1.22「Stripe 测试环境支付」第 11 条与 UX 0.19 的 A11：新增 26 个键——导航与页标题 `admin.nav_stripe_pending`（排在 `admin.nav_refunds` 之后），★ `admin.stripe_pending_note`，两个列表的标题、说明、空状态与列头（`admin.stripe_pending_checkouts_title`、`admin.stripe_pending_refunds_title`、`admin.stripe_pending_refunds_note`、`admin.stripe_pending_none`、`admin.col_checkout_record`、`admin.col_refund_attempt`、`admin.col_created`、`admin.col_alerted`、`admin.col_attempted`、`admin.col_error_type`），记录状态 `admin.checkout_status_creating`、`admin.checkout_status_open`、`admin.checkout_status_complete`，作废的按钮、确认框与五种结果（`admin.stripe_void`、`admin.stripe_void_confirm_title`、`admin.stripe_void_confirm_body`、`admin.stripe_void_verified`、`admin.stripe_void_submit`、`admin.stripe_void_confirmed`、`admin.stripe_void_cancelled`、`admin.stripe_void_voided`、`admin.stripe_void_unavailable`、`admin.stripe_pending_changed`），重试成功 `admin.stripe_late_refund_retried`。
- 沿用已有的键：`pay.order_no`、`admin.filter_status`、`admin.col_amount`、`admin.stripe_refund_retry`、`admin.stripe_refund_blocked`、`admin.reload`、`common.cancel`、`common.error_retry`。
- 已有键未改动；前端尚未使用新键，`frontend/src/i18n/copy.ts` 不改。

## 0.20 修订要点

- 依据已批准的 `docs/DESIGN.md` 1.21「运营告警邮件」第 7 条：后台显示的「已告警运营者」指服务端已记下告警日志并尽力发信，不保证邮件送达（告警未配置、队列满或最终发送失败时只有日志），也不为配置之前的失败补发；原文案断言运营者已被告警，与之不符。
- 改 3 个已有键的三语文字，键名、出现位置与显示条件不变：`admin.stock_reset_failed`（A07）、`admin.stripe_refund_not_sent`、`admin.stripe_refund_blocked`（A03）。英文「The operator has been alerted」改为「An operator alert has been logged」，中文「已告警运营者」改为「已记录运营告警」，马来文「Pengendali telah dimaklumkan」改为「Amaran pengendali telah direkodkan」。
- 视觉稿 `docs/design/pages/` 的 A07-desktop、A07-phone 与 A03-stripe-states 里的英文同步改为新文字；`docs/design/reference/` 的参考截图是位图，不重新导出，与新文字不同之处以本文件为准。
- 三个键前端都已在用，`frontend/src/i18n/copy.test.ts` 要求字典与本文件逐字相同，所以同一提交里同步改 `frontend/src/i18n/copy.ts` 的这三条与 `frontend/src/pages/AdminStockResetsPage.test.tsx` 断言的英文原文，不改页面代码。其余已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.19 修订要点

- 依据 `docs/DESIGN.md` 1.20 候批稿「计价、优惠、积分与库存」第 15 条（Kelvin 2026-11-05 决定），页面结构见 [UX.md](UX.md) 0.18。
- 新增 A08 的 `admin.logo_rules`（标志的像素尺寸，与商品图片不同）、`admin.logo_immediate`、`admin.logo_invalid` 与 `admin.logo_remove_confirm`；上传中与其余被拒的提示沿用 A04 的 `admin.image_uploading`、`admin.image_type_unsupported`、`admin.image_too_large`。
- 其余已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.18 修订要点

- 依据已批准的 `docs/DESIGN.md` 1.18 与 `docs/REQUIREMENTS.md` 1.15（记录见 `docs/HANDOFF.md` 0.73），页面结构见 [UX.md](UX.md) 0.17。
- 新增 A04 的 40 个键（商品列表状态、新建、上架条件、图片、规格值与 SKU、保存结果、当日库存调整）、A10 后台分类的 19 个键、A07 的 `admin.stock_reset_off_note`、A09 每日恢复库存的 4 个键，以及整单上限的 `detail.cart_units_full`（P03）、`cart.units_over_limit` 与 `cart.amount_over_limit`（P04、P05）；另有 A04 与 A10 共用的未保存提示 `admin.unsaved_confirm`、`admin.unsaved_leave`、`admin.unsaved_stay`，A04 规格名与规格值的 `admin.option_name`、`admin.option_value`，与 P03、P04 数量直接输入超过上限时的 `common.qty_capped`（Kelvin 2026-10-30 决定）。
- 改动 3 个已有键，前端都还没有用到：`admin.image_rules` 去掉变量、只写格式与大小（A08 店铺标志沿用它；像素与张数另由 `admin.product_image_rules` 给出）；`admin.max_per_order_hint` 的 1–99 改为 1–1000；`admin.initial_stock_note` 的中文由「次日重置」改为「下一次每日重置」（开关关闭期间不是次日）。
- 其余已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.17 修订要点

- 新增 A06 的两个键（UX 0.16，DESIGN 1.17 候批稿「计价、优惠、积分与库存」第 10 条，Kelvin 2026-10-28 决定）：`admin.shipping_my_west` 与 `admin.shipping_my_east`；`admin.shipping_my_state` 由「Malaysia, by state」改为区块标题「Malaysia」。前端尚无这三个键，改文字不影响已交付页面。
- 除 `admin.shipping_my_state` 外，已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.16 修订要点

- 新增 A06 的九个键（UX 0.15，DESIGN 1.16 候批稿「计价、优惠、积分与库存」第 9 条，Kelvin 2026-10-27 决定）：`admin.remove`（国家运费与汇率每行的删除）、`admin.add_fx`、新增汇率行的 `admin.fx_currency`、`admin.fx_decimals`、`admin.fx_rate_new`，保存结果 `admin.rates_saved`、`admin.rates_invalid`、`admin.rates_changed` 与其旁的 `admin.reload`。`admin.rates_invalid` 的取值范围与 DESIGN 1.16 第 9 条一致，改范围须同时改这条文字。新增行的国家沿用 `admin.shipping_country`。
- 已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.15 修订要点

- 新增 `admin.sms_not_configured`（A09，UX 0.14，DESIGN 1.15，Kelvin 2026-10-27 决定）：服务端告知短信服务的配置不齐全时显示在 `admin.sms_on` 之下（该选项禁用），保存「开启」被拒时也显示，本次保存不生效。文字只说哪几类配置不齐全，不写配置项名称与取值；写法照 `admin.stripe_not_configured`。
- 已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.14 修订要点

- 新增 `pay.member_reopen`（P06、P07，UX 0.13，Kelvin 2026-10-23 决定）：会员打开支付页或结果页，页面内存与本标签页暂存里都没有订单号（例如在新标签页直接打开）、或订单不是本人的，而有会员会话时显示，旁边是去 P13 我的订单的 `account.orders`。没有会员会话时仍显示 `pay.session_expired`。文字不提「刷新」：支付结果确定之前，会员刷新或从 Stripe 返回时凭暂存的订单号照常显示，不会看到它；结果确定、暂存清除后在结果页刷新才会看到。
- 已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.13 修订要点

- 新增 `admin.stripe_refund_not_sent`（A03，UX 0.12，Kelvin 2026-10-23 决定）：经 Stripe 测试付款、申请已批准、现金退款额大于 0，却因同一付款的退款合计会超过付款金额、或会话记录缺 PaymentIntent ID 而没有建成 Stripe 退款尝试时显示（DESIGN 1.12「Stripe 测试环境支付」第 8 条，服务端已记告警）。页面分不出是哪一种原因，文字对两种都成立；不提示重试（这时不能重试）。
- 已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.12 修订要点

- 新增 Stripe 测试环境支付的文案键（DESIGN 1.12「Stripe 测试环境支付」，UX 0.11）：
  - P06：`pay.stripe_title`、`pay.stripe_demo_hint`（★）、`pay.stripe_notice`（◆）、`pay.stripe_test_card`、`pay.stripe_go`、`pay.stripe_redirecting`、`pay.stripe_in_progress`、`pay.stripe_under_review`、`pay.stripe_continue`、`pay.stripe_unavailable`、`pay.method_changed`、`pay.cancel_confirm_stripe`、`pay.cancel_try_later`。
  - P07：`result.confirming_title`、`result.confirming_body`、`result.confirming_slow`、`result.check_again`、`result.stripe_not_paid`、`result.stripe_back_to_pay`、`result.stripe_late_refund`、`result.paid_not_cancelled`。
  - P14：`privacy.stripe`。
  - A03：`admin.paid_via_stripe`、`admin.stripe_method`、`admin.stripe_method_unknown`、`admin.stripe_deadline_fpx`、`admin.stripe_deadline_grabpay`、`admin.stripe_deadline_other`、`admin.stripe_refund_hint`（◆）、`admin.stripe_refund_title`、`admin.col_stripe_refund_id`、`admin.stripe_refund_pending`、`admin.stripe_refund_succeeded`、`admin.stripe_refund_failed`、`admin.stripe_refund_pending_hint`、`admin.stripe_refund_failed_hint`、`admin.stripe_refund_retry`、`admin.stripe_refund_none`、`admin.stripe_refund_blocked`。
  - A09：`admin.payment_method`、`admin.payment_simulated`、`admin.payment_stripe`、`admin.payment_method_hint`、`admin.stripe_not_configured`。
- 本文件不写 Stripe 测试环境的固定演示邮箱或任何地址；测试卡号是 Stripe 公布的测试卡号，不是真实卡号。FPX、GrabPay 测试授权页的呈现与两种方式的退款期限以实现前的测试环境核验为准，不符时先改文案。
- 已有键未改动、未删除；`pay.demo_hint`、`pay.action_hint` 等模拟支付文案只在支付途径为模拟时显示。马来文随其他文案在上线后母语审校。

## 0.11 修订要点

- 新增 `admin.coupon_code_taken` 与 `admin.coupon_invalid`（A05 新建优惠券被服务端拒绝时显示：代码已被使用；代码、面额、最低消费、次数上限或日期不合格。Kelvin 2026-10-12 决定补这两条，其余失败仍用 `common.error_retry`）。其余已有键未改动、未删除。马来文随其他文案在上线后母语审校。

## 0.10 修订要点

- 新增 `admin.email`（A01 登录名输入框的标签，管理员以邮箱登录，Kelvin 2026-10-07 决定）。`admin.username` 停用（提示列注明），三语文字未改；其余已有键未改动、未删除。

## 0.9 修订要点

- 新增 `admin.reviewed_by`（A03 已批准或已拒绝的申请显示审核人与审核时间，Kelvin 2026-10-06 认可）。其余已有键未改动、未删除。

## 0.8 修订要点

- 新增 `admin.logged_in`（Kelvin 2026-10-05 决定的过渡规则：A02 订单页上线前，A01 登录成功后留在本页显示它与 `admin.logout`；A02 上线后随过渡规则一并取消）。其余已有键未改动、未删除。

## 0.7 修订要点

- 新增 `admin.logout`（后台除 A01 外每页右上角的退出按钮，Kelvin 2026-10-05 决定）。其余已有键未改动、未删除。

## 0.6 修订要点

- 新增短信验证开关的文案键（Kelvin 2026-10-01 决定）：`checkout.phone_notice_sms_off`、`checkout.guest_sms_off`（P05），`auth.sms_paused`、`auth.login_failed_sms_off`（V1、P11、P12），`account.sms_paused_set_password`、`account.delete_verify_password`、`account.delete_password_wrong`、`account.delete_verify_session`、`account.delete_session_confirm`、`account.delete_keep`（P13），`privacy.sms_toggle`（P14），`admin.nav_settings`、`admin.sms_toggle`、`admin.sms_on`、`admin.sms_off`、`admin.sms_toggle_hint`、`admin.sms_toggle_cost`、`admin.settings_saved`（A09）。
- 修改 `privacy.fictional`、`privacy.sms`：限定为「短信验证开启时」，三种语言同步改。其余已有键未改动、未删除。

## 0.5 修订要点

- 新增每单限购的文案键：`detail.max_per_order`、`detail.limit_reached`、`detail.cart_full`（P03），`cart.over_limit`（P04），`admin.max_per_order`、`admin.max_per_order_hint`（A04）（Kelvin 2026-10-01 决定）。
- 新增精选商品挑选的文案键：`admin.featured_pick`、`admin.featured_add`、`admin.featured_remove`、`admin.featured_hint`（A08）（Kelvin 2026-10-01 决定）。
- 未改动、未删除任何已有键的文案。

## 0.4 修订要点

- 新增 `detail.a11y_image`（P03 缩略图与手机主图的读屏标签）。
- 新增后台 A08 店铺装修的文案键：`admin.nav_store_design`、`admin.design_demo_note`、`admin.theme`、10 款主题名 `admin.theme_*`、`admin.theme_dark_note`、`admin.preview`、`admin.preview_light`、`admin.preview_dark`、`admin.accent`、`admin.accent_option`、`admin.accent_hint`、`admin.logo`、`admin.logo_hint`、`admin.logo_remove`、`admin.home_blocks`、`admin.block_hero`、`admin.block_show`、`admin.block_move_up`、`admin.block_move_down`、`admin.home_blocks_hint`、`admin.design_saved`。首页其余三个区块在 A08 中沿用 `home.how_title`、`home.categories`、`home.featured` 作名称。
- `list.price_from` 提示列写明：商品有多于一个启用规格时一律使用，即使各规格同价（Kelvin 2026-09-30 决定）。
- 未改动、未删除任何已有键的文案。

## 0.3 修订要点

- 依据改为 `docs/REQUIREMENTS.md` 1.8 与 `docs/DESIGN.md` 1.9；「提示」列中依据 DESIGN 的小节自 0.3 起一律指 `docs/DESIGN.md` 1.9（小节标题不变）。
- 新增 `auth.sms_not_sent_no_change`（V1 用于重设密码与注销确认时，短信未发出或号码不在白名单：账号未作任何更改、请稍后再试，不提游客）与 `auth.reset_not_registered`（忘记密码遇到未注册号码：不创建账号，提供去注册的链接）。`auth.not_supported_country`、`auth.sms_failed` 只用于注册与短信登录，措辞不变。
- 改写 `privacy.member_backup`（Q18 已决：对外披露，用「密码哈希」，不写天数、不暗示一定清除，已定稿）、`privacy.browser_access`（30 分钟后或在其他浏览器须重新查单，不表示只能在本浏览器打开）、`admin.coupon_listed_note`（Q19 已决：对全部会员相同，不按使用上限过滤）。
- 前台不再用「现金 / cash / tunai」指不含积分抵扣的实付金额：改写 `order.cash_paid`、`refund.submit_hint`；`account.points_pending` 中文改写；后台 `admin.refund_amount` 保留；键名均不变。
- 改写 `home.how_2`，不再暗示马新号码可选择游客结账。
- 「提示」列中 Q16–Q19 的标注改为已决；Q14 改为上线后补审校（见「约定」）。未删除任何键。

## 0.2 修订要点

- 新增结账手机号步骤、短信验证组件 V1、游客短期凭据与查单授权、支付页取消、两种登录方式、首次设置密码、短信确认注销、「我的优惠券」可用券等文案键；改写涉及保留期限的文案，**删除全部「30 天后匿名化」措辞**（`checkout.form_notice`、`lookup.not_found`、`auth.claim_notice`、`privacy.*`），删除 `order.recipient_anonymised`、`privacy.retention_backup`、`auth.register_submit`。
- `pay.method_card` 改为「演示信用卡/借记卡（无需输入卡号）」；`detail.english_only` 改为「仅英文」。
- 「提示」列中依据 DESIGN 的小节一律指当时的现行批准版（0.3 起改指 `docs/DESIGN.md` 1.9，见上）。

## 约定

- **默认语言：英文（English）。** 访客可在页头切换为中文或马来文（Bahasa Melayu），选择只保存在本浏览器；不按 IP 或浏览器语言自动切换。
- 每条文案有英文、中文、马来文三列，均不留空。`{…}` 为运行时替换的变量，三种语言保留同名变量。
- UX.md 线框中向用户显示的界面文字（前台与后台，含按钮、列头、区块标题、状态值与读屏标签）都以本表的键引用；线框里不在 `[ ]` 内的中文只是给审阅者的标注，不向用户显示。线框中的 `[order.status_*]`、`[account.points_type_*]`、`[account.coupon_value_*]`、`[admin.actor_*]`、`[admin.nav_*]`、`[common.lang_*]` 指本表中该前缀下的一组键；`[order.refund_requested|approved|rejected]` 指三选一。
- 商品名称、分类、规格等商品数据的三语文案由管理员在后台维护，不在本表；缺少当前语言时回退英文，并显示「仅英文」标签 `detail.english_only`（DESIGN 1.9「数据模型」；Q8 已决）。
- 金额一律以 MYR 显示为 `RM {amount}`，`{amount}` 由整数仙格式化为两位小数；积分一律称「积分 / points / mata」，不与金额混称（DESIGN 1.9「边界与原则」）。
- 联系入口只有 WhatsApp，链接由私有配置 `{{WHATSAPP_CONTACT_LINK}}` 提供，未配置时隐藏按钮（Q10 已决）；本文件与仓库不写任何真实电话、WhatsApp 号码、邮箱或地址。
- 「演示提示」列标 ★ 的是每页的演示提示；标 ◆ 的是下单、模拟支付、退款三处操作旁的专门演示提示。
- 收货资料长期保存（DESIGN 1.9「资料保留」）：任何文案都不得写「30 天后删除/匿名化」，也不得提供或暗示访客删除收货资料的入口。
- 马来文为草稿，先上线，上线后再由马来文母语者审校；审校不是页面实现的前置条件（Kelvin 2026-09-30 决定，见「待决问题」Q14）。

## 1. 全局

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `common.demo_banner` | Demo store — no real payments, shipping or refunds. | 演示网店——不会真实收款、发货或退款。 | Kedai demo — tiada bayaran, penghantaran atau bayaran balik sebenar. | ★ 全站常驻 |
| `common.demo_banner_short` | Demo only — no real transactions | 仅供演示——无真实交易 | Demo sahaja — tiada transaksi sebenar | ★ 手机常驻 |
| `common.demo_badge` | DEMO | 演示 | DEMO | — |
| `common.lang_en` | English | English | English | — |
| `common.lang_zh` | 中文 | 中文 | 中文 | — |
| `common.lang_ms` | Bahasa Melayu | Bahasa Melayu | Bahasa Melayu | — |
| `common.nav_home` | Home | 首页 | Laman utama | — |
| `common.nav_shop` | Shop | 商品 | Kedai | — |
| `common.nav_cart` | Cart ({count}) | 购物车（{count}） | Troli ({count}) | — |
| `common.nav_track` | Track order | 查询订单 | Semak pesanan | 也用作凭据/授权过期后去 P08 的按钮 |
| `common.nav_login` | Log in | 登录 | Log masuk | — |
| `common.nav_register` | Register | 注册 | Daftar | — |
| `common.nav_account` | My account | 会员中心 | Akaun saya | — |
| `common.nav_logout` | Log out | 退出登录 | Log keluar | — |
| `common.nav_privacy` | Privacy | 隐私说明 | Privasi | — |
| `common.nav_menu` | Menu | 菜单 | Menu | — |
| `common.whatsapp_cta` | Interested in a store like this? Chat with Acuven on WhatsApp | 想要这样的网店？通过 WhatsApp 联系 Acuven | Berminat dengan kedai seperti ini? Hubungi Acuven melalui WhatsApp | 链接为占位 `{{WHATSAPP_CONTACT_LINK}}`；未配置时隐藏 |
| `common.footer_demo` | Acuven demo store for showcasing online store solutions. All products, prices, stock, payments, shipping and refunds are simulated. | Acuven 网店方案演示站。所有商品、价格、库存、支付、运费、发货与退款均为模拟。 | Kedai demo Acuven untuk mempamerkan penyelesaian kedai dalam talian. Semua produk, harga, stok, bayaran, penghantaran dan bayaran balik adalah simulasi. | ★ 页脚 |
| `common.price_myr` | RM {amount} | RM {amount} | RM {amount} | — |
| `common.fx_reference` | ≈ {currency} {amount} (demo rate, for reference only) | ≈ {currency} {amount}（演示汇率，仅供参考） | ≈ {currency} {amount} (kadar demo, untuk rujukan sahaja) | 只在结账页选定收货国家后显示（Q9 已决） |
| `common.points_unit` | {points} points | {points} 积分 | {points} mata | — |
| `common.error_retry` | Something went wrong. Please try again. | 出错了，请重试。 | Berlaku ralat. Sila cuba lagi. | — |
| `common.network_check` | Connection lost. Checking whether your last action went through… | 网络中断，正在确认上一步是否已完成… | Sambungan terputus. Menyemak sama ada tindakan terakhir anda berjaya… | — |
| `common.rate_limited` | Too many attempts. Please wait and try again later. | 尝试次数过多，请稍后再试。 | Terlalu banyak cubaan. Sila tunggu dan cuba lagi kemudian. | — |
| `common.service_unavailable` | This feature is temporarily unavailable. You can keep browsing products. | 此功能暂不可用，您仍可继续浏览商品。 | Ciri ini tidak tersedia buat sementara. Anda masih boleh melayari produk. | — |
| `common.copy` | Copy | 复制 | Salin | — |
| `common.copied` | Copied | 已复制 | Disalin | — |
| `common.back` | Back | 返回 | Kembali | — |
| `common.cancel` | Cancel | 取消 | Batal | — |
| `common.save` | Save | 保存 | Simpan | — |
| `common.search` | Search | 搜索 | Cari | 页头搜索按钮 |
| `common.create` | New | 新建 | Baharu | 后台 |
| `common.edit` | Edit | 编辑 | Sunting | 后台 |
| `common.a11y_qty_decrease` | Decrease quantity | 减少数量 | Kurangkan kuantiti | 读屏标签，`(-)` |
| `common.a11y_qty_increase` | Increase quantity | 增加数量 | Tambah kuantiti | 读屏标签，`(+)` |
| `common.qty_capped` | Set to {count}, the most you can have in this order. | 已改为 {count} 件，这是本单最多可买的件数。 | Ditetapkan kepada {count}, jumlah maksimum untuk pesanan ini. | P03、P04 直接输入的件数超过上限时；{count} 为上限（0.18） |
| `common.a11y_page_prev` | Previous page | 上一页 | Halaman sebelumnya | 读屏标签，`(‹)` |
| `common.a11y_page_next` | Next page | 下一页 | Halaman seterusnya | 读屏标签，`(›)` |

## 2. 首页（P01）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `home.hero_title` | Try a complete online store, end to end | 完整体验一家网店的购物流程 | Cuba kedai dalam talian yang lengkap, dari mula hingga akhir | — |
| `home.hero_body` | Browse, add to cart, check out and pay with a simulated payment. Everything here is a demo. | 浏览、加入购物车、结账并用模拟支付付款。这里的一切都是演示。 | Layari, tambah ke troli, daftar keluar dan bayar dengan pembayaran simulasi. Semua di sini adalah demo. | — |
| `home.hero_cta` | Start shopping | 开始逛逛 | Mula membeli-belah | — |
| `home.how_title` | How this demo works | 演示怎么玩 | Cara demo ini berfungsi | — |
| `home.how_1` | Pick products and options | 挑选商品和规格 | Pilih produk dan pilihan | — |
| `home.how_2` | Check out: Malaysian and Singapore mobile numbers are verified by SMS and continue as members; other numbers check out as guests | 结账：马来西亚、新加坡手机号经短信验证后以会员身份继续，其他号码以游客身份结账 | Daftar keluar: nombor telefon bimbit Malaysia dan Singapura disahkan melalui SMS dan diteruskan sebagai ahli; nombor lain mendaftar keluar sebagai tetamu | 不暗示马新号码可选择游客结账 |
| `home.how_3` | Choose "success" or "failure" on the simulated payment | 在模拟支付中选择“成功”或“失败” | Pilih "berjaya" atau "gagal" pada pembayaran simulasi | — |
| `home.how_4` | Track the order, confirm receipt or request a refund | 查询订单、确认收货或申请退款 | Semak pesanan, sahkan penerimaan atau mohon bayaran balik | — |
| `home.categories` | Shop by category | 按分类浏览 | Beli mengikut kategori | — |
| `home.featured` | Featured demo products | 精选示例商品 | Produk demo pilihan | — |
| `home.demo_hint` | Products, prices and stock are samples. Stock resets every day. | 商品、价格与库存均为示例，库存每天重置。 | Produk, harga dan stok adalah contoh. Stok ditetapkan semula setiap hari. | ★ |

## 3. 商品列表与详情（P02、P03）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `list.title` | All products | 全部商品 | Semua produk | — |
| `list.search_placeholder` | Search products | 搜索商品 | Cari produk | — |
| `list.filter_title` | Filter | 筛选 | Tapis | — |
| `list.filter_category` | Category | 分类 | Kategori | — |
| `list.filter_apply` | Show results | 查看结果 | Tunjuk hasil | — |
| `list.filter_clear` | Clear filters | 清除筛选 | Kosongkan penapis | — |
| `list.sort` | Sort | 排序 | Susun | — |
| `list.sort_newest` | Newest | 最新 | Terbaru | — |
| `list.sort_price_asc` | Price: low to high | 价格从低到高 | Harga: rendah ke tinggi | — |
| `list.sort_price_desc` | Price: high to low | 价格从高到低 | Harga: tinggi ke rendah | — |
| `list.results_count` | {count} products | 共 {count} 件商品 | {count} produk | — |
| `list.price_from` | From RM {amount} | RM {amount} 起 | Dari RM {amount} | 商品有多于一个启用规格时一律使用，即使各规格同价（0.4，Kelvin 2026-09-30 决定）；只有一个规格时用 `common.price_myr` |
| `list.out_of_stock` | Out of stock today | 今日已售罄 | Kehabisan stok hari ini | — |
| `list.load_more` | Load more | 加载更多 | Muat lagi | 手机 |
| `list.empty` | No products match your search. | 没有符合条件的商品。 | Tiada produk sepadan dengan carian anda. | — |
| `list.demo_hint` | Sample products for demonstration only; none are for real sale. | 示例商品仅供演示，均不真实出售。 | Produk contoh untuk demo sahaja; tiada yang dijual secara sebenar. | ★ |
| `detail.options` | Choose options | 选择规格 | Pilih pilihan | — |
| `detail.quantity` | Quantity | 数量 | Kuantiti | — |
| `detail.stock_left` | {count} left today (demo stock) | 今日剩余 {count} 件（示例库存） | Tinggal {count} hari ini (stok demo) | — |
| `detail.add_to_cart` | Add to cart | 加入购物车 | Tambah ke troli | — |
| `detail.added` | Added to cart | 已加入购物车 | Ditambah ke troli | — |
| `detail.view_cart` | View cart | 查看购物车 | Lihat troli | — |
| `detail.select_all_options` | Please choose all options first. | 请先选择全部规格。 | Sila pilih semua pilihan dahulu. | — |
| `detail.max_per_order` | Limit {count} per order | 每单限购 {count} 件 | Had {count} unit bagi setiap pesanan | P03 数量旁；同一商品各规格合计（0.5） |
| `detail.limit_reached` | You already have the maximum of {count} of this product in your cart. | 购物车中此商品已达每单限购 {count} 件。 | Troli anda sudah mempunyai jumlah maksimum {count} untuk produk ini. | P03 加入按钮禁用时（0.5） |
| `detail.cart_full` | Your cart can hold up to {count} different items. Remove one to add another. | 购物车最多可放 {count} 种商品规格，请先移除一项再加入。 | Troli anda boleh memuatkan sehingga {count} item berbeza. Buang satu untuk menambah yang lain. | P03 加入按钮禁用时；{count} 为 20（0.5） |
| `detail.cart_units_full` | Your cart can hold up to {count} items in total. | 购物车所有商品合计最多 {count} 件。 | Troli anda boleh memuatkan sehingga {count} unit secara keseluruhan. | P03 加入按钮禁用时；{count} 为 2,000（0.18） |
| `detail.description` | Description | 商品描述 | Penerangan | — |
| `detail.english_only` | English only | 仅英文 | Bahasa Inggeris sahaja | 商品文案回退英文时的标签（Q8 已决） |
| `detail.a11y_image` | Image {n} of {count} | 第 {n} 张图，共 {count} 张 | Imej {n} daripada {count} | 读屏标签：P03 缩略图与手机主图（0.4） |
| `detail.demo_hint` | This is a sample product. Adding it to your cart does not reserve stock. | 这是示例商品；加入购物车不会预留库存。 | Ini produk contoh. Menambahnya ke troli tidak menempah stok. | ★ |

## 4. 购物车（P04）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `cart.title` | Your cart | 购物车 | Troli anda | — |
| `cart.empty` | Your cart is empty. | 购物车是空的。 | Troli anda kosong. | — |
| `cart.remove` | Remove | 移除 | Buang | — |
| `cart.subtotal` | Item subtotal | 商品小计 | Jumlah kecil item | — |
| `cart.shipping_later` | Shipping is calculated at checkout. | 运费在结账时计算。 | Kos penghantaran dikira semasa daftar keluar. | — |
| `cart.price_recheck` | Prices and stock are confirmed at checkout. | 价格与库存以结账时为准。 | Harga dan stok disahkan semasa daftar keluar. | — |
| `cart.item_changed` | Some items changed in price or availability. Please review. | 部分商品的价格或库存已变化，请核对。 | Sesetengah item telah berubah harga atau ketersediaan. Sila semak. | — |
| `cart.over_limit` | This product is limited to {count} per order. Please reduce the quantity. | 此商品每单限购 {count} 件，请减少数量。 | Produk ini dihadkan kepada {count} unit bagi setiap pesanan. Sila kurangkan kuantiti. | P04 超出限购的行下（0.5） |
| `cart.units_over_limit` | Your cart has more than {count} items in total. Please reduce the quantities. | 购物车商品合计超过 {count} 件，请减少数量。 | Troli anda mempunyai lebih daripada {count} unit secara keseluruhan. Sila kurangkan kuantiti. | P04、P05；{count} 为接口返回的上限件数（0.18） |
| `cart.amount_over_limit` | One order can't exceed RM {amount} (items plus shipping, before coupons and points). Please reduce the quantities. | 一张订单（商品加运费，优惠券与积分抵扣之前）不能超过 RM {amount}，请减少数量。 | Satu pesanan tidak boleh melebihi RM {amount} (item serta penghantaran, sebelum kupon dan mata). Sila kurangkan kuantiti. | P04、P05；{amount} 为接口返回的上限金额（0.18） |
| `cart.checkout` | Check out | 去结账 | Daftar keluar | — |
| `cart.continue` | Continue shopping | 继续购物 | Teruskan membeli-belah | — |
| `cart.demo_hint` | Your cart is saved in this browser only. Nothing is charged. | 购物车只保存在本浏览器，不会扣款。 | Troli anda disimpan dalam pelayar ini sahaja. Tiada caj dikenakan. | ★ |

## 5. 结账（P05）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `checkout.title` | Checkout | 结账 | Daftar keluar | — |
| `checkout.phone_step_title` | Your mobile number | 您的手机号 | Nombor telefon bimbit anda | 第 1 步标题 |
| `checkout.phone_notice` | Malaysian (+60) and Singapore (+65) numbers will receive a verification SMS and be registered as a member automatically (or logged in if already registered). Your mobile number is kept until you delete your account, which you can do anytime in My account. Other numbers check out as a guest without SMS. | 马来西亚（+60）和新加坡（+65）号码会收到验证短信，并自动注册为会员（已注册则直接登录）。手机号保留至您注销账号，您可随时在会员中心注销。其他号码不发短信，以游客身份结账。 | Nombor Malaysia (+60) dan Singapura (+65) akan menerima SMS pengesahan dan didaftarkan sebagai ahli secara automatik (atau dilog masuk jika sudah berdaftar). Nombor telefon bimbit anda disimpan sehingga anda memadam akaun, yang boleh dibuat pada bila-bila masa di Akaun saya. Nombor lain mendaftar keluar sebagai tetamu tanpa SMS. | 手机号旁常显；无勾选框；DESIGN「权限与资料保护」「资料保留」 |
| `checkout.phone_step_hint` | Choose the country code, or start with + to type it yourself. | 请选择国家码，或以 + 开头自行输入。 | Pilih kod negara, atau mulakan dengan + untuk menaipnya sendiri. | 国家码下拉列出所有国家、默认 +60；之后选的收货国家不改变已判定的号码（Q16 已决，DESIGN「权限与资料保护」） |
| `checkout.phone_continue` | Continue | 继续 | Teruskan | — |
| `checkout.phone_change` | Change | 更改 | Tukar | 回到第 1 步 |
| `checkout.login_password` | Already set a password? Log in | 已设置密码？直接登录 | Sudah menetapkan kata laluan? Log masuk | 链到 P12 |
| `checkout.guest_other_country` | This number is outside Malaysia and Singapore, so no SMS is sent and you'll check out as a guest. | 此号码不属于马来西亚或新加坡，不会发送短信，将以游客身份结账。 | Nombor ini di luar Malaysia dan Singapura, jadi tiada SMS dihantar dan anda akan mendaftar keluar sebagai tetamu. | 白名单外号码 |
| `checkout.phone_notice_sms_off` | SMS verification is currently switched off, so if you continue without logging in, you'll check out as a guest whatever your number, and no SMS is sent. Your number becomes this order's phone number and, with the order number, lets you track the order. | 短信验证目前已关闭，不登录继续结账时，无论哪个号码都将以游客身份结账，不会发送短信。您的号码即本订单的电话，与订单号一起用于查询订单。 | Pengesahan SMS kini dimatikan, jadi jika anda meneruskan tanpa log masuk, anda akan mendaftar keluar sebagai tetamu tanpa mengira nombor anda, dan tiada SMS dihantar. Nombor anda menjadi nombor telefon pesanan ini dan, bersama nombor pesanan, membolehkan anda menjejak pesanan. | 短信验证关闭时代替 `checkout.phone_notice` 常显；无勾选框（0.6） |
| `checkout.guest_sms_off` | SMS verification is switched off, so you'll check out as a guest. | 短信验证已关闭，将以游客身份结账。 | Pengesahan SMS dimatikan, jadi anda akan mendaftar keluar sebagai tetamu. | 短信验证关闭时，任何号码继续后显示（0.6） |
| `checkout.sms_unavailable` | We can't send an SMS to this number right now, so you can't be registered. You can place this demo order as a guest instead. | 目前无法向此号码发送短信，因此暂时无法注册。您可以改为以游客身份提交此演示订单。 | Kami tidak dapat menghantar SMS ke nombor ini sekarang, jadi anda tidak boleh didaftarkan. Anda boleh membuat pesanan demo ini sebagai tetamu. | 仅在服务端判定短信无法送达或停发时显示；验证码错误不显示 |
| `checkout.continue_guest` | Continue as a guest | 以游客身份继续 | Teruskan sebagai tetamu | 降级按钮 |
| `checkout.verified_member` | Verified. You're logged in as {phoneMasked}. | 验证成功，您已以 {phoneMasked} 登录。 | Disahkan. Anda telah log masuk sebagai {phoneMasked}. | 号码已注册 |
| `checkout.verified_new` | Verified. We've created a member account for {phoneMasked} and logged you in. You can set a password later in My account. | 验证成功，已为 {phoneMasked} 创建会员账号并登录。您可稍后在会员中心设置密码。 | Disahkan. Kami telah mencipta akaun ahli untuk {phoneMasked} dan melog masuk anda. Anda boleh menetapkan kata laluan kemudian di Akaun saya. | 新建账号 |
| `checkout.guest_notice` | You are checking out as a guest. Coupons and points are for members, who register with a Malaysian or Singapore mobile number. | 您正以游客身份结账。优惠券与积分仅限会员使用；会员须以马来西亚或新加坡手机号注册。 | Anda mendaftar keluar sebagai tetamu. Kupon dan mata untuk ahli, yang mendaftar dengan nombor telefon bimbit Malaysia atau Singapura. | — |
| `checkout.recipient_title` | Shipping details | 收货资料 | Butiran penghantaran | 也用于 P06、P07、P09 |
| `checkout.name` | Recipient name | 收货人姓名 | Nama penerima | — |
| `checkout.phone` | Phone number | 电话 | Nombor telefon | — |
| `checkout.phone_hint` | The country above sets the default country code. Start with + to use a different one. | 默认按上方国家补全区号；以 + 开头可输入其他国家码。 | Kod negara lalai mengikut negara di atas. Mulakan dengan + untuk kod lain. | 会员收货电话 |
| `checkout.member_phone_hint` | Defaults to your member mobile number. You can change it to another number, including a fictional one. | 默认为您的会员手机号，可改为其他号码或虚构号码。 | Lalai kepada nombor telefon bimbit ahli anda. Anda boleh menukarnya kepada nombor lain, termasuk nombor rekaan. | 仅会员 |
| `checkout.phone_invalid` | This phone number doesn't look valid. Please check the country code and number. | 该电话号码格式不符，请检查国家码和号码。 | Nombor telefon ini tidak kelihatan sah. Sila semak kod negara dan nombor. | — |
| `checkout.phone_lookup_hint` | You'll need this phone number and your order number to track the order. | 查询订单需要此电话号码和订单号。 | Anda perlukan nombor telefon ini dan nombor pesanan untuk menyemak pesanan. | 仅游客 |
| `checkout.country` | Country | 国家/地区 | Negara | — |
| `checkout.state_my` | State | 州属 | Negeri | 仅马来西亚 |
| `checkout.region` | State / province / region | 州/省/地区 | Negeri / wilayah | 其他国家 |
| `checkout.address` | Address | 地址 | Alamat | — |
| `checkout.postcode` | Postcode | 邮编 | Poskod | — |
| `checkout.form_notice` | Demo only: you will not be charged and nothing will be shipped. You may use fictional shipping details. Shipping details are used only for this demo and are kept long-term with the order. | 仅为演示：不会真实扣款，也不会真实发货。收货资料可填写虚构内容，仅用于本次演示，并会随订单长期保存。 | Demo sahaja: anda tidak akan dicaj dan tiada barang akan dihantar. Anda boleh guna butiran penghantaran rekaan. Butiran penghantaran digunakan untuk demo ini sahaja dan disimpan untuk jangka panjang bersama pesanan. | 收货表单旁常显；无勾选框；DESIGN「资料保留」 |
| `checkout.form_notice_link` | How we handle your details | 我们如何处理您的资料 | Cara kami mengendalikan butiran anda | 链到 P14 |
| `checkout.coupon` | Coupon code | 优惠券代码 | Kod kupon | — |
| `checkout.coupon_apply` | Apply | 使用 | Guna | — |
| `checkout.coupon_members_only` | Coupons are for members only. | 优惠券仅限会员使用。 | Kupon untuk ahli sahaja. | — |
| `checkout.coupon_invalid` | This coupon can't be used for this order. | 此优惠券不适用于本订单。 | Kupon ini tidak boleh digunakan untuk pesanan ini. | — |
| `checkout.points` | Use points | 使用积分 | Guna mata | — |
| `checkout.points_available` | {points} points available (100 points = RM1) | 可用 {points} 积分（100 积分抵 RM1） | {points} mata tersedia (100 mata = RM1) | — |
| `checkout.points_guest` | Guests don't earn points. Members with a Malaysian or Singapore mobile number earn 1 point for every RM1 paid. | 游客不累积积分。以马来西亚或新加坡手机号注册的会员每实付 RM1 得 1 积分。 | Tetamu tidak mengumpul mata. Ahli dengan nombor telefon bimbit Malaysia atau Singapura mendapat 1 mata bagi setiap RM1 dibayar. | — |
| `checkout.points_not_shipping` | Coupons and points reduce the item amount only, not shipping. | 优惠券与积分只抵商品金额，不抵运费。 | Kupon dan mata hanya mengurangkan jumlah item, bukan kos penghantaran. | — |
| `checkout.summary_title` | Order summary | 订单摘要 | Ringkasan pesanan | — |
| `checkout.summary_coupon` | Coupon discount | 优惠券抵扣 | Diskaun kupon | — |
| `checkout.summary_points` | Points discount | 积分抵扣 | Diskaun mata | — |
| `checkout.summary_shipping` | Sample shipping | 示例运费 | Kos penghantaran contoh | — |
| `checkout.summary_total` | Total (MYR) | 合计（MYR） | Jumlah (MYR) | — |
| `checkout.fx_note` | Reference amounts use a fixed demo rate and are never charged. | 参考金额按固定演示汇率换算，不会收取。 | Jumlah rujukan menggunakan kadar demo tetap dan tidak pernah dicaj. | 选定收货国家后显示 |
| `checkout.fx_none` | No reference currency for this country; amounts are shown in MYR only. | 该国家暂无参考币种，仅显示 MYR。 | Tiada mata wang rujukan untuk negara ini; jumlah dipaparkan dalam MYR sahaja. | 选定收货国家后显示 |
| `checkout.place_order` | Place demo order | 提交演示订单 | Buat pesanan demo | — |
| `checkout.place_order_hint` | Placing the order holds stock for 15 minutes while you complete the simulated payment. No money is taken. | 提交后为您保留库存 15 分钟以完成模拟支付，不会扣任何钱。 | Membuat pesanan menahan stok selama 15 minit sementara anda melengkapkan pembayaran simulasi. Tiada wang diambil. | ◆ 下单 |
| `checkout.submitting` | Placing your order… | 正在提交订单… | Sedang membuat pesanan… | — |
| `checkout.demo_hint` | Everything on this page is a demo. Shipping fees are samples set per country. | 本页均为演示；运费为按国家设定的示例。 | Semua di halaman ini adalah demo. Kos penghantaran adalah contoh mengikut negara. | ★ |

## 6. 模拟支付与结果（P06、P07）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `pay.title` | Simulated payment | 模拟支付 | Pembayaran simulasi | — |
| `pay.order_no` | Order number | 订单号 | Nombor pesanan | — |
| `pay.save_order_no` | Save this order number. You'll need it with your phone number to track the order. | 请保存订单号，查询订单时需要它和您的电话号码。 | Simpan nombor pesanan ini. Anda perlukannya bersama nombor telefon untuk menyemak pesanan. | — |
| `pay.amount_due` | Amount due (demo) | 应付金额（演示） | Jumlah perlu dibayar (demo) | — |
| `pay.guest_access` | For your privacy, only this browser can open this order's payment and result pages, for 30 minutes after the order was placed. | 为保护您的资料，只有本浏览器能在下单后 30 分钟内打开此订单的支付与结果页。 | Demi privasi anda, hanya pelayar ini boleh membuka halaman pembayaran dan keputusan pesanan ini, selama 30 minit selepas pesanan dibuat. | 游客短期凭据；DESIGN「权限与资料保护」 |
| `pay.member_reopen` | This page can't show the order right now. Open it from My orders to continue payment or view it. | 本页暂时无法显示这笔订单。请到我的订单重新打开，继续支付或查看。 | Halaman ini tidak dapat memaparkan pesanan sekarang. Bukanya daripada Pesanan saya untuk meneruskan pembayaran atau melihatnya. | P06、P07 会员找不到这笔订单时（0.14） |
| `pay.session_expired` | This page is no longer available in this browser. To view the order, confirm receipt or request a refund, track it with your order number and phone number. | 本浏览器已无法打开此页面。如需查看订单、确认收货或申请退款，请凭订单号和电话查询订单。 | Halaman ini tidak lagi tersedia dalam pelayar ini. Untuk melihat pesanan, mengesahkan penerimaan atau memohon bayaran balik, semak pesanan dengan nombor pesanan dan nombor telefon anda. | 游客凭据过期或不属于该单 |
| `pay.choose_method` | Choose a demo payment method | 选择演示支付方式 | Pilih kaedah pembayaran demo | — |
| `pay.method_card` | Demo credit/debit card (no card number needed) | 演示信用卡/借记卡（无需输入卡号） | Kad kredit/debit demo (tiada nombor kad diperlukan) | Q6 已决 |
| `pay.method_bank` | Demo online banking | 演示网上银行 | Perbankan dalam talian demo | Q6 已决 |
| `pay.method_ewallet` | Demo e-wallet | 演示电子钱包 | E-dompet demo | Q6 已决 |
| `pay.simulate_success` | Simulate success | 模拟支付成功 | Simulasi berjaya | — |
| `pay.simulate_failure` | Simulate failure | 模拟支付失败 | Simulasi gagal | — |
| `pay.action_hint` | No card details are collected and no money moves. Pick an outcome to see what happens. | 不收集任何银行卡资料，也不会有资金流动。选择一个结果看看会发生什么。 | Tiada butiran kad dikumpul dan tiada wang berpindah. Pilih keputusan untuk melihat apa yang berlaku. | ◆ 模拟支付 |
| `pay.expires` | Complete within {minutes} min, or the demo order is cancelled and stock is released. | 请在 {minutes} 分钟内完成，否则演示订单将取消并释放库存。 | Lengkapkan dalam {minutes} minit, atau pesanan demo dibatalkan dan stok dilepaskan. | — |
| `pay.cancel_order` | Cancel this order | 取消此订单 | Batalkan pesanan ini | 游客与会员都显示（Q12 已决） |
| `pay.cancel_confirm` | Cancel this demo order? The held stock, coupon and points will be released. This cannot be undone. | 确定取消此演示订单？保留的库存、优惠券与积分将被释放，此操作不可撤销。 | Batalkan pesanan demo ini? Stok, kupon dan mata yang ditahan akan dilepaskan. Tindakan ini tidak boleh dibatalkan. | — |
| `pay.cancel_confirm_yes` | Yes, cancel order | 确认取消 | Ya, batalkan pesanan | — |
| `pay.cancel_confirm_no` | Keep order | 保留订单 | Kekalkan pesanan | — |
| `pay.processing` | Recording your simulated result… | 正在记录模拟结果… | Sedang merekod keputusan simulasi… | — |
| `pay.demo_hint` | This page stands in for a payment provider. It is not a real payment page. | 本页模拟支付服务商，并非真实支付页面。 | Halaman ini menggantikan penyedia pembayaran. Ia bukan halaman pembayaran sebenar. | ★ |
| `pay.stripe_title` | Payment (Stripe test mode) | 支付（Stripe 测试环境） | Pembayaran (mod ujian Stripe) | P06 标题，支付途径为 Stripe 测试时代替 `pay.title`（0.12） |
| `pay.stripe_demo_hint` | You'll pay on Stripe's test page. Stripe is a real payment service, but in test mode no real money moves. | 您将在 Stripe 的测试付款页付款。Stripe 是真实的支付服务，但在测试环境中不会有真实资金流动。 | Anda akan membayar di halaman ujian Stripe. Stripe ialah perkhidmatan pembayaran sebenar, tetapi dalam mod ujian tiada wang sebenar berpindah. | ★ P06，支付途径为 Stripe 测试时代替 `pay.demo_hint`（0.12） |
| `pay.stripe_notice` | Use only a test card number — never a real card. On Stripe's page, the email is a demo address, and any name or billing address you're asked for can be made up. Whatever you enter there is kept by Stripe's test environment. | 只用测试卡号，切勿输入真实银行卡。Stripe 页上的邮箱是演示地址，要求填写的姓名或账单地址都可填虚构内容。您在那里输入的内容由 Stripe 测试环境保存。 | Gunakan nombor kad ujian sahaja — jangan sekali-kali kad sebenar. Di halaman Stripe, e-mel ialah alamat demo, dan sebarang nama atau alamat pengebilan yang diminta boleh direka. Apa sahaja yang anda masukkan di sana disimpan oleh persekitaran ujian Stripe. | ◆ P06 跳转按钮旁；DESIGN 1.12「Stripe 测试环境支付」第 1、10 条（0.12） |
| `pay.stripe_test_card` | Test card: 4242 4242 4242 4242, any future expiry date, any CVC. If Stripe's page offers other methods such as FPX or GrabPay, follow the test instructions shown there. | 测试卡号：4242 4242 4242 4242，有效期填任意未来日期，CVC 任意。Stripe 页上如有 FPX、GrabPay 等其他方式，请按页面上的测试说明操作。 | Kad ujian: 4242 4242 4242 4242, sebarang tarikh luput akan datang, sebarang CVC. Jika halaman Stripe menawarkan kaedah lain seperti FPX atau GrabPay, ikut arahan ujian yang dipaparkan di sana. | P06；卡号为 Stripe 公布的测试卡号，直接显示（Kelvin 2026-10-15 决定）；不承诺 FPX、GrabPay 一定出现或如何呈现，实现前测试环境核验后如需更具体再改（0.12） |
| `pay.stripe_go` | Go to Stripe test payment | 前往 Stripe 测试付款 | Pergi ke pembayaran ujian Stripe | P06 跳转按钮（0.12） |
| `pay.stripe_redirecting` | Opening Stripe's test payment page… | 正在打开 Stripe 测试付款页… | Membuka halaman pembayaran ujian Stripe… | P06 跳转中（0.12） |
| `pay.stripe_in_progress` | A Stripe test payment for this order is already open. The order stays reserved while that payment page is open; once Stripe confirms the page has expired without a payment, the order is cancelled. | 此订单已有一个进行中的 Stripe 测试付款。该付款页开着期间订单保持保留；经 Stripe 确认付款页已失效且没有付款后，订单才会取消。 | Pembayaran ujian Stripe untuk pesanan ini sudah dibuka. Pesanan kekal ditempah selagi halaman pembayaran itu dibuka; setelah Stripe mengesahkan halaman itu tamat tempoh tanpa pembayaran, pesanan dibatalkan. | P06 有进行中的会话时代替 `pay.expires`；DESIGN 1.12 第 5 条（0.12） |
| `pay.stripe_under_review` | We couldn't confirm whether this order's Stripe test payment page was created. The order stays reserved while the store checks it; you don't need to do anything now. | 暂时无法确认此订单的 Stripe 测试付款页是否已建立。本站核对期间订单保持保留，您现在无需操作。 | Kami tidak dapat mengesahkan sama ada halaman pembayaran ujian Stripe untuk pesanan ini telah dicipta. Pesanan kekal ditempah semasa kedai menyemaknya; anda tidak perlu berbuat apa-apa sekarang. | P06 创建结果超过 24 小时仍无法确定、转人工核对时；DESIGN 1.12 第 3 条（0.12） |
| `pay.stripe_continue` | Continue on Stripe | 继续在 Stripe 付款 | Teruskan di Stripe | P06 打开同一会话（0.12） |
| `pay.stripe_unavailable` | Stripe test mode isn't responding right now. Your order is kept — please try again in a moment. | Stripe 测试环境暂时没有响应。订单已保留，请稍后再试。 | Mod ujian Stripe tidak bertindak balas sekarang. Pesanan anda disimpan — sila cuba lagi sebentar lagi. | P06 创建或查询会话时 Stripe 不可用；DESIGN 1.12 第 9 条（0.12） |
| `pay.method_changed` | The payment method for this order has changed. The page has been updated. | 此订单的支付方式已改变，页面已更新。 | Kaedah pembayaran untuk pesanan ini telah berubah. Halaman telah dikemas kini. | P06 服务端按当时的开关拒绝不符的提交（0.12） |
| `pay.cancel_confirm_stripe` | We'll first try to close this order's open Stripe test payment page. If the payment has already gone through, the order won't be cancelled. | 本站会先尝试关闭此订单进行中的 Stripe 测试付款页；若付款已经完成，订单不会取消。 | Kami akan cuba menutup halaman pembayaran ujian Stripe yang dibuka untuk pesanan ini terlebih dahulu. Jika pembayaran sudah berjaya, pesanan tidak akan dibatalkan. | P06 取消确认框，有进行中的会话时显示在 `pay.cancel_confirm` 下（0.12） |
| `pay.cancel_try_later` | We couldn't reach Stripe to close its payment page, so the order hasn't been cancelled. Please try again in a moment. | 暂时无法联系 Stripe 关闭付款页，订单尚未取消，请稍后再试。 | Kami tidak dapat menghubungi Stripe untuk menutup halaman pembayarannya, jadi pesanan belum dibatalkan. Sila cuba lagi sebentar lagi. | P06 取消时 Stripe 不可用；DESIGN 1.12 第 5 条（0.12） |
| `result.success_title` | Demo payment successful | 模拟支付成功 | Pembayaran demo berjaya | — |
| `result.success_body` | No real money was taken. Your order is now "Paid (demo)". | 未扣任何真实款项。订单状态为“已支付（演示）”。 | Tiada wang sebenar diambil. Pesanan anda kini "Dibayar (demo)". | — |
| `result.points_earned` | You earned {points} demo points. | 您获得了 {points} 演示积分。 | Anda memperoleh {points} mata demo. | 仅会员 |
| `result.guest_next` | To view this order later, confirm receipt or request a refund, use "Track order" with your order number and phone number. | 之后如需查看此订单、确认收货或申请退款，请在“查询订单”中输入订单号和电话。 | Untuk melihat pesanan ini kemudian, mengesahkan penerimaan atau memohon bayaran balik, gunakan "Semak pesanan" dengan nombor pesanan dan nombor telefon anda. | 仅游客；页面不设订单详情按钮 |
| `result.guest_register` | Have a Malaysian or Singapore mobile number? Register to earn points next time. | 有马来西亚或新加坡手机号？注册会员，下次购物可得积分。 | Ada nombor telefon bimbit Malaysia atau Singapura? Daftar untuk dapat mata pada pembelian seterusnya. | 仅游客，链到 P11 |
| `result.failure_title` | Demo payment failed | 模拟支付失败 | Pembayaran demo gagal | — |
| `result.failure_body` | You chose to simulate a failure. Your order is kept, and you can try again without creating a new order. | 您选择了模拟失败。订单已保留，可直接重试，不会重复下单。 | Anda memilih simulasi gagal. Pesanan anda disimpan dan anda boleh cuba lagi tanpa membuat pesanan baharu. | — |
| `result.retry` | Try payment again | 重新支付 | Cuba bayar semula | — |
| `result.cancelled` | This order was cancelled because payment was not completed in time. | 订单因未按时完成支付已取消。 | Pesanan ini dibatalkan kerana pembayaran tidak dilengkapkan tepat pada masanya. | 超时 |
| `result.cancelled_by_you` | You cancelled this demo order. The held stock, coupon and points have been released. | 您已取消此演示订单，保留的库存、优惠券与积分已释放。 | Anda telah membatalkan pesanan demo ini. Stok, kupon dan mata yang ditahan telah dilepaskan. | 本人取消 |
| `result.track` | View this order | 查看订单 | Lihat pesanan ini | 仅会员，去 P09 会员模式 |
| `result.continue` | Continue shopping | 继续购物 | Teruskan membeli-belah | — |
| `result.confirming_title` | Confirming your payment | 正在确认付款 | Mengesahkan pembayaran anda | P07 确认中（0.12） |
| `result.confirming_body` | We're checking this order's payment with Stripe. This page checks again automatically. | 我们正在向 Stripe 核实此订单的付款，本页会自动重查。 | Kami sedang menyemak pembayaran pesanan ini dengan Stripe. Halaman ini menyemak semula secara automatik. | P07 确认中，页面自动重查（0.12） |
| `result.confirming_slow` | Still waiting for Stripe to confirm. Your order stays reserved meanwhile — check again in a little while. | 仍在等待 Stripe 确认，期间订单保持保留，请稍后再查。 | Masih menunggu pengesahan Stripe. Pesanan anda kekal ditempah sementara itu — semak semula sebentar lagi. | P07 自动重查用完后；DESIGN 1.12 第 4、5 条（0.12） |
| `result.check_again` | Check again | 再查一次 | Semak semula | P07 手动重查按钮（0.12） |
| `result.stripe_not_paid` | This order hasn't been paid yet. You can go back to continue the Stripe test payment or cancel the order. | 此订单尚未付款。您可以返回继续 Stripe 测试付款，或取消订单。 | Pesanan ini belum dibayar. Anda boleh kembali untuk meneruskan pembayaran ujian Stripe atau membatalkan pesanan. | P07 会话仍进行中、未付款（0.12） |
| `result.stripe_back_to_pay` | Back to payment | 返回支付页 | Kembali ke pembayaran | P07，去 P06（0.12） |
| `result.stripe_late_refund` | This order had already been cancelled when your Stripe test payment came through, so the store handles that payment as a refund in Stripe's test mode. No real money was involved. | 您的 Stripe 测试付款到达时订单已经取消，这笔付款由本站在 Stripe 测试环境按退款处理。全程不涉及真实资金。 | Pesanan ini telah dibatalkan semasa pembayaran ujian Stripe anda diterima, jadi kedai mengendalikan pembayaran itu sebagai bayaran balik dalam mod ujian Stripe. Tiada wang sebenar terlibat. | P07 已取消且有来源为会话的 Stripe 退款尝试时（不论其当前状态；不承诺退款结果），代替 `result.cancelled` 或 `result.cancelled_by_you`；DESIGN 1.12 第 6 条（0.12） |
| `result.paid_not_cancelled` | Your payment on Stripe had already gone through, so this order was not cancelled. | 您在 Stripe 的付款已经完成，所以此订单没有取消。 | Pembayaran anda di Stripe sudah berjaya, jadi pesanan ini tidak dibatalkan. | P07 成功区顶部，从 P06 取消时会话已付款进入；DESIGN 1.12 第 5 条（0.12） |
| `result.demo_hint` | Next, the store admin will "ship" the order in the demo back office — nothing is actually sent. | 接下来管理员会在后台模拟发货——不会真的寄出。 | Seterusnya, pentadbir kedai akan "menghantar" pesanan dalam pejabat belakang demo — tiada apa yang benar-benar dihantar. | ★ |

## 7. 订单查询、确认收货与退款（P08、P09、P10）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `lookup.title` | Track your order | 查询订单 | Semak pesanan anda | — |
| `lookup.phone` | Phone number used for the order | 下单时填写的电话 | Nombor telefon yang digunakan untuk pesanan | — |
| `lookup.phone_hint` | Enter the number with its country code, starting with +. | 请输入带国家码的号码，以 + 开头。 | Masukkan nombor bersama kod negara, bermula dengan +. | — |
| `lookup.submit` | Find order | 查询 | Cari pesanan | — |
| `lookup.not_found` | We couldn't find an order with these details. Please check the order number and phone number. | 找不到与此资料相符的订单，请检查订单号和电话。 | Kami tidak menemui pesanan dengan butiran ini. Sila semak nombor pesanan dan nombor telefon. | 不区分“不存在”与“电话不符” |
| `lookup.access_note` | After a successful lookup, this browser can view this order, confirm receipt and request a refund for 30 minutes. Payment and cancellation are not available here. Each other order needs its own order number and phone number. | 查询成功后，本浏览器可在 30 分钟内查看此订单、确认收货和申请退款；此处不能支付或取消。查询其他订单须另行输入该单的订单号和电话。 | Selepas semakan berjaya, pelayar ini boleh melihat pesanan ini, mengesahkan penerimaan dan memohon bayaran balik selama 30 minit. Pembayaran dan pembatalan tidak tersedia di sini. Setiap pesanan lain memerlukan nombor pesanan dan nombor telefonnya sendiri. | 查单授权；DESIGN「权限与资料保护」 |
| `lookup.privacy_warning` | Anyone who knows both the order number and the phone number can see the full shipping details. Keep them private. | 同时知道订单号和电话的人都能看到完整收货资料，请妥善保管。 | Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Simpan dengan selamat. | — |
| `lookup.demo_hint` | Demo orders have no real parcel or tracking number. | 演示订单没有真实包裹或物流单号。 | Pesanan demo tiada bungkusan atau nombor penjejakan sebenar. | ★ |
| `order.title` | Order {orderNo} | 订单 {orderNo} | Pesanan {orderNo} | — |
| `order.current_status` | Status: | 状态： | Status: | — |
| `order.progress` | Progress | 进度 | Kemajuan | — |
| `order.amount_breakdown` | Amount details | 金额明细 | Butiran jumlah | 前台 P09 与后台 A02 共用 |
| `order.lookup_access` | You can view this order, confirm receipt and request a refund in this browser for 30 minutes after looking it up. | 查询后 30 分钟内，您可在本浏览器查看此订单、确认收货和申请退款。 | Anda boleh melihat pesanan ini, mengesahkan penerimaan dan memohon bayaran balik dalam pelayar ini selama 30 minit selepas menyemaknya. | 查单模式 |
| `order.lookup_another` | Track another order | 查询其他订单 | Semak pesanan lain | 去 P08，输入框为空 |
| `order.session_expired` | Access to this order has ended in this browser. Please look it up again with the order number and phone number. | 本浏览器对此订单的访问已结束，请凭订单号和电话重新查询。 | Akses kepada pesanan ini telah tamat dalam pelayar ini. Sila semak semula dengan nombor pesanan dan nombor telefon. | 查单授权过期或不属于该单 |
| `order.lookup_no_pay` | Payment and cancellation are not available from order tracking. Unpaid demo orders are cancelled automatically after 15 minutes. | 查询订单页不提供支付或取消。未支付的演示订单会在 15 分钟后自动取消。 | Pembayaran dan pembatalan tidak tersedia melalui semakan pesanan. Pesanan demo yang belum dibayar dibatalkan secara automatik selepas 15 minit. | 查单模式，待支付订单 |
| `order.status_awaiting` | Awaiting demo payment | 待模拟支付 | Menunggu bayaran demo | — |
| `order.status_paid` | Paid (demo) | 已支付（演示） | Dibayar (demo) | — |
| `order.status_packed` | Packed (demo) | 已打包（演示） | Dibungkus (demo) | — |
| `order.status_shipped` | Shipped (demo) | 已发货（演示） | Dihantar (demo) | — |
| `order.status_completed` | Completed (demo) | 已完成（演示） | Selesai (demo) | — |
| `order.status_cancelled` | Cancelled (demo) | 已取消（演示） | Dibatalkan (demo) | — |
| `order.items` | Items | 商品 | Item | — |
| `order.unit_price` | Unit price | 单价 | Harga seunit | — |
| `order.cash_paid` | Amount paid (excluding points discount) | 实付金额（不含积分抵扣） | Amaun dibayar (tidak termasuk diskaun mata) | 逐件实付；前台不用「现金 / cash / tunai」 |
| `order.confirm_receipt` | Confirm receipt | 确认收货 | Sahkan penerimaan | 查单模式与会员模式 |
| `order.confirm_receipt_hint` | Nothing was really delivered — confirming only moves the demo order to "Completed". If you do nothing, it completes automatically 7 days after shipping. | 并没有真实包裹——确认只会把演示订单改为“已完成”。若不操作，模拟发货 7 天后自动完成。 | Tiada penghantaran sebenar — pengesahan hanya menukar pesanan demo kepada "Selesai". Jika tiada tindakan, ia selesai secara automatik 7 hari selepas penghantaran. | — |
| `order.request_refund` | Request a refund | 申请退款 | Mohon bayaran balik | 查单模式与会员模式 |
| `order.refund_deadline` | Refunds can be requested until {date}. | 可在 {date} 前申请退款。 | Bayaran balik boleh dimohon sehingga {date}. | 以服务端截止时间为准（Q2 已决） |
| `order.refunded_total` | Refunded so far (demo): RM {amount} | 累计已退（演示）：RM {amount} | Telah dibayar balik (demo): RM {amount} | — |
| `order.refundable_left` | Still refundable: RM {amount} | 剩余可退：RM {amount} | Baki boleh dibayar balik: RM {amount} | — |
| `order.refund_requests` | Refund requests | 退款申请记录 | Permohonan bayaran balik | — |
| `order.refund_requested` | Under review | 审核中 | Dalam semakan | — |
| `order.refund_approved` | Approved (demo) | 已批准（演示） | Diluluskan (demo) | — |
| `order.refund_rejected` | Rejected | 已拒绝 | Ditolak | — |
| `order.fulfilment_frozen` | All items have been refunded, so this order will not move further. | 所有商品均已退款，订单不再推进。 | Semua item telah dibayar balik, jadi pesanan ini tidak akan diteruskan. | — |
| `order.demo_hint` | Status changes are simulated by the store admin. | 状态变化由店铺管理员模拟推进。 | Perubahan status disimulasikan oleh pentadbir kedai. | ★ |
| `refund.title` | Request a demo refund | 申请模拟退款 | Mohon bayaran balik demo | — |
| `refund.select_items` | Choose items and quantities | 选择商品与数量 | Pilih item dan kuantiti | — |
| `refund.max_qty` | Up to {count} | 最多 {count} 件 | Sehingga {count} | — |
| `refund.estimate` | Estimated refund (demo): RM {amount} | 预计退款（演示）：RM {amount} | Anggaran bayaran balik (demo): RM {amount} | — |
| `refund.points_back` | Points returned: {points} | 返还积分：{points} | Mata dikembalikan: {points} | 仅会员订单 |
| `refund.points_reversed` | Points taken back: {points} | 追回积分：{points} | Mata ditarik balik: {points} | 仅会员订单 |
| `refund.expired_points_note` | Points that have already expired are not returned. | 已过期的积分不返还。 | Mata yang telah tamat tempoh tidak dikembalikan. | 仅会员订单 |
| `refund.shipping_not_refunded` | Sample shipping fees are not refunded. | 示例运费不退。 | Kos penghantaran contoh tidak dibayar balik. | — |
| `refund.coupon_not_restored` | Used coupons are not restored. | 已使用的优惠券不恢复。 | Kupon yang telah digunakan tidak dipulihkan. | — |
| `refund.submit` | Submit refund request | 提交退款申请 | Hantar permohonan bayaran balik | — |
| `refund.submit_hint` | This is a simulated refund: no real money will be returned. The amount is calculated by the system from the amount paid for each item, excluding any points discount. | 这是模拟退款：不会退还任何真实款项。金额由系统按每件商品的实付金额（不含积分抵扣）计算。 | Ini bayaran balik simulasi: tiada wang sebenar akan dikembalikan. Jumlah dikira oleh sistem berdasarkan amaun dibayar bagi setiap item, tidak termasuk diskaun mata. | ◆ 退款；前台不用「现金 / cash / tunai」 |
| `refund.submitted` | Refund request submitted. The result will appear on this order. | 退款申请已提交，结果会显示在此订单中。 | Permohonan dihantar. Keputusan akan dipaparkan pada pesanan ini. | — |
| `refund.duplicate` | This quantity is already under review. | 该数量已在审核中。 | Kuantiti ini sudah dalam semakan. | — |
| `refund.nothing_left` | Nothing is left to refund on this order. | 此订单已无可退商品。 | Tiada lagi item untuk dibayar balik pada pesanan ini. | — |
| `refund.window_closed` | The 30-day refund period for this order has ended. | 此订单的 30 天退款期已过。 | Tempoh bayaran balik 30 hari untuk pesanan ini telah tamat. | — |
| `refund.demo_hint` | Refunds are reviewed by the admin in the demo back office. | 退款由管理员在演示后台审核。 | Bayaran balik disemak oleh pentadbir dalam pejabat belakang demo. | ★ |

## 8. 短信验证、注册、登录与会员中心（V1、P11、P12、P13）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `auth.register_title` | Create a member account | 注册会员 | Daftar akaun ahli | — |
| `auth.same_flow_note` | Verify your mobile number by SMS. If it already belongs to a member, you'll be logged in; otherwise a member account is created. | 通过短信验证手机号。号码已是会员则直接登录，否则创建会员账号。 | Sahkan nombor telefon bimbit anda melalui SMS. Jika ia sudah milik ahli, anda akan dilog masuk; jika tidak, akaun ahli akan dicipta. | P11 与 P12 短信页签共用 |
| `auth.sms_scope` | SMS verification is currently available for Malaysia (+60) and Singapore (+65) mobile numbers only. | 短信验证目前仅支持马来西亚（+60）和新加坡（+65）手机号。 | Pengesahan SMS kini hanya untuk nombor telefon bimbit Malaysia (+60) dan Singapura (+65). | V1 |
| `auth.phone` | Mobile number | 手机号 | Nombor telefon bimbit | V1、P05 第 1 步 |
| `auth.challenge` | Please complete the check below first. | 请先完成下方人机验证。 | Sila lengkapkan semakan di bawah dahulu. | V1 |
| `auth.challenge_failed` | The check wasn't completed. Please try again. | 人机验证未完成，请重试。 | Semakan tidak dilengkapkan. Sila cuba lagi. | V1 |
| `auth.send_code` | Send code | 发送验证码 | Hantar kod | V1 |
| `auth.resend_code` | Send a new code | 重新发送验证码 | Hantar kod baharu | V1；受限流 |
| `auth.code_sent` | We sent a code by SMS to {phoneMasked}. | 验证码已通过短信发送至 {phoneMasked}。 | Kami telah menghantar kod melalui SMS ke {phoneMasked}. | V1 |
| `auth.code` | Verification code | 验证码 | Kod pengesahan | V1 |
| `auth.verify_submit` | Verify | 验证 | Sahkan | V1 |
| `auth.code_wrong` | That code is incorrect or has expired. | 验证码错误或已过期。 | Kod itu salah atau telah tamat tempoh. | V1；不降级为游客 |
| `auth.code_too_many` | Too many incorrect codes. Please wait and request a new code later. | 验证码错误次数过多，请稍后重新获取验证码。 | Terlalu banyak kod salah. Sila tunggu dan minta kod baharu kemudian. | V1；不降级为游客 |
| `auth.verified_login` | Verified. You're logged in. | 验证成功，您已登录。 | Disahkan. Anda telah log masuk. | V1，号码已注册 |
| `auth.verified_registered` | Verified. Your member account has been created and you're logged in. You can set a password in My account. | 验证成功，会员账号已创建并已登录。您可在会员中心设置密码。 | Disahkan. Akaun ahli anda telah dicipta dan anda telah log masuk. Anda boleh menetapkan kata laluan di Akaun saya. | V1，新建账号 |
| `auth.not_supported_country` | We can't send SMS to this number yet. You can still check out as a guest. | 暂时无法向此号码发送短信。您仍可以游客身份下单。 | Kami belum dapat menghantar SMS ke nombor ini. Anda masih boleh membuat pesanan sebagai tetamu. | 仅注册、短信登录时；重设密码与注销确认改用 `auth.sms_not_sent_no_change` |
| `auth.sms_failed` | The SMS could not be sent, so no account was created or changed. You can still check out as a guest. | 短信发送失败，账号未创建或更改。您仍可以游客身份下单。 | SMS tidak dapat dihantar, jadi tiada akaun dicipta atau diubah. Anda masih boleh membuat pesanan sebagai tetamu. | 仅注册、短信登录时；重设密码与注销确认改用 `auth.sms_not_sent_no_change` |
| `auth.sms_paused` | SMS is currently switched off, so registration, SMS login and password reset are unavailable. Members who have set a password can still log in with it. | 短信目前已关闭，暂不能注册、短信登录或重设密码。已设置密码的会员仍可用密码登录。 | SMS kini dimatikan, jadi pendaftaran, log masuk SMS dan tetapan semula kata laluan tidak tersedia. Ahli yang telah menetapkan kata laluan masih boleh log masuk dengannya. | 短信验证关闭时代替 V1（结账以外）（0.6） |
| `auth.sms_not_sent_no_change` | The SMS was not sent, and nothing about your account has been changed. Please try again later. | 短信未发出，您的账号未作任何更改。请稍后再试。 | SMS tidak dihantar, dan tiada apa-apa pada akaun anda telah diubah. Sila cuba lagi kemudian. | V1 用于重设密码与注销确认时，短信发送失败或号码不在白名单；不显示游客文字或 `auth.continue_guest` |
| `auth.reset_not_registered` | This number isn't registered yet, so no account was created and no password was set. You can register with this number instead. | 此号码尚未注册，因此未创建账号，也未设置密码。您可以用此号码注册。 | Nombor ini belum didaftarkan, jadi tiada akaun dicipta dan tiada kata laluan ditetapkan. Anda boleh mendaftar dengan nombor ini. | P12 忘记密码，未注册号码通过短信验证后；旁附 `common.nav_register` 链到 P11 |
| `auth.continue_guest` | Continue as guest | 以游客身份继续 | Teruskan sebagai tetamu | 仅注册、短信登录时 |
| `auth.claim_notice` | Once verified, guest orders placed with this number in the last 30 days are added to your account. Points are not added for past orders. | 验证通过后，近 30 天内用此号码下单的游客订单会自动归入您的账号；过去的订单不补发积分。 | Setelah disahkan, pesanan tetamu dengan nombor ini dalam 30 hari lalu akan ditambah ke akaun anda. Mata tidak ditambah untuk pesanan lalu. | 认领剩余风险 Kelvin 已接受（Q11 已决）；措辞不变 |
| `auth.register_demo_hint` | Registration sends a real SMS to your phone. Everything else remains a demo. | 注册会向您的手机发送真实短信；其余一切仍是演示。 | Pendaftaran menghantar SMS sebenar ke telefon anda. Selain itu, semuanya kekal demo. | ★ P11 |
| `auth.login_title` | Log in | 登录 | Log masuk | — |
| `auth.login_method_password` | Password | 密码登录 | Kata laluan | 登录方式页签 |
| `auth.login_method_sms` | SMS code | 短信验证码登录 | Kod SMS | 登录方式页签 |
| `auth.password` | Password | 密码 | Kata laluan | P12、A01 |
| `auth.password_rule` | At least 8 characters. | 至少 8 位。 | Sekurang-kurangnya 8 aksara. | Q15 已决 |
| `auth.login_submit` | Log in | 登录 | Log masuk | — |
| `auth.login_failed` | Mobile number or password is incorrect. If you haven't set a password, log in with an SMS code. | 手机号或密码不正确。如未设置密码，请用短信验证码登录。 | Nombor telefon bimbit atau kata laluan salah. Jika anda belum menetapkan kata laluan, log masuk dengan kod SMS. | 未设密码与密码错误同一条 |
| `auth.login_failed_sms_off` | Mobile number or password is incorrect. | 手机号或密码不正确。 | Nombor telefon bimbit atau kata laluan salah. | 短信验证关闭时代替 `auth.login_failed`（0.6） |
| `auth.forgot` | Forgot password? | 忘记密码？ | Lupa kata laluan? | — |
| `auth.reset_title` | Reset password | 重设密码 | Tetapkan semula kata laluan | — |
| `auth.new_password` | New password | 新密码 | Kata laluan baharu | P12、P13 |
| `auth.reset_submit` | Set new password | 设置新密码 | Tetapkan kata laluan baharu | — |
| `auth.reset_done` | Password updated. Please log in. | 密码已更新，请重新登录。 | Kata laluan dikemas kini. Sila log masuk. | — |
| `auth.login_demo_hint` | Members can see order history, demo points and coupons, and confirm receipt or request refunds for their orders. | 会员可查看历史订单、演示积分与优惠券，并对自己的订单确认收货或申请退款。 | Ahli boleh melihat sejarah pesanan, mata demo dan kupon, serta mengesahkan penerimaan atau memohon bayaran balik bagi pesanan mereka. | ★ P12 |
| `account.orders` | My orders | 我的订单 | Pesanan saya | 也用作 P09 会员模式返回按钮 |
| `account.orders_hint` | Open an order to confirm receipt or request a refund. | 打开订单可确认收货或申请退款。 | Buka pesanan untuk mengesahkan penerimaan atau memohon bayaran balik. | — |
| `account.orders_empty` | No orders yet. | 还没有订单。 | Tiada pesanan lagi. | — |
| `account.order_view` | View | 查看 | Lihat | 链到 P09 会员模式 |
| `account.order_pay` | Continue payment | 继续支付 | Teruskan pembayaran | P09 会员模式待支付订单，回到 P06 继续支付或取消（Q17 已决） |
| `account.settings` | Settings | 设置 | Tetapan | — |
| `account.points_type_earned` | Earned | 获得 | Diperoleh | 积分明细类型 |
| `account.points_type_redeemed` | Used at checkout | 结账抵扣 | Digunakan semasa daftar keluar | 积分明细类型 |
| `account.points_type_expired` | Expired | 到期失效 | Tamat tempoh | 积分明细类型 |
| `account.points_type_returned` | Returned after refund | 退款返还 | Dikembalikan selepas bayaran balik | 积分明细类型 |
| `account.points_type_reversed` | Taken back after refund | 退款追回 | Ditarik balik selepas bayaran balik | 积分明细类型 |
| `account.points` | My points | 我的积分 | Mata saya | — |
| `account.points_balance` | Balance: {points} points | 余额：{points} 积分 | Baki: {points} mata | — |
| `account.points_pending` | Owed from refunds: {points} points (settled from future points) | 退款后尚欠积分：{points} 积分（以后获得的积分会先用于补足） | Tertunggak daripada bayaran balik: {points} mata (dijelaskan daripada mata akan datang) | — |
| `account.points_expiry` | {points} points expire on {date} | {points} 积分将于 {date} 到期 | {points} mata tamat tempoh pada {date} | — |
| `account.points_history` | Points history | 积分明细 | Sejarah mata | — |
| `account.coupons` | My coupons | 我的优惠券 | Kupon saya | 可用券 + 使用记录（Q13 已决） |
| `account.coupons_available` | Available coupons | 可用优惠券 | Kupon tersedia | 列出所有启用中且在有效期内的券，对全部会员相同，不按每会员或总次数上限过滤（Q19 已决） |
| `account.coupon_value_fixed` | RM {amount} off | 减 RM {amount} | Potongan RM {amount} | — |
| `account.coupon_value_percent` | {percent}% off | 减 {percent}% | Potongan {percent}% | — |
| `account.coupon_min_spend` | Minimum item spend: RM {amount} | 最低商品消费：RM {amount} | Perbelanjaan item minimum: RM {amount} | — |
| `account.coupon_valid_until` | Valid until {date} | 有效期至 {date} | Sah sehingga {date} | — |
| `account.coupon_use_hint` | Enter the code at checkout. Coupons reduce the item amount only. | 结账时输入代码即可使用；优惠券只抵商品金额。 | Masukkan kod semasa daftar keluar. Kupon hanya mengurangkan jumlah item. | — |
| `account.coupons_available_empty` | No coupons are available right now. | 目前没有可用的优惠券。 | Tiada kupon tersedia buat masa ini. | — |
| `account.coupons_used` | Coupons you've used | 使用记录 | Kupon yang telah anda gunakan | — |
| `account.coupon_used_on` | Used on order {orderNo} | 用于订单 {orderNo} | Digunakan pada pesanan {orderNo} | — |
| `account.coupons_used_empty` | You haven't used any coupons yet. | 您还没有使用过优惠券。 | Anda belum menggunakan sebarang kupon. | — |
| `account.password_title` | Password | 密码 | Kata laluan | — |
| `account.password_none` | You haven't set a password yet. You can keep logging in with an SMS code, or set a password now. | 您尚未设置密码。可继续用短信验证码登录，或现在设置密码。 | Anda belum menetapkan kata laluan. Anda boleh terus log masuk dengan kod SMS, atau tetapkan kata laluan sekarang. | 首次设置，已登录即可 |
| `account.sms_paused_set_password` | SMS is currently switched off, so you can't log in with an SMS code. Set a password now so you can log in again after you log out. | 短信目前已关闭，暂不能用短信验证码登录。请现在设置密码，以便退出后能再次登录。 | SMS kini dimatikan, jadi anda tidak boleh log masuk dengan kod SMS. Tetapkan kata laluan sekarang supaya anda boleh log masuk semula selepas log keluar. | 短信验证关闭、未设密码时（0.6） |
| `account.password_set_submit` | Set password | 设置密码 | Tetapkan kata laluan | — |
| `account.password_set_done` | Password set. You can now log in with your password or an SMS code. | 密码已设置，之后可用密码或短信验证码登录。 | Kata laluan ditetapkan. Anda kini boleh log masuk dengan kata laluan atau kod SMS. | — |
| `account.password_exists` | A password is set. Changing it requires SMS verification. | 已设置密码；更改密码须经短信验证。 | Kata laluan telah ditetapkan. Menukarnya memerlukan pengesahan SMS. | — |
| `account.password_change` | Change password | 更改密码 | Tukar kata laluan | 去 P12 重设流程 |
| `account.delete` | Delete account | 注销账号 | Padam akaun | — |
| `account.delete_warning` | Deleting your account removes your mobile number and password, logs you out everywhere, and forfeits your points and unused coupons. Your orders, including their shipping details, stay in our records but are no longer linked to you, and registering again with the same number will not restore them. This cannot be undone. | 注销将删除您的手机号和密码并退出所有登录，积分余额与未用优惠券作废。订单（含收货资料）仍保留在记录中，但不再与您关联，用同一号码重新注册也不会恢复。此操作不可撤销。 | Memadam akaun akan membuang nombor telefon bimbit dan kata laluan anda, log keluar di semua peranti, dan melucutkan mata serta kupon yang belum digunakan. Pesanan anda, termasuk butiran penghantaran, kekal dalam rekod tetapi tidak lagi dikaitkan dengan anda, dan mendaftar semula dengan nombor yang sama tidak akan memulihkannya. Tindakan ini tidak boleh dibatalkan. | DESIGN「权限与资料保护」「资料保留」 |
| `account.delete_verify` | To confirm, verify your mobile number by SMS first. | 请先通过短信验证手机号以确认注销。 | Untuk mengesahkan, sahkan nombor telefon bimbit anda melalui SMS dahulu. | Q15 已决 |
| `account.delete_confirm` | Delete my account | 确认注销 | Padam akaun saya | 短信验证通过后才显示 |
| `account.deleted` | Your account has been deleted. | 您的账号已注销。 | Akaun anda telah dipadam. | — |
| `account.delete_verify_password` | SMS is currently switched off. To confirm, enter your password. | 短信目前已关闭。请输入密码以确认注销。 | SMS kini dimatikan. Untuk mengesahkan, masukkan kata laluan anda. | 短信验证关闭、已设密码时代替 `account.delete_verify`（0.6） |
| `account.delete_password_wrong` | That password is incorrect. Your account has not been deleted. | 密码不正确，账号未注销。 | Kata laluan itu salah. Akaun anda belum dipadam. | （0.6） |
| `account.delete_verify_session` | SMS is currently switched off and you haven't set a password, so you can confirm deletion while logged in. | 短信目前已关闭，且您尚未设置密码，可在当前登录状态下确认注销。 | SMS kini dimatikan dan anda belum menetapkan kata laluan, jadi anda boleh mengesahkan pemadaman semasa log masuk. | 短信验证关闭、未设密码时代替 `account.delete_verify`（0.6） |
| `account.delete_session_confirm` | Delete your account now? This cannot be undone. | 确定现在注销账号？此操作不可撤销。 | Padam akaun anda sekarang? Tindakan ini tidak boleh dibatalkan. | 确认框（0.6） |
| `account.delete_keep` | Keep my account | 保留账号 | Kekalkan akaun saya | 确认框（0.6） |
| `account.demo_hint` | Points and coupons are demo only and have no cash value. | 积分与优惠券均为演示，没有现金价值。 | Mata dan kupon adalah demo sahaja dan tiada nilai tunai. | ★ P13 |

## 9. 隐私说明（P14）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `privacy.title` | Privacy | 隐私说明 | Privasi | — |
| `privacy.intro` | This site is a demo store. We collect only what is needed to run the demo, and nothing is really paid for or delivered. | 本站是演示网店，只收集完成演示所需的资料，不发生真实付款或寄送。 | Laman ini ialah kedai demo. Kami hanya mengumpul apa yang diperlukan untuk demo, dan tiada apa yang benar-benar dibayar atau dihantar. | — |
| `privacy.h_collect` | What we collect | 收集什么 | Apa yang kami kumpul | 段标题 |
| `privacy.h_retention` | How long we keep it | 保留多久 | Berapa lama kami menyimpannya | 段标题 |
| `privacy.h_access` | Who can see it | 谁能看到 | Siapa yang boleh melihatnya | 段标题 |
| `privacy.h_sms_logs` | SMS and logs | 短信与日志 | SMS dan log | 段标题 |
| `privacy.h_contact` | Contact | 联系 | Hubungi | 段标题；WhatsApp 未配置时整段隐藏 |
| `privacy.contact_button` | Chat on WhatsApp | 通过 WhatsApp 联系 | Sembang di WhatsApp | 链接为占位 `{{WHATSAPP_CONTACT_LINK}}`；未配置时隐藏 |
| `privacy.collect` | For orders: recipient name, phone number, country, region, address and postcode. For members: mobile number and, if you set one, a securely hashed password. We do not ask for email or ID documents. | 订单：收货人姓名、电话、国家、地区、地址与邮编。会员：手机号，以及（如已设置）安全哈希后的密码。不收集邮箱或证件。 | Untuk pesanan: nama penerima, nombor telefon, negara, wilayah, alamat dan poskod. Untuk ahli: nombor telefon bimbit dan, jika ditetapkan, kata laluan yang di-hash dengan selamat. Kami tidak meminta e-mel atau dokumen pengenalan. | — |
| `privacy.fictional` | You may use fictional shipping details. The exception is when SMS verification is on: a Malaysian or Singapore mobile number entered at checkout must then be able to receive our verification SMS. | 收货资料可以填写虚构内容。唯一例外是短信验证开启时，结账时填写的马来西亚或新加坡手机号须能收到验证短信。 | Anda boleh menggunakan butiran penghantaran rekaan. Pengecualiannya ialah apabila pengesahan SMS dihidupkan: nombor telefon bimbit Malaysia atau Singapura yang dimasukkan semasa daftar keluar mesti boleh menerima SMS pengesahan kami. | 0.6 修改 |
| `privacy.retention_recipient` | Shipping details are kept long-term together with the order's items, amounts and status. They are not deleted or anonymised, including after a member account is deleted. | 收货资料与订单商品、金额和状态一起长期保存，不删除、不匿名化；会员注销后也同样保留。 | Butiran penghantaran disimpan untuk jangka panjang bersama item, jumlah dan status pesanan. Ia tidak dipadam atau dianonimkan, termasuk selepas akaun ahli dipadam. | DESIGN「资料保留」；不提供删除入口 |
| `privacy.member` | Member mobile numbers, including accounts created automatically at checkout, are kept until you delete your account; inactive accounts are not deleted automatically. SMS verification records are kept briefly to prevent abuse. | 会员手机号（含结账时自动注册的账号）保留至您注销账号，长期未登录也不会自动注销。短信验证记录短期保留，用于防滥用。 | Nombor telefon bimbit ahli, termasuk akaun yang dicipta secara automatik semasa daftar keluar, disimpan sehingga anda memadam akaun; akaun yang tidak aktif tidak dipadam secara automatik. Rekod pengesahan SMS disimpan untuk tempoh singkat bagi mencegah penyalahgunaan. | — |
| `privacy.member_backup` | After you delete your account, copies of your mobile number and password hash made before the deletion may remain in our database backups for a period that has not yet been determined. | 注销账号后，注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间。 | Selepas anda memadam akaun, salinan nombor telefon bimbit dan hash kata laluan anda yang dibuat sebelum pemadaman mungkin kekal dalam sandaran pangkalan data untuk suatu tempoh yang belum ditentukan. | Q18 已决：对外披露；不写天数，不写或暗示备份一定会在某时被清除；DESIGN「资料保留」 |
| `privacy.browser_access` | After you place a guest order or look up an order, this browser gets access to that order for 30 minutes. After 30 minutes, or in another browser, look the order up again with its order number and phone number. | 游客下单或查询订单后，本浏览器获得对该订单 30 分钟的访问。30 分钟后，或在其他浏览器上，请凭订单号和电话重新查询该订单。 | Selepas anda membuat pesanan tetamu atau menyemak pesanan, pelayar ini mendapat akses kepada pesanan itu selama 30 minit. Selepas 30 minit, atau dalam pelayar lain, semak pesanan itu semula dengan nombor pesanan dan nombor telefonnya. | DESIGN「权限与资料保护」；不表示该单以后只能在本浏览器打开 |
| `privacy.lookup_risk` | Anyone who knows both the order number and the phone number can view the full shipping details. Because shipping details are kept long-term, this stays possible for as long as the order exists. | 同时知道订单号和电话的人可以查看完整收货资料。由于收货资料长期保存，只要订单存在，这一点就一直成立。 | Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Oleh sebab butiran penghantaran disimpan untuk jangka panjang, ini kekal boleh berlaku selagi pesanan wujud. | — |
| `privacy.sms` | When SMS verification is on, checkout verification for Malaysian and Singapore numbers, registration, SMS login, password reset and account deletion send a real SMS through our SMS provider. | 短信验证开启时，马新号码结账验证、注册、短信登录、重设密码与注销确认会通过短信服务商发送真实短信。 | Apabila pengesahan SMS dihidupkan, pengesahan daftar keluar untuk nombor Malaysia dan Singapura, pendaftaran, log masuk SMS, tetapan semula kata laluan dan pemadaman akaun menghantar SMS sebenar melalui penyedia SMS kami. | 0.6 修改 |
| `privacy.sms_toggle` | We can switch SMS verification off. While it is off, no SMS is sent: visitors who are not logged in check out as guests whatever their number; registration, SMS login and password reset are paused; and members confirm account deletion with their password, or while logged in if they have not set one. | 我们可以关闭短信验证。关闭期间不发送任何短信：未登录的访客无论号码都以游客身份结账；注册、短信登录与重设密码暂停；会员以密码确认注销，未设密码者在登录状态下确认。 | Kami boleh mematikan pengesahan SMS. Semasa ia dimatikan, tiada SMS dihantar: pelawat yang tidak log masuk mendaftar keluar sebagai tetamu tanpa mengira nombor mereka; pendaftaran, log masuk SMS dan tetapan semula kata laluan digantung; dan ahli mengesahkan pemadaman akaun dengan kata laluan mereka, atau semasa log masuk jika belum menetapkannya. | （0.6） |
| `privacy.stripe` | When payments use Stripe test mode, you pay on Stripe's test page instead of ours. We send Stripe only the amount and currency, a demo item name, an internal order reference, your language, the links back to this site and a fixed demo email — not your name, phone number, address or order number. Anything you type on Stripe's page, such as a cardholder name, billing address or a changed email, is kept by Stripe's test environment; we don't read or store it and can't delete it item by item, only the whole test environment. Use only test card numbers and made-up details there. | 支付使用 Stripe 测试环境时，您在 Stripe 的测试付款页而不是本站付款。本站发给 Stripe 的只有金额与币种、演示商品名、内部订单编号、您的语言、返回本站的链接与固定的演示邮箱，不发送您的姓名、电话、地址或订单号。您在 Stripe 页上输入的内容（如持卡人姓名、账单地址或改填的邮箱）由 Stripe 测试环境保存；本站不读取、不保存，也无法逐条删除，只能删除整个测试环境。请在那里只用测试卡号与虚构资料。 | Apabila pembayaran menggunakan mod ujian Stripe, anda membayar di halaman ujian Stripe, bukan di halaman kami. Kami hanya menghantar kepada Stripe jumlah dan mata wang, nama item demo, rujukan pesanan dalaman, bahasa anda, pautan kembali ke laman ini dan e-mel demo yang tetap — bukan nama, nombor telefon, alamat atau nombor pesanan anda. Apa sahaja yang anda taip di halaman Stripe, seperti nama pemegang kad, alamat pengebilan atau e-mel yang diubah, disimpan oleh persekitaran ujian Stripe; kami tidak membaca atau menyimpannya dan tidak boleh memadamnya satu demi satu, hanya keseluruhan persekitaran ujian. Gunakan nombor kad ujian dan butiran rekaan sahaja di sana. | P14「收集什么」段；始终显示；DESIGN 1.12「Stripe 测试环境支付」第 10 条、「资料保留」（0.12） |
| `privacy.logs` | Our logs are kept for up to 30 days and do not contain your name, full phone number, address or password. | 日志最多保留 30 天，不含您的姓名、完整电话、地址或密码。 | Log kami disimpan sehingga 30 hari dan tidak mengandungi nama, nombor telefon penuh, alamat atau kata laluan anda. | — |
| `privacy.contact` | Questions? Contact Acuven on WhatsApp. | 有疑问？请通过 WhatsApp 联系 Acuven。 | Ada soalan? Hubungi Acuven melalui WhatsApp. | 链接为占位；未配置时隐藏 |
| `privacy.demo_hint` | This whole site is a demonstration; no real orders are fulfilled. | 整个网站都是演示，不履行任何真实订单。 | Seluruh laman ini adalah demonstrasi; tiada pesanan sebenar dipenuhi. | ★ |

## 10. 管理后台（A01–A07）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `admin.demo_banner` | Admin — demo store. Actions here never move real money or goods. | 管理后台——演示网店。这里的操作不会产生真实资金或货物流动。 | Pentadbir — kedai demo. Tindakan di sini tidak pernah memindahkan wang atau barang sebenar. | ★ 后台常驻 |
| `admin.login_title` | Admin log in | 管理员登录 | Log masuk pentadbir | — |
| `admin.username` | Username | 用户名 | Nama pengguna | 停用：0.10 起 A01 改用 `admin.email` |
| `admin.email` | Email | 邮箱 | E-mel | A01 登录名输入框（0.10） |
| `admin.version` | Version {commit} | 版本 {commit} | Versi {commit} | A01 页面底部；`{commit}` 为 `/api/healthz` 返回的 commit 前 7 位，取不到时整行不显示（Kelvin 2026-11-02） |
| `admin.login_failed` | Username or password is incorrect. | 用户名或密码不正确。 | Nama pengguna atau kata laluan salah. | A01 |
| `admin.locked` | Too many failed attempts. This sign-in is locked for a short time. | 失败次数过多，登录已短时锁定。 | Terlalu banyak cubaan gagal. Log masuk ini dikunci untuk seketika. | — |
| `admin.nav_orders` | Orders | 订单 | Pesanan | — |
| `admin.nav_refunds` | Refunds | 退款审核 | Bayaran balik | — |
| `admin.nav_stripe_pending` | Stripe follow-ups | Stripe 待处理 | Susulan Stripe | A11 导航与页标题，排在 `admin.nav_refunds` 之后（0.21） |
| `admin.nav_products` | Products | 商品 | Produk | — |
| `admin.nav_categories` | Categories | 分类 | Kategori | A10 导航与页标题，排在 `admin.nav_products` 之后（0.18） |
| `admin.nav_coupons` | Coupons | 优惠券 | Kupon | — |
| `admin.nav_promotions` | Promotions | 促销活动 | Promosi | A12 导航与页标题，排在 `admin.nav_coupons` 之后（0.22） |
| `admin.nav_shipping` | Shipping & demo rates | 运费与演示汇率 | Penghantaran & kadar demo | — |
| `admin.nav_stock_resets` | Stock resets | 库存重置 | Tetapan semula stok | — |
| `admin.filter_status` | Status | 状态 | Status | — |
| `admin.search_order` | Search by order number | 按订单号搜索 | Cari mengikut nombor pesanan | — |
| `admin.col_date` | Date | 日期 | Tarikh | 列头 |
| `admin.col_total` | Total (MYR) | 合计（MYR） | Jumlah (MYR) | 列头 |
| `admin.col_refunds` | Refunds | 退款 | Bayaran balik | 列头 |
| `admin.refunds_pending` | {count} pending | {count} 项待审 | {count} menunggu | — |
| `admin.order_detail` | Order details | 订单详情 | Butiran pesanan | — |
| `admin.event_log` | Event log | 事件记录 | Log peristiwa | 不含收货资料原文 |
| `admin.col_time` | Time | 时间 | Masa | 列头 |
| `admin.col_event` | Event | 事件 | Peristiwa | 列头 |
| `admin.col_actor` | By | 操作者 | Oleh | 列头 |
| `admin.actor_admin` | Admin | 管理员 | Pentadbir | 操作者类别 |
| `admin.actor_system` | System | 系统 | Sistem | 操作者类别 |
| `admin.actor_customer` | Customer | 顾客 | Pelanggan | 操作者类别 |
| `admin.recipient_raw` | Shipping details (original) | 原始收货资料 | Butiran penghantaran (asal) | 长期保存；不设导出 |
| `admin.recipient_audited` | Viewing these details is recorded in the audit log. | 查看此资料会记入审计记录。 | Paparan butiran ini direkodkan dalam log audit. | — |
| `admin.mark_packed` | Mark as packed (demo) | 标记为已打包（演示） | Tandakan dibungkus (demo) | — |
| `admin.mark_shipped` | Mark as shipped (demo) | 标记为已发货（演示） | Tandakan dihantar (demo) | — |
| `admin.ship_hint` | No real parcel or courier booking is created. | 不会产生真实包裹或物流下单。 | Tiada bungkusan atau tempahan kurier sebenar dicipta. | — |
| `admin.frozen` | All items refunded — fulfilment is frozen. | 全部商品已退款，履约已冻结。 | Semua item dibayar balik — pemenuhan dibekukan. | — |
| `admin.col_requested_at` | Requested at | 申请时间 | Dimohon pada | 列头 |
| `admin.col_amount` | Amount (MYR) | 金额（MYR） | Amaun (MYR) | 列头 |
| `admin.refund_detail` | Refund request details | 退款申请详情 | Butiran permohonan bayaran balik | — |
| `admin.refund_qty` | Requested {requested} / bought {bought} / approved {approved} | 申请 {requested} / 购买 {bought} / 已批准 {approved} | Dimohon {requested} / dibeli {bought} / diluluskan {approved} | — |
| `admin.refund_amount` | Cash refund (demo): RM {amount} | 模拟退现金：RM {amount} | Bayaran balik tunai (demo): RM {amount} | — |
| `admin.refund_points` | Points returned {returned} / taken back {reversed} | 返还积分 {returned} / 追回积分 {reversed} | Mata dikembalikan {returned} / ditarik balik {reversed} | — |
| `admin.refund_approve` | Approve (demo) | 批准（演示） | Luluskan (demo) | — |
| `admin.refund_reject` | Reject | 拒绝 | Tolak | — |
| `admin.refund_reason` | Reason | 理由 | Sebab | — |
| `admin.refund_hint` | Approving never sends real money. | 批准不会退还任何真实款项。 | Kelulusan tidak pernah menghantar wang sebenar. | — |
| `admin.reviewed_by` | Reviewed by {username} on {time} | 由 {username} 于 {time} 审核 | Disemak oleh {username} pada {time} | A03 已审核的申请；{username} 为审核人，{time} 为审核时间（0.9） |
| `admin.product_edit` | Edit product | 编辑商品 | Sunting produk | — |
| `admin.content_language` | Content language | 内容语言 | Bahasa kandungan | 商品三语文案页签 |
| `admin.product_name` | Product name | 商品名称 | Nama produk | — |
| `admin.product_description` | Description | 描述 | Penerangan | — |
| `admin.product_category` | Category | 分类 | Kategori | — |
| `admin.product_images` | Images | 图片 | Imej | — |
| `admin.upload` | Upload | 上传 | Muat naik | — |
| `admin.variant` | Option values | 规格 | Nilai pilihan | 列头 |
| `admin.image_rules` | JPEG, PNG or WebP, up to 5 MB. | JPEG、PNG 或 WebP，不超过 5 MB。 | JPEG, PNG atau WebP, sehingga 5 MB. | A04、A08（0.18 改：去掉变量） |
| `admin.product_options` | Options (e.g. colour, size) | 规格（如颜色、尺寸） | Pilihan (cth. warna, saiz) | — |
| `admin.sku` | SKU | SKU | SKU | — |
| `admin.price` | Price (MYR) | 价格（MYR） | Harga (MYR) | — |
| `admin.initial_stock` | Daily initial stock | 每日初始库存 | Stok awal harian | — |
| `admin.max_per_order` | Limit per order | 每单限购 | Had setiap pesanan | A04（0.5） |
| `admin.max_per_order_hint` | 1–1000 items, counted across all options of this product. Changes apply to new orders only. | 1–1000 件，按此商品所有规格合计。修改只影响新订单。 | 1–1000 unit, dikira merentas semua pilihan produk ini. Perubahan hanya terpakai untuk pesanan baharu. | A04（0.5；0.18 改为 1–1000） |
| `admin.available_today` | Available today | 今日可用 | Tersedia hari ini | — |
| `admin.adjust_today` | Adjust today's stock | 调整当日库存 | Laraskan stok hari ini | — |
| `admin.initial_stock_note` | Changes to initial stock take effect from the next daily reset. | 修改初始库存从下一次每日重置起生效。 | Perubahan stok awal berkuat kuasa dari tetapan semula harian berikutnya. | A04（0.18 改中文） |
| `admin.active` | Published | 上架 | Diterbitkan | — |
| `admin.translation_missing` | {language} text is missing and will fall back to English. English text is required to publish. | 缺少{language}文案，将回退英文；缺少英文则不能上架。 | Teks {language} tiada dan akan kembali kepada bahasa Inggeris. Teks bahasa Inggeris diperlukan untuk diterbitkan. | — |
| `admin.rules_apply_new` | Changes apply to new orders only. Existing orders keep their original amounts. | 修改只影响新订单，已有订单保持原金额。 | Perubahan hanya terpakai untuk pesanan baharu. Pesanan sedia ada mengekalkan jumlah asal. | — |
| `admin.unpublished` | Not published | 未上架 | Tidak diterbitkan | A04 列表状态（0.18） |
| `admin.product_new` | New product | 新建商品 | Produk baharu | A04 新建时的编辑区标题（0.18） |
| `admin.category_current_unavailable` | This product's current category is disabled or has no English name. You can keep it, but the product can't be published until you choose an enabled category. | 此商品当前所属的分类已停用或缺少英文名称。可以保留不改，但要改选一个启用的分类才能上架。 | Kategori semasa produk ini dinyahaktifkan atau tiada nama bahasa Inggeris. Anda boleh mengekalkannya, tetapi produk tidak boleh diterbitkan sehingga anda memilih kategori yang aktif. | A04（0.18） |
| `admin.publish_requirements` | To publish: English name and description, English names for every option and value, at least one enabled SKU, and an enabled category with an English name. | 上架须有：英文名称与描述、全部规格名与规格值的英文名称、至少一个启用的 SKU，以及启用且有英文名称的分类。 | Untuk diterbitkan: nama dan penerangan bahasa Inggeris, nama bahasa Inggeris bagi setiap pilihan dan nilai, sekurang-kurangnya satu SKU aktif, dan kategori aktif yang mempunyai nama bahasa Inggeris. | A04 常显（0.18） |
| `admin.product_image_rules` | 200–4,000 pixels on each side. Up to 8 images; the first is the cover. Location and other photo data are removed, and the original file isn't kept. | 宽与高 200 到 4,000 像素。最多 8 张，第一张为封面。拍摄地点等照片资料会被去掉，不保留原文件。 | Setiap sisi 200–4,000 piksel. Sehingga 8 imej; yang pertama ialah kulit. Lokasi dan data foto lain dibuang, dan fail asal tidak disimpan. | A04，接在 `admin.image_rules` 之后（0.18） |
| `admin.image_count` | {count} of {max} images | 已有 {count} 张，最多 {max} 张 | {count} daripada {max} imej | A04；{max} 为 8（0.18） |
| `admin.image_cover` | Cover | 封面 | Imej utama | A04 第一张图的标签（0.18） |
| `admin.image_move_earlier` | Move earlier | 前移 | Alih ke depan | A04 读屏标签，`(‹)`（0.18） |
| `admin.image_move_later` | Move later | 后移 | Alih ke belakang | A04 读屏标签，`(›)`（0.18） |
| `admin.image_remove` | Remove image | 移除图片 | Buang imej | A04 读屏标签 `(✗)` 与确认框按钮（0.18） |
| `admin.image_remove_confirm` | Remove this image? It can't be restored; upload it again if you need it. | 移除这张图片？移除后无法恢复，需要时请重新上传。 | Buang imej ini? Ia tidak boleh dipulihkan; muat naik semula jika perlu. | A04 确认框（0.18） |
| `admin.images_immediate` | Image changes are saved straight away and don't need Save. | 图片的上传、排序与移除立即生效，不需要点保存。 | Perubahan imej disimpan serta-merta dan tidak memerlukan Simpan. | A04（0.18） |
| `admin.image_save_first` | Save the new product first, then upload images. | 请先保存新商品，再上传图片。 | Simpan produk baharu dahulu, kemudian muat naik imej. | A04 新建未保存时（0.18） |
| `admin.image_uploading` | Uploading… | 正在上传… | Sedang memuat naik… | A04（0.18） |
| `admin.image_type_unsupported` | This file type isn't supported. Use JPEG, PNG or WebP. | 不支持此文件格式，请用 JPEG、PNG 或 WebP。 | Jenis fail ini tidak disokong. Gunakan JPEG, PNG atau WebP. | A04 上传被拒：格式不支持（0.18） |
| `admin.image_too_large` | This file is larger than 5 MB. | 文件超过 5 MB。 | Fail ini melebihi 5 MB. | A04 上传被拒：文件太大（0.18） |
| `admin.images_full` | This product already has 8 images. Remove one before uploading another. | 此商品已有 8 张图片，请先移除一张再上传。 | Produk ini sudah mempunyai 8 imej. Buang satu sebelum memuat naik yang lain. | A04 上传被拒：图片已满（0.18） |
| `admin.image_invalid` | This image can't be used. It must be a single still image, 200–4,000 pixels on each side. | 这张图片不能使用：须为单张静态图，宽与高都在 200 到 4,000 像素之间。 | Imej ini tidak boleh digunakan. Ia mesti satu imej pegun, 200–4,000 piksel pada setiap sisi. | A04 上传被拒：图片不合格（0.18） |
| `admin.images_changed` | Images were changed elsewhere. Reload to see the latest images. | 图片已在别处修改，请重新读取最新的图片。 | Imej telah diubah di tempat lain. Muat semula untuk melihat imej terkini. | A04 排序被拒：图片已被改动（0.18） |
| `admin.option_values` | Values | 规格值 | Nilai | A04（0.18） |
| `admin.option_name` | Option name | 规格名 | Nama pilihan | A04 规格名输入框（0.18） |
| `admin.option_value` | Option value | 规格值 | Nilai pilihan | A04 读屏标签：规格值输入框与新 SKU 的规格值下拉框（0.18） |
| `admin.add_value` | Add value | 新增规格值 | Tambah nilai | A04；每个规格名至多 20 个值（0.18） |
| `admin.add_option` | Add option | 新增规格名 | Tambah pilihan | A04 新建商品时；至多 3 个（0.18） |
| `admin.no_options` | No options: this product has a single SKU. | 无规格：此商品只有一个 SKU。 | Tiada pilihan: produk ini mempunyai satu SKU sahaja. | A04（0.18） |
| `admin.options_fixed_note` | Options can't be added or removed after the product is created. To change them, create a new product and unpublish this one. | 规格名在新建商品后不能增减。要换规格，请新建商品并把此商品下架。 | Pilihan tidak boleh ditambah atau dibuang selepas produk dicipta. Untuk mengubahnya, cipta produk baharu dan nyahterbitkan produk ini. | A04（0.18） |
| `admin.sku_enabled` | Enabled | 启用 | Aktif | A04 SKU 列头与勾选框（0.18） |
| `admin.sku_code_on_save` | Code created on save | 保存时生成代码 | Kod dicipta semasa simpan | A04 新增、还没保存的 SKU（0.18） |
| `admin.add_sku` | Add SKU | 新增 SKU | Tambah SKU | A04；至多 50 个 SKU（0.18） |
| `admin.no_delete_note` | Products, options, values and SKUs can't be deleted because orders refer to them. Unpublish a product or disable a SKU instead. | 商品、规格名、规格值与 SKU 不能删除（订单会引用它们）。不再卖的商品请下架，SKU 请停用。 | Produk, pilihan, nilai dan SKU tidak boleh dipadam kerana pesanan merujuknya. Nyahterbitkan produk atau nyahaktifkan SKU sebagai ganti. | A04（0.18） |
| `admin.product_saved` | Saved. New quotes and orders use these values. | 已保存，之后的计价与下单按新值执行。 | Disimpan. Sebut harga dan pesanan baharu menggunakan nilai ini. | A04 保存成功（0.18） |
| `admin.product_created` | Product created. You can now upload images. | 商品已新建，现在可以上传图片。 | Produk dicipta. Anda kini boleh memuat naik imej. | A04 新建成功（0.18） |
| `admin.product_invalid` | Check the values: names up to 200 characters, descriptions up to 5,000, option and value names up to 100; English names are required for a new product and for new options and values, with no duplicates; prices RM0.00–RM500,000.00; daily initial stock 0–9,999; limit per order 1–1000; each SKU a different combination; up to 3 options, 20 values per option and 50 SKUs. To publish, the requirements above must also be met. | 请检查：名称不超过 200 个字符，描述不超过 5,000 个字符，规格名与规格值的名称不超过 100 个字符；新建商品与新增的规格名、规格值须有英文名称且不重复；价格 RM0.00 到 RM500,000.00；每日初始库存 0 到 9,999；每单限购 1 到 1000；每个 SKU 的规格组合不同；至多 3 个规格名、每个规格名 20 个值、50 个 SKU。上架还须满足上面的条件。 | Semak nilai: nama sehingga 200 aksara, penerangan sehingga 5,000, nama pilihan dan nilai sehingga 100; nama bahasa Inggeris diperlukan untuk produk baharu serta pilihan dan nilai baharu, tanpa pendua; harga RM0.00–RM500,000.00; stok awal harian 0–9,999; had setiap pesanan 1–1000; setiap SKU gabungan yang berbeza; sehingga 3 pilihan, 20 nilai setiap pilihan dan 50 SKU. Untuk diterbitkan, syarat di atas juga mesti dipenuhi. | A04 取值不合格（0.18） |
| `admin.product_changed` | This product was changed elsewhere. Reload to see the latest version. Your unsaved changes will be lost. | 此商品已在别处修改，请重新读取最新版本，未保存的修改会丢失。 | Produk ini telah diubah di tempat lain. Muat semula untuk melihat versi terkini. Perubahan yang belum disimpan akan hilang. | A04 商品已被改动，旁边 `admin.reload`（0.18） |
| `admin.adjust_title` | Adjust today's stock for {sku} | 调整 {sku} 的当日库存 | Laraskan stok hari ini untuk {sku} | A04 展开的调整区（0.18） |
| `admin.adjust_shown` | Shown now: {count} | 当前显示：{count} | Dipaparkan kini: {count} | A04；页面显示的当日可用库存，随请求提交（0.18） |
| `admin.adjust_new` | New stock for today | 新的当日库存 | Stok baharu hari ini | A04（0.18） |
| `admin.adjust_save` | Set today's stock | 改为此值 | Tetapkan stok hari ini | A04；立即提交（0.18） |
| `admin.adjust_note` | Takes effect now and lasts until the next daily reset. The daily initial stock and existing orders are not changed. | 立即生效，保留到下一次每日重置；不改每日初始库存，也不改已有订单。 | Berkuat kuasa sekarang hingga tetapan semula harian berikutnya. Stok awal harian dan pesanan sedia ada tidak diubah. | A04（0.18） |
| `admin.adjust_saved` | Today's stock updated. | 当日库存已更新。 | Stok hari ini dikemas kini. | A04 调整成功（0.18） |
| `admin.stock_changed` | Today's stock changed in the meantime (for example, an order was placed or cancelled). It is now {count}. Check it and set it again if needed. | 当日库存在此期间已变动（例如有订单下单或取消），现在为 {count}。请核对后视需要再次调整。 | Stok hari ini telah berubah sementara itu (contohnya, pesanan dibuat atau dibatalkan). Kini {count}. Semak dan tetapkan semula jika perlu. | A04 库存已变动；{count} 为服务端返回的当前值（0.18） |
| `admin.adjust_invalid` | Enter a whole number from 0 to 9,999. | 请输入 0 到 9,999 的整数。 | Masukkan nombor bulat dari 0 hingga 9,999. | A04 调整的取值不合格（0.18） |
| `admin.categories_count` | {count} of {max} categories | 已有 {count} 个分类，最多 {max} 个 | {count} daripada {max} kategori | A10；{max} 为 30（0.18） |
| `admin.categories_full` | The store already has the maximum of 30 categories. Categories can't be deleted; rename and re-enable a disabled one instead. | 已达 30 个分类的上限。分类不能删除，可把已停用的分类改名后重新启用。 | Kedai sudah mempunyai maksimum 30 kategori. Kategori tidak boleh dipadam; namakan semula dan aktifkan semula kategori yang dinyahaktifkan. | A10 已满时（0.18） |
| `admin.category_name` | Category name | 分类名称 | Nama kategori | A10 列头与输入框（0.18） |
| `admin.category_slug` | Link code | 链接代码 | Kod pautan | A10 列头；分类 slug（0.18） |
| `admin.category_slug_hint` | Created from the English name and never changes, so filter links keep working. Categories can't be deleted; disable one you no longer use. | 按英文名称生成，之后不变，筛选链接因此一直有效。分类不能删除，不再用的请停用。 | Dicipta daripada nama bahasa Inggeris dan tidak berubah, jadi pautan penapis terus berfungsi. Kategori tidak boleh dipadam; nyahaktifkan yang tidak digunakan lagi. | A10（0.18） |
| `admin.category_visible` | Products shown | 前台可见商品 | Produk dipaparkan | A10 列头；满足发布规则的商品件数（0.18） |
| `admin.category_enabled` | Enabled | 启用 | Aktif | A10 列头与勾选框（0.18） |
| `admin.category_on` | Enabled | 启用中 | Aktif | A10 状态（0.18） |
| `admin.category_off` | Disabled | 已停用 | Dinyahaktifkan | A10 状态（0.18） |
| `admin.category_edit` | Edit category | 编辑分类 | Sunting kategori | A10（0.18） |
| `admin.category_new` | New category | 新建分类 | Kategori baharu | A10（0.18） |
| `admin.category_translation_missing` | {language} name is missing and will fall back to English. An English name is required to enable the category. | 缺少{language}名称，将回退英文；缺少英文名称则不能启用。 | Nama {language} tiada dan akan kembali kepada bahasa Inggeris. Nama bahasa Inggeris diperlukan untuk mengaktifkan kategori. | A10（0.18） |
| `admin.category_disable_confirm` | Products from this category shown in the store: {count}. After disabling, the category and these products disappear from the store (including featured products on the home page), and carts can't check out with them. Disable it? | 此分类下正在前台显示的商品：{count} 件。停用后，该分类与这些商品都不再在前台显示（含首页精选），购物车里的这些商品不能下单。确定停用？ | Produk daripada kategori ini yang dipaparkan di kedai: {count}. Selepas dinyahaktifkan, kategori dan produk ini hilang dari kedai (termasuk produk pilihan di halaman utama), dan troli tidak boleh membuat pesanan untuknya. Nyahaktifkan? | A10 确认框；{count} 为 `admin.category_visible` 的件数（0.18） |
| `admin.category_disable` | Disable | 停用 | Nyahaktifkan | A10 确认框按钮（0.18） |
| `admin.category_saved` | Saved. The storefront now uses these categories. | 已保存，前台已改用这些分类。 | Disimpan. Kedai kini menggunakan kategori ini. | A10 保存成功（0.18） |
| `admin.category_created` | Category created. | 分类已新建。 | Kategori dicipta. | A10 新建成功（0.18） |
| `admin.category_invalid` | Check the values: names up to 200 characters; English names must differ from those of other categories; a new or enabled category needs an English name; up to 30 categories. | 请检查：名称不超过 200 个字符；英文名称不能与其他分类重复；新建或启用的分类须有英文名称；分类至多 30 个。 | Semak nilai: nama sehingga 200 aksara; nama bahasa Inggeris mesti berbeza daripada kategori lain; kategori baharu atau aktif memerlukan nama bahasa Inggeris; sehingga 30 kategori. | A10 取值不合格（0.18） |
| `admin.category_changed` | This category was changed elsewhere. Reload to see the latest version. Your unsaved changes will be lost. | 此分类已在别处修改，请重新读取最新版本，未保存的修改会丢失。 | Kategori ini telah diubah di tempat lain. Muat semula untuk melihat versi terkini. Perubahan yang belum disimpan akan hilang. | A10 分类已被改动，旁边 `admin.reload`（0.18） |
| `admin.unsaved_confirm` | You have unsaved changes. Leave without saving them? | 有未保存的修改，确定不保存就离开？ | Anda mempunyai perubahan yang belum disimpan. Tinggalkan tanpa menyimpannya? | A04、A10 确认框（0.18） |
| `admin.unsaved_leave` | Leave without saving | 不保存，离开 | Tinggalkan tanpa simpan | A04、A10 确认框按钮（0.18） |
| `admin.unsaved_stay` | Keep editing | 继续编辑 | Teruskan menyunting | A04、A10 确认框按钮（0.18） |
| `admin.coupon_listed_note` | All active coupons within their valid dates are listed under "My coupons", the same for every member, regardless of total or per-member usage limits. | 所有启用中且在有效期内的优惠券都会列在会员中心「我的优惠券」中，对全部会员相同，不因总次数或每会员次数上限而不列出。 | Semua kupon aktif dalam tempoh sah disenaraikan di bawah "Kupon saya", sama bagi setiap ahli, tanpa mengira had jumlah penggunaan atau had setiap ahli. | Q19 已决；不改数据模型 |
| `admin.coupon_code` | Coupon code | 优惠券代码 | Kod kupon | — |
| `admin.coupon_type` | Type | 类型 | Jenis | — |
| `admin.coupon_value` | Value | 面额 | Nilai | RM 或 % |
| `admin.coupon_usage` | Used / limit | 已用 / 上限 | Digunakan / had | 列头 |
| `admin.coupon_enabled` | Active | 启用 | Aktif | 状态 |
| `admin.coupon_disabled` | Disabled | 已停用 | Dinyahaktifkan | 状态 |
| `admin.coupon_fixed` | Fixed amount (RM) | 固定金额（RM） | Jumlah tetap (RM) | — |
| `admin.coupon_percent` | Percentage (%) | 百分比（%） | Peratusan (%) | — |
| `admin.coupon_valid` | Valid from / until | 有效期起止 | Sah dari / hingga | — |
| `admin.coupon_min_spend` | Minimum item spend (RM) | 最低商品消费（RM） | Perbelanjaan item minimum (RM) | — |
| `admin.coupon_total_limit` | Total uses allowed | 总使用次数上限 | Had jumlah penggunaan | — |
| `admin.coupon_member_limit` | Uses per member | 每会员使用次数上限 | Had penggunaan setiap ahli | — |
| `admin.coupon_disable` | Disable | 停用 | Nyahaktifkan | — |
| `admin.coupon_code_taken` | This coupon code is already in use. Choose another code. | 此优惠券代码已被使用，请换一个代码。 | Kod kupon ini sudah digunakan. Sila pilih kod lain. | A05 新建被拒：代码重复（0.11） |
| `admin.coupon_invalid` | Check the code (4–20 letters or digits), value, minimum spend, usage limits and dates. | 请检查代码（4 到 20 位字母或数字）、面额、最低消费、次数上限与日期。 | Semak kod (4–20 huruf atau digit), nilai, perbelanjaan minimum, had penggunaan dan tarikh. | A05 新建被拒：代码、面额、最低消费、次数上限或日期不合格（0.11） |
| `admin.shipping_title` | Sample shipping fees | 示例运费 | Kos penghantaran contoh | 区块标题 |
| `admin.shipping_by_country` | By country | 按国家 | Mengikut negara | — |
| `admin.add_country` | Add country | 新增国家 | Tambah negara | — |
| `admin.shipping_country` | Country | 国家/地区 | Negara | — |
| `admin.shipping_my_state` | Malaysia | 马来西亚 | Malaysia | 区块标题（0.17 改） |
| `admin.shipping_my_west` | West Malaysia | 西马 | Malaysia Barat | A06（0.17） |
| `admin.shipping_my_east` | East Malaysia (Sabah, Sarawak, Labuan) | 东马（沙巴、砂拉越、纳闽） | Malaysia Timur (Sabah, Sarawak, Labuan) | A06（0.17） |
| `admin.shipping_other` | All other countries | 其他国家 | Semua negara lain | — |
| `admin.shipping_fee` | Sample shipping fee (RM) | 示例运费（RM） | Kos penghantaran contoh (RM) | — |
| `admin.fx_title` | Demo exchange rates | 演示汇率 | Kadar pertukaran demo | 区块标题 |
| `admin.fx_rate` | Demo rate: 1 MYR = {rate} {currency} | 演示汇率：1 MYR = {rate} {currency} | Kadar demo: 1 MYR = {rate} {currency} | — |
| `admin.fx_version` | Version {n} | 版本 {n} | Versi {n} | — |
| `admin.remove` | Remove | 删除 | Padam | A06 国家运费与汇率每行（0.16） |
| `admin.add_fx` | Add exchange rate | 新增汇率 | Tambah kadar pertukaran | A06（0.16） |
| `admin.fx_currency` | Currency code | 币种代码 | Kod mata wang | A06 新增的汇率行（0.16） |
| `admin.fx_decimals` | Decimal places | 小数位 | Tempat perpuluhan | A06 新增的汇率行（0.16） |
| `admin.fx_rate_new` | Demo rate (1 MYR =) | 演示汇率（1 MYR =） | Kadar demo (1 MYR =) | A06 新增的汇率行（0.16） |
| `admin.rates_saved` | Saved. New orders use the new shipping fees; reference currencies show the new rates. | 已保存。新订单按新运费计算，参考外币按新汇率显示。 | Disimpan. Pesanan baharu menggunakan kos penghantaran baharu; mata wang rujukan memaparkan kadar baharu. | A06 保存成功（0.16） |
| `admin.rates_invalid` | Check the values: fees RM0.00–RM9,999.99; rates above 0 and below 1,000,000, with up to 6 decimal places; currency code 3 letters (not MYR); decimal places 0–3; each country only once. | 请检查：运费为 RM0.00 到 RM9,999.99；汇率大于 0、小于 1,000,000，至多 6 位小数；币种代码为 3 个字母（不是 MYR）；小数位为 0 到 3；每个国家只能有一行。 | Semak nilai: kos RM0.00–RM9,999.99; kadar melebihi 0 dan di bawah 1,000,000, dengan paling banyak 6 tempat perpuluhan; kod mata wang 3 huruf (bukan MYR); tempat perpuluhan 0–3; setiap negara sekali sahaja. | A06 保存被拒：取值不合格（0.16） |
| `admin.rates_changed` | Fees or rates were changed elsewhere. Reload to see the latest values. Your unsaved changes will be lost. | 运费或汇率已在别处修改。请重新读取最新的值，未保存的修改会丢失。 | Kos atau kadar telah diubah di tempat lain. Muat semula untuk melihat nilai terkini. Perubahan yang belum disimpan akan hilang. | A06 保存被拒：数据已被改动（0.16） |
| `admin.reload` | Reload | 重新读取 | Muat semula | A06 数据已被改动时（0.16） |
| `admin.stock_reset_title` | Daily stock reset results | 每日库存重置结果 | Keputusan tetapan semula stok harian | — |
| `admin.col_date_myt` | Date (Malaysia time) | 日期（马来西亚时间） | Tarikh (waktu Malaysia) | 列头 |
| `admin.col_result` | Result | 结果 | Keputusan | 列头 |
| `admin.col_sku_count` | SKUs | SKU 数 | Bilangan SKU | 列头 |
| `admin.stock_reset_breakdown` | Initial {initial} − active holds {held} = available today {available} | 初始 {initial} − 有效预留 {held} = 当日可用 {available} | Awal {initial} − tahanan aktif {held} = tersedia hari ini {available} | — |
| `admin.stock_reset_ok` | Completed | 已完成 | Selesai | — |
| `admin.stock_reset_failed` | Failed — an operator alert has been logged | 失败——已记录运营告警 | Gagal — amaran pengendali telah direkodkan | 0.20 改为不承诺送达；DESIGN 1.21「运营告警邮件」第 7 条 |
| `admin.stock_reset_note` | Resets restore today's stock only. Past orders, refunds and points are not changed. | 重置只恢复当日库存，不改动历史订单、退款与积分。 | Tetapan semula hanya memulihkan stok hari ini. Pesanan, bayaran balik dan mata lalu tidak diubah. | — |
| `admin.stock_reset_off_note` | While daily stock reset is turned off in Site settings, no resets run and no new results appear here. | 在站点设置中关闭每日恢复库存期间，不会重置，这里也没有新的记录。 | Semasa tetapan semula stok harian dimatikan dalam Tetapan laman, tiada tetapan semula dijalankan dan tiada keputusan baharu dipaparkan di sini. | A07 常显（0.18） |
| `admin.nav_store_design` | Store design | 店铺装修 | Reka bentuk kedai | A08 导航与页标题（0.4） |
| `admin.design_demo_note` | The demo banner and demo hints always show on the storefront and can't be turned off here. | 前台的演示横幅与演示提示始终显示，这里不能关闭。 | Sepanduk demo dan petunjuk demo sentiasa dipaparkan di kedai dan tidak boleh dimatikan di sini. | A08（0.4） |
| `admin.theme` | Theme | 主题 | Tema | A08（0.4） |
| `admin.theme_pandan` | Pandan | 班兰 | Pandan | 主题名（0.4） |
| `admin.theme_pasar` | Pasar Pagi | 早市 | Pasar Pagi | 主题名（0.4） |
| `admin.theme_receipt` | Receipt | 小票 | Resit | 主题名（0.4） |
| `admin.theme_kopitiam` | Kopitiam | 咖啡店 | Kopitiam | 主题名（0.4） |
| `admin.theme_batik` | Batik | 蜡染 | Batik | 主题名（0.4） |
| `admin.theme_malam` | Pasar Malam | 夜市 | Pasar Malam | 主题名（0.4） |
| `admin.theme_gula` | Gula-Gula | 糖果 | Gula-Gula | 主题名（0.4） |
| `admin.theme_galeri` | Galeri | 画廊 | Galeri | 主题名（0.4） |
| `admin.theme_songket` | Songket | 金线 | Songket | 主题名（0.4） |
| `admin.theme_litar` | Litar | 电路 | Litar | 主题名（0.4） |
| `admin.theme_dark_note` | Every theme has a light and a dark version. Visitors see the one that matches their device setting. | 每款主题都有浅色与深色，访客看到哪一种取决于其设备设置。 | Setiap tema ada versi cerah dan gelap. Pelawat melihat versi yang sepadan dengan tetapan peranti mereka. | A08（0.4） |
| `admin.preview` | Preview | 预览 | Pratonton | A08；只在本页显示未保存的选择（0.4） |
| `admin.preview_light` | Light | 浅色 | Cerah | A08 预览切换（0.4） |
| `admin.preview_dark` | Dark | 深色 | Gelap | A08 预览切换（0.4） |
| `admin.accent` | Accent colour | 主色 | Warna utama | A08（0.4） |
| `admin.accent_option` | Colour {n} | 颜色 {n} | Warna {n} | 读屏标签：主色色块（0.4） |
| `admin.accent_hint` | Only colours that stay readable in both light and dark mode are offered. | 只提供在浅色与深色下都清楚可读的颜色。 | Hanya warna yang kekal jelas dalam mod cerah dan gelap ditawarkan. | A08（0.4） |
| `admin.logo` | Logo | 店铺标志 | Logo | A08（0.4） |
| `admin.logo_hint` | Shown instead of the ACUVEN SHOP text in the storefront header and footer. | 在前台页头与页脚代替文字 ACUVEN SHOP 显示。 | Dipaparkan menggantikan teks ACUVEN SHOP pada pengepala dan pengaki kedai. | A08；格式与大小限制沿用 `admin.image_rules`（0.4） |
| `admin.logo_remove` | Remove logo | 移除标志 | Buang logo | A08（0.4） |
| `admin.logo_rules` | 40–4,000 pixels on each side, with the longer side at least 200. Location and other photo data are removed, and the original file isn't kept. | 宽与高 40 到 4,000 像素，长边至少 200 像素。拍摄地点等照片资料会被去掉，不保留原文件。 | Setiap sisi 40–4,000 piksel, dengan sisi lebih panjang sekurang-kurangnya 200. Lokasi dan data foto lain dibuang, dan fail asal tidak disimpan. | A08，接在 `admin.image_rules` 之后（0.19） |
| `admin.logo_immediate` | Logo changes are saved straight away and don't need Save. | 标志的上传与移除立即生效，不需要点保存。 | Perubahan logo disimpan serta-merta dan tidak memerlukan Simpan. | A08（0.19） |
| `admin.logo_invalid` | This image can't be used. It must be a single still image, 40–4,000 pixels on each side, with the longer side at least 200. | 这张图片不能使用：须为单张静态图，宽与高都在 40 到 4,000 像素之间，长边至少 200 像素。 | Imej ini tidak boleh digunakan. Ia mesti satu imej pegun, 40–4,000 piksel pada setiap sisi, dengan sisi lebih panjang sekurang-kurangnya 200. | A08 上传被拒：图片不合格（0.19） |
| `admin.logo_remove_confirm` | Remove the logo? It can't be restored; upload it again if you need it. | 移除店铺标志？移除后无法恢复，需要时请重新上传。 | Buang logo? Ia tidak boleh dipulihkan; muat naik semula jika perlu. | A08 确认框（0.19） |
| `admin.home_blocks` | Home page blocks | 首页区块 | Blok halaman utama | A08（0.4） |
| `admin.block_hero` | Main banner | 主视觉 | Sepanduk utama | A08 区块名；其余三块用 `home.how_title`、`home.categories`、`home.featured`（0.4） |
| `admin.block_show` | Show | 显示 | Papar | A08 区块显隐勾选框（0.4） |
| `admin.block_move_up` | Move up | 上移 | Alih ke atas | 读屏标签（0.4） |
| `admin.block_move_down` | Move down | 下移 | Alih ke bawah | 读屏标签（0.4） |
| `admin.home_blocks_hint` | Block text comes from the store's translations and can't be edited here. The demo hint on the home page always shows above these blocks. | 区块文字来自店铺的三语文案，不能在这里修改。首页的演示提示始终显示在这些区块上方。 | Teks blok datang daripada terjemahan kedai dan tidak boleh disunting di sini. Petunjuk demo di halaman utama sentiasa dipaparkan di atas blok ini. | A08（0.4） |
| `admin.featured_pick` | Featured products (up to 4) | 精选商品（最多 4 件） | Produk pilihan (sehingga 4) | A08（0.5） |
| `admin.featured_add` | Add product | 添加商品 | Tambah produk | A08（0.5） |
| `admin.featured_remove` | Remove from featured | 移出精选 | Keluarkan daripada pilihan | A08 读屏标签，`(✗)`（0.5） |
| `admin.featured_hint` | Only published products are shown on the home page, in this order. If none are chosen, or none of them are published, the 4 newest products are shown. | 首页按此顺序只显示仍上架的商品；未挑选或挑选的都已下架时，显示最新的 4 件。 | Hanya produk yang diterbitkan dipaparkan di halaman utama, mengikut susunan ini. Jika tiada yang dipilih atau tiada yang diterbitkan, 4 produk terbaharu dipaparkan. | A08（0.5） |
| `admin.design_saved` | Saved. The storefront now uses these settings. | 已保存，前台已改用这些设置。 | Disimpan. Kedai kini menggunakan tetapan ini. | A08 保存成功（0.4） |
| `admin.nav_settings` | Site settings | 站点设置 | Tetapan laman | A09 导航与页标题（0.6） |
| `admin.logout` | Log out | 退出登录 | Log keluar | 后台除 A01 外每页右上角（0.7） |
| `admin.logged_in` | You're logged in as {username}. The other admin pages are not available yet. | 已以 {username} 登录。后台其他页面尚未上线。 | Anda telah log masuk sebagai {username}. Halaman pentadbir lain belum tersedia. | A01 登录成功后（过渡，A02 上线前；0.8） |
| `admin.sms_toggle` | SMS verification | 短信验证 | Pengesahan SMS | A09（0.6） |
| `admin.sms_on` | On | 开启 | Hidup | A09（0.6） |
| `admin.sms_off` | Off (default) | 关闭（默认） | Mati (lalai) | A09（0.6） |
| `admin.sms_toggle_hint` | When on, Malaysian and Singapore numbers verify by SMS at checkout and become members, and registration, SMS login, password reset and account deletion use SMS. When off, no SMS is sent: visitors who are not logged in check out as guests whatever their number, registration, SMS login and password reset are paused, and members confirm account deletion with their password or, without one, while logged in. | 开启时，马新号码结账须短信验证并成为会员，注册、短信登录、重设密码与注销确认使用短信。关闭时不发送任何短信：未登录的访客无论号码都以游客身份结账，注册、短信登录与重设密码暂停，会员以密码确认注销，未设密码者在登录状态下确认。 | Apabila dihidupkan, nombor Malaysia dan Singapura disahkan melalui SMS semasa daftar keluar dan menjadi ahli, dan pendaftaran, log masuk SMS, tetapan semula kata laluan serta pemadaman akaun menggunakan SMS. Apabila dimatikan, tiada SMS dihantar: pelawat yang tidak log masuk mendaftar keluar sebagai tetamu tanpa mengira nombor mereka, pendaftaran, log masuk SMS dan tetapan semula kata laluan digantung, dan ahli mengesahkan pemadaman akaun dengan kata laluan atau, jika tiada, semasa log masuk. | A09（0.6） |
| `admin.sms_toggle_cost` | Turning this on sends real SMS messages, which cost money. Turn it on only after the SMS service has been set up and checked. | 开启后会发送真实短信并产生费用。请在短信服务开通并核实后再开启。 | Menghidupkan ini akan menghantar SMS sebenar yang dikenakan kos. Hidupkan hanya selepas perkhidmatan SMS disediakan dan disemak. | A09 演示提示（0.6） |
| `admin.sms_not_configured` | SMS verification can't be turned on: the SMS service, its anti-bot check or its sending limits are not fully set up. | 无法开启短信验证：短信服务、人机验证或发送限额的配置不完整。 | Pengesahan SMS tidak boleh dihidupkan: perkhidmatan SMS, semakan anti-bot atau had penghantarannya belum disediakan sepenuhnya. | A09 选项禁用或保存被拒；DESIGN 1.15「边界与原则」（0.15） |
| `admin.settings_saved` | Saved. New requests use this setting. | 已保存，之后的请求按此设置执行。 | Disimpan. Permintaan baharu menggunakan tetapan ini. | A09 保存成功（0.6） |
| `admin.stock_reset_toggle` | Daily stock reset | 每日恢复库存 | Tetapan semula stok harian | A09（0.18） |
| `admin.stock_reset_on` | On (default) | 开启（默认） | Hidup (lalai) | A09（0.18） |
| `admin.stock_reset_off` | Off | 关闭 | Mati | A09（0.18） |
| `admin.stock_reset_toggle_hint` | When on, each SKU's stock for today is reset every day (Malaysia time) to its daily initial stock minus active holds. When off, stock changes only with orders, cancellations and today's adjustments, and stays at zero once sold out. If today's reset hasn't run yet, turning it back on resets today's stock within about a minute, replacing adjustments made earlier today. | 开启时，每天（马来西亚时间）把各 SKU 的当日库存恢复为每日初始库存减去仍有效的预留。关闭时，库存只随下单、取消与当日库存调整变化，卖完即为零。当天还没恢复的，重新开启后约一分钟内即恢复当天库存，并覆盖当天此前的调整。 | Apabila hidup, stok hari ini bagi setiap SKU ditetapkan semula setiap hari (waktu Malaysia) kepada stok awal harian tolak tahanan aktif. Apabila mati, stok hanya berubah dengan pesanan, pembatalan dan pelarasan hari ini, dan kekal sifar apabila habis dijual. Jika tetapan semula hari ini belum dijalankan, menghidupkannya semula menetapkan semula stok hari ini dalam kira-kira seminit, menggantikan pelarasan yang dibuat awal hari ini. | A09（0.18） |
| `admin.payment_method` | Payment method | 支付方式 | Kaedah pembayaran | A09（0.12） |
| `admin.payment_simulated` | Simulated (default) | 模拟（默认） | Simulasi (lalai) | A09（0.12） |
| `admin.payment_stripe` | Stripe test mode | Stripe 测试环境 | Mod ujian Stripe | A09（0.12） |
| `admin.payment_method_hint` | Simulated: customers pick an outcome on the store's own demo payment page. Stripe test mode: customers pay on Stripe's test payment page with test cards or other test methods offered there (such as FPX or GrabPay), and no real money moves; for those orders, approving a refund also sends a refund request to Stripe's test mode, and its status is shown in the refund details. Orders below Stripe's minimum amount still use simulated payment. Stripe payment sessions already started keep being handled through Stripe even if you switch back. | 模拟：顾客在本站的演示支付页选择结果。Stripe 测试环境：顾客在 Stripe 测试付款页用测试卡或该页提供的其他测试方式（如 FPX、GrabPay）付款，不会有真实资金流动；这些订单的退款经您批准后，也会向 Stripe 测试环境发出退款请求，其状态在退款详情中查看。低于 Stripe 最低收款额的订单仍用模拟支付。已在 Stripe 发起的付款会话，即使切回模拟也仍按 Stripe 流程处理。 | Simulasi: pelanggan memilih keputusan di halaman pembayaran demo kedai sendiri. Mod ujian Stripe: pelanggan membayar di halaman pembayaran ujian Stripe dengan kad ujian atau kaedah ujian lain yang ditawarkan di sana (seperti FPX atau GrabPay), dan tiada wang sebenar berpindah; bagi pesanan itu, meluluskan bayaran balik juga menghantar permintaan bayaran balik ke mod ujian Stripe, dan statusnya dipaparkan dalam butiran bayaran balik. Pesanan di bawah jumlah minimum Stripe masih menggunakan pembayaran simulasi. Sesi pembayaran Stripe yang sudah dimulakan terus dikendalikan melalui Stripe walaupun anda menukar semula. | A09；DESIGN 1.12「Stripe 测试环境支付」第 2、8 条（0.12） |
| `admin.stripe_not_configured` | Stripe test mode can't be selected: its test secret key or webhook signing secret is missing or invalid. Only test keys are accepted, never live keys. | 无法选择 Stripe 测试环境：测试密钥或 webhook 签名密钥未配置或无效。只接受测试密钥，正式环境密钥一律不接受。 | Mod ujian Stripe tidak boleh dipilih: kunci rahsia ujian atau rahsia tandatangan webhook tiada atau tidak sah. Hanya kunci ujian diterima, bukan kunci langsung. | A09 选项禁用或保存被拒；DESIGN 1.12 第 1、2 条（0.12） |
| `admin.paid_via_stripe` | Paid with Stripe test mode | 经 Stripe 测试环境付款 | Dibayar dengan mod ujian Stripe | A03（0.12） |
| `admin.stripe_method` | Payment method type: {type} | 付款方式类型：{type} | Jenis kaedah pembayaran: {type} | A03；{type} 为 Stripe 记下的实际类型原文，如 card、fpx、grabpay（0.12） |
| `admin.stripe_method_unknown` | Payment method type: not known yet | 付款方式类型：尚未取得 | Jenis kaedah pembayaran: belum diketahui | A03；类型为空时（0.12） |
| `admin.stripe_deadline_fpx` | Stripe allows FPX refunds within 60 days of payment. | Stripe 允许 FPX 付款在付款后 60 天内退款。 | Stripe membenarkan bayaran balik FPX dalam tempoh 60 hari selepas pembayaran. | A03；天数以实现前核验为准；DESIGN 1.12 第 8 条（0.12） |
| `admin.stripe_deadline_grabpay` | Stripe allows GrabPay refunds within 90 days of payment. | Stripe 允许 GrabPay 付款在付款后 90 天内退款。 | Stripe membenarkan bayaran balik GrabPay dalam tempoh 90 hari selepas pembayaran. | A03；天数以实现前核验为准；DESIGN 1.12 第 8 条（0.12） |
| `admin.stripe_deadline_other` | The refund time limit for this payment method is set by Stripe. | 此付款方式的退款期限以 Stripe 为准。 | Had masa bayaran balik untuk kaedah pembayaran ini ditetapkan oleh Stripe. | A03；其他类型或类型为空（0.12） |
| `admin.stripe_refund_hint` | Approving also sends a refund request for the cash amount above to Stripe's test mode (nothing is sent if it is RM 0.00); after approval, its status is shown on this page. No real money moves. If Stripe doesn't complete the refund, the approval still stands. | 批准时也会就上面的现金金额向 Stripe 测试环境发出退款请求（为 RM 0.00 时不发送），批准后在本页查看其状态。不会有真实资金流动。Stripe 未完成退款时，批准依然有效。 | Kelulusan juga menghantar permintaan bayaran balik bagi amaun tunai di atas ke mod ujian Stripe (tiada apa dihantar jika RM 0.00); selepas kelulusan, statusnya dipaparkan di halaman ini. Tiada wang sebenar berpindah. Jika Stripe tidak melengkapkan bayaran balik, kelulusan tetap berkuat kuasa. | ◆ A03 审核中，经 Stripe 付款的订单；DESIGN 1.12 第 8 条（0.12） |
| `admin.stripe_refund_title` | Stripe test refunds | Stripe 测试环境退款 | Bayaran balik ujian Stripe | A03 已批准（0.12） |
| `admin.col_stripe_refund_id` | Stripe refund ID | Stripe 退款 ID | ID bayaran balik Stripe | A03 列头；尚无 ID 时显示 —（0.12） |
| `admin.stripe_refund_pending` | Processing | 处理中 | Sedang diproses | A03 尝试状态：Stripe 的 pending、requires_action（0.12） |
| `admin.stripe_refund_succeeded` | Refunded in Stripe | 已在 Stripe 退回 | Dikembalikan di Stripe | A03 尝试状态：succeeded（0.12） |
| `admin.stripe_refund_failed` | Failed | 失败 | Gagal | A03 尝试状态：failed、canceled，或 Stripe 明确拒绝创建（0.12） |
| `admin.stripe_refund_pending_hint` | Waiting for Stripe's result. Open this page again later to see the update. If it stays processing for a long time, check it in the Stripe dashboard. | 正在等待 Stripe 的结果，请稍后重新打开本页查看。若长时间仍在处理中，请到 Stripe 后台查看。 | Menunggu keputusan Stripe. Buka halaman ini semula kemudian untuk melihat kemas kini. Jika ia kekal diproses untuk masa yang lama, semak di papan pemuka Stripe. | A03 最新一次处理中（0.12） |
| `admin.stripe_refund_failed_hint` | Stripe didn't complete this refund. Check the time limit above and the Stripe dashboard, then retry. Retrying sends a new refund request. | Stripe 未完成这笔退款。请先核对上面的期限与 Stripe 后台，再重试；重试会发出一笔新的退款请求。 | Stripe tidak melengkapkan bayaran balik ini. Semak had masa di atas dan papan pemuka Stripe, kemudian cuba semula. Cubaan semula menghantar permintaan bayaran balik baharu. | A03 最新一次失败（0.12） |
| `admin.stripe_refund_retry` | Retry Stripe refund | 重试 Stripe 退款 | Cuba semula bayaran balik Stripe | A03 最新一次失败时（0.12） |
| `admin.stripe_refund_none` | Nothing to refund in Stripe: the approved items were fully paid with coupons or points. | 无需在 Stripe 退款：所退商品已全部由优惠券或积分抵扣。 | Tiada apa untuk dikembalikan di Stripe: item yang diluluskan dibayar sepenuhnya dengan kupon atau mata. | A03 已批准且现金退款额为 0（0.12） |
| `admin.stripe_refund_not_sent` | This refund couldn't be sent to Stripe. An operator alert has been logged; check this payment in the Stripe dashboard. | 这笔退款未能发送到 Stripe，已记录运营告警；请到 Stripe 后台核对这笔付款。 | Bayaran balik ini tidak dapat dihantar ke Stripe. Amaran pengendali telah direkodkan; semak pembayaran ini di papan pemuka Stripe. | A03 已批准、现金退款额大于 0 却没有 Stripe 退款尝试：合计会超过付款金额或缺 PaymentIntent ID；DESIGN 1.12 第 8 条（0.13）；0.20 改为不承诺送达（DESIGN 1.21「运营告警邮件」第 7 条） |
| `admin.stripe_refund_blocked` | This refund can't be sent to Stripe because it would exceed the amount paid. An operator alert has been logged. | 这笔退款会超过付款金额，无法发送到 Stripe，已记录运营告警。 | Bayaran balik ini tidak boleh dihantar ke Stripe kerana ia akan melebihi amaun yang dibayar. Amaran pengendali telah direkodkan. | A03 重试被拒：同一付款的退款合计会超过付款金额；DESIGN 1.12 第 8 条（0.12）；0.20 改为不承诺送达（DESIGN 1.21「运营告警邮件」第 7 条） |
| `admin.stripe_pending_note` | Stripe test-mode records that need your decision. Alert emails name the checkout record ID for the first list and, for failed refunds, the refund attempt ID for the second list. Nothing here moves real money. | 需要你决定的 Stripe 测试环境记录。第一个列表对照告警邮件里的结账记录 ID；第二个列表对照退款失败告警邮件里的退款尝试 ID。这里的操作不涉及真实资金。 | Rekod mod ujian Stripe yang memerlukan keputusan anda. E-mel amaran menyebut ID rekod pembayaran untuk senarai pertama dan, bagi bayaran balik yang gagal, ID cubaan bayaran balik untuk senarai kedua. Tiada wang sebenar terlibat di sini. | A11 ★（0.21） |
| `admin.stripe_pending_checkouts_title` | Checkout records without a result | 没有结果的结账记录 | Rekod pembayaran tanpa keputusan | A11 第一个列表的标题（0.21） |
| `admin.stripe_pending_refunds_title` | Failed refunds of late payments | 失败的迟到付款退款 | Bayaran balik gagal bagi bayaran lewat | A11 第二个列表的标题（0.21） |
| `admin.stripe_pending_refunds_note` | Payments that arrived after an order was cancelled or paid another way, or that didn't match the order, need a full refund. The refunds below failed in Stripe. Check them in the Stripe dashboard, then retry. Retrying sends a new refund request. | 订单已取消或已由其他途径付款之后才到的付款，以及与订单不符的付款，需要全额退回。下面列出在 Stripe 失败的退款；请先在 Stripe 后台核对，再重试，重试会发出一笔新的退款请求。 | Bayaran yang tiba selepas pesanan dibatalkan atau dibayar dengan cara lain, atau yang tidak sepadan dengan pesanan, perlu dikembalikan sepenuhnya. Bayaran balik di bawah gagal di Stripe. Semak di papan pemuka Stripe, kemudian cuba semula. Cubaan semula menghantar permintaan bayaran balik baharu. | A11 第二个列表标题下（0.21） |
| `admin.stripe_pending_none` | Nothing to handle. | 没有待处理的记录。 | Tiada apa-apa untuk diuruskan. | A11 列表为空时，两个列表各自显示（0.21） |
| `admin.col_checkout_record` | Checkout record | 结账记录 | Rekod pembayaran | A11 列头；值为 # 加记录 ID（0.21） |
| `admin.col_refund_attempt` | Refund attempt | 退款尝试 | Cubaan bayaran balik | A11 列头；值为 # 加尝试 ID（0.21） |
| `admin.col_created` | Created | 创建时间 | Dicipta | A11 列头（0.21） |
| `admin.col_alerted` | Alerted | 告警时间 | Diberi amaran | A11 列头（0.21） |
| `admin.col_attempted` | Attempted | 尝试时间 | Dicuba | A11 列头；最新一次尝试的创建时间（0.21） |
| `admin.col_error_type` | Error type | 错误类别 | Jenis ralat | A11 列头；值为 Stripe 的类别码原文，为空时 —（0.21） |
| `admin.checkout_status_creating` | Creating — result unknown | 创建中（结果不明） | Sedang dicipta — keputusan tidak diketahui | A11 记录状态 creating（0.21） |
| `admin.checkout_status_open` | Open | 进行中 | Terbuka | A11 记录状态 open（0.21） |
| `admin.checkout_status_complete` | Completed | 已完成 | Selesai | A11 记录状态 complete（0.21） |
| `admin.stripe_void` | Void and cancel order | 作废并取消订单 | Batalkan rekod dan pesanan | A11 每行的按钮，打开确认框（0.21） |
| `admin.stripe_void_confirm_title` | Void checkout record #{id} and cancel order {order}? | 作废结账记录 #{id} 并取消订单 {order}？ | Batalkan rekod pembayaran #{id} dan pesanan {order}? | A11 确认框标题；`{id}` 为记录 ID，`{order}` 为订单号（0.21） |
| `admin.stripe_void_confirm_body` | Stripe is asked first, and a clear answer is followed: a paid order is confirmed, not cancelled. The record is voided and the order cancelled on your check only when Stripe can't give a result, when a payment doesn't match the order and can't be refunded automatically, or when the checkout was never confirmed as created. Stock, coupons and points are then released. If the customer still pays later, the store tries to refund it in full: if a refund can't be started, an operator alert is logged; refunds that fail in Stripe are listed on this page. | 会先向 Stripe 查询，给出明确结果就照它处理：已付款的订单会被确认，不会取消。只有 Stripe 给不出结果、付款与订单不符而无法自动退款，或结账从未确认建成时，才凭你的核对作废记录并取消订单，并释放库存、优惠券与积分。之后若顾客仍付了款，系统会尝试全额退款：无法发起时会记下运营告警；在 Stripe 失败的退款会列在本页。 | Stripe ditanya dahulu dan jawapan yang jelas diikut: pesanan yang sudah dibayar disahkan, bukan dibatalkan. Rekod dan pesanan dibatalkan berdasarkan semakan anda hanya apabila Stripe tidak dapat memberi keputusan, bayaran tidak sepadan dengan pesanan dan tidak boleh dikembalikan secara automatik, atau pembayaran tidak pernah disahkan telah dicipta. Stok, kupon dan mata kemudian dilepaskan. Jika pelanggan masih membayar kemudian, kedai akan cuba membayar balik sepenuhnya: jika bayaran balik tidak dapat dimulakan, amaran operator direkodkan; bayaran balik yang gagal di Stripe disenaraikan di halaman ini. | A11 确认框（0.21） |
| `admin.stripe_void_verified` | I've checked this order's session and payment in the Stripe test dashboard. | 我已在 Stripe 测试后台核对过这笔订单的会话与付款。 | Saya telah menyemak sesi dan bayaran pesanan ini dalam papan pemuka ujian Stripe. | A11 确认框的勾选框；未勾选时提交按钮禁用（0.21） |
| `admin.stripe_void_submit` | Void and cancel | 作废并取消 | Batalkan | A11 确认框的提交按钮（0.21） |
| `admin.stripe_void_confirmed` | Stripe shows this order as paid. It has been confirmed and was not cancelled. | Stripe 显示这笔订单已付款，订单已确认，未取消。 | Stripe menunjukkan pesanan ini telah dibayar. Ia telah disahkan dan tidak dibatalkan. | A11 作废结果：confirmed（0.21） |
| `admin.stripe_void_cancelled` | The order has been cancelled based on Stripe's answer. If a payment that didn't match the order was found, a full refund has been requested for it. | 已按 Stripe 的结果取消订单。若查到与订单不符的付款，已为它发起全额退款。 | Pesanan telah dibatalkan berdasarkan jawapan Stripe. Jika bayaran yang tidak sepadan dengan pesanan ditemui, bayaran balik penuh telah diminta untuknya. | A11 作废结果：cancelled（0.21） |
| `admin.stripe_void_voided` | The record has been voided and the order cancelled. | 记录已作废，订单已取消。 | Rekod dan pesanan telah dibatalkan. | A11 作废结果：voided（0.21） |
| `admin.stripe_void_unavailable` | Stripe isn't available right now, and this record already shows a payment, so it can't be voided. Try again later. | Stripe 暂不可用，而这条记录已显示付款，不能作废。请稍后再试。 | Stripe tidak tersedia buat masa ini dan rekod ini sudah menunjukkan bayaran, jadi ia tidak boleh dibatalkan. Cuba lagi kemudian. | A11 作废结果：unavailable（0.21） |
| `admin.stripe_pending_changed` | This record has changed or no longer needs handling. The list has been reloaded. | 这条记录已有变化或已无需处理，列表已重新读取。 | Rekod ini telah berubah atau tidak lagi perlu diuruskan. Senarai telah dimuat semula. | A11 作废或重试时记录已变化或不存在（404）（0.21） |
| `admin.stripe_late_refund_retried` | A new refund attempt has been created and will be sent to Stripe. Check its result in the Stripe dashboard. | 已建立新的退款尝试，会发送到 Stripe；结果请到 Stripe 后台核对。 | Cubaan bayaran balik baharu telah dibuat dan akan dihantar ke Stripe. Semak keputusannya di papan pemuka Stripe. | A11 重试成功（0.21） |

## 11. 节日促销活动（P01–P05、P09、P15、A02、A08、A12；0.22）

前台：

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `promo.nav` | Sale | 活动 | Jualan | 页头导航，只在有活动生效时显示，排在 `common.nav_shop` 之后 |
| `promo.a11y_original` | Original price | 原价 | Harga asal | 划线原价的读屏标签 |
| `promo.a11y_sale` | Sale price | 活动价 | Harga jualan | 活动价的读屏标签 |
| `promo.badge_percent` | −{percent}% | −{percent}% | −{percent}% | 打折（百分比）的角标 |
| `promo.badge_amount` | RM {amount} off | 立减 RM {amount} | Jimat RM {amount} | 打折（每件直降）的角标 |
| `promo.badge_bxgy_free` | Buy {x} get {y} free | 买 {x} 送 {y} | Beli {x} percuma {y} | 买 X 享 Y，享受的件 100% 时的角标 |
| `promo.badge_bxgy_pct` | Buy {x}, {y} more {percent}% off | 买 {x} 件，再 {y} 件减 {percent}% | Beli {x}, {y} lagi diskaun {percent}% | 买 X 享 Y，享受的件不足 100% 时的角标 |
| `promo.ends_in` | Ends in {time} | 距结束还有 {time} | Tamat dalam {time} | 倒计时；`{time}` 用 `promo.countdown_days` 或 `promo.countdown` |
| `promo.countdown_days` | {d}d {hh}:{mm}:{ss} | {d} 天 {hh}:{mm}:{ss} | {d} hari {hh}:{mm}:{ss} | 剩余 1 天以上 |
| `promo.countdown` | {hh}:{mm}:{ss} | {hh}:{mm}:{ss} | {hh}:{mm}:{ss} | 剩余不足 1 天 |
| `promo.shop_now` | Shop the sale | 去逛活动 | Beli-belah jualan | P01 模板海报按钮 → P15 |
| `promo.view_all` | See all sale items | 查看全部活动商品 | Lihat semua item jualan | P02 横条与 P03 规则区 → P15 |
| `promo.detail_title` | Sale offers | 活动优惠 | Tawaran jualan | P03 活动规则区标题，其后显示活动标题 |
| `promo.rule_item_percent` | {percent}% off | 立减 {percent}% | Diskaun {percent}% | 打折规则说明（百分比） |
| `promo.rule_item_amount` | RM {amount} off each item | 每件立减 RM {amount} | Jimat RM {amount} setiap item | 打折规则说明（每件直降） |
| `promo.rule_bxgy_free` | Buy {x}, get {y} free — the lowest-priced items are free | 买 {x} 送 {y}，送价格最低的件 | Beli {x}, percuma {y} — item paling murah adalah percuma | 买 X 享 Y 规则说明（100%） |
| `promo.rule_bxgy_pct` | Buy {x}, get {y} more at {percent}% off — the discount goes to the lowest-priced items | 买 {x} 件，再 {y} 件减 {percent}%，优惠给价格最低的件 | Beli {x}, {y} lagi diskaun {percent}% — diskaun untuk item paling murah | 买 X 享 Y 规则说明（不足 100%） |
| `promo.rule_tier_amount` | Spend RM {threshold}, save RM {amount} | 满 RM {threshold} 减 RM {amount} | Belanja RM {threshold}, jimat RM {amount} | 满额减每一档一条；门槛按商品级优惠后的金额判断 |
| `promo.rule_tier_percent` | Spend RM {threshold}, get {percent}% off | 满 RM {threshold} 减 {percent}% | Belanja RM {threshold}, diskaun {percent}% | 满额折每一档一条 |
| `promo.rule_free_shipping` | Free sample shipping on orders from RM {threshold} | 满 RM {threshold} 免示例运费 | Penghantaran contoh percuma untuk pesanan dari RM {threshold} | 免运费规则说明 |
| `promo.rule_free_shipping_all` | Free sample shipping on all orders | 全部订单免示例运费 | Penghantaran contoh percuma untuk semua pesanan | 免运费门槛为 0 时 |
| `promo.rule_scope_with_items` | Applies to: {names}, and the items below | 适用：{names}，以及下方列出的商品 | Untuk: {names}, dan item di bawah | P15 规则同时含分类与单件商品时 |
| `promo.rule_scope` | Applies to: {names} | 适用：{names} | Untuk: {names} | P15 商品级规则含分类时附在说明后；`{names}` 为分类名称，以当前语言的顿号或逗号分隔 |
| `promo.coupon_not_combinable` | This sale can't be combined with coupons. | 本活动不与优惠券同用。 | Jualan ini tidak boleh digabung dengan kupon. | 活动关闭了与优惠券同用时 |
| `promo.points_not_combinable` | This sale can't be combined with points. | 本活动不与积分同用。 | Jualan ini tidak boleh digabung dengan mata. | 活动关闭了与积分同用时 |
| `promo.demo_hint` | This is a demo sale. Prices and discounts are examples; no real money is charged. | 这是演示活动，价格与优惠都是示例，不会真实扣款。 | Ini jualan demo. Harga dan diskaun hanyalah contoh; tiada wang sebenar dicaj. | ★ P15 |
| `promo.rules_title` | Offers | 活动规则 | Tawaran | P15 |
| `promo.products_title` | Sale items | 活动商品 | Item jualan | P15 |
| `promo.order_level_only` | This sale applies to your whole order, so every product counts. | 本活动按整单计算，全部商品都适用。 | Jualan ini dikira untuk keseluruhan pesanan, jadi semua produk layak. | P15 只有整单级规则时 |
| `promo.browse_all` | Browse all products | 浏览全部商品 | Lihat semua produk | P15 → P02 |
| `promo.ended` | There's no sale running right now. | 目前没有进行中的活动。 | Tiada jualan sedang berjalan sekarang. | P15 没有生效活动时 |
| `promo.preview_bar` | Preview — only you can see this sale here. Checkout is turned off. | 预览：只有你看得到这个活动，预览中不能结账。 | Pratonton — hanya anda boleh melihat jualan ini di sini. Pembayaran dimatikan. | A12 前台预览顶部常驻 |
| `promo.preview_exit` | Close preview | 关闭预览 | Tutup pratonton | A12 前台预览 |
| `promo.preview_no_checkout` | Checkout is off in preview. | 预览中不能结账。 | Pembayaran dimatikan dalam pratonton. | A12 前台预览的 P04 |
| `cart.promo_discount` | Sale discount (estimate) | 活动优惠（暂算） | Diskaun jualan (anggaran) | P04、P05 选定收货国家前；为 0 时不显示 |
| `cart.after_promo` | After sale discount (estimate) | 活动后金额（暂算） | Selepas diskaun jualan (anggaran) | P04；P05 选定收货国家前的手机折叠摘要与底栏；活动优惠为 0 时不显示 |
| `cart.hint_tier_amount` | Add RM {amount} more to save RM {save}. | 再买 RM {amount}，可减 RM {save}。 | Tambah RM {amount} lagi untuk jimat RM {save}. | P04 凑单提示（满额减下一档） |
| `cart.hint_tier_percent` | Add RM {amount} more to get {percent}% off. | 再买 RM {amount}，可减 {percent}%。 | Tambah RM {amount} lagi untuk diskaun {percent}%. | P04 凑单提示（满额折下一档） |
| `cart.hint_free_shipping` | Add RM {amount} more for free sample shipping. | 再买 RM {amount}，免示例运费。 | Tambah RM {amount} lagi untuk penghantaran contoh percuma. | P04 凑单提示（免运费） |
| `cart.free_shipping_reached` | You've unlocked free sample shipping. | 已达免示例运费门槛。 | Anda layak untuk penghantaran contoh percuma. | P04 已达免运费门槛 |
| `cart.hint_bxgy` | {rule}: add {n} more to get {y} more at the offer price. | {rule}：再加 {n} 件，可多享 {y} 件优惠。 | {rule}: tambah {n} lagi untuk {y} lagi item pada harga tawaran. | P04；`{rule}` 为该规则的角标文字 |
| `cart.member_scheme_note` | Members: if you use a coupon or points at checkout, we'll work out the order both with and without the sale and use whichever is better for you. | 会员：结账时若使用优惠券或积分，系统会分别按参加与不参加活动计算，取对你更划算的一种。 | Ahli: jika anda guna kupon atau mata semasa pembayaran, kami akan mengira pesanan dengan dan tanpa jualan dan menggunakan yang lebih jimat untuk anda. | P04 已登录会员且有活动优惠时 |
| `checkout.summary_promo` | Sale discount | 活动优惠 | Diskaun jualan | P05、P09、A02 金额明细；游客与会员都显示 |
| `checkout.shipping_free` | Free (was RM {amount}) | 免运费（原 RM {amount}） | Percuma (asal RM {amount}) | P05、P09、A02 运费行；活动免运费时 |
| `checkout.scheme_promo` | We've applied the sale. Without it, this order would be RM {other} before points — RM {diff} more. | 已按参加活动计算。不参加活动时本单积分抵扣前为 RM {other}，多 RM {diff}。 | Kami gunakan jualan. Tanpanya, pesanan ini RM {other} sebelum mata — RM {diff} lebih. | P05 会员用券或积分、取参加活动且两者不同时；`{diff}` 为差额 |
| `checkout.scheme_promo_equal` | With or without the sale, this order comes to the same amount before points, so we've applied the sale. | 参加与不参加活动，本单积分抵扣前的金额相同，已按参加活动计算。 | Dengan atau tanpa jualan, jumlah pesanan ini sama sebelum mata, jadi kami gunakan jualan. | P05 两种方案相同时 |
| `checkout.scheme_no_promo` | We've left out the sale and used what you chose, because that's better value for you. With the sale, this order would be RM {other} before points — RM {diff} more. | 已按不参加活动、使用你所选的优惠计算，对你更划算。参加活动时本单积分抵扣前为 RM {other}，多 RM {diff}。 | Kami tidak gunakan jualan dan gunakan pilihan anda kerana itu lebih jimat. Dengan jualan, pesanan ini RM {other} sebelum mata — RM {diff} lebih. | P05 会员用券或积分、取不参加活动时（此时差额必大于 0）；不预设券与积分都已使用 |
| `checkout.coupon_not_combinable` | This sale can't be combined with coupons, so your coupon wasn't used. It's still available. | 本活动不与优惠券同用，本单未使用该券，券仍可再用。 | Jualan ini tidak boleh digabung dengan kupon, jadi kupon anda tidak digunakan. Ia masih boleh digunakan. | P05，取参加活动时（与 `checkout.scheme_promo` 或 `checkout.scheme_promo_equal` 同时显示） |
| `checkout.points_not_combinable` | This sale can't be combined with points, so no points were used. Your points stay in your account. | 本活动不与积分同用，本单未使用积分，积分仍保留在你的账户。 | Jualan ini tidak boleh digabung dengan mata, jadi tiada mata digunakan. Mata anda kekal dalam akaun. | P05，取参加活动时（与 `checkout.scheme_promo` 或 `checkout.scheme_promo_equal` 同时显示） |
| `checkout.coupon_not_applied_sale` | After the sale discount, this order is below the coupon's minimum spend, so the coupon wasn't used. | 活动优惠后本单未达到优惠券的最低消费，未使用该券。 | Selepas diskaun jualan, pesanan ini di bawah perbelanjaan minimum kupon, jadi kupon tidak digunakan. | P05，取参加活动时（与 `checkout.scheme_promo` 或 `checkout.scheme_promo_equal` 同时显示） |

后台：

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `admin.promo_demo_note` | Sale prices and discounts are demo amounts; no real money moves. | 活动价格与优惠都是演示金额，不发生真实资金流动。 | Harga dan diskaun jualan ialah jumlah demo; tiada wang sebenar berpindah. | ★ A12 |
| `admin.promo_name` | Internal name | 内部名称 | Nama dalaman | 只在后台显示，1–100 个字符 |
| `admin.promo_time` | Time (Malaysia) | 时间（马来西亚） | Masa (Malaysia) | A12 列表列头 |
| `admin.promo_rule_count` | Rules | 规则数 | Peraturan | A12 列表列头 |
| `admin.promo_draft` | Draft | 草稿 | Draf | 状态值 |
| `admin.promo_scheduled` | Scheduled | 未开始 | Dijadualkan | 状态值：已发布、未到开始时间 |
| `admin.promo_running` | Running | 进行中 | Sedang berjalan | 状态值 |
| `admin.promo_ended` | Ended | 已结束 | Tamat | 状态值 |
| `admin.promo_open` | Open | 打开 | Buka | A12 列表 |
| `admin.promo_empty` | No promotions yet. | 还没有促销活动。 | Belum ada promosi. | A12 列表为空 |
| `admin.promo_back` | All promotions | 全部活动 | Semua promosi | 编辑区回到列表 |
| `admin.promo_publish` | Publish | 发布 | Terbitkan | 草稿 |
| `admin.promo_withdraw` | Back to draft | 撤回为草稿 | Kembali ke draf | 已发布未开始 |
| `admin.promo_end_now` | End now | 提前结束 | Tamatkan sekarang | 进行中 |
| `admin.promo_copy` | Copy as new draft | 复制为新草稿 | Salin sebagai draf baharu | 任何状态 |
| `admin.promo_delete` | Delete draft | 删除草稿 | Padam draf | 草稿 |
| `admin.promo_preview` | Preview on the storefront | 在前台预览 | Pratonton di kedai | 新标签页打开前台预览 |
| `admin.promo_more` | More | 更多 | Lagi | 手机操作栏 |
| `admin.promo_saved` | Saved. | 已保存。 | Disimpan. | 保存、发布、撤回、提前结束成功 |
| `admin.promo_changed` | This promotion was changed elsewhere. Reload to see the latest version; your edits here weren't saved. | 这个活动已在别处被修改。请重新读取最新版本；本页的修改未保存。 | Promosi ini telah diubah di tempat lain. Muat semula untuk melihat versi terkini; suntingan anda di sini tidak disimpan. | 版本不符，配 `admin.reload` |
| `admin.promo_field_invalid` | This value isn't allowed. See the note under this field. | 这个值不合规定，请看本栏下方的说明。 | Nilai ini tidak dibenarkan. Lihat nota di bawah ruangan ini. | A12 提交前逐栏检查不合格时 |
| `admin.promo_invalid_other` | Some values weren't accepted. Check the dates, numbers and text lengths, then try again. | 有取值未被接受，请检查日期、数字与文字长度后重试。 | Sesetengah nilai tidak diterima. Semak tarikh, nombor dan panjang teks, kemudian cuba lagi. | A12 服务端拒绝而原因不是重叠或发布条件时 |
| `admin.poster_save_first` | Save this promotion first, then upload images. | 请先保存这个活动，再上传图片。 | Simpan promosi ini dahulu, kemudian muat naik imej. | A12 新建未保存时上传按钮禁用 |
| `admin.promo_invalid` | Not saved. Please fix: | 未保存，请修正： | Tidak disimpan. Sila betulkan: | 其下逐条列出问题 |
| `admin.promo_overlap` | The time overlaps another published promotion: {name}. | 时间与另一个已发布的活动重叠：{name}。 | Masa bertindih dengan promosi lain yang diterbitkan: {name}. | `{name}` 为对方的内部名称 |
| `admin.promo_scope_overlap` | {product} is in more than one item rule. | {product} 同时属于多条商品级规则。 | {product} berada dalam lebih daripada satu peraturan item. | 商品级规则重叠 |
| `admin.promo_publish_missing` | To publish, add at least one rule, an English title, a start time (it may already have passed) and an end time that's still ahead; with an image poster, also both images and English alt text. | 发布需要至少一条规则、英文标题、开始时间与晚于现在的结束时间；海报为上传图时还需两张图与英文替代文字。 | Untuk menerbitkan, tambah sekurang-kurangnya satu peraturan, tajuk Inggeris, masa mula (boleh sudah berlalu) dan masa tamat yang belum berlalu; untuk poster imej, tambah juga kedua-dua imej dan teks alternatif Inggeris. | 发布条件不满足 |
| `admin.promo_end_confirm` | End this promotion now? It can't be restarted. | 现在结束这个活动？结束后不能恢复。 | Tamatkan promosi ini sekarang? Ia tidak boleh dimulakan semula. | 确认框 |
| `admin.promo_withdraw_confirm` | Move this promotion back to draft? It won't start until you publish it again. | 把这个活动撤回为草稿？重新发布之前不会开始。 | Kembalikan promosi ini ke draf? Ia tidak akan bermula sehingga anda menerbitkannya semula. | 确认框 |
| `admin.promo_delete_confirm` | Delete this draft? This can't be undone. | 删除这个草稿？删除后无法恢复。 | Padam draf ini? Ini tidak boleh dibuat asal. | 确认框 |
| `admin.promo_copied` | Copied. This is the new draft; set its time before publishing. | 已复制。这是新草稿，发布前请设定时间。 | Disalin. Ini draf baharu; tetapkan masanya sebelum diterbitkan. | 复制成功 |
| `admin.promo_ended_readonly` | This promotion has ended. It can only be copied. | 这个活动已结束，只能复制。 | Promosi ini telah tamat. Ia hanya boleh disalin. | 已结束 |
| `admin.promo_save_first` | Save your changes first. Preview, publish and the other actions use the saved version. | 请先保存。预览、发布等操作都按已保存的内容进行。 | Simpan perubahan dahulu. Pratonton, terbitan dan tindakan lain menggunakan versi yang disimpan. | 有未保存修改时点预览 |
| `admin.promo_basic` | Basics | 基本信息 | Asas | 区块标题 |
| `admin.promo_start` | Starts | 开始 | Mula | — |
| `admin.promo_end` | Ends | 结束 | Tamat | — |
| `admin.promo_time_note` | Malaysia time (UTC+8), to the minute. It must end after it starts, and last up to 92 days. | 马来西亚时间（UTC+8），精确到分钟；结束须晚于开始，最长 92 天。 | Masa Malaysia (UTC+8), hingga minit. Ia mesti tamat selepas bermula, dan berlangsung sehingga 92 hari. | — |
| `admin.promo_rules_locked` | This promotion has started, so its time, rules and combining options can't be changed. You can still edit its text and decoration. | 活动已开始，时间、规则与同用开关不能再改；文案与装饰仍可修改。 | Promosi ini telah bermula, jadi masa, peraturan dan pilihan gabungannya tidak boleh diubah. Anda masih boleh menyunting teks dan hiasannya. | 进行中 |
| `admin.promo_rules` | Rules | 规则 | Peraturan | 区块标题 |
| `admin.rule_add` | Add rule | 添加规则 | Tambah peraturan | 下拉选类别 |
| `admin.rule_remove` | Remove | 移除 | Buang | — |
| `admin.rule_move_up` | Move up | 上移 | Naikkan | 读屏标签 |
| `admin.rule_move_down` | Move down | 下移 | Turunkan | 读屏标签 |
| `admin.rule_item_discount` | Discount on items | 商品打折 | Diskaun item | 规则类别 |
| `admin.rule_bxgy` | Buy X get Y | 买 X 享 Y | Beli X dapat Y | 规则类别 |
| `admin.rule_tiers` | Spend and save | 满额减或满额折 | Belanja dan jimat | 规则类别 |
| `admin.rule_free_shipping` | Free shipping | 满额免运费 | Penghantaran percuma | 规则类别 |
| `admin.rule_scope_hint` | Up to 200 products and 30 categories. A product can be in only one item rule. | 最多 200 件商品与 30 个分类；一件商品只能属于一条商品级规则。 | Sehingga 200 produk dan 30 kategori. Satu produk hanya boleh berada dalam satu peraturan item. | A12 商品级规则的范围下方 |
| `admin.rule_item_hint` | Percent off: 1–90%. Amount off: RM0.01 to RM500,000.00 per item (never more than the item's price). | 百分比 1–90%；每件直降 RM0.01 至 RM500,000.00（不超过该件单价）。 | Peratus diskaun: 1–90%. Jumlah diskaun: RM0.01 hingga RM500,000.00 setiap item (tidak melebihi harga item). | A12 商品打折下方 |
| `admin.rule_xy_hint` | Buy and Get: 1–10 each. | 买、享各 1–10 件。 | Beli dan Dapat: 1–10 setiap satu. | A12 买 X 享 Y 下方 |
| `admin.rule_tiers_hint` | Up to 5 tiers. Each tier needs a higher spend and a bigger discount than the one before; an amount off must be less than its spend, and a percent off is 1–90%. Spend: RM0.01 to RM999,999.99. | 最多 5 档；每一档的门槛与优惠都须高于上一档；减金额须小于该档门槛，减百分比为 1–90%；门槛 RM0.01 至 RM999,999.99。 | Sehingga 5 peringkat. Setiap peringkat perlu perbelanjaan dan diskaun yang lebih tinggi daripada sebelumnya; jumlah diskaun mesti kurang daripada perbelanjaannya, dan peratus diskaun 1–90%. Perbelanjaan: RM0.01 hingga RM999,999.99. | A12 满额减或满额折下方 |
| `admin.rule_scope` | Applies to | 适用范围 | Untuk | — |
| `admin.rule_categories` | Categories | 分类 | Kategori | — |
| `admin.rule_products` | Products | 商品 | Produk | — |
| `admin.rule_percent` | Percent off | 按百分比 | Peratus diskaun | 1–90 |
| `admin.rule_amount` | Amount off each item | 每件直降 | Jumlah diskaun setiap item | RM |
| `admin.rule_buy` | Buy | 买 | Beli | X，1–10 |
| `admin.rule_get` | Get | 享 | Dapat | Y，1–10 |
| `admin.rule_get_percent` | Discount on those | 这几件的折扣 | Diskaun untuk item itu | 1–100 |
| `admin.rule_get_percent_hint` | 1–100%. 100% means free; 50% means half price. The discount goes to the lowest-priced items in this rule. | 1–100%。100% 即送，50% 即半价；优惠给本规则范围内价格最低的件。 | 1–100%. 100% bermaksud percuma; 50% bermaksud separuh harga. Diskaun diberi kepada item paling murah dalam peraturan ini. | — |
| `admin.rule_tier_amount` | Amount off | 减金额 | Jumlah diskaun | 满额减方式 |
| `admin.rule_tier_percent` | Percent off | 减百分比 | Peratus diskaun | 满额折方式 |
| `admin.rule_tier_threshold` | Spend at least | 满 | Belanja sekurang-kurangnya | 每档门槛 |
| `admin.rule_tier_discount` | Discount | 优惠 | Diskaun | 每档优惠 |
| `admin.rule_add_tier` | Add tier | 添加一档 | Tambah peringkat | 最多 5 档 |
| `admin.rule_remove_tier` | Remove tier | 移除这一档 | Buang peringkat | — |
| `admin.rule_threshold` | Minimum spend | 门槛 | Perbelanjaan minimum | 免运费 |
| `admin.rule_threshold_hint` | RM0.00 to RM999,999.99; 0 means every order ships free. Thresholds use the amount after item discounts. | RM0.00 至 RM999,999.99，填 0 即全部订单免运费；门槛按商品打折后的金额判断。 | RM0.00 hingga RM999,999.99; 0 bermaksud semua pesanan dihantar percuma. Had dikira selepas diskaun item. | — |
| `admin.promo_combine` | Combining with coupons and points | 与优惠券、积分同用 | Gabungan dengan kupon dan mata | 区块标题 |
| `admin.promo_allow_coupon` | Can be combined with coupons | 可与优惠券同用 | Boleh digabung dengan kupon | 默认不勾选 |
| `admin.promo_allow_points` | Can be combined with points | 可与积分同用 | Boleh digabung dengan mata | 默认不勾选 |
| `admin.promo_combine_hint` | When a member uses a coupon or points, the system compares the order with and without the sale and uses whichever is better for them. Unticked options can't be used together with the sale. | 会员使用优惠券或积分时，系统会比较参加与不参加活动两种算法，取对会员更划算的一种；未勾选的一项不能与活动同时使用。 | Apabila ahli menggunakan kupon atau mata, sistem membandingkan pesanan dengan dan tanpa jualan dan menggunakan yang lebih jimat untuk mereka. Pilihan yang tidak ditanda tidak boleh digunakan bersama jualan. | — |
| `admin.promo_texts` | Sale text | 活动文案 | Teks jualan | 区块标题 |
| `admin.copy_title` | Title | 标题 | Tajuk | 0–40 个字符；发布时英文须有（1–40） |
| `admin.copy_subtitle` | Subtitle | 副标题 | Subtajuk | 0–80 个字符 |
| `admin.copy_badge` | Badge phrase | 角标短语 | Frasa lencana | 0–12 个字符 |
| `admin.copy_strip` | Banner text | 横条文字 | Teks sepanduk | 0–60 个字符 |
| `admin.copy_fallback` | An English title is needed to publish. Empty Chinese or Malay text shows the English version. | 发布前须填英文标题；中文或马来文留空时显示英文。 | Tajuk Inggeris diperlukan untuk menerbitkan. Teks Cina atau Melayu yang kosong akan memaparkan versi Inggeris. | — |
| `admin.promo_decoration` | Decoration | 装饰 | Hiasan | 区块标题 |
| `admin.promo_pack` | Theme pack | 素材包 | Pek tema | — |
| `admin.pack_cny` | Chinese New Year | 农历新年 | Tahun Baru Cina | 素材包 |
| `admin.pack_raya` | Hari Raya | 开斋节 | Hari Raya | 素材包 |
| `admin.pack_deepavali` | Deepavali | 屠妖节 | Deepavali | 素材包 |
| `admin.pack_christmas` | Christmas | 圣诞节 | Krismas | 素材包 |
| `admin.pack_merdeka` | Merdeka | 国庆 | Merdeka | 素材包 |
| `admin.pack_mega_sale` | Mega sale (11.11, 12.12, Black Friday) | 电商大促（11.11、12.12、黑五） | Jualan mega (11.11, 12.12, Black Friday) | 素材包 |
| `admin.pack_generic` | General sale | 通用促销 | Jualan umum | 素材包 |
| `admin.promo_palette` | Colours | 配色 | Warna | — |
| `admin.palette_keep` | Keep the store theme colours | 保持店铺主题配色 | Kekalkan warna tema kedai | — |
| `admin.palette_of` | {pack} colours | {pack}配色 | Warna {pack} | `{pack}` 为素材包名称 |
| `admin.slot_ambient` | Page ornaments | 页面装饰 | Hiasan halaman | 装饰位 |
| `admin.slot_home_poster` | Home poster layout | 首页海报版式 | Susun atur poster utama | 装饰位，模板海报用 |
| `admin.slot_list_strip` | Sale banner | 活动横条 | Sepanduk jualan | 装饰位，P02、P15 |
| `admin.slot_card_badge` | Product badge | 商品角标 | Lencana produk | 装饰位 |
| `admin.slot_detail_ribbon` | Product page price ribbon | 详情页价格飘带 | Reben harga halaman produk | 装饰位，P03 |
| `admin.slot_cart_progress` | Cart progress bar | 购物车进度条 | Bar kemajuan troli | 装饰位，P04 |
| `admin.variant_none` | None | 无 | Tiada | 只用于 `admin.slot_ambient` |
| `admin.variant_n` | Style {n} | 样式 {n} | Gaya {n} | 装饰位变体 |
| `admin.promo_poster` | Home poster | 首页海报 | Poster utama | 区块标题 |
| `admin.poster_template` | Template | 模板 | Templat | — |
| `admin.poster_image` | Uploaded image | 上传图片 | Imej dimuat naik | — |
| `admin.poster_desktop` | Desktop image | 桌面图 | Imej desktop | — |
| `admin.poster_desktop_rules` | At least 1,200 pixels wide. Best at 1,800 × 600 (3:1); other shapes are cropped from the centre. | 宽至少 1,200 像素；建议 1,800 × 600（3:1），其他比例会从中间裁切。 | Lebar sekurang-kurangnya 1,200 piksel. Terbaik 1,800 × 600 (3:1); bentuk lain dipotong dari tengah. | — |
| `admin.poster_phone` | Phone image | 手机图 | Imej telefon | — |
| `admin.poster_phone_rules` | At least 600 pixels wide. Best at 960 × 1,200 (4:5); other shapes are cropped from the centre. | 宽至少 600 像素；建议 960 × 1,200（4:5），其他比例会从中间裁切。 | Lebar sekurang-kurangnya 600 piksel. Terbaik 960 × 1,200 (4:5); bentuk lain dipotong dari tengah. | — |
| `admin.poster_alt` | Image description (alt text) | 图片说明（替代文字） | Penerangan imej (teks alternatif) | 三语，各 1–150 个字符，英文必填 |
| `admin.ai_title` | Design with AI | 用 AI 装饰 | Reka dengan AI | 区块标题 |
| `admin.ai_placeholder` | e.g. Chinese New Year, festive red and gold | 例如：农历新年，喜庆红金 | cth. Tahun Baru Cina, merah dan emas yang meriah | A12 描述输入框占位 |
| `admin.ai_describe` | Describe the look you want | 描述你想要的效果 | Terangkan rupa yang anda mahu | 1–500 个字符；输入框占位可写例子，如「农历新年，喜庆红金」 |
| `admin.ai_privacy` | Don't include personal information. Your description is sent to the AI service and isn't saved. | 请不要填写个人资料。描述会发送给 AI 服务，不会保存。 | Jangan masukkan maklumat peribadi. Penerangan anda dihantar kepada perkhidmatan AI dan tidak disimpan. | — |
| `admin.ai_generate` | Suggest a design | 生成方案 | Cadangkan reka bentuk | — |
| `admin.ai_generating` | Designing… | 正在生成… | Sedang mereka… | 提交中 |
| `admin.ai_review_copy` | Check the text before you save — it appears on the storefront. | 保存前请检查文字，它会显示在前台。 | Semak teks sebelum menyimpan — ia dipaparkan di kedai. | AI 结果下方 |
| `admin.ai_apply` | Use this design | 填入编辑区 | Guna reka bentuk ini | 只填回，不保存 |
| `admin.ai_applied` | Filled in. Check it in the editor preview, then save to use it. | 已填入编辑区。请在编辑区预览里检查，保存后才生效。 | Telah diisi. Semak dalam pratonton penyunting, kemudian simpan untuk menggunakannya. | — |
| `admin.editor_preview` | Editor preview | 编辑区预览 | Pratonton penyunting | A12，随未保存的输入即时更新 |
| `admin.editor_preview_note` | A sketch with sample products and amounts. Real prices are worked out on the storefront. | 示意图，商品与金额都是示例；实际价格以前台计算为准。 | Lakaran dengan produk dan jumlah contoh. Harga sebenar dikira di kedai. | A12 编辑区预览下方 |
| `admin.promo_preview_needs_time` | To preview on the storefront, save a start time and an end time that's still ahead. | 保存开始时间与尚未过去的结束时间后，才能在前台预览。 | Untuk pratonton di kedai, simpan masa mula dan masa tamat yang belum berlalu. | A12 前台预览按钮禁用时 |
| `admin.ai_invalid` | The AI's suggestion couldn't be used. Try again, or set the decoration yourself. | AI 的结果不合格。请重试，或手动设置装饰。 | Cadangan AI tidak dapat digunakan. Cuba lagi, atau tetapkan hiasan sendiri. | — |
| `admin.ai_unavailable` | AI isn't available right now. You can still set the decoration yourself. | AI 暂不可用，仍可手动设置装饰。 | AI tidak tersedia sekarang. Anda masih boleh menetapkan hiasan sendiri. | 超时、出错或余额不足 |
| `admin.ai_daily_limit` | Today's AI limit ({count}) has been reached. Try again tomorrow, or set the decoration yourself. | 今天的 AI 次数（{count} 次）已用完。请明天再试，或手动设置装饰。 | Had AI hari ini ({count}) telah dicapai. Cuba esok, atau tetapkan hiasan sendiri. | — |
| `admin.ai_not_configured` | AI isn't set up for this store. You can set the decoration yourself. | 本店尚未设置 AI，可手动设置装饰。 | AI belum disediakan untuk kedai ini. Anda boleh menetapkan hiasan sendiri. | 按钮禁用时 |
| `admin.preview_home` | Home | 首页 | Utama | 预览页签 |
| `admin.preview_list` | Product list | 商品列表 | Senarai produk | 预览页签 |
| `admin.preview_detail` | Product page | 商品详情 | Halaman produk | 预览页签 |
| `admin.preview_cart` | Cart | 购物车 | Troli | 预览页签 |
| `admin.preview_sale` | Sale page | 活动专题页 | Halaman jualan | 预览页签 |
| `admin.design_promo_note` | While a promotion is running, its decoration is shown on top of these settings and may switch to festive colours. These settings don't change, and the storefront returns to them when it ends. | 活动进行期间，前台在本页设置之上叠加活动装饰，可能临时换为节日配色；本页设置不变，活动结束即恢复。 | Semasa promosi berjalan, hiasannya dipaparkan di atas tetapan ini dan mungkin bertukar kepada warna perayaan. Tetapan ini tidak berubah, dan kedai kembali kepadanya apabila promosi tamat. | A08 |
| `admin.order_promotion` | Promotion: {name} | 活动：{name} | Promosi: {name} | A02 金额明细；`{name}` 为活动内部名称 |

## 待决问题

与 [UX.md](UX.md)「待决问题」同一编号、同一内容，共 19 项（Q1–Q15 编号不变，Q16–Q19 为 0.2 新增，0.3–0.6 未新增）。本稿只列出，不自行改设计或需求。状态：已决 17 项（Q1、Q2、Q5–Q19）；不再适用 2 项（Q3、Q4）；待决 0 项。Q11、Q14、Q16–Q19 依据 Kelvin 2026-09-30 的决定（记录见 `docs/HANDOFF.md`）在 0.3 转为已决。

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

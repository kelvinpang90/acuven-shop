# 视觉稿导出

> 2026-09-30 导出。Kelvin 已认可视觉稿，审阅记录见 `docs/HANDOFF.md`。

页面的内容、流程、去向与文案以 [UX.md](../UX.md) 与 [UX-COPY.md](../UX-COPY.md) 为准；本目录只规定样式。二者冲突时以 UX 文档为准，并把冲突报给运营者。

## 目录

| 路径 | 内容 |
| --- | --- |
| [DESIGN-SYSTEM.md](DESIGN-SYSTEM.md) | 设计系统规范：演示标记规则、颜色、字体、间距、形状、10 款主题 |
| [COMPONENTS.md](COMPONENTS.md) | 各组件的用法与对应文案键 |
| `tokens/acuven-shop.css` | 前端直接使用的样式：Google Fonts 引入、10 款主题 × 浅色 / 深色的 CSS 变量、全部组件类（`acs-*`）与后台样式（`acs-admin*`） |
| `tokens/themes.json` | 10 款主题的完整取值（每种模式的全部颜色、字体、圆角、边框、横幅纹样、主视觉形状、可选主色），供后台 A08 列出主题与主色 |
| `tokens/tokens.json` | 默认主题（班兰）的颜色、间距与字号 |
| `src/components.css` | 组件样式的源文件 |
| `tools/themes_src.py`、`tools/gen.py` | 主题源数据与生成脚本（仅标准库） |
| `pages/*.html` | 每张画板的静态页面，默认主题、浅色，断网可直接用浏览器打开 |
| `reference/*.png` | 每张画板的参考图（与 `pages/` 同名） |

## 在前端使用

- 引入 `tokens/acuven-shop.css`（复制进前端或由构建流程引用；不要手改生成的文件）。
- 前台每页的根元素加 `class="acs"`，并设 `data-shop-theme="<主题 id>"`、可选 `data-accent="<主色 id>"`（不设即该主题的默认主色）、`data-mode="auto"`（深色跟随访客设备）。主题、主色取值来自 A08 保存的设置，见 `tokens/themes.json`。
- 组件只读语义变量（`--surface`、`--ink`、`--accent`……），页面里不写死颜色、字体或圆角。
- 后台所有页面用 `class="acs-admin"`，不受店铺主题影响。
- `acs--phone` 只供视觉稿在宽画布上强制手机样式；前端靠媒体查询（767px 以下）自动切换，不需要它。
- 演示横幅、★ 页面提示、◆ 操作提示与结账两处告知的样式与规则见 DESIGN-SYSTEM.md「Demo markers」；装修不能隐藏或弱化它们。

## 修改主题

1. 改 `tools/themes_src.py`（颜色、字体、圆角、边框、横幅纹样、主视觉形状、主色选项）或 `src/components.css`。
2. 在仓库根目录运行 `python docs/design/tools/gen.py`。脚本逐一校验 10 款主题两种模式及每个主色下的颜色对比度（正文 4.5:1，控件边框、焦点框与演示标记 3:1），略有不足的颜色自动调整明度并列出，仍不达标时以非零退出码失败。
3. 提交重新生成的 `tokens/` 文件。

## 画板示例数据

- 商品、分类、规格与价格取自示例目录迁移 `0003`；运费与汇率为示例数值。
- 会员订单 `K7Q2-9MXA`（小计 RM 110.00、券 RM 10.00、积分 500 抵 RM 5.00、运费 RM 8.00、合计 RM 103.00）的逐件实付、获得积分与退款预估，均按 `docs/DESIGN.md` 1.9「计价、优惠、积分与库存」第 2–5 条的最大余数分摊算出：两件 T 恤实付 RM 67.37、蜡烛 RM 27.63，获得 95 积分；退蜡烛预计 RM 27.63，返还 146 积分、追回 27 积分。
- 订单号（如 `K7Q2-9MXA`）只是示意，真实格式尚未定义。人名、地址、手机号均为虚构；英国号码取自保留给虚构用途的号段。
- 商品图、分类图是占位图形；图片上传的格式与大小限制尚未确定，画板显示为 `[TYPES]`、`[SIZE]`，实现时再定。
- 人机验证框是占位，实际由托管服务渲染。

## 未画的内容

- 后台 A03 退款审核、A05 优惠券、A06 运费与演示汇率、A07 库存重置：沿用 A02、A04 的后台样式与组件。
- P09、P10 的完整会员模式（与查单模式同版式，差别见 UX.md）、手机版游客结账第 3 步、会员中心手机版的积分 / 优惠券 / 设置页签：按已画画板与 UX.md 推出。

## 页面索引

| 画板 | 静态页面 | 参考图 | 尺寸 |
| --- | --- | --- | --- |
| P01 首页 · 桌面 | [P01-desktop.html](pages/P01-desktop.html) | [P01-desktop.png](reference/P01-desktop.png) | 1440 × 2720 |
| P01 首页 · 手机 | [P01-phone.html](pages/P01-phone.html) | [P01-phone.png](reference/P01-phone.png) | 390 × 2610 |
| P02 商品列表 · 桌面 | [P02-desktop.html](pages/P02-desktop.html) | [P02-desktop.png](reference/P02-desktop.png) | 1440 × 2240 |
| P02 商品列表 · 手机 | [P02-phone.html](pages/P02-phone.html) | [P02-phone.png](reference/P02-phone.png) | 390 × 1470 |
| P02 筛选抽屉 · 手机 | [P02-phone-filter.html](pages/P02-phone-filter.html) | [P02-phone-filter.png](reference/P02-phone-filter.png) | 390 × 844 |
| P03 商品详情 · 桌面 | [P03-desktop.html](pages/P03-desktop.html) | [P03-desktop.png](reference/P03-desktop.png) | 1440 × 1620 |
| P03 商品详情 · 手机（含加入后提示条） | [P03-phone.html](pages/P03-phone.html) | [P03-phone.png](reference/P03-phone.png) | 390 × 1310 |
| P04 购物车 · 桌面 | [P04-desktop.html](pages/P04-desktop.html) | [P04-desktop.png](reference/P04-desktop.png) | 1440 × 940 |
| P04 购物车 · 手机 | [P04-phone.html](pages/P04-phone.html) | [P04-phone.png](reference/P04-phone.png) | 390 × 890 |
| P05 第 1 步 手机号 · 桌面 | [P05-desktop-1.html](pages/P05-desktop-1.html) | [P05-desktop-1.png](reference/P05-desktop-1.png) | 1440 × 1080 |
| P05 第 2 步 短信验证（已发送验证码）· 桌面 | [P05-desktop-2.html](pages/P05-desktop-2.html) | [P05-desktop-2.png](reference/P05-desktop-2.png) | 1440 × 1320 |
| P05 第 3 步 会员 · 桌面 | [P05-desktop-3-member.html](pages/P05-desktop-3-member.html) | [P05-desktop-3-member.png](reference/P05-desktop-3-member.png) | 1440 × 1790 |
| P05 第 3 步 游客（白名单外号码）· 桌面 | [P05-desktop-3-guest.html](pages/P05-desktop-3-guest.html) | [P05-desktop-3-guest.png](reference/P05-desktop-3-guest.png) | 1440 × 1590 |
| P05 第 1 步 · 手机 | [P05-phone-1.html](pages/P05-phone-1.html) | [P05-phone-1.png](reference/P05-phone-1.png) | 390 × 900 |
| P05 第 2 步 · 手机 | [P05-phone-2.html](pages/P05-phone-2.html) | [P05-phone-2.png](reference/P05-phone-2.png) | 390 × 1270 |
| P05 第 3 步 会员 · 手机 | [P05-phone-3-member.html](pages/P05-phone-3-member.html) | [P05-phone-3-member.png](reference/P05-phone-3-member.png) | 390 × 2100 |
| V1 开始（注册 / 短信登录，仅 +60 +65） | [V1-start.html](pages/V1-start.html) | [V1-start.png](reference/V1-start.png) | 480 × 450 |
| V1 验证码错误（不降级） | [V1-code-wrong.html](pages/V1-code-wrong.html) | [V1-code-wrong.png](reference/V1-code-wrong.png) | 480 × 290 |
| V1 其他错误（按情况显示其一） | [V1-errors.html](pages/V1-errors.html) | [V1-errors.png](reference/V1-errors.png) | 480 × 240 |
| V1 结账：短信无法送达，可改游客 | [V1-checkout-unavailable.html](pages/V1-checkout-unavailable.html) | [V1-checkout-unavailable.png](reference/V1-checkout-unavailable.png) | 480 × 210 |
| V1 注册/短信登录：不在白名单或发送失败 | [V1-register-unavailable.html](pages/V1-register-unavailable.html) | [V1-register-unavailable.png](reference/V1-register-unavailable.png) | 480 × 270 |
| V1 重设密码/注销：短信未发出（无游客选项） | [V1-no-change.html](pages/V1-no-change.html) | [V1-no-change.png](reference/V1-no-change.png) | 480 × 130 |
| V1 验证通过（三种文案） | [V1-verified.html](pages/V1-verified.html) | [V1-verified.png](reference/V1-verified.png) | 480 × 290 |
| P02 无结果 | [P02-empty.html](pages/P02-empty.html) | [P02-empty.png](reference/P02-empty.png) | 640 × 230 |
| P04 购物车为空 / 商品有变化 | [P04-states.html](pages/P04-states.html) | [P04-states.png](reference/P04-states.png) | 640 × 270 |
| P05 报错与提交中 | [P05-states.html](pages/P05-states.html) | [P05-states.png](reference/P05-states.png) | 640 × 450 |
| P06 模拟支付（游客）· 桌面 | [P06-desktop.html](pages/P06-desktop.html) | [P06-desktop.png](reference/P06-desktop.png) | 1440 × 1540 |
| P06 模拟支付（游客）· 手机 | [P06-phone.html](pages/P06-phone.html) | [P06-phone.png](reference/P06-phone.png) | 390 × 1670 |
| P06 取消确认框 | [P06-cancel-confirm.html](pages/P06-cancel-confirm.html) | [P06-cancel-confirm.png](reference/P06-cancel-confirm.png) | 640 × 240 |
| P06 / P07 游客凭据过期 | [P06-expired.html](pages/P06-expired.html) | [P06-expired.png](reference/P06-expired.png) | 640 × 240 |
| P07 成功（会员）· 桌面 | [P07-desktop-member.html](pages/P07-desktop-member.html) | [P07-desktop-member.png](reference/P07-desktop-member.png) | 1440 × 1220 |
| P07 成功（游客）· 手机 | [P07-phone-guest.html](pages/P07-phone-guest.html) | [P07-phone-guest.png](reference/P07-phone-guest.png) | 390 × 1340 |
| P07 失败 | [P07-failure.html](pages/P07-failure.html) | [P07-failure.png](reference/P07-failure.png) | 640 × 370 |
| P07 已取消（上：超时；下：本人取消，二选一） | [P07-cancelled.html](pages/P07-cancelled.html) | [P07-cancelled.png](reference/P07-cancelled.png) | 640 × 280 |
| P08 订单查询 · 桌面 | [P08-desktop.html](pages/P08-desktop.html) | [P08-desktop.png](reference/P08-desktop.png) | 1440 × 1170 |
| P08 查不到订单 · 手机 | [P08-phone.html](pages/P08-phone.html) | [P08-phone.png](reference/P08-phone.png) | 390 × 1210 |
| P09 查单模式（已发货）· 桌面 | [P09-desktop.html](pages/P09-desktop.html) | [P09-desktop.png](reference/P09-desktop.png) | 1440 × 1450 |
| P09 查单模式 · 手机 | [P09-phone.html](pages/P09-phone.html) | [P09-phone.png](reference/P09-phone.png) | 390 × 1810 |
| P09 待支付（上：查单模式；下：会员模式） | [P09-pending.html](pages/P09-pending.html) | [P09-pending.png](reference/P09-pending.png) | 640 × 320 |
| P09 / P10 查单授权过期 | [P09-expired.html](pages/P09-expired.html) | [P09-expired.png](reference/P09-expired.png) | 640 × 220 |
| P10 退款申请（查单模式）· 桌面 | [P10-desktop.html](pages/P10-desktop.html) | [P10-desktop.png](reference/P10-desktop.png) | 1440 × 1300 |
| P10 退款申请 · 手机 | [P10-phone.html](pages/P10-phone.html) | [P10-phone.png](reference/P10-phone.png) | 390 × 1110 |
| P11 注册 · 桌面 | [P11-desktop.html](pages/P11-desktop.html) | [P11-desktop.png](reference/P11-desktop.png) | 1440 × 1320 |
| P11 注册（已发送验证码）· 手机 | [P11-phone.html](pages/P11-phone.html) | [P11-phone.png](reference/P11-phone.png) | 390 × 1380 |
| P12 登录 + 重设密码（已注册号码验证通过）· 桌面 | [P12-desktop.html](pages/P12-desktop.html) | [P12-desktop.png](reference/P12-desktop.png) | 1440 × 1210 |
| P12 密码错误 · 手机 | [P12-phone.html](pages/P12-phone.html) | [P12-phone.png](reference/P12-phone.png) | 390 × 1170 |
| P12 重设：号码未注册 / 密码已更新 | [P12-reset-states.html](pages/P12-reset-states.html) | [P12-reset-states.png](reference/P12-reset-states.png) | 640 × 290 |
| P13 会员中心 · 桌面 | [P13-desktop.html](pages/P13-desktop.html) | [P13-desktop.png](reference/P13-desktop.png) | 1440 × 2610 |
| P13 我的订单 · 手机 | [P13-phone.html](pages/P13-phone.html) | [P13-phone.png](reference/P13-phone.png) | 390 × 950 |
| P14 隐私说明 · 桌面 | [P14-desktop.html](pages/P14-desktop.html) | [P14-desktop.png](reference/P14-desktop.png) | 1440 × 1710 |
| P14 隐私说明 · 手机 | [P14-phone.html](pages/P14-phone.html) | [P14-phone.png](reference/P14-phone.png) | 390 × 2220 |
| A01 后台登录 | [A01-desktop.html](pages/A01-desktop.html) | [A01-desktop.png](reference/A01-desktop.png) | 1440 × 720 |
| A02 订单与模拟发货 | [A02-desktop.html](pages/A02-desktop.html) | [A02-desktop.png](reference/A02-desktop.png) | 1440 × 900 |
| A04 商品、规格与库存 | [A04-desktop.html](pages/A04-desktop.html) | [A04-desktop.png](reference/A04-desktop.png) | 1440 × 1080 |
| A08 店铺装修 · 桌面 | [A08-desktop.html](pages/A08-desktop.html) | [A08-desktop.png](reference/A08-desktop.png) | 1440 × 1250 |
| A08 店铺装修 · 手机 | [A08-phone.html](pages/A08-phone.html) | [A08-phone.png](reference/A08-phone.png) | 390 × 1640 |

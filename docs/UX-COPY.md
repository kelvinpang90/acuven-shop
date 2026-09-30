# Acuven Shop 三语文案与演示提示（审阅稿）

> **审阅稿。0.3 的内容 Kelvin 已于 2026-09-30 审阅；0.4 新增的文案键候审，审阅通过前以 0.3 为准。**
> 版本 0.4（2026-09-30），运营者修订（Claude Code），在 `SHOP-TASK-008` 的 0.3 上增补店铺装修与视觉稿审阅所需的文案键。页面结构与线框见 [UX.md](UX.md)；依据 `docs/REQUIREMENTS.md` 1.9 候批稿（店铺装修、多规格起价显示、游客结账摘要；其余条款同 1.8）与 `docs/DESIGN.md` 1.9（2026-09-30 获 Kelvin 批准，批准记录见 `docs/HANDOFF.md` 0.19）；Kelvin 2026-09-30 对待决问题 Q11、Q14、Q16、Q17、Q18、Q19 的决定见 `docs/HANDOFF.md`。

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
| `account.password_set_submit` | Set password | 设置密码 | Tetapkan kata laluan | — |
| `account.password_set_done` | Password set. You can now log in with your password or an SMS code. | 密码已设置，之后可用密码或短信验证码登录。 | Kata laluan ditetapkan. Anda kini boleh log masuk dengan kata laluan atau kod SMS. | — |
| `account.password_exists` | A password is set. Changing it requires SMS verification. | 已设置密码；更改密码须经短信验证。 | Kata laluan telah ditetapkan. Menukarnya memerlukan pengesahan SMS. | — |
| `account.password_change` | Change password | 更改密码 | Tukar kata laluan | 去 P12 重设流程 |
| `account.delete` | Delete account | 注销账号 | Padam akaun | — |
| `account.delete_warning` | Deleting your account removes your mobile number and password, logs you out everywhere, and forfeits your points and unused coupons. Your orders, including their shipping details, stay in our records but are no longer linked to you, and registering again with the same number will not restore them. This cannot be undone. | 注销将删除您的手机号和密码并退出所有登录，积分余额与未用优惠券作废。订单（含收货资料）仍保留在记录中，但不再与您关联，用同一号码重新注册也不会恢复。此操作不可撤销。 | Memadam akaun akan membuang nombor telefon bimbit dan kata laluan anda, log keluar di semua peranti, dan melucutkan mata serta kupon yang belum digunakan. Pesanan anda, termasuk butiran penghantaran, kekal dalam rekod tetapi tidak lagi dikaitkan dengan anda, dan mendaftar semula dengan nombor yang sama tidak akan memulihkannya. Tindakan ini tidak boleh dibatalkan. | DESIGN「权限与资料保护」「资料保留」 |
| `account.delete_verify` | To confirm, verify your mobile number by SMS first. | 请先通过短信验证手机号以确认注销。 | Untuk mengesahkan, sahkan nombor telefon bimbit anda melalui SMS dahulu. | Q15 已决 |
| `account.delete_confirm` | Delete my account | 确认注销 | Padam akaun saya | 短信验证通过后才显示 |
| `account.deleted` | Your account has been deleted. | 您的账号已注销。 | Akaun anda telah dipadam. | — |
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
| `privacy.fictional` | You may use fictional shipping details. The exception is a Malaysian or Singapore mobile number entered at checkout, which must be able to receive our verification SMS. | 收货资料可以填写虚构内容。唯一例外是结账时填写的马来西亚或新加坡手机号，须能收到验证短信。 | Anda boleh menggunakan butiran penghantaran rekaan. Pengecualiannya ialah nombor telefon bimbit Malaysia atau Singapura yang dimasukkan semasa daftar keluar, yang mesti boleh menerima SMS pengesahan kami. | — |
| `privacy.retention_recipient` | Shipping details are kept long-term together with the order's items, amounts and status. They are not deleted or anonymised, including after a member account is deleted. | 收货资料与订单商品、金额和状态一起长期保存，不删除、不匿名化；会员注销后也同样保留。 | Butiran penghantaran disimpan untuk jangka panjang bersama item, jumlah dan status pesanan. Ia tidak dipadam atau dianonimkan, termasuk selepas akaun ahli dipadam. | DESIGN「资料保留」；不提供删除入口 |
| `privacy.member` | Member mobile numbers, including accounts created automatically at checkout, are kept until you delete your account; inactive accounts are not deleted automatically. SMS verification records are kept briefly to prevent abuse. | 会员手机号（含结账时自动注册的账号）保留至您注销账号，长期未登录也不会自动注销。短信验证记录短期保留，用于防滥用。 | Nombor telefon bimbit ahli, termasuk akaun yang dicipta secara automatik semasa daftar keluar, disimpan sehingga anda memadam akaun; akaun yang tidak aktif tidak dipadam secara automatik. Rekod pengesahan SMS disimpan untuk tempoh singkat bagi mencegah penyalahgunaan. | — |
| `privacy.member_backup` | After you delete your account, copies of your mobile number and password hash made before the deletion may remain in our database backups for a period that has not yet been determined. | 注销账号后，注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间。 | Selepas anda memadam akaun, salinan nombor telefon bimbit dan hash kata laluan anda yang dibuat sebelum pemadaman mungkin kekal dalam sandaran pangkalan data untuk suatu tempoh yang belum ditentukan. | Q18 已决：对外披露；不写天数，不写或暗示备份一定会在某时被清除；DESIGN「资料保留」 |
| `privacy.browser_access` | After you place a guest order or look up an order, this browser gets access to that order for 30 minutes. After 30 minutes, or in another browser, look the order up again with its order number and phone number. | 游客下单或查询订单后，本浏览器获得对该订单 30 分钟的访问。30 分钟后，或在其他浏览器上，请凭订单号和电话重新查询该订单。 | Selepas anda membuat pesanan tetamu atau menyemak pesanan, pelayar ini mendapat akses kepada pesanan itu selama 30 minit. Selepas 30 minit, atau dalam pelayar lain, semak pesanan itu semula dengan nombor pesanan dan nombor telefonnya. | DESIGN「权限与资料保护」；不表示该单以后只能在本浏览器打开 |
| `privacy.lookup_risk` | Anyone who knows both the order number and the phone number can view the full shipping details. Because shipping details are kept long-term, this stays possible for as long as the order exists. | 同时知道订单号和电话的人可以查看完整收货资料。由于收货资料长期保存，只要订单存在，这一点就一直成立。 | Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Oleh sebab butiran penghantaran disimpan untuk jangka panjang, ini kekal boleh berlaku selagi pesanan wujud. | — |
| `privacy.sms` | Checkout verification for Malaysian and Singapore numbers, registration, SMS login, password reset and account deletion send a real SMS through our SMS provider. | 马新号码结账验证、注册、短信登录、重设密码与注销确认会通过短信服务商发送真实短信。 | Pengesahan daftar keluar untuk nombor Malaysia dan Singapura, pendaftaran, log masuk SMS, tetapan semula kata laluan dan pemadaman akaun menghantar SMS sebenar melalui penyedia SMS kami. | — |
| `privacy.logs` | Our logs are kept for up to 30 days and do not contain your name, full phone number, address or password. | 日志最多保留 30 天，不含您的姓名、完整电话、地址或密码。 | Log kami disimpan sehingga 30 hari dan tidak mengandungi nama, nombor telefon penuh, alamat atau kata laluan anda. | — |
| `privacy.contact` | Questions? Contact Acuven on WhatsApp. | 有疑问？请通过 WhatsApp 联系 Acuven。 | Ada soalan? Hubungi Acuven melalui WhatsApp. | 链接为占位；未配置时隐藏 |
| `privacy.demo_hint` | This whole site is a demonstration; no real orders are fulfilled. | 整个网站都是演示，不履行任何真实订单。 | Seluruh laman ini adalah demonstrasi; tiada pesanan sebenar dipenuhi. | ★ |

## 10. 管理后台（A01–A07）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `admin.demo_banner` | Admin — demo store. Actions here never move real money or goods. | 管理后台——演示网店。这里的操作不会产生真实资金或货物流动。 | Pentadbir — kedai demo. Tindakan di sini tidak pernah memindahkan wang atau barang sebenar. | ★ 后台常驻 |
| `admin.login_title` | Admin log in | 管理员登录 | Log masuk pentadbir | — |
| `admin.username` | Username | 用户名 | Nama pengguna | — |
| `admin.login_failed` | Username or password is incorrect. | 用户名或密码不正确。 | Nama pengguna atau kata laluan salah. | A01 |
| `admin.locked` | Too many failed attempts. This sign-in is locked for a short time. | 失败次数过多，登录已短时锁定。 | Terlalu banyak cubaan gagal. Log masuk ini dikunci untuk seketika. | — |
| `admin.nav_orders` | Orders | 订单 | Pesanan | — |
| `admin.nav_refunds` | Refunds | 退款审核 | Bayaran balik | — |
| `admin.nav_products` | Products | 商品 | Produk | — |
| `admin.nav_coupons` | Coupons | 优惠券 | Kupon | — |
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
| `admin.product_edit` | Edit product | 编辑商品 | Sunting produk | — |
| `admin.content_language` | Content language | 内容语言 | Bahasa kandungan | 商品三语文案页签 |
| `admin.product_name` | Product name | 商品名称 | Nama produk | — |
| `admin.product_description` | Description | 描述 | Penerangan | — |
| `admin.product_category` | Category | 分类 | Kategori | — |
| `admin.product_images` | Images | 图片 | Imej | — |
| `admin.upload` | Upload | 上传 | Muat naik | — |
| `admin.variant` | Option values | 规格 | Nilai pilihan | 列头 |
| `admin.image_rules` | Allowed types: {types}. Max size: {size} MB. | 允许格式：{types}；大小上限：{size} MB。 | Jenis dibenarkan: {types}. Saiz maksimum: {size} MB. | — |
| `admin.product_options` | Options (e.g. colour, size) | 规格（如颜色、尺寸） | Pilihan (cth. warna, saiz) | — |
| `admin.sku` | SKU | SKU | SKU | — |
| `admin.price` | Price (MYR) | 价格（MYR） | Harga (MYR) | — |
| `admin.initial_stock` | Daily initial stock | 每日初始库存 | Stok awal harian | — |
| `admin.available_today` | Available today | 今日可用 | Tersedia hari ini | — |
| `admin.adjust_today` | Adjust today's stock | 调整当日库存 | Laraskan stok hari ini | — |
| `admin.initial_stock_note` | Changes to initial stock take effect from the next daily reset. | 修改初始库存从次日重置起生效。 | Perubahan stok awal berkuat kuasa dari tetapan semula harian berikutnya. | — |
| `admin.active` | Published | 上架 | Diterbitkan | — |
| `admin.translation_missing` | {language} text is missing and will fall back to English. English text is required to publish. | 缺少{language}文案，将回退英文；缺少英文则不能上架。 | Teks {language} tiada dan akan kembali kepada bahasa Inggeris. Teks bahasa Inggeris diperlukan untuk diterbitkan. | — |
| `admin.rules_apply_new` | Changes apply to new orders only. Existing orders keep their original amounts. | 修改只影响新订单，已有订单保持原金额。 | Perubahan hanya terpakai untuk pesanan baharu. Pesanan sedia ada mengekalkan jumlah asal. | — |
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
| `admin.shipping_title` | Sample shipping fees | 示例运费 | Kos penghantaran contoh | 区块标题 |
| `admin.shipping_by_country` | By country | 按国家 | Mengikut negara | — |
| `admin.add_country` | Add country | 新增国家 | Tambah negara | — |
| `admin.shipping_country` | Country | 国家/地区 | Negara | — |
| `admin.shipping_my_state` | Malaysia, by state | 马来西亚（按州属） | Malaysia, mengikut negeri | — |
| `admin.shipping_other` | All other countries | 其他国家 | Semua negara lain | — |
| `admin.shipping_fee` | Sample shipping fee (RM) | 示例运费（RM） | Kos penghantaran contoh (RM) | — |
| `admin.fx_title` | Demo exchange rates | 演示汇率 | Kadar pertukaran demo | 区块标题 |
| `admin.fx_rate` | Demo rate: 1 MYR = {rate} {currency} | 演示汇率：1 MYR = {rate} {currency} | Kadar demo: 1 MYR = {rate} {currency} | — |
| `admin.fx_version` | Version {n} | 版本 {n} | Versi {n} | — |
| `admin.stock_reset_title` | Daily stock reset results | 每日库存重置结果 | Keputusan tetapan semula stok harian | — |
| `admin.col_date_myt` | Date (Malaysia time) | 日期（马来西亚时间） | Tarikh (waktu Malaysia) | 列头 |
| `admin.col_result` | Result | 结果 | Keputusan | 列头 |
| `admin.col_sku_count` | SKUs | SKU 数 | Bilangan SKU | 列头 |
| `admin.stock_reset_breakdown` | Initial {initial} − active holds {held} = available today {available} | 初始 {initial} − 有效预留 {held} = 当日可用 {available} | Awal {initial} − tahanan aktif {held} = tersedia hari ini {available} | — |
| `admin.stock_reset_ok` | Completed | 已完成 | Selesai | — |
| `admin.stock_reset_failed` | Failed — the operator has been alerted | 失败——已告警运营者 | Gagal — pengendali telah dimaklumkan | — |
| `admin.stock_reset_note` | Resets restore today's stock only. Past orders, refunds and points are not changed. | 重置只恢复当日库存，不改动历史订单、退款与积分。 | Tetapan semula hanya memulihkan stok hari ini. Pesanan, bayaran balik dan mata lalu tidak diubah. | — |
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
| `admin.home_blocks` | Home page blocks | 首页区块 | Blok halaman utama | A08（0.4） |
| `admin.block_hero` | Main banner | 主视觉 | Sepanduk utama | A08 区块名；其余三块用 `home.how_title`、`home.categories`、`home.featured`（0.4） |
| `admin.block_show` | Show | 显示 | Papar | A08 区块显隐勾选框（0.4） |
| `admin.block_move_up` | Move up | 上移 | Alih ke atas | 读屏标签（0.4） |
| `admin.block_move_down` | Move down | 下移 | Alih ke bawah | 读屏标签（0.4） |
| `admin.home_blocks_hint` | Block text comes from the store's translations and can't be edited here. The demo hint on the home page always shows above these blocks. | 区块文字来自店铺的三语文案，不能在这里修改。首页的演示提示始终显示在这些区块上方。 | Teks blok datang daripada terjemahan kedai dan tidak boleh disunting di sini. Petunjuk demo di halaman utama sentiasa dipaparkan di atas blok ini. | A08（0.4） |
| `admin.design_saved` | Saved. The storefront now uses these settings. | 已保存，前台已改用这些设置。 | Disimpan. Kedai kini menggunakan tetapan ini. | A08 保存成功（0.4） |

## 待决问题

与 [UX.md](UX.md)「待决问题」同一编号、同一内容，共 19 项（Q1–Q15 编号不变，Q16–Q19 为 0.2 新增，0.3、0.4 未新增）。本稿只列出，不自行改设计或需求。状态：已决 17 项（Q1、Q2、Q5–Q19）；不再适用 2 项（Q3、Q4）；待决 0 项。Q11、Q14、Q16–Q19 依据 Kelvin 2026-09-30 的决定（记录见 `docs/HANDOFF.md`）在 0.3 转为已决。

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
- **Q11 虚构电话被真实号码持有人认领。** **已决**，依据 Kelvin 2026-09-30 的决定（见 `docs/HANDOFF.md`）：1.7 起马来西亚、新加坡（短信白名单）号码结账须先短信验证并成为会员，不再用于游客订单（DESIGN 1.9「权限与资料保护」），一般情形下游客订单上不再出现可被他人注册认领的白名单号码。**短信无法送达或停发时降级的游客下单**，以及**以后扩大白名单**时，这些游客订单仍可能被该号码的真实持有人注册后认领并看到收货资料（DESIGN 1.9 也写明认领主要在这两种情形下生效）；Kelvin 接受这一剩余风险。不新增或改动文案。
- **Q12 待支付订单的取消入口与操作者。** **已决**，依据 DESIGN 1.9「订单与退款状态」「权限与资料保护」：待支付订单由下单者在支付页取消，游客凭该单短期凭据，会员凭登录会话；P06 为两者都提供 `pay.cancel_order`；查单页不提供取消。
- **Q13 会员中心「优惠券」的含义。** **已决**，依据 Kelvin 2026-09-29 的决定：「我的优惠券」同时列出当前可用的公开券与本人使用记录（P13）。「公开券」的范围见 Q19。
- **Q14 马来文文案审校。** **已决**，依据 Kelvin 2026-09-30 的决定：先上线，上线后再由马来文母语者审校用词（如 troli、daftar keluar、bayaran balik）。UX-COPY 的马来文仍标为草稿；审校不再是页面实现的前置条件。
- **Q15 密码规则与注销确认方式。** **已决**，依据 DESIGN 1.9「权限与资料保护」：密码至少 8 位、不强制复杂度（`auth.password_rule`）；注销须先以短信验证码确认（P13 嵌入 V1）。
- **Q16 结账第一步手机号的默认区号。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定与已批准的 DESIGN 1.9「权限与资料保护」：结账第 1 步国家码下拉列出所有国家、默认 +60；以 `+` 开头输入时以输入为准；服务端按所选或输入的国家码规范化为 E.164 并判定是否属于白名单，之后选的收货国家不改变已判定的号码。游客订单的收货电话即第 1 步号码（只读），下单时不按收货国家重新解析；以收货国家作为默认区号只用于会员改填的收货电话。线框见 P05；`checkout.phone_step_hint` 措辞不变。
- **Q17 会员待支付订单能否从会员中心回到支付页。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定（与 DESIGN 1.9「订单与退款状态」中会员凭登录会话在支付页取消一致）：会员可从 P09 会员模式的 `account.order_pay` 回到 P06，继续支付或取消自己的待支付订单。线框见 P06、P09、P13。
- **Q18 会员注销后手机号在共享备份中的残留期措辞。**（0.2 新增，承接原 Q3）**已决**，依据 Kelvin 2026-09-30 的决定与 DESIGN 1.9「资料保留」（注销时在线删除的手机号与密码哈希，最迟在注销后第 max(binlog 残留期, N) 天才从共享备份与 binlog 中消失，运营核实前没有确定上限）：隐私页对外披露。`privacy.member_backup` 改写为：注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间；不写天数，不写或暗示备份一定会在某时被清除，用「密码哈希」而不是「密码」；该键已定稿。
- **Q19 「可用的公开券」的范围。**（0.2 新增）**已决**，依据 Kelvin 2026-09-30 的决定：「我的优惠券」的可用券列出所有启用中且在有效期内的券，对全部会员相同，不按每会员或总次数上限过滤；不改 DESIGN 1.9「数据模型」的 `Coupon`。P13 说明与后台 A05 的 `admin.coupon_listed_note` 按同一口径；已达上限的券在结账时由服务端拒绝。

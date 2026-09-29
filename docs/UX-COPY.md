# Acuven Shop 三语文案与演示提示（审阅稿）

> **审阅稿，Kelvin 审阅通过前不实现任何页面。**
> 版本 0.1（2026-09-29），任务 `SHOP-TASK-001`。页面结构与线框见 [UX.md](UX.md)；依据 `docs/REQUIREMENTS.md` 1.5 与 `docs/DESIGN.md` 1.6。

## 约定

- **默认语言：英文（English）。** 访客可在页头切换为中文或马来文（Bahasa Melayu），选择只保存在本浏览器；不按 IP 或浏览器语言自动切换。
- 每条文案有英文、中文、马来文三列，均不留空。`{…}` 为运行时替换的变量，三种语言保留同名变量。
- UX.md 线框中向用户显示的界面文字（前台与后台，含按钮、列头、区块标题、状态值与读屏标签）都以本表的键引用；线框里不在 `[ ]` 内的中文只是给审阅者的标注，不向用户显示。线框中的 `[order.status_*]`、`[account.points_type_*]`、`[admin.actor_*]`、`[admin.nav_*]`、`[common.lang_*]` 指本表中该前缀下的一组键；`[order.refund_requested|approved|rejected]` 指三选一。
- 商品名称、分类、规格等商品数据的三语文案由管理员在后台维护，不在本表；缺少当前语言时回退英文（DESIGN 1.6「数据模型」）。
- 金额一律以 MYR 显示为 `RM {amount}`，`{amount}` 由整数仙格式化为两位小数；积分一律称「积分 / points / mata」，不与金额混称（DESIGN 1.6「边界与原则」）。
- 联系入口只有 WhatsApp，链接由私有配置 `{{WHATSAPP_CONTACT_LINK}}` 提供；本文件与仓库不写任何真实电话、WhatsApp 号码、邮箱或地址。
- 「演示提示」列标 ★ 的是每页的演示提示；标 ◆ 的是下单、模拟支付、退款三处操作旁的专门演示提示。
- 马来文为草稿，须母语者审校（见「待决问题」Q14）。

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
| `common.nav_track` | Track order | 查询订单 | Semak pesanan | — |
| `common.nav_login` | Log in | 登录 | Log masuk | — |
| `common.nav_register` | Register | 注册 | Daftar | — |
| `common.nav_account` | My account | 会员中心 | Akaun saya | — |
| `common.nav_logout` | Log out | 退出登录 | Log keluar | — |
| `common.nav_privacy` | Privacy | 隐私说明 | Privasi | — |
| `common.nav_menu` | Menu | 菜单 | Menu | — |
| `common.whatsapp_cta` | Interested in a store like this? Chat with Acuven on WhatsApp | 想要这样的网店？通过 WhatsApp 联系 Acuven | Berminat dengan kedai seperti ini? Hubungi Acuven melalui WhatsApp | 链接为占位 `{{WHATSAPP_CONTACT_LINK}}` |
| `common.footer_demo` | Acuven demo store for showcasing online store solutions. All products, prices, stock, payments, shipping and refunds are simulated. | Acuven 网店方案演示站。所有商品、价格、库存、支付、运费、发货与退款均为模拟。 | Kedai demo Acuven untuk mempamerkan penyelesaian kedai dalam talian. Semua produk, harga, stok, bayaran, penghantaran dan bayaran balik adalah simulasi. | ★ 页脚 |
| `common.price_myr` | RM {amount} | RM {amount} | RM {amount} | — |
| `common.fx_reference` | ≈ {currency} {amount} (demo rate, for reference only) | ≈ {currency} {amount}（演示汇率，仅供参考） | ≈ {currency} {amount} (kadar demo, untuk rujukan sahaja) | — |
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
| `home.how_2` | Check out as a guest or member | 以游客或会员身份结账 | Daftar keluar sebagai tetamu atau ahli | — |
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
| `list.price_from` | From RM {amount} | RM {amount} 起 | Dari RM {amount} | — |
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
| `detail.english_only` | Shown in English | 仅提供英文 | Dipaparkan dalam bahasa Inggeris | 待决 Q8 |
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
| `checkout.guest_notice` | You are checking out as a guest. Log in or register to use coupons and points. | 您正以游客身份结账。登录或注册后可使用优惠券与积分。 | Anda mendaftar keluar sebagai tetamu. Log masuk atau daftar untuk guna kupon dan mata. | — |
| `checkout.recipient_title` | Shipping details | 收货资料 | Butiran penghantaran | — |
| `checkout.name` | Recipient name | 收货人姓名 | Nama penerima | — |
| `checkout.phone` | Phone number | 电话 | Nombor telefon | — |
| `checkout.phone_hint` | The country below sets the default country code. Start with + to use a different one. | 默认按下方国家补全区号；以 + 开头可输入其他国家码。 | Kod negara lalai mengikut negara di bawah. Mulakan dengan + untuk kod lain. | — |
| `checkout.phone_invalid` | This phone number doesn't look valid for the selected country. Please check it. | 该电话号码与所选国家的格式不符，请修改。 | Nombor telefon ini tidak sah untuk negara yang dipilih. Sila semak. | — |
| `checkout.phone_lookup_hint` | You'll need this phone number and your order number to track the order. | 查询订单需要此电话号码和订单号。 | Anda perlukan nombor telefon ini dan nombor pesanan untuk menyemak pesanan. | — |
| `checkout.country` | Country | 国家/地区 | Negara | — |
| `checkout.state_my` | State | 州属 | Negeri | 仅马来西亚 |
| `checkout.region` | State / province / region | 州/省/地区 | Negeri / wilayah | 其他国家 |
| `checkout.address` | Address | 地址 | Alamat | — |
| `checkout.postcode` | Postcode | 邮编 | Poskod | — |
| `checkout.form_notice` | Demo only: you will not be charged and nothing will be shipped. You may use fictional details. Shipping details are used only for this demo and are anonymised after 30 days. | 仅为演示：不会真实扣款，也不会真实发货。可填写虚构资料。收货资料仅用于本次演示，30 天后匿名化。 | Demo sahaja: anda tidak akan dicaj dan tiada barang akan dihantar. Anda boleh guna butiran rekaan. Butiran penghantaran digunakan untuk demo ini sahaja dan dianonimkan selepas 30 hari. | 收货表单旁；无勾选框；待决 Q3、Q4 |
| `checkout.form_notice_link` | How we handle your details | 我们如何处理您的资料 | Cara kami mengendalikan butiran anda | 链到 P14 |
| `checkout.coupon` | Coupon code | 优惠券代码 | Kod kupon | — |
| `checkout.coupon_apply` | Apply | 使用 | Guna | — |
| `checkout.coupon_members_only` | Coupons are for members only. | 优惠券仅限会员使用。 | Kupon untuk ahli sahaja. | — |
| `checkout.coupon_invalid` | This coupon can't be used for this order. | 此优惠券不适用于本订单。 | Kupon ini tidak boleh digunakan untuk pesanan ini. | — |
| `checkout.points` | Use points | 使用积分 | Guna mata | — |
| `checkout.points_available` | {points} points available (100 points = RM1) | 可用 {points} 积分（100 积分抵 RM1） | {points} mata tersedia (100 mata = RM1) | — |
| `checkout.points_guest` | Guests don't earn points. Register to earn 1 point for every RM1 paid. | 游客不累积积分。注册会员后每实付 RM1 得 1 积分。 | Tetamu tidak mengumpul mata. Daftar untuk dapat 1 mata bagi setiap RM1 dibayar. | — |
| `checkout.points_not_shipping` | Coupons and points reduce the item amount only, not shipping. | 优惠券与积分只抵商品金额，不抵运费。 | Kupon dan mata hanya mengurangkan jumlah item, bukan kos penghantaran. | — |
| `checkout.summary_title` | Order summary | 订单摘要 | Ringkasan pesanan | — |
| `checkout.summary_coupon` | Coupon discount | 优惠券抵扣 | Diskaun kupon | — |
| `checkout.summary_points` | Points discount | 积分抵扣 | Diskaun mata | — |
| `checkout.summary_shipping` | Sample shipping | 示例运费 | Kos penghantaran contoh | — |
| `checkout.summary_total` | Total (MYR) | 合计（MYR） | Jumlah (MYR) | — |
| `checkout.fx_note` | Reference amounts use a fixed demo rate and are never charged. | 参考金额按固定演示汇率换算，不会收取。 | Jumlah rujukan menggunakan kadar demo tetap dan tidak pernah dicaj. | — |
| `checkout.fx_none` | No reference currency for this country; amounts are shown in MYR only. | 该国家暂无参考币种，仅显示 MYR。 | Tiada mata wang rujukan untuk negara ini; jumlah dipaparkan dalam MYR sahaja. | — |
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
| `pay.choose_method` | Choose a demo payment method | 选择演示支付方式 | Pilih kaedah pembayaran demo | — |
| `pay.method_card` | Demo card (no card details needed) | 演示银行卡（无需填写卡资料） | Kad demo (tiada butiran kad diperlukan) | 待决 Q6 |
| `pay.method_bank` | Demo online banking | 演示网上银行 | Perbankan dalam talian demo | 待决 Q6 |
| `pay.method_ewallet` | Demo e-wallet | 演示电子钱包 | E-dompet demo | 待决 Q6 |
| `pay.simulate_success` | Simulate success | 模拟支付成功 | Simulasi berjaya | — |
| `pay.simulate_failure` | Simulate failure | 模拟支付失败 | Simulasi gagal | — |
| `pay.action_hint` | No card details are collected and no money moves. Pick an outcome to see what happens. | 不收集任何银行卡资料，也不会有资金流动。选择一个结果看看会发生什么。 | Tiada butiran kad dikumpul dan tiada wang berpindah. Pilih keputusan untuk melihat apa yang berlaku. | ◆ 模拟支付 |
| `pay.expires` | Complete within {minutes} min, or the demo order is cancelled and stock is released. | 请在 {minutes} 分钟内完成，否则演示订单将取消并释放库存。 | Lengkapkan dalam {minutes} minit, atau pesanan demo dibatalkan dan stok dilepaskan. | — |
| `pay.cancel_order` | Cancel this order | 取消此订单 | Batalkan pesanan ini | 待决 Q12 |
| `pay.processing` | Recording your simulated result… | 正在记录模拟结果… | Sedang merekod keputusan simulasi… | — |
| `pay.demo_hint` | This page stands in for a payment provider. It is not a real payment page. | 本页模拟支付服务商，并非真实支付页面。 | Halaman ini menggantikan penyedia pembayaran. Ia bukan halaman pembayaran sebenar. | ★ |
| `result.success_title` | Demo payment successful | 模拟支付成功 | Pembayaran demo berjaya | — |
| `result.success_body` | No real money was taken. Your order is now "Paid (demo)". | 未扣任何真实款项。订单状态为“已支付（演示）”。 | Tiada wang sebenar diambil. Pesanan anda kini "Dibayar (demo)". | — |
| `result.points_earned` | You earned {points} demo points. | 您获得了 {points} 演示积分。 | Anda memperoleh {points} mata demo. | 仅会员 |
| `result.guest_register` | Register to earn points next time. | 注册会员，下次购物可得积分。 | Daftar untuk dapat mata pada pembelian seterusnya. | 仅游客 |
| `result.failure_title` | Demo payment failed | 模拟支付失败 | Pembayaran demo gagal | — |
| `result.failure_body` | You chose to simulate a failure. Your order is kept, and you can try again without creating a new order. | 您选择了模拟失败。订单已保留，可直接重试，不会重复下单。 | Anda memilih simulasi gagal. Pesanan anda disimpan dan anda boleh cuba lagi tanpa membuat pesanan baharu. | — |
| `result.retry` | Try payment again | 重新支付 | Cuba bayar semula | — |
| `result.cancelled` | This order was cancelled because payment was not completed in time. | 订单因未按时完成支付已取消。 | Pesanan ini dibatalkan kerana pembayaran tidak dilengkapkan tepat pada masanya. | — |
| `result.track` | View this order | 查看订单 | Lihat pesanan ini | — |
| `result.continue` | Continue shopping | 继续购物 | Teruskan membeli-belah | — |
| `result.demo_hint` | Next, the store admin will "ship" the order in the demo back office — nothing is actually sent. | 接下来管理员会在后台模拟发货——不会真的寄出。 | Seterusnya, pentadbir kedai akan "menghantar" pesanan dalam pejabat belakang demo — tiada apa yang benar-benar dihantar. | ★ |

## 7. 订单查询、确认收货与退款（P08、P09、P10）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `lookup.title` | Track your order | 查询订单 | Semak pesanan anda | — |
| `lookup.phone` | Phone number used for the order | 下单时填写的电话 | Nombor telefon yang digunakan untuk pesanan | — |
| `lookup.submit` | Find order | 查询 | Cari pesanan | — |
| `lookup.not_found` | We couldn't find an order with these details. Orders can be looked up by phone for 30 days. | 找不到与此资料相符的订单。订单只能在 30 天内凭电话查询。 | Kami tidak menemui pesanan dengan butiran ini. Pesanan boleh disemak dengan nombor telefon selama 30 hari. | 不区分“不存在”与“已匿名化” |
| `lookup.privacy_warning` | Anyone who knows both the order number and the phone number can see the full shipping details. Keep them private. | 同时知道订单号和电话的人都能看到完整收货资料，请妥善保管。 | Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Simpan dengan selamat. | — |
| `lookup.demo_hint` | Demo orders have no real parcel or tracking number. | 演示订单没有真实包裹或物流单号。 | Pesanan demo tiada bungkusan atau nombor penjejakan sebenar. | ★ |
| `order.title` | Order {orderNo} | 订单 {orderNo} | Pesanan {orderNo} | — |
| `order.current_status` | Status: | 状态： | Status: | — |
| `order.progress` | Progress | 进度 | Kemajuan | — |
| `order.amount_breakdown` | Amount details | 金额明细 | Butiran jumlah | 前台 P09 与后台 A02 共用 |
| `order.status_awaiting` | Awaiting demo payment | 待模拟支付 | Menunggu bayaran demo | — |
| `order.status_paid` | Paid (demo) | 已支付（演示） | Dibayar (demo) | — |
| `order.status_packed` | Packed (demo) | 已打包（演示） | Dibungkus (demo) | — |
| `order.status_shipped` | Shipped (demo) | 已发货（演示） | Dihantar (demo) | — |
| `order.status_completed` | Completed (demo) | 已完成（演示） | Selesai (demo) | — |
| `order.status_cancelled` | Cancelled (demo) | 已取消（演示） | Dibatalkan (demo) | — |
| `order.items` | Items | 商品 | Item | — |
| `order.unit_price` | Unit price | 单价 | Harga seunit | — |
| `order.cash_paid` | Paid in cash (demo) | 现金实付（演示） | Dibayar tunai (demo) | — |
| `order.recipient_anonymised` | Shipping details were anonymised after 30 days. | 收货资料已按 30 天规则匿名化。 | Butiran penghantaran telah dianonimkan selepas 30 hari. | — |
| `order.confirm_receipt` | Confirm receipt | 确认收货 | Sahkan penerimaan | — |
| `order.confirm_receipt_hint` | Nothing was really delivered — confirming only moves the demo order to "Completed". If you do nothing, it completes automatically 7 days after shipping. | 并没有真实包裹——确认只会把演示订单改为“已完成”。若不操作，模拟发货 7 天后自动完成。 | Tiada penghantaran sebenar — pengesahan hanya menukar pesanan demo kepada "Selesai". Jika tiada tindakan, ia selesai secara automatik 7 hari selepas penghantaran. | — |
| `order.request_refund` | Request a refund | 申请退款 | Mohon bayaran balik | — |
| `order.refund_deadline` | Refunds can be requested until {date}. | 可在 {date} 前申请退款。 | Bayaran balik boleh dimohon sehingga {date}. | 待决 Q2 |
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
| `refund.points_back` | Points returned: {points} | 返还积分：{points} | Mata dikembalikan: {points} | 仅会员 |
| `refund.points_reversed` | Points taken back: {points} | 追回积分：{points} | Mata ditarik balik: {points} | 仅会员 |
| `refund.expired_points_note` | Points that have already expired are not returned. | 已过期的积分不返还。 | Mata yang telah tamat tempoh tidak dikembalikan. | 仅会员 |
| `refund.shipping_not_refunded` | Sample shipping fees are not refunded. | 示例运费不退。 | Kos penghantaran contoh tidak dibayar balik. | — |
| `refund.coupon_not_restored` | Used coupons are not restored. | 已使用的优惠券不恢复。 | Kupon yang telah digunakan tidak dipulihkan. | — |
| `refund.submit` | Submit refund request | 提交退款申请 | Hantar permohonan bayaran balik | — |
| `refund.submit_hint` | This is a simulated refund: no real money will be returned. The amount is calculated by the system from what was paid in cash for each item. | 这是模拟退款：不会退还任何真实款项。金额由系统按每件商品的现金实付计算。 | Ini bayaran balik simulasi: tiada wang sebenar akan dikembalikan. Jumlah dikira oleh sistem berdasarkan bayaran tunai bagi setiap item. | ◆ 退款 |
| `refund.submitted` | Refund request submitted. The result will appear on this order. | 退款申请已提交，结果会显示在此订单中。 | Permohonan dihantar. Keputusan akan dipaparkan pada pesanan ini. | — |
| `refund.duplicate` | This quantity is already under review. | 该数量已在审核中。 | Kuantiti ini sudah dalam semakan. | — |
| `refund.nothing_left` | Nothing is left to refund on this order. | 此订单已无可退商品。 | Tiada lagi item untuk dibayar balik pada pesanan ini. | — |
| `refund.window_closed` | The 30-day refund period for this order has ended. | 此订单的 30 天退款期已过。 | Tempoh bayaran balik 30 hari untuk pesanan ini telah tamat. | — |
| `refund.demo_hint` | Refunds are reviewed by the admin in the demo back office. | 退款由管理员在演示后台审核。 | Bayaran balik disemak oleh pentadbir dalam pejabat belakang demo. | ★ |

## 8. 注册、登录与会员中心（P11、P12、P13）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `auth.register_title` | Create a member account | 注册会员 | Daftar akaun ahli | — |
| `auth.sms_scope` | SMS verification is currently available for Malaysia (+60) and Singapore (+65) mobile numbers only. | 短信验证目前仅支持马来西亚（+60）和新加坡（+65）手机号。 | Pengesahan SMS kini hanya untuk nombor telefon bimbit Malaysia (+60) dan Singapura (+65). | — |
| `auth.phone` | Mobile number | 手机号 | Nombor telefon bimbit | — |
| `auth.challenge` | Please complete the check below first. | 请先完成下方人机验证。 | Sila lengkapkan semakan di bawah dahulu. | — |
| `auth.send_code` | Send code | 发送验证码 | Hantar kod | — |
| `auth.code_sent` | We sent a code by SMS to {phoneMasked}. | 验证码已通过短信发送至 {phoneMasked}。 | Kami telah menghantar kod melalui SMS ke {phoneMasked}. | — |
| `auth.code` | Verification code | 验证码 | Kod pengesahan | — |
| `auth.code_wrong` | That code is incorrect or has expired. | 验证码错误或已过期。 | Kod itu salah atau telah tamat tempoh. | — |
| `auth.password` | Password | 密码 | Kata laluan | 规则待决 Q15 |
| `auth.register_submit` | Create account | 完成注册 | Cipta akaun | — |
| `auth.not_supported_country` | We can't send SMS to this number yet. You can still check out as a guest. | 暂时无法向此号码发送短信。您仍可以游客身份下单。 | Kami belum dapat menghantar SMS ke nombor ini. Anda masih boleh membuat pesanan sebagai tetamu. | — |
| `auth.sms_failed` | The SMS could not be sent, so your account was not created. You can still check out as a guest. | 短信发送失败，账号未创建。您仍可以游客身份下单。 | SMS tidak dapat dihantar, jadi akaun anda tidak dicipta. Anda masih boleh membuat pesanan sebagai tetamu. | — |
| `auth.continue_guest` | Continue as guest | 以游客身份继续 | Teruskan sebagai tetamu | — |
| `auth.claim_notice` | After you register, guest orders placed with this number in the last 30 days that are not yet anonymised are added to your account. | 注册后，近 30 天内用此号码下单且尚未匿名化的游客订单会自动归入您的账号。 | Selepas anda mendaftar, pesanan tetamu dengan nombor ini dalam 30 hari lalu yang belum dianonimkan akan ditambah ke akaun anda. | 待决 Q11 |
| `auth.register_demo_hint` | Registration sends a real SMS to your phone. Everything else remains a demo. | 注册会向您的手机发送真实短信；其余一切仍是演示。 | Pendaftaran menghantar SMS sebenar ke telefon anda. Selain itu, semuanya kekal demo. | ★ P11 |
| `auth.login_title` | Log in | 登录 | Log masuk | — |
| `auth.login_submit` | Log in | 登录 | Log masuk | — |
| `auth.login_failed` | Mobile number or password is incorrect. | 手机号或密码不正确。 | Nombor telefon bimbit atau kata laluan salah. | — |
| `auth.forgot` | Forgot password? | 忘记密码？ | Lupa kata laluan? | — |
| `auth.reset_title` | Reset password | 重设密码 | Tetapkan semula kata laluan | — |
| `auth.new_password` | New password | 新密码 | Kata laluan baharu | — |
| `auth.reset_submit` | Set new password | 设置新密码 | Tetapkan kata laluan baharu | — |
| `auth.reset_done` | Password updated. Please log in. | 密码已更新，请重新登录。 | Kata laluan dikemas kini. Sila log masuk. | — |
| `auth.login_demo_hint` | Members can see order history, demo points and coupons. | 会员可查看历史订单、演示积分与优惠券。 | Ahli boleh melihat sejarah pesanan, mata demo dan kupon. | ★ P12 |
| `account.orders` | My orders | 我的订单 | Pesanan saya | — |
| `account.orders_empty` | No orders yet. | 还没有订单。 | Tiada pesanan lagi. | — |
| `account.order_view` | View | 查看 | Lihat | 链到 P09 |
| `account.settings` | Settings | 设置 | Tetapan | — |
| `account.points_type_earned` | Earned | 获得 | Diperoleh | 积分明细类型 |
| `account.points_type_redeemed` | Used at checkout | 结账抵扣 | Digunakan semasa daftar keluar | 积分明细类型 |
| `account.points_type_expired` | Expired | 到期失效 | Tamat tempoh | 积分明细类型 |
| `account.points_type_returned` | Returned after refund | 退款返还 | Dikembalikan selepas bayaran balik | 积分明细类型 |
| `account.points_type_reversed` | Taken back after refund | 退款追回 | Ditarik balik selepas bayaran balik | 积分明细类型 |
| `account.points` | My points | 我的积分 | Mata saya | — |
| `account.points_balance` | Balance: {points} points | 余额：{points} 积分 | Baki: {points} mata | — |
| `account.points_pending` | Owed from refunds: {points} points (settled from future points) | 待抵扣：{points} 积分（以后获得的积分先偿还） | Tertunggak daripada bayaran balik: {points} mata (dijelaskan daripada mata akan datang) | — |
| `account.points_expiry` | {points} points expire on {date} | {points} 积分将于 {date} 到期 | {points} mata tamat tempoh pada {date} | — |
| `account.points_history` | Points history | 积分明细 | Sejarah mata | — |
| `account.coupons` | My coupons | 我的优惠券 | Kupon saya | 含义待决 Q13 |
| `account.coupon_used_on` | Used on order {orderNo} | 用于订单 {orderNo} | Digunakan pada pesanan {orderNo} | — |
| `account.delete` | Delete account | 注销账号 | Padam akaun | — |
| `account.delete_warning` | Deleting your account removes your mobile number and password, logs you out everywhere, and forfeits your points and unused coupons. Your orders stay in our records without your number. This cannot be undone. | 注销将删除您的手机号和密码并退出所有登录，积分余额与未用优惠券作废；订单记录保留但不再关联您的手机号。此操作不可撤销。 | Memadam akaun akan membuang nombor telefon bimbit dan kata laluan anda, log keluar di semua peranti, dan melucutkan mata serta kupon yang belum digunakan. Pesanan anda kekal dalam rekod tanpa nombor anda. Tindakan ini tidak boleh dibatalkan. | — |
| `account.delete_confirm` | Delete my account | 确认注销 | Padam akaun saya | 确认方式待决 Q15 |
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
| `privacy.h_contact` | Contact | 联系 | Hubungi | 段标题 |
| `privacy.contact_button` | Chat on WhatsApp | 通过 WhatsApp 联系 | Sembang di WhatsApp | 链接为占位 `{{WHATSAPP_CONTACT_LINK}}` |
| `privacy.collect` | For orders: recipient name, phone number, country, region, address and postcode. For members: mobile number and a securely hashed password. We do not ask for email or ID documents. | 订单：收货人姓名、电话、国家、地区、地址与邮编。会员：手机号与安全哈希后的密码。不收集邮箱或证件。 | Untuk pesanan: nama penerima, nombor telefon, negara, wilayah, alamat dan poskod. Untuk ahli: nombor telefon bimbit dan kata laluan yang di-hash dengan selamat. Kami tidak meminta e-mel atau dokumen pengenalan. | — |
| `privacy.fictional` | You may use fictional shipping details. | 收货资料可以填写虚构内容。 | Anda boleh menggunakan butiran penghantaran rekaan. | — |
| `privacy.retention_recipient` | Shipping details are deleted from the live system 30 days after payment, or 30 days after the order was placed if it was never paid. Order items, amounts and status are kept without them. | 收货资料在支付成功后 30 天（未支付订单为下单后 30 天）从在线系统删除；订单商品、金额与状态会保留，但不再含这些资料。 | Butiran penghantaran dipadam daripada sistem langsung 30 hari selepas pembayaran, atau 30 hari selepas pesanan dibuat jika ia tidak pernah dibayar. Item, jumlah dan status pesanan disimpan tanpa butiran itu. | — |
| `privacy.retention_backup` | Copies made before deletion may remain in routine database backups until those backups expire. | 删除前产生的副本可能仍留在例行数据库备份中，直到这些备份到期清除。 | Salinan yang dibuat sebelum pemadaman mungkin kekal dalam sandaran pangkalan data rutin sehingga sandaran itu tamat tempoh. | 草稿，待决 Q3；运营核实前不写天数 |
| `privacy.lookup_risk` | While shipping details are stored, anyone who knows both the order number and the phone number can view them in full. | 在资料保存期间，同时知道订单号和电话的人可以查看完整收货资料。 | Selagi butiran penghantaran disimpan, sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihatnya sepenuhnya. | — |
| `privacy.member` | Member mobile numbers are kept until you delete your account. SMS verification records are kept briefly to prevent abuse. | 会员手机号保留至您注销账号。短信验证记录短期保留，用于防滥用。 | Nombor telefon bimbit ahli disimpan sehingga anda memadam akaun. Rekod pengesahan SMS disimpan untuk tempoh singkat bagi mencegah penyalahgunaan. | — |
| `privacy.sms` | Registration and password reset send a real SMS through our SMS provider. | 注册与重设密码会通过短信服务商发送真实短信。 | Pendaftaran dan tetapan semula kata laluan menghantar SMS sebenar melalui penyedia SMS kami. | — |
| `privacy.logs` | Our logs are kept for up to 30 days and do not contain your name, full phone number, address or password. | 日志最多保留 30 天，不含您的姓名、完整电话、地址或密码。 | Log kami disimpan sehingga 30 hari dan tidak mengandungi nama, nombor telefon penuh, alamat atau kata laluan anda. | — |
| `privacy.contact` | Questions? Contact Acuven on WhatsApp. | 有疑问？请通过 WhatsApp 联系 Acuven。 | Ada soalan? Hubungi Acuven melalui WhatsApp. | 链接为占位 |
| `privacy.demo_hint` | This whole site is a demonstration; no real orders are fulfilled. | 整个网站都是演示，不履行任何真实订单。 | Seluruh laman ini adalah demonstrasi; tiada pesanan sebenar dipenuhi. | ★ |

## 10. 管理后台（A01–A07）

| 键 | English | 中文 | Bahasa Melayu | 提示 |
| --- | --- | --- | --- | --- |
| `admin.demo_banner` | Admin — demo store. Actions here never move real money or goods. | 管理后台——演示网店。这里的操作不会产生真实资金或货物流动。 | Pentadbir — kedai demo. Tindakan di sini tidak pernah memindahkan wang atau barang sebenar. | ★ 后台常驻 |
| `admin.login_title` | Admin log in | 管理员登录 | Log masuk pentadbir | — |
| `admin.username` | Username | 用户名 | Nama pengguna | — |
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
| `admin.recipient_raw` | Shipping details (original) | 原始收货资料 | Butiran penghantaran (asal) | — |
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

## 待决问题

与 [UX.md](UX.md)「待决问题」同一编号、同一内容。本稿只列出，不自行改设计或需求。

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

// 界面文案字典：每一条与 docs/UX-COPY.md 同名键的英、中、马三列逐字相同（copy.test.ts 读文档核对）。
// 只收录页面实际用到的键；新页面用到新键时从文档原样抄入，不在这里自行编写文案。

export const LANGUAGES = ["en", "zh", "ms"] as const;
export type Language = (typeof LANGUAGES)[number];
export const DEFAULT_LANGUAGE: Language = "en";

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && (LANGUAGES as readonly string[]).includes(value);
}

// UX「阅读说明」列出的不翻译字样；不是文案键，界面上可以原样出现。
export const BRAND = "ACUVEN SHOP";

type CopyEntry = Readonly<Record<Language, string>>;

export const COPY = {
  "common.demo_banner": {
    en: "Demo store — no real payments, shipping or refunds.",
    zh: "演示网店——不会真实收款、发货或退款。",
    ms: "Kedai demo — tiada bayaran, penghantaran atau bayaran balik sebenar.",
  },
  "common.demo_banner_short": {
    en: "Demo only — no real transactions",
    zh: "仅供演示——无真实交易",
    ms: "Demo sahaja — tiada transaksi sebenar",
  },
  "common.demo_badge": {
    en: "DEMO",
    zh: "演示",
    ms: "DEMO",
  },
  "common.lang_en": {
    en: "English",
    zh: "English",
    ms: "English",
  },
  "common.lang_zh": {
    en: "中文",
    zh: "中文",
    ms: "中文",
  },
  "common.lang_ms": {
    en: "Bahasa Melayu",
    zh: "Bahasa Melayu",
    ms: "Bahasa Melayu",
  },
  "common.nav_shop": {
    en: "Shop",
    zh: "商品",
    ms: "Kedai",
  },
  "common.nav_track": {
    en: "Track order",
    zh: "查询订单",
    ms: "Semak pesanan",
  },
  "common.nav_login": {
    en: "Log in",
    zh: "登录",
    ms: "Log masuk",
  },
  "common.nav_cart": {
    en: "Cart ({count})",
    zh: "购物车（{count}）",
    ms: "Troli ({count})",
  },
  "common.nav_privacy": {
    en: "Privacy",
    zh: "隐私说明",
    ms: "Privasi",
  },
  "common.nav_menu": {
    en: "Menu",
    zh: "菜单",
    ms: "Menu",
  },
  "common.footer_demo": {
    en: "Acuven demo store for showcasing online store solutions. All products, prices, stock, payments, shipping and refunds are simulated.",
    zh: "Acuven 网店方案演示站。所有商品、价格、库存、支付、运费、发货与退款均为模拟。",
    ms: "Kedai demo Acuven untuk mempamerkan penyelesaian kedai dalam talian. Semua produk, harga, stok, bayaran, penghantaran dan bayaran balik adalah simulasi.",
  },
  "common.price_myr": {
    en: "RM {amount}",
    zh: "RM {amount}",
    ms: "RM {amount}",
  },
  "common.error_retry": {
    en: "Something went wrong. Please try again.",
    zh: "出错了，请重试。",
    ms: "Berlaku ralat. Sila cuba lagi.",
  },
  "common.network_check": {
    en: "Connection lost. Checking whether your last action went through…",
    zh: "网络中断，正在确认上一步是否已完成…",
    ms: "Sambungan terputus. Menyemak sama ada tindakan terakhir anda berjaya…",
  },
  "common.copy": {
    en: "Copy",
    zh: "复制",
    ms: "Salin",
  },
  "common.copied": {
    en: "Copied",
    zh: "已复制",
    ms: "Disalin",
  },
  "common.search": {
    en: "Search",
    zh: "搜索",
    ms: "Cari",
  },
  "common.a11y_page_prev": {
    en: "Previous page",
    zh: "上一页",
    ms: "Halaman sebelumnya",
  },
  "common.a11y_page_next": {
    en: "Next page",
    zh: "下一页",
    ms: "Halaman seterusnya",
  },
  "common.a11y_qty_decrease": {
    en: "Decrease quantity",
    zh: "减少数量",
    ms: "Kurangkan kuantiti",
  },
  "common.a11y_qty_increase": {
    en: "Increase quantity",
    zh: "增加数量",
    ms: "Tambah kuantiti",
  },
  "home.hero_title": {
    en: "Try a complete online store, end to end",
    zh: "完整体验一家网店的购物流程",
    ms: "Cuba kedai dalam talian yang lengkap, dari mula hingga akhir",
  },
  "home.hero_body": {
    en: "Browse, add to cart, check out and pay with a simulated payment. Everything here is a demo.",
    zh: "浏览、加入购物车、结账并用模拟支付付款。这里的一切都是演示。",
    ms: "Layari, tambah ke troli, daftar keluar dan bayar dengan pembayaran simulasi. Semua di sini adalah demo.",
  },
  "home.hero_cta": {
    en: "Start shopping",
    zh: "开始逛逛",
    ms: "Mula membeli-belah",
  },
  "home.how_title": {
    en: "How this demo works",
    zh: "演示怎么玩",
    ms: "Cara demo ini berfungsi",
  },
  "home.how_1": {
    en: "Pick products and options",
    zh: "挑选商品和规格",
    ms: "Pilih produk dan pilihan",
  },
  "home.how_2": {
    en: "Check out: Malaysian and Singapore mobile numbers are verified by SMS and continue as members; other numbers check out as guests",
    zh: "结账：马来西亚、新加坡手机号经短信验证后以会员身份继续，其他号码以游客身份结账",
    ms: "Daftar keluar: nombor telefon bimbit Malaysia dan Singapura disahkan melalui SMS dan diteruskan sebagai ahli; nombor lain mendaftar keluar sebagai tetamu",
  },
  "home.how_3": {
    en: 'Choose "success" or "failure" on the simulated payment',
    zh: "在模拟支付中选择“成功”或“失败”",
    ms: 'Pilih "berjaya" atau "gagal" pada pembayaran simulasi',
  },
  "home.how_4": {
    en: "Track the order, confirm receipt or request a refund",
    zh: "查询订单、确认收货或申请退款",
    ms: "Semak pesanan, sahkan penerimaan atau mohon bayaran balik",
  },
  "home.categories": {
    en: "Shop by category",
    zh: "按分类浏览",
    ms: "Beli mengikut kategori",
  },
  "home.featured": {
    en: "Featured demo products",
    zh: "精选示例商品",
    ms: "Produk demo pilihan",
  },
  "home.demo_hint": {
    en: "Products, prices and stock are samples. Stock resets every day.",
    zh: "商品、价格与库存均为示例，库存每天重置。",
    ms: "Produk, harga dan stok adalah contoh. Stok ditetapkan semula setiap hari.",
  },
  "list.title": {
    en: "All products",
    zh: "全部商品",
    ms: "Semua produk",
  },
  "list.search_placeholder": {
    en: "Search products",
    zh: "搜索商品",
    ms: "Cari produk",
  },
  "list.filter_title": {
    en: "Filter",
    zh: "筛选",
    ms: "Tapis",
  },
  "list.filter_category": {
    en: "Category",
    zh: "分类",
    ms: "Kategori",
  },
  "list.filter_apply": {
    en: "Show results",
    zh: "查看结果",
    ms: "Tunjuk hasil",
  },
  "list.filter_clear": {
    en: "Clear filters",
    zh: "清除筛选",
    ms: "Kosongkan penapis",
  },
  "list.sort": {
    en: "Sort",
    zh: "排序",
    ms: "Susun",
  },
  "list.sort_newest": {
    en: "Newest",
    zh: "最新",
    ms: "Terbaru",
  },
  "list.sort_price_asc": {
    en: "Price: low to high",
    zh: "价格从低到高",
    ms: "Harga: rendah ke tinggi",
  },
  "list.sort_price_desc": {
    en: "Price: high to low",
    zh: "价格从高到低",
    ms: "Harga: tinggi ke rendah",
  },
  "list.results_count": {
    en: "{count} products",
    zh: "共 {count} 件商品",
    ms: "{count} produk",
  },
  "list.price_from": {
    en: "From RM {amount}",
    zh: "RM {amount} 起",
    ms: "Dari RM {amount}",
  },
  "list.out_of_stock": {
    en: "Out of stock today",
    zh: "今日已售罄",
    ms: "Kehabisan stok hari ini",
  },
  "list.load_more": {
    en: "Load more",
    zh: "加载更多",
    ms: "Muat lagi",
  },
  "list.empty": {
    en: "No products match your search.",
    zh: "没有符合条件的商品。",
    ms: "Tiada produk sepadan dengan carian anda.",
  },
  "list.demo_hint": {
    en: "Sample products for demonstration only; none are for real sale.",
    zh: "示例商品仅供演示，均不真实出售。",
    ms: "Produk contoh untuk demo sahaja; tiada yang dijual secara sebenar.",
  },
  "detail.options": {
    en: "Choose options",
    zh: "选择规格",
    ms: "Pilih pilihan",
  },
  "detail.quantity": {
    en: "Quantity",
    zh: "数量",
    ms: "Kuantiti",
  },
  "detail.stock_left": {
    en: "{count} left today (demo stock)",
    zh: "今日剩余 {count} 件（示例库存）",
    ms: "Tinggal {count} hari ini (stok demo)",
  },
  "detail.add_to_cart": {
    en: "Add to cart",
    zh: "加入购物车",
    ms: "Tambah ke troli",
  },
  "detail.added": {
    en: "Added to cart",
    zh: "已加入购物车",
    ms: "Ditambah ke troli",
  },
  "detail.view_cart": {
    en: "View cart",
    zh: "查看购物车",
    ms: "Lihat troli",
  },
  "detail.select_all_options": {
    en: "Please choose all options first.",
    zh: "请先选择全部规格。",
    ms: "Sila pilih semua pilihan dahulu.",
  },
  "detail.max_per_order": {
    en: "Limit {count} per order",
    zh: "每单限购 {count} 件",
    ms: "Had {count} unit bagi setiap pesanan",
  },
  "detail.limit_reached": {
    en: "You already have the maximum of {count} of this product in your cart.",
    zh: "购物车中此商品已达每单限购 {count} 件。",
    ms: "Troli anda sudah mempunyai jumlah maksimum {count} untuk produk ini.",
  },
  "detail.cart_full": {
    en: "Your cart can hold up to {count} different items. Remove one to add another.",
    zh: "购物车最多可放 {count} 种商品规格，请先移除一项再加入。",
    ms: "Troli anda boleh memuatkan sehingga {count} item berbeza. Buang satu untuk menambah yang lain.",
  },
  "detail.description": {
    en: "Description",
    zh: "商品描述",
    ms: "Penerangan",
  },
  "detail.english_only": {
    en: "English only",
    zh: "仅英文",
    ms: "Bahasa Inggeris sahaja",
  },
  "detail.a11y_image": {
    en: "Image {n} of {count}",
    zh: "第 {n} 张图，共 {count} 张",
    ms: "Imej {n} daripada {count}",
  },
  "detail.demo_hint": {
    en: "This is a sample product. Adding it to your cart does not reserve stock.",
    zh: "这是示例商品；加入购物车不会预留库存。",
    ms: "Ini produk contoh. Menambahnya ke troli tidak menempah stok.",
  },
  "cart.title": {
    en: "Your cart",
    zh: "购物车",
    ms: "Troli anda",
  },
  "cart.empty": {
    en: "Your cart is empty.",
    zh: "购物车是空的。",
    ms: "Troli anda kosong.",
  },
  "cart.remove": {
    en: "Remove",
    zh: "移除",
    ms: "Buang",
  },
  "cart.subtotal": {
    en: "Item subtotal",
    zh: "商品小计",
    ms: "Jumlah kecil item",
  },
  "cart.shipping_later": {
    en: "Shipping is calculated at checkout.",
    zh: "运费在结账时计算。",
    ms: "Kos penghantaran dikira semasa daftar keluar.",
  },
  "cart.price_recheck": {
    en: "Prices and stock are confirmed at checkout.",
    zh: "价格与库存以结账时为准。",
    ms: "Harga dan stok disahkan semasa daftar keluar.",
  },
  "cart.item_changed": {
    en: "Some items changed in price or availability. Please review.",
    zh: "部分商品的价格或库存已变化，请核对。",
    ms: "Sesetengah item telah berubah harga atau ketersediaan. Sila semak.",
  },
  "cart.over_limit": {
    en: "This product is limited to {count} per order. Please reduce the quantity.",
    zh: "此商品每单限购 {count} 件，请减少数量。",
    ms: "Produk ini dihadkan kepada {count} unit bagi setiap pesanan. Sila kurangkan kuantiti.",
  },
  "cart.checkout": {
    en: "Check out",
    zh: "去结账",
    ms: "Daftar keluar",
  },
  "cart.continue": {
    en: "Continue shopping",
    zh: "继续购物",
    ms: "Teruskan membeli-belah",
  },
  "cart.demo_hint": {
    en: "Your cart is saved in this browser only. Nothing is charged.",
    zh: "购物车只保存在本浏览器，不会扣款。",
    ms: "Troli anda disimpan dalam pelayar ini sahaja. Tiada caj dikenakan.",
  },
  "checkout.recipient_title": {
    en: "Shipping details",
    zh: "收货资料",
    ms: "Butiran penghantaran",
  },
  "pay.title": {
    en: "Simulated payment",
    zh: "模拟支付",
    ms: "Pembayaran simulasi",
  },
  "pay.order_no": {
    en: "Order number",
    zh: "订单号",
    ms: "Nombor pesanan",
  },
  "pay.save_order_no": {
    en: "Save this order number. You'll need it with your phone number to track the order.",
    zh: "请保存订单号，查询订单时需要它和您的电话号码。",
    ms: "Simpan nombor pesanan ini. Anda perlukannya bersama nombor telefon untuk menyemak pesanan.",
  },
  "pay.amount_due": {
    en: "Amount due (demo)",
    zh: "应付金额（演示）",
    ms: "Jumlah perlu dibayar (demo)",
  },
  "pay.guest_access": {
    en: "For your privacy, only this browser can open this order's payment and result pages, for 30 minutes after the order was placed.",
    zh: "为保护您的资料，只有本浏览器能在下单后 30 分钟内打开此订单的支付与结果页。",
    ms: "Demi privasi anda, hanya pelayar ini boleh membuka halaman pembayaran dan keputusan pesanan ini, selama 30 minit selepas pesanan dibuat.",
  },
  "pay.session_expired": {
    en: "This page is no longer available in this browser. To view the order, confirm receipt or request a refund, track it with your order number and phone number.",
    zh: "本浏览器已无法打开此页面。如需查看订单、确认收货或申请退款，请凭订单号和电话查询订单。",
    ms: "Halaman ini tidak lagi tersedia dalam pelayar ini. Untuk melihat pesanan, mengesahkan penerimaan atau memohon bayaran balik, semak pesanan dengan nombor pesanan dan nombor telefon anda.",
  },
  "pay.choose_method": {
    en: "Choose a demo payment method",
    zh: "选择演示支付方式",
    ms: "Pilih kaedah pembayaran demo",
  },
  "pay.method_card": {
    en: "Demo credit/debit card (no card number needed)",
    zh: "演示信用卡/借记卡（无需输入卡号）",
    ms: "Kad kredit/debit demo (tiada nombor kad diperlukan)",
  },
  "pay.method_bank": {
    en: "Demo online banking",
    zh: "演示网上银行",
    ms: "Perbankan dalam talian demo",
  },
  "pay.method_ewallet": {
    en: "Demo e-wallet",
    zh: "演示电子钱包",
    ms: "E-dompet demo",
  },
  "pay.simulate_success": {
    en: "Simulate success",
    zh: "模拟支付成功",
    ms: "Simulasi berjaya",
  },
  "pay.simulate_failure": {
    en: "Simulate failure",
    zh: "模拟支付失败",
    ms: "Simulasi gagal",
  },
  "pay.action_hint": {
    en: "No card details are collected and no money moves. Pick an outcome to see what happens.",
    zh: "不收集任何银行卡资料，也不会有资金流动。选择一个结果看看会发生什么。",
    ms: "Tiada butiran kad dikumpul dan tiada wang berpindah. Pilih keputusan untuk melihat apa yang berlaku.",
  },
  "pay.expires": {
    en: "Complete within {minutes} min, or the demo order is cancelled and stock is released.",
    zh: "请在 {minutes} 分钟内完成，否则演示订单将取消并释放库存。",
    ms: "Lengkapkan dalam {minutes} minit, atau pesanan demo dibatalkan dan stok dilepaskan.",
  },
  "pay.cancel_order": {
    en: "Cancel this order",
    zh: "取消此订单",
    ms: "Batalkan pesanan ini",
  },
  "pay.cancel_confirm": {
    en: "Cancel this demo order? The held stock, coupon and points will be released. This cannot be undone.",
    zh: "确定取消此演示订单？保留的库存、优惠券与积分将被释放，此操作不可撤销。",
    ms: "Batalkan pesanan demo ini? Stok, kupon dan mata yang ditahan akan dilepaskan. Tindakan ini tidak boleh dibatalkan.",
  },
  "pay.cancel_confirm_yes": {
    en: "Yes, cancel order",
    zh: "确认取消",
    ms: "Ya, batalkan pesanan",
  },
  "pay.cancel_confirm_no": {
    en: "Keep order",
    zh: "保留订单",
    ms: "Kekalkan pesanan",
  },
  "pay.processing": {
    en: "Recording your simulated result…",
    zh: "正在记录模拟结果…",
    ms: "Sedang merekod keputusan simulasi…",
  },
  "pay.demo_hint": {
    en: "This page stands in for a payment provider. It is not a real payment page.",
    zh: "本页模拟支付服务商，并非真实支付页面。",
    ms: "Halaman ini menggantikan penyedia pembayaran. Ia bukan halaman pembayaran sebenar.",
  },
  "result.success_title": {
    en: "Demo payment successful",
    zh: "模拟支付成功",
    ms: "Pembayaran demo berjaya",
  },
  "result.success_body": {
    en: 'No real money was taken. Your order is now "Paid (demo)".',
    zh: "未扣任何真实款项。订单状态为“已支付（演示）”。",
    ms: 'Tiada wang sebenar diambil. Pesanan anda kini "Dibayar (demo)".',
  },
  "result.guest_next": {
    en: 'To view this order later, confirm receipt or request a refund, use "Track order" with your order number and phone number.',
    zh: "之后如需查看此订单、确认收货或申请退款，请在“查询订单”中输入订单号和电话。",
    ms: 'Untuk melihat pesanan ini kemudian, mengesahkan penerimaan atau memohon bayaran balik, gunakan "Semak pesanan" dengan nombor pesanan dan nombor telefon anda.',
  },
  "result.failure_title": {
    en: "Demo payment failed",
    zh: "模拟支付失败",
    ms: "Pembayaran demo gagal",
  },
  "result.failure_body": {
    en: "You chose to simulate a failure. Your order is kept, and you can try again without creating a new order.",
    zh: "您选择了模拟失败。订单已保留，可直接重试，不会重复下单。",
    ms: "Anda memilih simulasi gagal. Pesanan anda disimpan dan anda boleh cuba lagi tanpa membuat pesanan baharu.",
  },
  "result.retry": {
    en: "Try payment again",
    zh: "重新支付",
    ms: "Cuba bayar semula",
  },
  "result.cancelled": {
    en: "This order was cancelled because payment was not completed in time.",
    zh: "订单因未按时完成支付已取消。",
    ms: "Pesanan ini dibatalkan kerana pembayaran tidak dilengkapkan tepat pada masanya.",
  },
  "result.cancelled_by_you": {
    en: "You cancelled this demo order. The held stock, coupon and points have been released.",
    zh: "您已取消此演示订单，保留的库存、优惠券与积分已释放。",
    ms: "Anda telah membatalkan pesanan demo ini. Stok, kupon dan mata yang ditahan telah dilepaskan.",
  },
  "result.continue": {
    en: "Continue shopping",
    zh: "继续购物",
    ms: "Teruskan membeli-belah",
  },
  "result.demo_hint": {
    en: 'Next, the store admin will "ship" the order in the demo back office — nothing is actually sent.',
    zh: "接下来管理员会在后台模拟发货——不会真的寄出。",
    ms: 'Seterusnya, pentadbir kedai akan "menghantar" pesanan dalam pejabat belakang demo — tiada apa yang benar-benar dihantar.',
  },
  "privacy.title": {
    en: "Privacy",
    zh: "隐私说明",
    ms: "Privasi",
  },
  "privacy.intro": {
    en: "This site is a demo store. We collect only what is needed to run the demo, and nothing is really paid for or delivered.",
    zh: "本站是演示网店，只收集完成演示所需的资料，不发生真实付款或寄送。",
    ms: "Laman ini ialah kedai demo. Kami hanya mengumpul apa yang diperlukan untuk demo, dan tiada apa yang benar-benar dibayar atau dihantar.",
  },
  "privacy.h_collect": {
    en: "What we collect",
    zh: "收集什么",
    ms: "Apa yang kami kumpul",
  },
  "privacy.h_retention": {
    en: "How long we keep it",
    zh: "保留多久",
    ms: "Berapa lama kami menyimpannya",
  },
  "privacy.h_access": {
    en: "Who can see it",
    zh: "谁能看到",
    ms: "Siapa yang boleh melihatnya",
  },
  "privacy.h_sms_logs": {
    en: "SMS and logs",
    zh: "短信与日志",
    ms: "SMS dan log",
  },
  "privacy.collect": {
    en: "For orders: recipient name, phone number, country, region, address and postcode. For members: mobile number and, if you set one, a securely hashed password. We do not ask for email or ID documents.",
    zh: "订单：收货人姓名、电话、国家、地区、地址与邮编。会员：手机号，以及（如已设置）安全哈希后的密码。不收集邮箱或证件。",
    ms: "Untuk pesanan: nama penerima, nombor telefon, negara, wilayah, alamat dan poskod. Untuk ahli: nombor telefon bimbit dan, jika ditetapkan, kata laluan yang di-hash dengan selamat. Kami tidak meminta e-mel atau dokumen pengenalan.",
  },
  "privacy.fictional": {
    en: "You may use fictional shipping details. The exception is when SMS verification is on: a Malaysian or Singapore mobile number entered at checkout must then be able to receive our verification SMS.",
    zh: "收货资料可以填写虚构内容。唯一例外是短信验证开启时，结账时填写的马来西亚或新加坡手机号须能收到验证短信。",
    ms: "Anda boleh menggunakan butiran penghantaran rekaan. Pengecualiannya ialah apabila pengesahan SMS dihidupkan: nombor telefon bimbit Malaysia atau Singapura yang dimasukkan semasa daftar keluar mesti boleh menerima SMS pengesahan kami.",
  },
  "privacy.retention_recipient": {
    en: "Shipping details are kept long-term together with the order's items, amounts and status. They are not deleted or anonymised, including after a member account is deleted.",
    zh: "收货资料与订单商品、金额和状态一起长期保存，不删除、不匿名化；会员注销后也同样保留。",
    ms: "Butiran penghantaran disimpan untuk jangka panjang bersama item, jumlah dan status pesanan. Ia tidak dipadam atau dianonimkan, termasuk selepas akaun ahli dipadam.",
  },
  "privacy.member": {
    en: "Member mobile numbers, including accounts created automatically at checkout, are kept until you delete your account; inactive accounts are not deleted automatically. SMS verification records are kept briefly to prevent abuse.",
    zh: "会员手机号（含结账时自动注册的账号）保留至您注销账号，长期未登录也不会自动注销。短信验证记录短期保留，用于防滥用。",
    ms: "Nombor telefon bimbit ahli, termasuk akaun yang dicipta secara automatik semasa daftar keluar, disimpan sehingga anda memadam akaun; akaun yang tidak aktif tidak dipadam secara automatik. Rekod pengesahan SMS disimpan untuk tempoh singkat bagi mencegah penyalahgunaan.",
  },
  "privacy.member_backup": {
    en: "After you delete your account, copies of your mobile number and password hash made before the deletion may remain in our database backups for a period that has not yet been determined.",
    zh: "注销账号后，注销前产生的手机号与密码哈希副本可能在数据库备份中保留一段尚未确定的时间。",
    ms: "Selepas anda memadam akaun, salinan nombor telefon bimbit dan hash kata laluan anda yang dibuat sebelum pemadaman mungkin kekal dalam sandaran pangkalan data untuk suatu tempoh yang belum ditentukan.",
  },
  "privacy.browser_access": {
    en: "After you place a guest order or look up an order, this browser gets access to that order for 30 minutes. After 30 minutes, or in another browser, look the order up again with its order number and phone number.",
    zh: "游客下单或查询订单后，本浏览器获得对该订单 30 分钟的访问。30 分钟后，或在其他浏览器上，请凭订单号和电话重新查询该订单。",
    ms: "Selepas anda membuat pesanan tetamu atau menyemak pesanan, pelayar ini mendapat akses kepada pesanan itu selama 30 minit. Selepas 30 minit, atau dalam pelayar lain, semak pesanan itu semula dengan nombor pesanan dan nombor telefonnya.",
  },
  "privacy.lookup_risk": {
    en: "Anyone who knows both the order number and the phone number can view the full shipping details. Because shipping details are kept long-term, this stays possible for as long as the order exists.",
    zh: "同时知道订单号和电话的人可以查看完整收货资料。由于收货资料长期保存，只要订单存在，这一点就一直成立。",
    ms: "Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Oleh sebab butiran penghantaran disimpan untuk jangka panjang, ini kekal boleh berlaku selagi pesanan wujud.",
  },
  "privacy.sms": {
    en: "When SMS verification is on, checkout verification for Malaysian and Singapore numbers, registration, SMS login, password reset and account deletion send a real SMS through our SMS provider.",
    zh: "短信验证开启时，马新号码结账验证、注册、短信登录、重设密码与注销确认会通过短信服务商发送真实短信。",
    ms: "Apabila pengesahan SMS dihidupkan, pengesahan daftar keluar untuk nombor Malaysia dan Singapura, pendaftaran, log masuk SMS, tetapan semula kata laluan dan pemadaman akaun menghantar SMS sebenar melalui penyedia SMS kami.",
  },
  "privacy.sms_toggle": {
    en: "We can switch SMS verification off. While it is off, no SMS is sent: visitors who are not logged in check out as guests whatever their number; registration, SMS login and password reset are paused; and members confirm account deletion with their password, or while logged in if they have not set one.",
    zh: "我们可以关闭短信验证。关闭期间不发送任何短信：未登录的访客无论号码都以游客身份结账；注册、短信登录与重设密码暂停；会员以密码确认注销，未设密码者在登录状态下确认。",
    ms: "Kami boleh mematikan pengesahan SMS. Semasa ia dimatikan, tiada SMS dihantar: pelawat yang tidak log masuk mendaftar keluar sebagai tetamu tanpa mengira nombor mereka; pendaftaran, log masuk SMS dan tetapan semula kata laluan digantung; dan ahli mengesahkan pemadaman akaun dengan kata laluan mereka, atau semasa log masuk jika belum menetapkannya.",
  },
  "privacy.logs": {
    en: "Our logs are kept for up to 30 days and do not contain your name, full phone number, address or password.",
    zh: "日志最多保留 30 天，不含您的姓名、完整电话、地址或密码。",
    ms: "Log kami disimpan sehingga 30 hari dan tidak mengandungi nama, nombor telefon penuh, alamat atau kata laluan anda.",
  },
  "privacy.demo_hint": {
    en: "This whole site is a demonstration; no real orders are fulfilled.",
    zh: "整个网站都是演示，不履行任何真实订单。",
    ms: "Seluruh laman ini adalah demonstrasi; tiada pesanan sebenar dipenuhi.",
  },
} as const satisfies Record<string, CopyEntry>;

export type CopyKey = keyof typeof COPY;

export type CopyVars = Readonly<Record<string, string | number>>;

// 把文案里的 {变量} 换成给定的值。三种语言保留同名变量（UX-COPY「约定」），所以同一组值对三列都适用。
// 缺少某个变量时抛错，而不是把 {变量} 原样显示给访客。
export function formatCopy(template: string, vars: CopyVars = {}): string {
  return template.replace(/\{(\w+)\}/g, (_match, name: string) => {
    const value = vars[name];
    if (value === undefined) {
      throw new Error(`missing copy variable: ${name}`);
    }
    return String(value);
  });
}

export function translate(language: Language, key: CopyKey, vars?: CopyVars): string {
  return formatCopy(COPY[key][language], vars);
}

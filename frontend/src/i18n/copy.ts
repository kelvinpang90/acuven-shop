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
  "common.whatsapp_cta": {
    en: "Interested in a store like this? Chat with Acuven on WhatsApp",
    zh: "想要这样的网店？通过 WhatsApp 联系 Acuven",
    ms: "Berminat dengan kedai seperti ini? Hubungi Acuven melalui WhatsApp",
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
  "common.save": {
    en: "Save",
    zh: "保存",
    ms: "Simpan",
  },
  "common.back": {
    en: "Back",
    zh: "返回",
    ms: "Kembali",
  },
  "common.create": {
    en: "New",
    zh: "新建",
    ms: "Baharu",
  },
  "common.rate_limited": {
    en: "Too many attempts. Please wait and try again later.",
    zh: "尝试次数过多，请稍后再试。",
    ms: "Terlalu banyak cubaan. Sila tunggu dan cuba lagi kemudian.",
  },
  "common.service_unavailable": {
    en: "This feature is temporarily unavailable. You can keep browsing products.",
    zh: "此功能暂不可用，您仍可继续浏览商品。",
    ms: "Ciri ini tidak tersedia buat sementara. Anda masih boleh melayari produk.",
  },
  "common.fx_reference": {
    en: "≈ {currency} {amount} (demo rate, for reference only)",
    zh: "≈ {currency} {amount}（演示汇率，仅供参考）",
    ms: "≈ {currency} {amount} (kadar demo, untuk rujukan sahaja)",
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
  "checkout.title": {
    en: "Checkout",
    zh: "结账",
    ms: "Daftar keluar",
  },
  "checkout.phone_step_title": {
    en: "Your mobile number",
    zh: "您的手机号",
    ms: "Nombor telefon bimbit anda",
  },
  "checkout.phone_notice": {
    en: "Malaysian (+60) and Singapore (+65) numbers will receive a verification SMS and be registered as a member automatically (or logged in if already registered). Your mobile number is kept until you delete your account, which you can do anytime in My account. Other numbers check out as a guest without SMS.",
    zh: "马来西亚（+60）和新加坡（+65）号码会收到验证短信，并自动注册为会员（已注册则直接登录）。手机号保留至您注销账号，您可随时在会员中心注销。其他号码不发短信，以游客身份结账。",
    ms: "Nombor Malaysia (+60) dan Singapura (+65) akan menerima SMS pengesahan dan didaftarkan sebagai ahli secara automatik (atau dilog masuk jika sudah berdaftar). Nombor telefon bimbit anda disimpan sehingga anda memadam akaun, yang boleh dibuat pada bila-bila masa di Akaun saya. Nombor lain mendaftar keluar sebagai tetamu tanpa SMS.",
  },
  "checkout.phone_step_hint": {
    en: "Choose the country code, or start with + to type it yourself.",
    zh: "请选择国家码，或以 + 开头自行输入。",
    ms: "Pilih kod negara, atau mulakan dengan + untuk menaipnya sendiri.",
  },
  "checkout.phone_continue": {
    en: "Continue",
    zh: "继续",
    ms: "Teruskan",
  },
  "checkout.phone_change": {
    en: "Change",
    zh: "更改",
    ms: "Tukar",
  },
  "checkout.login_password": {
    en: "Already set a password? Log in",
    zh: "已设置密码？直接登录",
    ms: "Sudah menetapkan kata laluan? Log masuk",
  },
  "checkout.guest_other_country": {
    en: "This number is outside Malaysia and Singapore, so no SMS is sent and you'll check out as a guest.",
    zh: "此号码不属于马来西亚或新加坡，不会发送短信，将以游客身份结账。",
    ms: "Nombor ini di luar Malaysia dan Singapura, jadi tiada SMS dihantar dan anda akan mendaftar keluar sebagai tetamu.",
  },
  "checkout.phone_notice_sms_off": {
    en: "SMS verification is currently switched off, so if you continue without logging in, you'll check out as a guest whatever your number, and no SMS is sent. Your number becomes this order's phone number and, with the order number, lets you track the order.",
    zh: "短信验证目前已关闭，不登录继续结账时，无论哪个号码都将以游客身份结账，不会发送短信。您的号码即本订单的电话，与订单号一起用于查询订单。",
    ms: "Pengesahan SMS kini dimatikan, jadi jika anda meneruskan tanpa log masuk, anda akan mendaftar keluar sebagai tetamu tanpa mengira nombor anda, dan tiada SMS dihantar. Nombor anda menjadi nombor telefon pesanan ini dan, bersama nombor pesanan, membolehkan anda menjejak pesanan.",
  },
  "checkout.guest_sms_off": {
    en: "SMS verification is switched off, so you'll check out as a guest.",
    zh: "短信验证已关闭，将以游客身份结账。",
    ms: "Pengesahan SMS dimatikan, jadi anda akan mendaftar keluar sebagai tetamu.",
  },
  "checkout.guest_notice": {
    en: "You are checking out as a guest. Coupons and points are for members, who register with a Malaysian or Singapore mobile number.",
    zh: "您正以游客身份结账。优惠券与积分仅限会员使用；会员须以马来西亚或新加坡手机号注册。",
    ms: "Anda mendaftar keluar sebagai tetamu. Kupon dan mata untuk ahli, yang mendaftar dengan nombor telefon bimbit Malaysia atau Singapura.",
  },
  "checkout.recipient_title": {
    en: "Shipping details",
    zh: "收货资料",
    ms: "Butiran penghantaran",
  },
  "checkout.name": {
    en: "Recipient name",
    zh: "收货人姓名",
    ms: "Nama penerima",
  },
  "checkout.phone": {
    en: "Phone number",
    zh: "电话",
    ms: "Nombor telefon",
  },
  "checkout.phone_invalid": {
    en: "This phone number doesn't look valid. Please check the country code and number.",
    zh: "该电话号码格式不符，请检查国家码和号码。",
    ms: "Nombor telefon ini tidak kelihatan sah. Sila semak kod negara dan nombor.",
  },
  "checkout.phone_lookup_hint": {
    en: "You'll need this phone number and your order number to track the order.",
    zh: "查询订单需要此电话号码和订单号。",
    ms: "Anda perlukan nombor telefon ini dan nombor pesanan untuk menyemak pesanan.",
  },
  "checkout.country": {
    en: "Country",
    zh: "国家/地区",
    ms: "Negara",
  },
  "checkout.state_my": {
    en: "State",
    zh: "州属",
    ms: "Negeri",
  },
  "checkout.region": {
    en: "State / province / region",
    zh: "州/省/地区",
    ms: "Negeri / wilayah",
  },
  "checkout.address": {
    en: "Address",
    zh: "地址",
    ms: "Alamat",
  },
  "checkout.postcode": {
    en: "Postcode",
    zh: "邮编",
    ms: "Poskod",
  },
  "checkout.form_notice": {
    en: "Demo only: you will not be charged and nothing will be shipped. You may use fictional shipping details. Shipping details are used only for this demo and are kept long-term with the order.",
    zh: "仅为演示：不会真实扣款，也不会真实发货。收货资料可填写虚构内容，仅用于本次演示，并会随订单长期保存。",
    ms: "Demo sahaja: anda tidak akan dicaj dan tiada barang akan dihantar. Anda boleh guna butiran penghantaran rekaan. Butiran penghantaran digunakan untuk demo ini sahaja dan disimpan untuk jangka panjang bersama pesanan.",
  },
  "checkout.form_notice_link": {
    en: "How we handle your details",
    zh: "我们如何处理您的资料",
    ms: "Cara kami mengendalikan butiran anda",
  },
  "checkout.coupon_members_only": {
    en: "Coupons are for members only.",
    zh: "优惠券仅限会员使用。",
    ms: "Kupon untuk ahli sahaja.",
  },
  "checkout.points_guest": {
    en: "Guests don't earn points. Members with a Malaysian or Singapore mobile number earn 1 point for every RM1 paid.",
    zh: "游客不累积积分。以马来西亚或新加坡手机号注册的会员每实付 RM1 得 1 积分。",
    ms: "Tetamu tidak mengumpul mata. Ahli dengan nombor telefon bimbit Malaysia atau Singapura mendapat 1 mata bagi setiap RM1 dibayar.",
  },
  "checkout.summary_title": {
    en: "Order summary",
    zh: "订单摘要",
    ms: "Ringkasan pesanan",
  },
  "checkout.summary_coupon": {
    en: "Coupon discount",
    zh: "优惠券抵扣",
    ms: "Diskaun kupon",
  },
  "checkout.summary_points": {
    en: "Points discount",
    zh: "积分抵扣",
    ms: "Diskaun mata",
  },
  "checkout.summary_shipping": {
    en: "Sample shipping",
    zh: "示例运费",
    ms: "Kos penghantaran contoh",
  },
  "checkout.summary_total": {
    en: "Total (MYR)",
    zh: "合计（MYR）",
    ms: "Jumlah (MYR)",
  },
  "checkout.fx_note": {
    en: "Reference amounts use a fixed demo rate and are never charged.",
    zh: "参考金额按固定演示汇率换算，不会收取。",
    ms: "Jumlah rujukan menggunakan kadar demo tetap dan tidak pernah dicaj.",
  },
  "checkout.fx_none": {
    en: "No reference currency for this country; amounts are shown in MYR only.",
    zh: "该国家暂无参考币种，仅显示 MYR。",
    ms: "Tiada mata wang rujukan untuk negara ini; jumlah dipaparkan dalam MYR sahaja.",
  },
  "checkout.place_order": {
    en: "Place demo order",
    zh: "提交演示订单",
    ms: "Buat pesanan demo",
  },
  "checkout.place_order_hint": {
    en: "Placing the order holds stock for 15 minutes while you complete the simulated payment. No money is taken.",
    zh: "提交后为您保留库存 15 分钟以完成模拟支付，不会扣任何钱。",
    ms: "Membuat pesanan menahan stok selama 15 minit sementara anda melengkapkan pembayaran simulasi. Tiada wang diambil.",
  },
  "checkout.submitting": {
    en: "Placing your order…",
    zh: "正在提交订单…",
    ms: "Sedang membuat pesanan…",
  },
  "checkout.demo_hint": {
    en: "Everything on this page is a demo. Shipping fees are samples set per country.",
    zh: "本页均为演示；运费为按国家设定的示例。",
    ms: "Semua di halaman ini adalah demo. Kos penghantaran adalah contoh mengikut negara.",
  },
  "auth.phone": {
    en: "Mobile number",
    zh: "手机号",
    ms: "Nombor telefon bimbit",
  },
  "auth.password": {
    en: "Password",
    zh: "密码",
    ms: "Kata laluan",
  },
  "auth.login_submit": {
    en: "Log in",
    zh: "登录",
    ms: "Log masuk",
  },
  "auth.sms_scope": {
    en: "SMS verification is currently available for Malaysia (+60) and Singapore (+65) mobile numbers only.",
    zh: "短信验证目前仅支持马来西亚（+60）和新加坡（+65）手机号。",
    ms: "Pengesahan SMS kini hanya untuk nombor telefon bimbit Malaysia (+60) dan Singapura (+65).",
  },
  "auth.challenge": {
    en: "Please complete the check below first.",
    zh: "请先完成下方人机验证。",
    ms: "Sila lengkapkan semakan di bawah dahulu.",
  },
  "auth.challenge_failed": {
    en: "The check wasn't completed. Please try again.",
    zh: "人机验证未完成，请重试。",
    ms: "Semakan tidak dilengkapkan. Sila cuba lagi.",
  },
  "auth.send_code": {
    en: "Send code",
    zh: "发送验证码",
    ms: "Hantar kod",
  },
  "auth.resend_code": {
    en: "Send a new code",
    zh: "重新发送验证码",
    ms: "Hantar kod baharu",
  },
  "auth.code_sent": {
    en: "We sent a code by SMS to {phoneMasked}.",
    zh: "验证码已通过短信发送至 {phoneMasked}。",
    ms: "Kami telah menghantar kod melalui SMS ke {phoneMasked}.",
  },
  "auth.code": {
    en: "Verification code",
    zh: "验证码",
    ms: "Kod pengesahan",
  },
  "auth.verify_submit": {
    en: "Verify",
    zh: "验证",
    ms: "Sahkan",
  },
  "auth.code_wrong": {
    en: "That code is incorrect or has expired.",
    zh: "验证码错误或已过期。",
    ms: "Kod itu salah atau telah tamat tempoh.",
  },
  "auth.not_supported_country": {
    en: "We can't send SMS to this number yet. You can still check out as a guest.",
    zh: "暂时无法向此号码发送短信。您仍可以游客身份下单。",
    ms: "Kami belum dapat menghantar SMS ke nombor ini. Anda masih boleh membuat pesanan sebagai tetamu.",
  },
  "auth.sms_failed": {
    en: "The SMS could not be sent, so no account was created or changed. You can still check out as a guest.",
    zh: "短信发送失败，账号未创建或更改。您仍可以游客身份下单。",
    ms: "SMS tidak dapat dihantar, jadi tiada akaun dicipta atau diubah. Anda masih boleh membuat pesanan sebagai tetamu.",
  },
  "auth.sms_paused": {
    en: "SMS is currently switched off, so registration, SMS login and password reset are unavailable. Members who have set a password can still log in with it.",
    zh: "短信目前已关闭，暂不能注册、短信登录或重设密码。已设置密码的会员仍可用密码登录。",
    ms: "SMS kini dimatikan, jadi pendaftaran, log masuk SMS dan tetapan semula kata laluan tidak tersedia. Ahli yang telah menetapkan kata laluan masih boleh log masuk dengannya.",
  },
  "auth.sms_not_sent_no_change": {
    en: "The SMS was not sent, and nothing about your account has been changed. Please try again later.",
    zh: "短信未发出，您的账号未作任何更改。请稍后再试。",
    ms: "SMS tidak dihantar, dan tiada apa-apa pada akaun anda telah diubah. Sila cuba lagi kemudian.",
  },
  "auth.continue_guest": {
    en: "Continue as guest",
    zh: "以游客身份继续",
    ms: "Teruskan sebagai tetamu",
  },
  "auth.claim_notice": {
    en: "Once verified, guest orders placed with this number in the last 30 days are added to your account. Points are not added for past orders.",
    zh: "验证通过后，近 30 天内用此号码下单的游客订单会自动归入您的账号；过去的订单不补发积分。",
    ms: "Setelah disahkan, pesanan tetamu dengan nombor ini dalam 30 hari lalu akan ditambah ke akaun anda. Mata tidak ditambah untuk pesanan lalu.",
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
  "pay.stripe_title": {
    en: "Payment (Stripe test mode)",
    zh: "支付（Stripe 测试环境）",
    ms: "Pembayaran (mod ujian Stripe)",
  },
  "pay.stripe_demo_hint": {
    en: "You'll pay on Stripe's test page. Stripe is a real payment service, but in test mode no real money moves.",
    zh: "您将在 Stripe 的测试付款页付款。Stripe 是真实的支付服务，但在测试环境中不会有真实资金流动。",
    ms: "Anda akan membayar di halaman ujian Stripe. Stripe ialah perkhidmatan pembayaran sebenar, tetapi dalam mod ujian tiada wang sebenar berpindah.",
  },
  "pay.stripe_notice": {
    en: "Use only a test card number — never a real card. On Stripe's page, the email is a demo address, and any name or billing address you're asked for can be made up. Whatever you enter there is kept by Stripe's test environment.",
    zh: "只用测试卡号，切勿输入真实银行卡。Stripe 页上的邮箱是演示地址，要求填写的姓名或账单地址都可填虚构内容。您在那里输入的内容由 Stripe 测试环境保存。",
    ms: "Gunakan nombor kad ujian sahaja — jangan sekali-kali kad sebenar. Di halaman Stripe, e-mel ialah alamat demo, dan sebarang nama atau alamat pengebilan yang diminta boleh direka. Apa sahaja yang anda masukkan di sana disimpan oleh persekitaran ujian Stripe.",
  },
  "pay.stripe_test_card": {
    en: "Test card: 4242 4242 4242 4242, any future expiry date, any CVC. If Stripe's page offers other methods such as FPX or GrabPay, follow the test instructions shown there.",
    zh: "测试卡号：4242 4242 4242 4242，有效期填任意未来日期，CVC 任意。Stripe 页上如有 FPX、GrabPay 等其他方式，请按页面上的测试说明操作。",
    ms: "Kad ujian: 4242 4242 4242 4242, sebarang tarikh luput akan datang, sebarang CVC. Jika halaman Stripe menawarkan kaedah lain seperti FPX atau GrabPay, ikut arahan ujian yang dipaparkan di sana.",
  },
  "pay.stripe_go": {
    en: "Go to Stripe test payment",
    zh: "前往 Stripe 测试付款",
    ms: "Pergi ke pembayaran ujian Stripe",
  },
  "pay.stripe_redirecting": {
    en: "Opening Stripe's test payment page…",
    zh: "正在打开 Stripe 测试付款页…",
    ms: "Membuka halaman pembayaran ujian Stripe…",
  },
  "pay.stripe_in_progress": {
    en: "A Stripe test payment for this order is already open. The order stays reserved while that payment page is open; once Stripe confirms the page has expired without a payment, the order is cancelled.",
    zh: "此订单已有一个进行中的 Stripe 测试付款。该付款页开着期间订单保持保留；经 Stripe 确认付款页已失效且没有付款后，订单才会取消。",
    ms: "Pembayaran ujian Stripe untuk pesanan ini sudah dibuka. Pesanan kekal ditempah selagi halaman pembayaran itu dibuka; setelah Stripe mengesahkan halaman itu tamat tempoh tanpa pembayaran, pesanan dibatalkan.",
  },
  "pay.stripe_under_review": {
    en: "We couldn't confirm whether this order's Stripe test payment page was created. The order stays reserved while the store checks it; you don't need to do anything now.",
    zh: "暂时无法确认此订单的 Stripe 测试付款页是否已建立。本站核对期间订单保持保留，您现在无需操作。",
    ms: "Kami tidak dapat mengesahkan sama ada halaman pembayaran ujian Stripe untuk pesanan ini telah dicipta. Pesanan kekal ditempah semasa kedai menyemaknya; anda tidak perlu berbuat apa-apa sekarang.",
  },
  "pay.stripe_continue": {
    en: "Continue on Stripe",
    zh: "继续在 Stripe 付款",
    ms: "Teruskan di Stripe",
  },
  "pay.stripe_unavailable": {
    en: "Stripe test mode isn't responding right now. Your order is kept — please try again in a moment.",
    zh: "Stripe 测试环境暂时没有响应。订单已保留，请稍后再试。",
    ms: "Mod ujian Stripe tidak bertindak balas sekarang. Pesanan anda disimpan — sila cuba lagi sebentar lagi.",
  },
  "pay.method_changed": {
    en: "The payment method for this order has changed. The page has been updated.",
    zh: "此订单的支付方式已改变，页面已更新。",
    ms: "Kaedah pembayaran untuk pesanan ini telah berubah. Halaman telah dikemas kini.",
  },
  "pay.cancel_confirm_stripe": {
    en: "We'll first try to close this order's open Stripe test payment page. If the payment has already gone through, the order won't be cancelled.",
    zh: "本站会先尝试关闭此订单进行中的 Stripe 测试付款页；若付款已经完成，订单不会取消。",
    ms: "Kami akan cuba menutup halaman pembayaran ujian Stripe yang dibuka untuk pesanan ini terlebih dahulu. Jika pembayaran sudah berjaya, pesanan tidak akan dibatalkan.",
  },
  "pay.cancel_try_later": {
    en: "We couldn't reach Stripe to close its payment page, so the order hasn't been cancelled. Please try again in a moment.",
    zh: "暂时无法联系 Stripe 关闭付款页，订单尚未取消，请稍后再试。",
    ms: "Kami tidak dapat menghubungi Stripe untuk menutup halaman pembayarannya, jadi pesanan belum dibatalkan. Sila cuba lagi sebentar lagi.",
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
  "result.confirming_title": {
    en: "Confirming your payment",
    zh: "正在确认付款",
    ms: "Mengesahkan pembayaran anda",
  },
  "result.confirming_body": {
    en: "We're checking this order's payment with Stripe. This page checks again automatically.",
    zh: "我们正在向 Stripe 核实此订单的付款，本页会自动重查。",
    ms: "Kami sedang menyemak pembayaran pesanan ini dengan Stripe. Halaman ini menyemak semula secara automatik.",
  },
  "result.confirming_slow": {
    en: "Still waiting for Stripe to confirm. Your order stays reserved meanwhile — check again in a little while.",
    zh: "仍在等待 Stripe 确认，期间订单保持保留，请稍后再查。",
    ms: "Masih menunggu pengesahan Stripe. Pesanan anda kekal ditempah sementara itu — semak semula sebentar lagi.",
  },
  "result.check_again": {
    en: "Check again",
    zh: "再查一次",
    ms: "Semak semula",
  },
  "result.stripe_not_paid": {
    en: "This order hasn't been paid yet. You can go back to continue the Stripe test payment or cancel the order.",
    zh: "此订单尚未付款。您可以返回继续 Stripe 测试付款，或取消订单。",
    ms: "Pesanan ini belum dibayar. Anda boleh kembali untuk meneruskan pembayaran ujian Stripe atau membatalkan pesanan.",
  },
  "result.stripe_back_to_pay": {
    en: "Back to payment",
    zh: "返回支付页",
    ms: "Kembali ke pembayaran",
  },
  "result.stripe_late_refund": {
    en: "This order had already been cancelled when your Stripe test payment came through, so the store handles that payment as a refund in Stripe's test mode. No real money was involved.",
    zh: "您的 Stripe 测试付款到达时订单已经取消，这笔付款由本站在 Stripe 测试环境按退款处理。全程不涉及真实资金。",
    ms: "Pesanan ini telah dibatalkan semasa pembayaran ujian Stripe anda diterima, jadi kedai mengendalikan pembayaran itu sebagai bayaran balik dalam mod ujian Stripe. Tiada wang sebenar terlibat.",
  },
  "result.paid_not_cancelled": {
    en: "Your payment on Stripe had already gone through, so this order was not cancelled.",
    zh: "您在 Stripe 的付款已经完成，所以此订单没有取消。",
    ms: "Pembayaran anda di Stripe sudah berjaya, jadi pesanan ini tidak dibatalkan.",
  },
  "result.demo_hint": {
    en: 'Next, the store admin will "ship" the order in the demo back office — nothing is actually sent.',
    zh: "接下来管理员会在后台模拟发货——不会真的寄出。",
    ms: 'Seterusnya, pentadbir kedai akan "menghantar" pesanan dalam pejabat belakang demo — tiada apa yang benar-benar dihantar.',
  },
  "lookup.title": {
    en: "Track your order",
    zh: "查询订单",
    ms: "Semak pesanan anda",
  },
  "lookup.phone": {
    en: "Phone number used for the order",
    zh: "下单时填写的电话",
    ms: "Nombor telefon yang digunakan untuk pesanan",
  },
  "lookup.phone_hint": {
    en: "Enter the number with its country code, starting with +.",
    zh: "请输入带国家码的号码，以 + 开头。",
    ms: "Masukkan nombor bersama kod negara, bermula dengan +.",
  },
  "lookup.submit": {
    en: "Find order",
    zh: "查询",
    ms: "Cari pesanan",
  },
  "lookup.not_found": {
    en: "We couldn't find an order with these details. Please check the order number and phone number.",
    zh: "找不到与此资料相符的订单，请检查订单号和电话。",
    ms: "Kami tidak menemui pesanan dengan butiran ini. Sila semak nombor pesanan dan nombor telefon.",
  },
  "lookup.access_note": {
    en: "After a successful lookup, this browser can view this order, confirm receipt and request a refund for 30 minutes. Payment and cancellation are not available here. Each other order needs its own order number and phone number.",
    zh: "查询成功后，本浏览器可在 30 分钟内查看此订单、确认收货和申请退款；此处不能支付或取消。查询其他订单须另行输入该单的订单号和电话。",
    ms: "Selepas semakan berjaya, pelayar ini boleh melihat pesanan ini, mengesahkan penerimaan dan memohon bayaran balik selama 30 minit. Pembayaran dan pembatalan tidak tersedia di sini. Setiap pesanan lain memerlukan nombor pesanan dan nombor telefonnya sendiri.",
  },
  "lookup.privacy_warning": {
    en: "Anyone who knows both the order number and the phone number can see the full shipping details. Keep them private.",
    zh: "同时知道订单号和电话的人都能看到完整收货资料，请妥善保管。",
    ms: "Sesiapa yang tahu nombor pesanan dan nombor telefon boleh melihat butiran penghantaran penuh. Simpan dengan selamat.",
  },
  "lookup.demo_hint": {
    en: "Demo orders have no real parcel or tracking number.",
    zh: "演示订单没有真实包裹或物流单号。",
    ms: "Pesanan demo tiada bungkusan atau nombor penjejakan sebenar.",
  },
  "order.title": {
    en: "Order {orderNo}",
    zh: "订单 {orderNo}",
    ms: "Pesanan {orderNo}",
  },
  "order.current_status": {
    en: "Status:",
    zh: "状态：",
    ms: "Status:",
  },
  "order.progress": {
    en: "Progress",
    zh: "进度",
    ms: "Kemajuan",
  },
  "order.amount_breakdown": {
    en: "Amount details",
    zh: "金额明细",
    ms: "Butiran jumlah",
  },
  "order.lookup_access": {
    en: "You can view this order, confirm receipt and request a refund in this browser for 30 minutes after looking it up.",
    zh: "查询后 30 分钟内，您可在本浏览器查看此订单、确认收货和申请退款。",
    ms: "Anda boleh melihat pesanan ini, mengesahkan penerimaan dan memohon bayaran balik dalam pelayar ini selama 30 minit selepas menyemaknya.",
  },
  "order.lookup_another": {
    en: "Track another order",
    zh: "查询其他订单",
    ms: "Semak pesanan lain",
  },
  "order.session_expired": {
    en: "Access to this order has ended in this browser. Please look it up again with the order number and phone number.",
    zh: "本浏览器对此订单的访问已结束，请凭订单号和电话重新查询。",
    ms: "Akses kepada pesanan ini telah tamat dalam pelayar ini. Sila semak semula dengan nombor pesanan dan nombor telefon.",
  },
  "order.lookup_no_pay": {
    en: "Payment and cancellation are not available from order tracking. Unpaid demo orders are cancelled automatically after 15 minutes.",
    zh: "查询订单页不提供支付或取消。未支付的演示订单会在 15 分钟后自动取消。",
    ms: "Pembayaran dan pembatalan tidak tersedia melalui semakan pesanan. Pesanan demo yang belum dibayar dibatalkan secara automatik selepas 15 minit.",
  },
  "order.status_awaiting": {
    en: "Awaiting demo payment",
    zh: "待模拟支付",
    ms: "Menunggu bayaran demo",
  },
  "order.status_paid": {
    en: "Paid (demo)",
    zh: "已支付（演示）",
    ms: "Dibayar (demo)",
  },
  "order.status_packed": {
    en: "Packed (demo)",
    zh: "已打包（演示）",
    ms: "Dibungkus (demo)",
  },
  "order.status_shipped": {
    en: "Shipped (demo)",
    zh: "已发货（演示）",
    ms: "Dihantar (demo)",
  },
  "order.status_completed": {
    en: "Completed (demo)",
    zh: "已完成（演示）",
    ms: "Selesai (demo)",
  },
  "order.status_cancelled": {
    en: "Cancelled (demo)",
    zh: "已取消（演示）",
    ms: "Dibatalkan (demo)",
  },
  "order.items": {
    en: "Items",
    zh: "商品",
    ms: "Item",
  },
  "order.unit_price": {
    en: "Unit price",
    zh: "单价",
    ms: "Harga seunit",
  },
  "order.cash_paid": {
    en: "Amount paid (excluding points discount)",
    zh: "实付金额（不含积分抵扣）",
    ms: "Amaun dibayar (tidak termasuk diskaun mata)",
  },
  "order.confirm_receipt": {
    en: "Confirm receipt",
    zh: "确认收货",
    ms: "Sahkan penerimaan",
  },
  "order.confirm_receipt_hint": {
    en: 'Nothing was really delivered — confirming only moves the demo order to "Completed". If you do nothing, it completes automatically 7 days after shipping.',
    zh: "并没有真实包裹——确认只会把演示订单改为“已完成”。若不操作，模拟发货 7 天后自动完成。",
    ms: 'Tiada penghantaran sebenar — pengesahan hanya menukar pesanan demo kepada "Selesai". Jika tiada tindakan, ia selesai secara automatik 7 hari selepas penghantaran.',
  },
  "order.request_refund": {
    en: "Request a refund",
    zh: "申请退款",
    ms: "Mohon bayaran balik",
  },
  "order.refund_deadline": {
    en: "Refunds can be requested until {date}.",
    zh: "可在 {date} 前申请退款。",
    ms: "Bayaran balik boleh dimohon sehingga {date}.",
  },
  "order.refunded_total": {
    en: "Refunded so far (demo): RM {amount}",
    zh: "累计已退（演示）：RM {amount}",
    ms: "Telah dibayar balik (demo): RM {amount}",
  },
  "order.refundable_left": {
    en: "Still refundable: RM {amount}",
    zh: "剩余可退：RM {amount}",
    ms: "Baki boleh dibayar balik: RM {amount}",
  },
  "order.refund_requests": {
    en: "Refund requests",
    zh: "退款申请记录",
    ms: "Permohonan bayaran balik",
  },
  "order.refund_requested": {
    en: "Under review",
    zh: "审核中",
    ms: "Dalam semakan",
  },
  "order.refund_approved": {
    en: "Approved (demo)",
    zh: "已批准（演示）",
    ms: "Diluluskan (demo)",
  },
  "order.refund_rejected": {
    en: "Rejected",
    zh: "已拒绝",
    ms: "Ditolak",
  },
  "order.fulfilment_frozen": {
    en: "All items have been refunded, so this order will not move further.",
    zh: "所有商品均已退款，订单不再推进。",
    ms: "Semua item telah dibayar balik, jadi pesanan ini tidak akan diteruskan.",
  },
  "order.demo_hint": {
    en: "Status changes are simulated by the store admin.",
    zh: "状态变化由店铺管理员模拟推进。",
    ms: "Perubahan status disimulasikan oleh pentadbir kedai.",
  },
  "refund.title": {
    en: "Request a demo refund",
    zh: "申请模拟退款",
    ms: "Mohon bayaran balik demo",
  },
  "refund.select_items": {
    en: "Choose items and quantities",
    zh: "选择商品与数量",
    ms: "Pilih item dan kuantiti",
  },
  "refund.max_qty": {
    en: "Up to {count}",
    zh: "最多 {count} 件",
    ms: "Sehingga {count}",
  },
  "refund.estimate": {
    en: "Estimated refund (demo): RM {amount}",
    zh: "预计退款（演示）：RM {amount}",
    ms: "Anggaran bayaran balik (demo): RM {amount}",
  },
  "refund.shipping_not_refunded": {
    en: "Sample shipping fees are not refunded.",
    zh: "示例运费不退。",
    ms: "Kos penghantaran contoh tidak dibayar balik.",
  },
  "refund.coupon_not_restored": {
    en: "Used coupons are not restored.",
    zh: "已使用的优惠券不恢复。",
    ms: "Kupon yang telah digunakan tidak dipulihkan.",
  },
  "refund.submit": {
    en: "Submit refund request",
    zh: "提交退款申请",
    ms: "Hantar permohonan bayaran balik",
  },
  "refund.submit_hint": {
    en: "This is a simulated refund: no real money will be returned. The amount is calculated by the system from the amount paid for each item, excluding any points discount.",
    zh: "这是模拟退款：不会退还任何真实款项。金额由系统按每件商品的实付金额（不含积分抵扣）计算。",
    ms: "Ini bayaran balik simulasi: tiada wang sebenar akan dikembalikan. Jumlah dikira oleh sistem berdasarkan amaun dibayar bagi setiap item, tidak termasuk diskaun mata.",
  },
  "refund.submitted": {
    en: "Refund request submitted. The result will appear on this order.",
    zh: "退款申请已提交，结果会显示在此订单中。",
    ms: "Permohonan dihantar. Keputusan akan dipaparkan pada pesanan ini.",
  },
  "refund.duplicate": {
    en: "This quantity is already under review.",
    zh: "该数量已在审核中。",
    ms: "Kuantiti ini sudah dalam semakan.",
  },
  "refund.nothing_left": {
    en: "Nothing is left to refund on this order.",
    zh: "此订单已无可退商品。",
    ms: "Tiada lagi item untuk dibayar balik pada pesanan ini.",
  },
  "refund.window_closed": {
    en: "The 30-day refund period for this order has ended.",
    zh: "此订单的 30 天退款期已过。",
    ms: "Tempoh bayaran balik 30 hari untuk pesanan ini telah tamat.",
  },
  "refund.demo_hint": {
    en: "Refunds are reviewed by the admin in the demo back office.",
    zh: "退款由管理员在演示后台审核。",
    ms: "Bayaran balik disemak oleh pentadbir dalam pejabat belakang demo.",
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
  "privacy.stripe": {
    en: "When payments use Stripe test mode, you pay on Stripe's test page instead of ours. We send Stripe only the amount and currency, a demo item name, an internal order reference, your language, the links back to this site and a fixed demo email — not your name, phone number, address or order number. Anything you type on Stripe's page, such as a cardholder name, billing address or a changed email, is kept by Stripe's test environment; we don't read or store it and can't delete it item by item, only the whole test environment. Use only test card numbers and made-up details there.",
    zh: "支付使用 Stripe 测试环境时，您在 Stripe 的测试付款页而不是本站付款。本站发给 Stripe 的只有金额与币种、演示商品名、内部订单编号、您的语言、返回本站的链接与固定的演示邮箱，不发送您的姓名、电话、地址或订单号。您在 Stripe 页上输入的内容（如持卡人姓名、账单地址或改填的邮箱）由 Stripe 测试环境保存；本站不读取、不保存，也无法逐条删除，只能删除整个测试环境。请在那里只用测试卡号与虚构资料。",
    ms: "Apabila pembayaran menggunakan mod ujian Stripe, anda membayar di halaman ujian Stripe, bukan di halaman kami. Kami hanya menghantar kepada Stripe jumlah dan mata wang, nama item demo, rujukan pesanan dalaman, bahasa anda, pautan kembali ke laman ini dan e-mel demo yang tetap — bukan nama, nombor telefon, alamat atau nombor pesanan anda. Apa sahaja yang anda taip di halaman Stripe, seperti nama pemegang kad, alamat pengebilan atau e-mel yang diubah, disimpan oleh persekitaran ujian Stripe; kami tidak membaca atau menyimpannya dan tidak boleh memadamnya satu demi satu, hanya keseluruhan persekitaran ujian. Gunakan nombor kad ujian dan butiran rekaan sahaja di sana.",
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
  "privacy.h_contact": {
    en: "Contact",
    zh: "联系",
    ms: "Hubungi",
  },
  "privacy.contact": {
    en: "Questions? Contact Acuven on WhatsApp.",
    zh: "有疑问？请通过 WhatsApp 联系 Acuven。",
    ms: "Ada soalan? Hubungi Acuven melalui WhatsApp.",
  },
  "privacy.contact_button": {
    en: "Chat on WhatsApp",
    zh: "通过 WhatsApp 联系",
    ms: "Sembang di WhatsApp",
  },
  "admin.demo_banner": {
    en: "Admin — demo store. Actions here never move real money or goods.",
    zh: "管理后台——演示网店。这里的操作不会产生真实资金或货物流动。",
    ms: "Pentadbir — kedai demo. Tindakan di sini tidak pernah memindahkan wang atau barang sebenar.",
  },
  "admin.login_title": {
    en: "Admin log in",
    zh: "管理员登录",
    ms: "Log masuk pentadbir",
  },
  "admin.email": {
    en: "Email",
    zh: "邮箱",
    ms: "E-mel",
  },
  "admin.login_failed": {
    en: "Username or password is incorrect.",
    zh: "用户名或密码不正确。",
    ms: "Nama pengguna atau kata laluan salah.",
  },
  "admin.locked": {
    en: "Too many failed attempts. This sign-in is locked for a short time.",
    zh: "失败次数过多，登录已短时锁定。",
    ms: "Terlalu banyak cubaan gagal. Log masuk ini dikunci untuk seketika.",
  },
  "admin.nav_orders": {
    en: "Orders",
    zh: "订单",
    ms: "Pesanan",
  },
  "admin.nav_refunds": {
    en: "Refunds",
    zh: "退款审核",
    ms: "Bayaran balik",
  },
  "admin.nav_coupons": {
    en: "Coupons",
    zh: "优惠券",
    ms: "Kupon",
  },
  "admin.coupon_listed_note": {
    en: 'All active coupons within their valid dates are listed under "My coupons", the same for every member, regardless of total or per-member usage limits.',
    zh: "所有启用中且在有效期内的优惠券都会列在会员中心「我的优惠券」中，对全部会员相同，不因总次数或每会员次数上限而不列出。",
    ms: "Semua kupon aktif dalam tempoh sah disenaraikan di bawah \"Kupon saya\", sama bagi setiap ahli, tanpa mengira had jumlah penggunaan atau had setiap ahli.",
  },
  "admin.coupon_code": {
    en: "Coupon code",
    zh: "优惠券代码",
    ms: "Kod kupon",
  },
  "admin.coupon_type": {
    en: "Type",
    zh: "类型",
    ms: "Jenis",
  },
  "admin.coupon_value": {
    en: "Value",
    zh: "面额",
    ms: "Nilai",
  },
  "admin.coupon_usage": {
    en: "Used / limit",
    zh: "已用 / 上限",
    ms: "Digunakan / had",
  },
  "admin.coupon_enabled": {
    en: "Active",
    zh: "启用",
    ms: "Aktif",
  },
  "admin.coupon_disabled": {
    en: "Disabled",
    zh: "已停用",
    ms: "Dinyahaktifkan",
  },
  "admin.coupon_fixed": {
    en: "Fixed amount (RM)",
    zh: "固定金额（RM）",
    ms: "Jumlah tetap (RM)",
  },
  "admin.coupon_percent": {
    en: "Percentage (%)",
    zh: "百分比（%）",
    ms: "Peratusan (%)",
  },
  "admin.coupon_valid": {
    en: "Valid from / until",
    zh: "有效期起止",
    ms: "Sah dari / hingga",
  },
  "admin.coupon_disable": {
    en: "Disable",
    zh: "停用",
    ms: "Nyahaktifkan",
  },
  "admin.coupon_min_spend": {
    en: "Minimum item spend (RM)",
    zh: "最低商品消费（RM）",
    ms: "Perbelanjaan item minimum (RM)",
  },
  "admin.coupon_total_limit": {
    en: "Total uses allowed",
    zh: "总使用次数上限",
    ms: "Had jumlah penggunaan",
  },
  "admin.coupon_member_limit": {
    en: "Uses per member",
    zh: "每会员使用次数上限",
    ms: "Had penggunaan setiap ahli",
  },
  "admin.rules_apply_new": {
    en: "Changes apply to new orders only. Existing orders keep their original amounts.",
    zh: "修改只影响新订单，已有订单保持原金额。",
    ms: "Perubahan hanya terpakai untuk pesanan baharu. Pesanan sedia ada mengekalkan jumlah asal.",
  },
  "admin.coupon_code_taken": {
    en: "This coupon code is already in use. Choose another code.",
    zh: "此优惠券代码已被使用，请换一个代码。",
    ms: "Kod kupon ini sudah digunakan. Sila pilih kod lain.",
  },
  "admin.coupon_invalid": {
    en: "Check the code (4–20 letters or digits), value, minimum spend, usage limits and dates.",
    zh: "请检查代码（4 到 20 位字母或数字）、面额、最低消费、次数上限与日期。",
    ms: "Semak kod (4–20 huruf atau digit), nilai, perbelanjaan minimum, had penggunaan dan tarikh.",
  },
  "admin.nav_stock_resets": {
    en: "Stock resets",
    zh: "库存重置",
    ms: "Tetapan semula stok",
  },
  "admin.filter_status": {
    en: "Status",
    zh: "状态",
    ms: "Status",
  },
  "admin.search_order": {
    en: "Search by order number",
    zh: "按订单号搜索",
    ms: "Cari mengikut nombor pesanan",
  },
  "admin.col_date": {
    en: "Date",
    zh: "日期",
    ms: "Tarikh",
  },
  "admin.col_total": {
    en: "Total (MYR)",
    zh: "合计（MYR）",
    ms: "Jumlah (MYR)",
  },
  "admin.col_refunds": {
    en: "Refunds",
    zh: "退款",
    ms: "Bayaran balik",
  },
  "admin.refunds_pending": {
    en: "{count} pending",
    zh: "{count} 项待审",
    ms: "{count} menunggu",
  },
  "admin.col_requested_at": {
    en: "Requested at",
    zh: "申请时间",
    ms: "Dimohon pada",
  },
  "admin.col_amount": {
    en: "Amount (MYR)",
    zh: "金额（MYR）",
    ms: "Amaun (MYR)",
  },
  "admin.order_detail": {
    en: "Order details",
    zh: "订单详情",
    ms: "Butiran pesanan",
  },
  "admin.event_log": {
    en: "Event log",
    zh: "事件记录",
    ms: "Log peristiwa",
  },
  "admin.col_time": {
    en: "Time",
    zh: "时间",
    ms: "Masa",
  },
  "admin.col_event": {
    en: "Event",
    zh: "事件",
    ms: "Peristiwa",
  },
  "admin.col_actor": {
    en: "By",
    zh: "操作者",
    ms: "Oleh",
  },
  "admin.actor_admin": {
    en: "Admin",
    zh: "管理员",
    ms: "Pentadbir",
  },
  "admin.actor_system": {
    en: "System",
    zh: "系统",
    ms: "Sistem",
  },
  "admin.actor_customer": {
    en: "Customer",
    zh: "顾客",
    ms: "Pelanggan",
  },
  "admin.recipient_raw": {
    en: "Shipping details (original)",
    zh: "原始收货资料",
    ms: "Butiran penghantaran (asal)",
  },
  "admin.recipient_audited": {
    en: "Viewing these details is recorded in the audit log.",
    zh: "查看此资料会记入审计记录。",
    ms: "Paparan butiran ini direkodkan dalam log audit.",
  },
  "admin.mark_packed": {
    en: "Mark as packed (demo)",
    zh: "标记为已打包（演示）",
    ms: "Tandakan dibungkus (demo)",
  },
  "admin.mark_shipped": {
    en: "Mark as shipped (demo)",
    zh: "标记为已发货（演示）",
    ms: "Tandakan dihantar (demo)",
  },
  "admin.ship_hint": {
    en: "No real parcel or courier booking is created.",
    zh: "不会产生真实包裹或物流下单。",
    ms: "Tiada bungkusan atau tempahan kurier sebenar dicipta.",
  },
  "admin.frozen": {
    en: "All items refunded — fulfilment is frozen.",
    zh: "全部商品已退款，履约已冻结。",
    ms: "Semua item dibayar balik — pemenuhan dibekukan.",
  },
  "admin.refund_detail": {
    en: "Refund request details",
    zh: "退款申请详情",
    ms: "Butiran permohonan bayaran balik",
  },
  "admin.refund_qty": {
    en: "Requested {requested} / bought {bought} / approved {approved}",
    zh: "申请 {requested} / 购买 {bought} / 已批准 {approved}",
    ms: "Dimohon {requested} / dibeli {bought} / diluluskan {approved}",
  },
  "admin.refund_amount": {
    en: "Cash refund (demo): RM {amount}",
    zh: "模拟退现金：RM {amount}",
    ms: "Bayaran balik tunai (demo): RM {amount}",
  },
  "admin.refund_points": {
    en: "Points returned {returned} / taken back {reversed}",
    zh: "返还积分 {returned} / 追回积分 {reversed}",
    ms: "Mata dikembalikan {returned} / ditarik balik {reversed}",
  },
  "admin.refund_approve": {
    en: "Approve (demo)",
    zh: "批准（演示）",
    ms: "Luluskan (demo)",
  },
  "admin.refund_reject": {
    en: "Reject",
    zh: "拒绝",
    ms: "Tolak",
  },
  "admin.refund_reason": {
    en: "Reason",
    zh: "理由",
    ms: "Sebab",
  },
  "admin.refund_hint": {
    en: "Approving never sends real money.",
    zh: "批准不会退还任何真实款项。",
    ms: "Kelulusan tidak pernah menghantar wang sebenar.",
  },
  "admin.reviewed_by": {
    en: "Reviewed by {username} on {time}",
    zh: "由 {username} 于 {time} 审核",
    ms: "Disemak oleh {username} pada {time}",
  },
  "admin.stock_reset_title": {
    en: "Daily stock reset results",
    zh: "每日库存重置结果",
    ms: "Keputusan tetapan semula stok harian",
  },
  "admin.col_date_myt": {
    en: "Date (Malaysia time)",
    zh: "日期（马来西亚时间）",
    ms: "Tarikh (waktu Malaysia)",
  },
  "admin.col_result": {
    en: "Result",
    zh: "结果",
    ms: "Keputusan",
  },
  "admin.col_sku_count": {
    en: "SKUs",
    zh: "SKU 数",
    ms: "Bilangan SKU",
  },
  "admin.stock_reset_breakdown": {
    en: "Initial {initial} − active holds {held} = available today {available}",
    zh: "初始 {initial} − 有效预留 {held} = 当日可用 {available}",
    ms: "Awal {initial} − tahanan aktif {held} = tersedia hari ini {available}",
  },
  "admin.stock_reset_ok": {
    en: "Completed",
    zh: "已完成",
    ms: "Selesai",
  },
  "admin.stock_reset_failed": {
    en: "Failed — the operator has been alerted",
    zh: "失败——已告警运营者",
    ms: "Gagal — pengendali telah dimaklumkan",
  },
  "admin.stock_reset_note": {
    en: "Resets restore today's stock only. Past orders, refunds and points are not changed.",
    zh: "重置只恢复当日库存，不改动历史订单、退款与积分。",
    ms: "Tetapan semula hanya memulihkan stok hari ini. Pesanan, bayaran balik dan mata lalu tidak diubah.",
  },
  "admin.nav_store_design": {
    en: "Store design",
    zh: "店铺装修",
    ms: "Reka bentuk kedai",
  },
  "admin.nav_settings": {
    en: "Site settings",
    zh: "站点设置",
    ms: "Tetapan laman",
  },
  "admin.design_demo_note": {
    en: "The demo banner and demo hints always show on the storefront and can't be turned off here.",
    zh: "前台的演示横幅与演示提示始终显示，这里不能关闭。",
    ms: "Sepanduk demo dan petunjuk demo sentiasa dipaparkan di kedai dan tidak boleh dimatikan di sini.",
  },
  "admin.theme": {
    en: "Theme",
    zh: "主题",
    ms: "Tema",
  },
  "admin.theme_pandan": {
    en: "Pandan",
    zh: "班兰",
    ms: "Pandan",
  },
  "admin.theme_pasar": {
    en: "Pasar Pagi",
    zh: "早市",
    ms: "Pasar Pagi",
  },
  "admin.theme_receipt": {
    en: "Receipt",
    zh: "小票",
    ms: "Resit",
  },
  "admin.theme_kopitiam": {
    en: "Kopitiam",
    zh: "咖啡店",
    ms: "Kopitiam",
  },
  "admin.theme_batik": {
    en: "Batik",
    zh: "蜡染",
    ms: "Batik",
  },
  "admin.theme_malam": {
    en: "Pasar Malam",
    zh: "夜市",
    ms: "Pasar Malam",
  },
  "admin.theme_gula": {
    en: "Gula-Gula",
    zh: "糖果",
    ms: "Gula-Gula",
  },
  "admin.theme_galeri": {
    en: "Galeri",
    zh: "画廊",
    ms: "Galeri",
  },
  "admin.theme_songket": {
    en: "Songket",
    zh: "金线",
    ms: "Songket",
  },
  "admin.theme_litar": {
    en: "Litar",
    zh: "电路",
    ms: "Litar",
  },
  "admin.theme_dark_note": {
    en: "Every theme has a light and a dark version. Visitors see the one that matches their device setting.",
    zh: "每款主题都有浅色与深色，访客看到哪一种取决于其设备设置。",
    ms: "Setiap tema ada versi cerah dan gelap. Pelawat melihat versi yang sepadan dengan tetapan peranti mereka.",
  },
  "admin.preview": {
    en: "Preview",
    zh: "预览",
    ms: "Pratonton",
  },
  "admin.preview_light": {
    en: "Light",
    zh: "浅色",
    ms: "Cerah",
  },
  "admin.preview_dark": {
    en: "Dark",
    zh: "深色",
    ms: "Gelap",
  },
  "admin.accent": {
    en: "Accent colour",
    zh: "主色",
    ms: "Warna utama",
  },
  "admin.accent_option": {
    en: "Colour {n}",
    zh: "颜色 {n}",
    ms: "Warna {n}",
  },
  "admin.accent_hint": {
    en: "Only colours that stay readable in both light and dark mode are offered.",
    zh: "只提供在浅色与深色下都清楚可读的颜色。",
    ms: "Hanya warna yang kekal jelas dalam mod cerah dan gelap ditawarkan.",
  },
  "admin.home_blocks": {
    en: "Home page blocks",
    zh: "首页区块",
    ms: "Blok halaman utama",
  },
  "admin.block_hero": {
    en: "Main banner",
    zh: "主视觉",
    ms: "Sepanduk utama",
  },
  "admin.block_show": {
    en: "Show",
    zh: "显示",
    ms: "Papar",
  },
  "admin.block_move_up": {
    en: "Move up",
    zh: "上移",
    ms: "Alih ke atas",
  },
  "admin.block_move_down": {
    en: "Move down",
    zh: "下移",
    ms: "Alih ke bawah",
  },
  "admin.home_blocks_hint": {
    en: "Block text comes from the store's translations and can't be edited here. The demo hint on the home page always shows above these blocks.",
    zh: "区块文字来自店铺的三语文案，不能在这里修改。首页的演示提示始终显示在这些区块上方。",
    ms: "Teks blok datang daripada terjemahan kedai dan tidak boleh disunting di sini. Petunjuk demo di halaman utama sentiasa dipaparkan di atas blok ini.",
  },
  "admin.featured_pick": {
    en: "Featured products (up to 4)",
    zh: "精选商品（最多 4 件）",
    ms: "Produk pilihan (sehingga 4)",
  },
  "admin.featured_add": {
    en: "Add product",
    zh: "添加商品",
    ms: "Tambah produk",
  },
  "admin.featured_remove": {
    en: "Remove from featured",
    zh: "移出精选",
    ms: "Keluarkan daripada pilihan",
  },
  "admin.featured_hint": {
    en: "Only published products are shown on the home page, in this order. If none are chosen, or none of them are published, the 4 newest products are shown.",
    zh: "首页按此顺序只显示仍上架的商品；未挑选或挑选的都已下架时，显示最新的 4 件。",
    ms: "Hanya produk yang diterbitkan dipaparkan di halaman utama, mengikut susunan ini. Jika tiada yang dipilih atau tiada yang diterbitkan, 4 produk terbaharu dipaparkan.",
  },
  "admin.design_saved": {
    en: "Saved. The storefront now uses these settings.",
    zh: "已保存，前台已改用这些设置。",
    ms: "Disimpan. Kedai kini menggunakan tetapan ini.",
  },
  "admin.logout": {
    en: "Log out",
    zh: "退出登录",
    ms: "Log keluar",
  },
  "admin.settings_saved": {
    en: "Saved. New requests use this setting.",
    zh: "已保存，之后的请求按此设置执行。",
    ms: "Disimpan. Permintaan baharu menggunakan tetapan ini.",
  },
  "admin.payment_method": {
    en: "Payment method",
    zh: "支付方式",
    ms: "Kaedah pembayaran",
  },
  "admin.payment_simulated": {
    en: "Simulated (default)",
    zh: "模拟（默认）",
    ms: "Simulasi (lalai)",
  },
  "admin.payment_stripe": {
    en: "Stripe test mode",
    zh: "Stripe 测试环境",
    ms: "Mod ujian Stripe",
  },
  "admin.payment_method_hint": {
    en: "Simulated: customers pick an outcome on the store's own demo payment page. Stripe test mode: customers pay on Stripe's test payment page with test cards or other test methods offered there (such as FPX or GrabPay), and no real money moves; for those orders, approving a refund also sends a refund request to Stripe's test mode, and its status is shown in the refund details. Orders below Stripe's minimum amount still use simulated payment. Stripe payment sessions already started keep being handled through Stripe even if you switch back.",
    zh: "模拟：顾客在本站的演示支付页选择结果。Stripe 测试环境：顾客在 Stripe 测试付款页用测试卡或该页提供的其他测试方式（如 FPX、GrabPay）付款，不会有真实资金流动；这些订单的退款经您批准后，也会向 Stripe 测试环境发出退款请求，其状态在退款详情中查看。低于 Stripe 最低收款额的订单仍用模拟支付。已在 Stripe 发起的付款会话，即使切回模拟也仍按 Stripe 流程处理。",
    ms: "Simulasi: pelanggan memilih keputusan di halaman pembayaran demo kedai sendiri. Mod ujian Stripe: pelanggan membayar di halaman pembayaran ujian Stripe dengan kad ujian atau kaedah ujian lain yang ditawarkan di sana (seperti FPX atau GrabPay), dan tiada wang sebenar berpindah; bagi pesanan itu, meluluskan bayaran balik juga menghantar permintaan bayaran balik ke mod ujian Stripe, dan statusnya dipaparkan dalam butiran bayaran balik. Pesanan di bawah jumlah minimum Stripe masih menggunakan pembayaran simulasi. Sesi pembayaran Stripe yang sudah dimulakan terus dikendalikan melalui Stripe walaupun anda menukar semula.",
  },
  "admin.stripe_not_configured": {
    en: "Stripe test mode can't be selected: its test secret key or webhook signing secret is missing or invalid. Only test keys are accepted, never live keys.",
    zh: "无法选择 Stripe 测试环境：测试密钥或 webhook 签名密钥未配置或无效。只接受测试密钥，正式环境密钥一律不接受。",
    ms: "Mod ujian Stripe tidak boleh dipilih: kunci rahsia ujian atau rahsia tandatangan webhook tiada atau tidak sah. Hanya kunci ujian diterima, bukan kunci langsung.",
  },
  "admin.paid_via_stripe": {
    en: "Paid with Stripe test mode",
    zh: "经 Stripe 测试环境付款",
    ms: "Dibayar dengan mod ujian Stripe",
  },
  "admin.stripe_method": {
    en: "Payment method type: {type}",
    zh: "付款方式类型：{type}",
    ms: "Jenis kaedah pembayaran: {type}",
  },
  "admin.stripe_method_unknown": {
    en: "Payment method type: not known yet",
    zh: "付款方式类型：尚未取得",
    ms: "Jenis kaedah pembayaran: belum diketahui",
  },
  "admin.stripe_deadline_fpx": {
    en: "Stripe allows FPX refunds within 60 days of payment.",
    zh: "Stripe 允许 FPX 付款在付款后 60 天内退款。",
    ms: "Stripe membenarkan bayaran balik FPX dalam tempoh 60 hari selepas pembayaran.",
  },
  "admin.stripe_deadline_grabpay": {
    en: "Stripe allows GrabPay refunds within 90 days of payment.",
    zh: "Stripe 允许 GrabPay 付款在付款后 90 天内退款。",
    ms: "Stripe membenarkan bayaran balik GrabPay dalam tempoh 90 hari selepas pembayaran.",
  },
  "admin.stripe_deadline_other": {
    en: "The refund time limit for this payment method is set by Stripe.",
    zh: "此付款方式的退款期限以 Stripe 为准。",
    ms: "Had masa bayaran balik untuk kaedah pembayaran ini ditetapkan oleh Stripe.",
  },
  "admin.stripe_refund_hint": {
    en: "Approving also sends a refund request for the cash amount above to Stripe's test mode (nothing is sent if it is RM 0.00); after approval, its status is shown on this page. No real money moves. If Stripe doesn't complete the refund, the approval still stands.",
    zh: "批准时也会就上面的现金金额向 Stripe 测试环境发出退款请求（为 RM 0.00 时不发送），批准后在本页查看其状态。不会有真实资金流动。Stripe 未完成退款时，批准依然有效。",
    ms: "Kelulusan juga menghantar permintaan bayaran balik bagi amaun tunai di atas ke mod ujian Stripe (tiada apa dihantar jika RM 0.00); selepas kelulusan, statusnya dipaparkan di halaman ini. Tiada wang sebenar berpindah. Jika Stripe tidak melengkapkan bayaran balik, kelulusan tetap berkuat kuasa.",
  },
  "admin.stripe_refund_title": {
    en: "Stripe test refunds",
    zh: "Stripe 测试环境退款",
    ms: "Bayaran balik ujian Stripe",
  },
  "admin.col_stripe_refund_id": {
    en: "Stripe refund ID",
    zh: "Stripe 退款 ID",
    ms: "ID bayaran balik Stripe",
  },
  "admin.stripe_refund_pending": {
    en: "Processing",
    zh: "处理中",
    ms: "Sedang diproses",
  },
  "admin.stripe_refund_succeeded": {
    en: "Refunded in Stripe",
    zh: "已在 Stripe 退回",
    ms: "Dikembalikan di Stripe",
  },
  "admin.stripe_refund_failed": {
    en: "Failed",
    zh: "失败",
    ms: "Gagal",
  },
  "admin.stripe_refund_pending_hint": {
    en: "Waiting for Stripe's result. Open this page again later to see the update. If it stays processing for a long time, check it in the Stripe dashboard.",
    zh: "正在等待 Stripe 的结果，请稍后重新打开本页查看。若长时间仍在处理中，请到 Stripe 后台查看。",
    ms: "Menunggu keputusan Stripe. Buka halaman ini semula kemudian untuk melihat kemas kini. Jika ia kekal diproses untuk masa yang lama, semak di papan pemuka Stripe.",
  },
  "admin.stripe_refund_failed_hint": {
    en: "Stripe didn't complete this refund. Check the time limit above and the Stripe dashboard, then retry. Retrying sends a new refund request.",
    zh: "Stripe 未完成这笔退款。请先核对上面的期限与 Stripe 后台，再重试；重试会发出一笔新的退款请求。",
    ms: "Stripe tidak melengkapkan bayaran balik ini. Semak had masa di atas dan papan pemuka Stripe, kemudian cuba semula. Cubaan semula menghantar permintaan bayaran balik baharu.",
  },
  "admin.stripe_refund_retry": {
    en: "Retry Stripe refund",
    zh: "重试 Stripe 退款",
    ms: "Cuba semula bayaran balik Stripe",
  },
  "admin.stripe_refund_none": {
    en: "Nothing to refund in Stripe: the approved items were fully paid with coupons or points.",
    zh: "无需在 Stripe 退款：所退商品已全部由优惠券或积分抵扣。",
    ms: "Tiada apa untuk dikembalikan di Stripe: item yang diluluskan dibayar sepenuhnya dengan kupon atau mata.",
  },
  "admin.stripe_refund_not_sent": {
    en: "This refund couldn't be sent to Stripe. The operator has been alerted; check this payment in the Stripe dashboard.",
    zh: "这笔退款未能发送到 Stripe，已告警运营者；请到 Stripe 后台核对这笔付款。",
    ms: "Bayaran balik ini tidak dapat dihantar ke Stripe. Pengendali telah dimaklumkan; semak pembayaran ini di papan pemuka Stripe.",
  },
  "admin.stripe_refund_blocked": {
    en: "This refund can't be sent to Stripe because it would exceed the amount paid. The operator has been alerted.",
    zh: "这笔退款会超过付款金额，无法发送到 Stripe，已告警运营者。",
    ms: "Bayaran balik ini tidak boleh dihantar ke Stripe kerana ia akan melebihi amaun yang dibayar. Pengendali telah dimaklumkan.",
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

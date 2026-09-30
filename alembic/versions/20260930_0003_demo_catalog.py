"""Demo catalog seed data: 6 categories, 30 generic physical products.

依据 docs/DESIGN.md 1.8（提交 2b68ad0）「数据模型」的 Product / Variant 一行，
写进 SHOP-TASK-004 建好的表，不改表结构。数据全是迁移内的常量，用轻量表定义写入，
不 import app 的模型：模型以后改了，这个迁移写入的内容不跟着变。
金额是 MYR 整数仙；每个 SKU 的当日可用库存等于每日初始库存；created_at 是不带时区的 UTC。
每件商品一张图片，引用是站点根路径，指向 frontend/public/demo-images/ 下所属分类的占位图。
马来文未经母语者审校。

downgrade 按 slug 与 SKU 删除这批数据，只保证在写入后未经后台编辑、未被其他数据引用的库上成立
（CI 与开发库）；部署只执行 upgrade。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (slug, 英文, 中文, 马来文)；写入顺序即 id 顺序。
CATEGORIES = (
    ("apparel", "Apparel", "服饰", "Pakaian"),
    ("bags", "Bags", "包袋", "Beg"),
    ("home", "Home", "家居", "Rumah"),
    ("kitchen", "Kitchen", "厨房", "Dapur"),
    ("stationery", "Stationery", "文具", "Alat Tulis"),
    ("gadgets", "Gadgets", "数码小物", "Gajet"),
)

# 规格名 code → (英文, 中文, 马来文)；写入顺序即规格名的排列序号。
OPTIONS = {
    "color": ("Colour", "颜色", "Warna"),
    "size": ("Size", "尺寸", "Saiz"),
}

# 规格名 code → 规格值 code → (英文, 中文, 马来文)；在字典中的位置即规格值的排列序号。
OPTION_VALUES = {
    "color": {
        "black": ("Black", "黑色", "Hitam"),
        "white": ("White", "白色", "Putih"),
        "grey": ("Grey", "灰色", "Kelabu"),
        "navy": ("Navy", "藏青色", "Biru Tua"),
        "blue": ("Blue", "蓝色", "Biru"),
        "red": ("Red", "红色", "Merah"),
        "green": ("Green", "绿色", "Hijau"),
        "yellow": ("Yellow", "黄色", "Kuning"),
        "pink": ("Pink", "粉色", "Merah Jambu"),
        "natural": ("Natural", "原色", "Warna Asli"),
    },
    "size": {
        "s": ("S", "S", "S"),
        "m": ("M", "M", "M"),
        "l": ("L", "L", "L"),
        "xl": ("XL", "XL", "XL"),
        "small": ("Small", "小号", "Kecil"),
        "medium": ("Medium", "中号", "Sederhana"),
        "large": ("Large", "大号", "Besar"),
        "1m": ("1 m", "1 米", "1 m"),
        "2m": ("2 m", "2 米", "2 m"),
    },
}

# 每件商品：name 与 description 是 (英文, 中文, 马来文)；options 是规格名 code，按排列序；
# variants 是 (各规格名下的规格值 code, 单价仙, 每日初始库存)。
# 双规格商品都刻意缺一个颜色与尺寸组合，演示筛选须由同一个 SKU 同时满足。
PRODUCTS = (
    {
        "slug": "crew-neck-tee",
        "category": "apparel",
        "name": ("Crew Neck Tee", "圆领 T 恤", "Baju-T Leher Bulat"),
        "description": (
            "A soft cotton tee with a relaxed fit for everyday wear.",
            "柔软棉质 T 恤，版型宽松，适合日常穿着。",
            "Baju-T kapas yang lembut dengan potongan santai untuk kegunaan harian.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("black", "s"), 3900, 12),
            (("black", "m"), 3900, 20),
            (("black", "l"), 3900, 20),
            (("black", "xl"), 4200, 8),
            (("white", "s"), 3900, 10),
            (("white", "m"), 3900, 18),
            (("white", "l"), 3900, 18),
            (("white", "xl"), 4200, 6),
            (("navy", "s"), 3900, 8),
            (("navy", "m"), 3900, 15),
            (("navy", "l"), 3900, 15),
        ),
    },
    {
        "slug": "pullover-hoodie",
        "category": "apparel",
        "name": ("Pullover Hoodie", "套头连帽卫衣", "Hoodie Sarung"),
        "description": (
            "A midweight fleece hoodie with a front pocket and lined hood.",
            "中等厚度抓绒连帽卫衣，带前袋与内衬帽子。",
            "Hoodie bulu sederhana tebal dengan poket hadapan dan hud berlapik.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("grey", "m"), 8900, 6),
            (("grey", "l"), 8900, 6),
            (("black", "m"), 8900, 8),
            (("black", "l"), 8900, 8),
            (("black", "xl"), 9500, 4),
        ),
    },
    {
        "slug": "cotton-cap",
        "category": "apparel",
        "name": ("Cotton Cap", "棉质棒球帽", "Topi Kapas"),
        "description": (
            "A six-panel cap with an adjustable strap at the back.",
            "六片式棒球帽，后部带可调节扣带。",
            "Topi enam panel dengan tali boleh laras di belakang.",
        ),
        "options": ("color",),
        "variants": (
            (("black",), 2500, 15),
            (("navy",), 2500, 15),
            (("red",), 2500, 10),
        ),
    },
    {
        "slug": "knit-beanie",
        "category": "apparel",
        "name": ("Knit Beanie", "针织毛线帽", "Topi Kait"),
        "description": (
            "A ribbed knit beanie that keeps you warm on cool evenings.",
            "罗纹针织毛线帽，凉爽的夜晚也能保暖。",
            "Topi kait berusuk yang memanaskan anda pada waktu malam yang sejuk.",
        ),
        "options": ("color",),
        "variants": (
            (("grey",), 2900, 10),
            (("green",), 2900, 10),
            (("yellow",), 2900, 6),
        ),
    },
    {
        "slug": "ankle-socks-3-pack",
        "category": "apparel",
        "name": ("Ankle Socks, 3 Pairs", "短袜三双装", "Stoking Buku Lali, 3 Pasang"),
        "description": (
            "Three pairs of breathable cotton ankle socks in one size.",
            "三双透气棉质短袜，均码。",
            "Tiga pasang stoking buku lali kapas yang telap udara, satu saiz.",
        ),
        "options": (),
        "variants": (((), 1500, 30),),
    },
    {
        "slug": "canvas-tote-bag",
        "category": "bags",
        "name": ("Canvas Tote Bag", "帆布托特包", "Beg Tote Kanvas"),
        "description": (
            "A sturdy canvas tote with long handles and an inner pocket.",
            "结实的帆布托特包，长提手，带内袋。",
            "Beg tote kanvas yang kukuh dengan pemegang panjang dan poket dalam.",
        ),
        "options": ("color",),
        "variants": (
            (("natural",), 3500, 20),
            (("black",), 3500, 20),
            (("green",), 3500, 12),
        ),
    },
    {
        "slug": "everyday-backpack",
        "category": "bags",
        "name": ("Everyday Backpack", "日常双肩包", "Beg Galas Harian"),
        "description": (
            "A water-resistant backpack with a padded laptop sleeve.",
            "防泼水双肩包，带加厚电脑隔层。",
            "Beg galas kalis air dengan ruang komputer riba berlapik.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("black", "medium"), 12900, 8),
            (("black", "large"), 14900, 6),
            (("grey", "medium"), 12900, 8),
        ),
    },
    {
        "slug": "zip-pouch",
        "category": "bags",
        "name": ("Zip Pouch", "拉链收纳包", "Pouch Berzip"),
        "description": (
            "A flat zip pouch for cables, cards or small toiletries.",
            "扁平拉链收纳包，可装线材、卡片或小件洗漱用品。",
            "Pouch berzip leper untuk kabel, kad atau barang mandian kecil.",
        ),
        "options": (),
        "variants": (((), 1800, 25),),
    },
    {
        "slug": "drawstring-bag",
        "category": "bags",
        "name": ("Drawstring Bag", "抽绳束口袋", "Beg Bertali Serut"),
        "description": (
            "A light drawstring bag that folds into its own pocket.",
            "轻便抽绳束口袋，可收进自带的小口袋。",
            "Beg bertali serut yang ringan dan boleh dilipat ke dalam poketnya sendiri.",
        ),
        "options": ("color",),
        "variants": (
            (("red",), 1200, 20),
            (("blue",), 1200, 20),
            (("black",), 1200, 20),
        ),
    },
    {
        "slug": "weekender-duffel",
        "category": "bags",
        "name": ("Weekender Duffel", "周末旅行袋", "Beg Duffel Hujung Minggu"),
        "description": (
            "A roomy duffel with a shoe compartment and shoulder strap.",
            "宽敞的旅行袋，带鞋袋与肩带。",
            "Beg duffel yang luas dengan ruang kasut dan tali bahu.",
        ),
        "options": (),
        "variants": (((), 18900, 5),),
    },
    {
        "slug": "linen-cushion-cover",
        "category": "home",
        "name": ("Linen Cushion Cover", "亚麻抱枕套", "Sarung Kusyen Linen"),
        "description": (
            "A 45 cm linen cushion cover with a hidden zip.",
            "45 厘米亚麻抱枕套，隐形拉链。",
            "Sarung kusyen linen 45 cm dengan zip tersembunyi.",
        ),
        "options": ("color",),
        "variants": (
            (("natural",), 4500, 10),
            (("grey",), 4500, 10),
            (("pink",), 4500, 8),
        ),
    },
    {
        "slug": "cotton-bath-towel",
        "category": "home",
        "name": ("Cotton Bath Towel", "纯棉浴巾", "Tuala Mandi Kapas"),
        "description": (
            "An absorbent cotton towel that dries quickly.",
            "吸水性好、干得快的纯棉毛巾。",
            "Tuala kapas yang menyerap air dan cepat kering.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("white", "medium"), 2900, 15),
            (("white", "large"), 4900, 12),
            (("grey", "medium"), 2900, 15),
            (("grey", "large"), 4900, 12),
            (("navy", "large"), 4900, 10),
        ),
    },
    {
        "slug": "soy-wax-candle",
        "category": "home",
        "name": ("Soy Wax Candle", "大豆蜡香薰蜡烛", "Lilin Soya"),
        "description": (
            "A lightly scented candle in a reusable glass jar.",
            "淡香蜡烛，装在可重复使用的玻璃罐里。",
            "Lilin berbau lembut dalam balang kaca yang boleh diguna semula.",
        ),
        "options": (),
        "variants": (((), 3200, 18),),
    },
    {
        # 刻意只有英文，演示「仅英文」标签。
        "slug": "woven-storage-basket",
        "category": "home",
        "name": ("Woven Storage Basket", None, None),
        "description": (
            "A hand-woven basket with side handles for blankets or toys.",
            None,
            None,
        ),
        "options": (),
        "variants": (((), 5500, 7),),
    },
    {
        "slug": "round-wall-clock",
        "category": "home",
        "name": ("Round Wall Clock", "圆形挂钟", "Jam Dinding Bulat"),
        "description": (
            "A quiet 30 cm wall clock with a clean, simple face.",
            "30 厘米静音挂钟，表盘简洁。",
            "Jam dinding 30 cm yang senyap dengan muka jam yang ringkas.",
        ),
        "options": ("color",),
        "variants": (
            (("black",), 6900, 6),
            (("white",), 6900, 6),
        ),
    },
    {
        "slug": "ceramic-mug",
        "category": "kitchen",
        "name": ("Ceramic Mug", "陶瓷马克杯", "Mug Seramik"),
        "description": (
            "A 350 ml stoneware mug that is safe for the dishwasher.",
            "350 毫升炻瓷马克杯，可放入洗碗机。",
            "Mug seramik 350 ml yang selamat untuk mesin basuh pinggan.",
        ),
        "options": ("color",),
        "variants": (
            (("white",), 2200, 24),
            (("black",), 2200, 24),
            (("green",), 2400, 12),
        ),
    },
    {
        "slug": "stainless-water-bottle",
        "category": "kitchen",
        "name": ("Stainless Water Bottle", "不锈钢保温水瓶", "Botol Air Keluli Tahan Karat"),
        "description": (
            "An insulated bottle that keeps drinks cold. Small is 500 ml, large is 750 ml.",
            "保温水瓶，饮品可长时间保持冰凉。小号 500 毫升，大号 750 毫升。",
            "Botol bertebat untuk minuman sejuk. Kecil 500 ml, besar 750 ml.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("black", "small"), 4500, 10),
            (("black", "large"), 5500, 10),
            (("blue", "small"), 4500, 10),
            (("blue", "large"), 5500, 8),
            (("white", "small"), 4500, 8),
        ),
    },
    {
        "slug": "bamboo-cutting-board",
        "category": "kitchen",
        "name": ("Bamboo Cutting Board", "竹制砧板", "Papan Pemotong Buluh"),
        "description": (
            "A solid bamboo board with a juice groove around the edge.",
            "实心竹砧板，边缘有导汁槽。",
            "Papan buluh padu dengan alur jus di sekeliling tepinya.",
        ),
        "options": (),
        "variants": (((), 3900, 14),),
    },
    {
        # 所有 SKU 库存为 0，演示「今日售罄」。
        "slug": "cotton-apron",
        "category": "kitchen",
        "name": ("Cotton Apron", "棉质围裙", "Apron Kapas"),
        "description": (
            "A full-length apron with a large front pocket and long ties.",
            "全身围裙，带大前袋与长系带。",
            "Apron panjang penuh dengan poket hadapan yang besar dan tali panjang.",
        ),
        "options": ("color",),
        "variants": (
            (("navy",), 2800, 0),
            (("red",), 2800, 0),
        ),
    },
    {
        "slug": "glass-food-container-set",
        "category": "kitchen",
        "name": ("Glass Food Container Set", "玻璃保鲜盒套装", "Set Bekas Makanan Kaca"),
        "description": (
            "Three glass containers with snap-lock lids, safe for the oven.",
            "三个玻璃保鲜盒，带卡扣盖，可进烤箱。",
            "Tiga bekas kaca dengan penutup berkunci, selamat untuk ketuhar.",
        ),
        "options": (),
        "variants": (((), 6500, 9),),
    },
    {
        "slug": "dotted-notebook",
        "category": "stationery",
        "name": ("Dotted Notebook", "点阵笔记本", "Buku Nota Bertitik"),
        "description": (
            "A lay-flat notebook with 160 dotted pages and a ribbon marker.",
            "可平摊的笔记本，160 页点阵内页，附书签带。",
            "Buku nota yang boleh dibuka rata dengan 160 halaman bertitik dan penanda reben.",
        ),
        "options": ("color",),
        "variants": (
            (("black",), 1600, 30),
            (("blue",), 1600, 30),
            (("red",), 1600, 20),
        ),
    },
    {
        "slug": "gel-pen-set",
        "category": "stationery",
        "name": ("Gel Pen Set", "中性笔套装", "Set Pen Gel"),
        "description": (
            "Six smooth gel pens in black, blue and red ink.",
            "六支书写顺滑的中性笔，黑、蓝、红三色墨水。",
            "Enam batang pen gel yang licin dengan dakwat hitam, biru dan merah.",
        ),
        "options": (),
        "variants": (((), 900, 40),),
    },
    {
        "slug": "desk-organiser",
        "category": "stationery",
        "name": ("Desk Organiser", "桌面收纳盒", "Penyusun Meja"),
        "description": (
            "A wooden organiser with slots for pens, notes and a phone.",
            "木质桌面收纳盒，分格放笔、便签与手机。",
            "Penyusun kayu dengan ruang untuk pen, nota dan telefon.",
        ),
        "options": (),
        "variants": (((), 3500, 10),),
    },
    {
        "slug": "sticky-notes-pack",
        "category": "stationery",
        "name": ("Sticky Notes Pack", "便利贴组合装", "Pek Nota Pelekat"),
        "description": (
            "Four pads of repositionable sticky notes in soft colours.",
            "四本可重复粘贴的便利贴，柔和配色。",
            "Empat pad nota pelekat yang boleh dilekat semula dalam warna lembut.",
        ),
        "options": (),
        "variants": (((), 500, 50),),
    },
    {
        "slug": "zip-pencil-case",
        "category": "stationery",
        "name": ("Zip Pencil Case", "拉链笔袋", "Bekas Pensel Berzip"),
        "description": (
            "A canvas pencil case that holds up to 20 pens.",
            "帆布拉链笔袋，最多可装 20 支笔。",
            "Bekas pensel kanvas yang memuatkan sehingga 20 batang pen.",
        ),
        "options": ("color",),
        "variants": (
            (("grey",), 1400, 20),
            (("pink",), 1400, 15),
            (("navy",), 1400, 15),
        ),
    },
    {
        "slug": "wireless-mouse",
        "category": "gadgets",
        "name": ("Wireless Mouse", "无线鼠标", "Tetikus Tanpa Wayar"),
        "description": (
            "A quiet-click wireless mouse with a rechargeable battery.",
            "静音按键无线鼠标，内置可充电电池。",
            "Tetikus tanpa wayar dengan klik senyap dan bateri boleh dicas semula.",
        ),
        "options": ("color",),
        "variants": (
            (("black",), 5900, 12),
            (("white",), 5900, 8),
        ),
    },
    {
        "slug": "braided-charging-cable",
        "category": "gadgets",
        "name": ("Braided Charging Cable", "编织充电线", "Kabel Pengecas Berjalin"),
        "description": (
            "A tangle-resistant braided cable for charging and data.",
            "不易缠绕的编织线，可充电与传输数据。",
            "Kabel berjalin yang tidak mudah kusut untuk mengecas dan data.",
        ),
        "options": ("color", "size"),
        "variants": (
            (("black", "1m"), 1500, 30),
            (("black", "2m"), 1900, 20),
            (("white", "1m"), 1500, 25),
        ),
    },
    {
        "slug": "folding-phone-stand",
        "category": "gadgets",
        "name": ("Folding Phone Stand", "折叠手机支架", "Dirian Telefon Lipat"),
        "description": (
            "An aluminium stand with adjustable angles that folds flat.",
            "铝合金手机支架，角度可调，可折叠收平。",
            "Dirian aluminium dengan sudut boleh laras yang boleh dilipat rata.",
        ),
        "options": (),
        "variants": (((), 2500, 16),),
    },
    {
        "slug": "compact-power-bank",
        "category": "gadgets",
        "name": ("Compact Power Bank", "小巧移动电源", "Bank Kuasa Padat"),
        "description": (
            "A pocket-sized 10000 mAh power bank with two output ports.",
            "口袋大小的 10000 毫安时移动电源，两个输出接口。",
            "Bank kuasa 10000 mAh bersaiz poket dengan dua port keluaran.",
        ),
        "options": (),
        "variants": (((), 8900, 10),),
    },
    {
        "slug": "portable-speaker",
        "category": "gadgets",
        "name": ("Portable Speaker", "便携音箱", "Pembesar Suara Mudah Alih"),
        "description": (
            "A splash-proof wireless speaker with 12 hours of playtime.",
            "防泼溅无线音箱，续航 12 小时。",
            "Pembesar suara tanpa wayar kalis percikan dengan masa main 12 jam.",
        ),
        "options": ("color",),
        "variants": (
            (("black",), 29900, 4),
            (("blue",), 29900, 3),
        ),
    },
)

# 第一件商品的创建时间，其余依次晚一个间隔：各不相同、确定，按最新排序时后写的排在前面。
CREATED_AT_START = datetime(2026, 9, 1, 1, 0)
CREATED_AT_STEP = timedelta(hours=7)


def _image_ref(category: str) -> str:
    return f"/demo-images/{category}.svg"


def _sku(slug: str, value_codes: tuple[str, ...]) -> str:
    # 商品 slug 加各规格值 code；无规格商品唯一的 SKU 以 -std 结尾。
    return "-".join((slug, *(value_codes or ("std",))))


CATEGORY_SLUGS = [slug for slug, *_ in CATEGORIES]
PRODUCT_SLUGS = [product["slug"] for product in PRODUCTS]
SKUS = [_sku(p["slug"], codes) for p in PRODUCTS for codes, _, _ in p["variants"]]

categories = sa.table(
    "categories",
    sa.column("id", sa.Integer),
    sa.column("slug", sa.String),
    sa.column("name_en", sa.String),
    sa.column("name_zh", sa.String),
    sa.column("name_ms", sa.String),
    sa.column("is_active", sa.Boolean),
)
products = sa.table(
    "products",
    sa.column("id", sa.Integer),
    sa.column("category_id", sa.Integer),
    sa.column("slug", sa.String),
    sa.column("name_en", sa.String),
    sa.column("name_zh", sa.String),
    sa.column("name_ms", sa.String),
    sa.column("description_en", sa.Text),
    sa.column("description_zh", sa.Text),
    sa.column("description_ms", sa.Text),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime),
)
product_images = sa.table(
    "product_images",
    sa.column("product_id", sa.Integer),
    sa.column("storage_ref", sa.String),
    sa.column("sort_order", sa.Integer),
)
product_options = sa.table(
    "product_options",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("sort_order", sa.Integer),
    sa.column("name_en", sa.String),
    sa.column("name_zh", sa.String),
    sa.column("name_ms", sa.String),
)
product_option_values = sa.table(
    "product_option_values",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("option_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("sort_order", sa.Integer),
    sa.column("name_en", sa.String),
    sa.column("name_zh", sa.String),
    sa.column("name_ms", sa.String),
)
product_variants = sa.table(
    "product_variants",
    sa.column("id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("sku", sa.String),
    sa.column("price_sen", sa.Integer),
    sa.column("daily_initial_stock", sa.Integer),
    sa.column("available_stock", sa.Integer),
    sa.column("is_active", sa.Boolean),
)
variant_option_values = sa.table(
    "variant_option_values",
    sa.column("variant_id", sa.Integer),
    sa.column("option_value_id", sa.Integer),
    sa.column("product_id", sa.Integer),
    sa.column("option_id", sa.Integer),
)


def _names(field: str, texts: tuple[str | None, str | None, str | None]) -> dict:
    return dict(zip((f"{field}_en", f"{field}_zh", f"{field}_ms"), texts, strict=True))


def _ids(table: sa.TableClause, key: str, values: list[str]) -> dict[str, int]:
    """按 slug 或 SKU 查回刚写入的行 id。"""
    stmt = sa.select(table.c[key], table.c.id).where(table.c[key].in_(values))
    return dict(op.get_bind().execute(stmt).all())


def upgrade() -> None:
    bind = op.get_bind()

    category_rows = [
        {"slug": slug, "is_active": True, **_names("name", (en, zh, ms))}
        for slug, en, zh, ms in CATEGORIES
    ]
    op.bulk_insert(categories, category_rows)
    category_ids = _ids(categories, "slug", CATEGORY_SLUGS)

    product_rows = [
        {
            "category_id": category_ids[product["category"]],
            "slug": product["slug"],
            **_names("name", product["name"]),
            **_names("description", product["description"]),
            "is_active": True,
            "created_at": CREATED_AT_START + index * CREATED_AT_STEP,
        }
        for index, product in enumerate(PRODUCTS)
    ]
    op.bulk_insert(products, product_rows)
    product_ids = _ids(products, "slug", PRODUCT_SLUGS)
    seed_product_ids = list(product_ids.values())

    image_rows = [
        {
            "product_id": product_ids[product["slug"]],
            "storage_ref": _image_ref(product["category"]),
            "sort_order": 0,
        }
        for product in PRODUCTS
    ]
    op.bulk_insert(product_images, image_rows)

    option_rows = [
        {
            "product_id": product_ids[product["slug"]],
            "code": code,
            "sort_order": sort_order,
            **_names("name", OPTIONS[code]),
        }
        for product in PRODUCTS
        for sort_order, code in enumerate(product["options"])
    ]
    op.bulk_insert(product_options, option_rows)
    columns = product_options.c
    stmt = sa.select(columns.id, columns.product_id, columns.code)
    stmt = stmt.where(columns.product_id.in_(seed_product_ids))
    option_ids = {(pid, code): oid for oid, pid, code in bind.execute(stmt)}

    # 每件商品只写它的 SKU 用到的规格值。
    value_rows = []
    for product in PRODUCTS:
        product_id = product_ids[product["slug"]]
        for position, option_code in enumerate(product["options"]):
            used = {codes[position] for codes, _, _ in product["variants"]}
            known = OPTION_VALUES[option_code]
            for sort_order, value_code in enumerate(known):
                if value_code in used:
                    value_rows.append(
                        {
                            "product_id": product_id,
                            "option_id": option_ids[(product_id, option_code)],
                            "code": value_code,
                            "sort_order": sort_order,
                            **_names("name", known[value_code]),
                        }
                    )
    op.bulk_insert(product_option_values, value_rows)
    columns = product_option_values.c
    stmt = sa.select(columns.id, columns.option_id, columns.code)
    stmt = stmt.where(columns.product_id.in_(seed_product_ids))
    value_ids = {(oid, code): vid for vid, oid, code in bind.execute(stmt)}

    variant_rows = [
        {
            "product_id": product_ids[product["slug"]],
            "sku": _sku(product["slug"], codes),
            "price_sen": price_sen,
            "daily_initial_stock": stock,
            "available_stock": stock,
            "is_active": True,
        }
        for product in PRODUCTS
        for codes, price_sen, stock in product["variants"]
    ]
    op.bulk_insert(product_variants, variant_rows)
    variant_ids = _ids(product_variants, "sku", SKUS)

    link_rows = []
    for product in PRODUCTS:
        product_id = product_ids[product["slug"]]
        for codes, _, _ in product["variants"]:
            for option_code, value_code in zip(product["options"], codes, strict=True):
                option_id = option_ids[(product_id, option_code)]
                link_rows.append(
                    {
                        "variant_id": variant_ids[_sku(product["slug"], codes)],
                        "option_value_id": value_ids[(option_id, value_code)],
                        "product_id": product_id,
                        "option_id": option_id,
                    }
                )
    op.bulk_insert(variant_option_values, link_rows)


def downgrade() -> None:
    # 按依赖倒序显式删除，不依赖外键的级联删除。
    seed_products = sa.select(products.c.id).where(products.c.slug.in_(PRODUCT_SLUGS))
    seed_variants = sa.select(product_variants.c.id).where(product_variants.c.sku.in_(SKUS))

    op.execute(
        variant_option_values.delete().where(variant_option_values.c.variant_id.in_(seed_variants))
    )
    op.execute(product_variants.delete().where(product_variants.c.sku.in_(SKUS)))
    op.execute(
        product_option_values.delete().where(product_option_values.c.product_id.in_(seed_products))
    )
    op.execute(product_options.delete().where(product_options.c.product_id.in_(seed_products)))
    op.execute(product_images.delete().where(product_images.c.product_id.in_(seed_products)))
    op.execute(products.delete().where(products.c.slug.in_(PRODUCT_SLUGS)))
    op.execute(categories.delete().where(categories.c.slug.in_(CATEGORY_SLUGS)))

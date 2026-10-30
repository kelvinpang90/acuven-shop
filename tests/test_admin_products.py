"""后台商品的读取与新建（app/services/admin_products.py，SHOP-TASK-183）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（下称「第 11 条」）
与 docs/HANDOFF.md 0.77。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool），示例商品照迁移 0003
（alembic/versions/20260930_0003_demo_catalog.py）的写法造：规格名代码 color 的英文名称为
Colour，规格值 1m 的英文名称为 1 m，SKU 为 slug 接各规格值代码，没有规格名的为 slug 接 -std。
只经 create_product、load_admin_products 与 load_admin_product 调用；结果以只查列值的 SELECT
读出。SQLite 不生成共享锁：锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Product,
    ProductImage,
    ProductOption,
    ProductOptionValue,
    ProductVariant,
    VariantOptionValue,
)
from app.services import admin_products
from app.services.admin_products import (
    AdminProduct,
    AdminProductImage,
    AdminProductListItem,
    AdminProductOption,
    AdminProductValue,
    AdminProductVariant,
    OptionInput,
    ProductChanged,
    ProductInput,
    ProductInvalid,
    ValueInput,
    ValueRef,
    VariantInput,
    create_product,
    load_admin_product,
    load_admin_products,
)
from app.services.catalog import list_products

NOW = datetime(2026, 10, 30, 3, 0)

# ---- 示例数据（照迁移 0003） ----

# (slug, 英文, 中文, 马来文, 启用)；写入顺序即 id 顺序。分类 4 启用但没有英文名称。
CATEGORIES = (
    ("apparel", "Apparel", "服饰", "Pakaian", True),
    ("gadgets", "Gadgets", "数码小物", "Gajet", True),
    ("archive", "Archive", "归档", "Arkib", False),
    ("misc", None, "杂项", None, True),
)

OPTIONS = {
    "color": ("Colour", "颜色", "Warna"),
    "size": ("Size", "尺寸", "Saiz"),
}

# 在字典中的位置即规格值的排列序号。
OPTION_VALUES = {
    "color": {
        "black": ("Black", "黑色", "Hitam"),
        "white": ("White", "白色", "Putih"),
        "grey": ("Grey", "灰色", "Kelabu"),
        "navy": ("Navy", "藏青色", "Biru Tua"),
    },
    "size": {
        "s": ("S", "S", "S"),
        "m": ("M", "M", "M"),
        "l": ("L", "L", "L"),
        "xl": ("XL", "XL", "XL"),
        "1m": ("1 m", "1 米", "1 m"),
        "2m": ("2 m", "2 米", "2 m"),
    },
}

# created 为创建时间的间隔序号：后两件同一时间。
PRODUCTS = (
    {
        "slug": "crew-neck-tee",
        "category": 1,
        "name": ("Crew Neck Tee", "圆领 T 恤", "Baju-T Leher Bulat"),
        "description": ("A soft cotton tee.", "柔软棉质 T 恤。", "Baju-T kapas yang lembut."),
        "is_active": True,
        "created": 0,
        "options": ("color", "size"),
        "variants": (
            (("black", "s"), 3900, 12),
            (("black", "m"), 3900, 20),
            (("white", "m"), 3900, 18),
            (("navy", "l"), 3900, 15),
        ),
    },
    {
        "slug": "braided-charging-cable",
        "category": 2,
        "name": ("Braided Charging Cable", "编织充电线", "Kabel Pengecas Berjalin"),
        "description": ("A braided cable.", "编织线。", "Kabel berjalin."),
        "is_active": True,
        "created": 1,
        "options": ("color", "size"),
        "variants": (
            (("black", "1m"), 1500, 30),
            (("black", "2m"), 1900, 20),
            (("white", "1m"), 1500, 25),
        ),
    },
    {
        "slug": "ankle-socks-3-pack",
        "category": 1,
        "name": ("Ankle Socks, 3 Pairs", "短袜三双装", "Stoking Buku Lali, 3 Pasang"),
        "description": ("Three pairs of socks.", "三双短袜。", "Tiga pasang stoking."),
        "is_active": True,
        "created": 2,
        "options": (),
        "variants": (((), 1500, 30),),
    },
    {
        "slug": "ceramic-mug",
        "category": 2,
        "name": ("Ceramic Mug", "陶瓷马克杯", "Mug Seramik"),
        "description": ("A stoneware mug.", "炻瓷马克杯。", "Mug seramik."),
        "is_active": False,
        "created": 3,
        "options": (),
        "variants": (((), 2200, 24),),
    },
    {
        "slug": "zip-pouch",
        "category": 2,
        "name": ("Zip Pouch", "拉链收纳包", "Pouch Berzip"),
        "description": ("A flat zip pouch.", "扁平拉链收纳包。", "Pouch berzip leper."),
        "is_active": True,
        "created": 3,
        "options": (),
        "variants": (((), 1800, 25),),
    },
)

CREATED_AT_START = datetime(2026, 9, 1, 1, 0)
CREATED_AT_STEP = timedelta(hours=7)


def _admin_value(
    value_id: int, code: str, name_en: str, name_zh: str | None, name_ms: str | None
) -> AdminProductValue:
    return AdminProductValue(
        id=value_id, code=code, name_en=name_en, name_zh=name_zh, name_ms=name_ms
    )


# 行 ID：商品 1–5，规格名 1–4，规格值 1–10，SKU 1–10，图片 1–5；新建商品从 6、5、11、11 起。
CABLE = AdminProduct(
    id=2,
    slug="braided-charging-cable",
    version=1,
    category_id=2,
    name_en="Braided Charging Cable",
    name_zh="编织充电线",
    name_ms="Kabel Pengecas Berjalin",
    description_en="A braided cable.",
    description_zh="编织线。",
    description_ms="Kabel berjalin.",
    is_active=True,
    max_per_order=10,
    options=[
        AdminProductOption(
            id=3,
            code="color",
            name_en="Colour",
            name_zh="颜色",
            name_ms="Warna",
            values=[
                _admin_value(7, "black", "Black", "黑色", "Hitam"),
                _admin_value(8, "white", "White", "白色", "Putih"),
            ],
        ),
        AdminProductOption(
            id=4,
            code="size",
            name_en="Size",
            name_zh="尺寸",
            name_ms="Saiz",
            values=[
                _admin_value(9, "1m", "1 m", "1 米", "1 m"),
                _admin_value(10, "2m", "2 m", "2 米", "2 m"),
            ],
        ),
    ],
    variants=[
        AdminProductVariant(
            id=5,
            sku="braided-charging-cable-black-1m",
            price_sen=1500,
            daily_initial_stock=30,
            available_stock=30,
            is_active=True,
            option_value_ids=[7, 9],
        ),
        AdminProductVariant(
            id=6,
            sku="braided-charging-cable-black-2m",
            price_sen=1900,
            daily_initial_stock=20,
            available_stock=20,
            is_active=True,
            option_value_ids=[7, 10],
        ),
        AdminProductVariant(
            id=7,
            sku="braided-charging-cable-white-1m",
            price_sen=1500,
            daily_initial_stock=25,
            available_stock=25,
            is_active=True,
            option_value_ids=[8, 9],
        ),
    ],
    images=[AdminProductImage(id=2, storage_ref="/demo-images/gadgets.svg")],
)


def _names(field: str, texts: tuple[str | None, str | None, str | None]) -> dict:
    return dict(zip((f"{field}_en", f"{field}_zh", f"{field}_ms"), texts, strict=True))


def _sku(slug: str, value_codes: tuple[str, ...]) -> str:
    return "-".join((slug, *(value_codes or ("std",))))


def _seed(db: Session) -> None:
    for slug, en, zh, ms, active in CATEGORIES:
        category = Category(
            slug=slug,
            is_active=active,
            version=1,
            **_names("name", (en, zh, ms)),
        )
        db.add(category)
        db.flush()
    for product in PRODUCTS:
        row = Product(
            category_id=product["category"],
            slug=product["slug"],
            **_names("name", product["name"]),
            **_names("description", product["description"]),
            is_active=product["is_active"],
            max_per_order=10,
            created_at=CREATED_AT_START + product["created"] * CREATED_AT_STEP,
            version=1,
        )
        db.add(row)
        db.flush()
        category_slug = CATEGORIES[product["category"] - 1][0]
        image_ref = f"/demo-images/{category_slug}.svg"
        db.add(ProductImage(product_id=row.id, storage_ref=image_ref, sort_order=0))
        db.flush()

        option_ids: dict[str, int] = {}
        value_ids: dict[tuple[str, str], int] = {}
        for sort_order, code in enumerate(product["options"]):
            option = ProductOption(
                product_id=row.id,
                code=code,
                sort_order=sort_order,
                **_names("name", OPTIONS[code]),
            )
            db.add(option)
            db.flush()
            option_ids[code] = option.id
            used = {codes[sort_order] for codes, _, _ in product["variants"]}
            for value_order, (value_code, texts) in enumerate(OPTION_VALUES[code].items()):
                if value_code not in used:
                    continue
                value = ProductOptionValue(
                    product_id=row.id,
                    option_id=option.id,
                    code=value_code,
                    sort_order=value_order,
                    **_names("name", texts),
                )
                db.add(value)
                db.flush()
                value_ids[(code, value_code)] = value.id

        for codes, price_sen, stock in product["variants"]:
            variant = ProductVariant(
                product_id=row.id,
                sku=_sku(product["slug"], codes),
                price_sen=price_sen,
                daily_initial_stock=stock,
                available_stock=stock,
                is_active=True,
            )
            db.add(variant)
            db.flush()
            for option_code, value_code in zip(product["options"], codes, strict=True):
                link = VariantOptionValue(
                    variant_id=variant.id,
                    option_value_id=value_ids[(option_code, value_code)],
                    product_id=row.id,
                    option_id=option_ids[option_code],
                )
                db.add(link)
            db.flush()


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        _seed(seed)
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin_id(db: Session) -> int:
    account = AdminAccount(
        username="admin@example.test", password_hash="x" * 60, password_updated_at=NOW
    )
    db.add(account)
    db.commit()
    return account.id


# ---- 请求 ----


def _value(temp_id: str, name_en: str, name_zh: str = "", name_ms: str = "") -> ValueInput:
    return ValueInput(id=None, temp_id=temp_id, name_en=name_en, name_zh=name_zh, name_ms=name_ms)


def _option(name_en: str, *values: ValueInput, name_zh: str = "") -> OptionInput:
    return OptionInput(id=None, name_en=name_en, name_zh=name_zh, name_ms="", values=list(values))


def _refs(*temp_ids: str) -> list[ValueRef]:
    return [ValueRef(id=None, temp_id=temp_id) for temp_id in temp_ids]


def _variant(
    temp_id: str,
    *value_temp_ids: str,
    price_sen: int = 2500,
    daily_initial_stock: int = 10,
    is_active: bool = True,
) -> VariantInput:
    return VariantInput(
        id=None,
        temp_id=temp_id,
        option_values=_refs(*value_temp_ids),
        price_sen=price_sen,
        daily_initial_stock=daily_initial_stock,
        is_active=is_active,
    )


BASE = ProductInput(
    category_id=1,
    name_en="Linen Shirt",
    name_zh="",
    name_ms="",
    description_en="A linen shirt.",
    description_zh="",
    description_ms="",
    is_active=True,
    max_per_order=10,
    options=[
        _option("Colour", _value("black", "Black"), _value("white", "White")),
        _option("Size", _value("s", "S"), _value("m", "M")),
    ],
    variants=[
        _variant("v1", "black", "s"),
        _variant("v2", "black", "m"),
        _variant("v3", "white", "s"),
    ],
)


def _with(**fields: object) -> ProductInput:
    return replace(BASE, **fields)


def _with_option(index: int, **fields: object) -> ProductInput:
    options = list(BASE.options)
    options[index] = replace(options[index], **fields)
    return _with(options=options)


def _with_value(option_index: int, value_index: int, **fields: object) -> ProductInput:
    option = BASE.options[option_index]
    values = list(option.values)
    values[value_index] = replace(values[value_index], **fields)
    return _with_option(option_index, values=values)


def _with_variant(index: int, **fields: object) -> ProductInput:
    variants = list(BASE.variants)
    variants[index] = replace(variants[index], **fields)
    return _with(variants=variants)


def _simple(name_en: str, **fields: object) -> ProductInput:
    """没有规格名、只有一个 SKU 的商品；fields 覆盖默认值。"""
    defaults = {"name_en": name_en, "options": [], "variants": [_variant("v1")]}
    return _with(**(defaults | fields))


def _grid(colours: int, sizes: int, count: int) -> ProductInput:
    """colours 个颜色乘 sizes 个尺寸的规格，取前 count 个组合作 SKU。"""
    colour_values = [_value(f"c{i}", f"Colour {i}") for i in range(colours)]
    size_values = [_value(f"s{i}", f"Size {i}") for i in range(sizes)]
    combos = [(f"c{c}", f"s{s}") for c in range(colours) for s in range(sizes)][:count]
    return _with(
        options=[_option("Colour", *colour_values), _option("Size", *size_values)],
        variants=[_variant(f"v{i}", *combo) for i, combo in enumerate(combos)],
    )


# ---- 辅助 ----


def _rows(db: Session, *columns: object, order_by: object) -> list[tuple]:
    return [tuple(row) for row in db.execute(select(*columns).order_by(order_by))]


def _table(db: Session, model: type) -> list[tuple]:
    table = model.__table__
    stmt = select(table).order_by(*table.primary_key)
    return [tuple(row) for row in db.execute(stmt)]


def _sort_orders(db: Session, model: type) -> list[tuple]:
    return _rows(db, model.id, model.sort_order, order_by=model.id)


def _audits(db: Session) -> list[tuple]:
    return _rows(
        db,
        AuditEvent.action,
        AuditEvent.target_type,
        AuditEvent.target_id,
        AuditEvent.old_value,
        AuditEvent.new_value,
        order_by=AuditEvent.id,
    )


def _snapshot(db: Session) -> tuple:
    models = (
        Category,
        Product,
        ProductImage,
        ProductOption,
        ProductOptionValue,
        ProductVariant,
        VariantOptionValue,
    )
    return tuple(_table(db, model) for model in models), _audits(db)


def _record_statements(engine: Engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    return statements


def _record_sequence(engine: Engine) -> list[str]:
    """回滚记为 ROLLBACK，SELECT 按 MySQL 方言编译，其他语句记类名。"""
    seen: list[str] = []

    @event.listens_for(engine, "rollback")
    def _rollback(_conn) -> None:
        seen.append("ROLLBACK")

    @event.listens_for(engine, "before_execute")
    def _execute(_conn, clauseelement, _multiparams, _params, _execution_options) -> None:
        # 只编译 SELECT：SQLite 上 ORM 的 INSERT 可能带 RETURNING，MySQL 方言编译不了。
        if isinstance(clauseelement, Select):
            seen.append(str(clauseelement.compile(dialect=mysql.dialect())))
        else:
            seen.append(type(clauseelement).__name__)

    return seen


def _create(db: Session, admin_id: int, product: ProductInput) -> AdminProduct:
    return create_product(db, admin_id, product, NOW)


def _codes(created: AdminProduct) -> tuple[list[str], list[list[str]], list[str]]:
    options = [option.code for option in created.options]
    values = [[value.code for value in option.values] for option in created.options]
    skus = [variant.sku for variant in created.variants]
    return options, values, skus


def _front_products(db: Session) -> list[str]:
    return sorted(item.slug for item in list_products(db, lang="en").items)


# ---- 读取 ----


def test_list_includes_inactive_newest_first(db: Session) -> None:
    """第 11 条「管理员在后台 A04 查看全部商品（含未上架的，按新建时间从新到旧、同时间按 ID
    从大到小，不分页）」：未上架的 ceramic-mug 也在；与它同一时间、ID 更大的 zip-pouch 在前。"""
    items = load_admin_products(db)

    assert [item.slug for item in items] == [
        "zip-pouch",
        "ceramic-mug",
        "ankle-socks-3-pack",
        "braided-charging-cable",
        "crew-neck-tee",
    ]
    assert items[1] == AdminProductListItem(
        id=4,
        slug="ceramic-mug",
        name_en="Ceramic Mug",
        name_zh="陶瓷马克杯",
        name_ms="Mug Seramik",
        is_active=False,
        category_id=2,
        category_name_en="Gadgets",
        category_name_zh="数码小物",
        category_name_ms="Gajet",
    )


def test_load_product_returns_all_editable_fields(db: Session) -> None:
    """第 11 条「读取一件商品时带它的版本号与全部规格名、规格值、SKU 的行 ID」「读取商品时
    返回它当前所属的分类」；HANDOFF 0.77「读取单件商品时一并返回它的图片」。"""
    assert load_admin_product(db, 2) == CABLE


def test_load_product_orders_options_values_variants_and_images(db: Session) -> None:
    """第 11 条「读取一件商品时带……全部规格名、规格值、SKU 的行 ID」：规格名与规格值按排列
    序号（同序按 ID），SKU 按 ID，SKU 的规格值按规格名的顺序，图片按排列序号。"""
    # 圆领 T 恤：尺寸排到颜色前面；颜色的值倒过来排，尺寸的值同序；新图片排到示例图前面。
    db.get(ProductOption, 1).sort_order = 2
    for value_id, sort_order in {1: 9, 2: 8, 3: 7, 4: 0, 5: 0, 6: 0}.items():
        db.get(ProductOptionValue, value_id).sort_order = sort_order
    db.get(ProductImage, 1).sort_order = 1
    db.flush()
    db.add(ProductImage(product_id=1, storage_ref="/uploads/front.webp", sort_order=0))
    db.commit()

    tee = load_admin_product(db, 1)

    assert tee is not None
    assert [option.code for option in tee.options] == ["size", "color"]
    assert [[value.id for value in option.values] for option in tee.options] == [
        [4, 5, 6],
        [3, 2, 1],
    ]
    assert [variant.id for variant in tee.variants] == [1, 2, 3, 4]
    assert [variant.option_value_ids for variant in tee.variants] == [
        [4, 1],
        [5, 1],
        [5, 2],
        [6, 3],
    ]
    assert [image.storage_ref for image in tee.images] == [
        "/uploads/front.webp",
        "/demo-images/apparel.svg",
    ]


def test_load_missing_product_is_none(db: Session) -> None:
    """第 11 条「读取一件商品时带它的版本号……」：没有这件商品时返回 None。"""
    assert load_admin_product(db, 999) is None


def test_loads_only_select(db: Session, engine: Engine) -> None:
    """第 11 条「管理员在后台 A04 查看全部商品」「读取一件商品时带它的版本号……」：读取只是
    查看，只发 SELECT，不改库。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    load_admin_products(db)
    load_admin_product(db, 1)
    load_admin_product(db, 999)

    assert statements
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert _snapshot(db) == before


# ---- 新建 ----


def test_create_product(db: Session, admin_id: int) -> None:
    """第 11 条「新建商品的版本号为 1」「首尾空白去掉，空串存为空」「新增 SKU 的当日可用库存
    等于它的每日初始库存」「新建商品写一条（新值为 slug），随它新建的规格名、规格值与 SKU 各写
    一条（新值为代码）」；规格名、规格值按请求顺序排列。"""
    request = _with(
        name_en="  Linen Shirt  ",
        name_zh=" 亚麻衬衫 ",
        description_en=" A breathable linen shirt. ",
        max_per_order=5,
        options=[
            _option("Colour", _value("b1", "Black", "黑色"), _value("b2", "Sky Blue")),
            _option("Size", _value("m", "M"), _value("1m", " 1 M ")),
        ],
        variants=[
            _variant("v1", "b1", "m", price_sen=9900, daily_initial_stock=7),
            _variant("v2", "b2", "1m", price_sen=0, daily_initial_stock=0, is_active=False),
            _variant("v3", "b1", "1m", price_sen=50_000_000, daily_initial_stock=9999),
        ],
    )

    created = _create(db, admin_id, request)

    assert created == AdminProduct(
        id=6,
        slug="linen-shirt",
        version=1,
        category_id=1,
        name_en="Linen Shirt",
        name_zh="亚麻衬衫",
        name_ms=None,
        description_en="A breathable linen shirt.",
        description_zh=None,
        description_ms=None,
        is_active=True,
        max_per_order=5,
        options=[
            AdminProductOption(
                id=5,
                code="color",
                name_en="Colour",
                name_zh=None,
                name_ms=None,
                values=[
                    _admin_value(11, "black", "Black", "黑色", None),
                    _admin_value(12, "sky-blue", "Sky Blue", None, None),
                ],
            ),
            AdminProductOption(
                id=6,
                code="size",
                name_en="Size",
                name_zh=None,
                name_ms=None,
                values=[
                    _admin_value(13, "m", "M", None, None),
                    _admin_value(14, "1m", "1 M", None, None),
                ],
            ),
        ],
        variants=[
            AdminProductVariant(
                id=11,
                sku="linen-shirt-black-m",
                price_sen=9900,
                daily_initial_stock=7,
                available_stock=7,
                is_active=True,
                option_value_ids=[11, 13],
            ),
            AdminProductVariant(
                id=12,
                sku="linen-shirt-sky-blue-1m",
                price_sen=0,
                daily_initial_stock=0,
                available_stock=0,
                is_active=False,
                option_value_ids=[12, 14],
            ),
            AdminProductVariant(
                id=13,
                sku="linen-shirt-black-1m",
                price_sen=50_000_000,
                daily_initial_stock=9999,
                available_stock=9999,
                is_active=True,
                option_value_ids=[11, 14],
            ),
        ],
        images=[],
    )
    assert load_admin_product(db, 6) == created
    assert db.scalar(select(Product.created_at).where(Product.id == 6)) == NOW
    assert _sort_orders(db, ProductOption)[-2:] == [(5, 0), (6, 1)]
    assert _sort_orders(db, ProductOptionValue)[-4:] == [(11, 0), (12, 1), (13, 0), (14, 1)]


def test_create_writes_one_audit_per_new_row(db: Session, admin_id: int) -> None:
    """第 11 条「新建商品写一条（新值为 slug），随它新建的规格名、规格值与 SKU
    各写一条（新值为代码）」「对象为商品、规格名、规格值或 SKU，以行 ID 记录」；
    HANDOFF 0.77 的操作名与对象类别。"""
    _create(db, admin_id, BASE)

    assert _audits(db) == [
        ("admin_product_created", "product", 6, None, "linen-shirt"),
        ("admin_product_option_created", "product_option", 5, None, "color"),
        ("admin_product_option_created", "product_option", 6, None, "size"),
        ("admin_product_option_value_created", "product_option_value", 11, None, "black"),
        ("admin_product_option_value_created", "product_option_value", 12, None, "white"),
        ("admin_product_option_value_created", "product_option_value", 13, None, "s"),
        ("admin_product_option_value_created", "product_option_value", 14, None, "m"),
        ("admin_product_variant_created", "product_variant", 11, None, "linen-shirt-black-s"),
        ("admin_product_variant_created", "product_variant", 12, None, "linen-shirt-black-m"),
        ("admin_product_variant_created", "product_variant", 13, None, "linen-shirt-white-s"),
    ]
    accounts = db.scalars(select(AuditEvent.admin_account_id)).all()
    assert set(accounts) == {admin_id}


def test_create_accepts_upper_limits(db: Session, admin_id: int) -> None:
    """第 11 条「每件商品 0–3 个规格名，每个规格名 1–20 个值，1–50 个 SKU」「名称每种语言
    0–200 个字符，描述 0–5,000 个字符，规格名与规格值的名称 0–100 个字符」「0 到 50,000,000
    仙」「每日初始库存 0–9,999」「每单限购 1–1000」：上限一端都可以；
    代码截到各自的上限。"""
    sizes = [_value(f"s_{i}-x", f"Size {i}") for i in range(20)]
    colours = [_value("C" * 32, "Red"), _value("c-1", "Green"), _value("c_2", "Blue")]
    pairs = [(size.temp_id, colour.temp_id) for size in sizes for colour in colours][:50]
    limits = {"price_sen": 50_000_000, "daily_initial_stock": 9999}
    variants = [_variant(f"v{i}", s, c, "w", **limits) for i, (s, c) in enumerate(pairs)]
    request = _with(
        name_en="N" * 200,
        name_zh="中" * 200,
        name_ms="M" * 200,
        description_en="d" * 5000,
        description_zh="述" * 5000,
        max_per_order=1000,
        options=[
            _option("Size", *sizes),
            _option("Colour", *colours),
            _option("O" * 100, _value("w", "V" * 100, "值" * 100), name_zh="名" * 100),
        ],
        variants=variants,
    )

    created = _create(db, admin_id, request)

    assert created.slug == "n" * 100
    assert created.max_per_order == 1000
    assert [len(option.values) for option in created.options] == [20, 3, 1]
    assert created.options[2].code == "o" * 50
    assert created.options[2].values[0].code == "v" * 50
    assert len(created.variants) == 50
    assert {v.price_sen for v in created.variants} == {50_000_000}
    assert {v.available_stock for v in created.variants} == {9999}
    assert all(len(v.sku) <= 64 for v in created.variants)
    assert len({v.sku for v in created.variants}) == 50


def test_create_accepts_lower_limits(db: Session, admin_id: int) -> None:
    """第 11 条「每件商品 0–3 个规格名」「没有规格名的商品恰有一个 SKU」「0 到 50,000,000 仙」
    「每日初始库存 0–9,999」「每单限购 1–1000」「下架不检查」：下限一端都可以，
    未上架时英文描述可以为空。"""
    request = _simple(
        "Gift Card",
        description_en="",
        is_active=False,
        max_per_order=1,
        variants=[_variant("v1", price_sen=0, daily_initial_stock=0, is_active=False)],
    )

    created = _create(db, admin_id, request)

    assert created.options == []
    assert created.description_en is None
    assert created.max_per_order == 1
    assert created.variants == [
        AdminProductVariant(
            id=11,
            sku="gift-card-std",
            price_sen=0,
            daily_initial_stock=0,
            available_stock=0,
            is_active=False,
            option_value_ids=[],
        )
    ]


INVALID = [
    pytest.param(_with(name_en="x" * 201), id="name-201"),
    pytest.param(_with(name_ms="x" * 201), id="name-ms-201"),
    pytest.param(_with(description_en="x" * 5001), id="description-5001"),
    pytest.param(_with(description_zh="x" * 5001), id="description-zh-5001"),
    pytest.param(_with_option(0, name_zh="x" * 101), id="option-name-101"),
    pytest.param(_with_value(1, 0, name_en="x" * 101), id="value-name-101"),
    pytest.param(_with(name_zh=None), id="name-not-string"),
    pytest.param(_with_value(0, 0, name_ms=1), id="value-name-not-string"),
    pytest.param(_with(category_id=True), id="category-bool"),
    pytest.param(_with(category_id="1"), id="category-string"),
    pytest.param(_with(max_per_order=0), id="max-per-order-0"),
    pytest.param(_with(max_per_order=1001), id="max-per-order-1001"),
    pytest.param(_with(max_per_order=True), id="max-per-order-bool"),
    pytest.param(_with(max_per_order=10.0), id="max-per-order-float"),
    pytest.param(_with_variant(0, price_sen=-1), id="price-negative"),
    pytest.param(_with_variant(0, price_sen=50_000_001), id="price-above-limit"),
    pytest.param(_with_variant(0, price_sen=True), id="price-bool"),
    pytest.param(_with_variant(0, daily_initial_stock=-1), id="stock-negative"),
    pytest.param(_with_variant(0, daily_initial_stock=10_000), id="stock-10000"),
    pytest.param(_with_variant(0, daily_initial_stock=False), id="stock-bool"),
    pytest.param(_with(is_active="true"), id="active-string"),
    pytest.param(_with_variant(0, is_active=1), id="variant-active-int"),
    pytest.param(_with(name_en=""), id="name-en-missing"),
    pytest.param(_with(name_en="   ", is_active=False), id="name-en-blank-inactive"),
    pytest.param(_with_option(0, name_en=""), id="option-name-en-missing"),
    pytest.param(_with_value(1, 1, name_en=" "), id="value-name-en-missing"),
    pytest.param(
        _with(
            options=[
                *BASE.options,
                _option("Material", _value("linen", "Linen")),
                _option("Fit", _value("slim", "Slim")),
            ],
            variants=[_variant("v1", "black", "s", "linen", "slim")],
        ),
        id="4-options",
    ),
    pytest.param(_with_option(1, values=[]), id="option-0-values"),
    pytest.param(_grid(1, 21, 21), id="option-21-values"),
    pytest.param(_with(variants=[]), id="0-variants"),
    pytest.param(_grid(3, 20, 51), id="51-variants"),
    pytest.param(
        _with(options=[], variants=[_variant("v1"), _variant("v2")]),
        id="no-options-two-variants",
    ),
    pytest.param(_with_variant(0, option_values=_refs("black")), id="variant-missing-value"),
    pytest.param(
        _with_variant(0, option_values=_refs("black", "s", "m")), id="variant-extra-value"
    ),
    pytest.param(_with_variant(0, option_values=None), id="new-variant-without-values"),
    pytest.param(_with_variant(2, option_values=_refs("black", "s")), id="duplicate-combination"),
    pytest.param(_with_option(1, name_en=" COLOUR "), id="duplicate-option-name"),
    pytest.param(_with_value(0, 1, name_en="black "), id="duplicate-value-name"),
    pytest.param(_with_value(1, 0, temp_id="black"), id="duplicate-value-temp-id"),
    pytest.param(_with_variant(1, temp_id="v1"), id="duplicate-variant-temp-id"),
    pytest.param(_with_value(0, 0, temp_id=""), id="temp-id-empty"),
    pytest.param(_with_value(0, 0, temp_id="has space"), id="temp-id-space"),
    pytest.param(_with_value(0, 0, temp_id="x" * 33), id="temp-id-33"),
    pytest.param(_with_value(0, 0, temp_id="ü"), id="temp-id-non-ascii"),
    pytest.param(_with_variant(0, temp_id="a.b"), id="variant-temp-id-dot"),
    pytest.param(_with_variant(0, temp_id=None), id="variant-without-temp-id"),
    pytest.param(_with_variant(0, option_values=_refs("black", "xl")), id="ref-unknown"),
    pytest.param(_with_variant(0, option_values=_refs("s", "black")), id="ref-other-option"),
    pytest.param(
        _with_variant(0, option_values=[ValueRef(id=1, temp_id="black"), *_refs("s")]),
        id="ref-id-and-temp-id",
    ),
    pytest.param(
        _with_variant(0, option_values=[ValueRef(id=None, temp_id=None), *_refs("s")]),
        id="ref-empty",
    ),
    pytest.param(
        _with_variant(0, option_values=[ValueRef(id=1, temp_id=None), *_refs("s")]),
        id="new-request-ref-id",
    ),
    pytest.param(_with_option(0, id=1), id="new-request-option-id"),
    pytest.param(_with_value(0, 0, id=1, temp_id=None), id="new-request-value-id"),
    pytest.param(_with_value(0, 0, id=1), id="new-request-value-id-and-temp-id"),
    pytest.param(
        _with_variant(0, id=1, temp_id=None, option_values=None), id="new-request-variant-id"
    ),
    pytest.param(_with_variant(0, id=1), id="new-request-variant-id-and-temp-id"),
    pytest.param(_with(description_en=" "), id="active-without-description"),
    pytest.param(
        _with(variants=[replace(v, is_active=False) for v in BASE.variants]),
        id="active-without-active-variant",
    ),
    pytest.param(_with(options=None), id="options-not-list"),
    pytest.param(_with(variants=[BASE.options[0]]), id="variant-wrong-type"),
    pytest.param(None, id="request-not-product"),
]


@pytest.mark.parametrize("request_body", INVALID)
def test_create_rejects_invalid_request_without_sql(
    db: Session, engine: Engine, admin_id: int, request_body: ProductInput
) -> None:
    """第 11 条「（一）先不碰数据库检查请求本身——结构、格式、取值范围、上限、英文名称不重复、
    临时编号的引用、新增 SKU 之间的组合不重复，以及上架条件中只看请求就能判断的部分——不通过
    即回答『取值不合格』」「新建商品与新增规格值时英文名称必填」：各上限外一端、布尔值当整数、
    缺英文名称、英文名称大小写不同的重复、临时编号重复或引用无效、新建请求带行 ID 都抛
    ProductInvalid，不发任何 SQL、不写库。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(ProductInvalid) as raised:
        _create(db, admin_id, request_body)

    assert statements == []
    assert _snapshot(db) == before
    # 只带英文说明，不回显提交的值。
    assert "x" * 33 not in str(raised.value)
    assert "COLOUR" not in str(raised.value)


def test_base_request_is_valid(db: Session, admin_id: int) -> None:
    """第 11 条「（一）先不碰数据库检查请求本身」：上面各不合格的用例都只在基准请求上
    改一处，基准请求本身可以新建。"""
    assert _create(db, admin_id, BASE).slug == "linen-shirt"


@pytest.mark.parametrize(
    ("category_id", "is_active"),
    [(3, True), (3, False), (4, True), (4, False), (999, True)],
)
def test_create_rejects_unusable_category(
    db: Session, admin_id: int, category_id: int, is_active: bool
) -> None:
    """第 11 条「改选时从已有、启用且有英文名称的分类中选，与前台分类列表相同」：停用的分类、
    缺英文名称的分类与不存在的分类都抛 ProductInvalid，未上架的新商品也一样，不写库。"""
    before = _snapshot(db)

    with pytest.raises(ProductInvalid):
        _create(db, admin_id, _with(category_id=category_id, is_active=is_active))

    assert _snapshot(db) == before


def test_create_locks_category_shared_after_rollback(
    db: Session, engine: Engine, admin_id: int
) -> None:
    """第 11 条「读分类时加共享锁（与第 14 条保存分类时锁分类行互相等待）」「新建商品没有第
    （二）步的版本与行 ID 核对，只锁新行」：回滚此前的事务后，第一条语句是以共享锁
    读所选分类，之后不再加锁。"""
    load_admin_products(db)
    seen = _record_sequence(engine)

    _create(db, admin_id, BASE)

    assert seen[0] == "ROLLBACK"
    lock = seen[1]
    assert "FROM categories" in lock
    assert "SHARE" in lock
    assert [sql for sql in seen if "SHARE" in sql or "FOR UPDATE" in sql] == [lock]


# ---- 代码 ----


def test_option_and_value_codes_reuse_existing(db: Session, admin_id: int) -> None:
    """第 11 条「规格名代码先沿用：全站已有规格名的英文名称与之相同时，取其中 ID 最小者的代码；
    规格值代码同样先沿用……（示例数据的 Colour 为 color、1 m 为 1m，新商品沿用后才与它们合为
    一组）」：大小写与首尾空白不同也沿用，没有可沿用的才按基本写法。"""
    request = _with(
        options=[
            _option(" colour ", _value("b", "BLACK"), _value("o", "Olive Green")),
            _option("SIZE", _value("1m", "1 m"), _value("3m", "3 m")),
        ],
        variants=[_variant("v1", "b", "1m"), _variant("v2", "o", "3m")],
    )

    created = _create(db, admin_id, request)

    assert _codes(created) == (
        ["color", "size"],
        [["black", "olive-green"], ["1m", "3-m"]],
        ["linen-shirt-black-1m", "linen-shirt-olive-green-3-m"],
    )


def test_value_codes_reuse_only_under_same_option_code(db: Session, admin_id: int) -> None:
    """第 11 条「规格值代码同样先沿用全站同一规格名代码之下英文名称相同的规格值的代码」：
    规格名 Shade 不沿用 color，它下面的 1 m 不沿用尺寸下的 1m。"""
    request = _with(
        name_en="Paint",
        options=[_option("Shade", _value("x", "1 m"))],
        variants=[_variant("v1", "x")],
    )

    created = _create(db, admin_id, request)

    assert _codes(created) == (["shade"], [["1-m"]], ["paint-1-m"])


def test_same_product_color_then_colour(db: Session, admin_id: int) -> None:
    """第 11 条「唯一范围为……规格名代码在该商品内」「去重时依次试后缀（无、-2、-3……）」；
    HANDOFF 0.77「沿用的代码同样经 unique_code 去重（同一商品先有 Color、后有 Colour
    时两者都会得到 color）」：先 Color 后 Colour 得 color 与 color-2。"""
    request = _with(
        options=[
            _option("Color", _value("a", "Black")),
            _option("Colour", _value("b", "Black")),
        ],
        variants=[_variant("v1", "a", "b")],
    )

    created = _create(db, admin_id, request)

    assert _codes(created) == (
        ["color", "color-2"],
        [["black"], ["black"]],
        ["linen-shirt-black-black"],
    )


def test_value_codes_unique_within_option(db: Session, admin_id: int) -> None:
    """第 11 条「唯一范围为……规格值代码在该规格名内」：英文名称不同而基本写法相同的两个值
    得 red 与 red-2。"""
    request = _with(
        options=[_option("Colour", _value("a", "Red!"), _value("b", "Red?"))],
        variants=[_variant("v1", "a"), _variant("v2", "b")],
    )

    created = _create(db, admin_id, request)

    assert _codes(created)[1] == [["red", "red-2"]]


def test_no_option_sku_is_slug_std(db: Session, admin_id: int) -> None:
    """第 11 条「没有规格名的商品为 slug 接 -std（与示例数据一致）」。"""
    created = _create(db, admin_id, _simple("Travel Mug"))

    assert _codes(created) == ([], [], ["travel-mug-std"])


def test_slug_and_sku_deduplicate_site_wide(db: Session, admin_id: int) -> None:
    """第 11 条「唯一范围为 slug 与 SKU 全站」「去重时依次试后缀（无、-2、-3……）」：与示例
    数据的 slug 相同时加 -2；SKU 与示例数据的 crew-neck-tee-black-m 相同时加 -2。"""
    socks = _create(db, admin_id, _simple("Ankle Socks, 3 Pack"))
    tee = _create(
        db,
        admin_id,
        _with(
            name_en="Crew Neck Tee Black",
            options=[_option("Size", _value("m", "M"))],
            variants=[_variant("v1", "m")],
        ),
    )

    assert socks.slug == "ankle-socks-3-pack-2"
    assert socks.variants[0].sku == "ankle-socks-3-pack-2-std"
    assert tee.slug == "crew-neck-tee-black"
    assert tee.variants[0].sku == "crew-neck-tee-black-m-2"


def test_skus_deduplicate_within_one_save(db: Session, admin_id: int) -> None:
    """第 11 条「长度上限为……SKU 64 个字符」「直到与已有的及同一次保存里先生成的都不重复；
    同一次保存里按请求顺序生成」：截到 64 个字符后相同的两个 SKU，后一个加 -2。"""
    request = _with(
        name_en="x" * 80,
        options=[_option("Size", _value("s", "S"), _value("m", "M"))],
        variants=[_variant("v1", "s"), _variant("v2", "m")],
    )

    created = _create(db, admin_id, request)

    assert created.slug == "x" * 80
    assert _codes(created)[2] == ["x" * 64, "x" * 62 + "-2"]


def test_code_conflict_is_changed(
    db: Session, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 11 条「生成的代码撞上唯一约束（只在两个后台会话同时保存时发生）时整次保存不生效，
    回答『商品已被改动』」：撞上即回滚、抛 ProductChanged，不写库。"""
    monkeypatch.setattr(admin_products, "generate_slug", lambda *_args: "crew-neck-tee")
    before = _snapshot(db)

    with pytest.raises(ProductChanged):
        _create(db, admin_id, BASE)

    assert _snapshot(db) == before


def test_create_rolls_back_on_audit_error(db: Session, admin_id: int) -> None:
    """第 11 条「新建商品写一条（新值为 slug）……」「每步不通过都整次保存不生效」：审计被拒
    （时间带时区）时整个事务回滚，不留下新商品与它的规格、SKU。"""
    before = _snapshot(db)

    with pytest.raises(ValueError):
        create_product(db, admin_id, BASE, NOW.replace(tzinfo=UTC))

    assert _snapshot(db) == before


# ---- 生效 ----


def test_new_products_on_front_catalog_only_when_active(db: Session, admin_id: int) -> None:
    """第 11 条「保存时须满足前台的发布条件……不存成『上架但前台看不见』」「保存提交后，之后的
    计价与下单即按新值（不缓存）」：新建的已上架商品出现在前台商品列表，未上架的不出现。"""
    before = _front_products(db)

    shown = _create(db, admin_id, BASE)
    hidden = _create(db, admin_id, _with(name_en="Linen Trousers", is_active=False))

    assert _front_products(db) == sorted([*before, shown.slug])
    assert hidden.slug not in _front_products(db)
    detail = list_products(db, lang="en", slugs=[shown.slug]).items[0]
    assert detail.min_price_sen == 2500
    assert detail.has_multiple_variants is True

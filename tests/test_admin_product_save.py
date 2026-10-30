"""保存已有商品（app/services/admin_products.py 的 save_product，SHOP-TASK-184）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（下称「第 11 条」）
与 docs/HANDOFF.md 0.77。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool），分类直接写入，商品经 create_product 造：
圆领 T 恤（商品 1；规格名 1 Colour、2 Size；规格值 1 Black、2 White、3 S、4 M；
SKU 1 black-s、2 black-m、3 white-m）与帆布袋（商品 2；规格名 3；规格值 5、6；SKU 4、5）。
结果以只查列值的 SELECT 读出。SQLite 不生成行锁与共享锁：锁定查询另用 MySQL 方言编译检查。
本任务只保存已有行；请求带新增项时的回答由 SHOP-TASK-185 改，这里不断言。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select, update
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
from app.services.admin_products import (
    AdminProduct,
    OptionInput,
    ProductChanged,
    ProductInput,
    ProductInvalid,
    ValueInput,
    ValueRef,
    VariantInput,
    create_product,
    load_admin_product,
    lock_product_statement,
    lock_variants_statement,
    save_product,
)
from app.services.catalog import list_products
from app.services.checkout import CartItem, UnavailableLine, quote_cart

NOW = datetime(2026, 10, 31, 3, 0)
TEE_ID = 1
TOTE_ID = 2

# (slug, 英文, 中文, 马来文, 启用)；写入顺序即 id 顺序。分类 4 启用但没有英文名称。
CATEGORIES = (
    ("apparel", "Apparel", "服饰", "Pakaian", True),
    ("bags", "Bags", "包袋", "Beg", True),
    ("archive", "Archive", "归档", "Arkib", False),
    ("misc", None, "杂项", None, True),
)

Edit = Callable[[ProductInput], ProductInput]


# ---- 新建请求（经 create_product 造数据） ----


def _new_value(temp_id: str, name_en: str) -> ValueInput:
    return ValueInput(id=None, temp_id=temp_id, name_en=name_en, name_zh="", name_ms="")


def _new_option(name_en: str, *values: ValueInput) -> OptionInput:
    return OptionInput(id=None, name_en=name_en, name_zh="", name_ms="", values=list(values))


def _new_variant(temp_id: str, *value_temp_ids: str) -> VariantInput:
    return VariantInput(
        id=None,
        temp_id=temp_id,
        option_values=[ValueRef(id=None, temp_id=value) for value in value_temp_ids],
        price_sen=2500,
        daily_initial_stock=10,
        is_active=True,
    )


TEE = ProductInput(
    category_id=1,
    name_en="Crew Neck Tee",
    name_zh="圆领 T 恤",
    name_ms="",
    description_en="A soft cotton tee.",
    description_zh="",
    description_ms="",
    is_active=True,
    max_per_order=10,
    options=[
        _new_option("Colour", _new_value("black", "Black"), _new_value("white", "White")),
        _new_option("Size", _new_value("s", "S"), _new_value("m", "M")),
    ],
    variants=[
        _new_variant("v1", "black", "s"),
        _new_variant("v2", "black", "m"),
        _new_variant("v3", "white", "m"),
    ],
)

TOTE = replace(
    TEE,
    category_id=2,
    name_en="Canvas Tote",
    name_zh="",
    description_en="A canvas tote.",
    options=[_new_option("Colour", _new_value("b", "Black"), _new_value("n", "Natural"))],
    variants=[_new_variant("v1", "b"), _new_variant("v2", "n")],
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        for slug, en, zh, ms, active in CATEGORIES:
            category = Category(
                slug=slug,
                name_en=en,
                name_zh=zh,
                name_ms=ms,
                is_active=active,
                version=1,
            )
            seed.add(category)
            seed.flush()
        account = AdminAccount(
            username="admin@example.test", password_hash="x" * 60, password_updated_at=NOW
        )
        seed.add(account)
        seed.commit()
        account_id = account.id
        create_product(seed, account_id, TEE, NOW)
        create_product(seed, account_id, TOTE, NOW)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin_id(db: Session) -> int:
    account_id = db.scalar(select(AdminAccount.id))
    assert account_id is not None
    return account_id


# ---- 保存请求 ----


def _as_input(product: AdminProduct) -> ProductInput:
    """读取的结果原样改写成保存请求：已有行只带 id，已有 SKU 不带组合，空文案为空串。"""
    return ProductInput(
        category_id=product.category_id,
        name_en=product.name_en or "",
        name_zh=product.name_zh or "",
        name_ms=product.name_ms or "",
        description_en=product.description_en or "",
        description_zh=product.description_zh or "",
        description_ms=product.description_ms or "",
        is_active=product.is_active,
        max_per_order=product.max_per_order,
        options=[
            OptionInput(
                id=option.id,
                name_en=option.name_en or "",
                name_zh=option.name_zh or "",
                name_ms=option.name_ms or "",
                values=[
                    ValueInput(
                        id=value.id,
                        temp_id=None,
                        name_en=value.name_en or "",
                        name_zh=value.name_zh or "",
                        name_ms=value.name_ms or "",
                    )
                    for value in option.values
                ],
            )
            for option in product.options
        ],
        variants=[
            VariantInput(
                id=variant.id,
                temp_id=None,
                option_values=None,
                price_sen=variant.price_sen,
                daily_initial_stock=variant.daily_initial_stock,
                is_active=variant.is_active,
            )
            for variant in product.variants
        ],
    )


def _loaded(db: Session, product_id: int = TEE_ID) -> AdminProduct:
    product = load_admin_product(db, product_id)
    assert product is not None
    return product


def _current(db: Session, product_id: int = TEE_ID) -> ProductInput:
    return _as_input(_loaded(db, product_id))


def _edit_option(request: ProductInput, index: int, **fields: object) -> ProductInput:
    options = list(request.options)
    options[index] = replace(options[index], **fields)
    return replace(request, options=options)


def _edit_value(
    request: ProductInput, option_index: int, value_index: int, **fields: object
) -> ProductInput:
    values = list(request.options[option_index].values)
    values[value_index] = replace(values[value_index], **fields)
    return _edit_option(request, option_index, values=values)


def _edit_variant(request: ProductInput, index: int, **fields: object) -> ProductInput:
    variants = list(request.variants)
    variants[index] = replace(variants[index], **fields)
    return replace(request, variants=variants)


def _save(
    db: Session,
    admin_id: int,
    request: ProductInput,
    version: object = 1,
    product_id: int = TEE_ID,
) -> AdminProduct:
    return save_product(db, admin_id, product_id, version, request, NOW)  # type: ignore[arg-type]


# ---- 辅助 ----


def _table(db: Session, model: type) -> list[tuple]:
    table = model.__table__
    stmt = select(table).order_by(*table.primary_key)
    return [tuple(row) for row in db.execute(stmt)]


def _audits(db: Session) -> list[tuple]:
    columns = (
        AuditEvent.action,
        AuditEvent.target_type,
        AuditEvent.target_id,
        AuditEvent.old_value,
        AuditEvent.new_value,
    )
    stmt = select(*columns).order_by(AuditEvent.id)
    return [tuple(row) for row in db.execute(stmt)]


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


def _codes(product: AdminProduct) -> tuple[str, list[str], list[list[str]], list[str]]:
    return (
        product.slug,
        [option.code for option in product.options],
        [[value.code for value in option.values] for option in product.options],
        [variant.sku for variant in product.variants],
    )


def _quote(db: Session, *skus: str) -> list:
    items = [CartItem(sku=sku, quantity=1) for sku in skus]
    return quote_cart(db, items, lang="en").lines


def _front_products(db: Session) -> list[str]:
    return sorted(item.slug for item in list_products(db, lang="en").items)


# ---- 改名与生效 ----


def test_rename_keeps_slug_and_codes(db: Session, admin_id: int) -> None:
    """第 11 条「商品的 slug（商品页地址的一段）、规格名与规格值的代码、SKU 代码都由服务端在
    新建时生成，之后改名称也不变（商品页地址、购物车与订单快照里的 SKU 不因改名失效）」：
    改商品、规格名与规格值的英文名称后，slug、代码与 SKU 都不变，名称为新值；
    返回值即读取的结果。"""
    before = _loaded(db)
    request = replace(_current(db), name_en="  Classic Tee ")
    request = _edit_option(request, 0, name_en="Color")
    request = _edit_value(request, 0, 0, name_en="Jet Black")

    saved = _save(db, admin_id, request)

    assert saved == _loaded(db)
    assert _codes(saved) == _codes(before)
    assert _codes(saved) == (
        "crew-neck-tee",
        ["colour", "size"],
        [["black", "white"], ["s", "m"]],
        ["crew-neck-tee-black-s", "crew-neck-tee-black-m", "crew-neck-tee-white-m"],
    )
    assert saved.name_en == "Classic Tee"
    assert saved.options[0].name_en == "Color"
    assert saved.options[0].values[0].name_en == "Jet Black"
    assert saved.version == 2
    # 购物车里的 SKU 照旧可计价。
    assert _quote(db, "crew-neck-tee-black-s")[0].status == "ok"


def test_price_change_applies_to_later_quotes(db: Session, admin_id: int) -> None:
    """第 11 条「保存提交后，之后的计价与下单即按新值（不缓存）；价格与限购按第 7、8 条只影响
    新订单」：改价格后 quote_cart 取新价，没改的 SKU 照旧。"""
    assert [line.unit_price_sen for line in _quote(db, "crew-neck-tee-black-s")] == [2500]

    _save(db, admin_id, _edit_variant(_current(db), 0, price_sen=3100))

    lines = _quote(db, "crew-neck-tee-black-s", "crew-neck-tee-black-m")
    assert [line.unit_price_sen for line in lines] == [3100, 2500]


def test_deactivated_product_leaves_front_catalog(db: Session, admin_id: int) -> None:
    """第 11 条「不再卖的 SKU 停用，不再卖的商品下架」「访客购物车里的 SKU 被停用或商品下架后，
    计价按现有规则把该行标为不可购买」：下架后前台商品列表不再出现，
    计价把它的行标为不可购买。"""
    assert "crew-neck-tee" in _front_products(db)

    _save(db, admin_id, replace(_current(db), is_active=False))

    assert _front_products(db) == ["canvas-tote"]
    assert isinstance(_quote(db, "crew-neck-tee-black-s")[0], UnavailableLine)


def test_deactivated_variant_is_unavailable(db: Session, admin_id: int) -> None:
    """第 11 条「不再卖的 SKU 停用」「访客购物车里的 SKU 被停用或商品下架后，计价按现有规则把
    该行标为不可购买」：停用一个 SKU 后计价把该行标为不可购买，同一商品别的 SKU 照常。"""
    _save(db, admin_id, _edit_variant(_current(db), 0, is_active=False))

    lines = _quote(db, "crew-neck-tee-black-s", "crew-neck-tee-black-m")
    assert [line.status for line in lines] == ["unavailable", "ok"]
    assert "crew-neck-tee" in _front_products(db)


def test_daily_stock_change_keeps_available_stock(db: Session, admin_id: int) -> None:
    """第 11 条「已有 SKU 改每日初始库存按第 6 条从下一次每日重置起生效」：改每日初始库存不改
    当日可用库存（已被预留掉一部分的当日库存保持不变）。"""
    db.execute(update(ProductVariant).where(ProductVariant.id == 1).values(available_stock=3))
    db.commit()

    saved = _save(db, admin_id, _edit_variant(_current(db), 0, daily_initial_stock=50))

    first = saved.variants[0]
    assert (first.daily_initial_stock, first.available_stock) == (50, 3)
    assert [variant.available_stock for variant in saved.variants[1:]] == [10, 10]


# ---- 版本与审计 ----


def _same(request: ProductInput) -> ProductInput:
    return request


def _whitespace_only(request: ProductInput) -> ProductInput:
    return replace(request, name_en="  Crew Neck Tee  ", name_ms="   ")


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(_same, id="same"),
        pytest.param(_whitespace_only, id="whitespace-only"),
    ],
)
def test_no_change_keeps_version_and_writes_no_audit(
    db: Session, admin_id: int, edit: Edit
) -> None:
    """第 11 条「核对通过后有任何改动即版本号加一；没有改动不加版本、不写审计」「首尾空白
    去掉，空串存为空」：原样保存、或只差首尾空白时不改库，版本仍为 1，返回当前的商品。"""
    before = _snapshot(db)

    saved = _save(db, admin_id, edit(_current(db)))

    assert saved == _loaded(db)
    assert saved.version == 1
    assert _snapshot(db) == before


def test_each_save_with_changes_increments_version_once(db: Session, admin_id: int) -> None:
    """第 11 条「核对通过后有任何改动即版本号加一」「保存请求带读取时的版本号」「核对版本号与
    当前相同……不通过即回答『商品已被改动』」：一次保存改几项都只加一；之后再带旧版本号
    保存即 ProductChanged，不写库。"""
    first = _save(db, admin_id, replace(_current(db), name_zh="圆领衫", max_per_order=3))
    second = _save(db, admin_id, _edit_variant(_current(db), 1, price_sen=2600), version=2)

    assert (first.version, second.version) == (2, 3)
    before = _snapshot(db)
    with pytest.raises(ProductChanged):
        _save(db, admin_id, replace(_current(db), name_zh="旧页面"), version=2)
    assert _snapshot(db) == before


def test_audit_fields_and_values(db: Session, admin_id: int) -> None:
    """第 11 条「每改一项写一条审计，不含个人资料；对象为商品、规格名、规格值或 SKU，以行 ID
    记录，旧值与新值写成『字段 值』（SKU 另在前面加 SKU 代码，如 crew-neck-tee-black-m
    price_sen 2500；文案的字段带语言，如 name_zh，值取前 100 个字符）」「记录的字段为分类、
    上架状态、每单限购、价格（仙）、每日初始库存、SKU 启用状态与各语言文案（上架状态与
    启用状态记 true 或 false）」；HANDOFF 0.77 的操作名与对象类别。为空的文案只写字段名。"""
    created = len(_audits(db))
    request = replace(
        _current(db),
        category_id=2,
        is_active=False,
        max_per_order=5,
        name_zh="",
        name_ms="Baju-T",
        description_en="d" * 150,
    )
    request = _edit_option(request, 0, name_zh="颜色")
    request = _edit_value(request, 1, 1, name_en="Medium")
    request = _edit_variant(request, 1, price_sen=0)
    request = _edit_variant(request, 2, daily_initial_stock=40, is_active=False)

    _save(db, admin_id, request)

    sku_m = "crew-neck-tee-black-m"
    sku_w = "crew-neck-tee-white-m"
    product = ("admin_product_changed", "product", 1)
    option = ("admin_product_option_changed", "product_option", 1)
    value = ("admin_product_option_value_changed", "product_option_value", 4)
    variant_m = ("admin_product_variant_changed", "product_variant", 2)
    variant_w = ("admin_product_variant_changed", "product_variant", 3)
    assert _audits(db)[created:] == [
        (*product, "category_id 1", "category_id 2"),
        (*product, "is_active true", "is_active false"),
        (*product, "max_per_order 10", "max_per_order 5"),
        (*product, "name_zh 圆领 T 恤", "name_zh"),
        (*product, "name_ms", "name_ms Baju-T"),
        (*product, "description_en A soft cotton tee.", "description_en " + "d" * 100),
        (*option, "name_zh", "name_zh 颜色"),
        (*value, "name_en M", "name_en Medium"),
        (*variant_m, f"{sku_m} price_sen 2500", f"{sku_m} price_sen 0"),
        (*variant_w, f"{sku_w} daily_initial_stock 10", f"{sku_w} daily_initial_stock 40"),
        (*variant_w, f"{sku_w} is_active true", f"{sku_w} is_active false"),
    ]
    accounts = db.scalars(select(AuditEvent.admin_account_id)).all()
    assert set(accounts) == {admin_id}
    saved = _loaded(db)
    assert saved.version == 2
    assert saved.description_en == "d" * 150


def test_write_error_rolls_back(db: Session, admin_id: int) -> None:
    """第 11 条「服务端分三步，每步不通过都整次保存不生效」「每改一项写一条审计」：写审计时
    出错（时间带时区）整个事务回滚，新值与版本号都不留下。"""
    request = replace(_current(db), name_zh="x")
    before = _snapshot(db)

    with pytest.raises(ValueError):
        save_product(db, admin_id, TEE_ID, 1, request, NOW.replace(tzinfo=UTC))

    assert _snapshot(db) == before


# ---- 第一步 ----


def _all_variants_inactive(request: ProductInput) -> ProductInput:
    return replace(request, variants=[replace(v, is_active=False) for v in request.variants])


INVALID = [
    pytest.param(_same, True, id="version-bool"),
    pytest.param(_same, "1", id="version-string"),
    pytest.param(_same, None, id="version-none"),
    pytest.param(_same, 1.0, id="version-float"),
    pytest.param(lambda r: _edit_option(r, 0, id=None), 1, id="option-without-id"),
    pytest.param(lambda r: _edit_option(r, 1, id=1), 1, id="duplicate-option-id"),
    pytest.param(lambda r: _edit_value(r, 1, 0, id=1), 1, id="duplicate-value-id"),
    pytest.param(lambda r: _edit_variant(r, 1, id=1), 1, id="duplicate-variant-id"),
    pytest.param(lambda r: _edit_variant(r, 0, id="1"), 1, id="variant-id-string"),
    pytest.param(lambda r: _edit_variant(r, 0, option_values=[]), 1, id="variant-combination"),
    pytest.param(lambda r: _edit_variant(r, 0, price_sen=-1), 1, id="price-negative"),
    pytest.param(lambda r: replace(r, max_per_order=1001), 1, id="max-per-order-1001"),
    pytest.param(lambda r: replace(r, name_zh="x" * 201), 1, id="name-201"),
    pytest.param(lambda r: _edit_value(r, 0, 1, name_en="black"), 1, id="duplicate-value-name"),
    pytest.param(lambda r: replace(r, description_en=" "), 1, id="active-without-description"),
    pytest.param(lambda r: _edit_value(r, 1, 0, name_en=""), 1, id="active-value-without-name"),
    pytest.param(_all_variants_inactive, 1, id="active-without-active-variant"),
]


@pytest.mark.parametrize(("edit", "version"), INVALID)
def test_invalid_request_rejected_without_sql(
    db: Session, engine: Engine, admin_id: int, edit: Edit, version: object
) -> None:
    """第 11 条「（一）先不碰数据库检查请求本身——结构、格式、取值范围、上限、英文名称不重复、
    临时编号的引用、新增 SKU 之间的组合不重复，以及上架条件中只看请求就能判断的部分（请求带有
    全部文案与 SKU 启用状态）——不通过即回答『取值不合格』」「规格名只在新建商品时给出」
    「已有 SKU 的规格值组合不能改，保存请求里已有 SKU 不带组合」：版本号不是整数、规格名不带
    id、同一类行 ID 重复等都抛 ProductInvalid，不发任何 SQL、不写库。"""
    request = edit(_current(db))
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request, version=version)

    assert statements == []
    assert _snapshot(db) == before


# ---- 第二步 ----

# 帆布袋的行：SKU 4、规格名 3、规格值 5。
OTHER_VARIANT = VariantInput(
    id=4,
    temp_id=None,
    option_values=None,
    price_sen=2500,
    daily_initial_stock=10,
    is_active=True,
)
OTHER_VALUE = ValueInput(id=5, temp_id=None, name_en="Navy", name_zh="", name_ms="")
OTHER_OPTION = OptionInput(id=3, name_en="Material", name_zh="", name_ms="", values=[OTHER_VALUE])


def _drop_variant(request: ProductInput) -> ProductInput:
    return replace(request, variants=list(request.variants[:-1]))


def _add_variant(request: ProductInput) -> ProductInput:
    return replace(request, variants=[*request.variants, OTHER_VARIANT])


def _drop_white(request: ProductInput) -> ProductInput:
    return _edit_option(request, 0, values=list(request.options[0].values[:1]))


def _add_value(request: ProductInput) -> ProductInput:
    return _edit_option(request, 0, values=[*request.options[0].values, OTHER_VALUE])


def _drop_option(request: ProductInput) -> ProductInput:
    return replace(request, options=list(request.options[:1]))


def _add_option(request: ProductInput) -> ProductInput:
    return replace(request, options=[*request.options, OTHER_OPTION])


def _white_under_size(request: ProductInput) -> ProductInput:
    white = request.options[0].values[1]
    request = _drop_white(request)
    return _edit_option(request, 1, values=[*request.options[1].values, white])


CHANGED = [
    pytest.param(_same, 2, TEE_ID, id="newer-version"),
    pytest.param(_same, 0, TEE_ID, id="older-version"),
    pytest.param(_drop_variant, 1, TEE_ID, id="missing-variant"),
    pytest.param(_add_variant, 1, TEE_ID, id="extra-variant"),
    pytest.param(_drop_white, 1, TEE_ID, id="missing-value"),
    pytest.param(_add_value, 1, TEE_ID, id="extra-value"),
    pytest.param(_drop_option, 1, TEE_ID, id="missing-option"),
    pytest.param(_add_option, 1, TEE_ID, id="extra-option"),
    pytest.param(_white_under_size, 1, TEE_ID, id="value-under-other-option"),
    pytest.param(_same, 1, TOTE_ID, id="rows-of-other-product"),
    pytest.param(_same, 1, 999, id="unknown-product"),
    pytest.param(_same, 1, 2**31, id="product-id-beyond-int"),
]


@pytest.mark.parametrize(("edit", "version", "product_id"), CHANGED)
def test_stale_or_mismatched_rows_are_changed(
    db: Session, admin_id: int, edit: Edit, version: int, product_id: int
) -> None:
    """第 11 条「保存请求带读取时的版本号，以行 ID 列出该商品现有的全部规格名、规格值与 SKU，
    一个不漏」「（二）……核对版本号与当前相同、请求里的行 ID 恰好是该商品现有的全部行……不通过
    即回答『商品已被改动』，页面提示重新读取，不自动合并」「不能删除商品、规格名、规格值与
    SKU」「规格名只在新建商品时给出，之后不能增减」：版本号不符、漏列或多列行 ID、规格值
    挂错规格名、商品不存在都抛 ProductChanged，不写库。"""
    request = edit(_current(db))
    before = _snapshot(db)

    with pytest.raises(ProductChanged):
        _save(db, admin_id, request, version=version, product_id=product_id)

    assert _snapshot(db) == before


# ---- 第三步 ----


@pytest.mark.parametrize(
    ("category_id", "is_active"),
    [(3, True), (3, False), (4, True), (4, False), (999, True), (999, False)],
)
def test_change_to_unusable_category_rejected(
    db: Session, admin_id: int, category_id: int, is_active: bool
) -> None:
    """第 11 条「（三）……分类与当前不同时，新分类须存在、启用且有英文名称」「改选时从已有、
    启用且有英文名称的分类中选」：改到停用、缺英文名称或不存在的分类都抛 ProductInvalid，
    未上架也一样，不写库。"""
    request = replace(_current(db), category_id=category_id, is_active=is_active)
    before = _snapshot(db)

    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)

    assert _snapshot(db) == before


def test_change_to_usable_category(db: Session, admin_id: int) -> None:
    """第 11 条「可改分类（改选时从已有、启用且有英文名称的分类中选……）」：改到启用且有
    英文名称的分类即保存。"""
    assert _save(db, admin_id, replace(_current(db), category_id=2)).category_id == 2


@pytest.mark.parametrize(("field", "value"), [("is_active", False), ("name_en", None)])
def test_unusable_current_category_kept_but_not_published(
    db: Session, admin_id: int, field: str, value: object
) -> None:
    """第 11 条「读取商品时返回它当前所属的分类，停用或缺英文名称的也返回，A04 显示它并允许
    保留不改」「分类不变时不检查」「保存为上架时，所属分类须启用且有英文名称」
    「不满足即整次保存不生效、回答『取值不合格』，不存成『上架但前台看不见』」：
    当前分类停用或缺英文名称后，保存为上架被拒、不写库；下架保存可以保留它。"""
    db.execute(update(Category).where(Category.id == 1).values({field: value}))
    db.commit()
    request = replace(_current(db), name_zh="圆领衫")
    before = _snapshot(db)

    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)
    assert _snapshot(db) == before

    saved = _save(db, admin_id, replace(request, is_active=False))
    assert (saved.category_id, saved.is_active, saved.name_zh) == (1, False, "圆领衫")


# ---- 加锁 ----


def test_lock_statements_compile_for_update() -> None:
    """第 11 条「在一个事务里先锁商品行，再按 ID 从小到大锁该商品的 SKU 行（与下单扣减、每日
    重置的加锁顺序一致）」：两条锁定查询按 MySQL 方言编译带 FOR UPDATE，SKU 按 id 排序，
    都以 populate_existing 重新载入会话里的旧值。"""
    product_sql = str(lock_product_statement(TEE_ID).compile(dialect=mysql.dialect()))
    variant_sql = str(lock_variants_statement(TEE_ID).compile(dialect=mysql.dialect()))

    assert "FROM products" in product_sql
    assert product_sql.rstrip().endswith("FOR UPDATE")
    assert "FROM product_variants" in variant_sql
    assert variant_sql.rstrip().endswith("ORDER BY product_variants.id FOR UPDATE")
    for stmt in (lock_product_statement(TEE_ID), lock_variants_statement(TEE_ID)):
        assert stmt.get_execution_options()["populate_existing"] is True


@pytest.mark.parametrize(
    ("fields", "shared"),
    [
        pytest.param({"category_id": 2}, True, id="category-changed"),
        pytest.param({"name_zh": "圆领衫"}, True, id="same-category-active"),
        pytest.param({"is_active": False}, False, id="same-category-inactive"),
    ],
)
def test_save_lock_order(
    db: Session, engine: Engine, admin_id: int, fields: dict, shared: bool
) -> None:
    """第 11 条「在一个事务里先锁商品行，再按 ID 从小到大锁该商品的 SKU 行」「读分类时加共享锁
    （与第 14 条保存分类时锁分类行互相等待）」「分类不变时不检查」；HANDOFF 0.77「保存的加锁
    顺序为商品行、该商品的 SKU 行（按 id）、分类行的共享锁」：回滚此前的事务后，第一条语句锁
    商品行、第二条锁 SKU 行；改分类或保存为上架时之后以共享锁读分类，分类不变且下架时不读。"""
    request = replace(_current(db), **fields)
    seen = _record_sequence(engine)

    _save(db, admin_id, request)

    assert seen[0] == "ROLLBACK"
    assert "FROM products" in seen[1]
    assert seen[1].rstrip().endswith("FOR UPDATE")
    assert "FROM product_variants" in seen[2]
    assert seen[2].rstrip().endswith("ORDER BY product_variants.id FOR UPDATE")
    locks = [sql for sql in seen if "FOR UPDATE" in sql or "SHARE" in sql]
    assert locks[:2] == seen[1:3]
    shares = locks[2:]
    if shared:
        assert len(shares) == 1
        assert "FROM categories" in shares[0]
        assert "SHARE" in shares[0]
    else:
        assert shares == []

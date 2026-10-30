"""保存商品时新增规格值与 SKU（app/services/admin_products.py 的 save_product，SHOP-TASK-185）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条（下称「第 11 条」）
与 docs/HANDOFF.md 0.77。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool），分类直接写入，商品经 create_product 造：
圆领 T 恤（商品 1；规格名 1 Colour、2 Size；规格值 1 Black、2 White、3 S、4 M；
SKU 1 black-s、2 black-m、3 white-m）与帆布袋（商品 2；规格名 3；规格值 5 Black、6 Natural；
SKU 4、5）。新增的规格值从 id 7 起，新增的 SKU 从 id 6 起。结果以只查列值的 SELECT 读出。
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
from app.services import admin_products
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
    save_product,
)
from app.services.checkout import CartItem, quote_cart

NOW = datetime(2026, 10, 31, 3, 0)
TEE_ID = 1

# (slug, 英文, 中文, 马来文, 启用)；写入顺序即 id 顺序。
CATEGORIES = (
    ("apparel", "Apparel", "服饰", "Pakaian", True),
    ("bags", "Bags", "包袋", "Beg", True),
)

Edit = Callable[[ProductInput], ProductInput]


# ---- 新建请求（经 create_product 造数据） ----


def _new_value(temp_id: str, name_en: str) -> ValueInput:
    return ValueInput(id=None, temp_id=temp_id, name_en=name_en, name_zh="", name_ms="")


def _new_option(name_en: str, *values: ValueInput) -> OptionInput:
    return OptionInput(id=None, name_en=name_en, name_zh="", name_ms="", values=list(values))


def _ref(ref: int | str) -> ValueRef:
    """整数以行 ID 引用已有规格值，字符串以 temp_id 引用新增的规格值。"""
    if isinstance(ref, int):
        return ValueRef(id=ref, temp_id=None)
    return ValueRef(id=None, temp_id=ref)


def _new_variant(
    temp_id: str,
    *refs: int | str,
    price_sen: int = 2500,
    daily_initial_stock: int = 10,
    is_active: bool = True,
) -> VariantInput:
    return VariantInput(
        id=None,
        temp_id=temp_id,
        option_values=[_ref(ref) for ref in refs],
        price_sen=price_sen,
        daily_initial_stock=daily_initial_stock,
        is_active=is_active,
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


def _current(db: Session) -> ProductInput:
    return _as_input(_loaded(db))


def _add_values(request: ProductInput, option_index: int, *values: ValueInput) -> ProductInput:
    options = list(request.options)
    option = options[option_index]
    options[option_index] = replace(option, values=[*option.values, *values])
    return replace(request, options=options)


def _add_variants(request: ProductInput, *variants: VariantInput) -> ProductInput:
    return replace(request, variants=[*request.variants, *variants])


def _edit_variant(request: ProductInput, index: int, **fields: object) -> ProductInput:
    variants = list(request.variants)
    variants[index] = replace(variants[index], **fields)
    return replace(request, variants=variants)


def _save(db: Session, admin_id: int, request: ProductInput, version: object = 1) -> AdminProduct:
    return save_product(db, admin_id, TEE_ID, version, request, NOW)  # type: ignore[arg-type]


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


def _codes(product: AdminProduct) -> tuple[list[list[str]], list[str]]:
    return (
        [[value.code for value in option.values] for option in product.options],
        [variant.sku for variant in product.variants],
    )


def _value_ids(product: AdminProduct, sku: str) -> list[int]:
    return next(variant.option_value_ids for variant in product.variants if variant.sku == sku)


def _quote(db: Session, *skus: str) -> list:
    items = [CartItem(sku=sku, quantity=1) for sku in skus]
    return quote_cart(db, items, lang="en").lines


# ---- 新增与引用 ----


def test_new_value_referenced_by_temp_id(db: Session, admin_id: int) -> None:
    """第 11 条「可新增规格值与 SKU」「新增的规格值与 SKU 各带一个请求内的临时编号（与行 ID
    分开的字段），新增 SKU 以行 ID 引用已有规格值、以临时编号引用同一次保存里新增的规格值」：
    新增 Red 与 XL，新增 SKU 以临时编号引用两者、另一个以临时编号引用 Red 并以行 ID 引用 S；
    规格值与 SKU 都写入，组合指向新行；返回值即读取的结果。"""
    request = _add_values(_current(db), 0, _new_value("red", "Red"))
    request = _add_values(request, 1, _new_value("xl", "XL"))
    request = _add_variants(request, _new_variant("n1", "red", "xl"), _new_variant("n2", "red", 3))

    saved = _save(db, admin_id, request)

    assert saved == _loaded(db)
    assert _codes(saved) == (
        [["black", "white", "red"], ["s", "m", "xl"]],
        [
            "crew-neck-tee-black-s",
            "crew-neck-tee-black-m",
            "crew-neck-tee-white-m",
            "crew-neck-tee-red-xl",
            "crew-neck-tee-red-s",
        ],
    )
    red = saved.options[0].values[2]
    xl = saved.options[1].values[2]
    assert (red.id, xl.id, red.name_en, xl.name_en) == (7, 8, "Red", "XL")
    assert _value_ids(saved, "crew-neck-tee-red-xl") == [7, 8]
    assert _value_ids(saved, "crew-neck-tee-red-s") == [7, 3]
    assert [variant.id for variant in saved.variants] == [1, 2, 3, 6, 7]


def test_new_variant_refers_to_existing_values(db: Session, admin_id: int) -> None:
    """第 11 条「新增 SKU 以行 ID 引用已有规格值」「SKU 代码为商品 slug 依规格名的顺序接上各
    规格值的代码」：只以行 ID 引用已有的 White 与 S，新增 crew-neck-tee-white-s，已有的
    SKU 不变。"""
    before = _loaded(db)
    request = _add_variants(_current(db), _new_variant("n1", 2, 3, price_sen=1990))

    saved = _save(db, admin_id, request)

    assert saved.variants[:3] == before.variants
    added = saved.variants[3]
    assert added.sku == "crew-neck-tee-white-s"
    assert (added.id, added.price_sen, added.is_active) == (6, 1990, True)
    assert added.option_value_ids == [2, 3]


def test_new_values_follow_existing_in_request_order(db: Session, admin_id: int) -> None:
    """第 11 条「新增的规格值与 SKU 按请求里的顺序排在已有的之后」：Colour 已有值的最大
    sort_order 为 5 时，新增的 Red、Blue 依次为 6、7，读取时排在已有的之后；没有新增 SKU 时
    只加规格值。"""
    db.execute(update(ProductOptionValue).where(ProductOptionValue.id == 2).values(sort_order=5))
    db.commit()
    request = _add_values(_current(db), 0, _new_value("red", "Red"), _new_value("blue", "Blue"))

    saved = _save(db, admin_id, request)

    names = [value.name_en for value in saved.options[0].values]
    assert names == ["Black", "White", "Red", "Blue"]
    stmt = (
        select(ProductOptionValue.id, ProductOptionValue.sort_order)
        .where(ProductOptionValue.option_id == 1)
        .order_by(ProductOptionValue.id)
    )
    assert [tuple(row) for row in db.execute(stmt)] == [(1, 0), (2, 5), (7, 6), (8, 7)]
    assert len(saved.variants) == 3


def test_new_variant_stock_and_quote(db: Session, admin_id: int) -> None:
    """第 11 条「新增 SKU 的当日可用库存等于它的每日初始库存（还没有预留）」「保存提交后，之后的
    计价与下单即按新值（不缓存）」：新 SKU 的当日可用库存等于每日初始库存，已有 SKU 被预留掉的
    当日库存不变；quote_cart 按新 SKU 的价格计价。"""
    db.execute(update(ProductVariant).where(ProductVariant.id == 1).values(available_stock=3))
    db.commit()
    request = _add_variants(
        _current(db), _new_variant("n1", 2, 3, price_sen=1990, daily_initial_stock=7)
    )

    saved = _save(db, admin_id, request)

    stocks = [(v.sku, v.daily_initial_stock, v.available_stock) for v in saved.variants]
    assert stocks[0] == ("crew-neck-tee-black-s", 10, 3)
    assert stocks[3] == ("crew-neck-tee-white-s", 7, 7)
    lines = _quote(db, "crew-neck-tee-white-s")
    assert [(line.status, line.unit_price_sen) for line in lines] == [("ok", 1990)]


# ---- 代码 ----


def test_new_value_codes_reuse_and_dedupe(db: Session, admin_id: int) -> None:
    """第 11 条「规格值代码同样先沿用全站同一规格名代码之下英文名称相同的规格值的代码」「唯一
    范围为……规格值代码在该规格名内」「去重时依次试后缀（无、-2、-3……）」「同一次保存里按请求
    顺序生成」：帆布袋的 Natural 改名为 Ecru 后（代码仍为 natural），T 恤新增的 ECRU 沿用
    natural；Black! 的基本写法 black 在该规格名下已有，得 black-2；SKU 接上沿用后的代码。"""
    natural = update(ProductOptionValue).where(ProductOptionValue.id == 6)
    db.execute(natural.values(name_en="Ecru"))
    db.commit()
    request = _add_values(_current(db), 0, _new_value("e", " ECRU "), _new_value("b", "Black!"))
    request = _add_variants(request, _new_variant("n1", "e", 3), _new_variant("n2", "b", 3))

    saved = _save(db, admin_id, request)

    codes, skus = _codes(saved)
    assert codes[0] == ["black", "white", "natural", "black-2"]
    assert skus[3:] == ["crew-neck-tee-natural-s", "crew-neck-tee-black-2-s"]


def test_new_sku_dedupes_across_the_site(db: Session, admin_id: int) -> None:
    """第 11 条「唯一范围为 slug 与 SKU 全站」「去重时依次试后缀（无、-2、-3……）」：
    别的商品已有 crew-neck-tee-white-s 时，新增的 White、S 得 crew-neck-tee-white-s-2。"""
    db.execute(
        update(ProductVariant).where(ProductVariant.id == 4).values(sku="crew-neck-tee-white-s")
    )
    db.commit()

    saved = _save(db, admin_id, _add_variants(_current(db), _new_variant("n1", 2, 3)))

    assert saved.variants[3].sku == "crew-neck-tee-white-s-2"


def _code_taken_values(_db: Session, _option_code: str, names: list, _taken: list) -> list[str]:
    return ["black"] * len(names)


def _code_taken_skus(_db: Session, _slug: str, combinations: list) -> list[str]:
    return ["canvas-tote-black"] * len(combinations)


@pytest.mark.parametrize(
    ("name", "fake"),
    [
        pytest.param("generate_value_codes", _code_taken_values, id="value-code"),
        pytest.param("generate_skus", _code_taken_skus, id="sku"),
    ],
)
def test_code_collision_is_changed(
    db: Session, admin_id: int, monkeypatch: pytest.MonkeyPatch, name: str, fake: Callable
) -> None:
    """第 11 条「生成的代码撞上唯一约束（只在两个后台会话同时保存时发生）时整次保存不生效，
    回答『商品已被改动』」：生成的规格值代码或 SKU 代码与另一会话已写入的相同（这里以替换生成
    函数模拟），抛 ProductChanged，已有行的改动与新增都不留下。"""
    request = _add_values(replace(_current(db), name_zh="圆领衫"), 0, _new_value("red", "Red"))
    request = _add_variants(request, _new_variant("n1", "red", 3))
    before = _snapshot(db)
    monkeypatch.setattr(admin_products, name, fake)

    with pytest.raises(ProductChanged):
        _save(db, admin_id, request)

    assert _snapshot(db) == before


# ---- 第一步 ----


def _ref_value_of_other_option(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", 3, 3))


def _ref_unlisted_value(request: ProductInput) -> ProductInput:
    # 规格值 5 属于帆布袋，请求里没有列出。
    return _add_variants(request, _new_variant("n1", 5, 3))


def _ref_new_value_of_other_option(request: ProductInput) -> ProductInput:
    request = _add_values(request, 1, _new_value("xl", "XL"))
    return _add_variants(request, _new_variant("n1", "xl", 3))


def _ref_unknown_temp_id(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", "nope", 3))


def _ref_too_few(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", 2))


def _ref_with_both(request: ProductInput) -> ProductInput:
    variant = _new_variant("n1", 2, 3)
    refs = [ValueRef(id=2, temp_id="white"), ValueRef(id=3, temp_id=None)]
    return _add_variants(request, replace(variant, option_values=refs))


def _new_variant_without_temp_id(request: ProductInput) -> ProductInput:
    return _add_variants(request, replace(_new_variant("n1", 2, 3), temp_id=None))


def _new_value_without_name(request: ProductInput) -> ProductInput:
    # 下架保存，免得被上架条件先拦下。
    return _add_values(replace(request, is_active=False), 0, _new_value("red", "  "))


def _new_value_same_name(request: ProductInput) -> ProductInput:
    return _add_values(request, 0, _new_value("black2", " BLACK "))


def _same_new_combinations(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", 2, 3), _new_variant("n2", 2, 3))


def _same_value_temp_ids(request: ProductInput) -> ProductInput:
    request = _add_values(request, 0, _new_value("x", "Red"))
    return _add_values(request, 1, _new_value("x", "XL"))


def _same_variant_temp_ids(request: ProductInput) -> ProductInput:
    request = _add_values(request, 0, _new_value("red", "Red"))
    return _add_variants(request, _new_variant("n1", 2, 3), _new_variant("n1", "red", 3))


def _bad_temp_id(request: ProductInput) -> ProductInput:
    return _add_values(request, 0, _new_value("red value", "Red"))


def _new_variant_price_negative(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", 2, 3, price_sen=-1))


INVALID = [
    pytest.param(_ref_value_of_other_option, id="ref-value-of-other-option"),
    pytest.param(_ref_unlisted_value, id="ref-unlisted-value"),
    pytest.param(_ref_new_value_of_other_option, id="ref-new-value-of-other-option"),
    pytest.param(_ref_unknown_temp_id, id="ref-unknown-temp-id"),
    pytest.param(_ref_too_few, id="ref-too-few"),
    pytest.param(_ref_with_both, id="ref-with-id-and-temp-id"),
    pytest.param(_new_variant_without_temp_id, id="variant-without-id-or-temp-id"),
    pytest.param(_new_value_without_name, id="new-value-without-english-name"),
    pytest.param(_new_value_same_name, id="new-value-same-english-name"),
    pytest.param(_same_new_combinations, id="same-new-combinations"),
    pytest.param(_same_value_temp_ids, id="same-value-temp-ids"),
    pytest.param(_same_variant_temp_ids, id="same-variant-temp-ids"),
    pytest.param(_bad_temp_id, id="temp-id-format"),
    pytest.param(_new_variant_price_negative, id="new-variant-price-negative"),
]


@pytest.mark.parametrize("edit", INVALID)
def test_invalid_additions_rejected_without_sql(
    db: Session, engine: Engine, admin_id: int, edit: Edit
) -> None:
    """第 11 条「（一）先不碰数据库检查请求本身——结构、格式、取值范围、上限、英文名称不重复、
    临时编号的引用、新增 SKU 之间的组合不重复……——不通过即回答『取值不合格』」「每个 SKU 在
    每个规格名下恰有一个值」「所以新建商品与新增规格值时英文名称必填」「新增 SKU 以行 ID 引用
    已有规格值、以临时编号引用同一次保存里新增的规格值」；HANDOFF 0.77「新增 SKU 以行 ID 引用了
    请求里没有列出的规格值，归第一步『取值不合格』」：引用别的规格名下的值、请求里没列出的值、
    不存在的临时编号等都抛 ProductInvalid，不发任何 SQL、不写库。"""
    request = edit(_current(db))
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)

    assert statements == []
    assert _snapshot(db) == before


def _with_new_values(request: ProductInput, option_index: int, count: int) -> ProductInput:
    prefix = "c" if option_index == 0 else "z"
    values = [_new_value(f"{prefix}{i}", f"{prefix.upper()}{i}") for i in range(count)]
    return _add_values(request, option_index, *values)


@pytest.mark.parametrize(("count", "accepted"), [(18, True), (19, False)])
def test_value_limit_counts_existing_and_new(
    db: Session, engine: Engine, admin_id: int, count: int, accepted: bool
) -> None:
    """第 11 条「每个规格名 1–20 个值」：Colour 已有 2 个值，新增 18 个（共 20）可以保存；
    新增 19 个（共 21）在第一步抛 ProductInvalid，不发 SQL。"""
    request = _with_new_values(_current(db), 0, count)
    if accepted:
        assert len(_save(db, admin_id, request).options[0].values) == 20
        return
    statements = _record_statements(engine)
    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)
    assert statements == []


@pytest.mark.parametrize(("count", "accepted"), [(47, True), (48, False)])
def test_variant_limit_counts_existing_and_new(
    db: Session, engine: Engine, admin_id: int, count: int, accepted: bool
) -> None:
    """第 11 条「1–50 个 SKU」：已有 3 个 SKU，新增 47 个（共 50）可以保存；新增 48 个
    （共 51）在第一步抛 ProductInvalid，不发 SQL。"""
    request = _with_new_values(_with_new_values(_current(db), 0, 8), 1, 4)
    colours: list[int | str] = [1, 2, *(f"c{i}" for i in range(8))]
    sizes: list[int | str] = [3, 4, *(f"z{i}" for i in range(4))]
    existing = {(1, 3), (1, 4), (2, 4)}
    pairs = [(c, s) for c in colours for s in sizes if (c, s) not in existing][:count]
    variants = [_new_variant(f"n{i}", c, s) for i, (c, s) in enumerate(pairs)]
    request = _add_variants(request, *variants)

    if accepted:
        assert len(_save(db, admin_id, request).variants) == 50
        return
    statements = _record_statements(engine)
    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)
    assert statements == []


# ---- 第二步 ----


def _white_listed_under_size(request: ProductInput) -> ProductInput:
    """请求把 White（2）挪到 Size 下列出，新增 SKU 在 Size 下引用它。"""
    options = list(request.options)
    white = options[0].values[1]
    options[0] = replace(options[0], values=list(options[0].values[:1]))
    options[1] = replace(options[1], values=[*options[1].values, white])
    return _add_variants(replace(request, options=options), _new_variant("n1", 1, 2))


def _white_s(request: ProductInput) -> ProductInput:
    return _add_variants(request, _new_variant("n1", 2, 3))


@pytest.mark.parametrize(
    ("edit", "version"),
    [
        pytest.param(_white_s, 2, id="stale-version"),
        pytest.param(_white_listed_under_size, 1, id="value-of-other-option"),
    ],
)
def test_additions_with_mismatched_rows_are_changed(
    db: Session, admin_id: int, edit: Edit, version: int
) -> None:
    """第 11 条「（二）……核对版本号与当前相同、请求里的行 ID 恰好是该商品现有的全部行（新增
    SKU 引用的已有规格值须属于该 SKU 所选的规格名），不通过即回答『商品已被改动』」：带新增项
    时版本号不符、新增 SKU 引用的已有规格值实际属于别的规格名，都抛 ProductChanged，不写库。"""
    request = edit(_current(db))
    before = _snapshot(db)

    with pytest.raises(ProductChanged):
        _save(db, admin_id, request, version=version)

    assert _snapshot(db) == before


# ---- 第三步 ----


@pytest.mark.parametrize("is_active", [False, True])
def test_combination_of_existing_variant_rejected(
    db: Session, admin_id: int, is_active: bool
) -> None:
    """第 11 条「同一商品任两个 SKU（含停用的）的规格值组合不同」「（三）同一事务里检查要查库的
    取值——……新增 SKU 的组合与已有 SKU（含停用的）不重复——不通过即回答『取值不合格』」：
    新增 SKU 选 Black、S，与已有的 black-s 相同（停用的与启用的都一样），抛 ProductInvalid，
    同一请求里其他的改动与新增都不留下。"""
    db.execute(update(ProductVariant).where(ProductVariant.id == 1).values(is_active=is_active))
    db.commit()
    request = _add_values(replace(_current(db), name_zh="圆领衫"), 0, _new_value("red", "Red"))
    request = _add_variants(request, _new_variant("n1", "red", 3), _new_variant("n2", 1, 3))
    before = _snapshot(db)

    with pytest.raises(ProductInvalid):
        _save(db, admin_id, request)

    assert _snapshot(db) == before


# ---- 版本与审计 ----


def test_changes_and_additions_increment_version_once(db: Session, admin_id: int) -> None:
    """第 11 条「核对通过后有任何改动即版本号加一」：只新增 SKU 也加一；改已有行与新增合在
    一次保存里只加一；之后再带旧版本号保存即 ProductChanged。"""
    first = _save(db, admin_id, _add_variants(_current(db), _new_variant("n1", 2, 3)))
    request = _add_values(replace(_current(db), name_zh="圆领衫"), 1, _new_value("xl", "XL"))
    request = _add_variants(request, _new_variant("n2", 1, "xl"))
    second = _save(db, admin_id, request, version=2)

    assert (first.version, second.version) == (2, 3)
    with pytest.raises(ProductChanged):
        # 规格值 7 为上一次新增的 XL。
        _save(db, admin_id, _add_variants(_current(db), _new_variant("n3", 2, 7)), version=2)


def test_audits_for_additions(db: Session, admin_id: int) -> None:
    """第 11 条「新建商品写一条（新值为 slug），随它新建的规格名、规格值与 SKU 各写一条（新值为
    代码）；之后新增的规格值与 SKU 也各写一条」「每改一项写一条审计」；HANDOFF 0.77 的操作名
    admin_product_option_value_created、admin_product_variant_created 与对象类别：已有行的改动
    照写，之后新增的规格值与 SKU 各一条，对象 ID 为新行 ID，新值为代码，没有旧值。"""
    created = len(_audits(db))
    request = _edit_variant(replace(_current(db), name_zh="圆领衫"), 0, price_sen=2600)
    request = _add_values(request, 1, _new_value("xl", "XL"))
    request = _add_values(request, 0, _new_value("red", "Red"))
    request = _add_variants(request, _new_variant("n1", "red", "xl"), _new_variant("n2", 2, 3))

    _save(db, admin_id, request)

    sku_s = "crew-neck-tee-black-s"
    assert _audits(db)[created:] == [
        ("admin_product_changed", "product", 1, "name_zh 圆领 T 恤", "name_zh 圆领衫"),
        (
            "admin_product_variant_changed",
            "product_variant",
            1,
            f"{sku_s} price_sen 2500",
            f"{sku_s} price_sen 2600",
        ),
        ("admin_product_option_value_created", "product_option_value", 7, None, "red"),
        ("admin_product_option_value_created", "product_option_value", 8, None, "xl"),
        ("admin_product_variant_created", "product_variant", 6, None, "crew-neck-tee-red-xl"),
        ("admin_product_variant_created", "product_variant", 7, None, "crew-neck-tee-white-s"),
    ]
    accounts = db.scalars(select(AuditEvent.admin_account_id)).all()
    assert set(accounts) == {admin_id}


def test_write_error_rolls_back_additions(db: Session, admin_id: int) -> None:
    """第 11 条「服务端分三步，每步不通过都整次保存不生效」「之后新增的规格值与 SKU 也各写
    一条」：写审计时出错（时间带时区）整个事务回滚，新增的规格值、SKU 与版本号都不留下。"""
    request = _add_values(_current(db), 0, _new_value("red", "Red"))
    request = _add_variants(request, _new_variant("n1", "red", 3))
    before = _snapshot(db)

    with pytest.raises(ValueError):
        save_product(db, admin_id, TEE_ID, 1, request, NOW.replace(tzinfo=UTC))

    assert _snapshot(db) == before


def test_additions_keep_lock_order(db: Session, engine: Engine, admin_id: int) -> None:
    """第 11 条「在一个事务里先锁商品行，再按 ID 从小到大锁该商品的 SKU 行」「（三）同一事务里
    检查要查库的取值」：带新增项时仍是回滚后先锁商品行、再锁 SKU 行，之后才有写入。"""
    request = _add_variants(_current(db), _new_variant("n1", 2, 3))
    seen = _record_sequence(engine)

    _save(db, admin_id, request)

    assert seen[0] == "ROLLBACK"
    assert "FROM products" in seen[1]
    assert seen[1].rstrip().endswith("FOR UPDATE")
    assert "FROM product_variants" in seen[2]
    assert seen[2].rstrip().endswith("ORDER BY product_variants.id FOR UPDATE")

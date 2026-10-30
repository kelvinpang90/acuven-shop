"""后台维护分类与代码生成（app/services/admin_categories.py、app/services/admin_codes.py，
SHOP-TASK-180）。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 14 条（下称「第 14 条」）
与第 11 条「代码」一段（下称「第 11 条」），及 docs/HANDOFF.md 0.76（下称「HANDOFF 0.76」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库按模型建表（StaticPool），写入三个分类与几件商品并提交。
结果一律以只查列值的 SELECT 读出，不看会话里的 ORM 对象。
SQLite 不生成 FOR UPDATE：锁定查询另用 MySQL 方言编译检查。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, Select, create_engine, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import AdminAccount, AuditEvent, Category, Product, ProductVariant
from app.services import admin_categories
from app.services.admin_categories import (
    AdminCategory,
    CategoryChanged,
    CategoryInvalid,
    create_category,
    load_admin_categories,
    lock_categories_statement,
    save_category,
)
from app.services.admin_codes import code_base, unique_code
from app.services.catalog import list_categories, list_products

NOW = datetime(2026, 10, 30, 3, 0)

CREATED = "admin_category_created"
CHANGED = "admin_category_changed"
CATEGORY = "category"

# 种子数据：分类 1 启用、分类 2 停用、分类 3 停用且没有英文名称。
KITCHEN = AdminCategory(
    id=1,
    slug="kitchen-dining",
    name_en="Kitchen & Dining",
    name_zh="厨房",
    name_ms=None,
    is_active=True,
    version=1,
    visible_products=1,
)
GARDEN = AdminCategory(
    id=2,
    slug="garden",
    name_en="Garden",
    name_zh=None,
    name_ms=None,
    is_active=False,
    version=1,
    visible_products=0,
)
MISC = AdminCategory(
    id=3,
    slug="misc",
    name_en=None,
    name_zh="杂项",
    name_ms=None,
    is_active=False,
    version=1,
    visible_products=0,
)


def _product(db: Session, category_id: int, slug: str, **fields: object) -> None:
    """一件满足发布规则的商品（带一个启用的 SKU）；fields 覆盖默认值。"""
    defaults = {
        "name_en": slug.title(),
        "description_en": f"A {slug}.",
        "is_active": True,
        "max_per_order": 10,
    }
    product = Product(category_id=category_id, slug=slug, **(defaults | fields))
    db.add(product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=product.id,
            sku=f"{slug}-std",
            price_sen=1990,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        )
    )
    db.flush()


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        for row in (KITCHEN, GARDEN, MISC):
            seed.add(
                Category(
                    slug=row.slug,
                    name_en=row.name_en,
                    name_zh=row.name_zh,
                    name_ms=row.name_ms,
                    is_active=row.is_active,
                    version=1,
                )
            )
            seed.flush()
        _product(seed, 1, "pan")
        _product(seed, 1, "pot", is_active=False)
        _product(seed, 1, "lid", description_en=None)
        _product(seed, 2, "rake")
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


# ---- 辅助 ----


def _categories(db: Session) -> list[tuple]:
    stmt = select(
        Category.id,
        Category.slug,
        Category.name_en,
        Category.name_zh,
        Category.name_ms,
        Category.is_active,
        Category.version,
    ).order_by(Category.id)
    return [tuple(row) for row in db.execute(stmt)]


def _audits(db: Session) -> list[tuple]:
    stmt = select(
        AuditEvent.action,
        AuditEvent.target_type,
        AuditEvent.target_id,
        AuditEvent.old_value,
        AuditEvent.new_value,
    ).order_by(AuditEvent.id)
    return [tuple(row) for row in db.execute(stmt)]


def _snapshot(db: Session) -> tuple:
    return _categories(db), _audits(db)


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


def _create(db: Session, admin_id: int, name_en: object, **fields: object) -> AdminCategory:
    values = {"name_zh": "", "name_ms": "", "is_active": True} | fields
    return create_category(db, admin_id, name_en=name_en, now=NOW, **values)


def _save(db: Session, admin_id: int, base: AdminCategory, **fields: object) -> AdminCategory:
    """以读取时的值为底，覆盖 fields 后保存；名称为 None 时提交空串。"""
    values = {
        "category_id": base.id,
        "version": base.version,
        "name_en": base.name_en or "",
        "name_zh": base.name_zh or "",
        "name_ms": base.name_ms or "",
        "is_active": base.is_active,
    } | fields
    return save_category(db, admin_id, now=NOW, **values)


def _front_categories(db: Session) -> list[str]:
    return [item.slug for item in list_categories(db, "en")]


def _front_products(db: Session) -> list[str]:
    return sorted(item.slug for item in list_products(db, lang="en").items)


# ---- 代码生成 ----


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kitchen & Dining", "kitchen-dining"),
        ("  Bath, Body & More!  ", "bath-body-more"),
        ("--Tea--", "tea"),
        ("Café Crème 2", "caf-cr-me-2"),
        ("厨房 Kitchen", "kitchen"),
        ("&&& ---", "category"),
        ("厨房用品", "category"),
    ],
)
def test_code_base(name: str, expected: str) -> None:
    """第 11 条「英文名称只保留 ASCII 字母与数字并转小写，其余连续字符换成一个连字符，去掉首尾
    连字符」；第 14 条「结果为空时以 category 代替」：Kitchen & Dining 为 kitchen-dining，首尾
    空白与标点去掉，非 ASCII 字母当作分隔，全是符号时为 fallback。"""
    assert code_base(name, "category") == expected


def test_unique_code_truncates_before_suffix() -> None:
    """第 11 条「每次先把基本部分截到『上限减后缀长度』、去掉截短后末尾的连字符，再接后缀」：
    100 个字符的基本部分接 -2 时截到 98 并去掉末尾连字符。"""
    base = "a" * 97 + "-bb"
    assert len(base) == 100

    assert unique_code(base, 100, set()) == base
    assert unique_code(base, 100, {base}) == "a" * 97 + "-2"


def test_unique_code_tries_suffixes_in_order() -> None:
    """第 11 条「去重时依次试后缀（无、-2、-3……）……直到与已有的及同一次保存里先生成的都不
    重复」：已有 kitchen 与 kitchen-2 时为 kitchen-3。"""
    assert unique_code("kitchen", 100, set()) == "kitchen"
    assert unique_code("kitchen", 100, {"kitchen"}) == "kitchen-2"
    assert unique_code("kitchen", 100, {"kitchen", "kitchen-2"}) == "kitchen-3"


def test_unique_code_truncates_long_base_without_suffix() -> None:
    """第 11 条「长度上限为 slug 100」：超长的基本部分不带后缀时截到上限，并去掉末尾连字符。"""
    base = "b" * 99 + "-c" + "d" * 10

    assert unique_code(base, 100, set()) == "b" * 99


# ---- 读取 ----


def test_load_returns_all_categories_in_id_order(db: Session) -> None:
    """第 14 条「管理员在后台查看全部分类（含停用的，按 ID 从小到大，即前台分类列表的顺序）」
    「后台读取分类时返回每个分类下前台可见（满足现有发布规则）的商品件数」：下架商品、缺英文
    描述的商品与停用分类下的商品不计。"""
    assert load_admin_categories(db) == [KITCHEN, GARDEN, MISC]


def test_visible_products_matches_front_catalog(db: Session) -> None:
    """第 14 条「前台可见（满足现有发布规则）的商品件数」：与前台商品列表同一规则。"""
    front = list_products(db, lang="en").items
    counts = {row.slug: row.visible_products for row in load_admin_categories(db)}

    for slug, count in counts.items():
        assert count == sum(1 for item in front if item.category.slug == slug)


def test_load_only_selects_and_writes_no_audit(db: Session, engine: Engine) -> None:
    """第 14 条「管理员在后台查看全部分类」「后台读取分类时返回每个分类下前台可见（满足现有
    发布规则）的商品件数」：读取只是查看，只发 SELECT，不改库、不写审计。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    load_admin_categories(db)

    assert statements
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert _snapshot(db) == before


# ---- 新建 ----


def test_create_category(db: Session, admin_id: int) -> None:
    """第 14 条「分类的 slug……在新建时按英文名称生成」「名称每种语言 0–200 个字符，首尾空白
    去掉，空串存为空」「每个分类带版本号（新建为 1）」「新建写一条（新值为 slug）」。"""
    created = _create(db, admin_id, "  Bath & Body!  ", name_zh=" 浴室 ", name_ms="   ")

    assert created == AdminCategory(
        id=4,
        slug="bath-body",
        name_en="Bath & Body!",
        name_zh="浴室",
        name_ms=None,
        is_active=True,
        version=1,
        visible_products=0,
    )
    assert _categories(db)[-1] == (4, "bath-body", "Bath & Body!", "浴室", None, True, 1)
    assert _audits(db) == [(CREATED, CATEGORY, 4, None, "bath-body")]


def test_create_slug_falls_back_and_deduplicates(db: Session, admin_id: int) -> None:
    """第 14 条「写法与第 11 条商品 slug 的基本写法、截短与去重相同（上限 100 个字符，全站分类
    之间唯一，结果为空时以 category 代替）」。"""
    first = _create(db, admin_id, "厨房用品")
    second = _create(db, admin_id, "家居!")
    third = _create(db, admin_id, "Garden Tools")

    assert (first.slug, second.slug, third.slug) == ("category", "category-2", "garden-tools")


def test_create_accepts_200_characters_after_trimming(db: Session, admin_id: int) -> None:
    """第 14 条「名称每种语言 0–200 个字符，首尾空白去掉」：去掉首尾空白后 200 个字符可以；
    slug 截到 100 个字符。"""
    name = "n" * 200

    created = _create(db, admin_id, f"  {name}  ", name_zh=name, name_ms=name)

    assert created.name_en == name
    assert created.slug == "n" * 100


@pytest.mark.parametrize(
    "fields",
    [
        {"name_en": ""},
        {"name_en": "   "},
        {"name_en": None},
        {"name_en": "x" * 201},
        {"name_zh": "x" * 201},
        {"name_ms": None},
        {"name_zh": 1},
        {"is_active": "true"},
        {"is_active": 1},
        {"is_active": None},
    ],
)
def test_create_rejects_invalid_request_without_sql(
    db: Session, engine: Engine, admin_id: int, fields: dict
) -> None:
    """第 14 条「所以新建时英文名称必填」「名称每种语言 0–200 个字符」：缺英文名称、超长、不是
    字符串、启用状态不是布尔值都抛 CategoryInvalid，不发任何 SQL、不写库。"""
    values = {"name_en": "Toys", "name_zh": "", "name_ms": "", "is_active": True} | fields
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(CategoryInvalid):
        create_category(db, admin_id, now=NOW, **values)

    assert statements == []
    assert _snapshot(db) == before


def test_create_rejects_31st_category(db: Session, admin_id: int) -> None:
    """第 14 条「全站至多 30 个分类，已满时新建回答『取值不合格』」：第 31 个被拒、不写库。"""
    for number in range(4, 31):
        _create(db, admin_id, f"Category {number}")
    assert len(_categories(db)) == 30
    before = _snapshot(db)

    with pytest.raises(CategoryInvalid):
        _create(db, admin_id, "Category 31")

    assert _snapshot(db) == before


@pytest.mark.parametrize("name", ["Kitchen & Dining", "  kitchen & dining ", "KITCHEN & DINING"])
def test_create_rejects_duplicate_english_name(db: Session, admin_id: int, name: str) -> None:
    """第 14 条「全部分类的英文名称按第 11 条的口径比较不得重复」；第 11 条「去掉首尾空白并转
    小写后比较不得重复」：大小写与首尾空白不同也算重复，不写库。停用分类同样参与比较。"""
    before = _snapshot(db)

    with pytest.raises(CategoryInvalid):
        _create(db, admin_id, name)
    with pytest.raises(CategoryInvalid):
        _create(db, admin_id, " garden ", is_active=False)

    assert _snapshot(db) == before


def test_create_slug_conflict_is_changed(
    db: Session, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 14 条「新建时 slug 撞上唯一约束（只在两个后台会话同时新建时发生）同样回答『分类已被
    改动』」：撞上即回滚、抛 CategoryChanged，不写库。"""
    monkeypatch.setattr(admin_categories, "unique_code", lambda *_args: "garden")
    before = _snapshot(db)

    with pytest.raises(CategoryChanged):
        _create(db, admin_id, "Toys")

    assert _snapshot(db) == before


def test_create_rolls_back_on_audit_error(db: Session, admin_id: int) -> None:
    """第 14 条「计数与插入在同一事务里」「新建写一条（新值为 slug）」：审计被拒（时间带时区）
    时整个事务回滚，不留下新分类。"""
    before = _snapshot(db)

    with pytest.raises(ValueError):
        create_category(db, admin_id, "Toys", "", "", True, NOW.replace(tzinfo=UTC))

    assert _snapshot(db) == before


# ---- 保存 ----


def test_save_writes_one_audit_per_changed_field(db: Session, admin_id: int) -> None:
    """第 14 条「有改动才加版本、写审计」「之后每改一项写一条，对象为分类、以行 ID 记录，旧值
    与新值写成『字段 值』（文案的字段带语言……；启用状态为 true 或 false）」：为空时只写字段名，
    按 name_en、name_zh、name_ms、is_active 的顺序。"""
    saved = _save(db, admin_id, KITCHEN, name_zh="", name_ms=" Dapur ", is_active=False)

    assert saved == AdminCategory(
        id=1,
        slug="kitchen-dining",
        name_en="Kitchen & Dining",
        name_zh=None,
        name_ms="Dapur",
        is_active=False,
        version=2,
        visible_products=0,
    )
    assert _categories(db)[0] == (1, "kitchen-dining", "Kitchen & Dining", None, "Dapur", False, 2)
    assert _audits(db) == [
        (CHANGED, CATEGORY, 1, "name_zh 厨房", "name_zh"),
        (CHANGED, CATEGORY, 1, "name_ms", "name_ms Dapur"),
        (CHANGED, CATEGORY, 1, "is_active true", "is_active false"),
    ]


def test_save_audit_text_takes_first_100_characters(db: Session, admin_id: int) -> None:
    """第 14 条「文案的字段带语言，值取前 100 个字符」。"""
    name = "Long " + "x" * 150

    _save(db, admin_id, KITCHEN, name_en=name)

    assert _audits(db) == [
        (CHANGED, CATEGORY, 1, "name_en Kitchen & Dining", "name_en " + name[:100]),
    ]


def test_save_unchanged_writes_nothing(db: Session, engine: Engine, admin_id: int) -> None:
    """第 14 条「有改动才加版本、写审计」：与当前相同（提交的名称带首尾空白也一样）时只发
    SELECT、不加版本、不写审计，返回与读取相同的一项。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    saved = _save(db, admin_id, KITCHEN, name_en="  Kitchen & Dining ", name_zh="厨房 ")

    assert saved == KITCHEN
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert _snapshot(db) == before


def test_rename_keeps_slug(db: Session, admin_id: int) -> None:
    """第 14 条「分类的 slug……在新建时按英文名称生成，之后改名称也不变」：改名后 slug 不变；
    原名空出后可再新建，新分类按全站 slug 去重。"""
    saved = _save(db, admin_id, KITCHEN, name_en="Cookware")
    created = _create(db, admin_id, "Kitchen & Dining")

    assert saved.slug == "kitchen-dining"
    assert created.slug == "kitchen-dining-2"


def test_save_may_change_case_of_own_name(db: Session, admin_id: int) -> None:
    """第 14 条「全部分类的英文名称……不得重复」：只与其他分类比较，改自己名称的大小写可以。"""
    saved = _save(db, admin_id, KITCHEN, name_en="KITCHEN & DINING")

    assert saved.name_en == "KITCHEN & DINING"
    assert saved.version == 2


@pytest.mark.parametrize("name", ["Garden", " garden ", "GARDEN"])
def test_save_rejects_duplicate_english_name(db: Session, admin_id: int, name: str) -> None:
    """第 14 条「全部分类的英文名称按第 11 条的口径比较不得重复」：大小写与首尾空白不同也算
    重复，抛 CategoryInvalid、不写库。"""
    before = _snapshot(db)

    with pytest.raises(CategoryInvalid):
        _save(db, admin_id, KITCHEN, name_en=name)

    assert _snapshot(db) == before


def test_save_inactive_without_english_name(db: Session, admin_id: int) -> None:
    """第 14 条「启用时须有英文名称」「停用不检查」「为空的不参与比较」：停用的分类可以去掉
    英文名称，与另一个没有英文名称的分类不算重复。"""
    saved = _save(db, admin_id, GARDEN, name_en="")

    assert saved.name_en is None
    assert _audits(db) == [(CHANGED, CATEGORY, 2, "name_en Garden", "name_en")]


@pytest.mark.parametrize(
    "fields",
    [
        {"is_active": True, "name_en": ""},
        {"is_active": True, "name_en": "   "},
        {"version": True},
        {"version": "1"},
        {"version": 1.0},
        {"is_active": "false"},
        {"name_en": None},
        {"name_ms": "x" * 201},
    ],
)
def test_save_rejects_invalid_request_without_sql(
    db: Session, engine: Engine, admin_id: int, fields: dict
) -> None:
    """第 14 条「启用时须有英文名称……不满足即回答『取值不合格』」「名称每种语言 0–200 个
    字符」：启用而缺英文名称、版本号不是整数（布尔值不算）、启用状态不是布尔值、名称不是字符串
    或超长都抛 CategoryInvalid，不发任何 SQL、不写库。"""
    before = _snapshot(db)
    statements = _record_statements(engine)

    with pytest.raises(CategoryInvalid):
        _save(db, admin_id, MISC, **fields)

    assert statements == []
    assert _snapshot(db) == before


@pytest.mark.parametrize("fields", [{"version": 2}, {"version": 0}, {"category_id": 999}])
def test_save_stale_or_missing_is_changed(db: Session, admin_id: int, fields: dict) -> None:
    """第 14 条「保存时带读取时的版本号，在事务里锁住该分类行核对，不同即回答『分类已被
    改动』」：版本号不符与分类不存在都抛 CategoryChanged，不写库。"""
    before = _snapshot(db)

    with pytest.raises(CategoryChanged):
        _save(db, admin_id, KITCHEN, name_en="Cookware", **fields)

    assert _snapshot(db) == before


def test_save_with_old_version_after_another_save_is_changed(db: Session, admin_id: int) -> None:
    """第 14 条「不同即回答『分类已被改动』，页面提示重新读取」：别人先保存后，带旧版本号的
    保存被拒，不覆盖对方的修改。"""
    _save(db, admin_id, KITCHEN, name_zh="厨具")
    before = _snapshot(db)

    with pytest.raises(CategoryChanged):
        _save(db, admin_id, KITCHEN, name_ms="Dapur")

    assert _snapshot(db) == before


def test_deactivate_hides_category_and_products_until_reactivated(
    db: Session, admin_id: int
) -> None:
    """第 14 条「分类停用后，前台不再显示该分类，它下面的商品按现有发布规则也不再显示（商品
    自己的上架状态不变，分类重新启用后照常显示）」。"""
    assert "kitchen-dining" in _front_categories(db)
    assert _front_products(db) == ["pan"]

    stopped = _save(db, admin_id, KITCHEN, is_active=False)

    assert "kitchen-dining" not in _front_categories(db)
    assert _front_products(db) == []
    assert db.scalar(select(Product.is_active).where(Product.slug == "pan")) is True

    restarted = _save(db, admin_id, stopped, is_active=True)

    assert restarted.version == 3
    assert restarted.visible_products == 1
    assert "kitchen-dining" in _front_categories(db)
    assert _front_products(db) == ["pan"]


# ---- 锁定 ----


def test_lock_statement_is_select_for_update() -> None:
    """第 14 条「计数与插入在同一事务里，先锁住现有的全部分类行再数」；HANDOFF 0.76「先锁住现有
    全部分类行（至多 30 行，按 id 排序）」：按 MySQL 方言编译带 FOR UPDATE、按 id 排序、不带
    条件，并带 populate_existing。"""
    stmt = lock_categories_statement()
    sql = str(stmt.compile(dialect=mysql.dialect()))

    assert "FROM categories" in sql
    assert "WHERE" not in sql
    assert "ORDER BY categories.id" in sql
    assert sql.rstrip().endswith("FOR UPDATE")
    assert stmt.get_execution_options()["populate_existing"] is True


def _assert_lock_follows_rollback(seen: list[str]) -> None:
    lock = str(lock_categories_statement().compile(dialect=mysql.dialect()))
    assert seen[:2] == ["ROLLBACK", lock]
    assert [sql for sql in seen if "FOR UPDATE" in sql] == [lock]


def test_create_locks_first_after_rollback(db: Session, engine: Engine, admin_id: int) -> None:
    """第 14 条「计数与插入在同一事务里，先锁住现有的全部分类行再数」：新建回滚此前的事务后，
    第一条语句是锁定查询，且只锁一次。"""
    load_admin_categories(db)
    seen = _record_sequence(engine)

    _create(db, admin_id, "Toys")

    _assert_lock_follows_rollback(seen)


def test_save_locks_first_after_rollback(db: Session, engine: Engine, admin_id: int) -> None:
    """第 14 条「在事务里锁住该分类行核对」；HANDOFF 0.76「保存在其中核对该分类的版本号」：
    保存回滚此前的事务后，第一条语句是与新建同一条锁定查询，且只锁一次。"""
    load_admin_categories(db)
    seen = _record_sequence(engine)

    _save(db, admin_id, KITCHEN, name_ms="Dapur")

    _assert_lock_follows_rollback(seen)

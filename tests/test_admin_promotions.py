"""后台促销活动的列表、读取、新建与保存（app/services/admin_promotions.py，SHOP-TASK-249）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条（下称「第 N 条」）、
docs/UX.md 0.20 的 A12（下称「UX A12」），以及 SHOP-TASK-249 写定的落实写法（下称
「落实写法」）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库（StaticPool，打开外键检查）按模型建表，写入管理员、发布锁那一行、
两个分类与三件商品并提交。SQLite 不生成 FOR UPDATE，加锁的顺序与效果由 MySQL 上的
并发守住；这里只核对结果。活动的发布由 SHOP-TASK-241 提供，这里直接改状态列。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, event, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AdminAccount,
    AuditEvent,
    Category,
    Order,
    Product,
    ProductVariant,
    Promotion,
    PromotionDecoration,
    PromotionPublishLock,
)
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionChanged,
    PromotionDetail,
    PromotionInput,
    PromotionInvalid,
    PromotionNotFound,
    PromotionOverlap,
    PromotionProblem,
    RuleInput,
    ScopeProduct,
    TierInput,
    create_promotion,
    list_promotions,
    read_promotion,
    save_promotion,
    scope_overlap_problems,
)

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。
NOW = datetime(2026, 11, 13, 4, 0)

# 种子数据的 ID：分类 Kitchen、Garden；商品 Pan（Kitchen，上架）、Pot（Kitchen，未上架）、
# Rake（Garden，上架）。
KITCHEN, GARDEN = 1, 2
PAN, POT, RAKE = 1, 2, 3

# 马来西亚本地时间的活动时段：相对 NOW 未开始、进行中、已结束。
WINDOWS = {
    "scheduled": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "running": ("2026-11-10T00:00", "2026-11-20T00:00"),
    "ended": ("2026-11-01T00:00", "2026-11-05T00:00"),
}

CREATED = "admin_promotion_created"
CHANGED = "admin_promotion_changed"


def _product(db: Session, category_id: int, slug: str, *, is_active: bool = True) -> None:
    """一件商品（带一个启用的 SKU）；is_active 为真时满足发布规则。"""
    product = Product(
        category_id=category_id,
        slug=slug,
        name_en=slug.title(),
        description_en=f"A {slug}.",
        is_active=is_active,
        max_per_order=10,
    )
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

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(PromotionPublishLock(singleton_slot=1))
        for slug in ("kitchen", "garden"):
            seed.add(Category(slug=slug, name_en=slug.title(), is_active=True, version=1))
            seed.flush()
        _product(seed, KITCHEN, "pan")
        _product(seed, KITCHEN, "pot", is_active=False)
        _product(seed, GARDEN, "rake")
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


def _item(
    products: tuple[int, ...] = (), categories: tuple[int, ...] = (), percent: int = 20
) -> RuleInput:
    return RuleInput(
        kind="item_discount",
        discount_mode="percent",
        percent=percent,
        product_ids=products,
        category_ids=categories,
    )


def _decoration(**fields: str) -> DecorationInput:
    values = {
        "pack": "cny",
        "palette": "theme",
        "ambient": "none",
        "home_poster": "split",
        "list_strip": "band",
        "card_badge": "tag",
        "detail_ribbon": "ribbon",
        "cart_progress": "bar",
        "poster_mode": "template",
    }
    return DecorationInput(**(values | fields))


def _input(**fields: object) -> PromotionInput:
    starts_at, ends_at = WINDOWS["scheduled"]
    values: dict[str, object] = {
        "name": "Raya",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "coupon_combinable": False,
        "points_combinable": False,
        "rules": (_item(products=(PAN,)),),
        "decoration": _decoration(),
        "copy": CopyInput(title_en="Raya Sale"),
    }
    return PromotionInput(**(values | fields))


def _publish(db: Session, promotion_id: int, **values: object) -> None:
    db.execute(
        update(Promotion).where(Promotion.id == promotion_id).values(status="published", **values)
    )
    db.commit()


def _make(
    db: Session, admin_id: int, state: str, now: datetime = NOW, **fields: object
) -> PromotionDetail:
    """按 WINDOWS 建一个处于 state 的活动（非草稿的直接改为已发布）。"""
    if state != "draft":
        starts_at, ends_at = WINDOWS[state]
        fields = {"starts_at": starts_at, "ends_at": ends_at} | fields
    created = create_promotion(db, admin_id, _input(**fields), now)
    if state != "draft":
        _publish(db, created.id)
    detail = read_promotion(db, created.id, NOW)
    assert detail.state == state
    return detail


def _order(db: Session, promotion_id: int) -> None:
    """一张引用该活动的订单（只填约束要求的列）。"""
    db.add(
        Order(
            order_number="0" * 16,
            status="awaiting_demo_payment",
            subtotal_sen=1000,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=0,
            total_sen=900,
            points_earned=0,
            shipping_zone_code="WM",
            shipping_rate_version=1,
            idempotency_key="order-1",
            request_fingerprint="0" * 64,
            payment_expires_at=NOW,
            promotion_id=promotion_id,
            promotion_item_discount_sen=100,
        )
    )
    db.commit()


def _audits(db: Session) -> list[tuple]:
    stmt = select(
        AuditEvent.action,
        AuditEvent.target_type,
        AuditEvent.target_id,
        AuditEvent.old_value,
        AuditEvent.new_value,
    ).order_by(AuditEvent.id)
    return [tuple(row) for row in db.execute(stmt)]


def _promotion_count(db: Session) -> int:
    return len(db.scalars(select(Promotion.id)).all())


def _problems(excinfo: pytest.ExceptionInfo[PromotionInvalid]) -> tuple[PromotionProblem, ...]:
    return excinfo.value.problems


# ---- 引用存在与商品级规则重叠 ----


def test_missing_product_and_category_are_scope_problems(db: Session, admin_id: int) -> None:
    """验收「规则引用的商品与分类须存在（问题码 scope）」：不存在的商品与分类各报在
    该规则的 product_ids 与 category_ids，整次不生效（UX A12「整次不生效」）。"""
    data = _input(rules=(_item(products=(PAN, 999), categories=(999,)),))

    with pytest.raises(PromotionInvalid) as excinfo:
        create_promotion(db, admin_id, data, NOW)

    assert _problems(excinfo) == (
        PromotionProblem("scope", "rules.1.product_ids"),
        PromotionProblem("scope", "rules.1.category_ids"),
    )
    assert _promotion_count(db) == 0
    assert _audits(db) == []


def test_overlap_by_explicit_product_names_the_product(db: Session, admin_id: int) -> None:
    """第 3 条「一件商品在同一活动里至多属于一条商品级规则：保存与发布时…重叠即回答
    取值不合格并指出重叠的商品」：两条规则显式选同一件商品，报 scope_overlap，带商品
    ID 与英文名称。"""
    data = _input(rules=(_item(products=(PAN, RAKE)), _item(products=(RAKE,), percent=30)))

    with pytest.raises(PromotionInvalid) as excinfo:
        create_promotion(db, admin_id, data, NOW)

    assert _problems(excinfo) == (
        PromotionProblem("scope_overlap", "rules.2", product_id=RAKE, product_name="Rake"),
    )
    assert _promotion_count(db) == 0


def test_overlap_by_category_counts_only_listed_products(db: Session, admin_id: int) -> None:
    """落实写法「商品级规则的重叠按显式选的商品（不论是否上架），加上所选分类当前下属
    且满足现有发布规则的商品计算」：分类 Kitchen 带上架的 Pan，与显式选的 Pan 重叠；
    未上架的 Pot 不随分类算入，显式选的 Pot 不与分类重叠。"""
    data = _input(rules=(_item(products=(PAN, POT)), _item(categories=(KITCHEN,), percent=30)))

    with pytest.raises(PromotionInvalid) as excinfo:
        create_promotion(db, admin_id, data, NOW)

    assert _problems(excinfo) == (
        PromotionProblem("scope_overlap", "rules.2", product_id=PAN, product_name="Pan"),
    )


def test_unlisted_product_chosen_twice_overlaps(db: Session, admin_id: int) -> None:
    """落实写法「显式选的商品（不论是否上架）」：未上架的 Pot 在两条规则里都被显式选中，
    仍是重叠。"""
    data = _input(rules=(_item(products=(POT,)), _item(products=(POT,), percent=30)))

    with pytest.raises(PromotionInvalid) as excinfo:
        create_promotion(db, admin_id, data, NOW)

    assert _problems(excinfo) == (
        PromotionProblem("scope_overlap", "rules.2", product_id=POT, product_name="Pot"),
    )


def test_running_promotion_saves_copy_after_product_moves_category(
    db: Session, admin_id: int
) -> None:
    """第 3 条末段「之后商品改了分类而造成重叠的，计价时取排序数较小的规则」与落实写法
    「只在草稿与未开始活动的保存时检查——若仍检查则进行中的活动永远保存不了文案」：
    Rake 改到 Kitchen 后与分类规则重叠，进行中的活动照样能保存文案。"""
    detail = _make(
        db,
        admin_id,
        "running",
        rules=(_item(products=(RAKE,)), _item(categories=(KITCHEN,), percent=30)),
    )
    db.execute(update(Product).where(Product.id == RAKE).values(category_id=KITCHEN))
    db.commit()
    assert scope_overlap_problems(db, detail.id) == [
        PromotionProblem("scope_overlap", "rules.2", product_id=RAKE, product_name="Rake")
    ]

    data = replace(detail.as_input(), copy=replace(detail.copy, subtitle_en="Ends soon"))
    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert saved.version == detail.version + 1
    assert saved.copy.subtitle_en == "Ends soon"
    assert [rule.rule_id for rule in saved.rules] == [rule.rule_id for rule in detail.rules]


def test_reordered_scope_is_not_a_change(db: Session, admin_id: int) -> None:
    """落实写法「商品 ID 与分类 ID 作为集合相同…相同即不替换规则行（不改规则 ID、不算
    改动）」：只调换范围里的顺序，版本不变、规则 ID 不变、不写审计。"""
    detail = _make(
        db, admin_id, "draft", rules=(_item(products=(PAN, RAKE), categories=(KITCHEN, GARDEN)),)
    )
    audits = _audits(db)
    data = replace(
        detail.as_input(),
        rules=(_item(products=(RAKE, PAN), categories=(GARDEN, KITCHEN)),),
    )

    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert saved.version == detail.version
    assert saved.rules[0].rule_id == detail.rules[0].rule_id
    assert _audits(db) == audits


# ---- 按状态的可改范围 ----


@pytest.mark.parametrize(
    ("field", "change"),
    [
        pytest.param(
            "rules",
            lambda d: replace(d, rules=(_item(products=(PAN,), percent=30),)),
            id="rules",
        ),
        pytest.param("starts_at", lambda d: replace(d, starts_at="2026-11-10T01:00"), id="starts"),
        pytest.param("ends_at", lambda d: replace(d, ends_at="2026-11-21T00:00"), id="ends"),
        pytest.param(
            "coupon_combinable", lambda d: replace(d, coupon_combinable=True), id="coupon"
        ),
        pytest.param(
            "points_combinable", lambda d: replace(d, points_combinable=True), id="points"
        ),
    ],
)
def test_running_rejects_rule_time_and_flag_changes(
    db: Session,
    admin_id: int,
    field: str,
    change: Callable[[PromotionInput], PromotionInput],
) -> None:
    """第 2 条「已开始的活动，规则、开始时间、结束时间与两个同用开关不能再改」与 UX A12
    状态表「进行中：开始与结束时间、规则与同用开关只读」：请求里的这些项与当前不同即
    rules_locked，什么都不写。"""
    detail = _make(db, admin_id, "running")
    audits = _audits(db)

    with pytest.raises(PromotionInvalid) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, change(detail.as_input()), NOW)

    assert _problems(excinfo) == (PromotionProblem("rules_locked", field),)
    assert read_promotion(db, detail.id, NOW) == detail
    assert _audits(db) == audits


def test_running_clearing_english_title_is_publish_missing(db: Session, admin_id: int) -> None:
    """落实写法「已发布的活动（scheduled 与 running）每次保存都要仍满足内容方面的发布
    条件（英文标题…）」：进行中的活动清空英文标题报 publish_missing。"""
    detail = _make(db, admin_id, "running")
    data = replace(detail.as_input(), copy=replace(detail.copy, title_en="  "))

    with pytest.raises(PromotionInvalid) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert _problems(excinfo) == (PromotionProblem("publish_missing", "title_en"),)


def test_ended_is_readonly(db: Session, admin_id: int) -> None:
    """UX A12 状态表「已结束：无（全部只读）」与第 2 条「装饰、海报与文案随时可改，
    已结束的除外」：已结束的活动连原样保存也报 ended_readonly。"""
    detail = _make(db, admin_id, "ended")

    with pytest.raises(PromotionInvalid) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, detail.as_input(), NOW)

    assert _problems(excinfo) == (PromotionProblem("ended_readonly", "state"),)


def test_running_saves_name_decoration_and_copy(db: Session, admin_id: int) -> None:
    """UX A12 状态表「进行中：内部名称、文案、装饰、海报…可改」：改内部名称、素材包与
    副标题成功，版本加一，每个改动的字段一条审计。"""
    detail = _make(db, admin_id, "running")
    data = replace(
        detail.as_input(),
        name="Raya 2026",
        decoration=replace(detail.decoration, pack="raya"),
        copy=replace(detail.copy, subtitle_en="Last days"),
    )

    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert saved.version == detail.version + 1
    assert (saved.name, saved.decoration.pack, saved.copy.subtitle_en) == (
        "Raya 2026",
        "raya",
        "Last days",
    )
    assert [row[3:] for row in _audits(db) if row[0] == CHANGED] == [
        ("name Raya", "name Raya 2026"),
        ("pack cny", "pack raya"),
        ("subtitle_en", "subtitle_en Last days"),
    ]


def test_scheduled_with_order_is_treated_as_running(db: Session, admin_id: int) -> None:
    """落实写法「锁住活动行后以加锁读查是否已有订单引用该活动（orders.promotion_id），
    有即按 running 处理」：已被订单引用的未开始活动改规则报 rules_locked。"""
    detail = _make(db, admin_id, "scheduled")
    _order(db, detail.id)
    data = replace(detail.as_input(), rules=(_item(products=(PAN,), percent=30),))

    with pytest.raises(PromotionInvalid) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert _problems(excinfo) == (PromotionProblem("rules_locked", "rules"),)


def test_scheduled_without_order_replaces_rules(db: Session, admin_id: int) -> None:
    """第 2 条「已发布但未开始的可修改」与落实写法「保存规则时整批替换该活动的规则、
    范围与满额档」：没有订单引用的未开始活动可以改规则。"""
    detail = _make(db, admin_id, "scheduled")
    rules = (_item(products=(PAN,), percent=30), _item(categories=(GARDEN,)))
    data = replace(detail.as_input(), rules=rules)

    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert saved.version == detail.version + 1
    assert tuple(rule.as_input() for rule in saved.rules) == rules


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        pytest.param(lambda d: replace(d, rules=()), ("rules",), id="no_rules"),
        pytest.param(
            lambda d: replace(d, copy=replace(d.copy, title_en=None)), ("title_en",), id="title"
        ),
        pytest.param(lambda d: replace(d, starts_at=None), ("starts_at",), id="no_start"),
        pytest.param(lambda d: replace(d, ends_at=None), ("ends_at",), id="no_end"),
        pytest.param(
            lambda d: replace(d, starts_at="2026-11-01T00:00", ends_at="2026-11-12T00:00"),
            ("ends_at_past",),
            id="ends_past",
        ),
        pytest.param(
            lambda d: replace(d, decoration=replace(d.decoration, poster_mode="image")),
            ("poster_desktop", "poster_phone", "poster_alt_en"),
            id="poster_image",
        ),
    ],
)
def test_scheduled_save_rechecks_publish_conditions(
    db: Session,
    admin_id: int,
    change: Callable[[PromotionInput], PromotionInput],
    fields: tuple[str, ...],
) -> None:
    """第 2 条「已发布但未开始的可修改（每次保存重新检查发布条件）」：至少一条规则、
    英文标题、开始与结束时间、结束晚于当前时刻、上传图模式的两张图与英文替代文字，
    缺的每项一个 publish_missing。"""
    detail = _make(db, admin_id, "scheduled")

    with pytest.raises(PromotionInvalid) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, change(detail.as_input()), NOW)

    assert _problems(excinfo) == tuple(PromotionProblem("publish_missing", f) for f in fields)
    assert read_promotion(db, detail.id, NOW) == detail


def test_scheduled_time_overlap_names_other_promotion(db: Session, admin_id: int) -> None:
    """第 1 条「所有已发布活动的开始时间到实际结束时间两两不得重叠（相接可以）」与
    UX A12「与其他已发布活动时间重叠为 promo_overlap（name 为对方的内部名称）」。"""
    other = create_promotion(
        db,
        admin_id,
        _input(name="Merdeka", starts_at="2026-12-10T00:00", ends_at="2026-12-20T00:00"),
        NOW,
    )
    _publish(db, other.id)
    detail = _make(db, admin_id, "scheduled")

    overlapping = replace(detail.as_input(), ends_at="2026-12-10T00:01")
    with pytest.raises(PromotionOverlap) as excinfo:
        save_promotion(db, admin_id, detail.id, detail.version, overlapping, NOW)
    assert excinfo.value.other_name == "Merdeka"

    touching = replace(detail.as_input(), starts_at="2026-12-02T00:00")
    saved = save_promotion(db, admin_id, detail.id, detail.version, touching, NOW)
    assert saved.starts_at == "2026-12-02T00:00"


def test_draft_skips_publish_conditions_and_time_overlap(db: Session, admin_id: int) -> None:
    """第 2 条「草稿可任意修改」与 UX A12「草稿可以不填时间」：草稿不查发布条件与时间
    重叠。"""
    other = _make(db, admin_id, "scheduled", name="Merdeka")
    detail = _make(db, admin_id, "draft")
    data = replace(detail.as_input(), ends_at=None, copy=CopyInput())
    assert other.starts_at == detail.starts_at

    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    assert (saved.ends_at, saved.copy.title_en, saved.version) == (None, None, 2)


# ---- 版本 ----


def test_version_mismatch_and_missing_promotion(db: Session, admin_id: int) -> None:
    """第 2 条「保存…带读取时的版本号，在事务里锁住活动行核对，不同即回答活动已被改动」：
    版本不符与活动不存在都抛 PromotionChanged；读取不存在的活动抛 PromotionNotFound。"""
    detail = _make(db, admin_id, "draft")
    data = replace(detail.as_input(), name="Other")

    with pytest.raises(PromotionChanged):
        save_promotion(db, admin_id, detail.id, detail.version + 1, data, NOW)
    with pytest.raises(PromotionChanged):
        save_promotion(db, admin_id, 999, 1, data, NOW)
    with pytest.raises(PromotionNotFound):
        read_promotion(db, 999, NOW)
    assert read_promotion(db, detail.id, NOW) == detail


def test_unchanged_save_keeps_version_and_writes_no_audit(db: Session, admin_id: int) -> None:
    """第 2 条「有改动即版本号加一」与验收「没有改动不加版本、不写审计」。"""
    detail = _make(db, admin_id, "scheduled")
    audits = _audits(db)

    saved = save_promotion(db, admin_id, detail.id, detail.version, detail.as_input(), NOW)

    assert saved == detail
    assert _audits(db) == audits


# ---- 审计 ----


def test_audit_rows_and_values(db: Session, admin_id: int) -> None:
    """第 2 条「新建、每次保存的改动项…都写 AuditEvent，不含个人资料」与验收的写法：
    新建一条（新值为内部名称）；每个改动的字段一条「字段 值」，时间为马来西亚本地时间，
    布尔为 true 或 false，空值只写字段名，文案带语言、取前 100 个字符，规则只写一条
    「rules 条数」。"""
    detail = _make(db, admin_id, "draft")
    long_alt = "a" * 150
    data = replace(
        detail.as_input(),
        name="Raya 2026",
        starts_at="2026-12-02T00:00",
        coupon_combinable=True,
        rules=(_item(products=(PAN,)), _item(products=(RAKE,))),
        decoration=replace(detail.decoration, pack="raya"),
        copy=replace(detail.copy, title_zh="开斋节", poster_alt_en=long_alt),
    )

    saved = save_promotion(db, admin_id, detail.id, detail.version, data, NOW)

    target = ("promotion", detail.id)
    assert _audits(db) == [
        (CREATED, *target, None, "Raya"),
        (CHANGED, *target, "name Raya", "name Raya 2026"),
        (CHANGED, *target, "starts_at 2026-12-01T00:00", "starts_at 2026-12-02T00:00"),
        (CHANGED, *target, "coupon_combinable false", "coupon_combinable true"),
        (CHANGED, *target, "rules 1", "rules 2"),
        (CHANGED, *target, "pack cny", "pack raya"),
        (CHANGED, *target, "title_zh", "title_zh 开斋节"),
        (CHANGED, *target, "poster_alt_en", "poster_alt_en " + "a" * 100),
    ]
    assert saved.version == 2


# ---- 列表与读取 ----


def test_list_order_and_fields(db: Session, admin_id: int) -> None:
    """UX A12「列表按进行中、未开始（开始时间从早到晚）、草稿（最近修改在前）、已结束
    （结束时间从晚到早）排列」与验收「同序按 ID 从大到小」「ends_at 为实际结束」。"""
    scheduled_late = _make(
        db, admin_id, "scheduled", starts_at="2026-12-21T00:00", ends_at="2026-12-25T00:00"
    )
    draft_old = _make(db, admin_id, "draft", now=NOW - timedelta(hours=2), starts_at=None)
    ended_first = _make(db, admin_id, "ended")
    running = _make(db, admin_id, "running")
    draft_tie = _make(db, admin_id, "draft", now=NOW - timedelta(hours=2))
    scheduled_early = _make(db, admin_id, "scheduled")
    draft_new = _make(db, admin_id, "draft", now=NOW - timedelta(hours=1))
    # 名义上 11-30 结束、11-08 08:00（马来西亚时间）提前结束：按实际结束排在 ended_first 前。
    ended_early = _make(
        db, admin_id, "draft", starts_at="2026-11-02T00:00", ends_at="2026-11-30T00:00"
    )
    _publish(db, ended_early.id, ended_early_at=datetime(2026, 11, 8, 0, 0))

    summaries = list_promotions(db, NOW)

    assert [s.id for s in summaries] == [
        running.id,
        scheduled_early.id,
        scheduled_late.id,
        draft_new.id,
        draft_tie.id,
        draft_old.id,
        ended_early.id,
        ended_first.id,
    ]
    by_id = {s.id: s for s in summaries}
    assert by_id[ended_early.id].state == "ended"
    assert (by_id[ended_early.id].starts_at, by_id[ended_early.id].ends_at) == (
        "2026-11-02T00:00",
        "2026-11-08T08:00",
    )
    old = by_id[draft_old.id]
    assert (old.name, old.state, old.starts_at, old.rule_count, old.updated_at) == (
        "Raya",
        "draft",
        None,
        1,
        NOW - timedelta(hours=2),
    )


def test_read_promotion_detail(db: Session, admin_id: int) -> None:
    """验收「read_promotion 返回…规则各带 rule_id 与所选商品的 slug 和英文名称、两张海报图
    的提供路径（/api/promotion-posters/ 加文件名加 .webp）或 None」与第 3 条满额档。"""
    spend = RuleInput(
        kind="spend_discount",
        discount_mode="amount",
        tiers=(TierInput(5000, 500, None), TierInput(10000, 1200, None)),
    )
    data = _input(
        rules=(_item(products=(RAKE, PAN), categories=(KITCHEN,)), spend),
        copy=CopyInput(title_en=" Raya Sale ", badge_zh=""),
    )
    created = create_promotion(db, admin_id, data, NOW)
    db.execute(
        update(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == created.id)
        .values(
            poster_desktop_file_name="a" * 32,
            poster_desktop_source_format="png",
            poster_desktop_width=1600,
            poster_desktop_height=533,
            poster_desktop_byte_size=1000,
        )
    )
    db.commit()

    detail = read_promotion(db, created.id, NOW)

    item, spend_rule = detail.rules
    assert item.product_ids == (PAN, RAKE)
    assert item.category_ids == (KITCHEN,)
    assert item.products == (ScopeProduct(PAN, "pan", "Pan"), ScopeProduct(RAKE, "rake", "Rake"))
    assert spend_rule.as_input() == spend
    assert (detail.state, detail.version, detail.ended_early_at) == ("draft", 1, None)
    assert (detail.starts_at, detail.ends_at) == WINDOWS["scheduled"]
    assert detail.copy == CopyInput(title_en="Raya Sale")
    assert detail.poster_desktop_url == "/api/promotion-posters/" + "a" * 32 + ".webp"
    assert detail.poster_phone_url is None
    assert replace(created, poster_desktop_url=detail.poster_desktop_url) == detail


def test_read_reports_early_end_in_local_time(db: Session, admin_id: int) -> None:
    """验收「ended_early_at（提前结束的马来西亚本地时刻或 None）」与第 2 条「提前结束的
    时刻取服务端当前时刻」：提前结束后状态为 ended，结束时间仍是原来的结束时间。"""
    detail = _make(db, admin_id, "running")
    _publish(db, detail.id, ended_early_at=datetime(2026, 11, 12, 1, 30, 45))

    reread = read_promotion(db, detail.id, NOW)

    assert (reread.state, reread.ended_early_at, reread.ends_at) == (
        "ended",
        "2026-11-12T09:30",
        detail.ends_at,
    )

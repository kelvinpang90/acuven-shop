"""节日促销活动的读取与规则换算（SHOP-TASK-233）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1、3、4、8 条
（下称「第 N 条」），以及 SHOP-TASK-233 写定的落实写法（下称「落实写法」）。
每条测试的文档字符串引用它守住的那一句。

用 SQLite 内存库（打开外键检查）按模型建表，写法照
tests/test_promotion_models.py。SQLite 不支持 FOR UPDATE，share_lock 在这里
只核对读出的结果不变；共享锁的效果由 MySQL 上的下单任务守住。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Product,
    Promotion,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
from app.services.promotion_pricing import (
    BuyXGetYRule,
    FreeShippingRule,
    ItemDiscountRule,
    SpendRule,
)
from app.services.promotions import (
    LoadedPromotion,
    PromoInputLine,
    active_promotion,
    load_promotion,
    price_lines,
    rule_for_product,
)

STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _add(db: Session, row: object) -> object:
    db.add(row)
    db.flush()
    return row


def _promotion(db: Session, **fields: object) -> Promotion:
    values = {
        "name": "Sale",
        "status": "published",
        "starts_at": STARTS,
        "ends_at": ENDS,
        **fields,
    }
    return _add(db, Promotion(**values))


def _category(db: Session, slug: str) -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=True))


def _product(db: Session, category: Category, slug: str) -> Product:
    return _add(db, Product(category_id=category.id, slug=slug, name_en=slug, is_active=True))


def _rule(
    db: Session,
    promotion: Promotion,
    position: int,
    *,
    products: tuple[Product, ...] = (),
    categories: tuple[Category, ...] = (),
    **params: object,
) -> PromotionRule:
    rule = _add(db, PromotionRule(promotion_id=promotion.id, position=position, **params))
    for product in products:
        _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))
    for category in categories:
        _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))
    return rule


def _tier(
    db: Session, rule: PromotionRule, position: int, threshold_sen: int, **value: int
) -> PromotionSpendTier:
    return _add(
        db,
        PromotionSpendTier(
            rule_id=rule.id, position=position, threshold_sen=threshold_sen, **value
        ),
    )


def _item_percent(db: Session, promotion: Promotion, position: int, **scope) -> PromotionRule:
    return _rule(
        db,
        promotion,
        position,
        kind="item_discount",
        discount_mode="percent",
        percent=20,
        **scope,
    )


# ---- 生效判定（第 1 条） ----


@pytest.mark.parametrize(
    ("now", "active"),
    [
        pytest.param(STARTS - timedelta(seconds=1), False, id="before_start"),
        pytest.param(STARTS, True, id="at_start"),
        pytest.param(ENDS - timedelta(seconds=1), True, id="just_before_end"),
        pytest.param(ENDS, False, id="at_end"),
    ],
)
def test_active_window_includes_start_and_excludes_end(
    db: Session, now: datetime, active: bool
) -> None:
    """第 1 条「活动在已发布、开始时间 ≤ 当前时刻、当前时刻 < 实际结束时间…时生效」：
    开始时刻即生效（未开始的不生效），结束时刻不再生效。
    """
    promotion = _promotion(db)

    loaded = active_promotion(db, now)

    if active:
        assert loaded is not None
        assert loaded.promotion_id == promotion.id
        assert loaded.effective_end == ENDS
    else:
        assert loaded is None


def test_ended_early_stops_at_the_early_end(db: Session) -> None:
    """第 1 条「实际结束时间（提前结束时为提前结束的时刻，否则为结束时间）」：
    提前结束的时刻不再生效，之前一秒仍生效，effective_end 为提前结束的时刻。
    """
    early = STARTS + timedelta(hours=6)
    _promotion(db, ended_early_at=early)

    assert active_promotion(db, early) is None
    before = active_promotion(db, early - timedelta(seconds=1))
    assert before is not None
    assert before.effective_end == early


def test_draft_is_never_active(db: Session) -> None:
    """第 1 条「活动在已发布…时生效」：时间覆盖当前时刻的草稿不生效。"""
    _promotion(db, status="draft")

    assert active_promotion(db, STARTS + timedelta(hours=1)) is None


def test_no_promotion_returns_none(db: Session) -> None:
    """第 1 条「每次计价、下单、展示都从数据库读取当时生效的活动」：库里没有活动时返回 None。"""
    assert active_promotion(db, STARTS) is None


def test_read_is_not_cached(db: Session) -> None:
    """第 1 条「从数据库读取当时生效的活动，不缓存」：同一时刻两次读取之间撤回为草稿，
    第二次即读不到。
    """
    promotion = _promotion(db)
    now = STARTS + timedelta(hours=1)
    assert active_promotion(db, now) is not None

    db.execute(update(Promotion).where(Promotion.id == promotion.id).values(status="draft"))

    assert active_promotion(db, now) is None


def test_aware_now_is_rejected(db: Session) -> None:
    """第 1 条「存为不带时区的 UTC」与落实写法「带时区抛 ValueError」。"""
    with pytest.raises(ValueError):
        active_promotion(db, STARTS.replace(tzinfo=UTC))


def test_share_lock_reads_the_same_promotion(db: Session) -> None:
    """落实写法「share_lock 为 True 时以共享锁读活动行」：加锁的读取与不加锁的读出同一活动。"""
    promotion = _promotion(db)
    now = STARTS + timedelta(hours=1)

    loaded = active_promotion(db, now, share_lock=True)

    assert loaded is not None
    assert loaded == active_promotion(db, now)
    assert loaded.promotion_id == promotion.id


def test_overlap_takes_earliest_start_and_logs(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """第 1 条「同一时刻至多一个活动生效」与落实写法「读到多于一个时取开始时间最早、
    同时取 ID 最小的一个，并记一条 warning 日志（键 promotion_overlap，只带活动 ID）」。
    """
    # 开始最晚的 ID 最小；开始最早的两个之间取 ID 较小者。
    later = _promotion(db, name="Later", starts_at=STARTS + timedelta(hours=2))
    earliest = _promotion(db, name="Earliest")
    tied = _promotion(db, name="Tied")
    now = STARTS + timedelta(hours=3)

    with caplog.at_level(logging.WARNING, logger="app.services.promotions"):
        loaded = active_promotion(db, now)

    assert loaded is not None
    assert loaded.promotion_id == earliest.id
    records = [r for r in caplog.records if r.name == "app.services.promotions"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert message == f"promotion_overlap promotion_ids={earliest.id},{tied.id},{later.id}"


def test_single_active_promotion_does_not_log(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """落实写法「读到多于一个时（数据异常）…记一条 warning 日志」：只有一个时不记。"""
    _promotion(db)

    with caplog.at_level(logging.WARNING, logger="app.services.promotions"):
        assert active_promotion(db, STARTS) is not None

    assert not [r for r in caplog.records if r.name == "app.services.promotions"]


# ---- load_promotion 与换算（第 2、3 条） ----


def test_load_promotion_reads_a_draft_without_times(db: Session) -> None:
    """落实写法「load_promotion 按 ID 读出任一状态的活动」（供第 11 条预览）：
    没有时间的草稿读得到，starts_at 与 effective_end 为 None；不存在的 ID 返回 None。
    """
    draft = _promotion(
        db,
        status="draft",
        starts_at=None,
        ends_at=None,
        coupon_combinable=True,
        points_combinable=False,
    )
    _rule(db, draft, 1, kind="free_shipping", threshold_sen=0)

    loaded = load_promotion(db, draft.id)

    assert loaded == LoadedPromotion(
        promotion_id=draft.id,
        version=1,
        status="draft",
        starts_at=None,
        effective_end=None,
        coupon_combinable=True,
        points_combinable=False,
        item_rules=(),
        spend_rule=None,
        free_shipping_rule=FreeShippingRule(0),
    )
    assert load_promotion(db, draft.id + 1) is None


def test_item_rules_are_converted_and_sorted_by_position(db: Session) -> None:
    """第 3 条「规则在活动内有排序」：商品级规则按 position 升序（与写入顺序无关），
    打折与买 X 享 Y 换算成对应规则对象，范围为 frozenset，结果不可改。
    """
    promotion = _promotion(db)
    home = _category(db, "home")
    mug = _product(db, home, "mug")
    bxgy = _rule(
        db,
        promotion,
        2,
        kind="buy_x_get_y",
        buy_quantity=2,
        get_quantity=1,
        get_percent=100,
        categories=(home,),
    )
    direct = _rule(
        db,
        promotion,
        1,
        kind="item_discount",
        discount_mode="amount",
        amount_sen=300,
        products=(mug,),
    )

    loaded = load_promotion(db, promotion.id)

    assert [r.position for r in loaded.item_rules] == [1, 2]
    first, second = loaded.item_rules
    assert first.rule_id == direct.id
    assert first.rule == ItemDiscountRule(direct.id, None, 300)
    assert first.product_ids == frozenset({mug.id})
    assert first.category_ids == frozenset()
    assert isinstance(first.product_ids, frozenset)
    assert second.rule == BuyXGetYRule(bxgy.id, 2, 1, 100)
    assert second.category_ids == frozenset({home.id})
    with pytest.raises(FrozenInstanceError):
        first.position = 3  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        loaded.version = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mode", "column", "values"),
    [
        pytest.param("amount", "amount_sen", (500, 1200), id="amount"),
        pytest.param("percent", "percent", (5, 10), id="percent"),
    ],
)
def test_spend_tiers_are_sorted_by_position(
    db: Session, mode: str, column: str, values: tuple[int, int]
) -> None:
    """第 3 条「满额减或满额折…1–5 档…全部档同一种方式」：各档按 position 升序
    （与写入顺序无关），模式取规则的 discount_mode，值取该方式的列。
    """
    promotion = _promotion(db)
    rule = _rule(db, promotion, 1, kind="spend_discount", discount_mode=mode)
    _tier(db, rule, 2, 10000, **{column: values[1]})
    _tier(db, rule, 1, 5000, **{column: values[0]})

    loaded = load_promotion(db, promotion.id)

    assert loaded.spend_rule == SpendRule(mode, ((5000, values[0]), (10000, values[1])))
    assert loaded.item_rules == ()
    assert loaded.free_shipping_rule is None


def test_rules_scopes_and_tiers_take_one_query_each(db: Session) -> None:
    """落实写法「规则、范围与满额档各用一次查询取出（不按规则逐条查询）」：
    三条商品级规则加一条满额规则，读一次活动共 5 条 SELECT（活动、规则、
    商品范围、分类范围、满额档）。
    """
    promotion = _promotion(db)
    home = _category(db, "home")
    products = [_product(db, home, f"p{i}") for i in range(3)]
    for position, product in enumerate(products, start=1):
        _item_percent(db, promotion, position, products=(product,), categories=(home,))
    spend = _rule(db, promotion, 4, kind="spend_discount", discount_mode="amount")
    _tier(db, spend, 1, 5000, amount_sen=500)
    promotion_id = promotion.id
    db.commit()

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        loaded = load_promotion(db, promotion_id)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(loaded.item_rules) == 3
    assert len(statements) == 5
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


# ---- 命中规则（第 3 条） ----


def test_rule_for_product_by_product_and_by_category(db: Session) -> None:
    """第 3 条「适用范围为若干商品与若干分类」：按商品 ID 命中、按分类命中，
    都不在范围里返回 None。
    """
    promotion = _promotion(db)
    gifts = _category(db, "gifts")
    home = _category(db, "home")
    other = _category(db, "other")
    a = _product(db, gifts, "a")
    b = _product(db, home, "b")
    d = _product(db, other, "d")
    by_product = _item_percent(db, promotion, 1, products=(a,))
    by_category = _item_percent(db, promotion, 2, categories=(home,))

    loaded = load_promotion(db, promotion.id)

    assert rule_for_product(loaded, a.id, a.category_id) == by_product.id
    assert rule_for_product(loaded, b.id, b.category_id) == by_category.id
    assert rule_for_product(loaded, d.id, d.category_id) is None


def test_rule_for_product_takes_smallest_position(db: Session) -> None:
    """第 3 条末段「之后商品改了分类而造成重叠的，计价时取排序数较小的规则」：
    商品同时在排序 2 的商品范围与排序 1 的分类范围里，取排序 1（规则 ID 较大）。
    """
    promotion = _promotion(db)
    home = _category(db, "home")
    mug = _product(db, home, "mug")
    _item_percent(db, promotion, 2, products=(mug,))
    first = _item_percent(db, promotion, 1, categories=(home,))

    loaded = load_promotion(db, promotion.id)

    assert rule_for_product(loaded, mug.id, mug.category_id) == first.id


def test_rule_follows_the_products_current_category(db: Session) -> None:
    """第 3 条「分类按商品当时所属分类判断」：商品改分类后，按新分类命中规则。"""
    promotion = _promotion(db)
    home = _category(db, "home")
    kitchen = _category(db, "kitchen")
    mug = _product(db, home, "mug")
    kitchen_rule = _item_percent(db, promotion, 1, categories=(kitchen,))
    home_rule = _item_percent(db, promotion, 2, categories=(home,))
    loaded = load_promotion(db, promotion.id)
    assert rule_for_product(loaded, mug.id, mug.category_id) == home_rule.id

    db.execute(update(Product).where(Product.id == mug.id).values(category_id=kitchen.id))
    db.refresh(mug)

    assert rule_for_product(loaded, mug.id, mug.category_id) == kitchen_rule.id


# ---- 计价（第 4、8 条） ----


def _section_8_promotion(db: Session) -> tuple[Promotion, dict[str, Product]]:
    """第 8 条的活动：A 打 8 折；分类「家居」（含 B、C）买 2 享 1、100%；
    满 RM50 减 RM5、满 RM100 减 RM12；满 RM60 免运费；两个同用开关都打开。
    """
    promotion = _promotion(db, coupon_combinable=True, points_combinable=True)
    gifts = _category(db, "gifts")
    home = _category(db, "home")
    products = {
        "A": _product(db, gifts, "a"),
        "B": _product(db, home, "b"),
        "C": _product(db, home, "c"),
    }
    _item_percent(db, promotion, 1, products=(products["A"],))
    _rule(
        db,
        promotion,
        2,
        kind="buy_x_get_y",
        buy_quantity=2,
        get_quantity=1,
        get_percent=100,
        categories=(home,),
    )
    spend = _rule(db, promotion, 3, kind="spend_discount", discount_mode="amount")
    _tier(db, spend, 1, 5000, amount_sen=500)
    _tier(db, spend, 2, 10000, amount_sen=1200)
    _rule(db, promotion, 4, kind="free_shipping", threshold_sen=6000)
    return promotion, products


def _section_8_lines(products: dict[str, Product]) -> list[PromoInputLine]:
    return [
        PromoInputLine(0, 2500, 2, products["A"].id, products["A"].category_id),
        PromoInputLine(1, 1200, 2, products["B"].id, products["B"].category_id),
        PromoInputLine(2, 900, 1, products["C"].id, products["C"].category_id),
    ]


def test_price_lines_reproduces_section_8(db: Session) -> None:
    """第 8 条算例（一）至（三）：每件活动优惠依次为 656、656、94、94、900 仙，
    合计 RM24.00；S1 = RM64.00；运费 RM8.00 全免（同 SHOP-TASK-231 的测试）。
    """
    promotion, products = _section_8_promotion(db)
    loaded = active_promotion(db, STARTS + timedelta(hours=1))
    assert loaded is not None and loaded.promotion_id == promotion.id
    assert loaded.coupon_combinable and loaded.points_combinable

    pricing = price_lines(loaded, _section_8_lines(products), shipping_fee_sen=800)

    assert [u.promotion_discount for u in pricing.units] == [656, 656, 94, 94, 900]
    assert [u.item_discount for u in pricing.units] == [500, 500, 0, 0, 900]
    assert [u.order_discount for u in pricing.units] == [156, 156, 94, 94, 0]
    first, second = loaded.item_rules
    assert [u.rule_id for u in pricing.units] == [first.rule_id] * 2 + [second.rule_id] * 3
    assert pricing.subtotal == 8300
    assert pricing.after_item_subtotal == 6400
    assert pricing.order_discount_total == 500
    assert pricing.spend_tier_reached == 1
    assert pricing.spend_next_gap_sen == 3600
    assert pricing.free_shipping_reached is True
    assert pricing.shipping_actual_sen == 0
    assert pricing.shipping_waived_sen == 800
    assert pricing.participates is True


def test_price_lines_without_shipping_quote_does_not_decide(db: Session) -> None:
    """第 4 条「还没有运费报价时…免运费只给出是否达到门槛，不判定是否参加活动」。"""
    _, products = _section_8_promotion(db)
    loaded = active_promotion(db, STARTS)

    pricing = price_lines(loaded, _section_8_lines(products), shipping_fee_sen=None)

    assert [u.promotion_discount for u in pricing.units] == [656, 656, 94, 94, 900]
    assert pricing.free_shipping_reached is True
    assert pricing.shipping_waived_sen is None
    assert pricing.participates is None


def test_price_lines_rejects_other_line_types(db: Session) -> None:
    """第 4 条「只算计价结果里的正常行」与落实写法「接收 PromoInputLine 的序列」：
    别的类型抛 TypeError，line_index 不严格递增照 price_promotion 抛 ValueError。
    """
    _, products = _section_8_promotion(db)
    loaded = active_promotion(db, STARTS)
    lines = _section_8_lines(products)

    with pytest.raises(TypeError):
        price_lines(loaded, [object()], shipping_fee_sen=800)  # type: ignore[list-item]
    with pytest.raises(ValueError):
        price_lines(loaded, [lines[1], lines[0]], shipping_fee_sen=800)

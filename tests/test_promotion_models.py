"""节日促销活动的七张表与迁移 0027 的常量（SHOP-TASK-229）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「数据模型」的 Promotion /
PromotionRule / PromotionDecoration 一行与「节日促销活动」第 1–3、12 条
（下称「第 N 条」），以及 SHOP-TASK-229 写定的表名、列名、类型与约束
（下称「落实写法」）。每条测试的文档字符串引用它守住的那一句。

用 SQLite 内存库（打开外键检查）按模型建表，写法照
tests/test_store_logo_schema.py。检查约束的反例每次只让目标约束不成立，
断言报出的是该约束的名称。迁移 0027 只在 MySQL 上执行，这里不在 SQLite 上
执行它，只经 importlib 载入、核对 revision 与约束常量；往返由 CI 的
backend 检查在 MySQL 8 上做。
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import CheckConstraint, Table, create_engine, delete, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Product,
    Promotion,
    PromotionDecoration,
    PromotionPublishLock,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
    StoreDesignSetting,
)
from app.models.promotion import (
    AMBIENT_NONE,
    DECORATION_CHOICES,
    PALETTE_KEEP_THEME,
    PROMOTION_PACKS,
    PROMOTION_SLOTS,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "20261110_0027_promotions.py"
TOKENS_PATH = ROOT / "docs" / "design" / "tokens" / "promotions.json"
TOKENS = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))

MODELS = (
    Promotion,
    PromotionRule,
    PromotionRuleProduct,
    PromotionRuleCategory,
    PromotionSpendTier,
    PromotionDecoration,
    PromotionPublishLock,
)

STARTS = datetime(2026, 11, 11, 0, 0)
ENDS = datetime(2026, 11, 12, 0, 0)

# 每类规则的一组合格参数（第 3 条）。
ITEM_PERCENT = {"kind": "item_discount", "discount_mode": "percent", "percent": 20}
ITEM_AMOUNT = {"kind": "item_discount", "discount_mode": "amount", "amount_sen": 500}
BUY_X_GET_Y = {"kind": "buy_x_get_y", "buy_quantity": 2, "get_quantity": 1, "get_percent": 100}
SPEND_DISCOUNT = {"kind": "spend_discount", "discount_mode": "amount"}
FREE_SHIPPING = {"kind": "free_shipping", "threshold_sen": 6000}
RULE_PARAM_COLUMNS = (
    "discount_mode",
    "percent",
    "amount_sen",
    "buy_quantity",
    "get_quantity",
    "get_percent",
    "threshold_sen",
)

# 一组合格的装饰 id（第 12 条）。
DECORATION = {
    "pack": "cny",
    "palette": "theme",
    "ambient": "none",
    "home_poster": "split",
    "list_strip": "band",
    "card_badge": "tag",
    "detail_ribbon": "ribbon",
    "cart_progress": "bar",
}
POSTER_PREFIXES = ("poster_desktop_", "poster_phone_")
COPY_COLUMNS = (
    ("poster_alt", 150),
    ("title", 40),
    ("subtitle", 80),
    ("badge", 12),
    ("strip", 60),
)
LANGUAGES = ("en", "zh", "ms")


def _load_migration() -> ModuleType:
    # 文件名以数字开头，不能直接 import。
    spec = importlib.util.spec_from_file_location("promotions_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


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
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        yield session
    engine.dispose()


def _add(db: Session, row: object) -> object:
    db.add(row)
    db.flush()
    return row


def _check_failed(name: str) -> AbstractContextManager[object]:
    # 名称后紧跟换行：断言报出的正是这条约束，不是以它为前缀的别的约束。
    return pytest.raises(IntegrityError, match=rf"CHECK constraint failed: {re.escape(name)}\n")


def _promotion(db: Session, **fields: object) -> Promotion:
    values = {"name": "11.11 sale", "status": "draft"}
    return _add(db, Promotion(**(values | fields)))


def _rule(db: Session, params: dict[str, object] | None = None, **fields: object) -> PromotionRule:
    # 不给参数时是一条满额免运费规则。
    values = {"position": 1} | (FREE_SHIPPING if params is None else params) | fields
    if "promotion_id" not in values:
        values["promotion_id"] = _promotion(db).id
    return _add(db, PromotionRule(**values))


def _tier(db: Session, **fields: object) -> PromotionSpendTier:
    values = {"position": 1, "threshold_sen": 5000, "amount_sen": 500} | fields
    if "rule_id" not in values:
        values["rule_id"] = _rule(db, SPEND_DISCOUNT).id
    return _add(db, PromotionSpendTier(**values))


def _poster(prefix: str) -> dict[str, object]:
    return {
        f"{prefix}file_name": "0123456789abcdef0123456789abcdef",
        f"{prefix}source_format": "jpeg",
        f"{prefix}width": 1600,
        f"{prefix}height": 600,
        f"{prefix}byte_size": 180_000,
    }


def _decoration(db: Session, **fields: object) -> PromotionDecoration:
    values = DECORATION | fields
    if "promotion_id" not in values:
        values["promotion_id"] = _promotion(db).id
    return _add(db, PromotionDecoration(**values))


def _category(db: Session, slug: str = "home") -> Category:
    return _add(db, Category(slug=slug, name_en=slug.title(), is_active=True))


def _product(db: Session, category: Category) -> Product:
    return _add(
        db,
        Product(category_id=category.id, slug="mug", name_en="Mug", is_active=True),
    )


def _count(db: Session, model: type[Base]) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _model_checks(table: Table) -> dict[str, str]:
    """表上检查约束的完整名称（按 app/db/base.py 的命名约定）→ 文本。"""
    prefix = f"ck_{table.name}_"
    checks = {}
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            name = str(constraint.name)
            checks[name if name.startswith(prefix) else prefix + name] = str(constraint.sqltext)
    return checks


# ---- promotions ----


def test_promotion_python_defaults(db: Session) -> None:
    """第 1 条「两个同用开关（第 6 条，默认都关闭）」与第 2 条
    「有改动即版本号加一」（新建为 1）：只填名称与状态的活动，两个开关为假、
    版本号为 1、创建与更新时间已填。
    """
    promotion = _promotion(db)

    assert (promotion.coupon_combinable, promotion.points_combinable) == (False, False)
    assert promotion.version == 1
    assert promotion.created_at is not None
    assert promotion.updated_at is not None


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="draft-without-times"),
        pytest.param({"starts_at": STARTS}, id="draft-only-start"),
        pytest.param({"ends_at": ENDS}, id="draft-only-end"),
        pytest.param({"starts_at": STARTS, "ends_at": ENDS}, id="draft-with-times"),
        pytest.param(
            {"status": "published", "starts_at": STARTS, "ends_at": ENDS},
            id="published",
        ),
        pytest.param({"name": "x"}, id="name-1"),
        pytest.param({"name": "x" * 100}, id="name-100"),
    ],
)
def test_promotion_is_accepted(db: Session, fields: dict[str, object]) -> None:
    """docs/UX.md 0.20 的 A12「草稿可以不填时间，发布时开始与结束都须填」
    与第 1 条「内部名称（…1–100 个字符）」：草稿的时间可空或只填一项，
    已发布的两项都填，名称 1 与 100 个字符都能写入。
    """
    promotion = _promotion(db, **fields)

    assert db.get(Promotion, promotion.id) is promotion


def test_promotion_name_empty_is_rejected(db: Session) -> None:
    """第 1 条「内部名称（…1–100 个字符）」与落实写法「name 非空」：
    空串被 name_not_empty 拒绝。
    """
    with _check_failed("ck_promotions_name_not_empty"):
        _promotion(db, name="")


def test_promotion_unknown_status_is_rejected(db: Session) -> None:
    """第 2 条「活动只有『草稿』与『已发布』两种状态」：
    其他状态被 status_valid 拒绝。
    """
    with _check_failed("ck_promotions_status_valid"):
        _promotion(db, status="ended")


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="no-times"),
        pytest.param({"starts_at": STARTS}, id="only-start"),
        pytest.param({"ends_at": ENDS}, id="only-end"),
    ],
)
def test_published_without_both_times_is_rejected(db: Session, fields: dict[str, object]) -> None:
    """UX 0.20 A12「发布时开始与结束都须填」：已发布而缺开始或结束时间
    被 published_has_times 拒绝。
    """
    with _check_failed("ck_promotions_published_has_times"):
        _promotion(db, status="published", **fields)


@pytest.mark.parametrize(
    "ends_at",
    [
        pytest.param(STARTS, id="equal"),
        pytest.param(STARTS - timedelta(minutes=1), id="before"),
    ],
)
def test_promotion_ends_not_after_starts_is_rejected(db: Session, ends_at: datetime) -> None:
    """第 1 条「结束须晚于开始」：结束等于或早于开始被 ends_after_starts
    拒绝（草稿也一样）。
    """
    with _check_failed("ck_promotions_ends_after_starts"):
        _promotion(db, starts_at=STARTS, ends_at=ends_at)


@pytest.mark.parametrize(
    "ended_early_at",
    [
        pytest.param(STARTS, id="at-start"),
        pytest.param(ENDS - timedelta(seconds=1), id="last-second"),
    ],
)
def test_ended_early_within_the_run_is_accepted(db: Session, ended_early_at: datetime) -> None:
    """第 2 条「已开始…只能在进行中『提前结束』（提前结束的时刻取服务端
    当前时刻…）」：已发布活动的提前结束时刻为开始时刻或结束前一秒都能写入。
    """
    promotion = _promotion(
        db,
        status="published",
        starts_at=STARTS,
        ends_at=ENDS,
        ended_early_at=ended_early_at,
    )

    assert promotion.ended_early_at == ended_early_at


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"status": "draft", "ended_early_at": STARTS + timedelta(hours=1)},
            id="draft",
        ),
        pytest.param({"status": "published", "ended_early_at": ENDS}, id="at-end"),
        pytest.param(
            {"status": "published", "ended_early_at": STARTS - timedelta(seconds=1)},
            id="before-start",
        ),
    ],
)
def test_ended_early_outside_the_run_is_rejected(db: Session, fields: dict[str, object]) -> None:
    """第 2 条「只能在进行中『提前结束』」（进行中即已发布、开始 ≤ 当前 < 结束）
    与落实写法「提前结束的时刻由之后的服务层截到整秒再写入」：草稿带提前
    结束时刻、提前结束于结束时刻或开始之前，都被 ended_early_within 拒绝。
    """
    with _check_failed("ck_promotions_ended_early_within"):
        _promotion(db, starts_at=STARTS, ends_at=ENDS, **fields)


def test_promotion_version_zero_is_rejected(db: Session) -> None:
    """第 2 条「读取时带版本号…有改动即版本号加一」（从 1 起）：
    版本号 0 被 version_positive 拒绝。
    """
    with _check_failed("ck_promotions_version_positive"):
        _promotion(db, version=0)


# ---- promotion_rules ----


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(ITEM_PERCENT | {"percent": 1}, id="item-percent-1"),
        pytest.param(ITEM_PERCENT | {"percent": 90}, id="item-percent-90"),
        pytest.param(ITEM_AMOUNT | {"amount_sen": 1}, id="item-amount-1"),
        pytest.param(ITEM_AMOUNT | {"amount_sen": 50_000_000}, id="item-amount-max"),
        pytest.param(
            BUY_X_GET_Y | {"buy_quantity": 1, "get_quantity": 1, "get_percent": 1},
            id="bxgy-min",
        ),
        pytest.param(
            BUY_X_GET_Y | {"buy_quantity": 10, "get_quantity": 10, "get_percent": 100},
            id="bxgy-max",
        ),
        pytest.param(SPEND_DISCOUNT, id="spend-amount"),
        pytest.param(SPEND_DISCOUNT | {"discount_mode": "percent"}, id="spend-percent"),
        pytest.param(FREE_SHIPPING | {"threshold_sen": 0}, id="free-shipping-0"),
        pytest.param(FREE_SHIPPING | {"threshold_sen": 99_999_999}, id="free-shipping-max"),
    ],
)
def test_rule_params_are_accepted(db: Session, params: dict[str, object]) -> None:
    """第 3 条各类参数的边界：商品级打折百分比 1–90、每件直降
    1–50,000,000 仙；买 X 享 Y 的 X、Y 为 1–10、享受的折扣 1–100；
    满额减或满额折为减固定金额或打百分比；免运费门槛 0–99,999,999 仙。
    边界值能写入并读回，未用的参数列为空。
    """
    rule = _rule(db, params)

    stored = {column: getattr(rule, column) for column in RULE_PARAM_COLUMNS}
    assert stored == {column: params.get(column) for column in RULE_PARAM_COLUMNS}


def test_rule_position_zero_is_rejected(db: Session) -> None:
    """第 3 条「规则在活动内有排序，取值为从 1 起连续的整数」：
    排序 0 被 position_positive 拒绝。
    """
    with _check_failed("ck_promotion_rules_position_positive"):
        _rule(db, position=0)


def test_rule_unknown_kind_is_rejected(db: Session) -> None:
    """第 3 条「每条规则属于一类」（四类之一）：未知类别被 kind_valid 拒绝，
    不触发各类的参数约束。
    """
    with _check_failed("ck_promotion_rules_kind_valid"):
        _rule(db, {"kind": "bundle"})


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(ITEM_PERCENT | {"percent": 0}, id="percent-0"),
        pytest.param(ITEM_PERCENT | {"percent": 91}, id="percent-91"),
        pytest.param(ITEM_PERCENT | {"percent": None}, id="percent-missing"),
        pytest.param(ITEM_PERCENT | {"amount_sen": 500}, id="percent-with-amount"),
        pytest.param(ITEM_AMOUNT | {"amount_sen": 0}, id="amount-0"),
        pytest.param(ITEM_AMOUNT | {"amount_sen": 50_000_001}, id="amount-over"),
        pytest.param(ITEM_AMOUNT | {"amount_sen": None}, id="amount-missing"),
        pytest.param(ITEM_AMOUNT | {"percent": 20}, id="amount-with-percent"),
        pytest.param(ITEM_PERCENT | {"discount_mode": None}, id="mode-missing"),
        pytest.param(ITEM_PERCENT | {"discount_mode": "fixed"}, id="mode-unknown"),
        pytest.param(ITEM_PERCENT | {"buy_quantity": 2}, id="with-buy-quantity"),
        pytest.param(ITEM_PERCENT | {"get_quantity": 1}, id="with-get-quantity"),
        pytest.param(ITEM_PERCENT | {"get_percent": 100}, id="with-get-percent"),
        pytest.param(ITEM_PERCENT | {"threshold_sen": 0}, id="with-threshold"),
    ],
)
def test_item_discount_bad_params_are_rejected(db: Session, params: dict[str, object]) -> None:
    """第 3 条「商品级打折…方式为百分比（1–90 的整数…）或每件直降
    （1–50,000,000 仙…）」与落实写法「其余参数列为空」：越界、缺值、
    两种方式同填、方式为空或未知、填了别类的参数，都被
    item_discount_params 拒绝。
    """
    with _check_failed("ck_promotion_rules_item_discount_params"):
        _rule(db, params)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"buy_quantity": 0}, id="buy-0"),
        pytest.param({"buy_quantity": 11}, id="buy-11"),
        pytest.param({"buy_quantity": None}, id="buy-missing"),
        pytest.param({"get_quantity": 0}, id="get-0"),
        pytest.param({"get_quantity": 11}, id="get-11"),
        pytest.param({"get_quantity": None}, id="get-missing"),
        pytest.param({"get_percent": 0}, id="percent-0"),
        pytest.param({"get_percent": 101}, id="percent-101"),
        pytest.param({"get_percent": None}, id="percent-missing"),
        pytest.param({"discount_mode": "percent"}, id="with-mode"),
        pytest.param({"percent": 20}, id="with-item-percent"),
        pytest.param({"amount_sen": 500}, id="with-amount"),
        pytest.param({"threshold_sen": 0}, id="with-threshold"),
    ],
)
def test_buy_x_get_y_bad_params_are_rejected(db: Session, params: dict[str, object]) -> None:
    """第 3 条「买 X 享 Y…X 为 1–10、Y 为 1–10 的整数，享受的折扣为 1–100
    的整数百分比」与落实写法「其余参数列与 discount_mode 为空」：越界、
    缺值或填了别类的参数被 buy_x_get_y_params 拒绝。
    """
    with _check_failed("ck_promotion_rules_buy_x_get_y_params"):
        _rule(db, BUY_X_GET_Y | params)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"discount_mode": None}, id="mode-missing"),
        pytest.param({"discount_mode": "fixed"}, id="mode-unknown"),
        pytest.param({"percent": 10}, id="with-percent"),
        pytest.param({"amount_sen": 500}, id="with-amount"),
        pytest.param({"buy_quantity": 2}, id="with-buy-quantity"),
        pytest.param({"get_quantity": 1}, id="with-get-quantity"),
        pytest.param({"get_percent": 100}, id="with-get-percent"),
        pytest.param({"threshold_sen": 5000}, id="with-threshold"),
    ],
)
def test_spend_discount_bad_params_are_rejected(db: Session, params: dict[str, object]) -> None:
    """第 3 条「满额减或满额折…全部档同一种方式：每档减固定金额…或打百分比」
    与落实写法「各档在满额档表」：方式为空或未知、规则行上填了金额、
    百分比或别类的参数，都被 spend_discount_params 拒绝。
    """
    with _check_failed("ck_promotion_rules_spend_discount_params"):
        _rule(db, SPEND_DISCOUNT | params)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"threshold_sen": None}, id="threshold-missing"),
        pytest.param({"threshold_sen": -1}, id="threshold-negative"),
        pytest.param({"threshold_sen": 100_000_000}, id="threshold-over"),
        pytest.param({"discount_mode": "amount"}, id="with-mode"),
        pytest.param({"percent": 10}, id="with-percent"),
        pytest.param({"amount_sen": 500}, id="with-amount"),
        pytest.param({"buy_quantity": 2}, id="with-buy-quantity"),
        pytest.param({"get_quantity": 1}, id="with-get-quantity"),
        pytest.param({"get_percent": 100}, id="with-get-percent"),
    ],
)
def test_free_shipping_bad_params_are_rejected(db: Session, params: dict[str, object]) -> None:
    """第 3 条「满额免运费…一个门槛（0–99,999,999 仙，0 即本活动期间全部
    订单免运费）」与落实写法「其余参数列与 discount_mode 为空」：缺门槛、
    越界或填了别类的参数被 free_shipping_params 拒绝。
    """
    with _check_failed("ck_promotion_rules_free_shipping_params"):
        _rule(db, FREE_SHIPPING | params)


def test_duplicate_rule_position_is_rejected(db: Session) -> None:
    """第 3 条「规则在活动内有排序…不重复」：同一活动两条规则同在排序 1
    被联合唯一约束拒绝；另一个活动的排序 1 不受影响。
    """
    first = _rule(db)
    _rule(db)

    with pytest.raises(
        IntegrityError,
        match="UNIQUE constraint failed: promotion_rules.promotion_id, promotion_rules.position",
    ):
        _rule(db, ITEM_PERCENT, promotion_id=first.promotion_id)


# ---- promotion_rule_products / promotion_rule_categories ----


def test_duplicate_rule_product_is_rejected(db: Session) -> None:
    """第 3 条「适用范围为若干商品与若干分类」：同一规则重复列同一件商品
    被联合唯一约束拒绝。
    """
    rule = _rule(db, ITEM_PERCENT)
    product = _product(db, _category(db))
    _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))

    with pytest.raises(
        IntegrityError,
        match=(
            "UNIQUE constraint failed: promotion_rule_products.rule_id,"
            " promotion_rule_products.product_id"
        ),
    ):
        _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))


def test_duplicate_rule_category_is_rejected(db: Session) -> None:
    """第 3 条「适用范围为若干商品与若干分类」：同一规则重复列同一个分类
    被联合唯一约束拒绝。
    """
    rule = _rule(db, ITEM_PERCENT)
    category = _category(db)
    _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))

    with pytest.raises(
        IntegrityError,
        match=(
            "UNIQUE constraint failed: promotion_rule_categories.rule_id,"
            " promotion_rule_categories.category_id"
        ),
    ):
        _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))


# ---- promotion_spend_tiers ----


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"position": 1, "threshold_sen": 1, "amount_sen": None, "percent": 1},
            id="min-percent",
        ),
        pytest.param(
            {"position": 5, "threshold_sen": 99_999_999, "amount_sen": None, "percent": 90},
            id="max-percent",
        ),
        pytest.param({"threshold_sen": 2, "amount_sen": 1}, id="amount-1"),
        pytest.param({"threshold_sen": 5000, "amount_sen": 4999}, id="amount-below-threshold"),
    ],
)
def test_spend_tier_is_accepted(db: Session, fields: dict[str, object]) -> None:
    """第 3 条「1–5 档，每档一个门槛（1–99,999,999 仙）与一个优惠…每档减
    固定金额（1 仙到小于门槛）或打百分比（1–90 的整数）」：边界值能写入。
    """
    tier = _tier(db, **fields)

    assert db.get(PromotionSpendTier, tier.id) is tier


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        pytest.param({"position": 0}, "position_range", id="position-0"),
        pytest.param({"position": 6}, "position_range", id="position-6"),
        pytest.param(
            {"threshold_sen": 0, "amount_sen": None, "percent": 10},
            "threshold_range",
            id="threshold-0",
        ),
        pytest.param(
            {"threshold_sen": 100_000_000, "amount_sen": None, "percent": 10},
            "threshold_range",
            id="threshold-over",
        ),
        pytest.param({"amount_sen": None}, "amount_or_percent", id="neither"),
        pytest.param({"percent": 10}, "amount_or_percent", id="both"),
        pytest.param({"amount_sen": 0}, "amount_below_threshold", id="amount-0"),
        pytest.param({"amount_sen": 5000}, "amount_below_threshold", id="amount-at-threshold"),
        pytest.param({"amount_sen": None, "percent": 0}, "percent_range", id="percent-0"),
        pytest.param({"amount_sen": None, "percent": 91}, "percent_range", id="percent-91"),
    ],
)
def test_spend_tier_bad_values_are_rejected(
    db: Session, fields: dict[str, object], constraint: str
) -> None:
    """第 3 条「1–5 档，每档一个门槛（1–99,999,999 仙）与一个优惠…每档减
    固定金额（1 仙到小于门槛）或打百分比（1–90 的整数）」与落实写法
    「恰有一个不为空」：位置、门槛越界，金额与百分比都空或都填，金额为 0
    或不小于门槛，百分比越界，各被对应的约束拒绝。
    """
    with _check_failed(f"ck_promotion_spend_tiers_{constraint}"):
        _tier(db, **fields)


def test_duplicate_spend_tier_position_is_rejected(db: Session) -> None:
    """第 3 条「只取达到的最高一档」（各档有确定的先后）与落实写法
    「position…与 rule_id 联合唯一」：同一规则两档同在位置 1 被拒绝。
    """
    first = _tier(db)

    with pytest.raises(
        IntegrityError,
        match=(
            "UNIQUE constraint failed: promotion_spend_tiers.rule_id,"
            " promotion_spend_tiers.position"
        ),
    ):
        _tier(db, rule_id=first.rule_id, threshold_sen=10_000, amount_sen=1200)


# ---- promotion_decorations ----


def _listed_ids() -> list[tuple[str, str]]:
    """promotions.json 里每个 ID 列可取的（列、值）。"""
    packs = [pack["id"] for pack in TOKENS["packs"]]
    pairs = [("pack", pack) for pack in packs]
    pairs += [("palette", palette) for palette in [TOKENS["keepThemePalette"], *packs]]
    for slot in TOKENS["slots"]:
        pairs += [(slot["id"], variant) for variant in slot["variants"]]
        if "none" in slot:
            pairs.append((slot["id"], slot["none"]))
    return pairs


@pytest.mark.parametrize(("column", "value"), _listed_ids())
def test_decoration_listed_id_is_accepted(db: Session, column: str, value: str) -> None:
    """第 12 条「装饰为一组从预置清单里选的 ID：素材包…、配色（保持店铺
    主题，或选一套节日配色）以及…各装饰位的变体」：promotions.json 清单里
    的每个 ID 都能写入对应列。
    """
    decoration = _decoration(db, **{column: value})

    assert getattr(decoration, column) == value


@pytest.mark.parametrize(
    ("column", "value"),
    [
        *[pytest.param(column, "festive", id=f"{column}-unknown") for column in DECORATION],
        pytest.param("pack", "theme", id="pack-theme"),
        pytest.param("pack", "none", id="pack-none"),
        pytest.param("palette", "none", id="palette-none"),
        pytest.param("home_poster", "none", id="home-poster-none"),
        pytest.param("card_badge", "garland", id="card-badge-other-slot"),
    ],
)
def test_decoration_unlisted_id_is_rejected(db: Session, column: str, value: str) -> None:
    """第 12 条「保存时任一 ID 不在清单里即回答『取值不合格』」：不在清单里
    的 ID、theme 与 none 用在不收它们的列、别的装饰位的变体，都被该列的
    _known 约束拒绝。
    """
    with _check_failed(f"ck_promotion_decorations_{column}_known"):
        _decoration(db, **{column: value})


def test_decoration_defaults_and_empty_copy(db: Session) -> None:
    """第 12 条「海报有两种模式：模板…与上传图」与落实写法「poster_mode…
    Python 默认 template」、海报图与文案均可空：只填 ID 的装饰行海报为
    模板模式，两张海报图的十列与十五个文案列全为空。
    """
    decoration = _decoration(db)

    assert decoration.poster_mode == "template"
    empty = [f"{prefix}{column}" for prefix in POSTER_PREFIXES for column in _poster("")]
    empty += [f"{prefix}_{language}" for prefix, _length in COPY_COLUMNS for language in LANGUAGES]
    assert len(empty) == 25
    assert {name: getattr(decoration, name) for name in empty} == dict.fromkeys(empty)


def test_decoration_unknown_poster_mode_is_rejected(db: Session) -> None:
    """第 12 条「海报有两种模式：模板…与上传图」：
    其他模式被 poster_mode_valid 拒绝。
    """
    with _check_failed("ck_promotion_decorations_poster_mode_valid"):
        _decoration(db, poster_mode="video")


def test_decoration_with_both_posters_and_full_copy_is_accepted(db: Session) -> None:
    """第 12 条「上传图（桌面、手机各一张…三语替代文字，各 1–150 个字符…）」
    与「文案：标题（1–40 个字符）、副标题（0–80）、角标短语（0–12）、
    横条文字（0–60），各有英文、中文、马来文」：两张海报图与写满列长的
    三语文案能写入并读回。
    """
    copy = {
        f"{prefix}_{language}": "x" * length
        for prefix, length in COPY_COLUMNS
        for language in LANGUAGES
    }
    fields = {"poster_mode": "image"} | _poster("poster_desktop_") | _poster("poster_phone_")

    decoration = _decoration(db, **(fields | copy))

    assert {name: getattr(decoration, name) for name in fields | copy} == fields | copy


@pytest.mark.parametrize("prefix", POSTER_PREFIXES)
@pytest.mark.parametrize("missing", ["file_name", "source_format", "width", "height", "byte_size"])
def test_partial_poster_is_rejected(db: Session, prefix: str, missing: str) -> None:
    """第 12 条「海报图沿用…第 13 条的…存储规则」与落实写法「照
    store_design.py 标志五列的三条约束各写一组（同为空或同不为空…）」：
    五列缺一被 <前缀>all_or_none 拒绝。
    """
    poster = _poster(prefix) | {f"{prefix}{missing}": None}

    with _check_failed(f"ck_promotion_decorations_{prefix}all_or_none"):
        _decoration(db, **poster)


@pytest.mark.parametrize("prefix", POSTER_PREFIXES)
def test_poster_source_format_gif_is_rejected(db: Session, prefix: str) -> None:
    """第 12 条「海报图沿用…第 13 条的格式」与落实写法「格式为 jpeg、png、
    webp」：上传格式 gif 被 <前缀>source_format_valid 拒绝。
    """
    with _check_failed(f"ck_promotion_decorations_{prefix}source_format_valid"):
        _decoration(db, **(_poster(prefix) | {f"{prefix}source_format": "gif"}))


@pytest.mark.parametrize("prefix", POSTER_PREFIXES)
@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"width": 0}, id="width-0"),
        pytest.param({"width": 1601}, id="width-1601"),
        pytest.param({"height": 0}, id="height-0"),
        pytest.param({"height": 1601}, id="height-1601"),
        pytest.param({"byte_size": 0}, id="byte-size-0"),
    ],
)
def test_poster_display_size_out_of_range_is_rejected(
    db: Session, prefix: str, bad: dict[str, int]
) -> None:
    """落实写法「展示图宽与高在 1–1600 且字节数 >= 1」：
    越界被 <前缀>display_size_range 拒绝。
    """
    poster = _poster(prefix) | {f"{prefix}{column}": value for column, value in bad.items()}

    with _check_failed(f"ck_promotion_decorations_{prefix}display_size_range"):
        _decoration(db, **poster)


@pytest.mark.parametrize("prefix", POSTER_PREFIXES)
@pytest.mark.parametrize("source_format", ["jpeg", "png", "webp"])
def test_poster_bounds_and_formats_are_accepted(
    db: Session, prefix: str, source_format: str
) -> None:
    """上两条的对照（落实写法「格式为 jpeg、png、webp，展示图宽与高在
    1–1600 且字节数 >= 1」）：三种格式与宽 1、高 1600、字节数 1 都能写入。
    """
    poster = _poster(prefix) | {
        f"{prefix}source_format": source_format,
        f"{prefix}width": 1,
        f"{prefix}height": 1600,
        f"{prefix}byte_size": 1,
    }

    decoration = _decoration(db, **poster)

    assert {name: getattr(decoration, name) for name in poster} == poster


def test_second_decoration_for_a_promotion_is_rejected(db: Session) -> None:
    """落实写法「每个活动至多一行」：同一活动的第二行装饰被唯一约束拒绝。"""
    first = _decoration(db)

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: promotion_decorations.promotion_id"
    ):
        _decoration(db, promotion_id=first.promotion_id)


def test_poster_checks_follow_the_store_logo_checks() -> None:
    """落实写法「照 app/models/store_design.py 标志五列的三条约束各写一组…
    约束名把 logo_ 换成 poster_desktop_ 或 poster_phone_」：两组约束的名称
    与文本就是标志的三条换前缀。
    """
    logo = _model_checks(StoreDesignSetting.__table__)
    poster = _model_checks(PromotionDecoration.__table__)

    for prefix in POSTER_PREFIXES:
        for suffix in ("all_or_none", "source_format_valid", "display_size_range"):
            expected = logo[f"ck_store_design_settings_logo_{suffix}"].replace("logo_", prefix)
            assert poster[f"ck_promotion_decorations_{prefix}{suffix}"] == expected


# ---- promotion_publish_locks ----


def test_publish_lock_has_a_single_row(db: Session) -> None:
    """第 2 条「发布与改时间先锁一个专用的单行锁表」：单例槽默认 1 的一行
    能写入，第二行被唯一约束拒绝。
    """
    lock = _add(db, PromotionPublishLock())
    assert lock.singleton_slot == 1

    with pytest.raises(
        IntegrityError, match="UNIQUE constraint failed: promotion_publish_locks.singleton_slot"
    ):
        _add(db, PromotionPublishLock())


def test_publish_lock_slot_other_than_one_is_rejected(db: Session) -> None:
    """第 2 条「专用的单行锁表」与落实写法「singleton_slot 唯一且恒为 1」：
    单例槽 2 被 singleton_slot_one 拒绝。
    """
    with _check_failed("ck_promotion_publish_locks_singleton_slot_one"):
        _add(db, PromotionPublishLock(singleton_slot=2))


# ---- 外键 ----


def test_deleting_a_promotion_deletes_its_rules_scope_tiers_and_decoration(db: Session) -> None:
    """第 2 条「草稿可任意修改、删除」与落实写法「ON DELETE CASCADE」：
    删除活动时规则、规则适用的商品与分类、满额档与装饰随之删除；
    被引用的商品与分类本身、别的活动的规则都留下。
    """
    promotion = _promotion(db)
    category = _category(db)
    product = _product(db, category)
    item_rule = _rule(db, ITEM_PERCENT, promotion_id=promotion.id)
    _add(db, PromotionRuleProduct(rule_id=item_rule.id, product_id=product.id))
    _add(db, PromotionRuleCategory(rule_id=item_rule.id, category_id=category.id))
    spend_rule = _rule(db, SPEND_DISCOUNT, promotion_id=promotion.id, position=2)
    _tier(db, rule_id=spend_rule.id)
    _decoration(db, promotion_id=promotion.id)
    other_rule_id = _rule(db).id

    db.execute(delete(Promotion).where(Promotion.id == promotion.id))
    db.expire_all()

    assert _count(db, Promotion) == 1
    assert db.execute(select(PromotionRule.id)).scalars().all() == [other_rule_id]
    for model in (
        PromotionRuleProduct,
        PromotionRuleCategory,
        PromotionSpendTier,
        PromotionDecoration,
    ):
        assert _count(db, model) == 0, model.__tablename__
    assert (_count(db, Product), _count(db, Category)) == (1, 1)


def test_product_in_a_rule_scope_cannot_be_deleted(db: Session) -> None:
    """落实写法「product_id 外键 products.id ON DELETE RESTRICT」：
    被规则范围引用的商品不能物理删除。
    """
    rule = _rule(db, ITEM_PERCENT)
    product = _product(db, _category(db))
    _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        db.execute(delete(Product).where(Product.id == product.id))


def test_category_in_a_rule_scope_cannot_be_deleted(db: Session) -> None:
    """落实写法「category_id 外键 categories.id ON DELETE RESTRICT」：
    被规则范围引用的分类（下面没有商品）不能物理删除。
    """
    rule = _rule(db, ITEM_PERCENT)
    category = _category(db, "festive")
    _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        db.execute(delete(Category).where(Category.id == category.id))


# ---- 常量与 promotions.json ----


def test_packs_match_promotions_json() -> None:
    """第 12 条「素材包（首版 7 套…）…清单…由视觉稿导出到
    docs/design/tokens/」与落实写法「模型常量与它逐项相同」：
    PROMOTION_PACKS 与 packs 的 id 逐项、同序相同。
    """
    assert len(PROMOTION_PACKS) == 7
    assert list(PROMOTION_PACKS) == [pack["id"] for pack in TOKENS["packs"]]


def test_slots_match_promotions_json() -> None:
    """第 12 条「首页、商品列表、商品详情、购物车各装饰位的变体」与落实写法
    「模型常量与它逐项相同」：PROMOTION_SLOTS 的装饰位与变体与 slots
    逐项、同序相同；保持主题与不要氛围的 id 也相同。
    """
    assert [(slot, list(variants)) for slot, variants in PROMOTION_SLOTS.items()] == [
        (slot["id"], slot["variants"]) for slot in TOKENS["slots"]
    ]
    assert PALETTE_KEEP_THEME == TOKENS["keepThemePalette"]
    assert [(slot["id"], slot["none"]) for slot in TOKENS["slots"] if "none" in slot] == [
        ("ambient", AMBIENT_NONE)
    ]


def test_every_pack_offers_every_slot_variant() -> None:
    """第 12 条「任一 ID 不在清单里即回答取值不合格」：每个素材包列出的变体
    与 slots 相同，所以装饰表按列单独检查（不看所选素材包）就等于按清单检查。
    """
    slots = {slot["id"]: slot["variants"] for slot in TOKENS["slots"]}
    for pack in TOKENS["packs"]:
        assert pack["variants"] == slots, pack["id"]


def test_decoration_id_columns() -> None:
    """落实写法「pack、palette、ambient、home_poster、list_strip、card_badge、
    detail_ribbon、cart_progress 为 String(16)…均非空、都没有默认值」。
    """
    assert list(DECORATION_CHOICES) == list(DECORATION)
    columns = PromotionDecoration.__table__.columns
    for name in DECORATION:
        column = columns[name]
        assert column.type.length == 16, name
        assert not column.nullable, name
        assert column.default is None and column.server_default is None, name


# ---- 迁移 0027 ----


def test_migration_revision_follows_0026() -> None:
    """落实写法「revision 0027、down_revision 0026」。"""
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0027", "0026")


def test_migration_tables_match_the_models() -> None:
    """落实写法「upgrade 建七张表…downgrade…按外键依赖的逆序删表」：
    迁移建的七张表与七个模型的表相同，被引用的表排在引用它的表之前。
    """
    assert set(MIGRATION.TABLES) == {model.__tablename__ for model in MODELS}
    order = {table: index for index, table in enumerate(MIGRATION.TABLES)}
    for model in MODELS:
        for foreign_key in model.__table__.foreign_keys:
            referred = foreign_key.column.table.name
            if referred in order:
                assert order[referred] < order[model.__tablename__], model.__tablename__


def test_migration_check_constraints_match_the_models() -> None:
    """落实写法「约束表达式作为常量自带并与模型逐字相同，约束名按
    app/db/base.py 的命名约定逐个写出」（alembic check 不比较检查约束）：
    迁移每张表的检查约束与模型的名称与文本逐字相同。
    """
    assert set(MIGRATION.CHECK_CONSTRAINTS) == set(MIGRATION.TABLES)
    for model in MODELS:
        table = model.__tablename__
        assert dict(MIGRATION.CHECK_CONSTRAINTS[table]) == _model_checks(model.__table__), table


def test_migration_decoration_columns_match_the_model() -> None:
    """落实写法「poster_alt、title、subtitle、badge、strip 各有 _en、_zh、_ms
    三列，分别为 String(150)、String(40)、String(80)、String(12)、
    String(60)」：迁移自带的 ID 列与文案列常量与模型一致。
    """
    columns = PromotionDecoration.__table__.columns
    assert MIGRATION.DECORATION_ID_COLUMNS == tuple(DECORATION)
    assert MIGRATION.DECORATION_TEXT_COLUMNS == COPY_COLUMNS
    assert MIGRATION.LANGUAGES == LANGUAGES
    for prefix, length in COPY_COLUMNS:
        for language in LANGUAGES:
            column = columns[f"{prefix}_{language}"]
            assert (column.type.length, column.nullable) == (length, True), column.name
    assert MIGRATION.SINGLETON_SLOT == 1

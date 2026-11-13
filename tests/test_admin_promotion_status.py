"""后台促销活动的发布、撤回、提前结束、删除与复制（app/services/admin_promotions.py，
SHOP-TASK-241）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1、2 条（下称「第 N 条」）、
docs/UX.md 0.20 的 A12 状态表（下称「UX A12」），以及 SHOP-TASK-241 写定的落实写法
（下称「落实写法」）。每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

用 SQLite 内存库（StaticPool，打开外键检查）按模型建表，写入管理员、发布锁那一行、
两个分类与三件商品并提交。SQLite 不生成 FOR UPDATE，加锁的顺序与效果由 MySQL 上的
并发守住；这里只核对结果。已发布的活动由 create_promotion 建成后直接改状态列造出
（发布本身另有测试）。海报存储以 monkeypatch 替换 image_store.delete_image 并记录调用，
不访问 R2。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
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
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
from app.services import image_store
from app.services.admin_promotions import (
    ADMIN_PROMOTION_COPIED,
    ADMIN_PROMOTION_DELETED,
    ADMIN_PROMOTION_ENDED,
    ADMIN_PROMOTION_PUBLISHED,
    ADMIN_PROMOTION_WITHDRAWN,
    CopyInput,
    DecorationInput,
    PromotionChanged,
    PromotionDetail,
    PromotionInput,
    PromotionInvalid,
    PromotionNotFound,
    PromotionOverlap,
    PromotionProblem,
    PromotionStateInvalid,
    RuleInput,
    TierInput,
    copy_promotion,
    create_promotion,
    delete_promotion,
    end_promotion,
    publish_promotion,
    read_promotion,
    withdraw_promotion,
)
from app.services.image_store import POSTER_KEY_PREFIX
from app.services.promotions import active_promotion

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。PRECISE 为带秒与微秒的同一分钟。
NOW = datetime(2026, 11, 13, 4, 0)
PRECISE = datetime(2026, 11, 13, 4, 0, 30, 750000)
SETTINGS = Settings(_env_file=None)

# 种子数据的 ID：分类 Kitchen、Garden；商品 Pan（Kitchen，上架）、Pot（Kitchen，未上架）、
# Rake（Garden，上架）。
KITCHEN, GARDEN = 1, 2
PAN, POT, RAKE = 1, 2, 3

# 马来西亚本地时间的活动时段：相对 NOW 未开始（草稿同）、进行中、已结束。
WINDOWS = {
    "draft": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "scheduled": ("2026-12-01T00:00", "2026-12-10T00:00"),
    "running": ("2026-11-10T00:00", "2026-11-20T00:00"),
    "ended": ("2026-11-01T00:00", "2026-11-05T00:00"),
}
STATES = ("draft", "scheduled", "running", "ended")

DESKTOP_NAME = "0123456789abcdef0123456789abcdef"
PHONE_NAME = "fedcba9876543210fedcba9876543210"
POSTER_VALUES = ("png", 1600, 600, 4321)
COLUMN_SUFFIXES = ("file_name", "source_format", "width", "height", "byte_size")
STATUS_ACTIONS = (
    ADMIN_PROMOTION_PUBLISHED,
    ADMIN_PROMOTION_WITHDRAWN,
    ADMIN_PROMOTION_ENDED,
    ADMIN_PROMOTION_DELETED,
    ADMIN_PROMOTION_COPIED,
)


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


class FakeStore:
    """替换 image_store.delete_image，按顺序记录调用与 prefix。

    prefix 只限关键字且没有默认值：漏传即 TypeError。
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def delete_image(self, settings: Settings, file_name: str, *, prefix: str) -> None:
        self.calls.append(("delete", file_name, prefix))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(image_store, "delete_image", fake.delete_image)
    return fake


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


def _input(state: str = "draft", **fields: object) -> PromotionInput:
    starts_at, ends_at = WINDOWS[state]
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


def _make(db: Session, admin_id: int, state: str, **fields: object) -> PromotionDetail:
    """按 WINDOWS 建一个处于 state 的活动（非草稿的直接改为已发布）。"""
    created = create_promotion(db, admin_id, _input(state, **fields), NOW)
    if state != "draft":
        db.execute(update(Promotion).where(Promotion.id == created.id).values(status="published"))
        db.commit()
    detail = read_promotion(db, created.id, NOW)
    assert detail.state == state
    return detail


def _set_poster(db: Session, promotion_id: int, slot: str, file_name: str) -> None:
    """直接写装饰行 slot 位置的五列。"""
    values = (file_name, *POSTER_VALUES)
    columns = {
        f"poster_{slot}_{suffix}": value
        for suffix, value in zip(COLUMN_SUFFIXES, values, strict=True)
    }
    db.execute(
        update(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == promotion_id)
        .values(**columns)
    )
    db.commit()


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


def _row(db: Session, promotion_id: int) -> tuple:
    columns = (Promotion.status, Promotion.version, Promotion.ended_early_at, Promotion.ends_at)
    return tuple(db.execute(select(*columns).where(Promotion.id == promotion_id)).one())


def _status_audits(db: Session) -> list[tuple]:
    stmt = (
        select(
            AuditEvent.action,
            AuditEvent.target_type,
            AuditEvent.target_id,
            AuditEvent.old_value,
            AuditEvent.new_value,
            AuditEvent.admin_account_id,
        )
        .where(AuditEvent.action.in_(STATUS_ACTIONS))
        .order_by(AuditEvent.id)
    )
    return [tuple(row) for row in db.execute(stmt)]


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _publish(db: Session, admin_id: int, detail: PromotionDetail) -> PromotionDetail:
    return publish_promotion(db, admin_id, detail.id, detail.version, NOW)


def _withdraw(db: Session, admin_id: int, detail: PromotionDetail) -> PromotionDetail:
    return withdraw_promotion(db, admin_id, detail.id, detail.version, NOW)


def _end(db: Session, admin_id: int, detail: PromotionDetail) -> PromotionDetail:
    return end_promotion(db, admin_id, detail.id, detail.version, NOW)


def _delete(db: Session, admin_id: int, detail: PromotionDetail) -> None:
    delete_promotion(db, SETTINGS, admin_id, detail.id, detail.version, NOW)


Operation = Callable[[Session, int, PromotionDetail], object]
OPERATIONS: dict[str, Operation] = {
    "publish": _publish,
    "withdraw": _withdraw,
    "end": _end,
    "delete": _delete,
}
ALLOWED = {"publish": "draft", "withdraw": "scheduled", "end": "running", "delete": "draft"}


# ---- 状态限制 ----


@pytest.mark.parametrize(
    ("operation", "state"),
    [(op, state) for op in OPERATIONS for state in STATES if state != ALLOWED[op]],
)
def test_operation_rejected_in_other_states(
    db: Session, admin_id: int, store: FakeStore, operation: str, state: str
) -> None:
    """第 2 条「活动只有草稿与已发布两种状态」下的各操作范围（UX A12 状态表：草稿可发布、
    删除；已发布未开始可撤回；进行中可提前结束；已结束只能复制）：不允许的状态抛
    PromotionStateInvalid（带当前 state），不改库、不写审计、不删海报对象。"""
    detail = _make(db, admin_id, state)
    before = _row(db, detail.id)

    with pytest.raises(PromotionStateInvalid) as excinfo:
        OPERATIONS[operation](db, admin_id, detail)

    assert excinfo.value.state == state
    assert _row(db, detail.id) == before
    assert _status_audits(db) == []
    assert store.calls == []


@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_version_mismatch_and_missing_are_changed(
    db: Session, admin_id: int, store: FakeStore, operation: str
) -> None:
    """第 2 条「发布、撤回、提前结束都带读取时的版本号，在事务里锁住活动行核对，不同即
    回答活动已被改动」（删除同，见落实写法）：版本不符与活动不存在都抛
    PromotionChanged，不改库。"""
    detail = _make(db, admin_id, ALLOWED[operation])
    before = _row(db, detail.id)

    with pytest.raises(PromotionChanged):
        OPERATIONS[operation](db, admin_id, replace(detail, version=detail.version + 1))
    with pytest.raises(PromotionChanged):
        OPERATIONS[operation](db, admin_id, replace(detail, id=999))

    assert _row(db, detail.id) == before
    assert _status_audits(db) == []


@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_version_must_be_int(db: Session, admin_id: int, operation: str) -> None:
    """第 2 条「带读取时的版本号」：版本号须为整数（布尔值不算），否则 TypeError。"""
    detail = _make(db, admin_id, ALLOWED[operation])

    with pytest.raises(TypeError):
        OPERATIONS[operation](db, admin_id, replace(detail, version=True))


# ---- 发布 ----


def test_publish_draft(db: Session, admin_id: int) -> None:
    """第 2 条「草稿……发布」与「有改动即版本号加一」：满足发布条件的草稿发布后为
    已发布未开始，版本加一，开始前不生效。"""
    draft = _make(db, admin_id, "draft")

    published = _publish(db, admin_id, draft)

    assert published.state == "scheduled"
    assert published.version == draft.version + 1
    assert _row(db, draft.id)[:2] == ("published", draft.version + 1)
    assert read_promotion(db, draft.id, NOW) == published
    assert active_promotion(db, NOW) is None


def test_publish_with_start_in_past_takes_effect(db: Session, admin_id: int) -> None:
    """第 2 条「开始时间已过的，发布即生效」：发布后为进行中，active_promotion 返回它。"""
    window = dict(zip(("starts_at", "ends_at"), WINDOWS["running"], strict=True))
    draft = _make(db, admin_id, "draft", **window)

    published = _publish(db, admin_id, draft)

    assert published.state == "running"
    active = active_promotion(db, NOW)
    assert active is not None and active.promotion_id == draft.id


@pytest.mark.parametrize(
    ("missing", "fields", "posters"),
    [
        ("rules", {"rules": ()}, ()),
        ("title_en", {"copy": CopyInput()}, ()),
        ("starts_at", {"starts_at": None}, ()),
        ("ends_at", {"ends_at": None}, ()),
        ("ends_at_past", dict(zip(("starts_at", "ends_at"), WINDOWS["ended"], strict=True)), ()),
        (
            "poster_desktop",
            {
                "decoration": _decoration(poster_mode="image"),
                "copy": CopyInput(title_en="Raya Sale", poster_alt_en="Raya poster"),
            },
            ("phone",),
        ),
        (
            "poster_phone",
            {
                "decoration": _decoration(poster_mode="image"),
                "copy": CopyInput(title_en="Raya Sale", poster_alt_en="Raya poster"),
            },
            ("desktop",),
        ),
        (
            "poster_alt_en",
            {"decoration": _decoration(poster_mode="image")},
            ("desktop", "phone"),
        ),
    ],
)
def test_publish_requires_each_condition(
    db: Session,
    admin_id: int,
    missing: str,
    fields: dict[str, object],
    posters: tuple[str, ...],
) -> None:
    """第 2 条「发布时须满足：至少一条规则、英文标题齐全、结束时间晚于当前时刻、海报为
    上传图模式时两张图与英文替代文字齐全」（开始与结束都须填见 UX A12「发布时开始与
    结束都须填」）：逐项缺失时抛 PromotionInvalid（publish_missing），仍为草稿、版本不变。"""
    draft = _make(db, admin_id, "draft", **fields)
    for slot in posters:
        _set_poster(db, draft.id, slot, DESKTOP_NAME if slot == "desktop" else PHONE_NAME)

    with pytest.raises(PromotionInvalid) as excinfo:
        _publish(db, admin_id, draft)

    assert excinfo.value.problems == (PromotionProblem("publish_missing", missing),)
    assert _row(db, draft.id)[:2] == ("draft", draft.version)
    assert _status_audits(db) == []


def test_publish_image_poster_with_both_images_and_alt(db: Session, admin_id: int) -> None:
    """第 2 条「海报为上传图模式时两张图与英文替代文字齐全」：齐全即可发布。"""
    draft = _make(
        db,
        admin_id,
        "draft",
        decoration=_decoration(poster_mode="image"),
        copy=CopyInput(title_en="Raya Sale", poster_alt_en="Raya poster"),
    )
    _set_poster(db, draft.id, "desktop", DESKTOP_NAME)
    _set_poster(db, draft.id, "phone", PHONE_NAME)

    assert _publish(db, admin_id, draft).state == "scheduled"


def test_publish_reports_scope_overlap_after_category_change(db: Session, admin_id: int) -> None:
    """第 3 条（第 2 条发布条件所引）「一件商品在同一活动里至多属于一条商品级规则：保存与
    发布时按当时的商品与分类检查」：草稿保存后 Pan 改到 Garden，发布报 scope_overlap。"""
    draft = _make(
        db,
        admin_id,
        "draft",
        rules=(_item(products=(PAN,)), _item(categories=(GARDEN,), percent=10)),
    )
    db.execute(update(Product).where(Product.id == PAN).values(category_id=GARDEN))
    db.commit()

    with pytest.raises(PromotionInvalid) as excinfo:
        _publish(db, admin_id, draft)

    assert excinfo.value.problems == (
        PromotionProblem("scope_overlap", "rules.2", product_id=PAN, product_name="Pan"),
    )
    assert _row(db, draft.id)[:2] == ("draft", draft.version)


def test_publish_time_overlap(db: Session, admin_id: int) -> None:
    """第 1 条「所有已发布活动的开始时间到实际结束时间两两不得重叠（相接可以）」与第 2 条
    「且与其他已发布活动不重叠」：与已发布的 Raya 相接的可以发布，重叠的抛
    PromotionOverlap（带对方的内部名称），仍为草稿。"""
    _make(db, admin_id, "scheduled")
    touching = _make(
        db,
        admin_id,
        "draft",
        name="Touching",
        starts_at="2026-12-10T00:00",
        ends_at="2026-12-15T00:00",
    )
    overlapping = _make(
        db,
        admin_id,
        "draft",
        name="Early",
        starts_at="2026-11-30T00:00",
        ends_at="2026-12-01T00:01",
    )

    assert _publish(db, admin_id, touching).state == "scheduled"
    with pytest.raises(PromotionOverlap) as excinfo:
        _publish(db, admin_id, overlapping)

    assert excinfo.value.other_name == "Raya"
    assert _row(db, overlapping.id)[:2] == ("draft", overlapping.version)


def test_publish_after_other_ended_early(db: Session, admin_id: int) -> None:
    """第 1 条「实际结束时间（提前结束时为提前结束的时刻）」：进行中的活动提前结束后，
    从结束时刻起的新活动不再与它重叠，可以发布。"""
    running = _make(db, admin_id, "running")
    end_promotion(db, admin_id, running.id, running.version, PRECISE)
    draft = _make(
        db,
        admin_id,
        "draft",
        name="Next",
        starts_at="2026-11-13T12:01",
        ends_at="2026-11-25T00:00",
    )

    assert _publish(db, admin_id, draft).state == "scheduled"


# ---- 撤回 ----


def test_withdraw_scheduled(db: Session, admin_id: int) -> None:
    """第 2 条「已发布但未开始的可……撤回为草稿」与「有改动即版本号加一」：撤回后为草稿，
    保留时间与内容，版本加一，到开始时刻也不生效。"""
    scheduled = _make(db, admin_id, "scheduled")

    withdrawn = _withdraw(db, admin_id, scheduled)

    assert withdrawn.state == "draft"
    assert withdrawn.version == scheduled.version + 1
    assert withdrawn.as_input() == scheduled.as_input()
    assert _row(db, scheduled.id)[:2] == ("draft", scheduled.version + 1)
    assert active_promotion(db, datetime(2026, 11, 30, 16, 0)) is None


def test_withdraw_rejected_when_orders_reference(db: Session, admin_id: int) -> None:
    """第 2 条「已开始的活动不能删除、不能撤回（订单引用它）」：已有订单引用的 scheduled
    活动按已开始处理，抛 PromotionStateInvalid（state 为 running），不改库。"""
    scheduled = _make(db, admin_id, "scheduled")
    _order(db, scheduled.id)

    with pytest.raises(PromotionStateInvalid) as excinfo:
        _withdraw(db, admin_id, scheduled)

    assert excinfo.value.state == "running"
    assert _row(db, scheduled.id)[:2] == ("published", scheduled.version)
    assert _status_audits(db) == []


# ---- 提前结束 ----


def test_end_running(db: Session, admin_id: int) -> None:
    """第 2 条「只能在进行中提前结束（提前结束的时刻取服务端当前时刻，之后不能恢复）」：
    ended_early_at 为截到整秒的当前时刻、不晚于结束时间，版本加一；之后不再生效，
    也不能再提前结束或撤回。"""
    running = _make(db, admin_id, "running")
    assert active_promotion(db, PRECISE) is not None

    ended = end_promotion(db, admin_id, running.id, running.version, PRECISE)

    assert ended.state == "ended"
    assert ended.version == running.version + 1
    assert ended.ended_early_at == "2026-11-13T12:00"
    status, version, ended_early_at, ends_at = _row(db, running.id)
    assert (status, version) == ("published", running.version + 1)
    assert ended_early_at == datetime(2026, 11, 13, 4, 0, 30)
    assert ended_early_at <= ends_at
    assert active_promotion(db, PRECISE) is None
    assert active_promotion(db, datetime(2026, 11, 13, 4, 0, 30)) is None
    with pytest.raises(PromotionStateInvalid) as excinfo:
        end_promotion(db, admin_id, ended.id, ended.version, PRECISE)
    assert excinfo.value.state == "ended"
    with pytest.raises(PromotionStateInvalid):
        withdraw_promotion(db, admin_id, ended.id, ended.version, PRECISE)
    assert _row(db, running.id)[2] == datetime(2026, 11, 13, 4, 0, 30)


# ---- 删除 ----


def test_delete_draft_with_rules_and_posters(
    db: Session, admin_id: int, store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条「草稿可任意修改、删除（连同它的海报图）」：规则、范围、满额档、装饰与活动
    一并删除，商品与分类不动；提交之后才以海报前缀删除两张图的对象。"""
    draft = _make(
        db,
        admin_id,
        "draft",
        rules=(
            _item(products=(PAN,), categories=(GARDEN,)),
            RuleInput(
                kind="spend_discount",
                discount_mode="amount",
                tiers=(TierInput(5000, 500, None), TierInput(10000, 1200, None)),
            ),
            RuleInput(kind="free_shipping", threshold_sen=6000),
        ),
    )
    _set_poster(db, draft.id, "desktop", DESKTOP_NAME)
    _set_poster(db, draft.id, "phone", PHONE_NAME)
    original_commit = db.commit

    def _commit() -> None:
        store.calls.append(("commit",))
        original_commit()

    monkeypatch.setattr(db, "commit", _commit)

    assert _delete(db, admin_id, draft) is None

    assert store.calls == [
        ("commit",),
        ("delete", DESKTOP_NAME, POSTER_KEY_PREFIX),
        ("delete", PHONE_NAME, POSTER_KEY_PREFIX),
    ]
    for model in (
        Promotion,
        PromotionDecoration,
        PromotionRule,
        PromotionRuleProduct,
        PromotionRuleCategory,
        PromotionSpendTier,
    ):
        assert _count(db, model) == 0, model.__name__
    assert _count(db, Product) == 3
    assert _count(db, Category) == 2
    with pytest.raises(PromotionNotFound):
        read_promotion(db, draft.id, NOW)


def test_delete_draft_without_posters_calls_no_store(
    db: Session, admin_id: int, store: FakeStore
) -> None:
    """第 2 条「删除（连同它的海报图）」：没有海报图的草稿删除时不调用存储。"""
    draft = _make(db, admin_id, "draft")

    _delete(db, admin_id, draft)

    assert store.calls == []
    assert _count(db, Promotion) == 0


def test_withdrawn_promotion_can_be_deleted(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 2 条「已发布但未开始的可……撤回为草稿」与「草稿可……删除」：撤回后的活动作为
    草稿删除。"""
    scheduled = _make(db, admin_id, "scheduled")
    withdrawn = _withdraw(db, admin_id, scheduled)

    _delete(db, admin_id, withdrawn)

    assert _count(db, Promotion) == 0


# ---- 复制 ----


@pytest.mark.parametrize("state", STATES)
def test_copy_any_state(db: Session, admin_id: int, state: str) -> None:
    """第 2 条「任何活动都可复制为新草稿」：四种状态都可复制，原活动不改、版本不变。"""
    source = _make(db, admin_id, state)

    copied = copy_promotion(db, admin_id, source.id, NOW)

    assert copied.state == "draft"
    assert copied.id != source.id
    assert read_promotion(db, source.id, NOW) == source


def test_copy_contents(db: Session, admin_id: int) -> None:
    """第 2 条「复制规则、开关、装饰与文案，不复制时间与海报图（海报改回模板模式）」：
    新草稿名称、规则（ID 不同）、开关、装饰与文案与原活动相同；没有时间、提前结束时刻
    与海报图，海报模式为模板，替代文字一并清空；版本号为 1。"""
    source = _make(
        db,
        admin_id,
        "running",
        coupon_combinable=True,
        points_combinable=True,
        rules=(
            _item(products=(PAN,)),
            RuleInput(
                kind="buy_x_get_y",
                buy_quantity=2,
                get_quantity=1,
                get_percent=100,
                category_ids=(GARDEN,),
            ),
            RuleInput(
                kind="spend_discount",
                discount_mode="percent",
                tiers=(TierInput(5000, None, 5), TierInput(10000, None, 10)),
            ),
            RuleInput(kind="free_shipping", threshold_sen=0),
        ),
        decoration=_decoration(pack="raya", palette="raya", poster_mode="image"),
        copy=CopyInput(
            title_en="Raya Sale",
            title_zh="开斋节特卖",
            subtitle_en="Up to 20% off",
            badge_ms="Jualan",
            strip_en="Free shipping",
            poster_alt_en="Raya poster",
            poster_alt_zh="开斋节海报",
            poster_alt_ms="Poster Raya",
        ),
    )
    _set_poster(db, source.id, "desktop", DESKTOP_NAME)
    _set_poster(db, source.id, "phone", PHONE_NAME)
    source = end_promotion(db, admin_id, source.id, source.version, PRECISE)

    copied = copy_promotion(db, admin_id, source.id, NOW)

    assert copied.version == 1
    assert copied.name == source.name
    assert (copied.starts_at, copied.ends_at, copied.ended_early_at) == (None, None, None)
    assert (copied.coupon_combinable, copied.points_combinable) == (True, True)
    assert copied.decoration == replace(source.decoration, poster_mode="template")
    assert copied.copy == replace(
        source.copy, poster_alt_en=None, poster_alt_zh=None, poster_alt_ms=None
    )
    assert (copied.poster_desktop_url, copied.poster_phone_url) == (None, None)
    assert [rule.as_input() for rule in copied.rules] == [rule.as_input() for rule in source.rules]
    assert not {rule.rule_id for rule in copied.rules} & {rule.rule_id for rule in source.rules}
    assert read_promotion(db, copied.id, NOW) == copied
    poster_columns = [
        getattr(PromotionDecoration, f"poster_{slot}_{suffix}")
        for slot in ("desktop", "phone")
        for suffix in COLUMN_SUFFIXES
    ]
    stmt = select(*poster_columns).where(PromotionDecoration.promotion_id == copied.id)
    assert set(db.execute(stmt).one()) == {None}
    assert read_promotion(db, source.id, PRECISE) == source


def test_copy_missing_is_not_found(db: Session, admin_id: int) -> None:
    """第 2 条「任何活动都可复制为新草稿」：活动不存在抛 PromotionNotFound，不建新活动。"""
    with pytest.raises(PromotionNotFound):
        copy_promotion(db, admin_id, 999, NOW)

    assert _count(db, Promotion) == 0


# ---- 审计 ----


def test_status_operations_write_audits(db: Session, admin_id: int, store: FakeStore) -> None:
    """第 2 条「审计：……发布、撤回、提前结束、删除、复制都写 AuditEvent，不含个人资料」：
    每种操作一条，对象类别为 promotion；删除的旧值为内部名称，复制的对象为新草稿、旧值为
    原活动 ID；不带其他值。"""
    draft = _make(db, admin_id, "draft", name="Draft Raya")
    running = _make(db, admin_id, "running", name="Running Raya")

    published = _publish(db, admin_id, draft)
    withdrawn = _withdraw(db, admin_id, published)
    _delete(db, admin_id, withdrawn)
    _end(db, admin_id, running)
    copied = copy_promotion(db, admin_id, running.id, NOW)

    assert _status_audits(db) == [
        (ADMIN_PROMOTION_PUBLISHED, "promotion", draft.id, None, None, admin_id),
        (ADMIN_PROMOTION_WITHDRAWN, "promotion", draft.id, None, None, admin_id),
        (ADMIN_PROMOTION_DELETED, "promotion", draft.id, "Draft Raya", None, admin_id),
        (ADMIN_PROMOTION_ENDED, "promotion", running.id, None, None, admin_id),
        (ADMIN_PROMOTION_COPIED, "promotion", copied.id, str(running.id), None, admin_id),
    ]
    assert ADMIN_PROMOTION_PUBLISHED == "admin_promotion_published"
    assert ADMIN_PROMOTION_WITHDRAWN == "admin_promotion_withdrawn"
    assert ADMIN_PROMOTION_ENDED == "admin_promotion_ended"
    assert ADMIN_PROMOTION_DELETED == "admin_promotion_deleted"
    assert ADMIN_PROMOTION_COPIED == "admin_promotion_copied"

"""节日促销活动的展示数据 promotion_display（app/services/promotion_display.py）与公开接口
GET /api/promotion（app/api/promotion.py），SHOP-TASK-243。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 10、12 条（下称「第 N 条」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的第 10 或 12 条哪一句。

用 SQLite 内存库（StaticPool、关闭 check_same_thread、打开外键检查）按模型建表；数据以模型行
直接写入并提交（promotion_display 读完回滚，未提交的数据会被丢掉）。接口的会话依赖每次请求
新开一个会话，时间固定为 NOW（monkeypatch 接口模块的 _utcnow）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.promotion as promotion_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import (
    Category,
    Product,
    ProductVariant,
    Promotion,
    PromotionDecoration,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
from app.services.promotion_display import (
    CategoryDisplay,
    RuleDisplay,
    TierDisplay,
    promotion_display,
)

URL = "/api/promotion"

# 不带时区的 UTC；活动默认在 NOW 进行中。
NOW = datetime(2026, 11, 13, 4, 0)
STARTS = datetime(2026, 11, 10, 0, 0)
ENDS = datetime(2026, 11, 20, 0, 0)
INTERNAL_NAME = "Internal-Name-7f3a"
DESKTOP_FILE = "0123456789abcdef0123456789abcdef"
PHONE_FILE = "fedcba9876543210fedcba9876543210"

DISPLAY_FIELDS = {
    "promotion_id",
    "title",
    "subtitle",
    "badge",
    "strip",
    "pack",
    "palette",
    "ambient",
    "home_poster",
    "list_strip",
    "card_badge",
    "detail_ribbon",
    "cart_progress",
    "poster_mode",
    "poster_desktop",
    "poster_phone",
    "poster_alt",
    "ends_at",
    "coupon_combinable",
    "points_combinable",
    "rules",
}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(promotion_api, "_utcnow", lambda: NOW)


@pytest.fixture
def client(engine: Engine) -> TestClient:
    app = create_app(Settings(_env_file=None))

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


def _add(db: Session, row: Any) -> Any:
    db.add(row)
    db.flush()
    return row


def _promotion(db: Session, **fields: Any) -> Promotion:
    values = {
        "name": INTERNAL_NAME,
        "status": "published",
        "starts_at": STARTS,
        "ends_at": ENDS,
        **fields,
    }
    return _add(db, Promotion(**values))


def _decoration(db: Session, promotion: Promotion, **fields: Any) -> PromotionDecoration:
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
        "title_en": "Year-End Sale",
        **fields,
    }
    return _add(db, PromotionDecoration(promotion_id=promotion.id, **values))


def _poster_columns(prefix: str, file_name: str) -> dict[str, Any]:
    return {
        f"{prefix}file_name": file_name,
        f"{prefix}source_format": "png",
        f"{prefix}width": 1200,
        f"{prefix}height": 400,
        f"{prefix}byte_size": 1000,
    }


def _category(db: Session, slug: str, **fields: Any) -> Category:
    values = {"name_en": slug.title(), "is_active": True, **fields}
    return _add(db, Category(slug=slug, **values))


def _product(db: Session, category: Category, slug: str, *, is_active: bool = True) -> Product:
    """一件满足发布规则的商品（is_active 为假时下架），带一个启用的 SKU。"""
    product = _add(
        db,
        Product(
            category_id=category.id,
            slug=slug,
            name_en=slug.upper(),
            description_en=f"About {slug}",
            is_active=is_active,
        ),
    )
    _add(
        db,
        ProductVariant(
            product_id=product.id,
            sku=f"{slug}-1",
            price_sen=1000,
            daily_initial_stock=10,
            available_stock=10,
            is_active=True,
        ),
    )
    return product


def _rule(
    db: Session,
    promotion: Promotion,
    position: int,
    *,
    products: tuple[Product, ...] = (),
    categories: tuple[Category, ...] = (),
    **params: Any,
) -> PromotionRule:
    rule = _add(db, PromotionRule(promotion_id=promotion.id, position=position, **params))
    for product in products:
        _add(db, PromotionRuleProduct(rule_id=rule.id, product_id=product.id))
    for category in categories:
        _add(db, PromotionRuleCategory(rule_id=rule.id, category_id=category.id))
    return rule


def _running(db: Session, **fields: Any) -> int:
    """一个在 NOW 进行中的活动，带装饰行与一条免运费规则；提交后返回活动 ID。"""
    promotion = _promotion(db, **fields)
    _decoration(db, promotion)
    _rule(db, promotion, 1, kind="free_shipping", threshold_sen=6000)
    db.commit()
    return promotion.id


# ---------------------------------------------------------------------------
# 文案（第 12 条）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        pytest.param(
            "en",
            ("Year-End Sale", "Up to 20% off", None, "Free shipping over RM60"),
            id="en",
        ),
        pytest.param(
            "zh",
            ("年终大促", "Up to 20% off", None, "满 RM60 免运费"),
            id="zh_falls_back",
        ),
        pytest.param(
            "ms",
            ("Jualan Akhir Tahun", "Up to 20% off", None, "Free shipping over RM60"),
            id="ms_falls_back",
        ),
    ],
)
def test_copy_falls_back_to_english_and_empty_is_null(
    db: Session, lang: str, expected: tuple[str | None, ...]
) -> None:
    """第 12 条「文案：标题…副标题…角标短语…横条文字，各有英文、中文、马来文，英文标题必填，
    其余缺译回退英文」与第 10 条「所选语言的标题与文案（缺译回退英文）」：缺译（空或只有空白）
    回退英文，英文也空即 null。
    """
    promotion = _promotion(db)
    _decoration(
        db,
        promotion,
        title_zh="年终大促",
        title_ms="Jualan Akhir Tahun",
        subtitle_en="Up to 20% off",
        subtitle_zh="   ",
        strip_en="Free shipping over RM60",
        strip_zh="满 RM60 免运费",
    )
    promotion_id = promotion.id
    db.commit()

    display = promotion_display(db, promotion_id, lang)

    assert display is not None
    assert (display.title, display.subtitle, display.badge, display.strip) == expected


# ---------------------------------------------------------------------------
# 规则（第 10 条）
# ---------------------------------------------------------------------------


def test_section_8_rules_give_kind_and_parameters(db: Session) -> None:
    """第 10 条「以及每条规则的类别与参数（前台按三语文案模板生成规则说明…保证说明与计价一致）」：
    第 8 条算例的四条规则按 position 升序给出类别与参数；家居分类给出所选语言的名称，
    A 的规则 has_products 为真；满额减的两档按门槛给出。
    """
    promotion = _promotion(db, coupon_combinable=True, points_combinable=True)
    _decoration(db, promotion)
    gifts = _category(db, "gifts")
    home = _category(db, "home", name_en="Home", name_zh="家居")
    a = _product(db, gifts, "a")
    _product(db, home, "b")
    _product(db, home, "c")
    # 写入顺序与排序不同：结果仍按 position 升序。
    free = _rule(db, promotion, 4, kind="free_shipping", threshold_sen=6000)
    spend = _rule(db, promotion, 3, kind="spend_discount", discount_mode="amount")
    for position, threshold_sen, amount_sen in ((2, 10000, 1200), (1, 5000, 500)):
        tier = PromotionSpendTier(
            rule_id=spend.id, position=position, threshold_sen=threshold_sen, amount_sen=amount_sen
        )
        _add(db, tier)
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
    item = _rule(
        db,
        promotion,
        1,
        kind="item_discount",
        discount_mode="percent",
        percent=20,
        products=(a,),
    )
    promotion_id = promotion.id
    ids = (item.id, bxgy.id, spend.id, free.id)
    db.commit()

    display = promotion_display(db, promotion_id, "zh")

    assert display is not None
    assert display.coupon_combinable is True
    assert display.points_combinable is True
    none = {
        "discount_mode": None,
        "percent": None,
        "amount_sen": None,
        "buy_quantity": None,
        "get_quantity": None,
        "get_percent": None,
        "threshold_sen": None,
        "tiers": [],
        "categories": [],
        "has_products": False,
    }
    assert display.rules == [
        RuleDisplay(
            **{
                **none,
                "rule_id": ids[0],
                "position": 1,
                "kind": "item_discount",
                "discount_mode": "percent",
                "percent": 20,
                "has_products": True,
            }
        ),
        RuleDisplay(
            **{
                **none,
                "rule_id": ids[1],
                "position": 2,
                "kind": "buy_x_get_y",
                "buy_quantity": 2,
                "get_quantity": 1,
                "get_percent": 100,
                "categories": [CategoryDisplay(slug="home", name="家居")],
            }
        ),
        RuleDisplay(
            **{
                **none,
                "rule_id": ids[2],
                "position": 3,
                "kind": "spend_discount",
                "discount_mode": "amount",
                "tiers": [
                    TierDisplay(threshold_sen=5000, amount_sen=500, percent=None),
                    TierDisplay(threshold_sen=10000, amount_sen=1200, percent=None),
                ],
            }
        ),
        RuleDisplay(
            **{
                **none,
                "rule_id": ids[3],
                "position": 4,
                "kind": "free_shipping",
                "threshold_sen": 6000,
            }
        ),
    ]


def test_scope_lists_active_categories_by_id_and_hides_products(db: Session) -> None:
    """第 10 条「每条规则的类别与参数」（范围只给分类与是否另选了单件商品，UX 0.20 P15 选中的
    单件商品不逐一列出）：分类按 ID 升序、停用的与没有英文名称的不列，名称缺译回退英文；
    显式商品都已下架时 has_products 为假；分类都已停用且没有上架的显式商品时，规则照常列出、
    范围为空。
    """
    promotion = _promotion(db)
    _decoration(db, promotion)
    first = _category(db, "kitchen")
    second = _category(db, "garden", name_ms="Taman")
    disabled = _category(db, "old", is_active=False)
    unnamed = _category(db, "unnamed", name_en="  ")
    hidden = _product(db, first, "hidden", is_active=False)
    # 写入范围时先写 ID 较大的分类。
    scoped = _rule(
        db,
        promotion,
        1,
        kind="item_discount",
        discount_mode="amount",
        amount_sen=300,
        products=(hidden,),
        categories=(unnamed, disabled, second, first),
    )
    orphan = _rule(
        db,
        promotion,
        2,
        kind="buy_x_get_y",
        buy_quantity=1,
        get_quantity=1,
        get_percent=50,
        categories=(disabled,),
    )
    promotion_id, scoped_id, orphan_id = promotion.id, scoped.id, orphan.id
    db.commit()

    display = promotion_display(db, promotion_id, "ms")

    assert display is not None
    by_id = {rule.rule_id: rule for rule in display.rules}
    assert [rule.rule_id for rule in display.rules] == [scoped_id, orphan_id]
    assert by_id[scoped_id].categories == [
        CategoryDisplay(slug="kitchen", name="Kitchen"),
        CategoryDisplay(slug="garden", name="Taman"),
    ]
    assert by_id[scoped_id].has_products is False
    assert by_id[orphan_id].categories == []
    assert by_id[orphan_id].has_products is False


# ---------------------------------------------------------------------------
# 海报与装饰（第 12 条）
# ---------------------------------------------------------------------------


def test_template_poster_hides_uploaded_images(db: Session) -> None:
    """第 12 条「海报有两种模式：模板（素材包的插画 + 标题、副标题、倒计时与去专题页的按钮）
    与上传图」：模板模式下已上传的图与替代文字都不给出，装饰 ID 原样给出。
    """
    promotion = _promotion(db)
    _decoration(
        db,
        promotion,
        pack="raya",
        palette="raya",
        ambient="garland",
        home_poster="centered",
        list_strip="ticket",
        card_badge="seal",
        detail_ribbon="panel",
        cart_progress="steps",
        poster_alt_en="Raya sale poster",
        **_poster_columns("poster_desktop_", DESKTOP_FILE),
        **_poster_columns("poster_phone_", PHONE_FILE),
    )
    promotion_id = promotion.id
    db.commit()

    display = promotion_display(db, promotion_id, "en")

    assert display is not None
    assert display.poster_mode == "template"
    assert (display.poster_desktop, display.poster_phone, display.poster_alt) == (None, None, None)
    decoration = (
        display.pack,
        display.palette,
        display.ambient,
        display.home_poster,
        display.list_strip,
        display.card_badge,
        display.detail_ribbon,
        display.cart_progress,
    )
    expected = ("raya", "raya", "garland", "centered", "ticket", "seal", "panel", "steps")
    assert decoration == expected


def test_image_poster_gives_paths_and_alt_text(db: Session) -> None:
    """第 12 条「上传图（桌面、手机各一张，点击去专题页，三语替代文字…英文必填）」与第 10 条
    「海报图地址」：上传图模式给出两张图的提供路径，替代文字缺译回退英文。
    """
    promotion = _promotion(db)
    _decoration(
        db,
        promotion,
        poster_mode="image",
        poster_alt_en="Year-end sale poster",
        poster_alt_zh="年终大促海报",
        **_poster_columns("poster_desktop_", DESKTOP_FILE),
        **_poster_columns("poster_phone_", PHONE_FILE),
    )
    promotion_id = promotion.id
    db.commit()

    zh = promotion_display(db, promotion_id, "zh")
    ms = promotion_display(db, promotion_id, "ms")

    assert zh is not None and ms is not None
    assert zh.poster_mode == "image"
    assert zh.poster_desktop == f"/api/promotion-posters/{DESKTOP_FILE}.webp"
    assert zh.poster_phone == f"/api/promotion-posters/{PHONE_FILE}.webp"
    assert zh.poster_alt == "年终大促海报"
    assert ms.poster_alt == "Year-end sale poster"


def test_missing_promotion_or_decoration_and_draft_without_times(db: Session) -> None:
    """第 10 条「实际结束时间」：没有时间的草稿 ends_at 为 null（预览按活动 ID 复用）；
    活动不存在或缺装饰行时返回 None。
    """
    draft = _promotion(db, status="draft", starts_at=None, ends_at=None)
    _decoration(db, draft)
    bare = _promotion(db)
    draft_id, bare_id = draft.id, bare.id
    db.commit()

    display = promotion_display(db, draft_id, "en")

    assert display is not None
    assert display.ends_at is None
    assert display.rules == []
    assert promotion_display(db, bare_id, "en") is None
    assert promotion_display(db, bare_id + 1, "en") is None


# ---------------------------------------------------------------------------
# 接口（第 10 条）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "ends_at"),
    [
        pytest.param({}, "2026-11-20T00:00:00Z", id="running"),
        pytest.param(
            {"ended_early_at": NOW + timedelta(hours=2)},
            "2026-11-13T06:00:00Z",
            id="ending_early_later",
        ),
        pytest.param(
            {"starts_at": NOW + timedelta(days=1), "ends_at": NOW + timedelta(days=5)},
            None,
            id="scheduled",
        ),
        pytest.param(
            {"starts_at": NOW - timedelta(days=5), "ends_at": NOW},
            None,
            id="ended",
        ),
        pytest.param({"ended_early_at": NOW}, None, id="ended_early"),
    ],
)
def test_endpoint_gives_active_promotion_and_server_time(
    db: Session, client: TestClient, fields: dict[str, Any], ends_at: str | None
) -> None:
    """第 10 条「公开接口按当前时刻给出生效活动的展示数据…实际结束时间与服务端当前时间（前台倒计时
    以二者之差为准…）」与「没有生效活动时这些字段为空」：进行中的给出活动与实际结束时间（提前
    结束时为提前结束的时刻），未开始、已结束与已提前结束的 promotion 为 null；server_time 总是
    服务端当前时刻（UTC）。
    """
    promotion_id = _running(db, **fields)

    response = client.get(URL, params={"lang": "en"})

    assert response.status_code == 200
    body = response.json()
    assert body["server_time"] == "2026-11-13T04:00:00Z"
    if ends_at is None:
        assert body["promotion"] is None
    else:
        assert body["promotion"]["promotion_id"] == promotion_id
        assert body["promotion"]["ends_at"] == ends_at
        assert body["promotion"]["title"] == "Year-End Sale"


def test_endpoint_without_promotion_gives_null(client: TestClient) -> None:
    """第 10 条「没有生效活动时这些字段为空，前台不显示任何活动内容」：库里没有活动时
    promotion 为 null，照样给出 server_time。
    """
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"promotion": None, "server_time": "2026-11-13T04:00:00Z"}


def test_endpoint_omits_internal_name_and_version(db: Session, client: TestClient) -> None:
    """第 1 条「内部名称（只在后台显示）」与第 10 条「给出生效活动的展示数据：活动 ID、所选语言的
    标题与文案…」：响应只有展示字段，不含内部名称与版本号。
    """
    _running(db)

    response = client.get(URL, params={"lang": "zh"})

    assert response.status_code == 200
    promotion = response.json()["promotion"]
    assert set(promotion) == DISPLAY_FIELDS
    assert INTERNAL_NAME not in response.text
    assert "version" not in response.text
    assert set(promotion["rules"][0]) == set(RuleDisplay.model_fields)


def test_endpoint_reads_every_request_with_no_store(db: Session, client: TestClient) -> None:
    """第 10 条「展示接口的响应带 no-store」与第 1 条「每次…展示都从数据库读取当时生效的活动，
    不缓存」：改了文案之后下一次请求即读到新值，两次响应都带 no-store。
    """
    promotion_id = _running(db)
    first = client.get(URL)
    db.execute(
        update(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == promotion_id)
        .values(title_en="Last Call")
    )
    db.commit()

    second = client.get(URL)

    for response in (first, second):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert first.json()["promotion"]["title"] == "Year-End Sale"
    assert second.json()["promotion"]["title"] == "Last Call"


def test_invalid_lang_is_422_without_echo_and_no_store(client: TestClient) -> None:
    """第 10 条「所选语言的标题与文案」与「展示接口的响应带 no-store」：lang 只接受 en、zh、ms，
    不合法时 422、不回显所给的值，同样带 no-store。
    """
    response = client.get(URL, params={"lang": "fr-ECHO42"})

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "ECHO42" not in response.text

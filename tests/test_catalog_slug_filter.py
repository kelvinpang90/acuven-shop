"""商品列表接口按 slug 筛选（SHOP-TASK-053）。

首页按店铺装修的精选商品取商品卡片：GET /api/catalog/products 的可重复参数 slug
只留 slug 在其中且满足 published() 的商品，与其他筛选为且；不存在或未发布的 slug 被略去；
最多 20 个、每个 1 到 100 个字符，超出 422 且不回显所给的值；不给时与之前完全相同。
每条测试的文档字符串写明它守住的是哪一条规则。
用 SQLite 内存库（打开外键检查）按模型建表，接口的会话依赖换成测试自己的会话；
建数据的辅助函数从 tests/test_catalog_api.py 导入（不改该文件）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.services.catalog import list_products
from tests.test_catalog_api import _category, _listed, _product, _variant

URL = "/api/catalog/products"


@pytest.fixture
def db() -> Iterator[Session]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session
    engine.dispose()


@pytest.fixture
def client(db: Session) -> TestClient:
    app = create_app(Settings(_env_file=None))
    # 接口用测试自己的会话：flush 过的数据就看得到，不必提交。
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def _page(client: TestClient, **params: object) -> dict:
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _slugs(client: TestClient, **params: object) -> list[str]:
    return [item["slug"] for item in _page(client, **params)["items"]]


def _five(db: Session) -> None:
    """五件可发布的商品 p1–p5，创建时间依次晚一天：newest 排序为 p5、p4、p3、p2、p1。"""
    for day in range(1, 6):
        _listed(db, f"p{day}", created_at=datetime(2026, 9, day))


def test_several_slugs_return_only_those_products_with_the_right_total(
    db: Session,
    client: TestClient,
) -> None:
    """「给出时只返回 slug 在其中的商品」且「排序、分页、每页条数与每项字段不变」：
    结果按 newest 排序而不是按参数顺序；总数是命中数；分页照常；每项与不筛选时的对应项相同。
    """
    _five(db)
    unfiltered = {item["slug"]: item for item in _page(client)["items"]}

    listing = _page(client, slug=["p1", "p4", "p2"])
    first = _page(client, slug=["p1", "p4", "p2"], page_size=2)
    second = _page(client, slug=["p1", "p4", "p2"], page_size=2, page=2)

    assert (listing["total"], listing["page"], listing["page_size"]) == (3, 1, 24)
    assert listing["items"] == [unfiltered[slug] for slug in ["p4", "p2", "p1"]]
    assert (first["total"], [item["slug"] for item in first["items"]]) == (3, ["p4", "p2"])
    assert (second["total"], [item["slug"] for item in second["items"]]) == (3, ["p1"])
    assert _slugs(client, slug=["p1", "p4", "p2"], sort="price_desc") == ["p1", "p2", "p4"]
    # 同一 slug 重复给出不重复返回。
    assert _page(client, slug=["p3", "p3"])["total"] == 1


def test_unknown_and_unpublished_slugs_are_left_out_not_rejected(
    db: Session,
    client: TestClient,
) -> None:
    """「不存在或不满足 published() 的 slug 被略去而不是报错」：停用的商品、所属分类停用的
    商品、没有启用 SKU 的商品与不存在的 slug 都不出现，接口仍 200；全都略去时为空页。
    """
    _listed(db, "mug")
    _listed(db, "retired", is_active=False)
    _listed(db, "hidden-category", category=_category(db, "closed", is_active=False))
    no_sku = _product(db, "no-sku")
    _variant(db, no_sku, "NO-SKU-1", is_active=False)

    wanted = ["no-such-product", "retired", "mug", "hidden-category", "no-sku"]
    listing = _page(client, slug=wanted)
    nothing = _page(client, slug=["no-such-product", "retired"])

    assert (listing["total"], [item["slug"] for item in listing["items"]]) == (1, ["mug"])
    assert (nothing["total"], nothing["items"]) == (0, [])


def test_slug_and_category_filters_are_and(db: Session, client: TestClient) -> None:
    """「与其他筛选为且」：slug 与分类同时给出时，两者都满足才出现；
    slug 在其中但分类不符的商品不出现，分类相符但 slug 不在其中的也不出现。
    """
    kitchen = _category(db, "kitchen")
    garden = _category(db, "garden", name_en="Garden")
    _listed(db, "mug", category=kitchen)
    _listed(db, "plate", category=kitchen)
    _listed(db, "rake", category=garden)

    assert _slugs(client, slug=["mug", "rake"], category="kitchen") == ["mug"]
    assert _slugs(client, slug=["rake"], category="kitchen") == []
    assert _slugs(client, slug=["mug", "rake"], q="rake") == ["rake"]


def test_without_slug_the_listing_is_unchanged(db: Session, client: TestClient) -> None:
    """「不给时与现在完全相同」与「现有调用方式与行为不变」：不带 slug 返回全部已发布商品，
    与服务函数不给 slugs、给空元组的结果相同；未发布的商品照旧不出现。
    """
    _five(db)
    _listed(db, "retired", is_active=False)

    listing = _page(client)

    assert listing["total"] == 5
    assert [item["slug"] for item in listing["items"]] == ["p5", "p4", "p3", "p2", "p1"]
    assert list_products(db, lang="en") == list_products(db, lang="en", slugs=())
    assert list_products(db, lang="en").total == 5


@pytest.mark.parametrize(
    ("slugs", "loc"),
    [
        ([f"p{n}" for n in range(21)], ["query", "slug"]),
        (["p1", "x" * 101], ["query", "slug", 1]),
        ([""], ["query", "slug", 0]),
    ],
    ids=["21-slugs", "101-characters", "empty"],
)
def test_too_many_or_badly_sized_slugs_are_rejected_without_echoing_them(
    db: Session,
    client: TestClient,
    slugs: list[str],
    loc: list[object],
) -> None:
    """「最多 20 个，每个 1 到 100 个字符，超出 422，detail 为不含输入值的错误数组，
    写法同 app/api/pay.py 的 _language」：错误只有类型、位置与固定消息，不含 input 也不含所给的值。
    """
    _five(db)

    response = client.get(URL, params={"slug": slugs})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert [error["loc"] for error in detail] == [loc]
    assert all(set(error) == {"type", "loc", "msg"} for error in detail)
    for value in slugs:
        if value:
            assert value not in response.text


def test_twenty_slugs_of_up_to_100_characters_are_accepted(
    db: Session,
    client: TestClient,
) -> None:
    """上限本身可用：「最多 20 个，每个 1 到 100 个字符」的边界值 20 个与 100 个字符不被拒。"""
    _five(db)
    long_slug = "x" * 100
    # 分类 slug 与 SKU 另给短的：辅助函数按商品 slug 生成的会超过列长。
    long_category = _category(db, "long")
    long = _product(db, long_slug, category=long_category, created_at=datetime(2026, 9, 10))
    _variant(db, long, "LONG-1")

    wanted = [long_slug, "p1"] + [f"missing-{n}" for n in range(18)]

    assert len(wanted) == 20
    assert _slugs(client, slug=wanted) == [long_slug, "p1"]

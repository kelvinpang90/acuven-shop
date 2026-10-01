"""订单相关六张表的数据库约束。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Order / OrderItem、OrderRecipient、
PaymentAttempt / OrderEvent 三行，以及「订单与退款状态」「失败、并发与重试」
「权限与资料保护」「资料保留」。每条测试（参数化的测试是每个用例）写明它守住的设计原句。

先写入一张合法订单及其各子表作对照，再逐个写反例。检查约束的反例先在独立的 SQLite
连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，再断言写入被拒、且报出的是
不成立的约束之一：有些约束是其他约束的推论（如应付不低于运费），它的反例必然同时违反
别的约束，SQLite 只报第一条。用 SQLite 内存库按模型建表；SQLite 默认不检查外键，
每个连接都须打开。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, create_engine, delete, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Category,
    Order,
    OrderEvent,
    OrderItem,
    OrderItemUnit,
    OrderRecipient,
    PaymentAttempt,
    Product,
    ProductVariant,
)
from app.models.order import (
    ACTOR_ADMIN,
    ACTOR_GUEST,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 9, 30, 2, 0)
FINGERPRINT = "0f" * 32

# 只用来对单行求值检查约束表达式，不建任何表。
_EVAL_ENGINE = create_engine("sqlite://")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield db
    engine.dispose()


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


# 合法数据取自「计价、优惠、积分与库存」第 4 条算例：A 商品 2 件各 RM10、B 商品 1 件 RM5，
# 券减 RM2.50，300 积分抵 RM3，示例运费 RM8，模拟支付成功后获得 19 积分。


def _order_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_number": "0123456789ABCDEF",
        "status": STATUS_PAID,
        "subtotal_sen": 2500,
        "coupon_discount_sen": 250,
        "points_redeemed": 300,
        "shipping_fee_sen": 800,
        "total_sen": 2750,
        "points_earned": 19,
        "shipping_zone_code": "MY-10",
        "shipping_rate_version": 1,
        "idempotency_key": "order-key-1",
        "request_fingerprint": FINGERPRINT,
        "created_at": CREATED,
        "payment_expires_at": CREATED + timedelta(minutes=15),
        "paid_at": CREATED + timedelta(minutes=3),
    }
    return values | overrides


def _item_values(order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order_id,
        "line_index": 0,
        "variant_id": None,
        "sku": "MUG-RED",
        "product_name_en": "Ceramic mug",
        "product_name_zh": "陶瓷马克杯",
        "product_name_ms": "Mug seramik",
        "variant_label_en": "Colour: Red",
        "variant_label_zh": "颜色：红色",
        "variant_label_ms": "Warna: Merah",
        "unit_price_sen": 1000,
        "quantity": 2,
        "line_subtotal_sen": 2000,
    }
    return values | overrides


def _unit_values(order_item_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_item_id": order_item_id,
        "unit_index": 0,
        "original_price_sen": 1000,
        "coupon_discount_sen": 100,
        "points_discount": 120,
        "cash_paid_sen": 780,
        "points_earned": 8,
    }
    return values | overrides


def _recipient_values(order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order_id,
        "name": "Demo Buyer",
        "phone": "+60123456789",
        "country_code": "MY",
        "region": "MY-10",
        "address": "1 Jalan Demo, Petaling Jaya",
        "postal_code": "47300",
    }
    return values | overrides


def _attempt_values(order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order_id,
        "method": "demo_card",
        "result": "succeeded",
        "idempotency_key": "pay-key-1",
        "request_fingerprint": FINGERPRINT,
        "created_at": CREATED + timedelta(minutes=3),
    }
    return values | overrides


def _event_values(order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "order_id": order_id,
        "from_status": None,
        "to_status": STATUS_AWAITING_PAYMENT,
        "actor_type": ACTOR_GUEST,
        "created_at": CREATED,
    }
    return values | overrides


def _order(session: Session, **overrides: Any) -> Order:
    return _add(session, Order(**_order_values(**overrides)))


def _item(session: Session, order: Order, **overrides: Any) -> OrderItem:
    return _add(session, OrderItem(**_item_values(order.id, **overrides)))


def _variant(session: Session) -> ProductVariant:
    category = _add(session, Category(slug="kitchen", name_en="Kitchen", is_active=True))
    product = _add(
        session,
        Product(category_id=category.id, slug="ceramic-mug", name_en="Mug", is_active=True),
    )
    variant = ProductVariant(
        product_id=product.id,
        sku="MUG-RED",
        price_sen=1000,
        daily_initial_stock=10,
        available_stock=10,
        is_active=True,
    )
    return _add(session, variant)


def _full_order(session: Session) -> Order:
    """第 4 条算例的整张订单：两行、三件分摊、收货资料、一次成功的模拟支付、两条事件。"""
    variant = _variant(session)
    order = _order(session)
    item_a = _item(session, order, variant_id=variant.id)
    item_b = _item(
        session,
        order,
        line_index=1,
        sku="TOTE-STD",
        product_name_en="Canvas tote bag",
        product_name_zh="帆布托特包",
        product_name_ms="Beg tote kanvas",
        variant_label_en="",
        variant_label_zh="",
        variant_label_ms="",
        unit_price_sen=500,
        quantity=1,
        line_subtotal_sen=500,
    )
    _add(session, OrderItemUnit(**_unit_values(item_a.id)))
    _add(session, OrderItemUnit(**_unit_values(item_a.id, unit_index=1, points_earned=7)))
    _add(
        session,
        OrderItemUnit(
            **_unit_values(
                item_b.id,
                original_price_sen=500,
                coupon_discount_sen=50,
                points_discount=60,
                cash_paid_sen=390,
                points_earned=4,
            )
        ),
    )
    _add(session, OrderRecipient(**_recipient_values(order.id)))
    _add(session, PaymentAttempt(**_attempt_values(order.id)))
    _add(session, OrderEvent(**_event_values(order.id)))
    _add(
        session,
        OrderEvent(
            **_event_values(
                order.id,
                from_status=STATUS_AWAITING_PAYMENT,
                to_status=STATUS_PAID,
                created_at=CREATED + timedelta(minutes=3),
            )
        ),
    )
    return order


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {
        key: value.isoformat(sep=" ") if isinstance(value, datetime) else value
        for key, value in values.items()
    }
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in model.__table__.constraints:
            if not isinstance(constraint, CheckConstraint):
                continue
            sql = text(f"SELECT ({constraint.sqltext}) FROM (SELECT {columns})")
            if connection.execute(sql, params).scalar_one() == 0:
                failing.append(str(constraint.name))
    return failing


def _assert_check_rejects(
    session: Session, model: type[Base], constraint: str, values: dict[str, Any]
) -> None:
    failing = _failing_checks(model, values)
    assert constraint in failing
    pattern = r"CHECK constraint failed: (?:" + "|".join(failing) + r")\b"

    with pytest.raises(IntegrityError, match=pattern):
        _add(session, model(**values))


def test_valid_order_and_all_children_are_accepted(session: Session) -> None:
    """「Order / OrderItem」「OrderRecipient」「PaymentAttempt / OrderEvent」：
    第 4 条算例的整张已支付订单、两行、三件分摊、收货资料、模拟支付与事件都写得进去。
    这是下面各条拒绝测试的对照：同样的建表与外键检查下，合法数据写得进去，
    且求值辅助函数对它们不报任何约束不成立。
    """
    order = _full_order(session)

    line_indexes = session.scalars(select(OrderItem.line_index).order_by(OrderItem.line_index))
    assert line_indexes.all() == [0, 1]
    units = session.scalars(select(OrderItemUnit)).all()
    assert [unit.cash_paid_sen for unit in units] == [780, 780, 390]
    assert sum(unit.points_earned for unit in units) == order.points_earned
    assert session.scalars(select(OrderRecipient.order_id)).all() == [order.id]
    assert len(session.scalars(select(PaymentAttempt)).all()) == 1
    assert len(session.scalars(select(OrderEvent)).all()) == 2

    assert _failing_checks(Order, _order_values()) == []
    assert _failing_checks(OrderItem, _item_values(order.id)) == []
    assert _failing_checks(OrderItemUnit, _unit_values(1)) == []
    assert _failing_checks(OrderRecipient, _recipient_values(order.id)) == []
    assert _failing_checks(PaymentAttempt, _attempt_values(order.id)) == []
    assert _failing_checks(OrderEvent, _event_values(order.id)) == []


def test_awaiting_and_cancelled_orders_without_paid_at_are_accepted(session: Session) -> None:
    """「订单与退款状态」：下单后待支付、超时或取消的订单没有支付时间，照常写入。"""
    _order(session, status=STATUS_AWAITING_PAYMENT, paid_at=None)
    _order(
        session,
        order_number="0123456789ABCDEG",
        idempotency_key="order-key-2",
        status=STATUS_CANCELLED,
        paid_at=None,
    )

    assert len(session.scalars(select(Order)).all()) == 2


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「最终应付不得低于运费」与应付公式：应付不等于商品小计减券减积分加运费",
            "ck_orders_total_formula",
            {"total_sen": 2751},
        ),
        (
            "「二者均不抵运费，最终应付不得低于运费」：应付低于运费",
            "ck_orders_total_covers_shipping",
            {
                "subtotal_sen": 500,
                "coupon_discount_sen": 600,
                "points_redeemed": 0,
                "total_sen": 700,
            },
        ),
        (
            "「优惠券先抵商品额，积分再抵剩余商品额」：券折扣加积分抵扣超过商品小计",
            "ck_orders_discounts_within_subtotal",
            {"coupon_discount_sen": 2000, "points_redeemed": 600, "total_sen": 700},
        ),
        (
            "「价格/优惠/积分/运费/应付快照」不为负：商品小计为负",
            "ck_orders_subtotal_sen_non_negative",
            {
                "subtotal_sen": -1,
                "coupon_discount_sen": 0,
                "points_redeemed": 0,
                "total_sen": 799,
            },
        ),
        (
            "「价格/优惠/积分/运费/应付快照」不为负：券折扣为负",
            "ck_orders_coupon_discount_sen_non_negative",
            {"coupon_discount_sen": -100, "total_sen": 3100},
        ),
        (
            "「价格/优惠/积分/运费/应付快照」不为负：积分抵扣为负",
            "ck_orders_points_redeemed_non_negative",
            {"points_redeemed": -100, "total_sen": 3150},
        ),
        (
            "「价格/优惠/积分/运费/应付快照」不为负：运费为负",
            "ck_orders_shipping_fee_sen_non_negative",
            {"shipping_fee_sen": -100, "total_sen": 1850},
        ),
        (
            "「价格/优惠/积分/运费/应付快照」不为负：应付为负",
            "ck_orders_total_sen_non_negative",
            {
                "subtotal_sen": 0,
                "coupon_discount_sen": 0,
                "points_redeemed": 0,
                "shipping_fee_sen": -1,
                "total_sen": -1,
            },
        ),
        (
            "「余额不得小于零」的积分一律不为负：获得积分为负",
            "ck_orders_points_earned_non_negative",
            {"points_earned": -1},
        ),
        (
            "「下单：awaiting_demo_payment……demo_paid」只有六种状态：未知状态",
            "ck_orders_status_valid",
            {"status": "demo_refunded"},
        ),
        (
            "「创建与支付时间」：待支付订单却有支付时间",
            "ck_orders_unpaid_status_no_paid_at",
            {"status": STATUS_AWAITING_PAYMENT},
        ),
        (
            "「已模拟支付订单不走取消」：已取消订单却有支付时间",
            "ck_orders_unpaid_status_no_paid_at",
            {"status": STATUS_CANCELLED},
        ),
        (
            "「创建与支付时间」：已发货订单却无支付时间",
            "ck_orders_paid_status_has_paid_at",
            {"status": STATUS_SHIPPED, "paid_at": None},
        ),
        (
            "「创建与支付时间」：已支付订单却无支付时间",
            "ck_orders_paid_status_has_paid_at",
            {"status": STATUS_PAID, "paid_at": None},
        ),
        (
            "「不可猜测的订单号」固定 16 位：订单号长度不对",
            "ck_orders_order_number_length",
            {"order_number": "0123456789ABCDE"},
        ),
        (
            "「幂等键和数据库唯一约束」：幂等键为空串",
            "ck_orders_idempotency_key_not_empty",
            {"idempotency_key": ""},
        ),
        (
            "「同键不同内容报冲突」须比对请求指纹：请求指纹为空串",
            "ck_orders_request_fingerprint_length",
            {"request_fingerprint": ""},
        ),
        (
            "「同键不同内容报冲突」须比对请求指纹：请求指纹不是 64 位",
            "ck_orders_request_fingerprint_length",
            {"request_fingerprint": "0f" * 31 + "0"},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_order_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """订单表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, Order, constraint, _order_values(**overrides))


@pytest.mark.parametrize(
    "column",
    ["payment_expires_at", "idempotency_key", "request_fingerprint"],
)
def test_order_required_columns_reject_null(session: Session, column: str) -> None:
    """「幂等键」「同键不同内容报冲突」与「15 分钟未支付自动取消」：幂等键、请求指纹与
    支付到期时间都不能缺，否则重试无从比对，超时取消与支付页倒计时无从计算。
    """
    with pytest.raises(IntegrityError, match=f"NOT NULL constraint failed: orders.{column}"):
        _order(session, **{column: None})


def test_duplicate_order_number_is_rejected(session: Session) -> None:
    """「查单先按高熵订单号定位」：订单号全表唯一，一个订单号只能定位一张订单。"""
    _order(session)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: orders.order_number"):
        _order(session, idempotency_key="order-key-2")


def test_duplicate_order_idempotency_key_is_rejected(session: Session) -> None:
    """「下单……均使用幂等键和数据库唯一约束」：同一幂等键不能建出第二张订单。"""
    _order(session)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: orders.idempotency_key"):
        _order(session, order_number="0123456789ABCDEG")


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「商品名称、规格、数量、单价」快照：行小计不等于单价乘件数",
            "ck_order_items_line_subtotal_formula",
            {"line_subtotal_sen": 1999},
        ),
        (
            "「价格与数量不得为负」且每行件数为 1–99：件数为 0",
            "ck_order_items_quantity_positive",
            {"quantity": 0, "line_subtotal_sen": 0},
        ),
        (
            "「价格与数量不得为负」：单价为负",
            "ck_order_items_unit_price_sen_non_negative",
            {"unit_price_sen": -1, "quantity": 1, "line_subtotal_sen": -1},
        ),
        (
            "「稳定的订单行序」从 0 起：行序为负",
            "ck_order_items_line_index_non_negative",
            {"line_index": -1},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_order_item_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """订单行表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    order = _order(session)
    _assert_check_rejects(session, OrderItem, constraint, _item_values(order.id, **overrides))


def test_duplicate_line_index_in_one_order_is_rejected(session: Session) -> None:
    """「将商品按稳定的订单行序与件序展开」：同一订单内行序唯一，否则展开顺序不确定。"""
    order = _order(session)
    _item(session, order)
    message = "UNIQUE constraint failed: order_items.order_id, order_items.line_index"

    with pytest.raises(IntegrityError, match=message):
        _item(session, order, sku="TOTE-STD")


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「每件现金实付 = 原价 − 分摊券额 − 分摊积分额」：现金实付不等于公式",
            "ck_order_item_units_cash_paid_formula",
            {"cash_paid_sen": 781},
        ),
        (
            "「逐件优惠、积分、现金实付、获得积分的分摊快照」不为负：原价为负",
            "ck_order_item_units_original_price_sen_non_negative",
            {
                "original_price_sen": -1,
                "coupon_discount_sen": 0,
                "points_discount": 0,
                "cash_paid_sen": -1,
            },
        ),
        (
            "「逐件优惠……分摊快照」不为负：分摊券额为负",
            "ck_order_item_units_coupon_discount_sen_non_negative",
            {"coupon_discount_sen": -1, "cash_paid_sen": 881},
        ),
        (
            "「逐件……积分……分摊快照」不为负：分摊积分额为负",
            "ck_order_item_units_points_discount_non_negative",
            {"points_discount": -1, "cash_paid_sen": 901},
        ),
        (
            "「逐件……现金实付……分摊快照」不为负：现金实付为负",
            "ck_order_item_units_cash_paid_sen_non_negative",
            {
                "original_price_sen": 100,
                "coupon_discount_sen": 101,
                "points_discount": 0,
                "cash_paid_sen": -1,
            },
        ),
        (
            "「逐件……获得积分的分摊快照」不为负：获得积分为负",
            "ck_order_item_units_points_earned_non_negative",
            {"points_earned": -1},
        ),
        (
            "「稳定的订单行序与件序」从 0 起：件序为负",
            "ck_order_item_units_unit_index_non_negative",
            {"unit_index": -1},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_order_item_unit_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """逐件分摊快照表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    item = _item(session, _order(session))
    _assert_check_rejects(session, OrderItemUnit, constraint, _unit_values(item.id, **overrides))


def test_duplicate_unit_index_in_one_item_is_rejected(session: Session) -> None:
    """「将商品按稳定的订单行序与件序展开」：同一订单行内件序唯一。"""
    item = _item(session, _order(session))
    _add(session, OrderItemUnit(**_unit_values(item.id)))
    message = (
        "UNIQUE constraint failed: order_item_units.order_item_id, order_item_units.unit_index"
    )

    with pytest.raises(IntegrityError, match=message):
        _add(session, OrderItemUnit(**_unit_values(item.id)))


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「国家、地区」：马来西亚的地区为空",
            "ck_order_recipients_region_matches_country",
            {"region": None},
        ),
        (
            "「国家、地区」：马来西亚的地区不是州属代码",
            "ck_order_recipients_region_matches_country",
            {"region": "Selangor"},
        ),
        (
            "「国家、地区」：马来西亚的地区长 5 位却不以 MY- 开头",
            "ck_order_recipients_region_matches_country",
            {"region": "SG-01"},
        ),
        (
            "「国家、地区」：马来西亚的地区以 MY- 开头却长度不对",
            "ck_order_recipients_region_matches_country",
            {"region": "MY-1"},
        ),
        (
            "「规范化 E.164 电话」：电话不以加号开头",
            "ck_order_recipients_phone_format",
            {"phone": "60123456789"},
        ),
        (
            "「规范化 E.164 电话」：电话超过 16 个字符",
            "ck_order_recipients_phone_format",
            {"phone": "+6012345678901234"},
        ),
        (
            "「国家」两位国家代码：国家代码长度不对",
            "ck_order_recipients_country_code_length",
            {"country_code": "SGP", "region": None},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_recipient_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """收货资料表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    order = _order(session)
    values = _recipient_values(order.id, **overrides)
    _assert_check_rejects(session, OrderRecipient, constraint, values)


@pytest.mark.parametrize(
    ("country_code", "region"),
    [("SG", None), ("TH", "Bangkok"), ("US", "California")],
)
def test_other_country_region_may_be_empty_or_free_text(
    session: Session, country_code: str, region: str | None
) -> None:
    """「仅收集姓名、电话、国家、地区、地址及邮编」：马来西亚以外的国家地区可空，
    也可写任意自由文本，数据库不套用州属代码规则。
    """
    order = _order(session)
    values = _recipient_values(order.id, country_code=country_code, region=region)
    _add(session, OrderRecipient(**values))

    assert session.scalars(select(OrderRecipient.region)).one() == region


def test_second_recipient_for_one_order_is_rejected(session: Session) -> None:
    """「OrderRecipient……引用所属订单」：每张订单恰好一条收货资料，
    查单比对的号码不能有两个。
    """
    order = _order(session)
    _add(session, OrderRecipient(**_recipient_values(order.id)))
    message = "UNIQUE constraint failed: order_recipients.order_id"

    with pytest.raises(IntegrityError, match=message):
        _add(session, OrderRecipient(**_recipient_values(order.id, phone="+6587654321")))


def test_recipient_phone_has_lookup_index() -> None:
    """「为电话建立查询索引」：收货电话上有普通（非唯一）索引，
    同一号码可出现在多张订单上。
    """
    indexes = {index.name: index for index in OrderRecipient.__table__.indexes}

    assert [column.name for column in indexes["ix_order_recipients_phone"].columns] == ["phone"]
    assert not indexes["ix_order_recipients_phone"].unique


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「模拟支付选择」只有演示信用卡/借记卡、网上银行、电子钱包：未知支付方式",
            "ck_payment_attempts_method_valid",
            {"method": "demo_paypal"},
        ),
        (
            "「模拟支付……结果」只有成功或失败：未知结果",
            "ck_payment_attempts_result_valid",
            {"result": "pending"},
        ),
        (
            "「模拟支付……幂等键」：幂等键为空串",
            "ck_payment_attempts_idempotency_key_not_empty",
            {"idempotency_key": ""},
        ),
        (
            "「同键不同内容报冲突」须比对请求指纹：请求指纹为空串",
            "ck_payment_attempts_request_fingerprint_length",
            {"request_fingerprint": ""},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_payment_attempt_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """模拟支付尝试表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    order = _order(session)
    values = _attempt_values(order.id, **overrides)
    _assert_check_rejects(session, PaymentAttempt, constraint, values)


@pytest.mark.parametrize("column", ["idempotency_key", "request_fingerprint"])
def test_payment_attempt_required_columns_reject_null(session: Session, column: str) -> None:
    """「模拟支付……均使用幂等键和数据库唯一约束」：幂等键与请求指纹都不能缺。"""
    order = _order(session)
    message = f"NOT NULL constraint failed: payment_attempts.{column}"

    with pytest.raises(IntegrityError, match=message):
        _add(session, PaymentAttempt(**_attempt_values(order.id, **{column: None})))


def test_duplicate_payment_idempotency_key_is_rejected(session: Session) -> None:
    """「模拟支付……均使用幂等键和数据库唯一约束」：同一幂等键不能记两次支付尝试，
    即使是不同订单。
    """
    first = _order(session)
    second = _order(session, order_number="0123456789ABCDEG", idempotency_key="order-key-2")
    _add(session, PaymentAttempt(**_attempt_values(first.id)))
    message = "UNIQUE constraint failed: payment_attempts.idempotency_key"

    with pytest.raises(IntegrityError, match=message):
        _add(session, PaymentAttempt(**_attempt_values(second.id, result="failed")))


def test_failed_payment_attempt_leaves_order_awaiting(session: Session) -> None:
    """「模拟支付失败仍停在此状态并记录失败尝试」：待支付订单可记多次失败尝试，
    订单仍是待支付、无支付时间。
    """
    order = _order(session, status=STATUS_AWAITING_PAYMENT, paid_at=None)
    _add(session, PaymentAttempt(**_attempt_values(order.id, result="failed")))
    _add(
        session,
        PaymentAttempt(
            **_attempt_values(
                order.id, method="demo_ewallet", result="failed", idempotency_key="pay-key-2"
            )
        ),
    )

    assert len(session.scalars(select(PaymentAttempt)).all()) == 2
    assert order.status == STATUS_AWAITING_PAYMENT


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「状态变更、操作者类别」只有 guest、member、admin、system：未知操作者类别",
            "ck_order_events_actor_type_valid",
            {"actor_type": "customer"},
        ),
        (
            "「状态变更」只用六种状态：未知迁移后状态",
            "ck_order_events_to_status_valid",
            {"from_status": STATUS_PAID, "to_status": "demo_refunded"},
        ),
        (
            "「状态变更」只用六种状态：未知迁移前状态",
            "ck_order_events_from_status_valid",
            {"from_status": "demo_refunded", "to_status": STATUS_PACKED},
        ),
        (
            "「下单：awaiting_demo_payment」：下单事件不能有迁移前状态",
            "ck_order_events_placement_has_no_from_status",
            {"from_status": STATUS_AWAITING_PAYMENT},
        ),
        (
            "「状态变更」：非下单事件必须有迁移前状态",
            "ck_order_events_placement_has_no_from_status",
            {"to_status": STATUS_PACKED, "actor_type": ACTOR_ADMIN},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_order_event_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """订单事件表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    order = _order(session)
    _assert_check_rejects(session, OrderEvent, constraint, _event_values(order.id, **overrides))


def test_order_event_has_no_free_text_columns() -> None:
    """「事件不写收货资料原文」：事件表只有订单、前后状态、操作者类别与时间，
    没有可写入任意文字的列。
    """
    columns = {column.name for column in OrderEvent.__table__.columns}

    assert columns == {"id", "order_id", "from_status", "to_status", "actor_type", "created_at"}


@pytest.mark.parametrize(
    "child",
    ["item", "recipient", "payment_attempt", "event"],
)
def test_deleting_order_with_children_is_rejected(session: Session, child: str) -> None:
    """「收货资料原文与订单、商品和状态历史一并长期保存，不删除」：订单有任何子行时
    外键 RESTRICT 拒绝删除订单，不级联删掉收货资料、订单行、支付尝试或事件。
    """
    order = _order(session)
    if child == "item":
        _item(session, order)
    elif child == "recipient":
        _add(session, OrderRecipient(**_recipient_values(order.id)))
    elif child == "payment_attempt":
        _add(session, PaymentAttempt(**_attempt_values(order.id)))
    else:
        _add(session, OrderEvent(**_event_values(order.id)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(Order).where(Order.id == order.id))


def test_deleting_item_with_units_is_rejected(session: Session) -> None:
    """「整单及逐件分摊快照不可因后来改价而重算」：订单行有逐件分摊时不能删订单行。"""
    item = _item(session, _order(session))
    _add(session, OrderItemUnit(**_unit_values(item.id)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(OrderItem).where(OrderItem.id == item.id))


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (OrderItem, _item_values(999)),
        (OrderRecipient, _recipient_values(999)),
        (PaymentAttempt, _attempt_values(999)),
        (OrderEvent, _event_values(999)),
        (OrderItemUnit, _unit_values(999)),
    ],
    ids=["item", "recipient", "payment_attempt", "event", "unit"],
)
def test_child_rows_must_reference_an_existing_parent(
    session: Session, model: type[Base], values: dict[str, Any]
) -> None:
    """「OrderRecipient……引用所属订单」：每张子表的行都必须属于一张存在的订单
    （逐件分摊属于一条存在的订单行）。
    """
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, model(**values))


def test_deleting_variant_keeps_order_item_snapshot(session: Session) -> None:
    """「整单及逐件分摊快照不可因后来改价而重算」「商品名称、规格、数量、单价」快照：
    订单行引用的 SKU 规格被删除后，订单行保留，规格外键置空，快照不变。
    """
    order = _full_order(session)
    variant_id = session.scalars(select(ProductVariant.id)).one()
    before = session.scalars(select(OrderItem.variant_id).order_by(OrderItem.line_index))
    assert before.all() == [variant_id, None]

    session.execute(delete(ProductVariant).where(ProductVariant.id == variant_id))
    session.expire_all()

    items = session.scalars(select(OrderItem).order_by(OrderItem.line_index)).all()
    assert [item.variant_id for item in items] == [None, None]
    assert [item.order_id for item in items] == [order.id, order.id]
    assert items[0].sku == "MUG-RED"
    assert items[0].product_name_en == "Ceramic mug"
    assert items[0].unit_price_sen == 1000
    assert len(session.scalars(select(OrderItemUnit)).all()) == 3

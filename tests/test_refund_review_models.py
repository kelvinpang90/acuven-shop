"""退款申请的审核人、审核理由、审核幂等键与审核请求指纹的数据库约束（SHOP-TASK-039）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 RefundRequest / RefundLine 一行
（「申请状态……审核人及理由」）、「订单与退款状态」（「requested → 管理员 approved 或
rejected」）与「失败、并发与重试」第 1 条（「退款申请/审核均使用幂等键和数据库唯一约束」），
以及 docs/HANDOFF.md 0.33 记录的 Kelvin 2026-10-06 退款审核决定（下称「Kelvin 2026-10-06」：
拒绝时审核理由必填，批准时可空，批准时没有理由存空值；审核幂等键与请求指纹存在退款申请表）。
每条测试（参数化的测试是每个用例）写明它守住的设计原句或 Kelvin 的哪一项决定。

先写入合法的 requested、approved（理由为空与非空各一）与 rejected 申请作对照，再逐个写反例。
检查约束的反例先在独立的 SQLite 连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，
再断言写入被拒、且报出的是不成立的约束之一（与 tests/test_admin_models.py 相同）。
用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
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
from app.models import AdminAccount, Order, RefundRequest
from app.models.order import ACTOR_GUEST, STATUS_SHIPPED
from app.models.refund import (
    REFUND_APPROVED,
    REFUND_REJECTED,
    REFUND_REQUESTED,
    REVIEW_REASON_MAX_LENGTH,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 6, 2, 0)
REVIEWED = CREATED + timedelta(hours=1)
FINGERPRINT = "ab" * 32
REVIEW_FINGERPRINT = "cd" * 32

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


def _account(session: Session) -> AdminAccount:
    return _add(
        session,
        AdminAccount(
            username="shop_admin",
            password_hash="not-a-real-hash",
            created_at=CREATED,
            password_updated_at=CREATED,
        ),
    )


def _order(session: Session) -> Order:
    """一张已模拟发货的游客订单（只建订单本身，申请表的外键只指向它）。"""
    paid_at = CREATED - timedelta(minutes=3)
    return _add(
        session,
        Order(
            order_number="A" * 16,
            status=STATUS_SHIPPED,
            subtotal_sen=3000,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=3800,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key="order-key",
            request_fingerprint=FINGERPRINT,
            created_at=paid_at - timedelta(minutes=2),
            payment_expires_at=paid_at + timedelta(minutes=13),
            paid_at=paid_at,
        ),
    )


def _request_values(order_id: int, key: str = "refund-key", **overrides: Any) -> dict[str, Any]:
    """审核中的申请：审核的四列都为空。"""
    values: dict[str, Any] = {
        "order_id": order_id,
        "status": REFUND_REQUESTED,
        "actor_type": ACTOR_GUEST,
        "idempotency_key": key,
        "request_fingerprint": FINGERPRINT,
        "amount_sen": 2700,
        "created_at": CREATED,
        "reviewed_at": None,
        "reviewer_admin_id": None,
        "review_reason": None,
        "review_idempotency_key": None,
        "review_fingerprint": None,
    }
    return values | overrides


def _reviewed_values(
    order_id: int, admin_id: int, status: str, key: str = "refund-key", **overrides: Any
) -> dict[str, Any]:
    """已审核的申请：有审核时间、审核人、审核幂等键与审核请求指纹；拒绝时另有理由。"""
    values = _request_values(
        order_id,
        key,
        status=status,
        reviewed_at=REVIEWED,
        reviewer_admin_id=admin_id,
        review_reason="Item was used" if status == REFUND_REJECTED else None,
        review_idempotency_key=f"review-{key}",
        review_fingerprint=REVIEW_FINGERPRINT,
    )
    return values | overrides


def _check_constraints() -> list[CheckConstraint]:
    table = RefundRequest.__table__
    constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    for column in table.columns:
        constraints += [c for c in column.constraints if isinstance(c, CheckConstraint)]
    return constraints


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _failing_checks(values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值申请表的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints():
            sql = text(f"SELECT ({constraint.sqltext}) FROM (SELECT {columns})")
            if connection.execute(sql, params).scalar_one() == 0:
                failing.append(str(constraint.name))
    return failing


def _assert_check_rejects(session: Session, constraint: str, values: dict[str, Any]) -> None:
    failing = _failing_checks(values)
    assert constraint in failing
    pattern = r"CHECK constraint failed: (?:" + "|".join(failing) + r")\b"

    with pytest.raises(IntegrityError, match=pattern):
        _add(session, RefundRequest(**values))


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_requested_approved_and_rejected_requests_are_accepted(session: Session) -> None:
    """「requested → 管理员 approved 或 rejected」与「审核人及理由」；Kelvin 2026-10-06「拒绝时
    审核理由必填，批准时可空」「批准时没有理由存空值」：审核中的申请（两笔，审核幂等键都为空，
    唯一约束允许多个空值）、没有理由与有理由的批准、有理由的拒绝都写得进去。这是下面各条拒绝
    测试的对照：同样的建表与外键检查下，合法数据写得进去，且求值辅助函数对它们不报任何
    约束不成立。
    """
    admin = _account(session)
    order = _order(session)
    rows = [
        _request_values(order.id, "requested-1"),
        _request_values(order.id, "requested-2"),
        _reviewed_values(order.id, admin.id, REFUND_APPROVED, "approved-no-reason"),
        _reviewed_values(
            order.id,
            admin.id,
            REFUND_APPROVED,
            "approved-reason",
            review_reason="Approved after checking photos",
        ),
        _reviewed_values(order.id, admin.id, REFUND_REJECTED, "rejected"),
    ]
    for values in rows:
        assert _failing_checks(values) == []
        _add(session, RefundRequest(**values))

    stored = session.execute(
        select(RefundRequest.status, RefundRequest.review_reason).order_by(RefundRequest.id)
    ).all()
    assert [tuple(row) for row in stored] == [
        (REFUND_REQUESTED, None),
        (REFUND_REQUESTED, None),
        (REFUND_APPROVED, None),
        (REFUND_APPROVED, "Approved after checking photos"),
        (REFUND_REJECTED, "Item was used"),
    ]


def test_review_columns_lengths() -> None:
    """Kelvin 2026-10-06「去掉首尾空白后 1 到 500 个字符」：理由的上限只由列长 500 保证
    （MySQL 的 LENGTH 按字节计，库里不加上限检查）；「失败、并发与重试」第 1 条的审核幂等键
    最长 64 个字符，审核请求指纹是长 64 的 SHA-256 十六进制。四列都可空（审核中时为空）。
    """
    columns = RefundRequest.__table__.c

    assert REVIEW_REASON_MAX_LENGTH == 500
    assert columns.review_reason.type.length == 500
    assert columns.review_idempotency_key.type.length == 64
    assert columns.review_fingerprint.type.length == 64
    assert all(
        columns[name].nullable
        for name in (
            "reviewer_admin_id",
            "review_reason",
            "review_idempotency_key",
            "review_fingerprint",
        )
    )


# ---------------------------------------------------------------------------
# 检查约束
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "status", "overrides"),
    [
        pytest.param(
            "「requested → 管理员 approved 或 rejected」：审核中的申请还没有审核人",
            "ck_refund_requests_requested_has_no_review",
            REFUND_REQUESTED,
            {"reviewer_admin_id": "admin"},
            id="requested-reviewer",
        ),
        pytest.param(
            "「审核人及理由」在审核时才写：审核中的申请没有审核理由",
            "ck_refund_requests_requested_has_no_review",
            REFUND_REQUESTED,
            {"review_reason": "Too early"},
            id="requested-reason",
        ),
        pytest.param(
            "「失败、并发与重试」第 1 条的审核幂等键在审核时才写：审核中的申请没有审核幂等键",
            "ck_refund_requests_requested_has_no_review",
            REFUND_REQUESTED,
            {"review_idempotency_key": "review-key"},
            id="requested-review-key",
        ),
        pytest.param(
            "Kelvin 2026-10-06「审核幂等键与请求指纹存在退款申请表」，在审核时才写：审核中的申请"
            "没有审核请求指纹",
            "ck_refund_requests_requested_has_no_review",
            REFUND_REQUESTED,
            {"review_fingerprint": REVIEW_FINGERPRINT},
            id="requested-review-fingerprint",
        ),
        pytest.param(
            "「管理员 approved」与「审核人及理由」：批准的申请必须有审核人",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_APPROVED,
            {"reviewer_admin_id": None},
            id="approved-no-reviewer",
        ),
        pytest.param(
            "「退款申请/审核均使用幂等键和数据库唯一约束」：批准的申请必须有审核幂等键",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_APPROVED,
            {"review_idempotency_key": None},
            id="approved-no-review-key",
        ),
        pytest.param(
            "「同键不同内容报冲突」须比较指纹：批准的申请必须有审核请求指纹",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_APPROVED,
            {"review_fingerprint": None},
            id="approved-no-review-fingerprint",
        ),
        pytest.param(
            "「管理员 rejected」与「审核人及理由」：拒绝的申请必须有审核人",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_REJECTED,
            {"reviewer_admin_id": None},
            id="rejected-no-reviewer",
        ),
        pytest.param(
            "「退款申请/审核均使用幂等键和数据库唯一约束」：拒绝的申请必须有审核幂等键",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_REJECTED,
            {"review_idempotency_key": None},
            id="rejected-no-review-key",
        ),
        pytest.param(
            "「同键不同内容报冲突」须比较指纹：拒绝的申请必须有审核请求指纹",
            "ck_refund_requests_reviewed_status_has_review",
            REFUND_REJECTED,
            {"review_fingerprint": None},
            id="rejected-no-review-fingerprint",
        ),
        pytest.param(
            "Kelvin 2026-10-06「拒绝时审核理由必填」：拒绝的申请没有理由",
            "ck_refund_requests_rejected_has_review_reason",
            REFUND_REJECTED,
            {"review_reason": None},
            id="rejected-no-reason",
        ),
        pytest.param(
            "Kelvin 2026-10-06「批准时没有理由存空值」（不存空串）：批准的理由为空串",
            "ck_refund_requests_review_reason_not_empty",
            REFUND_APPROVED,
            {"review_reason": ""},
            id="approved-empty-reason",
        ),
        pytest.param(
            "Kelvin 2026-10-06「拒绝时审核理由必填」「1 到 500 个字符」：拒绝的理由为空串",
            "ck_refund_requests_review_reason_not_empty",
            REFUND_REJECTED,
            {"review_reason": ""},
            id="rejected-empty-reason",
        ),
        pytest.param(
            "「退款申请/审核均使用幂等键」：审核幂等键为空串",
            "ck_refund_requests_review_idempotency_key_not_empty",
            REFUND_APPROVED,
            {"review_idempotency_key": ""},
            id="empty-review-key",
        ),
        pytest.param(
            "审核请求指纹是 SHA-256 十六进制（沿用 request_fingerprint_length）：长 63",
            "ck_refund_requests_review_fingerprint_length",
            REFUND_APPROVED,
            {"review_fingerprint": "c" * 63},
            id="review-fingerprint-63",
        ),
        pytest.param(
            "审核请求指纹是 SHA-256 十六进制（沿用 request_fingerprint_length）：长 65",
            "ck_refund_requests_review_fingerprint_length",
            REFUND_REJECTED,
            {"review_fingerprint": "c" * 65},
            id="review-fingerprint-65",
        ),
    ],
)
def test_review_check_constraints_reject(
    session: Session, design: str, constraint: str, status: str, overrides: dict[str, Any]
) -> None:
    """审核各列的每个检查约束至少一个反例；守住的设计原句或 Kelvin 的决定见每个用例的 design。

    审核人写成占位 "admin" 的用例换成真实管理员的 ID，使外键成立、只有检查约束能拒绝它。
    """
    assert design
    admin = _account(session)
    order = _order(session)
    if overrides.get("reviewer_admin_id") == "admin":
        overrides = overrides | {"reviewer_admin_id": admin.id}
    if status == REFUND_REQUESTED:
        values = _request_values(order.id, **overrides)
    else:
        values = _reviewed_values(order.id, admin.id, status, **overrides)

    _assert_check_rejects(session, constraint, values)


# ---------------------------------------------------------------------------
# 唯一约束与外键
# ---------------------------------------------------------------------------


def test_duplicate_review_idempotency_key_is_rejected(session: Session) -> None:
    """「退款申请/审核均使用幂等键和数据库唯一约束」与 Kelvin 2026-10-06「批准与拒绝都带幂等键
    并以数据库唯一约束保证」：另一笔申请的审核用了同一审核幂等键，被唯一约束拒绝。
    """
    admin = _account(session)
    order = _order(session)
    first = _reviewed_values(order.id, admin.id, REFUND_APPROVED, "first")
    _add(session, RefundRequest(**first))
    second = _reviewed_values(
        order.id,
        admin.id,
        REFUND_REJECTED,
        "second",
        review_idempotency_key=first["review_idempotency_key"],
    )
    message = "UNIQUE constraint failed: refund_requests.review_idempotency_key"

    with pytest.raises(IntegrityError, match=message):
        _add(session, RefundRequest(**second))


def test_reviewer_must_reference_an_existing_admin(session: Session) -> None:
    """「审核人」：审核人必须是存在的管理员账号。"""
    order = _order(session)
    values = _reviewed_values(order.id, 999, REFUND_APPROVED)

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, RefundRequest(**values))


def test_admin_referenced_as_reviewer_cannot_be_deleted(session: Session) -> None:
    """「审核人及理由」须一直可查：被申请引用为审核人的管理员账号不能物理删除（RESTRICT）。"""
    admin = _account(session)
    order = _order(session)
    _add(session, RefundRequest(**_reviewed_values(order.id, admin.id, REFUND_REJECTED)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(AdminAccount).where(AdminAccount.id == admin.id))

"""订单访问会话与授权两张表的约束，以及签发、校验授权、cookie 与 CSRF 的函数。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条（游客短期凭据与查单授权）
与第 7 条（日志）。每条测试（参数化的测试是每个用例）写明它守住的设计原句。

用 SQLite 内存库按模型建表，自建合法的游客订单；SQLite 默认不检查外键，每个连接都须打开。
检查约束的反例先在独立的 SQLite 连接上逐条求值该表的全部检查约束，断言目标约束确实不成立，
再断言写入被拒、且报出的是不成立的约束之一。非空约束的反例用 Core insert 显式写入 NULL：
ORM 会略过值为 None 的列、改用默认值。
"""

from __future__ import annotations

import hashlib
import inspect
import itertools
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Response
from sqlalchemy import (
    CheckConstraint,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.services.order_access as order_access
from app.db.base import Base
from app.models import Order, OrderAccessGrant, OrderAccessSession
from app.models.order import CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.order_access import SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP
from app.services.order_access import (
    COOKIE_NAME,
    csrf_token_for,
    issue_grant,
    set_order_access_cookie,
    verify_csrf,
    verify_grant,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

NOW = datetime(2026, 10, 1, 2, 0)
LIFETIME = timedelta(minutes=30)
TOKEN_HASH = "ab" * 32
FINGERPRINT = "0f" * 32
# 格式合法（43 个 URL 安全字符）但从未签发过的令牌。
UNKNOWN_TOKEN = "A" * 43
URL_SAFE_43 = re.compile(r"[A-Za-z0-9_-]{43}")
HEX_64 = re.compile(r"[0-9a-f]{64}")

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


_ORDER_SEQUENCE = itertools.count(1)


def _order(session: Session) -> Order:
    """待支付的游客订单；每次给不同的订单号与幂等键。"""
    n = next(_ORDER_SEQUENCE)
    return _add(
        session,
        Order(
            order_number=f"{n:016d}",
            status=STATUS_AWAITING_PAYMENT,
            subtotal_sen=2500,
            coupon_discount_sen=0,
            points_redeemed=0,
            shipping_fee_sen=800,
            total_sen=3300,
            points_earned=0,
            shipping_zone_code="MY-10",
            shipping_rate_version=1,
            idempotency_key=f"order-key-{n}",
            request_fingerprint=FINGERPRINT,
            created_at=NOW,
            payment_expires_at=NOW + timedelta(minutes=15),
            paid_at=None,
            member_id=None,
            claim_status=CLAIM_OPEN,
        ),
    )


def _session_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "token_hash": TOKEN_HASH,
        "created_at": NOW,
        "expires_at": NOW + LIFETIME,
        "revoked_at": None,
    }
    return values | overrides


def _grant_values(session_id: int, order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "session_id": session_id,
        "order_id": order_id,
        "scope": SCOPE_LOOKUP,
        "created_at": NOW,
        "expires_at": NOW + LIFETIME,
        "revoked_at": None,
    }
    return values | overrides


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。"""
    params = {key: _sql_param(value) for key, value in values.items()}
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


def _assert_null_rejected(
    session: Session, model: type[Base], column: str, values: dict[str, Any]
) -> None:
    message = f"NOT NULL constraint failed: {model.__table__.name}.{column}"

    with pytest.raises(IntegrityError, match=message):
        session.execute(insert(model).values(**(values | {column: None})))


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _stored_session(session: Session, token: str) -> OrderAccessSession:
    return session.scalars(
        select(OrderAccessSession).where(OrderAccessSession.token_hash == _sha256(token))
    ).one()


def _all_stored_text(session: Session) -> str:
    """两张表全部行的全部列值，拼成一个字符串。"""
    parts: list[str] = []
    for table in ("order_access_sessions", "order_access_grants"):
        for row in session.execute(text(f"SELECT * FROM {table}")):
            parts.extend(str(value) for value in row)
    return "\n".join(parts)


def _count_statements(session: Session) -> list[str]:
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", _record)
    return statements


# ---------------------------------------------------------------------------
# 两张表：对照与约束
# ---------------------------------------------------------------------------


def test_valid_session_and_grants_are_accepted(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权」：一个会话下两张订单的授权、同一订单的两种范围、
    已撤销的授权都写得进去。这是下面各条拒绝测试的对照。
    """
    first, second = _order(session), _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    _add(session, OrderAccessGrant(**_grant_values(access.id, first.id)))
    _add(
        session,
        OrderAccessGrant(**_grant_values(access.id, first.id, scope=SCOPE_GUEST_CHECKOUT)),
    )
    _add(
        session,
        OrderAccessGrant(**_grant_values(access.id, second.id, revoked_at=NOW + LIFETIME / 2)),
    )

    assert _count(session, OrderAccessGrant) == 3
    assert _failing_checks(OrderAccessSession, _session_values()) == []
    assert _failing_checks(OrderAccessGrant, _grant_values(1, 1)) == []


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「服务端校验……到期时间」：会话到期时间等于创建时间",
            "ck_order_access_sessions_expires_after_created",
            {"expires_at": NOW},
        ),
        (
            "「服务端校验……到期时间」：会话到期时间早于创建时间",
            "ck_order_access_sessions_expires_after_created",
            {"expires_at": NOW - timedelta(seconds=1)},
        ),
        (
            "「以服务端保存的会话实现」只存令牌的 SHA-256 摘要：摘要不是 64 个字符",
            "ck_order_access_sessions_token_hash_length",
            {"token_hash": "ab" * 31},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_session_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """会话表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, OrderAccessSession, constraint, _session_values(**overrides))


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「短期凭据与查单授权」只有这两种：未知范围",
            "ck_order_access_grants_scope_valid",
            {"scope": "refund"},
        ),
        (
            "「各自独立到期」：授权到期时间等于创建时间",
            "ck_order_access_grants_expires_after_created",
            {"expires_at": NOW},
        ),
        (
            "「各自独立到期」：授权到期时间早于创建时间",
            "ck_order_access_grants_expires_after_created",
            {"expires_at": NOW - timedelta(seconds=1)},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_grant_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """授权表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    order = _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    values = _grant_values(access.id, order.id, **overrides)
    _assert_check_rejects(session, OrderAccessGrant, constraint, values)


@pytest.mark.parametrize("column", ["token_hash", "created_at", "expires_at"])
def test_session_required_columns_reject_null(session: Session, column: str) -> None:
    """「服务端校验……到期时间并可撤销」：会话必须有摘要、创建与到期时间；只有撤销时间可空。"""
    _assert_null_rejected(session, OrderAccessSession, column, _session_values())


@pytest.mark.parametrize("column", ["session_id", "order_id", "scope", "created_at", "expires_at"])
def test_grant_required_columns_reject_null(session: Session, column: str) -> None:
    """「服务端校验所属订单、到期时间」：授权必须有会话、订单、范围与时间；空的范围会让
    范围检查约束放行，只能靠非空约束拒绝。
    """
    order = _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    _assert_null_rejected(session, OrderAccessGrant, column, _grant_values(access.id, order.id))


def test_duplicate_session_token_hash_is_rejected(session: Session) -> None:
    """「以服务端保存的会话实现」：按令牌摘要定位会话，同一摘要不能对应两个会话。"""
    _add(session, OrderAccessSession(**_session_values()))
    message = "UNIQUE constraint failed: order_access_sessions.token_hash"

    with pytest.raises(IntegrityError, match=message):
        _add(session, OrderAccessSession(**_session_values()))


def test_duplicate_grant_for_session_order_and_scope_is_rejected(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」：同一会话对同一订单、同一范围
    只有一条授权，到期时间不会分散在两行上。
    """
    order = _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    _add(session, OrderAccessGrant(**_grant_values(access.id, order.id)))
    message = (
        "UNIQUE constraint failed: order_access_grants.session_id, "
        "order_access_grants.order_id, order_access_grants.scope"
    )

    with pytest.raises(IntegrityError, match=message):
        _add(session, OrderAccessGrant(**_grant_values(access.id, order.id)))


@pytest.mark.parametrize("missing", ["order", "session"])
def test_grant_must_reference_existing_order_and_session(session: Session, missing: str) -> None:
    """「服务端校验所属订单」：授权必须属于一个存在的订单与一个存在的会话。"""
    order = _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    values = _grant_values(
        999 if missing == "session" else access.id,
        999 if missing == "order" else order.id,
    )

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, OrderAccessGrant(**values))


@pytest.mark.parametrize("parent", ["order", "session"])
def test_deleting_referenced_order_or_session_is_rejected(session: Session, parent: str) -> None:
    """「服务端……可撤销」：撤销写撤销时间而不删行；被授权引用的订单或会话不能物理删除，
    外键 RESTRICT 拒绝，不级联删掉授权。
    """
    order = _order(session)
    access = _add(session, OrderAccessSession(**_session_values()))
    _add(session, OrderAccessGrant(**_grant_values(access.id, order.id)))
    statement = (
        delete(Order).where(Order.id == order.id)
        if parent == "order"
        else delete(OrderAccessSession).where(OrderAccessSession.id == access.id)
    )

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(statement)


def test_grant_foreign_keys_are_restrict() -> None:
    """「服务端校验所属订单」：授权指向订单与会话的外键删除行为都写明为 RESTRICT。"""
    foreign_keys = sorted(OrderAccessGrant.__table__.foreign_keys, key=lambda fk: fk.parent.name)

    assert [(fk.parent.name, fk.column.table.name) for fk in foreign_keys] == [
        ("order_id", "orders"),
        ("session_id", "order_access_sessions"),
    ]
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys)


@pytest.mark.parametrize("model", [OrderAccessSession, OrderAccessGrant])
def test_only_revoked_at_is_nullable(model: type[Base]) -> None:
    """「服务端校验所属订单、到期时间并可撤销」：只有撤销时间可空，其余一律非空。"""
    nullable = {column.name for column in model.__table__.columns if column.nullable}

    assert nullable == {"revoked_at"}


def test_session_stores_no_token_ip_or_personal_data() -> None:
    """「不写入……日志、分析事件」与第 7 条「不记录……订单查询参数」：会话表只有令牌摘要与
    时间，没有令牌原文、IP、浏览器标识或任何个人资料的列。
    """
    columns = {column.name for column in OrderAccessSession.__table__.columns}

    assert columns == {"id", "token_hash", "created_at", "expires_at", "revoked_at"}


def test_grant_order_id_has_index() -> None:
    """「服务端校验所属订单……并可撤销」：按订单撤销授权须按订单 ID 查询，建普通索引。"""
    indexes = {index.name: index for index in OrderAccessGrant.__table__.indexes}
    index = indexes["ix_order_access_grants_order_id"]

    assert [column.name for column in index.columns] == ["order_id"]
    assert not index.unique


@pytest.mark.parametrize("model", [OrderAccessSession, OrderAccessGrant])
def test_tables_use_innodb_and_utf8mb4(model: type[Base]) -> None:
    """「服务端校验所属订单」靠外键，外键与事务要 InnoDB：两张表都显式 InnoDB、utf8mb4。"""
    assert model.__table__.kwargs == {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


# ---------------------------------------------------------------------------
# 签发
# ---------------------------------------------------------------------------


def test_first_issue_creates_session_storing_only_token_hash(session: Session) -> None:
    """「服务端只给当前浏览器发一个不可猜测、30 分钟有效且仅限该单的短期凭据」：没有 cookie 时
    新建会话；令牌 256 位随机、只含 URL 安全字符；库里只有它的 SHA-256 摘要，查不到令牌原文。
    """
    order = _order(session)

    issued = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert issued.new_token is not None
    assert URL_SAFE_43.fullmatch(issued.new_token)
    assert issued.expires_at == NOW + LIFETIME
    stored = _stored_session(session, issued.new_token)
    assert HEX_64.fullmatch(stored.token_hash)
    assert stored.created_at == NOW
    assert stored.expires_at == NOW + LIFETIME
    assert stored.revoked_at is None
    grant = session.scalars(select(OrderAccessGrant)).one()
    assert (grant.session_id, grant.order_id, grant.scope) == (
        stored.id,
        order.id,
        SCOPE_GUEST_CHECKOUT,
    )
    assert (grant.created_at, grant.expires_at, grant.revoked_at) == (NOW, NOW + LIFETIME, None)
    stored_text = _all_stored_text(session)
    assert issued.new_token not in stored_text
    assert issued.csrf_token not in stored_text


def test_fresh_sessions_get_different_tokens(session: Session) -> None:
    """「不可猜测」：两个浏览器各自新建会话，拿到的令牌不同。"""
    order = _order(session)

    first = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
    second = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)

    assert first.new_token != second.new_token
    assert _count(session, OrderAccessSession) == 2


def test_issue_does_not_commit(session: Session) -> None:
    """「服务端保存的会话」与订单在同一事务：签发不提交，调用方回滚时会话与授权一并消失。"""
    order = _order(session)
    issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    session.rollback()

    assert _count(session, OrderAccessSession) == 0
    assert _count(session, OrderAccessGrant) == 0


def test_issue_with_valid_cookie_reuses_session(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权」：带着有效 cookie 再签发沿用同一会话，
    不返回新令牌，CSRF 令牌不变。
    """
    first_order, second_order = _order(session), _order(session)
    first = issue_grant(session, None, first_order.id, SCOPE_GUEST_CHECKOUT, NOW)
    assert first.new_token is not None

    second = issue_grant(
        session, first.new_token, second_order.id, SCOPE_LOOKUP, NOW + timedelta(minutes=5)
    )

    assert second.new_token is None
    assert second.csrf_token == first.csrf_token
    assert _count(session, OrderAccessSession) == 1
    access = _stored_session(session, first.new_token)
    session_ids = session.scalars(select(OrderAccessGrant.session_id)).all()
    assert session_ids == [access.id, access.id]


@pytest.mark.parametrize("cookie_state", ["expired", "revoked", "malformed", "unknown"])
def test_issue_with_invalid_cookie_creates_new_session(session: Session, cookie_state: str) -> None:
    """「服务端校验……到期时间并可撤销」：cookie 对应的会话已到期、已撤销，或 cookie 格式不合法、
    查不到会话时，不沿用旧会话，新建会话并返回新令牌。
    """
    order = _order(session)
    old = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
    assert old.new_token is not None
    old_session = _stored_session(session, old.new_token)
    now = NOW + timedelta(minutes=1)
    cookie: str = old.new_token
    if cookie_state == "expired":
        now = NOW + LIFETIME
    elif cookie_state == "revoked":
        old_session.revoked_at = now
        session.flush()
    elif cookie_state == "malformed":
        cookie = old.new_token[:-1] + "!"
    else:
        cookie = UNKNOWN_TOKEN

    issued = issue_grant(session, cookie, order.id, SCOPE_LOOKUP, now)

    assert issued.new_token is not None
    assert issued.new_token not in {old.new_token, cookie}
    assert issued.csrf_token != old.csrf_token
    assert _count(session, OrderAccessSession) == 2
    new_session = _stored_session(session, issued.new_token)
    assert new_session.id != old_session.id
    assert old_session.expires_at == NOW + LIFETIME
    assert verify_grant(session, issued.new_token, order.id, SCOPE_LOOKUP, now)


def test_grants_for_two_orders_expire_independently(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」：同一会话下两张订单的授权，
    先签发的先到期，后签发的仍有效；会话到期时间跟着较晚的授权延长。
    """
    first_order, second_order = _order(session), _order(session)
    issued = issue_grant(session, None, first_order.id, SCOPE_LOOKUP, NOW)
    token = issued.new_token
    assert token is not None
    later = NOW + timedelta(minutes=20)
    second = issue_grant(session, token, second_order.id, SCOPE_LOOKUP, later)

    assert second.expires_at == later + LIFETIME
    assert _stored_session(session, token).expires_at == later + LIFETIME
    check_at = NOW + LIFETIME
    assert not verify_grant(session, token, first_order.id, SCOPE_LOOKUP, check_at)
    assert verify_grant(session, token, second_order.id, SCOPE_LOOKUP, check_at)


def test_reissue_extends_existing_grant_without_new_row(session: Session) -> None:
    """「30 分钟有效」：同一会话、订单与范围重复签发时把原授权的到期时间延长为当前时间加
    30 分钟、清空撤销时间，不新增行；创建时间不变。
    """
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
    token = issued.new_token
    assert token is not None
    grant = session.scalars(select(OrderAccessGrant)).one()
    grant.revoked_at = NOW + timedelta(minutes=5)
    session.flush()
    later = NOW + timedelta(minutes=10)

    again = issue_grant(session, token, order.id, SCOPE_LOOKUP, later)

    assert again.expires_at == later + LIFETIME
    assert _count(session, OrderAccessGrant) == 1
    session.refresh(grant)
    assert (grant.created_at, grant.expires_at, grant.revoked_at) == (
        NOW,
        later + LIFETIME,
        None,
    )
    assert verify_grant(session, token, order.id, SCOPE_LOOKUP, NOW + timedelta(minutes=35))


def test_session_expiry_keeps_the_later_time(session: Session) -> None:
    """「各自独立到期」：会话到期时间取它与新授权到期时间中较晚者，不被新授权缩短。"""
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
    token = issued.new_token
    assert token is not None
    access = _stored_session(session, token)
    access.expires_at = NOW + timedelta(hours=2)
    session.flush()

    issue_grant(session, token, order.id, SCOPE_GUEST_CHECKOUT, NOW + timedelta(minutes=1))

    assert access.expires_at == NOW + timedelta(hours=2)


def test_issue_rejects_aware_time_and_unknown_scope(session: Session) -> None:
    """「服务端校验……到期时间」：时间一律是不带时区的 UTC，带时区的拒绝；范围只有短期凭据
    与查单授权两种。拒绝时不写任何行。
    """
    order = _order(session)

    with pytest.raises(ValueError):
        issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW.replace(tzinfo=UTC))
    with pytest.raises(ValueError):
        issue_grant(session, None, order.id, "refund", NOW)

    assert _count(session, OrderAccessSession) == 0


# ---------------------------------------------------------------------------
# 校验授权
# ---------------------------------------------------------------------------


@pytest.fixture
def granted(session: Session) -> tuple[str, int, int]:
    """对第一张订单签发两种范围的授权；返回令牌、该订单与另一张订单的 ID。"""
    order, other = _order(session), _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
    assert issued.new_token is not None
    issue_grant(session, issued.new_token, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    return issued.new_token, order.id, other.id


def test_valid_grant_passes(session: Session, granted: tuple[str, int, int]) -> None:
    """「服务端校验所属订单、到期时间」：会话与授权都有效时通过，直到到期前一刻。"""
    token, order_id, _ = granted
    last_moment = NOW + LIFETIME - timedelta(seconds=1)

    assert verify_grant(session, token, order_id, SCOPE_LOOKUP, NOW)
    assert verify_grant(session, token, order_id, SCOPE_GUEST_CHECKOUT, last_moment)


def test_expired_grant_fails(session: Session, granted: tuple[str, int, int]) -> None:
    """「30 分钟授权……过期须重新查单」：到达到期时间即不通过。"""
    token, order_id, _ = granted

    assert not verify_grant(session, token, order_id, SCOPE_LOOKUP, NOW + LIFETIME)


def test_revoked_grant_fails(session: Session, granted: tuple[str, int, int]) -> None:
    """「服务端……可撤销」：撤销的授权不通过，同一订单另一范围的授权不受影响。"""
    token, order_id, _ = granted
    grant = session.scalars(
        select(OrderAccessGrant).where(OrderAccessGrant.scope == SCOPE_LOOKUP)
    ).one()
    grant.revoked_at = NOW + timedelta(minutes=1)
    session.flush()
    now = NOW + timedelta(minutes=2)

    assert not verify_grant(session, token, order_id, SCOPE_LOOKUP, now)
    assert verify_grant(session, token, order_id, SCOPE_GUEST_CHECKOUT, now)


@pytest.mark.parametrize("session_state", ["revoked", "expired"])
def test_grant_under_invalid_session_fails(
    session: Session, granted: tuple[str, int, int], session_state: str
) -> None:
    """「服务端校验……到期时间并可撤销」：会话已撤销或已到期时，其下未到期的授权也不通过。"""
    token, order_id, _ = granted
    access = _stored_session(session, token)
    if session_state == "revoked":
        access.revoked_at = NOW + timedelta(minutes=1)
    else:
        access.expires_at = NOW + timedelta(minutes=10)
    session.flush()

    assert not verify_grant(session, token, order_id, SCOPE_LOOKUP, NOW + timedelta(minutes=15))


def test_grant_does_not_cover_other_orders(session: Session, granted: tuple[str, int, int]) -> None:
    """「仅对该订单……不能替代其他订单的订单号加电话验证」与「凭据不能用于……其他订单」：
    一张订单的授权不能用于另一张订单。
    """
    token, _, other_id = granted

    assert not verify_grant(session, token, other_id, SCOPE_LOOKUP, NOW)
    assert not verify_grant(session, token, other_id, SCOPE_GUEST_CHECKOUT, NOW)


@pytest.mark.parametrize(
    ("issued_scope", "checked_scope"),
    [(SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP), (SCOPE_LOOKUP, SCOPE_GUEST_CHECKOUT)],
)
def test_scopes_do_not_substitute(session: Session, issued_scope: str, checked_scope: str) -> None:
    """「凭据不能用于确认收货、退款」与查单授权「不能支付或取消」：短期凭据不能当查单授权用，
    查单授权也不能当短期凭据用。
    """
    order = _order(session)
    issued = issue_grant(session, None, order.id, issued_scope, NOW)

    assert verify_grant(session, issued.new_token, order.id, issued_scope, NOW)
    assert not verify_grant(session, issued.new_token, order.id, checked_scope, NOW)


@pytest.mark.parametrize(
    "cookie",
    [None, "", "short", "A" * 42 + "!", "A" * 44, "A" * 43 + "\n"],
    ids=["none", "empty", "short", "bad-char", "too-long", "trailing-newline"],
)
def test_malformed_cookie_fails_without_query(
    session: Session, granted: tuple[str, int, int], cookie: str | None
) -> None:
    """「服务端校验」：格式不合法的 cookie 值不查库直接不通过。"""
    _, order_id, _ = granted
    statements = _count_statements(session)

    assert not verify_grant(session, cookie, order_id, SCOPE_LOOKUP, NOW)
    assert statements == []


def test_well_formed_unknown_cookie_fails(session: Session, granted: tuple[str, int, int]) -> None:
    """「以服务端保存的会话实现」：格式合法但查不到会话的 cookie 不通过。"""
    _, order_id, _ = granted

    assert not verify_grant(session, UNKNOWN_TOKEN, order_id, SCOPE_LOOKUP, NOW)


# ---------------------------------------------------------------------------
# cookie
# ---------------------------------------------------------------------------


def test_cookie_name_and_attributes() -> None:
    """「以 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，不交给页面脚本」与「30 分钟
    有效」：只有一个名为 __Host-shop_order_access 的 cookie，带 HttpOnly、Secure、SameSite=Lax、
    Path=/、Max-Age=1800，没有 Domain 与 Expires。
    """
    token = "Ab_-" + "x" * 39
    response = Response()

    set_order_access_cookie(response, token)

    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 1
    name_value, *attributes = [part.strip() for part in headers[0].split(";")]
    assert name_value == f"{COOKIE_NAME}={token}"
    assert COOKIE_NAME == "__Host-shop_order_access"
    parsed = {}
    for attribute in attributes:
        key, _, value = attribute.partition("=")
        parsed[key.lower()] = value.lower()
    assert parsed == {
        "httponly": "",
        "max-age": "1800",
        "path": "/",
        "samesite": "lax",
        "secure": "",
    }


def test_cookie_rejects_malformed_token_without_echoing_it() -> None:
    """「不写入……错误回显」：不合法的令牌不写进 cookie，异常消息里没有它。"""
    secret = "not a token; Domain=evil.example"

    with pytest.raises(ValueError) as caught:
        set_order_access_cookie(Response(), secret)

    assert secret not in str(caught.value)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_csrf_token_is_stable_and_differs_from_stored_hash(session: Session) -> None:
    """「写操作另须 CSRF 令牌」：CSRF 令牌对同一会话稳定，可由 cookie 随时重新算出；
    它与入库的会话摘要、令牌原文都不同，也不入库；不同会话的 CSRF 令牌不同。
    """
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    token = issued.new_token
    assert token is not None
    again = issue_grant(session, token, order.id, SCOPE_GUEST_CHECKOUT, NOW + timedelta(minutes=1))
    other = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert again.csrf_token == issued.csrf_token == csrf_token_for(token)
    assert HEX_64.fullmatch(issued.csrf_token)
    assert issued.csrf_token not in {token, _stored_session(session, token).token_hash}
    assert issued.csrf_token not in _all_stored_text(session)
    assert other.csrf_token != issued.csrf_token


def test_matching_csrf_token_passes(session: Session) -> None:
    """「写操作另须 CSRF 令牌」：请求头里的令牌与由有效 cookie 重新算出的值一致时通过。"""
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert verify_csrf(session, issued.new_token, issued.csrf_token, NOW + timedelta(minutes=1))


@pytest.mark.parametrize(
    "header",
    [None, "", "0" * 64, "wrong", "Ä" * 64],
    ids=["missing", "empty", "other-hex", "short", "non-ascii"],
)
def test_wrong_or_missing_csrf_token_fails(session: Session, header: str | None) -> None:
    """「写操作另须 CSRF 令牌」：请求头的令牌缺失、为空或不一致时不通过（非 ASCII 也不抛错）。"""
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert not verify_csrf(session, issued.new_token, header, NOW)


def test_csrf_token_of_another_session_fails(session: Session) -> None:
    """「写操作另须 CSRF 令牌」：另一个会话的 CSRF 令牌不能用于本会话的 cookie。"""
    order = _order(session)
    mine = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    theirs = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert not verify_csrf(session, mine.new_token, theirs.csrf_token, NOW)


@pytest.mark.parametrize("cookie_state", ["malformed", "unknown", "revoked", "expired"])
def test_csrf_fails_when_cookie_is_invalid(session: Session, cookie_state: str) -> None:
    """「服务端校验……到期时间并可撤销」：cookie 格式不合法、查不到会话、会话已撤销或已到期时，
    即使请求头给出与该 cookie 相符的 CSRF 令牌也不通过。
    """
    order = _order(session)
    issued = issue_grant(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    cookie = issued.new_token
    assert cookie is not None
    now = NOW + timedelta(minutes=1)
    if cookie_state == "malformed":
        cookie = cookie[:-1] + "!"
    elif cookie_state == "unknown":
        cookie = UNKNOWN_TOKEN
    elif cookie_state == "revoked":
        _stored_session(session, cookie).revoked_at = now
        session.flush()
    else:
        now = NOW + LIFETIME

    assert not verify_csrf(session, cookie, csrf_token_for(cookie), now)


def test_csrf_docstring_requires_both_checks_for_writes() -> None:
    """「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」：校验函数的说明写明这些写操作
    须同时通过授权校验与 CSRF 校验。
    """
    doc = inspect.getdoc(verify_csrf) or ""

    for word in ("支付", "取消", "确认收货", "退款申请", "verify_grant"):
        assert word in doc


# ---------------------------------------------------------------------------
# 日志与异常
# ---------------------------------------------------------------------------


def test_module_does_not_log() -> None:
    """第 7 条「应用日志与监控不记录……订单查询参数」与「不写入……日志」：模块不写日志。"""
    source = inspect.getsource(order_access)

    assert "logging" not in source
    assert "print(" not in source


@pytest.mark.parametrize("reuse", [False, True], ids=["new-session", "reused-session"])
def test_tokens_do_not_appear_in_exceptions(
    session: Session, monkeypatch: pytest.MonkeyPatch, reuse: bool
) -> None:
    """「不写入……错误回显」：签发因订单不存在失败时，异常消息里没有令牌、cookie 值或
    CSRF 令牌（库里与 SQL 参数里只有摘要）。
    """
    order = _order(session)
    known = "K" * 43
    cookie: str | None = None
    if reuse:
        issued = issue_grant(session, None, order.id, SCOPE_LOOKUP, NOW)
        cookie = issued.new_token
        assert cookie is not None
        secret = cookie
    else:
        monkeypatch.setattr(order_access.secrets, "token_urlsafe", lambda _n: known)
        secret = known

    with pytest.raises(IntegrityError) as caught:
        issue_grant(session, cookie, 999, SCOPE_LOOKUP, NOW)

    message = str(caught.value)
    assert secret not in message
    assert csrf_token_for(secret) not in message

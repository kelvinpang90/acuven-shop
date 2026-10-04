"""订单访问会话与授权两张表的约束，以及签发、校验、cookie 与 CSRF 令牌。

依据 docs/DESIGN.md 1.10（提交 e3b3505）「权限与资料保护」第 6 条（游客短期凭据与查单授权）
与第 7 条（日志）。每条测试（参数化的测试是每个用例）写明它守住的设计原句；没有直接原句的
写明是 SHOP-TASK-019 验收标准里的实现约定。

用 SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开），自建一张合法的游客订单。
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import Response
from sqlalchemy import Engine, create_engine, delete, event, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Order, OrderAccessGrant, OrderAccessSession
from app.models.order import CLAIM_OPEN, STATUS_AWAITING_PAYMENT
from app.models.order_access import SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP
from app.services.order_access import (
    COOKIE_NAME,
    check_csrf_token,
    check_order_access,
    csrf_token_for_cookie,
    issue_order_access,
    set_order_access_cookie,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"
URL_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

NOW = datetime(2026, 10, 1, 2, 0)
MINUTES_30 = timedelta(minutes=30)
TOKEN_HASH = "ab" * 32
FINGERPRINT = "0f" * 32
# 格式合法（43 个 URL 安全字符）但库里没有对应会话的 cookie 值。
UNKNOWN_TOKEN = "A" * 43


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield db


def _add[T](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


_ORDER_SEQUENCE = itertools.count(1)


def _order(session: Session) -> Order:
    """一张合法的待支付游客订单；每次给不同的订单号与幂等键。"""
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
        "expires_at": NOW + MINUTES_30,
        "revoked_at": None,
    }
    return values | overrides


def _grant_values(session_id: int, order_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "session_id": session_id,
        "order_id": order_id,
        "scope": SCOPE_LOOKUP,
        "created_at": NOW,
        "expires_at": NOW + MINUTES_30,
        "revoked_at": None,
    }
    return values | overrides


def _access_session(session: Session, **overrides: Any) -> OrderAccessSession:
    return _add(session, OrderAccessSession(**_session_values(**overrides)))


def _only_session(session: Session) -> OrderAccessSession:
    return session.scalars(select(OrderAccessSession)).one()


# ---------------------------------------------------------------------------
# 表与约束
# ---------------------------------------------------------------------------


def test_valid_session_and_grants_are_accepted(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权」：一个会话下两张订单、两种范围的授权，
    以及已撤销的授权都写得进去。这是下面各条拒绝测试的对照。
    """
    first, second = _order(session), _order(session)
    access = _access_session(session)
    _add(session, OrderAccessGrant(**_grant_values(access.id, first.id)))
    _add(
        session,
        OrderAccessGrant(**_grant_values(access.id, first.id, scope=SCOPE_GUEST_CHECKOUT)),
    )
    _add(
        session,
        OrderAccessGrant(**_grant_values(access.id, second.id, revoked_at=NOW + MINUTES_30)),
    )

    assert len(session.scalars(select(OrderAccessGrant)).all()) == 3


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
            "「以服务端保存的会话实现」，验收：只存 64 个字符的 SHA-256 摘要：摘要长 62",
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
    with pytest.raises(IntegrityError, match=f"CHECK constraint failed: {constraint}"):
        _access_session(session, **overrides)


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「仅限该单的短期凭据」与「查单授权」只有两种：未知范围",
            "ck_order_access_grants_scope_valid",
            {"scope": "admin"},
        ),
        (
            "「各自独立到期」：授权到期时间等于创建时间",
            "ck_order_access_grants_expires_after_created",
            {"expires_at": NOW},
        ),
        (
            "「各自独立到期」：授权到期时间早于创建时间",
            "ck_order_access_grants_expires_after_created",
            {"expires_at": NOW - timedelta(minutes=1)},
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
    access = _access_session(session)

    with pytest.raises(IntegrityError, match=f"CHECK constraint failed: {constraint}"):
        _add(session, OrderAccessGrant(**_grant_values(access.id, order.id, **overrides)))


def test_duplicate_session_token_hash_is_rejected(session: Session) -> None:
    """「服务端校验所属订单」须由 cookie 唯一定位会话：同一摘要不能对应两个会话。"""
    _access_session(session)
    message = "UNIQUE constraint failed: order_access_sessions.token_hash"

    with pytest.raises(IntegrityError, match=message):
        _access_session(session)


def test_duplicate_grant_for_same_session_order_and_scope_is_rejected(session: Session) -> None:
    """验收「同一会话、订单与范围唯一」：重复签发只能延长原授权，库里不会有第二条。"""
    order = _order(session)
    access = _access_session(session)
    _add(session, OrderAccessGrant(**_grant_values(access.id, order.id)))
    message = "UNIQUE constraint failed: order_access_grants.session_id"

    later = _grant_values(
        access.id, order.id, created_at=NOW + MINUTES_30, expires_at=NOW + 2 * MINUTES_30
    )

    with pytest.raises(IntegrityError, match=message):
        _add(session, OrderAccessGrant(**later))


@pytest.mark.parametrize("missing", ["order", "session"])
def test_grant_must_reference_existing_order_and_session(session: Session, missing: str) -> None:
    """「服务端校验所属订单」：授权必须属于存在的订单与存在的会话。"""
    order = _order(session)
    access = _access_session(session)
    session_id = 999 if missing == "session" else access.id
    order_id = 999 if missing == "order" else order.id

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, OrderAccessGrant(**_grant_values(session_id, order_id)))


@pytest.mark.parametrize("parent", ["order", "session"])
def test_deleting_order_or_session_with_grants_is_rejected(session: Session, parent: str) -> None:
    """「服务端……并可撤销」：撤销只写撤销时间；被授权引用的订单与会话不能物理删除，
    外键 RESTRICT 拒绝，不级联删掉授权。
    """
    order = _order(session)
    access = _access_session(session)
    _add(session, OrderAccessGrant(**_grant_values(access.id, order.id)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        if parent == "order":
            session.execute(delete(Order).where(Order.id == order.id))
        else:
            session.execute(delete(OrderAccessSession).where(OrderAccessSession.id == access.id))


def test_foreign_keys_are_restrict() -> None:
    """验收「外键一律 RESTRICT」：授权表指向会话与订单的外键删除行为都写明为 RESTRICT。"""
    foreign_keys = {
        fk.column.table.name: fk.ondelete for fk in OrderAccessGrant.__table__.foreign_keys
    }

    assert foreign_keys == {"order_access_sessions": "RESTRICT", "orders": "RESTRICT"}


@pytest.mark.parametrize(
    ("model", "column"),
    [
        (OrderAccessSession, "token_hash"),
        (OrderAccessSession, "created_at"),
        (OrderAccessSession, "expires_at"),
        (OrderAccessGrant, "session_id"),
        (OrderAccessGrant, "order_id"),
        (OrderAccessGrant, "scope"),
        (OrderAccessGrant, "created_at"),
        (OrderAccessGrant, "expires_at"),
    ],
)
def test_required_columns_reject_null(session: Session, model: type[Base], column: str) -> None:
    """「服务端校验所属订单、到期时间并可撤销」：除撤销时间外都不能为空；空的到期时间或
    范围会让检查约束放行。
    """
    order = _order(session)
    access = _access_session(session, token_hash="cd" * 32)
    if model is OrderAccessSession:
        values = _session_values()
    else:
        values = _grant_values(access.id, order.id)

    with pytest.raises(IntegrityError, match=f"NOT NULL constraint failed: .*\\.{column}"):
        session.execute(insert(model).values(**(values | {column: None})))


@pytest.mark.parametrize("model", [OrderAccessSession, OrderAccessGrant])
def test_only_revoked_at_is_nullable(model: type[Base]) -> None:
    """验收「除撤销时间外所有列非空」。"""
    nullable = {column.name for column in model.__table__.columns if column.nullable}

    assert nullable == {"revoked_at"}


def test_tables_store_no_token_ip_or_personal_data() -> None:
    """「不写入……日志、分析事件」与验收「不存 IP、浏览器标识或任何个人资料」：
    会话表只有摘要与时间，授权表只有会话、订单、范围与时间。
    """
    session_columns = {column.name for column in OrderAccessSession.__table__.columns}
    grant_columns = {column.name for column in OrderAccessGrant.__table__.columns}

    assert session_columns == {"id", "token_hash", "created_at", "expires_at", "revoked_at"}
    assert grant_columns == {
        "id",
        "session_id",
        "order_id",
        "scope",
        "created_at",
        "expires_at",
        "revoked_at",
    }


def test_grant_order_id_has_index() -> None:
    """「服务端……可撤销」：按订单撤销授权，订单 ID 上有普通索引。"""
    indexes = {index.name: index for index in OrderAccessGrant.__table__.indexes}
    index = indexes["ix_order_access_grants_order_id"]

    assert [column.name for column in index.columns] == ["order_id"]
    assert not index.unique


# ---------------------------------------------------------------------------
# 签发
# ---------------------------------------------------------------------------


def test_first_issue_creates_session_storing_only_the_digest(session: Session) -> None:
    """「不可猜测」的凭据「以服务端保存的会话实现」，令牌原文不入库：首次签发新建会话，
    令牌至少 256 位随机、只含 URL 安全字符；库里只有它的 SHA-256 摘要，任何一列都查不到
    令牌原文或 CSRF 令牌。
    """
    order = _order(session)

    issued = issue_order_access(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert issued.new_token is not None
    token = issued.new_token
    assert len(token) >= 43 and set(token) <= URL_SAFE
    assert issued.expires_at == NOW + MINUTES_30
    stored = _only_session(session)
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert stored.expires_at == NOW + MINUTES_30
    for model in (OrderAccessSession, OrderAccessGrant):
        for row in session.execute(select(model.__table__)).all():
            values = [str(value) for value in row]
            assert not any(token in value or issued.csrf_token in value for value in values)


def test_two_first_issues_get_different_tokens(session: Session) -> None:
    """「不可猜测」：两个没有 cookie 的浏览器得到不同的令牌与不同的会话。"""
    order = _order(session)

    first = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    second = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)

    assert first.new_token != second.new_token
    assert len(session.scalars(select(OrderAccessSession)).all()) == 2


def test_issue_with_valid_cookie_reuses_session(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权」：带有效 cookie 再签发沿用同一会话，
    不返回新令牌，CSRF 令牌不变。
    """
    first_order, second_order = _order(session), _order(session)
    first = issue_order_access(session, None, first_order.id, SCOPE_GUEST_CHECKOUT, NOW)

    second = issue_order_access(
        session, first.new_token, second_order.id, SCOPE_LOOKUP, NOW + timedelta(minutes=5)
    )

    assert second.new_token is None
    assert second.csrf_token == first.csrf_token
    access = _only_session(session)
    grants = session.scalars(select(OrderAccessGrant)).all()
    assert {grant.session_id for grant in grants} == {access.id}
    assert len(grants) == 2


@pytest.mark.parametrize("case", ["expired", "revoked", "malformed", "unknown", "missing"])
def test_issue_creates_new_session_when_cookie_is_not_usable(session: Session, case: str) -> None:
    """「服务端校验……到期时间并可撤销」：cookie 对应的会话已到期、已撤销，或 cookie 值格式
    不合法、库里没有、缺失时，不沿用，新建会话并返回新令牌。
    """
    order = _order(session)
    old = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    later = NOW + timedelta(minutes=10)
    cookie = old.new_token
    if case == "expired":
        later = NOW + MINUTES_30
    elif case == "revoked":
        _only_session(session).revoked_at = NOW + timedelta(minutes=5)
        session.flush()
    elif case == "malformed":
        cookie = f"{old.new_token}."
    elif case == "unknown":
        cookie = UNKNOWN_TOKEN
    else:
        cookie = None

    issued = issue_order_access(session, cookie, order.id, SCOPE_LOOKUP, later)

    assert issued.new_token is not None and issued.new_token != old.new_token
    assert issued.csrf_token != old.csrf_token
    assert len(session.scalars(select(OrderAccessSession)).all()) == 2
    assert check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, later)


def test_grants_of_two_orders_expire_independently(session: Session) -> None:
    """「一个浏览器可同时持有多张订单的授权，各自独立到期」：同一会话两张订单的授权
    各自 30 分钟到期，会话到期时间延到较晚者。
    """
    first_order, second_order = _order(session), _order(session)
    first = issue_order_access(session, None, first_order.id, SCOPE_LOOKUP, NOW)
    token = first.new_token
    second_at = NOW + timedelta(minutes=20)
    second = issue_order_access(session, token, second_order.id, SCOPE_LOOKUP, second_at)

    assert second.expires_at == second_at + MINUTES_30
    assert _only_session(session).expires_at == second_at + MINUTES_30
    at = NOW + timedelta(minutes=31)
    assert not check_order_access(session, token, first_order.id, SCOPE_LOOKUP, at)
    assert check_order_access(session, token, second_order.id, SCOPE_LOOKUP, at)


def test_session_expiry_is_not_shortened_by_a_later_issue(session: Session) -> None:
    """「各自独立到期」：会话到期时间取它与新授权到期时间中较晚者，不会被缩短。"""
    first_order, second_order = _order(session), _order(session)
    first = issue_order_access(session, None, first_order.id, SCOPE_LOOKUP, NOW)
    access = _only_session(session)
    access.expires_at = NOW + timedelta(hours=2)
    session.flush()

    issue_order_access(session, first.new_token, second_order.id, SCOPE_LOOKUP, NOW)

    assert _only_session(session).expires_at == NOW + timedelta(hours=2)


def test_reissue_extends_existing_grant_without_new_row(session: Session) -> None:
    """「30 分钟有效」，验收「重复签发延长原授权而不新增行」：已撤销的同一授权再签发时
    到期时间改为当前时间加 30 分钟、撤销时间清空，创建时间不变，仍只有一行。
    """
    order = _order(session)
    first = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    grant = session.scalars(select(OrderAccessGrant)).one()
    grant.revoked_at = NOW + timedelta(minutes=5)
    session.flush()
    again_at = NOW + timedelta(minutes=10)

    again = issue_order_access(session, first.new_token, order.id, SCOPE_LOOKUP, again_at)

    grant = session.scalars(select(OrderAccessGrant)).one()
    assert again.expires_at == again_at + MINUTES_30
    assert grant.expires_at == again_at + MINUTES_30
    assert grant.revoked_at is None
    assert grant.created_at == NOW
    later = NOW + timedelta(minutes=35)
    assert check_order_access(session, first.new_token, order.id, SCOPE_LOOKUP, later)


def test_issue_does_not_commit(engine: Engine, session: Session) -> None:
    """验收「不提交事务，由调用方在同一事务里提交」：回滚后会话与授权都不留下。"""
    order_id = _order(session).id
    session.commit()

    issue_order_access(session, None, order_id, SCOPE_GUEST_CHECKOUT, NOW)
    session.rollback()

    with Session(engine) as other:
        assert other.scalars(select(OrderAccessSession)).all() == []
        assert other.scalars(select(OrderAccessGrant)).all() == []


def test_issue_for_missing_order_is_rejected(session: Session) -> None:
    """「仅限该单」：不能给不存在的订单签发授权，外键在 flush 时拒绝。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        issue_order_access(session, None, 999, SCOPE_LOOKUP, NOW)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_fresh_grant_passes(session: Session) -> None:
    """「服务端校验所属订单、到期时间」：刚签发的授权在 30 分钟内通过。"""
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    just_before = NOW + MINUTES_30 - timedelta(seconds=1)

    assert check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, NOW)
    assert check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, just_before)


def test_expired_grant_fails(session: Session) -> None:
    """「30 分钟有效」「过期须重新查单」：到期时刻起不通过。"""
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)

    at_expiry = NOW + MINUTES_30
    assert not check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, at_expiry)


def test_expired_session_fails_even_if_grant_is_live(session: Session) -> None:
    """「服务端校验……到期时间」：会话已到期时，即使授权未到期也不通过。"""
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    _only_session(session).expires_at = NOW + timedelta(minutes=5)
    session.flush()

    later = NOW + timedelta(minutes=10)
    assert not check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, later)


@pytest.mark.parametrize("revoked", ["grant", "session"])
def test_revoked_grant_or_session_fails(session: Session, revoked: str) -> None:
    """「服务端……并可撤销」：授权或其会话被撤销后，未到期也不通过。"""
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    row = session.scalars(select(OrderAccessGrant if revoked == "grant" else OrderAccessSession))
    row.one().revoked_at = NOW + timedelta(minutes=1)
    session.flush()

    later = NOW + timedelta(minutes=2)
    assert not check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, later)


def test_grant_does_not_cover_other_orders(session: Session) -> None:
    """「仅限该单」「也不能替代其他订单的订单号加电话验证」：一张订单的授权不能用于其他订单。"""
    order, other = _order(session), _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)

    assert not check_order_access(session, issued.new_token, other.id, SCOPE_LOOKUP, NOW)


@pytest.mark.parametrize(
    ("issued_scope", "used_scope"),
    [(SCOPE_GUEST_CHECKOUT, SCOPE_LOOKUP), (SCOPE_LOOKUP, SCOPE_GUEST_CHECKOUT)],
)
def test_scopes_do_not_substitute(session: Session, issued_scope: str, used_scope: str) -> None:
    """「凭据不能用于确认收货、退款」与查单授权「不能支付或取消」：guest_checkout 不能当 lookup
    用，反之亦然。
    """
    order = _order(session)
    issued = issue_order_access(session, None, order.id, issued_scope, NOW)

    assert check_order_access(session, issued.new_token, order.id, issued_scope, NOW)
    assert not check_order_access(session, issued.new_token, order.id, used_scope, NOW)


def test_other_browser_cookie_fails(session: Session) -> None:
    """「只给当前浏览器发」：另一浏览器的会话对这张订单没有授权。"""
    order, other_order = _order(session), _order(session)
    issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    other = issue_order_access(session, None, other_order.id, SCOPE_LOOKUP, NOW)

    assert not check_order_access(session, other.new_token, order.id, SCOPE_LOOKUP, NOW)
    assert not check_order_access(session, UNKNOWN_TOKEN, order.id, SCOPE_LOOKUP, NOW)


@pytest.mark.parametrize(
    "cookie",
    [None, "", "short", "A" * 42, "A" * 44, "A" * 42 + "=", "A" * 42 + "+", "A" * 42 + "中"],
)
def test_malformed_cookie_fails_without_querying(
    engine: Engine, session: Session, cookie: str | None
) -> None:
    """验收「格式不合法的 cookie 值不查库直接不通过」：缺失、长度不对或含非 URL 安全字符的
    cookie 值不发出任何 SQL。
    """
    order = _order(session)
    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert not check_order_access(session, cookie, order.id, SCOPE_LOOKUP, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert statements == []


def test_unknown_scope_is_a_programming_error(session: Session) -> None:
    """「仅限该单的短期凭据」与「查单授权」之外没有别的范围：未知范围直接报错，不当作不通过。"""
    order = _order(session)

    with pytest.raises(ValueError, match="scope"):
        issue_order_access(session, None, order.id, "admin", NOW)
    with pytest.raises(ValueError, match="scope"):
        check_order_access(session, UNKNOWN_TOKEN, order.id, "admin", NOW)


# ---------------------------------------------------------------------------
# cookie
# ---------------------------------------------------------------------------


def test_cookie_name_and_attributes(session: Session) -> None:
    """「以 HttpOnly、Secure、SameSite=Lax 的 cookie 交给浏览器，不交给页面脚本」，验收
    「__Host-shop_order_access……Path=/，不设 Domain……Max-Age 为 30 分钟」：只设一个 cookie。
    """
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    assert issued.new_token is not None
    response = Response()

    set_order_access_cookie(response, issued.new_token)

    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 1
    pair, *attributes = [part.strip() for part in headers[0].split(";")]
    assert pair == f"{COOKIE_NAME}={issued.new_token}"
    assert COOKIE_NAME == "__Host-shop_order_access"
    lowered = {attribute.lower() for attribute in attributes}
    assert {"httponly", "secure", "samesite=lax", "path=/", "max-age=1800"} <= lowered
    assert not any(attribute.startswith("domain") for attribute in lowered)
    assert not any(attribute.startswith("expires") for attribute in lowered)


def test_cookie_rejects_malformed_token_without_echoing_it() -> None:
    """「不写入……错误回显」：拒绝格式不合法的令牌，异常消息里没有它。"""
    bad = "secret-value-that-must-not-leak"

    with pytest.raises(ValueError) as raised:
        set_order_access_cookie(Response(), bad)

    assert bad not in str(raised.value)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_csrf_token_is_stable_and_differs_from_stored_digest(session: Session) -> None:
    """「写操作另须 CSRF 令牌」，验收「CSRF 令牌不入库……与入库的会话摘要不同」：
    对同一会话可由 cookie 随时重新算出同一值，且不同于摘要与令牌原文。
    """
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    token = issued.new_token
    again = issue_order_access(session, token, order.id, SCOPE_GUEST_CHECKOUT, NOW)

    assert csrf_token_for_cookie(token) == issued.csrf_token == again.csrf_token
    assert issued.csrf_token != _only_session(session).token_hash
    assert issued.csrf_token != token
    assert check_csrf_token(token, issued.csrf_token)


@pytest.mark.parametrize(
    "case",
    [
        "wrong",
        "other_session",
        "missing",
        "empty",
        "cookie_missing",
        "cookie_malformed",
        "non_ascii",
    ],
)
def test_csrf_check_fails(session: Session, case: str) -> None:
    """「支付、取消、确认收货与退款申请等写操作另须 CSRF 令牌」：令牌错误、属于别的会话、
    缺失、为空，或 cookie 缺失、格式不合法时都不通过，非 ASCII 的请求头也不报错。
    """
    order = _order(session)
    issued = issue_order_access(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    other = issue_order_access(session, None, order.id, SCOPE_GUEST_CHECKOUT, NOW)
    cookie: str | None = issued.new_token
    header: str | None = issued.csrf_token
    if case == "wrong":
        header = issued.csrf_token[:-1] + ("0" if issued.csrf_token[-1] != "0" else "1")
    elif case == "other_session":
        header = other.csrf_token
    elif case == "missing":
        header = None
    elif case == "empty":
        header = ""
    elif case == "cookie_missing":
        cookie = None
    elif case == "cookie_malformed":
        cookie = f"{issued.new_token}."
    else:
        header = "中" * 64

    assert csrf_token_for_cookie(None) is None
    assert not check_csrf_token(cookie, header)


# ---------------------------------------------------------------------------
# 日志与回显
# ---------------------------------------------------------------------------


def test_module_writes_no_logs_and_hides_tokens_in_repr(
    session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """「不写入……日志、分析事件或错误回显」（第 6 条）与「应用日志与监控不记录……订单查询参数」
    （第 7 条）：签发、校验与 CSRF 校验不产生任何日志记录；签发结果的 repr 不含令牌或
    CSRF 令牌。
    """
    order = _order(session)
    caplog.set_level(logging.DEBUG)

    issued = issue_order_access(session, None, order.id, SCOPE_LOOKUP, NOW)
    check_order_access(session, issued.new_token, order.id, SCOPE_LOOKUP, NOW)
    check_csrf_token(issued.new_token, issued.csrf_token)

    module_records = [r for r in caplog.records if r.name.startswith("app.services")]
    assert module_records == []
    assert issued.new_token is not None
    assert issued.new_token not in repr(issued)
    assert issued.csrf_token not in repr(issued)

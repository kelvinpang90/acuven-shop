"""会员、会员会话、短信验证记录、短信每日用量四张表与订单会员列的数据库约束。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Member / VerificationAttempt 一行，
以及「失败、并发与重试」「权限与资料保护」「资料保留」。每条测试（参数化的测试是每个用例）
写明它守住的设计原句。

先写入合法的会员、会话、验证记录、每日用量与关联订单作对照，再逐个写反例。检查约束的反例
先在独立的 SQLite 连接上逐条求值该表的全部检查约束（含挂在列上的），断言目标约束确实
不成立，再断言写入被拒、且报出的是不成立的约束之一。非空约束的反例用 Core insert
显式写入 NULL：ORM 会略过值为 None 的列、改用默认值。用 SQLite 内存库按模型建表；
SQLite 默认不检查外键，每个连接都须打开。
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    Table,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Member,
    MemberSession,
    Order,
    SmsDailyUsage,
    VerificationAttempt,
)
from app.models.member import (
    MEMBER_ACTIVE,
    MEMBER_DELETED,
    PURPOSE_CHECKOUT,
    PURPOSE_DELETE_ACCOUNT,
    PURPOSE_LOGIN,
    PURPOSE_REGISTER,
    PURPOSE_RESET_PASSWORD,
    VERIFICATION_APPROVED,
    VERIFICATION_REJECTED,
    VERIFICATION_SENT,
    VERIFICATION_SUSPENDED,
    VERIFICATION_UNDELIVERABLE,
)
from app.models.order import (
    CLAIM_CLAIMED,
    CLAIM_NOT_CLAIMABLE,
    CLAIM_OPEN,
    STATUS_AWAITING_PAYMENT,
)

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 1, 2, 0)
TOKEN_HASH = "ab" * 32
REQUEST_ID = "VE" + "0123456789abcdef" * 2
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


def _member_values(**overrides: Any) -> dict[str, Any]:
    """短信验证自动注册、尚未设密码的有效会员。"""
    values: dict[str, Any] = {
        "phone": "+60123456789",
        "password_hash": None,
        "status": MEMBER_ACTIVE,
        "created_at": CREATED,
        "deleted_at": None,
    }
    return values | overrides


def _deleted_member_values(**overrides: Any) -> dict[str, Any]:
    """已注销的会员：手机号与密码哈希都已删除，有注销时间。"""
    values: dict[str, Any] = {
        "phone": None,
        "password_hash": None,
        "status": MEMBER_DELETED,
        "created_at": CREATED,
        "deleted_at": CREATED + timedelta(days=1),
    }
    return values | overrides


def _session_values(member_id: int, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "member_id": member_id,
        "token_hash": TOKEN_HASH,
        "created_at": CREATED,
        "expires_at": CREATED + timedelta(days=30),
        "revoked_at": None,
    }
    return values | overrides


def _attempt_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "phone": "+60123456789",
        "purpose": PURPOSE_CHECKOUT,
        "status": VERIFICATION_SENT,
        "provider_request_id": REQUEST_ID,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return values | overrides


def _usage_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "usage_date": date(2026, 10, 1),
        "sent_count": 3,
        "reserved_micro_usd": 150_000,
        "settled_micro_usd": 120_000,
    }
    return values | overrides


def _order_values(**overrides: Any) -> dict[str, Any]:
    """待支付的游客订单；会员订单由调用方覆盖会员 ID 与认领状态。"""
    values: dict[str, Any] = {
        "order_number": "0123456789ABCDEF",
        "status": STATUS_AWAITING_PAYMENT,
        "subtotal_sen": 2500,
        "coupon_discount_sen": 0,
        "points_redeemed": 0,
        "shipping_fee_sen": 800,
        "total_sen": 3300,
        "points_earned": 0,
        "shipping_zone_code": "MY-10",
        "shipping_rate_version": 1,
        "idempotency_key": "order-key-1",
        "request_fingerprint": FINGERPRINT,
        "created_at": CREATED,
        "payment_expires_at": CREATED + timedelta(minutes=15),
        "paid_at": None,
        "member_id": None,
        "claim_status": CLAIM_OPEN,
    }
    return values | overrides


def _member(session: Session, **overrides: Any) -> Member:
    return _add(session, Member(**_member_values(**overrides)))


_ORDER_SEQUENCE = itertools.count(1)


def _order(session: Session, **overrides: Any) -> Order:
    """每次给不同的订单号与幂等键，同一测试里可建多张订单。"""
    n = next(_ORDER_SEQUENCE)
    unique = {"order_number": f"{n:016d}", "idempotency_key": f"order-key-{n}"}
    return _add(session, Order(**_order_values(**(unique | overrides))))


def _check_constraints(table: Table) -> list[CheckConstraint]:
    """表上的与挂在列上的检查约束（订单表的认领状态约束挂在列上）。"""
    constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    for column in table.columns:
        constraints += [c for c in column.constraints if isinstance(c, CheckConstraint)]
    return constraints


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints(model.__table__):
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
    table = model.__table__.name
    message = f"NOT NULL constraint failed: {table}.{column}"

    with pytest.raises(IntegrityError, match=message):
        session.execute(insert(model).values(**(values | {column: None})))


# ---------------------------------------------------------------------------
# 对照：合法数据写得进去
# ---------------------------------------------------------------------------


def test_valid_members_sessions_attempts_usage_and_orders_are_accepted(
    session: Session,
) -> None:
    """「Member / VerificationAttempt」「权限与资料保护」：自动注册的无密码会员、设了密码的
    会员、已注销会员、会话、各种状态的验证记录、每日用量，以及游客订单、会员下单的订单、
    已认领订单与注销后会员 ID 已清空的订单都写得进去。这是下面各条拒绝测试的对照：同样的
    建表与外键检查下，合法数据写得进去，且求值辅助函数对它们不报任何约束不成立。
    """
    passwordless = _member(session)
    with_password = _member(session, phone="+6581234567", password_hash="$argon2id$demo")
    deleted = _add(session, Member(**_deleted_member_values()))

    _add(session, MemberSession(**_session_values(passwordless.id)))
    _add(
        session,
        MemberSession(
            **_session_values(
                with_password.id,
                token_hash="cd" * 32,
                revoked_at=CREATED + timedelta(hours=1),
            )
        ),
    )

    _add(session, VerificationAttempt(**_attempt_values()))
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(
                purpose=PURPOSE_REGISTER,
                status=VERIFICATION_APPROVED,
                provider_request_id="VE-approved",
            )
        ),
    )
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(
                purpose=PURPOSE_LOGIN,
                status=VERIFICATION_REJECTED,
                provider_request_id="VE-rejected",
            )
        ),
    )
    # 无法送达：提供方可能已给请求 ID，也可能没有。
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(
                purpose=PURPOSE_RESET_PASSWORD,
                status=VERIFICATION_UNDELIVERABLE,
                provider_request_id="VE-undeliverable",
            )
        ),
    )
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(
                purpose=PURPOSE_DELETE_ACCOUNT,
                status=VERIFICATION_UNDELIVERABLE,
                provider_request_id=None,
            )
        ),
    )
    # 停发的记录请求 ID 都为空，可以有多条。
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(status=VERIFICATION_SUSPENDED, provider_request_id=None)
        ),
    )
    _add(
        session,
        VerificationAttempt(
            **_attempt_values(status=VERIFICATION_SUSPENDED, provider_request_id=None)
        ),
    )

    _add(session, SmsDailyUsage(**_usage_values()))
    _add(
        session,
        SmsDailyUsage(
            **_usage_values(
                usage_date=date(2026, 10, 2),
                sent_count=0,
                reserved_micro_usd=0,
                settled_micro_usd=0,
            )
        ),
    )

    guest = _order(session)
    member_order = _order(session, member_id=passwordless.id, claim_status=CLAIM_NOT_CLAIMABLE)
    claimed = _order(session, member_id=with_password.id, claim_status=CLAIM_CLAIMED)
    after_deletion = _order(session, member_id=None, claim_status=CLAIM_CLAIMED)

    assert deleted.phone is None
    assert len(session.scalars(select(Member)).all()) == 3
    assert len(session.scalars(select(MemberSession)).all()) == 2
    assert len(session.scalars(select(VerificationAttempt)).all()) == 7
    assert len(session.scalars(select(SmsDailyUsage)).all()) == 2
    assert guest.claim_status == CLAIM_OPEN
    assert member_order.claim_status == CLAIM_NOT_CLAIMABLE
    assert claimed.claim_status == CLAIM_CLAIMED
    assert after_deletion.member_id is None

    assert _failing_checks(Member, _member_values()) == []
    assert _failing_checks(Member, _member_values(password_hash="$argon2id$demo")) == []
    assert _failing_checks(Member, _deleted_member_values()) == []
    assert _failing_checks(MemberSession, _session_values(1)) == []
    assert _failing_checks(VerificationAttempt, _attempt_values()) == []
    suspended = _attempt_values(status=VERIFICATION_SUSPENDED, provider_request_id=None)
    assert _failing_checks(VerificationAttempt, suspended) == []
    assert _failing_checks(SmsDailyUsage, _usage_values()) == []
    assert _failing_checks(Order, _order_values()) == []
    member_values = _order_values(member_id=1, claim_status=CLAIM_NOT_CLAIMABLE)
    assert _failing_checks(Order, member_values) == []


def test_existing_order_without_claim_status_defaults_to_open(session: Session) -> None:
    """「认领只查近 30 天且未认领的游客订单」：不写认领状态的订单（如已有的游客下单代码
    与迁移前的订单）是可认领的 open。
    """
    values = _order_values()
    del values["claim_status"]
    order = _add(session, Order(**values))

    assert order.claim_status == CLAIM_OPEN


# ---------------------------------------------------------------------------
# 会员
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "values"),
    [
        (
            "「账号状态」只有有效与已注销：未知状态",
            "ck_members_status_valid",
            _member_values(status="suspended"),
        ),
        (
            "「E.164 格式手机号」：有效会员必须有手机号",
            "ck_members_active_has_phone",
            _member_values(phone=None),
        ),
        (
            "「会员手机号保留至主动注销」：有效会员不能有注销时间",
            "ck_members_active_has_phone",
            _member_values(deleted_at=CREATED + timedelta(days=1)),
        ),
        (
            "「账号注销……删除手机号与密码」：已注销会员仍有手机号",
            "ck_members_deleted_cleared",
            _deleted_member_values(phone="+60123456789"),
        ),
        (
            "「账号注销……删除手机号与密码」：已注销会员仍有密码哈希",
            "ck_members_deleted_cleared",
            _deleted_member_values(password_hash="$argon2id$demo"),
        ),
        (
            "「账号注销」须记录注销时间：已注销会员无注销时间",
            "ck_members_deleted_cleared",
            _deleted_member_values(deleted_at=None),
        ),
        (
            "「E.164 格式手机号」：手机号不以加号开头",
            "ck_members_phone_format",
            _member_values(phone="60123456789"),
        ),
        (
            "「E.164 格式手机号」：手机号超过 16 个字符",
            "ck_members_phone_format",
            _member_values(phone="+6012345678901234"),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_member_check_constraints_reject(
    session: Session, design: str, constraint: str, values: dict[str, Any]
) -> None:
    """会员表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, Member, constraint, values)


@pytest.mark.parametrize("column", ["status", "created_at"])
def test_member_required_columns_reject_null(session: Session, column: str) -> None:
    """「账号状态」：除手机号、密码哈希与注销时间外，会员的列都不能为空；
    状态为空时状态检查约束会放行，只能靠非空约束拒绝。
    """
    _assert_null_rejected(session, Member, column, _member_values())


def test_duplicate_member_phone_is_rejected(session: Session) -> None:
    """「号码已注册且账号有效则登录该账号」：一个手机号只能对应一个有效会员。"""
    _member(session)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed: members.phone"):
        _member(session, password_hash="$argon2id$demo")


def test_several_deleted_members_without_phone_coexist(session: Session) -> None:
    """「注销后同号重注册」：注销删除手机号后，多个已注销会员的手机号都为空可以共存，
    同一号码也能再注册为新的有效会员。
    """
    _add(session, Member(**_deleted_member_values()))
    _add(session, Member(**_deleted_member_values()))
    _member(session)

    statuses = session.scalars(select(Member.status).order_by(Member.id)).all()
    assert statuses == [MEMBER_DELETED, MEMBER_DELETED, MEMBER_ACTIVE]


def test_member_stores_no_other_personal_data() -> None:
    """「Member……E.164 格式手机号、可为空的密码哈希、账号状态」：会员表不存其他个人资料。"""
    columns = {column.name for column in Member.__table__.columns}

    assert columns == {"id", "phone", "password_hash", "status", "created_at", "deleted_at"}


# ---------------------------------------------------------------------------
# 会员会话
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「会话到期」：到期时间等于创建时间",
            "ck_member_sessions_expires_after_created",
            {"expires_at": CREATED},
        ),
        (
            "「会话到期」：到期时间早于创建时间",
            "ck_member_sessions_expires_after_created",
            {"expires_at": CREATED - timedelta(seconds=1)},
        ),
        (
            "「安全哈希存储」只存 SHA-256 十六进制摘要：摘要不是 64 个字符",
            "ck_member_sessions_token_hash_length",
            {"token_hash": "ab" * 31},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_member_session_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """会员会话表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    member = _member(session)
    _assert_check_rejects(
        session, MemberSession, constraint, _session_values(member.id, **overrides)
    )


@pytest.mark.parametrize("column", ["member_id", "token_hash", "created_at", "expires_at"])
def test_member_session_required_columns_reject_null(session: Session, column: str) -> None:
    """「会话到期、退出与服务端授权检查」：会话必须属于会员、有摘要与到期时间；
    只有撤销时间可空。
    """
    member = _member(session)
    _assert_null_rejected(session, MemberSession, column, _session_values(member.id))


def test_duplicate_session_token_hash_is_rejected(session: Session) -> None:
    """「服务端授权检查」按会话摘要定位会话：同一摘要不能对应两个会话。"""
    first = _member(session)
    second = _member(session, phone="+6581234567")
    _add(session, MemberSession(**_session_values(first.id)))
    message = "UNIQUE constraint failed: member_sessions.token_hash"

    with pytest.raises(IntegrityError, match=message):
        _add(session, MemberSession(**_session_values(second.id)))


def test_session_must_reference_an_existing_member(session: Session) -> None:
    """「会话到期、退出与服务端授权检查」：会话必须属于一个存在的会员。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, MemberSession(**_session_values(999)))


def test_member_session_stores_no_token() -> None:
    """「安全哈希存储」：会话表只有令牌摘要，没有令牌原文或其他资料的列。"""
    columns = {column.name for column in MemberSession.__table__.columns}

    assert columns == {"id", "member_id", "token_hash", "created_at", "expires_at", "revoked_at"}


# ---------------------------------------------------------------------------
# 短信验证记录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「结账验证、短信登录、注册与密码重设共用」与注销确认：未知用途",
            "ck_verification_attempts_purpose_valid",
            {"purpose": "marketing"},
        ),
        (
            "「只存提供方请求 ID、结果」：未知状态",
            "ck_verification_attempts_status_valid",
            {"status": "pending"},
        ),
        (
            "「只存提供方请求 ID」：已发出的验证没有请求 ID",
            "ck_verification_attempts_accepted_has_request_id",
            {"status": VERIFICATION_SENT, "provider_request_id": None},
        ),
        (
            "「只存提供方请求 ID、结果」：已通过的验证没有请求 ID",
            "ck_verification_attempts_accepted_has_request_id",
            {"status": VERIFICATION_APPROVED, "provider_request_id": None},
        ),
        (
            "「只存提供方请求 ID、结果」：被拒的验证没有请求 ID",
            "ck_verification_attempts_accepted_has_request_id",
            {"status": VERIFICATION_REJECTED, "provider_request_id": None},
        ),
        (
            "「超过每日总量或费用阈值停发」：停发的短信未发出，却有请求 ID",
            "ck_verification_attempts_suspended_no_request_id",
            {"status": VERIFICATION_SUSPENDED},
        ),
        (
            "「按规范化手机号……限流」：手机号不以加号开头",
            "ck_verification_attempts_phone_format",
            {"phone": "60123456789"},
        ),
        (
            "「按规范化手机号……限流」：手机号超过 16 个字符",
            "ck_verification_attempts_phone_format",
            {"phone": "+6012345678901234"},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_verification_attempt_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """短信验证记录表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, VerificationAttempt, constraint, _attempt_values(**overrides))


@pytest.mark.parametrize("column", ["phone", "purpose", "status", "created_at", "updated_at"])
def test_verification_attempt_required_columns_reject_null(session: Session, column: str) -> None:
    """「按规范化手机号……限流」与「该号码刚遇到短信无法送达或停发」：判定须有号码、用途、
    状态与时间；只有提供方请求 ID 可空。
    """
    _assert_null_rejected(session, VerificationAttempt, column, _attempt_values())


def test_duplicate_provider_request_id_is_rejected(session: Session) -> None:
    """「只存提供方请求 ID」：一个提供方请求只对应一条验证记录，回调与核验不会落到两条上。"""
    _add(session, VerificationAttempt(**_attempt_values()))
    message = "UNIQUE constraint failed: verification_attempts.provider_request_id"

    with pytest.raises(IntegrityError, match=message):
        _add(session, VerificationAttempt(**_attempt_values(status=VERIFICATION_APPROVED)))


def test_verification_attempt_has_phone_and_time_index() -> None:
    """「除非服务端记录显示该号码刚遇到短信无法送达或停发」：按手机号与创建时间建索引。"""
    indexes = {index.name: index for index in VerificationAttempt.__table__.indexes}
    index = indexes["ix_verification_attempts_phone_created_at"]

    assert [column.name for column in index.columns] == ["phone", "created_at"]
    assert not index.unique


def test_verification_attempt_stores_no_code_or_ip() -> None:
    """「不存验证码」，且不存 IP 或其他资料：验证记录只有号码、用途、状态、请求 ID 与时间。"""
    columns = {column.name for column in VerificationAttempt.__table__.columns}

    assert columns == {
        "id",
        "phone",
        "purpose",
        "status",
        "provider_request_id",
        "created_at",
        "updated_at",
    }


# ---------------------------------------------------------------------------
# 短信每日用量
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「按日原子计数」：已发送条数为负",
            "ck_sms_daily_usage_sent_count_non_negative",
            {"sent_count": -1},
        ),
        (
            "「发送前……预占保守的单次最高费用」：预占费用为负",
            "ck_sms_daily_usage_reserved_micro_usd_non_negative",
            {"reserved_micro_usd": -1},
        ),
        (
            "「结算后更新记录」：已结算费用为负",
            "ck_sms_daily_usage_settled_micro_usd_non_negative",
            {"settled_micro_usd": -1},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_sms_daily_usage_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """短信每日用量表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, SmsDailyUsage, constraint, _usage_values(**overrides))


@pytest.mark.parametrize(
    "column", ["usage_date", "sent_count", "reserved_micro_usd", "settled_micro_usd"]
)
def test_sms_daily_usage_required_columns_reject_null(session: Session, column: str) -> None:
    """「防 Redis 重置后超过日上限」：日期、条数与费用都不能为空，空值会让不小于零的
    检查约束放行、让预算判定失效。
    """
    _assert_null_rejected(session, SmsDailyUsage, column, _usage_values())


def test_duplicate_usage_date_is_rejected(session: Session) -> None:
    """「按日原子计数和预算记录」：每天只有一行，计数不会分散到两行而绕过日上限。"""
    _add(session, SmsDailyUsage(**_usage_values()))
    message = "UNIQUE constraint failed: sms_daily_usage.usage_date"

    with pytest.raises(IntegrityError, match=message):
        _add(session, SmsDailyUsage(**_usage_values(sent_count=0)))


def test_sms_usage_fees_are_integers(session: Session) -> None:
    """「每日总量或费用阈值」：费用存整数微美元，读回仍是 int，不经浮点。"""
    usage = _add(session, SmsDailyUsage(**_usage_values(reserved_micro_usd=5_000_000_000)))
    session.expire_all()

    reloaded = session.get(SmsDailyUsage, usage.id)
    assert reloaded is not None
    assert reloaded.reserved_micro_usd == 5_000_000_000
    assert type(reloaded.reserved_micro_usd) is int


# ---------------------------------------------------------------------------
# 订单的会员 ID 与认领状态
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「会员下单的订单创建时即标记为不可认领」：会员订单的认领状态为 open",
            "ck_orders_member_claim_not_open",
            {"member_id": 1, "claim_status": CLAIM_OPEN},
        ),
        (
            "「已认领」与「不可认领」标记：未知认领状态",
            "ck_orders_claim_status_valid",
            {"claim_status": "pending"},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_order_claim_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """订单认领状态的每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    member = _member(session)
    assert member.id == 1
    _assert_check_rejects(session, Order, constraint, _order_values(**overrides))


def test_order_claim_status_rejects_null(session: Session) -> None:
    """「已认领」「不可认领」标记：认领状态不能为空，空值会让两条认领检查约束都放行。"""
    _assert_null_rejected(session, Order, "claim_status", _order_values())


def test_order_must_reference_an_existing_member(session: Session) -> None:
    """「可选会员 ID」：非空的会员 ID 必须指向存在的会员。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _order(session, member_id=999, claim_status=CLAIM_NOT_CLAIMABLE)


def test_clearing_member_id_keeps_claim_marker(session: Session) -> None:
    """「把其订单上的会员 ID 清空以解除关联……注销清空会员 ID 后这些标记保留」：
    清空会员 ID 后，已认领与不可认领的标记照常保留，订单不会回到可认领。
    """
    member = _member(session)
    claimed = _order(session, member_id=member.id, claim_status=CLAIM_CLAIMED)
    placed = _order(session, member_id=member.id, claim_status=CLAIM_NOT_CLAIMABLE)

    session.execute(update(Order).where(Order.member_id == member.id).values(member_id=None))
    session.expire_all()

    assert session.get(Order, claimed.id).claim_status == CLAIM_CLAIMED
    assert session.get(Order, placed.id).claim_status == CLAIM_NOT_CLAIMABLE
    assert session.scalars(select(Order.member_id)).all() == [None, None]


def test_order_member_id_has_lookup_index() -> None:
    """「会员仅能访问自己认领或下单的订单」：「我的订单」按会员 ID 查询，
    会员 ID 上有普通索引。
    """
    indexes = {index.name: index for index in Order.__table__.indexes}

    assert [column.name for column in indexes["ix_orders_member_id"].columns] == ["member_id"]
    assert not indexes["ix_orders_member_id"].unique


# ---------------------------------------------------------------------------
# 外键与可空列
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("child", ["order", "session"])
def test_deleting_member_referenced_by_order_or_session_is_rejected(
    session: Session, child: str
) -> None:
    """「账号注销……撤销会话、删除手机号与密码，并把其订单上的会员 ID 清空」：注销不删会员行，
    被订单或会话引用的会员不能物理删除，外键 RESTRICT 拒绝，不级联删掉订单或会话。
    """
    member = _member(session)
    if child == "order":
        _order(session, member_id=member.id, claim_status=CLAIM_NOT_CLAIMABLE)
    else:
        _add(session, MemberSession(**_session_values(member.id)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(Member).where(Member.id == member.id))


def test_foreign_keys_to_members_are_restrict() -> None:
    """「会员行不物理删除」：指向会员的外键删除行为都写明为 RESTRICT。"""
    foreign_keys = [
        foreign_key
        for table in (MemberSession.__table__, Order.__table__)
        for foreign_key in table.foreign_keys
        if foreign_key.column.table.name == "members"
    ]

    assert [fk.parent.table.name for fk in foreign_keys] == ["member_sessions", "orders"]
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys)


@pytest.mark.parametrize(
    ("model", "nullable"),
    [
        (Member, {"phone", "password_hash", "deleted_at"}),
        (MemberSession, {"revoked_at"}),
        (VerificationAttempt, {"provider_request_id"}),
        (SmsDailyUsage, set()),
    ],
    ids=["members", "member_sessions", "verification_attempts", "sms_daily_usage"],
)
def test_only_designated_columns_are_nullable(model: type[Base], nullable: set[str]) -> None:
    """「Member / VerificationAttempt」：只有设计指定可空的列（会员手机号、密码哈希、
    注销时间，会话撤销时间，验证记录的提供方请求 ID）可为空，其余一律非空。
    """
    actual = {column.name for column in model.__table__.columns if column.nullable}

    assert actual == nullable


def test_order_member_columns_nullability() -> None:
    """「可选会员 ID」：订单会员 ID 可空，认领状态非空。"""
    columns = Order.__table__.columns

    assert columns["member_id"].nullable
    assert not columns["claim_status"].nullable

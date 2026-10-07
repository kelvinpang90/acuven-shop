"""管理员账号、后台会话与审计记录三张表的数据库约束。

依据 docs/DESIGN.md 1.11（提交 2d13250）「数据模型」的 AdminAccount / AuditEvent 一行
（「单一管理员、密码哈希、会话及后台操作记录」）与 SiteSetting 一行（「后台修改写入
AuditEvent（操作者、时间、新旧值）」）、「权限与资料保护」第 4–6 条，以及
docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员登录决定。
每条测试（参数化的测试是每个用例）写明它守住的设计原句。

先写入合法的账号、会话与审计记录作对照，再逐个写反例。检查约束的反例先在独立的 SQLite 连接上
逐条求值该表的全部检查约束，断言目标约束确实不成立，再断言写入被拒、且报出的是不成立的约束
之一。非空约束的反例用 Core insert 显式写入 NULL：ORM 会略过值为 None 的列、改用默认值。
用 SQLite 内存库按模型建表；SQLite 默认不检查外键，每个连接都须打开。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    CheckConstraint,
    create_engine,
    delete,
    event,
    insert,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AdminAccount, AdminSession, AuditEvent

FOREIGN_KEY_FAILED = "FOREIGN KEY constraint failed"

CREATED = datetime(2026, 10, 4, 2, 0)
TOKEN_HASH = "ab" * 32
PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$demo$demo"

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


def _account_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "username": "shop_admin",
        "password_hash": PASSWORD_HASH,
        "singleton_slot": 1,
        "created_at": CREATED,
        "password_updated_at": CREATED,
    }
    return values | overrides


def _session_values(account_id: int, **overrides: Any) -> dict[str, Any]:
    """登录起 30 天到期、未撤销的后台会话。"""
    values: dict[str, Any] = {
        "admin_account_id": account_id,
        "token_hash": TOKEN_HASH,
        "created_at": CREATED,
        "expires_at": CREATED + timedelta(days=30),
        "revoked_at": None,
    }
    return values | overrides


def _event_values(account_id: int, **overrides: Any) -> dict[str, Any]:
    """没有对象与新旧值的审计记录（如登录成功）。"""
    values: dict[str, Any] = {
        "occurred_at": CREATED,
        "admin_account_id": account_id,
        "action": "login_succeeded",
        "target_type": None,
        "target_id": None,
        "old_value": None,
        "new_value": None,
    }
    return values | overrides


def _account(session: Session, **overrides: Any) -> AdminAccount:
    return _add(session, AdminAccount(**_account_values(**overrides)))


def _check_constraints(model: type[Base]) -> list[CheckConstraint]:
    table = model.__table__
    constraints = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    for column in table.columns:
        constraints += [c for c in column.constraints if isinstance(c, CheckConstraint)]
    return constraints


def _sql_param(value: Any) -> Any:
    # 与 SQLAlchemy 在 SQLite 上存的格式同样按字符串比较。
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _failing_checks(model: type[Base], values: dict[str, Any]) -> list[str]:
    """在独立连接上对这一行求值 model 的全部检查约束，返回不成立的约束名。

    与数据库相同，表达式结果为 NULL 时算成立。
    """
    params = {key: _sql_param(value) for key, value in values.items()}
    columns = ", ".join(f":{key} AS {key}" for key in params)
    failing: list[str] = []
    with _EVAL_ENGINE.connect() as connection:
        for constraint in _check_constraints(model):
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


def test_valid_account_sessions_and_audit_events_are_accepted(session: Session) -> None:
    """「AdminAccount / AuditEvent：单一管理员、密码哈希、会话及后台操作记录」：一个账号、
    一个有效会话与一个已撤销的会话、无对象的登录审计与带对象和新旧值的设置修改审计都写得进去。
    这是下面各条拒绝测试的对照：同样的建表与外键检查下，合法数据写得进去，且求值辅助函数
    对它们不报任何约束不成立。
    """
    account = _account(session)

    _add(session, AdminSession(**_session_values(account.id)))
    _add(
        session,
        AdminSession(
            **_session_values(
                account.id,
                token_hash="cd" * 32,
                revoked_at=CREATED + timedelta(hours=1),
            )
        ),
    )

    _add(session, AuditEvent(**_event_values(account.id)))
    setting_change = _event_values(
        account.id,
        occurred_at=CREATED + timedelta(minutes=5),
        action="site_setting_updated",
        target_type="site_setting",
        target_id=1,
        old_value="false",
        new_value="true",
    )
    _add(session, AuditEvent(**setting_change))
    # 只有新值或只有旧值也可以（如新建或删除某对象）。
    _add(session, AuditEvent(**_event_values(account.id, new_value="店铺名")))

    assert len(session.scalars(select(AdminAccount)).all()) == 1
    assert len(session.scalars(select(AdminSession)).all()) == 2
    assert len(session.scalars(select(AuditEvent)).all()) == 3

    assert _failing_checks(AdminAccount, _account_values()) == []
    assert _failing_checks(AdminAccount, _account_values(username="abc")) == []
    assert _failing_checks(AdminAccount, _account_values(username="a" * 32)) == []
    assert _failing_checks(AdminSession, _session_values(1)) == []
    assert _failing_checks(AuditEvent, _event_values(1)) == []
    assert _failing_checks(AuditEvent, setting_change) == []


def test_singleton_slot_defaults_to_one(session: Session) -> None:
    """「库里至多一个管理员账号，由账号表的单例槽唯一约束保证」：不写单例槽的账号取 1，
    建账号的写入方不写也照样受唯一约束保护。
    """
    values = _account_values()
    del values["singleton_slot"]
    account = _add(session, AdminAccount(**values))

    assert account.singleton_slot == 1


# ---------------------------------------------------------------------------
# 管理员账号
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「单一管理员」：用户名少于 3 个字符",
            "ck_admin_accounts_username_length",
            {"username": "ab"},
        ),
        (
            "「单一管理员」：用户名为空串",
            "ck_admin_accounts_username_length",
            {"username": ""},
        ),
        (
            "Kelvin 2026-10-07（HANDOFF 0.37）「列加长到 254 个字符」：用户名超过 254 个字符",
            "ck_admin_accounts_username_length",
            {"username": "a" * 255},
        ),
        (
            "「库里至多一个管理员账号，由账号表的单例槽唯一约束保证」：单例槽为 2",
            "ck_admin_accounts_singleton_slot_one",
            {"singleton_slot": 2},
        ),
        (
            "「库里至多一个管理员账号，由账号表的单例槽唯一约束保证」：单例槽为 0",
            "ck_admin_accounts_singleton_slot_one",
            {"singleton_slot": 0},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_admin_account_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """管理员账号表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    _assert_check_rejects(session, AdminAccount, constraint, _account_values(**overrides))


@pytest.mark.parametrize(
    "column",
    ["username", "password_hash", "singleton_slot", "created_at", "password_updated_at"],
)
def test_admin_account_required_columns_reject_null(session: Session, column: str) -> None:
    """「单一管理员、密码哈希」：账号的列都不能为空；单例槽为空时 singleton_slot = 1 的检查
    约束会放行、唯一约束也允许多个空值，只能靠非空约束拒绝。
    """
    _assert_null_rejected(session, AdminAccount, column, _account_values())


def test_second_admin_account_is_rejected_by_singleton_slot(session: Session) -> None:
    """「库里至多一个管理员账号，由账号表的单例槽唯一约束保证」：用户名不同的第二个账号
    （两个建账号命令并发时后插入的一个）撞上单例槽唯一约束被拒。
    """
    _account(session)
    message = "UNIQUE constraint failed: admin_accounts.singleton_slot"

    with pytest.raises(IntegrityError, match=message):
        _account(session, username="other_admin")


def test_duplicate_username_is_rejected(session: Session) -> None:
    """「管理员登录按来源与账号组合限流」按用户名定位账号：用户名全表唯一。

    同名的第二个账号本来就会撞上单例槽；为了单独验证用户名的唯一约束，只在这个连接上
    关掉检查约束，让第二行以单例槽 2 写入，此时只剩用户名的唯一约束能拒绝它。
    """
    _account(session)
    session.execute(text("PRAGMA ignore_check_constraints = ON"))
    message = "UNIQUE constraint failed: admin_accounts.username"

    with pytest.raises(IntegrityError, match=message):
        _account(session, singleton_slot=2)


def test_admin_account_stores_no_personal_data() -> None:
    """「单一管理员、密码哈希」：账号表不存姓名、电话、邮箱或其他个人资料。"""
    columns = {column.name for column in AdminAccount.__table__.columns}

    assert columns == {
        "id",
        "username",
        "password_hash",
        "singleton_slot",
        "created_at",
        "password_updated_at",
    }


# ---------------------------------------------------------------------------
# 后台会话
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「单一管理员也须服务端授权与会话到期」：到期时间等于创建时间",
            "ck_admin_sessions_expires_after_created",
            {"expires_at": CREATED},
        ),
        (
            "「单一管理员也须服务端授权与会话到期」：到期时间早于创建时间",
            "ck_admin_sessions_expires_after_created",
            {"expires_at": CREATED - timedelta(seconds=1)},
        ),
        (
            "「会话」只存令牌的 SHA-256 十六进制摘要：摘要不是 64 个字符",
            "ck_admin_sessions_token_hash_length",
            {"token_hash": "ab" * 31},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_admin_session_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """后台会话表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    account = _account(session)
    _assert_check_rejects(
        session, AdminSession, constraint, _session_values(account.id, **overrides)
    )


@pytest.mark.parametrize("column", ["admin_account_id", "token_hash", "created_at", "expires_at"])
def test_admin_session_required_columns_reject_null(session: Session, column: str) -> None:
    """「单一管理员也须服务端授权与会话到期」：会话必须属于管理员、有摘要与到期时间；
    只有撤销时间可空。
    """
    account = _account(session)
    _assert_null_rejected(session, AdminSession, column, _session_values(account.id))


def test_duplicate_admin_session_token_hash_is_rejected(session: Session) -> None:
    """「服务端授权」按会话摘要定位会话：同一摘要不能对应两个会话。"""
    account = _account(session)
    _add(session, AdminSession(**_session_values(account.id)))
    message = "UNIQUE constraint failed: admin_sessions.token_hash"

    with pytest.raises(IntegrityError, match=message):
        _add(session, AdminSession(**_session_values(account.id)))


def test_admin_session_must_reference_an_existing_account(session: Session) -> None:
    """「单一管理员也须服务端授权」：会话必须属于一个存在的管理员账号。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, AdminSession(**_session_values(999)))


def test_admin_session_stores_no_token() -> None:
    """「会话」：会话表只有令牌摘要，没有令牌原文、来源地址或其他资料的列。"""
    columns = {column.name for column in AdminSession.__table__.columns}

    assert columns == {
        "id",
        "admin_account_id",
        "token_hash",
        "created_at",
        "expires_at",
        "revoked_at",
    }


# ---------------------------------------------------------------------------
# 审计记录
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("design", "constraint", "overrides"),
    [
        (
            "「后台操作记录」须有操作名：操作名为空串",
            "ck_audit_events_action_length",
            {"action": ""},
        ),
        (
            "「后台操作记录」：操作名超过 40 个字符",
            "ck_audit_events_action_length",
            {"action": "a" * 41},
        ),
        (
            "「后台操作记录」：对象类别为空串",
            "ck_audit_events_target_type_length",
            {"target_type": "", "target_id": 1},
        ),
        (
            "「后台操作记录」：对象类别超过 20 个字符",
            "ck_audit_events_target_type_length",
            {"target_type": "a" * 21, "target_id": 1},
        ),
        (
            "「后台操作记录」的对象：有对象类别而无对象 ID",
            "ck_audit_events_target_both_or_neither",
            {"target_type": "site_setting", "target_id": None},
        ),
        (
            "「后台操作记录」的对象：有对象 ID 而无对象类别",
            "ck_audit_events_target_both_or_neither",
            {"target_type": None, "target_id": 1},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("ck_") else None,
)
def test_audit_event_check_constraints_reject(
    session: Session, design: str, constraint: str, overrides: dict[str, Any]
) -> None:
    """审计记录表每个检查约束至少一个反例；守住的设计原句见每个用例的 design。"""
    assert design
    account = _account(session)
    _assert_check_rejects(session, AuditEvent, constraint, _event_values(account.id, **overrides))


@pytest.mark.parametrize("column", ["occurred_at", "admin_account_id", "action"])
def test_audit_event_required_columns_reject_null(session: Session, column: str) -> None:
    """SiteSetting 一行「写入 AuditEvent（操作者、时间、新旧值）」与「每条审计记录都属于某个
    账号」：时间、操作者与操作名都不能为空；对象与新旧值可空。
    """
    account = _account(session)
    _assert_null_rejected(session, AuditEvent, column, _event_values(account.id))


def test_audit_event_must_reference_an_existing_account(session: Session) -> None:
    """「每条审计记录都属于某个账号」：操作者必须是存在的管理员账号。"""
    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        _add(session, AuditEvent(**_event_values(999)))


def test_audit_event_action_is_not_an_enum(session: Session) -> None:
    """「后台操作记录」：操作名取值由写入方常量决定，之后的业务接口任务增加的操作名
    不必改约束即可写入。
    """
    account = _account(session)
    row = _add(session, AuditEvent(**_event_values(account.id, action="order_viewed")))

    assert row.action == "order_viewed"


def test_audit_event_has_occurred_at_index() -> None:
    """「后台操作记录」按时间查看：按发生时间建普通索引。"""
    indexes = {index.name: index for index in AuditEvent.__table__.indexes}
    index = indexes["ix_audit_events_occurred_at"]

    assert [column.name for column in index.columns] == ["occurred_at"]
    assert not index.unique


def test_audit_event_stores_no_source_or_submitted_username() -> None:
    """「审计记录不存来源地址与提交的用户名」：审计表只有时间、操作者、操作名、对象与新旧值。"""
    columns = {column.name for column in AuditEvent.__table__.columns}

    assert columns == {
        "id",
        "occurred_at",
        "admin_account_id",
        "action",
        "target_type",
        "target_id",
        "old_value",
        "new_value",
    }


# ---------------------------------------------------------------------------
# 外键与可空列
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("child", ["session", "audit_event"])
def test_deleting_account_referenced_by_session_or_audit_event_is_rejected(
    session: Session, child: str
) -> None:
    """「后台操作记录」与「会话」须保留归属：被会话或审计记录引用的账号不能物理删除，
    外键 RESTRICT 拒绝，不级联删掉会话或审计记录。
    """
    account = _account(session)
    if child == "session":
        _add(session, AdminSession(**_session_values(account.id)))
    else:
        _add(session, AuditEvent(**_event_values(account.id)))

    with pytest.raises(IntegrityError, match=FOREIGN_KEY_FAILED):
        session.execute(delete(AdminAccount).where(AdminAccount.id == account.id))


def test_foreign_keys_to_admin_accounts_are_restrict() -> None:
    """「外键一律 RESTRICT」：指向管理员账号的外键删除行为都写明为 RESTRICT。"""
    foreign_keys = [
        foreign_key
        for table in (AdminSession.__table__, AuditEvent.__table__)
        for foreign_key in table.foreign_keys
    ]

    assert [fk.parent.table.name for fk in foreign_keys] == ["admin_sessions", "audit_events"]
    assert all(fk.column.table.name == "admin_accounts" for fk in foreign_keys)
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys)


@pytest.mark.parametrize(
    ("model", "nullable"),
    [
        (AdminAccount, set()),
        (AdminSession, {"revoked_at"}),
        (AuditEvent, {"target_type", "target_id", "old_value", "new_value"}),
    ],
    ids=["admin_accounts", "admin_sessions", "audit_events"],
)
def test_only_designated_columns_are_nullable(model: type[Base], nullable: set[str]) -> None:
    """「AdminAccount / AuditEvent」：只有会话撤销时间与审计记录的对象类别、对象 ID、旧值、
    新值可为空，其余一律非空。
    """
    actual = {column.name for column in model.__table__.columns if column.nullable}

    assert actual == nullable

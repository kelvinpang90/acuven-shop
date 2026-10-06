"""管理员后台会话、CSRF 令牌、审计记录与登录锁定（app/services/admin_auth.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条：「管理员登录按来源与账号
组合限流，连续失败后短时锁定并告警；锁定解除不绕过密码校验。单一管理员也须服务端授权与会话
到期」，第 6 条「应用日志……不记录……密码」，以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04
管理员登录决定（下称「Kelvin 2026-10-04」）。每条测试的文档字符串写明它守住的设计原句或
Kelvin 的哪一项决定；设计没有直接原句的，写明守住的是 SHOP-TASK-035 验收的哪一条。

数据库用 SQLite 内存库按模型建表，每个连接打开外键检查并断言已打开。Redis 不连真的：
FakeRedis 是本文件自写的内存替身，只实现用到的 GET 与事务管道（INCR、EXPIRE NX），带可推进的
替身时钟与可注入的连接错误。期望的摘要、CSRF 令牌与计数键在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import AdminAccount, AdminSession, AuditEvent
from app.services import order_access
from app.services.admin_auth import (
    ADMIN_ACCOUNT_CREATED,
    ADMIN_LOGIN_FAILED,
    ADMIN_LOGIN_LOCKED,
    ADMIN_LOGIN_SUCCEEDED,
    ADMIN_LOGOUT,
    ADMIN_PASSWORD_RESET,
    check_admin_session,
    check_csrf_token,
    csrf_token_for_cookie,
    issue_admin_session,
    login_identifier,
    login_locked,
    normalize_username,
    record_audit,
    record_login_failure,
    revoke_admin_session,
    revoke_all_admin_sessions,
)
from app.services.pw_hash import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.services.rate_limit import RateLimitUnavailable

NOW = datetime(2026, 10, 6, 2, 0)
USERNAME = "shop_admin"
PASSWORD = "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)
SOURCE = "203.0.113.5"
OTHER_SOURCE = "198.51.100.20"
HOUR = 3600


# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


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
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        yield session


@pytest.fixture
def account(db: Session) -> AdminAccount:
    row = AdminAccount(
        username=USERNAME,
        password_hash=PASSWORD_HASH,
        created_at=NOW - timedelta(days=1),
        password_updated_at=NOW - timedelta(days=1),
    )
    db.add(row)
    db.commit()
    return row


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻，以及可注入的连接错误。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.fail_on: str | None = None

    def advance(self, seconds: int) -> None:
        self.now += seconds

    def ttl(self, key: str) -> int | None:
        self._purge(key)
        if key not in self.expires_at:
            return None
        return self.expires_at[key] - self.now

    def _purge(self, key: str) -> None:
        if key in self.expires_at and self.expires_at[key] <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    def _maybe_fail(self, where: str) -> None:
        if self.fail_on == where:
            raise redis.exceptions.ConnectionError("Error connecting to redis-host:6379")

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    def get(self, key: str) -> bytes | None:
        self._maybe_fail("get")
        self._purge(key)
        if key not in self.values:
            return None
        return str(self.values[key]).encode()

    def _incr(self, key: str) -> int:
        self._purge(key)
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def _expire(self, key: str, seconds: int, nx: bool) -> bool:
        # 与 Redis 7 相同：NX 只在键没有过期时间时设置。
        self._purge(key)
        if key not in self.values or (nx and key in self.expires_at):
            return False
        self.expires_at[key] = self.now + seconds
        return True


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def incr(self, key: str) -> FakePipeline:
        self.queued.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> FakePipeline:
        self.queued.append(("expire", key, seconds, nx))
        return self

    def execute(self) -> list:
        self.store._maybe_fail("execute")
        batch, self.queued = self.queued, []
        return [
            self.store._incr(c[1]) if c[0] == "incr" else self.store._expire(c[1], c[2], c[3])
            for c in batch
        ]


def _sha256(text_value: str) -> str:
    return hashlib.sha256(text_value.encode()).hexdigest()


def _key(bucket: str, identifier: str) -> str:
    return "acuven_shop:rate_limit:" + bucket + ":" + _sha256(identifier)


def _attempt(fake: FakeRedis, source: str, username: str, password: str) -> str:
    """测试里按 admin_auth 的 docstring 写的顺序走一次登录判定（接口由 SHOP-TASK-036 实现）：
    锁定即拒绝；否则校验密码（用户名不存在时用假哈希）；不通过则计一次失败。"""
    if login_locked(fake, source, username):
        return "locked"
    known = normalize_username(username) == USERNAME
    if verify_password(password, PASSWORD_HASH if known else DUMMY_PASSWORD_HASH):
        return "ok"
    return "just_locked" if record_login_failure(fake, source, username) else "failed"


def _fail(fake: FakeRedis, times: int, source: str = SOURCE, username: str = USERNAME) -> list:
    return [record_login_failure(fake, source, username) for _ in range(times)]


# ---------------------------------------------------------------------------
# 后台会话
# ---------------------------------------------------------------------------


def test_issued_session_checks_and_returns_account_and_expiry(
    db: Session, account: AdminAccount
) -> None:
    """守住「单一管理员也须服务端授权与会话到期」与 Kelvin 2026-10-04「后台会话自登录起 30 天
    到期」：签发后按 cookie 值校验通过，返回的会话记录带所属账号与到期时间（签发时刻加 30 天）。"""
    issued = issue_admin_session(db, account.id, NOW)
    db.commit()

    found = check_admin_session(db, issued.token, NOW + timedelta(minutes=1))

    assert found is not None
    assert found.admin_account_id == account.id
    assert found.expires_at == NOW + timedelta(days=30)
    assert issued.expires_at == NOW + timedelta(days=30)
    assert found.revoked_at is None


def test_session_expires_after_30_days_and_use_does_not_extend(
    db: Session, account: AdminAccount
) -> None:
    """守住「会话到期」与 Kelvin 2026-10-04「后台会话自登录起 30 天到期，不随使用延长」：
    到期前一秒仍通过，期间反复校验不改到期时间，到期时刻起不通过。"""
    issued = issue_admin_session(db, account.id, NOW)
    db.commit()

    for day in range(0, 30, 5):
        assert check_admin_session(db, issued.token, NOW + timedelta(days=day)) is not None
    almost = NOW + timedelta(days=30) - timedelta(seconds=1)
    assert check_admin_session(db, issued.token, almost) is not None
    assert check_admin_session(db, issued.token, NOW + timedelta(days=30)) is None
    assert check_admin_session(db, issued.token, NOW + timedelta(days=31)) is None
    stored = db.scalars(select(AdminSession)).one()
    assert stored.expires_at == NOW + timedelta(days=30)


def test_revoked_session_does_not_check(db: Session, account: AdminAccount) -> None:
    """守住「单一管理员也须服务端授权」与 UX 0.8 的后台退出（Kelvin 2026-10-05：退出后撤销当前
    后台会话）：撤销后不通过；行保留、写撤销时间；另一个会话不受影响。"""
    first = issue_admin_session(db, account.id, NOW)
    second = issue_admin_session(db, account.id, NOW)
    db.commit()

    found = check_admin_session(db, first.token, NOW)
    assert found is not None
    revoke_admin_session(db, found, NOW + timedelta(hours=1))
    db.commit()

    assert check_admin_session(db, first.token, NOW + timedelta(hours=2)) is None
    assert check_admin_session(db, second.token, NOW + timedelta(hours=2)) is not None
    assert found.revoked_at == NOW + timedelta(hours=1)
    assert len(db.scalars(select(AdminSession)).all()) == 2


def test_revoke_all_sessions_of_account(db: Session, account: AdminAccount) -> None:
    """守住验收「撤销某账号全部会话」（重设密码时）：全部会话不再通过，返回本次撤销的条数，
    已撤销的会话保留原撤销时间。"""
    tokens = [issue_admin_session(db, account.id, NOW).token for _ in range(3)]
    already = check_admin_session(db, tokens[0], NOW)
    assert already is not None
    revoke_admin_session(db, already, NOW + timedelta(minutes=5))
    db.commit()

    revoked = revoke_all_admin_sessions(db, account.id, NOW + timedelta(hours=1))
    db.commit()

    assert revoked == 2
    for token in tokens:
        assert check_admin_session(db, token, NOW + timedelta(hours=2)) is None
    times = sorted(row.revoked_at for row in db.scalars(select(AdminSession)))
    assert times == [NOW + timedelta(minutes=5)] + [NOW + timedelta(hours=1)] * 2


def test_issue_and_revoke_only_flush(db: Session, account: AdminAccount) -> None:
    """守住验收「签发与撤销只 flush 不提交，由调用方提交」：调用方回滚时签发与撤销都不生效。"""
    issued = issue_admin_session(db, account.id, NOW)
    assert check_admin_session(db, issued.token, NOW) is not None
    db.rollback()
    assert check_admin_session(db, issued.token, NOW) is None

    kept = issue_admin_session(db, account.id, NOW)
    db.commit()
    found = check_admin_session(db, kept.token, NOW)
    assert found is not None
    revoke_admin_session(db, found, NOW)
    revoke_all_admin_sessions(db, account.id, NOW)
    db.rollback()
    assert check_admin_session(db, kept.token, NOW) is not None


def test_session_requires_existing_account(db: Session) -> None:
    """守住「单一管理员也须服务端授权」：会话必须属于存在的管理员账号，外键在 flush 时拒绝。"""
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        issue_admin_session(db, 999, NOW)


def test_database_stores_only_token_digest(db: Session, account: AdminAccount) -> None:
    """守住验收「库里只存 SHA-256 摘要」「库里没有令牌原文」：令牌为 32 字节随机数的
    Base64url（43 个字符），会话行只有另算的摘要，任何列都不含令牌原文或 CSRF 令牌。"""
    issued = issue_admin_session(db, account.id, NOW)
    db.commit()

    assert len(issued.token) == 43
    row = db.execute(text("SELECT * FROM admin_sessions")).one()
    values = [str(value) for value in row]
    assert row.token_hash == _sha256(issued.token)
    assert all(issued.token not in value for value in values)
    assert all(issued.csrf_token not in value for value in values)
    assert issued.token not in repr(issued)
    assert issued.csrf_token not in repr(issued)


def test_tokens_are_unique(db: Session, account: AdminAccount) -> None:
    """守住「服务端授权」：每次签发的令牌都不同（secrets 生成），摘要不冲突。"""
    tokens = {issue_admin_session(db, account.id, NOW).token for _ in range(20)}

    assert len(tokens) == 20


@pytest.mark.parametrize(
    "cookie",
    [None, "", "short", "!" * 43, "A" * 42, "A" * 44, "A" * 42 + "=", 12345],
    ids=["none", "empty", "short", "bad-chars", "42", "44", "padding", "not-str"],
)
def test_malformed_cookie_does_not_query_database(
    engine: Engine, db: Session, account: AdminAccount, cookie: object
) -> None:
    """守住验收「格式不合法不查库」：格式不合法的 cookie 值直接不通过，不发任何 SQL。"""
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))

    result = check_admin_session(db, cookie, NOW)  # type: ignore[arg-type]

    assert result is None
    assert statements == []


def test_unknown_token_does_not_check(db: Session, account: AdminAccount) -> None:
    """守住「单一管理员也须服务端授权」：格式合法但库里没有的令牌不通过。"""
    issue_admin_session(db, account.id, NOW)
    db.commit()

    assert check_admin_session(db, "A" * 43, NOW) is None


def test_now_must_be_naive_utc(db: Session, account: AdminAccount) -> None:
    """守住模块约定「时间一律是不带时区的 UTC」（沿用 SHOP-TASK-019）：带时区的时间是编程错误。"""
    with pytest.raises(ValueError):
        issue_admin_session(db, account.id, NOW.replace(tzinfo=UTC))


# ---------------------------------------------------------------------------
# CSRF 令牌
# ---------------------------------------------------------------------------


def test_csrf_token_differs_from_stored_digest_and_checks(
    db: Session, account: AdminAccount
) -> None:
    """守住验收「CSRF 令牌由令牌原文加用途前缀做 SHA-256 得到、不入库」：CSRF 令牌不等于入库的
    摘要与令牌原文，可由 cookie 值重新算出，校验通过。"""
    issued = issue_admin_session(db, account.id, NOW)
    db.commit()
    stored = db.scalars(select(AdminSession.token_hash)).one()

    assert issued.csrf_token != stored
    assert issued.csrf_token != issued.token
    assert csrf_token_for_cookie(issued.token) == issued.csrf_token
    assert check_csrf_token(issued.token, issued.csrf_token) is True


def test_csrf_prefix_differs_from_order_access(db: Session, account: AdminAccount) -> None:
    """守住验收「用途前缀不同于订单访问」：同一令牌原文在后台与订单访问得到的 CSRF 令牌不同，
    订单访问的 CSRF 令牌不能用于后台。"""
    issued = issue_admin_session(db, account.id, NOW)
    order_csrf = order_access.csrf_token_for_cookie(issued.token)

    assert order_csrf is not None
    assert order_csrf != issued.csrf_token
    assert check_csrf_token(issued.token, order_csrf) is False


@pytest.mark.parametrize(
    "header", [None, "", "0" * 64, "wrong"], ids=["none", "empty", "zeros", "wrong"]
)
def test_wrong_or_missing_csrf_token_fails(
    db: Session, account: AdminAccount, header: str | None
) -> None:
    """守住验收「CSRF 令牌错误或缺失时不通过」（设计第 4 条「服务端授权」）。"""
    issued = issue_admin_session(db, account.id, NOW)

    assert check_csrf_token(issued.token, header) is False


def test_csrf_token_of_another_session_fails(db: Session, account: AdminAccount) -> None:
    """守住验收「CSRF 令牌……校验」：另一个会话的 CSRF 令牌不通过；cookie 缺失或格式不合法时
    也不通过。"""
    first = issue_admin_session(db, account.id, NOW)
    second = issue_admin_session(db, account.id, NOW)

    assert check_csrf_token(first.token, second.csrf_token) is False
    assert check_csrf_token(None, first.csrf_token) is False
    assert check_csrf_token("short", first.csrf_token) is False
    assert csrf_token_for_cookie(None) is None


# ---------------------------------------------------------------------------
# 审计记录
# ---------------------------------------------------------------------------


def test_action_constants() -> None:
    """守住 Kelvin 2026-10-04「本轮审计只记登录成功、失败、锁定、退出与建账号、重设密码」。"""
    assert [
        ADMIN_LOGIN_SUCCEEDED,
        ADMIN_LOGIN_FAILED,
        ADMIN_LOGIN_LOCKED,
        ADMIN_LOGOUT,
        ADMIN_ACCOUNT_CREATED,
        ADMIN_PASSWORD_RESET,
    ] == [
        "admin_login_succeeded",
        "admin_login_failed",
        "admin_login_locked",
        "admin_logout",
        "admin_account_created",
        "admin_password_reset",
    ]


def test_record_audit_writes_event_without_values(db: Session, account: AdminAccount) -> None:
    """守住「AdminAccount / AuditEvent：……后台操作记录」与 Kelvin 2026-10-04「每条审计记录都
    属于某个账号；审计记录不存来源地址与提交的用户名」：写入时间、账号与操作名，本任务的操作
    不带对象与新旧值；只 flush，调用方回滚即不写入。"""
    event_row = record_audit(db, ADMIN_LOGIN_FAILED, account.id, NOW)
    assert db.scalars(select(AuditEvent)).one() is event_row
    db.rollback()
    assert db.scalars(select(AuditEvent)).all() == []

    record_audit(db, ADMIN_LOGIN_SUCCEEDED, account.id, NOW)
    db.commit()
    row = db.scalars(select(AuditEvent)).one()
    assert (row.occurred_at, row.admin_account_id, row.action) == (
        NOW,
        account.id,
        "admin_login_succeeded",
    )
    assert (row.target_type, row.target_id, row.old_value, row.new_value) == (None,) * 4


def test_record_audit_accepts_target_and_values(db: Session, account: AdminAccount) -> None:
    """守住 SiteSetting 一行「后台修改写入 AuditEvent（操作者、时间、新旧值）」：写审计记录的
    函数接收可选的对象类别与 ID、旧值与新值，供之后的业务接口任务使用。"""
    row = record_audit(
        db,
        "site_setting_updated",
        account.id,
        NOW,
        target_type="site_setting",
        target_id=1,
        old_value="false",
        new_value="true",
    )

    assert (row.target_type, row.target_id, row.old_value, row.new_value) == (
        "site_setting",
        1,
        "false",
        "true",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": ""},
        {"action": "Admin Login"},
        {"action": "a" * 41},
        {"action": ADMIN_LOGOUT, "target_type": "order"},
        {"action": ADMIN_LOGOUT, "target_id": 1},
        {"action": ADMIN_LOGOUT, "target_type": "Order!", "target_id": 1},
    ],
    ids=["empty", "not-constant", "too-long", "type-only", "id-only", "bad-type"],
)
def test_record_audit_rejects_invalid_action_or_target(
    db: Session, account: AdminAccount, kwargs: dict
) -> None:
    """守住「后台操作记录」的操作名取自写入方常量、对象类别与 ID 成对：不合规时是编程错误，
    抛 ValueError、不写库。"""
    kwargs = dict(kwargs)
    action = kwargs.pop("action")
    with pytest.raises(ValueError):
        record_audit(db, action, account.id, NOW, **kwargs)

    assert db.scalars(select(AuditEvent)).all() == []


# ---------------------------------------------------------------------------
# 登录锁定
# ---------------------------------------------------------------------------


def test_tenth_failure_returns_just_locked_and_eleventh_does_not() -> None:
    """守住「连续失败后短时锁定」与 Kelvin 2026-10-04「同一来源与用户名失败满 10 次即锁定」：
    前 9 次失败不锁、不报刚锁定，第 10 次返回刚锁定，第 11 次不再返回刚锁定。"""
    fake = FakeRedis()

    assert _fail(fake, 9) == [False] * 9
    assert login_locked(fake, SOURCE, USERNAME) is False
    assert _fail(fake, 1) == [True]
    assert login_locked(fake, SOURCE, USERNAME) is True
    assert _fail(fake, 1) == [False]


def test_correct_password_rejected_while_locked_and_accepted_after() -> None:
    """守住「锁定解除不绕过密码校验」与 Kelvin 2026-10-04「锁定 1 小时」：锁定期间正确密码
    也被拒；锁定窗口过后错误密码照常不通过，正确密码通过。"""
    fake = FakeRedis()
    assert _attempt(fake, SOURCE, USERNAME, PASSWORD) == "ok"
    _fail(fake, 9)
    assert _attempt(fake, SOURCE, USERNAME, "wrong password 10") == "just_locked"

    assert _attempt(fake, SOURCE, USERNAME, PASSWORD) == "locked"
    fake.advance(HOUR - 1)
    assert _attempt(fake, SOURCE, USERNAME, PASSWORD) == "locked"

    fake.advance(1)
    assert login_locked(fake, SOURCE, USERNAME) is False
    assert _attempt(fake, SOURCE, USERNAME, "wrong password 11") == "failed"
    assert _attempt(fake, SOURCE, USERNAME, PASSWORD) == "ok"


def test_lock_lasts_one_hour_from_first_lock_and_is_not_extended() -> None:
    """守住 Kelvin 2026-10-04「锁定 1 小时」与验收「锁定桶的过期只在首次设置，即从首次计入起锁
    1 小时」：锁定期间再计入锁定桶不延长锁定。"""
    fake = FakeRedis()
    _fail(fake, 10)
    lock_key = _key("admin_login_lock", f'["{SOURCE}", "{USERNAME}"]')
    assert fake.ttl(lock_key) == HOUR

    fake.advance(HOUR // 2)
    assert _fail(fake, 3) == [False] * 3
    assert fake.ttl(lock_key) == HOUR // 2
    fake.advance(HOUR // 2)
    assert login_locked(fake, SOURCE, USERNAME) is False


def test_success_does_not_reset_failures_within_window() -> None:
    """守住 Kelvin 2026-10-04「按 1 小时窗口内累计满 10 次计，成功登录不清零」：中间夹着成功
    登录，窗口内第 10 次失败照样锁定。"""
    fake = FakeRedis()
    _fail(fake, 5)
    assert _attempt(fake, SOURCE, USERNAME, PASSWORD) == "ok"
    assert _fail(fake, 4) == [False] * 4
    assert _fail(fake, 1) == [True]


def test_failures_outside_window_do_not_accumulate() -> None:
    """守住 Kelvin 2026-10-04「计数用 SHOP-TASK-026 的固定窗口，按 1 小时窗口内累计」：窗口过后
    计数从零开始，之前的失败不再累计。"""
    fake = FakeRedis()
    _fail(fake, 9)
    fake.advance(HOUR)
    assert _fail(fake, 9) == [False] * 9
    assert login_locked(fake, SOURCE, USERNAME) is False


def test_concurrent_failures_report_just_locked_once() -> None:
    """守住验收「并发失败也只报一次刚锁定」：两个并发的失败都已把失败计数推过上限（第 10、
    11 次）时，只有先计入锁定桶的一次返回刚锁定。"""
    fake = FakeRedis()
    _fail(fake, 9)
    # 两个并发请求都已自增失败计数，尚未计入锁定桶。
    failure_key = _key("admin_login_failures", f'["{SOURCE}", "{USERNAME}"]')
    fake.values[failure_key] += 2

    assert _fail(fake, 2) == [True, False]


def test_other_source_or_username_not_affected() -> None:
    """守住「管理员登录按来源与账号组合限流」与 Kelvin 2026-10-04「按来源与用户名组合计数，
    攻击者无法把唯一的管理员锁在外面」：一个来源锁住某用户名后，另一来源对同一用户名、同一
    来源对另一用户名都不受影响，正确密码照常通过。"""
    fake = FakeRedis()
    _fail(fake, 10)

    assert login_locked(fake, SOURCE, USERNAME) is True
    assert login_locked(fake, OTHER_SOURCE, USERNAME) is False
    assert login_locked(fake, SOURCE, "someone_else") is False
    assert _attempt(fake, OTHER_SOURCE, USERNAME, PASSWORD) == "ok"
    assert _fail(fake, 1, OTHER_SOURCE) == [False]
    assert _fail(fake, 1, SOURCE, "someone_else") == [False]


def test_username_case_and_whitespace_are_the_same() -> None:
    """守住验收「规范化用户名（去掉首尾空白、转小写）」：大小写与首尾空白不同的用户名计入同一
    个锁定，攻击者不能换写法绕过。"""
    fake = FakeRedis()
    _fail(fake, 4, SOURCE, "Shop_Admin")
    _fail(fake, 5, SOURCE, "  shop_admin\t")
    assert _fail(fake, 1, SOURCE, "SHOP_ADMIN ") == [True]

    assert login_locked(fake, SOURCE, USERNAME) is True
    assert login_locked(fake, SOURCE, " Shop_ADMIN ") is True
    assert normalize_username("  Shop_Admin\n") == "shop_admin"


def test_ipv6_source_is_not_confused_with_another_source() -> None:
    """守住验收「标识为 [访客来源, 规范化用户名] 的 JSON 编码，不用分隔符拼接（IPv6 来源含
    冒号）」：以冒号拼接时「2001:db8::1」加「beef:admin」与「2001:db8::1:beef」加「admin」
    相同，这里两者互不影响。"""
    fake = FakeRedis()
    first = ("2001:db8::1", "beef:admin")
    second = ("2001:db8::1:beef", "admin")
    assert ":".join(first) == ":".join(second)

    _fail(fake, 10, *first)

    assert login_locked(fake, *first) is True
    assert login_locked(fake, *second) is False
    assert login_identifier(*first) != login_identifier(*second)


def test_counter_keys_use_json_identifier_digest() -> None:
    """守住验收「标识为 [访客来源, 规范化用户名] 的 JSON 编码」「失败计数桶 admin_login_failures、
    锁定桶 admin_login_lock」：键为另算的 JSON 标识摘要，来源与用户名原文不进 Redis。"""
    fake = FakeRedis()
    _fail(fake, 10, "2001:db8::1", " Shop_Admin ")
    identifier = '["2001:db8::1", "shop_admin"]'

    assert set(fake.values) == {
        _key("admin_login_failures", identifier),
        _key("admin_login_lock", identifier),
    }
    assert fake.values[_key("admin_login_failures", identifier)] == 10
    assert fake.ttl(_key("admin_login_failures", identifier)) == HOUR
    for key in fake.values:
        assert "2001" not in key
        assert "shop_admin" not in key.lower()


@pytest.mark.parametrize("where", ["get", "execute"])
def test_redis_unavailable_raises(where: str) -> None:
    """守住「失败、并发与重试」第 4 条「管理员登录等依赖 Redis 限流的敏感接口也拒绝请求」：
    替身抛连接错误时判定与计数都抛 SHOP-TASK-026 的不可用异常，不当作未锁定放行。"""
    fake = FakeRedis()
    fake.fail_on = where

    with pytest.raises(RateLimitUnavailable):
        if where == "get":
            login_locked(fake, SOURCE, USERNAME)
        else:
            record_login_failure(fake, SOURCE, USERNAME)


def test_module_does_not_log(
    db: Session, account: AdminAccount, caplog: pytest.LogCaptureFixture
) -> None:
    """守住「应用日志……不记录……密码」与模块 docstring「不写日志」：会话、CSRF、
    审计与锁定函数都不产生 app. 下的日志记录。"""
    fake = FakeRedis()
    with caplog.at_level(logging.DEBUG):
        issued = issue_admin_session(db, account.id, NOW)
        session = check_admin_session(db, issued.token, NOW)
        assert session is not None
        check_csrf_token(issued.token, "wrong")
        revoke_admin_session(db, session, NOW)
        record_audit(db, ADMIN_LOGOUT, account.id, NOW)
        _fail(fake, 11)
        login_locked(fake, SOURCE, USERNAME)

    assert [r for r in caplog.records if r.name.startswith("app.")] == []

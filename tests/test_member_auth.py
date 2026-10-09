"""会员会话、CSRF 令牌、密码校验与密码登录锁定（app/services/member_auth.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：「未设密码的账号只能短信
登录，密码登录对其与对错误密码返回相同的通用失败，不暴露账号是否存在或是否设了密码」「密码
至少 8 位、不强制复杂度，安全哈希存储；会话到期、退出与服务端授权检查」，第 6 条「应用日志与
监控不记录……完整电话……密码」，以及 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（3）
（下称「Kelvin 2026-10-08」）：「会员会话 30 天、不随使用延长；同一号码加来源 15 分钟内密码
登录失败 5 次即锁 15 分钟，失败一律通用提示；重设密码与注销时撤销该会员的全部会话」。
每条测试的文档字符串写明它守住的设计原句或 Kelvin 的哪一句；设计没有直接原句的，写明守住的
是 SHOP-TASK-071 验收的哪一条。

数据库用 SQLite 内存库按模型建表，每个连接打开外键检查并断言已打开。Redis 不连真的：
FakeRedis 照 tests/test_admin_auth.py 的写法自写，只实现用到的 GET 与事务管道（INCR、EXPIRE NX），
带可推进的替身时钟与可注入的连接错误。期望的摘要、CSRF 令牌与计数键在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, MemberSession
from app.services import admin_auth, order_access, pw_hash
from app.services.member_auth import (
    authenticate_member,
    check_csrf_token,
    check_member_session,
    csrf_token_for_cookie,
    issue_member_session,
    login_identifier,
    login_locked,
    password_length_ok,
    password_login,
    record_login_failure,
    revoke_all_member_sessions,
    revoke_member_session,
)
from app.services.rate_limit import RateLimitUnavailable

NOW = datetime(2026, 10, 9, 2, 0)
PHONE = "+60123456789"
OTHER_PHONE = "+6591234567"
NO_PASSWORD_PHONE = "+60198765432"
UNREGISTERED_PHONE = "+60111222333"
PASSWORD = "correct horse"
PASSWORD_HASH = pw_hash.hash_password(PASSWORD)
SOURCE = "203.0.113.5"
OTHER_SOURCE = "198.51.100.20"
MINUTES_15 = 900


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


def _member(db: Session, phone: str, password_hash: str | None) -> Member:
    row = Member(
        phone=phone,
        password_hash=password_hash,
        status="active",
        created_at=NOW - timedelta(days=1),
        deleted_at=None,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def member(db: Session) -> Member:
    return _member(db, PHONE, PASSWORD_HASH)


@pytest.fixture
def no_password_member(db: Session) -> Member:
    return _member(db, NO_PASSWORD_PHONE, None)


def _delete(db: Session, row: Member) -> None:
    """按 SHOP-TASK-012 的模型约定注销：清空号码与密码哈希、改状态、写注销时间。"""
    row.phone = None
    row.password_hash = None
    row.status = "deleted"
    row.deleted_at = NOW + timedelta(hours=1)
    db.commit()


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


def _fail(fake: FakeRedis, times: int, source: str = SOURCE, phone: str = PHONE) -> list:
    return [record_login_failure(fake, source, phone) for _ in range(times)]


@pytest.fixture
def verify_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记下每次 pw_hash.verify_password 用的哈希（不记密码），照常返回真实结果。"""
    calls: list[str] = []
    real = pw_hash.verify_password

    def _recording(password: str, encoded: str) -> bool:
        calls.append(encoded)
        return real(password, encoded)

    monkeypatch.setattr(pw_hash, "verify_password", _recording)
    return calls


# ---------------------------------------------------------------------------
# 会员会话
# ---------------------------------------------------------------------------


def test_issued_session_checks_and_returns_session_and_member(db: Session, member: Member) -> None:
    """守住「会话到期……与服务端授权检查」与 Kelvin 2026-10-08「会员会话 30 天」：签发后按
    cookie 值校验通过，返回会话与会员，到期时间为签发时刻加 30 天。"""
    issued = issue_member_session(db, member, NOW)
    db.commit()

    found = check_member_session(db, issued.token, NOW + timedelta(minutes=1))

    assert found is not None
    assert found.member.id == member.id
    assert found.session.member_id == member.id
    assert found.session.expires_at == NOW + timedelta(days=30)
    assert issued.expires_at == NOW + timedelta(days=30)
    assert found.session.revoked_at is None


def test_session_expires_after_30_days_and_use_does_not_extend(db: Session, member: Member) -> None:
    """守住「会话到期」与 Kelvin 2026-10-08「会员会话 30 天、不随使用延长」：到期前一秒仍
    通过，期间反复校验不改到期时间，到期时刻起不通过。"""
    issued = issue_member_session(db, member, NOW)
    db.commit()

    for day in range(0, 30, 5):
        assert check_member_session(db, issued.token, NOW + timedelta(days=day)) is not None
    almost = NOW + timedelta(days=30) - timedelta(seconds=1)
    assert check_member_session(db, issued.token, almost) is not None
    assert check_member_session(db, issued.token, NOW + timedelta(days=30)) is None
    assert check_member_session(db, issued.token, NOW + timedelta(days=31)) is None
    stored = db.scalars(select(MemberSession)).one()
    assert stored.expires_at == NOW + timedelta(days=30)


def test_database_stores_only_token_digest(db: Session, member: Member) -> None:
    """守住验收「令牌原文为 secrets.token_urlsafe(32)，库里只存其 SHA-256 十六进制摘要」与签发
    结果 repr 不含令牌：令牌 43 个字符，会话行只有另算的摘要，任何列都不含令牌或 CSRF 令牌。"""
    issued = issue_member_session(db, member, NOW)
    db.commit()

    assert len(issued.token) == 43
    row = db.execute(text("SELECT * FROM member_sessions")).one()
    values = [str(value) for value in row]
    assert row.token_hash == _sha256(issued.token)
    assert all(issued.token not in value for value in values)
    assert all(issued.csrf_token not in value for value in values)
    assert issued.token not in repr(issued)
    assert issued.csrf_token not in repr(issued)


def test_tokens_are_unique(db: Session, member: Member) -> None:
    """守住「服务端授权检查」：每次签发的令牌都不同（secrets 生成），摘要不冲突。"""
    tokens = {issue_member_session(db, member, NOW).token for _ in range(20)}

    assert len(tokens) == 20


def test_issue_requires_active_member(db: Session, member: Member) -> None:
    """守住验收「签发会话……会员状态须为 active」（设计「资料保留」注销后不再可访问）：已注销
    会员签发时抛 ValueError、不写库，消息不含号码。"""
    _delete(db, member)

    with pytest.raises(ValueError) as excinfo:
        issue_member_session(db, member, NOW)

    assert PHONE not in str(excinfo.value)
    assert db.scalars(select(MemberSession)).all() == []


def test_deleted_member_session_does_not_check(db: Session, member: Member) -> None:
    """守住验收「会员已不是 active 不通过」（设计「服务端授权检查」）：会话签发后会员注销，
    即使会话未撤销、未到期也不再通过。"""
    issued = issue_member_session(db, member, NOW)
    db.commit()
    assert check_member_session(db, issued.token, NOW) is not None

    _delete(db, member)

    assert check_member_session(db, issued.token, NOW + timedelta(hours=2)) is None


def test_revoked_session_does_not_check(db: Session, member: Member) -> None:
    """守住设计「退出」与验收「撤销单个会话（写撤销时间，行保留）」：撤销后不通过；行保留、
    写撤销时间；同一会员的另一个会话不受影响；再撤销不改原撤销时间。"""
    first = issue_member_session(db, member, NOW)
    second = issue_member_session(db, member, NOW)
    db.commit()

    found = check_member_session(db, first.token, NOW)
    assert found is not None
    revoke_member_session(db, found.session, NOW + timedelta(hours=1))
    db.commit()
    revoke_member_session(db, found.session, NOW + timedelta(hours=3))
    db.commit()

    assert check_member_session(db, first.token, NOW + timedelta(hours=2)) is None
    assert check_member_session(db, second.token, NOW + timedelta(hours=2)) is not None
    assert found.session.revoked_at == NOW + timedelta(hours=1)
    assert len(db.scalars(select(MemberSession)).all()) == 2


def test_revoke_all_sessions_of_member(db: Session, member: Member) -> None:
    """守住 Kelvin 2026-10-08「重设密码与注销时撤销该会员的全部会话」：全部会话不再通过，返回
    本次撤销的条数，已撤销的保留原撤销时间，行都保留，其他会员的会话不受影响。"""
    other = _member(db, OTHER_PHONE, PASSWORD_HASH)
    tokens = [issue_member_session(db, member, NOW).token for _ in range(3)]
    other_token = issue_member_session(db, other, NOW).token
    already = check_member_session(db, tokens[0], NOW)
    assert already is not None
    revoke_member_session(db, already.session, NOW + timedelta(minutes=5))
    db.commit()

    revoked = revoke_all_member_sessions(db, member.id, NOW + timedelta(hours=1))
    db.commit()

    assert revoked == 2
    for token in tokens:
        assert check_member_session(db, token, NOW + timedelta(hours=2)) is None
    assert check_member_session(db, other_token, NOW + timedelta(hours=2)) is not None
    times = sorted(
        row.revoked_at
        for row in db.scalars(select(MemberSession).where(MemberSession.member_id == member.id))
    )
    assert times == [NOW + timedelta(minutes=5)] + [NOW + timedelta(hours=1)] * 2
    assert len(db.scalars(select(MemberSession)).all()) == 4


def test_issue_and_revoke_only_flush(db: Session, member: Member) -> None:
    """守住验收「只 flush、不提交」：调用方回滚时签发与撤销都不生效。"""
    issued = issue_member_session(db, member, NOW)
    assert check_member_session(db, issued.token, NOW) is not None
    db.rollback()
    assert check_member_session(db, issued.token, NOW) is None

    kept = issue_member_session(db, member, NOW)
    db.commit()
    found = check_member_session(db, kept.token, NOW)
    assert found is not None
    revoke_member_session(db, found.session, NOW)
    revoke_all_member_sessions(db, member.id, NOW)
    db.rollback()
    assert check_member_session(db, kept.token, NOW) is not None


@pytest.mark.parametrize(
    "cookie",
    [None, "", "short", "!" * 43, "A" * 42, "A" * 44, "A" * 42 + "=", 12345],
    ids=["none", "empty", "short", "bad-chars", "42", "44", "padding", "not-str"],
)
def test_malformed_cookie_does_not_query_database(
    engine: Engine, db: Session, member: Member, cookie: object
) -> None:
    """守住验收「校验 cookie 值：格式不对……不通过」：格式不合法的 cookie 值直接不通过，
    不发任何 SQL。"""
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))

    result = check_member_session(db, cookie, NOW)  # type: ignore[arg-type]

    assert result is None
    assert statements == []


def test_unknown_token_does_not_check(db: Session, member: Member) -> None:
    """守住验收「会话不存在……不通过」（设计「服务端授权检查」）：格式合法但库里没有的令牌
    不通过。"""
    issue_member_session(db, member, NOW)
    db.commit()

    assert check_member_session(db, "A" * 43, NOW) is None


def test_now_must_be_naive_utc(db: Session, member: Member) -> None:
    """守住模块约定「时间一律是不带时区的 UTC」（沿用 SHOP-TASK-035）：带时区的时间是编程错误。"""
    with pytest.raises(ValueError):
        issue_member_session(db, member, NOW.replace(tzinfo=UTC))
    with pytest.raises(ValueError):
        check_member_session(db, "A" * 43, NOW.replace(tzinfo=UTC))


# ---------------------------------------------------------------------------
# CSRF 令牌
# ---------------------------------------------------------------------------


def test_csrf_token_differs_from_stored_digest_and_checks(db: Session, member: Member) -> None:
    """守住验收「CSRF 令牌不入库，由令牌原文加本模块独有的用途前缀做 SHA-256 得到」：
    CSRF 令牌等于另算的带前缀摘要，不等于入库的摘要与令牌原文，可重新算出，校验通过。"""
    issued = issue_member_session(db, member, NOW)
    db.commit()
    stored = db.scalars(select(MemberSession.token_hash)).one()

    expected = hashlib.sha256(
        b"acuven-shop/member-session/csrf\x00" + issued.token.encode()
    ).hexdigest()
    assert issued.csrf_token == expected
    assert issued.csrf_token != stored
    assert issued.csrf_token != issued.token
    assert csrf_token_for_cookie(issued.token) == issued.csrf_token
    assert check_csrf_token(issued.token, issued.csrf_token) is True


def test_csrf_prefix_differs_from_admin_and_order_access(db: Session, member: Member) -> None:
    """守住验收「前缀不同于 app/services/admin_auth.py 与 app/services/order_access.py」：同一
    令牌原文在会员、后台与订单访问得到的 CSRF 令牌两两不同，后两者的不能用于会员。"""
    issued = issue_member_session(db, member, NOW)
    admin_csrf = admin_auth.csrf_token_for_cookie(issued.token)
    order_csrf = order_access.csrf_token_for_cookie(issued.token)

    assert admin_csrf is not None and order_csrf is not None
    assert len({issued.csrf_token, admin_csrf, order_csrf}) == 3
    assert check_csrf_token(issued.token, admin_csrf) is False
    assert check_csrf_token(issued.token, order_csrf) is False


@pytest.mark.parametrize(
    "header", [None, "", "0" * 64, "wrong"], ids=["none", "empty", "zeros", "wrong"]
)
def test_wrong_or_missing_csrf_token_fails(db: Session, member: Member, header: str | None) -> None:
    """守住验收「CSRF……错与缺都不通过」（设计「服务端授权检查」）。"""
    issued = issue_member_session(db, member, NOW)

    assert check_csrf_token(issued.token, header) is False


def test_csrf_token_of_another_session_fails(db: Session, member: Member) -> None:
    """守住验收「CSRF……错与缺都不通过」：另一个会话的 CSRF 令牌不通过；cookie 缺失或格式
    不合法时也不通过。"""
    first = issue_member_session(db, member, NOW)
    second = issue_member_session(db, member, NOW)

    assert check_csrf_token(first.token, second.csrf_token) is False
    assert check_csrf_token(None, first.csrf_token) is False
    assert check_csrf_token("short", first.csrf_token) is False
    assert csrf_token_for_cookie(None) is None


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------


def test_correct_password_returns_member(
    db: Session, member: Member, verify_calls: list[str]
) -> None:
    """守住「会员可用手机号加密码……登录」「安全哈希存储」：正确密码返回该会员，以库里的
    哈希校验一次。"""
    found = authenticate_member(db, PHONE, PASSWORD)

    assert found is not None and found.id == member.id
    assert verify_calls == [PASSWORD_HASH]


def test_three_password_failures_are_identical_and_each_hash_once(
    db: Session, member: Member, no_password_member: Member, verify_calls: list[str]
) -> None:
    """守住「密码登录对其（未设密码的账号）与对错误密码返回相同的通用失败，不暴露账号是否存在
    或是否设了密码」：号码未注册、未设密码、密码错误三种失败都返回 None，且各恰好调用一次哈希
    校验（前两种以假哈希）。"""
    results = [
        authenticate_member(db, UNREGISTERED_PHONE, PASSWORD),
        authenticate_member(db, NO_PASSWORD_PHONE, PASSWORD),
        authenticate_member(db, PHONE, "wrong password"),
    ]

    assert results == [None, None, None]
    assert verify_calls == [
        pw_hash.DUMMY_PASSWORD_HASH,
        pw_hash.DUMMY_PASSWORD_HASH,
        PASSWORD_HASH,
    ]


def test_deleted_member_cannot_password_login(
    db: Session, member: Member, verify_calls: list[str]
) -> None:
    """守住验收「按号码找 active 会员」（设计「资料保留」注销后不再可访问）：注销后的号码与
    未注册一样，以假哈希校验一次、返回 None。"""
    _delete(db, member)

    assert authenticate_member(db, PHONE, PASSWORD) is None
    assert verify_calls == [pw_hash.DUMMY_PASSWORD_HASH]


@pytest.mark.parametrize(
    ("password", "ok"),
    [("", False), ("1234567", False), ("12345678", True), ("pässwörd", True), (None, False)],
    ids=["empty", "7", "8", "8-non-ascii", "not-str"],
)
def test_password_length_rule(password: object, ok: bool) -> None:
    """守住「密码至少 8 位、不强制复杂度」：按字符计，至少 8 个字符即可，不要求字符种类。"""
    assert password_length_ok(password) is ok


# ---------------------------------------------------------------------------
# 登录锁定
# ---------------------------------------------------------------------------


def test_fifth_failure_returns_just_locked_and_sixth_does_not() -> None:
    """守住 Kelvin 2026-10-08「同一号码加来源 15 分钟内密码登录失败 5 次即锁」：前 4 次失败不锁、
    不报刚锁定，第 5 次返回刚锁定，第 6 次不再返回刚锁定。"""
    fake = FakeRedis()

    assert _fail(fake, 4) == [False] * 4
    assert login_locked(fake, SOURCE, PHONE) is False
    assert _fail(fake, 1) == [True]
    assert login_locked(fake, SOURCE, PHONE) is True
    assert _fail(fake, 1) == [False]


def test_correct_password_rejected_while_locked_and_checked_after(
    db: Session, member: Member, verify_calls: list[str]
) -> None:
    """守住 Kelvin 2026-10-08「锁 15 分钟」与验收「锁定期间即使密码正确也拒绝，锁定到期后照常
    校验」：锁定期间正确密码也被拒、不校验密码；15 分钟后错误密码照常不通过，正确密码通过。"""
    fake = FakeRedis()
    assert password_login(db, fake, SOURCE, PHONE, PASSWORD) is not None
    for _ in range(5):
        assert password_login(db, fake, SOURCE, PHONE, "wrong password") is None
    assert login_locked(fake, SOURCE, PHONE) is True
    calls_before = len(verify_calls)

    assert password_login(db, fake, SOURCE, PHONE, PASSWORD) is None
    fake.advance(MINUTES_15 - 1)
    assert password_login(db, fake, SOURCE, PHONE, PASSWORD) is None
    assert len(verify_calls) == calls_before

    fake.advance(1)
    assert login_locked(fake, SOURCE, PHONE) is False
    assert password_login(db, fake, SOURCE, PHONE, "wrong password") is None
    found = password_login(db, fake, SOURCE, PHONE, PASSWORD)
    assert found is not None and found.id == member.id


def test_lock_lasts_15_minutes_from_first_lock_and_is_not_extended() -> None:
    """守住 Kelvin 2026-10-08「锁 15 分钟」与验收「锁定桶过期只在首次计入时设置」：锁定期间
    再计入锁定桶不延长锁定。"""
    fake = FakeRedis()
    _fail(fake, 5)
    lock_key = _key("member_login_lock", f'["{SOURCE}", "{PHONE}"]')
    assert fake.ttl(lock_key) == MINUTES_15

    fake.advance(MINUTES_15 // 3)
    assert _fail(fake, 3) == [False] * 3
    assert fake.ttl(lock_key) == MINUTES_15 - MINUTES_15 // 3
    fake.advance(MINUTES_15 - MINUTES_15 // 3)
    assert login_locked(fake, SOURCE, PHONE) is False


def test_success_does_not_reset_failures_within_window(db: Session, member: Member) -> None:
    """守住验收「成功登录不清零失败计数」（Kelvin 2026-10-08「15 分钟内……失败 5 次」按窗口内
    累计）：中间夹着成功登录，窗口内第 5 次失败照样锁定。"""
    fake = FakeRedis()
    _fail(fake, 2)
    assert password_login(db, fake, SOURCE, PHONE, PASSWORD) is not None
    assert _fail(fake, 2) == [False] * 2
    assert _fail(fake, 1) == [True]


def test_failures_outside_window_do_not_accumulate() -> None:
    """守住 Kelvin 2026-10-08「15 分钟内……失败 5 次」：失败窗口过后计数从零开始。"""
    fake = FakeRedis()
    _fail(fake, 4)
    fake.advance(MINUTES_15)
    assert _fail(fake, 4) == [False] * 4
    assert login_locked(fake, SOURCE, PHONE) is False


def test_lock_is_per_source_and_phone(db: Session, member: Member) -> None:
    """守住 Kelvin 2026-10-08「同一号码加来源」：一个来源锁住某号码后，另一来源对同一号码、
    同一来源对另一号码都不受影响，另一来源的正确密码照常通过。"""
    fake = FakeRedis()
    _fail(fake, 5)

    assert login_locked(fake, SOURCE, PHONE) is True
    assert login_locked(fake, OTHER_SOURCE, PHONE) is False
    assert login_locked(fake, SOURCE, OTHER_PHONE) is False
    assert password_login(db, fake, OTHER_SOURCE, PHONE, PASSWORD) is not None
    assert _fail(fake, 1, OTHER_SOURCE) == [False]
    assert _fail(fake, 1, SOURCE, OTHER_PHONE) == [False]


def test_concurrent_failures_report_just_locked_once() -> None:
    """守住验收「写法与 admin_auth 的 record_login_failure 一致」：两个并发失败都已把失败计数
    推过上限（第 5、6 次）时，只有先计入锁定桶的一次返回刚锁定。"""
    fake = FakeRedis()
    _fail(fake, 4)
    failure_key = _key("member_login_failures", f'["{SOURCE}", "{PHONE}"]')
    fake.values[failure_key] += 2

    assert _fail(fake, 2) == [True, False]


def test_ipv6_source_is_not_confused_with_another_source() -> None:
    """守住验收「标识为 [访客来源, 规范化 E.164 号码] 的 JSON 编码」：IPv6 来源含冒号，两个
    来源互不影响，标识不同。"""
    fake = FakeRedis()
    _fail(fake, 5, "2001:db8::1")

    assert login_locked(fake, "2001:db8::1", PHONE) is True
    assert login_locked(fake, "2001:db8::1:6", PHONE) is False
    assert login_identifier("2001:db8::1", PHONE) == f'["2001:db8::1", "{PHONE}"]'


def test_counter_keys_use_json_identifier_digest() -> None:
    """守住验收「标识为 [访客来源, 规范化 E.164 号码] 的 JSON 编码」「失败计数窗口 15 分钟」：
    键为另算的 JSON 标识摘要，来源与号码原文不进 Redis。"""
    fake = FakeRedis()
    _fail(fake, 5)
    identifier = f'["{SOURCE}", "{PHONE}"]'

    assert set(fake.values) == {
        _key("member_login_failures", identifier),
        _key("member_login_lock", identifier),
    }
    assert fake.values[_key("member_login_failures", identifier)] == 5
    assert fake.ttl(_key("member_login_failures", identifier)) == MINUTES_15
    for key in fake.values:
        assert SOURCE not in key
        assert PHONE.lstrip("+") not in key


def test_member_keys_differ_from_admin_keys() -> None:
    """守住验收「登录锁定……写法与 admin_auth 一致」且两者互不影响：会员与管理员用各自的桶，
    会员锁定不使后台登录被锁。"""
    fake = FakeRedis()
    _fail(fake, 5)

    assert admin_auth.login_locked(fake, SOURCE, PHONE) is False


@pytest.mark.parametrize(
    "phone", ["60123456789", "+60 123456789", "+", "+1234567890123456", "", None]
)
def test_phone_must_be_normalized_e164(phone: object) -> None:
    """守住验收「标识为 [访客来源, 规范化 E.164 号码]」：不是规范化 E.164 的号码是编程错误，
    抛 ValueError，消息不含号码。"""
    with pytest.raises(ValueError) as excinfo:
        login_identifier(SOURCE, phone)  # type: ignore[arg-type]

    if isinstance(phone, str) and phone:
        assert phone not in str(excinfo.value)


@pytest.mark.parametrize("where", ["get", "execute"])
def test_redis_unavailable_raises(db: Session, member: Member, where: str) -> None:
    """守住验收「Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录」（设计「失败、并发
    与重试」第 4 条）：替身抛连接错误时判定、计数与登录判定都抛不可用异常，消息不含号码
    与来源。"""
    fake = FakeRedis()
    fake.fail_on = where

    with pytest.raises(RateLimitUnavailable) as excinfo:
        if where == "get":
            password_login(db, fake, SOURCE, PHONE, PASSWORD)
        else:
            record_login_failure(fake, SOURCE, PHONE)

    assert PHONE not in str(excinfo.value)
    assert SOURCE not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 不泄露
# ---------------------------------------------------------------------------


def test_repr_does_not_contain_sensitive_values(db: Session, member: Member) -> None:
    """守住第 6 条「应用日志……不记录……完整电话……密码」与验收「令牌、CSRF 令牌、cookie 值、
    号码、来源与密码不出现在……repr 里」：签发与校验结果的 repr 不含令牌、CSRF 令牌、号码或
    密码哈希。"""
    issued = issue_member_session(db, member, NOW)
    found = check_member_session(db, issued.token, NOW)
    assert found is not None

    for text_value in (repr(issued), repr(found)):
        for secret in (issued.token, issued.csrf_token, PHONE, PASSWORD_HASH):
            assert secret not in text_value


def test_module_does_not_log(db: Session, member: Member, caplog: pytest.LogCaptureFixture) -> None:
    """守住第 6 条「应用日志……不记录……完整电话……密码」与验收「不写日志」：会话、CSRF、
    密码与锁定函数都不产生 app. 下的日志记录。"""
    fake = FakeRedis()
    with caplog.at_level(logging.DEBUG):
        issued = issue_member_session(db, member, NOW)
        found = check_member_session(db, issued.token, NOW)
        assert found is not None
        check_csrf_token(issued.token, "wrong")
        revoke_member_session(db, found.session, NOW)
        revoke_all_member_sessions(db, member.id, NOW)
        for _ in range(6):
            password_login(db, fake, SOURCE, PHONE, "wrong password")

    assert [r for r in caplog.records if r.name.startswith("app.")] == []

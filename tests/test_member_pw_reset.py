"""会员重设密码的一次性凭据、CSRF 令牌与重设规则（app/services/member_pw_reset.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：「已有密码的重设再次短信
验证」「密码至少 8 位、不强制复杂度，安全哈希存储」，第 6 条「应用日志与监控不记录……密码」；
docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（3）（下称「Kelvin 2026-10-08」）：「重设
密码与注销时撤销该会员的全部会话」；0.44 记录的 Kelvin 2026-10-10 决定（下称「Kelvin
2026-10-10」）：「服务端签发一次性重设凭据，存于共享 Redis……10 分钟有效、使用一次即删除」
「提交新密码另须 CSRF 令牌」「Redis 不可用时重设暂停」。每条测试的文档字符串写明它守住的
设计原句或 Kelvin 的哪一句；没有直接原句的，写明守住的是 SHOP-TASK-078 验收的哪一条。

数据库用 SQLite 内存库按模型建表，每个连接打开外键检查并断言已打开。Redis 不连真的：
FakeRedis 照 tests/test_member_auth.py 的写法另写，只实现用到的 SET（带 EX）与事务管道
（GET、DEL），带可推进的替身时钟、命令记录与可注入的错误。期望的键与 CSRF 令牌在这里另算，
不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Member, MemberSession
from app.services import admin_auth, member_auth, order_access, pw_hash
from app.services.member_auth import (
    authenticate_member,
    check_member_session,
    issue_member_session,
)
from app.services.member_pw_reset import (
    check_reset_csrf,
    consume_pw_reset,
    csrf_token_for_reset,
    issue_pw_reset,
    reset_password,
)
from app.services.rate_limit import RateLimitUnavailable

NOW = datetime(2026, 10, 10, 2, 0)
PHONE = "+60123456789"
OTHER_PHONE = "+6591234567"
NO_PASSWORD_PHONE = "+60198765432"
OLD_PASSWORD = "correct horse"
NEW_PASSWORD = "battery staple"
OLD_HASH = pw_hash.hash_password(OLD_PASSWORD)
MINUTES_10 = 600


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
    return _member(db, PHONE, OLD_HASH)


def _delete(db: Session, row: Member) -> None:
    """按 SHOP-TASK-012 的模型约定注销：清空号码与密码哈希、改状态、写注销时间。"""
    row.phone = None
    row.password_hash = None
    row.status = "deleted"
    row.deleted_at = NOW + timedelta(hours=1)
    db.commit()


class FakeRedis:
    """内存替身：值（按 Redis 存成字节）、按替身时钟计的过期时刻、命令记录与可注入的错误。

    fail_on 为 "set" 或 "execute" 时在该处抛 error（默认连接错误）；del_error 为真时事务里的
    DEL 得到错误，GET 照常读出值，execute 像 redis-py 默认那样抛出第一个错误；before_execute
    在事务执行前调用一次，用来插入另一个取用。
    """

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.commands: list[tuple] = []
        self.fail_on: str | None = None
        self.error: Exception = redis.exceptions.ConnectionError(
            "Error connecting to redis-host:6379"
        )
        self.del_error = False
        self.before_execute: Callable[[], None] | None = None

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
            raise self.error

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.commands.append(("set", key, value, ex))
        self._maybe_fail("set")
        self.values[key] = str(value).encode()
        if ex is None:
            self.expires_at.pop(key, None)
        else:
            self.expires_at[key] = self.now + ex
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        self.commands.append(("pipeline",))
        return FakePipeline(self)

    def _get(self, key: str) -> bytes | None:
        self._purge(key)
        return self.values.get(key)

    def _delete(self, key: str) -> int | Exception:
        if self.del_error:
            return redis.exceptions.ResponseError("WRONGTYPE Operation against a key")
        self._purge(key)
        if key not in self.values:
            return 0
        del self.values[key]
        self.expires_at.pop(key, None)
        return 1


class FakePipeline:
    """事务管道替身：命令先排队，execute 时一次执行（中间不插入其他命令，同 MULTI / EXEC）。"""

    def __init__(self, store: FakeRedis) -> None:
        self.store = store
        self.queued: list[tuple] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        self.queued = []

    def get(self, key: str) -> FakePipeline:
        self.queued.append(("get", key))
        return self

    def delete(self, key: str) -> FakePipeline:
        self.queued.append(("delete", key))
        return self

    def execute(self, raise_on_error: bool = True) -> list:
        hook, self.store.before_execute = self.store.before_execute, None
        if hook is not None:
            hook()
        self.store.commands.append(("exec", tuple(self.queued)))
        self.store._maybe_fail("execute")
        batch, self.queued = self.queued, []
        results = [
            self.store._get(c[1]) if c[0] == "get" else self.store._delete(c[1]) for c in batch
        ]
        if raise_on_error:
            for result in results:
                if isinstance(result, Exception):
                    raise result
        return results


def _sha256(text_value: str) -> str:
    return hashlib.sha256(text_value.encode()).hexdigest()


def _key(token: str) -> str:
    return "acuven_shop:member_pw_reset:" + _sha256(token)


# ---------------------------------------------------------------------------
# 重设凭据
# ---------------------------------------------------------------------------


def test_issue_stores_only_digest_key_with_member_id_and_600s_in_one_set() -> None:
    """守住 Kelvin 2026-10-10「服务端签发一次性重设凭据，存于共享 Redis……10 分钟有效」与验收
    「凭据原文不进 Redis」「以一条带 600 秒过期的 SET 写入」：Redis 里只有另算的摘要键，值为
    会员 ID，过期 600 秒；值与过期由同一条 SET 写入，没有别的命令。"""
    fake = FakeRedis()

    issued = issue_pw_reset(fake, 42)

    assert len(issued.token) == 43
    key = _key(issued.token)
    assert set(fake.values) == {key}
    assert fake.values[key] == b"42"
    assert fake.ttl(key) == MINUTES_10
    assert fake.commands == [("set", key, "42", MINUTES_10)]
    assert issued.token not in key


def test_tokens_are_unique() -> None:
    """守住 Kelvin 2026-10-10「一次性重设凭据」：每次签发的凭据都不同（secrets 生成）。"""
    fake = FakeRedis()

    tokens = {issue_pw_reset(fake, 42).token for _ in range(20)}

    assert len(tokens) == 20
    assert len(fake.values) == 20


@pytest.mark.parametrize(
    "member_id", [0, -1, True, "42", None], ids=["zero", "negative", "bool", "str", "none"]
)
def test_issue_requires_positive_member_id(member_id: object) -> None:
    """守住验收「值为会员 ID」：会员 ID 不是正整数是编程错误，抛 ValueError、不访问 Redis。"""
    fake = FakeRedis()

    with pytest.raises(ValueError):
        issue_pw_reset(fake, member_id)  # type: ignore[arg-type]

    assert fake.commands == []


def test_consume_once_then_none() -> None:
    """守住 Kelvin 2026-10-10「使用一次即删除」：第一次取用得到会员 ID 并删除键，再取用为
    None；取用在一个事务里 GET 与 DEL。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 42)
    key = _key(issued.token)

    assert consume_pw_reset(fake, issued.token) == 42
    assert fake.values == {}
    assert consume_pw_reset(fake, issued.token) is None
    assert ("exec", (("get", key), ("delete", key))) in fake.commands


def test_consume_after_expiry_is_none() -> None:
    """守住 Kelvin 2026-10-10「10 分钟有效」：差 1 秒到 10 分钟仍可取用，满 10 分钟起为 None。"""
    fake = FakeRedis()
    early = issue_pw_reset(fake, 42)
    late = issue_pw_reset(fake, 43)

    fake.advance(MINUTES_10 - 1)
    assert consume_pw_reset(fake, early.token) == 42
    fake.advance(1)
    assert consume_pw_reset(fake, late.token) is None


def test_unknown_token_is_none() -> None:
    """守住验收「键不存在……返回 None」：格式合法但从未签发的凭据为 None。"""
    fake = FakeRedis()
    issue_pw_reset(fake, 42)

    assert consume_pw_reset(fake, "A" * 43) is None
    assert len(fake.values) == 1


@pytest.mark.parametrize(
    "token",
    [None, "", "short", "!" * 43, "A" * 42, "A" * 44, "A" * 42 + "=", 12345],
    ids=["none", "empty", "short", "bad-chars", "42", "44", "padding", "not-str"],
)
def test_malformed_token_does_not_touch_redis(token: object) -> None:
    """守住验收「格式不对时直接返回 None、不访问 Redis」：Redis 客户端为 None 时也不抛异常。"""
    fake = FakeRedis()

    assert consume_pw_reset(fake, token) is None  # type: ignore[arg-type]
    assert consume_pw_reset(None, token) is None  # type: ignore[arg-type]
    assert fake.commands == []


def test_interleaved_consumes_only_one_gets_member_id() -> None:
    """守住 Kelvin 2026-10-10「使用一次即删除」与验收「并发取用同一凭据时至多一次得到会员
    ID」：第一个取用已排好 GET 与 DEL、尚未执行时插入第二个取用，只有一个得到会员 ID。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 42)
    inner: list[int | None] = []
    fake.before_execute = lambda: inner.append(consume_pw_reset(fake, issued.token))

    outer = consume_pw_reset(fake, issued.token)

    assert sorted([outer, inner[0]], key=lambda v: v is None) == [42, None]
    assert fake.values == {}


# ---------------------------------------------------------------------------
# Redis 不可用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError("Error connecting to redis-host:6379"),
        redis.exceptions.TimeoutError("Timeout reading from redis-host:6379"),
        redis.exceptions.ResponseError("READONLY You can't write against a read only replica"),
    ],
    ids=["connection", "timeout", "response"],
)
def test_issue_raises_when_redis_unavailable(error: Exception) -> None:
    """守住 Kelvin 2026-10-10「Redis 不可用时重设暂停」与验收「签发……抛 RateLimitUnavailable」：
    连不上、超时与返回错误都抛不可用异常，不返回凭据，消息与异常链不含键与会员 ID。"""
    fake = FakeRedis()
    fake.fail_on = "set"
    fake.error = error

    with pytest.raises(RateLimitUnavailable) as excinfo:
        issue_pw_reset(fake, 4242)

    key = fake.commands[0][1]
    assert key not in str(excinfo.value)
    assert "4242" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_issue_raises_when_redis_not_configured() -> None:
    """守住 Kelvin 2026-10-10「Redis 不可用时重设暂停」与验收「Redis 未配置……抛
    RateLimitUnavailable」：客户端为 None 时签发与取用都抛不可用异常。"""
    with pytest.raises(RateLimitUnavailable):
        issue_pw_reset(None, 42)
    with pytest.raises(RateLimitUnavailable):
        consume_pw_reset(None, "A" * 43)


@pytest.mark.parametrize(
    "error",
    [
        redis.exceptions.ConnectionError("Error connecting to redis-host:6379"),
        redis.exceptions.TimeoutError("Timeout reading from redis-host:6379"),
        redis.exceptions.ExecAbortError("EXECABORT Transaction discarded"),
    ],
    ids=["connection", "timeout", "exec-abort"],
)
def test_consume_raises_when_redis_unavailable(error: Exception) -> None:
    """守住 Kelvin 2026-10-10「Redis 不可用时重设暂停」与验收「取用……抛 RateLimitUnavailable」：
    事务执行时连不上、超时与 EXEC 返回错误都抛不可用异常，消息不含凭据、键与会员 ID。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 4242)
    fake.fail_on = "execute"
    fake.error = error

    with pytest.raises(RateLimitUnavailable) as excinfo:
        consume_pw_reset(fake, issued.token)

    message = str(excinfo.value)
    for secret in (issued.token, _key(issued.token), "4242"):
        assert secret not in message
    assert excinfo.value.__cause__ is None


def test_consume_with_failed_delete_is_not_success() -> None:
    """守住 Kelvin 2026-10-10「使用一次即删除」与验收「事务执行时报错……不把读到的会员 ID 当作
    取用成功」：事务里 GET 读出了会员 ID 而 DEL 出错时抛不可用异常，消息不含会员 ID。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 4242)
    fake.del_error = True

    with pytest.raises(RateLimitUnavailable) as excinfo:
        consume_pw_reset(fake, issued.token)

    assert "4242" not in str(excinfo.value)
    assert fake.values[_key(issued.token)] == b"4242"


def test_consume_with_unexpected_value_raises() -> None:
    """守住验收「返回会员 ID」与「Redis……返回错误时……抛 RateLimitUnavailable」：键里的值不是
    正整数会员 ID 时不当作取用成功，抛不可用异常。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 42)
    fake.values[_key(issued.token)] = b"not-a-member"

    with pytest.raises(RateLimitUnavailable):
        consume_pw_reset(fake, issued.token)


# ---------------------------------------------------------------------------
# CSRF 令牌
# ---------------------------------------------------------------------------


def test_csrf_token_is_prefixed_digest_and_checks() -> None:
    """守住 Kelvin 2026-10-10「提交新密码另须 CSRF 令牌」与验收「CSRF 令牌不存储，由凭据原文加
    本模块独有的用途前缀做 SHA-256 得到」：等于另算的带前缀摘要，不等于凭据与 Redis 键里的
    摘要，不写入 Redis，校验通过。"""
    fake = FakeRedis()
    issued = issue_pw_reset(fake, 42)

    expected = hashlib.sha256(
        b"acuven-shop/member-pw-reset/csrf\x00" + issued.token.encode()
    ).hexdigest()
    assert issued.csrf_token == expected
    assert csrf_token_for_reset(issued.token) == expected
    assert issued.csrf_token != issued.token
    assert issued.csrf_token != _sha256(issued.token)
    assert all(issued.csrf_token not in key for key in fake.values)
    assert check_reset_csrf(issued.token, issued.csrf_token) is True


def test_csrf_differs_from_member_admin_and_order_access() -> None:
    """守住验收「前缀不同于 app/services/member_auth.py、app/services/admin_auth.py 与
    app/services/order_access.py」：同一原文在四处得到的 CSRF 令牌两两不同，另三处的不能用于
    重设。"""
    issued = issue_pw_reset(FakeRedis(), 42)
    others = [
        member_auth.csrf_token_for_cookie(issued.token),
        admin_auth.csrf_token_for_cookie(issued.token),
        order_access.csrf_token_for_cookie(issued.token),
    ]

    assert all(other is not None for other in others)
    assert len({issued.csrf_token, *others}) == 4
    for other in others:
        assert check_reset_csrf(issued.token, other) is False


@pytest.mark.parametrize(
    "header",
    [None, "", "0" * 64, "wrong", "é"],
    ids=["none", "empty", "zeros", "wrong", "non-ascii"],
)
def test_wrong_or_missing_csrf_fails(header: str | None) -> None:
    """守住 Kelvin 2026-10-10「提交新密码另须 CSRF 令牌」与验收「错、缺……都不通过」。"""
    issued = issue_pw_reset(FakeRedis(), 42)

    assert check_reset_csrf(issued.token, header) is False


def test_csrf_with_missing_or_malformed_token_fails() -> None:
    """守住验收「凭据或请求头为 None、格式不对时校验不通过」：另一凭据的 CSRF 令牌不通过；
    凭据缺失或格式不合法时也不通过，算不出 CSRF 令牌。"""
    fake = FakeRedis()
    first = issue_pw_reset(fake, 42)
    second = issue_pw_reset(fake, 42)

    assert check_reset_csrf(first.token, second.csrf_token) is False
    assert check_reset_csrf(None, first.csrf_token) is False
    assert check_reset_csrf("short", first.csrf_token) is False
    assert csrf_token_for_reset(None) is None
    assert csrf_token_for_reset("!" * 43) is None


# ---------------------------------------------------------------------------
# 重设密码
# ---------------------------------------------------------------------------


def test_reset_changes_password_and_revokes_all_sessions(db: Session, member: Member) -> None:
    """守住「密码……安全哈希存储」与 Kelvin 2026-10-08「重设密码……时撤销该会员的全部会话」：
    重设后旧密码不能、新密码能通过 authenticate_member，库里存的是哈希，该会员的全部会话不再
    通过，其他会员的会话不受影响。"""
    other = _member(db, OTHER_PHONE, OLD_HASH)
    tokens = [issue_member_session(db, member, NOW).token for _ in range(2)]
    other_token = issue_member_session(db, other, NOW).token
    db.commit()

    assert reset_password(db, member.id, NEW_PASSWORD, NOW + timedelta(minutes=1)) is True
    db.commit()

    assert authenticate_member(db, PHONE, OLD_PASSWORD) is None
    found = authenticate_member(db, PHONE, NEW_PASSWORD)
    assert found is not None and found.id == member.id
    stored = db.scalars(select(Member.password_hash).where(Member.id == member.id)).one()
    assert stored is not None and NEW_PASSWORD not in stored
    for token in tokens:
        assert check_member_session(db, token, NOW + timedelta(minutes=2)) is None
    assert check_member_session(db, other_token, NOW + timedelta(minutes=2)) is not None
    assert authenticate_member(db, OTHER_PHONE, OLD_PASSWORD) is not None
    revoked = db.scalars(
        select(MemberSession.revoked_at).where(MemberSession.member_id == member.id)
    ).all()
    assert revoked == [NOW + timedelta(minutes=1)] * 2


def test_member_without_password_can_reset(db: Session) -> None:
    """守住验收「未设过密码的会员同样可设置（等同首次设密码，UX P12）」：没有密码哈希的会员
    重设后可用新密码通过。"""
    row = _member(db, NO_PASSWORD_PHONE, None)

    assert reset_password(db, row.id, NEW_PASSWORD, NOW) is True
    db.commit()

    found = authenticate_member(db, NO_PASSWORD_PHONE, NEW_PASSWORD)
    assert found is not None and found.id == row.id


def test_deleted_member_returns_false_and_revokes_nothing(db: Session, member: Member) -> None:
    """守住验收「以条件更新（仅当该会员仍为 active）写入，更新不到返回 False」（设计「资料保留」
    注销后不再可访问）：已注销会员返回 False，密码哈希仍为空，任何会话都不被撤销。"""
    other = _member(db, OTHER_PHONE, OLD_HASH)
    issue_member_session(db, member, NOW)
    issue_member_session(db, other, NOW)
    db.commit()
    _delete(db, member)

    assert reset_password(db, member.id, NEW_PASSWORD, NOW + timedelta(hours=2)) is False
    db.commit()

    assert db.scalars(select(Member.password_hash).where(Member.id == member.id)).one() is None
    assert db.scalars(select(MemberSession.revoked_at)).all() == [None, None]


def test_unknown_member_returns_false(db: Session, member: Member) -> None:
    """守住验收「更新不到返回 False」：不存在的会员 ID 返回 False，现有会员不变。"""
    assert reset_password(db, member.id + 100, NEW_PASSWORD, NOW) is False
    assert authenticate_member(db, PHONE, OLD_PASSWORD) is not None


@pytest.mark.parametrize(
    "password", ["", "1234567", "short", None], ids=["empty", "7", "short", "not-str"]
)
def test_short_password_raises_without_writing(
    db: Session, member: Member, password: object
) -> None:
    """守住「密码至少 8 位」：不足 8 个字符（或不是字符串）抛 ValueError，消息不含密码，
    不改密码、不撤销会话。"""
    token = issue_member_session(db, member, NOW).token
    db.commit()

    with pytest.raises(ValueError) as excinfo:
        reset_password(db, member.id, password, NOW)  # type: ignore[arg-type]
    db.commit()

    if isinstance(password, str) and password:
        assert password not in str(excinfo.value)
    assert authenticate_member(db, PHONE, OLD_PASSWORD) is not None
    assert check_member_session(db, token, NOW) is not None


def test_eight_characters_non_ascii_is_accepted(db: Session, member: Member) -> None:
    """守住「密码至少 8 位、不强制复杂度」：8 个非 ASCII 字符即可重设。"""
    assert reset_password(db, member.id, "pässwörd", NOW) is True
    db.commit()

    assert authenticate_member(db, PHONE, "pässwörd") is not None


def test_reset_only_flushes(db: Session, member: Member) -> None:
    """守住验收「只 flush、不提交」：调用方回滚时密码与会话撤销都不生效。"""
    token = issue_member_session(db, member, NOW).token
    db.commit()

    assert reset_password(db, member.id, NEW_PASSWORD, NOW) is True
    db.rollback()

    assert authenticate_member(db, PHONE, OLD_PASSWORD) is not None
    assert authenticate_member(db, PHONE, NEW_PASSWORD) is None
    assert check_member_session(db, token, NOW) is not None


def test_now_must_be_naive_utc(db: Session, member: Member) -> None:
    """守住模块约定「时间一律是不带时区的 UTC」（沿用 SHOP-TASK-071）：带时区的时间被拒，
    不改密码。"""
    with pytest.raises(ValueError):
        reset_password(db, member.id, NEW_PASSWORD, NOW.replace(tzinfo=UTC))

    assert authenticate_member(db, PHONE, OLD_PASSWORD) is not None


# ---------------------------------------------------------------------------
# 不泄露
# ---------------------------------------------------------------------------


def test_repr_does_not_contain_token_csrf_or_key() -> None:
    """守住验收「结果的凭据字段 repr=False」与「异常与 repr 不含凭据……与键」：签发结果的 repr
    不含凭据、CSRF 令牌与键。"""
    issued = issue_pw_reset(FakeRedis(), 42)

    text_value = repr(issued)
    for secret in (issued.token, issued.csrf_token, _key(issued.token)):
        assert secret not in text_value


def test_module_does_not_log(db: Session, member: Member, caplog: pytest.LogCaptureFixture) -> None:
    """守住第 6 条「应用日志……不记录……密码」与验收「不写日志」：签发、CSRF、取用与重设都
    不产生 app. 下的日志记录。"""
    fake = FakeRedis()
    with caplog.at_level(logging.DEBUG):
        issued = issue_pw_reset(fake, member.id)
        check_reset_csrf(issued.token, "wrong")
        member_id = consume_pw_reset(fake, issued.token)
        assert member_id == member.id
        reset_password(db, member_id, NEW_PASSWORD, NOW)
        with pytest.raises(ValueError):
            reset_password(db, member_id, "short", NOW)

    assert [r for r in caplog.records if r.name.startswith("app.")] == []
    for record in caplog.records:
        assert NEW_PASSWORD not in record.getMessage()
        assert issued.token not in record.getMessage()

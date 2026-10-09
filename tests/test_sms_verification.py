"""短信验证的发送与核验规则（app/services/sms_verification.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 3 条（下称「原则第 3 条」）：
  “白名单内号码无法送达或短信服务停发时不声称注册成功，允许改为游客下单”；
  第 4 条（下称「原则第 4 条」）：“下单、发送短信、提交验证码与确认注销时，服务端都按
  当时读取的开关值判定：开关在流程途中被关闭后，提交的验证码不再核验”。
- 「失败、并发与重试」第 2 条（下称「第 2 条」）：“结账时白名单号码的短信无法送达或
  短信服务停发，允许改为游客下单，但验证码错误或超过尝试次数不降级为游客”；
  第 3 条（下称「第 3 条」）：“短信验证开关关闭时，所有短信发送请求在人机挑战核验、
  调用服务商与预占预算之前即被拒绝，不写验证记录，不计入限流与每日预算”；
  第 4 条（下称「第 4 条」）：“发送前采用托管人机挑战，并在后端核验令牌。按规范化
  手机号、来源、国家及全站限流……发送前按目的地预占保守的单次最高费用，未知费用时
  停发……Redis 或 MySQL 不可用时短信停发……不得把验证码、短信凭据或完整手机号写进
  日志”；第 5 条（下称「第 5 条」）：“结账验证、短信登录、注册与密码重设共用同一
  白名单、人机挑战、限流和每日预算”。
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 10-08」）：“每个号码
  60 秒 1 条、1 小时 5 条、24 小时 10 条，每个来源 1 小时 10 条”；“按国家限流取全站每日
  上限”；“核验只认 10 分钟内的验证”；“验证码错误、过期与服务不可用都不降级为游客，
  限流与访客未通过人机挑战也不降级；人机挑战服务本身不可用（连不上或出错）按停发处理、
  可改游客下单”。
- docs/HANDOFF.md 0.43 记录的 Kelvin 2026-10-09 决定（下称「Kelvin 10-09」）：请求 ID 对上
  已结束的记录或另一个号码时“原记录一律不改……按服务商不可用停发处理：写停发记录、
  释放本次预占、不计入每日条数与费用上限”；核验窗口为“当前时间减 10 分钟到当前时间，
  创建时间晚于当前时间的记录不算”。
每条测试的文档字符串写明它守住的是哪一句；没有直接原句的，写明是 SHOP-TASK-069
验收标准里的约定。

用 SQLite 内存库按模型建表（引擎设置与 tests/test_sms_budget.py 相同，由引擎发 BEGIN，
预占在保存点里插入当日行）。服务商与人机挑战用 SHOP-TASK-067 的测试替身；Redis 用照
tests/test_admin_auth.py 写的内存替身 FakeRedis，计数键在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import SiteSetting, SmsDailyUsage, VerificationAttempt
from app.models.site import SITE_SETTING_ID
from app.services import sms_verification
from app.services.captcha import CaptchaResult, FakeCaptchaVerifier
from app.services.ordering import SmsVerificationRequired, _require_guest_allowed
from app.services.phone import InvalidPhoneNumber
from app.services.sms_provider import (
    FAKE_ACCEPTED,
    CheckStatus,
    FakeSmsProvider,
    SendResult,
    SendStatus,
)
from app.services.sms_verification import (
    BUCKET_COUNTRY_DAY,
    BUCKET_PHONE_DAY,
    BUCKET_PHONE_HOUR,
    BUCKET_PHONE_MINUTE,
    BUCKET_SOURCE_HOUR,
    SendOutcome,
    SmsVerificationError,
    VerifyResult,
    VerifyStatus,
    send_verification,
    verify_code,
)

# UTC 03:00 即马来西亚 11:00，日期 2026-10-09。
NOW = datetime(2026, 10, 9, 3, 0, 0)
TODAY = date(2026, 10, 9)
FIVE_MINUTES_AGO = NOW - timedelta(minutes=5)
PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
PHONE_UK = "+447911123456"
SOURCE = "203.0.113.5"
TOKEN = "turnstile-token-value"
CODE = "482915"
COST_MY = 60_000
COST_SG = 50_000
RID_A = "VE" + "a" * 32
RID_B = "VE" + "b" * 32
MINUTE = 60
HOUR = 3600

# 替身逐次受理时的请求 ID 在整个测试进程里不重复，不同替身实例也不会撞上同一个。
_REQUEST_IDS = itertools.count(1)


# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_sms_budget.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 保存点与提交才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


@pytest.fixture
def enabled(db: Session) -> None:
    set_switch(db, True)


class FakeRedis:
    """内存替身：值、按替身时钟计的过期时刻，以及可注入的连接错误。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expires_at: dict[str, int] = {}
        self.now = 0
        self.fail_on: str | None = None

    def advance(self, seconds: int) -> None:
        self.now += seconds

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

    def count(self, key: str) -> int:
        self._purge(key)
        return self.values.get(key, 0)

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


class Provider(FakeSmsProvider):
    """SHOP-TASK-067 的替身，另加：未预设发起结果时每次受理给一个新的请求 ID；
    调用时可执行的钩子（模拟调用途中的并发改动或异常）；核验时收到的请求 ID。
    """

    def __init__(
        self,
        send_result: SendResult | None = None,
        check_result: CheckStatus = CheckStatus.APPROVED,
        on_send: Callable[[], None] | None = None,
        on_check: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(FAKE_ACCEPTED if send_result is None else send_result, check_result)
        self.sequential = send_result is None
        self.on_send = on_send
        self.on_check = on_check
        self.checked_request_ids: list[str] = []

    def start_verification(self, phone_e164: str) -> SendResult:
        result = super().start_verification(phone_e164)
        if self.on_send is not None:
            self.on_send()
        if self.sequential:
            return SendResult(SendStatus.ACCEPTED, f"VE{next(_REQUEST_IDS):032x}")
        return result

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        status = super().check_verification(request_id, code)
        self.checked_request_ids.append(request_id)
        if self.on_check is not None:
            self.on_check()
        return status


def make_settings(**overrides: int) -> Settings:
    values: dict[str, int] = {
        "sms_max_cost_micro_usd_my": COST_MY,
        "sms_max_cost_micro_usd_sg": COST_SG,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def set_switch(db: Session, value: bool) -> None:
    row = db.get(SiteSetting, SITE_SETTING_ID)
    if row is None:
        db.add(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=value))
    else:
        row.sms_verification_enabled = value
    db.commit()


def send(
    db: Session,
    *,
    settings: Settings | None = None,
    redis_client: object = "fake",
    provider: FakeSmsProvider | None = None,
    captcha: FakeCaptchaVerifier | None = None,
    phone: str = PHONE_MY,
    purpose: str = "checkout",
    source: str = SOURCE,
    now: datetime = NOW,
) -> SendOutcome:
    return send_verification(
        db,
        settings or make_settings(),
        FakeRedis() if redis_client == "fake" else redis_client,
        provider or Provider(),
        captcha or FakeCaptchaVerifier(),
        phone,
        purpose,
        TOKEN,
        source,
        now,
    )


def attempts(db: Session) -> list[tuple]:
    """全部验证记录的（号码, 用途, 状态, 请求 ID, 创建时间, 更新时间），按 ID。"""
    rows = db.execute(
        select(
            VerificationAttempt.phone,
            VerificationAttempt.purpose,
            VerificationAttempt.status,
            VerificationAttempt.provider_request_id,
            VerificationAttempt.created_at,
            VerificationAttempt.updated_at,
        ).order_by(VerificationAttempt.id)
    ).all()
    return [tuple(row) for row in rows]


def add_attempt(
    db: Session,
    status: str = "sent",
    request_id: str | None = RID_A,
    phone: str = PHONE_MY,
    purpose: str = "login",
    created_at: datetime = FIVE_MINUTES_AGO,
    updated_at: datetime | None = None,
) -> int:
    result = db.execute(
        insert(VerificationAttempt).values(
            phone=phone,
            purpose=purpose,
            status=status,
            provider_request_id=request_id,
            created_at=created_at,
            updated_at=created_at if updated_at is None else updated_at,
        )
    )
    db.commit()
    return result.inserted_primary_key[0]


def add_usage(db: Session, sent: int = 0, reserved: int = 0, settled: int = 0) -> None:
    db.execute(
        insert(SmsDailyUsage).values(
            usage_date=TODAY,
            sent_count=sent,
            reserved_micro_usd=reserved,
            settled_micro_usd=settled,
        )
    )
    db.commit()


def usage(db: Session) -> tuple[int, int, int] | None:
    """当日的（条数, 已预占, 已结算）；没有行为空。"""
    row = db.execute(
        select(
            SmsDailyUsage.sent_count,
            SmsDailyUsage.reserved_micro_usd,
            SmsDailyUsage.settled_micro_usd,
        ).where(SmsDailyUsage.usage_date == TODAY)
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def key(bucket: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return "acuven_shop:rate_limit:" + bucket + ":" + digest


def track_releases(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """记录释放预占的调用（照常执行释放）。"""
    calls: list[object] = []
    original = sms_verification.release_sms

    def _release(db: Session, reservation: object) -> None:
        calls.append(reservation)
        original(db, reservation)

    monkeypatch.setattr(sms_verification, "release_sms", _release)
    return calls


def assert_sanitized(exc: BaseException) -> None:
    """异常消息不含号码、验证码、令牌与来源，也不挂原异常（异常链里可能带着它们）。"""
    for text_value in (PHONE_MY, PHONE_MY[1:], CODE, TOKEN, SOURCE):
        assert text_value not in str(exc) and text_value not in repr(exc)
    assert exc.__cause__ is None and exc.__context__ is None


def guest_allowed(db: Session, phone: str, now: datetime) -> bool:
    """app/services/ordering.py 现有的降级判定（不改它，只调用）。"""
    try:
        _require_guest_allowed(db, phone, now)
    except SmsVerificationRequired:
        return False
    return True


# ---------------------------------------------------------------------------
# 判定顺序：开关、白名单、人机挑战
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("switch", [None, False])
def test_disabled_switch_rejects_before_captcha_provider_budget_and_rate_limit(
    db: Session, switch: bool | None
) -> None:
    """第 3 条：“开关关闭时，所有短信发送请求在人机挑战核验、调用服务商与预占预算
    之前即被拒绝，不写验证记录，不计入限流与每日预算”。没有设置行视为关闭。
    """
    if switch is not None:
        set_switch(db, switch)
    fake, provider, captcha = FakeRedis(), Provider(), FakeCaptchaVerifier()
    outcome = send(db, redis_client=fake, provider=provider, captcha=captcha)
    assert outcome is SendOutcome.SMS_DISABLED
    assert captcha.calls == 0
    assert provider.send_calls == 0
    assert fake.values == {}
    assert usage(db) is None
    assert attempts(db) == []


def test_disabled_switch_is_decided_before_purpose_check(db: Session) -> None:
    """第 3 条：开关关闭时“所有短信发送请求……即被拒绝”；原则第 4 条：开关关闭后提交的验证码
    不再核验。验收：开关是发送与核验的第一步，用途不合法也返回 sms_disabled 而不是抛异常。
    """
    set_switch(db, False)
    fake, provider, captcha = FakeRedis(), Provider(), FakeCaptchaVerifier()
    outcome = send(
        db, redis_client=fake, provider=provider, captcha=captcha, purpose="not_a_purpose"
    )
    assert outcome is SendOutcome.SMS_DISABLED
    assert captcha.calls == 0
    assert provider.send_calls == 0
    assert fake.values == {}
    assert attempts(db) == []
    result = verify_code(db, provider, PHONE_MY, "not_a_purpose", CODE, NOW)
    assert result == VerifyResult(VerifyStatus.SMS_DISABLED)
    assert provider.check_calls == 0


def test_switch_is_read_on_each_send(db: Session) -> None:
    """原则第 4 条：“发送短信……时，服务端都按当时读取的开关值判定”。"""
    set_switch(db, True)
    assert send(db) is SendOutcome.SENT
    set_switch(db, False)
    provider = Provider()
    assert send(db, provider=provider, now=NOW + timedelta(hours=2)) is SendOutcome.SMS_DISABLED
    assert provider.send_calls == 0


def test_not_whitelisted_writes_no_record(db: Session, enabled: None) -> None:
    """第 4 条之后的「初期短信国家」：“目的地白名单仅含马来西亚和新加坡”，白名单外号码
    不发短信。验收：白名单外即 not_whitelisted，不写记录（也不核验人机挑战、不计限流）。
    """
    fake, provider, captcha = FakeRedis(), Provider(), FakeCaptchaVerifier()
    outcome = send(db, phone=PHONE_UK, redis_client=fake, provider=provider, captcha=captcha)
    assert outcome is SendOutcome.NOT_WHITELISTED
    assert (captcha.calls, provider.send_calls) == (0, 0)
    assert fake.values == {}
    assert usage(db) is None
    assert attempts(db) == []


def test_captcha_failed_writes_no_record_and_no_fallback(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“访客未通过人机挑战也不降级”。验收：Turnstile 为 failed 即
    captcha_failed，不写记录；不计限流、不预占、不调用服务商。
    """
    fake, provider = FakeRedis(), Provider()
    captcha = FakeCaptchaVerifier(CaptchaResult.FAILED)
    outcome = send(db, redis_client=fake, provider=provider, captcha=captcha)
    assert outcome is SendOutcome.CAPTCHA_FAILED
    assert captcha.calls == 1
    assert provider.send_calls == 0
    assert fake.values == {}
    assert usage(db) is None
    assert attempts(db) == []
    assert not guest_allowed(db, PHONE_MY, NOW)


def test_captcha_service_unavailable_suspends(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“人机挑战服务本身不可用（连不上或出错）按停发处理、可改游客下单”。
    写不带请求 ID 的 suspended 记录；不计限流、不预占、不调用服务商。
    """
    fake, provider = FakeRedis(), Provider()
    captcha = FakeCaptchaVerifier(CaptchaResult.UNAVAILABLE)
    outcome = send(db, redis_client=fake, provider=provider, captcha=captcha)
    assert outcome is SendOutcome.SUSPENDED
    assert provider.send_calls == 0
    assert fake.values == {}
    assert usage(db) is None
    assert attempts(db) == [(PHONE_MY, "checkout", "suspended", None, NOW, NOW)]
    assert guest_allowed(db, PHONE_MY, NOW)


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------


def test_phone_limit_per_minute(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“每个号码 60 秒 1 条”。第 1 条放行，60 秒内第 2 条 rate_limited
    且不写记录、不调用服务商、不预占；60 秒后再放行。
    """
    fake = FakeRedis()
    assert send(db, redis_client=fake) is SendOutcome.SENT
    provider = Provider()
    assert send(db, redis_client=fake, provider=provider) is SendOutcome.RATE_LIMITED
    assert provider.send_calls == 0
    assert len(attempts(db)) == 1
    assert usage(db) == (1, 0, COST_MY)
    fake.advance(MINUTE)
    assert send(db, redis_client=fake, now=NOW + timedelta(minutes=1)) is SendOutcome.SENT


def test_phone_limit_per_hour(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“每个号码……1 小时 5 条”。间隔 60 秒的第 5 条放行、第 6 条拒绝。"""
    fake = FakeRedis()
    for i in range(5):
        now = NOW + timedelta(minutes=i)
        assert send(db, redis_client=fake, now=now) is SendOutcome.SENT
        fake.advance(MINUTE)
    assert send(db, redis_client=fake, now=NOW + timedelta(minutes=5)) is SendOutcome.RATE_LIMITED
    assert len(attempts(db)) == 5


def test_phone_limit_per_day(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“每个号码……24 小时 10 条”。间隔 1 小时的第 10 条放行、第 11 条拒绝。"""
    fake = FakeRedis()
    for i in range(10):
        now = NOW + timedelta(hours=i)
        assert send(db, redis_client=fake, now=now) is SendOutcome.SENT
        fake.advance(HOUR)
    assert send(db, redis_client=fake, now=NOW + timedelta(hours=10)) is SendOutcome.RATE_LIMITED
    assert len(attempts(db)) == 10


def test_source_limit_per_hour(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“每个来源 1 小时 10 条”。同一来源 10 个不同号码放行，第 11 个拒绝。"""
    fake = FakeRedis()
    phones = [f"+658123456{d}" for d in range(10)]
    for phone in phones:
        assert send(db, redis_client=fake, phone=phone) is SendOutcome.SENT
    assert send(db, redis_client=fake, phone=PHONE_MY) is SendOutcome.RATE_LIMITED
    assert len(attempts(db)) == 10
    # 另一个来源不受影响（换一个号码：被拒的那次已计入 PHONE_MY 的号码桶，不回退）。
    other = send(db, redis_client=fake, phone="+60123456788", source="198.51.100.20")
    assert other is SendOutcome.SENT


def test_country_limit_uses_daily_count_limit(db: Session, enabled: None) -> None:
    """Kelvin 10-08 同意“按国家限流取全站每日上限（每个国家呼叫码 24 小时的上限直接用
    全站每日条数的配置……不另设配置项）”。上限配成 3：同国 3 个号码放行，第 4 个
    rate_limited（在预算之前，不预占）。
    """
    settings = make_settings(sms_daily_count_limit=3)
    fake = FakeRedis()
    phones = ["+60123456789", "+60123456788", "+60123456787", "+60123456786"]
    sources = ["203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.4"]
    for phone, source in zip(phones[:3], sources[:3], strict=True):
        outcome = send(db, settings=settings, redis_client=fake, phone=phone, source=source)
        assert outcome is SendOutcome.SENT
    outcome = send(db, settings=settings, redis_client=fake, phone=phones[3], source=sources[3])
    assert outcome is SendOutcome.RATE_LIMITED
    assert usage(db) == (3, 0, 3 * COST_MY)
    assert fake.count(key(BUCKET_COUNTRY_DAY, "60")) == 4


def test_buckets_after_first_exceeded_are_not_counted(db: Session, enabled: None) -> None:
    """验收：按号码 60 秒、号码 1 小时、号码 24 小时、来源 1 小时、国家呼叫码 24 小时的
    顺序逐桶计数，第一个超限的桶即 rate_limited，其后的桶不再计数，已计入的桶不回退。
    """
    fake = FakeRedis()
    for i in range(5):
        send(db, redis_client=fake, now=NOW + timedelta(minutes=i))
        fake.advance(MINUTE)
    # 第 6 条：号码 60 秒桶计入并放行，号码 1 小时桶超限，其后各桶不计数。
    assert send(db, redis_client=fake, now=NOW + timedelta(minutes=5)) is SendOutcome.RATE_LIMITED
    assert fake.count(key(BUCKET_PHONE_MINUTE, PHONE_MY)) == 1
    assert fake.count(key(BUCKET_PHONE_HOUR, PHONE_MY)) == 6
    assert fake.count(key(BUCKET_PHONE_DAY, PHONE_MY)) == 5
    assert fake.count(key(BUCKET_SOURCE_HOUR, SOURCE)) == 5
    assert fake.count(key(BUCKET_COUNTRY_DAY, "60")) == 5

    # 60 秒内再发：号码 60 秒桶即超限，号码 1 小时桶也不再计数。
    assert send(db, redis_client=fake, now=NOW + timedelta(minutes=5)) is SendOutcome.RATE_LIMITED
    assert fake.count(key(BUCKET_PHONE_MINUTE, PHONE_MY)) == 2
    assert fake.count(key(BUCKET_PHONE_HOUR, PHONE_MY)) == 6


def test_bucket_names_are_distinct_and_keys_hold_only_digests(db: Session, enabled: None) -> None:
    """第 4 条“不得把……完整手机号写进日志”；验收：计数桶名各不相同，号码与来源只以
    rate_limit_key 的摘要进 Redis。
    """
    buckets = {
        BUCKET_PHONE_MINUTE,
        BUCKET_PHONE_HOUR,
        BUCKET_PHONE_DAY,
        BUCKET_SOURCE_HOUR,
        BUCKET_COUNTRY_DAY,
    }
    assert len(buckets) == 5
    fake = FakeRedis()
    assert send(db, redis_client=fake) is SendOutcome.SENT
    assert set(fake.values) == {
        key(BUCKET_PHONE_MINUTE, PHONE_MY),
        key(BUCKET_PHONE_HOUR, PHONE_MY),
        key(BUCKET_PHONE_DAY, PHONE_MY),
        key(BUCKET_SOURCE_HOUR, SOURCE),
        key(BUCKET_COUNTRY_DAY, "60"),
    }
    for stored in fake.values:
        assert PHONE_MY[1:] not in stored
        assert SOURCE not in stored


def test_rate_limit_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kelvin 10-08：标准档阈值，“各项都做成配置项”。默认值即决定的数，取自 SHOP_ 变量。"""
    settings = Settings(_env_file=None)
    assert settings.sms_phone_limit_per_minute == 1
    assert settings.sms_phone_limit_per_hour == 5
    assert settings.sms_phone_limit_per_day == 10
    assert settings.sms_source_limit_per_hour == 10
    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_HOUR", "6")
    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_DAY", "11")
    monkeypatch.setenv("SHOP_SMS_SOURCE_LIMIT_PER_HOUR", "12")
    settings = Settings(_env_file=None)
    assert (
        settings.sms_phone_limit_per_minute,
        settings.sms_phone_limit_per_hour,
        settings.sms_phone_limit_per_day,
        settings.sms_source_limit_per_hour,
    ) == (2, 6, 11, 12)


def test_configured_threshold_is_used(db: Session, enabled: None) -> None:
    """Kelvin 10-08：“各项都做成配置项”。号码 60 秒上限配成 2 时第 2 条放行、第 3 条拒绝。"""
    settings = make_settings(sms_phone_limit_per_minute=2)
    fake = FakeRedis()
    assert send(db, settings=settings, redis_client=fake) is SendOutcome.SENT
    assert send(db, settings=settings, redis_client=fake) is SendOutcome.SENT
    assert send(db, settings=settings, redis_client=fake) is SendOutcome.RATE_LIMITED


# ---------------------------------------------------------------------------
# 停发：Redis、预算、服务商
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("redis_state", ["none", "down"])
def test_redis_unavailable_suspends_without_reservation(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch, redis_state: str
) -> None:
    """第 4 条：“Redis 或 MySQL 不可用时短信停发”。客户端为 None 或抛 RateLimitUnavailable
    时写 suspended；未取得预占，不调用释放，预算不变，不调用服务商。
    """
    add_usage(db, sent=5, settled=5 * COST_MY)
    releases = track_releases(monkeypatch)
    client: FakeRedis | None = None
    if redis_state == "down":
        client = FakeRedis()
        client.fail_on = "execute"
    provider = Provider()
    outcome = send(db, redis_client=client, provider=provider)
    assert outcome is SendOutcome.SUSPENDED
    assert provider.send_calls == 0
    assert releases == []
    assert usage(db) == (5, 0, 5 * COST_MY)
    assert attempts(db) == [(PHONE_MY, "checkout", "suspended", None, NOW, NOW)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"sms_daily_cost_limit_micro_usd": COST_MY - 1},  # over_budget
        {"sms_max_cost_micro_usd_my": 0},  # unknown_cost
    ],
)
def test_budget_refusal_suspends_without_release(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, int]
) -> None:
    """第 4 条：“发送前按目的地预占保守的单次最高费用，未知费用时停发”；Kelvin 10-08
    的全站费用上限。预占被拒即写 suspended，不调用服务商，未取得预占、不调用释放。
    """
    releases = track_releases(monkeypatch)
    provider = Provider()
    outcome = send(db, settings=make_settings(**overrides), provider=provider)
    assert outcome is SendOutcome.SUSPENDED
    assert provider.send_calls == 0
    assert releases == []
    assert usage(db) == (0, 0, 0)
    assert attempts(db) == [(PHONE_MY, "checkout", "suspended", None, NOW, NOW)]


def test_provider_unavailable_suspends_and_releases(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第 2 条“短信服务停发，允许改为游客下单”；验收：服务商 unavailable 写 suspended
    并释放预占（不计入每日上限）。
    """
    releases = track_releases(monkeypatch)
    provider = Provider(SendResult(SendStatus.UNAVAILABLE))
    assert send(db, provider=provider) is SendOutcome.SUSPENDED
    assert provider.send_calls == 1
    assert len(releases) == 1
    assert usage(db) == (0, 0, 0)
    assert attempts(db) == [(PHONE_MY, "checkout", "suspended", None, NOW, NOW)]


# ---------------------------------------------------------------------------
# 三种发起结果
# ---------------------------------------------------------------------------


def test_accepted_writes_sent_record_and_settles(db: Session, enabled: None) -> None:
    """第 4 条“系统只存验证请求与结果……结算后更新记录”；Kelvin 10-08“按预占价结算”。
    受理写 sent 记录（带请求 ID）并结算，返回 sent。
    """
    provider = Provider(SendResult(SendStatus.ACCEPTED, RID_A))
    assert send(db, provider=provider, purpose="login") is SendOutcome.SENT
    assert attempts(db) == [(PHONE_MY, "login", "sent", RID_A, NOW, NOW)]
    assert usage(db) == (1, 0, COST_MY)


def test_undeliverable_without_request_id_releases(db: Session, enabled: None) -> None:
    """原则第 3 条“白名单内号码无法送达……允许改为游客下单”。没有请求 ID 的
    undeliverable 写记录、释放预占，返回 undeliverable。
    """
    provider = Provider(SendResult(SendStatus.UNDELIVERABLE))
    assert send(db, provider=provider) is SendOutcome.UNDELIVERABLE
    assert attempts(db) == [(PHONE_MY, "checkout", "undeliverable", None, NOW, NOW)]
    assert usage(db) == (0, 0, 0)


def test_undeliverable_with_request_id_settles(db: Session, enabled: None) -> None:
    """验收：undeliverable 有请求 ID 时带上并结算。"""
    provider = Provider(SendResult(SendStatus.UNDELIVERABLE, RID_A))
    assert send(db, provider=provider) is SendOutcome.UNDELIVERABLE
    assert attempts(db) == [(PHONE_MY, "checkout", "undeliverable", RID_A, NOW, NOW)]
    assert usage(db) == (1, 0, COST_MY)


def test_reservation_is_committed_before_provider_call(db: Session, enabled: None) -> None:
    """第 4 条“防 Redis 重置后超过日上限”；验收：预占成功即先提交，之后的步骤抛出异常时
    每日上限仍然计入。服务商被调用时会话已不在事务里；服务商抛异常时交给调用方（换成
    不含号码、不挂原异常的 SmsVerificationError），回滚后预占仍为 reserved，没有记录。
    """
    seen: list[bool] = []

    def _raise() -> None:
        seen.append(db.in_transaction())
        raise RuntimeError(f"provider exploded for {PHONE_MY}")

    with pytest.raises(SmsVerificationError) as excinfo:
        send(db, provider=Provider(on_send=_raise))
    assert_sanitized(excinfo.value)
    assert seen == [False]
    db.rollback()
    assert usage(db) == (1, COST_MY, 0)
    assert attempts(db) == []


def test_second_transaction_failure_keeps_reservation(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：第二个事务提交失败时异常交给调用方，已提交的预占保留为 reserved（保守计入
    每日上限），不重试；这次没有记录。数据库异常的消息带着 SQL 参数里的完整号码，交给
    调用方的是消息固定、不挂原异常的 SmsVerificationError。
    """
    original = db.commit
    commits: list[int] = []

    def _commit() -> None:
        commits.append(1)
        if len(commits) == 2:
            raise OperationalError("COMMIT", {"phone": PHONE_MY}, Exception("lost connection"))
        original()

    monkeypatch.setattr(db, "commit", _commit)
    with pytest.raises(SmsVerificationError) as excinfo:
        send(db)
    assert_sanitized(excinfo.value)
    assert len(commits) == 2
    db.rollback()
    assert usage(db) == (1, COST_MY, 0)
    assert attempts(db) == []


def test_request_id_unique_conflict_on_insert_is_sanitized(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：两次并发发送都没读到同一请求 ID 的记录、插入时撞上请求 ID 唯一约束时不重读、
    不重试，异常交给调用方，已提交的预占保留为 reserved，这次没有记录；手机号不出现在
    异常消息里。另一次发送在本次读过之后、插入之前写入同一请求 ID（以会话的
    before_flush 事件在插入前直接写库模拟）；SQLAlchemy 的 IntegrityError 消息带着 INSERT
    参数中的完整号码，交给调用方的是不含号码、不挂原异常的 SmsVerificationError。
    """
    done: list[int] = []

    @event.listens_for(db, "before_flush")
    def _concurrent_insert(session, _flush_context, _instances) -> None:
        pending = [o for o in session.new if isinstance(o, VerificationAttempt)]
        if not done and any(o.provider_request_id == RID_A for o in pending):
            done.append(1)
            session.connection().execute(
                insert(VerificationAttempt).values(
                    phone=PHONE_SG,
                    purpose="login",
                    status="sent",
                    provider_request_id=RID_A,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

    calls: list[str] = []
    original = sms_verification._send

    def _spy(*args: object) -> SendOutcome:
        try:
            return original(*args)
        except IntegrityError as exc:
            # 原异常确实带着号码，这条测试才有意义。
            calls.append(str(exc))
            raise

    monkeypatch.setattr(sms_verification, "_send", _spy)
    with pytest.raises(SmsVerificationError) as excinfo:
        send(db, provider=Provider(SendResult(SendStatus.ACCEPTED, RID_A)))
    assert done == [1]
    assert len(calls) == 1 and PHONE_MY in calls[0]
    assert_sanitized(excinfo.value)
    db.rollback()
    assert usage(db) == (1, COST_MY, 0)
    assert attempts(db) == []


# ---------------------------------------------------------------------------
# 沿用同一请求 ID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "status", "outcome"),
    [
        (SendResult(SendStatus.ACCEPTED, RID_A), "sent", SendOutcome.SENT),
        (SendResult(SendStatus.UNDELIVERABLE, RID_A), "undeliverable", SendOutcome.UNDELIVERABLE),
    ],
)
def test_reused_request_id_rewrites_pending_record(
    db: Session, enabled: None, result: SendResult, status: str, outcome: SendOutcome
) -> None:
    """验收：Twilio Verify 对同一号码仍待核验的验证再次发起时沿用原请求 ID，不新增记录，
    把仍为 sent 的那条改为本次结果的状态、本次的用途与当前时间（同一请求 ID 以最近一次
    发送的用途为准），照样结算本次预占。
    """
    fake = FakeRedis()
    first = Provider(SendResult(SendStatus.ACCEPTED, RID_A))
    assert send(db, redis_client=fake, provider=first, purpose="login") is SendOutcome.SENT
    fake.advance(MINUTE)
    later = NOW + timedelta(minutes=1)
    again = send(db, redis_client=fake, provider=Provider(result), purpose="checkout", now=later)
    assert again is outcome
    assert attempts(db) == [(PHONE_MY, "checkout", status, RID_A, NOW, later)]
    assert usage(db) == (2, 0, 2 * COST_MY)


@pytest.mark.parametrize(
    ("status", "phone"),
    [
        ("approved", PHONE_MY),
        ("rejected", PHONE_MY),
        ("undeliverable", PHONE_MY),
        ("sent", PHONE_SG),
    ],
)
def test_reused_request_id_of_ended_or_other_phone_record_suspends(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch, status: str, phone: str
) -> None:
    """Kelvin 10-09：请求 ID 对上已结束的记录（已通过、已作废或无法送达）或属于另一个号码时
    “原记录一律不改……按服务商不可用停发处理：写停发记录、释放本次预占、不计入每日条数
    与费用上限”。原记录的状态、用途与更新时间都不变。
    """
    created = FIVE_MINUTES_AGO
    add_attempt(db, status=status, phone=phone, purpose="register", created_at=created)
    releases = track_releases(monkeypatch)
    provider = Provider(SendResult(SendStatus.ACCEPTED, RID_A))
    assert send(db, provider=provider) is SendOutcome.SUSPENDED
    assert len(releases) == 1
    assert attempts(db) == [
        (phone, "register", status, RID_A, created, created),
        (PHONE_MY, "checkout", "suspended", None, NOW, NOW),
    ]
    assert usage(db) == (0, 0, 0)


def test_reused_request_id_ended_before_conditional_update_suspends(
    db: Session, enabled: None
) -> None:
    """Kelvin 10-09“已结束的不能改回待核验”；验收：条件更新（仅当仍为 sent 才改写）时
    已被改掉的也按停发处理。读到仍为 sent 之后、条件更新之前，那条记录被另一次核验
    改为 approved（以会话的 do_orm_execute 事件在条件更新执行前直接改库模拟）；
    原记录不被改回 sent，写 suspended 并释放预占。
    """
    attempt_id = add_attempt(db)
    done: list[int] = []

    @event.listens_for(db, "do_orm_execute")
    def _approve_first(state) -> None:
        if state.is_update and not done:
            done.append(1)
            state.session.connection().execute(
                text("UPDATE verification_attempts SET status = 'approved' WHERE id = :id"),
                {"id": attempt_id},
            )

    provider = Provider(SendResult(SendStatus.ACCEPTED, RID_A))
    assert send(db, provider=provider) is SendOutcome.SUSPENDED
    assert done == [1]
    assert attempts(db) == [
        (PHONE_MY, "login", "approved", RID_A, FIVE_MINUTES_AGO, FIVE_MINUTES_AGO),
        (PHONE_MY, "checkout", "suspended", None, NOW, NOW),
    ]
    assert usage(db) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 与 ordering 的降级判定衔接
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [SendResult(SendStatus.UNDELIVERABLE), SendResult(SendStatus.UNAVAILABLE)],
)
def test_undeliverable_and_suspended_checkout_allow_guest_order(
    db: Session, enabled: None, result: SendResult
) -> None:
    """原则第 3 条与第 2 条：“结账时白名单号码的短信无法送达或短信服务停发，允许改为游客
    下单”。checkout 用途的 undeliverable 与 suspended 记录使 ordering 放行该马新号码。
    """
    assert not guest_allowed(db, PHONE_MY, NOW)
    send(db, provider=Provider(result))
    assert guest_allowed(db, PHONE_MY, NOW + timedelta(minutes=29))


def test_rate_limited_and_captcha_failed_do_not_allow_guest_order(
    db: Session, enabled: None
) -> None:
    """Kelvin 10-08：“限流与访客未通过人机挑战也不降级”。两者都不写记录，ordering 仍拒绝。"""
    fake = FakeRedis()
    assert send(db, redis_client=fake) is SendOutcome.SENT
    assert send(db, redis_client=fake) is SendOutcome.RATE_LIMITED
    captcha = FakeCaptchaVerifier(CaptchaResult.FAILED)
    assert send(db, redis_client=fake, captcha=captcha) is SendOutcome.CAPTCHA_FAILED
    assert not guest_allowed(db, PHONE_MY, NOW)


def test_other_purpose_suspension_does_not_allow_guest_order(db: Session, enabled: None) -> None:
    """验收：记录的用途为调用方给出的用途；ordering 只认用途 checkout 的降级记录。"""
    send(db, provider=Provider(SendResult(SendStatus.UNAVAILABLE)), purpose="login")
    assert attempts(db)[0][1] == "login"
    assert not guest_allowed(db, PHONE_MY, NOW)


# ---------------------------------------------------------------------------
# 核验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check", "expected", "status_after"),
    [
        (CheckStatus.APPROVED, VerifyStatus.APPROVED, "approved"),
        (CheckStatus.WRONG_CODE, VerifyStatus.WRONG_CODE, "sent"),
        (CheckStatus.EXPIRED, VerifyStatus.EXPIRED, "rejected"),
        (CheckStatus.UNAVAILABLE, VerifyStatus.UNAVAILABLE, "sent"),
    ],
)
def test_verify_results(
    db: Session, enabled: None, check: CheckStatus, expected: VerifyStatus, status_after: str
) -> None:
    """第 4 条“由提供方负责验证码生命周期”；第 2 条“验证码错误或超过尝试次数不降级为游客”。
    通过改为 approved 并返回记录 ID；过期改为 rejected；验证码错误与服务不可用不改记录。
    """
    attempt_id = add_attempt(db)
    provider = Provider(check_result=check)
    result = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert result.status is expected
    assert result.attempt_id == (attempt_id if expected is VerifyStatus.APPROVED else None)
    assert provider.checked_request_ids == [RID_A]
    row = attempts(db)[0]
    assert row[2] == status_after
    assert row[5] == (FIVE_MINUTES_AGO if status_after == "sent" else NOW)


def test_verify_only_flushes(db: Session, enabled: None) -> None:
    """验收：核验只 flush、不提交，由调用方与之后的步骤一起提交；回滚后记录仍为 sent。"""
    add_attempt(db)
    result = verify_code(db, Provider(), PHONE_MY, "login", CODE, NOW)
    assert result.status is VerifyStatus.APPROVED
    db.rollback()
    assert attempts(db)[0][2] == "sent"


@pytest.mark.parametrize("code", ["", "123", "12345678901", "12a456", " 123456", "１２３４５６"])
def test_verify_malformed_code_is_wrong_code_without_provider(
    db: Session, enabled: None, code: str
) -> None:
    """验收：验证码不是 4 到 10 位数字即 wrong_code，不调用服务商（Twilio Verify 可设的长度）。"""
    add_attempt(db)
    provider = Provider()
    result = verify_code(db, provider, PHONE_MY, "login", code, NOW)
    assert result.status is VerifyStatus.WRONG_CODE
    assert provider.check_calls == 0
    assert attempts(db)[0][2] == "sent"


def test_verify_disabled_after_send_does_not_call_provider(db: Session, enabled: None) -> None:
    """原则第 4 条：“开关在流程途中被关闭后，提交的验证码不再核验”，按当次从数据库读取的值。"""
    assert send(db) is SendOutcome.SENT
    set_switch(db, False)
    provider = Provider()
    result = verify_code(db, provider, PHONE_MY, "checkout", CODE, NOW + timedelta(minutes=1))
    assert result == VerifyResult(VerifyStatus.SMS_DISABLED)
    assert provider.check_calls == 0
    assert attempts(db)[0][2] == "sent"


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (NOW - timedelta(minutes=10), VerifyStatus.APPROVED),
        (NOW, VerifyStatus.APPROVED),
        (NOW - timedelta(minutes=10, seconds=1), VerifyStatus.NO_PENDING),
        (NOW + timedelta(seconds=1), VerifyStatus.NO_PENDING),
    ],
)
def test_verify_ten_minute_window(
    db: Session, enabled: None, created_at: datetime, expected: VerifyStatus
) -> None:
    """Kelvin 10-08“核验只认 10 分钟内的验证”；Kelvin 10-09 窗口为“当前时间减 10 分钟到
    当前时间，创建时间晚于当前时间的记录不算”。两端都含；窗口外不调用服务商。
    """
    add_attempt(db, created_at=created_at)
    provider = Provider()
    assert verify_code(db, provider, PHONE_MY, "login", CODE, NOW).status is expected
    assert provider.check_calls == (1 if expected is VerifyStatus.APPROVED else 0)


def test_verify_window_counts_from_creation_not_update(db: Session, enabled: None) -> None:
    """验收：按创建时间而不是更新时间（验证码有效期从首次发起算，沿用请求 ID 的重发不延长）。"""
    add_attempt(
        db,
        created_at=NOW - timedelta(minutes=11),
        updated_at=NOW - timedelta(minutes=1),
    )
    provider = Provider()
    result = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert result.status is VerifyStatus.NO_PENDING
    assert provider.check_calls == 0


def test_verify_checks_latest_pending_of_phone_and_purpose(db: Session, enabled: None) -> None:
    """验收：找该号码与用途在窗口内状态为 sent 的最新记录；别的用途、号码与已结束的不算。"""
    add_attempt(db, request_id=RID_A, created_at=NOW - timedelta(minutes=8))
    add_attempt(db, request_id=RID_B, created_at=NOW - timedelta(minutes=2))
    add_attempt(db, request_id="VE" + "c" * 32, purpose="register", created_at=NOW)
    add_attempt(db, request_id="VE" + "d" * 32, phone=PHONE_SG, created_at=NOW)
    add_attempt(db, request_id="VE" + "e" * 32, status="rejected", created_at=NOW)
    provider = Provider(check_result=CheckStatus.WRONG_CODE)
    verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert provider.checked_request_ids == [RID_B]


def test_verify_without_pending_and_other_purpose(db: Session, enabled: None) -> None:
    """验收：没有该号码与用途的待核验记录即 no_pending，不调用服务商。"""
    provider = Provider()
    result = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert result == VerifyResult(VerifyStatus.NO_PENDING)
    add_attempt(db, purpose="register")
    result = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert result == VerifyResult(VerifyStatus.NO_PENDING)
    assert provider.check_calls == 0


def test_verify_is_single_use(db: Session, enabled: None) -> None:
    """验收：同一次验证只能用一次；通过之后再核验为 no_pending。"""
    add_attempt(db)
    provider = Provider()
    first = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert first.status is VerifyStatus.APPROVED
    again = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert again == VerifyResult(VerifyStatus.NO_PENDING)
    assert provider.check_calls == 1


@pytest.mark.parametrize("check", [CheckStatus.APPROVED, CheckStatus.EXPIRED])
@pytest.mark.parametrize("change", ["purpose", "approved"])
def test_verify_record_changed_before_update_is_no_pending(
    db: Session, enabled: None, check: CheckStatus, change: str
) -> None:
    """验收：以条件更新（仅当仍为 sent 且用途仍是读取时的用途）改写；核验途中被另一次发送
    改写了用途，或已被另一次核验改为 approved 时为 no_pending，原记录不变（已结束的不被
    覆盖，旧用途的核验不再生效）。
    """
    attempt_id = add_attempt(db)
    values = {"purpose": "checkout"} if change == "purpose" else {"status": "approved"}

    def _change() -> None:
        db.execute(
            update(VerificationAttempt).where(VerificationAttempt.id == attempt_id).values(values)
        )

    provider = Provider(check_result=check, on_check=_change)
    result = verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    assert result == VerifyResult(VerifyStatus.NO_PENDING)
    # 只比到创建时间：替身改库用的 ORM 更新会按模型的 onupdate 写更新时间。
    expected = {
        "purpose": (PHONE_MY, "checkout", "sent", RID_A, FIVE_MINUTES_AGO),
        "approved": (PHONE_MY, "login", "approved", RID_A, FIVE_MINUTES_AGO),
    }[change]
    assert [row[:5] for row in attempts(db)] == [expected]


# ---------------------------------------------------------------------------
# 不泄露
# ---------------------------------------------------------------------------


def test_errors_do_not_contain_phone_or_code(db: Session, enabled: None) -> None:
    """第 4 条：“不得把验证码、短信凭据或完整手机号写进日志”；验收：手机号、验证码、令牌与
    来源不出现在异常消息与返回值里。
    """
    bad_phone = "+60123"
    with pytest.raises(InvalidPhoneNumber) as excinfo:
        send(db, phone=bad_phone)
    assert bad_phone not in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        send(db, purpose="not_a_purpose")
    assert PHONE_MY not in str(excinfo.value) and TOKEN not in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        verify_code(db, Provider(), PHONE_MY, "not_a_purpose", CODE, NOW)
    message = str(excinfo.value)
    assert PHONE_MY not in message and CODE not in message

    add_attempt(db)
    result = verify_code(db, Provider(), PHONE_MY, "login", CODE, NOW)
    for text_value in (PHONE_MY, PHONE_MY[1:], CODE):
        assert text_value not in repr(result)
    outcome = send(db, now=NOW + timedelta(hours=1))
    for text_value in (PHONE_MY, TOKEN, SOURCE):
        assert text_value not in repr(outcome)


def test_verify_database_and_provider_errors_are_sanitized(
    db: Session, enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：手机号与验证码不出现在异常消息里。核验时数据库出错（异常消息带着 SQL 参数
    里的完整号码）或服务商抛出异常（消息里带着验证码）时，交给调用方的是消息固定、
    不挂原异常的 SmsVerificationError；记录不变。
    """
    add_attempt(db)

    def _raise() -> None:
        raise RuntimeError(f"check failed for {PHONE_MY} with {CODE}")

    with pytest.raises(SmsVerificationError) as excinfo:
        verify_code(db, Provider(on_check=_raise), PHONE_MY, "login", CODE, NOW)
    assert_sanitized(excinfo.value)

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT", {"phone": PHONE_MY}, Exception("lost connection"))

    monkeypatch.setattr(db, "execute", _fail)
    with pytest.raises(SmsVerificationError) as excinfo:
        verify_code(db, Provider(), PHONE_MY, "login", CODE, NOW)
    assert_sanitized(excinfo.value)
    monkeypatch.undo()
    db.rollback()
    assert attempts(db) == [(PHONE_MY, "login", "sent", RID_A, FIVE_MINUTES_AGO, FIVE_MINUTES_AGO)]


def test_no_log_records(db: Session, enabled: None, caplog: pytest.LogCaptureFixture) -> None:
    """第 4 条“不得把验证码、短信凭据或完整手机号写进日志”；验收：不写日志。"""
    caplog.set_level(logging.DEBUG)
    provider = Provider(SendResult(SendStatus.ACCEPTED, RID_A))
    assert send(db, provider=provider, purpose="login") is SendOutcome.SENT
    verify_code(db, provider, PHONE_MY, "login", CODE, NOW)
    send(db, provider=Provider(SendResult(SendStatus.UNAVAILABLE)), now=NOW + timedelta(hours=1))
    assert caplog.records == []

"""短信验证的发送与核验规则（app/services/sms_verification.py）。

依据：
- docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 3 条（下称「边界第 3 条」）：
  “白名单内号码无法送达或短信服务停发时不声称注册成功，允许改为游客下单。”
- 「边界与原则」第 4 条（下称「边界第 4 条」）：“下单、发送短信、提交验证码与确认注销时，
  服务端都按当时读取的开关值判定：开关在流程途中被关闭后，提交的验证码不再核验……”
- 「失败、并发与重试」第 2 条（下称「失败第 2 条」）：“结账时白名单号码的短信无法送达或
  短信服务停发，允许改为游客下单，但验证码错误或超过尝试次数不降级为游客。”
- 第 3 条（下称「失败第 3 条」）：“短信验证开关关闭时，所有短信发送请求在人机挑战核验、
  调用服务商与预占预算之前即被拒绝，不写验证记录，不计入限流与每日预算。”
- 第 4 条（下称「失败第 4 条」）：“发送前采用托管人机挑战，并在后端核验令牌。按规范化
  手机号、来源、国家及全站限流……短信每日总量及费用预算另在本项目 MySQL 库……兜底……
  Redis 或 MySQL 不可用时短信停发……不得把验证码、短信凭据或完整手机号写进日志。”
- 第 5 条（下称「失败第 5 条」）：“结账验证、短信登录、注册与密码重设共用同一白名单、
  人机挑战、限流和每日预算。”
- docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（下称「Kelvin 决定」）：
  标准档“每个号码 60 秒 1 条、1 小时 5 条、24 小时 10 条，
  每个来源 1 小时 10 条”；“按国家限流取全站每日上限”；
  “核验只认 10 分钟内的验证”；“验证码错误、过期与服务不可用都不降级为游客，
  限流与访客未通过人机挑战也不降级”；“人机挑战服务本身不可用……按停发处理、
  可改游客下单”；“各项都做成配置项”。
每条测试的文档字符串写明它守住的是哪一句；没有直接原句的，
写明是 SHOP-TASK-069 验收标准里的约定（下称「验收」）。

SQLite 内存库按模型建表，每个连接打开外键检查（并断言已打开）；
引擎设置与 tests/test_sms_budget.py 相同（由引擎发 BEGIN），
预占时的保存点与 MySQL 上一致。服务商与人机挑战用 SHOP-TASK-067 的
测试替身；Redis 用照 tests/test_admin_auth.py 写的内存替身 FakeRedis，
带可推进的替身时钟与可注入的连接错误。计数键在这里另算。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pytest
import redis
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import SiteSetting, SmsDailyUsage, VerificationAttempt
from app.models.member import (
    PURPOSE_CHECKOUT,
    PURPOSE_LOGIN,
    PURPOSE_REGISTER,
    VERIFICATION_APPROVED,
    VERIFICATION_REJECTED,
    VERIFICATION_SENT,
    VERIFICATION_SUSPENDED,
    VERIFICATION_UNDELIVERABLE,
)
from app.models.site import SITE_SETTING_ID
from app.services import ordering, sms_verification
from app.services.captcha import CaptchaResult, FakeCaptchaVerifier
from app.services.ordering import SmsVerificationRequired
from app.services.phone import InvalidPhoneNumber
from app.services.sms_provider import (
    FAKE_ACCEPTED,
    CheckStatus,
    FakeSmsProvider,
    SendResult,
    SendStatus,
)
from app.services.sms_verification import (
    CheckOutcome,
    CheckOutcomeStatus,
    SendOutcome,
    SendOutcomeStatus,
    check_verification,
    send_verification,
)

# UTC 03:00 即马来西亚 11:00，日期 2026-10-08。
NOW = datetime(2026, 10, 8, 3, 0, 0)
TODAY = date(2026, 10, 8)
PHONE_MY = "+60123456789"
PHONE_SG = "+6581234567"
PHONE_UK = "+447911123456"
SOURCE = "203.0.113.7"
TOKEN = "turnstile-token-7f3a"
CODE = "482913"
COST_MY = 60_000
COST_SG = 50_000
REQUEST_ID = FAKE_ACCEPTED.request_id
MINUTE = 60
HOUR = 3600
DAY = 86400


# ---------------------------------------------------------------------------
# 夹具与替身
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 与 tests/test_sms_budget.py 相同：关掉驱动的事务处理、由引擎发 BEGIN，
    # 预占在保存点里插入当日行时才和 MySQL 上一样。
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


class Provider(FakeSmsProvider):
    """SHOP-TASK-067 的 FakeSmsProvider，另加两点：

    unique_ids 为真时每次受理给一个新的请求 ID（不同号码不共用请求 ID）；
    on_send、on_check 在调用时先执行，用来观察或改动那一刻的数据库。
    """

    def __init__(
        self,
        send_result: SendResult = FAKE_ACCEPTED,
        check_result: CheckStatus = CheckStatus.APPROVED,
        *,
        unique_ids: bool = False,
        on_send: Callable[[], None] | None = None,
        on_check: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(send_result, check_result)
        self.unique_ids = unique_ids
        self.on_send = on_send
        self.on_check = on_check

    def start_verification(self, phone_e164: str) -> SendResult:
        if self.on_send is not None:
            self.on_send()
        result = super().start_verification(phone_e164)
        if self.unique_ids and result.status is SendStatus.ACCEPTED:
            return SendResult(SendStatus.ACCEPTED, f"VE{self.send_calls:032x}")
        return result

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        if self.on_check is not None:
            self.on_check()
        return super().check_verification(request_id, code)


@dataclass
class Harness:
    provider: Provider = field(default_factory=Provider)
    captcha: FakeCaptchaVerifier = field(default_factory=FakeCaptchaVerifier)
    redis: FakeRedis | None = field(default_factory=FakeRedis)


def make_settings(**overrides: int) -> Settings:
    values: dict[str, int] = {
        "sms_max_cost_micro_usd_my": COST_MY,
        "sms_max_cost_micro_usd_sg": COST_SG,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def set_switch(db: Session, enabled: bool) -> None:
    db.merge(SiteSetting(id=SITE_SETTING_ID, sms_verification_enabled=enabled))
    db.commit()


def send(
    db: Session,
    h: Harness,
    *,
    phone: str = PHONE_MY,
    purpose: str = PURPOSE_CHECKOUT,
    source: str = SOURCE,
    now: datetime = NOW,
    settings: Settings | None = None,
) -> SendOutcome:
    return send_verification(
        db,
        settings or make_settings(),
        h.provider,
        h.captcha,
        h.redis,
        phone_e164=phone,
        purpose=purpose,
        captcha_token=TOKEN,
        source=source,
        now=now,
    )


def check(
    db: Session,
    provider: Provider,
    *,
    phone: str = PHONE_MY,
    purpose: str = PURPOSE_CHECKOUT,
    code: str | None = CODE,
    now: datetime = NOW,
) -> CheckOutcome:
    return check_verification(db, provider, phone_e164=phone, purpose=purpose, code=code, now=now)


@dataclass(frozen=True)
class Attempt:
    id: int
    phone: str
    purpose: str
    status: str
    request_id: str | None
    created_at: datetime
    updated_at: datetime


def attempts(db: Session) -> list[Attempt]:
    """库里的验证记录（按列读取，不取会话里已加载的旧对象）。"""
    rows = db.execute(
        select(
            VerificationAttempt.id,
            VerificationAttempt.phone,
            VerificationAttempt.purpose,
            VerificationAttempt.status,
            VerificationAttempt.provider_request_id,
            VerificationAttempt.created_at,
            VerificationAttempt.updated_at,
        ).order_by(VerificationAttempt.id)
    ).all()
    return [Attempt(*row) for row in rows]


def usage(db: Session) -> tuple[int, int, int] | None:
    """当日预算行的（条数, 已预占, 已结算）；没有行为空。"""
    row = db.execute(
        select(
            SmsDailyUsage.sent_count,
            SmsDailyUsage.reserved_micro_usd,
            SmsDailyUsage.settled_micro_usd,
        ).where(SmsDailyUsage.usage_date == TODAY)
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def key(bucket: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return f"acuven_shop:rate_limit:{bucket}:{digest}"


def my_phone(i: int) -> str:
    """第 i 个马来西亚手机号（+60 12 加 7 位）。"""
    return f"+6012{i:07d}"


def guest_allowed(db: Session, phone: str = PHONE_MY) -> bool:
    """app/services/ordering.py 现有的降级判定是否放行该号码以游客下单。"""
    try:
        ordering._require_guest_allowed(db, phone, NOW)
    except SmsVerificationRequired:
        return False
    return True


@pytest.fixture
def enabled(db: Session) -> None:
    set_switch(db, True)


# ---------------------------------------------------------------------------
# 判定顺序：开关、白名单、人机挑战
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", [True, False], ids=["switch_off", "no_setting_row"])
@pytest.mark.parametrize("redis_configured", [True, False], ids=["redis", "no_redis"])
def test_switch_off_rejects_before_captcha_provider_budget_and_rate_limit(
    db: Session, row: bool, redis_configured: bool
) -> None:
    """失败第 3 条：“短信验证开关关闭时，所有短信发送请求在人机挑战核验、
    调用服务商与预占预算之前即被拒绝，不写验证记录，不计入限流与每日预算。”
    没有设置行时视为关闭；Redis 未配置也是 sms_disabled 而不是停发。
    """
    if row:
        set_switch(db, False)
    h = Harness(redis=FakeRedis() if redis_configured else None)

    outcome = send(db, h)

    assert outcome == SendOutcome(SendOutcomeStatus.SMS_DISABLED)
    assert h.captcha.calls == 0
    assert h.provider.send_calls == 0
    assert h.redis is None or h.redis.values == {}
    assert usage(db) is None
    assert attempts(db) == []


def test_switch_is_read_on_every_send(db: Session) -> None:
    """边界第 4 条：“发送短信……时，服务端都按当时读取的开关值判定”。"""
    h = Harness()
    set_switch(db, True)
    assert send(db, h).status is SendOutcomeStatus.SENT
    set_switch(db, False)
    h.redis.advance(MINUTE)

    assert send(db, h).status is SendOutcomeStatus.SMS_DISABLED
    assert h.provider.send_calls == 1
    assert h.captcha.calls == 1


@pytest.mark.usefixtures("enabled")
def test_not_whitelisted_writes_no_record(db: Session) -> None:
    """边界第 3 条：“初期短信验证只开放马来西亚和新加坡”，
    “白名单外号码不验证”。验收：白名单外即 not_whitelisted，不写记录
    （也不核验人机挑战、不计限流、不预占）。
    """
    h = Harness()

    outcome = send(db, h, phone=PHONE_UK)

    assert outcome == SendOutcome(SendOutcomeStatus.NOT_WHITELISTED)
    assert h.captcha.calls == 0
    assert h.provider.send_calls == 0
    assert h.redis.values == {}
    assert usage(db) is None
    assert attempts(db) == []


@pytest.mark.usefixtures("enabled")
def test_captcha_failed_writes_no_record_and_does_not_degrade(db: Session) -> None:
    """失败第 4 条：“发送前采用托管人机挑战，并在后端核验令牌”；
    Kelvin 决定：“访客未通过人机挑战也不降级”。不写记录、不计限流、
    不预占、不调用服务商，马新号码仍不能游客下单。
    """
    h = Harness(captcha=FakeCaptchaVerifier(CaptchaResult.FAILED))

    outcome = send(db, h)

    assert outcome == SendOutcome(SendOutcomeStatus.CAPTCHA_FAILED)
    assert h.provider.send_calls == 0
    assert h.redis.values == {}
    assert usage(db) is None
    assert attempts(db) == []
    assert not guest_allowed(db)


@pytest.mark.usefixtures("enabled")
def test_captcha_service_unavailable_suspends_and_allows_guest(db: Session) -> None:
    """Kelvin 决定：“人机挑战服务本身不可用（连不上或出错）按停发处理、
    可改游客下单”；边界第 3 条：“短信服务停发时……允许改为游客下单”。
    写 suspended（无请求 ID），不计限流、不预占、不调用服务商。
    """
    h = Harness(captcha=FakeCaptchaVerifier(CaptchaResult.UNAVAILABLE))

    outcome = send(db, h)

    assert outcome.status is SendOutcomeStatus.SUSPENDED
    [attempt] = attempts(db)
    assert outcome.attempt_id == attempt.id
    assert (attempt.status, attempt.request_id, attempt.purpose) == (
        VERIFICATION_SUSPENDED,
        None,
        PURPOSE_CHECKOUT,
    )
    assert h.provider.send_calls == 0
    assert h.redis.values == {}
    assert usage(db) is None
    assert guest_allowed(db)


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("overrides", "limit", "vary_phone"),
    [
        pytest.param({}, 1, False, id="phone_per_minute_1"),
        pytest.param({"sms_phone_limit_per_minute": 1000}, 5, False, id="phone_per_hour_5"),
        pytest.param(
            {"sms_phone_limit_per_minute": 1000, "sms_phone_limit_per_hour": 1000},
            10,
            False,
            id="phone_per_day_10",
        ),
        pytest.param({}, 10, True, id="source_per_hour_10"),
        pytest.param({"sms_source_limit_per_hour": 1000}, 200, True, id="country_per_day_200"),
    ],
)
def test_each_limit_allows_exactly_its_threshold(
    db: Session, overrides: dict[str, int], limit: int, vary_phone: bool
) -> None:
    """Kelvin 决定：“每个号码 60 秒 1 条、1 小时 5 条、24 小时 10 条，
    每个来源 1 小时 10 条”；“按国家限流取全站每日上限（……默认 200……）”。
    被测的阈值取默认值，其余调高；恰到上限放行，多一次 rate_limited，
    不写记录、不调用服务商、不预占。国家的第 201 条在预算之前即被限流
    拒绝（不是停发）。
    """
    settings = make_settings(**overrides)
    h = Harness(provider=Provider(unique_ids=True))

    def phone(i: int) -> str:
        return my_phone(i) if vary_phone else PHONE_MY

    for i in range(limit):
        assert send(db, h, phone=phone(i), settings=settings).status is SendOutcomeStatus.SENT
    outcome = send(db, h, phone=phone(limit), settings=settings)

    assert outcome == SendOutcome(SendOutcomeStatus.RATE_LIMITED)
    assert h.provider.send_calls == limit
    assert len(attempts(db)) == limit
    assert usage(db) == (limit, 0, limit * COST_MY)


@pytest.mark.usefixtures("enabled")
def test_buckets_are_distinct_hashed_and_windowed(db: Session) -> None:
    """失败第 4 条：“按规范化手机号、来源、国家及全站限流”。验收：计数桶名
    各不相同，号码与来源只以 rate_limit_key 的摘要进 Redis；窗口依次为
    60 秒、1 小时、24 小时、1 小时、24 小时。
    """
    h = Harness()

    send(db, h)

    expected = {
        key("sms_phone_minute", PHONE_MY): MINUTE,
        key("sms_phone_hour", PHONE_MY): HOUR,
        key("sms_phone_day", PHONE_MY): DAY,
        key("sms_source_hour", SOURCE): HOUR,
        key("sms_country_day", "60"): DAY,
    }
    assert h.redis.values == dict.fromkeys(expected, 1)
    assert {k: h.redis.ttl(k) for k in expected} == expected
    for k in h.redis.values:
        assert PHONE_MY[1:] not in k and SOURCE not in k


@pytest.mark.usefixtures("enabled")
def test_singapore_numbers_count_in_their_own_country_bucket(db: Session) -> None:
    """失败第 4 条：“按……国家……限流”。新加坡号码计入 65 的桶，
    不计入 60 的桶。
    """
    h = Harness()

    send(db, h, phone=PHONE_SG)

    assert h.redis.values[key("sms_country_day", "65")] == 1
    assert key("sms_country_day", "60") not in h.redis.values


@pytest.mark.usefixtures("enabled")
def test_buckets_after_the_first_exceeded_one_are_not_counted(db: Session) -> None:
    """验收：按号码 60 秒、号码 1 小时、号码 24 小时、来源 1 小时、
    国家呼叫码 24 小时的顺序逐桶计数，第一个超限的桶即 rate_limited，
    其后的桶不再计数，已计入的桶不回退。
    """
    h = Harness(provider=Provider(unique_ids=True))

    send(db, h)
    assert send(db, h).status is SendOutcomeStatus.RATE_LIMITED
    assert h.redis.values == {
        key("sms_phone_minute", PHONE_MY): 2,
        key("sms_phone_hour", PHONE_MY): 1,
        key("sms_phone_day", PHONE_MY): 1,
        key("sms_source_hour", SOURCE): 1,
        key("sms_country_day", "60"): 1,
    }

    # 来源超限：号码的三个桶照常计入（不回退），国家的桶不计。
    other = my_phone(1)
    settings = make_settings(sms_source_limit_per_hour=1)
    assert send(db, h, phone=other, settings=settings).status is SendOutcomeStatus.RATE_LIMITED
    assert h.redis.values[key("sms_phone_minute", other)] == 1
    assert h.redis.values[key("sms_phone_hour", other)] == 1
    assert h.redis.values[key("sms_phone_day", other)] == 1
    assert h.redis.values[key("sms_source_hour", SOURCE)] == 2
    assert h.redis.values[key("sms_country_day", "60")] == 1


@pytest.mark.usefixtures("enabled")
def test_rate_limited_writes_no_record_and_does_not_degrade(db: Session) -> None:
    """Kelvin 决定：“限流……也不降级”。rate_limited 不写记录；该号码只有
    sent 记录，ordering 的降级判定不放行游客下单。
    """
    h = Harness()
    send(db, h)

    assert send(db, h).status is SendOutcomeStatus.RATE_LIMITED

    assert [a.status for a in attempts(db)] == [VERIFICATION_SENT]
    assert not guest_allowed(db)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize("failure", ["none", "unavailable"])
def test_redis_missing_or_unavailable_suspends(db: Session, failure: str) -> None:
    """失败第 4 条：“Redis 或 MySQL 不可用时短信停发”。验收：客户端为 None
    或抛 RateLimitUnavailable 即停发，写 suspended（不带请求 ID），
    不当作未超限放行。限流在预算之前，此时尚未预占，没有要释放的数。
    """
    fake = FakeRedis()
    fake.fail_on = "execute"
    h = Harness(redis=None if failure == "none" else fake)

    outcome = send(db, h)

    assert outcome.status is SendOutcomeStatus.SUSPENDED
    [attempt] = attempts(db)
    assert (attempt.status, attempt.request_id) == (VERIFICATION_SUSPENDED, None)
    assert h.provider.send_calls == 0
    assert usage(db) is None
    assert guest_allowed(db)


def test_limit_settings_defaults_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kelvin 决定：“各项都做成配置项”；默认值即标准档 1、5、10、10，
    按国家取全站每日条数 200；环境变量前缀 SHOP_。
    """
    defaults = Settings(_env_file=None)
    assert (
        defaults.sms_phone_limit_per_minute,
        defaults.sms_phone_limit_per_hour,
        defaults.sms_phone_limit_per_day,
        defaults.sms_source_limit_per_hour,
        defaults.sms_daily_count_limit,
    ) == (1, 5, 10, 10, 200)

    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_HOUR", "6")
    monkeypatch.setenv("SHOP_SMS_PHONE_LIMIT_PER_DAY", "11")
    monkeypatch.setenv("SHOP_SMS_SOURCE_LIMIT_PER_HOUR", "12")
    configured = Settings(_env_file=None)
    assert (
        configured.sms_phone_limit_per_minute,
        configured.sms_phone_limit_per_hour,
        configured.sms_phone_limit_per_day,
        configured.sms_source_limit_per_hour,
    ) == (2, 6, 11, 12)


# ---------------------------------------------------------------------------
# 预算、发起结果与记录
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("phone", "overrides"),
    [
        pytest.param(PHONE_SG, {"sms_max_cost_micro_usd_sg": 0}, id="unknown_cost"),
        pytest.param(PHONE_MY, {"sms_daily_cost_limit_micro_usd": COST_MY - 1}, id="over_budget"),
    ],
)
def test_budget_rejection_suspends_without_calling_provider(
    db: Session, phone: str, overrides: dict[str, int]
) -> None:
    """失败第 4 条：“发送前按目的地预占保守的单次最高费用，未知费用时停发”，
    “超过每日总量或费用阈值停发”。写 suspended（不带请求 ID），
    不调用服务商，预算不改数，该号码可改游客下单。
    """
    h = Harness()

    outcome = send(db, h, phone=phone, settings=make_settings(**overrides))

    assert outcome.status is SendOutcomeStatus.SUSPENDED
    [attempt] = attempts(db)
    assert (attempt.status, attempt.request_id) == (VERIFICATION_SUSPENDED, None)
    assert h.provider.send_calls == 0
    assert usage(db) == (0, 0, 0)
    assert guest_allowed(db, phone)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("send_result", "outcome_status", "record_status", "request_id", "budget"),
    [
        pytest.param(
            FAKE_ACCEPTED,
            SendOutcomeStatus.SENT,
            VERIFICATION_SENT,
            REQUEST_ID,
            (1, 0, COST_MY),
            id="accepted_settles",
        ),
        pytest.param(
            SendResult(SendStatus.UNDELIVERABLE),
            SendOutcomeStatus.UNDELIVERABLE,
            VERIFICATION_UNDELIVERABLE,
            None,
            (0, 0, 0),
            id="undeliverable_without_id_releases",
        ),
        pytest.param(
            SendResult(SendStatus.UNDELIVERABLE, REQUEST_ID),
            SendOutcomeStatus.UNDELIVERABLE,
            VERIFICATION_UNDELIVERABLE,
            REQUEST_ID,
            (1, 0, COST_MY),
            id="undeliverable_with_id_settles",
        ),
        pytest.param(
            SendResult(SendStatus.UNAVAILABLE),
            SendOutcomeStatus.SUSPENDED,
            VERIFICATION_SUSPENDED,
            None,
            (0, 0, 0),
            id="unavailable_suspends_and_releases",
        ),
    ],
)
def test_provider_results_record_request_id_and_budget(
    db: Session,
    send_result: SendResult,
    outcome_status: SendOutcomeStatus,
    record_status: str,
    request_id: str | None,
    budget: tuple[int, int, int],
) -> None:
    """失败第 4 条：“系统只存验证请求与结果，不存验证码”，“结算后更新记录”。
    验收：accepted 写 sent（带请求 ID）并结算；undeliverable 有请求 ID 时
    带上并结算、没有时释放；服务商 unavailable 写 suspended（不带请求 ID）
    并释放。记录的用途为调用方给出的用途。
    """
    h = Harness(provider=Provider(send_result))

    outcome = send(db, h, purpose=PURPOSE_LOGIN)

    [attempt] = attempts(db)
    assert outcome == SendOutcome(outcome_status, attempt.id)
    assert (attempt.phone, attempt.purpose, attempt.status, attempt.request_id) == (
        PHONE_MY,
        PURPOSE_LOGIN,
        record_status,
        request_id,
    )
    assert attempt.created_at == attempt.updated_at == NOW
    assert usage(db) == budget


@pytest.mark.usefixtures("enabled")
def test_reservation_is_committed_before_calling_provider(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败第 4 条：“防 Redis 重置后超过日上限”。验收：预占成功即先提交，
    使每日上限在之后的步骤失败时仍然计入；写记录与结算在第二个事务里
    一起提交。
    """
    events: list[str] = []
    real_commit = db.commit

    def commit() -> None:
        events.append("commit")
        real_commit()

    def on_send() -> None:
        events.append("send")
        assert not db.in_transaction()
        assert not db.new and not db.dirty

    monkeypatch.setattr(db, "commit", commit)
    h = Harness(provider=Provider(on_send=on_send))

    assert send(db, h).status is SendOutcomeStatus.SENT
    assert events == ["commit", "send", "commit"]


@pytest.mark.usefixtures("enabled")
def test_second_transaction_failure_keeps_reservation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验收：第二个事务提交失败时异常交给调用方，已提交的预占保留为 reserved
    （保守计入每日上限），这次的验证码因没有记录而无法核验，访客可在限流
    允许时重发。
    """
    real_commit = db.commit
    calls: list[int] = []

    def commit() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise OperationalError("COMMIT", {}, Exception("server has gone away"))
        real_commit()

    monkeypatch.setattr(db, "commit", commit)
    h = Harness()

    with pytest.raises(OperationalError) as excinfo:
        send(db, h)
    assert PHONE_MY[1:] not in str(excinfo.value)
    monkeypatch.undo()
    db.rollback()

    assert h.provider.send_calls == 1
    assert attempts(db) == []
    assert usage(db) == (1, COST_MY, 0)
    assert check(db, h.provider).status is CheckOutcomeStatus.NO_PENDING
    assert h.provider.check_calls == 0

    h.redis.advance(MINUTE)
    assert send(db, h).status is SendOutcomeStatus.SENT
    assert usage(db) == (2, COST_MY, COST_MY)


@pytest.mark.usefixtures("enabled")
def test_mysql_error_while_reserving_is_raised_without_provider_or_record(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败第 4 条：“Redis 或 MySQL 不可用时短信停发”。验收：预占前或预占时
    MySQL 出错，异常交给调用方（不调用服务商、不写记录）。
    """

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT", {}, Exception("lock wait timeout"))

    monkeypatch.setattr(sms_verification, "reserve_sms", broken)
    h = Harness()

    with pytest.raises(OperationalError):
        send(db, h)
    db.rollback()

    assert h.provider.send_calls == 0
    assert attempts(db) == []


@pytest.mark.usefixtures("enabled")
def test_reused_request_id_accepted_rewrites_purpose_and_status(db: Session) -> None:
    """验收：Twilio Verify 对同一号码仍待核验的验证再次发起时沿用原请求 ID，
    不新增记录，把已有的那条改为 sent、用途改为本次的、更新时间改为当前，
    照样结算本次预占；同一请求 ID 以最近一次发送的用途为准，之前用途的
    待核验随之作废。
    """
    h = Harness()
    first = send(db, h, purpose=PURPOSE_CHECKOUT)
    h.redis.advance(MINUTE)
    later = NOW + timedelta(minutes=2)

    second = send(db, h, purpose=PURPOSE_REGISTER, now=later)

    [attempt] = attempts(db)
    assert first.attempt_id == second.attempt_id == attempt.id
    assert second.status is SendOutcomeStatus.SENT
    assert (attempt.status, attempt.purpose, attempt.request_id) == (
        VERIFICATION_SENT,
        PURPOSE_REGISTER,
        REQUEST_ID,
    )
    assert (attempt.created_at, attempt.updated_at) == (NOW, later)
    assert usage(db) == (2, 0, 2 * COST_MY)

    old_purpose = check(db, h.provider, purpose=PURPOSE_CHECKOUT, now=later)
    assert old_purpose.status is CheckOutcomeStatus.NO_PENDING
    assert h.provider.check_calls == 0
    new_purpose = check(db, h.provider, purpose=PURPOSE_REGISTER, now=later)
    assert new_purpose == CheckOutcome(CheckOutcomeStatus.APPROVED, attempt.id)


@pytest.mark.usefixtures("enabled")
def test_reused_request_id_undeliverable_rewrites_record(db: Session) -> None:
    """验收：带回已存在请求 ID 的 undeliverable 把已有记录改为 undeliverable、
    用途改为本次的、不新增记录、照样结算。边界第 3 条：“白名单内号码
    无法送达……允许改为游客下单”——该 checkout 记录使马新号码可游客下单。
    """
    h = Harness()
    send(db, h, purpose=PURPOSE_LOGIN)
    h.redis.advance(MINUTE)
    h.provider.send_result = SendResult(SendStatus.UNDELIVERABLE, REQUEST_ID)
    later = NOW + timedelta(minutes=1)

    outcome = send(db, h, purpose=PURPOSE_CHECKOUT, now=later)

    [attempt] = attempts(db)
    assert outcome == SendOutcome(SendOutcomeStatus.UNDELIVERABLE, attempt.id)
    assert (attempt.status, attempt.purpose, attempt.request_id) == (
        VERIFICATION_UNDELIVERABLE,
        PURPOSE_CHECKOUT,
        REQUEST_ID,
    )
    assert (attempt.created_at, attempt.updated_at) == (NOW, later)
    assert usage(db) == (2, 0, 2 * COST_MY)
    assert guest_allowed(db)


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("send_result", "allowed"),
    [
        pytest.param(SendResult(SendStatus.UNDELIVERABLE), True, id="undeliverable"),
        pytest.param(SendResult(SendStatus.UNAVAILABLE), True, id="suspended"),
        pytest.param(FAKE_ACCEPTED, False, id="sent"),
    ],
)
def test_checkout_records_feed_ordering_fallback(
    db: Session, send_result: SendResult, allowed: bool
) -> None:
    """边界第 3 条与失败第 2 条：“结账时白名单号码的短信无法送达或短信服务
    停发，允许改为游客下单”。验收：与 ordering 现有的降级判定（最近 30 分钟、
    用途 checkout、状态 undeliverable 或 suspended）衔接；已受理不降级。
    """
    h = Harness(provider=Provider(send_result))

    send(db, h, purpose=PURPOSE_CHECKOUT)

    assert guest_allowed(db) is allowed


@pytest.mark.usefixtures("enabled")
def test_non_checkout_purpose_does_not_feed_ordering_fallback(db: Session) -> None:
    """边界第 3 条只对结账降级。验收：记录的用途为调用方给出的用途，
    注册用途的 undeliverable 记录不使该号码可游客下单。
    """
    h = Harness(provider=Provider(SendResult(SendStatus.UNDELIVERABLE)))

    send(db, h, purpose=PURPOSE_REGISTER)

    assert guest_allowed(db) is False


# ---------------------------------------------------------------------------
# 核验
# ---------------------------------------------------------------------------


def _sent(db: Session, *, purpose: str = PURPOSE_CHECKOUT, now: datetime = NOW) -> Harness:
    set_switch(db, True)
    h = Harness()
    assert send(db, h, purpose=purpose, now=now).status is SendOutcomeStatus.SENT
    return h


@pytest.mark.parametrize(
    ("check_result", "outcome_status", "record_status"),
    [
        pytest.param(
            CheckStatus.APPROVED,
            CheckOutcomeStatus.APPROVED,
            VERIFICATION_APPROVED,
            id="approved",
        ),
        pytest.param(
            CheckStatus.WRONG_CODE,
            CheckOutcomeStatus.WRONG_CODE,
            VERIFICATION_SENT,
            id="wrong_code",
        ),
        pytest.param(
            CheckStatus.EXPIRED,
            CheckOutcomeStatus.EXPIRED,
            VERIFICATION_REJECTED,
            id="expired",
        ),
        pytest.param(
            CheckStatus.UNAVAILABLE,
            CheckOutcomeStatus.UNAVAILABLE,
            VERIFICATION_SENT,
            id="unavailable",
        ),
    ],
)
def test_check_results_and_record(
    db: Session,
    check_result: CheckStatus,
    outcome_status: CheckOutcomeStatus,
    record_status: str,
) -> None:
    """失败第 4 条：“由提供方负责验证码生命周期……系统只存验证请求与结果”。
    验收：approved 改为 approved 并返回记录 ID；wrong_code 不改记录；
    expired 改为 rejected；unavailable 不改记录。Kelvin 决定：“验证码错误、
    过期与服务不可用都不降级为游客”——ordering 不放行。
    """
    h = _sent(db)
    h.provider.check_result = check_result
    later = NOW + timedelta(minutes=3)

    outcome = check(db, h.provider, now=later)

    [attempt] = attempts(db)
    expected_id = attempt.id if outcome_status is CheckOutcomeStatus.APPROVED else None
    assert outcome == CheckOutcome(outcome_status, expected_id)
    assert attempt.status == record_status
    assert attempt.updated_at == (NOW if record_status == VERIFICATION_SENT else later)
    assert h.provider.check_calls == 1
    assert not guest_allowed(db)


def test_check_only_flushes(db: Session) -> None:
    """验收：核验只 flush、不提交，由调用方提交或回滚。"""
    h = _sent(db)

    assert check(db, h.provider).status is CheckOutcomeStatus.APPROVED
    db.rollback()

    assert [a.status for a in attempts(db)] == [VERIFICATION_SENT]


def test_one_verification_can_be_used_once(db: Session) -> None:
    """验收：同一次验证只能用一次——通过后再提交即 no_pending，
    不再调用服务商。
    """
    h = _sent(db)

    assert check(db, h.provider).status is CheckOutcomeStatus.APPROVED
    db.commit()

    assert check(db, h.provider) == CheckOutcome(CheckOutcomeStatus.NO_PENDING)
    assert h.provider.check_calls == 1


@pytest.mark.parametrize(
    "code",
    ["123", "12345678901", "12a456", "", " 482913", "４８２９１３", None],
    ids=["3_digits", "11_digits", "letter", "empty", "space", "fullwidth", "none"],
)
def test_malformed_code_is_wrong_code_without_provider(db: Session, code: str | None) -> None:
    """验收：验证码不是 4 到 10 位数字即 wrong_code（不调用服务商）；
    失败第 2 条：“验证码错误……不降级为游客”。记录不改。
    """
    h = _sent(db)

    assert check(db, h.provider, code=code) == CheckOutcome(CheckOutcomeStatus.WRONG_CODE)
    assert h.provider.check_calls == 0
    assert [a.status for a in attempts(db)] == [VERIFICATION_SENT]


@pytest.mark.parametrize("code", ["1234", "1234567890"], ids=["4_digits", "10_digits"])
def test_code_length_bounds_reach_provider(db: Session, code: str) -> None:
    """验收：4 到 10 位数字的验证码交给服务商核验。"""
    h = _sent(db)

    assert check(db, h.provider, code=code).status is CheckOutcomeStatus.APPROVED
    assert h.provider.check_calls == 1


@pytest.mark.parametrize(
    ("age", "outcome_status", "provider_calls"),
    [
        pytest.param(timedelta(minutes=10), CheckOutcomeStatus.APPROVED, 1, id="exactly_10_min"),
        pytest.param(
            timedelta(minutes=10, seconds=1), CheckOutcomeStatus.NO_PENDING, 0, id="10_min_1_s"
        ),
        pytest.param(timedelta(0), CheckOutcomeStatus.APPROVED, 1, id="created_now"),
        pytest.param(
            timedelta(seconds=-1), CheckOutcomeStatus.NO_PENDING, 0, id="created_1_s_in_future"
        ),
        pytest.param(
            timedelta(minutes=-5), CheckOutcomeStatus.NO_PENDING, 0, id="created_5_min_in_future"
        ),
    ],
)
def test_check_window_is_ten_minutes(
    db: Session, age: timedelta, outcome_status: CheckOutcomeStatus, provider_calls: int
) -> None:
    """Kelvin 决定：“核验只认 10 分钟内的验证（Twilio Verify 验证码的
    默认有效期）”。窗口是 [now - 10 分钟, now]：早于窗口与创建时间晚于
    当前时间的记录都不是“最近 10 分钟内”，即 no_pending，不调用服务商。
    """
    h = _sent(db, now=NOW - age)

    assert check(db, h.provider).status is outcome_status
    assert h.provider.check_calls == provider_calls


def test_resend_with_reused_request_id_does_not_extend_window(db: Session) -> None:
    """验收：有效期从首次发起算，沿用请求 ID 的重发不延长，所以按创建时间
    而不是更新时间判定 10 分钟。
    """
    h = _sent(db)
    h.redis.advance(MINUTE)
    assert send(db, h, now=NOW + timedelta(minutes=5)).status is SendOutcomeStatus.SENT

    late = NOW + timedelta(minutes=10, seconds=1)
    assert check(db, h.provider, now=late).status is CheckOutcomeStatus.NO_PENDING
    assert h.provider.check_calls == 0


def test_check_matches_phone_and_purpose(db: Session) -> None:
    """失败第 5 条：各用途“共用同一白名单、人机挑战、限流和每日预算”，
    但核验只找该号码与用途的记录：别的用途或别的号码即 no_pending。
    """
    h = _sent(db, purpose=PURPOSE_CHECKOUT)

    assert check(db, h.provider, purpose=PURPOSE_LOGIN).status is CheckOutcomeStatus.NO_PENDING
    assert check(db, h.provider, phone=PHONE_SG).status is CheckOutcomeStatus.NO_PENDING
    assert h.provider.check_calls == 0


def test_purpose_rewritten_between_read_and_update_is_no_pending(db: Session) -> None:
    """验收：approved 以条件更新（仅当该记录仍为 sent 且用途仍是读取时的
    用途）改为 approved，更新不到即 no_pending；核验途中被另一次发送
    改写了用途的，旧用途的核验不再生效。
    """
    h = _sent(db, purpose=PURPOSE_CHECKOUT)
    [attempt] = attempts(db)

    def rewrite_purpose() -> None:
        db.execute(
            update(VerificationAttempt)
            .where(VerificationAttempt.id == attempt.id)
            .values(purpose=PURPOSE_REGISTER)
        )

    h.provider.on_check = rewrite_purpose

    assert check(db, h.provider) == CheckOutcome(CheckOutcomeStatus.NO_PENDING)
    assert h.provider.check_calls == 1
    [after] = attempts(db)
    assert (after.status, after.purpose) == (VERIFICATION_SENT, PURPOSE_REGISTER)


def test_switch_off_after_send_stops_check_without_provider(db: Session) -> None:
    """边界第 4 条：“开关在流程途中被关闭后，提交的验证码不再核验”。
    验收：按当次从数据库读取的开关（不缓存），关闭即 sms_disabled、
    不调用服务商，含记录已存在之后才关闭的情形。
    """
    h = _sent(db)
    set_switch(db, False)

    assert check(db, h.provider) == CheckOutcome(CheckOutcomeStatus.SMS_DISABLED)
    assert h.provider.check_calls == 0
    assert [a.status for a in attempts(db)] == [VERIFICATION_SENT]

    set_switch(db, True)
    assert check(db, h.provider).status is CheckOutcomeStatus.APPROVED


# ---------------------------------------------------------------------------
# 不泄露
# ---------------------------------------------------------------------------


def test_no_logs_and_no_secrets_in_outcomes_or_errors(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """失败第 4 条：“不得把验证码、短信凭据或完整手机号写进日志”。验收：
    不写日志，手机号、验证码、令牌与来源不出现在异常消息与返回值里。
    """
    caplog.set_level(logging.DEBUG)
    secrets = (PHONE_MY[1:], CODE, TOKEN, SOURCE)
    h = _sent(db)
    outcomes: list[object] = [check(db, h.provider)]
    h.provider.unique_ids = True
    h.redis.advance(MINUTE)
    outcomes.append(send(db, h))
    h.provider.send_result = SendResult(SendStatus.UNAVAILABLE)
    h.redis.advance(MINUTE)
    outcomes.append(send(db, h))

    errors: list[Exception] = []
    with pytest.raises(ValueError) as bad_purpose:
        send(db, h, purpose="checkout " + PHONE_MY)
    errors.append(bad_purpose.value)
    with pytest.raises(ValueError) as bad_check_purpose:
        check(db, h.provider, purpose=CODE)
    errors.append(bad_check_purpose.value)
    with pytest.raises(InvalidPhoneNumber) as bad_phone:
        send(db, h, phone="+60 12-345 6789")
    errors.append(bad_phone.value)

    for item in [*outcomes, *errors]:
        for secret in secrets:
            assert secret not in repr(item) and secret not in str(item)
    assert [r for r in caplog.records if r.name.startswith("app")] == []

"""短信验证的发送与核验规则：开关、白名单、人机挑战、限流、每日预算、服务商与验证记录。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 3、4 条（白名单号码无法送达或停发时
允许改为游客下单；发送短信与提交验证码时按当时读取的开关值判定）、「失败、并发与重试」第 2 到
5 条（验证码错误不降级；开关关闭时在人机挑战、服务商与预算之前即拒绝，不写记录、不计限流与
预算；Twilio Verify、人机挑战、按号码、来源、国家与全站限流、MySQL 预算兜底、Redis 或 MySQL
不可用时停发；共用同一白名单、人机挑战、限流和每日预算），以及 docs/HANDOFF.md 0.41 记录的
Kelvin 2026-10-08 决定（标准档限流；人机挑战服务本身不可用按停发、访客未通过不降级；只认
10 分钟内的验证）与 0.43 记录的 2026-10-09 决定（请求 ID 对上已结束或另一个号码的记录时
原记录不改、按服务商不可用停发并释放预占）。

组合 SHOP-TASK-067 的适配器（sms_provider.py、captcha.py）、068 的每日预算（sms_budget.py）
与 026 的 Redis 限流（rate_limit.py），写 SHOP-TASK-012 的 verification_attempts。

发送 send_verification 分两个事务：预占成功即提交（之后无论出什么异常，每日上限都已计入）；
调用服务商之后，写记录与结算或释放在第二个事务里一起提交。第二个事务提交失败时异常交给
调用方，已提交的预占保留为 reserved（保守计入每日上限），不重读、不重试。
核验 verify_code 只 flush、不提交，由调用方（登录、注册与重设）与之后的步骤一起提交。

时间一律是不带时区的 UTC。接口、登录、注册与认领不在这里。
本模块不写日志；手机号、验证码、令牌与来源不出现在异常消息与返回值里。数据库异常（如请求 ID
撞上唯一约束时的 IntegrityError，消息里带 INSERT 参数中的完整号码）与服务商调用抛出的异常
都换成消息固定的 SmsVerificationError 交给调用方，且不挂原异常（__cause__ 与 __context__
都为空）；会话须由调用方回滚。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import redis
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import VerificationAttempt
from app.models.member import (
    VERIFICATION_APPROVED,
    VERIFICATION_PURPOSES,
    VERIFICATION_REJECTED,
    VERIFICATION_SENT,
    VERIFICATION_SUSPENDED,
    VERIFICATION_UNDELIVERABLE,
)
from app.services.captcha import CaptchaResult, CaptchaVerifier
from app.services.phone import is_sms_whitelisted, normalize_phone
from app.services.rate_limit import RateLimitUnavailable, hit
from app.services.site_settings import is_sms_verification_enabled
from app.services.sms_budget import RESERVED, Reservation, release_sms, reserve_sms, settle_sms
from app.services.sms_provider import CheckStatus, SendResult, SendStatus, SmsProvider

# Twilio Verify 验证码的默认有效期，从首次发起算；沿用请求 ID 的重发不延长（Kelvin 2026-10-08）。
CODE_WINDOW = timedelta(minutes=10)

# Twilio Verify 的验证码长度可设 4–10 位，只含数字。
_CODE_PATTERN = re.compile(r"[0-9]{4,10}")

# 计数桶名，各不相同。号码与来源只以 rate_limit_key 的摘要进 Redis。
BUCKET_PHONE_MINUTE = "sms_phone_minute"
BUCKET_PHONE_HOUR = "sms_phone_hour"
BUCKET_PHONE_DAY = "sms_phone_day"
BUCKET_SOURCE_HOUR = "sms_source_hour"
BUCKET_COUNTRY_DAY = "sms_country_day"

_MINUTE = 60
_HOUR = 3600
_DAY = 86400

_STORE_FAILED = "sms verification storage failed"
_PROVIDER_FAILED = "sms provider call failed"


class SmsVerificationError(RuntimeError):
    """数据库或服务商调用失败。消息固定，不含号码、验证码、令牌与来源，也不挂原异常。"""


class SendOutcome(StrEnum):
    SENT = "sent"
    UNDELIVERABLE = "undeliverable"
    SUSPENDED = "suspended"
    SMS_DISABLED = "sms_disabled"
    NOT_WHITELISTED = "not_whitelisted"
    CAPTCHA_FAILED = "captcha_failed"
    RATE_LIMITED = "rate_limited"


class VerifyStatus(StrEnum):
    APPROVED = "approved"
    WRONG_CODE = "wrong_code"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"
    NO_PENDING = "no_pending"
    SMS_DISABLED = "sms_disabled"


@dataclass(frozen=True)
class VerifyResult:
    status: VerifyStatus
    # 通过时为被改为 approved 的验证记录 ID，其他结果为空。
    attempt_id: int | None = None


# ---------------------------------------------------------------- 发送


def send_verification(
    db: Session,
    settings: Settings,
    redis_client: redis.Redis | None,
    provider: SmsProvider,
    captcha: CaptchaVerifier,
    phone_e164: str,
    purpose: str,
    captcha_token: str | None,
    source: str,
    now: datetime,
) -> SendOutcome:
    """按顺序判定后发起短信验证，任一步不通过即停止：

    1. 当次读取的短信验证开关关闭：sms_disabled，之后各步都不做、不写记录。
    2. 号码不属于短信白名单：not_whitelisted，不写记录。
    3. 人机挑战：failed 为 captcha_failed，不写记录；unavailable 停发。
    4. Redis 限流：超限为 rate_limited，不写记录；客户端为 None 或不可用停发。
    5. 每日预算预占：拒绝即停发；成功即提交。
    6. 调用服务商，按结果写记录并结算或释放，一起提交。

    停发写 suspended 记录（不带请求 ID，已预占的释放）并提交，返回 suspended。
    号码须为规范化 E.164（否则抛 InvalidPhoneNumber）；开关开启时用途不在
    VERIFICATION_PURPOSES 里抛 ValueError（开关关闭时先返回 sms_disabled）。
    预占前或预占时 MySQL 出错、服务商抛出异常、第二个事务失败（含请求 ID 撞上
    唯一约束），都抛 SmsVerificationError 交给调用方。
    """
    try:
        return _send(
            db,
            settings,
            redis_client,
            provider,
            captcha,
            phone_e164,
            purpose,
            captcha_token,
            source,
            now,
        )
    except SQLAlchemyError:
        pass
    # 在 except 块之外抛出，不挂带着 SQL 参数（含完整号码）的原异常。
    raise SmsVerificationError(_STORE_FAILED)


def _send(
    db: Session,
    settings: Settings,
    redis_client: redis.Redis | None,
    provider: SmsProvider,
    captcha: CaptchaVerifier,
    phone_e164: str,
    purpose: str,
    captcha_token: str | None,
    source: str,
    now: datetime,
) -> SendOutcome:
    if not is_sms_verification_enabled(db):
        return SendOutcome.SMS_DISABLED
    _check_purpose(purpose)
    if not is_sms_whitelisted(phone_e164):
        return SendOutcome.NOT_WHITELISTED

    captcha_result = captcha.verify(captcha_token)
    if captcha_result is CaptchaResult.FAILED:
        return SendOutcome.CAPTCHA_FAILED
    if captcha_result is not CaptchaResult.PASSED:
        return _suspend(db, phone_e164, purpose, None, now)

    if redis_client is None:
        return _suspend(db, phone_e164, purpose, None, now)
    try:
        allowed = _hit_rate_limits(redis_client, settings, phone_e164, source)
    except RateLimitUnavailable:
        return _suspend(db, phone_e164, purpose, None, now)
    if not allowed:
        return SendOutcome.RATE_LIMITED

    reserved = reserve_sms(db, settings, phone_e164, now)
    if reserved.status != RESERVED or reserved.reservation is None:
        return _suspend(db, phone_e164, purpose, None, now)
    # 先提交预占：之后的步骤（含第二个事务的提交）抛出异常时，每日上限仍然计入。
    db.commit()
    reservation = reserved.reservation

    result = _start_verification(provider, phone_e164)
    if result.status is SendStatus.ACCEPTED and result.request_id is not None:
        return _record_provider_request(
            db, phone_e164, purpose, result, VERIFICATION_SENT, reservation, now
        )
    if result.status is SendStatus.UNDELIVERABLE:
        if result.request_id is None:
            _add_attempt(db, phone_e164, purpose, VERIFICATION_UNDELIVERABLE, None, now)
            release_sms(db, reservation)
            db.commit()
            return SendOutcome.UNDELIVERABLE
        return _record_provider_request(
            db, phone_e164, purpose, result, VERIFICATION_UNDELIVERABLE, reservation, now
        )
    # unavailable（受理却没有请求 ID 也按此处理，记录不能没有请求 ID 而为 sent）。
    return _suspend(db, phone_e164, purpose, reservation, now)


def _start_verification(provider: SmsProvider, phone_e164: str) -> SendResult:
    """调用服务商发起验证；抛出的异常换成不挂原异常的 SmsVerificationError。"""
    try:
        return provider.start_verification(phone_e164)
    except Exception:
        pass
    raise SmsVerificationError(_PROVIDER_FAILED)


def _check_verification(provider: SmsProvider, request_id: str, code: str) -> CheckStatus:
    """按请求 ID 核验；抛出的异常换成不挂原异常的 SmsVerificationError。"""
    try:
        return provider.check_verification(request_id, code)
    except Exception:
        pass
    raise SmsVerificationError(_PROVIDER_FAILED)


def _hit_rate_limits(client: redis.Redis, settings: Settings, phone_e164: str, source: str) -> bool:
    """按号码 60 秒、1 小时、24 小时，来源 1 小时，国家呼叫码 24 小时的顺序逐桶计数。

    第一个超限的桶即返回 False，其后的桶不再计数；已计入的桶不回退。
    Redis 不可用时抛 RateLimitUnavailable。
    """
    country_code = str(normalize_phone(phone_e164, "MY").country_code)
    buckets = (
        (BUCKET_PHONE_MINUTE, phone_e164, settings.sms_phone_limit_per_minute, _MINUTE),
        (BUCKET_PHONE_HOUR, phone_e164, settings.sms_phone_limit_per_hour, _HOUR),
        (BUCKET_PHONE_DAY, phone_e164, settings.sms_phone_limit_per_day, _DAY),
        (BUCKET_SOURCE_HOUR, source, settings.sms_source_limit_per_hour, _HOUR),
        (BUCKET_COUNTRY_DAY, country_code, settings.sms_daily_count_limit, _DAY),
    )
    for bucket, identifier, limit, window in buckets:
        if not hit(client, bucket, identifier, limit, window):
            return False
    return True


def _record_provider_request(
    db: Session,
    phone_e164: str,
    purpose: str,
    result: SendResult,
    status: str,
    reservation: Reservation,
    now: datetime,
) -> SendOutcome:
    """服务商带回请求 ID（accepted 为 sent，undeliverable 为 undeliverable）时写记录并结算。

    请求 ID 全表唯一。没有该请求 ID 的记录时新增一条。Twilio Verify 对同一号码仍待核验的验证
    再次发起时沿用原请求 ID：该记录属于同一号码且仍为 sent 时，以条件更新（仅当仍为 sent）
    改为本次的状态、用途与更新时间，不新增记录。记录已结束（含条件更新时已被改掉的）或属于
    另一个号码时原记录一律不改，按服务商不可用停发（Kelvin 2026-10-09 决定）。
    """
    request_id = result.request_id
    stmt = select(VerificationAttempt.id, VerificationAttempt.phone, VerificationAttempt.status)
    stmt = stmt.where(VerificationAttempt.provider_request_id == request_id)
    existing = db.execute(stmt).one_or_none()
    if existing is None:
        _add_attempt(db, phone_e164, purpose, status, request_id, now)
    elif existing.phone != phone_e164 or existing.status != VERIFICATION_SENT:
        return _suspend(db, phone_e164, purpose, reservation, now)
    else:
        changed = db.execute(
            update(VerificationAttempt)
            .where(
                VerificationAttempt.id == existing.id,
                VerificationAttempt.status == VERIFICATION_SENT,
            )
            .values(status=status, purpose=purpose, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            return _suspend(db, phone_e164, purpose, reservation, now)
    settle_sms(db, reservation)
    db.commit()
    if status == VERIFICATION_SENT:
        return SendOutcome.SENT
    return SendOutcome.UNDELIVERABLE


def _suspend(
    db: Session,
    phone_e164: str,
    purpose: str,
    reservation: Reservation | None,
    now: datetime,
) -> SendOutcome:
    """停发：写不带请求 ID 的 suspended 记录，有预占时释放（不计入每日上限），然后提交。"""
    _add_attempt(db, phone_e164, purpose, VERIFICATION_SUSPENDED, None, now)
    if reservation is not None:
        release_sms(db, reservation)
    db.commit()
    return SendOutcome.SUSPENDED


def _add_attempt(
    db: Session,
    phone_e164: str,
    purpose: str,
    status: str,
    request_id: str | None,
    now: datetime,
) -> None:
    db.add(
        VerificationAttempt(
            phone=phone_e164,
            purpose=purpose,
            status=status,
            provider_request_id=request_id,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()


# ---------------------------------------------------------------- 核验


def verify_code(
    db: Session,
    provider: SmsProvider,
    phone_e164: str,
    purpose: str,
    code: str,
    now: datetime,
) -> VerifyResult:
    """核验该号码与用途最近一次待核验的验证。只 flush、不提交。

    1. 当次从数据库读取的开关关闭：sms_disabled，不调用服务商。
    2. 验证码不是 4 到 10 位数字：wrong_code，不调用服务商。
    3. 找 created_at 在 [now - 10 分钟, now] 内（两端都含）、状态为 sent 的最新记录，没有即
       no_pending。按创建时间而不是更新时间：沿用请求 ID 的重发不延长有效期。
    4. 按其请求 ID 调用服务商：approved 改为 approved、expired 改为 rejected，都以条件更新
       （仅当该记录仍为 sent 且用途仍是读取时的用途），更新不到即 no_pending；wrong_code 与
       unavailable 不改记录。

    开关开启时用途不在 VERIFICATION_PURPOSES 里抛 ValueError（开关关闭时先返回
    sms_disabled）；数据库出错或服务商抛出异常时抛
    SmsVerificationError。
    """
    try:
        return _verify(db, provider, phone_e164, purpose, code, now)
    except SQLAlchemyError:
        pass
    # 在 except 块之外抛出，不挂带着 SQL 参数（含完整号码）的原异常。
    raise SmsVerificationError(_STORE_FAILED)


def _verify(
    db: Session,
    provider: SmsProvider,
    phone_e164: str,
    purpose: str,
    code: str,
    now: datetime,
) -> VerifyResult:
    if not is_sms_verification_enabled(db):
        return VerifyResult(VerifyStatus.SMS_DISABLED)
    _check_purpose(purpose)
    if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
        return VerifyResult(VerifyStatus.WRONG_CODE)

    pending = db.execute(
        select(VerificationAttempt.id, VerificationAttempt.provider_request_id)
        .where(
            VerificationAttempt.phone == phone_e164,
            VerificationAttempt.purpose == purpose,
            VerificationAttempt.status == VERIFICATION_SENT,
            VerificationAttempt.created_at >= now - CODE_WINDOW,
            VerificationAttempt.created_at <= now,
        )
        .order_by(VerificationAttempt.created_at.desc(), VerificationAttempt.id.desc())
        .limit(1)
    ).first()
    if pending is None or pending.provider_request_id is None:
        return VerifyResult(VerifyStatus.NO_PENDING)

    checked = _check_verification(provider, pending.provider_request_id, code)
    if checked is CheckStatus.APPROVED:
        if not _finish(db, pending.id, purpose, VERIFICATION_APPROVED, now):
            return VerifyResult(VerifyStatus.NO_PENDING)
        return VerifyResult(VerifyStatus.APPROVED, pending.id)
    if checked is CheckStatus.EXPIRED:
        if not _finish(db, pending.id, purpose, VERIFICATION_REJECTED, now):
            return VerifyResult(VerifyStatus.NO_PENDING)
        return VerifyResult(VerifyStatus.EXPIRED)
    if checked is CheckStatus.WRONG_CODE:
        return VerifyResult(VerifyStatus.WRONG_CODE)
    return VerifyResult(VerifyStatus.UNAVAILABLE)


def _finish(db: Session, attempt_id: int, purpose: str, status: str, now: datetime) -> bool:
    """仅当记录仍为 sent 且用途未被另一次发送改写时改为结束状态；返回是否改到。"""
    changed = db.execute(
        update(VerificationAttempt)
        .where(
            VerificationAttempt.id == attempt_id,
            VerificationAttempt.status == VERIFICATION_SENT,
            VerificationAttempt.purpose == purpose,
        )
        .values(status=status, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return changed.rowcount == 1


def _check_purpose(purpose: object) -> None:
    if purpose not in VERIFICATION_PURPOSES:
        raise ValueError("purpose must be a verification purpose")

"""短信验证的发送与核验规则：开关、白名单、人机挑战、限流、预算与验证记录。

依据 docs/DESIGN.md 1.11（提交 2d13250）「边界与原则」第 3、4 条
（白名单号码无法送达或停发时允许改为游客下单；发送短信与提交验证码时
按当时读取的开关值判定）与「失败、并发与重试」第 2 到 5 条（验证码错误
不降级；开关关闭时在人机挑战、服务商与预算之前拒绝，不写记录、不计限流
与预算；按号码、来源、国家及全站限流，Redis 或 MySQL 不可用时停发；
各用途共用同一白名单、人机挑战、限流和每日预算），以及 docs/HANDOFF.md
0.41 记录的 Kelvin 2026-10-08 决定（标准档限流；按国家限流取全站每日
上限；核验只认 10 分钟内的验证；人机挑战服务本身不可用按停发，访客未
通过人机挑战不降级）。

组合 SHOP-TASK-067 的适配器（sms_provider.py、captcha.py）、068 的
每日预算（sms_budget.py）与 026 的 Redis 限流（rate_limit.py），
写 SHOP-TASK-012 的 verification_attempts。

发送 send_verification 按顺序判定，任一步不通过即停止：
1. 开关关闭：sms_disabled。
2. 号码不在短信白名单：not_whitelisted。
3. 人机挑战 failed：captcha_failed；服务本身 unavailable：停发。
4. Redis 限流：超限为 rate_limited；客户端为 None 或抛
   RateLimitUnavailable：停发。
5. 每日预算预占：unknown_cost、over_budget 停发；预占成功即先提交，
   之后的步骤失败时每日上限仍然计入。预占前或预占时 MySQL 出错，
   异常原样交给调用方（不调用服务商、不写记录）。
6. 调用服务商：accepted 为 sent（结算）；带请求 ID 的 undeliverable
   为 undeliverable（结算）；不带请求 ID 的 undeliverable（释放）；
   unavailable 停发（释放）。
停发写状态 suspended、不带请求 ID 的记录；sms_disabled、
not_whitelisted、captcha_failed 与 rate_limited 不写记录。
写记录与结算或释放在第二个事务里一起提交；它提交失败时异常交给
调用方，已提交的预占保留为 reserved（保守计入每日上限）。
Twilio Verify 对同一号码仍待核验的验证再次发起时沿用原请求 ID：
不新增记录，把那条记录改为本次的状态、用途与更新时间，创建时间不变
（之前用途的待核验随之作废）。

核验 check_verification：开关关闭为 sms_disabled；验证码不是 4 到 10 位
数字为 wrong_code；找该号码与用途创建于最近 10 分钟内、状态为 sent 的
最新记录（按创建时间：沿用请求 ID 的重发不延长有效期），没有为
no_pending；再按请求 ID 调用服务商。approved 以条件更新（仍为 sent 且
用途未变）改为 approved，更新不到为 no_pending；expired 改为 rejected；
wrong_code 与 unavailable 不改记录。只 flush、不提交。

本模块不写日志；手机号、验证码、令牌与来源不出现在异常消息与返回值里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import redis
from sqlalchemy import select, update
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
from app.services.phone import SMS_WHITELIST_COUNTRY_CODES, is_sms_whitelisted
from app.services.rate_limit import RateLimitUnavailable, hit
from app.services.site_settings import is_sms_verification_enabled
from app.services.sms_budget import RESERVED, Reservation, release_sms, reserve_sms, settle_sms
from app.services.sms_provider import CheckStatus, SendStatus, SmsProvider

# 核验只认创建于最近 10 分钟内的验证
# （Twilio Verify 验证码的默认有效期，Kelvin 2026-10-08）。
CHECK_WINDOW = timedelta(minutes=10)

MINUTE_SECONDS = 60
HOUR_SECONDS = 60 * 60
DAY_SECONDS = 24 * 60 * 60

# 限流桶名，各不相同；号码与来源经 rate_limit_key 只以摘要进 Redis。
BUCKET_PHONE_MINUTE = "sms_phone_minute"
BUCKET_PHONE_HOUR = "sms_phone_hour"
BUCKET_PHONE_DAY = "sms_phone_day"
BUCKET_SOURCE_HOUR = "sms_source_hour"
BUCKET_COUNTRY_DAY = "sms_country_day"

# Twilio Verify 的验证码长度可设 4–10 位，只含 ASCII 数字。
_CODE_PATTERN = re.compile(r"[0-9]{4,10}")


class SendOutcomeStatus(StrEnum):
    SMS_DISABLED = "sms_disabled"
    NOT_WHITELISTED = "not_whitelisted"
    CAPTCHA_FAILED = "captcha_failed"
    RATE_LIMITED = "rate_limited"
    SENT = "sent"
    UNDELIVERABLE = "undeliverable"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class SendOutcome:
    status: SendOutcomeStatus
    # 写入或改写的验证记录 ID；不写记录的结果为空。
    attempt_id: int | None = None


class CheckOutcomeStatus(StrEnum):
    SMS_DISABLED = "sms_disabled"
    WRONG_CODE = "wrong_code"
    NO_PENDING = "no_pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CheckOutcome:
    status: CheckOutcomeStatus
    # 只在 approved 时有：通过核验的验证记录 ID。
    attempt_id: int | None = None


# ---------------------------------------------------------------- 发送


def send_verification(
    db: Session,
    settings: Settings,
    provider: SmsProvider,
    captcha: CaptchaVerifier,
    redis_client: redis.Redis | None,
    *,
    phone_e164: str,
    purpose: str,
    captcha_token: str | None,
    source: str,
    now: datetime,
) -> SendOutcome:
    """按模块说明的顺序判定并发起短信验证。

    phone_e164 须是 app/services/phone.py 规范化的 E.164（否则抛
    InvalidPhoneNumber）；purpose 须是 VERIFICATION_PURPOSES 之一
    （否则抛 ValueError）；source 是 rate_limit.client_source 的结果；
    now 是不带时区的 UTC。redis_client 为 None 表示未配置或连不上。
    """
    _check_purpose(purpose)
    if not is_sms_verification_enabled(db):
        return SendOutcome(SendOutcomeStatus.SMS_DISABLED)
    if not is_sms_whitelisted(phone_e164):
        return SendOutcome(SendOutcomeStatus.NOT_WHITELISTED)

    captcha_result = captcha.verify(captcha_token)
    if captcha_result is CaptchaResult.FAILED:
        return SendOutcome(SendOutcomeStatus.CAPTCHA_FAILED)
    if captcha_result is not CaptchaResult.PASSED:
        return _suspend(db, phone_e164, purpose, now, None)

    if redis_client is None:
        return _suspend(db, phone_e164, purpose, now, None)
    try:
        within_limits = _hit_limits(redis_client, settings, phone_e164, source)
    except RateLimitUnavailable:
        return _suspend(db, phone_e164, purpose, now, None)
    if not within_limits:
        return SendOutcome(SendOutcomeStatus.RATE_LIMITED)

    reserved = reserve_sms(db, settings, phone_e164, now)
    if reserved.status != RESERVED or reserved.reservation is None:
        return _suspend(db, phone_e164, purpose, now, None)
    reservation = reserved.reservation
    # 第一个事务：预占先提交，之后的步骤失败时每日上限仍然计入。
    db.commit()

    result = provider.start_verification(phone_e164)
    accepted = result.status is SendStatus.ACCEPTED
    if result.request_id and (accepted or result.status is SendStatus.UNDELIVERABLE):
        status = VERIFICATION_SENT if accepted else VERIFICATION_UNDELIVERABLE
        attempt_id = _record_with_request_id(
            db, phone_e164, purpose, status, result.request_id, now
        )
        settle_sms(db, reservation)
        outcome = SendOutcome(SendOutcomeStatus(status), attempt_id)
    elif result.status is SendStatus.UNDELIVERABLE:
        attempt_id = _insert(db, phone_e164, purpose, VERIFICATION_UNDELIVERABLE, None, now)
        release_sms(db, reservation)
        outcome = SendOutcome(SendOutcomeStatus.UNDELIVERABLE, attempt_id)
    else:
        # unavailable（以及适配器不会给出的、没有请求 ID 的 accepted）：停发。
        return _suspend(db, phone_e164, purpose, now, reservation)
    # 第二个事务：记录与结算或释放一起提交。
    db.commit()
    return outcome


def _check_purpose(purpose: object) -> None:
    if purpose not in VERIFICATION_PURPOSES:
        raise ValueError("purpose must be a verification purpose")


def _country_calling_code(phone_e164: str) -> str:
    """白名单号码的国家呼叫码文本（如 "60"）。

    国家呼叫码是前缀码，按开头判定不会误认。
    """
    for code in sorted(SMS_WHITELIST_COUNTRY_CODES):
        if phone_e164.startswith(f"+{code}"):
            return str(code)
    raise ValueError("phone number must be in the sms whitelist")


def _hit_limits(client: redis.Redis, settings: Settings, phone_e164: str, source: str) -> bool:
    """逐桶计数：号码 60 秒、号码 1 小时、号码 24 小时、来源 1 小时、
    国家呼叫码 24 小时。

    第一个超限的桶即返回 False，其后的桶不再计数，已计入的桶不回退。
    Redis 不可用时抛 RateLimitUnavailable。
    """
    country = _country_calling_code(phone_e164)
    buckets = (
        (BUCKET_PHONE_MINUTE, phone_e164, settings.sms_phone_limit_per_minute, MINUTE_SECONDS),
        (BUCKET_PHONE_HOUR, phone_e164, settings.sms_phone_limit_per_hour, HOUR_SECONDS),
        (BUCKET_PHONE_DAY, phone_e164, settings.sms_phone_limit_per_day, DAY_SECONDS),
        (BUCKET_SOURCE_HOUR, source, settings.sms_source_limit_per_hour, HOUR_SECONDS),
        (BUCKET_COUNTRY_DAY, country, settings.sms_daily_count_limit, DAY_SECONDS),
    )
    for bucket, identifier, limit, window_seconds in buckets:
        if not hit(client, bucket, identifier, limit, window_seconds):
            return False
    return True


def _suspend(
    db: Session,
    phone_e164: str,
    purpose: str,
    now: datetime,
    reservation: Reservation | None,
) -> SendOutcome:
    """停发：写 suspended 记录（不带请求 ID），已预占的释放，一起提交。"""
    attempt_id = _insert(db, phone_e164, purpose, VERIFICATION_SUSPENDED, None, now)
    if reservation is not None:
        release_sms(db, reservation)
    db.commit()
    return SendOutcome(SendOutcomeStatus.SUSPENDED, attempt_id)


def _insert(
    db: Session,
    phone_e164: str,
    purpose: str,
    status: str,
    request_id: str | None,
    now: datetime,
) -> int:
    attempt = VerificationAttempt(
        phone=phone_e164,
        purpose=purpose,
        status=status,
        provider_request_id=request_id,
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    db.flush()
    return attempt.id


def _record_with_request_id(
    db: Session,
    phone_e164: str,
    purpose: str,
    status: str,
    request_id: str,
    now: datetime,
) -> int:
    """带请求 ID 的结果：写记录或改写已有的那条。

    该号码已有同一请求 ID 的记录（提供方沿用了仍待核验的验证）时，
    改写它的状态、用途与更新时间，创建时间不变；否则新增一条。
    """
    existing = db.scalars(
        select(VerificationAttempt).where(
            VerificationAttempt.provider_request_id == request_id,
            VerificationAttempt.phone == phone_e164,
        )
    ).one_or_none()
    if existing is None:
        return _insert(db, phone_e164, purpose, status, request_id, now)
    existing.status = status
    existing.purpose = purpose
    existing.updated_at = now
    db.flush()
    return existing.id


# ---------------------------------------------------------------- 核验


def check_verification(
    db: Session,
    provider: SmsProvider,
    *,
    phone_e164: str,
    purpose: str,
    code: str,
    now: datetime,
) -> CheckOutcome:
    """按模块说明核验验证码。只 flush、不提交，由调用方提交或回滚。

    purpose 须是 VERIFICATION_PURPOSES 之一（否则抛 ValueError）；
    now 是不带时区的 UTC。
    """
    _check_purpose(purpose)
    if not is_sms_verification_enabled(db):
        return CheckOutcome(CheckOutcomeStatus.SMS_DISABLED)
    if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
        return CheckOutcome(CheckOutcomeStatus.WRONG_CODE)

    pending = db.execute(
        select(VerificationAttempt.id, VerificationAttempt.provider_request_id)
        .where(
            VerificationAttempt.phone == phone_e164,
            VerificationAttempt.purpose == purpose,
            VerificationAttempt.status == VERIFICATION_SENT,
            VerificationAttempt.created_at >= now - CHECK_WINDOW,
        )
        .order_by(VerificationAttempt.created_at.desc(), VerificationAttempt.id.desc())
        .limit(1)
    ).first()
    if pending is None or pending.provider_request_id is None:
        return CheckOutcome(CheckOutcomeStatus.NO_PENDING)

    result = provider.check_verification(pending.provider_request_id, code)
    if result is CheckStatus.APPROVED:
        if _finish(db, pending.id, purpose, VERIFICATION_APPROVED, now):
            return CheckOutcome(CheckOutcomeStatus.APPROVED, pending.id)
        return CheckOutcome(CheckOutcomeStatus.NO_PENDING)
    if result is CheckStatus.EXPIRED:
        _finish(db, pending.id, purpose, VERIFICATION_REJECTED, now)
        return CheckOutcome(CheckOutcomeStatus.EXPIRED)
    if result is CheckStatus.WRONG_CODE:
        return CheckOutcome(CheckOutcomeStatus.WRONG_CODE)
    return CheckOutcome(CheckOutcomeStatus.UNAVAILABLE)


def _finish(db: Session, attempt_id: int, purpose: str, status: str, now: datetime) -> bool:
    """条件更新：仅当该记录仍为 sent 且用途仍是读取时的用途。

    返回是否更新到。
    """
    result = db.execute(
        update(VerificationAttempt)
        .where(
            VerificationAttempt.id == attempt_id,
            VerificationAttempt.status == VERIFICATION_SENT,
            VerificationAttempt.purpose == purpose,
        )
        .values(status=status, updated_at=now)
    )
    db.flush()
    return result.rowcount == 1

"""会员会话、CSRF 令牌、密码校验与密码登录锁定。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：「未设密码的账号只能短信
登录，密码登录对其与对错误密码返回相同的通用失败，不暴露账号是否存在或是否设了密码」「密码
至少 8 位、不强制复杂度，安全哈希存储；会话到期、退出与服务端授权检查」；第 6 条：应用日志
不记录完整电话与密码。以及 docs/HANDOFF.md 0.41 记录的 Kelvin 2026-10-08 决定（3）：「会员会话
30 天、不随使用延长；同一号码加来源 15 分钟内密码登录失败 5 次即锁 15 分钟，失败一律通用提示；
重设密码与注销时撤销该会员的全部会话」。表为 SHOP-TASK-012 的 Member 与 MemberSession。
本模块只提供规则函数，cookie 与接口留给会员登录接口任务。

会员会话：cookie 里是令牌原文（secrets 生成的 32 字节随机数），库里只存它的 SHA-256 十六进制
摘要。到期时间为签发时刻加 30 天，不随使用延长。只给 active 会员签发；会话不存在、已撤销、
已到期或会员已不是 active（已注销）都不通过。
CSRF 令牌不入库，由令牌原文加本模块的用途前缀做 SHA-256 得到；前缀不同于后台会话
（app/services/admin_auth.py）与订单访问（app/services/order_access.py），同一令牌原文在三处
得到的 CSRF 令牌都不同。签发与撤销都只 flush、不提交，由调用方提交。时间一律是不带时区的 UTC。

密码校验（app/services/pw_hash.py）：按规范化 E.164 号码找 active 会员；号码未注册或未设密码时
以 DUMMY_PASSWORD_HASH 照样校验一次，三种失败（号码未注册、未设密码、密码错误）对调用方都是
None，且都恰好跑一次 scrypt。密码长度规则（至少 8 个字符）由 password_length_ok 提供给之后的
设置与重设密码任务；登录时不按长度预先拒绝，长度不足的密码照常校验、照常不通过。

登录锁定（计数用 SHOP-TASK-026 的 app/services/rate_limit.py）：标识为
[访客来源, 规范化 E.164 号码] 的 JSON 编码；不用分隔符拼接，因为 IPv6 来源含冒号。号码由调用方
先经 app/services/phone.py 规范化，本模块只检查它是加号加 1 到 15 位数字，不合即抛 ValueError。
同一来源与号码 15 分钟窗口内累计失败满 5 次即锁定 15 分钟：

- 按来源与号码组合计数、组合锁定：只按号码锁定时，任何人从任何来源对某个号码连续输错就能把
  该会员的密码登录锁住；组合锁定只锁住攻击者自己的来源。
- 成功登录不清零失败计数：SHOP-TASK-026 只提供自增与读取，窗口从第一次失败算起，
  15 分钟窗口内累计满 5 次即锁，中间夹着成功登录也照样累计。
- 锁定期间即使密码正确也拒绝；锁定到期后照常校验密码，不因锁定解除而放行。
- Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录，不得放行。

接口层的一次密码登录按 password_login 的顺序：login_locked 为真即拒绝（不校验密码、不计失败）；
否则 authenticate_member；不通过则 record_login_failure；通过则调用方签发会话并提交。
锁定与密码错误对访客都是同一个通用失败（Kelvin 2026-10-08「失败一律通用提示」）。

本模块不写日志；令牌、CSRF 令牌、cookie 值、号码、来源与密码不出现在异常消息或 repr 里。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypeGuard

import redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.member import MEMBER_ACTIVE, Member, MemberSession
from app.services import pw_hash
from app.services.rate_limit import current_count, hit

SESSION_LIFETIME = timedelta(days=30)

# secrets.token_urlsafe(32)：32 字节（256 位）随机数的无填充 Base64url，恰好 43 个字符。
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")

# CSRF 令牌的用途前缀，与后台会话、订单访问的前缀都不同。
_CSRF_PREFIX = b"acuven-shop/member-session/csrf\x00"

# 设计「密码至少 8 位」：按字符计。
PASSWORD_MIN_LENGTH = 8

# 规范化 E.164：加号加 1 到 15 位数字（app/services/phone.py 的产出）。
_E164_PATTERN = re.compile(r"\+[0-9]{1,15}")

# 登录锁定（Kelvin 2026-10-08）：失败计数窗口 15 分钟、上限 4，计数超过上限（即窗口内第 5 次
# 失败）时在锁定桶计一次；锁定桶上限 1、窗口 15 分钟，过期只在首次计入时设置。
FAILURE_BUCKET = "member_login_failures"
FAILURE_LIMIT = 4
FAILURE_WINDOW_SECONDS = 900
LOCK_BUCKET = "member_login_lock"
LOCK_LIMIT = 1
LOCK_WINDOW_SECONDS = 900


@dataclass(frozen=True)
class IssuedMemberSession:
    """签发结果：token 交给 cookie，csrf_token 交给页面，session 是刚写入的会话记录。"""

    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    session: MemberSession = field(repr=False)

    @property
    def expires_at(self) -> datetime:
        return self.session.expires_at


@dataclass(frozen=True)
class CheckedMemberSession:
    """校验通过的结果：会话记录与所属的 active 会员。"""

    session: MemberSession = field(repr=False)
    member: Member = field(repr=False)


def _is_well_formed(cookie_value: str | None) -> TypeGuard[str]:
    return isinstance(cookie_value, str) and _TOKEN_PATTERN.fullmatch(cookie_value) is not None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf_for(token: str) -> str:
    return hashlib.sha256(_CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _require_naive(now: datetime) -> None:
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")


# ---------------------------------------------------------------------------
# 会员会话
# ---------------------------------------------------------------------------


def issue_member_session(db: Session, member: Member, now: datetime) -> IssuedMemberSession:
    """给 active 会员签发一个新会话：到期时间为 now 加 30 天，之后不随使用延长。

    会员不是 active（已注销）时抛 ValueError、不写库。只 flush、不提交：调用方在登录成功的
    同一事务里提交。
    """
    _require_naive(now)
    if member.status != MEMBER_ACTIVE:
        raise ValueError("member is not active")
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session = MemberSession(
        member_id=member.id,
        token_hash=_token_hash(token),
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
        revoked_at=None,
    )
    db.add(session)
    db.flush()
    return IssuedMemberSession(token=token, csrf_token=_csrf_for(token), session=session)


def check_member_session(
    db: Session, cookie_value: str | None, now: datetime
) -> CheckedMemberSession | None:
    """按 cookie 值找有效的会员会话；不通过时为空。

    格式不合法的 cookie 值不查库。会话不存在、已撤销、已到期（到期时刻起即不通过）或所属会员
    已不是 active 都不通过。通过时返回会话与会员；退出时把 session 交给 revoke_member_session。
    本函数只读，不延长会话。
    """
    _require_naive(now)
    if not _is_well_formed(cookie_value):
        return None
    row = db.execute(
        select(MemberSession, Member)
        .join(Member, Member.id == MemberSession.member_id)
        .where(MemberSession.token_hash == _token_hash(cookie_value))
    ).one_or_none()
    if row is None:
        return None
    session, member = row
    if session.revoked_at is not None or session.expires_at <= now:
        return None
    if member.status != MEMBER_ACTIVE:
        return None
    return CheckedMemberSession(session=session, member=member)


def revoke_member_session(db: Session, session: MemberSession, now: datetime) -> None:
    """撤销一个会话（退出）：写撤销时间，行保留；已撤销的不改原撤销时间。只 flush、不提交。"""
    _require_naive(now)
    if session.revoked_at is None:
        session.revoked_at = now
        db.flush()


def revoke_all_member_sessions(db: Session, member_id: int, now: datetime) -> int:
    """撤销某会员全部尚未撤销的会话（重设密码与注销时），返回本次撤销的条数。

    写撤销时间，行保留；已撤销的保留原撤销时间。只 flush、不提交。
    """
    _require_naive(now)
    db.flush()
    result = db.execute(
        update(MemberSession)
        .where(
            MemberSession.member_id == member_id,
            MemberSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session="fetch")
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# CSRF 令牌
# ---------------------------------------------------------------------------


def csrf_token_for_cookie(cookie_value: str | None) -> str | None:
    """由 cookie 值重新算出 CSRF 令牌；cookie 值缺失或格式不合法时为空。

    不查库：会话是否有效由 check_member_session 判定。
    """
    if not _is_well_formed(cookie_value):
        return None
    return _csrf_for(cookie_value)


def check_csrf_token(cookie_value: str | None, header_value: str | None) -> bool:
    """请求头里的 CSRF 令牌是否与由 cookie 值重新算出的值一致（常量时间比较）。

    令牌缺失、不一致或 cookie 值缺失、格式不合法都不通过。本函数不查库：会员写操作必须
    同时通过 check_member_session 与本函数。
    """
    expected = csrf_token_for_cookie(cookie_value)
    if expected is None or not isinstance(header_value, str) or not header_value:
        return False
    return hmac.compare_digest(
        header_value.encode("utf-8", "surrogatepass"),
        expected.encode("ascii"),
    )


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------


def password_length_ok(password: object) -> bool:
    """设置与重设密码时的长度规则：字符串且至少 8 个字符，不强制复杂度。"""
    return isinstance(password, str) and len(password) >= PASSWORD_MIN_LENGTH


def authenticate_member(db: Session, phone: str, password: str) -> Member | None:
    """按规范化 E.164 号码与密码校验 active 会员；通过时返回会员，否则为空。

    号码未注册（含已注销）、未设密码与密码错误三种失败都返回 None，且都恰好调用一次
    pw_hash.verify_password（前两种以 DUMMY_PASSWORD_HASH 校验），不暴露账号是否存在或是否
    设了密码。不查锁定、不计失败：由 password_login 或接口层处理。
    """
    member = db.scalars(
        select(Member).where(Member.phone == phone, Member.status == MEMBER_ACTIVE)
    ).one_or_none()
    encoded = None if member is None else member.password_hash
    if encoded is None:
        pw_hash.verify_password(password, pw_hash.DUMMY_PASSWORD_HASH)
        return None
    if not pw_hash.verify_password(password, encoded):
        return None
    return member


# ---------------------------------------------------------------------------
# 登录锁定
# ---------------------------------------------------------------------------


def login_identifier(source: str, phone: str) -> str:
    """锁定计数的标识：[访客来源, 规范化 E.164 号码] 的 JSON 编码。

    不用分隔符拼接：IPv6 来源含冒号。号码须已规范化（加号加 1 到 15 位数字），否则抛
    ValueError（消息不含号码）。标识只以摘要进 Redis（rate_limit_key）。
    """
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    if not isinstance(phone, str) or not _E164_PATTERN.fullmatch(phone):
        raise ValueError("phone must be a normalized E.164 number")
    return json.dumps([source, phone], ensure_ascii=True)


def login_locked(client: redis.Redis, source: str, phone: str) -> bool:
    """这个来源与号码现在是否被锁定。只读锁定桶，不自增。

    为真时调用方即使密码正确也拒绝登录；锁定桶过期（首次锁定起 15 分钟）后为假，调用方
    照常校验密码。Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录。
    """
    return current_count(client, LOCK_BUCKET, login_identifier(source, phone)) >= LOCK_LIMIT


def record_login_failure(client: redis.Redis, source: str, phone: str) -> bool:
    """记一次密码登录失败，返回这一次是否「刚锁定」。

    失败计数桶窗口 15 分钟、上限 4。计数超过上限（本次是窗口内第 5 次或之后的失败）时，
    在锁定桶以上限 1、窗口 15 分钟计一次：锁定桶的过期只在首次计入时设置，即从首次计入起
    锁 15 分钟，之后的计入不延长。只有这次锁定桶计数未超过上限时才返回 True，并发的失败
    因此也只有一个报告刚锁定。

    失败计数已计入而锁定桶计数时 Redis 出错的，下一次失败照样会计入锁定桶。
    Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录。
    """
    identifier = login_identifier(source, phone)
    if hit(client, FAILURE_BUCKET, identifier, FAILURE_LIMIT, FAILURE_WINDOW_SECONDS):
        return False
    return hit(client, LOCK_BUCKET, identifier, LOCK_LIMIT, LOCK_WINDOW_SECONDS)


def password_login(
    db: Session, client: redis.Redis, source: str, phone: str, password: str
) -> Member | None:
    """一次密码登录判定：锁定即拒绝；否则校验密码，不通过则计一次失败。

    通过时返回会员，由调用方签发会话并提交；锁定与三种密码失败都返回 None，对访客是同一个
    通用失败。锁定期间不校验密码、不计失败。成功不清零失败计数。
    Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录。
    """
    if login_locked(client, source, phone):
        return None
    member = authenticate_member(db, phone, password)
    if member is None:
        record_login_failure(client, source, phone)
    return member

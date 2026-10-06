"""管理员后台会话、CSRF 令牌、审计记录写入与登录锁定。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条：「管理员登录按来源与账号
组合限流，连续失败后短时锁定并告警；锁定解除不绕过密码校验。单一管理员也须服务端授权与会话
到期」；第 6 条：应用日志不记录密码。以及 docs/HANDOFF.md 记录的 Kelvin 2026-10-04 管理员
登录决定（2026-10-05 补充）。接口由 SHOP-TASK-036 交付，本模块只提供规则函数。

后台会话：cookie 里是令牌原文（secrets 生成的 32 字节随机数），库里只存它的 SHA-256 十六进制
摘要。到期时间为签发时刻加 30 天，不随使用延长。会话不存在、已撤销或已到期都不通过。
CSRF 令牌不入库，由令牌原文加本模块的用途前缀做 SHA-256 得到；前缀不同于订单访问
（app/services/order_access.py），同一令牌原文在两处得到的 CSRF 令牌也不同。
签发、撤销与写审计记录都只 flush、不提交，由调用方提交。时间一律是不带时区的 UTC。

登录锁定（计数用 SHOP-TASK-026 的 app/services/rate_limit.py）：标识为
[访客来源, 规范化用户名] 的 JSON 编码，规范化即去掉首尾空白、转小写；不用分隔符拼接，
因为 IPv6 来源含冒号。同一来源与用户名 1 小时窗口内累计失败满 10 次即锁定 1 小时：

- 按来源与用户名组合计数、组合锁定，而不是只按账号锁定：只按账号锁定时，攻击者从任何来源
  对唯一的管理员用户名连续输错就能把管理员锁在外面；组合锁定只锁住攻击者自己的来源。
- 成功登录不清零失败计数：SHOP-TASK-026 只提供自增与读取，窗口从第一次失败算起，
  1 小时窗口内累计满 10 次即锁，中间夹着成功登录也照样累计。
- 锁定期间即使密码正确也拒绝；锁定到期后照常校验密码，不因锁定解除而放行。
- 锁定告警在运营告警邮件接入后补，在那之前锁定只写审计记录（admin_login_locked）。
- Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录，不得放行。

接口层（SHOP-TASK-036）的一次登录建议按此顺序：login_locked 为真即拒绝（不校验密码、
不计失败）；否则按用户名找账号，账号不存在时以 pw_hash.DUMMY_PASSWORD_HASH 照样校验一次；
校验不通过则 record_login_failure，账号存在时写 admin_login_failed，返回「刚锁定」时
再写 admin_login_locked（用户名不存在的失败只计入锁定计数，不写审计）；通过则签发会话
并写 admin_login_succeeded。审计记录不存来源地址与提交的用户名。

本模块不写日志；令牌、CSRF 令牌、cookie 值、来源与用户名不出现在异常消息或 repr 里。
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

from app.models.admin import (
    ACTION_MAX_LENGTH,
    TARGET_TYPE_MAX_LENGTH,
    AdminSession,
    AuditEvent,
)
from app.services.rate_limit import current_count, hit

SESSION_LIFETIME = timedelta(days=30)

# secrets.token_urlsafe(32)：32 字节（256 位）随机数的无填充 Base64url，恰好 43 个字符。
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")

# CSRF 令牌的用途前缀，与订单访问的前缀不同。
_CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

# 审计操作名。之后的业务接口任务在这里增加，库里不按枚举约束。
ADMIN_LOGIN_SUCCEEDED = "admin_login_succeeded"
ADMIN_LOGIN_FAILED = "admin_login_failed"
ADMIN_LOGIN_LOCKED = "admin_login_locked"
ADMIN_LOGOUT = "admin_logout"
ADMIN_ACCOUNT_CREATED = "admin_account_created"
ADMIN_PASSWORD_RESET = "admin_password_reset"

_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

# 登录锁定（Kelvin 2026-10-04）：失败计数窗口 1 小时、上限 9，计数超过上限（即窗口内第 10 次
# 失败）时在锁定桶计一次；锁定桶上限 1、窗口 1 小时，过期只在首次计入时设置。
FAILURE_BUCKET = "admin_login_failures"
FAILURE_LIMIT = 9
FAILURE_WINDOW_SECONDS = 3600
LOCK_BUCKET = "admin_login_lock"
LOCK_LIMIT = 1
LOCK_WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class IssuedAdminSession:
    """签发结果：token 交给 cookie，csrf_token 交给页面，session 是刚写入的会话记录。"""

    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    session: AdminSession = field(repr=False)

    @property
    def expires_at(self) -> datetime:
        return self.session.expires_at


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
# 后台会话
# ---------------------------------------------------------------------------


def issue_admin_session(db: Session, admin_account_id: int, now: datetime) -> IssuedAdminSession:
    """给管理员签发一个新会话：到期时间为 now 加 30 天，之后不随使用延长。

    只 flush、不提交：调用方在登录成功的同一事务里写审计记录后提交。账号不存在时由外键在
    flush 时拒绝。
    """
    _require_naive(now)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    session = AdminSession(
        admin_account_id=admin_account_id,
        token_hash=_token_hash(token),
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
        revoked_at=None,
    )
    db.add(session)
    db.flush()
    return IssuedAdminSession(token=token, csrf_token=_csrf_for(token), session=session)


def check_admin_session(
    db: Session, cookie_value: str | None, now: datetime
) -> AdminSession | None:
    """按 cookie 值找有效的后台会话；不通过时为空。

    格式不合法的 cookie 值不查库。会话不存在、已撤销或已到期（到期时刻起即不通过）都不通过。
    通过时返回会话记录：所属账号为 admin_account_id，到期时间为 expires_at；撤销当前会话
    时直接把它交给 revoke_admin_session，不必再查令牌摘要。本函数只读，不延长会话。
    """
    _require_naive(now)
    if not _is_well_formed(cookie_value):
        return None
    session = db.scalars(
        select(AdminSession).where(AdminSession.token_hash == _token_hash(cookie_value))
    ).one_or_none()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return None
    return session


def revoke_admin_session(db: Session, session: AdminSession, now: datetime) -> None:
    """撤销一个会话（退出）：写撤销时间，行保留；已撤销的不改原撤销时间。只 flush、不提交。"""
    _require_naive(now)
    if session.revoked_at is None:
        session.revoked_at = now
        db.flush()


def revoke_all_admin_sessions(db: Session, admin_account_id: int, now: datetime) -> int:
    """撤销某账号全部尚未撤销的会话（重设密码时），返回本次撤销的条数。只 flush、不提交。"""
    _require_naive(now)
    db.flush()
    result = db.execute(
        update(AdminSession)
        .where(
            AdminSession.admin_account_id == admin_account_id,
            AdminSession.revoked_at.is_(None),
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

    不查库：会话是否有效由 check_admin_session 判定。
    """
    if not _is_well_formed(cookie_value):
        return None
    return _csrf_for(cookie_value)


def check_csrf_token(cookie_value: str | None, header_value: str | None) -> bool:
    """请求头里的 CSRF 令牌是否与由 cookie 值重新算出的值一致（常量时间比较）。

    令牌缺失、不一致或 cookie 值缺失、格式不合法都不通过。本函数不查库：后台写操作必须
    同时通过 check_admin_session 与本函数。
    """
    expected = csrf_token_for_cookie(cookie_value)
    if expected is None or not isinstance(header_value, str) or not header_value:
        return False
    return hmac.compare_digest(
        header_value.encode("utf-8", "surrogatepass"),
        expected.encode("ascii"),
    )


# ---------------------------------------------------------------------------
# 审计记录
# ---------------------------------------------------------------------------


def record_audit(
    db: Session,
    action: str,
    admin_account_id: int,
    now: datetime,
    *,
    target_type: str | None = None,
    target_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
) -> AuditEvent:
    """写一条审计记录，只 flush、不提交。

    action 取本模块的操作名常量；对象类别与对象 ID 要么都给、要么都不给。本任务的操作都不带
    旧值与新值。不存来源地址、提交的用户名、密码或其他个人资料，旧值与新值不含个人资料
    由调用方保证。操作名或对象不合规是编程错误，抛 ValueError、不写库。
    """
    _require_naive(now)
    if (
        not isinstance(action, str)
        or len(action) > ACTION_MAX_LENGTH
        or not _NAME_PATTERN.fullmatch(action)
    ):
        raise ValueError("audit action must be a lowercase constant name")
    if (target_type is None) != (target_id is None):
        raise ValueError("audit target type and id must be given together")
    if target_type is not None and (
        len(target_type) > TARGET_TYPE_MAX_LENGTH or not _NAME_PATTERN.fullmatch(target_type)
    ):
        raise ValueError("audit target type must be a lowercase constant name")
    event = AuditEvent(
        occurred_at=now,
        admin_account_id=admin_account_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(event)
    db.flush()
    return event


# ---------------------------------------------------------------------------
# 登录锁定
# ---------------------------------------------------------------------------


def normalize_username(username: str) -> str:
    """规范化提交的用户名：去掉首尾空白、转小写。锁定标识与按用户名找账号都用它。"""
    return username.strip().lower()


def login_identifier(source: str, username: str) -> str:
    """锁定计数的标识：[访客来源, 规范化用户名] 的 JSON 编码。

    不用分隔符拼接：IPv6 来源含冒号，「2001:db8::1」加「beef:admin」与「2001:db8::1:beef」
    加「admin」拼接后相同，JSON 数组则不同。标识只以摘要进 Redis（rate_limit_key）。
    """
    return json.dumps([source, normalize_username(username)], ensure_ascii=True)


def login_locked(client: redis.Redis, source: str, username: str) -> bool:
    """这个来源与用户名现在是否被锁定。只读锁定桶，不自增。

    为真时调用方即使密码正确也拒绝登录；锁定桶过期（首次锁定起 1 小时）后为假，调用方
    照常校验密码。Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录。
    """
    return current_count(client, LOCK_BUCKET, login_identifier(source, username)) >= LOCK_LIMIT


def record_login_failure(client: redis.Redis, source: str, username: str) -> bool:
    """记一次登录失败，返回这一次是否「刚锁定」。

    失败计数桶窗口 1 小时、上限 9。计数超过上限（本次是窗口内第 10 次或之后的失败）时，
    在锁定桶以上限 1、窗口 1 小时计一次：锁定桶的过期只在首次计入时设置，即从首次计入起
    锁 1 小时，之后的计入不延长。只有这次锁定桶计数未超过上限（本次是第一个计入锁定桶的）
    时才返回 True，并发的失败因此也只有一个报告刚锁定。

    失败计数已计入而锁定桶计数时 Redis 出错的，下一次失败照样会计入锁定桶。
    Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝登录。
    """
    identifier = login_identifier(source, username)
    if hit(client, FAILURE_BUCKET, identifier, FAILURE_LIMIT, FAILURE_WINDOW_SECONDS):
        return False
    return hit(client, LOCK_BUCKET, identifier, LOCK_LIMIT, LOCK_WINDOW_SECONDS)

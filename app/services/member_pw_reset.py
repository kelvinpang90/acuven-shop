"""会员重设密码：一次性重设凭据、CSRF 令牌与重设密码规则。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：「已有密码的重设再次短信
验证」「密码至少 8 位、不强制复杂度，安全哈希存储」；docs/HANDOFF.md 0.41 记录的 Kelvin
2026-10-08 决定（3）：「重设密码与注销时撤销该会员的全部会话」；以及 0.44 记录的 Kelvin
2026-10-10 决定：短信验证通过且号码已注册时，服务端签发一次性重设凭据，存于共享 Redis 为本项目
分配的独立库编号，10 分钟有效、使用一次即删除，提交新密码另须 CSRF 令牌；Redis 不可用时重设
暂停，凭据丢失只需重新验证。本模块只提供规则函数，接口与 cookie 留给 SHOP-TASK-079。

重设凭据：原文为 secrets.token_urlsafe(32)（43 个字符），交给 cookie；Redis 里的键为固定前缀加
原文的 SHA-256 十六进制摘要，原文不进 Redis，值为会员 ID。以一条带 600 秒过期的 SET 写入，
不分成 SET 与 EXPIRE 两步：两步之间出错会留下没有期限的凭据。
取用：在一个 MULTI 事务里 GET 与 DEL，原子地读出并删除，并发取用同一凭据时至多一次得到会员
ID。不用 GETDEL：它要求 Redis 6.2，共享 Redis 的版本未核实。

CSRF 令牌不存储，由凭据原文加本模块的用途前缀做 SHA-256 得到；前缀不同于会员会话
（app/services/member_auth.py）、后台会话（app/services/admin_auth.py）与订单访问
（app/services/order_access.py）。写法与 member_auth 相同，比较用常量时间。

Redis 未配置（客户端为 None）、连不上、超时或返回错误时，签发与取用都抛 SHOP-TASK-026 的
RateLimitUnavailable，调用方必须拒绝重设，不得放行。

重设密码：新密码须满足 SHOP-TASK-071 的 password_length_ok；以 pw_hash.hash_password 算哈希，
条件更新（仅当会员仍为 active）写入；写入后用 revoke_all_member_sessions 撤销该会员全部会话。
未设过密码的会员同样可设置（等同首次设密码，UX P12）。只 flush、不提交，由调用方提交。
时间一律是不带时区的 UTC。

本模块不写日志；凭据、CSRF 令牌、Redis 键、会员 ID 与密码不出现在异常消息或 repr 里。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeGuard

import redis
from redis.exceptions import RedisError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.member import MEMBER_ACTIVE, Member
from app.services import pw_hash
from app.services.member_auth import (
    TOKEN_BYTES,
    TOKEN_LENGTH,
    password_length_ok,
    revoke_all_member_sessions,
)
from app.services.rate_limit import RateLimitUnavailable

# Kelvin 2026-10-10：重设凭据 10 分钟有效。
RESET_TOKEN_TTL_SECONDS = 600

KEY_PREFIX = "acuven_shop:member_pw_reset:"

_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}")

# CSRF 令牌的用途前缀，与会员会话、后台会话、订单访问的前缀都不同。
_CSRF_PREFIX = b"acuven-shop/member-pw-reset/csrf\x00"


@dataclass(frozen=True)
class IssuedPwReset:
    """签发结果：token 交给 cookie，csrf_token 交给页面。"""

    token: str = field(repr=False)
    csrf_token: str = field(repr=False)


def _is_well_formed(token: str | None) -> TypeGuard[str]:
    return isinstance(token, str) and _TOKEN_PATTERN.fullmatch(token) is not None


def _reset_key(token: str) -> str:
    return KEY_PREFIX + hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf_for(token: str) -> str:
    return hashlib.sha256(_CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _positive_id(member_id: object) -> int:
    if isinstance(member_id, bool) or not isinstance(member_id, int) or member_id < 1:
        raise ValueError("member_id must be a positive integer")
    return member_id


# ---------------------------------------------------------------------------
# 重设凭据
# ---------------------------------------------------------------------------


def issue_pw_reset(client: redis.Redis | None, member_id: int) -> IssuedPwReset:
    """短信验证通过且号码已注册后，给该会员签发一次性重设凭据，10 分钟有效。

    键为前缀加凭据摘要、值为会员 ID，以一条 SET … EX 600 写入。会员 ID 不是正整数时抛
    ValueError（编程错误）。Redis 未配置或不可用时抛 RateLimitUnavailable，调用方必须拒绝。
    """
    member_id = _positive_id(member_id)
    if client is None:
        raise RateLimitUnavailable()
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        stored = client.set(_reset_key(token), str(member_id), ex=RESET_TOKEN_TTL_SECONDS)
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None
    if not stored:
        raise RateLimitUnavailable()
    return IssuedPwReset(token=token, csrf_token=_csrf_for(token))


def consume_pw_reset(client: redis.Redis | None, token: str | None) -> int | None:
    """取用重设凭据：原子地读出并删除，返回会员 ID；凭据不存在、已过期或已被取用时为空。

    格式不合法的凭据直接返回 None、不访问 Redis。在一个 MULTI 事务里 GET 与 DEL，并发取用
    同一凭据时至多一次得到会员 ID。Redis 未配置或不可用、事务里任何一条命令出错，或读到的值
    不是会员 ID 时抛 RateLimitUnavailable，不把读到的值当作取用成功。
    """
    if not _is_well_formed(token):
        return None
    if client is None:
        raise RateLimitUnavailable()
    key = _reset_key(token)
    try:
        with client.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            value, deleted = pipe.execute()
        if isinstance(value, Exception) or isinstance(deleted, Exception):
            raise RateLimitUnavailable()
        if value is None:
            return None
        if deleted != 1:
            raise RateLimitUnavailable()
        return _positive_id(int(value))
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None


# ---------------------------------------------------------------------------
# CSRF 令牌
# ---------------------------------------------------------------------------


def csrf_token_for_reset(token: str | None) -> str | None:
    """由凭据原文重新算出 CSRF 令牌；凭据缺失或格式不合法时为空。不访问 Redis。"""
    if not _is_well_formed(token):
        return None
    return _csrf_for(token)


def check_reset_csrf(token: str | None, header_value: str | None) -> bool:
    """请求头里的 CSRF 令牌是否与由凭据原文重新算出的值一致（常量时间比较）。

    令牌缺失、不一致或凭据缺失、格式不合法都不通过。不访问 Redis：提交新密码必须同时通过
    本函数与 consume_pw_reset。
    """
    expected = csrf_token_for_reset(token)
    if expected is None or not isinstance(header_value, str) or not header_value:
        return False
    return hmac.compare_digest(
        header_value.encode("utf-8", "surrogatepass"),
        expected.encode("ascii"),
    )


# ---------------------------------------------------------------------------
# 重设密码
# ---------------------------------------------------------------------------


def reset_password(db: Session, member_id: int, new_password: str, now: datetime) -> bool:
    """把 active 会员的密码改为新密码，并撤销该会员的全部会话；会员已不是 active 时为假。

    新密码不满足 password_length_ok 时抛 ValueError（消息不含密码），不写库。未设过密码的会员
    同样设置。只 flush、不提交：调用方在同一事务里提交。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    if not password_length_ok(new_password):
        # 不带消息：任何文字都可能恰好包含调用方传入的密码。
        raise ValueError()
    encoded = pw_hash.hash_password(new_password)
    result = db.execute(
        update(Member)
        .where(Member.id == member_id, Member.status == MEMBER_ACTIVE)
        .values(password_hash=encoded)
        .execution_options(synchronize_session="fetch")
    )
    if not result.rowcount:
        return False
    revoke_all_member_sessions(db, member_id, now)
    return True

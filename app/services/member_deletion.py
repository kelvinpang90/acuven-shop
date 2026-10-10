"""会员注销与注销的一次性批准。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 3 条：注销时撤销会话、删除手机号
与密码，并把其订单上的会员 ID 清空以解除关联；「注销清空会员 ID 后这些标记保留，重新注册不能
据此恢复旧订单的访问或积分」。「资料保留」：会员注销后收货资料也保留。docs/HANDOFF.md 0.41
记录的 Kelvin 2026-10-08 决定（3）：「重设密码与注销时撤销该会员的全部会话」；0.45 记录的
Kelvin 2026-10-10 决定（1）：注销验证码核验通过后，服务端在共享 Redis 本项目的库编号里记一条
绑定当前会员会话的一次性批准，10 分钟有效、使用一次即删除，确认注销时取用。
本模块只提供规则函数，接口留给 SHOP-TASK-081 与 082。积分余额与未用券作废、删除电话关联
防套利索引随之后的优惠券与积分账本任务实现（这些表尚不存在）。

注销：以一条条件更新（仅当会员仍为 active）把状态改为 deleted、清空手机号与密码哈希、写注销
时间（满足 app/models/member.py 的 deleted_cleared 约束）；更新到时再以 SHOP-TASK-071 的
revoke_all_member_sessions 撤销该会员全部会话，并把该会员所有订单的 member_id 置空，
claim_status 一律不改（已认领与不可认领的标记保留，同号重注册不会再次认领）。会员行、收货资料、
订单状态、订单事件与短信验证记录都不删不改。只 flush、不提交，由调用方提交。
时间一律是不带时区的 UTC。

注销批准：键为固定前缀加会员会话 ID，值为会员 ID，以一条 SET … EX 600 写入，不分成 SET 与
EXPIRE 两步；同一会话再次签发即覆盖并重新计时。取用：在一个 MULTI 事务里 GET 与 DEL，原子地
读出并删除，值等于该会员 ID 才算批准；并发取用同一批准时至多一次得到 True。不用 GETDEL：它要求
Redis 6.2，共享 Redis 的版本未核实。批准不随数据库事务回滚：取用后注销未提交的，访客须重新验证。

Redis 未配置（客户端为 None）、连不上、超时或返回错误时，签发与取用都抛 SHOP-TASK-026 的
RateLimitUnavailable，调用方必须拒绝注销，不得放行。

本模块不写日志；Redis 键、会话 ID 与会员 ID 不出现在异常消息里。
"""

from __future__ import annotations

from datetime import datetime

import redis
from redis.exceptions import RedisError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Order
from app.models.member import MEMBER_ACTIVE, MEMBER_DELETED, Member
from app.services.member_auth import revoke_all_member_sessions
from app.services.rate_limit import RateLimitUnavailable

# Kelvin 2026-10-10：注销批准 10 分钟有效。
DELETE_APPROVAL_TTL_SECONDS = 600

KEY_PREFIX = "acuven_shop:member_delete_approval:"


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _approval_key(session_id: int) -> str:
    return KEY_PREFIX + str(session_id)


# ---------------------------------------------------------------------------
# 注销批准
# ---------------------------------------------------------------------------


def issue_delete_approval(client: redis.Redis | None, session_id: int, member_id: int) -> None:
    """注销验证码核验通过后，给当前会员会话记一条一次性注销批准，10 分钟有效。

    键为前缀加会话 ID、值为会员 ID，以一条 SET … EX 600 写入；同一会话再次签发即覆盖并重新
    计时。会话 ID 或会员 ID 不是正整数时抛 ValueError（编程错误，不访问 Redis）。Redis 未配置
    或不可用时抛 RateLimitUnavailable，调用方必须拒绝。
    """
    session_id = _positive_id(session_id, "session_id")
    member_id = _positive_id(member_id, "member_id")
    if client is None:
        raise RateLimitUnavailable()
    try:
        stored = client.set(
            _approval_key(session_id), str(member_id), ex=DELETE_APPROVAL_TTL_SECONDS
        )
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None
    if not stored:
        raise RateLimitUnavailable()


def consume_delete_approval(client: redis.Redis | None, session_id: int, member_id: int) -> bool:
    """取用当前会员会话的注销批准：原子地读出并删除，值等于该会员 ID 时为真。

    键不存在（从未签发、已过期、已被取用）或值不是该会员 ID 时为假；两种情况键都已不在。
    在一个 MULTI 事务里 GET 与 DEL，并发取用同一批准时至多一次为真。会话 ID 或会员 ID 不是
    正整数时抛 ValueError（不访问 Redis）。Redis 未配置或不可用、事务里任何一条命令出错，或
    读到值而未删到时抛 RateLimitUnavailable，不把读到的值当作取用成功。
    """
    session_id = _positive_id(session_id, "session_id")
    member_id = _positive_id(member_id, "member_id")
    if client is None:
        raise RateLimitUnavailable()
    key = _approval_key(session_id)
    try:
        with client.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.delete(key)
            value, deleted = pipe.execute()
        if isinstance(value, Exception) or isinstance(deleted, Exception):
            raise RateLimitUnavailable()
        if value is None:
            return False
        if deleted != 1:
            raise RateLimitUnavailable()
        if isinstance(value, bytes):
            value = value.decode("ascii")
        if not isinstance(value, str):
            raise RateLimitUnavailable()
        return value == str(member_id)
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None


# ---------------------------------------------------------------------------
# 注销
# ---------------------------------------------------------------------------


def delete_member(db: Session, member_id: int, now: datetime) -> bool:
    """注销 active 会员：清空手机号与密码哈希、改为 deleted、写注销时间，撤销其全部会话，
    并把其全部订单的会员 ID 置空；会员不存在或已注销时为假，且不做任何改动。

    订单不论状态都解除关联，claim_status 不改；收货资料、订单状态、订单事件与短信验证记录
    不删不改。只 flush、不提交：调用方在同一事务里提交。
    """
    if now.tzinfo is not None:
        raise ValueError("now must be a naive UTC datetime")
    result = db.execute(
        update(Member)
        .where(Member.id == member_id, Member.status == MEMBER_ACTIVE)
        .values(status=MEMBER_DELETED, phone=None, password_hash=None, deleted_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if not result.rowcount:
        return False
    revoke_all_member_sessions(db, member_id, now)
    db.execute(
        update(Order)
        .where(Order.member_id == member_id)
        .values(member_id=None)
        .execution_options(synchronize_session="fetch")
    )
    db.flush()
    return True

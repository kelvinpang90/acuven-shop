"""短时限流：共享 Redis 的连接、固定窗口计数与访客来源。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：「短时限流计数存共享
infra_redis 为本项目分配的独立库编号，多 API 进程共用；Redis 数据丢失后这些计数清零可接受」，
「查单和管理员登录等依赖 Redis 限流的敏感接口也拒绝请求，商品浏览继续可用」。

连接串取自 SHOP_REDIS_URL（末尾数字即运营者分配的库编号）。未配置、连不上、超时或 Redis 返回
错误一律抛 RateLimitUnavailable（fail closed），由调用的接口拒绝请求（回答暂不可用），
不得把它当作「未超限」放行。计数只在 Redis 里，丢失后从零开始。

本模块不写日志；连接串、主机、计数键、来源地址与标识原文都不出现在异常消息或返回值里。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from functools import lru_cache

import redis
from fastapi import Request
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

# 连接与每次读写的超时（秒）。限流在请求路径上，Redis 慢时宁可很快拒绝也不拖住请求。
CONNECT_TIMEOUT_SECONDS = 0.5
SOCKET_TIMEOUT_SECONDS = 0.5

KEY_PREFIX = "acuven_shop:rate_limit:"

# 拿不到任何来源时的占位值：这些请求共用一个来源计数，只会更严格。
UNKNOWN_SOURCE = "unknown"

_BUCKET_PATTERN = re.compile(r"[a-z][a-z0-9_]*")


class RateLimitUnavailable(RuntimeError):
    """Redis 未配置、连不上、超时或返回错误。调用方必须拒绝请求，不得放行。"""

    def __init__(self) -> None:
        super().__init__("rate limit store unavailable")


def _no_retry() -> Retry:
    # redis-py 6 起默认会重试；这里显式关掉，失败立即报告。
    return Retry(NoBackoff(), 0)


@lru_cache
def _client_for(redis_url: str) -> redis.Redis:
    # 只建客户端与连接池，不连 Redis：第一次执行命令时才连接。同一连接串在进程内只建一次。
    pool = redis.ConnectionPool.from_url(
        redis_url,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        retry=_no_retry(),
    )
    return redis.Redis(connection_pool=pool, retry=_no_retry())


def get_redis_client(request: Request) -> redis.Redis:
    """FastAPI 依赖：按 SHOP_REDIS_URL 惰性创建、进程内复用的 Redis 客户端。

    连接串为空或格式不对时抛 RateLimitUnavailable。调用的接口须把它转成「暂不可用」的回答。
    测试用 app.dependency_overrides[get_redis_client] 换成自己的客户端。
    """
    redis_url = request.app.state.settings.redis_url
    if not redis_url:
        raise RateLimitUnavailable()
    try:
        return _client_for(redis_url)
    except (RedisError, ValueError):
        # 异常链里可能带连接串，from None 不带出去。
        raise RateLimitUnavailable() from None


def rate_limit_key(bucket: str, identifier: str) -> str:
    """计数键：固定前缀 + 桶名 + 标识的 SHA-256 十六进制摘要；标识原文不进 Redis。"""
    if not isinstance(bucket, str) or not _BUCKET_PATTERN.fullmatch(bucket):
        raise ValueError("bucket must be lowercase letters, digits and underscores")
    if not isinstance(identifier, str):
        raise ValueError("identifier must be a string")
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"{KEY_PREFIX}{bucket}:{digest}"


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def hit(client: redis.Redis, bucket: str, identifier: str, limit: int, window_seconds: int) -> bool:
    """计一次，返回计入这一次后是否仍不超过上限（True 放行，False 拒绝）。

    固定窗口：在一个 MULTI 事务里 INCR，并以 EXPIRE NX 只在键尚无过期时间时设置过期，
    窗口从第一次计数算起，之后的计数不延长窗口（NX 需要 Redis 7）。超限后的请求照样计数。

    具体阈值（limit、window_seconds）由调用的任务按 Kelvin 的决定传入，本模块不定任何阈值。
    Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝请求。
    """
    key = rate_limit_key(bucket, identifier)
    limit = _positive_int(limit, "limit")
    window_seconds = _positive_int(window_seconds, "window_seconds")
    try:
        with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = pipe.execute()
        return int(count) <= limit
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None


def current_count(client: redis.Redis, bucket: str, identifier: str) -> int:
    """当前窗口内的计数，不自增；键不存在（从未计数或窗口已过）为 0。

    Redis 不可用时抛 RateLimitUnavailable，调用方必须拒绝请求。
    """
    key = rate_limit_key(bucket, identifier)
    try:
        value = client.get(key)
        return 0 if value is None else int(value)
    except (RedisError, ValueError, TypeError):
        raise RateLimitUnavailable() from None


def client_source(request: Request) -> str:
    """访客来源（用作限流标识），规范化后的 IP 地址文本或 UNKNOWN_SOURCE。

    优先取反向代理设置的 X-Real-IP：恰好一个值且是合法的 IPv4 或 IPv6 地址时使用；缺失、
    出现多次、一个值里含多个地址或不合法时，退回连接的对端地址，再没有时用 UNKNOWN_SOURCE。

    不用 X-Forwarded-For：代理是在访客送来的值后面追加，访客自己能在前面追加伪造的地址，
    取哪一段都要依赖代理层数的假设。

    上线前须由运营者确认边缘反向代理为本站设置（覆盖，而不是透传访客送来的）X-Real-IP。
    代理不设置时，对端地址是代理自己，所有访客共享一个来源计数，只会更严格、不会放开；
    但若代理把访客自己送来的 X-Real-IP 原样转进来，访客就能换着地址绕过按来源的限流。
    """
    values = request.headers.getlist("x-real-ip")
    if len(values) == 1:
        try:
            return str(ipaddress.ip_address(values[0].strip()))
        except ValueError:
            pass
    if request.client is not None and request.client.host:
        return request.client.host
    return UNKNOWN_SOURCE

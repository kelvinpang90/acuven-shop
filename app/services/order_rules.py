"""订单号生成与订单状态迁移判定的纯函数。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Order / OrderItem 一行
（不可猜测的订单号）与「订单与退款状态」。不碰数据库。
订单号显示时的分组与查单时的输入规范化不在这里。
"""

from __future__ import annotations

import secrets

from app.models.order import (
    ACTOR_ADMIN,
    ACTOR_GUEST,
    ACTOR_MEMBER,
    ACTOR_SYSTEM,
    ORDER_NUMBER_LENGTH,
    STATUS_AWAITING_PAYMENT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PACKED,
    STATUS_PAID,
    STATUS_SHIPPED,
)

# Crockford Base32 大写字母表：不含 I、L、O、U。
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# 16 位 Base32 恰好 80 位随机数。
ORDER_NUMBER_BITS = 80

# (当前状态, 目标状态) → 允许的操作者类别。不在表里的迁移一律拒绝，
# 包括原地迁移、倒退、跨级与已支付订单取消。模拟支付失败不是状态迁移。
_ALLOWED_TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    # 下单者模拟支付成功。
    (STATUS_AWAITING_PAYMENT, STATUS_PAID): frozenset({ACTOR_GUEST, ACTOR_MEMBER}),
    # 下单者在支付页取消，或超时由系统取消。
    (STATUS_AWAITING_PAYMENT, STATUS_CANCELLED): frozenset(
        {ACTOR_GUEST, ACTOR_MEMBER, ACTOR_SYSTEM}
    ),
    # 管理员依次推进打包、发货。
    (STATUS_PAID, STATUS_PACKED): frozenset({ACTOR_ADMIN}),
    (STATUS_PACKED, STATUS_SHIPPED): frozenset({ACTOR_ADMIN}),
    # 访客或会员确认收货，或模拟发货满 7 天由系统自动确认。
    (STATUS_SHIPPED, STATUS_COMPLETED): frozenset({ACTOR_GUEST, ACTOR_MEMBER, ACTOR_SYSTEM}),
}


def generate_order_number() -> str:
    """用 secrets 取 80 位随机数，编码为 16 位 Crockford Base32 大写字符，不含分隔符。"""
    value = secrets.randbits(ORDER_NUMBER_BITS)
    digits: list[str] = []
    for _ in range(ORDER_NUMBER_LENGTH):
        value, digit = divmod(value, len(CROCKFORD_ALPHABET))
        digits.append(CROCKFORD_ALPHABET[digit])
    return "".join(reversed(digits))


def is_valid_order_number(value: object) -> bool:
    """是否恰好 16 位且每位都在大写 Crockford Base32 字母表里；不做任何规范化。"""
    return (
        isinstance(value, str)
        and len(value) == ORDER_NUMBER_LENGTH
        and all(char in CROCKFORD_ALPHABET for char in value)
    )


def is_transition_allowed(current: str, target: str, actor: str) -> bool:
    """操作者类别 actor 能否把订单从 current 迁移到 target。"""
    return actor in _ALLOWED_TRANSITIONS.get((current, target), frozenset())

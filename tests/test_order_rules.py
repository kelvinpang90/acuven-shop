"""订单号生成与订单状态迁移判定。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「数据模型」的 Order / OrderItem 一行
「不可猜测的订单号」，「权限与资料保护」「查单先按高熵订单号定位」，以及「订单与退款状态」。
每条测试的文档字符串引用它守住的那一句。期望的迁移表在这里另写一份，不取实现里的表。
"""

from __future__ import annotations

import itertools

import pytest

from app.models.order import ACTOR_TYPES, ORDER_STATUSES
from app.services.order_rules import (
    CROCKFORD_ALPHABET,
    generate_order_number,
    is_transition_allowed,
    is_valid_order_number,
)

EXPECTED_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# (当前状态, 目标状态) → 允许的操作者类别，按「订单与退款状态」逐句写出。
ALLOWED = {
    ("awaiting_demo_payment", "demo_paid"): {"guest", "member"},
    ("awaiting_demo_payment", "demo_cancelled"): {"guest", "member", "system"},
    ("demo_paid", "demo_packed"): {"admin"},
    ("demo_packed", "demo_shipped"): {"admin"},
    ("demo_shipped", "demo_completed"): {"guest", "member", "system"},
}


def test_statuses_and_actors_are_the_designed_sets() -> None:
    """「订单与退款状态」：六种订单状态与四种操作者类别，穷举测试以它们为全集。"""
    assert set(ORDER_STATUSES) == {
        "awaiting_demo_payment",
        "demo_paid",
        "demo_packed",
        "demo_shipped",
        "demo_completed",
        "demo_cancelled",
    }
    assert set(ACTOR_TYPES) == {"guest", "member", "admin", "system"}


def test_alphabet_is_crockford_base32_without_i_l_o_u() -> None:
    """「不可猜测的订单号」：字母表是 32 个互不相同的 Crockford Base32 大写字符，
    不含容易与 1、0 或彼此混淆的 I、L、O、U。
    """
    assert CROCKFORD_ALPHABET == EXPECTED_ALPHABET
    assert len(set(CROCKFORD_ALPHABET)) == 32
    assert not set("ILOU") & set(CROCKFORD_ALPHABET)


def test_generated_order_number_format() -> None:
    """「不可猜测的订单号」：生成的订单号恰好 16 位，只含大写 Crockford Base32 字符，
    不含 I、L、O、U、小写字母与分隔符，并通过格式校验。
    """
    for _ in range(1000):
        number = generate_order_number()

        assert len(number) == 16
        assert set(number) <= set(EXPECTED_ALPHABET)
        assert not set(number) & set("ILOUilou-_ ")
        assert is_valid_order_number(number)


def test_generated_order_numbers_do_not_repeat() -> None:
    """「不可猜测的订单号」「查单先按高熵订单号定位」：80 位随机数，大量生成不重复。"""
    numbers = [generate_order_number() for _ in range(20000)]

    assert len(set(numbers)) == len(numbers)


def test_generated_order_numbers_use_whole_alphabet_in_every_position() -> None:
    """「不可猜测的订单号」：每一位都取遍整个字母表，包括首位，说明 80 位全部是随机位，
    没有固定前缀或只用一部分字符。
    """
    numbers = [generate_order_number() for _ in range(5000)]

    for position in range(16):
        assert {number[position] for number in numbers} == set(EXPECTED_ALPHABET)


@pytest.mark.parametrize(
    "number",
    ["0123456789ABCDEF", "ZZZZZZZZZZZZZZZZ", "0000000000000000", "7XK3M9PQRSTVWY2H"],
)
def test_valid_order_number_is_accepted(number: str) -> None:
    """「不可猜测的订单号」：16 位大写 Crockford Base32 字符串是合法格式。"""
    assert is_valid_order_number(number)


@pytest.mark.parametrize(
    "number",
    [
        "0123456789abcdef",
        "0123456789ABCDEf",
        "0123-4567-89AB-CDEF",
        "01234567 89ABCDEF",
        "0123456789ABCDE",
        "0123456789ABCDEF0",
        "",
        "0123456789ABCDEI",
        "0123456789ABCDEL",
        "0123456789ABCDEO",
        "0123456789ABCDEU",
        "0123456789ABCDE!",
        "０１２３４５６７８９ＡＢＣＤＥＦ",
        None,
        1234567890123456,
    ],
)
def test_invalid_order_number_is_rejected(number: object) -> None:
    """「不可猜测的订单号」：小写、含分隔符或空格、长度不对、含 I、L、O、U 或其他字符、
    全角字符、不是字符串，一律不是合法订单号；格式校验不做任何规范化。
    """
    assert not is_valid_order_number(number)


@pytest.mark.parametrize(
    ("current", "target", "actor"),
    [
        (current, target, actor)
        for (current, target), actors in ALLOWED.items()
        for actor in sorted(actors)
    ],
)
def test_designed_transitions_are_allowed(current: str, target: str, actor: str) -> None:
    """「下单：awaiting_demo_payment……成功进入 demo_paid」「待支付订单可由下单者在支付页
    取消……或超时为 demo_cancelled」「管理员依次推进 demo_paid → demo_packed → demo_shipped」
    「访客……会员……确认收货后进入 demo_completed……模拟发货满 7 天自动确认收货」：
    五条迁移各自允许设计列出的操作者。
    """
    assert is_transition_allowed(current, target, actor)


def test_every_other_combination_is_rejected() -> None:
    """「所有状态迁移校验当前状态、操作者与数量」「已完成的履约状态不可倒退」
    「已模拟支付订单不走取消」：六种状态 × 六种状态 × 四种操作者中，除上面五条迁移与其
    操作者外全部被拒，包括原地迁移、倒退、跨级、已支付订单取消与错误的操作者。
    """
    checked = 0
    for current, target, actor in itertools.product(ORDER_STATUSES, ORDER_STATUSES, ACTOR_TYPES):
        if actor in ALLOWED.get((current, target), set()):
            continue
        assert not is_transition_allowed(current, target, actor), (current, target, actor)
        checked += 1

    allowed_count = sum(len(actors) for actors in ALLOWED.values())
    assert checked == 6 * 6 * 4 - allowed_count


@pytest.mark.parametrize(
    ("current", "target", "actor"),
    [
        ("demo_paid", "demo_paid", "admin"),
        ("awaiting_demo_payment", "awaiting_demo_payment", "guest"),
        ("demo_shipped", "demo_packed", "admin"),
        ("demo_completed", "demo_shipped", "admin"),
        ("demo_cancelled", "awaiting_demo_payment", "system"),
        ("demo_paid", "demo_shipped", "admin"),
        ("awaiting_demo_payment", "demo_completed", "system"),
        ("demo_paid", "demo_cancelled", "guest"),
        ("demo_paid", "demo_cancelled", "admin"),
        ("demo_shipped", "demo_cancelled", "system"),
        ("awaiting_demo_payment", "demo_paid", "admin"),
        ("awaiting_demo_payment", "demo_paid", "system"),
        ("demo_paid", "demo_packed", "guest"),
        ("demo_shipped", "demo_completed", "admin"),
    ],
)
def test_named_forbidden_transitions(current: str, target: str, actor: str) -> None:
    """「已完成的履约状态不可倒退」「已模拟支付订单不走取消，走退款流程」
    「管理员依次推进」：原地迁移、倒退、跨级、已支付订单取消，以及支付由下单者完成、
    打包由管理员完成、确认收货不由管理员代为完成，逐一点名被拒。
    """
    assert not is_transition_allowed(current, target, actor)


@pytest.mark.parametrize(
    ("current", "target", "actor"),
    [
        ("awaiting_demo_payment", "demo_paid", "customer"),
        ("awaiting_demo_payment", "demo_payment_failed", "guest"),
        ("demo_refunded", "demo_packed", "admin"),
        ("DEMO_PAID", "demo_packed", "admin"),
        ("demo_paid", "demo_packed", "ADMIN"),
    ],
)
def test_unknown_status_or_actor_is_rejected(current: str, target: str, actor: str) -> None:
    """「模拟支付失败仍停在此状态」：没有支付失败状态；未知状态、未知操作者类别与
    大小写不同的值一律被拒，不做规范化。
    """
    assert not is_transition_allowed(current, target, actor)

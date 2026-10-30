"""后台生成的代码：基本写法、截短与去重。

依据 docs/DESIGN.md 1.18（提交 5ba9796）「计价、优惠、积分与库存」第 11 条「代码」一段：
「英文名称只保留 ASCII 字母与数字并转小写，其余连续字符换成一个连字符，去掉首尾连字符，
结果为空时以 product、option、value 代替」「去重时依次试后缀（无、-2、-3……），每次先把基本
部分截到『上限减后缀长度』、去掉截短后末尾的连字符，再接后缀，直到与已有的及同一次保存里先
生成的都不重复」；第 14 条分类 slug 的写法与之相同（结果为空时以 category 代替）。
HANDOFF 0.76：写成独立模块，后台分类（SHOP-TASK-180）与之后的后台商品任务沿用。
两个函数都不碰数据库；已有的代码由调用方在锁行之后取出传入。
"""

from __future__ import annotations

import re
from collections.abc import Collection

# [A-Za-z0-9] 只含 ASCII：非 ASCII 字母与数字也当作分隔。
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")


def code_base(english_name: str, fallback: str) -> str:
    """代码的基本写法：只保留 ASCII 字母与数字并转小写，其余连续字符（含空白、标点与非 ASCII
    字符）换成一个连字符，去掉首尾连字符；结果为空时返回 fallback。不截短。"""
    base = _SEPARATORS.sub("-", english_name).lower().strip("-")
    return base or fallback


def unique_code(base: str, limit: int, taken: Collection[str]) -> str:
    """依次试后缀（无、-2、-3……），每次先把 base 截到 limit 减后缀长度、去掉截短后末尾的
    连字符、再接后缀，返回第一个不在 taken 里的结果。

    taken 为已有代码的集合；同一次保存里先生成的由调用方加进去。
    """
    number = 1
    while True:
        suffix = "" if number == 1 else f"-{number}"
        candidate = base[: limit - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
        number += 1

"""结账页 P05 的只读参考数据：地区代码与国家呼叫码、马来西亚州属代码与名称。

地区列表取自 phonenumbers 支持的地区（SUPPORTED_REGIONS 只有两位代码，非地理编码如 +800
不在其中），每项给出地区代码与国家呼叫码，按地区代码排序。国家名称不在这里提供：
前端按界面语言本地化。

州属代码取自 app/services/shipping.py 的 MY_STATE_CODES，不另立一套；名称是马来文官方名称，
三种界面语言都用这一名称（Kelvin 2026-10-01 决定）。
结账第 1 步国家码默认 +60，依据 docs/DESIGN.md 1.11「权限与资料保护」。

数据只由代码与 phonenumbers 的元数据决定，不访问数据库或网络，进程内只算一次。
"""

from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

import phonenumbers

from app.services.shipping import MALAYSIA, MY_STATE_CODES

DEFAULT_PHONE_REGION = MALAYSIA

# 按 MY-01 到 MY-16 的顺序（ISO 3166-2:MY）。
_MY_STATE_NAMES = (
    "Johor",
    "Kedah",
    "Kelantan",
    "Melaka",
    "Negeri Sembilan",
    "Pahang",
    "Pulau Pinang",
    "Perak",
    "Perlis",
    "Selangor",
    "Terengganu",
    "Sabah",
    "Sarawak",
    "Wilayah Persekutuan Kuala Lumpur",
    "Wilayah Persekutuan Labuan",
    "Wilayah Persekutuan Putrajaya",
)

# 代码与名称数目不一致时宁可启动失败，也不给出错位的州属下拉。
if len(_MY_STATE_NAMES) != len(MY_STATE_CODES):
    raise RuntimeError("Malaysian state names do not match MY_STATE_CODES")


class Region(NamedTuple):
    """地区代码（两位大写 ISO 3166-1）与国家呼叫码。"""

    code: str
    calling_code: int


class MyState(NamedTuple):
    """马来西亚州属代码（MY-01 到 MY-16）与马来文官方名称。"""

    code: str
    name: str


@lru_cache(maxsize=1)
def phone_regions() -> tuple[Region, ...]:
    """phonenumbers 支持的全部地区，按地区代码排序。"""
    return tuple(
        Region(code, phonenumbers.country_code_for_region(code))
        for code in sorted(phonenumbers.SUPPORTED_REGIONS)
    )


@lru_cache(maxsize=1)
def my_states() -> tuple[MyState, ...]:
    """马来西亚州属与直辖区，按代码 MY-01 到 MY-16 排序。"""
    return tuple(
        MyState(code, name)
        for code, name in zip(sorted(MY_STATE_CODES), _MY_STATE_NAMES, strict=True)
    )

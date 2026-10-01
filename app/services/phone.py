"""手机号 E.164 规范化与短信白名单判定的纯函数。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「权限与资料保护」前两条：按所选国家码或输入的
`+` 国家码规范化为 E.164，只检查区号和长度等格式是否可能成立、不校验号码真实存在，
并以规范化结果判定是否属于短信白名单。不写日志、不访问网络或数据库、不发短信。
"""

from __future__ import annotations

import re
from typing import NamedTuple

import phonenumbers

# 短信白名单的国家呼叫码：60 马来西亚、65 新加坡（「初期短信国家」）。
SMS_WHITELIST_COUNTRY_CODES: frozenset[int] = frozenset({60, 65})

# 去掉首尾空白后的长度上限。
MAX_RAW_LENGTH = 32

# 拒绝时的固定消息，不含输入原文。
INVALID_PHONE_MESSAGE = "invalid phone number"

# 只允许开头一个加号，其后是 ASCII 数字、空格、连字符、点与括号。
# 字母（phonenumbers 会按键盘转成数字）、全角字符、制表符等一律不匹配。
_RAW_PATTERN = re.compile(r"\+?[0-9 \-.()]+")

# 默认地区：两位大写 ASCII 字母，另须在 phonenumbers 支持的地区里。
_REGION_PATTERN = re.compile(r"[A-Z]{2}")

# 白名单判定的输入：加号后只有数字，E.164 至多 15 位。
_E164_PATTERN = re.compile(r"\+[0-9]{1,15}")


class InvalidPhoneNumber(ValueError):
    """号码或默认地区不合规。消息固定，不含输入原文。"""

    def __init__(self) -> None:
        super().__init__(INVALID_PHONE_MESSAGE)


class NormalizedPhone(NamedTuple):
    e164: str
    country_code: int


def _parse_possible(text: str, region: str | None) -> phonenumbers.PhoneNumber:
    """解析并只接受 IS_POSSIBLE；IS_POSSIBLE_LOCAL_ONLY 与其他结果一律拒绝。"""
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        raise InvalidPhoneNumber() from None
    reason = phonenumbers.is_possible_number_with_reason(number)
    if reason != phonenumbers.ValidationResult.IS_POSSIBLE:
        raise InvalidPhoneNumber()
    return number


def normalize_phone(raw: object, default_region: object) -> NormalizedPhone:
    """把访客输入的手机号规范化为 E.164，返回 E.164 字符串及其国家呼叫码。

    default_region 是两位大写 ISO 3166-1 代码，须是 phonenumbers 支持的地区。
    两种调用：
    - 结账第一步：传页面国家码选择对应的地区（页面默认马来西亚，即 "MY"）；
    - 会员改填收货电话：传收货国家。
    输入以加号开头时以输入的国家码为准、忽略默认地区，否则按默认地区解析。

    只按已解析的国家码判断号码长度是否可能成立（is_possible_number_with_reason 为
    IS_POSSIBLE），不检查号码起始数字、不验证号码真实存在、不保证前缀已分配，以允许
    虚构演示号码；缺区号、只能本地拨打的号码拒绝。任何不合规都抛 InvalidPhoneNumber。
    """
    if (
        not isinstance(default_region, str)
        or not _REGION_PATTERN.fullmatch(default_region)
        or default_region not in phonenumbers.SUPPORTED_REGIONS
    ):
        raise InvalidPhoneNumber()
    if not isinstance(raw, str):
        raise InvalidPhoneNumber()
    text = raw.strip()
    if not 1 <= len(text) <= MAX_RAW_LENGTH or not _RAW_PATTERN.fullmatch(text):
        raise InvalidPhoneNumber()
    number = _parse_possible(text, default_region)
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return NormalizedPhone(e164=e164, country_code=number.country_code)


def is_sms_whitelisted(e164: object) -> bool:
    """规范化后的号码是否属于短信白名单（马来西亚、新加坡），只看号码本身的国家呼叫码。

    只接受 normalize_phone 产出的 E.164 字符串（加号开头、只含数字、能原样规范化回自身），
    其他输入抛 InvalidPhoneNumber。
    """
    if not isinstance(e164, str) or not _E164_PATTERN.fullmatch(e164):
        raise InvalidPhoneNumber()
    number = _parse_possible(e164, None)
    if phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164) != e164:
        raise InvalidPhoneNumber()
    return number.country_code in SMS_WHITELIST_COUNTRY_CODES

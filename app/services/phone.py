"""手机号 E.164 规范化与短信白名单判定的纯函数。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「权限与资料保护」第 1、2 条：服务端按所选国家码或
访客输入的 `+` 国家码把手机号规范化为 E.164，只检查区号和长度等格式是否可能成立，不校验号码
真实存在，以允许虚构演示资料；并以规范化结果判定是否属于短信白名单（「初期短信国家」：
马来西亚和新加坡）。结账身份、会员改填的收货电话、注册、查单与认领都用同一个规范化结果。

只用 phonenumbers 的可能性判断，不用有效性判断（is_valid_number）。不写日志、不访问网络或
数据库；拒绝一律抛 InvalidPhoneNumber，消息固定、不含输入原文，也不串联 phonenumbers 的异常。
"""

from __future__ import annotations

import re
from typing import NamedTuple

import phonenumbers
from phonenumbers import PhoneNumberFormat, ValidationResult

# 短信白名单国家呼叫码：60 马来西亚、65 新加坡。扩展须另行变更设计并批准。
SMS_WHITELIST_COUNTRY_CODES = frozenset({60, 65})

# 去掉首尾空白后的字符数上限。
MAX_INPUT_LENGTH = 32
# E.164 号码（国家码加本国号码）最多 15 位数字。
E164_MAX_DIGITS = 15

_MESSAGE = "invalid phone number"

# 只允许 ASCII 数字、空格、连字符、点、括号，以及开头的一个加号。
# 字母会被 phonenumbers 按键盘转换成数字，所以在解析前拒绝。
_INPUT_PATTERN = re.compile(r"\+?[0-9 ().\-]+")
_REGION_PATTERN = re.compile(r"[A-Z]{2}")
_E164_PATTERN = re.compile(r"\+[0-9]+")


class InvalidPhoneNumber(ValueError):
    """手机号或默认地区不合法；消息不含输入原文。"""

    def __init__(self) -> None:
        super().__init__(_MESSAGE)


class NormalizedPhone(NamedTuple):
    """规范化结果：E.164 字符串（如 `+60123456789`）及其国家呼叫码（如 60）。"""

    e164: str
    country_code: int


def normalize_phone(raw: object, default_region: object) -> NormalizedPhone:
    """把访客输入的手机号按默认地区规范化为 E.164。

    `default_region` 是两位大写 ISO 3166-1 代码，须是 phonenumbers 支持的地区，否则拒绝
    （输入以加号开头时也照样校验）。两种调用：

    - 结账第一步：传页面国家码选择对应的地区，页面默认马来西亚（`"MY"`）。
    - 会员改填收货电话：传收货国家。

    输入以加号开头时以输入的国家码为准、忽略默认地区；否则按默认地区解析（可带或不带
    本国前缀，如马来西亚的前导 0）。输入须是字符串，去掉首尾空白后 1 到 32 个字符，
    只含数字、空格、连字符、点、括号与开头的一个加号。只接受 phonenumbers 判为
    IS_POSSIBLE 的号码（按国家码判断长度是否可能成立，不验证号码存在或前缀已分配）；
    IS_POSSIBLE_LOCAL_ONLY（缺区号）与其他结果一律拒绝，超过 15 位数字的结果也拒绝。
    任何拒绝都抛 InvalidPhoneNumber。
    """
    if not isinstance(default_region, str) or not _REGION_PATTERN.fullmatch(default_region):
        raise InvalidPhoneNumber
    if default_region not in phonenumbers.SUPPORTED_REGIONS:
        raise InvalidPhoneNumber
    if not isinstance(raw, str):
        raise InvalidPhoneNumber
    text = raw.strip()
    if not 1 <= len(text) <= MAX_INPUT_LENGTH or not _INPUT_PATTERN.fullmatch(text):
        raise InvalidPhoneNumber
    region = None if text.startswith("+") else default_region
    return _parse_possible(text, region)


def is_sms_whitelisted(e164: object) -> bool:
    """规范化后的号码是否属于短信白名单（国家呼叫码 60 或 65）。

    只接受 normalize_phone 产出的 E.164 字符串（加号开头、只含数字、已是规范形式），
    否则抛 InvalidPhoneNumber。只看号码本身的国家呼叫码，与默认地区或收货国家无关。
    """
    if not isinstance(e164, str) or not _E164_PATTERN.fullmatch(e164):
        raise InvalidPhoneNumber
    if len(e164) - 1 > E164_MAX_DIGITS:
        raise InvalidPhoneNumber
    normalized = _parse_possible(e164, None)
    if normalized.e164 != e164:
        raise InvalidPhoneNumber
    return normalized.country_code in SMS_WHITELIST_COUNTRY_CODES


def _parse_possible(text: str, region: str | None) -> NormalizedPhone:
    """解析已通过字符检查的文本；region 为 None 时文本必须以加号开头。"""
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        raise InvalidPhoneNumber from None
    if number.extension:
        raise InvalidPhoneNumber
    reason = phonenumbers.is_possible_number_with_reason(number)
    if reason != ValidationResult.IS_POSSIBLE:
        raise InvalidPhoneNumber
    e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)
    if not _E164_PATTERN.fullmatch(e164) or len(e164) - 1 > E164_MAX_DIGITS:
        raise InvalidPhoneNumber
    return NormalizedPhone(e164, number.country_code)

"""手机号 E.164 规范化与短信白名单判定。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「权限与资料保护」第 1、2 条，
以及「失败、并发与重试」中「初期短信国家」的白名单。
每条测试的文档字符串引用它守住的那一句。
期望的 E.164 与白名单在这里另写，不取实现里的常量计算。
"""

from __future__ import annotations

import inspect

import phonenumbers
import pytest
from phonenumbers import ValidationResult

from app.services.phone import (
    SMS_WHITELIST_COUNTRY_CODES,
    InvalidPhoneNumber,
    NormalizedPhone,
    is_sms_whitelisted,
    normalize_phone,
)


def _assert_rejected(raw: object, region: object = "MY") -> None:
    """拒绝时抛同一个 ValueError 子类，消息与异常链都不带输入原文。"""
    with pytest.raises(InvalidPhoneNumber) as info:
        normalize_phone(raw, region)
    _assert_message_hides(info.value, raw)
    _assert_message_hides(info.value, region)


def _assert_message_hides(error: BaseException, value: object) -> None:
    assert isinstance(error, ValueError)
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None
    message = str(error)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            assert stripped not in message
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) >= 3:
            assert digits not in message
    elif value is not None:
        assert str(value) not in message
        assert repr(value) not in message


# --- 规范化：马来西亚 -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "0123456789",
        "123456789",
        "012 345 6789",
        "012-345 6789",
        "012-3456789",
        "(012) 345-6789",
        "012.345.6789",
        "  012-345 6789  ",
        "\t012 345 6789\n",
        "+60123456789",
        "+60 12-345 6789",
        "+60 (12) 345 6789",
        "+6012.345.6789",
    ],
)
def test_malaysian_spellings_normalize_to_same_e164(raw: str) -> None:
    """「页面提供国家码选择，默认 +60（马来西亚）」
    「服务端按所选国家码或输入的国家码规范化为 E.164」
    「注册、查单和订单认领都用相同的 E.164 规范化结果」：
    马来西亚本地写法带或不带前导 0、空格、连字符、点、括号与首尾空白，
    以及带加号写法，都得到同一个 E.164。
    """
    assert normalize_phone(raw, "MY") == NormalizedPhone("+60123456789", 60)


def test_result_is_e164_string_and_country_code() -> None:
    """「规范化为 E.164」：结果是加号加数字的字符串与整数国家呼叫码。"""
    result = normalize_phone("012-345 6789", "MY")

    assert isinstance(result.e164, str)
    assert result.e164 == "+60123456789"
    assert result.country_code == 60
    assert type(result.country_code) is int


# --- 规范化：新加坡、加号优先、其他国家 ------------------------------------------


@pytest.mark.parametrize("raw", ["8123 4567", "81234567", "+65 8123 4567", "+6581234567"])
def test_singapore_number(raw: str) -> None:
    """「服务端按所选国家码或输入的国家码规范化为 E.164」：
    页面选新加坡时的新加坡号码。
    """
    assert normalize_phone(raw, "SG") == NormalizedPhone("+6581234567", 65)


@pytest.mark.parametrize("raw", ["+65 8123 4567", "+6581234567", "  +65-8123-4567 "])
def test_plus_prefixed_singapore_number_with_malaysia_default(raw: str) -> None:
    """「访客明确输入 `+` 国家码时以输入为准」：
    默认地区为马来西亚时，加号开头的新加坡号码按新加坡解析。
    """
    assert normalize_phone(raw, "MY") == NormalizedPhone("+6581234567", 65)


@pytest.mark.parametrize("region", ["MY", "SG", "US", "GB", "JP"])
def test_plus_prefix_ignores_any_default_region(region: str) -> None:
    """「访客明确输入 `+` 国家码时以该号码为准」：
    会员改填收货电话时，无论收货国家是哪国，加号开头的号码结果相同。
    """
    assert normalize_phone("+44 20 7946 0958", region) == NormalizedPhone("+442079460958", 44)


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        ("020 7946 0958", "GB", NormalizedPhone("+442079460958", 44)),
        ("(202) 555-0123", "US", NormalizedPhone("+12025550123", 1)),
        ("03-1234-5678", "JP", NormalizedPhone("+81312345678", 81)),
        ("+66 2 123 4567", "MY", NormalizedPhone("+6621234567", 66)),
    ],
)
def test_other_country_numbers(raw: str, region: str, expected: NormalizedPhone) -> None:
    """「会员改填的收货电话以收货国家作为默认区号解析自由文本」：
    其他国家按收货国家解析，带加号时按输入的国家码。
    """
    assert normalize_phone(raw, region) == expected


# --- 只看可能性，不看有效性 --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        ("+65 2123 4567", "MY", NormalizedPhone("+6521234567", 65)),
        ("(212) 155-0123", "US", NormalizedPhone("+12121550123", 1)),
    ],
)
def test_possible_but_invalid_number_is_accepted(
    raw: str, region: str, expected: NormalizedPhone
) -> None:
    """「只检查区号和长度等格式是否可能成立，不校验号码真实存在，
    以允许虚构演示资料」：phonenumbers 判为无效（前缀未分配）
    但长度可能成立的号码被接受。
    """
    parsed = phonenumbers.parse(raw, region)
    assert not phonenumbers.is_valid_number(parsed)
    assert phonenumbers.is_possible_number_with_reason(parsed) == ValidationResult.IS_POSSIBLE

    assert normalize_phone(raw, region) == expected


def test_local_only_number_without_area_code_is_rejected() -> None:
    """「只检查区号和长度等格式是否可能成立」「格式不成立则提示修改」：
    默认地区为美国时缺区号的 7 位号码只能本地拨打，
    is_possible_number 会算它可能，但规范化拒绝。
    """
    parsed = phonenumbers.parse("555 0123", "US")
    reason = phonenumbers.is_possible_number_with_reason(parsed)
    assert reason == ValidationResult.IS_POSSIBLE_LOCAL_ONLY
    assert phonenumbers.is_possible_number(parsed)

    _assert_rejected("555 0123", "US")
    _assert_rejected("555-0123", "US")


def test_number_longer_than_e164_allows_is_rejected() -> None:
    """「规范化为 E.164」：phonenumbers 认为长度可能、
    但国家码加本国号码超过 E.164 的 15 位上限的号码不是 E.164，被拒。
    """
    raw = "+49 123456789012345"
    parsed = phonenumbers.parse(raw, None)
    assert phonenumbers.is_possible_number_with_reason(parsed) == ValidationResult.IS_POSSIBLE
    assert len(str(parsed.country_code)) + len(str(parsed.national_number)) > 15

    _assert_rejected(raw)


# --- 拒绝 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "region"),
    [
        ("123", "MY"),
        ("0123", "MY"),
        ("+60 12", "MY"),
        ("812", "SG"),
        ("012 3456 7890123", "MY"),
        ("+60 12 3456 789012", "MY"),
        ("8123 4567 8901 2", "SG"),
    ],
)
def test_too_short_or_too_long_is_rejected(raw: str, region: str) -> None:
    """「只检查区号和长度等格式是否可能成立……
    格式不成立则提示修改、不创建订单」：过短与过长的号码被拒。
    """
    _assert_rejected(raw, region)


@pytest.mark.parametrize(
    "raw",
    [
        "012-FLOWERS",
        "1-800-FLOWERS",
        "012 345 678a",
        "+60 12 ABC 6789",
        "012 345 6789 ext 12",
        "012 345 6789 x12",
        "tel:+60123456789",
    ],
)
def test_letters_are_rejected(raw: str) -> None:
    """「格式不成立则提示修改」：phonenumbers 会把字母按键盘转换成数字，
    含字母一律拒绝，不当作号码或分机。
    """
    _assert_rejected(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "012/345/6789",
        "012#3456789",
        "012*3456789",
        "012,3456789",
        "012;3456789",
        "012_3456789",
        "012\t3456789",
        "012 3456789",
        "60+123456789",
        "++60123456789",
        "+ +60123456789",
        "(+60) 123456789",
        "＋60123456789",
        "０１２３４５６７８９",
        "٠١٢٣٤٥٦٧٨٩",
        "012[345]6789",
        "+",
        "()",
        "-.",
    ],
)
def test_illegal_characters_are_rejected(raw: str) -> None:
    """「格式不成立则提示修改」：
    只允许 ASCII 数字、空格、连字符、点、括号与开头的一个加号；
    其他符号、制表与不换行空格、非开头或重复的加号、
    全角或其他文字的数字一律拒绝。
    """
    _assert_rejected(raw)


@pytest.mark.parametrize("raw", ["", " ", "   ", "\t\n", "　"])
def test_empty_or_blank_is_rejected(raw: str) -> None:
    """「格式不成立则提示修改」：空串与只有空白的输入被拒。"""
    _assert_rejected(raw)


def test_input_length_limit_is_32_characters_after_strip() -> None:
    """「格式不成立则提示修改」：去掉首尾空白后不超过 32 个字符；
    33 个字符被拒，即使其中的号码本身可能成立；首尾空白不计入。
    """
    exactly_32 = "+60" + " " * 20 + "123456789"
    too_long = "+60" + " " * 21 + "123456789"
    assert len(exactly_32) == 32
    assert len(too_long) == 33

    assert normalize_phone(exactly_32, "MY") == NormalizedPhone("+60123456789", 60)
    assert normalize_phone("   " + exactly_32 + "   ", "MY").e164 == "+60123456789"
    _assert_rejected(too_long)
    _assert_rejected("0" * 33)


@pytest.mark.parametrize(
    "raw", [None, 60123456789, 123456789.0, b"0123456789", ["0123456789"], True]
)
def test_non_string_input_is_rejected(raw: object) -> None:
    """「格式不成立则提示修改」：输入须是字符串。"""
    _assert_rejected(raw)


@pytest.mark.parametrize(
    "region",
    ["my", "My", "MYS", "M", "", " MY", "MY ", "001", "ZZ", "XX", "AA", None, 60, b"MY"],
)
def test_invalid_or_unsupported_default_region_is_rejected(region: object) -> None:
    """「服务端按所选国家码……规范化」
    「会员改填的收货电话以收货国家作为默认区号」：
    默认地区须是两位大写 ISO 3166-1 代码且 phonenumbers 支持，
    否则拒绝，不转换大小写；加号开头的输入也照样校验。
    """
    _assert_rejected("012-345 6789", region)
    _assert_rejected("+60 12-345 6789", region)


def test_rejections_share_one_exception_with_fixed_message() -> None:
    """「应用日志与监控不记录……完整电话」：
    各种拒绝抛同一个 ValueError 子类，消息相同、不含输入原文，
    以免被调用方原样写进日志或错误回显。
    """
    messages = set()
    for raw, region in [
        ("0123456789123456", "MY"),
        ("012-FLOWERS", "MY"),
        ("", "MY"),
        (None, "MY"),
        ("0123456789", "my"),
        ("555 0123", "US"),
    ]:
        with pytest.raises(InvalidPhoneNumber) as info:
            normalize_phone(raw, region)
        assert type(info.value) is InvalidPhoneNumber
        messages.add(str(info.value))
    assert issubclass(InvalidPhoneNumber, ValueError)
    assert len(messages) == 1


# --- 白名单 ------------------------------------------------------------------------


def test_whitelist_constant_is_malaysia_and_singapore() -> None:
    """「目的地白名单仅含马来西亚和新加坡」：白名单国家呼叫码恰好是 60 与 65。"""
    assert frozenset({60, 65}) == SMS_WHITELIST_COUNTRY_CODES


@pytest.mark.parametrize("e164", ["+60123456789", "+60312345678", "+6581234567", "+6521234567"])
def test_malaysia_and_singapore_are_whitelisted(e164: str) -> None:
    """「以规范化结果判定是否属于白名单」「目的地白名单仅含马来西亚和新加坡」：
    马来西亚与新加坡号码属于白名单，包括前缀未分配的虚构号码。
    """
    assert is_sms_whitelisted(e164) is True


@pytest.mark.parametrize(
    "e164",
    [
        "+6281234567890",
        "+6621234567",
        "+6737123456",
        "+442079460958",
        "+12025550123",
        "+81312345678",
        "+61212345678",
    ],
)
def test_other_countries_are_not_whitelisted(e164: str) -> None:
    """「号码不属于白名单时不发短信，以游客下单」：
    印尼、泰国、文莱（国家码同样以 6 开头）、英国、美国、日本、
    澳大利亚的号码不属于白名单。
    """
    assert is_sms_whitelisted(e164) is False


def test_whitelist_uses_normalized_result_regardless_of_default_region() -> None:
    """「以规范化结果判定是否属于白名单；之后选择的收货国家不改变已判定的号码」：
    白名单判定只接收号码、不接收地区；
    同一个 E.164 无论当初按哪个默认地区规范化，判定结果相同。
    """
    assert list(inspect.signature(is_sms_whitelisted).parameters) == ["e164"]

    from_my = normalize_phone("+65 8123 4567", "MY")
    from_sg = normalize_phone("8123 4567", "SG")
    from_us = normalize_phone("+65 8123 4567", "US")
    assert from_my == from_sg == from_us
    assert is_sms_whitelisted(from_my.e164) is True

    gb_from_my = normalize_phone("+44 20 7946 0958", "MY")
    assert is_sms_whitelisted(gb_from_my.e164) is False


@pytest.mark.parametrize(
    "value",
    [
        "60123456789",
        "+60 12-345 6789",
        "+60-123456789",
        "012-345 6789",
        "0123456789",
        "",
        "+",
        "+600123456789",
        "+60123",
        "+6012345678901234",
        "+60abc",
        "＋60123456789",
        " +60123456789",
        "+60123456789\n",
        "+0123456789",
        "+49123456789012345",
        None,
        60123456789,
        b"+60123456789",
    ],
)
def test_whitelist_rejects_non_e164_input(value: object) -> None:
    """「以规范化结果判定是否属于白名单」：
    只接受规范化产出的 E.164（加号开头、只含数字、已是规范形式）；
    未规范化、带分隔符或空白、国家码后带前导 0、长度不可能、
    未知国家码、非字符串一律拒绝，异常消息不含输入原文。
    """
    with pytest.raises(InvalidPhoneNumber) as info:
        is_sms_whitelisted(value)
    _assert_message_hides(info.value, value)

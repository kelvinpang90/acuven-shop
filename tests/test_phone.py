"""手机号 E.164 规范化与短信白名单判定。

依据 docs/DESIGN.md 1.9（提交 361d8bf）「权限与资料保护」前两条与「初期短信国家」。
每条测试的文档字符串引用它守住的那一句。期望的 E.164 与白名单在这里另写，不取实现里的常量。
"""

from __future__ import annotations

import phonenumbers
import pytest

from app.services.phone import (
    InvalidPhoneNumber,
    NormalizedPhone,
    is_sms_whitelisted,
    normalize_phone,
)

MY_E164 = "+60123456789"
SG_E164 = "+6581234567"
INVALID_MESSAGE = "invalid phone number"


def _assert_rejected(raw: object, region: object = "MY") -> None:
    """拒绝抛同一个 ValueError 子类，消息固定且不含输入原文。"""
    with pytest.raises(InvalidPhoneNumber) as excinfo:
        normalize_phone(raw, region)
    assert isinstance(excinfo.value, ValueError)
    message = str(excinfo.value)
    assert message == INVALID_MESSAGE
    for value in (raw, region):
        if isinstance(value, str) and value.strip():
            assert value.strip() not in message


@pytest.mark.parametrize(
    "raw",
    [
        "012-345 6789",
        "0123456789",
        "12 345 6789",
        "123456789",
        "(012) 345-6789",
        "012.345.6789",
        "  012-345 6789  ",
        "+60123456789",
        "+60 12-345 6789",
        "+60 (12) 345.6789",
    ],
)
def test_malaysian_local_and_plus_forms_normalize_to_same_e164(raw: str) -> None:
    """第 1 条「页面提供国家码选择，默认 +60（马来西亚）……服务端按所选国家码或输入的国家码
    规范化为 E.164」：马来西亚本地写法（带或不带前导 0、空格、连字符、点、括号）与带加号写法
    得到同一个 E.164。
    """
    assert normalize_phone(raw, "MY") == NormalizedPhone(MY_E164, 60)


@pytest.mark.parametrize("raw", ["8123 4567", "+65 8123 4567", "+65-8123-4567"])
def test_singapore_number_with_singapore_region(raw: str) -> None:
    """第 1 条「服务端按所选国家码……规范化为 E.164」：页面选 +65 时按新加坡解析。"""
    assert normalize_phone(raw, "SG") == NormalizedPhone(SG_E164, 65)


def test_plus_input_overrides_default_region() -> None:
    """第 1 条「访客明确输入 `+` 国家码时以输入为准」与第 2 条同句：默认地区为马来西亚时，
    加号开头的新加坡号码按新加坡解析。
    """
    assert normalize_phone("+65 8123 4567", "MY") == NormalizedPhone(SG_E164, 65)
    # 会员改填收货电话：收货国家为英国，输入带 +60 时仍以 +60 为准。
    assert normalize_phone("+60 12-345 6789", "GB") == NormalizedPhone(MY_E164, 60)


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        ("020 7946 0958", "GB", NormalizedPhone("+442079460958", 44)),
        ("+44 20 7946 0958", "MY", NormalizedPhone("+442079460958", 44)),
        ("(650) 253-0000", "US", NormalizedPhone("+16502530000", 1)),
        ("0412 345 678", "AU", NormalizedPhone("+61412345678", 61)),
        ("090-1234-5678", "JP", NormalizedPhone("+819012345678", 81)),
    ],
)
def test_other_country_numbers(raw: str, region: str, expected: NormalizedPhone) -> None:
    """第 2 条「会员改填的收货电话以收货国家作为默认区号解析自由文本」：
    其他国家的号码按收货国家或输入的国家码规范化为 E.164。
    """
    assert normalize_phone(raw, region) == expected


def test_possible_but_invalid_number_is_accepted() -> None:
    """第 2 条「只检查区号和长度等格式是否可能成立，不校验号码真实存在，以允许虚构演示资料」：
    北美号码的局号以 0 开头不会分配，phonenumbers 判为无效，但长度可能成立，应接受。
    """
    raw = "+1 650 053 0000"
    parsed = phonenumbers.parse(raw, None)
    assert not phonenumbers.is_valid_number(parsed)
    assert (
        phonenumbers.is_possible_number_with_reason(parsed)
        == phonenumbers.ValidationResult.IS_POSSIBLE
    )
    assert normalize_phone(raw, "US") == NormalizedPhone("+16500530000", 1)


def test_local_only_number_without_area_code_is_rejected() -> None:
    """第 2 条「检查区号和长度等格式是否可能成立」：
    默认地区为美国时缺区号的 7 位号码只能本地拨打，
    phonenumbers 的 is_possible_number 仍算它可能，但缺区号不能成立，应拒绝。
    """
    raw = "253-0000"
    parsed = phonenumbers.parse(raw, "US")
    assert (
        phonenumbers.is_possible_number_with_reason(parsed)
        == phonenumbers.ValidationResult.IS_POSSIBLE_LOCAL_ONLY
    )
    assert phonenumbers.is_possible_number(parsed)
    _assert_rejected(raw, "US")


@pytest.mark.parametrize(
    "raw",
    [
        "1",
        "012",
        "+60 12",
        "+65 812",
        "+60 12 3456 789012",
        "+65 8123 4567 8901",
        "0123456789012345",
    ],
)
def test_too_short_or_too_long_is_rejected(raw: str) -> None:
    """第 2 条「格式不成立则提示修改、不创建订单」：
    长度对所解析的国家码不可能成立的号码被拒。
    """
    _assert_rejected(raw, "MY")


@pytest.mark.parametrize(
    ("raw", "region"),
    [
        ("+1 800 FLOWERS", "US"),
        ("1-800-FLOWERS", "US"),
        ("012-345 678A", "MY"),
        ("+60 12-345 6789 ext 12", "MY"),
        ("+60 12-345 6789 x12", "MY"),
    ],
)
def test_letters_are_rejected(raw: str, region: str) -> None:
    """第 2 条「格式不成立则提示修改」：含字母即拒绝，不按键盘把字母转成数字。"""
    _assert_rejected(raw, region)


@pytest.mark.parametrize(
    "raw",
    [
        "012/345/6789",
        "012_345_6789",
        "012#3456789",
        "012*3456789",
        "012,3456789",
        "012\t3456789",
        "012\n3456789",
        "++60123456789",
        "60+123456789",
        "(+60) 12-345 6789",
        "＋60123456789",
        "０１２３４５６７８９",
        "012 3456789",
    ],
)
def test_illegal_characters_are_rejected(raw: str) -> None:
    """第 2 条「格式不成立则提示修改」：
    只允许 ASCII 数字、空格、连字符、点、括号与开头的一个加号。
    """
    _assert_rejected(raw, "MY")


@pytest.mark.parametrize("raw", ["", " ", "   \t  ", "\n"])
def test_empty_or_blank_is_rejected(raw: str) -> None:
    """第 2 条「格式不成立则提示修改、不创建订单」：空串与只有空白被拒。"""
    _assert_rejected(raw, "MY")


def test_length_limit_after_stripping() -> None:
    """第 2 条「格式不成立则提示修改」：去掉首尾空白后至多 32 个字符。"""
    exactly_32 = "012" + " " * 22 + "3456789"
    assert len(exactly_32) == 32
    assert normalize_phone(exactly_32, "MY") == NormalizedPhone(MY_E164, 60)
    padded = " " * 20 + "0123456789" + " " * 20
    assert normalize_phone(padded, "MY") == NormalizedPhone(MY_E164, 60)
    over_32 = "012" + " " * 23 + "3456789"
    assert len(over_32) == 33
    _assert_rejected(over_32, "MY")


@pytest.mark.parametrize("raw", [None, 60123456789, b"0123456789", ["0123456789"], 1.5])
def test_non_string_input_is_rejected(raw: object) -> None:
    """第 2 条「格式不成立则提示修改」：输入不是字符串即拒绝。"""
    _assert_rejected(raw, "MY")


@pytest.mark.parametrize("region", ["my", "Mys", "MYS", "M", "", "ZZ", "XX", "AA", "001", None, 60])
def test_illegal_or_unsupported_default_region_is_rejected(region: object) -> None:
    """第 1 条「服务端按所选国家码……规范化」与第 2 条「以收货国家作为默认区号」：
    默认地区须是两位大写且 phonenumbers 支持的地区，加号开头的输入也不例外。
    """
    _assert_rejected("012-345 6789", region)
    _assert_rejected("+60 12-345 6789", region)


def test_whitelist_true_for_malaysia_and_singapore() -> None:
    """「初期短信国家」：目的地白名单仅含马来西亚和新加坡。"""
    assert is_sms_whitelisted(MY_E164) is True
    assert is_sms_whitelisted(SG_E164) is True


@pytest.mark.parametrize(
    ("raw", "region"),
    [
        ("020 7946 0958", "GB"),
        ("(650) 253-0000", "US"),
        ("0412 345 678", "AU"),
        ("090-1234-5678", "JP"),
    ],
)
def test_whitelist_false_for_other_countries(raw: str, region: str) -> None:
    """「初期短信国家」：白名单仅含马来西亚和新加坡；
    第 1 条「号码不属于白名单时不发短信，以游客下单」。
    """
    assert is_sms_whitelisted(normalize_phone(raw, region).e164) is False


def test_whitelist_depends_only_on_the_number() -> None:
    """第 1 条「以规范化结果判定是否属于白名单；之后选择的收货国家不改变已判定的号码」：
    同一号码从不同默认地区规范化后判定结果相同。
    """
    for region in ("MY", "SG", "GB", "US"):
        assert is_sms_whitelisted(normalize_phone("+65 8123 4567", region).e164) is True
        assert is_sms_whitelisted(normalize_phone("+44 20 7946 0958", region).e164) is False


@pytest.mark.parametrize(
    "value",
    [
        "60123456789",
        "+60 12-345 6789",
        "+60-123456789",
        "012-345 6789",
        "",
        "+",
        "+60123456789\n",
        " +60123456789",
        "+６0123456789",
        "+0123456789",
        "+60123",
        None,
        60123456789,
    ],
)
def test_whitelist_rejects_non_e164_input(value: object) -> None:
    """第 1 条「以规范化结果判定是否属于白名单」：只接受规范化函数产出的 E.164 字符串，
    其余输入抛同一个异常，消息不含输入原文。
    """
    with pytest.raises(InvalidPhoneNumber) as excinfo:
        is_sms_whitelisted(value)
    message = str(excinfo.value)
    assert message == INVALID_MESSAGE
    if isinstance(value, str) and value.strip():
        assert value.strip() not in message

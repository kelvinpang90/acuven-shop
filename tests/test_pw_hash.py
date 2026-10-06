"""密码哈希与校验（app/services/pw_hash.py）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条（「锁定解除不绕过密码校验」
「单一管理员也须服务端授权」）与第 6 条（「应用日志……不记录……密码」），以及 docs/HANDOFF.md
记录的 Kelvin 2026-10-04 管理员登录决定与 2026-10-05 补充（「密码校验以假哈希对齐耗时」）。
每条测试的文档字符串写明它守住的设计原句或 Kelvin 的哪一项决定；设计没有直接原句的，写明
守住的是 SHOP-TASK-035 验收的哪一条。期望的格式与参数在这里另写，不取实现里的常量。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

import pytest

from app.services import pw_hash
from app.services.pw_hash import DUMMY_PASSWORD_HASH, hash_password, verify_password

PASSWORD = "correct horse battery"
OTHER = "correct horse battery!"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_same_password_hashes_differ_and_both_verify() -> None:
    """守住「单一管理员也须服务端授权」（Kelvin 2026-10-04：服务端按密码哈希校验）与验收
    「每个密码 16 字节随机盐」：同一密码两次哈希不同，两个都能通过校验。"""
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


def test_hash_is_self_describing_scrypt_with_required_parameters() -> None:
    """守住验收「hashlib.scrypt，N 为 2 的 14 次方、r 为 8、p 为 5，16 字节随机盐，
    自描述的哈希字符串（含算法名与参数），不超过 255 个字符」：按字符串里的盐与参数另算
    scrypt，结果与字符串里的摘要相同。"""
    encoded = hash_password(PASSWORD)
    algorithm, params, salt_text, digest_text = encoded.split("$")
    salt = _unb64(salt_text)
    digest = _unb64(digest_text)

    assert algorithm == "scrypt"
    assert params == "n=16384,r=8,p=5"
    assert len(salt) == 16
    assert len(encoded) <= 255
    expected = hashlib.scrypt(
        PASSWORD.encode(), salt=salt, n=2**14, r=8, p=5, maxmem=64 * 2**20, dklen=len(digest)
    )
    assert digest == expected


def test_wrong_password_does_not_verify() -> None:
    """守住「锁定解除不绕过密码校验」所依赖的校验本身：错误的密码（只差一个字符、大小写不同、
    空串）都不通过。"""
    encoded = hash_password(PASSWORD)

    assert not verify_password(OTHER, encoded)
    assert not verify_password(PASSWORD.upper(), encoded)
    assert not verify_password("", encoded)


# 以 PASSWORD 生成的一个合法哈希，下面的反例都由它改出来。
_GOOD = hash_password(PASSWORD)
_, _PARAMS, _SALT, _DIGEST = _GOOD.split("$")

MALFORMED = {
    "empty": "",
    "not-a-hash": "not a hash",
    "argon2": "$argon2id$v=19$m=65536,t=3,p=4$demo$demo",
    "other-algorithm": _GOOD.replace("scrypt$", "bcrypt$", 1),
    "missing-digest": f"scrypt${_PARAMS}${_SALT}",
    "extra-part": f"{_GOOD}$extra",
    "n-not-power-of-two": f"scrypt$n=16383,r=8,p=5${_SALT}${_DIGEST}",
    "n-zero": f"scrypt$n=0,r=8,p=5${_SALT}${_DIGEST}",
    "n-too-large": f"scrypt$n=1073741824,r=8,p=5${_SALT}${_DIGEST}",
    "r-zero": f"scrypt$n=16384,r=0,p=5${_SALT}${_DIGEST}",
    "salt-not-base64url": f"scrypt${_PARAMS}${_SALT}!${_DIGEST}",
    "salt-too-short": f"scrypt${_PARAMS}${_b64(b'short')}${_DIGEST}",
    "salt-too-long": f"scrypt${_PARAMS}${_SALT}a${_DIGEST}",
    "digest-too-short": f"scrypt${_PARAMS}${_SALT}${_b64(b'short')}",
    "too-long": _GOOD + "x" * 300,
}


@pytest.mark.parametrize("encoded", list(MALFORMED.values()), ids=list(MALFORMED))
def test_malformed_hash_returns_false_without_raising(encoded: str) -> None:
    """守住验收「对格式不合法的哈希返回不通过而不抛异常」：缺段、多段、算法名不对、参数不是
    2 的幂或越界、盐或摘要不是 Base64url 或长度不对、超长，都返回 False。"""
    assert verify_password(PASSWORD, _GOOD) is True
    assert verify_password(PASSWORD, encoded) is False


@pytest.mark.parametrize("encoded", [None, 123, b"scrypt$n=16384,r=8,p=5$a$b"])
def test_non_string_hash_returns_false(encoded: object) -> None:
    """守住验收「对格式不合法的哈希返回不通过而不抛异常」：账号表读出的值不是字符串也不抛。"""
    assert verify_password(PASSWORD, encoded) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("password", [PASSWORD, "", "x" * 256, "管理员密码", "\0"])
def test_dummy_hash_never_verifies(password: str) -> None:
    """守住 Kelvin 2026-10-05 补充「密码校验以假哈希对齐耗时」与验收「假哈希对任何密码都不
    通过」：用户名不存在时以假哈希校验，任何密码都不通过。"""
    assert verify_password(password, DUMMY_PASSWORD_HASH) is False


def test_dummy_hash_uses_the_same_parameters_as_real_hashes() -> None:
    """守住 Kelvin 2026-10-05 补充「用户名不存在与密码错误……密码校验以假哈希对齐耗时」：
    假哈希的格式与参数和真实哈希相同，校验时同样跑一次完整的 scrypt，而不是格式不合法、
    直接返回。"""
    real = hash_password(PASSWORD)
    dummy_parts = DUMMY_PASSWORD_HASH.split("$")
    real_parts = real.split("$")

    assert dummy_parts[:2] == real_parts[:2]
    assert [len(part) for part in dummy_parts] == [len(part) for part in real_parts]
    assert pw_hash._parse(DUMMY_PASSWORD_HASH) is not None


def test_dummy_hash_runs_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住 Kelvin 2026-10-05 补充「密码校验以假哈希对齐耗时」：以假哈希校验时与真实哈希
    一样以同样的参数调用一次 scrypt。"""
    calls: list[tuple[object, object, object]] = []
    real_scrypt = hashlib.scrypt

    def counting(password: bytes, **kwargs: Any) -> bytes:
        calls.append((kwargs["n"], kwargs["r"], kwargs["p"]))
        return real_scrypt(password, **kwargs)

    encoded = hash_password(PASSWORD)
    monkeypatch.setattr(pw_hash.hashlib, "scrypt", counting)
    verify_password(PASSWORD, DUMMY_PASSWORD_HASH)
    verify_password(OTHER, encoded)

    assert calls == [(2**14, 8, 5), (2**14, 8, 5)]


def test_unencodable_password_is_rejected_without_leaking() -> None:
    """守住「应用日志……不记录……密码」与验收「密码不出现在异常消息或 repr 里」：含孤立代理项
    的密码，哈希时抛的异常消息与异常链不含原文，校验时直接不通过。"""
    secret = "secret-\ud800-password"
    with pytest.raises(ValueError) as info:
        hash_password(secret)

    assert "secret" not in str(info.value)
    assert "secret" not in repr(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert verify_password(secret, hash_password(PASSWORD)) is False


def test_hash_does_not_contain_the_password() -> None:
    """守住「应用日志……不记录……密码」：哈希字符串里没有密码原文。"""
    encoded = hash_password(PASSWORD)

    assert PASSWORD not in encoded
    assert "horse" not in encoded


def test_module_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    """守住「应用日志……不记录……密码」与验收「模块不写日志」。"""
    with caplog.at_level(logging.DEBUG):
        encoded = hash_password(PASSWORD)
        verify_password(PASSWORD, encoded)
        verify_password(OTHER, encoded)
        verify_password(PASSWORD, "not a hash")
        verify_password(PASSWORD, DUMMY_PASSWORD_HASH)

    assert [r for r in caplog.records if r.name.startswith("app.")] == []

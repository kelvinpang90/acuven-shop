"""密码哈希与校验（标准库 hashlib.scrypt）。

依据 docs/DESIGN.md 1.11（提交 2d13250）「权限与资料保护」第 4 条（管理员登录；
锁定解除不绕过密码校验）与第 6 条（应用日志不记录密码），以及 docs/HANDOFF.md 记录的
Kelvin 2026-10-04 管理员登录决定。之后的会员密码（Member.password_hash）也复用本模块，
不另写一套哈希。

哈希字符串自描述，含算法名与参数，不超过 255 个字符（账号表 password_hash 的列长）：

    scrypt$n=16384,r=8,p=5$<16 字节盐的 Base64url>$<64 字节摘要的 Base64url>

（Base64url 均不带填充。）每个密码 16 字节随机盐，同一密码两次哈希不同。校验按哈希里
记下的参数重新计算，以常量时间比较；哈希格式不合法、参数越界或计算失败时返回不通过，
不抛异常。

DUMMY_PASSWORD_HASH 是一个固定的假哈希，参数与正常哈希相同，对任何密码都不通过：
用户名不存在时调用方照样以它跑一次 verify_password，使「用户名不存在」与「密码错误」
在密码校验上耗时相近。账号存在时接口层另写一条审计记录并提交，由此多出的耗时差是
Kelvin 2026-10-05 接受的用户名计时侧信道（单一管理员的用户名不作秘密，防线是至少
12 个字符的密码与按来源与用户名的锁定），本模块不另做时序对齐。

密码长度等规则由调用方决定（管理员命令见 app/admin.py），本模块只做哈希与校验。
本模块不写日志；密码不出现在异常消息、异常链或 repr 里。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets

ALGORITHM = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 5
SALT_BYTES = 16
DIGEST_BYTES = 64
MAX_HASH_LENGTH = 255

# 校验时接受的参数上限：超出即视为格式不合法，不按它分配内存（最多约 128 MiB）。
_MAX_LOG2_N = 16
_MAX_R = 16
_MAX_P = 16
# scrypt 约需 128 * r * N 字节；显式给出上限，参数上调时不撞 OpenSSL 默认的 32 MiB。
_MAXMEM = 128 * _MAX_R * 2**_MAX_LOG2_N + 2**20

_HASH_PATTERN = re.compile(
    r"scrypt\$n=([1-9][0-9]{0,7}),r=([1-9][0-9]{0,2}),p=([1-9][0-9]{0,2})"
    r"\$([A-Za-z0-9_-]+)\$([A-Za-z0-9_-]+)"
)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _encode(n: int, r: int, p: int, salt: bytes, digest: bytes) -> str:
    return f"{ALGORITHM}$n={n},r={r},p={p}${_b64encode(salt)}${_b64encode(digest)}"


def _password_bytes(password: str) -> bytes | None:
    """密码按 UTF-8 编码；不是字符串或含无法编码的字符（孤立代理项）时为空。

    UnicodeEncodeError 带着整个原文，这里不让它传出去。
    """
    if not isinstance(password, str):
        return None
    try:
        return password.encode("utf-8")
    except UnicodeEncodeError:
        return None


def _scrypt(password: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=DIGEST_BYTES)


def hash_password(password: str) -> str:
    """以新的 16 字节随机盐生成自描述的哈希字符串。

    密码不是字符串或无法按 UTF-8 编码时抛 ValueError，消息与异常链不含密码。
    """
    raw = _password_bytes(password)
    if raw is None:
        raise ValueError("password must be text encodable as UTF-8") from None
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(raw, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return _encode(SCRYPT_N, SCRYPT_R, SCRYPT_P, salt, digest)


def _parse(encoded: object) -> tuple[int, int, int, bytes, bytes] | None:
    if not isinstance(encoded, str) or len(encoded) > MAX_HASH_LENGTH:
        return None
    match = _HASH_PATTERN.fullmatch(encoded)
    if match is None:
        return None
    n, r, p = (int(match.group(i)) for i in (1, 2, 3))
    # N 须为大于 1 的 2 的幂。
    if n < 2 or n & (n - 1) or n > 2**_MAX_LOG2_N or r > _MAX_R or p > _MAX_P:
        return None
    try:
        salt = _b64decode(match.group(4))
        digest = _b64decode(match.group(5))
    except (binascii.Error, ValueError):
        return None
    if len(salt) != SALT_BYTES or len(digest) != DIGEST_BYTES:
        return None
    return n, r, p, salt, digest


def verify_password(password: str, encoded: str) -> bool:
    """密码是否与哈希相符：按哈希里的参数重新计算，以常量时间比较摘要。

    哈希格式不合法、参数越界、密码不是字符串或无法编码、计算失败，以及哈希是
    DUMMY_PASSWORD_HASH 时都返回 False，不抛异常。
    """
    parsed = _parse(encoded)
    raw = _password_bytes(password)
    if parsed is None or raw is None:
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = _scrypt(raw, salt, n, r, p)
    except (ValueError, MemoryError, OverflowError):
        return False
    matched = hmac.compare_digest(actual, expected)
    # 假哈希的摘要是固定的全零字节，本就算不出来；这里再明确排除一次。
    return matched and encoded != DUMMY_PASSWORD_HASH


# 固定的假哈希：参数与 hash_password 相同（校验耗时相同），盐与摘要是固定的全零字节。
DUMMY_PASSWORD_HASH = _encode(SCRYPT_N, SCRYPT_R, SCRYPT_P, bytes(SALT_BYTES), bytes(DIGEST_BYTES))

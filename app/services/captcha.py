"""人机挑战适配器：Cloudflare Turnstile 的令牌核验。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
「发送前采用托管人机挑战，并在后端核验令牌」；以及 docs/HANDOFF.md 0.41 记录的
Kelvin 2026-10-08 决定：「发短信前的托管人机挑战用 Cloudflare Turnstile，后端核验令牌时
不把访客来源地址交给它」，「人机挑战服务本身不可用（连不上或出错）按停发处理」，
「访客未通过人机挑战也不降级」，接口地址和凭据都是私有配置。

本模块只做一次 HTTP 调用与结果归类，不做限流与接口（SHOP-TASK-069、070）。
密钥与核验地址取自 SHOP_TURNSTILE_*；未配置时一律归为 unavailable、不发请求。
表单只有 secret 与 response 两项，不带 remoteip。传输函数与 sms_provider.py 共用：
标准库 urllib 发一次 HTTPS POST（表单编码），超时 5 秒，不重试。

本模块不写日志；令牌、密钥、请求与响应原文都不出现在异常消息、repr 或返回值里，
返回值只有类别。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from fastapi import Request

from app.core.config import Settings
from app.services.sms_provider import (
    HttpRequest,
    HttpResponse,
    Transport,
    TransportError,
    json_object,
    urllib_transport,
)

# Turnstile 令牌最长 2048 个字符（Cloudflare 服务端核验文档）；更长的不发请求，直接归为 failed。
MAX_TOKEN_LENGTH = 2048


class CaptchaResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


# 核验响应 success 为 false 时 error-codes 里的错误码。依据 Cloudflare Turnstile 服务端核验文档
# （Siteverify 的 Error codes）。令牌本身的问题归为 failed；密钥或请求的问题、提供方内部错误
# 归为 unavailable（此时任何访客都过不了，等同服务不可用）。两类同时出现时按 unavailable；
# 不在表里的错误码或错误码为空时也按 unavailable。
ERROR_CODES: dict[str, CaptchaResult] = {
    "missing-input-response": CaptchaResult.FAILED,  # 令牌缺失
    "invalid-input-response": CaptchaResult.FAILED,  # 令牌无效、格式不对或已过期
    "timeout-or-duplicate": CaptchaResult.FAILED,  # 令牌已核验过（每个令牌只能用一次）或过期
    "missing-input-secret": CaptchaResult.UNAVAILABLE,  # 没带密钥
    "invalid-input-secret": CaptchaResult.UNAVAILABLE,  # 密钥无效或不存在
    "bad-request": CaptchaResult.UNAVAILABLE,  # 请求格式不对
    "internal-error": CaptchaResult.UNAVAILABLE,  # 提供方内部错误
}


class CaptchaVerifier(Protocol):
    def verify(self, token: str | None) -> CaptchaResult: ...


class TurnstileVerifier:
    """Cloudflare Turnstile。密钥为空或核验地址不是 https:// 时视为未配置。"""

    def __init__(
        self, secret_key: str, verify_url: str, transport: Transport = urllib_transport
    ) -> None:
        self._secret_key = secret_key
        self._verify_url = verify_url
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: Transport = urllib_transport
    ) -> TurnstileVerifier:
        return cls(settings.turnstile_secret_key, settings.turnstile_verify_url, transport)

    @property
    def configured(self) -> bool:
        return bool(self._secret_key and self._verify_url.startswith("https://"))

    def __repr__(self) -> str:
        return f"TurnstileVerifier(configured={self.configured})"

    def verify(self, token: str | None) -> CaptchaResult:
        """核验访客从页面带来的令牌。令牌缺失、为空或超过 MAX_TOKEN_LENGTH 时归为 failed、
        不发请求。
        """
        if not self.configured:
            return CaptchaResult.UNAVAILABLE
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            return CaptchaResult.FAILED
        response = self._post({"secret": self._secret_key, "response": token})
        if response is None or not 200 <= response.status < 300:
            return CaptchaResult.UNAVAILABLE
        data = json_object(response.body)
        if data is None:
            return CaptchaResult.UNAVAILABLE
        success = data.get("success")
        if success is True:
            return CaptchaResult.PASSED
        if success is not False:
            return CaptchaResult.UNAVAILABLE
        codes = data.get("error-codes")
        if not isinstance(codes, list) or not codes:
            return CaptchaResult.UNAVAILABLE
        results = {ERROR_CODES.get(c) if isinstance(c, str) else None for c in codes}
        if results == {CaptchaResult.FAILED}:
            return CaptchaResult.FAILED
        return CaptchaResult.UNAVAILABLE

    def _post(self, form: dict[str, str]) -> HttpResponse | None:
        try:
            return self._transport(HttpRequest(self._verify_url, form))
        except (TransportError, OSError):
            return None


class FakeCaptchaVerifier:
    """测试替身：按预设结果返回、只记调用次数，不发网络请求、不记令牌。"""

    def __init__(self, result: CaptchaResult = CaptchaResult.PASSED) -> None:
        self.result = result
        self.calls = 0

    def __repr__(self) -> str:
        return f"FakeCaptchaVerifier(calls={self.calls})"

    def verify(self, token: str | None) -> CaptchaResult:
        self.calls += 1
        return self.result


def get_captcha_verifier(request: Request) -> CaptchaVerifier:
    """FastAPI 依赖：按应用的 Settings 构造 Turnstile 适配器（未配置也返回，调用时归为
    unavailable）。测试用 app.dependency_overrides[get_captcha_verifier] 换成
    FakeCaptchaVerifier。
    """
    return TurnstileVerifier.from_settings(request.app.state.settings)

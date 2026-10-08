"""短信验证服务适配器：Twilio Verify v2 的发起与核验。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
「使用 Twilio Verify，由提供方负责验证码生命周期及防欺诈；系统只存验证请求与结果，不存验证码」，
「不得把验证码、短信凭据或完整手机号写进日志」；以及 docs/HANDOFF.md 0.41 记录的
Kelvin 2026-10-08 决定（服务商的接口地址和凭据都是私有配置）。

本模块只做两次 HTTP 调用与结果归类，不做限流、预算、验证记录与接口（SHOP-TASK-068 到 070）。
凭据取自 SHOP_TWILIO_*；未配置时一律归为 unavailable、不发请求。每次调用只发一次 HTTPS POST
（表单编码），不重试。HTTP 经可注入的传输函数（Transport），默认的 urllib_transport 只用标准库；
captcha.py 共用这里的传输函数。

本模块不写日志；手机号、验证码、凭据、请求与响应原文都不出现在异常消息、repr 或返回值里，
返回值只有类别与提供方请求 ID。
"""

from __future__ import annotations

import base64
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from fastapi import Request

from app.core.config import Settings

# urllib 的 timeout 是套接字超时：建立连接与之后每一次读取各自最多等 5 秒。
TIMEOUT_SECONDS = 5

# 响应体最多读这么多字节；截断后的 JSON 解析失败，归为 unavailable。
MAX_RESPONSE_BYTES = 64 * 1024

_E164_PATTERN = re.compile(r"\+[1-9][0-9]{1,14}")
# Twilio 的验证请求 ID：VE 加 32 位十六进制。
_VERIFICATION_SID_PATTERN = re.compile(r"VE[0-9a-fA-F]{32}")
# Twilio Verify 的验证码长度可设 4–10 位（默认 6 位），只含数字。
_CODE_PATTERN = re.compile(r"[0-9]{4,10}")


# ---------------------------------------------------------------- 传输


@dataclass(frozen=True)
class HttpRequest:
    """一次表单编码的 HTTPS POST。各字段可能含凭据、号码或验证码，都不进 repr。"""

    url: str = field(repr=False)
    form: Mapping[str, str] = field(repr=False)
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    """任何 HTTP 状态码的响应（含非 2xx）。响应体不进 repr。"""

    status: int
    body: bytes = field(repr=False, default=b"")


class TransportError(RuntimeError):
    """超时、网络错误或地址不是 https://。消息固定，不含地址或请求内容。"""

    def __init__(self) -> None:
        super().__init__("provider request failed")


# 传输函数：发出请求并返回响应（非 2xx 也返回），连不上或超时抛 TransportError。
Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 不跟随重定向：跟随会把认证头带到另一个地址。3xx 原样作为响应返回，归为 unavailable。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """默认传输：标准库 urllib 发一次 POST，超时 TIMEOUT_SECONDS，不重试、不跟随重定向。"""
    if not request.url.startswith("https://"):
        raise TransportError()
    data = urllib.parse.urlencode(dict(request.form)).encode("ascii")
    headers = {
        **request.headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    req = urllib.request.Request(request.url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            return HttpResponse(resp.status, resp.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_RESPONSE_BYTES)
        except (OSError, http.client.HTTPException):
            body = b""
        finally:
            exc.close()
        return HttpResponse(exc.code, body)
    except (OSError, http.client.HTTPException, ValueError):
        # 异常链里可能带地址，from None 不带出去。
        raise TransportError() from None


def json_object(body: bytes) -> dict[str, object] | None:
    """响应体解析为 JSON 对象；不是 JSON 或不是对象时为 None。"""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _int_field(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ---------------------------------------------------------------- 结果类别


class SendStatus(StrEnum):
    ACCEPTED = "accepted"
    UNDELIVERABLE = "undeliverable"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    # 提供方的验证请求 ID（VE…），只在 accepted 时有。
    request_id: str | None = None


class CheckStatus(StrEnum):
    APPROVED = "approved"
    WRONG_CODE = "wrong_code"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


_UNAVAILABLE_SEND = SendResult(SendStatus.UNAVAILABLE)

# 发起验证（POST /v2/Services/{ServiceSid}/Verifications）的非 2xx 响应里的 Twilio 错误码。
# 依据 Twilio 错误码字典（https://www.twilio.com/docs/api/errors）。不在表里的错误码、没有错误码
# 或 5xx 一律归为 unavailable（停发），不猜测为号码问题。
START_ERROR_CODES: Mapping[int, SendStatus] = {
    # 号码无效：本模块只传 To 与固定为 sms 的 Channel，参数无效只会因 To 而来。
    21211: SendStatus.UNDELIVERABLE,  # Invalid 'To' Phone Number
    60200: SendStatus.UNDELIVERABLE,  # Invalid parameter（Verify 对无效的 To 返回此码）
    # 不能收短信。
    21612: SendStatus.UNDELIVERABLE,  # The 'To' phone number is not currently reachable via SMS
    21614: SendStatus.UNDELIVERABLE,  # 'To' number is not a valid mobile number
    60205: SendStatus.UNDELIVERABLE,  # SMS is not supported by landline phone number
    # 不支持该目的地。
    21408: SendStatus.UNDELIVERABLE,  # SMS permission not enabled for the region（地区权限）
    60410: SendStatus.UNDELIVERABLE,  # Verification delivery attempt blocked（目的地前缀被拦截）
    60605: SendStatus.UNDELIVERABLE,  # Verification delivery attempt blocked: geo permissions
    # 认证失败、配置错误与提供方限流：停发。
    20003: SendStatus.UNAVAILABLE,  # Authentication Error
    20404: SendStatus.UNAVAILABLE,  # Resource not found（Verify 服务 SID 不对）
    20429: SendStatus.UNAVAILABLE,  # Too Many Requests
    60203: SendStatus.UNAVAILABLE,  # Max send attempts reached（提供方对该号码限流）
}

# 核验（POST /v2/Services/{ServiceSid}/VerificationCheck）的 2xx 响应里验证的 status。
# 依据 Twilio Verify 文档（Verification Check 资源）：status 为 pending 表示验证码不对、验证仍待
# 核验；approved 为通过；其余为验证已结束、不能再核验。不在表里的状态归为 unavailable。
CHECK_STATUSES: Mapping[str, CheckStatus] = {
    "approved": CheckStatus.APPROVED,
    "pending": CheckStatus.WRONG_CODE,
    "canceled": CheckStatus.EXPIRED,
    "expired": CheckStatus.EXPIRED,
    "max_attempts_reached": CheckStatus.EXPIRED,
    "deleted": CheckStatus.EXPIRED,
    "failed": CheckStatus.EXPIRED,
}

# 核验的非 2xx 响应里的 Twilio 错误码。Twilio 在验证通过、过期（默认 10 分钟）或尝试次数用尽后
# 删除该验证，再核验即 20404，所以 20404 归为 expired。不在表里的错误码、没有错误码或 5xx
# 一律归为 unavailable。
CHECK_ERROR_CODES: Mapping[int, CheckStatus] = {
    20404: CheckStatus.EXPIRED,  # Resource not found（验证已结束或不存在）
    60202: CheckStatus.EXPIRED,  # Max check attempts reached
    60023: CheckStatus.EXPIRED,  # No pending verifications found
    20003: CheckStatus.UNAVAILABLE,  # Authentication Error
    20429: CheckStatus.UNAVAILABLE,  # Too Many Requests
}


# ---------------------------------------------------------------- 适配器


class SmsProvider(Protocol):
    def start_verification(self, phone_e164: str) -> SendResult: ...

    def check_verification(self, request_id: str, code: str) -> CheckStatus: ...


class TwilioVerifyClient:
    """Twilio Verify v2。四项配置缺一或接口地址不是 https:// 时视为未配置。"""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        service_sid: str,
        base_url: str,
        transport: Transport = urllib_transport,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._service_sid = service_sid
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: Transport = urllib_transport
    ) -> TwilioVerifyClient:
        return cls(
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_verify_service_sid,
            settings.twilio_verify_base_url,
            transport,
        )

    @property
    def configured(self) -> bool:
        return bool(
            self._account_sid
            and self._auth_token
            and self._service_sid
            and self._base_url.startswith("https://")
        )

    def __repr__(self) -> str:
        return f"TwilioVerifyClient(configured={self.configured})"

    def start_verification(self, phone_e164: str) -> SendResult:
        """对规范化 E.164 号码发起短信验证（Channel 为 sms）。

        号码不是 E.164 时抛 ValueError（调用方须先规范化；消息不含号码）。
        """
        if not self.configured:
            return _UNAVAILABLE_SEND
        if not isinstance(phone_e164, str) or not _E164_PATTERN.fullmatch(phone_e164):
            raise ValueError("phone must be a normalized E.164 number")
        response = self._post("Verifications", {"To": phone_e164, "Channel": "sms"})
        if response is None or response.status >= 500:
            return _UNAVAILABLE_SEND
        data = json_object(response.body)
        if 200 <= response.status < 300:
            if data is None or data.get("status") != "pending":
                return _UNAVAILABLE_SEND
            sid = data.get("sid")
            if not isinstance(sid, str) or not _VERIFICATION_SID_PATTERN.fullmatch(sid):
                return _UNAVAILABLE_SEND
            return SendResult(SendStatus.ACCEPTED, sid)
        error_code = None if data is None else _int_field(data, "code")
        if error_code is None:
            return _UNAVAILABLE_SEND
        if START_ERROR_CODES.get(error_code) is SendStatus.UNDELIVERABLE:
            # 提供方拒绝时没有验证请求，不带请求 ID。
            return SendResult(SendStatus.UNDELIVERABLE)
        return _UNAVAILABLE_SEND

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        """按验证请求 ID 加验证码核验。

        请求 ID 格式不对归为 expired（找不到），验证码不是 4–10 位数字归为 wrong_code，
        这两种都不发请求。
        """
        if not self.configured:
            return CheckStatus.UNAVAILABLE
        if not isinstance(request_id, str) or not _VERIFICATION_SID_PATTERN.fullmatch(request_id):
            return CheckStatus.EXPIRED
        if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
            return CheckStatus.WRONG_CODE
        response = self._post("VerificationCheck", {"VerificationSid": request_id, "Code": code})
        if response is None or response.status >= 500:
            return CheckStatus.UNAVAILABLE
        data = json_object(response.body)
        if data is None:
            return CheckStatus.UNAVAILABLE
        if 200 <= response.status < 300:
            status = data.get("status")
            result = CHECK_STATUSES.get(status) if isinstance(status, str) else None
            if result is None:
                return CheckStatus.UNAVAILABLE
            # 通过须同时 valid 为 true；两者不一致时不放行。
            if result is CheckStatus.APPROVED and data.get("valid") is not True:
                return CheckStatus.UNAVAILABLE
            return result
        error_code = _int_field(data, "code")
        if error_code is None:
            return CheckStatus.UNAVAILABLE
        return CHECK_ERROR_CODES.get(error_code, CheckStatus.UNAVAILABLE)

    def _post(self, resource: str, form: Mapping[str, str]) -> HttpResponse | None:
        service = urllib.parse.quote(self._service_sid, safe="")
        url = f"{self._base_url}/v2/Services/{service}/{resource}"
        credentials = f"{self._account_sid}:{self._auth_token}".encode()
        headers = {"Authorization": "Basic " + base64.b64encode(credentials).decode("ascii")}
        try:
            return self._transport(HttpRequest(url, form, headers))
        except (TransportError, OSError):
            return None


FAKE_ACCEPTED = SendResult(SendStatus.ACCEPTED, "VE" + "0" * 32)


class FakeSmsProvider:
    """测试替身：按预设结果返回、只记调用次数，不发网络请求、不记号码与验证码。"""

    def __init__(
        self,
        send_result: SendResult = FAKE_ACCEPTED,
        check_result: CheckStatus = CheckStatus.APPROVED,
    ) -> None:
        self.send_result = send_result
        self.check_result = check_result
        self.send_calls = 0
        self.check_calls = 0

    def __repr__(self) -> str:
        return f"FakeSmsProvider(send_calls={self.send_calls}, check_calls={self.check_calls})"

    def start_verification(self, phone_e164: str) -> SendResult:
        self.send_calls += 1
        return self.send_result

    def check_verification(self, request_id: str, code: str) -> CheckStatus:
        self.check_calls += 1
        return self.check_result


def get_sms_provider(request: Request) -> SmsProvider:
    """FastAPI 依赖：按应用的 Settings 构造 Twilio Verify 适配器（未配置也返回，调用时归为
    unavailable）。测试用 app.dependency_overrides[get_sms_provider] 换成 FakeSmsProvider。
    """
    return TwilioVerifyClient.from_settings(request.app.state.settings)

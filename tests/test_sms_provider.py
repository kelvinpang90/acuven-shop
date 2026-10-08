"""短信验证服务适配器：Twilio Verify v2 的发起、核验与默认传输。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
「使用 Twilio Verify，由提供方负责验证码生命周期及防欺诈；系统只存验证请求与结果，不存验证码」，
「不得把验证码、短信凭据或完整手机号写进日志」，第 2 条「白名单号码的短信无法送达或短信服务停发，
允许改为游客下单，但验证码错误或超过尝试次数不降级为游客」；以及 docs/HANDOFF.md 0.41 记录的
Kelvin 2026-10-08 决定「服务商与 Turnstile 的接口地址和凭据都是私有配置」。
每条测试的文档字符串引用它守住的那一句；设计没有直接原句的，写明守住的是 SHOP-TASK-067
验收的哪一条。

不发真实网络请求：适配器的测试用本文件的 RecordingTransport 代替传输函数；默认传输的测试把
模块里的 opener 换成本文件的 FakeOpener。期望的认证头在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.error
import urllib.parse
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services import sms_provider
from app.services.sms_provider import (
    CheckStatus,
    FakeSmsProvider,
    HttpRequest,
    HttpResponse,
    SendResult,
    SendStatus,
    SmsProvider,
    TransportError,
    TwilioVerifyClient,
    get_sms_provider,
    urllib_transport,
)

ACCOUNT_SID = "AC" + "a1" * 16
AUTH_TOKEN = "hunter2-auth-token"
SERVICE_SID = "VA" + "b2" * 16
BASE_URL = "https://verify.example.test"
PHONE = "+60123456789"
CODE = "482915"
VERIFICATION_SID = "VE" + "c3" * 16
EXPECTED_AUTH = "Basic " + base64.b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()

SECRETS = [PHONE, PHONE[1:], CODE, ACCOUNT_SID, AUTH_TOKEN, EXPECTED_AUTH.split()[1]]


class RecordingTransport:
    """记录收到的请求，按顺序返回预设响应或抛预设异常。"""

    def __init__(self, *outcomes: HttpResponse | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def respond(status: int, payload: object) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode())


def twilio_error(status: int, code: int) -> HttpResponse:
    return respond(status, {"code": code, "message": "error", "status": status})


def client(transport: RecordingTransport, **overrides: str) -> TwilioVerifyClient:
    values = {
        "account_sid": ACCOUNT_SID,
        "auth_token": AUTH_TOKEN,
        "service_sid": SERVICE_SID,
        "base_url": BASE_URL,
        **overrides,
    }
    return TwilioVerifyClient(transport=transport, **values)


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


ACCEPTED_BODY = {"sid": VERIFICATION_SID, "status": "pending", "to": PHONE, "channel": "sms"}


# ---------------------------------------------------------------- 发起：请求


def test_start_posts_form_to_verifications_with_basic_auth() -> None:
    """守住「使用 Twilio Verify」与验收第 1 条「对规范化 E.164 号码发起短信验证
    （Channel 为 sms）」：向 /v2/Services/{服务 SID}/Verifications 发 To 与 Channel=sms，
    以账号 SID 与令牌作 Basic 认证。
    """
    transport = RecordingTransport(respond(201, ACCEPTED_BODY))
    client(transport).start_verification(PHONE)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == f"{BASE_URL}/v2/Services/{SERVICE_SID}/Verifications"
    assert dict(request.form) == {"To": PHONE, "Channel": "sms"}
    assert dict(request.headers) == {"Authorization": EXPECTED_AUTH}


def test_start_trailing_slash_in_base_url_is_not_doubled() -> None:
    """守住验收第 4 条：接口地址按配置项拼接（运营者填写时末尾可能带斜杠）。"""
    transport = RecordingTransport(respond(201, ACCEPTED_BODY))
    client(transport, base_url=BASE_URL + "/").start_verification(PHONE)
    assert transport.requests[0].url == f"{BASE_URL}/v2/Services/{SERVICE_SID}/Verifications"


# ---------------------------------------------------------------- 发起：归类


def test_start_accepted_returns_provider_request_id_only() -> None:
    """守住「系统只存验证请求与结果，不存验证码」：受理时只返回类别与提供方请求 ID。"""
    transport = RecordingTransport(respond(201, ACCEPTED_BODY))
    result = client(transport).start_verification(PHONE)
    assert result == SendResult(SendStatus.ACCEPTED, VERIFICATION_SID)


@pytest.mark.parametrize("code", [21211, 60200, 21612, 21614, 60205, 21408, 60410, 60605])
def test_start_rejected_number_is_undeliverable(code: int) -> None:
    """守住第 2 条「白名单号码的短信无法送达……允许改为游客下单」：
    提供方以号码无效、不能收短信或不支持该目的地拒绝时归为 undeliverable，没有请求 ID。
    """
    transport = RecordingTransport(twilio_error(400, code))
    result = client(transport).start_verification(PHONE)
    assert result == SendResult(SendStatus.UNDELIVERABLE, None)


@pytest.mark.parametrize(
    "response",
    [
        twilio_error(401, 20003),  # 认证失败
        twilio_error(404, 20404),  # 服务 SID 不对
        twilio_error(429, 20429),  # 提供方限流
        twilio_error(429, 60203),  # 提供方对号码限流
        twilio_error(400, 99999),  # 表外错误码
        twilio_error(500, 21211),  # 5xx 即使带了号码错误码
        HttpResponse(502, b"<html>bad gateway</html>"),
        HttpResponse(503, b""),
        HttpResponse(400, b"not json"),  # 非 JSON
        respond(400, {"message": "no code"}),  # 缺 code
        respond(400, {"code": "21211"}),  # code 不是整数
        respond(400, ["not", "an", "object"]),
        HttpResponse(302, b""),  # 不跟随的重定向
        HttpResponse(201, b"not json"),
        respond(201, {"status": "pending"}),  # 缺 sid
        respond(201, {"sid": "not-a-verification-sid", "status": "pending"}),
        respond(201, {"sid": VERIFICATION_SID}),  # 缺 status
        respond(201, {"sid": VERIFICATION_SID, "status": "canceled"}),
    ],
)
def test_start_provider_failures_are_unavailable(response: HttpResponse) -> None:
    """守住第 2 条「短信服务停发，允许改为游客下单」与验收第 1 条：认证失败、限流、5xx、
    非 JSON、缺字段与表外错误码一律归为 unavailable（停发），不当作受理或号码问题。
    """
    transport = RecordingTransport(response)
    result = client(transport).start_verification(PHONE)
    assert result == SendResult(SendStatus.UNAVAILABLE, None)


@pytest.mark.parametrize(
    "error",
    [
        TransportError(),
        TimeoutError("timed out"),
        urllib.error.URLError("connection refused"),
        ConnectionResetError(),
    ],
)
def test_start_timeout_or_network_error_is_unavailable(error: BaseException) -> None:
    """守住验收第 1 条「unavailable（……超时、网络错误……）」与第 3 条「不重试」：
    只调用一次。
    """
    transport = RecordingTransport(error, respond(201, ACCEPTED_BODY))
    result = client(transport).start_verification(PHONE)
    assert result.status is SendStatus.UNAVAILABLE
    assert len(transport.requests) == 1


@pytest.mark.parametrize("phone", ["60123456789", "+60 12-345 6789", "+0123", "+1234567890123456"])
def test_start_rejects_unnormalized_phone_without_request(phone: str) -> None:
    """守住验收第 1 条「对规范化 E.164 号码发起」：不是 E.164 时报编程错误、不发请求，
    消息不含号码（「不得把……完整手机号写进日志」）。
    """
    transport = RecordingTransport()
    with pytest.raises(ValueError) as excinfo:
        client(transport).start_verification(phone)
    assert transport.requests == []
    assert phone not in str(excinfo.value)


# ---------------------------------------------------------------- 核验：请求


def test_check_posts_sid_and_code_to_verification_check() -> None:
    """守住「由提供方负责验证码生命周期」与验收第 1 条「按验证请求 ID 加验证码核验」：
    向 /v2/Services/{服务 SID}/VerificationCheck 发 VerificationSid 与 Code，
    同样以 Basic 认证。
    """
    transport = RecordingTransport(respond(200, {"status": "approved", "valid": True}))
    client(transport).check_verification(VERIFICATION_SID, CODE)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == f"{BASE_URL}/v2/Services/{SERVICE_SID}/VerificationCheck"
    assert dict(request.form) == {"VerificationSid": VERIFICATION_SID, "Code": CODE}
    assert dict(request.headers) == {"Authorization": EXPECTED_AUTH}


# ---------------------------------------------------------------- 核验：归类


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "approved", "valid": True}, CheckStatus.APPROVED),
        ({"status": "pending", "valid": False}, CheckStatus.WRONG_CODE),
        ({"status": "canceled", "valid": False}, CheckStatus.EXPIRED),
        ({"status": "expired", "valid": False}, CheckStatus.EXPIRED),
        ({"status": "max_attempts_reached", "valid": False}, CheckStatus.EXPIRED),
        ({"status": "deleted", "valid": False}, CheckStatus.EXPIRED),
        ({"status": "failed", "valid": False}, CheckStatus.EXPIRED),
    ],
)
def test_check_status_mapping(payload: dict[str, object], expected: CheckStatus) -> None:
    """守住第 2 条「验证码错误或超过尝试次数不降级为游客」：仍待核验为 wrong_code，已结束的
    验证为 expired，两者都不是 unavailable（停发才降级）；只有 approved 才算通过。
    """
    transport = RecordingTransport(respond(200, payload))
    assert client(transport).check_verification(VERIFICATION_SID, CODE) is expected


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (twilio_error(404, 20404), CheckStatus.EXPIRED),  # 已结束或找不到
        (twilio_error(429, 60202), CheckStatus.EXPIRED),  # 尝试次数用尽
        (twilio_error(404, 60023), CheckStatus.EXPIRED),  # 没有待核验的验证
        (twilio_error(401, 20003), CheckStatus.UNAVAILABLE),
        (twilio_error(429, 20429), CheckStatus.UNAVAILABLE),
        (twilio_error(400, 60200), CheckStatus.UNAVAILABLE),  # 表外错误码
        (twilio_error(500, 20404), CheckStatus.UNAVAILABLE),
        (HttpResponse(503, b""), CheckStatus.UNAVAILABLE),
        (HttpResponse(404, b"not json"), CheckStatus.UNAVAILABLE),
        (respond(404, {"message": "no code"}), CheckStatus.UNAVAILABLE),
        (HttpResponse(200, b"not json"), CheckStatus.UNAVAILABLE),
        (respond(200, {"valid": True}), CheckStatus.UNAVAILABLE),  # 缺 status
        (respond(200, {"status": "approved"}), CheckStatus.UNAVAILABLE),  # 缺 valid
        (respond(200, {"status": "approved", "valid": False}), CheckStatus.UNAVAILABLE),
        (respond(200, {"status": "something-new", "valid": True}), CheckStatus.UNAVAILABLE),
        (respond(200, {"status": 1}), CheckStatus.UNAVAILABLE),
    ],
)
def test_check_error_mapping(response: HttpResponse, expected: CheckStatus) -> None:
    """守住第 2 条「超过尝试次数不降级为游客」：尝试次数用尽、已过期或找不到归为 expired；
    认证失败、限流、5xx、非 JSON、缺字段与表外错误码归为 unavailable；不完整的通过不放行。
    """
    transport = RecordingTransport(response)
    assert client(transport).check_verification(VERIFICATION_SID, CODE) is expected


@pytest.mark.parametrize("error", [TransportError(), TimeoutError(), urllib.error.URLError("x")])
def test_check_timeout_or_network_error_is_unavailable(error: BaseException) -> None:
    """守住验收第 1 条「unavailable（……超时、网络错误……）」与第 3 条「不重试」。"""
    transport = RecordingTransport(error, respond(200, {"status": "approved", "valid": True}))
    assert client(transport).check_verification(VERIFICATION_SID, CODE) is CheckStatus.UNAVAILABLE
    assert len(transport.requests) == 1


@pytest.mark.parametrize("request_id", ["", "VE123", "VA" + "c3" * 16, "VE" + "zz" * 16])
def test_check_malformed_request_id_is_expired_without_request(request_id: str) -> None:
    """守住验收第 1 条「expired（……找不到）」：请求 ID 不可能存在时不发请求。"""
    transport = RecordingTransport()
    result = client(transport).check_verification(request_id, CODE)
    assert result is CheckStatus.EXPIRED
    assert transport.requests == []


@pytest.mark.parametrize("code", ["", "123", "12345678901", "48291a", " 482915"])
def test_check_malformed_code_is_wrong_code_without_request(code: str) -> None:
    """守住第 2 条「验证码错误……不降级为游客」：
    不是 4–10 位数字的验证码为 wrong_code、不发请求。
    """
    transport = RecordingTransport()
    result = client(transport).check_verification(VERIFICATION_SID, code)
    assert result is CheckStatus.WRONG_CODE
    assert transport.requests == []


# ---------------------------------------------------------------- 未配置


@pytest.mark.parametrize(
    "overrides",
    [
        {"account_sid": ""},
        {"auth_token": ""},
        {"service_sid": ""},
        {"base_url": ""},
        {"base_url": "http://verify.example.test"},
    ],
)
def test_unconfigured_is_unavailable_without_calling_transport(overrides: dict[str, str]) -> None:
    """守住 Kelvin 2026-10-08「接口地址和凭据都是私有配置」与验收第 4 条
    「未配置时调用一律归为 unavailable、不发请求」：四项缺一或地址不是 https://
    都不调用传输函数，号码不合格也不报错。
    """
    transport = RecordingTransport()
    provider = client(transport, **overrides)
    assert provider.configured is False
    assert provider.start_verification(PHONE) == SendResult(SendStatus.UNAVAILABLE)
    assert provider.start_verification("not-a-phone").status is SendStatus.UNAVAILABLE
    assert provider.check_verification(VERIFICATION_SID, CODE) is CheckStatus.UNAVAILABLE
    assert transport.requests == []


def test_default_settings_are_unconfigured() -> None:
    """守住验收第 4 条「默认值都是空串即未配置」：六个配置项的默认值为空串。"""
    settings = Settings(_env_file=None)
    assert settings.twilio_account_sid == ""
    assert settings.twilio_auth_token == ""
    assert settings.twilio_verify_service_sid == ""
    assert settings.twilio_verify_base_url == ""
    assert settings.turnstile_secret_key == ""
    assert settings.turnstile_verify_url == ""
    assert TwilioVerifyClient.from_settings(settings).configured is False


def test_settings_read_from_shop_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 4 条「前缀 SHOP_」：配置项取自 .env.example 登记的 SHOP_TWILIO_* 变量名。"""
    monkeypatch.setenv("SHOP_TWILIO_ACCOUNT_SID", ACCOUNT_SID)
    monkeypatch.setenv("SHOP_TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("SHOP_TWILIO_VERIFY_SERVICE_SID", SERVICE_SID)
    monkeypatch.setenv("SHOP_TWILIO_VERIFY_BASE_URL", BASE_URL)
    settings = Settings(_env_file=None)
    transport = RecordingTransport(respond(201, ACCEPTED_BODY))
    provider = TwilioVerifyClient.from_settings(settings, transport)
    assert provider.configured is True
    assert provider.start_verification(PHONE).status is SendStatus.ACCEPTED
    assert transport.requests[0].headers["Authorization"] == EXPECTED_AUTH


# ---------------------------------------------------------------- 不泄露


def test_repr_and_results_contain_no_secrets() -> None:
    """守住「不得把验证码、短信凭据或完整手机号写进日志」：适配器、请求、响应与结果的 repr
    不含号码、验证码或凭据。
    """
    transport = RecordingTransport(respond(201, ACCEPTED_BODY), twilio_error(400, 21211))
    provider = client(transport)
    accepted = provider.start_verification(PHONE)
    undeliverable = provider.start_verification(PHONE)
    texts = [
        repr(provider),
        str(provider),
        repr(accepted),
        repr(undeliverable),
        repr(transport.requests[0]),
        repr(HttpResponse(201, json.dumps(ACCEPTED_BODY).encode())),
        repr(FakeSmsProvider()),
        str(TransportError()),
    ]
    for text in texts:
        assert_no_secrets(text)
    assert "configured=True" in repr(provider)


def test_transport_exception_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「不得把……短信凭据或完整手机号写进日志」：传输函数的异常
    （其消息可能含地址或请求）不从适配器抛出，调用方只看到类别。
    """
    transport = RecordingTransport(TimeoutError(f"{BASE_URL} {PHONE} {AUTH_TOKEN}"))
    assert client(transport).start_verification(PHONE).status is SendStatus.UNAVAILABLE


def test_adapter_writes_no_logs(caplog: pytest.LogCaptureFixture) -> None:
    """守住「不得把验证码、短信凭据或完整手机号写进日志」：本模块不产生任何日志记录。"""
    transport = RecordingTransport(
        respond(201, ACCEPTED_BODY),
        twilio_error(500, 20500),
        respond(200, {"status": "pending", "valid": False}),
        TimeoutError(),
    )
    provider = client(transport)
    with caplog.at_level(logging.DEBUG):
        provider.start_verification(PHONE)
        provider.start_verification(PHONE)
        provider.check_verification(VERIFICATION_SID, CODE)
        provider.check_verification(VERIFICATION_SID, CODE)
    assert caplog.records == []


# ---------------------------------------------------------------- 替身与依赖


def test_fake_returns_presets_and_counts_calls() -> None:
    """守住验收第 3 条「测试替身（按预设结果返回、记录调用次数，不发网络请求）」。"""
    fake = FakeSmsProvider(
        send_result=SendResult(SendStatus.UNDELIVERABLE), check_result=CheckStatus.EXPIRED
    )
    assert fake.start_verification(PHONE) == SendResult(SendStatus.UNDELIVERABLE)
    assert fake.check_verification(VERIFICATION_SID, CODE) is CheckStatus.EXPIRED
    assert fake.check_verification(VERIFICATION_SID, CODE) is CheckStatus.EXPIRED
    assert (fake.send_calls, fake.check_calls) == (1, 2)

    default = FakeSmsProvider()
    assert default.start_verification(PHONE).status is SendStatus.ACCEPTED
    assert default.check_verification(VERIFICATION_SID, CODE) is CheckStatus.APPROVED


def test_get_sms_provider_builds_from_app_settings() -> None:
    """守住验收第 3 条「按配置构造实例的函数」：取应用的 Settings，未配置时也返回实例。"""
    configured = Settings(
        _env_file=None,
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token=AUTH_TOKEN,
        twilio_verify_service_sid=SERVICE_SID,
        twilio_verify_base_url=BASE_URL,
    )
    for settings, expected in [(configured, True), (Settings(_env_file=None), False)]:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        provider = get_sms_provider(request)
        assert isinstance(provider, TwilioVerifyClient)
        assert provider.configured is expected


def test_get_sms_provider_can_be_overridden() -> None:
    """守住验收第 3 条「供 SHOP-TASK-070 用作 FastAPI 依赖并在测试中覆盖」。"""
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)

    @app.post("/probe")
    def probe(provider: Annotated[SmsProvider, Depends(get_sms_provider)]) -> dict[str, str]:
        return {"status": provider.start_verification(PHONE).status.value}

    fake = FakeSmsProvider()
    app.dependency_overrides[get_sms_provider] = lambda: fake
    with TestClient(app) as http:
        assert http.post("/probe").json() == {"status": "accepted"}
    assert fake.send_calls == 1

    app.dependency_overrides.clear()
    with TestClient(app) as http:
        assert http.post("/probe").json() == {"status": "unavailable"}


# ---------------------------------------------------------------- 默认传输（urllib）


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeOpener:
    """代替模块的 urllib opener：记录 open 的请求与超时，返回预设响应或抛预设异常。"""

    def __init__(self, outcome: FakeHttpResponse | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[tuple[object, float]] = []

    def open(self, request: object, timeout: float) -> FakeHttpResponse:
        self.calls.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


FORM_REQUEST = HttpRequest(
    f"{BASE_URL}/v2/Services/{SERVICE_SID}/Verifications",
    {"To": PHONE, "Channel": "sms"},
    {"Authorization": EXPECTED_AUTH},
)


def test_urllib_transport_posts_form_once_with_five_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守住验收第 3 条「只用标准库 urllib 发 HTTPS POST（表单编码），连接与读取超时各 5 秒，
    不重试」：一次 POST，表单编码的请求体与内容类型，认证头原样带上，超时 5 秒。
    """
    opener = FakeOpener(FakeHttpResponse(201, b'{"sid": "x"}'))
    monkeypatch.setattr(sms_provider, "_OPENER", opener)

    response = urllib_transport(FORM_REQUEST)

    assert response == HttpResponse(201, b'{"sid": "x"}')
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert timeout == 5
    assert request.get_method() == "POST"
    assert request.full_url == FORM_REQUEST.url
    assert urllib.parse.parse_qs(request.data.decode("ascii")) == {
        "To": [PHONE],
        "Channel": ["sms"],
    }
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert request.get_header("Authorization") == EXPECTED_AUTH


def test_urllib_transport_returns_non_2xx_as_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 1 条：非 2xx 的状态码与响应体交给适配器按错误码归类，
    而不是当作网络错误。
    """
    error = urllib.error.HTTPError(
        FORM_REQUEST.url, 400, "Bad Request", None, io.BytesIO(b'{"code": 21211}')
    )
    monkeypatch.setattr(sms_provider, "_OPENER", FakeOpener(error))
    assert urllib_transport(FORM_REQUEST) == HttpResponse(400, b'{"code": 21211}')


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        urllib.error.URLError(f"cannot reach {BASE_URL}"),
        ConnectionRefusedError(),
    ],
)
def test_urllib_transport_network_failure_raises_fixed_transport_error(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """守住「不得把……短信凭据……写进日志」与验收第 3 条：超时与网络错误抛消息固定的
    TransportError，不带原异常链（其中可能有地址）。
    """
    monkeypatch.setattr(sms_provider, "_OPENER", FakeOpener(error))
    with pytest.raises(TransportError) as excinfo:
        urllib_transport(FORM_REQUEST)
    assert str(excinfo.value) == "provider request failed"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_urllib_transport_refuses_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 3 条「发 HTTPS POST」：地址不是 https:// 时不发请求（凭据不走明文）。"""
    opener = FakeOpener(FakeHttpResponse(200, b"{}"))
    monkeypatch.setattr(sms_provider, "_OPENER", opener)
    with pytest.raises(TransportError):
        urllib_transport(HttpRequest("http://verify.example.test/v2", {"To": PHONE}))
    assert opener.calls == []


def test_urllib_transport_does_not_follow_redirects() -> None:
    """守住验收第 3 条「HTTPS POST」与凭据不外泄：opener 不跟随重定向（跟随会把认证头
    带到别的地址），3xx 作为响应返回、由适配器归为 unavailable。
    """
    handlers = [h for h in sms_provider._OPENER.handlers if isinstance(h, sms_provider._NoRedirect)]
    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None

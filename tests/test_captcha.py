"""人机挑战适配器：Cloudflare Turnstile 的令牌核验。

依据 docs/DESIGN.md 1.11（提交 2d13250）「失败、并发与重试」第 4 条：
「发送前采用托管人机挑战，并在后端核验令牌」；以及 docs/HANDOFF.md 0.41 记录的
Kelvin 2026-10-08 决定：「发短信前的托管人机挑战用 Cloudflare Turnstile，后端核验令牌时
不把访客来源地址交给它」，「访客未通过人机挑战也不降级」，「人机挑战服务本身不可用
（连不上或出错）按停发处理」，「服务商与 Turnstile 的接口地址和凭据都是私有配置」。
每条测试的文档字符串引用它守住的那一句；没有直接原句的，写明守住的是 SHOP-TASK-067
验收的哪一条。

不发真实网络请求：用本文件的 RecordingTransport 代替传输函数。默认的 urllib 传输与
sms_provider.py 共用，其测试见 tests/test_sms_provider.py。
"""

from __future__ import annotations

import json
import logging
import urllib.error
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services.captcha import (
    MAX_TOKEN_LENGTH,
    CaptchaResult,
    CaptchaVerifier,
    FakeCaptchaVerifier,
    TurnstileVerifier,
    get_captcha_verifier,
)
from app.services.sms_provider import HttpRequest, HttpResponse, TransportError

SECRET_KEY = "0x4AAA-hunter2-turnstile-secret"
VERIFY_URL = "https://challenge.example.test/turnstile/v0/siteverify"
TOKEN = "XXXX.DUMMY.TOKEN.visitor-token-value"


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


def verifier(transport: RecordingTransport, **overrides: str) -> TurnstileVerifier:
    values = {"secret_key": SECRET_KEY, "verify_url": VERIFY_URL, **overrides}
    return TurnstileVerifier(transport=transport, **values)


PASSED_BODY = {
    "success": True,
    "challenge_ts": "2026-10-08T00:00:00.000Z",
    "hostname": "shop.example.test",
    "error-codes": [],
}


def failed_body(*codes: str) -> dict[str, object]:
    return {"success": False, "error-codes": list(codes)}


# ---------------------------------------------------------------- 请求


def test_posts_secret_and_token_only() -> None:
    """守住 Kelvin 2026-10-08「后端核验令牌时不把访客来源地址交给它」与设计
    「在后端核验令牌」：向配置的核验地址只发 secret 与 response 两项，没有 remoteip，
    也没有别的请求头。
    """
    transport = RecordingTransport(respond(200, PASSED_BODY))
    verifier(transport).verify(TOKEN)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == VERIFY_URL
    assert dict(request.form) == {"secret": SECRET_KEY, "response": TOKEN}
    assert "remoteip" not in request.form
    assert dict(request.headers) == {}


# ---------------------------------------------------------------- 归类


def test_success_is_passed() -> None:
    """守住「发送前采用托管人机挑战，并在后端核验令牌」：提供方确认令牌有效时为 passed。"""
    transport = RecordingTransport(respond(200, PASSED_BODY))
    assert verifier(transport).verify(TOKEN) is CaptchaResult.PASSED


@pytest.mark.parametrize(
    "codes",
    [
        ("invalid-input-response",),
        ("timeout-or-duplicate",),
        ("missing-input-response",),
        ("invalid-input-response", "timeout-or-duplicate"),
    ],
)
def test_rejected_token_is_failed(codes: tuple[str, ...]) -> None:
    """守住 Kelvin 2026-10-08「访客未通过人机挑战也不降级」：令牌无效、已用过或缺失为 failed，
    不是 unavailable（停发才可改游客下单）。
    """
    transport = RecordingTransport(respond(200, failed_body(*codes)))
    assert verifier(transport).verify(TOKEN) is CaptchaResult.FAILED


@pytest.mark.parametrize(
    "token", [None, "", "x" * (MAX_TOKEN_LENGTH + 1), 12345], ids=["none", "empty", "long", "int"]
)
def test_missing_or_oversized_token_is_failed_without_request(token: object) -> None:
    """守住验收第 2 条「failed（令牌缺失、过长……）」：这些情形不发请求。"""
    transport = RecordingTransport()
    assert verifier(transport).verify(token) is CaptchaResult.FAILED
    assert transport.requests == []


def test_token_at_max_length_is_sent() -> None:
    """守住验收第 2 条「过长」的边界：恰为 2048 个字符的令牌照常核验。"""
    token = "t" * MAX_TOKEN_LENGTH
    transport = RecordingTransport(respond(200, PASSED_BODY))
    assert verifier(transport).verify(token) is CaptchaResult.PASSED
    assert transport.requests[0].form["response"] == token


@pytest.mark.parametrize(
    "response",
    [
        respond(200, failed_body("invalid-input-secret")),
        respond(200, failed_body("missing-input-secret")),
        respond(200, failed_body("internal-error")),
        respond(200, failed_body("bad-request")),
        respond(200, failed_body("invalid-input-response", "invalid-input-secret")),
        respond(200, failed_body("something-new")),
        respond(200, failed_body()),  # 错误码为空
        respond(200, {"success": False}),  # 缺 error-codes
        respond(200, {"success": False, "error-codes": "invalid-input-response"}),
        respond(200, {"hostname": "shop.example.test"}),  # 缺 success
        respond(200, {"success": "true"}),  # success 不是布尔值
        respond(200, ["not", "an", "object"]),
        HttpResponse(200, b"not json"),
        HttpResponse(200, b""),
        respond(400, PASSED_BODY),  # 非 2xx 即使响应体写着成功
        HttpResponse(302, b""),
        HttpResponse(500, b"<html>error</html>"),
        HttpResponse(503, b""),
    ],
)
def test_provider_problems_are_unavailable(response: HttpResponse) -> None:
    """守住 Kelvin 2026-10-08「人机挑战服务本身不可用（连不上或出错）按停发处理」：
    密钥问题、提供方内部错误、非 2xx、非 JSON、缺字段与表外错误码一律为 unavailable。
    """
    transport = RecordingTransport(response)
    assert verifier(transport).verify(TOKEN) is CaptchaResult.UNAVAILABLE


@pytest.mark.parametrize(
    "error", [TransportError(), TimeoutError(), urllib.error.URLError("x"), ConnectionError()]
)
def test_timeout_or_network_error_is_unavailable_without_retry(error: BaseException) -> None:
    """守住 Kelvin 2026-10-08「人机挑战服务本身不可用（连不上或出错）按停发处理」
    与验收第 3 条「不重试」：超时与网络错误为 unavailable，只调用一次。
    """
    transport = RecordingTransport(error, respond(200, PASSED_BODY))
    assert verifier(transport).verify(TOKEN) is CaptchaResult.UNAVAILABLE
    assert len(transport.requests) == 1


# ---------------------------------------------------------------- 未配置


@pytest.mark.parametrize(
    "overrides",
    [
        {"secret_key": ""},
        {"verify_url": ""},
        {"verify_url": "http://challenge.example.test/turnstile/v0/siteverify"},
    ],
)
def test_unconfigured_is_unavailable_without_calling_transport(overrides: dict[str, str]) -> None:
    """守住 Kelvin 2026-10-08「接口地址和凭据都是私有配置」与验收第 4 条
    「未配置时调用一律归为 unavailable、不发请求」：缺密钥、缺地址或地址不是 https://
    时都不调用传输函数，令牌缺失也归为 unavailable。
    """
    transport = RecordingTransport()
    captcha = verifier(transport, **overrides)
    assert captcha.configured is False
    assert captcha.verify(TOKEN) is CaptchaResult.UNAVAILABLE
    assert captcha.verify(None) is CaptchaResult.UNAVAILABLE
    assert transport.requests == []


def test_settings_read_from_shop_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 4 条「前缀 SHOP_」：配置项取自 SHOP_TURNSTILE_SECRET_KEY 与
    SHOP_TURNSTILE_VERIFY_URL，默认空串即未配置。
    """
    assert TurnstileVerifier.from_settings(Settings(_env_file=None)).configured is False
    monkeypatch.setenv("SHOP_TURNSTILE_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("SHOP_TURNSTILE_VERIFY_URL", VERIFY_URL)
    transport = RecordingTransport(respond(200, PASSED_BODY))
    captcha = TurnstileVerifier.from_settings(Settings(_env_file=None), transport)
    assert captcha.configured is True
    assert captcha.verify(TOKEN) is CaptchaResult.PASSED
    assert transport.requests[0].url == VERIFY_URL


# ---------------------------------------------------------------- 不泄露


def test_repr_contains_no_token_or_secret() -> None:
    """守住验收第 5 条「令牌、凭据、请求与响应原文不出现在……repr 或返回值里」。"""
    transport = RecordingTransport(respond(200, PASSED_BODY))
    captcha = verifier(transport)
    result = captcha.verify(TOKEN)
    texts = [
        repr(captcha),
        str(captcha),
        repr(result),
        str(result),
        repr(transport.requests[0]),
        repr(FakeCaptchaVerifier()),
    ]
    for text in texts:
        assert TOKEN not in text
        assert SECRET_KEY not in text
        assert VERIFY_URL not in text
    assert "configured=True" in repr(captcha)


def test_verifier_writes_no_logs(caplog: pytest.LogCaptureFixture) -> None:
    """守住验收第 5 条「不写日志」。"""
    transport = RecordingTransport(
        respond(200, PASSED_BODY),
        respond(200, failed_body("timeout-or-duplicate")),
        TimeoutError(),
    )
    captcha = verifier(transport)
    with caplog.at_level(logging.DEBUG):
        captcha.verify(TOKEN)
        captcha.verify(TOKEN)
        captcha.verify(TOKEN)
    assert caplog.records == []


# ---------------------------------------------------------------- 替身与依赖


def test_fake_returns_preset_and_counts_calls() -> None:
    """守住验收第 3 条「测试替身（按预设结果返回、记录调用次数，不发网络请求）」。"""
    fake = FakeCaptchaVerifier(CaptchaResult.FAILED)
    assert fake.verify(TOKEN) is CaptchaResult.FAILED
    assert fake.verify(None) is CaptchaResult.FAILED
    assert fake.calls == 2
    assert FakeCaptchaVerifier().verify(TOKEN) is CaptchaResult.PASSED


def test_get_captcha_verifier_builds_from_app_settings() -> None:
    """守住验收第 3 条「按配置构造实例的函数」：取应用的 Settings，未配置时也返回实例。"""
    configured = Settings(
        _env_file=None, turnstile_secret_key=SECRET_KEY, turnstile_verify_url=VERIFY_URL
    )
    for settings, expected in [(configured, True), (Settings(_env_file=None), False)]:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        captcha = get_captcha_verifier(request)
        assert isinstance(captcha, TurnstileVerifier)
        assert captcha.configured is expected


def test_get_captcha_verifier_can_be_overridden() -> None:
    """守住验收第 3 条「供 SHOP-TASK-070 用作 FastAPI 依赖并在测试中覆盖」。"""
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)

    @app.post("/probe")
    def probe(dep: Annotated[CaptchaVerifier, Depends(get_captcha_verifier)]) -> dict[str, str]:
        return {"result": dep.verify(TOKEN).value}

    fake = FakeCaptchaVerifier(CaptchaResult.FAILED)
    app.dependency_overrides[get_captcha_verifier] = lambda: fake
    with TestClient(app) as http:
        assert http.post("/probe").json() == {"result": "failed"}
    assert fake.calls == 1

    app.dependency_overrides.clear()
    with TestClient(app) as http:
        assert http.post("/probe").json() == {"result": "unavailable"}

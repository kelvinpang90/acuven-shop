"""AI 消息客户端：Billing Hub 的 POST /v1/messages 与默认传输。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条。
「链路」：「后端经 ai_billing_hub 的 POST /v1/messages 调用模型，
以本项目在 Billing Hub 的身份计量；接口地址、项目密钥与模型名经私有配置注入，
仓库只记变量名。未配置时后台的 AI 按钮禁用并说明原因」；
「限制」：「单次调用 60 秒超时，不自动重试」
「每次调用记时间、结果类别与用量（输入与输出 token 数），不记描述原文与模型回答原文」
「Billing Hub 余额不足、超时或出错时回答「AI 暂不可用」」。
每条测试的文档字符串引用它守住的那一句。

不发真实网络请求：客户端的测试用本文件的 RecordingTransport 代替传输函数；
默认传输的测试把模块里的 opener 换成本文件的 FakeOpener。
期望的请求头与请求体在这里另写，不取实现里的函数。
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from app.core.config import Settings
from app.services import ai_hub
from app.services.ai_hub import (
    AiHubClient,
    AiNotConfigured,
    AiReply,
    AiTimeout,
    AiUnavailable,
    HttpRequest,
    HttpResponse,
    TransportError,
    TransportTimeout,
    urllib_transport,
)

BASE_URL = "https://hub.example.test"
CLIENT_ID = "shop-client-7f3a"
ACCESS_SECRET = "hunter2-project-key"
MODEL = "model-code-x"
SYSTEM = "You propose festive decorations."
USER_TEXT = "Hari Raya sale, green and gold"
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
REPLY_TEXT = '{"title": "Selamat Hari Raya"}'

SECRETS = [ACCESS_SECRET, f"Bearer {ACCESS_SECRET}"]
CONTENT = [SYSTEM, USER_TEXT, REPLY_TEXT]


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


OK_BODY = {"text": REPLY_TEXT, "usage": {"input_tokens": 321, "output_tokens": 45}}


def client(transport: RecordingTransport, **overrides: str) -> AiHubClient:
    values = {
        "base_url": BASE_URL,
        "client_id": CLIENT_ID,
        "access_secret": ACCESS_SECRET,
        "model": MODEL,
        **overrides,
    }
    return AiHubClient(transport=transport, **values)


def call(provider: AiHubClient, **kwargs: object) -> AiReply:
    return provider.create_message(SYSTEM, USER_TEXT, max_tokens=800, **kwargs)


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS + CONTENT:
        assert secret not in text


# ---------------------------------------------------------------- 请求


def test_posts_once_to_v1_messages_with_client_id_and_bearer() -> None:
    """守住「后端经 ai_billing_hub 的 POST /v1/messages 调用模型，
    以本项目在 Billing Hub 的身份计量」：向 {接口地址}/v1/messages 发一次请求，
    带 X-Acuven-Client-Id 与 Bearer 项目密钥两个请求头。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    call(client(transport))

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == f"{BASE_URL}/v1/messages"
    assert dict(request.headers) == {
        "X-Acuven-Client-Id": CLIENT_ID,
        "Authorization": f"Bearer {ACCESS_SECRET}",
    }


def test_trailing_slash_in_base_url_is_not_doubled() -> None:
    """守住「接口地址……经私有配置注入」：
    运营者填写的地址末尾带斜杠时，去掉后再接 /v1/messages。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    call(client(transport, base_url=BASE_URL + "//"))
    assert transport.requests[0].url == f"{BASE_URL}/v1/messages"


def test_body_has_only_documented_fields_and_fixed_feature() -> None:
    """守住「经 ai_billing_hub 的 POST /v1/messages 调用模型」
    （Billing Hub 请求体严格、未知字段回 422）与「系统不从订单、会员或收货资料取任何内容送出」：
    请求体只有 feature、model、system、messages 与 max_tokens，
    feature 固定为 shop.promotion_proposal，messages 只有一条用户文字，
    内容只来自调用方传入的参数。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    call(client(transport))

    body = json.loads(transport.requests[0].body)
    assert body == {
        "feature": "shop.promotion_proposal",
        "model": MODEL,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": USER_TEXT}],
        "max_tokens": 800,
    }


def test_output_schema_is_sent_as_json_schema_format() -> None:
    """守住「校验：要求模型只回一个 JSON 对象」：给了 output_schema 时
    请求体另带 output_config.format，type 为 json_schema、schema 为该 JSON Schema 对象，
    其余字段不变。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    call(client(transport), output_schema=SCHEMA)

    body = json.loads(transport.requests[0].body)
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert set(body) == {"feature", "model", "system", "messages", "max_tokens", "output_config"}


# ---------------------------------------------------------------- 成功


def test_200_returns_text_and_token_usage() -> None:
    """守住「每次调用记时间、结果类别与用量（输入与输出 token 数）」：
    200 的响应解析为 AiReply(text, input_tokens, output_tokens)，供调用方记用量。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    reply = call(client(transport), output_schema=SCHEMA)
    assert reply == AiReply(REPLY_TEXT, 321, 45)


def test_200_with_extra_response_fields_is_accepted() -> None:
    """守住「后端经 ai_billing_hub 的 POST /v1/messages 调用模型」：
    响应里约定之外的字段（如 Billing Hub 的计量信息）不影响解析。
    """
    payload = {**OK_BODY, "id": "msg_1", "usage": {**OK_BODY["usage"], "cost": 12}}
    transport = RecordingTransport(respond(200, payload))
    assert call(client(transport)) == AiReply(REPLY_TEXT, 321, 45)


# ---------------------------------------------------------------- 失败


@pytest.mark.parametrize(
    "error",
    [TransportTimeout(), TimeoutError("timed out")],
    ids=["transport_timeout", "socket_timeout"],
)
def test_timeout_raises_ai_timeout_without_retry(error: BaseException) -> None:
    """守住「单次调用 60 秒超时，不自动重试」：超时抛 AiTimeout，只发过一次请求。"""
    transport = RecordingTransport(error, respond(200, OK_BODY))
    with pytest.raises(AiTimeout):
        call(client(transport))
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "error",
    [TransportError(), ConnectionRefusedError(), OSError("network unreachable")],
    ids=["transport_error", "refused", "os_error"],
)
def test_network_failure_raises_unavailable_without_retry(error: BaseException) -> None:
    """守住「Billing Hub……出错时回答「AI 暂不可用」」与「不自动重试」：
    连不上抛 AiUnavailable，只发过一次请求。
    """
    transport = RecordingTransport(error, respond(200, OK_BODY))
    with pytest.raises(AiUnavailable):
        call(client(transport))
    assert len(transport.requests) == 1


@pytest.mark.parametrize("status", [201, 204, 302, 400, 401, 402, 403, 422, 429, 500, 503])
def test_non_200_raises_unavailable_without_retry(status: int) -> None:
    """守住「Billing Hub 余额不足、超时或出错时回答「AI 暂不可用」」与「不自动重试」：
    200 以外的状态码（含余额不足、认证失败、请求体被拒与重定向）一律抛 AiUnavailable，
    不再发第二次。
    """
    transport = RecordingTransport(respond(status, OK_BODY), respond(200, OK_BODY))
    with pytest.raises(AiUnavailable):
        call(client(transport))
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"text": REPLY_TEXT}).encode(),
        json.dumps({"text": REPLY_TEXT, "usage": None}).encode(),
        json.dumps({"text": REPLY_TEXT, "usage": {"input_tokens": 3}}).encode(),
        json.dumps({"text": REPLY_TEXT, "usage": {"output_tokens": 3}}).encode(),
        json.dumps({"text": {"title": "x"}, "usage": OK_BODY["usage"]}).encode(),
        json.dumps({"text": None, "usage": OK_BODY["usage"]}).encode(),
        json.dumps({"usage": OK_BODY["usage"]}).encode(),
        json.dumps(
            {"text": REPLY_TEXT, "usage": {"input_tokens": "3", "output_tokens": 4}}
        ).encode(),
        json.dumps(
            {"text": REPLY_TEXT, "usage": {"input_tokens": 3, "output_tokens": 4.0}}
        ).encode(),
        json.dumps(
            {"text": REPLY_TEXT, "usage": {"input_tokens": True, "output_tokens": 4}}
        ).encode(),
        json.dumps([OK_BODY]).encode(),
        b"not json",
        b"",
        json.dumps(OK_BODY).encode()[:-5],
    ],
    ids=[
        "missing_usage",
        "usage_null",
        "missing_output_tokens",
        "missing_input_tokens",
        "text_object",
        "text_null",
        "missing_text",
        "tokens_string",
        "tokens_float",
        "tokens_bool",
        "array",
        "not_json",
        "empty",
        "truncated",
    ],
)
def test_malformed_200_raises_unavailable(body: bytes) -> None:
    """守住「Billing Hub……出错时回答「AI 暂不可用」」：200 但响应不是约定的 JSON
    （缺 usage、text 不是字符串、token 数不是整数等）抛 AiUnavailable，不猜测用量。
    """
    transport = RecordingTransport(HttpResponse(200, body))
    with pytest.raises(AiUnavailable):
        call(client(transport))
    assert len(transport.requests) == 1


# ---------------------------------------------------------------- 未配置


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": ""},
        {"client_id": ""},
        {"access_secret": ""},
        {"model": ""},
        {"base_url": "http://hub.example.test"},
        {"base_url": "hub.example.test"},
        {"base_url": "https://"},
    ],
    ids=["no_url", "no_client_id", "no_secret", "no_model", "http", "no_scheme", "scheme_only"],
)
def test_unconfigured_raises_without_calling_transport(overrides: dict[str, str]) -> None:
    """守住「接口地址、项目密钥与模型名经私有配置注入」「未配置时后台的 AI 按钮禁用」：
    四项缺一或地址不以 https:// 开头即未配置，configured 为 False，
    调用抛 AiNotConfigured 且不发请求。
    """
    transport = RecordingTransport()
    provider = client(transport, **overrides)
    assert provider.configured is False
    with pytest.raises(AiNotConfigured):
        call(provider, output_schema=SCHEMA)
    assert transport.requests == []


def test_default_settings_are_unconfigured() -> None:
    """守住「仓库只记变量名」与「未配置时后台的 AI 按钮禁用」：
    Settings 的默认值不含任何地址或密钥（四项为空串），按默认值构造的客户端未配置；
    每日调用上限默认 50。
    """
    settings = Settings(_env_file=None)
    assert settings.ai_hub_base_url == ""
    assert settings.ai_hub_client_id == ""
    assert settings.ai_hub_access_secret == ""
    assert settings.ai_model == ""
    assert settings.ai_daily_call_limit == 50
    transport = RecordingTransport()
    provider = AiHubClient.from_settings(settings, transport)
    assert provider.configured is False
    with pytest.raises(AiNotConfigured):
        call(provider)
    assert transport.requests == []


def test_settings_read_from_shop_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「接口地址、项目密钥与模型名经私有配置注入，仓库只记变量名」：
    配置项取自 .env.example 登记的 SHOP_AI_* 变量名。
    """
    monkeypatch.setenv("SHOP_AI_HUB_BASE_URL", BASE_URL)
    monkeypatch.setenv("SHOP_AI_HUB_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SHOP_AI_HUB_ACCESS_SECRET", ACCESS_SECRET)
    monkeypatch.setenv("SHOP_AI_MODEL", MODEL)
    monkeypatch.setenv("SHOP_AI_DAILY_CALL_LIMIT", "7")
    settings = Settings(_env_file=None)
    assert settings.ai_daily_call_limit == 7
    transport = RecordingTransport(respond(200, OK_BODY))
    provider = AiHubClient.from_settings(settings, transport)
    assert provider.configured is True
    assert call(provider) == AiReply(REPLY_TEXT, 321, 45)
    request = transport.requests[0]
    assert request.url == f"{BASE_URL}/v1/messages"
    assert request.headers["X-Acuven-Client-Id"] == CLIENT_ID
    assert request.headers["Authorization"] == f"Bearer {ACCESS_SECRET}"
    assert json.loads(request.body)["model"] == MODEL


# ---------------------------------------------------------------- 不泄露


def test_repr_shows_only_configured() -> None:
    """守住「项目密钥……经私有配置注入」与「不记描述原文与模型回答原文」：
    客户端的 repr 只显示 configured；请求、响应与结果的 repr 不含密钥、地址、
    提示、描述或模型回答原文。
    """
    transport = RecordingTransport(respond(200, OK_BODY))
    provider = client(transport)
    reply = call(provider, output_schema=SCHEMA)
    assert repr(provider) == "AiHubClient(configured=True)"
    assert str(provider) == "AiHubClient(configured=True)"
    assert repr(client(RecordingTransport(), model="")) == "AiHubClient(configured=False)"
    for text in [repr(reply), repr(transport.requests[0]), repr(respond(200, OK_BODY))]:
        assert_no_secrets(text)
        assert CLIENT_ID not in text
        assert BASE_URL not in text


@pytest.mark.parametrize(
    "outcome",
    [
        TimeoutError(f"{BASE_URL} {ACCESS_SECRET} {USER_TEXT}"),
        OSError(f"{BASE_URL} {ACCESS_SECRET} {USER_TEXT}"),
        respond(401, {"error": f"bad key {ACCESS_SECRET}", "echo": USER_TEXT}),
        respond(200, {"text": REPLY_TEXT, "detail": ACCESS_SECRET}),
    ],
    ids=["timeout", "os_error", "non_200_echo", "malformed_echo"],
)
def test_exceptions_carry_no_secret_or_response_content(
    outcome: HttpResponse | BaseException,
) -> None:
    """守住「项目密钥……经私有配置注入」与「不记描述原文与模型回答原文」：
    抛出的异常消息固定，不带传输异常的原文、响应内容、密钥或请求内容，
    也不带原异常链。
    """
    transport = RecordingTransport(outcome)
    with pytest.raises((AiTimeout, AiUnavailable)) as excinfo:
        call(client(transport))
    assert_no_secrets(str(excinfo.value))
    assert_no_secrets(repr(excinfo.value))
    assert BASE_URL not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True or excinfo.value.__context__ is None


def test_not_configured_message_has_no_values() -> None:
    """守住「未配置时后台的 AI 按钮禁用并说明原因」与「仓库只记变量名」：
    AiNotConfigured 的消息固定，不带已配置的那几项的值。
    """
    with pytest.raises(AiNotConfigured) as excinfo:
        call(client(RecordingTransport(), base_url="http://hub.example.test"))
    assert_no_secrets(str(excinfo.value))
    assert CLIENT_ID not in str(excinfo.value)
    assert "hub.example.test" not in str(excinfo.value)


def test_client_writes_no_logs(caplog: pytest.LogCaptureFixture) -> None:
    """守住「不记描述原文与模型回答原文」：本模块成功与各种失败时都不产生日志记录
    （日志由调用方只记结果类别与用量）。
    """
    transport = RecordingTransport(
        respond(200, OK_BODY),
        respond(500, {"error": "x"}),
        TimeoutError(),
        HttpResponse(200, b"not json"),
    )
    provider = client(transport)
    with caplog.at_level(logging.DEBUG):
        call(provider)
        for _ in range(3):
            with pytest.raises((AiTimeout, AiUnavailable)):
                call(provider)
        with pytest.raises(AiNotConfigured):
            call(client(RecordingTransport(), access_secret=""))
    assert caplog.records == []


# ---------------------------------------------------------------- 默认传输（urllib）


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.read_amounts: list[int] = []

    def read(self, amount: int = -1) -> bytes:
        self.read_amounts.append(amount)
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


JSON_BODY = json.dumps({"feature": "shop.promotion_proposal", "model": MODEL}).encode()
JSON_REQUEST = HttpRequest(
    f"{BASE_URL}/v1/messages",
    JSON_BODY,
    {"X-Acuven-Client-Id": CLIENT_ID, "Authorization": f"Bearer {ACCESS_SECRET}"},
)


def test_urllib_transport_posts_json_once_with_sixty_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守住「单次调用 60 秒超时，不自动重试」与「经 ai_billing_hub 的 POST /v1/messages」：
    一次 POST，JSON 请求体与 application/json 内容类型，两个请求头原样带上，
    超时 60 秒，响应最多读 64 KB。
    """
    response = FakeHttpResponse(200, b'{"text": "x"}')
    opener = FakeOpener(response)
    monkeypatch.setattr(ai_hub, "_OPENER", opener)

    assert urllib_transport(JSON_REQUEST) == HttpResponse(200, b'{"text": "x"}')
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert timeout == 60
    assert request.get_method() == "POST"
    assert request.full_url == f"{BASE_URL}/v1/messages"
    assert request.data == JSON_BODY
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-acuven-client-id") == CLIENT_ID
    assert request.get_header("Authorization") == f"Bearer {ACCESS_SECRET}"
    assert response.read_amounts == [64 * 1024]


def test_urllib_transport_returns_non_2xx_as_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「Billing Hub 余额不足……时回答「AI 暂不可用」」：
    非 2xx 作为响应交给客户端归类，而不是当作网络错误。
    """
    error = urllib.error.HTTPError(
        JSON_REQUEST.url, 402, "Payment Required", None, io.BytesIO(b'{"error": "balance"}')
    )
    monkeypatch.setattr(ai_hub, "_OPENER", FakeOpener(error))
    assert urllib_transport(JSON_REQUEST) == HttpResponse(402, b'{"error": "balance"}')


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))],
    ids=["socket_timeout", "wrapped_timeout"],
)
def test_urllib_transport_timeout_raises_transport_timeout(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """守住「单次调用 60 秒超时」：套接字超时（含被 URLError 包着的连接超时）
    抛 TransportTimeout，经客户端为 AiTimeout，每次调用只开一次连接。
    """
    opener = FakeOpener(error)
    monkeypatch.setattr(ai_hub, "_OPENER", opener)
    with pytest.raises(TransportTimeout) as excinfo:
        urllib_transport(JSON_REQUEST)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True

    with pytest.raises(AiTimeout):
        call(AiHubClient(BASE_URL, CLIENT_ID, ACCESS_SECRET, MODEL, urllib_transport))
    assert len(opener.calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(f"cannot reach {BASE_URL}"),
        urllib.error.URLError(ConnectionRefusedError()),
        ConnectionResetError(),
    ],
    ids=["url_error", "refused", "reset"],
)
def test_urllib_transport_other_failures_raise_transport_error(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """守住「Billing Hub……出错时回答「AI 暂不可用」」：超时以外的网络错误
    抛消息固定的 TransportError（不是 TransportTimeout），不带原异常链（其中可能有地址），
    经客户端为 AiUnavailable。
    """
    monkeypatch.setattr(ai_hub, "_OPENER", FakeOpener(error))
    with pytest.raises(TransportError) as excinfo:
        urllib_transport(JSON_REQUEST)
    assert not isinstance(excinfo.value, TransportTimeout)
    assert str(excinfo.value) == "ai hub request failed"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True

    with pytest.raises(AiUnavailable):
        call(AiHubClient(BASE_URL, CLIENT_ID, ACCESS_SECRET, MODEL, urllib_transport))


def test_urllib_transport_refuses_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住「项目密钥……经私有配置注入」：
    地址不是 https:// 时不发请求（密钥不走明文）。
    """
    opener = FakeOpener(FakeHttpResponse(200, b"{}"))
    monkeypatch.setattr(ai_hub, "_OPENER", opener)
    with pytest.raises(TransportError):
        urllib_transport(HttpRequest("http://hub.example.test/v1/messages", JSON_BODY))
    assert opener.calls == []


def test_urllib_transport_does_not_follow_redirects() -> None:
    """守住「项目密钥……经私有配置注入」与「不自动重试」：opener 不跟随重定向
    （跟随会把认证头带到别的地址并再发一次），3xx 作为响应返回、由客户端归为 AiUnavailable。
    """
    handlers = [h for h in ai_hub._OPENER.handlers if isinstance(h, ai_hub._NoRedirect)]
    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None

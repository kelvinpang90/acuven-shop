"""Stripe 测试环境适配器：配置判定、六个调用、结果归类、webhook 签名校验、替身与默认传输。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」第 1 条「服务端只接受 sk_test_
或 rk_test_ 开头的密钥，其他前缀……一律不启用 Stripe 支付并向运营告警邮件报告；……会话或付款的
livemode 为真即拒绝」，第 7 条「用原始请求体与 webhook 签名密钥校验 Stripe-Signature（含时间
容差）」，第 8 条「Stripe 以请求错误明确拒绝创建（4xx，不含 409 与 429……）」，第 9 条「CI 与
本地测试用模拟的 Stripe 适配器，不调用 Stripe」，第 10 条「本站不读取、不保存 Stripe 返回的
客户资料、卡号片段或账单地址……日志不记录 Stripe 请求与事件原文」；以及 docs/HANDOFF.md 0.53
记录的 2026-10-15 测试环境核验结论（下称「核验」）。每条测试的文档字符串引用它守住的那一句；
设计与核验没有直接原句的，写明守住的是 SHOP-TASK-111 验收的哪一条。

不连网：适配器的测试用本文件的 RecordingTransport 代替传输函数；默认传输的测试把模块里的
opener 换成本文件的 FakeOpener。期望的认证头与签名都在这里另算，不取实现里的函数。
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import urllib.error
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
import yaml
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.services import stripe_gateway
from app.services.stripe_gateway import (
    MAX_RESPONSE_BYTES,
    CheckoutSession,
    FakeStripeCall,
    FakeStripeGateway,
    HttpRequest,
    HttpResponse,
    PaymentIntentInfo,
    Refund,
    RefundPage,
    StripeError,
    StripeGateway,
    StripeHttpGateway,
    StripeOutcome,
    StripeResult,
    TransportError,
    get_stripe_gateway,
    stripe_configured,
    urllib_transport,
    verify_webhook_signature,
)

SECRET_KEY = "sk_test_" + "51Hx" * 6
RESTRICTED_KEY = "rk_test_" + "77Ab" * 6
LIVE_KEY = "sk_live_" + "99Zq" * 6
WEBHOOK_SECRET = "whsec_" + "c0ffee" * 5
DEMO_EMAIL = "demo-buyer@example.test"
BASE_URL = "https://stripe-api.example.test"
SITE_URL = "https://shop.example.test"

CUSTOMER_NAME = "Siti Demo Buyer"
ADDRESS = "88 Jalan Contoh"
CARD_LAST4 = "4321"
ERROR_MESSAGE = f"No such customer: {DEMO_EMAIL}"

SESSION_ID = "cs_test_a1B2c3"
PAYMENT_INTENT_ID = "pi_test_d4E5f6"
REFUND_ID = "re_test_g7H8i9"
CREATE_KEY = "create-key-0f3a"
REFUND_KEY = "17"
CHECKOUT_URL = "https://checkout.example.test/c/pay/cs_test_a1B2c3"

SECRETS = [
    SECRET_KEY,
    RESTRICTED_KEY,
    LIVE_KEY,
    "99Zq" * 6,
    WEBHOOK_SECRET,
    DEMO_EMAIL,
    CUSTOMER_NAME,
    ADDRESS,
    CARD_LAST4,
    "No such customer",
]

CREATE_FORM = (
    ("mode", "payment"),
    ("line_items[0][price_data][currency]", "myr"),
    ("line_items[0][price_data][unit_amount]", "12345"),
    ("line_items[0][price_data][product_data][name]", "Demo order"),
    ("line_items[0][quantity]", "1"),
    ("customer_email", DEMO_EMAIL),
    ("client_reference_id", "42"),
    ("metadata[order_id]", "42"),
    ("metadata[create_key]", CREATE_KEY),
    ("locale", "ms"),
    ("success_url", SITE_URL + "/pay/stripe/success"),
    ("cancel_url", SITE_URL + "/pay/stripe/cancel"),
    ("expires_at", "1792000000"),
)

REFUND_FORM = (
    ("payment_intent", PAYMENT_INTENT_ID),
    ("amount", "5000"),
    ("metadata[attempt_id]", REFUND_KEY),
)


@pytest.fixture(autouse=True)
def reset_key_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # 「同一进程只记一次」的标记是模块全局的；每条测试从未记过开始。
    monkeypatch.setattr(stripe_gateway, "_key_warning_logged", False)


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


def respond(status: int, payload: object, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode(), headers or {})


def stripe_error(status: int, error_type: str, param: str | None = None) -> HttpResponse:
    error = {"type": error_type, "param": param, "code": "x_code", "message": ERROR_MESSAGE}
    return respond(status, {"error": error})


def make_settings(**overrides: str) -> Settings:
    values = {
        "stripe_api_base_url": BASE_URL,
        "stripe_secret_key": SECRET_KEY,
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_demo_email": DEMO_EMAIL,
        "public_site_url": SITE_URL,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def gateway(transport: RecordingTransport, **overrides: str) -> StripeHttpGateway:
    return StripeHttpGateway(make_settings(**overrides), transport)


def payment_intent_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": PAYMENT_INTENT_ID,
        "object": "payment_intent",
        "status": "succeeded",
        "livemode": False,
        "receipt_email": DEMO_EMAIL,
        "latest_charge": {
            "id": "ch_test_1",
            "object": "charge",
            "billing_details": {
                "email": DEMO_EMAIL,
                "name": CUSTOMER_NAME,
                "address": {"line1": ADDRESS, "country": "MY"},
            },
            "payment_method_details": {
                "type": "card",
                "card": {"brand": "visa", "last4": CARD_LAST4},
            },
        },
    }
    body.update(overrides)
    return body


def session_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": SESSION_ID,
        "object": "checkout.session",
        "status": "complete",
        "payment_status": "paid",
        "livemode": False,
        "currency": "myr",
        "amount_total": 12345,
        "client_reference_id": "42",
        "metadata": {"order_id": "42", "create_key": CREATE_KEY, "note": "not-whitelisted"},
        "url": CHECKOUT_URL,
        "expires_at": 1792000000,
        "customer_email": DEMO_EMAIL,
        "customer_details": {
            "email": DEMO_EMAIL,
            "name": CUSTOMER_NAME,
            "address": {"line1": ADDRESS, "country": "MY"},
        },
        "payment_intent": payment_intent_body(),
    }
    body.update(overrides)
    return body


def refund_body(refund_id: str = REFUND_ID, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": refund_id,
        "object": "refund",
        "status": "pending",
        "amount": 5000,
        "currency": "myr",
        "payment_intent": PAYMENT_INTENT_ID,
        "metadata": {"attempt_id": REFUND_KEY, "note": "not-whitelisted"},
        "destination_details": {"card": {"last4": CARD_LAST4}},
    }
    body.update(overrides)
    return body


def list_body(*refunds: dict[str, object], has_more: object = False) -> dict[str, object]:
    return {"object": "list", "data": list(refunds), "has_more": has_more, "url": "/v1/refunds"}


EXPECTED_SESSION = CheckoutSession(
    id=SESSION_ID,
    status="complete",
    payment_status="paid",
    livemode=False,
    currency="myr",
    amount_total=12345,
    client_reference_id="42",
    order_id="42",
    create_key=CREATE_KEY,
    url=CHECKOUT_URL,
    expires_at=1792000000,
    payment_intent=PaymentIntentInfo(PAYMENT_INTENT_ID, "succeeded", False, "card"),
)

EXPECTED_REFUND = Refund(REFUND_ID, "pending", 5000, PAYMENT_INTENT_ID, REFUND_KEY)


def module_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == stripe_gateway.__name__]


def assert_no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def call_all(target: StripeGateway) -> list[StripeResult[object]]:
    """依次调用六个方法（参数都合格）。"""
    return [
        target.create_checkout_session(CREATE_FORM, CREATE_KEY),
        target.retrieve_checkout_session(SESSION_ID),
        target.expire_checkout_session(SESSION_ID),
        target.create_refund(REFUND_FORM, REFUND_KEY),
        target.retrieve_refund(REFUND_ID),
        target.list_refunds(PAYMENT_INTENT_ID),
    ]


# ---------------------------------------------------------------- 配置项


def test_settings_default_to_unconfigured_and_min_amount_200() -> None:
    """守住验收第 1 条「默认空串即未配置，最后一项默认 200」与第 2 条「订单应付低于 Stripe 对
    MYR 的最低收款额（配置项，默认 200 仙……）」。
    """
    settings = Settings(_env_file=None)
    assert settings.stripe_api_base_url == ""
    assert settings.stripe_secret_key == ""
    assert settings.stripe_webhook_secret == ""
    assert settings.stripe_demo_email == ""
    assert settings.public_site_url == ""
    assert settings.stripe_min_amount_sen == 200
    assert stripe_configured(settings) is False


def test_settings_read_from_shop_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 1 条「前缀 SHOP_」：六项取自 .env.example 登记的 SHOP_ 变量名。"""
    monkeypatch.setenv("SHOP_STRIPE_API_BASE_URL", BASE_URL)
    monkeypatch.setenv("SHOP_STRIPE_SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("SHOP_STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("SHOP_STRIPE_DEMO_EMAIL", DEMO_EMAIL)
    monkeypatch.setenv("SHOP_PUBLIC_SITE_URL", SITE_URL)
    monkeypatch.setenv("SHOP_STRIPE_MIN_AMOUNT_SEN", "250")
    settings = Settings(_env_file=None)
    assert settings.stripe_min_amount_sen == 250
    assert settings.public_site_url == SITE_URL
    assert stripe_configured(settings) is True


def test_compose_passes_all_six_variables_to_api_and_jobs() -> None:
    """守住验收第 1 条「shop_api 与 shop_jobs 两个服务把这六项都传进容器（前五项未设置时为
    空串、最后一项未设置时为 200，照常部署）」。
    """
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    expected = {
        "SHOP_STRIPE_API_BASE_URL": "${SHOP_STRIPE_API_BASE_URL:-}",
        "SHOP_STRIPE_SECRET_KEY": "${SHOP_STRIPE_SECRET_KEY:-}",
        "SHOP_STRIPE_WEBHOOK_SECRET": "${SHOP_STRIPE_WEBHOOK_SECRET:-}",
        "SHOP_STRIPE_DEMO_EMAIL": "${SHOP_STRIPE_DEMO_EMAIL:-}",
        "SHOP_PUBLIC_SITE_URL": "${SHOP_PUBLIC_SITE_URL:-}",
        "SHOP_STRIPE_MIN_AMOUNT_SEN": "${SHOP_STRIPE_MIN_AMOUNT_SEN:-200}",
    }
    for service in ("shop_api", "shop_jobs"):
        environment = compose["services"][service]["environment"]
        for name, value in expected.items():
            assert environment[name] == value


# ---------------------------------------------------------------- 配置判定


@pytest.mark.parametrize("key", [SECRET_KEY, RESTRICTED_KEY])
def test_configured_with_test_key_sends_request(key: str) -> None:
    """守住第 1 条「服务端只接受 sk_test_ 或 rk_test_ 开头的密钥」：两种测试前缀都启用。"""
    assert stripe_configured(make_settings(stripe_secret_key=key)) is True
    transport = RecordingTransport(respond(200, session_body()))
    target = gateway(transport, stripe_secret_key=key)
    assert target.configured is True
    assert target.retrieve_checkout_session(SESSION_ID).outcome is StripeOutcome.OK
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"stripe_api_base_url": ""},
        {"stripe_secret_key": ""},
        {"stripe_webhook_secret": ""},
        {"stripe_demo_email": ""},
        {"public_site_url": ""},
        {"stripe_api_base_url": "http://stripe-api.example.test"},
        {"public_site_url": "http://shop.example.test"},
        {"stripe_api_base_url": "https://"},
        {"stripe_webhook_secret": "whsek_" + "c0ffee" * 5},
        {"stripe_secret_key": LIVE_KEY},
        {"stripe_secret_key": "rk_live_" + "99Zq" * 6},
        {"stripe_secret_key": "pk_test_" + "99Zq" * 6},
        {"stripe_secret_key": "mystery-key"},
    ],
)
def test_unconfigured_calls_are_unavailable_without_requests(overrides: dict[str, str]) -> None:
    """守住第 1 条「其他前缀……一律不启用 Stripe 支付」与验收第 2 条「前五项都非空、
    两个地址都是 https 协议、密钥以 sk_test_ 或 rk_test_ 开头、webhook 签名密钥以
    whsec_ 开头时为已配置，否则未配置；未配置时适配器的每个调用都归为 unavailable、
    不发请求」：逐项缺失或不合格。
    """
    assert stripe_configured(make_settings(**overrides)) is False
    transport = RecordingTransport()
    target = gateway(transport, **overrides)
    assert target.configured is False
    for result in call_all(target):
        assert result == StripeResult(StripeOutcome.UNAVAILABLE)
    assert transport.requests == []


@pytest.mark.parametrize(
    "key", [LIVE_KEY, "rk_live_" + "99Zq" * 6, "pk_test_" + "99Zq" * 6, "mystery-key"]
)
def test_non_test_key_logs_category_once_without_key(
    key: str, caplog: pytest.LogCaptureFixture
) -> None:
    """守住第 1 条「其他前缀（含 sk_live_、rk_live_）一律不启用 Stripe 支付并向运营告警邮件
    报告」与验收第 2 条「另记一条 error 级日志（同一进程只记一次），内容只有类别
    stripe_key_not_test，不含密钥的任何部分」（告警邮件接入前先记日志）。
    """
    with caplog.at_level(logging.DEBUG):
        assert stripe_configured(make_settings(stripe_secret_key=key)) is False
        assert stripe_configured(make_settings(stripe_secret_key=key)) is False
        StripeHttpGateway(make_settings(stripe_secret_key=LIVE_KEY), RecordingTransport())
    records = module_logs(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "stripe_key_not_test"
    assert records[0].args in ((), None)
    for text in (records[0].getMessage(), caplog.text):
        assert key not in text
        assert key[8:] not in text


@pytest.mark.parametrize("key", [SECRET_KEY, RESTRICTED_KEY, ""])
def test_test_or_empty_key_logs_nothing(key: str, caplog: pytest.LogCaptureFixture) -> None:
    """守住验收第 2 条「密钥非空但不以这两种测试前缀开头时另记一条」：测试密钥与空密钥不记。"""
    with caplog.at_level(logging.DEBUG):
        stripe_configured(make_settings(stripe_secret_key=key))
    assert module_logs(caplog) == []


# ---------------------------------------------------------------- 六个调用：请求


def test_create_session_posts_form_in_given_order_with_idempotency_key() -> None:
    """守住第 3 条「再以该键与快照调用 Stripe 创建 Checkout 会话」与验收第 3 条「POST，表单
    参数按调用方给出的顺序原样发送，带 Idempotency-Key」「认证头为 Bearer 加密钥」。
    """
    transport = RecordingTransport(respond(200, session_body()))
    gateway(transport).create_checkout_session(CREATE_FORM, CREATE_KEY)
    [request] = transport.requests
    assert request.method == "POST"
    assert request.url == f"{BASE_URL}/v1/checkout/sessions"
    assert request.params == CREATE_FORM
    assert dict(request.headers) == {
        "Authorization": f"Bearer {SECRET_KEY}",
        "Idempotency-Key": CREATE_KEY,
    }


def test_adapter_adds_no_form_params_and_no_stripe_version() -> None:
    """守住核验（1）「创建 Checkout 会话已不接受 payment_method_types 参数（400）」
    与验收第 3 条「适配器不自行添加任何表单参数，也不传 payment_method_types」
    「不发送 Stripe-Version 头」。
    """
    transport = RecordingTransport(respond(200, session_body()), respond(200, refund_body()))
    target = gateway(transport)
    target.create_checkout_session((("mode", "payment"),), CREATE_KEY)
    target.create_refund((("payment_intent", PAYMENT_INTENT_ID),), REFUND_KEY)
    assert transport.requests[0].params == (("mode", "payment"),)
    assert transport.requests[1].params == (("payment_intent", PAYMENT_INTENT_ID),)
    for request in transport.requests:
        assert not any(name.startswith("payment_method_types") for name, _ in request.params)
        assert "stripe-version" not in {name.lower() for name in request.headers}


def test_retrieve_session_gets_with_expanded_latest_charge() -> None:
    """守住第 4 条「从该 PaymentIntent 实际成功的那笔 Charge 读取的付款方式类型」与核验（3）
    「付款方式类型取 PaymentIntent 的 latest_charge.payment_method_details.type」：GET 带
    expand[] 为 payment_intent.latest_charge，不带幂等键。
    """
    transport = RecordingTransport(respond(200, session_body()))
    gateway(transport).retrieve_checkout_session(SESSION_ID)
    [request] = transport.requests
    assert request.method == "GET"
    assert request.url == f"{BASE_URL}/v1/checkout/sessions/{SESSION_ID}"
    assert request.params == (("expand[]", "payment_intent.latest_charge"),)
    assert dict(request.headers) == {"Authorization": f"Bearer {SECRET_KEY}"}


def test_expire_session_posts_to_expire_without_form() -> None:
    """守住第 5 条「会话为 open 时请求 Stripe 作废」与核验（5）「作废 open 会话成功」：
    POST 到会话的 expire，不带表单与幂等键。
    """
    transport = RecordingTransport(respond(200, session_body(status="expired")))
    gateway(transport).expire_checkout_session(SESSION_ID)
    [request] = transport.requests
    assert request.method == "POST"
    assert request.url == f"{BASE_URL}/v1/checkout/sessions/{SESSION_ID}/expire"
    assert request.params == ()
    assert dict(request.headers) == {"Authorization": f"Bearer {SECRET_KEY}"}


def test_create_refund_posts_form_in_given_order_with_idempotency_key() -> None:
    """守住第 8 条「以该记录的本站 ID 为幂等键，并写进退款元数据」与验收第 3 条「创建退款
    （POST，带 Idempotency-Key）」。
    """
    transport = RecordingTransport(respond(200, refund_body()))
    gateway(transport).create_refund(REFUND_FORM, REFUND_KEY)
    [request] = transport.requests
    assert request.method == "POST"
    assert request.url == f"{BASE_URL}/v1/refunds"
    assert request.params == REFUND_FORM
    assert dict(request.headers) == {
        "Authorization": f"Bearer {SECRET_KEY}",
        "Idempotency-Key": REFUND_KEY,
    }


def test_retrieve_refund_gets_by_id() -> None:
    """守住第 8 条「状态一律以向 Stripe 查询到的该退款当前对象为准」：按 ID 查询退款。"""
    transport = RecordingTransport(respond(200, refund_body()))
    gateway(transport).retrieve_refund(REFUND_ID)
    [request] = transport.requests
    assert request.method == "GET"
    assert request.url == f"{BASE_URL}/v1/refunds/{REFUND_ID}"
    assert request.params == ()
    assert dict(request.headers) == {"Authorization": f"Bearer {SECRET_KEY}"}


def test_list_refunds_pages_with_limit_100_and_starting_after() -> None:
    """守住第 8 条「列出该 PaymentIntent 退款的全部分页、按元数据里的尝试 ID 对账」
    与验收第 3 条「GET，limit 为 100，支持 starting_after 分页」。
    """
    transport = RecordingTransport(respond(200, list_body()), respond(200, list_body()))
    target = gateway(transport)
    target.list_refunds(PAYMENT_INTENT_ID)
    target.list_refunds(PAYMENT_INTENT_ID, starting_after=REFUND_ID)
    first, second = transport.requests
    for request in (first, second):
        assert request.method == "GET"
        assert request.url == f"{BASE_URL}/v1/refunds"
        assert dict(request.headers) == {"Authorization": f"Bearer {SECRET_KEY}"}
    assert first.params == (("payment_intent", PAYMENT_INTENT_ID), ("limit", "100"))
    assert second.params == (
        ("payment_intent", PAYMENT_INTENT_ID),
        ("limit", "100"),
        ("starting_after", REFUND_ID),
    )


def test_base_url_trailing_slash_and_ids_are_quoted_into_path() -> None:
    """守住验收第 1 条「只写到主机的 https 地址，路径由适配器拼接」：末尾斜杠不重复，
    对象 ID 按路径段编码，不能改写路径。
    """
    transport = RecordingTransport(respond(200, session_body()))
    gateway(transport, stripe_api_base_url=BASE_URL + "/").expire_checkout_session("cs/../x?y")
    assert transport.requests[0].url == f"{BASE_URL}/v1/checkout/sessions/cs%2F..%2Fx%3Fy/expire"


def test_empty_ids_and_keys_raise_value_error_without_request() -> None:
    """守住验收第 3 条「带 Idempotency-Key」：幂等键或对象 ID 为空是调用方的编程错误，
    不发请求，消息不含参数。
    """
    transport = RecordingTransport()
    target = gateway(transport)
    calls = [
        lambda: target.create_checkout_session(CREATE_FORM, ""),
        lambda: target.create_refund(REFUND_FORM, ""),
        lambda: target.retrieve_checkout_session(""),
        lambda: target.expire_checkout_session(""),
        lambda: target.retrieve_refund(""),
        lambda: target.list_refunds(""),
        lambda: target.list_refunds(PAYMENT_INTENT_ID, starting_after=""),
    ]
    for call in calls:
        with pytest.raises(ValueError) as excinfo:
            call()
        assert_no_secrets(str(excinfo.value))
    assert transport.requests == []


# ---------------------------------------------------------------- 归类：ok 与白名单


def test_session_ok_returns_only_whitelisted_fields() -> None:
    """守住第 10 条「本站不读取、不保存 Stripe 返回的客户资料、卡号片段或账单地址」与验收
    第 4 条「会话只取……；customer_details、账单地址、姓名、邮箱与卡号片段一律不取，
    原始对象不出适配器」。
    """
    transport = RecordingTransport(respond(200, session_body()))
    result = gateway(transport).retrieve_checkout_session(SESSION_ID)
    assert result == StripeResult(StripeOutcome.OK, EXPECTED_SESSION, replayed=False)
    assert_no_secrets(repr(result))
    assert_no_secrets(str(result))


def test_session_replay_header_marks_replayed() -> None:
    """守住核验（2）「过期后以同一幂等键重放返回原会话（同 ID，响应头 Idempotent-Replayed:
    true），但内容是当初保存的响应（status 仍为 open），须再查询会话才得实际状态」。
    """
    replayed = respond(200, session_body(status="open"), {"Idempotent-Replayed": "true"})
    lowercase = respond(200, session_body(), {"idempotent-replayed": "true"})
    not_replayed = respond(200, session_body(), {"Idempotent-Replayed": "false"})
    transport = RecordingTransport(replayed, lowercase, not_replayed)
    target = gateway(transport)
    first = target.create_checkout_session(CREATE_FORM, CREATE_KEY)
    assert first.outcome is StripeOutcome.OK
    assert first.replayed is True
    assert first.value is not None and first.value.status == "open"
    assert target.create_checkout_session(CREATE_FORM, CREATE_KEY).replayed is True
    assert target.create_checkout_session(CREATE_FORM, CREATE_KEY).replayed is False


def test_unexpanded_payment_intent_keeps_only_its_id() -> None:
    """守住验收第 4 条「payment_intent 未展开（只是 ID 字符串）时只取其 ID、
    其余 PaymentIntent 字段为空」（如作废与创建的响应）。
    """
    transport = RecordingTransport(
        respond(200, session_body(payment_intent=PAYMENT_INTENT_ID)),
        respond(200, session_body(payment_intent=None)),
    )
    target = gateway(transport)
    result = target.expire_checkout_session(SESSION_ID)
    assert result.value is not None
    assert result.value.payment_intent == PaymentIntentInfo(PAYMENT_INTENT_ID)
    result = target.expire_checkout_session(SESSION_ID)
    assert result.value is not None and result.value.payment_intent is None


def test_unexpanded_latest_charge_leaves_payment_method_type_empty() -> None:
    """守住第 4 条「读不到时留空，不用会话的可用方式列表代替」：latest_charge 只是 ID 时为空。"""
    intent = payment_intent_body(latest_charge="ch_test_1")
    transport = RecordingTransport(
        respond(200, session_body(payment_intent=intent, payment_method_types=["card"]))
    )
    result = gateway(transport).retrieve_checkout_session(SESSION_ID)
    assert result.value is not None
    assert result.value.payment_intent == PaymentIntentInfo(
        PAYMENT_INTENT_ID, "succeeded", False, None
    )


def test_wrong_typed_whitelisted_fields_become_empty() -> None:
    """守住验收第 4 条「白名单里其他字段缺失或类型不对时取空值」。"""
    body = {
        "id": SESSION_ID,
        "object": "checkout.session",
        "status": 1,
        "livemode": "false",
        "amount_total": True,
        "expires_at": "1792000000",
        "metadata": ["order_id", "42"],
        "url": None,
        "payment_intent": {"status": "succeeded"},
    }
    transport = RecordingTransport(respond(200, body))
    result = gateway(transport).retrieve_checkout_session(SESSION_ID)
    assert result == StripeResult(StripeOutcome.OK, CheckoutSession(SESSION_ID))


def test_refund_ok_returns_only_whitelisted_fields() -> None:
    """守住第 10 条与验收第 4 条「退款只取 id、status、amount、payment_intent 与 metadata 里的
    attempt_id」；核验（4）的 FPX、GrabPay 退款 ID 前缀为 pyr_、对象仍是 refund。
    """
    expanded = refund_body("pyr_test_1", payment_intent=payment_intent_body())
    transport = RecordingTransport(
        respond(200, refund_body(), {"Idempotent-Replayed": "true"}),
        respond(200, expanded),
    )
    target = gateway(transport)
    result = target.create_refund(REFUND_FORM, REFUND_KEY)
    assert result == StripeResult(StripeOutcome.OK, EXPECTED_REFUND, replayed=True)
    second = target.retrieve_refund("pyr_test_1")
    expected = Refund("pyr_test_1", "pending", 5000, PAYMENT_INTENT_ID, REFUND_KEY)
    assert second == StripeResult(StripeOutcome.OK, expected)
    assert_no_secrets(repr(result) + repr(second))


def test_refund_list_returns_refunds_and_has_more() -> None:
    """守住第 8 条「列出该 PaymentIntent 退款的全部分页」与验收第 4 条「列表只取这些退款与
    has_more」。
    """
    transport = RecordingTransport(
        respond(200, list_body(refund_body(), refund_body("re_test_2"), has_more=True)),
        respond(200, list_body(has_more="yes")),
    )
    target = gateway(transport)
    page = target.list_refunds(PAYMENT_INTENT_ID)
    second = Refund("re_test_2", "pending", 5000, PAYMENT_INTENT_ID, REFUND_KEY)
    assert page == StripeResult(StripeOutcome.OK, RefundPage((EXPECTED_REFUND, second), True))
    assert target.list_refunds(PAYMENT_INTENT_ID).value == RefundPage((), None)


@pytest.mark.parametrize(
    "body",
    [
        list_body(refund_body(), {"id": "re_test_2", "object": "charge"}),
        list_body(refund_body(), {"object": "refund"}),
        list_body(refund_body(), "re_test_2"),
        {"object": "list", "data": {"id": REFUND_ID}},
        {"object": "search_result", "data": []},
    ],
)
def test_refund_list_with_any_bad_item_is_unavailable(body: dict[str, object]) -> None:
    """守住验收第 4 条「列表响应要求 object 为 list 且 data 为数组，其中任一退款不合格整页归
    unavailable」（第 8 条的对账不能漏看一笔）。
    """
    transport = RecordingTransport(respond(200, body))
    result = gateway(transport).list_refunds(PAYMENT_INTENT_ID)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)


# ---------------------------------------------------------------- 归类：live


@pytest.mark.parametrize(
    "body",
    [
        session_body(livemode=True),
        session_body(payment_intent=payment_intent_body(livemode=True)),
    ],
)
def test_livemode_session_or_payment_is_live_and_logged(
    body: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """守住第 1 条「会话或付款的 livemode 为真即拒绝」与验收第 4 条「改归单独的 live 类别
    （不是 rejected：会话可能已经建成）并记一条 error 日志（类别 stripe_livemode）
    ……日志只含类别」。
    """
    transport = RecordingTransport(respond(200, body), respond(200, body), respond(200, body))
    target = gateway(transport)
    with caplog.at_level(logging.DEBUG):
        results = [
            target.create_checkout_session(CREATE_FORM, CREATE_KEY),
            target.retrieve_checkout_session(SESSION_ID),
            target.expire_checkout_session(SESSION_ID),
        ]
    for result in results:
        assert result.outcome is StripeOutcome.LIVE
        assert result.value is not None and result.value.id == SESSION_ID
    records = module_logs(caplog)
    assert [r.getMessage() for r in records] == ["stripe_livemode"] * 3
    assert all(r.levelno == logging.ERROR and r.args in ((), None) for r in records)


def test_livemode_false_or_missing_stays_ok(caplog: pytest.LogCaptureFixture) -> None:
    """守住第 1 条「livemode 为真即拒绝」：为假或缺失（取空值）时照常为 ok，不记日志。"""
    missing = session_body(payment_intent=PAYMENT_INTENT_ID)
    del missing["livemode"]
    transport = RecordingTransport(respond(200, session_body()), respond(200, missing))
    target = gateway(transport)
    with caplog.at_level(logging.DEBUG):
        assert target.retrieve_checkout_session(SESSION_ID).outcome is StripeOutcome.OK
        assert target.retrieve_checkout_session(SESSION_ID).outcome is StripeOutcome.OK
    assert module_logs(caplog) == []


# ---------------------------------------------------------------- 归类：rejected 与 unavailable


@pytest.mark.parametrize(
    ("status", "error_type", "param"),
    [
        (400, "invalid_request_error", "expires_at"),
        (400, "invalid_request_error", "amount"),
        (422, "invalid_request_error", "metadata"),
        (401, "invalid_request_error", None),
        (404, "invalid_request_error", "id"),
    ],
)
def test_client_errors_are_rejected_with_type_param_and_code(
    status: int, error_type: str, param: str | None
) -> None:
    """守住第 8 条「Stripe 以请求错误明确拒绝创建（4xx，不含 409 与 429）……判断依据
    是 Stripe 返回的错误说明未建退款，不单凭状态码」，核验（2）「换新键带过去的
    expires_at 被 400 拒绝（param 为 expires_at）」与（4）「超额退款 400（param 为
    amount）」：归 rejected，带状态码与 type、param、code，不带 message 原文。
    """
    transport = RecordingTransport(stripe_error(status, error_type, param))
    result = gateway(transport).create_refund(REFUND_FORM, REFUND_KEY)
    expected = StripeError(status, error_type, param, "x_code")
    assert result == StripeResult(StripeOutcome.REJECTED, error=expected)
    assert_no_secrets(repr(result))


def test_expire_of_finished_session_is_rejected_for_caller_to_query() -> None:
    """守住核验（5）「再作废已过期或已完成的会话都 400，消息里带当前状态，实现时 400 之后
    查询会话决定，不解析消息」：归 rejected，不带消息。
    """
    transport = RecordingTransport(stripe_error(400, "invalid_request_error"))
    result = gateway(transport).expire_checkout_session(SESSION_ID)
    assert result.outcome is StripeOutcome.REJECTED
    assert result.error == StripeError(400, "invalid_request_error", None, "x_code")
    assert "message" not in repr(result)


@pytest.mark.parametrize("status", [409, 429, 301, 302, 307, 500, 502, 503, 102, 600])
def test_conflict_rate_limit_redirect_and_server_errors_are_unavailable(status: int) -> None:
    """守住第 8 条「409、429 与结果未知仍保持 pending」与验收第 4 条「409、429、3xx（不跟随
    重定向）、5xx、其他状态码……归 unavailable」：即使响应是带 error 的 JSON。
    """
    transport = RecordingTransport(stripe_error(status, "api_error"))
    result = gateway(transport).create_refund(REFUND_FORM, REFUND_KEY)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(400, b"<html>Bad Request</html>"),
        respond(400, {"error": "invalid_request_error"}),
        respond(404, [{"error": {"type": "invalid_request_error"}}]),
        respond(422, {"message": "no error object"}),
    ],
)
def test_client_error_without_error_object_is_unavailable(response: HttpResponse) -> None:
    """守住验收第 4 条「4xx 的响应体不是带 error 的 JSON 对象时同样归 unavailable，
    不算明确拒绝」（第 8 条：判断依据是 Stripe 返回的错误说明）。
    """
    transport = RecordingTransport(response)
    result = gateway(transport).create_checkout_session(CREATE_FORM, CREATE_KEY)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(200, b"not json"),
        respond(200, [session_body()]),
        respond(200, session_body(id=None)),
        respond(200, session_body(id="")),
        respond(200, session_body(id=123)),
        respond(200, session_body(object="payment_intent")),
        respond(201, {"id": SESSION_ID}),
    ],
)
def test_success_status_with_malformed_session_is_unavailable(response: HttpResponse) -> None:
    """守住验收第 4 条「2xx 且响应是 JSON 对象为 ok」「2xx 的对象缺少 id 或 object
    类型不对时归 unavailable」。
    """
    transport = RecordingTransport(response)
    result = gateway(transport).retrieve_checkout_session(SESSION_ID)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)


@pytest.mark.parametrize(
    "body",
    [refund_body(object="checkout.session"), refund_body(id=None), {"object": "refund"}],
)
def test_success_status_with_malformed_refund_is_unavailable(body: dict[str, object]) -> None:
    """守住验收第 4 条「2xx 的对象缺少 id 或 object 类型不对时归 unavailable」（退款）。"""
    transport = RecordingTransport(respond(200, body))
    result = gateway(transport).retrieve_refund(REFUND_ID)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(f"{BASE_URL} {SECRET_KEY}"),
        ConnectionRefusedError(DEMO_EMAIL),
        OSError("network unreachable"),
        TransportError(),
    ],
)
def test_timeout_and_connection_failure_are_unavailable_without_retry(
    error: BaseException,
) -> None:
    """守住第 9 条「创建会话或查询时 Stripe 不可用，支付页提示稍后再试」与验收第 3、4 条
    「不重试」「超时、连接失败……归 unavailable」：只调用一次，异常不外抛。
    """
    transport = RecordingTransport(error)
    result = gateway(transport).create_checkout_session(CREATE_FORM, CREATE_KEY)
    assert result == StripeResult(StripeOutcome.UNAVAILABLE)
    assert len(transport.requests) == 1


def test_oversized_response_is_unavailable() -> None:
    """守住验收第 3、4 条「响应体最多读 64 KB」「响应过大……归 unavailable」：超过 64 KiB 即使
    是合格的 JSON 也不解析；恰为 64 KiB 照常归类。
    """
    encoded = json.dumps(session_body()).encode()
    exact = encoded + b" " * (MAX_RESPONSE_BYTES - len(encoded))
    transport = RecordingTransport(HttpResponse(200, exact + b" "), HttpResponse(200, exact))
    target = gateway(transport)
    too_large = target.retrieve_checkout_session(SESSION_ID)
    assert too_large == StripeResult(StripeOutcome.UNAVAILABLE)
    assert target.retrieve_checkout_session(SESSION_ID).outcome is StripeOutcome.OK


def test_adapter_writes_no_other_logs(caplog: pytest.LogCaptureFixture) -> None:
    """守住第 10 条「日志不记录 Stripe 请求与事件原文」与验收第 4 条「除上面两类日志外
    不写日志」。
    """
    transport = RecordingTransport(
        respond(200, session_body()),
        stripe_error(400, "invalid_request_error", "expires_at"),
        stripe_error(500, "api_error"),
        respond(200, refund_body()),
        TimeoutError(),
        HttpResponse(200, b"not json"),
    )
    with caplog.at_level(logging.DEBUG):
        call_all(gateway(transport))
    assert module_logs(caplog) == []


def test_repr_and_messages_do_not_contain_secrets_or_email() -> None:
    """守住第 10 条与验收第 4 条「请求与响应原文、密钥、邮箱都不进异常消息、repr 或返回值的
    字符串形式」。
    """
    transport = RecordingTransport(
        respond(200, session_body()), stripe_error(400, "invalid_request_error", "email")
    )
    target = gateway(transport)
    ok = target.create_checkout_session(CREATE_FORM, CREATE_KEY)
    rejected = target.create_refund(REFUND_FORM, REFUND_KEY)
    request = transport.requests[0]
    response = respond(200, session_body())
    fake = FakeStripeGateway()
    fake.create_checkout_session(CREATE_FORM, CREATE_KEY)
    texts = [
        repr(target),
        repr(StripeHttpGateway(make_settings(stripe_secret_key=LIVE_KEY), transport)),
        repr(request),
        repr(response),
        str(TransportError()),
        repr(ok),
        str(ok),
        repr(rejected),
        repr(fake),
        repr(fake.calls),
    ]
    for text in texts:
        assert_no_secrets(text)
    assert repr(target) == "StripeHttpGateway(configured=True)"


# ---------------------------------------------------------------- webhook 签名校验

NOW = datetime(2026, 10, 18, 3, 0, 0)
TIMESTAMP = int(NOW.replace(tzinfo=UTC).timestamp())
PAYLOAD = b'{"id": "evt_test_1", "type": "checkout.session.completed"}'


def sign(payload: bytes, timestamp: int, secret: str = WEBHOOK_SECRET) -> str:
    signed = f"{timestamp}.".encode() + payload
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def header(timestamp: int = TIMESTAMP, payload: bytes = PAYLOAD) -> str:
    return f"t={timestamp},v1={sign(payload, timestamp)}"


def test_signature_valid_passes() -> None:
    """守住第 7 条「用原始请求体与 webhook 签名密钥校验 Stripe-Signature」：自算的签名通过，
    头里另有 v0 等其他项不影响。
    """
    assert verify_webhook_signature(PAYLOAD, header(), WEBHOOK_SECRET, NOW) is True
    with_v0 = header() + ",v0=" + "f" * 64
    assert verify_webhook_signature(PAYLOAD, with_v0, WEBHOOK_SECRET, NOW) is True


def test_signature_with_tampered_body_or_wrong_secret_fails() -> None:
    """守住第 7 条「不通过回 400、不处理」：请求体改一个字节或签名密钥不同即不通过。"""
    tampered = PAYLOAD.replace(b"completed", b"completeD")
    assert verify_webhook_signature(tampered, header(), WEBHOOK_SECRET, NOW) is False
    assert verify_webhook_signature(PAYLOAD, header(), "whsec_other", NOW) is False
    assert verify_webhook_signature(PAYLOAD, header(), "", NOW) is False


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(0, True), (300, True), (-300, True), (301, False), (-301, False), (86400, False)],
)
def test_signature_time_tolerance_is_300_seconds(offset: int, expected: bool) -> None:
    """守住第 7 条「含时间容差」与验收第 5 条「当前时间与 t 相差不超过 300 秒才通过」（签名时间
    在过去或将来都算）。
    """
    now = NOW + timedelta(seconds=offset)
    assert verify_webhook_signature(PAYLOAD, header(), WEBHOOK_SECRET, now) is expected


def test_signature_with_several_v1_passes_if_any_matches() -> None:
    """守住验收第 5 条「取 t 与全部 v1……与任一 v1 以常量时间比较相等」
    （Stripe 轮换签名密钥时同一事件带多个 v1）。
    """
    good = sign(PAYLOAD, TIMESTAMP)
    several = f"t={TIMESTAMP},v1={'0' * 64},v1={good},v1={'1' * 64}"
    assert verify_webhook_signature(PAYLOAD, several, WEBHOOK_SECRET, NOW) is True
    none_match = f"t={TIMESTAMP},v1={'0' * 64},v1={'1' * 64}"
    assert verify_webhook_signature(PAYLOAD, none_match, WEBHOOK_SECRET, NOW) is False


@pytest.mark.parametrize(
    "signature_header",
    [
        None,
        "",
        "garbage",
        f"t={TIMESTAMP}",
        f"v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t=abc,v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP}.5,v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t=-{TIMESTAMP},v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t=,v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP},t={TIMESTAMP},v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP};v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP},,v1={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP},v0={sign(PAYLOAD, TIMESTAMP)}",
        f"t={TIMESTAMP},v1=",
        f"t={TIMESTAMP},v1=éé",
        f"t={TIMESTAMP},v1=\ud800",
        f"t={'9' * 40},v1={sign(PAYLOAD, TIMESTAMP)}",
    ],
)
def test_malformed_signature_header_fails_without_exception_or_log(
    signature_header: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """守住验收第 5 条「头缺失、格式不合、t 不是整数、没有 v1、签名不符或超出容差
    都返回不通过，不抛异常、不写日志」。
    """
    with caplog.at_level(logging.DEBUG):
        result = verify_webhook_signature(PAYLOAD, signature_header, WEBHOOK_SECRET, NOW)
    assert result is False
    assert module_logs(caplog) == []


# ---------------------------------------------------------------- 替身与依赖


def test_fake_records_calls_and_returns_scripted_results() -> None:
    """守住第 9 条「CI 与本地测试用模拟的 Stripe 适配器，不调用 Stripe」与验收第 6 条
    「内存里记下每次调用的方法、路径、表单与幂等键，结果可按调用编排」：取完编排的
    结果后为 unavailable。
    """
    session = StripeResult(StripeOutcome.OK, EXPECTED_SESSION)
    error = StripeError(400, "invalid_request_error")
    rejected = StripeResult(StripeOutcome.REJECTED, error=error)
    fake = FakeStripeGateway(session, rejected)
    assert fake.create_checkout_session(CREATE_FORM, CREATE_KEY) is session
    assert fake.expire_checkout_session(SESSION_ID) is rejected
    assert fake.retrieve_checkout_session(SESSION_ID) == StripeResult(StripeOutcome.UNAVAILABLE)
    assert fake.calls == [
        FakeStripeCall("POST", "/v1/checkout/sessions", CREATE_FORM, CREATE_KEY),
        FakeStripeCall("POST", f"/v1/checkout/sessions/{SESSION_ID}/expire"),
        FakeStripeCall(
            "GET",
            f"/v1/checkout/sessions/{SESSION_ID}",
            (("expand[]", "payment_intent.latest_charge"),),
        ),
    ]


def test_fake_records_same_method_path_and_form_as_real_adapter() -> None:
    """守住第 9 条「模拟的 Stripe 适配器」：六个调用在替身里记下的方法、路径、表单与幂等键，
    与真实适配器发出的请求一致，用替身测的调用方不会与真实请求脱节。
    """
    transport = RecordingTransport(
        respond(200, session_body()),
        respond(200, session_body()),
        respond(200, session_body()),
        respond(200, refund_body()),
        respond(200, refund_body()),
        respond(200, list_body()),
    )
    fake = FakeStripeGateway()
    call_all(gateway(transport))
    call_all(fake)
    assert len(fake.calls) == len(transport.requests) == 6
    for call, request in zip(fake.calls, transport.requests, strict=True):
        assert request.method == call.method
        assert request.url == BASE_URL + call.path
        assert request.params == call.form
        assert request.headers.get("Idempotency-Key") == call.idempotency_key


def test_get_stripe_gateway_prefers_app_state_then_builds_from_settings() -> None:
    """守住验收第 6 条「从 app.state.stripe_gateway 取，测试以此注入替身；没有设置时按
    get_sms_provider 读取配置的写法建真实适配器」。
    """
    fake = FakeStripeGateway()
    injected = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(stripe_gateway=fake, settings=None))
    )
    assert get_stripe_gateway(injected) is fake
    for settings, expected in [(make_settings(), True), (Settings(_env_file=None), False)]:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
        built = get_stripe_gateway(request)
        assert isinstance(built, StripeHttpGateway)
        assert built.configured is expected


def test_get_stripe_gateway_works_as_fastapi_dependency() -> None:
    """守住验收第 6 条「取得适配器的 FastAPI 依赖」：注入替身时用替身，否则用真实适配器
    （未配置时归为 unavailable、不发请求）。
    """
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)

    @app.post("/probe")
    def probe(target: Annotated[StripeGateway, Depends(get_stripe_gateway)]) -> dict[str, str]:
        return {"outcome": target.retrieve_checkout_session(SESSION_ID).outcome.value}

    with TestClient(app) as http:
        assert http.post("/probe").json() == {"outcome": "unavailable"}

    fake = FakeStripeGateway(StripeResult(StripeOutcome.OK, EXPECTED_SESSION))
    app.state.stripe_gateway = fake
    with TestClient(app) as http:
        assert http.post("/probe").json() == {"outcome": "ok"}
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- 默认传输（urllib）


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
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


AUTH = {"Authorization": f"Bearer {SECRET_KEY}"}


def test_urllib_transport_get_puts_params_in_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 3 条「默认只用标准库 urllib，超时 5 秒，不重试」与「GET，expand[] 为
    payment_intent.latest_charge」：GET 把参数按顺序放进查询串、不带请求体。
    """
    response = FakeHttpResponse(200, b"{}", {"Idempotent-Replayed": "true", "Request-Id": "r"})
    opener = FakeOpener(response)
    monkeypatch.setattr(stripe_gateway, "_OPENER", opener)
    params = (("payment_intent", PAYMENT_INTENT_ID), ("limit", "100"))
    result = urllib_transport(HttpRequest("GET", f"{BASE_URL}/v1/refunds", params, AUTH))

    assert (result.status, result.body) == (200, b"{}")
    assert result.headers == {"idempotent-replayed": "true", "request-id": "r"}
    assert response.read_amounts == [MAX_RESPONSE_BYTES + 1]
    [(request, timeout)] = opener.calls
    assert timeout == 5
    assert request.get_method() == "GET"
    assert request.full_url == f"{BASE_URL}/v1/refunds?" + urllib.parse.urlencode(params)
    assert request.data is None
    assert request.get_header("Authorization") == f"Bearer {SECRET_KEY}"
    assert request.get_header("Accept") == "application/json"


def test_urllib_transport_post_encodes_form_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """守住验收第 3 条「表单参数按调用方给出的顺序原样发送，带 Idempotency-Key」：POST 的请求体
    按给出的顺序做表单编码。
    """
    opener = FakeOpener(FakeHttpResponse(200, b"{}"))
    monkeypatch.setattr(stripe_gateway, "_OPENER", opener)
    url = f"{BASE_URL}/v1/checkout/sessions"
    headers = {**AUTH, "Idempotency-Key": CREATE_KEY}
    urllib_transport(HttpRequest("POST", url, CREATE_FORM, headers))

    [(request, timeout)] = opener.calls
    assert timeout == 5
    assert request.get_method() == "POST"
    assert request.full_url == url
    assert request.data == urllib.parse.urlencode(CREATE_FORM).encode("ascii")
    assert request.data.startswith(b"mode=payment&line_items")
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert request.get_header("Idempotency-key") == CREATE_KEY
    assert request.get_header("Stripe-version") is None


def test_urllib_transport_returns_http_errors_as_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """守住验收第 4 条「409 与 429 以外的 4xx 归 rejected」：4xx 作为响应返回，
    由适配器归类。
    """
    error = urllib.error.HTTPError(
        f"{BASE_URL}/v1/refunds",
        400,
        "Bad Request",
        {"Idempotent-Replayed": "true"},
        io.BytesIO(b'{"error": {"type": "invalid_request_error"}}'),
    )
    monkeypatch.setattr(stripe_gateway, "_OPENER", FakeOpener(error))
    result = urllib_transport(HttpRequest("POST", f"{BASE_URL}/v1/refunds", REFUND_FORM, AUTH))
    assert result.status == 400
    assert result.body == b'{"error": {"type": "invalid_request_error"}}'
    assert result.headers == {"idempotent-replayed": "true"}


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(f"timed out {BASE_URL}"),
        urllib.error.URLError(f"refused {BASE_URL}"),
        ConnectionResetError(),
    ],
)
def test_urllib_transport_network_errors_raise_fixed_transport_error(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """守住验收第 4 条「请求与响应原文、密钥……都不进异常消息」：超时与网络错误抛消息固定的
    TransportError，不带原异常链（其中可能有地址）。
    """
    monkeypatch.setattr(stripe_gateway, "_OPENER", FakeOpener(error))
    with pytest.raises(TransportError) as excinfo:
        urllib_transport(HttpRequest("GET", f"{BASE_URL}/v1/refunds/{REFUND_ID}", (), AUTH))
    assert str(excinfo.value) == "stripe request failed"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "request_",
    [
        HttpRequest("GET", "http://stripe-api.example.test/v1/refunds", (), AUTH),
        HttpRequest("DELETE", f"{BASE_URL}/v1/refunds/{REFUND_ID}", (), AUTH),
    ],
)
def test_urllib_transport_refuses_plain_http_and_other_methods(
    request_: HttpRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """守住验收第 1 条「只写到主机的 https 地址」：地址不是 https:// 或方法不是 GET、POST 时
    不发请求（密钥不走明文）。
    """
    opener = FakeOpener(FakeHttpResponse(200, b"{}"))
    monkeypatch.setattr(stripe_gateway, "_OPENER", opener)
    with pytest.raises(TransportError):
        urllib_transport(request_)
    assert opener.calls == []


def test_urllib_transport_does_not_follow_redirects() -> None:
    """守住验收第 3 条「不跟随重定向」：跟随会把认证头带到别的地址，3xx 作为响应返回、由适配器
    归为 unavailable。
    """
    handlers = [
        h for h in stripe_gateway._OPENER.handlers if isinstance(h, stripe_gateway._NoRedirect)
    ]
    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None

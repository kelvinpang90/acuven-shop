"""Stripe 测试环境（sandbox）适配器：配置判定、六个调用与结果归类、webhook 签名校验、测试替身。

依据 docs/DESIGN.md 1.12（提交 acf576e）「Stripe 测试环境支付」：
第 1 条「服务端只接受 sk_test_ 或 rk_test_ 开头的密钥，其他前缀……一律不启用 Stripe 支付并向
运营告警邮件报告；……会话或付款的 livemode 为真即拒绝」，第 7 条「用原始请求体与 webhook
签名密钥校验 Stripe-Signature（含时间容差）」，第 9 条「CI 与本地测试用模拟的 Stripe 适配器，
不调用 Stripe」，第 10 条「本站不读取、不保存 Stripe 返回的客户资料……日志不记录 Stripe 请求与
事件原文」；以及 docs/HANDOFF.md 0.53 记录的 2026-10-15 测试环境核验结论（创建会话已不接受
payment_method_types；过期后以同一幂等键重放返回当初保存的响应；退款与作废被拒时为 400）。

本模块只做 HTTP 调用、结果归类与签名校验，不接业务、不读写数据库。运营告警邮件尚未接入：
设计写「告警」之处先记一条 error 级日志，只含类别（本模块没有本站记录 ID）。除这两类日志
（stripe_key_not_test、stripe_livemode）外不写日志；请求与响应原文、密钥、邮箱都不出现在
异常消息、repr 或返回值里，返回值只有类别与按白名单取出的对账字段。

不发送 Stripe-Version 头，沿用账户默认的 API 版本：2026-10-15 的核验即按账户默认版本完成，
升级账户 API 版本前须先在测试环境重跑核验。
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from fastapi import Request

from app.core.config import Settings

logger = logging.getLogger(__name__)

# urllib 的 timeout 是套接字超时：建立连接与之后每一次读取各自最多等 5 秒。
TIMEOUT_SECONDS = 5

# 响应体最多读这么多字节；传输多读 1 字节，超过即归为 unavailable。
MAX_RESPONSE_BYTES = 64 * 1024

# 第 1 条：只接受测试环境的密钥。
TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")
WEBHOOK_SECRET_PREFIX = "whsec_"

# 第 7 条的时间容差：当前时间与签名时间戳相差不超过 300 秒。
SIGNATURE_TOLERANCE_SECONDS = 300

# 告警邮件接入前的 error 级日志类别（只记类别）。
LOG_KEY_NOT_TEST = "stripe_key_not_test"
LOG_LIVEMODE = "stripe_livemode"

_SESSIONS_PATH = "/v1/checkout/sessions"
_REFUNDS_PATH = "/v1/refunds"
_SESSION_EXPAND = (("expand[]", "payment_intent.latest_charge"),)
_REFUND_LIST_LIMIT = "100"

_TIMESTAMP_PATTERN = re.compile(r"[0-9]{1,20}")

Form = Sequence[tuple[str, str]]


# ---------------------------------------------------------------- 配置判定

_key_warning_lock = threading.Lock()
_key_warning_logged = False


def _is_https(url: str) -> bool:
    return url.startswith("https://") and len(url) > len("https://")


def stripe_configured(settings: Settings) -> bool:
    """Stripe 测试是否已配置：前五项都非空、两个地址都是 https://、密钥以 sk_test_ 或
    rk_test_ 开头、webhook 签名密钥以 whsec_ 开头。

    密钥非空但不是测试前缀（含 sk_live_、rk_live_）时另记一条 error 级日志，同一进程只记一次，
    内容只有类别 stripe_key_not_test。供适配器与之后的站点设置接口共用。
    """
    key = settings.stripe_secret_key
    test_key = key.startswith(TEST_KEY_PREFIXES)
    if key and not test_key:
        _warn_key_not_test()
    return bool(
        test_key
        and _is_https(settings.stripe_api_base_url)
        and settings.stripe_webhook_secret.startswith(WEBHOOK_SECRET_PREFIX)
        and settings.stripe_demo_email
        and _is_https(settings.public_site_url)
    )


def _warn_key_not_test() -> None:
    global _key_warning_logged
    with _key_warning_lock:
        if _key_warning_logged:
            return
        _key_warning_logged = True
    logger.error(LOG_KEY_NOT_TEST)


# ---------------------------------------------------------------- 传输


@dataclass(frozen=True)
class HttpRequest:
    """一次 GET 或表单编码的 POST。参数按给出的顺序发送（GET 放进查询串）。

    地址、参数与请求头可能含密钥或演示邮箱，都不进 repr。
    """

    method: str
    url: str = field(repr=False)
    params: tuple[tuple[str, str], ...] = field(repr=False, default=())
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    """任何 HTTP 状态码的响应（含非 2xx）。响应体与响应头不进 repr；响应头名为小写。"""

    status: int
    body: bytes = field(repr=False, default=b"")
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)


class TransportError(RuntimeError):
    """超时、网络错误或地址不是 https://。消息固定，不含地址或请求内容。"""

    def __init__(self) -> None:
        super().__init__("stripe request failed")


# 传输函数：发出请求并返回响应（非 2xx 也返回），连不上或超时抛 TransportError。
Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 不跟随重定向：跟随会把认证头带到另一个地址。3xx 原样作为响应返回，归为 unavailable。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _lower_headers(message: object) -> dict[str, str]:
    items = getattr(message, "items", None)
    if items is None:
        return {}
    return {str(name).lower(): str(value) for name, value in items()}


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """默认传输：标准库 urllib 发一次请求，超时 TIMEOUT_SECONDS，不重试、不跟随重定向，
    响应体最多读 MAX_RESPONSE_BYTES 加 1 字节（多出的那 1 字节供适配器判定过大）。
    """
    if not request.url.startswith("https://") or request.method not in ("GET", "POST"):
        raise TransportError()
    encoded = urllib.parse.urlencode(request.params)
    headers = {**request.headers, "Accept": "application/json"}
    if request.method == "GET":
        url = f"{request.url}?{encoded}" if encoded else request.url
        data = None
    else:
        url = request.url
        data = encoded.encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=request.method)
    try:
        with _OPENER.open(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            return HttpResponse(resp.status, body, _lower_headers(resp.headers))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException):
            body = b""
        finally:
            exc.close()
        return HttpResponse(exc.code, body, _lower_headers(exc.headers))
    except (OSError, http.client.HTTPException, ValueError):
        # 异常链里可能带地址，from None 不带出去。
        raise TransportError() from None


# ---------------------------------------------------------------- 结果类别


class StripeOutcome(StrEnum):
    OK = "ok"
    # 第 1 条「会话或付款的 livemode 为真即拒绝」：会话可能已经建成，所以不是 rejected。
    LIVE = "live"
    # 409 与 429 以外的 4xx，响应是带 error 的 JSON 对象。是否说明没有建成对象由调用方按
    # 状态码与错误内容一起判断（第 8 条：不单凭状态码）。
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StripeError:
    """Stripe 错误的类别字段；不带 message 原文。"""

    http_status: int
    type: str | None = None
    param: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class PaymentIntentInfo:
    """会话的 PaymentIntent。未展开（只是 ID 字符串）时只有 id。"""

    id: str
    status: str | None = None
    livemode: bool | None = None
    # latest_charge.payment_method_details.type（如 card、fpx、grabpay）。
    payment_method_type: str | None = None


@dataclass(frozen=True)
class CheckoutSession:
    """Checkout 会话的对账字段（第 10 条）；order_id 与 create_key 取自会话元数据。"""

    id: str
    status: str | None = None
    payment_status: str | None = None
    livemode: bool | None = None
    currency: str | None = None
    amount_total: int | None = None
    client_reference_id: str | None = None
    order_id: str | None = None
    create_key: str | None = None
    url: str | None = None
    expires_at: int | None = None
    payment_intent: PaymentIntentInfo | None = None


@dataclass(frozen=True)
class Refund:
    """退款的对账字段；attempt_id 取自退款元数据，payment_intent 是 PaymentIntent ID。"""

    id: str
    status: str | None = None
    amount: int | None = None
    payment_intent: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class RefundPage:
    refunds: tuple[Refund, ...] = ()
    has_more: bool | None = None


@dataclass(frozen=True)
class StripeResult[T]:
    outcome: StripeOutcome
    # 只在 ok 与 live 时有。
    value: T | None = None
    # 只在 ok 与 live 时可能为真：响应头 Idempotent-Replayed 为 true（Stripe 回放了当初
    # 保存的响应，核验显示内容不是当前状态，须再查询）。
    replayed: bool = False
    # 只在 rejected 时有。
    error: StripeError | None = None


def _unavailable() -> StripeResult[Any]:
    return StripeResult(StripeOutcome.UNAVAILABLE)


# ---------------------------------------------------------------- 白名单取字段


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _str(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    return value if isinstance(value, str) else None


def _int(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bool(data: Mapping[str, object], name: str) -> bool | None:
    value = data.get(name)
    return value if isinstance(value, bool) else None


def _dict(data: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = data.get(name)
    return value if isinstance(value, dict) else {}


def _parse_payment_intent(value: object) -> PaymentIntentInfo | None:
    if isinstance(value, str):
        return PaymentIntentInfo(value) if value else None
    if not isinstance(value, dict):
        return None
    intent_id = _str(value, "id")
    if not intent_id:
        return None
    details = _dict(_dict(value, "latest_charge"), "payment_method_details")
    return PaymentIntentInfo(
        id=intent_id,
        status=_str(value, "status"),
        livemode=_bool(value, "livemode"),
        payment_method_type=_str(details, "type"),
    )


def _parse_session(data: Mapping[str, object]) -> CheckoutSession | None:
    session_id = _str(data, "id")
    if data.get("object") != "checkout.session" or not session_id:
        return None
    metadata = _dict(data, "metadata")
    return CheckoutSession(
        id=session_id,
        status=_str(data, "status"),
        payment_status=_str(data, "payment_status"),
        livemode=_bool(data, "livemode"),
        currency=_str(data, "currency"),
        amount_total=_int(data, "amount_total"),
        client_reference_id=_str(data, "client_reference_id"),
        order_id=_str(metadata, "order_id"),
        create_key=_str(metadata, "create_key"),
        url=_str(data, "url"),
        expires_at=_int(data, "expires_at"),
        payment_intent=_parse_payment_intent(data.get("payment_intent")),
    )


def _parse_refund(data: Mapping[str, object]) -> Refund | None:
    refund_id = _str(data, "id")
    if data.get("object") != "refund" or not refund_id:
        return None
    intent = data.get("payment_intent")
    if isinstance(intent, dict):
        intent = intent.get("id")
    return Refund(
        id=refund_id,
        status=_str(data, "status"),
        amount=_int(data, "amount"),
        payment_intent=intent if isinstance(intent, str) else None,
        attempt_id=_str(_dict(data, "metadata"), "attempt_id"),
    )


def _parse_refund_page(data: Mapping[str, object]) -> RefundPage | None:
    items = data.get("data")
    if data.get("object") != "list" or not isinstance(items, list):
        return None
    refunds = []
    for item in items:
        refund = _parse_refund(item) if isinstance(item, dict) else None
        if refund is None:
            # 任一退款不合格整页归 unavailable：对账不能漏看一笔。
            return None
        refunds.append(refund)
    return RefundPage(tuple(refunds), _bool(data, "has_more"))


def _replayed(headers: Mapping[str, str]) -> bool:
    for name, value in headers.items():
        if name.lower() == "idempotent-replayed":
            return value.strip().lower() == "true"
    return False


Parser = Callable[[Mapping[str, object]], object | None]


def _classify(response: HttpResponse, parse: Parser) -> StripeResult[Any]:
    if len(response.body) > MAX_RESPONSE_BYTES:
        return _unavailable()
    status = response.status
    if 200 <= status < 300:
        data = _json_object(response.body)
        value = None if data is None else parse(data)
        if value is None:
            return _unavailable()
        return StripeResult(StripeOutcome.OK, value, _replayed(response.headers))
    # 409（幂等键冲突或并发）与 429（限流）不算明确拒绝。
    if 400 <= status < 500 and status not in (409, 429):
        data = _json_object(response.body)
        error = data.get("error") if data is not None else None
        if not isinstance(error, dict):
            return _unavailable()
        detail = StripeError(
            http_status=status,
            type=_str(error, "type"),
            param=_str(error, "param"),
            code=_str(error, "code"),
        )
        return StripeResult(StripeOutcome.REJECTED, error=detail)
    # 3xx（不跟随重定向）、5xx 与其他状态码。
    return _unavailable()


def _check_livemode(result: StripeResult[CheckoutSession]) -> StripeResult[CheckoutSession]:
    session = result.value
    if result.outcome is not StripeOutcome.OK or session is None:
        return result
    intent = session.payment_intent
    if session.livemode is True or (intent is not None and intent.livemode is True):
        # 适配器没有本站记录 ID：日志只含类别，带记录 ID 的告警由调用方另记。
        logger.error(LOG_LIVEMODE)
        return StripeResult(StripeOutcome.LIVE, session, result.replayed)
    return result


# ---------------------------------------------------------------- 路径与参数（适配器与替身共用）


def _segment(object_id: str) -> str:
    if not isinstance(object_id, str) or not object_id:
        raise ValueError("stripe object id is required")
    return urllib.parse.quote(object_id, safe="")


def _require_key(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("idempotency key is required")
    return idempotency_key


def _session_path(session_id: str) -> str:
    return f"{_SESSIONS_PATH}/{_segment(session_id)}"


def _expire_path(session_id: str) -> str:
    return f"{_session_path(session_id)}/expire"


def _refund_path(refund_id: str) -> str:
    return f"{_REFUNDS_PATH}/{_segment(refund_id)}"


def _refund_list_params(
    payment_intent_id: str, starting_after: str | None
) -> tuple[tuple[str, str], ...]:
    _segment(payment_intent_id)
    params = [("payment_intent", payment_intent_id), ("limit", _REFUND_LIST_LIMIT)]
    if starting_after is not None:
        _segment(starting_after)
        params.append(("starting_after", starting_after))
    return tuple(params)


# ---------------------------------------------------------------- 适配器


class StripeGateway(Protocol):
    def create_checkout_session(
        self, form: Form, idempotency_key: str
    ) -> StripeResult[CheckoutSession]: ...

    def retrieve_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]: ...

    def expire_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]: ...

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]: ...

    def retrieve_refund(self, refund_id: str) -> StripeResult[Refund]: ...

    def list_refunds(
        self, payment_intent_id: str, starting_after: str | None = None
    ) -> StripeResult[RefundPage]: ...


class StripeHttpGateway:
    """Stripe 测试环境的 HTTP 适配器。未配置（见 stripe_configured）时每个调用都归为
    unavailable、不调用传输函数。

    表单参数按调用方给出的顺序原样发送：适配器不自行添加任何参数，也不传
    payment_method_types（核验显示 Checkout 已不接受它，付款方式由 Stripe 后台设置决定）。
    对象 ID 或幂等键为空时抛 ValueError（编程错误，消息不含参数）。
    """

    def __init__(self, settings: Settings, transport: Transport = urllib_transport) -> None:
        self._configured = stripe_configured(settings)
        self._secret_key = settings.stripe_secret_key
        self._base_url = settings.stripe_api_base_url.rstrip("/")
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: Transport = urllib_transport
    ) -> StripeHttpGateway:
        return cls(settings, transport)

    @property
    def configured(self) -> bool:
        return self._configured

    def __repr__(self) -> str:
        return f"StripeHttpGateway(configured={self._configured})"

    def create_checkout_session(
        self, form: Form, idempotency_key: str
    ) -> StripeResult[CheckoutSession]:
        """POST /v1/checkout/sessions，带 Idempotency-Key。"""
        key = _require_key(idempotency_key)
        return _check_livemode(self._call("POST", _SESSIONS_PATH, form, key, _parse_session))

    def retrieve_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]:
        """GET /v1/checkout/sessions/{id}，expand[] 为 payment_intent.latest_charge。"""
        path = _session_path(session_id)
        return _check_livemode(self._call("GET", path, _SESSION_EXPAND, None, _parse_session))

    def expire_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]:
        """POST /v1/checkout/sessions/{id}/expire。"""
        path = _expire_path(session_id)
        return _check_livemode(self._call("POST", path, (), None, _parse_session))

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        """POST /v1/refunds，带 Idempotency-Key。"""
        key = _require_key(idempotency_key)
        return self._call("POST", _REFUNDS_PATH, form, key, _parse_refund)

    def retrieve_refund(self, refund_id: str) -> StripeResult[Refund]:
        """GET /v1/refunds/{id}。"""
        return self._call("GET", _refund_path(refund_id), (), None, _parse_refund)

    def list_refunds(
        self, payment_intent_id: str, starting_after: str | None = None
    ) -> StripeResult[RefundPage]:
        """GET /v1/refunds?payment_intent=…&limit=100[&starting_after=…]，一次一页。"""
        params = _refund_list_params(payment_intent_id, starting_after)
        return self._call("GET", _REFUNDS_PATH, params, None, _parse_refund_page)

    def _call(
        self,
        method: str,
        path: str,
        params: Form,
        idempotency_key: str | None,
        parse: Parser,
    ) -> StripeResult[Any]:
        if not self._configured:
            return _unavailable()
        headers = {"Authorization": f"Bearer {self._secret_key}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = HttpRequest(method, self._base_url + path, tuple(params), headers)
        try:
            response = self._transport(request)
        except (TransportError, OSError):
            return _unavailable()
        return _classify(response, parse)


# ---------------------------------------------------------------- webhook 签名校验


def verify_webhook_signature(
    payload: bytes, signature_header: str | None, secret: str, now: datetime
) -> bool:
    """第 7 条：以原始请求体与 webhook 签名密钥校验 Stripe-Signature（含时间容差）。

    从头里取 t 与全部 v1，以签名密钥对「t 的十进制、一个英文句点、请求体」做 HMAC-SHA256，
    十六进制结果与任一 v1 以常量时间比较相等、且当前时间（不带时区的 UTC）与 t 相差不超过
    300 秒才通过。头缺失、格式不合、t 不是整数、没有 v1、签名不符或超出容差都返回 False，
    不抛异常、不写日志。
    """
    if not isinstance(payload, bytes) or not isinstance(signature_header, str):
        return False
    if not isinstance(secret, str) or not secret or not isinstance(now, datetime):
        return False
    timestamp: int | None = None
    signatures: list[str] = []
    for item in signature_header.split(","):
        name, separator, value = item.strip().partition("=")
        if not separator or not name:
            return False
        if name == "t":
            if timestamp is not None or not _TIMESTAMP_PATTERN.fullmatch(value):
                return False
            timestamp = int(value)
        elif name == "v1":
            signatures.append(value)
    if timestamp is None or not signatures:
        return False
    current = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    try:
        if abs(current.timestamp() - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
            return False
        signed = str(timestamp).encode("ascii") + b"." + payload
        digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        expected = digest.encode("ascii")
        candidates = [signature.encode("utf-8") for signature in signatures]
    except (ValueError, OverflowError, OSError):
        # UnicodeEncodeError 是 ValueError 的子类。
        return False
    matched = False
    for candidate in candidates:
        # 逐个比较全部 v1，不在第一个相符时提前返回。
        if hmac.compare_digest(expected, candidate):
            matched = True
    return matched


# ---------------------------------------------------------------- 测试替身与依赖


@dataclass(frozen=True)
class FakeStripeCall:
    """替身记下的一次调用。表单可能含演示邮箱，与幂等键都不进 repr。"""

    method: str
    path: str
    form: tuple[tuple[str, str], ...] = field(repr=False, default=())
    idempotency_key: str | None = field(repr=False, default=None)


class FakeStripeGateway:
    """测试替身（第 9 条：CI 与本地测试不调用 Stripe）。

    在内存里记下每次调用的方法、路径、表单（GET 为查询参数）与幂等键，路径与参数的拼法与
    真实适配器相同；结果按调用顺序从 results 依次取出，取完后一律返回 unavailable。
    替身原样返回编排的结果，不做 livemode 归类。
    """

    def __init__(self, *results: StripeResult[Any]) -> None:
        self.results: list[StripeResult[Any]] = list(results)
        self.calls: list[FakeStripeCall] = []

    def __repr__(self) -> str:
        return f"FakeStripeGateway(calls={len(self.calls)})"

    def create_checkout_session(
        self, form: Form, idempotency_key: str
    ) -> StripeResult[CheckoutSession]:
        key = _require_key(idempotency_key)
        return self._record("POST", _SESSIONS_PATH, form, key)

    def retrieve_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]:
        return self._record("GET", _session_path(session_id), _SESSION_EXPAND)

    def expire_checkout_session(self, session_id: str) -> StripeResult[CheckoutSession]:
        return self._record("POST", _expire_path(session_id))

    def create_refund(self, form: Form, idempotency_key: str) -> StripeResult[Refund]:
        key = _require_key(idempotency_key)
        return self._record("POST", _REFUNDS_PATH, form, key)

    def retrieve_refund(self, refund_id: str) -> StripeResult[Refund]:
        return self._record("GET", _refund_path(refund_id))

    def list_refunds(
        self, payment_intent_id: str, starting_after: str | None = None
    ) -> StripeResult[RefundPage]:
        params = _refund_list_params(payment_intent_id, starting_after)
        return self._record("GET", _REFUNDS_PATH, params)

    def _record(
        self, method: str, path: str, form: Form = (), idempotency_key: str | None = None
    ) -> StripeResult[Any]:
        self.calls.append(FakeStripeCall(method, path, tuple(form), idempotency_key))
        if self.results:
            return self.results.pop(0)
        return _unavailable()


def get_stripe_gateway(request: Request) -> StripeGateway:
    """FastAPI 依赖：app.state.stripe_gateway 已设置时用它（测试以此注入 FakeStripeGateway），
    否则按应用的 Settings 构造真实适配器（未配置也返回，调用时归为 unavailable）。
    """
    gateway = getattr(request.app.state, "stripe_gateway", None)
    if gateway is not None:
        return gateway
    return StripeHttpGateway.from_settings(request.app.state.settings)

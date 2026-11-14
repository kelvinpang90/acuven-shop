"""AI 消息客户端：经 ai_billing_hub 的 POST /v1/messages 调用模型。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条。
「链路」：「后端经 ai_billing_hub 的 POST /v1/messages 调用模型，
以本项目在 Billing Hub 的身份计量；接口地址、项目密钥与模型名经私有配置注入，
仓库只记变量名。未配置时后台的 AI 按钮禁用并说明原因」；
「限制」：「单次调用 60 秒超时，不自动重试」。

本模块只做一次 HTTP 调用与结果归类；提示词、每日计数与回答的校验在 SHOP-TASK-248。
客户端本身不读订单、会员与收货资料，请求里只有调用方传入的系统提示与一段用户文字。
配置取自 SHOP_AI_*；四项缺一或接口地址不是 https:// 时抛 AiNotConfigured、不发请求。
每次调用只发一次 HTTPS POST（JSON），不重试。HTTP 经可注入的传输函数（Transport），
默认的 urllib_transport 只用标准库。

请求体照 Billing Hub 的 POST /v1/messages 约定（ai_billing_hub 仓库 docs/api.md）：
字段严格，未知字段回 422，所以只发 feature、model、system、messages、max_tokens
与可选的 output_config；成功为 200，响应有字符串 text 与 usage 的 input_tokens、output_tokens。

本模块不写日志；项目密钥、请求内容与响应原文都不出现在异常消息或 repr 里。
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.core.config import Settings

# urllib 的 timeout 是套接字超时：建立连接与之后每一次读取各自最多等 60 秒。
TIMEOUT_SECONDS = 60

# 响应体最多读这么多字节；截断后的 JSON 解析失败，归为 AiUnavailable。
MAX_RESPONSE_BYTES = 64 * 1024

# Billing Hub 按 feature 区分用途计量；本项目只有节日促销的装饰提案一处调用。
FEATURE = "shop.promotion_proposal"


# ---------------------------------------------------------------- 传输


@dataclass(frozen=True)
class HttpRequest:
    """一次 JSON 的 HTTPS POST。各字段含项目密钥或请求内容，都不进 repr。"""

    url: str = field(repr=False)
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    """任何 HTTP 状态码的响应（含非 2xx）。响应体不进 repr。"""

    status: int
    body: bytes = field(repr=False, default=b"")


class TransportError(RuntimeError):
    """网络错误或地址不是 https://。消息固定，不含地址或请求内容。"""

    message = "ai hub request failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class TransportTimeout(TransportError):
    """套接字超时（连接或某一次读取超过 TIMEOUT_SECONDS）。"""

    message = "ai hub request timed out"


# 传输函数：发出请求并返回响应（非 2xx 也返回），
# 超时抛 TransportTimeout，连不上抛 TransportError。
Transport = Callable[[HttpRequest], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 不跟随重定向：跟随会把认证头带到另一个地址。3xx 原样作为响应返回，归为 AiUnavailable。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """默认传输：标准库 urllib 发一次 POST，超时 TIMEOUT_SECONDS，不重试、不跟随重定向。"""
    if not request.url.startswith("https://"):
        raise TransportError()
    headers = {
        **request.headers,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(request.url, data=request.body, headers=headers, method="POST")
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
    # 异常链里可能带地址，from None 不带出去。
    except TimeoutError:
        raise TransportTimeout() from None
    except urllib.error.URLError as exc:
        # 连接阶段的超时被 URLError 包着。
        if isinstance(exc.reason, TimeoutError):
            raise TransportTimeout() from None
        raise TransportError() from None
    except (OSError, http.client.HTTPException, ValueError):
        raise TransportError() from None


# ---------------------------------------------------------------- 结果与异常


@dataclass(frozen=True)
class AiReply:
    """一次成功调用的结果。模型回答原文不进 repr（设计：不记模型回答原文）。"""

    text: str = field(repr=False)
    input_tokens: int
    output_tokens: int


class AiHubError(RuntimeError):
    """调用失败的基类。消息固定，不含地址、密钥、请求或响应内容。"""

    message = "ai hub call failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class AiNotConfigured(AiHubError):
    """四项配置缺一或接口地址不是 https://；没有发请求。"""

    message = "ai hub is not configured"


class AiTimeout(AiHubError):
    """连接或读取超过 60 秒。"""

    message = "ai hub call timed out"


class AiUnavailable(AiHubError):
    """连不上、非 200（含余额不足与 3xx），或响应不是约定的 JSON。"""

    message = "ai hub is unavailable"


def _token_count(usage: Mapping[str, object], name: str) -> int | None:
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_reply(body: bytes) -> AiReply | None:
    """200 的响应体解析为 AiReply；不是约定的 JSON 时为 None。"""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    usage = data.get("usage")
    if not isinstance(text, str) or not isinstance(usage, dict):
        return None
    input_tokens = _token_count(usage, "input_tokens")
    output_tokens = _token_count(usage, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    return AiReply(text, input_tokens, output_tokens)


# ---------------------------------------------------------------- 客户端


class AiHubClient:
    """Billing Hub 的 POST /v1/messages。四项配置缺一或接口地址不是 https:// 时视为未配置。"""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        access_secret: str,
        model: str,
        transport: Transport = urllib_transport,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._access_secret = access_secret
        self._model = model
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: Transport = urllib_transport
    ) -> AiHubClient:
        return cls(
            settings.ai_hub_base_url,
            settings.ai_hub_client_id,
            settings.ai_hub_access_secret,
            settings.ai_model,
            transport,
        )

    @property
    def configured(self) -> bool:
        return bool(
            self._client_id
            and self._access_secret
            and self._model
            and self._base_url.startswith("https://")
        )

    def __repr__(self) -> str:
        return f"AiHubClient(configured={self.configured})"

    def create_message(
        self,
        system: str,
        user_text: str,
        *,
        max_tokens: int,
        output_schema: Mapping[str, object] | None = None,
    ) -> AiReply:
        """发一次 POST /v1/messages，返回模型回答与用量。

        output_schema 不为 None 时要求模型按该 JSON Schema 回答（output_config.format）。
        未配置抛 AiNotConfigured（不发请求），超时抛 AiTimeout，
        其余失败抛 AiUnavailable；不重试。
        """
        if not self.configured:
            raise AiNotConfigured()
        payload: dict[str, object] = {
            "feature": FEATURE,
            "model": self._model,
            "system": system,
            "messages": [{"role": "user", "content": user_text}],
            "max_tokens": max_tokens,
        }
        if output_schema is not None:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": dict(output_schema)}
            }
        try:
            body = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError):
            # schema 里有不能编码为 JSON 的值；消息固定，不带出内容。
            raise AiUnavailable() from None
        headers = {
            "X-Acuven-Client-Id": self._client_id,
            "Authorization": f"Bearer {self._access_secret}",
        }
        request = HttpRequest(f"{self._base_url}/v1/messages", body, headers)
        # 异常链里可能带地址或请求内容，from None 不带出去。
        try:
            response = self._transport(request)
        except (TransportTimeout, TimeoutError):
            raise AiTimeout() from None
        except (TransportError, OSError):
            raise AiUnavailable() from None
        if response.status != 200:
            raise AiUnavailable()
        reply = _parse_reply(response.body)
        if reply is None:
            raise AiUnavailable()
        return reply

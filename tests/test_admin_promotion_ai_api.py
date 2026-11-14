"""后台 AI 装饰提案接口（app/api/admin_promotion_ai.py，SHOP-TASK-251；
服务层见 SHOP-TASK-248 的 app/services/promotion_ai.py）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 13 条
（下称「第 13 条」）、docs/UX.md 0.20 的 A12 AI 区（下称「A12」）与
SHOP-TASK-251 的落实写法与验收（下称「落实写法」「验收」）。
每条测试（参数化的测试是每个用例）的文档字符串写明它守住的原句。

夹具照 tests/test_admin_promotions_api.py：SQLite 内存库按模型建表
（StaticPool、开外键），写入一个管理员；https 的 base_url，以
issue_admin_session 登录，期望的 CSRF 令牌在这里另算。时间固定为 NOW
（monkeypatch 本接口模块与 app.api.admin_auth 的 _utcnow）。
本接口模块引用的 propose_decoration 换成 RecordingService：记下每次调用，
默认调用原函数；成功与映射异常的用例以它返回替身结果，不连模型。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.api.admin_auth as admin_auth_api
import app.api.admin_promotion_ai as ai_api
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import AdminAccount
from app.services.admin_auth import issue_admin_session
from app.services.admin_promotions import (
    PromotionInvalid,
    PromotionProblem,
    RuleInput,
    TierInput,
)
from app.services.ai_hub import AiHubClient, AiNotConfigured, AiUnavailable
from app.services.promotion_ai import AiDailyLimit, AiResultInvalid, DecorationProposal

STATUS_URL = "/api/admin/promotion-ai/status"
PROPOSAL_URL = "/api/admin/promotion-ai/proposal"
MAX_BODY_BYTES = 8 * 1024

COOKIE_NAME = "__Host-shop_admin_session"
CSRF_PREFIX = b"acuven-shop/admin-session/csrf\x00"

# 不带时区的 UTC，即马来西亚 2026-11-13 12:00。
NOW = datetime(2026, 11, 13, 4, 0)
SECRET = "SECRETVALUE"

SESSION_REQUIRED = {"detail": "admin_session_required"}
CSRF_FAILED = {"detail": "csrf_failed"}
TOO_LARGE = {"detail": "request body too large"}
NOT_JSON = {"detail": "request body must be JSON"}

AI_CONFIG = {
    "ai_hub_base_url": "https://hub.example.test",
    "ai_hub_client_id": "shop-client",
    "ai_hub_access_secret": "not-a-real-secret",
    "ai_model": "test-model",
}

COPY_FIELDS = [
    f"{kind}_{lang}"
    for kind in ("title", "subtitle", "badge", "strip")
    for lang in ("en", "zh", "ms")
]
PROPOSAL_FIELDS = [
    "pack",
    "palette",
    "ambient",
    "home_poster",
    "list_strip",
    "card_badge",
    "detail_ribbon",
    "cart_progress",
    *COPY_FIELDS,
]

PROPOSAL = DecorationProposal(
    pack="raya",
    palette="theme",
    ambient="none",
    home_poster="split",
    list_strip="band",
    card_badge="tag",
    detail_ribbon="ribbon",
    cart_progress="bar",
    title_en="Raya Sale",
    title_zh="开斋特卖",
    title_ms="Jualan Raya",
    subtitle_en="Up to 20% off",
    subtitle_zh=None,
    subtitle_ms=None,
    badge_en="Raya",
    badge_zh=None,
    badge_ms=None,
    strip_en=None,
    strip_zh=None,
    strip_ms=None,
)

# 四类规则各一条；只有类别与参数，没有商品与分类 ID。
RULES_IN: list[dict[str, Any]] = [
    {"kind": "item_discount", "discount_mode": "percent", "percent": 20},
    {"kind": "buy_x_get_y", "buy_quantity": 2, "get_quantity": 1, "get_percent": 50},
    {
        "kind": "spend_discount",
        "discount_mode": "amount",
        "tiers": [
            {"threshold_sen": 10000, "amount_sen": 1000, "percent": None},
            {"threshold_sen": 20000, "amount_sen": 2500, "percent": None},
        ],
    },
    {"kind": "free_shipping", "threshold_sen": 5000},
]
# 上面四条规则传给服务层时的样子。
RULES_PASSED = (
    RuleInput(kind="item_discount", discount_mode="percent", percent=20),
    RuleInput(kind="buy_x_get_y", buy_quantity=2, get_quantity=1, get_percent=50),
    RuleInput(
        kind="spend_discount",
        discount_mode="amount",
        tiers=(TierInput(10000, 1000, None), TierInput(20000, 2500, None)),
    ),
    RuleInput(kind="free_shipping", threshold_sen=5000),
)


def _problem(code: str, field: str) -> dict[str, Any]:
    """promotion_invalid 的一项问题（写法同 SHOP-TASK-242）。"""
    return {"code": code, "field": field, "product_id": None, "product_name": None}


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


class RecordingService:
    """代替本接口模块引用的 propose_decoration：记下每次调用的参数。

    outcome 为 None 时调用原函数，否则返回给定的提案或抛给定的异常。
    """

    def __init__(self, original: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome: DecorationProposal | Exception | None = None
        self._original = original

    def __call__(
        self,
        db: Session,
        client: AiHubClient,
        settings: Settings,
        description: str,
        rules: Any,
        now: datetime,
    ) -> DecorationProposal:
        self.calls.append(
            {
                "client": client,
                "settings": settings,
                "description": description,
                "rules": rules,
                "now": now,
            }
        )
        if self.outcome is None:
            return self._original(db, client, settings, description, rules, now)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool：TestClient 在另一个线程里调用接口，内存库必须始终是同一个连接。
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as seed:
        seed.add(
            AdminAccount(
                username="admin@example.com",
                password_hash="not-a-real-hash",
                created_at=NOW - timedelta(days=1),
                password_updated_at=NOW - timedelta(days=1),
            )
        )
        seed.commit()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_api, "_utcnow", lambda: NOW)
    monkeypatch.setattr(admin_auth_api, "_utcnow", lambda: NOW)


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> RecordingService:
    recording = RecordingService(ai_api.propose_decoration)
    monkeypatch.setattr(ai_api, "propose_decoration", recording)
    return recording


@pytest.fixture
def calls(service: RecordingService) -> list[dict[str, Any]]:
    """服务层被调用的记录，按调用顺序。"""
    return service.calls


def _app(engine: Engine, **settings: Any) -> FastAPI:
    app = create_app(Settings(_env_file=None, redis_url="", **settings))

    def _session() -> Iterator[Session]:
        # 关闭时回滚未提交的改动，与 app/db/session.py 的会话依赖相同。
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    return app


def _client_for(engine: Engine, **settings: Any) -> TestClient:
    # https：cookie 带 Secure，TestClient 只在 https 下回送它。
    return TestClient(_app(engine, **settings), base_url="https://testserver")


@pytest.fixture
def client(engine: Engine) -> TestClient:
    """已配置 AI 的应用。"""
    return _client_for(engine, **AI_CONFIG)


@pytest.fixture
def unconfigured_client(engine: Engine) -> TestClient:
    """没有 AI 配置的应用。"""
    return _client_for(engine)


@pytest.fixture
def token(engine: Engine) -> str:
    """一个有效的后台会话，返回令牌原文。"""
    with Session(engine) as db:
        account_id = db.scalar(select(AdminAccount.id))
        assert account_id is not None
        issued = issue_admin_session(db, account_id, NOW - timedelta(minutes=1))
        db.commit()
        return issued.token


# ---------------------------------------------------------------------------
# 请求与断言
# ---------------------------------------------------------------------------


def _csrf_for(token: str) -> str:
    return hashlib.sha256(CSRF_PREFIX + token.encode("ascii")).hexdigest()


def _headers(token: str | None, csrf: str | None = "auto") -> dict[str, str]:
    """csrf 为 "auto" 时按 token 算出正确的令牌；给 None 即不带请求头。"""
    headers = {}
    if token is not None:
        headers["Cookie"] = f"{COOKIE_NAME}={token}"
    if csrf == "auto":
        csrf = None if token is None else _csrf_for(token)
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    return headers


def _body(**fields: Any) -> dict[str, Any]:
    """一个合格的提案请求体；fields 覆盖默认值。"""
    body = {
        "description": "Hari Raya, green and gold",
        "rules": json.loads(json.dumps(RULES_IN)),
    }
    return body | fields


def _status(client: TestClient, token: str | None) -> Any:
    return client.get(STATUS_URL, headers=_headers(token, None))


def _post(
    client: TestClient, token: str | None, body: Any = None, *, csrf: str | None = "auto"
) -> Any:
    """body 为 None 时用合格的请求体。"""
    payload = _body() if body is None else body
    return client.post(PROPOSAL_URL, json=payload, headers=_headers(token, csrf))


def _raw(client: TestClient, token: str | None, content: bytes, content_type: str | None) -> Any:
    headers = _headers(token)
    if content_type is not None:
        headers["Content-Type"] = content_type
    return client.post(PROPOSAL_URL, content=content, headers=headers)


def _assert_no_store(response: Any) -> None:
    assert response.headers["cache-control"] == "no-store"


def _ok(response: Any) -> Any:
    assert response.status_code == 200, response.text
    _assert_no_store(response)
    return response.json()


def _assert_error(response: Any, status_code: int, body: dict[str, Any]) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == body
    _assert_no_store(response)


def _assert_plain_errors(response: Any) -> list[dict[str, Any]]:
    """422：每条错误只有位置、类型与固定消息，不回显所给的值。"""
    assert response.status_code == 422, response.text
    _assert_no_store(response)
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    for error in errors:
        assert set(error) == {"type", "loc", "msg"}
    assert SECRET not in response.text
    return errors


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------


def test_status_is_false_when_ai_is_not_configured(
    unconfigured_client: TestClient, token: str, calls: list[dict[str, Any]]
) -> None:
    """A12「未配置：( [admin.ai_generate] ) 禁用，[admin.ai_not_configured]」；
    第 13 条「未配置时后台的 AI 按钮禁用并说明原因」；验收「GET …/status
    返回只有 configured 一个布尔字段的对象」：没有 AI 配置时回
    {"configured": false}，不调用服务层。"""
    body = _ok(_status(unconfigured_client, token))

    assert body == {"configured": False}
    assert calls == []


@pytest.mark.parametrize("missing", sorted(AI_CONFIG))
def test_status_is_false_when_any_setting_is_missing(
    engine: Engine, token: str, missing: str
) -> None:
    """第 13 条「接口地址、项目密钥与模型名经私有配置注入」「未配置时后台的
    AI 按钮禁用」：四项配置缺一时 configured 为 false（与服务层抛
    AiNotConfigured 的口径相同）。"""
    client = _client_for(engine, **(AI_CONFIG | {missing: ""}))

    assert _ok(_status(client, token)) == {"configured": False}


def test_status_is_false_when_the_hub_url_is_not_https(engine: Engine, token: str) -> None:
    """第 13 条「未配置时后台的 AI 按钮禁用」：接口地址不是 https:// 时
    客户端视为未配置，configured 为 false。"""
    client = _client_for(engine, **(AI_CONFIG | {"ai_hub_base_url": "http://hub.example.test"}))

    assert _ok(_status(client, token)) == {"configured": False}


def test_status_is_true_when_ai_is_configured(
    client: TestClient, token: str, calls: list[dict[str, Any]]
) -> None:
    """A12 AI 区在已配置时可用；验收「GET …/status 返回只有 configured
    一个布尔字段的对象」：四项配置齐全时回 {"configured": true}，只此一个
    字段；不调用服务层（不调用模型、不计数）。"""
    body = _ok(_status(client, token))

    assert body == {"configured": True}
    assert calls == []


def test_status_does_not_need_csrf(client: TestClient, token: str) -> None:
    """验收「POST 没有 CSRF 403」只针对提案：GET …/status 不带
    X-CSRF-Token 照常 200。"""
    response = client.get(STATUS_URL, headers=_headers(token, None))

    assert _ok(response) == {"configured": True}


# ---------------------------------------------------------------------------
# 提案的成功响应
# ---------------------------------------------------------------------------


def test_proposal_returns_only_the_proposal(
    client: TestClient, token: str, calls: list[dict[str, Any]], service: RecordingService
) -> None:
    """第 13 条「AI 的提案只含装饰 ID 与文案，没有改动规则、时间、开关或
    金额的字段」「不返回个人资料」；验收「成功 200 返回只有 proposal 一个
    字段的对象（字段同 DecorationProposal）」：响应只有 proposal，其字段
    依次为 DecorationProposal 的二十项，取值原样。"""
    service.outcome = PROPOSAL

    body = _ok(_post(client, token))

    assert list(body) == ["proposal"]
    assert list(body["proposal"]) == PROPOSAL_FIELDS
    assert body["proposal"] == {name: getattr(PROPOSAL, name) for name in PROPOSAL_FIELDS}
    assert len(calls) == 1


def test_proposal_passes_the_fields_through_to_the_service(
    client: TestClient, token: str, calls: list[dict[str, Any]], service: RecordingService
) -> None:
    """第 13 条「接口只传递与校验字段，出站内容与计数在 248 的服务层」：
    描述原样（首尾空白由服务层去掉）、规则按顺序转成 RuleInput（没有商品
    与分类 ID），连同按本应用配置构造的客户端、本应用的 Settings 与当前
    时间传给 propose_decoration，只调用一次。"""
    service.outcome = PROPOSAL

    _ok(_post(client, token, _body(description="  Raya  ")))

    assert len(calls) == 1
    call = calls[0]
    assert call["description"] == "  Raya  "
    assert tuple(call["rules"]) == RULES_PASSED
    assert all(rule.product_ids == () and rule.category_ids == () for rule in call["rules"])
    assert isinstance(call["client"], AiHubClient)
    assert call["client"].configured
    assert call["settings"].ai_model == AI_CONFIG["ai_model"]
    assert call["now"] == NOW


def test_proposal_accepts_empty_rules_and_omitted_params(
    client: TestClient, token: str, calls: list[dict[str, Any]], service: RecordingService
) -> None:
    """第 13 条「规则的类别与参数」：rules 可为空列表；规则只给 kind 时
    其余参数为 None、满额档为空，与 RuleInput 的默认值相同。"""
    service.outcome = PROPOSAL

    _ok(_post(client, token, _body(rules=[])))
    _ok(_post(client, token, _body(rules=[{"kind": "free_shipping"}])))

    assert [tuple(call["rules"]) for call in calls] == [(), (RuleInput(kind="free_shipping"),)]


def test_body_at_the_size_limit_is_accepted(
    client: TestClient, token: str, calls: list[dict[str, Any]], service: RecordingService
) -> None:
    """落实写法「请求体读取（上限 8 KB）」：恰好 8 KB（用 JSON 空白补足）的
    合法请求照常处理，回 200。"""
    service.outcome = PROPOSAL
    content = json.dumps(_body()).encode()
    content += b" " * (MAX_BODY_BYTES - len(content))
    assert len(content) == MAX_BODY_BYTES

    _ok(_raw(client, token, content, "application/json"))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 服务层的拒绝
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "status_code", "detail"),
    [
        pytest.param(AiNotConfigured(), 503, "ai_not_configured", id="not_configured"),
        pytest.param(AiDailyLimit(), 429, "ai_daily_limit", id="daily_limit"),
        pytest.param(AiUnavailable(), 503, "ai_unavailable", id="unavailable"),
        pytest.param(AiResultInvalid(), 502, "ai_result_invalid", id="result_invalid"),
    ],
)
def test_service_errors_map_to_fixed_codes(
    client: TestClient,
    token: str,
    calls: list[dict[str, Any]],
    service: RecordingService,
    exc: Exception,
    status_code: int,
    detail: str,
) -> None:
    """第 13 条「未配置时后台的 AI 按钮禁用并说明原因」「已达上限即拒绝并
    说明」「不合格即整份不用，回答『AI 结果不合格』」；A12「失败：
    [admin.ai_invalid] / [admin.ai_unavailable] / [admin.ai_daily_limit]」；
    验收的错误码：AiNotConfigured 503 ai_not_configured、AiDailyLimit 429
    ai_daily_limit、AiUnavailable 503 ai_unavailable、AiResultInvalid 502
    ai_result_invalid，只有固定的 detail，不带异常消息。"""
    service.outcome = exc

    response = _post(client, token)

    _assert_error(response, status_code, {"detail": detail})
    assert str(exc) not in response.text
    assert len(calls) == 1


def test_promotion_invalid_lists_the_problems(
    client: TestClient, token: str, calls: list[dict[str, Any]], service: RecordingService
) -> None:
    """第 13 条「输入一段描述（1–500 个字符）」；验收「PromotionInvalid 为
    422 promotion_invalid（带问题列表，写法同 SHOP-TASK-242）」：detail 为
    code 与 problems，每项 code、field、product_id、product_name，
    按服务层的顺序。"""
    service.outcome = PromotionInvalid(
        [
            PromotionProblem("description", "description"),
            PromotionProblem("rule", "rules.2.percent"),
        ]
    )

    response = _post(client, token)

    expected = {
        "code": "promotion_invalid",
        "problems": [_problem("description", "description"), _problem("rule", "rules.2.percent")],
    }
    _assert_error(response, 422, {"detail": expected})
    assert len(calls) == 1


@pytest.mark.parametrize("description", ["", "   ", "x" * 501], ids=["empty", "blank", "long"])
def test_real_service_rejects_a_bad_description(
    client: TestClient, token: str, calls: list[dict[str, Any]], description: str
) -> None:
    """第 13 条「输入一段描述（1–500 个字符）」：不用替身，服务层对空、
    全空白与超长的描述抛 PromotionInvalid，接口回 422 promotion_invalid
    与 description 一项问题。"""
    response = _post(client, token, _body(description=description))

    expected = {"code": "promotion_invalid", "problems": [_problem("description", "description")]}
    _assert_error(response, 422, {"detail": expected})
    assert len(calls) == 1


def test_real_service_refuses_when_not_configured(
    unconfigured_client: TestClient, token: str, calls: list[dict[str, Any]]
) -> None:
    """第 13 条「未配置时后台的 AI 按钮禁用并说明原因，手动配置不受影响」：
    不用替身，没有 AI 配置时提案回 503 ai_not_configured（服务层不计数、
    不调用）。"""
    response = _post(unconfigured_client, token)

    _assert_error(response, 503, {"detail": "ai_not_configured"})
    assert len(calls) == 1
    assert calls[0]["client"].configured is False


# ---------------------------------------------------------------------------
# 401、403、413、415、422 的顺序，都不调用服务层
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_state", ["missing", "malformed", "unknown"])
def test_both_endpoints_require_an_admin_session(
    client: TestClient, calls: list[dict[str, Any]], session_state: str
) -> None:
    """验收「没有会话 401 admin_session_required」；第 13 条「管理员在活动
    编辑页」请求提案：没有 cookie、格式不对与库里没有的会话，状态与提案
    都 401；没有 CSRF、请求体结构不合格时也先 401，不调用服务层。"""
    token = {"missing": None, "malformed": "not-a-token", "unknown": "A" * 43}[session_state]

    responses = [
        _status(client, token),
        _post(client, token, csrf="0" * 64),
        _post(client, token, csrf=None),
        _post(client, token, {"description": SECRET, "extra": 1}, csrf="0" * 64),
    ]

    for response in responses:
        _assert_error(response, 401, SESSION_REQUIRED)
    assert calls == []


@pytest.mark.parametrize("csrf", [None, "", "0" * 64], ids=["missing", "empty", "wrong"])
def test_proposal_without_a_valid_csrf_token_is_403(
    client: TestClient, token: str, calls: list[dict[str, Any]], csrf: str | None
) -> None:
    """验收「POST 没有 CSRF 403 csrf_failed」；落实写法「照 admin_categories
    的 CSRF」（先 CSRF、再请求体）：X-CSRF-Token 缺少、为空或不一致时 403，
    请求体结构不合格的也先 403，不调用服务层。"""
    responses = [
        _post(client, token, csrf=csrf),
        _post(client, token, {"description": SECRET, "extra": 1}, csrf=csrf),
    ]

    for response in responses:
        _assert_error(response, 403, CSRF_FAILED)
    assert calls == []


@pytest.mark.parametrize("logged_in", [True, False], ids=["session", "no_session"])
def test_body_over_the_limit_is_413_before_anything_else(
    client: TestClient, token: str, calls: list[dict[str, Any]], logged_in: bool
) -> None:
    """验收「请求体超过 8 KB 413」；落实写法「照 admin_categories 的请求体
    读取（上限 8 KB）」（先于会话与 CSRF）：多一个字节即 413，有没有会话
    都一样，不调用服务层。"""
    content = json.dumps(_body()).encode()
    content += b" " * (MAX_BODY_BYTES + 1 - len(content))

    response = _raw(client, token if logged_in else None, content, "application/json")

    _assert_error(response, 413, TOO_LARGE)
    assert calls == []


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
@pytest.mark.parametrize("logged_in", [True, False], ids=["session", "no_session"])
def test_non_json_is_415(
    client: TestClient,
    token: str,
    calls: list[dict[str, Any]],
    content_type: str | None,
    logged_in: bool,
) -> None:
    """验收「不是 JSON 415」（照 admin_categories 的 WriteBodyDep，先于会话
    与 CSRF）：Content-Type 不是 JSON 时有没有会话都 415，不调用服务层。"""
    content = json.dumps(_body()).encode()

    response = _raw(client, token if logged_in else None, content, content_type)

    _assert_error(response, 415, NOT_JSON)
    assert calls == []


def _tier(**fields: Any) -> dict[str, Any]:
    return {"threshold_sen": 1, "amount_sen": 1, "percent": None} | fields


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"rules": []}, id="missing_description"),
        pytest.param({"description": "Raya"}, id="missing_rules"),
        pytest.param(_body(description=1), id="description_not_string"),
        pytest.param(_body(description=None), id="description_null"),
        pytest.param(_body(rules={}), id="rules_not_list"),
        pytest.param(_body(extra=SECRET), id="extra_field"),
        pytest.param(_body(rules=[{"percent": 10}]), id="rule_without_kind"),
        pytest.param(_body(rules=[{"kind": "x", "product_ids": [1]}]), id="product_ids"),
        pytest.param(_body(rules=[{"kind": "x", "category_ids": [1]}]), id="category_ids"),
        pytest.param(_body(rules=[{"kind": "x", "percent": "10"}]), id="int_as_string"),
        pytest.param(_body(rules=[{"kind": "x", "percent": True}]), id="int_as_bool"),
        pytest.param(_body(rules=[{"kind": "x", "percent": 1.5}]), id="int_as_float"),
        pytest.param(_body(rules=[{"kind": "x", "name": SECRET}]), id="rule_extra"),
        pytest.param(
            _body(rules=[{"kind": "x", "tiers": [{"threshold_sen": 1}]}]), id="tier_missing"
        ),
        pytest.param(_body(rules=[{"kind": "x", "tiers": [_tier(x=SECRET)]}]), id="tier_extra"),
        pytest.param([SECRET], id="not_object"),
    ],
)
def test_bad_structure_is_422_without_echo(
    client: TestClient, token: str, calls: list[dict[str, Any]], body: Any
) -> None:
    """验收「结构或类型不对 422（不回显）」「rules 每项为 kind 与 RuleInput
    的参数字段，不含商品与分类 ID」；落实写法「请求模型 extra forbid 与
    strict」「422 不回显（app/api/pay.py 的 _body_errors）」：缺字段、类型
    不对（整数不收字符串、布尔值与浮点数）、多出字段（含 product_ids 与
    category_ids）都 422，每条错误只有位置、类型与固定消息，不调用服务层。"""
    response = _post(client, token, body)

    errors = _assert_plain_errors(response)
    assert all(error["loc"][0] == "body" for error in errors)
    assert calls == []


def test_malformed_json_is_422_without_echo(
    client: TestClient, token: str, calls: list[dict[str, Any]]
) -> None:
    """验收「结构或类型不对 422（不回显）」：Content-Type 为 JSON 但内容
    不是合法 JSON 时 422，不回显所给的内容，不调用服务层。"""
    content = json.dumps({"description": SECRET}).encode()[:-1]

    response = _raw(client, token, content, "application/json")

    _assert_plain_errors(response)
    assert calls == []


# ---------------------------------------------------------------------------
# 路由与 no-store
# ---------------------------------------------------------------------------


def test_routes_are_outside_the_promotion_id_routes(
    client: TestClient, token: str, service: RecordingService
) -> None:
    """落实写法「路径放在 /api/admin/promotion-ai/ 下，不放在
    /api/admin/promotions/ 下（免得被活动 ID 的路由先匹配）」：两个路径都由
    本模块回答（状态与提案都 200），不是活动接口的路径 ID 422。"""
    service.outcome = PROPOSAL

    assert _ok(_status(client, token)) == {"configured": True}
    assert list(_ok(_post(client, token))) == ["proposal"]


def test_every_response_is_no_store(
    client: TestClient, token: str, service: RecordingService
) -> None:
    """落实写法「_NoStoreRoute 写法」；验收「所有响应带 no-store」：状态、
    成功的提案、401、403、413、415、422 与服务层的拒绝都带
    Cache-Control: no-store。"""
    big = json.dumps(_body(description="x" * MAX_BODY_BYTES)).encode()
    responses = [
        _status(client, token),
        _status(client, None),
        _post(client, None),
        _post(client, token, csrf=None),
        _raw(client, token, big, "application/json"),
        _raw(client, token, b"{}", "text/plain"),
        _post(client, token, {"description": 1, "rules": []}),
    ]
    service.outcome = AiDailyLimit()
    responses.append(_post(client, token))
    service.outcome = PROPOSAL
    responses.append(_post(client, token))

    statuses = [response.status_code for response in responses]
    assert statuses == [200, 401, 401, 403, 413, 415, 422, 429, 200]
    for response in responses:
        _assert_no_store(response)

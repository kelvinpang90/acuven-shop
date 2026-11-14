"""AI 生成装饰提案的服务层（SHOP-TASK-248）。

覆盖 app/services/promotion_ai.py。依据 docs/DESIGN.md 1.23（提交 b7f9355）
「节日促销活动」第 12、13 条（下称「第 N 条」）；每条测试的文档字符串引用
它守住的那一句。

用 SQLite 内存库按模型建表，引擎设置照 tests/test_sms_budget.py（关掉驱动的
事务处理、由引擎发 BEGIN），保存点才和 MySQL 上一样。SQLite 会忽略
FOR UPDATE，也不复现 MySQL 的阻塞等待；并发只用两个会话按先后顺序模拟。
模型经替身客户端调用：真实的 AiHubClient 配上记录请求的替身传输函数
（写法照 tests/test_ai_hub.py），不发网络请求，测试可直接检查送出的请求体。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import fields
from datetime import date, datetime

import pytest
from sqlalchemy import Engine, create_engine, event, func, insert, inspect, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import (
    Promotion,
    PromotionAiCall,
    PromotionAiDailyUsage,
    PromotionDecoration,
    StoreDesignSetting,
)
from app.models.promotion import (
    DECORATION_CHOICES,
    KIND_BUY_X_GET_Y,
    KIND_FREE_SHIPPING,
    KIND_ITEM_DISCOUNT,
    KIND_SPEND_DISCOUNT,
)
from app.models.promotion_ai import (
    AI_RESULT_ERROR,
    AI_RESULT_INVALID,
    AI_RESULT_OK,
    AI_RESULT_TIMEOUT,
)
from app.services import promotion_ai
from app.services.admin_promotions import (
    COPY_MAX_LENGTHS,
    PromotionInvalid,
    RuleInput,
    TierInput,
)
from app.services.ai_hub import (
    AiHubClient,
    AiNotConfigured,
    HttpRequest,
    HttpResponse,
    TransportError,
    TransportTimeout,
)
from app.services.promotion_ai import (
    MAX_TOKENS,
    OUTPUT_SCHEMA,
    PROPOSAL_FIELDS,
    SYSTEM_PROMPT,
    AiDailyLimit,
    AiResultInvalid,
    AiUnavailable,
    DecorationProposal,
    propose_decoration,
)

# UTC 03:00 即马来西亚 11:00，日期 2026-11-14。
NOW = datetime(2026, 11, 14, 3, 0)
TODAY = date(2026, 11, 14)

BASE_URL = "https://hub.example.test"
DESCRIPTION = "Hari Raya sale for home goods, green and gold, warm and friendly"

# 商品与分类 ID 取不会出现在提示词与清单里的数。
PRODUCT_ID = 987_654
CATEGORY_IDS = (876_543, 765_432)

RULES = (
    RuleInput(
        kind=KIND_ITEM_DISCOUNT,
        discount_mode="percent",
        percent=20,
        product_ids=(PRODUCT_ID,),
        category_ids=(CATEGORY_IDS[0],),
    ),
    RuleInput(
        kind=KIND_BUY_X_GET_Y,
        buy_quantity=2,
        get_quantity=1,
        get_percent=100,
        category_ids=(CATEGORY_IDS[1],),
    ),
    RuleInput(
        kind=KIND_SPEND_DISCOUNT,
        discount_mode="amount",
        tiers=(TierInput(5000, 500, None), TierInput(10000, 1200, None)),
    ),
    RuleInput(kind=KIND_FREE_SHIPPING, threshold_sen=6000),
)
SENT_RULES = [
    {"kind": "item_discount", "discount_mode": "percent", "percent": 20},
    {"kind": "buy_x_get_y", "buy_quantity": 2, "get_quantity": 1, "get_percent": 100},
    {
        "kind": "spend_discount",
        "discount_mode": "amount",
        "tiers": [
            {"threshold_sen": 5000, "amount_sen": 500},
            {"threshold_sen": 10000, "amount_sen": 1200},
        ],
    },
    {"kind": "free_shipping", "threshold_sen": 6000},
]

VALID = {
    "pack": "raya",
    "palette": "raya",
    "ambient": "garland",
    "home_poster": "split",
    "list_strip": "band",
    "card_badge": "tag",
    "detail_ribbon": "ribbon",
    "cart_progress": "bar",
    "title_en": "  Hari Raya Home Sale  ",
    "title_zh": "开斋节家居特卖",
    "title_ms": "Jualan Rumah Hari Raya",
    "subtitle_en": "Buy 2, get 1 free on home goods",
    "subtitle_zh": "",
    "subtitle_ms": "Beli 2 percuma 1",
    "badge_en": "Buy 2 Get 1",
    "badge_zh": "买二送一",
    "badge_ms": "",
    "strip_en": "Free shipping from RM60",
    "strip_zh": "满 RM60 免运费",
    "strip_ms": "",
}
INPUT_TOKENS = 321
OUTPUT_TOKENS = 45
RECORD_COLUMNS = (
    PromotionAiCall.result,
    PromotionAiCall.input_tokens,
    PromotionAiCall.output_tokens,
)


# ---------------------------------------------------------------------------
# 夹具与数据
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)

    # 照 tests/test_sms_budget.py：关掉驱动的事务处理、由引擎发 BEGIN，
    # 保存点才和 MySQL 上一样。
    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection, _connection_record) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.rollback()
        yield session


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


def make_client(*outcomes: HttpResponse | BaseException) -> tuple[AiHubClient, RecordingTransport]:
    transport = RecordingTransport(*outcomes)
    return AiHubClient(BASE_URL, "shop-client", "project-key", "model-x", transport), transport


def make_settings(limit: int = 50) -> Settings:
    return Settings(_env_file=None, ai_daily_call_limit=limit)


def reply(answer: str) -> HttpResponse:
    body = {
        "text": answer,
        "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS},
    }
    return HttpResponse(200, json.dumps(body).encode())


def ok_reply(payload: object = VALID) -> HttpResponse:
    return reply(json.dumps(payload, ensure_ascii=False))


def with_changes(**changes: object) -> dict[str, object]:
    return {**VALID, **changes}


def without(name: str) -> dict[str, object]:
    return {key: value for key, value in VALID.items() if key != name}


def propose(
    db: Session,
    client: AiHubClient,
    settings: Settings | None = None,
    description: str = DESCRIPTION,
    rules: tuple[RuleInput, ...] = RULES,
    now: datetime = NOW,
) -> DecorationProposal:
    return propose_decoration(db, client, settings or make_settings(), description, rules, now)


def call_count(db: Session, day: date = TODAY) -> int | None:
    """该日期的调用次数；没有行为空。读完结束事务。"""
    value = db.scalar(
        select(PromotionAiDailyUsage.call_count).where(PromotionAiDailyUsage.usage_date == day)
    )
    db.rollback()
    return value


def records(db: Session) -> list[tuple[str, int | None, int | None]]:
    """调用记录的（结果类别, 输入 token, 输出 token），按 ID 排序。读完结束事务。"""
    rows = db.execute(select(*RECORD_COLUMNS).order_by(PromotionAiCall.id)).all()
    db.rollback()
    return [tuple(row) for row in rows]


def sent(transport: RecordingTransport, index: int = 0) -> dict[str, object]:
    return json.loads(transport.requests[index].body)


def sent_user(transport: RecordingTransport, index: int = 0) -> dict[str, object]:
    messages = sent(transport, index)["messages"]
    assert len(messages) == 1
    return json.loads(messages[0]["content"])


# ---------------------------------------------------------------------------
# 合格的提案
# ---------------------------------------------------------------------------


def test_valid_proposal_is_returned_not_saved_and_recorded_ok(db: Session) -> None:
    """守住第 13 条「不自动保存：合格的提案只返回给编辑页」与「AI 的提案只含
    装饰 ID 与文案，没有改动规则、时间、开关或金额的字段」：返回八个装饰 ID
    与四种文案各三语（去掉首尾空白，空串为空），不写活动与装饰表；
    记一条 ok 与两个 token 数，占用当天一次。
    """
    client, transport = make_client(ok_reply())

    proposal = propose(db, client)

    assert {f.name for f in fields(DecorationProposal)} == set(PROPOSAL_FIELDS)
    assert len(PROPOSAL_FIELDS) == 20
    assert proposal.pack == "raya"
    assert proposal.cart_progress == "bar"
    assert proposal.title_en == "Hari Raya Home Sale"
    assert proposal.badge_zh == "买二送一"
    assert proposal.subtitle_zh is None
    assert proposal.strip_ms is None
    assert len(transport.requests) == 1
    assert call_count(db) == 1
    assert records(db) == [(AI_RESULT_OK, INPUT_TOKENS, OUTPUT_TOKENS)]
    assert db.scalar(select(func.count()).select_from(Promotion)) == 0
    assert db.scalar(select(func.count()).select_from(PromotionDecoration)) == 0


def test_copy_at_length_limits_and_emoji_is_accepted(db: Session) -> None:
    """守住第 12 条「标题（1–40 个字符）、副标题（0–80）、角标短语（0–12）、
    横条文字（0–60）」：恰在上限的文案与带零宽连接符的 emoji 合格。
    """
    payload = with_changes(
        title_en="T" * COPY_MAX_LENGTHS["title"],
        subtitle_ms="S" * COPY_MAX_LENGTHS["subtitle"],
        badge_en="B" * COPY_MAX_LENGTHS["badge"],
        strip_zh="横" * COPY_MAX_LENGTHS["strip"],
        title_zh="家庭 \U0001f468‍\U0001f469‍\U0001f467",
    )
    client, _ = make_client(ok_reply(payload))

    proposal = propose(db, client)

    assert proposal.title_en == "T" * 40
    assert proposal.strip_zh == "横" * 60
    assert records(db) == [(AI_RESULT_OK, INPUT_TOKENS, OUTPUT_TOKENS)]


# ---------------------------------------------------------------------------
# 不合格的回答
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(json.dumps(with_changes(pack="diwali")), id="pack-not-in-list"),
        pytest.param(json.dumps(with_changes(palette="neon")), id="palette-not-in-list"),
        pytest.param(json.dumps(with_changes(ambient="snow")), id="ambient-not-in-list"),
        pytest.param(json.dumps(with_changes(card_badge="")), id="empty-id"),
        pytest.param(json.dumps(with_changes(title_en="T" * 41)), id="title-41-chars"),
        pytest.param(json.dumps(with_changes(badge_zh="买" * 13)), id="badge-13-chars"),
        pytest.param(json.dumps(with_changes(strip_ms="S" * 61)), id="strip-61-chars"),
        pytest.param(json.dumps(with_changes(subtitle_en="Line one\nline two")), id="newline"),
        pytest.param(json.dumps(with_changes(title_ms="\nJualan")), id="leading-newline"),
        pytest.param(json.dumps(with_changes(badge_en="Sale ")), id="line-separator"),
        pytest.param(json.dumps(with_changes(strip_en="Free\x07ship")), id="control-char"),
        pytest.param(json.dumps(with_changes(title_en="   ")), id="blank-english-title"),
        pytest.param(json.dumps(with_changes(title_en="")), id="empty-english-title"),
        pytest.param(json.dumps(without("title_en")), id="missing-english-title"),
        pytest.param(json.dumps(without("strip_ms")), id="missing-field"),
        pytest.param(json.dumps(with_changes(poster_mode="template")), id="extra-poster-mode"),
        pytest.param(json.dumps(with_changes(percent=50)), id="extra-rule-field"),
        pytest.param(json.dumps(with_changes(badge_en=None)), id="null-value"),
        pytest.param(json.dumps(with_changes(home_poster=1)), id="number-value"),
        pytest.param("Sure! Here is a festive proposal.", id="not-json"),
        pytest.param("```json\n" + json.dumps(VALID) + "\n```", id="code-fence"),
        pytest.param(json.dumps([VALID]), id="json-array"),
        pytest.param(json.dumps(VALID) + json.dumps(VALID), id="two-objects"),
        pytest.param(
            # 重复的字段取的是清单里的值，单看取值合格；字段名重复本身不合格。
            json.dumps(VALID)[:-1] + ', "pack": "cny"}',
            id="duplicate-key",
        ),
    ],
)
def test_any_invalid_item_rejects_whole_answer(db: Session, answer: str) -> None:
    """守住第 13 条「要求模型只回一个 JSON 对象；服务端按第 12 条逐项校验
    （ID 都在清单里，文案长度与字符合格），任何一项不合格即整份不用，回答
    「AI 结果不合格」，不部分采用」与第 12 条「英文标题必填」「不允许换行与
    控制字符」：抛 AiResultInvalid，记 invalid 与 token 数，仍占用当天一次。
    """
    client, _ = make_client(reply(answer))

    with pytest.raises(AiResultInvalid):
        propose(db, client)

    assert records(db) == [(AI_RESULT_INVALID, INPUT_TOKENS, OUTPUT_TOKENS)]
    assert call_count(db) == 1


def test_invalid_result_message_has_no_answer_text(db: Session) -> None:
    """守住第 13 条「不记……模型回答原文」：AiResultInvalid 的消息与 repr
    不带回答原文。
    """
    answer = json.dumps(with_changes(title_en="Secret answer " + "x" * 40))
    client, _ = make_client(reply(answer))

    with pytest.raises(AiResultInvalid) as caught:
        propose(db, client)

    assert "Secret answer" not in str(caught.value)
    assert "Secret answer" not in repr(caught.value)


# ---------------------------------------------------------------------------
# 每日上限与计数
# ---------------------------------------------------------------------------


def test_51st_call_is_refused_without_calling_when_limit_is_50(db: Session) -> None:
    """守住第 13 条「全站每天（马来西亚时间）调用次数上限……（默认 50）」与
    「已达上限即拒绝并说明，不调用」：前 50 次照常调用，第 51 次抛 AiDailyLimit，
    不发请求、不计数、不写记录。
    """
    settings = make_settings(50)
    client, transport = make_client(*(ok_reply() for _ in range(50)))
    for _ in range(50):
        propose(db, client, settings)

    with pytest.raises(AiDailyLimit):
        propose(db, client, settings)

    assert len(transport.requests) == 50
    assert call_count(db) == 50
    assert len(records(db)) == 50


def test_limit_is_counted_per_malaysia_date(db: Session) -> None:
    """守住第 13 条「计数表以马来西亚日期为主键（每天至多一行）」：
    UTC 16:00 起算马来西亚的下一天，前一天用满不影响下一天。
    """
    settings = make_settings(1)
    client, _ = make_client(ok_reply(), ok_reply())

    propose(db, client, settings, now=datetime(2026, 11, 14, 15, 59))
    with pytest.raises(AiDailyLimit):
        propose(db, client, settings, now=datetime(2026, 11, 14, 15, 59, 59))
    propose(db, client, settings, now=datetime(2026, 11, 14, 16, 0))

    assert call_count(db, date(2026, 11, 14)) == 1
    assert call_count(db, date(2026, 11, 15)) == 1


def test_first_call_of_the_day_inserts_zero_row_then_counts(db: Session) -> None:
    """守住第 13 条「调用前先以「不存在则插入、主键冲突即忽略」确保当天的行
    存在，再在一个事务里锁住该行，未达上限才加一并提交」：当天没有行时
    插入一行并加到 1，下一次在同一行上累加，不另插行。
    """
    client, _ = make_client(ok_reply(), ok_reply())
    assert call_count(db) is None

    propose(db, client)
    assert call_count(db) == 1
    propose(db, client)

    assert call_count(db) == 2
    assert db.scalar(select(func.count()).select_from(PromotionAiDailyUsage)) == 1


def test_two_sessions_each_insert_today_row_without_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """守住第 13 条「先以「不存在则插入、主键冲突即忽略」确保当天的行存在，
    再……锁住该行」与「并发请求也不会超过上限」：两个会话的普通查询都没看到
    当天的行（MySQL 上快照读可能如此）而各插一次，后插者撞上主键冲突即忽略，
    锁行后在前者提交的数上加一，不报错；上限 2 时第三次被拒。
    """
    monkeypatch.setattr(promotion_ai, "_find", lambda _db, _day: None)
    settings = make_settings(2)
    client, transport = make_client(ok_reply(), ok_reply())

    with Session(engine) as first, Session(engine) as second:
        propose(first, client, settings)
        propose(second, client, settings)
        with pytest.raises(AiDailyLimit):
            propose(first, client, settings)

    assert len(transport.requests) == 2
    with Session(engine) as check:
        assert call_count(check) == 2
        assert check.scalar(select(func.count()).select_from(PromotionAiDailyUsage)) == 1


@pytest.mark.parametrize(
    ("outcome", "result"),
    [
        pytest.param(TransportTimeout(), AI_RESULT_TIMEOUT, id="timeout"),
        pytest.param(TransportError(), AI_RESULT_ERROR, id="network-error"),
        pytest.param(HttpResponse(402, b'{"error": "balance"}'), AI_RESULT_ERROR, id="balance"),
        pytest.param(HttpResponse(500, b""), AI_RESULT_ERROR, id="server-error"),
        pytest.param(HttpResponse(200, b"not json"), AI_RESULT_ERROR, id="bad-envelope"),
    ],
)
def test_failed_call_still_uses_one_and_is_not_refunded(
    db: Session, outcome: HttpResponse | BaseException, result: str
) -> None:
    """守住第 13 条「加一之后的调用无论成功、不合格、超时或出错都占用当天的
    一次，不退回」与「Billing Hub 余额不足、超时或出错时回答「AI 暂不可用」」：
    抛 AiUnavailable，记 timeout 或 error（没有用量），上限 1 时当天不能再调用。
    """
    settings = make_settings(1)
    client, transport = make_client(outcome)

    with pytest.raises(AiUnavailable):
        propose(db, client, settings)
    with pytest.raises(AiDailyLimit):
        propose(db, client, settings)

    assert len(transport.requests) == 1
    assert call_count(db) == 1
    assert records(db) == [(result, None, None)]


# ---------------------------------------------------------------------------
# 输入不合格与未配置
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        pytest.param("", id="empty"),
        pytest.param(" \t ", id="blank"),
        pytest.param("x" * 501, id="501-chars"),
        pytest.param("  " + "x" * 501 + "  ", id="501-chars-padded"),
    ],
)
def test_invalid_description_is_refused_without_counting(db: Session, description: str) -> None:
    """守住第 13 条「输入一段描述（1–500 个字符）」与「已达上限即拒绝……不调用」
    的计数口径：描述去掉首尾空白后不在 1–500 个字符时抛 PromotionInvalid
    （description），不调用、不计数、不写记录。
    """
    client, transport = make_client(ok_reply())

    with pytest.raises(PromotionInvalid) as caught:
        propose(db, client, description=description)

    assert [(p.code, p.field) for p in caught.value.problems] == [("description", "description")]
    assert transport.requests == []
    assert call_count(db) is None
    assert records(db) == []


@pytest.mark.parametrize("description", ["x", "  " + "x" * 500 + "\n"])
def test_description_at_bounds_is_sent_stripped(db: Session, description: str) -> None:
    """守住第 13 条「输入一段描述（1–500 个字符）」：去掉首尾空白后 1 与 500
    个字符合格，送出的是去掉首尾空白后的描述。
    """
    client, transport = make_client(ok_reply())

    propose(db, client, description=description)

    assert sent_user(transport)["description"] == description.strip()


def test_invalid_rules_are_refused_without_counting(db: Session) -> None:
    """守住第 13 条「活动的规则类别与参数（只作为写文案的参考……）」：送出的
    规则先以 check_rule_params 检查，不合格抛 PromotionInvalid（field 带
    rules.<序号>. 前缀），不调用、不计数、不写记录。
    """
    rules = (
        RuleInput(kind=KIND_FREE_SHIPPING, threshold_sen=6000),
        RuleInput(kind=KIND_ITEM_DISCOUNT, discount_mode="percent", percent=91),
        RuleInput(kind="lucky_draw"),
    )
    client, transport = make_client(ok_reply())

    with pytest.raises(PromotionInvalid) as caught:
        propose(db, client, rules=rules)

    assert [(p.code, p.field) for p in caught.value.problems] == [
        ("rule", "rules.2.percent"),
        ("rule", "rules.3.kind"),
    ]
    assert transport.requests == []
    assert call_count(db) is None
    assert records(db) == []


def test_not_configured_is_refused_without_counting(db: Session) -> None:
    """守住第 13 条「未配置时后台的 AI 按钮禁用并说明原因」：客户端未配置时
    抛 AiNotConfigured，不调用、不计数、不写记录。
    """
    transport = RecordingTransport(ok_reply())
    client = AiHubClient(BASE_URL, "", "", "", transport)

    with pytest.raises(AiNotConfigured):
        propose(db, client)

    assert transport.requests == []
    assert call_count(db) is None
    assert records(db) == []


# ---------------------------------------------------------------------------
# 送出的内容
# ---------------------------------------------------------------------------


def test_request_carries_only_the_four_items(db: Session) -> None:
    """守住第 13 条「送出的内容只有：这段描述、活动的规则类别与参数……、
    第 12 条的清单与文案长度限制、当前店铺主题与主色 ID」与「系统不从订单、
    会员或收货资料取任何内容送出」：用户内容只有描述、规则（不含商品与分类
    ID）、主题与主色；系统提示与 output_schema 只由清单与长度常量组成；
    max_tokens 不超过 2000。
    """
    client, transport = make_client(ok_reply())

    propose(db, client)

    body = sent(transport)
    assert set(body) == {"feature", "model", "system", "messages", "max_tokens", "output_config"}
    assert body["system"] == SYSTEM_PROMPT
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert body["max_tokens"] == MAX_TOKENS <= 2000
    assert sent_user(transport) == {
        "description": DESCRIPTION,
        "rules": SENT_RULES,
        "theme": "pandan",
        "accent": "pandan",
    }
    raw = transport.requests[0].body.decode()
    for scope_id in (PRODUCT_ID, *CATEGORY_IDS):
        assert str(scope_id) not in raw
    assert "product_ids" not in raw
    assert "category_ids" not in raw


def test_system_prompt_states_reply_format_lists_limits_and_fixed_rules() -> None:
    """守住第 13 条「要求模型只回一个 JSON 对象」「模型的回答不能改动」规则与
    第 12 条的清单、文案长度与「不允许换行与控制字符」：系统提示写明这些，
    output_schema 以字段名、清单与长度约束回答，不允许多出字段。
    """
    assert "exactly one JSON object" in SYSTEM_PROMPT
    for name, choices in DECORATION_CHOICES.items():
        assert f"- {name}: one of {', '.join(choices)}" in SYSTEM_PROMPT
    for kind in ("title", "subtitle", "badge", "strip"):
        assert f"at most {COPY_MAX_LENGTHS[kind]} characters" in SYSTEM_PROMPT
    assert "no line breaks" in SYSTEM_PROMPT
    assert "control characters" in SYSTEM_PROMPT
    assert "do not change, add or remove rules" in SYSTEM_PROMPT

    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["required"]) == set(PROPOSAL_FIELDS)
    properties = OUTPUT_SCHEMA["properties"]
    assert set(properties) == set(PROPOSAL_FIELDS)
    assert properties["pack"]["enum"] == list(DECORATION_CHOICES["pack"])
    assert properties["title_en"] == {"type": "string", "maxLength": 40, "minLength": 1}
    assert properties["badge_ms"] == {"type": "string", "maxLength": 12}


def test_theme_and_accent_follow_current_store_design(db: Session) -> None:
    """守住第 13 条「当前店铺主题与主色 ID」：每次调用时从店铺装修单例行读出；
    还没有那一行时为默认主题，主色为空时送出该主题的默认主色（THEME_ACCENTS
    的第一项，不送 null），改过装修后送出新值。
    """
    client, transport = make_client(ok_reply(), ok_reply(), ok_reply(), ok_reply())

    propose(db, client)
    db.add(StoreDesignSetting(theme="batik", accent=None))
    db.commit()
    propose(db, client)
    db.execute(update(StoreDesignSetting).values(accent="maroon"))
    db.commit()
    propose(db, client)
    db.execute(update(StoreDesignSetting).values(theme="galeri", accent=None))
    db.commit()
    propose(db, client)

    sent_design = [
        (sent_user(transport, i)["theme"], sent_user(transport, i)["accent"]) for i in range(4)
    ]
    assert sent_design == [
        ("pandan", "pandan"),
        ("batik", "indigo"),
        ("batik", "maroon"),
        ("galeri", "black"),
    ]


def test_call_records_hold_no_description_or_answer(db: Session) -> None:
    """守住第 13 条「每次调用记时间、结果类别与用量（输入与输出 token 数），
    不记描述原文与模型回答原文」与「描述不保存」：记录表只有五列，
    各行的值里没有描述与回答的内容。
    """
    description = "Unique-desc-marker for the Raya sale"
    client, _ = make_client(ok_reply(), reply("Unique-answer-marker"))

    propose(db, client, description=description)
    with pytest.raises(AiResultInvalid):
        propose(db, client, description=description)

    table_columns = inspect(db.get_bind()).get_columns("promotion_ai_calls")
    columns = {column["name"] for column in table_columns}
    assert columns == {"id", "called_at", "result", "input_tokens", "output_tokens"}
    rows = db.execute(select(PromotionAiCall.__table__)).all()
    db.rollback()
    assert len(rows) == 2
    for row in rows:
        for value in row:
            assert "Unique-desc-marker" not in str(value)
            assert "Unique-answer-marker" not in str(value)
            assert VALID["title_zh"] not in str(value)
    assert [row.called_at for row in rows] == [NOW, NOW]


def test_existing_today_row_is_incremented_up_to_limit(db: Session) -> None:
    """守住第 13 条「计数表以马来西亚日期为主键（每天至多一行）」：已有当天的
    行（如别的进程刚插入）时在原行上加一，达到上限即拒绝。
    """
    db.execute(insert(PromotionAiDailyUsage).values(usage_date=TODAY, call_count=49))
    db.commit()
    client, transport = make_client(ok_reply())

    propose(db, client)
    with pytest.raises(AiDailyLimit):
        propose(db, client)

    assert len(transport.requests) == 1
    assert call_count(db) == 50

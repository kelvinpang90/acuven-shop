"""后台促销活动的输入取值检查、时间换算与活动状态（不碰数据库）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条
（下称「第 N 条」）与 SHOP-TASK-239 的落实写法。每条测试的文档字符串
写明它守住的第几条哪一句。
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.promotion import (
    KIND_BUY_X_GET_Y,
    KIND_FREE_SHIPPING,
    KIND_ITEM_DISCOUNT,
    KIND_SPEND_DISCOUNT,
    MODE_AMOUNT,
    MODE_PERCENT,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
)
from app.services.admin_promotions import (
    CopyInput,
    DecorationInput,
    PromotionInput,
    PromotionInvalid,
    RuleInput,
    TierInput,
    check_copy,
    check_decoration,
    check_promotion_input,
    check_rule_params,
    check_rule_scope,
    promotion_state,
    to_local,
    to_utc,
)

ROOT = Path(__file__).resolve().parents[1]
TOKENS_PATH = ROOT / "docs" / "design" / "tokens" / "promotions.json"
TOKENS = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))

DECORATION = DecorationInput(
    pack="cny",
    palette="theme",
    ambient="garland",
    home_poster="split",
    list_strip="band",
    card_badge="tag",
    detail_ribbon="ribbon",
    cart_progress="bar",
    poster_mode="template",
)
ITEM_RULE = RuleInput(
    kind=KIND_ITEM_DISCOUNT,
    discount_mode=MODE_PERCENT,
    percent=20,
    product_ids=(1,),
)
BXGY_RULE = RuleInput(
    kind=KIND_BUY_X_GET_Y,
    buy_quantity=2,
    get_quantity=1,
    get_percent=100,
    category_ids=(3,),
)
SPEND_RULE = RuleInput(
    kind=KIND_SPEND_DISCOUNT,
    discount_mode=MODE_AMOUNT,
    tiers=(TierInput(5000, 500, None), TierInput(10000, 1200, None)),
)
FREE_RULE = RuleInput(kind=KIND_FREE_SHIPPING, threshold_sen=6000)

STARTS = datetime(2027, 1, 20, 0, 0)
ENDS = datetime(2027, 2, 10, 0, 0)
SECOND = timedelta(seconds=1)


def _input(**overrides) -> PromotionInput:
    values = {
        "name": "CNY 2027",
        "starts_at": "2027-01-20T00:00",
        "ends_at": "2027-02-10T23:59",
        "coupon_combinable": False,
        "points_combinable": False,
        "rules": (ITEM_RULE, BXGY_RULE, SPEND_RULE, FREE_RULE),
        "decoration": DECORATION,
        "copy": CopyInput(title_en="Lunar New Year Sale"),
    }
    values.update(overrides)
    return PromotionInput(**values)


def _codes(problems) -> list[tuple[str, str]]:
    return [(problem.code, problem.field) for problem in problems]


def _invalid(data: PromotionInput) -> list[tuple[str, str]]:
    with pytest.raises(PromotionInvalid) as info:
        check_promotion_input(data)
    return _codes(info.value.problems)


def _params(rule: RuleInput, **changes) -> list[tuple[str, str]]:
    return _codes(check_rule_params(dataclasses.replace(rule, **changes)))


def _scope(rule: RuleInput, **changes) -> list[tuple[str, str]]:
    return _codes(check_rule_scope(dataclasses.replace(rule, **changes)))


def _spend(*tiers: TierInput, mode: str = MODE_AMOUNT) -> list[tuple[str, str]]:
    return _params(SPEND_RULE, discount_mode=mode, tiers=tiers)


# ---- 整份输入 ----


def test_valid_input_passes_and_is_cleaned() -> None:
    """第 1 条「管理员在后台新建活动，填内部名称…开始与结束时间…
    两个同用开关…规则…以及装饰、海报与文案」与第 12 条「去掉首尾空白」：
    四类规则齐全的输入通过；返回的名称去掉首尾空白，文案去掉首尾空白、
    空串存为空。
    """
    copy = CopyInput(title_en=" Lunar New Year Sale ", subtitle_en="   ", badge_zh="")
    data = _input(name="  CNY 2027  ", copy=copy)

    cleaned = check_promotion_input(data)

    assert cleaned.name == "CNY 2027"
    assert cleaned.copy == CopyInput(title_en="Lunar New Year Sale")
    assert cleaned.rules == data.rules
    assert cleaned.decoration == DECORATION


def test_all_problems_are_listed_at_once() -> None:
    """第 3 条「重叠即回答『取值不合格』」与 UX A12「整次不生效：
    [admin.promo_invalid] 下逐条列出问题」：一次列出全部问题，按名称、
    时间、规则、装饰、文案的顺序；不是重叠的问题不带商品。
    """
    data = _input(
        name="",
        starts_at="2027-01-20 00:00",
        rules=(ITEM_RULE, dataclasses.replace(ITEM_RULE, percent=91)),
        decoration=dataclasses.replace(DECORATION, pack="festive"),
        copy=CopyInput(title_en="a" * 41),
    )

    with pytest.raises(PromotionInvalid) as info:
        check_promotion_input(data)

    assert _codes(info.value.problems) == [
        ("name", "name"),
        ("time", "starts_at"),
        ("rule", "rules.2.percent"),
        ("decoration", "decoration.pack"),
        ("copy", "copy.title_en"),
    ]
    assert isinstance(info.value.problems, tuple)
    for problem in info.value.problems:
        assert problem.product_id is None
        assert problem.product_name is None


def test_rule_problem_field_names_the_rule_position() -> None:
    """第 3 条「规则在活动内有排序，取值为从 1 起连续的整数…由保存请求
    里的顺序决定」：问题的 field 以 rules 里从 1 起的序号指出出错的规则。
    """
    rules = (
        FREE_RULE,
        dataclasses.replace(BXGY_RULE, category_ids=()),
        dataclasses.replace(SPEND_RULE, tiers=(TierInput(5000, 5000, None),)),
    )

    assert _invalid(_input(rules=rules)) == [
        ("scope", "rules.2.product_ids"),
        ("tiers", "rules.3.tiers.1.amount_sen"),
    ]


def test_structure_errors_raise_type_error() -> None:
    """落实写法「数据类的结构不对是调用方的错误」：两个同用开关
    （第 1 条）须为 bool，规则须为 RuleInput。
    """
    with pytest.raises(TypeError):
        check_promotion_input(_input(coupon_combinable=1))
    with pytest.raises(TypeError):
        check_promotion_input(_input(rules=({"kind": KIND_FREE_SHIPPING},)))


# ---- 内部名称（第 1 条） ----


@pytest.mark.parametrize(
    "name",
    [
        "a",
        "a" * 100,
        "  " + "a" * 100 + "  ",
        "新年 🧧 Sale",
    ],
)
def test_name_within_limits_passes(name: str) -> None:
    """第 1 条「填内部名称（只在后台显示，1–100 个字符）」：
    去掉首尾空白后 1 与 100 个字符都合格。
    """
    assert check_promotion_input(_input(name=name)).name == name.strip()


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("a" * 101, id="101"),
        pytest.param("CNY\n2027", id="newline"),
        pytest.param("CNY 2027", id="line-separator"),
    ],
)
def test_name_out_of_limits_is_rejected(name: str) -> None:
    """第 1 条「内部名称…1–100 个字符」与落实写法「内部名称同样去掉
    首尾空白、照同一字符规则检查」：空、全空白、101 个字符与含换行的
    名称问题码为 name。
    """
    assert _invalid(_input(name=name)) == [("name", "name")]


# ---- 时间（第 1 条） ----


def test_duration_of_exactly_92_days_passes() -> None:
    """第 1 条「结束须晚于开始，时长不超过 92 天」：恰好 92 天合格。"""
    data = _input(starts_at="2027-01-01T00:00", ends_at="2027-04-03T00:00")

    assert check_promotion_input(data) == data


def test_duration_one_minute_over_92_days_is_rejected() -> None:
    """第 1 条「时长不超过 92 天」：多一分钟问题码为 duration。"""
    data = _input(starts_at="2027-01-01T00:00", ends_at="2027-04-03T00:01")

    assert _invalid(data) == [("duration", "ends_at")]


@pytest.mark.parametrize("ends_at", ["2027-01-20T00:00", "2027-01-19T23:59"])
def test_end_not_after_start_is_rejected(ends_at: str) -> None:
    """第 1 条「结束须晚于开始」：结束等于或早于开始问题码为 time。"""
    data = _input(starts_at="2027-01-20T00:00", ends_at=ends_at)

    assert _invalid(data) == [("time", "ends_at")]


@pytest.mark.parametrize(
    ("starts_at", "ends_at"),
    [
        (None, None),
        ("2027-01-20T00:00", None),
        (None, "2027-02-10T23:59"),
    ],
)
def test_draft_times_may_be_missing(starts_at: str | None, ends_at: str | None) -> None:
    """第 2 条「草稿可任意修改」与 UX A12「草稿可以不填时间，发布时开始
    与结束都须填」：取值检查不要求时间（发布条件由 SHOP-TASK-249 查）。
    """
    data = _input(starts_at=starts_at, ends_at=ends_at)

    assert check_promotion_input(data) == data


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2027-01-20 00:00", id="space"),
        pytest.param("2027-01-20T00:00:00", id="seconds"),
        pytest.param("2027-01-20T00:00Z", id="zone"),
        pytest.param("2027-1-20T00:00", id="unpadded"),
        pytest.param("2027-02-29T00:00", id="no-such-day"),
        pytest.param("2027-01-20T24:00", id="hour-24"),
        pytest.param("２０２７-01-20T00:00", id="fullwidth-digits"),
        pytest.param("", id="empty"),
    ],
)
def test_time_format_is_checked(value: str) -> None:
    """第 1 条「后台按马来西亚时间输入，精确到分钟」：不是
    「YYYY-MM-DDTHH:MM」或日期时刻不存在，问题码为 time，
    field 指出哪一个时间；to_utc 抛 ValueError。
    """
    assert _invalid(_input(starts_at=value)) == [("time", "starts_at")]
    with pytest.raises(ValueError):
        to_utc(value)


# ---- 规则参数（第 3 条） ----


@pytest.mark.parametrize("percent", [1, 90])
def test_item_percent_bounds_pass(percent: int) -> None:
    """第 3 条商品级打折「方式为百分比（1–90 的整数…）」：1 与 90 合格。"""
    assert _params(ITEM_RULE, percent=percent) == []


@pytest.mark.parametrize("percent", [0, 91, None, True])
def test_item_percent_out_of_range_is_rejected(percent: object) -> None:
    """第 3 条商品级打折「百分比（1–90 的整数…）」：0、91、缺失与
    非整数问题码为 rule。
    """
    assert _params(ITEM_RULE, percent=percent) == [("rule", "percent")]


def test_item_amount_bounds() -> None:
    """第 3 条商品级打折「或每件直降（1–50,000,000 仙…）」：1 与
    50,000,000 合格，0 与 50,000,001 问题码为 rule。
    """
    rule = dataclasses.replace(ITEM_RULE, discount_mode=MODE_AMOUNT, percent=None)

    assert _params(rule, amount_sen=1) == []
    assert _params(rule, amount_sen=50_000_000) == []
    assert _params(rule, amount_sen=0) == [("rule", "amount_sen")]
    assert _params(rule, amount_sen=50_000_001) == [("rule", "amount_sen")]


def test_item_discount_mode_must_match_parameter() -> None:
    """第 3 条商品级打折「方式为百分比…或每件直降」：方式未知问题码为
    rule；百分比方式另带直降额、直降方式另带百分比也是 rule。
    """
    assert _params(ITEM_RULE, discount_mode=None) == [("rule", "discount_mode")]
    assert _params(ITEM_RULE, amount_sen=100) == [("rule", "amount_sen")]
    changed = _params(ITEM_RULE, discount_mode=MODE_AMOUNT, amount_sen=100)
    assert changed == [("rule", "percent")]


@pytest.mark.parametrize("field", ["buy_quantity", "get_quantity"])
def test_buy_and_get_quantity_bounds(field: str) -> None:
    """第 3 条买 X 享 Y「X 为 1–10、Y 为 1–10 的整数」：1 与 10 合格，
    0 与 11 问题码为 rule。
    """
    assert _params(BXGY_RULE, **{field: 1}) == []
    assert _params(BXGY_RULE, **{field: 10}) == []
    assert _params(BXGY_RULE, **{field: 0}) == [("rule", field)]
    assert _params(BXGY_RULE, **{field: 11}) == [("rule", field)]


def test_get_percent_bounds() -> None:
    """第 3 条买 X 享 Y「享受的折扣为 1–100 的整数百分比（100 即送…）」：
    1 与 100 合格，0 与 101 问题码为 rule。
    """
    assert _params(BXGY_RULE, get_percent=1) == []
    assert _params(BXGY_RULE, get_percent=100) == []
    assert _params(BXGY_RULE, get_percent=0) == [("rule", "get_percent")]
    assert _params(BXGY_RULE, get_percent=101) == [("rule", "get_percent")]


def test_free_shipping_threshold_bounds() -> None:
    """第 3 条满额免运费「一个门槛（0–99,999,999 仙，0 即本活动期间全部
    订单免运费）」：0 与 99,999,999 合格，-1、100,000,000 与缺失问题码
    为 rule。
    """
    assert _params(FREE_RULE, threshold_sen=0) == []
    assert _params(FREE_RULE, threshold_sen=99_999_999) == []
    for threshold in (-1, 100_000_000, None):
        assert _params(FREE_RULE, threshold_sen=threshold) == [("rule", "threshold_sen")]


@pytest.mark.parametrize(
    ("rule", "changes", "field"),
    [
        pytest.param(BXGY_RULE, {"discount_mode": MODE_PERCENT}, "discount_mode", id="bxgy"),
        pytest.param(FREE_RULE, {"percent": 10}, "percent", id="free-percent"),
        pytest.param(SPEND_RULE, {"threshold_sen": 5000}, "threshold_sen", id="spend"),
        pytest.param(ITEM_RULE, {"get_percent": 50}, "get_percent", id="item"),
        pytest.param(ITEM_RULE, {"tiers": SPEND_RULE.tiers}, "tiers", id="item-tiers"),
        pytest.param(FREE_RULE, {"tiers": SPEND_RULE.tiers}, "tiers", id="free-tiers"),
    ],
)
def test_other_kind_params_must_be_empty(rule: RuleInput, changes: dict, field: str) -> None:
    """第 3 条「每条规则属于一类，参数如下」与落实写法「不属于该类别的
    参数与满额档须为空，否则问题码 rule」。
    """
    assert _params(rule, **changes) == [("rule", field)]


def test_unknown_kind_is_rejected() -> None:
    """第 3 条「每条规则属于一类」（四类之一）：类别未知问题码为 rule。"""
    assert _params(FREE_RULE, kind="bundle") == [("rule", "kind")]


def test_rule_params_ignore_missing_scope() -> None:
    """落实写法「check_rule_params 只查类别与参数…不查范围」：商品级
    规则没有选任何商品或分类时，参数检查不报错（范围由 check_rule_scope
    报出）。
    """
    for rule in (ITEM_RULE, BXGY_RULE):
        bare = dataclasses.replace(rule, product_ids=(), category_ids=())

        assert check_rule_params(bare) == []
        assert _codes(check_rule_scope(bare)) == [("scope", "product_ids")]


# ---- 满额档（第 3 条） ----


def test_valid_tiers_pass() -> None:
    """第 3 条满额减或满额折「每档减固定金额…或打百分比（1–90 的整数）」。"""
    amount_tiers = (TierInput(5000, 500, None), TierInput(10000, 1200, None))
    percent_tiers = (TierInput(5000, None, 1), TierInput(10000, None, 90))

    assert _spend(*amount_tiers) == []
    assert _spend(*percent_tiers, mode=MODE_PERCENT) == []


def test_tier_count_is_one_to_five() -> None:
    """第 3 条满额减或满额折「1–5 档」：0 档与 6 档问题码为 tiers，
    5 档合格。
    """
    tiers = [TierInput(1000 * n, 100 * n, None) for n in range(1, 7)]

    assert _spend() == [("tiers", "tiers")]
    assert _spend(*tiers[:5]) == []
    assert _spend(*tiers) == [("tiers", "tiers")]


def test_fixed_amount_must_be_below_threshold() -> None:
    """第 3 条「每档减固定金额（1 仙到小于门槛）」：等于门槛与 0 问题码
    为 tiers，少一仙合格。
    """
    assert _spend(TierInput(5000, 4999, None)) == []
    assert _spend(TierInput(5000, 5000, None)) == [("tiers", "tiers.1.amount_sen")]
    assert _spend(TierInput(5000, 0, None)) == [("tiers", "tiers.1.amount_sen")]


@pytest.mark.parametrize(
    ("tier", "problems"),
    [
        (TierInput(99_999_999, None, 90), []),
        (TierInput(0, None, 10), [("tiers", "tiers.1.threshold_sen")]),
        (TierInput(100_000_000, None, 10), [("tiers", "tiers.1.threshold_sen")]),
        (TierInput(5000, None, 91), [("tiers", "tiers.1.percent")]),
        (TierInput(5000, None, 0), [("tiers", "tiers.1.percent")]),
    ],
)
def test_tier_threshold_and_percent_bounds(tier: TierInput, problems: list) -> None:
    """第 3 条「每档一个门槛（1–99,999,999 仙）…或打百分比（1–90 的
    整数）」。
    """
    assert _spend(tier, mode=MODE_PERCENT) == problems


def test_thresholds_must_strictly_increase() -> None:
    """第 3 条「门槛与优惠都须逐档严格递增」：门槛相等问题码为 tiers。"""
    tiers = (TierInput(5000, 500, None), TierInput(5000, 600, None))

    assert _spend(*tiers) == [("tiers", "tiers.2.threshold_sen")]


@pytest.mark.parametrize(
    ("mode", "tiers", "field"),
    [
        (MODE_AMOUNT, (TierInput(5000, 500, None), TierInput(10000, 500, None)), "amount_sen"),
        (MODE_PERCENT, (TierInput(5000, None, 15), TierInput(10000, None, 10)), "percent"),
    ],
)
def test_discounts_must_strictly_increase(mode: str, tiers: tuple, field: str) -> None:
    """第 3 条「门槛与优惠都须逐档严格递增」：优惠相等或下降问题码为
    tiers。
    """
    assert _spend(*tiers, mode=mode) == [("tiers", f"tiers.2.{field}")]


def test_all_tiers_use_the_rule_mode() -> None:
    """第 3 条「全部档同一种方式」：固定金额方式下出现百分比的档，问题码
    为 tiers；方式未知问题码为 rule。
    """
    mixed = (TierInput(5000, 500, None), TierInput(10000, None, 10))

    assert _spend(*mixed) == [
        ("tiers", "tiers.2.percent"),
        ("tiers", "tiers.2.amount_sen"),
    ]
    assert _params(SPEND_RULE, discount_mode=None) == [("rule", "discount_mode")]


# ---- 适用范围（第 3 条） ----


def test_scope_limits_pass() -> None:
    """第 3 条「各至多 200 件、30 个，至少选一项」：200 件商品、30 个
    分类，或只选分类，都合格。
    """
    assert _scope(ITEM_RULE, product_ids=tuple(range(1, 201))) == []
    assert _scope(ITEM_RULE, product_ids=(), category_ids=tuple(range(1, 31))) == []


def test_scope_over_limits_is_rejected() -> None:
    """第 3 条「各至多 200 件、30 个」：201 件商品与 31 个分类问题码为
    scope。
    """
    products = _scope(ITEM_RULE, product_ids=tuple(range(1, 202)))
    categories = _scope(BXGY_RULE, category_ids=tuple(range(1, 32)))

    assert products == [("scope", "product_ids")]
    assert categories == [("scope", "category_ids")]


def test_scope_needs_at_least_one_item() -> None:
    """第 3 条「至少选一项」：商品与分类都空问题码为 scope。"""
    assert _scope(BXGY_RULE, category_ids=()) == [("scope", "product_ids")]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"product_ids": (1, 1)}, "product_ids"),
        ({"category_ids": (2, 2)}, "category_ids"),
        ({"product_ids": (0,)}, "product_ids"),
        ({"product_ids": (True,)}, "product_ids"),
    ],
)
def test_scope_ids_are_distinct_positive_integers(changes: dict, field: str) -> None:
    """落实写法「同一规则里不重复」：重复的或不是正整数的 ID 问题码为
    scope。
    """
    assert _scope(ITEM_RULE, **changes) == [("scope", field)]


@pytest.mark.parametrize(
    ("rule", "changes", "field"),
    [
        (SPEND_RULE, {"product_ids": (1,)}, "product_ids"),
        (FREE_RULE, {"category_ids": (1,)}, "category_ids"),
    ],
)
def test_order_rules_have_no_scope(rule: RuleInput, changes: dict, field: str) -> None:
    """第 3 条满额减或满额折与满额免运费为「整单级」，范围只属于商品级
    规则：整单级规则带范围问题码为 scope。
    """
    assert _scope(rule) == []
    assert _scope(rule, **changes) == [("scope", field)]


# ---- 规则条数（第 3 条） ----


def test_rule_count_limits() -> None:
    """第 3 条「一个活动最多 20 条商品级规则、至多一条满额减或满额折、
    至多一条满额免运费」：恰好上限合格，多一条问题码为 rule。
    """
    items = tuple(dataclasses.replace(ITEM_RULE, product_ids=(n,)) for n in range(1, 22))
    full = _input(rules=(*items[:20], SPEND_RULE, FREE_RULE))

    assert check_promotion_input(full) == full
    assert _invalid(_input(rules=items)) == [("rule", "rules")]
    assert _invalid(_input(rules=(SPEND_RULE, SPEND_RULE))) == [("rule", "rules")]
    assert _invalid(_input(rules=(FREE_RULE, FREE_RULE))) == [("rule", "rules")]


# ---- 装饰（第 12 条） ----


def _listed_ids() -> list[tuple[str, str]]:
    """promotions.json 里每个装饰字段可取的（字段、值），另加两种海报模式。"""
    packs = [pack["id"] for pack in TOKENS["packs"]]
    pairs = [("pack", pack) for pack in packs]
    pairs += [("palette", palette) for palette in [TOKENS["keepThemePalette"], *packs]]
    for slot in TOKENS["slots"]:
        pairs += [(slot["id"], variant) for variant in slot["variants"]]
        if "none" in slot:
            pairs.append((slot["id"], slot["none"]))
    return pairs + [("poster_mode", "template"), ("poster_mode", "image")]


@pytest.mark.parametrize(("field", "value"), _listed_ids())
def test_listed_decoration_id_passes(field: str, value: str) -> None:
    """第 12 条「装饰为一组从预置清单里选的 ID」与「海报有两种模式」：
    docs/design/tokens/promotions.json 清单里的每个 ID 都合格。
    """
    assert check_decoration(dataclasses.replace(DECORATION, **{field: value})) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        *[(f.name, "festive") for f in dataclasses.fields(DecorationInput)],
        ("pack", "theme"),
        ("pack", "none"),
        ("palette", "none"),
        ("home_poster", "none"),
        ("card_badge", "garland"),
        ("poster_mode", "video"),
        ("pack", None),
    ],
)
def test_unlisted_decoration_id_is_rejected(field: str, value: object) -> None:
    """第 12 条「保存时任一 ID 不在清单里即回答『取值不合格』」：清单外
    的 ID、theme 与 none 用在不收它们的位置、别的装饰位的变体，问题码为
    decoration。
    """
    decoration = dataclasses.replace(DECORATION, **{field: value})

    assert _codes(check_decoration(decoration)) == [("decoration", field)]
    assert _invalid(_input(decoration=decoration)) == [("decoration", f"decoration.{field}")]


# ---- 文案（第 12 条） ----

LANGS = ("en", "zh", "ms")
COPY_LIMITS = {"title": 40, "subtitle": 80, "badge": 12, "strip": 60, "poster_alt": 150}
COPY_FIELDS = [(f"{name}_{lang}", limit) for name, limit in COPY_LIMITS.items() for lang in LANGS]


@pytest.mark.parametrize(("field", "limit"), COPY_FIELDS)
def test_copy_length_limits(field: str, limit: int) -> None:
    """第 12 条「标题（1–40 个字符）、副标题（0–80）、角标短语（0–12）、
    横条文字（0–60）」与「三语替代文字，各 1–150 个字符」：恰好上限
    （去掉首尾空白后）与空串合格，多一个字符问题码为 copy。英文标题与
    替代文字必填是发布条件，不在这里查。
    """
    padded = "  " + "a" * limit + " "

    assert check_copy(CopyInput(**{field: "a" * limit})) == []
    assert check_copy(CopyInput(**{field: padded})) == []
    assert check_copy(CopyInput(**{field: ""})) == []
    assert _codes(check_copy(CopyInput(**{field: "a" * (limit + 1)}))) == [("copy", field)]


def test_title_of_41_characters_is_rejected() -> None:
    """第 12 条「标题（1–40 个字符）」：41 个字符的标题整次不合格。"""
    data = _input(copy=CopyInput(title_en="a" * 41))

    assert _invalid(data) == [("copy", "copy.title_en")]


@pytest.mark.parametrize(
    "char",
    [
        pytest.param("\n", id="lf"),
        pytest.param("\r", id="cr"),
        pytest.param("\t", id="tab"),
        pytest.param("\x00", id="nul"),
        pytest.param("\x1c", id="file-separator"),
        pytest.param("\x7f", id="del"),
        pytest.param("\x85", id="nel"),
        pytest.param(" ", id="line-separator"),
        pytest.param(" ", id="paragraph-separator"),
    ],
)
def test_copy_with_newline_or_control_character_is_rejected(char: str) -> None:
    """第 12 条「不允许换行与控制字符」：Cc 类字符与 U+2028、U+2029
    问题码为 copy，在文字中间或首尾都不收（不因去掉首尾空白而放过）。
    """
    for text in (f"Big{char}Sale", f"Big Sale{char}", f"{char}Big Sale"):
        assert _codes(check_copy(CopyInput(subtitle_zh=text))) == [("copy", "subtitle_zh")]


def test_copy_allows_emoji_with_zero_width_joiner() -> None:
    """落实写法「零宽连接符等 Cf 类允许，emoji 要用」：带零宽连接符的
    emoji 合格，字符数按码位计。
    """
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    copy = CopyInput(title_zh="新年快乐 " + family, badge_ms=family + "a" * 7)

    assert check_copy(copy) == []
    assert _codes(check_copy(CopyInput(badge_ms=family + "a" * 8))) == [("copy", "badge_ms")]


# ---- 时间换算（第 1 条） ----


def test_to_utc_subtracts_eight_hours_across_days() -> None:
    """第 1 条「后台按马来西亚时间输入…存为不带时区的 UTC」：马来西亚
    1 月 1 日 03:00 为 UTC 前一天 19:00，不带时区。
    """
    assert to_utc("2027-01-01T03:00") == datetime(2026, 12, 31, 19, 0)
    assert to_utc("2027-01-01T08:00") == datetime(2027, 1, 1, 0, 0)
    assert to_utc("2027-01-01T03:00").tzinfo is None
    assert to_utc(None) is None


def test_to_local_adds_eight_hours_and_truncates_seconds() -> None:
    """第 1 条「精确到分钟」与落实写法「反向换算时秒数截去」：UTC
    12 月 31 日 19:00:59.999999 为马来西亚 1 月 1 日 03:00。
    """
    assert to_local(datetime(2026, 12, 31, 19, 0, 59, 999_999)) == "2027-01-01T03:00"
    assert to_local(datetime(2026, 12, 31, 16, 0)) == "2027-01-01T00:00"
    assert to_local(to_utc("2027-02-10T23:59")) == "2027-02-10T23:59"
    assert to_local(None) is None


def test_time_conversion_rejects_bad_values() -> None:
    """第 1 条「存为不带时区的 UTC」：带时区的时间、换算越界与不是字符串
    的本地时间抛 ValueError。
    """
    with pytest.raises(ValueError):
        to_local(datetime(2027, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        to_utc("0001-01-01T07:59")
    with pytest.raises(ValueError):
        to_utc(202701010000)


# ---- 活动状态（第 2 条） ----


def test_draft_state_ignores_time() -> None:
    """第 2 条「活动只有『草稿』与『已发布』两种状态」：草稿不论时间
    都是 draft。
    """
    assert promotion_state(STATUS_DRAFT, None, None, STARTS) == "draft"
    assert promotion_state(STATUS_DRAFT, STARTS, ENDS, STARTS + SECOND) == "draft"


@pytest.mark.parametrize(
    ("now", "state"),
    [
        (STARTS - SECOND, "scheduled"),
        (STARTS, "running"),
        (ENDS - SECOND, "running"),
        (ENDS, "ended"),
        (ENDS + timedelta(days=1), "ended"),
    ],
)
def test_published_state_follows_time(now: datetime, state: str) -> None:
    """第 1 条「开始时间 ≤ 当前时刻、当前时刻 < 实际结束时间」时生效与
    第 2 条「未开始、进行中、已结束由时间算出」：开始时刻为 running，
    实际结束时刻为 ended。
    """
    assert promotion_state(STATUS_PUBLISHED, STARTS, ENDS, now) == state


def test_early_end_is_the_effective_end() -> None:
    """第 1 条「实际结束时间（提前结束时为提前结束的时刻，否则为结束
    时间）」：以提前结束的时刻为实际结束，该时刻即为 ended。
    """
    ended_early = STARTS + timedelta(days=3)
    before = promotion_state(STATUS_PUBLISHED, STARTS, ended_early, ended_early - SECOND)
    at = promotion_state(STATUS_PUBLISHED, STARTS, ended_early, ended_early)

    assert before == "running"
    assert at == "ended"


def test_state_rejects_inconsistent_values() -> None:
    """第 2 条发布时开始与结束都已填，第 1 条时间为不带时区的 UTC：
    已发布却缺时间、状态未知、带时区的当前时刻抛 ValueError。
    """
    aware = datetime(2027, 1, 21, tzinfo=UTC)

    with pytest.raises(ValueError):
        promotion_state(STATUS_PUBLISHED, None, ENDS, STARTS)
    with pytest.raises(ValueError):
        promotion_state("archived", STARTS, ENDS, STARTS)
    with pytest.raises(ValueError):
        promotion_state(STATUS_PUBLISHED, STARTS, ENDS, aware)

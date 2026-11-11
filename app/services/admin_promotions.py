"""后台促销活动（A12）：输入数据类、不碰数据库的取值检查、时间换算与活动状态。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条
（下称「第 N 条」）与 docs/UX.md 0.20 的 A12，以及 SHOP-TASK-239 的落实写法。
本模块的这一部分不读写数据库：查库的检查（商品级规则重叠、发布条件、
已开始与已结束的锁定）、列表、读取、新建与保存见 SHOP-TASK-249，AI 提案的
校验（SHOP-TASK-248）复用这里的 check_decoration 与 check_copy。

时间：后台按马来西亚时间输入到分钟（第 1 条）。服务层收发的时间是马来西亚
本地时间的「YYYY-MM-DDTHH:MM」字符串或 None；to_utc 换成不带时区的 UTC
（减 8 小时），to_local 反向换算并截去秒数。

检查：check_rule_params、check_rule_scope、check_decoration、check_copy 各自
返回问题列表（不抛异常），field 相对于各自的输入；check_promotion_input 依次
组合它们，给 field 加上 rules.<序号>.、decoration.、copy. 前缀，并另查内部名称、
时间与规则条数，有问题即抛 PromotionInvalid。field 用点分隔，序号从 1 起，
如 rules.2.tiers.3.amount_sen。规则的排序即 rules 里的顺序（从 1 起）。
数据类的结构（容器与元素的类型、两个开关是否为 bool）不对是调用方的错误，
抛 TypeError；只有取值不合格才记为问题。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta

from app.models.promotion import (
    DECORATION_CHOICES,
    DISCOUNT_PERCENT_MAX,
    GET_PERCENT_MAX,
    ITEM_AMOUNT_MAX_SEN,
    KIND_BUY_X_GET_Y,
    KIND_FREE_SHIPPING,
    KIND_ITEM_DISCOUNT,
    KIND_SPEND_DISCOUNT,
    MODE_AMOUNT,
    MODE_PERCENT,
    NAME_MAX_LENGTH,
    PERCENT_MIN,
    POSTER_MODES,
    QUANTITY_MAX,
    QUANTITY_MIN,
    RULE_KINDS,
    SPEND_TIERS_MAX,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    THRESHOLD_MAX_SEN,
)
from app.services.sms_budget import MALAYSIA_UTC_OFFSET

# 问题码。scope_overlap、publish_missing、rules_locked、ended_readonly 由
# SHOP-TASK-249 查库时再加。
PROBLEM_NAME = "name"
PROBLEM_TIME = "time"
PROBLEM_DURATION = "duration"
PROBLEM_RULE = "rule"
PROBLEM_SCOPE = "scope"
PROBLEM_TIERS = "tiers"
PROBLEM_DECORATION = "decoration"
PROBLEM_COPY = "copy"

# 由时间算出的活动状态（第 2 条）。
STATE_DRAFT = "draft"
STATE_SCHEDULED = "scheduled"
STATE_RUNNING = "running"
STATE_ENDED = "ended"
PROMOTION_STATES = (STATE_DRAFT, STATE_SCHEDULED, STATE_RUNNING, STATE_ENDED)

# 第 1 条「时长不超过 92 天」；第 3 条「最多 20 条商品级规则」与范围
# 「各至多 200 件、30 个」。
MAX_DURATION = timedelta(days=92)
ITEM_RULES_MAX = 20
SCOPE_PRODUCTS_MAX = 200
SCOPE_CATEGORIES_MAX = 30
ITEM_KINDS = (KIND_ITEM_DISCOUNT, KIND_BUY_X_GET_Y)
ORDER_KINDS = (KIND_SPEND_DISCOUNT, KIND_FREE_SHIPPING)

# 第 12 条文案的字符数上限，按 CopyInput 字段名去掉语言后缀取值。
COPY_MAX_LENGTHS = {
    "title": 40,
    "subtitle": 80,
    "badge": 12,
    "strip": 60,
    "poster_alt": 150,
}

LOCAL_TIME_FORMAT = "%Y-%m-%dT%H:%M"
# [0-9] 不含其他文字的数字；strptime 本身会接受不补零的月、日与时分。
_LOCAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")
# 行分隔符与段分隔符不属于 Cc，另列；零宽连接符等 Cf 类允许（emoji 要用）。
_LINE_SEPARATORS = frozenset("  ")

# 每类规则用到的参数（第 3 条）；不在这里的参数须为空。
_RULE_PARAMS = (
    "discount_mode",
    "percent",
    "amount_sen",
    "buy_quantity",
    "get_quantity",
    "get_percent",
    "threshold_sen",
)
_KIND_PARAMS = {
    KIND_ITEM_DISCOUNT: ("discount_mode", "percent", "amount_sen"),
    KIND_BUY_X_GET_Y: ("buy_quantity", "get_quantity", "get_percent"),
    KIND_SPEND_DISCOUNT: ("discount_mode",),
    KIND_FREE_SHIPPING: ("threshold_sen",),
}


# ---------------------------------------------------------------------------
# 输入数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierInput:
    """满额减或满额折的一档：门槛与一个优惠（固定金额或百分比，二选一）。"""

    threshold_sen: int
    amount_sen: int | None
    percent: int | None


@dataclass(frozen=True)
class RuleInput:
    """一条规则（第 3 条）：类别、参数、商品级规则的范围与满额档。

    不属于该类别的参数为 None，范围与满额档为空。
    """

    kind: str
    discount_mode: str | None = None
    percent: int | None = None
    amount_sen: int | None = None
    buy_quantity: int | None = None
    get_quantity: int | None = None
    get_percent: int | None = None
    threshold_sen: int | None = None
    product_ids: tuple[int, ...] = ()
    category_ids: tuple[int, ...] = ()
    tiers: tuple[TierInput, ...] = ()


@dataclass(frozen=True)
class DecorationInput:
    """装饰（第 12 条）：素材包、配色、六个装饰位的变体与海报模式。"""

    pack: str
    palette: str
    ambient: str
    home_poster: str
    list_strip: str
    card_badge: str
    detail_ribbon: str
    cart_progress: str
    poster_mode: str


@dataclass(frozen=True)
class CopyInput:
    """三语文案与海报替代文字（第 12 条），各项为 None 或字符串。"""

    title_en: str | None = None
    title_zh: str | None = None
    title_ms: str | None = None
    subtitle_en: str | None = None
    subtitle_zh: str | None = None
    subtitle_ms: str | None = None
    badge_en: str | None = None
    badge_zh: str | None = None
    badge_ms: str | None = None
    strip_en: str | None = None
    strip_zh: str | None = None
    strip_ms: str | None = None
    poster_alt_en: str | None = None
    poster_alt_zh: str | None = None
    poster_alt_ms: str | None = None


@dataclass(frozen=True)
class PromotionInput:
    """后台新建与保存活动时提交的内容（第 1 条）。

    starts_at 与 ends_at 为马来西亚本地时间的「YYYY-MM-DDTHH:MM」或 None
    （草稿可以不填）；rules 的顺序即规则的排序（从 1 起）。
    """

    name: str
    starts_at: str | None
    ends_at: str | None
    coupon_combinable: bool
    points_combinable: bool
    rules: tuple[RuleInput, ...]
    decoration: DecorationInput
    copy: CopyInput


# ---------------------------------------------------------------------------
# 问题与异常
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionProblem:
    """一个取值问题：问题码与出错的字段。

    product_id 与 product_name（英文名称，供页面显示）只在 scope_overlap 时有值。
    """

    code: str
    field: str
    product_id: int | None = None
    product_name: str | None = None


class PromotionInvalid(Exception):
    """提交的活动取值不合格（第 1–3、12 条「取值不合格」），problems 逐条列出。"""

    def __init__(self, problems: Sequence[PromotionProblem]) -> None:
        super().__init__(problems)
        self.problems = tuple(problems)


# ---------------------------------------------------------------------------
# 时间与状态
# ---------------------------------------------------------------------------


def to_utc(local: str | None) -> datetime | None:
    """马来西亚本地时间「YYYY-MM-DDTHH:MM」→ 不带时区的 UTC；None 原样返回。

    格式不对（含不存在的日期与时刻）抛 ValueError。
    """
    if local is None:
        return None
    if not isinstance(local, str) or _LOCAL_TIME.fullmatch(local) is None:
        raise ValueError("local time must be YYYY-MM-DDTHH:MM")
    try:
        return datetime.strptime(local, LOCAL_TIME_FORMAT) - MALAYSIA_UTC_OFFSET
    except OverflowError as exc:
        raise ValueError("local time out of range") from exc


def to_local(utc: datetime | None) -> str | None:
    """不带时区的 UTC → 马来西亚本地时间「YYYY-MM-DDTHH:MM」，秒数截去；None 原样返回。

    带时区或不是 datetime 抛 ValueError。
    """
    if utc is None:
        return None
    if not isinstance(utc, datetime) or utc.tzinfo is not None:
        raise ValueError("utc must be a naive UTC datetime")
    try:
        local = utc + MALAYSIA_UTC_OFFSET
    except OverflowError as exc:
        raise ValueError("utc time out of range") from exc
    # 不用 strftime：年份小于 1000 时各平台补零的写法不同；isoformat 总补到 4 位。
    return local.isoformat(timespec="minutes")


def _require_naive(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError(f"{name} must be a naive UTC datetime")


def promotion_state(
    status: str,
    starts_at: datetime | None,
    effective_end: datetime | None,
    now: datetime,
) -> str:
    """第 2 条「未开始、进行中、已结束由时间算出」。

    草稿为 draft；已发布而 now 早于开始为 scheduled，在开始（含）与实际结束
    （不含）之间为 running，不早于实际结束为 ended。effective_end 为提前结束的
    时刻（有时）或结束时间。时间都是不带时区的 UTC；已发布却缺时间、状态未知
    或时间带时区抛 ValueError。
    """
    _require_naive(now, "now")
    if status == STATUS_DRAFT:
        return STATE_DRAFT
    if status != STATUS_PUBLISHED:
        raise ValueError("unknown promotion status")
    if starts_at is None or effective_end is None:
        raise ValueError("published promotion must have times")
    _require_naive(starts_at, "starts_at")
    _require_naive(effective_end, "effective_end")
    if now < starts_at:
        return STATE_SCHEDULED
    if now < effective_end:
        return STATE_RUNNING
    return STATE_ENDED


# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------


def has_forbidden_char(text: str) -> bool:
    """第 12 条「不允许换行与控制字符」：Unicode 类别 Cc，以及 U+2028、U+2029。"""
    return any(ch in _LINE_SEPARATORS or unicodedata.category(ch) == "Cc" for ch in text)


def clean_text(value: str | None) -> str | None:
    """去掉首尾空白，空串为 None；None 原样返回。"""
    if value is None:
        return None
    text = value.strip()
    return text or None


def clean_copy(copy: CopyInput) -> CopyInput:
    """文案各项照 clean_text 处理，供保存时「空串存为空」。"""
    return replace(copy, **{f.name: clean_text(getattr(copy, f.name)) for f in fields(CopyInput)})


def _text_ok(value: object, minimum: int, maximum: int) -> bool:
    # 换行与控制字符查原文：str.strip 也会去掉 \x1c–\x1f 与 \x85 这些控制字符，
    # 先去空白再查就会悄悄收下它们。
    if not isinstance(value, str) or has_forbidden_char(value):
        return False
    return minimum <= len(value.strip()) <= maximum


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------


def _int_in(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _is_sequence(value: object) -> bool:
    return isinstance(value, (tuple, list))


def _tier_problems(mode: object, tiers: object) -> list[PromotionProblem]:
    """满额减或满额折的各档：1–5 档，每档门槛与优惠合格，方式与规则一致，
    门槛与优惠逐档严格递增，固定金额小于门槛（第 3 条）。"""
    problems: list[PromotionProblem] = []
    if not _is_sequence(tiers):
        return [PromotionProblem(PROBLEM_TIERS, "tiers")]
    if not 1 <= len(tiers) <= SPEND_TIERS_MAX:
        problems.append(PromotionProblem(PROBLEM_TIERS, "tiers"))
    if mode == MODE_PERCENT:
        discount_field, other_field = "percent", "amount_sen"
    elif mode == MODE_AMOUNT:
        discount_field, other_field = "amount_sen", "percent"
    else:
        # 方式不合格已由规则报出，优惠无从对照，只查门槛。
        discount_field = other_field = None

    thresholds: list[int | None] = []
    discounts: list[int | None] = []
    for number, tier in enumerate(tiers, start=1):
        if not isinstance(tier, TierInput):
            raise TypeError("tiers must contain TierInput")
        prefix = f"tiers.{number}."
        threshold = tier.threshold_sen
        if not _int_in(threshold, 1, THRESHOLD_MAX_SEN):
            problems.append(PromotionProblem(PROBLEM_TIERS, prefix + "threshold_sen"))
            threshold = None
        thresholds.append(threshold)
        if discount_field is None:
            discounts.append(None)
            continue
        if getattr(tier, other_field) is not None:
            problems.append(PromotionProblem(PROBLEM_TIERS, prefix + other_field))
        discount = getattr(tier, discount_field)
        if discount_field == "percent":
            valid = _int_in(discount, PERCENT_MIN, DISCOUNT_PERCENT_MAX)
        else:
            # 固定金额小于门槛；门槛不合格时只查不超过门槛的上限。
            below = THRESHOLD_MAX_SEN if threshold is None else threshold
            valid = _int_in(discount, 1, below - 1)
        if not valid:
            problems.append(PromotionProblem(PROBLEM_TIERS, prefix + discount_field))
        discounts.append(discount if valid else None)

    for values, name in ((thresholds, "threshold_sen"), (discounts, discount_field)):
        for number in range(2, len(values) + 1):
            previous, current = values[number - 2], values[number - 1]
            if previous is not None and current is not None and current <= previous:
                problems.append(PromotionProblem(PROBLEM_TIERS, f"tiers.{number}.{name}"))
    return problems


def check_rule_params(rule: RuleInput) -> list[PromotionProblem]:
    """第 3 条：类别与参数的取值，不查范围（范围见 check_rule_scope）。

    类别未知、参数超出范围、不属于该类别的参数或满额档不为空，问题码为 rule；
    满额档本身的问题码为 tiers。field 相对于这条规则。
    """
    if rule.kind not in RULE_KINDS:
        return [PromotionProblem(PROBLEM_RULE, "kind")]
    problems = [
        PromotionProblem(PROBLEM_RULE, name)
        for name in _RULE_PARAMS
        if name not in _KIND_PARAMS[rule.kind] and getattr(rule, name) is not None
    ]
    if rule.kind != KIND_SPEND_DISCOUNT and rule.tiers:
        problems.append(PromotionProblem(PROBLEM_RULE, "tiers"))

    def require(name: str, minimum: int, maximum: int) -> None:
        if not _int_in(getattr(rule, name), minimum, maximum):
            problems.append(PromotionProblem(PROBLEM_RULE, name))

    def forbid(name: str) -> None:
        if getattr(rule, name) is not None:
            problems.append(PromotionProblem(PROBLEM_RULE, name))

    if rule.kind == KIND_ITEM_DISCOUNT:
        if rule.discount_mode == MODE_PERCENT:
            require("percent", PERCENT_MIN, DISCOUNT_PERCENT_MAX)
            forbid("amount_sen")
        elif rule.discount_mode == MODE_AMOUNT:
            require("amount_sen", 1, ITEM_AMOUNT_MAX_SEN)
            forbid("percent")
        else:
            problems.append(PromotionProblem(PROBLEM_RULE, "discount_mode"))
    elif rule.kind == KIND_BUY_X_GET_Y:
        require("buy_quantity", QUANTITY_MIN, QUANTITY_MAX)
        require("get_quantity", QUANTITY_MIN, QUANTITY_MAX)
        require("get_percent", PERCENT_MIN, GET_PERCENT_MAX)
    elif rule.kind == KIND_SPEND_DISCOUNT:
        if rule.discount_mode not in (MODE_PERCENT, MODE_AMOUNT):
            problems.append(PromotionProblem(PROBLEM_RULE, "discount_mode"))
        problems.extend(_tier_problems(rule.discount_mode, rule.tiers))
    else:
        require("threshold_sen", 0, THRESHOLD_MAX_SEN)
    return problems


def _ids_ok(ids: object, maximum: int) -> bool:
    if not _is_sequence(ids) or len(ids) > maximum:
        return False
    if not all(type(value) is int and value >= 1 for value in ids):
        return False
    return len(set(ids)) == len(ids)


def check_rule_scope(rule: RuleInput) -> list[PromotionProblem]:
    """第 3 条的适用范围，问题码 scope，field 相对于这条规则。

    商品级规则：商品与分类 ID 为正整数，各至多 200 件、30 个，同一规则里
    不重复，且至少选一项（两项都空时指向 product_ids）；整单级规则两项须为空。
    类别未知时不查（由 check_rule_params 报出）。
    """
    problems: list[PromotionProblem] = []
    products, categories = rule.product_ids, rule.category_ids
    if rule.kind in ITEM_KINDS:
        if not _ids_ok(products, SCOPE_PRODUCTS_MAX):
            problems.append(PromotionProblem(PROBLEM_SCOPE, "product_ids"))
        if not _ids_ok(categories, SCOPE_CATEGORIES_MAX):
            problems.append(PromotionProblem(PROBLEM_SCOPE, "category_ids"))
        if _is_sequence(products) and _is_sequence(categories) and not (products or categories):
            problems.append(PromotionProblem(PROBLEM_SCOPE, "product_ids"))
    elif rule.kind in ORDER_KINDS:
        if products:
            problems.append(PromotionProblem(PROBLEM_SCOPE, "product_ids"))
        if categories:
            problems.append(PromotionProblem(PROBLEM_SCOPE, "category_ids"))
    return problems


def check_decoration(decoration: DecorationInput) -> list[PromotionProblem]:
    """第 12 条「保存时任一 ID 不在清单里即回答取值不合格」，问题码 decoration。

    清单为 app.models.promotion 的常量（与 docs/design/tokens/promotions.json
    逐项相同，由 SHOP-TASK-229 的测试守住）；每个素材包的变体与 slots 相同，
    所以按装饰位单独检查即等于按清单检查。field 为装饰的字段名。
    """
    problems: list[PromotionProblem] = []
    for f in fields(DecorationInput):
        value = getattr(decoration, f.name)
        choices = POSTER_MODES if f.name == "poster_mode" else DECORATION_CHOICES[f.name]
        if type(value) is not str or value not in choices:
            problems.append(PromotionProblem(PROBLEM_DECORATION, f.name))
    return problems


def check_copy(copy: CopyInput) -> list[PromotionProblem]:
    """第 12 条文案：去掉首尾空白后标题 0–40、副标题 0–80、角标短语 0–12、
    横条文字 0–60、替代文字 0–150 个字符，不含换行与控制字符，问题码 copy。

    None 与空串都可（英文标题与替代文字必填是发布条件，见 SHOP-TASK-249）。
    field 为文案的字段名。
    """
    problems: list[PromotionProblem] = []
    for f in fields(CopyInput):
        value = getattr(copy, f.name)
        if value is None:
            continue
        maximum = COPY_MAX_LENGTHS[f.name.rsplit("_", 1)[0]]
        if not _text_ok(value, 0, maximum):
            problems.append(PromotionProblem(PROBLEM_COPY, f.name))
    return problems


def _time_problems(starts_at: object, ends_at: object) -> list[PromotionProblem]:
    problems: list[PromotionProblem] = []
    parsed: dict[str, datetime] = {}
    for name, value in (("starts_at", starts_at), ("ends_at", ends_at)):
        if value is None:
            continue
        try:
            parsed[name] = to_utc(value)
        except ValueError:
            problems.append(PromotionProblem(PROBLEM_TIME, name))
    if len(parsed) == 2:
        length = parsed["ends_at"] - parsed["starts_at"]
        if length <= timedelta(0):
            problems.append(PromotionProblem(PROBLEM_TIME, "ends_at"))
        elif length > MAX_DURATION:
            problems.append(PromotionProblem(PROBLEM_DURATION, "ends_at"))
    return problems


def _rule_count_problems(rules: Sequence[RuleInput]) -> list[PromotionProblem]:
    """第 3 条「一个活动最多 20 条商品级规则、至多一条满额减或满额折、
    至多一条满额免运费」。"""
    kinds = [rule.kind for rule in rules]
    problems: list[PromotionProblem] = []
    if sum(kind in ITEM_KINDS for kind in kinds) > ITEM_RULES_MAX:
        problems.append(PromotionProblem(PROBLEM_RULE, "rules"))
    for kind in ORDER_KINDS:
        if kinds.count(kind) > 1:
            problems.append(PromotionProblem(PROBLEM_RULE, "rules"))
    return problems


def _prefixed(problems: list[PromotionProblem], prefix: str) -> list[PromotionProblem]:
    return [replace(problem, field=prefix + problem.field) for problem in problems]


def check_promotion_input(data: PromotionInput) -> PromotionInput:
    """依次检查内部名称、时间、每条规则的参数与范围、规则条数、装饰与文案。

    内部名称去掉首尾空白后 1–100 个字符，字符规则同文案；时间为分钟精度的
    本地时间或 None，两者都有时结束晚于开始、时长不超过 92 天。
    有问题即抛 PromotionInvalid（列出全部问题）；没有问题时返回去掉名称
    首尾空白、文案照 clean_copy 处理之后的输入。结构不对抛 TypeError。
    """
    if not isinstance(data, PromotionInput):
        raise TypeError("data must be PromotionInput")
    if type(data.coupon_combinable) is not bool or type(data.points_combinable) is not bool:
        raise TypeError("combinable flags must be bool")
    if not _is_sequence(data.rules) or not all(isinstance(r, RuleInput) for r in data.rules):
        raise TypeError("rules must be a sequence of RuleInput")
    if not isinstance(data.decoration, DecorationInput):
        raise TypeError("decoration must be DecorationInput")
    if not isinstance(data.copy, CopyInput):
        raise TypeError("copy must be CopyInput")

    problems: list[PromotionProblem] = []
    if not _text_ok(data.name, 1, NAME_MAX_LENGTH):
        problems.append(PromotionProblem(PROBLEM_NAME, "name"))
    problems.extend(_time_problems(data.starts_at, data.ends_at))
    for position, rule in enumerate(data.rules, start=1):
        prefix = f"rules.{position}."
        problems.extend(_prefixed(check_rule_params(rule), prefix))
        problems.extend(_prefixed(check_rule_scope(rule), prefix))
    problems.extend(_rule_count_problems(data.rules))
    problems.extend(_prefixed(check_decoration(data.decoration), "decoration."))
    problems.extend(_prefixed(check_copy(data.copy), "copy."))
    if problems:
        raise PromotionInvalid(problems)
    return replace(data, name=data.name.strip(), copy=clean_copy(data.copy))

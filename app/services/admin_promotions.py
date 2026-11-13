"""后台促销活动（A12）：输入数据类、取值检查、时间换算、活动状态，以及列表、读取、
新建、保存与状态操作（发布、撤回、提前结束、删除、复制）。

依据 docs/DESIGN.md 1.23（提交 b7f9355）「节日促销活动」第 1–3、12 条
（下称「第 N 条」）与 docs/UX.md 0.20 的 A12，以及 SHOP-TASK-239、SHOP-TASK-249
与 SHOP-TASK-241 的落实写法。前半部分（SHOP-TASK-239）不读写数据库；中间部分
（SHOP-TASK-249）做查库的检查（引用存在、商品级规则重叠、发布条件、时间重叠、
按状态锁定）、列表、读取、新建与保存；最后部分（SHOP-TASK-241）做状态操作。
AI 提案的校验（SHOP-TASK-248）复用这里的 check_decoration 与 check_copy；发布
复用 publish_problems、scope_overlap_problems 与 ensure_no_overlap。

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

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Category,
    Order,
    Product,
    Promotion,
    PromotionDecoration,
    PromotionPublishLock,
    PromotionRule,
    PromotionRuleCategory,
    PromotionRuleProduct,
    PromotionSpendTier,
)
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
    POSTER_MODE_IMAGE,
    POSTER_MODE_TEMPLATE,
    POSTER_MODES,
    QUANTITY_MAX,
    QUANTITY_MIN,
    RULE_KINDS,
    SINGLETON_SLOT,
    SPEND_TIERS_MAX,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    THRESHOLD_MAX_SEN,
)
from app.services import image_store
from app.services.admin_auth import record_audit
from app.services.catalog import published
from app.services.sms_budget import MALAYSIA_UTC_OFFSET

# 问题码。前八个由不碰数据库的检查报出；后四个由查库的检查报出（SHOP-TASK-249）。
PROBLEM_NAME = "name"
PROBLEM_TIME = "time"
PROBLEM_DURATION = "duration"
PROBLEM_RULE = "rule"
PROBLEM_SCOPE = "scope"
PROBLEM_TIERS = "tiers"
PROBLEM_DECORATION = "decoration"
PROBLEM_COPY = "copy"
PROBLEM_SCOPE_OVERLAP = "scope_overlap"
PROBLEM_PUBLISH_MISSING = "publish_missing"
PROBLEM_RULES_LOCKED = "rules_locked"
PROBLEM_ENDED_READONLY = "ended_readonly"

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


# ===========================================================================
# 查库的检查、列表、读取、新建与保存（SHOP-TASK-249）
#
# 新建与保存照 app/services/admin_categories.py：先不碰数据库检查请求本身
# （check_promotion_input），再回滚此前的事务、加锁核对，任何异常先回滚再抛出；
# 有改动才加版本、写审计并提交，没有改动回滚、不加版本、不写审计。
#
# 保存的加锁顺序（与 SHOP-TASK-241 的发布相同，免得死锁）：promotion_publish_locks
# 那一行为新事务的第一条语句，再锁活动行，再锁装饰行；状态在加锁后读出。保存前
# 不做普通读取，免得 MySQL 的读快照早于加锁。按状态的可改范围照 UX 0.20 A12 的
# 状态表：draft 与 scheduled 全部可改；running 只能改内部名称、装饰与文案，请求
# 里的时间、开关与规则须与当前相同，不同即 rules_locked；ended 保存即
# ended_readonly。scheduled 而已有订单引用（orders.promotion_id，以共享锁读，与
# 下单事务读活动时的共享锁互相等待）的按 running 处理。
# ===========================================================================

# 审计操作名与对象类别（对象 ID 为活动 ID）。
ADMIN_PROMOTION_CREATED = "admin_promotion_created"
ADMIN_PROMOTION_CHANGED = "admin_promotion_changed"
AUDIT_TARGET_PROMOTION = "promotion"
NEW_PROMOTION_VERSION = 1
# 审计里文案的值取前 100 个字符。
AUDIT_TEXT_LENGTH = 100
# 海报图的提供路径：前缀加文件名加 .webp（接口由 SHOP-TASK-250 提供）。
POSTER_PATH_PREFIX = "/api/promotion-posters/"
POSTER_PATH_SUFFIX = ".webp"
# 已结束的活动整体只读，ended_readonly 报在这个 field 上。
STATE_FIELD = "state"

# 列表的状态次序（UX A12「按进行中、未开始、草稿、已结束排列」）。
_STATE_ORDER = {STATE_RUNNING: 0, STATE_SCHEDULED: 1, STATE_DRAFT: 2, STATE_ENDED: 3}


class PromotionNotFound(Exception):
    """读取的活动不存在。"""


class PromotionEnded(Exception):
    """活动已结束，不能再改（海报图的上传与移除由 SHOP-TASK-240 抛出，241 复用）。"""


class PromotionChanged(Exception):
    """活动已被改动（第 2 条「并发」）：保存时活动不存在、版本号与当前不同，
    或替换规则时撞上订单逐件快照的外键。"""


class PromotionOverlap(Exception):
    """与其他已发布活动的「开始到实际结束」重叠（第 1 条），other_name 为对方的内部名称。"""

    def __init__(self, other_name: str) -> None:
        super().__init__(other_name)
        self.other_name = other_name


@dataclass(frozen=True)
class PromotionSummary:
    """列表的一项。starts_at 与 ends_at 为马来西亚本地时间（ends_at 为实际结束），
    草稿未填时为 None；updated_at 为不带时区的 UTC。"""

    id: int
    name: str
    state: str
    starts_at: str | None
    ends_at: str | None
    rule_count: int
    updated_at: datetime


@dataclass(frozen=True)
class ScopeProduct:
    """规则显式选的一件商品：ID、slug 与英文名称（为空时 None）。"""

    id: int
    slug: str
    name_en: str | None


@dataclass(frozen=True)
class RuleDetail:
    """读出的一条规则：RuleInput 的全部字段，另带规则 ID 与所选商品。

    product_ids 与 category_ids 按 ID 从小到大；products 与 product_ids 一一对应。
    """

    rule_id: int
    kind: str
    discount_mode: str | None
    percent: int | None
    amount_sen: int | None
    buy_quantity: int | None
    get_quantity: int | None
    get_percent: int | None
    threshold_sen: int | None
    product_ids: tuple[int, ...]
    category_ids: tuple[int, ...]
    tiers: tuple[TierInput, ...]
    products: tuple[ScopeProduct, ...]

    def as_input(self) -> RuleInput:
        return RuleInput(**{f.name: getattr(self, f.name) for f in fields(RuleInput)})


@dataclass(frozen=True)
class PromotionDetail:
    """一个活动的全部可改内容（PromotionInput 的全部字段）与只读的信息。

    ended_early_at 为提前结束的马来西亚本地时刻或 None；两个海报图路径为
    /api/promotion-posters/<文件名>.webp 或 None。
    """

    id: int
    version: int
    state: str
    ended_early_at: str | None
    name: str
    starts_at: str | None
    ends_at: str | None
    coupon_combinable: bool
    points_combinable: bool
    rules: tuple[RuleDetail, ...]
    decoration: DecorationInput
    copy: CopyInput
    poster_desktop_url: str | None
    poster_phone_url: str | None

    def as_input(self) -> PromotionInput:
        """原样提交即「没有改动」的保存内容。"""
        return PromotionInput(
            name=self.name,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            coupon_combinable=self.coupon_combinable,
            points_combinable=self.points_combinable,
            rules=tuple(rule.as_input() for rule in self.rules),
            decoration=self.decoration,
            copy=self.copy,
        )


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def _effective_end(promotion: Promotion) -> datetime | None:
    return promotion.ended_early_at or promotion.ends_at


def _state(promotion: Promotion, now: datetime) -> str:
    return promotion_state(promotion.status, promotion.starts_at, _effective_end(promotion), now)


def _poster_path(file_name: str | None) -> str | None:
    if file_name is None:
        return None
    return f"{POSTER_PATH_PREFIX}{file_name}{POSTER_PATH_SUFFIX}"


def _load_rules(db: Session, promotion_id: int) -> tuple[RuleDetail, ...]:
    """按排序读出活动的规则、范围与满额档（只查列值，不用会话里的旧对象）。"""
    params = [getattr(PromotionRule, name) for name in _RULE_PARAMS]
    rule_rows = db.execute(
        select(PromotionRule.id, PromotionRule.kind, *params)
        .where(PromotionRule.promotion_id == promotion_id)
        .order_by(PromotionRule.position)
    ).all()
    rule_ids = [row[0] for row in rule_rows]
    products: dict[int, list[ScopeProduct]] = {rule_id: [] for rule_id in rule_ids}
    categories: dict[int, list[int]] = {rule_id: [] for rule_id in rule_ids}
    tiers: dict[int, list[TierInput]] = {rule_id: [] for rule_id in rule_ids}
    if rule_ids:
        product_rows = db.execute(
            select(PromotionRuleProduct.rule_id, Product.id, Product.slug, Product.name_en)
            .join(Product, Product.id == PromotionRuleProduct.product_id)
            .where(PromotionRuleProduct.rule_id.in_(rule_ids))
            .order_by(Product.id)
        )
        for rule_id, product_id, slug, name_en in product_rows:
            products[rule_id].append(ScopeProduct(product_id, slug, name_en or None))
        category_rows = db.execute(
            select(PromotionRuleCategory.rule_id, PromotionRuleCategory.category_id)
            .where(PromotionRuleCategory.rule_id.in_(rule_ids))
            .order_by(PromotionRuleCategory.category_id)
        )
        for rule_id, category_id in category_rows:
            categories[rule_id].append(category_id)
        tier_rows = db.execute(
            select(
                PromotionSpendTier.rule_id,
                PromotionSpendTier.threshold_sen,
                PromotionSpendTier.amount_sen,
                PromotionSpendTier.percent,
            )
            .where(PromotionSpendTier.rule_id.in_(rule_ids))
            .order_by(PromotionSpendTier.rule_id, PromotionSpendTier.position)
        )
        for rule_id, threshold_sen, amount_sen, percent in tier_rows:
            tiers[rule_id].append(TierInput(threshold_sen, amount_sen, percent))

    return tuple(
        RuleDetail(
            rule_id=row[0],
            kind=row[1],
            **dict(zip(_RULE_PARAMS, row[2:], strict=True)),
            product_ids=tuple(product.id for product in products[row[0]]),
            category_ids=tuple(categories[row[0]]),
            tiers=tuple(tiers[row[0]]),
            products=tuple(products[row[0]]),
        )
        for row in rule_rows
    )


def _decoration_statement(
    promotion_id: int, *, lock: bool = False
) -> Select[tuple[PromotionDecoration]]:
    stmt = (
        select(PromotionDecoration)
        .where(PromotionDecoration.promotion_id == promotion_id)
        .execution_options(populate_existing=True)
    )
    return stmt.with_for_update() if lock else stmt


def _detail(
    db: Session, promotion: Promotion, decoration: PromotionDecoration, now: datetime
) -> PromotionDetail:
    return PromotionDetail(
        id=promotion.id,
        version=promotion.version,
        state=_state(promotion, now),
        ended_early_at=to_local(promotion.ended_early_at),
        name=promotion.name,
        starts_at=to_local(promotion.starts_at),
        ends_at=to_local(promotion.ends_at),
        coupon_combinable=bool(promotion.coupon_combinable),
        points_combinable=bool(promotion.points_combinable),
        rules=_load_rules(db, promotion.id),
        decoration=DecorationInput(
            **{f.name: getattr(decoration, f.name) for f in fields(DecorationInput)}
        ),
        copy=CopyInput(**{f.name: getattr(decoration, f.name) for f in fields(CopyInput)}),
        poster_desktop_url=_poster_path(decoration.poster_desktop_file_name),
        poster_phone_url=_poster_path(decoration.poster_phone_file_name),
    )


def _ticks(value: datetime) -> int:
    return (value - datetime.min) // timedelta(microseconds=1)


def list_promotions(db: Session, now: datetime) -> list[PromotionSummary]:
    """全部活动，按 running、scheduled（开始从早到晚）、draft（最近修改在前）、
    ended（实际结束从晚到早）排列，同序按 ID 从大到小。只发 SELECT。

    now 为不带时区的 UTC，用来算状态。
    """
    _require_naive(now, "now")
    rule_count = (
        select(func.count(PromotionRule.id))
        .where(PromotionRule.promotion_id == Promotion.id)
        .correlate(Promotion)
        .scalar_subquery()
    )
    rows = db.execute(
        select(
            Promotion.id,
            Promotion.name,
            Promotion.status,
            Promotion.starts_at,
            Promotion.ends_at,
            Promotion.ended_early_at,
            Promotion.updated_at,
            rule_count,
        )
    ).all()

    keyed: list[tuple[tuple[int, int, int], PromotionSummary]] = []
    for promotion_id, name, status, starts_at, ends_at, ended_early_at, updated_at, count in rows:
        effective_end = ended_early_at or ends_at
        state = promotion_state(status, starts_at, effective_end, now)
        if state == STATE_SCHEDULED:
            second = _ticks(starts_at)
        elif state == STATE_DRAFT:
            second = -_ticks(updated_at)
        elif state == STATE_ENDED:
            second = -_ticks(effective_end)
        else:
            second = 0
        summary = PromotionSummary(
            id=promotion_id,
            name=name,
            state=state,
            starts_at=to_local(starts_at),
            ends_at=to_local(effective_end),
            rule_count=count,
            updated_at=updated_at,
        )
        keyed.append(((_STATE_ORDER[state], second, -promotion_id), summary))
    keyed.sort(key=lambda item: item[0])
    return [summary for _, summary in keyed]


def read_promotion(db: Session, promotion_id: int, now: datetime) -> PromotionDetail:
    """一个活动的全部可改内容（PromotionDetail），不存在抛 PromotionNotFound。只发 SELECT。"""
    _require_naive(now, "now")
    promotion = db.execute(
        select(Promotion)
        .where(Promotion.id == promotion_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if promotion is None:
        raise PromotionNotFound(promotion_id)
    decoration = db.execute(_decoration_statement(promotion.id)).scalar_one()
    return _detail(db, promotion, decoration, now)


# ---------------------------------------------------------------------------
# 查库的检查（供 SHOP-TASK-241 复用的三个函数导出）
# ---------------------------------------------------------------------------


def _reference_problems(db: Session, rules: Sequence[RuleInput]) -> list[PromotionProblem]:
    """规则引用的商品与分类须存在：每条规则的 product_ids、category_ids 各至多一个 scope。"""
    product_ids = {product_id for rule in rules for product_id in rule.product_ids}
    category_ids = {category_id for rule in rules for category_id in rule.category_ids}
    existing_products: set[int] = set()
    existing_categories: set[int] = set()
    if product_ids:
        existing_products = set(db.scalars(select(Product.id).where(Product.id.in_(product_ids))))
    if category_ids:
        existing_categories = set(
            db.scalars(select(Category.id).where(Category.id.in_(category_ids)))
        )
    problems: list[PromotionProblem] = []
    for position, rule in enumerate(rules, start=1):
        if not set(rule.product_ids) <= existing_products:
            problems.append(PromotionProblem(PROBLEM_SCOPE, f"rules.{position}.product_ids"))
        if not set(rule.category_ids) <= existing_categories:
            problems.append(PromotionProblem(PROBLEM_SCOPE, f"rules.{position}.category_ids"))
    return problems


_Scope = tuple[int, Sequence[int], Sequence[int]]


def _overlap_problems(db: Session, scopes: Sequence[_Scope]) -> list[PromotionProblem]:
    """第 3 条「一件商品在同一活动里至多属于一条商品级规则」。

    scopes 为商品级规则的（排序、商品 ID、分类 ID）。每条规则的商品为显式选的
    商品（不论是否上架），加上所选分类当前下属且满足现有发布规则的商品。每件
    落在两条及以上规则里的商品一个问题，field 为它第二次出现的规则（rules.<排序>），
    按排序与商品 ID 排列；product_name 为英文名称，为空时用 slug。不存在的商品
    不报（已由引用检查报出）。
    """
    category_ids = {category_id for _, _, categories in scopes for category_id in categories}
    in_category: dict[int, set[int]] = {}
    if category_ids:
        rows = db.execute(
            select(Product.id, Product.category_id)
            .join(Category, Category.id == Product.category_id)
            .where(Product.category_id.in_(category_ids), published())
        )
        for product_id, category_id in rows:
            in_category.setdefault(category_id, set()).add(product_id)

    first_seen: set[int] = set()
    repeated: dict[int, int] = {}
    for position, products, categories in sorted(scopes, key=lambda scope: scope[0]):
        members = set(products)
        for category_id in categories:
            members |= in_category.get(category_id, set())
        for product_id in members:
            if product_id in first_seen:
                repeated.setdefault(product_id, position)
            else:
                first_seen.add(product_id)
    if not repeated:
        return []

    names = {
        product_id: name_en or slug
        for product_id, name_en, slug in db.execute(
            select(Product.id, Product.name_en, Product.slug).where(Product.id.in_(list(repeated)))
        )
    }
    return [
        PromotionProblem(
            PROBLEM_SCOPE_OVERLAP,
            f"rules.{position}",
            product_id=product_id,
            product_name=names[product_id],
        )
        for product_id, position in sorted(repeated.items(), key=lambda item: (item[1], item[0]))
        if product_id in names
    ]


def _input_scopes(rules: Sequence[RuleInput]) -> list[_Scope]:
    return [
        (position, rule.product_ids, rule.category_ids)
        for position, rule in enumerate(rules, start=1)
        if rule.kind in ITEM_KINDS
    ]


def scope_overlap_problems(db: Session, promotion_id: int) -> list[PromotionProblem]:
    """该活动已保存的商品级规则之间的 scope_overlap 问题，按当时的商品与分类计算
    （口径见 _overlap_problems）。不加锁。"""
    rules = _load_rules(db, promotion_id)
    return _overlap_problems(
        db,
        [
            (position, rule.product_ids, rule.category_ids)
            for position, rule in enumerate(rules, start=1)
            if rule.kind in ITEM_KINDS
        ],
    )


def _publish_missing(
    *,
    rule_count: int,
    title_en: str | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    poster_mode: str,
    poster_desktop_file_name: str | None,
    poster_phone_file_name: str | None,
    poster_alt_en: str | None,
    now: datetime,
) -> list[PromotionProblem]:
    """第 2 条的发布条件（时间重叠另由 ensure_no_overlap 查），缺的每项一个
    publish_missing，按 rules、title_en、starts_at、ends_at 或 ends_at_past、
    poster_desktop、poster_phone、poster_alt_en 的顺序。"""
    missing: list[str] = []
    if rule_count == 0:
        missing.append("rules")
    if clean_text(title_en) is None:
        missing.append("title_en")
    if starts_at is None:
        missing.append("starts_at")
    if ends_at is None:
        missing.append("ends_at")
    elif ends_at <= now:
        missing.append("ends_at_past")
    if poster_mode == POSTER_MODE_IMAGE:
        if poster_desktop_file_name is None:
            missing.append("poster_desktop")
        if poster_phone_file_name is None:
            missing.append("poster_phone")
        if clean_text(poster_alt_en) is None:
            missing.append("poster_alt_en")
    return [PromotionProblem(PROBLEM_PUBLISH_MISSING, field) for field in missing]


def publish_problems(db: Session, promotion: Promotion, now: datetime) -> list[PromotionProblem]:
    """已保存的活动缺的发布条件（第 2 条：至少一条规则、英文标题、开始与结束时间、
    结束晚于 now、上传图模式的两张图与英文替代文字），每项一个 publish_missing。

    不查时间重叠（见 ensure_no_overlap），不加锁；now 为不带时区的 UTC。
    """
    _require_naive(now, "now")
    rule_count = db.scalar(
        select(func.count(PromotionRule.id)).where(PromotionRule.promotion_id == promotion.id)
    )
    decoration = db.execute(_decoration_statement(promotion.id)).scalar_one()
    return _publish_missing(
        rule_count=rule_count or 0,
        title_en=decoration.title_en,
        starts_at=promotion.starts_at,
        ends_at=promotion.ends_at,
        poster_mode=decoration.poster_mode,
        poster_desktop_file_name=decoration.poster_desktop_file_name,
        poster_phone_file_name=decoration.poster_phone_file_name,
        poster_alt_en=decoration.poster_alt_en,
        now=now,
    )


def ensure_no_overlap(
    db: Session, promotion_id: int, starts_at: datetime, effective_end: datetime
) -> None:
    """第 1 条「所有已发布活动的开始时间到实际结束时间两两不得重叠（相接可以）」。

    与该活动以外的已发布活动比较，重叠抛 PromotionOverlap（对方取开始最早、
    同时 ID 最小的一个）。调用方须已以 lock_publish_row 锁住发布锁那一行。
    """
    other_end = func.coalesce(Promotion.ended_early_at, Promotion.ends_at)
    name = db.scalar(
        select(Promotion.name)
        .where(
            Promotion.status == STATUS_PUBLISHED,
            Promotion.id != promotion_id,
            Promotion.starts_at < effective_end,
            other_end > starts_at,
        )
        .order_by(Promotion.starts_at, Promotion.id)
        .limit(1)
    )
    if name is not None:
        raise PromotionOverlap(name)


def lock_publish_row(db: Session) -> None:
    """锁住 promotion_publish_locks 那一行（SELECT … FOR UPDATE）。

    保存与发布都以它为新事务的第一条语句。表里没有那一行（迁移 0027 应已写入）
    是部署错误，抛 RuntimeError。
    """
    row = db.scalar(
        select(PromotionPublishLock.id)
        .where(PromotionPublishLock.singleton_slot == SINGLETON_SLOT)
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("promotion_publish_locks has no row")


def _has_orders(db: Session, promotion_id: int) -> bool:
    """是否已有订单引用该活动（orders.promotion_id），以共享锁读。"""
    order_id = db.scalar(
        select(Order.id)
        .where(Order.promotion_id == promotion_id)
        .limit(1)
        .with_for_update(read=True)
    )
    return order_id is not None


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def _rule_key(rule: RuleInput) -> tuple[object, ...]:
    """「规则与当前相同」的比较口径：类别与参数相同，商品 ID 与分类 ID 作为集合
    相同，满额档按顺序逐档相同。"""
    return (
        rule.kind,
        *(getattr(rule, name) for name in _RULE_PARAMS),
        frozenset(rule.product_ids),
        frozenset(rule.category_ids),
        tuple((tier.threshold_sen, tier.amount_sen, tier.percent) for tier in rule.tiers),
    )


def _rules_equal(new: Sequence[RuleInput], current: Sequence[RuleDetail]) -> bool:
    """条数与顺序相同，且逐条按 _rule_key 相同。"""
    return [_rule_key(rule) for rule in new] == [_rule_key(rule.as_input()) for rule in current]


def _write_rules(db: Session, promotion_id: int, rules: Sequence[RuleInput]) -> None:
    for position, rule in enumerate(rules, start=1):
        row = PromotionRule(
            promotion_id=promotion_id,
            position=position,
            kind=rule.kind,
            **{name: getattr(rule, name) for name in _RULE_PARAMS},
        )
        db.add(row)
        db.flush()
        for product_id in rule.product_ids:
            db.add(PromotionRuleProduct(rule_id=row.id, product_id=product_id))
        for category_id in rule.category_ids:
            db.add(PromotionRuleCategory(rule_id=row.id, category_id=category_id))
        for number, tier in enumerate(rule.tiers, start=1):
            db.add(
                PromotionSpendTier(
                    rule_id=row.id,
                    position=number,
                    threshold_sen=tier.threshold_sen,
                    amount_sen=tier.amount_sen,
                    percent=tier.percent,
                )
            )
    db.flush()


def _replace_rules(db: Session, promotion_id: int, rules: Sequence[RuleInput]) -> None:
    """整批删除该活动的规则、范围与满额档，再按 rules 写入（新规则的 ID 是新的）。

    撞上订单逐件快照的外键（IntegrityError）抛 PromotionChanged。
    """
    old_ids = list(
        db.scalars(select(PromotionRule.id).where(PromotionRule.promotion_id == promotion_id))
    )
    # fetch：会话里可能留着已过期的旧规则对象，按删掉的主键把它们移出会话，
    # 免得新行重用同一 ID 时撞上。
    sync = {"synchronize_session": "fetch"}
    try:
        if old_ids:
            for model, column in (
                (PromotionRuleProduct, PromotionRuleProduct.rule_id),
                (PromotionRuleCategory, PromotionRuleCategory.rule_id),
                (PromotionSpendTier, PromotionSpendTier.rule_id),
                (PromotionRule, PromotionRule.id),
            ):
                db.execute(delete(model).where(column.in_(old_ids)).execution_options(**sync))
        _write_rules(db, promotion_id, rules)
    except IntegrityError:
        raise PromotionChanged("the promotion rules are referenced by orders") from None


def _audit_value(field: str, value: str | bool | int | None) -> str:
    """审计的「字段 值」：布尔为 true 或 false，空值只写字段名，文字取前 100 个字符。"""
    if isinstance(value, bool):
        return f"{field} {'true' if value else 'false'}"
    if value is None or value == "":
        return field
    return f"{field} {str(value)[:AUDIT_TEXT_LENGTH]}"


def create_promotion(
    db: Session, admin_account_id: int, data: PromotionInput, now: datetime
) -> PromotionDetail:
    """新建一个草稿（版本号 1，装饰一行），返回它的 PromotionDetail。

    now 为不带时区的 UTC（写入创建与修改时间、交给 record_audit）；admin_account_id
    须在调用前取出（这里先回滚）。按以下顺序：
    1. check_promotion_input，不发 SQL；不合格抛 PromotionInvalid，结构不对抛 TypeError。
    2. 回滚此前的事务后查库：规则引用的商品与分类须存在（scope）、商品级规则两两
       不得有共同的商品（scope_overlap），有问题抛 PromotionInvalid。草稿不查发布
       条件与时间重叠，不锁 promotion_publish_locks。
    3. 写入活动、装饰与规则，写一条 admin_promotion_created 审计（新值为内部名称）后提交。
    任何异常都先回滚再抛出。
    """
    _require_naive(now, "now")
    data = check_promotion_input(data)
    starts_at, ends_at = to_utc(data.starts_at), to_utc(data.ends_at)

    db.rollback()
    try:
        problems = _reference_problems(db, data.rules)
        problems.extend(_overlap_problems(db, _input_scopes(data.rules)))
        if problems:
            raise PromotionInvalid(problems)

        promotion = Promotion(
            name=data.name,
            status=STATUS_DRAFT,
            starts_at=starts_at,
            ends_at=ends_at,
            ended_early_at=None,
            coupon_combinable=data.coupon_combinable,
            points_combinable=data.points_combinable,
            version=NEW_PROMOTION_VERSION,
            created_at=now,
            updated_at=now,
        )
        db.add(promotion)
        db.flush()
        decoration = PromotionDecoration(
            promotion_id=promotion.id,
            **{f.name: getattr(data.decoration, f.name) for f in fields(DecorationInput)},
            **{f.name: getattr(data.copy, f.name) for f in fields(CopyInput)},
            updated_at=now,
        )
        db.add(decoration)
        _write_rules(db, promotion.id, data.rules)
        record_audit(
            db,
            ADMIN_PROMOTION_CREATED,
            admin_account_id,
            now,
            target_type=AUDIT_TARGET_PROMOTION,
            target_id=promotion.id,
            new_value=data.name,
        )
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


def save_promotion(
    db: Session,
    admin_account_id: int,
    promotion_id: int,
    version: int,
    data: PromotionInput,
    now: datetime,
) -> PromotionDetail:
    """以一次保存提交一个活动的内部名称、时间、两个开关、规则、装饰与文案，
    返回保存后的 PromotionDetail。

    now 与 admin_account_id 同 create_promotion。按以下顺序：
    1. check_promotion_input，不发 SQL；version 不是整数（布尔值不算）抛 TypeError。
    2. 回滚此前的事务，以 lock_publish_row 为新事务的第一条语句，再锁活动行：
       不存在或版本号与当前不同抛 PromotionChanged。再锁装饰行，按 now 算状态。
    3. ended：抛 PromotionInvalid（ended_readonly，field 为 state）。scheduled 而已有
       订单引用的按 running 处理。running：时间、两个开关与规则须与当前相同，
       不同的每项一个 rules_locked（field 为 starts_at、ends_at、coupon_combinable、
       points_combinable、rules）。draft 与 scheduled：引用存在（scope）与商品级
       规则不重叠（scope_overlap）。scheduled 与 running：保存后的内容仍须满足
       发布条件（publish_missing）。有问题抛 PromotionInvalid（列出全部问题）。
    4. scheduled（未按 running 处理时）：ensure_no_overlap，重叠抛 PromotionOverlap。
    5. 按内部名称、开始、结束、两个开关、规则、装饰、文案的顺序与当前值比较，
       有改动的写入（规则有改动时整批替换，撞上订单逐件快照的外键抛
       PromotionChanged），版本号加一、修改时间取 now，每个改动的字段一条
       admin_promotion_changed 审计（规则只写一条，值为「rules 条数」）后提交；
       都没有改动时回滚、不加版本、不写审计。海报图不随保存提交，不在这里改。
    任何异常都先回滚再抛出。
    """
    _require_naive(now, "now")
    # bool 是 int 的子类，但 True 不是一个版本号。
    if type(version) is not int:
        raise TypeError("version must be an integer")
    data = check_promotion_input(data)
    new_starts, new_ends = to_utc(data.starts_at), to_utc(data.ends_at)

    db.rollback()
    try:
        lock_publish_row(db)
        promotion = db.execute(
            select(Promotion)
            .where(Promotion.id == promotion_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if promotion is None or promotion.version != version:
            raise PromotionChanged("the promotion was changed since it was read")
        decoration = db.execute(_decoration_statement(promotion.id, lock=True)).scalar_one()

        state = _state(promotion, now)
        if state == STATE_ENDED:
            raise PromotionInvalid([PromotionProblem(PROBLEM_ENDED_READONLY, STATE_FIELD)])
        locked = state == STATE_RUNNING or (
            state == STATE_SCHEDULED and _has_orders(db, promotion.id)
        )
        current_rules = _load_rules(db, promotion.id)
        rules_changed = not _rules_equal(data.rules, current_rules)

        problems: list[PromotionProblem] = []
        if locked:
            for field, current, requested in (
                ("starts_at", to_local(promotion.starts_at), data.starts_at),
                ("ends_at", to_local(promotion.ends_at), data.ends_at),
                ("coupon_combinable", bool(promotion.coupon_combinable), data.coupon_combinable),
                ("points_combinable", bool(promotion.points_combinable), data.points_combinable),
            ):
                if current != requested:
                    problems.append(PromotionProblem(PROBLEM_RULES_LOCKED, field))
            if rules_changed:
                problems.append(PromotionProblem(PROBLEM_RULES_LOCKED, "rules"))
        else:
            problems.extend(_reference_problems(db, data.rules))
            problems.extend(_overlap_problems(db, _input_scopes(data.rules)))
        if promotion.status == STATUS_PUBLISHED:
            # 按 running 处理时时间与规则不能改，按当前值查（只有内容方面可能缺）。
            problems.extend(
                _publish_missing(
                    rule_count=len(current_rules) if locked else len(data.rules),
                    title_en=data.copy.title_en,
                    starts_at=promotion.starts_at if locked else new_starts,
                    ends_at=promotion.ends_at if locked else new_ends,
                    poster_mode=data.decoration.poster_mode,
                    poster_desktop_file_name=decoration.poster_desktop_file_name,
                    poster_phone_file_name=decoration.poster_phone_file_name,
                    poster_alt_en=data.copy.poster_alt_en,
                    now=now,
                )
            )
        if problems:
            raise PromotionInvalid(problems)
        if promotion.status == STATUS_PUBLISHED and not locked:
            ensure_no_overlap(db, promotion.id, new_starts, new_ends)

        # 按 running 处理时，时间、开关与规则到这里都与当前相同，下面不会改它们。
        changes: list[tuple[str, str | bool | int | None, str | bool | int | None]] = []
        if promotion.name != data.name:
            changes.append(("name", promotion.name, data.name))
            promotion.name = data.name
        for field, requested_local, requested_utc in (
            ("starts_at", data.starts_at, new_starts),
            ("ends_at", data.ends_at, new_ends),
        ):
            current_local = to_local(getattr(promotion, field))
            if current_local != requested_local:
                changes.append((field, current_local, requested_local))
                setattr(promotion, field, requested_utc)
        for field in ("coupon_combinable", "points_combinable"):
            current_flag, requested_flag = bool(getattr(promotion, field)), getattr(data, field)
            if current_flag != requested_flag:
                changes.append((field, current_flag, requested_flag))
                setattr(promotion, field, requested_flag)
        if rules_changed:
            changes.append(("rules", len(current_rules), len(data.rules)))
        decoration_changed = False
        for source, names in (
            (data.decoration, fields(DecorationInput)),
            (data.copy, fields(CopyInput)),
        ):
            for f in names:
                current_value = getattr(decoration, f.name)
                requested_value = getattr(source, f.name)
                if current_value != requested_value:
                    changes.append((f.name, current_value, requested_value))
                    setattr(decoration, f.name, requested_value)
                    decoration_changed = True

        if not changes:
            saved = _detail(db, promotion, decoration, now)
            db.rollback()
            return saved

        if rules_changed:
            _replace_rules(db, promotion.id, data.rules)
        promotion.version += 1
        promotion.updated_at = now
        if decoration_changed:
            decoration.updated_at = now
        db.flush()
        for field, old_value, new_value in changes:
            record_audit(
                db,
                ADMIN_PROMOTION_CHANGED,
                admin_account_id,
                now,
                target_type=AUDIT_TARGET_PROMOTION,
                target_id=promotion.id,
                old_value=_audit_value(field, old_value),
                new_value=_audit_value(field, new_value),
            )
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


# ===========================================================================
# 状态操作：发布、撤回、提前结束、删除与复制（SHOP-TASK-241）
#
# 第 2 条与 UX 0.20 A12 的状态表：发布只对 draft，撤回只对 scheduled，提前结束
# 只对 running，删除只对 draft，复制对任何状态。前四种带读取时的版本号，先回滚
# 此前的事务、再以 FOR UPDATE 锁活动行核对（不存在或不同抛 PromotionChanged），
# 状态在加锁后按 now 算出；任何异常先回滚再抛出。发布、撤回与提前结束版本加一、
# 修改时间取 now；删除与复制不改原活动的版本。
#
# 加锁顺序与保存相同：只有发布先锁 promotion_publish_locks 那一行（它会让活动
# 进入已发布的集合，须查时间重叠）；撤回、提前结束与删除只让活动离开已发布的
# 集合或缩短实际结束时间，不会造成重叠，所以从活动行开始锁。
#
# 撤回与订单：下单事务读生效活动时加共享锁（SHOP-TASK-233 的 share_lock）。撤回
# 锁住活动行后以 _has_orders 查订单引用，有即按已开始处理、拒绝；没有时提交前
# 下单读不到它，提交后下单读到的已是草稿，所以撤回成功的活动不被任何订单引用，
# 之后作为草稿删除不会撞上订单的外键。
# ===========================================================================

ADMIN_PROMOTION_PUBLISHED = "admin_promotion_published"
ADMIN_PROMOTION_WITHDRAWN = "admin_promotion_withdrawn"
ADMIN_PROMOTION_ENDED = "admin_promotion_ended"
ADMIN_PROMOTION_DELETED = "admin_promotion_deleted"
ADMIN_PROMOTION_COPIED = "admin_promotion_copied"

# 复制时清空的文案列：海报改回模板模式，替代文字随海报图一并不复制。
_POSTER_ALT_FIELDS = ("poster_alt_en", "poster_alt_zh", "poster_alt_ms")


class PromotionStateInvalid(Exception):
    """当前状态不允许这个操作（第 2 条、UX A12 的状态表），state 为当前状态。

    已有订单引用的 scheduled 活动撤回时，state 为 running（按已开始处理）。
    """

    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


def _check_version(version: object) -> None:
    # bool 是 int 的子类，但 True 不是一个版本号。
    if type(version) is not int:
        raise TypeError("version must be an integer")


def _lock_promotion(db: Session, promotion_id: int, version: int) -> Promotion:
    """以 FOR UPDATE 锁活动行；不存在或版本号与当前不同抛 PromotionChanged。"""
    promotion = db.execute(
        select(Promotion)
        .where(Promotion.id == promotion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if promotion is None or promotion.version != version:
        raise PromotionChanged("the promotion was changed since it was read")
    return promotion


def _record(
    db: Session,
    action: str,
    admin_account_id: int,
    now: datetime,
    *,
    target_id: int,
    old_value: str | None = None,
) -> None:
    """写一条对象类别为 promotion 的状态操作审计（没有新值），只 flush。"""
    record_audit(
        db,
        action,
        admin_account_id,
        now,
        target_type=AUDIT_TARGET_PROMOTION,
        target_id=target_id,
        old_value=old_value,
    )


def publish_promotion(
    db: Session, admin_account_id: int, promotion_id: int, version: int, now: datetime
) -> PromotionDetail:
    """发布一个草稿（第 2 条），返回发布后的 PromotionDetail。

    now 为不带时区的 UTC；admin_account_id 须在调用前取出（这里先回滚）。
    version 不是整数抛 TypeError。按以下顺序：
    1. 回滚，以 lock_publish_row 为新事务的第一条语句，再锁活动行（不存在或版本
       不同抛 PromotionChanged）、再锁装饰行；不是 draft 抛 PromotionStateInvalid。
    2. publish_problems（publish_missing）与 scope_overlap_problems（按当时的商品
       与分类），有问题抛 PromotionInvalid（列出全部问题）。
    3. ensure_no_overlap，与其他已发布活动时间重叠抛 PromotionOverlap。
    4. status 改为 published、版本加一、修改时间取 now，写一条
       admin_promotion_published 审计后提交。开始时间已过的发布即生效。
    """
    _require_naive(now, "now")
    _check_version(version)

    db.rollback()
    try:
        lock_publish_row(db)
        promotion = _lock_promotion(db, promotion_id, version)
        decoration = db.execute(_decoration_statement(promotion.id, lock=True)).scalar_one()
        state = _state(promotion, now)
        if state != STATE_DRAFT:
            raise PromotionStateInvalid(state)

        problems = publish_problems(db, promotion, now)
        problems.extend(scope_overlap_problems(db, promotion.id))
        if problems:
            raise PromotionInvalid(problems)
        ensure_no_overlap(db, promotion.id, promotion.starts_at, _effective_end(promotion))

        promotion.status = STATUS_PUBLISHED
        promotion.version += 1
        promotion.updated_at = now
        db.flush()
        _record(db, ADMIN_PROMOTION_PUBLISHED, admin_account_id, now, target_id=promotion.id)
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


def withdraw_promotion(
    db: Session, admin_account_id: int, promotion_id: int, version: int, now: datetime
) -> PromotionDetail:
    """把已发布未开始的活动撤回为草稿（第 2 条），返回撤回后的 PromotionDetail。

    now、admin_account_id 与 version 同 publish_promotion。回滚后锁活动行（不存在
    或版本不同抛 PromotionChanged）；不是 scheduled 抛 PromotionStateInvalid；已有
    订单引用（以共享锁读 orders.promotion_id）按已开始处理，抛
    PromotionStateInvalid（state 为 running）。之后 status 改回 draft、版本加一、
    修改时间取 now，写一条 admin_promotion_withdrawn 审计后提交。不锁发布锁。
    """
    _require_naive(now, "now")
    _check_version(version)

    db.rollback()
    try:
        promotion = _lock_promotion(db, promotion_id, version)
        state = _state(promotion, now)
        if state != STATE_SCHEDULED:
            raise PromotionStateInvalid(state)
        if _has_orders(db, promotion.id):
            raise PromotionStateInvalid(STATE_RUNNING)
        decoration = db.execute(_decoration_statement(promotion.id)).scalar_one()

        promotion.status = STATUS_DRAFT
        promotion.version += 1
        promotion.updated_at = now
        db.flush()
        _record(db, ADMIN_PROMOTION_WITHDRAWN, admin_account_id, now, target_id=promotion.id)
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


def end_promotion(
    db: Session, admin_account_id: int, promotion_id: int, version: int, now: datetime
) -> PromotionDetail:
    """提前结束进行中的活动（第 2 条），返回结束后的 PromotionDetail（state 为 ended）。

    now、admin_account_id 与 version 同 publish_promotion。回滚后锁活动行（不存在
    或版本不同抛 PromotionChanged）；不是 running 抛 PromotionStateInvalid。
    ended_early_at 取 now 截到整秒（不会早于开始、不会晚于结束：两者都是整分钟），
    版本加一、修改时间取 now，写一条 admin_promotion_ended 审计后提交。之后活动为
    ended，不能再提前结束或恢复。只缩短实际结束时间，不锁发布锁。
    """
    _require_naive(now, "now")
    _check_version(version)

    db.rollback()
    try:
        promotion = _lock_promotion(db, promotion_id, version)
        state = _state(promotion, now)
        if state != STATE_RUNNING:
            raise PromotionStateInvalid(state)
        decoration = db.execute(_decoration_statement(promotion.id)).scalar_one()

        promotion.ended_early_at = now.replace(microsecond=0)
        promotion.version += 1
        promotion.updated_at = now
        db.flush()
        _record(db, ADMIN_PROMOTION_ENDED, admin_account_id, now, target_id=promotion.id)
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved


def delete_promotion(
    db: Session,
    settings: Settings,
    admin_account_id: int,
    promotion_id: int,
    version: int,
    now: datetime,
) -> None:
    """删除一个草稿（第 2 条「草稿可任意修改、删除（连同它的海报图）」）。

    now、admin_account_id 与 version 同 publish_promotion。回滚后锁活动行（不存在
    或版本不同抛 PromotionChanged）；不是 draft 抛 PromotionStateInvalid。再锁装饰行，
    读出两张海报图的文件名，删除规则的范围与满额档、规则、装饰行与活动行，写一条
    admin_promotion_deleted 审计（旧值为内部名称）后提交。提交后以
    image_store.delete_image（前缀 POSTER_KEY_PREFIX）删除每张海报图的对象（只尝试
    1 次，存储未配置或失败都只记日志）。
    """
    _require_naive(now, "now")
    _check_version(version)

    db.rollback()
    try:
        promotion = _lock_promotion(db, promotion_id, version)
        state = _state(promotion, now)
        if state != STATE_DRAFT:
            raise PromotionStateInvalid(state)
        decoration = db.execute(_decoration_statement(promotion.id, lock=True)).scalar_one()
        # 提交后 ORM 对象过期（且已删除），先取出要用的值。
        name = promotion.name
        file_names = [
            file_name
            for file_name in (
                decoration.poster_desktop_file_name,
                decoration.poster_phone_file_name,
            )
            if file_name is not None
        ]

        rule_ids = list(
            db.scalars(select(PromotionRule.id).where(PromotionRule.promotion_id == promotion_id))
        )
        sync = {"synchronize_session": "fetch"}
        if rule_ids:
            for model, column in (
                (PromotionRuleProduct, PromotionRuleProduct.rule_id),
                (PromotionRuleCategory, PromotionRuleCategory.rule_id),
                (PromotionSpendTier, PromotionSpendTier.rule_id),
                (PromotionRule, PromotionRule.id),
            ):
                db.execute(delete(model).where(column.in_(rule_ids)).execution_options(**sync))
        for statement in (
            delete(PromotionDecoration).where(PromotionDecoration.promotion_id == promotion_id),
            delete(Promotion).where(Promotion.id == promotion_id),
        ):
            db.execute(statement.execution_options(**sync))
        _record(
            db,
            ADMIN_PROMOTION_DELETED,
            admin_account_id,
            now,
            target_id=promotion_id,
            old_value=name,
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    for file_name in file_names:
        image_store.delete_image(settings, file_name, prefix=image_store.POSTER_KEY_PREFIX)


def copy_promotion(
    db: Session, admin_account_id: int, promotion_id: int, now: datetime
) -> PromotionDetail:
    """把任何状态的活动复制为新草稿（第 2 条），返回新草稿的 PromotionDetail。

    now 与 admin_account_id 同 publish_promotion。回滚后读原活动（不加锁），不存在抛
    PromotionNotFound。新草稿复制内部名称、规则（含范围与满额档，规则 ID 是新的）、
    两个开关、装饰与文案；不复制时间与海报图：开始与结束为空，海报改回模板模式、
    两张图与三语替代文字清空。版本号 1，创建与修改时间取 now。写一条
    admin_promotion_copied 审计（对象为新草稿，旧值为原活动 ID）后提交。
    原活动不改，版本不变。
    """
    _require_naive(now, "now")

    db.rollback()
    try:
        source = db.execute(
            select(Promotion)
            .where(Promotion.id == promotion_id)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if source is None:
            raise PromotionNotFound(promotion_id)
        source_decoration = db.execute(_decoration_statement(source.id)).scalar_one()
        rules = [rule.as_input() for rule in _load_rules(db, source.id)]

        promotion = Promotion(
            name=source.name,
            status=STATUS_DRAFT,
            starts_at=None,
            ends_at=None,
            ended_early_at=None,
            coupon_combinable=bool(source.coupon_combinable),
            points_combinable=bool(source.points_combinable),
            version=NEW_PROMOTION_VERSION,
            created_at=now,
            updated_at=now,
        )
        db.add(promotion)
        db.flush()
        decoration_values = {
            f.name: getattr(source_decoration, f.name) for f in fields(DecorationInput)
        }
        copy_values = {f.name: getattr(source_decoration, f.name) for f in fields(CopyInput)}
        decoration_values["poster_mode"] = POSTER_MODE_TEMPLATE
        for name in _POSTER_ALT_FIELDS:
            copy_values[name] = None
        decoration = PromotionDecoration(
            promotion_id=promotion.id,
            **decoration_values,
            **copy_values,
            updated_at=now,
        )
        db.add(decoration)
        _write_rules(db, promotion.id, rules)
        _record(
            db,
            ADMIN_PROMOTION_COPIED,
            admin_account_id,
            now,
            target_id=promotion.id,
            old_value=str(source.id),
        )
        saved = _detail(db, promotion, decoration, now)
    except BaseException:
        db.rollback()
        raise

    db.commit()
    return saved

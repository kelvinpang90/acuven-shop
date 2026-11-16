import type {
  AdminPromotion,
  PromotionCopy,
  PromotionDecoration,
  PromotionInput,
  PromotionRuleInput,
  PromotionScopeProduct,
} from "../api/adminPromotions";

// 后台促销活动 A12 编辑区的表单（docs/UX.md 0.20 A12 的「时间」「文案」两条与「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查」，
// SHOP-TASK-281）：表单的类型、新表单的默认值、读取结果与提交内容之间的换算、是否有未保存修改，以及内部名称、时间与文案的提交前检查。
// 只是页面提交前的检查与换算；取值、核对、版本与审计在服务端（app/services/admin_promotions.py 的 check_promotion_input）。
// 规则的表单换算与检查在 SHOP-TASK-282。

// 内部名称的字符数上限，与 app/models/promotion.py 的 NAME_MAX_LENGTH 相同。
export const NAME_MAX_LENGTH = 100;
// 文案的字符数上限，与 app/services/admin_promotions.py 的 COPY_MAX_LENGTHS 相同（按文案字段名去掉语言后缀取值）。
export const COPY_MAX_LENGTHS = { title: 40, subtitle: 80, badge: 12, strip: 60, poster_alt: 150 } as const;
// 活动时长上限（MAX_DURATION，92 天），以分钟计。
export const MAX_DURATION_MINUTES = 92 * 24 * 60;

export const COPY_FIELDS = [
  "title_en",
  "title_zh",
  "title_ms",
  "subtitle_en",
  "subtitle_zh",
  "subtitle_ms",
  "badge_en",
  "badge_zh",
  "badge_ms",
  "strip_en",
  "strip_zh",
  "strip_ms",
  "poster_alt_en",
  "poster_alt_zh",
  "poster_alt_ms",
] as const satisfies readonly (keyof PromotionCopy)[];
export type CopyField = (typeof COPY_FIELDS)[number];

// 文案的表单值：十五项都为字符串，空串即未填（读取时 null 读成空串）。
export type PromotionFormCopy = Record<CopyField, string>;

// 表单里的一条规则：提交的各项，读回的规则另带 rule_id 与 products（新加的规则没有），提交时这两项不带进。
export type PromotionFormRule = PromotionRuleInput & { rule_id?: number; products?: PromotionScopeProduct[] };

// 编辑区的表单。starts_at 与 ends_at 为 datetime-local 的「YYYY-MM-DDTHH:MM」，空串即未填。
export interface PromotionForm {
  name: string;
  starts_at: string;
  ends_at: string;
  coupon_combinable: boolean;
  points_combinable: boolean;
  rules: PromotionFormRule[];
  decoration: PromotionDecoration;
  copy: PromotionFormCopy;
}

// 提交前检查的一个问题：code 与 field 的写法同服务端的 PromotionProblem（name、starts_at、ends_at、copy.<字段>）。
export interface PromotionFormProblem {
  code: "name" | "time" | "duration" | "copy";
  field: string;
}

// Python str.isspace() 为真的字符，即 str.strip() 去掉的空白（与服务端 clean_text 一致）：JS 正则 \s 去掉 U+FEFF，再加 U+001C–U+001F 与 U+0085。
// 都在基本多文种平面且不是代理项，按 UTF-16 码元比较即可。
const PY_WHITESPACE: ReadonlySet<number> = new Set([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
  0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

// 换行与控制字符（同服务端 has_forbidden_char）：Unicode 类别 Cc，以及 U+2028、U+2029。
const FORBIDDEN_CHAR = /[\p{Cc}\u{2028}\u{2029}]/u;

// 服务端 to_utc 收的格式；只认 ASCII 数字。
const LOCAL_TIME = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})$/;
const MINUTES_PER_DAY = 24 * 60;
// 服务端减去 8 小时换成 UTC，早于 0001-01-01T08:00 会溢出。
const MALAYSIA_OFFSET_MINUTES = 8 * 60;
const MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31] as const;

// 去掉首尾空白，空白集同 Python 的 str.strip()。
export function pyStrip(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && PY_WHITESPACE.has(text.charCodeAt(start))) {
    start += 1;
  }
  while (end > start && PY_WHITESPACE.has(text.charCodeAt(end - 1))) {
    end -= 1;
  }
  return text.slice(start, end);
}

// 文字的字符数（服务端的 len(value.strip())）：pyStrip 后的码点数，供页面显示已用字数。
export function copyLength(text: string): number {
  return [...pyStrip(text)].length;
}

// 文案字段的字符数上限。
export function copyMaxLength(field: CopyField): number {
  const group = field.slice(0, field.lastIndexOf("_")) as keyof typeof COPY_MAX_LENGTHS;
  return COPY_MAX_LENGTHS[group];
}

function copyOf(read: (field: CopyField) => string): PromotionFormCopy {
  return Object.fromEntries(COPY_FIELDS.map((field) => [field, read(field)])) as PromotionFormCopy;
}

// 新表单：名称与时间为空、两个开关不勾、没有规则；装饰为通用素材包、沿用主题配色、各装饰位取 docs/design/tokens/promotions.json 的第一个变体、
// 模板海报；文案全空。
export function newPromotionForm(): PromotionForm {
  return {
    name: "",
    starts_at: "",
    ends_at: "",
    coupon_combinable: false,
    points_combinable: false,
    rules: [],
    decoration: {
      pack: "generic",
      palette: "theme",
      ambient: "garland",
      home_poster: "split",
      list_strip: "band",
      card_badge: "tag",
      detail_ribbon: "ribbon",
      cart_progress: "bar",
      poster_mode: "template",
    },
    copy: copyOf(() => ""),
  };
}

// 读取结果 → 表单：时间与文案的 null 读成空串，规则（含 rule_id 与 products）与装饰照读取的值复制，不与读取结果共用对象。
export function formFromPromotion(promotion: AdminPromotion): PromotionForm {
  return {
    name: promotion.name,
    starts_at: promotion.starts_at ?? "",
    ends_at: promotion.ends_at ?? "",
    coupon_combinable: promotion.coupon_combinable,
    points_combinable: promotion.points_combinable,
    rules: promotion.rules.map((rule) => ({
      ...rule,
      product_ids: [...rule.product_ids],
      category_ids: [...rule.category_ids],
      tiers: rule.tiers.map((tier) => ({ ...tier })),
      products: rule.products.map((product) => ({ ...product })),
    })),
    decoration: { ...promotion.decoration },
    copy: copyOf((field) => promotion.copy[field] ?? ""),
  };
}

// 提交的一条规则：去掉 rule_id 与 products，满额档每项恰为三个键。
function ruleInput(rule: PromotionFormRule): PromotionRuleInput {
  return {
    kind: rule.kind,
    discount_mode: rule.discount_mode,
    percent: rule.percent,
    amount_sen: rule.amount_sen,
    buy_quantity: rule.buy_quantity,
    get_quantity: rule.get_quantity,
    get_percent: rule.get_percent,
    threshold_sen: rule.threshold_sen,
    product_ids: [...rule.product_ids],
    category_ids: [...rule.category_ids],
    tiers: rule.tiers.map((tier) => ({ threshold_sen: tier.threshold_sen, amount_sen: tier.amount_sen, percent: tier.percent })),
  };
}

// 表单 → 提交内容：时间空串提交 null；文案 pyStrip 后为空提交 null，否则原样提交；名称原样提交；规则与装饰提交表单里当前的值。
export function promotionInput(form: PromotionForm): PromotionInput {
  const d = form.decoration;
  return {
    name: form.name,
    starts_at: form.starts_at === "" ? null : form.starts_at,
    ends_at: form.ends_at === "" ? null : form.ends_at,
    coupon_combinable: form.coupon_combinable,
    points_combinable: form.points_combinable,
    rules: form.rules.map(ruleInput),
    decoration: {
      pack: d.pack,
      palette: d.palette,
      ambient: d.ambient,
      home_poster: d.home_poster,
      list_strip: d.list_strip,
      card_badge: d.card_badge,
      detail_ribbon: d.detail_ribbon,
      cart_progress: d.cart_progress,
      poster_mode: d.poster_mode,
    },
    copy: copyInput(form.copy),
  };
}

function copyInput(formCopy: PromotionFormCopy): PromotionCopy {
  const copy: Partial<PromotionCopy> = {};
  for (const field of COPY_FIELDS) {
    const text = formCopy[field];
    copy[field] = pyStrip(text) === "" ? null : text;
  }
  return copy as PromotionCopy;
}

// 是否有未保存修改：两者的提交内容是否不同。promotionInput 按固定的键序构造，JSON 文本相同即内容相同。
export function isDirty(form: PromotionForm, base: PromotionForm): boolean {
  return JSON.stringify(promotionInput(form)) !== JSON.stringify(promotionInput(base));
}

// 同服务端 _text_ok：先查原文不含换行与控制字符，再按 pyStrip 后的码点计数。
function textOk(text: string, minimum: number, maximum: number): boolean {
  if (FORBIDDEN_CHAR.test(text)) {
    return false;
  }
  const length = copyLength(text);
  return minimum <= length && length <= maximum;
}

function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year: number, month: number): number {
  return month === 2 && isLeapYear(year) ? 29 : (MONTH_DAYS[month - 1] ?? 0);
}

// 公历 0001-01-01 起的天数（0001-01-01 为 0）。
function dayNumber(year: number, month: number, day: number): number {
  const before = year - 1;
  let days = before * 365 + Math.floor(before / 4) - Math.floor(before / 100) + Math.floor(before / 400);
  for (let m = 1; m < month; m += 1) {
    days += daysInMonth(year, m);
  }
  return days + day - 1;
}

// 本地时间「YYYY-MM-DDTHH:MM」→ 0001-01-01T00:00 起的分钟数；服务端 to_utc 收不下（格式不对、不存在的日期与时刻、减 8 小时溢出）为 null。
// 按各分量直接判断，不经 Date.UTC（它把 0 至 99 年当作 1900 年代）。
function localMinutes(value: string): number | null {
  const match = LOCAL_TIME.exec(value);
  if (match === null) {
    return null;
  }
  const [year = 0, month = 0, day = 0, hour = 0, minute = 0] = match.slice(1).map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month) || hour > 23 || minute > 59) {
    return null;
  }
  const minutes = dayNumber(year, month, day) * MINUTES_PER_DAY + hour * 60 + minute;
  return minutes < MALAYSIA_OFFSET_MINUTES ? null : minutes;
}

// 提交前检查（同服务端 check_promotion_input 里不查库、不涉及规则与装饰的部分），按名称、开始、结束、文案的顺序列出问题：
// - 名称：不含换行与控制字符，pyStrip 后 1 至 NAME_MAX_LENGTH 个字符。
// - 时间：空串不查；填了须是服务端收得下的「YYYY-MM-DDTHH:MM」；两端都合格时结束晚于开始（否则 time）且不超过 92 天（否则 duration），问题报在 ends_at。
// - 文案：不含换行与控制字符，pyStrip 后 0 至该项上限个字符。
export function formProblems(form: PromotionForm): PromotionFormProblem[] {
  const problems: PromotionFormProblem[] = [];
  if (!textOk(form.name, 1, NAME_MAX_LENGTH)) {
    problems.push({ code: "name", field: "name" });
  }

  let start: number | null = null;
  let end: number | null = null;
  if (form.starts_at !== "") {
    start = localMinutes(form.starts_at);
    if (start === null) {
      problems.push({ code: "time", field: "starts_at" });
    }
  }
  if (form.ends_at !== "") {
    end = localMinutes(form.ends_at);
    if (end === null) {
      problems.push({ code: "time", field: "ends_at" });
    }
  }
  if (start !== null && end !== null) {
    const length = end - start;
    if (length <= 0) {
      problems.push({ code: "time", field: "ends_at" });
    } else if (length > MAX_DURATION_MINUTES) {
      problems.push({ code: "duration", field: "ends_at" });
    }
  }

  for (const field of COPY_FIELDS) {
    if (!textOk(form.copy[field], 0, copyMaxLength(field))) {
      problems.push({ code: "copy", field: `copy.${field}` });
    }
  }
  return problems;
}

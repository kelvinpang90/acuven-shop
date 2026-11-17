import type {
  AdminPromotion,
  AdminPromotionRule,
  PromotionCopy,
  PromotionDecoration,
  PromotionInput,
  PromotionRuleInput,
  PromotionScopeProduct,
  PromotionTier,
} from "../api/adminPromotions";

// 后台促销活动 A12 编辑区的表单（docs/UX.md 0.20 A12 的「时间」「规则」「文案」三条与「提交前页面先按 DESIGN 1.23 第 1–3、12 条的取值范围逐项检查」，
// SHOP-TASK-281 与 SHOP-TASK-282）：表单的类型、新表单与新规则的初值、读取结果与提交内容之间的换算、是否有未保存修改，以及内部名称、时间、规则与文案的
// 提交前检查。只是页面提交前的检查与换算；取值、核对、版本与审计在服务端（app/services/admin_promotions.py 的 check_promotion_input）。

// 内部名称的字符数上限，与 app/models/promotion.py 的 NAME_MAX_LENGTH 相同。
export const NAME_MAX_LENGTH = 100;
// 文案的字符数上限，与 app/services/admin_promotions.py 的 COPY_MAX_LENGTHS 相同（按文案字段名去掉语言后缀取值）。
export const COPY_MAX_LENGTHS = { title: 40, subtitle: 80, badge: 12, strip: 60, poster_alt: 150 } as const;
// 活动时长上限（MAX_DURATION，92 天），以分钟计。
export const MAX_DURATION_MINUTES = 92 * 24 * 60;

// 规则参数的取值范围，与 app/models/promotion.py 的同名常量相同（金额以整仙计）。
export const PERCENT_MIN = 1;
export const DISCOUNT_PERCENT_MAX = 90;
export const GET_PERCENT_MAX = 100;
export const ITEM_AMOUNT_MAX_SEN = 50_000_000;
export const QUANTITY_MIN = 1;
export const QUANTITY_MAX = 10;
export const THRESHOLD_MAX_SEN = 99_999_999;
export const SPEND_TIERS_MAX = 5;
// 规则条数与范围的上限，与 app/services/admin_promotions.py 的同名常量相同。
export const ITEM_RULES_MAX = 20;
export const SCOPE_PRODUCTS_MAX = 200;
export const SCOPE_CATEGORIES_MAX = 30;

// 规则的类别（app/models/promotion.py 的 RULE_KINDS），顺序同 UX 0.20 A12 的 [admin.rule_add]；前两类为商品级规则（ITEM_KINDS）。
export const RULE_KINDS = ["item_discount", "buy_x_get_y", "spend_discount", "free_shipping"] as const;
export type RuleKind = (typeof RULE_KINDS)[number];
// 每件直降与满额的方式（MODE_PERCENT、MODE_AMOUNT）。
export type DiscountMode = "percent" | "amount";

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

// 满额的一档：门槛为 RM 文字；优惠按规则的方式为 RM 文字（满额减）或整数文字（满额折的百分比）。
export interface PromotionFormTier {
  threshold: string;
  discount: string;
}

// 每条规则都有的项：key 只在页面内区分规则（读取时按排序为 0、1、2…，新加的规则取 nextRuleKey）；读回的规则另带 rule_id，提交时不带进。
interface FormRuleBase {
  key: number;
  rule_id?: number;
}

// 商品级规则的范围：所选商品与分类的 ID，products 为显示用的所选商品（ID 与名称，读取时与 product_ids 一一对应，之后由页面同步），不提交。
interface FormRuleScope {
  product_ids: number[];
  products: PromotionScopeProduct[];
  category_ids: number[];
}

// 每件直降：discount 按方式为整数文字（百分比）或 RM 文字（金额）。
export interface ItemDiscountFormRule extends FormRuleBase, FormRuleScope {
  kind: "item_discount";
  discount_mode: DiscountMode;
  discount: string;
}

// 买 X 享 Y：三项都为整数文字。
export interface BuyXGetYFormRule extends FormRuleBase, FormRuleScope {
  kind: "buy_x_get_y";
  buy_quantity: string;
  get_quantity: string;
  get_percent: string;
}

// 满额减或满额折：方式与各档。
export interface SpendDiscountFormRule extends FormRuleBase {
  kind: "spend_discount";
  discount_mode: DiscountMode;
  tiers: PromotionFormTier[];
}

// 满额免运费：门槛为 RM 文字。
export interface FreeShippingFormRule extends FormRuleBase {
  kind: "free_shipping";
  threshold: string;
}

// 表单里的一条规则：按类别只有该类用到的文字输入，商品级规则另有范围。
export type PromotionFormRule = ItemDiscountFormRule | BuyXGetYFormRule | SpendDiscountFormRule | FreeShippingFormRule;
export type ItemFormRule = ItemDiscountFormRule | BuyXGetYFormRule;

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

// 提交前检查的一个问题：code 与 field 的写法同服务端的 PromotionProblem（name、starts_at、ends_at、rules.N.<参数>、rules.N.tiers.M.<参数>、
// rules.N.product_ids、rules.N.category_ids、rules、copy.<字段>；N 与 M 从 1 起）。
export interface PromotionFormProblem {
  code: "name" | "time" | "duration" | "rule" | "scope" | "tiers" | "copy";
  field: string;
}

// 整数文字：只认 ASCII 数字（不带符号、空白与小数点，前导零照收）。
const INTEGER_TEXT = /^[0-9]+$/;
// RM 文字：同 AdminShippingPage 的运费输入（feeSen），一到九位数字，可再跟小数点与一到两位数字。
const RM_TEXT = /^(\d{1,9})(?:\.(\d{1,2}))?$/;

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

// 整数文字换成整数：不合 INTEGER_TEXT、或位数多到不是安全整数为 null。取值范围由 formProblems 查。
export function integerOf(text: string): number | null {
  if (!INTEGER_TEXT.test(text)) {
    return null;
  }
  const value = Number(text);
  return Number.isSafeInteger(value) ? value : null;
}

// RM 文字换成整仙（同 AdminShippingPage 的 feeSen）：整数部分乘 100 加补足两位的小数部分（不经浮点数相乘）；格式不对为 null。
export function rmSen(text: string): number | null {
  const match = RM_TEXT.exec(text);
  if (match === null) {
    return null;
  }
  const [, whole = "", fraction = ""] = match;
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

// 整仙写成不带 RM 前缀的两位小数文字（同 AdminShippingPage 的 feeText，整数运算：0 为 0.00，5 为 0.05，800 为 8.00）。
export function rmText(sen: number): string {
  const cents = sen % 100;
  return `${String((sen - cents) / 100)}.${String(cents).padStart(2, "0")}`;
}

function integerText(value: number | null): string {
  return value === null ? "" : String(value);
}

function senText(value: number | null): string {
  return value === null ? "" : rmText(value);
}

// 新规则的初值：每件直降为百分比方式、数值空；买 X 享 Y 三项空；满额为满额减方式、一档（门槛与优惠都空）；免运费门槛空；商品级规则的范围为空。
export function newPromotionRule(kind: RuleKind, key: number): PromotionFormRule {
  switch (kind) {
    case "item_discount":
      return { key, kind, discount_mode: "percent", discount: "", product_ids: [], products: [], category_ids: [] };
    case "buy_x_get_y":
      return { key, kind, buy_quantity: "", get_quantity: "", get_percent: "", product_ids: [], products: [], category_ids: [] };
    case "spend_discount":
      return { key, kind, discount_mode: "amount", tiers: [{ threshold: "", discount: "" }] };
    case "free_shipping":
      return { key, kind, threshold: "" };
  }
}

// 新加规则的 key：比表单里现有规则的 key 都大（没有规则时为 0）。
export function nextRuleKey(form: PromotionForm): number {
  return form.rules.reduce((next, rule) => Math.max(next, rule.key + 1), 0);
}

function scopeCopy(rule: FormRuleScope): FormRuleScope {
  return {
    product_ids: [...rule.product_ids],
    products: rule.products.map((product) => ({ ...product })),
    category_ids: [...rule.category_ids],
  };
}

// 切换每件直降的百分比与金额方式、满额的减与折：方式改变时清空优惠值文字（满额为各档的优惠，门槛保留），方式不变时照原值；其余各项照原值，返回新对象。
export function switchDiscountMode(rule: ItemDiscountFormRule, mode: DiscountMode): ItemDiscountFormRule;
export function switchDiscountMode(rule: SpendDiscountFormRule, mode: DiscountMode): SpendDiscountFormRule;
export function switchDiscountMode(
  rule: ItemDiscountFormRule | SpendDiscountFormRule,
  mode: DiscountMode,
): ItemDiscountFormRule | SpendDiscountFormRule {
  const changed = mode !== rule.discount_mode;
  if (rule.kind === "item_discount") {
    return { ...rule, ...scopeCopy(rule), discount_mode: mode, discount: changed ? "" : rule.discount };
  }
  return {
    ...rule,
    discount_mode: mode,
    tiers: rule.tiers.map((tier) => ({ threshold: tier.threshold, discount: changed ? "" : tier.discount })),
  };
}

// 读回的一条规则 → 表单：数值参数按类别与方式写成整数文字或 RM 文字（null 读成空串），方式不是 percent 与 amount 之一时取新规则的初值方式；
// 服务端只存四类规则，其他类别抛 TypeError。
function formRule(rule: AdminPromotionRule, key: number): PromotionFormRule {
  const base = { key, rule_id: rule.rule_id };
  const scope = { product_ids: rule.product_ids, products: rule.products, category_ids: rule.category_ids };
  switch (rule.kind) {
    case "item_discount": {
      const discount_mode = rule.discount_mode === "amount" ? "amount" : "percent";
      const discount = discount_mode === "percent" ? integerText(rule.percent) : senText(rule.amount_sen);
      return { ...base, kind: "item_discount", discount_mode, discount, ...scopeCopy(scope) };
    }
    case "buy_x_get_y":
      return {
        ...base,
        kind: "buy_x_get_y",
        buy_quantity: integerText(rule.buy_quantity),
        get_quantity: integerText(rule.get_quantity),
        get_percent: integerText(rule.get_percent),
        ...scopeCopy(scope),
      };
    case "spend_discount": {
      const discount_mode = rule.discount_mode === "percent" ? "percent" : "amount";
      const tiers = rule.tiers.map((tier) => ({
        threshold: rmText(tier.threshold_sen),
        discount: discount_mode === "percent" ? integerText(tier.percent) : senText(tier.amount_sen),
      }));
      return { ...base, kind: "spend_discount", discount_mode, tiers };
    }
    case "free_shipping":
      return { ...base, kind: "free_shipping", threshold: senText(rule.threshold_sen) };
    default:
      throw new TypeError(`unknown rule kind ${rule.kind}`);
  }
}

// 提交的一条规则：键序同 RuleIn，没有给出的参数为 null，范围与满额档没有给出时为空。
function ruleBody(
  kind: RuleKind,
  params: Partial<Omit<PromotionRuleInput, "kind" | "product_ids" | "category_ids" | "tiers">>,
  product_ids: number[] = [],
  category_ids: number[] = [],
  tiers: PromotionTier[] = [],
): PromotionRuleInput {
  return {
    kind,
    discount_mode: params.discount_mode ?? null,
    percent: params.percent ?? null,
    amount_sen: params.amount_sen ?? null,
    buy_quantity: params.buy_quantity ?? null,
    get_quantity: params.get_quantity ?? null,
    get_percent: params.get_percent ?? null,
    threshold_sen: params.threshold_sen ?? null,
    product_ids,
    category_ids,
    tiers,
  };
}

// 表单里的一条规则 → 提交内容：按类别只填该类用到的参数（照 check_rule_params 的 _KIND_PARAMS 与方式：直降按方式只填 percent 或 amount_sen，
// 满额各档按方式只填 percent 或 amount_sen），其余为 null，整单级规则的范围为空、满额以外的规则没有档；不提交 rule_id、key 与 products。
// 文字换不成整数或整仙的参数为 null（formProblems 同时报它不合格，页面不提交）。
function ruleInput(rule: PromotionFormRule): PromotionRuleInput {
  switch (rule.kind) {
    case "item_discount": {
      const percent = rule.discount_mode === "percent" ? integerOf(rule.discount) : null;
      const amount_sen = rule.discount_mode === "amount" ? rmSen(rule.discount) : null;
      return ruleBody(rule.kind, { discount_mode: rule.discount_mode, percent, amount_sen }, [...rule.product_ids], [...rule.category_ids]);
    }
    case "buy_x_get_y":
      return ruleBody(
        rule.kind,
        { buy_quantity: integerOf(rule.buy_quantity), get_quantity: integerOf(rule.get_quantity), get_percent: integerOf(rule.get_percent) },
        [...rule.product_ids],
        [...rule.category_ids],
      );
    case "spend_discount": {
      const mode = rule.discount_mode;
      const tiers = rule.tiers.map((tier) => ({
        // PromotionTier（后端 TierIn）的门槛不收 null；换不成整仙时照落实写法仍为 null，只供 isDirty 比较与 formProblems 检查，页面不会提交。
        threshold_sen: rmSen(tier.threshold) as number,
        amount_sen: mode === "amount" ? rmSen(tier.discount) : null,
        percent: mode === "percent" ? integerOf(tier.discount) : null,
      }));
      return ruleBody(rule.kind, { discount_mode: mode }, [], [], tiers);
    }
    case "free_shipping":
      return ruleBody(rule.kind, { threshold_sen: rmSen(rule.threshold) });
  }
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

// 读取结果 → 表单：时间与文案的 null 读成空串，规则按 formRule 换成可编辑的表单（key 按排序为 0、1、2…，带 rule_id 与 products），装饰照读取的值复制，
// 不与读取结果共用对象。
export function formFromPromotion(promotion: AdminPromotion): PromotionForm {
  return {
    name: promotion.name,
    starts_at: promotion.starts_at ?? "",
    ends_at: promotion.ends_at ?? "",
    coupon_combinable: promotion.coupon_combinable,
    points_combinable: promotion.points_combinable,
    rules: promotion.rules.map(formRule),
    decoration: { ...promotion.decoration },
    copy: copyOf((field) => promotion.copy[field] ?? ""),
  };
}

// 表单 → 提交内容：时间空串提交 null；文案 pyStrip 后为空提交 null，否则原样提交；名称原样提交；规则按 ruleInput 换算表单里当前的值；装饰提交表单里当前的值。
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

// 同服务端 _int_in：整数且在范围内（含两端）；换不成整数或整仙的参数为 null，不合格。
function intIn(value: number | null, minimum: number, maximum: number): boolean {
  return value !== null && Number.isSafeInteger(value) && minimum <= value && value <= maximum;
}

// 同服务端 _tier_problems（方式在表单里只能是 percent 或 amount）：1 至 SPEND_TIERS_MAX 档（否则 tiers）；逐档查门槛 1 至 THRESHOLD_MAX_SEN，
// 再查优惠（满额折 PERCENT_MIN 至 DISCOUNT_PERCENT_MAX；满额减 1 至门槛减 1，门槛不合格时至 THRESHOLD_MAX_SEN 减 1）；之后门槛、优惠各自逐档严格递增
// （只比相邻两档都合格的，问题报在后一档）。问题码都为 tiers。另一种方式的优惠由 ruleInput 填 null，不会出现服务端的 other_field 问题。
function tierProblems(mode: DiscountMode, tiers: readonly PromotionTier[], prefix: string): PromotionFormProblem[] {
  const problems: PromotionFormProblem[] = [];
  if (tiers.length < 1 || tiers.length > SPEND_TIERS_MAX) {
    problems.push({ code: "tiers", field: `${prefix}tiers` });
  }
  const discountField = mode === "percent" ? "percent" : "amount_sen";
  const thresholds: (number | null)[] = [];
  const discounts: (number | null)[] = [];
  for (const [index, tier] of tiers.entries()) {
    const tierPrefix = `${prefix}tiers.${String(index + 1)}.`;
    const threshold = intIn(tier.threshold_sen, 1, THRESHOLD_MAX_SEN) ? tier.threshold_sen : null;
    if (threshold === null) {
      problems.push({ code: "tiers", field: `${tierPrefix}threshold_sen` });
    }
    thresholds.push(threshold);
    const discount = tier[discountField];
    const valid = mode === "percent" ? intIn(discount, PERCENT_MIN, DISCOUNT_PERCENT_MAX) : intIn(discount, 1, (threshold ?? THRESHOLD_MAX_SEN) - 1);
    if (!valid) {
      problems.push({ code: "tiers", field: `${tierPrefix}${discountField}` });
    }
    discounts.push(valid ? discount : null);
  }
  for (const [values, name] of [
    [thresholds, "threshold_sen"],
    [discounts, discountField],
  ] as const) {
    for (let number = 2; number <= values.length; number += 1) {
      const previous = values[number - 2];
      const current = values[number - 1];
      if (typeof previous === "number" && typeof current === "number" && current <= previous) {
        problems.push({ code: "tiers", field: `${prefix}tiers.${String(number)}.${name}` });
      }
    }
  }
  return problems;
}

// 同服务端 _ids_ok：至多 maximum 个，都为正整数，不重复。
function idsOk(ids: readonly number[], maximum: number): boolean {
  return ids.length <= maximum && ids.every((id) => Number.isSafeInteger(id) && id >= 1) && new Set(ids).size === ids.length;
}

// 同服务端 check_rule_params 与 check_rule_scope（类别在表单里只能是四类之一，不属于该类的参数由 ruleInput 填 null、范围与档为空，不会出现那几种问题），
// 按参数、范围的顺序；field 加上 prefix（rules.N.）。
// - 每件直降：百分比方式 percent 为 PERCENT_MIN 至 DISCOUNT_PERCENT_MAX，金额方式 amount_sen 为 1 至 ITEM_AMOUNT_MAX_SEN（问题码 rule）。
// - 买 X 享 Y：buy_quantity 与 get_quantity 为 QUANTITY_MIN 至 QUANTITY_MAX，get_percent 为 PERCENT_MIN 至 GET_PERCENT_MAX（问题码 rule）。
// - 满额：各档见 tierProblems（问题码 tiers）。
// - 免运费：threshold_sen 为 0 至 THRESHOLD_MAX_SEN（问题码 rule）。
// - 商品级规则的范围（问题码 scope）：product_ids 与 category_ids 各按 idsOk（至多 SCOPE_PRODUCTS_MAX 件、SCOPE_CATEGORIES_MAX 个），两项都空时再报 product_ids。
function ruleProblems(rule: PromotionFormRule, prefix: string): PromotionFormProblem[] {
  const input = ruleInput(rule);
  const problems: PromotionFormProblem[] = [];
  const requireInt = (name: "percent" | "amount_sen" | "buy_quantity" | "get_quantity" | "get_percent" | "threshold_sen", minimum: number, maximum: number) => {
    if (!intIn(input[name], minimum, maximum)) {
      problems.push({ code: "rule", field: prefix + name });
    }
  };
  switch (rule.kind) {
    case "item_discount":
      if (rule.discount_mode === "percent") {
        requireInt("percent", PERCENT_MIN, DISCOUNT_PERCENT_MAX);
      } else {
        requireInt("amount_sen", 1, ITEM_AMOUNT_MAX_SEN);
      }
      break;
    case "buy_x_get_y":
      requireInt("buy_quantity", QUANTITY_MIN, QUANTITY_MAX);
      requireInt("get_quantity", QUANTITY_MIN, QUANTITY_MAX);
      requireInt("get_percent", PERCENT_MIN, GET_PERCENT_MAX);
      break;
    case "spend_discount":
      problems.push(...tierProblems(rule.discount_mode, input.tiers, prefix));
      break;
    case "free_shipping":
      requireInt("threshold_sen", 0, THRESHOLD_MAX_SEN);
      break;
  }
  if (rule.kind === "item_discount" || rule.kind === "buy_x_get_y") {
    if (!idsOk(rule.product_ids, SCOPE_PRODUCTS_MAX)) {
      problems.push({ code: "scope", field: `${prefix}product_ids` });
    }
    if (!idsOk(rule.category_ids, SCOPE_CATEGORIES_MAX)) {
      problems.push({ code: "scope", field: `${prefix}category_ids` });
    }
    if (rule.product_ids.length === 0 && rule.category_ids.length === 0) {
      problems.push({ code: "scope", field: `${prefix}product_ids` });
    }
  }
  return problems;
}

// 同服务端 _rule_count_problems：商品级规则多于 ITEM_RULES_MAX 条，满额、免运费各多于一条，各报一个 rules（问题码 rule）。
function ruleCountProblems(rules: readonly PromotionFormRule[]): PromotionFormProblem[] {
  const count = (test: (rule: PromotionFormRule) => boolean) => rules.filter(test).length;
  const problems: PromotionFormProblem[] = [];
  if (count((rule) => rule.kind === "item_discount" || rule.kind === "buy_x_get_y") > ITEM_RULES_MAX) {
    problems.push({ code: "rule", field: "rules" });
  }
  for (const kind of ["spend_discount", "free_shipping"] as const) {
    if (count((rule) => rule.kind === kind) > 1) {
      problems.push({ code: "rule", field: "rules" });
    }
  }
  return problems;
}

// 提交前检查（同服务端 check_promotion_input 里不查库、不涉及装饰的部分），按名称、开始、结束、各条规则、规则条数、文案的顺序列出问题：
// - 名称：不含换行与控制字符，pyStrip 后 1 至 NAME_MAX_LENGTH 个字符。
// - 时间：空串不查；填了须是服务端收得下的「YYYY-MM-DDTHH:MM」；两端都合格时结束晚于开始（否则 time）且不超过 92 天（否则 duration），问题报在 ends_at。
// - 规则：逐条按 ruleProblems，field 为 rules.N.…（N 从 1 起）；再按 ruleCountProblems 查条数。
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

  for (const [index, rule] of form.rules.entries()) {
    problems.push(...ruleProblems(rule, `rules.${String(index + 1)}.`));
  }
  problems.push(...ruleCountProblems(form.rules));

  for (const field of COPY_FIELDS) {
    if (!textOk(form.copy[field], 0, copyMaxLength(field))) {
      problems.push({ code: "copy", field: `copy.${field}` });
    }
  }
  return problems;
}

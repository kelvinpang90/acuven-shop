import { describe, expect, it } from "vitest";

import type { AdminPromotion, AdminPromotionRule, PromotionCopy, PromotionRuleInput, PromotionScopeProduct } from "../api/adminPromotions";
import {
  COPY_FIELDS,
  copyLength,
  copyMaxLength,
  DISCOUNT_PERCENT_MAX,
  formFromPromotion,
  formProblems,
  GET_PERCENT_MAX,
  integerOf,
  isDirty,
  ITEM_AMOUNT_MAX_SEN,
  ITEM_RULES_MAX,
  newPromotionForm,
  newPromotionRule,
  nextRuleKey,
  PERCENT_MIN,
  promotionInput,
  pyStrip,
  QUANTITY_MAX,
  QUANTITY_MIN,
  rmSen,
  rmText,
  RULE_KINDS,
  SCOPE_CATEGORIES_MAX,
  SCOPE_PRODUCTS_MAX,
  SPEND_TIERS_MAX,
  switchDiscountMode,
  THRESHOLD_MAX_SEN,
  type CopyField,
  type ItemFormRule,
  type PromotionForm,
  type PromotionFormRule,
  type PromotionFormTier,
  type RuleKind,
} from "./promotionForm";

const ITEM_RULE: AdminPromotionRule = {
  rule_id: 11,
  kind: "item_discount",
  discount_mode: "percent",
  percent: 20,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [3, 5],
  category_ids: [2],
  tiers: [],
  products: [
    { id: 3, slug: "kuih", name_en: "Kuih" },
    { id: 5, slug: "dodol", name_en: null },
  ],
};

const SPEND_RULE: AdminPromotionRule = {
  rule_id: 12,
  kind: "spend_discount",
  discount_mode: "amount",
  percent: null,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [],
  category_ids: [],
  tiers: [
    { threshold_sen: 10000, amount_sen: 1000, percent: null },
    { threshold_sen: 20000, amount_sen: 2500, percent: null },
  ],
  products: [],
};

const COPY: PromotionCopy = {
  title_en: "Raya Sale",
  title_zh: "开斋节特卖",
  title_ms: null,
  subtitle_en: "Up to 30% off",
  subtitle_zh: null,
  subtitle_ms: null,
  badge_en: "Raya",
  badge_zh: null,
  badge_ms: null,
  strip_en: "Free shipping over RM 100",
  strip_zh: null,
  strip_ms: "\u{feff}",
  poster_alt_en: "Green festive poster",
  poster_alt_zh: null,
  poster_alt_ms: null,
};

const PROMOTION: AdminPromotion = {
  id: 7,
  version: 3,
  state: "draft",
  ended_early_at: null,
  name: "Raya 2026",
  starts_at: "2026-03-20T00:00",
  ends_at: "2026-04-05T23:59",
  coupon_combinable: true,
  points_combinable: false,
  rules: [ITEM_RULE, SPEND_RULE],
  decoration: {
    pack: "raya",
    palette: "raya",
    ambient: "none",
    home_poster: "centered",
    list_strip: "ticket",
    card_badge: "seal",
    detail_ribbon: "panel",
    cart_progress: "steps",
    poster_mode: "image",
  },
  copy: COPY,
  poster_desktop_url: "/api/promotion-posters/a.webp",
  poster_phone_url: null,
};

const COPY_LIMITS: Record<string, number> = { title: 40, subtitle: 80, badge: 12, strip: 60, poster_alt: 150 };

// 读回的规则去掉 rule_id 与 products 后的提交内容。
function submitted(rule: AdminPromotionRule): PromotionRuleInput {
  const { kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent, threshold_sen, product_ids, category_ids, tiers } = rule;
  return { kind, discount_mode, percent, amount_sen, buy_quantity, get_quantity, get_percent, threshold_sen, product_ids, category_ids, tiers };
}

// 表单里的一条规则；products 在各类别上都可读（整单级规则没有，为 undefined），供断言直接取值。
function ruleAt(form: PromotionForm, index: number): PromotionFormRule & { products?: PromotionScopeProduct[] } {
  const rule = form.rules[index];
  if (rule === undefined) {
    throw new Error(`no rule ${String(index)}`);
  }
  return rule;
}

// 表单里的一条商品级规则。
function itemRuleAt(form: PromotionForm, index: number): ItemFormRule {
  const rule = ruleAt(form, index);
  if (rule.kind !== "item_discount" && rule.kind !== "buy_x_get_y") {
    throw new Error(`rule ${String(index)} is not item level`);
  }
  return rule;
}

// 满额规则的一档。
function tierAt(rule: PromotionFormRule, index: number): PromotionFormTier {
  if (rule.kind !== "spend_discount") {
    throw new Error("not a spend rule");
  }
  const tier = rule.tiers[index];
  if (tier === undefined) {
    throw new Error(`no tier ${String(index)}`);
  }
  return tier;
}

type FormRuleOf<K extends RuleKind> = Extract<PromotionFormRule, { kind: K }>;

// 某一类的新规则（newPromotionRule 的初值），类型收窄到该类。
function newRule<K extends RuleKind>(kind: K, key = 0): FormRuleOf<K> {
  const rule = newPromotionRule(kind, key);
  if (rule.kind !== kind) {
    throw new Error(`expected ${kind}`);
  }
  return rule as FormRuleOf<K>;
}

// 名称已填、时间与文案全空的合格表单，各条检查测试在它上面只改一项。
function validForm(changes: Partial<PromotionForm> = {}): PromotionForm {
  return { ...newPromotionForm(), name: "Sale", ...changes };
}

function withCopy(field: CopyField, text: string): PromotionForm {
  const form = validForm();
  return { ...form, copy: { ...form.copy, [field]: text } };
}

describe("new form", () => {
  // 落实写法（1）newPromotionForm：名称与时间为空、两个开关不勾、没有规则、装饰为 pack generic、palette theme、各装饰位取
  // docs/design/tokens/promotions.json 的第一个变体（ambient garland、home_poster split、list_strip band、card_badge tag、detail_ribbon ribbon、
  // cart_progress bar）、poster_mode template，文案全空。
  it("has the documented defaults", () => {
    const form = newPromotionForm();
    expect(form).toEqual({
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
      copy: Object.fromEntries(COPY_FIELDS.map((field) => [field, ""])),
    });
    expect(Object.keys(form.copy)).toHaveLength(15);
  });

  // 同上：每次都是新的对象，页面改一个新表单不影响下一个。
  it("returns fresh objects each time", () => {
    const first = newPromotionForm();
    first.rules.push(newPromotionRule("item_discount", 0));
    first.decoration.pack = "cny";
    first.copy.title_en = "x";
    const second = newPromotionForm();
    expect(second.rules).toEqual([]);
    expect(second.decoration.pack).toBe("generic");
    expect(second.copy.title_en).toBe("");
  });

  // 落实写法（1）「时间空串提交 null；文案 pyStrip 后为空提交 null」：新表单提交时两个时间与十五项文案都为 null。
  it("submits null times and copy", () => {
    const input = promotionInput(newPromotionForm());
    expect(input.starts_at).toBeNull();
    expect(input.ends_at).toBeNull();
    expect(input.copy).toEqual(Object.fromEntries(COPY_FIELDS.map((field) => [field, null])));
    expect(Object.keys(input.copy)).toEqual([...COPY_FIELDS]);
  });
});

describe("round trip", () => {
  // 验收「往返」：读取结果经 formFromPromotion 再经 promotionInput 得到的 decoration、copy 与读取时相同，rules 与读取时去掉 rule_id 与 products 后相同；
  // 名称、时间与两个开关原样回到提交内容（服务端 PromotionDetail.as_input「原样提交即没有改动」）。
  it("submits what was read", () => {
    const input = promotionInput(formFromPromotion(PROMOTION));
    expect(input).toEqual({
      name: PROMOTION.name,
      starts_at: PROMOTION.starts_at,
      ends_at: PROMOTION.ends_at,
      coupon_combinable: true,
      points_combinable: false,
      rules: PROMOTION.rules.map(submitted),
      decoration: PROMOTION.decoration,
      copy: PROMOTION.copy,
    });
    // 只有 U+FEFF 的文案：Python 的 str.strip() 不去掉它，服务端照存，所以原样保留、不变成 null。
    expect(input.copy.strip_ms).toBe("\u{feff}");
  });

  // 落实写法（1）「copy 为 15 个文案字段的字符串，null 读成空串」与「starts_at 与 ends_at 为空即未填」。
  it("reads null copy and times as empty strings", () => {
    const form = formFromPromotion({ ...PROMOTION, starts_at: null, ends_at: null });
    expect(form.starts_at).toBe("");
    expect(form.ends_at).toBe("");
    expect(form.copy.title_ms).toBe("");
    expect(form.copy.title_zh).toBe("开斋节特卖");
    expect(form.copy.strip_ms).toBe("\u{feff}");
    const input = promotionInput(form);
    expect(input.starts_at).toBeNull();
    expect(input.ends_at).toBeNull();
  });

  // 落实写法（1）「规则去掉 rule_id 与 products，tiers 每项恰为三个键」（后端 RuleIn 与 TierIn 不许多出字段）；表单里的规则仍带 rule_id 与 products 供页面显示。
  it("submits rules and tiers with exactly the input keys", () => {
    const form = formFromPromotion(PROMOTION);
    expect(ruleAt(form, 0).rule_id).toBe(11);
    expect(ruleAt(form, 0).products).toEqual(ITEM_RULE.products);
    Object.assign(tierAt(ruleAt(form, 1), 0), { extra: 1 });
    Object.assign(ruleAt(form, 1), { extra: 1 });
    const input = promotionInput(form);
    for (const rule of input.rules) {
      expect(Object.keys(rule)).toEqual([
        "kind",
        "discount_mode",
        "percent",
        "amount_sen",
        "buy_quantity",
        "get_quantity",
        "get_percent",
        "threshold_sen",
        "product_ids",
        "category_ids",
        "tiers",
      ]);
      for (const tier of rule.tiers) {
        expect(Object.keys(tier)).toEqual(["threshold_sen", "amount_sen", "percent"]);
      }
    }
  });

  // 落实写法（1）「rules 与 decoration 提交表单里当前的值」：新加的规则（没有 rule_id 与 products）、调整后的排序与改过的装饰照表单提交。
  it("submits the current rules and decoration of the form", () => {
    const form = formFromPromotion(PROMOTION);
    form.rules = [ruleAt(form, 1), { ...newRule("item_discount", nextRuleKey(form)), discount: "20", product_ids: [9], category_ids: [2] }];
    form.decoration = { ...form.decoration, pack: "cny", ambient: "fall" };
    const input = promotionInput(form);
    expect(input.rules).toEqual([submitted(SPEND_RULE), { ...submitted(ITEM_RULE), product_ids: [9] }]);
    expect(input.decoration).toEqual({ ...PROMOTION.decoration, pack: "cny", ambient: "fall" });
  });

  // 同上：表单不与读取结果共用对象，改表单不改读取结果（读取结果之后还作 isDirty 的基准）。
  it("does not share objects with the read result", () => {
    const form = formFromPromotion(PROMOTION);
    itemRuleAt(form, 0).product_ids.push(99);
    ruleAt(form, 0).products?.push({ id: 99, slug: "x", name_en: null });
    tierAt(ruleAt(form, 1), 0).discount = "0.01";
    form.decoration.pack = "cny";
    expect(ruleAt(form, 0).products).toHaveLength(3);
    expect(ITEM_RULE.product_ids).toEqual([3, 5]);
    expect(ITEM_RULE.products).toHaveLength(2);
    expect(SPEND_RULE.tiers[0]?.amount_sen).toBe(1000);
    expect(PROMOTION.decoration.pack).toBe("raya");
  });
});

describe("pyStrip and copyLength", () => {
  // 落实写法（1）「pyStrip 的空白集同 Python 的 str.strip()：JS 正则 \s 去掉 U+FEFF，再加 U+001C 至 U+001F 与 U+0085，与服务端 clean_text 一致」：
  // 逐个检查基本多文种平面的全部码点。
  it("strips exactly the Python whitespace set", () => {
    const extra = new Set([0x1c, 0x1d, 0x1e, 0x1f, 0x85]);
    const mismatched: string[] = [];
    for (let code = 0; code <= 0xffff; code += 1) {
      const ch = String.fromCharCode(code);
      const whitespace = (/\s/.test(ch) && code !== 0xfeff) || extra.has(code);
      if ((pyStrip(ch) === "") !== whitespace) {
        mismatched.push(code.toString(16));
      }
    }
    expect(mismatched).toEqual([]);
  });

  // 同上：只去首尾，中间的空白保留；U+FEFF 与零宽空格 U+200B 不算空白（Python 的 str.isspace() 对它们为假）。
  it("strips only the ends", () => {
    expect(pyStrip(" \t\u{3000}a b\u{a0}\u0085\u001c")).toBe("a b");
    expect(pyStrip("\u{2028}\u{2029}\u{202f}\u{205f}\u{1680}\u{2000}\u{200a}x\u000b\u000c\r\n")).toBe("x");
    expect(pyStrip("\u{feff} a \u{feff}")).toBe("\u{feff} a \u{feff}");
    expect(pyStrip("\u{200b}")).toBe("\u{200b}");
    expect(pyStrip("")).toBe("");
  });

  // 落实写法（2）「另导出 copyLength(文字)（pyStrip 后的码点数）」：服务端 len(value.strip()) 按码点计数，emoji 与汉字各算一个。
  it("counts code points after stripping", () => {
    expect(copyLength("  ab  ")).toBe(2);
    expect(copyLength("😀🎉")).toBe(2);
    expect(copyLength("开斋节")).toBe(3);
    expect(copyLength("\u{a0}\u{3000} ")).toBe(0);
    expect(copyLength("\u{feff}")).toBe(1);
  });
});

describe("submitted values", () => {
  // 落实写法（1）「时间空串提交 null」：非空的时间原样提交（格式由 formProblems 查）。
  it("submits times as typed", () => {
    const input = promotionInput(validForm({ starts_at: "2026-03-20T08:00", ends_at: "" }));
    expect(input.starts_at).toBe("2026-03-20T08:00");
    expect(input.ends_at).toBeNull();
  });

  // 落实写法（1）「文案以 pyStrip 去掉首尾空白后为空提交 null，否则原样提交」与 UX 0.20 A12「文案：草稿可以不填英文标题」。
  it.each([
    ["", null],
    ["   ", null],
    ["\u{3000}\u{a0}", null],
    ["\u001c\u0085", null],
    [" Raya ", " Raya "],
    ["\u{feff}", "\u{feff}"],
    ["开斋节", "开斋节"],
  ])("submits copy %j as %j", (text, expected) => {
    expect(promotionInput(withCopy("subtitle_zh", text)).copy.subtitle_zh).toBe(expected);
  });

  // 落实写法（1）「名称原样提交」：去空白由服务端 check_promotion_input 做。
  it("submits the name as typed", () => {
    expect(promotionInput(validForm({ name: "  Raya  " })).name).toBe("  Raya  ");
  });
});

describe("name check", () => {
  // check_promotion_input「内部名称去掉首尾空白后 1–100 个字符」（NAME_MAX_LENGTH），按码点计数；新表单的空名称不合格。
  it.each([
    ["a", true],
    ["a".repeat(100), true],
    [`\u{a0}${"a".repeat(100)}\u{3000}`, true],
    ["😀".repeat(100), true],
    ["", false],
    ["   ", false],
    ["\u{3000}", false],
    ["a".repeat(101), false],
    ["😀".repeat(101), false],
  ])("name %j is valid: %s", (name, valid) => {
    expect(formProblems(validForm({ name }))).toEqual(valid ? [] : [{ code: "name", field: "name" }]);
  });

  // 同上，新表单的名称为空。
  it("reports the empty name of a new form", () => {
    expect(formProblems(newPromotionForm())).toEqual([{ code: "name", field: "name" }]);
  });

  // check_promotion_input「字符规则同文案」与 has_forbidden_char「Unicode 类别 Cc，以及 U+2028、U+2029」；_text_ok「换行与控制字符查原文」，
  // 所以首尾的 U+001C–U+001F 与 U+0085（strip 会去掉的控制字符）也不合格。零宽连接符等 Cf 类与 U+FEFF 允许。
  it.each([
    ["a\nb", false],
    ["a\r", false],
    ["\ta", false],
    ["a\u0000", false],
    ["a\u007f", false],
    ["a\u0085", false],
    ["a\u009f", false],
    ["a\u001c", false],
    ["\u001fa", false],
    ["a\u{2028}b", false],
    ["a\u{2029}b", false],
    ["\u{1f468}\u{200d}\u{1f469}", true],
    ["\u{feff}a", true],
    ["a\u{a0}b", true],
  ])("name %j is valid: %s", (name, valid) => {
    expect(formProblems(validForm({ name }))).toEqual(valid ? [] : [{ code: "name", field: "name" }]);
  });
});

describe("time check", () => {
  const startProblem = [{ code: "time", field: "starts_at" }];

  // UX 0.20 A12「草稿可以不填时间」：空串不查（服务端 _time_problems 跳过 None），只填一端也不比较。
  it("accepts empty times", () => {
    expect(formProblems(validForm({ starts_at: "", ends_at: "" }))).toEqual([]);
    expect(formProblems(validForm({ starts_at: "2026-03-20T08:00" }))).toEqual([]);
    expect(formProblems(validForm({ ends_at: "2026-03-20T08:00" }))).toEqual([]);
  });

  // to_utc「格式不对抛 ValueError」：_LOCAL_TIME 为四位年、两位月日时分，只认 ASCII 数字，不带秒与时区，整串匹配。
  it.each([
    "2026-3-20T08:00",
    "2026-03-20 08:00",
    "2026-03-20T8:00",
    "2026-03-20T08:00:00",
    "2026-03-20T08:00Z",
    "2026-03-20t08:00",
    " 2026-03-20T08:00",
    "2026-03-20T08:00\n",
    "+2026-03-20T08:00",
    "02026-03-20T08:00",
    "２０２６-03-20T08:00",
    "2026-03-20",
  ])("rejects the format %j", (value) => {
    expect(formProblems(validForm({ starts_at: value }))).toEqual(startProblem);
  });

  // to_utc「含不存在的日期与时刻」：公历闰年（能被 4 整除且不能被 100 整除，或能被 400 整除）、月份天数、24:00 与第 60 分钟、0000 年。
  it.each([
    ["1900-02-29T12:00", false],
    ["2100-02-29T12:00", false],
    ["2026-02-29T12:00", false],
    ["2000-02-29T12:00", true],
    ["2024-02-29T12:00", true],
    ["2026-02-28T12:00", true],
    ["2026-04-31T12:00", false],
    ["2026-04-30T12:00", true],
    ["2026-12-31T12:00", true],
    ["2026-13-01T12:00", false],
    ["2026-00-10T12:00", false],
    ["2026-01-00T12:00", false],
    ["2026-01-32T12:00", false],
    ["2026-03-20T24:00", false],
    ["2026-03-20T23:60", false],
    ["2026-03-20T23:59", true],
    ["2026-03-20T00:00", true],
    ["0000-06-15T12:00", false],
  ])("starts_at %j is valid: %s", (value, valid) => {
    expect(formProblems(validForm({ starts_at: value }))).toEqual(valid ? [] : startProblem);
  });

  // 落实写法（2）「年 0001 至 9999…按各分量直接判断、不经 Date.UTC（它把 0 至 99 年当作 1900 年代）」：1999 年与 0099 年合格，
  // 0004 与 0100 年的 2 月 29 日按各自的年份判断。
  it.each([
    ["1999-12-31T23:59", true],
    ["0099-06-15T12:00", true],
    ["0004-02-29T12:00", true],
    ["0100-02-29T12:00", false],
    ["9999-12-31T23:59", true],
  ])("starts_at %j is valid: %s", (value, valid) => {
    expect(formProblems(validForm({ starts_at: value }))).toEqual(valid ? [] : startProblem);
  });

  // to_utc「减 8 小时」溢出即「local time out of range」：0001-01-01T00:00 至 07:59 不合格，08:00 起合格。
  it.each([
    ["0001-01-01T00:00", false],
    ["0001-01-01T07:59", false],
    ["0001-01-01T08:00", true],
    ["0001-01-02T00:00", true],
  ])("starts_at %j is valid: %s", (value, valid) => {
    expect(formProblems(validForm({ starts_at: value }))).toEqual(valid ? [] : startProblem);
  });

  // _time_problems：结束时间格式不对的问题报在 ends_at。
  it("reports a bad end on ends_at", () => {
    expect(formProblems(validForm({ ends_at: "2026-02-29T00:00" }))).toEqual([{ code: "time", field: "ends_at" }]);
  });

  // _time_problems「length <= timedelta(0)」：结束不晚于开始（相同或更早）为 time，报在 ends_at；晚一分钟合格。
  it.each([
    ["2026-03-20T08:00", "2026-03-20T08:00", false],
    ["2026-03-20T08:00", "2026-03-20T07:59", false],
    ["2026-03-20T08:00", "2025-12-31T23:59", false],
    ["2026-03-20T08:00", "2026-03-20T08:01", true],
    ["2026-12-31T23:59", "2027-01-01T00:00", true],
  ])("from %s to %s is valid: %s", (starts_at, ends_at, valid) => {
    expect(formProblems(validForm({ starts_at, ends_at }))).toEqual(valid ? [] : [{ code: "time", field: "ends_at" }]);
  });

  // _time_problems「length > MAX_DURATION」（92 天）：恰好 92 天合格，多一分钟为 duration，报在 ends_at；跨闰日、跨世纪闰年与跨年都按公历天数。
  it.each([
    ["2026-03-20T08:00", "2026-06-20T08:00", true],
    ["2026-03-20T08:00", "2026-06-20T08:01", false],
    ["2024-02-01T00:00", "2024-05-03T00:00", true],
    ["2024-02-01T00:00", "2024-05-03T00:01", false],
    ["1999-12-01T00:00", "2000-03-02T00:00", true],
    ["1999-12-01T00:00", "2000-03-02T00:01", false],
    ["2025-12-01T10:30", "2026-03-03T10:30", true],
    ["2025-12-01T10:30", "2026-03-03T10:31", false],
    ["0001-01-01T08:00", "0001-04-03T08:00", true],
  ])("from %s to %s is within 92 days: %s", (starts_at, ends_at, valid) => {
    expect(formProblems(validForm({ starts_at, ends_at }))).toEqual(valid ? [] : [{ code: "duration", field: "ends_at" }]);
  });

  // _time_problems「len(parsed) == 2」才比较先后与时长：一端不合格时只报该端。
  it("compares only when both ends are valid", () => {
    expect(formProblems(validForm({ starts_at: "2026-03-20T24:00", ends_at: "2026-01-01T00:00" }))).toEqual(startProblem);
    expect(formProblems(validForm({ starts_at: "bad", ends_at: "bad" }))).toEqual([
      { code: "time", field: "starts_at" },
      { code: "time", field: "ends_at" },
    ]);
  });
});

describe("copy check", () => {
  // check_copy「去掉首尾空白后标题 0–40、副标题 0–80、角标短语 0–12、横条文字 0–60、替代文字 0–150 个字符」与 UX 0.20 A12「超出不能保存」：
  // 三种语言各自按上限，恰好上限合格、多一个不合格，按码点计数，首尾空白不计，空串合格。
  it.each([...COPY_FIELDS])("checks the length of %s", (field) => {
    const maximum = copyMaxLength(field);
    expect(maximum).toBe(COPY_LIMITS[field.slice(0, field.lastIndexOf("_"))]);
    const problem = [{ code: "copy", field: `copy.${field}` }];
    expect(formProblems(withCopy(field, ""))).toEqual([]);
    expect(formProblems(withCopy(field, "a".repeat(maximum)))).toEqual([]);
    expect(formProblems(withCopy(field, `\u{a0}${"字".repeat(maximum)}\u{3000}`))).toEqual([]);
    expect(formProblems(withCopy(field, "😀".repeat(maximum)))).toEqual([]);
    expect(formProblems(withCopy(field, "a".repeat(maximum + 1)))).toEqual(problem);
    expect(formProblems(withCopy(field, "😀".repeat(maximum + 1)))).toEqual(problem);
  });

  // check_copy「不含换行与控制字符」与 UX 0.20 A12「不能输入换行」：_text_ok 查原文，只有空白控制字符的文案也不合格。
  it.each(["a\nb", "\n", "a\r", "a\t", "a\u0000", "a\u0085", "\u001c", "a\u{2028}", "a\u{2029}"])("rejects copy %j", (text) => {
    expect(formProblems(withCopy("badge_ms", text))).toEqual([{ code: "copy", field: "copy.badge_ms" }]);
  });

  // has_forbidden_char「零宽连接符等 Cf 类允许（emoji 要用）」：U+200D、U+FEFF 与不换行空格允许。
  it.each(["\u{1f468}\u{200d}\u{1f469}", "\u{feff}", "a\u{a0}b"])("accepts copy %j", (text) => {
    expect(formProblems(withCopy("badge_ms", text))).toEqual([]);
  });

  // check_promotion_input「依次检查内部名称、时间…与文案」：问题按名称、开始、结束、文案字段的顺序全部列出，field 写法同服务端。
  it("lists every problem in the server order", () => {
    const form = validForm({ name: "", starts_at: "x", ends_at: "2026-02-30T00:00" });
    form.copy = { ...form.copy, poster_alt_ms: "\n", title_en: "a".repeat(41) };
    expect(formProblems(form)).toEqual([
      { code: "name", field: "name" },
      { code: "time", field: "starts_at" },
      { code: "time", field: "ends_at" },
      { code: "copy", field: "copy.title_en" },
      { code: "copy", field: "copy.poster_alt_ms" },
    ]);
  });
});

describe("isDirty", () => {
  // 落实写法（1）「isDirty(表单, 基准) 比较两者的 promotionInput 是否相同」：刚读出或新建的表单没有改动。
  it("is clean right after reading", () => {
    expect(isDirty(formFromPromotion(PROMOTION), formFromPromotion(PROMOTION))).toBe(false);
    expect(isDirty(newPromotionForm(), newPromotionForm())).toBe(false);
  });

  // 同上：名称、时间、开关、规则（含排序）、装饰与文案的任一改动都算未保存修改（UX 0.20 A12「有未保存修改时先提示 [admin.promo_save_first]」与离开确认）。
  it.each<[string, (form: PromotionForm) => void]>([
    ["name", (form) => {
      form.name = "Raya 2026 ";
    }],
    ["starts_at", (form) => {
      form.starts_at = "";
    }],
    ["ends_at", (form) => {
      form.ends_at = "2026-04-06T00:00";
    }],
    ["coupon_combinable", (form) => {
      form.coupon_combinable = false;
    }],
    ["points_combinable", (form) => {
      form.points_combinable = true;
    }],
    ["rule order", (form) => {
      form.rules.reverse();
    }],
    ["rule removed", (form) => {
      form.rules.pop();
    }],
    ["rule scope", (form) => {
      itemRuleAt(form, 0).category_ids.push(4);
    }],
    ["tier", (form) => {
      tierAt(ruleAt(form, 1), 1).discount = "20.00";
    }],
    ["decoration", (form) => {
      form.decoration.palette = "theme";
    }],
    ["copy", (form) => {
      form.copy.badge_zh = "节";
    }],
    ["copy whitespace", (form) => {
      form.copy.title_en = "Raya Sale ";
    }],
  ])("is dirty after changing %s", (_name, change) => {
    const base = formFromPromotion(PROMOTION);
    const form = formFromPromotion(PROMOTION);
    change(form);
    expect(isDirty(form, base)).toBe(true);
  });

  // 同上：提交内容相同即没有改动——只含空白的文案与空串都提交 null；rule_id 与 products 不提交。
  it("ignores differences that are not submitted", () => {
    const base = formFromPromotion(PROMOTION);
    const form = formFromPromotion(PROMOTION);
    form.copy.title_ms = "\u{a0}\u{3000}";
    ruleAt(form, 0).products = [];
    delete ruleAt(form, 0).rule_id;
    expect(isDirty(form, base)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 规则（SHOP-TASK-282）
// ---------------------------------------------------------------------------

const ITEM_AMOUNT_RULE: AdminPromotionRule = {
  rule_id: 21,
  kind: "item_discount",
  discount_mode: "amount",
  percent: null,
  amount_sen: 1050,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [8],
  category_ids: [],
  tiers: [],
  products: [{ id: 8, slug: "ketupat", name_en: "Ketupat" }],
};

const BXGY_RULE: AdminPromotionRule = {
  rule_id: 22,
  kind: "buy_x_get_y",
  discount_mode: null,
  percent: null,
  amount_sen: null,
  buy_quantity: 2,
  get_quantity: 1,
  get_percent: 50,
  threshold_sen: null,
  product_ids: [],
  category_ids: [4, 6],
  tiers: [],
  products: [],
};

const PERCENT_SPEND_RULE: AdminPromotionRule = {
  rule_id: 23,
  kind: "spend_discount",
  discount_mode: "percent",
  percent: null,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: null,
  product_ids: [],
  category_ids: [],
  tiers: [
    { threshold_sen: 5000, amount_sen: null, percent: 5 },
    { threshold_sen: 15005, amount_sen: null, percent: 12 },
  ],
  products: [],
};

const FREE_SHIPPING_RULE: AdminPromotionRule = {
  rule_id: 24,
  kind: "free_shipping",
  discount_mode: null,
  percent: null,
  amount_sen: null,
  buy_quantity: null,
  get_quantity: null,
  get_percent: null,
  threshold_sen: 15000,
  product_ids: [],
  category_ids: [],
  tiers: [],
  products: [],
};

// 四类规则都有的活动：每件直降两种方式、买 X 享 Y、满额折与免运费。
const FOUR_KINDS: AdminPromotion = { ...PROMOTION, rules: [ITEM_RULE, ITEM_AMOUNT_RULE, BXGY_RULE, PERCENT_SPEND_RULE, FREE_SHIPPING_RULE] };

type Problem = { code: string; field: string };

// 合格的每件直降（百分比 10%，选了商品 1），各检查测试在它上面只改一项。
function itemDiscount(changes: Partial<FormRuleOf<"item_discount">> = {}): FormRuleOf<"item_discount"> {
  return { ...newRule("item_discount"), discount: "10", product_ids: [1], ...changes };
}

// 合格的买 X 享 Y（买 2 享 1 件 100%，选了商品 1）。
function buyXGetY(changes: Partial<FormRuleOf<"buy_x_get_y">> = {}): FormRuleOf<"buy_x_get_y"> {
  return { ...newRule("buy_x_get_y"), buy_quantity: "2", get_quantity: "1", get_percent: "100", product_ids: [1], ...changes };
}

function spend(mode: "percent" | "amount", tiers: readonly (readonly [string, string])[]): FormRuleOf<"spend_discount"> {
  return { ...newRule("spend_discount"), discount_mode: mode, tiers: tiers.map(([threshold, discount]) => ({ threshold, discount })) };
}

function freeShipping(threshold: string): FormRuleOf<"free_shipping"> {
  return { ...newRule("free_shipping"), threshold };
}

// 名称已填、只有这些规则的表单的问题。
function problemsOf(...rules: PromotionFormRule[]): Problem[] {
  return formProblems(validForm({ rules }));
}

// 第一条规则上的问题：field 前加 rules.1.。
function onFirst(code: string, ...fields: string[]): Problem[] {
  return fields.map((field) => ({ code, field: `rules.1.${field}` }));
}

// count 档逐档递增的合格满额减：门槛 RM100、200…，优惠 RM10、20…。
function increasingTiers(count: number): [string, string][] {
  return Array.from({ length: count }, (_, index) => [rmText((index + 1) * 10000), rmText((index + 1) * 1000)]);
}

function range(count: number): number[] {
  return Array.from({ length: count }, (_, index) => index + 1);
}

describe("rule constants", () => {
  // app/models/promotion.py 的 PERCENT_MIN、DISCOUNT_PERCENT_MAX、GET_PERCENT_MAX、ITEM_AMOUNT_MAX_SEN、QUANTITY_MIN、QUANTITY_MAX、THRESHOLD_MAX_SEN、
  // SPEND_TIERS_MAX 与 app/services/admin_promotions.py 的 ITEM_RULES_MAX、SCOPE_PRODUCTS_MAX、SCOPE_CATEGORIES_MAX、RULE_KINDS：前端的常量与它们相同。
  it("matches the server", () => {
    expect([PERCENT_MIN, DISCOUNT_PERCENT_MAX, GET_PERCENT_MAX, ITEM_AMOUNT_MAX_SEN]).toEqual([1, 90, 100, 50_000_000]);
    expect([QUANTITY_MIN, QUANTITY_MAX, THRESHOLD_MAX_SEN, SPEND_TIERS_MAX]).toEqual([1, 10, 99_999_999, 5]);
    expect([ITEM_RULES_MAX, SCOPE_PRODUCTS_MAX, SCOPE_CATEGORIES_MAX]).toEqual([20, 200, 30]);
    expect(RULE_KINDS).toEqual(["item_discount", "buy_x_get_y", "spend_discount", "free_shipping"]);
  });
});

describe("RM and integer text", () => {
  // UX 0.20 A12「金额以 RM 输入、提交整仙」与落实写法「照 AdminShippingPage 的 feeSen」：一到九位数字，可再跟小数点与一到两位数字，不经浮点数相乘。
  it.each<[string, number | null]>([
    ["0", 0],
    ["0.5", 50],
    ["0.05", 5],
    ["10", 1000],
    ["10.5", 1050],
    ["10.50", 1050],
    ["007.10", 710],
    ["0.29", 29],
    ["1.15", 115],
    ["999999999.99", 99_999_999_999],
    ["", null],
    [" 1", null],
    ["1 ", null],
    ["1.", null],
    [".5", null],
    ["1.234", null],
    ["RM1", null],
    ["1,000", null],
    ["-1", null],
    ["+1", null],
    ["1e3", null],
    ["１", null],
    ["1000000000", null],
  ])("rmSen(%j) is %j", (text, sen) => {
    expect(rmSen(text)).toBe(sen);
  });

  // 落实写法「照 feeText」：整仙用整数运算写成不带 RM 前缀的两位小数文字，再经 rmSen 换回原值。
  it.each<[number, string]>([
    [0, "0.00"],
    [5, "0.05"],
    [29, "0.29"],
    [800, "8.00"],
    [1050, "10.50"],
    [15005, "150.05"],
    [ITEM_AMOUNT_MAX_SEN, "500000.00"],
    [THRESHOLD_MAX_SEN, "999999.99"],
  ])("rmText(%j) is %j", (sen, text) => {
    expect(rmText(sen)).toBe(text);
    expect(rmSen(text)).toBe(sen);
  });

  // 落实写法「整数文字只认 /^[0-9]+$/（不带符号、空白与小数点，前导零照收）」；位数多到不是安全整数的也换不成整数。
  it.each<[string, number | null]>([
    ["0", 0],
    ["7", 7],
    ["007", 7],
    ["100", 100],
    ["", null],
    [" 7", null],
    ["7 ", null],
    ["+7", null],
    ["-7", null],
    ["7.0", null],
    ["7.", null],
    ["1e2", null],
    ["١", null],
    ["７", null],
    ["9".repeat(20), null],
  ])("integerOf(%j) is %j", (text, value) => {
    expect(integerOf(text)).toBe(value);
  });
});

describe("rule form", () => {
  // 落实写法（1）「每条规则有页面内的 key、kind 与按类别用到的文字输入（金额以 RM 文字输入…；百分比与件数为整数文字），商品级规则另有 product_ids、
  // 显示用的商品列表（ID 与名称）与 category_ids」：读回的四类规则按类别与方式写成文字，key 按排序。
  it("reads each kind as editable text", () => {
    expect(formFromPromotion(FOUR_KINDS).rules).toEqual([
      { key: 0, rule_id: 11, kind: "item_discount", discount_mode: "percent", discount: "20", product_ids: [3, 5], products: ITEM_RULE.products, category_ids: [2] },
      { key: 1, rule_id: 21, kind: "item_discount", discount_mode: "amount", discount: "10.50", product_ids: [8], products: ITEM_AMOUNT_RULE.products, category_ids: [] },
      { key: 2, rule_id: 22, kind: "buy_x_get_y", buy_quantity: "2", get_quantity: "1", get_percent: "50", product_ids: [], products: [], category_ids: [4, 6] },
      {
        key: 3,
        rule_id: 23,
        kind: "spend_discount",
        discount_mode: "percent",
        tiers: [
          { threshold: "50.00", discount: "5" },
          { threshold: "150.05", discount: "12" },
        ],
      },
      { key: 4, rule_id: 24, kind: "free_shipping", threshold: "150.00" },
    ]);
    expect(formFromPromotion(PROMOTION).rules[1]).toEqual({
      key: 1,
      rule_id: 12,
      kind: "spend_discount",
      discount_mode: "amount",
      tiers: [
        { threshold: "100.00", discount: "10.00" },
        { threshold: "200.00", discount: "25.00" },
      ],
    });
  });

  // 验收「读取到的四类规则经表单往返后提交的参数与读取时相同」：每件直降两种方式、买 X 享 Y、满额减与满额折、免运费，读回的规则也通过提交前检查。
  it("submits the four kinds as they were read", () => {
    const form = formFromPromotion(FOUR_KINDS);
    expect(promotionInput(form).rules).toEqual(FOUR_KINDS.rules.map(submitted));
    expect(promotionInput(formFromPromotion(PROMOTION)).rules).toEqual(PROMOTION.rules.map(submitted));
    expect(formProblems(form)).toEqual([]);
    expect(isDirty(form, formFromPromotion(FOUR_KINDS))).toBe(false);
  });

  // 同上，取值范围两端的金额：免运费门槛 0、门槛上限 THRESHOLD_MAX_SEN、直降上限 ITEM_AMOUNT_MAX_SEN 与 1 仙经 RM 文字往返不变。
  it.each<AdminPromotionRule>([
    { ...FREE_SHIPPING_RULE, threshold_sen: 0 },
    { ...FREE_SHIPPING_RULE, threshold_sen: THRESHOLD_MAX_SEN },
    { ...ITEM_AMOUNT_RULE, amount_sen: ITEM_AMOUNT_MAX_SEN },
    { ...ITEM_AMOUNT_RULE, amount_sen: 1 },
    { ...SPEND_RULE, tiers: [{ threshold_sen: THRESHOLD_MAX_SEN, amount_sen: THRESHOLD_MAX_SEN - 1, percent: null }] },
  ])("round-trips the edge amounts of %j", (rule) => {
    const form = formFromPromotion({ ...PROMOTION, rules: [rule] });
    expect(promotionInput(form).rules).toEqual([submitted(rule)]);
    expect(formProblems(form)).toEqual([]);
  });

  // check_rule_params「类别未知…问题码为 rule」：服务端只存四类规则，读到其他类别时不悄悄丢掉（保存会删掉它），而是抛错。
  it("rejects a rule kind the server does not store", () => {
    expect(() => formFromPromotion({ ...PROMOTION, rules: [{ ...ITEM_RULE, kind: "bundle" }] })).toThrow(TypeError);
  });

  // UX 0.20 A12「金额以 RM 输入、提交整仙」与 isDirty「提交内容相同即没有改动」：同一金额的不同写法提交相同的整仙，不算改动；金额改变才算。
  it("compares rules by the submitted amounts", () => {
    const base = formFromPromotion(FOUR_KINDS);
    const form = formFromPromotion(FOUR_KINDS);
    Object.assign(ruleAt(form, 1), { discount: "10.5" });
    Object.assign(ruleAt(form, 2), { buy_quantity: "02" });
    expect(isDirty(form, base)).toBe(false);
    Object.assign(ruleAt(form, 4), { threshold: "150.01" });
    expect(isDirty(form, base)).toBe(true);
  });

  // 落实写法（1）「promotionInput 按类别只填该类用到的参数，其余为 null 或空」（check_rule_params「不属于该类别的参数…不为空，问题码为 rule」）：
  // 每件直降按方式只填 percent 或 amount_sen，满额各档按方式只填 percent 或 amount_sen，整单级规则的范围为空；不提交 key、rule_id 与 products。
  it("submits only the parameters of the kind and mode", () => {
    const form = validForm({
      rules: [
        { ...itemDiscount({ discount: "15", category_ids: [7] }), rule_id: 5, products: [{ id: 1, slug: "a", name_en: "A" }] },
        itemDiscount({ discount_mode: "amount", discount: "12.50" }),
        buyXGetY(),
        spend("percent", [["80", "5"]]),
        freeShipping("60"),
      ],
    });
    const nulls = { discount_mode: null, percent: null, amount_sen: null, buy_quantity: null, get_quantity: null, get_percent: null, threshold_sen: null };
    expect(promotionInput(form).rules).toEqual([
      { kind: "item_discount", ...nulls, discount_mode: "percent", percent: 15, product_ids: [1], category_ids: [7], tiers: [] },
      { kind: "item_discount", ...nulls, discount_mode: "amount", amount_sen: 1250, product_ids: [1], category_ids: [], tiers: [] },
      { kind: "buy_x_get_y", ...nulls, buy_quantity: 2, get_quantity: 1, get_percent: 100, product_ids: [1], category_ids: [], tiers: [] },
      { kind: "spend_discount", ...nulls, discount_mode: "percent", product_ids: [], category_ids: [], tiers: [{ threshold_sen: 8000, amount_sen: null, percent: 5 }] },
      { kind: "free_shipping", ...nulls, threshold_sen: 6000, product_ids: [], category_ids: [], tiers: [] },
    ]);
  });

  // 落实写法「换不成整数或整仙的参数在 promotionInput 里为 null（formProblems 同时报它不合格，页面不会提交）」。
  it("submits null for text that is not a number", () => {
    const form = validForm({
      rules: [
        itemDiscount({ discount: "12.5" }),
        itemDiscount({ discount_mode: "amount", discount: "1.234" }),
        buyXGetY({ buy_quantity: "2.0", get_percent: " 50" }),
        spend("amount", [["RM100", "10"]]),
        freeShipping("-1"),
      ],
    });
    const rules = promotionInput(form).rules;
    expect(rules.map((rule) => [rule.percent, rule.amount_sen, rule.buy_quantity, rule.get_percent, rule.threshold_sen])).toEqual([
      [null, null, null, null, null],
      [null, null, null, null, null],
      [null, null, null, null, null],
      [null, null, null, null, null],
      [null, null, null, null, null],
    ]);
    expect(rules[3]?.tiers).toEqual([{ threshold_sen: null, amount_sen: 1000, percent: null }]);
    expect(formProblems(form)).toEqual([
      { code: "rule", field: "rules.1.percent" },
      { code: "rule", field: "rules.2.amount_sen" },
      { code: "rule", field: "rules.3.buy_quantity" },
      { code: "rule", field: "rules.3.get_percent" },
      { code: "tiers", field: "rules.4.tiers.1.threshold_sen" },
      { code: "rule", field: "rules.5.threshold_sen" },
    ]);
  });
});

describe("new rules and switching modes", () => {
  // 落实写法「新规则的初值（每件直降为百分比方式、数值空；买 X 享 Y 三项空；满额为满额减方式、一档（门槛与优惠都空）；免运费门槛空；商品级规则的范围为空）」。
  it("starts each kind with the documented values", () => {
    expect(newPromotionRule("item_discount", 3)).toEqual({
      key: 3,
      kind: "item_discount",
      discount_mode: "percent",
      discount: "",
      product_ids: [],
      products: [],
      category_ids: [],
    });
    expect(newPromotionRule("buy_x_get_y", 4)).toEqual({
      key: 4,
      kind: "buy_x_get_y",
      buy_quantity: "",
      get_quantity: "",
      get_percent: "",
      product_ids: [],
      products: [],
      category_ids: [],
    });
    expect(newPromotionRule("spend_discount", 5)).toEqual({ key: 5, kind: "spend_discount", discount_mode: "amount", tiers: [{ threshold: "", discount: "" }] });
    expect(newPromotionRule("free_shipping", 6)).toEqual({ key: 6, kind: "free_shipping", threshold: "" });
  });

  // 同上：每次都是新的对象，页面改一条新规则不影响下一条。
  it("returns fresh rules each time", () => {
    const one = newRule("item_discount");
    one.product_ids.push(1);
    one.products.push({ id: 1, slug: "a", name_en: null });
    const tiers = newRule("spend_discount");
    tiers.tiers.push({ threshold: "1", discount: "" });
    expect(newRule("item_discount").product_ids).toEqual([]);
    expect(newRule("item_discount").products).toEqual([]);
    expect(newRule("spend_discount").tiers).toHaveLength(1);
  });

  // 同上，新规则的提交内容：空文字换不成整数或整仙，参数为 null；满额的一档三项都为 null。
  it("submits new rules with null parameters", () => {
    const nulls = { discount_mode: null, percent: null, amount_sen: null, buy_quantity: null, get_quantity: null, get_percent: null, threshold_sen: null };
    const form = validForm({ rules: RULE_KINDS.map((kind, key) => newPromotionRule(kind, key)) });
    expect(promotionInput(form).rules).toEqual([
      { kind: "item_discount", ...nulls, discount_mode: "percent", product_ids: [], category_ids: [], tiers: [] },
      { kind: "buy_x_get_y", ...nulls, product_ids: [], category_ids: [], tiers: [] },
      { kind: "spend_discount", ...nulls, discount_mode: "amount", product_ids: [], category_ids: [], tiers: [{ threshold_sen: null, amount_sen: null, percent: null }] },
      { kind: "free_shipping", ...nulls, product_ids: [], category_ids: [], tiers: [] },
    ]);
  });

  // 落实写法（1）「每条规则有页面内的 key」：读取时 key 按排序，新加规则的 key 比现有的都大，删掉规则后也不与剩下的重复。
  it("gives new rules an unused key", () => {
    expect(nextRuleKey(newPromotionForm())).toBe(0);
    const form = formFromPromotion(FOUR_KINDS);
    expect(form.rules.map((rule) => rule.key)).toEqual([0, 1, 2, 3, 4]);
    expect(nextRuleKey(form)).toBe(5);
    form.rules = form.rules.filter((rule) => rule.key !== 0);
    expect(nextRuleKey(form)).toBe(5);
    form.rules.reverse();
    expect(nextRuleKey(form)).toBe(5);
  });

  // 落实写法「切换直降的百分比与金额方式…时，清空该规则的优惠值文字」：方式改变时优惠清空、范围保留；方式不变时照原值；不改原规则。
  it("clears the item discount when the mode changes", () => {
    const rule = itemDiscount({ discount: "20", category_ids: [2], products: [{ id: 1, slug: "a", name_en: "A" }] });
    const amount = switchDiscountMode(rule, "amount");
    expect(amount).toEqual({ ...rule, discount_mode: "amount", discount: "" });
    expect(amount.product_ids).not.toBe(rule.product_ids);
    expect(rule.discount_mode).toBe("percent");
    expect(rule.discount).toBe("20");
    expect(switchDiscountMode(rule, "percent")).toEqual(rule);
    const back = switchDiscountMode({ ...amount, discount: "12.50" }, "percent");
    expect(back.discount).toBe("");
    expect(promotionInput(validForm({ rules: [{ ...amount, discount: "12.50" }] })).rules[0]).toMatchObject({ percent: null, amount_sen: 1250 });
  });

  // 落实写法「切换…满额的减与折时，清空该规则（满额为各档）的优惠值文字，门槛保留」。
  it("clears every tier discount and keeps thresholds when the spend mode changes", () => {
    const rule = spend("amount", [
      ["100.00", "10.00"],
      ["200.00", "25.00"],
    ]);
    const percent = switchDiscountMode(rule, "percent");
    expect(percent).toEqual({
      ...rule,
      discount_mode: "percent",
      tiers: [
        { threshold: "100.00", discount: "" },
        { threshold: "200.00", discount: "" },
      ],
    });
    expect(rule.tiers[0]).toEqual({ threshold: "100.00", discount: "10.00" });
    expect(percent.tiers).not.toBe(rule.tiers);
    expect(switchDiscountMode(rule, "amount")).toEqual(rule);
    expect(switchDiscountMode({ ...percent, tiers: [{ threshold: "100.00", discount: "5" }] }, "amount").tiers).toEqual([{ threshold: "100.00", discount: "" }]);
  });
});

describe("rule parameter check", () => {
  // check_rule_params「每件直降…MODE_PERCENT：require("percent", PERCENT_MIN, DISCOUNT_PERCENT_MAX)」：百分比方式 1 至 90 的整数，整数文字只认 ASCII 数字
  // （前导零照收），换不成整数也不合格；问题码 rule，field rules.N.percent。
  it.each<[string, boolean]>([
    ["1", true],
    ["90", true],
    ["01", true],
    ["0", false],
    ["91", false],
    ["", false],
    ["1.5", false],
    ["-1", false],
    [" 5", false],
    ["5 ", false],
    ["+5", false],
    ["５", false],
  ])("item percent %j is valid: %s", (discount, valid) => {
    expect(problemsOf(itemDiscount({ discount }))).toEqual(valid ? [] : onFirst("rule", "percent"));
  });

  // check_rule_params「MODE_AMOUNT：require("amount_sen", 1, ITEM_AMOUNT_MAX_SEN)」与 UX 0.20 A12 M1「每件直降额（RM 输入，提交整仙）」：RM0.01 至 RM500,000.00，
  // RM 文字照 feeSen 的格式；field rules.N.amount_sen。
  it.each<[string, boolean]>([
    ["0.01", true],
    ["1", true],
    ["500000.00", true],
    ["500000.01", false],
    ["0", false],
    ["0.00", false],
    ["", false],
    ["1.234", false],
    ["RM1", false],
    ["1,000", false],
  ])("item amount %j is valid: %s", (discount, valid) => {
    expect(problemsOf(itemDiscount({ discount_mode: "amount", discount }))).toEqual(valid ? [] : onFirst("rule", "amount_sen"));
  });

  // check_rule_params「买 X 享 Y：require("buy_quantity", QUANTITY_MIN, QUANTITY_MAX)、get_quantity 同、require("get_percent", PERCENT_MIN, GET_PERCENT_MAX)」
  // 与 UX 0.20 A12「X 与 Y」：X、Y 为 1 至 10，Y 的折扣 1 至 100 的整数；field 为各自的参数名。
  it.each<["buy_quantity" | "get_quantity" | "get_percent", string, boolean]>([
    ["buy_quantity", "1", true],
    ["buy_quantity", "10", true],
    ["buy_quantity", "0", false],
    ["buy_quantity", "11", false],
    ["buy_quantity", "", false],
    ["buy_quantity", "2.0", false],
    ["get_quantity", "1", true],
    ["get_quantity", "10", true],
    ["get_quantity", "0", false],
    ["get_quantity", "11", false],
    ["get_quantity", "x", false],
    ["get_percent", "1", true],
    ["get_percent", "100", true],
    ["get_percent", "0", false],
    ["get_percent", "101", false],
    ["get_percent", "", false],
  ])("%s %j is valid: %s", (field, text, valid) => {
    const rule = buyXGetY();
    rule[field] = text;
    expect(problemsOf(rule)).toEqual(valid ? [] : onFirst("rule", field));
  });

  // check_rule_params「免运费：require("threshold_sen", 0, THRESHOLD_MAX_SEN)」与 UX 0.20 A12 M1「免运费门槛（RM 输入，提交整仙）」：RM0 至 RM999,999.99；
  // field rules.N.threshold_sen。
  it.each<[string, boolean]>([
    ["0", true],
    ["0.00", true],
    ["1.5", true],
    ["999999.99", true],
    ["1000000", false],
    ["1000000.00", false],
    ["", false],
    ["-1", false],
    ["abc", false],
  ])("free shipping threshold %j is valid: %s", (threshold, valid) => {
    expect(problemsOf(freeShipping(threshold))).toEqual(valid ? [] : onFirst("rule", "threshold_sen"));
  });

  // check_rule_params 按参数、check_rule_scope 按范围依次报出；新规则的空文字都不合格（UX 0.20 A12「不合格的字段旁显示 [admin.promo_field_invalid] 并不提交」）。
  it.each<[RuleKind, Problem[]]>([
    ["item_discount", [...onFirst("rule", "percent"), ...onFirst("scope", "product_ids")]],
    ["buy_x_get_y", [...onFirst("rule", "buy_quantity", "get_quantity", "get_percent"), ...onFirst("scope", "product_ids")]],
    ["spend_discount", onFirst("tiers", "tiers.1.threshold_sen", "tiers.1.amount_sen")],
    ["free_shipping", onFirst("rule", "threshold_sen")],
  ])("reports the empty fields of a new %s rule", (kind, problems) => {
    expect(problemsOf(newPromotionRule(kind, 0))).toEqual(problems);
  });
});

describe("spend tier check", () => {
  // _tier_problems「门槛…_int_in(threshold, 1, THRESHOLD_MAX_SEN)」与「固定金额小于门槛；门槛不合格时只查不超过门槛的上限」（below - 1）：满额减一档的门槛
  // RM0.01 至 RM999,999.99，优惠 RM0.01 至门槛减 1 仙；问题码 tiers，field rules.N.tiers.M.<参数>。
  it.each<{ tier: [string, string]; fields: string[] }>([
    { tier: ["100.00", "10.00"], fields: [] },
    { tier: ["0.02", "0.01"], fields: [] },
    { tier: ["100.00", "99.99"], fields: [] },
    { tier: ["999999.99", "999999.98"], fields: [] },
    { tier: ["0.01", "0.01"], fields: ["tiers.1.amount_sen"] },
    { tier: ["100.00", "100.00"], fields: ["tiers.1.amount_sen"] },
    { tier: ["100", "0"], fields: ["tiers.1.amount_sen"] },
    { tier: ["100", ""], fields: ["tiers.1.amount_sen"] },
    { tier: ["100", "1.234"], fields: ["tiers.1.amount_sen"] },
    { tier: ["0.00", "0.01"], fields: ["tiers.1.threshold_sen"] },
    { tier: ["abc", "10"], fields: ["tiers.1.threshold_sen"] },
    { tier: ["1000000.00", "999999.98"], fields: ["tiers.1.threshold_sen"] },
    { tier: ["1000000.00", "999999.99"], fields: ["tiers.1.threshold_sen", "tiers.1.amount_sen"] },
    { tier: ["", ""], fields: ["tiers.1.threshold_sen", "tiers.1.amount_sen"] },
  ])("checks the amount tier $tier", ({ tier, fields }) => {
    expect(problemsOf(spend("amount", [tier]))).toEqual(onFirst("tiers", ...fields));
  });

  // _tier_problems「percent：_int_in(discount, PERCENT_MIN, DISCOUNT_PERCENT_MAX)」：满额折的优惠为 1 至 90 的整数，field rules.N.tiers.M.percent。
  it.each<[string, boolean]>([
    ["1", true],
    ["90", true],
    ["05", true],
    ["0", false],
    ["91", false],
    ["12.5", false],
    ["", false],
  ])("percent tier discount %j is valid: %s", (discount, valid) => {
    expect(problemsOf(spend("percent", [["100", discount]]))).toEqual(valid ? [] : onFirst("tiers", "tiers.1.percent"));
  });

  // _tier_problems「1 <= len(tiers) <= SPEND_TIERS_MAX」否则 tiers：没有档与 6 档不合格，1 至 5 档合格；问题先于各档的问题。
  it.each<[number, boolean]>([
    [0, false],
    [1, true],
    [5, true],
    [6, false],
  ])("%i tiers are valid: %s", (count, valid) => {
    expect(problemsOf(spend("amount", increasingTiers(count)))).toEqual(valid ? [] : onFirst("tiers", "tiers"));
  });

  // 同上，档数问题排在各档问题之前。
  it("lists the tier count before the tiers", () => {
    const tiers = increasingTiers(6);
    tiers[0] = ["0", "1"];
    expect(problemsOf(spend("amount", tiers))).toEqual(onFirst("tiers", "tiers", "tiers.1.threshold_sen"));
  });

  // _tier_problems「门槛与优惠逐档严格递增」：相邻两档都合格时才比较，不递增的报在后一档；先报门槛、再报优惠；中间一档不合格时不跨档比较。
  it.each<{ mode: "percent" | "amount"; tiers: [string, string][]; fields: string[] }>([
    { mode: "amount", tiers: [["100", "10"], ["200", "20"]], fields: [] },
    { mode: "amount", tiers: [["100", "10"], ["100", "20"]], fields: ["tiers.2.threshold_sen"] },
    { mode: "amount", tiers: [["200", "10"], ["100", "20"]], fields: ["tiers.2.threshold_sen"] },
    { mode: "amount", tiers: [["100", "20"], ["200", "20"]], fields: ["tiers.2.amount_sen"] },
    { mode: "amount", tiers: [["100", "20"], ["200", "10"]], fields: ["tiers.2.amount_sen"] },
    { mode: "amount", tiers: [["200", "20"], ["100", "10"]], fields: ["tiers.2.threshold_sen", "tiers.2.amount_sen"] },
    { mode: "amount", tiers: [["300", "30"], ["x", ""], ["100", "10"]], fields: ["tiers.2.threshold_sen", "tiers.2.amount_sen"] },
    {
      mode: "amount",
      tiers: [["100", "10"], ["300", "30"], ["200", "20"], ["150", "40"]],
      fields: ["tiers.3.threshold_sen", "tiers.4.threshold_sen", "tiers.3.amount_sen"],
    },
    { mode: "amount", tiers: [["200", "250"], ["100", "10"]], fields: ["tiers.1.amount_sen", "tiers.2.threshold_sen"] },
    { mode: "percent", tiers: [["100", "5"], ["200", "10"]], fields: [] },
    { mode: "percent", tiers: [["100", "5"], ["200", "5"]], fields: ["tiers.2.percent"] },
    { mode: "percent", tiers: [["100", "50"], ["200", "95"], ["300", "40"]], fields: ["tiers.2.percent"] },
  ])("checks $mode tiers $tiers", ({ mode, tiers, fields }) => {
    expect(problemsOf(spend(mode, tiers))).toEqual(onFirst("tiers", ...fields));
  });
});

describe("rule scope check", () => {
  // check_rule_scope「商品与分类 ID 为正整数，各至多 200 件、30 个，同一规则里不重复，且至少选一项（两项都空时指向 product_ids）」与 UX 0.20 A12
  // 「范围至多 200 件商品与 30 个分类」：两类商品级规则相同；问题码 scope，field rules.N.product_ids 或 rules.N.category_ids。
  const cases: [string, number[], number[], string[]][] = [
    ["nothing", [], [], ["product_ids"]],
    ["one category", [], [2], []],
    ["one product", [1], [], []],
    ["the same ID as product and category", [3], [3], []],
    ["200 products", range(200), [], []],
    ["201 products", range(201), [], ["product_ids"]],
    ["30 categories", [], range(30), []],
    ["31 categories", [], range(31), ["category_ids"]],
    ["201 products and 31 categories", range(201), range(31), ["product_ids", "category_ids"]],
    ["a repeated product", [1, 1], [], ["product_ids"]],
    ["a repeated category", [], [2, 2], ["category_ids"]],
    ["product 0", [0], [], ["product_ids"]],
    ["category 0", [1], [0], ["category_ids"]],
    ["a fractional product", [1.5], [], ["product_ids"]],
  ];

  it.each(cases)("item_discount with %s", (_name, product_ids, category_ids, fields) => {
    expect(problemsOf(itemDiscount({ product_ids, category_ids }))).toEqual(onFirst("scope", ...fields));
  });

  it.each(cases)("buy_x_get_y with %s", (_name, product_ids, category_ids, fields) => {
    expect(problemsOf(buyXGetY({ product_ids, category_ids }))).toEqual(onFirst("scope", ...fields));
  });
});

describe("rule count check", () => {
  function items(count: number, kind: "item_discount" | "buy_x_get_y" = "item_discount"): PromotionFormRule[] {
    return Array.from({ length: count }, () => (kind === "item_discount" ? itemDiscount() : buyXGetY()));
  }
  const spendRule = spend("amount", [["100", "10"]]);
  const freeRule = freeShipping("0");
  const rulesProblem = { code: "rule", field: "rules" };

  // _rule_count_problems「一个活动最多 20 条商品级规则、至多一条满额减或满额折、至多一条满额免运费」与 UX 0.20 A12「最多 20 条商品级规则、一条满额减或满额折、
  // 一条免运费」：每项超出各报一个 rules（问题码 rule），按商品级、满额、免运费的顺序，排在各条规则的问题之后。
  it.each<[string, PromotionFormRule[], number]>([
    ["20 item rules with one spend and one free shipping rule", [...items(20), spendRule, freeRule], 0],
    ["21 item discount rules", items(21), 1],
    ["10 item discount and 11 buy x get y rules", [...items(10), ...items(11, "buy_x_get_y")], 1],
    ["20 buy x get y rules", items(20, "buy_x_get_y"), 0],
    ["two spend rules", [spendRule, spendRule], 1],
    ["two free shipping rules", [freeRule, freeRule], 1],
    ["21 item, two spend and two free shipping rules", [...items(21), spendRule, spendRule, freeRule, freeRule], 3],
  ])("%s", (_name, rules, count) => {
    expect(problemsOf(...rules)).toEqual(Array.from({ length: count }, () => rulesProblem));
  });

  // 同上：条数问题排在各条规则的问题之后。
  it("lists the count after the rules", () => {
    expect(problemsOf(freeShipping("x"), freeRule)).toEqual([{ code: "rule", field: "rules.1.threshold_sen" }, rulesProblem]);
  });
});

describe("rule problem order", () => {
  // check_promotion_input「依次检查内部名称、时间、每条规则的参数与范围、规则条数、装饰与文案」：规则的问题在时间之后、文案之前，按规则排序逐条
  // （field 的 N 从 1 起），每条先参数后范围，之后是条数。
  it("lists rule problems between the time and the copy", () => {
    const form = validForm({
      name: "",
      ends_at: "bad",
      rules: [itemDiscount(), buyXGetY({ get_percent: "0", product_ids: [] }), freeShipping(""), freeShipping("1"), spend("percent", [["1", "0"]])],
    });
    form.copy = { ...form.copy, title_en: "a".repeat(41) };
    expect(formProblems(form)).toEqual([
      { code: "name", field: "name" },
      { code: "time", field: "ends_at" },
      { code: "rule", field: "rules.2.get_percent" },
      { code: "scope", field: "rules.2.product_ids" },
      { code: "rule", field: "rules.3.threshold_sen" },
      { code: "tiers", field: "rules.5.tiers.1.percent" },
      { code: "rule", field: "rules" },
      { code: "copy", field: "copy.title_en" },
    ]);
  });
});

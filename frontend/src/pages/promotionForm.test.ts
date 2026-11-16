import { describe, expect, it } from "vitest";

import type { AdminPromotion, AdminPromotionRule, PromotionCopy, PromotionRuleInput, PromotionTier } from "../api/adminPromotions";
import {
  COPY_FIELDS,
  copyLength,
  copyMaxLength,
  formFromPromotion,
  formProblems,
  isDirty,
  newPromotionForm,
  promotionInput,
  pyStrip,
  type CopyField,
  type PromotionForm,
  type PromotionFormRule,
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
  strip_ms: "﻿",
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

function ruleAt(form: PromotionForm, index: number): PromotionFormRule {
  const rule = form.rules[index];
  if (rule === undefined) {
    throw new Error(`no rule ${String(index)}`);
  }
  return rule;
}

function tierAt(rule: PromotionFormRule, index: number): PromotionTier {
  const tier = rule.tiers[index];
  if (tier === undefined) {
    throw new Error(`no tier ${String(index)}`);
  }
  return tier;
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
    first.rules.push(submitted(ITEM_RULE));
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
    expect(input.copy.strip_ms).toBe("﻿");
  });

  // 落实写法（1）「copy 为 15 个文案字段的字符串，null 读成空串」与「starts_at 与 ends_at 为空即未填」。
  it("reads null copy and times as empty strings", () => {
    const form = formFromPromotion({ ...PROMOTION, starts_at: null, ends_at: null });
    expect(form.starts_at).toBe("");
    expect(form.ends_at).toBe("");
    expect(form.copy.title_ms).toBe("");
    expect(form.copy.title_zh).toBe("开斋节特卖");
    expect(form.copy.strip_ms).toBe("﻿");
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
    form.rules = [ruleAt(form, 1), { ...submitted(ITEM_RULE), product_ids: [9] }];
    form.decoration = { ...form.decoration, pack: "cny", ambient: "fall" };
    const input = promotionInput(form);
    expect(input.rules).toEqual([submitted(SPEND_RULE), { ...submitted(ITEM_RULE), product_ids: [9] }]);
    expect(input.decoration).toEqual({ ...PROMOTION.decoration, pack: "cny", ambient: "fall" });
  });

  // 同上：表单不与读取结果共用对象，改表单不改读取结果（读取结果之后还作 isDirty 的基准）。
  it("does not share objects with the read result", () => {
    const form = formFromPromotion(PROMOTION);
    ruleAt(form, 0).product_ids.push(99);
    ruleAt(form, 0).products?.push({ id: 99, slug: "x", name_en: null });
    tierAt(ruleAt(form, 1), 0).amount_sen = 1;
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
    expect(pyStrip(" \t　a b \u0085\u001c")).toBe("a b");
    expect(pyStrip("       x\u000b\u000c\r\n")).toBe("x");
    expect(pyStrip("﻿ a ﻿")).toBe("﻿ a ﻿");
    expect(pyStrip("​")).toBe("​");
    expect(pyStrip("")).toBe("");
  });

  // 落实写法（2）「另导出 copyLength(文字)（pyStrip 后的码点数）」：服务端 len(value.strip()) 按码点计数，emoji 与汉字各算一个。
  it("counts code points after stripping", () => {
    expect(copyLength("  ab  ")).toBe(2);
    expect(copyLength("😀🎉")).toBe(2);
    expect(copyLength("开斋节")).toBe(3);
    expect(copyLength(" 　 ")).toBe(0);
    expect(copyLength("﻿")).toBe(1);
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
    ["　 ", null],
    ["\u001c\u0085", null],
    [" Raya ", " Raya "],
    ["﻿", "﻿"],
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
    [` ${"a".repeat(100)}　`, true],
    ["😀".repeat(100), true],
    ["", false],
    ["   ", false],
    ["　", false],
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
    ["a b", false],
    ["a b", false],
    ["👨‍👩", true],
    ["﻿a", true],
    ["a b", true],
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
    expect(formProblems(withCopy(field, ` ${"字".repeat(maximum)}　`))).toEqual([]);
    expect(formProblems(withCopy(field, "😀".repeat(maximum)))).toEqual([]);
    expect(formProblems(withCopy(field, "a".repeat(maximum + 1)))).toEqual(problem);
    expect(formProblems(withCopy(field, "😀".repeat(maximum + 1)))).toEqual(problem);
  });

  // check_copy「不含换行与控制字符」与 UX 0.20 A12「不能输入换行」：_text_ok 查原文，只有空白控制字符的文案也不合格。
  it.each(["a\nb", "\n", "a\r", "a\t", "a\u0000", "a\u0085", "\u001c", "a ", "a "])("rejects copy %j", (text) => {
    expect(formProblems(withCopy("badge_ms", text))).toEqual([{ code: "copy", field: "copy.badge_ms" }]);
  });

  // has_forbidden_char「零宽连接符等 Cf 类允许（emoji 要用）」：U+200D、U+FEFF 与不换行空格允许。
  it.each(["👨‍👩", "﻿", "a b"])("accepts copy %j", (text) => {
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
      ruleAt(form, 0).category_ids.push(4);
    }],
    ["tier", (form) => {
      tierAt(ruleAt(form, 1), 1).amount_sen = 2000;
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
    form.copy.title_ms = " 　";
    ruleAt(form, 0).products = [];
    delete ruleAt(form, 0).rule_id;
    expect(isDirty(form, base)).toBe(false);
  });
});

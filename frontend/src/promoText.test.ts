import { describe, expect, it } from "vitest";

import type { CategoryDisplay, RuleDisplay, TierDisplay } from "./api/promotion";
import type { Language } from "./i18n/copy";
import { badgeText, countdownText, ruleTexts, scopeText } from "./promoText";

// 一条规则的展示数据：不属于该类别的参数为 null，同接口的实际返回。
function rule(overrides: Partial<RuleDisplay>): RuleDisplay {
  return {
    rule_id: 1,
    position: 1,
    kind: "item_discount",
    discount_mode: null,
    percent: null,
    amount_sen: null,
    buy_quantity: null,
    get_quantity: null,
    get_percent: null,
    threshold_sen: null,
    tiers: [],
    categories: [],
    has_products: false,
    ...overrides,
  };
}

const ITEM_PERCENT = rule({ kind: "item_discount", discount_mode: "percent", percent: 20 });
const ITEM_AMOUNT = rule({ kind: "item_discount", discount_mode: "amount", amount_sen: 500 });
const BXGY_FREE = rule({ kind: "buy_x_get_y", buy_quantity: 2, get_quantity: 1, get_percent: 100 });
const BXGY_HALF = rule({ kind: "buy_x_get_y", buy_quantity: 1, get_quantity: 1, get_percent: 50 });
const AMOUNT_TIERS: TierDisplay[] = [
  { threshold_sen: 5000, amount_sen: 500, percent: null },
  { threshold_sen: 10000, amount_sen: 1200, percent: null },
];
const PERCENT_TIERS: TierDisplay[] = [
  { threshold_sen: 5000, amount_sen: null, percent: 5 },
  { threshold_sen: 10000, amount_sen: null, percent: 10 },
];
const SPEND_AMOUNT = rule({ kind: "spend_discount", discount_mode: "amount", tiers: AMOUNT_TIERS });
const SPEND_PERCENT = rule({ kind: "spend_discount", discount_mode: "percent", tiers: PERCENT_TIERS });
const FREE_SHIPPING = rule({ kind: "free_shipping", threshold_sen: 6000 });
const FREE_SHIPPING_ALL = rule({ kind: "free_shipping", threshold_sen: 0 });
const UNKNOWN = rule({ kind: "mystery_gift", discount_mode: "percent", percent: 20 });

describe("badgeText", () => {
  // UX 0.20「商品卡价格与角标」「打折为 [promo.badge_percent] 或 [promo.badge_amount]，买 X 享 Y 为 [promo.badge_bxgy_free]（享受的件 100%）或 [promo.badge_bxgy_pct]」。
  it.each<[string, RuleDisplay, Record<Language, string>]>([
    ["percent discount", ITEM_PERCENT, { en: "−20%", zh: "−20%", ms: "−20%" }],
    ["amount discount", ITEM_AMOUNT, { en: "RM 5.00 off", zh: "立减 RM 5.00", ms: "Jimat RM 5.00" }],
    ["buy x get y free", BXGY_FREE, { en: "Buy 2 get 1 free", zh: "买 2 送 1", ms: "Beli 2 percuma 1" }],
    [
      "buy x get y at a percentage",
      BXGY_HALF,
      { en: "Buy 1, 1 more 50% off", zh: "买 1 件，再 1 件减 50%", ms: "Beli 1, 1 lagi diskaun 50%" },
    ],
  ])("writes the %s badge in all three languages", (_name, given, expected) => {
    expect(badgeText("en", given)).toBe(expected.en);
    expect(badgeText("zh", given)).toBe(expected.zh);
    expect(badgeText("ms", given)).toBe(expected.ms);
  });

  // UX 0.20「商品卡价格与角标」「只命中整单级规则的商品不显示角标」：满额与免运费没有角标；不认识的类别也不显示。
  it.each([
    ["spend amount", SPEND_AMOUNT],
    ["spend percent", SPEND_PERCENT],
    ["free shipping", FREE_SHIPPING],
    ["unknown kind", UNKNOWN],
  ])("gives no badge for %s", (_name, given) => {
    for (const language of ["en", "zh", "ms"] as const) {
      expect(badgeText(language, given)).toBeNull();
    }
  });

  // UX 0.20「商品卡与价格」「参加买 X 享 Y 的商品显示角标、不改价格」：商品卡只带角标所需的参数，也能直接传入。
  it("accepts the bare rule parameters of a product card", () => {
    const cardRule = {
      kind: "buy_x_get_y",
      discount_mode: null,
      percent: null,
      amount_sen: null,
      buy_quantity: 3,
      get_quantity: 2,
      get_percent: 100,
    };
    expect(badgeText("en", cardRule)).toBe("Buy 3 get 2 free");
  });
});

describe("ruleTexts", () => {
  // DESIGN 1.23 第 10 条「前台按三语文案模板生成规则说明，不用 AI 或管理员写的文字描述规则，保证说明与计价一致」：
  // 四类规则各套 promo.rule_* 模板，满额每档一行，金额只格式化接口给的整数仙。
  it.each<[string, RuleDisplay, Record<Language, string[]>]>([
    ["percent discount", ITEM_PERCENT, { en: ["20% off"], zh: ["立减 20%"], ms: ["Diskaun 20%"] }],
    [
      "amount discount",
      ITEM_AMOUNT,
      { en: ["RM 5.00 off each item"], zh: ["每件立减 RM 5.00"], ms: ["Jimat RM 5.00 setiap item"] },
    ],
    [
      "buy x get y free",
      BXGY_FREE,
      {
        en: ["Buy 2, get 1 free — the lowest-priced items are free"],
        zh: ["买 2 送 1，送价格最低的件"],
        ms: ["Beli 2, percuma 1 — item paling murah adalah percuma"],
      },
    ],
    [
      "buy x get y at a percentage",
      BXGY_HALF,
      {
        en: ["Buy 1, get 1 more at 50% off — the discount goes to the lowest-priced items"],
        zh: ["买 1 件，再 1 件减 50%，优惠给价格最低的件"],
        ms: ["Beli 1, 1 lagi diskaun 50% — diskaun untuk item paling murah"],
      },
    ],
    [
      "spend amount tiers",
      SPEND_AMOUNT,
      {
        en: ["Spend RM 50.00, save RM 5.00", "Spend RM 100.00, save RM 12.00"],
        zh: ["满 RM 50.00 减 RM 5.00", "满 RM 100.00 减 RM 12.00"],
        ms: ["Belanja RM 50.00, jimat RM 5.00", "Belanja RM 100.00, jimat RM 12.00"],
      },
    ],
    [
      "spend percent tiers",
      SPEND_PERCENT,
      {
        en: ["Spend RM 50.00, get 5% off", "Spend RM 100.00, get 10% off"],
        zh: ["满 RM 50.00 减 5%", "满 RM 100.00 减 10%"],
        ms: ["Belanja RM 50.00, diskaun 5%", "Belanja RM 100.00, diskaun 10%"],
      },
    ],
    [
      "free shipping from a threshold",
      FREE_SHIPPING,
      {
        en: ["Free sample shipping on orders from RM 60.00"],
        zh: ["满 RM 60.00 免示例运费"],
        ms: ["Penghantaran contoh percuma untuk pesanan dari RM 60.00"],
      },
    ],
    [
      "free shipping on all orders",
      FREE_SHIPPING_ALL,
      {
        en: ["Free sample shipping on all orders"],
        zh: ["全部订单免示例运费"],
        ms: ["Penghantaran contoh percuma untuk semua pesanan"],
      },
    ],
  ])("describes the %s rule in all three languages", (_name, given, expected) => {
    expect(ruleTexts("en", given)).toEqual(expected.en);
    expect(ruleTexts("zh", given)).toEqual(expected.zh);
    expect(ruleTexts("ms", given)).toEqual(expected.ms);
  });

  // DESIGN 1.23 第 10 条「不用 AI 或管理员写的文字描述规则，保证说明与计价一致」：没有模板的类别不编造说明。
  it("describes nothing for an unknown kind", () => {
    expect(ruleTexts("en", UNKNOWN)).toEqual([]);
  });
});

describe("scopeText", () => {
  const names: Record<Language, CategoryDisplay[]> = {
    en: [
      { slug: "home", name: "Home" },
      { slug: "bags", name: "Bags" },
    ],
    zh: [
      { slug: "home", name: "家居" },
      { slug: "bags", name: "包袋" },
    ],
    ms: [
      { slug: "home", name: "Rumah" },
      { slug: "bags", name: "Beg" },
    ],
  };

  // UX 0.20 P15「商品级规则含分类时，说明后附 [promo.rule_scope]（{names} 为分类名称）」；
  // 分类名称按接口给的顺序（不重排），中文用顿号、英文与马来文用逗号加空格分隔。
  it("lists the category names in the given order with the language's separator", () => {
    for (const language of ["en", "zh", "ms"] as const) {
      const given = rule({ ...ITEM_PERCENT, categories: names[language] });
      expect(scopeText(language, given)).toBe(
        { en: "Applies to: Home, Bags", zh: "适用：家居、包袋", ms: "Untuk: Rumah, Beg" }[language],
      );
    }
  });

  // UX 0.20 P15「同时含单件商品时改用 [promo.rule_scope_with_items]」。
  it("mentions the items below when the rule also picks products", () => {
    for (const language of ["en", "zh", "ms"] as const) {
      const given = rule({ ...BXGY_FREE, categories: names[language], has_products: true });
      expect(scopeText(language, given)).toBe(
        {
          en: "Applies to: Home, Bags, and the items below",
          zh: "适用：家居、包袋，以及下方列出的商品",
          ms: "Untuk: Rumah, Beg, dan item di bawah",
        }[language],
      );
    }
  });

  // UX 0.20 P15「只含单件商品时不附范围」；整单级规则没有分类，同样不附。
  it("gives no scope without categories", () => {
    expect(scopeText("en", rule({ ...ITEM_PERCENT, has_products: true }))).toBeNull();
    expect(scopeText("zh", SPEND_AMOUNT)).toBeNull();
    expect(scopeText("ms", FREE_SHIPPING_ALL)).toBeNull();
  });

  // UX 0.20 P15「商品级规则含分类时，说明后附 [promo.rule_scope]（{names} 为分类名称）」：只有一个分类时没有分隔符。
  it("writes a single category without a separator", () => {
    expect(scopeText("zh", rule({ ...ITEM_AMOUNT, categories: [{ slug: "home", name: "家居" }] }))).toBe("适用：家居");
  });
});

describe("countdownText", () => {
  const DAY_MS = 86_400_000;

  // UX-COPY promo.countdown_days「剩余 1 天以上」，守住 UX 0.20「节日促销活动」「倒计时以服务端返回的结束时间与服务端当前时间之差为准」：恰好 1 天整即带天数。
  it("shows days from exactly one day", () => {
    expect(countdownText("en", DAY_MS)).toBe("Ends in 1d 00:00:00");
    expect(countdownText("zh", DAY_MS)).toBe("距结束还有 1 天 00:00:00");
    expect(countdownText("ms", DAY_MS)).toBe("Tamat dalam 1 hari 00:00:00");
  });

  // UX 0.20「节日促销活动」「倒计时以服务端返回的结束时间与服务端当前时间之差为准」：天数之外的时、分、秒各两位。
  it("pads hours, minutes and seconds after the days", () => {
    const remaining = ((2 * 24 + 3) * 3600 + 4 * 60 + 5) * 1000;
    expect(countdownText("en", remaining)).toBe("Ends in 2d 03:04:05");
    expect(countdownText("zh", remaining)).toBe("距结束还有 2 天 03:04:05");
    expect(countdownText("ms", remaining)).toBe("Tamat dalam 2 hari 03:04:05");
  });

  // UX-COPY promo.countdown「剩余不足 1 天」，守住 UX 0.20「节日促销活动」的倒计时：不足 1 天不带天数，零点几秒向下取整。
  it.each([DAY_MS - 1000, DAY_MS - 1])("shows 23:59:59 for %i ms", (remaining) => {
    expect(countdownText("en", remaining)).toBe("Ends in 23:59:59");
    expect(countdownText("zh", remaining)).toBe("距结束还有 23:59:59");
    expect(countdownText("ms", remaining)).toBe("Tamat dalam 23:59:59");
  });

  // UX 0.20「节日促销活动」「到 0 时页面重新读取活动数据并去掉全部活动内容」：到 0 与已过结束时间都停在 00:00:00，不显示负数。
  it.each([0, 999, -1, -5000])("shows 00:00:00 for %i ms", (remaining) => {
    expect(countdownText("en", remaining)).toBe("Ends in 00:00:00");
    expect(countdownText("zh", remaining)).toBe("距结束还有 00:00:00");
    expect(countdownText("ms", remaining)).toBe("Tamat dalam 00:00:00");
  });
});

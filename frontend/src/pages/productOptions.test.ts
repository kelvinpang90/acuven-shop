import { describe, expect, it } from "vitest";

import type { CardPromotion, ProductDetail, VariantDetail } from "../api/catalog";
import type { PromotionDisplay, RuleDisplay } from "../api/promotion";
import type { CartLine } from "../cart";
import { LANGUAGES, translate } from "../i18n/copy";
import {
  clampQuantity,
  detailPriceCopy,
  detailRuleLines,
  detailSalePriceCopy,
  initialSelection,
  isValueEnabled,
  purchaseState,
  quantityInputResult,
  selectedVariant,
  toggleValue,
} from "./productOptions";
import type { Selection } from "./productOptions";

function text(value: string) {
  return { text: value, english_fallback: false };
}

function variant(sku: string, options: Record<string, string>, price_sen = 3900, available_stock = 20): VariantDetail {
  return { sku, options, price_sen, available_stock, sale_price_sen: null };
}

function product(variants: VariantDetail[], overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    slug: "tee",
    name: text("Tee"),
    description: text("A tee."),
    category: { slug: "apparel", name: text("Apparel") },
    images: [],
    options: [
      { code: "color", name: text("Colour"), values: [{ code: "red", name: text("Red") }, { code: "blue", name: text("Blue") }] },
      { code: "size", name: text("Size"), values: [{ code: "s", name: text("S") }, { code: "m", name: text("M") }] },
    ],
    variants,
    max_per_order: 10,
    promotion: null,
    ...overrides,
  };
}

// 只有红/S 与蓝/M 两个启用 SKU。
const twoSkus = product([variant("tee-red-s", { color: "red", size: "s" }, 3900), variant("tee-blue-m", { color: "blue", size: "m" }, 4200)]);

const single = product([variant("tee-red-s", { color: "red", size: "s" }, 3900)]);

function enabled(item: ProductDetail, selection: Selection): Record<string, boolean> {
  const result: Record<string, boolean> = {};
  for (const option of item.options) {
    for (const value of option.values) {
      result[`${option.code}:${value.code}`] = isValueEnabled(item, selection, option.code, value.code);
    }
  }
  return result;
}

describe("option availability", () => {
  // SHOP-TASK-017 验收第 3 条「与已选的其他规格值组不成任何启用 SKU 的值禁用」（DESIGN COMPONENTS「Options」同句）：
  // 只有红/S 与蓝/M 时，选红与 S 后蓝和 M 都禁用。
  it("disables values that make no enabled SKU with the other chosen values", () => {
    let selection: Selection = {};
    expect(enabled(twoSkus, selection)).toEqual({ "color:red": true, "color:blue": true, "size:s": true, "size:m": true });
    selection = toggleValue(twoSkus, selection, "color", "red");
    expect(enabled(twoSkus, selection)).toEqual({ "color:red": true, "color:blue": true, "size:s": true, "size:m": false });
    selection = toggleValue(twoSkus, selection, "size", "s");
    expect(selection).toEqual({ color: "red", size: "s" });
    expect(enabled(twoSkus, selection)).toEqual({ "color:red": true, "color:blue": false, "size:s": true, "size:m": false });
  });

  // SHOP-TASK-017 验收第 3 条「再次点击某组已选的值即取消该组的选择（Kelvin 2026-10-03 决定，UX 未写）…访客因此总能退回去换选」：
  // 依次取消这两组的选择后蓝与 M 可选。
  it("lets the visitor deselect a group so blocked values become available again", () => {
    let selection: Selection = { color: "red", size: "s" };
    selection = toggleValue(twoSkus, selection, "color", "red");
    expect(selection).toEqual({ size: "s" });
    expect(enabled(twoSkus, selection)["size:m"]).toBe(true);
    selection = toggleValue(twoSkus, selection, "size", "s");
    expect(selection).toEqual({});
    expect(enabled(twoSkus, selection)).toEqual({ "color:red": true, "color:blue": true, "size:s": true, "size:m": true });
    selection = toggleValue(twoSkus, selection, "color", "blue");
    selection = toggleValue(twoSkus, selection, "size", "m");
    expect(selectedVariant(twoSkus, selection)?.sku).toBe("tee-blue-m");
  });

  // SHOP-TASK-017 验收第 3 条「只有一个启用 SKU 的商品直接选定」与「只有一个启用 SKU 的商品不取消」（Kelvin 2026-10-03 决定）。
  it("preselects the only SKU and does not deselect it", () => {
    const selection = initialSelection(single);
    expect(selection).toEqual({ color: "red", size: "s" });
    expect(toggleValue(single, selection, "color", "red")).toEqual(selection);
    expect(toggleValue(single, selection, "size", "s")).toEqual(selection);
    expect(selectedVariant(single, selection)?.sku).toBe("tee-red-s");
  });

  // SHOP-TASK-017 验收第 3 条「初始不预选」：多个启用 SKU 时一个都不选。
  it("starts with nothing chosen when there are several SKUs", () => {
    expect(initialSelection(twoSkus)).toEqual({});
    expect(selectedVariant(twoSkus, {})).toBeNull();
  });

  // UX P03 无规格商品（种子数据的 <slug>-std）：唯一的 SKU 直接选定。
  it("selects the only SKU of a product without options", () => {
    const plain = product([variant("bag-std", {}, 1800)], { options: [] });
    expect(selectedVariant(plain, initialSelection(plain))?.sku).toBe("bag-std");
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-017 验收第 3 条「…的值禁用」——禁用的值点了也不改变选择。
  it("ignores a click on a disabled value", () => {
    const selection: Selection = { color: "red", size: "s" };
    expect(toggleValue(twoSkus, selection, "size", "m")).toEqual(selection);
  });

  // 派生实现约束（实现选择）：守住 UX P03「选择颜色、尺寸等规格」——同组换一个可选的值即替换原来的值。
  it("replaces the value of a group with another available value", () => {
    const three = product([variant("r-s", { color: "red", size: "s" }), variant("r-m", { color: "red", size: "m" })]);
    expect(toggleValue(three, { color: "red", size: "s" }, "size", "m")).toEqual({ color: "red", size: "m" });
  });
});

describe("price before and after choosing", () => {
  // SHOP-TASK-017 验收第 3 条「选全前价格按 SHOP-TASK-015 的起价规则显示」（UX P02 M1：多于一个启用规格时一律 list.price_from）。
  it("shows the from-price until every option is chosen", () => {
    expect(detailPriceCopy(twoSkus, null)).toEqual(["list.price_from", { amount: "39.00" }]);
    const samePrice = product([variant("a", { color: "red", size: "s" }, 2500), variant("b", { color: "blue", size: "m" }, 2500)]);
    expect(detailPriceCopy(samePrice, null)).toEqual(["list.price_from", { amount: "25.00" }]);
  });

  // SHOP-TASK-017 验收第 3 条「选全后显示所选 SKU 的单价」（UX P03 M1「所选规格的 MYR 单价」）。
  it("shows the unit price of the chosen SKU once every option is chosen", () => {
    const chosen = selectedVariant(twoSkus, { color: "blue", size: "m" });
    expect(detailPriceCopy(twoSkus, chosen)).toEqual(["common.price_myr", { amount: "42.00" }]);
    expect(selectedVariant(twoSkus, { color: "blue" })).toBeNull();
  });

  // UX-COPY list.price_from 提示：只有一个规格时用 common.price_myr。
  it("shows the plain price for a single SKU", () => {
    expect(detailPriceCopy(single, null)).toEqual(["common.price_myr", { amount: "39.00" }]);
  });
});

describe("purchase limit and cart size", () => {
  const cartWith = (...items: CartLine[]) => items;

  // UX P03「数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」：同一商品各 SKU 合计，其他商品不计。
  it("caps the quantity at the limit minus every SKU of the product in the cart", () => {
    const cart = cartWith(
      { sku: "tee-red-s", slug: "tee", quantity: 3 },
      { sku: "tee-blue-m", slug: "tee", quantity: 4 },
      { sku: "bag-std", slug: "bag", quantity: 9 },
    );
    expect(purchaseState(twoSkus, null, cart)).toEqual({ remaining: 3, block: null });
    expect(clampQuantity(5, 3)).toBe(3);
    expect(clampQuantity(0, 3)).toBe(1);
  });

  // UX P03「该商品已达限购时加入按钮禁用并显示 [detail.limit_reached]」：剩余为 0（各 SKU 合计达到限购）。
  it("blocks adding once the product has reached its limit", () => {
    const cart = cartWith({ sku: "tee-red-s", slug: "tee", quantity: 6 }, { sku: "tee-blue-m", slug: "tee", quantity: 4 });
    expect(purchaseState(twoSkus, null, cart)).toEqual({ remaining: 0, block: "limit_reached" });
    expect(purchaseState(twoSkus, twoSkus.variants[1] ?? null, cart).block).toBe("limit_reached");
    expect(clampQuantity(1, 0)).toBe(1);
  });

  // UX P03「购物车已有 20 行且所选规格不在其中时加入按钮禁用并显示 [detail.cart_full]」：所选 SKU 已在其中时不禁用。
  it("blocks a new SKU when the cart already has twenty lines", () => {
    const full = Array.from({ length: 19 }, (_, index) => ({ sku: `x-${String(index)}`, slug: `x-${String(index)}`, quantity: 1 }));
    const withRed = [...full, { sku: "tee-red-s", slug: "tee", quantity: 1 }];
    const red = selectedVariant(twoSkus, { color: "red", size: "s" });
    const blue = selectedVariant(twoSkus, { color: "blue", size: "m" });
    expect(purchaseState(twoSkus, blue, withRed)).toEqual({ remaining: 9, block: "cart_full" });
    expect(purchaseState(twoSkus, red, withRed)).toEqual({ remaining: 9, block: null });
    expect(purchaseState(twoSkus, null, withRed).block).toBeNull();
    expect(purchaseState(twoSkus, blue, full).block).toBeNull();
  });

  // docs/UX.md P03「整单件数上限」「数量…的上限取限购件数减去购物车中该商品已有的件数与 2,000 减去购物车全部行的件数中较小的」。
  it("caps the quantity at the smaller of the product limit and the whole-cart room", () => {
    const big = product(twoSkus.variants, { max_per_order: 1000 });
    const others = [
      { sku: "bag-std", slug: "bag", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 995 },
    ];
    expect(purchaseState(big, null, others)).toEqual({ remaining: 5, block: null });
    const withTee = [{ sku: "tee-red-s", slug: "tee", quantity: 997 }, { sku: "bag-std", slug: "bag", quantity: 100 }];
    expect(purchaseState(big, null, withTee)).toEqual({ remaining: 3, block: null });
    expect(purchaseState(twoSkus, null, others)).toEqual({ remaining: 5, block: null });
    expect(purchaseState(twoSkus, null, [{ sku: "bag-std", slug: "bag", quantity: 1000 }])).toEqual({ remaining: 10, block: null });
  });

  // docs/UX.md P03「后者为 0 时加入按钮禁用并显示 [detail.cart_units_full]」：购物车全部行合计已达 2,000 件（不小于 0）。
  it("blocks adding once the whole cart holds 2,000 items", () => {
    const full = [
      { sku: "bag-std", slug: "bag", quantity: 1000 },
      { sku: "mug-std", slug: "mug", quantity: 1000 },
    ];
    expect(purchaseState(twoSkus, null, full)).toEqual({ remaining: 0, block: "cart_units_full" });
    expect(purchaseState(twoSkus, twoSkus.variants[0] ?? null, full)).toEqual({ remaining: 0, block: "cart_units_full" });
    const over = [...full, { sku: "cup-std", slug: "cup", quantity: 7 }];
    expect(purchaseState(twoSkus, null, over)).toEqual({ remaining: 0, block: "cart_units_full" });
  });

  // docs/UX.md P03「与另两种禁用原因同时满足时只显示一条，依次为 [detail.limit_reached]、[detail.cart_units_full]、[detail.cart_full]」。
  it("gives one reason in the order limit reached, whole cart full, twenty lines", () => {
    const blue = selectedVariant(twoSkus, { color: "blue", size: "m" });
    // 20 行（不含蓝/M），其中本商品红/S 10 件（达到限购），合计 2,000 件：三种原因同时满足。
    const allThree = [
      { sku: "tee-red-s", slug: "tee", quantity: 10 },
      { sku: "x-0", slug: "x-0", quantity: 1000 },
      { sku: "x-1", slug: "x-1", quantity: 973 },
      ...Array.from({ length: 17 }, (_, index) => ({ sku: `y-${String(index)}`, slug: `y-${String(index)}`, quantity: 1 })),
    ];
    expect(allThree).toHaveLength(20);
    expect(allThree.reduce((sum, item) => sum + item.quantity, 0)).toBe(2000);
    expect(purchaseState(twoSkus, blue, allThree)).toEqual({ remaining: 0, block: "limit_reached" });
    // 限购未满（红/S 9 件），整单 2,000 件且 20 行：只给整单已满。
    const unitsAndLines = allThree.map((item) => (item.sku === "tee-red-s" ? { ...item, quantity: 9 } : item.sku === "x-1" ? { ...item, quantity: 974 } : item));
    expect(purchaseState(twoSkus, blue, unitsAndLines)).toEqual({ remaining: 0, block: "cart_units_full" });
    // 整单未满（少 1 件）且 20 行：只给 20 行已满。
    const linesOnly = unitsAndLines.map((item) => (item.sku === "x-1" ? { ...item, quantity: 973 } : item));
    expect(purchaseState(twoSkus, blue, linesOnly)).toEqual({ remaining: 1, block: "cart_full" });
  });
});

describe("typing a quantity", () => {
  // docs/UX.md P03「数量可直接输入」「为空、小于 1 或不是整数时恢复为原来的件数」：件数不变，不显示 common.qty_capped。
  it.each(["", "  ", "0", "-2", "1.5", "1e2", "３"])("restores the previous quantity for %j", (text) => {
    expect(quantityInputResult(text, 4, 10)).toEqual({ quantity: 4, capped: false });
  });

  // 同上「超过上限时改为上限，并在数量下方显示 [common.qty_capped]」；不超过时照收。
  it("takes a quantity within the cap and caps one above it", () => {
    expect(quantityInputResult(" 7 ", 4, 10)).toEqual({ quantity: 7, capped: false });
    expect(quantityInputResult("10", 4, 10)).toEqual({ quantity: 10, capped: false });
    expect(quantityInputResult("11", 4, 10)).toEqual({ quantity: 10, capped: true });
    expect(quantityInputResult("25", 4, 10)).toEqual({ quantity: 10, capped: true });
  });

  // 任务契约「remaining 为 0 时恢复为原来的件数（按钮已禁用）」：不改成 0 件，也不显示 common.qty_capped。
  it("keeps the previous quantity when nothing more can be added", () => {
    expect(quantityInputResult("3", 1, 0)).toEqual({ quantity: 1, capped: false });
  });
});

function saleVariant(sku: string, options: Record<string, string>, price_sen: number, sale_price_sen: number | null): VariantDetail {
  return { ...variant(sku, options, price_sen), sale_price_sen };
}

function cardRule(rule_id: number, kind: string): CardPromotion {
  return { rule_id, kind, discount_mode: "percent", percent: 20, amount_sen: null, buy_quantity: 2, get_quantity: 1, get_percent: 100 };
}

const ITEM_RULE = cardRule(11, "item_discount");
const BXGY_RULE = cardRule(14, "buy_x_get_y");

// 红/S RM 39.00 → 31.20，蓝/M RM 42.00 → 33.60（接口给的活动单价）。
const onSale = product([
  saleVariant("tee-red-s", { color: "red", size: "s" }, 3900, 3120),
  saleVariant("tee-blue-m", { color: "blue", size: "m" }, 4200, 3360),
]);

const singleOnSale = product([saleVariant("tee-red-s", { color: "red", size: "s" }, 3900, 3120)]);

describe("sale price in the price ribbon", () => {
  // UX 0.20 P03「价格区按 detail_ribbon 的变体显示所选规格的划线原价与活动单价（命中打折规则时）」：
  // 选全后为所选 SKU 的原价与活动单价，都按 common.price_myr，三种语言都不加「起」。
  it.each(LANGUAGES)("shows the chosen SKU's original and sale price in %s", (language) => {
    const copy = detailSalePriceCopy(onSale, selectedVariant(onSale, { color: "blue", size: "m" }), ITEM_RULE);
    expect(copy).toEqual({ original: ["common.price_myr", { amount: "42.00" }], sale: ["common.price_myr", { amount: "33.60" }] });
    expect(copy && translate(language, ...copy.original)).toBe(translate(language, "common.price_myr", { amount: "42.00" }));
    expect(copy && translate(language, ...copy.sale)).toBe(translate(language, "common.price_myr", { amount: "33.60" }));
  });

  // UX 0.20 P03「规则同「1. 全局框架」的商品卡」——商品卡「多于一个启用规格时，活动价取最低活动单价并照原规则加「起」；划线原价……不加「起」」：
  // 选全前划线价为启用 SKU 的最低单价（common.price_myr），活动价为最低活动单价加 list.price_from。
  it.each(LANGUAGES)("shows the lowest prices with a from-price sale before every option is chosen in %s", (language) => {
    const copy = detailSalePriceCopy(onSale, null, ITEM_RULE);
    expect(copy).toEqual({ original: ["common.price_myr", { amount: "39.00" }], sale: ["list.price_from", { amount: "31.20" }] });
    expect(copy && translate(language, ...copy.sale)).toBe(translate(language, "list.price_from", { amount: "31.20" }));
  });

  // 同上「照原规则用 [common.price_myr] 或 [list.price_from]」：只有一个启用 SKU 时活动价也不加「起」。
  it("shows a plain sale price for a single SKU", () => {
    const expected = { original: ["common.price_myr", { amount: "39.00" }], sale: ["common.price_myr", { amount: "31.20" }] };
    expect(detailSalePriceCopy(singleOnSale, null, ITEM_RULE)).toEqual(expected);
    expect(detailSalePriceCopy(singleOnSale, singleOnSale.variants[0] ?? null, ITEM_RULE)).toEqual(expected);
  });

  // UX 0.20「商品卡价格与角标」：「买 X 享 Y 为 [promo.badge_bxgy_free]……价格不变」；没有命中或没有活动单价时也不划线。
  it("gives no sale price for buy X get Y, without a hit or without a sale price", () => {
    expect(detailSalePriceCopy(onSale, null, BXGY_RULE)).toBeNull();
    expect(detailSalePriceCopy(onSale, null, null)).toBeNull();
    expect(detailSalePriceCopy(twoSkus, null, ITEM_RULE)).toBeNull();
    expect(detailSalePriceCopy(twoSkus, twoSkus.variants[0] ?? null, ITEM_RULE)).toBeNull();
  });

  // 任务契约「现有 detailPriceCopy 不变」：有活动单价时现有价格文案照旧。
  it("leaves the plain price copy unchanged", () => {
    expect(detailPriceCopy(onSale, null)).toEqual(["list.price_from", { amount: "39.00" }]);
    expect(detailPriceCopy(onSale, selectedVariant(onSale, { color: "blue", size: "m" }))).toEqual(["common.price_myr", { amount: "42.00" }]);
  });
});

function ruleDisplay(rule_id: number, position: number, overrides: Partial<RuleDisplay>): RuleDisplay {
  return {
    rule_id,
    position,
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

const ITEM_DISPLAY = ruleDisplay(11, 2, { discount_mode: "percent", percent: 20, categories: [{ slug: "apparel", name: "Apparel" }] });
const BXGY_DISPLAY = ruleDisplay(14, 4, { kind: "buy_x_get_y", buy_quantity: 2, get_quantity: 1, get_percent: 100 });
const SHIPPING_DISPLAY = ruleDisplay(13, 1, { kind: "free_shipping", threshold_sen: 12000 });
const SPEND_DISPLAY = ruleDisplay(12, 3, {
  kind: "spend_discount",
  tiers: [
    { threshold_sen: 15000, amount_sen: 1500, percent: null },
    { threshold_sen: 30000, amount_sen: 4000, percent: null },
  ],
});

function promotionWith(rules: RuleDisplay[]): PromotionDisplay {
  return {
    promotion_id: 7,
    title: "Lunar New Year Sale",
    subtitle: null,
    badge: null,
    strip: null,
    pack: "cny",
    palette: "cny",
    ambient: "none",
    home_poster: "split",
    list_strip: "band",
    card_badge: "tag",
    detail_ribbon: "ribbon",
    cart_progress: "bar",
    poster_mode: "template",
    poster_desktop: null,
    poster_phone: null,
    poster_alt: null,
    ends_at: null,
    coupon_combinable: true,
    points_combinable: true,
    rules,
  };
}

describe("rule lines of the sale area", () => {
  const all = promotionWith([SHIPPING_DISPLAY, ITEM_DISPLAY, SPEND_DISPLAY, BXGY_DISPLAY]);

  // UX 0.20 P03「先列本商品命中的商品级规则说明（没有命中时不列），再列满额减或满额折的各档与免运费」：
  // 本商品的打折规则在前（不附范围），其后按 position 为免运费（1）与满额减两档（3）；别的商品的买 X 享 Y 不列。
  it.each(LANGUAGES)("lists the product's rule first, then the order-level rules by position in %s", (language) => {
    expect(detailRuleLines(language, all, ITEM_RULE)).toEqual([
      translate(language, "promo.rule_item_percent", { percent: 20 }),
      translate(language, "promo.rule_free_shipping", { threshold: "120.00" }),
      translate(language, "promo.rule_tier_amount", { threshold: "150.00", amount: "15.00" }),
      translate(language, "promo.rule_tier_amount", { threshold: "300.00", amount: "40.00" }),
    ]);
  });

  // 同上「再列满额减或满额折的各档与免运费」：接口顺序不是 position 顺序时也按 position 排。
  it("orders the order-level rules by position", () => {
    const shuffled = promotionWith([SPEND_DISPLAY, SHIPPING_DISPLAY]);
    expect(detailRuleLines("en", shuffled, null)).toEqual([
      translate("en", "promo.rule_free_shipping", { threshold: "120.00" }),
      translate("en", "promo.rule_tier_amount", { threshold: "150.00", amount: "15.00" }),
      translate("en", "promo.rule_tier_amount", { threshold: "300.00", amount: "40.00" }),
    ]);
  });

  // 同上：命中买 X 享 Y 时它的说明在前。
  it("puts a buy X get Y rule of the product first", () => {
    expect(detailRuleLines("zh", all, BXGY_RULE)[0]).toBe(translate("zh", "promo.rule_bxgy_free", { x: 2, y: 1 }));
  });

  // 同上「没有命中时不列」，以及 HANDOFF 0.97「规则区的整单级部分只要有活动就显示」：没有命中或规则不在当前活动里时只列整单级规则。
  it("lists only the order-level rules without a hit in this promotion", () => {
    const orderOnly = detailRuleLines("en", all, null);
    expect(orderOnly).toHaveLength(3);
    expect(detailRuleLines("en", all, cardRule(99, "item_discount"))).toEqual(orderOnly);
  });

  // UX 0.20 P03「两者都没有时整区不显示」与「没有生效活动时各页与 0.19 完全相同」：没有活动、或只有别的商品的规则且本商品没有命中时为空。
  it("has no lines without a promotion or without any rule to show", () => {
    expect(detailRuleLines("en", null, ITEM_RULE)).toEqual([]);
    expect(detailRuleLines("en", promotionWith([BXGY_DISPLAY]), null)).toEqual([]);
    expect(detailRuleLines("en", promotionWith([BXGY_DISPLAY]), ITEM_RULE)).toEqual([]);
  });
});

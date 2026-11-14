import { describe, expect, it } from "vitest";

import type { ProductDetail, VariantDetail } from "../api/catalog";
import type { CartLine } from "../cart";
import {
  clampQuantity,
  detailPriceCopy,
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

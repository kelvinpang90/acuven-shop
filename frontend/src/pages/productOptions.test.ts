import { describe, expect, it } from "vitest";

import type { ProductDetail, ProductVariant } from "../api/catalog";
import type { CartLine } from "../cart";
import {
  addRequest,
  initialSelection,
  isComplete,
  isValueAvailable,
  priceCopy,
  purchaseState,
  selectedVariant,
  toggleValue,
} from "./productOptions";

function text(value: string) {
  return { text: value, english_fallback: false };
}

function variant(sku: string, colour: string, size: string, price_sen = 3900, available_stock = 20): ProductVariant {
  return { sku, options: { colour, size }, price_sen, available_stock };
}

// 颜色 black/white/navy/red，尺寸 s/m/l；启用 SKU 只有 black-s、black-m、white-m、navy-l（red 没有启用 SKU）。
function tee(variants: ProductVariant[] = [
  variant("TEE-BLK-S", "black", "s", 3900),
  variant("TEE-BLK-M", "black", "m", 4200),
  variant("TEE-WHT-M", "white", "m", 3500),
  variant("TEE-NVY-L", "navy", "l", 4500, 3),
]): ProductDetail {
  return {
    slug: "tee",
    name: text("Crew Neck Tee"),
    description: text("A soft tee."),
    category: { slug: "apparel", name: text("Apparel") },
    images: [],
    options: [
      { code: "colour", name: text("Colour"), values: ["black", "white", "navy", "red"].map((code) => ({ code, name: text(code) })) },
      { code: "size", name: text("Size"), values: ["s", "m", "l"].map((code) => ({ code, name: text(code) })) },
    ],
    variants,
    max_per_order: 10,
  };
}

function line(sku: string, quantity: number, slug = "tee"): CartLine {
  return { sku, slug, quantity };
}

describe("initial selection", () => {
  // SHOP-TASK-017 验收第 4 条「初始不预选」。
  it("selects nothing when there are several SKUs", () => {
    expect(initialSelection(tee())).toEqual({});
  });

  // SHOP-TASK-017 验收第 4 条「只有一个启用 SKU 的商品直接选定」。
  it("selects the only SKU straight away", () => {
    const single = tee([variant("TEE-BLK-S", "black", "s")]);
    const selection = initialSelection(single);
    expect(selection).toEqual({ colour: "black", size: "s" });
    expect(selectedVariant(single, selection)?.sku).toBe("TEE-BLK-S");
  });

  // 同一条：没有规格名、只有一个 SKU 的商品也直接选定。
  it("selects the only SKU of a product without options", () => {
    const plain = { ...tee([{ sku: "MUG", options: {}, price_sen: 2500, available_stock: 4 }]), options: [] };
    expect(selectedVariant(plain, initialSelection(plain))?.sku).toBe("MUG");
  });
});

describe("option availability", () => {
  // docs/design/COMPONENTS.md「Options」与 SHOP-TASK-017 验收第 4 条：与已选的其他规格值组不成任何启用 SKU 的值禁用。
  it("disables values that make no active SKU with the other chosen values", () => {
    const product = tee();
    const navy = { colour: "navy" };
    expect(isValueAvailable(product, navy, "size", "s")).toBe(false);
    expect(isValueAvailable(product, navy, "size", "m")).toBe(false);
    expect(isValueAvailable(product, navy, "size", "l")).toBe(true);
    const small = { size: "s" };
    expect(isValueAvailable(product, small, "colour", "black")).toBe(true);
    expect(isValueAvailable(product, small, "colour", "white")).toBe(false);
    expect(isValueAvailable(product, small, "colour", "navy")).toBe(false);
  });

  // 同一条：没有任何启用 SKU 的值（red）在什么都没选时也禁用；其余都可选。
  it("disables a value without any active SKU", () => {
    const product = tee();
    expect(isValueAvailable(product, {}, "colour", "red")).toBe(false);
    for (const code of ["black", "white", "navy"]) {
      expect(isValueAvailable(product, {}, "colour", code)).toBe(true);
    }
  });

  // 同一条「与已选的其他规格值组」：同一规格名下的已选值不限制换成别的值。
  it("does not restrict a group by its own chosen value", () => {
    expect(isValueAvailable(tee(), { colour: "black", size: "m" }, "colour", "white")).toBe(true);
    expect(isValueAvailable(tee(), { colour: "black", size: "m" }, "colour", "navy")).toBe(false);
  });

  // COMPONENTS「Options」chips 是 aria-pressed 按钮：点未选的值选上（替换同组原值），再点取消。
  it("toggles a value and replaces the value of the same group", () => {
    expect(toggleValue({}, "colour", "black")).toEqual({ colour: "black" });
    expect(toggleValue({ colour: "black", size: "m" }, "colour", "white")).toEqual({ colour: "white", size: "m" });
    expect(toggleValue({ colour: "black", size: "m" }, "colour", "black")).toEqual({ size: "m" });
  });
});

describe("price before and after choosing", () => {
  // SHOP-TASK-017 验收第 4 条「选全前价格按 SHOP-TASK-015 的起价规则显示」（UX P01、P02 M1 0.4：多于一个启用规格一律 list.price_from）。
  it("shows the from-price of the cheapest SKU until every option is chosen", () => {
    const product = tee();
    expect(isComplete(product, { colour: "black" })).toBe(false);
    expect(selectedVariant(product, { colour: "black" })).toBeNull();
    expect(priceCopy(product, null)).toEqual(["list.price_from", { amount: "35.00" }]);
  });

  // 同一条：各 SKU 同价时也显示「起」。
  it("still shows the from-price when every SKU has the same price", () => {
    const same = tee([variant("A", "black", "s", 3900), variant("B", "white", "m", 3900)]);
    expect(priceCopy(same, null)).toEqual(["list.price_from", { amount: "39.00" }]);
  });

  // UX P03 M1「所选规格的 MYR 单价」与 SHOP-TASK-017 验收第 4 条「选全后显示所选 SKU 的单价」。
  it("shows the unit price of the chosen SKU once complete", () => {
    const product = tee();
    const chosen = selectedVariant(product, { colour: "black", size: "m" });
    expect(chosen?.sku).toBe("TEE-BLK-M");
    expect(priceCopy(product, chosen)).toEqual(["common.price_myr", { amount: "42.00" }]);
  });
});

describe("per-order limit and cart size", () => {
  const product = tee();
  const blackM = variant("TEE-BLK-M", "black", "m");

  // UX P03 0.6「数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」：按 slug 合计同一商品各 SKU。
  it("caps the quantity at the limit minus what the cart already holds of the product", () => {
    const cart = [line("TEE-BLK-S", 3), line("TEE-WHT-M", 4), line("MUG", 9, "mug")];
    const state = purchaseState(product, blackM, cart, 8);
    expect(state.remaining).toBe(3);
    expect(state.quantity).toBe(3);
    expect(state.block).toBeNull();
    expect(purchaseState(product, blackM, cart, 0).quantity).toBe(1);
  });

  // UX P03 0.6「该商品已达限购时加入按钮禁用并显示 detail.limit_reached」：与是否选全无关。
  it("blocks adding when the product has reached its limit", () => {
    const cart = [line("TEE-BLK-S", 6), line("TEE-NVY-L", 4)];
    expect(purchaseState(product, blackM, cart, 1)).toEqual({ remaining: 0, quantity: 1, block: "limit_reached" });
    expect(purchaseState(product, null, cart, 1).block).toBe("limit_reached");
  });

  // UX P03 0.6「购物车已有 20 行且所选规格不在其中时加入按钮禁用并显示 detail.cart_full」。
  it("blocks a new SKU when the cart already has 20 lines", () => {
    const full = Array.from({ length: 20 }, (_, index) => line(`OTHER-${String(index)}`, 1, "other"));
    expect(purchaseState(product, blackM, full, 1).block).toBe("cart_full");
    const withBlackM = [...full.slice(0, 19), line("TEE-BLK-M", 1)];
    expect(purchaseState(product, blackM, withBlackM, 1).block).toBeNull();
    expect(purchaseState(product, null, full, 1).block).toBeNull();
  });

  // UX P03「未选全规格点加入：detail.select_all_options」。
  it("asks for every option before adding", () => {
    const state = purchaseState(product, null, [], 1);
    expect(addRequest(product, null, state)).toBe("select_all_options");
  });

  // 加入的一行只有 SKU、商品 slug 与件数（SHOP-TASK-017 验收第 5 条）；按钮禁用时什么也不加。
  it("adds the chosen SKU with the shown quantity, and nothing while blocked", () => {
    const state = purchaseState(product, blackM, [line("TEE-BLK-S", 8)], 5);
    expect(addRequest(product, blackM, state)).toEqual({ sku: "TEE-BLK-M", slug: "tee", quantity: 2 });
    const blocked = purchaseState(product, blackM, [line("TEE-BLK-S", 10)], 1);
    expect(addRequest(product, blackM, blocked)).toBeNull();
  });
});

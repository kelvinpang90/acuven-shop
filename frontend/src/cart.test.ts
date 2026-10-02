import { describe, expect, it } from "vitest";

import {
  CART_MAX_LINES,
  CART_STORAGE_KEY,
  addToCart,
  isCartFullFor,
  productQuantity,
  readCart,
  remainingAllowance,
  sanitizeCart,
  writeCart,
} from "./cart";
import type { CartLine, CartStorage } from "./cart";

// localStorage 的替身：只有 getItem 与 setItem。
function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  const storage: CartStorage = {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
  return { storage, data };
}

const brokenStorage: CartStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
};

function line(sku: string, quantity = 1, slug = "tee"): CartLine {
  return { sku, slug, quantity };
}

function lines(count: number, slug = "other"): CartLine[] {
  return Array.from({ length: count }, (_, index) => line(`SKU-${String(index)}`, 1, slug));
}

describe("cart storage format", () => {
  // SHOP-TASK-017 验收第 5 条「每行只有 SKU、商品 slug 与件数（不存价格、名称或任何个人资料）」，
  // 依据 UX「阅读说明」「不在浏览器计算价格」与 DESIGN「数据模型」Cart（行小计由服务端按当前价格返回，不信任浏览器保存的价格）。
  it("stores only the SKU, the product slug and the quantity of each line", () => {
    const { storage, data } = memoryStorage();
    const withExtras = [{ sku: "TEE-BLK-M", slug: "tee", quantity: 2, price_sen: 3900, name: "Tee", phone: "0123" }];
    expect(writeCart(storage, sanitizeCart(withExtras))).toBe(true);
    const stored: unknown = JSON.parse(data.get(CART_STORAGE_KEY) ?? "null");
    expect(stored).toEqual([{ sku: "TEE-BLK-M", slug: "tee", quantity: 2 }]);
  });

  // SHOP-TASK-017 验收第 5 条：读回写入的内容。
  it("reads back what was written", () => {
    const { storage } = memoryStorage();
    writeCart(storage, [line("A", 3), line("B", 1, "mug")]);
    expect(readCart(storage)).toEqual([line("A", 3), line("B", 1, "mug")]);
  });
});

describe("cart sanitising on read", () => {
  // SHOP-TASK-017 验收第 5 条「读取时丢弃格式不对…的部分」：不是 JSON、不是数组时为空购物车。
  it.each(["not json", "{}", '"text"', "null", "42"])("treats %j as an empty cart", (raw) => {
    expect(readCart(memoryStorage({ [CART_STORAGE_KEY]: raw }).storage)).toEqual([]);
  });

  // SHOP-TASK-017 验收第 5 条「丢弃格式不对、件数不在 1 到 99…的部分」：坏行丢弃，好行保留。
  it("drops malformed lines and quantities outside 1 to 99", () => {
    const raw = [
      line("OK-1", 1),
      { sku: "NO-SLUG", quantity: 1 },
      { slug: "tee", quantity: 1 },
      { sku: "", slug: "tee", quantity: 1 },
      { sku: 7, slug: "tee", quantity: 1 },
      line("ZERO", 0),
      line("TOO-MANY", 100),
      line("FRACTION", 1.5),
      { sku: "TEXT", slug: "tee", quantity: "2" },
      null,
      ["ARRAY", "tee", 1],
      line("OK-99", 99),
    ];
    expect(sanitizeCart(raw)).toEqual([line("OK-1", 1), line("OK-99", 99)]);
  });

  // SHOP-TASK-017 验收第 5 条「丢弃…重复 SKU」：同一 SKU 只留最先出现的一行。
  it("keeps only the first line of a repeated SKU", () => {
    expect(sanitizeCart([line("A", 2), line("B", 1), line("A", 5)])).toEqual([line("A", 2), line("B", 1)]);
  });

  // SHOP-TASK-017 验收第 5 条「丢弃…超过 20 行的部分」（UX 0.6 购物车 20 行上限）：只留前 20 行。
  it("keeps at most the first 20 lines", () => {
    const stored = lines(25);
    const { storage } = memoryStorage({ [CART_STORAGE_KEY]: JSON.stringify(stored) });
    expect(readCart(storage)).toEqual(stored.slice(0, CART_MAX_LINES));
  });
});

describe("cart storage failures", () => {
  // SHOP-TASK-017 验收第 5 条「读写失败时页面照常可用」：读取抛错当作空购物车，写入抛错不往外抛。
  it("reads an empty cart and ignores write errors when storage throws", () => {
    expect(readCart(brokenStorage)).toEqual([]);
    expect(writeCart(brokenStorage, [line("A")])).toBe(false);
  });

  // 同一条：取不到 localStorage（服务端渲染、被禁用）时同样可用。
  it("works without any storage", () => {
    expect(readCart(null)).toEqual([]);
    expect(writeCart(null, [line("A")])).toBe(false);
  });
});

describe("adding to the cart", () => {
  // UX P03 0.6「所选规格已在购物车中时加入即合并件数」与 SHOP-TASK-017 验收第 5 条「同一 SKU 再次加入时合并件数」。
  it("merges the quantity of a SKU already in the cart", () => {
    const result = addToCart([line("A", 2), line("B", 1)], line("A", 3));
    expect(result).toEqual({ ok: true, lines: [line("A", 5), line("B", 1)] });
  });

  // 新 SKU 追加为新的一行。
  it("appends a new SKU as a new line", () => {
    expect(addToCart([line("A", 2)], line("B", 1, "mug"))).toEqual({ ok: true, lines: [line("A", 2), line("B", 1, "mug")] });
  });

  // UX P03 0.6「购物车已有 20 行且所选规格不在其中时」不能加入；已在其中的 SKU 仍可合并。
  it("refuses a 21st line but still merges into one of the 20", () => {
    const full = lines(CART_MAX_LINES);
    expect(addToCart(full, line("NEW"))).toEqual({ ok: false, reason: "cart_full" });
    const merged = addToCart(full, line("SKU-3", 2, "other"));
    expect(merged.ok && merged.lines.find((item) => item.sku === "SKU-3")?.quantity).toBe(3);
    expect(isCartFullFor(full, "NEW")).toBe(true);
    expect(isCartFullFor(full, "SKU-3")).toBe(false);
    expect(isCartFullFor(lines(CART_MAX_LINES - 1), "NEW")).toBe(false);
  });

  // SHOP-TASK-017 验收第 5 条「件数不在 1 到 99」：合并后也不超过 99，不合格的加入被拒绝。
  it("refuses quantities that would leave 1 to 99", () => {
    expect(addToCart([line("A", 98)], line("A", 2))).toEqual({ ok: false, reason: "invalid" });
    expect(addToCart([], line("A", 0))).toEqual({ ok: false, reason: "invalid" });
  });
});

describe("per-order limit", () => {
  // UX P03 0.6「同一商品所有规格在本浏览器购物车中的件数合计计入限购；数量 (+) 的上限是限购件数减去购物车中该商品已有的件数」。
  it("counts every SKU of the same product against the limit", () => {
    const cart = [line("TEE-S", 2, "tee"), line("MUG", 4, "mug"), line("TEE-M", 3, "tee")];
    expect(productQuantity(cart, "tee")).toBe(5);
    expect(remainingAllowance(cart, "tee", 10)).toBe(5);
    expect(remainingAllowance(cart, "mug", 10)).toBe(6);
    expect(remainingAllowance(cart, "bag", 10)).toBe(10);
  });

  // UX P03 0.6「该商品已达限购时」：剩余为 0，不出现负数。
  it("never goes below zero", () => {
    expect(remainingAllowance([line("TEE-S", 6), line("TEE-M", 6)], "tee", 10)).toBe(0);
  });
});

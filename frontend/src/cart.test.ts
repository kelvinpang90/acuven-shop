import { describe, expect, it } from "vitest";

import {
  CART_STORAGE_KEY,
  MAX_CART_LINES,
  addToCart,
  cartSnapshot,
  isCartFullFor,
  productQuantity,
  readCart,
  sanitizeCart,
  saveCart,
  writeCart,
} from "./cart";
import type { CartLine, CartStorage } from "./cart";

// localStorage 的替身：只记键值。
function memoryStorage(initial: Record<string, string> = {}): CartStorage & { data: Record<string, string> } {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => data[key] ?? null,
    setItem: (key, value) => {
      data[key] = value;
    },
  };
}

const failing: CartStorage = {
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

function lines(count: number): CartLine[] {
  return Array.from({ length: count }, (_, index) => line(`sku-${String(index)}`, 1, `p-${String(index)}`));
}

describe("cart storage format", () => {
  // SHOP-TASK-017 验收第 4 条「每行只有 SKU、商品 slug 与件数（不存价格、名称或任何个人资料）」：写入的内容每行恰好三个字段。
  it("stores only the SKU, the product slug and the quantity", () => {
    const storage = memoryStorage();
    const withExtras = { sku: "tee-black-m", slug: "tee", quantity: 2, price_sen: 3900, name: "Tee", phone: "0123" } as CartLine;
    expect(writeCart(storage, [withExtras])).toBe(true);
    expect(JSON.parse(storage.data[CART_STORAGE_KEY] ?? "")).toEqual([{ sku: "tee-black-m", slug: "tee", quantity: 2 }]);
    expect(readCart(storage)).toEqual([{ sku: "tee-black-m", slug: "tee", quantity: 2 }]);
  });

  // SHOP-TASK-017 验收第 4 条「读取时丢弃格式不对…的部分」：不是 JSON、不是数组都当作空购物车。
  it.each(["not json", "{}", '"tee"', "null", "42"])("reads %j as an empty cart", (stored) => {
    expect(readCart(memoryStorage({ [CART_STORAGE_KEY]: stored }))).toEqual([]);
  });

  // SHOP-TASK-017 验收第 4 条「读取时丢弃格式不对、件数不在 1 到 99…的部分」：坏行丢掉，好行照用，多出的字段不带出来。
  it("drops malformed lines and quantities outside 1 to 99", () => {
    const stored = [
      line("ok-1", 1),
      { sku: "", slug: "tee", quantity: 1 },
      { sku: "no-slug", quantity: 1 },
      { sku: 7, slug: "tee", quantity: 1 },
      { sku: "zero", slug: "tee", quantity: 0 },
      { sku: "hundred", slug: "tee", quantity: 100 },
      { sku: "half", slug: "tee", quantity: 1.5 },
      { sku: "text", slug: "tee", quantity: "2" },
      null,
      "ok-2",
      [line("nested")],
      { sku: "ok-99", slug: "tee", quantity: 99, price_sen: 1 },
    ];
    expect(sanitizeCart(stored)).toEqual([line("ok-1", 1), line("ok-99", 99)]);
  });

  // SHOP-TASK-017 验收第 4 条「读取时丢弃…重复 SKU…的部分」：同一 SKU 只保留第一行。
  it("keeps only the first line of a repeated SKU", () => {
    expect(sanitizeCart([line("a", 2), line("b", 1), line("a", 5)])).toEqual([line("a", 2), line("b", 1)]);
  });

  // SHOP-TASK-017 验收第 4 条「读取时丢弃…超过 20 行的部分」与 UX P03「购物车已有 20 行」：只保留前 20 个合格的行。
  it("keeps at most twenty lines", () => {
    const stored = [{ sku: "bad", slug: "", quantity: 1 }, ...lines(25)];
    const read = sanitizeCart(stored);
    expect(read).toHaveLength(MAX_CART_LINES);
    expect(read).toEqual(lines(20));
  });

  // SHOP-TASK-017 验收第 4 条「读写失败时页面照常可用」：读取抛错时为空购物车，写入抛错或没有存储时返回 false 而不抛错。
  it("survives storage that throws or is missing", () => {
    expect(readCart(failing)).toEqual([]);
    expect(readCart(null)).toEqual([]);
    expect(writeCart(failing, [line("a")])).toBe(false);
    expect(writeCart(null, [line("a")])).toBe(false);
    expect(saveCart(failing, [line("a")])).toBe(false);
    expect(cartSnapshot(failing)).toEqual([]);
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-017 验收第 4 条「购物车模块供之后的购物车页复用」——
  // 页面读到的快照在存储内容不变时是同一个数组，内容变了才换新的（订阅购物车的页面据此判断是否重新渲染）。
  it("returns the same snapshot until the stored cart changes", () => {
    const storage = memoryStorage();
    expect(saveCart(storage, [line("a", 1)])).toBe(true);
    const first = cartSnapshot(storage);
    expect(first).toEqual([line("a", 1)]);
    expect(cartSnapshot(storage)).toBe(first);
    saveCart(storage, [line("a", 2)]);
    expect(cartSnapshot(storage)).toEqual([line("a", 2)]);
  });
});

describe("adding to the cart", () => {
  // UX P03「所选规格已在购物车中时加入即合并件数」与 SHOP-TASK-017 验收第 4 条「同一 SKU 再次加入时合并件数」：行数不变、位置不变。
  it("merges the quantity of a SKU already in the cart", () => {
    const cart = [line("a", 2), line("b", 1)];
    expect(addToCart(cart, line("a", 3))).toEqual([line("a", 5), line("b", 1)]);
  });

  // UX P03 加入购物车：新的 SKU 加在末尾。
  it("appends a new SKU", () => {
    expect(addToCart([line("a", 2)], line("b", 1, "bag"))).toEqual([line("a", 2), line("b", 1, "bag")]);
  });

  // UX P03「购物车已有 20 行且所选规格不在其中」：不能再加新的一行，但已在其中的 SKU 仍可合并。
  it("refuses a twenty-first line but still merges into an existing one", () => {
    const full = lines(20);
    expect(isCartFullFor(full, "new")).toBe(true);
    expect(addToCart(full, line("new"))).toBeNull();
    expect(isCartFullFor(full, "sku-3")).toBe(false);
    expect(addToCart(full, line("sku-3", 2, "p-3"))?.[3]).toEqual(line("sku-3", 3, "p-3"));
    expect(isCartFullFor(lines(19), "new")).toBe(false);
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-017 验收第 4 条「件数不在 1 到 99」——加入后的行同样只能是 1 到 99 件。
  it("refuses quantities that would leave 1 to 99", () => {
    expect(addToCart([], line("a", 0))).toBeNull();
    expect(addToCart([], line("a", 100))).toBeNull();
    expect(addToCart([line("a", 98)], line("a", 2))).toBeNull();
    expect(addToCart([line("a", 98)], line("a", 1))).toEqual([line("a", 99)]);
  });

  // UX P03「同一商品所有规格在本浏览器购物车中的件数合计计入限购」：按 slug 合计各行件数。
  it("sums the quantities of every SKU of the same product", () => {
    const cart = [line("tee-black-m", 3, "tee"), line("bag-red", 4, "bag"), line("tee-white-s", 2, "tee")];
    expect(productQuantity(cart, "tee")).toBe(5);
    expect(productQuantity(cart, "bag")).toBe(4);
    expect(productQuantity(cart, "mug")).toBe(0);
  });
});

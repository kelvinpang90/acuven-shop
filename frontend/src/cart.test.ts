import { describe, expect, it } from "vitest";

import {
  CART_STORAGE_KEY,
  MAX_CART_LINES,
  MAX_CART_UNITS,
  MAX_LINE_QUANTITY,
  addToCart,
  cartItemCount,
  cartSnapshot,
  clearCart,
  isCartFullFor,
  parseQuantityInput,
  productQuantity,
  readCart,
  removeLine,
  sanitizeCart,
  saveCart,
  setLineQuantity,
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

  // SHOP-TASK-017 验收第 4 条「读取时丢弃格式不对、件数不在…范围的部分」，范围随 DESIGN 1.18 第 8 条「每行 1–1000 件」改为 1 到 1000
  // （SHOP-TASK-193 改写：原以 99 为上限）：坏行丢掉，好行照用，多出的字段不带出来。
  it("drops malformed lines and quantities outside 1 to 1000", () => {
    const stored = [
      line("ok-1", 1),
      { sku: "", slug: "tee", quantity: 1 },
      { sku: "no-slug", quantity: 1 },
      { sku: 7, slug: "tee", quantity: 1 },
      { sku: "zero", slug: "tee", quantity: 0 },
      { sku: "over", slug: "tee", quantity: 1001 },
      { sku: "half", slug: "tee", quantity: 1.5 },
      { sku: "text", slug: "tee", quantity: "2" },
      null,
      "ok-2",
      [line("nested")],
      { sku: "ok-1000", slug: "tee", quantity: 1000, price_sen: 1 },
    ];
    expect(sanitizeCart(stored)).toEqual([line("ok-1", 1), line("ok-1000", 1000)]);
  });

  // HANDOFF 0.80「读取时的清洗不按整单合计丢行（服务端计价再判断）」：合计超过 2,000 件的购物车读回时各行照留。
  it("keeps every line when the whole cart is over 2,000 items", () => {
    const over = [line("a", 1000), line("b", 1000), line("c", 5)];
    expect(cartItemCount(over)).toBeGreaterThan(MAX_CART_UNITS);
    expect(sanitizeCart(over)).toEqual(over);
    expect(readCart(memoryStorage({ [CART_STORAGE_KEY]: JSON.stringify(over) }))).toEqual(over);
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

  // DESIGN 1.18「计价、优惠、积分与库存」第 8 条「每行 1–1000 件」（SHOP-TASK-193 改写：原以 99 为上限）——加入后的行同样只能是 1 到 1000 件。
  it("refuses quantities that would leave 1 to 1000", () => {
    expect(MAX_LINE_QUANTITY).toBe(1000);
    expect(addToCart([], line("a", 0))).toBeNull();
    expect(addToCart([], line("a", 1001))).toBeNull();
    expect(addToCart([], line("a", 1000))).toEqual([line("a", 1000)]);
    expect(addToCart([line("a", 998)], line("a", 3))).toBeNull();
    expect(addToCart([line("a", 998)], line("a", 2))).toEqual([line("a", 1000)]);
  });

  // DESIGN 1.18 第 8 条「整单所有行合计不超过 2,000 件」与 HANDOFF 0.80「addToCart 合并或新增后全部行合计超过 2,000 即不加入」：
  // 恰好 2,000 件照加，多 1 件不论合并还是新增都返回 null。
  it("refuses to take the whole cart over 2,000 items", () => {
    expect(MAX_CART_UNITS).toBe(2000);
    const cart = [line("a", 1000), line("b", 999, "bag")];
    expect(addToCart(cart, line("c", 1, "mug"))).toEqual([...cart, line("c", 1, "mug")]);
    expect(addToCart(cart, line("c", 2, "mug"))).toBeNull();
    expect(addToCart(cart, line("b", 1, "bag"))).toEqual([line("a", 1000), line("b", 1000, "bag")]);
    expect(addToCart([line("a", 1000), line("b", 1000, "bag")], line("c", 1, "mug"))).toBeNull();
  });

  // UX P03「同一商品所有规格在本浏览器购物车中的件数合计计入限购」：按 slug 合计各行件数。
  it("sums the quantities of every SKU of the same product", () => {
    const cart = [line("tee-black-m", 3, "tee"), line("bag-red", 4, "bag"), line("tee-white-s", 2, "tee")];
    expect(productQuantity(cart, "tee")).toBe(5);
    expect(productQuantity(cart, "bag")).toBe(4);
    expect(productQuantity(cart, "mug")).toBe(0);
  });
});

describe("changing the cart on the cart page", () => {
  // SHOP-TASK-018 验收第 2 条「页头显示 common.nav_cart，{count} 为购物车各行件数之和」：是件数之和，不是行数。
  it("counts the items of every line for the header", () => {
    expect(cartItemCount([line("a", 2), line("b", 3), line("c", 1)])).toBe(6);
    expect(cartItemCount([])).toBe(0);
  });

  // UX P04「查看、修改已选商品规格与数量」与 SHOP-TASK-018 验收第 4 条「件数不低于 1」：只改该行件数，位置不变；低于 1、
  // 高于 1000（DESIGN 1.18 第 8 条「每行 1–1000 件」，SHOP-TASK-193 改写：原以 99 为上限）或不在购物车中的 SKU 不改。
  it("sets the quantity of one line within 1 to 1000", () => {
    const cart = [line("a", 2), line("b", 1)];
    expect(setLineQuantity(cart, "b", 4)).toEqual([line("a", 2), line("b", 4)]);
    expect(setLineQuantity(cart, "b", 1000)).toEqual([line("a", 2), line("b", 1000)]);
    expect(setLineQuantity(cart, "a", 0)).toBeNull();
    expect(setLineQuantity(cart, "a", 1001)).toBeNull();
    expect(setLineQuantity(cart, "a", 1.5)).toBeNull();
    expect(setLineQuantity(cart, "missing", 1)).toBeNull();
  });

  // HANDOFF 0.80「整单上限只在加入时约束」：购物车页改件数不按整单合计判定（由服务端计价再判断）。
  it("does not check the whole-cart total when setting a line", () => {
    expect(setLineQuantity([line("a", 1000), line("b", 999)], "b", 1000)).toEqual([line("a", 1000), line("b", 1000)]);
    expect(setLineQuantity([line("a", 1000), line("b", 1000), line("c", 1)], "c", 2)).toEqual([line("a", 1000), line("b", 1000), line("c", 2)]);
  });

  // UX P04 线框「([cart.remove])」：移除一行，其余行与顺序不变。
  it("removes one line and keeps the others in order", () => {
    expect(removeLine([line("a"), line("b"), line("c")], "b")).toEqual([line("a"), line("c")]);
    expect(removeLine([line("a")], "missing")).toEqual([line("a")]);
  });
});

describe("typing a quantity", () => {
  // docs/UX.md P03「数量可直接输入」「只收整数…为空、小于 1 或不是整数时恢复为原来的件数」：去掉首尾空白后的 ASCII 数字按十进制读成整数。
  it.each([
    ["1", 1],
    ["25", 25],
    ["  7 ", 7],
    ["007", 7],
    ["1000", 1000],
    ["2500", 2500],
  ])("reads %j as %i", (text, quantity) => {
    expect(parseQuantityInput(text)).toBe(quantity);
  });

  // 同上「为空、小于 1 或不是整数时恢复为原来的件数」与任务契约「不收正负号、小数点、指数写法与全角数字」：都返回 null。
  it.each(["", "   ", "0", "00", "-1", "+3", "1.5", "2.", "1e3", "１２", "3 4", "abc", "0x10"])("rejects %j", (text) => {
    expect(parseQuantityInput(text)).toBeNull();
  });
});

describe("clearing the cart after an order", () => {
  // SHOP-TASK-025 验收第 9 条「201 或 200 时清空本浏览器购物车」：写成空数组，读回为空购物车；只写购物车这一个键。
  it("empties the browser cart", () => {
    const storage = memoryStorage({ [CART_STORAGE_KEY]: JSON.stringify([line("a", 2), line("b")]) });
    expect(clearCart(storage)).toBe(true);
    expect(readCart(storage)).toEqual([]);
    expect(storage.data).toEqual({ [CART_STORAGE_KEY]: "[]" });
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-017 验收「读写失败时页面照常可用」——没有存储或写入失败时返回 false，不抛错。
  it("reports a failed write", () => {
    expect(clearCart(null)).toBe(false);
    expect(clearCart(failing)).toBe(false);
  });
});

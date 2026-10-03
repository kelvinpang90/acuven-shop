import { useSyncExternalStore } from "react";

// 浏览器购物车（docs/UX.md P03「每单限购」、P04）：只存在本浏览器的 localStorage，不发给服务端保存。
// 每行只有 SKU、商品 slug 与件数，不存价格、名称或任何个人资料；价格与可否下单一律由服务端计价（P04 起）。
// 商品详情 P03 与之后的购物车 P04 共用本模块。限购与 20 行上限在这里只用于提示与禁用按钮，下单时由服务端再次校验。
// 存储格式：键 acuven-shop.cart，值为 JSON 数组，每项 {"sku": …, "slug": …, "quantity": …}。

export const CART_STORAGE_KEY = "acuven-shop.cart";

// 购物车最多 20 行（每行一个 SKU）；每行件数 1 到 99（与计价接口的请求上限相同）。
export const MAX_CART_LINES = 20;
export const MIN_LINE_QUANTITY = 1;
export const MAX_LINE_QUANTITY = 99;

export interface CartLine {
  sku: string;
  slug: string;
  quantity: number;
}

export type CartStorage = Pick<Storage, "getItem" | "setItem">;

function isLineQuantity(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= MIN_LINE_QUANTITY && (value as number) <= MAX_LINE_QUANTITY;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

// 清洗读到的内容：不是数组时为空；丢弃格式不对的行（SKU 或 slug 不是非空字符串、件数不是 1 到 99 的整数）、
// 与前面重复的 SKU，以及第 20 行之后的部分。每行只保留三个字段，多出的字段不带出来。
export function sanitizeCart(raw: unknown): CartLine[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const lines: CartLine[] = [];
  const seen = new Set<string>();
  for (const item of raw as unknown[]) {
    if (lines.length >= MAX_CART_LINES) {
      break;
    }
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      continue;
    }
    const { sku, slug, quantity } = item as Record<string, unknown>;
    if (!isNonEmptyString(sku) || !isNonEmptyString(slug) || !isLineQuantity(quantity) || seen.has(sku)) {
      continue;
    }
    seen.add(sku);
    lines.push({ sku, slug, quantity });
  }
  return lines;
}

// 存储里的文字：不是 JSON 时当作空购物车。
function parseCart(stored: string | null): CartLine[] {
  if (stored === null) {
    return [];
  }
  try {
    return sanitizeCart(JSON.parse(stored));
  } catch {
    return [];
  }
}

// 读取失败（没有存储、读取抛错、内容不是 JSON）时当作空购物车。
export function readCart(storage: CartStorage | null): CartLine[] {
  if (!storage) {
    return [];
  }
  try {
    return parseCart(storage.getItem(CART_STORAGE_KEY));
  } catch {
    return [];
  }
}

// 写入购物车；返回是否写成（没有存储或写入抛错时为 false，页面照常可用）。
export function writeCart(storage: CartStorage | null, lines: readonly CartLine[]): boolean {
  if (!storage) {
    return false;
  }
  try {
    storage.setItem(CART_STORAGE_KEY, JSON.stringify(sanitizeCart(lines)));
    return true;
  } catch {
    return false;
  }
}

// 购物车中同一商品（按 slug）各行件数之和。
export function productQuantity(lines: readonly CartLine[], slug: string): number {
  return lines.reduce((sum, line) => (line.slug === slug ? sum + line.quantity : sum), 0);
}

// 购物车已有 20 行且该 SKU 不在其中：不能再加入新的一行。
export function isCartFullFor(lines: readonly CartLine[], sku: string): boolean {
  return lines.length >= MAX_CART_LINES && !lines.some((line) => line.sku === sku);
}

// 加入购物车：同一 SKU 已在购物车中时合并件数（行的位置不变），否则加在末尾。
// 件数不是 1 到 99 的整数、合并后超过 99、或购物车已满且是新行时不加入，返回 null。
export function addToCart(lines: readonly CartLine[], added: CartLine): CartLine[] | null {
  if (!isNonEmptyString(added.sku) || !isNonEmptyString(added.slug) || !isLineQuantity(added.quantity)) {
    return null;
  }
  const existing = lines.find((line) => line.sku === added.sku);
  if (existing) {
    const quantity = existing.quantity + added.quantity;
    if (quantity > MAX_LINE_QUANTITY) {
      return null;
    }
    return lines.map((line) => (line === existing ? { ...line, quantity } : line));
  }
  if (isCartFullFor(lines, added.sku)) {
    return null;
  }
  return [...lines, { sku: added.sku, slug: added.slug, quantity: added.quantity }];
}

// 页面里的购物车：读 localStorage，写入后通知本页其他订阅者，别的标签页改动时经 storage 事件刷新。
// 服务端渲染与首次水合时为空购物车。

const EMPTY_CART: readonly CartLine[] = [];
const listeners = new Set<() => void>();

// 同一份存储内容只解析一次，返回同一个数组（useSyncExternalStore 要求快照在内容不变时不变）。
let cached: { raw: string | null; lines: readonly CartLine[] } = { raw: null, lines: EMPTY_CART };

export function cartSnapshot(storage: CartStorage | null): readonly CartLine[] {
  let raw: string | null;
  try {
    raw = storage ? storage.getItem(CART_STORAGE_KEY) : null;
  } catch {
    return EMPTY_CART;
  }
  if (raw !== cached.raw) {
    cached = { raw, lines: raw === null ? EMPTY_CART : parseCart(raw) };
  }
  return cached.lines;
}

function subscribeCart(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === CART_STORAGE_KEY) {
      onChange();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function useCartLines(storage: CartStorage | null): readonly CartLine[] {
  return useSyncExternalStore(
    subscribeCart,
    () => cartSnapshot(storage),
    () => EMPTY_CART,
  );
}

// 写入并通知订阅者；返回是否写成。
export function saveCart(storage: CartStorage | null, lines: readonly CartLine[]): boolean {
  const saved = writeCart(storage, lines);
  if (saved) {
    for (const listener of listeners) {
      listener();
    }
  }
  return saved;
}

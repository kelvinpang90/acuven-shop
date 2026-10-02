import { useCallback, useEffect, useState } from "react";

// 只存在本浏览器的购物车（docs/UX.md P03、P04；DESIGN「数据模型」Cart）：localStorage 里一个 JSON 数组，
// 每行只有 SKU、商品 slug 与件数——不存价格、名称或任何个人资料；金额一律由服务端按当前价格计算。
// 读取时清洗：格式不对、件数不在 1–99 的行丢弃，同一 SKU 只留第一行，最多留前 20 行。
// 读写失败（隐私模式、存储已满、被禁用）时页面照常可用：读不到当作空购物车，写不进只是下次不记得。
// 每单限购与 20 行上限在这里只用来提示与禁用按钮，下单时由服务端再次校验（UX 0.6）。
// 之后的购物车页 P04 复用本模块。

export const CART_STORAGE_KEY = "acuven-shop.cart";
// UX 0.6：购物车最多 20 行（不同 SKU）。
export const CART_MAX_LINES = 20;
// 每行件数 1–99（与后台每单限购的上限一致）。
export const CART_MAX_QUANTITY = 99;

export interface CartLine {
  sku: string;
  // 商品 slug：按它把同一商品各 SKU 的件数合计计入限购。
  slug: string;
  quantity: number;
}

export type CartStorage = Pick<Storage, "getItem" | "setItem">;

// 取 localStorage 本身也可能抛错，取不到就当作没有存储。
export function browserCartStorage(): CartStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isQuantity(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= CART_MAX_QUANTITY;
}

function isKey(value: unknown): value is string {
  return typeof value === "string" && value !== "" && value.trim() === value;
}

// 一行只取 sku、slug、quantity 三项；其他字段不读也不再写回。三项任一不合格时整行丢弃。
function toLine(value: unknown): CartLine | null {
  if (!isRecord(value)) {
    return null;
  }
  const { sku, slug, quantity } = value;
  if (!isKey(sku) || !isKey(slug) || !isQuantity(quantity)) {
    return null;
  }
  return { sku, slug, quantity };
}

// 清洗规则按顺序：丢弃格式不对的行，同一 SKU 只留最先出现的一行，再只留前 20 行。不是数组时为空购物车。
export function sanitizeCart(value: unknown): CartLine[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const lines: CartLine[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const line = toLine(item);
    if (line === null || seen.has(line.sku)) {
      continue;
    }
    seen.add(line.sku);
    lines.push(line);
  }
  return lines.slice(0, CART_MAX_LINES);
}

export function readCart(storage: CartStorage | null): CartLine[] {
  if (!storage) {
    return [];
  }
  try {
    const raw = storage.getItem(CART_STORAGE_KEY);
    return raw === null ? [] : sanitizeCart(JSON.parse(raw));
  } catch {
    return [];
  }
}

// 写入前同样清洗；返回是否写进了存储。
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

// 购物车里同一商品（按 slug）各行件数之和。
export function productQuantity(lines: readonly CartLine[], slug: string): number {
  return lines.reduce((sum, line) => (line.slug === slug ? sum + line.quantity : sum), 0);
}

// UX P03 0.6：还能加入的件数 = 每单限购 − 购物车中该商品各 SKU 已有件数，最少为 0。
export function remainingAllowance(lines: readonly CartLine[], slug: string, maxPerOrder: number): number {
  return Math.max(0, maxPerOrder - productQuantity(lines, slug));
}

// UX P03 0.6：购物车已有 20 行且该 SKU 不在其中时不能再加。
export function isCartFullFor(lines: readonly CartLine[], sku: string): boolean {
  return lines.length >= CART_MAX_LINES && !lines.some((line) => line.sku === sku);
}

export type AddResult = { ok: true; lines: CartLine[] } | { ok: false; reason: "cart_full" | "invalid" };

// 加入一行：同一 SKU 已在购物车中时合并件数，否则追加到最后。
// 新行会超过 20 行时为 cart_full；件数不合格或合并后超过 99 时为 invalid。限购由调用方按 remainingAllowance 先挡住。
export function addToCart(lines: readonly CartLine[], item: CartLine): AddResult {
  const added = toLine(item);
  if (added === null) {
    return { ok: false, reason: "invalid" };
  }
  const existing = lines.find((line) => line.sku === added.sku);
  if (existing) {
    const quantity = existing.quantity + added.quantity;
    if (!isQuantity(quantity)) {
      return { ok: false, reason: "invalid" };
    }
    return { ok: true, lines: lines.map((line) => (line === existing ? { ...line, quantity } : line)) };
  }
  if (lines.length >= CART_MAX_LINES) {
    return { ok: false, reason: "cart_full" };
  }
  return { ok: true, lines: [...lines, added] };
}

export interface CartState {
  lines: CartLine[];
  add: (item: CartLine) => AddResult;
}

// 页面用的购物车：首次渲染时读一次，加入后写回；其他标签页改了购物车时（storage 事件）重新读取。
// 写入失败时本页仍按加入后的内容显示与提示。
export function useCart(storage: CartStorage | null = browserCartStorage()): CartState {
  const [lines, setLines] = useState<CartLine[]>(() => readCart(storage));

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === CART_STORAGE_KEY) {
        setLines(readCart(storage));
      }
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
    };
  }, [storage]);

  const add = useCallback(
    (item: CartLine) => {
      // 按本页显示的内容加入：提示与按钮状态都是按它算的，其他标签页的改动已经由 storage 事件同步过来。
      const result = addToCart(lines, item);
      if (result.ok) {
        writeCart(storage, result.lines);
        setLines(result.lines);
      }
      return result;
    },
    [storage, lines],
  );

  return { lines, add };
}

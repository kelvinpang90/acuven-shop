// 后台库存重置结果 A07 的两个只读接口（app/api/admin_stock_resets.py，SHOP-TASK-052）：
// 列表 GET /api/admin/stock-resets?page=N 与某一天的明细 GET /api/admin/stock-resets/{内部 ID}。
// 后台会话凭服务端发的 HttpOnly cookie；只读，不带 CSRF 令牌。路径只用重置记录的内部 ID。
// 只取 SHOP-TASK-052 记录段列出的字段，多出的字段不带进结果；响应体不合格算失败。页面只按状态码区分，不显示错误体。

export const ADMIN_STOCK_RESETS_URL = "/api/admin/stock-resets";

// 重置记录内部 ID 的上限，与服务端路径 ID 的上限相同。
export const MAX_RESET_ID = 2147483647;

// 一天的重置结果：成功或失败（失败时整笔回滚，没有明细）。
export const RESET_RESULTS = ["succeeded", "failed"] as const;
export type ResetResult = (typeof RESET_RESULTS)[number];

// 列表的一行：内部 ID、马来西亚营业日期（YYYY-MM-DD）、结果、SKU 数与完成时间（带 Z 的 UTC，失败时为 null）。
export interface StockResetRow {
  id: number;
  business_date: string;
  result: ResetResult;
  sku_count: number;
  completed_at: string | null;
}

// 一页：总数、页码、每页条数与本页各天（按营业日期从新到旧）。
export interface StockResetList {
  total: number;
  page: number;
  page_size: number;
  resets: StockResetRow[];
}

// 明细的一行：SKU、初始库存、有效预留与当日可用（对应 admin.stock_reset_breakdown 的三个数）。
export interface StockResetLine {
  sku: string;
  initial_stock: number;
  held_quantity: number;
  available_stock: number;
}

// 某一天的明细：列表那一行的各字段与按 SKU 升序的各行。
export interface StockResetDetail extends StockResetRow {
  lines: StockResetLine[];
}

// 读取的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断。
export type ResetsRead = { kind: "ok"; list: StockResetList } | { kind: "none" } | { kind: "failed" } | { kind: "network" };
export type ResetRead = { kind: "ok"; reset: StockResetDetail } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositive(value: unknown): value is number {
  return isCount(value) && value > 0;
}

function isResult(value: unknown): value is ResetResult {
  return typeof value === "string" && (RESET_RESULTS as readonly string[]).includes(value);
}

function isTime(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

// 营业日期：恰为 YYYY-MM-DD，且是日历上存在的日子。
export function isBusinessDate(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) {
    return false;
  }
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function readRow(value: unknown): StockResetRow | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { id, business_date, result, sku_count, completed_at } = value as Record<string, unknown>;
  // 失败时为 null；不为 null 时须是能解析的时间（undefined 即不合格）。
  const completed = completed_at === null ? null : isTime(completed_at) ? completed_at : undefined;
  if (!isPositive(id) || !isBusinessDate(business_date) || !isResult(result) || !isCount(sku_count) || completed === undefined) {
    return null;
  }
  return { id, business_date, result, sku_count, completed_at: completed };
}

function readLine(value: unknown): StockResetLine | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { sku, initial_stock, held_quantity, available_stock } = value as Record<string, unknown>;
  if (typeof sku !== "string" || !isCount(initial_stock) || !isCount(held_quantity) || !isCount(available_stock)) {
    return null;
  }
  return { sku, initial_stock, held_quantity, available_stock };
}

// 逐项读取；任一项不合格整个数组不合格。
function readEach<T>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: T[] = [];
  for (const item of value as unknown[]) {
    const found = read(item);
    if (found === null) {
      return null;
    }
    items.push(found);
  }
  return items;
}

function readList(value: unknown): StockResetList | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const { total, page, page_size, resets } = value as Record<string, unknown>;
  const rows = readEach(resets, readRow);
  if (!isCount(total) || !isPositive(page) || !isPositive(page_size) || rows === null) {
    return null;
  }
  return { total, page, page_size, resets: rows };
}

// 明细须是所请求的那一天（ID 相同）。
function readDetail(value: unknown, id: number): StockResetDetail | null {
  const row = readRow(value);
  if (row === null || row.id !== id) {
    return null;
  }
  const lines = readEach((value as Record<string, unknown>).lines, readLine);
  return lines === null ? null : { ...row, lines };
}

export function adminStockResetsUrl(page: number): string {
  return `${ADMIN_STOCK_RESETS_URL}?${new URLSearchParams({ page: String(page) }).toString()}`;
}

export function adminStockResetUrl(id: number): string {
  return `${ADMIN_STOCK_RESETS_URL}/${String(id)}`;
}

// 同源 cookie、不缓存的 GET；网络中断为 null。
async function get(url: string, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return null;
  }
}

type Reply<T> = { kind: "ok"; value: T } | { kind: "none" | "failed" | "network" };

// 200 的响应体按 read 读取；401 为 none，其他状态与读不出、不合格的响应体为 failed。
async function readReply<T>(response: Response | null, read: (body: unknown) => T | null): Promise<Reply<T>> {
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "failed" };
  }
  const value = read(body);
  return value === null ? { kind: "failed" } : { kind: "ok", value };
}

// GET /api/admin/stock-resets?page=N：一页的重置结果。
export async function listStockResets(page: number, signal?: AbortSignal): Promise<ResetsRead> {
  const reply = await readReply(await get(adminStockResetsUrl(page), signal), readList);
  return reply.kind === "ok" ? { kind: "ok", list: reply.value } : { kind: reply.kind };
}

// GET /api/admin/stock-resets/{内部 ID}：某一天的明细。ID 不是不超过上限的正整数时不发请求，算失败。
export async function readStockReset(id: number, signal?: AbortSignal): Promise<ResetRead> {
  if (!Number.isSafeInteger(id) || id < 1 || id > MAX_RESET_ID) {
    return { kind: "failed" };
  }
  const reply = await readReply(await get(adminStockResetUrl(id), signal), (body) => readDetail(body, id));
  return reply.kind === "ok" ? { kind: "ok", reset: reply.value } : { kind: reply.kind };
}

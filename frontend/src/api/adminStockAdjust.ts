import { CSRF_HEADER } from "./pay";

// 后台 A04 当日库存调整的接口（app/api/admin_stock_adjust.py，SHOP-TASK-182）：POST /api/admin/variants/{variant_id}/available-stock，
// 不带查询参数。后台会话凭服务端发的 HttpOnly cookie，请求头带调用方给出的 CSRF 令牌。只取 StockOut 与 409 stock_changed 响应体列出的字段，
// 多出的字段不带进结果；响应体不合格算失败。取值范围（0–9,999）、带条件的写入与审计在服务端（DESIGN 1.18 第 12 条），这里只传递与校验字段。

const ADMIN_VARIANTS_URL = "/api/admin/variants";

// 调整的内容（与 StockIn 一致）：调整前页面显示的当日可用库存与新值，都是整数。
export interface StockAdjust {
  expected: number;
  available_stock: number;
}

// 调整的结果：调整了（200，带服务端返回的当日可用库存）、库存已变动（409 且 detail 为 stock_changed，带服务端返回的当前值）、取值不合格
// （422 且 detail 为 stock_invalid）、没有会话（401）、CSRF 令牌不对（403）、其他失败（含 404 variant_not_found、路径 ID 与请求体的 422、
// 413、415、409 缺当前值与 200 但响应体不合格）、网络中断（含调用方中止）。
export type StockAdjustResult =
  | { kind: "ok"; available_stock: number }
  | { kind: "changed"; available_stock: number }
  | { kind: "invalid" }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isStock(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

// 200 的响应体：variant_id 为正整数、sku 为非空字符串、available_stock 为不小于 0 的整数；只带回 available_stock。
function readAdjusted(value: unknown): number | null {
  if (!isRecord(value)) {
    return null;
  }
  const { variant_id, sku, available_stock } = value;
  if (!isPositiveInteger(variant_id) || !isFilled(sku) || !isStock(available_stock)) {
    return null;
  }
  return available_stock;
}

// 409 的响应体：detail 恰为 stock_changed，available_stock 为不小于 0 的整数（当前值）。
function readChanged(value: unknown): number | null {
  if (!isRecord(value)) {
    return null;
  }
  const { detail, available_stock } = value;
  if (detail !== "stock_changed" || !isStock(available_stock)) {
    return null;
  }
  return available_stock;
}

// 4xx 的响应体：detail 恰为给定的字符串时为 true，否则为 null。
function detailIs(detail: string): (body: unknown) => true | null {
  return (body) => (isRecord(body) && body.detail === detail ? true : null);
}

// 响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// POST /api/admin/variants/{variant_id}/available-stock：路径段为 SKU 规格 ID 的十进制，JSON 请求体只有 expected 与 available_stock，请求头带
// CSRF 令牌；同源 cookie、不缓存。成功（含新值与当前相同、未写入）为 200。
export async function adjustAdminAvailableStock(variantId: number, input: StockAdjust, csrfToken: string, signal?: AbortSignal): Promise<StockAdjustResult> {
  let response: Response;
  try {
    response = await fetch(`${ADMIN_VARIANTS_URL}/${String(variantId)}/available-stock`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ expected: input.expected, available_stock: input.available_stock }),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const stock = await readBody(response, readAdjusted);
      return stock === null ? { kind: "failed" } : { kind: "ok", available_stock: stock };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 409: {
      const current = await readBody(response, readChanged);
      return current === null ? { kind: "failed" } : { kind: "changed", available_stock: current };
    }
    case 422:
      return (await readBody(response, detailIs("stock_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    default:
      return { kind: "failed" };
  }
}

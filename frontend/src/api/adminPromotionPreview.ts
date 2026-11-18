import type { CartLine } from "../cart";
import type { Language } from "../i18n/copy";
import { CatalogError } from "./catalog";
import { QuoteError, quoteRequestBody } from "./checkout";
import type { CheckoutQuote } from "./checkout";
import { CSRF_HEADER } from "./pay";
import { PromotionError } from "./promotion";
import type { PromotionResponse } from "./promotion";

// 管理员预览草稿与未开始活动的数据接口（app/api/admin_promotion_preview.py，SHOP-TASK-245），供 A12 的前台预览取数
// （UX 0.20 A12「前台预览」，DESIGN 1.23「节日促销活动」第 11 条，HANDOFF 0.98）：
// GET /api/admin/promotions/{ID}/preview?lang=、…/preview/products、…/preview/products/{slug} 与 POST …/preview/quote?lang=。
// 预览的数据只经后台接口取得，凭管理员会话：请求都带同源 cookie（credentials: "same-origin"）、不缓存（cache: "no-store"），
// 与公开接口不带 cookie 不同；公开接口的地址不加任何预览参数。只取数与计价，不下单、不碰个人资料。

export const ADMIN_PROMOTIONS_URL = "/api/admin/promotions";

// 公开商品列表与详情的地址前缀（api/catalog.ts 的 productsUrl 与 productDetailUrl）。
const CATALOG_PRODUCTS_URL = "/api/catalog/products";

const PREVIEW_UNAVAILABLE = "promotion_preview_unavailable";

// 读取预览活动的结果：取到（2xx，响应同 GET /api/promotion）、没有会话（401）、活动不存在（404，或活动 ID 不是正整数，后者不发请求）、
// 不可预览（409 且 detail 为 promotion_preview_unavailable）、其他失败（其他状态码，或 2xx 但响应体不是 JSON、不是对象、缺 promotion 或
// server_time 键）、网络中断（含调用方中止）。更深的字段照 api/promotion.ts 的 fetchPromotion 不逐项校验。
export type PreviewPromotionRead =
  | { kind: "ok"; response: PromotionResponse }
  | { kind: "none" }
  | { kind: "missing" }
  | { kind: "unavailable" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function previewBase(promotionId: number): string {
  return `${ADMIN_PROMOTIONS_URL}/${String(promotionId)}/preview`;
}

function langQuery(language: Language): string {
  return new URLSearchParams({ lang: language }).toString();
}

// GET 请求的公共写法：同源 cookie、不缓存、Accept 为 JSON、没有请求体。
function getPreview(url: string, signal: AbortSignal | undefined): Promise<Response> {
  return fetch(url, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: signal ?? null,
  });
}

export function previewPromotionUrl(promotionId: number, language: Language): string {
  return `${previewBase(promotionId)}?${langQuery(language)}`;
}

// 响应体只认带 promotion 与 server_time 两个键的对象；读不出 JSON 或不合格为 null。
async function readPromotionBody(response: Response): Promise<PromotionResponse | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  if (!isRecord(body) || !("promotion" in body) || !("server_time" in body)) {
    return null;
  }
  return { promotion: body.promotion, server_time: body.server_time } as PromotionResponse;
}

// 409 的响应体 detail 恰为 promotion_preview_unavailable 时为真。
async function isUnavailable(response: Response): Promise<boolean> {
  try {
    const body: unknown = await response.json();
    return isRecord(body) && body.detail === PREVIEW_UNAVAILABLE;
  } catch {
    return false;
  }
}

// GET /api/admin/promotions/{ID}/preview?lang=：活动 ID 不是正整数时不发请求，即为 missing。
export async function readPreviewPromotion(promotionId: number, language: Language, signal?: AbortSignal): Promise<PreviewPromotionRead> {
  if (!isPositiveInteger(promotionId)) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await getPreview(previewPromotionUrl(promotionId, language), signal);
  } catch {
    return { kind: "network" };
  }
  if (response.ok) {
    const body = await readPromotionBody(response);
    if (body === null) {
      // 读响应体时被调用方中止也算中止，不算响应体不合格。
      return signal?.aborted === true ? { kind: "network" } : { kind: "failed" };
    }
    return { kind: "ok", response: body };
  }
  switch (response.status) {
    case 401:
      return { kind: "none" };
    case 404:
      return { kind: "missing" };
    case 409:
      return (await isUnavailable(response)) ? { kind: "unavailable" } : { kind: "failed" };
    default:
      return { kind: "failed" };
  }
}

// 符合 promotion.tsx 的 PromotionFetcher（PromotionProvider 的 fetcher 与 url 配合 previewPromotionUrl 使用）：
// 非 2xx 抛 PromotionError，网络错误与中止原样抛出。
export async function fetchPreviewPromotion(url: string, signal?: AbortSignal): Promise<PromotionResponse> {
  const response = await getPreview(url, signal);
  if (!response.ok) {
    throw new PromotionError(response.status);
  }
  return (await response.json()) as PromotionResponse;
}

// 公开商品列表（/api/catalog/products?…）与详情（/api/catalog/products/{slug}…）的地址换成预览接口，查询参数与 slug 原样保留；
// 分类、规格选项与其他地址原样返回。
export function previewCatalogUrl(promotionId: number, url: string): string {
  if (url.startsWith(`${CATALOG_PRODUCTS_URL}?`) || url.startsWith(`${CATALOG_PRODUCTS_URL}/`)) {
    return `${previewBase(promotionId)}/products${url.slice(CATALOG_PRODUCTS_URL.length)}`;
  }
  return url;
}

// 取一次预览的商品列表或详情（地址由 previewCatalogUrl 给出）：同 api/catalog.ts 的 fetchCatalog，但凭同源 cookie、不缓存；
// 非 2xx 抛 CatalogError（详情 404 照公开接口为商品不存在），网络错误与中止原样抛出。
export async function fetchPreviewCatalog<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await getPreview(url, signal);
  if (!response.ok) {
    throw new CatalogError(response.status);
  }
  return (await response.json()) as T;
}

// POST /api/admin/promotions/{ID}/preview/quote?lang=：请求体同 api/checkout.ts 的 fetchQuote（每行只有 sku 与 quantity），
// 请求头带 X-CSRF-Token；非 2xx 抛 QuoteError，网络错误与中止原样抛出。只计价，不下单。
export async function fetchPreviewQuote(
  promotionId: number,
  csrfToken: string,
  language: Language,
  lines: readonly CartLine[],
  signal?: AbortSignal,
): Promise<CheckoutQuote> {
  const response = await fetch(`${previewBase(promotionId)}/quote?${langQuery(language)}`, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
    body: JSON.stringify(quoteRequestBody(lines)),
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new QuoteError(response.status);
  }
  return (await response.json()) as CheckoutQuote;
}

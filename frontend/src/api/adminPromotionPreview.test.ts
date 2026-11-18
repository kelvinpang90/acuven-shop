import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "../cart";
import {
  fetchPreviewCatalog,
  fetchPreviewPromotion,
  fetchPreviewQuote,
  previewCatalogUrl,
  previewPromotionUrl,
  readPreviewPromotion,
} from "./adminPromotionPreview";
import { CatalogError, categoriesUrl, optionsUrl, productDetailUrl, productsUrl } from "./catalog";
import { QuoteError, quoteRequestBody } from "./checkout";
import { PromotionError, promotionUrl } from "./promotion";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const RESPONSE = { promotion: { promotion_id: 7, title: "Deepavali Sale", rules: [] }, server_time: "2026-11-14T04:00:00Z" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function text(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError。请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((_input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined) {
      return Promise.reject(new Error("unexpected request"));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function sentUrl(fetchMock: FetchMock, index: number): string {
  return String(fetchMock.mock.calls[index]?.[0]);
}

function sentInit(fetchMock: FetchMock, index: number): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 预览的 GET 请求：GET、凭同源 cookie、不缓存、Accept 为 JSON、没有请求体与 CSRF 头，并带上调用方的 signal。
function expectPreviewGet(fetchMock: FetchMock, index: number, signal: AbortSignal) {
  const init = sentInit(fetchMock, index);
  expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
  expect(init.body).toBeUndefined();
  const headers = new Headers(init.headers);
  expect(headers.get("Accept")).toBe("application/json");
  expect(headers.has("X-CSRF-Token")).toBe(false);
  expect(init.signal).toBe(signal);
}

describe("reading the previewed promotion", () => {
  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：GET /api/admin/promotions/{ID}/preview?lang=，凭同源 cookie、不缓存，
  // 与不带 cookie 的公开接口 /api/promotion 不同。
  it("gets the preview with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, RESPONSE));
    const controller = new AbortController();
    await readPreviewPromotion(7, "zh", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7/preview?lang=zh");
    expect(previewPromotionUrl(12, "ms")).toBe("/api/admin/promotions/12/preview?lang=ms");
    expectPreviewGet(fetchMock, 0, controller.signal);
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：2xx 带回 promotion 与 server_time（更深的字段不逐项校验，多出的顶层键不带进结果）。
  it("returns the promotion and server time", async () => {
    stubFetch(json(200, { ...RESPONSE, extra: 1 }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "ok", response: RESPONSE });
    stubFetch(json(200, { promotion: null, server_time: "2026-11-14T04:00:00Z" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({
      kind: "ok",
      response: { promotion: null, server_time: "2026-11-14T04:00:00Z" },
    });
  });

  // 同一句「凭管理员会话」：没有会话（401）为 none。
  it("treats 401 as no session", async () => {
    stubFetch(json(401, { detail: "admin_session_required" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "none" });
  });

  // UX 0.20 A12「前台预览」「草稿与已发布未开始的活动可预览」：活动不存在（404）为 missing；活动 ID 不是正整数时不发请求，也为 missing。
  it("treats 404 and a bad ID as missing", async () => {
    stubFetch(json(404, { detail: "promotion_not_found" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "missing" });
    const fetchMock = stubFetch();
    for (const id of [0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      await expect(readPreviewPromotion(id, "en")).resolves.toEqual({ kind: "missing" });
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // UX 0.20 A12「前台预览」「唯一禁用条件是：已保存的内容里没有开始时间或结束时间，或结束时间已过」：409 且 detail 为
  // promotion_preview_unavailable 为 unavailable；其他 detail 或读不出的 409 为 failed。
  it("treats the preview-unavailable 409 as unavailable", async () => {
    stubFetch(json(409, { detail: "promotion_preview_unavailable" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "unavailable" });
    stubFetch(json(409, { detail: "promotion_changed" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "failed" });
    stubFetch(text(409, "Conflict"));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "failed" });
  });

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：其他状态码为 failed。
  it.each([403, 422, 500, 503])("treats status %i as failed", async (status) => {
    stubFetch(json(status, { detail: "x" }));
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "failed" });
  });

  // 同一句：2xx 但响应体不是 JSON、不是对象、缺 promotion 或 server_time 键都为 failed。
  it.each([
    ["not JSON", text(200, "<html></html>")],
    ["null", json(200, null)],
    ["an array", json(200, [RESPONSE])],
    ["a string", json(200, "ok")],
    ["no promotion key", json(200, { server_time: RESPONSE.server_time })],
    ["no server_time key", json(200, { promotion: RESPONSE.promotion })],
  ])("treats a 2xx body that is %s as failed", async (_name, reply) => {
    stubFetch(reply);
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "failed" });
  });

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：网络中断与调用方中止为 network。
  it("treats a network error and an abort as network", async () => {
    stubFetch(networkDown);
    await expect(readPreviewPromotion(7, "en")).resolves.toEqual({ kind: "network" });
    stubFetch(json(200, RESPONSE));
    const controller = new AbortController();
    controller.abort();
    await expect(readPreviewPromotion(7, "en", controller.signal)).resolves.toEqual({ kind: "network" });
  });
});

describe("the promotion fetcher for the shared read", () => {
  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：按给的地址读取，凭同源 cookie、不缓存，
  // 返回响应体；地址由 previewPromotionUrl 给出，公开地址 promotionUrl 不变。
  it("gets the given url with the same-origin cookie", async () => {
    const fetchMock = stubFetch(json(200, RESPONSE));
    const controller = new AbortController();
    await expect(fetchPreviewPromotion(previewPromotionUrl(7, "en"), controller.signal)).resolves.toEqual(RESPONSE);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7/preview?lang=en");
    expectPreviewGet(fetchMock, 0, controller.signal);
    expect(promotionUrl("en")).toBe("/api/promotion?lang=en");
  });

  // 同一句：非 2xx 抛带状态码的 PromotionError（由共享读取按失败处理），网络错误原样抛出。
  it("throws PromotionError on a non-2xx status", async () => {
    stubFetch(json(409, { detail: "promotion_preview_unavailable" }));
    const error: unknown = await fetchPreviewPromotion(previewPromotionUrl(7, "en")).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(PromotionError);
    expect((error as PromotionError).status).toBe(409);
    stubFetch(networkDown);
    await expect(fetchPreviewPromotion(previewPromotionUrl(7, "en"))).rejects.toBeInstanceOf(TypeError);
  });
});

describe("rewriting catalog urls", () => {
  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：商品列表（?）换成 …/preview/products，查询参数（含 sale）原样保留。
  it("rewrites the product list", () => {
    const list = productsUrl("ms", { q: "tee", categories: ["apparel"], options: ["size:m"], sort: "price_desc", page: 3, sale: true });
    expect(previewCatalogUrl(7, list)).toBe(`/api/admin/promotions/7/preview/products${list.slice("/api/catalog/products".length)}`);
    expect(previewCatalogUrl(7, "/api/catalog/products?lang=en&sort=newest&page=1")).toBe(
      "/api/admin/promotions/7/preview/products?lang=en&sort=newest&page=1",
    );
  });

  // 同一句：商品详情（/slug）换成 …/preview/products/{slug}，已编码的 slug 与查询参数原样保留。
  it("rewrites the product detail", () => {
    expect(previewCatalogUrl(7, productDetailUrl("zh", "crew-neck-tee"))).toBe("/api/admin/promotions/7/preview/products/crew-neck-tee?lang=zh");
    expect(previewCatalogUrl(12, productDetailUrl("en", "a b"))).toBe("/api/admin/promotions/12/preview/products/a%20b?lang=en");
  });

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：只改写商品列表与详情的地址，分类、规格选项与其他地址原样返回，
  // 不加任何预览参数。
  it.each([
    categoriesUrl("en"),
    optionsUrl("zh"),
    promotionUrl("ms"),
    "/api/catalog/products",
    "/api/catalog/productsale?lang=en",
    "/api/storefront?lang=en",
  ])("leaves %s unchanged", (url) => {
    expect(previewCatalogUrl(7, url)).toBe(url);
  });

  // DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：改写后的列表与详情凭同源 cookie、不缓存地读取；非 2xx 抛 CatalogError。
  it("fetches the rewritten list and detail with the same-origin cookie", async () => {
    const fetchMock = stubFetch(json(200, { total: 0, page: 1, page_size: 12, items: [] }), json(200, { slug: "tee" }), json(404, { detail: "product not found" }));
    const controller = new AbortController();
    const listUrl = previewCatalogUrl(7, productsUrl("en", { sale: true }));
    await expect(fetchPreviewCatalog(listUrl, controller.signal)).resolves.toEqual({ total: 0, page: 1, page_size: 12, items: [] });
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7/preview/products?lang=en&sort=newest&page=1&sale=true");
    expectPreviewGet(fetchMock, 0, controller.signal);
    const detailUrl = previewCatalogUrl(7, productDetailUrl("en", "tee"));
    await expect(fetchPreviewCatalog(detailUrl, controller.signal)).resolves.toEqual({ slug: "tee" });
    expect(sentUrl(fetchMock, 1)).toBe("/api/admin/promotions/7/preview/products/tee?lang=en");
    expectPreviewGet(fetchMock, 1, controller.signal);
    const error: unknown = await fetchPreviewCatalog(detailUrl).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(CatalogError);
    expect((error as CatalogError).status).toBe(404);
  });
});

describe("pricing the cart in preview", () => {
  const LINES: CartLine[] = [
    { sku: "TEE-M", slug: "crew-neck-tee", quantity: 2 },
    { sku: "CAP-1", slug: "cap", quantity: 1 },
  ];
  const QUOTE = { lines: [], subtotal_sen: 0, unit_limit: 2000, units_over_limit: false, amount_limit_sen: 99999999, amount_over_limit: false, promotion: null };

  // UX 0.20 A12「前台预览」「购物车预览用本浏览器的购物车计价，不能结账、不能下单」与 DESIGN 1.23 第 11 条「预览的数据只经后台接口（凭管理员会话）取得」：
  // POST /api/admin/promotions/{ID}/preview/quote?lang=，凭同源 cookie、不缓存，请求头带 X-CSRF-Token，请求体同 fetchQuote（每行只有 sku 与 quantity）。
  it("posts the cart lines with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, QUOTE));
    const controller = new AbortController();
    await expect(fetchPreviewQuote(7, "csrf-abc", "ms", LINES, controller.signal)).resolves.toEqual(QUOTE);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/7/preview/quote?lang=ms");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
    if (typeof init.body !== "string") {
      throw new Error("request body is not a JSON string");
    }
    const body = JSON.parse(init.body) as unknown;
    expect(body).toEqual(quoteRequestBody(LINES));
    expect(body).toEqual({
      lines: [
        { sku: "TEE-M", quantity: 2 },
        { sku: "CAP-1", quantity: 1 },
      ],
    });
  });

  // 同一句「不能结账、不能下单」：只计价；非 2xx 抛带状态码的 QuoteError（同 fetchQuote），网络错误原样抛出。
  it("throws like fetchQuote", async () => {
    stubFetch(json(403, { detail: "csrf_failed" }));
    const error: unknown = await fetchPreviewQuote(7, "stale", "en", LINES).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(QuoteError);
    expect((error as QuoteError).status).toBe(403);
    stubFetch(networkDown);
    await expect(fetchPreviewQuote(7, "csrf-abc", "en", LINES)).rejects.toBeInstanceOf(TypeError);
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "../cart";
import type { Language } from "../i18n/copy";
import { QuoteError, checkoutQuoteBody, fetchCheckoutQuote, fetchQuote, fetchRegions, latestQuoter, latestRuns, quoteRequestBody, quoteUrl } from "./checkout";
import type { CheckoutQuote, CheckoutRegions, DestinationQuote } from "./checkout";

afterEach(() => {
  vi.unstubAllGlobals();
});

const cart: CartLine[] = [
  { sku: "tee-black-m", slug: "crew-neck-tee", quantity: 2 },
  { sku: "candle-std", slug: "soy-wax-candle", quantity: 1 },
];

function quote(subtotal_sen: number): CheckoutQuote {
  return { lines: [], subtotal_sen };
}

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

describe("quote request", () => {
  // SHOP-TASK-018 验收第 3 条「用购物车各行的 SKU 与件数（不带收货国家、不带任何价格）调用计价接口」：请求体每行恰好 sku 与 quantity，不带 slug。
  it("sends only the SKU and the quantity of every line", () => {
    expect(quoteRequestBody(cart)).toEqual({
      lines: [
        { sku: "tee-black-m", quantity: 2 },
        { sku: "candle-std", quantity: 1 },
      ],
    });
    for (const line of quoteRequestBody(cart).lines) {
      expect(Object.keys(line).sort()).toEqual(["quantity", "sku"]);
    }
  });

  // SHOP-TASK-018 验收第 3 条「不带收货国家、不带任何价格」与 UX P04「运费在结账时算」：实际发出的请求只有 lines，地址只带语言，不带 cookie。
  it("posts the lines without a destination, prices or cookies", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<ReturnType<typeof okResponse>>>(() =>
      Promise.resolve(okResponse(quote(11000))),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchQuote("zh", cart)).resolves.toEqual(quote(11000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init]: [string, RequestInit] = fetchMock.mock.calls[0] ?? ["", {}];
    const address = new URL(url, "https://shop.example");
    expect(address.pathname).toBe("/api/checkout/quote");
    expect([...address.searchParams.entries()]).toEqual([["lang", "zh"]]);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("omit");
    expect(typeof init.body).toBe("string");
    const text = init.body as string;
    const body = JSON.parse(text) as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["lines"]);
    expect(body).toEqual(quoteRequestBody(cart));
    expect(text).not.toMatch(/country|state|price|sen|slug/);
  });

  // SHOP-TASK-018 验收第 5 条「计价请求失败时显示 common.error_retry」：非 2xx 抛错（带状态码），由页面显示提示。
  it("throws on a failed response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) })));
    await expect(fetchQuote("en", cart)).rejects.toEqual(new QuoteError(503));
  });

  // UX P03 说明「当前语言缺少商品文案、回退英文时…」与 SHOP-TASK-018 验收第 3 条「显示返回的每行图片、名称与规格」：计价请求带当前语言，返回的名称与规格按当前语言（缺失时回退英文）。
  it("asks in the current language", () => {
    expect(quoteUrl("ms")).toBe("/api/checkout/quote?lang=ms");
  });
});

describe("only the latest quote is used", () => {
  interface Pending {
    signal: AbortSignal | undefined;
    resolve: (value: CheckoutQuote) => void;
    reject: (reason: unknown) => void;
  }

  function deferredRequests() {
    const pending: Pending[] = [];
    const request = (_language: Language, _lines: readonly CartLine[], signal?: AbortSignal) =>
      new Promise<CheckoutQuote>((resolve, reject) => {
        pending.push({ signal, resolve, reject });
      });
    return { pending, request };
  }

  // SHOP-TASK-018 验收第 3 条「连续修改时只采用最后一次请求的结果」：先发的请求后返回时被丢弃，并且已被中止。
  it("drops an earlier request that returns later", async () => {
    const { pending, request } = deferredRequests();
    const quoter = latestQuoter(request);
    const first = quoter.run("en", cart);
    const second = quoter.run("en", cart);
    expect(pending[0]?.signal?.aborted).toBe(true);
    pending[1]?.resolve(quote(200));
    pending[0]?.resolve(quote(100));
    await expect(second).resolves.toEqual({ status: "ready", quote: quote(200) });
    await expect(first).resolves.toBeNull();
  });

  // 同一条：先发的请求先返回也不采用。
  it("drops an earlier request that returns first", async () => {
    const { pending, request } = deferredRequests();
    const quoter = latestQuoter(request);
    const first = quoter.run("en", cart);
    const second = quoter.run("en", cart);
    pending[0]?.resolve(quote(100));
    await expect(first).resolves.toBeNull();
    pending[1]?.resolve(quote(200));
    await expect(second).resolves.toEqual({ status: "ready", quote: quote(200) });
  });

  // SHOP-TASK-018 验收第 5 条「计价请求失败时显示 common.error_retry」：最新的请求失败时结果为 error；被取代的失败不采用。
  it("reports the failure of the latest request only", async () => {
    const { pending, request } = deferredRequests();
    const quoter = latestQuoter(request);
    const first = quoter.run("en", cart);
    const second = quoter.run("en", cart);
    pending[0]?.reject(new Error("aborted"));
    pending[1]?.reject(new QuoteError(500));
    await expect(first).resolves.toBeNull();
    await expect(second).resolves.toEqual({ status: "error" });
  });

  // 派生实现约束（实现选择）：守住同一条「只采用最后一次请求的结果」——离开页面或购物车清空后（cancel），尚未返回的请求被中止且不采用。
  it("drops a request after cancel", async () => {
    const { pending, request } = deferredRequests();
    const quoter = latestQuoter(request);
    const run = quoter.run("en", cart);
    quoter.cancel();
    expect(pending[0]?.signal?.aborted).toBe(true);
    pending[0]?.resolve(quote(100));
    await expect(run).resolves.toBeNull();
  });

  // SHOP-TASK-025 验收第 6 条「改国家或州属后重新计价，只采用最后一次请求的结果」：结账页用同一套规则，先发的请求无论先后返回都作废。
  it("keeps only the latest checkout quote", async () => {
    const runs = latestRuns<number>();
    const resolvers: ((value: number) => void)[] = [];
    const signals: AbortSignal[] = [];
    const request = (signal: AbortSignal) =>
      new Promise<number>((resolve) => {
        signals.push(signal);
        resolvers.push(resolve);
      });
    const first = runs.run(request);
    const second = runs.run(request);
    expect(signals[0]?.aborted).toBe(true);
    resolvers[1]?.(2);
    resolvers[0]?.(1);
    await expect(first).resolves.toBeNull();
    await expect(second).resolves.toEqual({ status: "ready", quote: 2 });
  });
});

function destinationQuote(overrides: Partial<DestinationQuote> = {}): DestinationQuote {
  return { lines: [], subtotal_sen: 11000, shipping: null, total_sen: null, fx_reference: null, can_place_order: false, ...overrides };
}

describe("checkout quote with a destination", () => {
  // SHOP-TASK-025 验收第 6 条「用购物车各行 SKU 与件数（选了国家后加国家与州属）调用计价接口」：未选国家时与购物车页相同只有 lines；
  // 马来西亚加国家与州属代码，其他国家只加国家代码；任何时候都不带价格或金额。
  it("adds the country, and the state for Malaysia only", () => {
    expect(checkoutQuoteBody(cart, null)).toEqual(quoteRequestBody(cart));
    expect(checkoutQuoteBody(cart, { country_code: "MY", state_code: "MY-10" })).toEqual({ ...quoteRequestBody(cart), country_code: "MY", state_code: "MY-10" });
    const other = checkoutQuoteBody(cart, { country_code: "GB", state_code: null });
    expect(other).toEqual({ ...quoteRequestBody(cart), country_code: "GB" });
    expect(Object.keys(other)).toEqual(["lines", "country_code"]);
    expect(JSON.stringify(other)).not.toMatch(/price|sen|total|shipping|fx|amount/);
  });

  // 同一条：实际发出的请求地址只带语言，不带 cookie；接口给的运费、合计与参考外币原样交给页面。
  it("posts the destination and returns the server amounts", async () => {
    const answer = destinationQuote({
      shipping: { fee_sen: 2500, zone_code: "GB", version: 1 },
      total_sen: 13500,
      fx_reference: { currency_code: "GBP", currency_decimals: 2, amount_minor: 2295, version: 1 },
      can_place_order: true,
    });
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<ReturnType<typeof okResponse>>>(() => Promise.resolve(okResponse(answer)));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchCheckoutQuote("ms", cart, { country_code: "GB", state_code: null })).resolves.toEqual(answer);
    const [url, init]: [string, RequestInit] = fetchMock.mock.calls[0] ?? ["", {}];
    expect(url).toBe("/api/checkout/quote?lang=ms");
    expect(init.credentials).toBe("omit");
    expect(JSON.parse(init.body as string)).toEqual({ ...quoteRequestBody(cart), country_code: "GB" });
  });
});

describe("checkout regions", () => {
  const regions: CheckoutRegions = {
    regions: [
      { code: "GB", calling_code: 44 },
      { code: "MY", calling_code: 60 },
    ],
    my_states: [{ code: "MY-01", name: "Johor" }],
    default_phone_region: "MY",
  };

  // SHOP-TASK-025 验收第 3 条「打开时调用…SHOP-TASK-023 的 GET /api/checkout/regions」：固定地址、GET、不带 cookie，三项原样交给页面。
  it("reads the regions without cookies", async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<ReturnType<typeof okResponse>>>(() => Promise.resolve(okResponse(regions)));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchRegions()).resolves.toEqual(regions);
    const [url, init]: [string, RequestInit] = fetchMock.mock.calls[0] ?? ["", {}];
    expect(url).toBe("/api/checkout/regions");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("omit");
  });

  // SHOP-TASK-025 验收第 3 条「失败时显示 common.error_retry」：非 2xx 与缺少列表的回答都抛错。
  it("throws when the regions cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })));
    await expect(fetchRegions()).rejects.toEqual(new QuoteError(500));
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(okResponse({ regions: [] }))));
    await expect(fetchRegions()).rejects.toBeInstanceOf(QuoteError);
  });
});

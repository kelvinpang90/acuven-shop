import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "../cart";
import type { Language } from "../i18n/copy";
import { QuoteError, fetchQuote, latestQuoter, quoteRequestBody, quoteUrl } from "./checkout";
import type { CheckoutQuote } from "./checkout";

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
});

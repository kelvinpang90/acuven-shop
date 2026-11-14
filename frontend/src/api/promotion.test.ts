import { afterEach, describe, expect, it, vi } from "vitest";

import { PromotionError, fetchPromotion, promotionUrl } from "./promotion";
import type { PromotionResponse } from "./promotion";

afterEach(() => {
  vi.unstubAllGlobals();
});

const NO_PROMOTION: PromotionResponse = { promotion: null, server_time: "2026-11-13T04:00:00Z" };

describe("promotionUrl", () => {
  // DESIGN 1.23 第 10 条「公开接口按当前时刻给出生效活动的展示数据：活动 ID、所选语言的标题与文案（缺译回退英文）」：只带所选语言。
  it.each(["en", "zh", "ms"] as const)("asks for the active promotion in %s", (language) => {
    expect(promotionUrl(language)).toBe(`/api/promotion?lang=${language}`);
  });
});

describe("fetchPromotion", () => {
  // DESIGN 1.23 第 10 条「公开接口按当前时刻给出生效活动的展示数据」：公开接口只用 GET、不发 cookie，响应原样交给页面。
  it("sends a plain GET without credentials", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify(NO_PROMOTION), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    const url = promotionUrl("zh");
    await expect(fetchPromotion(url)).resolves.toEqual(NO_PROMOTION);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(url, {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: null,
    });
  });

  // DESIGN 1.23 第 10 条「没有生效活动时这些字段为空，前台不显示任何活动内容」：失败带状态码交给页面，不当作有活动。
  it.each([404, 422, 500, 503])("rejects with PromotionError on status %i", async (status) => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status }))));
    const error = await fetchPromotion(promotionUrl("en")).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(PromotionError);
    expect((error as PromotionError).status).toBe(status);
  });

  // UX 0.20「节日促销活动」「到 0 时页面重新读取活动数据」：重新读取时旧请求可以中止，中止的错误原样抛出，不变成 PromotionError。
  it("passes the abort signal and rethrows the abort error", async () => {
    const abortError = new DOMException("The operation was aborted.", "AbortError");
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      const signal = init.signal;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          reject(abortError);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const url = promotionUrl("ms");
    const pending = fetchPromotion(url, controller.signal);
    controller.abort();
    const error = await pending.catch((caught: unknown) => caught);
    expect(fetchMock).toHaveBeenCalledWith(url, expect.objectContaining({ signal: controller.signal }));
    expect(error).toBe(abortError);
    expect(error).not.toBeInstanceOf(PromotionError);
  });
});

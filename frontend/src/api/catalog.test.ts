import { afterEach, describe, expect, it, vi } from "vitest";

import {
  categoriesUrl,
  featuredProductsUrl,
  fetchCatalog,
  optionsUrl,
  productsUrl,
} from "./catalog";

afterEach(() => {
  vi.unstubAllGlobals();
});

function params(url: string): [string, string][] {
  return [...new URL(url, "https://shop.example").searchParams.entries()];
}

function pathOf(url: string): string {
  return new URL(url, "https://shop.example").pathname;
}

describe("catalog request addresses", () => {
  // 验收：分类与规格筛选项接口只带语言参数。
  it("asks for categories and filter options in the current language", () => {
    expect(pathOf(categoriesUrl("zh"))).toBe("/api/catalog/categories");
    expect(params(categoriesUrl("zh"))).toEqual([["lang", "zh"]]);
    expect(pathOf(optionsUrl("ms"))).toBe("/api/catalog/options");
    expect(params(optionsUrl("ms"))).toEqual([["lang", "ms"]]);
  });

  // 验收：筛选、排序与分页全部交给商品列表接口——category 与 option 按接口要求重复出现，不合并成一个值。
  it("passes search, repeated categories and options, sort and page to the product list", () => {
    const url = productsUrl("en", {
      q: "tote bag",
      categories: ["bags", "home"],
      options: ["colour:black", "size:m"],
      sort: "price_desc",
      page: 3,
    });
    expect(pathOf(url)).toBe("/api/catalog/products");
    expect(params(url)).toEqual([
      ["lang", "en"],
      ["q", "tote bag"],
      ["category", "bags"],
      ["category", "home"],
      ["option", "colour:black"],
      ["option", "size:m"],
      ["sort", "price_desc"],
      ["page", "3"],
    ]);
  });

  it("asks for the newest first page when nothing is chosen", () => {
    expect(params(productsUrl("ms"))).toEqual([
      ["lang", "ms"],
      ["sort", "newest"],
      ["page", "1"],
    ]);
  });

  // UX P01「精选商品」：未挑选时显示按最新排序的前 4 件。
  it("asks for the four newest products for the featured block", () => {
    expect(params(featuredProductsUrl("zh"))).toEqual([
      ["lang", "zh"],
      ["sort", "newest"],
      ["page", "1"],
      ["page_size", "4"],
    ]);
  });
});

describe("fetchCatalog", () => {
  // 验收：请求只带语言与筛选参数，不带任何个人资料——不发 cookie，只用 GET，没有请求体。
  it("sends a plain GET without credentials", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify([{ slug: "bags" }]), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    const url = categoriesUrl("en");
    await expect(fetchCatalog(url)).resolves.toEqual([{ slug: "bags" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(url, {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal: null,
    });
  });

  // 验收：请求失败时页面显示 common.error_retry——非 2xx 与网络错误都作为失败交给页面。
  it("rejects on an error status or a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 500 }))));
    await expect(fetchCatalog(categoriesUrl("en"))).rejects.toThrow("500");

    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(fetchCatalog(categoriesUrl("en"))).rejects.toThrow("Failed to fetch");
  });

  it("passes the abort signal so a superseded request can be cancelled", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("[]")));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const url = optionsUrl("en");
    await fetchCatalog(url, controller.signal);
    expect(fetchMock).toHaveBeenCalledWith(url, expect.objectContaining({ signal: controller.signal }));
  });
});

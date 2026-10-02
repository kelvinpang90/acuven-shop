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
  // SHOP-TASK-015 验收第 7 条「请求只带语言与筛选参数」：分类与规格筛选项接口只带语言参数。
  it("asks for categories and filter options in the current language", () => {
    expect(pathOf(categoriesUrl("zh"))).toBe("/api/catalog/categories");
    expect(params(categoriesUrl("zh"))).toEqual([["lang", "zh"]]);
    expect(pathOf(optionsUrl("ms"))).toBe("/api/catalog/options");
    expect(params(optionsUrl("ms"))).toEqual([["lang", "ms"]]);
  });

  // SHOP-TASK-015 验收第 5 条「筛选、排序与分页全部交给商品列表接口」与第 8 条「重复的 category 与 option」：按接口要求重复出现，不合并成一个值。
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

  // 派生实现约束（实现选择）：守住 UX P02 的搜索、排序与分页（目的「浏览、搜索、按分类及属性筛选」，线框的 [list.sort] 与页码）和 SHOP-TASK-015 验收第 5 条「筛选、排序与分页全部交给商品列表接口」——无条件时也显式请求接口默认的 newest 与第 1 页。
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
  // SHOP-TASK-015 验收第 7 条「请求只带语言与筛选参数，不带任何个人资料」：不发 cookie，只用 GET，没有请求体。
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

  // SHOP-TASK-015 验收第 7 条「请求失败时…显示 common.error_retry」：非 2xx 与网络错误都作为失败交给页面。
  it("rejects on an error status or a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 500 }))));
    await expect(fetchCatalog(categoriesUrl("en"))).rejects.toThrow("500");

    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(fetchCatalog(categoriesUrl("en"))).rejects.toThrow("Failed to fetch");
  });

  // 派生实现约束（实现选择）：守住 SHOP-TASK-015 验收第 6 条「切换语言时按新语言重新请求」——取消信号传给 fetch，语言或条件变化时作废旧请求，防止晚到的旧请求结果覆盖新请求。
  it("passes the abort signal so a superseded request can be cancelled", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response("[]")));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const url = optionsUrl("en");
    await fetchCatalog(url, controller.signal);
    expect(fetchMock).toHaveBeenCalledWith(url, expect.objectContaining({ signal: controller.signal }));
  });
});

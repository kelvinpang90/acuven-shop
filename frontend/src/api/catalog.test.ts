import { afterEach, describe, expect, it, vi } from "vitest";

import type { StorefrontPreview } from "../preview";
import {
  CatalogError,
  categoriesUrl,
  failedRemote,
  featuredBySlugsUrl,
  featuredProductsUrl,
  fetchCatalog,
  optionsUrl,
  productDetailUrl,
  productsUrl,
} from "./catalog";
import type { ProductListRequest } from "./catalog";

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

  // SHOP-TASK-017 验收第 2 条「路由 /products/<slug> 用商品详情接口取数（随当前语言）」与「详情请求用解码后的 slug，请求路径里只编码一次」。
  it.each([
    ["crew-neck-tee", "/api/catalog/products/crew-neck-tee"],
    ["a b", "/api/catalog/products/a%20b"],
    [":", "/api/catalog/products/%3A"],
    ["a%20b", "/api/catalog/products/a%2520b"],
    ["中", "/api/catalog/products/%E4%B8%AD"],
  ])("asks for the product %j with its path encoded once", (slug, path) => {
    const url = productDetailUrl("zh", slug);
    expect(url).toBe(`${path}?lang=zh`);
    expect(params(url)).toEqual([["lang", "zh"]]);
    expect(decodeURIComponent(pathOf(url).slice("/api/catalog/products/".length))).toBe(slug);
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

  // SHOP-TASK-054 验收「featured_slugs 非空时经 SHOP-TASK-053 的 slug 参数取商品卡片（frontend/src/api/catalog.ts 新增对应网址函数）」
  // 与 UX P01（0.6）「显示 A08 挑选的商品（最多 4 件）」：每个挑选的 slug 一个可重复的 slug 参数，按挑选顺序写出，最多 4 件放得下一页。
  it("asks for the picked featured products by repeated slug", () => {
    const url = featuredBySlugsUrl("ms", ["tote-bag", "crew-neck-tee", "mug"]);
    expect(pathOf(url)).toBe("/api/catalog/products");
    expect(params(url)).toEqual([
      ["lang", "ms"],
      ["slug", "tote-bag"],
      ["slug", "crew-neck-tee"],
      ["slug", "mug"],
      ["sort", "newest"],
      ["page", "1"],
      ["page_size", "4"],
    ]);
  });

  // SHOP-TASK-054 验收「slug 网址的编码」：slug 作为查询参数的值编码一次，接口解码后得到原样的 slug，不会被拆成别的参数。
  it.each([
    ["a b", "slug=a+b"],
    ["a&page=9", "slug=a%26page%3D9"],
    ["a%20b", "slug=a%2520b"],
    ["中", "slug=%E4%B8%AD"],
    ["a+b", "slug=a%2Bb"],
  ])("encodes the picked slug %j once", (slug, encoded) => {
    const url = featuredBySlugsUrl("en", [slug]);
    expect(url).toContain(`&${encoded}&`);
    expect(new URL(url, "https://shop.example").searchParams.getAll("slug")).toEqual([slug]);
    expect(new URL(url, "https://shop.example").searchParams.getAll("page")).toEqual(["1"]);
  });

  // SHOP-TASK-053「不给时条件不变，与之前完全相同」：其他列表请求不带 slug 参数。
  it("adds no slug parameter to other product list requests", () => {
    expect(productsUrl("en", { q: "tee" })).not.toContain("slug=");
    expect(featuredProductsUrl("en")).not.toContain("slug=");
  });

  // SHOP-TASK-262 验收「productsUrl 照落实写法支持 sale」，守住 UX 0.20 P15「商品区列出生效活动全部商品级规则范围内的上架商品」：
  // 专题页的列表请求在现有参数之后带 sale=true，由服务端按生效活动的商品级规则取商品，页面不自行过滤。
  it("asks for the sale products after the existing parameters when sale is true", () => {
    const url = productsUrl("zh", { categories: ["bags"], sort: "price_asc", page: 2, pageSize: 12, sale: true });
    expect(pathOf(url)).toBe("/api/catalog/products");
    expect(params(url)).toEqual([
      ["lang", "zh"],
      ["category", "bags"],
      ["sort", "price_asc"],
      ["page", "2"],
      ["page_size", "12"],
      ["sale", "true"],
    ]);
    expect(productsUrl("en", { sale: true })).toBe("/api/catalog/products?lang=en&sort=newest&page=1&sale=true");
  });

  // SHOP-TASK-262 验收「sale 省略或为假时地址与现在逐字相同」，守住 UX 0.20 P15 只在专题页收窄商品范围：其他列表请求不带 sale 参数。
  it("keeps the product list address unchanged when sale is omitted or false", () => {
    const request: ProductListRequest = { q: "tee", categories: ["apparel"], options: ["size:m"], sort: "price_desc", page: 3, pageSize: 4 };
    const unchanged = "/api/catalog/products?lang=ms&q=tee&category=apparel&option=size%3Am&sort=price_desc&page=3&page_size=4";
    expect(productsUrl("ms", request)).toBe(unchanged);
    expect(productsUrl("ms", { ...request, sale: false })).toBe(unchanged);
    expect(productsUrl("ms")).toBe("/api/catalog/products?lang=ms&sort=newest&page=1");
    expect(productsUrl("ms", { sale: false })).toBe("/api/catalog/products?lang=ms&sort=newest&page=1");
    expect(featuredProductsUrl("en")).not.toContain("sale=");
    expect(featuredBySlugsUrl("en", ["mug"])).not.toContain("sale=");
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

  // SHOP-TASK-017 验收第 2 条「商品不存在或未发布（接口 404）时替换为商品列表页」：失败带状态码，页面据此区分 404 与其他失败。
  it("keeps the status of a failed request", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
    const notFound = await fetchCatalog(productDetailUrl("en", "gone")).catch((error: unknown) => error);
    expect(notFound).toBeInstanceOf(CatalogError);
    expect((notFound as CatalogError).status).toBe(404);
    expect(failedRemote(notFound)).toEqual({ status: "not_found" });
  });

  // SHOP-TASK-015 验收第 7 条「请求失败时…显示 common.error_retry」：只有 404 是「不存在」，其他状态码与网络错误仍是一般失败。
  it("treats every other failure as an error", () => {
    expect(failedRemote(new CatalogError(500))).toEqual({ status: "error" });
    expect(failedRemote(new CatalogError(422))).toEqual({ status: "error" });
    expect(failedRemote(new TypeError("Failed to fetch"))).toEqual({ status: "error" });
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

describe("fetchCatalog in a storefront preview", () => {
  const PREVIEW: StorefrontPreview = { promotionId: 7, csrfToken: "csrf-token" };

  function stubOk() {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => Promise.resolve(new Response("{}", { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  function sent(fetchMock: ReturnType<typeof stubOk>, index: number): [string, RequestInit] {
    const call = fetchMock.mock.calls[index];
    if (call === undefined) {
      throw new Error(`no request ${String(index)}`);
    }
    return call;
  }

  // UX 0.20 A12「前台预览」「预览凭管理员会话经后台接口取数」：预览中的商品列表（含 sale）与详情换成后台预览接口，
  // 查询参数与 slug 原样保留，凭同源 cookie、不缓存地读取。
  it("reads the product list and detail from the preview endpoints with the admin session", async () => {
    const fetchMock = stubOk();
    const controller = new AbortController();
    await fetchCatalog(productsUrl("ms", { categories: ["bags"], sort: "price_asc", page: 2, sale: true }), controller.signal, PREVIEW);
    await fetchCatalog(productDetailUrl("zh", "a b"), controller.signal, PREVIEW);
    expect(sent(fetchMock, 0)).toEqual([
      "/api/admin/promotions/7/preview/products?lang=ms&category=bags&sort=price_asc&page=2&sale=true",
      { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal },
    ]);
    expect(sent(fetchMock, 1)).toEqual([
      "/api/admin/promotions/7/preview/products/a%20b?lang=zh",
      { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: controller.signal },
    ]);
  });

  // 同一句「预览凭管理员会话经后台接口取数」：预览接口没有分类与规格选项（与活动无关），预览中它们照旧取公开接口，
  // 地址不改写、不带 cookie、不设 cache，与不在预览中时逐字相同。
  it("keeps categories and filter options on the public endpoints", async () => {
    const fetchMock = stubOk();
    for (const url of [categoriesUrl("en"), optionsUrl("zh")]) {
      await fetchCatalog(url, undefined, PREVIEW);
      await fetchCatalog(url);
    }
    for (const [index, url] of [categoriesUrl("en"), categoriesUrl("en"), optionsUrl("zh"), optionsUrl("zh")].entries()) {
      const [sentUrl, init] = sent(fetchMock, index);
      expect(sentUrl).toBe(url);
      expect(init).toEqual({ method: "GET", credentials: "omit", headers: { Accept: "application/json" }, signal: null });
      expect("cache" in init).toBe(false);
    }
  });

  // UX 0.20 A12「前台预览」只在预览中「凭管理员会话经后台接口取数」：preview 为 null 或省略时，列表与详情的地址与选项与之前逐字相同。
  it("leaves every request unchanged outside a preview", async () => {
    const fetchMock = stubOk();
    const list = productsUrl("en", { sale: true });
    const detail = productDetailUrl("en", "tee");
    await fetchCatalog(list, undefined, null);
    await fetchCatalog(list);
    await fetchCatalog(detail, undefined, null);
    await fetchCatalog(detail);
    const plain = { method: "GET", credentials: "omit", headers: { Accept: "application/json" }, signal: null };
    expect(fetchMock.mock.calls).toEqual([
      [list, plain],
      [list, plain],
      [detail, plain],
      [detail, plain],
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect("cache" in init).toBe(false);
    }
  });

  // 同一句「预览凭管理员会话经后台接口取数」：预览接口的失败照公开接口带状态码（详情 404 为商品不存在）。
  it("keeps the status of a failed preview request", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
    const notFound = await fetchCatalog(productDetailUrl("en", "gone"), undefined, PREVIEW).catch((error: unknown) => error);
    expect(notFound).toBeInstanceOf(CatalogError);
    expect(failedRemote(notFound)).toEqual({ status: "not_found" });
  });
});

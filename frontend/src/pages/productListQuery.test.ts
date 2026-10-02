import { describe, expect, it } from "vitest";

import {
  DEFAULT_PRODUCT_LIST_QUERY,
  buildProductListSearch,
  categorySearch,
  clearFilters,
  keywordSearch,
  parseProductListQuery,
  toProductListRequest,
  withFilters,
} from "./productListQuery";
import type { ProductListQuery } from "./productListQuery";

const full: ProductListQuery = {
  q: "tote bag",
  categories: ["bags", "home"],
  options: ["colour:black", "size:m"],
  sort: "price_asc",
  page: 3,
};

describe("product list query string", () => {
  // 验收：搜索词、分类、规格筛选、排序与页码放在查询参数里，可分享——生成后再解析得到同一组条件。
  it.each<[string, ProductListQuery]>([
    ["defaults", DEFAULT_PRODUCT_LIST_QUERY],
    ["every condition", full],
    ["only a category", { ...DEFAULT_PRODUCT_LIST_QUERY, categories: ["bags"] }],
    ["only sort and page", { ...DEFAULT_PRODUCT_LIST_QUERY, sort: "price_desc", page: 12 }],
    ["text that needs escaping", { ...DEFAULT_PRODUCT_LIST_QUERY, q: "a&b=c?d #e+f 中文" }],
  ])("round-trips %s", (_name, query) => {
    expect(parseProductListQuery(buildProductListSearch(query))).toEqual(query);
  });

  // 同一组条件只有一种写法：默认值省略，全部默认时没有查询串；解析再生成得到同一个查询串。
  it("writes one canonical form", () => {
    expect(buildProductListSearch(DEFAULT_PRODUCT_LIST_QUERY)).toBe("");
    const search = buildProductListSearch(full);
    expect(search).toBe("?q=tote+bag&category=bags&category=home&option=colour%3Ablack&option=size%3Am&sort=price_asc&page=3");
    expect(buildProductListSearch(parseProductListQuery(search))).toBe(search);
  });

  // 接口的 category 与 option 可重复，网址用同样的写法。
  it("reads repeated category and option parameters", () => {
    const query = parseProductListQuery("?category=bags&option=colour:black&category=home&option=size:m");
    expect(query.categories).toEqual(["bags", "home"]);
    expect(query.options).toEqual(["colour:black", "size:m"]);
  });

  // 验收：非法值回退默认。
  it.each([
    ["?sort=cheapest", { sort: "newest" }],
    ["?sort=PRICE_ASC", { sort: "newest" }],
    ["?page=0", { page: 1 }],
    ["?page=-2", { page: 1 }],
    ["?page=1.5", { page: 1 }],
    ["?page=abc", { page: 1 }],
    ["?page=02", { page: 1 }],
    ["?page=99999999999999999999", { page: 1 }],
    ["?q=" + "x".repeat(101), { q: "" }],
    ["?q=%20%20", { q: "" }],
    ["?category=", { categories: [] }],
    ["?option=colour", { options: [] }],
    ["?option=colour:", { options: [] }],
    ["?option=:black", { options: [] }],
    ["?option=a:b:c", { options: [] }],
  ])("falls back to the default for %s", (search, expected) => {
    expect(parseProductListQuery(search)).toEqual({ ...DEFAULT_PRODUCT_LIST_QUERY, ...expected });
  });

  // 一项非法只丢掉那一项，其余条件照用；重复的值只算一次；不认识的参数（如语言）不读。
  it("keeps the valid parts", () => {
    const query = parseProductListQuery("?q=%20tee%20&category=bags&category=bags&option=bad&option=size:m&sort=nope&page=2&lang=zh");
    expect(query).toEqual({ q: "tee", categories: ["bags"], options: ["size:m"], sort: "newest", page: 2 });
  });

  it("accepts a search term of exactly 100 characters", () => {
    expect(parseProductListQuery("?q=" + "x".repeat(100)).q).toHaveLength(100);
  });
});

describe("links into the product list", () => {
  // UX P01：「按分类浏览」链到按该分类筛选的商品列表。
  it("filters by one category", () => {
    expect(categorySearch("bags")).toBe("?category=bags");
  });

  // UX「全局框架」：页头搜索跳到带搜索词的列表；空白搜索打开不带条件的列表。
  it("searches by keyword", () => {
    expect(keywordSearch("  tote ")).toBe("?q=tote");
    expect(keywordSearch("   ")).toBe("");
  });
});

describe("changing conditions", () => {
  // 换筛选或排序时回到第 1 页，不停在可能已不存在的页码上。
  it("goes back to the first page when a filter or the sort changes", () => {
    expect(withFilters(full, { categories: ["bags"] })).toEqual({ ...full, categories: ["bags"], page: 1 });
    expect(withFilters(full, { sort: "newest" })).toEqual({ ...full, sort: "newest", page: 1 });
  });

  // UX P02：清除筛选后留在本页；排序保留。
  it("clears search and filters but keeps the sort", () => {
    expect(clearFilters(full)).toEqual({ ...DEFAULT_PRODUCT_LIST_QUERY, sort: "price_asc" });
  });

  // 验收：前端不自行过滤或排序——网址里的全部条件原样交给商品列表接口，手机「加载更多」只换页码。
  it("hands every condition to the product list request", () => {
    expect(toProductListRequest(full)).toEqual({
      q: "tote bag",
      categories: ["bags", "home"],
      options: ["colour:black", "size:m"],
      sort: "price_asc",
      page: 3,
    });
    expect(toProductListRequest(full, 4).page).toBe(4);
  });
});

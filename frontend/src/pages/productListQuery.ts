import { isSortOrder } from "../api/catalog";
import type { ProductListRequest, SortOrder } from "../api/catalog";

// 商品列表 P02 的查询参数：搜索词、分类、规格筛选、排序与页码都放在网址里，可分享、前进后退可用。
// 参数名与商品列表接口相同（q、可重复的 category 与 option、sort、page），不放语言、订单号或电话。
// 解析时非法值回退默认：非法的单项丢掉，其余照用；生成时省略默认值，同一组条件只有一种写法。

export interface ProductListQuery {
  q: string;
  categories: readonly string[];
  // <规格名 code>:<规格值 code>
  options: readonly string[];
  sort: SortOrder;
  page: number;
}

export const DEFAULT_PRODUCT_LIST_QUERY: ProductListQuery = {
  q: "",
  categories: [],
  options: [],
  sort: "newest",
  page: 1,
};

// 与接口的长度上限一致：搜索词 100 个字符（接口 max_length），分类 slug 100，规格名与规格值 code 各 50。
const MAX_QUERY_LENGTH = 100;
const MAX_SLUG_LENGTH = 100;
const MAX_CODE_LENGTH = 50;
// 页码：不带前导零的正整数，位数有上限，不会变成非安全整数。
const PAGE_PATTERN = /^[1-9][0-9]{0,5}$/;

function isCategory(value: string): boolean {
  return value !== "" && value.length <= MAX_SLUG_LENGTH && value.trim() === value;
}

function isOption(value: string): boolean {
  const mark = value.indexOf(":");
  if (mark < 0) {
    return false;
  }
  const option = value.slice(0, mark);
  const choice = value.slice(mark + 1);
  return (
    option !== "" &&
    choice !== "" &&
    !choice.includes(":") &&
    option.length <= MAX_CODE_LENGTH &&
    choice.length <= MAX_CODE_LENGTH &&
    option.trim() === option &&
    choice.trim() === choice
  );
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values)];
}

export function parseProductListQuery(search: string): ProductListQuery {
  const params = new URLSearchParams(search);
  const q = (params.get("q") ?? "").trim();
  const sort = params.get("sort");
  const page = params.get("page") ?? "";
  return {
    q: q.length <= MAX_QUERY_LENGTH ? q : "",
    categories: unique(params.getAll("category").filter(isCategory)),
    options: unique(params.getAll("option").filter(isOption)),
    sort: isSortOrder(sort) ? sort : DEFAULT_PRODUCT_LIST_QUERY.sort,
    page: PAGE_PATTERN.test(page) ? Number(page) : DEFAULT_PRODUCT_LIST_QUERY.page,
  };
}

// 生成查询串（以 ? 开头；全是默认值时为空串）。
export function buildProductListSearch(query: ProductListQuery): string {
  const params = new URLSearchParams();
  const q = query.q.trim();
  if (q !== "" && q.length <= MAX_QUERY_LENGTH) {
    params.set("q", q);
  }
  for (const category of unique(query.categories.filter(isCategory))) {
    params.append("category", category);
  }
  for (const option of unique(query.options.filter(isOption))) {
    params.append("option", option);
  }
  if (query.sort !== DEFAULT_PRODUCT_LIST_QUERY.sort && isSortOrder(query.sort)) {
    params.set("sort", query.sort);
  }
  if (query.page > 1 && PAGE_PATTERN.test(String(query.page))) {
    params.set("page", String(query.page));
  }
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}

// 只筛选一个分类的列表（首页「按分类浏览」的链接）。
export function categorySearch(slug: string): string {
  return buildProductListSearch({ ...DEFAULT_PRODUCT_LIST_QUERY, categories: [slug] });
}

// 只带搜索词的列表（页头搜索框）。
export function keywordSearch(q: string): string {
  return buildProductListSearch({ ...DEFAULT_PRODUCT_LIST_QUERY, q });
}

// 清除筛选：去掉搜索词、分类、规格与页码，保留排序。
export function clearFilters(query: ProductListQuery): ProductListQuery {
  return { ...DEFAULT_PRODUCT_LIST_QUERY, sort: query.sort };
}

export function hasFilters(query: ProductListQuery): boolean {
  return query.q !== "" || query.categories.length > 0 || query.options.length > 0;
}

// 换筛选或排序时回到第 1 页。
export function withFilters(query: ProductListQuery, change: Partial<Omit<ProductListQuery, "page">>): ProductListQuery {
  return { ...query, ...change, page: 1 };
}

export function toggle(values: readonly string[], value: string): string[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

// 网址里的条件换成商品列表接口的参数（页码由调用方给出：手机「加载更多」请求下一页）。
export function toProductListRequest(query: ProductListQuery, page = query.page): ProductListRequest {
  return { q: query.q, categories: query.categories, options: query.options, sort: query.sort, page };
}

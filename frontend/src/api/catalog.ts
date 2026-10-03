import { useEffect, useState } from "react";

import type { Language } from "../i18n/copy";

// 商品目录的公开只读接口（app/api/catalog.py，SHOP-TASK-005 与 SHOP-TASK-013）。
// 请求只带语言与筛选参数：不带 cookie（credentials: "omit"），不带任何个人资料。
// 筛选、排序与分页全部交给服务端，页面不自行过滤或排序；金额是服务端给的整数仙，页面只格式化。

const CATALOG_BASE = "/api/catalog";

export const SORT_ORDERS = ["newest", "price_asc", "price_desc"] as const;
export type SortOrder = (typeof SORT_ORDERS)[number];

export function isSortOrder(value: unknown): value is SortOrder {
  return typeof value === "string" && (SORT_ORDERS as readonly string[]).includes(value);
}

// 首页「精选商品」：后台挑选尚未实现，按 UX P01「未挑选」时的规则取最新的 4 件。
export const FEATURED_COUNT = 4;

// 商品文案按请求语言给出，缺少时为英文并标 english_fallback。
export interface LocalizedText {
  text: string;
  english_fallback: boolean;
}

export interface CategoryListItem {
  slug: string;
  name: LocalizedText;
  // 该分类最新已发布商品的首张图片引用；没有时为 null。
  image: string | null;
}

export interface ProductSummary {
  slug: string;
  name: LocalizedText;
  category: { slug: string; name: LocalizedText };
  image: string | null;
  // 启用规格中的最低单价，MYR 整数仙。
  min_price_sen: number;
  has_multiple_variants: boolean;
  sold_out_today: boolean;
}

export interface ProductPage {
  total: number;
  page: number;
  page_size: number;
  items: ProductSummary[];
}

export interface FilterOptionValue {
  code: string;
  name: LocalizedText;
}

export interface FilterOption {
  code: string;
  name: LocalizedText;
  values: FilterOptionValue[];
}

// 商品详情的一个启用 SKU：options 为规格名 code → 规格值 code。
export interface VariantDetail {
  sku: string;
  options: Readonly<Record<string, string>>;
  // MYR 整数仙。
  price_sen: number;
  // 当日可用库存，只作提示。
  available_stock: number;
}

export interface ProductDetail {
  slug: string;
  name: LocalizedText;
  description: LocalizedText;
  category: { slug: string; name: LocalizedText };
  images: string[];
  options: FilterOption[];
  // 只有启用的 SKU。
  variants: VariantDetail[];
  // 每单限购：该商品所有 SKU 在一张购物车或订单中的件数合计上限。
  max_per_order: number;
}

// 商品列表接口的参数；category 与 option 可重复，option 写成 <规格名 code>:<规格值 code>。
export interface ProductListRequest {
  q?: string | undefined;
  categories?: readonly string[] | undefined;
  options?: readonly string[] | undefined;
  sort?: SortOrder | undefined;
  page?: number | undefined;
  pageSize?: number | undefined;
}

export function categoriesUrl(language: Language): string {
  return `${CATALOG_BASE}/categories?${new URLSearchParams({ lang: language }).toString()}`;
}

export function optionsUrl(language: Language): string {
  return `${CATALOG_BASE}/options?${new URLSearchParams({ lang: language }).toString()}`;
}

export function productsUrl(language: Language, request: ProductListRequest = {}): string {
  const params = new URLSearchParams({ lang: language });
  if (request.q) {
    params.set("q", request.q);
  }
  for (const category of request.categories ?? []) {
    params.append("category", category);
  }
  for (const option of request.options ?? []) {
    params.append("option", option);
  }
  params.set("sort", request.sort ?? "newest");
  params.set("page", String(request.page ?? 1));
  if (request.pageSize !== undefined) {
    params.set("page_size", String(request.pageSize));
  }
  return `${CATALOG_BASE}/products?${params.toString()}`;
}

export function featuredProductsUrl(language: Language): string {
  return productsUrl(language, { sort: "newest", page: 1, pageSize: FEATURED_COUNT });
}

// 商品详情：slug 是路由解码后的值，在请求路径里只编码一次。
export function productDetailUrl(language: Language, slug: string): string {
  return `${CATALOG_BASE}/products/${encodeURIComponent(slug)}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 接口返回非 2xx 时的错误，带状态码（详情接口用 404 表示商品不存在或未发布）。
export class CatalogError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`catalog request failed with status ${String(status)}`);
    this.name = "CatalogError";
    this.status = status;
  }
}

// 发一个目录请求；非 2xx 或网络错误时抛错，由页面显示 common.error_retry（详情 404 时回到商品列表）。
export async function fetchCatalog<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: "GET",
    credentials: "omit",
    headers: { Accept: "application/json" },
    signal: signal ?? null,
  });
  if (!response.ok) {
    throw new CatalogError(response.status);
  }
  return (await response.json()) as T;
}

// not_found 只在接口返回 404 时出现；其他失败都是 error。
export type Remote<T> =
  | { status: "loading" }
  | { status: "error" }
  | { status: "not_found" }
  | { status: "ready"; data: T };

const LOADING: Remote<never> = { status: "loading" };

// 请求失败时的状态：接口 404 为 not_found，其余（其他状态码、网络错误）为 error。
export function failedRemote(error: unknown): Remote<never> {
  return error instanceof CatalogError && error.status === 404 ? { status: "not_found" } : { status: "error" };
}

// 按地址取一次目录数据；地址变了（如切换语言、换筛选）就重新请求，旧请求作废。
// url 为 null 时不请求（如首页隐藏的区块）。结果按地址记下，地址变了之前的结果不再显示。
export function useCatalog<T>(url: string | null): Remote<T> {
  const [settled, setSettled] = useState<{ url: string; remote: Remote<T> } | null>(null);

  useEffect(() => {
    if (url === null) {
      return undefined;
    }
    const controller = new AbortController();
    fetchCatalog<T>(url, controller.signal).then(
      (data) => {
        setSettled({ url, remote: { status: "ready", data } });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setSettled({ url, remote: failedRemote(error) });
        }
      },
    );
    return () => {
      controller.abort();
    };
  }, [url]);

  return settled !== null && settled.url === url ? settled.remote : LOADING;
}

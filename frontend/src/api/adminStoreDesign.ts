import type { Language } from "../i18n/copy";
import { parseStoreDesign } from "../storeDesign";
import type { HomeBlockSetting, ShopTheme } from "../storeDesign";
import { FEATURED_COUNT } from "./catalog";
import { CSRF_HEADER } from "./pay";

// 后台店铺装修 A08 的接口（app/api/admin_store_design.py，SHOP-TASK-051 与 SHOP-TASK-061）：读取 GET /api/admin/store-design?lang=
// 与保存 PUT /api/admin/store-design?lang=。后台会话凭服务端发的 HttpOnly cookie；读取不要求 CSRF，保存带读取返回的 CSRF 令牌。
// 唯一的查询参数是语言 lang，只决定商品名称的语言。只取 SHOP-TASK-051 记录段列出的字段，多出的字段不带进结果；响应体不合格算失败。
// 主题与主色以 storeDesign.ts 的 THEME_ACCENTS 为准，区块以 HOME_BLOCKS 为准（与前台读取公开接口用同一校验）。
// 页面只按状态码区分，不显示错误体。

export const ADMIN_STORE_DESIGN_URL = "/api/admin/store-design";

// 精选的一件：商品内部 ID、slug、按请求语言的名称（请求语言与英文都缺少时为 null）与此刻是否满足 published()。
export interface FeaturedProduct {
  product_id: number;
  slug: string;
  name: string | null;
  published: boolean;
}

// 可挑选的一件（全部满足 published() 的商品）：商品内部 ID、slug 与名称。
export interface ProductChoice {
  product_id: number;
  slug: string;
  name: string;
}

// 保存后的设置（保存的响应体）：主题、主色（null 为该主题的默认主色）、按位置排序的四个区块与按位置排序的精选（最多 4 件）。
export interface AdminStoreDesign {
  theme: ShopTheme;
  accent: string | null;
  home_blocks: HomeBlockSetting[];
  featured: FeaturedProduct[];
}

// 读取的结果：保存后的四个字段，加上可挑选的商品（按商品 ID 升序）与只用于保存请求头的 CSRF 令牌。
export interface AdminStoreDesignDetail extends AdminStoreDesign {
  choices: ProductChoice[];
  csrf_token: string;
}

// 保存的请求：四个字段都必填；数组顺序即位置。
export interface StoreDesignInput {
  theme: ShopTheme;
  accent: string | null;
  home_blocks: readonly HomeBlockSetting[];
  featured_product_ids: readonly number[];
}

// 读取的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type StoreDesignRead = { kind: "ok"; design: AdminStoreDesignDetail } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 保存的结果：保存了（200，含与当前完全相同）、没有会话（401）、CSRF 令牌不对（403）、其他失败（含 422 与 200 但响应体不合格）、
// 网络中断（含调用方中止）。
export type StoreDesignSave =
  | { kind: "ok"; design: AdminStoreDesign }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

function readFeatured(value: unknown): FeaturedProduct | null {
  if (!isRecord(value)) {
    return null;
  }
  const { product_id, slug, name, published } = value;
  if (!isPositive(product_id) || !isFilled(slug) || !(name === null || typeof name === "string") || typeof published !== "boolean") {
    return null;
  }
  return { product_id, slug, name, published };
}

function readChoice(value: unknown): ProductChoice | null {
  if (!isRecord(value)) {
    return null;
  }
  const { product_id, slug, name } = value;
  if (!isPositive(product_id) || !isFilled(slug) || typeof name !== "string") {
    return null;
  }
  return { product_id, slug, name };
}

// 逐项读取；任一项不合格或商品 ID 重复时整个数组不合格。
function readProducts<T extends { product_id: number }>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: T[] = [];
  for (const item of value as unknown[]) {
    const found = read(item);
    if (found === null || items.some((other) => other.product_id === found.product_id)) {
      return null;
    }
    items.push(found);
  }
  return items;
}

// 保存的响应体：主题、主色与区块沿用前台的校验（主题在 THEME_ACCENTS 内、主色为 null 或属于该主题、区块为 HOME_BLOCKS 的一个排列），
// 精选最多 FEATURED_COUNT 件且商品 ID 不重复。
function readDesign(value: unknown): AdminStoreDesign | null {
  if (!isRecord(value)) {
    return null;
  }
  const design = parseStoreDesign(value);
  const featured = readProducts(value.featured, readFeatured);
  if (design === null || featured === null || featured.length > FEATURED_COUNT) {
    return null;
  }
  return {
    theme: design.theme,
    accent: design.accent,
    home_blocks: design.homeBlocks.map(({ block, visible }) => ({ block, visible })),
    featured,
  };
}

// 读取的响应体：保存的那四个字段，加上商品 ID 不重复的 choices 与非空的 csrf_token。
function readDetail(value: unknown): AdminStoreDesignDetail | null {
  const design = readDesign(value);
  if (design === null) {
    return null;
  }
  const { choices, csrf_token } = value as Record<string, unknown>;
  const items = readProducts(choices, readChoice);
  if (items === null || !isFilled(csrf_token)) {
    return null;
  }
  return { ...design, choices: items, csrf_token };
}

export function adminStoreDesignUrl(language: Language): string {
  return `${ADMIN_STORE_DESIGN_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 200 的响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// GET /api/admin/store-design?lang=：同源 cookie、不缓存、没有请求体。
export async function readAdminStoreDesign(language: Language, signal?: AbortSignal): Promise<StoreDesignRead> {
  let response: Response;
  try {
    response = await fetch(adminStoreDesignUrl(language), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const design = await readBody(response, readDetail);
  return design === null ? { kind: "failed" } : { kind: "ok", design };
}

// PUT /api/admin/store-design?lang=：JSON 请求体只有 theme、accent、home_blocks 与 featured_product_ids，请求头带读取给的 CSRF 令牌；
// 同源 cookie、不缓存。
export async function saveAdminStoreDesign(
  input: StoreDesignInput,
  csrfToken: string,
  language: Language,
  signal?: AbortSignal,
): Promise<StoreDesignSave> {
  let response: Response;
  try {
    response = await fetch(adminStoreDesignUrl(language), {
      method: "PUT",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({
        theme: input.theme,
        accent: input.accent,
        home_blocks: input.home_blocks.map(({ block, visible }) => ({ block, visible })),
        featured_product_ids: [...input.featured_product_ids],
      }),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const design = await readBody(response, readDesign);
      return design === null ? { kind: "failed" } : { kind: "ok", design };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    default:
      return { kind: "failed" };
  }
}

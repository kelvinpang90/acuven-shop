import { CSRF_HEADER } from "./pay";

// 后台分类 A10 的接口（app/api/admin_categories.py，SHOP-TASK-181）：读取 GET /api/admin/categories、新建 POST /api/admin/categories 与
// 保存 PUT /api/admin/categories/{category_id}，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie；读取不要求 CSRF，新建与保存带读取
// 返回的 CSRF 令牌。只取 CategoryListOut 与 CategoryOut 列出的字段，多出的字段不带进结果；响应体不合格算失败。分类的取值规则、核对与审计
// 在服务端（DESIGN 1.18 第 14 条），这里只传递与校验字段；名称原样提交，去首尾空白交给服务端。

export const ADMIN_CATEGORIES_URL = "/api/admin/categories";

// 一个分类（与 app/api/admin_categories.py 的 CategoryOut 一致）；名称为空时为 null，visible_products 为该分类下前台可见的商品件数。
export interface AdminCategory {
  id: number;
  slug: string;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
  is_active: boolean;
  version: number;
  visible_products: number;
}

// 新建的内容（与 CategoryIn 一致）：三语名称与启用状态。
export interface CategoryCreate {
  name_en: string;
  name_zh: string;
  name_ms: string;
  is_active: boolean;
}

// 保存的内容（与 CategorySaveIn 一致）：新建的四项另加读取时的版本号。
export interface CategorySave extends CategoryCreate {
  version: number;
}

// 读取的结果：取到（200，带按接口顺序的分类与只用于写请求头的 CSRF 令牌）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type CategoriesRead = { kind: "ok"; categories: AdminCategory[]; csrf_token: string } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 新建与保存的结果：成功（新建 201、保存 200，带服务端返回的分类）、没有会话（401）、CSRF 令牌不对（403）、取值不合格（422 且 detail 为
// category_invalid）、分类已被改动（409 且 detail 为 category_changed）、其他失败（含其他 422、其他 409、413、415 与响应体不合格）、网络中断（含调用方中止）。
export type CategoryWrite =
  | { kind: "ok"; category: AdminCategory }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "invalid" }
  | { kind: "changed" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isName(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

// 一个分类：id 为正整数、slug 为非空字符串、三语名称为字符串或 null、is_active 为布尔值、version 与 visible_products 为不小于 0 的整数。
function readCategory(value: unknown): AdminCategory | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, slug, name_en, name_zh, name_ms, is_active, version, visible_products } = value;
  if (
    !isPositiveInteger(id) ||
    !isFilled(slug) ||
    !isName(name_en) ||
    !isName(name_zh) ||
    !isName(name_ms) ||
    typeof is_active !== "boolean" ||
    !isNonNegativeInteger(version) ||
    !isNonNegativeInteger(visible_products)
  ) {
    return null;
  }
  return { id, slug, name_en, name_zh, name_ms, is_active, version, visible_products };
}

// 读取的响应体：categories 为数组（按接口给的顺序，任一项不合格为 null）与非空的 csrf_token。
function readList(value: unknown): { categories: AdminCategory[]; csrf_token: string } | null {
  if (!isRecord(value) || !Array.isArray(value.categories)) {
    return null;
  }
  const { csrf_token } = value;
  if (!isFilled(csrf_token)) {
    return null;
  }
  const categories: AdminCategory[] = [];
  for (const item of value.categories) {
    const category = readCategory(item);
    if (category === null) {
      return null;
    }
    categories.push(category);
  }
  return { categories, csrf_token };
}

// 4xx 的响应体：detail 恰为给定的字符串时为 true，否则为 null。
function detailIs(detail: string): (body: unknown) => true | null {
  return (body) => (isRecord(body) && body.detail === detail ? true : null);
}

// 响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// 请求体只取规定的字段，调用方对象上多出的属性不带进请求体；名称原样提交，不去空白。
function createBody(input: CategoryCreate): CategoryCreate {
  return { name_en: input.name_en, name_zh: input.name_zh, name_ms: input.name_ms, is_active: input.is_active };
}

function saveBody(input: CategorySave): CategorySave {
  return { ...createBody(input), version: input.version };
}

// 新建与保存的写请求：JSON 请求体，请求头带读取给的 CSRF 令牌；同源 cookie、不缓存。成功状态（新建 201、保存 200）带回一个合格的分类，其余按状态码与 detail 归类。
async function writeCategory(url: string, method: "POST" | "PUT", okStatus: number, body: unknown, csrfToken: string, signal?: AbortSignal): Promise<CategoryWrite> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  if (response.status === okStatus) {
    const category = await readBody(response, readCategory);
    return category === null ? { kind: "failed" } : { kind: "ok", category };
  }
  switch (response.status) {
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 422:
      return (await readBody(response, detailIs("category_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    case 409:
      return (await readBody(response, detailIs("category_changed"))) === null ? { kind: "failed" } : { kind: "changed" };
    default:
      return { kind: "failed" };
  }
}

// GET /api/admin/categories：同源 cookie、不缓存、没有请求体。
export async function readAdminCategories(signal?: AbortSignal): Promise<CategoriesRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_CATEGORIES_URL, {
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
  const list = await readBody(response, readList);
  return list === null ? { kind: "failed" } : { kind: "ok", ...list };
}

// POST /api/admin/categories：JSON 请求体只有 name_en、name_zh、name_ms 与 is_active；成功为 201。
export function createAdminCategory(input: CategoryCreate, csrfToken: string, signal?: AbortSignal): Promise<CategoryWrite> {
  return writeCategory(ADMIN_CATEGORIES_URL, "POST", 201, createBody(input), csrfToken, signal);
}

// PUT /api/admin/categories/{category_id}：路径段为分类 ID 的十进制，JSON 请求体为新建的四项另加 version；成功或未变都为 200。
export function saveAdminCategory(categoryId: number, input: CategorySave, csrfToken: string, signal?: AbortSignal): Promise<CategoryWrite> {
  return writeCategory(`${ADMIN_CATEGORIES_URL}/${String(categoryId)}`, "PUT", 200, saveBody(input), csrfToken, signal);
}

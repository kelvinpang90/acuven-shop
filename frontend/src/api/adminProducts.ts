import { CSRF_HEADER } from "./pay";

// 后台商品 A04 的接口（app/api/admin_products.py，SHOP-TASK-186）：列表 GET /api/admin/products、单件 GET /api/admin/products/{product_id}、
// 新建 POST /api/admin/products 与保存 PUT /api/admin/products/{product_id}，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie；两个读取
// 不要求 CSRF，新建与保存带列表返回的 CSRF 令牌。只取 ProductListOut 与 ProductOut 列出的字段，多出的字段不带进结果；响应体不合格算失败。
// 商品的取值规则、上架条件、并发核对与审计在服务端（DESIGN 1.18 第 11 条，SHOP-TASK-183 至 185），这里只传递与校验字段；价格以整仙收发、
// 不换算，文案原样提交。HANDOFF 0.82 第（1）点：规格值与 SKU 分「已有（带 id）」与「新增（带 temp_id）」两种形状，序列化时只写出该形状的字段。

export const ADMIN_PRODUCTS_URL = "/api/admin/products";

// 列表的一项（与 ProductListItemOut 一致）；名称为空时为 null。
export interface AdminProductListItem {
  id: number;
  slug: string;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
  is_active: boolean;
  category_id: number;
  category_name_en: string | null;
  category_name_zh: string | null;
  category_name_ms: string | null;
}

// 规格值（与 ValueOut 一致）。
export interface AdminOptionValue {
  id: number;
  code: string;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
}

// 规格名（与 OptionOut 一致），values 按接口给的顺序。
export interface AdminOption {
  id: number;
  code: string;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
  values: AdminOptionValue[];
}

// SKU（与 VariantOut 一致）：价格为整仙；option_value_ids 按规格名的顺序，第 i 个是第 i 个规格名下的值。
export interface AdminVariant {
  id: number;
  sku: string;
  price_sen: number;
  daily_initial_stock: number;
  available_stock: number;
  is_active: boolean;
  option_value_ids: number[];
}

// 图片（与 ImageOut 一致）。
export interface AdminProductImage {
  id: number;
  storage_ref: string;
}

// 一件商品（与 ProductOut 一致）：单件读取、新建 201 与保存 200 共用。
export interface AdminProduct {
  id: number;
  slug: string;
  version: number;
  category_id: number;
  name_en: string | null;
  name_zh: string | null;
  name_ms: string | null;
  description_en: string | null;
  description_zh: string | null;
  description_ms: string | null;
  is_active: boolean;
  max_per_order: number;
  options: AdminOption[];
  variants: AdminVariant[];
  images: AdminProductImage[];
}

// 请求体里的三语名称（字符串，可为空串，原样提交）。
export interface Names {
  name_en: string;
  name_zh: string;
  name_ms: string;
}

// 规格值的两种形状：已有的只有 id 与三语名称，新增的只有 temp_id 与三语名称。
export interface ExistingValue extends Names {
  id: number;
}

export interface NewValue extends Names {
  temp_id: string;
}

// 新增 SKU 所选的一个规格值：已有的只有 id，同一次新增的只有 temp_id。
export interface ExistingValueRef {
  id: number;
}

export interface NewValueRef {
  temp_id: string;
}

export type ValueRef = ExistingValueRef | NewValueRef;

// 新建商品的规格名：没有 id，值全是新增的。
export interface NewOption extends Names {
  values: NewValue[];
}

// 保存时的规格名：带 id，值为已有或新增。
export interface SavedOption extends Names {
  id: number;
  values: (ExistingValue | NewValue)[];
}

// SKU 的两种形状：已有的只有 id、price_sen、daily_initial_stock 与 is_active（不带 option_values）；新增的只有 temp_id、option_values 与这三项。
export interface ExistingVariant {
  id: number;
  price_sen: number;
  daily_initial_stock: number;
  is_active: boolean;
}

export interface NewVariant {
  temp_id: string;
  option_values: ValueRef[];
  price_sen: number;
  daily_initial_stock: number;
  is_active: boolean;
}

// 新建商品的 SKU：新增形状，所选的值都是同一次新增的（只有 temp_id）。
export interface CreateVariant {
  temp_id: string;
  option_values: NewValueRef[];
  price_sen: number;
  daily_initial_stock: number;
  is_active: boolean;
}

// 新建与保存共有的商品字段（与 ProductIn 去掉 options、variants 一致）；价格与金额不在这里换算。
export interface ProductFields {
  category_id: number;
  name_en: string;
  name_zh: string;
  name_ms: string;
  description_en: string;
  description_zh: string;
  description_ms: string;
  is_active: boolean;
  max_per_order: number;
}

// 新建的内容（与 ProductIn 一致）。
export interface ProductCreate extends ProductFields {
  options: NewOption[];
  variants: CreateVariant[];
}

// 保存的内容（与 ProductSaveIn 一致）：另有读取时的版本号，规格名带 id，规格值与 SKU 为已有或新增。
export interface ProductSave extends ProductFields {
  version: number;
  options: SavedOption[];
  variants: (ExistingVariant | NewVariant)[];
}

// 列表的结果：取到（200，带按接口顺序的商品与只用于写请求头的 CSRF 令牌）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type ProductsRead = { kind: "ok"; products: AdminProductListItem[]; csrf_token: string } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 单件读取的结果：取到（200，带一件合格的商品）、没有会话（401）、其他失败（含 404 product_not_found 与 200 但响应体不合格）、网络中断（含调用方中止）。
export type ProductRead = { kind: "ok"; product: AdminProduct } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 新建与保存的结果：成功（新建 201、保存 200，带服务端返回的商品）、没有会话（401）、CSRF 令牌不对（403）、取值不合格（422 且 detail 为
// product_invalid）、商品已被改动（409 且 detail 为 product_changed）、其他失败（含其他 422、其他 409、413、415 与响应体不合格）、网络中断（含调用方中止）。
export type ProductWrite =
  | { kind: "ok"; product: AdminProduct }
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

function isText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

// 数组的每一项按 read 读取；不是数组或任一项不合格为 null。
function readRows<T>(value: unknown, read: (row: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const rows: T[] = [];
  for (const item of value) {
    const row = read(item);
    if (row === null) {
      return null;
    }
    rows.push(row);
  }
  return rows;
}

// 列表的一项：id 与 category_id 为正整数、slug 为非空字符串、商品与分类的三语名称为字符串或 null、is_active 为布尔值。
function readListItem(value: unknown): AdminProductListItem | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, slug, name_en, name_zh, name_ms, is_active, category_id, category_name_en, category_name_zh, category_name_ms } = value;
  if (
    !isPositiveInteger(id) ||
    !isFilled(slug) ||
    !isText(name_en) ||
    !isText(name_zh) ||
    !isText(name_ms) ||
    typeof is_active !== "boolean" ||
    !isPositiveInteger(category_id) ||
    !isText(category_name_en) ||
    !isText(category_name_zh) ||
    !isText(category_name_ms)
  ) {
    return null;
  }
  return { id, slug, name_en, name_zh, name_ms, is_active, category_id, category_name_en, category_name_zh, category_name_ms };
}

// 列表的响应体：products 为数组（按接口给的顺序，任一项不合格为 null）与非空的 csrf_token。
function readList(value: unknown): { products: AdminProductListItem[]; csrf_token: string } | null {
  if (!isRecord(value)) {
    return null;
  }
  const products = readRows(value.products, readListItem);
  const { csrf_token } = value;
  if (products === null || !isFilled(csrf_token)) {
    return null;
  }
  return { products, csrf_token };
}

// 规格值：id 为正整数、code 为非空字符串、三语名称为字符串或 null。
function readValue(value: unknown): AdminOptionValue | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, code, name_en, name_zh, name_ms } = value;
  if (!isPositiveInteger(id) || !isFilled(code) || !isText(name_en) || !isText(name_zh) || !isText(name_ms)) {
    return null;
  }
  return { id, code, name_en, name_zh, name_ms };
}

// 规格名：同规格值，另有 values 数组。
function readOption(value: unknown): AdminOption | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, code, name_en, name_zh, name_ms } = value;
  const values = readRows(value.values, readValue);
  if (!isPositiveInteger(id) || !isFilled(code) || !isText(name_en) || !isText(name_zh) || !isText(name_ms) || values === null) {
    return null;
  }
  return { id, code, name_en, name_zh, name_ms, values };
}

// SKU：id 为正整数、sku 为非空字符串、price_sen、daily_initial_stock 与 available_stock 为不小于 0 的整数、is_active 为布尔值、
// option_value_ids 为正整数数组（与规格名的对应在 readProduct 里查）。
function readVariant(value: unknown): AdminVariant | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, sku, price_sen, daily_initial_stock, available_stock, is_active } = value;
  const option_value_ids = readRows(value.option_value_ids, (item) => (isPositiveInteger(item) ? item : null));
  if (
    !isPositiveInteger(id) ||
    !isFilled(sku) ||
    !isNonNegativeInteger(price_sen) ||
    !isNonNegativeInteger(daily_initial_stock) ||
    !isNonNegativeInteger(available_stock) ||
    typeof is_active !== "boolean" ||
    option_value_ids === null
  ) {
    return null;
  }
  return { id, sku, price_sen, daily_initial_stock, available_stock, is_active, option_value_ids };
}

// 图片：id 为正整数、storage_ref 为非空字符串。
function readImage(value: unknown): AdminProductImage | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, storage_ref } = value;
  if (!isPositiveInteger(id) || !isFilled(storage_ref)) {
    return null;
  }
  return { id, storage_ref };
}

// SKU 的规格值与规格名对应：个数等于规格名个数，第 i 个是第 i 个规格名下某个值的 id。
function matchesOptions(variant: AdminVariant, options: AdminOption[]): boolean {
  return variant.option_value_ids.length === options.length && variant.option_value_ids.every((valueId, index) => options[index]?.values.some((value) => value.id === valueId) === true);
}

// 一件商品：id、category_id 与 version 为正整数、slug 为非空字符串、三语名称与描述为字符串或 null、is_active 为布尔值、max_per_order 为整数，
// options、variants 与 images 各为合格的数组，每个 SKU 的规格值与规格名对得上。
function readProduct(value: unknown): AdminProduct | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, slug, version, category_id, name_en, name_zh, name_ms, description_en, description_zh, description_ms, is_active, max_per_order } = value;
  const options = readRows(value.options, readOption);
  const variants = readRows(value.variants, readVariant);
  const images = readRows(value.images, readImage);
  if (
    !isPositiveInteger(id) ||
    !isFilled(slug) ||
    !isPositiveInteger(version) ||
    !isPositiveInteger(category_id) ||
    !isText(name_en) ||
    !isText(name_zh) ||
    !isText(name_ms) ||
    !isText(description_en) ||
    !isText(description_zh) ||
    !isText(description_ms) ||
    typeof is_active !== "boolean" ||
    typeof max_per_order !== "number" ||
    !Number.isInteger(max_per_order) ||
    options === null ||
    variants === null ||
    images === null
  ) {
    return null;
  }
  if (!variants.every((variant) => matchesOptions(variant, options))) {
    return null;
  }
  return {
    id,
    slug,
    version,
    category_id,
    name_en,
    name_zh,
    name_ms,
    description_en,
    description_zh,
    description_ms,
    is_active,
    max_per_order,
    options,
    variants,
    images,
  };
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

// 请求体按形状只取规定的字段，调用方对象上多出的属性不带进请求体；有 id 的为已有形状，否则为新增形状。文案原样提交，不去空白；价格原样（整仙）。
function namesBody(row: Names): Names {
  return { name_en: row.name_en, name_zh: row.name_zh, name_ms: row.name_ms };
}

function newValueBody(row: NewValue): NewValue {
  return { temp_id: row.temp_id, ...namesBody(row) };
}

function valueBody(row: ExistingValue | NewValue): ExistingValue | NewValue {
  return "id" in row ? { id: row.id, ...namesBody(row) } : newValueBody(row);
}

function newRefBody(ref: NewValueRef): NewValueRef {
  return { temp_id: ref.temp_id };
}

function refBody(ref: ValueRef): ValueRef {
  return "id" in ref ? { id: ref.id } : newRefBody(ref);
}

function newOptionBody(row: NewOption): NewOption {
  return { ...namesBody(row), values: row.values.map(newValueBody) };
}

function savedOptionBody(row: SavedOption): SavedOption {
  return { id: row.id, ...namesBody(row), values: row.values.map(valueBody) };
}

function createVariantBody(row: CreateVariant): CreateVariant {
  return {
    temp_id: row.temp_id,
    option_values: row.option_values.map(newRefBody),
    price_sen: row.price_sen,
    daily_initial_stock: row.daily_initial_stock,
    is_active: row.is_active,
  };
}

function variantBody(row: ExistingVariant | NewVariant): ExistingVariant | NewVariant {
  if ("id" in row) {
    return { id: row.id, price_sen: row.price_sen, daily_initial_stock: row.daily_initial_stock, is_active: row.is_active };
  }
  return {
    temp_id: row.temp_id,
    option_values: row.option_values.map(refBody),
    price_sen: row.price_sen,
    daily_initial_stock: row.daily_initial_stock,
    is_active: row.is_active,
  };
}

function fieldsBody(input: ProductFields): ProductFields {
  return {
    category_id: input.category_id,
    name_en: input.name_en,
    name_zh: input.name_zh,
    name_ms: input.name_ms,
    description_en: input.description_en,
    description_zh: input.description_zh,
    description_ms: input.description_ms,
    is_active: input.is_active,
    max_per_order: input.max_per_order,
  };
}

function createBody(input: ProductCreate): ProductCreate {
  return { ...fieldsBody(input), options: input.options.map(newOptionBody), variants: input.variants.map(createVariantBody) };
}

function saveBody(input: ProductSave): ProductSave {
  return { ...fieldsBody(input), options: input.options.map(savedOptionBody), variants: input.variants.map(variantBody), version: input.version };
}

function productUrl(productId: number): string {
  return `${ADMIN_PRODUCTS_URL}/${String(productId)}`;
}

// 两个读取：同源 cookie、不缓存、没有请求体与 CSRF 头；200 按 read 读取响应体，401 为 none，其他状态为 failed。
async function readJson<T>(url: string, read: (body: unknown) => T | null, signal?: AbortSignal): Promise<{ kind: "ok"; value: T } | { kind: "none" } | { kind: "failed" } | { kind: "network" }> {
  let response: Response;
  try {
    response = await fetch(url, {
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
  const value = await readBody(response, read);
  return value === null ? { kind: "failed" } : { kind: "ok", value };
}

// 新建与保存的写请求：JSON 请求体，请求头带列表给的 CSRF 令牌；同源 cookie、不缓存。成功状态（新建 201、保存 200）带回一件合格的商品，其余按状态码与 detail 归类。
async function writeProduct(url: string, method: "POST" | "PUT", okStatus: number, body: unknown, csrfToken: string, signal?: AbortSignal): Promise<ProductWrite> {
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
    const product = await readBody(response, readProduct);
    return product === null ? { kind: "failed" } : { kind: "ok", product };
  }
  switch (response.status) {
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 422:
      return (await readBody(response, detailIs("product_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    case 409:
      return (await readBody(response, detailIs("product_changed"))) === null ? { kind: "failed" } : { kind: "changed" };
    default:
      return { kind: "failed" };
  }
}

// GET /api/admin/products：全部商品（按接口给的顺序）与 CSRF 令牌。
export async function readAdminProducts(signal?: AbortSignal): Promise<ProductsRead> {
  const result = await readJson(ADMIN_PRODUCTS_URL, readList, signal);
  return result.kind === "ok" ? { kind: "ok", ...result.value } : result;
}

// GET /api/admin/products/{product_id}：路径段为商品 ID 的十进制；404 product_not_found 归 failed。
export async function readAdminProduct(productId: number, signal?: AbortSignal): Promise<ProductRead> {
  const result = await readJson(productUrl(productId), readProduct, signal);
  return result.kind === "ok" ? { kind: "ok", product: result.value } : result;
}

// POST /api/admin/products：JSON 请求体为商品字段、options 与 variants（只有新增形状）；成功为 201。
export function createAdminProduct(input: ProductCreate, csrfToken: string, signal?: AbortSignal): Promise<ProductWrite> {
  return writeProduct(ADMIN_PRODUCTS_URL, "POST", 201, createBody(input), csrfToken, signal);
}

// PUT /api/admin/products/{product_id}：路径段为商品 ID 的十进制，JSON 请求体为新建的各项另加 version，规格值与 SKU 按形状写出；成功或未变都为 200。
export function saveAdminProduct(productId: number, input: ProductSave, csrfToken: string, signal?: AbortSignal): Promise<ProductWrite> {
  return writeProduct(productUrl(productId), "PUT", 200, saveBody(input), csrfToken, signal);
}

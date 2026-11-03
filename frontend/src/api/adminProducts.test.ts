import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_PRODUCTS_URL, createAdminProduct, readAdminProduct, readAdminProducts, saveAdminProduct } from "./adminProducts";
import type { AdminOption, AdminProduct, AdminVariant, ProductCreate, ProductSave } from "./adminProducts";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 列表的商品（SHOP-TASK-186 记录的字段：id、slug、name_en、name_zh、name_ms、is_active、category_id、category_name_en、category_name_zh、category_name_ms）。
const TEE_ITEM = {
  id: 7,
  slug: "crew-neck-tee",
  name_en: "Crew Neck Tee",
  name_zh: "圆领T恤",
  name_ms: null,
  is_active: true,
  category_id: 2,
  category_name_en: "Apparel",
  category_name_zh: null,
  category_name_ms: "Pakaian",
};
const CABLE_ITEM = {
  id: 3,
  slug: "usb-cable",
  name_en: null,
  name_zh: null,
  name_ms: null,
  is_active: false,
  category_id: 5,
  category_name_en: null,
  category_name_zh: null,
  category_name_ms: null,
};

const LIST = { products: [TEE_ITEM, CABLE_ITEM], csrf_token: "csrf-abc" };

// 一件商品（app/api/admin_products.py 的 ProductOut）：两个规格名（Colour、Size）、两个 SKU 与一张图片。
const BLACK = { id: 21, code: "black", name_en: "Black", name_zh: "黑", name_ms: null };
const WHITE = { id: 22, code: "white", name_en: "White", name_zh: null, name_ms: "Putih" };
const MEDIUM = { id: 23, code: "m", name_en: "M", name_zh: null, name_ms: null };
const LARGE = { id: 24, code: "l", name_en: "L", name_zh: null, name_ms: null };
const COLOUR: AdminOption = { id: 11, code: "color", name_en: "Colour", name_zh: "颜色", name_ms: null, values: [BLACK, WHITE] };
const SIZE: AdminOption = { id: 12, code: "size", name_en: "Size", name_zh: null, name_ms: null, values: [MEDIUM, LARGE] };
const BLACK_M: AdminVariant = { id: 31, sku: "crew-neck-tee-black-m", price_sen: 2500, daily_initial_stock: 20, available_stock: 18, is_active: true, option_value_ids: [21, 23] };
const WHITE_L: AdminVariant = { id: 32, sku: "crew-neck-tee-white-l", price_sen: 0, daily_initial_stock: 0, available_stock: 0, is_active: false, option_value_ids: [22, 24] };
const IMAGE = { id: 41, storage_ref: "products/7/a.webp" };
const PRODUCT: AdminProduct = {
  id: 7,
  slug: "crew-neck-tee",
  version: 3,
  category_id: 2,
  name_en: "Crew Neck Tee",
  name_zh: "圆领T恤",
  name_ms: null,
  description_en: "Cotton",
  description_zh: null,
  description_ms: "",
  is_active: true,
  max_per_order: 10,
  options: [COLOUR, SIZE],
  variants: [BLACK_M, WHITE_L],
  images: [IMAGE],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不在本接口下时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    const url = String(input);
    if (next === undefined || (url !== ADMIN_PRODUCTS_URL && !url.startsWith(`${ADMIN_PRODUCTS_URL}/`))) {
      return Promise.reject(new Error("unexpected request"));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

function sentUrl(fetchMock: FetchMock, index: number): string {
  return String(fetchMock.mock.calls[index]?.[0]);
}

function sentInit(fetchMock: FetchMock, index: number): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

function sentText(fetchMock: FetchMock, index: number): string {
  const body = sentInit(fetchMock, index).body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return body;
}

function sentBody(fetchMock: FetchMock, index: number): unknown {
  return JSON.parse(sentText(fetchMock, index)) as unknown;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 在合格的商品上换掉一处（值为 undefined 时 JSON 里没有这一项）。
function productWith(change: Record<string, unknown>): Record<string, unknown> {
  return { ...PRODUCT, ...change };
}

function optionsWith(colour: Record<string, unknown>): Record<string, unknown> {
  return productWith({ options: [{ ...COLOUR, ...colour }, SIZE] });
}

function colourValueWith(change: Record<string, unknown>): Record<string, unknown> {
  return optionsWith({ values: [{ ...BLACK, ...change }, WHITE] });
}

function variantWith(change: Record<string, unknown>): Record<string, unknown> {
  return productWith({ variants: [BLACK_M, { ...WHITE_L, ...change }] });
}

function imageWith(change: Record<string, unknown>): Record<string, unknown> {
  return productWith({ images: [{ ...IMAGE, ...change }] });
}

// 单件、新建与保存共用的不合格商品。
const MALFORMED_PRODUCTS: [string, Record<string, unknown>][] = [
  // SHOP-TASK-186 记录「字段同服务层的 `AdminProduct`（含 `options[].values[]`、`variants[]` 的 `option_value_ids` 与 `images[]`）」与 DESIGN 1.18
  // 第 11 条「读取一件商品时带它的版本号与全部规格名、规格值、SKU 的行 ID」：商品的 id、category_id 与 version 为正整数，缺少、0、负数、小数、字符串都不合格。
  ["id missing", productWith({ id: undefined })],
  ["id zero", productWith({ id: 0 })],
  ["id negative", productWith({ id: -7 })],
  ["id fractional", productWith({ id: 7.5 })],
  ["id as string", productWith({ id: "7" })],
  ["version missing", productWith({ version: undefined })],
  ["version zero", productWith({ version: 0 })],
  ["version fractional", productWith({ version: 1.5 })],
  ["version as string", productWith({ version: "3" })],
  ["category_id missing", productWith({ category_id: undefined })],
  ["category_id zero", productWith({ category_id: 0 })],
  ["category_id null", productWith({ category_id: null })],
  // DESIGN 1.18 第 11 条「商品的 slug（商品页地址的一段）……由服务端在新建时生成……结果为空时以 product……代替」：slug 不会为空，缺少、空串、null、数字都不合格。
  ["slug missing", productWith({ slug: undefined })],
  ["slug empty", productWith({ slug: "" })],
  ["slug null", productWith({ slug: null })],
  ["slug as number", productWith({ slug: 7 })],
  // SHOP-TASK-186 记录「空文案为 null」：三语名称与描述为字符串或 null，缺少、数字、布尔值、数组都不合格。
  ["name_en missing", productWith({ name_en: undefined })],
  ["name_zh as number", productWith({ name_zh: 1 })],
  ["name_ms as boolean", productWith({ name_ms: false })],
  ["description_en missing", productWith({ description_en: undefined })],
  ["description_zh as array", productWith({ description_zh: ["棉"] })],
  ["description_ms as number", productWith({ description_ms: 0 })],
  // UX 0.17 A04「状态显示 `[admin.active]` 或 `[admin.unpublished]`」：上架状态为布尔值，缺少、字符串、数字、null 都不合格。
  ["is_active missing", productWith({ is_active: undefined })],
  ["is_active as string", productWith({ is_active: "true" })],
  ["is_active as number", productWith({ is_active: 1 })],
  ["is_active null", productWith({ is_active: null })],
  // DESIGN 1.18 第 11 条「每单限购 1–1000 的整数」：限购为整数，缺少、小数、字符串、null 都不合格。
  ["max_per_order missing", productWith({ max_per_order: undefined })],
  ["max_per_order fractional", productWith({ max_per_order: 10.5 })],
  ["max_per_order as string", productWith({ max_per_order: "10" })],
  ["max_per_order null", productWith({ max_per_order: null })],
  // SHOP-TASK-186 记录「含 `options[].values[]`、`variants[]` 的 `option_value_ids` 与 `images[]`」：三者都是数组，缺少、null、对象都不合格，任一项为 null 也不合格。
  ["options missing", productWith({ options: undefined })],
  ["options null", productWith({ options: null })],
  ["options as object", productWith({ options: COLOUR })],
  ["option null", productWith({ options: [COLOUR, null] })],
  ["variants missing", productWith({ variants: undefined })],
  ["variants as object", productWith({ variants: BLACK_M })],
  ["variant null", productWith({ variants: [BLACK_M, null] })],
  ["images missing", productWith({ images: undefined })],
  ["images null", productWith({ images: null })],
  ["image null", productWith({ images: [null] })],
  // DESIGN 1.18 第 11 条「读取一件商品时带……全部规格名、规格值……的行 ID」与「规格名与规格值的代码……由服务端在新建时生成……结果为空时以 option、value 代替」：
  // 规格名与规格值的 id 为正整数、code 为非空字符串、三语名称为字符串或 null，规格名的 values 为数组。
  ["option id zero", optionsWith({ id: 0 })],
  ["option id as string", optionsWith({ id: "11" })],
  ["option code empty", optionsWith({ code: "" })],
  ["option code missing", optionsWith({ code: undefined })],
  ["option name_en as number", optionsWith({ name_en: 1 })],
  ["option name_ms missing", optionsWith({ name_ms: undefined })],
  ["option values missing", optionsWith({ values: undefined })],
  ["option values as object", optionsWith({ values: BLACK })],
  ["value id zero", colourValueWith({ id: 0 })],
  ["value id negative", colourValueWith({ id: -21 })],
  ["value id fractional", colourValueWith({ id: 21.5 })],
  ["value code missing", colourValueWith({ code: undefined })],
  ["value code null", colourValueWith({ code: null })],
  ["value name_zh as number", colourValueWith({ name_zh: 1 })],
  ["value name_en missing", colourValueWith({ name_en: undefined })],
  // DESIGN 1.18 第 11 条「价格以整仙提交，0 到 50,000,000 仙」「每日初始库存 0–9,999 的整数」与 SHOP-TASK-186 记录的 `VariantOut`：SKU 的 id 为正整数、sku 为非空字符串、
  // 价格、每日初始库存与当日可用库存为不小于 0 的整数（价格为整仙，小数与字符串都不合格，不在这里换算），启用状态为布尔值。
  ["variant id zero", variantWith({ id: 0 })],
  ["variant id as string", variantWith({ id: "32" })],
  ["sku empty", variantWith({ sku: "" })],
  ["sku missing", variantWith({ sku: undefined })],
  ["price_sen negative", variantWith({ price_sen: -1 })],
  ["price_sen fractional", variantWith({ price_sen: 25.5 })],
  ["price_sen as string", variantWith({ price_sen: "2500" })],
  ["price_sen missing", variantWith({ price_sen: undefined })],
  ["daily_initial_stock negative", variantWith({ daily_initial_stock: -1 })],
  ["daily_initial_stock fractional", variantWith({ daily_initial_stock: 0.5 })],
  ["available_stock negative", variantWith({ available_stock: -1 })],
  ["available_stock missing", variantWith({ available_stock: undefined })],
  ["available_stock null", variantWith({ available_stock: null })],
  ["variant is_active as string", variantWith({ is_active: "false" })],
  ["variant is_active missing", variantWith({ is_active: undefined })],
  // app/api/admin_products.py 的 VariantOut「`option_value_ids`……按规格名的顺序」与 DESIGN 1.18 第 11 条「每个 SKU 在每个规格名下恰有一个值」：option_value_ids
  // 为正整数数组，个数等于规格名个数，第 i 个是第 i 个规格名下的值；缺少、含 0 或字符串、少一个、多一个、顺序对调（引用别的规格名的值）、未知的值都不合格。
  ["option_value_ids missing", variantWith({ option_value_ids: undefined })],
  ["option_value_ids null", variantWith({ option_value_ids: null })],
  ["option_value_ids with zero", variantWith({ option_value_ids: [0, 24] })],
  ["option_value_ids with string", variantWith({ option_value_ids: ["22", 24] })],
  ["option_value_ids too few", variantWith({ option_value_ids: [22] })],
  ["option_value_ids too many", variantWith({ option_value_ids: [22, 24, 24] })],
  ["option_value_ids swapped", variantWith({ option_value_ids: [24, 22] })],
  ["option_value_ids from the other option", variantWith({ option_value_ids: [22, 21] })],
  ["option_value_ids unknown", variantWith({ option_value_ids: [22, 99] })],
  ["option_value_ids on a product without options", productWith({ options: [], variants: [{ ...BLACK_M, option_value_ids: [21] }] })],
  // UX 0.17 A04「成功后图片区改显示服务端返回的图片列表」与 SHOP-TASK-186 记录的 `images[]`：图片的 id 为正整数、storage_ref 为非空字符串。
  ["image id zero", imageWith({ id: 0 })],
  ["image id as string", imageWith({ id: "41" })],
  ["storage_ref empty", imageWith({ storage_ref: "" })],
  ["storage_ref missing", imageWith({ storage_ref: undefined })],
];

describe("reading the product list", () => {
  // SHOP-TASK-186 记录「`GET /api/admin/products`：`require_admin`（401 `admin_session_required`）→ `products`……与由 cookie 算出的 `csrf_token`。不写审计」：
  // 地址为固定路径、不带查询参数、GET、凭同源 cookie、不缓存、Accept 为 JSON、没有请求体与 CSRF 头，并带上调用方的 signal。
  it("gets the list with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await readAdminProducts(controller.signal);
    expect(ADMIN_PRODUCTS_URL).toBe("/api/admin/products");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/products");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // UX 0.17 A04「全部商品（含未上架的），按新建时间从新到旧」：商品按接口给的顺序带进结果（不在这里重排），未上架的与名称全为 null 的照样带进；CSRF 令牌带进结果。
  it("keeps the products in the order given", async () => {
    stubFetch(json(200, { products: [CABLE_ITEM, TEE_ITEM], csrf_token: "csrf-abc" }));
    await expect(readAdminProducts()).resolves.toEqual({ kind: "ok", products: [CABLE_ITEM, TEE_ITEM], csrf_token: "csrf-abc" });
  });

  // SHOP-TASK-186 记录「字段依次为 `id`、`slug`、`name_en`、`name_zh`、`name_ms`、`is_active`、`category_id`、`category_name_en`、`category_name_zh`、
  // `category_name_ms`」：顶层与每项上多出的字段不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(json(200, { ...LIST, total: 2, products: [{ ...TEE_ITEM, version: 3, price_sen: 2500, created_at: "2026-11-03" }, CABLE_ITEM] }));
    const result = await readAdminProducts();
    expect(result).toStrictEqual({ kind: "ok", ...LIST });
  });

  // UX 0.17 A04「全部商品……不分页」没有下限：一件商品都没有时照样合格。
  it("accepts an empty list", async () => {
    stubFetch(json(200, { products: [], csrf_token: "t" }));
    await expect(readAdminProducts()).resolves.toEqual({ kind: "ok", products: [], csrf_token: "t" });
  });

  // SHOP-TASK-186 记录「字段依次为 `id`、`slug`……`category_name_ms`」与「空文案为 null」、DESIGN 1.18 第 11 条「商品的 slug……结果为空时以 product 代替」：
  // id 与 category_id 为正整数、slug 为非空字符串、六个名称为字符串或 null、is_active 为布尔值；任一项不合格，整个读取归为 failed。
  it.each<[string, Record<string, unknown>]>([
    ["id missing", { id: undefined }],
    ["id zero", { id: 0 }],
    ["id negative", { id: -3 }],
    ["id fractional", { id: 3.5 }],
    ["id as string", { id: "3" }],
    ["slug missing", { slug: undefined }],
    ["slug empty", { slug: "" }],
    ["slug null", { slug: null }],
    ["name_en missing", { name_en: undefined }],
    ["name_zh as number", { name_zh: 1 }],
    ["name_ms as boolean", { name_ms: true }],
    ["is_active missing", { is_active: undefined }],
    ["is_active as string", { is_active: "false" }],
    ["is_active null", { is_active: null }],
    ["category_id missing", { category_id: undefined }],
    ["category_id zero", { category_id: 0 }],
    ["category_id negative", { category_id: -5 }],
    ["category_id fractional", { category_id: 5.5 }],
    ["category_id null", { category_id: null }],
    ["category_name_en missing", { category_name_en: undefined }],
    ["category_name_zh as number", { category_name_zh: 0 }],
    ["category_name_ms as array", { category_name_ms: ["Kabel"] }],
  ])("treats a malformed product as a failure: %s", async (_name, change) => {
    stubFetch(json(200, { ...LIST, products: [TEE_ITEM, { ...CABLE_ITEM, ...change }] }));
    await expect(readAdminProducts()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「→ `products`（`load_admin_products` 的全部项……）与由 cookie 算出的 `csrf_token`」：products 缺少、不是数组、为 null 或含 null，
  // csrf_token 缺少、为空、为数字或 null，整体不是对象或不是 JSON 都不合格。
  it.each<[string, unknown]>([
    ["products missing", { csrf_token: "csrf-abc" }],
    ["products not an array", { products: TEE_ITEM, csrf_token: "csrf-abc" }],
    ["products null", { products: null, csrf_token: "csrf-abc" }],
    ["product null", { products: [TEE_ITEM, null], csrf_token: "csrf-abc" }],
    ["csrf token missing", { products: [TEE_ITEM] }],
    ["csrf token empty", { ...LIST, csrf_token: "" }],
    ["csrf token as number", { ...LIST, csrf_token: 1 }],
    ["csrf token null", { ...LIST, csrf_token: null }],
    ["not an object", [LIST]],
    ["null body", null],
  ])("treats a malformed list as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminProducts()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「`products`……与由 cookie 算出的 `csrf_token`」：200 的响应体须为这样一个 JSON 对象，不是 JSON 时算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readAdminProducts()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「`require_admin`（401 `admin_session_required`）」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(500, {}), "failed"],
    [json(201, LIST), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminProducts()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-186 记录、DESIGN 1.18 第 11 条与 UX 0.17 A04 都没有关于中止的原句（记录的偏离一节已列明）；最接近的是 UX 0.17 A04「没有未保存的修改时直接切换」
  // （HANDOFF 0.82 第（2）点「返回会中止未完成的读取」），守住的是本任务验收「网络中断与调用方中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, LIST));
    controller.abort();
    await expect(readAdminProducts(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("reading one product", () => {
  // SHOP-TASK-186 记录「`GET /api/admin/products/{product_id}`：`require_admin`（401）→ 路径 ID（……不带符号与前导零、至多 10 位、不超过 2³¹−1……）」：
  // 地址为固定路径加商品 ID 的十进制、GET、凭同源 cookie、不缓存、Accept 为 JSON、没有请求体与 CSRF 头，并带上调用方的 signal。
  it("gets the product address with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, PRODUCT), json(200, PRODUCT));
    const controller = new AbortController();
    await readAdminProduct(7, controller.signal);
    await readAdminProduct(2_147_483_647);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/products/7");
    expect(sentUrl(fetchMock, 1)).toBe("/api/admin/products/2147483647");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-186 记录「字段同服务层的 `AdminProduct`（含 `options[].values[]`、`variants[]` 的 `option_value_ids` 与 `images[]`），空文案为 null」与
  // DESIGN 1.18 第 11 条「读取一件商品时带它的版本号与全部规格名、规格值、SKU 的行 ID」：结果带整件商品，价格为整仙原样（不换算），各数组按接口给的顺序。
  it("returns the product as given", async () => {
    stubFetch(json(200, PRODUCT));
    await expect(readAdminProduct(7)).resolves.toStrictEqual({ kind: "ok", product: PRODUCT });
  });

  // DESIGN 1.18 第 11 条「每件商品 0–3 个规格名……没有规格名的商品恰有一个 SKU」：没有规格名时 SKU 的 option_value_ids 为空数组，合格；没有图片也合格。
  it("accepts a product without options or images", async () => {
    const plain = { ...PRODUCT, options: [], variants: [{ ...BLACK_M, sku: "crew-neck-tee-std", option_value_ids: [] }], images: [] };
    stubFetch(json(200, plain));
    await expect(readAdminProduct(7)).resolves.toStrictEqual({ kind: "ok", product: plain });
  });

  // SHOP-TASK-186 记录「字段同服务层的 `AdminProduct`」：商品、规格名、规格值、SKU 与图片上多出的字段都不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(
      json(200, {
        ...PRODUCT,
        created_at: "2026-11-03",
        audit: [],
        options: [{ ...COLOUR, position: 1, values: [{ ...BLACK, position: 1 }, WHITE] }, SIZE],
        variants: [{ ...BLACK_M, reserved: 2 }, WHITE_L],
        images: [{ ...IMAGE, width: 800, original: "x.jpg" }],
      }),
    );
    await expect(readAdminProduct(7)).resolves.toStrictEqual({ kind: "ok", product: PRODUCT });
  });

  // 各例守住的 SHOP-TASK-186 记录、DESIGN 1.18 第 11 条与 UX 0.17 A04 原句见 MALFORMED_PRODUCTS 各组的注释：任一处不合格，整个读取归为 failed。
  it.each(MALFORMED_PRODUCTS)("treats a malformed product as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminProduct(7)).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「字段同服务层的 `AdminProduct`」：响应体须为一件商品的 JSON 对象，数组、列表、null 或不是 JSON 都算 failed。
  it.each<[string, () => Response]>([
    ["an array", () => json(200, [PRODUCT])],
    ["a list", () => json(200, LIST)],
    ["null body", () => json(200, null)],
    ["not JSON", () => new Response("", { status: 200 })],
  ])("treats a body that is %s as a failure", async (_case, reply) => {
    stubFetch(reply());
    await expect(readAdminProduct(7)).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「`require_admin`（401）→ 路径 ID（……不合法 422 `product_id_invalid`……）→ `load_admin_product`，不存在 404 `{"detail": "product_not_found"}`」：
  // 401 为 none，404 product_not_found、路径 ID 的 422 与其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(404, { detail: "product_not_found" }), "failed"],
    [json(422, { detail: [{ loc: ["path", "product_id"], type: "product_id_invalid", msg: "Product ID should be a positive integer" }] }), "failed"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(500, {}), "failed"],
    [json(201, PRODUCT), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminProduct(7)).resolves.toEqual({ kind });
  });

  // SHOP-TASK-186 记录、DESIGN 1.18 第 11 条与 UX 0.17 A04 都没有关于中止的原句（记录的偏离一节已列明）；最接近的是 UX 0.17 A04「没有未保存的修改时直接切换」
  // （HANDOFF 0.82 第（2）点「返回会中止未完成的读取」），守住的是本任务验收「网络中断与中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, PRODUCT));
    controller.abort();
    await expect(readAdminProduct(7, controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// 新建与保存的内容：带多出的属性（变量先成形再放进去，绕开字面量的多余属性检查，模拟调用方从读取结果或表单状态改出来的对象）；文案带首尾空白与空串，用来确认原样提交。
const createWithExtras = {
  category_id: 2,
  name_en: " Crew Neck Tee ",
  name_zh: "",
  name_ms: "Baju",
  description_en: "Cotton\n",
  description_zh: "",
  description_ms: "",
  is_active: false,
  max_per_order: 10,
  slug: "crew-neck-tee",
  version: 1,
  csrf_token: "csrf-abc",
  options: [
    {
      code: "color",
      name_en: "Colour ",
      name_zh: "",
      name_ms: "Warna",
      values: [
        { temp_id: "v1", name_en: "Black", name_zh: "黑", name_ms: "", code: "black", selected: true },
        { temp_id: "v2", name_en: "White", name_zh: "", name_ms: "Putih" },
      ],
    },
  ],
  variants: [
    { temp_id: "s1", option_values: [{ temp_id: "v1", label: "Black" }], price_sen: 2500, daily_initial_stock: 20, is_active: true, sku: "", price_text: "25.00" },
    { temp_id: "s2", option_values: [{ temp_id: "v2" }], price_sen: 0, daily_initial_stock: 0, is_active: false },
  ],
};
const CREATE: ProductCreate = createWithExtras;

// 保存：已有的规格值与 SKU 上带着读取结果的其他字段（code、sku、available_stock、option_value_ids），还有一个既有 id 又带 temp_id 的值与带 option_values 的已有 SKU，
// 用来确认只写出该形状的字段；新增的值与 SKU 以 temp_id 给出，新增 SKU 以 id 引用已有的值、以 temp_id 引用同一次新增的值。
const saveWithExtras = {
  ...PRODUCT,
  name_en: "Crew Neck Tee ",
  name_zh: "",
  name_ms: "Baju",
  description_en: "Cotton",
  description_zh: "",
  description_ms: " ",
  is_active: true,
  max_per_order: 5,
  options: [
    {
      ...COLOUR,
      name_en: "Colour",
      name_zh: "颜色",
      name_ms: "Warna",
      values: [{ ...BLACK, name_ms: "Hitam", temp_id: "stale" }, { ...WHITE, name_zh: "白" }, { temp_id: "v-red", name_en: "Red", name_zh: "", name_ms: "Merah" }],
    },
    { ...SIZE, name_en: "Size", name_zh: "尺码", name_ms: "Saiz", values: [{ ...MEDIUM, name_zh: "中", name_ms: "" }, { ...LARGE, name_zh: "大", name_ms: "" }] },
  ],
  variants: [
    { ...BLACK_M, price_sen: 2600, daily_initial_stock: 15, is_active: false, option_values: [{ id: 21 }, { id: 23 }] },
    { ...WHITE_L, name: "White / L" },
    { temp_id: "s-new", option_values: [{ temp_id: "v-red", label: "Red" }, { id: 23, code: "m" }], price_sen: 1999, daily_initial_stock: 5, is_active: true, sku: "" },
  ],
};
const SAVE: ProductSave = saveWithExtras;

const CREATE_KEYS = ["category_id", "name_en", "name_zh", "name_ms", "description_en", "description_zh", "description_ms", "is_active", "max_per_order", "options", "variants"];

describe("creating a product", () => {
  // SHOP-TASK-186 记录「`POST /api/admin/products`：请求体逐块读取超过 256 KB 即 413 → 不是 JSON 415……→ `require_admin`（401）→ X-CSRF-Token（403 `csrf_failed`）」：
  // 地址为固定路径、POST、凭同源 cookie、不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("posts to the list address with the CSRF token", async () => {
    const fetchMock = stubFetch(json(201, PRODUCT));
    const controller = new AbortController();
    await createAdminProduct(CREATE, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/products");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-186 记录「请求体模型：`ProductIn`、`OptionIn`、`ValueIn`、`VariantIn`、`ValueRefIn`……（`extra="forbid"`、`strict=True`）」「三语名称与描述为字符串（可为空串）」
  // 与 DESIGN 1.18 第 11 条「新增的规格值与 SKU 各带一个请求内的临时编号（与行 ID 分开的字段）」「价格以整仙提交」：请求体恰为十一项；规格名只有三语名称与 values，
  // 规格值只有 temp_id 与三语名称，SKU 只有 temp_id、option_values（每项只有 temp_id）、price_sen、daily_initial_stock 与 is_active；文案原样，价格为调用方给的整仙；
  // 调用方对象上多出的属性（slug、version、csrf_token、code、sku 等）不进请求体，也没有 null。
  it("sends only the agreed fields, text as given", async () => {
    const fetchMock = stubFetch(json(201, PRODUCT));
    await createAdminProduct(CREATE, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(CREATE_KEYS);
    expect(body).toStrictEqual({
      category_id: 2,
      name_en: " Crew Neck Tee ",
      name_zh: "",
      name_ms: "Baju",
      description_en: "Cotton\n",
      description_zh: "",
      description_ms: "",
      is_active: false,
      max_per_order: 10,
      options: [
        {
          name_en: "Colour ",
          name_zh: "",
          name_ms: "Warna",
          values: [
            { temp_id: "v1", name_en: "Black", name_zh: "黑", name_ms: "" },
            { temp_id: "v2", name_en: "White", name_zh: "", name_ms: "Putih" },
          ],
        },
      ],
      variants: [
        { temp_id: "s1", option_values: [{ temp_id: "v1" }], price_sen: 2500, daily_initial_stock: 20, is_active: true },
        { temp_id: "s2", option_values: [{ temp_id: "v2" }], price_sen: 0, daily_initial_stock: 0, is_active: false },
      ],
    });
    expect(sentText(fetchMock, 0)).not.toContain("null");
  });

  // UX 0.17 A04「没有规格名时为 [admin.no_options]，SKU 表固定一行」：没有规格名时 options 为空数组，唯一的 SKU 的 option_values 为空数组。
  it("sends a product without options", async () => {
    const fetchMock = stubFetch(json(201, PRODUCT));
    const plain: ProductCreate = { ...CREATE, options: [], variants: [{ temp_id: "s1", option_values: [], price_sen: 990, daily_initial_stock: 3, is_active: true }] };
    await createAdminProduct(plain, "csrf-abc");
    expect(sentBody(fetchMock, 0)).toMatchObject({ options: [], variants: [{ temp_id: "s1", option_values: [], price_sen: 990, daily_initial_stock: 3, is_active: true }] });
  });

  // SHOP-TASK-186 记录「成功 201 与新商品（同单件读取）」与 UX 0.17 A04「新建成功后进入该商品的编辑区、显示 `[admin.product_created]`」：结果带服务端返回的商品（含生成的
  // slug、代码与 SKU），多出的字段不带进结果。
  it("returns the created product", async () => {
    stubFetch(json(201, { ...PRODUCT, created_at: "2026-11-03" }));
    await expect(createAdminProduct(CREATE, "csrf-abc")).resolves.toStrictEqual({ kind: "ok", product: PRODUCT });
  });

  // SHOP-TASK-186 记录「成功 201 与新商品」：新建只在 201 时算成功，200 或 204 都不是本接口约定的回答，归 failed。
  it.each([200, 204])("treats a success status other than 201 as a failure (%i)", async (status) => {
    stubFetch(status === 204 ? new Response(null, { status }) : json(status, PRODUCT));
    await expect(createAdminProduct(CREATE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });
});

describe("saving a product", () => {
  // SHOP-TASK-186 记录「`PUT /api/admin/products/{product_id}`：413 → 415 → 401 → 403……→ 路径 ID 422」：地址为固定路径加商品 ID 的十进制、PUT、凭同源 cookie、不缓存，
  // Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("puts to the product address with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, PRODUCT), json(200, PRODUCT));
    const controller = new AbortController();
    await saveAdminProduct(7, SAVE, "csrf-abc", controller.signal);
    await saveAdminProduct(2_147_483_647, SAVE, "csrf-abc");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/products/7");
    expect(sentUrl(fetchMock, 1)).toBe("/api/admin/products/2147483647");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-186 记录「请求体模型 `ProductSaveIn`（同新建，另有必填的严格整数 `version`）」与 DESIGN 1.18 第 11 条「保存请求带读取时的版本号，以行 ID 列出该商品现有的
  // 全部规格名、规格值与 SKU，一个不漏，新增的只给临时编号与取值」「已有 SKU 的规格值组合不能改，保存请求里已有 SKU 不带组合」「新增 SKU 以行 ID 引用已有规格值、以临时编号
  // 引用同一次保存里新增的规格值」：请求体为新建的各项另加 version；规格名带 id；已有的值只有 id 与三语名称（另带的 temp_id 不写出），新增的值只有 temp_id 与三语名称；
  // 已有 SKU 只有 id、price_sen、daily_initial_stock 与 is_active（不带 option_values），新增 SKU 只有 temp_id、option_values（每项只有 id 或只有 temp_id）与三项取值；
  // 读取结果上的 slug、code、sku、available_stock、option_value_ids、images 不进请求体，也没有 null。
  it("sends each row in its own shape and the version", async () => {
    const fetchMock = stubFetch(json(200, PRODUCT));
    await saveAdminProduct(7, SAVE, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual([...CREATE_KEYS, "version"]);
    expect(body).toStrictEqual({
      category_id: 2,
      name_en: "Crew Neck Tee ",
      name_zh: "",
      name_ms: "Baju",
      description_en: "Cotton",
      description_zh: "",
      description_ms: " ",
      is_active: true,
      max_per_order: 5,
      options: [
        {
          id: 11,
          name_en: "Colour",
          name_zh: "颜色",
          name_ms: "Warna",
          values: [
            { id: 21, name_en: "Black", name_zh: "黑", name_ms: "Hitam" },
            { id: 22, name_en: "White", name_zh: "白", name_ms: "Putih" },
            { temp_id: "v-red", name_en: "Red", name_zh: "", name_ms: "Merah" },
          ],
        },
        {
          id: 12,
          name_en: "Size",
          name_zh: "尺码",
          name_ms: "Saiz",
          values: [
            { id: 23, name_en: "M", name_zh: "中", name_ms: "" },
            { id: 24, name_en: "L", name_zh: "大", name_ms: "" },
          ],
        },
      ],
      variants: [
        { id: 31, price_sen: 2600, daily_initial_stock: 15, is_active: false },
        { id: 32, price_sen: 0, daily_initial_stock: 0, is_active: false },
        { temp_id: "s-new", option_values: [{ temp_id: "v-red" }, { id: 23 }], price_sen: 1999, daily_initial_stock: 5, is_active: true },
      ],
      version: 3,
    });
    expect(sentText(fetchMock, 0)).not.toContain("null");
  });

  // SHOP-TASK-186 记录「成功或未变都 200 与保存后的商品」与 UX 0.17 A04「成功时页面改显示服务端返回的保存后数据（版本号随之更新）」：结果带服务端返回的商品
  // （以响应为准，不是提交的值），多出的字段不带进结果。
  it("returns the saved product from the reply", async () => {
    const saved = { ...PRODUCT, version: 4, max_per_order: 5, variants: [{ ...BLACK_M, price_sen: 2600 }, WHITE_L] };
    stubFetch(json(200, { ...saved, audit: [] }));
    await expect(saveAdminProduct(7, SAVE, "csrf-abc")).resolves.toStrictEqual({ kind: "ok", product: saved });
  });

  // SHOP-TASK-186 记录「成功或未变都 200」：保存只在 200 时算成功，201 或 204 都归 failed。
  it.each([201, 204])("treats a success status other than 200 as a failure (%i)", async (status) => {
    stubFetch(status === 204 ? new Response(null, { status }) : json(status, PRODUCT));
    await expect(saveAdminProduct(7, SAVE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });
});

// 两个写操作的差别：调用方式与成功的状态码。
const WRITES: [string, (signal?: AbortSignal) => ReturnType<typeof createAdminProduct>, number][] = [
  ["create", (signal) => createAdminProduct(CREATE, "csrf-abc", signal), 201],
  ["save", (signal) => saveAdminProduct(7, SAVE, "csrf-abc", signal), 200],
];

describe.each(WRITES)("sorting the reply to %s", (_name, write, okStatus) => {
  // SHOP-TASK-186 记录「成功 201 与新商品（同单件读取）」与「成功或未变都 200 与保存后的商品」：成功回答的商品与单件读取条件相同（各例守住的原句见 MALFORMED_PRODUCTS 的注释）。
  it.each(MALFORMED_PRODUCTS)("treats a malformed product as a failure: %s", async (_case, body) => {
    stubFetch(json(okStatus, body));
    await expect(write()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「成功 201 与新商品（同单件读取）」「成功或未变都 200 与保存后的商品」：响应体须为一件商品的对象，数组、列表、null 或不是 JSON 都算 failed。
  it.each<[string, () => Response]>([
    ["an array", () => json(okStatus, [PRODUCT])],
    ["a list", () => json(okStatus, LIST)],
    ["null body", () => json(okStatus, null)],
    ["not JSON", () => new Response("", { status: okStatus })],
  ])("treats a body that is %s as a failure", async (_case, reply) => {
    stubFetch(reply());
    await expect(write()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-186 记录「`ProductInvalid` 回 422 `{"detail": "product_invalid"}`，`ProductChanged`（含保存时商品不存在）回 409 `{"detail": "product_changed"}`」、
  // 「`require_admin`（401）」「X-CSRF-Token（403 `csrf_failed`）」「超过 256 KB 即 413」「不是 JSON 415」「不合法 422 `product_id_invalid`」与 UX 0.17 A04「其他失败显示
  // `[common.error_retry]`」：只有 detail 恰为 product_invalid 的 422 为 invalid、恰为 product_changed 的 409 为 changed；detail 对调、大小写不同、路径 ID 与请求体 422 的
  // 错误列表、不是 JSON、413、404 与其他状态带同样的 detail 都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "product_invalid" }), "invalid"],
    [json(409, { detail: "product_changed" }), "changed"],
    [json(422, { detail: "product_changed" }), "failed"],
    [json(409, { detail: "product_invalid" }), "failed"],
    [json(422, { detail: "PRODUCT_INVALID" }), "failed"],
    [json(409, { detail: "PRODUCT_CHANGED" }), "failed"],
    [json(422, { detail: [{ loc: ["path", "product_id"], type: "product_id_invalid", msg: "Product ID should be a positive integer" }] }), "failed"],
    [json(422, { detail: [{ loc: ["body", "variants", 0, "price_sen"], type: "int_type", msg: "invalid" }] }), "failed"],
    [json(422, {}), "failed"],
    [json(409, {}), "failed"],
    [new Response("product_invalid", { status: 422 }), "failed"],
    [new Response("product_changed", { status: 409 }), "failed"],
    [json(413, { detail: "request body too large" }), "failed"],
    [new Response("<html>413</html>", { status: 413 }), "failed"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(404, { detail: "product_not_found" }), "failed"],
    [json(405, {}), "failed"],
    [json(500, { detail: "product_invalid" }), "failed"],
    [json(400, { detail: "product_changed" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(write()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-186 记录、DESIGN 1.18 第 11 条与 UX 0.17 A04 都没有关于中止的原句（记录的偏离一节已列明）；最接近的是 UX 0.17 A04「没有未保存的修改时直接切换」，
  // 守住的是本任务验收「网络中断与中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(okStatus, PRODUCT));
    controller.abort();
    await expect(write(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

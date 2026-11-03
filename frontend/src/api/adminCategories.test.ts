import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_CATEGORIES_URL, createAdminCategory, readAdminCategories, saveAdminCategory } from "./adminCategories";
import type { CategoryCreate, CategorySave } from "./adminCategories";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 分类（SHOP-TASK-181 记录的字段：id、slug、name_en、name_zh、name_ms、is_active、version、visible_products，名称为空时为 null）。
const SHOES = { id: 1, slug: "shoes", name_en: "Shoes", name_zh: "鞋", name_ms: "Kasut", is_active: true, version: 2, visible_products: 5 };
const BAGS = { id: 3, slug: "bags", name_en: "Bags", name_zh: null, name_ms: null, is_active: false, version: 1, visible_products: 0 };

const LIST = { categories: [SHOES, BAGS], csrf_token: "csrf-abc" };

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
    if (next === undefined || (url !== ADMIN_CATEGORIES_URL && !url.startsWith(`${ADMIN_CATEGORIES_URL}/`))) {
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

function sentBody(fetchMock: FetchMock, index: number): unknown {
  const body = sentInit(fetchMock, index).body;
  if (typeof body !== "string") {
    throw new Error("request body is not a JSON string");
  }
  return JSON.parse(body) as unknown;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 读取与写操作共用的不合格分类：在合格的分类上换掉一项（值为 undefined 时 JSON 里没有这一项）。
const MALFORMED_CATEGORIES: [string, Record<string, unknown>][] = [
  // SHOP-TASK-181 记录「字段依次为 `id`、`slug`、…、`visible_products`」与 DESIGN 1.18 第 14 条「以行 ID 记录」：id 缺少、为 0、负数、小数、字符串都不合格。
  ["id missing", { id: undefined }],
  ["id zero", { id: 0 }],
  ["id negative", { id: -1 }],
  ["id fractional", { id: 1.5 }],
  ["id as string", { id: "1" }],
  // DESIGN 1.18 第 14 条「分类的 slug（前台分类筛选用）在新建时按英文名称生成……结果为空时以 category 代替」：slug 不会为空，缺少、为空串、null、数字都不合格。
  ["slug missing", { slug: undefined }],
  ["slug empty", { slug: "" }],
  ["slug null", { slug: null }],
  ["slug as number", { slug: 1 }],
  // SHOP-TASK-181 记录「名称为空时为 null」：名称为字符串或 null，缺少、数字、布尔值、数组都不合格。
  ["name_en missing", { name_en: undefined }],
  ["name_en as number", { name_en: 1 }],
  ["name_zh missing", { name_zh: undefined }],
  ["name_zh as boolean", { name_zh: false }],
  ["name_ms missing", { name_ms: undefined }],
  ["name_ms as array", { name_ms: ["Kasut"] }],
  // UX 0.17 A10「逐个保存分类的三语名称与启用状态」：启用状态为布尔值，缺少、字符串、数字、null 都不合格。
  ["is_active missing", { is_active: undefined }],
  ["is_active as string", { is_active: "true" }],
  ["is_active as number", { is_active: 1 }],
  ["is_active null", { is_active: null }],
  // DESIGN 1.18 第 14 条「每个分类带版本号……保存时带读取时的版本号」：版本号缺少、负数、小数、字符串、null 都不合格。
  ["version missing", { version: undefined }],
  ["version negative", { version: -1 }],
  ["version fractional", { version: 1.5 }],
  ["version as string", { version: "2" }],
  ["version null", { version: null }],
  // DESIGN 1.18 第 14 条「后台读取分类时返回每个分类下前台可见……的商品件数」：件数缺少、负数、小数、字符串都不合格。
  ["visible_products missing", { visible_products: undefined }],
  ["visible_products negative", { visible_products: -1 }],
  ["visible_products fractional", { visible_products: 0.5 }],
  ["visible_products as string", { visible_products: "5" }],
];

describe("reading the categories", () => {
  // SHOP-TASK-181 记录「`GET /api/admin/categories`：`require_admin`……→ `categories`……与由 cookie 算出的 `csrf_token`」：地址为固定路径、
  // 不带查询参数、GET、凭同源 cookie、不缓存、Accept 为 JSON、没有请求体与 CSRF 头，并带上调用方的 signal。
  it("gets the list with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, LIST));
    const controller = new AbortController();
    await readAdminCategories(controller.signal);
    expect(ADMIN_CATEGORIES_URL).toBe("/api/admin/categories");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/categories");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // UX 0.17 A10「全部分类按 ID 从小到大（即前台分类列表的顺序）」与 DESIGN 1.18 第 14 条「查看全部分类（含停用的……）」：分类按接口给的顺序
  // 带进结果（不在这里重排），停用的与名称为 null 的照样带进；CSRF 令牌带进结果。
  it("keeps the categories in the order given", async () => {
    stubFetch(json(200, { categories: [BAGS, SHOES], csrf_token: "csrf-abc" }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "ok", categories: [BAGS, SHOES], csrf_token: "csrf-abc" });
  });

  // SHOP-TASK-181 记录「字段依次为 `id`、`slug`、`name_en`、`name_zh`、`name_ms`、`is_active`、`version`、`visible_products`」：顶层与分类上多出的字段不带进结果。
  it("keeps only the agreed fields", async () => {
    stubFetch(json(200, { ...LIST, total: 2, limit: 30, categories: [{ ...SHOES, created_at: "2026-10-30", products: [] }, BAGS] }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "ok", ...LIST });
  });

  // DESIGN 1.18 第 14 条「全站至多 30 个分类」没有下限：一个分类都没有时照样合格；件数为 0、名称全为 null 的停用分类合格。
  it("accepts an empty list and a category without names", async () => {
    stubFetch(json(200, { categories: [], csrf_token: "t" }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "ok", categories: [], csrf_token: "t" });
    const unnamed = { ...BAGS, name_en: null, visible_products: 0 };
    stubFetch(json(200, { categories: [unnamed], csrf_token: "t" }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "ok", categories: [unnamed], csrf_token: "t" });
  });

  // 各例守住的 SHOP-TASK-181 记录、DESIGN 1.18 第 14 条与 UX 0.17 A10 原句见 MALFORMED_CATEGORIES 各组的注释：任一个分类不合格，整个读取归为 failed。
  it.each(MALFORMED_CATEGORIES)("treats a malformed category as a failure: %s", async (_name, change) => {
    stubFetch(json(200, { ...LIST, categories: [SHOES, { ...BAGS, ...change }] }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-181 记录「→ `categories`（`load_admin_categories` 的全部项……）与由 cookie 算出的 `csrf_token`」：categories 缺少、不是数组、含 null，
  // csrf_token 缺少、为空、为数字或 null，整体不是对象都不合格。
  it.each<[string, unknown]>([
    ["categories missing", { csrf_token: "csrf-abc" }],
    ["categories not an array", { categories: SHOES, csrf_token: "csrf-abc" }],
    ["categories null", { categories: null, csrf_token: "csrf-abc" }],
    ["category null", { categories: [SHOES, null], csrf_token: "csrf-abc" }],
    ["csrf token missing", { categories: [SHOES] }],
    ["csrf token empty", { ...LIST, csrf_token: "" }],
    ["csrf token as number", { ...LIST, csrf_token: 1 }],
    ["csrf token null", { ...LIST, csrf_token: null }],
    ["not an object", [LIST]],
    ["null body", null],
  ])("treats a malformed list as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-181 记录「→ `categories`……与由 cookie 算出的 `csrf_token`」：200 的响应体须为这样一个 JSON 对象，不是 JSON 时算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readAdminCategories()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-181 记录「`require_admin`（401 `admin_session_required`）」：401 为 none，其他状态为 failed，fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(422, { detail: "category_invalid" }), "failed"],
    [json(409, { detail: "category_changed" }), "failed"],
    [json(500, {}), "failed"],
    [json(201, LIST), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminCategories()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-181 记录、DESIGN 1.18 第 14 条与 UX 0.17 A10 都没有关于中止的原句（记录的偏离一节已列明）；最接近的是 UX 0.17 A10「没有未保存的
  // 修改时直接切换」（切换时页面中止未完成的请求），守住的是本任务验收「网络中断与调用方中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, LIST));
    controller.abort();
    await expect(readAdminCategories(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

// 新建与保存的内容：带多出的属性（变量先成形再放进去，绕开字面量的多余属性检查，模拟调用方从读取结果或表单状态改出来的对象）；
// 名称带首尾空白与空串，用来确认原样提交。
const createWithExtras = { name_en: "  Hats ", name_zh: "", name_ms: "Topi", is_active: true, slug: "hats", csrf_token: "csrf-abc" };
const CREATE: CategoryCreate = createWithExtras;
const saveWithExtras = { ...SHOES, name_en: " Shoes ", name_zh: "", is_active: false };
const SAVE: CategorySave = saveWithExtras;

// 两个写操作的差别：调用方式、方法、地址与成功的状态码。
const WRITES: [string, (signal?: AbortSignal) => ReturnType<typeof createAdminCategory>, number][] = [
  ["create", (signal) => createAdminCategory(CREATE, "csrf-abc", signal), 201],
  ["save", (signal) => saveAdminCategory(1, SAVE, "csrf-abc", signal), 200],
];

describe("creating a category", () => {
  // SHOP-TASK-181 记录「`POST /api/admin/categories`……→ X-CSRF-Token（403 `csrf_failed`）」与「不是 JSON 415」：地址为固定路径、POST、凭同源 cookie、
  // 不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("posts to the list address with the CSRF token", async () => {
    const fetchMock = stubFetch(json(201, SHOES));
    const controller = new AbortController();
    await createAdminCategory(CREATE, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/categories");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-181 记录「请求体模型 `CategoryIn`（`name_en`、`name_zh`、`name_ms` 为字符串，`is_active` 为布尔值，四项必填，`extra="forbid"`……）」
  // 与「偏离与取舍」「长度、首尾空白与英文名称必填都交给服务层」：请求体恰为四项，名称原样（含首尾空白与空串），调用方对象上多出的属性（slug、csrf_token）不进请求体。
  it("sends only the four fields, names as given", async () => {
    const fetchMock = stubFetch(json(201, SHOES));
    await createAdminCategory(CREATE, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(["name_en", "name_zh", "name_ms", "is_active"]);
    expect(body).toEqual({ name_en: "  Hats ", name_zh: "", name_ms: "Topi", is_active: true });
  });

  // SHOP-TASK-181 记录「成功 201 与新分类」与 UX 0.17 A10「新建成功后进入该分类的编辑区、显示 `[admin.category_created]` 与生成的 slug」：
  // 结果带服务端返回的分类（含生成的 slug），多出的字段不带进结果。
  it("returns the created category", async () => {
    stubFetch(json(201, { ...SHOES, created_at: "2026-11-03" }));
    await expect(createAdminCategory(CREATE, "csrf-abc")).resolves.toEqual({ kind: "ok", category: SHOES });
  });

  // SHOP-TASK-181 记录「成功 201 与新分类」：新建的分类只在 201 时算成功，200 或 204 都不是本接口约定的回答，归 failed。
  it.each([200, 204])("treats a success status other than 201 as a failure (%i)", async (status) => {
    stubFetch(status === 204 ? new Response(null, { status }) : json(status, SHOES));
    await expect(createAdminCategory(CREATE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });
});

describe("saving a category", () => {
  // SHOP-TASK-181 记录「`PUT /api/admin/categories/{category_id}`：413 → 415 → 401 → 403……→ 路径 ID（……不带符号与前导零……）」：地址为固定路径
  // 加分类 ID 的十进制、PUT、凭同源 cookie、不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("puts to the category address with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, SHOES), json(200, SHOES));
    const controller = new AbortController();
    await saveAdminCategory(1, SAVE, "csrf-abc", controller.signal);
    await saveAdminCategory(2_147_483_647, SAVE, "csrf-abc");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/categories/1");
    expect(sentUrl(fetchMock, 1)).toBe("/api/admin/categories/2147483647");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-181 记录「请求体模型 `CategorySaveIn`（同新建，另有必填的严格整数 `version`）」与 DESIGN 1.18 第 14 条「保存时带读取时的版本号」：
  // 请求体恰为五项，名称原样，版本号为调用方给的；读取结果上的 id、slug、visible_products 不进请求体。
  it("sends only the four fields and the version", async () => {
    const fetchMock = stubFetch(json(200, SHOES));
    await saveAdminCategory(1, SAVE, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(["name_en", "name_zh", "name_ms", "is_active", "version"]);
    expect(body).toEqual({ name_en: " Shoes ", name_zh: "", name_ms: "Kasut", is_active: false, version: 2 });
  });

  // SHOP-TASK-181 记录「成功或未变都 200 与保存后的分类」与 UX 0.17 A10「成功时页面改显示服务端返回的保存后数据（版本号与列表随之更新）」：
  // 结果带服务端返回的分类（以响应为准，不是提交的值），多出的字段不带进结果。
  it("returns the saved category from the reply", async () => {
    const saved = { ...SHOES, name_en: "Shoes", is_active: false, version: 3 };
    stubFetch(json(200, { ...saved, audit: [] }));
    await expect(saveAdminCategory(1, SAVE, "csrf-abc")).resolves.toEqual({ kind: "ok", category: saved });
  });

  // SHOP-TASK-181 记录「成功或未变都 200」：保存只在 200 时算成功，201 或 204 都归 failed。
  it.each([201, 204])("treats a success status other than 200 as a failure (%i)", async (status) => {
    stubFetch(status === 204 ? new Response(null, { status }) : json(status, SHOES));
    await expect(saveAdminCategory(1, SAVE, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });
});

describe.each(WRITES)("sorting the reply to %s", (_name, write, okStatus) => {
  // SHOP-TASK-181 记录「新分类（字段同读取的一项）」与「保存后的分类」：成功回答的分类与读取的一项条件相同（各例守住的原句见 MALFORMED_CATEGORIES 的注释）。
  it.each(MALFORMED_CATEGORIES)("treats a malformed category as a failure: %s", async (_case, change) => {
    stubFetch(json(okStatus, { ...SHOES, ...change }));
    await expect(write()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-181 记录「成功 201 与新分类」「成功或未变都 200 与保存后的分类」：响应体须为一个分类对象，数组、带 categories 的列表、null 或不是 JSON 都算 failed。
  it.each<[string, () => Response]>([
    ["an array", () => json(okStatus, [SHOES])],
    ["a list", () => json(okStatus, LIST)],
    ["null body", () => json(okStatus, null)],
    ["not JSON", () => new Response("", { status: okStatus })],
  ])("treats a body that is %s as a failure", async (_case, reply) => {
    stubFetch(reply());
    await expect(write()).resolves.toEqual({ kind: "failed" });
  });

  // SHOP-TASK-181 记录「`CategoryInvalid` 回 422 `{"detail": "category_invalid"}`，`CategoryChanged`（含分类不存在）回 409 `{"detail": "category_changed"}`」、
  // 「require_admin（401）」「X-CSRF-Token（403 `csrf_failed`）」「8 KB 上限 413」「不是 JSON 415」「不合法 422 `category_id_invalid`」与 UX 0.17 A10
  // 「其他失败显示 `[common.error_retry]`」：只有 detail 恰为 category_invalid 的 422 为 invalid、恰为 category_changed 的 409 为 changed；detail 对调、
  // 大小写不同、路径 ID 与请求体 422 的错误列表、不是 JSON、其他状态带同样的 detail 都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "category_invalid" }), "invalid"],
    [json(409, { detail: "category_changed" }), "changed"],
    [json(422, { detail: "category_changed" }), "failed"],
    [json(409, { detail: "category_invalid" }), "failed"],
    [json(422, { detail: "CATEGORY_INVALID" }), "failed"],
    [json(409, { detail: "CATEGORY_CHANGED" }), "failed"],
    [json(422, { detail: [{ loc: ["path", "category_id"], type: "category_id_invalid", msg: "Category ID should be a positive integer" }] }), "failed"],
    [json(422, { detail: [{ loc: ["body", "is_active"], type: "bool_type", msg: "invalid" }] }), "failed"],
    [json(422, {}), "failed"],
    [json(409, {}), "failed"],
    [new Response("category_invalid", { status: 422 }), "failed"],
    [new Response("category_changed", { status: 409 }), "failed"],
    [json(413, { detail: "request body too large" }), "failed"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(500, { detail: "category_invalid" }), "failed"],
    [json(400, { detail: "category_changed" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(write()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-181 记录、DESIGN 1.18 第 14 条与 UX 0.17 A10 都没有关于中止的原句（记录的偏离一节已列明）；最接近的是 UX 0.17 A10「没有未保存的
  // 修改时直接切换」，守住的是本任务验收「网络中断与中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(okStatus, SHOES));
    controller.abort();
    await expect(write(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

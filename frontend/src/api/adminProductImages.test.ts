import { afterEach, describe, expect, it, vi } from "vitest";

import { removeAdminProductImage, reorderAdminProductImages, uploadAdminProductImage } from "./adminProductImages";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 图片列表（app/api/admin_product_images.py 的 ImagesOut：images 每项为 ImageOut 的 id 与 storage_ref）。
const IMAGE_1 = { id: 11, storage_ref: "/api/product-images/a1b2c3.webp" };
const IMAGE_2 = { id: 12, storage_ref: "/api/product-images/d4e5f6.webp" };
const IMAGES = [IMAGE_1, IMAGE_2];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不在商品接口之下时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || !String(input).startsWith("/api/admin/products/")) {
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

// 一个文件：Blob 自带的类型与调用方给的 Content-Type 故意不同，用来确认请求头取的是调用方给的。
function imageFile(): Blob {
  return new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: "application/octet-stream" });
}

// 三个接口成功时共用的不合格响应体。
const MALFORMED_IMAGES: [string, unknown][] = [
  // 接口记录「三个接口成功都回答 images」（HANDOFF 0.79）与 ImagesOut 的 `images: list[ImageOut]`：images 缺少、为 null、为对象或字符串，
  // 整体不是对象、为数组或 null 都不合格。
  ["images missing", {}],
  ["images null", { images: null }],
  ["images an object", { images: IMAGE_1 }],
  ["images a string", { images: "11" }],
  ["body an array", IMAGES],
  ["body null", null],
  // ImageOut 的 `id: int`：任一项的 id 缺少、为 0、负数、小数、字符串或 null 都不合格（接口按数据库行 ID 给出，为正整数）。
  ["id missing", { images: [IMAGE_1, { storage_ref: IMAGE_2.storage_ref }] }],
  ["id zero", { images: [IMAGE_1, { ...IMAGE_2, id: 0 }] }],
  ["id negative", { images: [IMAGE_1, { ...IMAGE_2, id: -12 }] }],
  ["id fractional", { images: [IMAGE_1, { ...IMAGE_2, id: 12.5 }] }],
  ["id a string", { images: [IMAGE_1, { ...IMAGE_2, id: "12" }] }],
  ["id null", { images: [IMAGE_1, { ...IMAGE_2, id: null }] }],
  // ImageOut 的 `storage_ref: str`，HANDOFF 0.82 第（6）点「图片缩略图的 src 为接口给的 `storage_ref`」：缺少、空串、数字或 null 都不合格。
  ["storage_ref missing", { images: [IMAGE_1, { id: 12 }] }],
  ["storage_ref empty", { images: [IMAGE_1, { ...IMAGE_2, storage_ref: "" }] }],
  ["storage_ref a number", { images: [IMAGE_1, { ...IMAGE_2, storage_ref: 12 }] }],
  ["storage_ref null", { images: [IMAGE_1, { ...IMAGE_2, storage_ref: null }] }],
  ["image null", { images: [IMAGE_1, null] }],
  ["image an array", { images: [IMAGE_1, [12, "x"]] }],
];

describe("uploading an image", () => {
  // 接口记录「POST /api/admin/products/{product_id}/images」「2. X-CSRF-Token（403 csrf_failed）」与「三个接口的……全部响应……都带
  // Cache-Control: no-store」：地址为商品 ID 的十进制路径段、不带查询参数，POST、凭同源 cookie、不缓存，Accept 为 JSON，X-CSRF-Token 为调用方给的令牌，
  // 并带上调用方的 signal。
  it.each<[number, string]>([
    [7, "/api/admin/products/7/images"],
    [2147483647, "/api/admin/products/2147483647/images"],
  ])("posts to the images of product %i", async (productId, url) => {
    const fetchMock = stubFetch(json(201, { images: IMAGES }));
    const controller = new AbortController();
    await uploadAdminProductImage(productId, imageFile(), "image/jpeg", "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(url);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // DESIGN 1.19 第 13 条「上传**：一次一张，请求体就是图片文件本身（不用多部分表单）」、接口记录「由最后声明的异步依赖用 request.stream()
  // 逐块读取……（不用多部分表单）」与 HANDOFF 0.82 第（1）点「上传的请求体为文件本身（不用多部分表单）」：请求体就是调用方给的那个 Blob，不是 FormData；
  // 接口记录「6. Content-Type……不是 image/jpeg、image/png、image/webp 之一即 415」：Content-Type 为调用方给的类型字符串，不取 Blob 自带的类型。
  it.each(["image/jpeg", "image/png", "image/webp"])("sends the file itself with the given type %s", async (contentType) => {
    const fetchMock = stubFetch(json(201, { images: IMAGES }));
    const file = imageFile();
    await uploadAdminProductImage(7, file, contentType, "csrf-abc");
    const init = sentInit(fetchMock, 0);
    expect(init.body).toBe(file);
    expect(init.body instanceof FormData).toBe(false);
    expect(new Headers(init.headers).get("Content-Type")).toBe(contentType);
  });

  // UX 0.17 A04「上传、排序与移除各自立即生效……成功后图片区改显示服务端返回的图片列表」与接口记录「成功 201 与 images」：结果按接口给的顺序带回
  // 图片列表，顶层与每张图上多出的字段不带进结果。
  it("returns the images from the reply only", async () => {
    stubFetch(json(201, { images: [{ ...IMAGE_1, position: 0, width: 800 }, IMAGE_2], product_version: 3 }));
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "ok", images: IMAGES });
  });

  // 各例守住的接口记录、HANDOFF 0.82 原句见 MALFORMED_IMAGES 各组的注释：任一项不合格，整个上传归为 failed。
  it.each(MALFORMED_IMAGES)("treats a malformed reply as a failure: %s", async (_name, body) => {
    stubFetch(json(201, body));
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「成功 201 与 images」：201 的响应体须为 JSON 对象，不是 JSON 时读不出图片列表，算 failed；回 200 也不是上传成功。
  it.each<[string, Response]>([
    ["not JSON", new Response("<html>", { status: 201 })],
    ["200 instead of 201", json(200, { images: IMAGES })],
  ])("treats an unexpected success as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「1. require_admin（401 admin_session_required）」「2. X-CSRF-Token（403 csrf_failed）」「6. ……即 415 image_type_unsupported」
  // 「8. 已有 8 张即 409 images_full」「9. process_image 抛 ImageRejected 即 422 image_invalid」，「4. ……404 product_not_found」与「5. 存储未配置
  // 即 503 image_store_unavailable」，UX 0.17 A04「被拒时按服务端的回答显示 `[admin.image_type_unsupported]`、`[admin.image_too_large]`、
  // `[admin.images_full]` 或 `[admin.image_invalid]`」：只有 detail 恰为对应键时归为对应的拒绝；detail 对调、大小写不同、路径 ID 的错误列表、空对象、
  // 不是 JSON、404、503 与其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(415, { detail: "image_type_unsupported" }), "type_unsupported"],
    [json(409, { detail: "images_full" }), "full"],
    [json(422, { detail: "image_invalid" }), "invalid"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(415, { detail: "IMAGE_TYPE_UNSUPPORTED" }), "failed"],
    [json(415, {}), "failed"],
    [new Response("image_type_unsupported", { status: 415 }), "failed"],
    [json(409, { detail: "images_changed" }), "failed"],
    [json(409, { detail: "IMAGES_FULL" }), "failed"],
    [json(409, {}), "failed"],
    [new Response("images_full", { status: 409 }), "failed"],
    [json(422, { detail: "images_full" }), "failed"],
    [json(422, { detail: [{ loc: ["path", "product_id"], type: "product_id_invalid", msg: "Product ID should be a positive integer" }] }), "failed"],
    [json(422, {}), "failed"],
    [new Response("image_invalid", { status: 422 }), "failed"],
    [json(404, { detail: "product_not_found" }), "failed"],
    [json(503, { detail: "image_store_unavailable" }), "failed"],
    [json(500, { detail: "image_invalid" }), "failed"],
    [json(400, { detail: "images_full" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind });
  });

  // HANDOFF 0.82 第（1）点「413 一律归为文件过大、不看响应体（反向代理回答的 413 不是 JSON）」与接口记录「7. ……超过 5,242,880 字节即
  // 413 image_too_large」：接口的 413、反向代理的 HTML、空的与其他 detail 的 413 都为 too_large，且不读响应体。
  it.each<[string, Response]>([
    ["from the API", json(413, { detail: "image_too_large" })],
    ["from the reverse proxy", new Response("<html><head><title>413 Request Entity Too Large</title></head></html>", { status: 413, headers: { "Content-Type": "text/html" } })],
    ["with an empty body", new Response(null, { status: 413 })],
    ["with another detail", json(413, { detail: "request body too large" })],
  ])("treats 413 as too large without reading the body: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "too_large" });
    expect(reply.bodyUsed).toBe(false);
  });

  // 接口记录、DESIGN 1.19 第 13 条与 UX 0.17 A04 都没有关于中止的原句；守住的是本任务验收「网络中断与调用方中止为 network」（UX 0.17 A04 最接近的
  // 一句为「没有未保存的修改时直接切换」）：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(201, { images: IMAGES }));
    controller.abort();
    await expect(uploadAdminProductImage(7, imageFile(), "image/png", "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("reordering the images", () => {
  // 接口记录「PUT /api/admin/products/{product_id}/images」「请求体照 _write_body 逐块读取……不是 JSON 415」「CSRF（403）」：地址为商品 ID 的十进制
  // 路径段、不带查询参数，PUT、凭同源 cookie、不缓存，Accept 与 Content-Type 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it.each<[number, string]>([
    [7, "/api/admin/products/7/images"],
    [2147483647, "/api/admin/products/2147483647/images"],
  ])("puts the order of product %i", async (productId, url) => {
    const fetchMock = stubFetch(json(200, { images: IMAGES }));
    const controller = new AbortController();
    await reorderAdminProductImages(productId, [12, 11], "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(url);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // 接口记录 ReorderIn「该商品现有全部图片 ID 的新顺序」与「strict 的 image_ids 整数数组」（extra="forbid"）：请求体恰为 image_ids 一项，
  // 按调用方给的顺序原样写出，不排序、不去重；调用方的数组不被改动。
  it("sends only image_ids in the given order", async () => {
    const fetchMock = stubFetch(json(200, { images: IMAGES }));
    const ids = [12, 11, 13];
    await reorderAdminProductImages(7, ids, "csrf-abc");
    const body = sentBody(fetchMock, 0);
    expect(Object.keys(body as object)).toEqual(["image_ids"]);
    expect(body).toStrictEqual({ image_ids: [12, 11, 13] });
    expect(ids).toEqual([12, 11, 13]);
  });

  // UX 0.17 A04「成功后图片区改显示服务端返回的图片列表」与接口记录「成功 200 与 images」（顺序未变时同样 200）：结果为服务端返回的列表（不是调用方给的顺序），
  // 多出的字段不带进结果；空列表合格。
  it.each<[string, unknown, unknown[]]>([
    ["the reply order", { images: [{ ...IMAGE_2, extra: true }, IMAGE_1], audit: [] }, [IMAGE_2, IMAGE_1]],
    ["an empty list", { images: [] }, []],
  ])("returns the images from the reply: %s", async (_name, reply, images) => {
    stubFetch(json(200, reply));
    await expect(reorderAdminProductImages(7, [12, 11], "csrf-abc")).resolves.toEqual({ kind: "ok", images });
  });

  // 各例守住的接口记录、HANDOFF 0.82 原句见 MALFORMED_IMAGES 各组的注释；接口记录「成功 200 与 images」：不是 JSON 或回 201 也算 failed。
  it.each<[string, Response]>([
    ...MALFORMED_IMAGES.map(([name, body]): [string, Response] => [name, json(200, body)]),
    ["not JSON", new Response("", { status: 200 })],
    ["201 instead of 200", json(201, { images: IMAGES })],
  ])("treats a malformed reply as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(reorderAdminProductImages(7, [12, 11], "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「上限 8 KB（413）、不是 JSON 415」「require_admin（401）、CSRF（403）、路径 ID（422）、请求体结构（……422 不回显）；reorder_images：
  // ProductMissing 404 product_not_found、ImagesChanged 409 images_changed」与 UX 0.17 A04「排序被拒（图片已在别处增删）显示 `[admin.images_changed]`」：
  // 只有 detail 恰为 images_changed 的 409 为 changed；其他 409、413、415、422、404 与其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(409, { detail: "images_changed" }), "changed"],
    [json(409, { detail: "images_full" }), "failed"],
    [json(409, { detail: "IMAGES_CHANGED" }), "failed"],
    [json(409, {}), "failed"],
    [new Response("images_changed", { status: 409 }), "failed"],
    [json(413, { detail: "request body too large" }), "failed"],
    [json(415, { detail: "request body must be JSON" }), "failed"],
    [json(422, { detail: [{ loc: ["body", "image_ids"], type: "list_type", msg: "invalid" }] }), "failed"],
    [json(422, { detail: "images_changed" }), "failed"],
    [json(404, { detail: "product_not_found" }), "failed"],
    [json(500, { detail: "images_changed" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(reorderAdminProductImages(7, [12, 11], "csrf-abc")).resolves.toEqual({ kind });
  });

  // 接口记录、DESIGN 1.19 第 13 条与 UX 0.17 A04 都没有关于中止的原句；守住的是本任务验收「网络中断与中止为 network」（UX 0.17 A04 最接近的
  // 一句为「没有未保存的修改时直接切换」）：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, { images: IMAGES }));
    controller.abort();
    await expect(reorderAdminProductImages(7, [12, 11], "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("removing an image", () => {
  // 接口记录「DELETE /api/admin/products/{product_id}/images/{image_id}」「require_admin（401）、CSRF（403）、商品 ID 与图片 ID（不合法 422）」：
  // 两个路径段为商品 ID 与图片 ID 的十进制、不带查询参数，DELETE、凭同源 cookie、不缓存，Accept 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal；
  // 没有请求体，也不带 Content-Type。
  it.each<[number, number, string]>([
    [7, 12, "/api/admin/products/7/images/12"],
    [2147483647, 2147483647, "/api/admin/products/2147483647/images/2147483647"],
  ])("deletes image %i/%i without a body", async (productId, imageId, url) => {
    const fetchMock = stubFetch(json(200, { images: [IMAGE_1] }));
    const controller = new AbortController();
    await removeAdminProductImage(productId, imageId, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(url);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "DELETE", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // UX 0.17 A04「成功后图片区改显示服务端返回的图片列表」与接口记录「成功 200 与 images」：结果为服务端返回的列表，多出的字段不带进结果；
  // 移除最后一张后的空列表合格。
  it.each<[string, unknown, unknown[]]>([
    ["the remaining images", { images: [{ ...IMAGE_2, cover: true }], removed: 11 }, [IMAGE_2]],
    ["an empty list", { images: [] }, []],
  ])("returns the images from the reply: %s", async (_name, reply, images) => {
    stubFetch(json(200, reply));
    await expect(removeAdminProductImage(7, 11, "csrf-abc")).resolves.toEqual({ kind: "ok", images });
  });

  // 各例守住的接口记录、HANDOFF 0.82 原句见 MALFORMED_IMAGES 各组的注释；接口记录「成功 200 与 images」：不是 JSON 或回 204 也算 failed。
  it.each<[string, Response]>([
    ...MALFORMED_IMAGES.map(([name, body]): [string, Response] => [name, json(200, body)]),
    ["not JSON", new Response("", { status: 200 })],
    ["204 instead of 200", new Response(null, { status: 204 })],
  ])("treats a malformed reply as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(removeAdminProductImage(7, 11, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「require_admin（401）、CSRF（403）、商品 ID 与图片 ID（不合法 422）；remove_image：ProductMissing 404、ImagesChanged 409 images_changed；
  // 存储未配置时照常移除」：只有 detail 恰为 images_changed 的 409 为 changed；其他 409、422、404、503 与其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(409, { detail: "images_changed" }), "changed"],
    [json(409, { detail: "images_full" }), "failed"],
    [json(409, { detail: "Images_Changed" }), "failed"],
    [json(409, {}), "failed"],
    [new Response("images_changed", { status: 409 }), "failed"],
    [json(422, { detail: [{ loc: ["path", "image_id"], type: "image_id_invalid", msg: "Image ID should be a positive integer" }] }), "failed"],
    [json(404, { detail: "product_not_found" }), "failed"],
    [json(503, { detail: "image_store_unavailable" }), "failed"],
    [json(500, { detail: "images_changed" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(removeAdminProductImage(7, 11, "csrf-abc")).resolves.toEqual({ kind });
  });

  // 接口记录、DESIGN 1.19 第 13 条与 UX 0.17 A04 都没有关于中止的原句；守住的是本任务验收「网络中断与中止为 network」（UX 0.17 A04 最接近的
  // 一句为「没有未保存的修改时直接切换」）：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, { images: [] }));
    controller.abort();
    await expect(removeAdminProductImage(7, 11, "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

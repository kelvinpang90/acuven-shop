import { afterEach, describe, expect, it, vi } from "vitest";

import { adminPromotionPosterUrl, POSTER_SLOTS, type PosterSlot, uploadPromotionPoster } from "./adminPromotionPoster";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 海报图的提供路径（app/api/admin_promotion_poster.py：「成功 200 与 poster（新的提供路径 /api/promotion-posters/<文件名>.webp）」）。
const POSTER = "/api/promotion-posters/0123456789abcdef0123456789abcdef.webp";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不是海报接口时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || !/^\/api\/admin\/promotions\/[0-9]+\/poster\/(desktop|phone)$/.test(String(input))) {
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

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

// 一个文件：Blob 自带的类型与调用方给的 Content-Type 故意不同，用来确认请求头取的是调用方给的。
function posterFile(): Blob {
  return new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: "application/octet-stream" });
}

function upload(signal?: AbortSignal) {
  return uploadPromotionPoster(7, "desktop", posterFile(), "image/jpeg", "csrf-abc", signal);
}

// 上传成功时不合格的响应体。
const MALFORMED_UPLOADS: [string, unknown][] = [
  // 接口文档「成功 200 与 poster（新的提供路径 /api/promotion-posters/<文件名>.webp）」：poster 必为路径，缺少、为 null（移除才是 null）都不合格；
  // 整体不是对象也不合格。
  ["poster missing", {}],
  ["poster null", { poster: null }],
  ["body an array", [POSTER]],
  ["body null", null],
  ["body a string", POSTER],
  // 落实写法「ok 的 poster 须为以 /api/promotion-posters/ 开头、以 .webp 结尾的字符串（照标志模块对提供路径的检查）」：其他前缀与扩展名、
  // 文件名不是 32 位小写十六进制（服务层 secrets.token_hex(16)）、非字符串与空串都不合格。
  ["poster with the logo prefix", { poster: "/api/store-logos/0123456789abcdef0123456789abcdef.webp" }],
  ["poster with another extension", { poster: "/api/promotion-posters/0123456789abcdef0123456789abcdef.png" }],
  ["poster in upper case", { poster: "/api/promotion-posters/0123456789ABCDEF0123456789ABCDEF.webp" }],
  ["poster with 31 digits", { poster: "/api/promotion-posters/0123456789abcdef0123456789abcde.webp" }],
  ["poster with a nested path", { poster: "/api/promotion-posters/../0123456789abcdef0123456789abcdef.webp" }],
  ["poster a number", { poster: 1 }],
  ["poster empty", { poster: "" }],
];

describe("uploading a promotion poster", () => {
  // 接口文档「PUT /api/admin/promotions/{promotion_id}/poster/{slot}」「2. X-CSRF-Token（403 csrf_failed）」与「全部响应（含错误）都带
  // Cache-Control: no-store」：地址带活动 ID 与位置、不带查询参数，PUT、凭同源 cookie、不缓存，Accept 为 JSON，X-CSRF-Token 为调用方给的令牌，
  // 并带上调用方的 signal。
  it.each([...POSTER_SLOTS])("puts to the %s poster endpoint with the CSRF token", async (slot) => {
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    const controller = new AbortController();
    await uploadPromotionPoster(42, slot, posterFile(), "image/png", "csrf-abc", controller.signal);
    expect(adminPromotionPosterUrl(42, slot)).toBe(`/api/admin/promotions/42/poster/${slot}`);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe(`/api/admin/promotions/42/poster/${slot}`);
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // 落实写法「请求体为文件原始字节，Content-Type 为所给类型」与接口文档「6. Content-Type 不是 image/jpeg、image/png、image/webp 之一即 415」：
  // 请求体就是调用方给的那个 Blob，不是 FormData；Content-Type 为调用方给的类型字符串，不取 Blob 自带的类型。
  it.each(["image/jpeg", "image/png", "image/webp"])("sends the file itself with the given type %s", async (contentType) => {
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    const file = posterFile();
    await uploadPromotionPoster(7, "phone", file, contentType, "csrf-abc");
    const init = sentInit(fetchMock, 0);
    expect(init.body).toBe(file);
    expect(init.body instanceof FormData).toBe(false);
    expect(new Headers(init.headers).get("Content-Type")).toBe(contentType);
  });

  // UX 0.20 A12「上传即替换该位置的图」与接口文档「成功 200 与 poster」：结果带回服务端给的路径，多出的字段不带进结果。
  it("returns the poster from the reply only", async () => {
    stubFetch(json(200, { poster: POSTER, width: 1800, audit: [] }));
    await expect(upload()).resolves.toEqual({ kind: "ok", poster: POSTER });
  });

  // 各例守住的接口文档与落实写法原句见 MALFORMED_UPLOADS 各组的注释：poster 不合格，整个上传归为 failed。
  it.each(MALFORMED_UPLOADS)("treats a malformed reply as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(upload()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「成功 200 与 poster」：200 的响应体不是 JSON 读不出 poster，算 failed；回 201 也不是上传成功。
  it.each<[string, Response]>([
    ["not JSON", new Response("<html>", { status: 200 })],
    ["201 instead of 200", json(201, { poster: POSTER })],
  ])("treats an unexpected success as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(upload()).resolves.toEqual({ kind: "failed" });
  });

  // 接口文档「1. require_admin（401 admin_session_required）」「2. X-CSRF-Token（403 csrf_failed）」「4. ……不存在 404 promotion_not_found，
  // ended 409 promotion_ended」「6. ……即 415 image_type_unsupported」「8. ……抛 ImageRejected 即 422 poster_invalid」「5. 存储未配置即 503
  // image_store_unavailable」「3. ……不合法 422；slot 不是 desktop 或 phone 也是 422」：只有 detail 恰为对应键时归为对应的拒绝；detail 对调、
  // 大小写不同、FastAPI 的错误列表（含 slot 与 ID 的 422）、空对象、不是 JSON、503 与其他状态都归 failed。404 不看响应体。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(404, { detail: "promotion_not_found" }), "missing"],
    [json(404, { detail: "Not Found" }), "missing"],
    [json(409, { detail: "promotion_ended" }), "ended"],
    [json(415, { detail: "image_type_unsupported" }), "type_unsupported"],
    [json(422, { detail: "poster_invalid" }), "invalid"],
    [json(409, { detail: "poster_required" }), "failed"],
    [json(409, { detail: "PROMOTION_ENDED" }), "failed"],
    [json(409, {}), "failed"],
    [new Response("promotion_ended", { status: 409 }), "failed"],
    [json(415, { detail: "poster_invalid" }), "failed"],
    [json(415, {}), "failed"],
    [new Response("image_type_unsupported", { status: 415 }), "failed"],
    [json(422, { detail: "logo_invalid" }), "failed"],
    [json(422, { detail: "Poster_Invalid" }), "failed"],
    [json(422, { detail: [{ loc: ["path", "slot"], type: "slot_invalid", msg: "Slot should be desktop or phone" }] }), "failed"],
    [json(422, { detail: [{ loc: ["path", "promotion_id"], type: "promotion_id_invalid", msg: "Promotion ID should be a positive integer" }] }), "failed"],
    [json(422, {}), "failed"],
    [new Response("poster_invalid", { status: 422 }), "failed"],
    [json(503, { detail: "image_store_unavailable" }), "failed"],
    [json(405, { detail: "Method Not Allowed" }), "failed"],
    [json(500, { detail: "poster_invalid" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(upload()).resolves.toEqual({ kind });
  });

  // 接口文档「7. 请求头声明的长度超过 5,242,880 字节即 413 image_too_large」与落实写法「too_large（413）」（照标志模块：反向代理回答的 413
  // 不是 JSON）：接口的 413、反向代理的 HTML、空的与其他 detail 的 413 都为 too_large，且不读响应体。
  it.each<[string, Response]>([
    ["from the API", json(413, { detail: "image_too_large" })],
    ["from the reverse proxy", new Response("<html><head><title>413 Request Entity Too Large</title></head></html>", { status: 413, headers: { "Content-Type": "text/html" } })],
    ["with an empty body", new Response(null, { status: 413 })],
    ["with another detail", json(413, { detail: "request body too large" })],
  ])("treats 413 as too large without reading the body: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(upload()).resolves.toEqual({ kind: "too_large" });
    expect(reply.bodyUsed).toBe(false);
  });

  // 落实写法「slot 只能是 desktop 或 phone，不是时不发请求、为 failed」：大小写不同、其他位置与空串都不发请求。
  it.each(["Desktop", "mobile", "tablet", "", "desktop/../phone"])("does not send a request for slot %j", async (slot) => {
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    await expect(uploadPromotionPoster(7, slot as PosterSlot, posterFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "failed" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // 落实写法「活动 ID 不是正整数时不发请求、为 missing」与接口文档「3. 路径里的活动 ID 照 _category_id 校验，不合法 422」（上限 2³¹−1，
  // MAX_PROMOTION_ID）：0、负数、小数、超上限、NaN 与 Infinity 都不发请求。
  it.each([0, -1, 1.5, 2147483648, Number.NaN, Number.POSITIVE_INFINITY])("does not send a request for promotion ID %s", async (promotionId) => {
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    await expect(uploadPromotionPoster(promotionId, "desktop", posterFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "missing" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // 同一句的边界：上限 2147483647 本身合法，照发请求。
  it("sends a request for the largest promotion ID", async () => {
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    await expect(uploadPromotionPoster(2147483647, "phone", posterFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "ok", poster: POSTER });
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/promotions/2147483647/poster/phone");
  });

  // 接口文档与 UX 0.20 A12 都没有关于中止的原句；守住的是本任务结果一节「network」与验收「调用方中止为 network」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, { poster: POSTER }));
    controller.abort();
    await expect(upload(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

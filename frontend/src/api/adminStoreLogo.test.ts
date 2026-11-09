import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_STORE_LOGO_URL, removeStoreLogo, uploadStoreLogo } from "./adminStoreLogo";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// 标志的提供路径（app/api/admin_store_logo.py 的 LogoOut：「标志的提供路径 /api/store-logos/<文件名>.webp」）。
const LOGO = "/api/store-logos/0123456789abcdef0123456789abcdef.webp";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不是标志接口时拒绝。
// 参数带类型（地址只收字符串或 URL，String(…) 即得地址），请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || String(input) !== "/api/admin/store-design/logo") {
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
function logoFile(): Blob {
  return new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "application/octet-stream" });
}

// 上传成功时不合格的响应体。
const MALFORMED_UPLOADS: [string, unknown][] = [
  // 接口记录「成功 200 与 logo（新的提供路径）」：上传成功的 logo 必为路径，缺少、为 null 都不合格；整体不是对象也不合格。
  ["logo missing", {}],
  ["logo null", { logo: null }],
  ["body an array", [LOGO]],
  ["body null", null],
  ["body a string", LOGO],
  // HANDOFF 0.84「logo 只认 /api/store-logos/ 加 32 位小写十六进制加 .webp 的字符串或 null」：大写、31 位、其他前缀与扩展名、非字符串都不合格。
  ["logo in upper case", { logo: "/api/store-logos/0123456789ABCDEF0123456789ABCDEF.webp" }],
  ["logo with 31 digits", { logo: "/api/store-logos/0123456789abcdef0123456789abcde.webp" }],
  ["logo with another prefix", { logo: "/api/product-images/0123456789abcdef0123456789abcdef.webp" }],
  ["logo with another extension", { logo: "/api/store-logos/0123456789abcdef0123456789abcdef.png" }],
  ["logo a number", { logo: 1 }],
  ["logo empty", { logo: "" }],
];

describe("uploading the store logo", () => {
  // 接口记录「PUT /api/admin/store-design/logo」「2. X-CSRF-Token（403 csrf_failed）」与「两个接口的……全部响应（含错误）都带 Cache-Control: no-store」：
  // 地址为固定路径、不带查询参数，PUT、凭同源 cookie、不缓存，Accept 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal。
  it("puts to the logo endpoint with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, { logo: LOGO }));
    const controller = new AbortController();
    await uploadStoreLogo(logoFile(), "image/png", "csrf-abc", controller.signal);
    expect(ADMIN_STORE_LOGO_URL).toBe("/api/admin/store-design/logo");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/store-design/logo");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // HANDOFF 0.84「上传的请求体为文件本身（不用多部分表单），Content-Type 为调用方给的类型」与接口记录「4. Content-Type……不是 image/jpeg、
  // image/png、image/webp 之一即 415」：请求体就是调用方给的那个 Blob，不是 FormData；Content-Type 为调用方给的类型字符串，不取 Blob 自带的类型。
  it.each(["image/jpeg", "image/png", "image/webp"])("sends the file itself with the given type %s", async (contentType) => {
    const fetchMock = stubFetch(json(200, { logo: LOGO }));
    const file = logoFile();
    await uploadStoreLogo(file, contentType, "csrf-abc");
    const init = sentInit(fetchMock, 0);
    expect(init.body).toBe(file);
    expect(init.body instanceof FormData).toBe(false);
    expect(new Headers(init.headers).get("Content-Type")).toBe(contentType);
  });

  // UX 0.18 A08「上传即替换……立即生效」与接口记录「成功 200 与 logo（新的提供路径）」：结果带回服务端给的路径，多出的字段不带进结果。
  it("returns the logo from the reply only", async () => {
    stubFetch(json(200, { logo: LOGO, width: 512, audit: [] }));
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "ok", logo: LOGO });
  });

  // 各例守住的接口记录、HANDOFF 0.84 原句见 MALFORMED_UPLOADS 各组的注释：logo 不合格，整个上传归为 failed。
  it.each(MALFORMED_UPLOADS)("treats a malformed reply as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // HANDOFF 0.84「两个接口成功都是 200」：200 的响应体不是 JSON 读不出 logo，算 failed；回 201 也不是上传成功。
  it.each<[string, Response]>([
    ["not JSON", new Response("<html>", { status: 200 })],
    ["201 instead of 200", json(201, { logo: LOGO })],
  ])("treats an unexpected success as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「1. require_admin（401 admin_session_required）」「2. X-CSRF-Token（403 csrf_failed）」「3. 存储未配置即 503 image_store_unavailable」
  // 「4. ……即 415 image_type_unsupported」「6. ……抛 ImageRejected 即 422 logo_invalid」，UX 0.18 A08「被拒时按服务端的回答显示」：只有 detail
  // 恰为对应键时归为对应的拒绝；detail 对调、大小写不同、错误列表、空对象、不是 JSON、503 与其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(415, { detail: "image_type_unsupported" }), "type_unsupported"],
    [json(422, { detail: "logo_invalid" }), "invalid"],
    [json(415, { detail: "logo_invalid" }), "failed"],
    [json(415, { detail: "IMAGE_TYPE_UNSUPPORTED" }), "failed"],
    [json(415, {}), "failed"],
    [new Response("image_type_unsupported", { status: 415 }), "failed"],
    [json(422, { detail: "image_invalid" }), "failed"],
    [json(422, { detail: "Logo_Invalid" }), "failed"],
    [json(422, { detail: [{ loc: ["body"], type: "missing", msg: "Field required" }] }), "failed"],
    [json(422, {}), "failed"],
    [new Response("logo_invalid", { status: 422 }), "failed"],
    [json(503, { detail: "image_store_unavailable" }), "failed"],
    [json(404, { detail: "Not Found" }), "failed"],
    [json(409, { detail: "logo_invalid" }), "failed"],
    [json(500, { detail: "logo_invalid" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind });
  });

  // HANDOFF 0.84「413 一律归为文件过大，不看响应体（反向代理回答的 413 不是 JSON）」与接口记录「5. 请求头声明的长度超过 5,242,880 字节即
  // 413 image_too_large」：接口的 413、反向代理的 HTML、空的与其他 detail 的 413 都为 too_large，且不读响应体。
  it.each<[string, Response]>([
    ["from the API", json(413, { detail: "image_too_large" })],
    ["from the reverse proxy", new Response("<html><head><title>413 Request Entity Too Large</title></head></html>", { status: 413, headers: { "Content-Type": "text/html" } })],
    ["with an empty body", new Response(null, { status: 413 })],
    ["with another detail", json(413, { detail: "request body too large" })],
  ])("treats 413 as too large without reading the body: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc")).resolves.toEqual({ kind: "too_large" });
    expect(reply.bodyUsed).toBe(false);
  });

  // 接口记录、DESIGN 1.20 第 15 条与 UX 0.18 A08 都没有关于中止的原句；守住的是本任务验收「网络中断与调用方中止为 network」：
  // 中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, { logo: LOGO }));
    controller.abort();
    await expect(uploadStoreLogo(logoFile(), "image/png", "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("removing the store logo", () => {
  // 接口记录「DELETE /api/admin/store-design/logo」「require_admin（401）、CSRF（403），不读请求体」：地址为固定路径、不带查询参数，DELETE、
  // 凭同源 cookie、不缓存，Accept 为 JSON，X-CSRF-Token 为调用方给的令牌，并带上调用方的 signal；没有请求体，也不带 Content-Type。
  it("deletes the logo without a body", async () => {
    const fetchMock = stubFetch(json(200, { logo: null }));
    const controller = new AbortController();
    await removeStoreLogo("csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/store-design/logo");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "DELETE", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(init.signal).toBe(controller.signal);
  });

  // 接口记录「成功 200 与 logo 为 null」与 UX 0.18 A08「移除即恢复文字品牌」：200 且 logo 为 null 为 ok，多出的字段不带进结果。
  it.each<[string, unknown]>([
    ["only the logo", { logo: null }],
    ["with extra fields", { logo: null, removed: true }],
  ])("accepts a cleared logo: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(removeStoreLogo("csrf-abc")).resolves.toEqual({ kind: "ok" });
  });

  // 同一句「成功 200 与 logo 为 null」：200 但 logo 不为 null（含合格的路径）、缺少、整体不是对象或不是 JSON 都为 failed；回 204 也不是移除成功。
  it.each<[string, Response]>([
    ["a logo path", json(200, { logo: LOGO })],
    ["an empty logo", json(200, { logo: "" })],
    ["logo missing", json(200, {})],
    ["body null", json(200, null)],
    ["body an array", json(200, [null])],
    ["not JSON", new Response("", { status: 200 })],
    ["204 instead of 200", new Response(null, { status: 204 })],
  ])("treats a malformed reply as a failure: %s", async (_name, reply) => {
    stubFetch(reply);
    await expect(removeStoreLogo("csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 接口记录「移除（DELETE）：require_admin（401）、CSRF（403）……存储未配置时照常清空」：401 为 none、403 为 csrf，其他状态都归 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(404, { detail: "Not Found" }), "failed"],
    [json(413, { detail: "image_too_large" }), "failed"],
    [json(415, { detail: "image_type_unsupported" }), "failed"],
    [json(422, { detail: "logo_invalid" }), "failed"],
    [json(503, { detail: "image_store_unavailable" }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(removeStoreLogo("csrf-abc")).resolves.toEqual({ kind });
  });

  // 接口记录、DESIGN 1.20 第 15 条与 UX 0.18 A08 都没有关于中止的原句；守住的是本任务验收「网络中断与中止为 network」：
  // 中止后 fetch 抛错，归为 network。
  it("treats an aborted request as a network failure", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, { logo: null }));
    controller.abort();
    await expect(removeStoreLogo("csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

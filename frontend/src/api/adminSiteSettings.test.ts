import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_SITE_SETTINGS_URL, PAYMENT_METHODS, readAdminSiteSettings, saveAdminSiteSettings } from "./adminSiteSettings";
import type { PaymentMethod } from "./adminSiteSettings";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// SHOP-TASK-169 改动：读取的响应体多了 sms_verification_enabled 与 sms_available（SHOP-TASK-168），夹具随之补上。
const SETTINGS = { payment_method: "simulated", stripe_available: true, csrf_token: "csrf-abc", sms_verification_enabled: false, sms_available: true };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// fetch 替身：按顺序给出回答（函数则抛出网络错误）；signal 已中止时与浏览器一样抛 AbortError，地址不是本接口时拒绝。
// 请求从替身的 mock.calls 取。
function stubFetch(...replies: (Response | (() => never))[]) {
  const queue = [...replies];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.signal?.aborted === true) {
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    }
    const next = queue.shift();
    if (next === undefined || urlOf(input) !== ADMIN_SITE_SETTINGS_URL) {
      return Promise.reject(new Error("unexpected request"));
    }
    return typeof next === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(next);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubFetch>;

// 替身收到的地址；Request 没有自己的 toString，按类型取出字符串。
function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function sentUrl(fetchMock: FetchMock, index: number): string | undefined {
  const input = fetchMock.mock.calls[index]?.[0];
  return input === undefined ? undefined : urlOf(input);
}

function sentInit(fetchMock: FetchMock, index: number): RequestInit {
  return fetchMock.mock.calls[index]?.[1] ?? {};
}

// 请求体的原文；不是字符串时为空串（JSON.parse 随即失败，测试不通过）。
function sentText(fetchMock: FetchMock, index: number): string {
  const body = sentInit(fetchMock, index).body;
  return typeof body === "string" ? body : "";
}

function sentBody(fetchMock: FetchMock, index: number): unknown {
  const body: unknown = JSON.parse(sentText(fetchMock, index));
  return body;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

describe("reading the site settings", () => {
  // SHOP-TASK-138 验收第 1 条「调用 GET /api/admin/site-settings（同源 cookie，cache 为 no-store；都不带查询参数）」：
  // 地址为固定路径、GET、凭同源 cookie、不缓存、没有请求体与 CSRF 头，并带上调用方的 signal。
  it("gets the settings with the same-origin cookie and no cache", async () => {
    const fetchMock = stubFetch(json(200, SETTINGS));
    const controller = new AbortController();
    await readAdminSiteSettings(controller.signal);
    expect(ADMIN_SITE_SETTINGS_URL).toBe("/api/admin/site-settings");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/site-settings");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
    expect(init.body).toBeUndefined();
    expect(new Headers(init.headers).has("X-CSRF-Token")).toBe(false);
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-122 记录「GET：返回 payment_method（simulated 或 stripe_test）、stripe_available 与 csrf_token」、SHOP-TASK-168 记录「在
  // payment_method、stripe_available、csrf_token 之后加 sms_verification_enabled……与 sms_available」与 SHOP-TASK-138 验收第 2 条「多出的字段
  // 不带进结果」：两种取值、两种凭据状态、短信开关的开与关、短信服务可用与否都合格，多出的字段不进结果。
  // SHOP-TASK-169 改动：原先把 sms_verification_enabled 当作多出的字段，现在它是要带进结果的字段；各组设置补上两个短信字段，多出的字段改为别的名字。
  it.each([
    { payment_method: "simulated", stripe_available: false, csrf_token: "t1", sms_verification_enabled: false, sms_available: false },
    { payment_method: "stripe_test", stripe_available: true, csrf_token: "t2", sms_verification_enabled: true, sms_available: true },
    { payment_method: "simulated", stripe_available: true, csrf_token: "t3", sms_verification_enabled: true, sms_available: false },
    { payment_method: "simulated", stripe_available: true, csrf_token: "t4", sms_verification_enabled: false, sms_available: true },
  ])("keeps only the agreed fields (%o)", async (settings) => {
    stubFetch(json(200, { ...settings, sms_provider: "twilio", updated_at: "2026-10-22" }));
    await expect(readAdminSiteSettings()).resolves.toEqual({ kind: "ok", settings });
  });

  // SHOP-TASK-138 验收第 3 条「读取结果归为 ok、none（401）、failed（其他）与 network」：401 为 none，其他状态为 failed，
  // fetch 抛错为 network。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "failed"],
    [json(404, {}), "failed"],
    [json(405, {}), "failed"],
    [json(422, { detail: "stripe_not_configured" }), "failed"],
    [json(500, {}), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminSiteSettings()).resolves.toEqual({ kind });
  });

  // SHOP-TASK-138 验收第 3 条「调用方可传入 AbortSignal，中止时按 adminStoreDesign.ts 的做法处理」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request like adminStoreDesign.ts", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, SETTINGS));
    controller.abort();
    await expect(readAdminSiteSettings(controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // SHOP-TASK-138 验收第 2 条「读取的 payment_method 不是 simulated 或 stripe_test，stripe_available 不是布尔值，csrf_token 不是
  // 非空字符串……字段缺少或类型不对都算不合格」，含 SHOP-TASK-122 记录「大小写不同一律 422」对应的大写值。
  it.each<[string, unknown]>([
    ["not an object", [SETTINGS]],
    ["null body", null],
    ["payment method missing", { ...SETTINGS, payment_method: undefined }],
    ["payment method upper case", { ...SETTINGS, payment_method: "STRIPE_TEST" }],
    ["payment method capitalised", { ...SETTINGS, payment_method: "Simulated" }],
    ["payment method with a space", { ...SETTINGS, payment_method: " simulated" }],
    ["payment method unknown", { ...SETTINGS, payment_method: "stripe" }],
    ["payment method empty", { ...SETTINGS, payment_method: "" }],
    ["payment method null", { ...SETTINGS, payment_method: null }],
    ["payment method as number", { ...SETTINGS, payment_method: 1 }],
    ["stripe available missing", { ...SETTINGS, stripe_available: undefined }],
    ["stripe available as string", { ...SETTINGS, stripe_available: "true" }],
    ["stripe available as number", { ...SETTINGS, stripe_available: 1 }],
    ["stripe available null", { ...SETTINGS, stripe_available: null }],
    ["csrf token missing", { ...SETTINGS, csrf_token: undefined }],
    ["csrf token empty", { ...SETTINGS, csrf_token: "" }],
    ["csrf token null", { ...SETTINGS, csrf_token: null }],
    ["csrf token as number", { ...SETTINGS, csrf_token: 123 }],
    // SHOP-TASK-169 验收第 2 条「读取的响应体另要求 sms_verification_enabled 与 sms_available 为布尔值（缺少或类型不对算不合格）」。
    ["sms verification enabled missing", { ...SETTINGS, sms_verification_enabled: undefined }],
    ["sms verification enabled as string", { ...SETTINGS, sms_verification_enabled: "false" }],
    ["sms verification enabled as number", { ...SETTINGS, sms_verification_enabled: 0 }],
    ["sms verification enabled null", { ...SETTINGS, sms_verification_enabled: null }],
    ["sms available missing", { ...SETTINGS, sms_available: undefined }],
    ["sms available as string", { ...SETTINGS, sms_available: "true" }],
    ["sms available as number", { ...SETTINGS, sms_available: 1 }],
    ["sms available null", { ...SETTINGS, sms_available: null }],
  ])("treats malformed settings as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(readAdminSiteSettings()).resolves.toEqual({ kind: "failed" });
  });

  // 同一条「响应体不合格算失败」：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("<html>", { status: 200 }));
    await expect(readAdminSiteSettings()).resolves.toEqual({ kind: "failed" });
  });
});

// 保存成功的响应体（SHOP-TASK-168 记录「成功回 200 与保存后的 payment_method 和 sms_verification_enabled」）。
const SAVED = { payment_method: "simulated", sms_verification_enabled: false };

describe("saving the payment method", () => {
  // SHOP-TASK-138 验收第 1 条「PUT /api/admin/site-settings（同源 cookie，cache 为 no-store；不带查询参数；PUT 带 X-CSRF-Token 为调用方
  // 给出的 csrf_token，Content-Type 为 application/json）」，并带上调用方的 signal。
  // SHOP-TASK-169 改动：saveAdminSiteSettings 多了短信开关参数，调用与 200 的回答随之补上。
  it("puts the payment method with the CSRF token", async () => {
    const fetchMock = stubFetch(json(200, { payment_method: "stripe_test", sms_verification_enabled: true }));
    const controller = new AbortController();
    await saveAdminSiteSettings("stripe_test", true, "csrf-abc", controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentUrl(fetchMock, 0)).toBe("/api/admin/site-settings");
    const init = sentInit(fetchMock, 0);
    expect(init).toMatchObject({ method: "PUT", credentials: "same-origin", cache: "no-store" });
    const headers = new Headers(init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-abc");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.signal).toBe(controller.signal);
  });

  // SHOP-TASK-169 验收第 2 条「saveAdminSiteSettings 改为同时提交支付方式与短信开关（JSON 请求体只有 payment_method 与
  // sms_verification_enabled 两项）」与 HANDOFF 0.65 / UX A09「保存一次提交两项」：两种支付方式与短信开关的开与关各发一次，请求体恰为这两项。
  // SHOP-TASK-169 改动：原先断言请求体只有 payment_method、没有任何短信字段（SHOP-TASK-138 当时的约定），随两项改写。
  it.each<[PaymentMethod, boolean]>(PAYMENT_METHODS.flatMap((method) => [true, false].map((enabled): [PaymentMethod, boolean] => [method, enabled])))(
    "sends only payment_method and sms_verification_enabled for %s, %s",
    async (method, enabled) => {
      const fetchMock = stubFetch(json(200, { payment_method: method, sms_verification_enabled: enabled }));
      await saveAdminSiteSettings(method, enabled, "csrf-abc");
      const body = sentBody(fetchMock, 0);
      expect(body).toEqual({ payment_method: method, sms_verification_enabled: enabled });
      expect(Object.keys(body as object)).toEqual(["payment_method", "sms_verification_enabled"]);
    },
  );

  // SHOP-TASK-122 记录「成功或与当前值相同都回 200」与 SHOP-TASK-168 记录「成功回 200 与保存后的 payment_method 和 sms_verification_enabled」、
  // SHOP-TASK-169 验收第 2 条「200 的响应体另要求 sms_verification_enabled 为布尔值并带进 ok 结果」：结果带保存后的两项（以响应为准），
  // 多出的字段不带进结果。
  // SHOP-TASK-169 改动：原先断言多出的 sms_verification_enabled 不带进结果，现在它是要带进结果的字段，改为断言两项都在、其余不在。
  it("returns the saved payment method and SMS switch only", async () => {
    stubFetch(json(200, { payment_method: "stripe_test", sms_verification_enabled: true, stripe_available: true, sms_available: true, csrf_token: "x" }));
    await expect(saveAdminSiteSettings("simulated", false, "csrf-abc")).resolves.toEqual({
      kind: "ok",
      payment_method: "stripe_test",
      sms_verification_enabled: true,
    });
  });

  // SHOP-TASK-138 验收第 3 条「保存结果归为 ok、none（401）、csrf（403）、not_configured（422 且 detail 为 stripe_not_configured）、
  // failed（其他，含其他 422）与 network」：请求体不合格的 422 错误列表与其他 detail 的 422 都归 failed。
  // SHOP-TASK-169 验收第 2 条「422 且 detail 为 sms_not_configured 时归为新增的 sms_not_configured，detail 为 stripe_not_configured 仍归
  // not_configured，其他 422 仍为 failed」：大小写不同与其他状态码带同样的 detail 仍为 failed。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(422, { detail: "stripe_not_configured" }), "not_configured"],
    [json(422, { detail: "sms_not_configured" }), "sms_not_configured"],
    [json(422, { detail: "SMS_NOT_CONFIGURED" }), "failed"],
    [new Response("sms_not_configured", { status: 422 }), "failed"],
    [json(500, { detail: "sms_not_configured" }), "failed"],
    [json(422, { detail: "STRIPE_NOT_CONFIGURED" }), "failed"],
    [json(422, { detail: "payment_method_invalid" }), "failed"],
    [json(422, { detail: [{ loc: ["body", "payment_method"], type: "literal_error", msg: "invalid" }] }), "failed"],
    [json(422, {}), "failed"],
    [new Response("stripe_not_configured", { status: 422 }), "failed"],
    [json(413, {}), "failed"],
    [json(415, {}), "failed"],
    [json(405, {}), "failed"],
    [json(500, { detail: "stripe_not_configured" }), "failed"],
    [networkDown, "network"],
  ])("sorts the reply into a kind (%#)", async (reply, kind) => {
    stubFetch(reply);
    await expect(saveAdminSiteSettings("stripe_test", true, "csrf-abc")).resolves.toEqual({ kind });
  });

  // SHOP-TASK-138 验收第 3 条「调用方可传入 AbortSignal，中止时按 adminStoreDesign.ts 的做法处理」：中止后 fetch 抛错，归为 network。
  it("treats an aborted request like adminStoreDesign.ts", async () => {
    const controller = new AbortController();
    const fetchMock = stubFetch(json(200, SAVED));
    controller.abort();
    await expect(saveAdminSiteSettings("simulated", false, "csrf-abc", controller.signal)).resolves.toEqual({ kind: "network" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // SHOP-TASK-138 验收第 2 条「保存的 payment_method 不是这两个值之一……字段缺少或类型不对都算不合格」（含大写值）。
  // SHOP-TASK-169 改动：支付方式不合格的各例补上合格的 sms_verification_enabled（...SAVED），使它们仍只因支付方式不合格而失败；
  // 另按 SHOP-TASK-169 验收第 2 条「200 的响应体另要求 sms_verification_enabled 为布尔值」加入短信字段缺少或类型不对的各例。
  it.each<[string, unknown]>([
    ["not an object", ["simulated"]],
    ["a bare string", "simulated"],
    ["null body", null],
    ["payment method missing", { ...SAVED, payment_method: undefined }],
    ["payment method upper case", { ...SAVED, payment_method: "SIMULATED" }],
    ["payment method capitalised", { ...SAVED, payment_method: "Stripe_test" }],
    ["payment method unknown", { ...SAVED, payment_method: "stripe_live" }],
    ["payment method empty", { ...SAVED, payment_method: "" }],
    ["payment method null", { ...SAVED, payment_method: null }],
    ["payment method as boolean", { ...SAVED, payment_method: true }],
    ["payment method only", { payment_method: "simulated" }],
    ["sms verification enabled as string", { ...SAVED, sms_verification_enabled: "false" }],
    ["sms verification enabled as number", { ...SAVED, sms_verification_enabled: 1 }],
    ["sms verification enabled null", { ...SAVED, sms_verification_enabled: null }],
  ])("treats a malformed saved value as a failure: %s", async (_name, body) => {
    stubFetch(json(200, body));
    await expect(saveAdminSiteSettings("simulated", false, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });

  // 同一条「响应体不合格算失败」：200 但响应体不是 JSON 也算 failed。
  it("treats a body that is not JSON as a failure", async () => {
    stubFetch(new Response("", { status: 200 }));
    await expect(saveAdminSiteSettings("simulated", false, "csrf-abc")).resolves.toEqual({ kind: "failed" });
  });
});

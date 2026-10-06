import { afterEach, describe, expect, it, vi } from "vitest";

import { ADMIN_LOGIN_URL, ADMIN_LOGOUT_URL, ADMIN_SESSION_URL, readAdminSession, submitAdminLogin, submitAdminLogout } from "./admin";
import { CSRF_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const USERNAME = "shop-admin";
const PASSWORD = "correct horse battery";
const TOKEN = "admin-csrf-1";
const SESSION = { username: USERNAME, expires_at: "2026-11-05T08:00:00Z", csrf_token: TOKEN };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function empty(status: number): Response {
  return new Response(null, { status });
}

interface Call {
  url: string;
  init: RequestInit;
}

// fetch 替身：给出一个回答（函数则抛出网络错误），记录请求。
function stubFetch(reply: Response | (() => never)) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: typeof input === "string" ? input : input instanceof URL ? input.href : input.url, init: init ?? {} });
      return typeof reply === "function" ? Promise.reject(new TypeError("Failed to fetch")) : Promise.resolve(reply);
    }),
  );
  return calls;
}

const networkDown = (): never => {
  throw new TypeError("Failed to fetch");
};

function headers(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}

function body(call: Call | undefined): unknown {
  return JSON.parse(call?.init.body as string);
}

describe("reading the admin session", () => {
  // SHOP-TASK-038 验收第 3 条「frontend/src/api/admin.ts 调用 GET /api/admin/session（响应字段按 SHOP-TASK-036：username、expires_at、csrf_token）」：
  // 地址固定，凭同源 cookie 读取、不缓存；只取这三个字段。
  it("reads username, expiry and CSRF token with the same-origin cookie", async () => {
    const calls = stubFetch(json(200, { ...SESSION, extra: "ignored" }));
    await expect(readAdminSession()).resolves.toEqual({ kind: "ok", session: SESSION });
    expect(calls[0]?.url).toBe(ADMIN_SESSION_URL);
    expect(ADMIN_SESSION_URL).toBe("/api/admin/session");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
  });

  // SHOP-TASK-038 验收第 3 条「200 显示已登录状态，401 显示登录表单，其他结果显示 common.error_retry 与登录表单」：
  // 401 为没有会话；其他状态、不合格的 200 响应体与网络中断分开报告（页面都按「其他结果」处理）。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "none"],
    [json(500, {}), "failed"],
    [json(503, { detail: "service_unavailable" }), "failed"],
    [empty(204), "failed"],
    [json(200, { username: USERNAME }), "failed"],
    [json(200, { ...SESSION, csrf_token: 1 }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminSession()).resolves.toEqual({ kind });
  });
});

describe("logging in", () => {
  // SHOP-TASK-038 验收第 4 条「提交调用 POST /api/admin/login（JSON 请求体只有用户名与密码）」与 HANDOFF Kelvin 2026-10-05「登录请求体 username、password」：
  // 请求体恰好两个字段、原样提交；地址固定，不含用户名或密码。
  it("posts only the username and password as JSON", async () => {
    const calls = stubFetch(empty(204));
    await expect(submitAdminLogin(` ${USERNAME} `, PASSWORD)).resolves.toBe("ok");
    expect(calls[0]?.url).toBe(ADMIN_LOGIN_URL);
    expect(ADMIN_LOGIN_URL).toBe("/api/admin/login");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headers(calls[0])["Content-Type"]).toBe("application/json");
    const sent = body(calls[0]);
    expect(sent).toEqual({ username: ` ${USERNAME} `, password: PASSWORD });
    expect(Object.keys(sent as object)).toEqual(["username", "password"]);
    expect(calls[0]?.url).not.toContain(USERNAME);
  });

  // SHOP-TASK-038 验收第 4 条「204 后再取会话…；401 显示 admin.login_failed，429 显示 admin.locked，503 显示 common.service_unavailable，
  // 网络中断显示 common.network_check，其他显示 common.error_retry」：每种回答各自分开报告；只有 204 算登录成功。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "login_failed" }), "rejected"],
    [json(429, { detail: "login_locked" }), "locked"],
    [json(503, { detail: "service_unavailable" }), "unavailable"],
    [networkDown, "network"],
    [json(500, {}), "failed"],
    [json(422, { detail: [] }), "failed"],
    [json(200, {}), "failed"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitAdminLogin(USERNAME, PASSWORD)).resolves.toBe(kind);
  });
});

describe("logging out", () => {
  // SHOP-TASK-038 验收第 5 条「退出调用 POST /api/admin/logout 并带会话接口返回的 X-CSRF-Token」：请求头带令牌，没有请求体，地址里没有令牌。
  it("posts with the CSRF token header and no body", async () => {
    const calls = stubFetch(empty(204));
    await expect(submitAdminLogout(TOKEN)).resolves.toBe("done");
    expect(calls[0]?.url).toBe(ADMIN_LOGOUT_URL);
    expect(ADMIN_LOGOUT_URL).toBe("/api/admin/logout");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // SHOP-TASK-038 验收第 5 条「204 或 401 后回到登录表单，403 时重新取会话…，网络中断显示 common.network_check，其他结果显示 common.error_retry」：
  // 204 与 401 都算已退出；403、其他与网络中断分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "done"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [json(500, {}), "failed"],
    [json(200, {}), "failed"],
    [networkDown, "network"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitAdminLogout(TOKEN)).resolves.toBe(kind);
  });
});

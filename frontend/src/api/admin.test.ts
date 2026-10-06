import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ADMIN_LOGIN_URL,
  ADMIN_LOGOUT_URL,
  ADMIN_SESSION_URL,
  readAdminSession,
  submitAdminLogin,
  submitAdminLogout,
} from "./admin";
import type { AdminSession } from "./admin";
import { CSRF_HEADER } from "./pay";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const USERNAME = "kelvin-admin";
const PASSWORD = "correct horse battery staple";
const TOKEN = "admin-csrf-token-1";
const SESSION: AdminSession = { username: USERNAME, expires_at: "2026-11-05T10:00:00Z", csrf_token: TOKEN };

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

// fetch 替身：按顺序给出回答（函数则抛出网络错误），记录每次请求。
function stubFetch(...replies: (Response | (() => never))[]) {
  const calls: Call[] = [];
  const queue = [...replies];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      calls.push({ url, init: init ?? {} });
      const next = queue.shift();
      if (next === undefined) {
        return Promise.reject(new Error("unexpected request"));
      }
      if (typeof next === "function") {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(next);
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

describe("reading the admin session", () => {
  // SHOP-TASK-038 验收第 3 条「frontend/src/api/admin.ts 调用 GET /api/admin/session（响应字段按 SHOP-TASK-036：username、expires_at、csrf_token）」：
  // 凭本浏览器 cookie（同源）读取、不缓存，200 时原样取三个字段。
  it("reads username, expiry and CSRF token on 200", async () => {
    const calls = stubFetch(json(200, SESSION));
    await expect(readAdminSession()).resolves.toEqual({ kind: "ok", session: SESSION });
    expect(calls[0]?.url).toBe(ADMIN_SESSION_URL);
    expect(ADMIN_SESSION_URL).toBe("/api/admin/session");
    expect(calls[0]?.init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
  });

  // SHOP-TASK-038 验收第 3 条「200 显示已登录状态，401 显示登录表单，其他结果显示 common.error_retry 与登录表单」：
  // 401 为未登录；网络中断、其他状态码与不合约定的响应体都归为其他结果。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "signed_out"],
    [json(500, {}), "failed"],
    [json(503, { detail: "service_unavailable" }), "failed"],
    [empty(204), "failed"],
    [json(200, { username: USERNAME }), "failed"],
    [new Response("not json", { status: 200 }), "failed"],
    [networkDown, "failed"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(readAdminSession()).resolves.toEqual({ kind });
  });

  // 派生实现约束（实现选择）：守住「其他结果显示 common.error_retry」——响应体多出的字段不带进页面内存。
  it("keeps only the three documented fields", async () => {
    stubFetch(json(200, { ...SESSION, extra: "x" }));
    await expect(readAdminSession()).resolves.toEqual({ kind: "ok", session: SESSION });
  });
});

describe("logging in", () => {
  // SHOP-TASK-038 验收第 4 条「提交调用 POST /api/admin/login（JSON 请求体只有用户名与密码）」与 Kelvin 2026-10-05 补进 SHOP-TASK-036 契约的字段名 username、password：
  // 请求体恰好两个字段、原样提交，地址里没有二者。
  it("posts only the username and password as JSON", async () => {
    const calls = stubFetch(empty(204));
    await expect(submitAdminLogin(USERNAME, PASSWORD)).resolves.toBe("ok");
    expect(calls[0]?.url).toBe(ADMIN_LOGIN_URL);
    expect(ADMIN_LOGIN_URL).toBe("/api/admin/login");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(headers(calls[0])["Content-Type"]).toBe("application/json");
    const sent: unknown = JSON.parse(calls[0]?.init.body as string);
    expect(sent).toEqual({ username: USERNAME, password: PASSWORD });
    expect(calls[0]?.url).not.toContain(USERNAME);
  });

  // SHOP-TASK-038 验收第 4 条「401 显示 admin.login_failed，429 显示 admin.locked，503 显示 common.service_unavailable，网络中断显示 common.network_check，其他显示 common.error_retry」：
  // 各种回答分开报告（页面按它选文案，见 AdminLoginPage.test.tsx）；只有 204 算成功。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "login_failed" }), "failed"],
    [json(429, { detail: "login_locked" }), "locked"],
    [json(503, { detail: "service_unavailable" }), "unavailable"],
    [networkDown, "network"],
    [json(422, { detail: [] }), "error"],
    [json(413, { detail: "request body too large" }), "error"],
    [json(500, {}), "error"],
    [json(200, {}), "error"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitAdminLogin(USERNAME, PASSWORD)).resolves.toBe(kind);
  });
});

describe("logging out", () => {
  // SHOP-TASK-038 验收第 5 条「退出调用 POST /api/admin/logout 并带会话接口返回的 X-CSRF-Token」：令牌只在请求头里，不进地址，也没有请求体。
  it("posts with the CSRF token in the header", async () => {
    const calls = stubFetch(empty(204));
    await expect(submitAdminLogout(TOKEN)).resolves.toBe("done");
    expect(calls[0]?.url).toBe(ADMIN_LOGOUT_URL);
    expect(ADMIN_LOGOUT_URL).toBe("/api/admin/logout");
    expect(calls[0]?.init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
    expect(CSRF_HEADER).toBe("X-CSRF-Token");
    expect(headers(calls[0])[CSRF_HEADER]).toBe(TOKEN);
    expect(calls[0]?.init.body).toBeUndefined();
    expect(calls[0]?.url).not.toContain(TOKEN);
  });

  // SHOP-TASK-038 验收第 5 条「204 或 401 后回到登录表单，403 时重新取会话…，网络中断显示 common.network_check，其他结果显示 common.error_retry」：各种回答分开报告。
  it.each<[Response | (() => never), string]>([
    [json(401, { detail: "admin_session_required" }), "signed_out"],
    [json(403, { detail: "csrf_failed" }), "csrf"],
    [networkDown, "network"],
    [json(500, {}), "failed"],
    [json(200, {}), "failed"],
  ])("reports the reply %#", async (reply, kind) => {
    stubFetch(reply);
    await expect(submitAdminLogout(TOKEN)).resolves.toBe(kind);
  });
});

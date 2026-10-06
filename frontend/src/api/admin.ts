import { CSRF_HEADER } from "./pay";

// 后台登录页 A01 的管理员接口（app/api/admin_auth.py，SHOP-TASK-036）：GET /api/admin/session、POST /api/admin/login、
// POST /api/admin/logout。后台会话凭服务端发的 HttpOnly cookie（页面脚本读不到也不写）。
// 用户名与密码只放在登录请求体里，CSRF 令牌只放在退出请求头里；三者都只在页面内存中，不进路径、查询参数、
// localStorage、sessionStorage 或 cookie。错误体为 {"detail": "<错误码>"}，页面只按状态码选文案，不显示错误体。

export const ADMIN_SESSION_URL = "/api/admin/session";
export const ADMIN_LOGIN_URL = "/api/admin/login";
export const ADMIN_LOGOUT_URL = "/api/admin/logout";

// 会话接口的响应：用户名、会话到期时间（带 Z 的 UTC）与 CSRF 令牌。
export interface AdminSession {
  username: string;
  expires_at: string;
  csrf_token: string;
}

// 读取会话的结果：ok 为 200；signed_out 为 401（没有有效会话）；failed 为其他（含网络中断与不合约定的响应体）。
export type SessionRead = { kind: "ok"; session: AdminSession } | { kind: "signed_out" } | { kind: "failed" };

function isSession(value: unknown): value is AdminSession {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const body = value as Record<string, unknown>;
  return typeof body.username === "string" && typeof body.expires_at === "string" && typeof body.csrf_token === "string";
}

// GET /api/admin/session：凭本浏览器的后台会话 cookie（同源）读取，不缓存。
export async function readAdminSession(signal?: AbortSignal): Promise<SessionRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_SESSION_URL, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: signal ?? null,
    });
  } catch {
    return { kind: "failed" };
  }
  if (response.status === 401) {
    return { kind: "signed_out" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { kind: "failed" };
  }
  if (!isSession(body)) {
    return { kind: "failed" };
  }
  return { kind: "ok", session: { username: body.username, expires_at: body.expires_at, csrf_token: body.csrf_token } };
}

// 登录的回答：ok 为 204；failed 为 401 login_failed；locked 为 429 login_locked；unavailable 为 503；
// network 为网络中断；error 为其他（含 204 以外的 2xx、413、415、422 与 5xx）。
export type LoginReply = "ok" | "failed" | "locked" | "unavailable" | "network" | "error";

// POST /api/admin/login：JSON 请求体只有 username 与 password，原样提交（规范化与长度校验由服务端做）。
export async function submitAdminLogin(username: string, password: string): Promise<LoginReply> {
  let response: Response;
  try {
    response = await fetch(ADMIN_LOGIN_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "ok";
    case 401:
      return "failed";
    case 429:
      return "locked";
    case 503:
      return "unavailable";
    default:
      return "error";
  }
}

// 退出的回答：done 为 204；signed_out 为 401（会话已不存在）；csrf 为 403；network 为网络中断；failed 为其他。
export type LogoutReply = "done" | "signed_out" | "csrf" | "network" | "failed";

// POST /api/admin/logout：没有请求体，请求头带会话接口给的 X-CSRF-Token。
export async function submitAdminLogout(csrfToken: string): Promise<LogoutReply> {
  let response: Response;
  try {
    response = await fetch(ADMIN_LOGOUT_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken },
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "done";
    case 401:
      return "signed_out";
    case 403:
      return "csrf";
    default:
      return "failed";
  }
}

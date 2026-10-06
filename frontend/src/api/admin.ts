import { CSRF_HEADER } from "./pay";

// 后台登录页 A01 的管理员接口（app/api/admin_auth.py，SHOP-TASK-036）：GET /api/admin/session、POST /api/admin/login、
// POST /api/admin/logout。后台会话凭服务端发的 HttpOnly cookie（页面脚本读不到也不写）；用户名与密码只放在登录请求体里，
// CSRF 令牌只放在退出请求头里，三者都只在页面内存中，不进路径、查询参数、localStorage、sessionStorage 或 cookie。
// 错误体为 {"detail": "<错误码>"}；页面只按状态码区分，不显示错误体。

export const ADMIN_SESSION_URL = "/api/admin/session";
export const ADMIN_LOGIN_URL = "/api/admin/login";
export const ADMIN_LOGOUT_URL = "/api/admin/logout";

// 当前后台会话（SHOP-TASK-036）：用户名、到期时间（带时区的 ISO 字符串）与供写操作用的 CSRF 令牌。
export interface AdminSession {
  username: string;
  expires_at: string;
  csrf_token: string;
}

// 读取会话的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断。
export type SessionRead = { kind: "ok"; session: AdminSession } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

function isAdminSession(value: unknown): value is AdminSession {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { username, expires_at, csrf_token } = value as Record<string, unknown>;
  return typeof username === "string" && typeof expires_at === "string" && typeof csrf_token === "string";
}

// GET /api/admin/session：同源 cookie、不缓存。
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
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
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
  if (!isAdminSession(body)) {
    return { kind: "failed" };
  }
  return { kind: "ok", session: { username: body.username, expires_at: body.expires_at, csrf_token: body.csrf_token } };
}

// 登录的回答：ok 为 204；rejected 为 401（用户名不存在与密码错误不加区分）；locked 为 429；unavailable 为 503；
// failed 为其他（含 204 以外的 2xx）；network 为网络中断。
export type LoginReply = "ok" | "rejected" | "locked" | "unavailable" | "failed" | "network";

// POST /api/admin/login：JSON 请求体只有用户名与密码，原样提交，规范化由服务端负责。
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
      return "rejected";
    case 429:
      return "locked";
    case 503:
      return "unavailable";
    default:
      return "failed";
  }
}

// 退出的回答：done 为 204，或 401（会话已不存在，同样算已退出）；csrf 为 403；failed 为其他；network 为网络中断。
export type LogoutReply = "done" | "csrf" | "failed" | "network";

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
    case 401:
      return "done";
    case 403:
      return "csrf";
    default:
      return "failed";
  }
}

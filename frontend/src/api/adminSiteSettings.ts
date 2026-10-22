import { CSRF_HEADER } from "./pay";

// 后台站点设置 A09 的接口（app/api/admin_site_settings.py，SHOP-TASK-122）：读取 GET /api/admin/site-settings 与保存
// PUT /api/admin/site-settings，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie；读取不要求 CSRF，保存带读取返回的 CSRF 令牌。
// 只有支付方式，不含短信验证开关（Kelvin 2026-10-07 与 2026-10-15 的决定）。只取 SHOP-TASK-122 记录列出的字段，多出的字段不带进结果；
// 响应体不合格算失败。凭据检查与审计在服务端，这里只传递与校验字段。页面只按状态码区分，不显示错误体（422 的 stripe_not_configured 除外）。

export const ADMIN_SITE_SETTINGS_URL = "/api/admin/site-settings";

// 支付方式的两种取值（与服务端 PAYMENT_METHODS 一致，大小写不同不算）。
export const PAYMENT_METHODS = ["simulated", "stripe_test"] as const;

export type PaymentMethod = (typeof PAYMENT_METHODS)[number];

// 读取的结果：当前支付方式、此刻 Stripe 测试凭据是否可用，与只用于保存请求头的 CSRF 令牌。
export interface AdminSiteSettings {
  payment_method: PaymentMethod;
  stripe_available: boolean;
  csrf_token: string;
}

// 读取的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断（含调用方中止）。
export type SiteSettingsRead = { kind: "ok"; settings: AdminSiteSettings } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 保存的结果：保存了（200，含与当前相同，带保存后的支付方式）、没有会话（401）、CSRF 令牌不对（403）、Stripe 测试凭据不可用
// （422 且 detail 为 stripe_not_configured）、其他失败（含其他 422 与 200 但响应体不合格）、网络中断（含调用方中止）。
export type SiteSettingsSave =
  | { kind: "ok"; payment_method: PaymentMethod }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "not_configured" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPaymentMethod(value: unknown): value is PaymentMethod {
  return typeof value === "string" && (PAYMENT_METHODS as readonly string[]).includes(value);
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

// 读取的响应体：payment_method 为两种取值之一、stripe_available 为布尔值、csrf_token 为非空字符串。
function readSettings(value: unknown): AdminSiteSettings | null {
  if (!isRecord(value)) {
    return null;
  }
  const { payment_method, stripe_available, csrf_token } = value;
  if (!isPaymentMethod(payment_method) || typeof stripe_available !== "boolean" || !isFilled(csrf_token)) {
    return null;
  }
  return { payment_method, stripe_available, csrf_token };
}

// 保存的响应体：只有保存后的 payment_method，为两种取值之一。
function readSaved(value: unknown): PaymentMethod | null {
  if (!isRecord(value) || !isPaymentMethod(value.payment_method)) {
    return null;
  }
  return value.payment_method;
}

// 422 的响应体：detail 为 stripe_not_configured 时为 true；其他 422（含请求体不合格的错误列表）为 null。
function readNotConfigured(value: unknown): true | null {
  return isRecord(value) && value.detail === "stripe_not_configured" ? true : null;
}

// 响应体按 read 读取；读不出或不合格为 null。
async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return read(body);
}

// GET /api/admin/site-settings：同源 cookie、不缓存、没有请求体。
export async function readAdminSiteSettings(signal?: AbortSignal): Promise<SiteSettingsRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_SITE_SETTINGS_URL, {
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
  const settings = await readBody(response, readSettings);
  return settings === null ? { kind: "failed" } : { kind: "ok", settings };
}

// PUT /api/admin/site-settings：JSON 请求体只有 payment_method，请求头带读取给的 CSRF 令牌；同源 cookie、不缓存。
export async function saveAdminSiteSettings(
  paymentMethod: PaymentMethod,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<SiteSettingsSave> {
  let response: Response;
  try {
    response = await fetch(ADMIN_SITE_SETTINGS_URL, {
      method: "PUT",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ payment_method: paymentMethod }),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const saved = await readBody(response, readSaved);
      return saved === null ? { kind: "failed" } : { kind: "ok", payment_method: saved };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 422: {
      const rejected = await readBody(response, readNotConfigured);
      return rejected === null ? { kind: "failed" } : { kind: "not_configured" };
    }
    default:
      return { kind: "failed" };
  }
}

import { isStoreLogoPath } from "../storeDesign";
import { CSRF_HEADER } from "./pay";

// 后台店铺标志图 A08 的接口（app/api/admin_store_logo.py，SHOP-TASK-212）：上传 PUT /api/admin/store-design/logo 与移除
// DELETE /api/admin/store-design/logo，都不带查询参数。后台会话凭服务端发的 HttpOnly cookie，两个请求都带调用方给出的 CSRF 令牌。
// 两个接口成功都是 200，响应体只有 logo（LogoOut）：只取 logo，多出的字段不带进结果；响应体不合格算失败。格式、大小、像素的规则与审计
// 在服务端（DESIGN 1.20 第 15 条），这里只传递与校验字段。HANDOFF 0.84：上传的请求体为文件本身（不用多部分表单），Content-Type 为
// 调用方给的类型；413 一律归为文件过大、不看响应体（反向代理回答的 413 不是 JSON）；logo 只认 isStoreLogoPath 为真的字符串或 null。

export const ADMIN_STORE_LOGO_URL = "/api/admin/store-design/logo";

// 上传的结果：上传了（200，带新的提供路径）、没有会话（401）、CSRF 令牌不对（403）、格式不支持（415 且 detail 为 image_type_unsupported）、
// 文件过大（413，不看响应体）、标志不合格（422 且 detail 为 logo_invalid）、其他失败（含 503、其他 415 与 422、200 但响应体不合格）、
// 网络中断（含调用方中止）。
export type LogoUpload =
  | { kind: "ok"; logo: string }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "type_unsupported" }
  | { kind: "too_large" }
  | { kind: "invalid" }
  | { kind: "failed" }
  | { kind: "network" };

// 移除的结果：移除了（200 且 logo 为 null，没有标志时同样如此）、没有会话（401）、CSRF 令牌不对（403）、其他失败（含 200 但 logo 不为 null）、
// 网络中断（含调用方中止）。
export type LogoRemove = { kind: "ok" } | { kind: "none" } | { kind: "csrf" } | { kind: "failed" } | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// 上传成功的响应体：logo 为合格的提供路径。
function readUploaded(value: unknown): string | null {
  return isRecord(value) && isStoreLogoPath(value.logo) ? value.logo : null;
}

// 移除成功的响应体：logo 恰为 null（缺这一项不算）。
function readRemoved(value: unknown): true | null {
  return isRecord(value) && Object.hasOwn(value, "logo") && value.logo === null ? true : null;
}

// 4xx 的响应体：detail 恰为给定的字符串时为 true，否则为 null。
function detailIs(detail: string): (body: unknown) => true | null {
  return (body) => (isRecord(body) && body.detail === detail ? true : null);
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

// 两个写请求共用：同源 cookie、不缓存、带调用方的 signal；fetch 抛错（含中止）为 null。
async function send(init: Pick<RequestInit, "method" | "headers" | "body">, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(ADMIN_STORE_LOGO_URL, { ...init, credentials: "same-origin", cache: "no-store", signal: signal ?? null });
  } catch {
    return null;
  }
}

// PUT /api/admin/store-design/logo：请求体为调用方给的文件本身（不用 FormData），Content-Type 为调用方给的类型字符串，请求头带 CSRF 令牌；
// 成功为 200。
export async function uploadStoreLogo(file: Blob, contentType: string, csrfToken: string, signal?: AbortSignal): Promise<LogoUpload> {
  const response = await send(
    { method: "PUT", headers: { Accept: "application/json", "Content-Type": contentType, [CSRF_HEADER]: csrfToken }, body: file },
    signal,
  );
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const logo = await readBody(response, readUploaded);
      return logo === null ? { kind: "failed" } : { kind: "ok", logo };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 413:
      return { kind: "too_large" };
    case 415:
      return (await readBody(response, detailIs("image_type_unsupported"))) === null ? { kind: "failed" } : { kind: "type_unsupported" };
    case 422:
      return (await readBody(response, detailIs("logo_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    default:
      return { kind: "failed" };
  }
}

// DELETE /api/admin/store-design/logo：没有请求体，请求头带 CSRF 令牌；成功为 200 且 logo 为 null。
export async function removeStoreLogo(csrfToken: string, signal?: AbortSignal): Promise<LogoRemove> {
  const response = await send({ method: "DELETE", headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken } }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200:
      return (await readBody(response, readRemoved)) === null ? { kind: "failed" } : { kind: "ok" };
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    default:
      return { kind: "failed" };
  }
}

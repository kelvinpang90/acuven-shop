import { adminPromotionUrl, MAX_PROMOTION_ID } from "./adminPromotions";
import { CSRF_HEADER } from "./pay";

// 后台促销活动 A12 海报区的接口（app/api/admin_promotion_poster.py，SHOP-TASK-250）：上传 PUT /api/admin/promotions/{promotion_id}/poster/{slot}，
// slot 为 desktop 或 phone，不带查询参数。UX 0.20 A12 的海报区只有上传即替换、没有移除，所以不包装同一路径的 DELETE。后台会话凭服务端发的 HttpOnly
// cookie，请求带调用方给出的 CSRF 令牌。成功为 200，响应体只有 poster（PosterOut）：只取 poster，多出的字段不带进结果；响应体不合格算失败。格式、
// 宽度、大小的规则与审计在服务端（DESIGN 1.23「节日促销活动」第 12 条），这里只传递与校验字段。HANDOFF 0.98：写法照 api/adminStoreLogo.ts——
// 请求体为文件本身（不用多部分表单），Content-Type 为调用方给的类型；413 一律归为文件过大、不看响应体；poster 只认提供路径。

export const POSTER_SLOTS = ["desktop", "phone"] as const;
export type PosterSlot = (typeof POSTER_SLOTS)[number];

// 海报图的提供路径（app/services/promotion_poster.py：/api/promotion-posters/<文件名>.webp，文件名为 secrets.token_hex(16)）：照标志的检查，
// 恰为 /api/promotion-posters/ 加 32 位小写十六进制加 .webp。
const POSTER_PATH = /^\/api\/promotion-posters\/[0-9a-f]{32}\.webp$/;

// 上传的结果：上传了（200，带新的提供路径）、没有会话（401）、CSRF 令牌不对（403）、活动不存在（404 或路径 ID 不合法，后者不发请求）、活动已结束
// （409 promotion_ended）、格式不支持（415 且 detail 为 image_type_unsupported）、文件过大（413，不看响应体）、海报不合格（422 且 detail 为
// poster_invalid）、其他失败（含 503、其他 409、415 与 422（含 slot 或 ID 的 422）、200 但响应体不合格、slot 不合法时不发请求）、网络中断（含调用方中止）。
export type PosterUpload =
  | { kind: "ok"; poster: string }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "missing" }
  | { kind: "ended" }
  | { kind: "type_unsupported" }
  | { kind: "too_large" }
  | { kind: "invalid" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isPromotionPosterPath(value: unknown): value is string {
  return typeof value === "string" && POSTER_PATH.test(value);
}

function isPosterSlot(value: unknown): value is PosterSlot {
  return typeof value === "string" && (POSTER_SLOTS as readonly string[]).includes(value);
}

// 路径里的活动 ID：不超过 MAX_PROMOTION_ID 的正整数（与服务端相同），否则不发请求。
function isPromotionId(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_PROMOTION_ID;
}

export function adminPromotionPosterUrl(promotionId: number, slot: PosterSlot): string {
  return `${adminPromotionUrl(promotionId)}/poster/${slot}`;
}

// 上传成功的响应体：poster 为合格的提供路径。
function readUploaded(value: unknown): string | null {
  return isRecord(value) && isPromotionPosterPath(value.poster) ? value.poster : null;
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

// PUT /api/admin/promotions/{promotion_id}/poster/{slot}：请求体为调用方给的文件本身（不用 FormData），Content-Type 为调用方给的类型字符串，
// 请求头带 CSRF 令牌；同源 cookie、不缓存、带调用方的 signal。slot 不合法时不发请求、为 failed；活动 ID 不合法时不发请求、为 missing。成功为 200。
export async function uploadPromotionPoster(
  promotionId: number,
  slot: PosterSlot,
  file: Blob,
  contentType: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<PosterUpload> {
  if (!isPosterSlot(slot)) {
    return { kind: "failed" };
  }
  if (!isPromotionId(promotionId)) {
    return { kind: "missing" };
  }
  let response: Response;
  try {
    response = await fetch(adminPromotionPosterUrl(promotionId, slot), {
      method: "PUT",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": contentType, [CSRF_HEADER]: csrfToken },
      body: file,
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const poster = await readBody(response, readUploaded);
      return poster === null ? { kind: "failed" } : { kind: "ok", poster };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 404:
      return { kind: "missing" };
    case 409:
      return (await readBody(response, detailIs("promotion_ended"))) === null ? { kind: "failed" } : { kind: "ended" };
    case 413:
      return { kind: "too_large" };
    case 415:
      return (await readBody(response, detailIs("image_type_unsupported"))) === null ? { kind: "failed" } : { kind: "type_unsupported" };
    case 422:
      return (await readBody(response, detailIs("poster_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    default:
      return { kind: "failed" };
  }
}

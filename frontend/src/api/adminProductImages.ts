import type { AdminProductImage } from "./adminProducts";
import { CSRF_HEADER } from "./pay";

// 后台商品图片 A04 的接口（app/api/admin_product_images.py，SHOP-TASK-190）：上传 POST /api/admin/products/{product_id}/images、调序
// PUT /api/admin/products/{product_id}/images 与移除 DELETE /api/admin/products/{product_id}/images/{image_id}，都不带查询参数。后台会话凭
// 服务端发的 HttpOnly cookie，三个请求都带调用方给出的 CSRF 令牌。三个接口成功都回答 images（ImagesOut）：只取每张图的 id 与 storage_ref，
// 多出的字段不带进结果；响应体不合格算失败。格式、大小、张数与像素的规则、审计在服务端（DESIGN 1.19 第 13 条），这里只传递与校验字段。
// HANDOFF 0.82 第（1）点：上传的请求体为文件本身（不用多部分表单），Content-Type 为调用方给的类型；413 一律归为文件过大、不看响应体
// （反向代理回答的 413 不是 JSON）。

const ADMIN_PRODUCTS_URL = "/api/admin/products";

// 上传的结果：上传了（201，带服务端返回的图片列表）、没有会话（401）、CSRF 令牌不对（403）、格式不支持（415 且 detail 为 image_type_unsupported）、
// 文件过大（413，不看响应体）、图片已满（409 且 detail 为 images_full）、图片不合格（422 且 detail 为 image_invalid）、其他失败（含 404、503、
// 其他 409、415 与 422、201 但响应体不合格）、网络中断（含调用方中止）。
export type ImageUpload =
  | { kind: "ok"; images: AdminProductImage[] }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "type_unsupported" }
  | { kind: "too_large" }
  | { kind: "full" }
  | { kind: "invalid" }
  | { kind: "failed" }
  | { kind: "network" };

// 调序与移除的结果：成功（200，带服务端返回的图片列表）、没有会话（401）、CSRF 令牌不对（403）、图片已被改动（409 且 detail 为 images_changed）、
// 其他失败（含 404、422、413、415、其他 409 与 200 但响应体不合格）、网络中断（含调用方中止）。
export type ImagesWrite =
  | { kind: "ok"; images: AdminProductImage[] }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "changed" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFilled(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

// 一张图：id 为正整数、storage_ref 为非空字符串。
function readImage(value: unknown): AdminProductImage | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, storage_ref } = value;
  if (!isPositiveInteger(id) || !isFilled(storage_ref)) {
    return null;
  }
  return { id, storage_ref };
}

// 成功的响应体：images 为数组（可以为空，按接口给的顺序，第一张为封面），任一项不合格为 null。
function readImages(value: unknown): AdminProductImage[] | null {
  if (!isRecord(value) || !Array.isArray(value.images)) {
    return null;
  }
  const images: AdminProductImage[] = [];
  for (const item of value.images) {
    const image = readImage(item);
    if (image === null) {
      return null;
    }
    images.push(image);
  }
  return images;
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

function imagesUrl(productId: number): string {
  return `${ADMIN_PRODUCTS_URL}/${String(productId)}/images`;
}

// 三个写请求共用：同源 cookie、不缓存、带调用方的 signal；fetch 抛错（含中止）为 null。
async function send(url: string, init: Pick<RequestInit, "method" | "headers" | "body">, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, { ...init, credentials: "same-origin", cache: "no-store", signal: signal ?? null });
  } catch {
    return null;
  }
}

// 调序与移除的归类：200 带回合格的图片列表，409 只认 images_changed。
async function imagesWrite(response: Response | null): Promise<ImagesWrite> {
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const images = await readBody(response, readImages);
      return images === null ? { kind: "failed" } : { kind: "ok", images };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 409:
      return (await readBody(response, detailIs("images_changed"))) === null ? { kind: "failed" } : { kind: "changed" };
    default:
      return { kind: "failed" };
  }
}

// POST /api/admin/products/{product_id}/images：路径段为商品 ID 的十进制，请求体为调用方给的文件本身（不用 FormData），Content-Type 为调用方
// 给的类型字符串，请求头带 CSRF 令牌；成功为 201。
export async function uploadAdminProductImage(
  productId: number,
  file: Blob,
  contentType: string,
  csrfToken: string,
  signal?: AbortSignal,
): Promise<ImageUpload> {
  const response = await send(
    imagesUrl(productId),
    { method: "POST", headers: { Accept: "application/json", "Content-Type": contentType, [CSRF_HEADER]: csrfToken }, body: file },
    signal,
  );
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 201: {
      const images = await readBody(response, readImages);
      return images === null ? { kind: "failed" } : { kind: "ok", images };
    }
    case 401:
      return { kind: "none" };
    case 403:
      return { kind: "csrf" };
    case 413:
      return { kind: "too_large" };
    case 415:
      return (await readBody(response, detailIs("image_type_unsupported"))) === null ? { kind: "failed" } : { kind: "type_unsupported" };
    case 409:
      return (await readBody(response, detailIs("images_full"))) === null ? { kind: "failed" } : { kind: "full" };
    case 422:
      return (await readBody(response, detailIs("image_invalid"))) === null ? { kind: "failed" } : { kind: "invalid" };
    default:
      return { kind: "failed" };
  }
}

// PUT /api/admin/products/{product_id}/images：JSON 请求体只有 image_ids（按调用方给的顺序），请求头带 CSRF 令牌；成功或顺序未变都为 200。
export async function reorderAdminProductImages(productId: number, imageIds: readonly number[], csrfToken: string, signal?: AbortSignal): Promise<ImagesWrite> {
  const response = await send(
    imagesUrl(productId),
    {
      method: "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ image_ids: [...imageIds] }),
    },
    signal,
  );
  return imagesWrite(response);
}

// DELETE /api/admin/products/{product_id}/images/{image_id}：两个路径段为商品 ID 与图片 ID 的十进制，没有请求体，请求头带 CSRF 令牌；成功为 200。
export async function removeAdminProductImage(productId: number, imageId: number, csrfToken: string, signal?: AbortSignal): Promise<ImagesWrite> {
  const response = await send(
    `${imagesUrl(productId)}/${String(imageId)}`,
    { method: "DELETE", headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken } },
    signal,
  );
  return imagesWrite(response);
}

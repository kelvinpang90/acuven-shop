import { CSRF_HEADER } from "./pay";

// 后台优惠券 A05 的接口（app/api/admin_coupons.py，SHOP-TASK-099）。页面只按结果种类处理，不显示错误体。
export const ADMIN_COUPONS_URL = "/api/admin/coupons";
export const MAX_COUPON_ID = 2147483647;

export const COUPON_TYPES = ["fixed", "percent"] as const;
export type CouponType = (typeof COUPON_TYPES)[number];

export interface AdminCoupon {
  id: number;
  code: string;
  type: CouponType;
  amount_off_sen: number | null;
  percent_off: number | null;
  min_subtotal_sen: number;
  starts_on: string;
  ends_on: string;
  total_use_limit: number | null;
  per_member_use_limit: number | null;
  used_count: number;
  active: boolean;
}

export interface AdminCouponPage {
  total: number;
  page: number;
  page_size: 30;
  coupons: AdminCoupon[];
  csrf_token: string;
}

// 新建请求严格只有 SHOP-TASK-099 的九个字段。
export interface AdminCouponInput {
  code: string;
  type: CouponType;
  amount_off_sen: number | null;
  percent_off: number | null;
  min_subtotal_sen: number;
  starts_on: string;
  ends_on: string;
  total_use_limit: number | null;
  per_member_use_limit: number | null;
}

export type CouponsRead = { kind: "ok"; page: AdminCouponPage } | { kind: "none" } | { kind: "failed" } | { kind: "network" };
export type CouponCreate =
  | { kind: "ok"; coupon: AdminCoupon }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "code_taken" }
  | { kind: "invalid" }
  | { kind: "failed" }
  | { kind: "network" };
export type CouponDisable =
  | { kind: "ok"; coupon: AdminCoupon }
  | { kind: "none" }
  | { kind: "csrf" }
  | { kind: "not_found" }
  | { kind: "failed" }
  | { kind: "network" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositive(value: unknown): value is number {
  return isCount(value) && value > 0;
}

function isDate(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) {
    return false;
  }
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function readCoupon(value: unknown): AdminCoupon | null {
  if (!isRecord(value)) {
    return null;
  }
  const { id, code, type, amount_off_sen, percent_off, min_subtotal_sen, starts_on, ends_on, total_use_limit, per_member_use_limit, used_count, active } = value;
  if (
    !isPositive(id) ||
    typeof code !== "string" ||
    code === "" ||
    !isCount(min_subtotal_sen) ||
    !isDate(starts_on) ||
    !isDate(ends_on) ||
    !(total_use_limit === null || isPositive(total_use_limit)) ||
    !(per_member_use_limit === null || isPositive(per_member_use_limit)) ||
    !isCount(used_count) ||
    typeof active !== "boolean"
  ) {
    return null;
  }
  if (type === "fixed" && isPositive(amount_off_sen) && percent_off === null) {
    return { id, code, type, amount_off_sen, percent_off, min_subtotal_sen, starts_on, ends_on, total_use_limit, per_member_use_limit, used_count, active };
  }
  if (type === "percent" && isCount(percent_off) && percent_off >= 1 && percent_off <= 100 && amount_off_sen === null) {
    return { id, code, type, amount_off_sen, percent_off, min_subtotal_sen, starts_on, ends_on, total_use_limit, per_member_use_limit, used_count, active };
  }
  return null;
}

function readCoupons(value: unknown): AdminCoupon[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const coupons: AdminCoupon[] = [];
  for (const item of value) {
    const coupon = readCoupon(item);
    if (coupon === null) {
      return null;
    }
    coupons.push(coupon);
  }
  return coupons;
}

function readPage(value: unknown): AdminCouponPage | null {
  if (!isRecord(value)) {
    return null;
  }
  const { total, page, page_size, coupons, csrf_token } = value;
  const rows = readCoupons(coupons);
  if (!isCount(total) || !isCount(page) || page < 1 || page > 10000 || page_size !== 30 || rows === null || typeof csrf_token !== "string" || csrf_token === "") {
    return null;
  }
  return { total, page, page_size, coupons: rows, csrf_token };
}

async function readBody<T>(response: Response, read: (body: unknown) => T | null): Promise<T | null> {
  try {
    return read(await response.json());
  } catch {
    return null;
  }
}

async function detail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

export function adminCouponsUrl(page: number): string {
  return `${ADMIN_COUPONS_URL}?${new URLSearchParams({ page: String(page) }).toString()}`;
}

export function adminCouponDisableUrl(id: number): string {
  return `${ADMIN_COUPONS_URL}/${String(id)}/disable`;
}

export async function listAdminCoupons(page: number, signal?: AbortSignal): Promise<CouponsRead> {
  let response: Response;
  try {
    response = await fetch(adminCouponsUrl(page), { method: "GET", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }, signal: signal ?? null });
  } catch {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "none" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const pageResult = await readBody(response, readPage);
  return pageResult === null ? { kind: "failed" } : { kind: "ok", page: pageResult };
}

export async function createAdminCoupon(input: AdminCouponInput, csrfToken: string, signal?: AbortSignal): Promise<CouponCreate> {
  let response: Response;
  try {
    response = await fetch(ADMIN_COUPONS_URL, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ code: input.code, type: input.type, amount_off_sen: input.amount_off_sen, percent_off: input.percent_off, min_subtotal_sen: input.min_subtotal_sen, starts_on: input.starts_on, ends_on: input.ends_on, total_use_limit: input.total_use_limit, per_member_use_limit: input.per_member_use_limit }),
      signal: signal ?? null,
    });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 201: {
      const coupon = await readBody(response, readCoupon);
      return coupon === null ? { kind: "failed" } : { kind: "ok", coupon };
    }
    case 401: return { kind: "none" };
    case 403: return { kind: "csrf" };
    case 409: return (await detail(response)) === "coupon_code_taken" ? { kind: "code_taken" } : { kind: "failed" };
    case 422: return { kind: "invalid" };
    default: return { kind: "failed" };
  }
}

export async function disableAdminCoupon(id: number, csrfToken: string, signal?: AbortSignal): Promise<CouponDisable> {
  if (!Number.isSafeInteger(id) || id < 1 || id > MAX_COUPON_ID) {
    return { kind: "failed" };
  }
  let response: Response;
  try {
    response = await fetch(adminCouponDisableUrl(id), { method: "POST", credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken }, signal: signal ?? null });
  } catch {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200: {
      const coupon = await readBody(response, readCoupon);
      return coupon === null ? { kind: "failed" } : { kind: "ok", coupon };
    }
    case 401: return { kind: "none" };
    case 403: return { kind: "csrf" };
    case 404: return { kind: "not_found" };
    default: return { kind: "failed" };
  }
}

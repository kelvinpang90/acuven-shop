import { CSRF_HEADER } from "./pay";

// 后台 Stripe 待处理 A11 的三个接口（app/api/admin_stripe_pending.py，SHOP-TASK-225）：列表 GET /api/admin/stripe-pending、
// 作废 POST /api/admin/stripe-pending/checkouts/{结账记录 ID}/void 与迟到付款退款的重试 POST …/checkouts/{结账记录 ID}/refund-retry。
// 后台会话凭服务端发的 HttpOnly cookie；列表只读，不带 CSRF 令牌；两个写操作带列表给的 CSRF 令牌。路径只用结账记录 ID。
// 只取 SHOP-TASK-225 记录段列出的字段，多出的字段不带进结果；响应体不合格算失败。令牌与响应内容不进日志与错误；
// 页面只按状态码（409 与 503 另按错误码）区分，不显示错误体。

export const ADMIN_STRIPE_PENDING_URL = "/api/admin/stripe-pending";

// 结账记录 ID 的上限，与服务端路径 ID 的上限（2³¹−1）相同。
export const MAX_CHECKOUT_ID = 2147483647;

// 第一个列表的记录状态（UX 0.19 A11「记录状态」）：创建中、进行中、已完成。
export const CHECKOUT_STATUSES = ["creating", "open", "complete"] as const;
export type CheckoutStatus = (typeof CHECKOUT_STATUSES)[number];

// 已告警而订单仍待支付的结账记录：记录 ID、状态、订单号、创建时间与告警时间（带时区的 ISO 字符串）。
export interface PendingCheckout {
  checkout_id: number;
  status: CheckoutStatus;
  order_number: string;
  created_at: string;
  alerted_at: string;
}

// 失败的迟到付款退款：结账记录 ID、最新一次尝试的 ID、订单号、金额（整数仙）、尝试时间与 Stripe 的错误类别码（为空时 null）。
export interface FailedLateRefund {
  checkout_id: number;
  attempt_id: number;
  order_number: string;
  amount_sen: number;
  attempted_at: string;
  error_type: string | null;
}

// 整页：两个列表（按接口给的顺序）与写操作要带的 CSRF 令牌。
export interface StripePending {
  checkouts: PendingCheckout[];
  refunds: FailedLateRefund[];
  csrf_token: string;
}

// 读取的结果：取到（200）、没有会话（401）、其他失败（含 200 但响应体不合格）、网络中断。
export type StripePendingRead = { kind: "ok"; pending: StripePending } | { kind: "none" } | { kind: "failed" } | { kind: "network" };

// 作废的回答：confirmed、cancelled、voided 为 200 的三种结果；changed 为 409 stripe_pending_changed 或 404；unavailable 为
// 503 stripe_unavailable；csrf 为 403；none 为 401；failed 为其他（含别的 409、503、422 与 200 的结果不是三种之一）；network 为网络中断。
export type VoidReply = "confirmed" | "cancelled" | "voided" | "changed" | "unavailable" | "csrf" | "none" | "failed" | "network";

// 重试的回答：done 为 200；changed 为 409 stripe_refund_not_retryable 或 404；cap 为 409 stripe_refund_cap；csrf 为 403；
// none 为 401；failed 为其他（含别的 409 与 422）；network 为网络中断。
export type RefundRetryReply = "done" | "changed" | "cap" | "csrf" | "none" | "failed" | "network";

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositive(value: unknown): value is number {
  return isCount(value) && value > 0;
}

function isCheckoutStatus(value: unknown): value is CheckoutStatus {
  return typeof value === "string" && (CHECKOUT_STATUSES as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readCheckout(value: unknown): PendingCheckout | null {
  if (!isRecord(value)) {
    return null;
  }
  const { checkout_id, status, order_number, created_at, alerted_at } = value;
  if (
    !isPositive(checkout_id) ||
    !isCheckoutStatus(status) ||
    typeof order_number !== "string" ||
    typeof created_at !== "string" ||
    typeof alerted_at !== "string"
  ) {
    return null;
  }
  return { checkout_id, status, order_number, created_at, alerted_at };
}

function readRefund(value: unknown): FailedLateRefund | null {
  if (!isRecord(value)) {
    return null;
  }
  const { checkout_id, attempt_id, order_number, amount_sen, attempted_at, error_type } = value;
  if (
    !isPositive(checkout_id) ||
    !isPositive(attempt_id) ||
    typeof order_number !== "string" ||
    !isCount(amount_sen) ||
    typeof attempted_at !== "string" ||
    !(error_type === null || typeof error_type === "string")
  ) {
    return null;
  }
  return { checkout_id, attempt_id, order_number, amount_sen, attempted_at, error_type };
}

// 逐项读取；任一项不合格整个数组不合格。
function readEach<T>(value: unknown, read: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const items: T[] = [];
  for (const item of value as unknown[]) {
    const found = read(item);
    if (found === null) {
      return null;
    }
    items.push(found);
  }
  return items;
}

function readPending(value: unknown): StripePending | null {
  if (!isRecord(value)) {
    return null;
  }
  const { csrf_token } = value;
  const checkouts = readEach(value.checkouts, readCheckout);
  const refunds = readEach(value.refunds, readRefund);
  if (typeof csrf_token !== "string" || csrf_token === "" || checkouts === null || refunds === null) {
    return null;
  }
  return { checkouts, refunds, csrf_token };
}

function isCheckoutId(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_CHECKOUT_ID;
}

export function stripeVoidUrl(checkoutId: number): string {
  return `${ADMIN_STRIPE_PENDING_URL}/checkouts/${String(checkoutId)}/void`;
}

export function stripeRefundRetryUrl(checkoutId: number): string {
  return `${ADMIN_STRIPE_PENDING_URL}/checkouts/${String(checkoutId)}/refund-retry`;
}

// 错误体 {"detail": "<错误码>"} 的错误码；读不出或不是字符串为 null。
async function errorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// GET /api/admin/stripe-pending：同源 cookie、不缓存，没有查询参数与请求体。
export async function readStripePending(signal?: AbortSignal): Promise<StripePendingRead> {
  let response: Response;
  try {
    response = await fetch(ADMIN_STRIPE_PENDING_URL, {
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
  const pending = readPending(body);
  return pending === null ? { kind: "failed" } : { kind: "ok", pending };
}

const VOID_RESULTS: readonly string[] = ["confirmed", "cancelled", "voided"];

// POST …/checkouts/{结账记录 ID}/void：JSON 请求体只有 verified（固定为 true，页面在管理员勾选核对之后才调用），请求头带列表给的
// CSRF 令牌；同源 cookie、不缓存。ID 不是不超过上限的正整数时不发请求，算失败。
export async function voidCheckout(checkoutId: number, csrfToken: string): Promise<VoidReply> {
  if (!isCheckoutId(checkoutId)) {
    return "failed";
  }
  let response: Response;
  try {
    response = await fetch(stripeVoidUrl(checkoutId), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", [CSRF_HEADER]: csrfToken },
      body: JSON.stringify({ verified: true }),
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 200: {
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        return "failed";
      }
      const result = isRecord(body) ? body.result : undefined;
      return typeof result === "string" && VOID_RESULTS.includes(result) ? (result as VoidReply) : "failed";
    }
    case 401:
      return "none";
    case 403:
      return "csrf";
    case 404:
      return "changed";
    case 409:
      return (await errorCode(response)) === "stripe_pending_changed" ? "changed" : "failed";
    case 503:
      return (await errorCode(response)) === "stripe_unavailable" ? "unavailable" : "failed";
    default:
      return "failed";
  }
}

const RETRY_CONFLICTS: ReadonlyMap<string, RefundRetryReply> = new Map<string, RefundRetryReply>([
  ["stripe_refund_not_retryable", "changed"],
  ["stripe_refund_cap", "cap"],
]);

// POST …/checkouts/{结账记录 ID}/refund-retry：没有查询参数与请求体，请求头只带 Accept 与列表给的 CSRF 令牌（接口不要幂等键）；
// 同源 cookie、不缓存。ID 不是不超过上限的正整数时不发请求，算失败。200 的响应体不读。
export async function retryLateRefund(checkoutId: number, csrfToken: string): Promise<RefundRetryReply> {
  if (!isCheckoutId(checkoutId)) {
    return "failed";
  }
  let response: Response;
  try {
    response = await fetch(stripeRefundRetryUrl(checkoutId), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", [CSRF_HEADER]: csrfToken },
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 200:
      return "done";
    case 401:
      return "none";
    case 403:
      return "csrf";
    case 404:
      return "changed";
    case 409: {
      const code = await errorCode(response);
      return (code === null ? undefined : RETRY_CONFLICTS.get(code)) ?? "failed";
    }
    default:
      return "failed";
  }
}

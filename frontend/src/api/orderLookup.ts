import type { Language } from "../i18n/copy";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS, wait } from "./pay";
import type { PayRecipient } from "./pay";

// 订单查询 P08 与订单详情 P09（查单模式）的游客接口（app/api/order_lookup.py，SHOP-TASK-027）：
// POST /api/orders/lookup、GET /api/orders/lookup、POST /api/orders/confirm-receipt。
// 查单授权凭本浏览器的订单访问 cookie（服务端发的 HttpOnly cookie，页面脚本读不到也不写）；订单号与电话只放在请求体里，
// CSRF 令牌只放在请求头里，三者都只在页面内存中，不进路径、查询参数、localStorage、sessionStorage 或 cookie（UX「阅读说明」）。
// 金额是服务端算好的整数仙，页面只格式化，不计算。

export const LOOKUP_URL = "/api/orders/lookup";
export const CONFIRM_RECEIPT_URL = "/api/orders/confirm-receipt";

export const SHIPPED = "demo_shipped";

// 订单行快照：名称与规格说明按请求语言；逐件实付按件序（未支付时可为空）。
export interface LookupLine {
  name: string;
  variant_label: string;
  quantity: number;
  unit_price_sen: number;
  line_subtotal_sen: number;
  unit_cash_paid_sen: number[];
}

// 查单模式的一张订单。时间都是带时区的 ISO 字符串。不含优惠券与积分两项（游客订单，UX 0.5）。
export interface LookupOrder {
  order_number: string;
  status: string;
  created_at: string;
  paid_at: string | null;
  server_time: string;
  lines: LookupLine[];
  subtotal_sen: number;
  shipping_fee_sen: number;
  total_sen: number;
  recipient: PayRecipient | null;
}

interface LookupOrdersResponse {
  orders: LookupOrder[];
  csrf_token: string;
}

// 查单的回答：found 为 204（已签发查单授权）；not_found 为 404（不存在与电话不符不加区分）；rate_limited 为 429；
// unavailable 为 503；failed 为其他（含 204 以外的 2xx）；network 为网络中断。
export type LookupReply = "found" | "not_found" | "rate_limited" | "unavailable" | "failed" | "network";

// POST /api/orders/lookup：请求体只有订单号与电话，原样提交，格式由服务端判定。
export async function submitLookup(orderNumber: string, phone: string, signal?: AbortSignal): Promise<LookupReply> {
  let response: Response;
  try {
    response = await fetch(LOOKUP_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ order_number: orderNumber, phone }),
      signal: signal ?? null,
    });
  } catch {
    return "network";
  }
  switch (response.status) {
    case 204:
      return "found";
    case 404:
      return "not_found";
    case 429:
      return "rate_limited";
    case 503:
      return "unavailable";
    default:
      return "failed";
  }
}

// 读取订单的结果：取到、授权过期（401，或没有该订单）、其他失败、网络中断。
export type LookupRead =
  | { kind: "ok"; order: LookupOrder; csrfToken: string }
  | { kind: "expired" }
  | { kind: "failed" }
  | { kind: "network" };

export type LookupFound = Extract<LookupRead, { kind: "ok" }>;

export function lookupOrdersUrl(language: Language): string {
  return `${LOOKUP_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// GET /api/orders/lookup：orderNumber 为 null 时取第一张订单（最近查询的）；给定订单号时取该单，
// 列表里已没有该单（本浏览器对它的授权已结束）视为过期。
export async function readLookupOrder(language: Language, orderNumber: string | null, signal?: AbortSignal): Promise<LookupRead> {
  let response: Response;
  try {
    response = await fetch(lookupOrdersUrl(language), {
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
    return { kind: "expired" };
  }
  if (!response.ok) {
    return { kind: "failed" };
  }
  let body: LookupOrdersResponse;
  try {
    body = (await response.json()) as LookupOrdersResponse;
  } catch {
    return { kind: "failed" };
  }
  const order = orderNumber === null ? body.orders[0] : body.orders.find((candidate) => candidate.order_number === orderNumber);
  return order ? { kind: "ok", order, csrfToken: body.csrf_token } : { kind: "expired" };
}

// 确认收货的幂等键：每次点击新生成；只有上一次是网络中断后确认仍为 demo_shipped 时才沿用原来的键。
export function receiptKey(retry: string | null, newKey: () => string = () => crypto.randomUUID()): string {
  return retry ?? newKey();
}

// 确认收货的回答：done 为 200；closed 为 409 order_not_confirmable；expired 为 401；csrf 为 403；
// failed 为其他（含 200 以外的 2xx 与 409 idempotency_conflict）；network 为网络中断。
export type ConfirmReply = "done" | "closed" | "expired" | "csrf" | "failed" | "network";

async function conflictDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// POST /api/orders/confirm-receipt：请求体只有订单号，请求头带幂等键与 CSRF 令牌。
export async function submitConfirmReceipt(orderNumber: string, csrfToken: string, key: string): Promise<ConfirmReply> {
  let response: Response;
  try {
    response = await fetch(CONFIRM_RECEIPT_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        [IDEMPOTENCY_HEADER]: key,
        [CSRF_HEADER]: csrfToken,
      },
      body: JSON.stringify({ order_number: orderNumber }),
    });
  } catch {
    return "network";
  }
  if (response.status === 200) {
    return "done";
  }
  if (response.status === 401) {
    return "expired";
  }
  if (response.status === 403) {
    return "csrf";
  }
  if (response.status === 409) {
    return (await conflictDetail(response)) === "order_not_confirmable" ? "closed" : "failed";
  }
  return "failed";
}

export interface RecheckOptions {
  signal?: AbortSignal | undefined;
  delay?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

// 重新读取该单，直到得到 ok 或 expired；中止时返回 null。
export async function recheckOrder(
  language: Language,
  orderNumber: string,
  { signal, delay = wait }: RecheckOptions = {},
): Promise<Exclude<LookupRead, { kind: "failed" | "network" }> | null> {
  for (;;) {
    const read = await readLookupOrder(language, orderNumber, signal);
    if (signal?.aborted) {
      return null;
    }
    if (read.kind === "ok" || read.kind === "expired") {
      return read;
    }
    await delay(RECHECK_DELAY_MS, signal);
    if (signal?.aborted) {
      return null;
    }
  }
}

// 一次确认收货之后页面的去向：
// read 按重新读取到的订单显示（200 或 409 order_not_confirmable）；expired 整页 order.session_expired；
// error 显示 common.error_retry（read 为重新读取到的订单与新令牌，没有则沿用原来的）；
// retry 为网络中断后确认订单仍为 demo_shipped：显示 common.error_retry，允许以同一幂等键（key）重试。
export type ConfirmOutcome =
  | { kind: "read"; read: LookupFound }
  | { kind: "expired" }
  | { kind: "error"; read: LookupFound | null }
  | { kind: "retry"; read: LookupFound; key: string };

export interface ConfirmOptions extends RecheckOptions {
  // 网络中断、开始重新读取订单时调用（页面显示 common.network_check）。
  onChecking?: () => void;
}

// 提交确认收货并决定去向；页面已离开（中止）时返回 null。
export async function runConfirmReceipt(
  language: Language,
  orderNumber: string,
  csrfToken: string,
  key: string,
  options: ConfirmOptions = {},
): Promise<ConfirmOutcome | null> {
  const reply = await submitConfirmReceipt(orderNumber, csrfToken, key);
  if (options.signal?.aborted) {
    return null;
  }
  switch (reply) {
    case "expired":
      return { kind: "expired" };
    case "failed":
      return { kind: "error", read: null };
    case "done":
    case "closed":
    case "csrf": {
      // 已确认或不能再确认：重新读取订单按新状态显示；令牌失效：重新读取订单取得新令牌，再提示重试。
      const read = await readLookupOrder(language, orderNumber, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      if (read.kind !== "ok") {
        return { kind: "error", read: null };
      }
      return reply === "csrf" ? { kind: "error", read } : { kind: "read", read };
    }
    case "network": {
      // 网络中断：先重新读取订单确认上一步是否已完成，仍为 demo_shipped 时才允许以同一幂等键重试。
      options.onChecking?.();
      const read = await recheckOrder(language, orderNumber, options);
      if (read === null) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      if (read.order.status !== SHIPPED) {
        return { kind: "read", read };
      }
      return { kind: "retry", read, key };
    }
  }
}

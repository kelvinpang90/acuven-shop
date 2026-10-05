import type { Language } from "../i18n/copy";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS, wait } from "./pay";
import type { PayRecipient } from "./pay";

// 订单查询 P08、订单详情 P09 与退款申请 P10（查单模式）的游客接口（app/api/order_lookup.py，SHOP-TASK-027；
// app/api/refunds.py，SHOP-TASK-029）：POST /api/orders/lookup、GET /api/orders/lookup、POST /api/orders/confirm-receipt、
// POST /api/orders/refunds。
// 查单授权凭本浏览器的订单访问 cookie（服务端发的 HttpOnly cookie，页面脚本读不到也不写）；订单号与电话只放在请求体里，
// CSRF 令牌只放在请求头里，三者都只在页面内存中，不进路径、查询参数、localStorage、sessionStorage 或 cookie（UX「阅读说明」）。
// 金额是服务端算好的整数仙，页面只格式化，不计算；预计退款只把接口给的各行金额相加。

export const LOOKUP_URL = "/api/orders/lookup";
export const CONFIRM_RECEIPT_URL = "/api/orders/confirm-receipt";
export const REFUNDS_URL = "/api/orders/refunds";

export const SHIPPED = "demo_shipped";

// 退款申请的三种状态（app/models/refund.py）。
export const REFUND_REQUESTED = "requested";
export const REFUND_APPROVED = "approved";
export const REFUND_REJECTED = "rejected";

// 订单行快照：行序、名称与规格说明按请求语言；逐件实付按件序（未支付时可为空）。
// 另有该行可退件数与预计金额列表：第 i 项（从 1 数起）为申请 i 件时的金额，长度等于可退件数。
export interface LookupLine {
  line_index: number;
  name: string;
  variant_label: string;
  quantity: number;
  unit_price_sen: number;
  line_subtotal_sen: number;
  unit_cash_paid_sen: number[];
  refundable_quantity: number;
  refund_estimates_sen: number[];
}

// 一笔退款申请里的一行：名称与规格说明（按请求语言的下单快照）、件数与该行金额。
export interface LookupRefundLine {
  name: string;
  variant_label: string;
  quantity: number;
  amount_sen: number;
}

// 一笔退款申请：创建时间、状态、金额合计与各行。
export interface LookupRefund {
  created_at: string;
  status: string;
  amount_sen: number;
  lines: LookupRefundLine[];
}

// 查单模式的一张订单。时间都是带时区的 ISO 字符串。不含优惠券与积分两项（游客订单，UX 0.5），退款部分也不含积分。
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
  // 退款截止时间；未支付为空。
  refund_deadline: string | null;
  // 服务端判定是否在退款期内；未支付为假。
  refund_window_open: boolean;
  refunded_total_sen: number;
  refundable_left_sen: number;
  // 服务端判定全部件都已退。
  fully_refunded: boolean;
  // 按创建时间从新到旧。
  refund_requests: LookupRefund[];
}

// P09 的退款入口：接口判定在退款期内且剩余可退大于零时才显示（UX P09「已支付且在退款期内」）。
export function showsRefundEntry(order: Pick<LookupOrder, "refund_window_open" | "refundable_left_sen">): boolean {
  return order.refund_window_open && order.refundable_left_sen > 0;
}

// P09 的确认收货：只有 demo_shipped 且不是全部已退时（UX P09「全退后冻结」）。
export function canConfirmReceipt(order: Pick<LookupOrder, "status" | "fully_refunded">): boolean {
  return order.status === SHIPPED && !order.fully_refunded;
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

// 退款申请的一行：订单行序与件数（只放件数大于 0 的行）。
export interface RefundLineRequest {
  line_index: number;
  quantity: number;
}

// 各行选定的件数（与订单行同序）换成请求体的行。
export function refundLines(lines: readonly LookupLine[], quantities: readonly number[]): RefundLineRequest[] {
  return lines.flatMap((line, index) => {
    const quantity = quantities[index] ?? 0;
    return quantity > 0 ? [{ line_index: line.line_index, quantity }] : [];
  });
}

// 预计退款：取各行预计金额列表中对应件数的那一项再相加（UX P10 M2「由服务端按逐件快照计算」）；只做整数仙加法，不做乘除或分摊。
// 件数超出列表时没有对应的金额（页面把件数限在可退件数以内，不会出现），返回 null，不显示金额。
export function refundEstimate(lines: readonly LookupLine[], quantities: readonly number[]): number | null {
  let total = 0;
  for (const [index, line] of lines.entries()) {
    const quantity = quantities[index] ?? 0;
    if (quantity === 0) {
      continue;
    }
    const amount = line.refund_estimates_sen[quantity - 1];
    if (amount === undefined) {
      return null;
    }
    total += amount;
  }
  return total;
}

// 读取（或重新读取）订单后各行的件数：保留已选的件数，但不超过该行现在的可退件数。
export function clampQuantities(lines: readonly LookupLine[], previous: readonly number[]): number[] {
  return lines.map((line, index) => Math.max(0, Math.min(previous[index] ?? 0, line.refundable_quantity)));
}

// 网络中断后可以重试的那次申请：幂等键与当时的请求行。
export interface RefundRetry {
  key: string;
  lines: RefundLineRequest[];
}

function sameLines(a: readonly RefundLineRequest[], b: readonly RefundLineRequest[]): boolean {
  return a.length === b.length && a.every((line, index) => line.line_index === b[index]?.line_index && line.quantity === b[index]?.quantity);
}

// 退款申请的幂等键：每次提交新生成；只有网络中断后以同一请求内容重试时才沿用原来的键（内容改了就是新的申请）。
export function refundKey(retry: RefundRetry | null, lines: readonly RefundLineRequest[], newKey: () => string = () => crypto.randomUUID()): string {
  return retry !== null && sameLines(retry.lines, lines) ? retry.key : newKey();
}

// 提交退款申请的回答：done 为 201（新写）或 200（按幂等键重放）；409 按 detail 分为 duplicate、nothing_left、window_closed、
// not_refundable、conflict（idempotency_conflict）；expired 为 401；csrf 为 403；failed 为其他（含其余 2xx 与未知的 409）；
// network 为网络中断。
export type RefundReply = "done" | "duplicate" | "nothing_left" | "window_closed" | "not_refundable" | "conflict" | "expired" | "csrf" | "failed" | "network";

const REFUND_CONFLICTS: ReadonlyMap<string, RefundReply> = new Map<string, RefundReply>([
  ["refund_duplicate", "duplicate"],
  ["refund_nothing_left", "nothing_left"],
  ["refund_window_closed", "window_closed"],
  ["order_not_refundable", "not_refundable"],
  ["idempotency_conflict", "conflict"],
]);

// POST /api/orders/refunds：请求体只有订单号与申请行，请求头带幂等键与 CSRF 令牌；没有任何金额字段。
export async function submitRefund(orderNumber: string, csrfToken: string, key: string, lines: readonly RefundLineRequest[]): Promise<RefundReply> {
  let response: Response;
  try {
    response = await fetch(REFUNDS_URL, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        [IDEMPOTENCY_HEADER]: key,
        [CSRF_HEADER]: csrfToken,
      },
      body: JSON.stringify({ order_number: orderNumber, lines }),
    });
  } catch {
    return "network";
  }
  if (response.status === 201 || response.status === 200) {
    return "done";
  }
  if (response.status === 401) {
    return "expired";
  }
  if (response.status === 403) {
    return "csrf";
  }
  if (response.status === 409) {
    const detail = await conflictDetail(response);
    return (detail === null ? undefined : REFUND_CONFLICTS.get(detail)) ?? "failed";
  }
  return "failed";
}

// 被拒的申请（409）各自的提示：refund.duplicate、refund.nothing_left、refund.window_closed。
export type RefundRefusal = "duplicate" | "nothing_left" | "window_closed";

// 一次退款申请之后页面的去向：
// submitted 回到 P09 并显示 refund.submitted；leave 回到 P09（409 order_not_refundable）；expired 整页 order.session_expired；
// refused 显示对应提示并按重新读取到的订单显示（read 为空时沿用原来的）；error 显示 common.error_retry（同上）；
// retry 为网络中断后确认申请没有写入：显示 common.error_retry，允许以同一幂等键与同一请求内容（retry）重试。
export type RefundOutcome =
  | { kind: "submitted" }
  | { kind: "leave" }
  | { kind: "expired" }
  | { kind: "refused"; reason: RefundRefusal; read: LookupFound | null }
  | { kind: "error"; read: LookupFound | null }
  | { kind: "retry"; read: LookupFound; retry: RefundRetry };

// 提交退款申请并决定去向；页面已离开（中止）时返回 null。order 为提交时显示的订单（取订单号与已有的申请笔数）。
export async function runRefund(
  language: Language,
  order: Pick<LookupOrder, "order_number" | "refund_requests">,
  csrfToken: string,
  key: string,
  lines: readonly RefundLineRequest[],
  options: ConfirmOptions = {},
): Promise<RefundOutcome | null> {
  const reply = await submitRefund(order.order_number, csrfToken, key, lines);
  if (options.signal?.aborted) {
    return null;
  }
  switch (reply) {
    case "done":
      return { kind: "submitted" };
    case "not_refundable":
      return { kind: "leave" };
    case "expired":
      return { kind: "expired" };
    case "failed":
      return { kind: "error", read: null };
    case "duplicate":
    case "nothing_left":
    case "window_closed":
    case "conflict":
    case "csrf": {
      // 被拒：重新读取订单得到最新的可退件；幂等键冲突：重新读取后提示重试（下次提交另生成键）；令牌失效：重新读取取得新令牌。
      const read = await readLookupOrder(language, order.order_number, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      const found = read.kind === "ok" ? read : null;
      return reply === "conflict" || reply === "csrf" ? { kind: "error", read: found } : { kind: "refused", reason: reply, read: found };
    }
    case "network": {
      // 网络中断：先重新读取订单确认申请是否已写入（申请记录多了一笔），没有时才允许以同一幂等键与同一请求内容重试。
      options.onChecking?.();
      const read = await recheckOrder(language, order.order_number, options);
      if (read === null) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      if (read.order.refund_requests.length > order.refund_requests.length) {
        return { kind: "submitted" };
      }
      return { kind: "retry", read, retry: { key, lines: [...lines] } };
    }
  }
}

// P10 提交成功后交给 P09 的订单号：P09 打开时取该单并显示 refund.submitted。
// 只在页面内存（本模块的变量）里，不进网址、历史记录或任何浏览器存储；整页刷新即消失。
let submittedRefund: string | null = null;

export function handOffSubmittedRefund(orderNumber: string): void {
  submittedRefund = orderNumber;
}

export function submittedRefundOrder(): string | null {
  return submittedRefund;
}

export function clearSubmittedRefund(): void {
  submittedRefund = null;
}

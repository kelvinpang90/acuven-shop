import type { Language } from "../i18n/copy";
import { parsePointsEstimate, SHIPPED } from "./orderLookup";
import type { LookupOrder, PointsEstimate, RecheckOptions, RefundLineRequest, RefundRefusal, RefundRetry } from "./orderLookup";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS, wait } from "./pay";

// 我的订单 P13 与订单详情 P09、退款申请 P10（会员模式）的会员接口（app/api/account_orders.py，SHOP-TASK-104、105）：
// GET /api/member/orders、POST /api/member/orders/view、POST /api/member/orders/confirm-receipt、POST /api/member/orders/refunds
// 与 POST /api/member/orders/refunds/points-estimate。授权凭服务端发的会员会话 HttpOnly cookie（页面脚本不读也不写），
// 服务端只给出本会员认领或下单的订单（DESIGN 1.13「权限与资料保护」第 3、5 条）。订单号只放在请求体里，CSRF 令牌只放在
// 请求头里，都只在页面内存中，不进地址、日志、localStorage、sessionStorage 或 cookie；只有详情带语言查询参数。
// 确认收货与退款申请的规则、幂等键与提交后的去向与查单模式（orderLookup.ts）相同；金额是服务端算好的整数仙，页面不计算。

export const MEMBER_ORDERS_URL = "/api/member/orders";
export const MEMBER_ORDER_VIEW_URL = "/api/member/orders/view";
export const MEMBER_CONFIRM_RECEIPT_URL = "/api/member/orders/confirm-receipt";
export const MEMBER_REFUNDS_URL = "/api/member/orders/refunds";
export const MEMBER_POINTS_ESTIMATE_URL = "/api/member/orders/refunds/points-estimate";

// 我的订单的一行：订单号、下单时间（带时区的 ISO 字符串）、状态与实付合计（整数仙）。
export interface MemberOrderSummary {
  order_number: string;
  created_at: string;
  status: string;
  total_sen: number;
}

interface MemberOrdersResponse {
  total: number;
  orders: MemberOrderSummary[];
}

// 错误体为 {"detail": ...}：detail 为字符串时取它，否则（出错列表、缺少或不是 JSON）为 null。
async function errorCode(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// 五个接口共有的错误：signed_out 为 401 member_session_required；not_found 为 404 order_not_found（列表没有这一项）；
// csrf 为 403 csrf_failed（只有两个写操作）。其他错误码为 null，由各接口归 failed 或按 409 再分。
type MemberRejection = "signed_out" | "not_found" | "csrf";

const REJECTIONS: ReadonlyMap<string, [number, MemberRejection]> = new Map<string, [number, MemberRejection]>([
  ["member_session_required", [401, "signed_out"]],
  ["order_not_found", [404, "not_found"]],
  ["csrf_failed", [403, "csrf"]],
]);

function rejection(status: number, code: string | null): MemberRejection | null {
  const known = code === null ? undefined : REJECTIONS.get(code);
  return known !== undefined && known[0] === status ? known[1] : null;
}

// 同源 cookie、不缓存的 JSON 请求；网络中断与中止都返回 null。
async function send(url: string, method: "GET" | "POST", headers: Record<string, string>, body: unknown, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: method === "GET" ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json", ...headers },
      ...(method === "GET" ? {} : { body: JSON.stringify(body) }),
      signal: signal ?? null,
    });
  } catch {
    return null;
  }
}

// 我的订单的结果：ok 为 200（total 为全部订单数，orders 为第 1 页）；signed_out 为 401；failed 为其他（含 200 以外的 2xx 与不是 JSON）；
// network 为网络中断。
export type MemberOrdersRead =
  | { kind: "ok"; total: number; orders: MemberOrderSummary[] }
  | { kind: "signed_out" }
  | { kind: "failed" }
  | { kind: "network" };

// GET /api/member/orders：不带 page（服务端默认第 1 页，每页 20 张，按时间从新到旧），不带任何查询参数。
export async function readMemberOrders(signal?: AbortSignal): Promise<MemberOrdersRead> {
  const response = await send(MEMBER_ORDERS_URL, "GET", {}, null, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    return rejection(response.status, await errorCode(response)) === "signed_out" ? { kind: "signed_out" } : { kind: "failed" };
  }
  let body: MemberOrdersResponse;
  try {
    body = (await response.json()) as MemberOrdersResponse;
  } catch {
    return { kind: "failed" };
  }
  return { kind: "ok", total: body.total, orders: body.orders };
}

// 读取一张会员订单的结果：ok 为 200（单张订单，没有 CSRF 令牌，令牌另由会员会话给出）；signed_out 为 401；not_found 为 404
// （不存在与不属于本会员不加区分）；failed 为其他（含 200 以外的 2xx 与不是 JSON）；network 为网络中断。
export type MemberOrderRead = { kind: "ok"; order: LookupOrder } | { kind: "signed_out" } | { kind: "not_found" } | { kind: "failed" } | { kind: "network" };

export function memberOrderViewUrl(language: Language): string {
  return `${MEMBER_ORDER_VIEW_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// POST /api/member/orders/view：请求体只有订单号，地址只带语言。
export async function readMemberOrder(language: Language, orderNumber: string, signal?: AbortSignal): Promise<MemberOrderRead> {
  const response = await send(memberOrderViewUrl(language), "POST", {}, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    const rejected = rejection(response.status, await errorCode(response));
    return rejected === "signed_out" || rejected === "not_found" ? { kind: rejected } : { kind: "failed" };
  }
  let order: LookupOrder;
  try {
    order = (await response.json()) as LookupOrder;
  } catch {
    return { kind: "failed" };
  }
  return { kind: "ok", order };
}

// 确认收货的回答：done 为 200；closed 为 409 order_not_confirmable；signed_out 为 401；not_found 为 404；csrf 为 403；
// failed 为其他（含 200 以外的 2xx、409 fulfilment_frozen 与 idempotency_conflict）；network 为网络中断。
export type MemberConfirmReply = "done" | "closed" | MemberRejection | "failed" | "network";

// POST /api/member/orders/confirm-receipt：请求体只有订单号，请求头带幂等键（orderLookup.ts 的 receiptKey）与 CSRF 令牌。
export async function submitMemberConfirmReceipt(orderNumber: string, csrfToken: string, key: string): Promise<MemberConfirmReply> {
  const response = await send(MEMBER_CONFIRM_RECEIPT_URL, "POST", { [IDEMPOTENCY_HEADER]: key, [CSRF_HEADER]: csrfToken }, { order_number: orderNumber });
  if (response === null) {
    return "network";
  }
  if (response.status === 200) {
    return "done";
  }
  const code = await errorCode(response);
  if (response.status === 409) {
    return code === "order_not_confirmable" ? "closed" : "failed";
  }
  return rejection(response.status, code) ?? "failed";
}

// 重新读取该单，直到得到 ok、signed_out 或 not_found；中止时返回 null。
export async function recheckMemberOrder(
  language: Language,
  orderNumber: string,
  { signal, delay = wait }: RecheckOptions = {},
): Promise<Exclude<MemberOrderRead, { kind: "failed" | "network" }> | null> {
  for (;;) {
    const read = await readMemberOrder(language, orderNumber, signal);
    if (signal?.aborted) {
      return null;
    }
    if (read.kind === "ok" || read.kind === "signed_out" || read.kind === "not_found") {
      return read;
    }
    await delay(RECHECK_DELAY_MS, signal);
    if (signal?.aborted) {
      return null;
    }
  }
}

// 一次确认收货之后页面的去向（会员模式）：
// read 按重新读取到的订单显示（200 或 409 order_not_confirmable）；signed_out 会话已结束（页面按会员会话处理）；
// not_found 该单不存在或不属于本会员；csrf 令牌失效（order 为重新读取到的订单，没有则为空）：页面重读会员会话取得新令牌后提示重试；
// error 显示 common.error_retry（order 为空时沿用原来的）；
// retry 为网络中断后确认订单仍为 demo_shipped：显示 common.error_retry，允许以同一幂等键（key）重试。
export type MemberConfirmOutcome =
  | { kind: "read"; order: LookupOrder }
  | { kind: "signed_out" }
  | { kind: "not_found" }
  | { kind: "csrf"; order: LookupOrder | null }
  | { kind: "error"; order: LookupOrder | null }
  | { kind: "retry"; order: LookupOrder; key: string };

export interface MemberConfirmOptions extends RecheckOptions {
  // 网络中断、开始重新读取订单时调用（页面显示 common.network_check）。
  onChecking?: () => void;
}

// 提交确认收货并决定去向；页面已离开（中止）时返回 null。
export async function runMemberConfirmReceipt(
  language: Language,
  orderNumber: string,
  csrfToken: string,
  key: string,
  options: MemberConfirmOptions = {},
): Promise<MemberConfirmOutcome | null> {
  const reply = await submitMemberConfirmReceipt(orderNumber, csrfToken, key);
  if (options.signal?.aborted) {
    return null;
  }
  switch (reply) {
    case "signed_out":
    case "not_found":
      return { kind: reply };
    case "failed":
      return { kind: "error", order: null };
    case "done":
    case "closed":
    case "csrf": {
      // 已确认或不能再确认：重新读取订单按新状态显示；令牌失效：重新读取订单，由页面另取新令牌再提示重试。
      const read = await readMemberOrder(language, orderNumber, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "signed_out" || read.kind === "not_found") {
        return { kind: read.kind };
      }
      const order = read.kind === "ok" ? read.order : null;
      if (reply === "csrf") {
        return { kind: "csrf", order };
      }
      return order === null ? { kind: "error", order: null } : { kind: "read", order };
    }
    case "network": {
      // 网络中断：先重新读取订单确认上一步是否已完成，仍为 demo_shipped 时才允许以同一幂等键重试。
      options.onChecking?.();
      const read = await recheckMemberOrder(language, orderNumber, options);
      if (read === null) {
        return null;
      }
      if (read.kind !== "ok") {
        return { kind: read.kind };
      }
      if (read.order.status !== SHIPPED) {
        return { kind: "read", order: read.order };
      }
      return { kind: "retry", order: read.order, key };
    }
  }
}

// 提交退款申请的回答：done 为 201（新写）或 200（按幂等键重放）；409 按 detail 分为 duplicate、nothing_left、window_closed、
// not_refundable、conflict（idempotency_conflict）；signed_out 为 401；not_found 为 404；csrf 为 403；
// failed 为其他（含其余 2xx、422 与未知的 409）；network 为网络中断。
export type MemberRefundReply = "done" | "duplicate" | "nothing_left" | "window_closed" | "not_refundable" | "conflict" | MemberRejection | "failed" | "network";

const REFUND_CONFLICTS: ReadonlyMap<string, MemberRefundReply> = new Map<string, MemberRefundReply>([
  ["refund_duplicate", "duplicate"],
  ["refund_nothing_left", "nothing_left"],
  ["refund_window_closed", "window_closed"],
  ["order_not_refundable", "not_refundable"],
  ["idempotency_conflict", "conflict"],
]);

// POST /api/member/orders/refunds：请求体只有订单号与申请行（orderLookup.ts 的 refundLines 的结果），
// 请求头带幂等键（orderLookup.ts 的 refundKey）与 CSRF 令牌；没有任何金额字段。
export async function submitMemberRefund(orderNumber: string, csrfToken: string, key: string, lines: readonly RefundLineRequest[]): Promise<MemberRefundReply> {
  const response = await send(MEMBER_REFUNDS_URL, "POST", { [IDEMPOTENCY_HEADER]: key, [CSRF_HEADER]: csrfToken }, { order_number: orderNumber, lines });
  if (response === null) {
    return "network";
  }
  if (response.status === 201 || response.status === 200) {
    return "done";
  }
  const code = await errorCode(response);
  if (response.status === 409) {
    return (code === null ? undefined : REFUND_CONFLICTS.get(code)) ?? "failed";
  }
  return rejection(response.status, code) ?? "failed";
}

// 一次退款申请之后页面的去向（会员模式）：
// submitted 回到 P09 并显示 refund.submitted；leave 回到 P09（409 order_not_refundable）；signed_out 会话已结束；
// not_found 该单不存在或不属于本会员；refused 显示对应提示并按重新读取到的订单显示（order 为空时沿用原来的）；
// csrf 令牌失效（同上）：页面重读会员会话取得新令牌后提示重试；error 显示 common.error_retry（同上）；
// retry 为网络中断后确认申请没有写入：显示 common.error_retry，允许以同一幂等键与同一请求内容（retry）重试。
export type MemberRefundOutcome =
  | { kind: "submitted" }
  | { kind: "leave" }
  | { kind: "signed_out" }
  | { kind: "not_found" }
  | { kind: "refused"; reason: RefundRefusal; order: LookupOrder | null }
  | { kind: "csrf"; order: LookupOrder | null }
  | { kind: "error"; order: LookupOrder | null }
  | { kind: "retry"; order: LookupOrder; retry: RefundRetry };

// 提交退款申请并决定去向；页面已离开（中止）时返回 null。order 为提交时显示的订单（取订单号与已有的申请笔数）。
export async function runMemberRefund(
  language: Language,
  order: Pick<LookupOrder, "order_number" | "refund_requests">,
  csrfToken: string,
  key: string,
  lines: readonly RefundLineRequest[],
  options: MemberConfirmOptions = {},
): Promise<MemberRefundOutcome | null> {
  const reply = await submitMemberRefund(order.order_number, csrfToken, key, lines);
  if (options.signal?.aborted) {
    return null;
  }
  switch (reply) {
    case "done":
      return { kind: "submitted" };
    case "not_refundable":
      return { kind: "leave" };
    case "signed_out":
    case "not_found":
      return { kind: reply };
    case "failed":
      return { kind: "error", order: null };
    case "duplicate":
    case "nothing_left":
    case "window_closed":
    case "conflict":
    case "csrf": {
      // 被拒：重新读取订单得到最新的可退件；幂等键冲突：重新读取后提示重试（下次提交另生成键）；令牌失效：重新读取，由页面另取新令牌。
      const read = await readMemberOrder(language, order.order_number, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "signed_out" || read.kind === "not_found") {
        return { kind: read.kind };
      }
      const found = read.kind === "ok" ? read.order : null;
      if (reply === "csrf") {
        return { kind: "csrf", order: found };
      }
      return reply === "conflict" ? { kind: "error", order: found } : { kind: "refused", reason: reply, order: found };
    }
    case "network": {
      // 网络中断：先重新读取订单确认申请是否已写入（申请记录多了一笔），没有时才允许以同一幂等键与同一请求内容重试。
      options.onChecking?.();
      const read = await recheckMemberOrder(language, order.order_number, options);
      if (read === null) {
        return null;
      }
      if (read.kind !== "ok") {
        return { kind: read.kind };
      }
      if (read.order.refund_requests.length > order.refund_requests.length) {
        return { kind: "submitted" };
      }
      return { kind: "retry", order: read.order, retry: { key, lines: [...lines] } };
    }
  }
}

// 会员积分预估的结果：ok 为 200 且响应体合格（orderLookup.ts 的 parsePointsEstimate）；signed_out 为 401；not_found 为 404；
// failed 为其他（含 409 order_not_refundable、refund_window_closed、refund_nothing_left，422，200 以外的 2xx，以及 200 而响应体不合格）；
// network 为网络中断。
export type MemberPointsEstimateRead = ({ kind: "ok" } & PointsEstimate) | { kind: "signed_out" } | { kind: "not_found" } | { kind: "failed" } | { kind: "network" };

// POST /api/member/orders/refunds/points-estimate：请求体只有订单号与申请行；只读，不带幂等键与 CSRF 令牌。
export async function estimateMemberRefundPoints(orderNumber: string, lines: readonly RefundLineRequest[], signal?: AbortSignal): Promise<MemberPointsEstimateRead> {
  const response = await send(MEMBER_POINTS_ESTIMATE_URL, "POST", {}, { order_number: orderNumber, lines }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status !== 200) {
    const rejected = rejection(response.status, await errorCode(response));
    return rejected === "signed_out" || rejected === "not_found" ? { kind: rejected } : { kind: "failed" };
  }
  let estimate: PointsEstimate | null;
  try {
    estimate = parsePointsEstimate(await response.json());
  } catch {
    return { kind: "failed" };
  }
  return estimate === null ? { kind: "failed" } : { kind: "ok", ...estimate };
}

// 从我的订单 P13 进入订单详情时正在查看的会员订单号：P09 与 P10 的会员模式先后读同一个值（读取不清除），
// P13 交出新值时覆盖，退出登录时清除。只在页面内存（本模块的变量）里，不进网址、历史状态或任何浏览器存储；整页刷新即消失（HANDOFF 0.61）。
let viewingOrder: string | null = null;

export function handOffMemberOrder(orderNumber: string): void {
  viewingOrder = orderNumber;
}

export function memberOrderInView(): string | null {
  return viewingOrder;
}

export function clearMemberOrder(): void {
  viewingOrder = null;
}

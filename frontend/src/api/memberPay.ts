import type { Language } from "../i18n/copy";
import { AWAITING_PAYMENT, conflictDetail, CSRF_HEADER, IDEMPOTENCY_HEADER, isConfirmResult, isHttpsUrl, postJson, RECHECK_DELAY_MS, replyObject, wait } from "./pay";
import type { ConfirmOptions, PaymentAttempt, PayOrder, StripeConfirm, StripeStart, WriteOptions } from "./pay";

// 支付页 P06 与结果页 P07 的会员接口（app/api/member_pay.py）：POST /api/member/pay/order、
// POST /api/member/pay/attempts、POST /api/member/pay/cancel、POST /api/member/pay/stripe/start 与 POST /api/member/pay/stripe/confirm。
// 授权凭服务端发的会员会话 HttpOnly cookie（页面脚本不读也不写），服务端只给出本会员认领或下单的订单（DESIGN 1.14「权限与资料保护」第 5 条）；
// 不读游客的订单访问 cookie。订单号只放在请求体里，CSRF 令牌（取自会员会话）只放在写操作的请求头里，都不进地址；只有读取与 Stripe 发起带语言查询参数。
// 写法、409 的 detail 与回答的归类沿用 pay.ts 的游客接口，只是 401 为会员会话无效（signed_out），另有 404 order_not_found（not_found）。
// 本模块另管会员当前这一笔订单号的暂存（DESIGN 1.14 第 7 条），游客的函数与流程不读也不写它。

const MEMBER_PAY_ORDER_URL = "/api/member/pay/order";
export const MEMBER_ATTEMPTS_URL = "/api/member/pay/attempts";
export const MEMBER_CANCEL_URL = "/api/member/pay/cancel";
const MEMBER_STRIPE_START_URL = "/api/member/pay/stripe/start";
export const MEMBER_STRIPE_CONFIRM_URL = "/api/member/pay/stripe/confirm";

// 404 order_not_found：订单不存在、订单号不合法或不属于本会员，服务端不加区分。其他 404 归 failed。
async function isNotFound(response: Response): Promise<boolean> {
  return response.status === 404 && (await conflictDetail(response)) === "order_not_found";
}

// 读取一张会员订单的结果：ok 为 200（单张订单，没有 CSRF 令牌，令牌另由会员会话给出）；signed_out 为 401；not_found 为 404 order_not_found；
// failed 为其他（含 200 以外的 2xx、200 而不是 JSON 与 5xx）；network 为网络中断或中止。
export type MemberPayRead = { kind: "ok"; order: PayOrder } | { kind: "signed_out" } | { kind: "not_found" } | { kind: "failed" } | { kind: "network" };

export function memberPayOrderUrl(language: Language): string {
  return `${MEMBER_PAY_ORDER_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// POST /api/member/pay/order：只读，请求体只有订单号，不带 CSRF 令牌与幂等键。
export async function readMemberPayOrder(language: Language, orderNumber: string, signal?: AbortSignal): Promise<MemberPayRead> {
  const response = await postJson(memberPayOrderUrl(language), {}, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "signed_out" };
  }
  if (await isNotFound(response)) {
    return { kind: "not_found" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let order: PayOrder;
  try {
    order = (await response.json()) as PayOrder;
  } catch {
    return { kind: "failed" };
  }
  return { kind: "ok", order };
}

// 会员写接口的回答：照 pay.ts 的 WriteReply，expired 换成 signed_out（401），另有 not_found（404 order_not_found）。
export type MemberWriteReply = "done" | "closed" | "method_changed" | "unavailable" | "signed_out" | "not_found" | "csrf" | "failed" | "network";

const CLOSED_DETAILS: ReadonlySet<string> = new Set(["order_expired", "order_not_payable", "order_not_cancellable"]);

async function postMemberWrite(url: string, headers: Record<string, string>, body: unknown): Promise<MemberWriteReply> {
  const response = await postJson(url, headers, body);
  if (response === null) {
    return "network";
  }
  if (response.ok) {
    return "done";
  }
  if (response.status === 401) {
    return "signed_out";
  }
  if (response.status === 403) {
    return "csrf";
  }
  if (response.status === 404) {
    return (await isNotFound(response)) ? "not_found" : "failed";
  }
  if (response.status === 409) {
    const detail = await conflictDetail(response);
    if (detail === "method_changed") {
      return "method_changed";
    }
    return detail !== null && CLOSED_DETAILS.has(detail) ? "closed" : "failed";
  }
  if (response.status === 503) {
    return (await conflictDetail(response)) === "stripe_unavailable" ? "unavailable" : "failed";
  }
  return "failed";
}

// POST /api/member/pay/attempts：请求体只有订单号、支付方式与结果，请求头带幂等键（pay.ts 的 attemptFor）与 CSRF 令牌。
export function submitMemberPayment(orderNumber: string, csrfToken: string, attempt: PaymentAttempt): Promise<MemberWriteReply> {
  return postMemberWrite(
    MEMBER_ATTEMPTS_URL,
    { [IDEMPOTENCY_HEADER]: attempt.key, [CSRF_HEADER]: csrfToken },
    { order_number: orderNumber, method: attempt.method, result: attempt.result },
  );
}

// POST /api/member/pay/cancel：请求体只有订单号，不要求幂等键。
export function submitMemberCancel(orderNumber: string, csrfToken: string): Promise<MemberWriteReply> {
  return postMemberWrite(MEMBER_CANCEL_URL, { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber });
}

// 发起会员 Stripe 付款的结果：照 pay.ts 的 StripeStart，expired 换成 signed_out（401），另有 not_found（404 order_not_found）。
export type MemberStripeStart = Exclude<StripeStart, { kind: "expired" }> | { kind: "signed_out" } | { kind: "not_found" };

const START_CONFLICTS: ReadonlyMap<string, MemberStripeStart> = new Map<string, MemberStripeStart>([
  ["order_not_payable", { kind: "closed" }],
  ["method_changed", { kind: "method_changed" }],
  ["stripe_under_review", { kind: "under_review" }],
]);

export function memberStripeStartUrl(language: Language): string {
  return `${MEMBER_STRIPE_START_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// POST /api/member/pay/stripe/start：请求体只有订单号；成功响应与结果映射同 pay.ts 的 startStripe。
export async function startMemberStripe(language: Language, orderNumber: string, csrfToken: string, signal?: AbortSignal): Promise<MemberStripeStart> {
  const response = await postJson(memberStripeStartUrl(language), { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200:
      break;
    case 401:
      return { kind: "signed_out" };
    case 403:
      return { kind: "csrf" };
    case 404:
      return (await isNotFound(response)) ? { kind: "not_found" } : { kind: "failed" };
    case 409:
      return START_CONFLICTS.get((await conflictDetail(response)) ?? "") ?? { kind: "failed" };
    case 503:
      return (await conflictDetail(response)) === "stripe_unavailable" ? { kind: "unavailable" } : { kind: "failed" };
    default:
      return { kind: "failed" };
  }
  const body = await replyObject(response);
  const url = body?.checkout_url;
  if (body?.result === "redirect" && typeof url === "string" && isHttpsUrl(url)) {
    return { kind: "redirect", url };
  }
  if (body?.result === "complete" && url === null) {
    return { kind: "complete" };
  }
  return { kind: "failed" };
}

// 确认会员 Stripe 付款的结果：照 pay.ts 的 StripeConfirm，expired 换成 signed_out（401），另有 not_found（404 order_not_found）。
export type MemberStripeConfirm = Exclude<StripeConfirm, { kind: "expired" }> | { kind: "signed_out" } | { kind: "not_found" };

// POST /api/member/pay/stripe/confirm：不带查询参数，请求体只有订单号。
export async function confirmMemberStripe(orderNumber: string, csrfToken: string, signal?: AbortSignal): Promise<MemberStripeConfirm> {
  const response = await postJson(MEMBER_STRIPE_CONFIRM_URL, { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "signed_out" };
  }
  if (response.status === 403) {
    return { kind: "csrf" };
  }
  if (await isNotFound(response)) {
    return { kind: "not_found" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const result = (await replyObject(response))?.result;
  return isConfirmResult(result) ? { kind: result } : { kind: "failed" };
}

// 重新读取该单，直到得到 ok、signed_out 或 not_found；中止时返回 null。
export async function confirmMemberPayOrder(
  language: Language,
  orderNumber: string,
  { signal, delay = wait }: ConfirmOptions = {},
): Promise<Exclude<MemberPayRead, { kind: "failed" | "network" }> | null> {
  for (;;) {
    const read = await readMemberPayOrder(language, orderNumber, signal);
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

// 一次会员写操作（支付或取消）之后页面的去向：照 pay.ts 的 WriteOutcome，
// result 转到结果页；signed_out 会员会话已结束（页面按会员会话处理）；not_found 该单不存在或不属于本会员；
// csrf 令牌失效（order 为重新读取到的仍待支付的订单，没有则为空）：会员读取不给新令牌，页面重读会员会话取得新令牌后提示重试；
// error 显示 common.error_retry（沿用原来的订单）；
// retry 为网络中断后确认订单仍待支付：显示 common.error_retry，允许以同一幂等键（attempt）重试；
// method_changed 与 unavailable 同游客版。
export type MemberWriteOutcome =
  | { kind: "result" }
  | { kind: "signed_out" }
  | { kind: "not_found" }
  | { kind: "method_changed" }
  | { kind: "unavailable" }
  | { kind: "csrf"; order: PayOrder | null }
  | { kind: "error" }
  | { kind: "retry"; order: PayOrder; attempt: PaymentAttempt | null };

async function settleMember(
  language: Language,
  orderNumber: string,
  reply: MemberWriteReply,
  attempt: PaymentAttempt | null,
  options: WriteOptions,
): Promise<MemberWriteOutcome | null> {
  switch (reply) {
    case "done":
    case "closed":
      return { kind: "result" };
    case "signed_out":
    case "not_found":
    case "method_changed":
    case "unavailable":
      return { kind: reply };
    case "failed":
      return { kind: "error" };
    case "csrf": {
      // 令牌失效：重新读取订单，已不再待支付时转到结果页；否则由页面另取新令牌再提示重试。
      const read = await readMemberPayOrder(language, orderNumber, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "signed_out" || read.kind === "not_found") {
        return { kind: read.kind };
      }
      if (read.kind === "ok" && read.order.status !== AWAITING_PAYMENT) {
        return { kind: "result" };
      }
      return { kind: "csrf", order: read.kind === "ok" ? read.order : null };
    }
    case "network": {
      // 网络中断：先重新读取订单确认上一步是否已完成，仍待支付时才允许重试。
      options.onChecking?.();
      const read = await confirmMemberPayOrder(language, orderNumber, options);
      if (read === null) {
        return null;
      }
      if (read.kind !== "ok") {
        return { kind: read.kind };
      }
      if (read.order.status !== AWAITING_PAYMENT) {
        return { kind: "result" };
      }
      return { kind: "retry", order: read.order, attempt };
    }
  }
}

// 提交一次会员模拟支付并决定去向；页面已离开（中止）时返回 null。
export async function runMemberPayment(
  language: Language,
  orderNumber: string,
  csrfToken: string,
  attempt: PaymentAttempt,
  options: WriteOptions = {},
): Promise<MemberWriteOutcome | null> {
  const reply = await submitMemberPayment(orderNumber, csrfToken, attempt);
  if (options.signal?.aborted) {
    return null;
  }
  return settleMember(language, orderNumber, reply, attempt, options);
}

// 取消会员订单并决定去向；已支付（409 order_not_cancellable）也转到结果页，按订单当前状态显示。
export async function runMemberCancel(language: Language, orderNumber: string, csrfToken: string, options: WriteOptions = {}): Promise<MemberWriteOutcome | null> {
  const reply = await submitMemberCancel(orderNumber, csrfToken);
  if (options.signal?.aborted) {
    return null;
  }
  return settleMember(language, orderNumber, reply, null, options);
}

// 会员当前这一笔订单号（DESIGN 1.14「权限与资料保护」第 7 条，UX P06「会员的当前订单」）：刷新或从 Stripe 返回（整页加载）后
// 页面内存会丢失，故同时记在本模块的变量与本标签页 sessionStorage 的一个固定键里；只存订单号这一个值，
// 不进地址、历史状态、localStorage 或 cookie。只有会员流程写入（进入会员支付页时，覆盖之前的值）；结果确定、退出或注销、
// 会员接口回答会话无效或订单不存在时由页面清除。sessionStorage 不存在或读写抛错时当作没有暂存，写入与清除静默跳过。
export const MEMBER_PAY_ORDER_KEY = "acuven-shop.member-pay-order";

let stashedOrder: string | null = null;

export function stashMemberPayOrder(orderNumber: string): void {
  stashedOrder = orderNumber;
  try {
    globalThis.sessionStorage.setItem(MEMBER_PAY_ORDER_KEY, orderNumber);
  } catch {
    // 本标签页存不了：只留在页面内存里。
  }
}

// 先取模块变量，没有再取 sessionStorage；读取不清除。
export function stashedMemberPayOrder(): string | null {
  if (stashedOrder !== null) {
    return stashedOrder;
  }
  try {
    const stored = globalThis.sessionStorage.getItem(MEMBER_PAY_ORDER_KEY);
    return stored === null || stored === "" ? null : stored;
  } catch {
    return null;
  }
}

export function clearMemberPayOrder(): void {
  stashedOrder = null;
  try {
    globalThis.sessionStorage.removeItem(MEMBER_PAY_ORDER_KEY);
  } catch {
    // 同上：没有可清的存储。
  }
}

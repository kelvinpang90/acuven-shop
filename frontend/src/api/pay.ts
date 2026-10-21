import type { Language } from "../i18n/copy";

// 支付页 P06 与结果页 P07 的游客接口（app/api/pay.py，SHOP-TASK-021）：GET /api/pay/orders、POST /api/pay/attempts、POST /api/pay/cancel；
// Stripe 测试环境支付（DESIGN 1.12「Stripe 测试环境支付」，app/api/stripe_pay.py，SHOP-TASK-120）：POST /api/pay/stripe/start、POST /api/pay/stripe/confirm。
// 访问凭本浏览器的订单访问 cookie（服务端发的 HttpOnly cookie，页面脚本读不到也不写）；订单号只放在写接口的请求体里，
// CSRF 令牌只放在请求头里，二者都只在页面内存中，不进路径、查询参数、localStorage、sessionStorage 或 cookie（UX「阅读说明」）。
// 金额是服务端算好的整数仙，页面只格式化，不计算；支付途径与 Stripe 状态也由服务端判定，页面只按它显示。

const ORDERS_URL = "/api/pay/orders";
export const ATTEMPTS_URL = "/api/pay/attempts";
export const CANCEL_URL = "/api/pay/cancel";
const STRIPE_START_URL = "/api/pay/stripe/start";
export const STRIPE_CONFIRM_URL = "/api/pay/stripe/confirm";
const REGIONS_URL = "/api/checkout/regions";

export const IDEMPOTENCY_HEADER = "Idempotency-Key";
export const CSRF_HEADER = "X-CSRF-Token";

export const AWAITING_PAYMENT = "awaiting_demo_payment";
export const CANCELLED = "demo_cancelled";

// 接口里的支付方式，依次对应 pay.method_card、pay.method_bank、pay.method_ewallet。
export const PAY_METHODS = ["card", "bank", "ewallet"] as const;
export type PayMethod = (typeof PAY_METHODS)[number];
export type PayResult = "success" | "failure";

export interface PayRecipient {
  name: string;
  phone: string;
  // 两位 ISO 3166-1 代码。
  country_code: string;
  // 马来西亚为州属代码（MY-01 到 MY-16），其他国家为自由文本或空。
  region: string | null;
  address: string;
  postal_code: string;
}

export interface LastPayment {
  method: PayMethod;
  result: PayResult;
  created_at: string;
}

// 一张订单。时间都是带时区的 ISO 字符串；server_time 是服务端读取时的当前时间。
export interface PayOrder {
  order_number: string;
  status: string;
  created_at: string;
  payment_expires_at: string;
  server_time: string;
  subtotal_sen: number;
  shipping_fee_sen: number;
  total_sen: number;
  recipient: PayRecipient | null;
  last_payment: LastPayment | null;
  cancelled_by: "self" | "timeout" | null;
  // 支付途径：待支付时为 stripe 或 simulated，其他状态为空。
  payment_route: "stripe" | "simulated" | null;
  // 不是 expired 的 Stripe 记录的状态；creating 且已告警（转人工核对）为 under_review；没有这样的记录为空。
  stripe_state: "creating" | "open" | "complete" | "under_review" | null;
  // 本单有来源为会话的 Stripe 退款尝试（迟到或不符的付款，不论尝试状态）。
  stripe_refunded: boolean;
}

interface PayOrdersResponse {
  orders: PayOrder[];
  csrf_token: string;
}

// 读取订单的结果：取到（第一张，即最近签发的授权）、凭据过期（401，或没有订单）、其他失败、网络中断。
export type PayRead =
  | { kind: "ok"; order: PayOrder; csrfToken: string }
  | { kind: "expired" }
  | { kind: "failed" }
  | { kind: "network" };

export function payOrdersUrl(language: Language): string {
  return `${ORDERS_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

export async function readPayOrder(language: Language, signal?: AbortSignal): Promise<PayRead> {
  let response: Response;
  try {
    response = await fetch(payOrdersUrl(language), {
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
  let body: PayOrdersResponse;
  try {
    body = (await response.json()) as PayOrdersResponse;
  } catch {
    return { kind: "failed" };
  }
  const [order] = body.orders;
  return order ? { kind: "ok", order, csrfToken: body.csrf_token } : { kind: "expired" };
}

// 一次模拟支付：幂等键与内容。网络中断后订单仍待支付时，同一方式与结果的重试沿用这一个。
export interface PaymentAttempt {
  key: string;
  method: PayMethod;
  result: PayResult;
}

// 每次点击新生成幂等键；只有上一次是网络中断后确认仍待支付、且方式与结果都相同的重试才沿用原来的键。
export function attemptFor(
  retry: PaymentAttempt | null,
  method: PayMethod,
  result: PayResult,
  newKey: () => string = () => crypto.randomUUID(),
): PaymentAttempt {
  if (retry !== null && retry.method === method && retry.result === result) {
    return retry;
  }
  return { key: newKey(), method, result };
}

// 写接口的回答：done 为已记录；closed 为订单已不能支付或取消（409 order_expired、order_not_payable、order_not_cancellable）；
// method_changed 为订单已走 Stripe 途径（409 method_changed，支付与取消都可能）；unavailable 为 Stripe 不可用（503 stripe_unavailable）；
// expired 为 401；csrf 为 403；failed 为其他（含 409 idempotency_conflict 与其他 503）；network 为网络中断。
export type WriteReply = "done" | "closed" | "method_changed" | "unavailable" | "expired" | "csrf" | "failed" | "network";

const CLOSED_DETAILS: ReadonlySet<string> = new Set(["order_expired", "order_not_payable", "order_not_cancellable"]);

async function conflictDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

// 同源 cookie、不缓存的 JSON POST；网络中断与中止都返回 null。
async function postJson(url: string, headers: Record<string, string>, body: unknown, signal?: AbortSignal): Promise<Response | null> {
  try {
    return await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch {
    return null;
  }
}

async function postWrite(url: string, headers: Record<string, string>, body: unknown): Promise<WriteReply> {
  const response = await postJson(url, headers, body);
  if (response === null) {
    return "network";
  }
  if (response.ok) {
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

// POST /api/pay/attempts：请求体只有订单号、支付方式与结果，不含任何卡号或账户资料。
export function submitPayment(orderNumber: string, csrfToken: string, attempt: PaymentAttempt): Promise<WriteReply> {
  return postWrite(
    ATTEMPTS_URL,
    { [IDEMPOTENCY_HEADER]: attempt.key, [CSRF_HEADER]: csrfToken },
    { order_number: orderNumber, method: attempt.method, result: attempt.result },
  );
}

// POST /api/pay/cancel：请求体只有订单号，不要求幂等键。
export function submitCancel(orderNumber: string, csrfToken: string): Promise<WriteReply> {
  return postWrite(CANCEL_URL, { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber });
}

// 发起 Stripe 付款的结果（DESIGN 1.12 第 3、9 条）：redirect 为跳转到 Stripe 返回的付款页地址（只接受 https）；
// complete 为会话已完成，随后调用确认接口；closed 为订单已不能支付（409 order_not_payable）；method_changed 为 409 method_changed；
// under_review 为创建结果未定、转人工核对（409 stripe_under_review）；unavailable 为 Stripe 不可用（503 stripe_unavailable）；
// expired 为 401；csrf 为 403；failed 为其他（含 200 而地址缺少或不是 https）；network 为网络中断或中止。
export type StripeStart =
  | { kind: "redirect"; url: string }
  | { kind: "complete" }
  | { kind: "closed" }
  | { kind: "method_changed" }
  | { kind: "under_review" }
  | { kind: "unavailable" }
  | { kind: "expired" }
  | { kind: "csrf" }
  | { kind: "failed" }
  | { kind: "network" };

const START_CONFLICTS: ReadonlyMap<string, StripeStart> = new Map<string, StripeStart>([
  ["order_not_payable", { kind: "closed" }],
  ["method_changed", { kind: "method_changed" }],
  ["stripe_under_review", { kind: "under_review" }],
]);

export function stripeStartUrl(language: Language): string {
  return `${STRIPE_START_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

// 200 的响应体：不是 JSON 或不是对象时为 null。
async function replyObject(response: Response): Promise<Record<string, unknown> | null> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  return body !== null && typeof body === "object" ? (body as Record<string, unknown>) : null;
}

// POST /api/pay/stripe/start：请求体只有订单号。
export async function startStripe(language: Language, orderNumber: string, csrfToken: string, signal?: AbortSignal): Promise<StripeStart> {
  const response = await postJson(stripeStartUrl(language), { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 200:
      break;
    case 401:
      return { kind: "expired" };
    case 403:
      return { kind: "csrf" };
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

// 确认 Stripe 付款的结果（DESIGN 1.12 第 4、6 条，HANDOFF 0.54）：paid 已付款；confirming 确认中（含 Stripe 不可用），稍后重查；
// not_paid 尚未付款；refunded 迟到付款按退款处理；expired 为 401；csrf 为 403；failed 为其他；network 为网络中断或中止。
export const STRIPE_CONFIRM_RESULTS = ["paid", "confirming", "not_paid", "refunded"] as const;
export type StripeConfirmResult = (typeof STRIPE_CONFIRM_RESULTS)[number];

export type StripeConfirm = { kind: StripeConfirmResult } | { kind: "expired" } | { kind: "csrf" } | { kind: "failed" } | { kind: "network" };

function isConfirmResult(value: unknown): value is StripeConfirmResult {
  return (STRIPE_CONFIRM_RESULTS as readonly unknown[]).includes(value);
}

// POST /api/pay/stripe/confirm：不带查询参数，请求体只有订单号。
export async function confirmStripe(orderNumber: string, csrfToken: string, signal?: AbortSignal): Promise<StripeConfirm> {
  const response = await postJson(STRIPE_CONFIRM_URL, { [CSRF_HEADER]: csrfToken }, { order_number: orderNumber }, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "expired" };
  }
  if (response.status === 403) {
    return { kind: "csrf" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  const result = (await replyObject(response))?.result;
  return isConfirmResult(result) ? { kind: result } : { kind: "failed" };
}

// P06 交给 P07 的「取消时会话已付款」（HANDOFF 0.55 第（5）项）：订单号只放在本模块的变量里，
// 不进地址、history 的 state、localStorage、sessionStorage 或 cookie；读一次即失效，重新打开或刷新页面后不再显示。
let paidNotCancelled: string | null = null;

// P06 取消时发现会话已付款，转到结果页之前调用（SHOP-TASK-135）。
export function rememberPaidNotCancelled(orderNumber: string): void {
  paidNotCancelled = orderNumber;
}

// P07 打开时调用（SHOP-TASK-133）：记下的订单号与给出的相同时返回 true；不论是否相同都清除记录。
export function takePaidNotCancelled(orderNumber: string): boolean {
  const same = paidNotCancelled === orderNumber;
  paidNotCancelled = null;
  return same;
}

// 网络中断后确认订单的间隔：读取本身也中断或失败时，隔一段时间再读，直到得到回答。
export const RECHECK_DELAY_MS = 5000;

export function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

export interface ConfirmOptions {
  signal?: AbortSignal | undefined;
  delay?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

// 重新读取订单，直到得到 ok 或 expired；中止时返回 null。
export async function confirmOrder(language: Language, { signal, delay = wait }: ConfirmOptions = {}): Promise<Exclude<PayRead, { kind: "failed" | "network" }> | null> {
  for (;;) {
    const read = await readPayOrder(language, signal);
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

// 一次写操作（支付或取消）之后页面的去向：
// result 转到结果页；expired 整页 pay.session_expired；
// error 显示 common.error_retry（read 为重新读取到的订单与新令牌，没有则沿用原来的）；
// retry 为网络中断后确认订单仍待支付：显示 common.error_retry，允许以同一幂等键（attempt）重试；
// method_changed 为订单已走 Stripe 途径（409 method_changed），unavailable 为 Stripe 不可用（503 stripe_unavailable），
// 两者的显示由 P06 的 Stripe 任务（SHOP-TASK-134、135）接上。
export type WriteOutcome =
  | { kind: "result" }
  | { kind: "expired" }
  | { kind: "method_changed" }
  | { kind: "unavailable" }
  | { kind: "error"; read: Extract<PayRead, { kind: "ok" }> | null }
  | { kind: "retry"; read: Extract<PayRead, { kind: "ok" }>; attempt: PaymentAttempt | null };

export interface WriteOptions extends ConfirmOptions {
  // 网络中断、开始重新读取订单时调用（页面显示 common.network_check）。
  onChecking?: () => void;
}

async function settle(language: Language, reply: WriteReply, attempt: PaymentAttempt | null, options: WriteOptions): Promise<WriteOutcome | null> {
  switch (reply) {
    case "done":
    case "closed":
      return { kind: "result" };
    case "expired":
      return { kind: "expired" };
    case "method_changed":
      return { kind: "method_changed" };
    case "unavailable":
      return { kind: "unavailable" };
    case "failed":
      return { kind: "error", read: null };
    case "csrf": {
      // 令牌失效：重新读取订单取得新令牌，再提示重试。
      const read = await readPayOrder(language, options.signal);
      if (options.signal?.aborted) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      if (read.kind === "ok" && read.order.status !== AWAITING_PAYMENT) {
        return { kind: "result" };
      }
      return { kind: "error", read: read.kind === "ok" ? read : null };
    }
    case "network": {
      // 网络中断：先重新读取订单确认上一步是否已完成，仍待支付时才允许重试。
      options.onChecking?.();
      const read = await confirmOrder(language, options);
      if (read === null) {
        return null;
      }
      if (read.kind === "expired") {
        return { kind: "expired" };
      }
      if (read.order.status !== AWAITING_PAYMENT) {
        return { kind: "result" };
      }
      return { kind: "retry", read, attempt };
    }
  }
}

// 提交一次模拟支付并决定去向；页面已离开（中止）时返回 null。
export async function runPayment(
  language: Language,
  orderNumber: string,
  csrfToken: string,
  attempt: PaymentAttempt,
  options: WriteOptions = {},
): Promise<WriteOutcome | null> {
  const reply = await submitPayment(orderNumber, csrfToken, attempt);
  if (options.signal?.aborted) {
    return null;
  }
  return settle(language, reply, attempt, options);
}

// 取消订单并决定去向；已支付（409 order_not_cancellable）也转到结果页，按订单当前状态显示。
export async function runCancel(language: Language, orderNumber: string, csrfToken: string, options: WriteOptions = {}): Promise<WriteOutcome | null> {
  const reply = await submitCancel(orderNumber, csrfToken);
  if (options.signal?.aborted) {
    return null;
  }
  return settle(language, reply, null, options);
}

// 支付时限倒计时（UX「阅读说明」：P06、P07 的 pay.expires 是 15 分钟的支付时限，不是凭据有效期）。
// 剩余时间只用接口返回的支付到期时间与服务器当前时间之差，不读访客电脑的时钟；之后的流逝由页面以单调时钟（performance.now）计。

const MINUTE_MS = 60_000;

// ISO 时间转毫秒；小数秒多于三位时截到毫秒（只为解析，不影响差值的分钟数）。
function parseInstant(value: string): number {
  return Date.parse(value.replace(/(\.\d{3})\d+/, "$1"));
}

// 读取时刻的剩余毫秒：支付到期时间减服务器当前时间；解析不了时按 0。
export function remainingAtRead(order: Pick<PayOrder, "payment_expires_at" | "server_time">): number {
  const remaining = parseInstant(order.payment_expires_at) - parseInstant(order.server_time);
  return Number.isFinite(remaining) ? Math.max(0, remaining) : 0;
}

// pay.expires 的 {minutes}：剩余不足一分钟按一分钟（「请在 1 分钟内完成」），到 0 为 0。
export function minutesLeft(remainingMs: number): number {
  return remainingMs <= 0 ? 0 : Math.ceil(remainingMs / MINUTE_MS);
}

// 距分钟数下一次变化的毫秒数，即每分钟更新一次的下一个时刻；已到 0 为 0。
export function nextTickDelay(remainingMs: number): number {
  return remainingMs <= 0 ? 0 : ((remainingMs - 1) % MINUTE_MS) + 1;
}

// 结果页 P07 显示哪一种：成功、失败（最近一次支付失败且仍待支付）、超时取消、本人取消；
// 仍待支付而最近一次不是失败时回到支付页。已模拟支付之后的状态（打包、发货、完成）也按成功显示。
export type ResultKind = "success" | "failure" | "cancelled" | "cancelled_by_you" | "pay";

export function resultKind(order: Pick<PayOrder, "status" | "last_payment" | "cancelled_by">): ResultKind {
  if (order.status === AWAITING_PAYMENT) {
    return order.last_payment?.result === "failure" ? "failure" : "pay";
  }
  if (order.status === CANCELLED) {
    return order.cancelled_by === "self" ? "cancelled_by_you" : "cancelled";
  }
  return "success";
}

// 马来西亚州属代码 → 名称（GET /api/checkout/regions，SHOP-TASK-023；三种界面语言同一名称）。失败时返回 null，页面显示代码。
export async function fetchMyStates(signal?: AbortSignal): Promise<ReadonlyMap<string, string> | null> {
  try {
    const response = await fetch(REGIONS_URL, { credentials: "omit", headers: { Accept: "application/json" }, signal: signal ?? null });
    if (!response.ok) {
      return null;
    }
    const body = (await response.json()) as { my_states: { code: string; name: string }[] };
    return new Map(body.my_states.map((state) => [state.code, state.name]));
  } catch {
    return null;
  }
}

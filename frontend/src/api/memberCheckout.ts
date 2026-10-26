import type { CartLine } from "../cart";
import type { Language } from "../i18n/copy";
import { checkoutQuoteBody } from "./checkout";
import type { CheckoutDestination, DestinationQuote, QuoteLineBody } from "./checkout";
import type { GuestRecipient, OrderAttempt } from "./orders";
import { CSRF_HEADER, IDEMPOTENCY_HEADER, RECHECK_DELAY_MS, wait } from "./pay";

// 结账页 P05 第 3 步会员表单的会员接口：会员计价 POST /api/member/checkout/quote 与会员下单 POST /api/member/orders
// （app/api/member_orders.py，SHOP-TASK-088），以及取本人手机号 GET /api/member/phone（app/api/member_auth.py，SHOP-TASK-156）。
// 授权凭服务端发的会员会话 HttpOnly cookie（同源 cookie，页面脚本不读也不写），不同于游客计价的 credentials omit。
// 券先抵商品额、积分再抵剩余商品额，均不抵运费（DESIGN 1.14「计价、优惠、积分与库存」第 1 条）：金额全部由服务端算好，页面只格式化。
// 收货电话、收货资料、券代码与 CSRF 令牌只在页面内存、请求体与请求头里，不进地址、日志、localStorage、sessionStorage 或 cookie；
// 只有计价带语言查询参数。本人号码只返回给调用方，本模块不缓存、不写任何浏览器存储（DESIGN 1.14「权限与资料保护」第 8 条，HANDOFF 0.63）。

export const MEMBER_QUOTE_URL = "/api/member/checkout/quote";
export const MEMBER_ORDER_URL = "/api/member/orders";
export const MEMBER_PHONE_URL = "/api/member/phone";

// 会员计价：DestinationQuote 另加券与积分两行。coupon_applied 在未给代码或有不正常的行时为 null，否则为券是否可用（不给原因）；
// points_max 为可用余额与券后商品额的较小值；points_redeemed 为计入的抵扣积分（不接受时为 0）。金额为 MYR 整数仙。
export interface MemberQuote extends DestinationQuote {
  coupon_applied: boolean | null;
  coupon_discount_sen: number;
  points_available: number;
  points_max: number;
  points_accepted: boolean;
  points_redeemed: number;
  points_earned: number;
}

export interface MemberQuoteBody {
  lines: QuoteLineBody[];
  country_code?: string;
  state_code?: string;
  coupon_code?: string;
  points: number;
}

export function memberQuoteUrl(language: Language): string {
  return `${MEMBER_QUOTE_URL}?${new URLSearchParams({ lang: language }).toString()}`;
}

// 计价的请求体：checkoutQuoteBody 的结果另加 points；券代码只在调用方给出（不为 null）时带。
export function memberQuoteBody(lines: readonly CartLine[], destination: CheckoutDestination | null, couponCode: string | null, points: number): MemberQuoteBody {
  const body = { ...checkoutQuoteBody(lines, destination), points };
  return couponCode === null ? body : { ...body, coupon_code: couponCode };
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

// 错误体为 {"detail": ...}：取 detail（缺少或不是 JSON 时为 undefined）。
async function errorDetail(response: Response): Promise<unknown> {
  try {
    return ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    return undefined;
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

// 计价的结果：ok 为 200；signed_out 为 401 member_session_required；failed 为其他（含 200 以外的 2xx、200 而不是 JSON，
// 以及 413、415、422、429 与 503）；network 为网络中断。
export type MemberQuoteRead = { kind: "ok"; quote: MemberQuote } | { kind: "signed_out" } | { kind: "failed" } | { kind: "network" };

// POST /api/member/checkout/quote：只读，不带 CSRF 令牌与幂等键。
export async function fetchMemberQuote(
  language: Language,
  lines: readonly CartLine[],
  destination: CheckoutDestination | null,
  couponCode: string | null,
  points: number,
  signal?: AbortSignal,
): Promise<MemberQuoteRead> {
  const response = await send(memberQuoteUrl(language), "POST", {}, memberQuoteBody(lines, destination, couponCode, points), signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "signed_out" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let quote: MemberQuote;
  try {
    quote = (await response.json()) as MemberQuote;
  } catch {
    return { kind: "failed" };
  }
  return { kind: "ok", quote };
}

// 下单的请求体：没有 phone_region（服务端以收货国家为默认地区解析收货电话，以加号开头时以输入为准）。
export interface MemberOrderBody {
  lines: QuoteLineBody[];
  phone: string;
  name: string;
  address: string;
  postal_code: string;
  country_code: string;
  state_code?: string;
  region?: string;
  coupon_code?: string;
  points: number;
}

// 马来西亚带 state_code、不带 region；其他国家不带 state_code，地区去掉首尾空白后为空时不带（规则同 orders.ts 的 guestOrderBody）。
// phone 为会员填写的收货电话原文（预填会员手机号，可改）；券代码只在调用方给出（不为 null）时带。
export function memberOrderBody(lines: readonly CartLine[], phone: string, recipient: GuestRecipient, couponCode: string | null, points: number): MemberOrderBody {
  let body: MemberOrderBody = {
    lines: checkoutQuoteBody(lines, null).lines,
    phone,
    name: recipient.name,
    address: recipient.address,
    postal_code: recipient.postal_code,
    country_code: recipient.country_code,
    points,
  };
  if (recipient.state_code !== null) {
    body = { ...body, state_code: recipient.state_code };
  } else if (recipient.region !== null && recipient.region.trim() !== "") {
    body = { ...body, region: recipient.region };
  }
  return couponCode === null ? body : { ...body, coupon_code: couponCode };
}

// 一次会员下单的幂等键：请求体（表单、券、积分或购物车）不变时沿用同一个键，变了就在提交时新生成（规则同 orders.ts 的 orderAttemptFor）。
export function memberOrderAttemptFor(previous: OrderAttempt | null, body: MemberOrderBody, newKey: () => string = () => crypto.randomUUID()): OrderAttempt {
  const request = JSON.stringify(body);
  return previous !== null && previous.request === request ? previous : { key: newKey(), request };
}

// 下单的回答：placed 为 201（新订单）或 200（重放），且 order_number 为非空字符串、points_earned 为非负整数；
// 409 按 detail 分为 not_placeable（order_not_placeable）、coupon_invalid、points_unavailable 与 conflict（idempotency_conflict）；
// signed_out 为 401；csrf 为 403 csrf_failed；phone_invalid 为 422 且出错类型含 phone_invalid；unavailable 为 503；
// failed 为其他（含无效目的地的 422 与响应体不合格的 201、200）；network 为网络中断。
export type MemberOrderReply =
  | { kind: "placed"; orderNumber: string; pointsEarned: number }
  | { kind: "not_placeable" }
  | { kind: "coupon_invalid" }
  | { kind: "points_unavailable" }
  | { kind: "conflict" }
  | { kind: "signed_out" }
  | { kind: "csrf" }
  | { kind: "phone_invalid" }
  | { kind: "unavailable" }
  | { kind: "failed" }
  | { kind: "network" };

const ORDER_CONFLICTS: ReadonlyMap<unknown, MemberOrderReply> = new Map<unknown, MemberOrderReply>([
  ["order_not_placeable", { kind: "not_placeable" }],
  ["coupon_invalid", { kind: "coupon_invalid" }],
  ["points_unavailable", { kind: "points_unavailable" }],
  ["idempotency_conflict", { kind: "conflict" }],
]);

function hasPhoneInvalid(detail: unknown): boolean {
  return Array.isArray(detail) && detail.some((item: unknown) => typeof item === "object" && item !== null && (item as { type?: unknown }).type === "phone_invalid");
}

function isPointCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

// POST /api/member/orders：请求体为 memberOrderBody 的结果，请求头带幂等键与调用方给出的 CSRF 令牌，不带查询参数。
export async function submitMemberOrder(body: MemberOrderBody, key: string, csrfToken: string, signal?: AbortSignal): Promise<MemberOrderReply> {
  const response = await send(MEMBER_ORDER_URL, "POST", { [IDEMPOTENCY_HEADER]: key, [CSRF_HEADER]: csrfToken }, body, signal);
  if (response === null) {
    return { kind: "network" };
  }
  switch (response.status) {
    case 201:
    case 200: {
      const reply = await replyObject(response);
      const orderNumber = reply?.order_number;
      const pointsEarned = reply?.points_earned;
      return typeof orderNumber === "string" && orderNumber !== "" && isPointCount(pointsEarned) ? { kind: "placed", orderNumber, pointsEarned } : { kind: "failed" };
    }
    case 401:
      return { kind: "signed_out" };
    case 403:
      return (await errorDetail(response)) === "csrf_failed" ? { kind: "csrf" } : { kind: "failed" };
    case 409:
      return ORDER_CONFLICTS.get(await errorDetail(response)) ?? { kind: "failed" };
    case 422:
      return hasPhoneInvalid(await errorDetail(response)) ? { kind: "phone_invalid" } : { kind: "failed" };
    case 503:
      return { kind: "unavailable" };
    default:
      return { kind: "failed" };
  }
}

export interface MemberOrderOptions {
  signal?: AbortSignal | undefined;
  delay?: (ms: number, signal?: AbortSignal) => Promise<void>;
  // 网络中断、等待以同一幂等键重试时调用（页面显示 common.network_check）。
  onChecking?: () => void;
}

// 提交会员订单，直到得到接口的回答：网络中断时隔一段时间以同一幂等键与同一请求体重试——
// 上一次若已建单，服务端按重放返回原订单（200），不会重复下单（DESIGN 1.14「失败、并发与重试」第 1 条）。页面已离开（中止）时返回 null。
export async function runMemberOrder(
  body: MemberOrderBody,
  key: string,
  csrfToken: string,
  { signal, delay = wait, onChecking }: MemberOrderOptions = {},
): Promise<Exclude<MemberOrderReply, { kind: "network" }> | null> {
  for (;;) {
    const reply = await submitMemberOrder(body, key, csrfToken, signal);
    if (signal?.aborted) {
      return null;
    }
    if (reply.kind !== "network") {
      return reply;
    }
    onChecking?.();
    await delay(RECHECK_DELAY_MS, signal);
    if (signal?.aborted) {
      return null;
    }
  }
}

// 取本人号码的结果：ok 为 200 且响应体合格；signed_out 为 401；failed 为其他（含 200 而响应体不合格）；network 为网络中断。
export type MemberPhoneRead = { kind: "ok"; phone: string } | { kind: "signed_out" } | { kind: "failed" } | { kind: "network" };

// 200 的响应体 {"phone": …}：phone 为以加号开头的非空字符串（E.164）时取它，否则为 null。
export function parseMemberPhone(body: unknown): string | null {
  if (body === null || typeof body !== "object") {
    return null;
  }
  const phone = (body as { phone?: unknown }).phone;
  return typeof phone === "string" && phone.length > 1 && phone.startsWith("+") ? phone : null;
}

// GET /api/member/phone：不带查询参数与 CSRF 令牌。号码只返回给调用方，不缓存、不写任何浏览器存储。
export async function readMemberPhone(signal?: AbortSignal): Promise<MemberPhoneRead> {
  const response = await send(MEMBER_PHONE_URL, "GET", {}, null, signal);
  if (response === null) {
    return { kind: "network" };
  }
  if (response.status === 401) {
    return { kind: "signed_out" };
  }
  if (response.status !== 200) {
    return { kind: "failed" };
  }
  let phone: string | null;
  try {
    phone = parseMemberPhone(await response.json());
  } catch {
    return { kind: "failed" };
  }
  return phone === null ? { kind: "failed" } : { kind: "ok", phone };
}
